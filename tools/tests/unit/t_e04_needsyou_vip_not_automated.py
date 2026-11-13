#!/usr/bin/env python3
"""E04 (docs/audits/2026-11-09-codebase-audit.md): a text from someone the
owner replied to yesterday was classed `automated` -> `never` because the
"automated sender" regex ran over the message BODY before reply history was
read ("did you see the school newsletter?", "out for delivery — can you grab
it?", "is your garage code still 1234?").  And `never` rows travelled only as
a count: no screen listed them, so nothing could re-file one.

AFTER:
  * the sender's HANDLE (short code, no-reply style address) decides
    `automated` first; then reply history / a resolved contact decide
    vip/known; the body words count only for a sender with no history.
    Same order in the email collector.
  * the payload carries the `never` rows (newest first, capped) with the
    true `never_count`; the hub widget's payload does not; a plain GET and
    the pop-out never record a `never` row as SHOWN — only the beacon can,
    for a row the pop-out actually drew; a `never` row can be re-filed.

Drives the REAL dashboard/aux_needsyou.py through _ny_env (offline).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()

# the verifier's 22 ordinary texts from family and friends (verify-ap1/v1.py)
TEXTS = [
    "Did you see the school newsletter? Can you sign the slip?",
    "out for delivery - can you grab it off the porch?",
    "Did you get my notification? I tagged you in the photos",
    "Turn off notifications on the group chat lol, are you coming Saturday?",
    "What's your door code? I'm outside",
    "Is your garage code still 1234?",
    "Hey your zip code is 94110 right? Sending the invite",
    "Can you send me the verification code you just got? I'm logging into Netflix",
    "The pizza is out for delivery, be home by 7?",
    "I can't find the unsubscribe link for that gym, can you help me tonight?",
    "Amazon says my order shipped, can you watch for it tomorrow?",
    "Got a notification from the school - pickup is at 2 today, can you do it?",
    "Bank says verify your identity, is that a scam?",
    "Are you free for dinner Friday?",
    "Call me when you land",
    "Did you pay the electric bill? It's due tomorrow",
    "Mom's surgery is at 3pm, can you drive her?",
    "Can you pick up milk on the way home?",
    "The alert on my phone says tornado warning, are you ok?",
    "I'll confirm your appointment with the dentist, what time works?",
    "Do you still have the promo code for the tickets?",
    "your discount code is in the email I forwarded, did you get it?",
]

print("=== E04: a replied-to sender is never `automated` on body words ===")
g, data = E.load()
hist = {}
convos = []
for i, t in enumerate(TEXTS):
    ident = "+1555123%04d" % i
    hist[ident] = NOW - 86400                 # owner replied yesterday -> VIP
    convos.append(E.convo(ident, t, NOW - 60 * (i + 1), name="+1555123%04d" % i))
with open(os.path.join(data, "needsyou.json"), "w") as f:
    json.dump({"version": 1, "items": {}, "history": hist, "shown": {},
               "acts": []}, f)
E.write_messages(data, convos, generated_at=NOW)
p = g["_ny_build"](NOW)
st = g["_ny_load"]()
nev = [i for i in range(len(TEXTS))
       if (st["items"].get("message:+1555123%04d" % i) or {}).get("bucket") == "never"]
E.check("none of the 22 replied-to texts lands in never", nev == [],
        [TEXTS[i] for i in nev])
E.check("never_count is 0 for them", p["never_count"] == 0, p["never_count"])
tiers = set(it["sender_tier"] for b in ("now", "today", "later") for it in p[b])
E.check("every one is a vip row", tiers == {"vip"}, tiers)
E.check("all 22 are on a visible list", len(E.visible_ids(p)) == 22,
        len(E.visible_ids(p)))

print("=== E04: the sender-tier order, case by case ===")
tier = g["_ny_sender_tier"]
H = {"+15550000001": NOW - 3600}
E.check("vip + 'newsletter' -> vip",
        tier({"ident": "+15550000001", "name": "+15550000001",
              "last": "school newsletter?"}, H, NOW) == "vip")
E.check("from_me row + 'verification code' -> vip",
        tier({"ident": "+15550000002", "name": "+15550000002", "from_me": True,
              "ts": NOW - 60, "last": "here is the verification code"}, {}, NOW)
        == "vip")
E.check("contact, no history + 'out for delivery' -> known",
        tier({"ident": "+15550000003", "name": "Dana Ruiz",
              "last": "pizza is out for delivery"}, {}, NOW) == "known")
E.check("short code 2FA -> automated",
        tier({"ident": "262966", "name": "262966",
              "last": "Your verification code is 838201"}, {}, NOW)
        == "automated")
E.check("short code even with 'history' -> automated (handle first)",
        tier({"ident": "262966", "name": "262966", "last": "hi"},
             {"262966": NOW - 60}, NOW) == "automated")
E.check("no-reply handle, unknown -> automated",
        tier({"ident": "no-reply@shop.example", "name": "no-reply@shop.example",
              "last": "Your order has shipped"}, {}, NOW) == "automated")
E.check("noreply@ handle WITH a reply in history -> automated",
        tier({"ident": "noreply@bank.example", "name": "noreply@bank.example",
              "last": "statement"}, {"noreply@bank.example": NOW - 60}, NOW)
        == "automated")
E.check("alerts@ handle -> automated",
        tier({"ident": "alerts@bank.example", "name": "alerts@bank.example",
              "last": "Low balance"}, {}, NOW) == "automated")
E.check("unknown number, no history, shipping words -> automated",
        tier({"ident": "+15559990000", "name": "+15559990000",
              "last": "Your order shipped. Reply STOP to unsubscribe"}, {}, NOW)
        == "automated")
E.check("unknown number, no history, plain text -> unknown",
        tier({"ident": "+15559990001", "name": "+15559990001",
              "last": "hey it's me"}, {}, NOW) == "unknown")
E.check("a reply older than 30 days is no history -> body counts",
        tier({"ident": "+15559990002", "name": "+15559990002",
              "last": "newsletter: 50% off"},
             {"+15559990002": NOW - 40 * 86400}, NOW) == "automated")
E.check("group, no history + 'unsubscribe' -> automated",
        tier({"ident": "chat1", "name": "chat1", "group": True, "sender": "x",
              "last": "unsubscribe"}, {}, NOW) == "automated")
E.check("group you replied in + 'notification' -> vip",
        tier({"ident": "chat2", "name": "chat2", "group": True, "sender": "x",
              "last": "got a notification"}, {"chat2": NOW - 60}, NOW) == "vip")

print("=== E04: genuinely automated texts still go to never ===")
g, data = E.load()
E.write_messages(data, [
    E.convo("262966", "Your verification code is 838201", NOW - 30),
    E.convo("no-reply@shop.example", "Your order has shipped and is out for "
            "delivery", NOW - 40),
    E.convo("+15550000077", "Did you see the school newsletter? Can you "
            "sign the slip?", NOW - 50, name="Ana"),
], generated_at=NOW)
p = g["_ny_build"](NOW)
st = g["_ny_load"]()
E.check("short-code 2FA -> never",
        st["items"]["message:262966"]["bucket"] == "never",
        st["items"]["message:262966"])
E.check("no-reply shipping notice -> never",
        st["items"]["message:no-reply@shop.example"]["bucket"] == "never",
        st["items"]["message:no-reply@shop.example"])
E.check("a contact's newsletter question is not never",
        st["items"]["message:+15550000077"]["bucket"] != "never",
        st["items"]["message:+15550000077"])

print("=== E04: never rows are listed for the pop-out, and re-fileable ===")
E.check("payload lists the never rows", isinstance(p.get("never"), list)
        and sorted(i["id"] for i in p["never"]) ==
        ["message:262966", "message:no-reply@shop.example"], p.get("never"))
E.check("never rows carry a reason", all(i.get("reason") for i in p["never"]))
E.check("never rows are newest first",
        [i["id"] for i in p["never"]] ==
        ["message:262966", "message:no-reply@shop.example"])
E.check("never_count still the true count", p["never_count"] == 2)
g["_ny_cache"]["payload"] = p
g["_ny_cache"]["at"] = time.time()
w = g["w_needsyou"]()
E.check("the hub widget's payload carries only the count",
        "never" not in w and w["never_count"] == 2, sorted(w.keys()))
E.check("...and the cached payload keeps the list",
        isinstance(g["_ny_cache"]["payload"].get("never"), list))


def shown_ids():
    return set(k.rsplit("|", 1)[0] for k in g["_ny_load"]()["shown"])


g["_ny_get_handler"](E.Ctx(query={}))
E.check("a plain GET never records a never row as shown",
        not (shown_ids() & {"message:262966", "message:no-reply@shop.example"}),
        shown_ids())
ex = g["expand_needsyou"]()
E.check("the pop-out payload lists the never rows",
        len(ex.get("never") or []) == 2)
E.check("opening the pop-out does not record them either",
        not (shown_ids() & {"message:262966", "message:no-reply@shop.example"}),
        shown_ids())
r = g["POST_ROUTES"]["/api/needsyou/seen"](E.Ctx(body={"ids": ["message:262966"]}))
E.check("the beacon records a never row the pop-out drew",
        r.get("marked") == 1 and "message:262966" in shown_ids(), r)
rec = [v for k, v in g["_ny_load"]()["shown"].items()
       if k.startswith("message:262966|")]
E.check("...in the never bucket", rec and rec[0]["bucket"] == "never", rec)

r = g["_ny_act_handler"](E.Ctx(body={"id": "message:no-reply@shop.example",
                                      "action": "reclassify", "to": "today"}))
E.check("a never row can be re-filed", r.get("ok") is True, r)
cur = g["_ny_cache"]["payload"]
E.check("...it leaves the never list at once",
        "message:no-reply@shop.example" not in [i["id"] for i in cur["never"]])
E.check("...and shows in today", "message:no-reply@shop.example" in
        [i["id"] for i in cur["today"]])
E.check("...never_count follows", cur["never_count"] == 1, cur["never_count"])
p2 = g["_ny_build"](NOW + 1)
E.check("a rebuild keeps the owner's re-file", "message:no-reply@shop.example"
        in [i["id"] for i in p2["today"]])
g["_ny_cache"]["payload"] = p2
g["_ny_cache"]["at"] = time.time()
r = g["_ny_act_handler"](E.Ctx(body={"id": "message:+15550000077",
                                      "action": "reclassify", "to": "never"}))
cur = g["_ny_cache"]["payload"]
E.check("re-filing INTO never lists it there at once",
        "message:+15550000077" in [i["id"] for i in cur["never"]]
        and cur["never_count"] == 2, (cur["never_count"], cur["never"]))
r = g["_ny_act_handler"](E.Ctx(body={"id": "message:262966", "action": "done"}))
cur = g["_ny_cache"]["payload"]
E.check("Done on a never row drops it from the list",
        "message:262966" not in [i["id"] for i in cur["never"]]
        and cur["never_count"] == 1, (cur["never_count"], cur["never"]))

print("=== E04: the never list is capped, the count is not ===")
g, data = E.load()
E.write_messages(data, [E.convo("2%05d" % i, "Your code is %d" % i, NOW - i)
                        for i in range(70)], generated_at=NOW)
p = g["_ny_build"](NOW)
E.check("never_count is the true count", p["never_count"] == 70,
        p["never_count"])
E.check("the list is capped at %d" % g["NY_NEVER_MAX"],
        len(p["never"]) == g["NY_NEVER_MAX"] == 50, len(p["never"]))
E.check("newest first", p["never"][0]["id"] == "message:200000")

print("=== E04: the email collector uses the same order ===")
g, data = E.load()
g["needsyou_email_provider"] = lambda: {"available": True, "messages": [
    {"id": "m1", "from": "dana@example.test", "subject": "newsletter",
     "snippet": "did you see the school newsletter? can you sign it?",
     "ts": NOW - 100},
    {"id": "m2", "from": "deals@shop.test", "subject": "48 hours left",
     "snippet": "unsubscribe here", "ts": NOW - 200},
    {"id": "m3", "from": "noreply@bank.test", "subject": "hi",
     "snippet": "your statement", "ts": NOW - 300},
]}
with open(os.path.join(data, "needsyou.json"), "w") as f:
    json.dump({"version": 1, "items": {},
               "history": {"dana@example.test": NOW - 3600,
                           "noreply@bank.test": NOW - 3600},
               "shown": {}, "acts": []}, f)
em = {m["id"]: m["sender_tier"] for m in g["_ny_collect_email"]({}, NOW)}
E.check("mail from someone you replied to + 'newsletter' -> vip",
        em.get("email:m1") == "vip", em)
E.check("marketing from a stranger -> automated",
        em.get("email:m2") == "automated", em)
E.check("a noreply@ address -> automated even with history",
        em.get("email:m3") == "automated", em)

sys.exit(E.finish())
