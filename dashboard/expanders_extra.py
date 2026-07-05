# expanders_extra.py — rich pop-out data providers, exec()'d into
# server.py's namespace (so _http_json/_cached/get_settings etc. resolve).
# Generated from the rich-widget-views agent wave; edit freely.

# ===== markets =====
def expand_markets():
    # Section 1: broad-market index ETFs (friendly names for the tiles).
    INDEX_NAMES = {"SPY": "S&P 500", "QQQ": "Nasdaq 100",
                   "DIA": "Dow Jones", "IWM": "Russell 2000"}
    indices = ["SPY", "QQQ", "DIA", "IWM"]
    # Section 2: user tickers (default AAPL,NVDA,MSFT) + megacaps, deduped,
    # excluding anything already shown in the index strip.
    user = get_settings().get("tickers") or ["AAPL", "NVDA", "MSFT"]
    mega = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]
    watch, seen = [], set(indices)
    for s in list(user) + mega:
        s = (s or "").strip().upper()
        if s and s not in seen:
            seen.add(s)
            watch.append(s)

    def one(sym, friendly=None):
        j = _http_json("https://query1.finance.yahoo.com/v8/finance/chart/"
                       + urllib.parse.quote(sym) + "?range=1d&interval=15m", timeout=7)
        res = j["chart"]["result"][0]
        m = res["meta"]
        closes = [c for c in (res.get("indicators", {}).get("quote", [{}])[0]
                              .get("close") or []) if c is not None]
        price = m.get("regularMarketPrice")
        prev = m.get("chartPreviousClose") or m.get("previousClose") or price
        chg = (price - prev) if (price is not None and prev) else 0
        pct = (chg / prev * 100) if prev else 0
        # anchor the sparkline at prev close so its slope matches the daily %.
        spark = ([prev] + closes)[-60:] if prev else closes[-60:]
        return {"symbol": sym, "friendly": friendly,
                "name": m.get("shortName") or m.get("longName") or sym,
                "price": price, "chg": round(chg, 2), "pct": round(pct, 2),
                "prev": prev,
                "day_hi": m.get("regularMarketDayHigh"),
                "day_lo": m.get("regularMarketDayLow"),
                "wk_hi": m.get("fiftyTwoWeekHigh"),
                "wk_lo": m.get("fiftyTwoWeekLow"),
                "vol": m.get("regularMarketVolume"),
                "cur": m.get("currency") or "USD",
                "exch": m.get("exchangeName"),
                "spark": spark, "asof": m.get("regularMarketTime"),
                "state": m.get("marketState")}

    def grab(sym, friendly=None):
        try:
            return one(sym, friendly)
        except Exception:
            return {"symbol": sym, "friendly": friendly, "error": True}

    def fetch():
        try:
            idx = [grab(s, INDEX_NAMES.get(s)) for s in indices]
            wl = [grab(s) for s in watch[:12]]
            asof = state = None
            for q in idx + wl:
                if not q.get("error"):
                    asof = asof or q.get("asof")
                    state = state or q.get("state")
            return {"indices": idx, "watchlist": wl, "asof": asof, "state": state}
        except Exception as e:
            return {"error": "markets unavailable (" + type(e).__name__ + ")"}

    return _cached("mkt_exp2:" + ",".join(watch), 300, fetch)

# ===== battery =====
def expand_battery():
    try:
        info = {"available": True, "pct": None, "charging": False, "ac": False,
                "state_key": "unknown", "state": "", "time_min": None,
                "time_label": None, "low_power_mode": False,
                "health": {}, "adapter": {}, "devices": []}

        # ---- pmset -g batt : percent, source, charging state, time estimate ----
        try:
            out = subprocess.run(["/usr/bin/pmset", "-g", "batt"],
                                 capture_output=True, text=True, timeout=3).stdout
            src = re.search(r"drawing from '([^']+)'", out)
            info["ac"] = bool(src and "AC" in src.group(1))
            m = re.search(r"(\d+)%;\s*([^;]+);\s*([^\n]+?)(?:\s+present|$)", out)
            if m:
                info["pct"] = int(m.group(1))
                raw = m.group(2).strip().lower()
                info["charging"] = "charging" in raw and "not" not in raw
                tm = re.search(r"(\d+):(\d+)\s+remaining", m.group(3))
                if tm:
                    info["time_min"] = int(tm.group(1)) * 60 + int(tm.group(2))
        except Exception:
            pass

        # ---- SPPowerDataType : health, cycles, adapter, low-power mode ----
        def _power():
            r = subprocess.run(["/usr/sbin/system_profiler", "SPPowerDataType", "-json"],
                               capture_output=True, text=True, timeout=8)
            return json.loads(r.stdout)
        try:
            pj = _cached("bat_power", 60, _power)
            for blk in pj.get("SPPowerDataType", []):
                nm = blk.get("_name")
                if nm == "spbattery_information":
                    hi = blk.get("sppower_battery_health_info", {})
                    ci = blk.get("sppower_battery_charge_info", {})
                    mi = blk.get("sppower_battery_model_info", {})
                    cap = str(hi.get("sppower_battery_health_maximum_capacity", "")).rstrip("%")
                    info["health"] = {
                        "condition": hi.get("sppower_battery_health"),
                        "cycles": hi.get("sppower_battery_cycle_count"),
                        "max_capacity_pct": int(cap) if cap.isdigit() else None,
                        "rated_cycles": 1000,
                        "serial": mi.get("sppower_battery_serial_number"),
                        "device": mi.get("sppower_battery_device_name"),
                    }
                    if ci.get("sppower_battery_is_charging") == "TRUE":
                        info["charging"] = True
                    if str(ci.get("sppower_battery_at_warn_level")).upper() == "TRUE":
                        info["warn"] = True
                    if info["pct"] is None and ci.get("sppower_battery_state_of_charge") is not None:
                        info["pct"] = ci.get("sppower_battery_state_of_charge")
                elif nm == "sppower_information":
                    bp = blk.get("Battery Power", {})
                    ap = blk.get("AC Power", {})
                    lpm = (ap if info["ac"] else bp).get("LowPowerMode")
                    info["low_power_mode"] = (str(lpm).upper() == "YES")
                elif nm == "sppower_ac_charger_information":
                    w = str(blk.get("sppower_ac_charger_watts", "")).strip()
                    info["adapter"] = {
                        "name": blk.get("sppower_ac_charger_name"),
                        "watts": int(w) if w.isdigit() else None,
                        "manufacturer": blk.get("sppower_ac_charger_manufacturer"),
                        "connected": blk.get("sppower_battery_charger_connected") == "TRUE",
                    }
                    if blk.get("sppower_battery_is_charging") == "TRUE":
                        info["charging"] = True
        except Exception:
            pass

        # ---- human state label + time ----
        if info["charging"]:
            info["state_key"] = "charging"; info["state"] = "Charging"
        elif info["ac"] and info["pct"] == 100:
            info["state_key"] = "charged"; info["state"] = "Fully charged"
        elif info["ac"]:
            info["state_key"] = "ac"; info["state"] = "On AC power"
        else:
            info["state_key"] = "battery"; info["state"] = "On battery"
        if info["time_min"]:
            hh, mm = divmod(info["time_min"], 60)
            info["time_label"] = ("%d:%02d" % (hh, mm)) + (" to full" if info["charging"] else " remaining")

        # ---- SPBluetoothDataType : connected device batteries (no TCC) ----
        def _bt():
            r = subprocess.run(["/usr/sbin/system_profiler", "SPBluetoothDataType", "-json"],
                               capture_output=True, text=True, timeout=8)
            return json.loads(r.stdout)
        def _pct(v):
            if v is None:
                return None
            s = str(v).rstrip("%").strip()
            return int(s) if s.isdigit() else None
        try:
            bj = _cached("bat_bt", 45, _bt)
            for blk in bj.get("SPBluetoothDataType", []):
                for entry in blk.get("device_connected", []):
                    for name, props in entry.items():
                        if not isinstance(props, dict):
                            continue
                        lvls = {
                            "main": _pct(props.get("device_batteryLevelMain")),
                            "left": _pct(props.get("device_batteryLevelLeft")),
                            "right": _pct(props.get("device_batteryLevelRight")),
                            "case": _pct(props.get("device_batteryLevelCase")),
                            "single": _pct(props.get("device_batteryLevel")),
                        }
                        if not any(v is not None for v in lvls.values()):
                            continue
                        info["devices"].append({
                            "name": name,
                            "type": props.get("device_minorType") or props.get("device_majorType") or "",
                            "levels": {k: v for k, v in lvls.items() if v is not None},
                        })
        except Exception:
            pass

        return info
    except Exception as e:
        return {"available": False, "reason": str(e)}

