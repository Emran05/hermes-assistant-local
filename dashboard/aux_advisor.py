# aux_advisor.py — model advisor, part 2 (round 3, cluster C2): which plan
# fits this Mac and what the owner wants to do with it, and the routes.
#
# Design: docs/plans/model-advisor-2026-10.md §4 (questionnaire + storage),
# §6 (catalog), §7 (ranking), §9 (API), §10 (tests).  Part 1 is
# aux_advisor_hw.py (detection + the estimator); this module RANKS what that
# one estimates and never re-derives a number of its own.
#
# WHAT THIS MODULE IS
#   * The catalog seam (§6.1).  _ADV_EXTRA holds the FULL roster shape of any
#     curated id that is neither a server.py seed nor in the onboarding
#     catalog; _ADV_BY_ID is the id -> shape map server.py's
#     _curated_model_ids(), add_model() and _catalog_dl_gb() consult BY NAME
#     at call time.  Ids that are seeds are skipped (the seed shape is the
#     re-verified one), and every shape is locked down at the source (no
#     trust_remote_code, no code or pickle pattern, no `verified` claim).
#   * The questionnaire answers (§4): validation (strict types, 400 on any
#     failure) and storage under settings.json `advisor` ONLY, through
#     settings_update (the A01 rule).  Not exported by Config-as-Code (D12:
#     CFG_SETTINGS_ALLOW lists list-valued keys only).
#   * The ranking (§7): every (primary, background) pair over the catalog,
#     exclusions with a reason, the score, the verified-first Recommended
#     slot, up to two alternatives with different primaries, relaxation of a
#     use case the Mac cannot meet (docs -> code -> agent), "what you run
#     now", and the deterministic "why" templates.
#   * Routes: GET /api/advisor/state, GET /api/advisor/rank (no writes),
#     POST /api/advisor/answers.
#   * A guarded models_payload() rebind adding per-row `advice` and a
#     top-level `advisor` block (no port probe, _adv_hw() once per call, cached
#     30 s, never raises).
#   * _adv_recommend_compat(): _onb_recommend's exact key set (+ `advisor`),
#     the seam the onboarding cluster delegates to (_onb_tier stays the
#     fallback whenever the advisor cannot answer).
#
# UNCENSORED POLICY (owner decision D4): a row is uncensored when server.py's
# _roster_uncensored(m) says so (sticky to the curated seed), or the advisor
# facts / an _ADV_EXTRA shape do.  Such a row is NEVER the Recommended slot,
# never in a compat `alt`, and appears at all — as an alternative only — when
# `uncensored` is among the uses AND the acknowledgement is STORED
# (settings.json advisor.uncensored_ack, written by POST /api/advisor/answers
# — an unsaved ranking cannot assert it).  The hard gate is server.py's
# POST /api/models/switch (409 needs_ack); this module only advises.
#
# WHAT IT NEVER DOES
#   * Switch, download, activate or restart anything; write any file but
#     settings.json's `advisor` key (POST /api/advisor/answers only).
#   * Touch :8080/:8081/:9119 (no model_online / bg_online / mlx_admission),
#     or the network.  Detection is aux_advisor_hw's (cached commands).
#
# LOAD ORDER: server.py execs aux_*.py SORTED, and this file is the FIRST of
# them ('aux_advisor.py' < 'aux_advisor_hw.py' < 'aux_agent.py' ...).  So
# every name from aux_advisor_hw / aux_onboarding / aux_promotion /
# aux_watchtower / aux_context / aux_promptbudget is resolved BY NAME AT CALL
# TIME through _adv_g(), never captured at module load, and this module
# defines no name aux_advisor_hw.py defines (t_advisor_hw.py fails on a
# clash).  Private helpers are `_advr_*` / `_ADVR_*`.  No `from datetime
# import datetime` (CLAUDE.md aux gotcha).

import copy as _advr_copy
import json as _advr_json
import math as _advr_math
import os as _advr_os
import re as _advr_re
import sys as _advr_sys
import time as _advr_time

_ADVR_VERSION = 1


def _adv_g(name, default=None):
    """A server.py / sibling-aux global BY NAME AT CALL TIME (see LOAD ORDER)."""
    return globals().get(name, default)


# --------------------------------------------------------------------------
# the catalog seam (§6.1)
# --------------------------------------------------------------------------
# The design's two new curated MoE entries (the stock Qwen3.6-35B-A3B and the
# Youssofal Heretic build) became server.py SEEDS in round-3 wave A
# (a-roster), with re-verified shapes, and froggeric's Heretic is NOT curated
# (owner decision D4(b)).  So nothing is left for this list today: it stays as
# the one place a future advisor-only curated id is added (full roster shape,
# `size_gb`, and a facts record in aux_advisor_hw.py's _ADV_FACTS — the
# catalog test asserts both).  An entry that later becomes a seed is skipped
# automatically.
_ADV_EXTRA = ()

_ADVR_REPO_RE = _advr_re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?"
                                 r"/[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?$")
# B03/F01: a curated shape never asks for code or pickle-format weights
_ADVR_FORBIDDEN_PATTERN_RE = _advr_re.compile(
    r"(\.py[co]?|\.bin|\.pt|\.pth|\.ckpt|\.pkl|\.pickle|\.so|\.dylib|\.sh)$", _advr_re.I)
_ADVR_EXTRA_DROP = ("trust_remote_code", "verified")
_ADVR_BACKENDS = ("mlx_vlm", "mlx_lm")


def _advr_extra_ok(m):
    """(ok, reason) for one _ADV_EXTRA shape — the lockdown at the source."""
    if not isinstance(m, dict):
        return False, "not an object"
    mid = m.get("id")
    if not isinstance(mid, str) or not _ADVR_REPO_RE.fullmatch(mid) or ".." in mid or "--" in mid:
        return False, "id is not an org/name repo id"
    if m.get("trust_remote_code") is True:
        return False, "trust_remote_code is never curated"
    if not isinstance(m.get("label"), str) or not m["label"].strip():
        return False, "no label"
    if m.get("backend") not in _ADVR_BACKENDS:
        return False, "backend must be one of %s" % ", ".join(_ADVR_BACKENDS)
    if not isinstance(m.get("template_args"), dict):
        return False, "template_args must be an object"
    sz = m.get("size_gb")
    if isinstance(sz, bool) or not isinstance(sz, (int, float)) or not sz > 0:
        return False, "size_gb (the verified download size) is required"
    for key in ("allow_patterns", "ignore_patterns"):
        pats = m.get(key)
        if pats is None:
            continue
        if not isinstance(pats, list) or not all(isinstance(p, str) and p for p in pats):
            return False, "%s must be a list of strings" % key
        if key == "allow_patterns" and any(_ADVR_FORBIDDEN_PATTERN_RE.search(p) for p in pats):
            return False, "allow_patterns names code or pickle files"
    return True, ""


def _advr_extra_by_id(extra=None, seeds=None):
    """{id: shape} for the advisor-only curated ids: `extra` (default
    _ADV_EXTRA) minus every id that is a server.py seed (`seeds`, default
    _SEED_MODELS) or in the onboarding catalog when that is already loaded
    (at server.py's exec it is not — aux_onboarding sorts later — and every
    caller consults _ONB_BY_ID first anyway), each shape validated
    (_advr_extra_ok) and deep-copied without trust_remote_code / verified.
    A refused shape is left out with one stderr line, never raised."""
    extra = _ADV_EXTRA if extra is None else extra
    seeds = (_adv_g("_SEED_MODELS") or ()) if seeds is None else seeds
    skip = {m.get("id") for m in seeds if isinstance(m, dict)}
    skip |= set((_adv_g("_ONB_BY_ID") or {}).keys())
    out = {}
    for m in extra or ():
        ok, why = _advr_extra_ok(m)
        if not ok:
            print("[advisor] _ADV_EXTRA entry %r refused: %s"
                  % ((m or {}).get("id") if isinstance(m, dict) else m, why),
                  file=_advr_sys.stderr, flush=True)
            continue
        if m["id"] in skip or m["id"] in out:
            continue
        row = _advr_json.loads(_advr_json.dumps(m))
        for k in _ADVR_EXTRA_DROP:
            row.pop(k, None)
        out[m["id"]] = row
    return out


_ADV_BY_ID = _advr_extra_by_id()


def _adv_catalog():
    """[(id, static roster shape)] every curated id the advisor may offer, in
    a stable order: server.py seeds, the onboarding catalog, _ADV_BY_ID.  The
    first occurrence of an id wins.  Only ids with advisor facts are listed —
    an id nobody estimated is never ranked."""
    facts = _adv_g("_ADV_FACTS") or {}
    out, seen = [], set()
    for src in (_adv_g("_SEED_MODELS") or (), _adv_g("_ONB_CATALOG") or (),
                list((_adv_g("_ADV_BY_ID") or {}).values())):
        for m in src:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if not isinstance(mid, str) or mid in seen or mid not in facts:
                continue
            seen.add(mid)
            out.append((mid, m))
    return out


def _adv_is_uncensored(mid, row=None):
    """True when ANY source says `mid` is an uncensored (abliterated) build:
    server.py's _roster_uncensored(row) (sticky to the curated seed), the
    advisor facts, or an _ADV_EXTRA shape.  Belt and braces on purpose: a
    wrong False here would put a no-refusals model in the Recommended slot."""
    row = row if isinstance(row, dict) else {"id": mid}
    if row.get("id") is None:
        row = dict(row, id=mid)
    fn = _adv_g("_roster_uncensored")
    try:
        u = bool(fn(row)) if callable(fn) else row.get("uncensored") is True
    except Exception:
        u = row.get("uncensored") is True
    facts = (_adv_g("_ADV_FACTS") or {}).get(mid) or {}
    extra = (_adv_g("_ADV_BY_ID") or {}).get(mid) or {}
    return u or facts.get("uncensored") is True or extra.get("uncensored") is True


