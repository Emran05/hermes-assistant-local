#!/usr/bin/env python3
"""C121 (2026-10-21 audit): run_turn reads serve's end-of-turn contract.

  * message.complete carries `status`: complete | interrupted | error
    (tui_gateway _run_prompt_submit). run_turn reported all three as ok=True,
    so an interrupted half-answer or an "Error: …" was shown as a normal reply.
  * an `error` event is not always the end of the turn: "Could not switch to
    configured model …" is emitted at turn start and the turn then runs. The
    job was ended on the spot, and the real answer arrived to nobody.

Drives the REAL hermes_rpc.run_turn against a fake serve whose session.status
says whether a turn is still running (the "Agent Running: Yes|No" line).
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
os.environ["HOME"] = tempfile.mkdtemp(prefix="hermes-c121-")
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
    def __init__(self, events, running=False, status_fails=False):
        self.events = collections.deque(events)
        self.running = running
        self.status_fails = status_fails
        self.calls = []

    def call(self, method, params, timeout=30, on_sent=None):
        self.calls.append(method)
        if on_sent:
            on_sent()
        if method == "session.create":
            return {"session_id": "s1", "stored_session_id": "k1"}
        if method == "prompt.submit":
            return {"status": "streaming"}
        if method == "session.status":
            if self.status_fails:
                raise rpc.WSError("session not found")
            return {"output": "Hermes TUI Status\n\nAgent Running: %s"
                              % ("Yes" if self.running else "No")}
        return {}

    def next_event(self, timeout):
        if self.events:
            ev = self.events.popleft()
            if callable(ev):
                return ev(self)
            return ev
        time.sleep(min(timeout, 0.01))
        return None

    def close(self):
        pass


def ev(t, **payload):
    return {"type": t, "session_id": "s1", "payload": payload}


def pause(secs):
    """An event-stream gap: returns nothing for `secs`."""
    end = []

    def f(fake):
        if not end:
            end.append(time.time() + secs)
        if time.time() < end[0]:
            fake.events.appendleft(f)
            time.sleep(0.01)
        return None
    return f


def drive(fake, timeout=8):
    rpc.ServeSession = lambda: fake
    saved = rpc.TURN_TIMEOUT
    rpc.TURN_TIMEOUT = timeout
    job = {"id": "j", "state": "running", "text": "", "status": "",
           "approval": None, "reply": "", "ok": False, "done": False}
    t0 = time.time()
    try:
        rpc.run_turn(job, {"title": "t", "messages": []}, "go", lambda: None)
    finally:
        rpc.TURN_TIMEOUT = saved
    return job, time.time() - t0


def test_complete_status():
    job, _ = drive(FakeServe([
        ev("message.delta", text="Here is the first half"),
        ev("message.complete", text="Here is the first half", status="interrupted"),
    ]))
    check("interrupted: NOT reported as a success", job["ok"] is False, job)
    check("interrupted: the partial text is kept and labelled",
          job["reply"].startswith("Here is the first half")
          and "interrupted" in job["reply"], job["reply"])

    job, _ = drive(FakeServe([
        ev("message.complete", text="Error: 404 model not found", status="error"),
    ]))
    check("error status: NOT reported as a success", job["ok"] is False, job)
    check("error status: the error text is the reply",
          "404 model not found" in job["reply"], job["reply"])

    job, _ = drive(FakeServe([ev("message.complete", text="", status="error")]))
    check("error status with no text: says so", job["ok"] is False
          and "error" in job["reply"].lower(), job)

    job, _ = drive(FakeServe([ev("message.complete", text="fine", status="complete")]))
    check("complete: a success", job["ok"] is True and job["reply"] == "fine", job)
    job, _ = drive(FakeServe([ev("message.complete", text="old serve")]))
    check("no status (older serve): still a success", job["ok"] is True, job)


def test_nonterminal_error():
    fake = FakeServe([
        ev("error", message="Could not switch to configured model X: offline"),
        pause(2.2),                       # long enough for the 1.5 s probe
        ev("message.delta", text="the actual answer"),
        ev("message.complete", text="the actual answer", status="complete"),
    ], running=True)
    job, secs = drive(fake)
    check("THE FIX: an error event while the turn still runs does not end the job",
          job["ok"] is True and job["reply"].startswith("the actual answer"), job)
    check("...serve was asked whether the turn was still running",
          "session.status" in fake.calls, fake.calls)
    check("...and the error is still reported to the owner",
          "Could not switch to configured model" in job["reply"], job["reply"])


def test_terminal_error():
    fake = FakeServe([ev("error", message="agent init failed: no provider")],
                     running=False)
    job, secs = drive(fake)
    check("a terminal error (turn no longer running) ends the job",
          job["ok"] is False and "agent init failed" in job["reply"], job)
    check("...promptly, not at the turn timeout", secs < 4, secs)

    fake = FakeServe([ev("error", message="boom")], status_fails=True)
    job, secs = drive(fake)
    check("an error whose turn state cannot be read ends the job too",
          job["ok"] is False and job["reply"] == "boom" and secs < 4, (job, secs))


def main():
    for t in (test_complete_status, test_nonterminal_error, test_terminal_error):
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
