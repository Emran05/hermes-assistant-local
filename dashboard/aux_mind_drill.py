# aux_mind_drill.py — P2.6a: multi-day drill-downs for the Mind view analytics.
#
# exec'd into server.py's globals by the aux-module loader (after
# expanders_extra.py, sorted with the other aux_*.py).  It may use these
# server.py globals: STATE_DB, _cached, register_get.  It imports ALL its own
# stdlib deps (exec'd code can't rely on server.py's function-local imports)
# and defines only new names (MDR_*, _mdr_*) so it clobbers nothing.
# Per CLAUDE.md: NO `from datetime import datetime` here — this module needs
# no datetime at all (the `time` module covers local-day bucketing).
#
# Route: GET /api/mind_drill?days=N  (N clamped to 7..90, default 30)
#   -> {days, tokens_by_day: [{d,in_tok,out_tok}] zero-filled,
#       sessions_by_day: [{d,n}], model_mix: [{name,sessions}],
#       skill_usage: [{name,count}] bounded to the window,
#       busiest_day: {d,in_tok,out_tok,tokens,sessions} | None,
#       totals: {...}, generated}
# Reads state.db strictly READ-ONLY (file:...?mode=ro, timeout=2.0 — same
# pattern as mind_extra).  Cached 300s per distinct window.  Never raises:
# any failure degrades to {"error": ...} or a per-section *_error key.

import json
import re
import time
import sqlite3
import urllib.parse

MDR_MIN_DAYS = 7
MDR_MAX_DAYS = 90
MDR_DEFAULT_DAYS = 30
MDR_CACHE_SECS = 300


def _mdr_clamp_days(raw):
    """'30' -> 30; garbage -> default; always clamped to [7, 90]."""
    try:
        d = int(str(raw).strip())
    except (TypeError, ValueError):
        d = MDR_DEFAULT_DAYS
    return max(MDR_MIN_DAYS, min(MDR_MAX_DAYS, d))


def _mdr_short_model(name):
    """Same short-name cleanup mind_extra uses for the model mix."""
    t = (name or "unknown").split("/")[-1]
    t = re.sub(r"-(\d+bit|bf16|fp16|fp32)$", "", t, flags=re.I)
    t = re.sub(r"-Instruct(-\d+)?$", "", t, flags=re.I)
    return t


def _mdr_build(days):
    out = {
        "days": days,
        "generated": time.time(),
        "tokens_by_day": [],
        "sessions_by_day": [],
        "model_mix": [],
        "skill_usage": [],
        "busiest_day": None,
        "totals": {},
    }
    try:
        db = sqlite3.connect(
            "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro",
            uri=True, timeout=2.0)
        db.row_factory = sqlite3.Row
    except Exception as e:
        out["error"] = "state.db unavailable: %s" % e
        return out

    # window: today's local midnight back (days-1) whole days, zero-filled
    now = time.time()
    day0 = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    start = day0 - (days - 1) * 86400
    buckets = {}
    order = []
    for i in range(days):
        # +3600 keeps the label stable across a DST boundary (mind_extra idiom)
        d = time.strftime("%Y-%m-%d", time.localtime(start + i * 86400 + 3600))
        buckets[d] = {"d": d, "in_tok": 0, "out_tok": 0, "sessions": 0}
        order.append(d)

    # (1) tokens + session count per local day, from sessions
    try:
        for row in db.execute(
                "SELECT started_at, COALESCE(input_tokens,0) it, "
                "COALESCE(output_tokens,0) ot "
                "FROM sessions WHERE started_at >= ?", (start,)):
            d = time.strftime("%Y-%m-%d", time.localtime(row["started_at"]))
            b = buckets.get(d)
            if b:
                b["in_tok"] += row["it"]
                b["out_tok"] += row["ot"]
                b["sessions"] += 1
    except Exception as e:
        out["tokens_error"] = str(e)
    out["tokens_by_day"] = [
        {"d": d, "in_tok": buckets[d]["in_tok"], "out_tok": buckets[d]["out_tok"]}
        for d in order]
    out["sessions_by_day"] = [{"d": d, "n": buckets[d]["sessions"]} for d in order]

    # (2) sessions per model (short name) inside the window
    try:
        mix = {}
        for row in db.execute(
                "SELECT COALESCE(model,'unknown') m, COUNT(*) n FROM sessions "
                "WHERE started_at >= ? GROUP BY m ORDER BY n DESC", (start,)):
            t = _mdr_short_model(row["m"])
            mix[t] = mix.get(t, 0) + row["n"]
        out["model_mix"] = [
            {"name": k, "sessions": v}
            for k, v in sorted(mix.items(), key=lambda kv: -kv[1])]
    except Exception as e:
        out["model_mix_error"] = str(e)

    # (3) skills actually USED inside the window: skill_view tool calls,
    # skill name in the arguments JSON (mind_extra idiom, but time-bounded)
    try:
        counts = {}
        for (tc,) in db.execute(
                "SELECT tool_calls FROM messages "
                "WHERE timestamp >= ? AND tool_calls LIKE '%skill_view%'",
                (start,)):
            try:
                calls = json.loads(tc)
            except Exception:
                continue
            if not isinstance(calls, list):
                continue
            for call in calls:
                try:
                    fn = (call or {}).get("function") or {}
                    if fn.get("name") != "skill_view":
                        continue
                    args = json.loads(fn.get("arguments") or "{}")
                    nm = (args.get("name") or "").strip()
                    if nm:
                        counts[nm] = counts.get(nm, 0) + 1
                except Exception:
                    continue
        out["skill_usage"] = [
            {"name": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
    except Exception as e:
        out["skill_usage_error"] = str(e)

    try:
        db.close()
    except Exception:
        pass

    # (4) busiest day (by total tokens, sessions as tie-breaker) + totals
    best = None
    tin = tout = tsess = active = 0
    for d in order:
        b = buckets[d]
        tot = b["in_tok"] + b["out_tok"]
        tin += b["in_tok"]
        tout += b["out_tok"]
        tsess += b["sessions"]
        if tot > 0 or b["sessions"] > 0:
            active += 1
            key = (tot, b["sessions"])
            if best is None or key > (best["tokens"], best["sessions"]):
                best = {"d": d, "in_tok": b["in_tok"], "out_tok": b["out_tok"],
                        "tokens": tot, "sessions": b["sessions"]}
    out["busiest_day"] = best
    out["totals"] = {
        "in_tok": tin,
        "out_tok": tout,
        "tokens": tin + tout,
        "sessions": tsess,
        "active_days": active,
        "skill_invocations": sum(s["count"] for s in out["skill_usage"]),
        "models": len(out["model_mix"]),
    }
    return out


def _mdr_route(ctx):
    days = _mdr_clamp_days(ctx.q1("days", str(MDR_DEFAULT_DAYS)))
    try:
        return _cached("mind_drill_%d" % days, MDR_CACHE_SECS,
                       lambda: _mdr_build(days))
    except Exception as e:
        return {"error": str(e)}


register_get("/api/mind_drill", _mdr_route)
