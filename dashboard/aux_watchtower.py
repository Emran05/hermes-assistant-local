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

TELEGRAM_MAX = 4096            # per-message Telegram cap (hermes send chunks here)
SEND_MAX = 14000               # runaway ceiling for a whole brief (~3-4 messages)
SEND_TIMEOUT = 60                # hermes CLI import + Telegram round trip

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
    md = d.get("midday") if isinstance(d.get("midday"), dict) else {}
    d["midday"] = {"enabled": bool(md.get("enabled", True)),
                   "hour": _clamp_int(md.get("hour", 15), 11, 17, 15),
                   "minute": _clamp_int(md.get("minute", 0), 0, 59, 0),
                   "min_items": _clamp_int(md.get("min_items", 2), 1, 6, 2),
                   "mover_pct": _clamp_float(md.get("mover_pct", 1.5), 0.5, 10.0) or 1.5,
                   "channels": _valid_channels(md.get("channels"), ["telegram", "hub"])}
    ev = d.get("evening") if isinstance(d.get("evening"), dict) else {}
    d["evening"] = {"enabled": bool(ev.get("enabled", True)),
                    "hour": _clamp_int(ev.get("hour", 18), 16, 23, 18),
                    "minute": _clamp_int(ev.get("minute", 0), 0, 59, 0),
                    "min_items": _clamp_int(ev.get("min_items", 1), 1, 6, 1),
                    "channels": _valid_channels(ev.get("channels"), ["telegram", "hub"])}
    ms = d.get("master") if isinstance(d.get("master"), dict) else {}
    d["master"] = {"briefings": bool(ms.get("briefings", True)),
                   "news": bool(ms.get("news", True))}
    bk = d.get("breaking") if isinstance(d.get("breaking"), dict) else {}
    d["breaking"] = {"enabled": bool(bk.get("enabled", True)),
                     "override_quiet": bool(bk.get("override_quiet", True)),
                     "daily_cap": _clamp_int(bk.get("daily_cap", 5), 1, 10, 5),
                     "index_pct": _clamp_float(bk.get("index_pct", 2.5), 1.0, 10.0) or 2.5,
                     "ticker_pct": _clamp_float(bk.get("ticker_pct", 8.0), 3.0, 20.0) or 8.0,
                     "cooldown_min": _clamp_int(bk.get("cooldown_min", 90), 15, 360, 90)}
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
    s.setdefault("last_midday_date", "")
    s.setdefault("last_evening_date", "")
    day = s.get("day") if isinstance(s.get("day"), dict) else {}
    s["day"] = {"date": day.get("date", ""), "sent": int(day.get("sent", 0) or 0)}
    s.setdefault("fires", {})
    if not isinstance(s["fires"], dict):
        s["fires"] = {}
    bk = s.get("breaking") if isinstance(s.get("breaking"), dict) else {}
    s["breaking"] = {
        "date": bk.get("date", ""), "sent": int(bk.get("sent", 0) or 0),
        "sigs": bk.get("sigs") if isinstance(bk.get("sigs"), dict) else {},
        "last_class": bk.get("last_class") if isinstance(bk.get("last_class"), dict) else {}}
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


# ---- links: [display](url) markdown — clickable on BOTH Telegram and the hub.
# Telegram's send path (send_message_tool -> adapter.format_message) converts
# [text](url) to a proper MarkdownV2 inline link with correct escaping; the hub's
# renderMd()/inline() converts it to <a href>. Bare URLs are NOT auto-linked by
# the hub renderer, so markdown links are the only form clickable on both.
def _linksafe(s):
    """Neutralise characters that would break the [display](url) link regex."""
    return str(s or "").replace("[", "(").replace("]", ")").replace("\n", " ").strip()


def _md_link(text, url):
    text = _linksafe(text)
    url = str(url or "").strip()
    if url.startswith("http") and ")" not in url:   # hub link regex stops at ')'
        return "[%s](%s)" % (text, url)
    if url.startswith("http"):                       # url has parens: append bare
        return "%s %s" % (text, url)
    return text


def _quote_url(symbol):
    return "https://finance.yahoo.com/quote/" + urllib.parse.quote(str(symbol or "").upper())


def _count_urls(text):
    return len(re.findall(r"https?://", text or ""))


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


def _slept_through(cur_min, sched_min, cutoff_min, grace_min=120):
    """A scheduled push still pending long after its slot (the Mac slept
    through it) is stale — skip it for today. Measured from the SCHEDULED
    time, not just the clock: the old absolute cutoff alone made any slot at
    or past it unreachable (a brief set to 19:00 or an evening wrap at 22:30
    was marked done without ever sending)."""
    return cur_min >= cutoff_min and cur_min - sched_min > grace_min


