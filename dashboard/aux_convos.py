# ==========================================================================
# aux_convos.py — conversation management (backlog #13)
#
# The chat sidebar could only list and delete.  Everyone coming from LM
# Studio / Msty / BoltAI expects three more things, so this module adds them
# without touching the chat store's shape beyond two optional fields:
#
#   GET  /api/sessions/search?q=   -> [{session,title,ts,snippet,mark_*,hits}]
#   POST /api/sessions/meta        -> {session, pinned?, title?}  (persisted
#                                     on the chat JSON: `pinned`, `title`)
#   GET  /api/sessions/export?session=  -> text/markdown + Content-Disposition
#
# Everything reads/writes the SAME ~/.hermes/dashboard/chats/<sid>.json the
# rest of the hub uses (load_chat/save_chat), so a rename or a pin survives a
# restart and shows up in list_sessions() (server.py) with no extra store.
#
# Two invariants inherited from the chat store:
#   * PREWARM_SESSION (`__prewarm__`) is a reserved key, never a conversation
#     — skipped by search, refused by meta and export, exactly like
#     list_sessions()/api/history already do.
#   * SESSION_RE validates every session id that reaches the filesystem.
#
# The search snippet is PLAIN TEXT plus a (mark_start, mark_len) offset pair:
# the server never emits markup, the client escapes the three slices and
# wraps the middle one in its own <span class="cv-mark">.  That keeps chat
# content — which is arbitrary user/model text — off the innerHTML path.
#
# AUX MODULE GOTCHA: aux files exec into server.py's globals, so
# `from datetime import datetime` would rebind the global `datetime` module
# name to the class and break every other caller.  Private alias only.
# ==========================================================================
import datetime as _cv_datetime

_CV_MAX_RESULTS = 50      # cap: the sidebar is a list, not a search engine
_CV_SNIPPET_PAD = 60      # +/- chars of context around the first hit
_CV_TITLE_MAX = 80        # a sidebar row, not a document

# roles the transcript uses for real conversation turns; anything else
# (tool lines, approval prompts, status rows a future build might store) is
# metadata and is skipped by search AND by the export.
_CV_ROLES = ("user", "bot", "assistant")
_CV_META_KEYS = ("tool", "tool_name", "approval", "status", "kind")

_CV_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_CV_SLUG_RE = re.compile(r"[^a-z0-9]+")

# --- secret scrubbing for the export ---------------------------------------
# The transcript can quote whatever the agent printed: the serve token, a
# Bearer header, a path under ~/.hermes (where the tokens live).  An export is
# a file the user mails to themselves, so scrub before it leaves the process.
# The scrubber below is MIRRORED VERBATIM in dashboard/hermes_mcp.py (the MCP
# server re-implements it instead of importing it — importing would drag
# server.py's globals into that process and break its rule 1).

# ===== BEGIN MIRRORED SECRET BLOCK — KEEP BYTE-IDENTICAL (1.1.5, B21) =====
# Everything from this line to the END marker is the WHOLE scrubber, and it
# exists twice: dashboard/aux_convos.py (the export; aux_trace.py, doctor.py
# and aux_memlayer.py resolve it from there by name) and dashboard/hermes_mcp.py
# (every MCP result).  Copied, not imported — the MCP server must not pull
# server.py's globals into its process.  Change one, change the other; the
# unit tier compares the bytes between the two markers.
#
# LABELED secrets: a key that names a credential, `:` or `=`, then the value.
# B21 (2026-10-08 audit) widened it four ways, each for a reproduced leak:
#   * a JSON-quoted key, `"api_key": "sk-…"` — the closing quote sat between
#     the key and the colon, so nothing matched;
#   * a name that only ENDS in a credential word — OPENAI_API_KEY=,
#     GITHUB_TOKEN=, client_secret:, authToken:, aws_secret_access_key = .
#     The keyword is deliberately NOT anchored at a word start (an unanchored
#     fixed keyword also keeps this linear; a lazy identifier prefix in front
#     would be quadratic on a long dotted run), while the \b AFTER it is what
#     keeps max_tokens=4096 and prompt_tokens: 19127 out;
#   * `Authorization: Basic …` and `Token …`, not only Bearer;
#   * a value with punctuation in it — `hunter2!x` used to leak its tail.
# The value must sit on the same line ([ \t], not \s) and cannot start with
# `[`, so an already-redacted value is never matched twice.  Group 1 (key and
# separator) is kept, so JSON stays JSON: `"password": "[redacted]"` — and so
# does JSON quoted inside a string (`{\"api_key\": \"sk-…\"}`, the shape an
# echoed tool call has): a quote may carry one backslash, and the value stops
# at a backslash so the closing `\"` survives.
_CV_SECRET_RE = re.compile(
    r"((?:serve_sid|serve_key|session_token|token|api[_-]?key|apikey|"
    r"secret|password|passwd|passphrase|passcode|authorization)"
    r"(?:[_.-]?(?:access|key|secret|token|value|base)){0,3}\b"
    r"(?:\\?[\"'])?[ \t]*[:=][ \t]*(?:\\?[\"'])?)"
    r"(?:(?:Bearer|Basic|Token)[ \t]+)?"
    r"([^\s\"'`,;(){}\[\]<>\\]{6,})", re.I)
