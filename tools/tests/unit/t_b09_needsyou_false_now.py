#!/usr/bin/env python3
"""B09 (docs/audits/2026-10-08-codebase-audit.md): casual VIP chatter
("today", "tomorrow") and long-past deadlines landed in **Now**.

BEFORE: _NY_DEADLINE_RE counted a bare "today", "tonight", "tomorrow" or
"this morning" as deadline language, and the classifier's `dt <= 24 h` was
true for any NEGATIVE dt — so "had fun today" from someone you text, or an
unread "call me in 2 hours" from yesterday, were both a VIP time-bound ask
and went to Now, the one bucket with a precision counter pointed at it.

AFTER: day words count only behind "by"/"due"; a stated deadline is
time-bound while it is still ahead (<=24 h) or missed by at most
NY_PAST_DUE_W (6 h), and a deadline older than that no longer makes the
item urgent at all.

Goes through the REAL collector (regex + deadline parser + tiering) and the
REAL classifier, with a VIP sender (reply history <30 days).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
g, data = E.load()

CASES = [
    # (ident, text, received_at, want_now, label)
    ("+15550000031", "had such a fun time today", NOW - 600, False,
     "casual 'today'"),
    ("+15550000032", "see you tomorrow", NOW - 600, False, "casual 'tomorrow'"),
    ("+15550000033", "dinner tonight was great", NOW - 600, False,
     "casual 'tonight'"),
    ("+15550000034", "this morning was wild", NOW - 600, False,
     "casual 'this morning'"),
    ("+15550000035", "call me in 2 hours", NOW - 30 * 3600, False,
     "a deadline that passed 28 h ago"),
    # controls: the rule still fires where it should
    ("+15550000036", "can you send it by 5pm?", NOW - 600, True,
     "a question with a stated deadline"),
    ("+15550000037", "need the signed copy asap", NOW - 600, True,
     "'asap'"),
    ("+15550000038", "send it by tonight", NOW - 600, True, "'by tonight'"),
    ("+15550000039", "call me in 2 hours", NOW - 3 * 3600, True,
     "a deadline missed by one hour"),
]

E.write_messages(data, [E.convo(i, t, ts, name="Friend %d" % n)
                        for n, (i, t, ts, _w, _l) in enumerate(CASES)])
st = g["_ny_load"]()
for i, _t, _ts, _w, _l in CASES:
    st["history"][i] = NOW - 86400          # you replied yesterday -> VIP
g["_ny_save"](st)

items = {it["id"]: it for it in g["_ny_collect_message"]({}, None, NOW)}
print("=== B09: VIP rows through the real collector + classifier ===")
for i, text, _ts, want_now, label in CASES:
    it = items.get("message:" + i)
    if it is None:
        E.check("%s collected" % label, False, sorted(items))
        continue
    E.check("%s is a VIP row" % label, it["sender_tier"] == "vip", it["sender_tier"])
    v = g["_ny_classify"](it, NOW)
    E.check("%s -> %s" % (label, "now" if want_now else "not now"),
            (v["bucket"] == "now") == want_now, (text, v))

print("=== B09: the regex itself ===")
rx = g["_NY_DEADLINE_RE"]
for s in ("had fun today", "see you tomorrow", "tonight?", "this evening works"):
    E.check("no deadline language in %r" % s, rx.search(s) is None)
for s in ("due tomorrow", "by today", "by tomorrow", "by this evening",
          "asap", "before 5", "in 20 minutes", "deadline is friday"):
    E.check("deadline language in %r" % s, rx.search(s) is not None)

print("=== B09: a stale deadline is said to be stale ===")
it = items.get("message:+15550000035")
if it is not None:
    it = dict(it, requires_reply=True)
    v = g["_ny_classify"](it, NOW)
    E.check("a VIP question whose deadline is long gone -> today",
            v["bucket"] == "today", v)
    E.check("...and the reason does not claim nothing was due",
            "nothing is due" not in v["reason"], v)

sys.exit(E.finish())
