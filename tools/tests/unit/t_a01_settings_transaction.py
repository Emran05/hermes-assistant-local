#!/usr/bin/env python3
"""Regression test for audit finding A01 (docs/audits/2026-09-10-codebase-audit.md).

BEFORE: settings.json had thirteen writers spread across server.py and the
aux modules, and six of them did not hold `_state_lock` across the whole
read-modify-write.  `weather()` was the worst: it captured the ENTIRE settings
object, went to the network to geocode a city, and on the way back wrote that
stale object under the lock — so a Claude-bridge opt-out made while the
geocoding request was in flight was silently reverted to `enabled: true`.
`_cb_set_escalation()` and `_ar_set_mode()` took no lock at all, and every
writer shared one `settings.json.tmp` path, so two of them could interleave
their bytes into a single file and both rename it.
(docs/audits/reproduce_20260910.py's stale_weather_settings() showed the
weather case.)

AFTER: `settings_update(mutate_fn)` in server.py is THE writer — it reads the
current file, applies the mutation and writes atomically, all under
`_state_lock`, so a caller can only ever merge into what is on disk NOW.
`settings_set(**fields)` is the simple-merge form.  Network work happens
OUTSIDE the transaction (weather geocodes first, then merges its three keys).
`write_json` uses a unique O_EXCL temp file, so no two writers share one.

Every check here runs the REAL functions, extracted from the real files with
`ast` — the same technique the audit's reproduction script uses.  No dashboard,
no network, no model, no live settings.json (a throwaway tempfile.mkdtemp()
stands in).
"""
import ast
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def definitions(relative, names, namespace):
    tree = ast.parse((REPO / relative).read_text(), filename=relative)
    found = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in names]
    assert {n.name for n in found} == set(names), \
        "missing %s in %s" % (set(names) - {n.name for n in found}, relative)
    exec(compile(ast.Module(body=found, type_ignores=[]), relative, "exec"),
         namespace)


def server_namespace(scratch):
    """server.py's persistence core with nothing else running."""
    ns = dict(os=os, json=json, time=time, threading=threading, sys=sys,
              _state_lock=threading.Lock(),
              SETTINGS_FILE=str(scratch / "settings.json"),
              _widget_cache={})
    definitions("dashboard/server.py",
                ["read_json", "write_json", "get_settings",
                 "settings_update", "settings_set"], ns)
    return ns


# ---------------------------------------------------------------------------
# 1. the transaction itself
# ---------------------------------------------------------------------------
def test_transaction():
    with tempfile.TemporaryDirectory(prefix="hermes-a01-") as d:
        scratch = Path(d)
        ns = server_namespace(scratch)
        path = ns["SETTINGS_FILE"]

        ns["write_json"](path, {"a": 1, "keep": "me"})
        out = ns["settings_set"](b=2)
        check("settings_set merges without dropping existing keys",
              out == {"a": 1, "keep": "me", "b": 2}, out)
        check("...and that is what is on disk",
              ns["get_settings"] () == {"a": 1, "keep": "me", "b": 2})

        # the mutate function sees the CURRENT file, not a caller's snapshot
        stale = ns["get_settings"]()
        ns["settings_set"](landed_later=True)
        seen = {}
        ns["settings_update"](lambda s: seen.update(s))
        check("mutate_fn is handed the file as it is NOW, not a stale copy",
              seen.get("landed_later") is True, seen)
        check("a caller's own stale snapshot is simply not used",
              "landed_later" not in stale)

        # returning a dict replaces; mutating in place also works
        ns["settings_update"](lambda s: {"replaced": True})
        check("returning a dict replaces the whole object",
              ns["get_settings"]() == {"replaced": True})

        # the returned object is a copy: mutating it must not touch the file
        got = ns["settings_set"](x=1)
        got["x"] = 999
        check("the returned settings are a copy, not the live object",
              ns["get_settings"]()["x"] == 1)

        # a corrupt or wrong-typed file must not hand mutate_fn a non-dict
        Path(path).write_text("{broken")
        ns["settings_set"](recovered=True)
        check("a corrupt settings.json is rebuilt rather than crashing a writer",
              ns["get_settings"]() == {"recovered": True})
        Path(path).write_text("[1,2,3]")
        types_seen = []
        ns["settings_update"](lambda s: types_seen.append(type(s).__name__))
        check("valid JSON of the wrong type is normalised to a dict",
              types_seen == ["dict"], types_seen)