# ===== tasks =====
def expand_tasks():
    """Rich analytics over the local to-do list (~/.hermes/dashboard/tasks.json).
    No network/TCC — pure file read via get_tasks(). Returns the full list plus
    completion + age stats so the expand view can group and surface stale items."""
    try:
        raw = (get_tasks() or {}).get("tasks", []) or []
        now = time.time()
        DAY = 86400.0
        tasks, open_ages = [], []
        added_24h = added_7d = stale = 0
        buckets = {"today": 0, "week": 0, "older": 0}
        for t in raw:
            ts = t.get("ts")
            ts = ts if isinstance(ts, (int, float)) and ts else None
            age = (now - ts) if ts else None
            item = {"id": t.get("id"), "text": t.get("text", ""),
                    "done": bool(t.get("done")), "ts": ts,
                    "age": round(age) if age is not None else None}
            tasks.append(item)
            if age is not None:
                if age <= DAY:
                    added_24h += 1
                if age <= 7 * DAY:
                    added_7d += 1
            if not item["done"]:
                if age is None:
                    buckets["today"] += 1
                else:
                    open_ages.append(age)
                    if age <= DAY:
                        buckets["today"] += 1
                    elif age <= 7 * DAY:
                        buckets["week"] += 1
                    else:
                        buckets["older"] += 1
                        stale += 1
        open_n = sum(1 for x in tasks if not x["done"])
        done_n = sum(1 for x in tasks if x["done"])
        total = len(tasks)
        return {"available": True, "tasks": tasks,
                "open": open_n, "done": done_n, "total": total,
                "pct": round(done_n / total * 100) if total else 0,
                "added_24h": added_24h, "added_7d": added_7d,
                "oldest_open_age": round(max(open_ages)) if open_ages else None,
                "avg_open_age": round(sum(open_ages) / len(open_ages)) if open_ages else None,
                "buckets": buckets, "stale": stale, "generated": now}
    except Exception as e:
        return {"available": False, "reason": "%s: %s" % (type(e).__name__, e)}

# ===== reminders =====
def expand_reminders():
    """Rich Reminders view: open items grouped by list, due states, priority.
    Locale-proof dates via AppleScript component extraction; \x1f field /
    \x1e record delimiters. Degrades to available:false if Automation denied."""
    def fetch():
        script = (
            'set fs to (ASCII character 31)\n'
            'set rs to (ASCII character 30)\n'
            'set out to ""\n'
            'tell application "Reminders"\n'
            '  repeat with l in lists\n'
            '    set ln to name of l\n'
            '    repeat with r in (reminders of l whose completed is false)\n'
            '      set ds to ""\n'
            '      set dd to due date of r\n'
            '      if dd is not missing value then\n'
            '        set ds to ((year of dd) as string) & "," & ((month of dd) as integer as string) & "," & ((day of dd) as string) & "," & ((hours of dd) as string) & "," & ((minutes of dd) as string)\n'
            '      end if\n'
            '      set out to out & ln & fs & (name of r) & fs & (priority of r) & fs & (flagged of r) & fs & ds & rs\n'
            '    end repeat\n'
            '  end repeat\n'
            'end tell\n'
            'return out')
        try:
            proc = subprocess.run(["osascript", "-e", script],
                                  capture_output=True, text=True, timeout=25)
        except subprocess.TimeoutExpired:
            return {"available": False, "grant": True,
                    "reason": "Reminders read timed out — check Automation permission."}
        except Exception as e:
            return {"available": False, "reason": type(e).__name__}
        if proc.returncode != 0:
            return {"available": False, "grant": True,
                    "reason": "Reminders needs access — System Settings → Privacy "
                              "& Security → Automation, allow control of Reminders."}
        now = time.time()
        lt = time.localtime(now)
        day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        day_end = day_start + 86400
        lists, order = {}, []
        tot = over = today_n = sched = nodue = flagged_n = high_n = 0
        for rec in proc.stdout.split("\x1e"):
            f = rec.split("\x1f")
            if len(f) != 5 or not f[1].strip():
                continue
            ln, title, pri_s, flag_s, ds = [x.strip() for x in f]
            try:
                pri = int(pri_s)
            except ValueError:
                pri = 0
            flag = flag_s.lower() == "true"
            due_ts = due_iso = None
            all_day = False
            state = "none"
            if ds:
                try:
                    y, mo, dy, hh, mm = [int(x) for x in ds.split(",")]
                    due_ts = time.mktime((y, mo, dy, hh, mm, 0, 0, 0, -1))
                    due_iso = "%04d-%02d-%02dT%02d:%02d" % (y, mo, dy, hh, mm)
                    all_day = (hh == 0 and mm == 0)
                    if due_ts < (day_start if all_day else now):
                        state = "overdue"
                    elif day_start <= due_ts < day_end:
                        state = "today"
                    else:
                        state = "upcoming"
                except Exception:
                    due_ts = due_iso = None
            item = {"title": title[:120], "priority": pri, "flagged": flag,
                    "due_ts": due_ts, "due_iso": due_iso, "due_state": state,
                    "all_day": all_day, "note": None}
            if ln not in lists:
                lists[ln] = {"name": ln, "count": 0, "overdue": 0, "today": 0,
                             "items": []}
                order.append(ln)
            L = lists[ln]
            L["items"].append(item)
            L["count"] += 1
            tot += 1
            if state == "overdue":
                over += 1; L["overdue"] += 1
            elif state == "today":
                today_n += 1; L["today"] += 1
            if due_ts is not None:
                sched += 1
            else:
                nodue += 1
            if flag:
                flagged_n += 1
            if pri == 1:
                high_n += 1
        for L in lists.values():
            L["items"].sort(key=lambda i: (i["due_ts"] is None,
                                           i["due_ts"] or 0))
        out_lists = sorted((lists[n] for n in order),
                           key=lambda L: (-L["overdue"], -L["count"]))
        return {"available": True, "total": tot, "overdue": over,
                "today": today_n, "scheduled": sched, "no_due": nodue,
                "flagged": flagged_n, "high": high_n,
                "list_count": len(out_lists), "lists": out_lists}
    return _cached("rem_exp", 60, fetch)

# ===== notes =====
def expand_notes():
    # Rich scratchpad: current note text + live-derived stats, plus a
    # read-only glance at recent macOS Notes titles (degrades gracefully).
    try:
        note = get_notes()
    except Exception as e:
        return {"error": "notes unreadable: %s" % type(e).__name__}
    text = note.get("text") or ""
    words = len(re.findall(r"\S+", text))
    read_min = round(words / 200.0, 1) if words else 0

    def apple_notes():
        # Bulk-fetch names + modification deltas in one AppleScript pass.
        # Using (current date - modification date) keeps it timezone-clean and
        # avoids per-note round-trips. Needs a one-time Automation grant.
        osa = (
            'tell application "Notes"\n'
            '  set nowD to current date\n'
            '  set ns to name of notes\n'
            '  set ms to modification date of notes\n'
            '  set out to ""\n'
            '  repeat with i from 1 to (count of ns)\n'
            '    set out to out & ((nowD - (item i of ms)) as integer) & "\t" & (item i of ns) & linefeed\n'
            '  end repeat\n'
            '  return out\n'
            'end tell'
        )
        try:
            proc = subprocess.run(["osascript", "-e", osa],
                                  capture_output=True, text=True, timeout=8)
        except subprocess.TimeoutExpired:
            return {"available": False, "reason": "Apple Notes read timed out"}
        except Exception as e:
            return {"available": False, "reason": type(e).__name__}
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            if ("-1743" in err or "not allowed" in err.lower()
                    or "not authoriz" in err.lower()):
                return {"available": False, "reason":
                        "Grant Automation access for Notes (System Settings › Privacy & Security › Automation)."}
            return {"available": False, "reason": (err[:120] or "Apple Notes unavailable")}
        now = time.time()
        items = []
        for raw in proc.stdout.splitlines():
            if "\t" not in raw:
                continue
            secs, name = raw.split("\t", 1)
            name = name.strip()
            if not name:
                continue
            try:
                ago = int(secs)
            except ValueError:
                continue
            items.append({"title": name[:120], "ts": int(now - max(0, ago))})
        items.sort(key=lambda x: x["ts"], reverse=True)
        return {"available": True, "total": len(items), "notes": items[:8]}

    return {
        "text": text,
        "words": words,
        "chars": len(text),
        "lines": (text.count("\n") + 1) if text else 0,
        "read_min": read_min,
        "apple": _cached("notes_apple", 120, apple_notes),
    }

