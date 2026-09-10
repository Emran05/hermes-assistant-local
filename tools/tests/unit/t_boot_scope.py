#!/usr/bin/env python3
"""doctor.py's `_last_boot_index` — the window the health card counts errors in.

Only the lines AFTER the last dashboard start may be counted, or a single old
failure haunts the card forever.  Pure function over synthetic log text: no
dashboard, no model, no ~/.hermes.
"""
import importlib.util, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
spec = importlib.util.spec_from_file_location(
    "_d", os.path.join(REPO, "dashboard", "doctor.py"))
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

FAILS = []
CHECKS = [0]

import atexit as _atexit
_atexit.register(lambda: print("TESTS %d passed %d failed"
                               % (CHECKS[0] - len(FAILS), len(FAILS))))


def check(name, cond, extra=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


banner = "Hermes Assistant dashboard: http://127.0.0.1:7788"
rec = "[aux_recorder] reconciler started"

# name -> (log text, expected boot index, expected counted error lines)
CASES = (
    ("banner, errors both sides",
     ["old ERROR one", "old ERROR two", banner, "  hermes: x", "new ERROR three"],
     2, ["new ERROR three"]),
    ("two banners -> the last wins",
     ["ERROR a", banner, "ERROR b", banner, "ERROR c"],
     3, ["ERROR c"]),
    ("no banner, one recorder line",
     ["ERROR a", rec, "ERROR b"],
     1, ["ERROR b"]),
    ("no banner, restart storm counts from the earliest of the run",
     ["ERROR a", rec, rec, rec, "ERROR b"],
     1, ["ERROR b"]),
    ("no banner, two runs with a real gap -> the later run",
     [rec, "l1", "l2", "l3", "l4", "l5", rec, "ERROR b"],
     6, ["ERROR b"]),
    ("no marker at all -> the whole window",
     ["ERROR a", "ERROR b"],
     None, ["ERROR a", "ERROR b"]),
)

for name, lines, want_i, want_errs in CASES:
    i = d._last_boot_index(lines)
    after = lines[i + 1:] if i is not None else lines
    errs = [ln for ln in after if ln.strip() and d._ERR_RE.search(ln)
            and not d._ERR_SKIP_RE.match(ln)]
    check("%s: boot index" % name, i == want_i, "got %r want %r" % (i, want_i))
    check("%s: errors counted" % name, errs == want_errs,
          "got %r want %r" % (errs, want_errs))

print("\n%d checks, %d FAILED" % (len(CASES) * 2, len(FAILS)))
if FAILS:
    print("failed:", FAILS)
sys.exit(1 if FAILS else 0)
