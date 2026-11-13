#!/usr/bin/env python3
"""Regression test for E126 — docs/audits/2026-11-09-codebase-audit.md.

app/build-app.sh began with `rm -rf app/build`, so rebuilding the main app
(what update.sh advises after ANY app/ change) deleted the dictation helper
bundle the owner runs and registered for Start at login; doctor then said
dictation was "not installed (optional)". Now build-app.sh removes only its
own bundle and icon files, and doctor WARNs when the helper is gone although
it sent heartbeats here before.

build-app.sh runs for real on a scratch copy of app/ with swiftc, swift, sips,
iconutil, codesign and git as shims on PATH and HERMES_SKIP_INSTALL=1, so it
compiles, signs and installs nothing.
"""
import importlib.util
import json
import os
import shutil
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
        print("  FAIL %s  -- %s" % (name, str(extra)[-1200:]))


TMP = tempfile.mkdtemp(prefix="t-e126-")
ROOT = os.path.join(TMP, "repo")
APPDIR = os.path.join(ROOT, "app")
os.makedirs(APPDIR)
for f in ("build-app.sh", "main.swift", "render-icon.swift"):
    shutil.copy2(os.path.join(REPO, "app", f), os.path.join(APPDIR, f))
with open(os.path.join(ROOT, "VERSION"), "w") as f:
    f.write("9.9.9\n")

# shims: each creates the file the script expects next, and nothing else
SHIMS = {
    "swiftc": '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && { : > "$2"; shift; }; shift; done\n',
    "swift": '#!/bin/bash\n: > "$2"\n',
    "sips": '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = --out ] && { : > "$2"; shift; }; shift; done\n',
    "iconutil": '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && { : > "$2"; shift; }; shift; done\n',
    "codesign": "#!/bin/sh\nexit 0\n",
    "git": "#!/bin/sh\nexit 1\n",
}
SHIM = os.path.join(TMP, "shim")
os.makedirs(SHIM)
for n, body in SHIMS.items():
    p = os.path.join(SHIM, n)
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)
ENV = dict(os.environ, PATH=SHIM + ":/usr/bin:/bin", HERMES_SKIP_INSTALL="1",
           HOME=os.path.join(TMP, "home"))
which = subprocess.run(["bash", "-c", "command -v swiftc swift sips iconutil codesign"],
                       env=ENV, capture_output=True, text=True).stdout.split()
if which != [os.path.join(SHIM, n) for n in ("swiftc", "swift", "sips", "iconutil", "codesign")]:
    print("  FAIL the build tools do not resolve to the shims: %r" % which)
    print("TESTS 0 passed 1 failed")
    sys.exit(1)

BUILD = os.path.join(APPDIR, "build")
DICT = os.path.join(BUILD, "Hermes Dictation.app")
os.makedirs(os.path.join(DICT, "Contents", "MacOS"))
with open(os.path.join(DICT, "Contents", "MacOS", "HermesDictation"), "w") as f:
    f.write("helper")
stale = os.path.join(BUILD, "Hermes Assistant.app", "Contents", "stale-file")
os.makedirs(os.path.dirname(stale))
with open(stale, "w") as f:
    f.write("old")

print("\n1. building the main app leaves the dictation helper alone")
p = subprocess.run(["bash", os.path.join(APPDIR, "build-app.sh")], env=ENV,
                   capture_output=True, text=True, timeout=120)
check("build-app.sh completes (shimmed tools, not installed)",
      p.returncode == 0 and "HERMES_SKIP_INSTALL=1" in p.stdout, p.stdout + p.stderr)
check("the dictation helper bundle is still there",
      os.path.isfile(os.path.join(DICT, "Contents", "MacOS", "HermesDictation")))
check("the main bundle was rebuilt from scratch (an old file inside it is gone)",
      not os.path.exists(stale)
      and os.path.isfile(os.path.join(BUILD, "Hermes Assistant.app", "Contents", "Info.plist")))
check("nothing landed in the scratch HOME's Applications",
      not os.path.exists(os.path.join(TMP, "home", "Applications")))

print("\n2. doctor tells 'gone' from 'never installed'")
os.environ["HOME"] = os.path.join(TMP, "home")
spec = importlib.util.spec_from_file_location(
    "doctor_e126", os.path.join(REPO, "dashboard", "doctor.py"))
doc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doc)
doc.DICTATION_BUNDLE = os.path.join(TMP, "nowhere", "Hermes Dictation.app")
os.makedirs(os.path.dirname(doc.DICTATION_STORE), exist_ok=True)
r = doc._chk_dictation()
check("no bundle, never heard from -> PASS 'not installed (optional)'",
      r.get("status") == "pass" and "not installed" in r.get("detail", ""), r)
with open(doc.DICTATION_STORE, "w") as f:
    json.dump({"status": {"ts": time.time() - 86400, "running": True}}, f)
r = doc._chk_dictation()
check("no bundle, but it sent heartbeats here -> WARN 'missing although set up'",
      r.get("status") == "warn" and "missing although dictation was set up" in r.get("detail", "")
      and "build-dictation.sh" in r.get("fix", ""), r)
doc.DICTATION_BUNDLE = DICT
r = doc._chk_dictation()
check("bundle present again -> back to the normal running/not-running verdicts",
      "missing" not in r.get("detail", ""), r)

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
