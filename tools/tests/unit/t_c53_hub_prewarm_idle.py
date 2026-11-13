#!/usr/bin/env python3
"""C53 (docs/audits/2026-10-21-codebase-audit.md): hub_prewarm_loop
refreshed every enabled widget every 45 s with no client.

BEFORE: the loop submitted every widget provider every 45 s forever, window
or no window — measured at ~3,200 provider requests and ~5,800 subprocess
spawns a day on an idle Mac, on battery.

AFTER: one warm-up pass at boot, then a pass only while a client has fetched
/api/hub (hub_data) within HUB_PREWARM_IDLE_S (180 s).  hub_data stamps the
time; the first poll after a quiet spell runs the providers itself, as it
always did.

The REAL hub_prewarm_loop, _run_provider and hub_data are ast-extracted from
server.py (plus the module-level names they use, when present) and driven on
a fake clock: no threads, no sleeping, no network.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

SRC = open(os.path.join(E.DASH, "server.py")).read()
FUNCS = ("hub_prewarm_loop", "_run_provider", "hub_data")
NAMES = ("_hub_client_at", "HUB_PREWARM_IDLE_S", "_hub_last_good")


def extract():
    body = []
    for n in ast.parse(SRC).body:
        if isinstance(n, ast.FunctionDef) and n.name in FUNCS:
            body.append(n)
        elif isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in NAMES for t in n.targets):
            body.append(n)
    return ast.Module(body=body, type_ignores=[])


class Stop(BaseException):
    pass


class Clock(object):
    def __init__(self, max_sleeps, on_sleep=None):
        self.t = 1000000.0
        self.n = 0
        self.max = max_sleeps
        self.on_sleep = on_sleep

    def time(self):
        return self.t

    def sleep(self, s):
        self.n += 1
        if self.n > self.max:
            raise Stop()
        self.t += s
        if self.on_sleep:
            self.on_sleep(self)


class Pool(object):
    class F(object):
        def __init__(self, v):
            self.v = v

        def result(self, timeout=None):
            return self.v

    def submit(self, fn, *a):
        return Pool.F(fn(*a))


def run(max_sleeps, on_sleep=None, holder=None):
    """Drive the real loop on a fake clock until it has slept max_sleeps times."""
    clock = Clock(max_sleeps, on_sleep)
    calls = {"prewarm": [], "hub": 0}
    state = {"in_hub": False}

    def provider():
        if state["in_hub"]:
            calls["hub"] += 1
        else:
            calls["prewarm"].append(clock.t)
        return {"ok": True}

    meta = {"title": "W", "icon": "x", "size": "card", "cat": "life",
            "provider": provider}
    ns = {"time": clock, "WIDGETS": {"a": dict(meta), "b": dict(meta)},
          "get_layout": lambda: {"order": ["a", "b"]},
          "_hub_pool": lambda: Pool(), "model_online": lambda: False,
          "_hub_last_good": {}}
    exec(compile(extract(), "server.py", "exec"), ns)

    def poll():                          # what GET /api/hub does
        state["in_hub"] = True
        try:
            ns["hub_data"]()
        finally:
            state["in_hub"] = False
    if holder is not None:
        holder["poll"] = poll
    try:
        ns["hub_prewarm_loop"]()
    except Stop:
        pass
    return calls, clock


print("=== C53: no client, ~15 minutes ===")
calls, clock = run(21)
passes = len(calls["prewarm"]) // 2
E.check("the loop ran its 20 ticks", clock.n == 22, clock.n)
E.check("exactly one warm-up pass, nothing after it", passes == 1,
        "%d passes" % passes)

print("=== C53: a client polls for five minutes, then leaves ===")
polls = []
holder = {}


def on_sleep(c):
    # the hub window's 60 s refreshHub, for the first 300 s of the run
    start = holder.setdefault("start", c.t)
    if c.t - start <= 300 and (not polls or c.t - polls[-1] >= 60):
        polls.append(c.t)
        holder["poll"]()


calls, clock2 = run(41, on_sleep, holder)
times = sorted(set(calls["prewarm"]))
E.check("the client polled", len(polls) >= 4, polls)
E.check("the hub path still runs every provider on each poll",
        calls["hub"] == 2 * len(polls), (calls["hub"], len(polls)))
during = [t for t in times[1:] if t <= polls[-1]]
E.check("prewarm keeps running while the client is active",
        len(during) >= 3, times)


def last_poll_before(t):
    prior = [q for q in polls if q <= t]
    return prior[-1] if prior else None


bad = [t for t in times[1:]
       if last_poll_before(t) is None or t - last_poll_before(t) > 180]
E.check("no pass runs more than 180 s after the last poll", not bad,
        {"late passes": bad, "last poll": polls[-1]})
E.check("...and the loop did keep ticking after the client left",
        clock2.t - polls[-1] > 600, clock2.t - polls[-1])

sys.exit(E.finish())
