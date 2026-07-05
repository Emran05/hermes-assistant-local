# aux_watchtower.py — Watchtower triggers + 8am World Brief (P2.1).
#
# Two linked, notify-only features in one aux module:
#   1. World Brief — an 8:00am daily push (Telegram DM + the Briefing widget),
#      composed from *cached* widget data + one local-model synthesis pass.
#   2. Watchtower — a proactive trigger engine: user-defined watch rules that,
#      when they fire, send a Telegram notification and land in the hub feed.
#      It NEVER calls a tool, NEVER emits an approval, NEVER touches
#      permissions.decide() — the notify-only boundary is enforced structurally
#      (the rule schema refuses any action/command/chat_id/target key).
#
# exec'd into server.py's globals by the aux loader (server.py:2071-2083) AFTER
# expanders_extra.py + the other aux_*.py (sorts last: "watchtower" > "trust"),
# so it may safely REBIND _generate_briefing / _briefing_payload — the existing
# briefing_loop() + /api/briefing then serve the world brief with no route edit.
#
# May use these server globals (all confirmed by grep):
#   HOME HERE DATA read_json write_json _state_lock _widget_cache _cached
#   _http_json model_online agent_paused get_settings get_tasks weather
#   macos_calendar w_crypto w_hackernews w_github EXPANDERS SYS_HISTORY
#   _sys_sample expand_battery BRIEFING_FILE _briefing_is_sane
#   _briefing_generating MODEL_URL ACTIVE_MODEL_FILE DEFAULT_MODEL HERMES
#   _hermes_env register_get register_post.
# Imports ALL its own stdlib deps (exec'd code can't rely on server.py's
# function-local imports) and defines only new names (_wt_*, WT_*, _brief_*).
#
# Design laws (CLAUDE.md): zero emoji, bespoke tone, 12-hour time, markets
# labelled "at last close" when not REGULAR, atomic 0600 writes, everything
# wrapped so a bad rule / dead feed degrades one section, never the loop.

import os
import re
import sys
import json
import time
import shutil
import hashlib
import threading
import subprocess
import urllib.request

# --------------------------------------------------------------------------
# paths / constants
# --------------------------------------------------------------------------
WT_FILE   = os.path.join(DATA, "watchtower.json")          # rules + config
WT_STATE  = os.path.join(DATA, "watchtower-state.json")    # dedupe/cooldown/cap
WT_LOG    = os.path.join(DATA, "watchtower-log.jsonl")     # append-only fire log
WT_LOG_MAX = 1024 * 1024                                    # 1 MB single-gen rotate

TELEGRAM_MAX = 4096            # hard Telegram message cap
RULES_CAP    = 40
FEED_N       = 15
RECENT_N     = 20

LIVE_TYPES = ("ticker_move", "index_move", "crypto_move",
              "system_metric", "rss_keyword")
STUB_TYPES = ("email_important", "calendar_gap", "agent_run_done")
ALL_TYPES  = LIVE_TYPES + STUB_TYPES
CHANNELS_OK = ("telegram", "hub")
METRICS_OK  = ("ram_pct", "cpu_pct", "disk_pct", "battery_pct")
DESK_SECTIONS = ("Tech", "World", "Business", "Science", "Yours")

_SYMBOL_RE = re.compile(r"^[A-Za-z.\-]{1,12}$")
_HHMM_RE   = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_SUPPRESS_OK = ("", "cooldown", "dedupe", "quiet_hours", "daily_cap",
                "disabled", "deliver_failed")

# emoji ranges scrubbed from every rendered brief/notification (belt & braces —
# the deterministic path emits none, but the model might).  Deliberately avoids
# the U+2000-206F punctuation block so bullets (•), em dashes (—), ellipsis (…)
# and · survive; °/· live below U+2000 and are untouched.
_EMOJI_RE = re.compile(
    "[" "\U0001F000-\U0001FAFF"      # pictographs, emoticons, symbols & pict.
    "\U00002600-\U000027BF"          # misc symbols + dingbats
    "\U00002300-\U000023FF"          # technical (hourglass, alarm clock…)
    "\U00002B00-\U00002BFF"          # stars / arrows-as-emoji
    "\U0001F1E6-\U0001F1FF"          # regional indicators (flags)
    "\U0000FE00-\U0000FE0F"          # variation selectors
    "]+", flags=re.UNICODE)

_wt_lock = threading.Lock()          # guards watchtower.json / -state / -log


def _wt_log_err(msg):
    try:
        print("[aux_watchtower] " + str(msg), file=sys.stderr)
    except Exception:
        pass


# --------------------------------------------------------------------------
# atomic 0600 writes (mirror permissions._atomic_write), stdlib only
# --------------------------------------------------------------------------
def _wt_atomic_write(path, raw, mode=0o600):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    tmp = "%s.tmp.%d.%s" % (path, os.getpid(), hashlib.sha256(raw).hexdigest()[:8])
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def _wt_write_json(path, obj):
    _wt_atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------
# validation / clamping helpers
# --------------------------------------------------------------------------
def _clamp_float(v, lo, hi):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                       # NaN
        return None
    return max(lo, min(hi, f))


def _clamp_int(v, lo, hi, default=None):
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _valid_hhmm(s, default):
    if isinstance(s, str) and _HHMM_RE.match(s.strip()):
        h, m = s.strip().split(":")
        return "%02d:%02d" % (int(h), int(m))
    return default


def _valid_channels(v, default):
    if not isinstance(v, list):
        return list(default)
    out = [c for c in v if c in CHANNELS_OK]
    return out or list(default)


# forbidden keys — the notify-only invariant, refused structurally at write time
_FORBIDDEN = ("action", "command", "chat_id", "target")


def _has_forbidden(d):
    if not isinstance(d, dict):
        return None
    for k in _FORBIDDEN:
        if k in d:
            return k
    return None


def _err(msg, status=400):
    return ({"ok": False, "error": msg}, status)


def _wt_validate_params(rtype, params):
    """Returns (clean_params, None) or (None, err_tuple).  Clamps in range."""
    if not isinstance(params, dict):
        params = {}
    bad = _has_forbidden(params)
    if bad:
        return None, _err("forbidden key in params: " + bad)

    if rtype in ("ticker_move", "index_move"):
        sym = str(params.get("symbol", "")).strip().upper()
        if not _SYMBOL_RE.match(sym):
            return None, _err("bad symbol")
        thr = _clamp_float(params.get("threshold_pct", 5.0), 0.1, 100.0)
        if thr is None:
            return None, _err("bad threshold_pct")
        direction = params.get("direction", "any")
        if direction not in ("any", "up", "down"):
            direction = "any"
        return {"symbol": sym, "threshold_pct": round(thr, 2),
                "direction": direction}, None

    if rtype == "crypto_move":
        coin = str(params.get("coin", "")).strip().lower()
        if not _SYMBOL_RE.match(coin.replace(" ", "")):
            return None, _err("bad coin")
        thr = _clamp_float(params.get("threshold_pct", 5.0), 0.1, 100.0)
        if thr is None:
            return None, _err("bad threshold_pct")
        direction = params.get("direction", "any")
        if direction not in ("any", "up", "down"):
            direction = "any"
        return {"coin": coin, "threshold_pct": round(thr, 2),
                "direction": direction}, None

    if rtype == "system_metric":
        metric = str(params.get("metric", "")).strip()
        if metric not in METRICS_OK:
            return None, _err("bad metric")
        op = params.get("op", ">")
        if op not in (">", "<"):
            return None, _err("bad op")
        val = _clamp_float(params.get("value", 0), 0.0, 100.0)
        if val is None:
            return None, _err("bad value")
        return {"metric": metric, "op": op, "value": round(val, 1)}, None

    if rtype == "rss_keyword":
        kws = params.get("keywords")
        if not isinstance(kws, list) or not kws:
            return None, _err("keywords required")
        clean = []
        for k in kws[:10]:
            if not isinstance(k, str):
                return None, _err("bad keyword")
            k = k.strip()[:40]
            if k:
                clean.append(k)
        if not clean:
            return None, _err("keywords required")
        secs = params.get("sections")
        if isinstance(secs, list):
            secs = [s for s in secs if s in DESK_SECTIONS]
        else:
            secs = []
        return {"keywords": clean, "sections": secs}, None

    # stub types accept an empty params object
    if rtype in STUB_TYPES:
        return {}, None

    return None, _err("unknown type: " + str(rtype))