# ---------------------------------------------------------------------------
# 2. concurrent writers keep each other's keys
# ---------------------------------------------------------------------------
def test_concurrent_writers():
    with tempfile.TemporaryDirectory(prefix="hermes-a01-") as d:
        ns = server_namespace(Path(d))
        ns["write_json"](ns["SETTINGS_FILE"], {})
        start = threading.Barrier(8)
        errors = []

        def writer(i):
            try:
                start.wait(timeout=5)
                for _ in range(25):
                    ns["settings_set"](**{"key_%d" % i: i})
            except Exception as e:                       # pragma: no cover
                errors.append(repr(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        check("no writer raised", not errors, errors)
        final = ns["get_settings"]()
        check("all 8 concurrent writers' keys survive (none lost)",
              sorted(final) == ["key_%d" % i for i in range(8)], sorted(final))


# ---------------------------------------------------------------------------
# 3. write_json: unique temp file, preserved mode
# ---------------------------------------------------------------------------
def test_write_json_temp():
    with tempfile.TemporaryDirectory(prefix="hermes-a01-") as d:
        ns = server_namespace(Path(d))
        path = os.path.join(d, "shared.json")

        seen = []
        real_replace = os.replace

        def spy_replace(src, dst):
            seen.append(os.path.basename(src))
            return real_replace(src, dst)

        ns["os"] = SimpleNamespace(
            **{k: getattr(os, k) for k in
               ("open", "fdopen", "chmod", "stat", "unlink", "getpid",
                "O_WRONLY", "O_CREAT", "O_EXCL")}, replace=spy_replace)
        for i in range(3):
            ns["write_json"](path, {"n": i})
        check("write_json never reuses the shared '<path>.tmp' name",
              "shared.json.tmp" not in seen, seen)
        check("every write uses a distinct temp name", len(set(seen)) == 3, seen)
        ns["os"] = os

        check("no temp files are left behind",
              [f for f in os.listdir(d) if ".tmp" in f] == [],
              os.listdir(d))
        check("the file itself is correct", json.load(open(path)) == {"n": 2})

        # an existing target's mode survives a rewrite
        os.chmod(path, 0o640)
        ns["write_json"](path, {"n": 9})
        check("an existing file's permissions are preserved",
              (os.stat(path).st_mode & 0o777) == 0o640,
              oct(os.stat(path).st_mode & 0o777))
        fresh = os.path.join(d, "fresh.json")
        ns["write_json"](fresh, {})
        check("a brand-new state file is created 0600",
              (os.stat(fresh).st_mode & 0o777) == 0o600,
              oct(os.stat(fresh).st_mode & 0o777))


# ---------------------------------------------------------------------------
# 4. THE AUDIT PROBE, as a regression assertion.
#    A user turns the Claude bridge off while weather's geocoding is in flight.
# ---------------------------------------------------------------------------
def test_weather_cannot_revert_the_switch():
    with tempfile.TemporaryDirectory(prefix="hermes-a01-") as d:
        ns = server_namespace(Path(d))
        ns.update(_CB_ESC_LOGGED={"done": False},
                  _CB_CONFIG_ERROR={"error": None, "logged": False},
                  CB_ESC_DEFAULT=False)
        definitions("dashboard/aux_claudebridge.py",
                    ["_cb_set_escalation", "claude_escalation_enabled",
                     "_cb_settings_dict"], ns)
        ns["write_json"](ns["SETTINGS_FILE"], {
            "weather_city": "Sample City",
            "claude_escalation": {"enabled": True},
            "tickers": ["AAPL"]})

        flipped = []

        def fake_http(url):
            if "geocoding-api" in url:
                # the owner turns the bridge off WHILE the geocoding request
                # is in flight — the exact interleaving the audit reproduced
                ns["_cb_set_escalation"](False)
                flipped.append(ns["claude_escalation_enabled"]())
                return {"results": [{"name": "Sample City",
                                     "latitude": 1, "longitude": 2}]}
            return {"current": {"temperature_2m": 60, "weather_code": 0},
                    "daily": {"temperature_2m_max": [70],
                              "temperature_2m_min": [50]}}

        ns.update(_http_json=fake_http, _cached=lambda key, ttl, fn: fn(),
                  urllib=SimpleNamespace(parse=urllib.parse), WMO={0: "Clear"})
        definitions("dashboard/server.py", ["weather"], ns)

        out = ns["weather"]()
        check("the switch really was off before weather finished",
              flipped == [False], flipped)
        check("THE FIX: weather's save does NOT turn the bridge back on",
              ns["claude_escalation_enabled"]() is False,
              ns["get_settings"]().get("claude_escalation"))
        check("...and get_settings agrees",
              ns["get_settings"]()["claude_escalation"]["enabled"] is False)
        check("weather still persisted the coordinates it looked up",
              ns["get_settings"]().get("weather_lat") == 1
              and ns["get_settings"]().get("weather_lon") == 2,
              ns["get_settings"]())
        check("weather still persisted the canonical city name",
              ns["get_settings"]().get("weather_city") == "Sample City")
        check("unrelated settings are untouched",
              ns["get_settings"]().get("tickers") == ["AAPL"])
        check("and the widget still answers", out.get("configured") is True
              and out.get("city") == "Sample City", out)

        # a second refresh with coordinates already stored must not write at
        # all — nothing to merge, so nothing to lose
        before = json.dumps(ns["get_settings"](), sort_keys=True)
        ns["weather"]()
        check("a cached-coordinate refresh writes nothing",
              json.dumps(ns["get_settings"](), sort_keys=True) == before)


# ---------------------------------------------------------------------------
# 5. no writer may bypass the transaction (source-level guard)
# ---------------------------------------------------------------------------
# Each of these files writes settings.json; each must do it through
# settings_update()/settings_set(), never through a bare write of SETTINGS_FILE.
_WRITERS = ["dashboard/server.py", "dashboard/aux_appleapps.py",
            "dashboard/aux_autoroute.py", "dashboard/aux_claudebridge.py",
            "dashboard/aux_config.py", "dashboard/aux_dictation.py",
            "dashboard/aux_evals.py", "dashboard/aux_memlayer.py",
            "dashboard/aux_recorder.py", "dashboard/aux_toolbudget.py",
            "dashboard/aux_update.py"]

# a bare write of the settings file, in any of the spellings this repo uses
_BYPASS = re.compile(
    r"""write_json\s*\(\s*SETTINGS_FILE|"""
    r"""writer\s*\(\s*settings_file|"""
    r"""_ml_g\(\s*["']write_json["']\s*\)\s*\(\s*(?:path|SETTINGS)""")


def _without_settings_update(src):
    """server.py's own settings_update() is the ONE place that writes
    SETTINGS_FILE directly — cut it out before scanning for bypasses."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "settings_update":
            lines = src.splitlines(True)
            return "".join(lines[:node.lineno - 1] + lines[node.end_lineno:])
    return src


def test_no_bypass():
    for rel in _WRITERS:
        src = _without_settings_update((REPO / rel).read_text())
        hit = _BYPASS.search(src)
        check("%s writes settings.json only through settings_update()"
              % os.path.basename(rel), hit is None,
              hit.group(0) if hit else "")
        check("%s actually calls settings_update()/settings_set()"
              % os.path.basename(rel),
              "settings_update(" in src or "settings_set(" in src
              or "updater(" in src)

    server = (REPO / "dashboard/server.py").read_text()
    check("settings_update exists in server.py", "def settings_update(" in server)
    check("settings_set exists in server.py", "def settings_set(" in server)
    check("settings_update takes _state_lock",
          re.search(r"def settings_update\(mutate_fn\):.*?with _state_lock:",
                    server, re.S) is not None)
    # the network call must be outside the transaction
    fetch = server.split("def weather()", 1)[1].split("\ndef ", 1)[0]
    check("weather() does not write settings while holding a network result "
          "it captured earlier", "write_json(SETTINGS_FILE" not in fetch)
    check("weather() merges only its own three keys",
          "settings_set(weather_lat=" in fetch, fetch[:0])


def main():
    print("A01: settings.json lost updates "
          "(docs/audits/2026-09-10-codebase-audit.md)")
    print("\n1. settings_update / settings_set")
    test_transaction()
    print("\n2. concurrent writers")
    test_concurrent_writers()
    print("\n3. write_json temp-file isolation")
    test_write_json_temp()
    print("\n4. the audit probe: an opt-out during weather's geocoding")
    test_weather_cannot_revert_the_switch()
    print("\n5. no writer bypasses the transaction")
    test_no_bypass()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
