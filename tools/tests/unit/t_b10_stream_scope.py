#!/usr/bin/env python3
"""B10 (2026-10-08 audit): a streaming reply, or its approval card, rendered
into whichever conversation was on screen.

streamJob closed over DOM nodes and appended to #msgs on every poll, with no
idea which conversation it belonged to: switch conversations mid-turn and the
rest of the reply — and any approval card — landed in the other chat.

Runs the REAL streamJob (and sendMsg's hand-off of the conversation id) from
index.html in node against a fake DOM, switching conversations between polls
the way the sidebar does (session = …; loadHistory clears #msgs and redraws).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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


SCRIPT = r"""
%(dom)s
function addBubble(text,who,isMd){ const b=document.createElement('div'); b.className='bubble '+who;
  if(isMd){ const m=document.createElement('div'); m.className='md'; b.appendChild(m); } else b._text=String(text||'');
  $('msgs').appendChild(b); return b; }
globalThis.chatAnnounce=()=>{};
let session='chat-a';
let histLoading=0;
const RELOADS=[];
async function loadHistory(){ RELOADS.push(session); $('msgs').innerHTML=''; addBubble('A history (reloaded)','user',false); }
const chatInflight=new Set(['chat-a']), chatStreams=new Set();
%(fn)s
const STEPS=%(steps)s;
const LOG=[];
function view(){ return $('msgs').children.map(b=>{
  const md=b.querySelector('.md'); const ap=b.querySelector('.approval');
  return (b.className.includes('user')?'U:':'B:')+(md?md.innerHTML:b._text).replace(/ <span class="caret">▌<\/span>/,'')+(ap?' [card '+ap.dataset.aid+']':''); }); }
