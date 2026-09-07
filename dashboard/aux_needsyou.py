# ==========================================================================
# aux_needsyou.py — the "Needs you" attention inbox (release 1.1.2)
#
# docs/plans/purpose-and-direction.md §2 reads the honest verdict on the
# Attention layer: "Delivery exists; triage does not.  The Hub is a grid of
# feeds ... Nothing says 'these three things need you now'."  §4 item 1 and
# §4b "Needs-you inbox" specify the fix, and this is it.
#
#   GET  /api/needsyou          -> {ok, now[], today[], later[], never_count,
#                                   metrics, sources{}, generated_at}
#   POST /api/needsyou/act      -> {id, action: done|snooze|open|reclassify|draft}
#   GET  /api/needsyou/metrics  -> the trust counters on their own
#
# WHAT THIS IS NOT: a second copy of anything.  Every collector CONSUMES an
# existing store — the Message Center's messages.json, macos_calendar(),
# watchtower's fire log + intel.json, the live CHAT_JOBS registry, the
# Reminders provider.  Nothing here fetches, polls a feed, or re-derives a
# store someone else owns.  If a store is not on this Mac the collector
# returns [] and the source reports "absent"; the inbox degrades per source
# exactly as §4b requires (Gmail is not connected on this Mac, so `email` is
# absent until a provider exists).
#
# CLASSIFICATION IS RULES FIRST.  _ny_classify() is the §4b decision tree,
# pure and unit-tested:
#   now    = VIP sender (two-way history <=30 days, or a resolved contact)
#            AND a concrete time-bound ask (a question / a deadline <=24 h,
#            an event <2 h, an approval waiting)
#   today  = needs a reply but no hard deadline, or a thread you have replied
#            in, or an event later today
#   never  = automated sender, no history, no deadline language -> digest only
#   confidence < 0.6 falls to `today`, NEVER to `now`
# False urgency is the trust killer every product reviewed in the §6 sweep is
# criticised for, so `now` is the hardest bucket to reach and the only one
# with a precision counter pointed at it.
#
# THE MODEL IS OPTIONAL AND NEVER WOKEN.  settings.json `needs_you.model_pass`
# is false by default.  When it is true AND bg_online() already says a lane is
# up, the *today* bucket alone (never `now` — the model must not invent
# urgency) is sent as a 4-shot JSON prompt to that lane's
# /v1/chat/completions with a 20 s timeout.  The model may promote to `now`
# only with confidence >= 0.8 AND a concrete deadline.  Any failure, any
# malformed answer, any missing id: the rule result stands.  Nothing in this
# module ever calls agent_wake(), model_online() as a wake trigger, or starts
# a model — the settings gate is checked BEFORE bg_online(), so a disabled
# model pass does not even probe the lane.
#
# TAG, NEVER MOVE (§4b).  Snooze/done/reclassify write to this module's OWN
# store; the source rows are never touched, marked read, archived or deleted.
# Every decision is reversible because nothing upstream changed.
#
# STORE — ~/.hermes/dashboard/needsyou.json, 0600 (it holds message previews):
#   {version, items:{id:{bucket,reason,confidence,snoozed_until,done_at,
#                        reclassified_to,first_seen,last_seen}},
#    history:{ident: last_ts_you_replied},      # the VIP signal, §4b
#    shown:{"id|YYYY-MM-DD":{ts,bucket}},       # metric denominators
#    acts:[{ts,id,action,to}]}                  # metric numerators
# `shown` and `acts` are pruned to a rolling 7 days — the metric window.
#
# NEVER BLOCKS.  The payload is built on a background thread every 60 s and
# on demand when stale; the route serves the cache and returns immediately
# (a first call before the first build answers {building:true}).  The build
# runs osascript/icalBuddy, so it must never sit on a request thread.
#
# AUX MODULE GOTCHA (CLAUDE.md): aux files exec into server.py's globals, so
# `from datetime import datetime` would rebind the shared `datetime` module
# name to the class.  Private alias only — _ny_datetime below.
#
# LOAD ORDER: aux files exec SORTED, so this module runs AFTER aux_messages
# (MSG_STORE exists) but BEFORE aux_watchtower (INTEL_FILE, WT_LOG, _wt_load
# do NOT).  Watchtower globals are therefore resolved BY NAME AT CALL TIME
# with this module's own literals as the fallback — the same discipline
# aux_index.py documents.
# ==========================================================================
import os
import re
import sys
import json
import time
import tempfile
import threading
import subprocess
import urllib.request
import datetime as _ny_datetime            # private alias — never `from datetime`

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
NY_STORE = os.path.join(DATA, "needsyou.json")                # noqa: F821
NY_SOURCES = ("message", "calendar", "watchtower", "approval",
              "reminder", "email")
NY_BUCKETS = ("now", "today", "later", "never")

NY_TTL = 60                  # payload cache seconds (route + widget)
NY_LOOP = 60                 # background rebuild cadence
NY_LOOP_FIRST = 25           # let the hub finish booting first
NY_NOW_MAX = 5               # `now` never shows more than five (§4b rhythm)
NY_TODAY_MAX = 40
NY_LATER_MAX = 40
NY_MSG_WINDOW = 48 * 3600    # unread/incoming considered "recent"
NY_VIP_WINDOW = 30 * 86400   # two-way history <=30 days == VIP (§4b)
NY_EVENT_SOON = 2 * 3600     # event <2 h -> now (§4b)
NY_EVENT_GRACE = 15 * 60     # an event that started this recently still counts
NY_DEADLINE_W = 24 * 3600    # "deadline <=24 h" (§4b)
NY_BREAKING_MAX_H = 6        # breaking rows older than this are stale
NY_CONF_MIN = 0.6            # below this a rule result falls to `today`
NY_MODEL_MIN = 0.8           # the model may reach `now` only at/above this
NY_MODEL_DEMOTE_MIN = 0.6    # ...and demote only at/above this
NY_MODEL_TIMEOUT = 20        # seconds, hard
NY_MODEL_MAX_ITEMS = 12      # today-bucket rows sent in one prompt
NY_METRIC_DAYS = 7           # rolling metric window
NY_KEEP_DAYS = 14            # store rows for items not seen this long are dropped
NY_SUMMARY_MAX = 220
NY_REASON_MAX = 140

# Stores owned by modules that load AFTER this one (or by server.py) —
# resolved by name at CALL time, these literals are only the fallback.
_NY_MSG_STORE = os.path.join(DATA, "messages.json")            # noqa: F821
_NY_INTEL_FILE = os.path.join(DATA, "intel.json")              # noqa: F821
_NY_WT_LOG = os.path.join(DATA, "watchtower-log.jsonl")        # noqa: F821
_NY_WT_FILE = os.path.join(DATA, "watchtower.json")            # noqa: F821
_NY_WT_STATE = os.path.join(DATA, "watchtower-state.json")     # noqa: F821

# Gmail: no reader exists on this Mac (OAuth is read-only + pending, see §4b).
# These are the names a future provider would take; the collector looks for
# any of them and reports the source absent when none is callable.
_NY_MAIL_PROVIDERS = ("needsyou_email_provider", "gmail_needs_reply",
                      "goog_gmail_unread", "w_gmail")

_ny_lock = threading.Lock()          # serialises store writes
_ny_build_lock = threading.Lock()    # at most one build at a time
_ny_cache = {"payload": None, "at": 0.0, "kick": 0.0}
_ny_stats = {"builds": 0, "last_ms": 0, "last_error": ""}


def _ny_log(msg):
    print("[aux_needsyou] " + str(msg)[:400], file=sys.stderr)


def _ny_g(name, fallback):
    """A server/aux global by name, or a literal fallback (load order)."""
    v = globals().get(name)
    return v if isinstance(v, str) and v else fallback


def _ny_fn(name):
    """A server/aux callable by name at CALL time, or None."""
    v = globals().get(name)
    return v if callable(v) else None


