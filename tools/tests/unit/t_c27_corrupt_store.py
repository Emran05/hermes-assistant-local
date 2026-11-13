#!/usr/bin/env python3
"""Regression test for C27 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: read_json() returned the caller's default for a MISSING file and for
an unparsable or unreadable one alike, and every read-modify-write then saved
that default (+ one key) over the owner's file. A settings.json with one
hand-edit typo became `{"weather_city": ...}` on the next ordinary write —
tickers, quicklinks, claude_escalation and the rest gone, the broken original
no longer there to repair. get_layout / get_tasks / models.json did the same
through write_json().

AFTER:
  * settings_update() reads through read_json_for_update(): unparsable or
    wrong-typed -> moved aside as settings.json.corrupt-<ts> (bytes intact)
    and the write starts from {}; present but UNREADABLE -> raises, writes
    nothing; absent or empty -> {} as before.
  * write_json() itself moves an existing unparsable target aside before
    replacing it, so every other read-modify-write path (layout, tasks,
    models, ...) keeps the original too — and only after the replacement is
    safely on disk, so a failed dump never leaves the target moved away.
  * doctor's config check lists the .corrupt copies (WARN) so the owner
    knows the dashboard is running on defaults for that store.

Everything is the REAL code, ast-extracted from dashboard/server.py and
dashboard/doctor.py into throwaway namespaces; files live in a temp dir.
HERMES_T_SERVER_SRC=<old server.py> runs the same checks against the pre-fix
code (the helpers it lacks are skipped), which is how this was shown to fail.
"""
import contextlib
import glob
import io
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check

BROKEN = (b'{\n "tickers": ["NVDA", "AAPL"],\n "quicklinks": [["Mail", '
          b'"https://mail.example"]],\n "claude_escalation": {"enabled": false},'
          b'\n "weather_city": "Paris",\n')          # truncated: no closing brace


def store_namespace(d):
    ns = dict(os=os, json=json, time=time, threading=threading, sys=sys,
              _state_lock=threading.Lock(),
              SETTINGS_FILE=os.path.join(d, "settings.json"),
              _widget_cache={})
    H.extract(["read_json", "write_json", "get_settings", "settings_update",
               "settings_set"], ns,
              optional=["read_json_for_update", "_quarantine_corrupt",
                        "_json_unparsable"])
    return ns


def corrupt_copies(path):
    return sorted(glob.glob(path + ".corrupt-*"))


def quiet(fn, *a, **k):
    """Run fn, return (result, exception, stderr text)."""
    err = io.StringIO()
    res = exc = None
    with contextlib.redirect_stderr(err):
        try:
            res = fn(*a, **k)
        except Exception as e:      # noqa: BLE001 — the test inspects it
            exc = e
    return res, exc, err.getvalue()


