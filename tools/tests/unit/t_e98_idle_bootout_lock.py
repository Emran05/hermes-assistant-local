#!/usr/bin/env python3
"""E98 (2026-11-09 audit, P3): idle-suspend's bootout is serialised with
the other lifecycle changes and re-checks activity right before it.

BEFORE: idle_suspend_loop checked chat jobs / holds, computed the idle time,
then ran `launchctl bootout` with no lock and no re-check. A chat that
arrived in between (its route calls note_user_activity() and registers the
job before the worker submits) saw the model online, submitted, and had the
server killed under its turn.

AFTER: the bootout runs under _lifecycle_lock (non-blocking: a pause or a
restart already holding it owns the server; the next tick retries), and the
pause / asleep / jobs / briefing / holds / idle-time checks are repeated
inside it.

REAL idle_suspend_loop under a fake launchd + fake clock
(_e21_lifecycle_sim.py). The chat is injected at the exact point the old code
had already decided: during the idle-time read.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _e21_lifecycle_sim as sim  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-900:]))


def online_idle(home, idle_s=11 * 60):
    W, ns = sim.build(home)
    W.running = True
    W.online_at = W.t
    ns["_last_user_activity"] = W.t - idle_s
    return W, ns


def test_chat_arrives_mid_tick():
    print("\n1. a chat turn arrives while the tick is deciding")
    with tempfile.TemporaryDirectory(prefix="hermes-e98-") as home:
        W, ns = online_idle(home)
        fired = []

        def newest():
            if not fired:                       # the route lands right here
                fired.append(1)
                ns["CHAT_JOBS"]["job1"] = {"done": False}
                ns["_last_user_activity"] = W.t
            return 0.0
        ns["_newest_external_turn_ts"] = newest
        sim.run_idle_loop(W, ns, 35)            # exactly one tick
        check("the chat really arrived mid-tick", fired == [1])
        check("the model was NOT booted out under the new turn",
              W.running and not os.path.exists(ns["IDLE_SUSPEND_FILE"]), W.log)


def test_lock_held_skips():
    print("\n2. a pause / restart holding the lifecycle lock: the tick stands down")
    with tempfile.TemporaryDirectory(prefix="hermes-e98-") as home:
        W, ns = online_idle(home)
        ns["_lifecycle_lock"].acquire()
        try:
            sim.run_idle_loop(W, ns, 35)
        finally:
            ns["_lifecycle_lock"].release()
        check("no bootout while another lifecycle change holds the lock",
              W.running, W.log)
        sim.run_idle_loop(W, ns, 35)
        check("...and the next tick, lock free, suspends the idle model",
              not W.running and os.path.exists(ns["IDLE_SUSPEND_FILE"]), W.log)


def test_control_idle():
    print("\n3. control: an idle model with nothing arriving is suspended")
    with tempfile.TemporaryDirectory(prefix="hermes-e98-") as home:
        W, ns = online_idle(home)
        sim.run_idle_loop(W, ns, 35)
        check("suspended and marked asleep",
              not W.running and os.path.exists(ns["IDLE_SUSPEND_FILE"]), W.log)
        check("the lifecycle lock was released", ns["_lifecycle_lock"].acquire(blocking=False))


def test_hold_arrives_mid_tick():
    print("\n4. a drill / eval hold taken while the tick is deciding")
    with tempfile.TemporaryDirectory(prefix="hermes-e98-") as home:
        W, ns = online_idle(home)
        fired = []

        def newest():
            if not fired:
                fired.append(1)
                ns["_model_holds"]["k"] = ("drill", W.t)
            return 0.0
        ns["_newest_external_turn_ts"] = newest
        sim.run_idle_loop(W, ns, 35)
        check("not booted out under the new hold", W.running, W.log)


def main():
    test_chat_arrives_mid_tick()
    test_lock_held_skips()
    test_control_idle()
    test_hold_arrives_mid_tick()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
