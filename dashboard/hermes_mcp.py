#!/usr/bin/env python3
# ==========================================================================
# hermes_mcp.py — Hermes Assistant as a personal context MCP server (1.1.4)
#
# Ships §4 item 3 / §4b "Personal context MCP server" of
# docs/plans/purpose-and-direction.md: a loopback, READ-ONLY Model Context
# Protocol server so another agent on this Mac (Claude Code, primarily) can
# ask Hermes what it knows — calendar, notes, chats, the needs-you inbox,
# remembered facts, the unified index — without a second copy of the data
# and without anything leaving the machine.
#
#   claude mcp add hermes-assistant -- python3 <repo>/dashboard/hermes_mcp.py
#
# DESIGN RULES (each one is load-bearing, do not "simplify" them away)
#
#  1. THIS PROCESS NEVER TOUCHES A STORE.  Not index.db, not chats/*.json,
#     not icalBuddy, not USER.md.  Every tool is an HTTP GET against the
#     dashboard on 127.0.0.1:7788, so the dashboard stays the single place
#     that knows how to read the user's data — one set of invariants
#     (PREWARM_SESSION is reserved, FTS queries are sanitised, an absent
#     store degrades instead of raising), not two.  A second reader would
#     drift the moment either side changed.
#
#  2. NO Origin HEADER.  server.py's same-origin guard (CLAUDE.md, "API
#     same-origin guard") refuses a *present* cross-origin Origin and allows
#     requests that carry none — that is what leaves curl and the launchd
#     scripts working.  urllib sends no Origin unless told to, so do not add
#     one; the Host we send (127.0.0.1:7788) is in ALLOWED_HOSTS.
#
#  3. STDOUT IS THE PROTOCOL CHANNEL.  Per the stdio transport spec, the
#     server MUST NOT write anything to stdout that is not an MCP message,
#     and messages are newline-delimited and MUST NOT contain embedded
#     newlines (json.dumps escapes them, which is why every write goes
#     through _send()).  All logging goes to stderr, one line per call.
#
#  4. THE ALLOWLIST IS STATIC AND READ ONCE.  ~/.hermes/mcp-allow.json is
#     loaded at startup and never re-read: scope is decided at launch by the
#     owner, never negotiated at runtime by a model (§4b).  A disabled tool
#     is not merely refused — it is not even listed, so it never enters a
#     model's context as an option.  A corrupt file falls back to the
#     DEFAULTS (messages off), never to "allow everything".
#
#  5. EVERY RESULT IS BOUNDED PLAIN TEXT.  MAX_TEXT bytes, redacted, and an
#     upstream body that is not JSON is never forwarded (that is the only
#     way an HTML error page could reach a model).
#
# Stdlib only — this is launched by another agent's `python3`, which may be
# Homebrew 3.14 or the framework 3.12; nothing here is version-specific and
# nothing is imported that a bare CPython lacks.
# ==========================================================================
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
SERVER_NAME = "hermes-assistant"
_HERE = os.path.dirname(os.path.abspath(__file__))

# Protocol versions this server speaks, newest first.  initialize echoes the
# client's version when we know it and otherwise answers with LATEST, which
# is what the lifecycle spec asks a server to do on an unknown version.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL = PROTOCOL_VERSIONS[0]

DASH_URL = (os.environ.get("HERMES_MCP_DASHBOARD") or "http://127.0.0.1:7788").rstrip("/")
HTTP_TIMEOUT = 6.0          # the dashboard answers cached reads in ms
CAL_TIMEOUT = 20.0          # /api/expand?id=today may shell out to icalBuddy (15s cap)
MAX_BODY = 4 * 1024 * 1024  # never read an unbounded response into memory

MAX_TEXT = 8 * 1024         # hard ceiling on any single tool result, bytes
_TRUNC_NOTE = "\n\n[truncated — Hermes caps one MCP result at 8 KB]"

ALLOW_PATH = os.path.expanduser("~/.hermes/mcp-allow.json")
DEFAULT_ALLOW = {
    "tools": {
        "hermes_search": True,
        "calendar_next": True,
        "calendar_search": True,
        "notes_search": True,
        "chats_search": True,
        "chat_get": True,
        "needs_you": True,
        "memory_get": True,
        "messages_search": False,   # message bodies are the most private source
    },
    "max_results": 20,
}
MAX_RESULTS_CAP = 50            # /api/search refuses more than this anyway

# Chat-store invariants copied from server.py (see rule 1: we validate before
# the request so a bad id never reaches the filesystem side at all).
SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
PREWARM_SESSION = "__prewarm__"

