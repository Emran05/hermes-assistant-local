#!/usr/bin/env python3
"""Regression test for R2-56 (2026-10-08 audit, model lifecycle prose).

launch-dashboard.sh started mlx-server.sh — which the A09 on-demand gate
refuses (exit 0) without a fresh start token only the dashboard mints — and
then polled :8080 for up to six minutes before it started the dashboard. It
also logged into the repo under dashboard/logs.

The SHIPPED launch-dashboard.sh is copied into a throwaway install dir whose
dashboard/server.py and mlx-server.sh are stubs that only leave a marker,
with a throwaway HOME and `curl`, `open` and `sleep` shimmed on PATH (curl
answers "connection refused", or "200" for the already-running case). No
port is touched and no model can load: the real mlx-server.sh is never there.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
LAUNCH = os.path.join(REPO, "launch-dashboard.sh")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s\n    | %s" % (name, str(extra)[-1500:]))


TMP = tempfile.mkdtemp(prefix="t-r256-")


def run(name, dashboard_up):
    root = os.path.join(TMP, name)
    install, home, shim = (os.path.join(root, d) for d in ("install", "home", "bin"))
    for d in (os.path.join(install, "dashboard"), home, shim):
        os.makedirs(d)
    shutil.copy(LAUNCH, os.path.join(install, "launch-dashboard.sh"))
    with open(os.path.join(install, "dashboard", "server.py"), "w") as f:
        f.write("import os\nprint('stub dashboard on port', os.environ.get('DASH_PORT'))\n"
                "open(os.path.join(os.environ['HOME'], 'server-ran'), 'w').close()\n")
    with open(os.path.join(install, "mlx-server.sh"), "w") as f:
        f.write('#!/bin/sh\n: > "$HOME/mlx-server-ran"\nexit 0\n')
    os.chmod(os.path.join(install, "mlx-server.sh"), 0o755)
    calls = os.path.join(root, "calls")
    for tool, rc in (("curl", 0 if dashboard_up else 7), ("open", 0), ("sleep", 0)):
        p = os.path.join(shim, tool)
        with open(p, "w") as f:
            f.write('#!/bin/sh\nprintf "%s %%s\\n" "$*" >> "%s"\nexit %d\n' % (tool, calls, rc))
        os.chmod(p, 0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("DASH_PORT", "HERMES_MODEL_ALWAYS_ON")}
    env.update({"HOME": home, "DASH_PORT": "17788",
                "PATH": ":".join([shim, os.path.dirname(sys.executable), "/usr/bin", "/bin"])})
    which = subprocess.run(["bash", "-c", "command -v curl; command -v open"], env=env,
                           capture_output=True, text=True).stdout.split()
    if which != [os.path.join(shim, "curl"), os.path.join(shim, "open")]:
        print("  refusing to run: curl/open do not resolve to the shims (%r)" % which)
        sys.exit(1)
    t0 = time.time()
    r = subprocess.run(["bash", os.path.join(install, "launch-dashboard.sh")], env=env,
                       capture_output=True, text=True, timeout=60, cwd=install)
    r.elapsed = time.time() - t0
    r.calls = open(calls).read() if os.path.exists(calls) else ""
    r.install, r.home = install, home
    return r


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


try:
    print("\n1. with no dashboard answering, it starts the dashboard and nothing else")
    r = run("cold", dashboard_up=False)
    out = r.stdout + r.stderr
    check("the dashboard (stub) is started on DASH_PORT",
          r.returncode == 0 and os.path.exists(os.path.join(r.home, "server-ran"))
          and "stub dashboard on port 17788" in out, (r.returncode, out))
    check("mlx-server.sh is never run (the model is on demand)",
          not os.path.exists(os.path.join(r.home, "mlx-server-ran")), out)
    check("nothing polls the model port", ":8080" not in r.calls, r.calls)
    check("...and no sleep loop runs (at most the one browser-open delay)",
          r.calls.count("sleep ") <= 1, r.calls)
    check("nothing is logged into the repo (no dashboard/logs)",
          not os.path.exists(os.path.join(r.install, "dashboard", "logs")),
          os.listdir(os.path.join(r.install, "dashboard")))
    log = read(os.path.join(r.home, ".hermes", "logs", "dashboard.log")) or ""
    check("the dashboard's output lands in ~/.hermes/logs/dashboard.log",
          "stub dashboard on port 17788" in log, log)
    check("the browser is pointed at the dashboard", "open http://127.0.0.1:17788" in r.calls, r.calls)

    print("\n2. with the dashboard already answering, it only opens the browser")
    r = run("warm", dashboard_up=True)
    out = r.stdout + r.stderr
    check("it exits 0 without starting a second server.py",
          r.returncode == 0 and not os.path.exists(os.path.join(r.home, "server-ran")), (r.returncode, out))
    check("...asking the dashboard, not the model",
          "http://127.0.0.1:17788/api/version" in r.calls and ":8080" not in r.calls, r.calls)
    check("...and opens it", "open http://127.0.0.1:17788" in r.calls, r.calls)
    check("...and never runs mlx-server.sh",
          not os.path.exists(os.path.join(r.home, "mlx-server-ran")), out)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
