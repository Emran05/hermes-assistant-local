#!/usr/bin/env python3
"""
Hermes Assistant dashboard — local backend (Python stdlib only, no deps).

Serves index.html and a JSON API that shells out to the Hermes agent
(`hermes -z`, the full agent with tools/skills). Designed to run forever as a
launchd service. All data stays on this machine.

Data lives in ~/.hermes/dashboard/:
  briefing.json     cached briefing (page loads are instant; background refresh)
  access.json       folders the user has granted the assistant
  chats/<id>.json   chat transcripts (history survives reloads/restarts)
  inbox/            files dropped into the chat UI

API:
  GET  /                    UI
  GET  /api/health          model/hermes reachability
  GET  /api/status          disk, RAM, model, uptime
  GET  /api/briefing        cached briefing (+generating flag)
  POST /api/briefing/refresh  regenerate in background
  GET  /api/actions         quick-action templates
  GET  /api/access          granted folders
  POST /api/access          {op: add|remove, path}
  GET  /api/sessions        chat list (id, title, updated, pinned)
  GET  /api/history?session=ID
  POST /api/sessions/delete {session}
  (aux_convos.py adds /api/sessions/search, /api/sessions/meta and
   /api/sessions/export — conversation management)
  POST /api/chat            {message, session, attachments?}
  POST /api/upload          raw body + X-Filename header -> saved to inbox
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
DATA = os.path.join(HOME, ".hermes", "dashboard")
CHATS = os.path.join(DATA, "chats")
INBOX = os.path.join(DATA, "inbox")
for d in (DATA, CHATS, INBOX):
    os.makedirs(d, exist_ok=True)

DASH_HOST = os.environ.get("DASH_HOST", "127.0.0.1")
DASH_PORT = int(os.environ.get("DASH_PORT", "7788"))
MODEL_URL = os.environ.get("MODEL_URL", "http://127.0.0.1:8080/v1/models")
# Background lane — a second, always-on SMALL model (com.hermes.mlx-bg, :8081,
# mlx-server-bg.sh) that all non-interactive producers use (briefing, watchtower
# intel/news, For-You candidates…) so the primary model stays warm for the user.
# bg_lane() falls back to the primary when :8081 is down. Model id lives in
# ~/.hermes/dashboard/bg-model (default Qwen3.5-9B — same family/template as
# Qwen3.8; the only official Qwen3.8 sizes are 27B and a 2.4T MoE).
BG_MODEL_URL = os.environ.get("BG_MODEL_URL", "http://127.0.0.1:8081/v1/models")
DEFAULT_BG_MODEL = "mlx-community/Qwen3.5-9B-4bit"
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "600"))
BRIEFING_REFRESH_MIN = int(os.environ.get("BRIEFING_REFRESH_MIN", "30"))

HERMES = shutil.which("hermes") or os.path.join(HOME, ".local", "bin", "hermes")
STARTED = time.time()

_agent_lock = threading.Lock()   # one model call at a time
_state_lock = threading.Lock()   # briefing cache / json file writes
_briefing_generating = False


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# capabilities — the "gets better over time" surface: skills the agent has
# learned, what it remembers, and how much it's been used. All read straight
# off Hermes's own state (skills dir, USER.md, state.db) so it reflects the
# real agent, not a copy.
# --------------------------------------------------------------------------

SKILLS_DIR = os.path.join(HOME, ".hermes", "skills")
USER_MEM = os.path.join(HOME, ".hermes", "memories", "USER.md")
STATE_DB = os.path.join(HOME, ".hermes", "state.db")


def _skill_meta(path):
    """Pull name + description from a SKILL.md YAML-ish frontmatter block."""
    name = desc = ""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4000)
    except OSError:
        return None
    if head.startswith("---"):
        block = head.split("---", 2)
        fm = block[1] if len(block) >= 3 else ""
        for line in fm.splitlines():
            if line.startswith("name:") and not name:
                name = line.split(":", 1)[1].strip().strip('"\'')
            elif line.startswith("description:") and not desc:
                desc = line.split(":", 1)[1].strip().strip('"\'')
    return name, desc


def scan_skills():
    """Every learned skill (one SKILL.md each), grouped by category folder."""
    items, cats = [], {}
    if os.path.isdir(SKILLS_DIR):
        for dirpath, _, files in os.walk(SKILLS_DIR):
            if "SKILL.md" not in files:
                continue
            rel = os.path.relpath(dirpath, SKILLS_DIR)
            category = rel.split(os.sep)[0] if rel != "." else "general"
            meta = _skill_meta(os.path.join(dirpath, "SKILL.md"))
            if not meta:
                continue
            name, desc = meta
            slug = os.path.basename(dirpath)
            items.append({"name": name or slug, "slug": slug,
                          "category": category, "desc": desc})
            cats[category] = cats.get(category, 0) + 1
    items.sort(key=lambda s: (s["category"], s["name"].lower()))
    categories = sorted(({"name": k, "count": v} for k, v in cats.items()),
                        key=lambda c: -c["count"])
    return {"total": len(items), "categories": categories, "items": items}


def read_memory():
    """Facts the agent has stored about the user (built-in USER.md memory)."""
    facts, updated = [], None
    try:
        updated = os.path.getmtime(USER_MEM)
        with open(USER_MEM, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or s.startswith("<!--"):
                    continue
                s = s.lstrip("-*• ").strip()
                if s:
                    facts.append(s)
    except OSError:
        pass
    return {"facts": facts, "count": len(facts), "updated": updated}


def compute_insights():
    """Usage totals + a 14-day activity series, read-only from state.db."""
    import sqlite3
    out = {"sessions": 0, "messages": 0, "tool_calls": 0,
           "input_tokens": 0, "output_tokens": 0, "platforms": [],
           "by_day": [], "first_seen": None}
    if not os.path.exists(STATE_DB):
        return out
    uri = "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error:
        return out
    try:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        t = cur.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(message_count),0) m, "
            "COALESCE(SUM(tool_call_count),0) tc, "
            "COALESCE(SUM(input_tokens),0) it, COALESCE(SUM(output_tokens),0) ot, "
            "MIN(started_at) first FROM sessions").fetchone()
        out.update(sessions=t["n"], messages=t["m"], tool_calls=t["tc"],
                   input_tokens=t["it"], output_tokens=t["ot"],
                   first_seen=t["first"])
        for r in cur.execute(
                "SELECT source, COUNT(*) n, COALESCE(SUM(message_count),0) m "
                "FROM sessions GROUP BY source ORDER BY n DESC").fetchall():
            out["platforms"].append({"name": r["source"] or "unknown",
                                     "sessions": r["n"], "messages": r["m"]})
        starts = [row["started_at"] for row in
                  cur.execute("SELECT started_at FROM sessions "
                              "WHERE started_at IS NOT NULL").fetchall()]
    finally:
        con.close()
    # bucket into local calendar days: index 0 = today, 13 = 13 days ago
    today = time.localtime()
    midnight = time.mktime((today.tm_year, today.tm_mon, today.tm_mday,
                            0, 0, 0, 0, 0, -1))
    buckets = {}
    for ts in starts:
        days_ago = int(round((midnight - _day_floor(ts)) / 86400))
        if 0 <= days_ago <= 13:
            buckets[days_ago] = buckets.get(days_ago, 0) + 1
    out["by_day"] = [{"d": back, "n": buckets.get(back, 0)}
                     for back in range(13, -1, -1)]
    return out


def _day_floor(ts):
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


# --------------------------------------------------------------------------
# widget "expand" — rich, in-depth data for the pop-out detail view. Each
# returns MORE than the compact widget (forecasts, deep system info, etc.).
# --------------------------------------------------------------------------

def expand_weather():
    s = get_settings()
    lat, lon = s.get("weather_lat"), s.get("weather_lon")
    if lat is None or lon is None:
        weather()  # geocodes + caches lat/lon
        s = get_settings()
        lat, lon = s.get("weather_lat"), s.get("weather_lon")
    if lat is None or lon is None:
        return {"error": "Set a city in the Weather widget first."}

    def fetch():
        j = _http_json(
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,weather_code,wind_speed_10m,wind_direction_10m,"
            "pressure_msl,uv_index,is_day,cloud_cover"
            "&hourly=temperature_2m,precipitation_probability"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,sunrise,sunset,uv_index_max,wind_speed_10m_max"
            "&timezone=auto&forecast_days=7&temperature_unit=fahrenheit"
            "&wind_speed_unit=mph", timeout=8)
        cur = j.get("current", {})
        H = j.get("hourly", {})
        D = j.get("daily", {})
        now_iso = cur.get("time", "")
        htimes = H.get("time", [])
        try:
            start = htimes.index(now_iso) if now_iso in htimes else 0
        except ValueError:
            start = 0
        hourly = []
        for i in range(start, min(start + 24, len(htimes))):
            hh = int(htimes[i][11:13])
            hourly.append({"t": "%d%s" % ((hh % 12) or 12, "a" if hh < 12 else "p"),
                           "temp": round(H["temperature_2m"][i]),
                           "pop": H.get("precipitation_probability", [0] * len(htimes))[i]})
        days = []
        for i in range(len(D.get("time", []))):
            days.append({"date": D["time"][i], "code": D["weather_code"][i],
                         "hi": round(D["temperature_2m_max"][i]),
                         "lo": round(D["temperature_2m_min"][i]),
                         "pop": D["precipitation_probability_max"][i],
                         "sunrise": (D["sunrise"][i][11:16]),
                         "sunset": (D["sunset"][i][11:16]),
                         "uv": round(D["uv_index_max"][i] or 0)})
        wdir = cur.get("wind_direction_10m", 0)
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return {"city": s.get("weather_city"), "lat": lat, "lon": lon,
                "current": {
                    "temp": round(cur.get("temperature_2m", 0)),
                    "feels": round(cur.get("apparent_temperature", 0)),
                    "desc": WMO.get(cur.get("weather_code"), "—"),
                    "code": cur.get("weather_code"),
                    "is_day": bool(cur.get("is_day")),
                    "humidity": cur.get("relative_humidity_2m"),
                    "wind": round(cur.get("wind_speed_10m", 0)),
                    "wind_dir": dirs[int((wdir + 22.5) % 360 // 45)],
                    "pressure": round(cur.get("pressure_msl", 0)),
                    "uv": round(cur.get("uv_index", 0) or 0),
                    "cloud": cur.get("cloud_cover"),
                    "precip": cur.get("precipitation")},
                "hourly": hourly, "daily": days}
    try:
        return _cached(f"wx_exp:{lat},{lon}", 1200, fetch)
    except Exception as e:
        return {"error": "weather fetch failed (" + type(e).__name__ + ")"}


def expand_system():
    def ps(sort_flag):
        try:
            out = subprocess.run(["ps", "-Aceo", "pid,pcpu,pmem,comm", sort_flag],
                                 capture_output=True, text=True, timeout=4).stdout
        except Exception:
            return []
        rows = []
        for ln in out.splitlines()[1:7]:
            p = ln.split(None, 3)
            if len(p) == 4:
                rows.append({"pid": p[0], "cpu": float(p[1]), "mem": float(p[2]),
                             "name": p[3][:28]})
        return rows
    mem = {}
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
        pg = 16384
        st = dict(re.findall(r'^"?([\w -]+)"?:\s+(\d+)', vm, re.M))
        g = lambda k: int(st.get(k, 0)) * pg / 1e9
        total = int(subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"],
                                   capture_output=True, text=True, timeout=3).stdout) / 1e9
        mem = {"total": round(total), "wired": round(g("Pages wired down"), 1),
               "active": round(g("Pages active"), 1),
               "compressed": round(g("Pages occupied by compressor"), 1),
               "free": round(g("Pages free") + g("Pages inactive"), 1)}
    except Exception:
        pass
    net = {}
    try:
        out = subprocess.run(["netstat", "-ib"], capture_output=True, text=True,
                             timeout=4).stdout
        ib = ob = 0
        seen = set()
        for ln in out.splitlines()[1:]:
            f = ln.split()
            if len(f) >= 10 and f[0].startswith(("en", "en0")) and f[0] not in seen:
                seen.add(f[0])
                try:
                    ib += int(f[6]); ob += int(f[9])
                except (ValueError, IndexError):
                    pass
        net = {"in_gb": round(ib / 1e9, 2), "out_gb": round(ob / 1e9, 2)}
    except Exception:
        pass
    disks = []
    for label, path in (("Macintosh HD", "/"), ("Data", "/System/Volumes/Data")):
        try:
            du = shutil.disk_usage(path)
            disks.append({"name": label, "used_gb": round(du.used / 1e9),
                          "total_gb": round(du.total / 1e9),
                          "pct": round(100 * du.used / du.total)})
        except Exception:
            pass
    try:
        la = os.getloadavg()
        load = [round(x, 2) for x in la]
    except OSError:
        load = []
    boot = None
    try:
        bt = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"],
                            capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"sec = (\d+)", bt)
        if m:
            boot = int(m.group(1))
    except Exception:
        pass
    return {"cpu_top": ps("-r"), "mem_top": ps("-m"), "mem": mem, "net": net,
            "disks": disks, "load": load, "cores": os.cpu_count(),
            "uptime_hr": round((time.time() - boot) / 3600, 1) if boot else None,
            "sys": system_status()}


def expand_markets():
    syms = get_settings().get("tickers") or ["SPY", "AAPL", "NVDA", "MSFT"]

    def one(sym):
        j = _http_json("https://query1.finance.yahoo.com/v8/finance/chart/"
                       + urllib.parse.quote(sym) + "?range=5d&interval=30m", timeout=7)
        res = j["chart"]["result"][0]
        m = res["meta"]
        closes = [c for c in (res.get("indicators", {}).get("quote", [{}])[0]
                              .get("close") or []) if c is not None]
        price = m.get("regularMarketPrice")
        prev = m.get("previousClose") or m.get("chartPreviousClose") or price
        chg = (price - prev) if (price and prev) else 0
        return {"symbol": sym, "name": m.get("shortName") or sym, "price": price,
                "chg": round(chg, 2), "pct": round(chg / prev * 100, 2) if prev else 0,
                "day_hi": m.get("regularMarketDayHigh"), "day_lo": m.get("regularMarketDayLow"),
                "wk_hi": m.get("fiftyTwoWeekHigh"), "wk_lo": m.get("fiftyTwoWeekLow"),
                "prev": prev, "spark": ([prev] + closes)[-80:],
                "exch": m.get("exchangeName"), "asof": m.get("regularMarketTime")}

    def fetch():
        out = []
        for sym in syms[:8]:
            try:
                out.append(one(sym))
            except Exception:
                out.append({"symbol": sym, "error": True})
        return {"quotes": out}
    return _cached("mkt_exp:" + ",".join(syms), 300, fetch)


def expand_crypto():
    coins = get_settings().get("coins") or ["bitcoin", "ethereum", "solana"]

    def fetch():
        j = _http_json("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids="
                       + ",".join(coins) + "&price_change_percentage=1h,24h,7d", timeout=8)
        out = []
        for c in j:
            out.append({"id": c.get("id"), "name": c.get("name"),
                        "symbol": (c.get("symbol") or "").upper(),
                        "price": c.get("current_price"),
                        "pct1h": round(c.get("price_change_percentage_1h_in_currency") or 0, 2),
                        "pct24h": round(c.get("price_change_percentage_24h_in_currency") or 0, 2),
                        "pct7d": round(c.get("price_change_percentage_7d_in_currency") or 0, 2),
                        "mcap": c.get("market_cap"), "vol": c.get("total_volume"),
                        "hi24": c.get("high_24h"), "lo24": c.get("low_24h"),
                        "spark": (c.get("sparkline_in_7d") or {}).get("price")
                        or ([c.get("current_price")] if c.get("current_price") else [])})
        return {"coins": out}
    return _cached("crypto_exp:" + ",".join(coins), 300, fetch)


def _strip_html(s, limit=None):
    """HTML/entity string -> clean text snippet (stdlib only)."""
    import html
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?is)<br\s*/?>", " ", s)
    s = re.sub(r"(?is)</(p|div|li|h[1-6])>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip() + "…"
    return s


def expand_hackernews():
    HN = "https://hacker-news.firebaseio.com/v0"

    def fetch():
        try:
            ids = _http_json(f"{HN}/topstories.json", timeout=6) or []
        except Exception as e:
            return {"error": "Hacker News unreachable (" + type(e).__name__ + ")",
                    "stories": [], "top_comment": None}
        stories, first_it = [], None
        for rank, i in enumerate(ids[:15], 1):
            try:
                it = _http_json(f"{HN}/item/{i}.json", timeout=6)
            except Exception:
                continue
            if not it:
                continue
            if rank == 1:
                first_it = it
            hn = f"https://news.ycombinator.com/item?id={i}"
            url = it.get("url") or hn
            try:
                dom = urllib.parse.urlparse(url).netloc.replace("www.", "")
            except Exception:
                dom = ""
            stories.append({"rank": rank, "title": it.get("title", ""), "url": url,
                            "hn_url": hn, "domain": dom, "score": it.get("score", 0),
                            "by": it.get("by", ""), "comments": it.get("descendants", 0),
                            "time": it.get("time")})
        top_comment = None
        try:
            kids = (first_it or {}).get("kids") or []
            if kids:
                c = _http_json(f"{HN}/item/{kids[0]}.json", timeout=6)
                if c and not c.get("deleted") and not c.get("dead"):
                    txt = _strip_html(c.get("text", ""), 320)
                    if txt:
                        top_comment = {"by": c.get("by", ""), "text": txt}
        except Exception:
            pass
        return {"stories": stories, "top_comment": top_comment}
    return _cached("hn_exp", 600, fetch)


def expand_github():
    def fetch():
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
        try:
            j = _http_json("https://api.github.com/search/repositories?q=created:>"
                           + since + "&sort=stars&order=desc&per_page=15", timeout=8)
        except Exception as e:
            return {"error": "GitHub unreachable (" + type(e).__name__ + ")",
                    "repos": [], "since": since}
        import calendar
        repos = []
        for r in j.get("items", [])[:15]:
            stars = r.get("stargazers_count", 0)
            vel = None
            try:
                ct = calendar.timegm(time.strptime(r.get("created_at", ""),
                                                   "%Y-%m-%dT%H:%M:%SZ"))
                vel = round(stars / max(1.0, (time.time() - ct) / 86400.0))
            except Exception:
                pass
            repos.append({"name": r.get("full_name", ""), "url": r.get("html_url", ""),
                          "desc": _strip_html(r.get("description") or "", 160),
                          "stars": stars, "lang": r.get("language") or "",
                          "forks": r.get("forks_count", 0),
                          "issues": r.get("open_issues_count", 0), "vel": vel,
                          "topics": (r.get("topics") or [])[:5]})
        return {"repos": repos, "since": since}
    return _cached("gh_exp", 1800, fetch)


def expand_rss():
    feeds = get_settings().get("rss_feeds") or [
        "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "https://www.theverge.com/rss/index.xml",
        "https://techcrunch.com/feed/"]

    def parse_date(s):
        if not s:
            return None
        s = s.strip()
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(s)
            if dt:
                return dt.timestamp()
        except Exception:
            pass
        try:
            import datetime
            return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    def fetch():
        import xml.etree.ElementTree as ET

        def parse_feed(url):
            try:
                root = ET.fromstring(_http_text(url, timeout=7))
            except Exception:
                return []
            try:
                dom = urllib.parse.urlparse(url).netloc.replace("www.", "").replace("feeds.", "")
            except Exception:
                dom = url
            chan = None
            for el in root.iter():
                if el.tag.split("}")[-1] == "title" and (el.text or "").strip():
                    chan = el.text.strip()[:40]
                    break
            out = []
            for el in root.iter():
                if el.tag.split("}")[-1] not in ("item", "entry"):
                    continue
                title = link = desc = date_raw = ""
                for c in el:
                    ct = c.tag.split("}")[-1]
                    if ct == "title" and not title:
                        title = (c.text or "").strip()
                    elif ct == "link":
                        href = (c.get("href") or c.text or "").strip()
                        if href and (not link or c.get("rel") in (None, "", "alternate")):
                            link = href
                    elif ct in ("description", "summary", "encoded", "content") and not desc:
                        raw = c.text or ""
                        if not raw and len(c):
                            raw = "".join(ET.tostring(x, encoding="unicode") for x in c)
                        desc = raw
                    elif ct in ("pubDate", "published", "updated", "date") and not date_raw:
                        date_raw = (c.text or "").strip()
                if not title:
                    continue
                out.append({"title": _strip_html(title, 160), "url": link,
                            "source": chan or dom, "summary": _strip_html(desc, 180),
                            "ts": parse_date(date_raw)})
                if len(out) >= 8:
                    break
            return out

        buckets = [parse_feed(u) for u in feeds[:6]]
        items, col = [], 0
        while len(items) < 15 and any(col < len(b) for b in buckets):
            for b in buckets:
                if col < len(b):
                    items.append(b[col])
                    if len(items) >= 15:
                        break
            col += 1
        items.sort(key=lambda x: (x["ts"] or 0), reverse=True)
        return {"items": items}
    return _cached("rss_exp:" + "|".join(feeds), 900, fetch)


def _short_model(m):
    if not m:
        return ""
    return m.split("/")[-1].replace("-4bit", "").replace("-Instruct", "")


def expand_agent_pulse():
    import sqlite3
    if not os.path.exists(STATE_DB):
        return {"available": False, "reason": "No agent state database yet."}

    def fetch():
        uri = "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.Error:
            return {"available": False, "reason": "Can't open agent state database."}
        try:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            lt = time.localtime()
            midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
            t = cur.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(tool_call_count),0) tc, "
                "COALESCE(SUM(input_tokens),0) it, COALESCE(SUM(output_tokens),0) ot "
                "FROM sessions WHERE started_at >= ?", (midnight,)).fetchone()
            today = {"sessions": t["n"], "tool_calls": t["tc"],
                     "tokens": (t["it"] or 0) + (t["ot"] or 0)}
            g = cur.execute("SELECT COUNT(*) n, COALESCE(SUM(tool_call_count),0) tc, "
                            "COALESCE(SUM(input_tokens),0) it, COALESCE(SUM(output_tokens),0) ot "
                            "FROM sessions").fetchone()
            totals = {"sessions": g["n"], "tool_calls": g["tc"],
                      "tokens": (g["it"] or 0) + (g["ot"] or 0)}
            plat = {}
            for r in cur.execute("SELECT source, COUNT(*) n FROM sessions GROUP BY source").fetchall():
                name = r["source"] or "cli"
                plat.setdefault(name, {"name": name, "sessions": 0, "today": 0})["sessions"] = r["n"]
            for r in cur.execute("SELECT source, COUNT(*) n FROM sessions WHERE started_at >= ? GROUP BY source", (midnight,)).fetchall():
                name = r["source"] or "cli"
                plat.setdefault(name, {"name": name, "sessions": 0, "today": 0})["today"] = r["n"]
            platforms = sorted(plat.values(), key=lambda p: -p["sessions"])
            tool_counts = {}
            for r in cur.execute("SELECT tool_calls, tool_name FROM messages "
                                 "WHERE tool_calls IS NOT NULL OR tool_name IS NOT NULL "
                                 "ORDER BY timestamp DESC LIMIT 200").fetchall():
                if r["tool_calls"]:
                    try:
                        calls = json.loads(r["tool_calls"])
                    except Exception:
                        calls = []
                    for c in (calls if isinstance(calls, list) else []):
                        name = (c.get("function") or {}).get("name") or c.get("name")
                        if name:
                            tool_counts[name] = tool_counts.get(name, 0) + 1
                elif r["tool_name"]:
                    tool_counts[r["tool_name"]] = tool_counts.get(r["tool_name"], 0) + 1
            top_tools = [{"name": k, "count": v} for k, v in
                         sorted(tool_counts.items(), key=lambda kv: -kv[1])][:8]
            sessions = []
            for r in cur.execute(
                    "SELECT id, source, model, message_count, tool_call_count, input_tokens, "
                    "output_tokens, started_at, ended_at, title FROM sessions "
                    "WHERE started_at IS NOT NULL ORDER BY started_at DESC LIMIT 20").fetchall():
                dur = max(0, int(r["ended_at"] - r["started_at"])) if (r["ended_at"] and r["started_at"]) else None
                sessions.append({"source": r["source"] or "cli", "model": _short_model(r["model"]),
                                 "title": (r["title"] or "").strip()[:60],
                                 "msgs": r["message_count"] or 0, "tools": r["tool_call_count"] or 0,
                                 "tokens": (r["input_tokens"] or 0) + (r["output_tokens"] or 0),
                                 "ts": r["started_at"], "dur": dur})
        finally:
            con.close()
        return {"available": True, "today": today, "totals": totals,
                "platforms": platforms, "top_tools": top_tools, "sessions": sessions}
    return _cached("pulse_exp", 30, fetch)


def expand_today():
    def fetch():
        bud = shutil.which("icalBuddy") or "/opt/homebrew/bin/icalBuddy"
        if not os.path.exists(bud):
            return {"available": False, "reason": "icalBuddy not installed"}
        try:
            proc = subprocess.run(
                [bud, "-npn", "-nc", "-nrd", "-b", "", "-iep", "datetime,title",
                 "-po", "datetime,title", "-df", "%Y-%m-%d", "-tf", "%-I:%M %p",
                 "-ps", "| ~ |", "eventsToday+7"],
                capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            return {"available": False, "reason": "calendar read timed out"}
        if proc.returncode != 0:
            err = (proc.stderr or "icalBuddy failed").strip()
            if "No calendars" in err or "access" in err.lower():
                return {"available": False, "reason":
                        "macOS Calendar isn't readable yet — run `icalBuddy calendars` once in Terminal and allow the prompt."}
            return {"available": False, "reason": err[:140]}
        by_date, order, seen = {}, [], set()
        cur_ev = None
        for raw in proc.stdout.splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue
            if " ~ " in line:
                dt, title = line.split(" ~ ", 1)
                dt, title = dt.strip(), title.strip()
                dm = re.search(r"\d{4}-\d{2}-\d{2}", dt)
                if not dm:
                    continue
                date = dm.group(0)
                times = re.findall(r"\d{1,2}:\d{2}\s*[AaPp][Mm]", dt)
                ev = {"time": times[0] if times else "", "end": times[1] if len(times) > 1 else "",
                      "title": title, "all_day": len(times) == 0}
                key = (date, ev["time"], title)
                if key in seen:
                    cur_ev = None
                    continue
                seen.add(key)
                if date not in by_date:
                    by_date[date] = []
                    order.append(date)
                by_date[date].append(ev)
                cur_ev = ev
            elif cur_ev is not None:
                cur_ev["title"] = (cur_ev["title"] + " " + line.strip())[:140]
        total = sum(len(by_date[d]) for d in order)
        days = []
        for date in order:
            days.append({"date": date, "events": by_date[date]})
        return {"available": True, "days": days, "count": total}
    return _cached("today_exp", 300, fetch)


def _tz_offset_str(dt):
    off = dt.utcoffset()
    if off is None:
        return "UTC"
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    mins = abs(mins)
    h, m = divmod(mins, 60)
    return "UTC%s%d%s" % (sign, h, (":%02d" % m) if m else "")


def expand_worldclock():
    zones = get_settings().get("timezones") or [
        ["San Francisco", "America/Los_Angeles"], ["New York", "America/New_York"],
        ["London", "Europe/London"], ["Tokyo", "Asia/Tokyo"]]

    def fetch():
        import datetime
        try:
            from zoneinfo import ZoneInfo
        except Exception:
            return {"available": False, "reason": "zoneinfo unavailable"}
        local = datetime.datetime.now().astimezone()
        local_off = local.utcoffset()
        loc = {"label": "Local", "time": local.strftime("%-I:%M %p"),
               "date": local.strftime("%a, %b %-d"), "weekday": local.strftime("%A"),
               "offset": _tz_offset_str(local), "is_day": 6 <= local.hour < 19}
        out = []
        for entry in zones[:8]:
            try:
                now = datetime.datetime.now(ZoneInfo(entry[1]))
            except Exception:
                continue
            diff = ""
            if local_off is not None and now.utcoffset() is not None:
                dh = (now.utcoffset() - local_off).total_seconds() / 3600.0
                diff = "same as local" if dh == 0 else ("%+g h" % dh)
            out.append({"label": entry[0], "time": now.strftime("%-I:%M %p"),
                        "date": now.strftime("%a, %b %-d"), "weekday": now.strftime("%A"),
                        "offset": _tz_offset_str(now), "is_day": 6 <= now.hour < 19, "diff": diff})
        return {"available": True, "local": loc, "zones": out}
    return _cached("worldclock_exp", 15, fetch)


EXPANDERS = {"weather": expand_weather, "system": expand_system,
             "markets": expand_markets, "crypto": expand_crypto,
             "hackernews": expand_hackernews, "github": expand_github,
             "rss": expand_rss, "agent_pulse": expand_agent_pulse,
             "today": expand_today, "worldclock": expand_worldclock}

# (expanders_extra exec-include moved to just before the HTTP handler so its
# redefinitions — e.g. console_activity — WIN over every inline def above.)


def widget_expand(wid):
    fn = EXPANDERS.get(wid)
    if not fn:
        return {"rich": False}
    try:
        d = fn()
        d["rich"] = True
        return d
    except Exception as e:
        return {"rich": False, "error": type(e).__name__}


def _short_args(name, args):
    """Pull the meaningful bit of a tool call's arguments for the console."""
    try:
        d = json.loads(args) if isinstance(args, str) else args
    except Exception:
        return (args or "")[:180]
    if not isinstance(d, dict):
        return str(d)[:180]
    for k in ("command", "query", "q", "path", "file_path", "url", "cmd",
              "text", "prompt", "pattern", "name"):
        if k in d and d[k]:
            return str(d[k])[:220]
    return json.dumps(d)[:180]


