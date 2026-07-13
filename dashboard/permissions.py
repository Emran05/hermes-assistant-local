# permissions.py — Graduated Permission Tiers engine (P1.3).
#
# A REAL sibling module (import permissions), NOT exec-included: it is imported
# by BOTH dashboard/hermes_rpc.py (the enforcement seam) and
# dashboard/aux_permissions.py (the route registrar), so a single sys.modules
# entry means one lock, one mtime cache, one policy store shared across the
# HTTP threads and the chat-turn thread.
#
# It sits IN FRONT OF hermes-agent's own `approvals.mode: manual`: hermes keeps
# asking; this layer answers "once" (auto-approve) or "deny" (auto-deny) on the
# user's behalf when — and only when — the user has pre-decided a class into
# AUTO/NEVER via the Trust panel.  It NEVER sends session/always, so hermes's
# own allowlists never grow and ~/.hermes/permissions.json stays the single,
# visible source of graduated trust.
#
# SAFETY POSTURE (deliberately stricter than the spec's example JSON):
#   * SHIPPED_DEFAULT = "ask".  Merely installing this feature changes NOTHING —
#     every class that the spec would default to AUTO instead falls back to ASK.
#     AUTO is honored ONLY when a class is EXPLICITLY written "auto" in
#     permissions.json by the user (via the panel).  The three critical classes
#     keep their default of "never".
#   * Every failure mode (missing file, parse error, unknown key, tamper
#     detection, respond error) degrades toward ASK — never toward AUTO.
#   * Floors are enforced at BOTH write time (the API rejects) and read time
#     (decide() clamps), so a hand-crafted "credential-write":"auto" line still
#     resolves as ASK.
#   * Sidecar-hash mismatch (an out-of-band edit) marks the policy UNTRUSTED and
#     suspends all AUTO until a human clicks Review & re-trust.
#
# Self-contained: stdlib only, and only these five modules.

import hashlib
import json
import os
import threading
import time

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
HOME = os.path.expanduser("~")
HERMES_DIR = os.path.join(HOME, ".hermes")
DASH_DIR = os.path.join(HERMES_DIR, "dashboard")
PERM_FILE = os.path.join(HERMES_DIR, "permissions.json")          # mode 600
SIDECAR = os.path.join(DASH_DIR, "permissions.sha")               # mode 600
LOG_FILE = os.path.join(DASH_DIR, "permissions-log.jsonl")        # append-only

LOG_MAX = 1024 * 1024        # 1 MB -> rotate to .1 (single generation)
CMD_TRUNC = 2000             # execute_code scripts can be huge
RECENT_WINDOW = 7 * 86400    # per-class "this week" aggregation window

TIERS = ("auto", "ask", "never")
_RANK = {"auto": 0, "ask": 1, "never": 2}     # most-restrictive wins
SHIPPED_DEFAULT = "ask"                        # the fail-safe fallback tier

