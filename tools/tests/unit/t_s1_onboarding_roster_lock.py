#!/usr/bin/env python3
"""Seam S1-4 (audit 2026-11-09, E113 remainder) — onboarding's roster write.

BEFORE: aux_onboarding._onb_ensure_roster read models.json, appended the
picked model's catalog row and wrote the whole file back with no lock. A
roster writer that landed in between (a menu add, a Thinking flip — all of
which now go through server.py's locked models_update) was overwritten by
the stale snapshot.

AFTER: the row is appended inside models_update(...) (resolved by name at
call time), which re-reads under _state_lock; the id is re-checked inside
the lock, so a concurrent writer that added the same id leaves one row.

The REAL dashboard/server.py (every aux module) imported against a scratch
HOME. Nothing is woken, downloaded or restarted.
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-600:]))


TMP = tempfile.mkdtemp(prefix="hermes-s1-onbroster-")
os.environ["HOME"] = TMP
os.environ.pop("MLX_SOFT_GB", None)
os.environ.pop("MLX_HARD_GB", None)
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_s1_onbroster",
                                              os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_s1_onbroster"] = srv
spec.loader.exec_module(srv)
assert srv.HOME == TMP, srv.HOME
srv._bg_probe = lambda: False
ORIG_REG = srv._model_registry
OTHER = "someorg/Brand-New-Model"
PICK = srv.M2B


def roster_ids():
    with open(srv.MODELS_FILE) as f:
        return [m["id"] for m in json.load(f)["models"]]


def reset(drop=()):
    srv._model_registry = ORIG_REG
    try:
        os.remove(srv.MODELS_FILE)
    except OSError:
        pass
    reg = srv._model_registry()          # seeds written
    with open(srv.MODELS_FILE, "w") as f:
        json.dump({"models": [m for m in reg if m["id"] not in drop]}, f)


def overlap(other_writer, nth=2):
    """Run _onb_ensure_roster(PICK); right after the main thread's SECOND roster
    read (the first is _model_entry's "is it there" look; the second is the
    snapshot the row is appended to), `other_writer` runs in another thread
    (given 0.5 s)."""
    main = threading.current_thread()
    fired, box = [], {}

    def hooked():
        reg = ORIG_REG()
        if threading.current_thread() is main:
            fired.append(1)
        if len(fired) == nth and "t" not in box and threading.current_thread() is main:
            t = threading.Thread(target=lambda: box.update(r=other_writer()))
            t.start()
            t.join(0.5)
            box["t"] = t
        return reg
    srv._model_registry = hooked
    try:
        r = srv._onb_ensure_roster(PICK)
    finally:
        srv._model_registry = ORIG_REG
    if box.get("t"):
        box["t"].join(5)
    return r, box.get("r")


try:
    print("== a menu add lands while onboarding adds its pick ==")
    reset(drop=(PICK,))
    check("(the pick is not on the roster yet)", PICK not in roster_ids())
    r, add = overlap(lambda: srv.add_model(OTHER, confirm=True))
    ids = roster_ids()
    check("onboarding answered True (it added the row)", r is True, r)
    check("the menu add answered ok", (add or {}).get("ok") is True, add)
    check("BOTH rows are in models.json (the add was lost before)",
          PICK in ids and OTHER in ids, ids)
    check("no id stored twice", len(ids) == len(set(ids)), ids)

    print("== the same id is added by someone else in between ==")
    reset(drop=(PICK,))
    row = dict(srv._ONB_BY_ID[PICK], label="added elsewhere")
    row.pop("size_gb", None)
    row.pop("draft_size_gb", None)

    def same_id():
        def mut(reg):
            if any(m.get("id") == PICK for m in reg):
                return False
            reg.append(dict(row))
        return srv.models_update(mut)
    # it lands right after onboarding's "is it there" look, before its write
    r, _ = overlap(same_id, nth=1)
    check("onboarding says it added nothing", r is False, r)
    with open(srv.MODELS_FILE) as f:
        rows = [m for m in json.load(f)["models"] if m["id"] == PICK]
    check("one row for the pick, not two", len(rows) == 1, rows)
    check("...the one already there is kept as it is (additive only)",
          rows and rows[0].get("label") == "added elsewhere", rows)

    print("== the plain path still works ==")
    reset(drop=(PICK,))
    check("a free roster gets the row", srv._onb_ensure_roster(PICK) is True
          and PICK in roster_ids())
    check("a second call is a no-op", srv._onb_ensure_roster(PICK) is False
          and roster_ids().count(PICK) == 1)
    entry = srv._model_entry(PICK) or {}
    check("...with the full catalog shape, without the seed-only keys",
          entry.get("backend") == srv._ONB_BY_ID[PICK].get("backend")
          and "size_gb" not in entry and "verified" not in entry, entry)
finally:
    srv._model_registry = ORIG_REG

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