# --------------------------------------------------------------------------
# language rules — the only "understanding" the rule engine needs
# --------------------------------------------------------------------------
_NY_ASK_RE = re.compile(
    r"\?|\b(can|could|would|will)\s+you\b|\b(let me know|lmk|please|pls|"
    r"rsvp|confirm|thoughts|any update|what time|when are|when is|when can|"
    r"are you (free|around|available)|get back to me|waiting on you|"
    r"need your|needs your|your call|sign off|approve)\b", re.I)

_NY_DEADLINE_RE = re.compile(
    r"\b(asap|urgent|urgently|today|tonight|this (morning|afternoon|evening)|"
    r"tomorrow|by (noon|eod|end of day|tonight|today|tomorrow|\d{1,2})|"
    r"before (noon|\d{1,2})|deadline|due (today|tomorrow|by)|in \d{1,3} "
    r"(min|mins|minute|minutes|hour|hours|hr|hrs))\b", re.I)

# An automated sender: the shape of the handle or the shape of the message.
_NY_AUTOMATED_RE = re.compile(
    r"(no-?reply|do-?not-?reply|notification|alerts?@|noreply@|"
    r"verification code|verify your|confirm your (email|number|account)|"
    r"one[- ]time (code|password)|\botp\b|your .{0,12}code is|"
    r"is your .{0,20}code|unsubscribe|newsletter|"
    r"order (confirmation|shipped|has shipped)|out for delivery|"
    r"statement is ready|automated message)", re.I)

_NY_SHORTCODE_RE = re.compile(r"^\+?\d{3,6}$")       # SMS short code
_NY_LETTERS_RE = re.compile(r"[A-Za-z]{2,}")
_NY_T12_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\.?", re.I)
_NY_T24_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _ny_txt(v, limit=None):
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    s = s.replace("\r", " ").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:limit] if limit else s


def _ny_num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _ny_clocks(s):
    """Every clock time in `s` as minutes past midnight, in order.

    12-hour forms win outright: icalBuddy prints "10:30 AM - 11:30 AM" in a
    12-hour locale and "10:30 - 11:30" in a 24-hour one, and running the
    24-hour pattern over the first would read "10:30 PM" as 10:30.
    """
    out = []
    for m in _NY_T12_RE.finditer(s or ""):
        h = int(m.group(1)) % 12
        mi = int(m.group(2) or 0)
        if m.group(3).lower() == "p":
            h += 12
        if mi <= 59:
            out.append(h * 60 + mi)
    if out:
        return out
    for m in _NY_T24_RE.finditer(s or ""):
        h, mi = int(m.group(1)), int(m.group(2))
        if h <= 23 and mi <= 59:
            out.append(h * 60 + mi)
    return out


def _ny_today_at(minutes, base=None):
    """Epoch for today at `minutes` past local midnight."""
    lt = time.localtime(base if base is not None else time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                        int(minutes) // 60, int(minutes) % 60, 0,
                        lt.tm_wday, lt.tm_yday, -1))


def _ny_day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _ny_end_of_day(base=None):
    lt = time.localtime(base if base is not None else time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 23, 59, 59,
                        lt.tm_wday, lt.tm_yday, -1))


def _ny_deadline_from_text(text, received_at, now=None):
    """A concrete deadline epoch parsed out of message text, else 0.

    Deliberately conservative: only phrasings that pin an actual moment
    within the next day become a deadline.  Vague urgency ("asap") sets
    `deadline_lang` instead — enough to make an ask time-bound, not enough to
    claim a time we do not know.
    """
    now = time.time() if now is None else now
    t = text or ""
    base = received_at if received_at and received_at > 0 else now
    m = re.search(r"\bin (\d{1,3})\s*(min|mins|minute|minutes|hour|hours|hr|hrs)\b",
                  t, re.I)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        secs = n * 60 if unit.startswith("min") else n * 3600
        return base + secs
    m = re.search(r"\b(?:by|before)\s+(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\.?",
                  t, re.I)
    if m:
        h = int(m.group(1)) % 12
        mi = int(m.group(2) or 0)
        if m.group(3).lower() == "p":
            h += 12
        when = _ny_today_at(h * 60 + mi, base)
        if when < base:
            when += 86400
        return when
    if re.search(r"\bby (noon|12 ?pm)\b", t, re.I):
        when = _ny_today_at(12 * 60, base)
        return when if when >= base else when + 86400
    if re.search(r"\bby (eod|end of day|today|tonight)\b", t, re.I):
        return _ny_end_of_day(base)
    return 0.0


# --------------------------------------------------------------------------
# store — 0600, atomic, never partial
# --------------------------------------------------------------------------
_NY_EMPTY = {"version": 1, "items": {}, "history": {}, "shown": {}, "acts": []}


