#!/usr/bin/env python3
"""E76 (docs/audits/2026-11-09-codebase-audit.md): the morning brief's
"Look-ahead > Later today" read only `^(\\d{1,2}):(\\d{2})` from icalBuddy's
time column, ignoring AM/PM, so on a 12-hour clock "2:00 PM" read as 02:00
and every afternoon/evening event was dropped as already past.

AFTER: the 12-hour-first parsing Needs-you already uses (_ny_clocks) —
resolved by name at call time, with a local copy as the fallback when
aux_needsyou is not loaded (this harness loads only aux_watchtower).

The REAL aux_watchtower.py through _wt_harness; the clock is faked by
replacing time.localtime around the call (time-zone independent).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H                                         # noqa: E402

g, home, data = H.load()
EVENTS = [
    {"time": "12:15 AM - 12:45 AM", "title": "Night call"},
    {"time": "7:00 AM - 7:30 AM", "title": "Run"},
    {"time": "9:30 AM - 10:00 AM", "title": "Standup"},
    {"time": "12:30 PM - 1:00 PM", "title": "Lunch"},
    {"time": "2:00 PM - 3:00 PM", "title": "Dentist"},
    {"time": "6:30 PM - 8:00 PM", "title": "Dinner with Sam"},
]
g["macos_calendar"] = lambda: {"available": True, "events": list(EVENTS)}
g["weather"] = lambda: {}


def at(hour, minute, fn):
    real = time.localtime
    lt = real()
    fake = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0,
                        0, 0, -1))
    time.localtime = lambda t=None: real(fake if t is None else t)
    try:
        return fn()
    finally:
        time.localtime = real


def text(sec):
    return "\n".join(sec.get("lines") or [])


def run(label):
    s = text(at(8, 0, g["_brief_lookahead"]))
    for t in ("Standup", "Lunch", "Dentist", "Dinner with Sam"):
        H.check("%s: at 8 AM '%s' is later today" % (label, t), t in s, s)
    for t in ("Night call", "Run"):
        H.check("%s: at 8 AM '%s' is past" % (label, t), t not in s, s)
    s = text(at(13, 30, g["_brief_lookahead"]))
    H.check("%s: at 1:30 PM Lunch is past" % label, "Lunch" not in s, s)
    H.check("%s: at 1:30 PM Dentist and Dinner remain" % label,
            "Dentist" in s and "Dinner with Sam" in s, s)
    H.check("%s: 12:15 AM is just after midnight, not noon" % label,
            "Night call" not in s, s)


print("=== E76: fallback parser (aux_needsyou not loaded) ===")
g.pop("_ny_clocks", None)
run("fallback")

print("=== E76: Needs-you's _ny_clocks when it is loaded ===")
calls = []


def fake_clocks(s, _real=None):
    calls.append(s)
    return g["_wt_clocks_local"](s)


g["_ny_clocks"] = fake_clocks
run("shared")
H.check("the shared parser was used", len(calls) > 0, calls)

print("=== E76: a 24-hour locale still works ===")
g.pop("_ny_clocks", None)
g["macos_calendar"] = lambda: {"available": True, "events": [
    {"time": "07:00 - 07:30", "title": "Run"},
    {"time": "14:00 - 15:00", "title": "Dentist"}]}
s = text(at(8, 0, g["_brief_lookahead"]))
H.check("24h: 14:00 later, 07:00 past", "Dentist" in s and "Run" not in s, s)

print("=== E76: the fallback copy matches Needs-you's real _ny_clocks ===")
import _ny_env as NE                                            # noqa: E402
ng, _nd = NE.load()
SAMPLES = ["9:30 AM - 10:00 AM", "2:00 PM - 3:00 PM", "12:15 AM", "12:30 pm",
           "7 p.m.", "14:00 - 15:00", "07:00", "25:99", "", "all-day",
           "10:30 PM - 11:30 PM", "8am", "6:30PM-8:00PM", "99:00 AM"]
for s in SAMPLES:
    H.check("same answer for %r" % s,
            ng["_ny_clocks"](s) == g["_wt_clocks_local"](s),
            (ng["_ny_clocks"](s), g["_wt_clocks_local"](s)))

H.finish()
