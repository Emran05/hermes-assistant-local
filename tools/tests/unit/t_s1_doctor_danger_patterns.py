#!/usr/bin/env python3
"""Seam S1-6 — doctor compares Hermes' dangerous-command list with the copy.

dashboard/permissions.py holds `_DANGER_PATTERNS`, a COPY of upstream
Hermes' tools/approval.py DANGEROUS_PATTERNS (it re-scans compound commands
for the Trust tiers). When Hermes is upgraded and that list changes, nothing
said so: Trust "Auto" could judge commands differently from Hermes. doctor
now has one read-only check that parses both files (AST, nothing imported or
executed) and compares count, keys (descriptions), regex text and flags.

The REAL doctor.py, imported with HOME pointing at a scratch folder; the
"installed Hermes" is a fake approval.py written there, built from the
permissions.py copy itself (so the equal case is exact).
"""
import importlib.util
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:600]))


TMP = tempfile.mkdtemp(prefix="hermes-s1-doctor-")
os.environ["HOME"] = TMP
spec = importlib.util.spec_from_file_location("doctor_s1", os.path.join(DASH, "doctor.py"))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
UP = os.path.join(TMP, ".hermes", "hermes-agent", "tools", "approval.py")
os.makedirs(os.path.dirname(UP))

perm = open(os.path.join(DASH, "permissions.py"), encoding="utf-8").read()
start = perm.index("_DP_SSH_SENSITIVE_PATH = ")
end = perm.index("\n]\n", perm.index("_DANGER_PATTERNS = [")) + 3
block = perm[start:end].replace("_DP_", "_").replace("_DANGER_PATTERNS", "DANGEROUS_PATTERNS")
UPSTREAM = ("import re\n\n# upstream-shaped fake\n" + block +
            "\n_RE_FLAGS = re.IGNORECASE | re.DOTALL\n"
            "DANGEROUS_PATTERNS_COMPILED = [\n"
            "    (re.compile(pattern, _RE_FLAGS), description)\n"
            "    for pattern, description in DANGEROUS_PATTERNS\n]\n")


def write(text):
    with open(UP, "w", encoding="utf-8") as f:
        f.write(text)


def run():
    fn = {c[0]: c[2] for c in d.CHECKS}.get("danger_patterns")
    if fn is None:
        return None
    return fn()


ids = [c[0] for c in d.CHECKS]
check("doctor registers a danger_patterns check", "danger_patterns" in ids, ids)
check("doctor has 23 checks", len(ids) == 23, len(ids))

print("\n== Hermes' source not found ==")
r = run() or {}
check("a quiet pass", r.get("status") == d.PASS and "not found" in r.get("detail", ""), r)

print("\n== equal ==")
write(UPSTREAM)
r = run() or {}
check("the same list -> pass", r.get("status") == d.PASS, r)
n = perm[perm.index("_DANGER_PATTERNS = ["):end].count("\n    (")
check("...naming how many patterns it compared",
      re.search(r"\b%d\b" % n, r.get("detail", "")) is not None, (n, r))

print("\n== the same list written differently is not drift (review R4-seams) ==")
same = [
    ("type hints added", UPSTREAM.replace(
        "DANGEROUS_PATTERNS = [\n", "DANGEROUS_PATTERNS: list[tuple[str, str]] = [\n").replace(
        "_RE_FLAGS = re.IGNORECASE | re.DOTALL", "_RE_FLAGS: int = re.IGNORECASE | re.DOTALL")),
    ("whitespace, comments and blank lines moved", UPSTREAM.replace(
        "DANGEROUS_PATTERNS = [\n", "DANGEROUS_PATTERNS = [\n\n    # regrouped\n").replace(
        "    (r'\\bmkfs\\b', \"format filesystem\"),\n",
        "    (\n        r'\\bmkfs\\b',\n        \"format filesystem\",\n    ),\n")),
    ("flags in the other order, short names", UPSTREAM.replace(
        "_RE_FLAGS = re.IGNORECASE | re.DOTALL", "_RE_FLAGS = re.S | re.I")),
    ("one literal split in two", UPSTREAM.replace(
        "(r'\\bmkfs\\b', ", "(r'\\bmk' r'fs\\b', ")),
]
for name, text in same:
    check("(premise) %s changes the fake's text" % name, text != UPSTREAM)
    write(text)
    r = run() or {}
    check("%s -> still a pass" % name, r.get("status") == d.PASS, r)

MSG = "Hermes' dangerous-command list changed; Trust 'Auto' may judge commands differently"
cases = [
    ("a regex edited", UPSTREAM.replace(r"(r'\bmkfs\b', ", r"(r'\bmkfs(?:\.\w+)?\b', ")),
    ("a pattern added", UPSTREAM.replace(
        "DANGEROUS_PATTERNS = [\n", "DANGEROUS_PATTERNS = [\n    (r'\\bshred\\b', \"shred\"),\n")),
    ("a pattern removed", UPSTREAM.replace(
        "    (r'\\bmkfs\\b', \"format filesystem\"),\n", "")),
    ("a description (the key) renamed", UPSTREAM.replace('"format filesystem"', '"mkfs"')),
    ("a shared fragment edited", UPSTREAM.replace(
        "_MACOS_PRIVATE_SYSTEM_PATH = r'/private/(?:etc|var|tmp|home)/'",
        "_MACOS_PRIVATE_SYSTEM_PATH = r'/private/(?:etc|var|tmp|home|opt)/'")),
    ("the flags changed", UPSTREAM.replace(
        "_RE_FLAGS = re.IGNORECASE | re.DOTALL", "_RE_FLAGS = re.IGNORECASE")),
]
for name, text in cases:
    check("(premise) %s changes the fake" % name, text != UPSTREAM)
    write(text)
    r = run() or {}
    check("%s -> warn" % name, r.get("status") == d.WARN, r)
    check("%s -> the plain message" % name, MSG in r.get("detail", ""), r)

print("\n== unreadable ==")
write("DANGEROUS_PATTERNS = [ (unknown_call(), 'x') ]\n")
r = run() or {}
check("a list it cannot read -> warn (not a crash, not a pass)", r.get("status") == d.WARN, r)
write("def broken(:\n")
r = run() or {}
check("a file that does not parse -> warn", r.get("status") == d.WARN, r)
write("x = 1\n")
r = run() or {}
check("a file with no DANGEROUS_PATTERNS -> warn", r.get("status") == d.WARN, r)

print("\n== it never executes the upstream file ==")
marker = os.path.join(TMP, "executed")
write(UPSTREAM + "\nopen(%r, 'w').write('x')\n" % marker)
run()
check("a statement in the file did not run", not os.path.exists(marker))

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
