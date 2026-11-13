#!/usr/bin/env python3
"""Regression test for C51 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: GOOG_SCOPES asked Google for https://www.googleapis.com/auth/calendar
— full read/write Calendar — while the Google card, the "What leaves this
Mac" table, README, SECURITY and RUNBOOK all said read-only. Nothing in
aux_google.py writes the calendar (its only call is a GET of today's events),
but the stored token could create and delete events through the agent's
google-workspace skill.

AFTER: the consent URL asks for calendar.readonly; a grant that comes back
with the read/write `calendar` scope is refused and revoked like any other
scope outside the set; a token minted before the change is REPORTED
(`scope_excess`) rather than called read-only, so the card can ask for a
reconnect.

The REAL dashboard/aux_google.py is exec'd the way server.py's aux loader does
it, on a throwaway HOME. No network: _goog_post_form is replaced by a recorder.
"""
import json
import os
import sys
import tempfile
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-c51-")
os.makedirs(os.path.join(HOME, ".hermes"))
ROUTES = {}
CACHE = {}


def _cached(key, ttl, fn):
    return fn()


ns = {
    "__name__": "aux_google",
    "HOME": HOME,
    "register_get": lambda p, f: ROUTES.__setitem__(("GET", p), f),
    "register_post": lambda p, f: ROUTES.__setitem__(("POST", p), f),
    "_cached": _cached,
    "_widget_cache": CACHE,
    "_ssl_context": lambda: None,
    # never start the calendar prime thread in a test
    "_goog_calendar_prime_started": True,
}
SRC = os.path.join(REPO, "dashboard", "aux_google.py")
exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)

RO = "https://www.googleapis.com/auth/calendar.readonly"
RW = "https://www.googleapis.com/auth/calendar"
GMAIL_RO = "https://www.googleapis.com/auth/gmail.readonly"
CONTACTS_RO = "https://www.googleapis.com/auth/contacts.readonly"


class Ctx(object):
    def __init__(self, body=None, q=None):
        self.body = body or {}
        self._q = q or {}

    def q1(self, k, d=""):
        return self._q.get(k, d)


def call(method, path, body=None, q=None):
    r = ROUTES[(method, path)](Ctx(body, q))
    if isinstance(r, tuple):
        return r[0], r[1]
    return r, 200


POSTS = []
NEXT = {}


def fake_post(url, fields, timeout=25):
    POSTS.append((url, dict(fields)))
    if url == ns["GOOG_REVOKE_URI"]:
        return 200, {}
    return NEXT.get("status", 200), dict(NEXT.get("resp", {}))


ns["_goog_post_form"] = fake_post

print("== the scopes Hermes asks for ==")
scopes = list(ns["GOOG_SCOPES"])
check("calendar.readonly is requested", RO in scopes, scopes)
check("the read/write calendar scope is NOT requested", RW not in scopes, scopes)
check("every requested scope is a read-only one",
      all(s.endswith(".readonly") for s in scopes), scopes)

# the consent URL is what Google actually sees
secret = {"installed": {"client_id": "cid.apps.googleusercontent.com",
                        "client_secret": "s3cret", "redirect_uris": ["http://localhost"]}}
r, code = call("POST", "/api/google/client_secret", {"json": secret})
check("a client secret can be stored", code == 200 and r.get("ok"), r)
r, code = call("POST", "/api/google/auth_url")
check("the consent URL is built", code == 200 and r.get("auth_url"), r)
q = urllib.parse.parse_qs(urllib.parse.urlparse(r.get("auth_url", "")).query)
asked = (q.get("scope") or [""])[0].split()
check("the consent URL asks for exactly the read-only set",
      sorted(asked) == sorted([GMAIL_RO, RO, CONTACTS_RO]), asked)

print("== a grant wider than read-only is refused and revoked ==")
check("the scope wall flags read/write calendar", ns["_goog_scope_check"]([RW]) == [RW],
      ns["_goog_scope_check"]([RW]))
check("the scope wall passes calendar.readonly", ns["_goog_scope_check"]([RO]) == [])
pending = json.load(open(ns["GOOG_PENDING"]))
NEXT["resp"] = {"access_token": "at-wide", "refresh_token": "rt-wide",
                "expires_in": 3600, "scope": " ".join([GMAIL_RO, RW, CONTACTS_RO])}
POSTS[:] = []
r, code = call("POST", "/api/google/auth_code",
               {"code": "http://localhost:1/?code=abc&state=" + pending["state"]})
check("a read/write calendar grant is refused (403 scope_violation)",
      code == 403 and r.get("error") == "scope_violation", (code, r))
check("...and the refused scope is named", RW in (r.get("refused") or []), r)
check("...and the grant is revoked with Google",
      any(u == ns["GOOG_REVOKE_URI"] for u, _ in POSTS), POSTS)
check("...and no token is stored", not os.path.exists(ns["GOOG_TOKEN"]))

NEXT["resp"] = {"access_token": "at-ro", "refresh_token": "rt-ro", "expires_in": 3600,
                "scope": " ".join([GMAIL_RO, RO, CONTACTS_RO])}
r, code = call("POST", "/api/google/auth_code", {"code": "abc"})
check("a read-only grant connects", code == 200 and r.get("connected"), (code, r))

print("== status tells a read-only grant from an older, wider one ==")
CACHE.clear()
st, _ = call("GET", "/api/google/status")
check("a fresh read-only grant is connected", st.get("connected") is True, st)
check("...with nothing in scope_excess", st.get("scope_excess") == [], st)
check("...and the write calendar scope is listed as not granted, by design",
      any("calendar" in m.lower() and "read-only" in m for m in st.get("missing_features", [])),
      st.get("missing_features"))

tok = json.load(open(ns["GOOG_TOKEN"]))
tok["scopes"] = [GMAIL_RO, RW, CONTACTS_RO]       # a grant made before C51
with open(ns["GOOG_TOKEN"], "w") as f:
    json.dump(tok, f)
st, _ = call("GET", "/api/google/status")
check("an older read/write grant still reads as connected (not revoked behind "
      "the owner's back)", st.get("connected") is True, st)
check("...but its write scope is reported in scope_excess",
      st.get("scope_excess") == [RW], st.get("scope_excess"))

print("== the card, the table and the docs no longer contradict the code ==")
JS = open(os.path.join(REPO, "dashboard", "aux_google.js"), encoding="utf-8").read()
check("the card has a distinct state for a wider-than-read-only grant",
      "scope_excess" in JS and "wider than read-only" in JS)
ONB = open(os.path.join(REPO, "dashboard", "aux_onboarding.js"), encoding="utf-8").read()
check("the Google row says calendar, mail and contacts are read-only",
      "Read-only calendar, mail and contacts" in ONB)
for doc in ("README.md", "SECURITY.md", "RUNBOOK.md"):
    t = open(os.path.join(REPO, doc), encoding="utf-8").read()
    check("%s names calendar.readonly" % doc, "calendar.readonly" in t)
    flat = " ".join(t.split())
    bad = []
    i = flat.find("gmail.compose")
    while i >= 0:
        around = flat[max(0, i - 60):i + 100]
        # every mention left must be the WARNING that compose can send
        if "can send" not in around:
            bad.append(around)
        i = flat.find("gmail.compose", i + 1)
    check("%s no longer lists gmail.compose as a granted scope" % doc, not bad, bad)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
