# aux_foryou.py — the "For You" reasoning loop + Agent-Inbox widget
# (Proactive-Intelligence plan §3 reasoning loop, §5b surfacing, WS 1.3 + 1.5).
#
# Turns world signals (intel.json) into ranked personal MOVES by joining them
# against the You-Model (~/.hermes/memories/GOALS.md / NOW.md / LOOKING-FOR.md /
# INTERESTS.md / PREFERENCES.md + people/*.md cards).
#
# TWO-TIER FUNNEL (the memory-ceiling contract, plan §7):
#   tier 1 — cheap lexical pre-filter over ALL intel items, NO model. This is
#            the v1 stand-in for the embedding "preference center" (local
#            embeddings per interest facet are a Phase-later upgrade).
#   tier 2 — ONE batched chat completion over only the top ~15 survivors,
#            guarded by mlx_admission() (skip + keep last foryou.json when the
#            soft ceiling is engaged). Serialized under a lock — never
#            concurrent, never per-item.
#
# NOTIFY-ONLY BY CONSTRUCTION: this module never pings Telegram, never runs a
# tool, never touches the approval path. It only writes
# ~/.hermes/dashboard/foryou.json (0600) and serves the dashboard panel (+ the
# brief section reads the same store in WS 1.4). Consequential moves ("draft
# the intro to Y") are deliberately OUT of scope for v1 — future work behind
# the 17-class approval tiers.
#
# exec'd into server.py's globals by the aux loader (sorted BEFORE aux_memory /
# aux_watchtower, so it must not touch their names at load time). May use these
# server.py globals: HOME, DATA, read_json, register_get, register_post,
# WIDGETS, EXPANDERS, get_layout, save_layout, mlx_admission, model_online,
# agent_paused, CHAT_JOBS, MODEL_URL, ACTIVE_MODEL_FILE, DEFAULT_MODEL.
# Imports ALL its own stdlib deps and defines only new names (FY_*/FORYOU_*/
# _fy_*/w_foryou/expand_foryou/foryou_loop/_foryou_build) so it clobbers
# nothing. Per CLAUDE.md law: no bare `from datetime import datetime` (none
# needed here at all).

import os
import re
import sys
import json
import time
import tempfile
import threading
import urllib.request

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
FORYOU_FILE     = os.path.join(DATA, "foryou.json")
FY_REACTS_FILE  = os.path.join(DATA, "foryou-reactions.jsonl")
FY_INTEL_FILE   = os.path.join(DATA, "intel.json")     # read directly (shape:
                                                       # {"items":[{title,url,source,topic,ts,summary},...]})
FY_MEM_DIR      = os.path.join(HOME, ".hermes", "memories")
FY_PEOPLE_DIR   = os.path.join(FY_MEM_DIR, "people")
FY_ENTRY_DELIM  = "\n§\n"          # == aux_memory.ENTRY_DELIM

FY_INTERVAL     = 2 * 3600         # heavy rebuild cadence (~2h), plus on intel change
FY_LOOP_TICK    = 300              # daemon wakes every 5 min to CHECK (cheap)
FY_TOPK         = 15               # tier-2 candidate cap — ONE batched model call
FY_MIN_SCORE    = 1.5              # lexical threshold for tier 2
FY_MAX_MOVES    = 10               # ranked moves kept in the store
FY_FALLBACK_N   = 8                # raw intel items shown when You-Model is empty
FY_PERSON_BOOST = 4.0              # a people-card name match outranks topic overlap

# You-Model source files -> (category key, term weight). PREFERENCES feeds the
# model's profile summary (tone / what counts as noise) but contributes no
# match terms — it is interruptibility context, not an interest facet.
FY_FILES = [
    ("GOALS.md",       "goal",        2.5),
    ("NOW.md",         "now",         2.5),
    ("LOOKING-FOR.md", "looking-for", 2.5),
    ("INTERESTS.md",   "interest",    1.5),
    ("PREFERENCES.md", "preference",  0.0),
]
FY_CAT_LABEL = {"goal": "goal", "now": "current project",
                "looking-for": "looking-for", "interest": "interests"}

_fy_build_lock = threading.Lock()   # serializes builds — concurrency-1 by design
_fy_last_kick = [0.0]               # throttle for lazy async builds