# Real conversation turns vs. metadata — the same two constants aux_convos.py
# uses for its search and its export.  Tool lines, approval prompts and any
# status row a future build stores are metadata and never leave this process.
_CV_ROLES = ("user", "bot", "assistant")
_CV_META_KEYS = ("tool", "tool_name", "approval", "status", "kind")

# --- secret scrubbing ------------------------------------------------------
# COPIED VERBATIM from aux_convos.py's export scrubber (copied, not imported:
# importing would drag server.py's globals into this process and break rule 1).
# If those regexes change there, change them here too.
_CV_SECRET_RE = re.compile(
    r"\b(serve_sid|serve_key|session_token|token|api[_-]?key|apikey|"
    r"secret|password|passwd|authorization)\b\s*[:=]\s*"
    r"[\"']?(?:Bearer\s+)?([A-Za-z0-9_\-./+=]{6,})[\"']?", re.I)
_CV_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9_\-.=]{8,}", re.I)
_CV_HERMES_PATH_RE = re.compile(
    r"(?:~|/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+)/\.hermes"
    r"(?:/[^\s`'\"),;]*)?")

# The block below is MIRRORED VERBATIM from dashboard/aux_convos.py (copied,
# not imported: importing would drag server.py's globals into this process and
# break rule 1).  Change it there, change it here; the markers make the diff
# mechanical.

# ===== BEGIN MIRRORED SECRET BLOCK — KEEP BYTE-IDENTICAL (1.1.5) ===========
# Raw, UNLABELED secrets.  The key=value rule above only fires when a label
# ("token:", "api_key=") precedes the value and the Bearer rule only when the
# scheme does — but an agent that echoes a bare `sk-ant-...`, a PEM block or a
# Telegram bot token leaks exactly as hard with no label anywhere in sight.
# Ordered most-specific-first where prefixes overlap (sk-ant- before sk-) so
# the narrower shape wins the match, and PEM first because its body is base64
# that a later pattern could nibble at.  Every entry is anchored on a fixed
# vendor prefix (or a digits-colon shape) plus a minimum length, so ordinary
# prose and a 40-char git sha — hex only, no prefix, no colon — cannot match.
# That restraint is the point: a redactor that eats normal text gets switched
# off, and a switched-off redactor protects nothing.
_CV_RAW_SECRET_RES = (
    # PEM private key — multiline; non-greedy so two keys are two matches
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?"
               r"-----END [A-Z ]*PRIVATE KEY-----"),
    # JWT: three base64url segments, the first being the `{"alg"...` header
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),                  # Anthropic
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),                      # OpenAI-style
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),   # GitHub PAT
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),               # GH fine-grained
    re.compile(r"\bAKIA[0-9A-Z]{16}"),                           # AWS key id
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}"),               # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                     # Google API key
    # Telegram bot token, <8-10 digit bot id>:<35 chars>.  Deliberately NO \b
    # in front: the shape this Mac actually leaks is the send URL
    # ".../bot<id>:<secret>/sendMessage", where "bot" runs straight into the
    # digits and a word boundary would never match.
    re.compile(r"[0-9]{8,10}:[A-Za-z0-9_-]{35}"),
)


def _cv_redact_raw(text):
    """Replace secrets that arrive with no label in front of them.

    Runs LAST, after the labeled and Bearer rules: those already collapsed the
    values they own, so whatever still matches here genuinely had no label.
    """
    for _rx in _CV_RAW_SECRET_RES:
        text = _rx.sub("[redacted]", text)
    return text
# ===== END MIRRORED SECRET BLOCK ==========================================

# Defensive only: an upstream body is never forwarded unparsed, so nothing
# should ever match.  If a stored note or chat literally contains a script
# tag we neutralise the opening bracket rather than hand a model markup.
_HTMLISH_RE = re.compile(r"<(?=\s*/?\s*(?:script|iframe|object|embed|html|!doctype)\b)",
                         re.I)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _redact(text):
    """aux_convos._cv_redact, verbatim — path, key=value, Bearer, then the
    unlabeled raw shapes (1.1.5)."""
    text = _CV_HERMES_PATH_RE.sub("[redacted path]", text)
    text = _CV_SECRET_RE.sub(lambda m: m.group(1) + ": [redacted]", text)
    text = _CV_BEARER_RE.sub("Bearer [redacted]", text)
    return _cv_redact_raw(text)          # unlabeled shapes last (1.1.5)


def _plain(v, limit=0):
    """Any upstream value -> one safe plain-text string."""
    if v is None:
        return ""
    if not isinstance(v, str):
        v = str(v)
    v = _CTRL_RE.sub(" ", v)
    v = _HTMLISH_RE.sub("&lt;", v)
    v = _redact(v)
    if limit and len(v) > limit:
        v = v[:limit - 1].rstrip() + "…"
    return v


