#!/usr/bin/env python3
"""2026-10-08 audit P3 ("Stores"): the memory layer's pin ceiling race.

`_ml_facts_update` / `_ml_facts_post` counted the live pins and then wrote the
new one as two separate steps, so two pins landing together both counted 11
and the store ended up holding 13 (injection still caps at 12, so one pin the
owner made silently did nothing). The count and the write are now one
BEGIN IMMEDIATE transaction.

Two REAL `_ml_facts_update` calls run on two threads; a barrier inside the
count makes the old interleaving (both count before either writes) happen
deterministically whenever the code allows it. Throwaway HOME, no network.
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time

FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for i, a in enumerate(sys.argv):
    if a == "--repo" and i + 1 < len(sys.argv):
        REPO = os.path.abspath(sys.argv[i + 1])
AUX = os.path.join(REPO, "dashboard", "aux_memlayer.py")


class Ctx:
    def __init__(self, body=None):
        self.query = {}
        self.body = body or {}

    def q1(self, k, d=""):
        return d


tmp = tempfile.mkdtemp(prefix="hermes-pinrace-")
try:
    home = os.path.join(tmp, "home")
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data)
    os.makedirs(os.path.join(home, ".hermes", "memories", "people"))
    g = {"__name__": "aux_memlayer_pinrace", "HOME": home, "DATA": data,
         "HERE": os.path.dirname(AUX), "IX_DB": os.path.join(data, "index.db"),
         "_state_lock": threading.RLock(), "read_json": lambda p, d: d,
         "write_json": None, "settings_update": lambda fn: None,
         "get_settings": lambda: {},
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_cv_redact_raw": lambda t: t,
         "_CV_SECRET_RE": re.compile(r"\bpassword\s*[:=]\s*\S{6,}", re.I)}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    cap = g["_ML_PINNED_MAX"]

    ids = []
    for n in range(cap + 1):
        r = g["_ml_facts_post"](Ctx({"text": "fact number %d about the owner" % n,
                                     "origin": "card", "pinned": n < cap - 1}))
        ids.append(r["fact"]["id"])
    check("staged: one pin below the ceiling",
          g["memlayer_stats"]()["pinned"] == cap - 1, g["memlayer_stats"]())

    barrier = threading.Barrier(2, timeout=1.5)
    real_count = g["_ml_pinned_count"]

    def racing_count(con, exclude_id=None):
        n = real_count(con, exclude_id)
        try:
            barrier.wait()            # both threads have counted -> both write
        except threading.BrokenBarrierError:
            pass                      # the other one is (correctly) blocked
        return n

    g["_ml_pinned_count"] = racing_count
    results = {}

    def pin(fid):
        results[fid] = g["_ml_facts_update"](Ctx({"id": fid, "pinned": True}))

    t1 = threading.Thread(target=pin, args=(ids[-2],))
    t2 = threading.Thread(target=pin, args=(ids[-1],))
    t1.start(); t2.start(); t1.join(20); t2.join(20)
    g["_ml_pinned_count"] = real_count

    pinned = g["memlayer_stats"]()["pinned"]
    check("two concurrent pins never exceed the ceiling", pinned <= cap,
          (pinned, results))
    refused = [r for r in results.values() if isinstance(r, tuple)]
    check("exactly one of them is refused with the limit message",
          len(refused) == 1 and "limit" in refused[0][0].get("error", ""), results)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
sys.exit(1 if FAILS else 0)
