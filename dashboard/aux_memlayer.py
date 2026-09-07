# aux_memlayer.py — "memory layer v1" (1.2.2): a small facts store, retrieved
# by BM25 for the message being sent, injected as a fixed-shape block.
#
#   GET  /api/memory/facts?q=&include_archived=   -> list / search
#   POST /api/memory/facts        {text, kind?, pinned?}        -> add one
#   POST /api/memory/facts/update {id, text?, kind?, pinned?, archived?}
#   POST /api/memory/facts/import                               -> re-import
#   GET  /api/memory/preview?text=&budget_chars=  -> the exact injected block
#   GET/POST /api/memory/layer    {enabled, budget_chars, episodic}
#
# WHY THIS EXISTS, AND WHY IT IS THIS SMALL.  docs/plans/harness-research.md §5
# "Memory layers" and §7 "1.2.2": the evidence says a minimal, verifiable design
# — durable facts with recency decay, retrieved through the FTS5 index the repo
# already ships — beats a vector DB or an LLM-extraction pipeline, which is
# validated only against frontier cloud models and would compete for the same
# GPU budget as the turn it is trying to help.  So: no embeddings, no model
# call, no new dependency.  SQLite FTS5, stdlib only, same engine as
# aux_index.py.
#
# WHY A NEW STORE AND NOT MEMORY.md.  The agent's own MemoryStore
# (~/.hermes/hermes-agent/tools/memory_tool.py) reads MEMORY.md (2,200 chars)
# and USER.md (1,375 chars) into `_system_prompt_snapshot` ONCE per session and
# replays that snapshot verbatim on every turn — deliberately, to keep the
# prompt's token prefix byte-stable.  That is exactly the wrong shape for
# "retrieve what is relevant to THIS message": a per-turn choice cannot live in
# a per-session frozen block.  So those two files stay read-only here (this
# module NEVER writes them) and their entries are imported flagged
# `in_snapshot=1` — counted, editable, searchable, but never injected, because
# the system prompt already carries them and paying for them twice is the one
# thing a 65k window on a compute-bound prefill cannot afford.
#
# WHERE IT IS INJECTED.  server.py's access_preamble(), between the last stable
# [context] line and the wall-clock line.  That function's own comment records
# the measured rule (docs/plans/b3-prefix-ttft-findings.md): the mlx prompt
# cache reuses the longest common TOKEN PREFIX, so stable lines go first and
# volatile lines last.  The memory block changes per message, so it sits as
# late as possible — after grants/invariants/tasks/calendar — and still before
# the clock, which changes every minute for every message.  It only ever
# appends to the newest user turn, so it never perturbs the cached prefix of
# earlier turns or the agent's own cached system prompt.
#
# BUDGET.  600 chars by default — about 170 tokens at the 3.6 bytes/token this
# repo measures elsewhere (aux_promptbudget.py).  That is the entire point: the
# block is small enough that injecting it on every turn is cheaper than one
# avoidable "remind me who X is" round trip.
#
# FAIL-OPEN, ALWAYS.  Every path this module contributes to a live turn is
# wrapped: any exception injects NOTHING and logs one line, once.  A memory
# layer that can break a chat turn is worse than no memory layer.
#
# AUX MODULE GOTCHA (CLAUDE.md): aux files exec into server.py's globals, so
# `from datetime import datetime` would rebind the shared name to the class.
# This module needs no datetime at all — time.strftime does the one date it
# formats.
#
# LOAD ORDER.  aux files exec SORTED, so this runs AFTER aux_convos (its
# secret-shape regexes are reused) and aux_index (the chat FTS index the
# episodic lines read), and BEFORE aux_memory / aux_youmodel — which is why
# MEM_DIR and ENTRY_DELIM are re-declared privately here instead of borrowed,
# the same discipline aux_index.py follows for MSG_STORE/INTEL_FILE.  Foreign
# globals are resolved by name at CALL time, never captured at module load.
import os
import re
import sys
import json
import time
import sqlite3
import hashlib
import threading

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
ML_DB = os.path.join(DATA, "memory.db")                       # noqa: F821

# Re-declared, not imported: aux_memory.py defines these but loads AFTER us.
# They must stay equal to memory_tool.ENTRY_DELIMITER / MEM_DIR.
_ML_MEM_DIR = os.path.join(HOME, ".hermes", "memories")       # noqa: F821
_ML_ENTRY_DELIM = "\n§\n"                                # "\n§\n"
_ML_CORE_FILES = ("MEMORY.md", "USER.md")
_ML_YM_FILES = (
    ("GOALS.md", "project"),
    ("NOW.md", "project"),
    ("LOOKING-FOR.md", "note"),
    ("INTERESTS.md", "fact"),
    ("PREFERENCES.md", "preference"),
)

