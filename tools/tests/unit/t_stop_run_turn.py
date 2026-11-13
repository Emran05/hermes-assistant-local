#!/usr/bin/env python3
"""The owner's Stop, in hermes_rpc.run_turn (2026-11-12).

On 2026-11-12 a turn generated 9,000+ tokens toward a 65,536-token limit and
the only way to end it was to shut the model server down. hermes_rpc now has
`request_stop(job)`; the stop and the decision to submit the prompt share one
lock, so:

  * stopped BEFORE submission: no prompt.submit ever goes out, a session this
    turn created is closed and never saved over the conversation's;
  * stopped AFTER: serve's `session.interrupt` is sent once, every approval
    still waiting is denied through the FIFO mirror (never approved, a click
    taken just before is dropped), a request that arrives afterwards is denied
    without a card, the streamed text is KEPT and one line says the owner
    stopped it (plus "check what it already did" when tools ran); ok is False
    and job["stopped"] is True;
  * serve not acknowledging the interrupt, or the turn not ending within
    STOP_GRACE, ends the job anyway and says the session may still finish in
    the background;
  * a second stop sends nothing.

Drives the REAL run_turn on its own thread against a fake serve whose events
the test feeds. No dashboard, no serve, no model.
"""
import os
import queue
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
os.environ["HOME"] = tempfile.mkdtemp(prefix="hermes-stop-rt-")
sys.path.insert(0, os.path.join(REPO, "dashboard"))

import hermes_rpc as rpc  # noqa: E402

rpc._perm = None          # every approval.request is a card (tier ask)
rpc.STOP_GRACE = 1.5

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:900]))


class FakeServe(object):
    def __init__(self, interrupt="ack", on_interrupt=None, on_create=None):
        self.q = queue.Queue()
        self.calls = []          # (method, params)
        self.timed = []          # (time, method, params)
        self.interrupt = interrupt
        self.on_interrupt = on_interrupt
        self.on_create = on_create
        self.hooks = {}          # method -> fn(fake, params, on_sent): a value, None, or raise
        self.on_next = None      # fn(fake) before each next_event (may raise)
        self.deny_rpc_error = False

    def call(self, method, params, timeout=30, on_sent=None):
        self.calls.append((method, dict(params or {})))
        self.timed.append((time.time(), method, dict(params or {})))
        h = self.hooks.get(method)
        if h:
            r = h(self, params or {}, on_sent)
            if r is not None:
                return r
        if on_sent:
            on_sent()
        if method == "session.create":
            if self.on_create:
                self.on_create()
            return {"session_id": "s1", "stored_session_id": "k1"}
        if method == "prompt.submit":
            return {"status": "streaming"}
        if method == "session.interrupt":
            if self.interrupt == "fail":
                raise rpc.WSError("timeout waiting for session.interrupt")
            if self.on_interrupt:
                self.on_interrupt(self)
            return {"status": "interrupted"}
        if method == "approval.respond":
            if (self.deny_rpc_error and params.get("choice") == "deny"
                    and not params.get("all")):
                raise rpc.RPCError("session busy")
            return {"resolved": 1}
        if method == "session.status":
            return {"output": "Agent Running: Yes"}
        return {}

    def next_event(self, timeout):
        if self.on_next:
            self.on_next(self)
        try:
            return self.q.get(timeout=min(timeout, 0.05))
        except queue.Empty:
            return None

    def push(self, t, **payload):
        self.q.put({"type": t, "session_id": "s1", "payload": payload})

    def close(self):
        pass

    def methods(self):
        return [m for m, _ in self.calls]


def new_job():
    return {"id": "j", "state": "running", "text": "", "status": "",
            "approval": None, "reply": "", "ok": False, "done": False}


def start(fake, job, saves=None):
    rpc.ServeSession = lambda: fake
    saves = saves if saves is not None else []
    meta = {"title": "t", "messages": []}
    th = threading.Thread(target=rpc.run_turn,
                          args=(job, meta, "go", lambda: saves.append(dict(meta))))
    th.daemon = True
    th.start()
    return th, meta