def _ny_write_store(obj):
    """temp + fchmod(600) + fsync + os.replace — the aux_messages pattern."""
    d = os.path.dirname(NY_STORE)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".needsyou_")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, NY_STORE)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _ny_load():
    """The store, normalised.  Never raises, never returns a wrong shape."""
    try:
        with open(NY_STORE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = None
    if not isinstance(st, dict):
        st = {}
    out = {"version": 1,
           "items": st.get("items") if isinstance(st.get("items"), dict) else {},
           "history": st.get("history") if isinstance(st.get("history"), dict) else {},
           "shown": st.get("shown") if isinstance(st.get("shown"), dict) else {},
           "acts": st.get("acts") if isinstance(st.get("acts"), list) else []}
    return out


def _ny_save(st):
    with _ny_lock:
        try:
            _ny_write_store(st)
        except Exception as e:                                # pragma: no cover
            _ny_log("store write failed: %s" % type(e).__name__)


def _ny_enforce_600():
    try:
        if os.path.exists(NY_STORE) and os.stat(NY_STORE).st_mode & 0o077:
            os.chmod(NY_STORE, 0o600)
    except OSError:                                           # pragma: no cover
        pass


def _ny_prune(st, now=None):
    """Drop metric rows outside the 7-day window and long-unseen items."""
    now = time.time() if now is None else now
    win = now - NY_METRIC_DAYS * 86400
    st["shown"] = {k: v for k, v in st["shown"].items()
                   if isinstance(v, dict) and _ny_num(v.get("ts")) >= win}
    st["acts"] = [a for a in st["acts"]
                  if isinstance(a, dict) and _ny_num(a.get("ts")) >= win][-2000:]
    keep = now - NY_KEEP_DAYS * 86400
    st["items"] = {k: v for k, v in st["items"].items()
                   if isinstance(v, dict) and _ny_num(v.get("last_seen")) >= keep}
    st["history"] = {k: v for k, v in st["history"].items()
                     if _ny_num(v) >= now - NY_VIP_WINDOW}
    return st


# --------------------------------------------------------------------------
# item shape (§4b) — one record per inbound unit
# --------------------------------------------------------------------------
def _ny_item(source, ident, title, summary="", sender="", received_at=0.0,
             sender_tier="unknown", requires_reply=False, deadline_ts=0.0,
             deadline_lang=False, kind="", ref="", open_to="", flags=None):
    return {
        "id": "%s:%s" % (source, ident),
        "source": source,
        "sender": _ny_txt(sender or title, 80),
        "title": _ny_txt(title, 120),
        "summary": _ny_txt(summary, NY_SUMMARY_MAX),
        "received_at": _ny_num(received_at),
        "requires_reply": bool(requires_reply),
        "deadline_ts": _ny_num(deadline_ts),
        "deadline_lang": bool(deadline_lang),
        "sender_tier": sender_tier if sender_tier in
        ("vip", "known", "unknown", "automated") else "unknown",
        "kind": kind,
        "ref": _ny_txt(ref, 400),
        "open": open_to or source,
        "flags": list(flags or []),
        # filled by the classifier / build
        "bucket": "", "confidence": 0.0, "reason": "",
        "suggested_action": "none", "classified_by": "rules",
        "snoozed_until": 0.0, "done_at": 0.0, "reclassified_to": "",
    }


# ==========================================================================
# THE CLASSIFIER — §4b, implemented as a decision tree, pure and testable
# ==========================================================================
def _ny_classify(item, now=None):
    """Rules-first triage.  item -> {bucket, confidence, reason}.

    Pure: reads only the item dict (plus the clock), writes nothing, calls no
    model and no store.  This is the function the optional model pass may
    only ever REFINE, never replace.
    """
    now = time.time() if now is None else now
    if not isinstance(item, dict):
        return {"bucket": "today", "confidence": 0.5,
                "reason": "unreadable item — parked in today"}
    src = item.get("source") or ""
    tier = item.get("sender_tier") or "unknown"
    kind = item.get("kind") or ""
    flags = item.get("flags") or []
    ask = bool(item.get("requires_reply"))
    dl = _ny_num(item.get("deadline_ts"))
    dt = (dl - now) if dl else None          # seconds until it; <0 = past due
    vip = tier == "vip"

    def out(bucket, conf, why):
        return {"bucket": bucket, "confidence": round(float(conf), 2),
                "reason": _ny_txt(why, NY_REASON_MAX)}

    def guard(res):
        # §4b: "Confidence <0.6 falls to today, never now."  False urgency is
        # the trust killer, and a low-confidence `never` is a silent miss —
        # so both ends collapse to today, the bucket that costs least to be
        # wrong about.
        if res["confidence"] < NY_CONF_MIN and res["bucket"] != "today":
            return {"bucket": "today", "confidence": res["confidence"],
                    "reason": res["reason"] + " (not confident — today, not now)"}
        return res

    # ---- 1. an approval waiting IS the concrete time-bound ask, and the
    #         sender is your own agent, i.e. VIP by construction (§4b).
    if src == "approval":
        return guard(out("now", 0.95,
                         "your agent is stopped, waiting on your approval"))

    # ---- 2. news never competes with people.  Breaking already passed the
    #         master toggles upstream; curated intel is reading, not work.
    if src == "watchtower":
        if kind == "breaking":
            return guard(out("today", 0.8,
                             "breaking item that passed your alert filters"))
        return guard(out("later", 0.7,
                         "curated intel — worth reading, nothing to do"))

    # ---- 3. calendar.  Your own calendar carries the contact flag, so an
    #         event <2 h away satisfies both halves of the `now` rule.
    if src == "calendar":
        if "conflict" in flags and dt is not None and dt <= NY_DEADLINE_W:
            return guard(out("now" if dt <= NY_EVENT_SOON else "today", 0.85,
                             "overlaps another event — one of them has to move"))
        if dt is not None and dt <= NY_EVENT_SOON:
            return guard(out("now", 0.9, "starts within the next two hours"))
        return guard(out("today", 0.85, "on your calendar later today"))

    # ---- 4. reminders.  Overdue is the deadline; due-today is not yet.
    if src == "reminder":
        if dt is not None and dt < 0:
            return guard(out("now", 0.85, "overdue — you set this deadline"))
        return guard(out("today", 0.8, "due today"))

    # ---- 5. people: messages and (when a provider exists) mail.
    if src in ("message", "email"):
        time_bound = ask or (dt is not None and dt <= NY_DEADLINE_W) \
            or bool(item.get("deadline_lang"))
        if tier == "automated":
            if dt is not None and 0 <= dt <= NY_DEADLINE_W:
                return guard(out("today", 0.7,
                                 "automated, but it names a time today"))
            return guard(out("never", 0.85,
                             "automated sender, no deadline — digest only"))
        if vip and time_bound:
            both = ask and (dt is not None or item.get("deadline_lang"))
            return guard(out("now", 0.9 if both else 0.85,
                             "someone you talk to is asking you something "
                             "time-bound"))
        if ask:
            return guard(out("today", 0.75,
                             "wants a reply, but nothing is due at a set time"))
        if tier in ("vip", "known"):
            return guard(out("today", 0.7,
                             "a thread you have replied in"))
        if tier == "unknown" and not ask and dt is None \
                and not item.get("deadline_lang"):
            return guard(out("never", 0.7,
                             "no history with this sender and nothing is due"))
        return guard(out("today", 0.5,
                         "not confident either way — today, not now"))

    # ---- 6. anything a future collector adds lands in today, never now.
    return guard(out("today", 0.5,
                     "not confident either way — today, not now"))


# ==========================================================================
# COLLECTORS — each CONSUMES an existing store and returns [] when it is
# absent.  `srcs` (optional) records present/absent for the payload.
# ==========================================================================
def _ny_mark(srcs, name, present):
    if isinstance(srcs, dict):
        srcs[name] = "present" if present else "absent"
    return present


# ---- message (Message Center) --------------------------------------------
def _ny_sender_tier(convo, history, now=None):
    """VIP / known / unknown / automated, from the SAME store (§4b).

    The Message Center keeps one row per conversation, so "two-way history"
    is read two ways: `from_me` on the current row (you replied last) and
    this module's own `history` map, which remembers the last time we saw
    `from_me` for that handle.  A handle that resolves to a contact NAME
    (letters, not a formatted phone number) is the contact flag.
    """
    now = time.time() if now is None else now
    name = _ny_txt(convo.get("name") or "", 80)
    ident = _ny_txt(convo.get("ident") or "", 80)
    last = _ny_txt(convo.get("last") or "", 400)
    sender = _ny_txt(convo.get("sender") or "", 80)
    if _NY_SHORTCODE_RE.match(ident or "") or \
            _NY_AUTOMATED_RE.search(" ".join((name, ident, sender, last))):
        return "automated"
    # "two-way history <=30 days" — the window applies to EVERY reading of it,
    # including the contact-flag branch below: a reply from last winter is not
    # a VIP signal, it is an old acquaintance.
    replied = _ny_num(history.get(ident) or history.get(name))
    recent_reply = bool(replied) and replied >= now - NY_VIP_WINDOW
    if convo.get("from_me") and _ny_num(convo.get("ts")) >= now - NY_VIP_WINDOW:
        return "vip"
    if recent_reply:
        return "vip"
    contact = bool(_NY_LETTERS_RE.search(name)) and name != ident
    if contact:
        return "known"
    return "unknown"


def _ny_collect_message(srcs=None, st=None, now=None):
    """Unread / incoming Message Center rows from the last 48 h."""
    now = time.time() if now is None else now
    path = _ny_g("MSG_STORE", _NY_MSG_STORE)
    if not os.path.exists(path):
        _ny_mark(srcs, "message", False)
        return []
    try:
        with open(path) as f:
            store = json.load(f)
    except (OSError, ValueError):
        _ny_mark(srcs, "message", False)
        return []
    if not isinstance(store, dict):
        _ny_mark(srcs, "message", False)
        return []
    _ny_mark(srcs, "message", True)
    convos = store.get("conversations")
    if not store.get("fda") or not isinstance(convos, list):
        return []                       # store present, nothing readable yet
    # `st` comes from _ny_build when it is driving (one save for the whole
    # pass); called on its own the collector owns the store and saves the
    # reply history itself, or the VIP signal would be lost.
    own_store = st is None
    st = _ny_load() if own_store else st
    history = st.get("history") or {}
    hist_changed = False
    out = []
    for c in convos:
        if not isinstance(c, dict):
            continue
        ts = _ny_num(c.get("ts"))
        ident = _ny_txt(c.get("ident") or c.get("name") or "", 80)
        if not ident:
            continue
        # remember every reply we see — this is the VIP signal, and it has to
        # survive the row flipping back to `from_me: false` tomorrow.
        if c.get("from_me") and ts > _ny_num(history.get(ident)):
            history[ident] = ts
            hist_changed = True
        unread = int(_ny_num(c.get("unread")))
        incoming = (not c.get("from_me")) and ts >= now - NY_MSG_WINDOW
        if not (unread > 0 or incoming):
            continue
        last = _ny_txt(c.get("last") or "", NY_SUMMARY_MAX)
        name = _ny_txt(c.get("name") or ident, 80)
        tier = _ny_sender_tier(c, history, now)
        ask = bool(_NY_ASK_RE.search(last)) and not c.get("from_me")
        dl = _ny_deadline_from_text(last, ts, now)
        lang = bool(_NY_DEADLINE_RE.search(last))
        who = _ny_txt(c.get("sender") or name, 80)
        out.append(_ny_item(
            "message", ident, name,
            summary=last or "(no preview)",
            sender=who if c.get("group") else name,
            received_at=ts, sender_tier=tier, requires_reply=ask,
            deadline_ts=dl, deadline_lang=lang,
            kind="group" if c.get("group") else "direct",
            ref=ident, open_to="messages",
            flags=(["unread"] if unread > 0 else [])))
    st["history"] = history
    if own_store and hist_changed:
        _ny_save(st)
    return out


# ---- calendar ------------------------------------------------------------
def _ny_collect_calendar(srcs=None, now=None):
    """Today's events: <2 h away -> now, later today -> today, overlaps flagged."""
    now = time.time() if now is None else now
    prov = _ny_fn("macos_calendar")
    if prov is None:
        _ny_mark(srcs, "calendar", False)
        return []
    try:
        cal = prov()
    except Exception:
        _ny_mark(srcs, "calendar", False)
        return []
    if not isinstance(cal, dict) or not cal.get("available"):
        _ny_mark(srcs, "calendar", False)
        return []
    _ny_mark(srcs, "calendar", True)
    spans = []
    for e in (cal.get("events") or []):
        if not isinstance(e, dict):
            continue
        title = _ny_txt(e.get("title") or "", 120)
        if not title:
            continue
        clocks = _ny_clocks(_ny_txt(e.get("time") or ""))
        if not clocks:
            continue                     # all-day: nothing starts, nothing due
        start = _ny_today_at(clocks[0], now)
        end = _ny_today_at(clocks[1], now) if len(clocks) > 1 else start + 3600
        if end <= start:
            end = start + 3600
        if start < now - NY_EVENT_GRACE:
            continue                     # already under way or done
        spans.append((start, end, title, _ny_txt(e.get("time") or "", 60)))
    spans.sort()
    out = []
    for i, (start, end, title, when) in enumerate(spans):
        flags = []
        for j, (s2, e2, _t2, _w2) in enumerate(spans):
            if i != j and start < e2 and s2 < end:
                flags.append("conflict")
                break
        out.append(_ny_item(
            "calendar", "%d-%s" % (int(start), re.sub(r"\W+", "", title)[:24]),
            title, summary=when, sender="Calendar", received_at=now,
            sender_tier="vip", deadline_ts=start, kind="event",
            ref=title, open_to="today", flags=flags))
    return out


# ---- watchtower ----------------------------------------------------------
def _ny_collect_watchtower(srcs=None, now=None):
    """Breaking rows that already passed the master toggles, plus curated intel.

    The masters are NOT re-evaluated here: aux_watchtower's _breaking_pass
    already applied _master_on(cfg, "news"), the signature dedupe, the class
    cooldown, quiet hours and the daily cap before it wrote the fire-log row.
    A row with an empty `suppressed` is, by construction, one that passed.
    """
    now = time.time() if now is None else now
    present = False
    out = []

    # breaking — the fire log is the only place these live
    log = _ny_g("WT_LOG", _NY_WT_LOG)
    reader = _ny_fn("_wt_log_read")
    rows = None
    if reader is not None:
        try:
            rows = reader(200)
        except Exception:
            rows = None
    if rows is None and os.path.exists(log):
        rows = []
        try:
            with open(log) as f:
                for line in f.readlines()[-200:]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict):
                        rows.append(r)
        except OSError:
            rows = None
    if rows is not None:
        present = True
        cutoff = now - NY_BREAKING_MAX_H * 3600
        for r in reversed(rows):
            if not isinstance(r, dict):
                continue
            if not str(r.get("type") or "").startswith("breaking_"):
                continue
            if r.get("suppressed"):
                continue                 # never passed the gates
            ts = _ny_num(r.get("ts"))
            if ts < cutoff:
                continue
            ctx = r.get("context") if isinstance(r.get("context"), dict) else {}
            title = _ny_txt(r.get("label") or ctx.get("title") or "Breaking", 120)
            out.append(_ny_item(
                "watchtower", "brk-%d" % int(ts), title,
                summary=_ny_txt(r.get("text") or ctx.get("title") or "",
                                NY_SUMMARY_MAX),
                sender="Watchtower", received_at=ts, sender_tier="automated",
                kind="breaking", ref=_ny_txt(ctx.get("url") or "", 400),
                open_to="framing"))

    # curated intel
    intel = _ny_g("INTEL_FILE", _NY_INTEL_FILE)
    if os.path.exists(intel):
        try:
            with open(intel) as f:
                store = json.load(f)
        except (OSError, ValueError):
            store = None
        if isinstance(store, dict):
            present = True
            updated = _ny_num(store.get("updated"))
            for it in (store.get("curated") or []):
                if not isinstance(it, dict):
                    continue
                title = _ny_txt(it.get("title") or "", 120)
                if not title:
                    continue
                url = _ny_txt(it.get("url") or "", 400)
                out.append(_ny_item(
                    "watchtower", "cur-" + re.sub(r"\W+", "", url or title)[-32:],
                    title,
                    summary=_ny_txt(it.get("why") or it.get("source") or "",
                                    NY_SUMMARY_MAX),
                    sender=_ny_txt(it.get("source") or "Intel", 60),
                    received_at=_ny_num(it.get("ts")) or updated,
                    sender_tier="automated", kind="curated", ref=url,
                    open_to="framing"))
    _ny_mark(srcs, "watchtower", present)
    return out if present else []


