#!/usr/bin/env python3
"""C57 (docs/audits/2026-10-21-codebase-audit.md): GET /api/needsyou?mark=0
never suppressed the sighting bookkeeping.

BEFORE: _ny_get_handler did `str(q.get("mark", "1"))`, but server.py hands
aux routes `urllib.parse.parse_qs(...)`, whose values are LISTS — so the
test compared "['0']" with "0", always concluded mark=True, and the MCP
needs_you tool (which sends mark=0 precisely so a model's read is not a
human sighting) and every script fed now_precision's denominator.

AFTER: the first value of a list is used, the way RouteCtx.q1 reads it.

Uses the REAL RouteCtx class (ast-extracted from server.py) and the real
parse_qs output, exactly what the dispatcher builds.
"""
import ast
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

src = open(os.path.join(E.DASH, "server.py")).read()
node = next(n for n in ast.parse(src).body
            if isinstance(n, ast.ClassDef) and n.name == "RouteCtx")
ns = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), "server.py", "exec"), ns)
RouteCtx = ns["RouteCtx"]

NOW = time.time()
g, data = E.load()
E.write_messages(data, [E.convo("+15550000061", "can you call me?", NOW - 60,
                                name="Ana")], generated_at=NOW)
p = g["_ny_build"](NOW)
g["_ny_cache"]["payload"] = p
g["_ny_cache"]["at"] = time.time()
E.check("fixture has a visible row", len(E.visible_ids(p)) >= 1, E.visible_ids(p))


def shown():
    return len(g["_ny_load"]()["shown"])


def get(qs):
    return g["_ny_get_handler"](RouteCtx(query=urllib.parse.parse_qs(qs),
                                         raw_path="/api/needsyou"))


print("=== C57: read-only reads do not count as sightings ===")
for qs in ("mark=0", "mark=false", "mark=no", "mark=0&x=1"):
    r = get(qs)
    E.check("?%s answers the payload" % qs, r.get("ok") is True, r)
    E.check("?%s records no sighting" % qs, shown() == 0,
            g["_ny_load"]()["shown"])

print("=== C57: a normal read still does ===")
get("")
E.check("no query -> the sighting is recorded", shown() >= 1)
n = shown()
get("mark=1")
E.check("?mark=1 -> no duplicate (one per item per day)", shown() == n)

sys.exit(E.finish())
