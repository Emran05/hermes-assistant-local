#!/usr/bin/env python3
"""Seam S1-10 (audit 2026-11-09, the E97 trade-off) — needs_you says its age.

E97 made the needs-you inbox rebuild only while a reader came in the last
180 s. The MCP needs_you tool reads it rarely, so it often gets the last
payload, possibly hours old, while a rebuild starts — and printed it as if it
were current. The payload carries `generated_at` (when it was built); the
tool now prints one plain line ("As of 14:05, 52 minutes ago") when that is
more than 5 minutes old, in the text AND in structuredContent (`as_of`,
`age_minutes`, declared in the outputSchema — F21: schema and builder change
together), inside the one 8 KB bound _fit() applies to both.

Imports the REAL dashboard/hermes_mcp.py; only dash_get is stubbed. The
structure is checked with t_f21's own validator (ast-extracted). Offline.
"""
import ast
import contextlib
import datetime
import io
import json
import os
import re
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:500]))


vns = {}
_tree = ast.parse(open(os.path.join(_HERE, "t_f21_mcp_structured.py"), encoding="utf-8").read())
exec(compile(ast.Module(body=[n for n in _tree.body if (
    isinstance(n, ast.FunctionDef) and n.name == "validate") or (
    isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_KNOWN" for t in n.targets))],
    type_ignores=[]), "t_f21", "exec"), vns)
validate = vns["validate"]

TMP = tempfile.mkdtemp(prefix="hermes-s1-mcpage-")
os.environ["HERMES_MCP_ALLOW"] = os.path.join(TMP, "mcp-allow.json")
os.environ.setdefault("HERMES_MCP_DASHBOARD", "http://127.0.0.1:9")
sys.path.insert(0, DASH)
with contextlib.redirect_stderr(io.StringIO()):
    import hermes_mcp as M  # noqa: E402

SCHEMA = next(t for t in M.TOOLS if t["name"] == "needs_you")["outputSchema"]
BASE = {"ok": True, "today": [], "later": [], "today_count": 0, "later_count": 0,
        "never_count": 0, "sources": {"calendar": "present"}, "source_notes": {},
        "now": [{"source": "calendar", "sender": "Dentist", "summary": "Dentist at 3pm",
                 "reason": "event in <2 h"}]}
STATE = {}
M.dash_get = lambda path, params=None, timeout=None: json.loads(json.dumps(STATE["p"]))
M.ALLOW["tools"]["messages_search"] = True


def call(gen):
    STATE["p"] = dict(BASE, generated_at=gen)
    out = M.t_needs_you({})
    return str(out), getattr(out, "structured", None)


now = time.time()
print("== 52 minutes old ==")
gen = now - 52 * 60 - 5
text, st = call(gen)
ZONE = " UTC" + datetime.datetime.fromtimestamp(gen).astimezone().strftime("%z")
hhmm = datetime.datetime.fromtimestamp(gen).strftime("%H:%M") + ZONE
check("the text says 'As of HH:MM UTC+hhmm, 52 minutes ago'",
      ("As of %s, 52 minutes ago" % hhmm) in text, text)
check("...the zone is a real UTC offset, never missing",
      re.fullmatch(r" UTC[+-]\d{4}", ZONE) is not None, ZONE)
check("...near the top (before the items)", text.find("As of") < text.find("Dentist"), text)
check("structuredContent carries as_of and age_minutes",
      isinstance(st, dict) and st.get("as_of") == hhmm and st.get("age_minutes") == 52, st)
check("...and still validates against the declared outputSchema",
      validate(st, SCHEMA) == [], validate(st, SCHEMA))
check("the item is still listed", "Dentist at 3pm" in text, text)

print("\n== fresh (under 5 minutes) ==")
text, st = call(now - 4 * 60)
check("no age line", "As of" not in text, text)
check("no as_of in the structure", "as_of" not in st and "age_minutes" not in st, st)
check("validates", validate(st, SCHEMA) == [], validate(st, SCHEMA))

print("\n== hours old, a day old ==")
text, st = call(now - 3 * 3600 - 60)
check("3 hours: said in hours", "3 hours ago" in text and st.get("age_minutes") == 181,
      (text[:300], st))
gen = now - 26 * 3600
text, st = call(gen)
day = (datetime.datetime.fromtimestamp(gen).strftime("%Y-%m-%d %H:%M") + " UTC"
       + datetime.datetime.fromtimestamp(gen).astimezone().strftime("%z"))
check("over a day: the date is in as_of", st.get("as_of") == day and day in text, (text[:300], st))
check("validates", validate(st, SCHEMA) == [], validate(st, SCHEMA))

print("\n== the zone follows the Mac's clock (review R4-seams) ==")
if hasattr(time, "tzset"):
    _tz = os.environ.get("TZ")
    try:
        for tz, want in (("Asia/Kolkata", "UTC+0530"), ("UTC", "UTC+0000"),
                         ("America/St_Johns", None)):
            os.environ["TZ"] = tz
            time.tzset()
            gen = time.time() - 52 * 60
            text, st = call(gen)
            off = datetime.datetime.fromtimestamp(gen).astimezone().strftime("UTC%z")
            check("TZ=%s: as_of ends with %s" % (tz, want or off),
                  st.get("as_of", "").endswith(want or off)
                  and ("%s, 52 minutes ago" % (want or off)) in text, (st.get("as_of"), text[:120]))
            check("TZ=%s: validates" % tz, validate(st, SCHEMA) == [], validate(st, SCHEMA))
    finally:
        if _tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = _tz
        time.tzset()

print("\n== no or junk generated_at ==")
for g in (None, "soon", -5, now + 3600, float("nan")):
    text, st = call(g)
    check("generated_at=%r: no age line, no crash" % (g,),
          "As of" not in text and "as_of" not in (st or {}), text[:200])

print("\n== the one 8 KB bound still holds with the line ==")
STATE_ITEMS = [{"source": "calendar", "sender": "S%03d" % i, "summary": "x" * 180,
                "reason": "r" * 150} for i in range(200)]
BASE["now"] = STATE_ITEMS
text, st = call(now - 52 * 60)
check("text <= 8 KB", len(text.encode("utf-8")) <= M.MAX_TEXT, len(text.encode("utf-8")))
check("structure <= its bound", len(json.dumps(st, ensure_ascii=False).encode("utf-8"))
      <= M.MAX_STRUCT)
check("the age line survived the cut", "52 minutes ago" in text and st.get("age_minutes") == 52)
check("validates", validate(st, SCHEMA) == [], validate(st, SCHEMA))

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
