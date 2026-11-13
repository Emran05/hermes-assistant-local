# aux_google.py — Google connection, SAFETY-NARROWED (P2.5).
#
# Exec'd into server.py globals by the aux loader (after expanders_extra.py,
# before class Handler).  Registers /api/google/* routes that drive a
# minimal-scope OAuth Desktop flow whose stored artifacts are byte-compatible
# with the google-workspace skill's own setup.py / google_api.py:
#   * ~/.hermes/google_client_secret.json   (verbatim copy, 600)
#   * ~/.hermes/google_oauth_pending.json   ({state, code_verifier,
#                                             redirect_uri} — the exact shape
#                                             setup.py._save_pending_auth writes,
#                                             so setup.py --auth-code could even
#                                             finish OUR pending session)
#   * ~/.hermes/google_token.json           (authorized_user payload with the
#                                             same keys setup.py persists via
#                                             Credentials.to_json(): token,
#                                             refresh_token, token_uri,
#                                             client_id, client_secret, scopes,
#                                             universe_domain, account, expiry,
#                                             type — google_api.py's
#                                             get_credentials() loads + auto-
#                                             refreshes it unchanged)
#
# ★ SCOPE MANDATE (why this module exists instead of just running setup.py):
# Google has NO draft-without-send scope — gmail.compose AND gmail.modify both
# permit messages.send.  The skill's stock setup.py hardcodes gmail.send +
# gmail.modify + drive + docs + sheets.  This module therefore builds the
# consent URL ITSELF with only:
#     gmail.readonly    — read mail; sending is impossible at Google's own
#                         authorization layer
#     calendar.readonly — read the calendar (today widget, calendar_gap).
#                         It was the full read/write `calendar` scope until
#                         C51 (2026-10-21 audit), while every surface said
#                         read-only; nothing in this module writes events.
#     contacts.readonly
# The exchange step refuses + revokes any grant outside that set.  "Drafting"
# means the agent composes text the USER sends — no send-capable scope is ever
# requested.  setup.py --check will report AUTHENTICATED (partial): expected,
# its own code treats scope subsets as valid.
#
# Secrets discipline: the client_secret arrives PASTED in a POST body — it is
# stored straight to ~/.hermes at 600, never logged, never echoed back.  Status
# never makes network calls (file read only, cached); handlers never place
# token/secret bytes in responses or stderr.
#
# Stdlib only.  All new global names are prefixed GOOG_/ _goog_ so nothing in
# server.py's namespace can be clobbered.  datetime is imported ONLY under a
# private alias (CLAUDE.md aux-module gotcha).

import os
import sys
import json
import time
import base64
import threading
import hashlib
import secrets
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import datetime as _goog_datetime

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
GOOG_DIR      = os.path.join(HOME, ".hermes")
GOOG_TOKEN    = os.path.join(GOOG_DIR, "google_token.json")
GOOG_SECRET   = os.path.join(GOOG_DIR, "google_client_secret.json")
GOOG_PENDING  = os.path.join(GOOG_DIR, "google_oauth_pending.json")
GOOG_LASTURL  = os.path.join(GOOG_DIR, "google_oauth_last_url.txt")

GOOG_REDIRECT = "http://localhost:1"          # == setup.py REDIRECT_URI
GOOG_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOG_TOKEN_URI = "https://oauth2.googleapis.com/token"
# E87 (2026-11-09 audit): the only hosts an `auth_uri` / `token_uri` from a
# pasted client file (or the token file) may name.  The token address
# receives the authorization code, the PKCE verifier and the client secret,
# so a foreign one could redeem read access to the owner's Google data.
# www.googleapis.com: client files downloaded before ~2019 name
# https://www.googleapis.com/oauth2/v3/token, and setup.py copies it into
# google_token.json — refusing it would silently stop an owner's refresh.
GOOG_OAUTH_HOSTS = ("accounts.google.com", "oauth2.googleapis.com",
                    "www.googleapis.com")
GOOG_REVOKE_URI = "https://oauth2.googleapis.com/revoke"

