#!/usr/bin/env python3
"""C91.1 (round 2 of the 2026-10-21 audit): the recorder's state.db
reconciler reads a rowid RANGE, not the whole messages table, every tick.

_reconcile_once() filtered on `m.timestamp > cursor OR (m.timestamp = cursor
AND m.id > cursor_id)`.  Hermes's state.db has no messages(timestamp) index —
only (session_id, timestamp) and (session_id, active, timestamp) — and it is
Hermes's file, opened read-only, so no index can be added.  Every 5 s tick
therefore full-scanned messages, at a cost that grows with the whole history.
Now a stored rowid floor bounds the read (`m.id > floor AND m.id <= hi`), and
the (timestamp, id) cursor and its tie-break are unchanged.

Exec's the REAL aux_recorder.py (throwaway HOME, no reconciler thread) over a
fake state.db built from Hermes's real sessions/messages DDL and indexes
(hermes_state.py SCHEMA_SQL + DEFERRED_INDEX_SQL), with sqlite3 shimmed only
for the read-only state.db connection so the test can see the query plan and
count the VM steps the reconcile query costs.

Fails on the old code: the plan is a full SCAN and the steady-state cost
doubles when the history doubles.

Run:  python3 t_c91_1_reconcile_rowid.py
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


# Hermes's own DDL for the two tables the reconciler joins, and every index on
# them (hermes-agent hermes_state.py: SCHEMA_SQL + DEFERRED_INDEX_SQL).
HERMES_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, session_key TEXT,
    chat_id TEXT, chat_type TEXT, thread_id TEXT, model TEXT, model_config TEXT,
    system_prompt TEXT, parent_session_id TEXT, started_at REAL NOT NULL,
    ended_at REAL, end_reason TEXT, message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT, git_branch TEXT, git_repo_root TEXT, billing_provider TEXT,
    billing_base_url TEXT, billing_mode TEXT, estimated_cost_usd REAL,
    actual_cost_usd REAL, cost_status TEXT, cost_source TEXT, pricing_version TEXT,
    title TEXT, api_call_count INTEGER DEFAULT 0, handoff_state TEXT,
    handoff_platform TEXT, handoff_error TEXT,
    compression_failure_cooldown_until REAL, compression_failure_error TEXT,
    rewind_count INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT,
    tool_name TEXT, timestamp REAL NOT NULL, token_count INTEGER,
    finish_reason TEXT, reasoning TEXT, reasoning_content TEXT,
    reasoning_details TEXT, codex_reasoning_items TEXT, codex_message_items TEXT,
    platform_message_id TEXT, observed INTEGER DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1, compacted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source);
CREATE INDEX IF NOT EXISTS idx_sessions_source_id ON sessions(source, id);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_session_active
    ON messages(session_id, active, timestamp);
CREATE INDEX IF NOT EXISTS idx_sessions_session_key
    ON sessions(session_key, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_gateway_peer
    ON sessions(source, user_id, chat_id, chat_type, thread_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_handoff_state
    ON sessions(handoff_state, started_at);
"""

PLANS, STEPS = [], []


class _StateConn(object):
    """The reconciler's read-only state.db connection, observed: the plan of
    the messages query and its VM step count are recorded."""

    def __init__(self, con):
        object.__setattr__(self, "_c", con)

    def __getattr__(self, k):
        return getattr(self._c, k)

    def __setattr__(self, k, v):
        setattr(self._c, k, v)

    def execute(self, sql, params=()):
        if "FROM messages m" not in sql:
            return self._c.execute(sql, params)
        PLANS.append(" | ".join(str(r[-1]) for r in
                                self._c.execute("EXPLAIN QUERY PLAN " + sql, params)))
        n = [0]

        def tick():
            n[0] += 1
            return 0
        self._c.set_progress_handler(tick, 100)
        rows = self._c.execute(sql, params).fetchall()
        self._c.set_progress_handler(None, 100)
        STEPS.append(n[0] * 100)

        class _R(object):
            def fetchall(self_inner):
                return rows
        return _R()


class _Sqlite3Shim(object):
    def __getattr__(self, k):
        return getattr(sqlite3, k)

    def connect(self, *a, **k):
        con = sqlite3.connect(*a, **k)
        if a and str(a[0]).startswith("file:"):     # the mode=ro state.db read
            return _StateConn(con)
        return con