# --------------------------------------------------------------------------
# class taxonomy — 18 canonical action classes
# (17 hermes-pattern classes + shortcuts-run, the P3.1 dashboard action bus)
# CLASS_META[id] = {label, risk, default, floor, desc}
#   * default : the DOCUMENTED per-class default from the spec table (display
#               reference only — the ACTUAL shipped fallback is _shipped_tier()).
#   * floor   : the least-restrictive tier a class may EVER be set to.  "ask"
#               means the class can never run silently (auto is unrepresentable
#               and clamped); "" means auto is permitted.
# --------------------------------------------------------------------------
CLASS_META = {
    "read-only": {
        "label": "Read-only actions", "risk": "low",
        "default": "auto", "floor": "",
        "desc": "Reserved for future read-only bus tools — no hermes patterns today."},
    "git-destructive": {
        "label": "Git history & working tree", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "git reset --hard, force push, branch -D, clean -f — loses work or rewrites history."},
    "file-perms": {
        "label": "File permissions & ownership", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "World-writable chmod and recursive chown to root."},
    "project-config": {
        "label": "Project env/config writes", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "Overwrites of project-local .env / config files."},
    "process-control": {
        "label": "Process & service control", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "Stop/restart services, kill or force-kill processes."},
    "container-lifecycle": {
        "label": "Container lifecycle", "risk": "med",
        "default": "auto", "floor": "",
        "desc": "docker / docker compose restart, stop, kill, down."},
    "mcp-consent": {
        "label": "MCP elicitation consent", "risk": "med",
        "default": "ask", "floor": "ask",
        "desc": "An MCP elicitation is a question — auto-answering it silently consents."},
    "shortcuts-run": {
        "label": "macOS Shortcuts runs", "risk": "med",
        "default": "ask", "floor": "ask",
        "desc": "Runs a user-exposed macOS Shortcut through the dashboard action bus — allowlisted per shortcut, never silent."},
    "destructive-delete": {
        "label": "Destructive deletes", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "rm -r, find -delete, xargs rm, Windows del — irreversible file loss."},
    "sql-destructive": {
        "label": "Destructive SQL", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "DROP, TRUNCATE, DELETE without WHERE."},
    "arbitrary-exec": {
        "label": "Arbitrary code execution", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "Shell -c, pipe-to-shell, decode-and-run, heredoc scripts — runs unvetted code."},
    "execute-code": {
        "label": "execute_code scripts", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "Whole-script code_execution tool runs."},
    "system-config": {
        "label": "System config writes", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "Overwrites or in-place edits of /etc system configuration."},
    "hermes-self": {
        "label": "Hermes self-modification", "risk": "critical",
        "default": "ask", "floor": "ask",
        "desc": "Kills/restarts the gateway or edits ~/.hermes config — the security policy itself."},
    "credential-write": {
        "label": "Credential & SSH writes", "risk": "critical",
        "default": "never", "floor": "ask",
        "desc": "Writes to SSH keys, credential files, shell rc files."},
    "disk-device": {
        "label": "Disk & block devices", "risk": "critical",
        "default": "never", "floor": "ask",
        "desc": "mkfs, dd, raw writes to block devices."},
    "privilege-escalation": {
        "label": "Privilege escalation", "risk": "critical",
        "default": "never", "floor": "ask",
        "desc": "sudo with stdin/askpass/shell/list privilege flags."},
    "unknown": {
        "label": "Unrecognized actions", "risk": "high",
        "default": "ask", "floor": "ask",
        "desc": "Any pattern not in the table — new upstream patterns land here and always ask."},
}

# Display order for the Trust panel + GET payload: critical -> high -> med -> low.
CLASS_ORDER = [
    "credential-write", "disk-device", "privilege-escalation", "hermes-self",
    "destructive-delete", "sql-destructive", "arbitrary-exec", "execute-code",
    "system-config", "unknown",
    "git-destructive", "file-perms", "project-config", "process-control",
    "container-lifecycle", "mcp-consent", "shortcuts-run",
    "read-only",
]

