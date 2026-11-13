#!/usr/bin/env python3
"""E69: session ids are compared without regard to letter case.

2026-11-09 audit: every hidden-session and one-turn-per-conversation guard
was an exact-case lookup, SESSION_RE allows upper case, and the chat store
sits on case-insensitive APFS — `chats/NEEDSYOU.json` IS `chats/needsyou.json`.
So `NEEDSYOU` read back the Needs-you drafts (/api/history, hence MCP
chat_get), could be branched into a listed copy, exported, renamed, and
chatted in without the draft's no_human/no_escalate flags; and `chat-ABC`
next to a running `chat-abc` skipped the one-turn check (B11/C113).

The fix compares case-insensitively (it does not refuse upper-case ids: a
conversation whose id has capitals keeps working). Real server.py import
(aux chain included) + the real Handler on a free loopback port, throwaway
HOME; the turn worker is a stub. No model, no serve, no network.
"""
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
REPO = os.path.join(ROOT, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-e69-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
CH = os.path.join(DASH, "chats")
os.makedirs(CH, exist_ok=True)
os.makedirs(os.path.join(HOME, ".hermes", "memories", "people"), exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))
NOW = time.time() + 5          # "this process's" messages: no lost-reply notice
SECRET = "DRAFT-REPLY Sure Anna, Thursday works"
json.dump({"title": "Needs you — drafts", "messages": [
    {"role": "user", "text": "Draft a reply", "ts": NOW},
    {"role": "bot", "text": SECRET, "ts": NOW + 1}]},
    open(os.path.join(CH, "needsyou.json"), "w"))
MIXED = "chat-Mixed-Case"
json.dump({"title": "mixed", "messages": [
    {"role": "user", "text": "capital letters in my id", "ts": NOW},
    {"role": "bot", "text": "still mine", "ts": NOW + 1}]},
    open(os.path.join(CH, MIXED + ".json"), "w"))

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = str(PORT)
sys.path.insert(0, REPO)
import server                                    # noqa: E402
assert server.DATA == DASH, server.DATA
server.SB_BIN = os.path.join(HOME, "no-shortcuts-cli")   # the preamble lists no real Shortcuts
server._ix_cal_lines = lambda: []

WORKER = []


def _stub_worker(job, session, prompt):
    WORKER.append(session)
    job.update(reply="stub reply", ok=True, state="done")
    server._finish_chat_job(job, session)


server._chat_worker = _stub_worker
server.agent_paused = lambda: False
server.mlx_admission = lambda: (True, 1.0, 50.0)

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
        print("  FAIL %s  %s" % (name, str(extra)[:400]))


def req(path, body=None):
    h = {"Host": "127.0.0.1:%d" % PORT}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            txt = resp.read().decode()
            try:
                return resp.status, json.loads(txt)
            except ValueError:
                return resp.status, txt
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


def files():
    return sorted(f for f in os.listdir(CH) if f.endswith(".json"))