ML_KINDS = ("fact", "preference", "person", "project", "note")
ML_SOURCES = ("user", "agent", "onboarding", "youmodel", "import")

ML_BUDGET_CHOICES = (0, 300, 600, 1200)     # what the card offers
ML_DEFAULTS = {"enabled": True, "budget_chars": 600, "episodic": True}
_ML_BUDGET_MAX = 4000                        # hard ceiling on a hand-set value
_ML_BYTES_PER_TOKEN = 3.6                    # same constant aux_promptbudget uses

_ML_TEXT_MAX = 400          # one fact is a line, not an essay
_ML_CANDIDATES = 60         # rows pulled from FTS before scoring
_ML_PINNED_MAX = 12         # pinned facts considered per turn
_ML_HALF_LIFE_DAYS = 30.0   # recency = 0.5 ** (days / 30)
_ML_EPISODIC_MAX = 2
_ML_EPISODIC_FRESH = 900    # a conversation touched <15 min ago is "now", not
                            # "earlier" — that is the one you are already in
_ML_PREFIX = "[memory] "
_ML_Q_MAX = 400             # chars of the user message used to build the query
_ML_WORD_RE = re.compile(r"\w+", re.UNICODE)
# Function words, dropped before the query is built.  Without this, OR-ing
# every word of a sentence together retrieves almost anything: "who is jane and
# does she know anyone in the nyc ai scene" matched a conversation about
# Shortcuts on "does"/"the"/"in".  Deliberately short and boring — only words
# that carry no topic.  "know", "work", "today" and friends are NOT here: they
# are exactly what a question about the user is often made of.
_ML_STOP = frozenset("""
about all also am an and any are as at be been being but by can could did do
does doing done for from get got had has have he her here hers him his how i
if in into is it its just me might my no nor not of off on once one only or
our ours out over own she should so some such than that the their theirs them
then there these they this those to too under up us very was we were what when
where which while who whom why will with would you your yours
""".split())
_ML_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ML_PREF_RE = re.compile(
    r"\b(prefer|prefers|preferred|likes?|dislikes?|hates?|always|never|"
    r"don'?t|do not|should|must|avoid|only)\b", re.I)

_ML_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  text         TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'fact',
  source       TEXT NOT NULL DEFAULT 'user',
  created_ts   REAL NOT NULL DEFAULT 0,
  updated_ts   REAL NOT NULL DEFAULT 0,
  last_used_ts REAL NOT NULL DEFAULT 0,
  uses         INTEGER NOT NULL DEFAULT 0,
  weight       REAL NOT NULL DEFAULT 1.0,
  pinned       INTEGER NOT NULL DEFAULT 0,
  archived     INTEGER NOT NULL DEFAULT 0,
  in_snapshot  INTEGER NOT NULL DEFAULT 0,
  origin_key   TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS facts_live ON facts(archived, in_snapshot, pinned);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  text, content='facts', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TABLE IF NOT EXISTS ml_meta(k TEXT PRIMARY KEY, v TEXT);
"""

_ml_state = {"ready": False, "error": "", "logged": set()}
# Written by whichever request thread built the last block, read by the same
# thread a few lines later in /api/chat.  Thread-local precisely so two
# concurrent turns cannot report each other's size.
_ml_tl = threading.local()


def _ml_log(msg, once_key=None):
    """One stderr line.  With once_key, only the first occurrence is printed —
    a failing memory layer must not fill the log on every turn."""
    if once_key is not None:
        if once_key in _ml_state["logged"]:
            return
        _ml_state["logged"].add(once_key)
    print("[aux_memlayer] " + str(msg), file=sys.stderr)


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------
def _ml_secure():
    """0600 on the db and its WAL sidecars — these are the owner's own facts."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = ML_DB + suffix
        try:
            if os.path.exists(p):
                os.chmod(p, 0o600)
        except OSError:
            pass


