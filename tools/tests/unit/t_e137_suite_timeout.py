#!/usr/bin/env python3
"""Regression test for E137 — docs/audits/2026-11-09-codebase-audit.md.

tools/tests/run.sh ran every suite with no time limit, so one suite that
blocked (a stuck socket read, a child that kept the pipe to tee open) hung a
local run forever and burned CI's six-hour default.  Now each suite runs under
HERMES_TESTS_SUITE_TIMEOUT (default 600 s) in its own process group; on expiry
the whole group is stopped and the suite is a FAIL with its name on it.

Drives the REAL run.sh on a throwaway tools/tests/ tree with fake suites (the
t_c83_runner pattern).  Nothing outside that tree is touched.
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
TESTS = os.path.join(REPO, "tools", "tests")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        # quoted: the inner run's own TESTS/SKIP lines must never be read as ours
        print("  FAIL %s\n    | %s" % (name, str(extra)[-800:].replace("\n", "\n    | ")))


ROOTS = []
TOKEN = "e137-%d-%d" % (os.getpid(), int(time.time()))

SUITES = {
    # a suite that hangs AND leaves a child holding the pipe open
    "t_hang.py": (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)', '%s-child'])\n"
        "print('  ok   started')\n"
        "sys.stdout.flush()\n"
        "time.sleep(120)\n" % TOKEN),
    "t_fine.py": 'print("  ok   a")\nprint("TESTS 1 passed 0 failed")\n',
}


def tree(names):
    root = tempfile.mkdtemp(prefix="t-e137-")
    ROOTS.append(root)
    t = os.path.join(root, "tools", "tests")
    os.makedirs(os.path.join(t, "unit"))
    shutil.copy2(os.path.join(TESTS, "run.sh"), os.path.join(t, "run.sh"))
    shutil.copytree(os.path.join(TESTS, "lib"), os.path.join(t, "lib"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    for n in names:
        with open(os.path.join(t, "unit", n), "w") as f:
            f.write(SUITES[n])
    return root


# the inner run.sh's model-wake guard samples the real process table; it is not
# under test here (the OUTER run.sh still guards this suite)
NOPS = tempfile.mkdtemp(prefix="t-e137-ps-")
ROOTS.append(NOPS)
with open(os.path.join(NOPS, "ps"), "w") as _f:
    _f.write("#!/bin/sh\nexit 0\n")
os.chmod(os.path.join(NOPS, "ps"), 0o755)


def run(root, **env_extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CI", "HERMES_TESTS_STRICT", "HERMES_TESTS_FAIL_ON_SKIP",
                        "HERMES_TESTS_EXPECTED_SKIPS", "HERMES_TESTS_OFFLINE",
                        "PYTHONPATH", "NODE_OPTIONS", "HERMES_REPO",
                        "HERMES_TESTS_EGRESS_LOG", "HERMES_TESTS_SUITE",
                        "HERMES_TESTS_SUITE_TIMEOUT")}
    env.update(env_extra)
    env["PATH"] = NOPS + os.pathsep + env.get("PATH", "")
    t0 = time.time()
    p = subprocess.run(["bash", os.path.join(root, "tools", "tests", "run.sh"), "unit"],
                       env=env, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr, time.time() - t0


def row(out, suite):
    for ln in out.splitlines():
        bits = ln.split()
        if len(bits) >= 2 and bits[0] == "unit" and bits[1] == suite:
            return bits
    return []


def leftovers():
    p = subprocess.run(["/bin/ps", "-A", "-o", "pid=", "-o", "command="],
                       capture_output=True, text=True)
    return [ln for ln in p.stdout.splitlines() if TOKEN + "-child" in ln]


# ---------------------------------------------------------------------------
print("\n1. a hung suite is stopped and reported as FAIL by name")
# ---------------------------------------------------------------------------
root = tree(["t_hang.py", "t_fine.py"])
rc, out, secs = run(root, HERMES_TESTS_SUITE_TIMEOUT="3")
check("the run ends long before the suite's own 120 s (child kept the pipe open)",
      secs < 40, "%.1fs" % secs)
check("the run exits non-zero", rc == 1, out)
check("FAILED names the suite and the limit",
      "unit/t_hang.py (timed out after 3s)" in out, out)
r = row(out, "t_hang.py")
check("its summary row is FAIL", r[5:6] == ["FAIL"], r)
r = row(out, "t_fine.py")
check("the next suite still ran and passed", r[2:6] == ["1", "0", "0", "PASS"], r)
time.sleep(0.5)
check("nothing the hung suite started is left running", not leftovers(), leftovers())

# ---------------------------------------------------------------------------
print("\n2. a normal suite is untouched; the limit is env-overridable")
# ---------------------------------------------------------------------------
root = tree(["t_fine.py"])
rc, out, _ = run(root)
check("default limit: a quick suite passes, run is green",
      rc == 0 and row(out, "t_fine.py")[5:6] == ["PASS"] and "all green" in out, out)
rc, out, _ = run(root, HERMES_TESTS_SUITE_TIMEOUT="0")
check("HERMES_TESTS_SUITE_TIMEOUT=0 disables the limit and still passes",
      rc == 0 and row(out, "t_fine.py")[5:6] == ["PASS"], out)
rc, out, _ = run(root, HERMES_TESTS_SUITE_TIMEOUT="soon")
check("a non-numeric value falls back to the default instead of breaking the run",
      rc == 0 and row(out, "t_fine.py")[5:6] == ["PASS"], out)

# ---------------------------------------------------------------------------
print("\n3. a suite's own exit status still comes through the wrapper")
# ---------------------------------------------------------------------------
SUITES["t_rc.py"] = 'import sys\nprint("TESTS 2 passed 0 failed")\nsys.exit(3)\n'
root = tree(["t_rc.py"])
rc, out, _ = run(root)
check("rc!=0 with a clean summary is still a FAIL (not a timeout)",
      rc == 1 and row(out, "t_rc.py")[5:6] == ["FAIL"] and "timed out" not in out, out)

# ---------------------------------------------------------------------------
print("\n4. CI caps every job")
# ---------------------------------------------------------------------------
ci = open(os.path.join(REPO, ".github", "workflows", "ci.yml")).read()
jobs = ci.split("\njobs:\n", 1)[1]
names = [ln.strip()[:-1] for ln in jobs.splitlines()
         if ln.startswith("  ") and not ln.startswith("   ") and ln.strip().endswith(":")]
blocks = {}
cur = None
for ln in jobs.splitlines():
    if ln.startswith("  ") and not ln.startswith("   ") and ln.strip().endswith(":"):
        cur = ln.strip()[:-1]
        blocks[cur] = []
    elif cur:
        blocks[cur].append(ln)
check("ci.yml has jobs", len(names) >= 3, names)
for n in names:
    check("job %s sets timeout-minutes" % n,
          any(l.startswith("    timeout-minutes:") for l in blocks[n]), n)

for r_ in ROOTS:
    shutil.rmtree(r_, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
