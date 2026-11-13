#!/usr/bin/env python3
"""Regression test for E129 (review) — docs/audits/2026-11-09-codebase-audit.md.

The E129 fix left a loaded model job alone whenever its launchd plist was
unchanged. The plist names mlx-server.sh, which runs mlx-vlm-launch.py, so an
update that changed the LAUNCHER (a lockdown fix, say) but not the plist left
a running lane on the old launcher — an always-on primary until the next
reboot. Now update.sh fingerprints what the lanes run before and after the
swap, and install-services.sh keeps a loaded lane only when the update vouched
that none of it changed (HERMES_MODEL_CODE_UNCHANGED=1); otherwise it reloads
it and says so.

Scratch git repos, scratch HOME; launchctl is a shim that reports every job
loaded and logs each call. Nothing reaches this Mac's launchd.
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


TMP = tempfile.mkdtemp(prefix="t-e129r-")
SHIMS = {
    "launchctl": ('#!/bin/bash\nprintf "%s\\n" "$*" >> "$E29_FX/launchctl.calls"\n'
                  'case "$1" in print) exit 0;; esac\n'
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



LANE = {"mlx-server.sh": "#!/bin/bash\necho primary\n",
        "mlx-server-bg.sh": "#!/bin/bash\necho bg\n",
        "mlx-vlm-launch.py": "print('launcher v1')\n"}


def lane_scenario(name, new_launcher):
    install, home, fx = scenario(name, None)
    # rewrite both tags' trees with lane files: v1.4.0 has the v1 launcher,
    # v1.5.0 has `new_launcher`
    work = os.path.join(os.path.dirname(install), "work")
    for tag, launcher in (("v1.4.0", LANE["mlx-vlm-launch.py"]), ("v1.5.0", new_launcher)):
        git("checkout", "-q", tag, cwd=work)
        for n, t in LANE.items():
            write(os.path.join(work, n), launcher if n == "mlx-vlm-launch.py" else t, 0o755)
        git("add", "-A", cwd=work)
        git("commit", "-q", "-m", tag + " lanes", cwd=work)
        git("tag", "-f", tag, cwd=work)
    origin = os.path.join(os.path.dirname(install), "origin.git")
    git("push", "-q", "-f", "--tags", origin, cwd=work)
    git("fetch", "-q", "-f", "--tags", cwd=install)
    git("checkout", "-q", "v1.4.0", cwd=install)
    # the services as v1.4.0 left them: plists rendered, every job loaded
    p = subprocess.run(["/bin/bash", os.path.join(install, "install-services.sh")],
                       env=env_for(home, fx), capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stdout + p.stderr)
    os.remove(os.path.join(fx, "launchctl.calls"))
    return install, home, fx


def model_reloads(fx):
    return [l for l in read(os.path.join(fx, "launchctl.calls")).splitlines()
            if l.startswith("bootout ") and ("com.hermes.mlx-server" in l or "com.hermes.mlx-bg" in l)]


print("\n1. an update that does not touch what the lanes run leaves them running")
install, home, fx = lane_scenario("same", LANE["mlx-vlm-launch.py"])
rc, out = run(install, home, fx, "--yes", "--channel", "stable")
check("the update succeeds", rc == 0 and read(os.path.join(install, "VERSION")).strip() == "1.5.0", out)
check("neither model job was booted out", model_reloads(fx) == [], model_reloads(fx))
check("the note says both lanes were left as they were",
      "primary unchanged — left exactly as it was" in out
      and "background unchanged — left exactly as it was" in out, out)

print("\n2. an update that changes the launcher reloads both lanes and says so")
install, home, fx = lane_scenario("launcher", "print('launcher v2')\n")
rc, out = run(install, home, fx, "--yes", "--channel", "stable")
check("the update succeeds", rc == 0 and read(os.path.join(install, "VERSION")).strip() == "1.5.0", out)
check("both model jobs were booted out (and bootstrapped again)",
      len(model_reloads(fx)) == 2, model_reloads(fx))
check("the note says they were reloaded for the new code",
      "primary reloaded so it runs this version's code" in out
      and "start it again if you had" in out, out)

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