def console_activity():
    """A live timeline of what the agent has been doing across ALL surfaces
    (dashboard, Telegram, CLI) — every tool call + result from state.db.
    This is the 'watch it work' feed: terminal commands, searches, file ops."""
    import sqlite3
    if not os.path.exists(STATE_DB):
        return {"events": []}
    uri = "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error:
        return {"events": []}
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT m.role, m.content, m.tool_calls, m.tool_name, m.timestamp, "
            "s.source FROM messages m JOIN sessions s ON m.session_id = s.id "
            "WHERE m.tool_calls IS NOT NULL OR m.tool_name IS NOT NULL "
            "ORDER BY m.timestamp DESC LIMIT 80").fetchall()
    except sqlite3.Error:
        con.close()
        return {"events": []}
    con.close()
    out = []
    for r in rows:
        src = r["source"] or "cli"
        if r["tool_calls"]:
            try:
                calls = json.loads(r["tool_calls"])
            except Exception:
                calls = []
            for c in (calls if isinstance(calls, list) else []):
                fn = c.get("function") or {}
                name = fn.get("name") or c.get("name") or "tool"
                args = fn.get("arguments")
                if args is None:
                    args = c.get("arguments") or c.get("input") or ""
                out.append({"ts": r["timestamp"], "source": src, "kind": "call",
                            "tool": name, "detail": _short_args(name, args)})
        elif r["tool_name"]:
            out.append({"ts": r["timestamp"], "source": src, "kind": "result",
                        "tool": r["tool_name"],
                        "detail": (r["content"] or "").strip()[:240]})
    out.sort(key=lambda e: -(e["ts"] or 0))
    return {"events": out[:60]}


def capabilities():
    def safe(fn, fb):
        try:
            return fn()
        except Exception:
            return fb
    caps = safe(scan_skills, {"total": 0, "categories": [], "items": []})
    mem = safe(read_memory, {"facts": [], "count": 0, "updated": None})
    ins = safe(compute_insights, {"sessions": 0, "messages": 0,
                                  "tool_calls": 0, "input_tokens": 0,
                                  "output_tokens": 0, "platforms": [],
                                  "by_day": [], "first_seen": None})
    return {"skills": caps, "memory": mem, "insights": ins}


def _hermes_env():
    env = dict(os.environ)
    local_bin = os.path.join(HOME, ".local", "bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    return env


try:
    import hermes_rpc
except Exception as _rpc_e:  # never let a helper import take the hub down
    hermes_rpc = None
    # ...but SAY SO. A failed import here silently downgrades every chat turn
    # to the one-shot `hermes -z` path (no streaming, no interactive
    # approvals), and the only symptom was a status line the user reads as a
    # transient network blip.
    print("[chat] hermes_rpc import FAILED — every turn will fall back to "
          f"one-shot mode: {type(_rpc_e).__name__}: {_rpc_e}",
          file=sys.stderr, flush=True)

# Async chat jobs: /api/chat starts one, /api/chat/poll streams it to the UI.
CHAT_JOBS = {}
_jobs_lock = threading.Lock()


def _new_job(session):
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "session": session, "state": "running", "text": "",
           "status": "", "approval": None, "reply": "", "ok": False,
           "done": False, "ts": time.time()}
    with _jobs_lock:
        # drop finished jobs older than an hour
        for k in [k for k, v in CHAT_JOBS.items()
                  if v.get("done") and time.time() - v["ts"] > 3600]:
            CHAT_JOBS.pop(k, None)
        CHAT_JOBS[jid] = job
    return job


def _finish_chat_job(job, session):
    """Persist the bot reply once a job completes."""
    chat = load_chat(session)
    chat["messages"].append({"role": "bot", "text": job["reply"],
                             "ts": time.time(), "err": not job["ok"]})
    save_chat(session, chat)


def _chat_worker(job, session, prompt):
    try:
        # asleep (idle-suspend) — or down WITHOUT a marker (crash, external
        # bootout, refused start): a genuine user turn wakes it either way.
        # A deliberate pause never reaches here (/api/chat fails fast).
        if agent_idle_suspended() or (not agent_paused() and not model_online()):
            job["status"] = "Waking the model from sleep — about 30s…"
            if not agent_wake(wait=True):
                job.update(reply="The model was asleep and didn't wake in time — "
                           "give it a few seconds and send that again.",
                           ok=False, state="done", done=True)
                _finish_chat_job(job, session)
                return
        chat = load_chat(session)

        def save_meta():
            cur = load_chat(session)
            cur["serve_sid"] = chat.get("serve_sid", "")
            cur["serve_key"] = chat.get("serve_key", "")
            save_chat(session, cur)

        hermes_rpc.run_turn(job, chat, prompt, save_meta)
    except Exception as e:
        # serve backend unreachable/broken — fall back to the old one-shot CLI.
        # Print the real cause FIRST: this except swallowed everything, so a
        # missing hermes_rpc, a WS 401 from a stale serve token, a protocol
        # change and a plain bug all looked identical in the log (i.e. absent)
        # and were only visible as "one-shot mode" in the UI. Type + message +
        # the last 5 traceback frames is enough to name the failure without
        # dumping a full trace on every turn.
        print(f"[chat] serve turn failed ({type(e).__name__}: {e}) — "
              "falling back to one-shot mode", file=sys.stderr, flush=True)
        for _tl in traceback.format_exc().rstrip().splitlines()[-5:]:
            print("[chat]   " + _tl, file=sys.stderr, flush=True)
        job["status"] = "serve backend unavailable, using one-shot mode"
        ok, text = run_agent(prompt, session=session)
        job.update(reply=text, ok=ok, state="done", done=True)
    _finish_chat_job(job, session)


