#!/usr/bin/env python3
"""X-approval-flow-2 (round 2 of the 2026-10-21 audit): a denial the
DASHBOARD made is never passed off as the owner's.

Two kinds of answer come from the dashboard, not the owner:
  * the Trust policy's "Never" tier, answered at once with no card;
  * the TTL deny of a card nobody answered.
serve hands both to the agent as an ordinary denial ("Command denied by
user"), so the reply typically said "you denied it" when the owner never saw
a card or never answered one. Only the transient status line knew better.

Drives the REAL hermes_rpc.run_turn and the REAL permissions.decide in a
throwaway HOME. On the old code none of the replies carries a notice.
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
HOME = tempfile.mkdtemp(prefix="hermes-r2-x2-")
os.environ["HOME"] = HOME
os.makedirs(os.path.join(HOME, ".hermes", "dashboard"), exist_ok=True)
sys.path.insert(0, DASH)

import permissions as perm  # noqa: E402
import hermes_rpc as rpc    # noqa: E402

assert perm.HOME == HOME
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
        self.pending, self.resolved = [], []
        self.events = collections.deque()
        self.lock = threading.Lock()

    def call(self, method, params, timeout=30, on_sent=None):
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
                targets = ([self.pending.pop(0)] if self.pending and not params.get("all")
                           else (self.pending[:] if params.get("all") else []))
                if params.get("all"):
                    self.pending = []
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

    def ask(self, command, key):
        with self.lock:
            self.pending.append(command)
            self.events.append({"type": "approval.request", "session_id": "sid-1",
                                "payload": {"command": command, "pattern_key": key,
                                            "pattern_keys": [key],
                                            "description": key}})

    def finish(self, text):
        with self.lock:
            self.events.append({"type": "message.complete", "session_id": "sid-1",
                                "payload": {"text": text, "status": "complete"}})


class Turn(object):
    def __init__(self):
        self.fake = FakeServe()
        self.job = {"id": "job-1", "session": "chat-x", "state": "running",
                    "text": "", "status": "", "approval": None, "reply": "",
                    "ok": False, "done": False}
        rpc.ServeSession = lambda: self.fake
        self.t = threading.Thread(target=lambda: rpc.run_turn(
            self.job, {"title": "t", "messages": []}, "do it", lambda: None),
            daemon=True)
        self.t.start()

    def card(self):
        ap = self.job.get("approval")
        return ap if self.job.get("state") == "approval" and isinstance(ap, dict) else None

    def end(self, text):
        self.fake.finish(text)
        self.t.join(5)
        return self.job["reply"]


def reset_policy(classes=None):
    for p in (perm.PERM_FILE, perm.SIDECAR, perm.LOG_FILE):
        try:
            os.remove(p)
        except OSError:
            pass
    perm._STATE.update(mtime=None, policy={}, trusted=True, error=None, good=None)
    if classes:
        perm._save({"classes": classes})


rpc.TURN_TIMEOUT = 20
MODEL_SAYS = "You denied that, so I left ~/.zshrc alone."


def test_policy_never():
    reset_policy()
    turn = Turn()
    turn.fake.ask("echo x > ~/.ssh/id_rsa", "credential write")
    check("never: denied at once, no card",
          wait_until(lambda: turn.fake.resolved == [("echo x > ~/.ssh/id_rsa", "deny")])
          and turn.card() is None, (turn.fake.resolved, turn.job))
    reply = turn.end(MODEL_SAYS)
    check("the model's words still lead the reply", reply.startswith(MODEL_SAYS), reply)
    check("THE FIX: the reply says the Trust policy blocked it",
          "Trust policy blocked" in reply, reply)
    check("...naming the command", "`echo x > ~/.ssh/id_rsa`" in reply, reply)
    check("...and the rule (class + tier)",
          "is set to Never" in reply, reply)
    check("...and that no card was shown",
          "no approval card was shown" in reply, reply)


def test_policy_never_twice_said_once():
    reset_policy()
    turn = Turn()
    turn.fake.ask("echo x > ~/.ssh/id_rsa", "credential write")
    wait_until(lambda: len(turn.fake.resolved) == 1)
    turn.fake.ask("echo x > ~/.ssh/id_rsa", "credential write")
    wait_until(lambda: len(turn.fake.resolved) == 2)
    reply = turn.end("ok")
    check("the same block twice is reported once",
          reply.count("Trust policy blocked") == 1, reply)


def test_ttl_deny():
    reset_policy()
    saved = rpc.APPROVAL_TTL
    rpc.APPROVAL_TTL = 0.3
    try:
        turn = Turn()
        turn.fake.ask("rm -rf ~/old-builds", "recursive delete")
        check("ttl: the card is shown", wait_until(lambda: turn.card() is not None))
        check("ttl: ...and denied at its TTL",
              wait_until(lambda: turn.fake.resolved == [("rm -rf ~/old-builds", "deny")]),
              turn.fake.resolved)
        reply = turn.end("You declined, so nothing was deleted.")
        check("THE FIX: the reply says the approval timed out unanswered",
              "timed out unanswered" in reply and "`rm -rf ~/old-builds`" in reply,
              reply)
        check("...and that it did not run", "It did not run" in reply, reply)
    finally:
        rpc.APPROVAL_TTL = saved


def test_owner_answers_add_nothing():
    reset_policy({"git-destructive": "auto"})
    turn = Turn()
    turn.fake.ask("git push --force", "git force push (rewrites remote history)")
    wait_until(lambda: len(turn.fake.resolved) == 1)
    turn.fake.ask("rm -rf ~/x", "recursive delete")
    wait_until(lambda: turn.card() is not None)
    aid = turn.card()["approval_id"]
    turn.job["pending_approval_id"], turn.job["pending_choice"] = aid, "deny"
    wait_until(lambda: len(turn.fake.resolved) == 2)
    reply = turn.end("Done.")
    check("an auto-approval and the owner's own deny add no notice",
          reply == "Done.", reply)
    check("...and both answers went where they belong",
          turn.fake.resolved == [("git push --force", "once"), ("rm -rf ~/x", "deny")],
          turn.fake.resolved)


def test_label_shape():
    check("label: backticks in a command cannot break the code span",
          rpc._cmd_label({"command": "echo `id`"}) == "`echo 'id'`",
          rpc._cmd_label({"command": "echo `id`"}))
    check("label: falls back to the description, then to 'a command'",
          rpc._cmd_label({"description": "write_file ~/.zshrc"}) == "`write_file ~/.zshrc`"
          and rpc._cmd_label({}) == "a command")
    long = rpc._cmd_label({"command": "x" * 500})
    check("label: one bounded line", len(long) <= 124 and "\n" not in long, len(long))


def main():
    for t in (test_label_shape, test_policy_never, test_policy_never_twice_said_once,
              test_ttl_deny, test_owner_answers_add_nothing):
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
