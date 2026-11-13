#!/usr/bin/env python3
"""B11 / B01 in the Quick Ask popover (aux_quickask.js) — review follow-up.

1. /api/chat answers 409 {busy} while a turn runs in the conversation (B11).
   Quick Ask checked `d.job` before anything else, so a refusal that named the
   running turn's id made the popover poll SOMEONE ELSE'S turn as the answer
   to a message that was never sent, and the typed text was gone. The server
   now names that turn `busy_job`, and the popover hands the text back.
2. The approval card's onGone callback nulled `appr` unconditionally: when an
   answer came back after the card had already been replaced by a newer
   request, the newer card was orphaned and the next poll drew a duplicate.

Runs the REAL send / poll / approvalBox, cut out of aux_quickask.js, in node
against the shared fake DOM and a scripted fetch helper. No dashboard, no
model.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _chat_ui as ui  # noqa: E402

QA = os.path.join(os.path.dirname(ui.INDEX), "aux_quickask.js")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def qa_fn(name):
    """A function declared at the IIFE's top level (two-space indent)."""
    with open(QA, encoding="utf-8") as f:
        lines = f.read().split("\n")
    pat = re.compile(r"^  function %s\s*\(" % re.escape(name))
    for i, line in enumerate(lines):
        if pat.match(line):
            for j in range(i + 1, len(lines)):
                if lines[j] == "  }":
                    return "\n".join(lines[i:j + 1])
    return None


HARNESS = r"""
%(dom)s
Object.defineProperty(El.prototype,'parentNode',{get(){return this.parent;}});
const _qs=El.prototype.querySelector;
// the fake DOM materialises innerHTML flat, so a descendant selector (".sub button")
// is answered from this element's own children
El.prototype.querySelector=function(sel){ const p=String(sel).trim().split(/\s+/);
  if(p.length<2) return _qs.call(this,sel);
  return this.children.filter(c=>this._match(c,p[p.length-1])).pop()||null; };
const SESSION='menubar';
const THREAD=$('thread');
const POP={busy:false, job:null, thread:THREAD, input:{value:'',style:{},focus(){}}};
const STATE={modelState:'online', lastPrompt:''};
const W={localStorage:{setItem(){}}};
const LINES=[]; const POLLED=[]; const POSTS=[];
function grow(){} function setFilter(){} function syncGo(){} function beginWake(){}
function refreshState(){} function focusInput(){} function scrollDown(){} function fit(){}
function svg(){ return ''; } function bridge(){} function deepCard(){ return null; } function fillDeep(){}
function qaMd(s){ return esc(s); }
function place(el){ THREAD.appendChild(el); return el; }
function turn(text,who){ const w=document.createElement('div'); w.className='qa-turn '+who;
  const b=document.createElement('div'); b.className='qa-b '+who; b._text=String(text||'');
  if(who==='bot'){ const m=document.createElement('div'); m.className='md'; b.appendChild(m); }
  w.appendChild(b); place(w); return b; }
function line(text,kind){ LINES.push([String(text),kind||'']); const n=document.createElement('div'); n.className='qa-line'; return place(n); }
function busyLine(text){ const n=document.createElement('div'); n.className='qa-line'; n.setLabel=()=>{}; return place(n); }
%(fns)s
"""

