#!/usr/bin/env python3
"""C100 (2026-10-21 audit, P3): the chat had no live region and its bubbles
no author.

A screen-reader user heard nothing when a reply finished streaming or when an
approval card appeared, and every bubble read as anonymous text. Now: each
bubble is an article named "You" / "Hermes"; one off-screen status region
announces the approval request (assertive) and the finished reply (polite);
the approval card is a named group.

Runs the REAL addBubble, chatAnnounce and streamJob from index.html in node.
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
// index.html's $ is getElementById: null for an element nobody created yet
const _auto=$;
$=(id)=>(id==='chat-live')?(DOC_ROOT._all().find(e=>e.id===id)||null):_auto(id);
function brStamp(b){return b;}
let session='chat-a'; let histLoading=0; const chatInflight=new Set(), chatStreams=new Set();
%(fns)s
const STEPS=%(steps)s; const LIVE=[];
globalThis.fetch=async(url,opts)=>{
  if(url.startsWith('/api/chat/approve')) return {json:async()=>({ok:true})};
  const st=STEPS.shift();
  const el=$('chat-live'); if(el) LIVE.push({text:el.textContent, live:el.getAttribute('aria-live'), role:el.getAttribute('role')});
  return {json:async()=>st};
};
(async()=>{
  const u=addBubble('hello','user',false);
  const t=addBubble('','bot',false);
  await streamJob('J',t,'chat-a');
  await new Promise(r=>setImmediate(r));
  const el=$('chat-live'); LIVE.push({text:el?el.textContent:null, live:el?el.getAttribute('aria-live'):null, role:el?el.getAttribute('role'):null});
  const card=$('msgs').querySelector('.approval');
  console.log(JSON.stringify({user:{role:u.getAttribute('role'),label:u.getAttribute('aria-label')},
    bot:{role:t.getAttribute('role'),label:t.getAttribute('aria-label')}, live:LIVE}));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


def main():
    names = ["addBubble", "chatAnnounce", "streamJob"]
    fns = [ui.extract(n) for n in names]
    check("index.html has addBubble, chatAnnounce and streamJob", all(fns),
          [n for n, f in zip(names, fns) if not f])
    fns = [f for f in fns if f]
    steps = [
        {"state": "approval", "done": False, "text": "", "status": "",
         "approval": {"command": "rm -rf build", "approval_id": "A1"}},
        {"state": "running", "done": False, "text": "working", "status": "", "approval": None},
        {"state": "done", "done": True, "text": "All done.", "reply": "All done.", "err": False},
    ]
    rc, d, out = ui.run_node(SCRIPT % {"dom": ui.FAKE_DOM, "fns": "\n".join(fns),
                                       "steps": json.dumps(steps)})
    check("ran in node", rc == 0 and isinstance(d, dict) and "error" not in d, out[-1500:])
    d = d or {}
    check("a user bubble is an article named 'You'",
          d.get("user") == {"role": "article", "label": "You"}, d.get("user"))
    check("a reply bubble is an article named 'Hermes'",
          d.get("bot") == {"role": "article", "label": "Hermes"}, d.get("bot"))
    live = d.get("live") or []
    check("the approval request is announced, assertively",
          any(x.get("text") == "Approval needed: rm -rf build" and x.get("live") == "assertive"
              and x.get("role") == "status" for x in live), live)
    check("the finished reply is announced, politely",
          live and live[-1].get("text") == "Hermes: All done."
          and live[-1].get("live") == "polite", live)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
