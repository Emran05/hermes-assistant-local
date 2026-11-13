#!/usr/bin/env python3
"""Review R4-seams (S2-3): the unit tier cannot read the owner's data through
a system tool either.

S2-3 moved five suites off the owner's files (conversations, recorder.db,
config.yaml, the calendar via icalBuddy). A run of the whole tier under an
audit hook showed what was left: ny_harness.py, t_e18_lost_reply_notice.py and
t_e69_session_case.py each started the REAL /usr/bin/shortcuts (the owner's
Shortcuts list) through access_preamble -> aux_shortcuts._sb_installed().
Nothing in the tier refused it: the offline guard covered sockets only.

The guard (tools/tests/lib/usercustomize.py) now refuses the installed
`shortcuts`, `osascript` and `icalBuddy` for every Python process of the tier,
as a PermissionError the code under test treats like any failed tool, and
logs it like a refused connection. A stub of the same name in a scratch
folder still runs (that is how the suites fake these tools).

Each check runs a FRESH interpreter with the tier's environment, the way
guard_selfcheck.py does. The refusal happens before anything is started, so
nothing here runs the real tools. Offline; scratch HOME.
"""
import os
import subprocess
import sys
import tempfile

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
LIB = os.path.join(ROOT, "tools", "tests", "lib")
TMP = tempfile.mkdtemp(prefix="hermes-s3-tools-")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:500]))


