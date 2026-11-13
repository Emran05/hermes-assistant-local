#!/usr/bin/env python3
"""S2 seam (E105): the model menu and the server's in-flight disk rule.

download_model() now subtracts what downloads already in flight still have to
fetch (_dl_remaining_gb: catalog size minus the blobs already in the hub
cache) before judging the free-disk headroom.  The menu's own pre-check now
subtracts the same remainder (each downloading row's `remaining_gb`; see
t_s3_menu_inflight_disk.js), but its numbers are up to 30 s old, so the
server can still refuse a click the menu allowed.  This suite covers that
refusal: the row turns inert as "not enough disk", the reason names the
in-flight downloads with the server's own numbers, and the menu is NOT
rebuilt 1.5 s later into a clickable row the server will refuse again.  Other
failures keep the old retryable chip.

Runs the REAL onModelClick from index.html in node, offline.
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
        print("  FAIL %s %s" % (name, str(extra)[:600]))


SCRIPT = r"""
%(dom)s
let modelLoading=false, agentPaused=false, agentIdle=false, lastModelDisk=120;
let LOADS=0; function loadModels(){LOADS++;} function renderHub(){}
%(gb)s
globalThis.confirm=()=>true;
const TIMERS=[]; globalThis.setTimeout=(fn,ms)=>{TIMERS.push(ms);return 1;};
globalThis.setInterval=()=>1; globalThis.clearInterval=()=>{};
%(fn)s
let ANSWER=null;
globalThis.fetch=async(url,opts)=>{
  if(ANSWER==='throw') throw new TypeError('Load failed');
  return {ok:true,status:200,json:async()=>ANSWER};
};
function mkRow(){
  const el=document.createElement('div'); el.className='mmi'; el.dataset.id='org/model'; el.dataset.dl='0';
  el.dataset.dlgb='17';
  el.innerHTML='<div class="nm">Big Model</div><div class="rm2">note</div><span class="act">download</span>';
  document.body.appendChild(el); return el;
}
const out={};
async function run(label, answer){
  ANSWER=answer; TOASTS.length=0; TIMERS.length=0; LOADS=0;
  const el=mkRow();
  await onModelClick(el);
  out[label]={act:el.querySelector('.act').textContent, rm2:el.querySelector('.rm2').textContent,
    disk:el.dataset.disk||null, busy:el.dataset.busy||null, toasts:TOASTS.slice(), timers:TIMERS.slice()};
}
(async()=>{
  await run('inflight',{ok:false,error:'not enough free disk',download_gb:17,disk_free_gb:40,inflight_gb:30.2,disk_headroom_gb:5});
  await run('plain',{ok:false,error:'not enough free disk',download_gb:17,disk_free_gb:20,inflight_gb:0,disk_headroom_gb:5});
  await run('oldserver',{ok:false,error:'not enough free disk'});
  await run('other',{ok:false,error:'no interpreter with huggingface_hub (venv missing?)'});
  await run('throw','throw');
  console.log(JSON.stringify(out));
})().catch(e=>console.log(JSON.stringify({error:String(e&&e.stack||e)})));
"""


def main():
    fn = ui.extract("onModelClick")
    gb = ui.extract("gbTxt")
    check("index.html has onModelClick and gbTxt", bool(fn and gb))
    rc, d, out = ui.run_node(SCRIPT % {"dom": ui.FAKE_DOM, "fn": fn or "", "gb": gb or ""})
    check("ran in node", rc == 0 and isinstance(d, dict) and "error" not in d, out[-1500:])
    d = d or {}
    s = d.get("inflight") or {}
    check("a refusal turns the row inert as 'not enough disk'",
          s.get("act") == "not enough disk" and s.get("disk") == "short" and not s.get("busy"), s)
    why = " ".join([s.get("rm2") or ""] + (s.get("toasts") or []))
    check("...and says the downloads in progress still need ~30.2 GB", "30.2" in why and "in progress" in why, why)
    check("...with the server's own numbers for this model and the free space",
          "17" in why and "40" in why, why)
    check("...and the menu is not rebuilt at once into a row the server will refuse again",
          1500 not in (s.get("timers") or []), s.get("timers"))
    s = d.get("plain") or {}
    why = " ".join([s.get("rm2") or ""] + (s.get("toasts") or []))
    check("with nothing in flight it says the disk is simply too full (no in-flight clause)",
          s.get("disk") == "short" and "in progress" not in why and "20" in why, why)
    s = d.get("oldserver") or {}
    check("an older server's bare refusal still marks the row (no invented numbers)",
          s.get("act") == "not enough disk" and s.get("disk") == "short"
          and "undefined" not in (s.get("rm2") or "") + " ".join(s.get("toasts") or []), s)
    for lab in ("other", "throw"):
        s = d.get(lab) or {}
        check("%s failure: the old retryable chip (failed, menu reloaded in 1.5 s, not disk-short)" % lab,
              s.get("act") == "failed" and 1500 in (s.get("timers") or []) and not s.get("disk"), s)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