# generic tokens that would otherwise dominate lexical overlap
_FY_STOP = frozenset("""
the and for are but not you your our was were been being have has had having
this that these those with without about into over under from they them their
what which who whom when where why how all any both each few more most other
some such only own same than too very can could will would just should shall
may might must also its it's his her she him out off per via than then else
new news using use used uses want wants wanted looking look looks looked
interested interest goal goals project projects working works work currently
building build built thing things stuff meet find found get gets got make
makes made good great big small many much lot day days week weeks month months
year years time times today tomorrow now here there like need needs really
one two three way ways more said says say
""".split())


def _fy_log(msg):
    try:
        print("[aux_foryou] " + str(msg), file=sys.stderr)
    except Exception:
        pass


# --------------------------------------------------------------------------
# small io helpers (private — no load-order dependency on other aux modules)
# --------------------------------------------------------------------------
def _fy_atomic_write(path, raw, mode=0o600):
    """Atomic write with tight perms (the store is personal — 0600)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".foryou_")
    try:
        os.write(fd, raw.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def _fy_write_store(store):
    _fy_atomic_write(FORYOU_FILE, json.dumps(store, ensure_ascii=False, indent=1))


def _fy_norm_url(u):
    """Same normalization _intel_curate uses to reject invented URLs."""
    u = str(u or "").strip()
    u = re.sub(r"[?#].*$", "", u)
    return u.rstrip("/").lower()


def _fy_clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _fy_intel_items():
    d = read_json(FY_INTEL_FILE, None)
    items = (d or {}).get("items")
    return items if isinstance(items, list) else []


def _fy_intel_mtime():
    try:
        return os.path.getmtime(FY_INTEL_FILE)
    except OSError:
        return 0


# --------------------------------------------------------------------------
# the You-Model — read the typed memory files directly (degrade gracefully:
# a fresh install has none of them until onboarding writes them)
# --------------------------------------------------------------------------
def _fy_read_entries(name):
    try:
        with open(os.path.join(FY_MEM_DIR, name), encoding="utf-8",
                  errors="replace") as f:
            raw = f.read()
    except OSError:
        return []
    # template scaffolding (WS 1.1 seeds each file with an explanatory
    # <!-- comment -->) is NOT You-Model content — strip it or a fresh
    # install looks personalized and the model reasons over placeholders
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    out = []
    for chunk in raw.split(FY_ENTRY_DELIM):
        s = re.sub(r"^#{1,6}\s+", "", chunk.strip(), flags=re.M)
        s = s.replace("*", " ").replace("`", " ").replace("[[", " ").replace("]]", " ")
        s = _fy_clean(s)
        if s:
            out.append(s[:400])
    return out[:24]


def _fy_people_names():
    names = []
    try:
        entries = sorted(os.scandir(FY_PEOPLE_DIR), key=lambda e: e.name)
    except OSError:
        return names
    for e in entries:
        if not e.name.endswith(".md") or e.name.startswith("."):
            continue
        name = ""
        try:
            with open(e.path, encoding="utf-8", errors="replace") as f:
                head = re.sub(r"<!--.*?-->", " ", f.read(400), flags=re.S)
            m = re.search(r"^#\s+(.+)$", head, re.M)
            if m:
                name = _fy_clean(m.group(1))
        except OSError:
            pass
        if not name:
            name = _fy_clean(e.name[:-3].replace("-", " ").replace("_", " "))
        if 2 <= len(name) <= 60:
            names.append(name.title() if name.islower() else name)
        if len(names) >= 60:
            break
    return names


def _fy_tokens(text):
    return [t for t in re.findall(r"[a-z0-9+#]{3,}", str(text or "").lower())
            if t not in _FY_STOP and not t.isdigit()]


def _fy_profile():
    """{"empty", "entries":{cat:[...]}, "terms":{token:(weight,cat)}, "people"}."""
    entries, terms = {}, {}
    for fname, cat, weight in FY_FILES:
        es = _fy_read_entries(fname)
        entries[cat] = es
        if weight <= 0:
            continue
        for e in es:
            for tok in _fy_tokens(e):
                old = terms.get(tok)
                if old is None or weight > old[0]:
                    terms[tok] = (weight, cat)
    people = _fy_people_names()
    empty = not terms and not people
    return {"empty": empty, "entries": entries, "terms": terms, "people": people}


def _fy_profile_text(prof, limit=1500):
    """Compact You-Model summary for the single batched model pass."""
    parts = []
    for cat, label in (("goal", "GOALS"), ("now", "WORKING ON NOW"),
                       ("looking-for", "LOOKING FOR"), ("interest", "INTERESTS"),
                       ("preference", "PREFERENCES")):
        es = prof["entries"].get(cat) or []
        if es:
            parts.append(label + ":\n" + "\n".join("- " + e[:170] for e in es[:6]))
    if prof["people"]:
        parts.append("KEY PEOPLE: " + ", ".join(prof["people"][:20]))
    return "\n".join(parts)[:limit]


# --------------------------------------------------------------------------
# reactions — the useful/noise label stream (future learned P(useful) gate).
# v1 applies a LIGHT down-weight: noise on a source shrinks that source's
# future lexical scores; noise on a matched goal shaves items overlapping it.
# Attribution is to the specific source/goal that fired, never global (§5).
# --------------------------------------------------------------------------
def _fy_reactions_tail(n=400):
    out = []
    try:
        with open(FY_REACTS_FILE, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-n:]
        for ln in lines:
            try:
                r = json.loads(ln)
                if isinstance(r, dict):
                    out.append(r)
            except Exception:
                continue
    except OSError:
        pass
    return out


def _fy_reaction_weights():
    """(source -> multiplier, set of noise-flagged goal tokens)."""
    src_net, goal_noise = {}, {}
    for r in _fy_reactions_tail():
        delta = 1 if r.get("reaction") == "noise" else -1
        s = _fy_clean(r.get("source"))[:40].lower()
        if s:
            src_net[s] = src_net.get(s, 0) + delta
        if r.get("reaction") == "noise":
            for tok in _fy_tokens(r.get("matched_goal")):
                goal_noise[tok] = goal_noise.get(tok, 0) + 1
    src_mult = {s: 0.8 ** min(3, n) for s, n in src_net.items() if n > 0}
    noise_toks = {t for t, n in goal_noise.items() if n > 0}
    return src_mult, noise_toks


def _fy_react_append(rec):
    os.makedirs(DATA, exist_ok=True)
    fd = os.open(FY_REACTS_FILE, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


# --------------------------------------------------------------------------
# TIER 1 — cheap lexical pre-filter over ALL items. No model. (v1 stand-in
# for per-facet local embeddings — a Phase-later upgrade.)
# --------------------------------------------------------------------------
def _fy_prefilter(items, prof):
    """Score every intel item by weighted token overlap with the You-Model.
    Returns top FY_TOPK candidates over threshold, each:
    {item, score, matched:[(term,cat)], person}."""
    src_mult, noise_toks = _fy_reaction_weights()
    docs = []
    for it in items:
        if not isinstance(it, dict) or not it.get("url"):
            continue
        text = " ".join((str(it.get("title") or ""), str(it.get("summary") or ""),
                         str(it.get("topic") or "")))
        docs.append((it, text.lower(), set(_fy_tokens(text))))
    # document frequency damp: a term half the corpus shares says little
    df = {}
    for _it, _tx, toks in docs:
        for t in toks:
            if t in prof["terms"]:
                df[t] = df.get(t, 0) + 1
    n_docs = max(1, len(docs))
    now = time.time()
    scored = []
    for it, text_low, toks in docs:
        score, matched = 0.0, []
        for tok in toks:
            spec = prof["terms"].get(tok)
            if not spec:
                continue
            weight, cat = spec
            damp = 1.0 / (1.0 + 6.0 * df.get(tok, 0) / n_docs)
            score += weight * damp
            matched.append((tok, cat))
        person = ""
        for name in prof["people"]:
            if len(name) >= 5 and name.lower() in text_low:
                score += FY_PERSON_BOOST
                person = name
                break
        if score <= 0:
            continue
        # light learned-gate v0: source noise shrinks, noisy goals shave
        score *= src_mult.get(_fy_clean(it.get("source"))[:40].lower(), 1.0)
        if noise_toks:
            score -= 0.4 * min(3, len(toks & noise_toks))
        # perishability: fresher world signal matters more (plan §3 scoring)
        age = now - float(it.get("ts") or now)
        score *= 1.0 if age < 86400 else (0.9 if age < 3 * 86400 else 0.8)
        if score > 0:
            matched.sort(key=lambda m: -prof["terms"][m[0]][0])
            scored.append({"item": it, "score": round(score, 3),
                           "matched": matched[:4], "person": person})
    scored.sort(key=lambda c: -c["score"])
    over = [c for c in scored if c["score"] >= FY_MIN_SCORE]
    if len(over) < 4:                       # thin profile — don't starve tier 2
        over = scored[:FY_FALLBACK_N]
    return over[:FY_TOPK]


# --------------------------------------------------------------------------
# TIER 2 — ONE batched model pass on the survivors (mirrors _intel_curate's
# request/parse/validate style; URLs validated against the candidate pool)
# --------------------------------------------------------------------------
def _fy_chat_url():
    base = MODEL_URL
    if base.endswith("/v1/models"):
        return base[:-len("/models")] + "/chat/completions"
    return re.sub(r"/v1/models/?$", "/v1/chat/completions", base) \
        if "/v1/models" in base else "http://127.0.0.1:8080/v1/chat/completions"


def _fy_active_model():
    try:
        with open(ACTIVE_MODEL_FILE) as f:
            m = f.read().strip()
            if m:
                return m
    except OSError:
        pass
    return DEFAULT_MODEL


def _fy_model_ok():
    """MEMORY-CEILING GUARD — checked before EVERY model call. At/over the
    soft ceiling (mlx_admission not-ok) we skip the pass entirely and keep
    the last foryou.json. Also skips while paused/offline."""
    try:
        ok, _gb, _limit = mlx_admission()
    except Exception:
        return False                      # can't verify headroom -> don't spend it
    if not ok:
        return False
    try:
        return (not agent_paused()) and model_online()
    except Exception:
        return False


def _fy_chat_active():
    """Gentle: never compete with a live chat turn for the model."""
    try:
        return any(not (j or {}).get("done") for j in CHAT_JOBS.values())
    except Exception:
        return False


_FY_REASON_SYS = (
    "You are the reasoning engine of a personal chief-of-staff. You receive the "
    "user's private profile (goals, current projects, looking-for, interests, key "
    "people) and a list of candidate news/opportunity items. Select ONLY the items "
    "that genuinely matter to THIS user and turn each into one concrete move.\n"
    "Output ONLY a JSON array of objects with keys: title, url, why_you, "
    "matched_goal, matched_person, suggested_action, score.\n"
    "- why_you: ONE short line tying the item to a SPECIFIC goal, project, interest "
    "or person from the profile — name it.\n"
    "- matched_goal: the profile goal/project/interest it advances ('' if none).\n"
    "- matched_person: the profile person it involves ('' if none).\n"
    "- suggested_action: a short imperative — 'do X' / 'meet Y' / 'go to Z' / "
    "'read/try/apply ...'.\n"
    "- score: 0 to 1 — how much this matters to this user right now.\n"
    "Use ONLY the provided items. Copy each url EXACTLY as given — never invent or "
    "alter a URL. Omit items that don't matter to this user. No emoji, no prose, "
    "no code fence."
)


def _fy_extract_json(text):
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _fy_model_pass(cands, prof):
    """ONE chat completion over <=FY_TOPK candidates. Returns validated moves
    or [] (best-effort — caller falls back to the lexical tier)."""
    pool = {_fy_norm_url(c["item"].get("url")): c for c in cands}
    listing = "\n".join(
        "- %s | %s | %s | %s" % (
            _fy_clean(c["item"].get("title"))[:160],
            _fy_clean(c["item"].get("source"))[:40],
            _fy_clean(c["item"].get("url")),
            _fy_clean(c["item"].get("summary"))[:150])
        for c in cands)
    try:
        payload = json.dumps({
            "model": _fy_active_model(),
            "messages": [{"role": "system", "content": _FY_REASON_SYS},
                         {"role": "user", "content":
                          "USER PROFILE:\n" + _fy_profile_text(prof) +
                          "\n\nCANDIDATE ITEMS (title | source | url | summary):\n"
                          + listing}],
            "temperature": 0.3, "max_tokens": 1600, "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(_fy_chat_url(), data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
        text = (((resp.get("choices") or [{}])[0].get("message") or {})
                .get("content") or "").strip()
    except Exception as e:
        _fy_log("model pass failed: %r" % e)
        return []
    moves, seen = [], set()
    for o in _fy_extract_json(text):
        if not isinstance(o, dict):
            continue
        key = _fy_norm_url(o.get("url"))
        c = pool.get(key)
        if c is None or key in seen:        # reject invented/duplicate URLs
            continue
        seen.add(key)
        it = c["item"]
        try:
            score = max(0.0, min(1.0, float(o.get("score"))))
        except (TypeError, ValueError):
            score = 0.5
        moves.append({
            "title": _fy_clean(o.get("title") or it.get("title"))[:180],
            "url": str(it.get("url") or "").strip(),
            "source": _fy_clean(it.get("source"))[:40],
            "topic": _fy_clean(it.get("topic"))[:30],
            "ts": it.get("ts"),
            "why_you": _fy_clean(o.get("why_you"))[:220],
            "matched_goal": _fy_clean(o.get("matched_goal"))[:140],
            "matched_person": _fy_clean(o.get("matched_person"))[:80],
            "suggested_action": _fy_clean(o.get("suggested_action"))[:140],
            "score": round(score, 3),
        })
    moves.sort(key=lambda m: -(m["score"] or 0))
    return moves[:FY_MAX_MOVES]


def _fy_lexical_moves(cands, prof):
    """Deterministic fallback when the model tier is unavailable/failed —
    the pre-filter's own evidence, honestly labelled (reasoned:false)."""
    top = max((c["score"] for c in cands), default=1.0) or 1.0
    moves = []
    for c in cands[:FY_MAX_MOVES]:
        it = c["item"]
        terms = [t for t, _cat in c["matched"]]
        cat = c["matched"][0][1] if c["matched"] else "interest"
        goal = ""
        for e in prof["entries"].get(cat) or []:
            if any(t in e.lower() for t in terms):
                goal = e[:120]
                break
        why = ("Involves %s from your people cards" % c["person"]) if c["person"] \
            else ("Overlaps your %s: %s" % (FY_CAT_LABEL.get(cat, cat),
                                            ", ".join(terms[:3]) or "profile"))
        moves.append({
            "title": _fy_clean(it.get("title"))[:180],
            "url": str(it.get("url") or "").strip(),
            "source": _fy_clean(it.get("source"))[:40],
            "topic": _fy_clean(it.get("topic"))[:30],
            "ts": it.get("ts"),
            "why_you": why[:220],
            "matched_goal": goal,
            "matched_person": c["person"],
            "suggested_action": "Read: " + _fy_clean(it.get("title"))[:90],
            "score": round(min(1.0, c["score"] / top), 3),
        })
    return moves


