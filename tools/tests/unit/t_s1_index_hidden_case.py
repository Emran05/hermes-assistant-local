#!/usr/bin/env python3
"""Seam S1-8 (audit 2026-11-09, E69 defence in depth) — the search index.

server.py now treats session ids case-insensitively (`session_hidden`,
`session_key`): the chat store is on case-insensitive APFS, so
`chats/NEEDSYOU.json` IS the needs-you drafts file. aux_index still compared
with an exact-case `sid in HIDDEN_SESSIONS`:
  * a hidden session saved under a case variant (NEEDSYOU, __PREWARM__) was
    indexed by the sweep and by a touch, so its quoted third-party messages
    became searchable (and reachable through MCP hermes_search);
  * a touch with a case variant of a real id wrote a second row
    (`chat:mychat` beside `chat:MyChat`) until the next sweep pruned it.
Both now go through server.py's helpers (resolved by name at call time,
literal fallback kept), and a chat touch resolves to the on-disk spelling.

Real server.py import (aux chain included) on a throwaway HOME; no HTTP, no
model, icalBuddy stubbed out.
"""
import json
import os
import sys
import tempfile
import time

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-s1-ixcase-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
CH = os.path.join(DASH, "chats")
os.makedirs(CH, exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))
NOW = time.time()

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  %s" % (name, str(extra)[:400]))


def chat(sid, words):
    json.dump({"title": "", "messages": [
        {"role": "user", "text": "please remember %s" % words, "ts": NOW},
        {"role": "bot", "text": "noted, %s" % words, "ts": NOW + 1}]},
        open(os.path.join(CH, sid + ".json"), "w"))


chat("NEEDSYOU", "the quoted message zebradraft")
chat("__Prewarm__", "the warmup quokkawarm")
chat("MyChat", "the real conversation ibexreal")

os.environ["HOME"] = HOME
sys.path.insert(0, REPO)
import server                                    # noqa: E402
assert server.DATA == DASH, server.DATA
server._ix_cal_lines = lambda: []


def rows(where="1=1", args=()):
    con = server._ix_conn()
    try:
        return [r[0] for r in con.execute("SELECT id FROM items WHERE " + where, args)]
    finally:
        con.close()


def chat_rows():
    return rows("source='chat'")


sw = server.index_sweep("s1")
check("the sweep ran", sw.get("ok") is True, sw)
print("\n== hidden sessions in any letter case ==")
check("NEEDSYOU (the drafts file, upper case) is not indexed by the sweep",
      "chat:NEEDSYOU" not in chat_rows(), chat_rows())
check("__Prewarm__ is not indexed by the sweep", "chat:__Prewarm__" not in chat_rows(),
      chat_rows())
check("the real conversation is", "chat:MyChat" in chat_rows(), chat_rows())
server.index_touch("chat", "NEEDSYOU", sync=True)
server.index_touch("chat", "needsYou", sync=True)
check("a touch on a case variant of a hidden id writes no row",
      not [r for r in chat_rows() if r.lower() == "chat:needsyou"], chat_rows())
st = server.GET_ROUTES["/api/search"](server.RouteCtx(query={"q": ["zebradraft"]}))
st = st[0] if isinstance(st, tuple) else st
check("its quoted text is not searchable", (st or {}).get("results") == [], st)

print("\n== a case variant of a real id ==")
server.index_touch("chat", "mychat", sync=True)
server.index_touch("chat", "MYCHAT", sync=True)
mine = [r for r in chat_rows() if r.lower() == "chat:mychat"]
check("still ONE row for the conversation, under its on-disk id",
      mine == ["chat:MyChat"], mine)
st = server.GET_ROUTES["/api/search"](server.RouteCtx(query={"q": ["ibexreal"]}))
st = st[0] if isinstance(st, tuple) else st
check("search finds it once", [r.get("id") for r in (st or {}).get("results", [])]
      == ["chat:MyChat"], st)
os.remove(os.path.join(CH, "MyChat.json"))
server.index_touch("chat", "MYCHAT", sync=True)
check("a touch with a case variant after the delete prunes the row",
      not [r for r in chat_rows() if r.lower() == "chat:mychat"], chat_rows())

print("\n== the literal fallback (server.py's helpers absent) ==")
saved = {k: server.__dict__.pop(k) for k in ("session_hidden", "session_key")}
try:
    check("fallback: NEEDSYOU is hidden", server._ix_chat_item("NEEDSYOU") is None)
    check("fallback: a real id is not", server._ix_chat_item("nosuch") is None
          and "NEEDSYOU" not in [s for s, _p in server._ix_chat_paths()],
          server._ix_chat_paths())
finally:
    server.__dict__.update(saved)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
