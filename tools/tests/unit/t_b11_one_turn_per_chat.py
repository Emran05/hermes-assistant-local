#!/usr/bin/env python3
"""One turn per conversation (2026-10-08 B11, 2026-10-21 C113).

B11: Enter called sendMsg() straight past the disabled Send button, and the
server had no per-conversation in-flight check, so two turns could run on one
chat. C113 is what that did upstream: serve does not refuse a prompt.submit on
a busy session — it interrupts the live turn, queues the new text, answers
{"status": "queued"} and routes every later event for the session to the NEW
socket. Job 2 took turn 1's interrupted message.complete as its own reply; job
1 heard nothing until its 600 s timeout.

Real code, offline:
  * server.py's Handler.do_POST /api/chat (ast-extracted with _new_job, the
    reservation helpers and SESSION_RE; the worker is a stub that never ends)
  * hermes_rpc.run_turn against a fake serve that answers "queued"
  * index.html's sendMsg + the Enter handler, in node
"""
import ast
import collections
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
os.environ["HOME"] = tempfile.mkdtemp(prefix="hermes-b11-")
sys.path.insert(0, DASH)
sys.path.insert(0, _HERE)

import _chat_ui as ui  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


# --------------------------------------------------------------------------
# 1. the /api/chat route
# --------------------------------------------------------------------------
def route_namespace():
    src = open(os.path.join(DASH, "server.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    want_fn = {"_new_job", "_chat_inflight", "_chat_reserve", "_chat_release",
               "Handler",
               # E69: case-insensitive ids; E18: the lost-reply notice
               "session_hidden", "session_key", "_clear_awaiting_reply", "_lost_reply_due",
               "_lost_reply_row", "_new_message_id",
               # do_POST reads + validates the JSON body once, first (B12)
               "_hdr", "_content_length", "_parse_json_body"}
    want_var = {"CHAT_JOBS", "_jobs_lock", "_chat_reserve_lock",
                "_chat_reserving", "CHAT_BUSY_REPLY", "SESSION_RE",
                "MAX_JSON_BODY",
                # /api/chat refuses the reserved prewarm/draft sessions (C38)
                "PREWARM_SESSION", "DRAFT_SESSION", "HIDDEN_SESSIONS",
                "_CHAT_PROC_START", "LOST_REPLY_NOTICE"}
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in want_fn:
            body.append(node)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id in want_var):
            body.append(node)
    chats = {}
    writes = []

    def save_chat_update(session, fn):
        time.sleep(0.05)          # widen the check-then-act window
        chat = chats.setdefault(session, {"messages": []})
        fn(chat)
        writes.append(session)

    class Base(object):
        pass

    ns = {"re": re, "json": json, "sys": sys, "time": time, "threading": threading, "uuid": uuid,
          "urllib": urllib, "BaseHTTPRequestHandler": Base,
          "POST_ROUTES": {}, "agent_paused": lambda: False,
          "mlx_admission": lambda: (True, 0.0, 50.0),
          "save_chat_update": save_chat_update,
          "access_preamble": lambda m: "",
          "note_user_activity": lambda: None,
          "_chat_worker": lambda job, session, prompt: None}
    exec(compile(ast.Module(body=body, type_ignores=[]), "server.py", "exec"), ns)
    for n in want_fn | want_var:
        if n not in ns:
            raise AssertionError("server.py has no %s" % n)
    return ns, chats, writes


def post(ns, body):
    out = {}

    class H(ns["Handler"]):
        def _guard(self):
            return True

        def _json(self, obj, status=200):
            out["body"], out["status"] = obj, status

    raw = json.dumps(body).encode()
    h = object.__new__(H)
    h.path = "/api/chat"
    h.command = "POST"
    h.headers = {"Content-Length": str(len(raw))}
    h.rfile = io.BytesIO(raw)           # the body goes through the real preamble
    h.do_POST()
    return out.get("status"), out.get("body") or {}


