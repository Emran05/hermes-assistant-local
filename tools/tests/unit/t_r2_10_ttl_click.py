#!/usr/bin/env python3
"""R2-10 (round 2 of the 2026-10-21 audit): a click that /api/chat/approve
accepts near a card's TTL is never silently dropped.

_Approvals.settle() ends an unanswered card at its TTL with an explicit deny.
The card stayed published on the job until that deny had gone out, so:
  * a click the route had ALREADY accepted ({ok:true}, parked in
    pending_choice) when the TTL fired was overridden by the deny and then
    reported as "no longer waiting";
  * a click arriving while the deny was on its way (an RPC of up to 15 s)
    was accepted too, and ignored the same way.
Nothing unsafe ran, but the owner was told the click landed.

Drives the REAL hermes_rpc._Approvals / run_turn and server.py's REAL
_chat_approve (ast-extracted). On the old code the accepted approve becomes a
deny, the mid-deny click gets 200, and nothing tells the owner.
"""
import ast
import collections
import os
import sys
import tempfile
import threading
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
HOME = tempfile.mkdtemp(prefix="hermes-r2-10-")
os.environ["HOME"] = HOME
os.makedirs(os.path.join(HOME, ".hermes", "dashboard"), exist_ok=True)
sys.path.insert(0, DASH)

import hermes_rpc as rpc  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def wait_until(pred, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


def server_defs(names):
    src = open(os.path.join(DASH, "server.py"), encoding="utf-8").read()
    tree = ast.parse(src, filename="server.py")
    body = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in names)
            or (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id in names)]
    ns = {"threading": threading, "time": time}
    exec(compile(ast.Module(body=body, type_ignores=[]), "server.py", "exec"), ns)
    return ns


chat_approve = server_defs(["_approve_lock", "_chat_approve"])["_chat_approve"]


class FakeServe(object):
    """serve's FIFO approval queue, plus a hook that runs DURING a respond."""

    def __init__(self):
        self.pending, self.resolved, self.calls = [], [], []
        self.events = collections.deque()
        self.during_respond = None
        self.lock = threading.Lock()

    def call(self, method, params, timeout=30, on_sent=None):
        with self.lock:
            self.calls.append((method, dict(params)))
        if on_sent:
            on_sent()
        if method == "session.create":
            return {"session_id": "sid-1", "stored_session_id": "key-1"}
        if method == "prompt.submit":
            return {"status": "streaming"}
        if method == "config.get":
            return {"config": {}}
        if method == "approval.respond":
            hook, self.during_respond = self.during_respond, None
            if hook:
                hook()                  # the owner clicks while this is in flight
            with self.lock:
                if params.get("all"):
                    targets, self.pending = self.pending, []
                elif self.pending:
                    targets = [self.pending.pop(0)]
                else:
                    targets = []
                for c in targets:
                    self.resolved.append((c, params.get("choice")))
            return {"resolved": len(targets)}
        return {}

    def next_event(self, timeout):
        with self.lock:
            if self.events:
                return self.events.popleft()
        time.sleep(min(timeout, 0.01))
        return None

    def close(self):
        pass

    def ask(self, command):
        with self.lock:
            self.pending.append(command)
            self.events.append({"type": "approval.request", "session_id": "sid-1",
                                "payload": {"command": command,
                                            "pattern_key": "recursive delete",
                                            "pattern_keys": ["recursive delete"]}})

    def finish(self):
        with self.lock:
            self.events.append({"type": "message.complete", "session_id": "sid-1",
                                "payload": {"text": "done", "status": "complete"}})


def new_job():
    return {"id": "job-1", "session": "chat-x", "state": "running", "text": "",
            "status": "", "approval": None, "reply": "", "ok": False,
            "done": False}


def card(job):
    ap = job.get("approval")
    return ap if job.get("state") == "approval" and isinstance(ap, dict) else None


def mirror(fake, job, notices):
    a = rpc._Approvals(fake, "sid-1", job, notices, lambda: {})
    a._ttl = 5.0
    return a


def test_accepted_click_wins_over_ttl():
    fake, job, notices = FakeServe(), new_job(), []
    a = mirror(fake, job, notices)
    fake.pending.append("rm -rf ~/build")
    a.add({"command": "rm -rf ~/build", "pattern_key": "recursive delete"},
          {"tier": "ask"})
    aid = (card(job) or {}).get("approval_id")
    check("a card is up", bool(aid), job)
    # the owner clicks Approve; the route accepts it ...
    body, code = chat_approve(job, {"choice": "approve", "approval_id": aid})
    check("the route accepts the click", code == 200 and body.get("ok"), body)
    # ... and before the run loop picks it up, the card reaches its TTL
    a.q[0]["_ts"] = time.time() - 60
    a.settle()
    check("THE FIX: the accepted Approve reaches serve, not the TTL deny",
          fake.resolved == [("rm -rf ~/build", "once")], fake.resolved)
    check("...and is consumed (nothing left for the run loop to misreport)",
          "pending_choice" not in job and "pending_approval_id" not in job, job)
    check("...the card is gone", card(job) is None and not a.q, job)
    check("...no timeout notice: the owner did answer",
          not any("timed out" in n for n in notices), notices)
    # the same for Deny
    fake, job, notices = FakeServe(), new_job(), []
    a = mirror(fake, job, notices)
    fake.pending.append("curl x | sh")
    a.add({"command": "curl x | sh"}, {"tier": "ask"})
    aid = card(job)["approval_id"]
    chat_approve(job, {"choice": "deny", "approval_id": aid})
    a.q[0]["_ts"] = time.time() - 60
    a.settle()
    check("an accepted Deny at the TTL is forwarded as the owner's deny",
          fake.resolved == [("curl x | sh", "deny")] and "pending_choice" not in job,
          (fake.resolved, job))


