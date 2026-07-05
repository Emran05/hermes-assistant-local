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
  GET  /api/sessions        chat list (id, title, updated)
  GET  /api/history?session=ID
  POST /api/sessions/delete {session}
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
except Exception:            # never let a helper import take the hub down
    hermes_rpc = None

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
        chat = load_chat(session)

        def save_meta():
            cur = load_chat(session)
            cur["serve_sid"] = chat.get("serve_sid", "")
            cur["serve_key"] = chat.get("serve_key", "")
            save_chat(session, cur)

        hermes_rpc.run_turn(job, chat, prompt, save_meta)
    except Exception as e:
        # serve backend unreachable/broken — fall back to the old one-shot CLI
        job["status"] = "serve backend unavailable, using one-shot mode"
        ok, text = run_agent(prompt, session=session)
        job.update(reply=text, ok=ok, state="done", done=True)
    _finish_chat_job(job, session)


def run_agent(message, session=None):
    """One agent turn via `hermes -z`. Returns (ok, text)."""
    cmd = [HERMES]
    if session:
        cmd += ["--continue", session]
    cmd += ["-z", message]
    with _agent_lock:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=AGENT_TIMEOUT, env=_hermes_env())
        except subprocess.TimeoutExpired:
            return False, f"The agent took longer than {AGENT_TIMEOUT}s and was stopped."
        except FileNotFoundError:
            return False, f"Could not find the `hermes` binary at {HERMES}."
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
        for attempt in range(2):  # one retry if the model emits meta garbage
            ok, text = run_agent(access_preamble() + BRIEFING_PROMPT)
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


def _mlx_footprint_gb():
    """Real memory (phys_footprint) of the MLX server, in GB — ps RSS
    under-reports MLX's Metal/unified allocations, so use footprint(1)."""
    try:
        pids = subprocess.run(["pgrep", "-f", "mlx_lm"], capture_output=True,
                              text=True, timeout=5).stdout.split()
        if not pids:
            return None
        out = subprocess.run(["footprint", "-p", pids[0]], capture_output=True,
                             text=True, timeout=20).stdout
        m = re.search(r"phys_footprint:\s*([\d.]+)\s*(GB|MB)", out)
        if not m:
            return None
        v = float(m.group(1))
        return v if m.group(2) == "GB" else v / 1024
    except Exception:
        return None


def memory_guard_loop():
    """mlx_lm.server leaks over long uptime (prompt/KV cache accretes with the
    64k context) — it grew to ~49GB once and thrashed the machine. Auto-restart
    the model server when its footprint crosses a threshold to keep RAM sane."""
    thresh = float(os.environ.get("MLX_RESTART_GB", "32"))
    while True:
        time.sleep(300)
        if agent_paused():          # user parked the model on purpose
            continue
        gb = _mlx_footprint_gb()
        if gb and gb > thresh:
            try:
                subprocess.run(["launchctl", "kickstart", "-k",
                                f"gui/{os.getuid()}/com.hermes.mlx-server"],
                               capture_output=True, timeout=15)
                print(f"[memory_guard] mlx footprint {gb:.0f}GB > {thresh:.0f}GB "
                      "— restarted model server", file=sys.stderr)
            except Exception as e:
                print(f"[memory_guard] restart failed: {e}", file=sys.stderr)


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
        chat = load_chat(sid)
        if not chat["messages"]:
            continue
        out.append({
            "id": sid,
            "title": chat.get("title") or chat["messages"][0]["text"][:48],
            "updated": os.path.getmtime(chat_path(sid)),
        })
    out.sort(key=lambda c: -c["updated"])
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
DEFAULT_MODEL = "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"
PAUSE_FILE = os.path.join(DATA, "agent-paused")
MLX_LABEL = "com.hermes.mlx-server"
MLX_PLIST = os.path.join(HOME, "Library", "LaunchAgents", MLX_LABEL + ".plist")


def agent_paused():
    return os.path.exists(PAUSE_FILE)


def agent_power(action):
    """Pause = bootout the model server (frees ALL its RAM; chat/Telegram
    replies are down until resume). Resume = bootstrap it back."""
    uid = os.getuid()
    if action == "pause":
        try:
            subprocess.run(["launchctl", "bootout", f"gui/{uid}/{MLX_LABEL}"],
                           capture_output=True, timeout=15)
        except Exception as e:
            return {"ok": False, "error": type(e).__name__}
        with open(PAUSE_FILE, "w") as f:
            f.write(str(time.time()))
        _widget_cache.pop("sys_live", None)
        return {"ok": True, "paused": True}
    if action == "resume":
        try:
            os.remove(PAUSE_FILE)
        except OSError:
            pass
        try:
            r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", MLX_PLIST],
                               capture_output=True, text=True, timeout=20)
            if r.returncode != 0 and "already" not in (r.stderr or "").lower():
                time.sleep(3)  # launchd needs a beat after a recent bootout
                subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", MLX_PLIST],
                               capture_output=True, timeout=20)
        except Exception as e:
            return {"ok": False, "error": type(e).__name__}
        return {"ok": True, "paused": False, "loading": True}
    return {"ok": False, "error": "unknown action"}