# --------------------------------------------------------------------------
# the build — profile -> pre-filter -> ONE admission-gated model pass -> store
# --------------------------------------------------------------------------
def _foryou_build(force=False):
    """Serialized rebuild. Returns the current store (fresh or kept)."""
    if not _fy_build_lock.acquire(blocking=False):
        return read_json(FORYOU_FILE, {}) or {}
    try:
        now = time.time()
        prev = read_json(FORYOU_FILE, None)
        items = _fy_intel_items()
        base = {"generated_at": now, "intel_mtime": _fy_intel_mtime(),
                "interval_s": FY_INTERVAL}
        prof = _fy_profile()

        def _generic_fallback(reason):
            fresh = sorted(items, key=lambda it: -(it.get("ts") or 0))
            moves = [{
                "title": _fy_clean(it.get("title"))[:180],
                "url": str(it.get("url") or "").strip(),
                "source": _fy_clean(it.get("source"))[:40],
                "topic": _fy_clean(it.get("topic"))[:30],
                "ts": it.get("ts"),
                "why_you": "", "matched_goal": "", "matched_person": "",
                "suggested_action": "", "score": None,
            } for it in fresh[:FY_FALLBACK_N]]
            store = dict(base, personalized=False, reasoned=False, moves=moves,
                         note="complete onboarding to personalize")
            _fy_write_store(store)
            _fy_log("built (generic fallback — %s, %d raw items)"
                    % (reason, len(moves)))
            return store

        # fresh install: no You-Model yet -> raw top intel + the onboarding flag
        if prof["empty"]:
            return _generic_fallback("You-Model empty")

        cands = _fy_prefilter(items, prof)
        if not cands and not prof["terms"]:
            # only people cards, none mentioned in intel: still generic
            return _generic_fallback("people-only model, no mentions")
        if not cands:
            store = dict(base, personalized=True, reasoned=False, moves=[],
                         note="no intel matched your model this pass")
            _fy_write_store(store)
            _fy_log("built (personalized, 0 candidates over threshold)")
            return store

        # TIER 2 — admission-gated, ONE batched call, <=FY_TOPK items
        if _fy_model_ok():
            moves = _fy_model_pass(cands, prof)
            if moves:
                store = dict(base, personalized=True, reasoned=True, moves=moves)
                _fy_write_store(store)
                _fy_log("built (personalized, model pass: %d candidates -> %d moves)"
                        % (len(cands), len(moves)))
                return store
            # model was reachable but returned nothing usable -> lexical tier
        else:
            # ceiling engaged / model down: KEEP the last store rather than
            # overwrite a good reasoned build with a degraded one (plan §7)
            if prev and prev.get("moves"):
                _fy_log("model unavailable (admission/offline) — kept last store")
                return prev

        moves = _fy_lexical_moves(cands, prof)
        store = dict(base, personalized=True, reasoned=False, moves=moves,
                     note="model pass unavailable — lexical match only")
        _fy_write_store(store)
        _fy_log("built (personalized, lexical fallback: %d moves)" % len(moves))
        return store
    except Exception as e:
        _fy_log("build failed: %r" % e)
        return read_json(FORYOU_FILE, {}) or {}
    finally:
        _fy_build_lock.release()


