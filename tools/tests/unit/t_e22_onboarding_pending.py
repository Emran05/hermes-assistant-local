#!/usr/bin/env python3
"""Regression test for E22 (server half) and E111
(docs/audits/2026-11-09-codebase-audit.md).

E22 BEFORE: POST /api/onboarding/apply made the picked primary the ACTIVE
model as soon as its download had only STARTED. When that download then
failed, active-model kept naming a model that was not on disk, and the next
wake had the model server pull the weights itself — past the dashboard's
free-disk check (the C63 hazard, one step later).

E22 AFTER: the primary is activated only when its weights (and drafter) are
complete on disk. A pick whose download started is recorded as PENDING
(`onboarding-primary.pending`), apply says so (`primary_pending`,
`primary_active:false`, a `primary_note`), and GET /api/onboarding/state
reports it with `ready` once the download is complete, so the sheet can offer
"Make it the assistant" — a plain apply with download:false then activates it.
Nothing switches inside apply; the R4 review's watcher (t_e22_onboarding_promote.py)
promotes a complete pending pick while the model server is down.

E111 BEFORE: a download refused for lack of disk had already pointed the
background lane (bg-model) at the model it did not download.
E111 AFTER: on a download request, bg-model is written only when the lane's
model is complete or its download really started.

Loads the REAL dashboard/server.py (every aux module) against a throwaway
HOME; subprocess, downloads and model starts are stubbed. Nothing is woken,
downloaded or restarted.
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


TMP = tempfile.mkdtemp(prefix="hermes-e22-")
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
spec = importlib.util.spec_from_file_location("hermes_server_e22", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_e22"] = srv
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
    print("== E22: a started download does not activate the primary ==")
    r = post({"primary": M4B, "download": True})
    check("apply ok, the download started", r.get("ok") and DOWNLOADS == [M4B],
          (r.get("errors"), DOWNLOADS))
    check("...the pick is NOT the active model (was: activated on 'started')",
          read(AM) is None and STATE["config_sets"] == []
          and r["applied"].get("primary_active") is False, (read(AM), r["applied"]))
    check("...apply says it is pending, with a note naming the next step",
          r["applied"].get("primary_pending") is True
          and "assistant" in str(r["applied"].get("primary_note")), r["applied"])
    check("...and the pick is recorded on disk", (read(PEND) or "").split("\n")[0] == M4B, read(PEND))
    st = state()
    pp = st.get("primary_pending") or {}
    check("state reports the pending pick, not ready while its download runs",
          pp.get("id") == M4B and pp.get("ready") is False and isinstance(pp.get("label"), str),
          st.get("primary_pending"))
    check("nothing was started or restarted", STATE["starts"] == 0, STATE["starts"])

    print("== E22: once complete, a plain apply makes it the assistant ==")
    LOCAL.add(M4B)
    st = state()
    check("state says the pending pick is ready", (st.get("primary_pending") or {}).get("ready") is True,
          st.get("primary_pending"))
    check("...and still has not switched anything on its own",
          read(AM) is None and STATE["config_sets"] == [], read(AM))
    r = post({"primary": M4B, "download": False})
    check("'Make it the assistant' (apply, download:false) activates it",
          r.get("ok") and read(AM) == M4B and r["applied"].get("primary_active") is True
          and STATE["config_sets"][-1:] == [M4B], (r.get("errors"), read(AM)))
    check("...and clears the pending record", read(PEND) is None and state().get("primary_pending") is None,
          (read(PEND), state().get("primary_pending")))
    check("...still nothing started or restarted", STATE["starts"] == 0, STATE["starts"])

    print("== E22: a later choice supersedes a pending one ==")
    DOWNLOADS[:] = []
    r = post({"primary": M9B, "download": True})
    check("a new started download records the new pick", (read(PEND) or "").split("\n")[0] == M9B
          and r["applied"].get("primary_pending") is True, (read(PEND), r["applied"]))
    r = post({"primary": M4B, "download": False})     # M4B is local and already active
    check("picking a local model clears the stale pending pick", read(PEND) is None
          and state().get("primary_pending") is None, read(PEND))
    r = post({"primary": M9B, "download": True})
    check("(pending again)", (read(PEND) or "").split("\n")[0] == M9B, read(PEND))
    srv._onb_disk_free_gb = lambda p=None: 1.0
    r = post({"primary": M27B, "download": True})
    check("a refused download records nothing pending and activates nothing",
          not r.get("ok") and read(PEND) is None and read(AM) == M4B
          and r["applied"].get("primary_active") is False
          and not r["applied"].get("primary_pending"), (read(PEND), r["applied"]))
    srv._onb_disk_free_gb = lambda p=None: 500.0
    r = post({"primary": M9B, "download": True})
    with open(AM, "w") as f:
        f.write(M9B + "\n")                        # the owner switched in the model menu
    check("a pending pick that is already the active model is not offered again",
          state().get("primary_pending") is None and read(PEND) is None, state().get("primary_pending"))
    with open(AM, "w") as f:
        f.write(M4B + "\n")

    print("== E111: a refused download does not repoint the background lane ==")
    with open(BG, "w") as f:
        f.write(M2B + "\n")
    LOCAL.add(M2B)
    DOWNLOADS[:] = []
    srv._onb_disk_free_gb = lambda p=None: 1.0
    r = post({"primary": M27B, "background": M9B, "download": True})
    check("disk too small: refused", not r.get("ok")
          and any("not enough free disk" in e for e in r.get("errors", [])), r.get("errors"))
    check("...bg-model still names the old lane (was: repointed at the 9B)",
          read(BG) == M2B, read(BG))
    check("...and the result does not claim the lane changed",
          r["applied"].get("background_lane") in (None, M2B)
          and r["applied"].get("background") != M9B
          and "lane" in str(r["applied"].get("background_note", "")), r["applied"])
    srv._onb_disk_free_gb = lambda p=None: 500.0
    DL_REFUSE.add(M9B)
    r = post({"primary": M27B, "background": M9B, "download": True})
    check("the lane's own download refused: bg-model unchanged",
          read(BG) == M2B and M9B not in DOWNLOADS, (read(BG), DOWNLOADS))
    DL_REFUSE.clear()
    DOWNLOADS[:] = []
    r = post({"primary": M27B, "background": M9B, "download": True})
    check("its download really started: bg-model now names it",
          r.get("ok") and M9B in DOWNLOADS and read(BG) == M9B, (read(BG), DOWNLOADS, r.get("errors")))
    with open(BG, "w") as f:
        f.write(M2B + "\n")
    LOCAL.add(M9B)
    r = post({"primary": M4B, "background": M9B, "download": True})
    check("a complete lane model is written with nothing to download",
          read(BG) == M9B and r["applied"].get("background_lane") == M9B, (read(BG), r["applied"]))
    LOCAL.discard(M9B)
    with open(BG, "w") as f:
        f.write(M2B + "\n")
    r = post({"background": M9B})
    check("an explicit lane choice with no download asked (the advisor card, which "
          "warns first) still writes bg-model, as before", read(BG) == M9B, read(BG))
except Exception:
    import traceback
    traceback.print_exc()
    FAILS.append("crash")
finally:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