def test_route():
    try:
        ns, chats, writes = route_namespace()
    except Exception as e:
        check("server.py has the per-conversation reservation", False, e)
        return
    code, body = post(ns, {"message": "first", "session": "chat-a"})
    check("route: the first message starts a job", code == 200 and body.get("job"),
          (code, body))
    first = body.get("job")
    code, body = post(ns, {"message": "second", "session": "chat-a"})
    check("THE FIX: a second message while that turn runs is refused (409)",
          code == 409 and body.get("busy") is True, (code, body))
    check("...naming the running job as busy_job", body.get("busy_job") == first, body)
    check("...and NOT as `job`, which every client reads as 'your turn started'",
          "job" not in body, body)
    check("...with a reply the UI can show",
          "still answering" in (body.get("reply") or ""), body)
    check("...and the refused message is NOT written to the transcript",
          [m["text"] for m in chats["chat-a"]["messages"]] == ["first"],
          chats["chat-a"])
    check("...and no second job exists",
          sum(1 for j in ns["CHAT_JOBS"].values() if j["session"] == "chat-a") == 1,
          ns["CHAT_JOBS"])
    code, body = post(ns, {"message": "elsewhere", "session": "chat-b"})
    check("route: another conversation is not blocked", code == 200, (code, body))
    ns["CHAT_JOBS"][first]["done"] = True
    code, body = post(ns, {"message": "third", "session": "chat-a"})
    check("route: once the turn is done the conversation takes a message again",
          code == 200, (code, body))

    # the race the reservation exists for: two requests in the same instant
    ns, chats, writes = route_namespace()
    results = []
    barrier = threading.Barrier(4)

    def go(i):
        barrier.wait()
        results.append(post(ns, {"message": "m%d" % i, "session": "chat-r"})[0])

    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    check("race: four simultaneous sends -> exactly one turn",
          sorted(results) == [200, 409, 409, 409], results)
    check("race: ...and exactly one message in the transcript",
          len(chats["chat-r"]["messages"]) == 1, chats.get("chat-r"))


# --------------------------------------------------------------------------
# 2. hermes_rpc: a "queued" submit waits for its own turn (C113)
# --------------------------------------------------------------------------
import hermes_rpc as rpc  # noqa: E402


class FakeServe(object):
    def __init__(self, submit_status, events):
        self.submit_status = submit_status
        self.events = collections.deque(events)
        self.calls = []

    def call(self, method, params, timeout=30, on_sent=None):
        self.calls.append(method)
        if on_sent:
            on_sent()
        if method == "session.create":
            return {"session_id": "s1", "stored_session_id": "k1"}
        if method == "prompt.submit":
            return {"status": self.submit_status}
        return {}

    def next_event(self, timeout):
        if self.events:
            return self.events.popleft()
        time.sleep(0.01)
        return None

    def close(self):
        pass


def ev(t, **payload):
    return {"type": t, "session_id": "s1", "payload": payload}


def drive(fake, timeout=3):
    rpc.ServeSession = lambda: fake
    saved = rpc.TURN_TIMEOUT
    rpc.TURN_TIMEOUT = timeout
    job = {"id": "j", "state": "running", "text": "", "status": "",
           "approval": None, "reply": "", "ok": False, "done": False}
    try:
        rpc.run_turn(job, {"title": "t", "messages": []}, "second message",
                     lambda: None)
    finally:
        rpc.TURN_TIMEOUT = saved
    return job


def test_queued_submit():
    # the audit's reproduction: the live turn's tail, then ours
    fake = FakeServe("queued", [
        ev("message.delta", text="half of the OLD answ"),
        ev("message.complete", text="half of the OLD answ", status="interrupted"),
        ev("message.start"),
        ev("message.delta", text="the new "),
        ev("message.delta", text="answer"),
        ev("message.complete", text="the new answer", status="complete"),
    ])
    job = drive(fake)
    check("THE FIX (queued): the reply is THIS message's answer",
          job["reply"] == "the new answer" and job["ok"] is True, job)
    check("THE FIX (queued): the earlier turn's text never shows as ours",
          "OLD" not in job["text"] and "OLD" not in job["reply"], job)

    # the earlier turn's complete went to ITS socket before we were bound:
    # skipping "one complete" would skip ours and hang; waiting for our
    # message.start does not
    fake = FakeServe("queued", [
        ev("message.start"),
        ev("message.delta", text="ours"),
        ev("message.complete", text="ours", status="complete"),
    ])
    job = drive(fake)
    check("queued with no earlier tail on this socket: still answered at once",
          job["reply"] == "ours" and job["ok"] is True, job)

    fake = FakeServe("steered", [
        ev("message.delta", text="folded in"),
        ev("message.complete", text="folded in", status="complete"),
    ])
    job = drive(fake)
    check("steered: the live turn's complete IS the answer",
          job["reply"] == "folded in", job)

    fake = FakeServe("streaming", [
        ev("message.start"),
        ev("message.complete", text="plain", status="complete"),
    ])
    job = drive(fake)
    check("an ordinary submit is unchanged", job["reply"] == "plain", job)


# --------------------------------------------------------------------------
# 3. the UI: Enter honours the Send button; one send per conversation
# --------------------------------------------------------------------------
ENTER_JS = r"""
%(dom)s
const handlers={}; $('input').addEventListener=(t,fn)=>{handlers[t]=fn;};
let sent=0; globalThis.sendMsg=()=>{sent++;};
%(line)s
function key(o){ let prevented=false; handlers.keydown(Object.assign({key:'Enter',shiftKey:false,isComposing:false,keyCode:13,preventDefault(){prevented=true;}},o)); return prevented; }
const out={};
$('send').disabled=true; key({}); out.disabled=sent;
$('send').disabled=false; key({isComposing:true}); out.composing=sent;
key({keyCode:229}); out.ime229=sent;
key({shiftKey:true}); out.shift=sent;
key({}); out.enabled=sent;
console.log(JSON.stringify(out));
"""

