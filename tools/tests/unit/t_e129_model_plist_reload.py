#!/usr/bin/env python3
"""Regression test for E129 — docs/audits/2026-11-09-codebase-audit.md.

install-services.sh (which every update runs) booted out BOTH model jobs and
bootstrapped them again, so an update stopped a running lane, and a
background lane the owner had started by hand stayed down (nothing mints its
start token). update.sh said they stayed "exactly as they were". Now a model
job whose plist did not change and that launchd already has loaded is left
alone; a changed one is reloaded and the output says so; the always-on pair
is reloaded every time.

The REAL install-services.sh runs under a scratch HOME. launchctl is a shim
that keeps a fake launchd (a file of loaded labels) and logs every call;
sleep is a no-op shim. Nothing reaches this Mac's launchd.
"""
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


TMP = tempfile.mkdtemp(prefix="t-e129-")
FX = os.path.join(TMP, "fx")
os.makedirs(FX)
LOADED = os.path.join(FX, "loaded")
CALLS = os.path.join(FX, "calls")
LAUNCHCTL = r'''#!/bin/bash
printf '%s\n' "$*" >> "$E129_FX/calls"
touch "$E129_FX/loaded"
case "$1" in
  print)     label="${2##*/}"; grep -qx "$label" "$E129_FX/loaded" && exit 0; exit 113 ;;
  bootout)   label="${2##*/}"; grep -vx "$label" "$E129_FX/loaded" > "$E129_FX/l.tmp"; mv "$E129_FX/l.tmp" "$E129_FX/loaded"; exit 0 ;;
  bootstrap) label="$(basename "$3" .plist)"; grep -qx "$label" "$E129_FX/loaded" && exit 5; echo "$label" >> "$E129_FX/loaded"; exit 0 ;;
esac
exit 0
'''
SHIM = os.path.join(TMP, "shim")
os.makedirs(SHIM)
for n, body in (("launchctl", LAUNCHCTL), ("sleep", "#!/bin/sh\nexit 0\n")):
    p = os.path.join(SHIM, n)
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)

INSTALL = os.path.join(TMP, "install")
os.makedirs(INSTALL)
shutil.copy2(os.path.join(REPO, "install-services.sh"), INSTALL)
HOME = os.path.join(TMP, "home")
os.makedirs(HOME)
MLX, BG = "com.hermes.mlx-server", "com.hermes.mlx-bg"
DASH, SERVE = "com.hermes.dashboard", "com.hermes.serve"


def run(code_unchanged=True, **extra):
    """code_unchanged: update.sh vouches that the lanes' code did not change
    (HERMES_MODEL_CODE_UNCHANGED=1) — what an update that ships no model-lane
    change passes; a plain reinstall does not"""
    open(CALLS, "w").close()
    env = {k: v for k, v in os.environ.items()
           if k not in ("HERMES_MODEL_ALWAYS_ON", "HERMES_UPDATE_FROM", "HERMES_UPD_IN_POST",
                        "HERMES_MODEL_CODE_UNCHANGED")}
    if code_unchanged:
        env["HERMES_MODEL_CODE_UNCHANGED"] = "1"
    env.update({"HOME": HOME, "E129_FX": FX,
                "PATH": ":".join([SHIM, os.path.dirname(sys.executable), "/usr/bin", "/bin",
                                  "/usr/sbin", "/sbin"])})
    env.update(extra)
    p = subprocess.run(["/bin/bash", os.path.join(INSTALL, "install-services.sh")], env=env,
                       capture_output=True, text=True, timeout=120)
    calls = open(CALLS).read().splitlines()
    return p.returncode, p.stdout + p.stderr, calls


def touched(calls, verb, label):
    return [c for c in calls if c.startswith(verb + " ") and label in c]


def loaded():
    return set(open(LOADED).read().split()) if os.path.exists(LOADED) else set()


which = subprocess.run(["bash", "-c", "command -v launchctl"],
                       env={"PATH": SHIM + ":/usr/bin:/bin"}, capture_output=True, text=True)
if which.stdout.strip() != os.path.join(SHIM, "launchctl"):
    print("  FAIL launchctl does not resolve to the shim")
    print("TESTS 0 passed 1 failed")
    sys.exit(1)

