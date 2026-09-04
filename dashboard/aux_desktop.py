# aux_desktop.py — "Agent Desktop" backend: a LOCAL-ONLY desktop screenshot
# stream + the computer_use flight-recorder timeline + on-demand capture.
#
# This is the read/observe half of the "give the agent hands" trio (hands-3).
# It owns NO dangerous capability: it captures screenshots for the loopback
# dashboard and reads the recorder. It exposes no merge / approve / send.
#
# PRIVACY (enforced here, in code):
#   * Every screenshot written under ~/.hermes/dashboard/desktop/ is chmod 0600.
#   * The store is a ring buffer: capped at DESK_MAX_FRAMES and auto-pruned of
#     anything older than DESK_MAX_AGE_S on every list/capture.
#   * Frames are served ONLY over the loopback dashboard, as a base64 data URI
#     inside JSON (the aux dispatcher normalises every return to JSON — there is
#     no raw-bytes path, which keeps frames on the loopback JSON channel).
#   * No screenshot byte is ever sent to Telegram / Gmail / any host. Capture is
#     on-demand (Capture-now button or the agent), never a background timer.
#
# Loader note: aux_desktop sorts BEFORE aux_recorder, so recorder_record_local
# does NOT exist when this module is exec'd. Every handler reads it from globals
# at CALL time (by which point all aux modules have loaded). The timeline opens
# recorder.db read-only itself (reusing the recorder RO-connection pattern) so
# it depends on no aux load order.
#
# Defines only new names (DESK_*, _desk_*, desktop_*). Uses server.py globals:
# DATA, register_get, register_post. No `from datetime import datetime` (aux
# private-alias gotcha) — epoch seconds only.

import os
import time
import base64
import threading
import subprocess
import sqlite3
import urllib.parse

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
DESK_DIR        = os.path.join(DATA, "desktop")
DESK_REC_DB     = os.path.join(DATA, "recorder.db")
DESK_MAX_FRAMES = 30            # ring-buffer cap (newest kept)
DESK_MAX_AGE_S  = 24 * 3600     # prune anything older than 24h
DESK_FULL_EDGE  = 1400          # full-frame long edge (sips downscale)
DESK_THUMB_EDGE = 360           # thumbnail long edge
DESK_MIN_GAP_S  = 1.5           # capture rate-limit: min seconds between captures
DESK_LIST_LIMIT = 12            # thumbs returned inline by /shots (newest-first)
DESK_TL_LIMIT   = 40            # timeline rows
DESK_REC_MIN_GAP = 60           # rate-limit recorder provenance rows to 1/min
DESK_SUMMARY_CAP = 220          # truncate huge computer_use element dumps

DESK_NOTE = ("Local only — captures never leave this Mac. Stored 0600, "
             "auto-pruned, and shown only in this loopback dashboard.")

_desk_cap_lock = threading.Lock()
_desk_last_cap = [0.0]          # last capture epoch (rate-limit)
_desk_last_rec = [0.0]          # last recorder-row epoch (rate-limit)
_desk_last_err = [""]           # last capture error (surfaced in state)


# --------------------------------------------------------------------------
# store helpers
# --------------------------------------------------------------------------
def _desk_ensure_dir():
    try:
        os.makedirs(DESK_DIR, mode=0o700, exist_ok=True)
    except OSError:
        pass


def _desk_is_frame(name):
    return name.startswith("shot-") and name.endswith(".jpg")


def _desk_thumb_name(frame):
    # shot-<ms>.jpg -> thumb-<ms>.jpg
    return "thumb-" + frame[len("shot-"):]


def _desk_frames():
    """Full frames, newest-first, as (name, mtime)."""
    out = []
    try:
        for n in os.listdir(DESK_DIR):
            if not _desk_is_frame(n):
                continue
            p = os.path.join(DESK_DIR, n)
            try:
                out.append((n, os.path.getmtime(p)))
            except OSError:
                continue
    except OSError:
        return []
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _desk_prune():
    """Enforce count + age caps; drop each frame's thumb with it; sweep orphans."""
    frames = _desk_frames()
    now = time.time()
    keep = set()
    for i, (n, mt) in enumerate(frames):
        if i < DESK_MAX_FRAMES and (now - mt) <= DESK_MAX_AGE_S:
            keep.add(n)
    for n, _mt in frames:
        if n in keep:
            continue
        _desk_rm(os.path.join(DESK_DIR, n))
        _desk_rm(os.path.join(DESK_DIR, _desk_thumb_name(n)))
    # sweep orphan thumbs whose frame is gone
    try:
        for n in os.listdir(DESK_DIR):
            if n.startswith("thumb-") and n.endswith(".jpg"):
                frame = "shot-" + n[len("thumb-"):]
                if not os.path.exists(os.path.join(DESK_DIR, frame)):
                    _desk_rm(os.path.join(DESK_DIR, n))
    except OSError:
        pass


