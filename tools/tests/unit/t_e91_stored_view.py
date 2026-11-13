#!/usr/bin/env python3
"""E91: an unknown stored view name no longer blanks the whole stage.

2026-11-09 audit: index.html's boot line called setView() with whatever
localStorage.hermes_view held, and setView hid every view whose name differed
and saved the value back — so `bogus`, `Hub`, or a view name from a newer
build left no view and no tab on every launch until a tab was clicked.
Now setView falls back to the hub for a name it does not know, and the boot
line leaves `agent` / `desktop` to the modules that own them (aux_agent.js
migrates those itself; if it failed to load, the hub shows instead of
nothing).

Runs the REAL setView and boot line from index.html in node on a tiny DOM.
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


JS = r"""
const STORE={};
globalThis.localStorage={getItem:k=>(k in STORE?STORE[k]:null),setItem:(k,v)=>{STORE[k]=String(v);}};
const EL={};
function mk(id){ const cls=new Set(); return EL[id]={id,hidden:false,style:{},attrs:{},
  classList:{toggle:(c,on)=>{on?cls.add(c):cls.delete(c);},contains:c=>cls.has(c)},
  setAttribute(k,v){this.attrs[k]=String(v);}}; }
['view-hub','view-mind','view-console','tab-hub','tab-mind','tab-console','hubctl'].forEach(mk);
EL['view-mind'].hidden=true; EL['view-console'].hidden=true;   // the page's initial state
globalThis.$=id=>EL[id];
let curView='hub';
const CALLS=[];
globalThis.loadCapabilities=()=>CALLS.push('cap'); globalThis.loadConsole=()=>CALLS.push('con');
%(setView)s
const RUN=(sv)=>{ for(const k in STORE)delete STORE[k];
  EL['view-hub'].hidden=false; EL['view-mind'].hidden=true; EL['view-console'].hidden=true;
  if(sv!==null)STORE.hermes_view=sv;
  %(boot)s
  return {shown:['hub','mind','console'].filter(n=>!EL['view-'+n].hidden),
          on:['hub','mind','console'].filter(n=>EL['tab-'+n].classList.contains('on')),
          stored:STORE.hermes_view===undefined?null:STORE.hermes_view}; };
console.log(JSON.stringify(%(cases)s.map(RUN)));
"""


def main():
    fn = ui.extract("setView")
    boot = ui.extract_line("{const sv=localStorage.getItem('hermes_view');")
    check("index.html has setView and the stored-view boot line", bool(fn and boot))
    if not (fn and boot):
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    cases = ["bogus", "Hub", "MIND", "mind", "console", "agent", "desktop", None, "hub"]
    rc, data, out = ui.run_node(JS % {"setView": fn, "boot": boot, "cases": json.dumps(cases)})
    check("ran in node", rc == 0 and isinstance(data, list), out[-1200:])
    if not isinstance(data, list):
        print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
        return 1
    r = dict(zip([str(c) for c in cases], data))
    for sv in ("bogus", "Hub", "MIND"):
        check("stored %r: the hub is shown with its tab, never a blank stage" % sv,
              r[sv]["shown"] == ["hub"] and r[sv]["on"] == ["hub"], r[sv])
        check("stored %r: the bad value is not written back" % sv,
              r[sv]["stored"] in ("hub", None), r[sv])
    check("stored 'mind': Settings opens as before",
          r["mind"]["shown"] == ["mind"] and r["mind"]["stored"] == "mind", r["mind"])
    check("stored 'console': opens as before", r["console"]["shown"] == ["console"], r["console"])
    for sv in ("agent", "desktop"):
        check("stored %r: left for its module (stage not blanked, value kept)" % sv,
              r[sv]["shown"] == ["hub"] and r[sv]["stored"] == sv, r[sv])
    check("nothing stored: the hub", r["None"]["shown"] == ["hub"], r["None"])
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
