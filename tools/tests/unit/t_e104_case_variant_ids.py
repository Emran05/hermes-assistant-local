#!/usr/bin/env python3
"""E104 (2026-11-09 audit, P3): the uncensored badge, the sticky flag and the
acknowledgement gate hold for every letter-case spelling of a curated id.

BEFORE: _seed_entry, the advisor lookups and _switch_ack_refusal matched the
model id by exact case, while the hub cache (and so `downloaded` and the
loader) is case-insensitive on the default APFS volume. A roster row
`orcarouter/qwen3.8-27b-uncensored-mlx` (a menu add with confirm, a hand
edit, a config import) showed as downloaded, carried no Uncensored badge, and
POST /api/models/switch loaded the abliterated weights with no 409.

AFTER: those lookups compare ids case-insensitively, and add_model refuses a
case variant of an id that is already listed or curated.

The REAL dashboard/server.py imported as a module with a scratch HOME.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-500:]))


TMP = tempfile.mkdtemp(prefix="hermes-e104-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hermes_server_e104", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec)
sys.modules["hermes_server_e104"] = srv
spec.loader.exec_module(srv)
assert srv.HOME == TMP
srv._bg_probe = lambda: False
srv.get_settings = lambda: {}            # no stored acknowledgement

ORCA = "orcarouter/Qwen3.8-27B-Uncensored-MLX"
STOCK = "mlx-community/Qwen3.8-27B-4bit"
VARIANTS = [ORCA.lower(), ORCA.upper(), "OrcaRouter/qwen3.8-27B-uncensored-MLX"]


def add_row(mid):
    srv._model_registry()
    with open(srv.MODELS_FILE) as f:
        reg = json.load(f)
    reg["models"].append({"id": mid, "label": "hand-added"})
    with open(srv.MODELS_FILE, "w") as f:
        json.dump(reg, f)


def main():
    try:
        check("control: the curated id itself is refused without the ack",
              (srv._switch_ack_refusal(ORCA) or (None, 0))[1] == 409)
        for v in VARIANTS:
            r = srv.add_model(v, confirm=True)
            check("add_model refuses the case variant %s" % v,
                  r.get("ok") is False and ORCA in (r.get("error") or ""), r)
        ids = [m["id"] for m in srv._model_registry()]
        check("...and stored none of them", not any(i in ids for i in VARIANTS), ids)
        r = srv.add_model(ORCA, confirm=True)
        check("the exact curated id is still accepted (already listed -> ok)", r.get("ok") is True, r)
        r = srv.add_model("someorg/Fresh-Model", confirm=True)
        check("an unrelated new id is still accepted", r.get("ok") is True, r)
        r = srv.add_model("SOMEORG/fresh-model", confirm=True)
        check("...and its own case variant is refused", r.get("ok") is False, r)

        for v in VARIANTS:            # a hand edit / config import got one in anyway
            add_row(v)
            row = next(m for m in srv._model_registry() if m["id"] == v)
            check("%s: the menu badge says uncensored" % v,
                  srv._roster_badges(row)["uncensored"] is True, srv._roster_badges(row))
            ref = srv._switch_ack_refusal(v)
            check("%s: the switch gate answers 409 needs_ack" % v,
                  ref is not None and ref[1] == 409 and ref[0].get("needs_ack") is True, ref)
            check("%s: an explicit ack still passes" % v,
                  srv._switch_ack_refusal(v, ack=True) is None)
            check("%s: a hand-edited uncensored:false cannot clear the seed's true" % v,
                  srv._roster_uncensored({"id": v, "uncensored": False}) is True)

        check("control: a stock seed in another case is NOT uncensored",
              srv._roster_uncensored({"id": STOCK.upper()}) is False
              and srv._switch_ack_refusal(STOCK.upper()) is None)
        srv._ADV_FACTS = {"Facts/Only-Build": {"uncensored": True}}
        check("advisor facts are matched case-insensitively too",
              srv._roster_uncensored({"id": "facts/only-build"}) is True
              and (srv._switch_ack_refusal("FACTS/ONLY-BUILD") or (None, 0))[1] == 409)
        srv._ADV_FACTS = {}
        check("_id_get: exact hit, case-insensitive hit, miss, junk",
              srv._id_get({"A/b": 1}, "A/b") == 1 and srv._id_get({"A/b": 1}, "a/B") == 1
              and srv._id_get({"A/b": 1}, "a/c") is None and srv._id_get(None, "a") is None
              and srv._id_get({"A/b": 1}, None) is None)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
