#!/usr/bin/env python3
"""Regression test for audit finding C07 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: idle_suspend_loop's only "work in flight" checks were CHAT_JOBS and
_briefing_generating. The promotion drill (POST /api/models/drill) and an
evals run talk to the model server directly — no CHAT_JOBS entry, no
note_user_activity() — so with the last chat 10+ minutes old the next tick
booted the model out mid-drill, every remaining case raised URLError, and
the drill stored 0/6 pass=False for a good model.

AFTER: server.py has a small hold registry (`with model_hold(reason, user=)`,
`_model_held()`); idle_suspend_loop treats any hold like a chat turn in
flight (both the suspend path and the down-without-marker path). The drill
thread runs under a hold that also counts as owner activity; evals runs
under one (owner activity only for a manual run).

ast-extracts the REAL server.py registry + idle loop, aux_promotion's drill
route + held runner, and aux_evals' run route + held wrapper, with every
model/launchctl call stubbed.
"""
import ast
import os
import sys
import tempfile
import threading
import time as _real_time
import uuid
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
DASH = REPO / "dashboard"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def extract(path, names, ns):
    tree = ast.parse(Path(path).read_text())
    found, nodes = set(), []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names:
            nodes.append(n); found.add(n.name)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names
                                               for t in n.targets):
            nodes.append(n); found.update(t.id for t in n.targets if isinstance(t, ast.Name))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), ns)
    return sorted(set(names) - found)


class StopLoop(Exception):
    pass


class OneTick:
    def __init__(self):
        self.n = 0

    def sleep(self, _s):
        self.n += 1
        if self.n > 1:
            raise StopLoop()

    @staticmethod
    def time():
        return _real_time.time()


class _R:
    returncode, stdout, stderr = 0, "", ""


class FakeSub:
    def __init__(self):
        self.calls = []

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        return _R()


def server_ns(d):
    marker = os.path.join(d, "agent-idle-suspended")
    ns = {"os": os, "sys": sys, "time": OneTick(), "threading": threading, "uuid": uuid,
          "subprocess": FakeSub(), "IDLE_SUSPEND_FILE": marker,
          "MLX_LABEL": "com.hermes.mlx-server", "_widget_cache": {},
          "_down_seen_at": [0.0], "_DOWN_PERSIST_S": 60, "_idle_bootout_warned": [0.0],
          "idle_suspend_enabled": lambda: True, "agent_paused": lambda: False,
          "agent_idle_suspended": lambda: os.path.exists(marker),
          "model_online": lambda: True, "_chat_jobs_active": lambda: False,
          "_newest_external_turn_ts": lambda: 0.0, "_last_user_activity": 0.0,
          "_idle_min": lambda: 10.0, "_mlx_proc_alive": lambda: False,
          "_start_token_fresh": lambda: False, "agent_wake": lambda **k: None,
          "_mlx_primary_down": lambda timeout=3.0: True,
          "_lifecycle_lock": __import__("threading").Lock()}   # E98
    missing = extract(DASH / "server.py", ["note_user_activity", "_model_holds",
                                           "_model_holds_lock", "model_hold", "_model_held",
                                           "idle_suspend_loop"], ns)
    return ns, missing


def tick(ns):
    ns["time"] = OneTick()
    ns["subprocess"] = FakeSub()
    try:
        ns["idle_suspend_loop"]()
    except StopLoop:
        pass
    return ns["subprocess"].calls


def bootouts(calls):
    return [c for c in calls if c[:2] == ["launchctl", "bootout"]]


def test_registry_and_loop():
    with tempfile.TemporaryDirectory(prefix="hermes-c07-") as d:
        ns, missing = server_ns(d)
        if missing:
            check("server.py hold registry present", False, missing)
            return
        check("idle an hour, nothing held: the loop suspends (baseline)",
              bootouts(tick(ns)) and os.path.exists(ns["IDLE_SUSPEND_FILE"]))
        os.remove(ns["IDLE_SUSPEND_FILE"])
        ns["_last_user_activity"] = 0.0
        with ns["model_hold"]("drill", user=False):
            check("_model_held() is True inside a hold", ns["_model_held"]())
            calls = tick(ns)
            check("a drill/eval in flight (held, no chat job): NO bootout "
                  "(was: booted out mid-drill)", not bootouts(calls), calls)
            check("...and not marked asleep", not os.path.exists(ns["IDLE_SUSPEND_FILE"]))
        check("hold released -> _model_held() False", not ns["_model_held"]())
        check("after the run the idle loop may suspend again", bootouts(tick(ns)))
        os.remove(ns["IDLE_SUSPEND_FILE"])

        # down without a marker while held (the drill's switch window)
        ns["model_online"] = lambda: False
        ns["_down_seen_at"] = [_real_time.time() - 600]
        with ns["model_hold"]("drill", user=False):
            tick(ns)
            check("model down mid-drill (switching): not marked asleep while held",
                  not os.path.exists(ns["IDLE_SUSPEND_FILE"]))
        ns["model_online"] = lambda: True

        ns["_last_user_activity"] = 0.0
        with ns["model_hold"]("drill"):
            t_in = ns["_last_user_activity"]
        check("user=True: entering counts as owner activity", t_in > 0)
        check("user=True: finishing counts again (idle window restarts at the end)",
              ns["_last_user_activity"] >= t_in)
        ns["_last_user_activity"] = 0.0
        with ns["model_hold"]("evals:scheduled", user=False):
            pass
        check("user=False never touches the activity clock", ns["_last_user_activity"] == 0.0)
        try:
            with ns["model_hold"]("x", user=False):
                raise ValueError("boom")
        except ValueError:
            pass
        check("an exception inside the run still releases the hold", not ns["_model_held"]())


