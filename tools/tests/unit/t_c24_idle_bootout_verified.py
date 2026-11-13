#!/usr/bin/env python3
"""Regression test for audit finding C24 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: idle_suspend_loop ran `launchctl bootout`, ignored the result, wrote
~/.hermes/dashboard/agent-idle-suspended and logged "suspended model server
(freed its RAM)". When the bootout did not take, the marker sat over a
running model: memory_guard skips while it exists and the idle loop itself
only watches for a Telegram turn — both safety loops off, RAM never freed.

AFTER: the loop checks the end state the way a pause does
(_mlx_primary_down: launchctl print + :8080, unknown = still up) and marks the
model asleep only when it is really gone; otherwise one throttled log line and
a retry on the next tick.

ast-extracts the REAL idle_suspend_loop from dashboard/server.py and runs ONE
tick of it with launchctl / the probes stubbed. Nothing is stopped or started.
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
    """time stand-in: the loop's first sleep returns, the second ends it."""
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
    def __init__(self, rc, err=""):
        self.returncode, self.stdout, self.stderr = rc, "", err


class FakeSub:
    def __init__(self, rc):
        self.rc, self.calls = rc, []

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        return _R(self.rc, "Boot-out failed: 5: Input/output error" if self.rc else "")


def one_tick(d, bootout_rc, really_down, extra=None):
    tree = ast.parse(SERVER.read_text())
    want = {"idle_suspend_loop"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want]
    marker = os.path.join(d, "agent-idle-suspended")
    if os.path.exists(marker):
        os.remove(marker)
    sub = FakeSub(bootout_rc)
    ns = {"os": os, "sys": sys, "time": OneTick(), "subprocess": sub,
          "IDLE_SUSPEND_FILE": marker, "MLX_LABEL": "com.hermes.mlx-server",
          "_widget_cache": {}, "_down_seen_at": [0.0], "_DOWN_PERSIST_S": 60,
          "_idle_bootout_warned": [0.0],
          "idle_suspend_enabled": lambda: True, "agent_paused": lambda: False,
          "agent_idle_suspended": lambda: os.path.exists(marker),
          "model_online": lambda: True, "_chat_jobs_active": lambda: False,
          "_newest_external_turn_ts": lambda: 0.0, "_last_user_activity": 0.0,
          "_idle_min": lambda: 10.0, "_mlx_proc_alive": lambda: True,
          "_start_token_fresh": lambda: False, "agent_wake": lambda **k: None,
          "_mlx_primary_down": lambda timeout=3.0: really_down,
          "_model_held": lambda: False,
          # E98: the bootout runs under the lifecycle lock
          "_lifecycle_lock": __import__("threading").Lock()}
    ns.update(extra or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SERVER), "exec"), ns)
    try:
        ns["idle_suspend_loop"]()
    except StopLoop:
        pass
    return os.path.exists(marker), sub.calls


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-c24-") as d:
        marked, calls = one_tick(d, bootout_rc=5, really_down=False)
        check("idle for an hour: the loop tried a bootout",
              any(c[:2] == ["launchctl", "bootout"] for c in calls), calls)
        check("bootout failed and the server is still up -> NOT marked asleep "
              "(was: marker over a running model)", not marked)
        marked, _ = one_tick(d, bootout_rc=0, really_down=False)
        check("bootout rc=0 but the server still answers -> not marked asleep", not marked)
        marked, _ = one_tick(d, bootout_rc=0, really_down=True)
        check("bootout took and the server is gone -> marked asleep", marked)
        marked, _ = one_tick(d, bootout_rc=3, really_down=True)
        check("rc=3 'no such process' but gone -> marked asleep (same rule as pause)", marked)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
