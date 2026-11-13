#!/usr/bin/env python3
"""The Stop control, in the chat composer (index.html) and Quick Ask
(aux_quickask.js) — 2026-11-12.

Runs the REAL functions cut out of both files in node on the shared fake DOM
(_chat_ui.py) with a scripted fetch. No dashboard, no model.

index.html: while the conversation on screen has a turn streaming here,
#stopbtn takes #send's place (Send stays disabled underneath — aux_desktop
and Enter read that); a click POSTs /api/chat/stop ONCE (a second click, and a
held Enter/Space, send nothing), the button reads "Stopping…" (aria-disabled)
until the poll reports done, a waiting approval card goes inert at once, the
reply keeps its text with the server's note (no second note drawn, no error
styling for a stop), and then Send is back.

Quick Ask: while its chat turn runs the go button is Stop (aria-label "Stop",
data-stop) and Return still never stops; one POST; "Stopping…" until done;
the stopped reply is not styled as an error; then the button is Send again.
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
        print("  FAIL %s %s" % (name, str(extra)[:900]))


DOM_EXTRA = r"""
El.prototype.removeAttribute=function(k){ delete this.attrs[k]; };
El.prototype.addEventListener=function(t,f){ (this._ls=this._ls||{})[t]=f; };
El.prototype.focus=function(){};
function _desc(root,sel){ const p=String(sel).trim().split(/\s+/); let set=[root];
  for(const part of p){ const next=[]; set.forEach(n=>El.prototype.querySelectorAll.call(n,part).forEach(x=>{ if(!next.includes(x)) next.push(x); })); set=next; }
  return set; }
"""

INDEX_JS = r"""
%(dom)s
%(extra)s
document.querySelectorAll=(sel)=>{ const p=String(sel).trim().split(/\s+/);
  if(p[0]==='#msgs') return _desc($('msgs'), p.slice(1).join(' '));
  return _desc(DOC_ROOT, sel); };
function addBubble(text,who,isMd){ const b=document.createElement('div'); b.className='bubble '+who;
  if(isMd){ const m=document.createElement('div'); m.className='md'; b.appendChild(m); } else b._text=String(text||'');
  $('msgs').appendChild(b); return b; }
globalThis.chatAnnounce=()=>{};
let session='chat-a';
let histLoading=0;
async function loadHistory(){}
const chatInflight=new Set(), chatStreams=new Set();
%(fns)s
$('send').disabled=true;
$('stopbtn').hidden=true;
%(wire)s
const POSTS=[]; const LOG=[]; let n=0;
function btns(){ const s=$('send'), st=$('stopbtn');
  return {sendHidden:!!s.hidden, sendDisabled:!!s.disabled, stopHidden:!!st.hidden,
          label:st.getAttribute('aria-label'), ariaDisabled:st.getAttribute('aria-disabled'),
          title:st.title}; }
