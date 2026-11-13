#!/usr/bin/env python3
"""Regression test for E30 — docs/audits/2026-11-09-codebase-audit.md.

CI runs the unit tier with HERMES_TESTS_FAIL_ON_SKIP=1, so every SKIP a suite
prints on a fresh runner must be on ci.yml's HERMES_TESTS_EXPECTED_SKIPS.  The
round-3 suites t_r3_roster_launcher.py (venv layer, mlx-lm layer) and
t_r3_roster_catalog.py (venv cross-check) skip on any machine without
~/.hermes/mlx-vlm-venv and were not listed, so the job could not pass and
release.yml (which requires it) could not build a tag.

Checks, offline and without the venv:
  1. every entry in the list names a suite that exists, and its text occurs in
     that suite's source (a stale or misspelt entry silently matches nothing);
  2. every literal SKIP reason the venv/framework-dependent suites can print is
     matched by an entry, using run.sh's OWN matcher (skip_expected), extracted
     from the real run.sh and run in bash.
"""
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
UNIT = os.path.join(REPO, "tools", "tests", "unit")
RUNSH = os.path.join(REPO, "tools", "tests", "run.sh")
CI = os.path.join(REPO, ".github", "workflows", "ci.yml")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  -- %s" % (name, str(extra)[:400]))


def expected_list():
    text = open(CI).read()
    m = re.search(r"HERMES_TESTS_EXPECTED_SKIPS: \|\n((?:[ ]{12}\S.*\n)+)", text)
    if not m:
        return None
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]


def matcher_src():
    src = open(RUNSH).read()
    m = re.search(r"^skip_expected\(\) \{.*?^\}\n", src, re.S | re.M)
    return m.group(0) if m else None


def run_expected(entries, suite, reason):
    fn = matcher_src()
    script = fn + '\nskip_expected "$1" "$2" && echo YES || echo NO\n'
    env = dict(os.environ, HERMES_TESTS_EXPECTED_SKIPS="\n".join(entries))
    p = subprocess.run(["bash", "-c", script, "x", suite, reason],
                       capture_output=True, text=True, env=env, timeout=30)
    return p.stdout.strip() == "YES"


print("\n1. every expected-skip entry is live")
entries = expected_list()
check("ci.yml carries an HERMES_TESTS_EXPECTED_SKIPS block", bool(entries), entries)
entries = entries or []
for e in entries:
    suite, _, text = e.partition(":")
    suite, text = suite.strip(), text.strip()
    path = os.path.join(UNIT, suite)
    # implicit string concatenation across lines ("no Hermes " "install") joined
    src = re.sub(r'"\s*\n\s*"', "", open(path).read()) if os.path.isfile(path) else ""
    ok = os.path.isfile(path) and (not text or text in src)
    check("entry %r names a real suite and text it prints" % e, ok, path)

# review R4-seams: an entry kept alive only by a COMMENT is stale. Two suites
# stopped skipping (they run on synthetic data now) and kept the entry's text
# in a comment, so the check above still passed for a skip nothing can print.
for e in entries:
    suite, _, text = e.partition(":")
    suite, text = suite.strip(), text.strip()
    path = os.path.join(UNIT, suite)
    if not (text and os.path.isfile(path)):
        continue
    code = re.sub(r'"\s*\n\s*"', "", "\n".join(
        ln for ln in open(path).read().splitlines() if not ln.lstrip().startswith("#")))
    check("entry %r: its text is in the suite's code, not only in a comment" % e,
          text in code, path)
for gone in ("t_131_recorder_retention.py", "t_auxtoolbudget.py"):
    check("%s (no longer skips) has no expected-skip entry" % gone,
          not any(e.split(":")[0].strip() == gone for e in entries), entries)

print("\n2. the round-3 venv/mlx-lm skips are expected (run.sh's own matcher)")
check("run.sh's skip_expected() was found", matcher_src() is not None)
# reasons exactly as the suites print them (run.sh strips the SKIP prefix and
# cuts at 160 characters); the venv path is whatever $HOME the runner has
CASES = [
    ("t_r3_roster_launcher.py",
     "venv layer: no mlx-vlm venv at /Users/me/.hermes/mlx-vlm-venv/bin/python — the subfolder resolution, the "),
    ("t_r3_roster_launcher.py",
     "mlx-lm layer: no python with mlx_lm here — the sampling defaults on the real mlx_lm server did not run"),
    ("t_r3_roster_catalog.py",
     "venv cross-check: no mlx-vlm venv at /Users/me/.hermes/mlx-vlm-venv/bin/python"),
]
for suite, reason in CASES:
    check("%s: %s… is expected" % (suite, reason[:40]),
          matcher_src() is not None and run_expected(entries, suite, reason[:160]))
# and the matcher is not trivially true
check("an unrelated reason in the same suite is still unexpected",
      matcher_src() is not None
      and not run_expected(entries, "t_r3_roster_launcher.py", "the drift guard could not run"))

print("\n3. every literal SKIP those suites can print is covered")
for suite in ("t_r3_roster_launcher.py", "t_r3_roster_catalog.py"):
    src = open(os.path.join(UNIT, suite)).read()
    lits = re.findall(r'print\(\s*"\s*SKIP ([^"%]+)', src)
    check("%s prints at least one SKIP literal" % suite, bool(lits), suite)
    for lit in lits:
        check("%s: SKIP %r is listed" % (suite, lit[:50]),
              run_expected(entries, suite, lit.strip()[:160]), lit)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
