# ==========================================================================
# aux_index.py — unified local index + search (release 1.1.0)
#
# The honest inventory in docs/plans/purpose-and-direction.md §2 says it
# plainly: every context source is wired, but there is NO unified index — the
# agent cannot search across chats, notes, messages, calendar and news in one
# question, and the only search box in the product searches chats alone.
# §4 item 2 and §4b "Unified index" specify the fix, and this is it.
#
#   GET /api/search?q=&source=&limit=  -> ranked rows across every source
#   GET /api/search/status             -> per-source counts + last sweep
#
# STORE — SQLite FTS5 (stdlib sqlite3, no extension, no model, no network) at
# ~/.hermes/dashboard/index.db, 0600 like every other private store here:
#
#   items(id TEXT PRIMARY KEY, source, ts, title, body, ref, meta)
#   items_fts(title, body)   external-content FTS5 over items + 3 triggers
#
# External content means the text is stored ONCE (in `items`) and the FTS
# table keeps only the inverted index, so the db stays about the size of the
# sources themselves.  The triggers are the price of that: every write to
# `items` has to be mirrored into `items_fts`, and a missed 'delete' row
# leaves a phantom hit forever.
#
# SOURCES — one adapter each, and every adapter degrades to "this store is not
# on this Mac" rather than failing the sweep:
#   chat       ~/.hermes/dashboard/chats/*.json, ONE row per conversation
#              (user+bot text only; tool/approval/status rows and the
#              __prewarm__ session are never indexed — same invariants as
#              aux_convos' search, which this generalises)
#   note       notes.json (the Scratchpad) — absent on a fresh install
#   message    messages.json (Message Center; empty until the app has FDA)
#   calendar   icalBuddy, +/-30 days (falls back to the existing
#              macos_calendar() today-only provider, then to nothing)
#   watchtower intel.json items + curated picks
#
# An adapter returns None for "store absent" (NOTHING is pruned for that
# source) and a list for "store present" (rows it did not return are pruned).
# That difference is the whole reason a transiently unreadable intel.json does
# not silently empty the news half of the index.
#
# FRESHNESS — two paths, deliberately:
#   * index_touch(source, id) on the write paths this module can reach without
#     editing them: save_chat() is WRAPPED (the runtime-override pattern
#     aux_shortcuts already uses for access_preamble) and POST /api/notes is
#     re-registered as an aux route.  Touches are COALESCED on a 2s drain
#     thread, so a chat that saves three times in one turn is indexed once and
#     no request ever waits on sqlite.
#   * a sweep at start+60s and every 30 min that upserts changed rows (mtime
#     for file-backed sources, content compare otherwise) and prunes deleted
#     ones.  It holds no lock a request needs; WAL means searches read
#     straight through it.
#
# SNIPPETS — the server never emits markup.  FTS5's own snippet() is not used
# for exactly that reason: a result is PLAIN text plus (mark_start, mark_len)
# offsets into it, and the client escapes the three slices and wraps the
# middle one itself.  Same contract the chat search already ships, so the
# existing cvHi() renderer works unchanged.
#
# QUERY SAFETY — FTS5's MATCH grammar is a language: a bare user string can
# carry OR / NEAR / NOT / column filters / unbalanced quotes, which at best
# raise sqlite3.OperationalError on every other keystroke and at worst return
# rows the user did not ask for.  _ix_match() throws the grammar away: it
# keeps \w+ runs only, wraps each in double quotes (a quoted token is a
# literal phrase, never an operator) and re-attaches a single trailing * as
# the prefix operator.  So `a" OR b`, `NEAR(x y)` and `*` are all searched as
# words.
#
# AUX MODULE GOTCHA (CLAUDE.md): aux files exec into server.py's globals, so
# `from datetime import datetime` would rebind the shared `datetime` module
# name to the class.  Private alias only — see _ix_datetime below.
#
# LOAD ORDER: aux files exec in sorted order, so aux_index runs BEFORE
# aux_messages and aux_watchtower.  Their store paths (MSG_STORE, INTEL_FILE)
# do not exist yet at module load, so this module defines its own constants
# and prefers the real globals only at CALL time, inside the sweep.
# ==========================================================================
import os
import re
import sys
import json
import time
import hashlib
import sqlite3
import subprocess
import threading
import datetime as _ix_datetime          # private alias — never `from datetime`

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
IX_DB = os.path.join(DATA, "index.db")                       # noqa: F821
IX_SOURCES = ("chat", "note", "message", "calendar", "watchtower")

