"""The REAL dashboard/server.py helpers, for a unit suite to hand to an aux module.

C84 (docs/audits/2026-10-21-codebase-audit.md): six suites gave the aux module
under test their own hand-written stand-in for `settings_update` (plus
`read_json` / `write_json` / `get_settings`).  A stand-in only proves the module
works against the stand-in — one of them even used an RLock, which would hide
a re-entrant call that deadlocks the real, non-reentrant `_state_lock`.  This
execs the real definitions out of server.py (by ast, nothing else from the file
runs) into a fresh namespace bound to the suite's own throwaway settings file:

    import server_real
    api = server_real.settings_api(settings_path)          # or (path, lock)
    g.update(api)       # settings_update, settings_set, get_settings,
                        # read_json, write_json, _state_lock, SETTINGS_FILE

Pass `lock=` when the suite already hands the module a `_state_lock`, so the
module and settings_update share ONE lock exactly as they do in server.py.

Stdlib only.  run.sh puts tools/tests/lib on PYTHONPATH for the unit tier; a
suite that should also run on its own does
`sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))` first.
"""
import ast
import json
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
SERVER = os.path.join(REPO, "dashboard", "server.py")

SETTINGS_FUNCS = ("read_json", "write_json", "get_settings", "settings_update", "settings_set")
# what those call in turn (C27): settings_update reads through
# read_json_for_update, and both it and write_json move an unparsable file
# aside instead of overwriting it.  Exec'd alongside, never handed out.
SETTINGS_HELPERS = ("read_json_for_update", "_json_unparsable", "_quarantine_corrupt")

_TREE = [None]


def _tree():
    if _TREE[0] is None:
        with open(SERVER, encoding="utf-8") as f:
            _TREE[0] = ast.parse(f.read(), filename=SERVER)
    return _TREE[0]


def server_functions(names, namespace):
    """exec the named top-level functions of the real server.py into
    `namespace` (which must already hold whatever globals they read)."""
    found = [n for n in _tree().body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    missing = set(names) - {n.name for n in found}
    if missing:
        raise AssertionError("server.py has no %s" % ", ".join(sorted(missing)))
    exec(compile(ast.Module(body=found, type_ignores=[]), SERVER, "exec"), namespace)
    return namespace


def settings_api(settings_file, lock=None):
    """{name: real function} for settings.json, bound to `settings_file`."""
    ns = {"json": json, "os": os, "sys": sys, "threading": threading, "time": time,
          "SETTINGS_FILE": settings_file,
          "_state_lock": lock if lock is not None else threading.Lock()}
    server_functions(SETTINGS_FUNCS + SETTINGS_HELPERS, ns)
    out = {k: ns[k] for k in SETTINGS_FUNCS}
    out["_state_lock"] = ns["_state_lock"]
    out["SETTINGS_FILE"] = settings_file
    return out
