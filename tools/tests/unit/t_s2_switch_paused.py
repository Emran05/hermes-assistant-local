#!/usr/bin/env python3
"""S2 seam (E101): a model switch made while the agent is paused.

switch_model() writes the new model's files and, when a pause landed (E101:
re-checked under the lifecycle lock), answers {ok:true, loading:false,
paused:true} WITHOUT starting the server — the owner's Resume loads it.  The
menu's onModelClick ignored the body and called waitForModel(), so the
overlay sat on "Switching model…" polling /api/health for a model that would
not start, then said "Still loading…".  Now a paused answer stops there: no
poller, modelLoading cleared, the overlay says the model will load on Resume,
and the pill is refreshed from the server.

Runs the REAL functions from index.html (the t_b29 harness), offline.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _chat_ui as ui  # noqa: E402
import t_b29_swap_overlay as b29  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:600]))


def main():
    srcs = ([ui.extract_line(n) for n in b29.LINES]
            + [ui.extract(n) for n in b29.FNS + ["pausedModelSwap"]])
    check("index.html has every function the harness runs", all(srcs))
    script = b29.SCRIPT
    script = script.replace(
        "  return {ok:true,status:200,json:async()=>({ok:true,loading:true})};",
        "  if(MODE==='paused') return {ok:true,status:200,json:async()=>({ok:true,active:'m',loading:false,paused:true})};\n"
        "  if(MODE==='pausedstr') return {ok:true,status:200,json:async()=>({ok:true,loading:false,paused:'true'})};\n"
        "  return {ok:true,status:200,json:async()=>({ok:true,loading:true})};")
    a = script.index("  await run('resume-reject'")
    b = script.index("  console.log(JSON.stringify(out));")
    script = (script[:a] + "  await run('switch-paused','paused',()=>onModelClick(row));\n"
              "  await run('switch-pausedstr','pausedstr',()=>onModelClick(row));\n"
              "  await run('switch-ok','ok',()=>onModelClick(row));\n" + script[b:])
    rc, d, out = ui.run_node(script % {"dom": ui.FAKE_DOM, "fns": "\n".join(s for s in srcs if s)})
    check("ran in node", rc == 0 and isinstance(d, dict) and "error" not in d, out[-1500:])
    d = d or {}
    s = d.get("switch-paused") or {}
    check("paused: nothing escapes as an exception", s.get("threw") is False, s)
    check("paused: no wait-for-model poller is started", s.get("intervals") == 0, s)
    check("paused: modelLoading is cleared", s.get("loading") is False, s)
    check("paused: the overlay says the model loads on Resume, and names it",
          "Resume" in (s.get("sub") or "") and "Qwen3.5 9B" in (s.get("sub") or ""), s)
    check("paused: it does not say the switch failed",
          "didn" not in (s.get("title") or "") and "Try again" not in (s.get("sub") or ""), s)
    check("paused: the overlay comes down after its reading time", s.get("hidden") is True, s)
    check("paused: the model pill is refreshed from the server", (s.get("loads") or 0) >= 1, s)
    s = d.get("switch-pausedstr") or {}
    check("only a JSON true means paused (a string is the ordinary loading path)",
          s.get("intervals") == 1 and s.get("loading") is True, s)
    s = d.get("switch-ok") or {}
    check("an ordinary switch still waits for the model (overlay up, poller on)",
          s.get("hidden") is False and s.get("loading") is True and s.get("intervals") == 1, s)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