# --------------------------------------------------------------------------
# PATTERN_CLASS — every hermes pattern_key -> canonical class.
# The 72 unique DANGEROUS_PATTERNS description keys (approval.py:498-712;
# "start gateway outside systemd" appears twice) + the 2 event literals.
# "fork bomb" is the one DANGEROUS key absent from the spec taxonomy — it is a
# HARDLINE pattern that is blocked upstream anyway, so it maps to `unknown`
# (floor ask, never auto) per the fail-safe rule for unplaceable keys.
# --------------------------------------------------------------------------
PATTERN_CLASS = {
    # --- destructive-delete (8) ---
    "delete in root path": "destructive-delete",
    "recursive delete": "destructive-delete",
    "recursive delete (long flag)": "destructive-delete",
    "Windows cmd destructive delete": "destructive-delete",
    "Windows PowerShell destructive delete": "destructive-delete",
    "xargs with rm": "destructive-delete",
    "find -exec/-execdir rm": "destructive-delete",
    "find -delete": "destructive-delete",
    # --- git-destructive (7) ---
    "git reset --hard (destroys uncommitted changes)": "git-destructive",
    "git force push (rewrites remote history)": "git-destructive",
    "git force push short flag (rewrites remote history)": "git-destructive",
    "git clean with force (deletes untracked files)": "git-destructive",
    "git branch force delete": "git-destructive",
    "git branch force delete (long flags)": "git-destructive",
    "git branch force delete (long flags, force-first)": "git-destructive",
    # --- file-perms (4) ---
    "world/other-writable permissions": "file-perms",
    "recursive world/other-writable (long flag)": "file-perms",
    "recursive chown to root": "file-perms",
    "recursive chown to root (long flag)": "file-perms",
    # --- project-config (3) ---
    "overwrite project env/config via tee": "project-config",
    "overwrite project env/config via redirection": "project-config",
    "overwrite project env/config file": "project-config",
    # --- process-control (6) ---
    "stop/restart system service": "process-control",
    "kill all processes": "process-control",
    "force kill processes": "process-control",
    "force kill processes (killall -KILL)": "process-control",
    "force kill processes (killall -s KILL)": "process-control",
    "kill processes by regex (killall -r)": "process-control",
    # --- container-lifecycle (2) ---
    "docker compose restart/stop/kill/down (container lifecycle)": "container-lifecycle",
    "docker restart/stop/kill (container lifecycle)": "container-lifecycle",
    # --- sql-destructive (3) ---
    "SQL DROP": "sql-destructive",
    "SQL DELETE without WHERE": "sql-destructive",
    "SQL TRUNCATE": "sql-destructive",
    # --- arbitrary-exec (13) ---
    "shell command via -c/-lc flag": "arbitrary-exec",
    "script execution via -e/-c flag": "arbitrary-exec",
    "pipe remote content to shell": "arbitrary-exec",
    "execute remote script via process substitution": "arbitrary-exec",
    "execute remote content via command substitution": "arbitrary-exec",
    "pipe decoded content to shell (possible command obfuscation)": "arbitrary-exec",
    "pipe xxd-decoded content to shell (possible command obfuscation)": "arbitrary-exec",
    "pipe tr-transformed output to shell (possible command obfuscation)": "arbitrary-exec",
    "pipe openssl-decoded content to shell (possible command obfuscation)": "arbitrary-exec",
    "script execution via heredoc": "arbitrary-exec",
    "shell execution via heredoc": "arbitrary-exec",
    "chmod +x followed by immediate execution": "arbitrary-exec",
    "PowerShell encoded command execution": "arbitrary-exec",
    # --- system-config (6) ---
    "overwrite system config": "system-config",
    "overwrite system file via tee": "system-config",
    "overwrite system file via redirection": "system-config",
    "copy/move file into system config path": "system-config",
    "in-place edit of system config": "system-config",
    "in-place edit of system config (long flag)": "system-config",
    # --- hermes-self (10) ---
    "stop/restart hermes gateway (kills running agents)": "hermes-self",
    "hermes update (restarts gateway, kills running agents)": "hermes-self",
    "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')": "hermes-self",
    "kill hermes/gateway process (self-termination)": "hermes-self",
    "kill process via pgrep/pidof expansion (self-termination)": "hermes-self",
    "kill process via backtick pgrep/pidof expansion (self-termination)": "hermes-self",
    "stop/restart hermes launchd service (kills running agents)": "hermes-self",
    "in-place edit of Hermes config/env": "hermes-self",
    "in-place edit of Hermes config/env (long flag)": "hermes-self",
    "in-place edit of Hermes config/env (perl/ruby)": "hermes-self",
    # --- credential-write (4) ---
    "copy/move file into sensitive credential/SSH/shell-rc path": "credential-write",
    "in-place edit of sensitive credential/SSH/shell-rc path": "credential-write",
    "in-place edit of sensitive credential/SSH/shell-rc path (long flag)": "credential-write",
    "in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)": "credential-write",
    # --- disk-device (3) ---
    "format filesystem": "disk-device",
    "disk copy": "disk-device",
    "write to block device": "disk-device",
    # --- privilege-escalation (2) ---
    "sudo with privilege flag (stdin/askpass/shell/list)": "privilege-escalation",
    "sudo with combined-flag privilege escalation": "privilege-escalation",
    # --- unplaceable DANGEROUS key -> unknown (fail-safe, never auto) ---
    "fork bomb": "unknown",
    # --- event literals (non-terminal sources) ---
    "execute_code": "execute-code",
    "mcp_elicitation": "mcp-consent",
}

