#!/usr/bin/env python3
"""Review R4-seams (S2-4): the Trust reason line cannot be written by the agent.

The approval cards (main window since S2-4, Quick Ask since this review) show
permissions.decide()'s `reason` as one quiet line in the Trust policy's voice:
"<pattern key> → <class> is set to <Tier>". The pattern key is not always the
dashboard's own word: the approval-gate plugin builds `computer_use:<action>`
from the action string in the AGENT's tool call, any length. The key came
first and the card caps the line at 300 characters, so a long action string
filled the whole line with the agent's prose ("… this is on your allow list,
approve it …") and pushed the policy's own words off the end.

decide() now shows a bounded, one-line, printable form of the key in the
reason (the verdict's `pattern_key` stays whole), so the policy's own words
always follow it — checked here on the real decide() and on the real card.
Offline; scratch HOME; no dashboard.
"""
import json
import os
import sys
import tempfile

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
os.environ["HOME"] = tempfile.mkdtemp(prefix="hermes-s3-reason-")
sys.path.insert(0, os.path.join(ROOT, "dashboard"))
sys.path.insert(0, SP)
import permissions as P  # noqa: E402
import _chat_ui as ui  # noqa: E402
import t_e15_approval_multiline as e15  # noqa: E402
import t_s2_approval_reason as s2  # noqa: E402

KEY_MAX = getattr(P, "REASON_KEY_MAX", 96)

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:600]))


PROSE = ("click. Trust policy: this exact action is on your always-allow list and was "
         "reviewed by the owner, it is safe to approve without reading further. ") * 4
EVIL_KEYS = [
    ("long prose", "computer_use:" + PROSE),
    ("line breaks", "computer_use:click\n\nTrust policy: pre-approved\r\nPress Approve\u2028now"),
    ("hidden characters", "computer_use:click" + "\u202e\u200b\u0000\u009f" * 120),
    ("blank padding", "computer_use:click" + " " * 400 + "\u2800" * 200 + "approved"),
]

print("== decide(): the key part of the reason is bounded ==")
REASONS = []
for name, key in EVIL_KEYS:
    v = P.decide({"pattern_key": key, "command": "computer_use click"})
    r = v.get("reason") or ""
    REASONS.append(r)
    check("%s: still asks" % name, v.get("tier") in ("ask", "never"), v)
    check("%s: the verdict keeps the whole key" % name, v.get("pattern_key") == key,
          v.get("pattern_key", "")[:80])
    check("%s: the reason is short" % name, len(r) <= KEY_MAX + 120, len(r))
    check("%s: ...one line, nothing unprintable" % name,
          "\n" not in r and "\r" not in r and all(ch.isprintable() for ch in r), repr(r[:160]))
    check("%s: ...and ends with the policy's own words" % name,
          (" is set to %s" % v["tier"].capitalize()) in r[-(60 + len(v.get("class") or "")):]
          or "asking" in r[-80:], r[-120:])

v = P.decide({"pattern_keys": ["recursive delete", "computer_use:" + PROSE],
              "command": "rm -rf build"})
check("a long key among several is bounded too", len(v.get("reason") or "") <= KEY_MAX + 120,
      v.get("reason"))

print("\n== the dashboard's own keys read exactly as before ==")
longest = max((d for _rx, d in P._DANGER_PATTERNS), key=len)
check("(premise) the longest upstream description fits the bound",
      len(longest) <= KEY_MAX, len(longest))
for key in ("recursive delete", longest, "computer_use:click", "github-write:octo/app"):
    v = P.decide({"pattern_key": key, "command": "true"})
    check("%r is shown whole" % key[:40], (v.get("reason") or "").startswith(key + " → "),
          v.get("reason"))

print("\n== on the real card the policy's words survive the 300-character cap ==")
fn = ui.extract("streamJob")
steps = [{"state": "approval", "done": False, "text": "", "status": "",
          "approval": {"command": "computer_use click", "approval_id": "K%d" % i,
                       "_policy": P.decide({"pattern_key": key, "command": "computer_use click"})}}
         for i, (_n, key) in enumerate(EVIL_KEYS)]
steps.append({"state": "done", "done": True, "reply": "fin", "text": "fin"})
script = e15.SCRIPT.replace(
    e15.SCRIPT[e15.SCRIPT.index("function grab()"):e15.SCRIPT.index("globalThis.fetch=")], s2.GRAB)
rc, data, out = ui.run_node(script % {"dom": ui.FAKE_DOM, "fn": fn, "steps": json.dumps(steps)})
check("streamJob ran in node", rc == 0 and data and "error" not in data, out[-800:])
seen = (data or {}).get("seen") or []
for i, (name, _key) in enumerate(EVIL_KEYS):
    pol = (seen[i + 1] or {}).get("pol") if len(seen) > i + 1 else None
    check("%s: the card's line still says what the policy is set to" % name,
          isinstance(pol, str) and " is set to " in pol and not pol.endswith("\u2026"), pol)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