def _bound(text):
    """<= MAX_TEXT bytes of UTF-8, cut on a codepoint boundary."""
    raw = text.encode("utf-8")
    if len(raw) <= MAX_TEXT:
        return text
    keep = MAX_TEXT - len(_TRUNC_NOTE.encode("utf-8"))
    return raw[:keep].decode("utf-8", "ignore").rstrip() + _TRUNC_NOTE


def _version():
    try:
        with open(os.path.join(_HERE, "..", "VERSION"), "r") as fh:
            v = fh.read().strip()
        return v or "0.0.0"
    except OSError:
        return "0.0.0"


VERSION = _version()


def _log(msg):
    """One stderr line.  stdout belongs to the protocol (rule 3)."""
    try:
        sys.stderr.write("hermes-mcp " + msg + "\n")
        sys.stderr.flush()
    except Exception:
        pass


# --------------------------------------------------------------------------
# allowlist — read ONCE, at start (rule 4)
# --------------------------------------------------------------------------
def load_allow():
    """Return {'tools': {...}, 'max_results': n}; create the file if absent."""
    allow = {"tools": dict(DEFAULT_ALLOW["tools"]),
             "max_results": DEFAULT_ALLOW["max_results"]}
    raw = None
    try:
        with open(ALLOW_PATH, "r") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        try:
            d = os.path.dirname(ALLOW_PATH)
            if not os.path.isdir(d):
                os.makedirs(d, 0o700)
            # 0600 from birth: opened with the mode, not chmod'd afterwards
            fd = os.open(ALLOW_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(DEFAULT_ALLOW, fh, indent=2, sort_keys=True)
                fh.write("\n")
            _log("allowlist created %s (defaults, messages off)" % ALLOW_PATH)
        except OSError as e:
            _log("allowlist not writable (%s) — using defaults" % type(e).__name__)
        return allow
    except (OSError, ValueError) as e:
        # fail SAFE, never open: a corrupt file yields the defaults
        _log("allowlist unreadable (%s) — using defaults" % type(e).__name__)
        return allow

    # 1.1.5 review fix: the create path opens this 0600, but a file that
    # already existed — restored from a backup, copied in, written by an
    # editor with a loose umask — kept whatever mode it arrived with.  This
    # file is the switch deciding whether this process may read the user's
    # messages at all, so a group- or world-WRITABLE copy is a privilege
    # escalation into the MCP surface.  chmod unconditionally on every
    # successful read: it is a no-op on an already-0600 file, costs one
    # syscall per process start, and needs no stat to decide.
    try:
        os.chmod(ALLOW_PATH, 0o600)
    except OSError as e:
        _log("allowlist chmod 0600 failed (%s: %s) — %s may be readable by "
             "others" % (type(e).__name__, e, ALLOW_PATH))

    if not isinstance(raw, dict):
        _log("allowlist is not an object — using defaults")
        return allow
    tools = raw.get("tools")
    if isinstance(tools, dict):
        for name in allow["tools"]:          # unknown keys are ignored, never added
            if name in tools:
                allow["tools"][name] = bool(tools[name])
    try:
        n = int(raw.get("max_results", DEFAULT_ALLOW["max_results"]))
        allow["max_results"] = max(1, min(MAX_RESULTS_CAP, n))
    except (TypeError, ValueError):
        pass
    return allow


ALLOW = load_allow()


def _enabled(name):
    return bool(ALLOW["tools"].get(name))


def _limit(n):
    """Clamp a requested result count to the allowlist ceiling."""
    cap = ALLOW["max_results"]
    try:
        n = int(n)
    except (TypeError, ValueError):
        return cap
    return max(1, min(cap, n))


# --------------------------------------------------------------------------
# the dashboard, over loopback HTTP (rules 1 + 2)
# --------------------------------------------------------------------------
class ToolError(Exception):
    """Anything the caller should see as isError:true with a useful line."""


_DOWN_HINT = (
    "The Hermes dashboard is not answering on %s.\n"
    "It normally runs as a launchd agent; start it with:\n"
    "  launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard\n"
    "and check ~/.hermes/logs/dashboard.log." % DASH_URL)


def dash_get(path, params=None, timeout=HTTP_TIMEOUT):
    """GET <dashboard><path> and return the decoded JSON body.

    Sends no Origin header (rule 2) and never forwards a non-JSON body
    (rule 5) — an HTML error page becomes a one-line error, not content.
    """
    url = DASH_URL + path
    if params:
        pairs = [(k, v) for k, v in params.items() if v not in (None, "")]
        if pairs:
            url += "?" + urllib.parse.urlencode(pairs)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "hermes-mcp/" + VERSION)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            body = resp.read(MAX_BODY)
    except urllib.error.HTTPError as e:
        try:
            e.read()
        except Exception:
            pass
        if e.code == 403:
            raise ToolError(
                "The dashboard refused the request (403 — same-origin guard). "
                "Set HERMES_MCP_DASHBOARD to a host in ALLOWED_HOSTS "
                "(127.0.0.1, localhost or [::1]).")
        if e.code == 404:
            raise ToolError(
                "The dashboard has no %s route (HTTP 404). It is probably an "
                "older build — restart it so the newer aux modules load." % path)
        raise ToolError("The dashboard answered HTTP %d for %s." % (e.code, path))
    except urllib.error.URLError as e:
        raise ToolError(_DOWN_HINT + "\n(%s)" % (getattr(e, "reason", None) or e,))
    except (OSError, ValueError) as e:
        raise ToolError(_DOWN_HINT + "\n(%s)" % type(e).__name__)

    if "json" not in ctype:
        raise ToolError(
            "The dashboard answered %s for %s instead of JSON (%d bytes); the "
            "body was discarded." % (ctype or "an unknown content type", path, len(body)))
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise ToolError("The dashboard's %s answer was not valid JSON." % path)


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------
def _when(ts, fmt="%Y-%m-%d %H:%M"):
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime(fmt)
    except (OverflowError, OSError, ValueError):
        return ""