def build(home):
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    g = {"__name__": "aux_recorder_c911", "HOME": home,
         "HERE": os.path.join(REPO, "dashboard"), "DATA": data,
         "STATE_DB": os.path.join(home, ".hermes", "state.db"),
         "read_json": lambda p, d=None: d, "write_json": lambda p, o: None,
         "get_settings": lambda: {}, "_state_lock": threading.Lock(),
         "_widget_cache": {}, "settings_update": lambda fn: {},
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_recorder_thread_started": True}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    g["sqlite3"] = _Sqlite3Shim()      # resolved by global name at call time
    return g


def state_db(path):
    con = sqlite3.connect(path)
    con.executescript(HERMES_DDL)
    con.execute("INSERT INTO sessions(id, source, started_at) VALUES('s1', 'telegram', 1)")
    con.commit()
    return con


def call(cid, name="read_file", path="/tmp/x"):
    c = {"type": "function", "function": {"name": name,
                                          "arguments": json.dumps({"path": path})}}
    if cid:
        c["id"] = cid
    return c


def add_call(con, ts, cid, commit=True):
    con.execute("INSERT INTO messages(session_id, role, tool_calls, timestamp)"
                " VALUES('s1', 'assistant', ?, ?)", (json.dumps([call(cid)]), ts))
    if commit:
        con.commit()


def add_chatter(con, n, ts0):
    con.executemany("INSERT INTO messages(session_id, role, content, timestamp)"
                    " VALUES('s1', ?, 'hello there', ?)",
                    [("user" if i % 2 else "assistant", ts0 + i * 1e-3) for i in range(n)])
    con.commit()


def n_actions(g, where="1=1", params=()):
    c = sqlite3.connect(g["REC_DB"])
    try:
        return c.execute("SELECT COUNT(*) FROM actions WHERE " + where, params).fetchone()[0]
    finally:
        c.close()


def drain(g, ticks=4):
    for _ in range(ticks):
        g["_reconcile_once"]()


HOME = tempfile.mkdtemp(prefix="hermes-c911-")
try:
    G = build(HOME)
    sd = state_db(G["STATE_DB"])
    base = 1_800_000_000.0
    PAGE = G.get("RECONCILE_PAGE", 500)

    print("\n== steady state reads only what is new ==")
    add_chatter(sd, 20000, base)
    add_call(sd, base + 100, "c-a")
    G["_reconcile_once"]()
    G["_reconcile_once"]()                 # catch-up done, floor settled
    check("the first call is recorded", n_actions(G, "tool_call_id='c-a'") == 1)
    add_call(sd, base + 101, "c-b")
    del STEPS[:], PLANS[:]
    G["_reconcile_once"]()
    s20 = STEPS[-1] if STEPS else -1
    plan = PLANS[-1] if PLANS else ""
    check("a new call is recorded", n_actions(G, "tool_call_id='c-b'") == 1)
    check("the query is a rowid range search on messages (%s)" % plan,
          "SEARCH m USING INTEGER PRIMARY KEY" in plan and "SCAN m" not in plan, plan)

    add_chatter(sd, 20000, base + 200)
    G["_reconcile_once"]()
    add_call(sd, base + 300, "c-c")
    del STEPS[:]
    G["_reconcile_once"]()
    s40 = STEPS[-1] if STEPS else -1
    check("…and recorded with 40k rows of history", n_actions(G, "tool_call_id='c-c'") == 1)
    print("  steady-state VM steps: %d at 20k rows, %d at 40k rows" % (s20, s40))
    check("the per-tick cost does not grow with the history (%d -> %d)" % (s20, s40),
          0 <= s40 <= s20 * 1.2 + 500, (s20, s40))
    check("…and is far below one pass over the table (%d steps for 40k rows)" % s40,
          0 <= s40 < 40000, s40)

    print("\n== no row is skipped ==")
    # An OLDER id with a NEWER timestamp, separated from its neighbours by the
    # page boundary: sorted by (timestamp, id) it is read last, on the second
    # page, after the cursor's id has passed it.
    t0 = base + 1000
    add_call(sd, t0 + 50000, "late-ts", commit=False)          # lowest id, newest ts
    for i in range(PAGE + 100):
        add_call(sd, t0 + i, "p-%d" % i, commit=False)
    sd.commit()
    drain(G)
    check("a whole page and the rest are read",
          n_actions(G, "tool_call_id LIKE 'p-%'") == PAGE + 100,
          n_actions(G, "tool_call_id LIKE 'p-%'"))
    check("the older-id / newer-timestamp row is not skipped",
          n_actions(G, "tool_call_id='late-ts'") == 1)

    # a replay with ORIGINAL timestamps (Hermes's replace_messages) gets new,
    # higher ids; the timestamp cursor still keeps it from being re-read, so an
    # id-less call (keyed by message id) is not recorded twice
    add_call(sd, t0 + 60000, None)
    drain(G)
    n_sd = n_actions(G, "tool_call_id LIKE 'sd-%'")
    add_call(sd, t0 + 60000 - 1, None)                          # old ts, new id
    drain(G)
    check("a re-inserted message with its old timestamp is not re-read",
          n_actions(G, "tool_call_id LIKE 'sd-%'") == n_sd, n_actions(G))

    print("\n== a replaced state.db (ids restart) is still read ==")
    sd.close()
    os.remove(G["STATE_DB"])
    sd = state_db(G["STATE_DB"])
    add_call(sd, t0 + 70000, "fresh-db")
    drain(G)
    check("a call in a recreated state.db is recorded",
          n_actions(G, "tool_call_id='fresh-db'") == 1)
    add_call(sd, t0 + 70001, "fresh-db-2")
    drain(G)
    check("…and so is the next one", n_actions(G, "tool_call_id='fresh-db-2'") == 1)
    sd.close()
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
