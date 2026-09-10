#!/usr/bin/env python3
"""Regression test for audit finding A08 (docs/audits/2026-09-10-codebase-audit.md).

BEFORE: dashboard/aux_update.py's _upd_apply() checked whether an update was
already running and set `running: true` only AFTER spawning the child, with
no lock covering the gap in between — so two nearly-simultaneous
POST /api/update/apply requests could both pass the "not running" check and
both spawn update.sh, which itself had no process lock either, letting a
terminal-run `./update.sh` race a dashboard-triggered one over git, installed
files, services, logs and the shared state file.
(docs/audits/reproduce_20260910.py's `competing_updates()` demonstrated this:
two concurrent calls, both spawned.)

AFTER: an fcntl.flock on ~/.hermes/dashboard/update.lock is reserved BEFORE
spawning, atomically, and held by the detached updater for its whole run
(the fd survives the exec via Popen(pass_fds=...)); the loser gets
{"ok": false, "reason": "already_running", ...}, 409, and a spawn failure
releases the lock instead of leaking it. update.sh takes the identical lock
itself (fd 9, via a one-line python3 helper — macOS ships no flock(1)) when
run bare from a terminal, skipping it only when HERMES_UPDATE_FROM=dashboard
(that process already holds the inherited, pre-locked fd).

This suite extracts the REAL _upd_apply() (via ast, the same technique the
audit's reproduction script uses) and the REAL lock block out of update.sh,
and asserts the fixed, atomic behavior. No dashboard, no network, no model,
no real update.sh run (git/curl/rsync are never invoked — Popen is a fake).
"""
import ast
import fcntl
import os
import subprocess
import sys
import tempfile
import threading
import time
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


# ---------------------------------------------------------------------------
# extract the real _upd_apply() via AST, same technique
# docs/audits/reproduce_20260910.py uses
# ---------------------------------------------------------------------------
def definitions(relative, names, namespace):
    tree = ast.parse((REPO / relative).read_text(), filename=relative)
    found = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in names]
    assert {n.name for n in found} == set(names), \
        "missing %s in %s" % (set(names) - {n.name for n in found}, relative)
    exec(compile(ast.Module(body=found, type_ignores=[]), relative, "exec"), namespace)


def make_ns(scratch, spawn_fn):
    """A namespace carrying every global _upd_apply() touches, backed by a
    throwaway scratch dir and a REAL fcntl.flock (on a real temp file) so the
    atomicity guarantee is genuinely exercised, not mocked away."""
    return dict(
        _upd_os=os,
        _upd_fcntl=fcntl,
        _upd_time=time,
        _UPD_ROOT=str(REPO),
        _UPD_SCRIPT=str(REPO / "update.sh"),
        _UPD_DATA=str(scratch),
        _UPD_LOCK_FILE=str(scratch / "update.lock"),
        _UPD_LOG=str(scratch / "update.log"),
        _upd_channel=lambda: "stable",
        _upd_parse_version=lambda t: (1, 0, 0),
        _upd_known_tags=lambda: {"latest"},
        _upd_dirty_files=lambda: [],
        _upd_subprocess=SimpleNamespace(Popen=spawn_fn, DEVNULL=-3),
        _upd_state_write=lambda obj: None,
        _upd_state_read=lambda: {},
        _upd_version=lambda: "a08-test",
    )


def test_atomic_reserve():
    """Two overlapping requests: exactly one spawns, the other is refused
    409 'already_running' — never both, unlike the pre-fix behavior."""
    with tempfile.TemporaryDirectory(prefix="hermes-a08-") as d:
        scratch = Path(d)
        spawned = []
        popen_entered = threading.Event()
        release = threading.Event()

        def fake_popen(cmd, **kwargs):
            spawned.append(cmd)
            popen_entered.set()
            # Simulate the detached child holding the lock (via inherited
            # pass_fds) for a while — long enough that a genuinely
            # concurrent second request's flock attempt must fail, not just
            # win a race against how fast this fake returns.
            release.wait(timeout=5)
            return SimpleNamespace(pid=424242)

        ns = make_ns(scratch, fake_popen)
        definitions("dashboard/aux_update.py", ["_upd_apply"], ns)

        outcomes = {}

        def call_a():
            outcomes["a"] = ns["_upd_apply"](SimpleNamespace(body={}))

        def call_b():
            assert popen_entered.wait(timeout=5), "A never reached spawn"
            outcomes["b"] = ns["_upd_apply"](SimpleNamespace(body={}))
            release.set()

        ta = threading.Thread(target=call_a)
        tb = threading.Thread(target=call_b)
        ta.start(); tb.start()
        ta.join(timeout=10); tb.join(timeout=10)
        check("both requests returned", not ta.is_alive() and not tb.is_alive())

        a, b = outcomes.get("a"), outcomes.get("b")
        check("exactly one spawn happened (was 2 before the fix)",
              len(spawned) == 1, spawned)
        check("the first caller's result is a plain success dict",
              isinstance(a, dict) and a.get("ok") is True and a.get("started") is True, a)
        check("the second caller was refused (tuple, not a bare success dict)",
              isinstance(b, tuple) and len(b) == 2, b)
        if isinstance(b, tuple):
            body, status = b
            check("...with HTTP 409", status == 409, status)
            check("...reason already_running", body.get("reason") == "already_running", body)
            check("...ok is False", body.get("ok") is False, body)
            check("...error message matches the audit's required text",
                  body.get("error") == "an update is already running", body)


