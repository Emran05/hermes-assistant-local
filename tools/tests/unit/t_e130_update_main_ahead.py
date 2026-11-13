#!/usr/bin/env python3
"""Regression test for E130 and E85 — docs/audits/2026-11-09-codebase-audit.md.

E130: on the main channel a checkout with local commits on top of origin/main
read "update available" forever (HEAD never EQUALS the remote head there; the
ff-only merge then says "Already up to date"). Now an update is offered only
while the origin/main commit is not already an ancestor of HEAD.

E85: a check that reached GitHub (or origin) but failed — 401/403/404/5xx,
no tags — left no trace, so "Last outbound" froze at the last success. Now
every check that got an answer stamps `attempted_at` (+ `attempt_signed`)
into update-check.json, leaving checked_at (the cache clock) alone.

Runs the REAL dashboard/aux_update.py against throwaway git repos (file://
origin), the t_r254 harness. GitHub is never contacted: _upd_fetch is a stub.
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
print("\n1. E130: local commits on top of origin/main are not an update")
git(INSTALL, "checkout", "-q", "main")
with open(os.path.join(INSTALL, "local.txt"), "w") as f:
    f.write("mine\n")
git(INSTALL, "add", "local.txt")
git(INSTALL, "commit", "-q", "-m", "local work")
git(INSTALL, "fetch", "-q", "origin")
check("(HEAD is ahead of origin/main)",
      git(INSTALL, "rev-list", "--count", "origin/main..HEAD") == "1")
p = ns["_upd_check"](force=True)
check("a forced check says up to date", p.get("update_available") is False, p)
p = ns["_upd_check"]()
check("...and so does the cached answer", p.get("cached") is True
      and p.get("update_available") is False, p)

new = upstream_commit("upstream moved")
p = ns["_upd_check"](force=True)
check("once origin/main gains a commit HEAD lacks (not yet fetched), it IS an update",
      p.get("update_available") is True and p.get("remote_head") == new[:7], p)
git(INSTALL, "fetch", "-q", "origin")
p = ns["_upd_check"]()
check("...still an update after a fetch, while HEAD does not contain it",
      p.get("update_available") is True, p)
git(INSTALL, "merge", "-q", "--no-edit", "origin/main")
p = ns["_upd_check"]()
check("merged in (HEAD now contains it, plus local work): up to date again",
      p.get("update_available") is False, p)
check("_upd_main_behind with no remote is unknown (None)", ns["_upd_main_behind"]("") is None)

# --------------------------------------------------------------------------
print("\n2. E85: a check that reached GitHub but failed is still recorded")
SETTINGS["update"]["channel"] = "stable"
good_at = time.time() - 3600
write_json(ns["_UPD_CACHE_FILE"], {
    "checked_at": good_at, "channel": "stable", "etag": "",
    "payload": ns["_upd_payload"]("1.0.0", "stable", latest="v1.0.0", source="github")})
ns["_upd_is_git"] = lambda: False
ns["_upd_fetch"] = lambda *a, **k: (404, None, None)
ns["_upd_token"] = lambda: None
t0 = time.time()
p = ns["_upd_check"](force=True)
c = read_json(ns["_UPD_CACHE_FILE"])
check("the answer is the stale cached one, with the error", p.get("stale") is True
      and "no release found" in (p.get("error") or ""), p)
check("checked_at (the last GOOD answer, the cache clock) is unchanged",
      c.get("checked_at") == good_at, c)
check("attempted_at records this attempt", (c.get("attempted_at") or 0) >= t0 - 1, c)
check("...an anonymous one (attempt_signed false)", c.get("attempt_signed") is False, c)

calls = []


def fetch(url, etag=None, token=None, **k):
    calls.append(bool(token))
    return (403, None, None)


ns["_upd_fetch"] = fetch
ns["_upd_token"] = lambda: "tok"
p = ns["_upd_check"](force=True)
c = read_json(ns["_UPD_CACHE_FILE"])
check("a retry that sent the owner's token is recorded as signed",
      calls == [False, True] and c.get("attempt_signed") is True
      and c.get("checked_at") == good_at, (calls, c))

c["attempted_at"] = 1.0
write_json(ns["_UPD_CACHE_FILE"], c)
ns["_upd_fetch"] = lambda *a, **k: (0, None, None)      # nothing answered
ns["_upd_token"] = lambda: None
p = ns["_upd_check"](force=True)
c = read_json(ns["_UPD_CACHE_FILE"])
check("a check that reached nothing (offline) records no attempt",
      c.get("attempted_at") == 1.0 and c.get("checked_at") == good_at, c)

ns["_upd_fetch"] = lambda *a, **k: (200, {"tag_name": "v1.1.0", "body": "", "html_url": "",
                                          "published_at": "", "assets": []}, '"e"')
t0 = time.time()
p = ns["_upd_check"](force=True)
c = read_json(ns["_UPD_CACHE_FILE"])
check("a good answer moves checked_at AND attempted_at",
      p.get("latest") == "v1.1.0" and c.get("checked_at") >= t0 - 1
      and (c.get("attempted_at") or 0) >= t0 - 1 and c.get("attempt_signed") is False, c)

# the main channel's ls-remote that answers is a signed attempt
SETTINGS["update"]["channel"] = "main"
ns["_upd_is_git"] = lambda: True
t0 = time.time()
p = ns["_upd_check"](force=True)
c = read_json(ns["_UPD_CACHE_FILE"])
check("main channel: reaching origin is recorded, signed (owner's git credentials)",
      p.get("source") == "main" and (c.get("attempted_at") or 0) >= t0 - 1
      and c.get("attempt_signed") is True, c)

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
