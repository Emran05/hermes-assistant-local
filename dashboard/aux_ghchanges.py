# aux_ghchanges.py — the GitHub sandbox CHANGE PIPELINE (round 3, cluster R3-GH2).
#
# docs/plans/github-sandbox-2026-10.md §3 (sandbox, snapshot, checks, decide,
# push, retract), §5.1 (routes), §5.4 (the decide speed bump), §8.1.
#
# Exec'd into server.py's globals by the aux loader. aux files exec SORTED and
# "aux_ghc" < "aux_git", so this module runs BEFORE aux_github.py (the
# connector): every _gh_* / GH_* name, recorder_record_local, _wt_send_telegram,
# the aux_convos secret regexes, ALLOWED_HOSTS and _hdr are resolved BY NAME AT
# CALL TIME, never captured at load (the aux_index / aux_onboarding discipline).
#
# What it owns
#   ~/.hermes/dashboard/github/changes/<id>.json   0600  change requests (§3.3)
#   ~/.hermes/dashboard/github/mirrors/<o>__<r>.git 0700  dashboard-owned bare mirrors
#   ~/.hermes/dashboard/github/tmp/                 0700  HOME, index files, template
#   ~/HermesSandbox/github/<owner>/<repo>/          the agent's sandbox clone
#     (created here, never touched again: the dashboard never runs git in it)
#
# The laws this file enforces
#   * The parent of every commit Hermes builds is the sha GITHUB'S API reports
#     for the target branch, never a ref read from either local store (an
#     agent push into the mirror's refs/heads/main would otherwise make hidden
#     commits ride along); `rev-list <commit> --not <parent>` must be exactly
#     [commit].
#   * One push shape: <40-hex sha>:refs/heads/hermes/<slug>. No other refspec,
#     no force, no tags, no default branch. A deletion (retract) is a
#     compare-and-swap that only removes the branch while it still points at
#     the sha the ledger says Hermes left there.
#   * The token reaches git only as _gh_git_env()'s env triple, for ONE
#     network call; HTTP only through _gh_api(). Never in a response, a log
#     line, a recorder row, argv, a git config or a remote URL.
#   * decide / retract are OWNER-ONLY. Until F05's app-held nonce lands (B02)
#     the interim control is a SPEED BUMP, named as such: the request must
#     carry a present, allowed Origin and Sec-Fetch-Site: same-origin (curl
#     sends neither unless forged). Plus the digest, rate limits, and a
#     Telegram notice per push that the agent cannot suppress.
#   * State moves under one lock; pending -> pushing happens exactly once.
#     The ledger row is appended BEFORE the push, while _GH_NET_LOCK is held,
#     so the 6-hourly drift check never sees Hermes's own push as foreign —
#     not even after a crash between the push and the bookkeeping.
#   * Fail closed: no recorder -> 503; no secret scanner -> refused; a GitHub
#     answer Hermes cannot read -> nothing is pushed.
#   * Text is never trusted to be Markdown it cannot be sure of. @mentions are
#     left alone only inside a fenced block, a single-line code span (parsed per
#     table cell, cmark's way) or an autolinked URL, and not at all when the
#     text holds raw HTML; every other mention gets a code span of its own
#     (a backtick width no uncertain run uses) or, with HTML present, a
#     full-width @. Hidden characters are marked, never silently dropped from
#     a body; every scan sees non-UTF-8 files and text-like "binary" ones
#     too, and content that is not in the diff (any binary) needs a tick (E02).
#
# Stdlib only. New names are GHC_/_ghc_ prefixed. datetime only under a private
# alias (the aux-module gotcha). No settings.json writes.

import os
import re
import sys
import json
import time
import bisect
import hashlib
import secrets as _ghc_secrets
import socket as _ghc_socket
import tempfile
import threading
import urllib.parse
import datetime as _ghc_datetime


def _ghc_import(name):
    try:
        return __import__(name)
    except Exception:                                    # pragma: no cover
        import importlib.util as _ghc_ilu
        here = globals().get("HERE") or os.path.dirname(os.path.abspath(
            __file__ if "__file__" in globals() else "."))
        spec = _ghc_ilu.spec_from_file_location(name, os.path.join(here, name + ".py"))
        mod = _ghc_ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
        return mod


_ghc_api_mod = _ghc_import("gh_api")
_ghc_git = _ghc_import("gh_git")
try:
    import permissions as _ghc_perm
except Exception:                                        # pragma: no cover
    _ghc_perm = None

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
GHC_ID_RE = re.compile(r"^ghc-[0-9a-f]{12}$")
GHC_SLUG_RE = _ghc_git.SLUG_RE
GHC_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GHC_STATES = ("pending", "pushing", "pushed", "pr_open", "rejected", "withdrawn",
              "expired", "failed", "cancelled", "retracted")
GHC_DECISIONS = ("push_pr", "push_only", "reject")
GHC_RESERVED_SLUGS = ("main", "master", "head", "trunk", "default")
GHC_SOURCES = ("hub", "telegram", "cli", "api", "quickask", "mcp")
GHC_TITLE_MAX = 120
GHC_BODY_MAX = 8 * 1024
GHC_TITLE_IN_MAX = 4000            # what a request may send before sanitising
GHC_BODY_IN_MAX = 64 * 1024
GHC_SECRET_MIN = 12                # shorter known values are ignored (false hits)
GHC_SECRET_KEY_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD)", re.I)
GHC_NET_WAIT = 90                  # seconds to wait for _GH_NET_LOCK at decide
GHC_KEEP_S = 30 * 86400            # finished change files are kept 30 days
GHC_KEEP_MAX = 300                 # ...and at most this many (newest win)
GHC_REQ_WINDOW = 600
GHC_WORKFLOW_CAP = 30
GHC_WORKFLOW_MAX = 1024 * 1024
GHC_VERIFY_TRIES = 5               # the ref API can lag a push by a few seconds
GHC_MAX_FINDINGS = 20
GHC_ZERO = "0" * 40
# C-longline (E02): an added line longer than this, or holding a run of this
# many blank characters (or this many screen columns of them: a tab is up to
# 8) before more text, can carry code the review sheet shows off-screen
GHC_LONGLINE = 400
GHC_WS_RUN = 40
GHC_WS_COLS = 80
# a file git calls binary is "text-like" (E02) when, its NUL bytes left out,
# at most this share of its characters are undecodable or control characters
GHC_TEXTISH_BAD = 0.05
GHC_PR_FOOTER = ("Opened by Hermes Assistant from a local sandbox, after you approved it "
                 "on {host} at {time}. Change {id}.")
_GHC_LIMITS_FALLBACK = {"max_files": 200, "max_bytes": 5 * 1024 * 1024,
                        "max_file": 2 * 1024 * 1024, "max_diff": 1024 * 1024,
                        "max_repo_kb": 2 * 1024 * 1024, "pending_ttl_s": 24 * 3600,
                        "repo_requests_per_10min": 3, "max_pushes_day": 10,
                        "reads_per_10min": 30}
# hidden characters (C-unicode): bidi embeddings/overrides/isolates AND the
# bidi marks (LRM, RLM, ALM), zero-width characters, the word joiner and the
# invisible math operators (and the reserved U+2065 between them and the
# isolates), the deprecated format controls, soft hyphen, combining grapheme
# joiner, Mongolian vowel separator, ALL FOUR Hangul fillers (U+115F/U+1160,
# U+3164/U+FFA0: identifier characters that draw as nothing), the Khmer
# inherent vowels U+17B4/U+17B5, BOM, line/paragraph separators, the reserved
# U+FFF0-FFF8 and the interlinear annotation marks, the shorthand format
# controls U+1BCA0-1BCA3, the musical-symbol format controls U+1D173-1D17A,
# the WHOLE plane-14 block U+E0000-E0FFF (TAG characters — invisible ASCII
# smuggling — the supplementary variation selectors and the reserved rest),
# RUNS of two or more variation selectors, the Mongolian free variation
# selectors included (the "GlassWorm" way of hiding a payload after one
# visible character; a single VS16 after an emoji, or one FVS after a Mongolian
# letter, is ordinary text), C0 controls except \t \n \r, DEL and C1 controls.
# That is every Default_Ignorable_Code_Point, the lone-selector exemption
# aside. aux_github.js HIDDEN_RE is the same class: change both (the unit tier
# compares them code point by code point, t_r3_hidden_chars.py).
_GHC_HIDDEN_RE = re.compile(
    "(?:[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u2028-\u202e"
    "\u2060-\u206f\u3164\uffa0\ufeff\ufff0-\ufffb"
    "\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff"
    "\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|[\ufe00-\ufe0f\u180b-\u180d\u180f]{2,})")
# C-longline: what shows as BLANK on screen is more than space and tab. Every
# Unicode space (NBSP, the en/em family, the ideographic space), the braille
# blank and the Hangul fillers push text sideways like a space does, and a
# hidden or zero-width character between two spaces does not end the run.
_GHC_BLANK_WIDE = "\u3000\u2800\u3164\uffa0\u115f\u1160"
_GHC_BLANK_RUN_RE = re.compile(
    "(?:\\s|[\u2800\ufe00-\ufe0f\u180b-\u180d\u180f]|%s){10,}" % _GHC_HIDDEN_RE.pattern)
_GHC_PEM_RE = re.compile(rb"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----")
# GitHub's mention filter reads the rendered text and accepts a mention after
# ANY non-word character (a dot, a slash, a plus, a hyphen too), so only an
# ASCII letter or digit in front means "not a mention" (an e-mail address).
# An underscore does NOT: Markdown removes the _ of _@user_ and __@user__
# (emphasis), which leaves the mention at the start of its text. Nor does a
# non-ASCII letter on either side: the filter's word class is ASCII only.
_GHC_MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:@[A-Za-z0-9][A-Za-z0-9-]{0,38}"
    r"(?:/[A-Za-z0-9][A-Za-z0-9_.-]{0,99})?)+)(?![A-Za-z0-9-])")
# an @ inside a URL that GitHub autolinks (https://host/@user, www.x/@y) is part
# of the link, never a mention: wrapping it would only break the link. The URL
# must run unbroken up to the @ (a closing bracket, quote, space or backtick
# ends it), so "[x](https://a/b)@user" is still a mention. GFM autolinks an
# extended URL only at line start or after whitespace or * _ ~ ( (E60: so
# "docs/www.@victim" is plain text and its mention live), and only when the
# host has no underscore in its last two labels (_ghc_in_autolink).
_GHC_URL_TAIL_RE = re.compile(
    r"(?i)(?<![^\s*_~(])(?:(?:https?|ftp)://|(?=www\.))([^\s<>()\[\]\"'`]*)$")
# Raw HTML in a Markdown body (an HTML block, an inline tag, an autolink in
# angle brackets) changes what is Markdown at all, so nothing is trusted there.
_GHC_HTMLISH_RE = re.compile(r"<[A-Za-z/!?]")
_GHC_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_GHC_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_GHC_FULLWIDTH_AT = "\uff20"
_GHC_AT_ENTITY_RE = re.compile(r"&(?:#0*64|#[xX]0*40|commat);")
# Markdown link-reference definitions that render as NOTHING: the two comment
# idioms ([//]: # (text), [comment]: <> (text)) are as invisible as <!-- -->
_GHC_INVISIBLE_REF_RE = re.compile(
    r"^[ ]{0,3}\[(?://|comment)\]:[ \t]*(?:#|<>)[^\n]*$", re.I | re.M)
# files a CI job plausibly executes (C-ci, a labelled heuristic)
_GHC_CI_RUN_RE = re.compile(
    r"(^|/)(Makefile|GNUmakefile|[^/]*\.mk|package(-lock)?\.json|npm-shrinkwrap\.json|"
    r"yarn\.lock|pnpm-lock\.yaml|pyproject\.toml|setup\.(py|cfg)|requirements[^/]*\.txt|"
    r"tox\.ini|noxfile\.py|conftest\.py|Pipfile(\.lock)?|poetry\.lock|Gemfile(\.lock)?|"
    r"Rakefile|Cargo\.(toml|lock)|go\.(mod|sum)|build\.gradle(\.kts)?|pom\.xml|"
    r"Dockerfile|docker-compose\.ya?ml|justfile|Taskfile\.ya?ml|[^/]*\.(sh|bash|ps1))$|"
    r"(^|/)(scripts?|tests?|ci|\.github/actions|\.husky)/", re.I)

# E09: refs/hermes-hold/<change id> -> the change's commit, while it is pending
# or pushing, so `git prune` never takes objects a decision still needs
_GHC_HOLD_PREFIX = "refs/hermes-hold/"
GHC_HOLD_ORPHAN_S = 3600           # a hold whose change record never appeared

_GHC_LOCK = globals().get("_GHC_LOCK") or threading.RLock()
_GHC_INFLIGHT = globals().get("_GHC_INFLIGHT") or set()
_GHC_LOG_ONCE = globals().get("_GHC_LOG_ONCE") or set()
# GitHub reads per repository (prepare / refresh / change requests, refused
# or not), in memory: a request refused after its fetch is not a stored
# change, so C-rate alone would not bound how often the agent makes Hermes
# fetch
_GHC_ATTEMPTS = globals().get("_GHC_ATTEMPTS") or {}


class _GhcRefuse(Exception):
    """A refusal with its HTTP answer: raise _GhcRefuse(payload, status)."""

    def __init__(self, payload, status):
        Exception.__init__(self, payload.get("error") if isinstance(payload, dict) else "")
        self.payload, self.status = payload, status


# --------------------------------------------------------------------------
# foreign names (resolved at CALL time) + small helpers
# --------------------------------------------------------------------------
def _ghc_fn(name):
    fn = globals().get(name)
    return fn if callable(fn) else None


def _ghc_call(name, *a, **kw):
    fn = _ghc_fn(name)
    if fn is None:
        raise _GhcRefuse({"ok": False, "error": "connector_unavailable",
                          "hint": "The GitHub connector did not load. Restart the dashboard."},
                         503)
    return fn(*a, **kw)


def _ghc_now():
    return time.time()


def _ghc_home():
    return globals().get("HOME") or os.path.expanduser("~")


def _ghc_data():
    return globals().get("DATA") or os.path.join(_ghc_home(), ".hermes", "dashboard")


def _ghc_root():
    return globals().get("GH_DIR") or os.path.join(_ghc_data(), "github")


def _ghc_limits():
    lim = dict(_GHC_LIMITS_FALLBACK)
    g = globals().get("GH_LIMITS")
    if isinstance(g, dict):
        for k, v in g.items():
            if isinstance(v, int) and not isinstance(v, bool):
                lim[k] = v
    return lim


def _ghc_token():
    fn = _ghc_fn("_gh_token")
    try:
        return fn() if fn else None
    except Exception:
        return None


def _ghc_scrub(text, limit=300):
    """One printable line with the token (every form) and anything
    token-shaped replaced — the only way upstream text leaves this module."""
    return _ghc_api_mod.short(text, limit, _ghc_token() or "")


def _ghc_log(msg, once=None):
    if once:
        if once in _GHC_LOG_ONCE:
            return
        _GHC_LOG_ONCE.add(once)
    try:
        print("[aux_ghchanges] " + _ghc_scrub(msg), file=sys.stderr, flush=True)
    except Exception:
        pass


def _ghc_line(text, limit=200):
    try:
        import untrusted as _ghc_ut
        return _ghc_ut.line(text, limit)
    except Exception:
        return _ghc_api_mod.short(text, limit)


def _ghc_iso(ts):
    return _ghc_datetime.datetime.fromtimestamp(
        float(ts), tz=_ghc_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ghc_12h(ts):
    lt = time.localtime(ts)
    h = lt.tm_hour % 12 or 12
    return "%s %d, %d at %d:%02d %s" % (time.strftime("%b", lt), lt.tm_mday, lt.tm_year, h,
                                        lt.tm_min, "AM" if lt.tm_hour < 12 else "PM")


def _ghc_host():
    try:
        h = _ghc_socket.gethostname()
    except Exception:
        h = ""
    return re.sub(r"[^A-Za-z0-9-]", "", str(h).split(".")[0])[:40] or "this Mac"


def _ghc_mkdir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        if os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _ghc_dir(name):
    _ghc_mkdir(_ghc_root())
    return _ghc_mkdir(os.path.join(_ghc_root(), name))


def _ghc_atomic_write(path, obj):
    raw = json.dumps(obj, indent=1, sort_keys=True).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".ghc_")
    try:
        os.fchmod(fd, 0o600)
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


