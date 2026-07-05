# aux_shortcuts.py — P3.1 Shortcuts action-bus (governed by the P1.3 engine).
#
# exec'd into server.py's globals by the aux-module loader (sorted after
# aux_recorder.py / aux_permissions.py, so recorder_record_local and the
# route registry exist).  Defines only new names (SB_* / _sb_* / shortcuts_*)
# plus ONE deliberate global rebind: access_preamble is wrapped (not edited —
# same runtime-override pattern expanders_extra.py uses) to teach the agent
# that Shortcut runs go through the bus, never through raw `shortcuts run`.
#
# WHY A BUS: `shortcuts run <name>` matches NO hermes DANGEROUS_PATTERNS entry
# (verified: ~/.hermes/hermes-agent/tools/approval.py — the only "shortcut"
# hit is a docstring), so a bare terminal invocation would execute with ZERO
# approval.  The only verifiable gate is a dashboard-owned endpoint that
# consults permissions.decide() BEFORE any subprocess spawns.  That is what
# this module is.
#
# GATE MODEL (v1 — deliberately maximal):
#   * Allowlist: NO shortcut is agent-runnable until the user exposes it in
#     the dashboard card (config in ~/.hermes/dashboard/shortcuts.json, 600).
#   * Every run resolves pattern_key "shortcuts-run:<uuid>" through
#     permissions.decide().  Until the orchestrator lands the permissions.py
#     patch, _heuristic() maps that key to class "unknown" (floor ask) — so
#     the degraded and patched behaviors are IDENTICAL for v1: every run asks.
#   * v1 hard rule: shortcut runs are NEVER auto.  Even if decide() ever
#     returned "auto", the bus clamps it to ask (defense in depth on top of
#     the class floor).
#   * tier ask  -> single-use ticket (5-min expiry, in-memory, dies with the
#                  process); nothing spawns until {ticket, approved:true}
#                  comes back.  tier never -> 403, audited.
#   * EVERY attempt/outcome -> recorder_record_local (tool "shortcut_run",
#     kind "other" => /api/undo refuses).  EVERY decision -> permissions.audit()
#     with the hermes_rpc action vocabulary (asked / user-approve / user-deny /
#     auto-denied), so Trust-panel stats aggregate bus activity for free.
#
# Subprocess discipline: argv list, shell=False; the registry key is the UUID
# from `shortcuts list --show-identifiers`, and the CURRENT name is re-resolved
# from that UUID immediately before spawning (rename-stable policy identity);
# names beginning with "-" are refused (flag-injection guard).
#
# datetime gotcha: this module uses only `time` — no datetime import at all.

import json
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import time

import permissions as _sb_perm

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
SB_BIN = os.environ.get("HERMES_SHORTCUTS_BIN", "/usr/bin/shortcuts")
SB_DIR = os.path.join(os.path.expanduser("~"), ".hermes", "dashboard")
SB_CONFIG_FILE = os.path.join(SB_DIR, "shortcuts.json")
SB_IO_DIR = os.path.join(SB_DIR, "shortcut-io")
SB_RECORDER_DB = os.path.join(SB_DIR, "recorder.db")

SB_CLASS = "shortcuts-run"        # pattern_key prefix; class id in the
                                  # proposed permissions.py patch.  Until that
                                  # lands, _heuristic maps it to "unknown"
                                  # (floor ask) — same net behavior.
SB_TICKET_TTL = 300               # seconds a pending approval stays redeemable
SB_TICKET_PURGE = 900             # seconds before consumed/expired tickets drop
SB_RUN_TIMEOUT = 60               # subprocess kill deadline
SB_LIST_CACHE_S = 30              # `shortcuts list` cache
SB_OUT_CAP = 8192                 # output returned to the caller
SB_REC_CAP = 2048                 # output stored in recorder after_state
SB_IN_CAP = 16384                 # max input payload