def _adv_verified(mid, drills=None):
    """(verified, source, detail) for the tool drill (§7.4).

    A LOCAL drill record (aux_promotion's promotion.json, `drills`) decides
    when there is one: a pass (>= 5 of 6) verifies, a fail REMOVES the
    catalog's reference verification.  Otherwise the facts' reference drill
    verifies.  The roster's own `verified` field is never read: a config
    import can set it (a-roster concern 3), a drill cannot be imported."""
    rec = (drills or {}).get(mid) if isinstance(drills, dict) else None
    if isinstance(rec, dict) and ("pass" in rec or "score" in rec):
        score, of = rec.get("score"), rec.get("of")
        if isinstance(rec.get("pass"), bool):
            ok = rec["pass"]
        else:
            ok = isinstance(score, int) and not isinstance(score, bool) and score >= _ADVR_VERIFY_MIN
        detail = "drill %s/%s on this Mac" % (score if score is not None else "?",
                                             of if of is not None else _ADVR_VERIFY_OF)
        return (True, "here", detail) if ok else (False, "failed-here", "failed the " + detail)
    f = (_adv_g("_ADV_FACTS") or {}).get(mid) or {}
    v = f.get("verified")
    if isinstance(v, dict) and v.get("drill"):
        return True, "reference", "drill %s (reference Mac)" % v.get("drill")
    return False, None, "not drilled"


# --------------------------------------------------------------------------
# the questionnaire (§4)
# --------------------------------------------------------------------------
_ADV_USES = ("chat", "agent", "code", "docs", "vision", "uncensored")
_ADV_PRIORITIES = {"faster": (0.30, 0.70), "balanced": (0.55, 0.45), "best": (0.80, 0.20)}
_ADVR_POWERS = ("ac", "battery")
_ADV_DEFAULT_ANSWERS = {"uses": ["chat", "agent"], "priority": "balanced", "power": None,
                        "uncensored_ack": False}
_ADVR_ANSWER_KEYS = ("uses", "priority", "power", "uncensored_ack")
_ADVR_USE_LABEL = {"chat": "Everyday chat and writing",
                   "agent": "Getting things done on this Mac",
                   "code": "Coding help", "docs": "Long documents and big files",
                   "vision": "Screenshots and images",
                   "uncensored": "Unfiltered research (no refusals)"}

# The acknowledgement copy (§4), served by the state route so the UI and the
# switch gate's confirm quote ONE text.
_ADVR_UNCENSORED_ACK_TEXT = (
    "Uncensored models have had their refusal behaviour removed. They will answer "
    "things the standard model declines, they are no more accurate, and some still "
    "open with disclaimers. Nothing inside the model stops a harmful request. On "
    "this Mac the brake is the approval gate, which still asks before risky actions "
    "such as controlling apps or writing to protected places, and the permission "
    "tiers. The gate is a path denylist, not a sandbox: commands the agent runs in "
    "its terminal are not covered by it. These models are never the default and "
    "appear only as alternatives.")


def _advr_canon_uses(xs):
    """Deduplicated, in the questionnaire's own order (deterministic)."""
    s = set(xs)
    return [u for u in _ADV_USES if u in s]


def _advr_clean_answers(raw):
    """A STORED `advisor` value -> a well-formed answers dict.  Never raises:
    whatever does not validate falls back to the default for that field (a
    hand-edited settings.json must not take the advisor down)."""
    raw = raw if isinstance(raw, dict) else {}
    out = _advr_copy.deepcopy(_ADV_DEFAULT_ANSWERS)
    uses = raw.get("uses")
    if isinstance(uses, list):
        u = _advr_canon_uses(x for x in uses if isinstance(x, str) and x in _ADV_USES)
        if [x for x in u if x != "uncensored"]:
            out["uses"] = u
    if isinstance(raw.get("priority"), str) and raw["priority"] in _ADV_PRIORITIES:
        out["priority"] = raw["priority"]
    if raw.get("power") in _ADVR_POWERS:
        out["power"] = raw["power"]
    out["uncensored_ack"] = raw.get("uncensored_ack") is True
    if "uncensored" in out["uses"] and not out["uncensored_ack"]:
        out["uses"] = [x for x in out["uses"] if x != "uncensored"] or ["chat", "agent"]
    sa = raw.get("saved_at")
    out["saved_at"] = None
    if isinstance(sa, (int, float)) and not isinstance(sa, bool):
        try:
            # a plausible unix time only: a hand-edited 400-digit integer made
            # isfinite() raise OverflowError, which broke "never raises"
            if _advr_math.isfinite(sa) and 0 < sa < 1e11:
                out["saved_at"] = int(sa)
        except (OverflowError, ValueError):
            pass
    return out


def _adv_answers_validate(body, base=None, require_ack=True):
    """(answers, errors, ignored) for a POSTed body.  PURE.

    MERGE semantics: a key that is absent keeps `base`'s value (base = the
    stored answers, cleaned), so the acknowledgement can be posted on its own.
    Every PRESENT key must validate, strictly (a JSON boolean for
    uncensored_ack — "true" is not true):
      uses            a non-empty list of the six keys, at least one besides
                      `uncensored` (deduplicated; order normalized)
      priority        faster | balanced | best
      power           ac | battery | null (null = "what this Mac is on now")
      uncensored_ack  true | false
    and (require_ack, the POST) the result must satisfy: `uncensored` in uses
    requires uncensored_ack === true.  An unsaved ranking passes
    require_ack=False: it ranks without the uncensored rows instead and says
    so.  Revoking (uncensored_ack false with no `uses` in the body) always
    succeeds and drops the stored `uncensored` use with it.  Unknown keys are
    ignored and reported."""
    if not isinstance(body, dict):
        return None, ["the body must be a JSON object"], []
    out = _advr_clean_answers(base)
    out.pop("saved_at", None)
    errors = []
    if "uses" in body:
        v = body["uses"]
        if not isinstance(v, list) or not v:
            errors.append("uses: a non-empty list of %s" % ", ".join(_ADV_USES))
        else:
            bad = [x for x in v if not isinstance(x, str) or x not in _ADV_USES]
            if bad:
                errors.append("uses: unknown %s (allowed: %s)"
                              % (", ".join(repr(x)[:40] for x in bad[:5]), ", ".join(_ADV_USES)))
            else:
                u = _advr_canon_uses(v)
                if not [x for x in u if x != "uncensored"]:
                    errors.append("uses: pick at least one of chat, agent, code, docs, vision")
                else:
                    out["uses"] = u
    if "priority" in body:
        v = body["priority"]
        if not isinstance(v, str) or v not in _ADV_PRIORITIES:
            errors.append("priority: one of %s" % ", ".join(sorted(_ADV_PRIORITIES)))
        else:
            out["priority"] = v
    if "power" in body:
        v = body["power"]
        if v is not None and v not in _ADVR_POWERS:
            errors.append("power: ac, battery or null")
        else:
            out["power"] = v
    if "uncensored_ack" in body:
        v = body["uncensored_ack"]
        if not isinstance(v, bool):
            errors.append("uncensored_ack: a JSON boolean (true or false)")
        else:
            out["uncensored_ack"] = v
    if "uncensored_ack" in body and out["uncensored_ack"] is False and "uses" not in body \
            and "uncensored" in out["uses"]:
        # Revoking the acknowledgement must always work (it is the safe
        # direction): when the body names no `uses` of its own, the stored
        # `uncensored` use goes with it instead of refusing the revoke.  An
        # explicit contradiction (uses with `uncensored` AND ack false in the
        # same body) is still a 400 below.
        out["uses"] = [x for x in out["uses"] if x != "uncensored"]
    if require_ack and not errors and "uncensored" in out["uses"] \
            and out["uncensored_ack"] is not True:
        errors.append("uses: 'uncensored' needs uncensored_ack: true (the acknowledgement "
                      "is shown first)")
    ignored = sorted(k for k in body if k not in _ADVR_ANSWER_KEYS and k != "saved_at")
    return (None if errors else out), errors, ignored


def _adv_answers_stored():
    """The saved answers (cleaned), or the defaults.  A read, never a write.
    settings.json is read through server.py's get_settings (read_json), so a
    missing OR corrupt file reads as "no answers" — and so as NO
    acknowledgement (fail closed)."""
    gs = _adv_g("get_settings")
    try:
        s = gs() if callable(gs) else {}
    except Exception:
        s = {}
    s = s if isinstance(s, dict) else {}
    return _advr_clean_answers(s.get("advisor"))


class _AdvrAnswersInvalid(Exception):
    """Raised INSIDE settings_update's mutate fn so nothing is written."""

    def __init__(self, errors):
        Exception.__init__(self, "; ".join(errors))
        self.errors = list(errors)


def _adv_answers_save(body):
    """Validate + store under settings.json `advisor` -> (obj, status).

    Validation runs against the stored answers BEFORE the lock (a bad body
    never takes it) and again INSIDE settings_update against the file as it
    is NOW (the merge base may have moved); a body that fails there raises
    out of the mutate fn, so settings_update writes nothing.  Only the
    `advisor` key is touched."""
    ans, errs, ignored = _adv_answers_validate(body, _advr_raw_stored())
    if errs:
        return {"ok": False, "errors": errs}, 400
    su = _adv_g("settings_update")
    if not callable(su):
        return {"ok": False, "error": "settings are unavailable"}, 500
    now = int(_advr_time.time())
    holder = {}

    def _mutate(s):
        cur = s.get("advisor") if isinstance(s, dict) else None
        a, e, _ign = _adv_answers_validate(body, cur)
        if e:
            raise _AdvrAnswersInvalid(e)
        a["saved_at"] = now
        holder["answers"] = a
        s["advisor"] = a

    try:
        su(_mutate)
    except _AdvrAnswersInvalid as e:
        return {"ok": False, "errors": e.errors}, 400
    except OSError as e:
        return {"ok": False, "error": "could not save: %s" % type(e).__name__}, 500
    wc = _adv_g("_widget_cache")
    if isinstance(wc, dict):
        wc.pop("adv_advice", None)          # the model menu re-ranks on its next poll
    out = {"ok": True, "answers": holder.get("answers")}
    if ignored:
        out["ignored"] = ignored
    return out


def _advr_raw_stored():
    gs = _adv_g("get_settings")
    try:
        s = gs() if callable(gs) else {}
    except Exception:
        s = {}
    return (s if isinstance(s, dict) else {}).get("advisor")


