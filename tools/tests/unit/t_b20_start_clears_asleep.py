#!/usr/bin/env python3
"""Regression test for B20 (docs/audits/2026-10-08-codebase-audit.md).

BEFORE: switching model (switch_model: bootout -> _mlx_start) or "Free memory"
(_mem_free_run -> _mlx_restart -> _mlx_start) while the model was
idle-suspended started the server but left ~/.hermes/dashboard/
agent-idle-suspended on disk. With that marker over a RUNNING model,
memory_guard_loop skips every tick and idle_suspend_loop only looks for a
Telegram turn — neither safety loop ever runs again for that process.

AFTER: _mlx_start() removes the marker whenever it actually started the
server (and only then), which covers every start path.

ast-extracts the REAL _mlx_start/_mlx_restart from dashboard/server.py and
runs them with launchctl stubbed. Nothing is started.
"""
import ast
import os
import sys
import tempfile
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


class _R:
    def __init__(self, rc=0, err=""):
        self.returncode, self.stdout, self.stderr = rc, "", err


class FakeLaunchctl:
    def __init__(self, boot=0, kick=0, loaded=False):
        self.boot, self.kick, self.loaded, self.calls = boot, kick, loaded, []

    def run(self, argv, **kw):
        self.calls.append(argv[1] if len(argv) > 1 else argv[0])
        if argv[:2] == ["launchctl", "bootstrap"]:
            return _R(self.boot, "Bootstrap failed: 5: Input/output error" if self.boot else "")
        if argv[:2] == ["launchctl", "kickstart"]:
            return _R(self.kick, "kickstart failed" if self.kick else "")
        if argv[:2] == ["launchctl", "print"]:
            return _R(0 if self.loaded else 113, "" if self.loaded else "Could not find service")
        return _R(0)


class FakeTime:
    @staticmethod
    def time():
        import time
        return time.time()

    @staticmethod
    def sleep(_s):
        return None


def load(d, fake):
    tree = ast.parse(SERVER.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in ("_mlx_start", "_mlx_restart")]
    ns = {"os": os, "sys": sys, "time": FakeTime, "subprocess": fake,
          "MODEL_START_TOKEN": os.path.join(d, "model-start-ok"),
          "IDLE_SUSPEND_FILE": os.path.join(d, "agent-idle-suspended"),
          "MLX_PLIST": os.path.join(d, "com.hermes.mlx-server.plist"),
          "MLX_LABEL": "com.hermes.mlx-server"}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SERVER), "exec"), ns)
    return ns


def asleep(ns):
    with open(ns["IDLE_SUSPEND_FILE"], "w") as f:
        f.write("1700000000")


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-b20-") as d:
        ns = load(d, FakeLaunchctl())
        asleep(ns)
        ok = ns["_mlx_start"](501)
        check("a successful start returns True", ok is True)
        check("...and clears the idle-suspend marker (was: left over a running model)",
              not os.path.exists(ns["IDLE_SUSPEND_FILE"]))
        check("...and minted the start token", os.path.exists(ns["MODEL_START_TOKEN"]))

        ns = load(d, FakeLaunchctl(boot=5))
        asleep(ns)
        check("bootstrap refused twice -> False", ns["_mlx_start"](501) is False)
        check("...and the model stays marked asleep (nothing started)",
              os.path.exists(ns["IDLE_SUSPEND_FILE"]))

        # recent macOS: bootstrap of an ALREADY-loaded (stopped) job is error 5
        # with no "already" in it — the job is there, so it must be kickstarted
        fake = FakeLaunchctl(boot=5, loaded=True)
        ns = load(d, fake)
        asleep(ns)
        check("bootstrap error 5 on a job that IS loaded -> kickstarted, True",
              ns["_mlx_start"](501) is True and "kickstart" in fake.calls, fake.calls)
        check("...and the asleep marker is cleared (it started)",
              not os.path.exists(ns["IDLE_SUSPEND_FILE"]))

        ns = load(d, FakeLaunchctl(kick=1))
        asleep(ns)
        check("kickstart failure -> False", ns["_mlx_start"](501) is False)
        check("...marker kept", os.path.exists(ns["IDLE_SUSPEND_FILE"]))

        # the "Free memory" path: _mem_free_run -> _mlx_restart -> _mlx_start
        fake = FakeLaunchctl()
        ns = load(d, fake)
        asleep(ns)
        ok = ns["_mlx_restart"]()
        check("_mlx_restart (Free memory) while asleep: started",
              ok is True and fake.calls[:1] == ["bootout"], fake.calls)
        check("...and no asleep marker is left over the running model",
              not os.path.exists(ns["IDLE_SUSPEND_FILE"]))
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
