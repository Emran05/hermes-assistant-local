#!/usr/bin/env python3
"""Round-2 regression test for C59.1 (docs/audits/2026-10-21-codebase-audit.md
C59 follow-up): the inline switch routes in server.py coerced their value.

BEFORE: /api/agent/idle_config did `if d.get("enabled")`, /api/agent/prewarm
called set_prewarm_enabled(d.get("enabled")) (-> bool()), /api/models/thinking
passed bool(d.get("enabled")) and /api/model/mem_override bool(d.get("allow")).
bool("false") is True, so a client that sent the STRING "false" turned
idle-suspend, prewarm, thinking and the memory-ceiling override ON.

AFTER: Handler._bool_arg accepts only a JSON boolean (aux_claudebridge's
_cb_bool_body rule) and answers 400 for anything else, before anything is
applied — the stored value is untouched.

Runs the REAL Handler (ast-extracted from dashboard/server.py) on an
ephemeral loopback port with the lifecycle helpers stubbed. No dashboard, no
model, no ~/.hermes. HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def post(port, path, obj):
    raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    st, _, body = H.req(port, "POST", path, raw)
    return st, H.as_json(body) or {}


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-c59-") as d:
        off = os.path.join(d, "idle-suspend-off")
        mins = os.path.join(d, "idle-suspend-min")
        ovr = os.path.join(d, "mem-override")
        calls = {"prewarm": [], "thinking": []}

        def set_prewarm_enabled(on):
            calls["prewarm"].append(on)
            return bool(on)

        def set_model_thinking(enabled, mid=None):
            calls["thinking"].append((enabled, mid))
            return {"ok": True, "enabled": bool(enabled), "restarted": False}

        ns = H.http_namespace(
            d, IDLE_SUSPEND_OFF=off, IDLE_MIN_FILE=mins, IDLE_MIN_CAP=10080,
            idle_suspend_enabled=lambda: not os.path.exists(off),
            _idle_min=lambda: 10.0,
            set_prewarm_enabled=set_prewarm_enabled,
            prewarm_payload=lambda: {"enabled": True},
            set_model_thinking=set_model_thinking,
            MEM_OVERRIDE_FILE=ovr, MLX_SOFT_GB=50, MLX_HARD_GB=56)
        srv = H.Server(ns["Handler"])
        port = srv.port
        try:
            # ---- idle_config --------------------------------------------
            open(off, "w").close()               # idle-suspend currently OFF
            st, j = post(port, "/api/agent/idle_config", {"enabled": "false"})
            check('idle_config {"enabled":"false"} -> 400', st == 400, (st, j))
            check("...and idle-suspend stays OFF (was: the string turned it ON)",
                  os.path.exists(off))
            st, j = post(port, "/api/agent/idle_config",
                         {"enabled": "yes", "minutes": 30})
            check("a bad 'enabled' refuses the WHOLE request (minutes untouched)",
                  st == 400 and not os.path.exists(mins), (st, j))
            for bad in (1, 0, None, [], {}, "true"):
                st, _ = post(port, "/api/agent/idle_config", {"enabled": bad})
                check("idle_config enabled=%r -> 400" % (bad,), st == 400, st)
            check("...none of them changed the marker", os.path.exists(off))
            st, j = post(port, "/api/agent/idle_config", {"enabled": True})
            check("enabled:true still turns it on",
                  st == 200 and j.get("enabled") is True and not os.path.exists(off),
                  (st, j))
            st, j = post(port, "/api/agent/idle_config", {"enabled": False})
            check("enabled:false still turns it off",
                  st == 200 and j.get("enabled") is False and os.path.exists(off),
                  (st, j))
            st, j = post(port, "/api/agent/idle_config", {"minutes": 25})
            check("a minutes-only body needs no 'enabled' (partial update)",
                  st == 200 and open(mins).read() == "25.0", (st, j))

            # ---- prewarm ------------------------------------------------
            st, j = post(port, "/api/agent/prewarm", {"enabled": "false"})
            check('prewarm {"enabled":"false"} -> 400', st == 400, (st, j))
            check("...and set_prewarm_enabled was NOT called (was: called, -> True)",
                  calls["prewarm"] == [], calls["prewarm"])
            st, j = post(port, "/api/agent/prewarm", {})
            check("prewarm without 'enabled' -> 400 (unchanged)", st == 400, st)
            st, j = post(port, "/api/agent/prewarm", {"enabled": False})
            check("prewarm enabled:false is passed through as False",
                  st == 200 and calls["prewarm"] == [False], (st, calls["prewarm"]))

            # ---- models/thinking ----------------------------------------
            st, j = post(port, "/api/models/thinking", {"enabled": "false"})
            check('thinking {"enabled":"false"} -> 400', st == 400, (st, j))
            check("...and the model server was not touched (was: thinking ON + restart)",
                  calls["thinking"] == [], calls["thinking"])
            st, j = post(port, "/api/models/thinking", {})
            check("thinking without 'enabled' -> 400 (was: silently OFF + restart)",
                  st == 400 and calls["thinking"] == [], (st, calls["thinking"]))
            st, j = post(port, "/api/models/thinking", {"enabled": True, "id": [1]})
            check("thinking with a non-string id -> 400",
                  st == 400 and calls["thinking"] == [], (st, calls["thinking"]))
            st, j = post(port, "/api/models/thinking", {"enabled": True})
            check("thinking enabled:true reaches set_model_thinking(True, None)",
                  st == 200 and calls["thinking"] == [(True, None)],
                  (st, calls["thinking"]))

            # ---- model/mem_override -------------------------------------
            st, j = post(port, "/api/model/mem_override", {"allow": "false"})
            check('mem_override {"allow":"false"} -> 400', st == 400, (st, j))
            check("...and the over-limit override was NOT switched on",
                  not os.path.exists(ovr))
            st, j = post(port, "/api/model/mem_override", {"allow": True})
            check("allow:true still sets it", st == 200 and os.path.exists(ovr), (st, j))
            st, j = post(port, "/api/model/mem_override", {"allow": False})
            check("allow:false still clears it",
                  st == 200 and not os.path.exists(ovr), (st, j))

            check("no request crashed the handler", not srv.errors, srv.errors[:1])
        finally:
            srv.close()
    H.finish()


if __name__ == "__main__":
    main()
