#!/usr/bin/env python3
"""R2-20 — the unattended web-research pass never queues on _agent_lock.

BEFORE (k02): _intel_run_agent called server.py's run_agent(..., toolsets=)
whenever it took that keyword. run_agent holds _agent_lock for up to
AGENT_TIMEOUT (600 s), so the background pass serialised with every other
one-shot (briefing, Claude escalation fallbacks …) and could hold them for
ten minutes instead of the pass's own 180 s.
AFTER: always the module's own direct `hermes <lane> --toolsets search -z`
with a 180 s timeout — the explicit web_search-only toolset is unchanged.

Drives the real _intel_agent_pass (see _wt_harness.py) with the REAL
server.py run_agent (lifted by AST) wired in beside it; subprocess.run is a
recorder, so nothing runs.
"""
import ast
import io
import json
import os
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wt_harness as H  # noqa: E402

check = H.check
OUT = json.dumps([{"title": "Found", "url": "https://example.org/a", "source": "web", "why": "w"}])


class P:
    def __init__(self, out):
        self.stdout, self.stderr, self.returncode = out, "", 0


def real_run_agent(g, lock):
    """server.py's own run_agent + _toolsets_arg, bound to a lock we watch."""
    path = os.path.join(H.DASH, "server.py")
    tree = ast.parse(io.open(path, encoding="utf-8").read(), path)
    want = {"run_agent", "_toolsets_arg", "_TOOLSET_NAME_RE", "UNATTENDED_NO_TOOLS"}
    keep = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in want)
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in want
                                                  for t in n.targets))]
    import re
    ns = {"re": re, "os": os, "sys": sys, "subprocess": subprocess,
          "HERMES": g["HERMES"], "AGENT_TIMEOUT": 600, "_agent_lock": lock,
          "_hermes_env": lambda: {}, "bg_lane": g["bg_lane"]}
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), ns)
    return ns["run_agent"]


class WatchedLock(object):
    def __init__(self):
        self._l, self.taken = threading.Lock(), 0

    def __enter__(self):
        self.taken += 1
        return self._l.__enter__()

    def __exit__(self, *a):
        return self._l.__exit__(*a)


def main():
    print("R2-20: the research pass runs directly, never under _agent_lock")
    g, _home, data = H.load()
    H.wj(os.path.join(data, "watchtower.json"),
         {"master": {"briefings": True, "news": True}, "web_research": {"enabled": True}})
    g["_intel_web_ok"] = lambda: True
    g["bg_lane"] = lambda: {"lane": "bg", "hermes_args": ["--provider", "custom:bg", "-m", "bg"]}
    lock = WatchedLock()
    g["run_agent"] = real_run_agent(g, lock)

    calls = []
    orig = subprocess.run

    def fake(argv, *a, **k):
        calls.append((list(argv), k))
        return P(OUT)
    subprocess.run = fake
    try:
        items = g["_intel_agent_pass"]()
    finally:
        subprocess.run = orig
    argv, kw = calls[-1] if calls else ([], {})
    check("one hermes run", len(calls) == 1, calls)
    check("THE FIX: server.py's _agent_lock is never taken", lock.taken == 0, lock.taken)
    check("... the run keeps its own 180 s cap (not AGENT_TIMEOUT's 600)",
          kw.get("timeout") == 180, kw.get("timeout"))
    check("... with the explicit web_search-only toolset before -z",
          "--toolsets" in argv and argv[argv.index("--toolsets") + 1] == "search"
          and argv.index("--toolsets") < argv.index("-z"), argv)
    check("... on the background lane",
          argv[1:5] == ["--provider", "custom:bg", "-m", "bg"], argv)
    check("the items still reach the caller",
          [i.get("url") for i in items] == ["https://example.org/a"], items)
    H.finish()


if __name__ == "__main__":
    main()