# Cheap import-time consistency guard: every mapped class must exist.
for _pk, _cid in PATTERN_CLASS.items():
    assert _cid in CLASS_META, "PATTERN_CLASS maps %r to unknown class %r" % (_pk, _cid)

# Precompute per-class pattern counts for the panel.
PATTERN_COUNT = {cid: 0 for cid in CLASS_META}
for _cid in PATTERN_CLASS.values():
    PATTERN_COUNT[_cid] = PATTERN_COUNT.get(_cid, 0) + 1

# --------------------------------------------------------------------------
# module state
# --------------------------------------------------------------------------
_lock = threading.RLock()          # re-entrant: _load/_save may nest under it
_STATE = {"mtime": None, "policy": {}, "trusted": True, "error": None, "good": {}}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _shipped_tier(cid):
    """The fail-safe fallback tier used when a class is not explicitly set.

    Everything falls to ASK except the three classes the spec defaults to
    NEVER (credential-write / disk-device / privilege-escalation)."""
    meta = CLASS_META.get(cid)
    if meta and meta.get("default") == "never":
        return "never"
    return SHIPPED_DEFAULT


def _floor(cid):
    meta = CLASS_META.get(cid)
    return meta.get("floor", "ask") if meta else "ask"


def _auto_allowed(cid):
    return _floor(cid) != "ask"


def _heuristic(key):
    """Class for a key absent from PATTERN_CLASS (future upstream patterns).
    Biased toward the most restrictive plausible class; else `unknown`."""
    k = key.lower()
    if "credential" in k or "ssh" in k or "shell-rc" in k:
        return "credential-write"
    if "hermes" in k or "gateway" in k:
        return "hermes-self"
    if "sudo" in k:
        return "privilege-escalation"
    if "delete" in k or "rm " in k:
        return "destructive-delete"
    if "block device" in k or "filesystem" in k:
        return "disk-device"
    return "unknown"


def _class_of(key):
    if key.startswith("shortcuts-run:"):   # dashboard action bus (aux_shortcuts)
        return "shortcuts-run"
    return PATTERN_CLASS.get(key) or _heuristic(key)


def _ensure_dirs():
    for d in (HERMES_DIR, DASH_DIR):
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
        except OSError:
            pass


def _atomic_write(path, raw, mode=0o600):
    """temp + fsync + os.replace, stdlib-only (no tempfile dependency)."""
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


# --------------------------------------------------------------------------
# policy load / save / integrity
# --------------------------------------------------------------------------
def _read_sidecar():
    try:
        with open(SIDECAR) as f:
            return f.read().strip()
    except OSError:
        return ""


def _normalize(policy):
    """Return a clean, serializable policy (valid class ids + valid tiers only)."""
    classes = {}
    src = policy.get("classes") if isinstance(policy, dict) else None
    if isinstance(src, dict):
        for cid, tier in src.items():
            if cid in CLASS_META and tier in TIERS:
                classes[cid] = tier
    patterns = {}
    psrc = policy.get("patterns") if isinstance(policy, dict) else None
    if isinstance(psrc, dict):
        for k, tier in psrc.items():
            if isinstance(k, str) and k and tier in TIERS:
                patterns[k] = tier
    return {"version": 1, "updated": round(time.time(), 3),
            "updated_by": "dashboard", "classes": classes, "patterns": patterns}