class FakeThreading:
    Lock = threading.Lock

    def __init__(self):
        self.started = []

    def Thread(self, target=None, args=(), kwargs=None, daemon=None):
        fake = self

        class T:
            def start(self_inner):
                fake.started.append((target, args, kwargs or {}))
        return T()


class Ctx:
    def __init__(self, body):
        self.body = body


def test_drill():
    with tempfile.TemporaryDirectory(prefix="hermes-c07-pro-") as d:
        ns, missing = server_ns(d)
        if missing:
            return
        seen = []
        ft = FakeThreading()
        ns.update({"threading": ft, "_PRO_LOCK": threading.Lock(), "_PRO_STATE": {},
                   "_model_registry": lambda: [{"id": "org/model"}],
                   "_model_downloaded": lambda m: True, "agent_paused": lambda: False,
                   "_pro_busy_jobs": lambda: False, "active_model": lambda: "org/model",
                   "promotion_drill_run": lambda mid: seen.append(
                       (mid, ns["_model_held"](), ns["_last_user_activity"]))})
        missing = extract(DASH / "aux_promotion.py", ["_pro_run_held", "_pro_drill_post"], ns)
        if missing:
            check("aux_promotion held runner present", False, missing)
            return
        ns["_last_user_activity"] = 0.0
        r = ns["_pro_drill_post"](Ctx({"model_id": "org/model"}))
        check("drill route starts a thread", r.get("ok") and len(ft.started) == 1, r)
        target, args, kw = ft.started[0]
        target(*args, **kw)
        check("the drill thread runs promotion_drill_run under a hold",
              seen and seen[0][0] == "org/model" and seen[0][1] is True, seen)
        check("...that counts as owner activity", seen and seen[0][2] > 0, seen)
        check("...and is released when the drill ends", not ns["_model_held"]())


def test_evals():
    with tempfile.TemporaryDirectory(prefix="hermes-c07-ev-") as d:
        ns, missing = server_ns(d)
        if missing:
            return
        seen = []
        ft = FakeThreading()

        def fake_run(model_id=None, trigger="manual", allow_wake=False):
            seen.append((trigger, ns["_model_held"](), ns["_last_user_activity"]))
            return {"ok": True}
        ns.update({"threading": ft, "_EV_LOCK": threading.Lock(), "_EV_STATE": {},
                   "evals_power": lambda: {"ac": True},
                   "_ev_can_run": lambda: (True, "", False), "evals_run": fake_run,
                   "active_model": lambda: "org/model", "_ev_log": lambda *a: None})
        ns["_ev_g"] = lambda name, default=None: ns.get(name, default)
        missing = extract(DASH / "aux_evals.py", ["_ev_held", "_ev_run_post"], ns)
        if missing:
            check("aux_evals held wrapper present", False, missing)
            return
        ns["_last_user_activity"] = 0.0
        r = ns["_ev_run_post"](Ctx({}))
        check("manual evals route starts a thread", r.get("ok") and len(ft.started) == 1, r)
        target, args, kw = ft.started[0]
        target(*args, **kw)
        check("a manual evals run is held and counts as owner activity",
              seen and seen[0][0] == "manual" and seen[0][1] is True and seen[0][2] > 0, seen)
        ns["_last_user_activity"] = 0.0
        del seen[:]
        ns["_ev_held"]("scheduled", fake_run, trigger="scheduled", allow_wake=False)
        check("a scheduled run is held but is not owner activity",
              seen == [("scheduled", True, 0.0)], seen)
        check("hold released after the run", not ns["_model_held"]())
        tree = ast.parse((DASH / "aux_evals.py").read_text())
        tick_fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_ev_tick"]
        calls = [c for c in ast.walk(tick_fn[0]) if isinstance(c, ast.Call)
                 and isinstance(c.func, ast.Name)] if tick_fn else []
        check("the scheduler calls evals_run through _ev_held('scheduled', ...)",
              any(c.func.id == "_ev_held" and c.args and isinstance(c.args[0], ast.Constant)
                  and c.args[0].value == "scheduled" for c in calls)
              and not any(c.func.id == "evals_run" for c in calls))


def main():
    test_registry_and_loop()
    test_drill()
    test_evals()
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
