# aux_config.py — Config-as-Code Snapshot/Restore (P1.6).
#
# Makes the dashboard's mutable runtime state (which lives OUTSIDE the repo in
# ~/.hermes/) versionable INSIDE the repo as ONE deterministic, diffable
# artifact: docs/state-snapshot.json.  Export captures a strict ALLOWLIST;
# import validates + restores it (dry-run plan first, then atomic apply).
#
# Aux-registry integration (ZERO server.py edits): exec'd into server.py's
# globals by the aux-module loader, so it may use these server globals:
#   HOME, HERE, DATA, read_json, write_json, get_layout, save_layout,
#   get_settings, SETTINGS_FILE, LAYOUT_FILE, MODELS_FILE, WIDGETS,
#   _model_registry, active_model, switch_model, _model_downloaded,
#   _state_lock, _widget_cache, HERMES, _hermes_env, register_get/register_post.
# It imports ALL its own stdlib deps (exec'd code can't rely on server.py's
# function-local imports) and defines only new names (snapshot_*, _cfg_*, CFG_*)
# so it clobbers nothing.  The permission POLICY is read/restored through the
# SHARED engine module `permissions.py` (same lock / sidecar as aux_permissions).
#
# SECRETS never enter the repo — twice over: (1) the allowlist simply never
# reads ~/.hermes/.env, serve-token, access.json, chats, memories, or state.db;
# (2) _cfg_scan hard-REFUSES telegram-token / api-key / absolute-path / home-dir
# shaped strings in the OUTPUT (defense against a secret pasted into e.g. a
# quicklink label).  Scan excerpts are truncated to 12 chars so error responses
# can't leak the secret either.  approvals.mode is capture/verify-ONLY: never
# written by apply, and a non-"manual" value refuses the whole import.

import json
import os
import re
import subprocess
import time
from datetime import datetime

try:                       # shared P1.3 policy engine (one lock / one sidecar)
    import permissions as _cfg_pm
except Exception:          # never let a missing dep take the hub down
    _cfg_pm = None

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
SNAPSHOT_SCHEMA = 1
SNAPSHOT_KIND = "hermes-state-snapshot"
CFG_REPO_ROOT = os.path.dirname(HERE)                       # dashboard/ -> repo
CFG_SNAPSHOT_PATH = os.path.join(CFG_REPO_ROOT, "docs", "state-snapshot.json")
CFG_SNAPSHOT_RELPATH = "docs/state-snapshot.json"           # reported (no abs paths in JSON)
CFG_BACKUPS = os.path.join(DATA, "snapshot-backups")        # NOT in the repo
CFG_MAX_BYTES = 256 * 1024
CFG_BODY_MAX = 512 * 1024
CFG_PERM_MAX = 8 * 1024
CFG_YAML = os.path.join(HOME, ".hermes", "config.yaml")

# settings keys captured (weather_city/lat/lon deliberately EXCLUDED — privacy)
CFG_SETTINGS_ALLOW = ("tickers", "starred_tickers", "coins", "rss_feeds",
                      "news_feeds", "quicklinks", "timezones")
CFG_SECTIONS = ("layout", "settings", "models", "permissions", "agent_config")

CFG_TICKER_RE = re.compile(r"^[A-Za-z0-9.^=:-]{1,16}$")
CFG_URL_RE = re.compile(r"^https?://", re.I)
CFG_CTX_MIN, CFG_CTX_MAX = 1024, 262144