def _load():
    """Return (policy, trusted, error).  Never raises.  Re-reads the file only
    when its mtime changes; caches the parse behind the module lock."""
    with _lock:
        try:
            st = os.stat(PERM_FILE)
        except OSError:
            # File absent: safe defaults, and there is nothing to distrust.
            _STATE.update(mtime=None, policy={}, trusted=True, error=None)
            return ({}, True, None)
        mtime = st.st_mtime
        if mtime == _STATE["mtime"]:
            return (_STATE["policy"], _STATE["trusted"], _STATE["error"])
        try:
            with open(PERM_FILE, "rb") as f:
                raw = f.read()
        except OSError as e:
            policy = _STATE.get("good") or {}
            _STATE.update(mtime=mtime, policy=policy, trusted=False,
                          error="unreadable: " + str(e))
            return (policy, False, _STATE["error"])
        sha = hashlib.sha256(raw).hexdigest()
        trusted = bool(_read_sidecar()) and (sha == _read_sidecar())
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("policy is not a JSON object")
        except Exception as e:
            # Keep the last-known-good policy so user-set NEVER entries survive
            # a malformed write; mark untrusted so every AUTO clamps to ASK.
            policy = _STATE.get("good") or {}
            _STATE.update(mtime=mtime, policy=policy, trusted=False,
                          error="malformed: " + str(e))
            return (policy, False, _STATE["error"])
        _STATE.update(mtime=mtime, policy=parsed, trusted=trusted,
                      error=None, good=parsed)
        return (parsed, trusted, None)


