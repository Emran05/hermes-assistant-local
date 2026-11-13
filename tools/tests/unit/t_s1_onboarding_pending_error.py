#!/usr/bin/env python3
"""Seam S1-11 (audit 2026-11-09, E22 leftover) — a failed pending download.

E22 records an onboarding pick whose download STARTED as pending, and GET
/api/onboarding/state reports it as {id, label, ready}. When that download
then FAILED, a re-run of setup still read it as "still downloading": the
state carried no error. It now carries `primary_pending.error` — the same
failure text the model menu shows (server.py's _model_dl_err, read by name
only while _model_dl says "error"), None otherwise.

Loads the REAL dashboard/server.py (every aux module) against a throwaway
HOME; downloads and model starts are stubbed (the t_e22_onboarding_pending
harness). Nothing is woken, downloaded or restarted.
"""
import importlib.util
import json
import os
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


TMP = tempfile.mkdtemp(prefix="hermes-s1-onbpend-")
os.environ["HOME"] = TMP
os.environ.pop("MLX_SOFT_GB", None)
os.environ.pop("MLX_HARD_GB", None)
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")


class _R:
    def __init__(self, out="", rc=0, err=""):
        self.stdout, self.returncode, self.stderr = out, rc, err


STATE = {"config_sets": [], "starts": 0}


class FakeSubprocess:
    CalledProcessError = Exception
    TimeoutExpired = Exception
    PIPE, DEVNULL, STDOUT = -1, -3, -2

    def run(self, argv, **kw):
        a = " ".join(str(x) for x in argv)
        if "hw.memsize" in a:
            return _R(str(64 * 1024 ** 3))
        if "config set model.default" in a:
            STATE["config_sets"].append(argv[-1])
            return _R("", 0, "")
        if "launchctl" in a:
            STATE["starts"] += 1
        return _R("", 1)

    def Popen(self, *a, **k):
        raise RuntimeError("harness: no Popen")


sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_s1_onbpend", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_s1_onbpend"] = srv
spec.loader.exec_module(srv)
srv.subprocess = FakeSubprocess()
srv._RAM_GB_CACHE[0] = None
DOWNLOADS = []
DL_REFUSE = set()


def _dl(mid):
    if mid in DL_REFUSE:
        return {"ok": False, "error": "refused by the harness"}
    DOWNLOADS.append(mid)
    return {"ok": True, "status": "downloading"}


srv.download_model = _dl
srv._onb_disk_free_gb = lambda p=None: 500.0      # never the real disk
srv.agent_wake = lambda *a, **k: None


def _no_start(*a, **k):
    STATE["starts"] += 1
    return False


srv._mlx_start = _no_start
srv.model_online = lambda: False
srv._mlx_proc_alive = lambda: False
srv._bg_probe = lambda: False                     # the background lane is down
LOCAL = set()
srv._model_downloaded = lambda mid: mid in LOCAL


def post(body):
    r = srv.POST_ROUTES["/api/onboarding/apply"](srv.RouteCtx(body=body))
    return r[0] if isinstance(r, tuple) else r


def state():
    r = srv.GET_ROUTES["/api/onboarding/state"](srv.RouteCtx(query={}))
    return r[0] if isinstance(r, tuple) else r


def read(p):
    try:
        return open(p).read().strip()
    except OSError:
        return None


D = os.path.join(TMP, ".hermes", "dashboard")
AM = os.path.join(D, "active-model")
BG = os.path.join(D, "bg-model")
PEND = os.path.join(D, "onboarding-primary.pending")
M2B, M4B, M9B, M27B = srv.M2B, srv.M4B, srv.M9B, srv.M27B

try:
    r = post({"primary": M4B, "download": True})
    check("(premise) the pick is pending", r["applied"].get("primary_pending") is True,
          r["applied"])
    srv._model_dl[M4B] = "downloading"
    pp = state().get("primary_pending") or {}
    check("while it downloads: error is None", "error" in pp and pp["error"] is None
          and pp.get("ready") is False, pp)
    srv._model_dl[M4B] = "error"
    srv._model_dl_err[M4B] = "no interpreter with huggingface_hub (venv missing?)"
    pp = state().get("primary_pending") or {}
    check("a failed download: primary_pending.error says why",
          pp.get("error") == "no interpreter with huggingface_hub (venv missing?)", pp)
    check("...and it is still not ready, still the same pick",
          pp.get("ready") is False and pp.get("id") == M4B, pp)
    srv._model_dl_err[M4B] = None
    pp = state().get("primary_pending") or {}
    check("an error state with no text still reads as failed",
          isinstance(pp.get("error"), str) and pp["error"], pp)
    srv._model_dl[M4B] = "error"
    srv._model_dl_err[M4B] = "x" * 5000
    pp = state().get("primary_pending") or {}
    check("the text is bounded", isinstance(pp.get("error"), str) and len(pp["error"]) <= 600,
          len(pp.get("error") or ""))
    srv._model_dl[M4B] = "downloading"         # a retry started (server clears the text)
    srv._model_dl_err.pop(M4B, None)
    pp = state().get("primary_pending") or {}
    check("a retry clears it again", pp.get("error") is None, pp)
    srv._model_dl[M4B] = "done"
    LOCAL.add(M4B)
    pp = state().get("primary_pending") or {}
    check("complete: ready, no error", pp.get("ready") is True and pp.get("error") is None, pp)
except Exception:
    import traceback
    traceback.print_exc()
    FAILS.append("crash")
finally:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
