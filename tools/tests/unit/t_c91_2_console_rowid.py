#!/usr/bin/env python3
"""C91.2 (docs/audits/2026-10-21-codebase-audit.md) — the Console poll no
longer full-scans state.db's messages table.

BEFORE: console_activity() (expanders_extra.py, polled every 3 s while the
Console is open) ran its 80-event timeline as ORDER BY m.timestamp DESC and its
24 h stats as WHERE m.timestamp >= cutoff. messages.timestamp has no index of
its own (idx_messages_session leads with session_id), so all three queries
walked every row of the table on every poll, and the cost grew with the
history.

AFTER: the timeline walks the rowid backwards (ORDER BY m.id DESC LIMIT 80) and
the window queries start from a cached lower bound on messages.id, so a poll
touches only the rows in the window.

The REAL console_activity runs over a throwaway state.db that has Hermes's own
messages/sessions columns and indexes (hermes_state.py SCHEMA_SQL). Work is
measured in SQLite VM steps (a progress handler on the dashboard's own
connection), which does not depend on how fast this machine is. Correctness is
checked against an independent oracle that reads every row in Python. The old
code fails the work checks: each of its polls costs about one full scan.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _extra_harness as H  # noqa: E402

check = H.check

# The columns and indexes console_activity can see, copied from
# ~/.hermes/hermes-agent/hermes_state.py (SCHEMA_SQL + DEFERRED_INDEX_SQL).
SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT,
    started_at REAL NOT NULL, ended_at REAL, title TEXT);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT,
    tool_name TEXT, timestamp REAL NOT NULL, token_count INTEGER,
    platform_message_id TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE INDEX idx_sessions_source ON sessions(source);
CREATE INDEX idx_sessions_source_id ON sessions(source, id);
CREATE INDEX idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX idx_messages_session_active ON messages(session_id, active, timestamp);
CREATE INDEX idx_messages_platform_msg_id ON messages(session_id, platform_message_id)
    WHERE platform_message_id IS NOT NULL;
"""
SOURCES = ("cli", "hub", "telegram")
TOOLS = ("terminal", "read_file", "web_search", "skill_view")
N_OLD = 30000            # history older than the 24 h window


def call_json(name):
    return json.dumps([{"function": {"name": name, "arguments": "{\"q\": 1}"}}])


