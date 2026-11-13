#!/usr/bin/env python3
"""Regression test for audit finding C26 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: switch_model wrote active-model, called `hermes config set
model.default` inside `try/except: pass` without looking at the exit code,
and _write_template_args swallowed every OSError — then restarted the server
and answered {"ok": true, "loading": true}. A failed config set left Hermes
asking for the OLD model while the server loaded the new one (with the C130
pin that is a refusal on every turn); a failed server-backend write started
the new model with the old backend/drafter/thinking.

AFTER: _activate_model() writes active-model, chat-template-args +
server-backend, then model.default — all three or none. Any failure puts the
first two back, restarts nothing, and switch_model answers ok:false with the
reason. _write_template_args reports whether both files were written.

ast-extracts the REAL functions from dashboard/server.py; launchctl and the
hermes CLI are stubbed. Nothing is started.
"""
import ast
import json
import os
import re
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
    def __init__(self, rc, err=""):
        self.returncode, self.stdout, self.stderr = rc, "", err


class FakeSub:
    def __init__(self, config_rc=0):
        self.config_rc, self.calls = config_rc, []

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[1:3] == ["config", "set"]:
            return _R(self.config_rc, "Error: config.yaml is read-only" if self.config_rc else "")
        return _R(0)


class FakeTime:
    @staticmethod
    def sleep(_s):
        return None

    @staticmethod
    def time():
        import time
        return time.time()


ROSTER = {
    "org/old": {"id": "org/old", "backend": "mlx_vlm", "template_args": {"enable_thinking": False},
                "draft_model": "org/old-drafter", "draft_kind": "mtp"},
    "org/new": {"id": "org/new", "backend": "mlx_lm", "template_args": {"enable_thinking": True}},
}


def make_ns(d, sub, backend_path=None):
    venv_py = os.path.join(d, "python")
    open(venv_py, "w").close()
    starts = []
    ns = {"os": os, "sys": sys, "json": json, "re": re, "time": FakeTime, "subprocess": sub,
          "HOME": d, "HERMES": "hermes", "_hermes_env": lambda: {},
          "DEFAULT_MODEL": "org/default", "ACTIVE_MODEL_FILE": os.path.join(d, "active-model"),
          "TEMPLATE_ARGS_FILE": os.path.join(d, "chat-template-args"),
          "SERVER_BACKEND_FILE": backend_path or os.path.join(d, "server-backend"),
          "MLX_VLM_VENV_PY": venv_py, "MLX_LABEL": "com.hermes.mlx-server",
          "_model_entry": lambda m: ROSTER.get(m), "_draft_model_path": lambda m: m.get("draft_model"),
          "_model_downloaded": lambda m: True, "_draft_ready": lambda e: True,
          "agent_paused": lambda: False, "agent_power": lambda a: {"ok": True},
          "_mlx_start": lambda uid=None: starts.append(uid) or True, "_widget_cache": {},
          # E21/E101: switch_model counts as owner activity and restarts under
          # the lifecycle lock
          "note_user_activity": lambda: None,
          "_lifecycle_lock": __import__("threading").Lock()}
    tree = ast.parse(SERVER.read_text())
    want = {"_write_template_args", "_set_hermes_default_model", "_activate_model",
            "switch_model", "active_model", "_config_model_default"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SERVER), "exec"), ns)
    return ns, starts


def seed_old(ns):
    with open(ns["ACTIVE_MODEL_FILE"], "w") as f:
        f.write("org/old")
    ns["_write_template_args"]("org/old")


def read(p):
    try:
        return open(p).read()
    except OSError:
        return None


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-c26-") as d:
        sub = FakeSub(config_rc=1)
        ns, starts = make_ns(d, sub)
        seed_old(ns)
        backend_before = read(ns["SERVER_BACKEND_FILE"])
        args_before = read(ns["TEMPLATE_ARGS_FILE"])
        r = ns["switch_model"]("org/new")
        check("hermes config set fails -> switch answers ok:false (was: ok/loading)",
              not r.get("ok"), r)
        check("...with the reason", "model.default" in str(r.get("error")), r)
        check("...active-model rolled back", read(ns["ACTIVE_MODEL_FILE"]) == "org/old")
        check("...server-backend + chat-template-args rolled back",
              read(ns["SERVER_BACKEND_FILE"]) == backend_before
              and read(ns["TEMPLATE_ARGS_FILE"]) == args_before)
        check("...and the model server was not restarted",
              not starts and not any(c[:2] == ["launchctl", "bootout"] for c in sub.calls),
              sub.calls)

    with tempfile.TemporaryDirectory(prefix="hermes-c26-") as d:
        bad = os.path.join(d, "server-backend-is-a-dir")
        os.mkdir(bad)
        sub = FakeSub(config_rc=0)
        ns, starts = make_ns(d, sub, backend_path=bad)
        with open(ns["ACTIVE_MODEL_FILE"], "w") as f:
            f.write("org/old")
        check("_write_template_args reports a failed server-backend write",
              ns["_write_template_args"]("org/new") is False)
        r = ns["switch_model"]("org/new")
        check("server-backend unwritable -> switch ok:false", not r.get("ok"), r)
        check("...hermes model.default never touched",
              not any(c[1:3] == ["config", "set"] for c in sub.calls), sub.calls)
        check("...active-model rolled back", read(ns["ACTIVE_MODEL_FILE"]) == "org/old")
        check("...nothing restarted", not starts)

    with tempfile.TemporaryDirectory(prefix="hermes-c26-") as d:
        # review: a template-args WRITE that fails with FileNotFoundError (its
        # directory is gone) was swallowed as "nothing to remove" and
        # reported True; only the remove branch may treat a missing file as ok
        sub = FakeSub(config_rc=0)
        ns, starts = make_ns(d, sub)
        ns["TEMPLATE_ARGS_FILE"] = os.path.join(d, "gone", "chat-template-args")
        check("_write_template_args reports a failed chat-template-args write "
              "(missing directory)", ns["_write_template_args"]("org/new") is False)
        check("...while a model with NO template args (remove of an absent "
              "file) still reports True",
              ns["_write_template_args"]("org/none") is True)

    with tempfile.TemporaryDirectory(prefix="hermes-c26-") as d:
        sub = FakeSub(config_rc=0)
        ns, starts = make_ns(d, sub)
        seed_old(ns)
        r = ns["switch_model"]("org/new")
        check("all writes succeed -> ok/loading", r.get("ok") and r.get("loading"), r)
        check("...active-model, server-backend and model.default all name the new model",
              read(ns["ACTIVE_MODEL_FILE"]) == "org/new"
              and json.loads(read(ns["SERVER_BACKEND_FILE"]))["backend"] == "mlx_lm"
              and ["hermes", "config", "set", "model.default", "org/new"] in sub.calls, sub.calls)
        check("...and the server was restarted", starts and
              any(c[:2] == ["launchctl", "bootout"] for c in sub.calls))
        check("_write_template_args returns True on success", ns["_write_template_args"]("org/new") is True)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
