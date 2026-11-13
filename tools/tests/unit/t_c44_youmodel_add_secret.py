#!/usr/bin/env python3
"""C44 (docs/audits/2026-10-21-codebase-audit.md, the remaining part) — POST
/api/youmodel/add refuses an entry that looks like a credential.

BEFORE: _ym_add_handler (dashboard/aux_youmodel.py) appended any text to
GOALS.md / NOW.md / ... / people/<slug>.md. Those files are model-writable
through this very route, and For-You, the brief and (with both switches on)
Claude read them. C44's first half made For-You withhold flagged entries when
it reads them; the store still accepted the key in the first place.

AFTER: the handler runs aux_memlayer's _ml_looks_secret (resolved by name at
call time, the same test /api/memory/facts and MCP memory_facts(add=) apply)
and answers 400 "that looks like a credential — not storing it"; with the test
missing or raising it stores nothing and answers 503, as aux_memlayer does.

Real code: aux_convos.py exec'd (the regexes), _ml_looks_secret and its
helpers extracted from aux_memlayer.py by AST (t_c44's method), then
aux_memory.py and aux_youmodel.py exec'd in load order into one fake-server
globals dict on a throwaway HOME. The handler is called directly.
"""
import ast
import io
import json
import os
import re
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %r" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-c44ym-")
os.makedirs(os.path.join(HOME, ".hermes", "memories"))


class Ctx(object):
    def __init__(self, body=None, query=None):
        self.body = body or {}
        self.query = query or {}

    def q1(self, k, default=""):
        v = self.query.get(k)
        return v[0] if v else default


def read_json(p, default):
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)


G = {"__name__": "aux_c44ym", "HOME": HOME, "HERE": DASH,
     "DATA": os.path.join(HOME, ".hermes", "dashboard"),
     "read_json": read_json, "write_json": write_json,
     "_state_lock": threading.RLock(), "_widget_cache": {},
     "register_get": lambda p, f: None, "register_post": lambda p, f: None,
     "RouteCtx": Ctx, "re": re, "os": os, "json": json, "time": time, "sys": sys}

# E114: aux_memory's index edit reads through server.py's REAL
# read_json_for_update (C27), exec'd here with its two helpers.
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402
G.update({"json": json, "os": os, "time": time, "sys": sys})
server_real.server_functions(("read_json_for_update", "_json_unparsable",
                              "_quarantine_corrupt"), G)


def exec_file(fn):
    path = os.path.join(DASH, fn)
    exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), G)


exec_file("aux_convos.py")
ML = os.path.join(DASH, "aux_memlayer.py")
tree = ast.parse(io.open(ML, encoding="utf-8").read(), ML)
WANT = {"_ml_log", "_ml_g", "_MlNoScrubber", "_ml_scrubber", "_ml_looks_secret"}
picked = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
          and n.name in WANT]
G["_ml_state"] = {"ready": False, "error": "", "logged": set()}
exec(compile(ast.Module(body=picked, type_ignores=[]), ML, "exec"), G)
REAL_LOOKS = G["_ml_looks_secret"]
exec_file("aux_memory.py")
exec_file("aux_youmodel.py")
MEM = G["MEM_DIR"]


def add(name, text):
    r = G["_ym_add_handler"](Ctx({"file": name, "text": text}))
    return (r[0], r[1]) if isinstance(r, tuple) else (r, 200)


def content(name):
    try:
        return io.open(os.path.join(MEM, name), encoding="utf-8").read()
    except OSError:
        return None


KEY = "sk-ant-api03-" + "Q" * 40
GH = "ghp_" + "a" * 36

print("== credentials are refused ==")
r, st = add("GOALS.md", "Remember the deploy key " + KEY)
check("an Anthropic key in a new GOALS.md entry: 400", st == 400, (st, r))
check("...with aux_memlayer's message", "looks like a credential" in str(r.get("error")), r)
check("...and GOALS.md is not even created", content("GOALS.md") is None,
      content("GOALS.md"))

r, st = add("GOALS.md", "Ship the solar inverter firmware (as of Oct 2026)")
check("a real goal is stored", st == 200 and r.get("ok"), (st, r))
before = content("GOALS.md")
r, st = add("GOALS.md", "github token: " + GH)
check("a labelled token appended to an existing file: 400", st == 400, (st, r))
check("...and the file is unchanged", content("GOALS.md") == before)
r, st = add("people/dana-quill.md", "Dana Quill. Shared password=hunter2hunter2 for the demo")
check("a password on a people card: 400", st == 400, (st, r))
check("...and no card is written", content("people/dana-quill.md") is None)

print("== ordinary text is not a credential ==")
for text in ("The API token for the demo expired; ask Dana for a new one",
             "Looking for: a secret santa gift under $40"):
    r, st = add("LOOKING-FOR.md", text)
    check("stored: %r" % text[:40], st == 200 and r.get("ok"), (st, r))
check("both entries are in the file", all(t in (content("LOOKING-FOR.md") or "")
                                          for t in ("token for the demo", "secret santa")))

print("== no working credential test: nothing is stored ==")
G.pop("_ml_looks_secret")
r, st = add("NOW.md", "Plain entry while aux_memlayer is missing")
check("aux_memlayer not loaded: 503", st == 503 and r.get("ok") is False, (st, r))
check("...and NOW.md is not written", content("NOW.md") is None)


def broken(text):
    raise G["_MlNoScrubber"]("the credential check failed (re.error) — nothing stored")


G["_ml_looks_secret"] = broken
r, st = add("NOW.md", "Plain entry while the check is broken")
check("a check that raises: 503 with its reason", st == 503 and
      "credential check failed" in str(r.get("error")), (st, r))
check("...and NOW.md is still not written", content("NOW.md") is None)
G["_ml_looks_secret"] = REAL_LOOKS
r, st = add("NOW.md", "Plain entry once the check is back")
check("with the check back the entry is stored", st == 200 and r.get("ok"), (st, r))

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