print("\n1. a fresh install loads all four")
rc, out, calls = run()
check("install-services.sh succeeds", rc == 0, out)
check("all four jobs are loaded", loaded() == {MLX, BG, DASH, SERVE}, loaded())
check("the note says both model lanes were loaded",
      "primary loaded (it was not loaded before); background loaded" in out, out)
check("no temp plist is left behind",
      [f for f in os.listdir(os.path.join(HOME, "Library", "LaunchAgents")) if f.startswith(".")]
      == [], os.listdir(os.path.join(HOME, "Library", "LaunchAgents")))

print("\n2. a re-run with unchanged plists (an ordinary update)")
rc, out, calls = run()
check("succeeds", rc == 0, out)
for lab in (MLX, BG):
    check("%s: not booted out, not bootstrapped" % lab,
          not touched(calls, "bootout", lab) and not touched(calls, "bootstrap", lab), calls)
for lab in (DASH, SERVE):
    check("%s: reloaded as before" % lab,
          touched(calls, "bootout", lab) and touched(calls, "bootstrap", lab), calls)
check("everything is still loaded", loaded() == {MLX, BG, DASH, SERVE}, loaded())
check("the note says the lanes were left as they were",
      "primary unchanged — left exactly as it was" in out
      and "background unchanged — left exactly as it was" in out, out)

print("\n2b. same plists, but nobody vouched the lanes' code is unchanged")
# a plain ./install-services.sh, an older update.sh, or an update that changed
# mlx-server.sh / mlx-vlm-launch.py: a running lane must not keep the launcher
# it started with (an always-on primary would keep it until the next reboot)
rc, out, calls = run(code_unchanged=False)
check("succeeds", rc == 0, out)
for lab in (MLX, BG):
    check("%s: reloaded" % lab,
          touched(calls, "bootout", lab) and touched(calls, "bootstrap", lab), calls)
check("the note says why, and that it stopped a running lane",
      "primary reloaded so it runs this version's code, which STOPPED it" in out
      and "start it again if you had" in out, out)
check("everything is still loaded", loaded() == {MLX, BG, DASH, SERVE}, loaded())

print("\n3. the primary's plist changes (opting in to always-on)")
rc, out, calls = run(HERMES_MODEL_ALWAYS_ON="1")
check("succeeds", rc == 0, out)
check("the primary is booted out and bootstrapped",
      touched(calls, "bootout", MLX) and touched(calls, "bootstrap", MLX), calls)
check("the background lane is not touched",
      not touched(calls, "bootout", BG) and not touched(calls, "bootstrap", BG), calls)
check("its new plist is in place",
      "HERMES_MODEL_ALWAYS_ON" in open(os.path.join(HOME, "Library", "LaunchAgents",
                                                    MLX + ".plist")).read())
check("the note says the primary was reloaded (and stopped)",
      "primary its plist changed — reloaded, which STOPPED it" in out, out)
os.remove(os.path.join(HOME, ".hermes", "dashboard", "model-always-on"))

print("\n4. a model job that is not loaded (paused) is bootstrapped, never booted out")
with open(LOADED, "w") as f:
    f.write("\n".join([BG, DASH, SERVE]) + "\n")
rc, out, calls = run()
check("succeeds", rc == 0, out)
check("the primary was loaded again without a bootout",
      touched(calls, "bootstrap", MLX) and not touched(calls, "bootout", MLX), calls)

print("\n5. the background lane's plist changes")
p = os.path.join(HOME, "Library", "LaunchAgents", BG + ".plist")
with open(p, "a") as f:
    f.write("<!-- edited -->\n")
rc, out, calls = run()
check("bg reloaded, primary untouched",
      touched(calls, "bootout", BG) and touched(calls, "bootstrap", BG)
      and not touched(calls, "bootout", MLX) and not touched(calls, "bootstrap", MLX), calls)
check("the owner is told to start a hand-started background lane again",
      "start it again if you had" in out, out)

print("\n6. --uninstall still removes everything")
rc, out, calls = run()
rc, out, calls = subprocess.run(
    ["/bin/bash", os.path.join(INSTALL, "install-services.sh"), "--uninstall"],
    env=dict(os.environ, HOME=HOME, E129_FX=FX,
             PATH=":".join([SHIM, "/usr/bin", "/bin"])),
    capture_output=True, text=True, timeout=60).returncode, "", None
check("uninstall succeeds and nothing is loaded", rc == 0 and loaded() == set(), loaded())

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