def _fy_kick_async(min_gap=60):
    """Throttled background build (used when the store doesn't exist yet)."""
    now = time.time()
    if now - _fy_last_kick[0] < min_gap or _fy_build_lock.locked():
        return
    _fy_last_kick[0] = now
    threading.Thread(target=_foryou_build, daemon=True).start()


# --------------------------------------------------------------------------
# cadence — a light daemon: rebuild every ~FY_INTERVAL AND when intel.json's
# mtime moves, but only when the ceiling has headroom and no chat turn is
# live. Serialized, not continuous — the loop only CHECKS every 5 minutes.
# --------------------------------------------------------------------------
def foryou_loop():
    time.sleep(120)                      # let the hub finish booting first
    while True:
        try:
            store = read_json(FORYOU_FILE, {}) or {}
            gen = float(store.get("generated_at") or 0)
            im = _fy_intel_mtime()
            due = ((time.time() - gen) >= FY_INTERVAL or
                   (im and im > float(store.get("intel_mtime") or 0) + 1))
            if due and not _fy_chat_active():
                ok = True
                try:
                    ok, _gb, _lim = mlx_admission()   # gentle: skip the whole
                except Exception:                     # pass under pressure
                    ok = False
                if ok:
                    _foryou_build()
        except Exception as e:
            _fy_log("loop error: %r" % e)
        time.sleep(FY_LOOP_TICK)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def foryou_get_handler(ctx):
    store = read_json(FORYOU_FILE, None)
    if not isinstance(store, dict) or "moves" not in store:
        _fy_kick_async()
        return {"ok": True, "building": True, "generated_at": None,
                "personalized": False, "reasoned": False, "moves": []}
    out = dict(store)
    out["ok"] = True
    return out


