#!/usr/bin/env python3
"""C105 (audit 2026-10-21): a fact the importer archived as STALE comes back
when its position holds a line again.

Imported facts are keyed by file + ordinal. When a line disappears the
reconciler archives its row; `_ml_add` never cleared `archived` for an existing
key, so the next entry at that position (a new goal) or the very same line
restored from a snapshot reused the archived row and was silently excluded
from retrieval and from the Needs-review list. The fix records WHY a row is
archived (owner vs stale) and resurrects only the stale kind — an archive the
owner chose is still never undone by an importer.

Execs the REAL aux_memlayer.py into a fake-server globals dict against a
throwaway HOME/DATA (the test_memlayer.py pattern). No model, no network.
"""
import io
import os
import re
import shutil
import sys
import tempfile

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
DELIM = "\n§\n"

_CV_SECRET_RE = re.compile(r"\b(token|api[_-]?key|secret|password)\b\s*[:=]\s*\S{6,}", re.I)


def _cv_redact_raw(text):
    return re.sub(r"\bsk-ant-[A-Za-z0-9_-]{20,}", "[redacted]", text)


class Ctx:
    def __init__(self, query=None, body=None):
        self.query = query or {}
        self.body = body if body is not None else {}

    def q1(self, k, d=""):
        v = self.query.get(k)
        return v[0] if isinstance(v, list) and v else d


sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402


def build(tmp, memories):
    home = os.path.join(tmp, "home")
    data = os.path.join(home, ".hermes", "dashboard")
    memdir = os.path.join(home, ".hermes", "memories")
    os.makedirs(data, exist_ok=True)
    os.makedirs(os.path.join(memdir, "people"), exist_ok=True)
    for name, text in memories.items():
        io.open(os.path.join(memdir, name), "w", encoding="utf-8").write(text)
    settings_file = os.path.join(data, "settings.json")
    # the REAL server.py settings helpers and its NON-reentrant lock (2026-11-09
    # audit E138; an RLock stand-in would hide a re-entrant deadlock, C84)
    api = server_real.settings_api(settings_file)

    g = {"__name__": "aux_memlayer_c105", "HOME": home, "DATA": data,
         "HERE": os.path.dirname(AUX), "IX_DB": os.path.join(data, "index.db"),
         "_state_lock": api["_state_lock"], "read_json": api["read_json"],
         "write_json": api["write_json"],
         "settings_update": api["settings_update"],
         "get_settings": api["get_settings"],
         "register_get": lambda p, f: None, "register_post": lambda p, f: None,
         "_cv_redact_raw": _cv_redact_raw, "_CV_SECRET_RE": _CV_SECRET_RE}
    exec(compile(io.open(AUX, encoding="utf-8").read(), AUX, "exec"), g)
    return g, memdir


def facts(g):
    return {f["text"]: f for f in
            g["_ml_facts_get"](Ctx(query={"include_archived": ["1"]}))["facts"]}


def injected(g, q):
    return g["memlayer_block"](q, budget_chars=4000)["text"]


tmp = tempfile.mkdtemp(prefix="hermes-c105-")
try:
    g, memdir = build(tmp, {"NOW.md": DELIM.join(
        ["ship the atlas release", "write the bramble essay", "plan the kestrel trip"])})
    now_md = os.path.join(memdir, "NOW.md")
    g["_ml_facts_update"](Ctx(body={"approve_all": True}))
    check("three imported entries, approved",
          sorted(f["review"] for f in facts(g).values()) == [False] * 3, facts(g))

    print("\n1. a line removed from the file is archived as stale")
    io.open(now_md, "w", encoding="utf-8").write(
        DELIM.join(["ship the atlas release", "write the bramble essay"]))
    c = g["memlayer_import"]()
    check("one stale", c["stale"] == 1, c)
    check("it is archived", facts(g)["plan the kestrel trip"]["archived"] is True)

    print("\n2. the SAME line restored (snapshot undo) comes back live")
    io.open(now_md, "w", encoding="utf-8").write(DELIM.join(
        ["ship the atlas release", "write the bramble essay", "plan the kestrel trip"]))
    c = g["memlayer_import"]()
    check("reported as restored", c.get("restored") == 1, c)
    f = facts(g)["plan the kestrel trip"]
    check("no longer archived", f["archived"] is False, f)
    check("its approval survives (identical text the owner already approved)",
          f["review"] is False, f)
    check("and it is injected again", "kestrel" in injected(g, "kestrel trip plans"),
          injected(g, "kestrel trip plans"))

    print("\n3. a NEW line at a stale position is born live, waiting for review")
    io.open(now_md, "w", encoding="utf-8").write(
        DELIM.join(["ship the atlas release", "write the bramble essay"]))
    g["memlayer_import"]()
    io.open(now_md, "w", encoding="utf-8").write(DELIM.join(
        ["ship the atlas release", "write the bramble essay", "learn the zither"]))
    c = g["memlayer_import"]()
    rows = facts(g)
    f = rows.get("learn the zither") or {}
    check("the new text landed", bool(f), sorted(rows))
    check("not archived", f.get("archived") is False, f)
    check("held for review (new text from a writable file)", f.get("review") is True, f)
    check("and counted as waiting for review",
          c.get("needs_review") == 1 and
          g["memlayer_stats"]()["review"] == 1, (c, g["memlayer_stats"]()))
    g["_ml_facts_update"](Ctx(body={"id": f["id"], "approve": True}))
    check("approved -> injected", "zither" in injected(g, "learn zither"),
          injected(g, "learn zither"))

    print("\n4. an archive the OWNER chose is never undone by an import")
    fid = facts(g)["write the bramble essay"]["id"]
    g["_ml_facts_update"](Ctx(body={"id": fid, "archived": True}))
    c = g["memlayer_import"]()
    check("owner-archived row stays archived on re-import",
          facts(g)["write the bramble essay"]["archived"] is True, c)
    io.open(now_md, "w", encoding="utf-8").write(DELIM.join(
        ["ship the atlas release", "write the bramble essay, edited", "learn the zither"]))
    c = g["memlayer_import"]()
    f = facts(g)["write the bramble essay, edited"]
    check("an edit to an owner-archived line keeps it archived", f["archived"] is True, f)
    check("and is not reported as waiting for review (it is hidden)",
          c.get("needs_review") == 0, c)
    check("stats agree: archived rows are counted",
          g["memlayer_stats"]()["archived"] >= 1, g["memlayer_stats"]())
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
sys.exit(1 if FAILS else 0)