# ---- approval (live chat jobs) -------------------------------------------
def _ny_collect_approval(srcs=None, now=None):
    """Chat jobs stopped in the `approval` state — the agent is blocked."""
    now = time.time() if now is None else now
    jobs = globals().get("CHAT_JOBS")
    if not isinstance(jobs, dict):
        _ny_mark(srcs, "approval", False)
        return []
    _ny_mark(srcs, "approval", True)
    out = []
    for jid, job in list(jobs.items()):
        if not isinstance(job, dict) or job.get("done"):
            continue
        if job.get("state") != "approval" or not job.get("approval"):
            continue
        ap = job.get("approval") if isinstance(job.get("approval"), dict) else {}
        what = _ny_txt(ap.get("command") or ap.get("summary") or
                       ap.get("tool") or "a sensitive action", NY_SUMMARY_MAX)
        out.append(_ny_item(
            "approval", str(jid)[:40], "Approval needed",
            summary=what, sender="Hermes", received_at=_ny_num(job.get("ts")) or now,
            sender_tier="vip", requires_reply=True, deadline_ts=now,
            kind="approval", ref=str(job.get("session") or "")[:80],
            open_to="main"))
    return out


# ---- reminders -----------------------------------------------------------
def _ny_rem_fetch():
    """Open reminders with their due dates, via osascript.  Cached 120 s.

    A separate query from w_reminders() because that one returns names only
    and a reminder without a due date cannot be triaged.  Degrades to the
    existing provider (names, no dates) so the source still reports present
    when the richer query fails — those rows simply have no deadline and are
    therefore never collected.
    """
    def fetch():
        script = (
            'set out to ""\n'
            'tell application "Reminders"\n'
            'repeat with r in (reminders whose completed is false)\n'
            'set dd to due date of r\n'
            'if dd is missing value then\n'
            'set out to out & (name of r) & tab & "0" & linefeed\n'
            'else\n'
            'set out to out & (name of r) & tab & ((year of dd) as string) & '
            '"-" & ((month of dd as integer) as string) & "-" & '
            '((day of dd) as string) & "-" & ((hours of dd) as string) & '
            '"-" & ((minutes of dd) as string) & linefeed\n'
            'end if\n'
            'end repeat\n'
            'end tell\n'
            'return out')
        try:
            p = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=10)
        except Exception:
            return {"available": False}
        if p.returncode != 0:
            prov = _ny_fn("w_reminders")
            if prov is not None:
                try:
                    d = prov()
                except Exception:
                    d = None
                if isinstance(d, dict) and d.get("available"):
                    return {"available": True, "items":
                            [{"name": n, "due": 0.0} for n in (d.get("items") or [])]}
            return {"available": False, "grant": True}
        items = []
        for line in (p.stdout or "").splitlines():
            if "\t" not in line:
                continue
            name, _sep, raw = line.partition("\t")
            name = _ny_txt(name, 120)
            if not name:
                continue
            due = 0.0
            parts = raw.strip().split("-")
            if len(parts) == 5:
                try:
                    due = _ny_datetime.datetime(
                        int(parts[0]), int(parts[1]), int(parts[2]),
                        min(23, int(parts[3])), min(59, int(parts[4]))
                    ).timestamp()
                except (ValueError, OverflowError):
                    due = 0.0
            items.append({"name": name, "due": due})
        return {"available": True, "items": items[:100]}
    cache = _ny_fn("_cached")
    return cache("needsyou_reminders", 120, fetch) if cache else fetch()


