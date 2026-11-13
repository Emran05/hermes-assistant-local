#!/usr/bin/env python3
"""E33 (2026-11-09 audit, P2), server half: the recorder's detail diff is
cached per row.

GET /api/recorder?id= ran `_ckpt("diff")` — a Python + git subprocess — on
every call, and the Agent rail's 3 s poll refetched an open pane every time.
Now the answer is kept per row in a bounded cache, keyed on the row's state
(so an Undo, which changes it, refetches), cleared whenever an Undo changes
files on disk, and it expires after a short TTL.

Drives the REAL aux_recorder.py (exec'd like the other recorder suites) with a
counting stand-in for the checkpoint driver; scratch HOME, nothing else.
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
    def __init__(self, body=None, query=None):
        self.body = body or {}
        self.query = query or {}

    def q1(self, k, d=""):
        return self.query.get(k, d)


HOME = tempfile.mkdtemp(prefix="hermes-e33-")
try:
    data = os.path.join(HOME, ".hermes", "dashboard")
    os.makedirs(data)
    G = {"__name__": "aux_recorder_e33", "HOME": HOME,
         "HERE": os.path.join(REPO, "dashboard"), "DATA": data,
         "STATE_DB": os.path.join(HOME, ".hermes", "state.db"),
         "read_json": lambda p, d=None: d, "write_json": lambda p, o: None,
         "get_settings": lambda: {}, "_state_lock": threading.Lock(),
         "_widget_cache": {}, "settings_update": lambda fn: {},
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_recorder_thread_started": True}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), G)

    calls = []

    def fake_ckpt(op, **kw):
        calls.append(op)
        if op == "diff":
            return {"success": True, "diff": "+n%d" % len(calls), "stat": "1 file"}
        if op == "probe":
            return {"ok": True, "rel": "f.txt", "in_snapshot": True}
        if op == "restore":
            return {"success": True, "restored_to": "c0ffee"}
        if op == "list":
            return []
        return {"error": "bad op"}

    G["_ckpt"] = fake_ckpt
    work = os.path.join(HOME, "proj")
    os.makedirs(work)
    snap = json.dumps({"workdir": work, "commit": "c0ffee", "short": "c0ffee"})
    con = sqlite3.connect(G["REC_DB"])
    ids = []
    for i in range(70):
        tgt = os.path.join(work, "f%d.txt" % i)
        with open(tgt, "w") as f:
            f.write("agent %d" % i)
        con.execute("INSERT INTO actions(tool_call_id, ts, session, source, tool, target,"
                    " kind, reversible, status, snapshot_ref, after_state, origin)"
                    " VALUES(?, ?, 's', 'hub', 'write_file', ?, 'write', 'yes', 'done', ?, ?,"
                    " 'ws')", ("t%d" % i, time.time(), tgt, snap,
                               json.dumps(G["_after_state"](tgt))))
        ids.append(con.execute("SELECT id FROM actions WHERE tool_call_id=?",
                               ("t%d" % i,)).fetchone()[0])
    con.commit()
    con.close()

    def detail(aid):
        return G["recorder_api_handler"](Ctx(query={"id": str(aid)}))

    def ndiff():
        return calls.count("diff")

    print("\n== one diff per row, not one per poll ==")
    d1 = detail(ids[0])
    d2 = detail(ids[0])
    d3 = detail(ids[0])
    check("three detail reads, one checkpoint diff", ndiff() == 1, ndiff())
    check("...and the same diff each time", d1.get("diff") == d2.get("diff") == d3.get("diff")
          and d1.get("diff_stat") == "1 file", (d1.get("diff"), d3.get("diff")))
    detail(ids[1])
    check("another row has its own entry", ndiff() == 2, ndiff())

    print("\n== a change to the row refetches ==")
    con = sqlite3.connect(G["REC_DB"])
    con.execute("UPDATE actions SET reversible='no', undo_note='x' WHERE id=?", (ids[1],))
    con.commit()
    con.close()
    detail(ids[1])
    check("the row's state changed: a fresh diff", ndiff() == 3, ndiff())

    print("\n== an Undo clears the cache (files on disk changed) ==")
    detail(ids[2])
    n = ndiff()
    res = G["recorder_undo_handler"](Ctx({"id": ids[3]}))
    check("the undo went through", res.get("ok") is True, res)
    detail(ids[2])
    check("a row in the same folder is diffed again after the undo", ndiff() == n + 1,
          (n, ndiff()))
    detail(ids[2])
    check("...and cached again after that", ndiff() == n + 1, ndiff())

    print("\n== bounded, and it expires ==")
    for aid in ids[4:]:
        detail(aid)
    check("the cache never holds more than its cap",
          len(G["_REC_DIFF_CACHE"]) <= G["_REC_DIFF_MAX"] < 66,
          (len(G["_REC_DIFF_CACHE"]), G["_REC_DIFF_MAX"]))
    n = ndiff()
    detail(ids[-1])
    check("the newest entry is still served from the cache", ndiff() == n, ndiff())
    G["_REC_DIFF_TTL"] = -1
    detail(ids[-1])
    check("past the TTL it is fetched again", ndiff() == n + 1, ndiff())
    G["_REC_DIFF_TTL"] = 120

    print("\n== (review) an entry holds what the pane shows, not the whole folder's diff ==")
    G["_rec_diff_clear"]()
    huge = {"on": True}

    def big_ckpt(op, **kw):
        if op == "diff" and huge["on"]:
            calls.append(op)
            return {"success": True, "diff": "+" + "x" * (5 * 1024 * 1024),
                    "stat": "s" * (1024 * 1024), "extra": "y" * (1024 * 1024)}
        return fake_ckpt(op, **kw)

    G["_ckpt"] = big_ckpt
    b1 = detail(ids[6])
    held = sum(len(str(v)) for _t, pl in G["_REC_DIFF_CACHE"].values() for v in pl.values())
    check("a 5 MB diff is cached as the 20 KB the pane shows", 0 < held <= 20480 + 4096 + 16,
          held)
    check("...and the pane still gets its 20 KB", len(b1.get("diff") or "") == 20480
          and len(b1.get("diff_stat") or "") == 4096, (len(b1.get("diff") or ""),))
    huge["on"] = False
    G["_ckpt"] = fake_ckpt

    print("\n== (review) the owner edits the row's file: the cached picture is not shown ==")
    G["_rec_diff_clear"]()
    detail(ids[7])
    n = ndiff()
    detail(ids[7])
    check("unchanged file: served from the cache", ndiff() == n, ndiff())
    with open(os.path.join(work, "f7.txt"), "w") as f:
        f.write("the owner changed this file after the agent")
    detail(ids[7])
    check("the row's file changed on disk: a fresh diff", ndiff() == n + 1, (n, ndiff()))
    os.remove(os.path.join(work, "f7.txt"))
    detail(ids[7])
    check("...and again when it is gone", ndiff() == n + 2, (n, ndiff()))

    print("\n== (review) a whole-folder (shell) row is always read fresh ==")
    con = sqlite3.connect(G["REC_DB"])
    con.execute("INSERT INTO actions(tool_call_id, ts, session, source, tool, target, kind,"
                " reversible, status, snapshot_ref, origin) VALUES('sh1', ?, 's', 'hub',"
                " 'terminal', 'rm -rf build', 'shell', 'partial', 'done', ?, 'ws')",
                (time.time(), snap))
    shid = con.execute("SELECT id FROM actions WHERE tool_call_id='sh1'").fetchone()[0]
    con.commit()
    con.close()
    n = ndiff()
    detail(shid)
    detail(shid)
    check("two reads of a shell row's detail, two diffs", ndiff() == n + 2, (n, ndiff()))

    print("\n== an error answer is not cached ==")
    G["_ckpt"] = lambda op, **kw: (calls.append(op) or {"error": "git broke"}) \
        if op == "diff" else fake_ckpt(op, **kw)
    G["_rec_diff_clear"]()
    n = ndiff()
    e1 = detail(ids[5])
    e2 = detail(ids[5])
    check("a failed diff is reported and retried next time",
          e1.get("diff_error") == "git broke" and ndiff() == n + 2, (e1, ndiff()))
    del e2
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
