#!/usr/bin/env python3
"""Round-2 regression test for R2-6 (2026-10-08 audit P3 prose, model
lifecycle).

BEFORE:
  * memory_guard_loop checked agent_paused()/agent_idle_suspended() only at
    the START of its tick, then spent seconds in footprint(1) before
    _mlx_restart — a pause (or idle-suspend) that landed in between was
    undone: the model was bootstrapped again under the pause marker. Nothing
    serialised a pause with a restart already under way either.
  * _mlx_proc_alive()'s pgrep ("mlx_lm server|mlx-vlm-launch") matched BOTH
    lanes, so while the background 9B ran on :8081, idle_suspend_loop's
    "down without a marker" self-heal never fired for a crashed primary.

AFTER: the restart runs under a lifecycle lock that a pause also takes, and
re-checks both markers inside it; _mlx_proc_alive() matches only the primary
launcher (`--port 8080`, either backend).

REAL memory_guard_loop / agent_power / _mlx_proc_alive / idle_suspend_loop,
ast-extracted from dashboard/server.py; launchctl / pgrep / footprint are
stubs (a fake process table matched with the same regex pgrep -f gets); a fake
clock stops the loops. HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
import ast
import contextlib
import io
import os
import re
import sys
import tempfile
import threading
import time as _real_time
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SRC = os.environ.get("HERMES_T_SERVER_SRC") or os.path.join(REPO, "dashboard", "server.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-600:]))


TREE = ast.parse(open(SRC, encoding="utf-8").read(), filename=SRC)


def load(names, ns, optional=()):
    want = set(names) | set(optional)
    body = []
    for n in TREE.body:
        if isinstance(n, ast.FunctionDef) and n.name in want:
            body.append(n)
        elif isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in want for t in n.targets):
            body.append(n)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 SRC, "exec"), ns)
    missing = set(names) - set(ns)
    assert not missing, missing
    return ns


class Stop(BaseException):
    pass


class Clock:
    def __init__(self, ticks):
        self.t, self.ticks = 1_000_000.0, ticks

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, s):
        if self.ticks <= 0:
            raise Stop()
        self.ticks -= 1
        self.t += s


def pgrep_table(procs, fail=False):
    """subprocess stand-in whose pgrep -f matches `procs` like the real one."""
    def run(argv, **kw):
        if fail:
            raise OSError("pgrep exploded")
        if argv[:2] == ["pgrep", "-f"]:
            hits = [str(100 + i) for i, cmd in enumerate(procs)
                    if re.search(argv[2], cmd)]
            return SimpleNamespace(returncode=0 if hits else 1,
                                   stdout="\n".join(hits), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return SimpleNamespace(run=run)


PRIMARY_VLM = ("/u/.hermes/mlx-vlm-venv/bin/python /r/mlx-vlm-launch.py --model "
               "mlx-community/Qwen3.8-27B-4bit --host 127.0.0.1 --port 8080 "
               "--max-tokens 4096")
PRIMARY_LM = ("python3 /r/mlx-vlm-launch.py --mlx-lm --model m --host 127.0.0.1 "
              "--port 8080 --max-tokens 4096")
BG = ("/u/.hermes/mlx-vlm-venv/bin/python /r/mlx-vlm-launch.py --model "
      "mlx-community/Qwen3.5-9B-4bit --host 127.0.0.1 --port 8081 --max-tokens 4096")


def test_proc_probe():
    print("\n1. _mlx_proc_alive() sees the PRIMARY lane only")
    for label, procs, want in (("only the background 9B on :8081", [BG], False),
                               ("the primary (mlx_vlm) on :8080", [PRIMARY_VLM], True),
                               ("the primary (mlx_lm fallback) on :8080", [PRIMARY_LM], True),
                               ("both lanes", [BG, PRIMARY_VLM], True),
                               ("nothing", [], False)):
        ns = load(["_mlx_proc_alive"], {"subprocess": pgrep_table(procs)},
                  optional=["_MLX_PRIMARY_PGREP"])
        got = ns["_mlx_proc_alive"]()
        check(("THE FIX: " if label.startswith("only") else "") +
              "%s -> %s" % (label, want), got is want, got)
    ns = load(["_mlx_proc_alive"], {"subprocess": pgrep_table([], fail=True)},
              optional=["_MLX_PRIMARY_PGREP"])
    check("a failed probe still counts as alive (never mark a loading model)",
          ns["_mlx_proc_alive"]() is True)


def test_self_heal_with_bg_lane(tmp):
    print("\n2. idle_suspend_loop marks a crashed primary asleep while the 9B runs")
    mark = os.path.join(tmp, "agent-idle-suspended")
    clock = Clock(3)
    g = {"time": clock, "sys": sys, "os": os, "threading": threading,
         "subprocess": pgrep_table([BG]),
         "idle_suspend_enabled": lambda: True, "agent_paused": lambda: False,
         "agent_idle_suspended": lambda: os.path.exists(mark),
         "IDLE_SUSPEND_FILE": mark, "_newest_external_turn_ts": lambda: 0.0,
         "agent_wake": lambda wait=True: None, "model_online": lambda: False,
         "_chat_jobs_active": lambda: False, "_briefing_generating": False,
         "_start_token_fresh": lambda: False, "_down_seen_at": [0.0],
         "_DOWN_PERSIST_S": 60, "_last_user_activity": clock.t, "_idle_min": lambda: 10.0,
         "MLX_LABEL": "com.hermes.mlx-server", "_widget_cache": {},
         "_model_holds": {}, "_model_holds_lock": threading.Lock(),
         "_mlx_primary_down": lambda timeout=3.0: True, "_idle_bootout_warned": [0.0],
         "model_always_on": lambda: False, "_external_turn_inflight": lambda: False}
    load(["idle_suspend_loop", "_model_held", "_mlx_proc_alive"], g,
         optional=["_MLX_PRIMARY_PGREP"])
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            g["idle_suspend_loop"]()
        except Stop:
            pass
    check("THE FIX: after 60 s down (bg lane alive) the primary is marked asleep",
          os.path.exists(mark), err.getvalue())
    check("...without a loop error", "loop error" not in err.getvalue(), err.getvalue())


def guard_ns(tmp, footprint, restart):
    pause = os.path.join(tmp, "agent-paused")
    idle = os.path.join(tmp, "agent-idle-suspended")
    for p in (pause, idle):
        if os.path.exists(p):
            os.unlink(p)
    ns = {"time": Clock(1), "sys": sys, "os": os, "MLX_HARD_GB": 56.0,
          "PAUSE_FILE": pause, "IDLE_SUSPEND_FILE": idle,
          "_mlx_footprint_gb": footprint, "_mlx_restart": restart,
          "_widget_cache": {}, "_lifecycle_lock": threading.Lock()}
    load(["memory_guard_loop", "agent_paused", "agent_idle_suspended"], ns)
    return ns, pause, idle


def run_guard(ns):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            ns["memory_guard_loop"]()
        except Stop:
            pass
    return err.getvalue()


def test_guard_rechecks(tmp):
    print("\n3. memory_guard_loop: a pause / idle-suspend that lands during footprint wins")
    for label, which in (("a pause", "pause"), ("an idle-suspend", "idle")):
        restarts = []
        holder = {}

        def footprint():
            # the slow part of the tick: the owner's pause lands right here
            with open(holder[which], "w") as f:
                f.write("1")
            return 80.0

        ns, pause, idle = guard_ns(tmp, footprint, lambda: restarts.append(1) or True)
        holder.update(pause=pause, idle=idle)
        log = run_guard(ns)
        check("THE FIX: %s landing mid-tick is not undone by a restart" % label,
              restarts == [], (restarts, log))
    restarts = []
    ns, _, _ = guard_ns(tmp, lambda: 80.0, lambda: restarts.append(1) or True)
    run_guard(ns)
    check("with no pause, over the hard ceiling still restarts once", restarts == [1], restarts)


def test_pause_waits_for_restart(tmp):
    print("\n4. a pause that arrives mid-restart is applied AFTER it")
    events = []
    lock = threading.Lock()
    in_restart = threading.Event()

    def restart():
        events.append(("restart-start", _real_time.monotonic()))
        in_restart.set()
        _real_time.sleep(0.6)
        events.append(("restart-end", _real_time.monotonic()))
        return True

    def run(argv, **kw):
        if argv[:2] == ["launchctl", "bootout"]:
            events.append(("pause-bootout", _real_time.monotonic()))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ns, pause, idle = guard_ns(tmp, lambda: 80.0, restart)
    ns["_lifecycle_lock"] = lock
    pns = {"os": os, "sys": sys, "time": _real_time, "subprocess": SimpleNamespace(run=run),
           "MLX_LABEL": "com.hermes.mlx-server", "PAUSE_FILE": pause,
           "IDLE_SUSPEND_FILE": idle, "_widget_cache": {},
           "_mlx_primary_down": lambda timeout=3.0: True, "_lifecycle_lock": lock,
           "_mlx_start": lambda uid=None: True}
    load(["agent_power"], pns)
    t = threading.Thread(target=run_guard, args=(ns,))
    t.start()
    in_restart.wait(3)
    res = pns["agent_power"]("pause")
    t.join(5)
    order = [e[0] for e in sorted(events, key=lambda e: e[1])]
    check("THE FIX: the pause's bootout runs after the restart finished",
          order == ["restart-start", "restart-end", "pause-bootout"], order)
    check("...and the pause still lands (marker written, ok)",
          res.get("ok") is True and os.path.exists(pause), res)


def test_guard_skips_while_lifecycle_busy(tmp):
    print("\n5. memory_guard_loop never restarts on a reading taken before "
          "another restart (review)")
    # A "Free memory" restart (or a pause) holds the lifecycle lock while the
    # guard's footprint reading — taken BEFORE that restart freed the balloon
    # — is over the ceiling. Waiting for the lock and then restarting on that
    # stale number bounced a freshly restarted model a second time.
    restarts = []
    lock = threading.Lock()
    ns, _, _ = guard_ns(tmp, lambda: 80.0, lambda: restarts.append(1) or True)
    ns["_lifecycle_lock"] = lock
    lock.acquire()                      # someone else's restart is under way
    try:
        done = []
        t = threading.Thread(target=lambda: done.append(run_guard(ns)), daemon=True)
        t.start()
        t.join(2)
        waited = t.is_alive()
    finally:
        lock.release()                  # ...and now that restart is over
    t.join(3)
    check("the tick does not wait on the busy lock", not waited, done)
    check("REVIEW FIX: no restart on the stale over-ceiling reading", restarts == [],
          restarts)
    check("...and the lock is left free", lock.acquire(blocking=False))
    lock.release()


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-6-") as tmp:
        test_proc_probe()
        test_self_heal_with_bg_lane(tmp)
        test_guard_rechecks(tmp)
        test_pause_waits_for_restart(tmp)
        test_guard_skips_while_lifecycle_busy(tmp)
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
