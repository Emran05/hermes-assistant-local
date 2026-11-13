#!/usr/bin/env python3
"""C120 (audit 2026-10-21): a branch whose source has no live agent session
keeps its pre-branch context after serve drops the live session.

br_seed_params() adds `parent_session_id` only while the SOURCE chat's
serve_key is a live state.db row, and upstream `_persist_branch_seed`
(tui_gateway/server.py) writes a seed to state.db only for a session that has
a parent. Without one the seed lived only in serve's memory: turn 1 was fine,
then the 20 s orphan reaper dropped the live session, the next turn's
session.resume rebuilt it from state.db, and the branch answered as if nothing
before the branch point had happened.

This runs the REAL hermes_rpc.run_turn — as wrapped by the REAL aux_branch.py —
against a small fake serve that models exactly the upstream rules that matter:
session.create seeds the live history from `messages`; the first prompt.submit
writes the sessions row (with its parent) to a real sqlite state.db and
persists the seed ONLY when a parent is set; session.resume rebuilds history
from the persisted rows; reap() is the orphan reaper. Every fake reply reports
which texts the agent could see, so "did it remember?" is a plain assertion.
No network, no model, throwaway HOME.
"""
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid

FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for i, a in enumerate(sys.argv):
    if a == "--repo" and i + 1 < len(sys.argv):
        REPO = os.path.abspath(sys.argv[i + 1])
DASH = os.path.join(REPO, "dashboard")
TMP = tempfile.mkdtemp(prefix="hermes-c120-")
os.environ["HOME"] = TMP
sys.path.insert(0, DASH)
import hermes_rpc  # noqa: E402  (the real module; its socket class is replaced below)

STATE_DB = os.path.join(TMP, "state.db")
con = sqlite3.connect(STATE_DB)
con.executescript("""
CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT,
                      parent_session_id TEXT REFERENCES sessions(id),
                      model_config TEXT);
CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT);
""")
con.commit()
con.close()


class FakeServe(object):
    """The upstream behaviour run_turn + aux_branch depend on, nothing more."""
    live = {}            # sid -> {"key", "history", "parent", "row"}
    creates = []         # params of every session.create

    def __init__(self):
        self.events = []

    @classmethod
    def reap(cls):
        cls.live.clear()

    def _db(self):
        return sqlite3.connect(STATE_DB)

    def call(self, method, params, timeout=30, on_sent=None):
        if on_sent:
            on_sent()
        if method == "session.status":
            if params["session_id"] not in self.live:
                raise hermes_rpc.WSError("session not found")
            return {}
        if method == "session.resume":
            key = params["session_id"]
            db = self._db()
            row = db.execute("SELECT 1 FROM sessions WHERE id=?", (key,)).fetchone()
            hist = [{"role": r, "content": c} for r, c in db.execute(
                "SELECT role, content FROM messages WHERE session_id=? ORDER BY id", (key,))]
            db.close()
            if not row:
                raise hermes_rpc.WSError("no such session")
            sid = uuid.uuid4().hex[:8]
            self.live[sid] = {"key": key, "history": hist, "parent": None, "row": True}
            return {"session_id": sid}
        if method == "session.create":
            self.creates.append(dict(params))
            sid, key = uuid.uuid4().hex[:8], "key-" + uuid.uuid4().hex[:6]
            seed = [dict(m) for m in (params.get("messages") or [])]
            self.live[sid] = {"key": key, "history": seed,
                              "parent": params.get("parent_session_id"), "row": False,
                              "seed": list(seed)}
            return {"session_id": sid, "stored_session_id": key}
        if method == "prompt.submit":
            s = self.live[params["session_id"]]
            db = self._db()
            if not s["row"]:                      # _ensure_session_db_row
                # upstream stamps _branched_from exactly when a parent is set
                db.execute("INSERT OR IGNORE INTO sessions VALUES (?,?,?,?)",
                           (s["key"], "hub", s["parent"],
                            json.dumps({"_branched_from": s["parent"]})
                            if s["parent"] else None))
                if s["parent"]:                   # _persist_branch_seed
                    for m in s["seed"]:
                        db.execute("INSERT INTO messages(session_id, role, content) "
                                   "VALUES (?,?,?)", (s["key"], m["role"], m["content"]))
                s["row"] = True
            seen = [m["content"] for m in s["history"]]
            reply = "SEEN:" + json.dumps(seen)
            for role, content in (("user", params["text"]), ("assistant", reply)):
                db.execute("INSERT INTO messages(session_id, role, content) "
                           "VALUES (?,?,?)", (s["key"], role, content))
                s["history"].append({"role": role, "content": content})
            db.commit()
            db.close()
            self.events.append({"type": "message.complete",
                                "session_id": params["session_id"],
                                "payload": {"text": reply}})
            return {"status": "streaming"}
        return {}

    def next_event(self, timeout=1.0):
        return self.events.pop(0) if self.events else None

    def close(self):
        pass