def foryou_refresh_handler(ctx):
    b = ctx.body or {}
    if b.get("wait"):
        store = _foryou_build(force=True)
        out = dict(store)
        out["ok"] = True
        return out
    threading.Thread(target=_foryou_build, daemon=True).start()
    return {"ok": True, "started": True}


def foryou_react_handler(ctx):
    b = ctx.body or {}
    reaction = b.get("reaction")
    if reaction not in ("useful", "noise"):
        return ({"ok": False, "error": "reaction must be 'useful' or 'noise'"}, 400)
    url = str(b.get("url") or b.get("id") or "").strip()
    if not url:
        return ({"ok": False, "error": "need url|id"}, 400)
    key = _fy_norm_url(url)
    store = read_json(FORYOU_FILE, {}) or {}
    mv = None
    for m in store.get("moves") or []:
        if _fy_norm_url(m.get("url")) == key:
            mv = m
            break
    rec = {"ts": time.time(), "url": url, "reaction": reaction,
           "source": (mv or {}).get("source", ""),
           "matched_goal": (mv or {}).get("matched_goal", ""),
           "title": (mv or {}).get("title", "")}
    try:
        _fy_react_append(rec)
    except Exception as e:
        return ({"ok": False, "error": "log failed: " + str(e)}, 500)
    if mv is not None:                    # reflect in the panel immediately
        mv["reaction"] = reaction
        try:
            _fy_write_store(store)
        except Exception:
            pass
    return {"ok": True, "logged": rec}