# --------------------------------------------------------------------------
# the ranking (§7)
# --------------------------------------------------------------------------
_ADVR_M27 = "mlx-community/Qwen3.8-27B-4bit"
_ADVR_M9 = "mlx-community/Qwen3.5-9B-4bit"
_ADVR_M4 = "mlx-community/Qwen3.5-4B-4bit"
_ADVR_M2 = "mlx-community/Qwen3.5-2B-4bit"
_ADVR_BG_IDS = (_ADVR_M9, _ADVR_M4, _ADVR_M2)     # §7.1: the background lane is one of these
_ADVR_BG_MIN_RAM = 24                              # no background lane below (inherited from _onb_tier)
_ADVR_Q_WEIGHT = {"chat": 1.0, "agent": 1.5, "code": 1.5, "docs": 1.0, "vision": 1.0}
_ADVR_FLOORS = {"agent": 4, "code": 6}             # primary quality floors (§4 Q1)
_ADVR_USABLE_TPS = 8.0         # Recommended + relaxation: a plan this slow is not "usable"
_ADVR_SLOW_TPS = 6.0           # §7.2 "slow": decode at the turn below this ...
_ADVR_SLOW_PREFILL = 180.0     # ... or a cold setup read longer than this (s)
_ADVR_PEN_STEPS = ((20.0, 0.0), (45.0, 1.0), (90.0, 2.0), (150.0, 4.0))   # else 6
_ADVR_PEN_MAX = 6.0
_ADVR_BATTERY_W = 0.25         # w_battery on battery; the other weights x0.75
_ADVR_BATTERY_SCALE = 0.75
_ADVR_BG_BONUS = 0.8           # x q_bg.chat / 7, halved when the lane model is unverified
_ADVR_TIGHT_PENALTY = 1.0
_ADVR_RELAX_ORDER = ("docs", "code", "agent")
_ADVR_MAX_ALTS = 2
_ADVR_VERIFY_MIN = 5           # a local drill pass: >= 5 of 6 (aux_promotion PRO_THRESHOLD)
_ADVR_VERIFY_OF = 6
_ADVR_WHY_MAX = 3              # an opening clause + at most two more (§7.5)

_ADVR_RELAX_TEXT = {
    "docs": "No recommendable model runs long documents (128K-token turns) at 8 tok/s or "
            "more here; sized for normal turns instead.",
    "code": "Coding needs the 9B or larger at a 40K-token turn, which no recommendable model "
            "runs at 8 tok/s here; sized for chat and everyday tasks instead.",
    "agent": "Tool use needs a bigger model than this Mac can hold; Hermes runs chat-first "
             "here.",
}
_ADVR_UNVERIFIED_TEXT = ("Not yet verified for tools: run the drill (Settings › Agent "
                         "& Models › Evals).")
_ADVR_DOCS_NOTE = ("Long documents need a 128K-token context window: raise it in Settings "
                   "› Agent & Models › Context. The advisor never changes it.")
_ADVR_BG_NOTE = ("The background lane runs briefings and news; with both off you can skip "
                 "it.")
# E95 (2026-11-09 audit): the lane is OFF after install and nothing in the
# dashboard starts it (mlx-server-bg.sh needs a fresh model-start-ok-bg that
# only the owner mints). It counts as able to run only on file evidence: that
# token, or the lane's own log written within this window.
_ADVR_LANE_RECENT_S = 48 * 3600.0


def _advr_gb(x):
    """'36' / '4.5' — GB the way the plan lines quote them."""
    if not isinstance(x, (int, float)):
        return "?"
    return "%d" % int(round(x)) if x >= 10 else ("%.1f" % x).rstrip("0").rstrip(".")


def _advr_secs(s):
    if not isinstance(s, (int, float)):
        return "?"
    if s < 90:
        return "%d s" % max(1, int(round(s)))
    return "%d min" % int(round(s / 60.0))


def _advr_ctx_label(tokens):
    if not isinstance(tokens, (int, float)) or tokens <= 0:
        return "typical"
    k = tokens / 1000.0 if tokens % 1024 else tokens / 1024.0
    return "%dK-token" % int(round(k))


def _advr_tps(x):
    return "%d" % int(round(x)) if isinstance(x, (int, float)) else "?"


def _advr_short_label(mid):
    """'9B' / '27B' / '35B-A3B' for the "the 9B lane ..." clause."""
    f = (_adv_g("_ADV_FACTS") or {}).get(mid) or {}
    lab = f.get("label") or mid.split("/")[-1]
    m = _advr_re.search(r"(\d+B(?:-A\d+B)?)", lab)
    return m.group(1) if m else lab


def _advr_label(mid, row=None):
    if isinstance(row, dict) and isinstance(row.get("label"), str) and row["label"].strip():
        return row["label"].strip()[:80]
    f = (_adv_g("_ADV_FACTS") or {}).get(mid) or {}
    return f.get("label") or mid.split("/")[-1]


def _advr_pen(prefill_s, plugged):
    p = _ADVR_PEN_MAX
    if isinstance(prefill_s, (int, float)):
        for cap, v in _ADVR_PEN_STEPS:
            if prefill_s <= cap:
                p = v
                break
    return p / 2.0 if plugged else p


def _advr_quality(f, uses):
    q = (f or {}).get("quality") or {}
    us = [u for u in uses if u in _ADVR_Q_WEIGHT] or ["chat"]
    den = sum(_ADVR_Q_WEIGHT[u] for u in us)
    return sum(float(q.get(u) or 0) * _ADVR_Q_WEIGHT[u] for u in us) / den


def _advr_speed(decode_turn):
    if not isinstance(decode_turn, (int, float)) or decode_turn <= 0:
        return 0.0
    return 10.0 * min(1.0, max(0.0, _advr_math.log2(decode_turn / 8.0) / 3.0))


def _advr_effective(answers, hw):
    """The answers as the ranking uses them: (uses, priority, power, notes,
    unc_enabled).  `uncensored` counts only with answers.uncensored_ack ===
    true (the routes always pass the STORED value); power None = the source
    this Mac is on now; a desktop is always plugged in (§4 Q3)."""
    a = answers if isinstance(answers, dict) else _ADV_DEFAULT_ANSWERS
    raw_uses = a.get("uses") if isinstance(a.get("uses"), list) else _ADV_DEFAULT_ANSWERS["uses"]
    uses = _advr_canon_uses(x for x in raw_uses if isinstance(x, str) and x in _ADV_USES)
    if not [x for x in uses if x != "uncensored"]:
        uses = list(_ADV_DEFAULT_ANSWERS["uses"]) + [x for x in uses if x == "uncensored"]
    notes = []
    ack = a.get("uncensored_ack") is True
    unc_on = "uncensored" in uses and ack
    if "uncensored" in uses and not ack:
        notes.append("Uncensored options appear only after the acknowledgement.")
    uses = [x for x in uses if x != "uncensored"]
    pri = a.get("priority") if a.get("priority") in _ADV_PRIORITIES else "balanced"
    hw = hw or {}
    pw = hw.get("power") or {}
    laptop = hw.get("form") in ("laptop", "laptop_fanless") or pw.get("has_battery") is True
    if not laptop:
        power = "ac"
    elif a.get("power") in _ADVR_POWERS:
        power = a["power"]
    else:
        power = "battery" if pw.get("source") == "battery" else "ac"
    return uses, pri, power, notes, unc_on


def _advr_weights(priority, power):
    wq, ws = _ADV_PRIORITIES.get(priority) or _ADV_PRIORITIES["balanced"]
    if power == "battery":
        return wq * _ADVR_BATTERY_SCALE, ws * _ADVR_BATTERY_SCALE, _ADVR_BATTERY_W
    return wq, ws, 0.0


class _AdvrCtx(object):
    """Per-ranking caches: one estimate per (id, lane, uses), read once."""

    def __init__(self, hw, limits, prefix, measured, drills, bg_wanted, downloaded,
                 power, priority, unc_on, catalog):
        self.hw, self.limits = hw, limits
        self.limit = limits.get("limit_gb")
        self.prefix, self.measured = prefix, measured
        self.drills, self.bg_wanted, self.downloaded = drills, bg_wanted, downloaded
        self.power, self.priority, self.unc_on = power, priority, unc_on
        self.catalog = catalog
        self.rows = dict(catalog)
        self.facts = _adv_g("_ADV_FACTS") or {}
        self.est = {}
        self.unc = {mid: _adv_is_uncensored(mid, row) for mid, row in catalog}
        self.ver = {}

    def verified(self, mid):
        if mid not in self.ver:
            self.ver[mid] = _adv_verified(mid, self.drills)
        return self.ver[mid]

    def recommendable(self, mid):
        """May `mid` take the Recommended slot at all?  Never when uncensored
        (D4); an opt-in `alternative` build only once verified (D3)."""
        if self.unc.get(mid, _adv_is_uncensored(mid, self.rows.get(mid))):
            return False
        role = (self.facts.get(mid) or {}).get("role")
        return role != "alternative" or self.verified(mid)[0]

    def prefix_for(self, mid):
        p = self.prefix
        return p(mid) if callable(p) else p

    def measured_for(self, mid):
        m = self.measured
        if callable(m):
            return m(mid)
        if isinstance(m, dict):
            return m.get(mid) or False
        return False if m is None else m

    def estimate(self, mid, lane, uses):
        key = (mid, lane, tuple(uses) if lane == "primary" else ())
        if key not in self.est:
            fn = _adv_g("_adv_estimate")
            e = None
            if callable(fn):
                try:
                    e = fn(mid, self.hw, {"uses": list(uses)}, self.prefix_for(mid), lane,
                           self.measured_for(mid))
                except Exception as ex:
                    print("[advisor] estimate %s (%s) failed: %s" % (mid, lane, type(ex).__name__),
                          file=_advr_sys.stderr, flush=True)
                    e = None
            if not isinstance(e, dict) or not e.get("supported") \
                    or not isinstance(e.get("resident_gb"), (int, float)):
                e = None
            self.est[key] = e
        return self.est[key]