function switchTo(s,label){ session=s; $('msgs').innerHTML=''; addBubble(label,'user',false); }
globalThis.fetch=async(url,opts)=>{
  if(url.startsWith('/api/chat/poll')){
    const st=STEPS.shift();
    // what the PREVIOUS poll left on screen, before this step's switch
    LOG.push({note:st.note, view:view(), send:$('send').disabled, toasts:TOASTS.length});
    if(st.switchTo) switchTo(st.switchTo, st.label);
    histLoading=st.loading?1:0;
    if(st.loading&&st.resp.done) setImmediate(()=>{histLoading=0;});   // that reload finishes
    return {json:async()=>st.resp};
  }
  return {json:async()=>({})};
};
(async()=>{
  addBubble('my question','user',false);
  const thinking=addBubble('','bot',false);
  await streamJob('J',thinking,'chat-a');
  LOG.push({note:'end', view:view(), send:$('send').disabled, toasts:TOASTS.length});
  console.log(JSON.stringify({log:LOG, toasts:TOASTS, reloads:RELOADS}));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


R = {"state": "running", "done": False, "status": "", "approval": None}


def run(fn, steps=None):
    steps = steps or [
        {"note": "1", "resp": dict(R, text="Hel")},
        {"note": "2 switched to B", "switchTo": "chat-b", "label": "B history",
         "resp": dict(R, text="Hello wor")},
        {"note": "3 approval while away",
         "resp": dict(R, state="approval", text="Hello wor",
                      approval={"command": "rm -rf x", "approval_id": "X"})},
        {"note": "4 same approval",
         "resp": dict(R, state="approval", text="Hello wor",
                      approval={"command": "rm -rf x", "approval_id": "X"})},
        {"note": "5 back on A", "switchTo": "chat-a", "label": "A history",
         "resp": dict(R, state="approval", text="Hello wor",
                      approval={"command": "rm -rf x", "approval_id": "X"})},
        {"note": "6 approval cleared", "resp": dict(R, text="Hello world")},
        {"note": "7 A reloading", "loading": True, "switchTo": "chat-a",
         "label": "A history again", "resp": dict(R, text="Hello world!")},
        {"note": "8 off to B", "switchTo": "chat-b", "label": "B history",
         "resp": {"state": "done", "done": True, "text": "Hello world!",
                  "reply": "Hello world!", "err": False}},
    ]
    js = SCRIPT % {"dom": ui.FAKE_DOM, "fn": fn, "steps": json.dumps(steps)}
    return ui.run_node(js)


# The audit's reproduction: the switch happens BEFORE the first token, so the
# first text the stream receives creates its bubble in whatever is on screen.
EARLY = [
    {"note": "1 tool running, no text yet", "resp": dict(R, status="using terminal")},
    {"note": "2 switched to B", "switchTo": "chat-b", "label": "B history",
     "resp": dict(R, text="Hi there")},
    {"note": "3 approval", "resp": dict(R, state="approval", text="Hi there",
                                        approval={"command": "rm -rf y",
                                                  "approval_id": "Y"})},
    {"note": "4 back on A, done", "switchTo": "chat-a", "label": "A history",
     "resp": {"state": "done", "done": True, "text": "Hi there!",
              "reply": "Hi there!", "err": False}},
]


def main():
    fn = ui.extract("streamJob")
    check("index.html has a top-level streamJob", bool(fn))
    if not fn:
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    rc, data, out = run(fn)
    check("streamJob ran in node", rc == 0 and data and "error" not in data, out[-1500:])
    if not data or "error" in data:
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    log = data["log"]
    # log[k].view = the screen as poll k (1-based) left it: recorded at the
    # start of poll k+1, before that step switches conversation.
    v = {k: log[k]["view"] for k in range(1, len(log))}
    check("1: the reply streams into its own conversation",
          v[1] == ["U:my question", "B:Hel"], v[1])
    check("THE FIX 2: after switching to B, nothing of A's reply lands in B",
          v[2] == ["U:B history"], v[2])
    check("THE FIX 3: A's approval card is NOT drawn into B",
          v[3] == ["U:B history"] and v[4] == ["U:B history"], (v[3], v[4]))
    check("3: ...the owner is told once that another conversation is waiting",
          sum(1 for t in data["toasts"] if "another conversation is waiting" in t) == 1,
          data["toasts"])
    check("5: back on A, the reply and its card are rebuilt after A's history",
          v[5] == ["U:A history", "B:Hello wor [card X]"], v[5])
    check("6: the card goes when the approval is answered elsewhere",
          v[6] == ["U:A history", "B:Hello world"], v[6])
    check("7: nothing is drawn while A's history is reloading",
          v[7] == ["U:A history again"], v[7])
    check("THE FIX 8: the final reply is not drawn into B",
          log[-1]["view"] == ["U:B history"], log[-1]["view"])
    check("8: ...the owner is told A got its reply",
          any("replied in another conversation" in t for t in data["toasts"]),
          data["toasts"])
    sends = {e["note"]: e["send"] for e in log}
    check("Send follows the conversation on screen (free on B, busy on A)",
          sends.get("3 approval while away") is False
          and sends.get("6 approval cleared") is True, sends)

    rc, data, out = run(fn, EARLY)
    log = (data or {}).get("log") or []
    check("early switch: streamJob ran in node", rc == 0 and len(log) == 5, out[-800:])
    if len(log) == 5:
        check("THE FIX (audit repro): the first token after a switch does not "
              "open a bubble in the other conversation",
              log[2]["view"] == ["U:B history"], log[2]["view"])
        check("THE FIX (audit repro): nor does the approval card",
              log[3]["view"] == ["U:B history"], log[3]["view"])
        check("back on A, the finished reply is drawn after A's history",
              log[4]["view"] == ["U:A history", "B:Hi there!"], log[4]["view"])

    # (review) the turn ends while the owner's switch back to A is still
    # loading A's history: that reload may or may not hold the reply, so the
    # stream must neither drop it nor claim it went to another conversation
    for persisted in (True, False):
        done = {"state": "done", "done": True, "text": "Hello!", "reply": "Hello!",
                "err": False, "persisted": persisted}
        rc, data, out = run(fn, [
            {"note": "1", "resp": dict(R, text="Hel")},
            {"note": "2 B", "switchTo": "chat-b", "label": "B history",
             "resp": dict(R, text="Hello")},
            {"note": "3 back on A as the turn ends", "switchTo": "chat-a",
             "label": "A history", "loading": True, "resp": done},
        ])
        data = data or {}
        log = data.get("log") or []
        final = log[-1]["view"] if log else None
        tag = "persisted" if persisted else "NOT persisted"
        check("done mid-reload (%s): streamJob ran" % tag,
              rc == 0 and "error" not in data and len(log) == 4, out[-800:])
        check("done mid-reload (%s): no claim it replied elsewhere" % tag,
              not any("another conversation" in t for t in data.get("toasts", [])),
              data.get("toasts"))
        if persisted:
            check("THE FIX: done mid-reload — A's history is loaded again, "
                  "which holds the reply (shown once, never dropped)",
                  data.get("reloads") == ["chat-a"]
                  and final == ["U:A history (reloaded)"], (data.get("reloads"), final))
        else:
            check("done mid-reload, NOT persisted: the reply is drawn after the "
                  "reloaded history (it is not in it)",
                  final == ["U:A history (reloaded)", "B:Hello!"], final)

    send = ui.extract("sendMsg") or ""
    check("sendMsg hands its conversation to streamJob",
          "streamJob(d.job,thinking,sess)" in send, send[:200])
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