def _ny_collect_reminder(srcs=None, now=None):
    """Reminders overdue (-> now) or due today (-> today).  Nothing else."""
    now = time.time() if now is None else now
    try:
        d = _ny_rem_fetch()
    except Exception:
        d = None
    if not isinstance(d, dict) or not d.get("available"):
        _ny_mark(srcs, "reminder", False)
        return []
    _ny_mark(srcs, "reminder", True)
    eod = _ny_end_of_day(now)
    out = []
    for r in (d.get("items") or []):
        if not isinstance(r, dict):
            continue
        name = _ny_txt(r.get("name") or "", 120)
        due = _ny_num(r.get("due"))
        if not name or due <= 0 or due > eod:
            continue                     # no date, or not due today
        out.append(_ny_item(
            "reminder", re.sub(r"\W+", "", name)[:32] + "-%d" % int(due), name,
            # no summary: the reminder's own text IS the title, and the UI
            # already prints the countdown and the reason next to it
            summary="",
            sender="Reminders", received_at=due, sender_tier="vip",
            deadline_ts=due, kind="reminder", ref=name, open_to="reminders"))
    return out


# ---- email (absent until a Gmail reader exists) --------------------------
def _ny_collect_email(srcs=None, now=None):
    """Mail that looks like it wants a reply — only if a provider exists.

    aux_google.py holds read-only OAuth but ships no message reader, so on
    this Mac the source is absent by design (§4b) rather than faked.  A
    future provider only has to return {available, messages:[{id, from,
    subject, snippet, ts, unread}]} under one of _NY_MAIL_PROVIDERS.
    """
    now = time.time() if now is None else now
    prov = None
    for name in _NY_MAIL_PROVIDERS:
        prov = _ny_fn(name)
        if prov is not None:
            break
    if prov is None:
        _ny_mark(srcs, "email", False)
        return []
    try:
        d = prov()
    except Exception:
        _ny_mark(srcs, "email", False)
        return []
    if not isinstance(d, dict) or not d.get("available"):
        _ny_mark(srcs, "email", False)
        return []
    _ny_mark(srcs, "email", True)
    st = _ny_load()
    history = st.get("history") or {}
    out = []
    for m in (d.get("messages") or []):
        if not isinstance(m, dict):
            continue
        subject = _ny_txt(m.get("subject") or "(no subject)", 120)
        frm = _ny_txt(m.get("from") or "", 120)
        snippet = _ny_txt(m.get("snippet") or "", NY_SUMMARY_MAX)
        ts = _ny_num(m.get("ts"))
        blob = " ".join((frm, subject, snippet))
        if _NY_AUTOMATED_RE.search(blob):
            tier = "automated"
        elif _ny_num(history.get(frm)) >= now - NY_VIP_WINDOW:
            tier = "vip"
        elif _NY_LETTERS_RE.search(frm):
            tier = "known"
        else:
            tier = "unknown"
        out.append(_ny_item(
            "email", _ny_txt(m.get("id") or subject, 60), subject,
            summary=snippet, sender=frm, received_at=ts, sender_tier=tier,
            requires_reply=bool(_NY_ASK_RE.search(subject + " " + snippet)),
            deadline_ts=_ny_deadline_from_text(subject + " " + snippet, ts, now),
            deadline_lang=bool(_NY_DEADLINE_RE.search(blob)),
            kind="mail", ref=_ny_txt(m.get("id") or "", 60), open_to="email"))
    return out


_NY_COLLECTORS = (
    ("message", _ny_collect_message),
    ("calendar", _ny_collect_calendar),
    ("watchtower", _ny_collect_watchtower),
    ("approval", _ny_collect_approval),
    ("reminder", _ny_collect_reminder),
    ("email", _ny_collect_email),
)


# ==========================================================================
# OPTIONAL MODEL PASS — off by default, never wakes anything
# ==========================================================================
_NY_MODEL_SYSTEM = (
    "You triage one person's attention inbox. You are given items a rule "
    "engine already placed in the TODAY bucket. Re-check each one and reply "
    "with STRICT JSON only: a list of "
    '{"id":"...","bucket":"now|today|later|never","confidence":0.0-1.0,'
    '"reason":"one short line"}. '
    "Rules you must obey: 'now' requires BOTH a sender this person actually "
    "talks to AND a concrete time-bound ask (a question with a deadline "
    "within 24 hours, or something already past due). Use 'never' only for "
    "automated senders with no deadline. Use 'later' for reading with "
    "nothing to do. When unsure, answer 'today'. Never invent urgency. "
    "No prose, no markdown, no code fence: JSON array only."
)

# Four shots, chosen to pin the four failure modes: promoting a chatty VIP,
# demoting a real deadline, treating a newsletter as work, and guessing.
_NY_SHOTS = [
    ('[{"id":"message:alex","sender":"Alex","tier":"vip",'
     '"summary":"can you send the signed lease by 5pm today?",'
     '"requires_reply":true,"deadline_in_hours":4.0}]',
     '[{"id":"message:alex","bucket":"now","confidence":0.92,'
     '"reason":"someone you talk to needs the lease by 5pm"}]'),
    ('[{"id":"message:sam","sender":"Sam","tier":"vip",'
     '"summary":"haha that video was great","requires_reply":false,'
     '"deadline_in_hours":null}]',
     '[{"id":"message:sam","bucket":"today","confidence":0.8,'
     '"reason":"a thread you reply in, but nothing is being asked"}]'),
    ('[{"id":"email:promo","sender":"deals@shop.example","tier":"automated",'
     '"summary":"48 hours left on your cart","requires_reply":false,'
     '"deadline_in_hours":48.0}]',
     '[{"id":"email:promo","bucket":"never","confidence":0.9,'
     '"reason":"marketing countdown, not a deadline of yours"}]'),
    ('[{"id":"message:unknown","sender":"+15550100","tier":"unknown",'
     '"summary":"hey is this still available","requires_reply":true,'
     '"deadline_in_hours":null}]',
     '[{"id":"message:unknown","bucket":"today","confidence":0.55,'
     '"reason":"a real question from someone with no history — not urgent"}]'),
]


def _ny_model_enabled():
    """settings.json needs_you.model_pass, default FALSE.  Read fresh."""
    try:
        s = get_settings() or {}                              # noqa: F821
    except Exception:
        return False
    cfg = s.get("needs_you")
    if not isinstance(cfg, dict):
        return False
    return bool(cfg.get("model_pass", False))


def _ny_model_payload(items, now):
    rows = []
    for it in items[:NY_MODEL_MAX_ITEMS]:
        dl = _ny_num(it.get("deadline_ts"))
        rows.append({"id": it.get("id"), "sender": it.get("sender"),
                     "tier": it.get("sender_tier"),
                     "summary": it.get("summary"),
                     "requires_reply": bool(it.get("requires_reply")),
                     "deadline_in_hours":
                         (round((dl - now) / 3600.0, 1) if dl else None)})
    return rows