# The ONLY scopes this dashboard will ever request (see mandate above).
GOOG_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
]
# Benign identity scopes Google sometimes appends; a grant limited to
# GOOG_SCOPES ∪ GOOG_EXTRA_OK is accepted, anything else is revoked+refused.
GOOG_EXTRA_OK = {
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
}
# Hard-forbidden signals (belt-and-braces on top of the subset check).
GOOG_FORBIDDEN = ("gmail.send", "gmail.compose", "gmail.modify", "gmail.insert",
                  "mail.google.com", "/auth/drive", "spreadsheets", "documents")

# The skill's stock scope list (setup.py SCOPES) → honest missing_features.
GOOG_SKILL_STOCK = {
    "https://www.googleapis.com/auth/gmail.send":
        "Send mail — excluded permanently by safety design",
    "https://www.googleapis.com/auth/gmail.modify":
        "Modify/label mail — excluded by safety design (also permits send)",
    "https://www.googleapis.com/auth/drive":
        "Drive — not requested (opt-in later)",
    "https://www.googleapis.com/auth/spreadsheets":
        "Sheets — not requested (opt-in later)",
    "https://www.googleapis.com/auth/documents":
        "Docs — not requested (opt-in later)",
    "https://www.googleapis.com/auth/gmail.readonly": "Read mail",
    "https://www.googleapis.com/auth/calendar":
        "Create or change calendar events — excluded by design (read-only)",
    "https://www.googleapis.com/auth/contacts.readonly": "Contacts (read-only)",
}
GOOG_MAX_SECRET_BYTES = 65536
GOOG_STATUS_TTL = 15                         # seconds; mutations bust it


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _goog_atomic_write(path, raw, mode=0o600):
    """temp + fchmod(600) + fsync + os.replace — never a partial, never >600."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".goog_")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _goog_read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _goog_sslctx():
    """Reuse server.py's certifi-backed context (framework Python lacks roots)."""
    try:
        fn = globals().get("_ssl_context")
        if callable(fn):
            return fn()
    except Exception:
        pass
    return None


# C21 (2026-10-21 audit): what the last token refresh actually did, and when
# anything last went to Google. Module state on purpose (no new file): the
# calendar prime loop refreshes within seconds of every dashboard start, so a
# dead grant is known again almost at once. `tok_mtime` ties a failure to the
# token file it was made with — a reconnect writes a new file, and a failure
# recorded against the old one must not haunt it.
_GOOG_NET = globals().get("_GOOG_NET") or {"last_out": 0.0, "refresh": None}
# Errors Google uses for "this grant is gone": revoked by the owner, a Testing-
# mode OAuth app's 7-day refresh expiry, or a deleted client. Anything else
# (no network, 5xx, 429) is transient and retried by the prime loop.
GOOG_DEAD_GRANT = ("invalid_grant", "invalid_client", "unauthorized_client")


def _goog_net_note():
    _GOOG_NET["last_out"] = time.time()


def _goog_token_mtime():
    try:
        return os.stat(GOOG_TOKEN).st_mtime
    except OSError:
        return None


def _goog_note_refresh(ok, status, resp):
    """Record one refresh outcome; log once per change of state. Only Google's
    error CODE is kept — never the posted fields, never a token."""
    prev = _GOOG_NET.get("refresh")
    if ok:
        _GOOG_NET["refresh"] = None
        if prev:
            print("[aux_google] token refresh works again", file=sys.stderr, flush=True)
    else:
        code = str((resp or {}).get("error") or
                   (("http_%d" % status) if status else "network_error"))[:60]
        _GOOG_NET["refresh"] = {"at": time.time(), "status": int(status or 0),
                                "error": code, "dead": code in GOOG_DEAD_GRANT,
                                "tok_mtime": _goog_token_mtime()}
        if not prev or prev.get("error") != code:
            print("[aux_google] token refresh failed: %s (HTTP %s)%s"
                  % (code, status or "-", " — Google needs reconnecting"
                     if code in GOOG_DEAD_GRANT else ""),
                  file=sys.stderr, flush=True)
    if prev or not ok:
        _goog_bust()          # the status card must not keep a stale verdict


def _goog_refresh_failure():
    """The recorded refresh failure for the CURRENT token file, or None."""
    rf = _GOOG_NET.get("refresh")
    if not rf or rf.get("tok_mtime") != _goog_token_mtime():
        return None
    return rf