def _wt_validate_rule(rule, existing_id=None):
    """Whitelisting validator — builds a clean rule from allowed keys only.
    Returns (clean_rule, None) or (None, err_tuple)."""
    if not isinstance(rule, dict):
        return None, _err("rule must be an object")
    bad = _has_forbidden(rule)
    if bad:
        return None, _err("forbidden key: " + bad + " (Watchtower is notify-only)")

    rtype = rule.get("type")
    if rtype not in ALL_TYPES:
        return None, _err("unknown type: " + str(rtype))

    clean_params, perr = _wt_validate_params(rtype, rule.get("params"))
    if perr:
        return None, perr

    label = str(rule.get("label", "")).strip()[:80]
    if not label:
        label = _wt_default_label(rtype, clean_params)
    cooldown = _clamp_int(rule.get("cooldown_min", 120), 5, 1440, 120)
    channels = _valid_channels(rule.get("channels"), ["telegram", "hub"])
    now = time.time()
    rid = existing_id or rule.get("id") or ("wt-%d-%s" % (
        int(now), hashlib.sha1(os.urandom(8)).hexdigest()[:4]))
    return {
        "id": rid,
        "type": rtype,
        "enabled": bool(rule.get("enabled", True)),
        "label": label,
        "params": clean_params,
        "cooldown_min": cooldown,
        "channels": channels,
        "created_at": float(rule.get("created_at", now)),
        "updated_at": now,
    }, None


def _wt_default_label(rtype, p):
    if rtype in ("ticker_move", "index_move"):
        d = {"up": "rises", "down": "falls", "any": "moves"}.get(p.get("direction"), "moves")
        return "%s %s %s%s%%" % (p.get("symbol", "?"), d, "≥", p.get("threshold_pct", 0))
    if rtype == "crypto_move":
        return "%s moves %s%s%%" % (p.get("coin", "?"), "≥", p.get("threshold_pct", 0))
    if rtype == "system_metric":
        return "%s %s %s%%" % (p.get("metric", "?"), p.get("op", ">"), p.get("value", 0))
    if rtype == "rss_keyword":
        return "News: " + ", ".join(p.get("keywords", [])[:3])
    return {"email_important": "Important email",
            "calendar_gap": "Calendar gap",
            "agent_run_done": "Agent run finished"}.get(rtype, rtype)


# --------------------------------------------------------------------------
# config + state load (defaults + clamp on read; never raise)
# --------------------------------------------------------------------------
def _wt_load():
    d = read_json(WT_FILE, None)
    if not isinstance(d, dict):
        d = {}
    d["version"] = 1
    qh = d.get("quiet_hours") if isinstance(d.get("quiet_hours"), dict) else {}
    d["quiet_hours"] = {"start": _valid_hhmm(qh.get("start"), "22:00"),
                        "end": _valid_hhmm(qh.get("end"), "07:00")}
    d["daily_cap"] = _clamp_int(d.get("daily_cap", 20), 1, 200, 20)
    br = d.get("brief") if isinstance(d.get("brief"), dict) else {}
    d["brief"] = {"enabled": bool(br.get("enabled", True)),
                  "hour": _clamp_int(br.get("hour", 8), 0, 23, 8),
                  "minute": _clamp_int(br.get("minute", 0), 0, 59, 0),
                  "channels": _valid_channels(br.get("channels"), ["telegram", "hub"])}
    rules = []
    for r in (d.get("rules") or []):
        clean, e = _wt_validate_rule(r, existing_id=(r or {}).get("id"))
        if clean:
            rules.append(clean)
    d["rules"] = rules
    return d


def _wt_state_load():
    s = read_json(WT_STATE, None)
    if not isinstance(s, dict):
        s = {}
    s["version"] = 1
    s.setdefault("last_brief_date", "")
    day = s.get("day") if isinstance(s.get("day"), dict) else {}
    s["day"] = {"date": day.get("date", ""), "sent": int(day.get("sent", 0) or 0)}
    s.setdefault("fires", {})
    if not isinstance(s["fires"], dict):
        s["fires"] = {}
    return s


def _wt_save_config(mutate):
    """Read-modify-write watchtower.json under _wt_lock.  Returns the new config."""
    with _wt_lock:
        d = _wt_load()
        mutate(d)
        # re-validate rules after mutation
        good = []
        for r in d.get("rules", []):
            clean, e = _wt_validate_rule(r, existing_id=r.get("id"))
            if clean:
                good.append(clean)
        d["rules"] = good[:RULES_CAP]
        _wt_write_json(WT_FILE, d)
        return d


def _wt_save_state(mutate):
    with _wt_lock:
        s = _wt_state_load()
        mutate(s)
        _wt_write_json(WT_STATE, s)
        return s


# --------------------------------------------------------------------------
# fire log — append + rotate + read + rewrite (for reactions)
# --------------------------------------------------------------------------
def _wt_log_append(row):
    try:
        with _wt_lock:
            try:
                if os.path.getsize(WT_LOG) > WT_LOG_MAX:
                    os.replace(WT_LOG, WT_LOG + ".1")
            except OSError:
                pass
            line = json.dumps(row, ensure_ascii=False) + "\n"
            with open(WT_LOG, "a", encoding="utf-8") as f:
                f.write(line)
            try:
                os.chmod(WT_LOG, 0o600)
            except OSError:
                pass
    except Exception as e:
        _wt_log_err("log append failed: %r" % e)