# ===== briefing =====
_SEC_ICONS = (
    ("schedul", ("calendar", "schedule")), ("calendar", ("calendar", "schedule")),
    ("agenda", ("calendar", "schedule")), ("meeting", ("calendar", "schedule")),
    ("priorit", ("spark", "priority")), ("focus", ("spark", "priority")),
    ("top ", ("spark", "priority")), ("todo", ("check", "tasks")),
    ("task", ("check", "tasks")), ("to-do", ("check", "tasks")),
    ("repl", ("chat", "inbox")), ("email", ("chat", "inbox")),
    ("inbox", ("chat", "inbox")), ("message", ("chat", "inbox")),
    ("respon", ("chat", "inbox")), ("radar", ("bell", "radar")),
    ("watch", ("bell", "radar")), ("aware", ("bell", "radar")),
    ("news", ("news", "radar")), ("follow", ("activity", "radar")),
    ("weather", ("sun", "weather")), ("note", ("note", "notes")),
)


def _section_meta(title):
    t = (title or "").lower()
    for key, (ic, kind) in _SEC_ICONS:
        if key in t:
            return ic, kind
    return "note", "general"


_TIME_RE = re.compile(
    r"^\s*(\d{1,2}:\d{2}\s*(?:[AaPp][Mm])?)"
    r"\s*(?:[-–—to]+\s*(\d{1,2}:\d{2}\s*(?:[AaPp][Mm])?))?\s*[:\-–]\s*(.+)$"
)


def _parse_item(text, kind):
    """Split a list line into a leading time-range + label when present."""
    item = {"text": text}
    m = _TIME_RE.match(text) if kind == "schedule" else None
    if m:
        item["time"] = m.group(1).strip()
        item["end"] = (m.group(2) or "").strip()
        item["label"] = m.group(3).strip()
    else:
        b = re.match(r"^\s*\*\*(.+?)\*\*[\s:–\-—]*(.*)$", text)
        if b:
            item["label"] = b.group(1).strip()
            item["rest"] = b.group(2).strip()
    return item


def expand_briefing():
    """Structured, executive-brief view of the cached daily briefing:
    parses the markdown into iconed sections (schedule / priorities / inbox /
    radar), extracts schedule times for a timeline, and adds greeting + stats.
    Reuses the same BRIEFING_FILE the compact widget reads."""
    def build():
        with _state_lock:
            b = read_json(BRIEFING_FILE, {})
            generating = _briefing_generating
        reply = (b.get("reply") or "").strip()
        gen_at = b.get("generated_at")
        if not reply:
            return {"available": True, "reply": "", "generated_at": gen_at,
                    "generating": generating, "sections": [], "empty": True,
                    "refresh_min": BRIEFING_REFRESH_MIN}

        import datetime
        now = datetime.datetime.now()
        hour = now.hour
        greet = ("Good evening" if hour >= 17 else "Good afternoon"
                 if hour >= 12 else "Good morning" if hour >= 5 else "Up late")
        age_min = round((time.time() - gen_at) / 60.0, 1) if gen_at else None

        intro, sections, cur, total_items = [], [], None, 0
        empty_pat = re.compile(
            r"^\s*(nothing yet|none|n/?a|no [\w ]+)\s*[.!]?\s*$", re.I)
        for raw in reply.splitlines():
            line = raw.rstrip()
            h = re.match(r"^\s*#{1,3}\s+(.*)$", line)
            if h:
                title = re.sub(r"[:*]+\s*$", "", h.group(1)).strip()
                title = re.sub(r"^\*\*(.+?)\*\*$", r"\1", title).strip()
                ic, kind = _section_meta(title)
                cur = {"title": title, "icon": ic, "kind": kind,
                       "items": [], "empty": False}
                sections.append(cur)
                continue
            li = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", line)
            if li:
                txt = li.group(1).strip()
                if not txt:
                    continue
                if empty_pat.match(txt):
                    if cur is not None:
                        cur["empty"] = True
                    continue
                if cur is None:
                    cur = {"title": "Briefing", "icon": "spark",
                           "kind": "general", "items": [], "empty": False}
                    sections.append(cur)
                cur["items"].append(_parse_item(txt, cur["kind"]))
                total_items += 1
                continue
            if line.strip():
                if empty_pat.match(line.strip()) and cur is not None:
                    cur["empty"] = True
                elif cur is None:
                    intro.append(line.strip())
                else:
                    cur["items"].append({"text": line.strip()})
                    total_items += 1

        for s in sections:
            s["count"] = len(s["items"])
            if not s["items"]:
                s["empty"] = True

        words = len(re.findall(r"\w+", reply))
        read_sec = max(15, int(words / 200.0 * 60))
        try:
            open_tasks = sum(1 for t in get_tasks().get("tasks", [])
                             if not t.get("done"))
        except Exception:
            open_tasks = None

        return {
            "available": True, "reply": reply, "generated_at": gen_at,
            "generating": generating, "age_min": age_min,
            "stale": (age_min is not None and age_min >= BRIEFING_REFRESH_MIN),
            "refresh_min": BRIEFING_REFRESH_MIN, "greeting": greet,
            "date_label": now.strftime("%A, %B %-d"),
            "time_label": now.strftime("%-I:%M %p"),
            "intro": " ".join(intro).strip(), "sections": sections,
            "empty": len(sections) == 0, "open_tasks": open_tasks,
            "stats": {"words": words, "read_sec": read_sec,
                      "sections": len(sections), "items": total_items},
        }

    try:
        return build()
    except Exception as e:
        return {"available": False,
                "reason": type(e).__name__ + ": " + str(e)[:120]}

