#!/usr/bin/env python3
"""R2-49 (docs/audits/2026-10-08-codebase-audit.md, P3 For-You) — a Useful/
Noise click and a For-You rebuild no longer lose each other's writes.

BEFORE: foryou_react_handler (dashboard/aux_foryou.py) read foryou.json, put
the reaction on the move and wrote the whole store back, outside any lock,
while _foryou_build could write a fresh store at any moment (it runs a model
call of up to two minutes between reading and writing). Two interleavings:
  A. the build writes while a click is between its read and its write: the
     click writes its stale copy back and the build's new moves are gone;
  B. the click lands while the build is still reasoning: the build's write
     replaces the store and the mark the owner just made disappears.

AFTER: every foryou.json write, and the click's read-modify-write, run under
_fy_store_lock (not the build lock, which a click must not wait two minutes
for); a build's write re-applies reactions logged since the build started.

The REAL aux_foryou.py is exec'd the way the aux loader does it, on a
throwaway HOME, and the REAL _foryou_build and foryou_react_handler run. Only
the model is replaced: _fy_model_ok says yes and _fy_model_pass turns the
candidates into moves without a network call. Each interleaving is forced
from inside the code under test (a hook in the reaction log append for A, in
the model pass for B), so neither depends on thread timing. On the old code
A loses the new moves and B loses the mark.
"""
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

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %r" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-r249-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
MEM = os.path.join(HOME, ".hermes", "memories")
os.makedirs(DATA)
os.makedirs(os.path.join(MEM, "people"))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


ns = {
    "__name__": "aux_harness", "HOME": HOME, "DATA": DATA, "read_json": read_json,
    "register_get": lambda p, f: None, "register_post": lambda p, f: None,
    "WIDGETS": {}, "EXPANDERS": {}, "layout_inject": lambda *a, **k: None,
    "mlx_admission": lambda: (True, 0, 0), "model_online": lambda: True,
    "agent_paused": lambda: False, "CHAT_JOBS": {},
    "MODEL_URL": "http://127.0.0.1:9/v1/models",
    "ACTIVE_MODEL_FILE": os.path.join(DATA, "active-model"), "DEFAULT_MODEL": "none",
    "_foryou_thread_started": True,          # never start the daemon
    "re": re, "os": os, "json": json, "time": time, "threading": threading, "sys": sys,
}
SRC = os.path.join(REPO, "dashboard", "aux_foryou.py")
exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)
FORYOU = ns["FORYOU_FILE"]

with open(os.path.join(MEM, "GOALS.md"), "w") as f:
    f.write("Ship the solar inverter firmware\n§\nLearn rust embedded tooling\n")


def intel(tag):
    items = [{"title": "Solar inverter firmware %s %d" % (tag, i),
              "url": "https://example.org/%s/%d" % (tag, i), "source": "Feed",
              "topic": "tech", "ts": time.time() - i, "summary": "rust embedded inverter"}
             for i in range(3)]
    items.append({"title": "Solar inverter firmware shared story",
                  "url": "https://example.org/shared", "source": "Feed",
                  "topic": "tech", "ts": time.time(), "summary": "rust embedded inverter"})
    with open(ns["FY_INTEL_FILE"], "w") as f:
        json.dump({"items": items}, f)


HOOK = {"pass": None, "append": None}


def model_pass(cands, prof):
    if HOOK["pass"]:
        HOOK["pass"]()
    return [{"title": c["item"]["title"], "url": c["item"]["url"], "source": "Feed",
             "topic": "tech", "ts": c["item"]["ts"], "why_you": "your goal",
             "matched_goal": "firmware", "matched_person": "",
             "suggested_action": "Read it", "score": 0.9} for c in cands]


ns["_fy_model_ok"] = lambda: True
ns["_fy_model_pass"] = model_pass
_real_append = ns["_fy_react_append"]


def append(rec):
    _real_append(rec)
    if HOOK["append"]:
        h, HOOK["append"] = HOOK["append"], None
        h()


ns["_fy_react_append"] = append


class Ctx(object):
    def __init__(self, body):
        self.body = body


def react(url, reaction):
    r = ns["foryou_react_handler"](Ctx({"url": url, "reaction": reaction}))
    return r[0] if isinstance(r, tuple) else r


def urls(store):
    return sorted(m["url"] for m in (store or {}).get("moves") or [])


def marks(store):
    return {m["url"]: m.get("reaction") for m in (store or {}).get("moves") or []
            if m.get("reaction")}


print("== baseline: a build, then a click ==")
intel("old")
ns["_foryou_build"]()
first = read_json(FORYOU)
check("the first build stored the old moves",
      "https://example.org/old/0" in urls(first), urls(first))
r = react("https://example.org/old/0", "useful")
check("a click is logged and marked", r.get("ok") and
      marks(read_json(FORYOU)).get("https://example.org/old/0") == "useful",
      (r, marks(read_json(FORYOU))))

print("== A: a build lands between the click's read and its write ==")
intel("new")
started, go = threading.Event(), threading.Event()


def held():                  # the build is reasoning: it started before the click
    started.set()
    go.wait(10)


HOOK["pass"] = held
t = threading.Thread(target=ns["_foryou_build"], daemon=True)
t.start()
started.wait(10)


def release_build():         # inside the click, after its read: let the build write
    go.set()
    t.join(1.0)              # the old code writes here; the new one waits on the lock


HOOK["append"] = release_build
r = react("https://example.org/shared", "noise")
HOOK["pass"] = None
t.join(10)
after = read_json(FORYOU)
check("the click succeeded", r.get("ok"), r)
check("the build finished", not t.is_alive())
check("the build's new moves survive the click",
      "https://example.org/new/0" in urls(after) and
      "https://example.org/old/0" not in urls(after), urls(after))
check("...and the click's mark is on the new store too",
      marks(after).get("https://example.org/shared") == "noise", marks(after))

print("== B: a click lands while a build is still reasoning ==")
intel("newer")
HOOK["pass"] = lambda: react("https://example.org/shared", "useful")
ns["_foryou_build"]()
HOOK["pass"] = None
after = read_json(FORYOU)
check("the build wrote its moves", "https://example.org/newer/0" in urls(after),
      urls(after))
check("the mark made during the build is not lost",
      marks(after).get("https://example.org/shared") == "useful", marks(after))
check("a move nobody clicked carries no mark",
      "https://example.org/newer/1" not in marks(after), marks(after))
log = [json.loads(ln) for ln in open(ns["FY_REACTS_FILE"])]
check("every click is in the reaction log", [x["reaction"] for x in log] ==
      ["useful", "noise", "useful"], log)

print("== a click from before a build does not stick to its new moves ==")
intel("newest")
ns["_foryou_build"]()
check("an older reaction is not re-applied by a later build",
      "https://example.org/shared" not in marks(read_json(FORYOU)),
      marks(read_json(FORYOU)))

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