def _counts(d):
    if not isinstance(d, dict):
        return ""
    parts = ["%s %d" % (k, v) for k, v in sorted(d.items()) if isinstance(v, int) and v]
    return " · ".join(parts)


def _need_q(args, key="q"):
    q = args.get(key)
    q = q.strip() if isinstance(q, str) else ""
    if not q:
        raise ToolError("`%s` is required and must not be empty." % key)
    return q[:200]              # /api/search caps the query at 200 chars


def _search(q, source, limit):
    """/api/search, formatted.  Shared by every *_search tool."""
    d = dash_get("/api/search", {"q": q, "source": source, "limit": limit})
    if not isinstance(d, dict) or not d.get("ok"):
        raise ToolError("Search failed: %s" % _plain(
            (d or {}).get("error") or "the index returned no answer", 200))
    rows = d.get("results") or []
    # Message rows are gated by the messages_search flag even on an unfiltered
    # search — otherwise turning that tool off would hide the tool and leak the
    # content through the general one.
    if not _enabled("messages_search"):
        rows = [r for r in rows if (r or {}).get("source") != "message"]
    head = "%d result%s for %r" % (len(rows), "" if len(rows) == 1 else "s", q)
    counts = _counts(d.get("sources"))
    if counts and not source:
        head += "  (matches by source: %s)" % counts
    if not rows:
        return head + "\nNothing in the local index matched."
    out = [head, ""]
    for i, r in enumerate(rows, 1):
        r = r if isinstance(r, dict) else {}
        src = _plain(r.get("source"), 20) or "?"
        line = "%d. [%s] %s" % (i, src, _plain(r.get("title"), 120) or "(untitled)")
        when = _when(r.get("ts"))
        if when:
            line += "  · " + when
        out.append(line)
        snip = _plain(r.get("snippet"), 300)
        if snip:
            out.append("   " + snip)
        ref = _plain(r.get("ref"), 100)
        if ref and src == "chat":
            out.append("   session: %s   (open with chat_get)" % ref)
        elif ref and src == "watchtower":
            out.append("   " + ref)
    return "\n".join(out)


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------
def t_hermes_search(args):
    src = args.get("source")
    src = src.strip().lower() if isinstance(src, str) else ""
    known = ("chat", "note", "message", "calendar", "watchtower")
    if src and src not in known:
        raise ToolError("Unknown source %r. Use one of: %s." % (src, ", ".join(known)))
    if src == "message" and not _enabled("messages_search"):
        raise ToolError(
            "Message search is off in %s (messages_search: false). "
            "The owner turns it on there; it is never negotiated at runtime." % ALLOW_PATH)
    return _search(_need_q(args), src, _limit(args.get("limit")))


def t_notes_search(args):
    return _search(_need_q(args), "note", _limit(args.get("limit")))


def t_messages_search(args):
    return _search(_need_q(args), "message", _limit(args.get("limit")))


def t_calendar_search(args):
    return _search(_need_q(args), "calendar", _limit(args.get("limit")))


_CAL_LOOKBACK_MIN = 60          # an event that started 20 min ago still matters
_CAL_MAX_HOURS = 168            # the source is icalBuddy's `eventsToday+7`