_CV_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9_\-.=]{8,}", re.I)
# PROSE: "my password is hunter22", "the API key was sk-abc123".  Narrower on
# purpose — the value must carry a digit, or a symbol with more of the word
# after it, so "the token is expired", "the secret is out" and "the API key
# was invalid!" survive.  A passphrase of plain words is missed; a redactor that
# ate ordinary sentences would get switched off.
_CV_PROSE_SECRET_RE = re.compile(
    r"(\b(?:password|passwd|passphrase|passcode|api[ _-]?key|secret[ _-]?key|"
    r"(?:access|auth|bot|api)[ _-]?token|token|secret)"
    r"[ \t]+(?:is|was)[ \t]*:?[ \t]+[\"'`]?)"
    r"(?=[^\s\"'`,;(){}\[\]<>]*"
    r"(?:[0-9]|[!@#$%^*~][^\s\"'`,;(){}\[\]<>]))"
    r"([^\s\"'`,;(){}\[\]<>]{6,})", re.I)
_CV_HERMES_PATH_RE = re.compile(
    r"(?:~|/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+)/\.hermes"
    r"(?:/[^\s`'\"),;]*)?")

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
    # E82 (2026-11-09 audit): the shapes this app itself handles that used to
    # pass unlabeled — Google OAuth access/refresh tokens and client secret,
    # Hugging Face, Stripe (secret + restricted, live + test), npm, GitLab.
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"),                   # Google access
    re.compile(r"\b1//0[A-Za-z0-9_-]{20,}"),                      # Google refresh
    re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}"),                   # Google client
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),                         # Hugging Face
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),        # Stripe
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}"),                        # npm
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),                    # GitLab PAT
    # a password in a web address, scheme://user:password@host — only the
    # user:password part goes, so "postgres://[redacted]@db:5432" still says
    # which database.  The lookbehind pins every match to just after "://",
    # which keeps it linear; a "user@host" with no password is left alone.
    re.compile(r"(?<=://)[^\s/:@\[\]]+:[^\s/@]+(?=@)"),
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


def _cv_redact(text):
    """Path, key=value, Bearer, prose, then the unlabeled raw shapes."""
    text = _CV_HERMES_PATH_RE.sub("[redacted path]", text)
    # key=value FIRST: its value pattern swallows a "Bearer <tok>" whole, so
    # "Authorization: Bearer hx_..." collapses to one "[redacted]" instead of
    # the standalone rule leaving a bare "Bearer" for this one to redact again
    text = _CV_SECRET_RE.sub(lambda m: m.group(1) + "[redacted]", text)
    text = _CV_BEARER_RE.sub("Bearer [redacted]", text)
    text = _CV_PROSE_SECRET_RE.sub(lambda m: m.group(1) + "[redacted]", text)
    return _cv_redact_raw(text)          # unlabeled shapes last (1.1.5)
# ===== END MIRRORED SECRET BLOCK ==========================================


# --- shared helpers ---------------------------------------------------------
def _cv_hidden():
    """The reserved conversation ids no route may list, read, export, rename
    or pin: prewarm and the needs-you draft session (third-party text, 2026-10-21
    audit C38). Resolved by name at CALL time, with the literal fallback."""
    return globals().get("HIDDEN_SESSIONS") or (PREWARM_SESSION, "needsyou")