def _desk_rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _desk_data_uri(path):
    try:
        with open(path, "rb") as f:
            b = f.read()
        return "data:image/jpeg;base64," + base64.b64encode(b).decode("ascii")
    except OSError:
        return ""


def _desk_capturable():
    return os.path.exists("/usr/sbin/screencapture")


def _desk_in_dir(name):
    """Realpath-pin a requested file to DESK_DIR (defeats traversal)."""
    name = os.path.basename(name or "")
    if not name or not name.endswith(".jpg"):
        return None
    if not (name.startswith("shot-") or name.startswith("thumb-")):
        return None
    p = os.path.realpath(os.path.join(DESK_DIR, name))
    root = os.path.realpath(DESK_DIR)
    if p == root or p.startswith(root + os.sep):
        return p if os.path.isfile(p) else None
    return None


# --------------------------------------------------------------------------
# capture (on-demand, rate-limited, downscaled, 0600)
# --------------------------------------------------------------------------
def _desk_capture(display=None):
    _desk_ensure_dir()
    if not _desk_capturable():
        _desk_last_err[0] = "screencapture not found"
        return {"ok": False, "reason": "screencapture not available on this Mac"}

    now = time.time()
    with _desk_cap_lock:
        if now - _desk_last_cap[0] < DESK_MIN_GAP_S:
            # rate-limited: don't shoot again, just return current state
            return _desk_state(rate_limited=True)
        _desk_last_cap[0] = now

        ms = int(now * 1000)
        frame = "shot-%d.jpg" % ms
        fp = os.path.join(DESK_DIR, frame)
        args = ["/usr/sbin/screencapture", "-x", "-t", "jpg"]
        if isinstance(display, int) and display > 0:
            args += ["-D", str(display)]
        args.append(fp)
        try:
            subprocess.run(args, capture_output=True, timeout=15, check=False)
        except Exception as e:
            _desk_last_err[0] = "%s: %s" % (type(e).__name__, e)
            return {"ok": False, "reason": "capture failed: " + str(e)}

        # a TCC-denied capture exits 0 but writes nothing / 0 bytes
        if not os.path.exists(fp) or os.path.getsize(fp) == 0:
            _desk_rm(fp)
            _desk_last_err[0] = ("empty capture — grant Screen Recording "
                                 "(System Settings > Privacy) to the dashboard")
            return {"ok": False, "reason": _desk_last_err[0]}

        # downscale the full frame IN PLACE first (sips rewrites the file and
        # would otherwise reset perms to umask), THEN lock perms to 0600.
        _desk_sips(fp, DESK_FULL_EDGE, None)
        try:
            os.chmod(fp, 0o600)
        except OSError:
            pass
        # thumbnail
        thumb = os.path.join(DESK_DIR, _desk_thumb_name(frame))
        _desk_sips(fp, DESK_THUMB_EDGE, thumb)
        _desk_last_err[0] = ""

    _desk_prune()
    _desk_record_capture()
    st = _desk_state()
    st["captured"] = frame
    return st


def _desk_sips(src, edge, out):
    args = ["/usr/bin/sips", "-Z", str(edge), src]
    if out:
        args += ["--out", out]
    try:
        subprocess.run(args, capture_output=True, timeout=15, check=False)
        target = out or src   # in-place downscale rewrites src -> re-lock it
        if os.path.exists(target):
            os.chmod(target, 0o600)
    except Exception:
        # sips missing/failed: keep the full frame as-is; if thumb failed the
        # lister falls back to the full frame's data URI.
        pass


def _desk_record_capture():
    """Log capture provenance to the flight recorder (read-kind, rate-limited).
    recorder_record_local is resolved from globals at CALL time (load order)."""
    now = time.time()
    if now - _desk_last_rec[0] < DESK_REC_MIN_GAP:
        return
    rrl = globals().get("recorder_record_local")
    if not callable(rrl):
        return
    try:
        # reversible="n/a", not "no": a capture reads the screen and changes
        # nothing on the Mac, so there is no state to restore. aux_recorder's
        # REVERSIBLE_POLICY gives every read/net/agent kind "n/a" (1.0.0) and
        # the Flight Recorder draws NO badge for it — "no" painted every
        # screenshot with the same irreversible warning as an rm -rf.
        rrl("screencapture", "desktop-frame", kind="read", reversible="n/a",
            source="dashboard", status="done",
            summary="desktop thumbnail (local only, never sent)")
        _desk_last_rec[0] = now
    except Exception:
        pass