globalThis.fetch=async(url,opts)=>{
  if(url==='/api/chat/stop'){ POSTS.push(JSON.parse(opts.body)); return {json:async()=>({ok:true,stopping:true})}; }
  if(url.startsWith('/api/chat/poll')){
    n++;
    if(n===1) return {json:async()=>({state:'approval',done:false,text:'Hel',status:'',can_stop:true,
                       approval:{command:'rm -rf x',approval_id:'A'}})};
    if(n===2){
      LOG.push({at:'running',b:btns()});
      const ev={key:'Enter',repeat:true,_p:false,preventDefault(){this._p=true;}};
      $('stopbtn')._ls.keydown(ev);
      LOG.push({at:'repeat',prevented:ev._p});
      const ev2={key:'Enter',repeat:false,_p:false,preventDefault(){this._p=true;}};
      $('stopbtn')._ls.keydown(ev2);
      LOG.push({at:'first-press',prevented:ev2._p});
      $('stopbtn').onclick(); $('stopbtn').onclick();
      await new Promise(r=>setImmediate(r));
      const cardBtns=document.querySelectorAll('#msgs .approval button');
      LOG.push({at:'clicked',b:btns(),posts:POSTS.length,
                cardBtns:cardBtns.length,cardInert:cardBtns.length>0&&cardBtns.every(x=>x.disabled)});
      return {json:async()=>({state:'running',done:false,text:'Hello',status:'stopping…',
                               stopping:true,can_stop:false,approval:null})};
    }
    if(n===3){
      LOG.push({at:'stopping',b:btns()});
      $('stopbtn').onclick();
      await new Promise(r=>setImmediate(r));
      LOG.push({at:'stopping-click',posts:POSTS.length});
      return {json:async()=>({state:'done',done:true,text:'Hello',reply:'Hello\n\nStopped by you.',
                               err:true,stopped:true,stopping:false,can_stop:false,persisted:true})};
    }
  }
  return {json:async()=>({})};
};
(async()=>{
  // the REAL caller: chatAttach holds the conversation, streams the job and,
  // in its finally, releases it and syncs the buttons
  const fin=await chatAttach('J','chat-a');
  const bub=$('msgs').children.filter(b=>b.className.includes('bot')).pop();
  LOG.push({at:'end',b:btns(),fin,posts:POSTS,bubbleClass:bub&&bub.className,
            bubble:bub&&bub.querySelector('.md')&&bub.querySelector('.md').innerHTML});
  console.log(JSON.stringify(LOG));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


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


def qa_block(start, end_line="    };"):
    """The statement starting with `start` (4-space indent) through `end_line`."""
    with open(QA, encoding="utf-8") as f:
        lines = f.read().split("\n")
    for i, line in enumerate(lines):
        if line.startswith(start):
            for j in range(i, len(lines)):
                if lines[j] == end_line:
                    return "\n".join(lines[i:j + 1])
    return None


def qa_stmt(start):
    """A statement starting with `start` through its closing `});` line."""
    return qa_block(start, "    });")


QA_JS = r"""
%(dom)s
%(extra)s
const SESSION='menubar';
const THREAD=$('thread');
const GO=document.createElement('button');
const POP={busy:false, job:null, stopping:null, thread:THREAD, go:GO,
  input:{value:'',style:{},focus(){}}};
const STATE={modelState:'online', lastPrompt:'', filter:''};
const W={localStorage:{setItem(){}}};
const LINES=[]; const POSTS=[]; const LOG=[]; const SUBMITS=[];
function grow(){} function setFilter(){} function beginWake(){}
function refreshState(){} function focusInput(){} function scrollDown(){} function fit(){}
function bridge(){} function deepCard(){ return null; } function fillDeep(){}
function submit(){ SUBMITS.push(1); }
function qaMd(s){ return esc(s); }
function place(el){ THREAD.appendChild(el); return el; }
function turn(text,who){ const w=document.createElement('div'); w.className='qa-turn '+who;
  const b=document.createElement('div'); b.className='qa-b '+who; b._text=String(text||'');
  if(who==='bot'){ const m=document.createElement('div'); m.className='md'; b.appendChild(m); }
  w.appendChild(b); place(w); return b; }
function line(text,kind){ LINES.push([String(text),kind||'']); const n=document.createElement('div'); n.className='qa-line'; return place(n); }
function busyLine(text){ const n=document.createElement('div'); n.className='qa-line'; n.setLabel=()=>{}; return place(n); }
THREAD.querySelectorAll=function(sel){ return _desc(this,sel); };
%(fns)s
%(wire)s
function st(){ return {stop:GO.getAttribute('data-stop'), label:GO.getAttribute('aria-label'),
  ariaDisabled:GO.getAttribute('aria-disabled'), disabled:!!GO.disabled, title:GO.title,
  busyDots:GO.getAttribute('data-busy')}; }
let n=0;
globalThis.j=async(url,b)=>{
  if(url==='/api/chat'){ return {ok:true, job:'QJ'}; }
  if(url==='/api/chat/stop'){ POSTS.push(b); return {ok:true, stopping:true}; }
  if(url.startsWith('/api/chat/poll')){
    n++;
    if(n===1) return {state:'running',done:false,text:'Hel',status:'',can_stop:true};
    if(n===2){
      LOG.push({at:'running',s:st()});
      const ev={key:' ',repeat:true,_p:false,preventDefault(){this._p=true;}};
      GO._ls.keydown(ev); LOG.push({at:'repeat',prevented:ev._p});
      GO.onclick(); GO.onclick();
      await new Promise(r=>setImmediate(r));
      LOG.push({at:'clicked',s:st(),posts:POSTS.length,submits:SUBMITS.length});
      return {state:'running',done:false,text:'Hello',stopping:true,can_stop:false};
    }
    if(n===3){
      LOG.push({at:'stopping',s:st()});
      return {state:'done',done:true,text:'Hello',reply:'Hello\n\nStopped by you.',err:true,stopped:true};
    }
  }
  return {};
};
(async()=>{
  POP.input.value='go';
  send();
  for(let i=0;i<300 && !(n>=3 && !POP.busy);i++) await new Promise(r=>setImmediate(r));
  for(let i=0;i<10;i++) await new Promise(r=>setImmediate(r));
  const bots=THREAD.querySelectorAll('.qa-b').filter(b=>b.className.includes('bot'));
  const b=bots[bots.length-1];
  GO.onclick();
  LOG.push({at:'end',s:st(),posts:POSTS,busy:POP.busy,job:POP.job,submits:SUBMITS.length,
            botClass:b&&b.className, reply:b&&b.querySelector('.md')&&b.querySelector('.md').innerHTML});
  console.log(JSON.stringify(LOG));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


def by(log, at):
    for e in log:
        if e.get("at") == at:
            return e
    return {}


def test_index():
    print("\nindex.html composer")
    names = ("chatJobMark", "syncStop", "stopTurn", "streamJob", "chatAttach")
    fns = [ui.extract(n) for n in names]
    check("index.html has %s" % " / ".join(names), all(fns), [n for n, f in zip(names, fns) if not f])
    wire = [ui.extract_line("const chatJobOf="),
            ui.extract_line("$('stopbtn').onclick"),
            ui.extract_line("$('stopbtn').addEventListener('keydown'")]
    check("index.html declares the job map and wires #stopbtn (click + keydown)", all(wire))
    src = ui.index_src()
    check("the composer has a Stop button in Send's slot, hidden at rest",
          re.search(r'id="send"[^>]*>\s*<svg[^\n]*</svg></button>\s*<button class="iconbtn" '
                    r'id="stopbtn" type="button" title="[^"]+" aria-label="Stop" hidden>', src)
          is not None)
    check("Stop draws a solid square", re.search(
        r'id="stopbtn"[^\n]*\n\s*<svg class="ic"[^>]*><rect ', src) is not None)
    if not (all(fns) and all(wire)):
        return
    js = INDEX_JS % {"dom": ui.FAKE_DOM, "extra": DOM_EXTRA, "fns": "\n".join(fns),
                     "wire": "\n".join(wire)}
    rc, data, out = ui.run_node(js)
    check("index harness ran", rc == 0 and isinstance(data, list), out[-1500:])
    if not isinstance(data, list):
        return
    r = by(data, "running")["b"]
    check("THE FEATURE: while running, Stop shows in Send's place",
          r["stopHidden"] is False and r["sendHidden"] is True, r)
    check("Send stays disabled underneath (unchanged for its readers)", r["sendDisabled"] is True, r)
    check("Stop is labelled 'Stop' and enabled", r["label"] == "Stop"
          and r["ariaDisabled"] == "false" and r["title"], r)
    check("a held Enter on Stop is ignored (repeat)", by(data, "repeat").get("prevented") is True)
    check("the first press is not swallowed", by(data, "first-press").get("prevented") is False)
    c = by(data, "clicked")
    check("THE FEATURE: two clicks POST /api/chat/stop once", c.get("posts") == 1, c)
    check("THE FEATURE: 'Stopping…' at once", c["b"]["label"] == "Stopping…"
          and c["b"]["ariaDisabled"] == "true" and c["b"]["stopHidden"] is False, c)
    check("THE FEATURE: the waiting approval card goes inert", c.get("cardInert") is True, c)
    s = by(data, "stopping")
    check("still 'Stopping…' until the poll reports done",
          s["b"]["label"] == "Stopping…" and s["b"]["stopHidden"] is False, s)
    check("a click while stopping sends nothing", by(data, "stopping-click").get("posts") == 1)
    e = by(data, "end")
    check("after done, through the real caller's finally: Stop is gone, Send is back "
          "and enabled", e["b"]["stopHidden"] is True and e["b"]["sendHidden"] is False
          and e["b"]["sendDisabled"] is False, e)
    check("one POST in all, for this job", e.get("posts") == [{"job": "J"}], e.get("posts"))
    check("the bubble keeps the text and the server's ONE note",
          (e.get("bubble") or "").startswith("Hello")
          and (e.get("bubble") or "").count("Stopped by you.") == 1, e.get("bubble"))
    check("a stopped reply is not drawn as an error", "err" not in (e.get("bubbleClass") or ""), e)


def test_quickask():
    print("\nQuick Ask")
    fns = [qa_fn(n) for n in ("syncGo", "send", "poll", "qaStop", "dots", "svg")]
    check("aux_quickask.js has syncGo / send / poll / qaStop", all(fns))
    go_click = qa_block("    POP.go.onclick = function () {")
    go_key = qa_stmt('    POP.go.addEventListener("keydown"')
    check("the go button is wired for Stop (click + keydown)", bool(go_click and go_key))
    if not (all(fns) and go_click and go_key):
        return
    with open(QA, encoding="utf-8") as f:
        qsrc = f.read()
    paths = re.search(r"\n  var PATHS = \{.*?\n  \};", qsrc, re.S)
    js = QA_JS % {"dom": ui.FAKE_DOM, "extra": DOM_EXTRA,
                  "fns": (paths.group(0) if paths else "") + "\n" + "\n".join(fns),
                  "wire": go_click + "\n" + go_key}
    rc, data, out = ui.run_node(js)
    check("Quick Ask harness ran", rc == 0 and isinstance(data, list), out[-1500:])
    if not isinstance(data, list):
        return
    r = by(data, "running")["s"]
    check("THE FEATURE: while its turn runs, the go button is Stop",
          r["stop"] == "1" and r["label"] == "Stop" and r["disabled"] is False
          and r["ariaDisabled"] == "false" and not r["busyDots"], r)
    check("a held Space on it is ignored", by(data, "repeat").get("prevented") is True)
    c = by(data, "clicked")
    check("THE FEATURE: two clicks POST /api/chat/stop once", c.get("posts") == 1, c)
    check("the click never submits a message", c.get("submits") == 0, c)
    check("'Stopping…' at once", c["s"]["label"] == "Stopping…"
          and c["s"]["ariaDisabled"] == "true", c)
    check("still 'Stopping…' until done", by(data, "stopping")["s"]["label"] == "Stopping…")
    e = by(data, "end")
    check("after done: the button is Send again",
          e["s"]["stop"] is None and e["s"]["label"] == "Send" and e["busy"] is False
          and e["job"] is None, e)
    check("one POST, for this job", e.get("posts") == [{"job": "QJ"}], e.get("posts"))
    check("after done a click is a normal submit", e.get("submits") == 1, e)
    check("the stopped reply keeps the text and the note, not styled as an error",
          (e.get("reply") or "").startswith("Hello") and "Stopped by you." in (e.get("reply") or "")
          and "err" not in (e.get("botClass") or ""), e)


def main():
    test_index()
    test_quickask()
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
