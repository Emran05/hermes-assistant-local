#!/usr/bin/env python3
"""C60 (docs/audits/2026-10-21-codebase-audit.md): a resolved contact asking
by a stated deadline landed in Today with the false reason "nothing is due
at a set time".

BEFORE: §4b (and this module's own header) define `now` as VIP — two-way
history <=30 days OR a resolved contact — AND a concrete time-bound ask.  The
rule only ever read the history half: a contact with no recent reply is
tier "known", and every known/unknown ask fell to one branch whose reason
claimed there was no deadline even when the message named one.

AFTER: a contact asking for something by a stated time still ahead is Now
(0.8); a bare question from a contact stays Today; and every Today reason
says what the message actually did ("by a set time", "the time it named has
passed", "more than a day away", or genuinely "nothing is due").

Through the REAL collector (tiering, ask and deadline parsing) and the REAL
classifier.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

# today at local noon, never the real clock: received at NOW - 600, "by 5pm"
# must still be ahead (on the real clock 17:00-17:10 parsed it as already past)
_lt = time.localtime()
NOW = time.mktime((_lt.tm_year, _lt.tm_mon, _lt.tm_mday, 12, 0, 0, 0, 0, -1))
g, data = E.load()
E.write_messages(data, [
    E.convo("+15550000041", "can you send the signed lease by 5pm?", NOW - 600,
            name="Dana Ruiz"),
    E.convo("+15550000042", "are you free thursday?", NOW - 600,
            name="Marco Bell"),
    E.convo("+15550000043", "can you call me in 2 hours?", NOW - 600),
    E.convo("+15550000044", "landing in 30 minutes", NOW - 60, name="Priya Shah"),
])
items = {it["id"]: it for it in g["_ny_collect_message"]({}, None, NOW)}
C = lambda iid: g["_ny_classify"](items["message:" + iid], NOW)   # noqa: E731

print("=== C60: a contact asking by a stated time ===")
it = items.get("message:+15550000041") or {}
E.check("fixture: a resolved contact with no reply history",
        it.get("sender_tier") == "known", it.get("sender_tier"))
E.check("fixture: the deadline was parsed", it.get("deadline_ts", 0) > NOW, it)
v = C("+15550000041")
E.check("contact + ask + deadline ahead -> now", v["bucket"] == "now", v)
E.check("the reason does not deny the deadline",
        "nothing is due" not in v["reason"], v)

print("=== C60: what stays in today, and says so truthfully ===")
v = C("+15550000042")
E.check("contact + bare question -> today", v["bucket"] == "today", v)
E.check("...'nothing is due at a set time' is TRUE here",
        "nothing is due at a set time" in v["reason"], v)
v = C("+15550000043")
E.check("no history, not a contact, asks by a time -> today",
        v["bucket"] == "today", v)
E.check("...and the reason names the time instead of denying it",
        "nothing is due" not in v["reason"] and "set time" in v["reason"], v)
v = C("+15550000044")
E.check("a contact who asks nothing -> today", v["bucket"] == "today", v)
E.check("...without claiming you replied in that thread",
        "replied" not in v["reason"], v)

mk = g["_ny_item"]
for label, kw in (("urgency language", {"deadline_lang": True}),
                  ("a time ahead", {"deadline_ts": NOW + 1800})):
    it = mk("message", "+15550000047", "Lee Park", summary="need the signed copy",
            received_at=NOW - 60, sender_tier="known", **kw)
    v = g["_ny_classify"](it, NOW)
    E.check("a contact with %s and no ask -> today" % label,
            v["bucket"] == "today", v)
    E.check("...without claiming nothing is time-bound (%s)" % label,
            "time" in v["reason"] and "nothing asked of you" not in v["reason"], v)

grp = mk("message", "chat-soccer", "Soccer Parents",
          summary="can anyone bring the snacks by 5pm?", sender="+15551234567",
          received_at=NOW - 600, sender_tier="known", requires_reply=True,
          deadline_ts=NOW + 3600, kind="group")
v = g["_ny_classify"](grp, NOW)
E.check("a named GROUP asking by a time is not 'a contact asking' -> today",
        v["bucket"] == "today" and "contact" not in v["reason"], v)

print("=== C60: a VIP Now reason says what the message did ===")
mk = g["_ny_item"]
vip_urgent = mk("message", "+15550000045", "Sam Ito", summary="urgent: the server is down",
                received_at=NOW - 60, sender_tier="vip", deadline_lang=True)
v = g["_ny_classify"](vip_urgent, NOW)
E.check("VIP + urgency, no ask -> now", v["bucket"] == "now", v)
E.check("...not described as asking, nor as naming a time",
        "asking" not in v["reason"] and "named a time" not in v["reason"], v)
vip_eta = mk("message", "+15550000046", "Sam Ito", summary="landing in 30 minutes",
             received_at=NOW - 60, sender_tier="vip", deadline_ts=NOW + 1740)
v = g["_ny_classify"](vip_eta, NOW)
# B09 review (round 2): a parsed time is not an ask — "landing in 30
# minutes" names a time and asks nothing, so it is Today, and says why
E.check("VIP + a time ahead, no ask -> today, 'named a time'",
        v["bucket"] == "today" and "named a time" in v["reason"]
        and "asks nothing" in v["reason"], v)

print("=== C60: built payload ===")
p = g["_ny_build"](NOW)
E.check("the contact's deadline ask is in Now",
        "message:+15550000041" in [i["id"] for i in p["now"]],
        [i["id"] for i in p["now"]])

sys.exit(E.finish())
