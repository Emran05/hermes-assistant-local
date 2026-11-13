#!/usr/bin/env python3
"""Seam S1-5 (audit 2026-11-09, E106 remainder + a Trust-review note).

1. aux_config._cfg_clean_model_entry's inline `sampling` fallback (used when
   server.py's _roster_sampling is not in its globals) did `float(x)` on an
   int, so a hand-edited or imported models.json with
   `"temperature": 10**400` raised OverflowError and the whole export/import
   failed. The value is now dropped.
2. _cfg_clean_permissions iterated `raw.get("patterns").items()` and
   `raw.get("classes").items()` blind, so an import with `"patterns": 3`
   raised. A field of the wrong type is now dropped.

ast-extracts the REAL helpers from dashboard/aux_config.py with
`_roster_sampling` deliberately absent (the fallback path).
"""
import ast
import os
import re
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))
CFG = REPO / "dashboard" / "aux_config.py"

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
    tree = ast.parse(CFG.read_text())
    nodes = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in names:
            nodes.append(n)
        elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names
                                               for t in n.targets):
            nodes.append(n)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(CFG), "exec"), ns)


ns = {"re": re, "os": os, "_cfg_pm": None}
extract({"CFG_MODEL_ID_RE", "CFG_SUBFOLDER_RE", "CFG_GLOB_RE", "CFG_TA_KEY_RE",
         "_cfg_model_id", "_cfg_int", "_cfg_clean_model_entry", "_cfg_clean_models",
         "_cfg_clean_permissions"}, ns)
assert "_roster_sampling" not in ns

print("== 1. the inline sampling fallback ==")
ID = "me/custom-7b"
for name, val in (("a huge int temperature", 10 ** 400),
                  ("a huge int top_k", 10 ** 400),
                  ("a huge negative presence_penalty", -(10 ** 400)),
                  ("inf", float("inf")), ("nan", float("nan"))):
    key = "top_k" if "top_k" in name else (
        "presence_penalty" if "presence" in name else "temperature")
    try:
        out = ns["_cfg_clean_model_entry"]({"id": ID, "sampling": {key: val, "top_p": 0.9}})
        err = None
    except Exception as e:          # noqa: BLE001
        out, err = None, e
    check("%s does not raise" % name, err is None, repr(err))
    smp = (out or {}).get("sampling") or {}
    check("%s is dropped, the valid top_p kept" % name,
          key not in smp and smp.get("top_p") == 0.9, smp)
try:
    r = ns["_cfg_clean_models"]({"active": ID, "roster": [
        {"id": ID, "sampling": {"temperature": 10 ** 400}}, {"id": "me/other", "label": "x"}]})
    ok = [m["id"] for m in r["roster"]] == [ID, "me/other"]
except Exception as e:              # noqa: BLE001
    ok, r = False, e
check("the whole roster still cleans (the import is not lost)", ok, r)
out = ns["_cfg_clean_model_entry"]({"id": ID, "sampling": {"temperature": 0.7, "top_k": 40}})
check("normal values still pass", out.get("sampling") == {"temperature": 0.7, "top_k": 40},
      out)

print("\n== 2. permissions with wrong-typed fields ==")
for name, raw in (("patterns: 3", {"patterns": 3, "classes": {}}),
                  ("classes: 3", {"classes": 3, "patterns": {}}),
                  ("patterns: a list", {"patterns": ["git *"], "classes": {}}),
                  ("classes: a string", {"classes": "auto", "patterns": {}})):
    try:
        r = ns["_cfg_clean_permissions"](raw)
        err = None
    except Exception as e:          # noqa: BLE001
        r, err = None, e
    check("%s does not raise" % name, err is None, repr(err))
    check("%s is dropped (empty)" % name, r == {"classes": {}, "patterns": {}}, r)
r = ns["_cfg_clean_permissions"]({"patterns": 3, "classes": {"x": "ask"}})
check("a good field next to a bad one is kept",
      r == {"classes": {"x": "ask"}, "patterns": {}}, r)
r = ns["_cfg_clean_permissions"]({"patterns": {"git *": "auto", "y": ["ask"]}})
check("an unhashable tier is dropped, not raised", r["patterns"] == {"git *": "auto"}, r)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