def _ml_conn():
    """A fresh connection (sqlite objects are not thread-portable).

    The file is created by us at 0600 BEFORE sqlite ever sees it — connecting
    first and chmod'ing after leaves a window where it is world-readable.
    """
    if not os.path.exists(ML_DB):
        try:
            os.makedirs(os.path.dirname(ML_DB), mode=0o700, exist_ok=True)
        except OSError:
            pass
        try:
            os.close(os.open(ML_DB, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600))
        except FileExistsError:
            pass
    con = sqlite3.connect(ML_DB, timeout=10.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _ml_init():
    """Create the schema once.  Sets _ml_state['ready']; never raises."""
    try:
        con = _ml_conn()
        try:
            con.executescript(_ML_SCHEMA)
            con.commit()
        finally:
            con.close()
        _ml_secure()
        _ml_state["ready"] = True
        _ml_state["error"] = ""
    except Exception as e:
        _ml_state["ready"] = False
        _ml_state["error"] = "%s: %s" % (type(e).__name__, e)
        _ml_log("store unavailable — " + _ml_state["error"], once_key="init")
    return _ml_state["ready"]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _ml_txt(v, limit=None):
    s = _ML_CTRL_RE.sub(" ", str(v if v is not None else ""))
    s = " ".join(s.split())
    return s[:limit] if limit else s


def _ml_kind(v, default="fact"):
    s = str(v or "").strip().lower()
    return s if s in ML_KINDS else default


def _ml_source(v, default="user"):
    s = str(v or "").strip().lower()
    return s if s in ML_SOURCES else default


def _ml_g(name, default=None):
    """A foreign global, resolved at CALL time (load order, see header)."""
    return globals().get(name, default)


def _ml_looks_secret(text):
    """True when the text carries something shaped like a credential.

    Reuses aux_convos.py's regexes rather than inventing a second, drifting
    copy: _CV_RAW_SECRET_RES for unlabeled vendor shapes (sk-ant-, ghp_, a
    Telegram bot token…) and _CV_SECRET_RE for `token: <value>`.  This is the
    owner's own data and nothing is redacted for privacy — but a fact is
    replayed into the prompt of every matching turn, and a key that lands in
    the store would be re-sent forever.
    """
    try:
        raw = _ml_g("_cv_redact_raw")
        if callable(raw) and raw(text) != text:
            return True
        labeled = _ml_g("_CV_SECRET_RE")
        if labeled is not None and labeled.search(text):
            return True
    except Exception:
        pass
    return False


def _ml_match(q, column="", min_len=3):
    """A safe FTS5 MATCH expression, or ''.

    Same rule aux_index._ix_tokens documents: FTS5's MATCH grammar is a
    language, so throw it away — keep \\w+ runs, drop the function words, quote
    each survivor (a quoted token is a literal phrase, never an operator) and
    OR them together.  OR, not AND: a chat message is a sentence, and requiring
    every word of it to appear would retrieve nothing.

    `column` restricts the match to one FTS column — used for the episodic
    lookup, which matches conversation TITLES only.  A title is the first ~48
    chars of the first thing you said, so a title hit means that conversation
    was ABOUT this; a body hit only means the words occurred somewhere in a
    transcript.  Matching what the rendered line actually shows is also what
    makes the line legible: the model can see why it is there.
    """
    words = _ML_WORD_RE.findall(_ml_txt(q, _ML_Q_MAX).lower())
    # Episodic lines match titles with longer terms only: "say" in "what did we
    # say about…" must not surface a conversation titled "say the single word…".
    words = [w for w in words if len(w) >= min_len and w not in _ML_STOP][:16]
    if not words:
        return ""
    expr = " OR ".join('"%s"' % w.replace('"', '""') for w in words)
    return ('"%s" : (%s)' % (column, expr)) if column else expr


def _ml_recency(row_ts, now=None):
    """0.5 ** (days_since / 30), clamped to (0, 1].  A fact used today scores
    1.0; one untouched for a month scores 0.5; for a year, ~0.0002."""
    now = now if now is not None else time.time()
    try:
        ts = float(row_ts or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    if ts <= 0:
        return 0.05                     # unknown age: present, but faint
    days = max(0.0, (now - ts) / 86400.0)
    return 2.0 ** (-(days / _ML_HALF_LIFE_DAYS))


def _ml_tokens_for(chars):
    return int(round(float(chars) / _ML_BYTES_PER_TOKEN))


def _ml_row(r):
    """A db row tuple -> the dict the API returns."""
    (fid, text, kind, source, created, updated, last_used, uses,
     weight, pinned, archived, in_snapshot, origin) = r
    return {"id": int(fid), "text": text, "kind": kind, "source": source,
            "created_ts": created, "updated_ts": updated,
            "last_used_ts": last_used, "uses": int(uses),
            "weight": float(weight), "pinned": bool(pinned),
            "archived": bool(archived), "in_snapshot": bool(in_snapshot),
            "origin_key": origin}


_ML_COLS = ("id, text, kind, source, created_ts, updated_ts, last_used_ts, "
            "uses, weight, pinned, archived, in_snapshot, origin_key")
# Qualified form, for every query that joins facts_fts: `text` exists in BOTH
# tables and `id` in neither of the FTS columns, so a bare list is ambiguous.
_ML_QCOLS = ", ".join("facts." + c for c in _ML_COLS.split(", "))


# --------------------------------------------------------------------------
# settings — settings.json `memory_layer`, read fresh on every turn
# --------------------------------------------------------------------------
def memlayer_settings():
    """{enabled, budget_chars, episodic}.  Never raises; a corrupt or missing
    settings file yields the defaults, so the layer degrades to its documented
    behaviour instead of to an exception."""
    out = dict(ML_DEFAULTS)
    try:
        getter = _ml_g("get_settings")
        cfg = (getter() or {}).get("memory_layer") if callable(getter) else None
        if isinstance(cfg, dict):
            if "enabled" in cfg:
                out["enabled"] = bool(cfg.get("enabled"))
            if "episodic" in cfg:
                out["episodic"] = bool(cfg.get("episodic"))
            if "budget_chars" in cfg:
                try:
                    n = int(cfg.get("budget_chars"))
                    out["budget_chars"] = max(0, min(_ML_BUDGET_MAX, n))
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return out


def memlayer_set_settings(patch):
    """Merge a partial {enabled, budget_chars, episodic} into settings.json."""
    cur = memlayer_settings()
    if isinstance(patch, dict):
        if "enabled" in patch:
            cur["enabled"] = bool(patch.get("enabled"))
        if "episodic" in patch:
            cur["episodic"] = bool(patch.get("episodic"))
        if "budget_chars" in patch:
            try:
                n = int(patch.get("budget_chars"))
            except (TypeError, ValueError):
                raise ValueError("budget_chars must be a number")
            cur["budget_chars"] = max(0, min(_ML_BUDGET_MAX, n))
    lock = _ml_g("_state_lock")
    writer = _ml_g("write_json")
    getter = _ml_g("get_settings")
    path = _ml_g("SETTINGS_FILE")
    if not (callable(writer) and callable(getter) and path):
        raise RuntimeError("settings unavailable")
    if lock is not None:
        with lock:
            s = getter() or {}
            s["memory_layer"] = dict(cur)
            writer(path, s)
    else:                                                     # pragma: no cover
        s = getter() or {}
        s["memory_layer"] = dict(cur)
        writer(path, s)
    return cur


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------
def _ml_candidates(con, user_text, now):
    """[(score, row_dict)] for the facts eligible to be injected.

    Two passes, deliberately: every pinned fact is a candidate whether or not
    it matches (pinned means "always relevant"), and the BM25 hits are scored
    `relevance x recency x weight`.  Archived facts and facts already carried
    by the agent's own frozen system-prompt snapshot are excluded in SQL, not
    in Python, so a large store never materialises here.
    """
    picked, seen = [], set()

    for r in con.execute(
            "SELECT " + _ML_COLS + " FROM facts "
            "WHERE archived = 0 AND in_snapshot = 0 AND pinned = 1 "
            "ORDER BY updated_ts ASC LIMIT ?", (_ML_PINNED_MAX,)):
        row = _ml_row(r)
        seen.add(row["id"])
        picked.append((float("inf"), row))

    match = _ml_match(user_text)
    if not match:
        return picked

    try:
        rows = con.execute(
            "SELECT " + _ML_QCOLS + ", bm25(facts_fts) AS rank "
            "FROM facts_fts JOIN facts ON facts.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? AND facts.archived = 0 "
            "AND facts.in_snapshot = 0 "
            "ORDER BY rank LIMIT ?", (match, _ML_CANDIDATES)).fetchall()
    except sqlite3.OperationalError as e:      # a MATCH we failed to sanitise
        _ml_log("fts match refused: %s" % e, once_key="match")
        return picked

    scored = []
    for r in rows:
        row = _ml_row(r[:-1])
        if row["id"] in seen:
            continue
        # bm25() is negative-is-better; flip it so bigger is better.
        rel = max(0.0, -float(r[-1]))
        rec = _ml_recency(row["last_used_ts"] or row["updated_ts"], now)
        scored.append((rel * rec * max(0.0, row["weight"]), row))
    scored.sort(key=lambda p: (-p[0], p[1]["id"]))
    return picked + scored


def _ml_episodic(user_text, now, limit=_ML_EPISODIC_MAX):
    """Up to `limit` "earlier: <title> (Sep 3)" lines from past conversations.

    Read-only over aux_index.py's FTS5 index — the same rows /api/search
    ranks — so this adds no store and no sweep.  The point is NOT to replay a
    transcript into the prompt: it is to tell the model that a conversation
    about this exists, by name and date, so it can search for it instead of
    guessing.  Conversations touched in the last 15 minutes are excluded: the
    one you are in right now is not "earlier".
    """
    out = []
    ix_db = _ml_g("IX_DB")
    if not ix_db or not os.path.exists(ix_db):
        return out
    match = _ml_match(user_text, column="title", min_len=4)
    if not match:
        return out
    con = None
    try:
        con = sqlite3.connect(ix_db, timeout=5.0)
        rows = con.execute(
            "SELECT i.title, i.ts FROM items_fts f "
            "JOIN items i ON i.rowid = f.rowid "
            "WHERE items_fts MATCH ? AND i.source = 'chat' AND i.ts < ? "
            "ORDER BY bm25(items_fts, 10.0, 1.0) LIMIT ?",
            (match, now - _ML_EPISODIC_FRESH, limit)).fetchall()
    except Exception as e:
        _ml_log("episodic lookup skipped: %s" % type(e).__name__,
                once_key="episodic")
        return out
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    for title, ts in rows:
        t = _ml_txt(title, 60)
        if not t:
            continue
        try:
            lt = time.localtime(float(ts or 0))
            when = "%s %d" % (time.strftime("%b", lt), lt.tm_mday)
        except (TypeError, ValueError, OSError):
            when = ""
        out.append('earlier: "%s"%s' % (t, (" (%s)" % when) if when else ""))
    return out


def memlayer_block(user_text, budget_chars=None, episodic=None, now=None):
    """The exact block that would be injected for `user_text`.

    Returns {"text", "chars", "tokens", "fact_ids", "facts", "episodic"}.
    PURE with respect to the store — nothing is touched here, so the preview
    route and the unit tests can call it as often as they like.  Ordering of
    the rendered lines is stable->volatile: pinned first, then the rest oldest
    first, most-recently-updated last, then the episodic lines, which are the
    most volatile thing in the block.  Deterministic for a given set, so the
    same facts always render the same bytes.
    """
    cfg = memlayer_settings()
    budget = cfg["budget_chars"] if budget_chars is None else budget_chars
    try:
        budget = max(0, min(_ML_BUDGET_MAX, int(budget)))
    except (TypeError, ValueError):
        budget = ML_DEFAULTS["budget_chars"]
    want_ep = cfg["episodic"] if episodic is None else bool(episodic)
    now = now if now is not None else time.time()
    empty = {"text": "", "chars": 0, "tokens": 0, "fact_ids": [],
             "facts": [], "episodic": []}
    if budget <= 0 or not _ml_state["ready"]:
        return empty

    con = None
    try:
        con = _ml_conn()
        cands = _ml_candidates(con, user_text, now)
    except Exception as e:
        _ml_log("retrieval failed: %s: %s" % (type(e).__name__, e),
                once_key="retrieve")
        return empty
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

    chosen, used = [], 0
    for _score, row in cands:
        line = _ML_PREFIX + row["text"]
        cost = len(line) + (1 if chosen else 0)     # the joining newline
        if used + cost > budget:
            continue                                 # a shorter one may still fit
        chosen.append(row)
        used += cost
    # stable -> volatile: pinned first, then oldest-updated first.
    chosen.sort(key=lambda r: (0 if r["pinned"] else 1,
                               r["updated_ts"] or 0, r["id"]))

    eps = []
    if want_ep:
        for e in _ml_episodic(user_text, now):
            line = _ML_PREFIX + e
            cost = len(line) + (1 if (chosen or eps) else 0)
            if used + cost > budget:
                break
            eps.append(e)
            used += cost

    lines = [_ML_PREFIX + r["text"] for r in chosen] + [_ML_PREFIX + e for e in eps]
    text = "\n".join(lines)
    return {"text": text, "chars": len(text), "tokens": _ml_tokens_for(len(text)),
            "fact_ids": [r["id"] for r in chosen],
            "facts": chosen, "episodic": eps}


def _ml_touch(fact_ids, now=None):
    """Record that these facts were injected — last_used_ts drives the recency
    half of the score, so a fact that keeps being useful stays fresh."""
    if not fact_ids:
        return
    now = now if now is not None else time.time()
    con = None
    try:
        con = _ml_conn()
        con.executemany(
            "UPDATE facts SET last_used_ts = ?, uses = uses + 1 WHERE id = ?",
            [(now, int(i)) for i in fact_ids])
        con.commit()
    except Exception as e:
        _ml_log("touch failed: %s" % type(e).__name__, once_key="touch")
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def memlayer_lines(user_text=""):
    """The per-turn entry point server.py's access_preamble() calls.

    Returns the block as one string (no trailing newline) or "".  Fail-open:
    ANY problem returns "" and logs once, so the memory layer can never be the
    reason a chat turn fails.  Records the block's size on a thread-local so
    /api/chat can put `memory_chars` on the job without building it twice.
    """
    _ml_tl.chars = 0
    try:
        cfg = memlayer_settings()
        if not cfg["enabled"]:
            return ""
        res = memlayer_block(user_text)
        if not res["text"]:
            return ""
        _ml_touch(res["fact_ids"])
        _ml_tl.chars = res["chars"]
        return res["text"]
    except Exception as e:                                    # pragma: no cover
        _ml_log("injection skipped: %s: %s" % (type(e).__name__, e),
                once_key="inject")
        _ml_tl.chars = 0
        return ""


def memlayer_last_chars():
    """Chars injected by the most recent memlayer_lines() call ON THIS THREAD.
    0 when nothing was injected.  /api/chat reads it right after building the
    preamble, on the same request thread."""
    return int(getattr(_ml_tl, "chars", 0) or 0)


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------
def _ml_add(con, text, kind="fact", source="user", pinned=False,
            in_snapshot=False, origin_key=None, now=None):
    """Insert one fact, or update the existing row with this origin_key.

    Returns "added" | "updated" | "same".  origin_key is what makes every
    importer idempotent: re-running an import re-finds the row it wrote last
    time instead of duplicating it.
    """
    now = now if now is not None else time.time()
    text = _ml_txt(text, _ML_TEXT_MAX)
    if not text:
        raise ValueError("empty text")
    if _ml_looks_secret(text):
        raise ValueError("that looks like a credential — not storing it")
    kind = _ml_kind(kind)
    source = _ml_source(source)
    if origin_key:
        row = con.execute(
            "SELECT id, text FROM facts WHERE origin_key = ?",
            (origin_key,)).fetchone()
        if row:
            if row[1] == text:
                return "same"
            con.execute("UPDATE facts SET text = ?, kind = ?, updated_ts = ? "
                        "WHERE id = ?", (text, kind, now, row[0]))
            return "updated"
    con.execute(
        "INSERT INTO facts(text, kind, source, created_ts, updated_ts, "
        "last_used_ts, uses, weight, pinned, archived, in_snapshot, origin_key) "
        "VALUES (?,?,?,?,?,0,0,1.0,?,0,?,?)",
        (text, kind, source, now, now, 1 if pinned else 0,
         1 if in_snapshot else 0, origin_key))
    return "added"


# --------------------------------------------------------------------------
# importers — all read-only over what already exists on disk
# --------------------------------------------------------------------------
def _ml_entries(path):
    """The "\\n§\\n"-delimited entries of a memories file, template comments
    dropped.  Read-only: this module never writes into ~/.hermes/memories."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for e in raw.split(_ML_ENTRY_DELIM):
        s = e.strip()
        if not s:
            continue
        if s.startswith("<!--") and s.endswith("-->"):
            continue                     # aux_youmodel's section hint, not a fact
        out.append(s)
    return out


def _ml_key(prefix, name, text):
    h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
    return "%s:%s#%s" % (prefix, name, h)


def _ml_guess_kind(text, default="fact"):
    return "preference" if _ML_PREF_RE.search(text) else default


def memlayer_import(now=None):
    """Import every fact source already on disk.  Idempotent by origin_key.

    Three sources, and one deliberate absence:
      * MEMORY.md / USER.md  -> source=agent, in_snapshot=1.  Imported so the
        card can show them and so nothing is invisible, but NEVER injected:
        the agent's own system prompt already carries these verbatim.
      * the You-model typed files -> source=youmodel, kind per file.  These are
        injected — nothing else in the system reads them today.
      * people/*.md          -> source=youmodel, kind=person.
      * onboarding: NOTHING.  aux_onboarding.py's _onb_prefs() is a machine and
        service profile (idle minutes, prewarm, quiet hours) — there is no
        name, role or goal in it, so there is nothing about the user to import.
    """
    now = now if now is not None else time.time()
    counts = {"added": 0, "updated": 0, "same": 0, "skipped": 0, "sources": {}}
    if not _ml_state["ready"] and not _ml_init():
        counts["error"] = _ml_state["error"]
        return counts

    def bump(src, verdict):
        counts[verdict] = counts.get(verdict, 0) + 1
        s = counts["sources"].setdefault(src, {"added": 0, "updated": 0,
                                               "same": 0, "skipped": 0})
        s[verdict] += 1

    con = None
    try:
        con = _ml_conn()
        for name in _ML_CORE_FILES:
            for entry in _ml_entries(os.path.join(_ML_MEM_DIR, name)):
                try:
                    bump(name, _ml_add(
                        con, entry, kind=_ml_guess_kind(entry),
                        source="agent", in_snapshot=True,
                        origin_key=_ml_key("mem", name, entry), now=now))
                except Exception:
                    bump(name, "skipped")

        for name, kind in _ML_YM_FILES:
            for entry in _ml_entries(os.path.join(_ML_MEM_DIR, name)):
                try:
                    bump(name, _ml_add(
                        con, entry, kind=kind, source="youmodel",
                        origin_key=_ml_key("ym", name, entry), now=now))
                except Exception:
                    bump(name, "skipped")

        people_dir = os.path.join(_ML_MEM_DIR, "people")
        try:
            names = sorted(f for f in os.listdir(people_dir)
                           if f.endswith(".md"))
        except OSError:
            names = []
        for fn in names:
            for entry in _ml_entries(os.path.join(people_dir, fn)):
                try:
                    bump("people", _ml_add(
                        con, entry, kind="person", source="youmodel",
                        origin_key=_ml_key("person", fn, entry), now=now))
                except Exception:
                    bump("people", "skipped")
        con.commit()
    except Exception as e:
        counts["error"] = "%s: %s" % (type(e).__name__, e)
        _ml_log("import failed: " + counts["error"], once_key="import")
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    _ml_secure()
    return counts


def memlayer_stats():
    out = {"total": 0, "live": 0, "pinned": 0, "archived": 0,
           "in_snapshot": 0, "by_kind": {}, "by_source": {}}
    con = None
    try:
        con = _ml_conn()
        out["total"] = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        out["live"] = con.execute(
            "SELECT COUNT(*) FROM facts WHERE archived = 0 AND in_snapshot = 0"
        ).fetchone()[0]
        out["pinned"] = con.execute(
            "SELECT COUNT(*) FROM facts WHERE archived = 0 AND pinned = 1"
        ).fetchone()[0]
        out["archived"] = con.execute(
            "SELECT COUNT(*) FROM facts WHERE archived = 1").fetchone()[0]
        out["in_snapshot"] = con.execute(
            "SELECT COUNT(*) FROM facts WHERE in_snapshot = 1").fetchone()[0]
        out["by_kind"] = dict(con.execute(
            "SELECT kind, COUNT(*) FROM facts WHERE archived = 0 GROUP BY kind"))
        out["by_source"] = dict(con.execute(
            "SELECT source, COUNT(*) FROM facts WHERE archived = 0 "
            "GROUP BY source"))
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    return out


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def _ml_not_ready():
    return ({"ok": False, "error": _ml_state["error"] or "memory store "
             "unavailable"}, 503)


def _ml_facts_get(ctx):
    if not _ml_state["ready"] and not _ml_init():
        return _ml_not_ready()
    q = _ml_txt(ctx.q1("q"), _ML_Q_MAX)
    inc = str(ctx.q1("include_archived") or "").lower() in ("1", "true", "yes")
    con = None
    try:
        con = _ml_conn()
        where = [] if inc else ["archived = 0"]
        args = []
        if q:
            match = _ml_match(q)
            if match:
                sql = ("SELECT " + _ML_QCOLS +
                       " FROM facts_fts JOIN facts ON facts.id = facts_fts.rowid"
                       " WHERE facts_fts MATCH ?")
                args.append(match)
                if where:
                    sql += " AND " + " AND ".join("facts." + w for w in where)
                sql += " ORDER BY bm25(facts_fts) LIMIT 200"
            else:
                sql = ""                 # a query of only stop-short words
        else:
            sql = "SELECT " + _ML_COLS + " FROM facts"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY pinned DESC, updated_ts DESC LIMIT 500"
        rows = [_ml_row(r) for r in con.execute(sql, args)] if sql else []
    except Exception as e:
        return ({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    return {"ok": True, "q": q, "facts": rows, "count": len(rows),
            "kinds": list(ML_KINDS), "sources": list(ML_SOURCES),
            "stats": memlayer_stats(), "settings": memlayer_settings(),
            "budget_choices": list(ML_BUDGET_CHOICES),
            "bytes_per_token": _ML_BYTES_PER_TOKEN}


def _ml_facts_post(ctx):
    """Add one fact by hand.  Source is always 'user' — an owner-typed fact is
    never mislabeled as something the agent inferred."""
    if not _ml_state["ready"] and not _ml_init():
        return _ml_not_ready()
    body = ctx.body if isinstance(ctx.body, dict) else {}
    text = _ml_txt(body.get("text"), _ML_TEXT_MAX)
    if not text:
        return ({"ok": False, "error": "text is required"}, 400)
    con = None
    try:
        con = _ml_conn()
        verdict = _ml_add(con, text, kind=_ml_kind(body.get("kind")),
                          source="user", pinned=bool(body.get("pinned")))
        con.commit()
        row = con.execute("SELECT " + _ML_COLS + " FROM facts WHERE id = "
                          "last_insert_rowid()").fetchone()
    except ValueError as e:
        return ({"ok": False, "error": str(e)}, 400)
    except Exception as e:
        return ({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    return {"ok": True, "verdict": verdict,
            "fact": _ml_row(row) if row else None, "stats": memlayer_stats()}


def _ml_facts_update(ctx):
    """Edit / pin / archive / unarchive one fact.

    A POST with the id in the BODY, not a path segment: register_get/post are
    exact-match only (server.py's GET_ROUTES/POST_ROUTES are plain dicts), so
    every item mutation in this codebase carries its id in the body — the same
    shape as POST /api/needsyou/act {id, action} and POST /api/memory/save.
    Nothing is ever deleted here: archive is reversible, delete is not.
    """
    if not _ml_state["ready"] and not _ml_init():
        return _ml_not_ready()
    body = ctx.body if isinstance(ctx.body, dict) else {}
    try:
        fid = int(body.get("id"))
    except (TypeError, ValueError):
        return ({"ok": False, "error": "id is required"}, 400)

    sets, args = [], []
    if "text" in body:
        text = _ml_txt(body.get("text"), _ML_TEXT_MAX)
        if not text:
            return ({"ok": False, "error": "text cannot be empty"}, 400)
        if _ml_looks_secret(text):
            return ({"ok": False,
                     "error": "that looks like a credential — not storing it"}, 400)
        sets.append("text = ?")
        args.append(text)
    if "kind" in body:
        sets.append("kind = ?")
        args.append(_ml_kind(body.get("kind")))
    if "pinned" in body:
        sets.append("pinned = ?")
        args.append(1 if body.get("pinned") else 0)
    if "archived" in body:
        sets.append("archived = ?")
        args.append(1 if body.get("archived") else 0)
    if not sets:
        return ({"ok": False, "error": "nothing to change"}, 400)
    sets.append("updated_ts = ?")
    args.append(time.time())
    args.append(fid)

    con = None
    try:
        con = _ml_conn()
        cur = con.execute("UPDATE facts SET " + ", ".join(sets) +
                          " WHERE id = ?", args)
        if not cur.rowcount:
            return ({"ok": False, "error": "no such fact"}, 404)
        con.commit()
        row = con.execute("SELECT " + _ML_COLS + " FROM facts WHERE id = ?",
                          (fid,)).fetchone()
    except Exception as e:
        return ({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
    return {"ok": True, "fact": _ml_row(row) if row else None,
            "stats": memlayer_stats()}


def _ml_import_post(ctx):
    counts = memlayer_import()
    counts["ok"] = "error" not in counts
    counts["stats"] = memlayer_stats()
    return counts


def _ml_preview_get(ctx):
    """The exact block a given message would inject, and what it costs.

    Deliberately does NOT touch last_used_ts — a preview must not change the
    ranking of the thing it is previewing.
    """
    if not _ml_state["ready"] and not _ml_init():
        return _ml_not_ready()
    text = _ml_txt(ctx.q1("text"), _ML_Q_MAX)
    budget = ctx.q1("budget_chars")
    try:
        budget = int(budget) if str(budget).strip() else None
    except (TypeError, ValueError):
        budget = None
    cfg = memlayer_settings()
    res = memlayer_block(text, budget_chars=budget)
    return {"ok": True, "text": text, "block": res["text"],
            "chars": res["chars"], "tokens": res["tokens"],
            "fact_count": len(res["fact_ids"]),
            "episodic_count": len(res["episodic"]),
            "budget_chars": cfg["budget_chars"] if budget is None else budget,
            "enabled": cfg["enabled"],
            "facts": [{"id": f["id"], "text": f["text"], "kind": f["kind"],
                       "pinned": f["pinned"]} for f in res["facts"]],
            "bytes_per_token": _ML_BYTES_PER_TOKEN}


def _ml_layer_get(ctx):
    return {"ok": True, "settings": memlayer_settings(),
            "budget_choices": list(ML_BUDGET_CHOICES),
            "bytes_per_token": _ML_BYTES_PER_TOKEN,
            "stats": memlayer_stats()}


def _ml_layer_post(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    try:
        cfg = memlayer_set_settings(body)
    except ValueError as e:
        return ({"ok": False, "error": str(e)}, 400)
    except Exception as e:
        return ({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
    return {"ok": True, "settings": cfg, "stats": memlayer_stats()}


# --------------------------------------------------------------------------
# module-load side effects
# --------------------------------------------------------------------------
_ml_init()
try:
    # Cheap: a handful of small files, and idempotent by origin_key, so the
    # store is populated on the first boot after an update without the owner
    # having to press anything.  Never fatal — the aux loader would only print
    # the failure, but a memory layer that stops the dashboard is unthinkable.
    if _ml_state["ready"]:
        memlayer_import()
except Exception as _ml_e:                                    # pragma: no cover
    _ml_log("initial import failed: %s" % type(_ml_e).__name__,
            once_key="boot-import")

register_get("/api/memory/facts", _ml_facts_get)               # noqa: F821
register_post("/api/memory/facts", _ml_facts_post)             # noqa: F821
register_post("/api/memory/facts/update", _ml_facts_update)    # noqa: F821
register_post("/api/memory/facts/import", _ml_import_post)     # noqa: F821
register_get("/api/memory/preview", _ml_preview_get)           # noqa: F821
register_get("/api/memory/layer", _ml_layer_get)               # noqa: F821
register_post("/api/memory/layer", _ml_layer_post)             # noqa: F821