hermes_rpc.ServeSession = FakeServe
hermes_rpc.RECORDER_HOOK = None

# ---- the server.py globals aux_branch.py uses -------------------------------
CHATS = {}
G = {"__name__": "aux_branch_c120", "os": os, "sys": sys, "uuid": uuid,
     "SESSION_RE": re.compile(r"^[A-Za-z0-9_.-]{1,80}$"),
     "PREWARM_SESSION": "__prewarm__", "STATE_DB": STATE_DB,
     "chat_path": lambda sid: os.path.join(TMP, sid + ".json") if sid in CHATS
     else os.path.join(TMP, "missing", sid),
     "load_chat": lambda sid: json.loads(json.dumps(CHATS[sid])),
     "save_chat": lambda sid, c, **kw: CHATS.__setitem__(sid, c),
     "save_chat_update": lambda sid, fn: fn(CHATS[sid]),
     "register_get": lambda p, f: None, "register_post": lambda p, f: None}
for sid in ("src", "src-live"):
    open(os.path.join(TMP, sid + ".json"), "w").close()
path = os.path.join(DASH, "aux_branch.py")
exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), G)
check("SEED_HOOK wired", hermes_rpc.SEED_HOOK is G["br_seed_params"])

PREFIX = [{"role": "user", "text": "the plan is called Bluebird"},
          {"role": "bot", "text": "Noted: the plan is Bluebird."},
          {"role": "user", "text": "it ships on a Tuesday"},
          {"role": "bot", "text": "Tuesday it is."}]


def turn(chat, text):
    """What server.py's /api/chat + _chat_worker do around run_turn."""
    chat["messages"].append({"role": "user", "text": text})
    job = {}
    hermes_rpc.run_turn(job, chat, text, lambda: None)
    chat["messages"].append({"role": "bot", "text": job.get("reply", "")})
    return json.loads(job.get("reply", "SEEN:[]")[5:] or "[]")


print("\n1. a branch of a chat with NO agent session")
CHATS["src"] = {"messages": list(PREFIX)}                 # no serve_key at all
branch = {"messages": json.loads(json.dumps(PREFIX)),
          "forked_from": {"session": "src", "at_index": 4}}
seen = turn(branch, "what is the plan called?")
check("turn 1 sees the pre-branch context (live seed)",
      "the plan is called Bluebird" in seen, seen)
check("turn 1 had no parent to give (the C120 precondition)",
      "parent_session_id" not in FakeServe.creates[-1], FakeServe.creates[-1])
FakeServe.reap()                                          # 20 s later
seen = turn(branch, "and when does it ship?")
check("after the reaper, turn 2 STILL sees the pre-branch context",
      "the plan is called Bluebird" in seen and "it ships on a Tuesday" in seen, seen)
check("and its own turn 1", "what is the plan called?" in seen, seen)
check("the re-created session is parented to the branch's own first row",
      FakeServe.creates[-1].get("parent_session_id") not in (None, ""),
      FakeServe.creates[-1])
