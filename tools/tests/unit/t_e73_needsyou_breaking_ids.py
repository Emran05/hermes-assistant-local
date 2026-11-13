#!/usr/bin/env python3
"""E73 (docs/audits/2026-11-09-codebase-audit.md): two breaking alerts sent
in the same watchtower pass (one `news`, one `ai` — every row of one
_breaking_pass carries the same `ts`) both became `watchtower:brk-<int ts>`,
so a Done on one silently hid the other.

AFTER: the id is keyed on the row's `signature` (unique per story); a row
without one keeps the old timestamp id.

Drives the REAL dashboard/aux_needsyou.py through _ny_env (offline).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
TS = NOW - 120
g, data = E.load()
rows = [
    {"ts": TS, "rule_id": "", "type": "breaking_news", "label": "Breaking · news",
     "signature": "news|https://example.test/a", "suppressed": "",
     "delivered": ["telegram", "hub"], "text": "Story A",
     "context": {"title": "Story A", "url": "https://example.test/a"}},
    {"ts": TS, "rule_id": "", "type": "breaking_ai", "label": "Breaking · ai",
     "signature": "ai|https://example.test/b", "suppressed": "",
     "delivered": ["telegram", "hub"], "text": "Story B",
     "context": {"title": "Story B", "url": "https://example.test/b"}},
]
with open(os.path.join(data, "watchtower-log.jsonl"), "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")

p = g["_ny_build"](NOW)
ids = [i["id"] for i in (p["now"] + p["today"] + p["later"])
       if i["source"] == "watchtower"]
E.check("two breaking rows collected", len(ids) == 2, ids)
E.check("...with two different ids", len(set(ids)) == 2, ids)
E.check("ids are bounded and plain",
        all(len(i) <= 60 and i.startswith("watchtower:brk-") for i in ids), ids)
g["_ny_cache"]["payload"] = p
g["_ny_cache"]["at"] = time.time()
r = g["_ny_act_handler"](E.Ctx(body={"id": ids[0], "action": "done"}))
E.check("Done on the first", r.get("ok") is True, r)
p2 = g["_ny_build"](NOW + 1)
left = [i["id"] for i in p2["now"] + p2["today"] + p2["later"]
        if i["source"] == "watchtower"]
E.check("the other story is still listed", left == [ids[1]], left)
E.check("done_count is 1", p2["done_count"] == 1, p2["done_count"])

p3 = g["_ny_build"](NOW + 2)
again = sorted(i["id"] for i in p3["now"] + p3["today"] + p3["later"]
               if i["source"] == "watchtower")
E.check("the id is stable across builds", again == [ids[1]], again)

print("=== a row without a signature keeps a timestamp id ===")
g, data = E.load()
with open(os.path.join(data, "watchtower-log.jsonl"), "w") as f:
    f.write(json.dumps({"ts": TS, "type": "breaking_news", "suppressed": "",
                        "text": "x", "context": {"title": "x"}}) + "\n")
p = g["_ny_build"](NOW)
ids = [i["id"] for i in p["today"] if i["source"] == "watchtower"]
E.check("old shape still collected", ids == ["watchtower:brk-%d" % int(TS)], ids)

sys.exit(E.finish())