_IX_Q_MAX = 200            # characters of query accepted (rest is dropped)
_IX_LIMIT_MAX = 50         # hard cap on rows returned
_IX_LIMIT_DEF = 20
_IX_PAD = 60               # +/- chars of snippet context around the hit
_IX_TITLE_MAX = 90
_IX_BODY_MAX = 200_000     # per-item body cap (a very long chat is truncated)
_IX_MAX_ITEMS = 2000       # per-source row cap, newest first

_IX_SWEEP_FIRST = 60       # first sweep, seconds after start
_IX_SWEEP_EVERY = 1800     # every 30 minutes after that
_IX_TOUCH_DRAIN = 2.0      # coalescing window for index_touch()
_IX_CAL_DAYS = 30          # calendar window, +/- this many days
_IX_CAL_TIMEOUT = 15

# chat store invariants, mirrored from aux_convos so this module stands alone
_IX_ROLES = ("user", "bot", "assistant")
_IX_META_KEYS = ("tool", "tool_name", "approval", "status", "kind")

_IX_WORD_RE = re.compile(r"\w+", re.UNICODE)
_IX_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_IX_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# Stores owned by aux modules that load AFTER this one — resolved by name at
# call time (globals().get) and only then falling back to these literals.
_IX_MSG_STORE = os.path.join(DATA, "messages.json")           # noqa: F821
_IX_INTEL_FILE = os.path.join(DATA, "intel.json")             # noqa: F821
_IX_NOTES_FILE = os.path.join(DATA, "notes.json")             # noqa: F821

_ix_lock = threading.Lock()          # serialises writers (one process, tiny db)
_ix_sweep_lock = threading.Lock()    # at most one sweep at a time
_ix_pending = {}                     # (source, id) -> True, drained every 2s
_ix_pending_lock = threading.Lock()
_ix_state = {"ready": False, "error": "", "last_sweep": 0.0, "last_ms": 0,
             "sweeps": 0, "last_reason": "", "last_counts": {}}


def _ix_log(msg):
    print("[aux_index] " + str(msg)[:400], file=sys.stderr)


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------
_IX_SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id     TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  ts     REAL,
  title  TEXT,
  body   TEXT,
  ref    TEXT,
  meta   TEXT
);
CREATE INDEX IF NOT EXISTS items_source_ts ON items(source, ts);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  title, body, content='items', content_rowid='rowid', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid, title, body)
    VALUES (new.rowid, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, body)
    VALUES ('delete', old.rowid, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, body)
    VALUES ('delete', old.rowid, old.title, old.body);
  INSERT INTO items_fts(rowid, title, body)
    VALUES (new.rowid, new.title, new.body);
END;
CREATE TABLE IF NOT EXISTS ix_meta(k TEXT PRIMARY KEY, v TEXT);
"""


def _ix_secure():
    """0600 on the db AND its WAL sidecars — the index holds chat text."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = IX_DB + suffix
        try:
            if os.path.exists(p):
                os.chmod(p, 0o600)
        except OSError:
            pass