_SB_LOCK = threading.Lock()
_SB_LIST_CACHE = {"ts": 0.0, "items": None, "err": None}
_SB_TICKETS = {}                  # token -> ticket dict (in-memory, fail-closed)
_SB_LINE_RE = re.compile(r"^(.*) \(([0-9A-Fa-f-]{36})\)\s*$")


# --------------------------------------------------------------------------
# risk labeling — curated for the user's installed shortcuts + keyword fallback
# --------------------------------------------------------------------------
_SB_RISK_EXACT = {
    "spam text": ("high", "Sends text messages"),
    "text last image": ("high", "Sends your most recent photo as a message"),
    "yas download": ("med", "Downloads media from the web"),
    "skip forward": ("low", "Media playback control"),
    "take a break": ("low", "Starts a break timer"),
    "what's a shortcut?": ("low", "Opens Shortcuts help"),
    "what’s a shortcut?": ("low", "Opens Shortcuts help"),
    "shazam shortcut": ("med", "Listens through the microphone to identify audio"),
}

_SB_RISK_KEYWORDS = (
    (("spam", "text", "sms", "imessage", "message", "send", "mail", "email",
      "post", "tweet", "dm"), "high", "Name suggests it sends messages or posts"),
    (("delete", "remove", "erase", "clear"), "high", "Name suggests it deletes data"),
    (("download", "fetch"), "med", "Name suggests it downloads content"),
)


def _sb_risk(name):
    key = (name or "").strip().lower()
    hit = _SB_RISK_EXACT.get(key)
    if hit:
        return {"level": hit[0], "label": hit[1]}
    words = set(re.findall(r"[a-z']+", key))
    for kws, level, label in _SB_RISK_KEYWORDS:
        if words & set(kws):
            return {"level": level, "label": label}
    return {"level": "unknown",
            "label": "Contents unknown — review in Shortcuts.app before exposing"}


# --------------------------------------------------------------------------
# installed shortcuts (subprocess `shortcuts list --show-identifiers`, cached)
# --------------------------------------------------------------------------
def _sb_installed(force=False):
    """Return (items, err); items = [{id, name}], UUIDs uppercased."""
    now = time.time()
    with _SB_LOCK:
        c = _SB_LIST_CACHE
        if not force and c["items"] is not None and now - c["ts"] < SB_LIST_CACHE_S:
            return c["items"], c["err"]
    items, err = [], None
    if not os.path.exists(SB_BIN):
        err = "shortcuts CLI not found at " + SB_BIN
    else:
        try:
            p = subprocess.run([SB_BIN, "list", "--show-identifiers"],
                               capture_output=True, text=True, errors="replace",
                               timeout=10, stdin=subprocess.DEVNULL)
            if p.returncode != 0:
                err = "shortcuts list failed: " + (p.stderr or "").strip()[:200]
            else:
                for line in (p.stdout or "").splitlines():
                    m = _SB_LINE_RE.match(line.strip())
                    if m:
                        items.append({"id": m.group(2).upper(), "name": m.group(1)})
        except Exception as e:
            err = type(e).__name__ + ": " + str(e)
    with _SB_LOCK:
        _SB_LIST_CACHE.update(ts=now, items=items, err=err)
    return items, err


# --------------------------------------------------------------------------
# exposure config — ~/.hermes/dashboard/shortcuts.json (600, atomic writes)
# Corrupt/missing file => empty allowlist => every run 403s (fail closed).
# --------------------------------------------------------------------------
def _sb_load_config():
    try:
        with open(SB_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict) or not isinstance(cfg.get("exposed"), dict):
            raise ValueError("bad shape")
        return cfg
    except Exception:
        return {"version": 1, "updated": 0.0, "exposed": {}}