def wait(cond, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def interrupted_end(text=""):
    """serve's answer to an interrupt: the turn's interrupted complete."""
    def f(fake):
        fake.push("message.complete", text=text or "Operation interrupted.",
                  status="interrupted")
    return f


def test_mid_stream():
    print("\nstop mid-stream")
    fake = FakeServe(on_interrupt=interrupted_end())
    job = new_job()
    th, _ = start(fake, job)
    check("the prompt is submitted", wait(lambda: job.get("submitted")))
    fake.push("message.delta", text="Here is the first ")
    fake.push("message.delta", text="half of a long answer")
    check("text streams", wait(lambda: "first half" in job.get("text", "")))
    check("request_stop after submission answers 'post'",
          rpc.request_stop(job) == "post")
    th.join(5)
    check("the turn ended", not th.is_alive() and job.get("state") == "done", job)
    check("THE FEATURE: session.interrupt sent exactly once",
          fake.methods().count("session.interrupt") == 1, fake.methods())
    check("THE FEATURE: the streamed text is kept as the reply",
          job["reply"].startswith("Here is the first half of a long answer"), job["reply"])
    check("THE FEATURE: one plain line says the owner stopped it",
          job["reply"].rstrip().endswith("Stopped by you.")
          and job["reply"].count("Stopped by you.") == 1, job["reply"])
    check("serve's own 'Operation interrupted' text is not the reply",
          "Operation interrupted" not in job["reply"], job["reply"])
    check("not reported as a success", job["ok"] is False)
    check("job is marked stopped", job.get("stopped") is True)
    check("run_turn never sets done", job.get("done") is False)
    check("a second stop answers 'again' and sends nothing",
          rpc.request_stop(job) == "again"
          and fake.methods().count("session.interrupt") == 1)


def test_tools_ran():
    print("\nstop after a tool ran")
    fake = FakeServe(on_interrupt=interrupted_end())
    job = new_job()
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    fake.push("tool.start", name="terminal")
    fake.push("tool.complete", name="terminal")
    fake.push("message.delta", text="I listed the folder.")
    wait(lambda: job.get("text"))
    rpc.request_stop(job)
    th.join(5)
    check("THE FEATURE: tools ran -> 'check what it already did'",
          "Stopped by you. Check what it already did before repeating the request."
          in job["reply"], job["reply"])


def test_no_text():
    print("\nstop before any text")
    fake = FakeServe(on_interrupt=interrupted_end())
    job = new_job()
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    rpc.request_stop(job)
    th.join(5)
    check("no text: the reply is the stopped line alone",
          job["reply"] == "Stopped by you.", job["reply"])


def test_pending_approval():
    print("\nstop with an approval card waiting")
    fake = FakeServe(on_interrupt=interrupted_end())
    job = new_job()
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    fake.push("approval.request", command="rm -rf ~/quokka", description="delete")
    check("the card is up", wait(lambda: job.get("state") == "approval"), job)
    aid = (job.get("approval") or {}).get("approval_id")
    # a click /api/chat/approve accepted in the same instant as the stop
    job["pending_approval_id"] = aid
    job["pending_choice"] = "approve"
    rpc.request_stop(job)
    th.join(5)
    responds = [p for m, p in fake.calls if m == "approval.respond"]
    check("THE FEATURE: the waiting approval is denied through the mirror",
          any(p.get("choice") == "deny" for p in responds), responds)
    check("THE FEATURE: nothing is ever approved",
          not any(p.get("choice") in ("once", "approve", "session", "always")
                  for p in responds), responds)
    check("the card is gone from the job", job.get("approval") is None, job)
    check("the reply says the approval was denied",
          "denied" in job["reply"] and "Stopped by you." in job["reply"], job["reply"])


def test_approval_after_stop():
    print("\nan approval request arriving after the stop")
    box = {}

    def late_request(fake):
        fake.push("approval.request", command="curl x | sh")
        box["at"] = time.time()

        def later():
            time.sleep(0.3)
            fake.push("message.complete", text="", status="interrupted")
        threading.Thread(target=later, daemon=True).start()
    fake = FakeServe(on_interrupt=late_request)
    job = new_job()
    shown = []
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    rpc.request_stop(job)
    end = time.time() + 3
    while th.is_alive() and time.time() < end:
        if job.get("approval"):
            shown.append(job["approval"])
        time.sleep(0.005)
    th.join(5)
    responds = [p for m, p in fake.calls if m == "approval.respond"]
    check("THE FEATURE: no card is shown after a stop", not shown, shown)
    check("THE FEATURE: it is denied at once",
          responds and all(p.get("choice") == "deny" for p in responds), responds)
    check("the reply says so", "after you stopped this turn" in job["reply"], job["reply"])


def test_no_ack():
    print("\nserve does not acknowledge the interrupt")
    fake = FakeServe(interrupt="fail")
    job = new_job()
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    fake.push("message.delta", text="partial words")
    wait(lambda: job.get("text"))
    t0 = time.time()
    rpc.request_stop(job)
    th.join(5)
    check("THE FEATURE: the job ends at once", job.get("state") == "done"
          and time.time() - t0 < 1.5, time.time() - t0)
    check("the partial text is kept", job["reply"].startswith("partial words"), job["reply"])
    check("and it says the session may still finish in the background",
          "could not be told to stop" in job["reply"]
          and "may still finish in the background" in job["reply"], job["reply"])
    check("stopped, not ok", job.get("stopped") is True and job["ok"] is False)


def test_never_ends():
    print("\nserve acknowledges but the turn never ends")
    fake = FakeServe()            # ack, then silence
    job = new_job()
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    t0 = time.time()
    rpc.request_stop(job)
    th.join(6)
    took = time.time() - t0
    check("THE FEATURE: ended within STOP_GRACE", job.get("state") == "done"
          and rpc.STOP_GRACE - 0.2 <= took < rpc.STOP_GRACE + 2, took)
    check("it says the session may still finish in the background",
          "did not stop in time" in job["reply"]
          and "may still finish in the background" in job["reply"], job["reply"])


def test_before_submit():
    print("\nstop before submission")
    fake = FakeServe()
    job = new_job()
    check("request_stop before anything answers 'pre'",
          rpc.request_stop(job, pre_submit=lambda j: j.update(reply="pre")) == "pre")
    check("pre_submit ran", job["reply"] == "pre")
    saves = []
    th, _ = start(fake, job, saves)
    th.join(5)
    check("THE FEATURE: nothing reaches serve", fake.calls == [], fake.calls)
    check("nothing saved, the job untouched by run_turn",
          saves == [] and job["reply"] == "pre", (saves, job))

    print("\nstop while the session is being created")
    fake = FakeServe()
    job = new_job()
    fake.on_create = lambda: rpc.request_stop(job)
    saves = []
    th, meta = start(fake, job, saves)
    th.join(5)
    check("THE FEATURE: no prompt.submit after the stop",
          "prompt.submit" not in fake.methods(), fake.methods())
    check("the new session is closed", "session.close" in fake.methods(), fake.methods())
    check("and never saved over the conversation's",
          saves == [] and not meta.get("serve_sid"), (saves, meta))


def test_finished_normally():
    print("\na stop that arrives as the turn completes")
    fake = FakeServe(on_interrupt=lambda f: f.push("message.complete",
                                                   text="All done.", status="complete"))
    job = new_job()
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    rpc.request_stop(job)
    th.join(5)
    check("a turn that completed is reported as completed",
          job["ok"] is True and job["reply"] == "All done." and not job.get("stopped"), job)


# ---- review round (2026-11-12): forced interleavings ------------------------

def card(fake, job):
    fake.push("approval.request", command="rm -rf ~/quokka")
    wait(lambda: job.get("state") == "approval")
    return (job.get("approval") or {}).get("approval_id")


def accept_click(job, aid):
    """What server._chat_approve stores for an accepted click."""
    job["_answered_approval"] = aid
    job["pending_approval_id"] = aid
    job["pending_choice"] = "approve"


def onces(fake, after=None):
    return [p for t, m, p in fake.timed if m == "approval.respond"
            and p.get("choice") != "deny" and (after is None or t >= after)]


def test_toctou_pop():
    print("\n[review 1a] stop lands between run_turn's stop check and its pop of the click")
    STOP = {}

    class Job(dict):
        armed = False

        def pop(self, k, *a):
            if k == "pending_choice" and self.armed and dict.get(self, "pending_choice"):
                self.armed = False
                STOP["kind"] = rpc.request_stop(self)
                STOP["t"] = time.time()
            return dict.pop(self, k, *a)
    fake = FakeServe(on_interrupt=interrupted_end())
    job = Job(new_job())
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    aid = card(fake, job)
    job.armed = True
    accept_click(job, aid)
    wait(lambda: "t" in STOP)
    th.join(6)
    check("the stop was accepted as 'post'", STOP.get("kind") == "post", STOP)
    check("THE FIX: no approving answer leaves after the stop", onces(fake) == [],
          onces(fake))
    check("the approval was denied", any(p.get("choice") == "deny" for m, p in fake.calls
                                         if m == "approval.respond"), fake.calls)


def test_stop_before_claim():
    print("\n[review 1b] stop lands just before an Approve is claimed for sending")
    fake = FakeServe(on_interrupt=interrupted_end())
    job = new_job()
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    aid = card(fake, job)
    real = rpc._claim_approve
    seen = {}

    def racing(j):
        seen["kind"] = rpc.request_stop(j)     # the owner's Stop wins the race
        return real(j)
    rpc._claim_approve = racing
    try:
        accept_click(job, aid)
        th.join(6)
    finally:
        rpc._claim_approve = real
    check("the race really ran", seen.get("kind") == "post", seen)
    check("THE FIX: nothing but deny leaves", onces(fake) == [], onces(fake))
    check("the reply says that approval was not sent",
          "was not sent because you stopped" in job["reply"], job["reply"])


def test_stop_during_send():
    print("\n[review 1c] stop lands while an Approve is already on the wire")
    fake = FakeServe(on_interrupt=interrupted_end())
    job = new_job()
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    aid = card(fake, job)
    seen = {}

    def during(f, params, on_sent):
        if params.get("choice") == "once" and "kind" not in seen:
            seen["kind"] = rpc.request_stop(job)
        return None
    fake.hooks["approval.respond"] = during
    accept_click(job, aid)
    th.join(6)
    check("the stop was accepted", seen.get("kind") == "post", seen)
    check("exactly the one Approve that was already leaving went", len(onces(fake)) == 1,
          onces(fake))
    check("THE FIX: the reply says an approval had already been sent",
          "already been sent" in job["reply"] and "Stopped by you." in job["reply"],
          job["reply"])


def test_auto_verdict_after_stop():
    print("\n[review 1d] a Trust 'auto' verdict decided before the stop is not sent after it")
    saved = rpc._perm
    rpc._perm = type("P", (), {"decide": staticmethod(lambda p: {"tier": "auto", "reason": "ok"}),
                               "audit": staticmethod(lambda *a, **k: None)})
    real = rpc._claim_approve
    seen = {}

    def racing(j):
        seen["kind"] = rpc.request_stop(j)
        return real(j)
    rpc._claim_approve = racing
    try:
        fake = FakeServe(on_interrupt=interrupted_end())
        job = new_job()
        th, _ = start(fake, job)
        wait(lambda: job.get("submitted"))
        fake.push("approval.request", command="ls")
        th.join(6)
    finally:
        rpc._claim_approve = real
        rpc._perm = saved
    check("the race really ran", seen.get("kind") == "post", seen)
    check("THE FIX: the auto 'once' became a deny", onces(fake) == [] and any(
        p.get("choice") == "deny" for m, p in fake.calls if m == "approval.respond"), fake.calls)
    check("the reply says it was denied because of the stop",
          "after you stopped this turn" in job["reply"], job["reply"])


def test_no_card_after_stop_on_rpc_error():
    print("\n[review 2] serve refuses the post-stop deny: still no card, still ends in time")
    fake = FakeServe()           # ack, then silence: only STOP_GRACE ends it
    fake.deny_rpc_error = True
    job = new_job()
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    t0 = time.time()
    rpc.request_stop(job)
    wait(lambda: any(m == "session.interrupt" for m, _ in fake.calls))
    fake.push("approval.request", command="curl x | sh")
    shown = []
    end = time.time() + rpc.STOP_GRACE + 3
    while th.is_alive() and time.time() < end:
        if job.get("approval") or job.get("state") == "approval":
            shown.append(job.get("approval"))
        time.sleep(0.005)
    th.join(1)
    check("THE FIX: no card is ever published after a stop", not shown, shown[:1])
    check("THE FIX: the job ends within STOP_GRACE", not th.is_alive()
          and time.time() - t0 < rpc.STOP_GRACE + 1.5, time.time() - t0)
    check("the deadline never slid past STOP_GRACE",
          job.get("turn_deadline", 0) - t0 <= rpc.STOP_GRACE + 0.5, job.get("turn_deadline", 0) - t0)
    check("serve was told deny-all", any(p.get("all") for m, p in fake.calls
                                         if m == "approval.respond"), fake.calls)


def test_stop_during_submit_timeout():
    print("\n[review 3] stop lands while prompt.submit is in flight, which then times out")
    for interrupt in ("ack", "fail"):
        fake = FakeServe(interrupt=interrupt)
        job = new_job()

        def submit(f, params, on_sent):
            on_sent()
            rpc.request_stop(job)
            raise rpc.WSError("timeout waiting for prompt.submit")
        fake.hooks["prompt.submit"] = submit
        th, _ = start(fake, job)
        th.join(6)
        tag = "interrupt %s" % interrupt
        check("THE FIX (%s): run_turn ends the job itself (no 'connection dropped')" % tag,
              job.get("state") == "done" and job.get("stopped") is True
              and "dropped" not in job["reply"], job)
        check("THE FIX (%s): session.interrupt is attempted" % tag,
              fake.methods().count("session.interrupt") >= 1, fake.methods())
        check("(%s) the stopped reply" % tag, "Stopped by you." in job["reply"], job["reply"])
        if interrupt == "fail":
            check("not delivered: it says the session may still finish in the background",
                  "could not be told to stop" in job["reply"]
                  and "may still finish in the background" in job["reply"], job["reply"])


def test_stop_submit_refused():
    print("\n[review 3b] stop, then serve refuses the prompt")
    fake = FakeServe()
    job = new_job()

    def submit(f, params, on_sent):
        on_sent()
        rpc.request_stop(job)
        raise rpc.RPCError("subagent still running")
    fake.hooks["prompt.submit"] = submit
    th, _ = start(fake, job)
    th.join(6)
    check("the stopped reply, with what serve said",
          job.get("stopped") is True and "Stopped by you." in job["reply"]
          and "did not take this message" in job["reply"], job["reply"])


def test_socket_dies_after_stop():
    print("\n[review 4] the socket dies after a post-submit stop")
    fake = FakeServe()
    job = new_job()

    def boom(f):
        if job.get("stop_requested") and "session.interrupt" in f.methods():
            raise rpc.WSError("socket closed")
    th, _ = start(fake, job)
    wait(lambda: job.get("submitted"))
    fake.push("message.delta", text="Half of an answer the owner was reading")
    wait(lambda: job.get("text"))
    fake.on_next = boom
    rpc.request_stop(job)
    th.join(6)
    check("THE FIX: the streamed text is kept",
          job["reply"].startswith("Half of an answer the owner was reading"), job["reply"])
    check("THE FIX: it ends with the stopped note and is marked stopped",
          "Stopped by you." in job["reply"] and job.get("stopped") is True, job)
    check("it says the session may still finish in the background",
          "may still finish in the background" in job["reply"], job["reply"])
    check("interrupt sent once", fake.methods().count("session.interrupt") == 1, fake.methods())


def test_no_card_when_stop_lands_before_publish():
    print("\na stop that lands between add()'s check and the card being published")
    # add() checks stop_requested, then audits "asked", then settle() publishes
    # the head. A stop accepted in between used to put a card on screen after
    # the Stop, until serve acknowledged the interrupt (2026-11-12 re-review).
    saved = rpc._perm
    job = new_job()
    box = {}

    def audit(j, payload, verdict, action, choice=""):
        if action == "asked" and "kind" not in box:
            box["kind"] = rpc.request_stop(job)
            job["approval"] = None        # what server.py's _chat_stop does next
            job["state"] = "running"

    rpc._perm = type("P", (), {"decide": staticmethod(lambda p: {"tier": "ask", "reason": ""}),
                               "audit": staticmethod(audit)})
    try:
        def slow_ack(f):
            time.sleep(1.0)               # a serve that is slow to acknowledge
            interrupted_end()(f)

        fake = FakeServe(on_interrupt=slow_ack)
        th, _ = start(fake, job)
        wait(lambda: job.get("submitted"))
        fake.push("approval.request", command="rm -rf ~/quokka", description="delete")
        check("control: the stop landed inside add()", wait(lambda: "kind" in box), box)
        seen = False
        end = time.time() + 6
        while th.is_alive() and time.time() < end:
            if job.get("state") == "approval" or job.get("approval"):
                seen = True
            time.sleep(0.003)
        th.join(5)
        check("THE FIX: no card is published after the stop", not seen, job)
        responds = [p for m, p in fake.calls if m == "approval.respond"]
        check("...and only deny left for it",
              responds and all(p.get("choice") == "deny" for p in responds), responds)
        check("...the turn ended stopped", job.get("stopped") and "Stopped by you." in job["reply"],
              job["reply"])
    finally:
        rpc._perm = saved


def test_claim_submit_lock():
    print("\n[review 6] _claim_submit and request_stop decide under one lock")
    out = {}

    class Job(dict):
        armed = True

        def get(self, k, *a):
            if k == "stop_requested" and self.armed:
                self.armed = False
                t = threading.Thread(target=lambda: out.update(kind=rpc.request_stop(
                    self, pre_submit=lambda j: out.update(pre=True))))
                t.start()
                t.join(0.3)        # with the lock it is still waiting here
                out["t"] = t
            return dict.get(self, k, *a)
    job = Job(new_job())
    claimed = rpc._claim_submit(job)
    out["t"].join(2)
    check("THE FIX: a claimed submit and a 'pre' stop never both happen",
          not (claimed and out.get("kind") == "pre"), (claimed, out))
    check("...the stop that lost the race is 'post'", claimed and out.get("kind") == "post",
          (claimed, out))


def main():
    for t in (test_mid_stream, test_tools_ran, test_no_text, test_pending_approval,
              test_approval_after_stop, test_no_ack, test_never_ends,
              test_before_submit, test_finished_normally, test_toctou_pop,
              test_stop_before_claim, test_stop_during_send, test_auto_verdict_after_stop,
              test_no_card_after_stop_on_rpc_error, test_stop_during_submit_timeout,
              test_stop_submit_refused, test_socket_dies_after_stop,
              test_no_card_when_stop_lands_before_publish,
              test_claim_submit_lock):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILS.append("%s raised %r" % (t.__name__, e))
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
