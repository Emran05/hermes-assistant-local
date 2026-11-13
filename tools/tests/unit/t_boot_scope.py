#!/usr/bin/env python3
"""doctor.py's `_last_boot_index` — the window the health card counts errors in.

Only the lines AFTER the last dashboard start may be counted, or a single old
failure haunts the card forever.  The boot index comes from the real
`_last_boot_index`, and the COUNT from the real `_chk_log_errors` check run
over a synthetic dashboard.log in a temp dir (C84: it used to re-implement the
slice-and-filter itself, so a check that forgot to scope its count still
passed).  No dashboard, no model, no ~/.hermes.
"""
import importlib.util, os, re, sys, tempfile

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

_TMP = tempfile.mkdtemp(prefix="t-boot-scope-")
import shutil  # noqa: E402
_atexit.register(shutil.rmtree, _TMP, True)   # leave no temp dir behind
d.DASH_LOG = os.path.join(_TMP, "dashboard.log")
d.ERR_LOG = os.path.join(_TMP, "errors.log")      # absent: only the dashboard half
d._dash_started_when = lambda: ""                 # no launchctl/ps from a unit test


def real_count(lines):
    """Run the REAL doctor check over `lines` as dashboard.log -> (n, scoped, detail)."""
    with open(d.DASH_LOG, "w") as f:
        f.write("\n".join(lines) + "\n")
    r = d._chk_log_errors()
    det = r.get("detail", "")
    m = re.search(r"(\d+) error lines in (?:dashboard\.log since the last start|the last)", det)
    return (int(m.group(1)) if m else None), ("since the last start" in det), det


for name, lines, want_i, want_errs in CASES:
    i = d._last_boot_index(lines)
    check("%s: boot index" % name, i == want_i, "got %r want %r" % (i, want_i))
    n, scoped, det = real_count(lines)
    check("%s: _chk_log_errors counts only the running process's errors" % name,
          n == len(want_errs), "got %r want %r (%s)" % (n, len(want_errs), det))
    check("%s: and says which window it counted" % name,
          scoped == (want_i is not None), det)
    if len(set(want_errs)) == 1:
        check("%s: the sample is the in-window error" % name,
              ("most common (%dx): %s" % (len(want_errs), want_errs[0])) in det, det)

n, scoped, det = real_count(["[guard] refused POST /api/x host='evil' (cross-origin)",
                             banner, "[guard] refused GET / host='evil' (forbidden host)"])
check("same-origin guard refusals are not counted as errors", n == 0, det)

print("\n%d checks, %d FAILED" % (CHECKS[0], len(FAILS)))
if FAILS:
    print("failed:", FAILS)
sys.exit(1 if FAILS else 0)