def _cv_session_files():
    """(sid, path) for every REAL conversation — prewarm, the needs-you draft
    session (third-party text, 2026-10-21 audit C38) and bad ids skipped."""
    try:
        names = os.listdir(CHATS)
    except OSError:
        return []
    hidden = globals().get("HIDDEN_SESSIONS") or (PREWARM_SESSION, "needsyou")
    out = []
    for fn in names:
        if not fn.endswith(".json"):
            continue
        sid = fn[:-5]
        if sid.casefold() in hidden or not SESSION_RE.match(sid):   # any case (E69)
            continue
        out.append((sid, os.path.join(CHATS, fn)))
    return out


def _cv_turn_text(m):
    """The plain text of a real conversation turn, '' for metadata rows."""
    if not isinstance(m, dict):
        return ""
    if (m.get("role") or "") not in _CV_ROLES:
        return ""
    for k in _CV_META_KEYS:
        if m.get(k):
            return ""
    t = m.get("text")
    return t if isinstance(t, str) else ""


def _cv_auto_title(msgs):
    for m in msgs:
        t = _cv_turn_text(m)
        if t.strip():
            return " ".join(t.split())[:48]
    return ""


def _cv_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _cv_clean_title(t):
    """<= 80 chars, no control characters, no runs of whitespace."""
    t = _CV_CTRL_RE.sub(" ", str(t if t is not None else ""))
    return " ".join(t.split())[:_CV_TITLE_MAX].strip()


def _cv_snippet(text, idx, n):
    """+/-60 chars around a hit, whitespace collapsed to one line.

    Returns (snippet, mark_start, mark_len) — offsets into the RETURNED
    string, so the client can escape the three slices and wrap the middle
    one itself.  Collapsing runs of whitespace changes lengths, which is
    exactly why the offsets are computed here and not guessed there.
    """
    lo = max(0, idx - _CV_SNIPPET_PAD)
    hi = min(len(text), idx + n + _CV_SNIPPET_PAD)
    raw_pre, raw_mid, raw_post = text[lo:idx], text[idx:idx + n], text[idx + n:hi]
    pre = " ".join(raw_pre.split())
    mid = " ".join(raw_mid.split())
    post = " ".join(raw_post.split())
    # a collapsed run of whitespace at a boundary must not glue words together
    if pre and raw_pre[-1:].isspace():
        pre += " "
    if post and raw_post[:1].isspace():
        post = " " + post
    head = "…" if lo > 0 else ""
    tail = "…" if hi < len(text) else ""
    return head + pre + mid + post + tail, len(head) + len(pre), len(mid)


# --- GET /api/sessions/search?q= -------------------------------------------
def _cv_search(ctx):
    q = (ctx.q1("q") or "").strip()
    if not q:
        return []
    ql = q.lower()
    nq = len(ql)
    results = []
    for sid, path in _cv_session_files():
        chat = load_chat(sid)
        msgs = chat.get("messages") or []
        if not msgs:
            continue
        hits = 0
        snippet = mark_start = mark_len = None
        for m in msgs:
            t = _cv_turn_text(m)
            if not t:
                continue
            tl = t.lower()
            i = tl.find(ql)
            if i < 0:
                continue
            j = i
            while j >= 0:                    # every occurrence counts as a hit
                hits += 1
                j = tl.find(ql, j + nq)
            if snippet is None:
                snippet, mark_start, mark_len = _cv_snippet(t, i, nq)
        if not hits:
            continue
        results.append({
            "session": sid,
            "title": chat.get("title") or _cv_auto_title(msgs) or sid,
            "ts": _cv_mtime(path),
            "snippet": snippet,
            "mark_start": mark_start,
            "mark_len": mark_len,
            "hits": hits,
        })
    results.sort(key=lambda r: -r["ts"])
    return results[:_CV_MAX_RESULTS]