def _ghc_read_json(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _ghc_safe(fn):
    """Never 500 with a raw exception string (it could carry a token)."""
    def wrapped(ctx):
        try:
            return fn(ctx)
        except _GhcRefuse as r:
            return r.payload, r.status
        except Exception as e:
            _ghc_log("%s failed: %s" % (getattr(fn, "__name__", "route"), type(e).__name__))
            return ({"ok": False, "error": "internal: " + type(e).__name__}, 500)
    wrapped.__name__ = getattr(fn, "__name__", "wrapped")
    return wrapped


def _ghc_body(ctx):
    return ctx.body if isinstance(getattr(ctx, "body", None), dict) else {}


def _ghc_refuse(status, error, hint=None, **kw):
    p = {"ok": False, "error": error}
    if hint:
        p["hint"] = hint
    p.update(kw)
    raise _GhcRefuse(p, status)


# --------------------------------------------------------------------------
# owner-only: the F05 hook, the interim speed bump, the gate predicate
# --------------------------------------------------------------------------
_GHC_OWNER_HINT = ("Only the owner can do this, from the review sheet in the dashboard "
                   "window. If you are the agent: tell the owner the change is waiting "
                   "for their review, and stop.")


def _ghc_hdr(headers, name):
    fn = _ghc_fn("_hdr")
    if fn is not None:
        try:
            return fn(headers, name)
        except Exception:
            return None
    if not headers:
        return None
    low = name.lower()
    try:
        for k, v in headers.items():
            if str(k).lower() == low:
                return v
    except Exception:
        return None
    return None


def _ghc_allowed_hosts():
    hs = globals().get("ALLOWED_HOSTS")
    if isinstance(hs, (set, frozenset, list, tuple)) and hs:
        return {str(h).lower() for h in hs}
    port = globals().get("DASH_PORT") or 7788
    return {"127.0.0.1:%s" % port, "localhost:%s" % port, "[::1]:%s" % port}


def _ghc_from_dashboard(ctx):
    """The §5.4 interim SPEED BUMP — not a boundary (B02: any same-uid process
    can forge both headers). True only when the request carries a PRESENT
    Origin that is one of the dashboard's own origins and equals its Host, plus
    Sec-Fetch-Site: same-origin. The WKWebView and Quick Ask send both; curl
    sends neither unless told to. A RouteCtx without headers fails closed."""
    h = getattr(ctx, "headers", None)
    if not h:
        return False
    origin = str(_ghc_hdr(h, "Origin") or "").strip()
    host = str(_ghc_hdr(h, "Host") or "").strip().lower()
    sfs = str(_ghc_hdr(h, "Sec-Fetch-Site") or "").strip().lower()
    if not origin or not host or sfs != "same-origin":
        return False
    try:
        u = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    netloc = (u.netloc or "").lower()
    if u.scheme != "http" or u.path not in ("", "/") or netloc != host:
        return False
    return netloc in _ghc_allowed_hosts()


def _ghc_owner_check(ctx):
    """Raise the 403 unless this request may act as the owner: F05's nonce
    (`ctx.nonce_ok is False` -> refuse; None = pre-F05, tolerated) AND the
    interim speed bump."""
    if getattr(ctx, "nonce_ok", None) is False or not _ghc_from_dashboard(ctx):
        raise _GhcRefuse({"ok": False, "error": "owner_only", "hint": _GHC_OWNER_HINT}, 403)


def _ghc_route_gated(method, path, body=None):
    """The F05 predicate for this module's owner-only routes (§5.4): approving
    a change and retracting a pushed one need the owner's proof; rejecting
    (like withdrawing) only reduces capability, the §5.1 asymmetry rule.
    Unknown shapes fail CLOSED (gated)."""
    if str(method or "").upper() != "POST":
        return False
    b = body if isinstance(body, dict) else {}
    if path == "/api/github/changes/decide":
        return b.get("decision") != "reject"
    if path == "/api/github/changes/retract":
        return True
    return False


# aux_github.py (which sorts after this file) only setdefault()s these two
# paths, so this finer predicate is the one F05's gate consults
_ghc_gates = globals().setdefault("ROUTE_OWNER_GATES", {})
for _ghc_gp in ("/api/github/changes/decide", "/api/github/changes/retract"):
    _ghc_gates[_ghc_gp] = _ghc_route_gated


# --------------------------------------------------------------------------
# side channels: recorder, permissions audit, Trust tier, Telegram
# --------------------------------------------------------------------------
def _ghc_recorder():
    return _ghc_fn("recorder_record_local")


def _ghc_require_recorder():
    if _ghc_recorder() is None:
        _ghc_refuse(503, "recorder_unavailable",
                    "The Flight Recorder is not available, so Hermes refuses to act on "
                    "GitHub unrecorded.")


def _ghc_record(event, target, summary, args=None, status="done", after_state=None,
                session=""):
    fn = _ghc_recorder()
    if fn is None:
        return False
    try:
        r = fn(tool="github_" + event, target=_ghc_scrub(target, 200), kind="other",
               reversible="no", source="dashboard", args=args or {}, status=status,
               summary=_ghc_scrub(summary, 300), after_state=after_state,
               session=str(session or "")[:80],
               tool_call_id="ghc-%s-%d-%s" % (event, int(_ghc_now() * 1000),
                                              _ghc_secrets.token_hex(3)))
        return bool(isinstance(r, dict) and r.get("ok"))
    except Exception:
        return False


def _ghc_pattern(repo):
    return "github-write:" + str(repo or "").lower()


def _ghc_trust(repo):
    """(tier, verdict) from the Trust engine for github-write:<repo>. `auto`
    is clamped to `ask` in code as well (the class floor already does it, the
    aux_shortcuts.py:558 defence). An engine error asks."""
    pk = _ghc_pattern(repo)
    try:
        v = _ghc_perm.decide({"pattern_key": pk, "command": "push to %s hermes/*" % repo})
    except Exception:
        v = {"tier": "ask", "class": "github-write", "reason": "engine error -> ask"}
    if not isinstance(v, dict):
        v = {"tier": "ask", "class": "github-write", "reason": "engine error -> ask"}
    tier = v.get("tier") if v.get("tier") in ("auto", "ask", "never") else "ask"
    if tier == "auto":
        tier = "ask"
    return tier, v


def _ghc_audit(rec, verdict, action, command):
    try:
        if _ghc_perm is not None:
            _ghc_perm.audit("github:" + rec["id"],
                            {"pattern_key": _ghc_pattern(rec["repo"]), "command": command},
                            verdict or {}, action)
    except Exception:
        pass


def _ghc_notify(text):
    fn = _ghc_fn("_gh_notify")
    if fn is None:
        return False, "notifier unavailable"
    try:
        return fn(text)
    except Exception as e:
        return False, type(e).__name__


# --------------------------------------------------------------------------
# change records (changes/<id>.json, one module lock)
# --------------------------------------------------------------------------
def _ghc_path(cid):
    if not isinstance(cid, str) or not GHC_ID_RE.fullmatch(cid):
        return None
    return os.path.join(_ghc_dir("changes"), cid + ".json")


def _ghc_load(cid):
    p = _ghc_path(cid)
    if not p:
        return None
    r = _ghc_read_json(p)
    if not isinstance(r, dict) or r.get("id") != cid or r.get("state") not in GHC_STATES:
        return None
    return r


def _ghc_save(rec):
    _ghc_atomic_write(_ghc_path(rec["id"]), rec)


def _ghc_sweep(rec, now=None):
    """Lazy state fixes (call under _GHC_LOCK): a pending change past its TTL
    expires; a 'pushing' change nobody in THIS process is pushing was
    interrupted (a dashboard restart mid-push) and is marked failed."""
    now = _ghc_now() if now is None else now
    if rec["state"] == "pending" and (rec.get("expires_at") or 0) <= now:
        rec["state"] = "expired"
        rec["error"] = "No decision within %d hours." % (
            int(_ghc_limits()["pending_ttl_s"]) // 3600)
        return True
    if rec["state"] == "pushing" and rec["id"] not in _GHC_INFLIGHT:
        rec["state"] = "failed"
        rec["interrupted"] = True            # E57: _ghc_reconcile settles the ledger
        rec["error"] = ("Interrupted while pushing (the dashboard restarted). Check the "
                        "branch on GitHub before asking Hermes to request it again.")
        return True
    return False


def _ghc_all(now=None):
    """Every record, oldest first, with lazy sweeps applied (and finished
    records older than GHC_KEEP_S removed)."""
    now = _ghc_now() if now is None else now
    out = []
    with _GHC_LOCK:
        d = _ghc_dir("changes")
        try:
            names = sorted(os.listdir(d))
        except OSError:
            names = []
        for n in names:
            if not n.endswith(".json"):
                continue
            rec = _ghc_load(n[:-5])
            if rec is None:
                continue
            if _ghc_sweep(rec, now):
                _ghc_save(rec)
            if rec["state"] not in ("pending", "pushing") and \
                    now - float(rec.get("requested_at") or 0) > GHC_KEEP_S:
                try:
                    os.unlink(os.path.join(d, n))
                except OSError:
                    pass
                continue
            out.append(rec)
    out.sort(key=lambda r: float(r.get("requested_at") or 0))
    done = [r for r in out if r["state"] not in ("pending", "pushing")]
    if len(done) > GHC_KEEP_MAX:
        # every list / rate check parses every record (a diff each): an agent
        # that requests in a loop must not grow that without bound. Oldest
        # finished records go first; waiting and in-flight ones never do.
        # (changes that never left the Mac go first; a pushed one is what a
        # retract needs, so it outlives them)
        order = [r for r in done if r["state"] not in ("pushed", "pr_open")] + \
                [r for r in done if r["state"] in ("pushed", "pr_open")]
        drop = {r["id"] for r in order[:len(done) - GHC_KEEP_MAX]}
        with _GHC_LOCK:
            for cid in drop:
                try:
                    os.unlink(os.path.join(_ghc_dir("changes"), cid + ".json"))
                except OSError:
                    pass
        out = [r for r in out if r["id"] not in drop]
    return out


def _ghc_get(cid):
    with _GHC_LOCK:
        rec = _ghc_load(cid)
        if rec is not None and _ghc_sweep(rec):
            _ghc_save(rec)
        return rec


def _ghc_transition(cid, frm, fn=None):
    """Under the lock: re-read, require state in `frm`, apply fn(rec), save.
    -> the new record, or None when the state was not in `frm`."""
    with _GHC_LOCK:
        rec = _ghc_load(cid)
        if rec is None:
            return None
        if _ghc_sweep(rec):
            _ghc_save(rec)
        if rec["state"] not in frm:
            return None
        if fn is not None:
            fn(rec)
        _ghc_save(rec)
        return rec


def _ghc_set(cid, **fields):
    with _GHC_LOCK:
        rec = _ghc_load(cid)
        if rec is None:
            return None
        rec.update(fields)
        _ghc_save(rec)
        return rec


def _ghc_summary(rec):
    keys = ("id", "repo", "private", "branch", "slug", "mode", "state", "title", "stat",
            "approvable", "blocked", "needs_ack", "confirm_public", "requested_at",
            "expires_at", "decided_at", "decision", "pushed_sha", "pr", "error",
            "default_branch", "parent", "source", "retracted_at")
    out = {k: rec.get(k) for k in keys}
    out["review_url"] = "#github-review=" + rec["id"]
    return out


def _ghc_full(rec):
    out = _ghc_summary(rec)
    for k in ("body", "checks", "files", "diff", "diff_truncated", "diff_lines", "digest", "tree",
              "commit", "base", "pr_footer", "sanitized", "target_ref", "author",
              "interrupted", "reconciled"):
        out[k] = rec.get(k)
    return out


# --------------------------------------------------------------------------
# exports for the connector (resolved there by name) and Data & Network
# --------------------------------------------------------------------------
def _ghc_pending():
    """[summary] of every change waiting for the owner's decision."""
    return [_ghc_summary(r) for r in _ghc_all() if r["state"] == "pending"]


def _ghc_cancel_where(pred, reason):
    n, repos = 0, set()
    for r in _ghc_all():
        if r["state"] != "pending" or not pred(r):
            continue

        def cancel(rec):
            rec["state"] = "cancelled"
            rec["error"] = "Cancelled: %s." % _ghc_line(reason, 120)
        if _ghc_transition(r["id"], ("pending",), cancel):
            n += 1
            repos.add(r["repo"])
            _ghc_record("change_cancel", "%s@%s" % (r["repo"], r["branch"]),
                        "change %s cancelled (%s)" % (r["id"], _ghc_line(reason, 80)))
    for full in sorted(repos):
        _ghc_gc(full)
    return n


def _ghc_cancel_all(reason="cancelled"):
    """Cancel every pending change (disconnect / revoke). -> how many."""
    return _ghc_cancel_where(lambda r: True, reason)


def _ghc_cancel_repo(repo, reason="repository disabled"):
    key = str(repo or "").lower()
    return _ghc_cancel_where(lambda r: str(r.get("repo") or "").lower() == key, reason)


def _ghc_last_push_ts():
    """Newest push / PR Hermes made (the Data & Network github_push evidence).
    The ledger is the connector's; a file read, no network."""
    fn = _ghc_fn("_gh_last_push_ts")
    if fn is not None:
        try:
            return fn()
        except Exception:
            return None
    return None


# --------------------------------------------------------------------------
# text: C-text (title/body as they will be sent)
# --------------------------------------------------------------------------
def _ghc_strip_comments(t):
    """HTML comments are invisible on GitHub and an injection carrier for the
    next agent that reads the PR: removed (an unterminated one to the end), and
    so are the Markdown "comment" link-reference idioms, which render as
    nothing too."""
    n0 = len(t)
    t = re.sub(r"<!--[\s\S]*?-->", "", t)
    i = t.find("<!--")
    if i >= 0:
        t = t[:i]
    t = _GHC_INVISIBLE_REF_RE.sub("", t)
    return t, len(t) != n0


_GHC_REFDEF_RE = re.compile(r"^ {0,3}\[((?:[^\]\\\n]|\\.){1,999})\]:(.*)$")
_GHC_REFTITLE_RE = re.compile(r"""^\s*("[^"]*"|'[^']*'|\([^)]*\))\s*$""")
_GHC_REFUSE_RE = re.compile(r"\[((?:[^\]\\\n]|\\.){1,999})\]")


def _ghc_strip_unused_refs(t):
    """E60: a Markdown link-reference definition renders as NOTHING, which
    makes it a carrier for text the owner never sees (like <!-- -->). Every
    definition outside a code fence whose label no [..] elsewhere in the
    text uses is removed, with a destination or title on the next line.
    -> (text, removed?)."""
    lines = t.split("\n")
    fence, defs, def_lines = None, [], set()
    for i, ln in enumerate(lines):
        if fence is None:
            m = _GHC_FENCE_OPEN_RE.match(ln)
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = (m.group(1)[0], len(m.group(1)))
                continue
            d = _GHC_REFDEF_RE.match(ln)
            if d:
                defs.append((i, d.group(1), d.group(2)))
                def_lines.add(i)
        elif re.match(r"^ {0,3}(%s{%d,})[ \t]*$" % (re.escape(fence[0]), fence[1]), ln):
            fence = None

    def norm(x):
        return re.sub(r"\s+", " ", x).strip().lower()
    used = set()
    for i, ln in enumerate(lines):
        if i in def_lines:
            continue
        for u in _GHC_REFUSE_RE.finditer(ln):
            used.add(norm(u.group(1)))
    drop = set()
    for i, label, rest in defs:
        if norm(label) in used:
            continue
        drop.add(i)
        j = i + 1
        if not rest.strip() and j < len(lines) and lines[j].strip() and j not in def_lines:
            drop.add(j)
            j += 1
        if j < len(lines) and j not in def_lines and _GHC_REFTITLE_RE.match(lines[j]):
            drop.add(j)
    if not drop:
        return t, False
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop), True


def _ghc_line_spans(ln):
    """Single-line inline code spans of one line, cmark's way (a backslash
    escapes a following punctuation character, so an escaped backtick is no
    opener; a closer is the next RAW backtick run of exactly the opener's
    length, escaped or not). The line is cut at every unescaped | first and
    each piece parsed alone: in a table row the cells are split before any
    inline parsing, so a span never reaches across a pipe (a line that is not
    a table row just loses the spans around its pipes, which only means more
    mentions get wrapped)."""
    spans, start, i, n = [], 0, 0, len(ln)
    cells = []
    while i < n:
        if ln[i] == "\\" and i + 1 < n and ln[i + 1] in _GHC_PUNCT:
            i += 2
            continue
        if ln[i] == "|":
            cells.append((start, i))
            start = i + 1
        i += 1
    cells.append((start, n))
    for cs, ce in cells:
        cell, i, m_ = ln[cs:ce], 0, ce - cs
        while i < m_:
            c = cell[i]
            if c == "\\" and i + 1 < m_ and cell[i + 1] in _GHC_PUNCT:
                i += 2
                continue
            if c != "`":
                i += 1
                continue
            j = i
            while j < m_ and cell[j] == "`":
                j += 1
            width, k, found = j - i, j, None
            while k < m_:
                if cell[k] != "`":
                    k += 1
                    continue
                q = k
                while q < m_ and cell[q] == "`":
                    q += 1
                if q - k == width:
                    found = (k, q)
                    break
                k = q
            if found is None:
                i = j
                continue
            spans.append((cs + i, cs + found[1]))
            i = found[1]
    return spans


def _ghc_protected(t):
    """[(start, end)] of the text GitHub CERTAINLY renders as code: fenced code
    blocks and single-line code spans (never one that reaches across a line: a
    blank line, a list item, a quote or a heading starts a new inline context).
    Nothing is trusted when the text holds raw HTML. A mention is only ever left
    alone where the rendering is certain; everywhere else it is neutralised.

    A fence is certain only when its opener is indented by at most ONE space:
    that can never be inside a list item (whose content starts at column 2 or
    later), so it is a top-level block that only its own closing fence ends.
    A fence indented 2 or 3 spaces may sit inside a list item, where the END
    OF THE ITEM closes it (a less indented line after it is ordinary text, its
    mention live) and where a closer may be indented deeper than 3 spaces. The
    body keeps its indentation since E59, so such a fence, and every fence
    after it, is not trusted: from there on only single-line code spans are
    (code whether the line is inside a fenced block or not)."""
    if _GHC_HTMLISH_RE.search(t):
        return []
    out, off, fence, unsure = [], 0, None, False
    for ln in t.split("\n"):
        end = off + len(ln)
        if fence is None:
            m = _GHC_FENCE_OPEN_RE.match(ln)
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                if not unsure and len(ln) - len(ln.lstrip(" ")) <= 1:
                    fence = (m.group(1)[0], len(m.group(1)), off)
                else:
                    # the fence line itself is an info string or literal code
                    # either way (never inline text); what FOLLOWS is unsure
                    unsure = True
                    out.append((off, end))
            else:
                out += [(off + a, off + b) for a, b in _ghc_line_spans(ln)]
        else:
            m = re.match(r"^ {0,3}(%s{%d,})[ \t]*$" % (re.escape(fence[0]), fence[1]), ln)
            if m:
                out.append((fence[2], end))
                fence = None
        off = end + 1
    if fence is not None:
        out.append((fence[2], len(t)))
    return out


def _ghc_in_autolink(t, pos):
    """True when t[pos] (an @) sits inside a URL GitHub certainly autolinks
    (GFM extended autolinks: left boundary, then a valid domain — no
    underscore in its last two labels)."""
    m = _GHC_URL_TAIL_RE.search(t, max(0, pos - 2048), pos)
    if not m:
        return False
    host = re.split(r"[/?#:]", m.group(1), 1)[0]
    labels = host.split(".")
    if len(labels) < 2 or not all(labels[-2:]):
        return False
    return not any("_" in x for x in labels[-2:])


def _ghc_neutralise_mentions(t):
    """@user / @org/team made harmless, so GitHub notifies no one.
      * Where the rendering is certain code (a fenced block, a single-line code
        span, an autolinked URL) the text is left alone.
      * Everywhere else the mention goes in a code span of its own, delimited by
        a backtick run of a width the text does not use (so it can only pair with
        itself), padded by a space where it would fuse with a neighbouring
        backtick or be escaped by a backslash.
      * If the text holds raw HTML a backtick span would not hold (an HTML block
        is not Markdown), so the @ itself becomes the full-width one, which no
        mention filter reads.
      * HTML entities for @ are turned back into @ first (they render as one).
    -> (text, number of mentions)."""
    html = bool(_GHC_HTMLISH_RE.search(t))
    prot = _ghc_protected(t)

    def inside(pos, spans):
        k = bisect.bisect_right(spans, (pos, float("inf"))) - 1
        return k >= 0 and spans[k][0] <= pos < spans[k][1]
    if _GHC_AT_ENTITY_RE.search(t):
        out, last = [], 0
        for m in _GHC_AT_ENTITY_RE.finditer(t):
            if inside(m.start(), prot):
                continue
            out += [t[last:m.start()], "@"]
            last = m.end()
        out.append(t[last:])
        t = "".join(out)
        prot = _ghc_protected(t)
    # the delimiter must not be a width an UNCERTAIN backtick run of the text
    # could pair with (a lone backtick before the mention would take our opener
    # as its closer). Runs inside text that is certainly code pair among
    # themselves and we never insert inside them, so they do not count.
    used = set()
    for m in re.finditer(r"`+", t):
        if not inside(m.start(), prot):
            used.update((len(m.group(0)), len(m.group(0)) - 1))  # an escaped run opens one shorter
    width = 1
    while width in used:
        width += 1
    delim = "`" * width
    out, last, n = [], 0, 0
    for m in _GHC_MENTION_RE.finditer(t):
        if inside(m.start(), prot) or _ghc_in_autolink(t, m.start()):
            continue
        n += 1
        if html:
            out += [t[last:m.start()], m.group(1).replace("@", _GHC_FULLWIDTH_AT)]
            last = m.end()
            continue
        prev = t[m.start() - 1] if m.start() > 0 else ""
        lead = " " if prev in ("`", "\\") else ""
        trail = " " if t[m.end():m.end() + 1] == "`" else ""
        out += [t[last:m.start()], lead, delim, m.group(1), delim, trail]
        last = m.end()
    out.append(t[last:])
    return "".join(out), n


def _ghc_fit(text, cap, measure):
    """Neutralise the mentions in `text` and cut the result to `cap` (by
    `measure`) WITHOUT ever cutting through a mention's code span: the cut is
    made before the wrapping and the wrapped text is re-measured, so a cut can
    never leave a lone backtick in front of a live @mention. -> (text, mentions,
    cut?)."""
    limit, cut = len(text), False
    for _ in range(12):
        piece = text[:limit]
        out, n = _ghc_neutralise_mentions(piece)
        m = measure(out)
        if m <= cap:
            return out, n, cut or len(piece) < len(text)
        cut = True
        limit = min(limit - 1, int(limit * cap / m))
        if limit <= 0:
            break
    out, n = _ghc_neutralise_mentions(text[:max(0, cap // 4)])
    return out, n, True


GHC_INDENT_MAX = 40
_GHC_BODY_CTRL_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")


def _ghc_body_clean(text):
    """The PR description / commit body as Markdown (E59): newlines kept,
    each line's LEADING indentation kept (capped at GHC_INDENT_MAX
    characters, so a line cannot be pushed off-screen), runs of spaces/tabs
    inside a line made one space, trailing spaces dropped, 3+ blank lines
    made one, control characters removed, chat-template tokens neutralised
    (untrusted.neutralise). Not untrusted.body(), which flattens indentation
    because it shapes prompt lines."""
    t = str(text or "")
    try:
        import untrusted as _ghc_ut
        t = _ghc_ut.neutralise(t)
    except Exception:
        pass
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = _GHC_BODY_CTRL_RE.sub(" ", t)
    out = []
    for ln in t.split("\n"):
        lead = re.match(r"[ \t]*", ln).group(0)
        rest = re.sub(r"[ \t]+", " ", ln[len(lead):]).rstrip()
        out.append(lead[:GHC_INDENT_MAX] + rest if rest else "")
    t = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return t.strip("\n")


def _ghc_clean_text(title, body):
    """-> (title, body, notes). Title: one inert line, <= 120 chars. Body: <=
    8 KB, newlines kept. Both: comments stripped, mentions neutralised."""
    notes = {"comments_stripped": False, "mentions": 0, "title_cut": False,
             "body_cut": False, "hidden_chars": False}
    try:
        import untrusted as _ghc_ut
        line_fn = lambda s: _ghc_ut.line(s, 0)                     # noqa: E731
    except Exception:
        line_fn = lambda s: re.sub(r"\s+", " ", s).strip()         # noqa: E731
    body_fn = _ghc_body_clean
    # hidden / direction-changing characters: dropped from the one-line title
    # FIRST (so a comment opener split by one cannot survive the comment
    # stripping), made VISIBLE in the body (U+202E shown as a marker), so what
    # GitHub shows is what was read
    t_raw = str(title or "")
    t_vis = _GHC_HIDDEN_RE.sub("", t_raw)
    t, c1 = _ghc_strip_comments(t_vis)
    b_raw = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    b, c2 = _ghc_strip_comments(b_raw)
    b, c3 = _ghc_strip_unused_refs(b)
    notes["comments_stripped"] = c1 or c2 or c3
    b2 = _ghc_mark_hidden(b)
    notes["hidden_chars"] = (t_vis != t_raw) or (b2 != b)
    b = b2
    t = line_fn(t)
    b = body_fn(b)
    t, m1, notes["title_cut"] = _ghc_fit(t, GHC_TITLE_MAX, len)
    b, m2, notes["body_cut"] = _ghc_fit(b, GHC_BODY_MAX, lambda x: len(x.encode("utf-8")))
    notes["mentions"] = m1 + m2
    return t.rstrip(), b.rstrip(" \t\n"), notes


def _ghc_message(title, body, cid, approved):
    parts = [title]
    if body:
        parts.append(body)
    parts.append("Hermes-Change: %s\nHermes-Approved: %s" % (cid, approved))
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# secrets: C-secret-known (block) and C-secret-shape (warn)
# --------------------------------------------------------------------------
# python-dotenv's grammar (dotenv/parser.py), which is how Hermes reads
# ~/.hermes/.env: optional `export`, a bare or single-quoted key, an optional
# `=`, then a single-quoted, double-quoted (both may span lines) or unquoted
# value, then an optional ` # comment`
_GHC_ENV_KEY_RE = re.compile(r"[^\S\r\n]*(?:export[^\S\r\n]+)?(?:'([^']+)'|([^=#\s]+))"
                             r"[^\S\r\n]*(=[^\S\r\n]*)?")
_GHC_ENV_TAIL_RE = re.compile(r"[^\S\r\n]*(?:#[^\r\n]*)?(?:\r\n|\n|\r|$)")
_GHC_ENV_DQ_ESC = {"\\": "\\", "'": "'", '"': '"', "a": "\a", "b": "\b", "f": "\f",
                   "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _ghc_env_quoted(text, i):
    """dotenv's quoted value regex ('((?:\\'|[^'])*)' and the "-quoted twin)
    at text[i], WITHOUT its exponential backtracking: scan forward taking an
    escaped quote as part of the value; at the end of the text with no
    closing quote, the regex would backtrack to the LAST escaped quote and
    close there, so this does too. -> (body, end) | None."""
    qc, j, n, last_esc = text[i], i + 1, len(text), None
    while j < n:
        c = text[j]
        if c == "\\" and j + 1 < n and text[j + 1] == qc:
            last_esc = j + 1
            j += 2
            continue
        if c == qc:
            return text[i + 1:j], j + 1
        j += 1
    if last_esc is not None:
        return text[i + 1:last_esc], last_esc + 1
    return None


def _ghc_dotenv(text):
    """[(key, value, raw)] from a .env text, parsed the way python-dotenv
    does (E06). `raw` is the rest of the key's first line after `=`, stripped
    and with one pair of outer quotes removed (what this scanner used to
    take). Never raises: a line it cannot parse is skipped like dotenv skips
    it, its raw remainder still reported."""
    out, pos, n = [], 0, len(text)
    while pos < n:
        eol = text.find("\n", pos)
        eol = n if eol < 0 else eol
        line = text[pos:eol].rstrip("\r")
        st = line.strip()
        if not st or st.startswith("#"):
            pos = eol + 1
            continue
        m = _GHC_ENV_KEY_RE.match(text, pos)
        if not m or m.end() > eol + 1 or not m.group(3):
            pos = eol + 1                                # no `=`: no value
            continue
        key = m.group(1) or m.group(2)
        vpos = m.end()
        raw = text[vpos:eol].rstrip("\r").strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        value, nxt = None, eol + 1
        if vpos < n and text[vpos] in "'\"":
            q = _ghc_env_quoted(text, vpos)
            if q:
                body = q[0]
                if text[vpos] == "'":
                    value = re.sub(r"\\([\\'])", lambda e: e.group(1), body)
                else:
                    value = re.sub(r"\\([\\'\"abfnrtv])",
                                   lambda e: _GHC_ENV_DQ_ESC[e.group(1)], body)
                t = _GHC_ENV_TAIL_RE.match(text, q[1])
                if t:
                    nxt = t.end()
                else:                                    # junk after the quote
                    value = None
                    e2 = text.find("\n", q[1])
                    nxt = n if e2 < 0 else e2 + 1
        else:
            part = text[vpos:eol].rstrip("\r")
            # dotenv: re.sub(r"\s+#.*", "", part).rstrip() -- the same cut, found
            # in one pass (that pattern is quadratic on a long run of blanks)
            cm = re.search(r"\s#", part)
            value = (part[:cm.start()] if cm else part).rstrip()
        out.append((key, value, raw))
        pos = max(nxt, pos + 1)
    return out


def _ghc_env_lines(text):
    """[(key, None, raw)]: KEY=value per line, outer quotes removed -- the
    reading this scanner used before E06, kept as the fallback."""
    out = []
    for ln in text.splitlines():
        st = ln.strip()
        if not st or st.startswith("#") or "=" not in st:
            continue
        if st.startswith("export "):
            st = st[7:].lstrip()
        k, _, v = st.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out.append((k.strip(), None, v))
    return out


def _ghc_env_values(path):
    """The secret-keyed values of a .env file as canaries: BOTH the value
    python-dotenv gives Hermes and the raw remainder of the line (E06), so an
    inline comment, quotes, escapes or a multi-line value cannot hide one."""
    vals = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(256 * 1024)
    except OSError:
        return vals
    try:
        pairs = _ghc_dotenv(text)
    except Exception:
        # never fewer canaries than before E06: if the dotenv reading fails,
        # the plain line-by-line reading still names every secret-keyed value
        _ghc_log("the .env parser failed; using the line-by-line reading")
        pairs = _ghc_env_lines(text)
    for key, value, raw in pairs:
        if not GHC_SECRET_KEY_RE.search(key):
            continue
        for v in (value, raw):
            if isinstance(v, str) and v not in vals:
                vals.append(v)
    return vals


def _ghc_file_value(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(8192).strip()
    except OSError:
        return ""


def _ghc_json_secrets(obj, out, depth=0):
    """String values stored under a key that names a token/key/secret/password,
    anywhere in a JSON value (the shape of the stores whose layout Hermes owns)."""
    if depth > 6:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                if GHC_SECRET_KEY_RE.search(str(k)):
                    out.append(v)
            else:
                _ghc_json_secrets(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:200]:
            _ghc_json_secrets(v, out, depth + 1)


def _ghc_known_secrets():
    """The values the canary scan looks for (§3.4): ~/.hermes/.env values of
    keys naming a TOKEN/KEY/SECRET/PASSWORD, serve-token, messages-token,
    google_token.json token/refresh_token, google_client_secret.json's
    client_secret, the secret-keyed values of ~/.hermes/auth.json and
    mcp-tokens/*.json, and this connector's own token.
    In memory only, for the scan; never logged. Values < 12 chars ignored."""
    home = _ghc_home()
    data = _ghc_data()
    vals = _ghc_env_values(os.path.join(home, ".hermes", ".env"))
    vals.append(_ghc_file_value(os.path.join(data, "serve-token")))
    vals.append(_ghc_file_value(os.path.join(data, "messages-token")))
    gt = globals().get("GOOG_TOKEN") or os.path.join(home, ".hermes", "google_token.json")
    g = _ghc_read_json(gt) if isinstance(gt, str) else None
    if isinstance(g, dict):
        for k in ("token", "refresh_token", "access_token"):
            if isinstance(g.get(k), str):
                vals.append(g[k])
    # the OAuth client secret of the Google connector (a pasted
    # client_secret.json: {"installed": {"client_secret": ...}} or {"web": ...})
    gs = globals().get("GOOG_SECRET") or os.path.join(home, ".hermes",
                                                      "google_client_secret.json")
    cs = _ghc_read_json(gs) if isinstance(gs, str) else None
    if isinstance(cs, dict):
        for inner in (cs, cs.get("installed"), cs.get("web")):
            if isinstance(inner, dict) and isinstance(inner.get("client_secret"), str):
                vals.append(inner["client_secret"])
    # Hermes's own credential stores: ~/.hermes/auth.json and mcp-tokens/*.json
    hd = os.path.join(home, ".hermes")
    stores = [os.path.join(hd, "auth.json")]
    try:
        mt = os.path.join(hd, "mcp-tokens")
        stores += [os.path.join(mt, n) for n in sorted(os.listdir(mt))[:20]
                   if n.endswith(".json")]
    except OSError:
        pass
    for sp in stores:
        try:
            if os.path.getsize(sp) <= 256 * 1024:
                _ghc_json_secrets(_ghc_read_json(sp), vals)
        except OSError:
            pass
    tok = _ghc_token()
    if tok:
        vals.append(tok)
    out, seen = [], set()
    for v in vals:
        if isinstance(v, str) and len(v) >= GHC_SECRET_MIN and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _ghc_needles(values):
    """(str_forms, bytes_forms) for the scan, longest first."""
    forms = set()
    for v in values:
        forms.update(_ghc_git.secret_forms(v))
    s = sorted(forms, key=len, reverse=True)
    return s, [f.encode("utf-8") for f in s]


def _ghc_redact(text, str_forms):
    for f in str_forms:
        if f in text:
            text = text.replace(f, "[secret]")
    return text


def _ghc_redact_obj(obj, str_forms):
    """_ghc_redact over every string inside a JSON-shaped value (paths and
    check texts can carry a saved secret too)."""
    if isinstance(obj, str):
        return _ghc_redact(obj, str_forms)
    if isinstance(obj, list):
        return [_ghc_redact_obj(x, str_forms) for x in obj]
    if isinstance(obj, dict):
        return {k: _ghc_redact_obj(v, str_forms) for k, v in obj.items()}
    return obj


def _ghc_scanner():
    """aux_convos' regexes, resolved by name. FAILS CLOSED (the
    _ml_looks_secret rule): if they did not load, nothing is requested."""
    raw = globals().get("_CV_RAW_SECRET_RES")
    lab = globals().get("_CV_SECRET_RE")
    ok = isinstance(raw, (tuple, list)) and raw and all(hasattr(r, "finditer") for r in raw) \
        and hasattr(lab, "finditer")
    if not ok:
        _ghc_log("secret scanner unavailable (aux_convos.py did not load)", once="scanner")
        return None, None
    return raw, lab


def _ghc_value_is_secret(m):
    """The VALUE of a labeled match must itself look like a credential (the
    aux_memlayer value_is_secret rule, copied: hermes_mcp._secret_value and
    aux_memlayer._ml_looks_secret carry the same test)."""
    try:
        v = m.group(2)
        if m.start(2) > m.end(1):
            return True
    except (IndexError, AttributeError):
        return True
    if not v:
        return True
    if any(c.isdigit() for c in v):
        return True
    if any(not (c.isalpha() or c in "_.") for c in v):
        return True
    v = v.rstrip(".")
    return len(v) >= 16 and "." not in v


def _ghc_shape_hits(ln, raw_res, lab_re):
    """Credential-shaped values in one line / one blob (aux_convos' regexes,
    passed in). The PEM rule is left out: _GHC_PEM_RE blocks private keys at the
    byte level, and that regex is QUADRATIC on repeated BEGIN lines (256 KB took
    6 s), which a whole-file binary scan must never hand to it. Long input is
    scanned in windows so no pattern ever sees more than ~16 KB at once. A
    scanner that blows up refuses the whole request: nothing is requested
    unscanned."""
    try:
        rxs = [rx for rx in raw_res if not rx.pattern.startswith("-----BEGIN")]
        step = 16384
        pieces = [ln] if len(ln) <= step + 256 else \
            [ln[i:i + step + 256] for i in range(0, len(ln), step)]
        hits = []
        for piece in pieces:
            for rx in rxs:
                for m in rx.finditer(piece):
                    hits.append(m.group(0))
            for m in lab_re.finditer(piece):
                if _ghc_value_is_secret(m):
                    hits.append(m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(0))
        return list(dict.fromkeys(hits))
    except Exception:
        _ghc_refuse(503, "scanner_failed",
                    "Hermes's secret scanner failed, so nothing was requested.")


def _ghc_mask(s):
    s = str(s)
    return (s[:6] + "…(%d chars)" % len(s)) if len(s) > 6 else "…"


def _ghc_mark_hidden(s):
    return _GHC_HIDDEN_RE.sub(
        lambda m: "".join("⟦U+%04X⟧" % ord(c) for c in m.group(0)), s)


# --------------------------------------------------------------------------
# paths, mirrors, sandboxes
# --------------------------------------------------------------------------
def _ghc_mirror_path(full):
    o, n = full.split("/", 1)
    return os.path.join(_ghc_dir("mirrors"), ("%s__%s.git" % (o, n)).lower())


def _ghc_on_tamper(path, why):
    """gh_git found the mirror's config (or a borrow-from-elsewhere file)
    changed while git ran (E56): hold GitHub writes through the connector
    when it offers a hold, and tell the owner. The git call itself fails."""
    _ghc_log("mirror tampering detected (%s) in %s" % (why, os.path.basename(str(path))))
    what = ("Something on this Mac changed the git settings of Hermes's GitHub mirror (%s) "
            "while Hermes was using it, so Hermes stopped that step."
            % _ghc_line(os.path.basename(str(path)), 80))
    # the hold is lifted only by connecting the key again (the connector's
    # GET-only check cannot test it), so the text the card, the status and
    # Needs-you show must say that, not "pause writes" (they already are)
    held_text = (what + " GitHub writes are paused until you connect your GitHub key again "
                 "in Settings › Connections. If you did not do this, check what runs as "
                 "you first.")
    held = False
    hold = _ghc_fn("_gh_hold")
    if hold is not None:
        try:
            held = hold("mirror_tamper", held_text) is True
        except Exception:
            held = False
    text = held_text if held else (
        what + " If you did not do this, pause GitHub writes in Settings › Connections "
        "and check what runs as you.")
    key = "tamper:%s" % path
    if key not in _GHC_LOG_ONCE:
        _GHC_LOG_ONCE.add(key)
        _ghc_notify(text)


# one hook per loaded copy of this module (a reload replaces the old one)
_ghc_git.TAMPER_HOOKS[:] = [h for h in _ghc_git.TAMPER_HOOKS
                            if getattr(h, "__name__", "") != "_ghc_on_tamper"]
_ghc_git.TAMPER_HOOKS.append(_ghc_on_tamper)


def _ghc_mirror(full):
    return _ghc_git.Mirror(_ghc_mirror_path(full), _ghc_dir("tmp"),
                           secrets=[t for t in [_ghc_token()] if t])


def _ghc_precheck_size(mirror, parent, sandbox, cid, lim):
    """E09: refuse a change whose new or grown files are over the C-size
    limits BEFORE the snapshot writes them into the mirror (a refused
    request used to leave every byte of it there for good)."""
    try:
        ch = mirror.worktree_changes(parent, sandbox,
                                     os.path.join(_ghc_dir("tmp"), "pre-" + cid))
    except _ghc_git.GitError as e:
        _ghc_refuse(500, "snapshot_failed", _ghc_scrub(str(e)))
    files = ch["files"]
    big = [p for p, n in files if n > lim["max_file"]]
    total = sum(n for _p, n in files)
    why = []
    # only what the snapshot would STORE is counted here: a rename is one new
    # file plus one deletion on disk but one entry in the change, so adding
    # the deletions refused a 150-file directory move the C-size check after
    # the snapshot allows. Deletions store nothing; that check still counts them
    if len(files) > lim["max_files"]:
        why.append("It adds or changes %d files; Hermes pushes at most %d." % (
            len(files), lim["max_files"]))
    if total > lim["max_bytes"]:
        why.append("Its new and changed files add up to %.1f MB; Hermes pushes at most %d MB."
                   % (total / 1048576.0, lim["max_bytes"] // 1048576))
    if big:
        why.append("%s %s larger than %d MB." % (
            ", ".join(_ghc_line(p, 100) for p in big[:5]), "is" if len(big) == 1 else "are",
            lim["max_file"] // 1048576))
    if why:
        _ghc_refuse(413, "too_large", " ".join(why) + " Nothing was stored. Remove the files "
                    "(or add them to .gitignore) and request again.", check="C-size",
                    paths=big[:20])


def _ghc_gc(full, prune=False):
    """E09: drop the hold ref of every change of `full` that is no longer
    pending or pushing, then (when one was dropped, or `prune`) delete every
    object no ref reaches — the content of refused, rejected, withdrawn,
    expired, cancelled and failed changes. The keep-set is read while the
    mirror lock is held, the lock a request creates its hold under; a hold
    whose record does not exist yet is kept (a request saves its record just
    after) unless its commit is over an hour old. Never raises."""
    try:
        mirror = _ghc_mirror(full)
        if not mirror.exists():
            return False
        key = str(full).lower()
        with _ghc_git.mirror_lock(mirror.path):
            recs = {r["id"]: r for r in _ghc_all()
                    if str(r.get("repo") or "").lower() == key}
            now = _ghc_now()
            for name, ts in mirror.ref_times(_GHC_HOLD_PREFIX).items():
                r = recs.get(name[len(_GHC_HOLD_PREFIX):])
                if r is not None and r["state"] in ("pending", "pushing"):
                    continue
                if r is None and now - ts < GHC_HOLD_ORPHAN_S:
                    continue
                mirror.delete_ref(name)
                prune = True
            if prune:
                mirror.prune()
        return prune
    except Exception as e:
        _ghc_log("mirror clean-up failed: %s" % type(e).__name__)
        return False


def _ghc_delete_mirror(owner, repo):
    """Delete the dashboard's mirror of owner/repo (repository removed, or
    Disconnect). Its pending changes are cancelled first; a change being
    pushed right now makes it refuse. -> {ok, deleted, error?}."""
    full = _ghc_api_mod.normalize_repo("%s/%s" % (owner, repo)) \
        if isinstance(owner, str) and isinstance(repo, str) else None
    if not full:
        return {"ok": False, "deleted": False, "error": "bad_repo"}
    return _ghc_delete_mirror_path(_ghc_mirror_path(full), full)


def _ghc_delete_mirror_path(path, full=None):
    import shutil as _ghc_shutil
    root = os.path.realpath(_ghc_dir("mirrors"))
    if os.path.islink(path) or os.path.dirname(os.path.abspath(path)) != \
            os.path.abspath(_ghc_dir("mirrors")) or \
            os.path.dirname(os.path.realpath(path)) != root:
        try:
            if os.path.islink(path):
                os.unlink(path)              # a planted link: remove the link only
        except OSError:
            pass
        return {"ok": True, "deleted": False}
    if not os.path.lexists(path):
        return {"ok": True, "deleted": False}
    key = os.path.basename(path)[:-len(".git")]

    def same(r):
        try:
            return _ghc_mirror_path(str(r.get("repo") or "")) == path
        except (ValueError, TypeError):
            return False
    mine = [r for r in _ghc_all() if same(r)]
    if any(r["state"] == "pushing" for r in mine):
        return {"ok": False, "deleted": False, "error": "pushing"}
    for r in mine:
        if r["state"] == "pending":
            _ghc_cancel_repo(r["repo"], "its mirror was deleted")
            break
    with _ghc_git.mirror_lock(os.path.abspath(path)):
        try:
            _ghc_shutil.rmtree(path)
        except OSError as e:
            _ghc_log("could not delete a mirror: %s" % type(e).__name__)
            return {"ok": False, "deleted": False, "error": "delete_failed"}
    _ghc_record("mirror_delete", full or key, "deleted the local mirror of %s" % (full or key))
    return {"ok": True, "deleted": True}


def _ghc_delete_all_mirrors():
    """Every mirror under github/mirrors/ (Disconnect). -> {ok, deleted: n,
    failed: n}."""
    d = _ghc_dir("mirrors")
    out = {"ok": True, "deleted": 0, "failed": 0}
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for n in names:
        if not n.endswith(".git"):
            continue
        r = _ghc_delete_mirror_path(os.path.join(d, n))
        if r.get("deleted"):
            out["deleted"] += 1
        elif not r.get("ok"):
            out["failed"] += 1
            out["ok"] = False
    return out


def _ghc_sandbox_root():
    fn = _ghc_fn("_gh_sandbox_root")
    root = fn() if fn else os.path.join(_ghc_home(), "HermesSandbox", "github")
    return root


def _ghc_sandbox(full):
    """The sandbox path for a repository, or a refusal when anything along it
    is a symlink (a planted link would aim the clone or the snapshot at
    some other folder)."""
    fn = _ghc_fn("_gh_sandbox_path")
    path = fn(full) if fn else None
    if not path:
        o, n = full.split("/", 1)
        path = os.path.join(_ghc_sandbox_root(), o, n)
    root = _ghc_sandbox_root()
    top = os.path.dirname(root)                          # ~/HermesSandbox
    rel = os.path.relpath(path, top)
    if rel.startswith("..") or os.path.isabs(rel):
        _ghc_refuse(500, "bad_sandbox_path")
    cur = top
    for part in [None] + rel.split(os.sep):
        if part:
            cur = os.path.join(cur, part)
        if os.path.islink(cur):
            _ghc_refuse(409, "sandbox_symlink",
                        "%s is a symbolic link, so Hermes will not use it as a sandbox." % cur)
    return path


def _ghc_ensure_sandbox_parent(path):
    """~/HermesSandbox (0700) / github / <owner> — created 0700 when missing;
    an existing folder's permissions are the owner's and are not changed."""
    top = os.path.dirname(_ghc_sandbox_root())
    for d in (top, _ghc_sandbox_root(), os.path.dirname(path)):
        if not os.path.isdir(d):
            os.makedirs(d, mode=0o700, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass


def _ghc_repo_row(repo):
    """(full_name, row) for an allowlisted repository, else a refusal."""
    full = _ghc_api_mod.normalize_repo(repo) if isinstance(repo, str) else None
    if not full:
        _ghc_refuse(400, "bad_repo", "Write the repository as owner/repo.")
    if not _ghc_call("_gh_connected"):
        _ghc_refuse(400, "not_connected", "GitHub is not connected.")
    row = _ghc_call("_gh_repo", full)
    if not row:
        _ghc_refuse(403, "not_enabled", "%s is not connected to Hermes." % full)
    if not row.get("enabled"):
        _ghc_refuse(403, "not_enabled", _ghc_line(row.get("enable_blocked") or (
            "%s is not enabled for Hermes." % row["full_name"]), 600), check="C-scope")
    full = row["full_name"]
    default = row.get("default_branch")
    if not isinstance(default, str) or not _ghc_git.BRANCH_RE.fullmatch(default) or \
            ".." in default:
        _ghc_refuse(409, "bad_default_branch",
                    "Hermes cannot work with this repository's default branch name.")
    if default == "hermes" or default.startswith("hermes/"):
        _ghc_refuse(409, "bad_default_branch",
                    "The default branch is called hermes (or starts with hermes/), which "
                    "collides with the hermes/ branches Hermes pushes, so Hermes will not "
                    "work with this repository.")
    return full, row


def _ghc_api():
    api = _ghc_call("_gh_api")
    if api is None:
        _ghc_refuse(400, "not_connected", "GitHub is not connected.")
    return api


def _ghc_remote_head(api, full, branch):
    """GitHub's truth for one branch: (sha | None-for-absent). A status Hermes
    cannot read raises (nothing is decided on a guess)."""
    r = api.get(_ghc_api_mod._rp(full) + "/git/ref/heads/" + _ghc_api_mod._refq(branch))
    if r.status == 404 and not r.error:
        return None
    if r.status == 200 and isinstance(r.data, dict):
        obj = r.data.get("object") or {}
        sha = str(obj.get("sha") or "")
        if r.data.get("ref") == "refs/heads/" + branch and GHC_SHA_RE.fullmatch(sha):
            return sha
    _ghc_refuse(503, "github_unreadable",
                "Hermes could not read %s on GitHub (status %s), so nothing was done."
                % (_ghc_line(branch, 80), r.status), retry=True)


def _ghc_url(full):
    try:
        return _ghc_git.remote_url(_ghc_call("_gh_git_base"), full)
    except _ghc_git.GitError as e:
        _ghc_refuse(503, "bad_git_base", _ghc_scrub(str(e)))


def _ghc_sync_heads(mirror, default):
    """Publish remote truth into the mirror's refs/heads/* so the sandbox can
    `git pull` from it. These refs are a convenience for the agent and are
    NEVER read back as a parent."""
    rem = mirror.refs("refs/hermes-remote/")
    d = rem.get("refs/hermes-remote/" + default)
    if d:
        mirror.update_ref("refs/heads/" + default, d)
    want = {}
    for name, sha in rem.items():
        if name.startswith("refs/hermes-remote/hermes/"):
            want["refs/heads/" + name[len("refs/hermes-remote/"):]] = sha
    for name in mirror.refs("refs/heads/hermes/"):
        if name not in want:
            mirror.delete_ref(name)
    for name, sha in want.items():
        mirror.update_ref(name, sha)
    return d


def _ghc_fetch(full, row, mirror):
    """Fetch the default branch and every hermes/* head into the mirror's
    refs/hermes-remote/* (the token only via _gh_git_env, for this one call).
    -> the fetched default-branch sha."""
    default = row["default_branch"]
    url = _ghc_url(full)
    mirror.ensure()
    mirror.set_head(default)
    try:
        mirror.fetch(url, ["+refs/heads/%s:refs/hermes-remote/%s" % (default, default),
                           "+refs/heads/hermes/*:refs/hermes-remote/hermes/*"],
                     auth_env=_ghc_call("_gh_git_env") or None)
    except _ghc_git.GitError as e:
        msg = _ghc_scrub(str(e))
        try:
            _ghc_call("_gh_repo_update", full, fetch_error=msg, mirror_ok=False)
        except Exception:
            pass
        _ghc_refuse(502, "fetch_failed", "Hermes could not fetch %s from GitHub." % full,
                    detail=msg, retry=True)
    head = _ghc_sync_heads(mirror, default)
    now = _ghc_now()
    try:
        _ghc_call("_gh_repo_update", full, last_fetch=now, fetch_error=None, mirror_ok=True,
                  head_sha=head)
    except Exception:
        pass
    return head


# --------------------------------------------------------------------------
# POST /api/github/sandbox/prepare | refresh  (agent-callable)
# --------------------------------------------------------------------------
def _ghc_attempt(full, now=None):
    """At most limits["reads_per_10min"] (30) GitHub-reading calls per
    repository per ten minutes, whatever they answer."""
    now = _ghc_now() if now is None else now
    cap = int(_ghc_limits().get("reads_per_10min") or 30)
    with _GHC_LOCK:
        key = full.lower()
        ts = [t for t in _GHC_ATTEMPTS.get(key, []) if now - t < GHC_REQ_WINDOW]
        if len(ts) >= cap:
            _GHC_ATTEMPTS[key] = ts
            _ghc_refuse(429, "too_many_reads", "Hermes read %s from GitHub %d times in the last "
                        "ten minutes. Wait a few minutes." % (full, len(ts)), check="C-rate")
        ts.append(now)
        _GHC_ATTEMPTS[key] = ts


def _ghc_prepare(ctx, create):
    b = _ghc_body(ctx)
    full, row = _ghc_repo_row(b.get("repo"))
    lim = _ghc_limits()
    size = row.get("size_kb")
    if isinstance(size, int) and not isinstance(size, bool) and size > lim["max_repo_kb"]:
        _ghc_refuse(413, "too_big", "%s is larger than %d GB, so Hermes will not mirror it."
                    % (full, lim["max_repo_kb"] // (1024 * 1024)))
    if not _ghc_git.available():
        _ghc_refuse(503, "git_unavailable",
                    "git is not installed (install the Command Line Tools: xcode-select "
                    "--install).")
    sandbox = _ghc_sandbox(full)
    mirror = _ghc_mirror(full)
    if not create and not mirror.exists():
        _ghc_refuse(409, "not_prepared", "Prepare the sandbox first.",
                    route="/api/github/sandbox/prepare")
    _ghc_attempt(full)
    with _ghc_git.mirror_lock(mirror.path):
        head = _ghc_fetch(full, row, mirror)
        created = False
        if create and not os.path.lexists(sandbox):
            _ghc_ensure_sandbox_parent(sandbox)
            _ghc_sandbox(full)                         # re-check after mkdir
            try:
                _ghc_git.clone_sandbox(mirror.path, sandbox, row["default_branch"],
                                       _ghc_dir("tmp"))
            except _ghc_git.GitError as e:
                _ghc_refuse(500, "clone_failed", _ghc_scrub(str(e)))
            created = True
            try:
                _ghc_call("_gh_repo_update", full, prepared_at=_ghc_now())
            except Exception:
                pass
    prepared = os.path.isdir(os.path.join(sandbox, ".git"))
    _ghc_record("prepare" if create else "refresh", full,
                "%s %s (%s @ %s)" % ("prepared" if create else "refreshed", full,
                                     row["default_branch"], (head or "")[:12]),
                args={"repo": full, "created": created})
    return {"ok": True, "repo": full, "sandbox": sandbox, "created": created,
            "prepared": prepared, "default_branch": row["default_branch"], "head": head,
            "fetched_at": _ghc_now(),
            "hint": ("Edit files in %s with your normal tools. `git pull` there picks up the "
                     "latest from GitHub after a refresh. When the change is ready and its "
                     "tests pass, request it (POST /api/github/changes) and stop: publishing "
                     "is the owner's decision." % sandbox)}


@_ghc_safe
def _ghc_prepare_handler(ctx):
    return _ghc_prepare(ctx, True)


@_ghc_safe
def _ghc_refresh_handler(ctx):
    return _ghc_prepare(ctx, False)


# --------------------------------------------------------------------------
# the §3.4 checks
# --------------------------------------------------------------------------
def _ghc_check(checks, cid, level, text, ack=False, **kw):
    c = {"id": cid, "level": level, "text": text}
    if level == "warn":
        n = sum(1 for x in checks if x["id"].split(":")[0] == cid)
        c["ack"] = cid if n == 0 else "%s:%d" % (cid, n + 1)
    c.update(kw)
    checks.append(c)
    return c


def _ghc_text_lines(data):
    """The lines of a blob. Bytes that are not UTF-8 become U+FFFD instead of
    exempting the whole file: a single stray 0xFF byte must not switch off the
    credential and hidden-character scans for everything else in it."""
    return data.decode("utf-8", "replace").split("\n")


def _ghc_textish(data):
    """True when a file git calls binary is really text (E02): git's test is
    one NUL byte in the first 8,000, which one byte in a comment satisfies.
    The rule: drop every NUL byte, decode the rest as UTF-8 (undecodable bytes
    become U+FFFD); the file is text-like when something is left and at most
    GHC_TEXTISH_BAD of it is U+FFFD or a control character other than tab,
    newline, carriage return, form feed and vertical tab. So a shell script
    with NULs, or UTF-16 text, is text-like; a PNG or random bytes are not."""
    if not data:
        return False
    t = bytes(data).replace(b"\x00", b"").decode("utf-8", "replace")
    if not t:
        return False
    bad = 0
    for c in t:
        o = ord(c)
        if c == "�" or (o < 32 and c not in "\t\n\r\x0b\x0c") or 0x7f <= o <= 0x9f:
            bad += 1
    return bad <= GHC_TEXTISH_BAD * len(t)


def _ghc_long_lines(added):
    """[(line_no, why)] of the added lines C-longline reports (E02)."""
    out = []
    for ln_no, ln in added:
        s = ln[:-1] if ln.endswith("\r") else ln
        if len(s) > GHC_LONGLINE:
            out.append((ln_no, "is %d characters long" % len(s)))
            continue
        for m in _GHC_BLANK_RUN_RE.finditer(s):
            if m.end() >= len(s):
                break                                    # trailing blanks hide nothing
            n, cols = _ghc_blank_width(m.group(0))
            if n >= GHC_WS_RUN or cols >= GHC_WS_COLS:
                out.append((ln_no, "has a run of %d spaces, tabs or other blank characters "
                                   "(about %d columns) before more text" % (n, cols)))
                break
    return out


def _ghc_blank_width(run):
    """(characters, screen columns) of the blank characters in a run that take
    up room: a tab counts as 8 columns (what a browser shows), a wide blank
    as 2, a zero-width or hidden character as nothing."""
    n = cols = 0
    for c in run:
        if c == "\t":
            n, cols = n + 1, cols + 8
        elif c in _GHC_BLANK_WIDE:
            n, cols = n + 1, cols + 2
        elif c.isspace():
            n, cols = n + 1, cols + 1
    return n, cols


def _ghc_added_lines(new, old):
    """Lines of `new` not in `old` (a multiset difference — the added lines a
    diff would show, without parsing the (possibly truncated) patch). Lines
    split on \\n only, as git counts them."""
    nl = _ghc_text_lines(new)
    ol = _ghc_text_lines(old) if old else []
    have = {}
    for ln in ol:
        have[ln] = have.get(ln, 0) + 1
    out = []
    for i, ln in enumerate(nl):
        if have.get(ln):
            have[ln] -= 1
        else:
            out.append((i + 1, ln))
    return out


def _ghc_workflows(mirror, parent):
    """({path: text} | None) for the PARENT tree's .github/workflows/*.y(a)ml,
    read from the mirror's object store (GitHub runs these on a push of the
    new commit, since C-workflow refuses any change to them)."""
    ents = mirror.ls_tree(parent, ".github/workflows")
    files = {}
    wf = [e for e in ents if e[1] == "blob" and re.search(r"\.ya?ml$", e[3], re.I)]
    if len(wf) > GHC_WORKFLOW_CAP:
        return None
    for mode, typ, sha, name in wf:
        data, cut = mirror.blob(sha, GHC_WORKFLOW_MAX)
        if cut:
            return None
        files[name] = data.decode("utf-8", "replace")
    return files


def _ghc_compute_checks(mirror, full, row, parent, tree, entries, numstat, title, body,
                        slug, patch_len, patch_cut, raw=("", "")):
    """-> (checks, files, stat, str_forms). Every rule of §3.4 that looks at
    content. Raises _GhcRefuse when the secret scanner is unavailable."""
    lim = _ghc_limits()
    checks = []
    raw_res, lab_re = _ghc_scanner()
    if raw_res is None:
        _ghc_refuse(503, "scanner_unavailable",
                    "Hermes's secret scanner did not load, so it refuses to request any "
                    "change. Restart the dashboard.")
    str_forms, byte_forms = _ghc_needles(_ghc_known_secrets())

    files, total, big, gitlinks, paths = [], 0, [], [], []
    sizes = mirror.sizes([e["new_sha"] for e in entries if e["new_sha"] != GHC_ZERO
                          and e["new_mode"] not in ("160000",)])
    for e in entries:
        paths.append(e["path"])
        if e.get("old_path"):
            paths.append(e["old_path"])
        add, dele = numstat.get(e["path"], (None, None))
        size = sizes.get(e["new_sha"], 0) if e["status"] != "D" else 0
        # binary = git said "-  -" for THIS path; a path numstat did not list at
        # all is scanned as text (never exempted from the content scans)
        f = {"path": e["path"], "old_path": e.get("old_path"), "status": e["status"],
             "old_mode": e["old_mode"], "new_mode": e["new_mode"], "add": add, "del": dele,
             "binary": e["path"] in numstat and add is None and dele is None
             and e["status"] != "D" and e["new_mode"] not in ("120000", "160000"),
             "size": size}
        files.append(f)
        total += size
        if size > lim["max_file"]:
            big.append(e["path"])
        if e["new_mode"] == "160000" or (e["old_mode"] == "160000" and e["status"] != "D"):
            gitlinks.append(e["path"])

    # C-workflow
    wf = _ghc_api_mod.touches_workflows(paths)
    if wf:
        _ghc_check(checks, "C-workflow", "block",
                   "It changes %s. Hermes's GitHub key has no Workflows permission and Hermes "
                   "will not ask for it, so changes under .github/workflows are refused."
                   % ", ".join(_ghc_line(p, 100) for p in wf[:5]), paths=wf[:20])
    # gitlinks (nested repositories) are refused in v1
    if gitlinks:
        _ghc_check(checks, "C-gitlink", "block",
                   "It contains a nested git repository (%s). Hermes does not push "
                   "submodules or nested repositories." % ", ".join(
                       _ghc_line(p, 100) for p in gitlinks[:5]), paths=gitlinks[:20])
    # C-size
    if len(entries) > lim["max_files"]:
        _ghc_check(checks, "C-size", "block", "It changes %d files; Hermes pushes at most %d."
                   % (len(entries), lim["max_files"]))
    if total > lim["max_bytes"]:
        _ghc_check(checks, "C-size", "block", "Its files add up to %.1f MB; Hermes pushes at "
                   "most %d MB." % (total / 1048576.0, lim["max_bytes"] // 1048576))
    if big:
        _ghc_check(checks, "C-size", "block", "%s is larger than %d MB." % (
            ", ".join(_ghc_line(p, 100) for p in big[:5]), lim["max_file"] // 1048576),
            paths=big[:20])
    if patch_cut or patch_len > lim["max_diff"]:
        _ghc_check(checks, "C-size", "block", "The diff is larger than %d MB, too large to "
                   "review here." % (lim["max_diff"] // 1048576))
    scan_content = total <= lim["max_bytes"] and len(entries) <= lim["max_files"]

    # known secrets in the commit text, the branch name and the file names
    # (the agent's RAW text is scanned too: the cleaned text has mentions wrapped
    # and hidden characters dropped or made visible, which can split a value)
    meta_hits = list(dict.fromkeys(
        what for what, txt in (("the title", title), ("the description", body),
                               ("the title", raw[0]), ("the description", raw[1]),
                               ("the branch name", slug), ("a file name", "\n".join(paths)))
        if any(f in txt for f in str_forms)))
    if meta_hits:
        _ghc_check(checks, "C-secret-known", "block",
                   "%s contains one of your saved secret values. This cannot be overridden."
                   % (meta_hits[0][:1].upper() + meta_hits[0][1:]), where=meta_hits)
    shape_n, shape_over = [0], [0]
    hidden_files, hidden_total, binaries = [], [0], []
    known_files, pem_files = [], []
    long_files, long_over, preview_left = [], [0], [lim["max_diff"]]
    # a credential-shaped value in the title or description would be published
    # in the clear (a public repository) the moment the change is approved
    for what, txt in (("The title", title), ("The description", body)):
        for ln in txt.split("\n"):
            for h in _ghc_shape_hits(ln, raw_res, lab_re)[:2]:
                if shape_n[0] >= GHC_MAX_FINDINGS:
                    shape_over[0] += 1
                    continue
                shape_n[0] += 1
                _ghc_check(checks, "C-secret-shape", "warn",
                           "%s has something shaped like a credential (%s). Tick only if you "
                           "are sure it is not a real secret." % (what, _ghc_mask(h)))
    if scan_content:
        for e, f in zip(entries, files):
            if e["status"] == "D" or e["new_mode"] == "160000":
                continue
            new, _cut = mirror.blob(e["new_sha"], lim["max_file"] + 1)
            old = b""
            if e["old_sha"] != GHC_ZERO and e["old_mode"] != "160000":
                old, _c = mirror.blob(e["old_sha"], lim["max_file"] + 1)
            # C-secret-known: a canary form appears more often than before (raw,
            # url-encoded, hex, base64/base64url at any alignment), in text or binary
            for nb in byte_forms:
                if new.count(nb) > old.count(nb):
                    known_files.append(e["path"])
                    break
            # private-key blocks are a hard block too
            if len(_GHC_PEM_RE.findall(new)) > len(_GHC_PEM_RE.findall(old)):
                pem_files.append(e["path"])
            if e["new_mode"] == "120000":
                target = new.decode("utf-8", "replace")
                norm = os.path.normpath(os.path.join(os.path.dirname(e["path"]), target))
                leaves = target.startswith("/") or norm == ".." or norm.startswith("../")
                _ghc_check(checks, "C-symlink", "warn" if leaves else "info",
                           ("%s is a symbolic link to %s, outside the repository."
                            if leaves else "%s is a symbolic link to %s.")
                           % (_ghc_line(e["path"], 100), _ghc_line(target, 100)),
                           path=e["path"])
                continue
            textish = False
            if f["binary"]:
                binaries.append(f)
                f["sha256"] = hashlib.sha256(new).hexdigest()
                # E02: git calls a file with one NUL byte binary, so its content is
                # not in the diff. A file that is really text still gets every
                # line scan below and a --text preview for the review sheet; a
                # real binary gets the whole-blob credential-shape scan only
                textish = f["textish"] = _ghc_textish(new)
                # a file that was TEXT before and is binary now is the E02 trick
                # itself (git's test: a NUL byte in the first 8,000): say so
                f["was_text"] = bool(old) and b"\x00" not in old[:8000]
                if textish:
                    if preview_left[0] > 0:
                        try:
                            pv, pcut = mirror.patch_text(parent, tree, e["path"],
                                                         min(lim["max_diff"], preview_left[0]))
                        except _ghc_git.GitError:
                            pv, pcut = "", True
                        preview_left[0] -= len(pv)
                        f["text_preview"], f["text_preview_truncated"] = pv, bool(pcut)
                    else:
                        f["text_preview"], f["text_preview_truncated"] = None, True
                if not textish:
                    if new != old:
                        had = set(_ghc_shape_hits(old.decode("latin-1"), raw_res, lab_re)) \
                            if old else set()
                        for h in [x for x in _ghc_shape_hits(new.decode("latin-1"), raw_res,
                                                             lab_re) if x not in had][:2]:
                            if shape_n[0] >= GHC_MAX_FINDINGS:
                                shape_over[0] += 1
                                continue
                            shape_n[0] += 1
                            _ghc_check(checks, "C-secret-shape", "warn",
                                       "%s (a binary file) has something shaped like a "
                                       "credential (%s). Tick only if you are sure it is not "
                                       "a real secret." % (_ghc_line(e["path"], 100),
                                                           _ghc_mask(h)),
                                       path=e["path"])
                    continue
            added = _ghc_added_lines(new, old) or []
            for ln_no, ln in added:
                if _GHC_HIDDEN_RE.search(ln):
                    hidden_total[0] += 1
                    if len(hidden_files) < GHC_MAX_FINDINGS:
                        hidden_files.append({"path": e["path"], "line": ln_no,
                                             "sample": _ghc_mark_hidden(ln[:160])})
                for h in _ghc_shape_hits(ln, raw_res, lab_re)[:3]:
                    if shape_n[0] >= GHC_MAX_FINDINGS:
                        shape_over[0] += 1
                        continue
                    shape_n[0] += 1
                    _ghc_check(checks, "C-secret-shape", "warn",
                               "%s%s line %d has something shaped like a credential (%s). "
                               "Tick only if you are sure it is not a real secret."
                               % (_ghc_line(e["path"], 100), " (a binary file)" if textish
                                  else "", ln_no, _ghc_mask(h)),
                               path=e["path"], line=ln_no)
            longs = _ghc_long_lines(added)
            if longs:
                if len(long_files) < GHC_MAX_FINDINGS:
                    long_files.append((e["path"], longs))
                else:
                    long_over[0] += 1
    else:
        _ghc_check(checks, "C-scan", "block", "The change is too large to scan for secrets.")
    if known_files:
        _ghc_check(checks, "C-secret-known", "block",
                   "%s contains one of your saved secret values (from ~/.hermes/.env, the "
                   "dashboard's own keys or this GitHub key). This cannot be overridden."
                   % ", ".join(_ghc_line(p, 100) for p in known_files[:5]),
                   paths=known_files[:20])
    if pem_files:
        _ghc_check(checks, "C-secret-pem", "block",
                   "%s contains a private key block (-----BEGIN … PRIVATE KEY-----)."
                   % ", ".join(_ghc_line(p, 100) for p in pem_files[:5]),
                   paths=pem_files[:20])
    if shape_over[0]:
        _ghc_check(checks, "C-secret-shape", "warn",
                   "%d more credential-shaped values were found than the %d listed above. "
                   "Read the whole diff before ticking." % (shape_over[0], GHC_MAX_FINDINGS),
                   more=shape_over[0])
    if hidden_files:
        _ghc_check(checks, "C-unicode", "warn",
                   "%d added line%s contain hidden or direction-changing characters, which "
                   "can make code read differently from what it does. They are shown as "
                   "⟦U+…⟧ in the samples." % (hidden_total[0], "" if hidden_total[0] == 1
                                              else "s"),
                   samples=hidden_files, total=hidden_total[0])
    odd_names = [p for p in dict.fromkeys(paths) if _GHC_HIDDEN_RE.search(p)]
    if odd_names:
        _ghc_check(checks, "C-unicode", "warn",
                   "%d file name%s contain hidden or direction-changing characters (shown as "
                   "⟦U+…⟧): a name can read as a different file type or path than it is."
                   % (len(odd_names), "" if len(odd_names) == 1 else "s"),
                   samples=[{"path": _ghc_mark_hidden(p[:160]), "line": 0,
                             "sample": _ghc_mark_hidden(p[:160])} for p in odd_names[:GHC_MAX_FINDINGS]],
                   total=len(odd_names))
    # E02: content the owner cannot read in the diff needs its own tick
    for f in binaries[:GHC_MAX_FINDINGS]:
        if f.get("textish"):
            txt = ("%s is treated as a binary file by git (%d bytes, sha256 %s), so its "
                   "content is not in the diff. It is mostly text"
                   % (_ghc_line(f["path"], 100), f["size"], f.get("sha256", "")[:16]))
            # the tick must say what was really shown: a preview that was cut
            # (or never made) leaves content nobody read
            if not f.get("text_preview"):
                txt += (", but Hermes could not make a text preview of it here (this change "
                        "has more to preview than fits). NONE of it is shown: tick only "
                        "if you know what this file is.")
            elif f.get("text_preview_truncated"):
                txt += (", but too large to show in full: its text preview stops part-way "
                        "and the rest is NOT shown. Tick only if you know what the whole "
                        "file is.")
            else:
                txt += ": read its text preview before ticking."
        else:
            txt = ("%s is a binary file (%d bytes, sha256 %s).%s Its content is not in the "
                   "diff and cannot be shown here: tick only if you know what this file is."
                   % (_ghc_line(f["path"], 100), f["size"], f.get("sha256", "")[:16],
                      " It was a TEXT file before this change." if f.get("was_text") else ""))
        _ghc_check(checks, "C-binary", "warn", txt, path=f["path"], size=f["size"],
                   sha256=f.get("sha256"), preview=bool(f.get("text_preview")),
                   preview_truncated=bool(f.get("textish") and (
                       f.get("text_preview_truncated") or not f.get("text_preview"))))
    if len(binaries) > GHC_MAX_FINDINGS:
        extra = len(binaries) - GHC_MAX_FINDINGS
        _ghc_check(checks, "C-binary", "warn",
                   "%d more binary file%s than the %d listed above. None of their content is "
                   "in the diff." % (extra, "" if extra == 1 else "s", GHC_MAX_FINDINGS),
                   more=extra)
    for path, longs in long_files:
        ln_no, why = longs[0]
        _ghc_check(checks, "C-longline", "warn",
                   "%s line %d %s%s. The review may not show all of it on screen: read it in "
                   "full before ticking." % (
                       _ghc_line(path, 100), ln_no, why,
                       (" (and %d more such line%s in this file)" % (
                           len(longs) - 1, "" if len(longs) == 2 else "s"))
                       if len(longs) > 1 else ""),
                   path=path, line=ln_no, lines=[x[0] for x in longs[:GHC_MAX_FINDINGS]],
                   total=len(longs))
    if long_over[0]:
        _ghc_check(checks, "C-longline", "warn",
                   "%d more file%s than the %d listed above have very long lines or long runs "
                   "of spaces." % (long_over[0], "" if long_over[0] == 1 else "s",
                                   GHC_MAX_FINDINGS), more=long_over[0])
    for e in entries:
        if e["new_mode"] == "100755" and e["old_mode"] != "100755" and e["status"] != "D":
            _ghc_check(checks, "C-mode", "warn", "%s becomes executable."
                       % _ghc_line(e["path"], 100), path=e["path"])

    # C-ci: the PARENT tree's workflows (a labelled text heuristic)
    wfiles = _ghc_workflows(mirror, parent)
    if wfiles is None:
        _ghc_check(checks, "C-ci", "block",
                   "Hermes could not read this repository's workflows, so it cannot tell "
                   "whether pushing would run CI with your secrets.")
    else:
        ci = _ghc_api_mod.ci_exposure({".github/workflows/" + k: v for k, v in wfiles.items()})
        names = []
        for w in ci["workflows"]:
            for n in w.get("secrets") or []:
                if n not in names:
                    names.append(n)
        if ci["exposed"]:
            push_w = [w["path"] for w in ci["workflows"] if w["push_exposed"]]
            _ghc_check(checks, "C-ci-push", "block",
                       "Pushing a hermes/ branch runs %s with secrets (%s) on GitHub, so Hermes "
                       "will not push to this repository. %s" % (
                           ", ".join(push_w[:4]), ", ".join(names[:6]) or "secrets",
                           _ghc_api_mod.CI_FIX.format(paths=", ".join(push_w[:4]))))
        elif ci["pr_exposed"]:
            pr_w = [w for w in ci["workflows"] if w["pr_exposed"]]
            texts = "\n".join(wfiles.get(w["path"].rsplit("/", 1)[-1], "") for w in pr_w)
            runs = [f["path"] for f in files if f["status"] != "D" and (
                _GHC_CI_RUN_RE.search(f["path"]) or f["path"] in texts)]
            _ghc_check(checks, "C-ci", "warn",
                       "Opening a pull request runs %s with secrets (%s) on GitHub (heuristic)."
                       "%s" % (", ".join(w["path"] for w in pr_w[:4]),
                               ", ".join(names[:6]) or "secrets",
                               (" This change edits files it runs: %s." % ", ".join(
                                   _ghc_line(p, 80) for p in runs[:6])) if runs else ""),
                       workflows=[w["path"] for w in pr_w[:10]], files=runs[:20])
    # C-public
    if row.get("private") is not True:
        _ghc_check(checks, "C-public", "confirm",
                   "I understand this is public. It is visible to everyone the moment it is "
                   "pushed.")
    stat = {"files": len(entries), "add": sum(f["add"] or 0 for f in files),
            "del": sum(f["del"] or 0 for f in files), "bytes": total}
    return checks, files, stat, str_forms


def _ghc_digest_ok(rec):
    """E134: the stored digest still describes the stored record (a record
    edited on disk after the card was built is stale, whatever it says)."""
    try:
        return _ghc_digest(rec) == rec.get("digest")
    except (KeyError, TypeError, ValueError):
        return False


def _ghc_digest(rec):
    """Binds a decision to this exact content (the B01 stale-card lesson)."""
    blob = json.dumps([str(rec["repo"]).lower(), rec["target_ref"], rec["parent"],
                       rec["tree"], rec["title"], rec["body"], sorted(rec["needs_ack"]),
                       bool(rec["confirm_public"])], separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# POST /api/github/changes  (agent-callable: snapshot + checks -> pending)
# --------------------------------------------------------------------------
@_ghc_safe
def _ghc_request_handler(ctx):
    b = _ghc_body(ctx)
    slug, title, body, mode = b.get("slug"), b.get("title"), b.get("body", ""), b.get("mode")
    if not isinstance(slug, str) or not GHC_SLUG_RE.fullmatch(slug):
        _ghc_refuse(400, "bad_slug", "The branch name is hermes/<slug>; the slug is 2-49 "
                    "lowercase letters, digits or hyphens, starting with a letter or digit.")
    if not isinstance(title, str) or not title.strip() or len(title) > GHC_TITLE_IN_MAX:
        _ghc_refuse(400, "bad_title", "Give the change a one-line title.")
    if body is None:
        body = ""
    if not isinstance(body, str) or len(body) > GHC_BODY_IN_MAX:
        _ghc_refuse(400, "bad_body", "The description must be text (at most 64 KB).")
    if mode not in (None, "new", "append"):
        _ghc_refuse(400, "bad_mode", "mode is new or append (or leave it out).")
    src = b.get("source") if b.get("source") in GHC_SOURCES else "api"
    session = re.sub(r"[^A-Za-z0-9._:-]", "", str(b.get("session") or ""))[:80]
    _ghc_require_recorder()
    full, row = _ghc_repo_row(b.get("repo"))
    ok, why = _ghc_call("_gh_writes_allowed", full)
    if not ok:
        _ghc_refuse(409, "writes_blocked", _ghc_line(why, 400), check="C-scope")
    tier, verdict = _ghc_trust(full)
    if tier == "never":
        _ghc_refuse(403, "trust_never", "Your Trust settings say Never for GitHub pushes to "
                    "%s." % full, check="C-scope")
    default = row["default_branch"]
    branch = "hermes/" + slug
    ref = _ghc_git.branch_ref(slug)
    if not _ghc_git.check_ref(ref) or ref == "refs/heads/" + default or \
            slug in GHC_RESERVED_SLUGS or slug == default.lower():
        # hermes/main is harmless to GitHub, but a branch that reads like the
        # default branch on a PR card or in a notice is a confusion to avoid
        _ghc_refuse(400, "bad_slug", "That branch name is not allowed.", check="C-branch")
    if not _ghc_git.available():
        _ghc_refuse(503, "git_unavailable", "git is not installed.")
    lim = _ghc_limits()
    now = _ghc_now()
    _ghc_rate_check(full, branch, now, lim)
    budget = _ghc_call("_gh_push_budget")
    if int(budget.get("left") or 0) <= 0:
        _ghc_refuse(429, "push_budget", "Hermes has used today's %s pushes. Try again "
                    "tomorrow." % budget.get("limit"), check="C-rate")
    sandbox = _ghc_sandbox(full)
    mirror = _ghc_mirror(full)
    if not os.path.isdir(os.path.join(sandbox, ".git")) or not mirror.exists():
        _ghc_refuse(409, "not_prepared", "Prepare the sandbox first.",
                    route="/api/github/sandbox/prepare")
    _ghc_gc(full)                  # E09: release what expired / was decided since
    ident = _ghc_call("_gh_identity")
    if not ident:
        _ghc_refuse(400, "not_connected", "GitHub is not connected.")
    api = _ghc_api()
    _ghc_attempt(full)

    _ghc_reconcile(full)           # E57: an interrupted push must not skew the ledger
    # 1. remote truth: the parent is the sha GitHub reports — never a local ref
    ledger = _ghc_call("_gh_ledger_heads", full)
    default_head = _ghc_remote_head(api, full, default)
    if default_head is None:
        _ghc_refuse(409, "empty_repo", "%s has no %s branch on GitHub." % (full, default))
    branch_head = _ghc_remote_head(api, full, branch)
    lh = ledger.get(branch, Ellipsis)
    if mode is None:
        mode = "append" if (branch_head and lh not in (Ellipsis, None) and lh == branch_head) \
            else "new"
    if mode == "new":
        if branch_head is not None:
            _ghc_refuse(409, "branch_exists", "%s already exists on GitHub. Choose another "
                        "name%s." % (branch, " or add to it with mode append" if lh == branch_head
                                     else ""), check="C-branch")
        parent, target = default_head, default
    else:
        if branch_head is None:
            _ghc_refuse(409, "no_branch", "%s does not exist on GitHub, so there is nothing to "
                        "add to." % branch, check="C-branch")
        if lh in (Ellipsis, None) or lh != branch_head:
            _ghc_refuse(409, "branch_changed", "%s changed on GitHub since Hermes last pushed "
                        "it, so Hermes will not add to it." % branch, check="C-branch")
        parent, target = branch_head, branch

    cid = "ghc-" + _ghc_secrets.token_hex(6)
    title_c, body_c, notes = _ghc_clean_text(title, body)
    if not title_c:
        _ghc_refuse(400, "bad_title", "Give the change a one-line title.")
    with _ghc_git.mirror_lock(mirror.path):
        _ghc_fetch(full, row, mirror)
        fetched = mirror.rev("refs/hermes-remote/" + target)
        if fetched != parent or not mirror.has_commit(parent):
            _ghc_refuse(409, "moved", "%s moved on GitHub while Hermes was reading it. Try "
                        "again." % target, retry=True)
        # E09: measure what the snapshot would store BEFORE `add -A` copies it
        # into the mirror; content over the limits is refused unwritten
        _ghc_precheck_size(mirror, parent, sandbox, cid, lim)
        try:
            tree = mirror.snapshot(parent, sandbox,
                                   os.path.join(_ghc_dir("tmp"), "index-" + cid))
        except _ghc_git.GitError as e:
            _ghc_refuse(500, "snapshot_failed", _ghc_scrub(str(e)))
        if tree == mirror.tree_of(parent):
            _ghc_refuse(400, "no_changes", "The sandbox has no changes against %s on GitHub."
                        % target)
        entries = mirror.diff_raw(parent, tree)
        numstat = mirror.numstat(parent, tree)
        patch, cut = mirror.patch(parent, tree, lim["max_diff"] + 1)
        checks, files, stat, forms = _ghc_compute_checks(
            mirror, full, row, parent, tree, entries, numstat, title_c, body_c, slug,
            len(patch.encode("utf-8", "surrogateescape")), cut,
            raw=(str(title), str(body)))
        commit = mirror.commit_tree(tree, parent, _ghc_message(title_c, body_c, cid, "pending"),
                                    ident["name"], ident["email"], now, now)
        if not mirror.integrity(commit, parent, tree):
            _ghc_refuse(500, "integrity", "The commit Hermes built does not check out. Nothing "
                        "was requested.")
        # E09: an approvable change's objects are held by a ref until it leaves
        # pending/pushing, so pruning the mirror never takes them; a blocked
        # one can never be pushed and is not held
        if not any(c["level"] == "block" for c in checks):
            mirror.update_ref(_GHC_HOLD_PREFIX + cid, commit)
    diff = _ghc_redact(patch, forms)
    checks = _ghc_redact_obj(checks, forms)
    files = _ghc_redact_obj(files, forms)
    diff_cut = bool(cut)
    if len(diff) > lim["max_diff"]:
        diff, diff_cut = diff[:lim["max_diff"]], True
    blocks = [c for c in checks if c["level"] == "block"]
    needs = [c["ack"] for c in checks if c["level"] == "warn"]
    rec = {"id": cid, "repo": full, "private": bool(row.get("private")), "branch": branch,
           "slug": slug, "target_ref": ref, "mode": mode, "default_branch": default,
           "target": target, "parent": parent, "tree": tree, "commit": commit,
           "title": _ghc_redact(title_c, forms), "body": _ghc_redact(body_c, forms),
           "sanitized": notes, "stat": stat, "files": files, "checks": checks,
           "needs_ack": needs, "confirm_public": row.get("private") is not True,
           "approvable": not blocks, "blocked": bool(blocks),
           "diff": diff, "diff_truncated": diff_cut,
           # E02: the sheet must be able to say how much of the diff it shows
           "diff_lines": diff.count("\n") + (0 if diff.endswith("\n") or not diff else 1),
           "base": {"branch": target, "sha": parent, "at": now},
           "author": {"name": ident["name"], "email": ident["email"]},
           "pr_footer": GHC_PR_FOOTER.format(host=_ghc_host(), time="<the time you approve>",
                                             id=cid),
           "state": "pending", "requested_at": now,
           "expires_at": now + int(lim["pending_ttl_s"]), "decided_at": None,
           "decision": None, "pushed_sha": None, "pr": {"number": None, "url": None,
                                                        "draft": True},
           "source": src, "session": session, "error": None}
    # a blocked change can never be pushed, so its title/body may be stored
    # redacted; an approvable one had nothing to redact
    rec["digest"] = _ghc_digest(rec)
    with _GHC_LOCK:
        _ghc_rate_check(full, branch, now, lim)
        _ghc_save(rec)
    _ghc_record("change_request", "%s@%s" % (full, branch),
                "change %s requested: %s (%d files, +%d -%d)%s" % (
                    cid, rec["title"][:80], stat["files"], stat["add"], stat["del"],
                    " BLOCKED" if blocks else ""),
                args={"id": cid, "repo": full, "branch": branch, "mode": mode,
                      "parent": parent, "tree": tree, "blocked": [c["id"] for c in blocks]},
                status="blocked" if blocks else "pending", session=session)
    _ghc_audit(rec, verdict, "asked", "push %s to %s %s" % (commit[:12], full, branch))
    if blocks:
        _ghc_gc(full, prune=True)        # E09: a blocked change's objects are not kept
    out = _ghc_summary(rec)
    out.update({"ok": True, "checks": checks, "message": (
        ("This change cannot be approved: %s Withdraw it, fix the sandbox, and request "
         "again. Do not route around these checks." % " ".join(c["text"] for c in blocks[:3]))
        if blocks else
        ("Requested. It is waiting for the owner's review in the dashboard (%s). Approving "
         "it is the owner's decision alone: tell them it is waiting, and stop."
         % out["review_url"]))})
    return out


def _ghc_rate_check(full, branch, now, lim):
    """C-rate (requests per repository per 10 minutes) and one pending change
    per (repository, branch). Called again under _GHC_LOCK right before save."""
    recs = _ghc_all(now)
    key = full.lower()
    recent = [r for r in recs if str(r.get("repo") or "").lower() == key
              and now - float(r.get("requested_at") or 0) < GHC_REQ_WINDOW]
    if len(recent) >= int(lim["repo_requests_per_10min"]):
        _ghc_refuse(429, "rate_limited", "Hermes requested %d changes to %s in the last ten "
                    "minutes. Wait a few minutes." % (len(recent), full), check="C-rate")
    for r in recs:
        if r["state"] == "pending" and str(r.get("repo") or "").lower() == key and \
                r.get("branch") == branch:
            _ghc_refuse(409, "pending_exists", "A change for %s is already waiting for review "
                        "(%s). Withdraw it first." % (branch, r["id"]), pending_id=r["id"])


# --------------------------------------------------------------------------
# GET /api/github/changes[?id=]   POST /withdraw
# --------------------------------------------------------------------------
@_ghc_safe
def _ghc_list_handler(ctx):
    cid = ctx.q1("id", "") if hasattr(ctx, "q1") else ""
    if cid:
        rec = _ghc_get(cid)
        if rec is None:
            return ({"ok": False, "error": "not_found"}, 404)
        return {"ok": True, "change": _ghc_full(rec)}
    recs = _ghc_all()
    st = ctx.q1("state", "") if hasattr(ctx, "q1") else ""
    if st in GHC_STATES:
        recs = [r for r in recs if r["state"] == st]
    rows = [_ghc_summary(r) for r in reversed(recs)][:50]
    return {"ok": True, "changes": rows,
            "pending": sum(1 for r in recs if r["state"] == "pending")}


@_ghc_safe
def _ghc_withdraw_handler(ctx):
    """pending -> withdrawn. Anyone (it only reduces capability)."""
    cid = _ghc_body(ctx).get("id")
    if not isinstance(cid, str) or not GHC_ID_RE.fullmatch(cid):
        return ({"ok": False, "error": "bad_id"}, 400)
    now = _ghc_now()

    def wd(rec):
        rec["state"] = "withdrawn"
        rec["decided_at"] = now
    rec = _ghc_transition(cid, ("pending",), wd)
    if rec is None:
        cur = _ghc_get(cid)
        if cur is None:
            return ({"ok": False, "error": "not_found"}, 404)
        return ({"ok": False, "error": "not_pending", "state": cur["state"]}, 409)
    _ghc_record("change_withdraw", "%s@%s" % (rec["repo"], rec["branch"]),
                "change %s withdrawn" % cid)
    _ghc_gc(rec["repo"])
    return {"ok": True, "change": _ghc_summary(rec)}


# --------------------------------------------------------------------------
# POST /api/github/changes/decide   (OWNER-ONLY for push_pr / push_only)
# --------------------------------------------------------------------------
def _ghc_fail(cid, error, **fields):
    _GHC_INFLIGHT.discard(cid)
    rec = _ghc_set(cid, state="failed", error=_ghc_scrub(error, 400), **fields)
    return rec


def _ghc_verify_head(api, full, branch, want):
    """GitHub's head of `branch` after a push, re-read a few times (the ref API
    can lag a moment behind git). -> sha | None | False (unreadable)."""
    last = False
    for i in range(GHC_VERIFY_TRIES):
        try:
            last = _ghc_remote_head(api, full, branch)
        except _GhcRefuse:
            last = False
        if last == want:
            return last
        if i + 1 < GHC_VERIFY_TRIES:
            time.sleep(1.0)
    return last


def _ghc_resp_text(r):
    """Everything a GitHub error answer says, lower-cased: the top-level message
    AND each errors[] entry's message/field (a 422 usually says only "Validation
    Failed" at the top)."""
    parts = [r.message or ""]
    d = r.data if isinstance(r.data, dict) else {}
    errs = d.get("errors")
    for e in errs if isinstance(errs, list) else []:
        if isinstance(e, dict):
            parts += [str(e.get("message") or ""), str(e.get("field") or "")]
        elif isinstance(e, str):
            parts.append(e)
    return " ".join(parts).lower()


def _ghc_pr_url(data):
    url = str((data or {}).get("html_url") or "")
    return url if re.fullmatch(r"https://github\.com/[A-Za-z0-9._/-]{3,300}", url) else None


def _ghc_find_open_pr(api, rec):
    """The open PR whose head is this change's branch (an append-mode push goes
    onto a branch that already has one), or None."""
    full = rec["repo"]
    r = api.get(_ghc_api_mod._rp(full) + "/pulls",
                query={"head": "%s:%s" % (full.split("/", 1)[0], rec["branch"]),
                       "state": "open", "per_page": 5})
    if r.status != 200 or not isinstance(r.data, list):
        return None
    for pr in r.data:
        if isinstance(pr, dict) and isinstance(pr.get("number"), int) \
                and not isinstance(pr.get("number"), bool) \
                and (pr.get("head") or {}).get("ref") == rec["branch"]:
            return {"number": pr["number"], "url": _ghc_pr_url(pr),
                    "draft": bool(pr.get("draft")), "existing": True}
    return None


def _ghc_open_pr(api, rec, title, body):
    """POST /pulls (draft); a 422 because draft PRs are not available on the
    plan retries ONCE as a ready PR titled "[Hermes] ..." (D5); a 422 because
    the branch already has an open PR (append mode) adopts that PR.
    -> ({number, url, draft[, existing]} | None, error)."""
    full = rec["repo"]
    payload = {"title": title, "body": body, "head": rec["branch"],
               "base": rec["default_branch"], "draft": True, "maintainer_can_modify": False}
    r = api.post(_ghc_api_mod._rp(full) + "/pulls", payload)
    if r.status == 422 and "draft" in _ghc_resp_text(r):
        payload["draft"] = False
        payload["title"] = ("[Hermes] " + title)[:GHC_TITLE_MAX + 9]
        r = api.post(_ghc_api_mod._rp(full) + "/pulls", payload)
    if r.status == 201 and isinstance(r.data, dict) and isinstance(r.data.get("number"), int):
        return {"number": r.data["number"], "url": _ghc_pr_url(r.data),
                "draft": bool(payload["draft"])}, None
    if r.status == 422 and "already exists" in _ghc_resp_text(r):
        ex = _ghc_find_open_pr(api, rec)
        if ex:
            return ex, None
    return None, "GitHub did not open the pull request (status %s%s)." % (
        r.status, (": " + _ghc_scrub(r.message, 160)) if r.message else "")


def _ghc_decide_args(b, rec):
    """Validate the approve half of a decide body. -> (title, body, acks)."""
    acks = b.get("acks", [])
    if not isinstance(acks, list) or not all(isinstance(a, str) for a in acks):
        _ghc_refuse(400, "bad_acks", "acks is a list of check ids.")
    missing = [a for a in rec.get("needs_ack") or [] if a not in acks]
    if missing:
        _ghc_refuse(422, "needs_ack", "Tick every warning first.", needs_ack=missing)
    if rec.get("confirm_public") and b.get("confirm_public") is not True:
        _ghc_refuse(422, "confirm_public", "Confirm that this repository is public.")
    title, body = rec["title"], rec["body"]
    edited = False
    if "title" in b:
        if not isinstance(b["title"], str) or not b["title"].strip() or \
                len(b["title"]) > GHC_TITLE_IN_MAX:
            _ghc_refuse(400, "bad_title", "The title must be one line of text.")
        title, edited = b["title"], True
    if "body" in b:
        if not isinstance(b["body"], str) or len(b["body"]) > GHC_BODY_IN_MAX:
            _ghc_refuse(400, "bad_body", "The description must be text.")
        body, edited = b["body"], True
    if edited:
        title, body, _n = _ghc_clean_text(title, body)
        if "body" in b and _n.get("body_cut"):
            _ghc_refuse(422, "body_too_long", "The description is longer than the %d KB "
                        "Hermes sends once it is cleaned, so it would be cut. Shorten it and "
                        "approve again."
                        % (GHC_BODY_MAX // 1024))
        forms, _bf = _ghc_needles(_ghc_known_secrets())
        if any(f in title or f in body for f in forms):
            _ghc_refuse(422, "secret_in_text", "The title or description contains one of your "
                        "saved secret values.", check="C-secret-known")
        if not title:
            _ghc_refuse(400, "bad_title", "The title must not be empty.")
    return title, body


@_ghc_safe
def _ghc_decide_handler(ctx):
    b = _ghc_body(ctx)
    cid, decision, digest = b.get("id"), b.get("decision"), b.get("digest")
    if not isinstance(cid, str) or not GHC_ID_RE.fullmatch(cid):
        return ({"ok": False, "error": "bad_id"}, 400)
    if decision not in GHC_DECISIONS:
        return ({"ok": False, "error": "bad_decision",
                 "hint": "decision is push_pr, push_only or reject"}, 400)
    if decision != "reject":
        _ghc_owner_check(ctx)
        # approving must be recorded first; REJECTING only reduces capability
        # and must stay possible when the Flight Recorder is down (the row is
        # then simply not written)
        _ghc_require_recorder()
    rec = _ghc_get(cid)
    if rec is None:
        return ({"ok": False, "error": "not_found"}, 404)
    if rec["state"] != "pending":
        return ({"ok": False, "error": "not_pending", "stale": True, "state": rec["state"]},
                409)
    if not isinstance(digest, str) or digest != rec.get("digest") or \
            not _ghc_digest_ok(rec):
        return ({"ok": False, "error": "stale", "stale": True,
                 "hint": "This card is out of date. Reopen the review."}, 409)
    now = _ghc_now()
    if decision == "reject":
        def rj(r):
            r["state"], r["decided_at"], r["decision"] = "rejected", now, "reject"
        done = _ghc_transition(cid, ("pending",), lambda r: (
            rj(r) if r.get("digest") == digest else None))
        if done is None or done["state"] != "rejected":
            return ({"ok": False, "error": "not_pending", "stale": True}, 409)
        tier, verdict = _ghc_trust(rec["repo"])
        _ghc_audit(done, verdict, "user-deny", "reject %s" % cid)
        _ghc_record("change_reject", "%s@%s" % (rec["repo"], rec["branch"]),
                    "change %s rejected by the owner" % cid)
        _ghc_gc(rec["repo"])
        return {"ok": True, "state": "rejected", "change": _ghc_summary(done)}

    # ---- approve: everything that can refuse runs BEFORE pending -> pushing ----
    if not rec.get("approvable"):
        return ({"ok": False, "error": "blocked", "blocked": True,
                 "checks": [c for c in rec.get("checks") or [] if c["level"] == "block"]}, 422)
    title, body = _ghc_decide_args(b, rec)
    full = rec["repo"]
    refusal, verdict = _ghc_decide_gate(full)
    if refusal:
        return refusal
    ident = _ghc_call("_gh_identity")
    api = _ghc_api()
    mirror = _ghc_mirror(full)
    if not mirror.exists():
        return ({"ok": False, "error": "not_prepared"}, 409)
    url = _ghc_url(full)          # a bad git base refuses before anything moves
    net = globals().get("_GH_NET_LOCK")
    if net is None:
        _ghc_refuse(503, "connector_unavailable", "The GitHub connector did not load.")
    if not net.acquire(timeout=GHC_NET_WAIT):
        return ({"ok": False, "error": "busy",
                 "hint": "Hermes is talking to GitHub. Try again in a moment."}, 409)
    try:
        # E05: Pause/holds, Trust and the budget again, now that the lock is
        # held: an approval may have waited up to GHC_NET_WAIT behind another
        # push (or the 6-hourly check), and push ledger rows are only appended
        # under this lock, so the budget read here is exact
        refusal, verdict = _ghc_decide_gate(full)
        if refusal:
            return refusal
        _ghc_reconcile(full, net_held=True)            # E57
        refusal = _ghc_visibility_refusal(rec, b)      # E55
        if refusal:
            return refusal
        # one-shot: pending -> pushing exactly once (digest re-checked under the lock)
        def go(r):
            if r.get("digest") != digest or r["id"] in _GHC_INFLIGHT:
                raise _GhcRefuse({"ok": False, "error": "stale", "stale": True}, 409)
            r["state"], r["decided_at"], r["decision"] = "pushing", now, decision
            r["requested_text"] = [r["title"], r["body"]]
            r["title"], r["body"] = title, body
            _GHC_INFLIGHT.add(r["id"])
        try:
            rec = _ghc_transition(cid, ("pending",), go)
        except _GhcRefuse as e:
            return e.payload, e.status
        if rec is None:
            return ({"ok": False, "error": "not_pending", "stale": True}, 409)
        try:
            res = _ghc_push_locked(rec, decision, title, body, now, api, mirror, ident,
                                   verdict, url)
        except BaseException as e:
            # never leave a change stuck in 'pushing'
            if cid in _GHC_INFLIGHT:
                _ghc_fail(cid, "Hermes stopped while pushing (%s). Check the branch on GitHub "
                               "before asking again." % type(e).__name__, interrupted=True)
            raise
    finally:
        net.release()
    _ghc_gc(full)                  # E09: a decided change no longer holds its objects
    if res[0] != "pushed":
        if len(res) > 3 and res[3]:
            sent, det = _ghc_notify(res[3])
            if not sent:
                _ghc_log("push notice not delivered: %s" % det)
        return res[1], res[2]
    commit = res[1]
    try:
        return _ghc_after_push(cid, rec, decision, title, body, now, api, commit, full)
    except BaseException as e:
        _GHC_INFLIGHT.discard(cid)
        _ghc_set(cid, state="pushed", pushed_sha=commit, commit=commit,
                 error="Pushed, but Hermes stopped before finishing the bookkeeping (%s)."
                       % type(e).__name__)
        raise


def _ghc_reconcile(full=None, net_held=False):
    """E57: settle every change that was interrupted mid-push (swept from
    'pushing' after a restart, or stopped by an exception). Its push ledger
    row was written BEFORE the push, so unless the push landed the ledger
    names a commit GitHub never received. For each such change whose row is
    still the ledger head of its branch, GitHub's head is read once:
      * it is the commit -> the push landed: the change becomes 'pushed';
      * it is what the branch had before -> the same compensating row the
        failed-push path writes (a ref_delete, or a push of the parent, with
        change id '<id>:undo', which the push budget skips);
      * anything else -> left to the drift check, said on the change.
    Takes _GH_NET_LOCK (briefly; skipped when busy) unless `net_held`.
    -> how many changes were settled. Never raises."""
    todo = [r for r in _ghc_all() if r["state"] == "failed" and r.get("interrupted")
            and not r.get("reconciled")
            and (full is None or str(r.get("repo")).lower() == str(full).lower())]
    if not todo:
        return 0
    net = globals().get("_GH_NET_LOCK")
    if not net_held:
        if net is None or not net.acquire(timeout=5):
            return 0
    n = 0
    try:
        api = _ghc_api()
        for rec in todo:
            try:
                n += _ghc_reconcile_one(api, rec)
            except _GhcRefuse:
                continue                     # GitHub unreadable: try again later
            except Exception as e:
                _ghc_log("reconcile failed: %s" % type(e).__name__)
    except _GhcRefuse:
        pass
    finally:
        if not net_held:
            net.release()
    return n


def _ghc_reconcile_one(api, rec):
    cid, full, branch = rec["id"], rec["repo"], rec["branch"]
    rows = _ghc_call("_gh_ledger_rows")
    mine = [r for r in rows if r.get("change_id") == cid and r.get("event") == "push"
            and str(r.get("repo") or "").lower() == full.lower() and r.get("branch") == branch]
    if not mine:
        _ghc_set(cid, reconciled=True)       # stopped before its ledger row: nothing out
        return 1
    sha = mine[-1].get("sha")
    led = _ghc_call("_gh_ledger_heads", full).get(branch)
    if led != sha:
        _ghc_set(cid, reconciled=True)       # later ledger activity on the branch
        return 1
    head = _ghc_remote_head(api, full, branch)
    previous = None if rec.get("mode") == "new" else rec.get("parent")
    if head == sha:
        _GHC_INFLIGHT.discard(cid)
        _ghc_set(cid, state="pushed", pushed_sha=sha, commit=sha, reconciled=True,
                 error="Pushed, confirmed after Hermes was interrupted; no pull request was "
                       "opened.")
        _ghc_record("push", "%s@%s" % (full, branch),
                    "change %s: an interrupted push turned out to have landed" % cid,
                    after_state={"commit": sha})
        return 1
    if head == previous:
        if previous is None:
            _ghc_call("_gh_ledger_append", {"event": "ref_delete", "repo": full,
                                            "branch": branch, "change_id": cid + ":undo"})
        else:
            _ghc_call("_gh_ledger_append", {"event": "push", "repo": full, "branch": branch,
                                            "sha": previous, "change_id": cid + ":undo"})
        _ghc_set(cid, reconciled=True, pushed_sha=None,
                 error="Interrupted before the push reached GitHub; nothing was pushed.")
        _ghc_record("change_failed", "%s@%s" % (full, branch),
                    "change %s: interrupted push did not land; ledger restored" % cid,
                    status="error")
        return 1
    _ghc_set(cid, reconciled=True,
             error="Interrupted while pushing, and %s on GitHub is neither this change nor "
                   "what it had before. Check the branch on GitHub." % _ghc_line(branch, 80))
    return 1


def _ghc_visibility_refusal(rec, b):
    """E55: the public-repository confirmation must not rest on a visibility
    flag up to six hours old. When the connector offers
    _gh_repo_visibility_fresh (one GET, no _GH_NET_LOCK, so callable under it),
    the repository's visibility is read now: unreadable, renamed or changed
    since the request -> stale (409; the change must be requested again);
    public now -> the body must carry confirm_public: true. An older
    connector without it keeps the stored flag. -> (payload, status) | None."""
    fn = _ghc_fn("_gh_repo_visibility_fresh")
    if fn is None:
        return None
    try:
        v = fn(rec["repo"])
    except Exception:
        v = None
    if not isinstance(v, dict) or v.get("ok") is not True or v.get("moved"):
        return ({"ok": False, "error": "stale", "stale": True,
                 "hint": "Hermes could not confirm on GitHub whether %s is public, so nothing "
                         "was pushed. Try again in a moment." % _ghc_line(rec["repo"], 140)},
                409)
    public_now = v.get("private") is not True
    if v.get("changed") or public_now != bool(rec.get("confirm_public")):
        return ({"ok": False, "error": "stale", "stale": True,
                 "hint": "%s is now %s on GitHub, so this review is out of date. Withdraw it "
                         "and ask Hermes to request the change again." % (
                             _ghc_line(rec["repo"], 140), "public" if public_now else "private")},
                409)
    if public_now and b.get("confirm_public") is not True:
        return ({"ok": False, "error": "confirm_public",
                 "hint": "Confirm that this repository is public."}, 422)
    return None


def _ghc_decide_gate(full):
    """The approve-time conditions that can change while a decide waits for
    _GH_NET_LOCK: writes paused / held, the Trust tier, the daily push budget.
    -> ((payload, status) | None, trust verdict). Called before the wait AND
    again under the lock (E05)."""
    ok, why = _ghc_call("_gh_writes_allowed", full)
    if not ok:
        return ({"ok": False, "error": "writes_blocked", "hint": _ghc_line(why, 400)}, 409), None
    tier, verdict = _ghc_trust(full)
    if tier == "never":
        return ({"ok": False, "error": "trust_never",
                 "hint": "Your Trust settings say Never for GitHub pushes to %s." % full},
                403), verdict
    budget = _ghc_call("_gh_push_budget")
    if int(budget.get("left") or 0) <= 0:
        return ({"ok": False, "error": "push_budget",
                 "hint": "Hermes has used today's %s pushes." % budget.get("limit")},
                429), verdict
    return None, verdict


def _ghc_push_locked(rec, decision, title, body, now, api, mirror, ident, verdict, url):
    """The part of decide that runs holding _GH_NET_LOCK, with the change in
    'pushing'. -> ("pushed", commit) or ("error", payload, status[, notice]);
    every error path has already marked the change failed (or put it back).
    `notice` is a Telegram text the caller sends after releasing the lock."""
    cid, full, branch = rec["id"], rec["repo"], rec["branch"]
    approved = _ghc_iso(now)
    if not _ghc_record("change_approve", "%s@%s" % (full, branch),
                       "owner approved change %s (%s)" % (cid, decision),
                       args={"id": cid, "decision": decision, "parent": rec["parent"],
                             "tree": rec["tree"]}, status="pending",
                       session=rec.get("session")):
        # nothing has left the Mac: back to pending, refuse unrecorded
        _GHC_INFLIGHT.discard(cid)
        orig = rec.get("requested_text") or [rec["title"], rec["body"]]
        _ghc_set(cid, state="pending", decided_at=None, decision=None, title=orig[0],
                 body=orig[1], requested_text=None)
        return ("error", {"ok": False, "error": "recorder_unavailable",
                          "hint": "The Flight Recorder did not record the approval, so "
                                  "nothing was pushed."}, 503)
    _ghc_audit(rec, verdict, "user-approve", "push %s to %s %s" % (
        rec["tree"][:12], full, branch))
    # rebuild with the final text + approval time: same tree, same parent
    try:
        # under the mirror lock with its hold ref (E09), so a prune cannot
        # take the rebuilt commit before it is pushed
        with _ghc_git.mirror_lock(mirror.path):
            commit = mirror.commit_tree(
                rec["tree"], rec["parent"], _ghc_message(title, body, cid, approved),
                ident["name"], ident["email"], rec["requested_at"], now)
            mirror.update_ref(_GHC_HOLD_PREFIX + cid, commit)
        good = mirror.integrity(commit, rec["parent"], rec["tree"])
    except _ghc_git.GitError as e:
        _ghc_fail(cid, "Hermes could not build the commit: %s" % e)
        return ("error", {"ok": False, "error": "failed", "state": "failed"}, 500)
    if not good:
        _ghc_fail(cid, "The commit Hermes built does not check out; nothing was pushed.")
        return ("error", {"ok": False, "error": "failed", "state": "failed"}, 500)
    # re-verify remote truth: never rebase silently
    try:
        if rec["mode"] == "new":
            d_head = _ghc_remote_head(api, full, rec["default_branch"])
            b_head = _ghc_remote_head(api, full, branch)
            moved = d_head != rec["parent"] or b_head is not None
            what = rec["default_branch"] if d_head != rec["parent"] else branch
        else:
            b_head = _ghc_remote_head(api, full, branch)
            led = _ghc_call("_gh_ledger_heads", full).get(branch)
            moved = b_head != rec["parent"] or led != rec["parent"]
            what = branch
    except _GhcRefuse as e:
        _ghc_fail(cid, "Hermes could not check GitHub before pushing, so nothing was pushed. "
                       "Ask Hermes to request it again.")
        return ("error", {"ok": False, "error": "failed", "state": "failed",
                          "hint": e.payload.get("hint")}, 502)
    if moved:
        msg = ("%s moved on GitHub after this change was requested; nothing was pushed. Ask "
               "Hermes to refresh the sandbox and request it again." % what)
        _ghc_fail(cid, msg)
        _ghc_record("change_failed", "%s@%s" % (full, branch),
                    "change %s not pushed: %s moved" % (cid, what), status="error")
        return ("error", {"ok": False, "error": "moved", "state": "failed", "hint": msg}, 409)
    # the ledger row goes in BEFORE the push (drift-check consistency, even
    # across a crash between the push and the bookkeeping)
    try:
        _ghc_call("_gh_ledger_append", {"event": "push", "repo": full, "branch": branch,
                                        "sha": commit, "change_id": cid})
    except (ValueError, _GhcRefuse, OSError):
        _ghc_fail(cid, "Hermes could not write its push ledger, so nothing was pushed.")
        return ("error", {"ok": False, "error": "failed", "state": "failed"}, 500)
    try:
        push_ok, flag, line = mirror.push(url, commit, rec["slug"],
                                          auth_env=_ghc_call("_gh_git_env") or None)
    except _ghc_git.GitError as e:
        push_ok, flag, line = False, "", _ghc_scrub(str(e))
    head = _ghc_verify_head(api, full, branch, commit)
    if head == commit:
        return ("pushed", commit)
    previous = None if rec["mode"] == "new" else rec["parent"]
    maybe_landed = bool(push_ok) or head is False
    if not push_ok and head is not False and head == previous:
        # nothing changed on GitHub: put the ledger back where it was
        try:
            if previous is None:
                _ghc_call("_gh_ledger_append", {"event": "ref_delete", "repo": full,
                                                "branch": branch, "change_id": cid + ":undo"})
            else:
                _ghc_call("_gh_ledger_append", {"event": "push", "repo": full,
                                                "branch": branch, "sha": previous,
                                                "change_id": cid + ":undo"})
        except Exception:
            pass
        err = "The push did not go through: %s" % line
    elif push_ok:
        err = ("git reported the push, but GitHub does not show %s on %s yet. Check the "
               "branch on GitHub." % (commit[:12], branch))
    else:
        err = ("The push did not go through, and GitHub shows something unexpected on %s. "
               "Check the branch on GitHub." % branch)
    # a push that may have landed keeps its sha: the Settings card lists a
    # failed change WITH a pushed_sha under Recent pushes ("Unconfirmed"), the
    # only place the owner sees a branch that may exist (no PR, no retract)
    _ghc_fail(cid, err, commit=commit, pushed_sha=(commit if maybe_landed else None))
    _ghc_record("change_failed", "%s@%s" % (full, branch), "change %s push failed" % cid,
                status="error", args={"id": cid, "porcelain": line})
    notice = None
    if maybe_landed:
        # git said the push went through, or GitHub could not be read to say it
        # did not: the branch may well exist. The owner hears about EVERY push
        # that may have happened (the ledger row stays, so the drift check
        # does not call it foreign), not only the ones Hermes could confirm.
        # (Sent by the caller once _GH_NET_LOCK is released: the sender can
        # take a minute.)
        n_files = int(rec["stat"]["files"])
        notice = ("Hermes tried to push %d file%s to %s on %s but could not confirm the result "
                  "on GitHub, so the branch may exist. Check it on GitHub. If you did not "
                  "approve this, pause GitHub writes in Settings › Connections." % (
                      n_files, "" if n_files == 1 else "s", _ghc_line(full, 140),
                      _ghc_line(branch, 80)))
    return ("error", {"ok": False, "error": "push_failed", "state": "failed",
                      "hint": _ghc_scrub(err, 400)}, 502, notice)


def _ghc_after_push(cid, rec, decision, title, body, now, api, commit, full):
    """PR (optional), ledger pr_open, recorder row, the Telegram notice."""
    pr, pr_err = None, None
    if decision == "push_pr":
        footer = GHC_PR_FOOTER.format(host=_ghc_host(), time=_ghc_12h(now), id=cid)
        pr, pr_err = _ghc_open_pr(api, rec, title, (body + "\n\n---\n" if body else "") + footer)
        if pr and not pr.get("existing"):
            try:
                row = {"event": "pr_open", "repo": full, "branch": rec["branch"], "sha": commit,
                       "change_id": cid, "pr_number": pr["number"]}
                if pr.get("url"):
                    row["pr_url"] = pr["url"]
                _ghc_call("_gh_ledger_append", row)
            except Exception:
                pass
    state = "pr_open" if pr else "pushed"
    _GHC_INFLIGHT.discard(cid)
    rec = _ghc_set(cid, state=state, pushed_sha=commit, commit=commit,
                   pr=pr or {"number": None, "url": None, "draft": True},
                   error=pr_err) or rec
    _ghc_record("push", "%s@%s" % (full, rec["branch"]),
                "pushed %d files to %s %s%s" % (rec["stat"]["files"], full, rec["branch"],
                                                (" and %s PR #%d" % (
                                                    "updated" if pr.get("existing")
                                                    else "opened", pr["number"])) if pr
                                                else ""),
                args={"id": cid, "decision": decision, "parent": rec["parent"]},
                after_state={"commit": commit, "tree": rec["tree"],
                             "pr_url": (pr or {}).get("url")},
                session=rec.get("session"))
    opened = pr if (pr and not pr.get("existing")) else None
    text = _ghc_call("_gh_push_notice_text", full, rec["branch"], int(rec["stat"]["files"]),
                     pr_number=(opened or {}).get("number"),
                     draft=(opened or {}).get("draft", True))
    sent, det = _ghc_notify(text)
    if not sent:
        _ghc_log("push notice not delivered: %s" % det)
    out = {"ok": True, "state": state, "pushed_sha": commit, "pr": pr, "notified": bool(sent),
           "change": _ghc_summary(rec)}
    if pr_err:
        out["pr_error"] = pr_err
    return out


# --------------------------------------------------------------------------
# POST /api/github/changes/retract   (OWNER-ONLY; ledger-matching head only)
# --------------------------------------------------------------------------
@_ghc_safe
def _ghc_retract_handler(ctx):
    """Close the PR and delete refs/heads/hermes/<slug> — ONLY while GitHub's
    head is the sha the ledger says Hermes left there AND that is this
    change's commit. A branch someone else moved is never touched."""
    b = _ghc_body(ctx)
    cid = b.get("id")
    if not isinstance(cid, str) or not GHC_ID_RE.fullmatch(cid):
        return ({"ok": False, "error": "bad_id"}, 400)
    _ghc_owner_check(ctx)
    _ghc_require_recorder()
    rec = _ghc_get(cid)
    if rec is None:
        return ({"ok": False, "error": "not_found"}, 404)
    if rec["state"] not in ("pushed", "pr_open") or not rec.get("pushed_sha"):
        return ({"ok": False, "error": "not_retractable", "state": rec["state"]}, 409)
    full, branch, sha = rec["repo"], rec["branch"], rec["pushed_sha"]
    api = _ghc_api()
    mirror = _ghc_mirror(full)
    # E09: the mirror may be gone (its repository was removed, or Disconnect
    # deleted the mirrors). Deleting a branch needs no objects, only a
    # repository to run git in, so an empty one is made here, BEFORE the pull
    # request is closed: a retract never stops half-way for want of a mirror
    try:
        mirror.ensure()
    except (_ghc_git.GitError, OSError) as e:
        return ({"ok": False, "error": "mirror_unavailable",
                 "hint": "Hermes could not set up its local copy of %s, so nothing was "
                         "changed on GitHub (%s)." % (_ghc_line(full, 140),
                                                      type(e).__name__)}, 503)
    net = globals().get("_GH_NET_LOCK")
    if net is None:
        _ghc_refuse(503, "connector_unavailable", "The GitHub connector did not load.")
    if not net.acquire(timeout=GHC_NET_WAIT):
        return ({"ok": False, "error": "busy"}, 409)
    try:
        _ghc_reconcile(full, net_held=True)            # E57
        led = _ghc_call("_gh_ledger_heads", full).get(branch)
        try:
            head = _ghc_remote_head(api, full, branch)
        except _GhcRefuse as e:
            return e.payload, e.status
        if led != sha or head != sha:
            return ({"ok": False, "error": "branch_changed",
                     "hint": ("%s on GitHub is not what Hermes pushed for this change (it "
                              "moved, or a later change added to it), so Hermes will not "
                              "delete it." % branch)}, 409)
        closed = None
        pr = rec.get("pr") or {}
        if isinstance(pr.get("number"), int):
            r = api.request("PATCH", _ghc_api_mod._rp(full) + "/pulls/%d" % pr["number"],
                            body={"state": "closed"})
            closed = r.status == 200
            if closed:
                try:
                    _ghc_call("_gh_ledger_append", {"event": "pr_close", "repo": full,
                                                    "branch": branch, "sha": sha,
                                                    "change_id": cid,
                                                    "pr_number": pr["number"]})
                except Exception:
                    pass
        try:
            ok, flag, line = mirror.delete_branch(_ghc_url(full), rec["slug"], sha,
                                                  auth_env=_ghc_call("_gh_git_env") or None)
        except _ghc_git.GitError as e:
            ok, line = False, _ghc_scrub(str(e))
        except OSError as e:                 # the mirror vanished under the call
            ok, line = False, "the local mirror is unavailable (%s)" % type(e).__name__
        after = _ghc_verify_head(api, full, branch, None)
        if after is not None:
            _ghc_record("retract", "%s@%s" % (full, branch),
                        "retract of %s failed: %s" % (cid, line), status="error")
            return ({"ok": False, "error": "delete_failed", "pr_closed": closed,
                     "hint": "GitHub still has %s: %s" % (branch, _ghc_scrub(line))}, 502)
        # deleted: the ledger row goes in AFTER (a crash here leaves the ledger
        # naming a sha on a branch that no longer exists, which the drift check
        # never alerts on)
        try:
            _ghc_call("_gh_ledger_append", {"event": "ref_delete", "repo": full,
                                            "branch": branch, "change_id": cid})
        except Exception:
            pass
    finally:
        net.release()
    now = _ghc_now()
    rec = _ghc_set(cid, state="retracted", retracted_at=now) or rec
    # E58: the branch (and its PR) is gone, so every other change still shown
    # as pushed on it went with it
    for other in _ghc_all():
        if other["id"] != cid and other["state"] in ("pushed", "pr_open") and \
                str(other.get("repo")).lower() == full.lower() and \
                other.get("branch") == branch:
            _ghc_transition(other["id"], ("pushed", "pr_open"), lambda r: r.update(
                state="retracted", retracted_at=now,
                error="Retracted together with %s (the branch was deleted)." % cid))
    _ghc_record("retract", "%s@%s" % (full, branch),
                "retracted change %s: deleted %s%s" % (
                    cid, branch, " and closed PR #%d" % pr["number"] if closed else ""),
                after_state={"deleted": branch, "sha": sha, "pr_closed": closed})
    _ghc_notify("Hermes deleted %s on %s%s, as you asked from the dashboard. If you did not "
                "ask for this, pause GitHub writes in Settings › Connections." % (
                    _ghc_line(branch, 80), _ghc_line(full, 140),
                    (" and closed PR #%d" % pr["number"]) if closed else ""))
    return {"ok": True, "state": "retracted", "pr_closed": closed,
            "change": _ghc_summary(rec)}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
register_post("/api/github/sandbox/prepare", _ghc_prepare_handler)      # noqa: F821
register_post("/api/github/sandbox/refresh", _ghc_refresh_handler)      # noqa: F821
register_post("/api/github/changes", _ghc_request_handler)              # noqa: F821
register_get("/api/github/changes", _ghc_list_handler)                  # noqa: F821
register_post("/api/github/changes/withdraw", _ghc_withdraw_handler)    # noqa: F821
register_post("/api/github/changes/decide", _ghc_decide_handler)        # noqa: F821
register_post("/api/github/changes/retract", _ghc_retract_handler)      # noqa: F821
