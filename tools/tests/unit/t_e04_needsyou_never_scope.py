#!/usr/bin/env python3
"""E04 review (R4-c06): the `never` rows the E04 fix added to the payload
carry message previews that used to travel only as a count.  GET
/api/needsyou is read by the MCP server's `needs_you` tool (which filters
message rows out of now/today/later when `messages_search` is off, and knows
nothing of a `never` list), by scripts and by the hub widget's reload, none
of which draws them.  So the list is served only to a caller that asks for
it (`?never=1`) and to the pop-out (/api/expand); every other GET keeps the
old shape: the true `never_count` and no rows.

Drives the REAL dashboard/aux_needsyou.py through _ny_env (offline).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
g, data = E.load()
E.write_messages(data, [
    E.convo("262966", "Your verification code is 838201", NOW - 30),
    E.convo("no-reply@shop.example", "Your order has shipped", NOW - 40),
], generated_at=NOW)
p = g["_ny_build"](NOW)
g["_ny_cache"]["payload"] = p
g["_ny_cache"]["at"] = time.time()
get = g["_ny_get_handler"]

print("=== E04 review: GET /api/needsyou does not list never rows by default ===")
for label, q in (("plain GET", {}), ("?mark=0 (the MCP read)", {"mark": ["0"]}),
                 ("?never=0", {"never": ["0"], "mark": ["0"]}),
                 ("?never=yes-please", {"never": ["yes-please"]})):
    d = get(E.Ctx(query=q))
    E.check("%s: no never rows" % label, "never" not in d, sorted(d.keys()))
    E.check("%s: never_count still true" % label, d.get("never_count") == 2,
            d.get("never_count"))
d = get(E.Ctx(query={"never": ["1"], "mark": ["0"]}))
E.check("?never=1: the rows are listed", len(d.get("never") or []) == 2,
        d.get("never"))
E.check("the cached payload keeps its list",
        len(g["_ny_cache"]["payload"].get("never") or []) == 2)
E.check("the pop-out still lists them",
        len(g["expand_needsyou"]().get("never") or []) == 2)
cold = dict(g["_ny_cache"])
g["_ny_cache"]["payload"] = None
d = get(E.Ctx(query={"mark": ["0"]}))
E.check("a cold (building) answer has no never key either", "never" not in d,
        sorted(d.keys()))
g["_ny_cache"].update(cold)
sys.exit(E.finish())
