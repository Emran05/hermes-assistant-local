#!/usr/bin/env python3
"""E87 — the Google OAuth endpoints must be Google's own https hosts.

2026-11-09 audit E87: aux_google took `auth_uri` and `token_uri` from the
pasted client JSON unchecked, so a client file (from a tutorial, or POSTed by
a same-uid process) naming another host received the authorization code, the
PKCE verifier and the client secret at the exchange — enough to redeem read
access to Gmail, Calendar and Contacts.  Now both must be https on
accounts.google.com or oauth2.googleapis.com: the upload refuses anything
else, and a stored file or token file that fails the check (written before
the fix, or by hand) is refused at use, before anything is posted.

The REAL dashboard/aux_google.py exec'd the way server.py's aux loader does,
on a throwaway HOME; _goog_post_form is a recorder (no network).
"""
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  — " + str(detail)[:300]) if detail and not cond else ""))


HOME = tempfile.mkdtemp(prefix="hermes-e87-")
os.makedirs(os.path.join(HOME, ".hermes"))
ROUTES = {}
ns = {"__name__": "aux_google", "HOME": HOME,
      "register_get": lambda p, f: ROUTES.__setitem__(("GET", p), f),
      "register_post": lambda p, f: ROUTES.__setitem__(("POST", p), f),
      "_cached": lambda key, ttl, fn: fn(), "_widget_cache": {},
      "_ssl_context": lambda: None, "_goog_calendar_prime_started": True}
SRC = os.path.join(REPO, "dashboard", "aux_google.py")
exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)


class Ctx(object):
    def __init__(self, body=None):
        self.body = body or {}

    def q1(self, k, d=""):
        return d


def call(path, body=None):
    r = ROUTES[("POST", path)](Ctx(body))
    return (r[0], r[1]) if isinstance(r, tuple) else (r, 200)


POSTS = []
ns["_goog_post_form"] = lambda url, fields, timeout=25: (
    POSTS.append((url, dict(fields))) or (200, {"access_token": "at", "refresh_token": "rt",
                                                 "expires_in": 3600}))


def client(**extra):
    inst = {"client_id": "cid.apps.googleusercontent.com", "client_secret": "s3cret",
            "redirect_uris": ["http://localhost"]}
    inst.update(extra)
    return {"installed": inst}


print("--- the upload accepts Google's own addresses")
for label, extra in (
        ("no auth_uri/token_uri at all", {}),
        ("the current Google pair", {"auth_uri": "https://accounts.google.com/o/oauth2/auth",
                                     "token_uri": "https://oauth2.googleapis.com/token"}),
        ("the older token address", {"token_uri": "https://accounts.google.com/o/oauth2/token"}),
        # review R4: client files downloaded before ~2019 carry this token
        # address (and setup.py copies it into google_token.json)
        ("the www.googleapis.com v3 token address",
         {"token_uri": "https://www.googleapis.com/oauth2/v3/token"}),
        ("the v2 consent address", {"auth_uri": "https://accounts.google.com/o/oauth2/v2/auth"})):
    r, code = call("/api/google/client_secret", {"json": client(**extra)})
    check("accepted: %s" % label, code == 200 and r.get("ok"), (code, r))

print("--- the upload refuses anything else, and stores nothing")
BADS = [
    ("another host", "token_uri", "https://oauth.example.com/token"),
    ("http, not https", "token_uri", "http://oauth2.googleapis.com/token"),
    ("a look-alike host", "token_uri", "https://oauth2.googleapis.com.example.com/token"),
    ("userinfo in front", "token_uri", "https://oauth2.googleapis.com@example.com/token"),
    ("a backslash trick", "token_uri", "https://example.com\\@oauth2.googleapis.com/token"),
    ("an odd port", "token_uri", "https://oauth2.googleapis.com:8443/token"),
    ("not a string", "token_uri", ["https://oauth2.googleapis.com/token"]),
    ("auth_uri on another host", "auth_uri", "https://accounts.example.com/o/oauth2/auth"),
    ("auth_uri with a query", "auth_uri", "https://accounts.google.com/o/oauth2/auth?x=1"),
    ("javascript:", "auth_uri", "javascript:alert(1)"),
]
good = open(ns["GOOG_SECRET"]).read()
for label, key, val in BADS:
    r, code = call("/api/google/client_secret", {"json": client(**{key: val})})
    check("refused: %s" % label, code == 400 and r.get("ok") is False, (code, r))
    check("refused %s: the stored client file is unchanged" % label,
          open(ns["GOOG_SECRET"]).read() == good)

print("--- a stored file that fails the check is refused at use")
r, code = call("/api/google/auth_url")
check("a good stored file builds the consent URL", code == 200 and
      r["auth_url"].startswith("https://accounts.google.com/"), r)
EVIL = "https://oauth.example.com/token"
with open(ns["GOOG_SECRET"], "w") as f:              # as if written before the fix
    json.dump(client(token_uri=EVIL), f)
del POSTS[:]
r, code = call("/api/google/auth_code", {"code": "4/abc"})
check("auth_code refuses a stored non-Google token_uri", code == 400 and r.get("ok") is False,
      (code, r))
check("...and posted nothing anywhere", POSTS == [], POSTS)
with open(ns["GOOG_SECRET"], "w") as f:
    json.dump(client(auth_uri="https://accounts.example.com/auth"), f)
r, code = call("/api/google/auth_url")
check("auth_url refuses a stored non-Google auth_uri", code == 400 and
      "example.com" not in json.dumps(r), (code, r))

print("--- the refresh never posts to a non-Google token_uri")
with open(ns["GOOG_TOKEN"], "w") as f:
    json.dump({"token": "old", "refresh_token": "rt", "token_uri": EVIL,
               "client_id": "c", "client_secret": "s", "expiry": "2000-01-01T00:00:00Z"}, f)
del POSTS[:]
tok = ns["_goog_ensure_access_token"]()
check("no refresh POST to the foreign host", not any(u == EVIL for u, _ in POSTS), POSTS)
check("no access token handed out", tok is None, tok)
with open(ns["GOOG_TOKEN"], "w") as f:
    json.dump({"token": "old", "refresh_token": "rt",
               "token_uri": "https://oauth2.googleapis.com/token",
               "client_id": "c", "client_secret": "s", "expiry": "2000-01-01T00:00:00Z"}, f)
del POSTS[:]
ns["_goog_ensure_access_token"]()
check("a Google token_uri still refreshes",
      [u for u, _ in POSTS] == ["https://oauth2.googleapis.com/token"], POSTS)

# review R4: a token file setup.py wrote from an older client file keeps
# refreshing — it names Google's www.googleapis.com token address
OLDTOK = "https://www.googleapis.com/oauth2/v3/token"
with open(ns["GOOG_TOKEN"], "w") as f:
    json.dump({"token": "old", "refresh_token": "rt", "token_uri": OLDTOK,
               "client_id": "c", "client_secret": "s", "expiry": "2000-01-01T00:00:00Z"}, f)
del POSTS[:]
ns["_goog_ensure_access_token"]()
check("an older Google token_uri (www.googleapis.com) still refreshes",
      [u for u, _ in POSTS] == [OLDTOK], POSTS)
check("a look-alike www host is still refused",
      ns["_goog_oauth_uri"]("https://www.googleapis.com.example.com/oauth2/v3/token",
                            "D") is None)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