def _goog_post_form(url, fields, timeout=25):
    """POST x-www-form-urlencoded; returns (http_status, parsed_json|{}).
    Raises nothing; upstream errors come back as (status, {"error": ...}).
    The posted fields are never included in any error we surface."""
    _goog_net_note()
    body = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_goog_sslctx()) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            raw = ""
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"error": ("http_%d" % e.code)}
        if not isinstance(parsed, dict):
            parsed = {"error": ("http_%d" % e.code)}
        return e.code, parsed
    except Exception as e:
        return 0, {"error": "network_error", "error_description": str(e)[:200]}


def _goog_revoke_token(tok):
    """Best-effort remote revoke (mirrors setup.py --revoke's POST)."""
    if not tok:
        return False
    st, _ = _goog_post_form(GOOG_REVOKE_URI, {"token": tok}, timeout=15)
    return st == 200


def _goog_enforce_600(path):
    try:
        if os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o600)
    except OSError:
        pass


def _goog_bust():
    try:
        _widget_cache.pop("google_status", None)
    except Exception:
        pass


def _goog_scope_list(payload):
    raw = payload.get("scopes") or payload.get("scope") or []
    if isinstance(raw, str):
        raw = raw.split()
    return [s for s in raw if isinstance(s, str) and s]


def _goog_scope_check(granted):
    """Return the list of granted scopes we refuse to hold (empty == clean)."""
    allowed = set(GOOG_SCOPES) | GOOG_EXTRA_OK
    bad = []
    for s in granted:
        if s not in allowed or any(f in s for f in GOOG_FORBIDDEN):
            bad.append(s)
    return bad


