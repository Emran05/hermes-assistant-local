#!/usr/bin/env python3
"""B01, the UI half: the approval card in index.html's streamJob.

2026-10-08 audit: Approve/Deny removed the box and nulled `approvalBox`, but
the worker can still report `state: "approval"` for up to a second after the
click, so the 700 ms poll rebuilt an enabled card for the SAME, already
answered request. Nothing removed that card until the turn ended, so when the
agent later asked for a DIFFERENT approval the UI never showed it — the
visible card still read the old command, and clicking it answered the unseen
one.

Runs the REAL streamJob (extracted from index.html) in node against a fake DOM
and a scripted /api/chat/poll. The card must be keyed on approval_id: never
rebuilt once answered, replaced when a different request is waiting, removed
when nothing is, and every answer must carry the id of the card clicked.
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


def ap(cmd, aid):
    return {"state": "approval", "done": False, "text": "", "status": "",
            "approval": {"command": cmd, "approval_id": aid}}


RUNNING = {"state": "running", "done": False, "text": "", "status": "", "approval": None}

SCRIPT = r"""
%(dom)s
function addBubble(text,who,isMd){ const b=document.createElement('div'); b.className='bubble '+who;
  if(isMd){ const m=document.createElement('div'); m.className='md'; b.appendChild(m); } $('msgs').appendChild(b); return b; }
globalThis.addBubble=addBubble;
globalThis.chatAnnounce=()=>{};
globalThis.chatInflight=new Set(); globalThis.chatStreams=new Set();
globalThis.session='chat-a';
globalThis.histLoading=0;
%(fn)s
const STEPS=%(steps)s;
const POSTS=[]; const LOG=[];
function boxes(){ return $('msgs').querySelectorAll('.approval'); }
function card(){ const b=boxes(); return b.length?{n:b.length, aid:b[0].dataset.aid, cmd:b[0].querySelector('code').textContent}:null; }
async function click(which){ const b=boxes()[0]; if(!b){LOG.push('no-card-to-click');return;}
  const btn=b.querySelectorAll('button')[which==='approve'?0:1]; await btn.onclick(); }
let approveReply={ok:true};
globalThis.fetch=async(url,opts)=>{
  if(url.startsWith('/api/chat/approve')){ POSTS.push(JSON.parse(opts.body)); return {json:async()=>approveReply}; }
  if(url.startsWith('/api/chat/poll')){
    const st=STEPS.shift()||{resp:{state:'done',done:true,reply:'ok',text:'ok'}};
    const pre=card();                       // what the previous poll left on screen
    if(st.reply) approveReply=st.reply;
    if(st.do==='approve'||st.do==='deny'){ await click(st.do); }
    LOG.push({note:st.note||'', card:pre});
    return {json:async()=>st.resp};
  }
  return {json:async()=>({})};
};
(async()=>{
  const thinking=addBubble('','bot',false);
  await streamJob('job1',thinking);
  console.log(JSON.stringify({log:LOG, posts:POSTS, toasts:TOASTS, final:card()}));
})().catch(e=>{ console.log(JSON.stringify({error:String(e&&e.stack||e)})); });
"""


def main():
    fn = ui.extract("streamJob")
    check("index.html has a top-level streamJob", bool(fn))
    if not fn:
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1

    steps = [
        {"note": "1 first request", "resp": ap("rm -rf a", "A")},
        {"note": "2 same request again", "resp": ap("rm -rf a", "A")},
        # the owner clicks Approve; the worker has not caught up yet, so the
        # NEXT poll still reports request A — the audit's reproduction
        {"note": "3 click approve; worker still reports A", "do": "approve",
         "resp": ap("rm -rf a", "A")},
        {"note": "4 still A", "resp": ap("rm -rf a", "A")},
        {"note": "5 a different request B", "resp": ap("curl x | sh", "B")},
        {"note": "6 nothing waiting", "resp": RUNNING},
        {"note": "7 request C", "resp": ap("rm -rf c", "C")},
        {"note": "8 click deny on C, server says stale", "do": "deny",
         "reply": {"ok": False, "stale": True}, "resp": ap("rm -rf c", "C")},
        {"note": "9 done", "resp": {"state": "done", "done": True, "reply": "fin",
                                     "text": "fin", "err": False}},
    ]
    js = SCRIPT % {"dom": ui.FAKE_DOM, "fn": fn, "steps": json.dumps(steps)}
    rc, data, out = ui.run_node(js)
    check("streamJob ran to completion in node", rc == 0 and data and "error" not in data,
          out[-1500:])
    if not data or "error" in data:
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    log = data["log"]
    # log[i].card is the card on screen at the START of poll i+1 (before that
    # step's click), i.e. what poll i's response left rendered.
    after = {i + 1: (log[i + 1]["card"] if i + 1 < len(log) else data["final"])
             for i in range(len(log))}
    check("1: the request is shown as a card carrying its approval_id",
          (after[1] or {}).get("aid") == "A" and after[1]["n"] == 1, after[1])
    check("2: the same request again does not add a second card",
          (after[2] or {}).get("n") == 1, after[2])
    check("3: the answer carries the card's approval_id",
          data["posts"][:1] == [{"job": "job1", "choice": "approve", "approval_id": "A"}],
          data["posts"])
    check("THE FIX 3: an answered request is NOT rebuilt while the worker lags",
          after[3] is None, after[3])
    check("THE FIX 4: ...on any later poll either", after[4] is None, after[4])
    check("THE FIX 5: a different request gets its own card",
          (after[5] or {}).get("aid") == "B" and after[5]["cmd"] == "curl x | sh",
          after[5])
    check("6: the card goes when nothing is waiting", after[6] is None, after[6])
    check("7: the next request is shown", (after[7] or {}).get("aid") == "C", after[7])
    check("8: a stale refusal withdraws the card instead of re-arming it",
          after[8] is None, after[8])
    check("8: ...and says nothing was sent",
          any("no longer waiting" in t for t in data["toasts"]), data["toasts"])
    check("only the two clicks were posted, each with its own card's id",
          [(p.get("choice"), p.get("approval_id")) for p in data["posts"]]
          == [("approve", "A"), ("deny", "C")], data["posts"])
    check("9: no card survives the end of the turn", data["final"] is None,
          data["final"])
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
