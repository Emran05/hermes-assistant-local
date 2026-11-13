#!/usr/bin/env python3
"""E113 (2026-11-09 audit, P3): models.json read-modify-writes no longer
lose each other's changes.

BEFORE: set_model_thinking and add_model each read the roster, changed it
and wrote the whole file back with no lock, and _model_registry's migration
write did the same — only the config import took _state_lock, which protects
nothing while the others ignore it. Two overlapping writers: the later one
replaced models.json with its stale snapshot (an added model row, a
template_args flip silently gone).

AFTER: models_update(mutate_fn) re-reads and writes under _state_lock and
every roster writer in server.py goes through it; the migration write only
happens with the lock free AND the file still as it was read (otherwise the
caller's own write, or the next read, persists the migration).

The REAL dashboard/server.py imported as a module with a scratch HOME (the
pattern t_r3_roster_fields uses). Offline; no model, no launchctl.
"""
import importlib.util
import json
import os
import shutil
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


TMP = tempfile.mkdtemp(prefix="hermes-e113-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_e113", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_e113"] = srv
spec.loader.exec_module(srv)
assert srv.HOME == TMP, srv.HOME
srv._bg_probe = lambda: False
ORIG_REG = srv._model_registry
ORCA = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
NEWID = "someorg/Brand-New-Model"


def roster():
    with open(srv.MODELS_FILE) as f:
        return {m["id"]: m for m in json.load(f)["models"]}


def reset():
    srv._model_registry = ORIG_REG
    try:
        os.remove(srv.MODELS_FILE)
    except OSError:
        pass
    srv._model_registry()          # seeds written


def test_thinking_vs_add():
    print("\n1. a Thinking toggle and a menu add overlap")
    reset()
    main = threading.current_thread()
    fired, res = [], {}

    def hooked():
        reg = ORIG_REG()
        if not fired and threading.current_thread() is main:
            fired.append(1)          # the toggle has its snapshot: the add lands now
            t = threading.Thread(target=lambda: res.update(add=srv.add_model(NEWID, confirm=True)))
            t.start()
            t.join(0.5)
            res["t"] = t
        return reg
    srv._model_registry = hooked
    try:
        r = srv.set_model_thinking(True, ORCA)
    finally:
        srv._model_registry = ORIG_REG
    res["t"].join(5)
    rows = roster()
    check("the toggle answered ok", r.get("ok") is True, r)
    check("the add answered ok", (res.get("add") or {}).get("ok") is True, res.get("add"))
    check("BOTH changes are in models.json: the added row ...", NEWID in rows, sorted(rows))
    check("... and the thinking flip",
          (rows.get(ORCA, {}).get("template_args") or {}).get("enable_thinking") is True,
          rows.get(ORCA))


def test_add_vs_add():
    print("\n2. two adds at once: both rows stored, no duplicate")
    reset()
    ids = ["someorg/A-1", "someorg/B-2", "someorg/A-1"]
    ts = [threading.Thread(target=srv.add_model, args=(i,), kwargs={"confirm": True}) for i in ids]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    with open(srv.MODELS_FILE) as f:
        got = [m["id"] for m in json.load(f)["models"]]
    check("both new ids present", "someorg/A-1" in got and "someorg/B-2" in got, got)
    check("no id stored twice", len(got) == len(set(got)), got)


def test_migration_cas():
    print("\n3. the migration write never overwrites a newer roster")
    reset()
    with open(srv.MODELS_FILE) as f:
        full = json.load(f)
    old = {"models": [m for m in full["models"] if m["id"] != ORCA]}   # a seed missing
    with open(srv.MODELS_FILE, "w") as f:
        json.dump(old, f)
    newer = {"models": old["models"] + [{"id": "someorg/Written-Meanwhile", "label": "x"}]}
    real = srv.read_json_for_update
    calls = []

    def racing_read(path, default):
        v = real(path, default)
        if path == srv.MODELS_FILE and not calls:
            calls.append(1)
            with open(srv.MODELS_FILE, "w") as f:     # another writer, right after our read
                json.dump(newer, f)
        return v
    srv.read_json_for_update = racing_read
    try:
        srv._model_registry()
    finally:
        srv.read_json_for_update = real
    rows = roster()
    check("the row written meanwhile survives the migration",
          "someorg/Written-Meanwhile" in rows, sorted(rows))
    srv._model_registry()                  # the next read migrates for real
    rows = roster()
    check("...and the next read still adds the missing seed",
          ORCA in rows and "someorg/Written-Meanwhile" in rows, sorted(rows))


def test_models_update_contract():
    print("\n4. models_update() contract")
    reset()
    before = open(srv.MODELS_FILE).read()
    check("mutate_fn -> False writes nothing",
          srv.models_update(lambda reg: False) is None and open(srv.MODELS_FILE).read() == before)
    out = srv.models_update(lambda reg: reg + [{"id": "someorg/Replaced"}])
    check("a returned list replaces the roster", "someorg/Replaced" in roster()
          and isinstance(out, list))
    check("the lock is free afterwards", srv._state_lock.acquire(blocking=False))
    srv._state_lock.release()
    os.chmod(srv.MODELS_FILE, 0)
    try:
        try:
            srv.models_update(lambda reg: reg.append({"id": "x/y"}))
            raised = False
        except OSError:
            raised = True
    finally:
        os.chmod(srv.MODELS_FILE, 0o600)
    check("an unreadable models.json raises and is not overwritten with seeds",
          raised and "someorg/Replaced" in roster(), raised)
    check("...and the lock was released", srv._state_lock.acquire(blocking=False))
    srv._state_lock.release()


def main():
    try:
        test_thinking_vs_add()
        test_add_vs_add()
        test_migration_cas()
        test_models_update_contract()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
