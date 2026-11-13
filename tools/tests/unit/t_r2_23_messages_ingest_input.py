#!/usr/bin/env python3
"""R2-23 (2026-10-08 audit P3 prose, stores): POST /api/messages/ingest
answered 500 for input it should simply refuse.

BEFORE:
  * hmac.compare_digest(str, str) raises TypeError for a non-ASCII string,
    so a wrong token like "tök" was a 500 instead of a 403;
  * json.loads accepts Infinity/NaN: totals.unread = Infinity made int()
    raise OverflowError (not ValueError, so no fallback caught it) -> 500,
    and so did a conversation's unread/participants/today_count;
  * generated_at = Infinity (or NaN) was stored as-is — invalid JSON for
    every strict reader, and a sync that looks fresh forever — and so was a
    conversation ts of Infinity.

AFTER: the token is compared as UTF-8 bytes; non-finite totals or
generated_at answer 400 and store nothing; a conversation with a non-finite
count is dropped like any other hopeless row and a non-finite ts reads as 0;
the store reader tolerates a file written before the fix.

Real dashboard/aux_messages.py, throwaway data dir (_msg_env.py).  Against
the pre-fix module the 403/400 checks see 500 and the stored-file checks see
Infinity.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _msg_env as E                                            # noqa: E402

g, data, TOK = E.load()
STORE = os.path.join(data, "messages.json")
ingest = g["_msg_ingest_handler"]


def wire(text):
    """What the server's body parser hands the handler (json.loads)."""
    return json.loads(text)


def post(body):
    return ingest(E.Ctx(body=body))


def status(r):
    return r[1] if isinstance(r, tuple) else 200


def stored_text():
    with open(STORE) as f:
        return f.read()


def base(**kw):
    b = {"v": 1, "token": TOK, "fda": True, "conversations": [],
         "generated_at": 1700000000.0}
    b.update(kw)
    return b


print("=== token ===")
for bad in ("tök", "☃" * 32, "\ud800", TOK[:-1] + "é"):
    r = post(base(token=bad))
    E.check("non-ASCII token %r -> 403" % bad[:6], status(r) == 403, r)
r = post(base())
E.check("the real token still works", status(r) == 200 and r.get("ok"), r)

print("=== totals / generated_at ===")
for label, text in (
        ("totals.unread Infinity", '{"unread": Infinity, "today": 1}'),
        ("totals.unread -Infinity", '{"unread": -Infinity}'),
        ("totals.today NaN", '{"unread": 1, "today": NaN}')):
    os.path.exists(STORE) and os.remove(STORE)
    b = base()
    b["totals"] = wire(text)
    r = post(b)
    E.check(label + " -> 400", status(r) == 400, r)
    E.check(label + " stores nothing", not os.path.exists(STORE))
for label, val in (("generated_at Infinity", "Infinity"),
                   ("generated_at NaN", "NaN"),
                   ("generated_at '1e999'", '"1e999"')):
    os.path.exists(STORE) and os.remove(STORE)
    b = wire('{"generated_at": %s}' % val)
    r = post(base(generated_at=b["generated_at"]))
    E.check(label + " -> 400", status(r) == 400, r)
    E.check(label + " stores nothing", not os.path.exists(STORE))
# review: json.loads reads a 400-digit number with no exponent as an INT, and
# float() of that raises OverflowError, not ValueError -> it was still a 500
os.path.exists(STORE) and os.remove(STORE)
r = post(base(generated_at=wire('{"g": 1%s}' % ("0" * 400))["g"]))
E.check("generated_at a 400-digit integer -> 400, not 500", status(r) == 400, r)
E.check("...stores nothing", not os.path.exists(STORE))

print("=== one conversation ===")
for field in ("unread", "participants", "today_count"):
    c = E.convo(ident="+15550003002")
    c[field] = float("inf")
    r = post(base(conversations=[c, E.convo()]))
    E.check("conversation %s Infinity is not a 500" % field,
            status(r) == 200, r)
    E.check("...that row is dropped, the good one kept",
            isinstance(r, dict) and r.get("stored") == 1, r)
r = post(base(conversations=[E.convo(ts=float("inf"))]))
E.check("conversation ts Infinity is accepted", status(r) == 200, r)
txt = stored_text()
E.check("...and never written as Infinity/NaN",
        "Infinity" not in txt and "NaN" not in txt, txt[:200])
E.check("...it reads as no time",
        json.loads(txt)["conversations"][0]["ts"] == 0)

print("=== a store written before the fix ===")
with open(STORE, "w") as f:
    f.write('{"v": 1, "fda": true, "generated_at": Infinity, '
            '"stored_at": 1700000000.0, "conversations": [], '
            '"totals": {"unread": Infinity, "today": NaN}}')
d = g["_msg_read_store"]()
E.check("the reader does not raise", d.get("ok") is True and d.get("available"), d)
E.check("an Infinity sync is not 'fresh forever' (stored_at is used)",
        d.get("generated_at") == 1700000000.0 and d.get("stale") is True, d)
E.check("non-finite totals read as 0",
        d.get("total_unread") == 0 and d.get("today_count") == 0, d)
# review: the no-Full-Disk-Access branch returned the stored Infinity as-is,
# which the dashboard serialises as `Infinity` — JSON no browser parses
with open(STORE, "w") as f:
    f.write('{"v": 1, "fda": false, "generated_at": Infinity, '
            '"conversations": []}')
d = g["GET_ROUTES"]["/api/messages"](E.Ctx())
E.check("no-FDA store with an old Infinity -> generated_at is no time",
        d.get("grant") is True and d.get("generated_at") is None, d)
try:
    json.dumps(d, allow_nan=False)
    strict = True
except ValueError:
    strict = False
E.check("...and the GET answer is strict JSON", strict, d)

sys.exit(E.finish())
