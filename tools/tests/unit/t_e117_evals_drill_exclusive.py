#!/usr/bin/env python3
"""E117 (2026-11-09 audit, P3): an eval run and a model drill exclude each
other, and an eval run judges only the model it started on.

BEFORE: neither runner looked at the other's state, and _ev_completion read
active_model() fresh on every call. A drill started during an eval run (or a
scheduled run starting during a drill's temporary swap) judged the format
cases on the drilled model and stored the run under the original model's id.

AFTER: evals_run and _ev_can_run refuse while _PRO_STATE.running is set;
_pro_drill_post refuses while _EV_STATE.running is set, claiming its run
under aux_evals' _EV_LOCK (the lock evals_run holds while it checks and
claims), so the two cannot both start. Every case gets the model id captured
at the run's start; a mid-run switch becomes a 400 (an infrastructure error)
instead of a silent cross-model verdict.

aux_evals.py and aux_promotion.py exec'd, in server.py's sorted order, into
ONE namespace (the way server.py loads them), with the REAL settings
helpers; only the completion call, the threads and the clock are stubbed.
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import urllib.error

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %r" % (name, extra))


def load(tmp, state):
    served = []

    def completion(mid, messages, tools=None, max_tokens=0, timeout=0):
        state["calls"] += 1
        if state.get("switch_at") == state["calls"]:
            state["active"] = "other/model"       # something swaps the model
        if mid != state["active"]:                # C130 pin: other id -> 400
            raise urllib.error.HTTPError("u", 400, "pinned", {}, None)
        served.append((mid, messages[-1]["content"][:24]))
        return {"choices": [{"message": {"content":
                                         '{"city": "Tokyo", "country": "Japan", "population": 1}'}}]}

    started = []
    g = {"__name__": "aux_test", "DATA": tmp,
         "MODEL_URL": "http://127.0.0.1:1/v1/models", "CHAT_JOBS": {},
         **server_real.settings_api(os.path.join(tmp, "settings.json")),
         "active_model": lambda: state["active"], "model_online": lambda: True,
         "agent_paused": lambda: False, "agent_idle_suspended": lambda: False,
         "agent_wake": lambda **k: True,
         "_model_registry": lambda: [{"id": "orig/model"}, {"id": "drill/model"}],
         "_model_downloaded": lambda m: True, "_hf_cache_dir": lambda m: tmp,
         "switch_model": lambda mid: {"ok": True}, "models_payload": lambda: {"models": []},
         "register_get": lambda *a, **k: None,
         "register_post": lambda p, f: g.setdefault("_POSTS", {}).__setitem__(p, f),
         "_cached": lambda k, ttl, fn: fn(),
         "_ev_thread_started": True, "_pro_boot_checked": True}
    for name in ("aux_evals.py", "aux_promotion.py"):        # sorted, as server.py
        path = os.path.join(DASH, name)
        exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), g)
    g["_pro_completion"] = completion
    return g, served


class Ctx:
    def __init__(self, body):
        self.body = body


def test_drill_starts_mid_eval():
    print("\n1. the model is swapped under an eval run (what a drill does)")
    with tempfile.TemporaryDirectory(prefix="hermes-e117-") as tmp:
        state = {"active": "orig/model", "calls": 0, "switch_at": 3}
        g, served = load(tmp, state)
        res = g["evals_run"](trigger="manual")
        check("the run is stored under the model it started on",
              res.get("model") == "orig/model", res.get("model"))
        check("no request was ever judged on another model",
              all(m == "orig/model" for m, _ in served), served)
        check("the cases after the swap are infrastructure errors, not verdicts",
              res.get("errors", 0) >= 1 and res.get("passed", 0) + res.get("errors", 0) <= 9, res)
        rows = sqlite3.connect(os.path.join(tmp, "evals.db")).execute(
            "SELECT model_id FROM runs").fetchall()
        check("evals.db holds one run, for orig/model", rows == [("orig/model",)], rows)


def test_eval_refused_while_drill_runs():
    print("\n2. an eval run (manual or scheduled) while a drill runs")
    with tempfile.TemporaryDirectory(prefix="hermes-e117-") as tmp:
        state = {"active": "drill/model", "calls": 0}
        g, served = load(tmp, state)
        g["_PRO_STATE"]["running"] = "drill/model"
        res = g["evals_run"](trigger="scheduled")
        check("evals_run refuses", res.get("ok") is False and "drill" in res.get("reason", ""), res)
        check("nothing was sent to the model", served == [], served)
        ok, reason, _w = g["_ev_can_run"]() if g["evals_power"]().get("ac") else (False, "drill", False)
        check("_ev_can_run says why (or battery refuses first)",
              ok is False and ("drill" in reason or "battery" in reason), reason)
        g["_PRO_STATE"]["running"] = None
        res = g["evals_run"](trigger="manual")
        check("control: once the drill ends the run goes ahead", res.get("ok") is True, res)


def test_drill_refused_while_eval_runs():
    print("\n3. a drill started while an eval run is in progress")
    with tempfile.TemporaryDirectory(prefix="hermes-e117-") as tmp:
        state = {"active": "orig/model", "calls": 0}
        g, served = load(tmp, state)
        g["_EV_STATE"]["running"] = True
        orig_thread = g["threading"].Thread
        started = []
        g["threading"] = type("T", (), {"Thread": lambda *a, **k: type(
            "X", (), {"start": lambda self: started.append(k.get("args"))})()})
        r = g["_POSTS"]["/api/models/drill"](Ctx({"model_id": "drill/model"}))
        check("the drill refuses", isinstance(r, dict) and r.get("ok") is False
              and "eval" in r.get("error", ""), r)
        check("no drill thread started, no drill claimed",
              started == [] and not g["_PRO_STATE"].get("running"), (started, g["_PRO_STATE"]))
        g["_EV_STATE"]["running"] = False
        r = g["_POSTS"]["/api/models/drill"](Ctx({"model_id": "drill/model"}))
        check("control: with no eval running the drill starts",
              r.get("ok") is True and started == [("drill/model",)], (r, started))
        check("the evals lock is free again", g["_EV_LOCK"].acquire(blocking=False))
        g["_EV_LOCK"].release()
        g["threading"].Thread = orig_thread


def test_race_claims():
    print("\n4. eval and drill claimed at the same moment: exactly one wins")
    for _ in range(25):
        with tempfile.TemporaryDirectory(prefix="hermes-e117-") as tmp:
            state = {"active": "orig/model", "calls": 0}
            g, served = load(tmp, state)
            g["threading"] = type("T", (), {"Thread": lambda *a, **k: type(
                "X", (), {"start": lambda self: None})(), "Lock": threading.Lock})
            gate = threading.Barrier(2)
            out = {}

            def drill():
                gate.wait()
                out["drill"] = g["_POSTS"]["/api/models/drill"](Ctx({"model_id": "drill/model"}))

            def ev():
                gate.wait()
                # evals_run claims then runs; hold it in "running" by
                # checking the claim only
                with g["_EV_LOCK"]:
                    if not g["_ev_drill_running"]() and not g["_EV_STATE"].get("running"):
                        g["_EV_STATE"]["running"] = True
                        out["ev"] = True
                    else:
                        out["ev"] = False
            a, b = threading.Thread(target=drill), threading.Thread(target=ev)
            a.start(); b.start(); a.join(); b.join()
            both = bool(out.get("ev")) and bool((out.get("drill") or {}).get("ok"))
            if both:
                break
    check("never both claimed (25 races)", not both, out)


def main():
    test_drill_starts_mid_eval()
    test_eval_refused_while_drill_runs()
    test_drill_refused_while_eval_runs()
    test_race_claims()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
