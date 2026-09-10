#!/usr/bin/env python3
"""Regression test for audit finding A04 (docs/audits/2026-09-10-codebase-audit.md).

BEFORE: when the serve turn raised, `_chat_worker()` re-ran the prompt through
the one-shot CLI as `hermes --continue <dashboard conversation key>`.  Both
halves were wrong:

  * the dashboard's `chat-…` file name is not an agent session id.  The
    installed CLI resolves `--continue` against state.db session ids and titles
    (hermes_cli/main.py `_resolve_session_by_name_or_id`) and exits 1 with
    "No session found matching '<id>'." for anything else — so the recovery
    path could not work even when a perfectly good serve session existed.  The
    durable id was sitting in `chat["serve_key"]` (serve's
    `stored_session_id`, which IS a state.db session id).
  * it retried unconditionally — including after `prompt.submit` had gone out
    and after tool events had arrived — so one dropped stream could run an
    action-bearing prompt twice.

(docs/audits/reproduce_20260910.py's duplicate_fallback() showed both.)

AFTER: hermes_rpc stamps `submit_sent` (the request bytes left the socket),
`submitted` (serve acknowledged) and `tool_events` on the job.  The one-shot
fallback runs ONLY when nothing was submitted, and it uses `chat["serve_key"]`
— falling back to a brand-new session, and saying so, if the CLI cannot resolve
it.  After submission the job finishes with an explicit "check the conversation
before repeating an action" instead of re-running anything.

Real functions, extracted with `ast`.  No dashboard, no serve backend, no
model, no subprocess: `hermes_rpc.run_turn` and `run_agent` are stubs and the
chats live in a throwaway tempfile.mkdtemp().
"""
import ast
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def definitions(relative, names, namespace):
    tree = ast.parse((REPO / relative).read_text(), filename=relative)
    found = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in names]
    assert {n.name for n in found} == set(names), \
        "missing %s in %s" % (set(names) - {n.name for n in found}, relative)
    exec(compile(ast.Module(body=found, type_ignores=[]), relative, "exec"),
         namespace)


class Recorder(object):
    """Stands in for both the serve backend and the one-shot CLI."""

    def __init__(self, fail_at, cli_reply=(True, "Done"), serve_key=""):
        self.fail_at = fail_at            # "before-submit" | "after-submit"
        self.cli_reply = cli_reply
        self.serve_key = serve_key
        self.actions = []                 # side effects the agent performed
        self.continued = []               # what --continue was handed
        self.status_calls = []

    # ---- the serve turn ----
    def run_turn(self, job, meta, prompt, save_meta):
        if self.fail_at == "before-submit":
            raise ConnectionError("websocket closed during session.create")
        # hermes_rpc marks the prompt as sent the moment the bytes go out
        job["submit_sent"] = True
        job["submitted"] = True
        job["_submitted_ts"] = time.time()
        if self.fail_at == "after-tool":
            job["tool_events"] = 2
        self.actions.append("serve performed the requested write")
        raise ConnectionError("stream disconnected after a completed tool action")

    # ---- `hermes -z --continue <session>` ----
    def run_agent(self, prompt, session=None):
        self.continued.append(session)
        if session is not None and session != self.serve_key:
            return False, "No session found matching '%s'." % session
        self.actions.append("CLI repeated the requested write")
        return self.cli_reply

    def turn_status(self, sid):
        self.status_calls.append(sid)
        return {"output": "Agent Running: No"}