def _sb_save_config(cfg):
    cfg = {"version": 1, "updated": round(time.time(), 3),
           "exposed": cfg.get("exposed") or {}}
    raw = (json.dumps(cfg, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        os.makedirs(SB_DIR, mode=0o700, exist_ok=True)
    except OSError:
        pass
    tmp = "%s.tmp.%d" % (SB_CONFIG_FILE, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, SB_CONFIG_FILE)
    return cfg


# --------------------------------------------------------------------------
# recorder / audit / metrics glue (NameError-tolerant: a failed sibling aux
# module must never take the bus down — but the bus NEVER runs unrecorded:
# if the recorder is unavailable the caller refuses to execute)
# --------------------------------------------------------------------------
def _sb_record(event, name, sid, src, status, summary, extra=None, after=None):
    """recorder.db row for every bus event.  Returns True when recorded."""
    fn = globals().get("recorder_record_local")
    if not callable(fn):
        return False
    args = {"id": sid, "event": event, "source": src}
    if extra:
        args.update(extra)
    try:
        r = fn(tool="shortcut_config" if event == "config" else "shortcut_run",
               target=name or "", kind="other",
               reversible="no", source="shortcut-bus", args=args, status=status,
               summary=(summary or "")[:300], after_state=after,
               tool_call_id="sb-%s-%s" % (event, secrets.token_hex(6)))
        return bool(isinstance(r, dict) and r.get("ok"))
    except Exception:
        return False


def _sb_audit(src, pk, cmd, verdict, action, choice=""):
    try:
        _sb_perm.audit("shortcut-bus:" + (src or "api"),
                       {"pattern_key": pk, "command": cmd}, verdict, action, choice)
    except Exception:
        pass


def _sb_metric(**fields):
    fn = globals().get("metrics_record")
    if callable(fn):
        try:
            fn("shortcut_run", **fields)
        except Exception:
            pass


# --------------------------------------------------------------------------
# ticket store — in-memory, single-use, 5-minute expiry.  Deliberately NOT
# persisted: a dashboard restart voids any consent-in-flight (fail closed).
# --------------------------------------------------------------------------
def _sb_sweep_tickets(now):
    """Caller holds _SB_LOCK.  Expire stale pendings; purge old carcasses."""
    for tok in list(_SB_TICKETS):
        t = _SB_TICKETS[tok]
        if t["state"] == "pending" and now > t["expires"]:
            t["state"] = "expired"
            _sb_audit(t["src"], t["pk"], t["cmd"],
                      {"tier": "ask", "class": t["cls"]}, "auto-denied", "expired")
        if now > t["created"] + SB_TICKET_PURGE:
            _SB_TICKETS.pop(tok, None)


def _sb_pending_list():
    now = time.time()
    with _SB_LOCK:
        _sb_sweep_tickets(now)
        out = []
        for tok, t in _SB_TICKETS.items():
            if t["state"] == "pending":
                out.append({"ticket": tok, "id": t["sid"], "name": t["name"],
                            "source": t["src"], "requested": t["created"],
                            "expires_in": max(0, int(t["expires"] - now))})
        out.sort(key=lambda x: x["requested"])
        return out


# --------------------------------------------------------------------------
# execution — the ONLY place a Shortcut subprocess spawns
# --------------------------------------------------------------------------
def _sb_execute(sid, input_text):
    """Run one exposed, user-approved shortcut.  Returns a result dict."""
    start = time.time()
    items, err = _sb_installed(force=True)      # fresh name-by-UUID resolution
    cur = next((i for i in items if i["id"] == sid), None)
    if cur is None:
        return {"outcome": "error", "rc": -1, "output": "",
                "error": "shortcut no longer installed" + ((" (" + err + ")") if err else ""),
                "duration_s": 0.0, "name": ""}
    name = cur["name"]
    if name.startswith("-"):
        return {"outcome": "error", "rc": -1, "output": "",
                "error": "shortcut name begins with '-' — rename it in Shortcuts.app",
                "duration_s": 0.0, "name": name}
    argv = [SB_BIN, "run", name]
    tmp = None
    try:
        if input_text:
            try:
                os.makedirs(SB_IO_DIR, mode=0o700, exist_ok=True)
            except OSError:
                pass
            tmp = os.path.join(SB_IO_DIR, "in-%s.txt" % secrets.token_hex(8))
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, input_text.encode("utf-8", "replace"))
            finally:
                os.close(fd)
            argv += ["-i", tmp]
        try:
            p = subprocess.run(argv, capture_output=True, text=True,
                               errors="replace", timeout=SB_RUN_TIMEOUT,
                               stdin=subprocess.DEVNULL)
            rc = p.returncode
            out = (p.stdout or "")[:SB_OUT_CAP]
            errout = (p.stderr or "").strip()[:1024]
            outcome = "done" if rc == 0 else "error"
        except subprocess.TimeoutExpired:
            rc, out, errout, outcome = -1, "", \
                "timed out after %ds (shortcuts that open UI hang headless)" % SB_RUN_TIMEOUT, \
                "timeout"
        except Exception as e:
            rc, out, errout, outcome = -1, "", type(e).__name__ + ": " + str(e), "error"
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return {"outcome": outcome, "rc": rc, "output": out,
            "error": errout if outcome != "done" else "",
            "duration_s": round(time.time() - start, 2), "name": name}


# --------------------------------------------------------------------------
# recorder read-back (last runs / recent feed) — read-only sqlite
# --------------------------------------------------------------------------
def _sb_runs_from_recorder(limit=120):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % SB_RECORDER_DB, uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT ts, target, args, status, summary FROM actions "
                "WHERE tool='shortcut_run' ORDER BY ts DESC LIMIT ?",
                (int(limit),)).fetchall()
        finally:
            con.close()
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            args = json.loads(r["args"]) if r["args"] else {}
        except ValueError:
            args = {}
        if (args or {}).get("event") == "config":   # legacy rows from v1 drills
            continue
        out.append({"ts": r["ts"], "name": r["target"],
                    "id": (args or {}).get("id", ""),
                    "event": (args or {}).get("event", ""),
                    "status": r["status"], "summary": r["summary"]})
    return out