# hard-refuse patterns (export AND import) — defense-in-depth over the allowlist
CFG_SECRET_PATTERNS = [
    (r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b", "telegram-bot-token"),
    (r"\bsk-[A-Za-z0-9_-]{20,}\b", "api-key"),
    (r"\bghp_[A-Za-z0-9]{30,}\b", "github-token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "slack-token"),
    (r"\bAIza[0-9A-Za-z_-]{30,}\b", "google-key"),
    (r"/Users/\S+", "absolute-path"),
    (r"(?i)\b(?:secret|password|passwd|api[_-]?key|access[_-]?token|bearer)\b\s*[=:]\s*[^\s\"]{8,}",
     "secret-kv"),
]
# warn-only (export still proceeds, warning surfaced)
CFG_ENTROPY_PATTERN = r"\b[A-Za-z0-9+/=_-]{48,}\b"


# --------------------------------------------------------------------------
# module-load side effects (never let them take the hub down)
# --------------------------------------------------------------------------
try:
    os.makedirs(CFG_BACKUPS, exist_ok=True)
except Exception as _e:                                     # pragma: no cover
    print("[aux_config] mkdir %s failed: %s" % (CFG_BACKUPS, _e))


# --------------------------------------------------------------------------
# config.yaml scanner (stdlib, no yaml dep — same approach as _config_model_default)
# --------------------------------------------------------------------------
def _cfg_read_yaml_keys():
    """Return {'model.context_length': int|None, 'approvals.mode': str|None}."""
    out = {"model.context_length": None, "approvals.mode": None}
    try:
        section = None
        with open(CFG_YAML, encoding="utf-8") as f:
            for line in f:
                if re.match(r"^\S", line):                  # top-level line
                    m = re.match(r"^(\w+):\s*$", line)      # a bare `section:`
                    section = m.group(1) if m else None
                    continue
                if section == "model":
                    m = re.match(r"^\s+context_length:\s*(\d+)", line)
                    if m:
                        out["model.context_length"] = int(m.group(1))
                elif section == "approvals":
                    m = re.match(r"^\s+mode:\s*([^\s#]+)", line)
                    if m:
                        out["approvals.mode"] = m.group(1).strip().strip('"\'')
    except OSError:
        pass
    return out


# --------------------------------------------------------------------------
# per-section cleaners — the allowlist, expressed as pure fixed-point functions.
# snapshot_build() runs them on LIVE state; snapshot_validate() runs the SAME
# ones on an incoming file.  Because they are idempotent, build(state) is always
# a fixed point of validate(): a freshly-exported file re-validates unchanged, so
# re-export is byte-identical and drift compares clean-vs-clean.
# --------------------------------------------------------------------------
def _cfg_clean_layout(lay):
    raw = (lay or {}).get("order") if isinstance(lay, dict) else None
    order, unknown = [], []
    for w in (raw or [])[:200]:
        if not isinstance(w, str):
            continue
        if w in WIDGETS:
            if w not in order:
                order.append(w)
        else:
            unknown.append(w)
    return order[:64], unknown


def _cfg_clean_settings(raw):
    out = {}
    if not isinstance(raw, dict):
        return out
    for k in CFG_SETTINGS_ALLOW:
        if k not in raw:
            continue
        v = raw[k]
        if not isinstance(v, list):
            continue
        if k in ("tickers", "starred_tickers"):
            out[k] = [x.strip() for x in v
                      if isinstance(x, str) and CFG_TICKER_RE.match(x.strip())][:20]
        elif k == "coins":
            out[k] = [x.strip()[:40] for x in v if isinstance(x, str) and x.strip()][:20]
        elif k in ("rss_feeds", "news_feeds"):
            out[k] = [x.strip()[:400] for x in v
                      if isinstance(x, str) and CFG_URL_RE.match(x.strip())][:20]
        elif k == "timezones":
            out[k] = [x.strip()[:64] for x in v if isinstance(x, str) and x.strip()][:20]
        elif k == "quicklinks":
            ql = []
            for it in v[:20]:
                if (isinstance(it, dict) and isinstance(it.get("url"), str)
                        and CFG_URL_RE.match(it["url"].strip())):
                    ql.append({"label": str(it.get("label", ""))[:60],
                               "url": it["url"].strip()[:400]})
            out[k] = ql
    return out


def _cfg_clean_models(raw):
    active = str((raw or {}).get("active") or "")[:120]
    roster = []
    for m in ((raw or {}).get("roster") or [])[:24]:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id", ""))[:120]
        if not mid:
            continue
        ram = m.get("ram")
        roster.append({"id": mid,
                       "label": str(m.get("label", ""))[:64],
                       "ram": ram if (ram is None or isinstance(ram, (int, float))) else None,
                       "note": str(m.get("note", ""))[:120]})
    return {"active": active, "roster": roster}


def _cfg_clean_permissions(raw):
    classes, patterns = {}, {}
    if not isinstance(raw, dict):
        return {"classes": classes, "patterns": patterns}
    valid_tiers = getattr(_cfg_pm, "TIERS", ("auto", "ask", "never")) if _cfg_pm else ("auto", "ask", "never")
    class_meta = getattr(_cfg_pm, "CLASS_META", None) if _cfg_pm else None
    for cid, tier in (raw.get("classes") or {}).items():
        if isinstance(cid, str) and tier in valid_tiers and (class_meta is None or cid in class_meta):
            classes[cid] = tier
    for pat, tier in (raw.get("patterns") or {}).items():
        if isinstance(pat, str) and pat and tier in valid_tiers:
            patterns[pat] = tier
    return {"classes": classes, "patterns": patterns}


def _cfg_clean_agent(raw):
    out = {}
    if not isinstance(raw, dict):
        raw = {}
    cl = raw.get("model.context_length")
    if isinstance(cl, int) and not isinstance(cl, bool):
        out["model.context_length"] = cl
    mode = raw.get("approvals.mode")
    if isinstance(mode, str) and mode:
        out["approvals.mode"] = mode
    return out


# --------------------------------------------------------------------------
# live-state readers (raw, pre-clean)
# --------------------------------------------------------------------------
def _cfg_live_permissions_raw():
    if _cfg_pm is None:
        return {"classes": {}, "patterns": {}}
    try:
        policy, _t, _e = _cfg_pm._load()
    except Exception:
        policy = {}
    return {"classes": (policy.get("classes") if isinstance(policy, dict) else {}) or {},
            "patterns": (policy.get("patterns") if isinstance(policy, dict) else {}) or {}}


def _cfg_live_models_raw():
    return {"active": active_model(), "roster": list(_model_registry() or [])}


def _cfg_live_agent_raw():
    return _cfg_read_yaml_keys()


# --------------------------------------------------------------------------
# build / scan / serialize / dump
# --------------------------------------------------------------------------
def snapshot_build(note=""):
    """Assemble the (timestamp-less) snapshot from LIVE state, allowlist only."""
    order, _ = _cfg_clean_layout(get_layout())
    return {
        "schema": SNAPSHOT_SCHEMA,
        "kind": SNAPSHOT_KIND,
        "note": (str(note) if note else "")[:200],
        "dashboard": {
            "layout": {"order": order},
            "settings": _cfg_clean_settings(get_settings()),
            "models": _cfg_clean_models(_cfg_live_models_raw()),
        },
        "permissions": _cfg_clean_permissions(_cfg_live_permissions_raw()),
        "agent_config": _cfg_clean_agent(_cfg_live_agent_raw()),
    }


def _cfg_scan(obj):
    """json.dumps(obj) then run the secret patterns -> (hard_hits, warnings).
    Excerpts truncated to 12 chars so the scan result itself can't leak."""
    blob = json.dumps(obj, ensure_ascii=False)
    hard, warns = [], []
    for pat, name in CFG_SECRET_PATTERNS:
        m = re.search(pat, blob)
        if m:
            hard.append({"pattern_name": name, "excerpt": m.group(0)[:12]})
    if HOME and HOME in blob:                               # literal home dir leak
        hard.append({"pattern_name": "home-path", "excerpt": "~"})
    m = re.search(CFG_ENTROPY_PATTERN, blob)
    if m:
        warns.append({"pattern_name": "high-entropy", "excerpt": m.group(0)[:12]})
    return hard, warns


def _cfg_serialize(obj):
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _cfg_dump(obj, path):
    """Atomic deterministic write; tmp cleaned up on failure.  Held under lock."""
    data = _cfg_serialize(obj)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with _state_lock:
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return len(data)


def _cfg_without_ts(d):
    if not isinstance(d, dict):
        return d
    c = dict(d)
    c.pop("exported_at", None)
    return c


# --------------------------------------------------------------------------
# validate (schema/kind/size + per-field allowlist + secret scan + gates)
# --------------------------------------------------------------------------
def snapshot_validate(snap):
    """Return (clean, errors, warnings, dropped).  clean has unknown widget ids
    dropped (recorded), all caps applied, and is a fixed point of build()."""
    errors, warnings, dropped = [], [], {}
    if not isinstance(snap, dict):
        return (None, ["not a JSON object"], warnings, dropped)
    if snap.get("schema") != SNAPSHOT_SCHEMA:
        errors.append("unsupported schema %r (need %d)" % (snap.get("schema"), SNAPSHOT_SCHEMA))
    if snap.get("kind") != SNAPSHOT_KIND:
        errors.append("wrong kind %r" % (snap.get("kind"),))
    try:
        if len(_cfg_serialize(snap)) > CFG_MAX_BYTES:
            errors.append("snapshot too large")
    except Exception:
        pass
    if errors:
        return (None, errors, warnings, dropped)

    dash = snap.get("dashboard") if isinstance(snap.get("dashboard"), dict) else {}
    clean = {"schema": SNAPSHOT_SCHEMA, "kind": SNAPSHOT_KIND,
             "note": str(snap.get("note", ""))[:200], "dashboard": {}}

    # layout ---------------------------------------------------------------
    if isinstance(dash.get("layout"), dict):
        raw_order = dash["layout"].get("order")
        order, unknown = _cfg_clean_layout(dash["layout"])
        if unknown:
            dropped["layout"] = unknown
        if isinstance(raw_order, list) and raw_order and not order:
            errors.append("layout empty after validation")
        clean["dashboard"]["layout"] = {"order": order}

    # settings -------------------------------------------------------------
    if isinstance(dash.get("settings"), dict):
        clean["dashboard"]["settings"] = _cfg_clean_settings(dash["settings"])

    # models ---------------------------------------------------------------
    if isinstance(dash.get("models"), dict):
        clean["dashboard"]["models"] = _cfg_clean_models(dash["models"])

    # permissions ----------------------------------------------------------
    if isinstance(snap.get("permissions"), dict):
        cp = _cfg_clean_permissions(snap["permissions"])
        try:
            if len(json.dumps(cp)) > CFG_PERM_MAX:
                errors.append("permissions too large")
        except Exception:
            pass
        clean["permissions"] = cp

    # agent_config: approvals.mode gate + context_length range -------------
    if isinstance(snap.get("agent_config"), dict):
        ac = snap["agent_config"]
        mode = ac.get("approvals.mode")
        if mode is not None and mode != "manual":
            errors.append("approvals.mode must be manual")
        cl = ac.get("model.context_length")
        if cl is not None and (not isinstance(cl, int) or isinstance(cl, bool)
                               or cl < CFG_CTX_MIN or cl > CFG_CTX_MAX):
            errors.append("model.context_length out of range")
        clean["agent_config"] = _cfg_clean_agent(ac)

    # secret scan on the CLEAN payload (defense-in-depth) -------------------
    hard, warns = _cfg_scan(clean)
    if hard:
        errors.append("secret-like value detected (%s)" % hard[0]["pattern_name"])
    warnings.extend(w["pattern_name"] for w in warns)

    if errors:
        return (None, errors, warnings, dropped)
    return (clean, errors, warnings, dropped)


# --------------------------------------------------------------------------
# diff (dry-run plan)
# --------------------------------------------------------------------------
def snapshot_diff(clean, apply_active=False, dropped=None):
    dropped = dropped or {}
    live = snapshot_build()
    ld, cd = live["dashboard"], clean.get("dashboard", {})
    plan = {}

    if "layout" in cd:
        lo, so = ld["layout"]["order"], cd["layout"]["order"]
        adds = [w for w in so if w not in lo]
        removes = [w for w in lo if w not in so]
        reordered = (set(lo) == set(so)) and (lo != so)
        plan["layout"] = {
            "changed": bool(adds or removes or reordered or dropped.get("layout")),
            "adds": adds, "removes": removes, "reordered": reordered,
            "dropped_unknown": dropped.get("layout", [])}

    if "settings" in cd:
        ls, ss = ld["settings"], cd["settings"]
        changed = {k: {"from": ls.get(k), "to": v} for k, v in ss.items() if ls.get(k) != v}
        plan["settings"] = {"changed_keys": changed}

    if "models" in cd:
        lr = [m["id"] for m in ld["models"]["roster"]]
        sr = [m["id"] for m in cd["models"]["roster"]]
        tgt = cd["models"].get("active")
        plan["models"] = {
            "roster_changed": ld["models"]["roster"] != cd["models"]["roster"],
            "added": [m for m in sr if m not in lr],
            "removed": [m for m in lr if m not in sr],
            "active": {"from": ld["models"]["active"], "to": tgt,
                       "downloaded": _model_downloaded(tgt) if tgt else False,
                       "will_apply": bool(apply_active and tgt and tgt != ld["models"]["active"])}}

    if "permissions" in clean:
        lp, sp = live["permissions"], clean["permissions"]
        plan["permissions"] = {
            "changed": lp != sp,
            "classes": ({"from": lp.get("classes", {}), "to": sp.get("classes", {})}
                        if lp.get("classes") != sp.get("classes") else {}),
            "patterns": ({"from": lp.get("patterns", {}), "to": sp.get("patterns", {})}
                         if lp.get("patterns") != sp.get("patterns") else {})}

    if "agent_config" in clean:
        la, sa = live["agent_config"], clean["agent_config"]
        changes = {}
        if "model.context_length" in sa and la.get("model.context_length") != sa.get("model.context_length"):
            changes["model.context_length"] = {"from": la.get("model.context_length"),
                                                "to": sa.get("model.context_length")}
        plan["agent_config"] = {"changes": changes,
                                "verify_only": {"approvals.mode": sa.get("approvals.mode")}}
    return plan


# --------------------------------------------------------------------------
# backup (local, NOT in the repo) — newest 5 kept
# --------------------------------------------------------------------------
def _cfg_gc_backups():
    try:
        files = [os.path.join(CFG_BACKUPS, f) for f in os.listdir(CFG_BACKUPS)
                 if f.startswith("pre-restore-") and f.endswith(".json")]
    except OSError:
        return
    files.sort(key=lambda p: (os.path.getmtime(p) if os.path.exists(p) else 0))
    while len(files) > 5:
        p = files.pop(0)
        try:
            os.remove(p)
        except OSError:
            pass


def _cfg_backup():
    """Export the CURRENT live state to snapshot-backups/ before any apply."""
    try:
        os.makedirs(CFG_BACKUPS, exist_ok=True)
        snap = snapshot_build()
        snap["exported_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        ts = time.strftime("%Y%m%d-%H%M%S")
        name = "pre-restore-%s.json" % ts
        path = os.path.join(CFG_BACKUPS, name)
        n = 1
        while os.path.exists(path):
            n += 1
            name = "pre-restore-%s-%d.json" % (ts, n)
            path = os.path.join(CFG_BACKUPS, name)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(_cfg_serialize(snap))
        os.replace(tmp, path)
        _cfg_gc_backups()
        return "snapshot-backups/" + name
    except Exception:
        return None


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------
def snapshot_export(note=""):
    snap = snapshot_build(note)
    hard, warns = _cfg_scan(snap)
    if hard:
        h = hard[0]
        return ({"ok": False,
                 "error": "secret-like value detected (%s)" % h["pattern_name"],
                 "where": h["pattern_name"]}, 400)
    # deterministic exported_at: reuse the previous one when the substantive
    # content is unchanged, so a no-op re-export is byte-identical (clean git diff)
    prev = read_json(CFG_SNAPSHOT_PATH, None)
    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    if isinstance(prev, dict) and _cfg_without_ts(prev) == snap:
        snap["exported_at"] = prev.get("exported_at") or now_iso
    else:
        snap["exported_at"] = now_iso
    try:
        data_len = len(_cfg_serialize(snap))
    except Exception as e:
        return ({"ok": False, "error": "serialize failed: %s" % e}, 400)
    if data_len > CFG_MAX_BYTES:
        return ({"ok": False, "error": "snapshot too large (%d bytes)" % data_len}, 400)
    try:
        n = _cfg_dump(snap, CFG_SNAPSHOT_PATH)
    except OSError as e:
        return ({"ok": False, "error": "write failed: %s" % e}, 400)
    return {"ok": True, "path": CFG_SNAPSHOT_RELPATH, "bytes": n,
            "sections": list(CFG_SECTIONS),
            "warnings": [w["pattern_name"] for w in warns]}


# --------------------------------------------------------------------------
# apply (dry_run=false)
# --------------------------------------------------------------------------
def snapshot_apply(clean, sections, apply_active, dropped):
    applied, warnings = {}, []
    backup = _cfg_backup()
    dash = clean.get("dashboard", {})

    if "layout" in sections and "layout" in dash:
        save_layout({"order": list(dash["layout"]["order"])})
        applied["layout"] = True

    if "settings" in sections and "settings" in dash:
        with _state_lock:
            s = get_settings() or {}
            touched = []
            for k, v in dash["settings"].items():
                s[k] = v
                touched.append(k)
            write_json(SETTINGS_FILE, s)
        _widget_cache.clear()
        applied["settings"] = touched

    if "models" in sections and "models" in dash:
        with _state_lock:
            write_json(MODELS_FILE, {"models": dash["models"]["roster"]})
        applied["models"] = True
        if apply_active:
            tgt = dash["models"].get("active")
            if tgt and tgt != active_model():
                if _model_downloaded(tgt):
                    r = switch_model(tgt) or {}
                    applied["active_model"] = "applied" if r.get("ok") else \
                        ("failed: " + str(r.get("error")))
                else:
                    applied["active_model"] = "not_downloaded"
            else:
                applied["active_model"] = "skipped"
        else:
            applied["active_model"] = "skipped"

    if "permissions" in sections and "permissions" in clean and _cfg_pm is not None:
        try:
            _cfg_pm._save({"classes": clean["permissions"].get("classes", {}),
                           "patterns": clean["permissions"].get("patterns", {})})
            applied["permissions"] = True
        except Exception as e:
            applied["permissions"] = False
            warnings.append("permissions restore failed: %s" % e)

    if "agent_config" in sections and "agent_config" in clean:
        ac = clean["agent_config"]
        done = []
        cl = ac.get("model.context_length")
        live_cl = _cfg_read_yaml_keys().get("model.context_length")
        if isinstance(cl, int) and cl != live_cl and os.path.exists(HERMES):
            try:
                r = subprocess.run([HERMES, "config", "set", "model.context_length", str(cl)],
                                   capture_output=True, text=True, timeout=30, env=_hermes_env())
                if r.returncode == 0:
                    done.append("model.context_length")
                else:
                    warnings.append("hermes config set failed: %s"
                                    % ((r.stderr or "").strip()[:120]))
            except Exception as e:
                warnings.append("hermes config set failed: %s" % e)
        live_mode = _cfg_read_yaml_keys().get("approvals.mode")
        if live_mode and live_mode != "manual":
            warnings.append("live approvals.mode is not manual")
        applied["agent_config"] = done

    if dropped.get("layout"):
        warnings.append("dropped unknown widgets: " + ", ".join(dropped["layout"]))

    return {"ok": True, "dry_run": False, "applied": applied,
            "backup": backup, "warnings": warnings}


# --------------------------------------------------------------------------
# file read + status/drift
# --------------------------------------------------------------------------
def _cfg_read_snapshot_file():
    """Return (data, err) where err in {None,'missing','too large','invalid'}."""
    try:
        sz = os.path.getsize(CFG_SNAPSHOT_PATH)
    except OSError:
        return (None, "missing")
    if sz > CFG_MAX_BYTES:
        return (None, "too large")
    data = read_json(CFG_SNAPSHOT_PATH, None)
    if not isinstance(data, dict):
        return (None, "invalid")
    return (data, None)


def _cfg_drift(live, clean):
    ld, cd = live.get("dashboard", {}), clean.get("dashboard", {})
    return {
        "layout": "in_sync" if ld.get("layout") == cd.get("layout") else "drifted",
        "settings": "in_sync" if ld.get("settings") == cd.get("settings") else "drifted",
        "models": "in_sync" if ld.get("models") == cd.get("models") else "drifted",
        "permissions": "in_sync" if live.get("permissions") == clean.get("permissions") else "drifted",
        "agent_config": "in_sync" if live.get("agent_config") == clean.get("agent_config") else "drifted",
    }


def _cfg_status():
    warnings = []
    live = snapshot_build()
    exists = os.path.exists(CFG_SNAPSHOT_PATH)
    file_info = {"exists": exists, "path": CFG_SNAPSHOT_RELPATH}
    snap, ferr = _cfg_read_snapshot_file()
    if exists:
        try:
            file_info["bytes"] = os.path.getsize(CFG_SNAPSHOT_PATH)
        except OSError:
            file_info["bytes"] = 0
    if not exists or snap is None:
        file_info["valid"] = False
        file_info["exported_at"] = None
        return file_info, {s: "missing" for s in CFG_SECTIONS}, warnings
    file_info["exported_at"] = snap.get("exported_at")
    clean, errors, warns, dropped = snapshot_validate(snap)
    if errors:
        file_info["valid"] = False
        return file_info, {s: "invalid" for s in CFG_SECTIONS}, warnings
    file_info["valid"] = True
    return file_info, _cfg_drift(live, clean), warnings


# --------------------------------------------------------------------------
# HTTP handlers
# --------------------------------------------------------------------------
def snapshot_get(qs):
    try:
        preview = snapshot_build()
        preview["exported_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        file_info, drift, warnings = _cfg_status()
        return {"ok": True, "preview": preview, "file": file_info,
                "drift": drift, "warnings": warnings}
    except Exception as e:                                   # never 500 the panel
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def snapshot_post(path, body):
    body = body if isinstance(body, dict) else {}
    if path.endswith("/export"):
        return snapshot_export(body.get("note", ""))

    # ---- import ----------------------------------------------------------
    try:
        if len(json.dumps(body)) > CFG_BODY_MAX:
            return ({"ok": False, "error": "request too large"}, 400)
    except Exception:
        pass
    dry = body.get("dry_run", True)
    if not isinstance(dry, bool):
        dry = True
    sections = body.get("sections") or list(CFG_SECTIONS)
    if not isinstance(sections, list):
        sections = list(CFG_SECTIONS)
    bad = [s for s in sections if s not in CFG_SECTIONS]
    if bad:
        return ({"ok": False, "error": "unknown section %s" % bad[0]}, 400)
    apply_active = bool(body.get("apply_active_model", False))

    inline = body.get("snapshot")
    if inline is not None:
        snap = inline
    else:
        snap, ferr = _cfg_read_snapshot_file()
        if ferr == "missing":
            return ({"ok": False, "error": "no snapshot file"}, 400)
        if ferr == "too large":
            return ({"ok": False, "error": "snapshot too large"}, 400)
        if ferr == "invalid":
            return ({"ok": False, "error": "snapshot invalid: not valid JSON"}, 400)

    clean, errors, warnings, dropped = snapshot_validate(snap)
    if errors:
        return ({"ok": False, "error": "snapshot invalid: " + "; ".join(errors)}, 400)

    if dry:
        return {"ok": True, "dry_run": True,
                "plan": snapshot_diff(clean, apply_active, dropped),
                "warnings": warnings}

    res = snapshot_apply(clean, sections, apply_active, dropped)
    if warnings:
        res["warnings"] = warnings + res.get("warnings", [])
    return res


# --------------------------------------------------------------------------
# route registration
# --------------------------------------------------------------------------
register_get("/api/config/snapshot", lambda ctx: snapshot_get(ctx.query))
register_post("/api/config/export", lambda ctx: snapshot_post(ctx.raw_path, ctx.body))
register_post("/api/config/import", lambda ctx: snapshot_post(ctx.raw_path, ctx.body))