# ===== messages =====
def expand_messages():
    """Rich Message Center from the local Messages db (read-only, local-first).
    Groups recent activity by conversation: latest message, sender, unread
    hint, participants + top-line counts. Modern macOS leaves message.text NULL
    and stores the body inside an NSKeyedArchiver 'attributedBody' blob, which we
    best-effort decode. Needs Full Disk Access for the launchd process; degrades
    to {available:False, grant:True, reason} with a clear enable path if blocked."""
    import sqlite3, re, time
    db = os.path.join(HOME, "Library", "Messages", "chat.db")
    try:
        if not os.path.exists(db):
            return {"available": False, "reason": "Messages database not found on this Mac."}
    except Exception as e:
        return {"available": False, "reason": str(e)}

    def _body(text, blob):
        if text:
            return text
        if not blob:
            return ""
        try:
            raw = bytes(blob)
        except Exception:
            return ""
        i = raw.find(b"NSString")
        if i != -1:
            j = raw.find(b"+", i)
            if j != -1 and j + 1 < len(raw):
                p = j + 1
                ln = raw[p]
                if ln == 0x81:            # 0x81 => u16 LE length follows
                    ln = int.from_bytes(raw[p + 1:p + 3], "little"); p += 3
                elif ln == 0x82:          # 0x82 => u32 LE length follows
                    ln = int.from_bytes(raw[p + 1:p + 5], "little"); p += 5
                else:
                    p += 1
                s = raw[p:p + max(0, min(ln, 4000))].decode("utf-8", "ignore").strip("\x00").strip()
                if s:
                    return s
            # fallback: first printable run after the marker
            seg = raw[i + 8:i + 8 + 3000]
            out = bytearray(); started = False
            for b in seg:
                if 32 <= b < 127 or b in (9, 10):
                    out.append(b); started = True
                elif started and len(out) > 1:
                    break
            s = out.decode("utf-8", "ignore").strip()
            if len(s) > 1:
                return s
        return ""

    def _apple_ts(dv):
        if not dv:
            return None
        return 978307200 + (dv / 1e9 if dv > 1e11 else dv)  # Apple epoch = 2001-01-01

    _PHONE = re.compile(r"^\+?[0-9][0-9\-\s()]{5,}$")

    def _pretty(hh):
        if not hh:
            return "Unknown"
        hh = hh.strip()
        if "@" in hh:
            return hh
        digits = re.sub(r"[^\d+]", "", hh)
        if _PHONE.match(hh) and len(digits) >= 10:
            d = digits[-10:]
            return "(%s) %s-%s" % (d[0:3], d[3:6], d[6:10])
        return hh

    def fetch():
        uri = "file:" + urllib.parse.quote(db) + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2.5)
        except sqlite3.Error:
            return {"available": False, "grant": True,
                    "reason": "Full Disk Access needed to read Messages."}
        try:
            con.row_factory = sqlite3.Row
            try:
                chats = con.execute(
                    "SELECT c.ROWID AS cid, c.chat_identifier AS ident, "
                    "c.display_name AS dname, c.style AS style, MAX(m.date) AS mx "
                    "FROM chat c "
                    "JOIN chat_message_join cmj ON cmj.chat_id=c.ROWID "
                    "JOIN message m ON m.ROWID=cmj.message_id "
                    "GROUP BY c.ROWID ORDER BY mx DESC LIMIT 14").fetchall()
            except sqlite3.Error:
                con.close()
                return {"available": False, "grant": True,
                        "reason": "Full Disk Access needed to read Messages."}

            now = time.time()
            today_apple_ns = int((now - (now % 86400) - 978307200) * 1e9)
            convos, total_unread, today_count = [], 0, 0

            for c in chats:
                cid = c["cid"]
                last = con.execute(
                    "SELECT m.text AS text, m.attributedBody AS body, m.is_from_me AS me, "
                    "m.date AS d, m.cache_has_attachments AS att, "
                    "m.associated_message_type AS amt, h.id AS handle "
                    "FROM message m LEFT JOIN handle h ON m.handle_id=h.ROWID "
                    "JOIN chat_message_join cmj ON cmj.message_id=m.ROWID "
                    "WHERE cmj.chat_id=? ORDER BY m.date DESC LIMIT 1", (cid,)).fetchone()
                if not last:
                    continue
                body = _body(last["text"], last["body"])
                is_tapback = bool(last["amt"])
                if not body:
                    body = "Attachment" if last["att"] else ("Reaction" if is_tapback else "")

                try:
                    ur = con.execute(
                        "SELECT COUNT(*) FROM message m "
                        "JOIN chat_message_join cmj ON cmj.message_id=m.ROWID "
                        "WHERE cmj.chat_id=? AND m.is_from_me=0 AND m.is_read=0", (cid,)).fetchone()[0]
                except sqlite3.Error:
                    ur = 0
                total_unread += ur

                try:
                    tc = con.execute(
                        "SELECT COUNT(*) FROM message m "
                        "JOIN chat_message_join cmj ON cmj.message_id=m.ROWID "
                        "WHERE cmj.chat_id=? AND m.date >= ?", (cid, today_apple_ns)).fetchone()[0]
                except sqlite3.Error:
                    tc = 0
                today_count += tc

                parts = []
                try:
                    for p in con.execute(
                        "SELECT h.id AS hid FROM chat_handle_join chj "
                        "JOIN handle h ON h.ROWID=chj.handle_id WHERE chj.chat_id=?", (cid,)).fetchall():
                        if p["hid"]:
                            parts.append(p["hid"])
                except sqlite3.Error:
                    pass

                is_group = (c["style"] == 43) or len(parts) > 1
                if c["dname"]:
                    name = c["dname"]
                elif parts:
                    name = _pretty(parts[0]) + (" +%d" % (len(parts) - 1) if (is_group and len(parts) > 1) else "")
                else:
                    name = _pretty(c["ident"])

                convos.append({
                    "name": name,
                    "ident": c["ident"] or "",
                    "group": bool(is_group),
                    "participants": len(parts) or 1,
                    "last": body[:140],
                    "from_me": bool(last["me"]),
                    "sender": "You" if last["me"] else _pretty(last["handle"] or c["ident"]),
                    "ts": _apple_ts(last["d"]),
                    "unread": int(ur),
                    "attachment": bool(last["att"]),
                    "reaction": is_tapback,
                })

            con.close()
            return {"available": True, "conversations": convos,
                    "total_unread": int(total_unread), "convo_count": len(convos),
                    "today_count": int(today_count)}
        except sqlite3.Error:
            try:
                con.close()
            except Exception:
                pass
            return {"available": False, "grant": True,
                    "reason": "Full Disk Access needed to read Messages."}

    return _cached("msg_expand", 40, fetch)

# ===== quicklinks =====
def expand_quicklinks():
    """Rich launcher: derive domain/monogram/color per link + curated quick-add
    suggestions for links the user has not pinned yet. Pure/local, no network."""
    DEFAULTS = [
        {"label": "GitHub", "url": "https://github.com"},
        {"label": "Hacker News", "url": "https://news.ycombinator.com"},
        {"label": "Gmail", "url": "https://mail.google.com"},
        {"label": "Calendar", "url": "https://calendar.google.com"},
    ]
    CURATED = [
        {"label": "Gmail", "url": "https://mail.google.com"},
        {"label": "Calendar", "url": "https://calendar.google.com"},
        {"label": "Google Drive", "url": "https://drive.google.com"},
        {"label": "GitHub", "url": "https://github.com"},
        {"label": "Notion", "url": "https://notion.so"},
        {"label": "Linear", "url": "https://linear.app"},
        {"label": "Figma", "url": "https://figma.com"},
        {"label": "Slack", "url": "https://slack.com"},
        {"label": "Claude", "url": "https://claude.ai"},
        {"label": "ChatGPT", "url": "https://chat.openai.com"},
        {"label": "Hacker News", "url": "https://news.ycombinator.com"},
        {"label": "Reddit", "url": "https://reddit.com"},
        {"label": "YouTube", "url": "https://youtube.com"},
        {"label": "Stack Overflow", "url": "https://stackoverflow.com"},
        {"label": "X", "url": "https://x.com"},
        {"label": "Maps", "url": "https://maps.google.com"},
    ]

    def _domain(url):
        try:
            u = url if "://" in url else "https://" + url
            net = urllib.parse.urlparse(u).netloc.lower()
            if net.startswith("www."):
                net = net[4:]
            return net
        except Exception:
            return ""

    def _mono(label, dom):
        s = (label or dom or "").strip()
        return s[0].upper() if s else "#"

    def _hue(key):
        h = 0
        for ch in (key or "?"):
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        return h % 360

    def _enrich(l):
        url = (l.get("url") or "").strip()
        label = (l.get("label") or "").strip()
        dom = _domain(url)
        return {"label": label or dom or url, "url": url, "domain": dom,
                "mono": _mono(label, dom), "hue": _hue(dom or label or url)}

    try:
        raw = get_settings().get("quicklinks")
        if not isinstance(raw, list) or not raw:
            raw = DEFAULTS
        links = [_enrich(l) for l in raw if isinstance(l, dict) and l.get("url")]
        have = {x["domain"] for x in links if x["domain"]}
        suggestions = [_enrich(s) for s in CURATED if _domain(s["url"]) not in have]
        domains = sorted({x["domain"] for x in links if x["domain"]})
        return {"links": links, "count": len(links),
                "domain_count": len(domains), "suggestions": suggestions}
    except Exception as e:
        return {"error": "Quick links unavailable (" + type(e).__name__ + ")"}

