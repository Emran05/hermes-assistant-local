"""Shared fixture for the Message Center regression tests (t_r2_23_*, t_c39_*).

The leading underscore keeps run.sh from treating this file as a suite.

load() exec's the REAL dashboard/aux_messages.py into a bare namespace with
the server.py globals it touches stubbed (DATA, read_json, _state_lock,
_widget_cache, _cached, register_*, WIDGETS, EXPANDERS, layout_inject) in a
throwaway data directory.  No dashboard, no app, no chat.db.
"""
import atexit
import json
import os
import shutil
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

FAILS = []
PASSES = [0]
_DIRS = []
atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in _DIRS])


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


class Ctx(object):
    def __init__(self, body=None, query=None):
        self.body = body
        self.query = query or {}


def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load():
    """-> (namespace, data_dir, token)."""
    data = tempfile.mkdtemp(prefix="msg-test-")
    _DIRS.append(data)
    g = {"__name__": "msg_test", "DATA": data,
         "_state_lock": threading.Lock(), "_widget_cache": {},
         "_cached": lambda key, ttl, fn: fn(), "read_json": _read_json,
         "WIDGETS": {"messages": {}}, "EXPANDERS": {},
         "GET_ROUTES": {}, "POST_ROUTES": {},
         "layout_inject": lambda *a, **k: None}
    g["register_get"] = lambda p, fn: g["GET_ROUTES"].__setitem__(p, fn)
    g["register_post"] = lambda p, fn: g["POST_ROUTES"].__setitem__(p, fn)
    path = os.path.join(DASH, "aux_messages.py")
    with open(path) as f:
        exec(compile(f.read(), path, "exec"), g)
    with open(os.path.join(data, "messages-token")) as f:
        tok = f.read().strip()
    return g, data, tok


def convo(ident="+15550003001", last="hi", ts=1700000000.0, **kw):
    c = {"name": kw.pop("name", ident), "ident": ident, "group": False,
         "participants": 2, "last": last, "from_me": False, "sender": ident,
         "ts": ts, "unread": 1, "attachment": False, "reaction": False,
         "today_count": 1, "service": "iMessage"}
    c.update(kw)
    return c
