#!/usr/bin/env python3
"""E69 (MCP half) — chat_get refuses the hidden conversations in any case.

2026-11-09 audit E69: the chat store sits on a case-insensitive disk, but
hermes_mcp.t_chat_get compared the session id to HIDDEN_SESSIONS case-exactly,
so `NEEDSYOU` read the Needs-you drafts conversation back (sidestepping
`messages_search: false`) and `__PREWARM__` the prewarm one.

The REAL dashboard/hermes_mcp.py in process, dash_get stubbed so the test can
see whether the dashboard was even asked.  Offline.
"""
import contextlib
import io
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
          (("  — " + str(detail)[:300]) if detail and not cond else ""))


TMP = tempfile.mkdtemp(prefix="hermes-e69-")
ALLOW = os.path.join(TMP, "mcp-allow.json")
with open(ALLOW, "w") as fh:
    json.dump({"tools": {"messages_search": False}}, fh)
os.environ["HERMES_MCP_ALLOW"] = ALLOW
os.environ["HERMES_MCP_DASHBOARD"] = "http://127.0.0.1:9"
sys.path.insert(0, DASH)
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402

ASKED = []


def fake_get(path, params=None, timeout=None):
    ASKED.append((path, dict(params or {})))
    return {"title": "Needs you — drafts", "messages": [
        {"role": "user", "text": "Draft a reply to: ...", "ts": 1.0e9},
        {"role": "bot", "text": "DRAFT: Sure Anna, Thursday 3pm works.", "ts": 1.0e9 + 5}]}


M.dash_get = fake_get


def call(sid):
    del ASKED[:]
    with contextlib.redirect_stderr(io.StringIO()):
        return M.do_tools_call({"name": "chat_get", "arguments": {"session": sid}})


for sid in ("needsyou", "NEEDSYOU", "NeedsYou", "needsYOU", "__prewarm__",
            "__PREWARM__", "__Prewarm__", " NEEDSYOU "):
    r = call(sid)
    text = r["content"][0]["text"]
    check("chat_get(%r) is refused" % sid, r.get("isError") is True, text)
    check("chat_get(%r) never asked the dashboard" % sid, ASKED == [], ASKED)
    check("chat_get(%r) carries no draft text" % sid, "DRAFT" not in text, text)

r = call("chat-abc123")
check("an ordinary conversation is still read", r.get("isError") is False and
      "DRAFT" in r["content"][0]["text"], r)
r = call("Chat-ABC123")
check("an ordinary id in capitals is still read", r.get("isError") is False, r)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