def run_agent(message, session=None, lane="primary"):
    """One agent turn via `hermes -z`. Returns (ok, text). lane="bg" routes the
    run to the background model lane when it's up (briefing / news / intel)."""
    cmd = [HERMES]
    if session:
        cmd += ["--continue", session]
    if lane == "bg":
        cmd += bg_lane()["hermes_args"]
    cmd += ["-z", message]
    with _agent_lock:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=AGENT_TIMEOUT, env=_hermes_env())
        except subprocess.TimeoutExpired:
            return False, f"The agent took longer than {AGENT_TIMEOUT}s and was stopped."
        except FileNotFoundError:
            return False, f"Could not find the `hermes` binary at {HERMES}."
        except OSError as e:
            # Anything else the OS can refuse a spawn with (EACCES on a
            # non-executable hermes, ENOMEM/EAGAIN under load, ENOTDIR on a
            # broken PATH entry). The narrow except let those escape into the
            # calling thread, which killed the chat-job worker BEFORE it could
            # set done=True — the UI then polled a job that never finished.
            print(f"[run_agent] spawn failed: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
            return False, f"Could not run the agent ({type(e).__name__}: {e})."
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        tail = "\n".join(err[-6:]) if err else "(no error output)"
        return False, out or f"hermes exited with code {proc.returncode}:\n{tail}"
    return True, out or "(the agent returned an empty response)"


def model_online():
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def bg_model():
    try:
        v = open(os.path.join(DATA, "bg-model")).read().strip()
        if v:
            return v
    except OSError:
        pass
    return DEFAULT_BG_MODEL


def _bg_probe():
    try:
        with urllib.request.urlopen(BG_MODEL_URL, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def bg_online():
    return bool(_cached("bg_online", 15, _bg_probe))


def _chat_url_of(models_url):
    if models_url.endswith("/v1/models"):
        return models_url[:-len("/models")] + "/chat/completions"
    return re.sub(r"/v1/models/?$", "/v1/chat/completions", models_url)


def bg_lane():
    """Where background (non-user) model work should go:
    {lane: 'bg'|'primary', chat_url, model, hermes_args: [...]} — hermes_args are
    the extra `hermes` CLI args that route a -z run to the lane (the named
    custom provider `custom:bg` in ~/.hermes/config.yaml)."""
    if bg_online():
        return {"lane": "bg", "chat_url": _chat_url_of(BG_MODEL_URL), "model": bg_model(),
                "hermes_args": ["--provider", "custom:bg", "-m", bg_model()]}
    return {"lane": "primary", "chat_url": _chat_url_of(MODEL_URL), "model": active_model(),
            "hermes_args": []}


# --------------------------------------------------------------------------
# access grants — folders the user lets the assistant see
# --------------------------------------------------------------------------

ACCESS_FILE = os.path.join(DATA, "access.json")


def get_access():
    return read_json(ACCESS_FILE, {"dirs": []})


def access_preamble():
    dirs = get_access()["dirs"]
    now = time.strftime("%A %Y-%m-%d %H:%M %Z")
    # Prefix-stability (P3.B3): the mlx prompt cache reuses the longest common
    # TOKEN PREFIX between requests — the first changed byte busts every token
    # after it. So stable lines go FIRST and volatile lines LAST, ordered
    # least→most volatile (grants → invariant → tasks → calendar → wall-clock
    # minute). Measured on the local Qwen3-30B: time-line-first re-prefilled
    # 9,266 of ~9,280 tokens on a minute tick (TTFT 2.92s); time-line-last only
    # 15 tokens (TTFT 0.26s). See docs/plans/b3-prefix-ttft-findings.md.
    lines = [
        f"[context] Files the user drops in chat land in: {INBOX}",
    ]
    if dirs:
        lines.append(
            "[context] The user has granted you access to these folders — you may "
            "read/list/search inside them with your terminal tools when useful: "
            + ", ".join(dirs)
            + ". Do not modify or delete anything unless explicitly asked, and do "
            "not roam outside granted folders."
        )
    else:
        lines.append(
            "[context] The user has not granted any folder access yet; don't "
            "browse their files. They can grant folders in the dashboard sidebar."
        )
    lines.append(
        "[context] Never invent facts, events, or files you did not actually "
        "read or verify. If you don't know, say so plainly."
    )
    lines.append(
        "[context] Do NOT repeat a web_search (or any tool call) with an "
        "identical or near-identical query. If two searches don't answer the "
        "question, stop and conclude honestly — say what you found and that you "
        "couldn't confirm the rest (it may not exist or be too new). Never loop "
        "on the same search; a handful of distinct searches is the ceiling."
    )
    tasks = [t for t in get_tasks()["tasks"] if not t.get("done")][:10]
    if tasks:
        lines.append("[context] The user's open tasks (from their dashboard task "
                     "list): " + "; ".join(t["text"] for t in tasks))
    cal = macos_calendar()
    if cal.get("available") and cal.get("events"):
        lines.append("[context] Today's events from the user's macOS Calendar: "
                     + "; ".join(f"{e['time']} {e['title']}".strip()
                                 for e in cal["events"]))
    lines.append(f"[context] Local time: {now}.")
    return "\n".join(lines) + "\n\n"


# --------------------------------------------------------------------------
# briefing — cached, regenerated in the background
# --------------------------------------------------------------------------

BRIEFING_FILE = os.path.join(DATA, "briefing.json")

BRIEFING_PROMPT = (
    "You are my personal-assistant dashboard. Give me a concise briefing for "
    "right now, as short markdown sections with '##' headers:\n"
    "1. **Today's schedule** — my calendar events for today with times.\n"
    "2. **Top priorities** — up to 3 things most worth my attention.\n"
    "3. **Needs a reply** — emails from the last 24h that want a response.\n"
    "4. **On your radar** — anything else you genuinely think I should know "
    "(e.g. notable new files in granted folders, follow-ups from our chats).\n\n"
    "Keep it tight and skimmable. CRITICAL: only include real items you can "
    "actually verify from my calendar, email, granted folders, or your memory "
    "of me. Never invent events, priorities, or emails — if a section has no "
    "real data, write 'nothing yet' under it. If calendar/email aren't "
    "connected, say so in ONE line at the top. Do NOT send or modify anything.\n\n"
    "Output ONLY the briefing itself as plain markdown starting with a '##' "
    "header. No preamble, no meta commentary, no role tags, no system-message "
    "formatting of any kind."
)


def _briefing_is_sane(text):
    """Reject model output that isn't actually a briefing (meta/role-play
    garbage) so we never overwrite a good cached briefing with junk."""
    if "##" not in text:
        return False
    bad = ("OUT-OF-BAND", "[/", "USER MESSAGE", "[context]", "SYSTEM NOTIFICATION")
    return not any(b.lower() in text.lower() for b in bad)


def _generate_briefing():
    global _briefing_generating
    with _state_lock:
        if _briefing_generating:
            return
        _briefing_generating = True
    try:
        if not mlx_admission()[0]:   # back off background work when memory is high
            return
        for attempt in range(2):  # one retry if the model emits meta garbage
            ok, text = run_agent(access_preamble() + BRIEFING_PROMPT, lane="bg")
            if ok and _briefing_is_sane(text):
                with _state_lock:
                    write_json(BRIEFING_FILE, {
                        "ok": True, "reply": text, "generated_at": time.time(),
                    })
                break
    finally:
        with _state_lock:
            _briefing_generating = False


def briefing_loop():
    """Keep the briefing fresh so the page always loads instantly."""
    while True:
        cached = read_json(BRIEFING_FILE, {})
        age_min = (time.time() - cached.get("generated_at", 0)) / 60
        if age_min >= BRIEFING_REFRESH_MIN and model_online():
            _generate_briefing()
        time.sleep(60)


_DOWN_PERSIST_S = 60          # two consecutive 30s ticks of genuine down-ness
_down_seen_at = [0.0]         # first tick the loop saw it down with no process


def _start_token_fresh():
    """An unconsumed start token (<180s, mlx-server.sh's window) means a start
    is in flight — never treat that as 'down'."""
    try:
        return time.time() - os.path.getmtime(MODEL_START_TOKEN) <= 180
    except OSError:
        return False


def _mlx_proc_alive():
    """Is any model-server process present (either backend, either lane)?
    Cheap pgrep — used to tell 'loading right now' from 'genuinely down'.
    Unknown (probe failed) counts as alive so we never mark a loading model."""
    try:
        return bool(subprocess.run(["pgrep", "-f", "mlx_lm server|mlx-vlm-launch"],
                                   capture_output=True, text=True,
                                   timeout=5).stdout.strip())
    except Exception:
        return True


def _mlx_footprint_gb():
    """Real memory (phys_footprint) of the MLX server, in GB — ps RSS
    under-reports MLX's Metal/unified allocations, so use footprint(1)."""
    try:
        # matches both backends: `python3 -m mlx_lm server` and the mlx_vlm
        # launcher (`.../mlx-vlm-venv/bin/python mlx-vlm-launch.py`)
        pids = subprocess.run(["pgrep", "-f", "mlx_lm server|mlx-vlm-launch"],
                              capture_output=True, text=True, timeout=5).stdout.split()
        if not pids:
            return None
        total, seen = 0.0, False
        for pid in pids:                              # both lanes (primary + bg)
            out = subprocess.run(["footprint", "-p", pid], capture_output=True,
                                 text=True, timeout=20).stdout
            m = re.search(r"phys_footprint:\s*([\d.]+)\s*(GB|MB)", out)
            if not m:
                continue
            v = float(m.group(1))
            total += v if m.group(2) == "GB" else v / 1024
            seen = True
        return total if seen else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# MLX memory ceiling — the KV/prompt cache can balloon under concurrent load
# (several 20k-token prefills at ~2GB KV each) and take the whole Mac down.
# Two-layer defence:
#   1. ADMISSION CONTROL (mlx_admission): at/above MLX_SOFT_GB, the dashboard
#      REFUSES new model work ("can't take more unless you allow it") so the
#      balloon can't grow — new turns get a clear "memory high, try again"
#      instead of piling on. The user can override (touch MEM_OVERRIDE_FILE via
#      /api/model/mem_override) to force work through.
#   2. HARD WATCHDOG (memory_guard_loop): polls every 30s; above MLX_HARD_GB it
#      restarts the model server (reliable bootout→bootstrap, not kickstart -k)
#      as a last resort — this is the cache-clear.
# Until 1.0.3 both numbers were hardcoded at 50/56 — right for the 64GB machine
# Hermes was built on, actively harmful on a 16GB Air, where a "soft 50" ceiling
# means admission control NEVER engages and the first big prefill swaps the Mac
# to death. Since Hermes ships as downloadable software the defaults now derive
# from physical RAM (_mem_ceilings below); env still wins.
# --------------------------------------------------------------------------

_RAM_GB_CACHE = [None]      # one-slot memo: hw.memsize cannot change at runtime


def _machine_ram_gb():
    """Physical RAM in GB, cached for the life of the process.

    GB here means GiB — the number Apple prints on the box. `hw.memsize` on a
    "64 GB" Mac is exactly 64 * 2**30, so bytes >> 30 reproduces the marketing
    capacity for every real config (16/24/32/36/48/64/96/128) with no rounding
    slop. (The system-status panel divides by 1e9 instead, deliberately: it
    reports *usage* against vm_stat's decimal totals. Don't mix the two.)

    Falls back to 64 when sysctl is missing or unparseable — a familiar default
    (the machine every measurement in docs/plans was taken on) beats raising at
    import time inside a launchd service.  That fallback SAYS SO on stderr: it
    silently sets every memory ceiling in the process (MLX_SOFT_GB/MLX_HARD_GB
    and each model row's `fit`) to 64GB numbers, so on a 16GB Mac admission
    control would never engage and the only visible symptom is swap.  One line
    per process — the memo below is filled in either way, so this branch runs
    at most once (and `sysctl` needs /usr/sbin on PATH under launchd, a known
    gotcha this line finally makes legible in ~/.hermes/logs/dashboard.log).
    """
    if _RAM_GB_CACHE[0] is None:
        gb, why = 64.0, ""
        try:
            out = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True, timeout=3).stdout
            b = int(out.strip())
            if b > 0:
                gb = b / (1024 ** 3)
            else:
                why = "hw.memsize is %r" % (b,)
        except Exception as e:
            why = "%s: %s" % (type(e).__name__, e)
        if why:
            print("[mem] could not read hw.memsize (%s) — assuming %.0f GB of "
                  "RAM for every ceiling in this process" % (why, gb),
                  file=sys.stderr)
        _RAM_GB_CACHE[0] = gb
    return _RAM_GB_CACHE[0]


def _mem_ceilings(ram_gb, env=None):
    """(soft_gb, hard_gb) for a machine with `ram_gb` of physical RAM.

    PURE — no I/O, no globals — so the rule is unit-testable without a Mac of
    each size. The rule:

      * **RAM >= 64 GB -> (50, 56)**, byte-identical to the pre-1.0.3 constants.
        Flat, not a percentage, and that is deliberate: the ceiling bounds the
        KV/prompt-cache BALLOON, whose useful size is set by the workload (~2GB
        of KV per ~20k-token agent sequence x a handful of concurrent
        producers), not by how much RAM the machine happens to have. Letting a
        128GB Mac balloon to 100GB buys nothing and just delays the restart that
        clears the thrash. Operators who want more raise MLX_SOFT_GB by hand.
      * **Below 64 GB -> soft = round(0.72 x RAM), hard = round(0.82 x RAM)** —
        the same shape as 50/56 on a 64GB box scaled down (0.78/0.88 there;
        slightly tighter below because a small Mac has proportionally less slack
        for the OS, the app and Safari).
      * **hard >= soft + 2** so the watchdog can never fire at or below the
        admission ceiling (only bites under ~20GB, where 0.10 x RAM < 2).
      * **Floor (8, 10)**: below ~11GB no local model of ours fits anyway; a
        floor keeps the pair sane rather than pretending 4/5 is a working config.

    env overrides (MLX_SOFT_GB / MLX_HARD_GB) are taken VERBATIM — an explicit
    setting is the operator's call, including a deliberately silly one. The one
    courtesy: setting only MLX_SOFT_GB drags the derived hard up with it, so a
    soft-only override can't invert the pair. Unparseable values are ignored
    (they used to raise ValueError at import and take the dashboard down).
    """
    env = os.environ if env is None else env

    def _num(key):
        try:
            return float(env[key])
        except (KeyError, TypeError, ValueError):
            return None

    if ram_gb >= 64:
        soft, hard = 50.0, 56.0
    else:
        soft = max(8.0, float(round(0.72 * ram_gb)))
        hard = max(10.0, float(round(0.82 * ram_gb)))
        hard = max(hard, soft + 2)
    soft_env, hard_env = _num("MLX_SOFT_GB"), _num("MLX_HARD_GB")
    if soft_env is not None:
        soft = soft_env
    if hard_env is not None:
        hard = hard_env
    elif soft_env is not None and hard < soft + 2:
        hard = soft + 2
    return soft, hard


MACHINE_RAM_GB = _machine_ram_gb()
# admission ceiling / last-resort restart. 68.7e9-byte M5 Max -> 64 GiB -> 50/56.
MLX_SOFT_GB, MLX_HARD_GB = _mem_ceilings(MACHINE_RAM_GB)
MEM_OVERRIDE_FILE = os.path.join(DATA, "mem-override")


def _model_fit(model_ram_gb, machine_gb=None):
    """How comfortably a roster model's resident size fits this Mac.

    "ok" | "tight" | "no", or None when the roster entry carries no `ram` (the
    synthetic active-model row does not) so the UI can simply omit the line
    rather than guess. Thresholds are of TOTAL RAM, not of the memory ceiling:
    the question the model menu answers is "will downloading this 17GB thing be
    a mistake on MY Mac", which is a hardware question.

      no    : ram > 0.85 x machine — it would not co-exist with the OS at all.
      tight : ram > 0.60 x machine — it loads, but expect swap under real use.
      ok    : everything else.
    """
    if not model_ram_gb or model_ram_gb <= 0:
        return None
    m = _machine_ram_gb() if machine_gb is None else machine_gb
    if not m or m <= 0:
        return None
    if model_ram_gb > 0.85 * m:
        return "no"
    if model_ram_gb > 0.60 * m:
        return "tight"
    return "ok"


# --- Idle suspend ----------------------------------------------------------
# The model server is the memory hog (~26GB resident, weights + prompt/KV cache).
# When NOBODY is using it there's no reason to hold that RAM. idle_suspend_loop()
# boots the model server out after _idle_min() of no USER activity (dashboard
# chat + Telegram/hub turns; background briefing/watchtower does NOT count and
# never wakes it), and the next user turn transparently wakes it (agent_wake,
# ~30-50s cold start). This is DISTINCT from a manual pause: a pause is a
# deliberate "stay down, fail fast" park (PAUSE_FILE); an idle-suspend is
# "asleep, wake on demand" (IDLE_SUSPEND_FILE). The two files never coexist.
IDLE_SUSPEND_FILE = os.path.join(DATA, "agent-idle-suspended")
IDLE_SUSPEND_OFF = os.path.join(DATA, "idle-suspend-off")   # user opt-out marker
IDLE_MIN_FILE = os.path.join(DATA, "idle-suspend-min")      # persisted minutes
IDLE_SUSPEND_MIN = float(os.environ.get("IDLE_SUSPEND_MIN", "10"))
_last_user_activity = time.time()   # bumped on every genuine user turn


MODEL_START_TOKEN = os.path.join(DATA, "model-start-ok")


def _mlx_start(uid=None):
    """Load + start the model server regardless of the plist's RunAtLoad
    (on-demand mode, 2026-09-01: RunAtLoad/KeepAlive are false). Mints the
    start token (mlx-server.sh's gate refuses to load the model without a
    fresh one when model-autostart-off exists — this is what separates a real
    user-intent start from the app's blind kickstart), bootstraps the job
    (tolerating 'already loaded'), then kickstarts it (no-op when running).

    Returns True only when the server was actually asked to run. Both launchctl
    calls used to be fire-and-forget, so a failed bootstrap (bad plist, launchd
    error 5 after a recent bootout, a job removed by hand) still reported
    `loading: True` to the UI — the user watched a spinner for a model that was
    never going to come up, and the dashboard log said nothing. With RunAtLoad
    false (on-demand mode) the KICKSTART is what starts the process, so its
    failure is fatal too, not cosmetic."""
    uid = os.getuid() if uid is None else uid
    try:
        with open(MODEL_START_TOKEN, "w") as f:
            f.write(str(time.time()))
    except OSError as e:
        print(f"[mlx_start] could not mint the start token: {e}",
              file=sys.stderr, flush=True)

    def _boot():
        return subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", MLX_PLIST],
                              capture_output=True, text=True, timeout=20)

    def _clean(r):
        # "already loaded"/"already bootstrapped" is success: the job is there.
        return r.returncode == 0 or "already" in ((r.stderr or "") + (r.stdout or "")).lower()

    r = _boot()
    if not _clean(r):
        time.sleep(3)  # launchd needs a beat after a recent bootout (error 5)
        r2 = _boot()
        if not _clean(r2):
            print(f"[mlx_start] bootstrap failed (rc={r.returncode} then "
                  f"rc={r2.returncode}): {' '.join((r2.stderr or r.stderr or '').split())[:300]}",
                  file=sys.stderr, flush=True)
            return False
    k = subprocess.run(["launchctl", "kickstart", f"gui/{uid}/{MLX_LABEL}"],
                       capture_output=True, text=True, timeout=15)
    if k.returncode != 0:
        print(f"[mlx_start] kickstart failed (rc={k.returncode}): "
              f"{' '.join((k.stderr or '').split())[:300]}", file=sys.stderr, flush=True)
        return False
    return True


def _mlx_restart():
    """Reliable model-server restart (kickstart -k does NOT reload the KeepAlive
    service — it kept the old process). bootout fully frees the balloon, then
    _mlx_start reloads the active model with a fresh, empty cache."""
    uid = os.getuid()
    try:
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{MLX_LABEL}"],
                       capture_output=True, timeout=15)
        time.sleep(3)
        return _mlx_start(uid)      # propagate a failed start (was always True)
    except Exception as e:
        print(f"[memory_guard] restart failed: {e}", file=sys.stderr)
        return False


# --- manual "free memory now" (/api/model/mem_free) ------------------------
# The restart takes ~30-60s, so the POST can't wait for it — but it used to
# fire-and-forget a thread and answer {"ok":true,"restarting":true} even when
# the bootout/bootstrap failed, and the UI then blind-waited 4s and claimed
# success. The thread now records its outcome here and the client polls
# /api/model/mem_free/status until running goes false.
_mem_free_state = {"running": False, "ok": None, "error": "", "finished_at": 0.0}
_mem_free_lock = threading.Lock()


def _mem_free_run():
    """Thread body: restart the model server and PUBLISH the boolean result."""
    ok, err = False, ""
    try:
        ok = bool(_mlx_restart())
        if not ok:
            err = "restart failed — see dashboard log"
    except Exception as e:                       # _mlx_restart already catches,
        err = f"restart failed: {e}"             # belt & braces for the thread
    if ok:                                       # footprint changed — drop caches
        _widget_cache.pop("mlx_ram", None)
        _widget_cache.pop("mlx_ram_fast", None)
    with _mem_free_lock:
        _mem_free_state.update(running=False, ok=ok, error=err,
                               finished_at=time.time())


def _mem_free_start():
    """POST handler. One restart at a time: overlapping bootouts race the
    launchd start, and the button is very mashable while nothing looks to be
    happening. Returns {"ok":True,"started":True} only when a thread began."""
    with _mem_free_lock:
        if _mem_free_state["running"]:
            return {"ok": False, "error": "already restarting"}
        _mem_free_state.update(running=True, ok=None, error="", finished_at=0.0)
    threading.Thread(target=_mem_free_run, daemon=True).start()
    return {"ok": True, "started": True}


