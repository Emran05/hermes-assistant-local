#!/usr/bin/env python3
"""B04 (docs/audits/2026-10-08-codebase-audit.md, P1): "Done" on a message
hid every future message from that contact.

BEFORE: Message Center items are one id per conversation
(`message:<handle>`).  _ny_build hid any item whose record had done_at set
and never compared it with received_at; nothing ever cleared done_at; and an
active conversation keeps refreshing last_seen so the record is never
pruned.  Mark Monday's "can you send it by 5pm?" Done and Tuesday's urgent
message from the same person never appeared.  Same for Snooze and
Reclassify -> never.

AFTER: on threaded sources a decision covers only what had arrived when it
was made: done_at >= received_at (and snoozed_at / reclassified_at the same
way) or it is cleared.  Your OWN reply bumping the row's timestamp does not
count as something new.  Non-threaded sources (calendar, reminders, ...) are
unchanged: their received_at is not an arrival time, so Done still hides
them for good.

Real aux_needsyou.py, throwaway HOME, explicit clocks, no model.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

# "Monday": noon two days ago, so "by 5pm" is always hours ahead of the
# message (the real clock put it seconds away around 17:00) and T0 stays
# before the act handler's real time.time() stamp
_lt = time.localtime()
T0 = time.mktime((_lt.tm_year, _lt.tm_mon, _lt.tm_mday, 12, 0, 0, 0, 0, -1)) - 2 * 86400
H = "+15550000021"
ID = "message:" + H


def act(g, action, iid=ID, **kw):
    body = {"id": iid, "action": action}
    body.update(kw)
    return g["_ny_act_handler"](E.Ctx(body=body))


def build(g, data, last, ts, now, from_me=False, unread=1):
    E.write_messages(data, [E.convo(H, last, ts, name="Dana Ruiz",
                                    from_me=from_me, unread=unread)],
                     generated_at=now)
    p = g["_ny_build"](now)
    g["_ny_cache"]["payload"] = p
    g["_ny_cache"]["at"] = now
    return p


print("=== B04: Done ===")
g, data = E.load()
p = build(g, data, "can you send it by 5pm?", T0, T0 + 60)
E.check("Monday's ask is listed", ID in E.visible_ids(p), E.visible_ids(p))
r = act(g, "done")
E.check("Done accepted", isinstance(r, dict) and r.get("ok"), r)
p = build(g, data, "can you send it by 5pm?", T0, T0 + 120)
E.check("Monday's ask is hidden after Done", ID not in E.visible_ids(p),
        E.visible_ids(p))
# the act handler stamps time.time(); Tuesday must be later than that
T1 = time.time() + 5
p = build(g, data, "URGENT: the landlord needs it now", T1, T1 + 60)
E.check("Tuesday's new message from the same person is listed",
        ID in E.visible_ids(p), E.visible_ids(p))
rec = g["_ny_load"]()["items"].get(ID) or {}
E.check("the superseded done_at is cleared in the store",
        not rec.get("done_at"), rec)

print("=== B04: your own reply does not reopen it ===")
g, data = E.load()
p = build(g, data, "can you send it by 5pm?", T0, T0 + 60)
act(g, "done")
T2 = time.time() + 5
p = build(g, data, "sent!", T2, T2 + 60, from_me=True, unread=1)
E.check("a reply from you (row ts bumped) stays filed",
        ID not in E.visible_ids(p), E.visible_ids(p))

print("=== B04: Snooze ===")
g, data = E.load()
p = build(g, data, "are you free later?", T0, time.time())
r = act(g, "snooze", until="1h")
E.check("Snooze accepted", isinstance(r, dict) and r.get("ok"), r)
now = time.time()
p = build(g, data, "are you free later?", T0, now + 60)
E.check("snoozed thread hidden while asleep", ID not in E.visible_ids(p),
        E.visible_ids(p))
p = build(g, data, "never mind, it's urgent — call me", now + 120, now + 180)
E.check("a NEW message inside the snooze window is listed",
        ID in E.visible_ids(p), E.visible_ids(p))

print("=== B04: Reclassify -> never ===")
g, data = E.load()
p = build(g, data, "are you free later?", T0, time.time())
r = act(g, "reclassify", to="never")
E.check("reclassify accepted", isinstance(r, dict) and r.get("ok"), r)
now = time.time()
p = build(g, data, "are you free later?", T0, now + 60)
E.check("re-filed thread stays out of view", ID not in E.visible_ids(p),
        E.visible_ids(p))
p = build(g, data, "can you call me back?", now + 120, now + 180)
E.check("a NEW message after the re-file is classified afresh",
        ID in E.visible_ids(p), E.visible_ids(p))

print("=== B04 (review): a follow-up the user never saw is not filed ===")
# Dana's 5pm ask is on screen; her follow-up reaches messages.json AFTER the
# last build but BEFORE the click.  Comparing done_at (click time) with
# received_at filed the follow-up with the ask it never saw.
for action, extra in (("done", {}), ("snooze", {"until": "1h"}),
                      ("reclassify", {"to": "never"})):
    g, data = E.load()
    t = time.time()
    p = build(g, data, "can you send it by 5pm?", t - 120, t - 115)
    E.write_messages(data, [E.convo(H, "actually call me asap", t - 30,
                                    name="Dana Ruiz")], generated_at=t - 30)
    r = act(g, action, **extra)
    E.check("%s accepted" % action, isinstance(r, dict) and r.get("ok"), r)
    p = g["_ny_build"](time.time())
    E.check("%s on the ask does not file the follow-up" % action,
            ID in E.visible_ids(p), E.visible_ids(p))

print("=== B04 (review): the client's `seen` wins when the cache is newer ===")
g, data = E.load()
t = time.time()
build(g, data, "can you send it by 5pm?", t - 120, t - 115)   # the screen
build(g, data, "actually call me asap", t - 30, t - 25)       # the cache now
r = act(g, "done", seen=t - 120)
E.check("Done with seen= accepted", isinstance(r, dict) and r.get("ok"), r)
p = g["_ny_build"](time.time())
E.check("the follow-up the screen never showed is listed",
        ID in E.visible_ids(p), E.visible_ids(p))
for name, bogus in (("a day ahead", t + 86400), ("Infinity", "Infinity"),
                    ("NaN", "NaN"), ("negative", -5)):
    g, data = E.load()
    build(g, data, "can you send it by 5pm?", t - 120, t - 115)
    act(g, "done", seen=bogus)
    p = build(g, data, "can you send it by 5pm?", t - 120, time.time())
    E.check("seen=%s cannot stretch the Done past what was served" % name,
            ID not in E.visible_ids(p), E.visible_ids(p))
    rec = g["_ny_load"]()["items"].get(ID) or {}
    E.check("seen=%s stores the served received_at" % name,
            rec.get("done_through") == t - 120, rec)

print("=== B04: a decision recorded before this fix (no *_at stamp) ===")
g, data = E.load()
now = time.time()
st = g["_ny_load"]()
st["items"][ID] = {"first_seen": T0, "last_seen": now, "source": "message",
                   "reclassified_to": "never"}
st["acts"].append({"ts": T0 + 30, "id": ID, "action": "reclassify",
                   "to": "never"})
g["_ny_save"](st)
p = build(g, data, "are you free later?", T0, now)
E.check("legacy re-file still covers the message it was made on",
        ID not in E.visible_ids(p), E.visible_ids(p))
p = build(g, data, "new: can you call me?", now + 60, now + 120)
E.check("...and not a later one", ID in E.visible_ids(p), E.visible_ids(p))

print("=== B04: non-threaded sources are unchanged ===")
g, data = E.load()
E.write_messages(data, [])


def fake_calendar(srcs=None, now=None):
    # the real collector's shape: received_at is the BUILD time, not an arrival
    g["_ny_mark"](srcs, "calendar", True)
    return [g["_ny_item"]("calendar", "evt-design", "Design review",
                          summary="in 30 min", sender="Calendar",
                          received_at=now, sender_tier="vip",
                          deadline_ts=now + 1800, kind="event", open_to="today")]


g["_NY_COLLECTORS"] = tuple((n, fake_calendar if n == "calendar" else f)
                            for n, f in g["_NY_COLLECTORS"])
p = g["_ny_build"](time.time())
g["_ny_cache"]["payload"] = p
cal = [i["id"] for i in p["now"] + p["today"] if i["source"] == "calendar"]
E.check("calendar fixture listed", len(cal) == 1, E.visible_ids(p))
if cal:
    act(g, "done", iid=cal[0])
    p = g["_ny_build"](time.time() + 60)   # calendar received_at == build time
    E.check("Done on a calendar event still hides it",
            cal[0] not in E.visible_ids(p), E.visible_ids(p))

sys.exit(E.finish())
