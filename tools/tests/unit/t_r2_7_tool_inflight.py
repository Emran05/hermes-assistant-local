#!/usr/bin/env python3
"""Round-2 regression test for R2-7 (2026-10-08 audit P3 prose, model
lifecycle): idle-suspend booted the model out under a long tool run.

BEFORE: idle time was measured from the newest state.db message. Hermes
persists the assistant tool-call row BEFORE the tool runs and writes nothing
until it returns, so one Telegram/hub tool call running longer than
_idle_min() (10 min by default) looked idle, and idle_suspend_loop booted the
model out in the middle of that run.

AFTER: _newest_external_turn_ts() answers "now" while a telegram/hub
session's newest row is an assistant row with tool_calls (no result after it)
under an hour old — in the same single read-only query. Prewarm and cli
sessions still never count; a finished, stale or empty tool call does not.

REAL _newest_external_turn_ts + idle_suspend_loop, ast-extracted from
dashboard/server.py, over a fake state.db built with Hermes' own messages /
sessions columns and indexes; a fake clock; launchctl stubbed.
HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import threading
import urllib.parse
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t_r2_6_lifecycle_races as L  # noqa: E402  (shared loader/clock)

check = L.check
NOW = 1_000_000.0

SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT,
                       started_at REAL NOT NULL DEFAULT 0);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id), role TEXT NOT NULL,
    content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,
    timestamp REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE INDEX idx_sessions_source ON sessions(source);
CREATE INDEX idx_messages_session ON messages(session_id, timestamp);
"""
CALL = '[{"id": "c1", "type": "function", "function": {"name": "terminal"}}]'
CALL2 = ('[{"id": "c1", "type": "function", "function": {"name": "read_file"}}, '
         '{"id": "c2", "type": "function", "function": {"name": "terminal"}}]')
OPTIONAL = ["EXTERNAL_TOOL_INFLIGHT_S", "_EXT_TURN_SQL", "_EXT_TURN_CALLS_JSON"]


