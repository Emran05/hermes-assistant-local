#!/usr/bin/env python3
"""2026-10-08 audit, P3 (install): install-services.sh bootstrapped the model
plists before the dashboard, so under `set -e` one "Bootstrap failed: 5" on a
model plist (a common launchd hiccup right after a bootout) ended the script
and left the always-on hub and serve unloaded.

The reload section is cut out of the SHIPPED install-services.sh and run in
bash with `launchctl`, `unload` and `sleep` replaced by shell functions that
only record their arguments — the stub fails every model-plist bootstrap.
Nothing reaches launchd.
"""
import io, os, re, shutil, subprocess, sys, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
INSTALL = os.path.join(REPO, "install-services.sh")
FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def reload_section():
    src = io.open(INSTALL, encoding="utf-8").read()
    # since E129 the section starts with the model-plist staging helpers and
    # ends where the per-lane note helper begins
    m = re.search(r'\n(job_loaded\(\) \{.*?)\nmodel_note\(\) \{', src, re.S)
    return m.group(1) if m else None


def main():
    tmp = tempfile.mkdtemp(prefix="t-bp3-")
    try:
        sec = reload_section()
        check("the reload section is found", sec is not None)
        if sec is None:
            return
        boots = re.findall(r'launchctl bootstrap "gui/\$UID_N" "\$(\w+)"', sec)
        check("all four plists are bootstrapped", sorted(boots) == ["BG_PLIST", "DASH_PLIST", "MLX_PLIST", "SERVE_PLIST"], boots)
        check("the always-on pair comes first", boots[:2] == ["DASH_PLIST", "SERVE_PLIST"], boots)
        log = os.path.join(tmp, "calls")
        stub = (
            'set -euo pipefail\n'
            'LOG=%s\n'
            'T=%s\n'
            'UID_N=501; MLX_PLIST=$T/mlx.plist; BG_PLIST=$T/bg.plist; DASH_PLIST=$T/dash.plist; SERVE_PLIST=$T/serve.plist\n'
            'MLX_NEW=$T/mlx.new; BG_NEW=$T/bg.new; echo a > $MLX_NEW; echo b > $BG_NEW\n'
            'MLX_LABEL=m; BG_LABEL=b; DASH_LABEL=d; SERVE_LABEL=s\n'
            'unload() { :; }\n'
            'sleep() { :; }\n'
            'launchctl() { echo "$*" >> "$LOG"; case "$*" in print*) return 113;; *mlx*|*bg.plist*) echo "Bootstrap failed: 5: Input/output error" >&2; return 5;; esac; }\n'
            % (repr(log), repr(tmp))) + sec
        r = subprocess.run(["/bin/bash", "-c", stub], capture_output=True, text=True, timeout=30)
        calls = io.open(log).read() if os.path.exists(log) else ""
        check("a failing model bootstrap still stops the script (set -e is kept)", r.returncode != 0, r.returncode)
        check("but the dashboard was loaded before it", "dash.plist" in calls, calls)
        check("and so was serve", "serve.plist" in calls, calls)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
