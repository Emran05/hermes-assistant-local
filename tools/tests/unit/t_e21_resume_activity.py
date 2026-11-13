#!/usr/bin/env python3
"""E21 (2026-11-09 audit, P2): an owner-started model is not booted out by
idle-suspend a minute later.

BEFORE: Resume (agent_power("resume")), a model switch made while the model
was asleep, a Thinking toggle and "Free memory" loaded the model but did not
count as user activity, so on its first tick that saw the model online
idle_suspend_loop measured idle time from the last chat (hours ago) and booted
it out again. "Wake now" (agent_wake) was the only start that reset the clock.

AFTER: every OWNER-initiated start calls note_user_activity(); background
restarts (memory_guard's _mlx_restart) still do not.

REAL server.py lifecycle functions under a fake launchd + fake clock
(_e21_lifecycle_sim.py). Offline, scratch HOME.
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


HOURS = 3600.0


def scenario(label, prepare, act, last_activity_ago):
    print("\n" + label)
    with tempfile.TemporaryDirectory(prefix="hermes-e21-") as home:
        W, ns = sim.build(home)
        ns["_last_user_activity"] = W.t - last_activity_ago
        prepare(W, ns, home)
        out = {}
        W.at(5, lambda: out.update(r=act(W, ns)))
        sim.run_idle_loop(W, ns, 150)
        return W, ns, out.get("r"), home


def test_resume():
    def prep(W, ns, home):
        open(os.path.join(home, "agent-paused"), "w").write("0")
        W.loaded = False
    W, ns, r, home = scenario("A. Resume after a 3 h pause, no message for 2.5 min",
                              prep, lambda W, ns: ns["agent_power"]("resume"), 3 * HOURS)
    check("resume answered ok", isinstance(r, dict) and r.get("ok"), r)
    check("the model is still online 2.5 min later (not idle-suspended)",
          W.online() and not os.path.exists(ns["IDLE_SUSPEND_FILE"]), W.log)


def test_switch_while_asleep():
    def prep(W, ns, home):
        open(os.path.join(home, "agent-idle-suspended"), "w").write(str(W.t - HOURS))
        W.loaded = False
    W, ns, r, home = scenario("B. Switch model from the menu while the model is asleep",
                              prep, lambda W, ns: ns["switch_model"]("new/model"), 3 * HOURS)
    check("switch answered ok", isinstance(r, dict) and r.get("ok"), r)
    check("the switched-to model is still online 2.5 min later",
          W.online() and W.state["active"] == "new/model", W.log)


def test_thinking_toggle():
    def prep(W, ns, home):        # online, idle for 9.5 of the 10 minutes
        W.running = True
        W.online_at = W.t
    W, ns, r, home = scenario("C. Thinking toggle on an online model close to the idle mark",
                              prep, lambda W, ns: ns["set_model_thinking"](True), 9.5 * 60)
    check("toggle restarted the server", isinstance(r, dict) and r.get("restarted"), r)
    check("the restarted model is still online 2.5 min later", W.online(), W.log)


def test_mem_free():
    def prep(W, ns, home):
        W.running = True
        W.online_at = W.t
    W, ns, r, home = scenario('D. "Free memory" on an online model close to the idle mark',
                              prep, lambda W, ns: ns["_mem_free_run"](), 9.5 * 60)
    check("the free-memory restart succeeded", ns["_mem_free_state"].get("ok") is True,
          ns["_mem_free_state"])
    check("the restarted model is still online 2.5 min later", W.online(), W.log)


def test_background_restart_is_not_activity():
    print("\nE. memory_guard's restart (background) is NOT user activity")
    with tempfile.TemporaryDirectory(prefix="hermes-e21-") as home:
        W, ns = sim.build(home)
        W.running = True
        W.online_at = W.t
        before = W.t - 2 * HOURS
        ns["_last_user_activity"] = before
        ns["_mlx_restart"]()
        check("_mlx_restart() leaves the idle clock alone",
              ns["_last_user_activity"] == before, ns["_last_user_activity"] - before)


def test_control_wake():
    def prep(W, ns, home):
        open(os.path.join(home, "agent-idle-suspended"), "w").write(str(W.t - HOURS))
        W.loaded = False
    W, ns, r, home = scenario("F. control: Wake now from asleep",
                              prep, lambda W, ns: ns["agent_wake"](wait=False), 3 * HOURS)
    check("Wake now stays up (unchanged)", W.online(), W.log)


def main():
    test_resume()
    test_switch_while_asleep()
    test_thinking_toggle()
    test_mem_free()
    test_background_restart_is_not_activity()
    test_control_wake()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