# ===== recent =====
def expand_recent():
    """Rich recent-file activity across granted folders (last 48h): per-file
    size + extension + parent folder, grouped by folder, with a type breakdown
    and a 'N files changed' summary. Pure filesystem walk, no LLM/network."""
    def scan():
        try:
            dirs = get_access().get("dirs", []) or []
        except Exception:
            dirs = []
        if not dirs:
            return {"available": False,
                    "reason": "No folders granted yet — grant a folder in Access "
                              "to see recent file activity."}
        now = time.time()
        cutoff = now - 48 * 3600
        SKIP = {"node_modules", "__pycache__", "venv", ".venv", "env", ".git",
                "build", "dist", ".next", "target", ".cache", "Pods"}
        files, scanned = [], 0
        for root_dir in dirs:
            try:
                if not os.path.isdir(root_dir):
                    continue
                budget = 6000
                base_depth = root_dir.count(os.sep)
                for cur, subdirs, names in os.walk(root_dir):
                    subdirs[:] = [d for d in subdirs
                                  if not d.startswith(".") and d not in SKIP]
                    if cur.count(os.sep) - base_depth >= 4:
                        subdirs[:] = []
                    for fn in names:
                        if fn.startswith("."):
                            continue
                        budget -= 1
                        if budget <= 0:
                            break
                        p = os.path.join(cur, fn)
                        try:
                            stt = os.stat(p)
                        except OSError:
                            continue
                        scanned += 1
                        mt = stt.st_mtime
                        # window filter; drop clock-skewed future timestamps
                        if mt <= cutoff or mt > now + 300:
                            continue
                        ext = os.path.splitext(fn)[1].lower().lstrip(".")
                        parent = os.path.dirname(p)
                        try:
                            rel = os.path.relpath(parent, HOME)
                        except Exception:
                            rel = parent
                        files.append({
                            "path": p, "name": fn, "ext": ext or "—",
                            "size": stt.st_size, "mtime": mt, "parent": parent,
                            "parent_name": os.path.basename(parent) or parent,
                            "parent_rel": rel})
                    if budget <= 0:
                        break
            except Exception:
                continue
        files.sort(key=lambda f: -f["mtime"])
        total = len(files)
        total_size = sum(f["size"] for f in files)
        last24 = sum(1 for f in files if f["mtime"] > now - 24 * 3600)
        last1h = sum(1 for f in files if f["mtime"] > now - 3600)
        # group by parent folder (cap files kept per folder, keep full count)
        groups = {}
        for f in files:
            g = groups.get(f["parent"])
            if g is None:
                g = groups[f["parent"]] = {
                    "parent": f["parent"], "name": f["parent_name"],
                    "rel": f["parent_rel"], "files": [],
                    "count": 0, "size": 0, "latest": 0}
            g["count"] += 1
            g["size"] += f["size"]
            if f["mtime"] > g["latest"]:
                g["latest"] = f["mtime"]
            if len(g["files"]) < 8:
                g["files"].append({k: f[k] for k in
                                   ("path", "name", "ext", "size", "mtime")})
        grouped = sorted(groups.values(), key=lambda g: -g["latest"])[:12]
        # extension breakdown
        types = {}
        for f in files:
            t = types.get(f["ext"])
            if t is None:
                t = types[f["ext"]] = {"ext": f["ext"], "count": 0, "size": 0}
            t["count"] += 1
            t["size"] += f["size"]
        type_list = sorted(types.values(), key=lambda t: -t["count"])[:8]
        return {"available": True, "count": total, "total_size": total_size,
                "last_24h": last24, "last_1h": last1h,
                "folder_count": len(groups), "scanned": scanned,
                "window_h": 48, "groups": grouped, "types": type_list,
                "generated": now}
    try:
        return _cached("recent_exp", 90, scan)
    except Exception as e:
        return {"available": False,
                "reason": "recent scan failed (%s)" % type(e).__name__}

# ===== folders =====
def expand_folders():
    """Rich manager for the folders the user has granted the assistant.
    Per folder: file count, total size, subfolder count, last activity,
    file-type breakdown and largest files (bounded os.walk, cached)."""
    try:
        dirs = get_access().get("dirs", [])
    except Exception as e:
        return {"available": False,
                "reason": "access list unreadable: " + type(e).__name__}

    IGNORE = {"node_modules", "__pycache__", "venv", ".venv", ".git", "Library",
              ".Trash", "dist", "build", ".next", ".cache"}

    def scan_one(path):
        info = {"path": path,
                "name": os.path.basename(path.rstrip("/")) or path,
                "exists": True, "files": 0, "dirs": 0, "bytes": 0,
                "last_mtime": 0, "newest": None, "types": [], "largest": [],
                "truncated": False}
        if not os.path.isdir(path):
            info["exists"] = False
            return info
        budget = 12000          # entries per grant — keeps this near-instant
        future = time.time() + 86400
        types = {}
        largest = []
        try:
            for cur, subdirs, files in os.walk(path):
                subdirs[:] = [d for d in subdirs
                              if not d.startswith(".") and d not in IGNORE]
                if cur.count(os.sep) - path.count(os.sep) >= 6:
                    subdirs[:] = []
                info["dirs"] += len(subdirs)
                for fn in files:
                    if fn.startswith("."):
                        continue
                    budget -= 1
                    if budget <= 0:
                        info["truncated"] = True
                        break
                    fp = os.path.join(cur, fn)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    sz = st.st_size
                    info["files"] += 1
                    info["bytes"] += sz
                    # ignore bogus future mtimes when tracking "last activity"
                    if info["last_mtime"] < st.st_mtime <= future:
                        info["last_mtime"] = st.st_mtime
                        info["newest"] = {"name": fn, "mtime": st.st_mtime,
                                          "rel": os.path.relpath(fp, path)}
                    ext = os.path.splitext(fn)[1].lower().lstrip(".") or "none"
                    t = types.setdefault(ext, [0, 0])
                    t[0] += 1
                    t[1] += sz
                    largest.append((sz, os.path.relpath(fp, path)))
                if budget <= 0:
                    break
        except Exception as e:
            info["error"] = type(e).__name__
        info["types"] = sorted(
            [{"ext": k, "n": v[0], "bytes": v[1]} for k, v in types.items()],
            key=lambda x: -x["bytes"])[:6]
        largest.sort(key=lambda x: -x[0])
        info["largest"] = [{"name": os.path.basename(r), "rel": r, "bytes": b}
                           for b, r in largest[:5]]
        return info

    def build():
        folders = [scan_one(d) for d in dirs]
        # common home folders not yet granted -> one-tap quick-add chips
        cand = []
        for name in ("Desktop", "Documents", "Downloads", "Projects",
                     "Developer", "Movies", "Pictures", "Music"):
            p = os.path.join(HOME, name)
            if os.path.isdir(p) and os.path.realpath(p) not in dirs and p not in dirs:
                cand.append(p)
        return {"available": True, "count": len(folders),
                "total_files": sum(f["files"] for f in folders),
                "total_bytes": sum(f["bytes"] for f in folders),
                "folders": folders, "inbox": INBOX, "home": HOME,
                "suggestions": cand[:5], "scanned_at": time.time()}

    try:
        # key on the dir set so add/remove invalidates the cache immediately
        return _cached("folders_expand:" + "|".join(sorted(dirs)), 60, build)
    except Exception as e:
        return {"available": False, "reason": type(e).__name__}

