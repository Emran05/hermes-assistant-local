#!/usr/bin/env python3
"""R2-24 — a hung icalBuddy must not prune the calendar half of the index.

The calendar adapter runs icalBuddy over +/-30 days.  When that command timed
out (15 s) — or could not be spawned at all — `_ix_cal_lines()` fell back to
`macos_calendar()`'s TODAY-ONLY list and returned it as "store present", so
the sweep pruned every indexed event outside today.  A hang says nothing about
which events exist: it now answers None (store absent, nothing pruned).  The
today-only fallback stays for the case it was written for — the range form is
unsupported, i.e. icalBuddy answers with a non-zero exit.

Execs the REAL aux_index.py into a stub globals dict against a throwaway
HOME, with `subprocess` swapped for a stub in that dict only and a fake
icalBuddy on PATH (it is never executed).  No dashboard, no network.

Fails on the old code: the timeout and OSError cases returned the today-only
list, and the sweep then pruned the two out-of-today events.
"""
import io
import os
import subprocess
import sys
import tempfile
import types

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + name)
    else:
        FAIL += 1
        print("  FAIL %s  %r" % (name, extra))


_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
AUX = os.path.join(REPO, "dashboard", "aux_index.py")

TMP = tempfile.mkdtemp(prefix="hermes-r2-24-")
DATA = os.path.join(TMP, "home", ".hermes", "dashboard")
os.makedirs(DATA, exist_ok=True)
BIN = os.path.join(TMP, "bin")
os.makedirs(BIN)
FAKE = os.path.join(BIN, "icalBuddy")
with open(FAKE, "w") as f:
    f.write("#!/bin/sh\nexit 99\n")       # never run: subprocess is stubbed
os.chmod(FAKE, 0o755)
os.environ["PATH"] = BIN + os.pathsep + os.environ.get("PATH", "")

MODE = {"m": "ok"}
CALLS = []
RANGE_OUT = ("|2026-10-01 at 09:30 ~ Quarterly kumquat review\n"
             "|2026-11-12 at 14:00 ~ Dentist far future\n")


def fake_run(argv, **kw):
    CALLS.append(list(argv))
    m = MODE["m"]
    if m == "timeout":
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    if m == "oserror":
        raise PermissionError(13, "Operation not permitted")
    if m == "rc1":
        return subprocess.CompletedProcess(argv, 1, "", "unknown command eventsFrom")
    return subprocess.CompletedProcess(argv, 0, RANGE_OUT, "")


SUB = types.SimpleNamespace(run=fake_run,
                            SubprocessError=subprocess.SubprocessError,
                            TimeoutExpired=subprocess.TimeoutExpired,
                            CompletedProcess=subprocess.CompletedProcess)

TODAY_ONLY = {"available": True,
              "events": [{"time": "2026-10-26 at 08:00", "title": "Today standup"}]}

g = {"__name__": "aux_index_r2_24", "DATA": DATA,
     "register_get": lambda p, f: None, "register_post": lambda p, f: None,
     "macos_calendar": lambda: TODAY_ONLY}
exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
g["subprocess"] = SUB                     # only this module's view of it


def cal_titles():
    con = g["_ix_conn"]()
    try:
        return sorted(r[0] for r in con.execute(
            "SELECT title FROM items WHERE source='calendar'"))
    finally:
        con.close()


try:
    print("=== the range command answers: two events indexed ===")
    MODE["m"] = "ok"
    lines = g["_ix_cal_lines"]()
    ok("range form is used (eventsFrom:/to:)",
       CALLS and any(a.startswith("eventsFrom:") for a in CALLS[-1]), CALLS[-1:])
    ok("two lines come back", lines is not None and len(lines) == 2, lines)
    sw = g["index_sweep"]("r2-24-seed", sources=("calendar",))
    ok("seed sweep ok", sw.get("ok") is True, sw)
    ok("both events are indexed",
       cal_titles() == ["Dentist far future", "Quarterly kumquat review"], cal_titles())

    print("\n=== a hung icalBuddy (TimeoutExpired) is 'store absent' ===")
    MODE["m"] = "timeout"
    ok("_ix_cal_lines() answers None on a timeout", g["_ix_cal_lines"]() is None,
       g["_ix_cal_lines"]())
    sw = g["index_sweep"]("r2-24-timeout", sources=("calendar",))
    ok("the sweep reports calendar absent",
       sw.get("stats", {}).get("calendar") == {"absent": True}, sw)
    ok("NOTHING outside today was pruned",
       cal_titles() == ["Dentist far future", "Quarterly kumquat review"], cal_titles())
    ok("today's fallback list did not replace them",
       "Today standup" not in cal_titles(), cal_titles())

    print("\n=== a spawn failure (OSError) is 'store absent' too ===")
    MODE["m"] = "oserror"
    ok("_ix_cal_lines() answers None on OSError", g["_ix_cal_lines"]() is None)
    g["index_sweep"]("r2-24-oserror", sources=("calendar",))
    ok("rows survive an OSError",
       cal_titles() == ["Dentist far future", "Quarterly kumquat review"], cal_titles())

    print("\n=== an unsupported range form (non-zero rc) still falls back ===")
    MODE["m"] = "rc1"
    lines = g["_ix_cal_lines"]()
    ok("rc!=0 falls back to macos_calendar()'s today-only list",
       lines == ["|2026-10-26 at 08:00 ~ Today standup"], lines)
    g["index_sweep"]("r2-24-rc1", sources=("calendar",))
    ok("... which is 'store present' (the documented, deliberate fallback)",
       cal_titles() == ["Today standup"], cal_titles())

    print("\n=== no fallback provider and a non-zero rc: absent, not empty ===")
    g["macos_calendar"] = None
    ok("rc!=0 with no provider answers None", g["_ix_cal_lines"]() is None)
finally:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
