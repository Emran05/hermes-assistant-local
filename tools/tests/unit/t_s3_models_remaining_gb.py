#!/usr/bin/env python3
"""Review R4-seams (E105 / S2-5): models_payload() says what each download in
flight still has to fetch.

download_model() refuses a pull when (free disk - what the downloads in flight
still need) - this download < the headroom. The model menu makes the same
check before the click, but a downloading row carried only `downloading` and
`download_gb` (the FULL size), so the menu could not subtract the remainder
and offered downloads the server then refused. Each downloading row now
carries `remaining_gb` = _dl_remaining_gb([id]); every other row carries None.

The real server.py on a scratch HOME; sizes, cache bytes and the registry are
stubs. No model, no network, no download.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="hermes-s3-remaining-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_s3rem", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_s3rem"] = srv
spec.loader.exec_module(srv)
assert srv.HOME == TMP

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:500]))


SIZES = {"org/a": 20.0, "org/b": 18.0, "org/unknown": None}
BLOBS = {}
REG = [{"id": "org/a", "label": "A", "ram": 8}, {"id": "org/b", "label": "B", "ram": 8},
       {"id": "org/unknown", "label": "U", "ram": 8}]
srv._model_registry = lambda: [dict(m) for m in REG]
srv._model_entry = lambda mid: next((dict(m) for m in REG if m["id"] == mid), None)
srv._catalog_dl_gb = lambda mid: SIZES.get(mid)
srv._model_download_gb = lambda m: SIZES.get(m.get("id"))
srv._hf_blobs_bytes = lambda mid: BLOBS.get(mid, 0)
srv._model_downloaded = lambda mid: False
srv._disk_free_gb = lambda path=None: 40.0
srv.active_model = lambda: "org/b"
srv.bg_online = lambda: False
srv.agent_paused = lambda: True            # no footprint probe: the process is "not resident"


def rows():
    p = srv.models_payload()
    return {m["id"]: m for m in p["models"]}, p


try:
    print("== nothing in flight ==")
    srv._model_dl.clear()
    r, p = rows()
    check("every row carries the key", all("remaining_gb" in m for m in r.values()), list(r.values())[0])
    check("...and it is None while nothing downloads", all(m["remaining_gb"] is None for m in r.values()),
          {k: m["remaining_gb"] for k, m in r.items()})
    check("the payload is still JSON", bool(json.dumps(p, default=str)))

    print("\n== one download in flight, 18 of 20 GB still to come ==")
    srv._model_dl["org/a"] = "downloading"
    BLOBS["org/a"] = int(2 * 1024 ** 3)
    r, _p = rows()
    check("its row says 18.0 GB remain", r["org/a"]["downloading"] is True and r["org/a"]["remaining_gb"] == 18.0,
          r["org/a"])
    check("...the same number download_model() takes off the free space",
          r["org/a"]["remaining_gb"] == srv._dl_remaining_gb(["org/a"]))
    check("the other rows stay None", r["org/b"]["remaining_gb"] is None and r["org/unknown"]["remaining_gb"] is None)
    check("download_gb is still the FULL size (not the remainder)", r["org/a"]["download_gb"] == 20.0, r["org/a"])
    # the menu's rule with these numbers is the server's verdict
    ref = srv.download_model("org/b")
    menu_short = (40.0 - r["org/a"]["remaining_gb"]) - 18.0 < srv.DISK_HEADROOM_GB
    check("menu rule == server verdict (refused: 40 - 18 - 18 = 4 < 5)",
          menu_short is True and ref.get("ok") is False and ref.get("error") == "not enough free disk"
          and ref.get("inflight_gb") == r["org/a"]["remaining_gb"], (menu_short, ref))

    print("\n== nearly finished: only the remainder counts ==")
    BLOBS["org/a"] = int(19.5 * 1024 ** 3)
    r, _p = rows()
    check("0.5 GB remain", r["org/a"]["remaining_gb"] == 0.5, r["org/a"]["remaining_gb"])
    BLOBS["org/a"] = int(25 * 1024 ** 3)
    r, _p = rows()
    check("more on disk than the catalog size is 0, never negative", r["org/a"]["remaining_gb"] == 0.0,
          r["org/a"]["remaining_gb"])

    print("\n== an unknown size counts 0; finished and failed downloads carry None ==")
    srv._model_dl.clear()
    srv._model_dl.update({"org/unknown": "downloading", "org/a": "done", "org/b": "error"})
    r, _p = rows()
    check("unknown size in flight -> 0.0 (never a guess)", r["org/unknown"]["remaining_gb"] == 0.0, r["org/unknown"])
    check("done / error -> None", r["org/a"]["remaining_gb"] is None and r["org/b"]["remaining_gb"] is None)

    print("\n== a helper that raises (or is missing) never breaks the payload ==")
    srv._model_dl.clear()
    srv._model_dl["org/a"] = "downloading"
    real = srv._dl_remaining_gb

    def boom(ids):
        raise RuntimeError("cache unreadable")
    srv._dl_remaining_gb = boom
    r, _p = rows()
    check("a raising helper -> None for that row, the payload still answers",
          r["org/a"]["remaining_gb"] is None and r["org/a"]["downloading"] is True, r["org/a"])
    del srv._dl_remaining_gb
    r, _p = rows()
    check("a missing helper -> None", r["org/a"]["remaining_gb"] is None, r["org/a"])
    srv._dl_remaining_gb = real
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