def main():
    # -- settings.json: unparsable -> moved aside, never overwritten ----------
    with tempfile.TemporaryDirectory(prefix="hermes-c27-") as d:
        ns = store_namespace(d)
        sf = ns["SETTINGS_FILE"]
        with open(sf, "wb") as f:
            f.write(BROKEN)
        _, exc, err = quiet(ns["settings_set"], weather_lat=48.85)
        copies = corrupt_copies(sf)
        check("a settings write over a corrupt settings.json succeeds",
              exc is None, repr(exc))
        check("...and the corrupt original is kept as settings.json.corrupt-<ts>",
              len(copies) == 1, copies)
        check("...byte for byte",
              bool(copies) and open(copies[0], "rb").read() == BROKEN)
        check("...the live file now starts from defaults + the one key",
              json.load(open(sf)) == {"weather_lat": 48.85},
              open(sf).read()[:200])
        check("...and the move is logged on stderr",
              "[store]" in err and "settings.json" in err, err)

        # wrong JSON type is just as unusable as a parse error
        with open(sf, "w") as f:
            f.write('["not", "a", "dict"]')
        _, exc, _ = quiet(ns["settings_set"], theme="dark")
        copies2 = [c for c in corrupt_copies(sf) if c not in copies]
        check("valid JSON of the wrong type is moved aside too",
              exc is None and len(copies2) == 1
              and json.load(open(copies2[0])) == ["not", "a", "dict"],
              (exc, copies2))
        check("...two move-asides in the same second get distinct names",
              len(set(corrupt_copies(sf))) == 2, corrupt_copies(sf))

    # -- settings.json: empty / absent / healthy behave as before ------------
    with tempfile.TemporaryDirectory(prefix="hermes-c27-") as d:
        ns = store_namespace(d)
        sf = ns["SETTINGS_FILE"]
        ns["settings_set"](a=1)
        check("an absent settings.json is created with the key",
              json.load(open(sf)) == {"a": 1} and not corrupt_copies(sf))
        ns["settings_set"](b=2)
        check("a healthy settings.json is merged, nothing moved aside",
              json.load(open(sf)) == {"a": 1, "b": 2} and not corrupt_copies(sf))
        open(sf, "w").close()
        _, exc, _ = quiet(ns["settings_set"], c=3)
        check("an EMPTY settings.json is not worth a .corrupt copy",
              exc is None and json.load(open(sf)) == {"c": 3}
              and not corrupt_copies(sf), (exc, corrupt_copies(sf)))

    # -- settings.json: present but unreadable -> refuse, write nothing ------
    if os.geteuid() != 0:
        with tempfile.TemporaryDirectory(prefix="hermes-c27-") as d:
            ns = store_namespace(d)
            sf = ns["SETTINGS_FILE"]
            good = b'{"tickers": ["NVDA"], "claude_escalation": {"enabled": false}}'
            with open(sf, "wb") as f:
                f.write(good)
            os.chmod(sf, 0o000)
            try:
                _, exc, _ = quiet(ns["settings_set"], weather_city="Oslo")
            finally:
                os.chmod(sf, 0o600)
            check("an UNREADABLE settings.json makes the write raise",
                  isinstance(exc, OSError), repr(exc))
            check("...and its content is untouched (was: replaced by one key)",
                  open(sf, "rb").read() == good, open(sf, "rb").read()[:120])
            check("...and nothing was moved aside", not corrupt_copies(sf))
    else:
        print("  (skipped the unreadable-file case: running as root)")

    # -- write_json: every other read-modify-write path ----------------------
    with tempfile.TemporaryDirectory(prefix="hermes-c27-") as d:
        ns = store_namespace(d)
        lay = os.path.join(d, "layout.json")
        with open(lay, "wb") as f:
            f.write(b'{"order": ["needsyou", "weather"')
        _, exc, err = quiet(ns["write_json"], lay, {"order": ["default"]})
        copies = corrupt_copies(lay)
        check("write_json over a corrupt store keeps the original aside",
              exc is None and len(copies) == 1
              and open(copies[0], "rb").read() == b'{"order": ["needsyou", "weather"',
              (exc, copies))
        check("...and writes the new content",
              json.load(open(lay)) == {"order": ["default"]})

        ns["write_json"](lay, {"order": ["x"]})
        check("write_json over a healthy store moves nothing",
              len(corrupt_copies(lay)) == 1 and json.load(open(lay)) == {"order": ["x"]})

        tasks = os.path.join(d, "tasks.json")
        with open(tasks, "wb") as f:
            f.write(b"{oops")
        _, exc, _ = quiet(ns["write_json"], tasks, {"bad": {1, 2}})  # a set: not JSON
        check("a dump that FAILS leaves a corrupt target where it was",
              isinstance(exc, TypeError) and open(tasks, "rb").read() == b"{oops"
              and not corrupt_copies(tasks), (exc, corrupt_copies(tasks)))
        check("...and leaves no temp file behind",
              not [n for n in os.listdir(d) if ".tmp." in n], os.listdir(d))

        # read_json() (strict UTF-8 text) gives every reader the DEFAULT for
        # a BOM-prefixed file, so the writer that follows must keep a copy —
        # json.loads(bytes) would have called it fine (review finding)
        bom = os.path.join(d, "bom.json")
        orig = b'\xef\xbb\xbf{"order": ["needsyou", "weather"]}'
        with open(bom, "wb") as f:
            f.write(orig)
        check("(a BOM-prefixed store reads as the default)",
              ns["read_json"](bom, "DEFAULT") == "DEFAULT")
        _, exc, _ = quiet(ns["write_json"], bom, {"order": ["default"]})
        copies = corrupt_copies(bom)
        check("write_json over a store read_json cannot parse (UTF-8 BOM) "
              "keeps the original aside", exc is None and len(copies) == 1
              and open(copies[0], "rb").read() == orig, (exc, copies))

        empty = os.path.join(d, "empty.json")
        open(empty, "w").close()
        ns["write_json"](empty, {"k": 1})
        check("write_json over an EMPTY file moves nothing aside",
              not corrupt_copies(empty) and json.load(open(empty)) == {"k": 1})

    # -- read_json_for_update, directly --------------------------------------
    if "read_json_for_update" in ns:
        with tempfile.TemporaryDirectory(prefix="hermes-c27-") as d:
            ns = store_namespace(d)
            p = os.path.join(d, "x.json")
            check("read_json_for_update: absent -> the default, no side effect",
                  ns["read_json_for_update"](p, {"d": 1}) == {"d": 1}
                  and os.listdir(d) == [])
            with open(p, "w") as f:
                f.write('{"a": [1]}')
            check("read_json_for_update: healthy -> the object",
                  ns["read_json_for_update"](p, {}) == {"a": [1]})
            check("read_json_for_update: kind=None accepts any JSON",
                  ns["read_json_for_update"](p, None, kind=None) == {"a": [1]})
    else:
        check("read_json_for_update exists in server.py", False)

    # -- doctor tells the owner ----------------------------------------------
    with tempfile.TemporaryDirectory(prefix="hermes-c27-doc-") as top:
        # DATA's parent stands in for ~/.hermes: 0700, so the C110 parent-mode
        # bit of the same check stays quiet whatever the system temp dir is
        os.mkdir(os.path.join(top, ".hermes"), 0o700)
        d = os.path.join(top, ".hermes", "dashboard")
        os.makedirs(os.path.join(d, "chats"))
        cfg = os.path.join(d, "config.yaml")
        open(cfg, "w").write("model: {}\n")
        dns = dict(os=os, json=json, _glob=glob, DATA=d,
                   SETTINGS_FILE=os.path.join(d, "settings.json"),
                   REPO_CONFIG=cfg, HERMES_CONFIG=cfg,
                   PASS="pass", WARN="warn", FAIL="fail",
                   _yaml_sane=lambda p: (True, ""),
                   check=lambda *a, **k: (lambda f: f))
        H.extract(["_json_state", "_chk_config"], dns,
                  src=H.REPO / "dashboard" / "doctor.py")
        with open(dns["SETTINGS_FILE"], "w") as f:
            f.write("{}")
        r0 = dns["_chk_config"]()
        check("doctor: no .corrupt copies -> config passes",
              r0["status"] == "pass", r0)
        open(os.path.join(d, "settings.json.corrupt-1700000000"), "w").close()
        open(os.path.join(d, "chats", "chat-1.json.corrupt-1700000001"), "w").close()
        r1 = dns["_chk_config"]()
        check("doctor: .corrupt copies (dashboard + chats) -> WARN naming them",
              r1["status"] == "warn" and "2 store file(s)" in r1["detail"]
              and "settings.json.corrupt-1700000000" in r1["detail"]
              and "chat-1.json.corrupt" in r1["detail"], r1)
        check("doctor: the fix line points at the copy to merge back",
              ".corrupt-" in r1.get("fix", ""), r1)

    H.finish()


if __name__ == "__main__":
    main()
