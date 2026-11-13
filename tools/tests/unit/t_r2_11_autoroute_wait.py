#!/usr/bin/env python3
"""R2-11 (round 2 of the 2026-10-21 audit): Claude's auto-routed answer waits
for the local turn up to that turn's REAL ceiling, so the history keeps
user -> local reply -> Claude.

_ar_think_thread waited at most 2000 x 0.3 s (10 min) for job["done"]. A
local turn may run 600 s of agent time plus up to 290 s per approval card, so
past that cap the deep answer was persisted BEFORE the local reply. Now it
waits for `done`, for the job to leave CHAT_JOBS, or for the job's own
`turn_deadline` (published by hermes_rpc.run_turn, extended while a card
waits) plus a grace; and if the turn never finishes, the answer goes right
after the user message it answers (by that message's id), not at the end.

Execs the REAL dashboard/aux_autoroute.py into a namespace with the server
globals it uses stubbed, and runs its REAL _ar_think_thread on a virtual
clock (time.sleep advances it), so a 15-minute turn takes milliseconds. On
the old code the long turn's order comes out user, Claude, local.
"""
import os
import sys
import threading
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SRC = os.path.join(REPO, "dashboard", "aux_autoroute.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


class Clock(object):
    """A virtual time module: sleep() advances now and fires due hooks."""

    def __init__(self):
        self.now = 1_000_000.0
        self.hooks = []               # (at, fn)

    def time(self):
        return self.now

    def sleep(self, d):
        self.now += max(0.0, float(d))
        for at, fn in list(self.hooks):
            if self.now >= at:
                self.hooks.remove((at, fn))
                fn()

    def monotonic(self):
        return self.now


def load(chats, jobs):
    lock = threading.Lock()

    def save_chat_update(session, fn):
        with lock:
            chat = chats.setdefault(session, {"messages": []})
            out = fn(chat)
            if isinstance(out, dict):
                chats[session] = out
            return chats[session]

    ns = {"__name__": "aux_autoroute_under_test",
          "_chat_worker": lambda job, session, prompt: None,
          "get_settings": lambda: {}, "settings_update": lambda fn: None,
          "load_chat": lambda s: chats.get(s, {"messages": []}),
          "save_chat_update": save_chat_update,
          "CHAT_JOBS": jobs,
          "register_get": lambda *a: None, "register_post": lambda *a: None}
    exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)
    ns["claude_think"] = lambda task, user_context="", depth="quick": {
        "ok": True, "text": "Claude's deeper answer.", "model": "sonnet", "ms": 7000}
    return ns


def order(chat):
    return [(m.get("role"), m.get("text")) for m in chat["messages"]]


def run_thread(ns, job, session, after_id):
    args = (job, session, "Should I take the offer? Compare the trade-offs.",
            "quick", "score 4.0", 4.0)
    try:
        ns["_ar_think_thread"](*(args + (after_id,)))
    except TypeError:              # the old signature had no after_id
        ns["_ar_think_thread"](*args)


def test_long_turn_keeps_order():
    clock = Clock()
    chats = {"chat-a": {"messages": [
        {"id": "m1", "role": "user", "text": "Should I take the offer?"}]}}
    job = {"id": "job-a", "ts": clock.now, "done": False}
    jobs = {"job-a": job}
    ns = load(chats, jobs)
    ns["time"] = clock
    # serve took the prompt; two approval cards extended the deadline to
    # 600 + 2 x 290 s, and the local reply lands at 900 s
    job["turn_deadline"] = clock.now + 600 + 580

    def local_reply():
        ns["save_chat_update"]("chat-a", lambda c: c["messages"].append(
            {"id": "m2", "role": "bot", "text": "Local reply."}))
        job["done"] = True
    clock.hooks.append((clock.now + 900, local_reply))
    run_thread(ns, job, "chat-a", "m1")
    o = order(chats["chat-a"])
    check("THE FIX: a 15-minute local turn still gets its reply in first",
          o == [("user", "Should I take the offer?"), ("bot", "Local reply."),
                ("bot", "Claude's deeper answer.")], o)
    check("...and the deep answer is published on the job",
          (job.get("deep") or {}).get("state") == "done", job.get("deep"))


