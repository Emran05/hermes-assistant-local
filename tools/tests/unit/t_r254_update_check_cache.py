#!/usr/bin/env python3
"""Regression test for R2-54 (2026-10-08 audit, update prose).

A cached `main`-channel answer from GET /api/update/check kept
update_available=true for up to the 6 h cache lifetime after the update had
been applied, although HEAD then equalled the remote head it was cached
against: the cached-read branch passed the stored verdict straight through
for source == "main". The lock-busy and stale branches did not recompute
anything, on either channel.

This runs the REAL dashboard/aux_update.py (exec'd with stubbed server.py
globals, its background thread suppressed) against a REAL git checkout whose
`origin` is a bare repo on local disk — offline, a throwaway HOME, no
dashboard. GitHub is never asked: _upd_fetch is replaced by an "unreachable"
stub.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import json
import threading
import time

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


if not shutil.which("git"):
    print("SKIP git is not installed")
    print("TESTS 0 passed 0 failed 1 skipped")
    sys.exit(0)

TMP = tempfile.mkdtemp(prefix="t-r254-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
GENV = dict(os.environ, HOME=HOME, GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def git(cwd, *args):
    p = subprocess.run(["git", "-C", cwd] + list(args), env=GENV,
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), p.stderr))
    return p.stdout.strip()


ORIGIN = os.path.join(TMP, "origin.git")
INSTALL = os.path.join(TMP, "install")       # the checkout the dashboard runs from
PUSHER = os.path.join(TMP, "pusher")         # where "upstream" commits come from
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", ORIGIN], env=GENV, check=True)
subprocess.run(["git", "clone", "-q", ORIGIN, PUSHER], env=GENV, check=True,
               capture_output=True)
git(PUSHER, "checkout", "-q", "-b", "main")
with open(os.path.join(PUSHER, "VERSION"), "w") as f:
    f.write("1.0.0\n")
git(PUSHER, "add", "VERSION")
git(PUSHER, "commit", "-q", "-m", "a")
git(PUSHER, "push", "-q", "origin", "main")
subprocess.run(["git", "clone", "-q", ORIGIN, INSTALL], env=GENV, check=True,
               capture_output=True)
os.makedirs(os.path.join(INSTALL, "dashboard"))

# --------------------------------------------------------------------------
# load the real module
# --------------------------------------------------------------------------
SETTINGS = {"update": {"channel": "main"}}


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


GIT_CALLS = []
ns = {"__file__": os.path.join(INSTALL, "dashboard", "aux_update.py"),
      "__name__": "aux_update_under_test",
      "DATA": DATA, "read_json": read_json, "write_json": write_json,
      "get_settings": lambda: SETTINGS, "settings_update": lambda fn: fn(SETTINGS),
      "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None,
      "_UPD_BG_STARTED": True}                  # no background thread in a test
old_home = os.environ.get("HOME")
os.environ["HOME"] = HOME
try:
    exec(compile(open(AUX_UPDATE).read(), AUX_UPDATE, "exec"), ns)
finally:
    if old_home is not None:
        os.environ["HOME"] = old_home
check("aux_update.py loaded against the throwaway checkout",
      ns["_UPD_ROOT"] == INSTALL and ns["_UPD_CACHE_FILE"].startswith(DATA),
      (ns.get("_UPD_ROOT"), ns.get("_UPD_CACHE_FILE")))
_real_git = ns["_upd_git"]


def counting_git(args, timeout=6):
    GIT_CALLS.append(list(args))
    return _real_git(args, timeout=timeout)


ns["_upd_git"] = counting_git
ns["_upd_fetch"] = lambda *a, **k: (0, None, None)     # GitHub is "unreachable"
ns["_upd_token"] = lambda: None


def ls_remote_calls():
    return sum(1 for a in GIT_CALLS if a[:1] == ["ls-remote"])


def upstream_commit(msg):
    with open(os.path.join(PUSHER, "f.txt"), "a") as f:
        f.write(msg + "\n")
    git(PUSHER, "add", "f.txt")
    git(PUSHER, "commit", "-q", "-m", msg)
    git(PUSHER, "push", "-q", "origin", "main")
    return git(PUSHER, "rev-parse", "HEAD")


def apply_update():
    """What update.sh's main-channel path does to the checkout."""
    git(INSTALL, "fetch", "-q", "origin")
    git(INSTALL, "merge", "-q", "--ff-only", "origin/main")


