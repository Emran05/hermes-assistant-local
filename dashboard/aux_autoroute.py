# aux_autoroute.py — deterministic "should this turn ALSO go to Claude?" router
#
# Problem it fixes (2026-08-18): the two-brain design left the escalation
# decision to the local model via the think-with-claude skill — which it never
# used (bridge log: 4 calls, all 07-06). Only the manual "Escalate to Claude"
# button ever routed. This module makes the routing decision in code, per chat
# turn, so hard questions reliably get Claude's answer while routine ones stay
# local and free.
#
# exec'd into server.py's globals by the aux loader (sorted BEFORE
# aux_claudebridge.py, so `claude_think` is looked up at CALL time via
# globals()). It wraps two server.py globals — `_new_job` (so the job dict can
# hold `done` open while Claude thinks) and `_chat_worker` (runs the local turn,
# then decides + routes) — and adds routes:
#   GET  /api/claude/autoroute            {mode, min_score, stats}
#   POST /api/claude/autoroute {mode}     "auto" | "suggest" | "off"
#   POST /api/claude/autoroute/score {q}  dry-run the scorer (debug)
#   GET  /api/claude/autoroute/job?job=   {deep} — the UI polls this after the
#        local turn is done while deep.state == "thinking"
# /api/chat/poll also carries `deep` (state thinking|done|suggest, text, model,
# ms, reason); the UI (index.html streamJob → window.hermesDeep in aux_agent.js)
# renders it as the same deep-card the manual button uses; the answer is
# persisted as a bot message with `deep:{...}` so history shows it.
#
# Decision = a transparent score over the USER's message (never the preamble):
#   +3  explicit ask ("think hard/deeply", "ask claude", "escalate", "second opinion")
#   +1  each judgment cue (should I / worth it / compare / tradeoffs / recommend /
#       decide / strategy / plan for / why does / design / risk / evaluate …), max 3
#   +1  long question (>= 25 words)   +0.5 ends with "?"
#   -1  each mechanical cue (summarize / translate / list / convert / weather /
#       remind / open / run / count / search for / what time …)
#   -2  very short (< 6 words)         -3  looks like a command/tool ask ("run", "open")
# route when score >= min_score (default 2.5). Depth: "deep" only on an explicit
# "think hard/deeply" ask, else "quick" (Sonnet). Every routed turn logs its
# reason (bridge log has the call; the chat message carries the reason).
#
# Modes: auto (route + answer inline) · suggest (just flag: UI highlights the
# escalate button with the reason) · off. Stored in settings.json → auto_route.
# The GLOBAL master switch (settings.json claude_escalation.enabled, owned by
# aux_claudebridge) overrides all three: off = treat every turn as mode "off".

import re
import sys
import json
import time
import threading

AR_DEFAULT_MODE = "auto"
AR_DEFAULT_MIN = 2.5
AR_MAX_TASK = 4000          # chars of user message passed to Claude
AR_MAX_REPLY = 3000         # chars of the local reply passed as context

_AR_EXPLICIT = re.compile(
    r"\b(?:think\s+(?:hard|harder|deeply|carefully|it\s+through)|deep\s*(?:think|dive)"
    r"|ask\s+claude|use\s+claude|escalate|second\s+opinion|really\s+think|reason\s+(?:carefully|deeply)"
    r"|go\s+deep(?:er)?|thorough(?:ly)?\s+analy)", re.I)
_AR_DEEP = re.compile(r"\b(?:think\s+(?:hard|harder|deeply)|deep\s*(?:think|dive)|go\s+deep|opus)\b", re.I)
_AR_JUDGMENT = re.compile(
    r"\b(?:should\s+(?:i|we)|would\s+you|is\s+it\s+worth|worth\s+it|which\s+(?:is|one|should)|better"
    r"|compare|comparison|versus|\bvs\.?\b|trade-?offs?|pros?\s+and\s+cons?|recommend\w*|advice|advise"
    r"|decide|decision|strategy|strateg\w+|plan\s+for|roadmap|why\s+(?:does|is|do|are|did|would|can't|isn't)"
    r"|how\s+should|how\s+would|design|architect\w*|risk\w*|evaluate|assess|analy[sz]e|weigh|opportunit\w+"
    r"|implications?|consequences?|reason\s+about|think\s+about|what\s+do\s+you\s+think|opinion|argue|argument"
    r"|explain\s+why|root\s+cause|diagnos\w+|priorit\w+|worth\s+(?:doing|pursuing|building)|feasib\w+"
    r"|negotiat\w+|career|relationship|cofounder|investor|hire|hiring|pricing|business\s+model)\b", re.I)
