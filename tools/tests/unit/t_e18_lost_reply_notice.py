#!/usr/bin/env python3
"""E18: a reply lost to a dashboard restart leaves a stored notice.

2026-11-09 audit: chat jobs live only in memory. When the dashboard restarts
during a turn, the user message is already in chats/<id>.json but the reply
never is — serve may still finish the turn (and the agent remembers it and any
tool it ran), while on reload the transcript just ends at the question, with
nothing saying an answer is missing. The bounded fix: when such a conversation
is read back (GET /api/history) the notice is SHOWN as a virtual row (a GET
writes nothing: cross-site navigations and MCP chat_get reach it); when a new
turn starts in it (POST /api/chat) one notice row is stored after that
message, once. The evidence is the `awaiting_reply` marker /api/chat stores
on the user message and _finish_chat_job clears with the reply — a message
without it (older data, a refused or legacy write) never gets a notice.
`chat_jobs_active()` is exported for the update card (cluster c12).

Real server.py import (aux chain included) + the real Handler on a free
loopback port, throwaway HOME. The turn worker is replaced by a stub, so no
model, no serve, no network.

Checks:
  * a transcript ending in a marked user message from BEFORE this process
    started reads back with one virtual notice on GET /api/history, and the
    file is never written by a GET (any headers);
  * a transcript ending in an UNMARKED user message (stored before the
    marker existed) gets nothing;
  * the notice is a status row (`kind`, `err`): chat search does not find its
    words, hermes_rpc's model seed and the branch seed skip it, MCP chat_get's
    filter skips it;
  * no notice for: a user message written by THIS process (a turn just
    started), a conversation with a turn still running, a branch cut at a
    user message with no turn of its own yet, the hidden drafts conversation,
    a conversation ending in a reply;
  * POST /api/chat on a conversation that ends in a lost message stores the
    notice BEFORE the new message, in the same write; the new message is
    marked while its turn runs and the reply clears every marker;
  * chat_jobs_active() is False with no job, True while one is not done.
"""
import json
import os
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
HOME = tempfile.mkdtemp(prefix="hermes-e18-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
CH = os.path.join(DASH, "chats")
os.makedirs(CH, exist_ok=True)
os.makedirs(os.path.join(HOME, ".hermes", "memories", "people"), exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
os.environ["HOME"] = HOME
os.environ["DASH_PORT"] = str(PORT)
sys.path.insert(0, REPO)
import server                                    # noqa: E402
assert server.DATA == DASH, server.DATA
server.SB_BIN = os.path.join(HOME, "no-shortcuts-cli")   # the preamble lists no real Shortcuts
server._ix_cal_lines = lambda: []                # never shell out to icalBuddy

# a turn never runs: the worker stub finishes the job at once with a reply
WORKER_CALLS = []


def _stub_worker(job, session, prompt):
    WORKER_CALLS.append(session)
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


def req(path, body=None, headers=None):
    h = dict({"Host": "127.0.0.1:%d" % PORT}, **(headers or {}))
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


START = getattr(server, "_CHAT_PROC_START", time.time())
OLD = START - 300          # written by the process before the restart


def put(sid, msgs, **extra):
    d = dict({"title": sid, "messages": msgs}, **extra)
    with open(os.path.join(CH, sid + ".json"), "w") as f:
        json.dump(d, f)


def disk(sid):
    with open(os.path.join(CH, sid + ".json")) as f:
        return json.load(f)


def notices(msgs):
    return [m for m in msgs if isinstance(m, dict) and m.get("kind") == "lost_reply"]


Q = "please clean the quokka folder"
AW = {"awaiting_reply": True}      # what /api/chat stores until the reply lands
put("chat-lost", [{"role": "user", "text": "hello", "ts": OLD - 60},
                  {"role": "bot", "text": "hi", "ts": OLD - 50},
                  dict({"role": "user", "text": Q, "ts": OLD}, **AW)])
put("chat-legacy", [{"role": "user", "text": "q", "ts": OLD - 9},
                    {"role": "bot", "text": "a", "ts": OLD - 8},
                    {"role": "user", "text": "an old unanswered question", "ts": OLD}])
put("chat-answered", [{"role": "user", "text": "q", "ts": OLD},
                      {"role": "bot", "text": "a", "ts": OLD + 1}])
put("chat-fresh", [dict({"role": "user", "text": "just sent", "ts": time.time() + 1}, **AW)])
put("chat-running", [dict({"role": "user", "text": "working on it", "ts": OLD}, **AW)])
put("chat-branch", [{"role": "user", "text": "q1", "ts": OLD - 10},
                    {"role": "bot", "text": "a1", "ts": OLD - 9},
                    dict({"role": "user", "text": "q2", "ts": OLD}, **AW)],
    forked_from={"session": "chat-answered", "at_index": 3, "ts": OLD})
put("chat-nots", [dict({"role": "user", "text": "no timestamp"}, **AW)])
put("needsyou", [dict({"role": "user", "text": "Draft a reply", "ts": OLD}, **AW)])
put("chat-send", [dict({"role": "user", "text": "lost question", "ts": OLD}, **AW)])
put("chat-legacy-send", [{"role": "user", "text": "old question", "ts": OLD}])

try:
    print("\n=== GET /api/history SHOWS a notice after a lost message, writes nothing ===")
    raw_lost = open(os.path.join(CH, "chat-lost.json"), "rb").read()
    c, d = req("/api/history?session=chat-lost")
    msgs = d.get("messages") if isinstance(d, dict) else []
    ok("the read answers 200", c == 200, (c, d))
    ok("the transcript reads back with a notice after the unanswered message",
       len(msgs) == 4 and msgs[2].get("text") == Q and notices(msgs[3:]) == [msgs[3]], msgs)
    n = msgs[3] if len(msgs) == 4 else {}
    ok("the notice is a status row: kind + err, never the agent's words",
       n.get("kind") == "lost_reply" and n.get("err") is True and n.get("role") == "bot", n)
    ok("it says the reply was lost and to check before repeating an action",
       "restart" in (n.get("text") or "") and "before repeating an action" in (n.get("text") or ""),
       n.get("text"))
    ok("it is marked virtual (no id to branch from)",
       n.get("virtual") is True and "id" not in n, n)
    ok("the GET wrote nothing", open(os.path.join(CH, "chat-lost.json"), "rb").read() == raw_lost)
    c, d = req("/api/history?session=chat-lost",
               headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})
    ok("a cross-site navigation to it (allowed by the guard) writes nothing either",
       open(os.path.join(CH, "chat-lost.json"), "rb").read() == raw_lost
       and len(d.get("messages", [])) == 4, d)
    raw_legacy = open(os.path.join(CH, "chat-legacy.json"), "rb").read()
    c, d = req("/api/history?session=chat-legacy")
    ok("a conversation ending in an UNMARKED old message gets no notice (no evidence)",
       c == 200 and not notices(d.get("messages", []))
       and open(os.path.join(CH, "chat-legacy.json"), "rb").read() == raw_legacy, d)

    print("\n=== the notice stays out of search, seeds and MCP ===")
    import hermes_rpc                              # noqa: E402
    seed = hermes_rpc._transcript_seed(dict(disk("chat-lost"), messages=msgs))
    ok("hermes_rpc's model seed keeps the lost question and skips the notice",
       [m["content"] for m in seed] == ["hello", "hi", Q], seed)
    bs = server.br_seed_history(msgs)
    ok("the branch seed skips it too", all("restart" not in m["content"] for m in bs), bs)
    with open(os.path.join(REPO, "hermes_mcp.py"), encoding="utf-8") as f:
        mcp_src = f.read()
    ok("MCP chat_get drops rows with a `kind` key (the notice's marker)",
       '_CV_META_KEYS = ("tool", "tool_name", "approval", "status", "kind")' in mcp_src)

    print("\n=== no notice where no reply was lost ===")
    for sid, why in (("chat-answered", "a conversation ending in a reply"),
                     ("chat-fresh", "a message this process wrote (its turn is starting)"),
                     ("chat-branch", "a branch cut at a question, no turn of its own yet"),
                     ("chat-nots", "a message with no timestamp (age unknown)")):
        before = disk(sid)
        c, d = req("/api/history?session=" + sid)
        ok("%s: untouched" % why, c == 200 and disk(sid) == before, disk(sid))
    raw = open(os.path.join(CH, "needsyou.json"), "rb").read()
    req("/api/history?session=needsyou")
    ok("the hidden drafts conversation is never written",
       open(os.path.join(CH, "needsyou.json"), "rb").read() == raw)
    ok("chat_jobs_active() is exported and False with no job",
       callable(getattr(server, "chat_jobs_active", None)) and server.chat_jobs_active() is False)
    job = server._new_job("chat-running")
    try:
        ok("chat_jobs_active() is True while a job is not done",
           server.chat_jobs_active() is True)
        before = disk("chat-running")
        c, d = req("/api/history?session=chat-running")
        ok("a conversation with a turn still running: untouched",
           c == 200 and disk("chat-running") == before and d.get("inflight_job") == job["id"],
           (disk("chat-running"), d))
    finally:
        job["done"] = True
    ok("chat_jobs_active() is False again once it is done", server.chat_jobs_active() is False)

    print("\n=== POST /api/chat notes the lost message before the new one ===")
    c, d = req("/api/chat", {"message": "and now this", "session": "chat-send"})
    ok("the turn starts", c == 200 and d.get("job"), (c, d))
    t_end = time.time() + 5
    while time.time() < t_end and len(disk("chat-send")["messages"]) < 4:
        time.sleep(0.02)
    m = disk("chat-send")["messages"]
    ok("order: lost question, notice, new question, its reply",
       [x.get("kind") or x.get("role") for x in m] == ["user", "lost_reply", "user", "bot"]
       and m[2].get("text") == "and now this", m)
    ok("the stored notice has an id and a timestamp, and is not virtual",
       len(m) == 4 and m[1].get("id") and m[1].get("ts") and "virtual" not in m[1], m)
    ok("the reply cleared every awaiting_reply marker",
       not any("awaiting_reply" in x for x in m), m)
    c, d = req("/api/history?session=chat-send")
    ok("...and a read afterwards adds no second notice",
       len(notices(disk("chat-send")["messages"])) == 1
       and len(notices(d.get("messages", []))) == 1, disk("chat-send")["messages"])
    c, d = req("/api/sessions/search?q=restarted")
    ok("chat search does not match the stored notice's words",
       c == 200 and "chat-send" not in json.dumps(d), d)
    c, d = req("/api/chat", {"message": "next", "session": "chat-legacy-send"})
    t_end = time.time() + 5
    while time.time() < t_end and len(disk("chat-legacy-send")["messages"]) < 3:
        time.sleep(0.02)
    ok("an UNMARKED old last message gets no stored notice on the next send",
       not notices(disk("chat-legacy-send")["messages"])
       and len(disk("chat-legacy-send")["messages"]) == 3, disk("chat-legacy-send"))

    print("\n=== a marked message stays marked while its turn runs ===")
    held = threading.Event()

    def _slow_worker(job, session, prompt):
        held.wait(5)
        job.update(reply="late reply", ok=True, state="done")
        server._finish_chat_job(job, session)
    server._chat_worker = _slow_worker
    c, d = req("/api/chat", {"message": "slow one", "session": "chat-answered"})
    last = disk("chat-answered")["messages"][-1]
    ok("the new user message carries awaiting_reply while its turn runs",
       last.get("text") == "slow one" and last.get("awaiting_reply") is True, last)
    held.set()
    t_end = time.time() + 5
    while time.time() < t_end and disk("chat-answered")["messages"][-1].get("role") != "bot":
        time.sleep(0.02)
    ok("...and the stored reply clears it",
       not any("awaiting_reply" in x for x in disk("chat-answered")["messages"]),
       disk("chat-answered")["messages"])
finally:
    srv.shutdown()
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