_SEED_MODELS = [
    {"id": "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit", "label": "Qwen3-30B-A3B",
     "ram": 18, "note": "MoE · fast · current default"},
    {"id": "mlx-community/Hermes-3-Llama-3.1-8B-4bit", "label": "Hermes-3-8B",
     "ram": 5, "note": "Nous · tuned for tool-calling"},
    {"id": "mlx-community/Qwen3-8B-4bit", "label": "Qwen3-8B",
     "ram": 5, "note": "strong general 8B"},
    {"id": "mlx-community/Qwen3-14B-4bit", "label": "Qwen3-14B",
     "ram": 9, "note": "more headroom for tool chains"},
    {"id": "mlx-community/Qwen3-4B-Instruct-2507-4bit", "label": "Qwen3-4B",
     "ram": 3, "note": "ultra-light"},
]

_model_dl = {}          # id -> "downloading" | "done" | "error"


def _model_registry():
    reg = read_json(MODELS_FILE, None)
    if not reg or not isinstance(reg.get("models"), list):
        reg = {"models": list(_SEED_MODELS)}
        write_json(MODELS_FILE, reg)
    return reg["models"]


def _hf_cache_dir(mid):
    return os.path.join(HOME, ".cache", "huggingface", "hub",
                        "models--" + mid.replace("/", "--"))


def _model_downloaded(mid):
    d = _hf_cache_dir(mid)
    try:
        for _root, _dirs, files in os.walk(d):
            if any(f.endswith(".safetensors") for f in files):
                return True
    except OSError:
        pass
    return False


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
    for m in reg:
        out.append({**m, "active": m["id"] == active,
                    "downloaded": _model_downloaded(m["id"]),
                    "downloading": _model_dl.get(m["id"]) == "downloading"})
    return {"active": active, "models": out, "paused": agent_paused(),
            "ram_gb": None if agent_paused()
            else _cached("mlx_ram", 60, _mlx_footprint_gb)}


def switch_model(mid):
    if not any(m["id"] == mid for m in _model_registry()) and mid != active_model():
        return {"ok": False, "error": "unknown model"}
    if not _model_downloaded(mid):
        return {"ok": False, "error": "model not downloaded yet"}
    with open(ACTIVE_MODEL_FILE, "w") as f:
        f.write(mid)
    try:
        subprocess.run([HERMES, "config", "set", "model.default", mid],
                       capture_output=True, text=True, timeout=30, env=_hermes_env())
    except Exception:
        pass
    try:
        if agent_paused():          # switching while paused implies waking up
            agent_power("resume")
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
            r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", MLX_PLIST],
                               capture_output=True, text=True, timeout=20)
            if r.returncode != 0 and "already" not in (r.stderr or "").lower():
                time.sleep(3)
                subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", MLX_PLIST],
                               capture_output=True, timeout=20)
    except Exception as e:
        return {"ok": False, "error": f"restart failed: {e}"}
    _widget_cache.pop("sys_live", None)
    return {"ok": True, "active": mid, "loading": True}


def download_model(mid):
    if _model_dl.get(mid) == "downloading":
        return {"ok": True, "status": "downloading"}

    def run():
        _model_dl[mid] = "downloading"
        try:
            subprocess.run(
                [sys.executable, "-c",
                 "from huggingface_hub import snapshot_download;"
                 f"snapshot_download('{mid}')"],
                capture_output=True, text=True, timeout=7200, env=_hermes_env())
            _model_dl[mid] = "done" if _model_downloaded(mid) else "error"
        except Exception:
            _model_dl[mid] = "error"
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

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

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
                            "reply": job["reply"], "err": not job["ok"]})
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
        elif path == "/api/history":
            qs = urllib.parse.parse_qs(parsed.query)
            sid = (qs.get("session") or [""])[0]
            if not SESSION_RE.match(sid):
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
        if isinstance(res, tuple) and len(res) == 2:
            self._json(res[0], res[1])
        else:
            self._json(res if res is not None else {})

    # ---- POST ----
    def do_POST(self):
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
        if path == "/api/models/switch":
            self._json(switch_model((self._body_json().get("id") or "").strip()))
            return
        if path == "/api/models/download":
            self._json(download_model((self._body_json().get("id") or "").strip()))
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
    threading.Thread(target=briefing_loop, daemon=True).start()
    threading.Thread(target=memory_guard_loop, daemon=True).start()
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
