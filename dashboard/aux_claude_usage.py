# aux_claude_usage.py — "Claude Usage" hub widget (local-first, read-only).
#
# exec'd into server.py's globals by the aux-module loader (after
# expanders_extra.py, sorted before the other aux_*.py). It may use these
# server.py globals: HOME, read_json, write_json, _cached, _widget_cache,
# register_get, register_post, get_layout, save_layout, WIDGETS, EXPANDERS.
# It imports ALL its own stdlib deps (exec'd code can't rely on server.py's
# function-local imports) and defines only new names (CU_*, _cu_*,
# w_claude_usage, expand_claude_usage) so it clobbers nothing.
#
# What it does: scans Claude Code's session logs at ~/.claude/projects/**/*.jsonl
# (assistant records carry message.usage + message.model + a top-level ISO
# timestamp), and reports the rolling 5-hour rate-limit window, today, a 7-day
# series, per-model / per-project breakdowns, block history, and an
# "≈ API-equivalent" cost. Read-only on ~/.claude; efficient (60s cache, bounded
# scan, skips files older than the window); never raises the hub down.
#
# Rate-limit modeling: Claude Max resets on a ROLLING 5-hour window (plus weekly
# caps) and publishes no hard token number, so we don't fabricate one. Instead we
# reconstruct the 5-hour "blocks" the way ccusage does — a block starts at the
# first activity (floored to the hour) and spans 5h; a gap >5h opens a new block —
# and surface the ACTIVE block's usage + its reset countdown, plus today/7-day
# totals and a sparkline against the user's own recent peak. An OPTIONAL soft cap
# (stored in our own JSON, no server.py edit) turns that into a % gauge.

import os
import sys
import json
import time
import datetime as _cu_datetime   # private alias: other aux modules do
                                  # `from datetime import datetime`, which would
                                  # rebind a bare `datetime` global to the class.

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
CU_PROJECTS    = os.path.join(HOME, ".claude", "projects")
CU_CONFIG_FILE = os.path.join(HOME, ".hermes", "dashboard", "claude-usage.json")

CU_BLOCK_SECS = 5 * 3600      # the rolling 5-hour rate-limit window
CU_SCAN_DAYS  = 8             # only read files modified within this many days
CU_MAX_FILES  = 800           # bound the scan
CU_MAX_LINES  = 400000        # per-file safety cap