# ===== clock =====
def expand_clock():
    # Rich time view: sun times + daylight for the day-progress bar & sun stats.
    # Live clock, day/week strips are computed client-side; this only adds sun data.
    s = get_settings()
    lat, lon = s.get("weather_lat"), s.get("weather_lon")
    if lat is None or lon is None:
        try:
            weather()  # geocodes + caches lat/lon if a city is configured
            s = get_settings()
            lat, lon = s.get("weather_lat"), s.get("weather_lon")
        except Exception:
            pass
    if lat is None or lon is None:
        return {"available": True,
                "sun": {"available": False,
                        "reason": "No location set — add a city in the Weather widget for sun times."}}

    def fetch():
        j = _http_json(
            "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
            "&daily=sunrise,sunset,daylight_duration"
            "&timezone=auto&forecast_days=2&past_days=1" % (lat, lon), timeout=8)
        D = j.get("daily", {})
        srs = D.get("sunrise", []); sss = D.get("sunset", []); dur = D.get("daylight_duration", [])
        # past_days=1 -> index 0 = yesterday, 1 = today, 2 = tomorrow

        def mins(iso):
            if not iso or len(iso) < 16:
                return None
            return int(iso[11:13]) * 60 + int(iso[14:16])

        def t12(iso):
            if not iso or len(iso) < 16:
                return None
            h = int(iso[11:13]); mm = iso[14:16]
            return "%d:%s %s" % (h % 12 or 12, mm, "AM" if h < 12 else "PM")

        def durstr(sec):
            if sec is None:
                return None
            m = int(round(sec / 60.0))
            return "%dh %02dm" % (m // 60, m % 60)

        yi, ti, wi = 0, 1, 2
        sr_iso = srs[ti] if len(srs) > ti else None
        ss_iso = sss[ti] if len(sss) > ti else None
        srm, ssm = mins(sr_iso), mins(ss_iso)
        noon_min = (srm + ssm) // 2 if srm is not None and ssm is not None else None
        noon_str = None
        if noon_min is not None:
            h = noon_min // 60; mm = noon_min % 60
            noon_str = "%d:%02d %s" % (h % 12 or 12, mm, "AM" if h < 12 else "PM")
        today_dur = dur[ti] if len(dur) > ti else None
        yest_dur = dur[yi] if len(dur) > yi else None
        delta_min = int(round((today_dur - yest_dur) / 60.0)) \
            if (today_dur is not None and yest_dur is not None) else None
        return {"available": True, "tz": j.get("timezone", ""),
                "tzabbr": j.get("timezone_abbreviation", ""),
                "sunrise": t12(sr_iso), "sunset": t12(ss_iso),
                "sunrise_min": srm, "sunset_min": ssm,
                "solar_noon": noon_str, "noon_min": noon_min,
                "daylight": durstr(today_dur), "daylight_sec": today_dur,
                "delta_min": delta_min,
                "tomorrow_sunrise": t12(srs[wi]) if len(srs) > wi else None,
                "tomorrow_sunset": t12(sss[wi]) if len(sss) > wi else None,
                "lat": lat, "lon": lon}

    try:
        sun = _cached("clock_sun:%s,%s" % (lat, lon), 1800, fetch)
    except Exception as e:
        sun = {"available": False, "reason": "sun-times fetch failed (%s)" % type(e).__name__}
    return {"available": True, "sun": sun}

# register everything (rebinds ids already present, e.g. markets)
EXPANDERS.update({
    "markets": expand_markets,
    "battery": expand_battery,
    "tasks": expand_tasks,
    "reminders": expand_reminders,
    "notes": expand_notes,
    "briefing": expand_briefing,
    "messages": expand_messages,
    "quicklinks": expand_quicklinks,
    "recent": expand_recent,
    "folders": expand_folders,
    "clock": expand_clock,
})


# ============================================================
# WAVE 2 — revamp fleet (news desk, live system, day drill,
# markets pro, console pro, mind extras). Later defs override
# earlier bindings; routes/threads wired in server.py.
# ============================================================
import collections
import concurrent.futures
import datetime
import sqlite3

# ---- news_desk ----
NEWS_DESK_SECTIONS = [
    ("Tech", [
        ("https://techcrunch.com/feed/", "TechCrunch"),
        ("https://www.theverge.com/rss/index.xml", "The Verge"),
        ("https://feeds.arstechnica.com/arstechnica/index", "Ars Technica"),
    ]),
    ("World", [
        ("http://feeds.bbci.co.uk/news/world/rss.xml", "BBC World"),
        ("https://feeds.npr.org/1004/rss.xml", "NPR World"),
        ("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
    ]),
    ("Business", [
        ("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147", "CNBC"),
        ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch"),
        ("https://finance.yahoo.com/news/rssindex", "Yahoo Finance"),
    ]),
    ("Science", [
        ("https://www.sciencedaily.com/rss/all.xml", "ScienceDaily"),
        ("https://www.nasa.gov/feed/", "NASA"),
        ("https://phys.org/rss-feed/", "Phys.org"),
    ]),
]


def _news_parse_date(s):
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


def _news_parse_feed(url, label=None, limit=10):
    """Fetch + parse one RSS/Atom feed -> list of item dicts. Never raises."""
    import xml.etree.ElementTree as ET
    try:
        raw = _http_text(url, timeout=7)
        root = ET.fromstring(raw)
    except Exception:
        return []
    src = label
    if not src:
        try:
            src = urllib.parse.urlparse(url).netloc.replace("www.", "").replace("feeds.", "")
        except Exception:
            src = url
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            if tag == "title" and (el.text or "").strip():
                src = _strip_html(el.text, 32)
                break
            if tag in ("item", "entry"):
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
                if href.startswith("http") and (not link or c.get("rel") in (None, "", "alternate")):
                    link = href
            elif ct == "guid" and not link:
                g = (c.text or "").strip()
                if g.startswith("http"):
                    link = g
            elif ct in ("description", "summary", "encoded", "content") and not desc:
                raw_d = c.text or ""
                if not raw_d and len(c):
                    try:
                        raw_d = "".join(ET.tostring(x, encoding="unicode") for x in c)
                    except Exception:
                        raw_d = ""
                desc = raw_d
            elif ct in ("pubDate", "published", "updated", "date") and not date_raw:
                date_raw = (c.text or "").strip()
        if not title or not link:
            continue
        title_c = _strip_html(title, 160)
        summary = _strip_html(desc, 160)
        if summary and title_c and summary[:60].lower() == title_c[:60].lower():
            summary = ""  # feed repeats title in description
        out.append({"title": title_c, "url": link, "source": src,
                    "summary": summary, "ts": _news_parse_date(date_raw)})
        if len(out) >= limit:
            break
    return out


def expand_rss():
    """News Desk: top stories per category, round-robined across 2-3 feeds each."""
    user_feeds = [u for u in (get_settings().get("news_feeds") or []) if isinstance(u, str)][:4]

    def fetch():
        plan = [(name, list(feeds)) for name, feeds in NEWS_DESK_SECTIONS]
        if user_feeds:
            plan.append(("Yours", [(u, None) for u in user_feeds]))
        # fetch all feeds in parallel; each worker degrades to [] on failure
        jobs = [(si, url, label) for si, (_n, feeds) in enumerate(plan) for url, label in feeds]
        results = {}
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(8, len(jobs) or 1)) as ex:
                futs = {ex.submit(_news_parse_feed, u, lb): (si, u) for si, u, lb in jobs}
                for f in futs:
                    si, u = futs[f]
                    try:
                        results.setdefault(si, []).append(f.result(timeout=12))
                    except Exception:
                        results.setdefault(si, []).append([])
        except Exception:
            for si, u, lb in jobs:
                results.setdefault(si, []).append(_news_parse_feed(u, lb))
        sections, seen = [], set()
        for si, (name, feeds) in enumerate(plan):
            buckets = results.get(si, [])
            items, col = [], 0
            while len(items) < 7 and any(col < len(b) for b in buckets):
                for b in buckets:
                    if col < len(b) and len(items) < 7:
                        it = b[col]
                        key = re.sub(r"\W+", "", (it["title"] or "").lower())[:70]
                        if key and key not in seen:
                            seen.add(key)
                            items.append(it)
                col += 1
            items.sort(key=lambda x: (x["ts"] or 0), reverse=True)
            if items:
                sections.append({"name": name, "items": items})
        if not sections:
            return {"error": "No feeds reachable right now."}
        return {"sections": sections}

    return _cached("rss_desk:" + "|".join(user_feeds), 900, fetch)

# ---- system_pro ----
import collections

SYS_HISTORY = collections.deque(maxlen=360)   # 360 x 5s = 30 min
_SYS_MEMSIZE = None


def _sys_net_bytes():
    """Cumulative in/out bytes across en* interfaces (netstat -ib)."""
    out = subprocess.run(["netstat", "-ib"], capture_output=True, text=True,
                         timeout=4).stdout
    ib = ob = 0
    seen = set()
    for ln in out.splitlines()[1:]:
        f = ln.split()
        if len(f) >= 10 and f[0].startswith("en") and f[0] not in seen:
            seen.add(f[0])
            try:
                ib += int(f[6]); ob += int(f[9])
            except (ValueError, IndexError):
                pass
    return ib, ob


def _sys_sample():
    """One {ts, cpu_pct, ram_pct, net_in_bytes, net_out_bytes} sample."""
    global _SYS_MEMSIZE
    ts = int(time.time())
    cpu = None
    try:  # ps aggregation: sum %CPU across processes / cores (responsive)
        out = subprocess.run(["ps", "-Aceo", "pcpu"], capture_output=True,
                             text=True, timeout=4).stdout
        tot = 0.0
        for ln in out.splitlines()[1:]:
            try:
                tot += float(ln)
            except ValueError:
                pass
        cpu = round(min(100.0, tot / (os.cpu_count() or 1)), 1)
    except Exception:
        try:  # fallback: 1-min loadavg / cores
            cpu = round(min(100.0, 100.0 * os.getloadavg()[0] /
                            (os.cpu_count() or 1)), 1)
        except Exception:
            pass
    ram = None
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True,
                            timeout=3).stdout
        pg = 16384
        st = dict(re.findall(r'^"?([\w -]+)"?:\s+(\d+)', vm, re.M))
        free = (int(st.get("Pages free", 0)) + int(st.get("Pages inactive", 0))
                + int(st.get("Pages purgeable", 0))) * pg
        if _SYS_MEMSIZE is None:
            _SYS_MEMSIZE = int(subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True,
                text=True, timeout=3).stdout)
        ram = round(100.0 * (1 - free / _SYS_MEMSIZE), 1)
    except Exception:
        pass
    nin = nout = None
    try:
        nin, nout = _sys_net_bytes()
    except Exception:
        pass
    return {"ts": ts, "cpu_pct": cpu, "ram_pct": ram,
            "net_in_bytes": nin, "net_out_bytes": nout}


def system_sampler_loop():
    """Infinite daemon loop: one sample every 5s into SYS_HISTORY."""
    while True:
        try:
            SYS_HISTORY.append(_sys_sample())
        except Exception:
            pass
        time.sleep(5)


