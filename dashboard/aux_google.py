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
#     gmail.readonly   — read mail; sending is impossible at Google's own
#                        authorization layer
#     calendar         — read/manage calendar (today widget, calendar_gap)
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
GOOG_REVOKE_URI = "https://oauth2.googleapis.com/revoke"

# The ONLY scopes this dashboard will ever request (see mandate above).
GOOG_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
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
    "https://www.googleapis.com/auth/calendar": "Calendar",
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


def _goog_post_form(url, fields, timeout=25):
    """POST x-www-form-urlencoded; returns (http_status, parsed_json|{}).
    Raises nothing; upstream errors come back as (status, {"error": ...}).
    The posted fields are never included in any error we surface."""
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
           "missing_features": [], "reason": "", "checked_at": time.time()}
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
        url = ((inst.get("auth_uri") or GOOG_AUTH_URI) + "?"
               + urllib.parse.urlencode(params))
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
        raw = (b.get("code") or "").strip()
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
        token_uri = inst.get("token_uri") or GOOG_TOKEN_URI

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
# routes
# --------------------------------------------------------------------------
register_get("/api/google/status", _goog_status_handler)
register_get("/api/google/auth_url", _goog_authurl_handler)
register_post("/api/google/client_secret", _goog_secret_handler)
register_post("/api/google/auth_code", _goog_authcode_handler)
register_post("/api/google/disconnect", _goog_disconnect_handler)
