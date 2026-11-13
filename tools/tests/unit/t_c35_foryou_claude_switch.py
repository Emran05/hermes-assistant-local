#!/usr/bin/env python3
"""Regression test for C35 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: with Claude escalation on, the unattended For-You loop (about hourly)
preferred the Claude Bridge and called
claude_think(task, user_context=_fy_profile_text(prof)) — the You-Model
profile (goals, projects, looking-for, interests, preferences, key people's
names) plus 15 feed headlines went to Anthropic with no question from the
owner. The only gate was the master switch, which the UI describes as "that
one question".

AFTER: For-You's Claude tier has its own switch, settings.json
`foryou_claude.enabled` — default off, explicit JSON true only, fail closed —
checked in aux_foryou BEFORE any profile text is built, and again at the
choke point (claude_think(caller="foryou")). Both switches must be on.

The REAL aux_claudebridge.py and aux_foryou.py are exec'd into one namespace
the way server.py's aux loader does it, on a throwaway HOME. claude -p is never
run: subprocess.run is replaced by a recorder.
"""
import json
import os
import shutil
import subprocess
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


HOME = tempfile.mkdtemp(prefix="hermes-c35-")
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


def write_settings(obj):
    with open(SETTINGS, "w") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


ROUTES = {}
ns = {
    "__name__": "aux_harness", "HOME": HOME, "DATA": DATA,
    "SETTINGS_FILE": SETTINGS, "read_json": read_json,
    "settings_update": settings_update,
    "register_get": lambda p, f: ROUTES.__setitem__(("GET", p), f),
    "register_post": lambda p, f: ROUTES.__setitem__(("POST", p), f),
    "WIDGETS": {}, "EXPANDERS": {},
    "get_layout": lambda: {"order": ["foryou"]}, "save_layout": lambda lay: None,
    "mlx_admission": lambda: (False, 0, 0),      # local model tier unavailable
    "model_online": lambda: False, "agent_paused": lambda: False,
    "CHAT_JOBS": {}, "MODEL_URL": "http://127.0.0.1:9/v1/models",
    "ACTIVE_MODEL_FILE": os.path.join(DATA, "active-model"),
    "DEFAULT_MODEL": "none",
    "_foryou_thread_started": True,              # never start the daemon
    # aux_memlayer's credential test (C44); a pass-through here — the test
    # itself is exercised for real in t_c44_foryou_credentials.py
    "_ml_looks_secret": lambda text: False,
}
for rel in ("dashboard/aux_claudebridge.py", "dashboard/aux_foryou.py"):
    path = os.path.join(REPO, rel)
    with open(path, encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"), ns)

# a fake claude binary + the bridge prompt, so claude_think reaches its argv
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

# a You-Model with a recognisable private goal, and intel that matches it
SECRET_GOAL = "Raise a seed round for Zephyrine Labs before March"
with open(os.path.join(MEM, "GOALS.md"), "w") as f:
    f.write(SECRET_GOAL + "\n§\nLearn zephyrine robotics deeply")
with open(os.path.join(MEM, "people", "dana-quill.md"), "w") as f:
    f.write("# Dana Quill\nInvestor")
now = __import__("time").time()
with open(os.path.join(DATA, "intel.json"), "w") as f:
    json.dump({"items": [
        {"title": "Zephyrine robotics startup raises seed round", "url":
         "https://example.org/a%d" % i, "source": "r/LocalLLaMA",
         "topic": "AI", "ts": now, "summary": "zephyrine seed round robotics"}
        for i in range(6)]}, f)

# spy on profile-text construction: the switch must be honoured BEFORE it
PROFILE_BUILDS = []
_real_profile_text = ns["_fy_profile_text"]


def spy_profile_text(prof, *a, **k):
    PROFILE_BUILDS.append(1)
    return _real_profile_text(prof, *a, **k)


ns["_fy_profile_text"] = spy_profile_text

# claude_think spy (wraps the REAL one, so the choke point is exercised too)
THINK = []
_real_think = ns["claude_think"]


def spy_think(task, user_context="", depth="quick", **kw):
    THINK.append({"task": task, "context": user_context, "kw": kw})
    return _real_think(task, user_context=user_context, depth=depth, **kw)


ns["claude_think"] = spy_think


def reset():
    PROFILE_BUILDS.clear()
    THINK.clear()
    RUNS.clear()
    try:
        os.remove(os.path.join(DATA, "foryou.json"))
    except OSError:
        pass


print("== the switch itself ==")
fy = ns["foryou_claude_enabled"]
check("a missing settings.json reads OFF", fy() is False)
write_settings("{broken")
check("a corrupt settings.json reads OFF (fail closed)", fy() is False)
write_settings({"foryou_claude": {"enabled": "true"}})
check('the string "true" is not accepted', fy() is False)
write_settings({"foryou_claude": {"enabled": 1}})
check("the integer 1 is not accepted", fy() is False)
write_settings({"foryou_claude": {"enabled": True}})
check("an explicit JSON true reads ON", fy() is True)
write_settings({"claude_escalation": {"enabled": True}, "tickers": ["X"]})
check("the setter returns the new state", ns["_cb_set_foryou_claude"](True) is True)
s = read_json(SETTINGS, {})
check("the setter writes only foryou_claude (through settings_update)",
      s.get("foryou_claude") == {"enabled": True}
      and s.get("claude_escalation") == {"enabled": True}
      and s.get("tickers") == ["X"], s)
check("GET /api/claude/foryou is registered", ("GET", "/api/claude/foryou") in ROUTES)
check("POST /api/claude/foryou is registered", ("POST", "/api/claude/foryou") in ROUTES)
g = ROUTES[("GET", "/api/claude/foryou")](types.SimpleNamespace(body=None))
check("GET reports both switches", g.get("enabled") is True and g.get("escalation") is True, g)

print("== THE AUDIT CASE: escalation ON, For-You's own switch never touched ==")
reset()
write_settings({"claude_escalation": {"enabled": True}})
store = ns["_foryou_build"]()
check("the For-You build still ran and stored moves", bool(store.get("moves")), store)
check("NO profile text was built for Claude (was: built and sent)",
      not PROFILE_BUILDS, len(PROFILE_BUILDS))
check("claude_think was never called (was: called with the profile)", not THINK,
      [t["context"][:80] for t in THINK])
check("no claude -p child was started", not RUNS)
check("the build fell through to the local/lexical tier",
      store.get("engine") != "claude", store.get("engine"))

print("== For-You ON but the master switch OFF ==")
reset()
write_settings({"foryou_claude": {"enabled": True},
                "claude_escalation": {"enabled": False}})
ns["_foryou_build"]()
check("still no profile text and no Claude call", not PROFILE_BUILDS and not THINK)
check("still no claude -p child", not RUNS)

print("== the choke point: a caller that skips aux_foryou's own check ==")
reset()
write_settings({"claude_escalation": {"enabled": True}})
r = _real_think("pick items", user_context="GOALS: x", caller="foryou")
check("claude_think(caller='foryou') refuses while For-You's switch is off",
      r.get("refused") is True and r.get("reason") == "foryou_off", r)
check("...without starting claude -p", not RUNS)
r2 = _real_think("Is this a good idea?", user_context="")
check("an ordinary escalation (no caller) is unaffected", r2.get("ok") is True
      and len(RUNS) == 1, r2)

print("== both switches ON: the documented, opted-in path still works ==")
reset()
write_settings({"claude_escalation": {"enabled": True},
                "foryou_claude": {"enabled": True}})
ns["_foryou_build"]()
check("the profile is built exactly for the Claude call", len(PROFILE_BUILDS) >= 1)
check("claude_think was called with caller='foryou'",
      len(THINK) == 1 and THINK[0]["kw"].get("caller") == "foryou",
      [t["kw"] for t in THINK])
check("...and the profile rides in the context (what the switch discloses)",
      bool(THINK) and SECRET_GOAL in THINK[0]["context"])
check("exactly one claude -p child", len(RUNS) == 1)
log = os.path.join(DATA, "claude-bridge-log.jsonl")
rows = [json.loads(ln) for ln in open(log) if ln.strip()] if os.path.exists(log) else []
check("the usage log records the call as caller=foryou (Last outbound reads it)",
      bool(rows) and rows[-1].get("caller") == "foryou", rows[-1:] if rows else rows)

print("== a missing bridge module reads as OFF ==")
reset()
saved = ns.pop("foryou_claude_enabled")
try:
    check("_fy_claude_allowed() is False without the helper",
          ns["_fy_claude_allowed"]() is False)
finally:
    ns["foryou_claude_enabled"] = saved
ns["foryou_claude_enabled"] = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
check("a raising helper reads as OFF", ns["_fy_claude_allowed"]() is False)
ns["foryou_claude_enabled"] = saved

subprocess.run = _real_run
shutil.rmtree(HOME, ignore_errors=True)
print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