def fresh(code, guard=True, extra_env=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONNOUSERSITE", "HERMES_TESTS_EGRESS_LOG")}
    env.update({"PYTHONPATH": LIB, "HOME": TMP, "HERMES_REPO": ROOT})
    if guard:
        env["HERMES_TESTS_OFFLINE"] = "1"
    else:
        env.pop("HERMES_TESTS_OFFLINE", None)
    env.update(extra_env or {})
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


# a recorder instead of the real spawn: whatever the guard lets through lands
# here, so these checks never start the owner's tools even on unguarded code
PROBE = r'''
import os, subprocess, sys
import _posixsubprocess
real_fork = _posixsubprocess.fork_exec
started = []
def fake_fork(args, executable_list, *rest):
    started.append([os.fsdecode(a) for a in args][:2])
    raise OSError(2, "probe: not started")
_posixsubprocess.fork_exec = fake_fork
subprocess._fork_exec = fake_fork
subprocess._USE_POSIX_SPAWN = False
subprocess._USE_VFORK = False
out = []
for argv in %(cases)r:
    try:
        subprocess.run(argv, capture_output=True, timeout=5)
        out.append("ran")
    except PermissionError as e:
        out.append("refused:" + ("guard" if "HERMES_TESTS_OFFLINE" in str(e) else "other"))
    except OSError as e:
        out.append("reached-spawn")
print("|".join(out))
print("STARTED %%d" %% len(started))
'''

INSTALLED = [["/usr/bin/shortcuts", "list", "--show-identifiers"],
             ["/usr/bin/osascript", "-e", "return 1"],
             ["/opt/homebrew/bin/icalBuddy", "eventsToday"],
             ["/usr/local/bin/icalBuddy", "eventsToday"]]

print("== the installed tools are refused before anything starts ==")
rc, out, err = fresh(PROBE % {"cases": INSTALLED})
lines = out.splitlines()
check("the probe ran", rc == 0 and len(lines) == 2, (rc, out, err[-300:]))
got = (lines[0] if lines else "").split("|")
for argv, res in zip(INSTALLED, got + [""] * 4):
    check("%s is refused by the guard" % argv[0], res == "refused:guard", res)
check("...and none reached the spawn", (lines[1:] or [""])[0] == "STARTED 0", lines[1:])

print("\n== by bare name too (PATH lookup), and through a shell string ==")
rc, out, err = fresh(PROBE % {"cases": [["osascript", "-e", "return 1"], ["shortcuts", "list"]]},
                     extra_env={"PATH": "/usr/bin:/bin"})
got = (out.splitlines() or [""])[0].split("|")
check("a bare `osascript` found on PATH in /usr/bin is refused", got[:1] == ["refused:guard"], (out, err[-200:]))
check("a bare `shortcuts` found on PATH in /usr/bin is refused", got[1:2] == ["refused:guard"], out)

print("\n== a suite's own stub still runs ==")
stub_dir = os.path.join(TMP, "stub-bin")
os.makedirs(stub_dir)
for name in ("shortcuts", "osascript", "icalBuddy"):
    p = os.path.join(stub_dir, name)
    with open(p, "w") as f:
        f.write("#!/bin/sh\necho stub-%s\n" % name)
    os.chmod(p, 0o755)
code = r'''
import subprocess, sys
out = []
for name in ("shortcuts", "osascript", "icalBuddy"):
    out.append(subprocess.run([sys.argv[1] + "/" + name], capture_output=True, text=True).stdout.strip())
    out.append(subprocess.run([name], capture_output=True, text=True).stdout.strip())
print("|".join(out))
'''
env = {"PATH": stub_dir + ":/usr/bin:/bin"}
p = subprocess.run([sys.executable, "-c", code, stub_dir], capture_output=True, text=True, timeout=60,
                   env=dict({k: v for k, v in os.environ.items() if k != "PYTHONNOUSERSITE"},
                            PYTHONPATH=LIB, HOME=TMP, HERMES_TESTS_OFFLINE="1", **env))
check("stubs run by path and by name (first on PATH)",
      p.stdout.strip() == "|".join("stub-%s|stub-%s" % (n, n) for n in ("shortcuts", "osascript", "icalBuddy")),
      (p.stdout, p.stderr[-300:]))

print("\n== unrelated tools are untouched, and a refusal is logged ==")
log = os.path.join(TMP, "egress.log")
code = r'''
import subprocess
print(subprocess.run(["/bin/echo", "hi"], capture_output=True, text=True).stdout.strip())
print(subprocess.run(["/usr/bin/true"]).returncode)
try:
    subprocess.run(["/usr/bin/osascript", "-e", "return 1"], capture_output=True)
except PermissionError:
    print("refused")
'''
rc, out, err = fresh(code, extra_env={"HERMES_TESTS_EGRESS_LOG": log, "HERMES_TESTS_SUITE": "t_s3"})
check("/bin/echo and /usr/bin/true still run", out.splitlines()[:2] == ["hi", "0"], (out, err[-200:]))
logged = open(log).read() if os.path.exists(log) else ""
check("the refusal is in the egress log with the suite's name",
      "t_s3" in logged and "owner-tool /usr/bin/osascript" in logged, logged)

print("\n== the real code path: access_preamble's Shortcuts list ==")
code = r'''
import os, sys
os.makedirs(os.path.join(os.environ["HOME"], ".hermes", "dashboard", "chats"), exist_ok=True)
sys.path.insert(0, os.path.join(os.environ["HERMES_REPO"], "dashboard"))
import contextlib, io
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    import server
    items, err = server._sb_installed(force=True)
    pre = server.access_preamble("hello")
print("ITEMS %d" % len(items))
print("ERR %s" % err)
print("PRE %d" % len(pre))
'''
rc, out, err = fresh(code)
lines = out.splitlines()
check("server.py imported and the preamble was built", rc == 0 and any(l.startswith("PRE ") for l in lines),
      (rc, out[-300:], err[-500:]))
check("_sb_installed() answered with NO items", "ITEMS 0" in lines, lines)
check("...because the guard refused the tool (or this machine has none)",
      any(l.startswith("ERR ") and ("HERMES_TESTS_OFFLINE" in l or "not found" in l) for l in lines), lines)

print("\n== outside the tier the guard is inert ==")
rc, out, err = fresh(PROBE % {"cases": [["/usr/bin/osascript", "-e", "return 1"]]}, guard=False)
check("without HERMES_TESTS_OFFLINE nothing is refused by the guard",
      (out.splitlines() or [""])[0] == "reached-spawn", (out, err[-200:]))

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
