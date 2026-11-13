#!/usr/bin/env python3
"""Round-2 regression test for X-http-plumbing-2 (cross-cluster review of the
merged 2026-10-21 fixes): an unreadable layout.json / models.json took down all
of /api/hub or /api/models, and the inline do_GET/do_POST chains had no
exception boundary.

BEFORE:
  * get_layout() (every /api/hub call) and _model_registry() (GET /api/models,
    polled every 30 s) read a failed read as "no file" and wrote the default
    back. Since C27 write_json() raises PermissionError for a target it cannot
    read, nobody caught it, and the inline route chain has no try/except — so
    the browser got a dropped connection (curl: 000) and a blank Hub.
  * models.json holding valid JSON of the wrong type (a list) raised
    AttributeError in _model_registry().
  * ANY exception in an inline route dropped the socket with no response.

AFTER: both readers show their default as a view and write nothing (one
rate-limited [store] line); Handler.handle_one_request() is the boundary for
every route — one [route] line, 500 JSON when nothing was sent yet, and no
second answer when the route had already started its own.

REAL code, ast-extracted from dashboard/server.py; an ephemeral loopback
server; temp dirs. HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check
ROOT = os.geteuid() == 0


def store_ns(d):
    ns = dict(os=os, json=json, re=re, time=time, threading=threading, sys=sys,
              _state_lock=threading.Lock(),
              LAYOUT_FILE=os.path.join(d, "layout.json"),
              MODELS_FILE=os.path.join(d, "models.json"),
              WIDGETS={"clock": {}, "weather": {}, "tasks": {}})
    H.extract(["read_json", "write_json", "_layout_ids", "_layout_read",
               "get_layout", "DEFAULT_LAYOUT", "LAYOUT_LEGACY_INJECTED",
               "_model_registry", "_SEED_MODELS", "DEFAULT_MODEL"], ns,
              optional=["_json_unparsable", "_quarantine_corrupt",
                        "read_json_for_update", "_STORE_VIEW_LOG_GAP",
                        "_store_view_logged", "_log_store_view"])
    return ns


def call(fn, *a):
    buf = io.StringIO()
    with redirect_stderr(buf):
        try:
            return fn(*a), None, buf.getvalue()
        except Exception as e:
            return None, e, buf.getvalue()


def test_readers():
    print("\n1. get_layout() / _model_registry() over a store they cannot read")
    if ROOT:
        print("  SKIP unreadable-store cases: running as root")
        return
    with tempfile.TemporaryDirectory(prefix="hermes-r2-x2-") as d:
        ns = store_ns(d)
        lay = os.path.join(d, "layout.json")
        with open(lay, "w") as f:
            json.dump({"order": ["tasks", "clock"], "seen": ["tasks", "clock"]}, f)
        before = open(lay, "rb").read()
        os.chmod(lay, 0)
        got, err, log = call(ns["get_layout"])
        os.chmod(lay, 0o600)
        check("THE FIX: get_layout() over a chmod-000 layout.json does not raise",
              err is None, repr(err))
        check("...it shows the default view (filtered to the catalog)",
              got == {"order": [w for w in ns["DEFAULT_LAYOUT"]
                                if w in ns["WIDGETS"]]}, got)
        check("...and writes nothing over the owner's file",
              open(lay, "rb").read() == before and
              not [f for f in os.listdir(d) if ".corrupt" in f or ".tmp" in f],
              os.listdir(d))
        check("...one [store] line says so", "[store]" in log and "layout.json" in log, log)
        got2, err2, log2 = call(ns["get_layout"])
        check("the readable file is shown again once it can be read",
              err2 is None and got2 == {"order": ["tasks", "clock"]}, (got2, err2))

        mod = os.path.join(d, "models.json")
        mine = {"models": [{"id": "me/my-model", "label": "Mine"}]}
        with open(mod, "w") as f:
            json.dump(mine, f)
        before = open(mod, "rb").read()
        os.chmod(mod, 0)
        got, err, log = call(ns["_model_registry"])
        os.chmod(mod, 0o600)
        check("THE FIX: _model_registry() over a chmod-000 models.json does not raise",
              err is None, repr(err))
        check("...it returns the seed roster as a view",
              isinstance(got, list) and [m.get("id") for m in got]
              == [m["id"] for m in ns["_SEED_MODELS"]], got and [m.get("id") for m in got])
        check("...and the owner's roster is untouched",
              open(mod, "rb").read() == before, open(mod, "rb").read()[:80])
        check("...one [store] line says so", "[store]" in log and "models.json" in log, log)

        # EMFILE on the READ only: it must not read as "no roster" and write
        # the seeds over the owner's models (the file is perfectly healthy).
        real_open = open

        def emfile_once(path, *a, **kw):
            if os.path.abspath(str(path)) == mod and not emfile_once.done:
                emfile_once.done = True
                raise OSError(24, "Too many open files", str(path))
            return real_open(path, *a, **kw)
        emfile_once.done = False
        ns["open"] = emfile_once
        got, err, log = call(ns["_model_registry"])
        del ns["open"]
        check("a transient EMFILE on models.json does not overwrite it with seeds",
              err is None and json.load(real_open(mod)) == mine, (err, real_open(mod).read()[:120]))


def test_wrong_type_models():
    print("\n2. models.json holding a JSON list")
    with tempfile.TemporaryDirectory(prefix="hermes-r2-x2-") as d:
        ns = store_ns(d)
        mod = os.path.join(d, "models.json")
        with open(mod, "w") as f:
            f.write("[1, 2]")
        got, err, log = call(ns["_model_registry"])
        check("THE FIX: a list models.json does not raise AttributeError",
              err is None and isinstance(got, list) and got, repr(err))
        kept = [f for f in os.listdir(d) if f.startswith("models.json.corrupt")]
        check("...the original is kept aside as .corrupt-*", len(kept) == 1, os.listdir(d))


def test_boundary():
    print("\n3. the inline route chain's exception boundary")
    with tempfile.TemporaryDirectory(prefix="hermes-r2-x2-") as d:
        def hub_data():
            raise RuntimeError("hub exploded")

        def agent_power(op):
            raise KeyError("power")

        ns = H.http_namespace(d, hub_data=hub_data, agent_power=agent_power)

        class Half(ns["Handler"]):
            def do_PUT(self):            # test-only verb: answer, THEN raise
                self._json({"ok": True, "partial": True})
                raise ValueError("after the answer")

        srv = H.Server(Half)
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                st, _, body = H.req(srv.port, "GET", "/api/hub", None)
                j = H.as_json(body) or {}
                check("THE FIX: an inline GET route that raises answers 500 JSON",
                      st == 500 and j.get("ok") is False
                      and "RuntimeError" in str(j.get("error")), (st, body[:160]))
                st, _, body = H.req(srv.port, "POST", "/api/agent/pause", b"{}")
                j = H.as_json(body) or {}
                check("...and so does an inline POST route",
                      st == 500 and j.get("ok") is False
                      and "KeyError" in str(j.get("error")), (st, body[:160]))
                st, _, body = H.req(srv.port, "PUT", "/x", b"")
                j = H.as_json(body) or {}
                check("a route that already answered is not answered twice",
                      st == 200 and j == {"ok": True, "partial": True},
                      (st, body[:200]))
                st, _, body = H.req(srv.port, "GET", "/api/nope", None)
                check("an unknown path is still a plain 404", st == 404, st)
            log = buf.getvalue()
            check("each failure is one [route] line naming the route",
                  "[route] GET /api/hub failed: RuntimeError: hub exploded" in log
                  and "[route] POST /api/agent/pause failed: KeyError" in log
                  and "[route] PUT /x failed: ValueError" in log, log)
            check("no traceback reached socketserver's handle_error",
                  not srv.errors, srv.errors[:1])
        finally:
            srv.close()


def main():
    test_readers()
    test_wrong_type_models()
    test_boundary()
    H.finish()


if __name__ == "__main__":
    main()
