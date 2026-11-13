#!/usr/bin/env python3
"""Regression test for E18 (update half) — docs/audits/2026-11-09-codebase-audit.md.

Applying an update restarts the dashboard and serve, which kills a chat turn
in flight and loses its reply; POST /api/update/apply never checked. Now it
asks server.py's chat_jobs_active() (resolved BY NAME at call time; a
server.py without it falls back to CHAT_JOBS) and answers 409 reason
"chat_busy" with a plain message unless the body carries a literal
`force: true`.

Runs the REAL dashboard/aux_update.py (exec'd like server.py does, with
stubbed store helpers) in a throwaway git checkout; Popen is a fake, so no
update.sh ever runs.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
AUX_UPDATE = os.path.join(REPO, "dashboard", "aux_update.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s\n    | %s" % (name, str(extra)[-1200:]))


TMP = tempfile.mkdtemp(prefix="t-e18-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
INSTALL = os.path.join(TMP, "install")
GENV = dict(os.environ, HOME=HOME, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
subprocess.run(["git", "init", "-q", "-b", "main", INSTALL], env=GENV, check=True)
os.makedirs(os.path.join(INSTALL, "dashboard"))
with open(os.path.join(INSTALL, "VERSION"), "w") as f:
    f.write("1.0.0\n")
with open(os.path.join(INSTALL, "update.sh"), "w") as f:
    f.write("#!/bin/bash\nexit 0\n")
subprocess.run(["git", "-C", INSTALL, "add", "-A"], env=GENV, check=True)
subprocess.run(["git", "-C", INSTALL, "commit", "-q", "-m", "a"], env=GENV, check=True)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


SETTINGS = {"update": {"channel": "stable"}}


def load():
    ns = {"__file__": os.path.join(INSTALL, "dashboard", "aux_update.py"),
          "__name__": "aux_update_under_test",
          "DATA": DATA, "read_json": read_json, "write_json": write_json,
          "get_settings": lambda: SETTINGS, "settings_update": lambda fn: fn(SETTINGS),
          "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None,
          "_UPD_BG_STARTED": True}
    old = os.environ.get("HOME")
    os.environ["HOME"] = HOME
    try:
        exec(compile(open(AUX_UPDATE).read(), AUX_UPDATE, "exec"), ns)
    finally:
        os.environ["HOME"] = old
    ns["SPAWNED"] = []

    def fake_popen(cmd, **kw):
        ns["SPAWNED"].append((cmd, kw))
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    ns["_upd_subprocess"] = SimpleNamespace(Popen=fake_popen, DEVNULL=subprocess.DEVNULL,
                                            PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT)
    return ns


def lock_free(ns):
    fd = os.open(ns["_UPD_LOCK_FILE"], os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def apply(ns, body):
    r = ns["_upd_apply"](SimpleNamespace(body=body))
    return r if isinstance(r, tuple) else (r, 200)


def reset(ns):
    ns["SPAWNED"][:] = []
    try:
        os.remove(ns["_UPD_STATE_FILE"])
    except OSError:
        pass


ns = load()
check("aux_update.py loaded against the throwaway checkout",
      ns["_UPD_ROOT"] == INSTALL, ns.get("_UPD_ROOT"))

print("\n1. chat_jobs_active() says a turn is running")
ns["chat_jobs_active"] = lambda: True
r, code = apply(ns, {"target": "latest"})
check("Apply is refused 409 reason chat_busy", code == 409 and r.get("reason") == "chat_busy"
      and r.get("busy") is True and r.get("ok") is False, (r, code))
check("the message is plain and names the consequence and the way out",
      "chat turn is still running" in r.get("error", "")
      and "loses its reply" in r.get("error", "") and "Update anyway" in r.get("error", ""), r)
check("nothing was spawned and the update lock is free",
      ns["SPAWNED"] == [] and lock_free(ns), ns["SPAWNED"])
for bad in ("true", 1, "yes", None, {}, [True]):
    r, code = apply(ns, {"target": "latest", "force": bad})
    check("force=%r is not a confirmation (409)" % (bad,),
          code == 409 and r.get("reason") == "chat_busy" and ns["SPAWNED"] == [], (r, code))
r, code = apply(ns, {"target": "latest", "force": True})
check("force: true goes ahead and spawns update.sh",
      code == 200 and r.get("ok") is True and len(ns["SPAWNED"]) == 1, (r, code))
reset(ns)

print("\n2. no turn running")
ns["chat_jobs_active"] = lambda: False
r, code = apply(ns, {"target": "latest"})
check("Apply proceeds without force", code == 200 and r.get("ok") is True
      and len(ns["SPAWNED"]) == 1, (r, code))
reset(ns)

print("\n3. the name is resolved at CALL time (aux files load before server.py's defs win)")
ns.pop("chat_jobs_active", None)
ns["CHAT_JOBS"] = {"a": {"done": True}, "b": {"done": False}}
r, code = apply(ns, {"target": "latest"})
check("without chat_jobs_active, a not-done CHAT_JOBS entry still refuses",
      code == 409 and r.get("reason") == "chat_busy" and ns["SPAWNED"] == [], (r, code))
ns["CHAT_JOBS"] = {"a": {"done": True}}
r, code = apply(ns, {"target": "latest"})
check("only finished jobs: Apply proceeds", code == 200 and len(ns["SPAWNED"]) == 1, (r, code))
reset(ns)
ns["chat_jobs_active"] = lambda: True       # defined AFTER load, as server.py would
ns["CHAT_JOBS"] = {}
r, code = apply(ns, {"target": "latest"})
check("a chat_jobs_active defined after load is the one asked",
      code == 409 and r.get("reason") == "chat_busy", (r, code))


def boom():
    raise RuntimeError("x")


ns["chat_jobs_active"] = boom
r, code = apply(ns, {"target": "latest"})
check("a chat_jobs_active that raises never blocks Apply", code == 200 and r.get("ok") is True,
      (r, code))
reset(ns)

print("\n4. the order of refusals: a bad target is still a 400, not a busy 409")
ns["chat_jobs_active"] = lambda: True
r, code = apply(ns, {"target": "not-a-version"})
check("bad target -> 400 bad_target", code == 400 and r.get("reason") == "bad_target", (r, code))

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