def _ix_conn():
    """A fresh connection (sqlite objects are not thread-portable).

    The file is created by us, with 0600 already set, BEFORE sqlite ever sees
    it — connecting first and chmod'ing after leaves a window where the whole
    chat history is world-readable.
    """
    if not os.path.exists(IX_DB):
        try:
            os.close(os.open(IX_DB, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600))
        except FileExistsError:
            pass
    con = sqlite3.connect(IX_DB, timeout=10.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _ix_init():
    """Create the schema once. Sets _ix_state['ready']; never raises."""
    try:
        con = _ix_conn()
        try:
            con.executescript(_IX_SCHEMA)
            con.commit()
        finally:
            con.close()
        _ix_secure()
        _ix_state["ready"] = True
        _ix_state["error"] = ""
    except Exception as e:
        _ix_state["ready"] = False
        _ix_state["error"] = "%s: %s" % (type(e).__name__, e)
        _ix_log("index unavailable — " + _ix_state["error"])
    return _ix_state["ready"]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _ix_txt(v, limit=None):
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    s = _IX_CTRL_RE.sub(" ", s)
    return s[:limit] if limit else s


def _ix_title(v, fallback=""):
    t = " ".join(_ix_txt(v).split())[:_IX_TITLE_MAX].strip()
    return t or fallback


def _ix_num(v, default=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f and abs(f) != float("inf") else default   # NaN/inf out


def _ix_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _ix_hash(s):
    return hashlib.sha1(_ix_txt(s).encode("utf-8", "replace")).hexdigest()[:16]


def _ix_item(source, ident, title, body, ts=0.0, ref="", meta=None):
    return {"id": "%s:%s" % (source, ident), "source": source,
            "ts": _ix_num(ts), "title": _ix_title(title, source),
            "body": _ix_txt(body, _IX_BODY_MAX), "ref": _ix_txt(ref, 400),
            "meta": json.dumps(meta or {}, ensure_ascii=False)[:2000]}


def _ix_snippet(text, idx, n):
    """+/-60 chars around a hit, whitespace collapsed to one line.

    Returns (snippet, mark_start, mark_len) — offsets into the RETURNED
    string, because collapsing runs of whitespace changes lengths and the
    client cannot guess where the hit landed.  Same contract as the chat
    search (aux_convos._cv_snippet), so cvHi() renders both.
    """
    if idx is None or idx < 0:
        head = " ".join(text[:2 * _IX_PAD].split())
        return head + ("…" if len(text) > 2 * _IX_PAD else ""), None, 0
    lo = max(0, idx - _IX_PAD)
    hi = min(len(text), idx + n + _IX_PAD)
    raw_pre, raw_mid, raw_post = text[lo:idx], text[idx:idx + n], text[idx + n:hi]
    pre = " ".join(raw_pre.split())
    mid = " ".join(raw_mid.split())
    post = " ".join(raw_post.split())
    if pre and raw_pre[-1:].isspace():
        pre += " "
    if post and raw_post[:1].isspace():
        post = " " + post
    head = "…" if lo > 0 else ""
    tail = "…" if hi < len(text) else ""
    return head + pre + mid + post + tail, len(head) + len(pre), len(mid)


# --------------------------------------------------------------------------
# query sanitisation — see the header note. Never interpolate user text into
# an FTS5 MATCH expression without going through this.
# --------------------------------------------------------------------------
def _ix_tokens(q):
    """(match_expression, [(needle, is_prefix), ...]) or ('', []).

    Every token is quoted, so FTS5 reads it as a literal phrase: OR, NEAR,
    NOT, AND, a lone `"` and a column filter like `title:` are all just
    words.  A single trailing `*` on the raw query survives as the prefix
    operator on the last token — the one piece of grammar we deliberately
    keep, because "hermes fts*" is a search people actually type.
    """
    raw = _ix_txt(q).strip()[:_IX_Q_MAX]
    prefix = raw.endswith("*")
    words = _IX_WORD_RE.findall(raw)
    if not words:
        return "", []
    words = words[:12]                       # a sane ceiling on AND-terms
    parts, needles = [], []
    for i, w in enumerate(words):
        last = (i == len(words) - 1)
        quoted = '"%s"' % w.replace('"', '""')
        if last and prefix:
            parts.append(quoted + "*")
            needles.append((w.lower(), True))
        else:
            parts.append(quoted)
            needles.append((w.lower(), False))
    return " ".join(parts), needles


def _ix_locate(body, needles):
    """Earliest needle in `body` -> (index, length), or (None, 0).

    A prefix needle's mark is stretched to the end of the word it matched, so
    the highlight covers `indexing` and not just `index`.
    """
    if not body:
        return None, 0
    low = body.lower()
    best_i, best_n = None, 0
    for needle, is_prefix in needles:
        i = low.find(needle)
        if i < 0 or (best_i is not None and i >= best_i):
            continue
        n = len(needle)
        if is_prefix:
            while i + n < len(low) and (low[i + n].isalnum() or low[i + n] == "_"):
                n += 1
        best_i, best_n = i, n
    return best_i, best_n


# --------------------------------------------------------------------------
# source adapters
#
# Contract: adapter() -> list[item]  (store present; anything not returned is
# pruned from that source) or None   (store absent; nothing is pruned).
# Adapters never raise — the sweep catches anyway, but a source that throws
# would take its own rows down with it on the next prune.
# --------------------------------------------------------------------------
def _ix_g(name, fallback):
    """A server/aux global by name, or a literal fallback — aux modules that
    own these paths (aux_messages, aux_watchtower) load AFTER this one."""
    v = globals().get(name)
    return v if isinstance(v, str) and v else fallback


def _ix_turn_text(m):
    """Plain text of a real conversation turn; '' for tool/approval metadata."""
    if not isinstance(m, dict):
        return ""
    if (m.get("role") or "") not in _IX_ROLES:
        return ""
    for k in _IX_META_KEYS:
        if m.get(k):
            return ""
    t = m.get("text")
    return t if isinstance(t, str) else ""


def _ix_chat_paths():
    """(sid, path) per REAL conversation — __prewarm__ and bad ids skipped."""
    chats = _ix_g("CHATS", os.path.join(DATA, "chats"))            # noqa: F821
    prewarm = globals().get("PREWARM_SESSION") or "__prewarm__"
    sess_re = globals().get("SESSION_RE") or re.compile(r"^[A-Za-z0-9._-]{1,80}$")
    try:
        names = os.listdir(chats)
    except OSError:
        return None
    out = []
    for fn in names:
        if not fn.endswith(".json"):
            continue
        sid = fn[:-5]
        if sid == prewarm or not sess_re.match(sid):
            continue
        out.append((sid, os.path.join(chats, fn)))
    return out


def _ix_chat_item(sid, path=None):
    """One conversation as an index row, or None if it is empty/unreadable."""
    chats = _ix_g("CHATS", os.path.join(DATA, "chats"))            # noqa: F821
    path = path or os.path.join(chats, sid + ".json")
    try:
        with open(path) as f:
            chat = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(chat, dict):
        return None
    msgs = chat.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return None
    lines, auto = [], ""
    for m in msgs:
        t = _ix_turn_text(m)
        if not t.strip():
            continue
        if not auto:
            auto = " ".join(t.split())[:48]
        lines.append(t)
    if not lines:
        return None
    return _ix_item("chat", sid,
                    chat.get("title") or auto or sid,
                    "\n".join(lines),
                    ts=_ix_mtime(path), ref=sid,
                    meta={"turns": len(lines)})


def _ix_src_chat(known):
    paths = _ix_chat_paths()
    if paths is None:
        return None                       # no chats dir at all
    out = []
    for sid, path in paths:
        mt = _ix_mtime(path)
        prev = known.get("chat:" + sid)
        if prev is not None and abs(prev - mt) < 0.001:
            out.append({"id": "chat:" + sid, "unchanged": True})
            continue
        it = _ix_chat_item(sid, path)
        if it:
            out.append(it)
    return out


def _ix_note_item():
    path = _ix_g("NOTES_FILE", _IX_NOTES_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    text = (d or {}).get("text") if isinstance(d, dict) else None
    if not isinstance(text, str) or not text.strip():
        return False                       # present but empty -> prune the row
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    return _ix_item("note", "scratchpad", first or "Scratchpad", text,
                    ts=_ix_mtime(path), ref="notes", meta={"widget": "notes"})


def _ix_src_note(known):
    it = _ix_note_item()
    if it is None:
        return None                        # notes.json absent -> keep whatever
    return [] if it is False else [it]


def _ix_src_message(known):
    path = _ix_g("MSG_STORE", _IX_MSG_STORE)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict):
        return None
    convos = st.get("conversations")
    if not st.get("fda") or not isinstance(convos, list):
        return []                          # no Full Disk Access yet: no rows
    out = []
    for c in convos[:_IX_MAX_ITEMS]:
        if not isinstance(c, dict):
            continue
        name = _ix_txt(c.get("name") or c.get("ident") or "", 80)
        ident = _ix_txt(c.get("ident") or name, 80)
        last = _ix_txt(c.get("last") or "", 400)
        if not (name or last):
            continue
        sender = _ix_txt(c.get("sender") or "", 80)
        body = ("%s: %s" % (sender, last)) if sender and last else (last or name)
        out.append(_ix_item("message", _ix_hash(ident or name), name or ident,
                            body, ts=_ix_num(c.get("ts")), ref=ident or name,
                            meta={"widget": "messages",
                                  "group": bool(c.get("group")),
                                  "service": _ix_txt(c.get("service") or "", 16)}))
    return out


def _ix_cal_lines():
    """icalBuddy over +/-30 days -> ['|<when> ~ <title>', ...] or None.

    Same binary, flags and `|`/` ~ ` framing macos_calendar() uses — only the
    command differs (a date range instead of eventsToday) and -df/-tf pin an
    ISO date we can turn back into a timestamp.  If the range command fails
    for any reason we fall back to the existing provider's today-only data, so
    a future icalBuddy that drops eventsFrom still leaves calendar searchable.
    """
    try:
        import shutil as _ix_shutil
        bud = _ix_shutil.which("icalBuddy") or "/opt/homebrew/bin/icalBuddy"
    except Exception:
        bud = "/opt/homebrew/bin/icalBuddy"
    if not os.path.exists(bud):
        return None
    today = _ix_datetime.date.today()
    lo = (today - _ix_datetime.timedelta(days=_IX_CAL_DAYS)).strftime("%Y-%m-%d")
    hi = (today + _ix_datetime.timedelta(days=_IX_CAL_DAYS)).strftime("%Y-%m-%d")
    try:
        proc = subprocess.run(
            [bud, "-npn", "-nc", "-b", "|", "-nrd", "-ps", "/ ~ /",
             "-df", "%Y-%m-%d", "-tf", "%H:%M",
             "-iep", "datetime,title", "-po", "datetime,title",
             "eventsFrom:" + lo, "to:" + hi],
            capture_output=True, text=True, timeout=_IX_CAL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        proc = None
    if proc is not None and proc.returncode == 0:
        return proc.stdout.splitlines()
    # fall back to the existing provider (today only, cached)
    prov = globals().get("macos_calendar")
    if not callable(prov):
        return None
    try:
        cal = prov()
    except Exception:
        return None
    if not isinstance(cal, dict) or not cal.get("available"):
        return None
    return ["|%s ~ %s" % (e.get("time") or today.strftime("%Y-%m-%d"),
                          e.get("title") or "")
            for e in (cal.get("events") or []) if isinstance(e, dict)]


def _ix_cal_ts(when):
    m = _IX_ISO_RE.match(when.strip())
    if not m:
        return 0.0
    try:
        d = _ix_datetime.datetime(int(m.group(1)), int(m.group(2)),
                                  int(m.group(3)))
    except ValueError:
        return 0.0
    hm = re.search(r"\b(\d{1,2}):(\d{2})\b", when)
    if hm:
        try:
            d = d.replace(hour=min(23, int(hm.group(1))), minute=int(hm.group(2)))
        except ValueError:
            pass
    return d.timestamp()


def _ix_src_calendar(known):
    lines = _ix_cal_lines()
    if lines is None:
        return None                        # no calendar on this Mac
    out, seen = [], set()
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line[1:].split(" ~ ", 1)]
        if len(parts) == 2:
            when, title = parts
        elif parts and parts[0]:
            when, title = "", parts[0]
        else:
            continue
        if not title:
            continue
        ident = _ix_hash(when + "|" + title)
        if ident in seen:
            continue                       # icalBuddy repeats all-day events
        seen.add(ident)
        out.append(_ix_item("calendar", ident, title,
                            (title + " — " + when).strip(" —"),
                            ts=_ix_cal_ts(when), ref="today",
                            meta={"widget": "today", "when": when[:60]}))
        if len(out) >= _IX_MAX_ITEMS:
            break
    return out


def _ix_src_watchtower(known):
    path = _ix_g("INTEL_FILE", _IX_INTEL_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict):
        return None
    updated = _ix_num(st.get("updated"))
    rows, seen = [], {}
    for it in (st.get("curated") or []) + (st.get("items") or []):
        if not isinstance(it, dict):
            continue
        url = _ix_txt(it.get("url") or "", 400)
        title = _ix_txt(it.get("title") or "", 200)
        if not title:
            continue
        ident = _ix_hash(url or title)
        curated = it in (st.get("curated") or [])
        if ident in seen:
            continue
        seen[ident] = True
        why = _ix_txt(it.get("why") or "", 300)
        summary = _ix_txt(it.get("summary") or "", 400)
        src = _ix_txt(it.get("source") or "", 60)
        body = " ".join(x for x in (why, summary, src) if x)
        rows.append(_ix_item("watchtower", ident, title, body or title,
                             ts=_ix_num(it.get("ts")) or updated,
                             ref=url,
                             meta={"widget": "framing", "source": src,
                                   "topic": _ix_txt(it.get("topic") or "", 40),
                                   "curated": bool(curated)}))
        if len(rows) >= _IX_MAX_ITEMS:
            break
    return rows


_IX_ADAPTERS = {
    "chat": _ix_src_chat,
    "note": _ix_src_note,
    "message": _ix_src_message,
    "calendar": _ix_src_calendar,
    "watchtower": _ix_src_watchtower,
}

# single-item resolvers for index_touch(): returns an item, or None to DELETE
_IX_RESOLVERS = {
    "chat": lambda ident: _ix_chat_item(ident),
    "note": lambda ident: (lambda it: None if it in (None, False) else it)(
        _ix_note_item()),
}


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------
_IX_COLS = ("source", "ts", "title", "body", "ref", "meta")
_IX_UPSERT = (
    "INSERT INTO items(id, source, ts, title, body, ref, meta) "
    "VALUES (?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(id) DO UPDATE SET source=excluded.source, ts=excluded.ts, "
    "title=excluded.title, body=excluded.body, ref=excluded.ref, "
    "meta=excluded.meta")


def _ix_row_tuple(it):
    return (it["id"], it["source"], it["ts"], it["title"], it["body"],
            it["ref"], it["meta"])


def _ix_write(items, prune=None, con=None):
    """Upsert `items`, then delete rows of `prune['source']` not in
    `prune['keep']`.  Returns (written, deleted).  Skips rows that are
    byte-identical to what is already stored, which is what makes a sweep
    idempotent (no trigger churn, no FTS rewrite, no mtime change)."""
    own = con is None
    if own:
        con = _ix_conn()
    written = deleted = 0
    try:
        cur = con.cursor()
        for it in items:
            if it.get("unchanged"):
                continue
            row = cur.execute(
                "SELECT source, ts, title, body, ref, meta FROM items WHERE id=?",
                (it["id"],)).fetchone()
            if row is not None and tuple(row) == tuple(it[c] for c in _IX_COLS):
                continue
            cur.execute(_IX_UPSERT, _ix_row_tuple(it))
            written += 1
        if prune is not None:
            keep = prune["keep"]
            gone = [r[0] for r in cur.execute(
                "SELECT id FROM items WHERE source=?", (prune["source"],))
                if r[0] not in keep]
            for gid in gone:
                cur.execute("DELETE FROM items WHERE id=?", (gid,))
            deleted = len(gone)
        con.commit()
    finally:
        if own:
            con.close()
    return written, deleted


def _ix_known(source, con):
    return {r[0]: _ix_num(r[1]) for r in con.execute(
        "SELECT id, ts FROM items WHERE source=?", (source,))}


# --------------------------------------------------------------------------
# incremental: index_touch() + the coalescing drain thread
# --------------------------------------------------------------------------
def index_touch(source, ident, sync=False):
    """Mark one item dirty.  The write paths call this; nothing waits on it.

    Touches are coalesced in a dict and applied by a 2s drain thread, so the
    three save_chat() calls one turn makes cost exactly one re-index and a
    request never sits behind sqlite.  `sync=True` applies immediately (used
    by the harness, and safe anywhere the caller can afford ~1ms)."""
    if source not in _IX_RESOLVERS or not ident:
        return False
    if not _ix_state["ready"]:
        return False
    if sync:
        return _ix_apply_touch(source, ident)
    with _ix_pending_lock:
        _ix_pending[(source, ident)] = True
    return True


def _ix_apply_touch(source, ident):
    try:
        resolve = _IX_RESOLVERS.get(source)
        if not resolve:
            return False
        it = resolve(ident)
        with _ix_lock:
            con = _ix_conn()
            try:
                if it is None:
                    con.execute("DELETE FROM items WHERE id=?",
                                ("%s:%s" % (source, ident),))
                    con.commit()
                else:
                    _ix_write([it], con=con)
            finally:
                con.close()
        return True
    except Exception as e:
        _ix_log("touch %s:%s failed — %s: %s" % (source, ident, type(e).__name__, e))
        return False


def _ix_drain_once():
    with _ix_pending_lock:
        if not _ix_pending:
            return 0
        batch = list(_ix_pending)
        _ix_pending.clear()
    for source, ident in batch:
        _ix_apply_touch(source, ident)
    return len(batch)


def _ix_drain_loop():
    while True:
        time.sleep(_IX_TOUCH_DRAIN)
        try:
            _ix_drain_once()
        except Exception:
            pass


# --------------------------------------------------------------------------
# the sweep — start+60s, then every 30 min
# --------------------------------------------------------------------------
def index_sweep(reason="tick", sources=None):
    """Re-scan every source: upsert what changed, prune what vanished.

    Never raises, never holds a lock a request needs, and a source that blows
    up (icalBuddy hanging, a half-written intel.json) costs only its own rows
    staying stale for 30 minutes."""
    if not _ix_state["ready"] and not _ix_init():
        return {"ok": False, "error": _ix_state["error"]}
    if not _ix_sweep_lock.acquire(blocking=False):
        return {"ok": False, "error": "sweep already running"}
    t0 = time.time()
    stats = {}
    try:
        for source in (sources or IX_SOURCES):
            adapter = _IX_ADAPTERS.get(source)
            if not adapter:
                continue
            try:
                with _ix_lock:
                    con = _ix_conn()
                    try:
                        items = adapter(_ix_known(source, con))
                        if items is None:
                            stats[source] = {"absent": True}
                            continue
                        keep = {it["id"] for it in items}
                        w, d = _ix_write(
                            items, prune={"source": source, "keep": keep},
                            con=con)
                        stats[source] = {"rows": len(items), "written": w,
                                         "pruned": d}
                    finally:
                        con.close()
            except Exception as e:
                stats[source] = {"error": "%s: %s" % (type(e).__name__, e)}
                _ix_log("sweep %s failed — %s: %s" % (source, type(e).__name__, e))
        ms = int((time.time() - t0) * 1000)
        _ix_state.update(last_sweep=time.time(), last_ms=ms,
                         sweeps=_ix_state["sweeps"] + 1, last_reason=reason,
                         last_counts=stats)
        try:
            with _ix_lock:
                con = _ix_conn()
                try:
                    con.execute(
                        "INSERT INTO ix_meta(k, v) VALUES('last_sweep', ?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (json.dumps({"ts": _ix_state["last_sweep"], "ms": ms,
                                     "reason": reason, "stats": stats}),))
                    con.commit()
                finally:
                    con.close()
            _ix_secure()
        except Exception:
            pass
        return {"ok": True, "ms": ms, "stats": stats}
    finally:
        _ix_sweep_lock.release()