n_creates = len(FakeServe.creates)
FakeServe.reap()
seen = turn(branch, "remind me of everything")
check("after another reap, a plain resume keeps it all (seed now persisted)",
      "the plan is called Bluebird" in seen and "and when does it ship?" in seen, seen)
check("so the re-seed happened exactly once", len(FakeServe.creates) == n_creates,
      len(FakeServe.creates) - n_creates)

print("\n2. a branch whose source HAS a live agent session is unchanged")
db = sqlite3.connect(STATE_DB)
db.execute("INSERT INTO sessions VALUES ('key-src-live', 'hub', NULL, NULL)")
db.commit()
db.close()
CHATS["src-live"] = {"messages": list(PREFIX), "serve_key": "key-src-live"}
b2 = {"messages": json.loads(json.dumps(PREFIX)),
      "forked_from": {"session": "src-live", "at_index": 4}}
before = len(FakeServe.creates)
turn(b2, "first question")
check("parented to the source", FakeServe.creates[-1].get("parent_session_id")
      == "key-src-live", FakeServe.creates[-1])
FakeServe.reap()
seen = turn(b2, "second question")
check("resumes without re-creating", len(FakeServe.creates) == before + 1,
      len(FakeServe.creates) - before)
check("with its context", "the plan is called Bluebird" in seen, seen)

print("\n3. an ordinary conversation never goes near the re-seed path")
plain = {"messages": []}
before = len(FakeServe.creates)
turn(plain, "hello")
FakeServe.reap()
turn(plain, "again")
check("one create, then a resume", len(FakeServe.creates) == before + 1,
      len(FakeServe.creates) - before)
check("and no seed params on it", "messages" not in FakeServe.creates[-1],
      FakeServe.creates[-1])

print("\n4. a branch whose seed WAS persisted keeps it after its source is deleted")
db = sqlite3.connect(STATE_DB)
db.execute("INSERT INTO sessions VALUES ('key-src-gone', 'hub', NULL, NULL)")
db.commit()
db.close()
CHATS["src-gone"] = {"messages": list(PREFIX), "serve_key": "key-src-gone"}
open(os.path.join(TMP, "src-gone.json"), "w").close()
b4 = {"messages": json.loads(json.dumps(PREFIX)),
      "forked_from": {"session": "src-gone", "at_index": 4}}
turn(b4, "first question")
check("parented to the source at creation",
      FakeServe.creates[-1].get("parent_session_id") == "key-src-gone", FakeServe.creates[-1])
db = sqlite3.connect(STATE_DB)                 # hermes_state.delete_session(source):
db.execute("UPDATE sessions SET parent_session_id = NULL "      # orphan the branch,
           "WHERE parent_session_id = 'key-src-gone'")         # keep its marker
db.execute("DELETE FROM sessions WHERE id = 'key-src-gone'")
db.commit()
db.close()
FakeServe.reap()
before = len(FakeServe.creates)
seen = turn(b4, "second question")
check("it resumes its persisted session instead of being re-created",
      len(FakeServe.creates) == before, len(FakeServe.creates) - before)
check("with its context", "the plan is called Bluebird" in seen
      and "first question" in seen, seen)

print("\n5. an older state.db without model_config still answers")
old_db = os.path.join(TMP, "old-state.db")
db = sqlite3.connect(old_db)
db.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT, "
                 "parent_session_id TEXT);"
                 "INSERT INTO sessions VALUES ('k-root', 'hub', NULL);"
                 "INSERT INTO sessions VALUES ('k-kid', 'hub', 'k-root');")
db.commit()
db.close()
G["STATE_DB"] = old_db
check("a parentless row reads as unanchored", G["_br_row_parent"]("k-root") == "",
      G["_br_row_parent"]("k-root"))
check("a parented row reads as anchored", G["_br_row_parent"]("k-kid") == "k-root",
      G["_br_row_parent"]("k-kid"))
G["STATE_DB"] = STATE_DB

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
sys.exit(1 if FAILS else 0)
