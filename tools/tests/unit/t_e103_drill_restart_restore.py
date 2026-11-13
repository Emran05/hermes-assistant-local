#!/usr/bin/env python3
"""E103 (2026-11-09 audit, P3): a dashboard restart in the middle of a model
drill does not leave the candidate as the assistant's model for good.

BEFORE: the drill's restore lived only in its daemon thread's `finally`, and
_PRO_STATE is memory-only. A dashboard that ended mid-drill (self-update,
kickstart -k, crash, logout) left active-model and Hermes model.default
naming the candidate — for an uncensored candidate, a permanent activation
with no acknowledgement.

AFTER: the drill writes ~/.hermes/dashboard/drill-restore.json (0600)
BEFORE the swap and removes it once the drilled model is no longer active.
At load, aux_promotion starts one thread that finds a left-over restore
point and puts the original back through the normal path — switch_model
when the model is up and not paused, the files only (_activate_model)
when it is down, paused or asleep — and nothing when the owner has
switched since. The outcome lands in last.restore for the UI.

Exec's the WHOLE real dashboard/aux_promotion.py into a stub namespace.
"""
import io
import json
import os
import stat
import sys
import tempfile
import time as _real_time

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
    def __init__(self, active="stock/model", up=True, paused=False):
        self.active, self.up, self.paused = active, up, paused
        self.log = []

    def switch_model(self, mid):
        self.log.append(("switch_model", mid))
        self.active = mid
        if self.paused:
            self.paused = False
            self.log.append(("RESUMED",))
        self.up = True
        return {"ok": True, "active": mid}

    def activate(self, mid):
        self.log.append(("_activate_model", mid))
        self.active = mid
        return True, ""

    def completion(self, mid, messages, tools=None, max_tokens=0, timeout=0):
        if mid != self.active:
            raise OSError("400 only serves " + self.active)
        return OK_RESP


def load(d, w, boot_checked=True):
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
    if boot_checked:
        ns["_pro_boot_checked"] = True
    path = os.path.join(DASH, "aux_promotion.py")
    exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), ns)
    ns["_pro_completion"] = w.completion
    ns["time"] = FakeClock()
    ns["_pro_parser"] = lambda mid: None
    return ns


def mark_path(d):
    return os.path.join(d, "drill-restore.json")


def test_marker_lifecycle():
    print("\n1. the restore point exists for exactly the swap's duration")
    with tempfile.TemporaryDirectory(prefix="hermes-e103-") as d:
        w = World()
        seen = {}
        ns = load(d, w)
        real_sw = w.switch_model

        def sw(mid):
            if mid == "cand/model":
                p = mark_path(d)
                seen["exists"] = os.path.exists(p)
                if seen["exists"]:
                    seen["mode"] = stat.S_IMODE(os.stat(p).st_mode)
                    seen["rec"] = json.load(open(p))
            return real_sw(mid)
        ns["_pro_orig_switch_model"] = sw
        ns["_PRO_STATE"].update(running="cand/model")
        ns["promotion_drill_run"]("cand/model")
        check("written BEFORE the swap", seen.get("exists") is True, seen)
        check("...0600", seen.get("mode") == 0o600, oct(seen.get("mode") or 0))
        check("...naming the original and the candidate",
              (seen.get("rec") or {}).get("restore_to") == "stock/model"
              and (seen.get("rec") or {}).get("candidate") == "cand/model", seen.get("rec"))
        check("removed once the original is back", not os.path.exists(mark_path(d))
              and w.active == "stock/model", (w.active, os.listdir(d)))

    with tempfile.TemporaryDirectory(prefix="hermes-e103-") as d:
        w = World()
        ns = load(d, w)
        ns["_PRO_STATE"].update(running="stock/model")
        ns["promotion_drill_run"]("stock/model")
        check("drilling the SERVING model writes no restore point",
              not os.path.exists(mark_path(d)), os.listdir(d))


def write_mark(d, orig="stock/model", cand="cand/model"):
    with open(mark_path(d), "w") as f:
        json.dump({"restore_to": orig, "candidate": cand, "at": 1}, f)


def test_boot_recover_online():
    print("\n2. dashboard restarted mid-drill, candidate still loaded and online")
    with tempfile.TemporaryDirectory(prefix="hermes-e103-") as d:
        w = World(active="cand/model", up=True)
        write_mark(d)
        ns = load(d, w)
        rs = ns["_pro_boot_recover"]()
        check("the original is switched back through switch_model",
              ("switch_model", "stock/model") in w.log and w.active == "stock/model", w.log)
        check("the restore point is gone", not os.path.exists(mark_path(d)))
        last = ns["_PRO_STATE"].get("last") or {}
        check("last records the restart, no verdict, and the restore",
              last.get("pass") is None and "restarted" in (last.get("error") or "")
              and (last.get("restore") or {}).get("after_restart") is True, last)