def _sys_history_lists():
    """Deque -> aligned lists; net rates in KB/s computed server-side."""
    ts, cpu, ram, nin, nout = [], [], [], [], []
    prev = None
    for s in list(SYS_HISTORY):
        ts.append(s["ts"]); cpu.append(s["cpu_pct"]); ram.append(s["ram_pct"])
        ri = ro = 0.0
        if (prev and s.get("net_in_bytes") is not None
                and prev.get("net_in_bytes") is not None):
            dt = max(1, s["ts"] - prev["ts"])
            ri = round(max(0, s["net_in_bytes"] - prev["net_in_bytes"]) / dt / 1024, 1)
            ro = round(max(0, s["net_out_bytes"] - prev["net_out_bytes"]) / dt / 1024, 1)
        nin.append(ri); nout.append(ro)
        prev = s
    return {"ts": ts, "cpu": cpu, "ram": ram,
            "net_in_kbs": nin, "net_out_kbs": nout}


def expand_system():
    """REPLACEMENT: everything the current expand_system returns + history."""
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
        ib, ob = _sys_net_bytes()
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
        load = [round(x, 2) for x in os.getloadavg()]
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
    if not SYS_HISTORY:  # server just started: seed one point so charts render
        try:
            SYS_HISTORY.append(_sys_sample())
        except Exception:
            pass
    return {"cpu_top": ps("-r"), "mem_top": ps("-m"), "mem": mem, "net": net,
            "disks": disks, "load": load, "cores": os.cpu_count(),
            "uptime_hr": round((time.time() - boot) / 3600, 1) if boot else None,
            "sys": system_status(), "history": _sys_history_lists()}

# ---- day_drill ----
def expand_weather():
    s = get_settings()
    lat, lon = s.get("weather_lat"), s.get("weather_lon")
    if lat is None or lon is None:
        try:
            weather()  # geocodes + caches lat/lon
        except Exception:
            pass
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
            "&hourly=temperature_2m,precipitation_probability,weather_code"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,sunrise,sunset,uv_index_max,wind_speed_10m_max"
            "&timezone=auto&forecast_days=7&temperature_unit=fahrenheit"
            "&wind_speed_unit=mph", timeout=8)
        cur = j.get("current", {})
        H = j.get("hourly", {})
        D = j.get("daily", {})
        now_iso = cur.get("time", "")
        htimes = H.get("time", [])
        temps = H.get("temperature_2m", [])
        pops = H.get("precipitation_probability", [0] * len(htimes))
        codes = H.get("weather_code", [0] * len(htimes))

        def hlbl(hh):  # 12-hour label: 0 -> "12a", 15 -> "3p"
            return "%d%s" % ((hh % 12) or 12, "a" if hh < 12 else "p")

        try:
            start = htimes.index(now_iso) if now_iso in htimes else 0
        except ValueError:
            start = 0
        hourly = []
        for i in range(start, min(start + 24, len(htimes))):
            hourly.append({"t": hlbl(int(htimes[i][11:13])),
                           "temp": round(temps[i]),
                           "pop": pops[i] if pops[i] is not None else 0})
        # group the full 7-day hourly series by calendar date for per-day drill-down
        by_day = {}
        for i in range(len(htimes)):
            try:
                by_day.setdefault(htimes[i][:10], []).append({
                    "t": hlbl(int(htimes[i][11:13])),
                    "temp": round(temps[i]),
                    "pop": pops[i] if pops[i] is not None else 0,
                    "code": codes[i] if codes[i] is not None else 0})
            except (TypeError, IndexError, ValueError):
                continue
        days = []
        for i in range(len(D.get("time", []))):
            days.append({"date": D["time"][i], "code": D["weather_code"][i],
                         "hi": round(D["temperature_2m_max"][i]),
                         "lo": round(D["temperature_2m_min"][i]),
                         "pop": D["precipitation_probability_max"][i],
                         "sunrise": (D["sunrise"][i][11:16]),
                         "sunset": (D["sunset"][i][11:16]),
                         "uv": round(D["uv_index_max"][i] or 0),
                         "hours": by_day.get(D["time"][i], [])})
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
        return _cached(f"wx_exp2:{lat},{lon}", 1200, fetch)
    except Exception as e:
        return {"error": "weather fetch failed (" + type(e).__name__ + ")"}

# ---- markets_pro ----
# ===== markets_pro backend =====
# expand_markets(): indices strip + big watchlist with starred-first ordering.
# market_search(q): Yahoo unauthenticated symbol search (needs route wiring:
#   GET /api/markets/search?q=  ->  market_search(q)).

def expand_markets():
    INDEX_NAMES = {"SPY": "S&P 500", "QQQ": "Nasdaq 100",
                   "DIA": "Dow Jones", "IWM": "Russell 2000"}
    indices = ["SPY", "QQQ", "DIA", "IWM"]
    s = get_settings()
    user = [str(t or "").strip().upper() for t in (s.get("tickers") or ["AAPL", "NVDA", "MSFT"])]
    user = [t for t in user if t]
    starred = [str(t or "").strip().upper() for t in (s.get("starred_tickers") or [])]
    starred = [t for t in starred if t]
    mega = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]
    # starred pinned first, then user tickers, then megacaps — deduped,
    # index ETFs excluded (they get their own strip).
    watch, seen = [], set(indices)
    for t in starred + user + mega:
        if t not in seen:
            seen.add(t)
            watch.append(t)
    watch = watch[:16]

    def one(sym, friendly=None):
        j = _http_json("https://query1.finance.yahoo.com/v8/finance/chart/"
                       + urllib.parse.quote(sym) + "?range=1d&interval=15m", timeout=7)
        res = j["chart"]["result"][0]
        m = res["meta"]
        closes = [c for c in (res.get("indicators", {}).get("quote", [{}])[0]
                              .get("close") or []) if c is not None]
        price = m.get("regularMarketPrice")
        prev = m.get("chartPreviousClose") or m.get("previousClose") or price
        chg = (price - prev) if (price is not None and prev) else 0
        pct = (chg / prev * 100) if prev else 0
        # prevClose-anchored sparkline so slope matches the daily %.
        spark = ([prev] + closes)[-60:] if prev else closes[-60:]
        return {"symbol": sym, "friendly": friendly,
                "name": m.get("shortName") or m.get("longName") or sym,
                "price": price, "chg": round(chg, 2), "pct": round(pct, 2),
                "prev": prev,
                "day_hi": m.get("regularMarketDayHigh"),
                "day_lo": m.get("regularMarketDayLow"),
                "wk_hi": m.get("fiftyTwoWeekHigh"),
                "wk_lo": m.get("fiftyTwoWeekLow"),
                "vol": m.get("regularMarketVolume"),
                "cur": m.get("currency") or "USD",
                "exch": m.get("exchangeName"),
                "spark": spark, "asof": m.get("regularMarketTime"),
                "state": m.get("marketState")}

    def grab(sym, friendly=None):
        # per-symbol cache: adding/starring one ticker only refetches the
        # missing symbol, not the whole board. Errors are NOT cached.
        try:
            return _cached("mkt_q:" + sym, 300, lambda: one(sym, friendly))
        except Exception:
            return {"symbol": sym, "friendly": friendly, "error": True}

    try:
        # warm uncached symbols in parallel (falls back to the sequential
        # grabs below if the pool is unavailable for any reason)
        try:
            from concurrent.futures import ThreadPoolExecutor
            todo = [(t, INDEX_NAMES.get(t)) for t in indices + watch]
            with ThreadPoolExecutor(max_workers=8) as ex:
                list(ex.map(lambda a: grab(a[0], a[1]), todo))
        except Exception:
            pass
        idx = [grab(t, INDEX_NAMES.get(t)) for t in indices]
        star_set, user_set = set(starred), set(user)
        wl = []
        for t in watch:
            q = dict(grab(t))
            q["starred"] = t in star_set
            q["removable"] = t in user_set
            wl.append(q)
        asof = state = None
        adv = dec = flat = 0
        for q in idx + wl:
            if q.get("error"):
                continue
            asof = asof or q.get("asof")
            state = state or q.get("state")
            p = q.get("pct") or 0
            if p > 0.05:
                adv += 1
            elif p < -0.05:
                dec += 1
            else:
                flat += 1
        return {"indices": idx, "watchlist": wl, "asof": asof, "state": state,
                "tickers": user, "starred": starred,
                "breadth": {"adv": adv, "dec": dec, "flat": flat}}
    except Exception as e:
        return {"error": "markets unavailable (" + type(e).__name__ + ")"}


