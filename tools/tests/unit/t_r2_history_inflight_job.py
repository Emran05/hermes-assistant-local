#!/usr/bin/env python3
"""Round-2 review X-approval-flow-1 (server half): GET /api/history carries
`inflight_job`, the conversation's not-done chat job id (or null), so a
window that is not streaming that turn — reloaded mid-approval, another
window, Quick Ask's hand-off — can attach to it and render the approval card
it is waiting on. Without it the only way to learn the running job's id was a
send that came back 409 busy.

The REAL Handler.do_GET, _chat_inflight, CHAT_JOBS and the hidden-session
constants are ast-extracted from dashboard/server.py; load_chat is a stub.
HERMES_T_SERVER_SRC=<old server.py> runs the same checks against older code.
Offline: no socket, no ~/.hermes.
"""
import ast
import json
import os
import re
import sys
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SRC = os.environ.get("HERMES_T_SERVER_SRC") or os.path.join(REPO, "dashboard", "server.py")
PASSES, FAILS = [0], []


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def namespace():
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    want_fn = {"_chat_inflight", "Handler",
               # E69: case-insensitive ids; E18: the lost-reply notice
               "session_hidden", "session_key", "_clear_awaiting_reply", "_lost_reply_due",
               "_lost_reply_row", "_new_message_id",}
    want_var = {"CHAT_JOBS", "_jobs_lock", "SESSION_RE", "PREWARM_SESSION",
                "DRAFT_SESSION", "HIDDEN_SESSIONS", "HISTORY_UNTRUSTED_NOTICE",
                "_CHAT_PROC_START", "LOST_REPLY_NOTICE"}
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in want_fn:
            body.append(node)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and node.targets[0].id in want_var):
            body.append(node)

    class Base(object):
        pass

    chats = {"chat-a": {"messages": [{"role": "user", "text": "type hello"}], "title": "t"},
             "chat-b": {"messages": [{"role": "user", "text": "hi"}, {"role": "bot", "text": "yo"}]},
             "chat-list": [["not", "a"], ["chat", "object"]]}
    ns = {"re": re, "json": json, "os": os, "sys": sys, "threading": threading,
          "time": time,
          "urllib": urllib, "BaseHTTPRequestHandler": Base, "GET_ROUTES": {},
          "HERE": "/nonexistent",
          "load_chat": lambda sid: json.loads(json.dumps(chats.get(sid, {"messages": []})))}
    exec(compile(ast.Module(body=body, type_ignores=[]), "server.py", "exec"), ns)
    for n in want_fn | want_var:
        if n not in ns:
            raise AssertionError("server.py has no %s" % n)
    return ns


def get(ns, sid):
    out = {}

    class H(ns["Handler"]):
        def _guard(self):
            return True

        def _json(self, obj, status=200):
            out["body"], out["status"] = obj, status

        def send_error(self, code, *a):
            out["status"] = code

    h = object.__new__(H)
    h.path = "/api/history?session=" + urllib.parse.quote(sid)
    h.command = "GET"
    h.headers = {}
    h.do_GET()
    return out.get("status"), out.get("body") or {}


def main():
    print("X-approval-flow-1: /api/history names the conversation's running turn")
    try:
        ns = namespace()
    except Exception as e:
        check("server.py has the history route and _chat_inflight", False, repr(e))
        return finish()
    jobs = ns["CHAT_JOBS"]
    jobs["J7"] = {"id": "J7", "session": "chat-a", "state": "approval", "done": False}
    jobs["J1"] = {"id": "J1", "session": "chat-b", "state": "done", "done": True}

    code, d = get(ns, "chat-a")
    check("the history is still returned", code == 200 and d.get("messages") == [{"role": "user", "text": "type hello"}], d)
    check("THE FIX: it names the turn still running there", d.get("inflight_job") == "J7", d)
    code, d = get(ns, "chat-b")
    check("a conversation whose turn is done reports none", "inflight_job" in d and d.get("inflight_job") is None, d)
    code, d = get(ns, "chat-new")
    check("an unknown conversation reports none", d.get("inflight_job") is None and d.get("messages") == [], d)
    jobs["J7"]["done"] = True
    code, d = get(ns, "chat-a")
    check("once the turn is done it is gone from the answer", d.get("inflight_job") is None, d)
    # review: a chat file that is JSON but not an object (hand-edited) went
    # through dict(...), which raises (the route then drops the connection)
    # or, for a list of pairs like this one, invents keys from it
    jobs["J8"] = {"id": "J8", "session": "chat-list", "state": "approval", "done": False}
    try:
        code, d = get(ns, "chat-list")
    except Exception as e:
        code, d = None, {"raised": repr(e)}
    check("a chat file that is not an object still answers", code == 200 and d.get("messages") == [] and d.get("inflight_job") == "J8", d)
    jobs["D1"] = {"id": "D1", "session": "needsyou", "state": "approval", "done": False}
    code, d = get(ns, "needsyou")
    check("a reserved conversation still answers nothing at all",
          d.get("messages") == [] and d.get("title") == "" and "inflight_job" not in d
          and set(d) <= {"messages", "title", "notice"}, d)
    return finish()


def finish():
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
