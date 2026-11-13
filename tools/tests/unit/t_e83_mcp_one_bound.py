#!/usr/bin/env python3
"""E83 — an MCP result's text and structuredContent come from the same capped
items, under one 8 KB bound.

2026-11-09 audit E83: hermes_search / calendar_next / needs_you cut their text
at 8 KB (MAX_TEXT, mid-list) but dropped structured items only past 16 KB
(MAX_STRUCT), so a structured client got up to twice the cap AND a different
item list from the text (reproduced: text listing 7 items, structuredContent
12).  Now one fitting pass picks how many items are shown; both channels
carry exactly those items, both stay within MAX_TEXT, and the text is never
cut by the 8 KB backstop.

The REAL dashboard/hermes_mcp.py in process, dash_get stubbed.  Offline.
"""
import contextlib
import datetime
import io
import json
import os
import re
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


TMP = tempfile.mkdtemp(prefix="hermes-e83-")
ALLOW = os.path.join(TMP, "mcp-allow.json")
with open(ALLOW, "w") as fh:
    json.dump({"tools": {"messages_search": True}, "max_results": 50}, fh)
os.environ["HERMES_MCP_ALLOW"] = ALLOW
os.environ["HERMES_MCP_DASHBOARD"] = "http://127.0.0.1:9"
sys.path.insert(0, DASH)
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402
    M.do_initialize({"protocolVersion": "2025-06-18"})

STATE = {}
M.dash_get = lambda path, params=None, timeout=None: json.loads(json.dumps(STATE[path]))
CJK = "東京の会議の議事録と次の打ち合わせ"            # multi-byte, 3 bytes a char


def size(obj):
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def call(tool, args):
    with contextlib.redirect_stderr(io.StringIO()):
        r = M.do_tools_call({"name": tool, "arguments": args})
    return r["content"][0]["text"], r.get("structuredContent"), r


def common(tag, text, sc, shown_text, shown_struct):
    tb = len(text.encode("utf-8"))
    check(tag + ": text within MAX_TEXT (%d bytes)" % tb, tb <= M.MAX_TEXT)
    check(tag + ": text was not cut by the backstop", "[truncated" not in text, text[-120:])
    sb = size(sc) if isinstance(sc, dict) else -1
    check(tag + ": structuredContent within MAX_TEXT (%d bytes)" % sb, 0 < sb <= M.MAX_TEXT)
    check(tag + ": the two channels list the same items (%d text / %d structured)"
          % (len(shown_text), len(shown_struct)), shown_text == shown_struct,
          (shown_text[:3], shown_struct[:3]))
    check(tag + ": something is still shown", len(shown_struct) > 0)


print("--- hermes_search, 40 multi-byte hits")
STATE["/api/search"] = {"ok": True, "sources": {"note": 40}, "results": [
    {"source": "note", "title": "%02d %s" % (i, CJK * 2), "ts": 1.79e9,
     "snippet": CJK * 6, "ref": ""} for i in range(40)]}
text, sc, _ = call("hermes_search", {"q": "会議", "limit": 50})
tl = re.findall(r"^\d+\. \[note\] (.+?)(?: +· .*)?$", text, re.M)
common("search", text, sc, tl, [x["title"] for x in sc["results"]])
check("search: count says what is shown", sc["count"] == len(sc["results"]), sc["count"])
check("search: truncated is flagged", sc.get("truncated") is True)
check("search: the text says only some are shown",
      "40 results" in text and ("first %d" % len(tl)) in text, text[:200])

print("--- hermes_search, small answer is untouched")
STATE["/api/search"] = {"ok": True, "sources": {"note": 2}, "results": [
    {"source": "note", "title": "a", "snippet": "s"}, {"source": "note", "title": "b"}]}
text, sc, _ = call("hermes_search", {"q": "x"})
check("small search: both items, no truncation",
      [x["title"] for x in sc["results"]] == ["a", "b"] and "truncated" not in sc
      and "first" not in text.split("\n", 1)[0], (text[:200], sc))

print("--- calendar_next, 300 all-day events")
TODAY = datetime.date.today().isoformat()
STATE["/api/expand"] = {"available": True, "days": [{"date": TODAY, "events": [
    {"title": "%03d %s" % (i, CJK * 3), "all_day": True} for i in range(300)]}]}
text, sc, _ = call("calendar_next", {"hours": 24})
tl = re.findall(r"^- .+? · all day · (.+?)$", text, re.M)
common("calendar", text, sc, tl, [x["title"] for x in sc["events"]])
check("calendar: truncated is flagged", sc.get("truncated") is True)
check("calendar: count says what is shown", sc["count"] == len(sc["events"]))

print("--- needs_you, 40 multi-byte items per bucket")
row = lambda src, i: {"source": src, "sender": "%s%02d %s" % (src[:3], i, CJK),
                      "summary": CJK * 3, "reason": CJK * 2, "confidence": 0.7}
STATE["/api/needsyou"] = {"ok": True, "now": [row("calendar", i) for i in range(5)],
                          "today": [row("watchtower", i) for i in range(40)],
                          "later": [row("reminder", i) for i in range(40)],
                          "today_count": 40, "later_count": 40, "never_count": 1}
text, sc, _ = call("needs_you", {})
tl = re.findall(r"^- \[\w+\] (.+?) — ", text, re.M)
sl = [x["who"] for b in ("now", "today", "later") for x in sc[b]]
common("needs_you", text, sc, tl, sl)
check("needs_you: the most urgent bucket is kept whole", len(sc["now"]) == 5, len(sc["now"]))
check("needs_you: truncated is flagged", sc.get("truncated") is True)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
