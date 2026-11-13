#!/usr/bin/env python3
"""Seam S1-13 (audit 2026-11-09, E98 residual) — a chat that lands mid-bootout.

E98 put idle-suspend's bootout under _lifecycle_lock and re-checks the chat
jobs inside it. What was left: a chat registered in the window AFTER that
re-check, while `launchctl bootout` was still running (it waits for the
19 GB process to exit). Its worker saw no asleep marker yet and the model
still answering, so it skipped the wake and submitted the turn to a model
that was being killed.

Now the loop clears `_idle_bootout_done` BEFORE its in-lock re-check and sets
it again once the marker is written (or the bootout did not take), and the
chat worker waits on it (bounded) before deciding whether to wake. A job
registered before the flag is cleared is seen by the re-check (no bootout);
one registered after it waits, then finds the marker and wakes the model
through agent_wake (whose _mlx_start already retries a just-booted-out
job). Nothing waits on a model START: a switch or restart holding the
lifecycle lock does not hold this flag, and the HTTP route never waits.

Real server.py imported on a throwaway HOME; launchctl, the model probe,
agent_wake and the serve turn are stubs. No model, no launchctl, no network.
"""
import os
import sys
import tempfile
import threading
import time as _real_time

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(SP)))
HOME = tempfile.mkdtemp(prefix="hermes-s1-e98-")
os.makedirs(os.path.join(HOME, ".hermes", "dashboard", "chats"), exist_ok=True)
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(ROOT, "dashboard"))
import server  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:400]))


LOOP_THREADS = set()
PARKED = threading.Event()


class FakeTime(object):
    """server's `time`, except that the idle loop's own sleep returns at once
    the first time and then parks that thread for good (one tick)."""

    def __getattr__(self, n):
        return getattr(_real_time, n)

    def sleep(self, s):
        t = threading.current_thread()
        if t.name.startswith("s1-loop"):
            if t.name in LOOP_THREADS:
                PARKED.set()                   # this tick is over
                threading.Event().wait()
            LOOP_THREADS.add(t.name)
            return None
        return _real_time.sleep(s)


S = {}


def reset(bootout_takes=True):
    S.clear()
    S.update(up=True, booted=False, takes=bootout_takes, wakes=0, turns=[],
             in_bootout=threading.Event(), release=threading.Event())
    try:
        os.remove(server.IDLE_SUSPEND_FILE)
    except OSError:
        pass


class R(object):
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


class FakeSub(object):
    TimeoutExpired = Exception
    CalledProcessError = Exception
    PIPE, DEVNULL, STDOUT = -1, -3, -2

    def run(self, argv, **kw):
        if argv[:2] == ["launchctl", "bootout"]:
            S["in_bootout"].set()
            S["release"].wait(10)
            if S["takes"]:
                S["up"] = False
                S["booted"] = True
            return R(0)
        return R(1)


def fake_wake(wait=True, timeout=90):
    S["wakes"] += 1
    S["up"] = True
    try:
        os.remove(server.IDLE_SUSPEND_FILE)
    except OSError:
        pass
    return True


def fake_turn(job, chat, prompt, save_meta, **kw):
    S["turns"].append({"up": S["up"], "booted_before": S["booted"], "wakes": S["wakes"]})
    job.update(state="done", reply="fine", ok=True)


server.time = FakeTime()
server.subprocess = FakeSub()
server.idle_suspend_enabled = lambda: True
server.agent_paused = lambda: False
server.model_online = lambda: S["up"]
server._mlx_primary_down = lambda timeout=3.0: not S["up"]
server._newest_external_turn_ts = lambda: 0.0
server._idle_min = lambda: 1
server._model_held = lambda: False
server.agent_wake = fake_wake
server.hermes_rpc.run_turn = fake_turn


def tick():
    """One idle-suspend tick on its own thread."""
    PARKED.clear()
    server._last_user_activity = 0.0
    name = "s1-loop-%d" % len(LOOP_THREADS)
    t = threading.Thread(target=server.idle_suspend_loop, name=name, daemon=True)
    t.start()
    return t


def chat(sid):
    job = server._new_job(sid)
    t = threading.Thread(target=server._chat_worker, args=(job, sid, "hello"), daemon=True)
    t.start()
    return job, t


check("(premise) the barrier exists", isinstance(
    getattr(server, "_idle_bootout_done", None), threading.Event))

print("== a chat registered while the bootout runs ==")
reset()
tick()
check("(the loop reached the bootout)", S["in_bootout"].wait(5))
job, wt = chat("s1-mid-bootout")
_real_time.sleep(0.4)                 # the worker reaches its wake check now
check("the turn was NOT submitted while the bootout was running", S["turns"] == [],
      S["turns"])
S["release"].set()
wt.join(15)
PARKED.wait(10)
check("the worker woke the model once", S["wakes"] == 1, S)
check("...and submitted the turn only after the bootout, to a woken model",
      len(S["turns"]) == 1 and S["turns"][0]["booted_before"] and S["turns"][0]["up"]
      and S["turns"][0]["wakes"] == 1, S["turns"])
check("...and finished", job.get("done") is True and job.get("ok") is True, job)

print("\n== the bootout did not take ==")
reset(bootout_takes=False)
tick()
check("(the loop reached the bootout)", S["in_bootout"].wait(5))
job, wt = chat("s1-no-take")
_real_time.sleep(0.4)
check("still waits while the bootout runs", S["turns"] == [], S["turns"])
S["release"].set()
wt.join(15)
PARKED.wait(10)
check("the model is still up: no wake, the turn runs", S["wakes"] == 0
      and len(S["turns"]) == 1 and S["turns"][0]["up"], (S["wakes"], S["turns"]))

print("\n== no bootout in flight: the worker does not wait ==")
reset()
check("the barrier is open between ticks", server._idle_bootout_done.is_set())
t0 = _real_time.time()
job, wt = chat("s1-quiet")
wt.join(10)
check("the turn ran at once, no wake", _real_time.time() - t0 < 2.0 and S["wakes"] == 0
      and len(S["turns"]) == 1, (_real_time.time() - t0, S))

print("\n== a lifecycle change that is not a bootout does not hold a chat ==")
reset()
server._lifecycle_lock.acquire()      # a switch / restart in progress
try:
    t0 = _real_time.time()
    job, wt = chat("s1-switch")
    wt.join(10)
    check("the worker did not wait on the lifecycle lock", _real_time.time() - t0 < 2.0
          and len(S["turns"]) == 1, (_real_time.time() - t0, S["turns"]))
finally:
    server._lifecycle_lock.release()

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
