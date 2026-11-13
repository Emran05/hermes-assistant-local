#!/usr/bin/env python3
"""Regression test for C21 (docs/audits/2026-10-21-codebase-audit.md).

BEFORE: /api/google/status said "Connected" from the token file alone. When
Google refused the refresh token (revoked by the owner, or a Testing-mode OAuth
client's 7-day expiry), _goog_ensure_access_token returned None with no trace,
the calendar fetch reported reason "not_connected" (the wrong cause), every
prompt silently lost its calendar line, and the card kept showing
"Connected · read-only".

AFTER: the last refresh outcome is remembered against the token file it was
made with. Status reports needs_reauth + reason "refresh_failed:<code>" for a
dead grant (a transient failure gets the reason but not needs_reauth), the
calendar fetch gives the real reason, a dead grant is not re-sent to Google
every five minutes, and a reconnect (a new token file) clears it.

The REAL dashboard/aux_google.py is exec'd on a throwaway HOME; Google is a
recorder, nothing touches the network.
"""
import io
import json
import os
import sys
import tempfile
import contextlib

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


HOME = tempfile.mkdtemp(prefix="hermes-c21-")
os.makedirs(os.path.join(HOME, ".hermes"))
ROUTES = {}
CACHE = {}


def _cached(key, ttl, fn):
    if key not in CACHE:
        CACHE[key] = fn()
    return CACHE[key]


ns = {
    "__name__": "aux_google",
    "HOME": HOME,
    "register_get": lambda p, f: ROUTES.__setitem__(("GET", p), f),
    "register_post": lambda p, f: ROUTES.__setitem__(("POST", p), f),
    "_cached": _cached,
    "_widget_cache": CACHE,
    "_ssl_context": lambda: None,
    "_goog_calendar_prime_started": True,     # no background thread
}
SRC = os.path.join(REPO, "dashboard", "aux_google.py")
exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)

POSTS = []
NEXT = {}


class _Resp(object):
    def __init__(self, status, body):
        self.status = status
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None, context=None):
    """Google, as a recorder. Only the token endpoint may be called here: a
    refresh failure stops before the events GET."""
    url = getattr(req, "full_url", req)
    if not str(url).startswith("https://oauth2.googleapis.com/token"):
        raise AssertionError("unexpected network call: %s" % url)
    POSTS.append(url)
    st, body = NEXT["status"], json.dumps(NEXT["resp"]).encode()
    if st == 200:
        return _Resp(200, body)
    if st == 0:
        raise urllib.error.URLError("timed out")
    raise urllib.error.HTTPError(url, st, "err", {}, io.BytesIO(body))


import urllib.error  # noqa: E402
ns["urllib"].request.urlopen = fake_urlopen

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/calendar.readonly",
          "https://www.googleapis.com/auth/contacts.readonly"]
TOK = ns["GOOG_TOKEN"]


def write_token(mtime):
    with open(TOK, "w") as f:
        json.dump({"token": "at-old", "refresh_token": "rt-SECRET-123",
                   "token_uri": "https://oauth2.googleapis.com/token",
                   "client_id": "cid", "client_secret": "cs-SECRET",
                   "scopes": SCOPES, "expiry": "2020-01-01T00:00:00Z",
                   "type": "authorized_user"}, f)
    os.utime(TOK, (mtime, mtime))


def status():
    CACHE.pop("google_status", None)
    return ns["_goog_status_compute"]()


def fetch():
    CACHE.pop("goog_calendar", None)
    return ns["_goog_calendar_fetch"]()


write_token(1_700_000_000)
print("== before any refresh: the file alone ==")
st = status()
check("an unrefreshed token file reads as connected", st.get("connected") is True, st)
check("...with no reauth flag yet", not st.get("needs_reauth"), st)

print("== Google refuses the refresh token (revoked / 7-day Testing expiry) ==")
NEXT.update(status=400, resp={"error": "invalid_grant",
                              "error_description": "Token has been expired or revoked."})
err = io.StringIO()
with contextlib.redirect_stderr(err):
    cal = fetch()
check("the calendar fetch is unavailable", cal.get("available") is False, cal)
check("...and gives the REAL reason, not 'not_connected'",
      cal.get("reason") == "refresh_failed:invalid_grant", cal)
st = status()
check("status now says the grant needs reconnecting", st.get("needs_reauth") is True, st)
check("...with the reason", st.get("reason") == "refresh_failed:invalid_grant", st)
check("...and when it failed", isinstance(st.get("refresh_failed_at"), float), st)
check("...while the token file still exists (Disconnect stays meaningful)",
      st.get("connected") is True, st)
log = err.getvalue()
check("one stderr line names the failure", "invalid_grant" in log and
      "reconnect" in log.lower(), log)
check("...and never a token or secret", "SECRET" not in log and "rt-" not in log, log)
check("status reports when Google was last contacted",
      isinstance(st.get("last_outbound"), float) and st["last_outbound"] > 0, st)

print("== a dead grant is not re-sent every five minutes ==")
n = len(POSTS)
with contextlib.redirect_stderr(io.StringIO()):
    cal = fetch()
check("the next fetch sends nothing to Google", len(POSTS) == n, POSTS[n:])
check("...and still gives the real reason", cal.get("reason") == "refresh_failed:invalid_grant", cal)

print("== a reconnect (a new token file) clears it ==")
write_token(1_800_000_000)
st = status()
check("a new token file is not haunted by the old failure",
      not st.get("needs_reauth") and st.get("reason") == "", st)

print("== a transient failure is reported, but is not 'reconnect' ==")
NEXT.update(status=0, resp={"error": "network_error", "error_description": "timed out"})
with contextlib.redirect_stderr(io.StringIO()):
    cal = fetch()
st = status()
check("no network: reason refresh_failed:network_error",
      st.get("reason") == "refresh_failed:network_error", st)
check("...without needs_reauth", st.get("needs_reauth") is False, st)
n = len(POSTS)
with contextlib.redirect_stderr(io.StringIO()):
    fetch()
check("...and a transient failure IS retried", len(POSTS) == n + 1, POSTS[n:])

print("== a successful refresh clears the failure ==")
NEXT.update(status=200, resp={"access_token": "at-new", "expires_in": 3600})
with contextlib.redirect_stderr(io.StringIO()):
    tok = ns["_goog_ensure_access_token"](timeout=4)
check("the refresh succeeds", tok == "at-new", tok)
st = status()
check("...and status is clean again", not st.get("needs_reauth") and st.get("reason") == "", st)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
