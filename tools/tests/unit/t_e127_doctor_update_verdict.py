#!/usr/bin/env python3
"""Regression test for E127 — docs/audits/2026-11-09-codebase-audit.md.

Doctor's updater check trusted the cached `update_available`, so after an
update (before the next successful check) it warned "vX is available" while
vX was installed. It now re-judges the cached answer against VERSION, as the
Settings card does, with the same SemVer order as aux_update._upd_cmp (this
suite also keeps the two orders equal), and on the main channel asks git
whether origin/main's commit is already in HEAD (E130).

The REAL doctor.py is loaded by path with HOME in a scratch folder.
"""
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time

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
        print("  FAIL %s  -- %s" % (name, str(extra)[:600]))


TMP = tempfile.mkdtemp(prefix="t-e127-")
os.environ["HOME"] = os.path.join(TMP, "home")
DATA = os.path.join(os.environ["HOME"], ".hermes", "dashboard")
os.makedirs(DATA)
spec = importlib.util.spec_from_file_location(
    "doctor_e127", os.path.join(REPO, "dashboard", "doctor.py"))
doc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doc)
check("doctor reads the cache under the scratch HOME", doc.UPD_CACHE.startswith(DATA), doc.UPD_CACHE)
VER = os.path.join(TMP, "VERSION")
doc.VERSION_FILE = VER


def scenario(installed, latest, cached_flag, source="github", **extra):
    with open(VER, "w") as f:
        f.write(installed + "\n")
    pay = {"latest": latest, "update_available": cached_flag, "source": source,
           "checked_at": time.time(), "channel": "stable", "error": ""}
    pay.update(extra)
    with open(doc.UPD_CACHE, "w") as f:
        json.dump({"checked_at": time.time(), "channel": pay["channel"], "payload": pay}, f)
    return doc._chk_updater()


print("\n1. stable channel: the cached flag is re-judged against VERSION")
r = scenario("1.3.2", "v1.3.2", True)
check("cache says available, v1.3.2 installed -> PASS, no 'is available'",
      r.get("status") == "pass" and "is available" not in r.get("detail", ""), r)
r = scenario("1.3.1", "v1.3.2", True)
check("v1.3.1 installed -> WARN v1.3.2 is available",
      r.get("status") == "warn" and "v1.3.2 is available" in r.get("detail", ""), r)
r = scenario("1.3.1", "v1.3.2", False)
check("a cache that predates the release still says available when VERSION is older",
      r.get("status") == "warn" and "is available" in r.get("detail", ""), r)
r = scenario("1.4.0", "v1.3.2", True)
check("installed is NEWER than the cached latest -> PASS", r.get("status") == "pass", r)

print("\n2. the order is aux_update's, pair for pair")
src = open(os.path.join(REPO, "dashboard", "aux_update.py")).read()
tree = ast.parse(src)
names = {"_upd_parse_version", "_upd_cmp_pre", "_upd_cmp", "_upd_newer"}
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
rx = re.search(r"_UPD_VER_RE = _upd_re\.compile\(\s*(r\"[^\"]+\")\)", src)
ns = {"_upd_re": re}
exec("_UPD_VER_RE = _upd_re.compile(%s)" % rx.group(1), ns)
exec(compile(ast.Module(body=nodes, type_ignores=[]), "aux_update.py", "exec"), ns)
check("aux_update's four helpers were extracted", len(nodes) == 4, [n.name for n in nodes])
VERS = ["", "x", "v1", "1.0", "1.0.0", "v1.0.0", "1.0.1", "1.10.0", "1.9.9", "2.0.0",
        "1.0.0-rc.1", "1.0.0-rc.2", "1.0.0-rc.10", "1.0.0-alpha", "1.0.0-alpha.1",
        "1.0.0-1", "1.0.0-beta", "1.0.0+build.5", "V1.3.2", "1.3.2-rc", "0.0.0"]
diff = [(a, b) for a in VERS for b in VERS if doc._upd_newer(a, b) != ns["_upd_newer"](a, b)]
check("doctor._upd_newer == aux_update._upd_newer on %d pairs" % (len(VERS) ** 2), not diff, diff[:8])

print("\n3. main channel: the remote commit is checked against HEAD")
G = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
         GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
         GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
R = os.path.join(TMP, "repo")


def git(*a):
    return subprocess.run(["git", "-C", R] + list(a), env=G, capture_output=True,
                          text=True, check=True).stdout.strip()


subprocess.run(["git", "init", "-q", "-b", "main", R], env=G, check=True)
git("commit", "-q", "--allow-empty", "-m", "a")
first = git("rev-parse", "HEAD")
git("commit", "-q", "--allow-empty", "-m", "local work")
doc.ROOT = R
r = scenario("1.0.0", "main@" + first[:7], True, source="main", remote_head=first[:7])
check("cached 'available' but origin/main's commit is an ancestor of HEAD -> PASS",
      r.get("status") == "pass", r)
git("checkout", "-q", "-b", "side", first)
git("commit", "-q", "--allow-empty", "-m", "upstream")
up = git("rev-parse", "HEAD")
git("checkout", "-q", "main")
r = scenario("1.0.0", "main@" + up[:7], False, source="main", remote_head=up[:7])
check("a remote commit HEAD lacks -> WARN available (whatever the cache said)",
      r.get("status") == "warn" and "is available" in r.get("detail", ""), r)
r = scenario("1.0.0", "main@abcdef0", True, source="main", remote_head="abcdef0")
check("a remote commit git cannot resolve -> the cached verdict stands",
      r.get("status") == "warn", r)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
