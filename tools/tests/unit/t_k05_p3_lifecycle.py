#!/usr/bin/env python3
"""Regression tests for the small model-lifecycle items in the 2026-10-08
audit's P3 prose ("Model lifecycle / launchers", docs/audits/
2026-10-08-codebase-audit.md) that sit in the k05 files:

  1. mlx-vlm-launch.py exited 0 on every failure and swallowed the traceback
     (`finally: _hard_exit()` -> os._exit(0)).
  2. IDLE_SUSPEND_MIN=abc raised at import and stopped the dashboard starting.
  3. /api/agent/idle_config (and the persisted minutes file) accepted `inf`.
  4. idle_suspend_loop could write its asleep marker after a pause landed
     mid-tick (the two markers coexisting).
  5. memory_guard_loop's body was unguarded: one exception ended the watchdog.
  6. A download double-click spawned two snapshot_downloads (the "downloading"
     state was set inside the thread).
  7. "Wake now" was a no-op when the model was down WITHOUT the asleep marker.

Everything runs the REAL code (ast extraction / the launcher file itself)
with launchctl, downloads and the model stubbed.
"""
import ast
import os
import subprocess
import sys
import tempfile
import time as _real_time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
SERVER = REPO / "dashboard" / "server.py"
LAUNCHER = REPO / "mlx-vlm-launch.py"

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def extract(names, ns):
    tree = ast.parse(SERVER.read_text())
    found, nodes = set(), []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names:
            nodes.append(n); found.add(n.name)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names
                                               for t in n.targets):
            nodes.append(n); found.update(t.id for t in n.targets if isinstance(t, ast.Name))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SERVER), "exec"), ns)
    return set(names) - found


# --------------------------------------------------------------------------
def test_launcher_exit_status():
    probe = subprocess.run([sys.executable, "-c", "import mlx_vlm"], capture_output=True)
    with tempfile.TemporaryDirectory(prefix="hermes-k05p3-") as d:
        (Path(d) / "boom_mod.py").write_text("raise RuntimeError('boom at import')\n")
        (Path(d) / "exit3_mod.py").write_text("import sys\nsys.exit(3)\n")
        (Path(d) / "ok_mod.py").write_text("print('fine')\n")
        drv = ("import importlib.util, sys\n"
               "spec = importlib.util.spec_from_file_location('L', sys.argv[1])\n"
               "L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)\n"
               "L._run_server_module(sys.argv[2])\n")
        env = dict(os.environ, PYTHONPATH=d + os.pathsep + os.environ.get("PYTHONPATH", ""))

        def run(mod):
            return subprocess.run([sys.executable, "-c", drv, str(LAUNCHER), mod],
                                  capture_output=True, text=True, env=env, timeout=60)
        r = run("boom_mod")
        check("server module crashes at import -> non-zero exit (was 0)", r.returncode == 1,
              (r.returncode, r.stderr[-300:]))
        check("...and the traceback reaches the log (was swallowed by os._exit)",
              "RuntimeError: boom at import" in r.stderr, r.stderr[-300:])
        r = run("exit3_mod")
        check("sys.exit(3) inside the server -> exit 3", r.returncode == 3, r.returncode)
        r = run("ok_mod")
        check("a clean return still exits 0", r.returncode == 0 and "fine" in r.stdout,
              (r.returncode, r.stdout))
        if probe.returncode != 0:
            # this python has no mlx_vlm, so running the launcher can never
            # start a server: it must refuse loudly, not exit 0
            r = subprocess.run([sys.executable, str(LAUNCHER), "--model", "org/x"],
                               capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, HOME=d))
            check("launcher without mlx_vlm: refuses with exit 78 (was 0)",
                  r.returncode == 78, (r.returncode, r.stderr[-300:]))


# --------------------------------------------------------------------------
def test_idle_min_parsing():
    with tempfile.TemporaryDirectory(prefix="hermes-k05p3-") as d:
        ns = {"os": os, "IDLE_MIN_FILE": os.path.join(d, "idle-suspend-min")}
        missing = extract({"IDLE_MIN_CAP", "_idle_min_env", "_idle_min"}, ns)
        check("idle-minutes helpers present", not missing, sorted(missing))
        if missing:
            return
        f = ns["_idle_min_env"]
        check("IDLE_SUSPEND_MIN=abc -> default 10 (was: ValueError at import)", f("abc") == 10.0)
        check("IDLE_SUSPEND_MIN=inf / nan / 0 -> default",
              f("inf") == 10.0 and f("nan") == 10.0 and f("0") == 10.0)
        check("IDLE_SUSPEND_MIN=25 -> 25", f("25") == 25.0)
        src = SERVER.read_text()
        check("the module constant is built through the safe parser",
              'IDLE_SUSPEND_MIN = _idle_min_env(os.environ.get("IDLE_SUSPEND_MIN", "10"))' in src)
        ns["IDLE_SUSPEND_MIN"] = 10.0
        with open(ns["IDLE_MIN_FILE"], "w") as fh:
            fh.write("inf")
        check("a persisted 'inf' is ignored (was: never suspend)", ns["_idle_min"]() == 10.0)
        with open(ns["IDLE_MIN_FILE"], "w") as fh:
            fh.write("20")
        check("a persisted 20 is used", ns["_idle_min"]() == 20.0)
        check("/api/agent/idle_config refuses inf/nan the same way",
              "if 1 <= mins <= IDLE_MIN_CAP:" in src)


