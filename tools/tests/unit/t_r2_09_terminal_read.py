#!/usr/bin/env python3
"""R2-9 (round 2 of the 2026-10-21 audit): read_terminal no longer stalls.

serve wires the read_terminal tool through the same blocking bridge as
clarify (tui_gateway `read_terminal_callback` -> `_block("terminal.read.
request", ..., timeout=30)`): it emits the event and holds the agent thread
until `terminal.read.respond` arrives. That event was not in
hermes_rpc.BLOCKING_PROMPTS, so every read_terminal call in a dashboard turn
sat for 30 s and then failed anyway.

Drives the REAL hermes_rpc.run_turn against a fake serve that, like serve,
holds every later event back until the request is answered. On the old code
the turn never gets past the request and ends at the (shortened) turn
timeout.
"""
import collections
import os
import sys
import tempfile
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
os.environ["HOME"] = tempfile.mkdtemp(prefix="hermes-r2-9-")
sys.path.insert(0, os.path.join(REPO, "dashboard"))

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


class FakeServe(object):
    def __init__(self, events):
        self.events = collections.deque(events)
        self.calls = []
        self.blocked_on = None

    def call(self, method, params, timeout=30, on_sent=None):
        self.calls.append((method, dict(params)))
        if on_sent:
            on_sent()
        if method == "session.create":
            return {"session_id": "s1", "stored_session_id": "k1"}
        if method == "prompt.submit":
            return {"status": "streaming"}
        if method == "terminal.read.respond" \
                and params.get("request_id") == self.blocked_on:
            self.blocked_on = None
            return {"status": "ok"}
        return {}

    def next_event(self, timeout):
        if self.blocked_on is None and self.events:
            ev = self.events.popleft()
            if ev["type"] == "terminal.read.request":
                self.blocked_on = ev["payload"].get("request_id")
            return ev
        time.sleep(min(timeout, 0.01))
        return None

    def close(self):
        pass


def ev(t, **payload):
    return {"type": t, "session_id": "s1", "payload": payload}


def drive(events, timeout=3):
    fake = FakeServe(events)
    rpc.ServeSession = lambda: fake
    saved = rpc.TURN_TIMEOUT
    rpc.TURN_TIMEOUT = timeout
    statuses = []
    job = StatusJob(statuses, {"id": "j", "state": "running", "text": "",
                               "status": "", "approval": None, "reply": "",
                               "ok": False, "done": False})
    t0 = time.time()
    try:
        rpc.run_turn(job, {"title": "t", "messages": []}, "what's in my terminal?",
                     lambda: None)
    finally:
        rpc.TURN_TIMEOUT = saved
    return job, fake, time.time() - t0, statuses


class StatusJob(dict):
    """Records every status line the turn published."""

    def __init__(self, sink, *a):
        super().__init__(*a)
        self._sink = sink

    def __setitem__(self, k, v):
        if k == "status":
            self._sink.append(v)
        super().__setitem__(k, v)


def test_answered_at_once():
    job, fake, secs, statuses = drive([
        ev("tool.start", name="read_terminal"),
        ev("terminal.read.request", request_id="t1", count=40),
        ev("tool.complete", name="read_terminal"),
        ev("message.complete", text="There is no terminal pane open here.",
           status="complete"),
    ])
    rs = [(m, p) for m, p in fake.calls if m.endswith(".respond")]
    check("the request is answered with terminal.read.respond",
          [m for m, _ in rs] == ["terminal.read.respond"], fake.calls)
    check("...for ITS request_id",
          rs and rs[0][1].get("request_id") == "t1", rs)
    check("...with an empty buffer (the tool reports 'no terminal open')",
          rs and rs[0][1].get("text") == "", rs)
    check("THE FIX: the turn finishes instead of waiting out the request",
          job["ok"] is True and job["state"] == "done" and secs < 2.5,
          (job.get("reply"), secs))
    check("the status line says there is no terminal here",
          any("no terminal" in s for s in statuses), statuses)
    check("the reply carries no extra notice (nothing for the owner to do)",
          job["reply"] == "There is no terminal pane open here.", job["reply"])


def test_every_call_answered():
    job, fake, secs, _ = drive([
        ev("terminal.read.request", request_id="a"),
        ev("terminal.read.request", request_id="b", start=0, count=10),
        ev("message.complete", text="done", status="complete"),
    ])
    ids = [p.get("request_id") for m, p in fake.calls
           if m == "terminal.read.respond"]
    check("two reads in one turn are both answered, in order",
          ids == ["a", "b"], ids)
    check("...and the turn still finishes promptly",
          job["ok"] is True and secs < 2.5, (job.get("reply"), secs))


def test_registered_as_blocking_prompt():
    check("terminal.read.request is a blocking prompt answered by "
          "terminal.read.respond (field 'text', as tui_gateway reads it)",
          rpc.BLOCKING_PROMPTS.get("terminal.read.request")
          == ("terminal.read.respond", "text"),
          rpc.BLOCKING_PROMPTS)


def main():
    for t in (test_registered_as_blocking_prompt, test_answered_at_once,
              test_every_call_answered):
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