def _ny_model_pass(items, now=None):
    """Refine the TODAY bucket on the background lane.  Returns a dict
    id -> {bucket, confidence, reason}; {} on any failure at all.

    NEVER called for the `now` bucket: the model's job is to find what the
    rules under-rated, not to manufacture urgency.  NEVER wakes a lane: the
    settings gate is checked first so a disabled pass does not even probe.
    """
    now = time.time() if now is None else now
    if not items:
        return {}
    if not _ny_model_enabled():
        return {}
    online = _ny_fn("bg_online")
    if online is None:
        return {}
    try:
        if not online():
            return {}
    except Exception:
        return {}
    lane_fn = _ny_fn("bg_lane")
    if lane_fn is None:
        return {}
    try:
        lane = lane_fn() or {}
        url = lane.get("chat_url")
        model = lane.get("model")
    except Exception:
        return {}
    if not url or not model:
        return {}

    msgs = [{"role": "system", "content": _NY_MODEL_SYSTEM}]
    for shot_in, shot_out in _NY_SHOTS:
        msgs.append({"role": "user", "content": shot_in})
        msgs.append({"role": "assistant", "content": shot_out})
    msgs.append({"role": "user",
                 "content": json.dumps(_ny_model_payload(items, now))})
    try:
        body = json.dumps({"model": model, "messages": msgs,
                           "temperature": 0.1, "max_tokens": 900,
                           "stream": False}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=NY_MODEL_TIMEOUT) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
        text = (((resp.get("choices") or [{}])[0].get("message") or {})
                .get("content") or "").strip()
    except Exception as e:
        _ny_log("model pass skipped (%s) — rules stand" % type(e).__name__)
        return {}
    # tolerate a fenced or chatty answer; refuse anything that is not a list
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return {}
    try:
        rows = json.loads(m.group(0))
    except ValueError:
        return {}
    if not isinstance(rows, list):
        return {}

    by_id = {it.get("id"): it for it in items}
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        it = by_id.get(rid)
        if it is None:
            continue                     # an id we did not send: ignore
        bucket = row.get("bucket")
        if bucket not in NY_BUCKETS:
            continue
        conf = _ny_num(row.get("confidence"))
        reason = _ny_txt(row.get("reason") or "", NY_REASON_MAX)
        if bucket == "now":
            dl = _ny_num(it.get("deadline_ts"))
            concrete = bool(dl) and (dl - now) <= NY_DEADLINE_W
            if conf < NY_MODEL_MIN or not concrete:
                continue                 # the one promotion guard that matters
        elif conf < NY_MODEL_DEMOTE_MIN:
            continue
        out[rid] = {"bucket": bucket, "confidence": round(conf, 2),
                    "reason": reason or "reviewed on the background lane"}
    return out


# ==========================================================================
# BUILD — collect, classify, apply the user's own decisions, bucket
# ==========================================================================
def _ny_suggested(item):
    if item["source"] in ("message", "email"):
        return "reply_draft"
    if item["source"] == "approval":
        return "delegate"
    if item["source"] == "reminder":
        return "done"
    return "none"


def _ny_sort_key(it):
    dl = _ny_num(it.get("deadline_ts"))
    return (dl if dl else 9e18, -_ny_num(it.get("received_at")))


def _ny_build(now=None):
    """One full pass.  Returns the payload dict.  Never raises."""
    t0 = time.time()
    now = time.time() if now is None else now
    srcs = {s: "absent" for s in NY_SOURCES}
    st = _ny_load()
    items = []
    for name, fn in _NY_COLLECTORS:
        try:
            got = fn(srcs, st, now) if name == "message" else fn(srcs, now)
        except Exception as e:
            _ny_log("collector %s failed: %s" % (name, type(e).__name__))
            got = []
        for it in (got or []):
            if isinstance(it, dict) and it.get("id"):
                items.append(it)

    # rules first, always
    for it in items:
        try:
            v = _ny_classify(it, now)
        except Exception:
            v = {"bucket": "today", "confidence": 0.5, "reason": "rule error"}
        it["bucket"] = v["bucket"]
        it["confidence"] = v["confidence"]
        it["reason"] = v["reason"]
        it["rule_bucket"] = v["bucket"]
        it["rule_reason"] = v["reason"]
        it["classified_by"] = "rules"
        it["suggested_action"] = _ny_suggested(it)

    # optional refinement of TODAY only
    todays = [it for it in items if it["bucket"] == "today"]
    try:
        refined = _ny_model_pass(todays, now)
    except Exception as e:                                    # pragma: no cover
        _ny_log("model pass error: %s" % type(e).__name__)
        refined = {}
    if refined:
        for it in items:
            r = refined.get(it["id"])
            if not r:
                continue
            it["bucket"] = r["bucket"]
            it["confidence"] = r["confidence"]
            it["reason"] = r["reason"]
            it["classified_by"] = "model"

    # the user's own decisions win over both, and are the reason nothing
    # upstream ever has to be moved or archived
    store_items = st.get("items") or {}
    live, hidden_done, hidden_snoozed = [], 0, 0
    for it in items:
        rec = store_items.get(it["id"])
        rec = rec if isinstance(rec, dict) else {}
        rec["last_seen"] = now
        rec.setdefault("first_seen", now)
        rec["source"] = it["source"]
        it["first_seen"] = _ny_num(rec.get("first_seen"), now)
        done_at = _ny_num(rec.get("done_at"))
        snooze = _ny_num(rec.get("snoozed_until"))
        recl = rec.get("reclassified_to") or ""
        it["done_at"] = done_at
        it["snoozed_until"] = snooze
        it["reclassified_to"] = recl if recl in NY_BUCKETS else ""
        if it["reclassified_to"]:
            it["bucket"] = it["reclassified_to"]
            it["reason"] = "you moved this to " + it["reclassified_to"]
            it["classified_by"] = "you"
        rec["bucket"] = it["bucket"]
        rec["reason"] = it["reason"]
        rec["confidence"] = it["confidence"]
        store_items[it["id"]] = rec
        if done_at:
            hidden_done += 1
            continue
        if snooze > now:
            hidden_snoozed += 1
            continue
        live.append(it)
    st["items"] = store_items
    _ny_prune(st, now)
    _ny_save(st)

    buckets = {"now": [], "today": [], "later": [], "never": []}
    for it in live:
        buckets.get(it["bucket"], buckets["today"]).append(it)
    buckets["now"].sort(key=_ny_sort_key)
    buckets["today"].sort(key=_ny_sort_key)
    buckets["later"].sort(key=lambda i: -_ny_num(i.get("received_at")))
    overflow = buckets["now"][NY_NOW_MAX:]
    buckets["now"] = buckets["now"][:NY_NOW_MAX]
    # `now` is capped at five; the rest fall to today rather than vanishing
    for it in overflow:
        it["bucket"] = "today"
        it["reason"] = it["reason"] + " (over the five-item now cap)"
        buckets["today"].insert(0, it)

    payload = {
        "ok": True,
        "now": buckets["now"],
        "today": buckets["today"][:NY_TODAY_MAX],
        "later": buckets["later"][:NY_LATER_MAX],
        "never_count": len(buckets["never"]),
        "today_count": len(buckets["today"]),
        "later_count": len(buckets["later"]),
        "done_count": hidden_done,
        "snoozed_count": hidden_snoozed,
        "metrics": _ny_metrics(st, now),
        "sources": srcs,
        "model_pass": bool(refined),
        "next_brief": _ny_next_brief(now),
        "generated_at": now,
    }
    _ny_stats["builds"] += 1
    _ny_stats["last_ms"] = int((time.time() - t0) * 1000)
    return payload


