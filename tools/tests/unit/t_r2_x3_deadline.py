#!/usr/bin/env python3
"""X-approval-flow-3 (round 2 of the 2026-10-21 audit): time an approval card
spends waiting for the owner does not count against the turn deadline, and
the timeout reply only says "may still finish in the background" when that
is true.

run_turn fixed `deadline = time.time() + TURN_TIMEOUT` when its event loop
started. With a card on every computer_use click, a desktop task of a couple
of dozen steps reached 600 s on the owner's reading time alone; run_turn then
sent session.interrupt, which denies the card in flight and stops the agent
between steps — a form left half typed — and the reply claimed the session
"may still finish in the background", which the interrupt makes false.

Drives the REAL hermes_rpc.run_turn with a shortened TURN_TIMEOUT. On the old
code the long-approval turn is cut off and the wording is always the old one.
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
HOME = tempfile.mkdtemp(prefix="hermes-r2-x3-")
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
    def __init__(self, interrupt_fails=False):
        self.pending, self.resolved, self.calls = [], [], []
        self.events = collections.deque()
        self.interrupt_fails = interrupt_fails
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
        if method == "session.interrupt":
            if self.interrupt_fails:
                raise rpc.WSError("timeout waiting for session.interrupt")
            return {}
        if method == "approval.respond":
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
        time.sleep(min(timeout, 0.02))
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

    def finish(self, text="done"):
        with self.lock:
            self.events.append({"type": "message.complete", "session_id": "sid-1",
                                "payload": {"text": text, "status": "complete"}})

    def methods(self):
        with self.lock:
            return [m for m, _ in self.calls]


class Turn(object):
    def __init__(self, fake):
        self.fake = fake
        self.job = {"id": "job-1", "session": "chat-x", "state": "running",
                    "text": "", "status": "", "approval": None, "reply": "",
                    "ok": False, "done": False}
        rpc.ServeSession = lambda: fake
        self.t0 = time.time()
        self.t = threading.Thread(target=lambda: rpc.run_turn(
            self.job, {"title": "t", "messages": []}, "fill in the form",
            lambda: None), daemon=True)
        self.t.start()

    def card(self):
        ap = self.job.get("approval")
        return ap if self.job.get("state") == "approval" and isinstance(ap, dict) else None

    def click(self, choice):
        c = self.card()
        self.job["pending_approval_id"] = c["approval_id"]
        self.job["pending_choice"] = choice


def test_card_wait_not_counted():
    saved = rpc.TURN_TIMEOUT
    rpc.TURN_TIMEOUT = 1.5
    try:
        fake = FakeServe()
        turn = Turn(fake)
        # three steps, each with a card the owner reads for ~0.8 s
        for i in range(3):
            fake.ask("computer_use type step %d" % i)
            check("step %d: its card is shown" % i,
                  wait_until(lambda: turn.card() is not None, 2), turn.job)
            time.sleep(0.8)
            if turn.job.get("state") == "done":
                break
            turn.click("approve")
            wait_until(lambda: turn.card() is None, 2)
            time.sleep(0.2)                       # the agent's own step time
        fake.finish("All three fields filled in.")
        turn.t.join(5)
        took = time.time() - turn.t0
        check("THE FIX: a turn whose wall time exceeds TURN_TIMEOUT only "
              "through card waits is not cut off",
              turn.job.get("ok") is True
              and turn.job.get("reply") == "All three fields filled in.",
              (turn.job.get("reply"), round(took, 2)))
        check("...it really did run past the bare deadline", took > 1.5, took)
        check("...every step was approved, none denied by an interrupt",
              fake.resolved == [("computer_use type step %d" % i, "once")
                                for i in range(3)], fake.resolved)
        check("...and serve was never interrupted",
              "session.interrupt" not in fake.methods(), fake.methods())
        check("the live ceiling is published on the job",
              isinstance(turn.job.get("turn_deadline"), float)
              and turn.job["turn_deadline"] > turn.t0 + 1.5 + 1.5,
              turn.job.get("turn_deadline"))
    finally:
        rpc.TURN_TIMEOUT = saved


def test_agent_time_still_bounded():
    saved = rpc.TURN_TIMEOUT
    rpc.TURN_TIMEOUT = 0.6
    try:
        fake = FakeServe()
        turn = Turn(fake)
        turn.t.join(5)
        took = time.time() - turn.t0
        check("with no card, the turn still ends at TURN_TIMEOUT",
              turn.job.get("state") == "done" and turn.job.get("ok") is False
              and took < 3, (turn.job.get("reply"), took))
        check("...serve is told to stop it",
              "session.interrupt" in fake.methods(), fake.methods())
        check("THE FIX: ...and the reply no longer claims it may still finish",
              "background" not in turn.job["reply"]
              and "was stopped" in turn.job["reply"], turn.job["reply"])

        fake = FakeServe(interrupt_fails=True)
        turn = Turn(fake)
        turn.t.join(8)
        check("when the interrupt cannot be delivered, the reply says the "
              "session may still finish",
              "may still finish in the background" in turn.job.get("reply", ""),
              turn.job.get("reply"))
    finally:
        rpc.TURN_TIMEOUT = saved


def main():
    for t in (test_card_wait_not_counted, test_agent_time_still_bounded):
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