# --------------------------------------------------------------------------
# GET /api/shortcuts
# --------------------------------------------------------------------------
def shortcuts_get_handler(ctx):
    items, err = _sb_installed(force=bool(ctx.q1("refresh")))
    cfg = _sb_load_config()
    exposed = cfg.get("exposed") or {}
    runs = _sb_runs_from_recorder()
    last_by_id = {}
    for r in runs:
        if r["id"] and r["id"] not in last_by_id:
            last_by_id[r["id"]] = {"ts": r["ts"], "status": r["status"],
                                   "summary": r["summary"]}
    shortcuts = []
    for it in items:
        is_exposed = it["id"] in exposed
        tier = None
        if is_exposed:
            try:
                v = _sb_perm.decide({"pattern_key": SB_CLASS + ":" + it["id"]})
                tier = "ask" if v.get("tier") == "auto" else v.get("tier")
            except Exception:
                tier = "ask"
        shortcuts.append({"id": it["id"], "name": it["name"],
                          "exposed": is_exposed, "risk": _sb_risk(it["name"]),
                          "tier": tier, "last_run": last_by_id.get(it["id"])})
    # stale exposures (uninstalled but still allowlisted) — surface, don't hide
    ids = {it["id"] for it in items}
    for sid, meta in exposed.items():
        if sid not in ids:
            shortcuts.append({"id": sid, "name": (meta or {}).get("name", sid),
                              "exposed": True, "missing": True,
                              "risk": _sb_risk((meta or {}).get("name", "")),
                              "tier": None, "last_run": last_by_id.get(sid)})
    out = {"ok": True, "available": not (err and not items), "bin": SB_BIN,
           "shortcuts": shortcuts,
           "exposed_count": sum(1 for s in shortcuts if s.get("exposed")),
           "pending": _sb_pending_list(),
           "recent": runs[:20],
           "policy": {"class": SB_CLASS,
                      "registered": SB_CLASS in getattr(_sb_perm, "CLASS_META", {}),
                      "note": "every run requires approval in v1 (floor: ask)"}}
    if err:
        out["error"] = err
    return out