def _save(policy):
    """Atomic write of the normalized policy + sidecar rewrite + cache bust."""
    with _lock:
        _ensure_dirs()
        norm = _normalize(policy)
        raw = (json.dumps(norm, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_write(PERM_FILE, raw, 0o600)
        _atomic_write(SIDECAR, (hashlib.sha256(raw).hexdigest() + "\n").encode(), 0o600)
        _STATE["mtime"] = None        # force a fresh read on next _load()
        return norm


# --------------------------------------------------------------------------
# resolution engine — decide(payload)
# --------------------------------------------------------------------------
def _pattern_tier(policy, key):
    pats = policy.get("patterns") if isinstance(policy, dict) else None
    if isinstance(pats, dict):
        v = pats.get(key)
        if v in TIERS:
            return v
    return None


def _class_tier(policy, cid):
    classes = policy.get("classes") if isinstance(policy, dict) else None
    if isinstance(classes, dict):
        v = classes.get(cid)
        if v in TIERS:
            return v
    return _shipped_tier(cid)


def decide(payload):
    """Deterministic, total, never-raising resolution of one approval.request.

    Returns {tier, class, classes, pattern_key, reason, clamped}.
    tier is one of auto|ask|never; the enforcement seam sends "once" for auto,
    "deny" for never, and surfaces the card for ask."""
    try:
        if not isinstance(payload, dict):
            payload = {}
        keys = payload.get("pattern_keys")
        if not isinstance(keys, list):
            keys = None
        if keys is None:
            pk = payload.get("pattern_key")
            keys = [pk] if pk else []
        keys = [k for k in keys if isinstance(k, str) and k]

        policy, trusted, _err = _load()

        if not keys:
            return {"tier": "ask", "class": "unknown", "classes": ["unknown"],
                    "pattern_key": "", "clamped": False,
                    "reason": "no pattern key → unknown is set to Ask"}

        resolved = []   # (rank, tier, cls, key, floor_clamped)
        for key in keys:
            cls = _class_of(key)
            raw = _pattern_tier(policy, key)
            if raw is None:
                raw = _class_tier(policy, cls)
            fc = False
            if raw == "auto" and _floor(cls) == "ask":
                raw = "ask"
                fc = True
            resolved.append((_RANK[raw], raw, cls, key, fc))

        # most-restrictive wins; ties resolve to first occurrence
        best = max(resolved, key=lambda r: r[0])
        _rank, tier, cls, key, floor_clamped = best

        trust_clamped = False
        if not trusted and tier == "auto":
            tier = "ask"
            trust_clamped = True

        classes = []
        for r in resolved:
            if r[2] not in classes:
                classes.append(r[2])

        reason = "%s → %s is set to %s" % (key, cls, tier.capitalize())
        if trust_clamped:
            reason += " — auto suspended (policy changed outside the dashboard)"
        elif floor_clamped:
            reason += " (safety floor)"

        return {"tier": tier, "class": cls, "classes": classes,
                "pattern_key": key, "clamped": bool(floor_clamped or trust_clamped),
                "reason": reason}
    except Exception as e:
        # Absolute fail-safe: anything unexpected still asks, never auto.
        return {"tier": "ask", "class": "unknown", "classes": ["unknown"],
                "pattern_key": "", "clamped": True,
                "reason": "policy error → asking (%s)" % (type(e).__name__,)}


# --------------------------------------------------------------------------
# audit log — append-only JSONL, 1 MB single-generation rotation
# --------------------------------------------------------------------------
def _log_append(entry):
    try:
        _ensure_dirs()
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        try:
            if os.path.getsize(LOG_FILE) > LOG_MAX:
                os.replace(LOG_FILE, LOG_FILE + ".1")
        except OSError:
            pass
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass   # audit must never break a turn


def audit(job, payload, verdict, action, choice=""):
    """Append one audit entry.  Best-effort; swallows all errors.

    action in auto-approved | auto-denied | asked | user-approve | user-deny |
    policy-error.  `job` may be the chat-job dict (has id/session) or a string."""
    try:
        if isinstance(job, dict):
            jid = job.get("id") or job.get("job") or ""
            sess = job.get("session") or job.get("serve_sid") or job.get("sid") or ""
        elif isinstance(job, str):
            jid, sess = job, ""
        else:
            jid, sess = "", ""
        verdict = verdict if isinstance(verdict, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        pk = payload.get("pattern_key") or ""
        if not pk:
            ks = payload.get("pattern_keys")
            if isinstance(ks, list) and ks and isinstance(ks[0], str):
                pk = ks[0]
        command = str(payload.get("command") or "")[:CMD_TRUNC]
        entry = {"ts": round(time.time(), 3), "job": jid, "session": sess,
                 "pattern_key": pk, "class": verdict.get("class", ""),
                 "tier": verdict.get("tier", ""), "action": action,
                 "command": command}
        if choice:
            entry["choice"] = choice
        _log_append(entry)
    except Exception:
        pass


def _log_policy_change(op, detail):
    entry = {"ts": round(time.time(), 3), "job": "", "session": "",
             "pattern_key": detail.get("pattern", ""),
             "class": detail.get("class", ""), "tier": detail.get("tier", ""),
             "action": "policy-change", "op": op}
    _log_append(entry)


def _tail_entries(limit):
    """Parse up to `limit` most recent audit lines; return oldest-first list."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except ValueError:
            continue
    return out


def _recent_stats(entries):
    """Per-class {auto-approved, auto-denied, asked} counts within the window."""
    cutoff = time.time() - RECENT_WINDOW
    stats = {}
    for e in entries:
        try:
            if float(e.get("ts", 0)) < cutoff:
                continue
        except (TypeError, ValueError):
            continue
        cls = e.get("class")
        act = e.get("action")
        if cls not in CLASS_META or act not in ("auto-approved", "auto-denied", "asked"):
            continue
        d = stats.setdefault(cls, {"auto-approved": 0, "auto-denied": 0, "asked": 0})
        d[act] += 1
    return stats


# --------------------------------------------------------------------------
# HTTP-facing surface (called by aux_permissions.py handlers)
# --------------------------------------------------------------------------
def permissions_payload():
    """GET /api/permissions — 18 classes ordered critical->low + recent audit."""
    policy, trusted, error = _load()
    entries = _tail_entries(500)
    stats = _recent_stats(entries)
    classes = []
    for cid in CLASS_ORDER:
        meta = CLASS_META[cid]
        classes.append({
            "id": cid, "label": meta["label"], "risk": meta["risk"],
            "tier": _class_tier(policy, cid),
            "default": _shipped_tier(cid), "floor": meta["floor"],
            "auto_allowed": _auto_allowed(cid), "desc": meta["desc"],
            "pattern_count": PATTERN_COUNT.get(cid, 0),
            "recent": stats.get(cid, {"auto-approved": 0, "auto-denied": 0, "asked": 0}),
        })
    patterns = policy.get("patterns") if isinstance(policy, dict) else {}
    if not isinstance(patterns, dict):
        patterns = {}
    recent = list(reversed(entries[-20:]))
    out = {"ok": True, "trusted": bool(trusted), "path": PERM_FILE,
           "exists": os.path.exists(PERM_FILE), "classes": classes,
           "patterns": patterns, "recent": recent}
    if error:
        out["policy_error"] = error
    return out


def permissions_log(n=50):
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 50
    n = max(1, min(500, n))
    entries = _tail_entries(n)
    return {"ok": True, "entries": list(reversed(entries))}


def permissions_test(pattern_key, command=""):
    """GET /api/permissions/test — dry-run decide(); never mutates state."""
    payload = {"pattern_key": pattern_key or "", "command": command or ""}
    return {"ok": True, "verdict": decide(payload)}


def _floor_error(cid, tier):
    return {"ok": False,
            "error": "'%s' has a safety floor of '%s' and cannot be set to '%s'"
                     % (cid, _floor(cid), tier),
            "_status": 403}


def permissions_set(body):
    """POST /api/permissions — set_class/set_pattern/clear_pattern/reset/retrust.

    Returns a dict.  On error it carries `_status` (400 unknown, 403 floor); the
    route registrar pops it and uses it as the HTTP status.  Never raises."""
    try:
        if not isinstance(body, dict):
            return {"ok": False, "error": "body must be a JSON object", "_status": 400}
        op = body.get("op")

        with _lock:
            if op == "set_class":
                cid = body.get("class")
                tier = body.get("tier")
                if cid not in CLASS_META:
                    return {"ok": False, "error": "unknown class %r" % (cid,), "_status": 400}
                if tier not in TIERS:
                    return {"ok": False, "error": "unknown tier %r" % (tier,), "_status": 400}
                if tier == "auto" and _floor(cid) == "ask":
                    return _floor_error(cid, tier)
                policy, _t, _e = _load()
                classes = dict(policy.get("classes") or {})
                classes[cid] = tier
                policy = dict(policy)
                policy["classes"] = classes
                _save(policy)
                _log_policy_change("set_class", {"class": cid, "tier": tier})
                return {"ok": True, "classes": permissions_payload()["classes"]}

            if op == "set_pattern":
                pat = body.get("pattern")
                tier = body.get("tier")
                if not isinstance(pat, str) or not pat.strip():
                    return {"ok": False, "error": "empty pattern", "_status": 400}
                pat = pat.strip()
                if tier not in TIERS:
                    return {"ok": False, "error": "unknown tier %r" % (tier,), "_status": 400}
                cid = _class_of(pat)
                if tier == "auto" and _floor(cid) == "ask":
                    return _floor_error(cid, tier)
                policy, _t, _e = _load()
                pats = dict(policy.get("patterns") or {})
                pats[pat] = tier
                policy = dict(policy)
                policy["patterns"] = pats
                _save(policy)
                _log_policy_change("set_pattern", {"pattern": pat, "class": cid, "tier": tier})
                return {"ok": True, "patterns": _save_view()}

            if op == "clear_pattern":
                pat = body.get("pattern")
                if not isinstance(pat, str) or not pat.strip():
                    return {"ok": False, "error": "empty pattern", "_status": 400}
                pat = pat.strip()
                policy, _t, _e = _load()
                pats = dict(policy.get("patterns") or {})
                pats.pop(pat, None)
                policy = dict(policy)
                policy["patterns"] = pats
                _save(policy)
                _log_policy_change("clear_pattern", {"pattern": pat})
                return {"ok": True, "patterns": _save_view()}

            if op == "reset":
                _save({"classes": {}, "patterns": {}})
                _log_policy_change("reset", {})
                return {"ok": True, "classes": permissions_payload()["classes"]}

            if op == "retrust":
                # Rewrite the sidecar for the CURRENT file content (if any).
                try:
                    with open(PERM_FILE, "rb") as f:
                        raw = f.read()
                    _ensure_dirs()
                    _atomic_write(SIDECAR, (hashlib.sha256(raw).hexdigest() + "\n").encode(), 0o600)
                    _STATE["mtime"] = None
                except OSError:
                    pass   # no policy file yet -> nothing to trust; defaults are safe
                _log_policy_change("retrust", {})
                return {"ok": True, "trusted": True}

            return {"ok": False, "error": "unknown op", "_status": 400}
    except Exception as e:
        return {"ok": False, "error": "internal: " + str(e), "_status": 500}


def _save_view():
    policy, _t, _e = _load()
    pats = policy.get("patterns") if isinstance(policy, dict) else {}
    return pats if isinstance(pats, dict) else {}
