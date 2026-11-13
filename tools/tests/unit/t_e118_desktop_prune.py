#!/usr/bin/env python3
"""E118 (docs/audits/2026-11-09-codebase-audit.md) — desktop screenshots older
than DESK_MAX_AGE_S were pruned only when someone listed or captured, and
GET /api/desktop/shot served them at any age.

Now the store is pruned at module load and in the shot handler, and the shot
handler refuses (and deletes) a frame past the limit.  Execs the REAL
aux_desktop.py into a namespace with a scratch DATA dir.  Offline.
"""
import os, shutil, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
MODULE = os.path.join(REPO, "dashboard", "aux_desktop.py")
PASS = [0]
FAILS = []


def check(name, cond, detail=""):
    if cond:
        PASS[0] += 1
        print("  ok   " + name)
    else:
        FAILS.append(name)
        print("  FAIL " + name + "  <- %r" % (detail,))


TMP = tempfile.mkdtemp(prefix="t-e118-")
DATA = os.path.join(TMP, "dashboard")
DESK = os.path.join(DATA, "desktop")
os.makedirs(DESK)


def frame(name, age_s):
    p = os.path.join(DESK, name)
    with open(p, "wb") as f:
        f.write(b"\xff\xd8 jpeg")
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


class Ctx:
    def __init__(self, q):
        self.q = q
        self.body = {}

    def q1(self, k, d=""):
        return self.q.get(k, d)


old = frame("shot-1000.jpg", 30 * 86400)
old_thumb = frame("thumb-1000.jpg", 30 * 86400)
fresh = frame("shot-2000.jpg", 60)
fresh_thumb = frame("thumb-2000.jpg", 60)

routes = {}
NS = {"__name__": "aux_desktop", "DATA": DATA,
      "register_get": lambda r, f: routes.setdefault(r, f),
      "register_post": lambda r, f: routes.setdefault(r, f)}
with open(MODULE) as f:
    exec(compile(f.read(), MODULE, "exec"), NS)
AGE = NS["DESK_MAX_AGE_S"]

print("E118 — prune at module load")
check("a 30-day-old frame is gone after load", not os.path.exists(old))
check("...and its thumb", not os.path.exists(old_thumb))
check("a fresh frame stays", os.path.exists(fresh) and os.path.exists(fresh_thumb))

print("E118 — the shot route refuses a frame past the limit")
shot = routes["/api/desktop/shot"]
r = shot(Ctx({"name": "shot-2000.jpg"}))
check("a fresh frame is served", isinstance(r, dict) and r.get("ok") and r.get("data_uri"), r)
late = frame("shot-3000.jpg", AGE + 120)
late_thumb = frame("thumb-3000.jpg", AGE + 120)
r = shot(Ctx({"name": "shot-3000.jpg"}))
body = r[0] if isinstance(r, tuple) else r
check("an over-age frame is refused", not body.get("ok") and not body.get("data_uri"), body)
check("...and deleted", not os.path.exists(late) and not os.path.exists(late_thumb))
lt = frame("thumb-4000.jpg", AGE + 120)
frame("shot-4000.jpg", 10)
r = shot(Ctx({"name": "thumb-4000.jpg"}))
body = r[0] if isinstance(r, tuple) else r
check("an over-age thumb is refused too", not body.get("ok") and not body.get("data_uri"), body)
edge = frame("shot-5000.jpg", AGE - 30)
r = shot(Ctx({"name": "shot-5000.jpg"}))
check("a frame just inside the limit is still served", isinstance(r, dict) and r.get("ok"), r)

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASS[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
