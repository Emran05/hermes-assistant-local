#!/usr/bin/env python3
"""Regression test for C44 (docs/audits/2026-10-21-codebase-audit.md), the part
this branch fixes.

BEFORE: For-You read the You-Model files (GOALS.md, NOW.md, ..., people/*.md)
straight off disk. Those files are model-writable (POST /api/youmodel/add, the
agent's file tool) and aux_memlayer holds them for review and refuses any
entry that looks like a credential — but For-You did neither, so a key the
model wrote into GOALS.md went into the profile, the brief's For-You lines
and, with both Claude switches on, to Claude.

AFTER: For-You runs the SAME credential test aux_memlayer uses
(_ml_looks_secret over aux_convos' regexes, resolved at call time) on every
entry and person name, withholds what it flags, and refuses the Claude tier
for a profile it could not check. (The review HOLD itself is not honoured
here — see the report.)

Real code: aux_convos.py (exec'd), _ml_looks_secret and its helpers extracted
from aux_memlayer.py by AST, aux_claudebridge.py + aux_foryou.py exec'd the way
the aux loader does it. `claude -p` is never run.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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


HOME = tempfile.mkdtemp(prefix="hermes-c44-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
MEM = os.path.join(HOME, ".hermes", "memories")
os.makedirs(DATA)
os.makedirs(os.path.join(MEM, "people"))
SETTINGS = os.path.join(DATA, "settings.json")
LOCK = threading.Lock()


# The REAL server.py settings helpers (2026-11-09 audit E138; C84 pattern),
# bound to this suite's own settings file and lock — never a stand-in.
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402
_SETTINGS_API = server_real.settings_api(SETTINGS, LOCK)
settings_update = _SETTINGS_API["settings_update"]


def read_json(path, default=None):
    return _SETTINGS_API["read_json"](path, default)


ns = {
    "__name__": "aux_harness", "HOME": HOME, "DATA": DATA,
    "SETTINGS_FILE": SETTINGS, "read_json": read_json,
    "settings_update": settings_update,
    "register_get": lambda p, f: None, "register_post": lambda p, f: None,
    "WIDGETS": {}, "EXPANDERS": {},
    "get_layout": lambda: {"order": ["foryou"]}, "save_layout": lambda lay: None,
    "mlx_admission": lambda: (False, 0, 0), "model_online": lambda: False,
    "agent_paused": lambda: False, "CHAT_JOBS": {},
    "MODEL_URL": "http://127.0.0.1:9/v1/models",
    "ACTIVE_MODEL_FILE": os.path.join(DATA, "active-model"), "DEFAULT_MODEL": "none",
    "_foryou_thread_started": True,
    "re": re, "os": os, "json": json, "time": time, "threading": threading, "sys": sys,
}


def exec_file(rel):
    path = os.path.join(REPO, rel)
    with open(path, encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"), ns)


# the credential test, exactly as aux_memlayer defines it
exec_file("dashboard/aux_convos.py")
ML = os.path.join(REPO, "dashboard", "aux_memlayer.py")
tree = ast.parse(open(ML, encoding="utf-8").read(), ML)
WANT = {"_ml_log", "_ml_g", "_MlNoScrubber", "_ml_scrubber", "_ml_looks_secret"}
picked = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
          and n.name in WANT]
ns["_ml_state"] = {"ready": False, "error": "", "logged": set()}
exec(compile(ast.Module(body=picked, type_ignores=[]), ML, "exec"), ns)
check("aux_memlayer's credential test is loaded", callable(ns.get("_ml_looks_secret")))
REAL_LOOKS = ns["_ml_looks_secret"]

exec_file("dashboard/aux_claudebridge.py")
exec_file("dashboard/aux_foryou.py")

fake_claude = os.path.join(HOME, "claude")
with open(fake_claude, "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(fake_claude, 0o755)
ns["_CB_BIN_CACHE"]["path"] = fake_claude
with open(os.path.join(HOME, ".hermes", "claude-bridge-prompt.md"), "w") as f:
    f.write("persona")
RUNS = []
_real_run = subprocess.run


def fake_run(argv, **kw):
    if argv and argv[0] == fake_claude:
        RUNS.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="[]", stderr="")
    return _real_run(argv, **kw)


subprocess.run = fake_run
THINK = []
_real_think = ns["claude_think"]


def spy_think(task, user_context="", depth="quick", **kw):
    THINK.append(user_context)
    return _real_think(task, user_context=user_context, depth=depth, **kw)


ns["claude_think"] = spy_think

KEY = "sk-ant-api03-" + "Q" * 40
GOAL = "Raise a seed round for Zephyrine Labs before March"
with open(os.path.join(MEM, "GOALS.md"), "w") as f:
    f.write(GOAL + "\n§\nRemember the deploy key " + KEY + "\n§\nLearn zephyrine robotics")
with open(os.path.join(MEM, "people", "dana-quill.md"), "w") as f:
    f.write("# Dana Quill\nInvestor")
with open(os.path.join(MEM, "people", "x.md"), "w") as f:
    f.write("# token: ghp_" + "a" * 36 + "\n")
now = time.time()
with open(os.path.join(DATA, "intel.json"), "w") as f:
    json.dump({"items": [
        {"title": "Zephyrine robotics startup raises seed round",
         "url": "https://example.org/a%d" % i, "source": "r/LocalLLaMA",
         "topic": "AI", "ts": now, "summary": "zephyrine seed round robotics"}
        for i in range(6)]}, f)
with open(SETTINGS, "w") as f:
    json.dump({"claude_escalation": {"enabled": True},
               "foryou_claude": {"enabled": True}}, f)


def reset():
    THINK.clear()
    RUNS.clear()
    try:
        os.remove(os.path.join(DATA, "foryou.json"))
    except OSError:
        pass


print("== the profile ==")
prof = ns["_fy_profile"]()
flat = json.dumps(prof)
check("a credential written into GOALS.md is withheld from the profile",
      KEY not in flat, [e for e in prof["entries"].get("goal", [])])
check("...and a credential-shaped people card name too", "ghp_" not in flat.lower(),
      prof["people"])
check("the owner's real goal is kept", any(GOAL in e for e in prof["entries"]["goal"]))
check("...and the real person", "Dana Quill" in prof["people"], prof["people"])
check("the profile says it was checked", prof.get("scanned") is True, prof.get("scanned"))
check("the profile TEXT carries no credential", KEY not in ns["_fy_profile_text"](prof))

print("== what reaches Claude (both switches on) ==")
reset()
store = ns["_foryou_build"]()
check("the Claude tier ran", len(THINK) == 1, len(THINK))
check("...and the context it sent has the goal but not the key",
      bool(THINK) and GOAL in THINK[0] and KEY not in THINK[0],
      THINK[0][:300] if THINK else "")
check("nothing in the stored For-You moves carries the key",
      KEY not in json.dumps(store))

print("== a profile that could not be checked does not leave the Mac ==")
reset()
ns.pop("_ml_looks_secret")
store = ns["_foryou_build"]()
check("no credential test loaded: Claude is not asked", not THINK and not RUNS,
      len(THINK))
check("...and For-You still builds from the local tiers", bool(store.get("moves")),
      store.get("note"))
reset()


def broken(text):
    raise ns["_MlNoScrubber"]("the credential check failed")


ns["_ml_looks_secret"] = broken
ns["_foryou_build"]()
check("a credential test that raises: Claude is not asked either", not THINK)
ns["_ml_looks_secret"] = REAL_LOOKS

subprocess.run = _real_run
shutil.rmtree(HOME, ignore_errors=True)
print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