def market_search(q):
    """Yahoo unauthenticated symbol search -> top 8 equity/ETF matches.
    Wire as: GET /api/markets/search?q=  ->  market_search(q)."""
    q = str(q or "").strip()[:60]
    if not q:
        return {"q": q, "results": []}

    def fetch():
        try:
            j = _http_json("https://query1.finance.yahoo.com/v1/finance/search?q="
                           + urllib.parse.quote(q)
                           + "&quotesCount=12&newsCount=0&listsCount=0", timeout=6)
        except Exception as e:
            return {"q": q, "results": [],
                    "error": "search unavailable (" + type(e).__name__ + ")"}
        out = []
        for it in (j.get("quotes") or []):
            qt = str(it.get("quoteType") or "").upper()
            sym = str(it.get("symbol") or "").strip()
            if not sym or qt not in ("EQUITY", "ETF"):
                continue
            out.append({"symbol": sym,
                        "name": it.get("shortname") or it.get("longname") or sym,
                        "exch": it.get("exchDisp") or it.get("exchange") or "",
                        "type": qt})
            if len(out) >= 8:
                break
        return {"q": q, "results": out}
    return _cached("mkt_srch:" + q.lower(), 60, fetch)

# ---- console_pro ----
def console_activity():
    """Live timeline of every tool call across all surfaces (dashboard,
    Telegram, CLI) plus aggregate stats: calls today, per-source split,
    top tools, and a rolling 24h hourly histogram — from state.db, read-only.
    REPLACEMENT for the existing console_activity(); same /api/console route.
    Depends on the existing _short_args() helper (unchanged)."""
    import sqlite3
    now = time.time()
    lt = time.localtime(now)
    hour_start = int(now) - lt.tm_min * 60 - lt.tm_sec       # top of this local hour
    midnight = hour_start - lt.tm_hour * 3600                # local midnight
    labels = []
    for i in range(24):
        h = time.localtime(hour_start - (23 - i) * 3600).tm_hour
        labels.append("%d%s" % (((h + 11) % 12) + 1, "a" if h < 12 else "p"))
    stats = {"today_calls": 0, "active_sessions": 0,
             "by_source": {"cli": 0, "hub": 0, "telegram": 0},
             "by_tool": [], "histogram": [0] * 24, "hist_labels": labels}
    if not os.path.exists(STATE_DB):
        return {"events": [], "stats": stats}
    uri = "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error:
        return {"events": [], "stats": stats}
    con.row_factory = sqlite3.Row

    # ---- timeline events (shape unchanged: 60 newest) ----
    out = []
    try:
        rows = con.execute(
            "SELECT m.role, m.content, m.tool_calls, m.tool_name, m.timestamp, "
            "s.source FROM messages m JOIN sessions s ON m.session_id = s.id "
            "WHERE m.tool_calls IS NOT NULL OR m.tool_name IS NOT NULL "
            "ORDER BY m.timestamp DESC LIMIT 80").fetchall()
    except sqlite3.Error:
        rows = []
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

    # ---- stats: today + rolling 24h, from the tool_calls JSON ----
    try:
        cutoff = min(midnight, hour_start - 23 * 3600)
        srows = con.execute(
            "SELECT m.timestamp AS ts, m.tool_calls AS tc, s.source AS src "
            "FROM messages m JOIN sessions s ON m.session_id = s.id "
            "WHERE m.tool_calls IS NOT NULL AND m.timestamp >= ?",
            (cutoff,)).fetchall()
        tool_counts = {}
        for r in srows:
            ts = r["ts"] or 0
            try:
                calls = json.loads(r["tc"])
            except Exception:
                continue
            if not isinstance(calls, list):
                continue
            names = []
            for c in calls:
                if isinstance(c, dict):
                    fn = c.get("function") or {}
                    names.append(fn.get("name") or c.get("name") or "tool")
            if not names:
                continue
            n = len(names)
            for nm in names:
                tool_counts[nm] = tool_counts.get(nm, 0) + 1
            if ts >= midnight:
                stats["today_calls"] += n
                src = r["src"] or "cli"
                stats["by_source"][src] = stats["by_source"].get(src, 0) + n
            if ts >= hour_start - 23 * 3600:
                idx = 23 if ts >= hour_start else \
                    23 - (int((hour_start - ts) // 3600) + 1)
                if 0 <= idx <= 23:
                    stats["histogram"][idx] += n
        stats["by_tool"] = [{"name": k, "count": v} for k, v in
                            sorted(tool_counts.items(),
                                   key=lambda kv: (-kv[1], kv[0]))[:6]]
    except sqlite3.Error:
        pass
    try:
        # "active" = session produced a message in the last 15 minutes.
        # (ended_at is unreliable: CLI sessions are often never closed —
        # ended_at IS NULL matched 109 sessions on the live DB.)
        stats["active_sessions"] = con.execute(
            "SELECT COUNT(DISTINCT session_id) FROM messages "
            "WHERE timestamp >= ?", (now - 900,)).fetchone()[0]
    except sqlite3.Error:
        pass
    con.close()
    return {"events": out[:60], "stats": stats}

# ---- mind_pro ----
def mind_extra():
    """Extra Mind-view analytics: skills actually used, token fuel by day, model mix, memory files. Cached 60s."""
    def build():
        out = {"skill_usage": [], "tokens_by_day": [], "model_mix": [], "memory_files": []}
        # ---- sqlite (strictly read-only) ----
        try:
            db = sqlite3.connect("file:" + urllib.parse.quote(STATE_DB) + "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
        except Exception as e:
            out["error"] = "state.db unavailable: %s" % e
            db = None
        if db:
            # (1) skills actually USED: skill_view tool calls; skill name lives in the arguments JSON
            try:
                counts = {}
                for (tc,) in db.execute(
                        "SELECT tool_calls FROM messages WHERE tool_calls LIKE '%skill_view%'"):
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
                    for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
                ]
            except Exception as e:
                out["skill_usage_error"] = str(e)
            # (2) tokens per local day, last 14 days inclusive, zero-filled
            now = time.time()
            day0 = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))  # local midnight today
            start = day0 - 13 * 86400
            buckets = {}
            for i in range(14):
                d = time.strftime("%Y-%m-%d", time.localtime(start + i * 86400 + 3600))
                buckets[d] = {"d": d, "in_tok": 0, "out_tok": 0}
            try:
                for row in db.execute(
                        "SELECT started_at, COALESCE(input_tokens,0) it, COALESCE(output_tokens,0) ot "
                        "FROM sessions WHERE started_at >= ?", (start,)):
                    d = time.strftime("%Y-%m-%d", time.localtime(row["started_at"]))
                    b = buckets.get(d)
                    if b:
                        b["in_tok"] += row["it"]
                        b["out_tok"] += row["ot"]
            except Exception as e:
                out["tokens_error"] = str(e)
            out["tokens_by_day"] = [buckets[k] for k in sorted(buckets)]
            # (3) sessions per model (short name), last 14 days
            try:
                mix = {}
                for row in db.execute(
                        "SELECT COALESCE(model,'unknown') m, COUNT(*) n FROM sessions "
                        "WHERE started_at >= ? GROUP BY m ORDER BY n DESC", (start,)):
                    t = (row["m"] or "unknown").split("/")[-1]
                    t = re.sub(r"-(\d+bit|bf16|fp16|fp32)$", "", t, flags=re.I)
                    t = re.sub(r"-Instruct(-\d+)?$", "", t, flags=re.I)
                    mix[t] = mix.get(t, 0) + row["n"]
                out["model_mix"] = [
                    {"name": k, "sessions": v}
                    for k, v in sorted(mix.items(), key=lambda kv: -kv[1])
                ]
            except Exception as e:
                out["model_mix_error"] = str(e)
            try:
                db.close()
            except Exception:
                pass
        # (4) memory files on disk
        try:
            mdir = os.path.join(HOME, ".hermes", "memories")
            files = []
            for entry in os.scandir(mdir):
                if entry.is_file() and entry.name.endswith(".md"):
                    st = entry.stat()
                    files.append({"name": entry.name, "mtime": st.st_mtime, "size": st.st_size})
            files.sort(key=lambda f: -f["mtime"])
            out["memory_files"] = files[:12]
        except Exception:
            out["memory_files"] = []
        return out
    try:
        return _cached("mind_extra", 60, build)
    except Exception as e:
        return {"error": str(e)}

EXPANDERS.update({
    "rss": expand_rss,
    "system": expand_system,
    "weather": expand_weather,
    "markets": expand_markets,
})