def test_boot_recover_paused():
    print("\n3. ...but the agent is PAUSED: files only, the pause holds")
    with tempfile.TemporaryDirectory(prefix="hermes-e103-") as d:
        w = World(active="cand/model", up=False, paused=True)
        write_mark(d)
        ns = load(d, w)
        ns["_pro_boot_recover"]()
        check("files re-activated, nothing switched or resumed",
              w.log == [("_activate_model", "stock/model")] and w.paused, w.log)
        check("the restore point is gone", not os.path.exists(mark_path(d)))


def test_boot_recover_down():
    print("\n4. ...the model is down (not running): files only, nothing started")
    with tempfile.TemporaryDirectory(prefix="hermes-e103-") as d:
        w = World(active="cand/model", up=False)
        write_mark(d)
        ns = load(d, w)
        rs = ns["_pro_boot_recover"]()
        check("files re-activated, no switch (no model load at dashboard start)",
              w.log == [("_activate_model", "stock/model")] and not w.up, w.log)
        check("recorded as files_only", (rs or {}).get("files_only") is True, rs)


def test_boot_recover_owner_switched():
    print("\n5. ...the owner already switched to another model: nothing touched")
    with tempfile.TemporaryDirectory(prefix="hermes-e103-") as d:
        for up in (True, False):
            w = World(active="third/model", up=up)
            write_mark(d)
            ns = load(d, w)
            rs = ns["_pro_boot_recover"]()
            check("online=%s: no switch, no activation" % up, w.log == [], w.log)
            check("online=%s: skipped=switched, restore point removed" % up,
                  (rs or {}).get("skipped") == "switched" and not os.path.exists(mark_path(d)),
                  rs)


def test_boot_recover_garbage():
    print("\n6. an unreadable / hostile restore point")
    with tempfile.TemporaryDirectory(prefix="hermes-e103-") as d:
        for body in ("not json", json.dumps({"restore_to": 5, "candidate": "x"}),
                     json.dumps(["a"]), json.dumps({"restore_to": "a" * 500, "candidate": "b"})):
            w = World(active="cand/model")
            with open(mark_path(d), "w") as f:
                f.write(body)
            ns = load(d, w)
            rs = ns["_pro_boot_recover"]()
            check("%s...: nothing switched, file removed" % body[:20],
                  w.log == [] and not os.path.exists(mark_path(d)), (w.log, rs))
        w = World(active="cand/model")
        ns = load(d, w)
        check("no restore point -> None, nothing done",
              ns["_pro_boot_recover"]() is None and w.log == [])


def test_load_starts_recovery():
    print("\n7. loading the module with a restore point present starts the recovery")
    with tempfile.TemporaryDirectory(prefix="hermes-e103-") as d:
        w = World(active="cand/model")
        write_mark(d)
        calls = []
        ns = load(d, w, boot_checked=False)
        ns["_pro_boot_recover"] = lambda: calls.append(1)   # looked up after the delay
        deadline = _real_time.time() + ns["PRO_BOOT_DELAY"] + 5
        while not calls and _real_time.time() < deadline:
            _real_time.sleep(0.1)
        check("one recovery ran, after the boot delay", calls == [1], calls)
        check("a re-exec does not start a second one", ns.get("_pro_boot_checked") is True)


def test_owner_switch_retires_restore_point():
    print("\n8. an owner's own switch retires a left-over restore point")
    # review R4: a restore point outlives a boot recovery that could not move
    # the model (Hermes' config write failed, say). An owner who then picks a
    # model on purpose — the candidate included — must not see that choice
    # reverted at some later dashboard start.
    with tempfile.TemporaryDirectory(prefix="hermes-e103-") as d:
        w = World(active="cand/model", up=True)
        write_mark(d)
        ns = load(d, w)
        r = ns["switch_model"]("cand/model")          # the menu, no drill running
        check("the owner's switch answered ok", (r or {}).get("ok") is True, r)
        check("...and removed the restore point", not os.path.exists(mark_path(d)))
        w.log.clear()
        check("a later start restores nothing",
              ns["_pro_boot_recover"]() is None and w.log == [] and w.active == "cand/model",
              w.log)
        write_mark(d)
        ns["_PRO_STATE"].update(running="cand/model")
        ns["switch_model"]("cand/model")              # the drill's own swap
        check("a drill's own switch keeps its restore point", os.path.exists(mark_path(d)))
        ns["_PRO_STATE"].update(running=None)
        w2 = World(active="cand/model", up=True)
        w2.switch_model = lambda mid: {"ok": False, "error": "boom"}
        write_mark(d)
        ns2 = load(d, w2)
        ns2["switch_model"]("third/model")
        check("a switch that failed keeps the restore point", os.path.exists(mark_path(d)))


def main():
    test_owner_switch_retires_restore_point()
    test_marker_lifecycle()
    test_boot_recover_online()
    test_boot_recover_paused()
    test_boot_recover_down()
    test_boot_recover_owner_switched()
    test_boot_recover_garbage()
    test_load_starts_recovery()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
