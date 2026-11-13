#!/usr/bin/env python3
"""Regression test for audit finding C63 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: POST /api/onboarding/apply put the chosen primary on the roster (and
could download it) but wrote no active-model, server-backend,
chat-template-args or hermes model.default. mlx-server.sh then loaded its
hardcoded default (Qwen3.8-27B) on the first wake: on a 16 GB Air that the
sheet had just put on Qwen3.5-4B, an in-server 16 GB download that bypassed
the disk guard and a 19 GB model on a 16 GB machine.

AFTER: when the model server is not running (nothing answers on :8080 and no
model-server process is alive — the normal first-run state), apply makes the
pick the active model through the same all-or-nothing helper a switch uses
(_activate_model) and restarts nothing; when it IS running, the pick stays a
roster entry and `applied.primary_note` says to switch in the model menu.

Loads the REAL dashboard/server.py (which execs every aux module) against a
throwaway HOME, the way t_onboarding.py does; subprocess, downloads and any
model start are stubbed. Nothing is woken, downloaded or restarted.
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


TMP = tempfile.mkdtemp(prefix="hermes-c63-")
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


STATE = {"config_rc": 0, "config_sets": [], "starts": 0}


class FakeSubprocess:
    CalledProcessError = Exception
    TimeoutExpired = Exception
    PIPE, DEVNULL, STDOUT = -1, -3, -2

    def run(self, argv, **kw):
        a = " ".join(str(x) for x in argv)
        if "hw.memsize" in a:
            return _R(str(16 * 1024 ** 3))
        if "config set model.default" in a:
            STATE["config_sets"].append(argv[-1])
            return _R("", STATE["config_rc"], "boom" if STATE["config_rc"] else "")
        if "launchctl" in a:
            STATE["starts"] += 1
        return _R("", 1)

    def Popen(self, *a, **k):
        raise RuntimeError("harness: no Popen")


sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_c63", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_c63"] = srv
spec.loader.exec_module(srv)
srv.subprocess = FakeSubprocess()
srv._RAM_GB_CACHE[0] = None
srv.download_model = lambda mid: {"ok": True, "status": "downloading"}
srv._onb_disk_free_gb = lambda p=None: 500.0      # never the real disk
srv.agent_wake = lambda *a, **k: None


def _no_start(*a, **k):
    STATE["starts"] += 1
    return False


srv._mlx_start = _no_start
srv.model_online = lambda: False
srv._mlx_proc_alive = lambda: False


def post(body):
    r = srv.POST_ROUTES["/api/onboarding/apply"](srv.RouteCtx(body=body))
    return r[0] if isinstance(r, tuple) else r


def read(p):
    try:
        return open(p).read().strip()
    except OSError:
        return None


AM = os.path.join(TMP, ".hermes", "dashboard", "active-model")
SB = os.path.join(TMP, ".hermes", "dashboard", "server-backend")
CTA = os.path.join(TMP, ".hermes", "dashboard", "chat-template-args")
M2B, M4B = srv.M2B, srv.M4B

try:
    # review: activation is gated on the pick being local or its download
    # having started — a refused (disk) or never-requested download must not
    # leave the next wake to pull the model inside the server, past the check
    srv._onb_disk_free_gb = lambda p=None: 1.0
    r = post({"primary": M4B, "download": True})
    check("disk too small: the download is refused",
          not r.get("ok") and any("not enough free disk" in e for e in r.get("errors", [])),
          r.get("errors"))
    check("...and the pick is NOT activated (the server would pull it past the "
          "disk check)", read(AM) is None and r["applied"].get("primary_active") is False
          and STATE["config_sets"] == [], (read(AM), r["applied"]))
    srv._onb_disk_free_gb = lambda p=None: 500.0
    r = post({"primary": M4B, "download": False})
    check("not downloaded and no download asked: not activated either",
          read(AM) is None and r["applied"].get("primary_active") is False
          and "not downloaded" in str(r["applied"].get("primary_note")), r["applied"])

    r = post({"primary": M4B, "download": True})
    check("16 GB, model asleep: apply ok", r.get("ok"), r.get("errors"))
    # E22 (2026-11-09 audit): this used to activate the pick as soon as its
    # download STARTED, so a download that then failed left active-model on
    # a model that is not on disk (the next wake pulled it past the disk
    # check). Now a started pick is only PENDING; it is activated by a second
    # apply once its weights are complete (t_e22_onboarding_pending.py).
    check("download only started: NOT activated yet, reported pending (E22)",
          read(AM) is None and r["applied"].get("primary_active") is False
          and r["applied"].get("primary_pending") is True and STATE["config_sets"] == [],
          (read(AM), r["applied"]))
    srv._model_downloaded = lambda mid: True      # the download has completed
    r = post({"primary": M4B, "download": False})
    check("complete on disk, model asleep: apply ok", r.get("ok"), r.get("errors"))
    check("the pick is now the active model (was: active-model never written -> 27B default)",
          read(AM) == M4B and srv.active_model() == M4B, read(AM))
    sb = json.loads(read(SB) or "{}")
    cta = json.loads(read(CTA) or "{}")
    # no venv in a throwaway HOME, so the switch helper downgrades mlx_vlm to
    # mlx_lm exactly as it would on a Mac without it
    check("server-backend + chat-template-args describe the pick",
          sb.get("backend") in ("mlx_vlm", "mlx_lm") and "draft_model" not in sb
          and cta == {"enable_thinking": False}, (sb, cta))
    check("hermes model.default set to the pick", STATE["config_sets"][-1:] == [M4B],
          STATE["config_sets"])
    check("nothing was started or restarted", STATE["starts"] == 0, STATE["starts"])
    check("applied.primary_active is reported", r["applied"].get("primary_active") is True,
          r["applied"])

    STATE["config_sets"][:] = []
    srv.model_online = lambda: True
    r = post({"primary": M2B, "download": True})
    check("model running: apply still ok (roster only)", r.get("ok"), r.get("errors"))
    check("...active model untouched", read(AM) == M4B, read(AM))
    check("...no model.default change", STATE["config_sets"] == [], STATE["config_sets"])
    check("...and the result says to switch in the model menu",
          r["applied"].get("primary_active") is False and "model menu" in
          str(r["applied"].get("primary_note")), r["applied"])

    srv.model_online = lambda: False
    srv._mlx_proc_alive = lambda: True
    r = post({"primary": M2B, "download": True})
    check("server still loading (process alive, not answering): not switched under it",
          read(AM) == M4B and r["applied"].get("primary_active") is False, read(AM))
    srv._mlx_proc_alive = lambda: False

    STATE["config_rc"] = 1
    r = post({"primary": M2B, "download": True})
    check("hermes config set fails: apply reports it",
          not r.get("ok") and any("primary" in e for e in r.get("errors", [])), r.get("errors"))
    check("...and the active model is rolled back", read(AM) == M4B, read(AM))
    STATE["config_rc"] = 0

    r = post({"primary": M4B, "download": False})
    check("re-applying the active pick is a no-op ok (even before its download "
          "finished)",
          r.get("ok") and r["applied"].get("primary_active") is True, r)

    # round 3: the uncensored acknowledgement gate sits in front of all of
    # this — refused, nothing is activated; acknowledged, it activates the
    # same way any pick does (the 64 GB check is the roster's own, by fit)
    UNC9 = "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit"
    STATE["config_sets"][:] = []
    raw = srv.POST_ROUTES["/api/onboarding/apply"](srv.RouteCtx(body={
        "primary": UNC9, "download": True}))
    body, st = (raw[0], raw[1]) if isinstance(raw, tuple) else (raw, 200)
    check("uncensored primary, no ack: 409 and NOT activated",
          st == 409 and body.get("needs_ack") is True and read(AM) == M4B
          and STATE["config_sets"] == [], (st, read(AM)))
    r = post({"primary": UNC9, "download": True, "ack_uncensored": True})
    check("...acknowledged: activated like any pick (model asleep, on disk)",
          r.get("ok") and read(AM) == UNC9 and r["applied"].get("primary_active") is True
          and STATE["config_sets"][-1:] == [UNC9], (r.get("errors"), read(AM)))
    check("...still nothing started or restarted", STATE["starts"] == 0, STATE["starts"])
except Exception:
    import traceback
    traceback.print_exc()
    FAILS.append("crash")

finally:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
