#!/usr/bin/env python3
"""Regression test for C78 (docs/audits/2026-10-21-codebase-audit.md).

The machine-protection layer had no tests at all, while CLAUDE.md said
_mem_ceilings was unit-tested: the RAM-aware ceilings (1.0.3), admission
control, the memory-guard restart and idle-suspend's "mark a down model
asleep" predicate could each be broken with every suite green.

Everything here is the REAL code, ast-extracted from dashboard/server.py and
run in a namespace whose side effects are stubs: no pgrep/footprint/sysctl
runs, no launchctl, no real _mlx_restart, no model. The loops are driven tick
by tick with a fake clock whose sleep() raises a BaseException to stop them
(so a future `except Exception` around the loop body cannot swallow it).
"""
import ast
import io
import os
import sys
import tempfile
import threading
import contextlib
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SERVER = os.path.join(REPO, "dashboard", "server.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-800:]))


with open(SERVER, encoding="utf-8") as f:
    TREE = ast.parse(f.read(), filename=SERVER)


def fn(name):
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("%s not found in server.py" % name)


def load(names, ns):
    exec(compile(ast.Module(body=[fn(n) for n in names], type_ignores=[]), SERVER, "exec"), ns)
    return ns


TMP = tempfile.mkdtemp(prefix="t-c78-")
import atexit, shutil  # noqa: E402
atexit.register(shutil.rmtree, TMP, True)   # leave no temp dir behind

# --------------------------------------------------------------------------
print("\n1. _mem_ceilings: the documented table")
ns = load(["_mem_ceilings"], {"os": os})
mc = ns["_mem_ceilings"]
for ram, want in ((16, (12, 14)), (24, (17, 20)), (32, (23, 26)), (36, (26, 30)),
                  (48, (35, 39)), (64, (50, 56)), (96, (50, 56)), (128, (50, 56))):
    got = mc(ram, env={})
    check("%d GB -> soft %d / hard %d" % ((ram,) + want), got == want, got)
# D9 (round 3, model advisor): the (8, 10) floor is capped by the machine —
# soft >= min(8, RAM - 2), hard >= min(10, RAM). The old floor put soft at or
# above ALL of an 8 GB Mac's RAM, so admission control could never engage.
check("8 GB -> (6, 8): soft keeps 2 GB for macOS, hard is the RAM itself (D9)",
      mc(8, env={}) == (6, 8), mc(8, env={}))
check("8 GB: the admission ceiling is now BELOW the machine's RAM (it can engage)",
      mc(8, env={})[0] < 8, mc(8, env={}))
check("4 GB -> (3, 5): the proportional rule wins over the capped floor",
      mc(4, env={}) == (3, 5), mc(4, env={}))
check("9 GB -> (7, 9) (the capped floor, RAM - 2 and RAM)", mc(9, env={}) == (7, 9), mc(9, env={}))
check("10 GB -> (8, 10): from 10 GB up the floor is the old (8, 10), unchanged",
      mc(10, env={}) == (8, 10), mc(10, env={}))
check("11 GB -> (8, 10) as before", mc(11, env={}) == (8, 10), mc(11, env={}))
check("12 GB keeps hard >= soft + 2 (9 -> 11, not 10)", mc(12, env={}) == (9, 11), mc(12, env={}))
for ram in (4, 8, 10, 12, 14, 16, 18, 20, 24, 32, 36, 48, 63, 64, 128):
    s, h = mc(ram, env={})
    if not (h >= s + 2 and s >= min(8, ram - 2) and h >= min(10, ram)):
        check("%d GB: hard >= soft + 2 and the floor hold" % ram, False, (s, h))
        break
else:
    check("every size from 4 to 128 GB: hard >= soft + 2, floor (min(8, RAM-2), min(10, RAM))", True)
_prev = None
for ram in [x / 2.0 for x in range(4, 257)]:          # 2 .. 128 GB in 0.5 GB steps
    cur = mc(ram, env={})
    if _prev is not None and (cur[0] < _prev[0] or cur[1] < _prev[1]):
        check("the pair is monotone in RAM (%.1f GB)" % ram, False, (_prev, cur))
        break
    _prev = cur
else:
    check("the pair is monotone non-decreasing in RAM from 2 to 128 GB", True)
_OLD_FLOOR = lambda r: (max(8.0, float(round(0.72 * r))),
                        max(max(10.0, float(round(0.82 * r))), max(8.0, float(round(0.72 * r))) + 2))
_diff = [r for r in range(10, 64) if mc(r, env={}) != _OLD_FLOOR(r)]
check("D9 changes nothing from 10 GB up (10..63 GB identical to the old floor)", not _diff, _diff)
check("63.9 GB is still on the proportional rule", mc(63.9, env={}) == (46, 52), mc(63.9, env={}))
print("   env overrides")
check("MLX_SOFT_GB and MLX_HARD_GB are taken verbatim",
      mc(64, env={"MLX_SOFT_GB": "40", "MLX_HARD_GB": "45"}) == (40, 45))
check("...even a silly pair (the operator's call)",
      mc(16, env={"MLX_SOFT_GB": "30", "MLX_HARD_GB": "20"}) == (30, 20))
check("a soft-only override drags hard up to soft + 2",
      mc(64, env={"MLX_SOFT_GB": "60"}) == (60, 62), mc(64, env={"MLX_SOFT_GB": "60"}))
check("...but leaves a hard that is already higher alone",
      mc(64, env={"MLX_SOFT_GB": "30"}) == (30, 56))
check("a hard-only override is verbatim", mc(16, env={"MLX_HARD_GB": "15.5"}) == (12, 15.5))
check("unparseable values are ignored, not raised",
      mc(64, env={"MLX_SOFT_GB": "abc", "MLX_HARD_GB": ""}) == (50, 56))
saved = {k: os.environ.get(k) for k in ("MLX_SOFT_GB", "MLX_HARD_GB")}
os.environ["MLX_SOFT_GB"] = "33"
os.environ.pop("MLX_HARD_GB", None)
try:
    check("env=None reads os.environ", mc(64) == (33, 56), mc(64))
finally:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
wired = [n for n in TREE.body if isinstance(n, ast.Assign) and any(
    isinstance(t, ast.Tuple) and [getattr(e, "id", None) for e in t.elts] == ["MLX_SOFT_GB", "MLX_HARD_GB"]
    for t in n.targets)]
check("MLX_SOFT_GB, MLX_HARD_GB come from _mem_ceilings(MACHINE_RAM_GB) (not hardcoded)",
      len(wired) == 1 and isinstance(wired[0].value, ast.Call)
      and getattr(wired[0].value.func, "id", None) == "_mem_ceilings"
      and [getattr(a, "id", None) for a in wired[0].value.args] == ["MACHINE_RAM_GB"],
      ast.dump(wired[0]) if wired else "no assignment")

# --------------------------------------------------------------------------
print("\n2. _machine_ram_gb: bytes -> GiB, absolute sysctl, a loud memoized fallback")


def ram_ns(result):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(stdout=result, returncode=0)
    ns = load(["_machine_ram_gb"], {"subprocess": SimpleNamespace(run=run), "sys": sys,
                                     "_RAM_GB_CACHE": [None]})
    return ns, calls


for raw, want in (("68719476736\n", 64.0), ("17179869184", 16.0), ("38654705664", 36.0)):
    ns, calls = ram_ns(raw)
    check("hw.memsize %s -> %.0f GB" % (raw.strip(), want), ns["_machine_ram_gb"]() == want)
check("it runs /usr/sbin/sysctl by absolute path (launchd PATH has no /usr/sbin)",
      calls and calls[0][0] == "/usr/sbin/sysctl" and "hw.memsize" in calls[0], calls)
ns["_machine_ram_gb"]()
check("the answer is memoized (sysctl runs once per process)", len(calls) == 1, calls)
for bad in (FileNotFoundError("sysctl"), "garbage", "0"):
    ns, calls = ram_ns(bad)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        v = ns["_machine_ram_gb"]()
        ns["_machine_ram_gb"]()
    check("sysctl %r -> falls back to 64" % (bad,), v == 64.0, v)
    check("...and says so on stderr exactly once", err.getvalue().count("[mem]") == 1, err.getvalue())

# --------------------------------------------------------------------------
print("\n3. _mlx_footprint_gb: phys_footprint of every lane, GB and MB")


def fp_ns(pids, outs):
    def run(argv, **kw):
        if argv[0] == "pgrep":
            return SimpleNamespace(stdout=pids)
        if argv[0] == "footprint":
            o = outs[argv[2]]
            if isinstance(o, Exception):
                raise o
            return SimpleNamespace(stdout=o)
        raise AssertionError("unexpected command %r" % argv)
    import re
    return load(["_mlx_footprint_gb"], {"subprocess": SimpleNamespace(run=run), "re": re})


f = fp_ns("101\n202\n", {"101": "phys_footprint: 18.5 GB\n", "202": "x\nphys_footprint:   512 MB\n"})
check("two lanes are summed (18.5 GB + 512 MB = 19.0)",
      abs(f["_mlx_footprint_gb"]() - 19.0) < 1e-9, f["_mlx_footprint_gb"]())
f = fp_ns("", {})
check("no server process -> None", f["_mlx_footprint_gb"]() is None)
f = fp_ns("101\n", {"101": "no number here"})
check("unparseable footprint output -> None (not 0)", f["_mlx_footprint_gb"]() is None)
f = fp_ns("101\n202\n", {"101": "phys_footprint: 7 GB", "202": "nothing"})
check("one unparseable lane is skipped, the other counted", f["_mlx_footprint_gb"]() == 7.0)
f = fp_ns("101\n", {"101": OSError("footprint missing")})
check("a failing footprint(1) -> None, never an exception", f["_mlx_footprint_gb"]() is None)

# --------------------------------------------------------------------------
print("\n4. mlx_admission: refuse new model work at/above the soft ceiling")
override = os.path.join(TMP, "mem-override")
seen = {"ttl": None, "key": None}


def adm(gb, soft=50.0):
    def cached(key, ttl, fn):
        seen["key"], seen["ttl"] = key, ttl
        return fn()
    return load(["mlx_admission"], {"os": os, "MLX_SOFT_GB": soft, "MEM_OVERRIDE_FILE": override,
                                    "_cached": cached, "_mlx_footprint_gb": lambda: gb})["mlx_admission"]


check("below the ceiling -> admitted", adm(49.9)() == (True, 49.9, 50.0))
check("AT the ceiling -> refused", adm(50.0)() == (False, 50.0, 50.0))
check("above -> refused", adm(55.0)() == (False, 55.0, 50.0))
check("no server (footprint None) -> admitted", adm(None)() == (True, None, 50.0))
check("the limit is MLX_SOFT_GB (a 16 GB Mac: 12)", adm(12.5, soft=12.0)() == (False, 12.5, 12.0))
check("the footprint read is cached (15 s, its own key)",
      seen["ttl"] == 15 and seen["key"] == "mlx_ram_fast", seen)
open(override, "w").close()
check("the owner's override file admits even above the ceiling", adm(60.0)()[0] is True)
os.unlink(override)

# --------------------------------------------------------------------------
print("\n5. memory_guard_loop: restart above the HARD ceiling, never while parked")


class Stop(BaseException):
    pass


class Clock:
    def __init__(self, ticks):
        self.t, self.ticks = 1_000_000.0, ticks

    def time(self):
        return self.t

    def sleep(self, s):
        if self.ticks <= 0:
            raise Stop()
        self.ticks -= 1
        self.t += s


def guard(gb, paused=False, asleep=False, restart_ok=True, ticks=1):
    restarts = []
    cache = {"mlx_ram": 1, "mlx_ram_fast": 2, "other": 3}

    def restart():
        restarts.append(1)
        return restart_ok
    g = load(["memory_guard_loop"], {
        "time": Clock(ticks), "sys": sys, "MLX_HARD_GB": 56.0,
        "agent_paused": lambda: paused, "agent_idle_suspended": lambda: asleep,
        "_mlx_footprint_gb": lambda: gb, "_mlx_restart": restart, "_widget_cache": cache,
        # R2-6: the restart runs under the lock a pause also takes
        "_lifecycle_lock": threading.Lock()})
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            g["memory_guard_loop"]()
        except Stop:
            pass
    return restarts, cache, err.getvalue()


r, cache, err = guard(57.0)
check("above the hard ceiling -> one restart", r == [1], r)
check("...the footprint caches are dropped", "mlx_ram" not in cache and "mlx_ram_fast" not in cache
      and "other" in cache, cache)
check("...and it logs one line", err.count("[memory_guard]") == 1, err)
r, cache, err = guard(56.0)
check("AT the hard ceiling -> no restart (strictly above)", r == [], r)
r, cache, err = guard(None)
check("no server -> no restart", r == [], r)
r, cache, err = guard(80.0, paused=True)
check("paused -> no restart, however high", r == [], r)
r, cache, err = guard(80.0, asleep=True)
check("idle-suspended -> no restart", r == [], r)
r, cache, err = guard(80.0, restart_ok=False)
check("a failed restart keeps the caches and logs nothing", r == [1] and "mlx_ram" in cache
      and "[memory_guard]" not in err, (r, cache, err))
r, cache, err = guard(57.0, ticks=3)
check("it keeps watching: one restart per tick over the ceiling", r == [1, 1, 1], r)

# --------------------------------------------------------------------------
print("\n6. idle_suspend_loop: when a down model is marked asleep, and when not")
MARK = os.path.join(TMP, "agent-idle-suspended")


def idle(ticks, *, online=False, proc=False, token=False, jobs=False, briefing=False,
         paused=False, enabled=True, asleep_at=None, external=0.0, idle_s=0.0, idle_min=10.0):
    if os.path.exists(MARK):
        os.unlink(MARK)
    if asleep_at is not None:
        with open(MARK, "w") as f:
            f.write(str(asleep_at))
    clock = Clock(ticks)
    calls = {"bootout": [], "wake": []}

    def run(argv, **kw):
        calls["bootout"].append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    g = {
        "time": clock, "sys": sys, "os": os,
        "subprocess": SimpleNamespace(run=run),
        "idle_suspend_enabled": lambda: enabled,
        "model_always_on": lambda: False,     # X-model-lifecycle-5 branch
        "agent_paused": lambda: paused,
        "agent_idle_suspended": lambda: os.path.exists(MARK),
        "IDLE_SUSPEND_FILE": MARK,
        "_newest_external_turn_ts": lambda: external,
        "agent_wake": lambda wait=True: calls["wake"].append(wait),
        "model_online": lambda: online,
        "_chat_jobs_active": lambda: jobs,
        "_briefing_generating": briefing,
        "_mlx_proc_alive": lambda: proc,
        "_start_token_fresh": lambda: token,
        "_down_seen_at": [0.0],
        "_DOWN_PERSIST_S": 60,
        "_last_user_activity": clock.t - idle_s,
        "_idle_min": lambda: idle_min,
        "MLX_LABEL": "com.hermes.mlx-server",
        "_widget_cache": {"mlx_ram": 1, "mlx_ram_fast": 1, "sys_live": 1},
        # drills / evals hold the model awake (C07): none held here
        "_model_holds": {}, "_model_holds_lock": threading.Lock(),
        # a bootout is confirmed before the marker is written (C24): it took
        "_mlx_primary_down": lambda timeout=3.0: True, "_idle_bootout_warned": [0.0],
        "_lifecycle_lock": threading.Lock(),     # E98: the bootout runs under it
    }
    load(["idle_suspend_loop", "_model_held"], g)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            g["idle_suspend_loop"]()
        except Stop:
            pass
    check_no_errors = "loop error" not in err.getvalue()
    if not check_no_errors:
        check("the loop ran without an error (stub namespace complete?)", False, err.getvalue())
    return os.path.exists(MARK), calls, g, err.getvalue()


m, c, g, e = idle(1)
check("down, no process, no token, no job: tick 1 only notes the time", not m and g["_down_seen_at"][0] > 0)
m, c, g, e = idle(2)
check("...tick 2 (30 s later) is still inside the 60 s persistence window", not m)
m, c, g, e = idle(3)
check("...tick 3 (60 s of persistent down) marks it asleep", m, e)
check("...without running launchctl (nothing to boot out)", c["bootout"] == [], c)
check("...and resets the down clock", g["_down_seen_at"][0] == 0.0)
for label, kw in (("a server process is alive (loading)", {"proc": True}),
                  ("a fresh start token (start in flight)", {"token": True}),
                  ("a chat job is running", {"jobs": True}),
                  ("the briefing is generating", {"briefing": True}),
                  ("the owner paused it", {"paused": True}),
                  ("idle-suspend is switched off", {"enabled": False})):
    m, c, g, e = idle(6, **kw)
    check("never marked while %s" % label, not m and c["bootout"] == [], (m, c))
m, c, g, e = idle(6, online=True, idle_s=0)
check("online and in use: no marker, no bootout", not m and c["bootout"] == [])
m, c, g, e = idle(1, online=True, idle_s=11 * 60)
check("online and idle past _idle_min: booted out and marked", m and len(c["bootout"]) == 1, (m, c))
check("...the bootout targets the primary model service only",
      c["bootout"] and c["bootout"][0][:2] == ["launchctl", "bootout"]
      and c["bootout"][0][2].endswith("/com.hermes.mlx-server"), c)
check("...and drops the RAM widget caches", g["_widget_cache"] == {}, g["_widget_cache"])
m, c, g, e = idle(3, online=True, idle_s=11 * 60, jobs=True)
check("idle but a turn is in flight: never suspended mid-work", not m and c["bootout"] == [])
m, c, g, e = idle(1, online=True, idle_s=9 * 60)
check("idle 9 min of 10: not yet", not m and c["bootout"] == [])
m, c, g, e = idle(1, online=True, idle_s=60 * 60, external=1_000_000.0 + 30)
check("a recent Telegram/hub turn counts as activity", not m and c["bootout"] == [])
m, c, g, e = idle(1, asleep_at=1_000_000.0, external=1_000_000.0 + 120)
check("asleep + an external turn since: wakes it without waiting", c["wake"] == [False], c)
m, c, g, e = idle(1, asleep_at=1_000_000.0, external=1_000_000.0 - 5)
check("asleep + nothing new: stays asleep", c["wake"] == [] and m, c)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