def _advr_exclusion(ctx, mid, est, uses):
    """(kind, reason) when `mid` cannot be a primary here, else None (§7.2).
    Checked in this order, first match reported: memory, vision, small, slow."""
    f = ctx.facts.get(mid) or {}
    lim = ctx.limit
    if est is None:
        return "unknown", "No estimate for this model on this Mac."
    res = est["resident_gb"]
    if isinstance(lim, (int, float)) and res > lim:
        b = ctx.limits.get("binding")
        if b == "gpu":
            tail = "GPU working set %.1f GB" % (ctx.limits.get("gpu_budget_gb") or 0)
        elif b == "soft":
            tail = "Hermes's memory ceiling %s GB, less 2 GB for one turn" % _advr_gb(ctx.limits.get("soft_gb"))
        else:
            tail = "after %s GB for macOS, this app and a browser" % _advr_gb(ctx.limits.get("reserve_gb"))
        return "memory", ("Needs ~%s GB with its cache; this Mac's budget is ~%s GB (%s)."
                          % (_advr_gb(res), _advr_gb(lim), tail))
    if "vision" in uses and f.get("vision") is False:
        return "vision", "No image support."
    q = f.get("quality") or {}
    for use, floor in (("agent", _ADVR_FLOORS["agent"]), ("code", _ADVR_FLOORS["code"])):
        if use in uses and float(q.get(use) or 0) < floor:
            lane = (" (fine as a background lane)" if mid in _ADVR_BG_IDS
                    and float(ctx.hw.get("ram_gb") or 0) >= _ADVR_BG_MIN_RAM else "")
            return "small", "Too small for %s as the assistant%s." % (
                "tool use" if use == "agent" else "coding", lane)
    d, p = est.get("decode_turn_tps"), est.get("prefill_s")
    if (isinstance(d, (int, float)) and d < _ADVR_SLOW_TPS) or \
            (isinstance(p, (int, float)) and p > _ADVR_SLOW_PREFILL):
        return "slow", ("Too slow here: ~%s tok/s in a typical %s turn and ~%s to re-read the "
                        "setup after each wake." % (_advr_tps(d), _advr_ctx_label(est.get("turn_ctx")),
                                                    _advr_secs(p)))
    return None


def _advr_smaller(ctx, bid, pid):
    fb, fp = ctx.facts.get(bid) or {}, ctx.facts.get(pid) or {}
    try:
        return float(fb["weights_bytes"]) < float(fp["weights_bytes"])
    except (KeyError, TypeError, ValueError):
        return False


def _advr_score(ctx, uses, pid, pe, bid, be, total, verdict):
    wq, ws, wb = _advr_weights(ctx.priority, ctx.power)
    fp = ctx.facts.get(pid) or {}
    Q = _advr_quality(fp, uses)
    pen = _advr_pen(pe.get("prefill_s"), ctx.power == "ac")
    V = _advr_speed(pe.get("decode_turn_tps")) - pen
    B = 0.0
    if wb:
        bg_w = (float((ctx.facts.get(bid) or {}).get("weights_bytes") or 0) / 1e9) if bid else 0.0
        lim = ctx.limit if isinstance(ctx.limit, (int, float)) and ctx.limit > 0 else None
        B = (-3.0 * (float(pe.get("bytes_per_token_gb") or 0) / 16.0) - bg_w / 6.0
             - ((total / lim) if lim else 1.0))
    bgb = 0.0
    if bid and ctx.bg_wanted:
        qb = float(((ctx.facts.get(bid) or {}).get("quality") or {}).get("chat") or 0)
        bgb = _ADVR_BG_BONUS * qb / 7.0 * (1.0 if ctx.verified(bid)[0] else 0.5)
        if ctx.power == "battery":
            bgb *= _ADVR_BATTERY_SCALE
    tight = _ADVR_TIGHT_PENALTY if verdict == "tight" else 0.0
    score = wq * Q + ws * V + wb * B + bgb - tight
    return score, {"Q": round(Q, 3), "V": round(V, 3), "B": round(B, 3), "bg": round(bgb, 3),
                   "tight": tight, "w_quality": round(wq, 4), "w_speed": round(ws, 4),
                   "w_battery": round(wb, 4), "pen": pen}


def _advr_plans(ctx, uses):
    """Every plan for `uses` plus the exclusions: (plans, wont {id: (kind, reason)})."""
    plans, wont = [], {}
    ram = float(ctx.hw.get("ram_gb") or 0)
    # E95: no plan carries a lane that cannot run (it is offered as optional)
    bg_ok = ram >= _ADVR_BG_MIN_RAM and getattr(ctx, "lane_on", True)
    order = {mid: i for i, (mid, _r) in enumerate(ctx.catalog)}
    verdict_of = _adv_g("_adv_verdict")
    for pid, row in ctx.catalog:
        if ctx.unc[pid] and not ctx.unc_on:
            continue                         # absent unless asked for AND acknowledged
        pe = ctx.estimate(pid, "primary", uses)
        ex = _advr_exclusion(ctx, pid, pe, uses)
        if ex:
            wont[pid] = ex
            continue
        bgs = [None]
        if bg_ok:
            bgs += [b for b in _ADVR_BG_IDS if b != pid and b in ctx.rows and not ctx.unc.get(b)
                    and _advr_smaller(ctx, b, pid)]
        for bid in bgs:
            be = ctx.estimate(bid, "bg", ()) if bid else None
            if bid and be is None:
                continue
            total = pe["resident_gb"] + (be["resident_gb"] if be else 0.0)
            verdict = verdict_of(total, ctx.limit)
            if verdict not in ("fits", "tight"):
                continue
            if bid and verdict != "fits":     # §7.1: a plan with a lane must fit, not be tight
                continue
            score, parts = _advr_score(ctx, uses, pid, pe, bid, be, total, verdict)
            unc = bool(ctx.unc[pid] or (bid and ctx.unc.get(bid)))
            plans.append({"pid": pid, "bid": bid, "pe": pe, "be": be, "total": total,
                          "verdict": verdict, "score": score, "parts": parts,
                          "uncensored": unc,
                          "recommendable": ctx.recommendable(pid) and not unc,
                          "verified": ctx.verified(pid)[0],
                          "usable": verdict == "fits" and (pe.get("decode_turn_tps") or 0) >= _ADVR_USABLE_TPS,
                          "order": (order.get(pid, 99), -1 if bid is None else _ADVR_BG_IDS.index(bid))})
    return plans, wont


def _advr_best(plans):
    """Highest score; ties -> verified first, fewer lanes, catalog order."""
    if not plans:
        return None
    return sorted(plans, key=lambda p: (-round(p["score"], 6), not p["verified"],
                                        p["bid"] is not None, p["order"]))[0]


def _advr_pick(plans):
    """The Recommended plan among `plans` (§7.4): the best VERIFIED usable
    recommendable plan, else the best usable recommendable plan, else None.
    "Usable" = fits with >= 8 tok/s in a typical turn (the relaxation
    threshold); "recommendable" = not uncensored (D4, never), and an opt-in
    `alternative` build (the stock 35B-A3B) only once a drill has verified it
    (D3: "a faster alternative, never the default" until drilled on AC)."""
    ok = [p for p in plans if p["usable"] and p["recommendable"]]
    return _advr_best([p for p in ok if p["verified"]]) or _advr_best(ok)


def _advr_trade(alt, rec, laptop):
    if alt["uncensored"]:
        return "Uncensored"
    if rec is None:
        return "Alternative"
    ad = alt["pe"].get("decode_turn_tps") or 0
    rd = rec["pe"].get("decode_turn_tps") or 0
    if rd and ad >= rd * 1.15:
        return "Faster"
    if alt["parts"]["Q"] > rec["parts"]["Q"] + 0.25:
        return "Better answers"
    if alt["total"] <= rec["total"] * 0.85:
        return "Lighter on battery" if laptop else "Lighter"
    ap, rp = alt["pe"].get("prefill_s") or 0, rec["pe"].get("prefill_s") or 0
    if rp and ap < rp * 0.7:
        return "Quicker after wake"
    return "Another option"


def _advr_fits_clause(ctx, p, verified):
    pe = p["pe"]
    used = "%s of %s GB" % (_advr_gb(p["total"]), _advr_gb(ctx.limit))
    if p["verdict"] == "tight":
        return "Loads, but uses %s: expect swapping under heavy use." % used
    speed = "~%s tok/s in a typical %s turn" % (_advr_tps(pe.get("decode_turn_tps")),
                                                 _advr_ctx_label(pe.get("turn_ctx")))
    wake = "first reply after wake ~%s" % _advr_secs(pe.get("prefill_s"))
    if p["verdict"] != "fits":           # no memory budget known (RAM unreadable)
        return "This Mac's memory budget is unknown: %s, %s." % (speed, wake)
    lane = ("; the %s lane keeps briefings off the assistant" % _advr_short_label(p["bid"])
            if p["bid"] else "")
    head = "Fits with room to spare" if (verified and p["bid"]) else "Fits"
    return "%s (%s): %s, %s%s." % (head, used, speed, wake, lane)


def _advr_contrast(ctx, rec, wont, uses):
    """"The <bigger brain> would load but ..." — the best-quality model this
    Mac CAN hold but that was ruled out for speed (§7.5 example), if any."""
    if rec is None:
        return None
    rq = _advr_quality(ctx.facts.get(rec["pid"]), uses)
    best = None
    for mid, (kind, _r) in wont.items():
        if kind != "slow" or ctx.unc.get(mid):
            continue
        q = _advr_quality(ctx.facts.get(mid), uses)
        if q > rq and (best is None or q > best[0]):
            best = (q, mid)
    if not best:
        return None
    e = ctx.estimate(best[1], "primary", uses) or {}
    lab = _advr_label(best[1], ctx.rows.get(best[1]))
    p = e.get("prefill_s")
    if isinstance(p, (int, float)) and p > _ADVR_SLOW_PREFILL:
        return "The %s would load but needs ~%s to re-read its setup after each wake." % (lab, _advr_secs(p))
    return "The %s would load but runs at ~%s tok/s here." % (lab, _advr_tps(e.get("decode_turn_tps")))


def _advr_why(ctx, p, relaxed, wont=None, uses=(), kind="recommended"):
    """The deterministic one-line "why" (§7.5): an opening clause (tight >
    fits [with room to spare when verified with a lane]), then at most two
    more in this order: the relaxation sentence(s), "not yet verified for
    tools", and the bigger-brain contrast."""
    verified = ctx.verified(p["pid"])[0]
    out = [_advr_fits_clause(ctx, p, verified)]
    if kind == "alternative" and p["uncensored"]:
        out.insert(0, "Uncensored: no refusals and no more accurate; the approval gate is the "
                      "brake.")
    for u in relaxed or ():
        out.append(_ADVR_RELAX_TEXT[u])
    if not verified:
        out.append(_ADVR_UNVERIFIED_TEXT)
    if kind == "recommended" and wont is not None:
        c = _advr_contrast(ctx, p, wont, uses)
        if c:
            out.append(c)
    return " ".join(out[:_ADVR_WHY_MAX])


