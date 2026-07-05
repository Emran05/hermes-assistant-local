# aux_messages.py — Message Center store + ingest (P2.4, app-reads-chat.db).
#
# The signed native app (/Applications/Hermes Assistant.app) is the only
# process on this Mac that can hold Full Disk Access, so IT reads
# ~/Library/Messages/chat.db on a timer (see app/main.swift MessagesSync) and
# POSTs decoded conversation previews here over loopback. This module stores
# the last good ingest and REBINDS the existing "messages" widget + expander
# providers to serve from that store — the dashboard python never opens
# chat.db and never needs FDA (the launchd-python-can't-get-FDA blocker).
#
# exec'd into server.py globals AFTER expanders_extra.py (aux files load in
# sorted order, server.py:2071), so the WIDGETS["messages"] provider /
# EXPANDERS["messages"] rebinding below wins over w_messages (server.py:1585)
# and expand_messages (expanders_extra.py). Uses server globals: DATA,
# read_json, _state_lock, _widget_cache, _cached, register_get, register_post,
# WIDGETS, EXPANDERS, get_layout, save_layout. Imports ALL its own stdlib deps
# and defines only new names (MSG_* / _msg_* / *_messages_store).
#
# CLAUDE.md aux gotcha: datetime is deliberately NOT imported here. If it is
# ever needed, import ONLY under a private alias (import datetime as
# _msg_datetime) — a bare `from datetime import datetime` would rebind the
# shared global and silently break other server code.
#
# Privacy: message previews live ONLY in ~/.hermes/dashboard/messages.json
# (0600, one <=140-char preview per conversation, <=200 conversations kept).
# Message content is NEVER logged (errors report exception TYPE only) and
# never leaves loopback. Ingest is token-guarded (messages-token, 0600) so no
# other local process can spoof or read-modify the widget.

import os
import re
import sys
import json
import time
import hmac
import tempfile

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
MSG_STORE = os.path.join(DATA, "messages.json")
MSG_TOKEN_F = os.path.join(DATA, "messages-token")
MSG_MAX_RAW = 512 * 1024      # serialized ingest body cap -> 413
MSG_MAX_CONVOS = 200          # >200 conversations are capped, never rejected
MSG_PREVIEW = 140             # store-side preview clamp (app already caps 200)
MSG_STALE_S = 600             # last sync older than 10 min -> stale badge
MSG_CACHE_KEY = "messages_store"

_MSG_PHONE = re.compile(r"^\+?[0-9][0-9\-\s()]{5,}$")


# --------------------------------------------------------------------------
# shared secret — minted at module load if absent; the app reads it each tick
# --------------------------------------------------------------------------
def _msg_token():
    try:
        with open(MSG_TOKEN_F) as f:
            t = f.read().strip()
        return t or None
    except OSError:
        return None


def _msg_mint_token():
    if _msg_token():
        return
    try:
        fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".msgtok_")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(os.urandom(16).hex() + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, MSG_TOKEN_F)
            tmp = None
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception as _e:                                  # pragma: no cover
        print("[aux_messages] token mint failed: %s" % type(_e).__name__,
              file=sys.stderr)


_msg_mint_token()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _msg_pretty(hh):
    """Prettify a raw handle: phone -> (555) 123-4567; emails/names pass through."""
    if not hh:
        return "Unknown"
    hh = hh.strip()
    if "@" in hh:
        return hh
    digits = re.sub(r"[^\d+]", "", hh)
    if _MSG_PHONE.match(hh) and len(digits) >= 10:
        d = digits[-10:]
        return "(%s) %s-%s" % (d[0:3], d[3:6], d[6:10])
    return hh


def _msg_clamp_convo(c):
    """Validate + clamp one ingested conversation; None if hopeless."""
    if not isinstance(c, dict):
        return None
    try:
        name = _msg_pretty(str(c.get("name") or c.get("ident") or "Unknown"))[:80]
        sender = str(c.get("sender") or "")[:80]
        if sender and sender != "You":
            sender = _msg_pretty(sender)[:80]
        ts = float(c.get("ts") or 0)
        return {
            "name": name,
            "ident": str(c.get("ident") or "")[:80],
            "group": bool(c.get("group")),
            "participants": max(1, int(c.get("participants") or 1)),
            "last": str(c.get("last") or "")[:MSG_PREVIEW],
            "from_me": bool(c.get("from_me")),
            "sender": sender or "Unknown",
            "ts": ts if ts > 0 else 0,
            "unread": max(0, min(99999, int(c.get("unread") or 0))),
            "attachment": bool(c.get("attachment")),
            "reaction": bool(c.get("reaction")),
            "today_count": max(0, min(99999, int(c.get("today_count") or 0))),
            "service": str(c.get("service") or "")[:16],
        }
    except (TypeError, ValueError):
        return None


def _msg_write_store(obj):
    """Atomic 0600 write (temp + fchmod + fsync + os.replace)."""
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".msgstore_")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, MSG_STORE)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


