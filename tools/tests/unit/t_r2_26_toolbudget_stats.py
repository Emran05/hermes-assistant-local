#!/usr/bin/env python3
"""R2-26 — one bad row in tool-budget.jsonl no longer fails the whole card.

_tb_stats called int() on kept_chars / omitted_chars / est_tokens_saved with
no guard, so a single row carrying "n/a", a list, a dict, NaN or Infinity
raised inside GET /api/tool/budget and the card showed nothing at all.  Each
row is now coerced on its own and a row that is not numeric is skipped (and
counted in `rows_skipped`); the newest GOOD row is `last`.

Execs the REAL aux_toolbudget.py the way t_auxtoolbudget.py does, into a
throwaway HOME.  No serve, no model, no network.

Fails on the old code: GET answers a 500 ("could not be read — ValueError"),
and _tb_stats raises on the same rows.
"""
import json
import os
import shutil
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_toolbudget.py")
sys.path.insert(0, os.path.join(_HERE, "..", "lib"))
import server_real  # noqa: E402

SB = tempfile.mkdtemp(prefix="tb-r2-26-")
DATA = os.path.join(SB, ".hermes", "dashboard")
os.makedirs(DATA)
os.makedirs(os.path.join(SB, ".hermes", "plugins"))
SETTINGS = os.path.join(DATA, "settings.json")
json.dump({}, open(SETTINGS, "w"))
open(os.path.join(SB, ".hermes", "config.yaml"), "w").write(
    "model:\n  default: x\n  context_length: 65536\n")

G = {"__name__": "aux_toolbudget", "HOME": SB,
     "HERE": os.path.join(REPO, "dashboard"), "DATA": DATA,
     **server_real.settings_api(SETTINGS, threading.Lock()),
     "register_get": lambda p, f: None, "register_post": lambda p, f: None}
exec(compile(open(AUX).read(), AUX, "exec"), G)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + name)
    else:
        FAIL += 1
        print("  FAIL %s  — %r" % (name, detail))


class Ctx:
    def __init__(self, q=None):
        self.query = {k: [v] for k, v in (q or {}).items()}
        self.body = {}

    def q1(self, k, d=None):
        return (self.query.get(k) or [d])[0]


today = __import__("datetime").date.today().isoformat()


def row(t, tool, kept, omitted, est, spill=""):
    return json.dumps({"ts": "%sT%s" % (today, t), "tool": tool, "kept_chars": kept,
                       "omitted_chars": omitted, "est_tokens_saved": est,
                       "spill_path": spill, "session": "s"})


LOG = os.path.join(DATA, "tool-budget.jsonl")
try:
    lines = [
        row("09:00:00", "read_file", 6000, 35000, 9722, "/a"),
        row("09:00:30", "terminal", "n/a", 100, 10),          # bad: string
        row("09:00:40", "terminal", 10, [1, 2], 10),          # bad: list
        row("09:00:50", "terminal", 10, 20, {"x": 1}),        # bad: dict
        '{"ts":"%sT09:00:55","tool":"terminal","kept_chars":NaN,'
        '"omitted_chars":1,"est_tokens_saved":1}' % today,    # bad: NaN
        '{"ts":"%sT09:00:56","tool":"terminal","kept_chars":Infinity,'
        '"omitted_chars":1,"est_tokens_saved":1}' % today,    # bad: inf
        row("09:01:00", "terminal", 5900, "40000", None),     # numeric string, est derived
        row("09:02:00", "web_extract", 6000, 12000, 3333, "/c"),
        row("09:03:00", "terminal", True, 5, 5),              # bad: a bool is not a count
    ]
    open(LOG, "w").write("\n".join(lines) + "\n")

    p = G["_tb_get"](Ctx())
    code = p[1] if isinstance(p, tuple) else 200
    p = p[0] if isinstance(p, tuple) else p
    check("GET /api/tool/budget answers (not a 500)",
          code == 200 and isinstance(p.get("stats"), dict), (code, p))
    st = p.get("stats") or {}
    t = st.get("today") or {}
    check("only the three good rows are counted", t.get("calls") == 3, t)
    check("kept_chars summed over good rows", t.get("kept_chars") == 17900, t)
    check("a numeric string still counts", t.get("omitted_chars") == 87000, t)
    check("a missing est_tokens_saved is derived from omitted_chars",
          t.get("est_tokens_saved") == 9722 + round(40000 / 3.6) + 3333, t)
    check("spills counted on good rows only", t.get("spills") == 2, t)
    check("per-tool counts skip the bad rows",
          t.get("tools") == {"read_file": 1, "terminal": 1, "web_extract": 1}, t.get("tools"))
    check("the skipped rows are counted", st.get("rows_skipped") == 6, st.get("rows_skipped"))
    check("rows_seen still reports every parsed row", st.get("rows_seen") == 9, st.get("rows_seen"))
    last = st.get("last") or {}
    check("last is the newest GOOD row, not the bad one after it",
          last.get("tool") == "web_extract" and last.get("kept_chars") == 6000, last)

    print("\n=== a log of only bad rows ===")
    open(LOG, "w").write(row("09:00:00", "x", "?", 1, 1) + "\n")
    p = G["_tb_get"](Ctx())
    p = p[0] if isinstance(p, tuple) else p
    st = p.get("stats") or {}
    check("answers with zero calls and last=None",
          st.get("today", {}).get("calls") == 0 and st.get("last") is None, st)
finally:
    shutil.rmtree(SB, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
