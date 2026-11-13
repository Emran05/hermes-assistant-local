#!/usr/bin/env python3
"""Regression test for C59 (docs/audits/2026-10-21-codebase-audit.md), for the
switches in aux_claudebridge.py.

BEFORE: POST /api/claude/escalate did `_cb_set_escalation(bool(b.get("enabled")))`,
so {"enabled": "false"} — a string, from any client that stringifies form
values — turned Claude escalation ON (bool("false") is True), and
{"enabled": null} / 0 / "" quietly turned it off.

AFTER: only a JSON boolean is accepted; anything else is a 400 and the stored
switch is untouched. The same rule covers For-You's own switch (C35).

Exec's the REAL module on a throwaway HOME; settings_update is a locked
read-modify-write of a temp settings.json, like server.py's.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-c59-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
SETTINGS = os.path.join(DATA, "settings.json")
LOCK = threading.Lock()


# The REAL server.py settings helpers (2026-11-09 audit E138; C84 pattern),
# bound to this suite's own settings file and lock — never a stand-in.
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402
_SETTINGS_API = server_real.settings_api(SETTINGS, LOCK)
settings_update = _SETTINGS_API["settings_update"]


ROUTES = {}
ns = {"__name__": "aux_harness", "HOME": HOME, "SETTINGS_FILE": SETTINGS,
      "settings_update": settings_update,
      "register_get": lambda p, f: ROUTES.__setitem__(("GET", p), f),
      "register_post": lambda p, f: ROUTES.__setitem__(("POST", p), f)}
path = os.path.join(REPO, "dashboard", "aux_claudebridge.py")
with open(path, encoding="utf-8") as f:
    exec(compile(f.read(), path, "exec"), ns)


def post(route, body):
    out = ROUTES[("POST", route)](types.SimpleNamespace(body=body))
    if isinstance(out, tuple):
        return out[0], out[1]
    return out, 200


def stored(key):
    try:
        return (json.load(open(SETTINGS)).get(key) or {}).get("enabled")
    except Exception:
        return None


for route, key in (("/api/claude/escalate", "claude_escalation"),
                   ("/api/claude/foryou", "foryou_claude")):
    print("== %s ==" % route)
    if ("POST", route) not in ROUTES:
        check("%s is registered" % route, False)
        continue
    json.dump({key: {"enabled": False}}, open(SETTINGS, "w"))
    body, code = post(route, {"enabled": "false"})
    check('{"enabled":"false"} is refused with 400 (was: switched ON)',
          code == 400 and body.get("ok") is False, (code, body))
    check("...and the switch is still off", stored(key) is False, stored(key))

    json.dump({key: {"enabled": True}}, open(SETTINGS, "w"))
    for bad in ("true", "", 0, 1, None, [], {}):
        body, code = post(route, {"enabled": bad})
        check("%r is refused with 400" % (bad,), code == 400, (code, body))
    check("...and none of them changed an ON switch", stored(key) is True, stored(key))

    body, code = post(route, {})
    check("a body without 'enabled' is refused (unchanged rule)", code == 400)
    body, code = post(route, None)
    check("a missing body is refused", code == 400)

    body, code = post(route, {"enabled": False})
    check("JSON false turns it off", code == 200 and body.get("enabled") is False
          and stored(key) is False, (code, body))
    body, code = post(route, {"enabled": True})
    check("JSON true turns it on", code == 200 and body.get("enabled") is True
          and stored(key) is True, (code, body))

shutil.rmtree(HOME, ignore_errors=True)
print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