def _advr_dl_gb(mid):
    """Decimal GB the curated download of `mid` costs: server.py's
    _catalog_dl_gb (onboarding catalog, seeds, _ADV_BY_ID), else the facts."""
    fn = _adv_g("_catalog_dl_gb")
    v = None
    if callable(fn):
        try:
            v = fn(mid)
        except Exception:
            v = None
    if not v:
        fn2 = _adv_g("_adv_download_gb")
        v = fn2(mid) if callable(fn2) else None
    return v


def _advr_lane(ctx, mid, est, lane):
    if not mid:
        return None
    row = ctx.rows.get(mid) or {"id": mid}
    ver, vsrc, vdetail = ctx.verified(mid)
    fit_fn = _adv_g("_model_fit")
    try:
        fit = fit_fn(row.get("ram"), ctx.hw.get("ram_gb")) if callable(fit_fn) else None
    except Exception:
        fit = None
    return {"id": mid, "label": _advr_label(mid, row), "lane": lane,
            "uncensored": bool(ctx.unc.get(mid, _adv_is_uncensored(mid, row))),
            "verified": ver, "verified_source": vsrc, "verified_detail": vdetail,
            "downloaded": mid in ctx.downloaded, "fit": fit,
            "download_gb": _advr_dl_gb(mid)}


def _advr_plan_out(ctx, p, kind, why, label="", notes=None, relaxed=()):
    pid, bid = p["pid"], p["bid"]
    ids = [i for i in (pid, bid) if i]
    need = 0.0
    unknown = False
    for i in ids:
        if i in ctx.downloaded:
            continue
        g = _advr_dl_gb(i)
        if isinstance(g, (int, float)):
            need += float(g)
        else:
            unknown = True
    disk_fn = _adv_g("_adv_disk_ok")
    need_r = round(need, 1)
    disk_ok = disk_fn(need_r, ctx.hw.get("disk_free_gb")) if callable(disk_fn) and need_r else True
    verified = ctx.verified(pid)[0]
    badges = []
    if p["verdict"] == "tight":
        badges.append("tight")
    if not verified:
        badges.append("unverified")
    if p["uncensored"]:
        badges.append("uncensored")
    if not p["usable"] and p["verdict"] == "fits":
        badges.append("slow")            # fits, but below 8 tok/s in a typical turn
    pe = p["pe"]
    return {
        "kind": kind, "label": label,
        "primary": _advr_lane(ctx, pid, pe, "primary"),
        "background": _advr_lane(ctx, bid, p["be"], "bg"),
        "verdict": p["verdict"], "total_gb": round(p["total"], 2),
        "limit_gb": ctx.limit, "score": round(p["score"], 4), "score_parts": p["parts"],
        "why": why, "notes": list(notes or []), "relaxed": list(relaxed or ()),
        "download_gb": need_r, "download_unknown": unknown, "disk_ok": bool(disk_ok),
        "downloaded": all(i in ctx.downloaded for i in ids),
        "verified": verified, "uncensored": p["uncensored"], "usable": p["usable"],
        "badges": sorted(set(badges)),
        "summary": {"decode_short_tps": pe.get("decode_short_tps"),
                    "decode_turn_tps": pe.get("decode_turn_tps"),
                    "turn_ctx": pe.get("turn_ctx"), "prefill_s": pe.get("prefill_s"),
                    "prefix_tokens": pe.get("prefix_tokens"),
                    "battery": _advr_plan_battery(p)},
        "estimates": {"primary": pe, "background": p["be"]},
        "estimate": True,
    }


def _advr_plan_battery(p):
    fn = _adv_g("_adv_battery_label")
    if not callable(fn):
        return None
    try:
        return fn(p["pe"].get("bytes_per_token_gb"), with_bg=bool(p["bid"]))
    except Exception:
        return None


def _advr_current(ctx, current, uses):
    """"What you run now" (§7.4): always evaluated, never hidden.  `current` =
    (primary id, background id | None)."""
    if not current or not current[0]:
        return None
    pid, bid = current[0], (current[1] if len(current) > 1 else None)
    if bid == pid or not getattr(ctx, "lane_on", True):
        bid = None                       # E95: a lane that is not running holds no memory
    pe = ctx.estimate(pid, "primary", uses)
    be = ctx.estimate(bid, "bg", ()) if bid else None
    if pe is None:
        return {"kind": "current", "label": "What you run now",
                "primary": {"id": pid, "label": _advr_label(pid, ctx.rows.get(pid)),
                            "uncensored": _adv_is_uncensored(pid, ctx.rows.get(pid))},
                "background": ({"id": bid, "label": _advr_label(bid, ctx.rows.get(bid))}
                               if bid else None),
                "verdict": None, "total_gb": None, "limit_gb": ctx.limit, "score": None,
                "why": "The advisor has no estimate for this model.", "notes": [],
                "estimates": {"primary": None, "background": be}, "estimate": True,
                "verified": ctx.verified(pid)[0], "uncensored": _adv_is_uncensored(pid),
                "usable": None, "badges": [], "download_gb": 0.0, "disk_ok": True,
                "downloaded": True, "relaxed": [], "summary": {}}
    total = pe["resident_gb"] + (be["resident_gb"] if be else 0.0)
    verdict = _adv_g("_adv_verdict")(total, ctx.limit)
    for mid in (pid, bid):
        if mid and mid not in ctx.unc:
            ctx.unc[mid] = _adv_is_uncensored(mid, ctx.rows.get(mid))
    score, parts = _advr_score(ctx, uses, pid, pe, bid, be, total, verdict) \
        if verdict in ("fits", "tight") else (0.0, {"Q": 0, "V": 0, "B": 0, "bg": 0, "tight": 0})
    p = {"pid": pid, "bid": bid, "pe": pe, "be": be, "total": total, "verdict": verdict,
         "score": score, "parts": parts, "uncensored": bool(ctx.unc.get(pid) or (bid and ctx.unc.get(bid))),
         "recommendable": ctx.recommendable(pid),
         "verified": ctx.verified(pid)[0],
         "usable": verdict == "fits" and (pe.get("decode_turn_tps") or 0) >= _ADVR_USABLE_TPS,
         "order": (0, 0)}
    if verdict == "no":
        why = ("Does not fit: needs ~%s GB with its cache, this Mac's budget is ~%s GB."
               % (_advr_gb(total), _advr_gb(ctx.limit)))
    else:
        why = _advr_why(ctx, p, (), None, uses, kind="current")
    return _advr_plan_out(ctx, p, "current", why, label="What you run now")


def _advr_make_ctx(hw, answers=None, prefix=None, measured=None, drills=None, bg_wanted=None,
                   downloaded=None, env=None, lane_on=None):
    """(ctx, effective uses, notes) — the ranking's inputs, resolved once —
    or None when aux_advisor_hw is not loaded."""
    lim_fn = _adv_g("_adv_limits")
    if not callable(lim_fn) or not callable(_adv_g("_adv_estimate")) \
            or not callable(_adv_g("_adv_verdict")):
        return None
    hw = hw or {}
    limits = lim_fn(hw, env)
    uses0, pri, power, notes, unc_on = _advr_effective(answers, hw)
    ctx = _AdvrCtx(hw, limits, prefix, measured, drills if isinstance(drills, dict) else {},
                   True if bg_wanted is None else bool(bg_wanted),
                   set(downloaded or ()), power, pri, unc_on, _adv_catalog())
    ctx.lane_on = True if lane_on is None else bool(lane_on)
    return ctx, uses0, notes


