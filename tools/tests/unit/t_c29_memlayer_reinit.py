#!/usr/bin/env python3
"""C29 (audit 2026-10-21) + the 2026-10-08 P3 "non-atomic memlayer review
migration": the memory layer's store recovers from a failed init.

  1. C29 — `memlayer_block()` (the per-turn chat path) only READ
     `_ml_state["ready"]`. One failed `_ml_init()` at import (memory.db locked
     or unreadable at dashboard start) meant every later turn silently carried
     no memory block until a restart. It now re-tries, at most once per
     _ML_RETRY_S, so a store that comes back is used again.
  2. The review-column migration ran ALTER TABLE in autocommit, so a failure
     between it and the backfill left the column present, the imported rows
     NOT held for review, and the early `"review" in cols` return made that
     permanent. It is one transaction now.

Execs the REAL aux_memlayer.py (test_memlayer.py's pattern) against a
throwaway HOME. No model, no network, no dashboard.
"""
import io
import json
import os
import re
import shutil
import sqlite3
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

_CV_SECRET_RE = re.compile(r"\b(token|api[_-]?key|secret|password)\b\s*[:=]\s*\S{6,}", re.I)


def build(tmp, pre=None):
    home = os.path.join(tmp, "home")
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, exist_ok=True)
    os.makedirs(os.path.join(home, ".hermes", "memories", "people"), exist_ok=True)
    if pre:
        pre(data)
    settings_file = os.path.join(data, "settings.json")
    lock = threading.RLock()

    def read_json(p, default):
        try:
            return json.load(io.open(p, encoding="utf-8"))
        except Exception:
            return default

    g = {"__name__": "aux_memlayer_c29", "HOME": home, "DATA": data,
         "HERE": os.path.dirname(AUX), "IX_DB": os.path.join(data, "index.db"),
         "_state_lock": lock, "read_json": read_json, "write_json": None,
         "settings_update": lambda fn: None,
         "get_settings": lambda: read_json(settings_file, {}),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_cv_redact_raw": lambda t: t, "_CV_SECRET_RE": _CV_SECRET_RE}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g, data


def seed_fact(db, schema, text):
    con = sqlite3.connect(db)
    con.executescript(schema)
    cols = [r[1] for r in con.execute("PRAGMA table_info(facts)")]
    if "review" not in cols:
        con.execute("ALTER TABLE facts ADD COLUMN review INTEGER NOT NULL DEFAULT 0")
    now = time.time()
    con.execute("INSERT INTO facts(text, kind, source, created_ts, updated_ts, "
                "pinned, review) VALUES (?, 'fact', 'user', ?, ?, 1, 0)",
                (text, now, now))
    con.commit()
    con.close()


tmp = tempfile.mkdtemp(prefix="hermes-c29-")
try:
    print("\n1. a failed init at import does not disable memory for good")
    # memory.db is a DIRECTORY at import: sqlite cannot open it, init fails
    g, data = build(os.path.join(tmp, "a"),
                    pre=lambda d: os.makedirs(os.path.join(d, "memory.db")))
    db = g["ML_DB"]
    check("init failed at import, as staged", g["_ml_state"]["ready"] is False,
          g["_ml_state"])
    check("a turn while it is still broken injects nothing, without raising",
          g["memlayer_lines"]("the owner prefers tea") == "")
    os.rmdir(db)                                    # the store comes back
    seed_fact(db, g["_ML_SCHEMA"], "The owner prefers green tea")
    g["_ml_state"]["retry_at"] = 0.0                # retry window elapsed
    out = g["memlayer_lines"]("what tea does the owner like")
    check("the next turn re-initialises and injects the pinned fact",
          "green tea" in out, out)
    check("and the layer reports ready again", g["_ml_state"]["ready"] is True)

    print("   ... and a still-broken store is not re-opened on every turn")
    g2, data2 = build(os.path.join(tmp, "b"),
                      pre=lambda d: os.makedirs(os.path.join(d, "memory.db")))
    calls = []
    real_init = g2["_ml_init"]
    g2["_ml_init"] = lambda: (calls.append(1), real_init())[1]
    g2["_ml_state"]["retry_at"] = 0.0
    for _ in range(5):
        g2["memlayer_lines"]("anything at all here")
    check("five turns inside the window cost ONE retry", len(calls) == 1, len(calls))

    print("\n2. the review migration is all-or-nothing")
    g3, data3 = build(os.path.join(tmp, "c"))
    old = os.path.join(data3, "old-memory.db")
    con = sqlite3.connect(old)
    con.executescript(g3["_ML_SCHEMA"].replace(
        "  origin_key   TEXT UNIQUE,\n  review       INTEGER NOT NULL DEFAULT 0\n",
        "  origin_key   TEXT UNIQUE\n"))
    con.execute("INSERT INTO facts(text, kind, source, created_ts, updated_ts, "
                "origin_key) VALUES ('imported from NOW.md', 'note', 'youmodel', "
                "1, 1, 'ym:NOW.md#0')")
    con.commit()
    check("staged a pre-review store",
          "review" not in [r[1] for r in con.execute("PRAGMA table_info(facts)")])
    con.close()

    class Crashy(object):
        """A real connection that dies on the backfill, like a crash would."""
        def __init__(self, c):
            self.c = c

        def execute(self, sql, *a):
            if sql.lstrip().startswith("UPDATE facts SET review = 1"):
                raise sqlite3.OperationalError("simulated crash mid-migration")
            return self.c.execute(sql, *a)

        def commit(self):
            self.c.commit()

        def rollback(self):
            self.c.rollback()

    c = sqlite3.connect(old)
    try:
        g3["_ml_migrate"](Crashy(c))
        check("the simulated crash surfaced", False, "no exception")
    except sqlite3.OperationalError:
        pass
    finally:
        try:
            c.rollback()                 # what process death would do
        except Exception:
            pass
        c.close()
    c = sqlite3.connect(old)
    cols = [r[1] for r in c.execute("PRAGMA table_info(facts)")]
    check("a failed migration leaves NO half-added review column", "review" not in cols,
          cols)
    g3["_ml_migrate"](c)
    held = c.execute("SELECT review FROM facts WHERE origin_key='ym:NOW.md#0'").fetchone()
    c.close()
    check("so the next start runs it fully and the imported row IS held",
          held is not None and held[0] == 1, held)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
sys.exit(1 if FAILS else 0)