def test_spawn_failure_releases_lock():
    """A spawn failure must release the lock, not leak it — otherwise every
    update would be permanently wedged after one bad attempt."""
    with tempfile.TemporaryDirectory(prefix="hermes-a08b-") as d:
        scratch = Path(d)

        def boom(cmd, **kwargs):
            raise OSError("simulated spawn failure")

        ns = make_ns(scratch, boom)
        definitions("dashboard/aux_update.py", ["_upd_apply"], ns)
        r1 = ns["_upd_apply"](SimpleNamespace(body={}))
        check("a failed spawn reports spawn_failed",
              isinstance(r1, tuple) and r1[0].get("reason") == "spawn_failed", r1)

        # If the lock leaked, this second call — with a Popen that WOULD
        # succeed — would incorrectly come back "already_running".
        spawned = []

        def ok_popen(cmd, **kwargs):
            spawned.append(cmd)
            return SimpleNamespace(pid=1)

        ns2 = make_ns(scratch, ok_popen)
        definitions("dashboard/aux_update.py", ["_upd_apply"], ns2)
        r2 = ns2["_upd_apply"](SimpleNamespace(body={}))
        check("the lock was released after the failed spawn (next call succeeds)",
              isinstance(r2, dict) and r2.get("ok") is True and len(spawned) == 1, r2)


# ---------------------------------------------------------------------------
# update.sh's own lock block (terminal-run half of the fix)
# ---------------------------------------------------------------------------
def extract_lock_block():
    text = (REPO / "update.sh").read_text()
    start = 'if [ "$DRY" != "1" ] && [ "${HERMES_UPDATE_FROM:-}" != "dashboard" ]; then'
    end = '\n# ---------------------------------------------------------------------------\n# channel'
    assert start in text, "update.sh: lock block start marker not found (was it edited?)"
    assert end in text, "update.sh: channel section marker not found"
    body = text.split(start, 1)[1].split(end, 1)[0]
    return start + body


def run_lock_script(state_dir, sleep_after_lock=0.0):
    script = (
        "set -euo pipefail\n"
        "die(){ echo \"DIE: $*\" >&2; exit 1; }\n"
        "DRY=0\n"
        "STATE_DIR=%s\n"
        "%s\n"
        "echo REACHED_AFTER_LOCK\n"
        "sleep %s\n"
    ) % (str(state_dir), extract_lock_block(), sleep_after_lock)
    return subprocess.run(["bash"], input=script, text=True, capture_output=True)


def test_update_sh_own_lock():
    """A bare terminal run of update.sh must exclude a second bare run over
    the SAME lock file the dashboard uses (~/.hermes/dashboard/update.lock)."""
    with tempfile.TemporaryDirectory(prefix="hermes-a08sh-") as d:
        state_dir = Path(d)
        results = {}

        def first():
            results["first"] = run_lock_script(state_dir, sleep_after_lock=1.2)

        t = threading.Thread(target=first)
        t.start()
        time.sleep(0.4)   # give the first process time to acquire the lock
        second = run_lock_script(state_dir, sleep_after_lock=0.0)
        t.join(timeout=10)

        first_r = results.get("first")
        check("first invocation reached past the lock",
              first_r is not None and "REACHED_AFTER_LOCK" in (first_r.stdout or ""),
              first_r and (first_r.stdout, first_r.stderr))
        check("first invocation exited 0",
              first_r is not None and first_r.returncode == 0,
              first_r and first_r.returncode)
        check("second (overlapping) invocation was refused, not both allowed through",
              "REACHED_AFTER_LOCK" not in (second.stdout or ""),
              (second.stdout, second.stderr))
        check("second invocation exited non-zero",
              second.returncode != 0, second.returncode)
        check("second invocation names the lock file in its refusal",
              "update.lock" in (second.stderr or ""), second.stderr)

    # a plain --dry-run-shaped invocation (DRY=1) must skip the lock entirely
    with tempfile.TemporaryDirectory(prefix="hermes-a08dry-") as d:
        script = (
            "set -euo pipefail\n"
            "die(){ echo \"DIE: $*\" >&2; exit 1; }\n"
            "DRY=1\n"
            "STATE_DIR=%s\n"
            "%s\n"
            "echo REACHED_AFTER_LOCK\n"
        ) % (str(Path(d)), extract_lock_block())
        r = subprocess.run(["bash"], input=script, text=True, capture_output=True)
        check("--dry-run is exempt from the lock (reaches past it unconditionally)",
              "REACHED_AFTER_LOCK" in (r.stdout or ""), (r.stdout, r.stderr))


def main():
    print("A08: overlapping updates (docs/audits/2026-09-10-codebase-audit.md)")
    test_atomic_reserve()
    test_spawn_failure_releases_lock()
    test_update_sh_own_lock()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