# --------------------------------------------------------------------------
print("\n1. a cached main-channel answer follows HEAD, not the cache")
new = upstream_commit("b")
p = ns["_upd_check"](force=True)
check("a forced check sees origin/main ahead of HEAD", p.get("update_available") is True
      and p.get("remote_head") == new[:7], p)
p = ns["_upd_check"]()
check("...and the cached answer agrees while HEAD is behind",
      p.get("cached") is True and p.get("update_available") is True, p)

apply_update()
check("(the update is applied: HEAD == origin/main)",
      git(INSTALL, "rev-parse", "HEAD") == new)
before = ls_remote_calls()
p = ns["_upd_check"]()
check("once HEAD equals the cached remote head, the cached answer says up to date",
      p.get("cached") is True and p.get("update_available") is False, p)
check("...and reports the new local head", p.get("local_head") == new[:7], p)
check("...without asking origin again (HEAD is local)", ls_remote_calls() == before,
      GIT_CALLS[-3:])

# --------------------------------------------------------------------------
print("\n2. the lock-busy and stale answers are re-judged too")
cache = read_json(ns["_UPD_CACHE_FILE"])
cache["checked_at"] = time.time() - 7 * 3600          # past the 6 h TTL
write_json(ns["_UPD_CACHE_FILE"], cache)
held = threading.Event()
release = threading.Event()


def hold_lock():
    with ns["_UPD_LOCK"]:
        held.set()
        release.wait(10)


t = threading.Thread(target=hold_lock, daemon=True)
t.start()
held.wait(5)
p = ns["_upd_check"](budget=1.0)
release.set()
t.join(5)
check("while another check holds the lock, the cached answer is re-judged (up to date)",
      p.get("checking") is True and p.get("update_available") is False, p)

git(INSTALL, "remote", "set-url", "origin", os.path.join(TMP, "gone.git"))
p = ns["_upd_check"]()
check("with origin unreachable, the stale cached answer is re-judged (up to date)",
      p.get("stale") is True and p.get("update_available") is False, p)
git(INSTALL, "remote", "set-url", "origin", ORIGIN)

SETTINGS["update"]["channel"] = "stable"
write_json(ns["_UPD_CACHE_FILE"], {
    "checked_at": time.time() - 7 * 3600, "channel": "stable", "etag": "",
    "payload": ns["_upd_payload"]("1.0.0", "stable", latest="v1.0.0", source="github")})
cache = read_json(ns["_UPD_CACHE_FILE"])
cache["payload"]["update_available"] = True            # as cached before an update
write_json(ns["_UPD_CACHE_FILE"], cache)
ns["_upd_is_git"] = lambda: False                      # no git-tags fallback either
p = ns["_upd_check"]()
check("stable: the stale cached answer is re-judged against VERSION",
      p.get("stale") is True and p.get("update_available") is False, p)
ns["_upd_is_git"] = lambda: True
SETTINGS["update"]["channel"] = "main"

# --------------------------------------------------------------------------
print("\n3. update.sh finishing busts the cache")
p = ns["_upd_check"](force=True)
check("(a fresh main answer is cached)", p.get("update_available") is False, p)
before = ls_remote_calls()
p = ns["_upd_check"]()
check("(a read straight after is served from the cache)",
      p.get("cached") is True and ls_remote_calls() == before, p)
write_json(ns["_UPD_STATE_FILE"], {"running": False,
                                   "last_result": {"ok": True, "finished_at": time.time() + 1}})
newer = upstream_commit("c")
p = ns["_upd_check"]()
check("an update that finished after the answer was cached forces a real re-check",
      not p.get("cached") and ls_remote_calls() == before + 1, (p, GIT_CALLS[-3:]))
check("...which sees what origin has now", p.get("remote_head") == newer[:7]
      and p.get("update_available") is True, p)
write_json(ns["_UPD_STATE_FILE"], {"running": False, "last_result": {"finished_at": "junk"}})
p = ns["_upd_check"]()
check("a malformed finished_at in the state file is ignored, not raised",
      isinstance(p, dict) and p.get("cached") is True, p)

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