def _cal_start(date_s, time_s):
    """('2026-09-07', '9:30 AM') -> datetime, or None."""
    try:
        d = datetime.datetime.strptime(date_s, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    t = (time_s or "").strip()
    if not t:
        return d
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            p = datetime.datetime.strptime(t.upper(), fmt)
            return d.replace(hour=p.hour, minute=p.minute)
        except ValueError:
            continue
    return d


def t_calendar_next(args):
    hours = args.get("hours", 24)
    try:
        hours = float(hours)
    except (TypeError, ValueError):
        hours = 24.0
    hours = max(1.0, min(float(_CAL_MAX_HOURS), hours))

    d = dash_get("/api/expand", {"id": "today"}, timeout=CAL_TIMEOUT)
    if not isinstance(d, dict):
        raise ToolError("The calendar route returned an unexpected shape.")
    if not d.get("available"):
        raise ToolError("Calendar unavailable: %s" % _plain(
            d.get("reason") or d.get("error") or "no reason given", 300))

    now = datetime.datetime.now()
    floor = now - datetime.timedelta(minutes=_CAL_LOOKBACK_MIN)
    horizon = now + datetime.timedelta(hours=hours)
    rows = []
    for day in (d.get("days") or []):
        day = day if isinstance(day, dict) else {}
        date_s = str(day.get("date") or "")
        for ev in (day.get("events") or []):
            ev = ev if isinstance(ev, dict) else {}
            title = _plain(ev.get("title"), 140)
            if not title:
                continue
            if ev.get("all_day") or not ev.get("time"):
                # an all-day event belongs to a date, not an instant
                try:
                    dd = datetime.datetime.strptime(date_s, "%Y-%m-%d").date()
                except (TypeError, ValueError):
                    continue
                if now.date() <= dd <= horizon.date():
                    rows.append((datetime.datetime.combine(
                        dd, datetime.time(0, 0)), True, date_s, "", title))
                continue
            start = _cal_start(date_s, ev.get("time"))
            if start is None or not (floor <= start <= horizon):
                continue
            end = _plain(ev.get("end"), 20)
            rows.append((start, False, date_s, _plain(ev.get("time"), 20) +
                         (("–" + end) if end else ""), title))
    rows.sort(key=lambda r: (r[0], r[4]))

    label = ("%g hours" % hours) if hours != 24 else "24 hours"
    if not rows:
        return "Nothing on the calendar in the next %s." % label
    out = ["%d event%s in the next %s:" % (len(rows), "" if len(rows) == 1 else "s", label), ""]
    for start, all_day, date_s, tspan, title in rows:
        stamp = start.strftime("%a %b %-d") if hasattr(start, "strftime") else date_s
        when = "all day" if all_day else tspan
        started = "  (already started)" if (not all_day and start < now) else ""
        out.append("- %s · %s · %s%s" % (stamp, when, title, started))
    return "\n".join(out)


def t_chats_search(args):
    q = _need_q(args)
    limit = _limit(args.get("limit"))
    rows = dash_get("/api/sessions/search", {"q": q})
    if not isinstance(rows, list):
        raise ToolError("Conversation search returned an unexpected shape.")
    rows = rows[:limit]
    head = "%d conversation%s matching %r" % (len(rows), "" if len(rows) == 1 else "s", q)
    if not rows:
        return head + "\nNo stored conversation contains that."
    out = [head, ""]
    for i, r in enumerate(rows, 1):
        r = r if isinstance(r, dict) else {}
        sid = _plain(r.get("session"), 80)
        line = "%d. %s" % (i, _plain(r.get("title"), 120) or sid or "(untitled)")
        when = _when(r.get("ts"), "%Y-%m-%d")
        hits = r.get("hits")
        tail = [x for x in (when, ("%s hits" % hits) if hits else "") if x]
        if tail:
            line += "  · " + " · ".join(tail)
        out.append(line)
        out.append("   session: %s" % sid)
        snip = _plain(r.get("snippet"), 300)
        if snip:
            out.append("   " + snip)
    return "\n".join(out)


def t_chat_get(args):
    sid = args.get("session")
    sid = sid.strip() if isinstance(sid, str) else ""
    if not sid or not SESSION_RE.match(sid) or sid == PREWARM_SESSION:
        raise ToolError(
            "`session` must be a conversation id from chats_search or "
            "hermes_search (letters, digits, . _ -).")
    last_n = args.get("last_n", 20)
    try:
        last_n = int(last_n)
    except (TypeError, ValueError):
        last_n = 20
    last_n = max(1, min(100, last_n))

    d = dash_get("/api/history", {"session": sid})
    if not isinstance(d, dict):
        raise ToolError("The history route returned an unexpected shape.")
    msgs = d.get("messages") or []
    turns = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if (m.get("role") or "") not in _CV_ROLES:
            continue
        if any(m.get(k) for k in _CV_META_KEYS):
            continue              # tool lines, approvals, status rows
        text = m.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        turns.append(m)
    if not turns:
        return ("No conversation is stored under %s (or it holds only tool "
                "and approval rows, which this server never returns)." % sid)
    shown = turns[-last_n:]
    title = _plain(d.get("title"), 120) or sid
    out = ["%s  (%s)" % (title, sid),
           "%d of %d turns; tool, approval and status rows are omitted." %
           (len(shown), len(turns)), ""]
    for m in shown:
        who = "You" if (m.get("role") == "user") else "Hermes"
        if m.get("deep"):
            deep = m["deep"] if isinstance(m.get("deep"), dict) else {}
            who = "Claude (%s)" % (_plain(deep.get("model"), 40) or "escalated")
        stamp = _when(m.get("ts"))
        out.append(("%s%s:" % (who, ("  · " + stamp) if stamp else "")))
        out.append(_plain(m.get("text"), 2000))
        out.append("")
    return "\n".join(out).rstrip()


def t_needs_you(args):
    d = dash_get("/api/needsyou", {"mark": "0"})   # read-only: never a sighting
    if not isinstance(d, dict):
        raise ToolError("The needs-you route returned an unexpected shape.")
    if d.get("building"):
        return ("The needs-you inbox is still building on a background thread "
                "(a cold dashboard answers this way). Ask again in a few seconds.")
    if not d.get("ok"):
        raise ToolError("Needs-you failed: %s" % _plain(d.get("error"), 200))

    def block(name, items, cap):
        items = [i for i in (items or []) if isinstance(i, dict)][:cap]
        if not items:
            return []
        lines = ["%s (%d):" % (name, len(items))]
        for it in items:
            who = _plain(it.get("sender") or it.get("title"), 80)
            what = _plain(it.get("summary") or it.get("title"), 200)
            lines.append("- [%s] %s — %s" % (_plain(it.get("source"), 20), who, what))
            why = _plain(it.get("reason"), 200)
            if why:
                conf = it.get("confidence")
                conf = ("  (confidence %.2f)" % conf) if isinstance(conf, (int, float)) else ""
                lines.append("    why: %s%s" % (why, conf))
            dl = _when(it.get("deadline_ts"))
            if dl:
                lines.append("    deadline: " + dl)
        lines.append("")
        return lines

    cap = ALLOW["max_results"]
    out = ["Needs you — now %d · today %d · later %d · never %d" % (
        len(d.get("now") or []), d.get("today_count") or len(d.get("today") or []),
        d.get("later_count") or len(d.get("later") or []), d.get("never_count") or 0), ""]
    out += block("Now", d.get("now"), cap)
    out += block("Today", d.get("today"), cap)
    out += block("Later", d.get("later"), cap)
    if len(out) <= 2:
        out.append("Nothing needs you right now.")
    srcs = d.get("sources") if isinstance(d.get("sources"), dict) else {}
    if srcs:
        out.append("sources: " + ", ".join(
            "%s %s" % (k, _plain(v, 12)) for k, v in sorted(srcs.items())))
    m = d.get("metrics") if isinstance(d.get("metrics"), dict) else {}
    if isinstance(m.get("now_precision"), (int, float)):
        out.append("now precision (7d): %d%%" % round(100 * m["now_precision"]))
    out.append("This server is read-only: snooze, done, open and draft are "
               "actions in the dashboard, not tools here.")
    return "\n".join(out)


def t_memory_get(args):
    d = dash_get("/api/capabilities")
    if not isinstance(d, dict):
        raise ToolError("The capabilities route returned an unexpected shape.")
    mem = d.get("memory") if isinstance(d.get("memory"), dict) else {}
    facts = [f for f in (mem.get("facts") or []) if isinstance(f, str) and f.strip()]
    # USER.md is section-delimited; a lone marker is not a fact
    facts = [f for f in facts if f.strip() not in ("§", "-", "*")]
    if not facts:
        return ("Hermes has no remembered facts yet (~/.hermes/memories/USER.md "
                "is empty or unreadable).")
    when = _when(mem.get("updated"), "%Y-%m-%d")
    out = ["%d remembered fact%s%s:" % (
        len(facts), "" if len(facts) == 1 else "s",
        ("  · updated " + when) if when else ""), ""]
    for f in facts:
        out.append("- " + _plain(f, 600))
    return "\n".join(out)


# --------------------------------------------------------------------------
# tool table — name, schema, handler.  One tool per source (§4b): never one
# blob tool, so the allowlist can switch a single source off.
# --------------------------------------------------------------------------
def _schema(props, required=()):
    return {"type": "object", "properties": props,
            "required": list(required), "additionalProperties": False}


_Q = {"type": "string", "description": "Search words. Plain text; the index "
      "tokenises it, so operators like OR or NEAR are searched as words.",
      "maxLength": 200}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS_CAP,
          "description": "Maximum results (capped by the launch-time allowlist)."}

