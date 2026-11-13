#!/usr/bin/env python3
"""R4 review of E22 (docs/audits/2026-11-09-codebase-audit.md): a fresh Mac
must still end up with the model the owner picked.

The E22 fix stopped apply from activating a primary whose download had only
STARTED (right: a failed pull then left active-model naming weights that were
not on disk). But it made activation wait for a "Make it the assistant"
button that lives only on the setup sheet's models step. A 6-17 GB pull takes
minutes, the owner presses Continue and Done, the done marker means the sheet
never opens again, and on a fresh Mac active-model is absent — so the first
wake loads mlx-server.sh's hardcoded 27B default instead of the pick (the
C63 failure C63 had fixed; on a 16 GB Mac that is a model that cannot fit,
pulled inside the model server past the disk check).

AFTER: a pending pick is promoted by a watcher (one step =
`_onb_primary_promote()`) once its weights and drafter are COMPLETE and the
model server is DOWN (the same `_onb_activate_primary` rule apply always
used: restarts nothing) — and only while active-model still says what it said
when the pick was made. A pick the owner superseded in the model menu, a
failed download, or a running server never activates anything.

Real dashboard/server.py + every aux module against a throwaway HOME;
downloads, subprocess and model starts are stubbed.
"""
import importlib.util
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


TMP = tempfile.mkdtemp(prefix="hermes-e22p-")
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
            return _R(str(16 * 1024 ** 3))
        if "config set model.default" in a:
            STATE["config_sets"].append(argv[-1])
            return _R("", 0, "")
        if "launchctl" in a:
            STATE["starts"] += 1
        return _R("", 1)

    def Popen(self, *a, **k):
        raise RuntimeError("harness: no Popen")


sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_e22p", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_e22p"] = srv
spec.loader.exec_module(srv)
srv.subprocess = FakeSubprocess()
srv._RAM_GB_CACHE[0] = None
srv.download_model = lambda mid: {"ok": True, "status": "downloading"}
srv._onb_disk_free_gb = lambda p=None: 500.0
srv.agent_wake = lambda *a, **k: None
srv._onb_bg_watch = lambda: None
WATCH = [0]
srv._onb_primary_watch = lambda: WATCH.__setitem__(0, WATCH[0] + 1)   # no thread here


def _no_start(*a, **k):
    STATE["starts"] += 1
    return False


srv._mlx_start = _no_start
UP = [False]
srv.model_online = lambda: UP[0]
srv._mlx_proc_alive = lambda: UP[0]
srv._bg_probe = lambda: False
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
PEND = os.path.join(D, "onboarding-primary.pending")
M2B, M4B, M9B = srv.M2B, srv.M4B, srv.M9B
promote = getattr(srv, "_onb_primary_promote", None)


def step():
    return promote() if callable(promote) else None


try:
    print("== fresh Mac: pick, download starts, the owner finishes setup ==")
    r = post({"primary": M4B, "download": True})
    check("apply: pending, not active yet (E22 kept)", read(AM) is None
          and r["applied"].get("primary_pending") is True, (read(AM), r["applied"]))
    check("apply started the pending-pick watcher", WATCH[0] >= 1, WATCH[0])
    srv.POST_ROUTES["/api/onboarding/done"](srv.RouteCtx(body={}))
    check("a watcher step exists (_onb_primary_promote)", callable(promote))
    step()
    check("download still running: nothing activated", read(AM) is None
          and STATE["config_sets"] == [], read(AM))

    print("== the download completes while the model server is down ==")
    LOCAL.add(M4B)
    step()
    check("the pick becomes the assistant without reopening the sheet "
          "(was: active-model absent, so the next wake loaded the 27B default)",
          read(AM) == M4B and STATE["config_sets"][-1:] == [M4B], (read(AM), STATE))
    check("...the pending record is cleared", read(PEND) is None, read(PEND))
    check("...and nothing was started or restarted", STATE["starts"] == 0, STATE["starts"])

    print("== a running model server is never touched ==")
    r = post({"primary": M9B, "download": True})
    check("(a second pick is pending)", read(PEND) is not None and read(AM) == M4B, read(PEND))
    LOCAL.add(M9B)
    UP[0] = True
    step()
    check("complete, but the server is up: active-model unchanged",
          read(AM) == M4B and read(PEND) is not None, (read(AM), read(PEND)))
    UP[0] = False
    step()
    check("...activated on a later step, once the server is down",
          read(AM) == M9B and read(PEND) is None, (read(AM), read(PEND)))
    check("still nothing started", STATE["starts"] == 0, STATE["starts"])

    print("== a pick the owner superseded is never activated ==")
    LOCAL.discard(M2B)
    r = post({"primary": M2B, "download": True})
    check("(pending)", read(PEND) is not None, read(PEND))
    with open(AM, "w") as f:
        f.write(M4B)                            # the owner switched in the model menu
    LOCAL.add(M2B)
    step()
    check("the owner's menu choice stands; the stale pick is dropped",
          read(AM) == M4B and read(PEND) is None, (read(AM), read(PEND)))
    st = state()
    check("...and the sheet no longer offers it", st.get("primary_pending") is None,
          st.get("primary_pending"))

    print("== a failed download activates nothing ==")
    LOCAL.discard(M2B)
    r = post({"primary": M2B, "download": True})
    for _ in range(3):
        step()
    check("never complete: active-model unchanged, the pick still pending",
          read(AM) == M4B and read(PEND) is not None, (read(AM), read(PEND)))

    print("== an unreadable/old pending record never auto-activates ==")
    with open(PEND, "w") as f:
        f.write(M2B + "\n")                     # one line: no base recorded
    LOCAL.add(M2B)
    step()
    check("no recorded base: not activated by the watcher", read(AM) == M4B, read(AM))
except Exception:
    import traceback
    traceback.print_exc()
    FAILS.append("crash")
finally:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