# --- POST /api/sessions/meta {session, pinned?, title?} ---------------------
def _cv_meta(ctx):
    b = ctx.body if isinstance(ctx.body, dict) else {}
    sid = b.get("session")
    sid = sid.strip() if isinstance(sid, str) else ""     # E123: a non-string is a bad session
    if not sid or not SESSION_RE.match(sid) or sid.casefold() in _cv_hidden():   # C38, E69
        return {"ok": False, "error": "bad session"}, 400
    if not os.path.exists(chat_path(sid)):
        # the "(new conversation)" pseudo-row has no file yet: nothing to pin
        return {"ok": False, "error": "unknown session"}, 404
    if "pinned" not in b and "title" not in b:
        return {"ok": False, "error": "nothing to set"}, 400
    # C59: bool("false") is True — the STRING "false" used to pin it
    if "pinned" in b and not isinstance(b.get("pinned"), bool):
        return {"ok": False, "error": "'pinned' must be a JSON boolean "
                                      "(true or false)"}, 400
    def _apply(chat):
        if "pinned" in b:
            chat["pinned"] = b.get("pinned")
        if "title" in b:
            # an empty title clears the rename — list_sessions() then falls
            # back to the first-message excerpt again
            chat["title"] = _cv_clean_title(b.get("title"))

    # One lock across load+edit+save: a rename used to be a read, a think and
    # a write of the WHOLE conversation, so a reply that arrived in between
    # was dropped along with it (2026-09-10 audit A03).
    chat = save_chat_update(sid, _apply)                      # noqa: F821
    return {"ok": True, "session": sid, "pinned": bool(chat.get("pinned")),
            "title": chat.get("title") or ""}


# --- GET /api/sessions/export?session= -> markdown --------------------------
def _cv_ts(v, fallback=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return fallback


def _cv_slug(t):
    s = _CV_SLUG_RE.sub("-", (t or "").lower()).strip("-")
    return s[:40].strip("-") or "conversation"


def _cv_export_title(sid, chat, msgs):
    """The export's title, scrubbed BEFORE it is cut (B21).

    The title line and the download's filename both left the process raw, so
    a conversation that opened with "my OPENAI_API_KEY=sk-…" carried the key
    in its heading and in the file name on disk.  The auto-title takes only
    the first 48 characters of the first turn, and a secret cut in half no
    longer matches a pattern — so scrub the whole turn first, then shorten.
    """
    t = chat.get("title")
    if not (isinstance(t, str) and t.strip()):
        t = ""
        for m in msgs:
            tt = _cv_turn_text(m)
            if tt.strip():
                t = " ".join(_cv_redact(tt).split())[:48]
                break
    return _cv_clean_title(_cv_redact(t or sid)) or sid


def _cv_markdown(sid, chat, when):
    msgs = chat.get("messages") or []
    out = ["# " + _cv_export_title(sid, chat, msgs), "",
           when.strftime("%B %-d, %Y") if hasattr(when, "strftime") else "", ""]
    for m in msgs:
        t = _cv_turn_text(m)          # tool / approval metadata never exports
        if not t.strip():
            continue
        t = _cv_redact(t)
        if (m.get("role") or "") == "user":
            out += ["**You**", "", t, ""]
        elif m.get("deep"):
            # a Claude escalation answer — quoted, labelled with its model
            deep = m.get("deep") if isinstance(m.get("deep"), dict) else {}
            model = str(deep.get("model") or "claude")
            quoted = "\n".join(("> " + ln) if ln.strip() else ">"
                               for ln in t.split("\n"))
            out += ["> **Claude (" + model + ")**", ">", quoted, ""]
        else:
            out += ["**Hermes**", "", t, ""]
    return "\n".join(out).rstrip() + "\n"


def _cv_export(ctx):
    sid = (ctx.q1("session") or "").strip()
    if not sid or not SESSION_RE.match(sid) or sid.casefold() in _cv_hidden():   # C38, E69
        return {"ok": False, "error": "bad session"}, 400
    path = chat_path(sid)
    if not os.path.exists(path):
        return {"ok": False, "error": "unknown session"}, 404
    chat = load_chat(sid)
    msgs = chat.get("messages") or []
    last = _cv_ts(msgs[-1].get("ts") if msgs else 0, 0.0) or _cv_mtime(path)
    when = _cv_datetime.datetime.fromtimestamp(last or time.time())
    body = _cv_markdown(sid, chat, when)
    fname = "hermes-%s-%s.md" % (
        _cv_slug(_cv_export_title(sid, chat, msgs)),
        when.strftime("%Y-%m-%d"))
    return RawResponse(body, "text/markdown; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="%s"' % fname})


register_get("/api/sessions/search", _cv_search)
register_post("/api/sessions/meta", _cv_meta)
register_get("/api/sessions/export", _cv_export)
