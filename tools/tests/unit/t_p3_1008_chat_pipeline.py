#!/usr/bin/env python3
"""Two P3s from the 2026-10-08 audit's chat-pipeline / frontend prose.

1. "a definitive prompt.submit RPC error is treated as 'already submitted',
   hiding the real error": serve ANSWERED the prompt.submit with a JSON-RPC
   error (e.g. "subagent still running — wait for it to finish"), nothing
   ran, yet run_turn raised the same WSError a dropped socket raises, and the
   chat fallback told the owner the connection dropped after submission.
   Driven through the REAL ServeSession.call over a scripted WebSocket.

2. "forced scroll-to-bottom during streaming": every poll yanked #msgs to the
   bottom, so the owner could not scroll up to read while a reply streamed.
   The REAL streamJob, in node.
"""
import json
import os
import sys
import tempfile
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
os.environ["HOME"] = tempfile.mkdtemp(prefix="hermes-p3chat-")
sys.path.insert(0, os.path.join(REPO, "dashboard"))
sys.path.insert(0, _HERE)

import hermes_rpc as rpc  # noqa: E402
import _chat_ui as ui  # noqa: E402

REAL_SERVE = rpc.ServeSession          # the class; tests swap in factories

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


class ScriptedWS(object):
    """Answers each JSON-RPC request the way serve would, by method."""

    def __init__(self, answers):
        self.answers = answers
        self.out = []
        self.sent = []

    def send_text(self, text):
        req = json.loads(text)
        self.sent.append(req["method"])
        a = self.answers.get(req["method"], {"result": {}})
        resp = {"jsonrpc": "2.0", "id": req["id"]}
        resp.update(a)
        self.out.append(json.dumps(resp))

    def recv_text(self, timeout):
        return self.out.pop(0) if self.out else None

    def close(self):
        pass


def serve_with(answers):
    s = object.__new__(REAL_SERVE)
    s.ws = ScriptedWS(answers)
    s._next_id = 1
    s._events = []
    return s


def test_refused_submit():
    srv = serve_with({
        "session.create": {"result": {"session_id": "s1", "stored_session_id": "k1"}},
        "prompt.submit": {"error": {"code": 4009,
                                    "message": "subagent still running — wait for it to finish"}},
    })
    rpc.ServeSession = lambda: srv
    job = {"id": "j", "state": "running", "text": "", "status": "",
           "approval": None, "reply": "", "ok": False, "done": False}
    raised = None
    try:
        rpc.run_turn(job, {"title": "t", "messages": []}, "hi", lambda: None)
    except Exception as e:
        raised = e
    check("THE FIX: a refused prompt.submit ends the turn instead of raising",
          raised is None, repr(raised))
    check("...with serve's own reason as the reply",
          "subagent still running" in job.get("reply", "")
          and job.get("ok") is False and job.get("state") == "done", job)
    check("...and it is NOT marked as accepted by serve",
          not job.get("submitted"), job)

    # a timeout is still the A04 case (may have been acted on): it raises
    srv = serve_with({"session.create": {"result": {"session_id": "s1"}}})
    srv.ws.answers["prompt.submit"] = None
    orig = srv.ws.send_text

    def drop_submit(text, _o=orig):
        if json.loads(text)["method"] == "prompt.submit":
            return
        _o(text)
    srv.ws.send_text = drop_submit
    rpc.ServeSession = lambda: srv
    job = {"id": "j", "state": "running", "text": "", "status": "",
           "approval": None, "reply": "", "ok": False, "done": False}
    import time as _t
    real_time = rpc.time.time
    clock = [real_time()]
    rpc.time.time = lambda: clock.__setitem__(0, clock[0] + 5) or clock[0]
    try:
        try:
            rpc.run_turn(job, {"title": "t", "messages": []}, "hi", lambda: None)
            raised = None
        except rpc.WSError as e:
            raised = e
    finally:
        rpc.time.time = real_time
    check("an unanswered prompt.submit still raises (the A04 fallback decides)",
          isinstance(raised, rpc.WSError) and not isinstance(raised, rpc.RPCError)
          and job.get("submit_sent") is True, (repr(raised), job))


SCROLL_JS = r"""
%(dom)s
function addBubble(text,who,isMd){ const b=document.createElement('div'); b.className='bubble '+who;
  if(isMd){ const m=document.createElement('div'); m.className='md'; b.appendChild(m); } $('msgs').appendChild(b); return b; }
globalThis.chatAnnounce=()=>{};
let session='chat-a', histLoading=0; const chatInflight=new Set(), chatStreams=new Set();
// content height grows with what has been rendered — read BEFORE a render it
// is the previous poll's height, the way a real layout behaves
const m=$('msgs'); m.clientHeight=400;
Object.defineProperty(m,'scrollHeight',{configurable:true,get:()=>{const md=m.querySelector('.md');return 1000+(md?md.innerHTML.length:0)*20;}});
%(fn)s
const STEPS=[
  {pre:()=>{m.scrollTop=m.scrollHeight-400;}, resp:{state:'running',done:false,text:'one',status:''}},
  {resp:{state:'running',done:false,text:'one two',status:''}},
  {pre:()=>{m.scrollTop=300;}, resp:{state:'running',done:false,text:'one two three',status:''}},
  {resp:{state:'done',done:true,text:'x',reply:'one two three four',err:false}},
];
const SEEN=[];
globalThis.fetch=async()=>{ const st=STEPS.shift(); SEEN.push(m.scrollTop); st.pre&&st.pre(); return {json:async()=>st.resp}; };
(async()=>{ const t=addBubble('','bot',false); await streamJob('J',t,'chat-a'); SEEN.push(m.scrollTop);
  console.log(JSON.stringify({seen:SEEN})); })().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


def test_scroll():
    fn = ui.extract("streamJob")
    rc, d, out = ui.run_node(SCROLL_JS % {"dom": ui.FAKE_DOM, "fn": fn})
    seen = (d or {}).get("seen") or []
    check("streamJob ran in node", rc == 0 and len(seen) == 5, out[-1200:])
    if len(seen) == 5:
        check("at the bottom, the stream is followed",
              seen[1] > 1000 and seen[2] > seen[1], seen)
        check("THE FIX: scrolled up to read, the stream does not yank the view",
              seen[3] == 300 and seen[4] == 300, seen)


def main():
    for t in (test_refused_submit, test_scroll):
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
