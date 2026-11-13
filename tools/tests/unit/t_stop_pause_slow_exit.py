#!/usr/bin/env python3
"""Pause reported failure when it worked (2026-11-12).

With a generation in flight the model server took several seconds to exit
after `launchctl bootout` (rc=0). agent_power("pause") checked for only 3 s,
answered {"ok": false, "error": "bootout failed: rc=0, server still
responding"} and wrote no `agent-paused` marker, and the model then went down
with NEITHER marker on disk.

Now the pause waits (polling, bounded at PAUSE_DOWN_WAIT_S) for the server to
really go before it decides. REAL agent_power / _mlx_primary_down from
server.py under the fake launchd + fake clock of _e21_lifecycle_sim.py.
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


def world(home, exit_after):
    """Model online; a bootout unloads the job at once (rc=0) but the
    process keeps answering for `exit_after` seconds (a generation in
    flight), as launchd's SIGTERM + uvicorn's graceful shutdown do."""
    W, ns = sim.build(home, extra_ns={"_pause_busy": threading.Event()})
    W.running = True
    W.online_at = W.t
    real = W.run
    probes = []

    def run(argv, **kw):
        if argv[:2] == ["launchctl", "bootout"]:
            W.busy_at_bootout = ns["_pause_busy"].is_set()
            W.loaded = False
            W.say("launchd: bootout taken, server still finishing a generation")

            def gone():
                W.running = False
                W.online_at = None
                W.say("model server exited")
            W.at(exit_after, gone)
            return sim.types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["launchctl", "print"]:
            probes.append(W.t)
            W.busy_in_wait = ns["_pause_busy"].is_set()
        return real(argv, **kw)
    ns["subprocess"].run = run
    return W, ns, probes


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-pause-") as home:
        W, ns, probes = world(home, exit_after=8.0)
        r = ns["agent_power"]("pause")
        check("THE FIX: a server that takes 8 s to exit is a pause that worked",
              r.get("ok") is True and r.get("paused") is True, (r, W.log))
        check("THE FIX: the agent-paused marker is written",
              os.path.exists(ns["PAUSE_FILE"]), W.log)
        check("control: it polled several times before deciding",
              len(probes) >= 5, probes)
        check("control: and decided once the server was gone, not at the bound",
              W.t - sim.T0 < 15, W.t - sim.T0)
        check("THE FIX (window): 'pause in progress' is set before the bootout",
              getattr(W, "busy_at_bootout", False) is True)
        check("THE FIX (window): ...and through the wait",
              getattr(W, "busy_in_wait", False) is True)
        check("...and cleared once the pause decided", not ns["_pause_busy"].is_set())

    with tempfile.TemporaryDirectory(prefix="hermes-pause-") as home:
        W, ns, probes = world(home, exit_after=600.0)
        r = ns["agent_power"]("pause")
        check("control: a server that never goes is still a failed pause (bounded wait)",
              r.get("ok") is False and not os.path.exists(ns["PAUSE_FILE"]), r)
        waited = W.t - sim.T0
        check("THE FIX: the wait is bounded at about 20 s (was 3 s)", 15 <= waited <= 25, waited)
        check("a failed pause clears 'pause in progress' too", not ns["_pause_busy"].is_set())

    with tempfile.TemporaryDirectory(prefix="hermes-pause-") as home:
        W, ns, probes = world(home, exit_after=0.0)
        r = ns["agent_power"]("pause")
        check("control: a server that exits at once: paused, no long wait",
              r.get("ok") is True and W.t - sim.T0 < 2, (r, W.t - sim.T0))

    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
