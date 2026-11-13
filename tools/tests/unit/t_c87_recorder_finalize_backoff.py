#!/usr/bin/env python3
"""C87 (audit 2026-10-21): the recorder's finalize pass no longer re-spawns the
checkpoint driver for every unmatched row on every 5 s tick.

`_finalize_pass()` runs on the reconciler's 5 s tick and, for each recent
write/shell row with no snapshot yet, called `_associate()`, which runs the
checkpoint driver (a Python + git subprocess). With no snapshot to find — every
dashboard/Telegram turn on this Mac, see C114 — that was one spawn per row per
tick for 15 minutes (~180 per row). Rows are now retried on a widening
schedule, and one pass lists each workdir once.

Exec's the REAL aux_recorder.py (throwaway HOME, no reconciler thread), swaps
the module's `time` for a fake clock and `_ckpt` for a counting stub, and
drives 16 simulated minutes of ticks. Checks:
  1. three unmatched rows in one workdir cost a handful of driver runs, not
     hundreds;
  2. they are still marked "no snapshot captured" once 15 minutes old;
  3. a checkpoint that only shows up two minutes in is still attached, so the
     backoff does not cost a reversible row its Undo.

Run:  python3 t_c87_recorder_finalize_backoff.py
"""
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time as _real_time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    return os.environ.get("HERMES_REPO") or os.path.dirname(
        os.path.dirname(os.path.dirname(_HERE)))


REPO = _find_repo()
AUX = os.path.join(REPO, "dashboard", "aux_recorder.py")
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %s" % (name, extra))


class Clock:
    """Stands in for the `time` module inside the exec'd recorder."""

    def __init__(self):
        self.t = _real_time.time()
        self.m = 1000.0

    def time(self):
        return self.t

    def monotonic(self):
        return self.m

    def advance(self, s):
        self.t += s
        self.m += s

    def sleep(self, s):
        self.advance(s)


def build(home):
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    g = {"__name__": "aux_recorder_c87", "HOME": home,
         "HERE": os.path.join(REPO, "dashboard"), "DATA": data,
         "STATE_DB": os.path.join(home, ".hermes", "state.db"),
         "read_json": lambda p, d=None: d, "write_json": lambda p, o: None,
         "get_settings": lambda: {}, "_state_lock": threading.Lock(),
         "_widget_cache": {}, "settings_update": lambda fn: {},
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_recorder_thread_started": True}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g


def insert(db, tcid, ts, target, kind="write", tool="write_file"):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO actions(tool_call_id, ts, session, source, tool, target, kind,"
        " reversible, status, origin) VALUES(?,?,?,?,?,?,?, 'no', 'done', 'ws')",
        (tcid, ts, "s1", "hub", tool, target, kind))
    con.commit()
    con.close()


def rows(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    out = {r["tool_call_id"]: dict(r) for r in con.execute("SELECT * FROM actions")}
    con.close()
    return out


HOME = os.path.realpath(tempfile.mkdtemp(prefix="hermes-c87-"))
try:
    G = build(HOME)
    clock = Clock()
    G["time"] = clock                    # every time.time()/monotonic() inside
    DB = G["REC_DB"]

    work = os.path.join(HOME, "proj")
    os.makedirs(work)
    open(os.path.join(work, "Makefile"), "w").close()     # a workdir marker
    later = os.path.join(HOME, "later")
    os.makedirs(later)
    open(os.path.join(later, "Makefile"), "w").close()

    calls = []
    appear_at = {}                       # workdir -> fake time a snapshot exists

    def fake_ckpt(op, **kw):
        calls.append((op, kw.get("workdir")))
        if op != "list":
            return {"error": "unexpected op"}
        wd = kw.get("workdir")
        if wd in appear_at and clock.t >= appear_at[wd][0]:
            iso = datetime.fromtimestamp(appear_at[wd][1]).isoformat()
            return [{"reason": "before write_file", "timestamp": iso,
                     "hash": "c0ffee" * 6, "short_hash": "c0ffee"}]
        return []

    G["_ckpt"] = fake_ckpt

    t0 = clock.t
    for i in range(3):
        insert(DB, "w%d" % i, t0 - 1, os.path.join(work, "f%d.txt" % i))
    insert(DB, "late", t0 - 1, os.path.join(later, "g.txt"))
    appear_at[later] = (t0 + 120, t0 - 2)        # visible from 2 minutes in

    print("\n== 16 simulated minutes of 5 s reconciler ticks ==")
    lists_at_15min = None
    for tick in range(int(16 * 60 / 5)):
        G["_finalize_pass"]()
        if lists_at_15min is None and clock.t - t0 >= 900:
            lists_at_15min = len([c for c in calls if c[1] == work])
        clock.advance(5)

    n_work = len([c for c in calls if c[1] == work])
    check("three unmatched rows in one workdir cost a handful of driver runs",
          n_work <= 12, "%d driver runs" % n_work)
    r = rows(DB)
    check("all three still get the 15-minute verdict",
          all(r["w%d" % i]["undo_note"] == "no snapshot captured"
              and r["w%d" % i]["reversible"] == "no" for i in range(3)),
          [(r["w%d" % i]["undo_note"], r["w%d" % i]["reversible"]) for i in range(3)])
    check("a snapshot that appears two minutes in is still attached",
          r["late"]["snapshot_ref"] != "" and r["late"]["reversible"] == "yes",
          (r["late"]["snapshot_ref"], r["late"]["reversible"]))
    n_late = len([c for c in calls if c[1] == later])
    check("and the late row stopped asking once it matched", n_late <= 6, n_late)
    n_after = len([c for c in calls if c[1] == work]) - (lists_at_15min or 0)
    check("nothing is retried after the verdict", n_after <= 1, n_after)
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