def _goog_parse_expiry(s):
    """Parse the token file's expiry the same way google-auth does
    (rstrip('Z'), drop fraction, %Y-%m-%dT%H:%M:%S, naive UTC)."""
    try:
        return _goog_datetime.datetime.strptime(
            str(s).rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


# --------------------------------------------------------------------------
# status (file-read only — NO network calls, ever)
# --------------------------------------------------------------------------
def _goog_status_compute():
    has_secret = os.path.isfile(GOOG_SECRET)
    has_pending = os.path.isfile(GOOG_PENDING)
    out = {"ok": True, "connected": False, "has_client_secret": has_secret,
           "awaiting_code": has_pending, "partial": False, "scopes": [],
           "missing_features": [], "reason": "", "checked_at": time.time(),
           "needs_reauth": False,
           # when a request last went to Google (not when this file was read)
           "last_outbound": _GOOG_NET.get("last_out") or None}
    if not os.path.isfile(GOOG_TOKEN):
        out["reason"] = "not_connected"
        return out

    _goog_enforce_600(GOOG_TOKEN)
    payload = _goog_read_json(GOOG_TOKEN)
    if not isinstance(payload, dict):
        out["reason"] = "token_unreadable"
        return out

    scopes = _goog_scope_list(payload)
    has_refresh = bool(payload.get("refresh_token"))
    expiry = _goog_parse_expiry(payload.get("expiry")) if payload.get("expiry") else None
    now_utc = _goog_datetime.datetime.now(
        _goog_datetime.timezone.utc).replace(tzinfo=None)
    expired = bool(expiry and expiry <= now_utc)

    # Connected == the skill's google_api.py can produce a working credential:
    # a live access token, or an expired one it can auto-refresh.
    connected = has_refresh or (bool(payload.get("token")) and not expired)
    out["connected"] = connected
    out["scopes"] = scopes
    out["access_token_expired"] = expired
    out["can_refresh"] = has_refresh
    email = payload.get("account") or ""
    if email:
        out["email"] = email
    if not connected:
        out["reason"] = "token_expired_no_refresh" if expired else "token_incomplete"
        return out

    # Partial by design: we hold a deliberate subset of the skill's stock list.
    missing = [label for scope, label in GOOG_SKILL_STOCK.items()
               if scope not in scopes]
    out["partial"] = bool(missing)
    out["missing_features"] = missing
    out["read_only_gmail"] = ("https://www.googleapis.com/auth/gmail.readonly"
                              in scopes) and not any(
        any(f in s for f in ("gmail.send", "gmail.compose", "gmail.modify",
                             "mail.google.com")) for s in scopes)
    # A grant made before C51 holds the full read/write `calendar` scope. It is
    # not revoked behind the owner's back (that would disconnect them); it is
    # REPORTED, and the card asks for a reconnect to narrow it.
    out["scope_excess"] = _goog_scope_check(scopes)
    # C21: a token FILE is not a working grant. Once a refresh of THIS file
    # has failed, say so — needs_reauth when Google called the grant dead
    # (revoked, a Testing-mode client's 7-day expiry), a transient reason
    # otherwise. Still no network here: this reads what the last refresh did.
    rf = _goog_refresh_failure()
    if rf:
        out["reason"] = "refresh_failed:" + rf["error"]
        out["refresh_failed_at"] = rf["at"]
        out["needs_reauth"] = bool(rf.get("dead"))
    return out


def _goog_status_handler(ctx):
    try:
        if ctx.q1("fresh", "") == "1":
            _goog_bust()
        return _cached("google_status", GOOG_STATUS_TTL, _goog_status_compute)
    except Exception as e:
        # degrade, never 500 the Mind view
        return {"ok": True, "connected": False, "partial": False, "scopes": [],
                "missing_features": [], "reason": "status_failed: " + type(e).__name__}


# --------------------------------------------------------------------------
# client secret (pasted JSON in; stored verbatim at 600; NEVER echoed/logged)
# --------------------------------------------------------------------------
def _goog_secret_handler(ctx):
    try:
        b = ctx.body if isinstance(ctx.body, dict) else {}
        data = None
        j = b.get("json")
        if isinstance(j, dict):
            data = j
        elif isinstance(j, str):
            if len(j) > GOOG_MAX_SECRET_BYTES:
                return ({"ok": False, "error": "too_big"}, 413)
            try:
                data = json.loads(j)
            except Exception:
                return ({"ok": False, "error": "not_json",
                         "hint": "That paste isn't valid JSON — copy the whole "
                                 "downloaded client_secret file contents."}, 422)
        elif "installed" in b or "web" in b:
            data = b
        if not isinstance(data, dict):
            return ({"ok": False, "error": "not_json",
                     "hint": "Paste the downloaded client_secret JSON file's "
                             "contents."}, 422)
        try:
            raw = json.dumps(data, indent=2)      # same formatting as setup.py
        except Exception:
            return ({"ok": False, "error": "not_json"}, 422)
        if len(raw) > GOOG_MAX_SECRET_BYTES:
            return ({"ok": False, "error": "too_big"}, 413)
        if "installed" not in data:
            if "web" in data:
                return ({"ok": False, "error": "wrong_client_type",
                         "hint": "This is a Web application client. Create a "
                                 "Desktop app OAuth client instead (Credentials "
                                 "→ Create Credentials → OAuth client ID → "
                                 "Desktop app)."}, 400)
            return ({"ok": False, "error": "not_client_secret",
                     "hint": "Missing the 'installed' key — download the OAuth "
                             "client JSON for a Desktop app from Google Cloud "
                             "Console → Credentials."}, 400)
        inst = data["installed"]
        if not isinstance(inst, dict):
            return ({"ok": False, "error": "not_client_secret"}, 400)
        cid = inst.get("client_id")
        csec = inst.get("client_secret")
        ruris = inst.get("redirect_uris")
        if not (isinstance(cid, str) and cid.strip()):
            return ({"ok": False, "error": "not_client_secret",
                     "hint": "installed.client_id is missing."}, 400)
        if not (isinstance(csec, str) and csec.strip()):
            return ({"ok": False, "error": "not_client_secret",
                     "hint": "installed.client_secret is missing."}, 400)
        if not (isinstance(ruris, list) and ruris):
            return ({"ok": False, "error": "not_client_secret",
                     "hint": "installed.redirect_uris is missing."}, 400)
        for key, dflt in (("auth_uri", GOOG_AUTH_URI), ("token_uri", GOOG_TOKEN_URI)):
            if _goog_oauth_uri(inst.get(key), dflt) is None:
                return ({"ok": False, "error": "bad_client_secret",
                         "hint": "installed.%s must be Google's own https address "
                                 "(accounts.google.com, oauth2. or www.googleapis.com) — "
                                 "download the client JSON from Google Cloud "
                                 "Console again." % key}, 400)
        _goog_atomic_write(GOOG_SECRET, raw)
        _goog_bust()
        return {"ok": True, "stored": True}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + type(e).__name__}, 500)