def _ix_sweep_loop():
    time.sleep(_IX_SWEEP_FIRST)
    while True:
        try:
            index_sweep("bg")
        except Exception:
            pass
        time.sleep(_IX_SWEEP_EVERY)


# --------------------------------------------------------------------------
# GET /api/search?q=&source=&limit=
# --------------------------------------------------------------------------
def _ix_search(ctx):
    t0 = time.time()
    q = _ix_txt(ctx.q1("q")).strip()[:_IX_Q_MAX]
    source = _ix_txt(ctx.q1("source")).strip().lower()
    if source in ("", "all"):
        source = ""
    try:
        limit = int(ctx.q1("limit") or _IX_LIMIT_DEF)
    except (TypeError, ValueError):
        limit = _IX_LIMIT_DEF
    limit = max(1, min(_IX_LIMIT_MAX, limit))

    base = {"ok": True, "q": q, "results": [], "took_ms": 0,
            "sources": {s: 0 for s in IX_SOURCES}}
    if source and source not in IX_SOURCES:
        base.update(ok=False, error="unknown source")
        return (base, 400)
    if not _ix_state["ready"]:
        base.update(ok=False, error=_ix_state["error"] or "index unavailable")
        return base
    match, needles = _ix_tokens(q)
    if not match:
        base["took_ms"] = int((time.time() - t0) * 1000)
        return base

    try:
        con = _ix_conn()
        try:
            counts = dict(con.execute(
                "SELECT i.source, COUNT(*) FROM items_fts f "
                "JOIN items i ON i.rowid = f.rowid "
                "WHERE items_fts MATCH ? GROUP BY i.source", (match,)))
            sql = ("SELECT i.id, i.source, i.title, i.ts, i.body, i.ref "
                   "FROM items_fts f JOIN items i ON i.rowid = f.rowid "
                   "WHERE items_fts MATCH ?")
            args = [match]
            if source:
                sql += " AND i.source = ?"
                args.append(source)
            # BM25 is negative-is-better; title outweighs body 10:1 so a hit in
            # a conversation's name beats a hit in its thousandth line.
            sql += " ORDER BY bm25(items_fts, 10.0, 1.0), i.ts DESC LIMIT ?"
            args.append(limit)
            rows = con.execute(sql, args).fetchall()
        finally:
            con.close()
    except Exception as e:
        base.update(ok=False, error="%s: %s" % (type(e).__name__, e))
        return base

    results = []
    for rid, rsrc, rtitle, rts, rbody, rref in rows:
        body = rbody or rtitle or ""
        idx, n = _ix_locate(body, needles)
        snippet, ms_, ml_ = _ix_snippet(body, idx, n)
        results.append({"id": rid, "source": rsrc, "title": rtitle or rid,
                        "ts": _ix_num(rts), "snippet": snippet,
                        "mark_start": ms_, "mark_len": ml_,
                        "ref": rref or ""})
    base["results"] = results
    for s in IX_SOURCES:
        base["sources"][s] = int(counts.get(s, 0))
    base["took_ms"] = int((time.time() - t0) * 1000)
    return base


