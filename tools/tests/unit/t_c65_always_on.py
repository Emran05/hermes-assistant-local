#!/usr/bin/env python3
"""Regression test for audit finding C65 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: HERMES_MODEL_ALWAYS_ON=1 (install-services.sh bakes it into the model
plist and remembers it in ~/.hermes/dashboard/model-always-on) only let
mlx-server.sh's on-demand gate pass a launch-time kickstart. The dashboard
ignored it: at dashboard start a down model was marked asleep (so nothing
started it), and idle_suspend_loop booted it out after ten idle minutes —
the opposite of "always on".

AFTER: server.py's model_always_on() reads the marker (or the env var);
idle_suspend_enabled() is False under it, and at dashboard start a down,
un-paused model is STARTED instead of being marked asleep
(_boot_model_state). A deliberate pause still wins.

ast-extracts the REAL functions (+ one tick of idle_suspend_loop) from
dashboard/server.py with launchctl and the probes stubbed. Nothing starts.
"""
import ast
import os
import sys
import tempfile
import time as _real_time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
SERVER = REPO / "dashboard" / "server.py"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


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


class FakeThreading:
    def __init__(self):
        self.targets = []

    def Thread(self, target=None, daemon=None, **kw):
        fake = self

        class T:
            def start(self_inner):
                fake.targets.append(target)
        return T()


def make_ns(d, paused=False, online=False):
    starts = []
    ns = {"os": os, "sys": sys, "time": OneTick(), "subprocess": FakeSub(),
          "threading": FakeThreading(), "DATA": d,
          "IDLE_SUSPEND_FILE": os.path.join(d, "agent-idle-suspended"),
          "IDLE_SUSPEND_OFF": os.path.join(d, "idle-suspend-off"),
          "MODEL_ALWAYS_ON_FILE": os.path.join(d, "model-always-on"),
          "MLX_LABEL": "com.hermes.mlx-server", "_widget_cache": {},
          "_down_seen_at": [0.0], "_DOWN_PERSIST_S": 60, "_idle_bootout_warned": [0.0],
          "agent_paused": lambda: paused, "model_online": lambda: online,
          "_chat_jobs_active": lambda: False, "_newest_external_turn_ts": lambda: 0.0,
          "_last_user_activity": 0.0, "_idle_min": lambda: 10.0,
          "_mlx_proc_alive": lambda: True, "_start_token_fresh": lambda: False,
          "agent_wake": lambda **k: None, "_mlx_primary_down": lambda timeout=3.0: True,
          "_model_held": lambda: False,
          "_mlx_start": lambda uid=None: starts.append(1) or True,
          "_lifecycle_lock": __import__("threading").Lock()}   # E98
    ns["agent_idle_suspended"] = lambda: os.path.exists(ns["IDLE_SUSPEND_FILE"])
    tree = ast.parse(SERVER.read_text())
    want = {"model_always_on", "idle_suspend_enabled", "_boot_model_state", "idle_suspend_loop"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SERVER), "exec"), ns)
    return ns, starts, want - {n.name for n in nodes}


def tick(ns):
    ns["time"] = OneTick()
    ns["subprocess"] = FakeSub()
    try:
        ns["idle_suspend_loop"]()
    except StopLoop:
        pass
    return [c for c in ns["subprocess"].calls if c[:2] == ["launchctl", "bootout"]]


def main():
    os.environ.pop("HERMES_MODEL_ALWAYS_ON", None)
    with tempfile.TemporaryDirectory(prefix="hermes-c65-") as d:
        ns, starts, missing = make_ns(d, online=True)
        check("server.py always-on helpers present", not missing, sorted(missing))
        check("default (on-demand): idle-suspend enabled", ns["idle_suspend_enabled"]())
        check("default: an idle model is suspended", tick(ns))
        os.remove(ns["IDLE_SUSPEND_FILE"])
        open(ns["MODEL_ALWAYS_ON_FILE"], "w").close()
        check("always-on: idle-suspend reports disabled", not ns["idle_suspend_enabled"]())
        check("always-on: an hour idle and the model is NOT booted out "
              "(was: suspended after ten minutes)", not tick(ns))
        if not missing:
            check("always-on marker -> model_always_on()", ns["model_always_on"]())
            os.remove(ns["MODEL_ALWAYS_ON_FILE"])
            os.environ["HERMES_MODEL_ALWAYS_ON"] = "1"
            check("the env var means the same on a hand-run dashboard", ns["model_always_on"]())
            os.environ.pop("HERMES_MODEL_ALWAYS_ON", None)

    with tempfile.TemporaryDirectory(prefix="hermes-c65-") as d:
        ns, starts, missing = make_ns(d, online=False)
        if not missing:
            r = ns["_boot_model_state"]()
            check("on-demand boot: a down model is marked asleep (unchanged)",
                  r == "asleep" and os.path.exists(ns["IDLE_SUSPEND_FILE"])
                  and not ns["threading"].targets)
            os.remove(ns["IDLE_SUSPEND_FILE"])
            open(ns["MODEL_ALWAYS_ON_FILE"], "w").close()
            r = ns["_boot_model_state"]()
            check("always-on boot: the model is STARTED with the dashboard, not marked asleep",
                  r == "starting" and ns["threading"].targets == [ns["_mlx_start"]]
                  and not os.path.exists(ns["IDLE_SUSPEND_FILE"]), r)
    with tempfile.TemporaryDirectory(prefix="hermes-c65-") as d:
        ns, starts, missing = make_ns(d, paused=True, online=False)
        if not missing:
            open(ns["MODEL_ALWAYS_ON_FILE"], "w").close()
            r = ns["_boot_model_state"]()
            check("always-on but deliberately paused: left down", r is None
                  and not ns["threading"].targets)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
