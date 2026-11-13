#!/usr/bin/env python3
"""E70: a conversation with replies but no stored agent session is seeded.

2026-11-09 audit: run_turn seeded a new agent session from the transcript (and
said so) only when the conversation HAD a stored serve_sid/serve_key that could
not be used (C23). A conversation with earlier replies and no stored id — one
written before the serve backend, or one whose save_meta() write failed once —
got a brand-new, empty agent and no notice, while the window showed the whole
conversation. Now the transcript seeds the new session whenever it is
non-empty, with the same notice. A Needs-you draft (`no_human`) keeps its
fresh, unseeded session by design (C38: no earlier draft's text in the next).

Drives the REAL hermes_rpc.run_turn against t_r2_c23_seed_notice's fake serve.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t_r2_c23_seed_notice as base  # noqa: E402  (main() is guarded)

rpc = base.rpc
FakeServe = base.FakeServe
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


HISTORY = [
    {"role": "user", "text": "My sister's name is Odile."},
    {"role": "bot", "text": "Noted: your sister is Odile."},
    {"role": "user", "text": "What is my sister's name?"},
]


def drive(meta, job_extra=None, hook=None):
    fake = FakeServe()
    rpc.ServeSession = lambda: fake
    rpc.SEED_HOOK = hook
    job = {"id": "j", "state": "running", "text": "", "status": "",
           "approval": None, "reply": "", "ok": False, "done": False}
    job.update(job_extra or {})
    import io
    from contextlib import redirect_stderr
    try:
        with redirect_stderr(io.StringIO()):
            rpc.run_turn(job, meta, "What is my sister's name?", lambda: None)
    finally:
        rpc.SEED_HOOK = None
    created = fake.params("session.create")
    return job, (created[0] if created else None)


def test_no_ids():
    for label, ids in (("no ids at all", {}),
                       ("blank ids (a failed save_meta)", {"serve_sid": "", "serve_key": ""})):
        meta = dict({"title": "t", "messages": [dict(m) for m in HISTORY]}, **ids)
        job, created = drive(meta)
        msgs = (created or {}).get("messages") or []
        check("%s: the new session is seeded with the earlier messages" % label,
              [m["content"] for m in msgs] == [HISTORY[0]["text"], HISTORY[1]["text"]], created)
        check("%s: the reply says it comes from a new agent session" % label,
              "could not be used" in job["reply"] and "given the earlier messages" in job["reply"],
              job["reply"])
        check("%s: ...naming why (none was stored)" % label,
              "no agent session was stored" in job["reply"], job["reply"])
        check("%s: the agent's answer still leads, the notice appears once" % label,
              job["reply"].startswith("answer") and job["reply"].count("could not be used") == 1,
              job["reply"])
        check("%s: the new ids are kept for the next turn" % label,
              meta.get("serve_sid") == "s-new" and meta.get("serve_key") == "k-new", meta)


def test_unchanged():
    job, created = drive({"title": "new", "messages": [{"role": "user", "text": "hi"}]})
    check("a brand-new conversation: no seed, no notice",
          job["reply"] == "answer" and not (created or {}).get("messages"), (job["reply"], created))
    job, created = drive({"title": "x", "messages": [
        {"role": "bot", "text": "The model was asleep", "err": True},
        {"role": "user", "text": "hi again"}]})
    check("only an error bubble before the prompt: nothing to seed, no notice",
          job["reply"] == "answer" and not (created or {}).get("messages"), (job["reply"], created))
    job, created = drive({"title": "Needs you — drafts", "messages": [dict(m) for m in HISTORY]},
                         job_extra={"no_human": True, "no_escalate": True})
    check("a Needs-you draft (no_human) keeps a fresh, unseeded session",
          job["reply"] == "answer" and not (created or {}).get("messages"), (job["reply"], created))
    seed = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    job, created = drive({"title": "b", "messages": [dict(m) for m in HISTORY],
                          "forked_from": {"session": "chat-src", "at_index": 2, "ts": 1}},
                         hook=lambda m: {"messages": list(seed)})
    check("a branch's first turn: its own seed, no notice",
          job["reply"] == "answer" and (created or {}).get("messages") == seed, (job["reply"], created))


def main():
    for t in (test_no_ids, test_unchanged):
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
