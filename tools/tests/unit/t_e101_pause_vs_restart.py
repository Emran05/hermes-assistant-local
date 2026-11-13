#!/usr/bin/env python3
"""E101 (2026-11-09 audit, P3): no restart path starts the model under a
pause marker.

BEFORE: (a) POST /api/model/mem_free while paused restarted the model
(_mem_free_run never looked at the pause); (b) a pause landing in the 3 s
gap between a Thinking toggle's / a model switch's bootout and its start was
undone by that start. Either way the model ran with `agent-paused` on disk,
where memory_guard and idle-suspend both stand down.

AFTER: the Thinking restart and the switch's bootout+start run under
_lifecycle_lock (which a pause also takes) and re-check the pause inside it;
"Free memory" refuses while paused or asleep; Resume is serialised the same
way. A pause that arrives mid-restart waits, then wins.

REAL server.py functions under a fake launchd + fake clock
(_e21_lifecycle_sim.py); the owner's Pause runs on its own thread, as the
HTTP handler's would.
"""
import os
import sys
import tempfile
import threading

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


def paused(ns):
    return os.path.exists(ns["PAUSE_FILE"])


def race(label, act):
    """Model online; `act` starts a restart; the owner presses Pause 1.5 s
    into its 3 s bootout->start gap."""
    print("\n" + label)
    with tempfile.TemporaryDirectory(prefix="hermes-e101-") as home:
        W, ns = sim.build(home)
        W.running = True
        W.online_at = W.t
        th = {}

        def owner_pause():
            t = threading.Thread(target=lambda: th.update(r=ns["agent_power"]("pause")))
            t.start()
            th["t"] = t
            t.join(0.5)          # old code: finishes here; new code: waits on the lock
        W.at(1.5, owner_pause)   # inside the restart's time.sleep(3)
        out = act(ns)
        th["t"].join(5)
        W.say("final: online=%s paused=%s" % (W.online() or W.running, paused(ns)))
        check("the pause answered ok", (th.get("r") or {}).get("ok") is True, th.get("r"))
        check("final state: paused AND no model process (never both)",
              paused(ns) and not W.running, W.log)
        return out, W, ns


def test_thinking():
    out, W, ns = race("1. Pause inside a Thinking toggle's restart",
                      lambda ns: ns["set_model_thinking"](True))


def test_switch():
    out, W, ns = race("2. Pause inside a model switch's restart",
                      lambda ns: ns["switch_model"]("new/model"))
    check("the switch itself stands (files name the new model)",
          W.state["active"] == "new/model", W.state)


def test_mem_free_paused():
    print('\n3. "Free memory" while PAUSED')
    with tempfile.TemporaryDirectory(prefix="hermes-e101-") as home:
        W, ns = sim.build(home)
        open(ns["PAUSE_FILE"], "w").write("0")
        W.loaded = False
        ns["_mem_free_run"]()
        st = ns["_mem_free_state"]
        check("nothing was started", W.starts == 0 and not W.running, W.log)
        check("the run reports ok:false with the reason",
              st.get("ok") is False and "paused" in (st.get("error") or ""), st)


def test_mem_free_asleep():
    print('\n4. "Free memory" while ASLEEP')
    with tempfile.TemporaryDirectory(prefix="hermes-e101-") as home:
        W, ns = sim.build(home)
        open(ns["IDLE_SUSPEND_FILE"], "w").write("0")
        W.loaded = False
        ns["_mem_free_run"]()
        st = ns["_mem_free_state"]
        check("nothing was started (the asleep model stays asleep)",
              W.starts == 0 and not W.running, W.log)
        check("the run reports ok:false with the reason",
              st.get("ok") is False and "asleep" in (st.get("error") or ""), st)


def test_mem_free_control():
    print('\n5. control: "Free memory" on a running model still restarts it')
    with tempfile.TemporaryDirectory(prefix="hermes-e101-") as home:
        W, ns = sim.build(home)
        W.running = True
        W.online_at = W.t
        ns["_mem_free_run"]()
        check("restarted", ns["_mem_free_state"].get("ok") is True and W.starts == 1, W.log)


def test_resume_after_pause_order():
    print("\n6. control: Resume after a completed pause still starts the model")
    with tempfile.TemporaryDirectory(prefix="hermes-e101-") as home:
        W, ns = sim.build(home)
        W.running = True
        W.online_at = W.t
        r1 = ns["agent_power"]("pause")
        r2 = ns["agent_power"]("resume")
        check("pause then resume: running, no pause marker",
              r1.get("ok") and r2.get("ok") and W.running and not paused(ns), (r1, r2, W.log))


def main():
    test_thinking()
    test_switch()
    test_mem_free_paused()
    test_mem_free_asleep()
    test_mem_free_control()
    test_resume_after_pause_order()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
