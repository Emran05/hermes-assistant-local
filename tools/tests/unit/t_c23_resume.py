#!/usr/bin/env python3
"""C23 + C112 (2026-10-21 audit): what happens when a conversation's stored
agent session cannot simply be picked up again.

C23: run_turn tried session.status, then session.resume, and on any failure
quietly called session.create — the model answered with no memory of the
conversation while the dashboard still showed every earlier message, with
nothing in the log, on the job or in the reply.

C112: a cold session.resume rebuilds the agent with the model stored in that
session's state.db row, and serve's per-turn config sync skips a session that
carries such an override — so old conversations kept asking the model server
for models the roster deleted (here: 28 of 36 hub sessions).

Drives the REAL hermes_rpc.run_turn against a fake serve and checks:
  * a failed resume is logged, the new session is SEEDED with the transcript
    (user/assistant text, no error bubbles, no trailing prompt, newest first
    under SEED_MAX_CHARS) and the reply says so;
  * a resumed session on another model is moved to the configured model —
    agent built first, then config.set model "<model> --provider <p>
    --session" — before the prompt goes in; if that fails the resumed session
    is closed and a seeded new one is used, and the reply says why.
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
os.environ["HOME"] = tempfile.mkdtemp(prefix="hermes-c23-")
sys.path.insert(0, os.path.join(REPO, "dashboard"))

import hermes_rpc as rpc  # noqa: E402

FAILS = []
PASSES = [0]

OLD = "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"
NEW = "mlx-community/Qwen3.8-27B-4bit"
CFG = {"model": {"default": NEW, "provider": "custom",
                 "base_url": "http://127.0.0.1:8080/v1"}}


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


class FakeServe(object):
    def __init__(self, status_ok=False, resume=None, set_result=None,
                 set_fails=False):
        self.status_ok = status_ok
        self.resume = resume              # dict result, or None = "not found"
        self.set_result = set_result if set_result is not None else {"value": NEW}
        self.set_fails = set_fails
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
            if self.resume is None:
                raise rpc.WSError("session not found")
            return self.resume
        if method == "config.get":
            return {"config": CFG}
        if method == "config.set":
            if self.set_fails:
                raise rpc.WSError("session busy")
            return self.set_result
        if method == "session.create":
            return {"session_id": "s-new", "stored_session_id": "k-new"}
        if method == "prompt.submit":
            self.events.append({"type": "message.complete",
                                "session_id": params["session_id"],
                                "payload": {"text": "answer", "status": "complete"}})
            return {"status": "streaming"}
        return {}

    def next_event(self, timeout):
        if self.events:
            return self.events.popleft()
        time.sleep(0.01)
        return None

    def close(self):
        pass

    def methods(self):
        return [m for m, _ in self.calls]

    def params(self, method):
        return [p for m, p in self.calls if m == method]


def chat(messages, **kw):
    meta = {"title": "an old chat", "serve_sid": "s-old", "serve_key": "k-old",
            "messages": messages}
    meta.update(kw)
    return meta


HISTORY = [
    {"role": "user", "text": "Which project are we on?"},
    {"role": "bot", "text": "The Hermes dashboard."},
    {"role": "bot", "text": "The model was asleep and didn't wake in time.", "err": True},
    {"role": "user", "text": "Right — remember the deadline is Friday."},
    {"role": "bot", "text": "Noted: Friday."},
    {"role": "user", "text": "So what's left?"},          # the prompt being sent
]


def drive(fake, meta):
    rpc.ServeSession = lambda: fake
    job = {"id": "j", "state": "running", "text": "", "status": "",
           "approval": None, "reply": "", "ok": False, "done": False}
    err = io.StringIO()
    with redirect_stderr(err):
        rpc.run_turn(job, meta, "So what's left?", lambda: None)
    return job, err.getvalue()


def test_c23_resume_failure():
    fake = FakeServe(resume=None)
    meta = chat(list(HISTORY))
    job, log = drive(fake, meta)
    creates = fake.params("session.create")
    check("C23: a new session is created after the failed resume",
          len(creates) == 1, fake.methods())
    seed = (creates[0] if creates else {}).get("messages") or []
    check("THE FIX: the new session is seeded with the conversation",
          [m["content"] for m in seed] == [
              "Which project are we on?", "The Hermes dashboard.",
              "Right — remember the deadline is Friday.", "Noted: Friday."],
          seed)
    check("...as user/assistant turns", [m["role"] for m in seed] ==
          ["user", "assistant", "user", "assistant"], seed)
    check("...without the dashboard's error bubble",
          all("asleep" not in m["content"] for m in seed), seed)
    check("...and without the prompt about to be submitted",
          all(m["content"] != "So what's left?" for m in seed), seed)
    check("THE FIX: the reply says the agent session was replaced",
          job["reply"].startswith("answer")
          and "could not be used" in job["reply"]
          and "session not found" in job["reply"], job["reply"])
    check("THE FIX: the failure is in the log, with the key",
          "session.resume(k-old) failed" in log, log)
    check("the conversation now points at the new session",
          meta["serve_sid"] == "s-new" and meta["serve_key"] == "k-new", meta)

    # a brand-new conversation is untouched: no seed, no note
    fake = FakeServe()
    meta = {"title": "new", "messages": [{"role": "user", "text": "hi"}]}
    job, _ = drive(fake, meta)
    check("a new conversation: created exactly as before (no seed)",
          "messages" not in fake.params("session.create")[0], fake.calls)
    check("a new conversation: no note on the reply", job["reply"] == "answer",
          job["reply"])

    # a very long conversation keeps only its newest messages
    saved = rpc.SEED_MAX_CHARS
    rpc.SEED_MAX_CHARS = 50
    try:
        fake = FakeServe(resume=None)
        long = [{"role": "user" if i % 2 == 0 else "bot", "text": "m%02d " % i + "x" * 20}
                for i in range(10)] + [{"role": "user", "text": "now"}]
        drive(fake, chat(long))
        seed = fake.params("session.create")[0].get("messages") or []
        check("a long transcript is cut to the newest messages under the cap",
              [m["content"][:3] for m in seed] == ["m08", "m09"], seed)
    finally:
        rpc.SEED_MAX_CHARS = saved

    # a branch keeps its own seed (aux_branch's SEED_HOOK)
    rpc.SEED_HOOK = lambda meta: {"messages": [{"role": "user", "content": "branch seed"}]}
    try:
        fake = FakeServe(resume=None)
        drive(fake, chat(list(HISTORY), forked_from={"session": "chat-src"}))
        seed = fake.params("session.create")[0].get("messages")
        check("a branch's own seed is not replaced",
              seed == [{"role": "user", "content": "branch seed"}], seed)
    finally:
        rpc.SEED_HOOK = None


def resumed(model):
    return {"session_id": "s-res", "resumed": "k-old",
            "info": {"model": model, "provider": "custom"}}


def test_c112_model():
    fake = FakeServe(resume=resumed(OLD))
    meta = chat(list(HISTORY))
    job, log = drive(fake, meta)
    sets = fake.params("config.set")
    check("THE FIX: a resumed session on a deleted model is moved",
          sets == [{"session_id": "s-res", "key": "model",
                    "value": NEW + " --provider custom --session"}], sets)
    m = fake.methods()
    check("...after its agent is built and before the prompt goes in",
          "process.list" in m and "config.set" in m and "prompt.submit" in m
          and m.index("process.list") < m.index("config.set") < m.index("prompt.submit"),
          m)
    check("...session-scoped: config.yaml is never written (--session, no --global)",
          sets and "--global" not in sets[0]["value"], sets)
    check("...and the conversation keeps its own session (no new one)",
          "session.create" not in m and meta["serve_key"] == "k-old"
          and meta["serve_sid"] == "s-res", (m, meta))
    check("...with no note on the reply", job["reply"] == "answer", job["reply"])

    fake = FakeServe(resume=resumed(NEW))
    drive(fake, chat(list(HISTORY)))
    check("a resumed session already on the configured model is left alone",
          "config.set" not in fake.methods(), fake.methods())

    fake = FakeServe(resume={"session_id": "s-res", "resumed": "k-old"})
    drive(fake, chat(list(HISTORY)))
    check("a resume reply with no model info is left alone",
          "config.set" not in fake.methods(), fake.methods())

    for label, fake in (("the switch fails", FakeServe(resume=resumed(OLD), set_fails=True)),
                        ("the switch wants confirmation",
                         FakeServe(resume=resumed(OLD),
                                   set_result={"confirm_required": True}))):
        meta = chat(list(HISTORY))
        job, _ = drive(fake, meta)
        m = fake.methods()
        check("%s: the resumed session is closed" % label,
              fake.params("session.close") == [{"session_id": "s-res"}], m)
        check("%s: a seeded new session is used instead" % label,
              len(fake.params("session.create")) == 1
              and fake.params("session.create")[0].get("messages")
              and fake.params("prompt.submit")[0]["session_id"] == "s-new", m)
        check("%s: the reply names both models" % label,
              OLD in job["reply"] and NEW in job["reply"], job["reply"])


def main():
    for t in (test_c23_resume_failure, test_c112_model):
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
