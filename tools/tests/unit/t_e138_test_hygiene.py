#!/usr/bin/env python3
"""Regression test for E138 — docs/audits/2026-11-09-codebase-audit.md.

Two test-hygiene claims were false:
  1. "Suites use the REAL server.py helpers, never copies": five suites still
     handed the aux module under test a hand-written settings_update (one an
     RLock, which hides a re-entrant deadlock on the real non-reentrant
     _state_lock). They now use lib/server_real.settings_api.
  2. "No network ... ENFORCED": the guard covers Python and Node processes
     only, so git was never guarded. run.sh's unit tier now sets
     GIT_ALLOW_PROTOCOL=file for every git that inherits its environment (a
     child with a stripped environment is still outside every guard — run.sh
     says so).

Part 2 drives the REAL run.sh on a throwaway tree with one fake suite.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
UNIT = os.path.join(REPO, "tools", "tests", "unit")
TESTS = os.path.join(REPO, "tools", "tests")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s\n    | %s" % (name, str(extra)[-800:].replace("\n", "\n    | ")))


print("\n1. no suite hands an aux module its own settings_update")
NAMED = ("t_c35_foryou_claude_switch.py", "t_c59_strict_bool_switch.py",
         "t_c44_foryou_credentials.py", "t_c105_memlayer_stale_restore.py",
         "t_f07_foryou_listing_fence.py")
for f in NAMED:
    src = open(os.path.join(UNIT, f)).read()
    check("%s uses server_real.settings_api" % f,
          "server_real.settings_api(" in src and not re.search(r"^\s*def settings_update\(", src, re.M))
standins = [f for f in sorted(os.listdir(UNIT)) if f.endswith(".py")
            and re.search(r"^\s*def settings_update\(", open(os.path.join(UNIT, f)).read(), re.M)]
check("no unit suite defines a settings_update stand-in", not standins, standins)
check("no RLock stands in for _state_lock in the c105 suite",
      "RLock(" not in open(os.path.join(UNIT, "t_c105_memlayer_stale_restore.py")).read())

print("\n1b. no JS suite hands the code under test a hand-written esc()")
# index.html's esc() is the one the dashboard ships; a suite that writes its
# own (one escaped "'" too, several escaped nothing) tests a different
# escaping from the one the owner runs.  Load the real one instead:
#   require(".../tools/tests/lib/index_helpers.js").indexFunction("esc")
# A suite that truly needs its own goes in ESC_EXCEPTIONS with a reason.
ESC_EXCEPTIONS = {}
ESC_DEF = re.compile(
    r"^\s*function\s+esc\s*\("                                   # function esc(
    r"|\.\s*esc\s*=(?!=)"                                         # x.esc = ...
    r"|\b(?:const|let|var)\s+esc\s*=\s*(?:function\b|\(|\w+\s*=>)"  # const esc = fn
    r"|\besc\s*:\s*(?:function\b|\(|\w+\s*=>)")                  # { esc: fn }
own_esc = []
for f in sorted(os.listdir(UNIT)):
    if not f.endswith(".js") or f in ESC_EXCEPTIONS:
        continue
    lines = open(os.path.join(UNIT, f), encoding="utf-8").read().split("\n")
    for n, ln in enumerate(lines, 1):
        nxt = lines[n] if n < len(lines) else ""
        if ESC_DEF.search(ln) and "indexFunction" not in ln + nxt:
            own_esc.append("%s:%d" % (f, n))
check("no JS suite defines its own esc()", not own_esc, own_esc)
for f in ("t_b04_needsyou_act_seen.js", "t_c21_google_card.js", "t_b18_1_needsyou_open_url.js",
          "t_c103_needsyou_button_names.js", "t_c99_2_needsyou_focus.js",
          "t_r2_22_needsyou_draft_poll.js"):
    check("%s loads index.html's real esc()" % f,
          'indexFunction("esc")' in open(os.path.join(UNIT, f)).read())
# review R4-seams: the same rule for the JS that Python suites embed in
# strings (ten of them carried a hand-written esc; four of those also escaped
# "'" and two escaped nothing). This file is skipped: it holds the probes.
own_esc_py = []
for f in sorted(os.listdir(UNIT)):
    if not f.endswith(".py") or f in ESC_EXCEPTIONS or f == os.path.basename(__file__):
        continue
    lines = open(os.path.join(UNIT, f), encoding="utf-8").read().split("\n")
    for n, ln in enumerate(lines, 1):
        nxt = lines[n] if n < len(lines) else ""
        if ESC_DEF.search(ln) and "indexFunction" not in ln + nxt:
            own_esc_py.append("%s:%d" % (f, n))
check("no Python suite's embedded JS defines its own esc()", not own_esc_py, own_esc_py)
for f in ("_chat_ui.py", "t_c06_needsyou_sightings.py", "t_c18_needsyou_blind_messages.py",
          "t_c18_2_needsyou_not_read.py", "t_c32_sessions_cap.py", "t_gh_needsyou.py",
          "t_c114_recorder_snapshot_surfaces.py", "t_c126_trust_pattern_overrides.py",
          "t_c126_retrust_diff.py", "t_r2_16_trust_badges.py"):
    check("%s loads index.html's real esc()" % f,
          'indexFunction("esc")' in open(os.path.join(UNIT, f)).read())
probe = ("function esc(s) {", "global.esc = s => String(s);", "  esc: (s) => s,",
         "const esc = s => s;", "G.esc = function (s) { return s; };")
check("the esc() detector catches every spelling it is meant to",
      all(ESC_DEF.search(p) for p in probe), [p for p in probe if not ESC_DEF.search(p)])
check("...and not a variable that merely holds a key event",
      not ESC_DEF.search('  const esc = w.__key(d.body, "Escape");'))

print("\n1c. no unit suite reads the owner's own data")
# A suite may read Hermes' source checkout (~/.hermes/hermes-agent) and the
# mlx-vlm venv as optional, read-only CODE layers; it never reads a store the
# owner's data lives in (S2 seam: index_harness / convos_harness / t_132 copied
# real conversations, t_131 snapshotted the live recorder.db, t_auxtoolbudget
# read the installed config.yaml).
REAL_HOME_RE = re.compile(r'expanduser\(\s*["\']~|Path\.home\(\)|\bREAL_HOME\b|homedir\(\)')
OWNER_DATA_RE = re.compile(r'"dashboard"|"chats"|"memories"|"sessions"|config\.yaml|\.db\b|'
                           r'\.hermes/dashboard|\.hermes/memories|\.env\b')
owner_reads = []
for f in sorted(os.listdir(UNIT)):
    if not f.endswith((".py", ".js")) or f == os.path.basename(__file__):
        continue
    for n, ln in enumerate(open(os.path.join(UNIT, f), encoding="utf-8"), 1):
        code = ln.split("#", 1)[0] if f.endswith(".py") else ln.split("//", 1)[0]
        if REAL_HOME_RE.search(code) and OWNER_DATA_RE.search(code):
            owner_reads.append("%s:%d" % (f, n))
check("no unit suite builds a path into the owner's ~/.hermes data", not owner_reads, owner_reads)
for f in ("index_harness.py", "convos_harness.py", "t_132_branch.py",
          "t_131_recorder_retention.py", "t_auxtoolbudget.py"):
    src = open(os.path.join(UNIT, f), encoding="utf-8").read()
    check("%s keeps no borrow of owner data" % f,
          "REAL_CHATS" not in src and "LIVE_DB" not in src and "REAL_IXDB" not in src
          and "_real_cfg" not in src and "qglb4" not in src)

print("\n2. git under the unit tier is limited to local repositories")
root = tempfile.mkdtemp(prefix="t-e138-")
t = os.path.join(root, "tools", "tests")
os.makedirs(os.path.join(t, "unit"))
shutil.copy2(os.path.join(TESTS, "run.sh"), os.path.join(t, "run.sh"))
shutil.copytree(os.path.join(TESTS, "lib"), os.path.join(t, "lib"),
                ignore=shutil.ignore_patterns("__pycache__"))
SUITE = r'''
import os, subprocess, tempfile
print("  GAP=" + os.environ.get("GIT_ALLOW_PROTOCOL", "<unset>"))
# loopback port 9: nothing listens, and nothing would leave this Mac anyway
r = subprocess.run(["git", "ls-remote", "http://127.0.0.1:9/x.git"],
                   capture_output=True, text=True, timeout=30,
                   env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
print("  HTTP=" + ("refused-by-policy" if "not allowed" in r.stderr else "attempted"))
d = tempfile.mkdtemp()
subprocess.run(["git", "init", "-q", "--bare", d + "/o.git"], check=True)
r = subprocess.run(["git", "clone", "-q", "file://" + d + "/o.git", d + "/c"],
                   capture_output=True, text=True, timeout=30)
print("  FILE=" + ("ok" if r.returncode == 0 else "failed: " + r.stderr.strip()[:80]))
r = subprocess.run(["git", "clone", "-q", d + "/o.git", d + "/c2"],
                   capture_output=True, text=True, timeout=30)
print("  PATH=" + ("ok" if r.returncode == 0 else "failed: " + r.stderr.strip()[:80]))
print("TESTS 1 passed 0 failed")
'''
with open(os.path.join(t, "unit", "t_git.py"), "w") as f:
    f.write(SUITE)
nops = os.path.join(root, "bin")
os.makedirs(nops)
with open(os.path.join(nops, "ps"), "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
os.chmod(os.path.join(nops, "ps"), 0o755)
env = {k: v for k, v in os.environ.items()
       if k not in ("CI", "HERMES_TESTS_STRICT", "HERMES_TESTS_FAIL_ON_SKIP",
                    "HERMES_TESTS_EXPECTED_SKIPS", "HERMES_TESTS_OFFLINE", "PYTHONPATH",
                    "NODE_OPTIONS", "HERMES_REPO", "HERMES_TESTS_EGRESS_LOG",
                    "HERMES_TESTS_SUITE", "GIT_ALLOW_PROTOCOL")}
env["PATH"] = nops + os.pathsep + env.get("PATH", "")
p = subprocess.run(["bash", os.path.join(t, "run.sh"), "unit"], env=env,
                   capture_output=True, text=True, timeout=180)
out = p.stdout + p.stderr
check("the inner run is green", p.returncode == 0, out)
check("the suite saw GIT_ALLOW_PROTOCOL=file", "GAP=file" in out, out)
check("git refused an http remote by policy", "HTTP=refused-by-policy" in out, out)
check("file:// and plain-path clones still work", "FILE=ok" in out and "PATH=ok" in out, out)
check("run.sh names what the guard does NOT cover",
      "is NOT guarded" in open(os.path.join(TESTS, "run.sh")).read())
shutil.rmtree(root, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