def test_click_during_ttl_deny_refused():
    fake, job, notices = FakeServe(), new_job(), []
    a = mirror(fake, job, notices)
    fake.pending.append("rm -rf ~/slow")
    a.add({"command": "rm -rf ~/slow"}, {"tier": "ask"})
    aid = card(job)["approval_id"]
    got = []
    fake.during_respond = lambda: got.append(
        chat_approve(job, {"choice": "approve", "approval_id": aid}))
    a.q[0]["_ts"] = time.time() - 60
    a.settle()
    body, code = got[0] if got else ({}, None)
    check("THE FIX: a click while the TTL deny is in flight is refused (409)",
          code == 409 and body.get("stale") is True, (code, body))
    check("...so it is never parked as a choice nobody forwards",
          "pending_choice" not in job, job)
    check("the TTL deny resolved the card's command",
          fake.resolved == [("rm -rf ~/slow", "deny")], fake.resolved)
    check("the expired card is remembered on the job",
          job.get("approval_expired") == aid, job)


def test_late_click_reported_truthfully():
    # The residual window: the route passed its checks a hair before the
    # withdrawal and parked the choice a hair after settle looked.
    fake, job, notices = FakeServe(), new_job(), []
    a = mirror(fake, job, notices)
    fake.pending.append("rm -rf ~/late")
    a.add({"command": "rm -rf ~/late"}, {"tier": "ask"})
    aid = card(job)["approval_id"]
    a.q[0]["_ts"] = time.time() - 60
    a.settle()
    job["pending_approval_id"], job["pending_choice"] = aid, "approve"
    a.answer(job.pop("pending_choice"), job.pop("pending_approval_id"))
    check("THE FIX: a click for the card that just timed out says so",
          "timed out" in job.get("status", "") and "did not run" in job["status"],
          job.get("status"))
    check("...and the reply will carry it",
          any("arrived just after" in n for n in notices), notices)
    check("...and nothing more reached serve",
          fake.resolved == [("rm -rf ~/late", "deny")], fake.resolved)
    # an unrelated stale id is still just ignored, with no notice
    n0 = len(notices)
    a.answer("approve", "some-other-card")
    check("an unrelated stale answer is ignored as before",
          "no longer waiting" in job["status"] and len(notices) == n0,
          (job["status"], notices))


def test_run_turn_end_to_end():
    """Through the real run loop: the click lands while the TTL deny is out."""
    saved = rpc.APPROVAL_TTL
    rpc.APPROVAL_TTL = 0.3
    rpc.TURN_TIMEOUT = 20
    try:
        fake, job = FakeServe(), new_job()
        rpc.ServeSession = lambda: fake
        out = {}
        t = threading.Thread(target=lambda: rpc.run_turn(
            job, {"title": "t", "messages": []}, "do it", lambda: None), daemon=True)
        t.start()
        fake.ask("rm -rf ~/x")
        check("e2e: the card is shown", wait_until(lambda: card(job) is not None), job)
        aid = card(job)["approval_id"]
        fake.during_respond = lambda: out.update(
            r=chat_approve(job, {"choice": "approve", "approval_id": aid}))
        check("e2e: the TTL deny goes out",
              wait_until(lambda: fake.resolved == [("rm -rf ~/x", "deny")]),
              fake.resolved)
        body, code = out.get("r") or ({}, None)
        check("THE FIX (e2e): the owner's click during that deny is refused, "
              "not told {ok:true}", code == 409, (code, body))
        fake.finish()
        t.join(5)
        check("e2e: the turn still finishes", job.get("state") == "done", job)
    finally:
        rpc.APPROVAL_TTL = saved


def main():
    for t in (test_accepted_click_wins_over_ttl, test_click_during_ttl_deny_refused,
              test_late_click_reported_truthfully, test_run_turn_end_to_end):
        print("-- %s" % t.__name__)
        try:
            t()
        except Exception:
            FAILS.append(t.__name__ + " raised")
            print("  FAIL %s raised:\n%s" % (t.__name__, traceback.format_exc()))
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