def _adv_rank(hw, answers=None, prefix=None, measured=None, drills=None, bg_wanted=None,
              downloaded=None, current=None, env=None, lane_on=None):
    """The whole §7 result for `hw` and `answers`.  PURE when every optional
    input is given (the goldens): no file, no command, no clock.

      hw          an _adv_hw() / _adv_machine() dict
      answers     stored/validated answers (None = defaults)
      prefix      int | {tokens,source,profile} | fn(mid) | None (the estimator's own rule)
      measured    {mid: measured dict} | fn(mid) | False | None (= False here;
                  _adv_rank_local reads the logs once and passes a map)
      drills      {mid: drill record} (aux_promotion) | None = {}
      bg_wanted   briefings or news on (the lane earns its bonus) | None = True
      downloaded  set of complete ids | None = empty
      current     (active id, bg id | None) | None = no "what you run now"
      env         passed to _adv_limits (None = os.environ: MLX_SOFT_GB/HARD)
      lane_on     can the background lane run (E95)? None = True (the goldens);
                  False = no plan carries a lane, "what you run now" counts
                  none, and `lane {running:false, optional}` + a note give the
                  lane's real cost

    -> {ok, recommended, alternatives[], wont[{id,label,kind,reason,uncensored}],
        notes[], relaxed[{use,label,text}], current, limits, answers{uses,
        priority,power,uncensored,requested}, estimate: true}"""
    hw = hw or {}
    made = _advr_make_ctx(hw, answers, prefix, measured, drills, bg_wanted, downloaded, env,
                          lane_on)
    if made is None:
        return {"ok": False, "error": "the advisor's estimator is not loaded"}
    ctx, uses0, notes = made
    limits, pri, power, unc_on, catalog = ctx.limits, ctx.priority, ctx.power, ctx.unc_on, ctx.catalog
    laptop = hw.get("form") in ("laptop", "laptop_fanless")
    ans_out = {"uses": list(uses0), "priority": pri, "power": power, "uncensored": unc_on,
               "requested": _advr_canon_uses(list(uses0) + (["uncensored"] if unc_on else []))}
    base = {"ok": True, "version": _ADVR_VERSION, "estimate": True, "limits": limits,
            "answers": ans_out, "recommended": None, "alternatives": [], "wont": [],
            "relaxed": [], "notes": [], "current": None}
    hw_notes_fn = _adv_g("_adv_hw_notes")
    hw_notes = []
    if callable(hw_notes_fn):
        try:
            hw_notes = list(hw_notes_fn(hw) or [])
        except Exception:
            hw_notes = []
    if not hw.get("supported", False):
        reason = hw.get("unsupported_reason") or \
            "Local models need Apple silicon; this Mac has an Intel chip."
        base["wont"] = [{"id": mid, "label": _advr_label(mid, row), "kind": "chip",
                         "reason": reason, "uncensored": ctx.unc[mid]}
                        for mid, row in catalog if not ctx.unc[mid] or unc_on]
        base["notes"] = [reason]
        base["current"] = _advr_current(ctx, current, uses0) if current else None
        return base

    # relaxation (§7.4): docs -> code -> agent, while nothing usable survives
    uses = list(uses0)
    relaxed = []
    plans, wont = _advr_plans(ctx, uses)
    for u in _ADVR_RELAX_ORDER:
        if _advr_pick(plans) is not None:
            break
        if u in uses and [x for x in uses if x != u]:
            uses = [x for x in uses if x != u]
            relaxed.append(u)
            plans, wont = _advr_plans(ctx, uses)
    rec = _advr_pick(plans)
    if rec is None:
        # nothing usable even chat-first: the best recommendable plan that loads at all
        rec = _advr_best([p for p in plans if p["recommendable"]])
        if rec is not None:
            notes.append("Nothing in the catalog runs well here; this is the closest fit.")
    ans_out["uses_ranked"] = list(uses)

    alts, seen = [], {rec["pid"]} if rec else set()
    stock = [p for p in plans if not p["uncensored"] and p["pid"] not in seen]
    per = {}
    for p in stock:
        if p["pid"] not in per or _advr_best([per[p["pid"]], p]) is p:
            per[p["pid"]] = p
    for p in sorted(per.values(), key=lambda p: (-round(p["score"], 6), not p["verified"],
                                                 p["bid"] is not None, p["order"])):
        if len(alts) >= _ADVR_MAX_ALTS:
            break
        alts.append(p)
    if unc_on:
        u = _advr_best([p for p in plans if p["uncensored"]])
        if u is not None:
            alts.append(u)

    if rec is not None:
        rnotes = []
        if "docs" in uses:
            rnotes.append(_ADVR_DOCS_NOTE)
        if rec["bid"]:
            rnotes.append(_ADVR_BG_NOTE)
        for n in rec["pe"].get("notes") or ():
            if n.startswith("After the model sleeps"):
                rnotes.append(n)
        base["recommended"] = _advr_plan_out(
            ctx, rec, "recommended", _advr_why(ctx, rec, relaxed, wont, uses),
            label="Recommended", notes=rnotes, relaxed=relaxed)
    base["alternatives"] = [
        _advr_plan_out(ctx, p, "alternative", _advr_why(ctx, p, (), None, uses, kind="alternative"),
                       label=_advr_trade(p, rec, laptop))
        for p in alts]
    pos = {mid: i for i, (mid, _r) in enumerate(catalog)}
    base["wont"] = [{"id": mid, "label": _advr_label(mid, ctx.rows.get(mid)), "kind": k,
                     "reason": r, "uncensored": bool(ctx.unc.get(mid))}
                    for mid, (k, r) in sorted(wont.items(), key=lambda kv: pos.get(kv[0], 99))]
    base["relaxed"] = [{"use": u, "label": _ADVR_USE_LABEL[u], "text": _ADVR_RELAX_TEXT[u]}
                       for u in relaxed]
    out_notes = list(notes) + [_ADVR_RELAX_TEXT[u] for u in relaxed]
    if "docs" in uses:
        out_notes.append(_ADVR_DOCS_NOTE)
    if limits.get("binding") == "soft" and float(hw.get("ram_gb") or 0) >= 64:
        out_notes.append(limits.get("note") or "")
    out_notes += hw_notes
    if not ctx.lane_on:
        base["lane"] = _advr_optional_lane(ctx, rec)
        if base["lane"].get("note"):
            out_notes.append(base["lane"]["note"])
    base["notes"] = [n for i, n in enumerate(out_notes) if n and n not in out_notes[:i]]
    base["current"] = _advr_current(ctx, current, uses) if current else None
    return base


def _advr_optional_lane(ctx, rec):
    """E95: the lane is not running. {running: False, optional: {id, label,
    resident_gb, total_gb, limit_gb} | None, note}: the lane model that would
    FIT next to the Recommended primary (largest first), and what it costs.
    Nothing below _ADVR_BG_MIN_RAM, and no note while briefings and news are
    both off (nothing would use it)."""
    out = {"running": False, "optional": None, "note": ""}
    ram = float(ctx.hw.get("ram_gb") or 0)
    if rec is None or ram < _ADVR_BG_MIN_RAM:
        return out
    verdict_of = _adv_g("_adv_verdict")
    pid = rec["pid"]
    for b in _ADVR_BG_IDS:
        if b == pid or b not in ctx.rows or ctx.unc.get(b) or not _advr_smaller(ctx, b, pid):
            continue
        be = ctx.estimate(b, "bg", ())
        if be is None:
            continue
        total = rec["pe"]["resident_gb"] + be["resident_gb"]
        if not callable(verdict_of) or verdict_of(total, ctx.limit) != "fits":
            continue
        out["optional"] = {"id": b, "label": _advr_label(b, ctx.rows.get(b)),
                           "resident_gb": round(be["resident_gb"], 2),
                           "total_gb": round(total, 2), "limit_gb": ctx.limit}
        break
    if not ctx.bg_wanted:
        return out
    o = out["optional"]
    head = ("The background lane is not running (nothing starts it; it runs only when you "
            "start it by hand), so briefings and news use the assistant model while it is "
            "awake.")
    if o:
        out["note"] = ("%s Running the %s lane too would hold ~%s GB more (%s of %s GB in all)."
                       % (head, _advr_short_label(o["id"]), _advr_gb(o["resident_gb"]),
                          _advr_gb(o["total_gb"]), _advr_gb(ctx.limit)))
    else:
        out["note"] = "%s No lane model fits next to this plan." % head
    return out


def _advr_lane_runs():
    """E95: can the background lane run? FILES only (no port, no process
    probe): the owner's start token (mlx-server-bg.sh's model-start-ok-bg) or
    the lane's own log written within _ADVR_LANE_RECENT_S."""
    pth = _advr_os.path
    data = _adv_g("DATA") or pth.join(_adv_g("HOME") or pth.expanduser("~"), ".hermes",
                                      "dashboard")
    try:
        if pth.exists(pth.join(data, "model-start-ok-bg")):
            return True
    except Exception:
        pass
    log = _adv_g("_CX_LOG_BG") or pth.join(_adv_g("HOME") or pth.expanduser("~"), ".hermes",
                                           "logs", "mlx-bg.log")
    try:
        return _advr_time.time() - pth.getmtime(log) < _ADVR_LANE_RECENT_S
    except Exception:
        return False


# --------------------------------------------------------------------------
# the local machine: what the routes and the payload rank
# --------------------------------------------------------------------------
def _advr_local_inputs(hw, ids):
    """Everything _adv_rank needs about THIS Mac, read once: measured-here
    rows (one read of both logs), the prompt profile, the drill records, the
    watchtower masters, which catalog ids are complete, and the current
    plan.  File reads only — no command, no port."""
    rows = []
    cx = _adv_g("_cx_rows")
    if callable(cx):
        for name in ("_CX_LOG", "_CX_LOG_BG"):
            p = _adv_g(name)
            if p:
                try:
                    rows.extend(cx(p) or [])
                except Exception:
                    pass
    mfn = _adv_g("_adv_measured")
    meas = {}
    for mid in ids:
        m = None
        if callable(mfn) and rows:
            try:
                m = mfn(mid, rows)
            except Exception:
                m = None
        meas[mid] = m if isinstance(m, dict) else False
    prof = None
    pfn = _adv_g("prompt_budget_profile")
    if callable(pfn):
        try:
            prof = pfn()
        except Exception:
            prof = None
    pre_fn = _adv_g("_adv_prefix")
    pre = {}
    for mid in ids:
        try:
            pre[mid] = pre_fn(mid, meas[mid] or None, prof) if callable(pre_fn) else None
        except Exception:
            pre[mid] = None
    drills = {}
    pr = _adv_g("_pro_read")
    if callable(pr):
        try:
            d = pr()
            drills = d if isinstance(d, dict) else {}
        except Exception:
            drills = {}
    bg_wanted = True
    wl = _adv_g("_wt_load")
    if callable(wl):
        try:
            wt = wl() or {}
            ms = wt.get("master") if isinstance(wt.get("master"), dict) else {}
            bg_wanted = bool(ms.get("briefings", True)) or bool(ms.get("news", True))
        except Exception:
            bg_wanted = True
    have = set()
    md, dr = _adv_g("_model_downloaded"), _adv_g("_draft_ready")
    rows_by = dict(_adv_catalog())
    for mid in ids:
        try:
            if callable(md) and md(mid) and (not callable(dr) or dr(rows_by.get(mid) or {"id": mid})):
                have.add(mid)
        except Exception:
            pass
    cur = None
    am, bm = _adv_g("active_model"), _adv_g("bg_model")
    try:
        a = am() if callable(am) else None
        b = bm() if callable(bm) else None
        if a:
            cur = (a, b if b and b != a else None)
    except Exception:
        cur = None
    try:
        lane_on = bool(_advr_lane_runs())
    except Exception:
        lane_on = False
    return {"measured": meas, "prefix": pre, "drills": drills, "bg_wanted": bg_wanted,
            "downloaded": have, "current": cur, "profile": prof, "lane_on": lane_on}


def _adv_rank_local(answers=None, hw=None, bust=False):
    """_adv_rank for THIS Mac -> (result, hw, inputs)."""
    hwf = _adv_g("_adv_hw")
    if hw is None:
        hw = hwf(bust=bust) if callable(hwf) else {}
    ids = [mid for mid, _r in _adv_catalog()]
    inputs = _advr_local_inputs(hw, ids)
    cur = inputs["current"]
    if cur:
        inputs.update(_advr_local_inputs_for(cur, inputs))
    pre_map, meas_map = inputs["prefix"], inputs["measured"]
    res = _adv_rank(hw, answers if answers is not None else _adv_answers_stored(),
                    prefix=lambda mid: pre_map.get(mid),
                    measured=lambda mid: meas_map.get(mid) or False,
                    drills=inputs["drills"], bg_wanted=inputs["bg_wanted"],
                    downloaded=inputs["downloaded"], current=cur,
                    lane_on=inputs.get("lane_on"))
    return res, hw, inputs


