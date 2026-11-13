#!/usr/bin/env python3
"""Regression test for C83 (and the runner half of C82) —
docs/audits/2026-10-21-codebase-audit.md.

C83 — tools/tests/run.sh credited what it never checked:
  * a suite printing "TESTS 0 passed 0 failed" and exiting 0 was a PASS;
  * any line starting with SKIP (with rc 0) won over everything else, so
    "SKIP ..." followed by "TESTS 2 passed 3 failed" was reported as a SKIP and
    the run exited 0;
  * skipped checks were invisible — the suites printed "  --  ... skipped" or
    "  skip ..." and the check count simply came out smaller, so CI (no
    ~/.hermes on the runner) ran a smaller suite than the owner's Mac and
    nobody could tell.
Now skips are counted in their own column and listed with their reasons, a
SKIP line never hides a failure, "TESTS 0 passed" is a FAIL, and
HERMES_TESTS_FAIL_ON_SKIP=1 (CI) fails every skip not on
HERMES_TESTS_EXPECTED_SKIPS.

C82 (runner half) — run.sh's guard self-check imported usercustomize itself,
so a python3 that skipped it at start-up (here: a `python3 -s` wrapper on
PATH, the venv/PYTHONNOUSERSITE case) still read as "offline guard active",
and strict mode ran the tier unguarded. The same for node with NODE_OPTIONS
dropped.

The suite drives the REAL run.sh: it copies run.sh and lib/ into a throwaway
tools/tests/ tree with fake suites and runs `run.sh unit` there.  Nothing
outside that tree is touched; the fake suites make no connections.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile

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
        # quote the inner run's output: a bare "TESTS ..." or "SKIP ..." line
        # in it must never be read by the OUTER runner as this suite's own
        print("  FAIL %s\n    | %s" % (name, str(extra)[-600:].replace("\n", "\n    | ")))


SUITES = {
    "t_pass.py": 'print("  ok   a\\n  ok   b\\n  ok   c")\nprint("TESTS 3 passed 0 failed")\n',
    "t_zero.py": 'print("TESTS 0 passed 0 failed")\n',
    "t_skip_hides.py": 'print("SKIP: something is missing")\n'
                       'print("TESTS 2 passed 3 failed")\n',
    "t_suite_skip.py": 'print("SKIP: this machine has no frobnicator")\n',
    "t_sub_skip.py": 'print("  ok   one")\nprint("  SKIP the owner-only snapshot checks")\n'
                     'print("TESTS 4 passed 0 failed")\n',
    "t_ext.py": 'print("TESTS 4 passed 0 failed 2 skipped")\n',
    "t_quiet.py": 'import sys\nsys.exit(0)\n',
}

ROOTS = []


def tree(names):
    root = tempfile.mkdtemp(prefix="t-c83-")
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


def wrapper(root, name, body):
    d = os.path.join(root, "bin")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return d


# The inner run.sh's model-wake guard samples the REAL process table (pgrep -f)
# before and after; any unrelated process whose command line merely mentions
# the model server (another shell's grep, a model the owner's Telegram turn
# woke) made the inner run fail at random. That guard is not under test here
# and the OUTER run.sh still guards this suite, so the inner one sees a pgrep
# that always answers "no model server".
NOPGREP = tempfile.mkdtemp(prefix="t-c83-pgrep-")
ROOTS.append(NOPGREP)
with open(os.path.join(NOPGREP, "pgrep"), "w") as _f:
    _f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(NOPGREP, "pgrep"), 0o755)


def run(root, path_prefix=None, tier="unit", **env_extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CI", "HERMES_TESTS_STRICT", "HERMES_TESTS_FAIL_ON_SKIP",
                        "HERMES_TESTS_EXPECTED_SKIPS", "HERMES_TESTS_OFFLINE",
                        "PYTHONPATH", "NODE_OPTIONS", "HERMES_REPO",
                        "HERMES_TESTS_EGRESS_LOG", "HERMES_TESTS_SUITE")}
    env.update(env_extra)
    env["PATH"] = NOPGREP + os.pathsep + env.get("PATH", "")
    if path_prefix:
        env["PATH"] = path_prefix + os.pathsep + env["PATH"]
    p = subprocess.run(["bash", os.path.join(root, "tools", "tests", "run.sh"), tier],
                       env=env, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr


def row(out, suite):
    for ln in out.splitlines():
        bits = ln.split()
        if len(bits) >= 2 and bits[0] == "unit" and bits[1] == suite:
            return bits
    return []


# ---------------------------------------------------------------------------
print("\n1. a skip is counted and listed, never credited (default: reported only)")
# ---------------------------------------------------------------------------
root = tree(["t_pass.py", "t_sub_skip.py", "t_suite_skip.py", "t_ext.py"])
rc, out = run(root)
check("skips alone do not fail a normal run", rc == 0, out)
r = row(out, "t_sub_skip.py")
check("a suite with a skipped check is PASS with SKIP=1", r[2:6] == ["4", "0", "1", "PASS"], r)
r = row(out, "t_ext.py")
check("the 'TESTS p passed f failed s skipped' form is read", r[2:6] == ["4", "0", "2", "PASS"], r)
r = row(out, "t_suite_skip.py")
check("a whole-suite SKIP is SKIP, not PASS", r[2:6] == ["0", "0", "1", "SKIP"], r)
tot = [ln.split() for ln in out.splitlines() if ln.split()[:1] == ["TOTAL"]]
check("TOTAL carries the skip count", tot and tot[-1][1:4] == ["11", "0", "4"], tot)
check("every skip is listed with its reason",
      "t_sub_skip.py — the owner-only snapshot checks" in out
      and "t_suite_skip.py — this machine has no frobnicator" in out
      and "t_ext.py — 2 check(s) skipped" in out, out)

# ---------------------------------------------------------------------------
print("\n2. HERMES_TESTS_FAIL_ON_SKIP=1: an unexpected skip fails the run")
# ---------------------------------------------------------------------------
rc, out = run(root, HERMES_TESTS_FAIL_ON_SKIP="1")
check("unexpected skips fail the run", rc != 0, out)
check("each unexpected skip is named",
      out.count("(unexpected SKIP:") == 3, out)
rc, out = run(root, HERMES_TESTS_FAIL_ON_SKIP="1", HERMES_TESTS_EXPECTED_SKIPS=(
    "t_sub_skip.py: owner-only snapshot\n"
    "  t_suite_skip.py  \n"
    "# a comment line\n"
    "t_ext.py"))
check("skips on the expected list pass", rc == 0, out)
check("expected skips are still listed, marked (expected)", out.count("(expected)") == 3, out)
rc, out = run(root, HERMES_TESTS_FAIL_ON_SKIP="1",
              HERMES_TESTS_EXPECTED_SKIPS="t_sub_skip.py: a different reason;t_suite_skip.py;t_ext.py")
check("an entry with the wrong reason does not cover the skip", rc != 0
      and "t_sub_skip.py (unexpected SKIP" in out, out)
rc, out = run(root, HERMES_TESTS_FAIL_ON_SKIP="1",
              HERMES_TESTS_EXPECTED_SKIPS="t_sub_skip.py: owner-only;t_suite_skip.py;t_ext.py;t_pass.py")
check("';' separates entries too", rc == 0, out)

# ---------------------------------------------------------------------------
print("\n3. nothing that did not assert can pass")
# ---------------------------------------------------------------------------
rc, out = run(tree(["t_pass.py", "t_zero.py"]))
check("'TESTS 0 passed 0 failed' fails the run", rc != 0, out)
check("... and says why", "t_zero.py (TESTS 0 passed: no assertion ran)" in out, out)
check("... and is a FAIL row", row(out, "t_zero.py")[5:6] == ["FAIL"], row(out, "t_zero.py"))
rc, out = run(tree(["t_pass.py", "t_skip_hides.py"]))
check("a SKIP line no longer hides 3 failures", rc != 0
      and row(out, "t_skip_hides.py")[5:6] == ["FAIL"], out)
rc, out = run(tree(["t_pass.py", "t_quiet.py"]))
check("a suite with no summary line still fails", rc != 0
      and "t_quiet.py (no TESTS summary line)" in out, out)
rc, out = run(tree(["t_pass.py"]))
check("a normal suite still passes", rc == 0 and row(out, "t_pass.py")[2:6] == ["3", "0", "0", "PASS"],
      out)

# ---------------------------------------------------------------------------
print("\n4. the runner's guard self-check is not vacuous (C82)")
# ---------------------------------------------------------------------------
root = tree(["t_pass.py"])
bindir = wrapper(root, "python3", 'exec "%s" -s "$@"' % sys.executable)
rc, out = run(root, path_prefix=bindir, HERMES_TESTS_STRICT="1")
check("strict: a python3 that skipped usercustomize at start-up refuses the tier",
      rc != 0 and "the offline guard did not load" in out, out)
check("... and never ran the suites", row(out, "t_pass.py") == [], out)
rc, out = run(root, path_prefix=bindir)
check("non-strict: it says so loudly", "WARNING: this python3 did not load" in out, out)
nodebin = shutil.which("node")
root = tree(["t_pass.py"])
bindir = wrapper(root, "node", 'unset NODE_OPTIONS; exec "%s" "$@"' % nodebin)
rc, out = run(root, path_prefix=bindir, HERMES_TESTS_STRICT="1")
check("strict: a node that lost the preload refuses the tier",
      rc != 0 and "the node offline guard did not load" in out, out)
rc, out = run(root, path_prefix=None, HERMES_TESTS_STRICT="1")
check("strict: a healthy python3 + node run passes", rc == 0
      and "offline guard active" in out and "node offline guard preloaded" in out, out)
rc, out = run(root, path_prefix=None, HERMES_TESTS_STRICT="1", PYTHONNOUSERSITE="1")
check("run.sh unsets PYTHONNOUSERSITE for the tier (the guard is a usercustomize)",
      rc == 0, out)

# ---------------------------------------------------------------------------
print("\n5. in `all`, a live suite talks to the dashboard run.sh checked")
# ---------------------------------------------------------------------------
# do_unit unsets HERMES_DASH_BASE on its way out, so in `all` a live suite fell
# back to its own default — the owner's live :7788 — even when the run was
# pointed at another dashboard (verify_round2.sh toggles the escalation switch)
import http.server  # noqa: E402
import threading  # noqa: E402


class _Dash(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


_srv = http.server.HTTPServer(("127.0.0.1", 0), _Dash)     # loopback, port 0
threading.Thread(target=_srv.serve_forever, daemon=True).start()
_base = "http://127.0.0.1:%d" % _srv.server_address[1]
root = tree(["t_pass.py"])
os.makedirs(os.path.join(root, "tools", "tests", "live"))
with open(os.path.join(root, "tools", "tests", "live", "t_base.sh"), "w") as f:
    f.write('echo "  base=${HERMES_DASH_BASE:-unset}"\n'
            'if [ "${HERMES_DASH_BASE:-}" = "%s" ]; then echo "TESTS 1 passed 0 failed"\n'
            'else echo "TESTS 0 passed 1 failed"; exit 1; fi\n' % _base)
rc, out = run(root, tier="all", HERMES_DASH_BASE=_base, PWENV="/nonexistent")
check("the live suite got HERMES_DASH_BASE=$BASE (not unset, not the unit tier's port)",
      rc == 0 and ("base=%s" % _base) in out, out)
_srv.shutdown()

for r_ in ROOTS:
    shutil.rmtree(r_, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
