#!/usr/bin/env python3
"""Round-2 regression test for R2-8 (2026-10-08 audit P3 prose, model
lifecycle): resuming a partial model download.

BEFORE:
  * the free-disk check quoted the onboarding catalog's FULL size for a model
    that was partially downloaded and did not count the blobs already in the
    hub cache, so resuming a mostly finished pull could be refused ("not
    enough free disk") on a Mac that had plenty of room for the rest — and the
    menu showed "not enough disk" for it too (same download_gb).
  * snapshot_download ran under a fixed 7200 s subprocess timeout and was
    killed at two hours even while it was still making progress.

AFTER: download_gb for a not-yet-complete model is the catalog size minus the
bytes already in blobs/ (finished + *.incomplete), floored at 0.1; each
download job runs through _dl_run(), which kills it only when neither the
repo's blob bytes nor the process output have moved for DL_STALL_S.

REAL _model_download_gb / _disk_short / download_model's disk gate / _dl_run,
ast-extracted from dashboard/server.py; the hub cache is a temp HOME with
SPARSE blobs (st_size without using disk); the "download" subprocesses are
tiny python children writing into it. HERMES_T_SERVER_SRC=<old server.py>
shows it failing.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t_r2_6_lifecycle_races as L  # noqa: E402  (shared loader)

check = L.check
GiB = 1024 ** 3
MID = "org/model-27b"


def cache_dir(home, mid):
    return os.path.join(home, ".cache", "huggingface", "hub",
                        "models--" + mid.replace("/", "--"))


def sparse(path, gib):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(int(gib * GiB))


def size_ns(home, free_gb):
    ns = {"os": os, "json": json, "sys": sys, "time": time, "HOME": home,
          "threading": threading, "subprocess": subprocess,
          "_cached": lambda key, ttl, fn: fn(),
          "_ONB_BY_ID": {MID: {"size_gb": 16.0, "draft_size_gb": 1.0}},
          "_disk_free_gb": lambda path=None: free_gb, "DISK_HEADROOM_GB": 5.0,
          "_model_dl": {}, "_model_dl_err": {}, "_model_dl_lock": threading.Lock(),
          "_hf_python": lambda: None,       # stop right after the disk gate
          "_disk_guard_warn": lambda *a: None}
    L.load(["_hf_cache_dir", "_weights_complete", "_model_downloaded",
            "_hf_snapshot_dir", "_catalog_dl_gb", "_model_download_gb",
            "_disk_short", "download_model", "_dir_size_gb"], ns,
           optional=["_hf_blobs_bytes", "_dl_run", "DL_STALL_S", "DL_POLL_S"])
    entry = {"id": MID, "draft_model": "org/drafter"}
    ns["_model_entry"] = lambda m: entry if m == MID else None
    ns["_draft_ready"] = lambda e: False
    return ns, entry


def test_resume_disk(home):
    print("\n1. a mostly finished download is quoted (and allowed) by what is LEFT")
    blobs = os.path.join(cache_dir(home, MID), "blobs")
    sparse(os.path.join(blobs, "aaa"), 10.0)                 # finished shards
    sparse(os.path.join(blobs, "bbb.incomplete"), 4.0)       # the one in flight
    sparse(os.path.join(cache_dir(home, "org/drafter"), "blobs", "ccc"), 1.0)
    ns, entry = size_ns(home, free_gb=9.0)
    gb = ns["_model_download_gb"](entry)
    check("THE FIX: download_gb = 17 GB catalog - 15 GB already in blobs = 2.0",
          gb == 2.0, gb)
    r = ns["download_model"](MID)
    check("THE FIX: with 9 GB free the resume passes the disk gate (needs ~2 + 5 headroom)",
          r.get("error") != "not enough free disk", r)
    ns, entry = size_ns(home, free_gb=6.0)
    r = ns["download_model"](MID)
    check("...and is still refused when even the rest would leave < 5 GB",
          r.get("error") == "not enough free disk" and r.get("download_gb") == 2.0, r)
    sparse(os.path.join(blobs, "ddd"), 5.0)                   # more than the catalog
    ns, entry = size_ns(home, free_gb=9.0)
    check("blobs beyond the catalog size floor at 0.1 GB, never 0 or negative",
          ns["_model_download_gb"](entry) == 0.1, ns["_model_download_gb"](entry))
    ns, entry = size_ns(os.path.join(home, "empty"), free_gb=9.0)
    check("nothing on disk yet -> the full catalog size (17.0)",
          ns["_model_download_gb"](entry) == 17.0, ns["_model_download_gb"](entry))


CHILD_GROW = r"""
import os, sys, time
p = sys.argv[1]
os.makedirs(os.path.dirname(p), exist_ok=True)
for i in range(int(sys.argv[2])):
    with open(p, "ab") as f:
        f.write(b"x" * 4096)
    time.sleep(0.2)
"""
CHILD_TALK = r"""
import sys, time
for i in range(int(sys.argv[1])):
    sys.stderr.write("Fetching 3 files: %d%%\r" % i); sys.stderr.flush()
    time.sleep(0.2)
"""
CHILD_HANG = "import time; time.sleep(60)"
CHILD_FAIL = "import sys; sys.stderr.write('401 Unauthorized'); sys.exit(3)"


def test_watchdog(home):
    print("\n2. _dl_run(): killed only when it stops making progress")
    ns, _ = size_ns(home, 100.0)
    run = ns.get("_dl_run")
    if not callable(run):
        check("THE FIX: download jobs run through a no-progress watchdog (_dl_run)",
              False, "no _dl_run in this server.py (fixed 7200 s timeout)")
        return
    repo = "org/grow"
    blob = os.path.join(cache_dir(home, repo), "blobs", "x.incomplete")
    t0 = time.monotonic()
    rc, out, stalled = run([sys.executable, "-c", CHILD_GROW, blob, "14"], repo,
                           stall_s=1.0, poll_s=0.1)
    took = time.monotonic() - t0
    check("THE FIX: a pull that keeps writing blobs runs past the stall window "
          "(~2.8 s vs 1 s) and finishes", rc == 0 and not stalled and took > 2.0,
          (rc, stalled, round(took, 2)))
    rc, out, stalled = run([sys.executable, "-c", CHILD_TALK, "14"], "org/none",
                           stall_s=1.0, poll_s=0.1)
    check("progress-bar output alone also counts as progress",
          rc == 0 and not stalled and "Fetching" in out, (rc, stalled, out[-80:]))
    t0 = time.monotonic()
    rc, out, stalled = run([sys.executable, "-c", CHILD_HANG], "org/none",
                           stall_s=1.0, poll_s=0.1)
    took = time.monotonic() - t0
    check("a pull with no new bytes and no output is killed after the window",
          stalled and rc != 0 and took < 5, (rc, stalled, round(took, 2)))
    rc, out, stalled = run([sys.executable, "-c", CHILD_FAIL], "org/none",
                           stall_s=5.0, poll_s=0.1)
    check("a failing pull reports its exit code and output",
          rc == 3 and not stalled and "401" in out, (rc, stalled, out))
    src = open(L.SRC, encoding="utf-8").read()
    check("download_model no longer carries the fixed 7200 s timeout",
          "timeout=7200" not in src)


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-8-") as home:
        test_resume_disk(home)
        test_watchdog(home)
    print("\nTESTS %d passed %d failed" % (L.PASSES[0], len(L.FAILS)))
    sys.exit(1 if L.FAILS else 0)


if __name__ == "__main__":
    main()
