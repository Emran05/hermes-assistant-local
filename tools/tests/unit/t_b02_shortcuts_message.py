#!/usr/bin/env python3
"""B02, the Shortcuts part (audit 2026-10-08): the bus's approval reply no
longer promises something the code does not enforce.

POST /api/shortcuts/run answered an ask-tier request with the single-use
ticket AND the sentence "It cannot be approved by the requester." — while
redeeming {ticket, approved: true} works from any caller, the requester
included. The real fix (an app-held nonce the agent cannot read, F05) is out
of this change's scope; this one makes the message true.

Lifts the REAL shortcuts_run_handler / _sb_redeem / _sb_finish_run out of
aux_shortcuts.py by AST and runs them over stubs (no `shortcuts` binary, no
permissions file, nothing spawned). It first SHOWS that the requester can
redeem its own ticket, then checks the reply does not deny it.

Run:  python3 t_b02_shortcuts_message.py
"""
import ast
import os
import secrets
import sys
import threading
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    return os.environ.get("HERMES_REPO") or os.path.dirname(
        os.path.dirname(os.path.dirname(_HERE)))


SRC = os.path.join(_find_repo(), "dashboard", "aux_shortcuts.py")
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %s" % (name, extra))


WANT = {"shortcuts_run_handler", "_sb_redeem", "_sb_finish_run"}
tree = ast.parse(open(SRC).read(), filename=SRC)
body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in WANT]
if {n.name for n in body} != WANT:
    print("SKIP: aux_shortcuts.py no longer defines %s" % sorted(WANT))
    sys.exit(0)

ran = []
NS = {
    "time": time, "secrets": secrets, "SB_CLASS": "shortcuts-run",
    "SB_TICKET_TTL": 300, "SB_IN_CAP": 16384, "SB_REC_CAP": 2000,
    "_SB_LOCK": threading.Lock(), "_SB_TICKETS": {},
    "_sb_sweep_tickets": lambda now: None,
    "_sb_resolve": lambda b: ({"id": "U-1", "name": "Morning"}, None),
    "_sb_load_config": lambda: {"exposed": {"U-1": True}},
    "_sb_perm": types.SimpleNamespace(
        decide=lambda p: {"tier": "ask", "class": "shortcuts-run", "reason": "ask"}),
    "_sb_audit": lambda *a, **k: None, "_sb_record": lambda *a, **k: None,
    "_sb_metric": lambda **k: None,
    "recorder_record_local": lambda *a, **k: {"ok": True},
    "_sb_execute": lambda sid, inp: (ran.append(sid) or
                                     {"outcome": "done", "name": "Morning", "rc": 0,
                                      "output": "", "error": "", "duration_s": 0.1}),
}
exec(compile(ast.Module(body=body, type_ignores=[]), SRC, "exec"), NS)


class Ctx:
    def __init__(self, body):
        self.body = body


print("\n== what the code allows ==")
ask = NS["shortcuts_run_handler"](Ctx({"name": "Morning", "source": "agent"}))
check("an ask-tier run hands the ticket back to the requester",
      ask.get("needs_approval") is True and bool(ask.get("ticket")), ask)
red = NS["shortcuts_run_handler"](Ctx({"ticket": ask.get("ticket"), "approved": True,
                                       "source": "agent"}))
check("…and the SAME requester can redeem it (this is not enforced today)",
      red.get("ok") is True and ran == ["U-1"], (red, ran))

print("\n== what the reply says ==")
msg = (ask.get("message") or "")
check("the reply no longer claims the requester cannot approve it",
      "cannot be approved by the requester" not in msg, msg)
check("it still says the user must confirm in the dashboard",
      "user must confirm" in msg and "Shortcuts" in msg, msg)
check("and tells the requester not to redeem the ticket itself",
      "do not redeem" in msg, msg)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
