#!/usr/bin/env python3
"""C23 follow-up (round 2 of the 2026-10-21 audit): the "this conversation's
agent session could not be used" notice no longer depends on WHO seeded the
replacement session.

run_turn only added the notice on the branch where it built the seed itself
(`had_session and not params.get("messages")`). A branched conversation gets
its messages from aux_branch's SEED_HOOK, so when its stored agent session
could not be resumed the new session was created silently: the reply read as
continuous although every earlier tool result was gone.

Drives the REAL hermes_rpc.run_turn against a fake serve. On the old code the
branch cases get no notice at all.
"""
import collections
import io
import os
import sys
import tempfile
import time
import traceback
from contextlib import redirect_stderr

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
os.environ["HOME"] = tempfile.mkdtemp(prefix="hermes-r2-c23-")
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
    def __init__(self, status_ok=False, resume_ok=False):
        self.status_ok, self.resume_ok = status_ok, resume_ok
        self.calls = []
        self.events = collections.deque()

    def call(self, method, params, timeout=30, on_sent=None):
        self.calls.append((method, dict(params)))
        if on_sent:
            on_sent()
        if method == "session.status":
            if self.status_ok:
                return {"output": "Agent Running: No"}
            raise rpc.WSError("session not found")
        if method == "session.resume":
            if self.resume_ok:
                return {"session_id": "s-res"}
            raise rpc.WSError("resume refused: row pruned")
        if method == "config.get":
            return {"config": {}}
        if method == "session.create":
            return {"session_id": "s-new", "stored_session_id": "k-new"}
        if method == "prompt.submit":
            self.events.append({"type": "message.complete",
                                "session_id": params["session_id"],
                                "payload": {"text": "answer",
                                            "status": "complete"}})
            return {"status": "streaming"}
        return {}

    def next_event(self, timeout):
        if self.events:
            return self.events.popleft()
        time.sleep(0.01)
        return None

    def close(self):
        pass

    def params(self, method):
        return [p for m, p in self.calls if m == method]


HISTORY = [
    {"role": "user", "text": "Rename the photos in ~/Pictures/trip."},
    {"role": "bot", "text": "Done: 42 files renamed."},
    {"role": "user", "text": "Now undo the last three."},
]
BRANCH_SEED = [{"role": "user", "content": "Rename the photos in ~/Pictures/trip."},
               {"role": "assistant", "content": "Done: 42 files renamed."}]


def drive(fake, meta, hook=None):
    rpc.ServeSession = lambda: fake
    rpc.SEED_HOOK = hook
    job = {"id": "j", "state": "running", "text": "", "status": "",
           "approval": None, "reply": "", "ok": False, "done": False}
    try:
        with redirect_stderr(io.StringIO()):
            rpc.run_turn(job, meta, "Now undo the last three.", lambda: None)
    finally:
        rpc.SEED_HOOK = None
    return job


def branch_meta(**kw):
    meta = {"title": "a branch", "serve_sid": "s-old", "serve_key": "k-old",
            "messages": list(HISTORY),
            "forked_from": {"session": "chat-src", "at_index": 1, "ts": 1}}
    meta.update(kw)
    return meta


def test_branch_resume_failed():
    fake = FakeServe()
    job = drive(fake, branch_meta(),
                hook=lambda m: {"messages": list(BRANCH_SEED)})
    seed = (fake.params("session.create") or [{}])[0].get("messages")
    check("the branch's own seed is still what the new session gets",
          seed == BRANCH_SEED, seed)
    check("THE FIX: a branch whose session could not be resumed says so",
          "could not be used" in job["reply"], job["reply"])
    check("...naming why (the resume error)",
          "row pruned" in job["reply"], job["reply"])
    check("...and that tool results were not carried over",
          "tool results" in job["reply"] and "not carried" in job["reply"],
          job["reply"])
    check("the agent's answer still leads the reply",
          job["reply"].startswith("answer"), job["reply"])
    check("the notice appears once", job["reply"].count("could not be used") == 1,
          job["reply"])


def test_branch_hook_seeds_nothing():
    # A branch whose hook gave nothing (seed build failed): the dashboard's own
    # transcript seed is used, and the notice says so — as before.
    fake = FakeServe()
    job = drive(fake, branch_meta(), hook=lambda m: {})
    seed = (fake.params("session.create") or [{}])[0].get("messages") or []
    check("hook gave nothing: the transcript seed is used",
          [m["content"] for m in seed] == [h["text"] for h in HISTORY[:2]], seed)
    check("...and the reply says the session was replaced",
          "could not be used" in job["reply"], job["reply"])


def test_lost_with_nothing_to_seed():
    # The resume failed and the conversation holds nothing that can be seeded
    # (only an error bubble before the prompt): the notice is still given,
    # without claiming the earlier messages were carried over.
    fake = FakeServe()
    meta = {"title": "t", "serve_sid": "s-old", "serve_key": "k-old",
            "messages": [{"role": "bot", "text": "model asleep", "err": True},
                         {"role": "user", "text": "Now undo the last three."}]}
    job = drive(fake, meta)
    check("THE FIX: a failed resume with nothing to seed is still reported",
          "could not be used" in job["reply"] and "row pruned" in job["reply"],
          job["reply"])
    check("...without claiming the messages were carried over",
          "given the earlier messages" not in job["reply"], job["reply"])


def test_unchanged_paths():
    # A fresh branch (no session yet, e.g. its first turn or a C120 re-seed,
    # which clears serve_sid/serve_key): seeded, no notice.
    fake = FakeServe()
    job = drive(fake, branch_meta(serve_sid="", serve_key=""),
                hook=lambda m: {"messages": list(BRANCH_SEED)})
    check("a branch's first turn: seeded, no notice",
          job["reply"] == "answer"
          and fake.params("session.create")[0].get("messages") == BRANCH_SEED,
          job["reply"])
    # a live session: nothing is created, nothing is said
    fake = FakeServe(status_ok=True)
    job = drive(fake, branch_meta(),
                hook=lambda m: {"messages": list(BRANCH_SEED)})
    check("a live branch session: reused, no notice",
          job["reply"] == "answer" and not fake.params("session.create"),
          job["reply"])
    # a successful resume: no notice
    fake = FakeServe(resume_ok=True)
    job = drive(fake, branch_meta(),
                hook=lambda m: {"messages": list(BRANCH_SEED)})
    check("a resumed branch session: no notice",
          job["reply"] == "answer" and not fake.params("session.create"),
          job["reply"])
    # a brand-new ordinary conversation: no seed, no notice
    fake = FakeServe()
    job = drive(fake, {"title": "new", "messages": [{"role": "user", "text": "hi"}]})
    check("a new ordinary conversation: no notice", job["reply"] == "answer",
          job["reply"])


def main():
    for t in (test_branch_resume_failed, test_branch_hook_seeds_nothing,
              test_lost_with_nothing_to_seed, test_unchanged_paths):
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