# --------------------------------------------------------------------------
# auth URL (PKCE S256 + state, narrowed scopes, setup.py-shaped pending file)
# --------------------------------------------------------------------------
def _goog_authurl_handler(ctx):
    try:
        if not os.path.isfile(GOOG_SECRET):
            return ({"ok": False, "error": "no_client_secret",
                     "hint": "Paste your OAuth client JSON first (step 1). "
                             "Download it from Google Cloud Console → "
                             "Credentials → OAuth client ID (Desktop app)."}, 400)
        _goog_enforce_600(GOOG_SECRET)
        data = _goog_read_json(GOOG_SECRET)
        inst = (data or {}).get("installed") or {}
        cid = inst.get("client_id") or ""
        if not cid:
            return ({"ok": False, "error": "bad_client_secret",
                     "hint": "Stored client file has no installed.client_id — "
                             "re-paste it (step 1)."}, 400)

        verifier = secrets.token_urlsafe(64)          # 86 chars, RFC 7636 range
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        state = secrets.token_urlsafe(24)

        # Exact shape setup.py._save_pending_auth writes.
        _goog_atomic_write(GOOG_PENDING, json.dumps(
            {"state": state, "code_verifier": verifier,
             "redirect_uri": GOOG_REDIRECT}, indent=2))

        params = [
            ("response_type", "code"),
            ("client_id", cid),
            ("redirect_uri", GOOG_REDIRECT),
            ("scope", " ".join(GOOG_SCOPES)),
            ("state", state),
            ("code_challenge", challenge),
            ("code_challenge_method", "S256"),
            ("access_type", "offline"),
            ("prompt", "consent"),
            ("include_granted_scopes", "false"),  # never inherit an old broad grant
        ]
        auth_uri = _goog_oauth_uri(inst.get("auth_uri"), GOOG_AUTH_URI)
        if auth_uri is None:                          # E87: a file stored pre-check
            return ({"ok": False, "error": "bad_client_secret",
                     "hint": "The stored client file names a sign-in address "
                             "that is not Google's — re-paste it (step 1)."}, 400)
        url = auth_uri + "?" + urllib.parse.urlencode(params)
        try:
            _goog_atomic_write(GOOG_LASTURL, url + "\n")   # parity with SKILL.md
        except Exception:
            pass
        _goog_bust()
        return {"ok": True, "auth_url": url, "scopes": list(GOOG_SCOPES),
                "redirect_uri": GOOG_REDIRECT}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + type(e).__name__}, 500)


# --------------------------------------------------------------------------
# code exchange (urllib to oauth2.googleapis.com/token; scope wall; 600 token)
# --------------------------------------------------------------------------
def _goog_oauth_uri(u, default):
    """`default` when `u` is absent or empty; `u` itself when it is an https
    address on one of GOOG_OAUTH_HOSTS (default port, no userinfo, query or
    fragment); otherwise None — the caller refuses rather than posting (E87)."""
    if u is None or u == "":
        return default
    if not isinstance(u, str) or any(c in u for c in "\\ \t\r\n") or \
            any(ord(c) < 32 or ord(c) == 127 for c in u):
        return None
    try:
        p = urllib.parse.urlsplit(u)
        port = p.port
    except ValueError:
        return None
    if (p.scheme != "https" or p.username is not None or p.password is not None
            or port not in (None, 443) or p.query or p.fragment
            or (p.hostname or "").lower() not in GOOG_OAUTH_HOSTS
            or "@" in p.netloc):
        return None
    return u


def _goog_extract_code(code_or_url):
    """Accept a bare code or the full localhost:1 redirect URL (like setup.py)."""
    s = (code_or_url or "").strip()
    if not s.startswith("http"):
        return s, None, ""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(s).query)
    code = (q.get("code") or [""])[0]
    state = (q.get("state") or [None])[0]
    scope = (q.get("scope") or [""])[0]
    return code, state, scope


