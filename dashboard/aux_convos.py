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
_CV_SECRET_RE = re.compile(
    r"\b(serve_sid|serve_key|session_token|token|api[_-]?key|apikey|"
    r"secret|password|passwd|authorization)\b\s*[:=]\s*"
    r"[\"']?(?:Bearer\s+)?([A-Za-z0-9_\-./+=]{6,})[\"']?", re.I)
_CV_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9_\-.=]{8,}", re.I)
_CV_HERMES_PATH_RE = re.compile(
    r"(?:~|/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+)/\.hermes"
    r"(?:/[^\s`'\"),;]*)?")


def _cv_redact(text):
    text = _CV_HERMES_PATH_RE.sub("[redacted path]", text)
    # key=value FIRST: its value pattern swallows a "Bearer <tok>" whole, so
    # "Authorization: Bearer hx_..." collapses to one "[redacted]" instead of
    # the standalone rule leaving a bare "Bearer" for this one to redact again
    text = _CV_SECRET_RE.sub(lambda m: m.group(1) + ": [redacted]", text)
    return _CV_BEARER_RE.sub("Bearer [redacted]", text)


# --- shared helpers ---------------------------------------------------------
def _cv_session_files():
    """(sid, path) for every REAL conversation — prewarm/bad ids skipped."""
    try:
        names = os.listdir(CHATS)
    except OSError:
        return []
    out = []
    for fn in names:
        if not fn.endswith(".json"):
            continue
        sid = fn[:-5]
        if sid == PREWARM_SESSION or not SESSION_RE.match(sid):
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
    sid = (b.get("session") or "").strip()
    if not sid or not SESSION_RE.match(sid) or sid == PREWARM_SESSION:
        return {"ok": False, "error": "bad session"}, 400
    if not os.path.exists(chat_path(sid)):
        # the "(new conversation)" pseudo-row has no file yet: nothing to pin
        return {"ok": False, "error": "unknown session"}, 404
    if "pinned" not in b and "title" not in b:
        return {"ok": False, "error": "nothing to set"}, 400
    chat = load_chat(sid)
    if "pinned" in b:
        chat["pinned"] = bool(b.get("pinned"))
    if "title" in b:
        # an empty title clears the rename — list_sessions() then falls back
        # to the first-message excerpt again
        chat["title"] = _cv_clean_title(b.get("title"))
    save_chat(sid, chat)
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


def _cv_markdown(sid, chat, when):
    msgs = chat.get("messages") or []
    title = chat.get("title") or _cv_auto_title(msgs) or sid
    out = ["# " + _cv_clean_title(title), "",
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
    if not sid or not SESSION_RE.match(sid) or sid == PREWARM_SESSION:
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
        _cv_slug(chat.get("title") or _cv_auto_title(msgs) or sid),
        when.strftime("%Y-%m-%d"))
    return RawResponse(body, "text/markdown; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="%s"' % fname})


register_get("/api/sessions/search", _cv_search)
register_post("/api/sessions/meta", _cv_meta)
register_get("/api/sessions/export", _cv_export)
