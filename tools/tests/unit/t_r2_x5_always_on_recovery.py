#!/usr/bin/env python3
"""Round-2 regression test for X-model-lifecycle-5 (cross-cluster review of the
merged 2026-10-21 fixes): always-on (C65) disabled the whole idle loop,
including crash recovery for Telegram.

BEFORE: under HERMES_MODEL_ALWAYS_ON / the model-always-on marker,
idle_suspend_enabled() is False and idle_suspend_loop's first statement
skipped EVERY branch — suspend, the down-without-a-marker self-heal and the
Telegram cross-surface wake. The mlx-server plist is still KeepAlive=false, so
a primary that crashed (or was booted out by hand) stayed down until someone
sent a dashboard chat; every Telegram turn failed meanwhile.

AFTER: in always-on mode the loop still watches for a down primary and, once
the down state has persisted (_DOWN_PERSIST_S, no process, no start token, no
job), STARTS it (_mlx_start) — never marks it asleep, never boots it out. A
pause still wins; on-demand mode and an "idle-suspend off" switch are
unchanged.

REAL idle_suspend_loop + model_always_on + idle_suspend_enabled, ast-extracted
from dashboard/server.py; launchctl and the probes stubbed; a fake clock.
HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
import contextlib
import io
import os
import sys
import tempfile
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t_r2_6_lifecycle_races as L  # noqa: E402  (shared loader/clock)

check = L.check


def run_loop(d, ticks, *, always_on=True, idle_off=False, paused=False, online=False,
             proc=False, token=False, jobs=False, lock=None):
    for f in os.listdir(d):
        os.unlink(os.path.join(d, f))
    if always_on:
        open(os.path.join(d, "model-always-on"), "w").close()
    if idle_off:
        open(os.path.join(d, "idle-suspend-off"), "w").close()
    clock = L.Clock(ticks)
    starts, boots = [], []

    def run(argv, **kw):
        boots.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    mark = os.path.join(d, "agent-idle-suspended")
    g = {"time": clock, "sys": sys, "os": os, "threading": threading,
         "subprocess": SimpleNamespace(run=run), "DATA": d,
         "IDLE_SUSPEND_FILE": mark,
         "IDLE_SUSPEND_OFF": os.path.join(d, "idle-suspend-off"),
         "MODEL_ALWAYS_ON_FILE": os.path.join(d, "model-always-on"),
         "agent_paused": paused if callable(paused) else (lambda: paused),
         "_lifecycle_lock": lock or threading.Lock(),
         "agent_idle_suspended": lambda: os.path.exists(mark),
         "_newest_external_turn_ts": lambda: 0.0, "agent_wake": lambda wait=True: None,
         "model_online": lambda: online, "_chat_jobs_active": lambda: jobs,
         "_briefing_generating": False, "_mlx_proc_alive": lambda: proc,
         # _mlx_start mints a token that is fresh for 180 s (mlx-server.sh)
         "_start_token_fresh": lambda: token or bool(
             starts and clock.t - starts[-1] <= 180), "_down_seen_at": [0.0],
         "_DOWN_PERSIST_S": 60, "_last_user_activity": clock.t - 7200,
         "_idle_min": lambda: 10.0, "MLX_LABEL": "com.hermes.mlx-server",
         "_widget_cache": {}, "_model_held": lambda: False,
         "_mlx_primary_down": lambda timeout=3.0: True, "_idle_bootout_warned": [0.0],
         "_mlx_start": lambda uid=None: starts.append(clock.t) or True}
    L.load(["idle_suspend_loop", "model_always_on", "idle_suspend_enabled"], g)
    err = io.StringIO()
    os.environ.pop("HERMES_MODEL_ALWAYS_ON", None)
    with contextlib.redirect_stderr(err):
        try:
            g["idle_suspend_loop"]()
        except L.Stop:
            pass
    return starts, boots, os.path.exists(mark), err.getvalue()


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-x5-") as d:
        print("\n1. always-on: a crashed primary is started again")
        s, b, m, e = run_loop(d, 1)
        check("tick 1 only notes that it is down (nothing started yet)", s == [], s)
        s, b, m, e = run_loop(d, 3)
        check("THE FIX: after 60 s of genuine down-ness it is STARTED once",
              len(s) == 1, (s, e))
        check("...never marked asleep, never booted out", not m and b == [], (m, b))
        check("...and says so in one log line",
              e.count("[idle_suspend] always-on") == 1, e)
        s, b, m, e = run_loop(d, 9)
        check("a start in flight gets its 180 s token window, then (still down) "
              "60 s more before the next attempt", len(s) == 1, s)
        s, b, m, e = run_loop(d, 16)
        check("...so a model that never comes up is retried every ~4 min, not "
              "every tick", len(s) == 2 and s[1] - s[0] >= 240, s)

        print("\n2. ...but never against the owner or a starting model")
        for label, kw in (("paused", {"paused": True}),
                          ("online", {"online": True}),
                          ("a server process is alive (loading)", {"proc": True}),
                          ("a fresh start token", {"token": True}),
                          ("a chat job is waking it itself", {"jobs": True})):
            s, b, m, e = run_loop(d, 6, **kw)
            check("not started while %s" % label, s == [] and b == [], (s, b))

        print("\n2b. ...nor against a pause landing mid-tick, or a restart under way "
              "(review)")
        calls = []

        def pause_lands():
            # False for the three ticks' own checks; the owner's pause lands
            # while the third tick's probes run, so any later look sees it
            calls.append(1)
            return len(calls) > 3
        s, b, m, e = run_loop(d, 3, paused=pause_lands)
        check("REVIEW FIX: a pause that lands during the probes is not undone by "
              "a start", s == [], (s, calls))
        busy = threading.Lock()
        busy.acquire()
        try:
            s, b, m, e = run_loop(d, 3, lock=busy)
        finally:
            busy.release()
        check("REVIEW FIX: not started (and not waited for) while a pause/restart "
              "holds the lifecycle lock", s == [], s)
        s, b, m, e = run_loop(d, 3)
        check("...and the lock is free again after a start", len(s) == 1, s)

        print("\n3. on-demand / idle-suspend-off behave as before")
        s, b, m, e = run_loop(d, 6, always_on=False, idle_off=True)
        check("idle-suspend switched off (not always-on): nothing is started",
              s == [] and b == [] and not m, (s, b, m))
        s, b, m, e = run_loop(d, 3, always_on=False)
        check("on-demand: a down model is still marked asleep, not started",
              s == [] and m, (s, m, e))
    print("\nTESTS %d passed %d failed" % (L.PASSES[0], len(L.FAILS)))
    sys.exit(1 if L.FAILS else 0)


if __name__ == "__main__":
    main()