def test_never_finishes_goes_after_its_question():
    clock = Clock()
    chats = {"chat-b": {"messages": [
        {"id": "u1", "role": "user", "text": "Should I take the offer?"},
        {"id": "x9", "role": "bot", "text": "(a row that landed later)"}]}}
    job = {"id": "job-b", "ts": clock.now, "done": False,
           "turn_deadline": clock.now + 600}
    jobs = {"job-b": job}
    ns = load(chats, jobs)
    ns["time"] = clock
    run_thread(ns, job, "chat-b", "u1")
    waited = clock.now - 1_000_000.0
    check("a turn that never finishes is waited for up to its deadline + grace",
          600 <= waited <= 600 + ns.get("AR_WAIT_GRACE", 90) + 1, waited)
    o = order(chats["chat-b"])
    check("THE FIX: ...then the answer is placed right after the user "
          "message it answers (by id)",
          o == [("user", "Should I take the offer?"),
                ("bot", "Claude's deeper answer."),
                ("bot", "(a row that landed later)")], o)


def test_job_gone_stops_waiting():
    clock = Clock()
    chats = {"chat-c": {"messages": [{"id": "q", "role": "user", "text": "?"}]}}
    job = {"id": "job-c", "ts": clock.now, "done": False,
           "turn_deadline": clock.now + 600}
    jobs = {}                          # not (or no longer) in CHAT_JOBS
    ns = load(chats, jobs)
    ns["time"] = clock
    run_thread(ns, job, "chat-c", "q")
    check("a job that has left CHAT_JOBS is not waited for",
          clock.now - 1_000_000.0 < 2, clock.now - 1_000_000.0)
    check("...and its answer still lands after its question",
          order(chats["chat-c"])[-1] == ("bot", "Claude's deeper answer."),
          order(chats["chat-c"]))


def test_presubmit_ceiling():
    clock = Clock()
    chats = {"chat-d": {"messages": [{"id": "q", "role": "user", "text": "?"}]}}
    job = {"id": "job-d", "ts": clock.now, "done": False}   # no turn_deadline yet
    jobs = {"job-d": job}
    ns = load(chats, jobs)
    ns["time"] = clock
    # the model wakes slowly; serve takes the prompt at 200 s, finishes at 700 s

    def submitted():
        job["turn_deadline"] = clock.now + 600

    def finished():
        ns["save_chat_update"]("chat-d", lambda c: c["messages"].append(
            {"id": "r", "role": "bot", "text": "Local reply."}))
        job["done"] = True
    clock.hooks += [(clock.now + 200, submitted), (clock.now + 700, finished)]
    run_thread(ns, job, "chat-d", "q")
    check("a deadline published after a slow wake is picked up",
          [t for _, t in order(chats["chat-d"])] == ["?", "Local reply.",
                                                      "Claude's deeper answer."],
          order(chats["chat-d"]))


def test_quick_turn_unchanged():
    clock = Clock()
    chats = {"chat-e": {"messages": [{"id": "q", "role": "user", "text": "?"}]}}
    job = {"id": "job-e", "ts": clock.now, "done": True,
           "turn_deadline": clock.now + 600}
    ns = load(chats, {"job-e": job})
    ns["time"] = clock
    chats["chat-e"]["messages"].append({"id": "r", "role": "bot", "text": "Local."})
    run_thread(ns, job, "chat-e", "q")
    check("a turn already done: appended at once, no wait",
          [t for _, t in order(chats["chat-e"])] == ["?", "Local.",
                                                      "Claude's deeper answer."]
          and clock.now == 1_000_000.0, (order(chats["chat-e"]), clock.now))


def main():
    for t in (test_long_turn_keeps_order, test_never_finishes_goes_after_its_question,
              test_job_gone_stops_waiting, test_presubmit_ceiling,
              test_quick_turn_unchanged):
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
