#!/usr/bin/env python3
"""C18 (MCP part) — needs_you never says "nothing needs you" over a blind source.

2026-10-21 audit C18: with Full Disk Access denied (the normal state after
every app rebuild) Needs-you reads no messages, yet every surface — the MCP
needs_you tool included — said "Nothing needs you right now."  The dashboard
now carries the reason per source in payload["source_notes"] {name: reason};
the tool prints it as a caveat, under the same rule the widget uses (a note on
a source that is not "present"), and still honours messages_search (B06).

Imports the REAL dashboard/hermes_mcp.py; only dash_get is stubbed.  Offline.
"""
import io
import contextlib
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  — " + str(detail)[:400]) if detail and not cond else ""))


TMP = tempfile.mkdtemp(prefix="hermes-c18-")
os.environ["HERMES_MCP_ALLOW"] = os.path.join(TMP, "mcp-allow.json")
os.environ.setdefault("HERMES_MCP_DASHBOARD", "http://127.0.0.1:9")
sys.path.insert(0, DASH)
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402

BLIND = {"ok": True, "now": [], "today": [], "later": [],
         "today_count": 0, "later_count": 0, "never_count": 0,
         "sources": {"message": "absent", "reminder": "absent", "calendar": "present",
                     "approval": "present"},
         "source_notes": {"message": "Full Disk Access needed",
                          "reminder": "Apple Reminders is off in Settings",
                          "calendar": "a stale note on a present source"}}
STATE = {"payload": BLIND}
M.dash_get = lambda path, params=None, timeout=None: json.loads(json.dumps(STATE["payload"]))

print("--- empty inbox, two blind sources, messages_search ON")
M.ALLOW["tools"]["messages_search"] = True
out = M.t_needs_you({})
print("     | " + "\n     | ".join(out.splitlines()))
check("it does not say 'Nothing needs you right now.'",
      "Nothing needs you right now." not in out, out)
check("it says the inbox is incomplete", "incomplete" in out, out)
check("it names the Messages reason", "Full Disk Access needed" in out, out)
check("it names the Reminders reason", "Apple Reminders is off in Settings" in out, out)
check("a note on a PRESENT source is not a caveat",
      "stale note" not in out, out)

print("--- the same, messages_search OFF: the Messages note stays private (B06)")
M.ALLOW["tools"]["messages_search"] = False
out = M.t_needs_you({})
check("no Messages reason", "Full Disk Access" not in out, out)
check("the Reminders caveat is still there",
      "Apple Reminders is off in Settings" in out and
      "Nothing needs you right now." not in out, out)

print("--- items present + a blind source: items, then the caveat")
M.ALLOW["tools"]["messages_search"] = True
STATE["payload"] = dict(BLIND, now=[{"source": "calendar", "sender": "Dentist",
                                     "summary": "Dentist at 3pm", "reason": "event in <2 h"}])
out = M.t_needs_you({})
check("the item is shown", "Dentist at 3pm" in out, out)
check("a 'Not being read' line follows",
      "Not being read:" in out and "Full Disk Access needed" in out, out)

print("--- notes are upstream text: scrubbed and bounded like the rest")
STATE["payload"] = dict(BLIND, source_notes={
    "message": "<script>x</script> token: abcdef123456 " + "z" * 400})
out = M.t_needs_you({})
check("markup neutralised", "<script" not in out, out)
check("a labelled secret is redacted", "abcdef123456" not in out, out)
check("the reason is bounded", "z" * 200 not in out, len(out))

print("--- no notes (an older dashboard, or every source fine)")
STATE["payload"] = dict(BLIND, source_notes={})
out = M.t_needs_you({})
check("plain 'Nothing needs you right now.'", "Nothing needs you right now." in out, out)
STATE["payload"] = {k: v for k, v in BLIND.items() if k != "source_notes"}
out = M.t_needs_you({})
check("a payload without source_notes still works",
      "Nothing needs you right now." in out, out)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