def _mem_free_status():
    """GET handler: {running, ok, error, finished_at} for the LAST/current run
    (ok is None until one has finished; finished_at 0 while in flight)."""
    with _mem_free_lock:
        return dict(_mem_free_state)


def mlx_admission():
    """(ok, gb, limit). ok=False → at/over the soft memory ceiling: refuse NEW
    model work so the KV cache can't overrun the machine. A present
    MEM_OVERRIDE_FILE bypasses the gate (the user's 'allow it' escape hatch)."""
    limit = MLX_SOFT_GB
    if os.path.exists(MEM_OVERRIDE_FILE):
        return True, None, limit
    gb = _cached("mlx_ram_fast", 15, _mlx_footprint_gb)
    if gb and gb >= limit:
        return False, gb, limit
    return True, gb, limit


def memory_guard_loop():
    """Hard watchdog: mlx_lm.server's prompt/KV cache accretes under load and
    once grew to ~49GB and thrashed the machine. Poll frequently; above the hard
    ceiling, restart the model server (frees the balloon + clears the cache).
    Admission control (mlx_admission) is the first line — this is the backstop."""
    while True:
        time.sleep(30)             # was 300 — balloons spike faster than that
        if agent_paused() or agent_idle_suspended():   # model intentionally down
            continue
        gb = _mlx_footprint_gb()
        if gb and gb > MLX_HARD_GB:
            if _mlx_restart():
                _widget_cache.pop("mlx_ram", None)
                _widget_cache.pop("mlx_ram_fast", None)
                print(f"[memory_guard] mlx footprint {gb:.0f}GB > "
                      f"{MLX_HARD_GB:.0f}GB — restarted model server "
                      "(freed the KV balloon)", file=sys.stderr)


# --------------------------------------------------------------------------
# Idle suspend — free the model's RAM when no one is using it, wake on demand.
# See the IDLE_SUSPEND_* block above for the design. Core invariant: an
# idle-suspend (automatic, wakes on the next prompt) is NEVER confused with a
# user pause (deliberate, stays down and fails fast) — different marker files.
# --------------------------------------------------------------------------

def agent_idle_suspended():
    return os.path.exists(IDLE_SUSPEND_FILE)


def idle_suspend_enabled():
    return not os.path.exists(IDLE_SUSPEND_OFF)


def _idle_min():
    """Minutes of no-activity before suspend (a persisted file overrides env)."""
    try:
        v = float(open(IDLE_MIN_FILE).read().strip())
        if v >= 1:
            return v
    except Exception:
        pass
    return IDLE_SUSPEND_MIN


def note_user_activity():
    """Record a genuine USER turn (dashboard chat / menu-bar quick-ask). Resets
    the idle clock; background work must NOT call this or the model never sleeps."""
    global _last_user_activity
    _last_user_activity = time.time()


# --------------------------------------------------------------------------
# Prewarm after wake (post-v1 backlog #1)
# --------------------------------------------------------------------------
# agent_wake() returns the moment /v1/models answers, but the model server is
# only *loaded* then — nothing is prefilled. The first real turn after every
# idle-suspend therefore pays the cold prefill of the ~18k-token Hermes system
# prompt (~25s at the measured ~700-1000 prompt tok/s; docs/plans/
# post-v1-baseline.md). Every LATER turn is ~0.2s because mlx-vlm's APC exact
# prefix cache holds that prefix.
#
# So: right after the wake poll first sees the server, run ONE throwaway turn
# through the SAME serve WebSocket path real dashboard/Telegram turns use. The
# byte-identical system prompt is the entire point — a `hermes -z` no-op is a
# different invocation and would not necessarily land on the same trie entry.
#
# The synthetic turn must be invisible to everything that measures "is a human
# using this": it is NOT registered in CHAT_JOBS, it never calls
# note_user_activity(), it never writes a chats/*.json (so list_sessions() /
# /api/history can't show it), and its serve session carries its own
# sessions.source + title so _newest_external_turn_ts() cannot mistake it for a
# Telegram/hub turn and keep the model awake forever.
PREWARM_SESSION = "__prewarm__"      # chat-store key that must never be listed
PREWARM_TITLE = "__prewarm__"        # serve session title (filtered in SQL)
PREWARM_SOURCE = "prewarm"           # sessions.source in state.db
PREWARM_PROMPT = "Reply with exactly: ok"
PREWARM_TIMEOUT = float(os.environ.get("HERMES_PREWARM_TIMEOUT", "120"))
PREWARM_DEFAULT = True               # settings.json prewarm.enabled default

# last_ms / last_at / last_result surface in models_payload() and
# GET /api/agent/prewarm. Plain module dict: written by the prewarm thread,
# read by HTTP threads; single assignments of immutable values, no lock needed.
_prewarm_state = {"last_ms": None, "last_at": None, "last_result": None}
_prewarm_inflight = threading.Lock()   # never held while calling the chat path


def prewarm_enabled():
    """settings.json `prewarm.enabled`, default True. Read fresh per call (the
    file is a few hundred bytes) so the toggle needs no restart, and fails OPEN
    on a read problem — a corrupt settings file must not silently disable it."""
    try:
        cfg = (get_settings() or {}).get("prewarm")
        if isinstance(cfg, dict) and "enabled" in cfg:
            return bool(cfg.get("enabled"))
    except Exception:
        pass
    return PREWARM_DEFAULT


def set_prewarm_enabled(on):
    on = bool(on)
    with _state_lock:
        s = get_settings() or {}
        cfg = s.get("prewarm")
        s["prewarm"] = {**(cfg if isinstance(cfg, dict) else {}), "enabled": on}
        write_json(SETTINGS_FILE, s)
    return on


def prewarm_payload():
    return {"enabled": prewarm_enabled(), **_prewarm_state}


def _newest_external_turn_ts():
    """Newest message ts from Telegram/hub sessions — activity this process can't
    see directly (they go agent→serve→:8080, not through the dashboard). Read-only,
    best-effort, 0.0 on any problem. Excludes 'cli' (the briefing's own -z turns)
    so background work never resets the clock — and excludes the prewarm session
    (source PREWARM_SOURCE, and by title too in case a future serve build ignores
    the source we pass and files it under 'hub'), which is OUR OWN synthetic turn:
    counting it would reset the idle clock on every wake and the model would never
    sleep again."""
    if not os.path.exists(STATE_DB):
        return 0.0
    try:
        import sqlite3
        uri = "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=1.5)
        row = con.execute(
            "SELECT MAX(m.timestamp) FROM messages m JOIN sessions s "
            "ON m.session_id = s.id WHERE s.source IN ('telegram','hub') "
            "AND s.source <> ? AND (s.title IS NULL OR s.title <> ?)",
            (PREWARM_SOURCE, PREWARM_TITLE)
        ).fetchone()
        con.close()
        return float(row[0]) if row and row[0] else 0.0
    except Exception:
        return 0.0


def _chat_jobs_active():
    with _jobs_lock:
        return any(not v.get("done") for v in CHAT_JOBS.values())


def _prewarm_skip_reason():
    """Why NOT to prewarm right now, or None to go ahead. Every reason is a
    case where the extra generation would either be wasted or actively harmful:
    a paused agent must stay down; a real turn already in flight will warm the
    prefix itself (and racing it just competes for the model); the briefing is
    the same story on the background lane; and over the soft memory ceiling
    mlx_admission() is refusing new model work for the whole process, which
    includes ours."""
    if not prewarm_enabled():
        return "disabled"
    if hermes_rpc is None:
        return "no-serve-client"
    if agent_paused():
        return "paused"
    if _chat_jobs_active():
        return "chat-job-active"
    if globals().get("_briefing_generating"):
        return "briefing"
    try:
        ok, gb, limit = mlx_admission()
        if not ok:
            return "memory-ceiling"
    except Exception:
        pass
    return None


