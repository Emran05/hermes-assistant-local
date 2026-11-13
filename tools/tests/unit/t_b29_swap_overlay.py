#!/usr/bin/env python3
"""B29 (2026-10-08 audit): the model-swap overlay stayed up for good when its
POST failed.

resumeAgent / wakeAgent / a model switch put up a full-window modal, then did
a bare `await fetch(...)`. A rejected fetch (dashboard restarting) threw out of
the function and an {ok:false} answer was ignored, so the overlay never came
down and `modelLoading` stayed true until ⌘R.

Runs the REAL functions from index.html in node (resumeAgent, wakeAgent,
onModelClick, swapPost, failModelSwap, showModelSwap, hideModelSwap,
waitForModel) against a fake DOM and a scripted fetch.
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


FNS = ["resumeAgent", "wakeAgent", "onModelClick", "swapPost", "failModelSwap",
       "showModelSwap", "hideModelSwap", "waitForModel", "swapGone",
       # R3 motion (§8.6): the overlay's steps go through HA() (the page's
       # ANIM_NULL here, from _chat_ui.FAKE_DOM) and these helpers (drawIn is
       # the check mark's draw, which keeps the page's own line caps)
       "swapStep", "swapLeave", "drawIn"]
# ...and the overlay's top-level state they share
LINES = ["let swapGen", "const SWAP_CHECK"]

SCRIPT = r"""
%(dom)s
let modelLoading=false, agentPaused=false, agentIdle=false, lastModelDisk=null;
let LOADS=0; function loadModels(){LOADS++;} function renderHub(){} function gbTxt(x){return x+' GB';}
globalThis.confirm=()=>true;
const INTERVALS=[]; globalThis.setInterval=(fn,ms)=>{INTERVALS.push(ms);return 1;}; globalThis.clearInterval=()=>{};
%(fns)s
let MODE='reject';
globalThis.fetch=async(url,opts)=>{
  if(MODE==='reject') throw new TypeError('Load failed');
  if(MODE==='okfalse') return {ok:true,status:200,json:async()=>({ok:false,error:'launchctl would not start the model server'})};
  if(MODE==='http500') return {ok:false,status:500,json:async()=>{throw new SyntaxError('not json');}};
  return {ok:true,status:200,json:async()=>({ok:true,loading:true})};
};
const tick=()=>new Promise(r=>setImmediate(r));
async function settle(){ for(let i=0;i<6;i++) await tick(); }
function state(){ const o=$('model-swap'); return {hidden:!!o.hidden, loading:modelLoading,
  title:$('swap-title').textContent, sub:$('swap-sub').textContent, loads:LOADS, intervals:INTERVALS.length}; }
function reset(){ const o=$('model-swap'); o.hidden=true; $('swap-title').textContent=''; $('swap-sub').textContent='';
  LOADS=0; INTERVALS.length=0; modelLoading=false; }
const out={};
async function run(label, mode, fn){
  reset(); MODE=mode; let threw=false;
  try{ await fn(); }catch(e){ threw=String(e); }
  await settle();
  out[label]=Object.assign(state(),{threw});
}
const row={dataset:{id:'mlx-community/Qwen3.5-9B-4bit',dl:'1'},querySelector:(s)=>({textContent:'Qwen3.5 9B'})};
(async()=>{
  await run('resume-reject','reject',()=>resumeAgent());
  await run('resume-okfalse','okfalse',()=>resumeAgent());
  await run('wake-500','http500',()=>wakeAgent());
  await run('switch-reject','reject',()=>onModelClick(row));
  await run('resume-ok','ok',()=>resumeAgent());
  console.log(JSON.stringify(out));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


def main():
    srcs = []
    for n in LINES:
        f = ui.extract_line(n)
        if f:
            srcs.append(f)
    for n in FNS:
        f = ui.extract(n)
        if f:
            srcs.append(f)
    check("index.html has the swap functions", ui.extract("resumeAgent") and ui.extract("showModelSwap"))
    check("...and every helper and state line this suite runs them with",
          all(ui.extract(n) for n in FNS) and all(ui.extract_line(n) for n in LINES),
          [n for n in FNS if not ui.extract(n)] + [n for n in LINES if not ui.extract_line(n)])
    rc, d, out = ui.run_node(SCRIPT % {"dom": ui.FAKE_DOM, "fns": "\n".join(srcs)})
    check("ran in node", rc == 0 and isinstance(d, dict) and "error" not in d, out[-1500:])
    d = d or {}
    for label, why in (("resume-reject", "a rejected fetch (dashboard restarting)"),
                       ("resume-okfalse", "an {ok:false} answer"),
                       ("wake-500", "an HTTP 500 with no JSON"),
                       ("switch-reject", "a model switch whose POST rejects")):
        s = d.get(label) or {}
        check("THE FIX (%s): nothing escapes as an exception" % why,
              s.get("threw") is False, s)
        check("THE FIX (%s): the overlay comes down" % why, s.get("hidden") is True, s)
        check("THE FIX (%s): modelLoading is cleared" % why, s.get("loading") is False, s)
        check("(%s): it says the action did not start" % why,
              "didn" in (s.get("title") or "") and "Try again" in (s.get("sub") or ""), s)
        check("(%s): the model pill is refreshed from the server" % why,
              (s.get("loads") or 0) >= 1, s)
        check("(%s): no wait-for-model poller is started" % why,
              s.get("intervals") == 0, s)
    s = d.get("resume-okfalse") or {}
    check("the server's reason is shown", "launchctl would not start" in (s.get("sub") or ""), s)
    s = d.get("switch-reject") or {}
    check("a failed switch names the model", "Qwen3.5 9B" in (s.get("sub") or ""), s)
    s = d.get("resume-ok") or {}
    check("a resume that started still waits for the model (overlay up, poller on)",
          s.get("hidden") is False and s.get("loading") is True
          and s.get("intervals") == 1, s)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
