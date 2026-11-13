#!/usr/bin/env python3
"""C59.4 — a pin (or approve / archive) switch accepts only a JSON boolean.

`pinned = bool(body.get("pinned"))`: bool("false") is True, so the STRING
"false" pinned the memory fact (POST /api/memory/facts and
/api/memory/facts/update) or the conversation (POST /api/sessions/meta).  The
same coercion sat on the update route's `approve`, `approve_all` and
`archived` — and approve/approve_all are the gate between an imported,
model-writable fact and the prompt.  Anything but true/false is now a 400 and
nothing changes; JSON booleans work as before.

Real server.py import (aux chain included) + the real Handler on a free
loopback port, throwaway HOME.  No model, no ~/.hermes, no network.

Fails on the old code: every string switch answered 200 and flipped the row.
"""
import json, os, shutil, socket, sqlite3, sys, tempfile, threading, time, urllib.error, urllib.request

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-c59-4-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
CH = os.path.join(DASH, "chats")
os.makedirs(CH, exist_ok=True)
os.makedirs(os.path.join(HOME, ".hermes", "memories", "people"), exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))
NOW = time.time()
SID = "chat-2026-10-26-pinme"
json.dump({"title": "pin target", "messages": [
    {"role": "user", "text": "hello there", "ts": NOW},
    {"role": "bot", "text": "hi", "ts": NOW + 1}]},
    open(os.path.join(CH, SID + ".json"), "w"))

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = str(PORT)
sys.path.insert(0, REPO)
import server                                    # noqa: E402
assert server.DATA == DASH, server.DATA
server._ix_cal_lines = lambda: []                # never shell out to icalBuddy

from http.server import ThreadingHTTPServer      # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", PORT), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, str(extra)[:300]))


def req(path, body=None):
    h = {"Host": "127.0.0.1:%d" % PORT}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


def chat_pinned():
    return json.load(open(os.path.join(CH, SID + ".json"))).get("pinned")


def row(fid):
    con = sqlite3.connect(server.ML_DB)
    try:
        r = con.execute("SELECT pinned, archived, review FROM facts WHERE id=?",
                        (fid,)).fetchone()
    finally:
        con.close()
    return {"pinned": r[0], "archived": r[1], "review": r[2]} if r else None


try:
    print("\n=== POST /api/sessions/meta ===")
    for bad in ("false", "true", 1, 0, None, [], {}):
        c, d = req("/api/sessions/meta", {"session": SID, "pinned": bad})
        ok("pinned=%r -> 400, conversation unchanged" % (bad,),
           c == 400 and d.get("ok") is False and not chat_pinned(), (c, d, chat_pinned()))
    c, d = req("/api/sessions/meta", {"session": SID, "pinned": True})
    ok("pinned=true pins", c == 200 and d.get("pinned") is True and chat_pinned() is True, (c, d))
    c, d = req("/api/sessions/meta", {"session": SID, "pinned": "false"})
    ok("the string 'false' does not touch a pinned conversation either",
       c == 400 and chat_pinned() is True, (c, d))
    c, d = req("/api/sessions/meta", {"session": SID, "pinned": False})
    ok("pinned=false unpins", c == 200 and d.get("pinned") is False and chat_pinned() is False, (c, d))
    c, d = req("/api/sessions/meta", {"session": SID, "title": "renamed"})
    ok("a title-only change still works", c == 200 and d.get("title") == "renamed", (c, d))

    print("\n=== POST /api/memory/facts ===")
    c, d = req("/api/memory/facts", {"text": "Likes long walks", "origin": "card",
                                     "pinned": "false"})
    ok("add with pinned='false' -> 400, nothing stored",
       c == 400 and d.get("ok") is False, (c, d))
    c, d = req("/api/memory/facts?q=walks")
    ok("... and the fact is not in the store", c == 200 and d.get("facts") == [], d)
    c, d = req("/api/memory/facts", {"text": "Likes long walks", "origin": "card"})
    ok("add without pinned works, unpinned", c == 200 and d["fact"]["pinned"] in (0, False), (c, d))
    fid = d["fact"]["id"]
    c, d = req("/api/memory/facts", {"text": "Reads before bed", "origin": "card",
                                     "pinned": True})
    ok("add with pinned=true pins", c == 200 and d["fact"]["pinned"] in (1, True), (c, d))

    print("\n=== POST /api/memory/facts/update ===")
    for key in ("pinned", "archived", "approve"):
        before = row(fid)
        c, d = req("/api/memory/facts/update", {"id": fid, key: "false"})
        ok("%s='false' -> 400, row unchanged" % key,
           c == 400 and d.get("ok") is False and row(fid) == before, (c, d, row(fid)))
    c, d = req("/api/memory/facts/update", {"id": fid, "pinned": True})
    ok("pinned=true pins", c == 200 and row(fid)["pinned"] == 1, (c, d))
    c, d = req("/api/memory/facts/update", {"id": fid, "pinned": False})
    ok("pinned=false unpins", c == 200 and row(fid)["pinned"] == 0, (c, d))
    c, d = req("/api/memory/facts/update", {"id": fid, "archived": True})
    ok("archived=true archives", c == 200 and row(fid)["archived"] != 0, (c, d))
    c, d = req("/api/memory/facts/update", {"id": fid, "archived": False})
    ok("archived=false restores", c == 200 and row(fid)["archived"] == 0, (c, d))

    # an agent-sourced fact waits for approval; approve_all="false" must not approve it
    c, d = req("/api/memory/facts", {"text": "The owner works at Initech"})
    held = (d.get("fact") or {}).get("id")
    ok("a fact without origin:card is held for review",
       c == 200 and held and row(held)["review"] == 1, (c, d))
    c, d = req("/api/memory/facts/update", {"approve_all": "false"})
    ok("approve_all='false' -> 400 and the held fact stays held",
       c == 400 and row(held)["review"] == 1, (c, d, row(held)))
    c, d = req("/api/memory/facts/update", {"id": held, "approve": 1})
    ok("approve=1 (a number) -> 400", c == 400 and row(held)["review"] == 1, (c, d))
    c, d = req("/api/memory/facts/update", {"approve_all": True, "origin": "card"})
    ok("approve_all=true approves", c == 200 and row(held)["review"] == 0, (c, d))
finally:
    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
