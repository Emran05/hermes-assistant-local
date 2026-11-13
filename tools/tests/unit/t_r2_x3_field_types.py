#!/usr/bin/env python3
"""Round-2 regression test for X-http-plumbing-3 (cross-cluster review of the
merged 2026-10-21 fixes): inline POST routes dropped the connection with no
response when a FIELD had the wrong type.

BEFORE: B12 guarantees the body is a JSON object, but the inline handlers
called .strip(), SESSION_RE.match() or `in WIDGETS` on whatever a field held.
A number for message/session/path/id/text/weather_city, or a list id / order
element, raised inside do_POST and the client got an empty reply (curl: 000).
/api/chat with attachments that were not a list of strings raised AFTER the
user message had been saved.

AFTER: each of those fields is checked first and a wrong type is a 400 JSON;
nothing is saved or applied. The valid shapes behave as before.

REAL Handler, ast-extracted from dashboard/server.py, on an ephemeral loopback
port; every store the routes would touch is a recording stub.
HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-x3-") as d:
        calls = []

        def rec(name, ret=None):
            def f(*a, **kw):
                calls.append(name)
                return ret
            return f

        def layout_update(fn):
            order = ["clock"]
            if fn(order) is False:
                return None
            calls.append("layout_update")
            return order

        ns = H.http_namespace(
            d, SESSION_RE=re.compile(r"^[A-Za-z0-9._-]{1,80}$"),
            CHATS=d, HIDDEN_SESSIONS=frozenset(), WIDGETS={"clock": {}, "tasks": {}},
            chat_path=lambda s: os.path.join(d, s + ".json"),
            chat_delete=lambda s: None,
            get_access=lambda: {"dirs": []}, ACCESS_FILE=os.path.join(d, "a.json"),
            get_tasks=lambda: {"tasks": []}, save_tasks=rec("save_tasks"),
            tasks_update=lambda fn: (calls.append("tasks_update"),
                                     fn({"tasks": []}), {"tasks": []})[2],
            access_update=lambda fn: (calls.append("access_update"),
                                      fn({"dirs": []}), {"dirs": []})[2],
            settings_update=rec("settings_update", {}), _widget_cache={},
            layout_update=layout_update,
            switch_model=rec("switch_model", {"ok": True}),
            download_model=rec("download_model", {"ok": True}),
            agent_paused=lambda: False, mlx_admission=lambda: (True, 0, 50),
            save_chat_update=rec("save_chat_update"),
            _chat_reserve=lambda s: (True, None), _chat_release=lambda s: None)
        ns["_state_lock"] = __import__("threading").Lock()
        ns["write_json"] = rec("write_json")
        srv = H.Server(ns["Handler"])
        bad = (
            ("/api/chat", {"message": 5, "session": "s1"}),
            ("/api/chat", {"message": "hi", "session": 5}),
            ("/api/chat", {"message": "hi", "session": "s1", "attachments": "a.txt"}),
            ("/api/chat", {"message": "hi", "session": "s1", "attachments": [5]}),
            ("/api/sessions/delete", {"session": 5}),
            ("/api/access", {"op": "add", "path": 5}),
            ("/api/models/switch", {"id": 5}),
            ("/api/models/download", {"id": ["x"]}),
            ("/api/tasks", {"op": "add", "text": 5}),
            ("/api/settings", {"weather_city": 5}),
            ("/api/layout", {"op": "add", "id": [1]}),
        )
        try:
            for path, body in bad:
                del calls[:]
                st, _, raw = H.req(srv.port, "POST", path, json.dumps(body).encode())
                j = H.as_json(raw)
                check("THE FIX: %s %s -> 400 JSON" % (path, json.dumps(body)),
                      st == 400 and isinstance(j, dict) and j.get("ok") is False,
                      (st, raw[:160]))
                check("...and nothing was saved or applied", calls == [], calls)

            del calls[:]
            st, _, raw = H.req(srv.port, "POST", "/api/layout",
                               json.dumps({"op": "set", "order": [[1], "clock", 7]}).encode())
            j = H.as_json(raw) or {}
            check("/api/layout set skips a non-string order element (no crash)",
                  st == 200 and j.get("order") == ["clock"], (st, raw[:160]))

            # the valid shapes still go through
            del calls[:]
            st, _, raw = H.req(srv.port, "POST", "/api/models/switch",
                               json.dumps({"id": " org/m "}).encode())
            check("a string id still reaches switch_model",
                  st == 200 and calls == ["switch_model"], (st, calls))
            del calls[:]
            st, _, raw = H.req(srv.port, "POST", "/api/settings",
                               json.dumps({"weather_city": None}).encode())
            check("weather_city null is still accepted (clears the city)",
                  st == 200 and calls == ["settings_update"], (st, calls))
            del calls[:]
            st, _, raw = H.req(srv.port, "POST", "/api/tasks",
                               json.dumps({"op": "add", "text": "buy milk"}).encode())
            check("a string task text is still saved",
                  st == 200 and (H.as_json(raw) or {}).get("ok") is True
                  and any(c in calls for c in ("save_tasks", "write_json", "tasks_update")),
                  (st, raw[:160], calls))
            del calls[:]
            st, _, raw = H.req(srv.port, "POST", "/api/sessions/delete",
                               json.dumps({"session": "nope"}).encode())
            check("a string session id still answers ok",
                  st == 200 and (H.as_json(raw) or {}).get("ok") is True, (st, raw[:160]))
            check("no request reached socketserver's handle_error",
                  not srv.errors, srv.errors[:1])
        finally:
            srv.close()
    H.finish()


if __name__ == "__main__":
    main()