def _goog_authcode_handler(ctx):
    try:
        b = ctx.body if isinstance(ctx.body, dict) else {}
        raw = b.get("code")
        raw = raw.strip() if isinstance(raw, str) else ""       # E123: no_code, not 500
        if not raw:
            return ({"ok": False, "error": "no_code",
                     "hint": "Paste the full address-bar URL from the browser "
                             "(it starts with http://localhost:1/?...)."}, 400)
        if not os.path.isfile(GOOG_SECRET):
            return ({"ok": False, "error": "no_client_secret",
                     "hint": "Start at step 1 — no OAuth client is stored."}, 400)
        pending = _goog_read_json(GOOG_PENDING)
        if not isinstance(pending, dict) or not pending.get("code_verifier"):
            return ({"ok": False, "error": "no_pending",
                     "hint": "No consent session in progress — reopen the "
                             "Google consent link (step 2) and try again."}, 400)

        code, cb_state, cb_scope = _goog_extract_code(raw)
        if not code:
            return ({"ok": False, "error": "no_code",
                     "hint": "That URL has no ?code= parameter — copy the "
                             "entire redirected address."}, 400)
        if cb_state and pending.get("state") and cb_state != pending["state"]:
            return ({"ok": False, "error": "state_mismatch",
                     "hint": "That redirect came from an older consent tab — "
                             "reopen the consent link and use only the newest "
                             "redirect."}, 400)

        data = _goog_read_json(GOOG_SECRET) or {}
        inst = data.get("installed") or {}
        token_uri = _goog_oauth_uri(inst.get("token_uri"), GOOG_TOKEN_URI)
        if token_uri is None:                         # E87: never post the code there
            return ({"ok": False, "error": "bad_client_secret",
                     "hint": "The stored client file names a token address "
                             "that is not Google's — re-paste it (step 1)."}, 400)

        st, resp = _goog_post_form(token_uri, {
            "code": code,
            "client_id": inst.get("client_id") or "",
            "client_secret": inst.get("client_secret") or "",
            "redirect_uri": pending.get("redirect_uri") or GOOG_REDIRECT,
            "grant_type": "authorization_code",
            "code_verifier": pending["code_verifier"],
        })
        if st != 200 or not resp.get("access_token"):
            err = str(resp.get("error") or "exchange_failed")[:80]
            desc = str(resp.get("error_description") or "")[:160]
            return ({"ok": False, "error": "exchange_failed",
                     "upstream": (err + (": " + desc if desc else "")),
                     "hint": "The code may have expired or been used already — "
                             "reopen the consent link (step 2) and paste a "
                             "fresh redirect URL."}, 400)

        granted = (resp.get("scope") or cb_scope or "").split() or list(GOOG_SCOPES)

        # ★ the scope wall: refuse + revoke anything beyond the narrowed set
        bad = _goog_scope_check(granted)
        if bad:
            _goog_revoke_token(resp.get("access_token"))
            return ({"ok": False, "error": "scope_violation",
                     "granted": granted, "refused": bad,
                     "hint": "Google returned a permission outside the "
                             "read-only set, so the grant was revoked and "
                             "nothing was stored. Reconnect and approve only "
                             "the requested items."}, 403)

        refresh = resp.get("refresh_token")
        if not refresh:
            _goog_revoke_token(resp.get("access_token"))
            return ({"ok": False, "error": "no_refresh_token",
                     "hint": "Google returned no refresh token. Remove Hermes "
                             "at myaccount.google.com/permissions, then "
                             "reconnect from step 2."}, 400)

        expires_in = int(resp.get("expires_in") or 3600)
        expiry = (_goog_datetime.datetime.now(_goog_datetime.timezone.utc)
                  .replace(tzinfo=None)
                  + _goog_datetime.timedelta(seconds=expires_in))
        # Same key set setup.py persists (Credentials.to_json() + normalize).
        token_payload = {
            "token": resp["access_token"],
            "refresh_token": refresh,
            "token_uri": GOOG_TOKEN_URI,
            "client_id": inst.get("client_id") or "",
            "client_secret": inst.get("client_secret") or "",
            "scopes": granted,
            "universe_domain": "googleapis.com",
            "account": "",
            "expiry": expiry.isoformat() + "Z",
            "type": "authorized_user",
        }
        _goog_atomic_write(GOOG_TOKEN, json.dumps(token_payload, indent=2))
        try:
            os.unlink(GOOG_PENDING)
        except OSError:
            pass
        _goog_bust()
        return {"ok": True, "connected": True, "scopes": granted,
                "read_only_gmail": True}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + type(e).__name__}, 500)