def _prewarm_after_wake(reason="wake"):
    """ONE trivial turn through the serve WebSocket so the ~18k-token system
    prompt is resident in the APC exact-prefix cache before the user's real
    first turn. Runs on a detached daemon thread (see agent_wake); never raises
    into its caller, never touches the idle clock, never appears in the UI.

    Holds no lock the chat path needs: a real /api/chat that arrives mid-prewarm
    goes straight through (serve handles concurrent sessions), and the worst
    case is that it queues behind ~2 generated tokens."""
    if not _prewarm_inflight.acquire(blocking=False):
        return "already-running"      # a second wake raced us; one is enough
    try:
        skip = _prewarm_skip_reason()
        if skip:
            _prewarm_state.update(last_result="skipped:" + skip,
                                  last_at=time.time(), last_ms=None)
            print(f"[prewarm] skipped ({skip})", file=sys.stderr, flush=True)
            return "skipped:" + skip

        # A throwaway job/meta pair. Deliberately NOT registered in CHAT_JOBS:
        # _chat_jobs_active() gates idle-suspend and the prewarm guard above,
        # and a synthetic turn must not look like work in flight.
        job = {"id": "prewarm", "state": "running", "text": "", "status": "",
               "approval": None, "reply": "", "ok": False, "done": False,
               "ts": time.time()}
        meta = {"title": PREWARM_TITLE, "serve_sid": "", "serve_key": ""}
        t0 = time.time()

        def _turn():
            try:
                # save_meta is a no-op: persisting serve_sid/serve_key would
                # mean a chats/*.json for a conversation the user never had.
                # A fresh serve session per wake is fine — the cache entry we
                # want is the SYSTEM PROMPT prefix, which is identical either way.
                hermes_rpc.run_turn(job, meta, PREWARM_PROMPT, lambda: None,
                                    source=PREWARM_SOURCE)
            except Exception as e:
                job["_err"] = f"{type(e).__name__}: {e}"
                job["done"] = True

        th = threading.Thread(target=_turn, name="prewarm-turn", daemon=True)
        th.start()
        th.join(PREWARM_TIMEOUT)
        ms = int((time.time() - t0) * 1000)
        if th.is_alive():
            result = "timeout"
        elif job.get("_err"):
            result = "error:" + job["_err"]
        elif job.get("ok"):
            result = "ok"
        else:
            result = "failed:" + (job.get("reply") or "no reply")[:120]
        _prewarm_state.update(last_ms=ms, last_at=time.time(),
                              last_result=result)
        print(f"[prewarm] {reason}: {result} in {ms}ms "
              f"(system prompt now in the prefix cache)",
              file=sys.stderr, flush=True)
        return result
    except Exception as e:      # a prewarm failure must never affect the wake
        _prewarm_state.update(last_result=f"error:{type(e).__name__}: {e}",
                              last_at=time.time(), last_ms=None)
        print(f"[prewarm] failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return "error"
    finally:
        _prewarm_inflight.release()


def _prewarm_kick(reason="wake"):
    """Fire-and-forget the prewarm turn. Called only from agent_wake(), right
    after the model first answers /v1/models."""
    try:
        threading.Thread(target=_prewarm_after_wake, args=(reason,),
                         name="prewarm", daemon=True).start()
    except Exception as e:
        print(f"[prewarm] could not start: {e}", file=sys.stderr, flush=True)


def agent_wake(wait=True, timeout=90):
    """Bring the model back after an idle-suspend (NOT a user pause). Bootstraps
    the launchd service, clears the idle marker, and — when wait — blocks until
    the model answers /v1/models or the timeout elapses. Returns True if the
    model is (or came) online. Safe to call when already awake (idempotent)."""
    uid = os.getuid()
    try:
        if not _mlx_start(uid):     # logged in detail by _mlx_start itself
            print("[idle_suspend] wake: launchctl would not start the model "
                  "server — the poll below will time out", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[idle_suspend] wake bootstrap failed: {e}", file=sys.stderr)
        return False
    try:
        os.remove(IDLE_SUSPEND_FILE)
    except OSError:
        pass
    for _k in ("mlx_ram", "mlx_ram_fast", "sys_live"):
        _widget_cache.pop(_k, None)
    note_user_activity()
    if not wait:
        # The server is up but not answering yet; there is nothing to prefill
        # and nobody waiting on this call. The prewarm fires only where we can
        # prove the model is online (below).
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if model_online():
            _prewarm_kick("wake")   # detached; the caller's turn is not delayed
            return True
        time.sleep(1.5)
    up = model_online()             # last chance — it may have landed just now
    if up:
        _prewarm_kick("wake")
    return up


def idle_suspend_loop():
    """Auto-suspend the model server after _idle_min() of no USER activity,
    freeing its ~26GB; the next user turn wakes it (agent_wake). Background
    briefing/watchtower work never counts as activity and never wakes a sleeping
    model. While asleep, also wake if a Telegram/hub turn shows up in state.db
    (best-effort cross-surface wake — the dashboard can't intercept that path)."""
    while True:
        time.sleep(30)
        try:
            if not idle_suspend_enabled():
                continue
            if agent_paused():
                continue                # deliberate park — leave it down
            if agent_idle_suspended():
                # asleep: wake if a Telegram/hub turn arrived since we slept.
                slept_at = 0.0
                try:
                    slept_at = float(open(IDLE_SUSPEND_FILE).read().strip() or 0)
                except Exception:
                    pass
                if _newest_external_turn_ts() > slept_at + 1:
                    print("[idle_suspend] external turn while asleep — waking",
                          file=sys.stderr)
                    agent_wake(wait=False)
                continue
            if not model_online():
                # down but not marked asleep (crash, external bootout, a start
                # the on-demand gate refused): mark it asleep so chat / Telegram
                # cross-surface wake / "Wake now" bring it back on real use —
                # the rule main() applies at boot. Only once the down state has
                # PERSISTED across ticks (>= _DOWN_PERSIST_S) with no server
                # process and no fresh start token: switch_model / _mlx_restart /
                # resume / thinking-toggle all go bootout -> sleep 3 -> start,
                # a ~4s window with nothing running, and a stale marker while
                # online would silence memory_guard AND stop idle-suspend
                # (RAM never reclaimed).
                if (not _chat_jobs_active() and not globals().get("_briefing_generating")
                        and not _mlx_proc_alive() and not _start_token_fresh()):
                    if _down_seen_at[0] <= 0:
                        _down_seen_at[0] = time.time()
                    elif (time.time() - _down_seen_at[0] >= _DOWN_PERSIST_S
                          and not agent_paused()):
                        with open(IDLE_SUSPEND_FILE, "w") as _f:
                            _f.write(str(time.time()))
                        _down_seen_at[0] = 0.0
                        print("[idle_suspend] model down without a marker — marked "
                              "asleep; next user turn wakes it", file=sys.stderr)
                else:
                    _down_seen_at[0] = 0.0
                continue                # nothing running to suspend
            _down_seen_at[0] = 0.0
            if _chat_jobs_active() or globals().get("_briefing_generating"):
                continue                # a turn is in flight — never suspend mid-work
            idle_for = time.time() - max(_last_user_activity,
                                         _newest_external_turn_ts())
            if idle_for < _idle_min() * 60:
                continue
            uid = os.getuid()
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{MLX_LABEL}"],
                           capture_output=True, timeout=15)
            with open(IDLE_SUSPEND_FILE, "w") as _f:
                _f.write(str(time.time()))
            for _k in ("mlx_ram", "mlx_ram_fast", "sys_live"):
                _widget_cache.pop(_k, None)
            print(f"[idle_suspend] no user activity for {idle_for / 60:.0f}m — "
                  "suspended model server (freed its RAM); next prompt wakes it",
                  file=sys.stderr)
        except Exception as e:
            print(f"[idle_suspend] loop error: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# chats — server-side transcripts so history survives anything
# --------------------------------------------------------------------------

SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def chat_path(session):
    return os.path.join(CHATS, session + ".json")


def load_chat(session):
    return read_json(chat_path(session), {"messages": [], "title": ""})


def save_chat(session, chat):
    with _state_lock:
        write_json(chat_path(session), chat)


def list_sessions():
    out = []
    for fn in os.listdir(CHATS):
        if not fn.endswith(".json"):
            continue
        sid = fn[:-5]
        if sid == PREWARM_SESSION:
            continue        # synthetic prewarm-after-wake turn, never a chat
        chat = load_chat(sid)
        if not chat["messages"]:
            continue
        out.append({
            "id": sid,
            # a user rename (POST /api/sessions/meta) writes chat["title"], so
            # the custom title already wins over the first-message excerpt
            "title": chat.get("title") or chat["messages"][0].get("text", "")[:48],
            "updated": os.path.getmtime(chat_path(sid)),
            "pinned": bool(chat.get("pinned")),
        })
    # pinned first (they must survive the 30-row cap even when old), then newest
    out.sort(key=lambda c: (not c["pinned"], -c["updated"]))
    return out[:30]


# --------------------------------------------------------------------------
# tasks — local to-do list, visible to the agent via preamble
# --------------------------------------------------------------------------

TASKS_FILE = os.path.join(DATA, "tasks.json")


def get_tasks():
    return read_json(TASKS_FILE, {"tasks": []})


def save_tasks(t):
    with _state_lock:
        write_json(TASKS_FILE, t)


# --------------------------------------------------------------------------
# settings (weather location etc.)
# --------------------------------------------------------------------------

SETTINGS_FILE = os.path.join(DATA, "settings.json")


def get_settings():
    return read_json(SETTINGS_FILE, {})


# --------------------------------------------------------------------------
# widgets: weather / calendar / recent files / system — all cached briefly
# --------------------------------------------------------------------------

_widget_cache = {}


def _cached(key, ttl, fn):
    now = time.time()
    hit = _widget_cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _widget_cache[key] = (now, val)
    return val


def _ssl_context():
    # python.org framework builds ship without system root certs wired up;
    # use certifi's bundle when available so HTTPS doesn't CERTIFICATE_VERIFY_FAILED.
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


def _http_json(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-dashboard"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        return json.loads(r.read().decode())


WMO = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
       45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
       61: "Rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
       67: "Freezing rain", 71: "Snow", 73: "Snow", 75: "Heavy snow",
       77: "Snow", 80: "Showers", 81: "Showers", 82: "Heavy showers",
       85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
       96: "Thunderstorm", 99: "Thunderstorm"}


def weather():
    s = get_settings()
    city = s.get("weather_city")
    if not city:
        return {"configured": False}

    def fetch():
        lat, lon = s.get("weather_lat"), s.get("weather_lon")
        if lat is None or lon is None:
            try:
                geo = _http_json("https://geocoding-api.open-meteo.com/v1/search?count=1&name="
                                 + urllib.parse.quote(city))
            except Exception as e:
                return {"configured": True,
                        "error": "weather lookup failed (" + type(e).__name__ + ")"}
            hits = geo.get("results") or []
            if not hits:
                return {"configured": True, "error": f"city '{city}' not found"}
            lat, lon = hits[0]["latitude"], hits[0]["longitude"]
            s.update({"weather_lat": lat, "weather_lon": lon,
                      "weather_city": hits[0]["name"]})
            with _state_lock:
                write_json(SETTINGS_FILE, s)
        try:
            w = _http_json(f"https://api.open-meteo.com/v1/forecast?latitude={lat}"
                           f"&longitude={lon}&current=temperature_2m,weather_code"
                           f"&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
                           f"&forecast_days=1&temperature_unit=fahrenheit")
        except Exception:
            return {"configured": True, "error": "weather fetch failed"}
        cur = w.get("current", {})
        daily = w.get("daily", {})
        return {"configured": True, "city": s.get("weather_city", city),
                "temp": round(cur.get("temperature_2m", 0)),
                "desc": WMO.get(cur.get("weather_code"), "—"),
                "hi": round((daily.get("temperature_2m_max") or [0])[0]),
                "lo": round((daily.get("temperature_2m_min") or [0])[0])}

    return _cached("weather:" + city, 1200, fetch)


def macos_calendar():
    """Today's events from the macOS Calendar app via icalBuddy (if installed)."""
    def fetch():
        bud = shutil.which("icalBuddy") or "/opt/homebrew/bin/icalBuddy"
        if not os.path.exists(bud):
            return {"available": False, "reason": "icalBuddy not installed"}
        try:
            proc = subprocess.run(
                [bud, "-npn", "-nc", "-b", "|", "-nrd", "-ps", "/ ~ /",
                 "-iep", "datetime,title", "-po", "datetime,title",
                 "eventsToday"],
                capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            return {"available": False, "reason": "calendar read timed out (check Calendar permission)"}
        if proc.returncode != 0:
            err = (proc.stderr or "icalBuddy failed").strip()
            if "No calendars" in err:
                return {"available": False, "reason":
                        "macOS Calendar isn't readable yet — either grant access "
                        "(run `icalBuddy calendars` once in Terminal and allow the "
                        "prompt) or skip this; Google Calendar can fill this widget "
                        "once connected."}
            return {"available": False, "reason": err[:120]}
        events = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("|"):
                parts = [p.strip() for p in line[1:].split(" ~ ", 1)]
                if len(parts) == 2:
                    events.append({"time": parts[0], "title": parts[1]})
                elif parts and parts[0]:
                    events.append({"time": "", "title": parts[0]})
        return {"available": True, "events": events[:12]}

    return _cached("calendar", 300, fetch)


def recent_files():
    """Recently modified files in granted folders — computed directly, no LLM."""
    def scan():
        cutoff = time.time() - 48 * 3600
        out = []
        for root_dir in get_access()["dirs"]:
            budget = 4000  # entries per granted dir, keeps this instant
            for cur, dirs, files in os.walk(root_dir):
                dirs[:] = [d for d in dirs if not d.startswith(".")
                           and d not in ("node_modules", "__pycache__", "venv")]
                if cur.count(os.sep) - root_dir.count(os.sep) >= 3:
                    dirs[:] = []
                for fn in files:
                    if fn.startswith("."):
                        continue
                    budget -= 1
                    if budget <= 0:
                        break
                    p = os.path.join(cur, fn)
                    try:
                        mt = os.path.getmtime(p)
                    except OSError:
                        continue
                    if mt > cutoff:
                        out.append({"path": p, "name": fn, "mtime": mt,
                                    "dir": os.path.relpath(cur, os.path.expanduser("~"))})
                if budget <= 0:
                    break
        out.sort(key=lambda f: -f["mtime"])
        return out[:10]

    return _cached("recent", 120, scan)


def battery():
    try:
        out = subprocess.run(["/usr/bin/pmset", "-g", "batt"],
                             capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"(\d+)%;\s*(\w[\w ]*)", out)
        if m:
            return {"pct": int(m.group(1)), "state": m.group(2).strip()}
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# quick actions
# --------------------------------------------------------------------------

QUICK_ACTIONS = [
    {"label": "Plan my day",
     "prompt": "Help me plan my day. Look at whatever real data you have "
               "(calendar, email, our recent chats, granted folders) and propose "
               "a realistic schedule with priorities. Ask me at most one "
               "clarifying question if needed."},
    {"label": "What's new in my folders",
     "prompt": "Look through the folders I've granted you and tell me what "
               "changed in the last 48 hours — new or modified files, anything "
               "notable. Group by folder, keep it short."},
    {"label": "Find a file",
     "prompt": "I'm looking for a file but only half-remember it. Ask me what I "
               "remember (rough name, contents, when), then search my granted "
               "folders for the best matches."},
    {"label": "Summarize a document",
     "prompt": "I want you to summarize a document. If I haven't attached one "
               "yet, tell me to drag it into this chat, then read it from the "
               "inbox and give me a tight summary plus anything that needs action."},
    {"label": "Draft an email",
     "prompt": "Help me draft an email. Ask me: to whom, about what, and the "
               "tone. Then write a draft I can copy. Do not send anything."},
    {"label": "Weekly review",
     "prompt": "Give me a weekly review: what we worked on together this week "
               "(from our chat history and your memory), loose ends worth "
               "closing, and up to 3 suggestions for next week. Only real items."},
    {"label": "Clean-up suggestions",
     "prompt": "Look at my granted folders and suggest clean-up candidates: "
               "large files, obvious duplicates, old downloads. List them with "
               "sizes. Do NOT delete anything — just report."},
]


# --------------------------------------------------------------------------
# system status
# --------------------------------------------------------------------------

def system_status():
    disk = shutil.disk_usage("/")
    ram_pct = None
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
        page = 16384
        stats = dict(re.findall(r'^"?([\w -]+)"?:\s+(\d+)', vm, re.M))
        free = (int(stats.get("Pages free", 0)) +
                int(stats.get("Pages inactive", 0)) +
                int(stats.get("Pages purgeable", 0))) * page
        total = int(subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"],
                                   capture_output=True, text=True, timeout=3).stdout)
        ram_pct = round(100 * (1 - free / total))
    except Exception:
        pass
    try:
        cores = os.cpu_count() or 1
        cpu_pct = min(99, round(100 * os.getloadavg()[0] / cores))
    except OSError:
        cpu_pct = None
    return {
        "model_online": model_online(),
        "disk_free_gb": round(disk.free / 1e9),
        "disk_pct": round(100 * disk.used / disk.total),
        "ram_pct": ram_pct,
        "cpu_pct": cpu_pct,
        "uptime_min": round((time.time() - STARTED) / 60),
    }


# --------------------------------------------------------------------------
# modular widget registry — "ahead on everything" hub.
# Each provider returns JSON-serializable data; all are cached + wrapped in
# safe() by the aggregator so one bad widget never takes the hub down.
# Layout (which widgets, what order) lives in layout.json; settings per widget
# ride in settings.json so they survive restarts.
# --------------------------------------------------------------------------

LAYOUT_FILE = os.path.join(DATA, "layout.json")
NOTES_FILE = os.path.join(DATA, "notes.json")

DEFAULT_LAYOUT = ["clock", "weather", "markets", "system",
                  "messages", "tasks", "hackernews", "briefing",
                  "crypto", "agent_pulse", "recent", "folders"]


def _http_text(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-dashboard"})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        return r.read().decode("utf-8", "replace")


def get_layout():
    lay = read_json(LAYOUT_FILE, None)
    if not lay or not isinstance(lay.get("order"), list):
        lay = {"order": list(DEFAULT_LAYOUT)}
        write_json(LAYOUT_FILE, lay)
    # drop ids no longer in the catalog
    lay["order"] = [w for w in lay["order"] if w in WIDGETS]
    return lay


def save_layout(lay):
    with _state_lock:
        write_json(LAYOUT_FILE, lay)


# ---- individual widget data providers ------------------------------------

def w_hackernews():
    def fetch():
        ids = _http_json("https://hacker-news.firebaseio.com/v0/topstories.json")[:9]
        out = []
        for i in ids:
            try:
                it = _http_json(f"https://hacker-news.firebaseio.com/v0/item/{i}.json")
                out.append({"title": it.get("title", ""), "url": it.get("url")
                            or f"https://news.ycombinator.com/item?id={i}",
                            "score": it.get("score", 0),
                            "by": it.get("by", ""), "comments": it.get("descendants", 0)})
            except Exception:
                continue
        return {"stories": out}
    return _cached("hn", 600, fetch)


def w_rss():
    feeds = get_settings().get("rss_feeds") or [
        "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "https://www.theverge.com/rss/index.xml",
    ]

    def fetch():
        import xml.etree.ElementTree as ET
        items = []
        for url in feeds[:5]:
            try:
                root = ET.fromstring(_http_text(url))
            except Exception:
                continue
            # RSS <item> or Atom <entry>
            for el in root.iter():
                tag = el.tag.split("}")[-1]
                if tag in ("item", "entry"):
                    title = link = ""
                    for c in el:
                        ct = c.tag.split("}")[-1]
                        if ct == "title":
                            title = (c.text or "").strip()
                        elif ct == "link":
                            link = (c.get("href") or c.text or "").strip()
                    if title:
                        src = url.split("/")[2].replace("www.", "").replace("feeds.", "")
                        items.append({"title": title, "url": link, "source": src})
                    if len([x for x in items]) > 40:
                        break
        return {"items": items[:12]}
    return _cached("rss:" + "|".join(feeds), 900, fetch)


def w_markets():
    syms = get_settings().get("tickers") or ["SPY", "AAPL", "NVDA", "MSFT"]

    def one(sym):
        j = _http_json("https://query1.finance.yahoo.com/v8/finance/chart/"
                       + urllib.parse.quote(sym) + "?range=1d&interval=15m", timeout=6)
        res = j["chart"]["result"][0]
        meta = res["meta"]
        price = meta.get("regularMarketPrice")
        prev = meta.get("previousClose") or meta.get("chartPreviousClose") or price
        closes = [c for c in (res.get("indicators", {}).get("quote", [{}])[0]
                              .get("close") or []) if c is not None]
        chg = (price - prev) if (price is not None and prev) else 0
        pct = (chg / prev * 100) if prev else 0
        # anchor the sparkline at the previous close so its shape matches the
        # daily %; otherwise an overnight-gap move draws a contradictory line.
        spark = ([prev] + closes)[-31:] if prev else closes[-30:]
        return {"symbol": sym, "price": price, "chg": round(chg, 2),
                "pct": round(pct, 2), "spark": spark,
                "asof": meta.get("regularMarketTime"),
                "state": meta.get("marketState"),
                "name": meta.get("shortName") or sym}

    def fetch():
        out, asof = [], None
        for s in syms[:6]:
            try:
                q = one(s)
                out.append(q)
                asof = asof or q.get("asof")
            except Exception:
                out.append({"symbol": s, "error": True})
        return {"quotes": out, "asof": asof}
    return _cached("mkt:" + ",".join(syms), 300, fetch)


def w_crypto():
    coins = get_settings().get("coins") or ["bitcoin", "ethereum", "solana"]

    def fetch():
        j = _http_json("https://api.coingecko.com/api/v3/simple/price?ids="
                       + ",".join(coins) + "&vs_currencies=usd&include_24hr_change=true")
        out = []
        for c in coins:
            d = j.get(c)
            if d:
                out.append({"id": c, "price": d.get("usd"),
                            "pct": round(d.get("usd_24h_change") or 0, 2)})
        return {"coins": out}
    return _cached("crypto:" + ",".join(coins), 300, fetch)


def w_github():
    def fetch():
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
        j = _http_json("https://api.github.com/search/repositories?q=created:>"
                       + since + "&sort=stars&order=desc&per_page=8")
        out = []
        for r in j.get("items", [])[:8]:
            out.append({"name": r["full_name"], "url": r["html_url"],
                        "stars": r["stargazers_count"], "lang": r.get("language") or "",
                        "desc": (r.get("description") or "")[:90]})
        return {"repos": out}
    return _cached("gh_trending", 1800, fetch)


def _imsg_text(text, blob):
    """Recent macOS stores body in attributedBody (NSKeyedArchiver); when the
    plain text column is NULL, best-effort extract the string from the blob."""
    if text:
        return text
    if not blob:
        return ""
    try:
        raw = bytes(blob)
        marker = raw.find(b"NSString")
        if marker == -1:
            return ""
        seg = raw[marker + 8:marker + 8 + 2000]
        # skip class-table bytes to the first printable run
        out = bytearray()
        started = False
        for b in seg:
            if 32 <= b < 127 or b in (9, 10):
                out.append(b); started = True
            elif started and len(out) > 1:
                break
        s = out.decode("utf-8", "ignore").strip()
        return s if len(s) > 1 else ""
    except Exception:
        return ""


def w_messages():
    """Recent iMessages straight from the local Messages db (needs Full Disk
    Access for the dashboard process). Local-first: nothing leaves the Mac."""
    import sqlite3
    db = os.path.join(HOME, "Library", "Messages", "chat.db")
    if not os.path.exists(db):
        return {"available": False, "reason": "Messages database not found"}

    def fetch():
        uri = "file:" + urllib.parse.quote(db) + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.Error:
            return {"available": False, "grant": True,
                    "reason": "Can't open Messages — grant Full Disk Access"}
        try:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT m.text AS text, m.attributedBody AS body, "
                "m.is_from_me AS me, m.date AS d, h.id AS handle "
                "FROM message m LEFT JOIN handle h ON m.handle_id=h.ROWID "
                "ORDER BY m.date DESC LIMIT 40").fetchall()
        except sqlite3.Error as e:
            con.close()
            return {"available": False, "grant": True,
                    "reason": "Full Disk Access needed to read Messages"}
        con.close()
        seen, out = set(), []
        for r in rows:
            body = _imsg_text(r["text"], r["body"])
            if not body:
                continue
            who = "You" if r["me"] else (r["handle"] or "Unknown")
            key = who
            if key in seen:
                continue
            seen.add(key)
            # Apple epoch: nanoseconds since 2001-01-01
            ts = 978307200 + (r["d"] / 1e9 if r["d"] > 1e11 else r["d"])
            out.append({"who": who, "text": body[:120], "from_me": bool(r["me"]),
                        "ts": ts})
            if len(out) >= 7:
                break
        return {"available": True, "messages": out}
    return _cached("imsg", 45, fetch)


def w_agent_pulse():
    """What Hermes has been doing lately — recent sessions across platforms."""
    import sqlite3
    if not os.path.exists(STATE_DB):
        return {"events": []}

    def fetch():
        uri = "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.Error:
            return {"events": []}
        try:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT source, message_count, tool_call_count, started_at "
                "FROM sessions WHERE started_at IS NOT NULL "
                "ORDER BY started_at DESC LIMIT 8").fetchall()
        finally:
            con.close()
        return {"events": [{"source": r["source"] or "cli",
                            "msgs": r["message_count"] or 0,
                            "tools": r["tool_call_count"] or 0,
                            "ts": r["started_at"]} for r in rows]}
    return _cached("pulse", 45, fetch)


def w_worldclock():
    zones = get_settings().get("timezones") or [
        ["San Francisco", "America/Los_Angeles"], ["New York", "America/New_York"],
        ["London", "Europe/London"], ["Tokyo", "Asia/Tokyo"]]
    try:
        from zoneinfo import ZoneInfo
        import datetime
        out = []
        for label, tz in zones[:6]:
            try:
                now = datetime.datetime.now(ZoneInfo(tz))
                out.append({"label": label, "time": now.strftime("%H:%M"),
                            "day": now.strftime("%a")})
            except Exception:
                continue
        return {"zones": out}
    except Exception:
        return {"zones": []}


def w_reminders():
    def fetch():
        script = ('set out to ""\n'
                  'tell application "Reminders"\n'
                  'repeat with r in (reminders whose completed is false)\n'
                  'set out to out & (name of r) & linefeed\n'
                  'end repeat\nend tell\nreturn out')
        try:
            p = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=8)
        except Exception:
            return {"available": False}
        if p.returncode != 0:
            return {"available": False, "grant": True}
        items = [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]
        return {"available": True, "items": items[:10]}
    return _cached("reminders", 120, fetch)


def w_quicklinks():
    links = get_settings().get("quicklinks") or [
        {"label": "GitHub", "url": "https://github.com"},
        {"label": "Hacker News", "url": "https://news.ycombinator.com"},
        {"label": "Gmail", "url": "https://mail.google.com"},
        {"label": "Calendar", "url": "https://calendar.google.com"},
    ]
    return {"links": links}


def get_notes():
    return read_json(NOTES_FILE, {"text": ""})


def w_notes():
    return get_notes()


# ---- catalog: id -> metadata + provider ----------------------------------
# size: "tile" (compact), "card" (1 col), "wide" (2 col). client=True means
# the frontend renders it with no backend data (clock).

WIDGETS = {
    "clock":      {"title": "Clock", "icon": "clock", "size": "tile", "cat": "system", "client": True, "provider": None},
    "weather":    {"title": "Weather", "icon": "sun", "size": "tile", "cat": "life", "provider": weather},
    "battery":    {"title": "Battery", "icon": "battery", "size": "tile", "cat": "system", "provider": battery},
    "system":     {"title": "System", "icon": "cpu", "size": "tile", "cat": "system", "provider": system_status},
    "markets":    {"title": "Markets", "icon": "trend", "size": "tile", "cat": "markets", "provider": w_markets},
    "crypto":     {"title": "Crypto", "icon": "coin", "size": "tile", "cat": "markets", "provider": w_crypto},
    "worldclock": {"title": "World Clock", "icon": "globe", "size": "tile", "cat": "life", "provider": w_worldclock},
    "today":      {"title": "Today", "icon": "calendar", "size": "card", "cat": "life", "provider": macos_calendar},
    "tasks":      {"title": "Tasks", "icon": "check", "size": "card", "cat": "productivity", "provider": lambda: {"tasks": get_tasks()["tasks"]}},
    "reminders":  {"title": "Reminders", "icon": "bell", "size": "card", "cat": "productivity", "provider": w_reminders},
    "notes":      {"title": "Scratchpad", "icon": "note", "size": "card", "cat": "productivity", "provider": w_notes},
    "briefing":   {"title": "Briefing", "icon": "spark", "size": "wide", "cat": "agent", "provider": lambda: _briefing_payload()},
    "messages":   {"title": "Message Center", "icon": "chat", "size": "card", "cat": "comms", "provider": w_messages},
    "hackernews": {"title": "Tech News", "icon": "news", "size": "card", "cat": "news", "provider": w_hackernews},
    "rss":        {"title": "News Feed", "icon": "rss", "size": "card", "cat": "news", "provider": w_rss},
    "github":     {"title": "GitHub Trending", "icon": "code", "size": "card", "cat": "news", "provider": w_github},
    "agent_pulse":{"title": "Agent Pulse", "icon": "activity", "size": "card", "cat": "agent", "provider": w_agent_pulse},
    "quicklinks": {"title": "Quick Links", "icon": "link", "size": "card", "cat": "productivity", "provider": w_quicklinks},
    "recent":     {"title": "Recent Activity", "icon": "file", "size": "card", "cat": "productivity", "provider": lambda: {"recent": recent_files()}},
    "folders":    {"title": "Folder Access", "icon": "folder", "size": "card", "cat": "productivity", "provider": lambda: get_access()},
}


def _briefing_payload():
    with _state_lock:
        b = read_json(BRIEFING_FILE, {})
    return {"reply": b.get("reply", ""), "generated_at": b.get("generated_at"),
            "generating": _briefing_generating}