_AR_MECHANICAL = re.compile(
    r"\b(?:summari[sz]e|tl;?dr|translate|list\s+(?:the|all|my)|convert|format|reformat|rename|typo|proofread"
    r"|what\s+time|what's\s+the\s+time|weather|forecast|remind\s+me|set\s+a\s+(?:timer|reminder|alarm)"
    r"|open\s+(?:the|my|a)|launch|run\s+(?:the|this|a|my)|execute|kill|restart|count\s+(?:the|how)|how\s+many\s+files"
    r"|search\s+(?:for|the\s+web)|look\s+up|google|find\s+(?:me\s+)?(?:the|a)|show\s+me|read\s+(?:the|this|my)\s+file"
    r"|copy|paste|download|install|update\s+(?:the|my)|check\s+(?:the|my|if)|status\s+of|what\s+is\s+the\s+capital"
    r"|define|definition\s+of|spell|calculate|compute|sum\s+of|hello|hi\b|thanks|thank\s+you)\b", re.I)
_AR_COMMANDISH = re.compile(r"^\s*(?:run|open|launch|kill|restart|install|download|copy|move|delete|remove|start|stop|show|list|find|search|read|cat|ls|cd|git|npm|pip|curl)\b", re.I)


def ar_score(text):
    """Return (score, reasons[]) — transparent, deterministic."""
    t = (text or "").strip()
    if not t:
        return 0.0, ["empty"]
    words = len(t.split())
    score, why = 0.0, []
    if _AR_EXPLICIT.search(t):
        score += 3; why.append("explicit ask")
    j = len(set(m.group(0).lower() for m in _AR_JUDGMENT.finditer(t)))
    if j:
        add = min(3, j); score += add; why.append("judgment cues×%d" % j)
    if words >= 25:
        score += 1; why.append("long")
    if t.rstrip().endswith("?"):
        score += 0.5; why.append("question")
    m = len(set(x.group(0).lower() for x in _AR_MECHANICAL.finditer(t)))
    if m:
        score -= m; why.append("mechanical×%d" % m)
    if words < 6:
        score -= 2; why.append("very short")
    if _AR_COMMANDISH.match(t):
        score -= 3; why.append("command-like")
    return round(score, 1), why


def _ar_escalation_on():
    """The Claude master switch (aux_claudebridge.claude_escalation_enabled).
    Looked up through globals() at CALL time because aux_autoroute sorts BEFORE
    aux_claudebridge in the aux loader, so the name does not exist yet at exec
    time. Missing helper = bridge module absent = treat as ON and let
    claude_think's own absence do the gating (fail open, same as claude_think)."""
    fn = globals().get("claude_escalation_enabled")
    try:
        return bool(fn()) if callable(fn) else True
    except Exception:
        return True


def _ar_settings():
    try:
        s = get_settings() or {}
    except Exception:
        s = {}
    cfg = s.get("auto_route") if isinstance(s.get("auto_route"), dict) else {}
    mode = str(cfg.get("mode") or AR_DEFAULT_MODE).lower()
    if mode not in ("auto", "suggest", "off"):
        mode = AR_DEFAULT_MODE
    try:
        mn = float(cfg.get("min_score", AR_DEFAULT_MIN))
    except (TypeError, ValueError):
        mn = AR_DEFAULT_MIN
    return mode, mn


def _ar_set_mode(mode, min_score=None):
    mode = str(mode or "").lower()
    if mode not in ("auto", "suggest", "off"):
        return False
    s = read_json(SETTINGS_FILE, {}) or {}
    cfg = s.get("auto_route") if isinstance(s.get("auto_route"), dict) else {}
    cfg["mode"] = mode
    if min_score is not None:
        try:
            cfg["min_score"] = float(min_score)
        except (TypeError, ValueError):
            pass
    s["auto_route"] = cfg
    write_json(SETTINGS_FILE, s)
    return True


_AR_STATS = {"turns": 0, "routed": 0, "suggested": 0, "refused": 0, "failed": 0,
             "last": None}
_AR_LOCK = threading.Lock()

_ar_orig_worker = _chat_worker


def _ar_last_user_text(session):
    try:
        for m in reversed(load_chat(session).get("messages") or []):
            if m.get("role") == "user":
                return m.get("text") or ""
    except Exception:
        pass
    return ""


def _ar_persist(session, entry):
    try:
        chat = load_chat(session)
        chat["messages"].append(entry)
        save_chat(session, chat)
    except Exception as e:
        print("[aux_autoroute] persist failed: %r" % e, file=sys.stderr)