def _master_on(cfg, key):
    """Master toggles: 'briefings' gates the 8am/midday/evening pushes,
    'news' gates breaking alerts + rss_keyword watch rules."""
    m = cfg.get("master") if isinstance(cfg.get("master"), dict) else {}
    return bool(m.get(key, True))


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
    """Returns (ok, detail).  Reuses server's HERMES + _hermes_env().  NEVER
    passes a chat_id (locked to the home channel).  `hermes send` auto-chunks a
    long message into multiple 4096-char Telegram messages (and falls back to
    plain text if MarkdownV2 fails), so we do NOT pre-truncate at 4096 — that
    would drop the tail of a multi-section brief.  We only guard a runaway with
    a generous ceiling (a normal brief is one or two messages)."""
    text = _strip_emoji(text)
    if len(text) > SEND_MAX:
        text = text[:SEND_MAX - 1].rstrip() + "…"
    # --json (not --quiet): quiet mode prints NOTHING on failure, so every
    # delivery problem used to surface as a bare "exit 1". The JSON payload
    # carries the real reason ({error} / {skipped} / {success}).
    # 60s: the hermes CLI is a slow venv import plus a real Telegram round
    # trip; 20s timed out under load and the day's brief was lost.
    try:
        p = subprocess.run([HERMES, "send", "--to", "telegram", "--json", text],
                           capture_output=True, text=True, timeout=SEND_TIMEOUT,
                           env=_hermes_env())
    except subprocess.TimeoutExpired:
        return False, "TimeoutExpired: hermes send exceeded %ds" % SEND_TIMEOUT
    except Exception as e:
        return False, type(e).__name__ + ": " + str(e)
    out = (p.stdout or "").strip()
    payload = {}
    if "{" in out:
        try:
            payload = json.loads(out[out.index("{"):])
        except Exception:
            payload = {}
    if payload.get("error"):
        return False, str(payload["error"])[:200]
    if payload.get("skipped"):
        # `skipped: true` exits 0 (send_message_tool's cron-duplicate short
        # circuit) — still NOT a delivery; surface its reason/note
        return False, "skipped: " + str(payload.get("reason") or
                                        payload.get("note") or "")[:180]
    if p.returncode != 0:
        return False, ((p.stderr or out).strip()[:200] or
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
    """World front page from the full-spread feeds — PAST 24H ONLY, tagged by
    lean so the framing gap is visible. Dedupes near-identical headlines across
    outlets; falls back to the rss widget only if the intel store is cold."""
    now = time.time()
    fresh = [it for it in (_intel_load().get("items") or [])
             if it.get("topic") == "World" and it.get("ts")
             and (now - it["ts"]) <= WORLD_MAX_H * 3600]
    fresh.sort(key=lambda it: it.get("ts") or 0, reverse=True)
    seen, per_src, picks = set(), {}, []

    def _consider(it, src_cap):
        src = it.get("source", "")
        k = re.sub(r"\W+", "", str(it.get("title", "")).lower())[:55]
        if not k or k in seen or per_src.get(src, 0) >= src_cap:
            return
        seen.add(k)
        per_src[src] = per_src.get(src, 0) + 1
        picks.append(it)

    # pass 1: one per source first, so the spread (Fox/CNN/BBC/NPR/AJ/…) shows
    for it in fresh:
        if len(picks) >= 6:
            break
        _consider(it, 1)
    # pass 2: backfill up to 6 with a 2nd from the most active sources
    for it in fresh:
        if len(picks) >= 6:
            break
        _consider(it, 2)
    if picks:
        lines = []
        for it in picks:
            src = it.get("source", "")
            lean = _WORLD_LEAN.get(src)
            tag = (" (%s · %s)" % (src, lean)) if lean else ((" (%s)" % src) if src else "")
            lines.append("• %s%s" % (_md_link(it.get("title", ""), it.get("url")), tag))
        return _sec(lines)
    # cold-store fallback: the rss widget (no hard recency guarantee, but better
    # than an empty section on first boot before the hourly gather has run).
    secs = _rss_data().get("sections")
    if not isinstance(secs, list) or not secs:
        return _sec(note="No fresh headlines in the last 24h.")
    lines = []
    for sec in secs:
        for it in (sec.get("items") or [])[:2]:
            src = it.get("source", "")
            lines.append("• %s%s" % (_md_link(it.get("title", ""), it.get("url")),
                                     (" (" + src + ")") if src else ""))
        if len(lines) >= 5:
            break
    return _sec(lines) if lines else _sec(note="No fresh headlines in the last 24h.")


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
            parts.append("%s %s" % (_md_link(nm, _quote_url(q.get("symbol"))),
                                    _fmt_pct(q.get("pct"))))
        lines.append("Indices: " + ", ".join(parts))
    wl = [q for q in (mk.get("watchlist") or []) if not q.get("error")
          and q.get("pct") is not None]
    wl.sort(key=lambda q: abs(float(q.get("pct") or 0)), reverse=True)
    movers = [q for q in wl if abs(float(q.get("pct") or 0)) >= 0.4][:6] or wl[:4]
    if movers:
        lines.append("Movers:")
        for q in movers:
            link = _md_link(q.get("symbol"), _quote_url(q.get("symbol")))
            base = "• %s %s · %s" % (link, _fmt_pct(q.get("pct")),
                                     _fmt_price(q.get("price")))
            # honest extended-hours read: only when the session is truly PRE/POST
            if q.get("ext_kind") and q.get("ext_price") is not None:
                phase = "after hours" if q["ext_kind"] == "post" else "pre-market"
                base += " · %s %s (%s)" % (phase, _fmt_price(q.get("ext_price")),
                                           _fmt_pct(q.get("ext_pct")))
            lines.append(base)
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
            lines.append("• %s%s%s" % (_md_link(r.get("name", ""), r.get("url")),
                                            (" — " + desc) if desc else "",
                                            (" (" + meta + ")") if meta else ""))
    hn = _safe_call(w_hackernews, {})
    stories = [s for s in (hn.get("stories") or []) if s.get("title")]
    stories.sort(key=lambda s: int(s.get("score") or 0), reverse=True)
    if stories:
        lines.append("Hacker News risers:")
        for s in stories[:3]:
            lines.append("• %s (%s pts)" % (_md_link(s.get("title", ""), s.get("url")),
                                            s.get("score", 0)))
    # research-feed picks (Substack voices + niche communities) — the freshest
    # intel items most people haven't seen yet.
    picks = _intel_underground_picks(3)
    if picks:
        lines.append("From the feeds:")
        for it in picks:
            lines.append("• %s (%s)" % (_md_link(it.get("title", ""), it.get("url")),
                                        it.get("source", "")))
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
    if not lines:
        return _sec(note="Nothing flagged further out.")
    return _sec(lines)


_BRIEF_HEADERS = [("foryou", "For you — moves & people"),
                  ("day", "Your day"),
                  ("world", "World front page"),
                  ("ai", "AI & Labs"),
                  ("underground", "Underground signal"),
                  ("lookahead", "Look-ahead")]

FORYOU_FILE = os.path.join(DATA, "foryou.json")


def _brief_foryou():
    """The proactive-intelligence lead section: the top ranked 'moves for you'
    (do X / meet Y / go Z) with the why-you reasoning. Empty (degraded, so the
    render skips it) until the You-Model is onboarded — the brief then just
    starts with 'Your day' as before."""
    d = read_json(FORYOU_FILE, None)
    if not d or not d.get("personalized"):
        return _sec(note="run onboarding to personalize")
    lines = []
    for m in (d.get("moves") or [])[:3]:
        why = (m.get("why_you") or "").strip()
        act = (m.get("suggested_action") or "").strip()
        head = _md_link(m.get("title", ""), m.get("url"))
        tail = " · ".join([b for b in (act, ("because " + why) if why else "") if b])
        lines.append("• %s%s" % (head, (" — " + tail) if tail else ""))
    return _sec(lines=lines) if lines else _sec(note="no moves yet")


def _brief_build_sections():
    """Deterministic structured brief.  Returns (sections, degraded)."""
    builders = {"foryou": _brief_foryou,
                "day": _brief_day, "world": _brief_world, "ai": _brief_ai_labs,
                "markets": _brief_markets, "underground": _brief_underground,
                "lookahead": _brief_lookahead}
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
    """Background lane (:8081 small model) when it's up, else the primary."""
    bl = globals().get("bg_lane")
    if callable(bl):
        try:
            return bl()["chat_url"]
        except Exception:
            pass
    base = MODEL_URL
    if base.endswith("/v1/models"):
        return base[:-len("/models")] + "/chat/completions"
    return re.sub(r"/v1/models/?$", "/v1/chat/completions", base) \
        if "/v1/models" in base else "http://127.0.0.1:8080/v1/chat/completions"


def _active_model():
    bl = globals().get("bg_lane")
    if callable(bl):
        try:
            return bl()["model"]
        except Exception:
            pass
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
    "RULES: Keep EXACTLY these section headers as markdown '##' lines and in this "
    "order: 'For you — moves & people', 'Your day', 'World front page', 'AI & Labs', "
    "'Underground signal', 'Look-ahead'. Never add, drop, or rename a section. "
    "Use 12-hour clock times. NO emoji of any kind.\n"
    "CRITICAL — LINKS: The draft contains markdown links written as [text](url). "
    "You MUST preserve every link EXACTLY, keeping its full URL verbatim inside the "
    "parentheses. Never drop a link, never shorten or alter a URL, never replace a "
    "URL with '#' or a placeholder. Every item that had a link must still have it.\n"
    "Do NOT invent events, numbers, headlines, prices, or URLs — use only what the "
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
            # the brief now carries 6 sections + a link on every item; give the
            # rewrite enough room to reproduce it all (else the tail truncates and
            # the link-retention guard rightly falls back to deterministic).
            "temperature": 0.4, "max_tokens": 3200, "stream": False,
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
    # link-retention guard: if the model dropped the links, fall back to the
    # deterministic draft (which carries every URL) rather than ship a linkless
    # brief. Require >=70% of the draft's URLs to survive.
    det_urls = _count_urls(det_text)
    if det_urls and _count_urls(text) < 0.7 * det_urls:
        _wt_log_err("synthesis dropped links (%d/%d urls) — using deterministic"
                    % (_count_urls(text), det_urls))
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


# ==========================================================================
# AI & SOCIAL INTEL — hourly background research feeding the World Brief.
#
# The user asked to "send off the agent every hour" to search trending
# Twitter/Reddit/Substack + AI-lab news. The agent's web_search tool needs a
# provider (Firecrawl/Tavily key or Nous Portal login) that is NOT configured
# here and the keyless `ddgs` package is not installed, so `hermes -z` deflects
# instead of searching (verified). Per the fallback plan we gather the sources
# DIRECTLY over keyless RSS (reliable, zero-auth) on an hourly loop, then use the
# LOCAL model for one curation pass (why-it-matters) — the "hourly research
# pass" the user wanted, on real fetched data. If web_search is ever enabled,
# `_intel_web_ok()` flips True and `_intel_agent_pass()` augments the store.
#
# NOTIFY-ONLY + safety unchanged: read-only network (RSS GET), no mutating tool,
# no approval path, local model only. The brief is composed from the STORED
# intel (+ cached widgets) so the 8am compose does no fresh network.
# ==========================================================================
INTEL_FILE = os.path.join(DATA, "intel.json")
INTEL_MAX_ITEMS = 200            # room for AI feeds + the 8 world feeds together
INTEL_INTERVAL = 3600            # gather at most once/hour
INTEL_FRESH_H = 72               # retain 3 days so a poor-fetch pass can't thin
                                 # the store (labs don't post daily); the brief
                                 # still surfaces the freshest ~24h on top.
_intel_gather_lock = threading.Lock()   # only one gather at a time (no clobber)
INTEL_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36")
_VENV_PY = os.path.join(HOME, ".hermes", "hermes-agent", "venv", "bin", "python")
_HERMES_SRC = os.path.join(HOME, ".hermes", "hermes-agent")

# (topic, source-label, feed-url) — all verified keyless + reachable.
_INTEL_FEEDS = [
    ("OpenAI",  "OpenAI",           "https://openai.com/news/rss.xml"),
    ("Labs",    "Google DeepMind",  "https://deepmind.google/blog/rss.xml"),
    ("Labs",    "Hugging Face",     "https://huggingface.co/blog/feed.xml"),
    ("Labs",    "MIT Tech Review",  "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("News",    "TechCrunch AI",    "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("News",    "The Verge AI",     "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("News",    "VentureBeat AI",   "https://venturebeat.com/category/ai/feed/"),
    ("News",    "Ars Technica AI",  "https://arstechnica.com/ai/feed/"),
    ("Voices",  "Simon Willison",   "https://simonwillison.net/atom/everything/"),
    ("Voices",  "Import AI",        "https://importai.substack.com/feed"),
    ("Voices",  "Zvi",              "https://thezvi.substack.com/feed"),
    ("Voices",  "One Useful Thing", "https://www.oneusefulthing.org/feed"),
    ("Social",  "r/LocalLLaMA",     "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day"),
    ("Social",  "r/artificial",     "https://www.reddit.com/r/artificial/top/.rss?t=day"),
]
_UNDERGROUND_TOPICS = ("Voices", "Social")   # what enriches the underground section

# --- World news — full spread, tagged by lean so the brief shows the framing gap.
# AP + Reuters intentionally absent: both discontinued public RSS (verified dead
# 2026-07 — AP's host doesn't resolve, Reuters 404s). Guardian/PBS/DW carry the
# factual/wire layer; BBC/NPR public; Fox/CNN the US poles; Al Jazeera the Mideast.
# All verified live + carrying pubDate/published timestamps (recency-filterable).
WORLD_MAX_H = 24              # general brief only surfaces items from the past 24h
BREAKING_MAX_H = 6           # breaking alerts locked to the past 6h (user: "6-12")
_WORLD_FEEDS = [
    ("World", "Guardian",   "https://www.theguardian.com/world/rss"),
    ("World", "PBS",        "https://www.pbs.org/newshour/feeds/rss/headlines"),
    ("World", "DW",         "https://rss.dw.com/rdf/rss-en-world"),
    ("World", "BBC World",  "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("World", "NPR",        "https://feeds.npr.org/1001/rss.xml"),
    ("World", "Fox",        "https://moxie.foxnews.com/google-publisher/world.xml"),
    ("World", "CNN",        "http://rss.cnn.com/rss/cnn_world.rss"),
    ("World", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
]
_WORLD_SOURCES = {f[1] for f in _WORLD_FEEDS}      # keeps World out of AI & Labs
_WORLD_LEAN = {"Guardian": "factual", "PBS": "factual", "DW": "factual",
               "BBC World": "public", "NPR": "public",
               "Fox": "right", "CNN": "left", "Al Jazeera": "Mideast"}


def _intel_clean(s):
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    s = re.sub(r"&#?\w+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _intel_norm_url(u):
    u = str(u or "").strip()
    u = re.sub(r"[?#].*$", "", u)          # drop query/fragment for dedupe
    return u.rstrip("/").lower()


def _intel_load():
    d = read_json(INTEL_FILE, None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("version", 1)
    d.setdefault("updated", 0)
    if not isinstance(d.get("items"), list):
        d["items"] = []
    if not isinstance(d.get("curated"), list):
        d["curated"] = []
    return d


def _intel_save(store):
    with _wt_lock:
        _wt_write_json(INTEL_FILE, store)


def _intel_fetch_feed(url, source, topic):
    """Fetch + parse one feed with a browsery UA (reddit/substack reject the
    default UA). Never raises; returns a list of item dicts."""
    import xml.etree.ElementTree as ET
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": INTEL_UA,
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*"})
        with urllib.request.urlopen(req, timeout=14, context=_SSL_CTX) as r:
            raw = r.read().decode("utf-8", "replace")
        root = ET.fromstring(raw)
    except Exception:
        return []
    out = []
    for el in root.iter():
        if el.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title = link = date_raw = summary = ""
        for c in el:
            ct = c.tag.split("}")[-1]
            if ct == "title" and not title:
                title = (c.text or "").strip()
            elif ct == "link":
                href = (c.get("href") or c.text or "").strip()
                if href.startswith("http") and (not link or c.get("rel") in (None, "", "alternate")):
                    link = href
            elif ct in ("pubDate", "published", "updated", "date") and not date_raw:
                date_raw = (c.text or "").strip()
            elif ct in ("description", "summary", "content") and not summary:
                summary = c.text or ""
        if not title or not link:
            continue
        try:
            ts = _news_parse_date(date_raw)
        except Exception:
            ts = None
        out.append({"title": _intel_clean(title)[:180], "url": link,
                    "source": source, "topic": topic, "ts": ts,
                    "summary": _intel_clean(summary)[:220]})
        if len(out) >= 8:
            break
    return out


def _intel_feeds():
    feeds = list(_INTEL_FEEDS) + list(_WORLD_FEEDS)
    for u in (_safe_call(get_settings, {}).get("intel_feeds") or []):
        if isinstance(u, str) and u.startswith("http"):
            try:
                dom = urllib.parse.urlparse(u).netloc.replace("www.", "")
            except Exception:
                dom = "custom"
            feeds.append(("Yours", dom, u))
    return feeds


def _intel_gather():
    """One hourly research pass: fetch feeds -> merge/dedupe/prune -> curate -> save.
    Guarded so a manual poke and the hourly loop can't run concurrently and
    clobber each other's read-modify-write."""
    if not _intel_gather_lock.acquire(blocking=False):
        return (_intel_load().get("items") or [])
    try:
        feeds = _intel_feeds()
        fetched = []
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=8) as ex:
                for res in ex.map(lambda f: _intel_fetch_feed(f[2], f[1], f[0]), feeds):
                    fetched.extend(res or [])
        except Exception:
            for f in feeds:
                fetched.extend(_intel_fetch_feed(f[2], f[1], f[0]) or [])

        # optional: augment with the agent's web_search when it is actually usable
        fetched.extend(_intel_agent_pass())

        store = _intel_load()
        now = time.time()
        by_url = {}
        for it in (store.get("items", []) + fetched):
            key = _intel_norm_url(it.get("url"))
            if not key:
                continue
            prev = by_url.get(key)
            if prev is None or (it.get("ts") or 0) > (prev.get("ts") or 0):
                by_url[key] = it
        merged = [it for it in by_url.values()
                  if not it.get("ts") or (now - (it.get("ts") or now)) < INTEL_FRESH_H * 3600]
        merged.sort(key=lambda it: it.get("ts") or 0, reverse=True)
        merged = merged[:INTEL_MAX_ITEMS]
        # curate only the AI-topic items — world news has its own section
        curated = _intel_curate([it for it in merged
                                 if it.get("source") not in _WORLD_SOURCES])
        _intel_save({"version": 1, "updated": now, "items": merged, "curated": curated,
                     "feeds": len(feeds)})
        try:
            _trends_update(merged)          # refresh today's trend-radar tally
        except Exception as _e:
            _wt_log_err("trends update: %r" % _e)
        _wt_log_err("intel gather: %d feeds, %d fetched -> %d items, %d curated"
                    % (len(feeds), len(fetched), len(merged), len(curated)))
        return merged
    finally:
        _intel_gather_lock.release()


# ---- one LOCAL-model curation pass: pick the top items + a one-line "why" ----
_INTEL_CURATE_SYS = (
    "You are an AI-industry analyst. From the list of recent headlines you are "
    "given, select the 6 MOST significant developments about AI labs (Anthropic/"
    "Claude, OpenAI, Google DeepMind, Meta, Mistral, xAI, etc.), notable model or "
    "product releases, or important research/community signals. For each, write ONE "
    "short sentence on why it matters. Use ONLY the items provided — never invent a "
    "headline, source, or URL, and copy each URL verbatim. Output ONLY a JSON array "
    "of objects with keys: title, source, url, why. No prose, no code fence.")


def _intel_curate(items):
    """Returns a curated list [{title,source,url,why}] or [] (best-effort)."""
    if not items or not model_online() or agent_paused():
        return []
    pool = items[:22]
    listing = "\n".join(
        "- %s | %s | %s" % (it.get("title", ""), it.get("source", ""), it.get("url", ""))
        for it in pool)
    try:
        payload = json.dumps({
            "model": _active_model(),
            "messages": [{"role": "system", "content": _INTEL_CURATE_SYS},
                         {"role": "user", "content":
                          "Recent AI headlines:\n" + listing}],
            "temperature": 0.3, "max_tokens": 900, "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(_model_chat_url(), data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
        text = (((resp.get("choices") or [{}])[0].get("message") or {})
                .get("content") or "").strip()
    except Exception as e:
        _wt_log_err("intel curate failed: %r" % e)
        return []
    arr = _intel_extract_json(text)
    valid_urls = {_intel_norm_url(it.get("url")) for it in pool}
    out = []
    for o in arr:
        if not isinstance(o, dict):
            continue
        url = str(o.get("url", "")).strip()
        if _intel_norm_url(url) not in valid_urls:   # reject invented URLs
            continue
        out.append({"title": _intel_clean(o.get("title"))[:180], "url": url,
                    "source": _intel_clean(o.get("source"))[:40],
                    "why": _intel_clean(o.get("why"))[:180]})
        if len(out) >= 6:
            break
    return out


def _intel_extract_json(text):
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.S)     # first JSON array
    if not m:
        return []
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, list) else []
    except Exception:
        return []


# ---- optional agent augmentation (dormant until web_search is configured) ----
def _intel_web_ok():
    """Cached probe: is the agent's web_search actually usable here? Currently
    False (no Firecrawl/Tavily key, no Nous Portal login, ddgs not installed).
    Flips True automatically once a provider is configured."""
    def probe():
        if not os.path.exists(_VENV_PY):
            return False
        code = ("import json;from tools.web_tools import web_search_tool;"
                "print(web_search_tool('AI news',1))")
        try:
            p = subprocess.run([_VENV_PY, "-c", code], capture_output=True, text=True,
                               timeout=30, cwd=_HERMES_SRC,
                               env={**_hermes_env(),
                                    "HERMES_HOME": os.path.join(HOME, ".hermes")})
            out = (p.stdout or "").strip()
            # web_search_tool returns PRETTY-PRINTED multi-line JSON — parse
            # the whole blob from the first brace, not just the last line.
            j = json.loads(out[out.index("{"):]) if "{" in out else {}
            return bool(j.get("success") or j.get("data"))
        except Exception:
            return False
    try:
        return _cached("intel_web_ok", 6 * 3600, probe)
    except Exception:
        return False


_INTEL_AGENT_PROMPT = (
    "Use your web_search tool NOW (do not answer from memory) to find the most "
    "important, freshest items from the last 24 hours across: (a) trending AI "
    "discussion on Reddit/X/Substack, and (b) AI-lab news — Anthropic/Claude, "
    "OpenAI, Google DeepMind, Meta AI, Mistral, xAI. Return ONLY a JSON array of "
    "objects with keys title, source, url, why (one line). Copy URLs verbatim.")


_intel_skip_last = [0.0]


def _intel_note_skip(why):
    """Log a skipped agent pass at most once per hour (the loop polls 5-minutely)."""
    now = time.time()
    if now - _intel_skip_last[0] >= 3600:
        _intel_skip_last[0] = now
        _wt_log_err("intel agent pass skipped: " + why)


def _intel_agent_pass():
    """When web_search is usable, run ONE `hermes -z` research pass and return
    parsed items. Dormant (returns []) until a web provider is configured."""
    if not _intel_web_ok():
        return []
    try:
        bl = globals().get("bg_lane")
        lane = {"lane": "primary", "hermes_args": []}
        if callable(bl):
            try:
                lane = bl() or lane                            # background lane
            except Exception:
                pass
        extra = list(lane.get("hermes_args") or [])
        if lane.get("lane") != "bg":
            # bg lane down -> this run would land on the PRIMARY. Only use it
            # when it is genuinely up and not paused; never wake it for
            # background research, never hang 180s against a dead endpoint.
            if agent_paused() or not model_online():
                _intel_note_skip("no model lane online (primary paused/asleep, "
                                 "background lane down)")
                return []
        p = subprocess.run([HERMES] + extra + ["-z", _INTEL_AGENT_PROMPT],
                           capture_output=True, text=True, timeout=180,
                           env=_hermes_env())
        arr = _intel_extract_json(p.stdout or "")
    except Exception as e:
        _wt_log_err("intel agent pass failed: %r" % e)
        return []
    out = []
    for o in arr:
        if isinstance(o, dict) and str(o.get("url", "")).startswith("http"):
            out.append({"title": _intel_clean(o.get("title"))[:180],
                        "url": str(o.get("url")).strip(),
                        "source": _intel_clean(o.get("source"))[:40] or "web",
                        "topic": "Agent", "ts": time.time(),
                        "summary": _intel_clean(o.get("why"))[:220]})
    _wt_log_err("intel agent pass: %d items" % len(out))
    return out


# ---- brief consumers ----
def _brief_ai_labs():
    store = _intel_load()
    curated = [c for c in (store.get("curated") or [])
               if c.get("source") not in _WORLD_SOURCES]
    lines = []
    if curated:
        for c in curated[:4]:
            why = (c.get("why") or "").strip()
            lines.append("• %s (%s)%s" % (
                _md_link(c.get("title", ""), c.get("url")),
                c.get("source", ""), (" — " + why) if why else ""))
    else:
        items = store.get("items") or []
        now = time.time()
        fresh = [it for it in items
                 if it.get("source") not in _WORLD_SOURCES
                 and (not it.get("ts") or (now - (it.get("ts") or now)) < 24 * 3600)]
        for it in (fresh or items)[:4]:
            lines.append("• %s (%s)" % (_md_link(it.get("title", ""), it.get("url")),
                                        it.get("source", "")))
    if not lines:
        return _sec(note="No fresh AI-lab news gathered yet "
                         "(the hourly research pass runs in the background).")
    return _sec(lines, meta={"updated": store.get("updated")})


def _intel_underground_picks(n):
    """Freshest Voices/Social intel items for the underground section."""
    store = _intel_load()
    picks = [it for it in (store.get("items") or [])
             if it.get("topic") in _UNDERGROUND_TOPICS]
    picks.sort(key=lambda it: it.get("ts") or 0, reverse=True)
    return picks[:n]


def intel_loop():
    """Sibling daemon: gather intel at most once/hour (catch-up after downtime)."""
    _wt_log_err("intel loop started")
    time.sleep(20)                       # let the hub prewarm first
    while True:
        try:
            store = _intel_load()
            if time.time() - (store.get("updated") or 0) >= INTEL_INTERVAL:
                _intel_gather()
        except Exception as e:
            _wt_log_err("intel loop: %r" % e)
        time.sleep(300)                  # check every 5 min


# ==========================================================================
# NEWS DESK — framing comparison ("one story, every lens").
# Clusters the lean-tagged world headlines into stories that several outlets
# cover, then keeps the ones where the FRAMING differs across the spectrum
# (>=2 leans). Pure headline analysis — no model, no network (reads the intel
# store). The headline IS the framing: "hardline war backer dies" vs "Trump ally
# dies" is the whole point.
# ==========================================================================

_NEWS_STOP = set((
    "the a an and or of to in on for with at by from as is are was were be been "
    "being this that these those over into after before amid it its his her their "
    "they he she you we has have had will would could should may might must not no "
    "new now say says said report reports can more most than then them out up down "
    "off but who what when where why how about against says amid live day".split()))


def _news_terms(title):
    """(all-significant-terms, proper-nouns) from a headline. Proper nouns
    (Capitalized mid-title: names/places/orgs) are the strong clustering signal."""
    caps = {w.lower() for w in re.findall(r"\b([A-Z][a-zA-Z]{2,})", title or "")}
    words = {w for w in re.findall(r"[a-zA-Z]{4,}", (title or "").lower())
             if w not in _NEWS_STOP}
    return words | caps, caps


def _framing_clusters(window_h=36, max_stories=6):
    """Fresh world items clustered into cross-outlet stories with >=2 leans, so
    every result is a genuine framing contrast. Most-covered / newest first."""
    now = time.time()
    prepared = []
    for it in (_intel_load().get("items") or []):
        if it.get("source") not in _WORLD_SOURCES or not it.get("ts"):
            continue
        if now - it["ts"] > window_h * 3600:
            continue
        allt, caps = _news_terms(it.get("title", ""))
        if caps:                        # no proper noun -> nothing to anchor on
            prepared.append((it, allt, caps))
    clusters = []                        # each: {terms, caps, items:[(it,allt,caps)]}
    for it, allt, caps in prepared:
        best, best_score = None, 0
        for cl in clusters:
            scap = cl["caps"] & caps     # REQUIRE a shared proper noun (kills weak merges)
            if not scap:
                continue
            sall = cl["terms"] & allt
            if len(scap) >= 2 or len(sall) >= 3:
                score = 2 * len(scap) + len(sall)
                if score > best_score:
                    best, best_score = cl, score
        if best:
            best["items"].append((it, allt, caps))
            best["terms"] |= allt
            best["caps"] |= caps
        else:
            clusters.append({"terms": set(allt), "caps": set(caps),
                             "items": [(it, allt, caps)]})
    stories = []
    for cl in clusters:
        by_src = {}
        for it, _a, _c in cl["items"]:
            by_src.setdefault(it["source"], it)     # one headline per outlet
        leans = {_WORLD_LEAN.get(s) for s in by_src}
        leans.discard(None)
        if len(by_src) < 2 or len(leans) < 2:        # need a real cross-lens contrast
            continue
        label = sorted(cl["caps"],
                       key=lambda w: -sum(w in c for _i, _a, c in cl["items"]))[:3]
        angles = sorted(
            [{"source": s, "lean": _WORLD_LEAN.get(s, ""),
              "title": it.get("title", ""), "url": it.get("url"), "ts": it.get("ts")}
             for s, it in by_src.items()],
            key=lambda a: (a["lean"], a["source"]))
        stories.append({"label": " ".join(w.title() for w in label),
                        "leans": sorted(leans), "sources": len(by_src),
                        "ts": max(it.get("ts") or 0 for it in by_src.values()),
                        "angles": angles})
    stories.sort(key=lambda s: (-len(s["leans"]), -s["sources"], -(s["ts"] or 0)))
    return stories[:max_stories]


def w_framing():
    try:
        return {"stories": _framing_clusters(), "updated": time.time()}
    except Exception as e:
        return {"stories": [], "error": type(e).__name__}


def expand_framing():
    try:
        return {"stories": _framing_clusters(window_h=48, max_stories=14),
                "updated": time.time()}
    except Exception as e:
        return {"stories": [], "error": type(e).__name__}


def framing_handler(ctx):
    return w_framing()


register_get("/api/framing", framing_handler)          # noqa: F821
WIDGETS["framing"] = {"title": "Every Lens", "icon": "news", "size": "card",   # noqa: F821
                      "cat": "news", "provider": w_framing}
EXPANDERS["framing"] = expand_framing                  # noqa: F821


# ==========================================================================
# TREND RADAR — what's ACCELERATING across the feeds.
# Tallies proper-noun entities (people/places/orgs) from each day's headlines
# into a small daily ledger (trends.json), then ranks entities whose mentions
# TODAY spike above their recent baseline. New + rising both surface. Pure
# headline counting — no model. On day 1 there's no baseline, so it shows the
# day's hottest topics; acceleration sharpens as the ledger fills.
# ==========================================================================

TRENDS_FILE = os.path.join(DATA, "trends.json")
TRENDS_KEEP_DAYS = 14
_TREND_STOP = set(("us u.s new the how what says amid live day world part watch "
                   "here more back top full first video news update".split()))


def _trend_entities(title):
    ents = {}
    for w in re.findall(r"\b([A-Z][a-zA-Z]{2,})", title or ""):
        k = w.lower()
        if k in _TREND_STOP or k in _NEWS_STOP:
            continue
        ents[k] = w                     # keep a display surface form
    return ents


def _trends_load():
    d = read_json(TRENDS_FILE, {"days": {}, "labels": {}})
    if not isinstance(d.get("days"), dict):
        d = {"days": {}, "labels": {}}
    d.setdefault("labels", {})
    return d


def _trends_update(items=None):
    """Recompute TODAY's entity tally from the intel store (idempotent for the
    day); past days stay frozen. Called each intel gather (+ lazily on view)."""
    if items is None:
        items = _intel_load().get("items") or []
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    counts, labels = {}, {}
    for it in items:
        ts = it.get("ts") or 0
        if not ts or time.strftime("%Y-%m-%d", time.localtime(ts)) != today:
            continue
        for k, disp in _trend_entities(it.get("title", "")).items():
            counts[k] = counts.get(k, 0) + 1
            labels[k] = disp
    d = _trends_load()
    d["days"][today] = counts
    d["labels"].update(labels)
    for day in sorted(d["days"])[:-TRENDS_KEEP_DAYS]:      # prune old days
        d["days"].pop(day, None)
    d["updated"] = now
    with _state_lock:
        write_json(TRENDS_FILE, d)
    return d


def _trends_rising(n=8):
    d = _trends_load()
    days = sorted(d["days"])
    if not days:
        return []
    today = days[-1]
    prior = days[:-1][-7:]                       # up to 7 prior days = baseline
    tcounts = d["days"].get(today, {})
    out = []
    for k, tc in tcounts.items():
        if tc < 2:
            continue                             # ignore one-off mentions
        hist = [d["days"].get(day, {}).get(k, 0) for day in prior]
        avg = (sum(hist) / len(hist)) if hist else 0.0
        delta = tc - avg
        ratio = tc / (avg + 1.0)
        score = tc + 2.0 * max(0.0, delta) + 1.5 * ratio
        kind = "new" if avg < 0.3 else ("rising" if delta > 0.6 else "steady")
        out.append({"entity": d["labels"].get(k, k), "today": tc,
                    "avg": round(avg, 1), "kind": kind, "score": score,
                    "spark": (hist + [tc])[-8:]})
    out.sort(key=lambda e: -e["score"])
    return out[:n]


def w_trends():
    try:
        d = _trends_load()
        if time.strftime("%Y-%m-%d") not in d.get("days", {}):
            _trends_update()                     # lazy first tally for instant display
        return {"rising": _trends_rising(), "updated": time.time(),
                "days": len(_trends_load().get("days", {}))}
    except Exception as e:
        return {"rising": [], "error": type(e).__name__}


def expand_trends():
    try:
        return {"rising": _trends_rising(16), "updated": time.time(),
                "days": len(_trends_load().get("days", {}))}
    except Exception as e:
        return {"rising": [], "error": type(e).__name__}


def trends_handler(ctx):
    return w_trends()


register_get("/api/trends", trends_handler)            # noqa: F821
WIDGETS["trends"] = {"title": "Trend Radar", "icon": "trend", "size": "card",   # noqa: F821
                     "cat": "news", "provider": w_trends}
EXPANDERS["trends"] = expand_trends                    # noqa: F821


# ==========================================================================
# MIDDAY PULSE (~3pm) + BREAKING ALERTS
#
# GUIDELINES (author-defined per the user's ask):
#
# Midday pulse — a light, optional update, NOT a second full brief:
#   * Fires once/day at ~3:00 PM local (configurable 11:00-17:00), date-guarded
#     (last_midday_date), catch-up-on-wake until 6 PM — after that the day is
#     marked done (a 7 PM "midday" update is noise).
#   * ONLY sends when there is something noteworthy: intraday movers >=1.5%
#     (only when the market session is live/extended — honest), fresh AI/news
#     items since the morning brief, and Watchtower fires since morning. Fewer
#     than `min_items` (default 2) buckets of content => skipped silently
#     (logged suppressed:"not_noteworthy"). "Not a must" by design.
#   * Compact, deterministic (no model pass), links on every item, zero emoji,
#     12-hour times. Composed 100% from cached data + the intel store.
#
# Breaking alerts — reserved for genuinely urgent, pushed immediately:
#   * Scanned every loop pass (60s) against CACHED data only, so detection
#     latency is bounded by cache freshness (markets ~5 min, news <=15 min,
#     AI intel <=1 h) with zero extra network.
#   * Three trigger classes, each with a high bar:
#       market — an index moves >=2.5% or a watchlist ticker >=8% intraday,
#                only during a live/extended session (never stale weekend data);
#       news   — a severe-event keyword (war/disaster/market-halt class) in a
#                fresh (<2 h) headline CORROBORATED by >=2 distinct sources
#                (one outlet's clickbait never pages the phone);
#       ai     — a fresh (<3 h) intel item matching an AI-lab name AND a
#                major-action verb (releases/acquires/resigns/breach/...).
#   * Anti-spam, in order: per-story signature dedupe (never the same story
#     twice; market sigs re-arm only on a materially deeper move), per-class
#     cooldown (default 90 min), its own daily cap (default 5), and by default
#     it OVERRIDES quiet hours (that is its purpose) — set override_quiet:false
#     to keep nights silent. Every push/suppression is logged; Useful/Noise
#     reactions in the Mind card tune it.
#   * Notify-only, same as everything here: no tools, no approvals, no chat_id.
# ==========================================================================
def _today_at(hour, minute=0, ts=None):
    lt = time.localtime(ts if ts is not None else time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(hour), int(minute),
                        0, lt.tm_wday, lt.tm_yday, -1))


def _midday_compose(cfg):
    """Deterministic 'what changed since this morning' pulse.  Always builds
    text; `noteworthy` says whether the scheduler should actually send it."""
    md = cfg.get("midday", {})
    cutoff = _today_at((cfg.get("brief") or {}).get("hour", 8))
    now = time.time()
    items_count = 0
    lines = ["Midday pulse — %s" % _t12(now)]

    # markets: only when a session is live/extended (weekend/closed => nothing
    # has changed since the morning brief, so say nothing)
    mk = _markets_data()
    state = mk.get("state")
    if state in ("REGULAR", "PRE", "POST") and not mk.get("error"):
        tag = {"REGULAR": "live", "PRE": "pre-market", "POST": "after hours"}[state]
        idx = [q for q in (mk.get("indices") or []) if not q.get("error")]
        if idx:
            lines.append("Markets (%s): " % tag + " · ".join(
                "%s %s" % (_md_link(q.get("friendly") or q.get("symbol"),
                                    _quote_url(q.get("symbol"))), _fmt_pct(q.get("pct")))
                for q in idx))
        wl = [q for q in (mk.get("watchlist") or [])
              if not q.get("error") and q.get("pct") is not None]
        movers = [q for q in wl
                  if abs(float(q.get("pct") or 0)) >= float(md.get("mover_pct", 1.5))]
        movers.sort(key=lambda q: abs(float(q.get("pct") or 0)), reverse=True)
        if movers:
            lines.append("Movers now:")
            for q in movers[:4]:
                lines.append("• %s %s · %s" % (
                    _md_link(q.get("symbol"), _quote_url(q.get("symbol"))),
                    _fmt_pct(q.get("pct")), _fmt_price(q.get("price"))))
            items_count += len(movers[:4])

    # fresh AI intel + headlines since the morning brief
    fresh = []
    for it in (_intel_load().get("items") or []):
        if (it.get("ts") or 0) >= cutoff:
            fresh.append(("AI", it))
    for sec in (_rss_data().get("sections") or []):
        for it in (sec.get("items") or []):
            if (it.get("ts") or 0) >= cutoff:
                fresh.append((sec.get("name", "News"), it))
    fresh.sort(key=lambda p: p[1].get("ts") or 0, reverse=True)
    seen, picked = set(), []
    for tag, it in fresh:
        k = _intel_norm_url(it.get("url"))
        if k and k not in seen:
            seen.add(k)
            picked.append((tag, it))
        if len(picked) >= 5:
            break
    if picked:
        lines.append("New since this morning:")
        for tag, it in picked:
            lines.append("• %s: %s (%s)" % (
                tag, _md_link(it.get("title", ""), it.get("url")), it.get("source", "")))
        items_count += len(picked)

    # watchtower fires since morning (delivered only; skip midday's own rows)
    fires = [r for r in _wt_log_read(200)
             if (r.get("ts") or 0) >= cutoff and not r.get("suppressed")
             and r.get("type") != "midday_brief"]
    if fires:
        lines.append("Watchtower flags:")
        for r in fires[-3:]:
            lines.append("• %s — %s" % (r.get("label") or r.get("type", ""),
                                        _t12(r.get("ts"))))
        items_count += len(fires[-3:])

    noteworthy = items_count >= int(md.get("min_items", 2))
    return {"noteworthy": noteworthy, "items": items_count,
            "text": _strip_emoji("\n".join(lines)), "since": cutoff}


def _midday_tick(cfg, state, now_ts):
    """Once/day at ~3pm: send the pulse only if noteworthy; date-guarded."""
    md = cfg.get("midday", {})
    if not _master_on(cfg, "briefings") or not md.get("enabled", True):
        return
    today = _today_str(now_ts)
    if state.get("last_midday_date") == today:
        return
    if _in_quiet(now_ts, cfg.get("quiet_hours", {})):
        return                           # retried next pass; window caps below
    lt = time.localtime(now_ts)
    cur_min = lt.tm_hour * 60 + lt.tm_min
    md_min = md.get("hour", 15) * 60 + md.get("minute", 0)
    if cur_min < md_min:
        return
    if _slept_through(cur_min, md_min, 18 * 60):   # slept through the window — skip today
        _wt_save_state(lambda s: s.__setitem__("last_midday_date", today))
        state["last_midday_date"] = today
        return
    try:
        comp = _midday_compose(cfg)
    except Exception as e:
        _wt_log_err("midday compose failed: %r" % e)
        comp = {"noteworthy": False, "items": 0, "text": ""}
    row = {"ts": now_ts, "rule_id": "", "type": "midday_brief",
           "label": "Midday pulse", "signature": "midday:" + today,
           "context": {"items": comp["items"]},
           "channels": md.get("channels", ["telegram", "hub"]),
           "delivered": [], "suppressed": "", "reaction": ""}
    if not comp["noteworthy"]:
        row["suppressed"] = "not_noteworthy"
    else:
        ok, det = True, ""
        if "telegram" in row["channels"]:
            ok, det = _wt_send_telegram(comp["text"])
        if ok:
            row["delivered"] = list(row["channels"])
            row["text"] = comp["text"]
        else:
            row["suppressed"] = "deliver_failed"
            row["detail"] = det
    _wt_log_append(row)
    # date flips after one attempt either way — no afternoon retry storm
    _wt_save_state(lambda s: s.__setitem__("last_midday_date", today))
    state["last_midday_date"] = today


def _evening_compose(cfg):
    """Deterministic end-of-day wrap: the day's biggest stories + what's trending
    + still-open tasks. Short by design (the morning brief is the long one).
    Always builds text; `noteworthy` gates the send."""
    ev = cfg.get("evening", {})
    now = time.time()
    day_start = _today_at(0)
    lines = ["Evening wrap — %s, %s" % (_fmt_date(now), _t12(now))]
    items = 0

    try:
        stories = _framing_clusters(window_h=20, max_stories=3)
    except Exception:
        stories = []
    if stories:
        lines.append("Today's biggest stories:")
        for s in stories:
            top = (s.get("angles") or [{}])[0]
            lines.append("• %s — %s" % (s.get("label", ""),
                         _md_link(top.get("title", ""), top.get("url"))))
        items += len(stories)
    else:
        world = sorted(
            [it for it in (_intel_load().get("items") or [])
             if it.get("source") in _WORLD_SOURCES and (it.get("ts") or 0) >= day_start],
            key=lambda it: it.get("ts") or 0, reverse=True)
        if world:
            lines.append("Today's headlines:")
            for it in world[:4]:
                lines.append("• %s (%s)" % (
                    _md_link(it.get("title", ""), it.get("url")), it.get("source", "")))
            items += min(4, len(world))

    try:
        rising = _trends_rising(4)
    except Exception:
        rising = []
    if rising:
        lines.append("Trending today: " + ", ".join(
            "%s (%d)" % (r.get("entity"), r.get("today")) for r in rising))
        items += 1

    tasks = [t for t in _safe_call(get_tasks, {"tasks": []}).get("tasks", [])
             if not t.get("done")]
    if tasks:
        lines.append("Still open: " + "; ".join(t.get("text", "") for t in tasks[:3]))

    noteworthy = items >= int(ev.get("min_items", 1))
    return {"noteworthy": noteworthy, "items": items,
            "text": _strip_emoji("\n".join(lines))}


def _evening_tick(cfg, state, now_ts):
    """Once/day at ~6pm: a short end-of-day wrap; catch-up until ~10pm, then the
    day is done. Date-guarded; notify-only (no actions)."""
    ev = cfg.get("evening", {})
    if not _master_on(cfg, "briefings") or not ev.get("enabled", True):
        return
    today = _today_str(now_ts)
    if state.get("last_evening_date") == today:
        return
    if _in_quiet(now_ts, cfg.get("quiet_hours", {})):
        return                           # retried next pass; window caps below
    lt = time.localtime(now_ts)
    cur_min = lt.tm_hour * 60 + lt.tm_min
    ev_min = ev.get("hour", 18) * 60 + ev.get("minute", 0)
    if cur_min < ev_min:
        return
    if _slept_through(cur_min, ev_min, 22 * 60):   # slept through the window — skip today
        _wt_save_state(lambda s: s.__setitem__("last_evening_date", today))
        state["last_evening_date"] = today
        return
    try:
        comp = _evening_compose(cfg)
    except Exception as e:
        _wt_log_err("evening compose failed: %r" % e)
        comp = {"noteworthy": False, "items": 0, "text": ""}
    row = {"ts": now_ts, "rule_id": "", "type": "evening_wrap",
           "label": "Evening wrap", "signature": "evening:" + today,
           "context": {"items": comp["items"]},
           "channels": ev.get("channels", ["telegram", "hub"]),
           "delivered": [], "suppressed": "", "reaction": ""}
    if not comp["noteworthy"]:
        row["suppressed"] = "not_noteworthy"
    else:
        ok, det = True, ""
        if "telegram" in row["channels"]:
            ok, det = _wt_send_telegram(comp["text"])
        if ok:
            row["delivered"] = list(row["channels"])
            row["text"] = comp["text"]
        else:
            row["suppressed"] = "deliver_failed"
            row["detail"] = det
    _wt_log_append(row)
    _wt_save_state(lambda s: s.__setitem__("last_evening_date", today))
    state["last_evening_date"] = today


# ---- breaking: severity keyword tables (curated tight to keep the bar high) --
_BREAK_NEWS_KW = [
    "declares war", "invasion of", "invades", "airstrike", "air strike",
    "missile strike", "nuclear", "assassinat", "coup ", "state of emergency",
    "earthquake", "tsunami", "hurricane", "mass shooting", "explosion",
    "market crash", "flash crash", "trading halted", "circuit breaker",
    "defaults on", "files for bankruptcy", "bank run", "hijack", "shot down",
    "cyberattack", "ransomware", "evacuation order", "outbreak of",
]
_AI_LABS = ["anthropic", "claude", "openai", "chatgpt", "gpt-5", "gpt-6",
            "deepmind", "gemini", "meta ai", "llama", "mistral", "xai", "grok"]
_AI_ACTIONS = ["releases", "released", "launches", "launched", "announces",
               "announced", "unveils", "acquires", "acquired", "acquisition",
               "resigns", "steps down", "ousted", "breach", "hacked",
               "lawsuit", "sues", "sued", "bans", "banned", "shuts down",
               "outage", "raises $"]


def _breaking_scan(cfg):
    """Candidates from cached data only.  Never raises; returns a list of
    {class, sig, text, context}."""
    bc = cfg.get("breaking", {})
    now = time.time()
    cands = []

    # (market-shock breaking removed — the user cut stocks from the feed.)

    # 1) corroborated severe news — fresh + >=2 distinct sources per keyword,
    #    locked to the past BREAKING_MAX_H (breaking window, tighter than the 24h brief)
    try:
        matches = {}
        for sec in (_rss_data().get("sections") or []):
            for it in (sec.get("items") or []):
                ts = it.get("ts") or 0
                if not ts or now - ts > BREAKING_MAX_H * 3600:
                    continue
                hay = str(it.get("title", "")).lower()
                for kw in _BREAK_NEWS_KW:
                    if kw in hay:
                        matches.setdefault(kw, []).append((it, sec.get("name", "")))
                        break
        for kw, hits in matches.items():
            sources = {h[0].get("source") for h in hits}
            if len(sources) < 2:
                continue                     # single outlet != breaking
            hits.sort(key=lambda h: h[0].get("ts") or 0, reverse=True)
            it, secname = hits[0]
            others = sorted(s for s in sources if s != it.get("source"))
            sig = "news:%s:%s" % (kw.strip().replace(" ", "_"), _today_str(now))
            cands.append({
                "class": "news", "sig": sig,
                "text": _strip_emoji(
                    "BREAKING — %s (%d sources)\n%s (%s)%s" % (
                        secname or "News", len(sources),
                        _md_link(it.get("title", ""), it.get("url")),
                        it.get("source", ""),
                        ("\nAlso reported by " + ", ".join(others[:3])) if others else "")),
                "context": {"keyword": kw, "sources": sorted(sources),
                            "url": it.get("url")}})
    except Exception as e:
        _wt_log_err("breaking news scan: %r" % e)

    # 2) major AI-lab event — fresh intel item (breaking window), lab + action verb
    try:
        for it in (_intel_load().get("items") or []):
            ts = it.get("ts") or 0
            if not ts or now - ts > BREAKING_MAX_H * 3600:
                continue
            hay = (str(it.get("title", "")) + " " + str(it.get("summary", ""))).lower()
            if any(l in hay for l in _AI_LABS) and any(a in hay for a in _AI_ACTIONS):
                sig = "ai:" + hashlib.sha1(
                    _intel_norm_url(it.get("url")).encode("utf-8")).hexdigest()[:12]
                cands.append({
                    "class": "ai", "sig": sig,
                    "text": _strip_emoji("BREAKING — AI\n%s (%s)" % (
                        _md_link(it.get("title", ""), it.get("url")),
                        it.get("source", ""))),
                    "context": {"title": it.get("title"), "url": it.get("url"),
                                "source": it.get("source")}})
    except Exception as e:
        _wt_log_err("breaking ai scan: %r" % e)
    return cands


def _breaking_pass(cfg, state, now_ts):
    """Gate + deliver + log breaking candidates.  Runs every loop pass; the
    signature store makes re-scans of the same story silent (no log flood)."""
    bc = cfg.get("breaking", {})
    if not _master_on(cfg, "news") or not bc.get("enabled", True):
        return
    cands = _breaking_scan(cfg)
    if not cands:
        return
    today = _today_str(now_ts)
    br = dict(state.get("breaking") or {})
    if br.get("date") != today:
        br = {"date": today, "sent": 0,
              "sigs": br.get("sigs") or {}, "last_class": br.get("last_class") or {}}
    br["sigs"] = {k: v for k, v in (br.get("sigs") or {}).items()
                  if now_ts - v < 48 * 3600}
    changed = False
    for c in cands:
        if c["sig"] in br["sigs"]:
            continue                        # already alerted/handled — silent
        cd = int(bc.get("cooldown_min", 90)) * 60
        if now_ts - (br.get("last_class", {}).get(c["class"]) or 0) < cd:
            continue                        # class cooling down — retry later
        row = {"ts": now_ts, "rule_id": "", "type": "breaking_" + c["class"],
               "label": "Breaking · " + c["class"], "signature": c["sig"],
               "context": c.get("context") or {}, "channels": ["telegram", "hub"],
               "delivered": [], "suppressed": "", "reaction": ""}
        if (not bc.get("override_quiet", True)) and \
                _in_quiet(now_ts, cfg.get("quiet_hours", {})):
            row["suppressed"] = "quiet_hours"
            br["sigs"][c["sig"]] = now_ts
            changed = True
            _wt_log_append(row)
            continue
        if int(br.get("sent", 0)) >= int(bc.get("daily_cap", 5)):
            row["suppressed"] = "daily_cap"
            br["sigs"][c["sig"]] = now_ts
            changed = True
            _wt_log_append(row)
            continue
        ok, det = _wt_send_telegram(c["text"])
        br.setdefault("last_class", {})[c["class"]] = now_ts   # even on failure:
        changed = True                                         # 90-min retry space
        if ok:
            row["delivered"] = ["telegram", "hub"]
            row["text"] = c["text"]
            br["sigs"][c["sig"]] = now_ts
            br["sent"] = int(br.get("sent", 0)) + 1
        else:
            row["suppressed"] = "deliver_failed"
            row["detail"] = det
        _wt_log_append(row)
    if changed:
        def _mut(s):
            s["breaking"] = br
        _wt_save_state(_mut)
        state["breaking"] = br


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
    if not _master_on(cfg, "briefings") or not br.get("enabled", True):
        return
    # respect quiet hours: hold the brief until quiet ends (the date guard only
    # flips after a compose, so it fires on the first pass past the quiet edge)
    if _in_quiet(now_ts, cfg.get("quiet_hours", {})):
        return
    lt = time.localtime(now_ts)
    today = _today_str(now_ts)
    at_or_past = (lt.tm_hour > br.get("hour", 8) or
                  (lt.tm_hour == br.get("hour", 8) and lt.tm_min >= br.get("minute", 0)))
    if not at_or_past or state.get("last_brief_date") == today:
        return
    if _slept_through(lt.tm_hour * 60 + lt.tm_min,
                      br.get("hour", 8) * 60 + br.get("minute", 0), 18 * 60):
        # woke long past the morning window — the widget refreshes on its own
        # loop and the evening wrap covers the day, so a 9pm "8am World Brief"
        # push is noise. Mark the day done without sending.
        _wt_save_state(lambda s: s.__setitem__("last_brief_date", today))
        state["last_brief_date"] = today
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
    news_on = _master_on(cfg, "news")
    for rule in cfg.get("rules", []):
        if not rule.get("enabled", True):
            continue
        if rule.get("type") == "rss_keyword" and not news_on:
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
            _midday_tick(cfg, state, now_ts)
            _evening_tick(cfg, state, now_ts)
            _breaking_pass(cfg, state, now_ts)
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
    kind = ctx.q1("kind", "morning")
    if kind == "midday":
        try:
            comp = _midday_compose(_wt_load())
            return {"ok": True, "kind": "midday", "noteworthy": comp["noteworthy"],
                    "items": comp["items"], "text": comp["text"],
                    "since": comp["since"]}
        except Exception as e:
            return {"ok": False, "kind": "midday",
                    "error": type(e).__name__ + ": " + str(e)}
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
    kind = str(b.get("kind", "morning"))

    if kind == "midday":
        cfg = _wt_load()
        try:
            comp = _midday_compose(cfg)
        except Exception as e:
            return _err("compose_failed: " + str(e), 500)
        if dry_run:
            return {"ok": True, "kind": "midday", "noteworthy": comp["noteworthy"],
                    "items": comp["items"], "text": comp["text"],
                    "delivered": [], "dry_run": True}
        # manual send: the user asked, so send even below the noteworthy bar;
        # does NOT flip last_midday_date (the scheduler owns the 3pm push)
        ok, detail = _wt_send_telegram(comp["text"])
        row = {"ts": time.time(), "rule_id": "", "type": "midday_brief",
               "label": "Midday pulse (manual)", "signature": "",
               "context": {"items": comp["items"]}, "channels": ["telegram", "hub"],
               "delivered": ["telegram", "hub"] if ok else [],
               "suppressed": "" if ok else "deliver_failed", "reaction": ""}
        if ok:
            row["text"] = comp["text"]
        _wt_log_append(row)
        if not ok:
            return ({"ok": False, "error": "send_failed", "detail": detail,
                     "text": comp["text"], "dry_run": False}, 502)
        return {"ok": True, "kind": "midday", "text": comp["text"],
                "delivered": ["telegram", "hub"], "dry_run": False}

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


def breaking_preview_handler(ctx):
    """Dry-run scan: what WOULD page the phone right now, with per-candidate
    gate verdicts.  Never sends, never mutates state."""
    cfg = _wt_load()
    state = _wt_state_load()
    now_ts = time.time()
    bc = cfg.get("breaking", {})
    br = state.get("breaking") or {}
    today = _today_str(now_ts)
    sent_today = int(br.get("sent", 0)) if br.get("date") == today else 0
    out = []
    for c in _breaking_scan(cfg):
        if c["sig"] in (br.get("sigs") or {}):
            verdict = "already_alerted"
        elif now_ts - ((br.get("last_class") or {}).get(c["class"]) or 0) \
                < int(bc.get("cooldown_min", 90)) * 60:
            verdict = "class_cooldown"
        elif (not bc.get("override_quiet", True)) and \
                _in_quiet(now_ts, cfg.get("quiet_hours", {})):
            verdict = "quiet_hours"
        elif sent_today >= int(bc.get("daily_cap", 5)):
            verdict = "daily_cap"
        else:
            verdict = "would_push"
        out.append({"class": c["class"], "sig": c["sig"], "verdict": verdict,
                    "text": c["text"]})
    return {"ok": True, "candidates": out, "sent_today": sent_today,
            "config": bc}


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
                "midday": cfg["midday"], "evening": cfg["evening"],
                "breaking": cfg["breaking"], "master": cfg["master"],
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


def _op_set_midday(b):
    def _mut(d):
        md = dict(d.get("midday", {}))
        if "enabled" in b:
            md["enabled"] = bool(b["enabled"])
        if "hour" in b:
            md["hour"] = _clamp_int(b["hour"], 11, 17, md.get("hour", 15))
        if "minute" in b:
            md["minute"] = _clamp_int(b["minute"], 0, 59, md.get("minute", 0))
        if "min_items" in b:
            md["min_items"] = _clamp_int(b["min_items"], 1, 6, md.get("min_items", 2))
        if "mover_pct" in b:
            v = _clamp_float(b["mover_pct"], 0.5, 10.0)
            if v is not None:
                md["mover_pct"] = v
        if "channels" in b:
            md["channels"] = _valid_channels(b["channels"], ["telegram", "hub"])
        d["midday"] = md
    d = _wt_save_config(_mut)
    return {"ok": True, "midday": d["midday"]}


def _op_set_evening(b):
    def _mut(d):
        ev = dict(d.get("evening", {}))
        if "enabled" in b:
            ev["enabled"] = bool(b["enabled"])
        if "hour" in b:
            ev["hour"] = _clamp_int(b["hour"], 16, 23, ev.get("hour", 18))
        if "minute" in b:
            ev["minute"] = _clamp_int(b["minute"], 0, 59, ev.get("minute", 0))
        if "min_items" in b:
            ev["min_items"] = _clamp_int(b["min_items"], 1, 6, ev.get("min_items", 1))
        if "channels" in b:
            ev["channels"] = _valid_channels(b["channels"], ["telegram", "hub"])
        d["evening"] = ev
    d = _wt_save_config(_mut)
    return {"ok": True, "evening": d["evening"]}


def _op_set_master(b):
    def _mut(d):
        m = dict(d.get("master", {}))
        if "briefings" in b:
            m["briefings"] = bool(b["briefings"])
        if "news" in b:
            m["news"] = bool(b["news"])
        d["master"] = m
    d = _wt_save_config(_mut)
    return {"ok": True, "master": d["master"]}


def _op_set_breaking(b):
    def _mut(d):
        bk = dict(d.get("breaking", {}))
        if "enabled" in b:
            bk["enabled"] = bool(b["enabled"])
        if "override_quiet" in b:
            bk["override_quiet"] = bool(b["override_quiet"])
        if "daily_cap" in b:
            bk["daily_cap"] = _clamp_int(b["daily_cap"], 1, 10, bk.get("daily_cap", 5))
        if "index_pct" in b:
            v = _clamp_float(b["index_pct"], 1.0, 10.0)
            if v is not None:
                bk["index_pct"] = v
        if "ticker_pct" in b:
            v = _clamp_float(b["ticker_pct"], 3.0, 20.0)
            if v is not None:
                bk["ticker_pct"] = v
        if "cooldown_min" in b:
            bk["cooldown_min"] = _clamp_int(b["cooldown_min"], 15, 360,
                                            bk.get("cooldown_min", 90))
        d["breaking"] = bk
    d = _wt_save_config(_mut)
    return {"ok": True, "breaking": d["breaking"]}


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
    "set_brief": _op_set_brief, "set_midday": _op_set_midday,
    "set_evening": _op_set_evening, "set_master": _op_set_master,
    "set_breaking": _op_set_breaking, "mark_reaction": _op_mark_reaction,
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


def intel_handler(ctx):
    """Visibility into the hourly AI/social research store (also a manual poke)."""
    if (ctx.q1("gather", "") or "") == "1":
        try:
            threading.Thread(target=_intel_gather, daemon=True).start()
        except Exception:
            pass
        return {"ok": True, "gathering": True}
    store = _intel_load()
    return {"ok": True, "updated": store.get("updated"),
            "feeds": store.get("feeds"), "web_search_available": _intel_web_ok(),
            "count": len(store.get("items") or []),
            "curated": store.get("curated") or [],
            "items": (store.get("items") or [])[:40]}


# ==========================================================================
# WIRING — rebind, routes, guarded threads (mirror aux_recorder's pattern)
# ==========================================================================
globals()["_generate_briefing"] = _wt_generate_briefing
globals()["_briefing_payload"] = _wt_briefing_payload

def evening_preview_handler(ctx):
    return _evening_compose(_wt_load())


register_get("/api/brief/preview", brief_preview_handler)
register_get("/api/evening/preview", evening_preview_handler)
register_post("/api/brief/send", brief_send_handler)
register_get("/api/watchtower", watchtower_get_handler)
register_post("/api/watchtower", watchtower_post_handler)
register_get("/api/watchtower/feed", watchtower_feed_handler)
register_get("/api/watchtower/breaking", breaking_preview_handler)
register_get("/api/intel", intel_handler)

if not globals().get("_watchtower_thread_started"):
    globals()["_watchtower_thread_started"] = True
    try:
        threading.Thread(target=watchtower_loop, daemon=True).start()
    except Exception as _e:            # pragma: no cover
        _wt_log_err("thread failed to start: %r" % _e)

if not globals().get("_intel_thread_started"):
    globals()["_intel_thread_started"] = True
    try:
        threading.Thread(target=intel_loop, daemon=True).start()
    except Exception as _e:            # pragma: no cover
        _wt_log_err("intel thread failed to start: %r" % _e)


# ==========================================================================
# TODO (Tier 3 — DEFERRED, not built here): prettier "blurb" formatting of the
# outgoing messages. The user said this is not paramount and asked to delegate
# it for later. Scope for that pass: richer per-item blurbs (a headline + a
# 1-2 line synthesized summary instead of a bare title), section dividers/emphasis
# that survive both MarkdownV2 and the hub's renderMd(), and optional grouping of
# AI & Labs by lab. Keep the link-on-every-item + zero-emoji + 12-hour invariants.
# ==========================================================================