def build(path, now, n_old, recent):
    """n_old rows 3..30 days old, then `recent` (offset_s, kind) rows."""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO sessions(id, source, started_at) VALUES (?,?,0)",
                    [("s%d" % i, SOURCES[i % 3]) for i in range(60)])
    rows = []
    start = now - 30 * 86400
    step = (27 * 86400.0) / max(1, n_old)
    for i in range(n_old):
        tc = call_json(TOOLS[i % 4]) if i % 4 == 0 else None
        rows.append(("s%d" % (i % 60), "assistant", "old %d" % i, tc, None,
                     start + i * step))
    con.executemany("INSERT INTO messages(session_id, role, content, tool_calls, "
                    "tool_name, timestamp) VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    add(path, now, recent)


def add(path, now, recent, sid_base=0):
    con = sqlite3.connect(path)
    recent = sorted(recent, key=lambda r: -r[0])     # Hermes inserts in time order
    for j, (off, kind) in enumerate(recent):
        sid = "s%d" % ((sid_base + j) % 60)
        if kind == "call":
            con.execute("INSERT INTO messages(session_id, role, content, tool_calls, "
                        "timestamp) VALUES (?,?,?,?,?)",
                        (sid, "assistant", "", call_json(TOOLS[j % 4]), now - off))
        elif kind == "result":
            con.execute("INSERT INTO messages(session_id, role, content, tool_name, "
                        "timestamp) VALUES (?,?,?,?,?)",
                        (sid, "tool", "result %d" % j, TOOLS[j % 4], now - off))
        else:
            con.execute("INSERT INTO messages(session_id, role, content, timestamp) "
                        "VALUES (?,?,?,?)", (sid, "user", "hi %d" % j, now - off))
    con.commit()
    con.close()


def oracle(path, now):
    """console_activity's answer, computed by reading every row in Python."""
    con = sqlite3.connect(path)
    src = dict(con.execute("SELECT id, source FROM sessions"))
    rows = con.execute("SELECT id, session_id, tool_calls, tool_name, timestamp "
                       "FROM messages").fetchall()
    con.close()
    lt = time.localtime(now)
    hour_start = int(now) - lt.tm_min * 60 - lt.tm_sec
    midnight = hour_start - lt.tm_hour * 3600
    tl = [r for r in rows if r[2] is not None or r[3] is not None]
    tl.sort(key=lambda r: -r[4])
    events = [(r[4], json.loads(r[2])[0]["function"]["name"] if r[2] else r[3])
              for r in tl[:60]]
    today = 0
    by_source = {"cli": 0, "hub": 0, "telegram": 0}
    hist = [0] * 24
    counts = {}
    for r in rows:
        if r[2] is None:
            continue
        ts = r[4]
        if ts < min(midnight, hour_start - 23 * 3600):
            continue
        nm = json.loads(r[2])[0]["function"]["name"]
        counts[nm] = counts.get(nm, 0) + 1
        if ts >= midnight:
            today += 1
            by_source[src[r[1]]] += 1
        if ts >= hour_start - 23 * 3600:
            idx = 23 if ts >= hour_start else 23 - (int((hour_start - ts) // 3600) + 1)
            if 0 <= idx <= 23:
                hist[idx] += 1
    active = len({r[1] for r in rows if r[4] >= now - 900})
    by_tool = [{"name": k, "count": v}
               for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]]
    return {"events": events, "today_calls": today, "by_source": by_source,
            "histogram": hist, "by_tool": by_tool, "active_sessions": active}


class Meter:
    """Counts SQLite VM steps on every connection console_activity opens."""
    def __init__(self):
        self.steps = 0
        self.real = sqlite3.connect

    def __enter__(self):
        meter = self

        def connect(*a, **kw):
            con = meter.real(*a, **kw)

            def tick():
                meter.steps += 1
                return 0
            con.set_progress_handler(tick, 100)
            return con
        sqlite3.connect = connect
        return self

    def __exit__(self, *exc):
        sqlite3.connect = self.real


def poll(g):
    with Meter() as m:
        out = g["console_activity"]()
    return out, m.steps


def same(name, out, want):
    got = {"events": [(e["ts"], e["tool"]) for e in out["events"]],
           "today_calls": out["stats"]["today_calls"],
           "by_source": out["stats"]["by_source"],
           "histogram": out["stats"]["histogram"],
           "by_tool": out["stats"]["by_tool"],
           "active_sessions": out["stats"]["active_sessions"]}
    bad = [k for k in want if got[k] != want[k]]
    check(name, not bad, [(k, got[k], want[k]) for k in bad])


def full_scan_steps(path):
    """What one pass over the whole messages table costs, in the same units."""
    con = sqlite3.connect(path)
    m = [0]

    def tick():
        m[0] += 1
        return 0
    con.set_progress_handler(tick, 100)
    con.execute("SELECT COUNT(*) FROM messages NOT INDEXED WHERE timestamp >= 0 "
                "AND content IS NOT NULL").fetchone()
    con.close()
    return m[0]


def main():
    print("C91.2: the Console poll reads only the rows in its window")
    now = time.time()
    home = tempfile.mkdtemp(prefix="hermes-c912-")
    g = H.load({"_short_args": lambda name, args: str(args)[:40]}, home=home)
    db = g["STATE_DB"]
    os.makedirs(os.path.dirname(db))
    recent = [(60, "call"), (120, "result"), (300, "user"), (1800, "call"),
              (3 * 3600, "call"), (7 * 3600, "result"), (20 * 3600, "call"),
              (26 * 3600, "call")]
    build(db, now, N_OLD, recent)
    scan = full_scan_steps(db)
    check("the fixture is big enough to tell a scan from a seek (%d steps)" % scan,
          scan > 300, scan)

    out1, s1 = poll(g)
    same("first poll matches the oracle", out1, oracle(db, now))
    out2, s2 = poll(g)
    same("second poll matches the oracle", out2, oracle(db, now))
    check("a warm poll costs well under one full scan (%d vs %d steps)" % (s2, scan),
          s2 * 10 < scan, (s2, scan))
    check("the 24 h window is non-empty (today/histogram really counted)",
          sum(out2["stats"]["histogram"]) >= 4, out2["stats"])

    add(db, now, [(5, "call"), (2, "result"), (1, "call")], sid_base=7)
    out3, s3 = poll(g)
    same("rows added after the bound was cached are counted", out3, oracle(db, now))
    check("the newest event is the newest row", out3["events"][0]["ts"] == now - 1,
          out3["events"][:2])
    check("still cheap after new rows (%d steps)" % s3, s3 * 10 < scan, (s3, scan))

    # review: a state.db rewritten IN PLACE (same inode — `hermes import`
    # extracts it with open("wb")) by a database whose ids are all below the
    # cached bound: the bound must not hide its rows
    tmp = db + ".new"
    build(tmp, now, 50, [(30, "call"), (45, "call"), (2 * 3600, "result")])
    ino = os.stat(db).st_ino
    with open(tmp, "rb") as f:
        data = f.read()
    os.remove(tmp)
    with open(db, "r+b") as f:
        f.write(data)
        f.truncate()
    check("the rewrite kept the inode (the cache key cannot see it)",
          os.stat(db).st_ino == ino)
    out_ip, _ = poll(g)
    same("a state.db rewritten in place is read correctly", out_ip, oracle(db, now))
    check("its two tool calls are counted (rewritten in place)",
          sum(out_ip["stats"]["histogram"]) == 2, out_ip["stats"]["histogram"])

    # a state.db replaced underneath (new inode, smaller ids): no stale bound
    tmp = db + ".new"
    build(tmp, now, 50, [(30, "call"), (40, "call"), (90 * 60, "result")])
    os.replace(tmp, db)
    out4, _ = poll(g)
    same("a replaced state.db is read from scratch", out4, oracle(db, now))
    check("its two tool calls are counted", sum(out4["stats"]["histogram"]) == 2,
          out4["stats"]["histogram"])

    # nothing in the window yet, then a row arrives
    tmp = db + ".new"
    build(tmp, now, 2000, [])
    os.replace(tmp, db)
    out5, _ = poll(g)
    same("an empty window reports zeros", out5, oracle(db, now))
    check("no calls today", out5["stats"]["today_calls"] == 0, out5["stats"])
    add(db, now, [(10, "call")])
    out6, s6 = poll(g)
    same("the first row into an empty window is counted", out6, oracle(db, now))
    check("today_calls is 1", out6["stats"]["today_calls"] == 1, out6["stats"])
    scan5 = full_scan_steps(db)
    check("cheap with an empty window too (%d vs %d steps)" % (s6, scan5),
          s6 * 5 < scan5, (s6, scan5))
    H.finish()


if __name__ == "__main__":
    main()
