#!/usr/bin/env python3
"""t_c122_ptt_gate.py — a release while the speech engine is starting is not lost.

2026-10-21 audit C122: on the SpeechAnalyzer path the transcription session
exists only after `await live.start()` returns (100 ms to seconds).  The old
endDictation() began `guard let live = session else { return }`, so a key-up
inside that window was dropped; the recorder then started with the key up and
recorded until the 60 s watchdog.

The fix keeps the push-to-talk INTENT in a pure `struct PushToTalk` that the
Controller consults on every transition.  This suite lifts the REAL struct out
of app/dictation/main.swift, compiles it with swiftc in a temp dir and drives
the audit's sequences through it, then checks that the Controller is wired to
it (the parts that need AppKit cannot run headless).  The helper app is never
built.  Against the old source there is no struct and the wiring checks fail.

    python3 t_c122_ptt_gate.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _dictation_swift as SW  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  ok   " + name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


if not SW.available():
    print("SKIP swiftc is not installed (the helper is macOS-only)")
    sys.exit(0)

src = SW.read_source()
piece = SW.extract(src, "struct PushToTalk {")
check("main.swift declares the push-to-talk gate (struct PushToTalk)", piece is not None)

# --- behaviour of the real struct -------------------------------------------
if piece is not None:
    main = r'''
var g = PushToTalk()

// 1. the audit's sequence: press, release inside the start, start completes
check("a press starts an engine", g.press(at: 0) == true)
check("release during the start stops nothing", g.release(at: 120) == .none)
check("the finished start does NOT record", g.engineReady() == .abandon(meant: false))
check("and the gate is idle again", g.phase == .idle && !g.pressed)

// 2. a real hold that outlasted the user's patience but not the engine start
g = PushToTalk()
_ = g.press(at: 0)
_ = g.release(at: 900)
check("a long hold released before ready is abandoned, with a reason",
      g.engineReady() == .abandon(meant: true))

// 3. the normal path
g = PushToTalk()
_ = g.press(at: 0)
check("key still down when ready -> capture", g.engineReady() == .capture)
check("phase is live", g.phase == .live)
check("a second press while live starts nothing", g.press(at: 500) == false)
check("release after a real hold finishes", g.release(at: 1800) == .finish)
check("idle after finishing", g.phase == .idle)

// 4. a tap / an AltGr chord after the engine was already up
g = PushToTalk()
_ = g.press(at: 0)
_ = g.engineReady()
check("a hold under the minimum is discarded unheard", g.release(at: 140) == .discard)
check("the minimum is a quarter second", PushToTalk.minHoldMs == 250)

// 5. press, release, press again while the FIRST start is still in flight
g = PushToTalk()
check("first press starts", g.press(at: 0) == true)
_ = g.release(at: 60)
check("second press joins the in-flight start (no second engine)",
      g.press(at: 90) == false)
check("the start serves the second press", g.engineReady() == .capture)
check("which finishes normally", g.release(at: 1400) == .finish)

// 6. the watchdog always finishes, and the real key-up afterwards is a no-op
g = PushToTalk()
_ = g.press(at: 0)
_ = g.engineReady()
check("watchdog finishes", g.release(at: 60000, forced: true) == .finish)
check("the late key-up does nothing", g.release(at: 61000) == .none)
g = PushToTalk()
_ = g.press(at: 0)
_ = g.engineReady()
check("a forced stop is never discarded as a tap",
      g.release(at: 10, forced: true) == .finish)

// 7. a start that failed (permission prompt, no recognizer, no input device)
g = PushToTalk()
_ = g.press(at: 0)
g.failed()
check("after a failure the release is a no-op", g.release(at: 700) == .none)
check("and the next press starts afresh", g.press(at: 900) == true)

// 8. a stray engineReady with nothing starting never records
g = PushToTalk()
check("ready while idle is abandoned", g.engineReady() == .abandon(meant: false))
'''
    p, f, out = SW.run([piece], main)
    print("\n".join("  " + ln for ln in out.splitlines() if ln and not ln.startswith("SWIFT")))
    PASS += p
    FAIL += f

# --- the Controller is wired to it ------------------------------------------
end = SW.extract(src, "    private func endDictation() {") or ""
begin = SW.extract(src, "    private func beginDictation() {") or ""
ready = SW.extract(src, "    private func engineReady(_ live: LiveSession) {") or ""
settle = SW.extract(src, "    private func settle(_ s: StatusIcon.State) {") or ""
deliver = SW.extract(src, "    private func deliver(") or ""
empty = SW.extract(src, "    private func finishEmpty(") or ""

first = end.split("{", 1)[1].strip().splitlines()[0].strip() if end else ""
check("endDictation no longer starts by dropping a release with no session",
      first != "guard let live = session else { return }", first)
check("endDictation records the release before looking at the session",
      re.search(r"ptt\.release\(at:.*\n(?:.*\n)*?\s*guard verdict != \.none, let live = session",
                end) is not None)
check("beginDictation records the press before starting an engine",
      begin.find("ptt.press(") != -1
      and begin.find("ptt.press(") < begin.find("AnalyzerLive()"))
check("the analyzer start hands its session to engineReady, not straight to capture",
      "self.engineReady(live)" in begin and "self.startCapture(live)" not in begin)
check("engineReady captures only on .capture and cancels otherwise",
      ".capture:" in ready and "startCapture(live)" in ready and "live.cancel()" in ready)
check("a short hold is discarded with cancel(), not transcribed",
      "verdict == .discard" in end and "live.cancel()" in end)
check("LiveSession can be cancelled", re.search(
      r"protocol LiveSession[^}]*func cancel\(\)", src) is not None)
check("both engines implement cancel()", src.count("    func cancel() {") >= 2)
check("deliver/finishEmpty settle through the busy guard, not setState",
      "settle(" in deliver and "settle(" in empty
      and "setState(" not in deliver and "setState(" not in empty)
check("the busy guard keeps a newer dictation's state",
      "if ptt.phase != .idle {" in settle)
# A tap discarded AFTER the recorder started (state already .listening) while an
# older dictation is still being cleaned: the icon must not stay "listening".
check("with an older dictation still pending, the icon says cleaning",
      "setState(pending > 0 ? .cleaning : s)" in settle, settle)

print()
print("TESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