def _advr_local_inputs_for(cur, inputs):
    """Prefix/measured for a current-plan id outside the catalog (an owner's
    hand-added model with facts): filled with the estimator's own defaults."""
    pre, meas = dict(inputs["prefix"]), dict(inputs["measured"])
    for mid in cur:
        if mid and mid not in pre:
            pre[mid] = None
            meas[mid] = False
    return {"prefix": pre, "measured": meas}


# --------------------------------------------------------------------------
# routes (§9)
# --------------------------------------------------------------------------
_ADVR_HW_KEYS = ("ok", "version", "local", "supported", "unsupported_reason", "ram_gb", "macos",
                 "model_id", "machine_name", "form", "fanless", "disk_free_gb", "rosetta",
                 "static_at", "dynamic_at", "error")
_ADVR_CHIP_KEYS = ("brand", "family", "variant", "gen", "gpu_cores", "cores_source",
                   "bandwidth_gbs", "e_chip", "p_index", "row", "row_source", "confidence",
                   "reference", "notes")


def _advr_hw_public(hw):
    """The §9 `hw` block: a whitelist (nothing a command printed beyond it)."""
    hw = hw or {}
    out = {k: hw.get(k) for k in _ADVR_HW_KEYS if k in hw}
    chip = hw.get("chip") if isinstance(hw.get("chip"), dict) else None
    out["chip"] = {k: chip.get(k) for k in _ADVR_CHIP_KEYS} if chip else None
    cpu = hw.get("cpu") if isinstance(hw.get("cpu"), dict) else {}
    out["cpu"] = {"levels": list(cpu.get("levels") or []), "count": cpu.get("count")}
    pw = hw.get("power") if isinstance(hw.get("power"), dict) else {}
    out["power"] = {k: pw.get(k) for k in ("source", "pct", "has_battery", "low_power")}
    th = hw.get("thermal") if isinstance(hw.get("thermal"), dict) else {}
    out["thermal"] = {k: th.get(k) for k in ("limited", "cpu_speed_limit", "text")}
    gb = hw.get("gpu_budget") if isinstance(hw.get("gpu_budget"), dict) else {}
    out["gpu_budget"] = {k: gb.get(k) for k in ("gb", "source", "fraction", "note")}
    out["now"] = dict(hw.get("now") or {}) if isinstance(hw.get("now"), dict) else {}
    return out


def _advr_constants():
    return {"usable_tps": _ADVR_USABLE_TPS, "slow_tps": _ADVR_SLOW_TPS,
            "slow_prefill_s": _ADVR_SLOW_PREFILL, "bg_min_ram_gb": _ADVR_BG_MIN_RAM,
            "background_ids": list(_ADVR_BG_IDS), "quality_weights": dict(_ADVR_Q_WEIGHT),
            "floors": dict(_ADVR_FLOORS), "priorities": {k: list(v) for k, v in _ADV_PRIORITIES.items()},
            "prefill_penalty": [[c, v] for c, v in _ADVR_PEN_STEPS] + [["more", _ADVR_PEN_MAX]],
            "battery_weight": _ADVR_BATTERY_W, "battery_scale": _ADVR_BATTERY_SCALE,
            "bg_bonus": _ADVR_BG_BONUS, "tight_penalty": _ADVR_TIGHT_PENALTY,
            "relax_order": list(_ADVR_RELAX_ORDER), "max_alternatives": _ADVR_MAX_ALTS,
            "verify_min": _ADVR_VERIFY_MIN, "verify_of": _ADVR_VERIFY_OF}


def _advr_formulas():
    return {"score": "score = w_quality x Q + w_speed x V + w_battery x B + bg - (1 if tight)",
            "quality": "Q = mean of the model's quality per use, weighted chat 1, agent 1.5, "
                       "code 1.5, docs 1, vision 1",
            "speed": "V = 10 x clamp(log2(tok/s in a typical turn / 8) / 3, 0, 1) - penalty for "
                     "the first reply after wake (0 / 1 / 2 / 4 / 6 at 20 / 45 / 90 / 150 s; "
                     "halved when plugged in)",
            "battery": "on battery only: B = -3 x GB moved per token / 16 - background weights "
                       "GB / 6 - plan total / limit; the other weights x0.75",
            "background": "a lane earns 0.8 x its chat quality / 7 (half when not verified) "
                          "while briefings or news are on",
            "recommended": "the best verified plan that fits with at least 8 tok/s; otherwise "
                           "the best such plan, marked not yet verified; uncensored never",
            "relaxation": "while nothing fits at 8 tok/s: drop long documents, then coding, then "
                          "tool use, and say so"}


def _adv_state(ctx_query=None):
    q = ctx_query or {}
    bust = (q.get("bust") or [""])[0] in ("1", "true") if isinstance(q, dict) else False
    answers = _adv_answers_stored()
    res, hw, inputs = _adv_rank_local(answers, bust=bust)
    lim = res.get("limits") or {}
    models = {}
    for key, e in sorted(_advr_last_estimates(hw, answers, inputs).items()):
        models[key] = e
    hwf, hcf = _adv_g("_adv_formulas"), _adv_g("_adv_constants")
    pre_fn = _adv_g("_adv_prefix")
    prefix = None
    try:
        prefix = pre_fn(None, None, inputs.get("profile")) if callable(pre_fn) else None
    except Exception:
        prefix = None
    cur = inputs.get("current") or (None, None)
    out_err = {} if res.get("ok") else {"error": res.get("error") or "the advisor could not rank"}
    return dict(out_err, **{"ok": bool(res.get("ok")), "version": _ADVR_VERSION, "estimate": True,
            "hw": _advr_hw_public(hw),
            "limits": {k: lim.get(k) for k in ("gpu_budget_gb", "gpu_budget_source", "soft_gb",
                                               "hard_gb", "reserve_gb", "limit_gb", "binding",
                                               "note", "formula", "terms")},
            "prefix": prefix,
            "answers": answers,
            "questions": {"uses": [{"key": u, "label": _ADVR_USE_LABEL[u]} for u in _ADV_USES],
                          "priorities": list(_ADV_PRIORITIES), "powers": list(_ADVR_POWERS),
                          "power_asked": hw.get("form") in ("laptop", "laptop_fanless"),
                          "uncensored_ack_text": _ADVR_UNCENSORED_ACK_TEXT},
            "current": {"active": cur[0], "background": cur[1] if len(cur) > 1 else None,
                        "plan": res.get("current")},
            "result": res,
            "models": models,
            "formulas": dict((hwf() if callable(hwf) else {}), **_advr_formulas()),
            "constants": dict((hcf() if callable(hcf) else {}), **_advr_constants())})


def _advr_last_estimates(hw, answers, inputs):
    """{id: primary-lane estimate} for every catalog id (the §9 `models`)."""
    uses, _pri, _pw, _n, _u = _advr_effective(answers, hw)
    fn = _adv_g("_adv_estimate")
    out = {}
    if not callable(fn):
        return out
    for mid, _row in _adv_catalog():
        try:
            e = fn(mid, hw, {"uses": uses}, inputs["prefix"].get(mid),
                   "primary", inputs["measured"].get(mid) or False)
        except Exception:
            e = None
        if isinstance(e, dict):
            out[mid] = e
    return out


def _advr_query_answers(q):
    """GET /api/advisor/rank query -> a body for _adv_answers_validate.
    uses=chat,agent (comma list) · priority= · power=ac|battery|auto (auto and
    null = what this Mac is on now; a BLANK value never arrives, parse_qs drops
    it, so it keeps the stored answer).  There is deliberately no way
    to assert the uncensored acknowledgement here: it counts only once it is
    STORED (POST /api/advisor/answers {uncensored_ack: true})."""
    def one(k):
        v = q.get(k)
        if isinstance(v, list):
            return v[0] if v else None
        return v
    body = {}
    uses = one("uses")
    if uses is not None:
        body["uses"] = [x.strip() for x in str(uses).split(",") if x.strip()]
    pri = one("priority")
    if pri is not None:
        body["priority"] = pri
    pw = one("power")
    if pw is not None:
        body["power"] = None if pw in ("", "null", "auto") else pw
    return body


def _adv_rank_route(ctx):
    """GET /api/advisor/rank -> {ok, result, answers, saved: false, needs_ack}
    for UNSAVED answers (each one the query does not give comes from the
    stored answers).  No writes of any kind.  An invalid value is a 400 {ok:
    false, errors[]}; `uncensored` without the STORED acknowledgement is not
    an error here — it is ranked without the uncensored rows, the result
    says so, and needs_ack is true (the UI stores the acknowledgement first,
    then re-ranks)."""
    body = _advr_query_answers(getattr(ctx, "query", None) or {})
    stored = _advr_raw_stored()
    ans, errs, _ign = _adv_answers_validate(body, stored, require_ack=False)
    if errs:
        return {"ok": False, "errors": errs}, 400
    ans["uncensored_ack"] = _advr_clean_answers(stored)["uncensored_ack"]    # stored only
    res, _hw, _inp = _adv_rank_local(ans)
    return {"ok": bool(res.get("ok")), "result": res, "answers": ans, "saved": False,
            "needs_ack": "uncensored" in ans["uses"] and ans["uncensored_ack"] is not True}


def _adv_state_route(ctx):
    return _adv_state(getattr(ctx, "query", None) or {})


def _adv_answers_route(ctx):
    return _adv_answers_save(getattr(ctx, "body", None))


if callable(globals().get("register_get")) and callable(globals().get("register_post")):
    register_get("/api/advisor/state", _adv_state_route)      # noqa: F821 (server.py's)
    register_get("/api/advisor/rank", _adv_rank_route)        # noqa: F821
    register_post("/api/advisor/answers", _adv_answers_route)  # noqa: F821


# --------------------------------------------------------------------------
# the models_payload() rebind (§9, §8.3): per-row `advice` + `advisor`
# --------------------------------------------------------------------------
_ADVR_ADVICE_TTL = 30