TOOLS = [
    {
        "name": "hermes_search",
        "title": "Search everything Hermes indexes",
        "description":
            "Full-text search across every local source Hermes indexes: chats, "
            "notes, calendar events, saved news and (only if enabled) messages. "
            "Read-only, local, no network. Chat hits carry a session id you can "
            "pass to chat_get.",
        "inputSchema": _schema({
            "q": _Q,
            "source": {"type": "string",
                       "enum": ["chat", "note", "message", "calendar", "watchtower"],
                       "description": "Restrict to one source. Omit for all."},
            "limit": _LIMIT,
        }, ["q"]),
        "handler": t_hermes_search,
    },
    {
        "name": "calendar_next",
        "title": "Upcoming calendar events",
        "description":
            "The user's macOS Calendar events in the next N hours (default 24, "
            "up to 7 days — the underlying read covers a week). All-day events "
            "are included; an event already in progress is marked.",
        "inputSchema": _schema({
            "hours": {"type": "number", "minimum": 1, "maximum": _CAL_MAX_HOURS,
                      "description": "Look-ahead window in hours (default 24)."},
        }),
        "handler": t_calendar_next,
    },
    {
        "name": "calendar_search",
        "title": "Search calendar events",
        "description":
            "Search calendar event titles in the indexed window (about ±30 days "
            "around today). For 'what is next', use calendar_next instead.",
        "inputSchema": _schema({"q": _Q, "limit": _LIMIT}, ["q"]),
        "handler": t_calendar_search,
    },
    {
        "name": "notes_search",
        "title": "Search notes",
        "description": "Search the user's Hermes notes (the dashboard Scratchpad).",
        "inputSchema": _schema({"q": _Q, "limit": _LIMIT}, ["q"]),
        "handler": t_notes_search,
    },
    {
        "name": "chats_search",
        "title": "Search past conversations",
        "description":
            "Find past Hermes conversations containing a phrase. Returns a "
            "session id per hit for chat_get. Tool and approval rows are never "
            "searched or returned.",
        "inputSchema": _schema({"q": _Q, "limit": _LIMIT}, ["q"]),
        "handler": t_chats_search,
    },
    {
        "name": "chat_get",
        "title": "Read one conversation",
        "description":
            "The last N turns of one stored Hermes conversation, user and "
            "assistant text only — tool calls, approval prompts and status rows "
            "are stripped, and secrets and ~/.hermes paths are redacted.",
        "inputSchema": _schema({
            "session": {"type": "string", "maxLength": 80,
                        "description": "Session id from chats_search or hermes_search."},
            "last_n": {"type": "integer", "minimum": 1, "maximum": 100,
                       "description": "How many recent turns (default 20)."},
        }, ["session"]),
        "handler": t_chat_get,
    },
    {
        "name": "needs_you",
        "title": "What needs the user's attention",
        "description":
            "Hermes's triaged attention inbox: the now / today / later buckets "
            "with the one-line reason each item was placed there. Read-only — "
            "snoozing, drafting and dismissing happen in the dashboard.",
        "inputSchema": _schema({}),
        "handler": t_needs_you,
    },
    {
        "name": "memory_get",
        "title": "Facts Hermes remembers about the user",
        "description":
            "The durable facts Hermes has stored about its owner "
            "(~/.hermes/memories/USER.md), as the dashboard reports them.",
        "inputSchema": _schema({}),
        "handler": t_memory_get,
    },
    {
        "name": "messages_search",
        "title": "Search iMessage / Message Center rows",
        "description":
            "Search the messages Hermes has ingested from the Message Center. "
            "Off by default — the owner enables it in the allowlist.",
        "inputSchema": _schema({"q": _Q, "limit": _LIMIT}, ["q"]),
        "handler": t_messages_search,
    },
]

