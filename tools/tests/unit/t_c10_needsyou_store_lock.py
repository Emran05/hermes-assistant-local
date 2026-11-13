#!/usr/bin/env python3
"""C10 (docs/audits/2026-10-21-codebase-audit.md): every needsyou.json
load -> save round trip outside the build was unlocked.

BEFORE: _ny_mark_shown, _ny_record_act, the act handler and the message
collector's own-store path each did `st = _ny_load(); ...; _ny_save(st)` with
only the final write under _ny_lock.  A second writer that landed between the
load and the save was silently overwritten: a Done click lost a sighting, a
sighting lost a Done's act row, two quick clicks lost one of their rows.

AFTER: each of them is one _ny_update() transaction — load, mutate and write
under the (re-entrant) lock — and the act handler writes the decision and its
act row in the SAME write.

Deterministic, not timing-based: the first _ny_load() of writer A starts
writer B on another thread and waits up to 0.5 s for B to finish before
returning A's (now stale) snapshot.  Unlocked code lets B finish inside that
window and A then saves over it; locked code makes B wait for A instead.
Real aux_needsyou.py, throwaway HOME.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()


def race(g, writer_a, writer_b):
    real = g["_ny_load"]
    a_thread = threading.current_thread()
    armed = [True]
    done = threading.Event()
    box = []

    def run_b():
        try:
            box.append(writer_b())
        finally:
            done.set()

    def hooked(*a, **kw):
        st = real(*a, **kw)
        if armed[0] and threading.current_thread() is a_thread:
            armed[0] = False
            threading.Thread(target=run_b, daemon=True).start()
            done.wait(0.5)
        return st

    g["_ny_load"] = hooked
    try:
        res_a = writer_a()
    finally:
        g["_ny_load"] = real
    done.wait(5)
    return res_a, (box[0] if box else None)


def fresh():
    g, data = E.load()
    E.write_messages(data, [
        E.convo("+15550000011", "can you call me back?", NOW - 900, name="Ana"),
        E.convo("+15550000012", "are you around later?", NOW - 600, name="Ben"),
    ])
    p = g["_ny_build"](NOW)
    g["_ny_cache"]["payload"] = p
    g["_ny_cache"]["at"] = time.time()
    return g


def act(g, iid, action, **kw):
    body = {"id": iid, "action": action}
    body.update(kw)
    return g["_ny_act_handler"](E.Ctx(body=body))


A, B = "message:+15550000011", "message:+15550000012"

print("=== C10: mark_shown vs a Done click ===")
g = fresh()
race(g, lambda: g["_ny_mark_shown"](g["_ny_cache"]["payload"], NOW),
     lambda: act(g, A, "done"))
st = g["_ny_load"]()
E.check("sightings recorded", len(st["shown"]) >= 2, st["shown"])
E.check("the Done that landed mid-mark survived",
        (st["items"].get(A) or {}).get("done_at", 0) > 0, st["items"].get(A))
E.check("...with its act row",
        any(a["id"] == A and a["action"] == "done" for a in st["acts"]), st["acts"])

print("=== C10: record_act vs record_act ===")
g = fresh()
race(g, lambda: g["_ny_record_act"](A, "open"),
     lambda: g["_ny_record_act"](B, "open"))
acts = [(a["id"], a["action"]) for a in g["_ny_load"]()["acts"]]
E.check("both act rows kept", (A, "open") in acts and (B, "open") in acts, acts)

print("=== C10: two act-handler clicks ===")
g = fresh()
ra, rb = race(g, lambda: act(g, A, "done"),
              lambda: act(g, B, "snooze", until="1h"))
E.check("both clicks answered ok",
        isinstance(ra, dict) and ra.get("ok") and isinstance(rb, dict)
        and rb.get("ok"), (ra, rb))
st = g["_ny_load"]()
E.check("Done kept", (st["items"].get(A) or {}).get("done_at", 0) > 0, st["items"].get(A))
E.check("Snooze kept",
        (st["items"].get(B) or {}).get("snoozed_until", 0) > time.time(),
        st["items"].get(B))
acts = [(a["id"], a["action"]) for a in st["acts"]]
E.check("both act rows kept", (A, "done") in acts and (B, "snooze") in acts, acts)

print("=== C10: the message collector's own-store history save ===")
g = fresh()
data = os.path.dirname(g["NY_STORE"])
E.write_messages(data, [
    E.convo("+15550000013", "sounds good", NOW - 50, name="Cy", from_me=True,
            unread=0)])
race(g, lambda: g["_ny_collect_message"]({}),
     lambda: g["_ny_record_act"](A, "open"))
st = g["_ny_load"]()
E.check("the reply history was saved",
        st["history"].get("+15550000013") == NOW - 50, st["history"])
E.check("...without erasing the act written meanwhile",
        any(a["id"] == A for a in st["acts"]), st["acts"])

print("=== P3: snooze `until` must be a real time ===")
g = fresh()
for bad in ("Infinity", "NaN", "inf", float("inf")):
    r = act(g, A, "snooze", until=bad)
    E.check("snooze until=%r refused with 400" % (bad,),
            isinstance(r, tuple) and r[1] == 400, r)
rec = g["_ny_load"]()["items"].get(A) or {}
E.check("nothing non-finite reached the store",
        rec.get("snoozed_until", 0) in (0, 0.0), rec)

print("=== P3: a re-file into Now keeps the five-item cap ===")
g = fresh()
cur = g["_ny_cache"]["payload"]
filler = []
for i in range(5):
    filler.append({"id": "calendar:f%d" % i, "source": "calendar",
                   "bucket": "now", "reason": "r"})
cur["now"] = filler
cur["today"] = [{"id": "approval:z", "source": "approval", "bucket": "today",
                 "reason": "r"}]
r = act(g, "approval:z", "reclassify", to="now")
E.check("reclassify ok", isinstance(r, dict) and r.get("ok"), r)
E.check("now still holds at most five", len(cur["now"]) == 5, len(cur["now"]))
E.check("the re-filed row is in now", cur["now"][0]["id"] == "approval:z",
        [i["id"] for i in cur["now"]])
E.check("the overflow fell to today rather than vanishing",
        any(i["id"] == "calendar:f4" for i in cur["today"]),
        [i["id"] for i in cur["today"]])

sys.exit(E.finish())
