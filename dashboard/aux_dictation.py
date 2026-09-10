# aux_dictation.py — the dashboard half of local voice dictation.
#
#   POST /api/dictation/finish     {text, app_bundle, app_name, duration_ms, engine}
#                                  -> {text, mode, ms}  (the cleaned transcript)
#   GET  /api/dictation/settings   -> the settings block
#   POST /api/dictation/settings   -> merge + clamp + persist, returns the block
#   POST /api/dictation/status     the helper's heartbeat
#   GET  /api/dictation            helper state, today's counts, recent list
#   GET  /api/dictation/install    is the helper built / running, and how to build it
#
# WHAT OWNS WHAT.  The microphone, the hotkey and the text insertion belong to
# the "Hermes Dictation" helper app (app/dictation/main.swift) — TCC attributes
# those to the responsible process and a launchd python can never durably hold
# them.  This module owns exactly two things the helper should not: the CLEANUP
# of the transcript, and the RECORD of what happened.  It never touches audio, a
# microphone, or the keyboard.
#
# THE HELPER IS NEVER BLOCKED ON US.  Every route here answers fast or not at
# all: `finish` has a hard budget (rules are pure Python; the optional model pass
# is capped at 4 s and falls back to the rules result), and the helper inserts
# the raw transcript if this dashboard is down.  Dictation that stops working
# because a web server is restarting is worse than dictation with an "um" in it.
#
# THREE CLEANUP MODES (settings.json `dictation.cleanup`):
#   off    — the raw transcript, untouched.  The honest baseline.
#   rules  — pure, deterministic, no model, sub-millisecond: filler removal,
#            doubled-word collapse, self-corrections, the user's dictionary,
#            then sentence capitalisation and trailing punctuation.  DEFAULT.
#   model  — the rules result FIRST, then a short prompt to a model lane that is
#            ALREADY ONLINE.  It never wakes anything: `bg_online()` /
#            `model_online()` are checked before `bg_lane()` is even called (that
#            helper silently falls back to the primary lane, which on this Mac is
#            a ~19 GB on-demand model the owner may be deliberately keeping
#            asleep on battery).  4 s hard timeout, and ANY failure — offline,
#            slow, empty, chatty, suspiciously long — leaves the rules result
#            standing.  A cleanup step that can lose your sentence is not a
#            cleanup step.
#
# PER-APP STYLE.  `app_styles` maps a bundle id to one of three styles, and the
# bundle the helper reports decides which is used:
#   prose    (Mail, Notes)        full sentences, capitalised, closing full stop.
#   casual   (Slack, Messages)    capitalised, but no full stop is added — chat
#                                 messages do not end in one, and adding it is
#                                 the tell that a machine wrote the line.
#   verbatim (Terminal, VS Code)  fillers, doubled words and self-corrections are
#                                 still removed (that is what dictation cleanup
#                                 IS), but NOTHING else: no capitalisation, no
#                                 punctuation added or removed.  A shell command
#                                 that gets a capital letter and a full stop is a
#                                 shell command that does not run.
#
# PRIVACY.  `keep_history` is OFF by default and only it decides whether the
# TEXT is stored; the counts, the app name, the latency and the engine are kept
# either way so the card can say something true without keeping what you said.
# The store is 0600 from birth (it can hold dictated text) and prunes to
# `history_days`.
#
# AUX MODULE GOTCHA (CLAUDE.md): never `from datetime import datetime` in an aux
# module — it rebinds the shared global for every other module.  Private aliases
# only, which is why everything below is imported as `_dct_*`.
#
# LOAD ORDER.  aux files exec SORTED, so this module runs after aux_desktop and
# before aux_evals.  It reads no foreign global at module load: HOME/DATA/HERE/
# SETTINGS_FILE/_state_lock/get_settings/write_json are server.py's own (defined
# before any aux exec) and the model-lane helpers are resolved BY NAME AT CALL
# TIME through `_dct_g()`, the discipline aux_index documents.
import datetime as _dct_datetime
import json as _dct_json
import os as _dct_os
import re as _dct_re
import threading as _dct_threading
import time as _dct_time
import urllib.request as _dct_urlrequest

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

DCT_STORE = _dct_os.path.join(DATA, "dictation.json")          # noqa: F821
DCT_ROOT = _dct_os.path.dirname(HERE)                          # noqa: F821
DCT_BUNDLE = _dct_os.path.join(DCT_ROOT, "app", "build", "Hermes Dictation.app")
DCT_BUILD_SCRIPT = _dct_os.path.join(DCT_ROOT, "app", "build-dictation.sh")