def widget_catalog():
    return {"catalog": [{"id": k, "title": v["title"], "icon": v["icon"],
                         "size": v["size"], "cat": v["cat"]}
                        for k, v in WIDGETS.items()]}


_HUB_POOL = None
_hub_last_good = {}          # wid -> last successful payload (stale beats blank)


def _hub_pool():
    global _HUB_POOL
    if _HUB_POOL is None:
        import concurrent.futures as _cf
        _HUB_POOL = _cf.ThreadPoolExecutor(max_workers=8,
                                           thread_name_prefix="hub")
    return _HUB_POOL


def _run_provider(wid, prov):
    try:
        d = prov()
        _hub_last_good[wid] = d
        return d
    except Exception as e:
        return _hub_last_good.get(wid, {"error": type(e).__name__})


def hub_prewarm_loop():
    """Keep every enabled widget's cache warm in the background so user-facing
    /api/hub is always instant — slow feeds (HN item fan-out, RSS, Yahoo)
    refresh off the request path."""
    time.sleep(3)
    while True:
        try:
            lay = get_layout()
            pool = _hub_pool()
            for wid in lay["order"]:
                meta = WIDGETS.get(wid)
                if meta and meta.get("provider"):
                    pool.submit(_run_provider, wid, meta["provider"])
        except Exception:
            pass
        time.sleep(45)


def hub_data():
    """Layout + data for every enabled widget. Providers run IN PARALLEL with
    a wall-clock budget — one slow feed can no longer stall the whole hub.
    Providers that miss the budget serve their last-good payload."""
    lay = get_layout()
    pool = _hub_pool()
    futs = {}
    for wid in lay["order"]:
        meta = WIDGETS.get(wid)
        if meta and meta.get("provider"):
            futs[wid] = pool.submit(_run_provider, wid, meta["provider"])
    deadline = time.time() + 3.0
    widgets = {}
    for wid in lay["order"]:
        meta = WIDGETS.get(wid)
        if not meta:
            continue
        data = {}
        f = futs.get(wid)
        if f is not None:
            try:
                data = f.result(timeout=max(0.1, deadline - time.time()))
            except Exception:
                data = _hub_last_good.get(wid, {"error": "slow"})
        widgets[wid] = {"meta": {"title": meta["title"], "icon": meta["icon"],
                                 "size": meta["size"], "cat": meta["cat"]},
                        "data": data}
    return {"order": lay["order"], "widgets": widgets,
            "model_online": model_online()}


# --------------------------------------------------------------------------
# model toggle — switch the local model the agent runs on. Seeded with
# verified MLX repo ids; the roster is user-extendable via models.json.
# Switching rewrites active-model (read by mlx-server.sh) + hermes config +
# restarts the mlx-server launchd service.
# --------------------------------------------------------------------------

MODELS_FILE = os.path.join(DATA, "models.json")
ACTIVE_MODEL_FILE = os.path.join(DATA, "active-model")
DEFAULT_MODEL = "mlx-community/Qwen3.8-27B-4bit"
PAUSE_FILE = os.path.join(DATA, "agent-paused")
MLX_LABEL = "com.hermes.mlx-server"
MLX_PLIST = os.path.join(HOME, "Library", "LaunchAgents", MLX_LABEL + ".plist")


def agent_paused():
    return os.path.exists(PAUSE_FILE)


def _mlx_primary_down(timeout=3.0):
    """Poll (up to `timeout`s) until the PRIMARY model server is genuinely gone.

    Not `_mlx_proc_alive()`: its pgrep pattern ("mlx_lm server|mlx-vlm-launch")
    matches BOTH lanes, so whenever the background 9B is up on :8081 it answers
    True forever and a pause could never confirm. These two signals are
    lane-specific — the launchd job being unloaded (`launchctl print` fails once
    the bootout took) and :8080 no longer answering. An unknown result (probe
    error) counts as STILL UP, because a false "paused" is by far the more
    expensive mistake: memory_guard_loop AND idle_suspend_loop both skip while
    paused, so claiming a pause that didn't happen leaves a live model with no
    memory watchdog and no idle reclaim."""
    uid = os.getuid()
    deadline = time.time() + timeout
    while True:
        try:
            loaded = subprocess.run(["launchctl", "print", f"gui/{uid}/{MLX_LABEL}"],
                                    capture_output=True, timeout=10).returncode == 0
        except Exception:
            loaded = True
        if not loaded and not model_online():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.4)


def agent_power(action):
    """Pause = bootout the model server (frees ALL its RAM; chat/Telegram
    replies are down until resume). Resume = bootstrap it back."""
    uid = os.getuid()
    if action == "pause":
        try:
            r = subprocess.run(["launchctl", "bootout", f"gui/{uid}/{MLX_LABEL}"],
                               capture_output=True, text=True, timeout=15)
        except Exception as e:
            return {"ok": False, "error": "bootout failed: %s: %s" % (type(e).__name__, e)}
        # The return code alone is not the answer: launchctl exits 3 ("No such
        # process") when the job was already unloaded, which IS the state we
        # want. So verify the end state and only write PAUSE_FILE once the
        # server is really gone — the marker is what makes memory_guard and the
        # idle loop stand down, so a pause file over a running model silently
        # disables both safety loops (the exact failure this check exists for).
        if not _mlx_primary_down():
            tail = " ".join(((r.stderr or "") + " " + (r.stdout or "")).split())[-200:]
            print(f"[agent_power] pause FAILED — model server still up after "
                  f"bootout (rc={r.returncode}): {tail}", file=sys.stderr, flush=True)
            return {"ok": False, "error": "bootout failed: "
                    + (tail or f"rc={r.returncode}, server still responding")}
        if r.returncode != 0:      # gone anyway (already unloaded) — note it
            print(f"[agent_power] bootout rc={r.returncode} but the server is "
                  f"gone: {' '.join((r.stderr or '').split())[-200:]}",
                  file=sys.stderr, flush=True)
        with open(PAUSE_FILE, "w") as f:
            f.write(str(time.time()))
        try:                       # a deliberate pause supersedes any idle-suspend
            os.remove(IDLE_SUSPEND_FILE)
        except OSError:
            pass
        _widget_cache.pop("sys_live", None)
        return {"ok": True, "paused": True}
    if action == "resume":
        for _pf in (PAUSE_FILE, IDLE_SUSPEND_FILE):   # clear both down-states
            try:
                os.remove(_pf)
            except OSError:
                pass
        try:
            started = _mlx_start(uid)
        except Exception as e:
            return {"ok": False, "error": "start failed: %s: %s" % (type(e).__name__, e)}
        if not started:
            # The down-state markers stay cleared: the user asked for the model
            # to run, and every wake path (chat turn, "Wake now") should keep
            # trying. What must NOT happen is answering `loading: True` for a
            # server launchd refused to start — that spins the UI forever.
            return {"ok": False, "paused": False,
                    "error": "launchctl would not start the model server "
                             "(see the dashboard log for the bootstrap/kickstart error)"}
        return {"ok": True, "paused": False, "loading": True}
    return {"ok": False, "error": "unknown action"}

_SEED_MODELS = [
    # Roster policy (2026-08-18, user call): TWO models only — plus, since
    # 2026-09-03 (user call), ONE opt-in alternative brain: the abliterated
    # Qwen3.8-27B below. Never the default; the user picks it in the model menu.
    #  * Qwen3.8-27B — the assistant's brain (primary lane :8080, mlx_vlm backend
    #    + native MTP speculative decoding ≈2x, APC prefix cache).
    #  * Qwen3.5-9B — the BACKGROUND lane (:8081, com.hermes.mlx-bg): all
    #    briefing / watchtower news+intel / For-You candidate passes go here so
    #    the 27B stays warm for the user. Same GatedDeltaNet family + chat
    #    template as 3.8 (XML tool calls, thinking control) — the only official
    #    Qwen3.8 sizes are 27B and a 2.4T MoE, so 3.5-9B is the nearest sibling.
    #    ~6GB, ~88 tok/s, 6/6 on the tool drill. Served via the mlx-vlm venv
    #    (its tokenizer_config uses transformers-5's TokenizersBackend, which
    #    mlx-lm's pinned transformers<5 cannot load).
    # Qwen3.8-27B (2026-08-14): dense 27B, hybrid GatedDeltaNet/full-attention
    # (model_type qwen3_5). Served by the mlx_vlm backend (isolated venv
    # ~/.hermes/mlx-vlm-venv, launcher mlx-vlm-launch.py) with its NATIVE MTP
    # drafter (`Qwen3.8-27B-MTP-bf16`, 0.9GB — the -MTP-4bit drafter ships NaN
    # weights, mlx-vlm #1931) → speculative decoding ≈2.0x on code / 1.5x prose
    # (measured M5 Max: 31 → 63/47 tok/s, block 3; block 6 is SLOWER than AR).
    # APC_ENABLED=1 gives exact prefix caching (18k-token system prompt: 26s
    # cold → 0.4s). mlx-lm 0.31.3 also loads it (qwen3_5 module) — that's the
    # fallback if the venv is missing. Thinking is ON by default in the chat
    # template at reasoning_effort=xhigh (~22k think tokens on trivial prompts),
    # far too slow for a tool loop → roster default enable_thinking=false; the
    # model-menu "Thinking" row flips it (low effort) and restarts the server.
    {"id": "mlx-community/Qwen3.8-27B-4bit", "label": "Qwen3.8-27B",
     "ram": 19, "note": "assistant brain · dense · MTP ~2x · Aug-2026",
     "thinking": True,
     "template_args": {"enable_thinking": False},
     "backend": "mlx_vlm",
     "draft_model": "mlx-community/Qwen3.8-27B-MTP-bf16",
     "draft_kind": "mtp", "draft_block_size": 3},
    # Qwen3.8-27B Uncensored (2026-09-03, user call — "the jailbroken Qwen3.8"):
    # orcarouter's abliterated build (refusal direction orthogonalized out of the
    # residual stream) of the SAME Qwen3.8-27B — MLX 4-bit affine g64, identical
    # layout / quant / Qwen2Tokenizer / chat template to the primary, so the
    # mlx_vlm backend, template_args and the Thinking toggle carry over as-is.
    # No guardrails: it answers what the stock model refuses — opt-in from the
    # model menu, never the default. Repo quirks encoded by roster fields:
    #  * ONE repo holds 2/4/6/8-bit SUBFOLDERS (95GB) plus a root mirror of the
    #    4-bit build → `ignore_patterns` keeps download_model() to root + mtp/
    #    (~17GB). `hf_offline` makes mlx-server.sh export HF_HUB_OFFLINE=1 for
    #    it — otherwise mlx_vlm's get_model_path() would snapshot_download the
    #    skipped 62GB of subfolders at every server start.
    #  * its native MTP drafter lives INSIDE the repo (`mtp/`, model_type
    #    qwen3_5_mtp — same shape as Qwen3.8-27B-MTP-bf16) → `draft_subfolder`;
    #    _draft_model_path() resolves it to the local snapshot path that
    #    mlx-server.sh passes as --draft-model. Block 3 = the size measured best
    #    for this architecture on the M5 Max (see the primary entry).
    {"id": "orcarouter/Qwen3.8-27B-Uncensored-MLX", "label": "Qwen3.8-27B Uncensored",
     "ram": 19, "note": "no refusals · abliterated 27B · MTP ~2x · Aug-2026",
     "thinking": True,
     "template_args": {"enable_thinking": False},
     "backend": "mlx_vlm",
     "draft_subfolder": "mtp", "draft_kind": "mtp", "draft_block_size": 3,
     "ignore_patterns": ["2-bit/*", "4-bit/*", "6-bit/*", "8-bit/*"],
     "hf_offline": True},
    {"id": "mlx-community/Qwen3.5-9B-4bit", "label": "Qwen3.5-9B",
     "ram": 7, "note": "background lane · news, scraping, briefings · fast",
     "role": "background", "thinking": True,
     "template_args": {"enable_thinking": False},
     "backend": "mlx_vlm"},
]

_model_dl = {}          # id -> "downloading" | "done" | "error"
# id -> the last download failure, human-readable. Cleared when a new attempt
# starts and on success. Exists because `_model_dl[mid] = "error"` on its own
# left the menu showing a dead "download failed" chip with no way to find out
# why (the reason was in the dashboard log at best, and for the commonest cause
# — a python without huggingface_hub — nowhere at all). models_payload() ships
# it as `download_error`, so a state of "error" always has a message with it.
_model_dl_err = {}


def _model_registry():
    reg = read_json(MODELS_FILE, None)
    if not reg or not isinstance(reg.get("models"), list):
        reg = {"models": list(_SEED_MODELS)}
        write_json(MODELS_FILE, reg)
    else:
        # migrate: seed entries added after models.json was first written are
        # merged in (by id, appended after the current default) so an existing
        # install sees new roster models without a reset. User edits win.
        have = {m.get("id") for m in reg["models"]}
        missing = [dict(m) for m in _SEED_MODELS if m["id"] not in have]
        dirty = False
        if missing:
            ins = 1 if reg["models"] and reg["models"][0].get("id") == DEFAULT_MODEL else 0
            reg["models"][ins:ins] = missing
            dirty = True
        # backfill keys a seed entry gained later (backend/draft_model/...) —
        # only ABSENT keys, so user edits win
        seeds = {m["id"]: m for m in _SEED_MODELS}
        for m in reg["models"]:
            sm = seeds.get(m.get("id"))
            if not sm:
                continue
            for k, v in sm.items():
                if k not in m:
                    m[k] = v
                    dirty = True
        if dirty:
            write_json(MODELS_FILE, reg)
    return reg["models"]


def _model_entry(mid):
    for m in _model_registry():
        if m.get("id") == mid:
            return m
    return None


# Per-model chat-template kwargs (mlx_lm server --chat-template-args). Written
# on switch from the roster entry's "template_args"; mlx-server.sh reads it.
TEMPLATE_ARGS_FILE = os.path.join(DATA, "chat-template-args")


# Per-model server backend (mlx-server.sh reads it): {"backend": "mlx_lm"|
# "mlx_vlm", "draft_model", "draft_kind", "draft_block_size", "enable_thinking",
# "reasoning_effort"}. mlx_vlm = isolated venv + mlx-vlm-launch.py (native MTP
# speculative decoding + APC prefix cache); anything else = python3 -m mlx_lm server.
SERVER_BACKEND_FILE = os.path.join(DATA, "server-backend")
MLX_VLM_VENV_PY = os.path.join(HOME, ".hermes", "mlx-vlm-venv", "bin", "python")


def _write_template_args(mid):
    m = _model_entry(mid) or {}
    ta = m.get("template_args")
    try:
        if isinstance(ta, dict) and ta:
            with open(TEMPLATE_ARGS_FILE, "w") as f:
                json.dump(ta, f)
        else:
            os.remove(TEMPLATE_ARGS_FILE)
    except OSError:
        pass
    # backend selection for mlx-server.sh
    backend = m.get("backend") or "mlx_lm"
    if backend == "mlx_vlm" and not os.path.exists(MLX_VLM_VENV_PY):
        backend = "mlx_lm"          # venv missing → mlx-lm still loads Qwen3.8
    cfg = {"backend": backend}
    if m.get("hf_offline"):
        cfg["hf_offline"] = True        # mlx-server.sh exports HF_HUB_OFFLINE=1
    if backend == "mlx_vlm":
        for k in ("draft_model", "draft_kind", "draft_block_size"):
            if m.get(k) is not None:
                cfg[k] = m[k]
        if m.get("draft_subfolder"):    # drafter shipped inside the repo
            dp = _draft_model_path(m)
            if dp:
                cfg["draft_model"] = dp         # absolute local snapshot path
            else:
                cfg.pop("draft_model", None)    # not local yet → plain AR
        ta = ta if isinstance(ta, dict) else {}
        cfg["enable_thinking"] = bool(ta.get("enable_thinking", True))
        if ta.get("reasoning_effort"):
            cfg["reasoning_effort"] = ta["reasoning_effort"]
    try:
        with open(SERVER_BACKEND_FILE, "w") as f:
            json.dump(cfg, f)
    except OSError:
        pass


def model_thinking_state(mid=None):
    """{supported, enabled} for the roster entry (thinking = chat template
    honours enable_thinking; enabled = what the server was started with)."""
    m = _model_entry(mid or active_model()) or {}
    if not m.get("thinking"):
        return {"supported": False, "enabled": False}
    ta = m.get("template_args") or {}
    return {"supported": True, "enabled": ta.get("enable_thinking", True) is not False}


def set_model_thinking(enabled, mid=None):
    """Flip enable_thinking for a thinking-capable roster model. Persists to
    models.json; if it's the active model, rewrites the args file and restarts
    the server (bootout→bootstrap, same as a switch) so it takes effect."""
    mid = mid or active_model()
    reg = _model_registry()
    m = next((x for x in reg if x.get("id") == mid), None)
    if not m or not m.get("thinking"):
        return {"ok": False, "error": "model has no thinking toggle"}
    ta = dict(m.get("template_args") or {})
    if enabled:
        ta["enable_thinking"] = True
        ta.setdefault("reasoning_effort", "low")   # xhigh default overthinks
    else:
        ta["enable_thinking"] = False
        ta.pop("reasoning_effort", None)
    m["template_args"] = ta
    write_json(MODELS_FILE, {"models": reg})
    if mid != active_model():
        return {"ok": True, "enabled": bool(enabled), "restarted": False}
    _write_template_args(mid)
    if agent_paused() or agent_idle_suspended():
        return {"ok": True, "enabled": bool(enabled), "restarted": False}
    # The setting IS persisted by now, so `enabled` rides along on every branch
    # — a failed restart means "saved, takes effect on the next start", not
    # "nothing happened". Never claim restarted:True on a False return: the UI
    # would show the loading swap and waitForModel against a server that is
    # never coming back.
    try:
        restarted = bool(_mlx_restart())
    except Exception as e:
        return {"ok": False, "error": f"restart failed: {e}", "enabled": bool(enabled)}
    if not restarted:
        return {"ok": False, "error": "restart failed — see dashboard log",
                "enabled": bool(enabled)}
    _widget_cache.pop("sys_live", None)
    return {"ok": True, "enabled": bool(enabled), "restarted": True, "loading": True}


def _hf_cache_dir(mid):
    return os.path.join(HOME, ".cache", "huggingface", "hub",
                        "models--" + mid.replace("/", "--"))


def _weights_complete(d):
    """Are the weights in model dir `d` (a snapshot root or a drafter subfolder)
    all present? HF materializes each snapshot symlink only when its blob
    finishes, so with a shard index every file in weight_map must exist; a
    single-file model just needs its .safetensors."""
    idx = os.path.join(d, "model.safetensors.index.json")
    try:
        if os.path.isfile(idx):
            with open(idx) as f:
                files = set((json.load(f).get("weight_map") or {}).values())
            return bool(files) and all(os.path.isfile(os.path.join(d, fn)) for fn in files)
        return any(f.endswith(".safetensors") for f in os.listdir(d))
    except (OSError, ValueError):
        return False