# --------------------------------------------------------------------------
# POST /api/messages/ingest — the app's (token-guarded, loopback) feed
# --------------------------------------------------------------------------
def _msg_ingest_handler(ctx):
    try:
        b = ctx.body
        if not isinstance(b, dict) or not b:
            return ({"ok": False, "error": "bad body"}, 400)
        try:
            if len(json.dumps(b)) > MSG_MAX_RAW:
                return ({"ok": False, "error": "too_big"}, 413)
        except (TypeError, ValueError):
            return ({"ok": False, "error": "bad body"}, 400)
        tok = _msg_token()
        if not tok:
            _msg_mint_token()                      # self-heal for the next tick
            return ({"ok": False, "error": "no token"}, 403)
        sent = b.get("token")
        if not isinstance(sent, str) or not hmac.compare_digest(sent, tok):
            return ({"ok": False, "error": "forbidden"}, 403)
        if b.get("v") != 1:
            return ({"ok": False, "error": "bad body"}, 400)
        raw = b.get("conversations")
        if not isinstance(raw, list):
            return ({"ok": False, "error": "bad body"}, 400)

        fda = bool(b.get("fda"))
        convos = []
        for c in raw[:MSG_MAX_CONVOS]:             # cap, never reject on count
            cc = _msg_clamp_convo(c)
            if cc is not None:
                convos.append(cc)
        if not fda:
            convos = []

        totals = b.get("totals") if isinstance(b.get("totals"), dict) else {}
        try:
            unread = max(0, int(totals.get("unread") or 0))
        except (TypeError, ValueError):
            unread = sum(c["unread"] for c in convos)
        try:
            today = max(0, int(totals.get("today") or 0))
        except (TypeError, ValueError):
            today = sum(c["today_count"] for c in convos)
        try:
            gen = float(b.get("generated_at") or 0) or time.time()
        except (TypeError, ValueError):
            gen = time.time()

        now = time.time()
        store = {"v": 1, "fda": fda, "generated_at": gen, "stored_at": now,
                 "host": str(b.get("host") or "")[:64],
                 "reason": str(b.get("reason") or "")[:200],
                 "conversations": convos,
                 "totals": {"unread": unread if fda else 0,
                            "today": today if fda else 0}}
        with _state_lock:
            _msg_write_store(store)
        _widget_cache.pop(MSG_CACHE_KEY, None)
        return {"ok": True, "stored": len(convos), "at": now, "fda": fda}
    except Exception as e:
        # NEVER echo body content into an error or log — type name only.
        return ({"ok": False, "error": "internal: " + type(e).__name__}, 500)


# --------------------------------------------------------------------------
# store -> widget/pop-out contract (the three degradation states)
# --------------------------------------------------------------------------
def _msg_read_store():
    base = {"ok": True, "available": False, "grant": False,
            "never_synced": False, "stale": False, "age_s": None,
            "generated_at": None, "host": "", "conversations": [],
            "total_unread": 0, "convo_count": 0, "today_count": 0}
    try:
        st = read_json(MSG_STORE, None)
    except Exception:
        st = None
    if not isinstance(st, dict) or st.get("v") != 1:
        base["never_synced"] = True
        base["reason"] = "Waiting for the Hermes app to sync Messages…"
        return base
    if not st.get("fda"):
        base["grant"] = True
        base["reason"] = "Full Disk Access needed to read Messages."
        try:
            base["generated_at"] = float(st.get("generated_at") or 0) or None
        except (TypeError, ValueError):
            pass
        return base
    convos = st.get("conversations")
    if not isinstance(convos, list):
        convos = []
    totals = st.get("totals") if isinstance(st.get("totals"), dict) else {}
    try:
        gen = float(st.get("generated_at") or st.get("stored_at") or 0)
    except (TypeError, ValueError):
        gen = 0
    age = max(0.0, time.time() - gen) if gen else None
    base.update(
        available=True,
        conversations=convos,
        total_unread=max(0, int(totals.get("unread") or 0)),
        convo_count=len(convos),
        today_count=max(0, int(totals.get("today") or 0)),
        generated_at=gen or None,
        age_s=(round(age, 1) if age is not None else None),
        stale=bool(age is not None and age > MSG_STALE_S),
        host=str(st.get("host") or "")[:64],
    )
    return base


def _msg_data():
    return _cached(MSG_CACHE_KEY, 5, _msg_read_store)


def w_messages_store():
    """Card provider: top 6 conversations + counts (replaces w_messages)."""
    d = dict(_msg_data())
    d["conversations"] = list(d.get("conversations") or [])[:6]
    return d


def expand_messages_store():
    """Pop-out provider: the full store (replaces expand_messages)."""
    return dict(_msg_data())


def _msg_get_handler(ctx):
    try:
        return _msg_read_store()          # always fresh for the API surface
    except Exception as e:
        return {"ok": False, "available": False, "grant": False,
                "reason": "internal: " + type(e).__name__}


# --------------------------------------------------------------------------
# module-load side effects: routes, provider REBIND, layout injection
# --------------------------------------------------------------------------
register_post("/api/messages/ingest", _msg_ingest_handler)
register_get("/api/messages", _msg_get_handler)

try:
    if isinstance(WIDGETS.get("messages"), dict):
        WIDGETS["messages"]["provider"] = w_messages_store   # was w_messages
    EXPANDERS["messages"] = expand_messages_store            # was expand_messages
except Exception as _msg_e:                                  # pragma: no cover
    print("[aux_messages] provider rebind failed: %s" % type(_msg_e).__name__,
          file=sys.stderr)

# Show up without a manual add: append to the layout order IF absent (never
# clobber the user's order) — same pattern as aux_claude_usage.
try:
    _msg_lay = get_layout()
    if isinstance(_msg_lay, dict):
        _msg_order = _msg_lay.get("order")
        if isinstance(_msg_order, list) and "messages" not in _msg_order:
            _msg_order.append("messages")
            save_layout(_msg_lay)
except Exception as _msg_e:                                  # pragma: no cover
    print("[aux_messages] layout inject failed: %s" % type(_msg_e).__name__,
          file=sys.stderr)