DCT_MAX_CHARS = 8000          # one dictation; anything longer is a bug upstream
DCT_MODEL_TIMEOUT = 4.0       # hard, per the design — the user is waiting
DCT_STALE_S = 90              # heartbeat older than this = "not running"
DCT_HISTORY_MAX = 400         # rows, before the day-based prune even runs
DCT_HISTORY_DAYS_MAX = 90

DCT_STYLES = ("prose", "casual", "verbatim")
DCT_MODES = ("off", "rules", "model")

# Seeded, not hardcoded: these land in settings.json on the first write and the
# user can edit or delete any of them.  Bundle ids, because an app's NAME is
# localised and changes; the id does not.
DCT_DEFAULT_APP_STYLES = {
    "com.apple.mail": "prose",
    "com.apple.Notes": "prose",
    "com.apple.TextEdit": "prose",
    "com.tinyspeck.slackmacgap": "casual",
    "com.apple.MobileSMS": "casual",
    "com.hnc.Discord": "casual",
    "com.apple.Terminal": "verbatim",
    "com.googlecode.iterm2": "verbatim",
    "com.microsoft.VSCode": "verbatim",
}

DCT_DEFAULTS = {
    "cleanup": "rules",
    "dictionary": {},
    "app_styles": dict(DCT_DEFAULT_APP_STYLES),
    "hotkey": "Right Option",
    "history_days": 7,
    "keep_history": False,
}

DCT_STYLE_HINT = {
    "prose": ("Write it as prose: full sentences, normal punctuation and "
              "capitalisation."),
    "casual": ("It is a chat message: keep it informal and do not add a full "
               "stop at the end."),
    "verbatim": ("Verbatim: do not change punctuation, capitalisation, spelling "
                 "or word choice. Only remove filler words and false starts."),
}

_dct_lock = _dct_threading.Lock()


def _dct_g(name):
    """A server.py/aux global resolved AT CALL TIME (never captured at module
    load — the sorted exec order means a name may not exist yet)."""
    v = globals().get(name)
    return v if callable(v) else None


def _dct_log(msg):
    print("[dictation] " + str(msg), file=__import__("sys").stderr)


# ---------------------------------------------------------------------------
# settings — fresh read-modify-write under _state_lock, clamped on the way IN
# ---------------------------------------------------------------------------