# --------------------------------------------------------------------------
# GET /api/search/status
# --------------------------------------------------------------------------
def _ix_status(ctx=None):
    out = {"ok": bool(_ix_state["ready"]), "db": IX_DB,
           "sources": {s: 0 for s in IX_SOURCES}, "total": 0,
           "last_sweep": _ix_state["last_sweep"] or None,
           "last_sweep_ms": _ix_state["last_ms"],
           "sweeps": _ix_state["sweeps"],
           "last_reason": _ix_state["last_reason"],
           "detail": _ix_state["last_counts"],
           "pending": len(_ix_pending)}
    if not _ix_state["ready"]:
        out["error"] = _ix_state["error"] or "index unavailable"
        return out
    try:
        con = _ix_conn()
        try:
            for src, n in con.execute(
                    "SELECT source, COUNT(*) FROM items GROUP BY source"):
                out["sources"][src] = int(n)
                out["total"] += int(n)
            if not out["last_sweep"]:
                row = con.execute(
                    "SELECT v FROM ix_meta WHERE k='last_sweep'").fetchone()
                if row:
                    try:
                        d = json.loads(row[0])
                        out["last_sweep"] = d.get("ts")
                        out["last_sweep_ms"] = d.get("ms")
                        out["detail"] = d.get("stats") or {}
                    except ValueError:
                        pass
        finally:
            con.close()
    except Exception as e:
        out["ok"] = False
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


