#!/usr/bin/env python3
"""R2-48 (docs/audits/2026-10-08-codebase-audit.md, P3 state-changing GETs) —
/api/google/auth_url is a POST.

BEFORE: aux_google.py registered GET /api/google/auth_url, and the handler
rewrites ~/.hermes/google_oauth_pending.json (a new PKCE verifier and state)
on every call. server.py's same-origin guard lets a cross-site NAVIGATION
through on GET by design (no Origin, Sec-Fetch-Site: cross-site), so any web
page could, with one link or redirect, replace the verifier of a connect the
owner had half done; pasting Google's redirect then fails with
state_mismatch.

AFTER: the route is POST-only, which the guard refuses cross-site, and the
card (aux_google.js) asks for it with POST.

The REAL aux_google.py is exec'd on a throwaway HOME (as t_c51 does) with its
routes recorded; the REAL _request_allowed and its helpers come from server.py
by AST. A request is "served" when the guard allows it AND a route exists for
its verb, which is what Handler does. No network: _goog_post_form is a
recorder.
"""
import ast
import io
import json
import os
import re
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
        print("  FAIL %s %r" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-r248-")
os.makedirs(os.path.join(HOME, ".hermes"))
ROUTES = {}
ns = {
    "__name__": "aux_google",
    "HOME": HOME,
    "register_get": lambda p, f: ROUTES.__setitem__(("GET", p), f),
    "register_post": lambda p, f: ROUTES.__setitem__(("POST", p), f),
    "_cached": lambda key, ttl, fn: fn(),
    "_widget_cache": {},
    "_ssl_context": lambda: None,
    "_goog_calendar_prime_started": True,      # never start the prime thread
}
SRC = os.path.join(REPO, "dashboard", "aux_google.py")
exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)
ns["_goog_post_form"] = lambda url, fields, timeout=25: (
    200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600,
          "scope": " ".join(ns["GOOG_SCOPES"])})

# server.py's guard, verbatim
SERVER = os.path.join(REPO, "dashboard", "server.py")
guard = {"os": os, "urllib": urllib}
WANT = {"_SAFE_METHODS", "_build_allowed_hosts", "ALLOWED_HOSTS", "_hdr", "_request_allowed",
        "DASH_PORT"}
for node in ast.parse(io.open(SERVER, encoding="utf-8").read()).body:
    if (isinstance(node, ast.FunctionDef) and node.name in WANT) or (
            isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in WANT for t in node.targets)):
        try:
            exec(compile(ast.Module([node], []), SERVER, "exec"), guard)
        except Exception:
            pass
guard.setdefault("DASH_PORT", 7788)
if "ALLOWED_HOSTS" not in guard:
    guard["ALLOWED_HOSTS"] = guard["_build_allowed_hosts"]()


class Ctx(object):
    def __init__(self, body=None):
        self.body = body or {}
        self.query = {}

    def q1(self, k, d=""):
        return d


def request(method, path, headers, body=None):
    """(status, payload): the guard first, then the route for this verb."""
    ok, why = guard["_request_allowed"](method, headers)
    if not ok:
        return 403, {"error": why}
    fn = ROUTES.get((method, path))
    if fn is None:
        return 404, {"error": "not found"}
    r = fn(Ctx(body))
    return (r[1], r[0]) if isinstance(r, tuple) else (200, r)


HOST = {"Host": "127.0.0.1:%s" % guard["DASH_PORT"]}
OWNER = dict(HOST, **{"Origin": "http://127.0.0.1:%s" % guard["DASH_PORT"],
                      "Sec-Fetch-Site": "same-origin"})
NAVIGATION = dict(HOST, **{"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})


def pending():
    try:
        return json.load(open(ns["GOOG_PENDING"]))
    except Exception:
        return None


print("== a cross-site navigation cannot restart the owner's connect ==")
secret = {"installed": {"client_id": "cid.apps.googleusercontent.com",
                        "client_secret": "s3cret", "redirect_uris": ["http://localhost"]}}
st, r = request("POST", "/api/google/client_secret", OWNER, {"json": secret})
check("the owner stores a client secret", st == 200 and r.get("ok"), (st, r))
st, r = request("POST", "/api/google/auth_url", OWNER, {})
check("the owner's card gets a consent URL with POST", st == 200 and r.get("auth_url"),
      (st, r))
first = pending()
check("...and a pending PKCE session is written", first and first.get("code_verifier"), first)
state = urllib.parse.parse_qs(urllib.parse.urlparse(r.get("auth_url", "")).query).get(
    "state", [""])[0]
check("...whose state is on the URL", first and state == first.get("state"), state)

check("the guard does let a cross-site navigation GET through (by design)",
      guard["_request_allowed"]("GET", NAVIGATION)[0] is True)
st, r = request("GET", "/api/google/auth_url", NAVIGATION)
check("a cross-site navigation to /api/google/auth_url is not served",
      st in (403, 404, 405) and not r.get("auth_url"), (st, r))
check("...and the pending verifier and state are untouched", pending() == first,
      (pending(), first))
st, r = request("POST", "/api/google/auth_url", NAVIGATION, {})
check("a cross-site POST to it is refused by the guard", st == 403, (st, r))
check("no GET route rewrites the pending file", ("GET", "/api/google/auth_url") not in ROUTES,
      sorted(k for k in ROUTES if k[0] == "GET"))

st, r = request("POST", "/api/google/auth_code", OWNER,
                {"code": "http://localhost:1/?code=abc&state=" + (first or {}).get("state", "")})
check("the owner's half-done connect still completes", st == 200 and r.get("connected"),
      (st, r))

print("== the card asks for the consent URL with POST ==")
JS = io.open(os.path.join(REPO, "dashboard", "aux_google.js"), encoding="utf-8").read()
check("aux_google.js POSTs /api/google/auth_url (jpost)",
      re.search(r'jpost\(\s*"/api/google/auth_url"', JS) is not None)
check("...and never fetches it with a bare GET",
      re.search(r'fetch\(\s*"/api/google/auth_url"\s*,\s*\{\s*cache', JS) is None)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
