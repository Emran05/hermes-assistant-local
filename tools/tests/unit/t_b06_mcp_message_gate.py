#!/usr/bin/env python3
"""B06 — with messages_search off, no MCP tool hands out Message Center data.

2026-10-08 audit B06: `needs_you` printed `sender` + `summary` (the message
preview) for every item, message items included, while `messages_search` was
off; `hermes_search` still printed the per-source `message N` count; and the
allowlist parser used bool(value), so the STRING "false" switched a tool on.

Imports the REAL dashboard/hermes_mcp.py with HERMES_MCP_ALLOW pointing at a
throwaway file and stubs only dash_get (no dashboard, no network).
"""
import io
import json
import os
import sys
import tempfile
import contextlib

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  — " + str(detail)[:400]) if detail and not cond else ""))


TMP = tempfile.mkdtemp(prefix="hermes-b06-")
ALLOW = os.path.join(TMP, "mcp-allow.json")
# The owner TYPED strings: every one of these must read as OFF.
with open(ALLOW, "w") as fh:
    json.dump({"tools": {"messages_search": "false", "doctor": "off"},
               "writes": {"evals": "false", "memory_facts": "no",
                          "prompt_budget": 1, "tool_budget": "true"}}, fh)
os.environ["HERMES_MCP_ALLOW"] = ALLOW
os.environ.setdefault("HERMES_MCP_DASHBOARD", "http://127.0.0.1:9")

sys.path.insert(0, DASH)
err = io.StringIO()
with contextlib.redirect_stderr(err):
    import hermes_mcp as M  # noqa: E402
assert M.ALLOW_PATH == ALLOW, M.ALLOW_PATH

print("--- the allowlist reads only a literal true as on")
check('tools.messages_search "false" is OFF', M.ALLOW["tools"]["messages_search"] is False,
      M.ALLOW["tools"])
check('tools.doctor "off" is OFF', M.ALLOW["tools"]["doctor"] is False, M.ALLOW["tools"])
for k in ("evals", "memory_facts", "prompt_budget", "tool_budget"):
    check("writes.%s %r is OFF" % (k, json.load(open(ALLOW))["writes"][k]),
          M.ALLOW["writes"][k] is False, M.ALLOW["writes"])
check("messages_search is not listed", "messages_search" not in M.BY_NAME, sorted(M.BY_NAME))
check("the evals schema has no run argument",
      "run" not in (M.BY_NAME["evals"]["inputSchema"].get("properties") or {}))
check("a non-boolean value is reported on stderr",
      "tools.messages_search" in err.getvalue() and "read as false" in err.getvalue(),
      err.getvalue())
check("an untouched default-on tool stays on", M.ALLOW["tools"]["hermes_search"] is True)

# the same rule, value by value, through load_allow() itself
for raw, want in (("false", False), ("true", False), ("off", False), ("0", False),
                  (1, False), (0, False), (None, False), ([], False), ({}, False),
                  (True, True), (False, False)):
    p = os.path.join(TMP, "a.json")
    with open(p, "w") as fh:
        json.dump({"tools": {"messages_search": raw}}, fh)
    saved = M.ALLOW_PATH
    M.ALLOW_PATH = p
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            got = M.load_allow()["tools"]["messages_search"]
    finally:
        M.ALLOW_PATH = saved
    check("load_allow: messages_search %r -> %r" % (raw, want), got is want, got)

# ---------------------------------------------------------------------------
print("--- needs_you leaves message items out while messages_search is off")
PAYLOAD = {
    "ok": True,
    "now": [{"id": "message:alex", "source": "message", "sender": "Alex Prentiss",
             "title": "Alex Prentiss", "summary": "can you send the signed lease by 5pm",
             "reason": "VIP asks by a deadline", "confidence": 0.9},
            {"id": "calendar:e1", "source": "calendar", "sender": "Dentist",
             "title": "Dentist", "summary": "Dentist at 3pm", "reason": "event in <2 h",
             "confidence": 0.9}],
    "today": [{"id": "message:sam", "source": "message", "sender": "Sam Okafor",
               "summary": "lunch thursday?", "reason": "wants a reply"},
              {"id": "approval:j1", "source": "approval", "sender": "Approval",
               "summary": "rm -rf build/", "reason": "an approval is waiting"}],
    "later": [{"id": "message:news", "source": "message", "sender": "Brand Mailer",
               "summary": "50% off this weekend"}],
    "today_count": 2, "later_count": 1, "never_count": 7,
    "sources": {"message": "present", "calendar": "present", "approval": "present"},
    "metrics": {"now_precision": 0.5},
}
SEARCH = {"ok": True, "q": "lease",
          "results": [{"source": "note", "title": "Lease checklist", "snippet": "sign it"},
                      {"source": "message", "title": "Alex Prentiss",
                       "snippet": "the signed lease by 5pm"}],
          "sources": {"message": 4, "note": 1}}


def fake_get(path, params=None, timeout=None):
    if path == "/api/needsyou":
        return json.loads(json.dumps(PAYLOAD))
    if path == "/api/search":
        return json.loads(json.dumps(SEARCH))
    raise AssertionError("unexpected route " + path)


M.dash_get = fake_get
out = M.t_needs_you({})
print("     | " + "\n     | ".join(out.splitlines()))
for leak in ("Alex Prentiss", "signed lease", "Sam Okafor", "lunch thursday",
             "Brand Mailer", "50% off", "[message]"):
    check("needs_you (off): %r is not in the result" % leak, leak not in out, out)
check("needs_you (off): the calendar item is still there", "Dentist at 3pm" in out, out)
check("needs_you (off): the approval item is still there", "rm -rf build/" in out, out)
check("needs_you (off): counts are the non-message rows",
      out.splitlines()[0] == "Needs you — now 1 · today 1 · later 0", out.splitlines()[0])
check("needs_you (off): the never count (mostly messages) is not shown",
      "never" not in out.splitlines()[0], out.splitlines()[0])
check("needs_you (off): says message items are left out",
      "messages_search is off" in out, out)
check("needs_you (off): the sources line has no message entry",
      not any(l.startswith("sources:") and "message" in l for l in out.splitlines()), out)

PAYLOAD["today_count"] = 52     # upstream capped the list at 40
out = M.t_needs_you({})
check("needs_you (off): a capped upstream list reads as 'at least'",
      "today 1+" in out.splitlines()[0], out.splitlines()[0])
PAYLOAD["today_count"] = 2

print("--- hermes_search drops message rows AND the message count")
out = M.t_hermes_search({"q": "lease"})
print("     | " + "\n     | ".join(out.splitlines()))
check("hermes_search (off): the message row is gone", "Alex Prentiss" not in out, out)
check("hermes_search (off): no 'message 4' per-source count", "message" not in out, out)
check("hermes_search (off): the note row and its count stay",
      "Lease checklist" in out and "note 1" in out, out)

print("--- control: with messages_search ON the same payloads show everything")
M.ALLOW["tools"]["messages_search"] = True
out = M.t_needs_you({})
check("needs_you (on): message items appear", "Alex Prentiss" in out and "Sam Okafor" in out,
      out)
check("needs_you (on): upstream counts incl. never",
      out.splitlines()[0] == "Needs you — now 2 · today 2 · later 1 · never 7",
      out.splitlines()[0])
out = M.t_hermes_search({"q": "lease"})
check("hermes_search (on): message row + count appear",
      "Alex Prentiss" in out and "message 4" in out, out)
M.ALLOW["tools"]["messages_search"] = False

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