def _model_downloaded(mid):
    """Fully local? NOT 'any .safetensors under the cache dir' — that flipped
    True as soon as the first shard (or an in-repo drafter like orcarouter's
    0.85GB mtp/) landed, letting a switch start against missing shards."""
    snap = _hf_snapshot_dir(mid)
    return bool(snap) and _weights_complete(snap)


def _hf_snapshot_dir(mid):
    """The local snapshot dir of an HF repo in the hub cache, or None.

    refs/main FIRST — its content is the commit sha of the snapshot the hub
    cache considers current — and newest-mtime only as a fallback when the ref
    is missing or names a directory that isn't there. This mirrors
    `_patch_local_snapshot_resolution()` in mlx-vlm-launch.py exactly, and that
    agreement is the point: with `hf_offline` roster entries the LOADER resolves
    a repo id through refs/main while the dashboard used to pick newest-mtime,
    so after a re-download (or any touch of an older snapshot dir) the two could
    disagree about which snapshot "the" model is — `downloaded`, `_draft_ready`
    and the `--draft-model` path would then describe a different checkout than
    the server actually loads."""
    base = _hf_cache_dir(mid)
    snaps = os.path.join(base, "snapshots")
    try:
        with open(os.path.join(base, "refs", "main")) as f:
            sha = f.read().strip()
        # a sha is one path segment; refuse anything that could escape snapshots/
        if sha and "/" not in sha and ".." not in sha and \
                os.path.isdir(os.path.join(snaps, sha)):
            return os.path.join(snaps, sha)
    except OSError:
        pass
    try:
        cands = [os.path.join(snaps, d) for d in os.listdir(snaps)]
    except OSError:
        return None
    cands = [d for d in cands if os.path.isdir(d)]
    return max(cands, key=os.path.getmtime) if cands else None


def _draft_model_path(m):
    """What mlx-server.sh should pass as --draft-model for a roster entry: the
    draft_model repo id, or — when the drafter ships INSIDE a repo
    (`draft_subfolder`, e.g. orcarouter's `mtp/`) — the absolute path of that
    subfolder in the local snapshot (mlx_vlm's get_model_path takes local
    paths verbatim). None = no drafter / not downloaded yet."""
    sub = (m.get("draft_subfolder") or "").strip("/")
    if sub:
        snap = _hf_snapshot_dir(m.get("draft_model") or m.get("id") or "")
        p = os.path.join(snap, sub) if snap else None
        return p if p and os.path.isdir(p) else None
    return m.get("draft_model") or None


def _draft_ready(m):
    """True when the entry has no drafter, or its drafter weights are local."""
    if m.get("draft_subfolder"):
        p = _draft_model_path(m)
        return bool(p) and _weights_complete(p)
    dm = m.get("draft_model")
    return (not dm) or _model_downloaded(dm)


def _config_model_default():
    """Read model.default from config.yaml without a YAML dep (stdlib only)."""
    try:
        with open(os.path.join(HOME, ".hermes", "config.yaml")) as f:
            in_model = False
            for line in f:
                if re.match(r"^model:\s*$", line):
                    in_model = True
                    continue
                if in_model:
                    m = re.match(r"^\s+default:\s*(.+?)\s*$", line)
                    if m:
                        return m.group(1).strip().strip('"\'')
                    if re.match(r"^\S", line):
                        break
    except OSError:
        pass
    return None


def active_model():
    try:
        v = open(ACTIVE_MODEL_FILE).read().strip()
        if v:
            return v
    except OSError:
        pass
    return _config_model_default() or DEFAULT_MODEL


def models_payload():
    active = active_model()
    reg = _model_registry()
    # make sure the active model always appears in the list
    if active and not any(m["id"] == active for m in reg):
        reg = [{"id": active, "label": active.split("/")[-1], "ram": None,
                "note": "active"}] + reg
    out = []
    machine_gb = _machine_ram_gb()
    for m in reg:
        out.append({**m, "active": m["id"] == active,
                    "downloaded": _model_downloaded(m["id"]) and _draft_ready(m),
                    "downloading": _model_dl.get(m["id"]) == "downloading",
                    # 1.0.3: "will this run on MY Mac" — the roster's `ram` is
                    # the author's measured footprint, meaningless to a reader
                    # until it is put next to their own hardware. None when the
                    # entry has no `ram` (the injected active row); the menu
                    # then shows no fit line at all rather than a guess.
                    "fit": _model_fit(m.get("ram"), machine_gb),
                    # None unless the last attempt failed — pairs with
                    # _model_dl[id] == "error" so the UI never shows a bare
                    # failure state with nothing to explain it
                    "download_error": _model_dl_err.get(m["id"])})
    _down = agent_paused() or agent_idle_suspended()   # process not resident
    ram = None if _down else _cached("mlx_ram", 60, _mlx_footprint_gb)
    bgm = bg_model()
    return {"active": active, "models": out, "paused": agent_paused(),
            "thinking": model_thinking_state(active),
            "bg": {"model": bgm, "online": bg_online(),
                   "label": next((m.get("label") for m in reg if m.get("id") == bgm), bgm.split("/")[-1])},
            "idle_suspended": agent_idle_suspended(),
            "idle_enabled": idle_suspend_enabled(),
            "idle_min": _idle_min(),
            "ram_gb": ram,
            # prewarm-after-wake (backlog #1): {enabled, last_ms, last_at,
            # last_result}. aux_promotion rebinds models_payload() but only ADDS
            # keys to whatever the base returns, so this passes through.
            "prewarm": prewarm_payload(),
            # machine_gb: physical RAM (GiB). The menu pairs it with each row's
            # `ram` ("needs ~19 GB · this Mac has 64 GB") and it is also what
            # explains a soft_gb that is no longer the familiar 50 — on a 16GB
            # Air the ceiling derives to 12/14 (see _mem_ceilings).
            "mem": {"soft_gb": MLX_SOFT_GB, "hard_gb": MLX_HARD_GB,
                    "machine_gb": round(machine_gb),
                    "over": bool(ram and ram >= MLX_SOFT_GB),
                    "override": os.path.exists(MEM_OVERRIDE_FILE)}}


def switch_model(mid):
    ent = _model_entry(mid)
    if ent is None and mid != active_model():
        return {"ok": False, "error": "unknown model"}
    if not _model_downloaded(mid):
        return {"ok": False, "error": "model not downloaded yet"}
    # Same definition of "ready" the menu shows: models_payload() reports
    # `downloaded` as _model_downloaded AND _draft_ready, so without this the
    # switch would accept a model the UI itself lists as not downloaded — and
    # mlx-server.sh would then start with a --draft-model pointing at weights
    # that aren't there (or, for an in-repo drafter, at nothing at all).
    if ent is not None and not _draft_ready(ent):
        return {"ok": False, "error": "drafter not downloaded yet"}
    with open(ACTIVE_MODEL_FILE, "w") as f:
        f.write(mid)
    _write_template_args(mid)      # per-model chat-template kwargs (thinking)
    try:
        subprocess.run([HERMES, "config", "set", "model.default", mid],
                       capture_output=True, text=True, timeout=30, env=_hermes_env())
    except Exception:
        pass
    try:
        if agent_paused():          # switching while paused implies waking up
            rs = agent_power("resume") or {}
            if not rs.get("ok"):
                return {"ok": False, "active": mid,
                        "error": rs.get("error") or "could not resume the model server"}
        else:
            # Reliable model swap: `kickstart -k` does NOT dependably reload a
            # KeepAlive service — it kept serving the OLD model after the
            # active-model file changed. bootout fully stops the server (freeing
            # the old model's RAM), then bootstrap starts it fresh so
            # mlx-server.sh re-reads active-model and loads the new one.
            uid = os.getuid()
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{MLX_LABEL}"],
                           capture_output=True, timeout=15)
            time.sleep(3)  # launchd needs a beat after bootout (avoids error 5)
            if not _mlx_start(uid):
                # active-model / template-args / hermes model.default are
                # already written, so the switch itself stands — but say the
                # server didn't come up instead of answering `loading: True`
                # and leaving the UI polling /api/health forever.
                return {"ok": False, "active": mid,
                        "error": "model server failed to start after the switch "
                                 "(see the dashboard log for the launchctl error)"}
    except Exception as e:
        return {"ok": False, "error": f"restart failed: {e}"}
    _widget_cache.pop("sys_live", None)
    return {"ok": True, "active": mid, "loading": True}


_HF_PY = None


def _hf_python():
    """Interpreter for huggingface_hub downloads, or None if there isn't one.
    The dashboard runs on Homebrew python (no hf hub → menu downloads failed
    silently); the mlx-vlm venv always has it, the framework python (mlx-lm's
    home) usually does.

    Returns None rather than falling back to sys.executable: handing back an
    interpreter that provably cannot `import huggingface_hub` only moved the
    failure one step later, into a subprocess whose ModuleNotFoundError went to
    a log nobody reads. Only a SUCCESSFUL probe is memoized — a negative result
    is re-probed, so installing the venv fixes downloads without restarting the
    dashboard (the probe is a handful of sub-second subprocesses, and it only
    runs on the path that is already broken)."""
    global _HF_PY
    if _HF_PY:
        return _HF_PY
    cands = [MLX_VLM_VENV_PY,
             "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
             shutil.which("python3"), sys.executable]
    for py in cands:
        if not py or not os.path.exists(py):
            continue
        try:
            if subprocess.run([py, "-c", "import huggingface_hub"],
                              capture_output=True, timeout=30).returncode == 0:
                _HF_PY = py
                return py
        except Exception:
            continue
    return None


def download_model(mid):
    # Validate the id like switch_model does — this route took an arbitrary
    # string straight from the request body into snapshot_download(), so a typo
    # (or anything else) started a real multi-GB pull of a repo that is not on
    # the roster and can never be switched to.
    if _model_entry(mid) is None:
        return {"ok": False, "error": "unknown model"}
    if _model_dl.get(mid) == "downloading":
        return {"ok": True, "status": "downloading"}
    # Resolve the interpreter BEFORE the thread so a missing huggingface_hub is
    # an immediate, actionable answer to the click instead of an "error" chip
    # that appears seconds later with nothing behind it.
    py = _hf_python()
    if not py:
        _model_dl[mid] = "error"
        _model_dl_err[mid] = "no interpreter with huggingface_hub (venv missing?)"
        print(f"[models] download {mid}: {_model_dl_err[mid]}",
              file=sys.stderr, flush=True)
        return {"ok": False, "error": _model_dl_err[mid]}

    def run():
        _model_dl[mid] = "downloading"
        _model_dl_err.pop(mid, None)      # a new attempt clears the old reason
        try:
            ent = _model_entry(mid) or {}
            # (repo, snapshot_download kwargs) jobs. Roster hints:
            #  allow_patterns / ignore_patterns — scope for multi-variant repos
            #  (orcarouter: 2/4/6/8-bit subfolders + a root mirror → root + mtp/)
            #  draft_model (+ draft_subfolder) — a separate-repo drafter rides
            #  along; a same-repo subfolder drafter comes with the main download.
            jobs = [(mid, {k: ent[k] for k in ("allow_patterns", "ignore_patterns")
                           if isinstance(ent.get(k), list) and ent[k]})]
            dm, sub = ent.get("draft_model"), ent.get("draft_subfolder")
            if dm and dm != mid:
                jobs.append((dm, {"allow_patterns": [sub.strip("/") + "/*"]} if sub else {}))
            errs = []
            for _id, kw in jobs:
                r = subprocess.run(
                    [py, "-c",
                     "import json, sys; from huggingface_hub import snapshot_download;"
                     "snapshot_download(**json.loads(sys.argv[1]))",
                     json.dumps({"repo_id": _id, **kw})],
                    capture_output=True, text=True, timeout=7200, env=_hermes_env())
                if r.returncode != 0:
                    tail = " ".join((r.stderr or "").split())[-300:]
                    errs.append(f"{_id}: {tail or 'exit %d' % r.returncode}")
                    print(f"[models] download {_id} failed (rc={r.returncode}): "
                          f"{(r.stderr or '')[-600:]}", file=sys.stderr, flush=True)
            ok = _model_downloaded(mid) and _draft_ready(ent)
            _model_dl[mid] = "done" if ok else "error"
            if ok:
                _model_dl_err.pop(mid, None)
            else:
                # every "error" state carries a reason — including the subtle
                # one where each subprocess exited 0 but the weights are still
                # incomplete (partial mirror, ignore_patterns too broad)
                _model_dl_err[mid] = ("; ".join(errs))[:600] if errs else (
                    "download finished but the weights are still incomplete "
                    "(missing shards or drafter)")
        except Exception as e:
            print(f"[models] download {mid} crashed: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
            _model_dl[mid] = "error"
            _model_dl_err[mid] = f"{type(e).__name__}: {e}"[:600]
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "status": "downloading"}


def add_model(mid, label=None):
    reg = _model_registry()
    if any(m["id"] == mid for m in reg):
        return {"ok": True}
    reg.append({"id": mid, "label": label or mid.split("/")[-1],
                "ram": None, "note": "added"})
    write_json(MODELS_FILE, {"models": reg})
    return {"ok": True}


# --------------------------------------------------------------------------
# Aux-module route registry.  Phase-1+ features live in self-contained
# exec-included modules (aux_*.py) that register their own HTTP routes here
# instead of editing the 2400-line dispatch chain below.  A module calls
# register_get("/api/foo", handler) / register_post("/api/bar", handler);
# handlers receive (ctx) with .query (parsed GET query dict), .body (parsed
# JSON for POST), and return a (obj[, status]) tuple or a plain dict.
# --------------------------------------------------------------------------
GET_ROUTES = {}
POST_ROUTES = {}


def register_get(path, fn):
    GET_ROUTES[path] = fn


def register_post(path, fn):
    POST_ROUTES[path] = fn


class RouteCtx:
    """Passed to aux route handlers — thin, avoids leaking the raw handler."""
    __slots__ = ("query", "body", "raw_path")

    def __init__(self, query=None, body=None, raw_path=""):
        self.query = query or {}
        self.body = body if body is not None else {}
        self.raw_path = raw_path

    def q1(self, key, default=""):
        """First value of a GET query param."""
        v = self.query.get(key)
        return v[0] if isinstance(v, list) and v else default


def _check_header(name, value):
    """Reject a header name/value that could split the response.  Raises.

    BaseHTTPRequestHandler.send_header does no validation: a CR or LF anywhere
    in either half ends the header line early and everything after it is read
    by the browser as further headers — or, after a blank line, as a second
    response body.  Every RawResponse header today is built from data the USER
    controls (aux_convos' Content-Disposition carries a conversation TITLE),
    so "the caller sanitises it" is exactly the assumption that eventually
    fails silently.  Refuse here instead, once, for every caller and every
    header a future aux module invents.
    """
    for part, what in ((name, "name"), (value, "value")):
        s = part if isinstance(part, str) else str(part)
        if "\r" in s or "\n" in s:
            raise ValueError("illegal CR/LF in response header %s: %r"
                             % (what, s))
    return value


class RawResponse:
    """What an aux route returns when the answer is NOT JSON — a file
    download, text/markdown, csv.  _dispatch_aux writes body + headers
    verbatim instead of json-encoding.  (Added for /api/sessions/export in
    aux_convos.py, which has to send Content-Disposition.)

    Headers are validated HERE, at construction, because that is still inside
    the aux handler call `_dispatch_aux` wraps in try/except — so a bad header
    becomes a clean 500 JSON error.  By the time the headers are written the
    status line is already on the wire and nothing can be salvaged."""
    __slots__ = ("body", "content_type", "headers", "status")

    def __init__(self, body, content_type="text/plain; charset=utf-8",
                 headers=None, status=200):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.content_type = _check_header("Content-Type", content_type)
        self.headers = dict(headers or {})   # copy: caller can't mutate ours
        for k, v in self.headers.items():
            _check_header(k, v)
        self.status = status


# Rich per-widget providers built by the widget agent wave live in a sibling
# file, exec'd into THIS namespace so they can use all the helpers above.
# It ends with EXPANDERS.update({...}), overriding e.g. markets with the
# richer indices+watchlist version.  aux_*.py modules (Phase-1 features) load
# afterwards, in sorted order, and register their routes via register_get/post.
_AUX_FILES = ["expanders_extra.py"] + sorted(
    f for f in os.listdir(HERE)
    if f.startswith("aux_") and f.endswith(".py"))
for _auxf in _AUX_FILES:
    _auxp = os.path.join(HERE, _auxf)
    if not os.path.exists(_auxp):
        continue
    try:
        with open(_auxp) as _f:
            exec(_f.read(), globals())
    except Exception as _e:  # never let an aux file take the hub down
        print(f"[{_auxf}] failed to load: {type(_e).__name__}: {_e}",
              file=sys.stderr)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
# SAME-ORIGIN GUARD (2026-09-03) — the only access control this API has.
#
# Threat model. The hub binds 127.0.0.1:7788 with no auth token, no session
# cookie and no CSRF token, and nearly every route changes real state:
# /api/chat runs the agent, /api/access grants the agent a folder,
# /api/shortcuts/run executes, /api/config/import overwrites config. Two ways
# an arbitrary web page the user happens to visit reaches all of that:
#   1. CSRF. A cross-site `fetch()` with Content-Type: text/plain is a "simple
#      request" — no preflight, so the browser just sends it. The attacker
#      never needs to READ the (CORS-less) response: the side effect has
#      already happened.
#   2. DNS rebinding. evil.example answers 127.0.0.1 on its second lookup, so
#      the attacker's page becomes genuinely same-origin with the hub and can
#      read every GET as well.
# The two matching defences, both free and token-less:
#   * HOST must be a loopback name we actually serve on. A rebound request
#     still carries `Host: evil.example`, so this closes rebinding — and it is
#     applied to `/` and the static JS too, so a rebound page cannot even load
#     the app shell to drive the API from inside.
#   * ORIGIN, whenever the browser sends one, must be one of our own origins.
#     Browsers always attach Origin to cross-origin fetch/XHR and to POSTs, so
#     "no Origin header" reliably means "not a browser": curl, the launchd
#     scripts and the Swift app's MessagesSync URLSession POST to
#     /api/messages/ingest keep working untouched. That is what buys CSRF
#     protection without a token. We apply it to GET as well as POST — no
#     third-party page has any business reading this API, and several GETs
#     (/api/expand, /api/hub, /api/history) return the user's private data.
#     A literal `Origin: null` (sandboxed iframe, file://) fails the check.
#   * SEC-FETCH-SITE: cross-site is refused on state-changing verbs as a second
#     line for browsers that send the metadata header. It is deliberately NOT
#     applied to GET: a plain cross-site NAVIGATION (the user clicking a link
#     to the dashboard from any other page) sends exactly that header with no
#     Origin, and refusing it would stop them opening their own hub.
# NO CORS headers are added anywhere — nothing off-origin should ever be
# granted read access. Extra hostnames (a tunnel, a second bind) go in the
# comma-separated env var HERMES_DASH_ALLOWED_HOSTS.
# --------------------------------------------------------------------------
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _build_allowed_hosts():
    """Loopback names x {with :DASH_PORT, without} + HERMES_DASH_ALLOWED_HOSTS.
    Both forms are needed because a Host header may legally omit the port only
    when it is the scheme default, but clients (and the WKWebView) vary."""
    names = ["127.0.0.1", "localhost", "[::1]"]
    names += [x.strip() for x in
              os.environ.get("HERMES_DASH_ALLOWED_HOSTS", "").split(",") if x.strip()]
    out = set()
    for n in names:
        n = n.lower()
        out.add(n)
        if not (":" in n and not n.endswith("]")):   # no explicit port yet
            out.add("%s:%d" % (n, DASH_PORT))
    return out


