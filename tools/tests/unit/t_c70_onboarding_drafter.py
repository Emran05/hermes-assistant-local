#!/usr/bin/env python3
"""Regression test for audit finding C70 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: onboarding judged "downloaded" with _model_downloaded() alone. A
Qwen3.8-27B whose main weights were local but whose MTP drafter
(Qwen3.8-27B-MTP-bf16) was missing was listed in detect.models_downloaded,
the recommendation said already_downloaded, and apply with download:true
answered "already downloaded" and never fetched the drafter — the model menu
meanwhile showed the same model as NOT downloaded (it uses _draft_ready too).

AFTER: _onb_model_complete() = _model_downloaded AND _draft_ready, the menu's
definition; detect, the recommendation and apply's download all use it.

Loads the REAL dashboard/server.py (execs every aux module) against a
throwaway HOME; downloads are recorded, never started.
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
        print("  FAIL %s %s" % (name, extra))


TMP = tempfile.mkdtemp(prefix="hermes-c70-")
os.environ["HOME"] = TMP
os.environ.pop("MLX_SOFT_GB", None)
os.environ.pop("MLX_HARD_GB", None)
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")


class _R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode, self.stderr = out, rc, ""


class FakeSubprocess:
    CalledProcessError = Exception
    TimeoutExpired = Exception
    PIPE, DEVNULL, STDOUT = -1, -3, -2

    def run(self, argv, **kw):
        a = " ".join(str(x) for x in argv)
        if "hw.memsize" in a:
            return _R(str(64 * 1024 ** 3))
        # round 3: the recommendation is the model advisor's, which reads the
        # chip (an unidentified chip is estimated as the slowest M1 and gets
        # the 2B); the M5 Max is the Mac the 27B + 9B pair is drawn for
        if "machdep.cpu.brand_string" in a:
            return _R("Apple M5 Max")
        return _R("", 1)

    def Popen(self, *a, **k):
        raise RuntimeError("harness: no Popen")


sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_c70", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_c70"] = srv
spec.loader.exec_module(srv)
srv.subprocess = FakeSubprocess()
srv._RAM_GB_CACHE[0] = None
DOWNLOADS = []
srv.download_model = lambda mid: DOWNLOADS.append(mid) or {"ok": True, "status": "downloading"}
srv.agent_wake = lambda *a, **k: None
srv._mlx_start = lambda *a, **k: False
srv.model_online = lambda: True          # never activate anything in this test
srv._bg_probe = lambda: False            # R2-42: apply asks whether :8081 runs
srv._onb_disk_free_gb = lambda p=None: 500.0

M27B, M9B = srv.M27B, srv.M9B
DRAFTER = "mlx-community/Qwen3.8-27B-MTP-bf16"
LOCAL = {M27B, M9B}                       # main weights local, drafter NOT
srv._model_downloaded = lambda mid: mid in LOCAL

try:
    st = srv.GET_ROUTES["/api/onboarding/state"](srv.RouteCtx())
    st = st[0] if isinstance(st, tuple) else st
    dl = st["detect"]["models_downloaded"]
    check("27B with its drafter missing is NOT listed as downloaded", M27B not in dl, dl)
    check("9B (no drafter) is", M9B in dl, dl)
    rec = st["recommendation"]
    check("64 GB recommendation (27B + 9B) is not 'already downloaded'",
          rec["primary"] == M27B and rec["already_downloaded"] is False,
          {k: rec.get(k) for k in ("tier", "primary", "background", "already_downloaded")})
    check("...and it is the model advisor's (it too counts only COMPLETE models)",
          rec.get("tier") == "advisor", rec.get("tier"))
    legacy = srv._onb_recommend(64, st["detect"]["models_downloaded"])
    check("the RAM-tier fallback agrees: not 'already downloaded'",
          legacy["primary"] == M27B and legacy["already_downloaded"] is False, legacy)
    r = srv.POST_ROUTES["/api/onboarding/apply"](srv.RouteCtx(body={
        "primary": M27B, "background": M9B, "download": True}))
    r = r[0] if isinstance(r, tuple) else r
    check("apply download:true hands the 27B to download_model (which pulls the drafter)",
          DOWNLOADS == [M27B], (DOWNLOADS, r.get("applied")))
    check("...and does not claim 'already downloaded'",
          r["applied"].get("download_note") != "already downloaded", r["applied"])

    LOCAL.add(DRAFTER)
    del DOWNLOADS[:]
    st = srv.GET_ROUTES["/api/onboarding/state"](srv.RouteCtx())
    st = st[0] if isinstance(st, tuple) else st
    check("with the drafter local the 27B counts as downloaded",
          M27B in st["detect"]["models_downloaded"]
          and st["recommendation"]["already_downloaded"] is True, st["detect"])
    r = srv.POST_ROUTES["/api/onboarding/apply"](srv.RouteCtx(body={
        "primary": M27B, "background": M9B, "download": True}))
    r = r[0] if isinstance(r, tuple) else r
    check("...and is not re-pulled", DOWNLOADS == [] and
          r["applied"].get("download_note") == "already downloaded", r["applied"])
except Exception:
    import traceback
    traceback.print_exc()
    FAILS.append("crash")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