SEND_JS = r"""
%(dom)s
function addBubble(text,who,isMd){ const b=document.createElement('div'); b.className='bubble '+who; b._text=String(text||''); $('msgs').appendChild(b); return b; }
globalThis.attachments=[]; let attachments=globalThis.attachments;
function renderAttach(){}
function stampBubbles(){} function loadSessions(){} function renderHub(){} function renderChatSide(){}
const RELOADS=[]; async function loadHistory(hint){ RELOADS.push(hint||null); }
let session='chat-a';
let release=null; const POSTS=[];
async function streamJob(jid,thinking){ await new Promise(r=>{release=r;}); }
let busyReply=null;
globalThis.fetch=async(url,opts)=>{
  if(url==='/api/chat'){ POSTS.push(JSON.parse(opts.body));
    if(busyReply) return {json:async()=>busyReply};
    return {json:async()=>({ok:true,job:'J'+POSTS.length})}; }
  return {json:async()=>({messages:[]})};
};
$('input').focus=()=>{};
%(inflight)s
%(fn)s
(async()=>{
  const out={};
  $('input').value='hello';
  const p1=sendMsg();
  await new Promise(r=>setImmediate(r));
  $('input').value='again';
  await sendMsg();
  out.postsWhileRunning=POSTS.length;
  out.toastWhileRunning=TOASTS.slice();
  out.inputKept=$('input').value;
  release(); await p1;
  out.postsAfter=POSTS.length;
  // a 409 from the server: the message is taken back, not left as sent
  busyReply={ok:false,busy:true,busy_job:'X',reply:'Hermes is still answering your previous message in this conversation.'};
  const before=$('msgs').children.length;
  $('input').value='refused one';
  await sendMsg();
  out.bubblesDelta=$('msgs').children.length-before;
  out.inputRestored=$('input').value;
  out.toasts=TOASTS.slice();
  out.sendEnabled=!$('send').disabled;
  out.reloads=RELOADS.slice();
  console.log(JSON.stringify(out));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


def test_ui():
    line = ui.extract_line("$('input').addEventListener('keydown'")
    check("index.html has the composer's keydown handler", bool(line))
    if line:
        rc, d, out = ui.run_node(ENTER_JS % {"dom": ui.FAKE_DOM, "line": line})
        d = d or {}
        check("THE FIX: Enter does nothing while Send is disabled",
              d.get("disabled") == 0, out[-800:])
        check("Enter during IME composition does not send (isComposing)",
              d.get("composing") == 0, d)
        check("Enter during IME composition does not send (keyCode 229)",
              d.get("ime229") == 0, d)
        check("Shift+Enter does not send", d.get("shift") == 0, d)
        check("Enter sends when Send is enabled", d.get("enabled") == 1, d)

    fn = ui.extract("sendMsg")
    inflight = ui.extract_line("const chatInflight")
    check("index.html tracks conversations with a turn in flight",
          bool(fn) and bool(inflight))
    if fn and inflight:
        rc, d, out = ui.run_node(SEND_JS % {"dom": ui.FAKE_DOM, "fn": fn,
                                            "inflight": inflight})
        d = d or {}
        check("sendMsg ran in node", rc == 0 and "error" not in d, out[-1500:])
        check("THE FIX: a second send in the same conversation is not posted",
              d.get("postsWhileRunning") == 1, d)
        check("...the owner is told why",
              any("still answering" in t for t in d.get("toastWhileRunning", [])), d)
        check("...and the typed text is kept", d.get("inputKept") == "again", d)
        check("a 409 busy takes the message bubble back",
              d.get("bubblesDelta") == 0, d)
        check("...puts the text back in the composer",
              d.get("inputRestored") == "refused one", d)
        check("...and shows the server's reason",
              any("previous message" in t for t in d.get("toasts", [])), d)
        check("Send is usable again afterwards", d.get("sendEnabled") is True, d)
        # round-2 X-approval-flow-1: the refused send reloads the conversation
        # with the running turn's id, so this window attaches to it
        check("...and the conversation is reloaded to attach to the busy turn",
              d.get("reloads") == ["X"], d)


def main():
    for t in (test_route, test_queued_submit, test_ui):
        print("-- %s" % t.__name__)
        try:
            t()
        except Exception:
            FAILS.append(t.__name__ + " raised")
            print("  FAIL %s raised:\n%s" % (t.__name__, traceback.format_exc()))
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
