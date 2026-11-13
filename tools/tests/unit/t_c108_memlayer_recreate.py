#!/usr/bin/env python3
"""C108 (memory.db half) — the memory layer survives its store being deleted.

index.db got this fix in k04b; memory.db did not.  If memory.db was deleted
(or emptied) while the dashboard ran, `_ml_conn()` recreated an empty 0600
file but `_ml_state["ready"]` stayed True, so every fact query, every add and
every turn's retrieval failed "no such table: facts" until a restart.  Now one
sqlite_master probe per connection recreates the schema (the _ix_conn
pattern), stale WAL/shm sidecars of the vanished file are removed first, and
the boot import re-runs so MEMORY.md/USER.md/You-model rows come back.

Execs the REAL aux_memlayer.py (test_memlayer.py's pattern) against a
throwaway HOME.  No model, no network, no dashboard.

Fails on the old code: after the delete, GET /api/memory/facts answers 500
"no such table", POST /api/memory/facts cannot add, and nothing is re-imported.
"""
import io
import json
import os
import re
import shutil
import stat
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
AUX = os.path.join(REPO, "dashboard", "aux_memlayer.py")

_CV_SECRET_RE = re.compile(r"\b(token|api[_-]?key|secret|password)\b\s*[:=]\s*(\S{6,})", re.I)


class Ctx:
    def __init__(self, body=None, **q):
        self.body, self.q = body, q

    def q1(self, k):
        return self.q.get(k)


def res(r):
    return r if isinstance(r, tuple) else (r, 200)


tmp = tempfile.mkdtemp(prefix="hermes-c108m-")
try:
    home = os.path.join(tmp, "home")
    data = os.path.join(home, ".hermes", "dashboard")
    mem = os.path.join(home, ".hermes", "memories")
    os.makedirs(data)
    os.makedirs(os.path.join(mem, "people"))
    io.open(os.path.join(mem, "USER.md"), "w", encoding="utf-8").write(
        "The owner lives in Lisbon\n§\nThe owner drinks green tea")
    settings_file = os.path.join(data, "settings.json")

    def read_json(p, default):
        try:
            return json.load(io.open(p, encoding="utf-8"))
        except Exception:
            return default

    g = {"__name__": "aux_memlayer_c108", "HOME": home, "DATA": data,
         "HERE": os.path.dirname(AUX), "IX_DB": os.path.join(data, "index.db"),
         "_state_lock": threading.RLock(), "read_json": read_json,
         "write_json": None, "settings_update": lambda fn: None,
         "get_settings": lambda: read_json(settings_file, {}),
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_cv_redact_raw": lambda t: t, "_CV_SECRET_RE": _CV_SECRET_RE}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    db = g["ML_DB"]

    print("\n=== boot ===")
    check("store ready at import", g["_ml_state"]["ready"] is True, g["_ml_state"])
    body, code = res(g["_ml_facts_post"](Ctx({"text": "Prefers window seats",
                                               "origin": "card", "pinned": True})))
    check("a card fact is added", code == 200 and body.get("ok") is True, body)
    texts = [f["text"] for f in res(g["_ml_facts_get"](Ctx()))[0].get("facts", [])]
    check("the boot import brought USER.md in", "The owner lives in Lisbon" in texts, texts)

    print("\n=== memory.db deleted under the running dashboard ===")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db + suffix)
        except OSError:
            pass
    # a stale sidecar from the vanished file (garbage, as a crash would leave)
    io.open(db + "-wal", "wb").write(b"\x37\x7f\x06\x82" + b"\x00" * 60)
    check("ready is still True (nothing told the module)", g["_ml_state"]["ready"] is True)

    body, code = res(g["_ml_facts_get"](Ctx()))
    check("GET /api/memory/facts answers ok, not 'no such table'",
          code == 200 and body.get("ok") is True, body)
    check("the store was recreated 0600",
          os.path.exists(db) and stat.S_IMODE(os.stat(db).st_mode) == 0o600,
          oct(stat.S_IMODE(os.stat(db).st_mode)) if os.path.exists(db) else "missing")

    body, code = res(g["_ml_facts_post"](Ctx({"text": "Keeps a paper notebook",
                                               "origin": "card"})))
    check("POST /api/memory/facts can add again", code == 200 and body.get("ok") is True, body)
    body, code = res(g["_ml_facts_get"](Ctx(q="notebook")))
    check("... and FTS finds the new fact",
          [f["text"] for f in body.get("facts", [])] == ["Keeps a paper notebook"], body)

    for _ in range(100):                       # the re-import runs on a thread
        texts = [f["text"] for f in res(g["_ml_facts_get"](Ctx()))[0].get("facts", [])]
        if "The owner lives in Lisbon" in texts:
            break
        time.sleep(0.05)
    check("the boot import re-ran: USER.md rows are back",
          "The owner lives in Lisbon" in texts and "The owner drinks green tea" in texts, texts)
    check("the deleted card fact is gone (it lived only in the store)",
          "Prefers window seats" not in texts, texts)

    blk = g["memlayer_block"]("do I keep a paper notebook", budget_chars=600)
    check("a turn's retrieval works again", "paper notebook" in blk.get("text", ""), blk)

    print("\n=== an emptied (zero-byte) store ===")
    io.open(db, "wb").close()
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(db + suffix)
        except OSError:
            pass
    body, code = res(g["_ml_facts_get"](Ctx()))
    check("a zero-byte memory.db is re-schema'd too", code == 200 and body.get("ok") is True, body)
finally:
    time.sleep(0.2)
    shutil.rmtree(tmp, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
sys.exit(1 if FAILS else 0)
