#!/usr/bin/env python3
"""C08 (audit 2026-10-21): an Undo no longer holds the recorder's writer lock
across its checkpoint subprocesses.

recorder_undo_handler took `_rec_lock` for its whole run, including
`_ckpt("restore")` and `_ckpt("list")` — each a Python+git subprocess with a
60 s timeout. Every other recorder writer takes the same lock: the live
hub-turn hook (RECORDER_HOOK), the state.db reconciler, and
recorder_record_local, which the Shortcuts bus calls on every request. So one
slow restore stalled a chat turn's tool events and the Shortcuts endpoints.

Exec's the REAL aux_recorder.py the way server.py does (throwaway HOME, no
reconciler thread), swaps the checkpoint driver for one that blocks inside
"restore" until the test releases it, and checks that
  1. recorder_record_local finishes while that restore is in flight;
  2. a second Undo of the same row still waits for the first (undos stay
     serialised) and then reports "already undone" instead of restoring twice;
  3. the first Undo completes normally once the restore returns.

Run:  python3 t_c08_recorder_undo_lock.py
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time

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


class Ctx:
    def __init__(self, body=None):
        self.query = {}
        self.body = body or {}

    def q1(self, k, d=""):
        return d


def build(home):
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    g = {"__name__": "aux_recorder_c08", "HOME": home,
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


HOME = tempfile.mkdtemp(prefix="hermes-c08-")
try:
    G = build(HOME)
    work = os.path.join(HOME, "proj")
    os.makedirs(work)
    target = os.path.join(work, "notes.txt")
    with open(target, "w") as f:
        f.write("what the agent wrote")
    after = G["_after_state"](target)
    con = sqlite3.connect(G["REC_DB"])
    con.execute(
        "INSERT INTO actions(tool_call_id, ts, session, source, tool, target, kind,"
        " reversible, status, snapshot_ref, after_state, origin)"
        " VALUES('tc1', ?, 's', 'hub', 'write_file', ?, 'write', 'yes', 'done', ?, ?,"
        " 'ws')",
        (time.time() - 30, target,
         json.dumps({"workdir": work, "commit": "c0ffee", "short": "c0ffee"}),
         json.dumps(after)))
    con.commit()
    aid = con.execute("SELECT id FROM actions WHERE tool_call_id='tc1'").fetchone()[0]
    con.close()

    entered, release = threading.Event(), threading.Event()
    restores = []

    def fake_ckpt(op, **kw):
        if op == "restore":
            restores.append(kw)
            entered.set()
            release.wait(15)           # the "slow git restore"
            return {"success": True, "restored_to": "c0ffee"}
        if op == "list":
            return []
        return {"error": "bad op"}

    G["_ckpt"] = fake_ckpt             # resolved by global name at call time

    out = {}

    def undo(tag):
        out[tag] = G["recorder_undo_handler"](Ctx({"id": aid}))

    print("\n== a slow restore does not stall the other recorder writers ==")
    ta = threading.Thread(target=undo, args=("a",), daemon=True)
    ta.start()
    check("the first undo reached the restore", entered.wait(5))

    rec_done = threading.Event()

    def record():
        G["recorder_record_local"]("shortcut_run", "Morning", kind="other",
                                   reversible="no", source="shortcuts")
        rec_done.set()

    tb = threading.Thread(target=record, daemon=True)
    tb.start()
    check("recorder_record_local finishes while the restore is in flight",
          rec_done.wait(2.0), "still blocked after 2 s")

    tc = threading.Thread(target=undo, args=("c",), daemon=True)
    tc.start()
    time.sleep(0.5)
    check("a second undo of the same row waits for the first", "c" not in out,
          out.get("c"))

    release.set()
    for t in (ta, tb, tc):
        t.join(20)
    check("the first undo succeeded", (out.get("a") or {}).get("ok") is True,
          out.get("a"))
    check("the second one reports 'already undone' — no second restore",
          (out.get("c") or {}).get("error") == "already undone"
          and len(restores) == 1, (out.get("c"), len(restores)))
    con = sqlite3.connect(G["REC_DB"])
    st = con.execute("SELECT status FROM actions WHERE id=?", (aid,)).fetchone()[0]
    n_local = con.execute("SELECT COUNT(*) FROM actions WHERE tool='shortcut_run'"
                          ).fetchone()[0]
    con.close()
    check("the row is undone and the local record landed",
          st == "undone" and n_local == 1, (st, n_local))
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