def _wt_log_read(limit=200):
    rows = []
    try:
        with open(WT_LOG, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return rows
    for ln in lines[-limit:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
            if isinstance(o, dict):
                rows.append(o)
        except Exception:
            pass
    return rows


def _wt_log_rewrite(patch_fn):
    """Read all rows, apply patch_fn(row)->bool (True if changed), rewrite
    atomically.  Returns number of rows changed."""
    with _wt_lock:
        rows = []
        try:
            with open(WT_LOG, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        pass
        except OSError:
            return 0
        changed = 0
        for r in rows:
            try:
                if patch_fn(r):
                    changed += 1
            except Exception:
                pass
        if changed:
            body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            _wt_atomic_write(WT_LOG, body)
        return changed


# --------------------------------------------------------------------------
# time helpers (12-hour, local)
# --------------------------------------------------------------------------
def _t12(ts):
    """epoch -> '3:42 PM' (local); '' for falsy."""
    try:
        n = float(ts)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    lt = time.localtime(n)
    h = lt.tm_hour % 12 or 12
    ap = "AM" if lt.tm_hour < 12 else "PM"
    return "%d:%02d %s" % (h, lt.tm_min, ap)


def _clock_to12(s):
    """Normalise a clock string ('09:00' / '9:00 AM') to 12-hour."""
    if not isinstance(s, str):
        return ""
    s = s.strip()
    if re.search(r"[AaPp][Mm]", s):
        return s.upper().replace("AM", "AM").replace("PM", "PM")
    m = re.match(r"^(\d{1,2}):(\d{2})", s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        ap = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return "%d:%02d %s" % (h12, mn, ap)
    return s


def _fmt_date(ts=None):
    lt = time.localtime(ts) if ts else time.localtime()
    return time.strftime("%b ", lt) + str(lt.tm_mday)


def _long_date():
    lt = time.localtime()
    return time.strftime("%A, %B ", lt) + str(lt.tm_mday)


def _greeting():
    h = time.localtime().tm_hour
    if h < 12:
        return "good morning"
    if h < 18:
        return "good afternoon"
    return "good evening"


def _strip_emoji(text):
    if not isinstance(text, str):
        text = str(text)
    return _EMOJI_RE.sub("", text)


def _fmt_pct(p):
    try:
        v = float(p)
    except (TypeError, ValueError):
        return "?"
    return ("+" if v >= 0 else "") + ("%.1f%%" % v)


def _fmt_price(p):
    try:
        v = float(p)
    except (TypeError, ValueError):
        return ""
    if v >= 1000:
        return "$" + format(int(round(v)), ",")
    if v >= 1:
        return "$%.2f" % v
    return "$%.4f" % v


# --------------------------------------------------------------------------
# cached-provider access (ride hub_prewarm_loop; zero extra network in-loop)
# --------------------------------------------------------------------------
def _provider(name):
    try:
        fn = EXPANDERS.get(name)
    except Exception:
        fn = None
    return fn


def _safe_call(fn, default=None):
    try:
        v = fn()
        return v if v is not None else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def _markets_data():
    fn = _provider("markets")
    if fn:
        return _safe_call(fn, {})
    return {}


def _rss_data():
    fn = _provider("rss")
    if fn:
        return _safe_call(fn, {})
    return {}


def _sys_now():
    """Latest system sample — ride SYS_HISTORY (populated every 5s), else sample."""
    try:
        if SYS_HISTORY:
            last = SYS_HISTORY[-1]
            if isinstance(last, dict):
                return last
    except Exception:
        pass
    return _safe_call(_sys_sample, {})


def _disk_pct():
    try:
        u = shutil.disk_usage("/")
        return round(100.0 * u.used / u.total, 1) if u.total else None
    except Exception:
        return None


def _wt_quote(symbol):
    """Cached single-symbol quote for a rule referencing an off-watchlist ticker.
    Rides _cached (5-min TTL) so the loop stays near-zero-network."""
    sym = symbol.upper()

    def fetch():
        j = _http_json("https://query1.finance.yahoo.com/v8/finance/chart/"
                       + urllib.parse.quote(sym) + "?range=1d&interval=15m", timeout=7)
        res = j["chart"]["result"][0]
        m = res["meta"]
        price = m.get("regularMarketPrice")
        prev = m.get("chartPreviousClose") or m.get("previousClose") or price
        chg = (price - prev) if (price is not None and prev) else 0
        pct = (chg / prev * 100) if prev else 0
        return {"symbol": sym, "price": price, "pct": round(pct, 2),
                "state": m.get("marketState"), "asof": m.get("regularMarketTime"),
                "name": m.get("shortName") or sym}
    try:
        return _cached("wt_q:" + sym, 300, fetch)
    except Exception:
        return None


# import urllib.parse lazily via the server global namespace (already imported
# there); fall back to our own if somehow absent.
try:
    urllib.parse
except AttributeError:            # pragma: no cover
    import urllib.parse           # noqa


# --------------------------------------------------------------------------
# EVALUATORS — each returns (fire: bool, signature: str, context: dict).
# Never raise (callers wrap too).  No-data => no fire (data absence != fire).
# --------------------------------------------------------------------------
def _dir_ok(pct, direction):
    if direction == "up":
        return pct > 0
    if direction == "down":
        return pct < 0
    return True


def _lookup_symbol(symbol, scope="all"):
    """Find a symbol in the markets cache. scope: 'all'|'indices'|'watchlist'."""
    mk = _markets_data()
    pools = []
    if scope in ("all", "indices"):
        pools.append(mk.get("indices") or [])
    if scope in ("all", "watchlist"):
        pools.append(mk.get("watchlist") or [])
    for pool in pools:
        for item in pool:
            if isinstance(item, dict) and not item.get("error") \
                    and str(item.get("symbol", "")).upper() == symbol:
                return item, mk
    return None, mk


def _eval_ticker(params, allow_fetch=True, scope="all"):
    sym = str(params.get("symbol", "")).upper()
    thr = float(params.get("threshold_pct", 5.0))
    direction = params.get("direction", "any")
    item, mk = _lookup_symbol(sym, scope=scope)
    if not item and allow_fetch and scope != "indices":
        item = _wt_quote(sym)          # off-watchlist single quote (cached)
    if not item or item.get("pct") is None:
        return False, "", {"symbol": sym, "error": "no quote"}
    pct = float(item.get("pct") or 0.0)
    price = item.get("price")
    state = item.get("state") or (mk.get("state") if isinstance(mk, dict) else None)
    fire = abs(pct) >= thr and _dir_ok(pct, direction)
    bucket = int(round(pct))           # 1% dedupe bucket
    d = "up" if pct > 0 else ("down" if pct < 0 else "flat")
    sig = "%s:%d:%s" % (sym, bucket, d)
    ctx = {"symbol": sym, "pct": round(pct, 2), "price": price,
           "threshold_pct": thr, "state": state, "asof": item.get("asof")}
    return fire, sig, ctx


def _eval_index(params):
    return _eval_ticker(params, allow_fetch=False, scope="indices")


def _eval_crypto(params):
    coin = str(params.get("coin", "")).lower()
    thr = float(params.get("threshold_pct", 5.0))
    direction = params.get("direction", "any")
    data = _safe_call(w_crypto, {})
    match = None
    for c in (data.get("coins") or []):
        if isinstance(c, dict) and str(c.get("id", "")).lower() == coin:
            match = c
            break
    if not match or match.get("pct") is None:
        return False, "", {"coin": coin, "error": "no quote"}
    pct = float(match.get("pct") or 0.0)
    price = match.get("price")
    fire = abs(pct) >= thr and _dir_ok(pct, direction)
    bucket = int(round(pct))
    d = "up" if pct > 0 else ("down" if pct < 0 else "flat")
    sig = "%s:%d:%s" % (coin, bucket, d)
    ctx = {"coin": coin, "pct": round(pct, 2), "price": price, "threshold_pct": thr}
    return fire, sig, ctx


def _metric_value(metric):
    if metric == "disk_pct":
        return _disk_pct()
    if metric == "battery_pct":
        bat = _safe_call(expand_battery, {})
        return bat.get("pct")
    samp = _sys_now()
    return samp.get(metric)            # cpu_pct / ram_pct


def _eval_system(params):
    metric = params.get("metric")
    op = params.get("op", ">")
    thr = float(params.get("value", 0))
    cur = _metric_value(metric)
    if cur is None:
        return False, "", {"metric": metric, "error": "no reading"}
    cur = float(cur)
    fire = (cur > thr) if op == ">" else (cur < thr)
    bucket = int(round(cur / 5.0) * 5)   # 5% dedupe bucket
    sig = "%s:%s:%g:%d" % (metric, op, thr, bucket)
    ctx = {"metric": metric, "op": op, "threshold": thr, "value": round(cur, 1)}
    return fire, sig, ctx


def _eval_rss(params):
    kws = [k.lower() for k in (params.get("keywords") or [])]
    want = set(params.get("sections") or [])
    data = _rss_data()
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        return False, "", {"error": "no headlines"}
    best = None                        # (ts, keyword, item, section)
    for sec in sections:
        name = sec.get("name", "")
        if want and name not in want:
            continue
        for it in (sec.get("items") or []):
            hay = (str(it.get("title", "")) + " " + str(it.get("summary", ""))).lower()
            for kw in kws:
                if kw and kw in hay:
                    ts = it.get("ts") or 0
                    if best is None or (ts or 0) > (best[0] or 0):
                        best = (ts, kw, it, name)
                    break
    if not best:
        return False, "", {"keywords": kws, "note": "no match"}
    ts, kw, it, name = best
    url = str(it.get("url", ""))
    sig = "%s:%s" % (kw, hashlib.sha1((url or it.get("title", "")).encode("utf-8")).hexdigest()[:10])
    ctx = {"keyword": kw, "title": it.get("title", ""), "url": url,
           "source": it.get("source", ""), "section": name, "ts": ts}
    return True, sig, ctx


def _eval_stub(rtype):
    reason = {"email_important": "gmail not connected",
              "calendar_gap": "calendar signal not wired",
              "agent_run_done": "agent-run signal not wired"}.get(rtype, "not available")
    return False, "", {"stub": True, "reason": reason}


def _evaluate(rule):
    """Dispatch to the right evaluator; never raise."""
    rtype = rule.get("type")
    params = rule.get("params") or {}
    try:
        if rtype == "ticker_move":
            return _eval_ticker(params)
        if rtype == "index_move":
            return _eval_index(params)
        if rtype == "crypto_move":
            return _eval_crypto(params)
        if rtype == "system_metric":
            return _eval_system(params)
        if rtype == "rss_keyword":
            return _eval_rss(params)
        if rtype in STUB_TYPES:
            return _eval_stub(rtype)
    except Exception as e:
        return False, "", {"error": type(e).__name__ + ": " + str(e)}
    return False, "", {"error": "unknown type"}


# --------------------------------------------------------------------------
# notification text (12-hour, zero emoji)
# --------------------------------------------------------------------------
def _mkt_state_tag(ctx):
    state = ctx.get("state")
    if state and state != "REGULAR":
        return " (at last close · %s)" % _fmt_date(ctx.get("asof"))
    return ""


def _wt_notif_text(rule, ctx):
    label = rule.get("label", "Watch")
    rtype = rule.get("type")
    head = "Watchtower · " + label
    if rtype in ("ticker_move", "index_move"):
        body = "%s %s at %s%s" % (ctx.get("symbol", "?"), _fmt_pct(ctx.get("pct")),
                                  _fmt_price(ctx.get("price")), _mkt_state_tag(ctx))
    elif rtype == "crypto_move":
        body = "%s %s at %s" % (ctx.get("coin", "?").title(),
                                _fmt_pct(ctx.get("pct")), _fmt_price(ctx.get("price")))
    elif rtype == "system_metric":
        body = "%s is %.1f%% (%s %s%%)" % (ctx.get("metric", "?"),
                                           ctx.get("value", 0), ctx.get("op", ">"),
                                           ctx.get("threshold", 0))
    elif rtype == "rss_keyword":
        body = "“%s” matched %s — %s (%s)" % (
            ctx.get("keyword", ""), ctx.get("title", ""),
            ctx.get("source", ""), ctx.get("section", ""))
    else:
        body = ctx.get("reason", "")
    return _strip_emoji(head + "\n" + body)


# --------------------------------------------------------------------------
# gates — quiet-hours / cooldown / dedupe / daily-cap
# --------------------------------------------------------------------------
def _in_quiet(now_ts, qh):
    lt = time.localtime(now_ts)
    cur = lt.tm_hour * 60 + lt.tm_min
    try:
        sh, sm = (int(x) for x in qh.get("start", "22:00").split(":"))
        eh, em = (int(x) for x in qh.get("end", "07:00").split(":"))
    except Exception:
        return False
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end   # overnight wrap


def _today_str(ts=None):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _wt_gate(rule, signature, now_ts, cfg, state):
    """Returns a suppression reason ('' = pass).  Pure — no side effects."""
    if not rule.get("enabled", True):
        return "disabled"
    if _in_quiet(now_ts, cfg.get("quiet_hours", {})):
        return "quiet_hours"
    fires = state.get("fires", {})
    fs = fires.get(rule["id"]) or {}
    last = float(fs.get("last_fired", 0) or 0)
    cd = int(rule.get("cooldown_min", 120)) * 60
    if last and (now_ts - last) < cd:
        return "cooldown"
    if signature and signature == fs.get("last_signature"):
        return "dedupe"
    day = state.get("day", {})
    cap = int(cfg.get("daily_cap", 20))
    if day.get("date") == _today_str(now_ts) and int(day.get("sent", 0)) >= cap:
        return "daily_cap"
    return ""


# --------------------------------------------------------------------------
# delivery — Telegram home channel (no chat_id => cannot be redirected)
# --------------------------------------------------------------------------
def _wt_send_telegram(text):
    """Returns (ok, detail).  Reuses server's HERMES + _hermes_env(); trims to
    the Telegram limit.  NEVER passes a chat_id (locked to the home channel)."""
    text = _strip_emoji(text)
    if len(text) > TELEGRAM_MAX:
        text = text[:TELEGRAM_MAX - 1].rstrip() + "…"
    try:
        p = subprocess.run([HERMES, "send", "--to", "telegram", "--quiet", text],
                           capture_output=True, text=True, timeout=20,
                           env=_hermes_env())
    except Exception as e:
        return False, type(e).__name__ + ": " + str(e)
    if p.returncode != 0:
        return False, ((p.stderr or p.stdout or "").strip()[:200] or
                       "exit %d" % p.returncode)
    return True, ""


def _deliver(channels, text):
    """Deliver a notification to the requested channels.
    Returns (delivered_list, fail_detail).  'hub' is delivered by the log row."""
    delivered, detail = [], ""
    if "telegram" in channels:
        ok, det = _wt_send_telegram(text)
        if ok:
            delivered.append("telegram")
        else:
            detail = det
    if "hub" in channels:
        delivered.append("hub")        # the log row IS the hub delivery
    return delivered, detail


# --------------------------------------------------------------------------
# rule fire pipeline (loop uses this; test_rule never calls it)
# --------------------------------------------------------------------------
def _fire_rule(rule, signature, ctx, cfg, state, now_ts):
    """Gate -> deliver -> log -> update fire state.  Returns the log row."""
    reason = _wt_gate(rule, signature, now_ts, cfg, state)
    row = {"ts": now_ts, "rule_id": rule["id"], "type": rule["type"],
           "label": rule.get("label", ""), "signature": signature,
           "context": ctx, "channels": rule.get("channels", []),
           "delivered": [], "suppressed": reason, "reaction": ""}
    if reason:
        _wt_log_append(row)
        return row

    text = _wt_notif_text(rule, ctx)
    delivered, detail = _deliver(rule.get("channels", []), text)
    telegram_wanted = "telegram" in rule.get("channels", [])
    if telegram_wanted and "telegram" not in delivered:
        row["suppressed"] = "deliver_failed"
        row["detail"] = detail
        _wt_log_append(row)
        return row                     # retried next pass (bounded by cooldown)

    row["delivered"] = delivered
    row["text"] = text
    _wt_log_append(row)

    def _mut(s):
        today = _today_str(now_ts)
        if s.get("day", {}).get("date") != today:
            s["day"] = {"date": today, "sent": 0}
        s["day"]["sent"] = int(s["day"].get("sent", 0)) + 1
        fs = s.setdefault("fires", {}).setdefault(rule["id"], {})
        fs["last_fired"] = now_ts
        fs["last_signature"] = signature
        fs["count"] = int(fs.get("count", 0)) + 1
    _wt_save_state(_mut)
    # keep the in-memory copy coherent for the rest of this pass
    d = state.setdefault("day", {})
    if d.get("date") != _today_str(now_ts):
        state["day"] = {"date": _today_str(now_ts), "sent": 0}
    state["day"]["sent"] = int(state["day"].get("sent", 0)) + 1
    fs = state.setdefault("fires", {}).setdefault(rule["id"], {})
    fs["last_fired"] = now_ts
    fs["last_signature"] = signature
    fs["count"] = int(fs.get("count", 0)) + 1
    return row


# ==========================================================================
# WORLD BRIEF — deterministic 5-section builder + one synthesis pass
# ==========================================================================
def _sec(lines=None, note="", meta=None):
    s = {"lines": lines or [], "note": note}
    if meta:
        s["meta"] = meta
    return s


def _brief_overnight_flags():
    """Fires since ~last night (delivered or quiet-hours-suppressed) — the tie
    between Watchtower and the brief's 'your day' section."""
    cutoff = time.time() - 14 * 3600
    out = []
    for r in _wt_log_read(300):
        if (r.get("ts") or 0) < cutoff:
            continue
        if r.get("suppressed") not in ("", "quiet_hours"):
            continue
        ctx = r.get("context") or {}
        piece = r.get("label", "")
        if "pct" in ctx:
            piece = "%s %s" % (ctx.get("symbol") or str(ctx.get("coin", "")).title()
                               or r.get("label", ""), _fmt_pct(ctx.get("pct")))
        out.append("%s — %s" % (piece, _t12(r.get("ts"))))
    return out[-5:]


def _brief_day():
    lines = []
    lines.append("%s — %s." % (_long_date(), _greeting()))
    cal = _safe_call(macos_calendar, {})
    if not cal.get("available"):
        lines.append("Calendar not connected — " + str(cal.get("reason", "no calendar"))[:80])
    else:
        evs = cal.get("events") or []
        if evs:
            for e in evs[:6]:
                tm = _clock_to12(e.get("time", ""))
                lines.append("• %s%s" % ((tm + " — ") if tm else "", e.get("title", "")))
        else:
            lines.append("No events on the calendar today.")
    tasks = [t for t in _safe_call(get_tasks, {"tasks": []}).get("tasks", [])
             if not t.get("done")]
    if tasks:
        lines.append("Open tasks: " + "; ".join(t.get("text", "") for t in tasks[:5]))
    flags = _brief_overnight_flags()
    if flags:
        lines.append("Overnight flags: " + "; ".join(flags))
    return _sec(lines)


def _brief_world():
    data = _rss_data()
    secs = data.get("sections")
    if not isinstance(secs, list) or not secs:
        return _sec(note="No fresh headlines right now.")
    lines = []
    want = ("Tech", "World", "Business", "Science")
    order = [s for s in secs if s.get("name") in want] + \
            [s for s in secs if s.get("name") not in want]
    for sec in order:
        items = sec.get("items") or []
        if not items:
            continue
        lines.append(sec.get("name", "") + ":")
        for it in items[:3]:
            src = it.get("source", "")
            lines.append("• %s%s" % (it.get("title", ""),
                                          (" (" + src + ")") if src else ""))
    return _sec(lines) if lines else _sec(note="No fresh headlines right now.")


def _brief_markets():
    mk = _markets_data()
    if mk.get("error") or not (mk.get("indices") or mk.get("watchlist")):
        return _sec(note="Markets data unavailable.")
    state = mk.get("state")
    asof = mk.get("asof")
    if state == "REGULAR":
        tag = "live"
    elif state in ("PRE", "PREPRE"):
        tag = "pre-market"
    elif state in ("POST", "POSTPOST"):
        tag = "after hours"
    else:
        tag = "at last close · " + _fmt_date(asof)
    lines = ["Markets — " + tag]
    idx = [q for q in (mk.get("indices") or []) if not q.get("error")]
    if idx:
        parts = []
        for q in idx:
            nm = q.get("friendly") or q.get("name") or q.get("symbol")
            parts.append("%s %s" % (nm, _fmt_pct(q.get("pct"))))
        lines.append("Indices: " + ", ".join(parts))
    wl = [q for q in (mk.get("watchlist") or []) if not q.get("error")
          and q.get("pct") is not None]
    wl.sort(key=lambda q: abs(float(q.get("pct") or 0)), reverse=True)
    movers = [q for q in wl if abs(float(q.get("pct") or 0)) >= 0.4][:6] or wl[:4]
    if movers:
        lines.append("Movers:")
        for q in movers:
            lines.append("• %s %s · %s" % (
                q.get("symbol"), _fmt_pct(q.get("pct")), _fmt_price(q.get("price"))))
    return _sec(lines, meta={"state": state, "asof": asof})


def _brief_underground():
    lines = []
    gh = _safe_call(w_github, {})
    repos = gh.get("repos") or []
    if repos:
        lines.append("GitHub trending:")
        for r in repos[:3]:
            lang = r.get("lang") or ""
            stars = r.get("stars")
            meta = " · ".join([x for x in [lang, ("★%s" % stars) if stars else ""] if x])
            desc = (r.get("desc") or "").strip()
            lines.append("• %s%s%s" % (r.get("name", ""),
                                            (" — " + desc) if desc else "",
                                            (" (" + meta + ")") if meta else ""))
    hn = _safe_call(w_hackernews, {})
    stories = [s for s in (hn.get("stories") or []) if s.get("title")]
    stories.sort(key=lambda s: int(s.get("score") or 0), reverse=True)
    if stories:
        lines.append("Hacker News risers:")
        for s in stories[:3]:
            lines.append("• %s (%s pts)" % (s.get("title", ""), s.get("score", 0)))
    if not lines:
        return _sec(note="Nothing surfacing from the underground feeds yet.")
    return _sec(lines)


def _brief_lookahead():
    lines = []
    w = _safe_call(weather, {})
    if w.get("configured") and not w.get("error"):
        lines.append("Weather in %s: %s°, %s (hi %s / lo %s)." % (
            w.get("city", ""), w.get("temp", "?"), w.get("desc", ""),
            w.get("hi", "?"), w.get("lo", "?")))
    cal = _safe_call(macos_calendar, {})
    if cal.get("available") and cal.get("events"):
        now12 = time.localtime()
        cur_min = now12.tm_hour * 60 + now12.tm_min
        later = []
        for e in cal.get("events", []):
            m = re.match(r"^(\d{1,2}):(\d{2})", str(e.get("time", "")))
            if m and (int(m.group(1)) * 60 + int(m.group(2))) >= cur_min:
                later.append("• %s — %s" % (_clock_to12(e.get("time", "")),
                                                       e.get("title", "")))
        if later:
            lines.append("Later today:")
            lines.extend(later[:4])
    mk = _markets_data()
    wl = [q for q in (mk.get("watchlist") or []) if not q.get("error")
          and q.get("pct") is not None]
    if wl:
        top = max(wl, key=lambda q: abs(float(q.get("pct") or 0)))
        if abs(float(top.get("pct") or 0)) >= 1.0:
            lines.append("Watch %s (%s) at the open." % (
                top.get("symbol"), _fmt_pct(top.get("pct"))))
    if not lines:
        return _sec(note="Nothing flagged further out.")
    return _sec(lines)


_BRIEF_HEADERS = [("day", "Your day"),
                  ("world", "World & tech front page"),
                  ("markets", "Market movers"),
                  ("underground", "Underground signal"),
                  ("lookahead", "Look-ahead")]


def _brief_build_sections():
    """Deterministic structured brief.  Returns (sections, degraded)."""
    builders = {"day": _brief_day, "world": _brief_world, "markets": _brief_markets,
                "underground": _brief_underground, "lookahead": _brief_lookahead}
    sections, degraded = {}, []
    for key, _title in _BRIEF_HEADERS:
        try:
            sec = builders[key]()
        except Exception as e:
            sec = _sec(note="section unavailable (" + type(e).__name__ + ")")
        if not sec.get("lines") and sec.get("note"):
            degraded.append(key)
        sections[key] = sec
    return sections, degraded


def _brief_render_text(sections):
    """Deterministic markdown render (always works)."""
    out = []
    for key, title in _BRIEF_HEADERS:
        sec = sections.get(key, {})
        out.append("## " + title)
        lines = sec.get("lines") or []
        if lines:
            out.append("\n".join(lines))
        else:
            out.append(sec.get("note") or "nothing yet")
        out.append("")
    return _strip_emoji("\n".join(out).strip() + "\n")


# ---- synthesis pass — ONE tool-free chat completion, sanity-gated -----------
def _model_chat_url():
    base = MODEL_URL
    if base.endswith("/v1/models"):
        return base[:-len("/models")] + "/chat/completions"
    return re.sub(r"/v1/models/?$", "/v1/chat/completions", base) \
        if "/v1/models" in base else "http://127.0.0.1:8080/v1/chat/completions"


def _active_model():
    try:
        with open(ACTIVE_MODEL_FILE) as f:
            m = f.read().strip()
            if m:
                return m
    except OSError:
        pass
    return DEFAULT_MODEL


_SYNTH_SYSTEM = (
    "You are the editor of a personal 8am World Brief. You are given a factual, "
    "already-correct draft assembled from cached data. Rewrite it into a crisp, "
    "scannable brief a busy person reads in under 60 seconds.\n"
    "RULES: Keep EXACTLY these five section headers as markdown '##' lines and in "
    "this order: 'Your day', 'World & tech front page', 'Market movers', "
    "'Underground signal', 'Look-ahead'. Use 12-hour clock times. NO emoji of any "
    "kind. Do NOT invent events, numbers, headlines, or prices — use only what the "
    "draft contains. For a market mover you MAY add a short, widely-known one-line "
    "'why' ONLY if you are confident; otherwise leave it. Bespoke, direct tone; no "
    "preamble, no sign-off, no role tags. Output ONLY the brief markdown."
)


def _brief_synthesize(det_text):
    """Returns polished text (sane) or None.  Never raises."""
    if not model_online() or agent_paused():
        return None
    try:
        payload = json.dumps({
            "model": _active_model(),
            "messages": [{"role": "system", "content": _SYNTH_SYSTEM},
                         {"role": "user", "content":
                          "Here is today's draft brief. Rewrite per the rules.\n\n"
                          + det_text}],
            "temperature": 0.4, "max_tokens": 1100, "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(_model_chat_url(), data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
        text = (((resp.get("choices") or [{}])[0].get("message") or {})
                .get("content") or "").strip()
    except Exception as e:
        _wt_log_err("synthesis failed: %r" % e)
        return None
    text = _strip_emoji(text)
    if not _brief_is_sane(text):
        return None
    return text.rstrip() + "\n"


def _brief_is_sane(text):
    """Reuse the server's briefing sanity gate, plus require our headers."""
    if not text or len(text) < 120:
        return False
    try:
        if not _briefing_is_sane(text):
            return False
    except Exception:
        if "##" not in text:
            return False
    low = text.lower()
    hits = sum(1 for _k, title in _BRIEF_HEADERS if title.lower() in low)
    return hits >= 3


def _brief_compose(run_synthesis):
    """Full compose. Returns dict with text/synthesized/sections/degraded/meta."""
    sections, degraded = _brief_build_sections()
    det_text = _brief_render_text(sections)
    text, synthesized = det_text, False
    if run_synthesis:
        prose = _brief_synthesize(det_text)
        if prose:
            text, synthesized = prose, True
    mk_meta = (sections.get("markets", {}).get("meta") or {})
    return {"text": _strip_emoji(text), "synthesized": synthesized,
            "sections": sections, "degraded": degraded,
            "asof": mk_meta.get("asof"), "markets_state": mk_meta.get("state")}


# --------------------------------------------------------------------------
# REBIND — the widget refresh path writes the world brief (no Telegram send).
# briefing_loop() + /api/briefing + /api/briefing/refresh call these globals by
# name at call time, so assignment here makes them serve the world brief.
# --------------------------------------------------------------------------
def _wt_generate_briefing():
    global _briefing_generating
    with _state_lock:
        if _briefing_generating:
            return
        _briefing_generating = True
    try:
        comp = _brief_compose(run_synthesis=True)
        payload = {"ok": True, "reply": comp["text"], "generated_at": time.time(),
                   "kind": "world_brief", "synthesized": comp["synthesized"],
                   "sections": comp["sections"]}
        with _state_lock:
            write_json(BRIEFING_FILE, payload)
    except Exception as e:
        _wt_log_err("generate_briefing failed: %r" % e)
    finally:
        with _state_lock:
            _briefing_generating = False


def _wt_briefing_payload():
    with _state_lock:
        b = read_json(BRIEFING_FILE, {})
    return {"reply": b.get("reply", ""), "generated_at": b.get("generated_at"),
            "generating": _briefing_generating}


def _wt_write_widget(comp):
    """Write BRIEFING_FILE from an already-composed brief (used by send/scheduler)."""
    payload = {"ok": True, "reply": comp["text"], "generated_at": time.time(),
               "kind": "world_brief", "synthesized": comp["synthesized"],
               "sections": comp["sections"]}
    with _state_lock:
        write_json(BRIEFING_FILE, payload)


# --------------------------------------------------------------------------
# background thread — brief tick (8am date-guard + catch-up) + rule pass
# --------------------------------------------------------------------------
def _prewarm():
    for fn in (_provider("rss"), _provider("markets"), w_github, w_hackernews, w_crypto):
        if fn:
            try:
                fn()
            except Exception:
                pass


def _brief_tick(cfg, state, now_ts):
    br = cfg.get("brief", {})
    if not br.get("enabled", True):
        return
    lt = time.localtime(now_ts)
    today = _today_str(now_ts)
    at_or_past = (lt.tm_hour > br.get("hour", 8) or
                  (lt.tm_hour == br.get("hour", 8) and lt.tm_min >= br.get("minute", 0)))
    if not at_or_past or state.get("last_brief_date") == today:
        return
    _prewarm()
    comp = _brief_compose(run_synthesis=True)
    channels = br.get("channels", ["telegram", "hub"])
    if "telegram" in channels:
        ok, det = _wt_send_telegram(comp["text"])
        if not ok:
            _wt_log_err("8am brief telegram send failed: %s" % det)
    try:
        _wt_write_widget(comp)
    except Exception as e:
        _wt_log_err("8am brief widget write failed: %r" % e)
    # flip the guard once the day's brief is composed + the widget is written,
    # so a flaky Telegram send can't cause a re-compose storm (>=1 brief/day).
    _wt_save_state(lambda s: s.__setitem__("last_brief_date", today))
    state["last_brief_date"] = today


def _rule_pass(cfg, state, now_ts):
    for rule in cfg.get("rules", []):
        if not rule.get("enabled", True):
            continue
        try:
            fire, sig, ctx = _evaluate(rule)
            if not fire:
                continue
            _fire_rule(rule, sig, ctx, cfg, state, now_ts)
        except Exception as e:
            _wt_log_err("rule %s failed: %r" % (rule.get("id"), e))


def watchtower_loop():
    _wt_log_err("loop started")
    time.sleep(8)                      # let the hub prewarm the first caches
    while True:
        try:
            cfg = _wt_load()
            state = _wt_state_load()
            now_ts = time.time()
            _brief_tick(cfg, state, now_ts)
            _rule_pass(cfg, state, now_ts)
        except Exception as e:
            _wt_log_err("loop: %r" % e)
        time.sleep(60)


# ==========================================================================
# ENDPOINTS
# ==========================================================================
def _brief_sections_public(sections):
    return {k: {"lines": v.get("lines", []), "note": v.get("note", "")}
            for k, v in sections.items()}


def brief_preview_handler(ctx):
    try:
        comp = _brief_compose(run_synthesis=False)
        return {"ok": True, "sections": _brief_sections_public(comp["sections"]),
                "asof": comp["asof"], "markets_state": comp["markets_state"],
                "synthesized": False, "degraded": comp["degraded"]}
    except Exception as e:
        return {"ok": True, "sections": {}, "degraded": list(dict(_BRIEF_HEADERS).keys()),
                "error": type(e).__name__ + ": " + str(e)}


def brief_send_handler(ctx):
    b = ctx.body or {}
    dry_run = bool(b.get("dry_run", True))
    try:
        comp = _brief_compose(run_synthesis=True)
    except Exception as e:
        return _err("compose_failed: " + str(e), 500)
    if dry_run:
        return {"ok": True, "text": comp["text"], "synthesized": comp["synthesized"],
                "delivered": [], "dry_run": True, "degraded": comp["degraded"]}
    cfg = _wt_load()
    channels = cfg.get("brief", {}).get("channels", ["telegram", "hub"])
    delivered, detail = [], ""
    if "telegram" in channels:
        ok, detail = _wt_send_telegram(comp["text"])
        if ok:
            delivered.append("telegram")
    if "hub" in channels:
        delivered.append("hub")
    try:
        _wt_write_widget(comp)         # always write the widget, even on send fail
    except Exception as e:
        _wt_log_err("send widget write failed: %r" % e)
    if "telegram" in channels and "telegram" not in delivered:
        return ({"ok": False, "error": "send_failed", "detail": detail,
                 "text": comp["text"], "synthesized": comp["synthesized"],
                 "delivered": delivered, "dry_run": False}, 502)
    return {"ok": True, "text": comp["text"], "synthesized": comp["synthesized"],
            "delivered": delivered, "dry_run": False}


def _rule_stats():
    """Per-rule fired/useful/noise/precision/last_fired from the fire log."""
    stats = {}
    for r in _wt_log_read(500):
        rid = r.get("rule_id")
        if not rid:
            continue
        st = stats.setdefault(rid, {"fired": 0, "useful": 0, "noise": 0,
                                    "suppressed": 0, "last_fired": 0})
        if r.get("suppressed"):
            st["suppressed"] += 1
        else:
            st["fired"] += 1
            st["last_fired"] = max(st["last_fired"], r.get("ts", 0) or 0)
        if r.get("reaction") == "useful":
            st["useful"] += 1
        elif r.get("reaction") == "noise":
            st["noise"] += 1
    for rid, st in stats.items():
        st["precision"] = (round(st["useful"] / st["fired"], 3)
                           if st["fired"] else None)
    return stats


def watchtower_get_handler(ctx):
    try:
        cfg = _wt_load()
        recent = list(reversed(_wt_log_read(RECENT_N)))
        return {"ok": True, "quiet_hours": cfg["quiet_hours"],
                "daily_cap": cfg["daily_cap"], "brief": cfg["brief"],
                "rules": cfg["rules"], "stats": _rule_stats(),
                "recent": recent, "live_types": list(LIVE_TYPES),
                "stub_types": list(STUB_TYPES)}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__ + ": " + str(e),
                "rules": [], "stats": {}, "recent": []}


def _op_add_rule(b):
    clean, e = _wt_validate_rule(b.get("rule"))
    if e:
        return e
    added = {}

    def _mut(d):
        rules = d.setdefault("rules", [])
        if len(rules) >= RULES_CAP:
            raise _WTFull()
        rules.append(clean)
        added.update(clean)
    try:
        _wt_save_config(_mut)
    except _WTFull:
        return _err("too many rules (max %d)" % RULES_CAP)
    return {"ok": True, "rule": added}


def _op_update_rule(b):
    rid = b.get("id")
    patch = b.get("patch") or {}
    bad = _has_forbidden(patch)
    if bad:
        return _err("forbidden key: " + bad)
    found = {"hit": False, "rule": None}

    def _mut(d):
        for i, r in enumerate(d.get("rules", [])):
            if r.get("id") == rid:
                merged = dict(r)
                for k in ("label", "enabled", "cooldown_min", "channels", "params"):
                    if k in patch:
                        merged[k] = patch[k]
                clean, e = _wt_validate_rule(merged, existing_id=rid)
                if clean:
                    clean["created_at"] = r.get("created_at", clean["created_at"])
                    d["rules"][i] = clean
                    found["hit"] = True
                    found["rule"] = clean
                return
    _wt_save_config(_mut)
    if not found["hit"]:
        return _err("rule not found", 404)
    return {"ok": True, "rule": found["rule"]}


def _op_toggle_rule(b):
    rid = b.get("id")
    enabled = bool(b.get("enabled", True))
    hit = {"v": False}

    def _mut(d):
        for r in d.get("rules", []):
            if r.get("id") == rid:
                r["enabled"] = enabled
                hit["v"] = True
    _wt_save_config(_mut)
    return {"ok": True} if hit["v"] else _err("rule not found", 404)


def _op_delete_rule(b):
    rid = b.get("id")
    hit = {"v": False}

    def _mut(d):
        before = len(d.get("rules", []))
        d["rules"] = [r for r in d.get("rules", []) if r.get("id") != rid]
        hit["v"] = len(d["rules"]) < before
    _wt_save_config(_mut)
    return {"ok": True} if hit["v"] else _err("rule not found", 404)


def _op_set_quiet(b):
    start = _valid_hhmm(b.get("start"), None)
    end = _valid_hhmm(b.get("end"), None)
    if start is None or end is None:
        return _err("bad time (need HH:MM 24h)")
    _wt_save_config(lambda d: d.__setitem__("quiet_hours", {"start": start, "end": end}))
    return {"ok": True, "quiet_hours": {"start": start, "end": end}}


def _op_set_cap(b):
    n = _clamp_int(b.get("n"), 1, 200, None)
    if n is None:
        return _err("bad cap")
    _wt_save_config(lambda d: d.__setitem__("daily_cap", n))
    return {"ok": True, "daily_cap": n}


def _op_set_brief(b):
    def _mut(d):
        br = dict(d.get("brief", {}))
        if "enabled" in b:
            br["enabled"] = bool(b["enabled"])
        if "hour" in b:
            br["hour"] = _clamp_int(b["hour"], 0, 23, br.get("hour", 8))
        if "minute" in b:
            br["minute"] = _clamp_int(b["minute"], 0, 59, br.get("minute", 0))
        if "channels" in b:
            br["channels"] = _valid_channels(b["channels"], ["telegram", "hub"])
        d["brief"] = br
    d = _wt_save_config(_mut)
    return {"ok": True, "brief": d["brief"]}


def _latest_delivered_ts(rid):
    """ts of the most recent delivered (non-suppressed) fire for a rule, or None."""
    latest = None
    for r in _wt_log_read(500):
        if r.get("rule_id") == rid and not r.get("suppressed"):
            t = r.get("ts")
            if t is not None and (latest is None or t > latest):
                latest = t
    return latest


def _op_mark_reaction(b):
    reaction = b.get("reaction")
    if reaction not in ("useful", "noise", ""):
        return _err("bad reaction")
    ts = b.get("ts")
    if ts is None:
        rid = b.get("rule_id")
        if not rid:
            return _err("need ts or rule_id")
        ts = _latest_delivered_ts(rid)
        if ts is None:
            return _err("no fire to react to", 404)

    def _patch(row):
        try:
            if abs(float(row.get("ts", 0)) - float(ts)) < 1e-6:
                row["reaction"] = reaction
                return True
        except (TypeError, ValueError):
            return False
        return False
    changed = _wt_log_rewrite(_patch)
    return {"ok": True, "changed": changed}


def _op_mute_rule(b):
    rid = b.get("id")
    hit = {"v": False}

    def _mut(d):
        for r in d.get("rules", []):
            if r.get("id") == rid:
                r["enabled"] = False
                hit["v"] = True
    _wt_save_config(_mut)
    if not hit["v"]:
        return _err("rule not found", 404)

    # log a 'noise' reaction on this rule's most recent delivered fire
    latest = _latest_delivered_ts(rid)
    if latest is not None:
        def _patch(row):
            try:
                if abs(float(row.get("ts", 0)) - float(latest)) < 1e-6:
                    row["reaction"] = "noise"
                    return True
            except (TypeError, ValueError):
                return False
            return False
        _wt_log_rewrite(_patch)
    return {"ok": True}


def _op_test_rule(b):
    """Dry-run evaluate now against live cache — never sends, never mutates."""
    clean, e = _wt_validate_rule(b.get("rule"))
    if e:
        return e
    fire, sig, cctx = _evaluate(clean)
    text = _wt_notif_text(clean, cctx) if fire else ""
    return {"ok": True, "would_fire": bool(fire), "signature": sig,
            "context": cctx, "text": text, "type": clean["type"],
            "live": clean["type"] in LIVE_TYPES}


class _WTFull(Exception):
    pass


_WT_OPS = {
    "add_rule": _op_add_rule, "update_rule": _op_update_rule,
    "toggle_rule": _op_toggle_rule, "delete_rule": _op_delete_rule,
    "set_quiet_hours": _op_set_quiet, "set_daily_cap": _op_set_cap,
    "set_brief": _op_set_brief, "mark_reaction": _op_mark_reaction,
    "mute_rule": _op_mute_rule, "test_rule": _op_test_rule,
}


def watchtower_post_handler(ctx):
    b = ctx.body or {}
    op = b.get("op")
    fn = _WT_OPS.get(op)
    if not fn:
        return _err("unknown op: " + str(op))
    try:
        return fn(b)
    except Exception as e:
        return _err("internal: " + type(e).__name__ + ": " + str(e), 500)


def watchtower_feed_handler(ctx):
    fires = []
    for r in reversed(_wt_log_read(120)):
        if r.get("suppressed"):
            continue
        fires.append({"ts": r.get("ts"), "label": r.get("label", ""),
                      "type": r.get("type", ""), "text": r.get("text", ""),
                      "rule_id": r.get("rule_id", "")})
        if len(fires) >= FEED_N:
            break
    return {"ok": True, "fires": fires}


# ==========================================================================
# WIRING — rebind, routes, guarded thread (mirror aux_recorder's pattern)
# ==========================================================================
globals()["_generate_briefing"] = _wt_generate_briefing
globals()["_briefing_payload"] = _wt_briefing_payload

register_get("/api/brief/preview", brief_preview_handler)
register_post("/api/brief/send", brief_send_handler)
register_get("/api/watchtower", watchtower_get_handler)
register_post("/api/watchtower", watchtower_post_handler)
register_get("/api/watchtower/feed", watchtower_feed_handler)

if not globals().get("_watchtower_thread_started"):
    globals()["_watchtower_thread_started"] = True
    try:
        threading.Thread(target=watchtower_loop, daemon=True).start()
    except Exception as _e:            # pragma: no cover
        _wt_log_err("thread failed to start: %r" % _e)
