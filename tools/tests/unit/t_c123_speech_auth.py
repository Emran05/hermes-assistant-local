#!/usr/bin/env python3
"""t_c123_speech_auth.py — the SFSpeechRecognizer fallback can be authorized,
and one SpeechAnalyzer refusal is not forever.

2026-10-21 audit C123: (a) nothing in the helper ever called
SFSpeechRecognizer.requestAuthorization, so on macOS 14/15 (where the fallback
is the only engine) every hold ended "speech recognition is not authorized"
with no prompt; (b) one analyzer refusal set `engineKind = .legacy` for the
life of the process.

The REAL `enum SpeechAuth` is compiled out of app/dictation/main.swift (with
the Speech framework, never the helper app) and its decisions checked; the
Controller's use of it is checked in the source.  Against the old source
there is no SpeechAuth, no request, and the sticky assignment is present.

    python3 t_c123_speech_auth.py
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
piece = SW.extract(src, "enum SpeechAuth {")
check("main.swift declares the authorization step (enum SpeechAuth)", piece is not None)

if piece is not None:
    main = r'''
check("not determined -> ask (this is what never happened)",
      SpeechAuth.step(.notDetermined) == .ask)
check("authorized -> proceed", SpeechAuth.step(.authorized) == .proceed)
check("denied -> refuse", SpeechAuth.step(.denied) == .refuse)
check("restricted -> refuse", SpeechAuth.step(.restricted) == .refuse)
'''
    p, f, out = SW.run(["import Speech", piece], main, frameworks=("Speech",))
    print("\n".join("  " + ln for ln in out.splitlines() if ln and not ln.startswith("SWIFT")))
    PASS += p
    FAIL += f

legacy = SW.extract(src, "    private func startLegacy() {") or ""
begin = SW.extract(src, "    private func beginDictation() {") or ""
end = SW.extract(src, "    private func endDictation() {") or ""

ask = legacy.find("SFSpeechRecognizer.requestAuthorization")
check("the fallback asks for Speech Recognition", ask != -1)
check("it decides with SpeechAuth.step before building a recognizer",
      "SpeechAuth.step(SFSpeechRecognizer.authorizationStatus())" in legacy
      and -1 < legacy.find("SpeechAuth.step(") < legacy.find("LegacyLive()"))
ask_branch = legacy[legacy.find("case .ask:"):legacy.find("case .refuse:")]
check("the asking press is spent on the prompt (returns, gate reset)",
      "ptt.failed()" in ask_branch and re.search(r"\n\s*return\n", ask_branch) is not None)
check("the answer is handled on the main thread",
      "DispatchQueue.main.async" in ask_branch)
check("the --selftest file path still never prompts",
      "requestAuthorization" not in (SW.extract(src, "    static func transcribeFile(_ url: URL, _ done:") or "x"))
check("a refusal is not sticky: nothing assigns engineKind = .legacy",
      re.search(r"engineKind\s*=\s*\.legacy", src) is None)
check("the analyzer refusal falls back for this press only",
      "self.startLegacy()" in begin and "the next one tries again" in begin)
check("the transcript reports the engine this dictation used",
      "live is LegacyLive ? EngineKind.legacy : engineKind" in end)
check("an undetermined grant is reported as unknown, never denied",
      re.search(r"case \.denied, \.restricted: return \.denied\s*\n\s*default: return \.unknown",
                SW.extract(src, "    static func speech(engine: String") or "") is not None)

print()
print("TESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