# --------------------------------------------------------------------------
# metrics — the §5 counter, computed from this module's own event log
# --------------------------------------------------------------------------
def _ny_metrics(st=None, now=None):
    """now_precision / now_snooze_rate / reclass_rate over a rolling 7 days.

    §5: "of items marked now, the share acted on within the day; snoozes and
    dismissals count against it".  So acting = done | open | draft within 24 h
    of the item first being SHOWN as `now`; a snooze is not acting.
    """
    now = time.time() if now is None else now
    st = _ny_load() if st is None else st
    win = now - NY_METRIC_DAYS * 86400
    shown_now, shown_any = {}, set()
    for key, v in (st.get("shown") or {}).items():
        if not isinstance(v, dict):
            continue
        ts = _ny_num(v.get("ts"))
        if ts < win:
            continue
        iid = key.rsplit("|", 1)[0]
        shown_any.add(iid)
        if v.get("bucket") == "now":
            prev = shown_now.get(iid)
            shown_now[iid] = ts if prev is None else min(prev, ts)
    acted, snoozed, reclassed = set(), set(), set()
    for a in (st.get("acts") or []):
        if not isinstance(a, dict):
            continue
        ts = _ny_num(a.get("ts"))
        if ts < win:
            continue
        iid, act = a.get("id"), a.get("action")
        if act == "reclassify":
            reclassed.add(iid)
        if iid not in shown_now:
            continue
        if act in ("done", "open", "draft") and ts <= shown_now[iid] + 86400:
            acted.add(iid)
        elif act == "snooze":
            snoozed.add(iid)
    n_shown = len(shown_now)
    return {
        "window_days": NY_METRIC_DAYS,
        "now_shown": n_shown,
        "now_acted": len(acted),
        "now_snoozed": len(snoozed),
        "reclassified": len(reclassed),
        "shown_total": len(shown_any),
        "now_precision": (round(len(acted) / n_shown, 3) if n_shown else None),
        "now_snooze_rate": (round(len(snoozed) / n_shown, 3) if n_shown else None),
        "reclass_rate": (round(len(reclassed) / len(shown_any), 3)
                         if shown_any else None),
    }


def _ny_mark_shown(payload, now=None):
    """Record the metric denominator — once per item per calendar day.

    Deliberately called when a payload is HANDED TO A CLIENT, not when it is
    built: an item nobody ever saw must not count against `now` precision.
    """
    now = time.time() if now is None else now
    if not isinstance(payload, dict):
        return
    rows = []
    for bucket in ("now", "today", "later"):
        for it in (payload.get(bucket) or []):
            if isinstance(it, dict) and it.get("id"):
                rows.append((it["id"], bucket))
    if not rows:
        return
    day = _ny_day(now)
    st = _ny_load()
    shown = st.get("shown") or {}
    changed = False
    for iid, bucket in rows:
        key = "%s|%s" % (iid, day)
        cur = shown.get(key)
        if isinstance(cur, dict) and cur.get("bucket") == bucket:
            continue
        # a `now` sighting is never overwritten by a later, softer bucket
        if isinstance(cur, dict) and cur.get("bucket") == "now":
            continue
        shown[key] = {"ts": now, "bucket": bucket}
        changed = True
    if changed:
        st["shown"] = shown
        _ny_prune(st, now)
        _ny_save(st)


def _ny_record_act(item_id, action, to=""):
    now = time.time()
    st = _ny_load()
    acts = st.get("acts") or []
    acts.append({"ts": now, "id": item_id, "action": action, "to": to})
    st["acts"] = acts
    _ny_prune(st, now)
    _ny_save(st)


# --------------------------------------------------------------------------
# next scheduled brief — for the empty state ("nothing needs you ... next
# brief at 8:00 AM").  Reads watchtower's config; absent watchtower -> None.
# --------------------------------------------------------------------------
def _ny_next_brief(now=None):
    now = time.time() if now is None else now
    cfg = None
    loader = _ny_fn("_wt_load")
    if loader is not None:
        try:
            cfg = loader()
        except Exception:
            cfg = None
    if not isinstance(cfg, dict):
        try:
            with open(_ny_g("WT_FILE", _NY_WT_FILE)) as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return None
    if not isinstance(cfg, dict):
        return None
    best = None
    for key in ("brief", "midday", "evening"):
        slot = cfg.get(key)
        if not isinstance(slot, dict) or not slot.get("enabled", True):
            continue
        try:
            when = _ny_today_at(int(slot.get("hour", 8)) * 60 +
                                int(slot.get("minute", 0)), now)
        except (TypeError, ValueError):
            continue
        if when <= now:
            when += 86400
        if best is None or when < best[0]:
            best = (when, key)
    if best is None:
        return None
    return {"ts": best[0], "which": best[1]}


# --------------------------------------------------------------------------
# cache + background refresh — the route NEVER blocks on a build
# --------------------------------------------------------------------------
def _ny_kick(force=False):
    now = time.time()
    if not force and now - _ny_cache["kick"] < 5:
        return
    if _ny_build_lock.locked():
        return
    _ny_cache["kick"] = now
    try:
        threading.Thread(target=_ny_refresh, daemon=True,
                         name="needsyou-build").start()
    except Exception as e:                                    # pragma: no cover
        _ny_log("refresh thread failed: %s" % type(e).__name__)


def _ny_refresh():
    if not _ny_build_lock.acquire(blocking=False):
        return _ny_cache["payload"]
    try:
        payload = _ny_build()
        _ny_cache["payload"] = payload
        _ny_cache["at"] = time.time()
        _ny_stats["last_error"] = ""
        return payload
    except Exception as e:
        _ny_stats["last_error"] = "%s: %s" % (type(e).__name__, e)
        _ny_log("build failed: %r" % e)
        return _ny_cache["payload"]
    finally:
        _ny_build_lock.release()


def _ny_payload(mark=True):
    """The cached payload, refreshed in the background when stale."""
    now = time.time()
    cur = _ny_cache["payload"]
    if cur is None:
        _ny_kick()
        return {"ok": True, "building": True, "now": [], "today": [],
                "later": [], "never_count": 0, "today_count": 0,
                "later_count": 0, "done_count": 0, "snoozed_count": 0,
                "metrics": _ny_metrics(None, now),
                "sources": {s: "absent" for s in NY_SOURCES},
                "model_pass": False, "next_brief": _ny_next_brief(now),
                "generated_at": None}
    if now - _ny_cache["at"] > NY_TTL:
        _ny_kick()
    if mark:
        try:
            _ny_mark_shown(cur, now)
        except Exception:                                     # pragma: no cover
            pass
    return cur


def _ny_loop():
    time.sleep(NY_LOOP_FIRST)                # let the hub finish booting
    while True:
        try:
            _ny_refresh()
        except Exception as e:                                # pragma: no cover
            _ny_log("loop error: %r" % e)
        time.sleep(NY_LOOP)


# --------------------------------------------------------------------------
# widget + pop-out providers
# --------------------------------------------------------------------------
def w_needsyou():
    return _ny_payload()


def expand_needsyou():
    d = dict(_ny_payload())
    d["expanded"] = True
    return d


# --------------------------------------------------------------------------
# GET /api/needsyou
# --------------------------------------------------------------------------
def _ny_get_handler(ctx=None):
    try:
        return _ny_payload()
    except Exception as e:
        return {"ok": False, "error": "internal: " + type(e).__name__,
                "now": [], "today": [], "later": [], "never_count": 0,
                "metrics": {}, "sources": {s: "absent" for s in NY_SOURCES},
                "generated_at": None}


# --------------------------------------------------------------------------
# GET /api/needsyou/metrics
# --------------------------------------------------------------------------
def _ny_metrics_handler(ctx=None):
    try:
        m = _ny_metrics()
        m["ok"] = True
        m["builds"] = _ny_stats["builds"]
        m["last_build_ms"] = _ny_stats["last_ms"]
        if _ny_stats["last_error"]:
            m["last_error"] = _ny_stats["last_error"]
        return m
    except Exception as e:
        return {"ok": False, "error": "internal: " + type(e).__name__}