ALLOWED_HOSTS = _build_allowed_hosts()


def _hdr(headers, name):
    """Case-insensitive header read that works for both the handler's
    email.message.Message and a plain dict (so _request_allowed stays a pure,
    directly unit-testable function)."""
    if headers is None:
        return None
    try:
        v = headers.get(name)
    except Exception:
        v = None
    if v is None:
        low = name.lower()
        try:
            for k, val in headers.items():
                if str(k).lower() == low:
                    return val
        except Exception:
            pass
    return v


def _request_allowed(method, headers):
    """(ok, reason) for one request. Pure: no I/O, no handler state — the whole
    decision lives here so it can be tested without standing up a server."""
    host = (_hdr(headers, "Host") or "").strip().lower()
    if not host or host not in ALLOWED_HOSTS:
        return False, "forbidden host"
    origin = (_hdr(headers, "Origin") or "").strip()
    if origin:
        try:
            u = urllib.parse.urlsplit(origin)
        except ValueError:
            return False, "cross-origin request refused"
        if u.scheme != "http" or (u.netloc or "").lower() not in ALLOWED_HOSTS:
            return False, "cross-origin request refused"
    if (method or "").upper() not in _SAFE_METHODS:
        if (_hdr(headers, "Sec-Fetch-Site") or "").strip().lower() == "cross-site":
            return False, "cross-origin request refused"
    return True, ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _guard(self):
        """Pre-dispatch gate for EVERY verb. Returns False after having already
        answered 403, so callers just `if not self._guard(): return`."""
        ok, reason = _request_allowed(self.command, self.headers)
        if ok:
            return True
        print("[guard] refused %s %s host=%r origin=%r sec-fetch-site=%r (%s)"
              % (self.command, self.path, self.headers.get("Host"),
                 self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site"),
                 reason), file=sys.stderr, flush=True)
        self._json({"error": reason}, 403)
        return False

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except json.JSONDecodeError:
            return {}

    # ---- GET ----
    def do_GET(self):
        if not self._guard():          # Host/Origin check covers / and static too
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    body = f.read()
            except FileNotFoundError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path in ("/motion.min.js", "/expand.js") or \
                (path.startswith("/aux_") and path.endswith(".js")
                 and "/" not in path[1:]):
            try:
                with open(os.path.join(HERE, path.lstrip("/")), "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control",
                             "max-age=86400" if path == "/motion.min.js" else "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/health":
            self._json({"model_online": model_online(),
                        "hermes_found": os.path.exists(HERMES),
                        "hermes_path": HERMES})
        elif path == "/api/chat/poll":
            q = urllib.parse.parse_qs(parsed.query)
            job = CHAT_JOBS.get((q.get("job") or [""])[0])
            if not job:
                self._json({"ok": False, "gone": True}, 404)
            else:
                self._json({"ok": True, "state": job["state"],
                            "text": job["text"], "status": job["status"],
                            "approval": job["approval"], "done": job["done"],
                            "reply": job["reply"], "err": not job["ok"],
                            # aux_autoroute: Claude auto-escalation for this turn
                            "deep": job.get("deep")})
        elif path == "/api/status":
            self._json(system_status())
        elif path == "/api/widgets":
            # every widget individually fault-tolerant: one failure must never
            # take the whole endpoint (and thus the whole hub) down
            def safe(fn, fallback=None):
                try:
                    return fn()
                except Exception:
                    return fallback
            self._json({
                "system": safe(system_status, {}),
                "battery": safe(battery),
                "weather": safe(weather, {"configured": True, "error": "weather unavailable"}),
                "calendar": safe(macos_calendar, {"available": False, "reason": "calendar unavailable"}),
                "recent": safe(recent_files, []),
                "tasks": safe(lambda: get_tasks()["tasks"], []),
                "inbox_count": safe(lambda: len([f for f in os.listdir(INBOX)
                                                 if not f.startswith(".")]), 0),
            })
        elif path == "/api/capabilities":
            self._json(capabilities())
        elif path == "/api/console":
            self._json(console_activity())
        elif path == "/api/expand":
            q = urllib.parse.parse_qs(parsed.query)
            self._json(widget_expand((q.get("id") or [""])[0]))
        elif path == "/api/markets/search":
            q = urllib.parse.parse_qs(parsed.query)
            fn = globals().get("market_search")
            self._json(fn((q.get("q") or [""])[0]) if fn else {"results": []})
        elif path == "/api/mind_extra":
            fn = globals().get("mind_extra")
            self._json(fn() if fn else {})
        elif path == "/api/hub":
            self._json(hub_data())
        elif path == "/api/sys":
            self._json(_cached("sys_live", 2, system_status))
        elif path == "/api/models":
            self._json(models_payload())
        elif path == "/api/model/mem_free/status":
            # progress/outcome of the last POST /api/model/mem_free restart
            self._json(_mem_free_status())
        elif path == "/api/catalog":
            self._json(widget_catalog())
        elif path == "/api/briefing":
            cached = read_json(BRIEFING_FILE, {})
            cached["generating"] = _briefing_generating
            self._json(cached if cached.get("reply") else
                       {"ok": False, "reply": "", "generating": _briefing_generating})
        elif path == "/api/actions":
            self._json({"actions": QUICK_ACTIONS})
        elif path == "/api/access":
            self._json(get_access())
        elif path == "/api/sessions":
            self._json({"sessions": list_sessions()})
        elif path == "/api/agent/prewarm":
            # prewarm-after-wake state (also mirrored in /api/models.prewarm)
            self._json({"ok": True, **prewarm_payload()})
        elif path == "/api/history":
            qs = urllib.parse.parse_qs(parsed.query)
            sid = (qs.get("session") or [""])[0]
            # PREWARM_SESSION is a reserved key, not a conversation: the prewarm
            # turn writes no chat file, but refuse it by name so it can never be
            # read back even if something ever leaves one behind.
            if not SESSION_RE.match(sid) or sid == PREWARM_SESSION:
                self._json({"messages": [], "title": ""})
                return
            self._json(load_chat(sid))
        elif path in GET_ROUTES:
            self._dispatch_aux(GET_ROUTES[path],
                               RouteCtx(query=urllib.parse.parse_qs(parsed.query),
                                        raw_path=path))
        else:
            self.send_error(404)

    def _dispatch_aux(self, fn, ctx):
        """Run an aux route handler; normalise its return to a JSON response."""
        try:
            res = fn(ctx)
        except Exception as e:
            self._json({"ok": False, "error": type(e).__name__ + ": " + str(e)}, 500)
            return
        if isinstance(res, RawResponse):     # non-JSON body (download, md, csv)
            # Re-check before the status line goes out. RawResponse.__init__
            # already validated, but `headers` is a plain dict a handler can
            # still write to after constructing the response — and once
            # send_response() has run, a bad header can only be answered with a
            # split response, never with an error. So: validate, THEN commit.
            try:
                _check_header("Content-Type", res.content_type)
                for k, v in res.headers.items():
                    _check_header(k, v)
            except ValueError as e:
                self._json({"ok": False, "error": "ValueError: " + str(e)}, 500)
                return
            self.send_response(res.status)
            self.send_header("Content-Type", res.content_type)
            self.send_header("Content-Length", str(len(res.body)))
            for k, v in res.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(res.body)
        elif isinstance(res, tuple) and len(res) == 2:
            self._json(res[0], res[1])
        else:
            self._json(res if res is not None else {})

    # ---- POST ----
    def do_POST(self):
        if not self._guard():          # + Sec-Fetch-Site on state-changing verbs
            return
        path = urllib.parse.urlparse(self.path).path

        if path in POST_ROUTES:
            self._dispatch_aux(POST_ROUTES[path],
                               RouteCtx(body=self._body_json(), raw_path=path))
            return

        if path == "/api/briefing/refresh":
            threading.Thread(target=_generate_briefing, daemon=True).start()
            self._json({"generating": True})
            return

        if path == "/api/access":
            data = self._body_json()
            op = data.get("op")
            p = os.path.expanduser((data.get("path") or "").strip())
            acc = get_access()
            if op == "add":
                if not os.path.isdir(p):
                    self._json({"ok": False, "error": "Not a folder: " + p}, 400)
                    return
                p = os.path.realpath(p)
                if p not in acc["dirs"]:
                    acc["dirs"].append(p)
            elif op == "remove":
                acc["dirs"] = [d for d in acc["dirs"] if d != p]
            else:
                self._json({"ok": False, "error": "bad op"}, 400)
                return
            with _state_lock:
                write_json(ACCESS_FILE, acc)
            self._json({"ok": True, "dirs": acc["dirs"]})
            return

        if path == "/api/upload":
            name = os.path.basename(self.headers.get("X-Filename", "upload.bin"))
            name = re.sub(r"[^\w.\- ]", "_", name) or "upload.bin"
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n > 200 * 1024 * 1024:
                self._json({"ok": False, "error": "file too large (200MB max)"}, 413)
                return
            dest = os.path.join(INBOX, name)
            base, ext = os.path.splitext(dest)
            i = 1
            while os.path.exists(dest):
                dest = f"{base}-{i}{ext}"
                i += 1
            with open(dest, "wb") as f:
                remaining = n
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            self._json({"ok": True, "path": dest})
            return

        if path == "/api/tasks":
            data = self._body_json()
            op = data.get("op")
            t = get_tasks()
            if op == "add":
                text = (data.get("text") or "").strip()[:300]
                if text:
                    t["tasks"].insert(0, {"id": f"{time.time():.0f}-{len(t['tasks'])}",
                                          "text": text, "done": False,
                                          "ts": time.time()})
            elif op == "toggle":
                for task in t["tasks"]:
                    if task["id"] == data.get("id"):
                        task["done"] = not task["done"]
            elif op == "delete":
                t["tasks"] = [x for x in t["tasks"] if x["id"] != data.get("id")]
            elif op == "clear_done":
                t["tasks"] = [x for x in t["tasks"] if not x.get("done")]
            else:
                self._json({"ok": False}, 400)
                return
            save_tasks(t)
            self._json({"ok": True, "tasks": t["tasks"]})
            return

        if path == "/api/settings":
            data = self._body_json()
            s = get_settings()
            if "weather_city" in data:
                s["weather_city"] = (data["weather_city"] or "").strip()[:80]
                s.pop("weather_lat", None)
                s.pop("weather_lon", None)
            # widget config: list-valued settings (tickers, coins, rss_feeds,
            # quicklinks, timezones) accepted as-is so widgets are customizable
            for key in ("tickers", "coins", "rss_feeds", "quicklinks", "timezones",
                        "starred_tickers", "news_feeds"):
                if key in data and isinstance(data[key], list):
                    s[key] = data[key][:20]
            _widget_cache.clear()
            with _state_lock:
                write_json(SETTINGS_FILE, s)
            self._json({"ok": True})
            return

        if path == "/api/layout":
            data = self._body_json()
            op, wid = data.get("op"), data.get("id", "")
            lay = get_layout()
            order = lay["order"]
            if op == "add" and wid in WIDGETS and wid not in order:
                order.append(wid)
            elif op == "remove" and wid in order:
                order.remove(wid)
            elif op == "move" and wid in order:
                i = order.index(wid)
                j = i + (1 if data.get("dir") == "down" else -1)
                if 0 <= j < len(order):
                    order[i], order[j] = order[j], order[i]
            elif op == "set" and isinstance(data.get("order"), list):
                order[:] = [w for w in data["order"] if w in WIDGETS]
            else:
                self._json({"ok": False}, 400)
                return
            lay["order"] = order
            save_layout(lay)
            self._json({"ok": True, "order": order})
            return

        if path == "/api/notes":
            text = (self._body_json().get("text") or "")[:8000]
            with _state_lock:
                write_json(NOTES_FILE, {"text": text})
            self._json({"ok": True})
            return

        if path == "/api/agent/pause":
            self._json(agent_power("pause"))
            return
        if path == "/api/agent/resume":
            self._json(agent_power("resume"))
            return
        if path == "/api/agent/wake":
            # explicit wake from an idle-suspend (async; UI polls /api/models).
            # A no-op if the model is a deliberate pause or already awake.
            if agent_idle_suspended():
                threading.Thread(target=agent_wake, kwargs={"wait": True},
                                 daemon=True).start()
                self._json({"ok": True, "waking": True})
            else:
                self._json({"ok": True, "waking": False,
                            "paused": agent_paused()})
            return
        if path == "/api/agent/idle_config":
            # toggle idle-suspend on/off and/or set the minutes threshold.
            d = self._body_json()
            if "enabled" in d:
                try:
                    if d.get("enabled"):
                        if os.path.exists(IDLE_SUSPEND_OFF):
                            os.remove(IDLE_SUSPEND_OFF)
                    else:
                        open(IDLE_SUSPEND_OFF, "w").close()
                except OSError:
                    pass
            if d.get("minutes") is not None:
                try:
                    mins = float(d["minutes"])
                    if mins >= 1:
                        with open(IDLE_MIN_FILE, "w") as f:
                            f.write(str(mins))
                except (TypeError, ValueError):
                    pass
            self._json({"ok": True, "enabled": idle_suspend_enabled(),
                        "minutes": _idle_min()})
            return
        if path == "/api/agent/prewarm":
            # {"enabled": bool} — turn the after-wake prefix warm-up on/off.
            # Strict about the key being present: a body that forgot it must
            # not silently switch the feature off.
            d = self._body_json()
            if "enabled" not in d:
                self._json({"ok": False,
                            "error": "missing 'enabled' (bool)"}, 400)
                return
            try:
                set_prewarm_enabled(d.get("enabled"))
            except Exception as e:
                self._json({"ok": False,
                            "error": f"{type(e).__name__}: {e}"}, 500)
                return
            self._json({"ok": True, **prewarm_payload()})
            return
        if path == "/api/model/mem_override":
            # user's "allow it" escape hatch: touch/remove the override file so
            # mlx_admission stops refusing work while memory is over the ceiling.
            allow = bool(self._body_json().get("allow"))
            try:
                if allow:
                    open(MEM_OVERRIDE_FILE, "w").close()
                elif os.path.exists(MEM_OVERRIDE_FILE):
                    os.remove(MEM_OVERRIDE_FILE)
            except OSError as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, "override": allow,
                        "soft_gb": MLX_SOFT_GB, "hard_gb": MLX_HARD_GB})
            return
        if path == "/api/model/mem_free":
            # manual "clear the cache now" — reliable restart frees the balloon.
            # Answers whether a restart STARTED; poll /api/model/mem_free/status
            # for whether it worked (the old fire-and-forget always said yes).
            self._json(_mem_free_start())
            return
        if path == "/api/models/switch":
            self._json(switch_model((self._body_json().get("id") or "").strip()))
            return
        if path == "/api/models/download":
            self._json(download_model((self._body_json().get("id") or "").strip()))
            return
        if path == "/api/models/thinking":
            d = self._body_json()
            self._json(set_model_thinking(bool(d.get("enabled")), d.get("id")))
            return
        if path == "/api/models/add":
            d = self._body_json()
            self._json(add_model((d.get("id") or "").strip(), d.get("label")))
            return

        if path == "/api/sessions/delete":
            sid = self._body_json().get("session", "")
            if SESSION_RE.match(sid) and os.path.exists(chat_path(sid)):
                os.remove(chat_path(sid))
            self._json({"ok": True})
            return

        if path == "/api/chat":
            data = self._body_json()
            message = (data.get("message") or "").strip()
            session = (data.get("session") or "").strip()
            attachments = data.get("attachments") or []
            if not message or not SESSION_RE.match(session):
                self._json({"ok": False, "reply": "Empty message or bad session id."}, 400)
                return

            if agent_paused():
                self._json({"ok": False, "reply":
                            "The agent is paused to save memory. Resume it from "
                            "the model menu (top right), then send this again."})
                return

            _adm_ok, _adm_gb, _adm_lim = mlx_admission()
            if not _adm_ok:
                self._json({"ok": False, "reply":
                            f"The model is using {_adm_gb:.0f}GB of memory "
                            f"(ceiling {_adm_lim:.0f}GB), so I've paused new "
                            "requests to keep your Mac stable. It should recover "
                            "in a moment — resend shortly. To force it through, "
                            "hit 'Allow over-limit' in the model menu."})
                return

            chat = load_chat(session)
            chat["messages"].append({"role": "user", "text": message, "ts": time.time()})
            if not chat.get("title"):
                chat["title"] = message[:48]
            save_chat(session, chat)

            prompt = access_preamble()
            if attachments:
                prompt += ("[context] The user attached these files (read them "
                           "with your terminal tools): " + ", ".join(attachments) + "\n\n")
            prompt += message

            note_user_activity()   # genuine user turn — resets the idle clock
            job = _new_job(session)
            threading.Thread(target=_chat_worker, args=(job, session, prompt),
                             daemon=True).start()
            self._json({"ok": True, "job": job["id"]})
            return

        if path == "/api/chat/approve":
            data = self._body_json()
            job = CHAT_JOBS.get(data.get("job", ""))
            choice = data.get("choice", "")
            if not job or job.get("done") or choice not in ("approve", "deny"):
                self._json({"ok": False}, 400)
                return
            job["pending_choice"] = choice
            self._json({"ok": True})
            return

        self.send_error(404)


def main():
    # On-demand model (2026-09-01): the mlx services no longer start at login.
    # If the model is down and not deliberately paused, mark it idle-suspended
    # so every existing wake path (chat worker, the idle loop's Telegram
    # cross-surface wake, the menu's "Wake now") treats it as asleep and
    # starts it on real use.
    try:
        if not agent_paused() and not agent_idle_suspended() and not model_online():
            with open(IDLE_SUSPEND_FILE, "w") as _f:
                _f.write(str(time.time()))
            print("[autostart] model down at dashboard start — marked asleep; "
                  "first user turn wakes it", file=sys.stderr)
    except Exception as _e:
        print(f"[autostart] init check failed: {_e}", file=sys.stderr)
    threading.Thread(target=briefing_loop, daemon=True).start()
    threading.Thread(target=memory_guard_loop, daemon=True).start()
    threading.Thread(target=idle_suspend_loop, daemon=True).start()
    if "system_sampler_loop" in globals():   # wave-2 live system charts
        threading.Thread(target=globals()["system_sampler_loop"],
                         daemon=True).start()
    threading.Thread(target=hub_prewarm_loop, daemon=True).start()
    server = ThreadingHTTPServer((DASH_HOST, DASH_PORT), Handler)
    print(f"Hermes Assistant dashboard: http://{DASH_HOST}:{DASH_PORT}")
    print(f"  hermes: {HERMES}   model: {'ONLINE' if model_online() else 'offline'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
