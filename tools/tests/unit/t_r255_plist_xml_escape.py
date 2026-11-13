#!/usr/bin/env python3
"""Regression test for R2-55 (2026-10-08 audit, install prose).

install-services.sh interpolated $HERE, $HOME, $LOGS, $SVC_PATH (and $PY,
the serve token) raw into its four plist heredocs, so an install path or a
home folder containing '&' or '<' produced invalid XML and `launchctl
bootstrap` failed.

This runs the WHOLE shipped install-services.sh — copied into an install dir
whose path holds & < > " and ', with a throwaway HOME holding the same — and
`launchctl` and `sleep` shimmed on PATH (they only record their arguments).
Nothing reaches launchd and nothing outside the temp dir is written. Every
plist must then parse, and every value must round-trip to the real path.
"""
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
INSTALL = os.path.join(REPO, "install-services.sh")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s\n    | %s" % (name, str(extra)[-1500:]))


TMP = tempfile.mkdtemp(prefix="t-r255-")
ODD = "R&D <x> \"q\" it's"
try:
    here = os.path.join(TMP, ODD, "Hermes")
    home = os.path.join(TMP, "home & " + ODD)
    shim = os.path.join(TMP, "bin")
    for d in (here, home, shim):
        os.makedirs(d)
    shutil.copy(INSTALL, os.path.join(here, "install-services.sh"))
    calls = os.path.join(TMP, "launchctl.calls")
    with open(os.path.join(shim, "launchctl"), "w") as f:
        f.write('#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\nexit 0\n' % calls)
    with open(os.path.join(shim, "sleep"), "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    for n in ("launchctl", "sleep"):
        os.chmod(os.path.join(shim, n), 0o755)
    env = {k: v for k, v in os.environ.items() if k != "HERMES_MODEL_ALWAYS_ON"}
    env.update({"HOME": home,
                "PATH": ":".join([shim, os.path.dirname(sys.executable), "/usr/bin", "/bin",
                                  "/usr/sbin", "/sbin"])})
    which = subprocess.run(["bash", "-c", "command -v launchctl"], env=env,
                           capture_output=True, text=True).stdout.strip()
    if which != os.path.join(shim, "launchctl"):
        print("  refusing to run: launchctl does not resolve to the shim (%r)" % which)
        sys.exit(1)

    print("\n1. install-services.sh under an install path and HOME holding & < > \" '")
    r = subprocess.run(["bash", os.path.join(here, "install-services.sh")], env=env,
                       capture_output=True, text=True, timeout=60, cwd=here)
    check("the script runs to the end", r.returncode == 0 and "installed" in r.stdout,
          (r.returncode, r.stdout, r.stderr))
    agents = os.path.join(home, "Library", "LaunchAgents")
    logs = os.path.join(home, ".hermes", "logs")
    py = subprocess.run(["bash", "-c", "command -v python3"], env=env,
                        capture_output=True, text=True).stdout.strip()
    with open(os.path.join(home, ".hermes", "dashboard", "serve-token")) as f:
        token = f.read().strip()
    want = {
        "com.hermes.mlx-server": (["/bin/bash", os.path.join(here, "mlx-server.sh")], here,
                                  "mlx-server.log"),
        "com.hermes.mlx-bg": (["/bin/bash", os.path.join(here, "mlx-server-bg.sh")], here,
                              "mlx-bg.log"),
        "com.hermes.dashboard": ([py, os.path.join(here, "dashboard", "server.py")], here,
                                 "dashboard.log"),
        "com.hermes.serve": ([os.path.join(home, ".local", "bin", "hermes"), "serve", "--port",
                              "9119", "--host", "127.0.0.1"], home, "serve.log"),
    }
    for label, (args, wd, log) in want.items():
        path = os.path.join(agents, label + ".plist")
        try:
            with open(path, "rb") as f:
                pl = plistlib.load(f)
        except Exception as e:                      # noqa: BLE001 - reported
            pl = None
            err = "%s: %s" % (type(e).__name__, e)
        check("%s: the plist is valid XML" % label, pl is not None,
              err if pl is None else "")
        if pl is None:
            continue
        envv = pl.get("EnvironmentVariables") or {}
        check("%s: ProgramArguments round-trip to the real paths" % label,
              pl.get("ProgramArguments") == args, (pl.get("ProgramArguments"), args))
        check("%s: WorkingDirectory, HOME and the log path round-trip" % label,
              pl.get("WorkingDirectory") == wd and envv.get("HOME") == home
              and pl.get("StandardOutPath") == os.path.join(logs, log)
              and pl.get("StandardErrorPath") == os.path.join(logs, log),
              (pl.get("WorkingDirectory"), envv.get("HOME"), pl.get("StandardOutPath")))
        check("%s: PATH round-trips (python's dir, then ~/.local/bin)" % label,
              (envv.get("PATH") or "").split(":")[:2]
              == [os.path.dirname(py), os.path.join(home, ".local", "bin")], envv.get("PATH"))
        if label == "com.hermes.serve":
            check("serve: the token round-trips", envv.get("HERMES_DASHBOARD_SESSION_TOKEN") == token)
        if os.path.exists("/usr/bin/plutil"):
            lint = subprocess.run(["/usr/bin/plutil", "-lint", path], capture_output=True, text=True)
            check("%s: plutil -lint agrees" % label, lint.returncode == 0, lint.stdout + lint.stderr)
    boots = open(calls).read() if os.path.exists(calls) else ""
    check("all four plists went to (the shimmed) launchctl bootstrap",
          sum(1 for ln in boots.splitlines() if ln.startswith("bootstrap ")) == 4, boots)
    check("the summary still prints the raw (unescaped) log dir",
          logs in r.stdout and "&amp;" not in r.stdout, r.stdout)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