# --------------------------------------------------------------------------
# POST /api/needsyou/act  {id, action, until?, to?}
# --------------------------------------------------------------------------
_NY_SNOOZE = ("1h", "evening", "tomorrow")


def _ny_snooze_until(spec, now=None):
    now = time.time() if now is None else now
    if spec == "1h":
        return now + 3600
    if spec == "evening":
        eve = _ny_today_at(19 * 60, now)
        return eve if eve > now else now + 3600
    if spec == "tomorrow":
        lt = time.localtime(now + 86400)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 8, 0, 0,
                            lt.tm_wday, lt.tm_yday, -1))
    return 0.0


def _ny_find(item_id):
    """The live item with this id, from the cached payload (never a build)."""
    cur = _ny_cache["payload"]
    if not isinstance(cur, dict):
        return None
    for bucket in ("now", "today", "later"):
        for it in (cur.get(bucket) or []):
            if isinstance(it, dict) and it.get("id") == item_id:
                return it
    return None


def _ny_draft_prompt(item):
    who = item.get("sender") or item.get("title") or "them"
    what = item.get("summary") or ""
    src = "message" if item.get("source") == "message" else "email"
    return ("Draft a reply I could send. Do NOT send anything — write the "
            "draft only, in my voice, short and direct.\n"
            "Channel: %s\nFrom: %s\nTheir message: %s\n"
            "Reply with the draft text and nothing else."
            % (src, who, what[:600]))


def _ny_start_draft(item):
    """Start a chat job on the PRIMARY lane with a prepared prompt.

    Only ever reached when model_online() is already true — this module never
    wakes the model, so an asleep model returns {"ok": false} and the UI
    offers the existing "wake" affordance instead.
    """
    new_job = _ny_fn("_new_job")
    worker = _ny_fn("_chat_worker")
    loader = _ny_fn("load_chat")
    saver = _ny_fn("save_chat")
    preamble = _ny_fn("access_preamble")
    if new_job is None or worker is None:
        return ({"ok": False, "error": "chat unavailable"}, 503)
    session = "needsyou"
    prompt_body = _ny_draft_prompt(item)
    try:
        if loader is not None and saver is not None:
            chat = loader(session)
            chat.setdefault("messages", []).append(
                {"role": "user", "text": prompt_body, "ts": time.time()})
            if not chat.get("title"):
                chat["title"] = "Needs you — drafts"
            saver(session, chat)
    except Exception as e:                                    # pragma: no cover
        _ny_log("draft chat save failed: %s" % type(e).__name__)
    full = prompt_body
    try:
        if preamble is not None:
            full = preamble() + prompt_body
    except Exception:                                         # pragma: no cover
        full = prompt_body
    job = new_job(session)
    threading.Thread(target=worker, args=(job, session, full),
                     daemon=True, name="needsyou-draft").start()
    return {"ok": True, "action": "draft", "job": job["id"],
            "session": session, "id": item.get("id")}


def _ny_act_handler(ctx):
    b = ctx.body if isinstance(getattr(ctx, "body", None), dict) else {}
    item_id = _ny_txt(b.get("id") or "", 120)
    action = _ny_txt(b.get("action") or "", 20)
    if not item_id or action not in ("done", "snooze", "open", "reclassify",
                                     "draft"):
        return ({"ok": False, "error": "bad id or action"}, 400)
    now = time.time()
    item = _ny_find(item_id)

    if action == "draft":
        if item is None:
            return ({"ok": False, "error": "unknown item"}, 404)
        if item.get("source") not in ("message", "email"):
            return ({"ok": False, "error": "nothing to draft here"}, 400)
        online = _ny_fn("model_online")
        try:
            up = bool(online()) if online is not None else False
        except Exception:
            up = False
        if not up:
            # do NOT wake it — the user decides whether to spend the 30 s
            return {"ok": False, "error": "model asleep", "wakeable": True}
        res = _ny_start_draft(item)
        if isinstance(res, dict) and res.get("ok"):
            _ny_record_act(item_id, "draft")
        return res

    st = _ny_load()
    items = st.get("items") or {}
    rec = items.get(item_id)
    rec = rec if isinstance(rec, dict) else {"first_seen": now}
    rec.setdefault("first_seen", now)
    rec["last_seen"] = now
    out = {"ok": True, "id": item_id, "action": action}

    if action == "done":
        rec["done_at"] = now
        rec["snoozed_until"] = 0.0
    elif action == "snooze":
        until = _ny_num(b.get("until"))
        if until <= 0:
            until = _ny_snooze_until(_ny_txt(b.get("until") or "1h", 12), now)
        if until <= now:
            return ({"ok": False, "error": "snooze must be in the future"}, 400)
        rec["snoozed_until"] = until
        out["until"] = until
    elif action == "reclassify":
        to = _ny_txt(b.get("to") or "", 10)
        if to not in NY_BUCKETS:
            return ({"ok": False, "error": "to must be now|today|later|never"},
                    400)
        rec["reclassified_to"] = to
        out["to"] = to
    elif action == "open":
        out["open"] = (item or {}).get("open") or ""
        out["ref"] = (item or {}).get("ref") or ""

    items[item_id] = rec
    st["items"] = items
    _ny_prune(st, now)
    _ny_save(st)
    _ny_record_act(item_id, action, out.get("to", ""))

    # reflect the decision immediately: patch the cached payload rather than
    # rebuilding (a rebuild shells out to osascript and the click must be
    # instant), then let the 60 s loop reconcile.
    cur = _ny_cache["payload"]
    if isinstance(cur, dict) and action in ("done", "snooze", "reclassify"):
        for bucket in ("now", "today", "later"):
            cur[bucket] = [it for it in (cur.get(bucket) or [])
                           if it.get("id") != item_id]
        if action == "reclassify" and item is not None and out.get("to") in \
                ("now", "today", "later"):
            item["bucket"] = out["to"]
            item["reclassified_to"] = out["to"]
            item["reason"] = "you moved this to " + out["to"]
            item["classified_by"] = "you"
            cur[out["to"]].insert(0, item)
        cur["today_count"] = len(cur.get("today") or [])
        cur["later_count"] = len(cur.get("later") or [])
        cur["metrics"] = _ny_metrics(None, now)
    _ny_kick()
    return out


# --------------------------------------------------------------------------
# module-load side effects: routes, catalog entry, layout injection FIRST
# --------------------------------------------------------------------------
_ny_enforce_600()

register_get("/api/needsyou", _ny_get_handler)               # noqa: F821
register_get("/api/needsyou/metrics", _ny_metrics_handler)   # noqa: F821
register_post("/api/needsyou/act", _ny_act_handler)          # noqa: F821

WIDGETS["needsyou"] = {"title": "Needs you", "icon": "spark",   # noqa: F821
                       "size": "wide", "cat": "assistant",
                       "provider": w_needsyou}
EXPANDERS["needsyou"] = expand_needsyou                        # noqa: F821

# The whole point of §4 item 6 ("Hub re-centering") is that triage comes
# BEFORE the feeds — so this one inserts at the FRONT rather than appending.
# It still only ever touches a layout that is MISSING the id: an existing
# user order is never reordered.
try:
    _ny_lay = get_layout()                                     # noqa: F821
    if isinstance(_ny_lay, dict):
        _ny_order = _ny_lay.get("order")
        if not isinstance(_ny_order, list):
            _ny_order = _ny_lay["order"] = []
        if "needsyou" not in _ny_order:
            _ny_order.insert(0, "needsyou")
            save_layout(_ny_lay)                               # noqa: F821
except Exception as _ny_e:                                    # pragma: no cover
    print("[aux_needsyou] layout inject failed: %s" % type(_ny_e).__name__,
          file=sys.stderr)

if not globals().get("_NY_BG_STARTED"):
    _NY_BG_STARTED = True
    try:
        threading.Thread(target=_ny_loop, daemon=True,
                         name="needsyou").start()
    except Exception as _ny_e:                                # pragma: no cover
        _ny_log("loop thread failed to start: %s" % type(_ny_e).__name__)