# A disabled tool is not listed and not callable (rule 4).
LIVE_TOOLS = [t for t in TOOLS if _enabled(t["name"])]
BY_NAME = dict((t["name"], t) for t in LIVE_TOOLS)


def tool_descriptors():
    return [{"name": t["name"], "title": t["title"],
             "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in LIVE_TOOLS]


# --------------------------------------------------------------------------
# JSON-RPC 2.0 over newline-delimited stdio (rule 3)
# --------------------------------------------------------------------------
E_PARSE, E_REQUEST, E_METHOD, E_PARAMS, E_INTERNAL = (
    -32700, -32600, -32601, -32602, -32603)

_OUT = getattr(sys.stdout, "buffer", sys.stdout)


def _send(obj):
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    # json.dumps escapes newlines inside strings, so `line` can never contain
    # one — the spec requires that of every stdio message.
    data = line.encode("utf-8") + b"\n"
    try:
        _OUT.write(data)
        _OUT.flush()
    except (BrokenPipeError, ValueError):
        raise SystemExit(0)


def _result(rid, result):
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _error(rid, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _send({"jsonrpc": "2.0", "id": rid, "error": err})


def _text_result(text, is_error=False):
    return {"content": [{"type": "text", "text": _bound(text)}], "isError": bool(is_error)}


def do_initialize(params):
    want = params.get("protocolVersion")
    version = want if want in PROTOCOL_VERSIONS else LATEST_PROTOCOL
    client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
    _log("initialize client=%s/%s protocol=%s->%s tools=%d" % (
        str(client.get("name"))[:40], str(client.get("version"))[:20],
        str(want)[:20], version, len(LIVE_TOOLS)))
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": VERSION},
        "instructions":
            "Hermes Assistant's personal context, read-only over loopback. "
            "Use these tools when the user asks about their own calendar, "
            "notes, past Hermes conversations, what needs their attention, or "
            "what Hermes remembers about them. Nothing here writes, sends or "
            "wakes a model, and the visible tool set is fixed at launch by "
            "~/.hermes/mcp-allow.json.",
    }


def do_tools_call(params):
    """Returns a tools/call result dict, or raises _Protocol for a JSON-RPC error."""
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise _Protocol(E_PARAMS, "tools/call requires a tool name")
    spec = BY_NAME.get(name)
    if spec is None:
        # Not listed => not callable.  A disabled tool is indistinguishable
        # from an unknown one on purpose.
        raise _Protocol(E_PARAMS, "Unknown tool: " + name[:64])
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise _Protocol(E_PARAMS, "tools/call arguments must be an object")

    t0 = time.time()
    try:
        text = spec["handler"](args)
        ok, is_err = True, False
    except ToolError as e:
        text, ok, is_err = str(e), False, True
    except Exception as e:                       # never let one tool kill the server
        text = "Hermes MCP hit an internal error (%s) running %s." % (type(e).__name__, name)
        ok, is_err = False, True
        _log("tool=%s internal=%r" % (name, e))
    ms = int((time.time() - t0) * 1000)
    _log("tool=%s ms=%d ok=%d" % (name, ms, 1 if ok else 0))
    return _text_result(text, is_err)


class _Protocol(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def handle(msg):
    """One decoded JSON-RPC object -> None (notifications) or a reply is sent."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        _error(None, E_REQUEST, "Invalid Request: expected a JSON-RPC 2.0 object")
        return
    method = msg.get("method")
    rid = msg.get("id")
    is_notification = "id" not in msg
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}

    if not isinstance(method, str):
        if not is_notification:
            _error(rid, E_REQUEST, "Invalid Request: missing method")
        return

    try:
        if method == "initialize":
            result = do_initialize(params)
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_descriptors()}   # one page, no nextCursor
        elif method == "tools/call":
            result = do_tools_call(params)
        elif method.startswith("notifications/"):
            return                                    # initialized, cancelled, …
        else:
            raise _Protocol(E_METHOD, "Method not found: " + method[:64])
    except _Protocol as e:
        if not is_notification:
            _error(rid, e.code, e.message)
        return
    except Exception as e:                            # pragma: no cover
        _log("handler error method=%s %r" % (method, e))
        if not is_notification:
            _error(rid, E_INTERNAL, "Internal error: " + type(e).__name__)
        return

    if not is_notification:
        _result(rid, result)


def main():
    _log("start version=%s dashboard=%s tools=%s allow=%s" % (
        VERSION, DASH_URL, ",".join(sorted(BY_NAME)) or "(none)", ALLOW_PATH))
    stdin = getattr(sys.stdin, "buffer", sys.stdin)
    while True:
        try:
            raw = stdin.readline()
        except KeyboardInterrupt:
            break
        if not raw:
            break                                     # client closed stdin
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            # A malformed line is answered and survived, never fatal.
            _log("parse error (%d bytes)" % len(line))
            _error(None, E_PARSE, "Parse error: line was not valid JSON")
            continue
        if isinstance(msg, list):
            # JSON-RPC batches were removed in MCP 2025-06-18.
            _error(None, E_REQUEST, "Batch requests are not supported")
            continue
        handle(msg)
    _log("stdin closed — exiting")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
