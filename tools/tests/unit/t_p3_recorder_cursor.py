#!/usr/bin/env python3
"""2026-10-08 audit, P3 "Stores": the recorder's state.db reconciler no longer
skips messages that share the cursor's timestamp, and no longer collapses two
same-named tool calls that carry no id.

`_reconcile_once()` read `WHERE m.timestamp > cursor ORDER BY m.timestamp
LIMIT 500` and advanced the cursor to the newest timestamp it saw. A message
with the SAME timestamp as the cursor that the read did not reach (the LIMIT
fell between two equal timestamps, or it landed a moment later) was never read.
And a call without an id was keyed "sd-<message id>-<tool name>", so a second
call of the same tool in that message hit INSERT OR IGNORE and vanished.

Exec's the REAL aux_recorder.py (throwaway HOME, no reconciler thread) over a
hand-built state.db with upstream's messages/sessions columns.

Run:  python3 t_p3_recorder_cursor.py
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading

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


def build(home):
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    g = {"__name__": "aux_recorder_p3c", "HOME": home,
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


def state_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT, cwd TEXT);"
        "CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,"
        " tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL);"
        "INSERT INTO sessions VALUES('s1', 'telegram', '');")
    con.commit()
    return con


def call(cid, name="read_file", path="/tmp/x"):
    c = {"type": "function", "function": {"name": name,
                                          "arguments": json.dumps({"path": path})}}
    if cid:
        c["id"] = cid
    return c


def add(con, ts, calls=None, result_for=None):
    if calls is not None:
        con.execute("INSERT INTO messages(session_id, role, tool_calls, timestamp)"
                    " VALUES('s1', 'assistant', ?, ?)", (json.dumps(calls), ts))
    else:
        con.execute("INSERT INTO messages(session_id, role, content, tool_call_id,"
                    " tool_name, timestamp) VALUES('s1', 'tool', 'ok', ?, 'read_file', ?)",
                    (result_for, ts))
    con.commit()


def n_actions(g, where="1=1", params=()):
    c = sqlite3.connect(g["REC_DB"])
    try:
        return c.execute("SELECT COUNT(*) FROM actions WHERE " + where, params).fetchone()[0]
    finally:
        c.close()


HOME = tempfile.mkdtemp(prefix="hermes-p3c-")
try:
    G = build(HOME)
    sd = state_db(G["STATE_DB"])

    print("\n== a message sharing the cursor's timestamp is still read ==")
    base = 1_800_000_000.0
    for i in range(499):
        add(sd, base + i, [call("c%d" % i)])
    T = base + 1000
    add(sd, T, [call("c499")])                 # the 500th row: the LIMIT ends here
    add(sd, T, [call("c500")])                 # same timestamp, the 501st row
    G["_reconcile_once"]()
    check("the first pass reads the 500-row page", n_actions(G) == 500, n_actions(G))
    G["_reconcile_once"]()
    check("the next pass picks up the row sharing the cursor's timestamp",
          n_actions(G, "tool_call_id='c500'") == 1, n_actions(G))
    add(sd, T, [call("c501")])                 # lands later, same timestamp again
    G["_reconcile_once"]()
    check("…and one that lands later with that same timestamp",
          n_actions(G, "tool_call_id='c501'") == 1, n_actions(G))
    G["_reconcile_once"]()
    check("re-reading never duplicates a row", n_actions(G) == 502, n_actions(G))

    print("\n== two same-named calls without ids are two rows ==")
    add(sd, T + 5, [call(None, path="/tmp/a"), call(None, path="/tmp/b")])
    G["_reconcile_once"]()
    rows = sqlite3.connect(G["REC_DB"]).execute(
        "SELECT tool_call_id, target FROM actions WHERE tool_call_id LIKE 'sd-%' "
        "ORDER BY id").fetchall()
    check("both calls recorded", [r[1] for r in rows] == ["/tmp/a", "/tmp/b"], rows)
    check("the first keeps the old key shape (nothing recorded before duplicates)",
          rows and rows[0][0].count("-") == 2, rows)

    print("\n== a late tool result does not flip an undone row back to done ==")
    c = sqlite3.connect(G["REC_DB"])
    c.execute("UPDATE actions SET status='undone' WHERE tool_call_id='c501'")
    c.commit()
    c.close()
    add(sd, T + 9, result_for="c501")
    G["_reconcile_once"]()
    check("still undone", n_actions(G, "tool_call_id='c501' AND status='undone'") == 1)
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