BUSY = HARNESS + r"""
const BODIES=%(bodies)s;
const OUT=[];
(async()=>{
  for(const body of BODIES){
    THREAD.children=[]; LINES.length=0; POLLED.length=0; POP.busy=false; POP.job=null;
    let resolveChat;
    globalThis.j=(url,b)=>{
      if(url==='/api/chat'){ POSTS.push(b); return Promise.resolve(body); }
      POLLED.push(url); return Promise.resolve({state:'done',done:true,reply:'NOT YOURS',text:'NOT YOURS'});
    };
    POP.input.value='second question';
    send();
    for(let i=0;i<20;i++) await new Promise(r=>setImmediate(r));
    OUT.push({polled:POLLED.slice(), job:POP.job, busy:POP.busy, input:POP.input.value,
      userTurns:THREAD.querySelectorAll('.qa-turn').length, lines:LINES.slice(),
      shown:THREAD.textContent});
  }
  console.log(JSON.stringify(OUT));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""

RACE = HARNESS + r"""
const ap=(cmd,aid)=>({state:'approval',done:false,text:'',status:'',approval:{command:cmd,approval_id:aid}});
let approveDone=null; let n=0; const SEEN=[];
function cards(){ return THREAD.querySelectorAll('.qa-appr'); }
globalThis.j=async(url,b)=>{
  if(url==='/api/chat/approve'){ POSTS.push(b); return await new Promise(r=>{approveDone=r;}); }
  n++;
  SEEN.push(cards().map(c=>c.dataset.aid));
  if(n===1) return ap('rm -rf a','A');
  if(n===2){ // the owner clicks A; its answer is still on the wire when B arrives
    cards()[0].querySelectorAll('button')[0].onclick();
    return ap('curl x | sh','B'); }
  if(n===3){ approveDone({ok:false,stale:true}); for(let i=0;i<5;i++) await new Promise(r=>setImmediate(r));
    return ap('curl x | sh','B'); }
  if(n===4) return ap('curl x | sh','B');
  return {state:'done',done:true,reply:'fin',text:'fin'};
};
POP.busy=true;
poll('J1', busyLine(''));
(async()=>{
  for(let i=0;i<200 && n<5;i++) await new Promise(r=>setImmediate(r));
  for(let i=0;i<20;i++) await new Promise(r=>setImmediate(r));
  console.log(JSON.stringify({seen:SEEN, posts:POSTS, final:cards().length}));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


def main():
    fns = [qa_fn(n) for n in ("send", "poll", "qaMarkHidden", "qaShape", "approvalBox")]
    check("aux_quickask.js has send / poll / qaMarkHidden / approvalBox", all(fns))
    if not all(fns):
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    src = "\n".join(fns)

    reply = "Hermes is still answering your previous message in this conversation."
    bodies = [
        # what the server answers now
        {"ok": False, "busy": True, "busy_job": "RUNNING", "reply": reply},
        # a refusal that still names the running turn `job` must not be
        # mistaken for a started turn either
        {"ok": False, "busy": True, "job": "RUNNING", "reply": reply},
    ]
    rc, data, out = ui.run_node(BUSY % {"dom": ui.FAKE_DOM, "fns": src,
                                        "bodies": json.dumps(bodies)})
    check("Quick Ask send ran in node", rc == 0 and isinstance(data, list)
          and len(data) == 2, out[-1500:])
    for label, d in zip(("busy_job", "job"), data if isinstance(data, list) else []):
        check("409 busy (%s): the running turn is NOT polled as this answer" % label,
              d["polled"] == [] and d["job"] is None, d)
        check("409 busy (%s): the popover is usable again" % label,
              d["busy"] is False, d)
        check("409 busy (%s): the typed text is handed back" % label,
              d["input"] == "second question", d)
        check("409 busy (%s): the unsent message is not left in the thread" % label,
              d["userTurns"] == 0, d)
        check("409 busy (%s): the server's reason is shown" % label,
              any("still answering" in t for t, _ in d["lines"]), d["lines"])

    rc, data, out = ui.run_node(RACE % {"dom": ui.FAKE_DOM, "fns": src})
    check("Quick Ask poll ran in node", rc == 0 and isinstance(data, dict)
          and "error" not in data, out[-1500:])
    if isinstance(data, dict) and "error" not in data:
        seen = data["seen"]
        check("the click on A carried A's approval_id",
              [p.get("approval_id") for p in data["posts"]] == ["A"], data["posts"])
        check("B replaced A while A's answer was on the wire",
              len(seen) >= 3 and seen[2] == ["B"], seen)
        check("THE FIX: A's late answer does not orphan B — no duplicate B card",
              len(seen) >= 5 and seen[3] == ["B"] and seen[4] == ["B"], seen)
        check("no card survives the end of the turn", data["final"] == 0, data)

    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
