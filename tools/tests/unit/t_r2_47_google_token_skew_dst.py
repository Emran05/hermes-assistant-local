#!/usr/bin/env python3
"""R2-47 (docs/audits/2026-10-08-codebase-audit.md, P3 other aux) — the Google
access token is refreshed a minute early, and "today" ends at the next local
midnight.

BEFORE: _goog_ensure_access_token() (dashboard/aux_google.py) handed out the
cached access token until its exact expiry, so a calendar GET sent with a few
seconds left could reach Google after the token had died. _goog_calendar_fetch
asked for events between local midnight and midnight + 86400, which on a DST
day (23 or 25 hours long) ends an hour into tomorrow or an hour before today
ends.

AFTER: a token within GOOG_TOKEN_SKEW_S (60 s) of expiry is refreshed first;
timeMax is mktime of the next local midnight.

The REAL aux_google.py is exec'd on a throwaway HOME the way t_c51 does it.
No network: the refresh POST is a recorder, and urlopen is replaced with one
that records the events URL. TZ is pinned to America/New_York and the clock
is set to the two 2026 DST days and one ordinary day.
"""
import json
import os
import sys
import tempfile
import time
import types
import urllib.error
import urllib.parse
import urllib.request
import datetime as _dt

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
        print("  FAIL %s %r" % (name, extra))


os.environ["TZ"] = "America/New_York"
time.tzset()

HOME = tempfile.mkdtemp(prefix="hermes-r247-")
os.makedirs(os.path.join(HOME, ".hermes"))


class FakeTime:
    """localtime()/time() at a chosen instant; the rest is the real module."""
    def __init__(self):
        self.now = None

    def __getattr__(self, name):
        return getattr(time, name)

    def time(self):
        return time.time() if self.now is None else self.now

    def localtime(self, secs=None):
        return time.localtime(self.time() if secs is None else secs)


CLOCK = FakeTime()
OPENED = []


class Resp:
    def __init__(self, body):
        self._b = body.encode()
        self.status = 200

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None, context=None):
    OPENED.append(req.full_url)
    return Resp(json.dumps({"items": [
        {"summary": "Standup", "start": {"dateTime": "2026-03-08T09:00:00-04:00"}}]}))


fake_urllib = types.SimpleNamespace(
    parse=urllib.parse, error=urllib.error,
    request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=fake_urlopen))

ns = {
    "__name__": "aux_google",
    "HOME": HOME,
    "register_get": lambda p, f: None,
    "register_post": lambda p, f: None,
    "_cached": lambda key, ttl, fn: fn(),
    "_widget_cache": {},
    "_ssl_context": lambda: None,
    "_goog_calendar_prime_started": True,      # never start the prime thread
}
SRC = os.path.join(REPO, "dashboard", "aux_google.py")
exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)
ns["time"] = CLOCK
ns["urllib"] = fake_urllib

POSTS = []


def fake_post(url, fields, timeout=25):
    POSTS.append((url, fields.get("grant_type")))
    return 200, {"access_token": "at-fresh", "expires_in": 3600}


ns["_goog_post_form"] = fake_post


def write_token(seconds_left):
    exp = (_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
           + _dt.timedelta(seconds=seconds_left))
    with open(ns["GOOG_TOKEN"], "w") as f:
        json.dump({"token": "at-old", "refresh_token": "rt", "client_id": "c",
                   "client_secret": "s", "expiry": exp.isoformat() + "Z",
                   "scopes": list(ns["GOOG_SCOPES"])}, f)


print("== an access token about to expire is refreshed first ==")
write_token(30)
POSTS[:] = []
tok = ns["_goog_ensure_access_token"]()
check("30 s left: the token is refreshed before use", tok == "at-fresh", tok)
check("...with one refresh_token grant", [g for _, g in POSTS] == ["refresh_token"], POSTS)
saved = json.load(open(ns["GOOG_TOKEN"]))
check("...and the new token is stored", saved.get("token") == "at-fresh", saved)

write_token(59)
POSTS[:] = []
check("59 s left: still refreshed", ns["_goog_ensure_access_token"]() == "at-fresh", POSTS)

write_token(10 * 60)
POSTS[:] = []
tok = ns["_goog_ensure_access_token"]()
check("10 min left: the cached token is used as is", tok == "at-old" and not POSTS,
      (tok, POSTS))

write_token(-5)
POSTS[:] = []
check("already expired: refreshed (unchanged)",
      ns["_goog_ensure_access_token"]() == "at-fresh" and len(POSTS) == 1, POSTS)

print("== review: an early refresh that fails still hands out the live token ==")


def failing_post(url, fields, timeout=25):
    POSTS.append((url, fields.get("grant_type")))
    return 0, {}                               # network error


ns["_goog_post_form"] = failing_post
ns["_GOOG_NET"]["refresh"] = None
write_token(30)
POSTS[:] = []
tok = ns["_goog_ensure_access_token"]()
check("30 s left, refresh fails: the still-valid token is used (as before)",
      tok == "at-old" and len(POSTS) == 1, (tok, POSTS))
write_token(-5)
ns["_GOOG_NET"]["refresh"] = None
POSTS[:] = []
check("expired, refresh fails: nothing (unchanged)",
      ns["_goog_ensure_access_token"]() is None and len(POSTS) == 1, POSTS)
write_token(30)
ns["_GOOG_NET"]["refresh"] = {"dead": True, "error": "invalid_grant",
                              "tok_mtime": ns["_goog_token_mtime"]()}
POSTS[:] = []
tok = ns["_goog_ensure_access_token"]()
check("30 s left, grant already dead: no refresh POST, the live token is used",
      tok == "at-old" and not POSTS, (tok, POSTS))
ns["_GOOG_NET"]["refresh"] = None
ns["_goog_post_form"] = fake_post

print("== the events window is one local calendar day, DST included ==")


def window_for(y, m, d, hour=15):
    CLOCK.now = time.mktime((y, m, d, hour, 0, 0, 0, 0, -1))
    write_token(3600)
    OPENED[:] = []
    out = ns["_goog_calendar_fetch"]()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(OPENED[-1]).query) if OPENED else {}

    def ts(k):
        v = (q.get(k) or [""])[0]
        return _dt.datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp() if v else None
    return out, ts("timeMin"), ts("timeMax")


for (y, m, d), hours in (((2026, 3, 8), 23), ((2026, 11, 1), 25), ((2026, 6, 15), 24),
                         ((2026, 12, 31), 24)):
    out, lo, hi = window_for(y, m, d)
    label = "%04d-%02d-%02d" % (y, m, d)
    check("%s: the fetch ran" % label, out.get("available") is True and lo and hi, out)
    want_lo = time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))
    nxt = _dt.date(y, m, d) + _dt.timedelta(days=1)
    want_hi = time.mktime((nxt.year, nxt.month, nxt.day, 0, 0, 0, 0, 0, -1))
    check("%s: timeMin is local midnight" % label, lo == want_lo, (lo, want_lo))
    check("%s: timeMax is the NEXT local midnight" % label, hi == want_hi,
          (hi and time.strftime("%Y-%m-%d %H:%M", time.localtime(hi)), want_hi))
    check("%s: the window is %d hours" % (label, hours),
          hi is not None and lo is not None and (hi - lo) == hours * 3600,
          hi is not None and lo is not None and (hi - lo) / 3600.0)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