def _dct_clean_dictionary(raw):
    """{from: to} with both halves trimmed strings.  Keys are matched
    case-insensitively later, so a duplicate key differing only in case would be
    a silent no-op — the LAST one wins and the rest are dropped here."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in list(raw.items())[:200]:
        k = str(k or "").strip()[:80]
        v = str(v or "").strip()[:120]
        if not k:
            continue
        out[k] = v
    return out


def _dct_clean_app_styles(raw):
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in list(raw.items())[:200]:
        k = str(k or "").strip()[:120]
        v = str(v or "").strip().lower()
        if not k or v not in DCT_STYLES:
            continue
        out[k] = v
    return out


def _dct_num(v, default=0.0):
    """float(v) that can't raise. A history row's `ts` (or the status
    heartbeat's) round-trips through the JSON store on disk — a hand-edited
    file, a partial write recovered from a stale .tmp, or a future producer
    sending the wrong shape can hand back a string/list/None where a number
    is expected. `float(r.get("ts") or 0)` only guarded the falsy/missing
    case; a non-numeric-but-truthy value (e.g. ts: "unknown") raised
    ValueError straight out of whatever was scanning history — pruning,
    today's stats, or the status heartbeat."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _dct_bool(v, default=False):
    """Accept only an actual JSON bool. `bool(v)` on anything else is a foot-
    gun here: bool("false") is True in Python, so a client sending the STRING
    "false" for keep_history (a stray JSON.stringify, a form field, a curl
    typo) silently turned history back ON instead of being rejected. Anything
    that isn't literally True/False falls back to `default`."""
    return v if isinstance(v, bool) else default


def dictation_settings():
    """The settings block, defaults filled in, always the full shape."""
    s = (_dct_g("get_settings") or (lambda: {}))() or {}
    raw = s.get("dictation")
    raw = raw if isinstance(raw, dict) else {}
    mode = str(raw.get("cleanup") or DCT_DEFAULTS["cleanup"]).strip().lower()
    if mode not in DCT_MODES:
        mode = DCT_DEFAULTS["cleanup"]
    try:
        days = int(raw.get("history_days", DCT_DEFAULTS["history_days"]))
    except (TypeError, ValueError):
        days = DCT_DEFAULTS["history_days"]
    days = max(0, min(DCT_HISTORY_DAYS_MAX, days))
    styles = _dct_clean_app_styles(raw.get("app_styles"))
    if "app_styles" not in raw:
        styles = dict(DCT_DEFAULT_APP_STYLES)
    return {
        "cleanup": mode,
        "dictionary": _dct_clean_dictionary(raw.get("dictionary")),
        "app_styles": styles,
        "hotkey": str(raw.get("hotkey") or DCT_DEFAULTS["hotkey"]).strip()[:40],
        "history_days": days,
        "keep_history": bool(raw.get("keep_history", DCT_DEFAULTS["keep_history"])),
    }


def dictation_set_settings(patch):
    """Merge + clamp + persist through server.py's settings_update() — ONE
    locked read-modify-write of settings.json, like every other settings writer
    since the 2026-09-10 audit (A01).  A stale copy here would silently undo a
    change another card made between the read and the write."""
    patch = patch if isinstance(patch, dict) else {}
    updater = _dct_g("settings_update")
    if not callable(updater):
        return dictation_settings()
    flags = {"turn_off": False}

    def _apply(s):
        cur = s.get("dictation")
        cur = dict(cur) if isinstance(cur, dict) else {}
        if "cleanup" in patch:
            m = str(patch.get("cleanup") or "").strip().lower()
            if m in DCT_MODES:
                cur["cleanup"] = m
        if "dictionary" in patch:
            cur["dictionary"] = _dct_clean_dictionary(patch.get("dictionary"))
        if "app_styles" in patch:
            cur["app_styles"] = _dct_clean_app_styles(patch.get("app_styles"))
        if "hotkey" in patch:
            cur["hotkey"] = str(patch.get("hotkey") or "").strip()[:40] or \
                DCT_DEFAULTS["hotkey"]
        if "history_days" in patch:
            try:
                d = int(patch.get("history_days"))
            except (TypeError, ValueError):
                d = DCT_DEFAULTS["history_days"]
            cur["history_days"] = max(0, min(DCT_HISTORY_DAYS_MAX, d))
        if "keep_history" in patch:
            # Strict: only an actual JSON bool changes the setting.
            # bool("false") is True in Python, so a stringified "false" (a
            # stray JSON.stringify, a form field, a curl typo) used to
            # silently turn history back ON — a privacy-relevant setting
            # must not be guessable from truthy/falsy coercion. An invalid
            # value is a no-op: the existing setting stands rather than the
            # patch being interpreted either way.
            cur["keep_history"] = _dct_bool(
                patch.get("keep_history"),
                cur.get("keep_history", DCT_DEFAULTS["keep_history"]))
        s["dictation"] = cur
        flags["turn_off"] = ("keep_history" in patch
                             and cur.get("keep_history") is False)

    updater(_apply)
    turn_off = flags["turn_off"]
    out = dictation_settings()
    # Turning history off is not a promise about the future — it must also drop
    # what is already stored, or the toggle is decoration. _dct_forget_text()
    # now reports whether it actually succeeded — a silently swallowed OSError
    # there used to mean this could report "Saved" while the old text was
    # still sitting on disk.
    if turn_off and not _dct_forget_text():
        out = dict(out)
        out["ok"] = False
        out["error"] = "settings saved but the stored text could not be removed"
    return out


def _dct_style_for(bundle, name, settings=None):
    st = settings or dictation_settings()
    styles = st.get("app_styles") or {}
    b = (bundle or "").strip()
    if b and b in styles:
        return styles[b]
    # A case-insensitive second look: bundle ids are case-sensitive in principle
    # and inconsistently cased in practice ("com.apple.notes" vs ".Notes").
    lb = b.lower()
    for k, v in styles.items():
        if k.lower() == lb and lb:
            return v
    n = (name or "").strip().lower()
    for k, v in styles.items():
        if k.lower().split(".")[-1] == n and n:
            return v
    return "prose"


# ---------------------------------------------------------------------------
# the rules cleanup — pure, deterministic, no model, no I/O
# ---------------------------------------------------------------------------
#
# Everything below is a pure function of (text, style, dictionary).  That is the
# point: it is the DEFAULT mode, it runs on every dictation, and it is the
# fallback when the model pass fails — so it has to be testable without a model,
# a microphone or a dashboard.

# Standalone hesitation sounds.  Deliberately conservative: "like", "so" and
# "well" are real words far more often than they are filler, and a cleanup that
# eats a real word costs the user more than one that leaves an "uh" in.
_DCT_FILLER_WORDS = ("um", "umm", "ummm", "uh", "uhh", "uhhh", "uhm", "erm",
                     "er", "ah", "ahh", "hmm", "mmm", "mhm")
_DCT_FILLER_PHRASES = ("you know", "kind of like", "sort of like")

# The commas around a filler belong to the filler — "this is, uh, a test" is one
# clause with a hesitation in it, not three clauses, so both commas go with the
# "uh".  Leaving them behind produced "This is, a test.", which reads worse than
# the raw transcript did.
_DCT_FILLER_RE = _dct_re.compile(
    r"(?:,\s*)?(?<![\w'])(?:" + "|".join(_DCT_FILLER_WORDS) + r")(?![\w'])\s*,?\s*",
    _dct_re.IGNORECASE)
_DCT_PHRASE_RE = _dct_re.compile(
    r"(?:,\s*)?(?<![\w'])(?:"
    + "|".join(_p.replace(" ", r"\s+") for _p in _DCT_FILLER_PHRASES)
    + r")(?![\w'])\s*,?\s*", _dct_re.IGNORECASE)

# A self-correction marker: everything before it in the clause was a false start.
_DCT_MARKER_RE = _dct_re.compile(
    r"(?<![\w'])(?:no,?\s+wait|wait,?\s+no|actually,?\s+no|no,?\s+sorry|"
    r"scratch\s+that|sorry,?\s+i\s+meant|i\s+meant|i\s+mean|or\s+rather)"
    r"(?![\w'])[\s,]*", _dct_re.IGNORECASE)

_DCT_REPEAT_RE = _dct_re.compile(r"(?<![\w'])(\w+)(\s+\1)+(?![\w'])", _dct_re.IGNORECASE)
_DCT_SENT_RE = _dct_re.compile(r"[^.!?]+(?:[.!?]+|$)")
_DCT_SPACE_BEFORE_PUNCT_RE = _dct_re.compile(r"\s+([,.;:!?])")
_DCT_DUP_PUNCT_RE = _dct_re.compile(r"([,;:])(\s*[,;:])+")


def _dct_norm(text):
    """Whitespace only.  Runs first and last so every later rule can assume
    single spaces and no stray control characters."""
    s = "".join(" " if (ord(c) < 0x20 or ord(c) == 0x7F) else c for c in str(text or ""))
    return " ".join(s.split())


def _dct_words(s):
    return [w for w in s.split(" ") if w]


def _dct_bare(w):
    return w.strip(",.;:!?\"'()[]").lower()


def _dct_splice(prev_words, corr_words):
    """Fold a spoken self-correction into what it corrects.

    "a test of the local dictation system" + "the local dictation pipeline"
    -> "a test of the local dictation pipeline"

    The correction usually RE-SAYS the start of the part it is replacing, so the
    longest prefix of the correction (>= 2 words) that also occurs in the
    original marks where the false start began.  The LAST occurrence wins: in
    "the local file, no wait, the local folder" the anchor must be the second
    "the local", not the first.

    With no anchor there is nothing to align on, so the correction replaces the
    clause back to the previous comma, and failing that the whole thing — that
    is what a self-correction MEANS, and the raw transcript is one Undo away in
    the app that received the text."""
    if not corr_words:
        return prev_words
    if not prev_words:
        return corr_words
    lp = [_dct_bare(w) for w in prev_words]
    lc = [_dct_bare(w) for w in corr_words]
    # Longest anchor first, down to a single word: a speaker who restarts a
    # phrase almost always repeats at least its first word, and taking the LAST
    # occurrence of a one-word anchor cuts at the restart rather than at the
    # first time that word happened to appear.
    for k in range(min(len(lc), 6), 0, -1):
        needle = lc[:k]
        for i in range(len(lp) - k, -1, -1):
            if lp[i:i + k] == needle:
                return prev_words[:i] + corr_words
    for i in range(len(prev_words) - 1, -1, -1):
        if prev_words[i].endswith(","):
            return prev_words[:i + 1] + corr_words
    return corr_words


def _dct_self_correct(text):
    """Two passes: a marker INSIDE a sentence corrects the clause before it, and
    a sentence that STARTS with a marker corrects the sentence before it."""
    sentences = [m.group(0).strip() for m in _DCT_SENT_RE.finditer(text)]
    sentences = [s for s in sentences if s]
    if not sentences:
        return text

    fixed = []
    for sent in sentences:
        end = ""
        core = sent
        while core and core[-1] in ".!?":
            end = core[-1] + end
            core = core[:-1]
        core = core.strip()

        starts_with_marker = False
        m = _DCT_MARKER_RE.match(core)
        if m:
            starts_with_marker = True
            core = core[m.end():].strip()

        # inline: fold each remaining marker into what came before it
        while True:
            m = _DCT_MARKER_RE.search(core)
            if not m:
                break
            left = core[:m.start()].strip()
            right = core[m.end():].strip()
            if not right:
                core = left.rstrip(" ,;:")
                break
            core = " ".join(_dct_splice(_dct_words(left), _dct_words(right)))

        if starts_with_marker and fixed:
            prev_core, prev_end = fixed[-1]
            merged = " ".join(_dct_splice(_dct_words(prev_core), _dct_words(core)))
            fixed[-1] = (merged, prev_end or end)
        elif core:
            fixed.append((core, end))
    return " ".join((c + e).strip() for c, e in fixed if c).strip()


def _dct_strip_fillers(text):
    s = _DCT_PHRASE_RE.sub(" ", text)
    s = _DCT_FILLER_RE.sub(" ", s)
    return _dct_norm(s)


def _dct_collapse_repeats(text):
    def keep_first(m):
        return m.group(1)
    return _DCT_REPEAT_RE.sub(keep_first, text)


def _dct_apply_dictionary(text, mapping):
    """Word-boundary, case-insensitive replacement.  Longest key first, so a
    dictionary holding both "hermes" and "hermes assistant" cannot have the
    short key eat the long one."""
    if not mapping:
        return text
    out = text
    for src in sorted(mapping, key=len, reverse=True):
        dst = mapping[src]
        if not src.strip():
            continue
        left = r"(?<![\w'])" if src[0].isalnum() else ""
        right = r"(?![\w'])" if src[-1].isalnum() else ""
        try:
            out = _dct_re.sub(left + _dct_re.escape(src) + right,
                              dst.replace("\\", "\\\\"), out, flags=_dct_re.IGNORECASE)
        except _dct_re.error:
            continue
    return out


def _dct_tidy_punct(text):
    s = _DCT_DUP_PUNCT_RE.sub(r"\1", text)
    s = _DCT_SPACE_BEFORE_PUNCT_RE.sub(r"\1", s)
    s = _dct_re.sub(r"^[\s,;:]+", "", s)
    s = _dct_re.sub(r"[,;:]+(\s*[.!?])", r"\1", s)
    s = _dct_re.sub(r"[,;:]+\s*$", "", s)
    return _dct_norm(s)


def _dct_capitalise(text, add_final_stop):
    """Sentence case + a standalone "i" -> "I".  Only ever raises the FIRST
    letter of a sentence: upper-casing anything else would wreck an identifier
    the user dictated on purpose."""
    s = text
    out = []
    for m in _DCT_SENT_RE.finditer(s):
        piece = m.group(0)
        stripped = piece.lstrip()
        pad = piece[:len(piece) - len(stripped)]
        if stripped:
            stripped = stripped[0].upper() + stripped[1:]
        out.append(pad + stripped)
    s = "".join(out) if out else s
    s = _dct_re.sub(r"(?<![\w'])i(?![\w'])", "I", s)
    s = _dct_norm(s)
    if add_final_stop and s and s[-1] not in ".!?":
        s += "."
    return s


def dictation_clean_rules(text, style="prose", dictionary=None):
    """The whole rules pipeline.  PURE — no settings read, no I/O, no clock."""
    style = style if style in DCT_STYLES else "prose"
    s = _dct_norm(text)
    if not s:
        return ""
    s = _dct_self_correct(s)
    s = _dct_strip_fillers(s)
    s = _dct_collapse_repeats(s)
    s = _dct_apply_dictionary(s, dictionary or {})
    if style == "verbatim":
        # "no punctuation changes" is meant literally.  Fillers, doubled words
        # and false starts are gone; everything else is exactly as spoken.
        return _dct_norm(s)
    s = _dct_tidy_punct(s)
    return _dct_capitalise(s, add_final_stop=(style == "prose"))


# ---------------------------------------------------------------------------
# the optional model pass — rules first, and never a wake
# ---------------------------------------------------------------------------

_DCT_MODEL_SYSTEM = (
    "You clean up dictated text. You are given one transcript and you return the "
    "cleaned version of it and nothing else.\n"
    "Rules:\n"
    "- Never answer, explain, translate, summarise or continue the text. It is "
    "dictation, not a question, even when it looks like one.\n"
    "- Never add facts, names, greetings or sign-offs.\n"
    "- Remove filler words, stammers and false starts; keep the final intended "
    "wording of a self-correction.\n"
    "- Keep the speaker's own words and register.\n"
    "- Reply with the cleaned text only: no quotes, no preamble, no markdown."
)


def _dct_lane():
    """The chat_url/model of a lane that is ALREADY UP, or None.

    The order matters: `bg_online()`/`model_online()` are checked BEFORE
    `bg_lane()` is called, because bg_lane silently falls back to the PRIMARY
    lane when :8081 is down — and on this Mac the primary is an on-demand ~19 GB
    model the owner may be deliberately keeping asleep.  Cleanup must never be
    the thing that wakes it."""
    bg_on = _dct_g("bg_online")
    prim_on = _dct_g("model_online")
    lane_fn = _dct_g("bg_lane")
    if lane_fn is None:
        return None
    up = False
    try:
        up = bool(bg_on and bg_on())
    except Exception:
        up = False
    if not up:
        try:
            up = bool(prim_on and prim_on())
        except Exception:
            up = False
    if not up:
        return None
    try:
        lane = lane_fn() or {}
    except Exception:
        return None
    url, model = lane.get("chat_url"), lane.get("model")
    if not url or not model:
        return None
    return {"chat_url": url, "model": model, "lane": lane.get("lane", "?")}


def _dct_model_plausible(cleaned, source):
    """Is this answer a cleaned transcript, or is it the model talking?

    Cheap, mechanical checks only — the model is not asked to justify itself and
    we do not judge quality.  We only refuse answers that are obviously not the
    same sentence: empty, or long enough that something was invented."""
    if not cleaned:
        return False
    if len(cleaned) > max(120, int(len(source) * 1.6) + 60):
        return False
    if "```" in cleaned:
        return False
    return True


def _dct_model_clean(text, style, timeout=DCT_MODEL_TIMEOUT):
    """(cleaned_text | None, note).  Any failure returns None and the caller
    keeps the rules result — that is the whole contract."""
    lane = _dct_lane()
    if lane is None:
        return None, "no model lane is online"
    hint = DCT_STYLE_HINT.get(style, DCT_STYLE_HINT["prose"])
    body = _dct_json.dumps({
        "model": lane["model"],
        "messages": [
            {"role": "system", "content": _DCT_MODEL_SYSTEM + "\n" + hint},
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
        "max_tokens": min(600, max(64, len(text) // 2 + 64)),
        "stream": False,
    }).encode("utf-8")
    try:
        req = _dct_urlrequest.Request(lane["chat_url"], data=body,
                                      headers={"Content-Type": "application/json"})
        with _dct_urlrequest.urlopen(req, timeout=timeout) as r:
            resp = _dct_json.loads(r.read().decode("utf-8", "replace"))
        cleaned = (((resp.get("choices") or [{}])[0].get("message") or {})
                   .get("content") or "").strip()
    except Exception as e:
        return None, "model pass skipped (%s)" % type(e).__name__
    cleaned = cleaned.strip().strip('"').strip()
    if not _dct_model_plausible(cleaned, text):
        return None, "model answer refused (not a cleaned transcript)"
    return _dct_norm(cleaned), "cleaned on the %s lane" % lane["lane"]


# ---------------------------------------------------------------------------
# the store — heartbeat + history.  0600 from birth; it can hold dictated text.
# ---------------------------------------------------------------------------

def _dct_load():
    try:
        with open(DCT_STORE) as f:
            st = _dct_json.load(f)
    except (OSError, ValueError):
        st = {}
    if not isinstance(st, dict):
        st = {}
    st.setdefault("status", {})
    st.setdefault("history", [])
    if not isinstance(st["history"], list):
        st["history"] = []
    return st


def _dct_save(st):
    """Write the store.  Returns True on success, False on failure.

    A bare `except OSError: log and move on` used to mean callers had no way
    to tell "written" from "silently not written" — in particular
    _dct_forget_text() (below), whose caller is the keep_history=off settings
    route: a failed write there could report "Saved" to the user while the
    text it claimed to have removed was still sitting on disk."""
    tmp = DCT_STORE + ".tmp"
    try:
        fd = _dct_os.open(tmp, _dct_os.O_WRONLY | _dct_os.O_CREAT | _dct_os.O_TRUNC, 0o600)
        with _dct_os.fdopen(fd, "w") as f:
            _dct_json.dump(st, f, indent=1)
        _dct_os.replace(tmp, DCT_STORE)
        return True
    except OSError as e:
        _dct_log("could not write the store: %s" % e)
        return False


def _dct_prune(history, days):
    if days <= 0:
        return []
    cutoff = _dct_time.time() - days * 86400
    rows = [r for r in history if isinstance(r, dict) and _dct_num(r.get("ts")) >= cutoff]
    return rows[-DCT_HISTORY_MAX:]


def _dct_forget_text():
    """Drop the stored text of every row, keeping the counts.  Called the moment
    keep_history goes off.  Returns True unless there was text to remove AND
    the write failed — the settings route surfaces that instead of reporting
    a clean "Saved" while the text is still on disk."""
    with _dct_lock:
        st = _dct_load()
        changed = False
        for row in st["history"]:
            if isinstance(row, dict) and row.pop("text", None) is not None:
                changed = True
        if changed:
            return _dct_save(st)
    return True


def _dct_record(row, settings):
    """Append one dictation row to the store.  Returns True when it was written.

    THE keep_history DECISION IS MADE HERE, at persistence time — not from the
    snapshot _dct_finish() took before the (possibly multi-second) model pass.
    Turning history off mid-dictation used to scrub the store via
    _dct_forget_text() and then have the in-flight request append a brand-new
    row carrying the transcript, so an explicit privacy choice was undone by a
    request that was already running (2026-09-10 audit A07).  `settings` is
    still accepted, but only as the fallback for the retention window; the
    privacy flag is re-read from the live settings every time.

    LOCK ORDER — the one rule this module has, and the reason the settings read
    is on the line BEFORE the `with`: **settings first, then _dct_lock, never
    the other way round.**  dictation_set_settings() takes server.py's
    _state_lock (inside settings_update), releases it, and only then calls
    _dct_forget_text(), which takes _dct_lock.  Reading settings before
    entering _dct_lock therefore keeps both paths in the same order and no
    deadlock is possible.  Nothing in this module may take _state_lock while
    holding _dct_lock."""
    live = dictation_settings()          # settings read — OUTSIDE _dct_lock
    keep = bool(live.get("keep_history"))
    try:
        days = int(live.get("history_days", settings.get("history_days")))
    except (TypeError, ValueError):
        days = DCT_DEFAULTS["history_days"]
    if not keep:
        # The counts stay (they are what the "today" numbers are made of); the
        # words do not.
        row = {k: v for k, v in row.items() if k != "text"}
    with _dct_lock:
        st = _dct_load()
        st["history"] = _dct_prune(st["history"] + [row], days)
        return _dct_save(st)


def _dct_normalise_status(raw):
    raw = raw if isinstance(raw, dict) else {}

    def grant(k):
        v = str(raw.get(k) or "unknown").strip().lower()
        return v if v in ("granted", "denied", "unknown") else "unknown"

    return {
        "running": bool(raw.get("running", True)),
        "state": str(raw.get("state") or "idle")[:24],
        "mic": grant("mic"),
        "accessibility": grant("accessibility"),
        "speech": grant("speech"),
        "engine": str(raw.get("engine") or "")[:48],
        "hotkey": str(raw.get("hotkey") or "")[:40],
        "note": str(raw.get("note") or "")[:200],
        "version": str(raw.get("version") or "")[:32],
        "ts": _dct_time.time(),
    }


def dictation_status():
    """The last heartbeat plus its age.  `running` is the HELPER's claim ANDed
    with a fresh heartbeat: a process that was killed never gets to say so, and
    a stale claim is worse than none."""
    st = _dct_load()
    status = st.get("status") if isinstance(st.get("status"), dict) else {}
    ts = _dct_num(status.get("ts"))
    age = (_dct_time.time() - ts) if ts else None
    fresh = age is not None and age <= DCT_STALE_S
    return {
        "status": status,
        "age_s": round(age, 1) if age is not None else None,
        "running": bool(status.get("running")) and fresh,
        "stale": (ts > 0 and not fresh),
        "ever": ts > 0,
    }


# ---------------------------------------------------------------------------
# today's numbers
# ---------------------------------------------------------------------------

def _dct_today_bounds():
    now = _dct_datetime.datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


def _dct_today(history):
    start = _dct_today_bounds()
    rows = [r for r in history
            if isinstance(r, dict) and _dct_num(r.get("ts")) >= start]
    words = sum(int(r.get("words") or 0) for r in rows)
    lat = [float(r.get("total_ms")) for r in rows
           if isinstance(r.get("total_ms"), (int, float))]
    return {
        "dictations": len(rows),
        "words": words,
        "mean_ms": int(round(sum(lat) / len(lat))) if lat else None,
    }


def _dct_recent(history, keep_history, limit=8):
    out = []
    for row in reversed(history[-limit * 3:]):
        if not isinstance(row, dict):
            continue
        item = {
            "ts": row.get("ts"),
            "app": row.get("app") or "",
            "bundle": row.get("bundle") or "",
            "words": row.get("words") or 0,
            "mode": row.get("mode") or "",
            "style": row.get("style") or "",
            "ms": row.get("total_ms"),
            "engine": row.get("engine") or "",
        }
        if keep_history and row.get("text"):
            item["text"] = str(row.get("text"))[:400]
        out.append(item)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# install / build state
# ---------------------------------------------------------------------------

def _dct_tilde(path):
    home = globals().get("HOME") or _dct_os.path.expanduser("~")
    return ("~" + path[len(home):]) if path.startswith(home) else path


def dictation_install():
    built = _dct_os.path.isdir(DCT_BUNDLE)
    binary = _dct_os.path.join(DCT_BUNDLE, "Contents", "MacOS", "HermesDictation")
    live = dictation_status()
    return {
        "ok": True,
        "built": built,
        "runnable": built and _dct_os.path.exists(binary),
        "bundle": _dct_tilde(DCT_BUNDLE),
        "build_cmd": "bash app/build-dictation.sh",
        "launch_cmd": 'open "app/build/Hermes Dictation.app"',
        "build_script": _dct_tilde(DCT_BUILD_SCRIPT),
        "running": live["running"],
        "age_s": live["age_s"],
        # Said once, here, so every surface repeats the same sentence.
        "rebuild_note": ("The helper is ad-hoc signed, so rebuilding it resets its "
                         "Microphone and Accessibility permissions — grant them "
                         "again after every rebuild."),
        "launch_note": ("Launch it yourself (double-click, or Start at login in its "
                        "menu). A GUI launch is what makes it the process macOS "
                        "asks you about when it wants the microphone."),
    }


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def _dct_finish(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    raw = str(body.get("text") or "")[:DCT_MAX_CHARS]
    t0 = _dct_time.time()
    settings = dictation_settings()
    bundle = str(body.get("app_bundle") or "")[:160]
    app_name = str(body.get("app_name") or "")[:120]
    engine = str(body.get("engine") or "")[:48]
    try:
        duration = int(body.get("duration_ms") or 0)
    except (TypeError, ValueError):
        duration = 0
    style = _dct_style_for(bundle, app_name, settings)

    if not raw.strip():
        return {"ok": False, "text": "", "mode": "off", "ms": 0,
                "error": "empty transcript"}, 400

    mode = settings["cleanup"]
    note = ""
    if mode == "off":
        text, used = _dct_norm(raw), "off"
    else:
        text = dictation_clean_rules(raw, style, settings["dictionary"])
        used = "rules"
        if mode == "model":
            better, note = _dct_model_clean(text, style)
            if better:
                text, used = better, "model"
    if not text:
        # Every rule together must never turn speech into silence.
        text, used, note = _dct_norm(raw), "off", "cleanup emptied the transcript"

    ms = int(round((_dct_time.time() - t0) * 1000))
    row = {
        "ts": _dct_time.time(),
        "app": app_name,
        "bundle": bundle,
        "words": len([w for w in text.split() if w]),
        "mode": used,
        "style": style,
        "engine": engine,
        "cleanup_ms": ms,
        "total_ms": duration + ms if duration else ms,
    }
    if settings["keep_history"]:
        row["text"] = text[:2000]
    try:
        # _dct_record re-reads keep_history itself: if the owner turned history
        # off while the (slow) model pass above was running, the row is stored
        # WITHOUT its text even though `settings` above still says otherwise.
        if not _dct_record(row, settings):
            _dct_log("history write failed (store not written)")
    except Exception as e:      # a full disk must not cost the user their words
        _dct_log("history write failed: %s: %s" % (type(e).__name__, e))
    return {"ok": True, "text": text, "raw": raw, "mode": used, "style": style,
            "ms": ms, "note": note}


def _dct_settings_get(ctx):
    return {"ok": True, **dictation_settings()}


def _dct_settings_post(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    return {"ok": True, **dictation_set_settings(body)}


def _dct_status_post(ctx):
    body = ctx.body if isinstance(ctx.body, dict) else {}
    with _dct_lock:
        st = _dct_load()
        st["status"] = _dct_normalise_status(body)
        _dct_save(st)
    return {"ok": True}


def _dct_get(ctx):
    settings = dictation_settings()
    with _dct_lock:
        st = _dct_load()
        pruned = _dct_prune(st["history"], settings["history_days"])
        if len(pruned) != len(st["history"]):
            st["history"] = pruned
            _dct_save(st)
        history = list(pruned)
    live = dictation_status()
    return {
        "ok": True,
        "running": live["running"],
        "stale": live["stale"],
        "ever": live["ever"],
        "age_s": live["age_s"],
        "status": live["status"],
        "settings": settings,
        "today": _dct_today(history),
        "recent": _dct_recent(history, settings["keep_history"]),
        "install": dictation_install(),
        "styles": list(DCT_STYLES),
        "modes": list(DCT_MODES),
    }


def _dct_install_get(ctx):
    return dictation_install()


register_get("/api/dictation", _dct_get)                      # noqa: F821
register_get("/api/dictation/settings", _dct_settings_get)    # noqa: F821
register_get("/api/dictation/install", _dct_install_get)      # noqa: F821
register_post("/api/dictation/finish", _dct_finish)           # noqa: F821
register_post("/api/dictation/settings", _dct_settings_post)  # noqa: F821
register_post("/api/dictation/status", _dct_status_post)      # noqa: F821
