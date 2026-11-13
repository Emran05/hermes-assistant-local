"""Shared fixture for the Needs-you regression tests (t_b04_*, t_b08_*, ...).

The leading underscore keeps run.sh from treating this file as a suite.

load() exec's the REAL dashboard/aux_needsyou.py into a bare namespace with
the handful of server.py globals it touches stubbed, under a throwaway HOME.
That is much faster than exec'ing all of server.py (ny_harness.py does that)
and it is hermetic: no background thread (_NY_BG_STARTED is pre-set), no
osascript, no icalBuddy, no model, no network.  Every test that uses it must
call cleanup() (or let atexit do it).
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  %s" % (name, extra))


def finish():
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


_HOMES = []


def _cleanup():
    for h in _HOMES:
        shutil.rmtree(h, ignore_errors=True)


atexit.register(_cleanup)


class Ctx(object):
    """The shape aux route handlers receive (server.py RouteCtx)."""

    def __init__(self, query=None, body=None):
        self.query = query or {}
        self.body = body if body is not None else {}

    def q1(self, key, default=""):
        v = self.query.get(key)
        return v[0] if isinstance(v, list) and v else default


def load(now=None):
    """-> (namespace, data_dir).  A fresh HOME and a fresh module each call."""
    home = tempfile.mkdtemp(prefix="ny-test-")
    _HOMES.append(home)
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    layout = {"order": []}
    g = {
        "__name__": "ny_test",
        "DATA": data,
        "GET_ROUTES": {}, "POST_ROUTES": {},
        "WIDGETS": {}, "EXPANDERS": {},
        "_NY_BG_STARTED": True,            # never start the 60 s build loop
        "CHAT_JOBS": {},
        "apple_apps_enabled": lambda name: True,
        "get_settings": lambda: {},
        "get_layout": lambda: layout,
        "save_layout": lambda lay: None,
        "bg_online": lambda: False,
        "model_online": lambda: False,
        "macos_calendar": lambda: {"available": True, "events": []},
    }
    g["register_get"] = lambda p, fn: g["GET_ROUTES"].__setitem__(p, fn)
    g["register_post"] = lambda p, fn: g["POST_ROUTES"].__setitem__(p, fn)
    if DASH not in sys.path:
        sys.path.insert(0, DASH)
    path = os.path.join(DASH, "aux_needsyou.py")
    with open(path) as f:
        exec(compile(f.read(), path, "exec"), g)
    # the Reminders osascript must never run in a unit test
    g["_ny_rem_fetch"] = lambda: {"available": True, "items": []}
    # no background rebuild threads: every build in a test is an explicit
    # _ny_build() call, so the interleavings under test are the only ones
    g["_ny_kick"] = lambda force=False: None
    return g, data


def write_messages(data, conversations, fda=True, generated_at=None):
    t = time.time() if generated_at is None else generated_at
    with open(os.path.join(data, "messages.json"), "w") as f:
        json.dump({"v": 1, "fda": fda, "generated_at": t, "stored_at": t,
                   "totals": {"unread": 0, "today": 0},
                   "conversations": conversations}, f)


def convo(ident, last, ts, name=None, from_me=False, unread=1):
    return {"name": name or ident, "ident": ident, "group": False,
            "participants": 2, "last": last, "from_me": from_me,
            "sender": name or ident, "ts": ts, "unread": unread,
            "attachment": False, "reaction": False, "today_count": 1,
            "service": "iMessage"}


def visible_ids(payload):
    return [i["id"] for b in ("now", "today", "later")
            for i in (payload.get(b) or [])]