def make_db(path, sessions, rows):
    if os.path.exists(path):
        os.unlink(path)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO sessions (id, source, title) VALUES (?,?,?)", sessions)
    con.executemany("INSERT INTO messages (session_id, role, tool_calls, tool_call_id, "
                    "timestamp) VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()


def ns_for(db, clock_t=NOW):
    ns = {"os": os, "urllib": urllib, "STATE_DB": db,
          "time": SimpleNamespace(time=lambda: clock_t),
          "PREWARM_SOURCE": "prewarm", "PREWARM_TITLE": "__prewarm__"}
    L.load(["_newest_external_turn_ts"], ns, optional=OPTIONAL)
    return ns


def test_probe(tmp):
    print("\n1. _newest_external_turn_ts(): a running tool call is activity now")
    db = os.path.join(tmp, "state.db")
    m20 = NOW - 20 * 60
    cases = (
        ("a telegram tool call started 20 min ago, no result yet",
         [("t1", "telegram", None)],
         [("t1", "user", None, None, m20 - 5), ("t1", "assistant", CALL, None, m20)],
         lambda v: v >= NOW, True),
        ("...the same in a hub session",
         [("h1", "hub", "Plan")],
         [("h1", "user", None, None, m20 - 5), ("h1", "assistant", CALL, None, m20)],
         lambda v: v >= NOW, False),
        ("a tool call that already has its result is just its timestamp",
         [("t1", "telegram", None)],
         [("t1", "assistant", CALL, None, m20), ("t1", "tool", None, "c1", m20 + 1)],
         lambda v: v == m20 + 1, False),
        ("an unanswered tool call older than an hour is a crashed run, not activity",
         [("t1", "telegram", None)],
         [("t1", "assistant", CALL, None, NOW - 90 * 60)],
         lambda v: v == NOW - 90 * 60, False),
        ("an assistant row with an empty tool_calls list is not a tool run",
         [("t1", "telegram", None)],
         [("t1", "assistant", "[]", None, m20)],
         lambda v: v == m20, False),
        ("a cli session's tool call never counts (the briefing's own -z turns)",
         [("c1", "cli", None)],
         [("c1", "assistant", CALL, None, m20)],
         lambda v: v == 0.0, False),
        ("nor does the prewarm session (by source or by title)",
         [("p1", "prewarm", None), ("p2", "hub", "__prewarm__")],
         [("p1", "assistant", CALL, None, m20), ("p2", "assistant", CALL, None, m20)],
         lambda v: v == 0.0, False),
        ("REVIEW FIX: the SECOND call of a sequential batch is still running (the "
         "first call's result is the newest row)",
         [("t1", "telegram", None)],
         [("t1", "assistant", CALL2, None, m20), ("t1", "tool", None, "c1", m20 + 2)],
         lambda v: v >= NOW, None),
        ("...but once every call of the batch has its result it is just that ts",
         [("t1", "telegram", None)],
         [("t1", "assistant", CALL2, None, m20), ("t1", "tool", None, "c1", m20 + 2),
          ("t1", "tool", None, "c2", m20 + 9)],
         lambda v: v == m20 + 9, False),
        ("an interrupted batch (cancelled results are result rows too) is not running",
         [("t1", "telegram", None)],
         [("t1", "assistant", CALL2, None, m20), ("t1", "tool", None, "c1", m20 + 1),
          ("t1", "tool", None, "c2", m20 + 1)],
         lambda v: v == m20 + 1, False),
        ("a user message sent while the tool runs does not end the run",
         [("t1", "telegram", None)],
         [("t1", "assistant", CALL, None, m20), ("t1", "user", None, None, m20 + 60)],
         lambda v: v >= NOW, False),
        ("a later final answer (no tool calls) ends it",
         [("t1", "telegram", None)],
         [("t1", "assistant", CALL2, None, m20), ("t1", "tool", None, "c1", m20 + 1),
          ("t1", "tool", None, "c2", m20 + 2), ("t1", "assistant", None, None, m20 + 3)],
         lambda v: v == m20 + 3, False),
        ("a telegram run in flight while another hub chat finished later still counts",
         [("t1", "telegram", None), ("h1", "hub", "x")],
         [("t1", "assistant", CALL, None, m20), ("h1", "user", None, None, NOW - 300),
          ("h1", "assistant", None, None, NOW - 290)],
         lambda v: v >= NOW, False),
    )
    for label, sessions, rows, ok, fix in cases:
        make_db(db, sessions, rows)
        v = ns_for(db)["_newest_external_turn_ts"]()
        check(("THE FIX: " if fix else "") + label, ok(v), v)
    # JSON1 missing (a hypothetical SQLite build): the probe degrades to "one
    # result per call row" instead of failing the whole read
    import sqlite3 as _real_sqlite3
    make_db(db, [("t1", "telegram", None)],
            [("t1", "user", None, None, m20 - 5), ("t1", "assistant", CALL, None, m20)])

    class NoJsonCon:
        def __init__(self, con):
            self.con = con

        def execute(self, sql, *a):
            if "json_" in sql:
                raise _real_sqlite3.OperationalError("no such function: json_valid")
            return self.con.execute(sql, *a)

        def close(self):
            self.con.close()
    fake = SimpleNamespace(connect=lambda *a, **k: NoJsonCon(_real_sqlite3.connect(*a, **k)),
                           OperationalError=_real_sqlite3.OperationalError)
    saved = sys.modules.get("sqlite3")
    sys.modules["sqlite3"] = fake
    try:
        v = ns_for(db)["_newest_external_turn_ts"]()
    finally:
        sys.modules["sqlite3"] = saved
    check("without JSON1 the single-call run still reads as in flight (never 0.0)",
          v >= NOW, v)
    os.unlink(db)
    check("no state.db -> 0.0", ns_for(db)["_newest_external_turn_ts"]() == 0.0)
    with open(db, "w") as f:
        f.write("not a database")
    check("an unreadable state.db -> 0.0, never raises",
          ns_for(db)["_newest_external_turn_ts"]() == 0.0)


def test_query_plan(tmp):
    print("\n2. the REAL query is index lookups, not a scan of messages")
    db = os.path.join(tmp, "plan.db")
    make_db(db, [("t1", "telegram", None)], [("t1", "assistant", CALL, None, NOW)])
    ns = ns_for(db)
    sql = ns.get("_EXT_TURN_SQL")
    if not sql:
        check("the in-flight query is a module constant (_EXT_TURN_SQL)", False)
        return
    con = sqlite3.connect(db)
    # hermes_state.py's DEFERRED index, so the planner has Hermes' real choice
    con.execute("CREATE INDEX idx_messages_session_active "
                "ON messages(session_id, active, timestamp)")
    plan = con.execute("EXPLAIN QUERY PLAN " + sql.format(
        calls=ns["_EXT_TURN_CALLS_JSON"]),
        ("prewarm", "__prewarm__", "prewarm", "__prewarm__", NOW - 3600)).fetchall()
    con.close()
    text = " | ".join(r[-1] for r in plan)
    check("messages are reached only through an index (no SCAN m/m2/a/t)",
          "idx_messages_session" in text
          and not any(("SCAN %s " % a) in text + " " or text.endswith("SCAN " + a)
                      for a in ("m", "m2", "a", "t")), text)


def test_idle_loop(tmp):
    print("\n3. idle_suspend_loop keeps the model up under a long Telegram tool run")
    db = os.path.join(tmp, "state.db")
    mark = os.path.join(tmp, "agent-idle-suspended")
    if os.path.exists(mark):
        os.unlink(mark)
    clock = L.Clock(1)
    make_db(db, [("t1", "telegram", None)],
            [("t1", "user", None, None, clock.t - 30 * 60 - 5),
             ("t1", "assistant", CALL, None, clock.t - 30 * 60)])
    boots = []

    def run(argv, **kw):
        boots.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    g = {"time": clock, "sys": sys, "os": os, "urllib": urllib, "threading": threading,
         "subprocess": SimpleNamespace(run=run), "STATE_DB": db,
         "PREWARM_SOURCE": "prewarm", "PREWARM_TITLE": "__prewarm__",
         "idle_suspend_enabled": lambda: True, "agent_paused": lambda: False,
         "agent_idle_suspended": lambda: os.path.exists(mark),
         "IDLE_SUSPEND_FILE": mark, "agent_wake": lambda wait=True: None,
         "model_online": lambda: True, "_chat_jobs_active": lambda: False,
         "_briefing_generating": False, "_mlx_proc_alive": lambda: True,
         "_start_token_fresh": lambda: False, "_down_seen_at": [0.0],
         "_DOWN_PERSIST_S": 60, "_last_user_activity": clock.t - 3 * 3600,
         "_idle_min": lambda: 10.0, "MLX_LABEL": "com.hermes.mlx-server",
         "_widget_cache": {}, "_model_holds": {}, "_model_holds_lock": threading.Lock(),
         "_mlx_primary_down": lambda timeout=3.0: True, "_idle_bootout_warned": [0.0],
         "model_always_on": lambda: False,
         "_lifecycle_lock": threading.Lock()}     # E98
    L.load(["idle_suspend_loop", "_model_held", "_newest_external_turn_ts"], g,
           optional=OPTIONAL)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            g["idle_suspend_loop"]()
        except L.Stop:
            pass
    check("THE FIX: a 30-min tool run is not booted out (no launchctl bootout)",
          boots == [] and not os.path.exists(mark), (boots, err.getvalue()))
    # once the tool returns and nothing else happens, idle-suspend resumes
    con = sqlite3.connect(db)
    con.execute("INSERT INTO messages (session_id, role, tool_call_id, timestamp) "
                "VALUES ('t1', 'tool', 'c1', ?)", (clock.t - 20 * 60,))
    con.commit()
    con.close()
    g["time"] = L.Clock(1)
    g["time"].t = clock.t
    with contextlib.redirect_stderr(err):
        try:
            g["idle_suspend_loop"]()
        except L.Stop:
            pass
    check("...and once it has returned and 20 min pass idle, it is suspended",
          len(boots) == 1 and os.path.exists(mark), (boots, err.getvalue()[-300:]))


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-7-") as tmp:
        test_probe(tmp)
        test_query_plan(tmp)
        test_idle_loop(tmp)
    print("\nTESTS %d passed %d failed" % (L.PASSES[0], len(L.FAILS)))
    sys.exit(1 if L.FAILS else 0)


if __name__ == "__main__":
    main()