# --------------------------------------------------------------------------
# state + shot list
# --------------------------------------------------------------------------
def _desk_shots(limit=DESK_LIST_LIMIT):
    _desk_ensure_dir()
    _desk_prune()
    frames = _desk_frames()
    shots = []
    for n, mt in frames[:limit]:
        thumb = os.path.join(DESK_DIR, _desk_thumb_name(n))
        src = thumb if os.path.exists(thumb) else os.path.join(DESK_DIR, n)
        shots.append({"name": n, "ts": mt, "thumb": _desk_data_uri(src)})
    return shots, len(frames)


def _desk_state(rate_limited=False):
    shots, count = _desk_shots()
    return {
        "ok": True,
        "shots": shots,
        "count": count,
        "capturable": _desk_capturable(),
        "rate_limited": rate_limited,
        "err": _desk_last_err[0],
        "note": DESK_NOTE,
    }


# --------------------------------------------------------------------------
# timeline — recent computer_use rows from recorder.db (read-only)
# --------------------------------------------------------------------------
def _desk_timeline(limit=DESK_TL_LIMIT):
    if not os.path.exists(DESK_REC_DB):
        return {"ok": True, "available": False, "rows": [],
                "reason": "recorder not initialised yet"}
    try:
        uri = "file:" + urllib.parse.quote(DESK_REC_DB) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error as e:
        return {"ok": True, "available": False, "rows": [], "reason": str(e)}
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, ts, source, tool, target, kind, reversible, status, "
            "duration_s, summary FROM actions WHERE kind='computer' "
            "ORDER BY ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error as e:
        con.close()
        return {"ok": True, "available": False, "rows": [], "reason": str(e)}
    con.close()

    out = []
    for r in rows:
        summ = (r["summary"] or "")
        if len(summ) > DESK_SUMMARY_CAP:
            summ = summ[:DESK_SUMMARY_CAP] + "…"
        out.append({
            "id": r["id"], "ts": r["ts"], "source": r["source"],
            "tool": r["tool"], "target": r["target"], "kind": r["kind"],
            "reversible": r["reversible"], "status": r["status"],
            "duration_s": r["duration_s"], "summary": summ,
            "action": _desk_action_label(r["tool"], r["target"]),
        })
    return {"ok": True, "available": True, "rows": out}


def _desk_action_label(tool, target):
    t = (target or "").strip()
    # computer_use rows record the action verb in `target` (capture/left_click/
    # type/key/scroll/...); surface it cleanly for the timeline.
    verb = t.split()[0] if t else (tool or "action")
    pretty = {
        "capture": "Screenshot", "screenshot": "Screenshot",
        "left_click": "Click", "click": "Click", "double_click": "Double-click",
        "right_click": "Right-click", "type": "Type", "key": "Key press",
        "scroll": "Scroll", "drag": "Drag", "left_click_drag": "Drag",
        "mouse_move": "Move cursor", "wait": "Wait",
        "open_application": "Open app", "list_apps": "List apps",
    }.get(verb, verb.replace("_", " ").title() or "Action")
    return pretty


# --------------------------------------------------------------------------
# route handlers
# --------------------------------------------------------------------------
def desktop_shots_handler(ctx):
    try:
        return _desk_state()
    except Exception as e:
        return ({"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}, 500)


def desktop_shot_handler(ctx):
    name = ctx.q1("name", "")
    p = _desk_in_dir(name)
    if not p:
        return {"ok": False, "reason": "no such frame"}
    try:
        return {"ok": True, "name": os.path.basename(p),
                "ts": os.path.getmtime(p), "data_uri": _desk_data_uri(p)}
    except Exception as e:
        return ({"ok": False, "reason": str(e)}, 500)


def desktop_capture_handler(ctx):
    body = ctx.body or {}
    display = body.get("display")
    try:
        display = int(display) if display not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        display = None
    try:
        return _desk_capture(display=display)
    except Exception as e:
        return ({"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}, 500)


def desktop_timeline_handler(ctx):
    try:
        limit = min(200, max(1, int(ctx.q1("limit", str(DESK_TL_LIMIT)) or DESK_TL_LIMIT)))
    except ValueError:
        limit = DESK_TL_LIMIT
    try:
        return _desk_timeline(limit)
    except Exception as e:
        return ({"ok": True, "available": False, "rows": [],
                 "reason": "%s: %s" % (type(e).__name__, e)})


# --------------------------------------------------------------------------
# module-load side effects: register routes (ensure the store dir exists 0700)
# --------------------------------------------------------------------------
_desk_ensure_dir()

register_get("/api/desktop/shots", desktop_shots_handler)
register_get("/api/desktop/shot", desktop_shot_handler)
register_get("/api/desktop/timeline", desktop_timeline_handler)
register_post("/api/desktop/capture", desktop_capture_handler)