# --------------------------------------------------------------------------
# POST /api/shortcuts/config — expose / unexpose (user-only, from the UI)
# --------------------------------------------------------------------------
def shortcuts_config_handler(ctx):
    body = ctx.body or {}
    items, err = _sb_installed()
    sid = str(body.get("id") or "").strip().upper()
    want = bool(body.get("exposed"))
    entry = next((i for i in items if i["id"] == sid), None)
    if entry is None and not (sid and not want):
        return ({"ok": False, "error": "unknown shortcut id (refresh the list)"}, 404)
    name = entry["name"] if entry else (sid and _sb_load_config()
                                        .get("exposed", {}).get(sid, {}).get("name", sid))
    if want and entry and entry["name"].startswith("-"):
        return ({"ok": False, "error": "shortcut name begins with '-' — rename it "
                                       "in Shortcuts.app before exposing"}, 400)
    with _SB_LOCK:
        cfg = _sb_load_config()
        exp = cfg.get("exposed") or {}
        if want:
            exp[sid] = {"name": entry["name"], "ts": round(time.time(), 3)}
        else:
            exp.pop(sid, None)
        cfg["exposed"] = exp
        try:
            _sb_save_config(cfg)
        except Exception as e:
            return ({"ok": False, "error": "could not save config: " + str(e)}, 500)
    _sb_record("config", name, sid, str(body.get("source") or "ui")[:24], "done",
               ("exposed" if want else "unexposed") + " · " + str(name),
               extra={"exposed": want})
    return {"ok": True, "id": sid, "exposed": want,
            "exposed_count": len(cfg["exposed"])}


# --------------------------------------------------------------------------
# POST /api/shortcuts/run — request / redeem
# --------------------------------------------------------------------------
def _sb_resolve(body):
    """Resolve {id}|{name} against the installed list.  (entry, (msg, status))."""
    items, err = _sb_installed()
    if err and not items:
        return None, ("shortcuts CLI unavailable: " + err, 503)
    sid = str(body.get("id") or "").strip().upper()
    if sid:
        e = next((i for i in items if i["id"] == sid), None)
        return (e, None) if e else (None, ("no installed shortcut with that id", 404))
    name = str(body.get("name") or "").strip()
    if not name:
        return None, ("provide \"name\" or \"id\"", 400)
    for probe in (name, name.replace("'", "’")):   # straight->curly quote
        matches = [i for i in items if i["name"].strip().lower() == probe.lower()]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, ("ambiguous name — use \"id\"", 400)
    return None, ("no installed shortcut named %r" % name, 404)


def _sb_finish_run(t, res, http_extra=None):
    """Shared post-execution bookkeeping for a redeemed ticket."""
    status = "done" if res["outcome"] == "done" else "error"
    summary = "%s · %s · %.1fs · %s" % (
        res.get("name") or t["name"], t["tier"], res["duration_s"], res["outcome"])
    _sb_record("run", res.get("name") or t["name"], t["sid"], t["src"], status,
               summary, extra={"tier": t["tier"], "ticket": t["token"][:8]},
               after={"exit_code": res["rc"],
                      "output_head": res["output"][:SB_REC_CAP],
                      "error": res["error"], "duration_s": res["duration_s"]})
    _sb_metric(name=res.get("name") or t["name"], outcome=res["outcome"],
               ms=int(res["duration_s"] * 1000), source=t["src"])
    out = {"ok": res["outcome"] == "done", "status": res["outcome"],
           "name": res.get("name") or t["name"], "output": res["output"],
           "error": res["error"], "duration_s": res["duration_s"]}
    if http_extra:
        out.update(http_extra)
    return out