# --------------------------------------------------------------------------
# POST /api/notes — DELIBERATE ROUTE OVERRIDE (the notes write path)
#
# do_POST checks POST_ROUTES before its inline chain, so registering the path
# here replaces server.py's three-line handler.  It is re-implemented byte-for
# -byte (same 8000-char clamp, same _state_lock, same write_json, same
# {"ok":True}) and then touches the index — the alternative was editing
# server.py's dispatch chain, and this module's whole contract is that it
# reaches write paths WITHOUT invasive edits.  If server.py's notes handler
# ever grows, this one has to grow with it.
# --------------------------------------------------------------------------
def _ix_notes_post(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    text = (body.get("text") or "")[:8000]
    with _state_lock:                                          # noqa: F821
        write_json(_ix_g("NOTES_FILE", _IX_NOTES_FILE),        # noqa: F821
                   {"text": text})
    index_touch("note", "scratchpad")
    return {"ok": True}


# --------------------------------------------------------------------------
# module-load side effects
# --------------------------------------------------------------------------
_ix_init()

register_get("/api/search", _ix_search)              # noqa: F821
register_get("/api/search/status", _ix_status)       # noqa: F821
register_post("/api/notes", _ix_notes_post)          # noqa: F821

# save_chat / save_chat_update wrap — runtime override, no file edit (the
# pattern aux_shortcuts uses for access_preamble).  The touch runs AFTER the
# real save returns, so it never holds _state_lock, and it only queues work for
# the drain thread.
#
# BOTH entry points have to be wrapped.  save_chat_update() (server.py's
# load+mutate+save under one lock) calls _save_chat_locked directly and so
# never went through this wrapper — every writer converted to it during the
# 2026-09-10 audit fixes (A03: the chat POST's user append, _finish_chat_job's
# reply, aux_convos' rename/pin, aux_autoroute's deep answer, aux_branch's
# parent link, aux_needsyou's draft) would otherwise have stopped re-indexing
# its conversation, and the 30-minute sweep would have been the only thing
# keeping search current.
try:
    _ix_prev_save_chat                                # noqa: B018 — rewrap guard
except NameError:
    try:
        _ix_prev_save_chat = save_chat                # noqa: F821
    except NameError:
        _ix_prev_save_chat = None

if _ix_prev_save_chat is not None:
    def save_chat(session, chat, **kw):
        # **kw is pass-through, not a feature: server.py's save_chat grew a
        # `truncate=` opt-out for the append-only guard, and a wrapper that
        # ate it would TypeError on the one caller that ever needs it.
        _ix_prev_save_chat(session, chat, **kw)
        try:
            index_touch("chat", session)
        except Exception:
            pass

try:
    _ix_prev_save_chat_update                         # noqa: B018 — rewrap guard
except NameError:
    try:
        _ix_prev_save_chat_update = save_chat_update  # noqa: F821
    except NameError:
        _ix_prev_save_chat_update = None

if _ix_prev_save_chat_update is not None:
    def save_chat_update(session, mutate_fn):
        out = _ix_prev_save_chat_update(session, mutate_fn)
        try:
            index_touch("chat", session)
        except Exception:
            pass
        return out

if not globals().get("_IX_BG_STARTED"):
    _IX_BG_STARTED = True
    for _ix_fn, _ix_name in ((_ix_drain_loop, "index-drain"),
                             (_ix_sweep_loop, "index-sweep")):
        try:
            threading.Thread(target=_ix_fn, daemon=True, name=_ix_name).start()
        except Exception as _ix_e:                            # pragma: no cover
            _ix_log("thread %s failed to start: %s" % (_ix_name, type(_ix_e).__name__))
