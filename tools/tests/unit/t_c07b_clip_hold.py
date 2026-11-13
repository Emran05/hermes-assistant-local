#!/usr/bin/env python3
"""Regression test for C07.2 (round 2, w9; follow-up to audit C07).

BEFORE: a clipboard transform (POST /api/clip/transform) is a direct call to
the model server with no CHAT_JOBS entry, so idle_suspend_loop could boot the
model out in the middle of it, and the owner's transform did not count as
user activity either (the idle clock kept running).

AFTER: aux_clip takes the model hold (`model_hold("clip")`, which counts as
owner activity) BEFORE its model_online() check and holds it for the whole
completion, and calls note_user_activity(); both are resolved by name, so a
build without them still transforms.

Exec's the REAL dashboard/aux_clip.py with the REAL server.py hold registry
(ast-extracted: note_user_activity, _model_holds, model_hold, _model_held);
the one model call (_clip_complete) is replaced. No model, no network.
On the old code the "held during the call" and "activity" checks fail.
"""
import ast
import io
import os
import sys
import threading
import time
import uuid

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
        print("  FAIL %s %r" % (name, extra))


def extract(path, names, ns):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    nodes = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names:
            nodes.append(n)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names
                                               for t in n.targets):
            nodes.append(n)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), ns)


class Ctx:
    def __init__(self, body):
        self.body, self.query = body, {}


def build(with_hold=True, online=True):
    routes = {}
    g = {"__name__": "aux_clip_c07b", "os": os, "time": time, "threading": threading,
         "uuid": uuid, "_last_user_activity": 0.0,
         "MODEL_URL": "http://127.0.0.1:8080/v1/models",
         "model_online": lambda: online,
         "active_model": lambda: "test-model",
         "get_settings": lambda: {},
         "_cached": lambda k, ttl, fn: fn(),
         "register_get": lambda p, f: routes.__setitem__(("GET", p), f),
         "register_post": lambda p, f: routes.__setitem__(("POST", p), f)}
    if with_hold:
        extract(os.path.join(DASH, "server.py"),
                ["note_user_activity", "_model_holds", "_model_holds_lock",
                 "model_hold", "_model_held"], g)
    path = os.path.join(DASH, "aux_clip.py")
    exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), g)
    return g, routes[("POST", "/api/clip/transform")]


def body_of(r):
    return r[0] if isinstance(r, tuple) else r


def main():
    g, handler = build()
    if "model_hold" not in g:
        check("server.py hold registry extracted", False)
    seen = []

    def fake(payload):
        seen.append((g["_model_held"](), [v[0] for v in g["_model_holds"].values()],
                     g["_last_user_activity"]))
        return {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
    g["_clip_complete"] = fake
    r = body_of(handler(Ctx({"action": "summarize", "text": "some clipboard text"})))
    check("the transform still answers", r.get("ok") is True and r.get("result") == "done", r)
    check("the model is HELD for the whole completion (idle loop cannot boot it out)",
          seen and seen[0][0] is True and seen[0][1] == ["clip"], seen)
    check("the transform counts as owner activity", seen and seen[0][2] > 0, seen)
    check("the hold is released after the call", not g["_model_held"](), g["_model_holds"])

    g["_last_user_activity"] = 0.0
    g["_clip_complete"] = lambda p: (_ for _ in ()).throw(TimeoutError("slow"))
    r = handler(Ctx({"action": "summarize", "text": "x"}))
    check("a model error still returns 502", isinstance(r, tuple) and r[1] == 502, r)
    check("...and releases the hold", not g["_model_held"]())

    g, handler = build(online=False)
    held_at_check = []
    g["model_online"] = lambda: held_at_check.append(g["_model_held"]()) or False
    r = handler(Ctx({"action": "summarize", "text": "x"}))
    check("model offline -> 503 model_offline as before",
          isinstance(r, tuple) and r[1] == 503 and r[0].get("error") == "model_offline", r)
    check("the hold is taken BEFORE the online check (no check-then-bootout race)",
          held_at_check == [True], held_at_check)
    check("...and released on the 503 path", not g["_model_held"]())

    g, handler = build(with_hold=False)
    g["_clip_complete"] = lambda p: {"choices": [{"message": {"content": "ok"},
                                                  "finish_reason": "stop"}]}
    r = body_of(handler(Ctx({"action": "summarize", "text": "x"})))
    check("without model_hold/note_user_activity (older server.py) it still transforms",
          r.get("ok") is True, r)

    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