# --------------------------------------------------------------------------
class StopLoop(Exception):
    pass


class Ticks:
    def __init__(self, n):
        self.n, self.left = 0, n

    def sleep(self, _s):
        self.n += 1
        if self.n > self.left:
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


def test_pause_mid_tick():
    with tempfile.TemporaryDirectory(prefix="hermes-k05p3-") as d:
        marker = os.path.join(d, "agent-idle-suspended")
        seq = iter([False, True, True, True])        # pause lands after the top check
        ns = {"os": os, "sys": sys, "time": Ticks(1), "subprocess": FakeSub(),
              "IDLE_SUSPEND_FILE": marker, "MLX_LABEL": "x", "_widget_cache": {},
              "_down_seen_at": [0.0], "_DOWN_PERSIST_S": 60, "_idle_bootout_warned": [0.0],
              "idle_suspend_enabled": lambda: True, "agent_paused": lambda: next(seq),
              "agent_idle_suspended": lambda: os.path.exists(marker),
              "model_online": lambda: True, "_chat_jobs_active": lambda: False,
              "_newest_external_turn_ts": lambda: 0.0, "_last_user_activity": 0.0,
              "_idle_min": lambda: 10.0, "_mlx_proc_alive": lambda: True,
              "_start_token_fresh": lambda: False, "agent_wake": lambda **k: None,
              "_mlx_primary_down": lambda timeout=3.0: True, "_model_held": lambda: False}
        extract({"idle_suspend_loop"}, ns)
        try:
            ns["idle_suspend_loop"]()
        except StopLoop:
            pass
        check("a pause that lands mid-tick: no asleep marker written over it",
              not os.path.exists(marker))


def test_memory_guard_survives():
    calls = []

    def paused():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("launchctl print exploded")
        return True

    ns = {"sys": sys, "time": Ticks(2), "agent_paused": paused,
          "agent_idle_suspended": lambda: False, "_mlx_footprint_gb": lambda: 0,
          "MLX_HARD_GB": 56, "_mlx_restart": lambda: True, "_widget_cache": {}}
    extract({"memory_guard_loop"}, ns)
    try:
        ns["memory_guard_loop"]()
        died = False
    except StopLoop:
        died = False
    except Exception:
        died = True
    check("memory_guard_loop survives a failing tick and keeps ticking (was: thread died)",
          not died and len(calls) >= 2, (died, calls))


def test_download_double_click():
    import threading
    started = []

    class FakeThreading:
        Lock = threading.Lock

        class Thread:
            def __init__(self, target=None, daemon=None, **kw):
                self.t = target

            def start(self):
                started.append(self.t)          # never runs: still "downloading"

    ns = {"json": __import__("json"), "sys": sys, "os": os, "subprocess": FakeSub(),
          "threading": FakeThreading, "_model_entry": lambda m: {"id": m},
          "_model_dl": {}, "_model_dl_err": {}, "_model_dl_lock": threading.Lock(),
          "_model_download_gb": lambda e: None, "_disk_free_gb": lambda: None,
          "_disk_short": lambda a, b: False, "DISK_HEADROOM_GB": 5.0,
          "_hf_python": lambda: "/fake/python", "_hermes_env": lambda: {},
          "_model_downloaded": lambda m: True, "_draft_ready": lambda e: True,
          "_dl_ignore_patterns": lambda ent, extra=None: list(extra or [])}
    extract({"download_model"}, ns)
    r1 = ns["download_model"]("org/m")
    r2 = ns["download_model"]("org/m")
    check("download double-click: one snapshot_download thread, not two",
          len(started) == 1 and r1.get("ok") and r2.get("status") == "downloading",
          (len(started), r1, r2))


def test_wake_now():
    src = SERVER.read_text()
    check("'Wake now' also wakes a model that is down without the marker",
          "if agent_idle_suspended() or (not agent_paused() and not model_online()):" in src)


def main():
    test_launcher_exit_status()
    test_idle_min_parsing()
    test_pause_mid_tick()
    test_memory_guard_survives()
    test_download_double_click()
    test_wake_now()
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