# Published Claude API prices ($/1M tok in, $/1M tok out), keyed by model-family
# substring. Cost is labelled "≈ API-equivalent" — Max is a flat subscription.
# cache_read ~= 0.1x input, cache_creation (write) ~= 1.25x input.
CU_PRICES = {
    "fable":  (10.0, 50.0),
    "mythos": (10.0, 50.0),
    "opus":   (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku":  (1.0, 5.0),
}
CU_DEFAULT_PRICE = (3.0, 15.0)   # unknown family -> Sonnet-tier (neutral middle)
CU_CACHE_READ_MULT = 0.1
CU_CACHE_WRITE_MULT = 1.25


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _cu_family(model):
    m = (model or "").lower()
    for fam in ("fable", "mythos", "haiku", "sonnet", "opus"):
        if fam in m:
            return fam
    return "other"


def _cu_price(model):
    return CU_PRICES.get(_cu_family(model), CU_DEFAULT_PRICE)


def _cu_cost(model, inp, out, cr, cc):
    pin, pout = _cu_price(model)
    return (inp * pin + cr * pin * CU_CACHE_READ_MULT +
            cc * pin * CU_CACHE_WRITE_MULT + out * pout) / 1e6


def _cu_ts(rec):
    """Top-level ISO timestamp (e.g. 2026-07-05T15:27:00.439Z) -> epoch secs."""
    t = rec.get("timestamp")
    if not isinstance(t, str):
        return None
    try:
        return _cu_datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _cu_cfg():
    c = read_json(CU_CONFIG_FILE, {})
    return c if isinstance(c, dict) else {}


# --------------------------------------------------------------------------
# scan: walk ~/.claude/projects/**/*.jsonl (read-only, bounded, fault-tolerant)
# --------------------------------------------------------------------------
def _cu_scan():
    events = []            # (ts, family, model, in, out, cache_read, cache_write)
    sessions = set()
    proj_tok = {}          # project-dir -> aggregate
    proj_cwd = {}          # project-dir -> real cwd (for a friendly name)
    now = time.time()
    cutoff = now - CU_SCAN_DAYS * 86400

    files = []
    try:
        for root, dirs, fnames in os.walk(CU_PROJECTS):
            for fn in fnames:
                if not fn.endswith(".jsonl"):
                    continue
                p = os.path.join(root, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    continue
                files.append((st.st_mtime, p))
    except Exception:
        pass
    files.sort(reverse=True)           # newest first
    files = files[:CU_MAX_FILES]

    for _mt, p in files:
        try:
            rel = os.path.relpath(os.path.dirname(p), CU_PROJECTS)
            proj = rel.split(os.sep)[0] if rel and rel != "." else "?"
        except Exception:
            proj = "?"
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                n = 0
                for line in f:
                    n += 1
                    if n > CU_MAX_LINES:
                        break
                    if '"usage"' not in line:      # cheap skip before json.loads
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    msg = rec.get("message")
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    ts = _cu_ts(rec)
                    if ts is None:
                        continue
                    model = msg.get("model") or "?"
                    try:
                        inp = int(usage.get("input_tokens") or 0)
                        out = int(usage.get("output_tokens") or 0)
                        cr = int(usage.get("cache_read_input_tokens") or 0)
                        cc = int(usage.get("cache_creation_input_tokens") or 0)
                    except (TypeError, ValueError):
                        continue
                    if (inp + out + cr + cc) <= 0:
                        continue
                    events.append((ts, _cu_family(model), model, inp, out, cr, cc))
                    sid = rec.get("sessionId")
                    if sid:
                        sessions.add(sid)
                    if proj not in proj_cwd:
                        cw = rec.get("cwd")
                        if isinstance(cw, str) and cw:
                            proj_cwd[proj] = cw
                    pa = proj_tok.get(proj)
                    if pa is None:
                        pa = proj_tok[proj] = {"in": 0, "out": 0, "cr": 0,
                                               "cc": 0, "cost": 0.0, "msgs": 0}
                    pa["in"] += inp; pa["out"] += out
                    pa["cr"] += cr; pa["cc"] += cc
                    pa["cost"] += _cu_cost(model, inp, out, cr, cc)
                    pa["msgs"] += 1
        except OSError:
            continue

    events.sort(key=lambda e: e[0])
    return events, sessions, proj_tok, proj_cwd


# --------------------------------------------------------------------------
# build: aggregate the scanned events into the full payload
# --------------------------------------------------------------------------
def _cu_agg():
    return {"in": 0, "out": 0, "cr": 0, "cc": 0, "total": 0, "cost": 0.0, "msgs": 0}


def _cu_add(a, e):
    _ts, _fam, model, inp, out, cr, cc = e
    a["in"] += inp; a["out"] += out; a["cr"] += cr; a["cc"] += cc
    # "total" is REAL work = input + output. Cache-READ tokens (the same cached
    # prompt prefix re-served on every message) are NOT counted here — including
    # them made the headline 10-100x too high (a session's context is re-read
    # every turn). Cache read/write are surfaced separately as `cache`.
    a["total"] += inp + out
    a["cost"] += _cu_cost(model, inp, out, cr, cc)
    a["msgs"] += 1


def _cu_pack(a):
    return {"in": a["in"], "out": a["out"], "cr": a["cr"], "cc": a["cc"],
            "cache": a["cr"] + a["cc"], "total": a["total"],
            "cost": round(a["cost"], 4), "msgs": a["msgs"]}


def _cu_proj_name(name, cwd):
    if isinstance(cwd, str) and cwd:
        base = os.path.basename(cwd.rstrip("/"))
        return base or cwd
    return (name or "?").lstrip("-")[:40] or "?"


def _cu_build():
    events, sessions, proj_tok, proj_cwd = _cu_scan()
    now = time.time()

    # --- rolling 5-hour blocks (ccusage-style) ---
    blocks = []
    cur = None
    last_ts = None
    for e in events:
        ts = e[0]
        new_block = (cur is None
                     or (ts - cur["start"] >= CU_BLOCK_SECS)
                     or (last_ts is not None and ts - last_ts >= CU_BLOCK_SECS))
        if new_block:
            start = ts - (ts % 3600)          # floor first activity to the hour
            cur = _cu_agg()
            cur["start"] = start
            cur["end"] = ts
            blocks.append(cur)
        cur["end"] = ts
        _cu_add(cur, e)
        last_ts = ts

    active = None
    if blocks:
        b = blocks[-1]
        if now < b["start"] + CU_BLOCK_SECS:  # still inside its 5-hour window
            active = b

    # --- local day boundaries ---
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    wk_cut = midnight - 6 * 86400

    today = _cu_agg()
    week = _cu_agg()
    for e in events:
        if e[0] >= midnight:
            _cu_add(today, e)
        if e[0] >= wk_cut:
            _cu_add(week, e)

    # --- 7-day daily series (oldest -> today) ---
    days = []
    day_aggs = [_cu_agg() for _ in range(7)]
    for e in events:
        if e[0] < wk_cut:
            continue
        idx = int((e[0] - wk_cut) // 86400)
        if 0 <= idx <= 6:
            _cu_add(day_aggs[idx], e)
    for i in range(7):
        d0 = wk_cut + i * 86400
        a = day_aggs[i]
        days.append({"date": time.strftime("%Y-%m-%d", time.localtime(d0 + 3600)),
                     "total": a["total"], "in": a["in"], "out": a["out"],
                     "cr": a["cr"], "cc": a["cc"], "cost": round(a["cost"], 4),
                     "msgs": a["msgs"]})

    # --- per-model (7 days) ---
    models = {}
    for e in events:
        if e[0] < wk_cut:
            continue
        fam = e[1]
        ma = models.get(fam)
        if ma is None:
            ma = models[fam] = _cu_agg()
            ma["family"] = fam
        _cu_add(ma, e)
    model_list = []
    for ma in sorted(models.values(), key=lambda m: -m["total"]):
        row = _cu_pack(ma)
        row["family"] = ma["family"]
        model_list.append(row)

    # --- per-project ---
    projs = []
    for name, pa in proj_tok.items():
        total = pa["in"] + pa["out"]          # real work (cache-read excluded)
        projs.append({"name": _cu_proj_name(name, proj_cwd.get(name)),
                      "path": proj_cwd.get(name), "total": total,
                      "cache": pa["cr"] + pa["cc"],
                      "cost": round(pa["cost"], 4), "msgs": pa["msgs"]})
    projs.sort(key=lambda p: -p["total"])

    # --- block history (recent) ---
    bhist = []
    for b in blocks[-14:]:
        row = _cu_pack(b)
        row["start"] = b["start"]
        row["end"] = b["end"]
        row["active"] = (active is not None and b["start"] == active["start"])
        bhist.append(row)

    day_peak = max((d["total"] for d in days), default=0)
    block_peak = max((b["total"] for b in blocks), default=0)

    # --- current 5-hour window ---
    if active is not None:
        window = _cu_pack(active)
        window["active"] = True
        window["start"] = active["start"]
        window["reset_at"] = active["start"] + CU_BLOCK_SECS
        window["reset_in"] = max(0, active["start"] + CU_BLOCK_SECS - now)
    else:
        window = _cu_pack(_cu_agg())
        window["active"] = False
        window["start"] = None
        window["reset_at"] = None
        window["reset_in"] = 0

    cfg = _cu_cfg()
    return {
        "available": True,
        "generated": now,
        "block_hours": 5,
        "scan_days": CU_SCAN_DAYS,
        "window": window,
        "today": _cu_pack(today),
        "week": _cu_pack(week),
        "days": days,
        "day_peak": day_peak,
        "block_peak": block_peak,
        "models": model_list,
        "projects": projs[:12],
        "blocks": bhist,
        "sessions": len(sessions),
        "messages": len(events),
        "cap": cfg.get("cap"),
        "plan": cfg.get("plan"),
        "prices": {k: {"in": v[0], "out": v[1]} for k, v in CU_PRICES.items()},
    }


def _cu_data():
    return _cached("claude_usage", 60, _cu_build)


# --------------------------------------------------------------------------
# widget provider (compact body data) + rich expander
# --------------------------------------------------------------------------
def w_claude_usage():
    try:
        d = _cu_data()
    except Exception as e:
        return {"available": False, "reason": type(e).__name__}
    return {
        "available": d.get("available", True),
        "window": d["window"],
        "today": d["today"],
        "week_total": d["week"]["total"],
        "spark": [x["total"] for x in d["days"]],
        "day_peak": d["day_peak"],
        "block_peak": d["block_peak"],
        "cap": d.get("cap"),
        "block_hours": d["block_hours"],
        "sessions": d["sessions"],
        "messages": d["messages"],
        "generated": d["generated"],
    }


def expand_claude_usage():
    try:
        return _cu_data()
    except Exception as e:
        return {"available": False, "reason": "%s: %s" % (type(e).__name__, e)}


# --------------------------------------------------------------------------
# config route: optional soft cap / plan tier (own JSON, no server.py edit)
# --------------------------------------------------------------------------
def _cu_config_get(ctx):
    return {"ok": True, "config": _cu_cfg()}


def _cu_config_post(ctx):
    b = ctx.body or {}
    cfg = _cu_cfg()
    if "cap" in b:
        cap = b.get("cap")
        if cap in (None, "", 0, "0"):
            cfg.pop("cap", None)
        else:
            try:
                cfg["cap"] = max(0, int(cap))
            except (TypeError, ValueError):
                return ({"ok": False, "error": "bad_cap"}, 400)
    if "plan" in b:
        plan = b.get("plan")
        if plan:
            cfg["plan"] = str(plan)[:40]
        else:
            cfg.pop("plan", None)
    try:
        os.makedirs(os.path.dirname(CU_CONFIG_FILE), exist_ok=True)
        write_json(CU_CONFIG_FILE, cfg)
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)
    try:
        _widget_cache.pop("claude_usage", None)   # reflect the cap immediately
    except Exception:
        pass
    return {"ok": True, "config": cfg}


# --------------------------------------------------------------------------
# module-load side effects: register routes, catalog entry, layout injection
# --------------------------------------------------------------------------
register_get("/api/claude_usage/config", _cu_config_get)
register_post("/api/claude_usage/config", _cu_config_post)

WIDGETS["claude_usage"] = {"title": "Claude Usage", "icon": "activity",
                           "size": "card", "cat": "agent",
                           "provider": w_claude_usage}
EXPANDERS["claude_usage"] = expand_claude_usage

# Show up without a manual add: append to the layout order IF absent (never
# clobber the user's order). WIDGETS already has claude_usage above, so
# get_layout()'s catalog filter keeps it.
try:
    _cu_lay = get_layout()
    if isinstance(_cu_lay, dict):
        _cu_order = _cu_lay.get("order")
        if not isinstance(_cu_order, list):
            _cu_order = _cu_lay["order"] = []
        if "claude_usage" not in _cu_order:
            _cu_order.append("claude_usage")
            save_layout(_cu_lay)
except Exception as _cu_e:                                   # pragma: no cover
    print("[aux_claude_usage] layout inject failed: %s" % _cu_e, file=sys.stderr)
