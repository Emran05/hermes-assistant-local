#!/usr/bin/env python3
"""X-approval-flow-6 (round 2 of the 2026-10-21 audit): the FIFO mirror never
answers by position when it cannot know serve's order, and no approval
outlives its turn.

serve's _await_gateway_decision appends to the session's queue under a lock,
then emits approval.request OUTSIDE it, so two threads of one session can
reach the dashboard in the opposite order to serve's queue. approval.respond
carries no request id and resolves serve's OLDEST entry — so the owner's
Approve on the card shown (B) approved the other command (A). And _end()
cleared the card without denying serve's entry, so a request left pending at
the end of one turn was resolved by the NEXT turn's first click.

Drives the REAL hermes_rpc.run_turn against a fake serve whose emit order can
be inverted relative to its queue. On the old code the inverted case approves
the command the owner never saw, and the leftover entry survives the turn.
"""
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
HOME = tempfile.mkdtemp(prefix="hermes-r2-x6-")
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


class FakeServe(object):
    def __init__(self):
        self.pending, self.resolved, self.calls = [], [], []
        self.events = collections.deque()
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
            with self.lock:
                if params.get("all"):
                    targets, self.pending = self.pending, []
                elif self.pending:
                    targets = [self.pending.pop(0)]     # serve: the OLDEST entry
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

    def queue(self, command):
        with self.lock:
            self.pending.append(command)

    def emit(self, command):
        with self.lock:
            self.events.append({"type": "approval.request", "session_id": "sid-1",
                                "payload": {"command": command,
                                            "pattern_key": "recursive delete",
                                            "pattern_keys": ["recursive delete"]}})

    def finish(self, text="done"):
        with self.lock:
            self.events.append({"type": "message.complete", "session_id": "sid-1",
                                "payload": {"text": text, "status": "complete"}})


class Turn(object):
    def __init__(self, fake):
        self.fake = fake
        self.job = {"id": "job-1", "session": "chat-x", "state": "running",
                    "text": "", "status": "", "approval": None, "reply": "",
                    "ok": False, "done": False}
        rpc.ServeSession = lambda: fake
        self.t = threading.Thread(target=lambda: rpc.run_turn(
            self.job, {"title": "t", "messages": []}, "do it", lambda: None),
            daemon=True)
        self.t.start()

    def card(self):
        ap = self.job.get("approval")
        return ap if self.job.get("state") == "approval" and isinstance(ap, dict) else None

    def click(self, choice):
        c = self.card()
        if c:
            self.job["pending_approval_id"] = c["approval_id"]
            self.job["pending_choice"] = choice

    def end(self, text="done"):
        self.fake.finish(text)
        self.t.join(5)
        return not self.t.is_alive()


rpc.TURN_TIMEOUT = 20


def test_inverted_order():
    fake = FakeServe()
    turn = Turn(fake)
    # serve's queue: A then B ... but B's approval.request reaches us first
    fake.queue("rm -rf /tmp/zz-A/sub")
    fake.queue("computer_use key cmd+q in Finder")
    fake.emit("computer_use key cmd+q in Finder")
    wait_until(lambda: turn.card() is not None, 2)
    time.sleep(0.05)
    fake.emit("rm -rf /tmp/zz-A/sub")
    wait_until(lambda: len(fake.resolved) >= 2 or turn.card() is None, 2)
    time.sleep(0.2)
    turn.click("approve")              # whatever card is up, the owner approves it
    time.sleep(0.3)
    check("THE FIX: the unseen command is never approved by position",
          ("rm -rf /tmp/zz-A/sub", "once") not in fake.resolved, fake.resolved)
    check("THE FIX: both are denied in one deny-all",
          sorted(fake.resolved) == [("computer_use key cmd+q in Finder", "deny"),
                                    ("rm -rf /tmp/zz-A/sub", "deny")]
          and any(p.get("all") for m, p in fake.calls if m == "approval.respond"),
          fake.resolved)
    check("...and no card is shown for either", turn.card() is None, turn.job)
    turn.end("I could not do that.")
    check("THE FIX: the reply says two approvals arrived at once",
          "Two approvals were requested at the same time" in turn.job["reply"],
          turn.job["reply"])


def test_leftover_denied_at_end():
    fake = FakeServe()
    turn = Turn(fake)
    fake.queue("rm -rf ~/from-a-subagent")
    fake.emit("rm -rf ~/from-a-subagent")
    check("a card is up", wait_until(lambda: turn.card() is not None), turn.job)
    # the turn's own agent thread finishes while that card still waits
    check("the turn ends", turn.end("Main task done."), turn.job)
    check("THE FIX: the leftover entry is denied when the turn ends",
          fake.resolved == [("rm -rf ~/from-a-subagent", "deny")]
          and not fake.pending, (fake.resolved, fake.pending))
    check("...with a deny-all, so nothing of this turn is left in serve's queue",
          any(p.get("all") for m, p in fake.calls if m == "approval.respond"),
          fake.calls)
    check("...and the reply says so",
          "still waiting when this turn ended" in turn.job["reply"]
          and turn.job["reply"].startswith("Main task done."), turn.job["reply"])
    check("...the card is gone", turn.job.get("approval") is None, turn.job)

    # the NEXT turn: its first click lands on its own card, not on the leftover
    turn2 = Turn(fake)
    fake.queue("rm -rf ~/next-turn")
    fake.emit("rm -rf ~/next-turn")
    wait_until(lambda: turn2.card() is not None)
    turn2.click("approve")
    wait_until(lambda: ("rm -rf ~/next-turn", "once") in fake.resolved
               or ("rm -rf ~/from-a-subagent", "once") in fake.resolved)
    check("THE FIX: the next turn's Approve lands on the command it showed",
          ("rm -rf ~/next-turn", "once") in fake.resolved
          and ("rm -rf ~/from-a-subagent", "once") not in fake.resolved,
          fake.resolved)
    turn2.end()


def test_clean_end_sends_nothing():
    fake = FakeServe()
    turn = Turn(fake)
    turn.end("hi")
    check("a turn with no approvals sends no approval.respond at its end",
          not [1 for m, _ in fake.calls if m == "approval.respond"], fake.calls)
    check("...and adds no notice", turn.job["reply"] == "hi", turn.job["reply"])


def main():
    for t in (test_inverted_order, test_leftover_denied_at_end,
              test_clean_end_sends_nothing):
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
