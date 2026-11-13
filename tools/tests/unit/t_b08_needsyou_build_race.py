#!/usr/bin/env python3
"""B08 (docs/audits/2026-10-08-codebase-audit.md): the Needs-you build
overwrote Done / Snooze / act rows written while it ran.

BEFORE: _ny_build loaded needsyou.json, ran every collector (osascript,
icalBuddy, the optional 20 s model pass), then saved the WHOLE object it had
loaded.  _ny_lock covered only the final write, so a click that landed while
the collectors ran was erased: the item came back, and its act row (the
numerator of now_precision) was gone.

AFTER: the build applies the user's decisions inside _ny_update(), i.e. on
the store as it is NOW under the lock; only the reply history the collectors
gathered is merged over from the early snapshot.

The act is injected from inside the calendar collector, which is exactly the
window the audit names.  Real aux_needsyou.py, throwaway HOME, no thread, no
model.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
g, data = E.load()
E.write_messages(data, [
    E.convo("+15550000001", "can you call me back?", NOW - 900, name="Ana Lee"),
    E.convo("+15550000002", "are you around later?", NOW - 600, name="Ben Ode"),
])

print("=== B08: a decision made while the build runs survives it ===")
p0 = g["_ny_build"](NOW)
g["_ny_cache"]["payload"] = p0
g["_ny_cache"]["at"] = time.time()
ids = E.visible_ids(p0)
E.check("fixture yields both conversations",
        "message:+15550000001" in ids and "message:+15550000002" in ids, ids)

answers = []


def calendar_with_a_click_inside():
    # the user acts while the build is still collecting
    Ctx = E.Ctx
    answers.append(g["_ny_act_handler"](Ctx(body={
        "id": "message:+15550000001", "action": "done"})))
    answers.append(g["_ny_act_handler"](Ctx(body={
        "id": "message:+15550000002", "action": "snooze", "until": "1h"})))
    return {"available": True, "events": []}


g["macos_calendar"] = calendar_with_a_click_inside
p1 = g["_ny_build"](NOW + 5)
g["macos_calendar"] = lambda: {"available": True, "events": []}

E.check("both clicks were accepted",
        all(isinstance(a, dict) and a.get("ok") for a in answers), answers)
st = g["_ny_load"]()
rec1 = st["items"].get("message:+15550000001") or {}
rec2 = st["items"].get("message:+15550000002") or {}
E.check("Done written mid-build is still on disk", rec1.get("done_at", 0) > 0, rec1)
E.check("Snooze written mid-build is still on disk",
        rec2.get("snoozed_until", 0) > time.time(), rec2)
acts = [(a.get("id"), a.get("action")) for a in st["acts"]]
E.check("both act rows survived the build",
        ("message:+15550000001", "done") in acts
        and ("message:+15550000002", "snooze") in acts, acts)
vis = E.visible_ids(p1)
E.check("the build that was running honours the Done",
        "message:+15550000001" not in vis, vis)
E.check("...and the Snooze", "message:+15550000002" not in vis, vis)
E.check("done/snoozed counted in that payload",
        p1.get("done_count") == 1 and p1.get("snoozed_count") == 1,
        (p1.get("done_count"), p1.get("snoozed_count")))

print("=== B08: the reply history the collectors saw is still merged ===")
E.write_messages(data, [
    E.convo("+15550000003", "sounds good", NOW - 100, name="Cy", from_me=True,
            unread=0),
])


def calendar_with_a_history_write():
    g["_ny_record_act"]("x:1", "open")
    return {"available": True, "events": []}


g["macos_calendar"] = calendar_with_a_history_write
g["_ny_build"](NOW + 10)
g["macos_calendar"] = lambda: {"available": True, "events": []}
st = g["_ny_load"]()
E.check("reply seen by the collector lands in history",
        st["history"].get("+15550000003") == NOW - 100, st["history"])
E.check("...without losing the act written meanwhile",
        any(a.get("id") == "x:1" for a in st["acts"]), st["acts"])

sys.exit(E.finish())