def _sb_redeem(body, src):
    token = str(body.get("ticket") or "")
    approved = bool(body.get("approved"))
    now = time.time()
    with _SB_LOCK:
        _sb_sweep_tickets(now)
        t = _SB_TICKETS.get(token)
        if t is None:
            _sb_record("redeem", "", "", src, "blocked",
                       "unknown ticket (never issued, purged, or bus restarted)")
            return ({"ok": False, "error": "unknown ticket — request the run again"}, 403)
        if t["state"] == "expired":
            _sb_record("redeem", t["name"], t["sid"], src, "expired",
                       "ticket expired before approval · " + t["name"])
            return ({"ok": False, "error": "ticket expired — request the run again"}, 403)
        if t["state"] != "pending":
            _sb_record("redeem", t["name"], t["sid"], src, "blocked",
                       "ticket reuse refused (single-use, already %s) · %s"
                       % (t["state"], t["name"]))
            return ({"ok": False, "error": "ticket already used (single-use)"}, 403)
        # consume BEFORE anything happens — single-use is unconditional
        t["state"] = "approved" if approved else "denied"
    if not approved:
        _sb_audit(src, t["pk"], t["cmd"], {"tier": "ask", "class": t["cls"]},
                  "user-deny", "deny")
        _sb_record("deny", t["name"], t["sid"], src, "denied",
                   "user denied · " + t["name"])
        return {"ok": True, "status": "denied", "name": t["name"],
                "message": "Denied — nothing was run."}
    _sb_audit(src, t["pk"], t["cmd"], {"tier": "ask", "class": t["cls"]},
              "user-approve", "approve")
    res = _sb_execute(t["sid"], t["input"])
    return _sb_finish_run(t, res)


def shortcuts_run_handler(ctx):
    body = ctx.body or {}
    src = str(body.get("source") or "api")[:24]
    if body.get("ticket"):
        return _sb_redeem(body, src)

    input_text = body.get("input")
    if input_text is not None and not isinstance(input_text, str):
        return ({"ok": False, "error": "input must be a string"}, 400)
    if input_text and len(input_text) > SB_IN_CAP:
        return ({"ok": False, "error": "input too large (max %d bytes)" % SB_IN_CAP}, 400)

    entry, err = _sb_resolve(body)
    if entry is None:
        msg, status = err
        _sb_record("resolve", str(body.get("name") or body.get("id") or ""), "",
                   src, "error", "unresolved run request · " + msg)
        return ({"ok": False, "error": msg}, status)

    sid, name = entry["id"], entry["name"]
    pk = SB_CLASS + ":" + sid
    cmd = "shortcuts run \"%s\"" % name

    # recorder is the flight log — if it cannot record, the bus does not run
    if not callable(globals().get("recorder_record_local")):
        return ({"ok": False, "error": "flight recorder unavailable — refusing "
                                       "to run unrecorded"}, 503)

    # gate 1: user allowlist (default: NOTHING is exposed)
    exposed = _sb_load_config().get("exposed") or {}
    if sid not in exposed:
        verdict = {"tier": "never", "class": SB_CLASS, "pattern_key": pk,
                   "reason": "not exposed by the user"}
        _sb_audit(src, pk, cmd, verdict, "auto-denied", "not-exposed")
        _sb_record("gate", name, sid, src, "blocked",
                   "refused · not exposed by the user · " + name)
        return ({"ok": False, "blocked": True,
                 "error": "\"%s\" is not exposed to the agent — the user can "
                          "expose it in the dashboard Shortcuts card" % name}, 403)

    # gate 2: the P1.3 engine
    try:
        v = _sb_perm.decide({"pattern_key": pk, "command": cmd})
    except Exception:
        v = {"tier": "ask", "class": "unknown", "reason": "engine error → ask"}
    tier = v.get("tier") or "ask"
    if tier == "never":
        _sb_audit(src, pk, cmd, v, "auto-denied")
        _sb_record("gate", name, sid, src, "blocked",
                   "blocked by policy · %s · %s" % (name, v.get("reason", "")))
        return ({"ok": False, "blocked": True,
                 "error": "blocked by permission policy: " + str(v.get("reason", ""))}, 403)
    if tier == "auto":
        tier = "ask"          # v1 invariant: shortcut runs are never silent

    # tier ask -> ticket; nothing spawns now
    ttl = SB_TICKET_TTL
    try:                       # shrink-only override (drill/testing aid)
        ttl = max(1, min(int(body.get("ttl") or SB_TICKET_TTL), SB_TICKET_TTL))
    except (TypeError, ValueError):
        pass
    token = secrets.token_hex(16)
    now = time.time()
    with _SB_LOCK:
        _sb_sweep_tickets(now)
        _SB_TICKETS[token] = {"token": token, "sid": sid, "name": name,
                              "pk": pk, "cmd": cmd, "cls": v.get("class", SB_CLASS),
                              "tier": tier, "input": input_text or "",
                              "src": src, "state": "pending",
                              "created": now, "expires": now + ttl}
    _sb_audit(src, pk, cmd, v, "asked")
    _sb_record("ask", name, sid, src, "pending",
               "approval requested · %s · tier %s" % (name, tier),
               extra={"ticket": token[:8], "expires_in": ttl})
    return {"ok": True, "needs_approval": True, "ticket": token,
            "name": name, "tier": tier, "expires_in": ttl,
            "message": "Approval required: the user must confirm this run in "
                       "the dashboard (Mind view → Shortcuts). It cannot "
                       "be approved by the requester."}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
