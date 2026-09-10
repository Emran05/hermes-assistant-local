#!/usr/bin/env python3
"""Regression test for audit finding A09 (docs/audits/2026-09-10-codebase-audit.md).

BEFORE: mlx-server.sh refused an unsolicited start ONLY when
~/.hermes/dashboard/model-autostart-off already existed, and no installer
ever created that marker — so a FRESH install (no marker, no state at all)
fell through to always-on: app/main.swift's blind `launchctl kickstart` of
com.hermes.mlx-server at every app launch could load the ~18GB model with no
chat request behind it. (docs/audits/reproduce_20260910.py's
fresh_install_start_gate() demonstrated exactly this: no marker -> the gate
fell straight through to "REACHED_MODEL_LAUNCH_SECTION".)

AFTER:
  * mlx-server.sh's on-demand gate is unconditional — a fresh (<=180s) start
    token is required whether or not the marker file exists — unless
    HERMES_MODEL_ALWAYS_ON=1 is set, which skips the gate entirely.
  * install.sh and install-services.sh create the marker by default on a
    genuinely fresh install (no settings.json, no marker yet); an existing
    install (settings.json already present) is left untouched.
  * HERMES_MODEL_ALWAYS_ON=1 makes the choice sticky via a second marker
    (model-always-on) so a later reinstall without the env var does not
    silently flip an opted-in owner back to on-demand.

This suite runs the REAL shell snippets (extracted verbatim out of
mlx-server.sh, install.sh and install-services.sh) against temp HERMES homes.
It never starts a real model — the "launch" is a printf sentinel appended
after the extracted gate, exactly like the audit's own probe — and never
touches this Mac's real ~/.hermes.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def extract_between(text, start, end):
    assert start in text, "start marker not found: %r" % (start,)
    assert end in text, "end marker not found: %r" % (end,)
    return text.split(start, 1)[1].split(end, 1)[0]


# ---------------------------------------------------------------------------
# mlx-server.sh's own gate
# ---------------------------------------------------------------------------
def run_gate(scratch, env_extra=None, token_age=None, touch_marker=False):
    """Run just mlx-server.sh's on-demand gate (everything before
    "# --- Model choice") against `scratch` as the HERMES home, exactly the
    way the audit's reproduction script isolates it. Returns the
    CompletedProcess."""
    script = (REPO / "mlx-server.sh").read_text()
    gate = script.split("# --- Model choice", 1)[0]
    marker = scratch / "model-autostart-off"
    token = scratch / "model-start-ok"
    gate = gate.replace('"$HOME/.hermes/dashboard/model-autostart-off"',
                        "'" + str(marker) + "'")
    gate = gate.replace('"$HOME/.hermes/dashboard/model-start-ok"',
                        "'" + str(token) + "'")
    if touch_marker:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    if token_age is not None:
        token.parent.mkdir(parents=True, exist_ok=True)
        token.touch()
        old = time.time() - token_age
        os.utime(token, (old, old))
    gate += '\nprintf "REACHED_MODEL_LAUNCH_SECTION\\n"\n'
    env = dict(os.environ)
    env.pop("HERMES_MODEL_ALWAYS_ON", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash"], input=gate, text=True, capture_output=True,
                          env=env)


def reached(result):
    return "REACHED_MODEL_LAUNCH_SECTION" in (result.stdout or "")


def test_gate():
    with tempfile.TemporaryDirectory(prefix="hermes-a09-gate-") as d:
        scratch = Path(d)

        r = run_gate(scratch)
        check("fresh install (no marker, no token) -> refuses (was: always starts)",
              not reached(r), (r.stdout, r.stderr))
        check("...exits 0 (a refusal, not a crash)", r.returncode == 0, r.returncode)

        r = run_gate(scratch, token_age=5)
        check("fresh install + a FRESH start token -> starts", reached(r), r.stdout)

        r = run_gate(scratch, token_age=500)
        check("a STALE (>180s) start token is not honored -> refuses",
              not reached(r), r.stdout)

        # Existing-machine parity: behavior with the marker present must be
        # byte-identical to before this fix (this Mac already has the marker).
        r = run_gate(scratch, touch_marker=True)
        check("marker present, no token -> still refuses (unchanged)",
              not reached(r), r.stdout)
        r = run_gate(scratch, touch_marker=True, token_age=5)
        check("marker present, fresh token -> still starts (unchanged)",
              reached(r), r.stdout)

        r = run_gate(scratch, env_extra={"HERMES_MODEL_ALWAYS_ON": "1"})
        check("HERMES_MODEL_ALWAYS_ON=1 skips the gate entirely, no marker/token needed",
              reached(r), r.stdout)


# ---------------------------------------------------------------------------
# the installers create the marker on a fresh install
# ---------------------------------------------------------------------------
# Each block only needs the surrounding script's OWN preexisting variables
# and helper functions stubbed — the block itself is pasted verbatim.
_PREAMBLES = {
    "install.sh": (
        'HERMES_DIR="$HOME/.hermes"\n'
        'DRY=0\n'
        'oky(){ :; }\n'
        'plan(){ :; }\n'
        'info(){ :; }\n'
    ),
    "install-services.sh": "",
}


def run_installer_marker_block(relative, start, end, home, env_extra=None):
    text = (REPO / relative).read_text()
    block = extract_between(text, start, end)
    script = "set -euo pipefail\nHOME=%s\n%s\n%s\necho DONE\n" % (
        str(home), _PREAMBLES[relative], block)
    env = dict(os.environ)
    env.pop("HERMES_MODEL_ALWAYS_ON", None)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(["bash"], input=script, text=True, capture_output=True, env=env)
    return r


INSTALL_SH_START = "# --- model on-demand default (2026-09-10 audit A09) -------------------------"
INSTALL_SH_END = "seed() {   # seed <template> <dest> <mode>"
INSTALL_SVC_START = "# model on-demand default (2026-09-10 audit A09)"
INSTALL_SVC_END = "\nunload() {"


def _marker_paths(home):
    d = home / ".hermes" / "dashboard"
    return d / "model-autostart-off", d / "model-always-on", d / "settings.json"


def test_install_sh_marker():
    for label, start, end in (
        ("install.sh", INSTALL_SH_START, INSTALL_SH_END),
        ("install-services.sh", INSTALL_SVC_START, INSTALL_SVC_END),
    ):
        with tempfile.TemporaryDirectory(prefix="hermes-a09-inst-") as d:
            home = Path(d)
            autostart, always_on, settings = _marker_paths(home)

            r = run_installer_marker_block(label, start, end, home)
            check("%s: fresh install (no settings.json yet) creates the on-demand marker" % label,
                  r.returncode == 0 and autostart.exists(), (r.returncode, r.stdout, r.stderr))
            check("%s: does not create the always-on marker by default" % label,
                  not always_on.exists())

        with tempfile.TemporaryDirectory(prefix="hermes-a09-inst2-") as d:
            home = Path(d)
            autostart, always_on, settings = _marker_paths(home)
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text("{}")

            r = run_installer_marker_block(label, start, end, home)
            check("%s: an EXISTING install (settings.json present) is left untouched"
                  % label, r.returncode == 0 and not autostart.exists(),
                  (r.returncode, r.stdout, r.stderr))

        with tempfile.TemporaryDirectory(prefix="hermes-a09-inst3-") as d:
            home = Path(d)
            autostart, always_on, settings = _marker_paths(home)
            autostart.parent.mkdir(parents=True, exist_ok=True)
            autostart.touch()   # simulates THIS Mac: marker already present

            r = run_installer_marker_block(label, start, end, home)
            check("%s: a Mac that already has the marker keeps it, unchanged" % label,
                  r.returncode == 0 and autostart.exists(),
                  (r.returncode, r.stdout, r.stderr))

        with tempfile.TemporaryDirectory(prefix="hermes-a09-inst4-") as d:
            home = Path(d)
            autostart, always_on, settings = _marker_paths(home)
            autostart.parent.mkdir(parents=True, exist_ok=True)
            autostart.touch()   # a prior on-demand install...

            r = run_installer_marker_block(label, start, end, home,
                                            env_extra={"HERMES_MODEL_ALWAYS_ON": "1"})
            check("%s: HERMES_MODEL_ALWAYS_ON=1 removes the on-demand marker" % label,
                  r.returncode == 0 and not autostart.exists(),
                  (r.returncode, r.stdout, r.stderr))
            check("%s: ...and writes the sticky always-on marker" % label,
                  always_on.exists())

            # a LATER rerun with no env var must stay always-on (sticky)
            r2 = run_installer_marker_block(label, start, end, home)
            check("%s: a later rerun without the env var stays always-on (sticky)"
                  % label, r2.returncode == 0 and not autostart.exists() and always_on.exists(),
                  (r2.returncode, r2.stdout, r2.stderr))


def main():
    print("A09: fresh installs can start the model merely by opening the app "
          "(docs/audits/2026-09-10-codebase-audit.md)")
    test_gate()
    test_install_sh_marker()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