def worker_namespace(scratch, rec):
    ns = dict(os=os, json=json, time=time, threading=threading, sys=sys,
              traceback=traceback, _state_lock=threading.Lock(),
              CHATS=str(scratch),
              agent_idle_suspended=lambda: False,
              agent_paused=lambda: False,
              model_online=lambda: True,
              agent_wake=lambda wait=True: True,
              hermes_rpc=SimpleNamespace(run_turn=rec.run_turn,
                                         turn_status=rec.turn_status),
              run_agent=rec.run_agent)
    definitions("dashboard/server.py",
                ["read_json", "write_json", "chat_path", "load_chat",
                 "_new_message_id", "stamp_message_ids", "_save_chat_locked",
                 "save_chat", "save_chat_update", "_finish_chat_job"], ns)
    # the module-level regex _chat_worker uses to spot the CLI's
    # "No session found matching '<id>'." — taken from the real source, not
    # re-typed here, so a change to it is caught rather than duplicated.
    ns["re"] = re
    tree = ast.parse((REPO / "dashboard/server.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_CLI_NO_SESSION_RE"
                for t in node.targets):
            mod = ast.fix_missing_locations(
                ast.Module(body=[node], type_ignores=[]))
            exec(compile(mod, "server.py", "exec"), ns)
    assert "_CLI_NO_SESSION_RE" in ns, "_CLI_NO_SESSION_RE not found in server.py"
    definitions("dashboard/server.py", ["_chat_worker"], ns)
    return ns


def new_job():
    return {"reply": "", "ok": False, "done": False, "state": "running",
            "text": "", "status": ""}


# ---------------------------------------------------------------------------
# 1. THE AUDIT PROBE: the serve turn fails AFTER the prompt was submitted
# ---------------------------------------------------------------------------
def test_no_retry_after_submit():
    for label, fail_at in (("after prompt.submit", "after-submit"),
                           ("after tool events", "after-tool")):
        with tempfile.TemporaryDirectory(prefix="hermes-a04-") as d:
            rec = Recorder(fail_at, serve_key="actual-agent-session")
            ns = worker_namespace(Path(d), rec)
            ns["save_chat"]("chat-abc",
                            {"serve_key": "actual-agent-session",
                             "serve_sid": "sid-1",
                             "messages": [{"role": "user",
                                           "text": "Update a file"}]})
            job = new_job()
            ns["_chat_worker"](job, "chat-abc", "Update a file")

            check("THE FIX (%s): the CLI is NOT invoked at all" % label,
                  rec.continued == [], rec.continued)
            check("...so the action ran exactly once (%s)" % label,
                  len(rec.actions) == 1, rec.actions)
            check("the turn is reported as failed (%s)" % label,
                  job["ok"] is False and job["done"] is True, job)
            check("the reply says the message was already submitted (%s)"
                  % label, "submitted" in job["reply"], job["reply"])
            check("the reply tells the user to check before repeating (%s)"
                  % label, "repeating an action" in job["reply"], job["reply"])
            check("it asked serve about the original turn first (%s)" % label,
                  rec.status_calls == ["sid-1"], rec.status_calls)
            check("the failure is still written to the transcript (%s)" % label,
                  [m["text"] for m in ns["load_chat"]("chat-abc")["messages"]]
                  [-1] == job["reply"])
            check("the durable serve key is untouched (%s)" % label,
                  ns["load_chat"]("chat-abc")["serve_key"]
                  == "actual-agent-session")


# ---------------------------------------------------------------------------
# 2. a pre-submission failure DOES fall back — with the right session id
# ---------------------------------------------------------------------------
def test_fallback_uses_serve_key():
    with tempfile.TemporaryDirectory(prefix="hermes-a04-") as d:
        rec = Recorder("before-submit", serve_key="20260910_120000_abc123")
        ns = worker_namespace(Path(d), rec)
        ns["save_chat"]("chat-abc", {"serve_key": "20260910_120000_abc123",
                                     "messages": [{"role": "user",
                                                   "text": "Summarise this"}]})
        job = new_job()
        ns["_chat_worker"](job, "chat-abc", "Summarise this")

        check("THE FIX: --continue gets the DURABLE serve key, not 'chat-abc'",
              rec.continued == ["20260910_120000_abc123"], rec.continued)
        check("the dashboard conversation key is never passed to the CLI",
              "chat-abc" not in rec.continued, rec.continued)
        check("the fallback answered", job["ok"] is True and job["done"] is True,
              job)
        check("the answer is persisted",
              [m["text"] for m in ns["load_chat"]("chat-abc")["messages"]]
              == ["Summarise this", "Done"])


# ---------------------------------------------------------------------------
# 3. no serve_key yet (first turn) -> a NEW session, no bogus --continue
# ---------------------------------------------------------------------------
def test_fallback_without_a_key():
    with tempfile.TemporaryDirectory(prefix="hermes-a04-") as d:
        rec = Recorder("before-submit")
        ns = worker_namespace(Path(d), rec)
        ns["save_chat"]("chat-new", {"messages": [{"role": "user",
                                                   "text": "Hello"}]})
        job = new_job()
        ns["_chat_worker"](job, "chat-new", "Hello")
        check("a conversation with no serve_key starts a NEW CLI session",
              rec.continued == [None], rec.continued)
        check("it answered", job["ok"] is True, job)


# ---------------------------------------------------------------------------
# 4. the CLI cannot resolve the key -> retry once as a new session, and SAY so
# ---------------------------------------------------------------------------
def test_unresolvable_key():
    with tempfile.TemporaryDirectory(prefix="hermes-a04-") as d:
        # serve minted a key but never persisted a row for it
        rec = Recorder("before-submit", serve_key="a-key-the-cli-knows")
        ns = worker_namespace(Path(d), rec)
        ns["save_chat"]("chat-x", {"serve_key": "a-key-the-cli-never-stored",
                                   "messages": [{"role": "user",
                                                 "text": "Hi"}]})
        job = new_job()
        ns["_chat_worker"](job, "chat-x", "Hi")
        check("it tries the saved key first, then a fresh session",
              rec.continued == ["a-key-the-cli-never-stored", None],
              rec.continued)
        check("the user is told the old session could not be resumed",
              "could not be resumed" in job["reply"], job["reply"])
        check("and that the new one has no memory of the conversation",
              "no memory" in job["reply"], job["reply"])
        check("nothing was submitted, so running once here is safe",
              rec.actions == ["CLI repeated the requested write"], rec.actions)


# ---------------------------------------------------------------------------
# 5. hermes_rpc marks the prompt as sent BEFORE waiting for the response
# ---------------------------------------------------------------------------
class FakeWS(object):
    def __init__(self, replies):
        self.sent = []
        self.replies = list(replies)

    def send_text(self, raw):
        self.sent.append(raw)

    def recv_text(self, timeout=None):
        return self.replies.pop(0) if self.replies else None

    def close(self):
        pass


def test_on_sent_callback():
    ns = dict(json=json, time=time, os=os, sys=sys, threading=threading)
    definitions("dashboard/hermes_rpc.py", ["WSError", "ServeSession"], ns)
    srv = ns["ServeSession"].__new__(ns["ServeSession"])
    order = []
    srv.ws = FakeWS([json.dumps({"id": 1, "result": {"ok": True}})])
    srv._next_id = 1
    srv._events = []

    def on_sent():
        order.append(("sent", len(srv.ws.sent)))

    srv.call("prompt.submit", {"session_id": "s", "text": "hi"}, timeout=5,
             on_sent=on_sent)
    check("call() fires on_sent AFTER the bytes are written",
          order == [("sent", 1)], order)

    # a call whose reply never arrives still reports that it was sent — that
    # is the whole point: an unanswered submit may still have been acted on
    srv2 = ns["ServeSession"].__new__(ns["ServeSession"])
    srv2.ws = FakeWS([])
    srv2._next_id = 1
    srv2._events = []
    fired = []
    try:
        srv2.call("prompt.submit", {}, timeout=0.2,
                  on_sent=lambda: fired.append(1))
    except Exception:
        pass
    check("a call that times out still reported the prompt as sent",
          fired == [1], fired)

    rpc = (REPO / "dashboard/hermes_rpc.py").read_text()
    body = rpc.split("def run_turn")[1]
    check("run_turn sets submit_sent through on_sent",
          'job["submit_sent"] = True' in body)
    check("run_turn sets submitted once serve acknowledges",
          'job["submitted"] = True' in body)
    check("run_turn counts tool events", 'job["tool_events"]' in body)

    server = (REPO / "dashboard/server.py").read_text()
    worker = server.split("def _chat_worker")[1].split("\ndef ")[0]
    check("the worker gates the fallback on the submit flags",
          'job.get("submit_sent")' in worker and 'job.get("submitted")' in worker)
    check("the worker no longer passes the dashboard session key to run_agent",
          "run_agent(prompt, session=session)" not in worker)
    check("the worker uses the chat's serve_key",
          'meta.get("serve_key")' in worker)


def main():
    print("A04: CLI fallback resumes the wrong session and retries after "
          "submission (docs/audits/2026-09-10-codebase-audit.md)")
    print("\n1. the audit probe: a drop AFTER prompt.submit")
    test_no_retry_after_submit()
    print("\n2. a drop BEFORE prompt.submit falls back on the durable key")
    test_fallback_uses_serve_key()
    print("\n3. no serve_key yet")
    test_fallback_without_a_key()
    print("\n4. the CLI cannot resolve the saved key")
    test_unresolvable_key()
    print("\n5. hermes_rpc's submitted/tool-event bookkeeping")
    test_on_sent_callback()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