def _ar_think_thread(job, session, q, depth, reason, score):
    """Runs IN PARALLEL with the local turn (Sonnet quick ≈ 5-8s, so the deep
    answer usually lands about when the local reply does). Persists AFTER the
    local turn is done so the chat history keeps user → local → Claude order."""
    think = globals().get("claude_think")
    t0 = time.time()
    try:
        task = (q[:AR_MAX_TASK] +
                "\n\n(Answer this properly and concretely — reason it through, name the "
                "considerations and give a best-reasoned recommendation. A smaller local "
                "assistant is answering in parallel; you are the deeper second brain.)")
        res = think(task, user_context="", depth=depth) if callable(think) else \
            {"ok": False, "error": "bridge unavailable"}
    except Exception as e:
        res = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    ms = int((time.time() - t0) * 1000)
    # wait (bounded) for the local turn so persistence order is sane
    for _ in range(2000):                            # ≤ 10 min
        if job.get("done"):
            break
        time.sleep(0.3)
    if res.get("ok") and (res.get("text") or "").strip():
        entry = {"state": "done", "ok": True, "text": res["text"], "model": res.get("model"),
                 "ms": res.get("ms") or ms, "reason": reason, "score": score,
                 "depth": depth, "ts": time.time(), "auto": True}
        job["deep"] = entry
        _ar_persist(session, {"role": "bot", "text": res["text"], "ts": entry["ts"],
                              "deep": {k: entry[k] for k in ("model", "ms", "reason", "score", "depth", "auto")}})
        with _AR_LOCK:
            _AR_STATS["routed"] += 1
            _AR_STATS["last"] = {"ts": entry["ts"], "reason": reason, "model": entry["model"], "ms": entry["ms"]}
    else:
        job["deep"] = {"state": "done", "ok": False, "reason": reason, "score": score, "depth": depth,
                       "refused": bool(res.get("refused")),
                       "error": (res.get("text") if res.get("refused") else res.get("error")) or "Claude unavailable"}
        with _AR_LOCK:
            _AR_STATS["refused" if res.get("refused") else "failed"] += 1
        print("[aux_autoroute] route failed (%s): %s" % (reason, str(job["deep"]["error"])[:120]), file=sys.stderr)


def _ar_before(job, session):
    """Decide from the user's question and (maybe) start Claude in parallel."""
    mode, min_score = _ar_settings()
    # Master switch OFF behaves EXACTLY like mode "off", and is checked here
    # rather than only at the choke point so a disabled bridge costs nothing:
    # no scoring, no thread spawn, and — critically — no job["deep"] =
    # {"state": "thinking"} that would make the UI render a deep-card spinner
    # for an answer that can never arrive. The stored auto_route.mode is NOT
    # touched: flipping escalation back on must restore the user's routing mode
    # exactly as they left it.
    if mode == "off" or not _ar_escalation_on():
        return
    q = _ar_last_user_text(session)
    score, why = ar_score(q)
    with _AR_LOCK:
        _AR_STATS["turns"] += 1
    if score < min_score:
        return
    reason = "score %.1f: %s" % (score, ", ".join(why))
    if mode == "suggest":
        job["deep"] = {"state": "suggest", "reason": reason, "score": score}
        with _AR_LOCK:
            _AR_STATS["suggested"] += 1
        return
    if not callable(globals().get("claude_think")):
        return
    depth = "deep" if _AR_DEEP.search(q) else "quick"
    job["deep"] = {"state": "thinking", "reason": reason, "score": score, "depth": depth}
    threading.Thread(target=_ar_think_thread, args=(job, session, q, depth, reason, score),
                     daemon=True).start()


def _chat_worker(job, session, prompt):
    try:
        _ar_before(job, session)
    except Exception as e:                            # never break a turn
        print("[aux_autoroute] before-hook failed: %r" % e, file=sys.stderr)
    _ar_orig_worker(job, session, prompt)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def _ar_get(ctx):
    mode, mn = _ar_settings()
    with _AR_LOCK:
        st = dict(_AR_STATS)
    # claude_escalation rides along so a UI that already polls this endpoint can
    # render the master switch without a second request (the authoritative
    # read/write pair is GET/POST /api/claude/escalate in aux_claudebridge).
    return {"ok": True, "mode": mode, "min_score": mn, "stats": st,
            "claude_escalation": _ar_escalation_on()}


def _ar_post(ctx):
    b = ctx.body or {}
    if not _ar_set_mode(b.get("mode"), b.get("min_score")):
        return ({"ok": False, "error": "mode must be auto|suggest|off"}, 400)
    mode, mn = _ar_settings()
    return {"ok": True, "mode": mode, "min_score": mn}


def _ar_score_route(ctx):
    b = ctx.body or {}
    q = b.get("q") if isinstance(b.get("q"), str) else ""
    score, why = ar_score(q)
    mode, mn = _ar_settings()
    return {"ok": True, "score": score, "reasons": why, "routes": score >= mn,
            "depth": "deep" if _AR_DEEP.search(q or "") else "quick", "mode": mode, "min_score": mn}


def _ar_job(ctx):
    jid = (ctx.query.get("job") or [""])[0] if isinstance(ctx.query.get("job"), list) else (ctx.query.get("job") or "")
    job = CHAT_JOBS.get(jid)
    if not job:
        return ({"ok": False, "gone": True}, 404)
    return {"ok": True, "deep": job.get("deep"), "done": bool(job.get("done"))}


register_get("/api/claude/autoroute/job", _ar_job)
register_get("/api/claude/autoroute", _ar_get)
register_post("/api/claude/autoroute", _ar_post)
register_post("/api/claude/autoroute/score", _ar_score_route)
