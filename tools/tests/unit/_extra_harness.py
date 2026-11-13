"""Shared loader for tests of dashboard/expanders_extra.py.

The REAL file is exec'd the way server.py's aux loader does it, into a dict
that stands in for server.py's globals: the handful of server.py names the
module needs at load time are stubbed (no network, no subprocess unless a test
passes its own), and a throwaway HOME is used. Tests add or override any name
through `extra` before the exec, or by assigning into the returned dict after
it (expanders_extra resolves every global at call time).
"""
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
EXTRA = os.path.join(REPO, "dashboard", "expanders_extra.py")
SERVER = os.path.join(REPO, "dashboard", "server.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %r" % (name, extra))


def finish():
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


def server_defs(names, g):
    """exec the named top-level defs/assignments of the REAL server.py into g."""
    tree = ast.parse(io.open(SERVER, encoding="utf-8").read())
    for node in tree.body:
        if (isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names) or (
                isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in names for t in node.targets)):
            exec(compile(ast.Module([node], []), SERVER, "exec"), g)
    return g


def load(extra=None, home=None):
    home = home or tempfile.mkdtemp(prefix="hermes-extra-")
    g = {
        "__name__": "srv_extra",
        "os": os, "re": re, "json": json, "time": time, "sys": sys,
        "urllib": urllib, "subprocess": subprocess, "shutil": shutil,
        "HOME": home,
        "DATA": os.path.join(home, ".hermes", "dashboard"),
        "STATE_DB": os.path.join(home, ".hermes", "state.db"),
        "EXPANDERS": {}, "WIDGETS": {},
        "register_get": lambda p, f: None,
        "register_post": lambda p, f: None,
        "get_settings": lambda: {},
        "get_access": lambda: {"dirs": []},
        "_cached": lambda key, ttl, fn: fn(),
        "model_online": lambda: False,
        "STARTED": time.time(),
    }
    g.update(extra or {})
    exec(compile(io.open(EXTRA, encoding="utf-8").read(), EXTRA, "exec"), g)
    return g
