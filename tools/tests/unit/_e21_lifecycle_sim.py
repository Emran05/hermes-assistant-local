"""Fixture for t_e21_* / t_e98_* / t_e101_*: a fake launchd + fake clock around
the REAL dashboard/server.py model-lifecycle functions (ast-extracted, nothing
else of server.py runs). The fake `kickstart` applies mlx-server.sh's start
token rule. Derived from the 2026-11-09 audit's model-lifecycle sim.

Not a suite (no `t_` prefix): the runner never executes it on its own.
"""
import ast
import os
import re
import sys
import threading
import types
import uuid
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
SRC_PATH = os.environ.get("HERMES_T_SERVER_SRC") or os.path.join(REPO, "dashboard", "server.py")

NAMES = {"idle_suspend_loop", "agent_power", "_mlx_start", "_mlx_restart", "agent_wake",
         "_mlx_primary_down", "agent_paused", "agent_idle_suspended", "idle_suspend_enabled",
         "model_always_on", "_idle_min", "note_user_activity", "_model_held",
         "_chat_jobs_active", "_start_token_fresh", "switch_model", "_mem_free_run",
         "set_model_thinking", "memory_guard_loop", "model_thinking_state"}

T0 = 1_000_000.0


class Stop(BaseException):
    pass


class World:
    def __init__(self, home):
        self.t = T0
        self.loaded = True       # launchd job bootstrapped
        self.running = False     # process alive
        self.online_at = None    # when /v1/models starts answering
        self.log = []
        self.hooks = []          # (at_time, fn) run when the clock passes at_time
        self.home = home
        self.load_s = 40.0
        self.starts = 0

    def now(self):
        return self.t

    def sleep(self, s):
        end = self.t + s
        while True:
            due = sorted([h for h in self.hooks if h[0] <= end], key=lambda h: h[0])
            if not due:
                break
            h = due[0]
            self.hooks.remove(h)
            self.t = max(self.t, h[0])
            h[1]()
        self.t = end

    def at(self, dt, fn):
        self.hooks.append((self.t + dt, fn))

    def online(self):
        return self.running and self.online_at is not None and self.t >= self.online_at

    def say(self, msg):
        self.log.append("t+%7.1fs  %s" % (self.t - T0, msg))

    def run(self, argv, **kw):
        R = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[0] == "launchctl":
            verb = argv[1]
            if verb == "bootout":
                if not self.loaded:
                    R.returncode = 3
                    R.stderr = "No such process"
                if self.running:
                    self.say("launchd: model server KILLED (bootout)")
                self.loaded = False
                self.running = False
                self.online_at = None
            elif verb == "bootstrap":
                if self.loaded:
                    R.returncode = 5
                    R.stderr = "Bootstrap failed: 5: Input/output error"
                self.loaded = True
            elif verb == "kickstart":
                if not self.loaded:
                    R.returncode = 113
                    R.stderr = "Could not find service"
                elif not self.running:
                    tok = os.path.join(self.home, "model-start-ok")
                    if os.path.exists(tok) and self.t - float(open(tok).read()) <= 180:
                        os.remove(tok)
                        self.running = True
                        self.starts += 1
                        self.online_at = self.t + self.load_s
                        self.say("launchd: model server STARTED")
                    else:
                        self.say("launchd: start refused by the on-demand gate")
            elif verb == "print":
                R.returncode = 0 if self.loaded else 113
        elif argv[0] == "pgrep":
            R.stdout = "123\n" if self.running else ""
            R.returncode = 0 if self.running else 1
        elif argv[0] == "footprint":
            R.stdout = "phys_footprint: 20 GB"
        return R


def build(home, idle_min=10, extra_ns=None):
    W = World(home)
    faketime = types.SimpleNamespace(time=W.now, sleep=W.sleep, monotonic=W.now)

    class _P:
        def __getattr__(self, k):
            return getattr(os.path, k)

        @staticmethod
        def getmtime(p):
            if p.endswith("model-start-ok"):
                return float(open(p).read())
            return os.path.getmtime(p)

    class _FakeOS:
        def __getattr__(self, k):
            return getattr(os, k)
    fos = _FakeOS()
    fos.path = _P()
    fos.getuid = lambda: 501
    reg = [{"id": "stock/model", "thinking": True, "template_args": {}},
           {"id": "new/model", "thinking": True, "template_args": {}}]
    state = {"active": "stock/model", "writes": []}

    def _activate_model(mid):
        state["active"] = mid
        return True, ""

    ns = {"os": fos, "sys": sys, "time": faketime, "threading": threading, "uuid": uuid,
          "re": re, "json": json, "subprocess": types.SimpleNamespace(run=W.run),
          "IDLE_SUSPEND_FILE": os.path.join(home, "agent-idle-suspended"),
          "IDLE_SUSPEND_OFF": os.path.join(home, "idle-suspend-off"),
          "IDLE_MIN_FILE": os.path.join(home, "idle-suspend-min"), "IDLE_MIN_CAP": 10080,
          "IDLE_SUSPEND_MIN": float(idle_min),
          "MODEL_ALWAYS_ON_FILE": os.path.join(home, "model-always-on"),
          "MODEL_START_TOKEN": os.path.join(home, "model-start-ok"),
          "PAUSE_FILE": os.path.join(home, "agent-paused"), "MLX_LABEL": "com.hermes.mlx-server",
          "MLX_PLIST": "/x.plist", "_lifecycle_lock": threading.Lock(),
          "_DOWN_PERSIST_S": 60, "_down_seen_at": [0.0], "_idle_bootout_warned": [0.0],
          "_last_user_activity": W.now(), "_model_holds": {}, "_model_holds_lock": threading.Lock(),
          "CHAT_JOBS": {}, "_jobs_lock": threading.Lock(), "_widget_cache": {},
          "model_online": W.online, "_newest_external_turn_ts": lambda: 0.0,
          "_mlx_proc_alive": lambda: W.running, "_prewarm_kick": lambda *a, **k: None,
          "_mem_free_state": {"running": True}, "_mem_free_lock": threading.Lock(),
          "MLX_HARD_GB": 56.0, "_mlx_footprint_gb": lambda: 20,
          "_model_entry": lambda mid: next((m for m in reg if m["id"] == mid), None),
          "_model_registry": lambda: reg, "_model_downloaded": lambda mid: True,
          "_draft_ready": lambda m: True, "_activate_model": _activate_model,
          "active_model": lambda: state["active"],
          "write_json": lambda p, d: state["writes"].append(p),
          "models_update": lambda fn: (None if fn(reg) is False else reg),
          "MODELS_FILE": os.path.join(home, "models.json"),
          "_write_template_args": lambda mid: True}
    if extra_ns:
        ns.update(extra_ns)
    tree = ast.parse(open(SRC_PATH, encoding="utf-8").read(), filename=SRC_PATH)
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    exec(compile(ast.Module(body=body, type_ignores=[]), SRC_PATH, "exec"), ns)
    W.ns = ns
    W.state = state
    return W, ns


def run_idle_loop(W, ns, until_dt):
    """Run the real idle_suspend_loop until the fake clock passes now+until_dt."""
    W.at(until_dt, lambda: (_ for _ in ()).throw(Stop()))
    try:
        ns["idle_suspend_loop"]()
    except Stop:
        pass
