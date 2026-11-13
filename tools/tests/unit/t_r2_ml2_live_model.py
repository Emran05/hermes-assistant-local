#!/usr/bin/env python3
"""X-model-lifecycle-2 (round 2 of the 2026-10-21 audit): a LIVE serve
session is moved to the active model too, not only a cold-resumed one.

C112 aligned a conversation's agent session with the configured model only
inside the session.resume branch. That alignment pins a per-session model
override, and serve's per-turn `_sync_agent_model_with_config` returns early
for every pinned session. So after the owner switched the primary model,
every conversation cold-resumed since serve started kept naming the old id
on its live session, and the pinned model server answered each turn with a
400 until serve restarted. run_turn found the session alive via
session.status and never looked at its model.

Drives the REAL hermes_rpc.run_turn against a fake serve whose session.status
reports the live agent's model the way tui_gateway does
("Model: <id> (<provider>)"). On the old code no config.set is ever sent.
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
os.environ["HOME"] = tempfile.mkdtemp(prefix="hermes-r2-ml2-")
sys.path.insert(0, os.path.join(REPO, "dashboard"))

import hermes_rpc as rpc  # noqa: E402

FAILS = []
PASSES = [0]

OLD = "mlx-community/Qwen3.8-27B-4bit"
NEW = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
CFG = {"model": {"default": NEW, "provider": "custom",
                 "base_url": "http://127.0.0.1:8080/v1"}}


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def status_text(model, running=False):
    return "\n".join(["Hermes TUI Status", "", "Session ID: k-old",
                      "Path: ~/.hermes", "Title: an old chat",
                      "Model: %s (custom)" % model,
                      "Created: 2026-10-20 09:00", "Last Activity: 2026-10-26 10:00",
                      "Tokens: 12,345",
                      "Agent Running: %s" % ("Yes" if running else "No")])


class FakeServe(object):
    def __init__(self, live_model, running=False, set_fails=False):
        self.live_model, self.running, self.set_fails = live_model, running, set_fails
        self.calls = []
        self.events = collections.deque()

    def call(self, method, params, timeout=30, on_sent=None):
        self.calls.append((method, dict(params)))
        if on_sent:
            on_sent()
        if method == "session.status":
            return {"output": status_text(self.live_model, self.running)}
        if method == "session.resume":
            raise AssertionError("a live session must not be resumed")
        if method == "config.get":
            return {"config": CFG}
        if method == "config.set":
            if self.set_fails:
                raise rpc.RPCError("session busy")
            self.live_model = params["value"].split()[0]
            return {"value": self.live_model}
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


HISTORY = [{"role": "user", "text": "hello"}, {"role": "bot", "text": "hi there"},
           {"role": "user", "text": "and now?"}]


def drive(fake):
    rpc.ServeSession = lambda: fake
    meta = {"title": "an old chat", "serve_sid": "s-live", "serve_key": "k-old",
            "messages": list(HISTORY)}
    job = {"id": "j", "state": "running", "text": "", "status": "",
           "approval": None, "reply": "", "ok": False, "done": False}
    with redirect_stderr(io.StringIO()):
        rpc.run_turn(job, meta, "and now?", lambda: None)
    return job, meta


def test_parse():
    check("status: the live model is read off session.status",
          rpc._status_model({"output": status_text(OLD)}) == OLD,
          rpc._status_model({"output": status_text(OLD)}))
    check("status: '(unknown)' (agent not built) reads as no model",
          rpc._status_model({"output": "Model: (unknown) (unknown)"}) == "")
    check("status: no Model line reads as no model",
          rpc._status_model({"output": "Agent Running: No"}) == ""
          and rpc._status_model(None) == "")
    check("status: running flag parsed as before",
          rpc._status_running({"output": status_text(OLD, True)}) is True
          and rpc._status_running({"output": status_text(OLD)}) is False
          and rpc._status_running({}) is None)


def test_live_session_moved():
    fake = FakeServe(OLD)
    job, meta = drive(fake)
    sets = fake.params("config.set")
    check("THE FIX: a live session on the old model is moved to the active one",
          sets == [{"session_id": "s-live", "key": "model",
                    "value": NEW + " --provider custom --session"}], sets)
    m = fake.methods()
    check("...before the prompt goes in",
          "config.set" in m and m.index("config.set") < m.index("prompt.submit"), m)
    check("...and the conversation keeps its own live session",
          "session.create" not in m and meta["serve_sid"] == "s-live"
          and fake.params("prompt.submit")[0]["session_id"] == "s-live", (m, meta))
    check("...with no note on the reply", job["reply"] == "answer", job["reply"])


def test_live_session_left_alone():
    fake = FakeServe(NEW)
    drive(fake)
    check("a live session already on the active model: no switch",
          "config.set" not in fake.methods(), fake.methods())
    fake = FakeServe(OLD, running=True)
    job, meta = drive(fake)
    check("a live session mid-turn is not switched (serve would refuse) "
          "and not closed", "config.set" not in fake.methods()
          and "session.close" not in fake.methods(), fake.methods())


def test_live_switch_fails():
    fake = FakeServe(OLD, set_fails=True)
    job, meta = drive(fake)
    m = fake.methods()
    check("a live session that cannot be moved is closed",
          fake.params("session.close") == [{"session_id": "s-live"}], m)
    check("...without a pointless resume of the same session",
          "session.resume" not in m, m)
    creates = fake.params("session.create")
    check("...and a new session, seeded with the conversation, is used",
          len(creates) == 1 and [x["content"] for x in creates[0].get("messages") or []]
          == ["hello", "hi there"] and meta["serve_sid"] == "s-new", (creates, meta))
    check("...and the reply names both models",
          OLD in job["reply"] and NEW in job["reply"]
          and "could not be used" in job["reply"], job["reply"])


def main():
    for t in (test_parse, test_live_session_moved, test_live_session_left_alone,
              test_live_switch_fails):
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
