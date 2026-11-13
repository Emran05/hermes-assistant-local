#!/usr/bin/env python3
"""E86 — one headline no longer refuses For-You's whole Claude pass.

2026-11-09 audit E86: _fy_claude_moves put every candidate headline into the
bridge TASK, and _cb_gate judges a task for code-generation and harm wording,
so ONE feed title such as "Show HN: Build a CLI that …" (codegen) or "…share
your banking password…" (harm) refused the entire batch and For-You silently
fell back to the local model.  Now each candidate row is gated on its own and
an offending one is dropped; the rest still go to Claude, and the full gate in
claude_think still runs over what is sent.

REAL aux_claudebridge.py + aux_foryou.py exec'd the way server.py's aux loader
does, on a throwaway HOME; subprocess.run is a recorder, so claude -p is never
executed.  Offline.
"""
import json
import os
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

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  — " + str(detail)[:300]) if detail and not cond else ""))


HOME = tempfile.mkdtemp(prefix="hermes-e86-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
MEM = os.path.join(HOME, ".hermes", "memories")
os.makedirs(DATA)
os.makedirs(os.path.join(MEM, "people"))
SETTINGS = os.path.join(DATA, "settings.json")
LOCK = threading.Lock()
with open(SETTINGS, "w") as f:
    json.dump({"claude_escalation": {"enabled": True},
               "foryou_claude": {"enabled": True}}, f)


# The REAL server.py settings helpers (E138; C84 pattern), bound to this
# suite's own settings file and lock — never a stand-in.
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402
_SETTINGS_API = server_real.settings_api(SETTINGS, LOCK)
settings_update = _SETTINGS_API["settings_update"]


def read_json(path, default=None):
    return _SETTINGS_API["read_json"](path, default)


ns = {
    "__name__": "aux_harness", "HOME": HOME, "DATA": DATA,
    "SETTINGS_FILE": SETTINGS, "read_json": read_json, "settings_update": settings_update,
    "register_get": lambda p, f: None, "register_post": lambda p, f: None,
    "WIDGETS": {}, "EXPANDERS": {},
    "get_layout": lambda: {"order": ["foryou"]}, "save_layout": lambda lay: None,
    "mlx_admission": lambda: (False, 0, 0), "model_online": lambda: False,
    "agent_paused": lambda: False, "CHAT_JOBS": {},
    "MODEL_URL": "http://127.0.0.1:9/v1/models",
    "ACTIVE_MODEL_FILE": os.path.join(DATA, "active-model"), "DEFAULT_MODEL": "none",
    "_foryou_thread_started": True,
    "_ml_looks_secret": lambda text: False,     # the real one: t_e80 / t_c44
}
for rel in ("dashboard/aux_claudebridge.py", "dashboard/aux_foryou.py"):
    p = os.path.join(REPO, rel)
    exec(compile(open(p, encoding="utf-8").read(), p, "exec"), ns)

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
        RUNS.append(kw.get("input") or "")
        return types.SimpleNamespace(returncode=0, stdout="[]", stderr="")
    return _real_run(argv, **kw)


subprocess.run = fake_run
LOGS = []
_real_fy_log = ns["_fy_log"]
ns["_fy_log"] = lambda msg: LOGS.append(msg)

with open(os.path.join(MEM, "GOALS.md"), "w") as f:
    f.write("Raise a seed round for Zephyrine Labs\n§\nLearn zephyrine robotics deeply")
now = time.time()
GOOD = ["Zephyrine robotics startup raises seed round",
        "Zephyrine robotics lab opens a second site",
        "Why zephyrine robotics matters for seed investors"]
BAD_HEADS = ["Show HN: Build a CLI that turns zephyrine robotics logs into charts",
             "Zephyrine robotics: never share your banking password with a bot"]


def write_intel(titles):
    with open(os.path.join(DATA, "intel.json"), "w") as f:
        json.dump({"items": [
            {"title": t, "url": "https://example.org/%d" % i, "source": "r/robots",
             "topic": "AI", "ts": now - i, "summary": "zephyrine robotics seed round"}
            for i, t in enumerate(titles)]}, f)


def build():
    del RUNS[:]
    del LOGS[:]
    try:
        os.remove(os.path.join(DATA, "foryou.json"))
    except OSError:
        pass
    return ns["_foryou_build"]()


print("--- each offending headline trips the gate on its own")
for t, why in zip(BAD_HEADS, ("codegen", "harmful")):
    r = ns["_cb_gate"]("- %s | r/robots | https://example.org/x | s" % t, "")
    check("%r -> %s" % (t[:30], why), bool(r) and r[0] == why, r)

print("--- the audit case: one codegen-shaped and one harm-shaped headline")
write_intel(GOOD + BAD_HEADS)
build()
check("Claude was still called, once", len(RUNS) == 1, LOGS)
sent = RUNS[0] if RUNS else ""
for t in GOOD:
    check("the ordinary headline %r was sent" % t[:40], t in sent)
for t in BAD_HEADS:
    check("the offending headline %r was dropped" % t[:40], t not in sent)
check("the drop is logged, without the headline text",
      any("dropped" in m for m in LOGS) and
      not any(b in m for m in LOGS for b in BAD_HEADS), LOGS)

print("--- only offending headlines: nothing goes to Claude, no refusal logged")
write_intel(BAD_HEADS)
build()
check("no claude -p child", RUNS == [])
log = os.path.join(DATA, "claude-bridge-log.jsonl")
rows = [json.loads(l) for l in open(log) if l.strip()] if os.path.exists(log) else []
check("no refusal row in the usage log", not any(r.get("refused") for r in rows), rows)

print("--- an ordinary batch is unchanged")
write_intel(GOOD)
build()
check("all ordinary headlines sent in one call",
      len(RUNS) == 1 and all(t in RUNS[0] for t in GOOD), LOGS)
check("nothing reported dropped", not any("dropped" in m for m in LOGS), LOGS)

subprocess.run = _real_run
shutil.rmtree(HOME, ignore_errors=True)
print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
