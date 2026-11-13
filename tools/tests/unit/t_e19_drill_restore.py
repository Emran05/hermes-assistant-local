#!/usr/bin/env python3
"""E19 (2026-11-09 audit, P2): a model drill's restore step never undoes the
owner's Pause or a switch the owner made while the drill ran.

BEFORE: promotion_drill_run's `finally` always called switch_model(orig).
On a paused agent switch_model resumes it (deletes the pause marker, loads
~19 GB) — the owner's kill switch was undone a few seconds after it was
pressed — and a switch to a third model the owner made mid-drill was
reverted to the drill's original. Nothing recorded why.

AFTER: the restore runs only while the drilled model is still the active one.
Paused (or asleep): the original model's FILES are re-activated
(_activate_model, nothing started), so the next start loads the owner's model
and the pause holds. Switched by the owner: nothing is touched. Both outcomes
are recorded in last.restore ({skipped: "paused"|"switched", ...}). A swap
whose start failed after the files were written is restored too.

Exec's the WHOLE real dashboard/aux_promotion.py into a stub namespace whose
switch_model mirrors server.py's rule (paused -> resume). No model, no network.
"""
import io
import json
import os
import sys
import tempfile
import urllib.error

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %r" % (name, extra))


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


OK_RESP = {"choices": [{"message": {"role": "assistant", "content": "4"}}]}


class World:
    """active model, pause marker, server state; `owner_at` = (n, action) fires
    the owner's action on the n-th completion."""

    def __init__(self, d, owner_at=None, start_fails=False):
        self.d = d
        self.active = "stock/model"
        self.paused = False
        self.up = True
        self.log = []
        self.owner_at = owner_at
        self.n = 0
        self.start_fails = start_fails
        self.activated = []

    # what server.py's switch_model does, reduced to its observable effects
    def switch_model(self, mid):
        self.log.append(("switch_model", mid, "paused=%s" % self.paused))
        self.activated.append(mid)
        self.active = mid
        if self.paused:
            self.paused = False                  # agent_power("resume")
            self.log.append(("RESUMED", mid))
            self.up = True
            return {"ok": True, "active": mid, "loading": True}
        if self.start_fails:
            self.up = False
            return {"ok": False, "active": mid, "error": "model server failed to start"}
        self.up = True
        return {"ok": True, "active": mid, "loading": True}

    def activate(self, mid):
        self.log.append(("_activate_model", mid))
        self.activated.append(mid)
        self.active = mid
        return True, ""

    def completion(self, mid, messages, tools=None, max_tokens=0, timeout=0):
        self.n += 1
        if self.owner_at and self.n == self.owner_at[0]:
            what = self.owner_at[1]
            self.log.append(("OWNER", what))
            if what == "pause":
                self.paused = True
                self.up = False
            elif what == "switch":
                self.active = "third/model"
        if not self.up:
            raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        if mid != self.active:
            raise urllib.error.HTTPError("http://x", 400, "only serves " + self.active, {}, None)
        return OK_RESP


def load(d, w):
    def read_json(p, default):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return default

    ns = {"__name__": "aux_promotion_test", "DATA": d,
          "MODEL_URL": "http://127.0.0.1:1/v1/models", "CHAT_JOBS": {},
          "read_json": read_json,
          "_model_registry": lambda: [{"id": "stock/model"}, {"id": "cand/model"}],
          "_model_downloaded": lambda m: True, "_hf_cache_dir": lambda m: d,
          "active_model": lambda: w.active, "agent_paused": lambda: w.paused,
          "agent_idle_suspended": lambda: False,
          "model_online": lambda: w.up, "agent_wake": lambda **k: True,
          "switch_model": w.switch_model, "_activate_model": w.activate,
          "models_payload": lambda: {"models": []},
          "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None}
    path = os.path.join(DASH, "aux_promotion.py")
    exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), ns)
    ns["_pro_completion"] = w.completion
    ns["time"] = FakeClock()
    ns["_pro_parser"] = lambda mid: None
    return ns


def drill(ns, mid="cand/model"):
    ns["_PRO_STATE"].update(running=mid)
    ns["promotion_drill_run"](mid)
    return ns["_PRO_STATE"]


def test_pause_mid_drill():
    print("\n1. the owner presses Pause while a swap drill runs")
    with tempfile.TemporaryDirectory(prefix="hermes-e19-") as d:
        w = World(d, owner_at=(3, "pause"))
        ns = load(d, w)
        st = drill(ns)
        check("the pause HOLDS (agent still paused, never resumed)",
              w.paused and not any(e[0] == "RESUMED" for e in w.log), w.log)
        check("the owner's model is what the next start loads (files re-activated)",
              w.active == "stock/model" and ("_activate_model", "stock/model") in w.log, w.log)
        check("switch_model was NOT called to restore",
              [e for e in w.log if e[0] == "switch_model"] == [("switch_model", "cand/model",
                                                                 "paused=False")], w.log)
        last = st.get("last") or {}
        rs = last.get("restore") or {}
        check("last.restore says it was skipped because of the pause",
              rs.get("skipped") == "paused" and rs.get("ok") is True, rs)
        check("last.error says the run stopped (no verdict)",
              last.get("error") and last.get("pass") is None, last)


def test_switch_mid_drill():
    print("\n2. the owner switches to a third model while a swap drill runs")
    with tempfile.TemporaryDirectory(prefix="hermes-e19-") as d:
        w = World(d, owner_at=(3, "switch"))
        ns = load(d, w)
        st = drill(ns)
        check("the owner's choice stands (third/model still active)",
              w.active == "third/model", (w.active, w.log))
        check("nothing was switched or activated after the owner's switch",
              w.activated == ["cand/model"], w.activated)
        rs = (st.get("last") or {}).get("restore") or {}
        check("last.restore: skipped, switched, names the model now active",
              rs.get("skipped") == "switched" and rs.get("active") == "third/model", rs)


def test_normal_restore():
    print("\n3. control: an undisturbed swap drill restores the original")
    with tempfile.TemporaryDirectory(prefix="hermes-e19-") as d:
        w = World(d)
        ns = load(d, w)
        st = drill(ns)
        check("finished without an error", st.get("error") is None, st.get("error"))
        check("the original model is active again via switch_model",
              w.active == "stock/model" and ("switch_model", "stock/model", "paused=False") in w.log,
              w.log)
        rs = (st.get("last") or {}).get("restore") or {}
        check("last.restore ok, not skipped", rs.get("ok") is True and not rs.get("skipped"), rs)


def test_failed_start_still_restores():
    print("\n4. the swap wrote the candidate's files but its start failed")
    with tempfile.TemporaryDirectory(prefix="hermes-e19-") as d:
        w = World(d, start_fails=True)
        ns = load(d, w)
        w_restore = w.switch_model
        calls = []

        def sw(mid):
            calls.append(mid)
            w.start_fails = (mid == "cand/model")
            return w_restore(mid)
        ns["_pro_orig_switch_model"] = sw     # under the module's warn wrapper
        st = drill(ns)
        check("the candidate is NOT left as the assistant's model",
              w.active == "stock/model", (w.active, calls))
        check("the run reports the failed switch", "switch failed" in str(st.get("error")),
              st.get("error"))


def main():
    test_pause_mid_drill()
    test_switch_mid_drill()
    test_normal_restore()
    test_failed_start_still_restores()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
