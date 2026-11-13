#!/usr/bin/env python3
"""Seam S1-7 (audit 2026-11-09, E85 reader half) — "Last outbound" for updates.

aux_update now records every check that REACHED GitHub / origin as
`attempted_at` + `attempt_signed` beside `checked_at` (the last GOOD answer)
in update-check.json. aux_network._net_last() still read only `checked_at`,
so a check that got a 401/403/404/5xx left "Last outbound" frozen. It now
reads max(checked_at, attempted_at), and when the attempt is the newer one,
whether it signed in comes from `attempt_signed`.

Also pins that the route does not claim the new `agent_tools` row (the card
then shows "not tracked" for it, not a broken cell).

The REAL dashboard/aux_network.py exec'd on a throwaway HOME; no network.
"""
import json
import os
import sys
import tempfile
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SRC = os.path.join(REPO, "dashboard", "aux_network.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def _no_net(*a, **k):
    raise AssertionError("aux_network made a network call")


urllib.request.urlopen = _no_net
HOME = tempfile.mkdtemp(prefix="hermes-s1-net-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
ROUTES = {}
ns = {"__name__": "aux_network", "HOME": HOME, "DATA": DATA,
      "register_get": lambda p, fn: ROUTES.__setitem__(p, fn),
      "register_post": lambda p, fn: None}
exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)


def last(upd):
    with open(os.path.join(DATA, "update-check.json"), "w") as f:
        json.dump(upd, f)
    return ROUTES["/api/network/last"](None)


GOOD_ANON = {"source": "github", "checked_at": 1000.0}
GOOD_TOKEN = {"source": "github-token", "checked_at": 1000.0}

print("== a failed attempt after the last good answer ==")
r = last({"checked_at": 1000.0, "payload": GOOD_ANON,
          "attempted_at": 5000.0, "attempt_signed": True})
check("Updates = the newer attempt (5000), not the frozen good answer",
      r["last"].get("updates") == 5000.0, r["last"])
check("...signed in comes from attempt_signed", r.get("updates_signed") is True, r)
r = last({"checked_at": 1000.0, "payload": GOOD_TOKEN,
          "attempted_at": 5000.0, "attempt_signed": False})
check("an anonymous newer attempt is not 'signed in' though the good answer was",
      r["last"].get("updates") == 5000.0 and r.get("updates_signed") is False, r)

print("== the good answer is the newer one ==")
r = last({"checked_at": 6000.0, "payload": GOOD_TOKEN,
          "attempted_at": 5000.0, "attempt_signed": False})
check("Updates = checked_at", r["last"].get("updates") == 6000.0, r["last"])
check("...signed from the payload's source", r.get("updates_signed") is True, r)

print("== only one of them, or junk ==")
r = last({"attempted_at": 5000.0, "attempt_signed": True})
check("no good answer yet, an attempt: its time and its signed flag",
      r["last"].get("updates") == 5000.0 and r.get("updates_signed") is True, r)
r = last({"checked_at": 1000.0, "payload": GOOD_ANON})
check("no attempt recorded: checked_at as before",
      r["last"].get("updates") == 1000.0 and r.get("updates_signed") is False, r)
r = last({"checked_at": 1000.0, "payload": GOOD_ANON,
          "attempted_at": "soon", "attempt_signed": "yes"})
check("a junk attempted_at is ignored", r["last"].get("updates") == 1000.0
      and r.get("updates_signed") is False, r)
r = last({"checked_at": 1000.0, "payload": GOOD_ANON,
          "attempted_at": 5000.0, "attempt_signed": "true"})
check("only a real boolean attempt_signed counts as signed",
      r["last"].get("updates") == 5000.0 and r.get("updates_signed") is False, r)
r = last({})
check("nothing at all: no record, not signed",
      r["last"].get("updates") is None and r.get("updates_signed") is False, r)

print("== the agent_tools row ==")
check("agent_tools is not claimed as tracked (the card shows 'not tracked')",
      "agent_tools" not in r["last"], r["last"])

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
