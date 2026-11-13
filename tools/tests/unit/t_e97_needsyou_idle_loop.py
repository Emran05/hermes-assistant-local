#!/usr/bin/env python3
"""E97 (docs/audits/2026-11-09-codebase-audit.md): Needs-you rebuilt every
60 s forever — a full collector pass whether or not anyone was looking or the
widget was even on the hub — and fsync-rewrote needsyou.json on every build,
even when nothing had changed (C53 fixed exactly this for hub prewarm).

AFTER:
  * a build whose store transaction changed nothing writes nothing
    (`last_seen` is refreshed at most once an hour, which is all the 14-day
    prune needs);
  * the background loop rebuilds only for its first pass and then only while
    a reader (the hub widget, the route, the pop-out) asked within
    NY_CLIENT_IDLE_S;
  * the brief / midday / evening line (watchtower's _wt_needsyou_line)
    refreshes a stale cache itself first, so its counts stay current while
    the loop is idle.

Drives the REAL aux_needsyou.py (_ny_env) and the REAL aux_watchtower.py's
_wt_needsyou_line (_wt_harness) — offline.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
g, data = E.load()
writes = [0]
real_write = g["_ny_write_store"]


def counting(obj):
    writes[0] += 1
    return real_write(obj)


g["_ny_write_store"] = counting
E.write_messages(data, [E.convo("+15550000001", "can you call me?", NOW - 60,
                                name="Ana")], generated_at=NOW)

print("=== E97: an unchanged rebuild writes nothing ===")
g["_ny_build"](NOW)
E.check("first build writes the store", writes[0] == 1, writes[0])
g["_ny_build"](NOW + 60)
E.check("a rebuild a minute later with nothing new writes nothing",
        writes[0] == 1, writes[0])
g["_ny_build"](NOW + 120)
E.check("...nor the one after", writes[0] == 1, writes[0])
E.write_messages(data, [E.convo("+15550000001", "can you call me?", NOW - 60,
                                name="Ana"),
                        E.convo("+15550000002", "lunch?", NOW + 150,
                                name="Bo")], generated_at=NOW + 150)
g["_ny_build"](NOW + 180)
E.check("a new message is written", writes[0] == 2, writes[0])
g["_ny_build"](NOW + 180 + 3700)
E.check("last_seen is refreshed after an hour", writes[0] == 3, writes[0])
rec = g["_ny_load"]()["items"]["message:+15550000002"]
E.check("...to that build's time", abs(rec["last_seen"] - (NOW + 3880)) < 1,
        rec)
p = g["_ny_build"](NOW + 3900)
g["_ny_cache"]["payload"] = p
g["_ny_cache"]["at"] = time.time()
n = writes[0]
r = g["_ny_act_handler"](E.Ctx(body={"id": "message:+15550000001",
                                      "action": "done"}))
E.check("a Done is still written", r.get("ok") and writes[0] == n + 1,
        (r, writes[0]))
g["_ny_build"](NOW + 3960)
E.check("...and the rebuild after it keeps it hidden",
        "message:+15550000001" not in E.visible_ids(g["_ny_build"](NOW + 3970)))

print("=== E97: the loop rebuilds only while someone is reading ===")
built = [0]
g["_ny_refresh"] = lambda: built.__setitem__(0, built[0] + 1)
g["_ny_cache"]["client"] = 0.0
E.check("the first pass always builds", g["_ny_loop_tick"](True) is True
        and built[0] == 1, built)
E.check("no reader -> no build", g["_ny_loop_tick"](False) is False
        and built[0] == 1, built)
g["_ny_cache"]["client"] = time.time() - g["NY_CLIENT_IDLE_S"] - 5
E.check("a reader gone past the idle window -> no build",
        g["_ny_loop_tick"](False) is False and built[0] == 1, built)
g["w_needsyou"]()
E.check("the hub widget's provider counts as a reader",
        g["_ny_loop_tick"](False) is True and built[0] == 2, built)
g["_ny_cache"]["client"] = 0.0
g["_ny_get_handler"](E.Ctx(query={"mark": ["0"]}))
E.check("GET /api/needsyou counts as a reader",
        g["_ny_loop_tick"](False) is True and built[0] == 3, built)
g["_ny_cache"]["client"] = 0.0
g["expand_needsyou"]()
E.check("the pop-out counts as a reader",
        g["_ny_loop_tick"](False) is True and built[0] == 4, built)

print("=== E97: the brief's line refreshes a stale cache itself ===")
g, data = E.load()
E.write_messages(data, [E.convo("+15550000001", "can you call me?", NOW - 60,
                                name="Ana")], generated_at=NOW)
# a cache built an hour ago, before Ana's message: the loop has been idle
g["_ny_cache"]["payload"] = {"ok": True, "now": [], "today": [], "later": [],
                             "sources": {}}
g["_ny_cache"]["at"] = time.time() - 3600
import _wt_harness as H                                         # noqa: E402
wg, _wh, _wd = H.load()
for k in ("_ny_payload", "_ny_fresh"):
    if k in g:
        wg[k] = g[k]
line = wg["_wt_needsyou_line"]()
E.check("the line reflects the current inbox, not the hour-old cache",
        "Needs you:" in line and ("1 now" in line or "1 today" in line), repr(line))
E.check("the refresh replaced the cache",
        time.time() - g["_ny_cache"]["at"] < 30, g["_ny_cache"]["at"])
E.check("...and recorded no sighting", g["_ny_load"]()["shown"] == {})
at = g["_ny_cache"]["at"]
wg["_wt_needsyou_line"]()
E.check("a fresh cache is not rebuilt", g["_ny_cache"]["at"] == at)
g["_ny_cache"]["payload"] = None
E.check("a cold cache (first boot) is left to the loop",
        wg["_wt_needsyou_line"]() == "" and g["_ny_cache"]["payload"] is None)

sys.exit(E.finish())