# --------------------------------------------------------------------------
# hub widget provider + expander (the Agent-Inbox surface, WS 1.5)
# --------------------------------------------------------------------------
def _fy_move_view(m):
    return {k: m.get(k) for k in ("title", "url", "source", "topic", "ts",
                                  "why_you", "matched_goal", "matched_person",
                                  "suggested_action", "score", "reaction")}


def w_foryou():
    store = read_json(FORYOU_FILE, None)
    if not isinstance(store, dict) or "moves" not in store:
        _fy_kick_async()
        return {"available": True, "building": True, "personalized": False,
                "reasoned": False, "moves": [], "count": 0}
    moves = store.get("moves") or []
    return {"available": True, "building": False,
            "personalized": bool(store.get("personalized")),
            "reasoned": bool(store.get("reasoned")),
            "generated_at": store.get("generated_at"),
            "note": store.get("note", ""), "count": len(moves),
            "moves": [_fy_move_view(m) for m in moves[:4]]}


def expand_foryou():
    store = read_json(FORYOU_FILE, None) or {}
    moves = store.get("moves") or []
    useful = noise = 0
    for r in _fy_reactions_tail():
        if r.get("reaction") == "useful":
            useful += 1
        elif r.get("reaction") == "noise":
            noise += 1
    return {"available": True,
            "personalized": bool(store.get("personalized")),
            "reasoned": bool(store.get("reasoned")),
            "generated_at": store.get("generated_at"),
            "note": store.get("note", ""),
            "interval_s": store.get("interval_s", FY_INTERVAL),
            "moves": [_fy_move_view(m) for m in moves],
            "reactions": {"useful": useful, "noise": noise}}


