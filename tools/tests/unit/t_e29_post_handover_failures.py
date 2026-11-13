#!/usr/bin/env python3
"""Regression test for E29 (review) — docs/audits/2026-11-09-codebase-audit.md.

The E29 fix execs the INCOMING update.sh after the code swap. The incoming
script is untested code by definition, so this checks the failure paths of
that hand-over:

  * an incoming update.sh that does not parse (bash -n) is never exec'd: the
    outgoing text finishes the update (plugins, services, gateway) and says so.
    Before, the run died at the syntax error with exit 0, nothing linked, no
    services re-rendered, and the next run said "already up to date";
  * an incoming update.sh that dies before it can write a result leaves a
    result that says so (the outgoing run records a provisional one before
    the exec), not the previous update's stale "ok";
  * a hand-run `./update.sh --post-update` (what install-services.sh tells
    the owner to run when the post step failed) takes the update lock like a
    normal run, so it cannot race an update the dashboard is running.

Scratch git repos, scratch HOME, launchctl/pgrep/gh/curl/sleep shims on PATH.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile

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
        print("  FAIL %s\n    | %s" % (name, str(extra)[-1500:].replace("\n", "\n    | ")))


TMP = tempfile.mkdtemp(prefix="t-e29r-")
SHIMS = {
    "launchctl": ('#!/bin/bash\nprintf "%s\\n" "$*" >> "$E29_FX/launchctl.calls"\n'
                  'case "$1" in print) case "$2" in */ai.hermes.gateway) exit 0;; esac; exit 113;; esac\n'
                  'exit 0\n'),
    "pgrep": "#!/bin/sh\nexit 1\n",
    "gh": "#!/bin/sh\nexit 1\n",
    "curl": "#!/bin/sh\nexit 7\n",
    "sleep": "#!/bin/sh\nexit 0\n",
}
SHIM = os.path.join(TMP, "shim")
os.makedirs(SHIM)
for n, body in SHIMS.items():
    p = os.path.join(SHIM, n)
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def git(*a, cwd=None):
    p = subprocess.run(["git"] + list(a), cwd=cwd, env=dict(os.environ, **GIT_ENV),
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(a), p.stderr))
    return p.stdout


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


CUR = read(os.path.join(REPO, "update.sh"))


def put(work, version, update_text=None):
    dst = os.path.join(work, "hermes-plugins")
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(os.path.join(REPO, "hermes-plugins"), dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in ("update.sh", "install-services.sh"):
        shutil.copy2(os.path.join(REPO, f), os.path.join(work, f))
    if update_text is not None:
        write(os.path.join(work, "update.sh"), update_text, 0o755)
    write(os.path.join(work, "VERSION"), version + "\n")


def scenario(name, incoming):
    root = os.path.join(TMP, name)
    work = os.path.join(root, "work")
    os.makedirs(work)
    git("init", "-q", "-b", "main", cwd=work)
    for tag, text in (("v1.4.0", None), ("v1.5.0", incoming)):
        put(work, tag[1:], text)
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", tag, cwd=work)
        git("tag", tag, cwd=work)
    origin = os.path.join(root, "origin.git")
    git("clone", "-q", "--bare", work, origin)
    install = os.path.join(root, "install")
    git("clone", "-q", origin, install)
    git("checkout", "-q", "v1.4.0", cwd=install)
    home = os.path.join(root, "home")
    write(os.path.join(home, ".hermes", "config.yaml"),
          "model:\n  default: m\nplugins:\n  enabled:\n    - tool-budget\n")
    fx = os.path.join(root, "fx")
    os.makedirs(fx)
    return install, home, fx


def env_for(home, fx, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("HERMES_UPDATE_FROM", "HERMES_UPDATE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
           and not k.startswith("HERMES_UPD_")}
    env.update(GIT_ENV)
    env.update({"HOME": home, "E29_FX": fx, "TMPDIR": os.path.join(home, "tmp"),
                "PATH": ":".join([SHIM, os.path.dirname(sys.executable),
                                  "/usr/bin", "/bin", "/usr/sbin", "/sbin"])})
    os.makedirs(env["TMPDIR"], exist_ok=True)
    env.update(extra)
    return env


def run(install, home, fx, *args, **extra):
    p = subprocess.run(["/bin/bash", os.path.join(install, "update.sh")] + list(args),
                       env=env_for(home, fx, **extra), capture_output=True, text=True,
                       timeout=180)
    return p.returncode, p.stdout + p.stderr


def state(home):
    try:
        return json.loads(read(os.path.join(home, ".hermes", "dashboard", "update-state.json")))
    except ValueError:
        return {}


def ag_linked(home, install):
    p = os.path.join(home, ".hermes", "plugins", "approval-gate")
    return os.path.islink(p) and os.readlink(p) == os.path.join(install, "hermes-plugins",
                                                               "approval-gate")


def kicks(fx):
    return [l for l in read(os.path.join(fx, "launchctl.calls")).splitlines()
            if l.startswith("kickstart -k ")]


TB_LINE = 'TB_SRC="$ROOT/hermes-plugins/tool-budget"'
check("the plugin step's first line is where the test expects it", CUR.count(TB_LINE) == 1)

print("\n1. an incoming update.sh with a syntax error is not exec'd")
BROKEN = CUR.replace(TB_LINE, 'TB_SRC=( "$ROOT"\n' + TB_LINE, 1)
install, home, fx = scenario("syntax", BROKEN)
check("the broken variant really fails bash -n", subprocess.run(
    ["/bin/bash", "-n", "/dev/stdin"], input=BROKEN, text=True,
    capture_output=True).returncode != 0)
rc, out = run(install, home, fx, "--yes", "--channel", "stable")
st = state(home).get("last_result") or {}
check("the update lands on 1.5.0 and exits 0",
      rc == 0 and read(os.path.join(install, "VERSION")).strip() == "1.5.0", (rc, out))
check("no hand-over to the broken script; the owner is told why",
      "continuing with the incoming update.sh" not in out and "does not parse" in out, out)
check("the outgoing text finished it: approval-gate linked, services re-rendered, "
      "gateway restarted once",
      ag_linked(home, install) and "bootstrap" in read(os.path.join(fx, "launchctl.calls"))
      and len(kicks(fx)) == 1, (out, kicks(fx)))
check("update-state.json records a good update to 1.5.0",
      st.get("ok") is True and st.get("to_version") == "1.5.0" and st.get("gateway") == "restarted",
      st)

print("\n2. an incoming update.sh that dies before it can write a result")
EARLY = CUR.replace("set -euo pipefail", "set -euo pipefail\nexit 3", 1)
install, home, fx = scenario("early", EARLY)
# a previous good update left this behind; it must not be what the owner sees
write(os.path.join(home, ".hermes", "dashboard", "update-state.json"),
      json.dumps({"running": True, "pid": 1, "last_result": {"ok": True, "message": "old"}}))
rc, out = run(install, home, fx, "--yes", "--channel", "stable")
cur = state(home)
st = cur.get("last_result") or {}
check("the run fails", rc != 0, (rc, out))
check("the result is a failure that names the hand-over, not the stale 'ok'",
      st.get("ok") is False and "handed over to the incoming update.sh" in (st.get("message") or ""),
      cur)
check("the provisional result leaves the dashboard's running flag alone",
      cur.get("running") is True, cur)

print("\n3. a hand-run ./update.sh --post-update takes the update lock")
install, home, fx = scenario("hand", None)
git("checkout", "-q", "v1.5.0", cwd=install)
lock = os.path.join(home, ".hermes", "dashboard", "update.lock")
os.makedirs(os.path.dirname(lock), exist_ok=True)
fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)      # an update is running
rc, out = run(install, home, fx, "--post-update")
check("refused while another update holds the lock",
      rc != 0 and "already running" in out and kicks(fx) == []
      and not ag_linked(home, install), (rc, out))
os.close(fd)
rc, out = run(install, home, fx, "--post-update")
check("runs once the lock is free: plugins linked, gateway restarted",
      rc == 0 and ag_linked(home, install) and len(kicks(fx)) == 1, (rc, out))
try:
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(fd)
    free = True
except OSError:
    free = False
check("the lock is free again afterwards", free)

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