# --------------------------------------------------------------------------
# disconnect (mirror setup.py --revoke: remote revoke best-effort, then delete)
# --------------------------------------------------------------------------
def _goog_disconnect_handler(ctx):
    try:
        revoked = False
        payload = _goog_read_json(GOOG_TOKEN) if os.path.isfile(GOOG_TOKEN) else None
        if isinstance(payload, dict):
            # Revoking the refresh token kills the whole grant and works even
            # when the access token is long expired (no refresh round-trip).
            revoked = _goog_revoke_token(
                payload.get("refresh_token") or payload.get("token"))
        for p in (GOOG_TOKEN, GOOG_PENDING):
            try:
                os.unlink(p)
            except OSError:
                pass
        _goog_bust()
        return {"ok": True, "connected": False, "revoked": revoked}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + type(e).__name__}, 500)


# --------------------------------------------------------------------------
# calendar (read-only — the "today" context source when Google is connected)
#
# Owner call (2026-09): Reminders/Notes automation defaults off (repeated
# osascript launches of those apps), and Google Calendar is connected, so
# server.py's access_preamble() prefers THIS over the icalBuddy-backed
# macos_calendar() when connected, and drops the calendar line entirely
# otherwise — resolved by name at call time (aux_google.py execs after
# server.py's body), same discipline aux_index/aux_memlayer document.
# --------------------------------------------------------------------------
GOOG_CAL_TTL = 300
GOOG_TOKEN_SKEW_S = 60


def _goog_ensure_access_token(timeout=25):
    """A live access token, refreshing via the stored refresh_token if the
    cached one is expired.  None if not connected or the refresh fails — this
    never raises and never prompts; the caller degrades to "unavailable".

    `timeout` bounds only the refresh POST. Default (25s) matches
    _goog_post_form's own default for the general OAuth path (auth-code
    exchange etc.); _goog_calendar_fetch passes a much shorter timeout since
    it runs on the chat request path (access_preamble()) where a slow
    network must not stall a turn."""
    payload = _goog_read_json(GOOG_TOKEN)
    if not isinstance(payload, dict):
        return None
    expiry = (_goog_parse_expiry(payload.get("expiry"))
              if payload.get("expiry") else None)
    now_utc = _goog_datetime.datetime.now(
        _goog_datetime.timezone.utc).replace(tzinfo=None)
    # refresh GOOG_TOKEN_SKEW_S early: a token handed out with seconds left
    # can expire while the request that carries it is still in flight
    soon = now_utc + _goog_datetime.timedelta(seconds=GOOG_TOKEN_SKEW_S)
    if payload.get("token") and not (expiry and expiry <= soon):
        return payload["token"]
    # inside the skew window the old token still works: when the early
    # refresh cannot happen, hand it out as before rather than nothing
    still = (payload.get("token")
             if payload.get("token") and not (expiry and expiry <= now_utc)
             else None)
    refresh = payload.get("refresh_token")
    if not refresh:
        return still
    dead = _goog_refresh_failure()
    if dead and dead.get("dead"):
        # Google already called THIS grant dead; asking again every five
        # minutes would only resend the refresh token. A reconnect (or the
        # agent's own successful refresh) rewrites the file and clears this.
        return still
    token_uri = _goog_oauth_uri(payload.get("token_uri"), GOOG_TOKEN_URI)
    if token_uri is None:
        # E87: a token file naming a foreign token address is never sent the
        # refresh token; the caller degrades to "unavailable" as on any miss
        return still
    st, resp = _goog_post_form(token_uri, {
        "client_id": payload.get("client_id") or "",
        "client_secret": payload.get("client_secret") or "",
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }, timeout=timeout)
    if st != 200 or not resp.get("access_token"):
        # C21: this used to vanish without a trace, and status kept saying
        # "Connected" from the token file alone. Now it is remembered.
        _goog_note_refresh(False, st, resp)
        return still
    _goog_note_refresh(True, st, resp)
    expires_in = int(resp.get("expires_in") or 3600)
    new_expiry = (_goog_datetime.datetime.now(_goog_datetime.timezone.utc)
                  .replace(tzinfo=None)
                  + _goog_datetime.timedelta(seconds=expires_in))
    payload["token"] = resp["access_token"]
    payload["expiry"] = new_expiry.isoformat() + "Z"
    try:
        _goog_atomic_write(GOOG_TOKEN, json.dumps(payload, indent=2))
    except Exception:
        pass  # a failed persist just means the next call refreshes again
    return payload["token"]


