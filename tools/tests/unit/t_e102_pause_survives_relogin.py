#!/usr/bin/env python3
"""E102 (2026-11-09 audit, P3): on an always-on install a Pause survives a
re-login, a reboot or an update.

BEFORE: mlx-server.sh never read ~/.hermes/dashboard/agent-paused. With
HERMES_MODEL_ALWAYS_ON=1 the on-demand gate is skipped, launchd forgets a
bootout at logout, install-services.sh re-bootstraps the job, and the app's
blind kickstart at launch loaded the ~19 GB model while the dashboard still
said "paused" (memory guard and idle-suspend both stand down then).

AFTER: the script exits 0 before the always-on branch whenever the pause
marker exists — on-demand installs too, even with a fresh start token.

Runs the REAL gate section of mlx-server.sh (everything before
"# --- Model choice") with HOME pointed at a scratch folder; a printf
sentinel stands in for the model launch. Nothing is started.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-400:]))


def run_gate(home, always_on=False, paused=False, token_age=None):
    dash = Path(home) / ".hermes" / "dashboard"
    dash.mkdir(parents=True, exist_ok=True)
    for f in ("agent-paused", "model-start-ok"):
        try:
            (dash / f).unlink()
        except FileNotFoundError:
            pass
    if paused:
        (dash / "agent-paused").write_text(str(time.time()))
    if token_age is not None:
        tok = dash / "model-start-ok"
        tok.touch()
        old = time.time() - token_age
        os.utime(tok, (old, old))
    script = (REPO / "mlx-server.sh").read_text()
    gate = script.split("# --- Model choice", 1)[0]
    gate += '\nprintf "REACHED_MODEL_LAUNCH_SECTION\\n"\n'
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin"}
    if always_on:
        env["HERMES_MODEL_ALWAYS_ON"] = "1"
    return subprocess.run(["bash"], input=gate, text=True, capture_output=True, env=env)


def reached(r):
    return "REACHED_MODEL_LAUNCH_SECTION" in (r.stdout or "")


def main():
    r = subprocess.run(["bash", "-n", str(REPO / "mlx-server.sh")], capture_output=True, text=True)
    check("bash -n mlx-server.sh", r.returncode == 0, r.stderr)
    with tempfile.TemporaryDirectory(prefix="hermes-e102-") as home:
        r = run_gate(home, always_on=True, paused=True)
        check("always-on + PAUSED (the app's kickstart after a re-login) -> not loaded",
              not reached(r) and r.returncode == 0, (r.stdout, r.stderr))
        check("...and says why", "paused" in r.stdout, r.stdout)
        r = run_gate(home, always_on=False, paused=True, token_age=5)
        check("on-demand + PAUSED + a fresh start token -> not loaded (the pause wins)",
              not reached(r), r.stdout)
        r = run_gate(home, always_on=True, paused=False)
        check("control: always-on, not paused -> loads (unchanged)", reached(r), r.stdout)
        r = run_gate(home, always_on=False, paused=False, token_age=5)
        check("control: on-demand, fresh token, not paused -> loads (unchanged)",
              reached(r), r.stdout)
        r = run_gate(home, always_on=False, paused=False)
        check("control: on-demand, no token -> refused (unchanged)", not reached(r), r.stdout)
        tok = Path(home) / ".hermes" / "dashboard" / "model-start-ok"
        run_gate(home, always_on=False, paused=True, token_age=5)
        check("a refused-by-pause start does not consume the token it never used",
              tok.exists())
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
