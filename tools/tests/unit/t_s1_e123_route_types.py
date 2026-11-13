#!/usr/bin/env python3
"""Seam S1-9 (audit 2026-11-09, E123 remainder) — wrong-typed fields are 400s.

Several aux POST routes called a str method on a field without checking its
type (`(b.get("session") or "").strip()` with `"session": 5`), used a list as
a dict key (`_WT_OPS.get(["x"])`: unhashable), or converted a JSON 1e999 (a
float infinity) with int() and caught only TypeError/ValueError, so the
dashboard answered HTTP 500 (and logged a route failure doctor reports) for
what is a client error. Each now checks the type first and answers its own
module's existing 400 shape, writing nothing.

Real server.py (aux chain included) imported on a throwaway HOME, served by
its REAL Handler on an ephemeral loopback port (tools/tests/unit/_http_harness).
Desktop capture is replaced by a recorder so no screenshot is ever taken; no
model, no network.
"""
import json
import os
import sys
import tempfile

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
import _http_harness as H  # noqa: E402

check = H.check
ROOT = str(H.REPO)
HOME = tempfile.mkdtemp(prefix="hermes-s1-e123-")
DASH = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(os.path.join(DASH, "chats"), exist_ok=True)
os.makedirs(os.path.join(HOME, ".hermes", "memories"), exist_ok=True)
json.dump({"master": {"briefings": False, "news": False}},
          open(os.path.join(DASH, "watchtower.json"), "w"))
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(ROOT, "dashboard"))
import server  # noqa: E402
assert server.DATA == DASH, server.DATA
server._ix_cal_lines = lambda: []
CAPTURES = []
server._desk_capture = lambda **kw: CAPTURES.append(kw) or {"ok": True, "stub": True}

INF = "1e999"          # JSON for a float infinity (json.loads accepts it)
CASES = [
    # (label, path, raw JSON body)
    ("convos meta: session is a number", "/api/sessions/meta",
     '{"session": 5, "pinned": true}'),
    ("convos meta: session is a list", "/api/sessions/meta",
     '{"session": ["a"], "title": "x"}'),
    ("promotion drill: model_id is a number", "/api/models/drill", '{"model_id": 5}'),
    ("promotion drill: id is a dict", "/api/models/drill", '{"id": {"a": 1}}'),
    ("watchtower: op is a list", "/api/watchtower", '{"op": ["set_master"]}'),
    ("watchtower: op is a dict", "/api/watchtower", '{"op": {"x": 1}}'),
    ("claude usage: cap is 1e999", "/api/claude_usage/config", '{"cap": %s}' % INF),
    ("claude usage: cap is -1e999", "/api/claude_usage/config", '{"cap": -%s}' % INF),
    ("desktop capture: display is 1e999", "/api/desktop/capture", '{"display": %s}' % INF),
    ("desktop capture: display is a list", "/api/desktop/capture", '{"display": [1]}'),
    ("dictation finish: duration_ms is 1e999", "/api/dictation/finish",
     '{"text": "hello there", "duration_ms": %s}' % INF),
    ("dictation finish: duration_ms is a dict", "/api/dictation/finish",
     '{"text": "hello there", "duration_ms": {"a": 1}}'),
    ("memory create: name is a number", "/api/memory/create", '{"name": 5, "content": "x"}'),
    ("memory save: name is a list", "/api/memory/save",
     '{"name": ["a.md"], "content": "x", "base_etag": ""}'),
    ("memory delete: name is a dict", "/api/memory/delete", '{"name": {"a": 1}}'),
    ("memory restore: trash_name is a number", "/api/memory/restore", '{"trash_name": 5}'),
    ("youmodel add: file is a number", "/api/youmodel/add", '{"file": 5, "text": "likes tea"}'),
    ("youmodel add: name is a list", "/api/youmodel/add", '{"name": ["x"], "text": "likes tea"}'),
    ("clip run: action is a list", "/api/clip/run", '{"action": ["summarize"], "text": "hi"}'),
    ("clip transform: action is a dict", "/api/clip/transform",
     '{"action": {"a": 1}, "text": "hi"}'),
    ("google auth_code: code is a number", "/api/google/auth_code", '{"code": 5}'),
    ("google auth_code: code is a list", "/api/google/auth_code", '{"code": ["http://x"]}'),
]


def tree():
    out = {}
    for base in (os.path.join(HOME, ".hermes", "memories"),
                 os.path.join(DASH, "chats")):
        for dp, _dn, fns in os.walk(base):
            for fn in fns:
                p = os.path.join(dp, fn)
                out[p] = os.stat(p).st_mtime_ns
    for p in (getattr(server, "CU_CONFIG_FILE", None), getattr(server, "PROMO_FILE", None)):
        if p and os.path.exists(p):
            out[p] = os.stat(p).st_mtime_ns
    return out


srv = H.Server(server.Handler)
try:
    for label, path in {(c[0].split(":")[0], c[1]) for c in CASES}:
        check("(premise) %s is a registered POST route" % path, path in server.POST_ROUTES)
    before = tree()
    for label, path, body in CASES:
        st, _h, b = H.req(srv.port, "POST", path, body.encode(),
                          headers={"Content-Type": "application/json"})
        j = H.as_json(b) or {}
        check("%s -> 400 (not 500)" % label, st == 400, (st, b[:200]))
        check("...with the module's error shape (ok:false + error/reason)",
              j.get("ok") is False and (j.get("error") or j.get("reason")), j)
    check("no desktop capture was taken", CAPTURES == [], CAPTURES)
    check("nothing was written to the memory, chat or config stores", tree() == before,
          sorted(set(tree()) ^ set(before)))

    print("\n== the right types still work ==")
    st, _h, b = H.req(srv.port, "POST", "/api/claude_usage/config", b'{"cap": 5}',
                      headers={"Content-Type": "application/json"})
    check("claude usage: an int cap is stored", st == 200
          and (H.as_json(b) or {}).get("config", {}).get("cap") == 5, (st, b[:200]))
    st, _h, b = H.req(srv.port, "POST", "/api/watchtower", b'{"op": "no_such_op"}',
                      headers={"Content-Type": "application/json"})
    check("watchtower: an unknown string op is still the old 400", st == 400
          and "unknown op" in (H.as_json(b) or {}).get("error", ""), (st, b[:200]))
    st, _h, b = H.req(srv.port, "POST", "/api/desktop/capture", b'{"display": 2}',
                      headers={"Content-Type": "application/json"})
    check("desktop: an int display reaches the capture", st == 200
          and CAPTURES == [{"display": 2}], (st, CAPTURES))
    st, _h, b = H.req(srv.port, "POST", "/api/desktop/capture", b'{}',
                      headers={"Content-Type": "application/json"})
    check("desktop: no display = the main display", st == 200
          and CAPTURES[-1:] == [{"display": None}], (st, CAPTURES))
    st, _h, b = H.req(srv.port, "POST", "/api/memory/create", b'{"name": 5}',
                      headers={"Content-Type": "application/json"})
    check("memory: a wrong-typed name is the same bad_name as an invalid one",
          (H.as_json(b) or {}).get("error") == "bad_name", b[:200])
finally:
    srv.close()
H.finish()