register_get("/api/shortcuts", shortcuts_get_handler)
register_post("/api/shortcuts/config", shortcuts_config_handler)
register_post("/api/shortcuts/run", shortcuts_run_handler)


# --------------------------------------------------------------------------
# agent instruction — wrap access_preamble (runtime override, no file edit).
# Every hub-chat prompt now tells the agent the ONLY sanctioned path is the
# bus endpoint.  This is instruction, not enforcement — the enforcement gap
# for raw `shortcuts run` is documented as residual risk R1 in FINDINGS.
# --------------------------------------------------------------------------
def _sb_preamble_extra():
    items, err = _sb_installed()
    if err and not items:
        return ""
    exposed = _sb_load_config().get("exposed") or {}
    ids = {i["id"]: i["name"] for i in items}
    names = sorted(ids[sid] for sid in exposed if sid in ids)
    listing = ("; ".join("\"%s\"" % n for n in names)
               if names else
               "none yet — the user can expose one in the dashboard Shortcuts card")
    return ("[context] macOS Shortcuts action bus: to run a Shortcut, POST JSON "
            "to http://127.0.0.1:7788/api/shortcuts/run with "
            "{\"name\": \"<shortcut name>\", \"source\": \"agent\"} (curl is fine). "
            "Running the `shortcuts` command directly in the terminal is NOT "
            "permitted — it bypasses the user's permission gates; always use the "
            "bus endpoint. If the bus responds with needs_approval, the user must "
            "approve the run in the dashboard (Mind view → Shortcuts card): "
            "say that approval is waiting and stop — never approve, retry, or "
            "work around it yourself. Shortcuts the user has exposed to the bus: "
            + listing + ".\n\n")


try:
    _sb_prev_access_preamble                     # noqa: B018 — rewrap guard
except NameError:
    try:
        _sb_prev_access_preamble = access_preamble
    except NameError:
        _sb_prev_access_preamble = None

if _sb_prev_access_preamble is not None:
    def access_preamble():
        base = _sb_prev_access_preamble()
        try:
            return base + _sb_preamble_extra()
        except Exception:
            return base