def _goog_calendar_fetch():
    # This is the function access_preamble() (server.py) calls on the LIVE
    # chat request path on a cache miss — a slow/hung network call here used
    # to be able to spend up to ~35s (25s token refresh + 10s events GET)
    # before a turn even reached the model. ~4s per leg instead; the
    # background prime loop below keeps _widget_cache["goog_calendar"] warm
    # so the request path hits that cache far more often than it hits this
    # function at all.
    token = _goog_ensure_access_token(timeout=4)
    if not token:
        # say WHY (C21): a dead grant is not the same as never connected
        rf = _goog_refresh_failure()
        return {"available": False,
                "reason": ("refresh_failed:" + rf["error"]) if rf else "not_connected"}
    _goog_net_note()                       # the events GET below goes to Google
    lt = time.localtime()
    day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    # the next local midnight, not +86400: a DST day is 23 or 25 hours long
    # (mktime normalises tm_mday past the end of the month)
    day_end = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1))

    def rfc(ts):
        return _goog_datetime.datetime.fromtimestamp(
            ts, tz=_goog_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    qs = urllib.parse.urlencode({
        "timeMin": rfc(day_start), "timeMax": rfc(day_end),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": "20",
    })
    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events?" + qs
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=4, context=_goog_sslctx()) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"available": False, "reason": "google_calendar_error: " + type(e).__name__}
    # An event title is written by whoever sent the invite and goes straight
    # into every chat prompt via access_preamble() (audit B19): one inert line,
    # chat-template tokens defanged (dashboard/untrusted.py). Imported here,
    # not at module load — aux files exec into server.py's globals.
    try:
        from untrusted import line as _goog_line
    except Exception:
        import re as _goog_re

        def _goog_line(s, limit=120):
            return _goog_re.sub(r"\s+", " ", str(s)).strip()[:limit]
    events = []
    for it in (data.get("items") or []):
        if not isinstance(it, dict) or it.get("status") == "cancelled":
            continue
        start = it.get("start") or {}
        title = _goog_line(it.get("summary") or "(untitled)", 120) or "(untitled)"
        tstr = ""
        if start.get("dateTime"):
            try:
                dt = _goog_datetime.datetime.fromisoformat(
                    str(start["dateTime"]).replace("Z", "+00:00")).astimezone()
                tstr = dt.strftime("%H:%M")
            except Exception:
                tstr = ""
        events.append({"time": tstr, "title": title})
    return {"available": True, "events": events[:12]}


def _goog_calendar_events():
    return _cached("goog_calendar", GOOG_CAL_TTL, _goog_calendar_fetch)


# --------------------------------------------------------------------------
# background priming — access_preamble() reads _goog_calendar_events() on
# EVERY chat turn; left alone, a cache miss (first call, or GOOG_CAL_TTL
# having lapsed) pays a real network round trip inline on the request path.
# Refresh proactively on an interval at/under the TTL so, once warm, the
# request path is reading _widget_cache and nothing else. Connected-only:
# no point spending a cycle when there's no token to use.
# --------------------------------------------------------------------------
GOOG_CAL_PRIME_INTERVAL = GOOG_CAL_TTL  # 300s


def _goog_calendar_prime_loop():
    while True:
        try:
            if _goog_status_compute().get("connected"):
                _goog_calendar_events()
        except Exception as e:
            print(f"[aux_google] calendar prime failed: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
        time.sleep(GOOG_CAL_PRIME_INTERVAL)


if not globals().get("_goog_calendar_prime_started"):
    globals()["_goog_calendar_prime_started"] = True
    try:
        threading.Thread(target=_goog_calendar_prime_loop, daemon=True).start()
    except Exception as _e:                                  # pragma: no cover
        print(f"[aux_google] calendar prime thread failed to start: {_e!r}",
              file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
register_get("/api/google/status", _goog_status_handler)
# POST, not GET (R2-48): building the URL rewrites the PKCE pending file, and
# the same-origin guard lets a cross-site NAVIGATION through on GET by design,
# so any page could silently break a connect the owner had half done.
register_post("/api/google/auth_url", _goog_authurl_handler)
register_post("/api/google/client_secret", _goog_secret_handler)
register_post("/api/google/auth_code", _goog_authcode_handler)
register_post("/api/google/disconnect", _goog_disconnect_handler)
