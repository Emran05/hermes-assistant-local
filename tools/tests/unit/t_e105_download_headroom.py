#!/usr/bin/env python3
"""E105 (2026-11-09 audit, P3): the free-disk check for a model download
counts the downloads already in flight.

BEFORE: download_model judged each click against the free space of that
instant (`_disk_short(dl_gb, free_gb)`), and `_model_dl` was consulted only
for the same id. Three 15-25 GB models started a minute apart were each
judged against nearly the whole disk, and together could leave less than the
5 GB headroom or fill the volume (ENOSPC for every write under ~).

AFTER: the remaining size of every download in flight (catalog size minus the
blobs already on disk; unknown sizes count 0) comes off the free space first,
and the rule is re-checked at the moment the download slot is claimed.

The REAL dashboard/server.py imported as a module with a scratch HOME; disk,
catalog and the download thread are stubbed. Nothing is downloaded.
"""
import importlib.util
import os
import shutil
import sys
import tempfile

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-500:]))


TMP = tempfile.mkdtemp(prefix="hermes-e105-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_e105", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_e105"] = srv
spec.loader.exec_module(srv)
assert srv.HOME == TMP

SIZES = {"org/a": 20.0, "org/b": 18.0, "org/c": 3.0, "org/unknown": None}
BLOBS = {}
STARTED = []


class _T:
    def __init__(self, target=None, daemon=None, **k):
        self.target = target

    def start(self):
        STARTED.append(self.target)


srv._model_entry = lambda mid: {"id": mid}
srv._catalog_dl_gb = lambda mid: SIZES.get(mid)
srv._model_download_gb = lambda m: SIZES.get(m.get("id"))
srv._hf_blobs_bytes = lambda mid: BLOBS.get(mid, 0)
srv._disk_free_gb = lambda path=None: 40.0
srv._hf_python = lambda: "/usr/bin/true"
srv.threading = type("FakeThreading", (), {"Thread": _T, "Lock": srv.threading.Lock})


def reset():
    srv._model_dl.clear()
    srv._model_dl_err.clear()
    STARTED.clear()
    BLOBS.clear()


def main():
    try:
        print("\n1. one download alone fits (40 GB free, 20 GB pull, 5 GB headroom)")
        reset()
        r = srv.download_model("org/a")
        check("started", r.get("ok") is True and len(STARTED) == 1, r)

        print("\n2. a second big pull while the first is still in flight")
        r = srv.download_model("org/b")
        check("refused: 40 - 20 (in flight) - 18 leaves < 5 GB",
              r.get("ok") is False and r.get("error") == "not enough free disk", r)
        check("...the refusal says what is in flight", r.get("inflight_gb") == 20.0, r)
        check("...and no second thread started", len(STARTED) == 1, STARTED)

        print("\n3. a small pull that still fits beside it")
        r = srv.download_model("org/c")
        check("started (40 - 20 - 3 = 17 GB left)", r.get("ok") is True and len(STARTED) == 2, r)

        print("\n4. the in-flight download is mostly done: only its remainder counts")
        reset()
        srv._model_dl["org/a"] = "downloading"
        BLOBS["org/a"] = int(18 * 1024 ** 3)       # 18 of 20 GB already on disk
        r = srv.download_model("org/b")
        check("started (40 - 2 - 18 = 20 GB left)", r.get("ok") is True, r)

        print("\n5. an in-flight download of unknown size never refuses")
        reset()
        srv._model_dl["org/unknown"] = "downloading"
        r = srv.download_model("org/b")
        check("started", r.get("ok") is True, r)

        print("\n6. the same id clicked again is still 'already downloading'")
        reset()
        srv._model_dl["org/a"] = "downloading"
        r = srv.download_model("org/a")
        check("ok, status downloading, nothing new", r == {"ok": True, "status": "downloading"}
              and STARTED == [], r)

        print("\n7. finished and failed downloads do not count")
        reset()
        srv._model_dl.update({"org/a": "done", "org/c": "error"})
        r = srv.download_model("org/b")
        check("started", r.get("ok") is True, r)

        check("_dl_remaining_gb: sums only known sizes, never negative",
              srv._dl_remaining_gb(["org/a", "org/unknown", "org/c"]) == 23.0
              and (BLOBS.update({"org/c": int(9 * 1024 ** 3)}) or
                   srv._dl_remaining_gb(["org/c"]) == 0.0))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
