#!/usr/bin/env python3
"""E106 (2026-11-09 audit, P3): a huge whole number in a roster `sampling`
value is dropped, not raised — in BOTH validator copies.

BEFORE: server.py's _roster_sampling and mlx-vlm-launch.py's _clean_sampling
did `float(x)` unguarded, so models.json (hand-editable, importable) holding
e.g. {"temperature": 10**400} raised OverflowError — and models_payload()
runs it for every row, so GET /api/models (the whole model menu) failed.
Both docstrings promise an out-of-range number is DROPPED.

AFTER: OverflowError drops the value in both copies; they still agree
(t_r3_roster_launcher keeps the drift guard on the ordinary cases).

REAL functions: server.py's by ast, the launcher imported by path (stdlib).
"""
import ast
import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %r" % (name, extra))


def server_fn():
    src = open(os.path.join(REPO, "dashboard", "server.py"), encoding="utf-8").read()
    want = {"_ROSTER_SAMPLING_BOUNDS", "_roster_sampling", "_roster_badges", "_roster_subfolder",
            "_ROSTER_SUBFOLDER_RE", "_roster_license", "_ROSTER_LICENSE_RE"}
    nodes = [n for n in ast.parse(src).body
             if (isinstance(n, ast.FunctionDef) and n.name in want)
             or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in want
                                                   for t in n.targets))]
    ns = {"re": re, "_roster_uncensored": lambda m: False}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "server.py", "exec"), ns)
    return ns


def launcher():
    path = os.path.join(REPO, "mlx-vlm-launch.py")
    spec = importlib.util.spec_from_file_location("mlx_vlm_launch_e106", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ns = server_fn()
    L = launcher()
    big = 10 ** 400
    cases = [{"temperature": big}, {"top_p": -big}, {"repetition_penalty": big},
             {"presence_penalty": big, "temperature": 0.7},
             {"top_k": big}, {"repetition_context_size": big}, {"min_p": 2 ** 1100}]
    for v in cases:
        for label, fn in (("server.py _roster_sampling", ns["_roster_sampling"]),
                          ("launcher _clean_sampling", L._clean_sampling)):
            try:
                out = fn(v)
                err = None
            except Exception as e:
                out, err = None, "%s: %s" % (type(e).__name__, str(e)[:60])
            keys = sorted(v)
            check("%s %s=<huge>: dropped, no raise" % (label, keys[0]),
                  err is None and all(k not in out for k in keys if k != "temperature"
                                      or v[k] == big), (out, err))
        try:
            same = ns["_roster_sampling"](v) == L._clean_sampling(v)
        except Exception as e:
            same = False
        check("both copies agree for %s" % sorted(v), same)
    try:
        nb = ns["_roster_sampling"]({"presence_penalty": big, "temperature": 0.7})
    except Exception as e:
        nb = repr(e)
    check("the valid neighbour survives: temperature 0.7 kept beside a huge penalty",
          nb == {"temperature": 0.7}, nb)
    try:
        b = ns["_roster_badges"]({"id": "o/m", "sampling": {"temperature": big}})
        check("_roster_badges (one models_payload row) no longer raises",
              b.get("sampling") is None, b)
    except Exception as e:
        check("_roster_badges (one models_payload row) no longer raises", False, repr(e))
    env = {"MLX_VLM_SAMPLING_DEFAULTS": '{"temperature": 1%s, "top_p": 0.9}' % ("0" * 400)}
    try:
        d = L._sampling_defaults(env)
        check("launcher env path: huge value dropped, the rest applied", d == {"top_p": 0.9}, d)
    except Exception as e:
        check("launcher env path: huge value dropped, the rest applied", False, repr(e))
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