try:
    before = files()
    raw = open(os.path.join(CH, "needsyou.json"), "rb").read()
    print("\n=== a case variant of a hidden id is hidden too ===")
    for sid in ("NEEDSYOU", "NeedsYou", "needsYOU", "__PREWARM__"):
        c, d = req("/api/history?session=" + sid)
        ok("GET /api/history?session=%s reads nothing" % sid,
           c == 200 and d.get("messages") == [] and SECRET not in json.dumps(d), (c, d))
        c, d = req("/api/chat", {"message": "hi", "session": sid})
        ok("POST /api/chat in %s is refused" % sid, c == 400 and d.get("ok") is False, (c, d))
        c, d = req("/api/sessions/branch", {"session": sid, "at_index": 2})
        ok("POST /api/sessions/branch of %s is refused" % sid, c == 400, (c, d))
        c, d = req("/api/sessions/export?session=" + sid)
        ok("GET /api/sessions/export?session=%s is refused" % sid,
           c == 400 and SECRET not in json.dumps(d), (c, d))
        c, d = req("/api/sessions/meta", {"session": sid, "title": "mine"})
        ok("POST /api/sessions/meta on %s is refused" % sid, c == 400, (c, d))
    ok("no turn ran in a hidden conversation", WORKER == [], WORKER)
    ok("no conversation file was written", files() == before, files())
    ok("the drafts file is byte-identical",
       open(os.path.join(CH, "needsyou.json"), "rb").read() == raw)
    for name in ("session_hidden",):
        ok("server.%s is the shared helper" % name, callable(getattr(server, name, None)))
    if callable(getattr(server, "session_hidden", None)):
        ok("session_hidden: case variants, and not ordinary ids",
           server.session_hidden("NEEDSYOU") and server.session_hidden("__Prewarm__")
           and not server.session_hidden(MIXED) and not server.session_hidden("needsyou2")
           and not server.session_hidden(None))
    c, d = req("/api/sessions?limit=all")
    rows = d if isinstance(d, list) else d.get("sessions", [])
    ok("the sidebar lists no variant of it", [r.get("id") for r in rows] == [MIXED], rows)

    print("\n=== one turn per conversation, whatever the case ===")
    job = server._new_job("chat-abc")            # a turn running in chat-abc
    try:
        c, d = req("/api/chat", {"message": "second", "session": "chat-ABC"})
        ok("POST /api/chat to chat-ABC while chat-abc runs -> 409 busy",
           c == 409 and d.get("busy") is True and d.get("busy_job") == job["id"], (c, d))
        c, d = req("/api/history?session=chat-ABC")
        ok("GET /api/history?session=chat-ABC reports chat-abc's running turn",
           d.get("inflight_job") == job["id"], d)
    finally:
        job["done"] = True
    r1 = server._chat_reserve("chat-xyz")
    r2 = server._chat_reserve("CHAT-XYZ")
    server._chat_release("chat-xyz")
    r3 = server._chat_reserve("Chat-Xyz")
    server._chat_release("Chat-Xyz")
    ok("a reservation blocks its case variant, and release frees it",
       r1[0] is True and r2[0] is False and r3[0] is True, (r1, r2, r3))

    print("\n=== an id with capitals keeps working ===")
    c, d = req("/api/history?session=" + MIXED)
    ok("its history reads", c == 200 and len(d.get("messages") or []) == 2, (c, d))
    c, d = req("/api/chat", {"message": "more", "session": MIXED})
    ok("a turn starts in it", c == 200 and d.get("job"), (c, d))
    t_end = time.time() + 5
    while time.time() < t_end and len(json.load(open(os.path.join(CH, MIXED + ".json")))["messages"]) < 4:
        time.sleep(0.02)
    ok("...and its reply is stored",
       [m["text"] for m in json.load(open(os.path.join(CH, MIXED + ".json")))["messages"]][-2:]
       == ["more", "stub reply"])
    c, d = req("/api/sessions/branch", {"session": MIXED, "at_index": 2})
    ok("it can be branched", c == 200 and d.get("ok") is True, (c, d))

    print("\n=== the window refuses a case variant too ===")
    with open(os.path.join(REPO, "index.html"), encoding="utf-8") as f:
        idx = f.read()
    uses = [idx[m.end():m.end() + 40] for m in re.finditer(r"CHAT_HIDDEN\.has\(", idx)]
    ok("every CHAT_HIDDEN check lower-cases the id",
       uses and all(re.match(r"[\w.]+\.toLowerCase\(\)\)", u) for u in uses), uses)

    print("\n=== a delete under a case variant still stops a late reply ===")
    json.dump({"title": "t", "messages": [{"role": "user", "text": "q", "ts": NOW}]},
              open(os.path.join(CH, "chat-tomb.json"), "w"))
    c, d = req("/api/sessions/delete", {"session": "CHAT-TOMB"})
    ok("the delete answers ok and removes the file",
       c == 200 and not os.path.exists(os.path.join(CH, "chat-tomb.json")), (c, d))
    late = {"id": "late", "session": "chat-tomb", "reply": "the late reply", "ok": True,
            "state": "done", "done": False, "ts": time.time()}
    server._finish_chat_job(late, "chat-tomb")
    ok("the running turn's reply (lower case) does not bring it back",
       not os.path.exists(os.path.join(CH, "chat-tomb.json")) and late.get("persisted") is False,
       (os.listdir(CH), late))
finally:
    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