# --------------------------------------------------------------------------
# module-load side effects: routes, widget catalog, layout inject, daemon
# --------------------------------------------------------------------------
register_get("/api/foryou", foryou_get_handler)
register_post("/api/foryou/refresh", foryou_refresh_handler)
register_post("/api/foryou/react", foryou_react_handler)

WIDGETS["foryou"] = {"title": "For You", "icon": "spark", "size": "card",
                     "cat": "agent", "provider": w_foryou}
EXPANDERS["foryou"] = expand_foryou

# appear without a manual add — append to the layout order IF absent
try:
    _fy_lay = get_layout()
    if isinstance(_fy_lay, dict):
        _fy_order = _fy_lay.get("order")
        if not isinstance(_fy_order, list):
            _fy_order = _fy_lay["order"] = []
        if "foryou" not in _fy_order:
            _fy_order.insert(0, "foryou")      # the lead panel — §5: "why this
            save_layout(_fy_lay)               # is for you" comes first
except Exception as _fy_e:                                    # pragma: no cover
    _fy_log("layout inject failed: %s" % _fy_e)

if not globals().get("_foryou_thread_started"):
    globals()["_foryou_thread_started"] = True
    try:
        threading.Thread(target=foryou_loop, daemon=True).start()
    except Exception as _fy_e:                                # pragma: no cover
        _fy_log("daemon failed to start: %r" % _fy_e)
