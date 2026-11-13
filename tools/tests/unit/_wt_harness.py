"""Shared loader for the aux_watchtower unit suites (k08: C20, C48, C50, B22 …).

Not a suite itself (the runner skips files that start with "_").  exec()s the
REAL dashboard/aux_watchtower.py the way server.py's aux loader does — into a
globals dict — against a throwaway HOME, with server.py's own read_json /
write_json / _cached lifted out of server.py by AST so the persistence semantics
are the real ones.  Both background threads are pre-marked as started, so
loading the module starts nothing; every network/model/subprocess entry point is
a stub the suite can replace.

WT_AUX_PATH / WT_JS_PATH point a suite at another copy of the module (e.g. the
pre-fix file from `git show ade42af:dashboard/aux_watchtower.py`) — that is how
each suite is shown to FAIL on the old code.
"""
import ast
import io
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(HERE)))
DASH = os.path.join(REPO, "dashboard")
AUX = os.environ.get("WT_AUX_PATH") or os.path.join(DASH, "aux_watchtower.py")
JS = os.environ.get("WT_JS_PATH") or os.path.join(DASH, "aux_watchtower.js")
if DASH not in sys.path:
    sys.path.insert(0, DASH)             # `import untrusted`, as in the live process

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   " + name)
    else:
        FAILS.append(name)
        print("  FAIL " + name + (("  <- " + str(extra)[:400]) if extra != "" else ""))


def finish():
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


def _server_defs(names):
    path = os.path.join(DASH, "server.py")
    tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets):
            keep.append(node)
    ns = {"os": os, "sys": sys, "json": json, "time": time, "threading": threading}
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), ns)
    return ns


class Ctx:
    def __init__(self, query=None, body=None):
        self.query = query or {}
        self.body = body if body is not None else {}

    def q1(self, k, d=""):
        v = self.query.get(k)
        if isinstance(v, list):
            return v[0] if v else d
        return d if v is None else v


def load(home=None, path=None):
    """Exec the module into a fresh namespace. Returns (g, home, data)."""
    home = home or tempfile.mkdtemp(prefix="wt-home-")
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    srv = _server_defs({
        "read_json", "write_json", "_cached", "_widget_cache",
        # write_json moves an unparsable target aside first (C27)
        "_json_unparsable", "_quarantine_corrupt",
        # _cached's last-good fallback + retry backoff (C12), and the dict
        # subclass whose pop/del/clear also forgets that fallback (C17)
        "_WidgetCache", "_widget_cache_good", "_widget_cache_fails",
        "_CACHE_RETRY_S", "_CACHE_STALE_X", "_cache_failed"})
    routes = {"get": {}, "post": {}}

    def _no_net(*a, **k):
        raise RuntimeError("network disabled in the unit tier")

    g = {
        "__name__": "aux_watchtower_unit", "__builtins__": __builtins__,
        "HOME": home, "DATA": data, "HERE": DASH,
        "_state_lock": threading.Lock(),
        "read_json": srv["read_json"], "write_json": srv["write_json"],
        "_cached": srv["_cached"], "_widget_cache": srv["_widget_cache"],
        "register_get": lambda p, f: routes["get"].__setitem__(p, f),
        "register_post": lambda p, f: routes["post"].__setitem__(p, f),
        "WIDGETS": {}, "EXPANDERS": {},
        "model_online": lambda: False, "agent_paused": lambda: False,
        "get_settings": lambda: {}, "get_tasks": lambda: {"tasks": []},
        "weather": lambda: {}, "macos_calendar": lambda: {"available": False,
                                                           "reason": "unit"},
        "w_crypto": lambda: {}, "w_hackernews": lambda: {}, "w_github": lambda: {},
        "SYS_HISTORY": [], "_sys_sample": lambda: {}, "expand_battery": lambda: {},
        "BRIEFING_FILE": os.path.join(data, "briefing.json"),
        "_briefing_is_sane": lambda t: "##" in (t or ""),
        "_briefing_generating": False,
        "MODEL_URL": "http://127.0.0.1:9/v1/models",
        "ACTIVE_MODEL_FILE": os.path.join(data, "active-model"),
        "DEFAULT_MODEL": "unit-model",
        "HERMES": os.path.join(home, "no-such-hermes"),
        "_hermes_env": lambda: dict(os.environ),
        "_SSL_CTX": None, "_news_parse_date": lambda s: None, "_http_json": _no_net,
        "_watchtower_thread_started": True, "_intel_thread_started": True,
    }
    src = io.open(path or AUX, encoding="utf-8").read()
    exec(compile(src, path or AUX, "exec"), g)
    g["__routes__"] = routes
    return g, home, data


def wj(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


class FakeResp:
    def __init__(self, body, url):
        self._b = body if isinstance(body, bytes) else body.encode("utf-8")
        self._u = url
        self.status = 200

    def read(self, *a):
        return self._b

    def geturl(self):
        return self._u

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class patch_urlopen:
    """Replace urllib.request.urlopen for the block. handler(req) -> FakeResp
    (or raise). Every call is recorded in .calls as (url, data). An opener's
    .open() (the feed fetcher builds its own, C47.1) is routed the same way."""

    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def __enter__(self):
        import urllib.request as ur
        self._ur, self._orig = ur, ur.urlopen
        self._orig_open = ur.OpenerDirector.open

        def fake(req, *a, **k):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            data = getattr(req, "data", None)
            self.calls.append((url, data))
            return self.handler(req)

        def fake_open(_opener, req, data=None, *a, **k):
            if not hasattr(req, "full_url"):
                req = ur.Request(str(req), data)
            return fake(req)
        ur.urlopen = fake
        ur.OpenerDirector.open = fake_open
        return self

    def __exit__(self, *a):
        self._ur.urlopen = self._orig
        self._ur.OpenerDirector.open = self._orig_open
        return False