def _advr_advice_build():
    """{"rows": {id: advice}, "advisor": {...}} for THIS Mac — one _adv_hw(),
    one local ranking with the stored answers.  No port probe."""
    answers = _adv_answers_stored()
    res, hw, inputs = _adv_rank_local(answers)
    uses = (res.get("answers") or {}).get("uses_ranked") or (res.get("answers") or {}).get("uses") or []
    lim = (res.get("limits") or {}).get("limit_gb")
    rec = res.get("recommended") or {}
    rp = (rec.get("primary") or {}).get("id")
    rb = (rec.get("background") or {}).get("id")
    alts = {(a.get("primary") or {}).get("id"): a for a in res.get("alternatives") or []}
    wont = {w["id"]: w for w in res.get("wont") or []}
    fn, vfn = _adv_g("_adv_estimate"), _adv_g("_adv_verdict")
    cur = inputs.get("current") or (None, None)
    rows = {}
    facts = _adv_g("_ADV_FACTS") or {}
    ids = [mid for mid, _r in _adv_catalog()]
    for mid in list(cur):
        if mid and mid in facts and mid not in ids:
            ids.append(mid)
    for mid in ids:
        try:
            e = fn(mid, hw, {"uses": uses}, inputs["prefix"].get(mid), "primary",
                   inputs["measured"].get(mid) or False)
        except Exception:
            e = None
        if not isinstance(e, dict) or not e.get("supported"):
            continue
        solo = vfn(e.get("resident_gb"), lim) if callable(vfn) else None
        unc = _adv_is_uncensored(mid)
        if mid == rp:
            why, role = ("recommended for you" + (" with the %s lane" % _advr_short_label(rb) if rb else "")), \
                "recommended"
        elif mid in wont:
            w = wont[mid]
            role = "wont"
            if w["kind"] == "memory":
                why = "won't fit: needs ~%s GB of a %s GB budget" % (_advr_gb(e.get("resident_gb")), _advr_gb(lim))
            elif w["kind"] == "slow":
                p = e.get("prefill_s")
                why = ("too slow here: ~%s first read" % _advr_secs(p)
                       if isinstance(p, (int, float)) and p > _ADVR_SLOW_PREFILL
                       else "too slow here: ~%s tok/s in a typical turn" % _advr_tps(e.get("decode_turn_tps")))
            elif w["kind"] == "unknown":
                why = "no estimate here"
            else:                             # small / vision / chip: the reason, as a phrase
                r0 = w["reason"].split(" (")[0].rstrip(".")
                why = r0[:1].lower() + r0[1:]
        elif unc:
            role = "uncensored"
            why = ("uncensored alternative: never recommended"
                   + ("" if solo in ("fits", "tight") else ", and it does not fit here"))
        elif mid in alts:
            role = "alternative"
            a = alts[mid]
            why = "%s alternative" % (a.get("label") or "an").lower() if a.get("label") else "alternative"
            if a.get("background"):
                why += " with the %s lane" % _advr_short_label(a["background"]["id"])
        elif solo == "fits":
            role, why = "fits", "fits"
        elif solo == "tight":
            role, why = "tight", "tight: loads, expect swapping under heavy use"
        else:
            role, why = "no", "won't fit here"
        m = e.get("measured") if isinstance(e.get("measured"), dict) else None
        rows[mid] = {
            "estimate": True, "lane": "primary",
            "resident_gb": e.get("resident_gb"), "decode_short_tps": e.get("decode_short_tps"),
            "decode_turn_tps": e.get("decode_turn_tps"), "turn_ctx": e.get("turn_ctx"),
            "prefill_s": e.get("prefill_s"), "prefix_tokens": e.get("prefix_tokens"),
            "limit_gb": lim, "verdict_solo": solo, "basis": e.get("basis"),
            "range": e.get("range"), "formula": e.get("formula"),
            "measured": ({k: m.get(k) for k in ("decode_short", "decode_turn", "prefill_tps",
                                                "n_short", "n_turn", "n_prefill")} if m else None),
            "role": role, "why": why, "uncensored": unc,
            "recommended": mid == rp, "kind": (wont.get(mid) or {}).get("kind")}
    cplan = res.get("current") or {}
    lims = res.get("limits") or {}
    advisor = {"ok": bool(res.get("ok")), "estimate": True, "limit_gb": lim,
               "gpu_budget_gb": lims.get("gpu_budget_gb"),
               "gpu_budget_source": lims.get("gpu_budget_source"),
               "binding": lims.get("binding"),
               "current": {"total_gb": cplan.get("total_gb"), "verdict": cplan.get("verdict"),
                           "primary": (cplan.get("primary") or {}).get("id"),
                           "background": (cplan.get("background") or {}).get("id")},
               "recommended": {"primary": rp, "background": rb} if rp else None,
               "answers_saved": bool(answers.get("saved_at"))}
    return {"rows": rows, "advisor": advisor}


def _advr_payload_advice(out):
    """Decorate a models_payload() dict in place.  Never raises (the caller
    catches anyway); a row with no estimate gets `advice: None`."""
    if not isinstance(out, dict):
        return out
    cached = _adv_g("_cached")
    if callable(cached):
        data = cached("adv_advice", _ADVR_ADVICE_TTL, _advr_advice_build)
    else:
        data = _advr_advice_build()
    if not isinstance(data, dict):
        raise RuntimeError("advice unavailable")
    rows = data.get("rows") or {}
    for m in out.get("models") or []:
        if isinstance(m, dict):
            a = rows.get(m.get("id"))
            m["advice"] = _advr_copy.deepcopy(a) if a else None
    out["advisor"] = _advr_copy.deepcopy(data.get("advisor"))
    return out


if not globals().get("_adv_wrapped") and callable(globals().get("models_payload")):
    globals()["_adv_wrapped"] = True
    _adv_orig_models_payload = models_payload    # noqa: F821 (server.py's)

    def models_payload():
        out = _adv_orig_models_payload()
        try:
            _advr_payload_advice(out)
        except Exception as e:      # advice is decoration: the menu must never break
            try:
                for m in out.get("models") or []:
                    if isinstance(m, dict):
                        m.setdefault("advice", None)
                out["advisor"] = {"ok": False, "error": type(e).__name__}
            except Exception:
                pass
        return out


# --------------------------------------------------------------------------
# onboarding seam (§9): _onb_recommend's exact key set, plus `advisor`
# --------------------------------------------------------------------------
_ADVR_COMPAT_KEYS = ("tier", "primary", "background", "tight", "alt", "why", "note",
                     "primary_label", "background_label", "download_gb", "total_gb",
                     "already_downloaded", "fits")


def _advr_compat_fallback(ram_gb, downloaded, why):
    fb = _adv_g("_onb_recommend")
    out = None
    if callable(fb):
        try:
            out = fb(ram_gb, downloaded)
        except Exception:
            out = None
    if not isinstance(out, dict):
        out = {k: None for k in _ADVR_COMPAT_KEYS}
        out.update(tier="unknown", tight=False, why="", note="", primary_label="",
                   background_label="", download_gb=0.0, total_gb=0.0,
                   already_downloaded=False, fits=[])
    out = dict(out)
    out["advisor"] = {"ok": False, "fallback": "_onb_tier", "error": why}
    return out


def _adv_recommend_compat(ram_gb=None, downloaded=(), hw=None, answers=None):
    """The advisor's recommendation in _onb_recommend's EXACT key set
    (tier = "advisor", primary, background, tight, alt, why, note,
    primary_label, background_label, download_gb, total_gb,
    already_downloaded, fits) plus `advisor` {ok, result, ...}, so
    aux_onboarding can swap it in with one line and the legacy sheet keeps
    working.  NEVER raises: whenever the advisor cannot answer (estimator not
    loaded, an unsupported Mac, nothing fits, an exception) it returns
    _onb_recommend(ram_gb, downloaded) — the RAM-only tier stays the fallback
    — with advisor.ok False.  `alt` is never an uncensored plan."""
    downloaded = tuple(downloaded or ())
    try:
        if hw is None:
            hwf = _adv_g("_adv_hw")
            hw = hwf() if callable(hwf) else None
        if not isinstance(hw, dict):
            return _advr_compat_fallback(ram_gb, downloaded, "no hardware facts")
        try:
            r = float(ram_gb) if ram_gb is not None else None
        except (TypeError, ValueError):
            r = None
        if r and r > 0 and abs(float(hw.get("ram_gb") or 0) - r) > 0.5:
            hw = _advr_copy.deepcopy(hw)
            hw["ram_gb"] = r
            gbf = _adv_g("_adv_gpu_budget")
            if callable(gbf):
                hw["gpu_budget"] = gbf(r, 0, 0)
        if answers is None:
            answers = _adv_answers_stored()
        if hw.get("local"):
            res, hw, _inp = _adv_rank_local(answers, hw=hw)
        else:
            res = _adv_rank(hw, answers, downloaded=set(downloaded))
        rec = res.get("recommended") if isinstance(res, dict) else None
        if not res.get("ok") or not rec:
            return _advr_compat_fallback(ram_gb, downloaded,
                                         res.get("error") or "no plan fits this Mac")
        pid = rec["primary"]["id"]
        bid = (rec.get("background") or {}).get("id")
        ids = [i for i in (pid, bid) if i]

        def dl(i):
            g = _advr_dl_gb(i)
            return float(g) if isinstance(g, (int, float)) else 0.0
        need = sum(dl(i) for i in ids if i not in downloaded)
        alt = None
        for a in res.get("alternatives") or []:
            if a.get("uncensored"):
                continue
            alt = {"primary": a["primary"]["id"],
                   "background": (a.get("background") or {}).get("id"),
                   "label": a.get("label") or "Alternative", "why": a.get("why") or ""}
            break
        fits_fn = _adv_g("_onb_fits")
        try:
            fits = fits_fn(ram_gb if ram_gb is not None else hw.get("ram_gb"), downloaded) \
                if callable(fits_fn) else []
        except Exception:
            fits = []
        tight = rec.get("verdict") == "tight"
        relaxed = [x.get("use") for x in res.get("relaxed") or []]
        note = "tight" if tight else ("chat first" if "agent" in relaxed else "")
        out = {"tier": "advisor", "primary": pid, "background": bid, "tight": tight,
               "alt": alt, "why": rec.get("why") or "", "note": note,
               "primary_label": rec["primary"].get("label") or "",
               "background_label": (rec.get("background") or {}).get("label") or "",
               "download_gb": round(need, 1), "total_gb": round(sum(dl(i) for i in ids), 1),
               "already_downloaded": bool(ids) and not need, "fits": fits,
               "advisor": {"ok": True, "verified": bool(rec.get("verified")),
                           "result": res}}
        return out
    except Exception as e:
        return _advr_compat_fallback(ram_gb, downloaded, "%s" % type(e).__name__)
