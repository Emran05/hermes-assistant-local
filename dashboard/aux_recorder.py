# aux_recorder.py — Flight Recorder + Undo (P1.2).
#
# Every consequential agent action becomes a timestamped, reviewable row in
# ~/.hermes/dashboard/recorder.db; file-mutating actions ride hermes-agent's
# OWN pre-write checkpoint store (~/.hermes/checkpoints/store) so a one-click
# Undo can restore the byte-identical original.  Irreversibility is decided by
# a FIXED table (TOOL_KIND / REVERSIBLE_POLICY / UNDO_WHITELIST) — never a
# judgment call — and /api/undo is a whitelist machine that structurally
# refuses anything else.
#
# Three observation legs (all cheap, all local, zero network):
#   1. hermes_rpc RECORDER_HOOK  — live hub-turn tool.start/complete events;
#   2. recorder_loop reconciler  — 5s state.db (mode=ro) poll, catches EVERY
#      surface (hub / Telegram / CLI) and any dashboard downtime;
#   3. upstream CheckpointManager (subprocess driver) — the only race-free
#      before-snapshot; we never write its git store, only call restore/list.
#
# Routes: GET /api/recorder (feed + detail), POST /api/undo,
#         GET/POST /api/recorder/retention (the actions TTL — see "retention").
#
# exec'd into server.py globals by the aux loader (after expanders_extra.py and
# aux_memory.py, before class Handler).  May use these server globals:
#   HOME HERE DATA STATE_DB read_json write_json _state_lock _widget_cache
#   _cached register_get register_post.
# Imports ALL its own stdlib deps (exec'd code cannot rely on server.py's
# function-local imports) and defines only new names.

import os
import re
import sys
import json
import time
import shutil
import hashlib
import sqlite3
import subprocess
import threading
import urllib.parse
from datetime import datetime

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
REC_DB      = os.path.join(DATA, "recorder.db")
UNDO_TRASH  = os.path.join(DATA, "undo-trash")
HERMES_SRC  = os.path.join(HOME, ".hermes", "hermes-agent")
CONFIG_YAML = os.path.join(HOME, ".hermes", "config.yaml")
_VENV_PY    = os.path.join(HOME, ".hermes", "hermes-agent", "venv", "bin", "python")
_PY         = _VENV_PY if os.path.exists(_VENV_PY) else sys.executable

_rec_lock   = threading.Lock()

# ---- classification tables (FIXED; P1.3 consumes these as its tier seed) ----
TOOL_KIND = {
    "write_file": "write",  "patch": "write",
    "terminal": "shell",    "process": "shell",  "execute_code": "shell",
    "computer_use": "computer",
    "memory": "memory",
    "read_file": "read", "search_files": "read", "vision_analyze": "read",
    "web_search": "net", "web_extract": "net",
    # Browser tools that only OBSERVE. Unclassified they fell through to
    # "other" -> "no" -> an "irreversible" badge, which read as a warning about
    # a screenshot. The ones that actually drive the page (browser_click /
    # _fill / _type / _press / _dialog / _close) stay "other" on purpose: they
    # DO change something out there and honestly cannot be undone.
    "browser_snapshot": "read", "browser_console": "read",
    "browser_screenshot": "read", "browser_take_screenshot": "read",
    "browser_get_images": "read",
    "browser_navigate": "net", "browser_back": "net",
    "delegate": "agent", "skill": "agent", "clarify": "agent",
    "todo": "other", "cronjob": "other",
}   # unknown tool -> "other"

# Reversibility POLICY is fixed at classification time.  A found snapshot can
# only DOWNGRADE the *effective* reversibility (write/shell without a snapshot
# are "no"); it can never lift a kind above its policy ceiling.
# "n/a" is NOT "no".  read / net / agent kinds change nothing on the Mac, so
# there is no state to restore — labelling them "irreversible" in the UI was
# technically true and actively misleading (a web_search reads as dangerous as
# an rm -rf).  Only write / shell / computer / memory can honestly carry a
# reversible-or-not verdict; everything else answers "the question does not
# apply".  Every consumer treats "n/a" like "no" for undo purposes: it is not
# in ('yes','partial'), so the reversible counter skips it and /api/undo still
# refuses it via UNDO_WHITELIST.
REVERSIBLE_POLICY = {
    "write":    "yes",       # only if a snapshot_ref is found, else -> "no"
    "shell":    "partial",   # file state restorable IF checkpointed; side
                             # effects (network, launchctl, sends) are NOT
    "memory":   "no",        # v1: memory versioning is P1.1's surface
    "computer": "no",        # clicks / keystrokes cannot be unwound
    "read": "n/a", "net": "n/a", "agent": "n/a",   # read-only: nothing to undo
    "other": "no",
}
READONLY_KINDS = ("read", "net", "agent")   # ... and so the migration below
# tools whose classification changed in this pass, for the same migration
_RECLASSIFIED = tuple(k for k in TOOL_KIND if k.startswith("browser_"))
_REV_MIGRATION = 2                          # bump when this table changes again
UNDO_WHITELIST = {"write", "shell"}   # /api/undo refuses everything else
ARGS_CAP, SUMMARY_CAP, HASH_CAP_BYTES = 8192, 500, 32 * 1024 * 1024

# Working-dir markers — a faithful clone of CheckpointManager.
# get_working_dir_for_path (tools/checkpoint_manager.py). MUST match so our
# association resolves to the same per-project ref the agent checkpointed under.
_WD_MARKERS = {".git", "pyproject.toml", "package.json", "Cargo.toml",
               "go.mod", "Makefile", "pom.xml", ".hg", "Gemfile"}

RUNNING_TIMEOUT = 600        # running -> error after 10 min with no completion
UNDO_TRASH_TTL  = 14 * 86400
ASSOC_BACK, ASSOC_FWD = 900, 60     # snapshot ISO within [ts-900, ts+60]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tool_call_id  TEXT UNIQUE,
  ts            REAL NOT NULL,
  session       TEXT DEFAULT '',
  source        TEXT DEFAULT '',
  tool          TEXT NOT NULL,
  args          TEXT DEFAULT '',
  target        TEXT DEFAULT '',
  kind          TEXT NOT NULL,
  reversible    TEXT NOT NULL,
  status        TEXT NOT NULL,
  duration_s    REAL,
  summary       TEXT DEFAULT '',
  snapshot_ref  TEXT DEFAULT '',
  after_state   TEXT DEFAULT '',
  undone_ts     REAL,
  undo_note     TEXT DEFAULT '',
  origin        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_actions_ts   ON actions(ts DESC);
CREATE INDEX IF NOT EXISTS idx_actions_kind ON actions(kind, ts DESC);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def _rec_log(msg):
    try:
        print("[aux_recorder] " + str(msg), file=sys.stderr)
    except Exception:
        pass


# --------------------------------------------------------------------------
# db init / connection
# --------------------------------------------------------------------------
_rec_inited = False
_rec_init_error = ""     # the last init failure, verbatim, for /api/recorder


def _rec_chmod():
    """0600 on the db — recorded args can carry sensitive strings.

    Its OWN function, called outside the try that wraps the schema + the
    migration, because it used to sit inside it: a migration that raised (as
    one did for a release) jumped to the handler and skipped the chmod on the
    way, leaving the database at the umask default with nothing anywhere
    saying so."""
    try:
        os.chmod(REC_DB, 0o600)
    except OSError:
        pass


def _rec_init():
    """Create dirs + schema; recover a corrupt db by renaming aside.  Idempotent."""
    global _rec_inited, _rec_init_error
    if _rec_inited:
        return
    for d in (DATA, UNDO_TRASH):
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
        except OSError:
            pass
    try:
        con = sqlite3.connect(REC_DB, timeout=8.0)
        _rec_chmod()      # the file exists the moment the connect returns —
                          # the permissions must not wait on the migration
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)
            # Versioned backfill: rows written before read/net/agent became
            # "n/a" (and before the browser_* tools were classified at all)
            # still say kind='other' / reversible='no', which the UI renders as
            # an "irreversible" badge on a screenshot.  Bump _REV_MIGRATION
            # whenever TOOL_KIND / REVERSIBLE_POLICY change again.
            row = con.execute("SELECT v FROM meta WHERE k='rev_policy_migration'").fetchone()
            done = int(row[0]) if row and str(row[0]).isdigit() else 0   # plain tuple: no row_factory here
            if done < _REV_MIGRATION:
                for _t in _RECLASSIFIED:            # re-home the browser tools
                    con.execute("UPDATE actions SET kind=? WHERE tool=? AND kind='other'",
                                (TOOL_KIND[_t], _t))
                con.execute(
                    "UPDATE actions SET reversible='n/a' WHERE reversible='no' "
                    "AND status!='undone' AND kind IN (?,?,?)", READONLY_KINDS)
                con.execute("INSERT INTO meta(k,v) VALUES('rev_policy_migration',?) "
                            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(_REV_MIGRATION),))
            con.commit()
        finally:
            con.close()
        _rec_chmod()                     # and again, now that WAL/-shm exist
        _rec_inited = True
        _rec_init_error = ""
    except sqlite3.DatabaseError as e:
        _rec_init_error = "recorder.db corrupt: %r" % e
        _rec_log("recorder.db corrupt, recreating: %r" % e)
        try:
            os.rename(REC_DB, REC_DB + ".corrupt-%d" % int(time.time()))
        except OSError:
            pass
        try:
            con = sqlite3.connect(REC_DB, timeout=8.0)
            _rec_chmod()
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(_SCHEMA)
            con.commit()
            con.close()
            _rec_chmod()
            _rec_inited = True
            _rec_init_error = ""         # recovered: the rename-aside worked
        except Exception as e2:                              # pragma: no cover
            _rec_init_error = "recorder.db recreate failed: %r" % e2
            _rec_log("recorder.db recreate failed: %r" % e2)
    except Exception as e:                                   # pragma: no cover
        _rec_init_error = "init failed: %r" % e
        _rec_log("init failed: %r" % e)


def _rec_init_state():
    """The two keys every /api/recorder answer carries.

    `_rec_init()` swallows its own failures (a dead recorder must never take
    the dashboard down), so a broken init used to be visible ONLY as a stderr
    line in dashboard.log — the API answered with an empty list and
    `recorder_ok: true`, which reads exactly like "you have done nothing yet"."""
    return {"inited": bool(_rec_inited), "init_error": _rec_init_error or None}


def _rec_conn():
    con = sqlite3.connect(REC_DB, timeout=8.0)
    con.execute("PRAGMA busy_timeout=8000")
    con.row_factory = sqlite3.Row
    return con


def _meta_get(k, default=""):
    try:
        con = _rec_conn()
        try:
            r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return r["v"] if r else default
        finally:
            con.close()
    except Exception:
        return default


def _meta_set(k, v):
    with _rec_lock:
        con = _rec_conn()
        try:
            con.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
            con.commit()
        finally:
            con.close()


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _rec_json(s):
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _looks_path(s):
    return isinstance(s, str) and (s.startswith("/") or s.startswith("~"))


def _kind_for(tool):
    return TOOL_KIND.get(tool, "other")


def _reversible_for(kind, has_snapshot):
    """Effective reversibility: policy ceiling, downgraded to 'no' with no snap."""
    if kind == "write":
        return "yes" if has_snapshot else "no"
    if kind == "shell":
        return "partial" if has_snapshot else "no"
    return REVERSIBLE_POLICY.get(kind, "no")


def _target_for(tool, args):
    if not isinstance(args, dict):
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                return ""
        if not isinstance(args, dict):
            return ""
    kind = _kind_for(tool)
    if kind in ("write", "read"):
        return str(args.get("path") or args.get("file_path")
                   or args.get("file") or "")[:300]
    if kind == "shell":
        return str(args.get("command") or args.get("cmd")
                   or args.get("code") or "")[:200]
    if kind == "computer":
        a = args.get("action") or ""
        if not a and args.get("coordinate"):
            a = "click"
        return str(a)[:200]
    if kind == "memory":
        return str(args.get("path") or args.get("file")
                   or args.get("operation") or "")[:200]
    if kind == "net":
        return str(args.get("query") or args.get("url") or "")[:200]
    return ""


def _cap_args(args):
    try:
        s = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False,
                                                           default=str)
    except Exception:
        return ""
    b = s.encode("utf-8", "replace")
    if len(b) > ARGS_CAP:
        return json.dumps({"_truncated": len(b),
                           "preview": b[:ARGS_CAP].decode("utf-8", "replace")})
    return s


def _sha256_file(p):
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _after_state(path):
    try:
        p = os.path.expanduser(path or "")
        if not p:
            return {}
        if not os.path.exists(p):
            return {"exists": False, "size": 0, "mtime": 0, "sha256": None}
        st = os.stat(p)
        sha = None
        if os.path.isfile(p) and st.st_size <= HASH_CAP_BYTES:
            sha = _sha256_file(p)
        return {"exists": True, "size": st.st_size, "mtime": st.st_mtime,
                "sha256": sha}
    except Exception:
        return {}


def _iso_epoch(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        # tolerate a trailing Z or missing colon in the offset
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None


def _workdir_for(path):
    """Clone of CheckpointManager.get_working_dir_for_path — walk up for a
    project marker, else the containing dir.  list_checkpoints/restore
    re-normalize internally, so the returned string need only resolve to the
    same canonical path the agent checkpointed under."""
    try:
        p = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return ""
    check = p if os.path.isdir(p) else os.path.dirname(p)
    if not check:
        return ""
    while True:
        for m in _WD_MARKERS:
            try:
                if os.path.exists(os.path.join(check, m)):
                    return check
            except OSError:
                pass
        parent = os.path.dirname(check)
        if parent == check:
            return (p if os.path.isdir(p) else os.path.dirname(p))
        check = parent


def _row_workdir(kind, target, cwd=""):
    if kind == "write" and target:
        return _workdir_for(target)
    if kind == "shell":
        base = cwd or ""
        if not base and target:
            m = re.search(r"(/[^\s\"']+)", target)   # sniff an abs path from the cmd
            if m:
                base = m.group(1)
        return _workdir_for(base) if base else ""
    return ""


def _cwd_for_session(session_id):
    """Best-effort session cwd from state.db (read-only) for shell association."""
    if not session_id or not os.path.exists(STATE_DB):
        return ""
    try:
        uri = "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            r = con.execute("SELECT cwd FROM sessions WHERE id=?",
                            (session_id,)).fetchone()
            return (r[0] or "") if r else ""
        finally:
            con.close()
    except Exception:
        return ""


# --------------------------------------------------------------------------
# checkpoint driver — short-lived subprocess into the hermes-agent checkout
# (the dashboard python is stdlib-only; the checkout's venv has the deps).
# --------------------------------------------------------------------------
_CKPT_DRIVER = r"""
import json, sys
sys.path.insert(0, %r)
from tools.checkpoint_manager import CheckpointManager
req = json.load(sys.stdin)
mgr = CheckpointManager(enabled=True)
op = req.get("op")
try:
    if op == "list":
        out = mgr.list_checkpoints(req["workdir"])
    elif op == "diff":
        out = mgr.diff(req["workdir"], req["commit"])
    elif op == "restore":
        out = mgr.restore(req["workdir"], req["commit"], req.get("file") or None)
    else:
        out = {"error": "bad op"}
except Exception as e:
    out = {"error": type(e).__name__ + ": " + str(e)}
json.dump(out, sys.stdout, default=str)
""" % (HERMES_SRC,)


def _ckpt(op, **kw):
    try:
        p = subprocess.run([_PY, "-c", _CKPT_DRIVER],
                           input=json.dumps(dict(op=op, **kw)),
                           capture_output=True, text=True, timeout=60)
        if not (p.stdout or "").strip():
            return {"error": "checkpoint driver: empty (%s)" %
                    ((p.stderr or "").strip()[:200] or "no stderr")}
        return json.loads(p.stdout)
    except Exception as e:
        return {"error": "checkpoint driver: %s: %s" % (type(e).__name__, e)}


def _associate(workdir, ts):
    """Newest checkpoint whose reason starts 'before ' with an ISO timestamp
    inside [ts-900, ts+60].  Returns a snapshot_ref dict or None."""
    if not workdir:
        return None
    res = _ckpt("list", workdir=workdir)
    if not isinstance(res, list):
        return None
    lo, hi = ts - ASSOC_BACK, ts + ASSOC_FWD
    for c in res:                                # list is newest-first
        if not isinstance(c, dict):
            continue
        reason = c.get("reason") or ""
        if not reason.startswith("before "):
            continue
        e = _iso_epoch(c.get("timestamp") or "")
        if e is None or not (lo <= e <= hi):
            continue
        return {"workdir": workdir, "commit": c.get("hash", ""),
                "short": c.get("short_hash", ""), "reason": reason,
                "iso": c.get("timestamp", "")}
    return None


def _newest_prerollback(workdir):
    """The pre-rollback snapshot restore() just took — so an Undo is re-undoable."""
    res = _ckpt("list", workdir=workdir)
    if not isinstance(res, list):
        return None
    for c in res:
        if isinstance(c, dict) and (c.get("reason") or "").startswith("pre-rollback"):
            return {"workdir": workdir, "commit": c.get("hash", ""),
                    "short": c.get("short_hash", ""), "reason": c.get("reason", ""),
                    "iso": c.get("timestamp", "")}
    return None


# --------------------------------------------------------------------------
# leg 1 — live hub-turn events (called from hermes_rpc.run_turn via RECORDER_HOOK)
# --------------------------------------------------------------------------
def recorder_ws_event(sid, etype, payload):
    """Never raises — a recorder fault must never break a chat turn."""
    try:
        _rec_init()
        if not isinstance(payload, dict):
            return
        tcid = payload.get("tool_id") or payload.get("id")
        name = payload.get("name") or payload.get("tool") or ""
        if not tcid or not name:
            return
        kind = _kind_for(name)
        now = time.time()
        if etype == "tool.start":
            ctx0 = payload.get("context") or ""
            tgt = ctx0[:200] if _looks_path(ctx0) else ""
            with _rec_lock:
                con = _rec_conn()
                try:
                    con.execute(
                        "INSERT OR IGNORE INTO actions(tool_call_id, ts, session, "
                        "source, tool, target, kind, reversible, status, origin) "
                        "VALUES(?,?,?,?,?,?,?,?, 'running', 'ws')",
                        (tcid, now, sid or "", "hub", name, tgt, kind,
                         _reversible_for(kind, False)))
                    con.commit()
                finally:
                    con.close()
        elif etype == "tool.complete":
            args = payload.get("args")
            target = _target_for(name, args)
            summ = (payload.get("summary") or "")[:SUMMARY_CAP]
            dur = payload.get("duration_s")
            argj = _cap_args(args) if args is not None else ""
            with _rec_lock:
                con = _rec_conn()
                try:
                    con.execute(
                        "INSERT OR IGNORE INTO actions(tool_call_id, ts, session, "
                        "source, tool, target, kind, reversible, status, origin) "
                        "VALUES(?,?,?,?,?,?,?,?, 'running', 'ws')",
                        (tcid, now, sid or "", "hub", name, target, kind,
                         _reversible_for(kind, False)))
                    con.execute(
                        "UPDATE actions SET args=?, "
                        "target=CASE WHEN ?<>'' THEN ? ELSE target END, "
                        "duration_s=?, summary=?, status='done' "
                        "WHERE tool_call_id=?",
                        (argj, target, target, dur, summ, tcid))
                    r = con.execute("SELECT ts FROM actions WHERE tool_call_id=?",
                                    (tcid,)).fetchone()
                    row_ts = r["ts"] if r else now
                    con.commit()
                finally:
                    con.close()
            if kind in ("write", "shell"):
                threading.Thread(target=_finalize_ws,
                                 args=(tcid, target, row_ts, kind),
                                 daemon=True).start()
    except Exception as e:                                   # pragma: no cover
        _rec_log("ws_event: %r" % e)


def _finalize_ws(tcid, target, ts, kind):
    """Fire-and-forget: after_state + snapshot association off the chat thread."""
    try:
        after = _after_state(target) if (kind == "write" and _looks_path(target)) else {}
        wd = _row_workdir(kind, target, "")
        snap = _associate(wd, ts) if wd else None
        rev = _reversible_for(kind, bool(snap))
        with _rec_lock:
            con = _rec_conn()
            try:
                con.execute(
                    "UPDATE actions SET after_state=?, snapshot_ref=?, reversible=? "
                    "WHERE tool_call_id=?",
                    (json.dumps(after) if after else "",
                     json.dumps(snap) if snap else "", rev, tcid))
                con.commit()
            finally:
                con.close()
    except Exception as e:                                   # pragma: no cover
        _rec_log("finalize_ws: %r" % e)


# --------------------------------------------------------------------------
# dashboard-side local logging (P1.1 / P1.3 call this for their own mutations)
# --------------------------------------------------------------------------
def recorder_record_local(tool, target, kind="write", reversible="yes",
                          source="dashboard", **kw):
    try:
        _rec_init()
        tcid = kw.get("tool_call_id") or ("local-%s-%d" % (tool, int(time.time() * 1000)))
        now = kw.get("ts") or time.time()
        args = kw.get("args")
        argj = _cap_args(args) if args is not None else ""
        snap = kw.get("snapshot_ref")
        after = kw.get("after_state")
        with _rec_lock:
            con = _rec_conn()
            try:
                con.execute(
                    "INSERT OR IGNORE INTO actions(tool_call_id, ts, session, source, "
                    "tool, args, target, kind, reversible, status, summary, "
                    "snapshot_ref, after_state, origin) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'dashboard')",
                    (tcid, now, kw.get("session", ""), source, tool, argj,
                     str(target or ""), kind, reversible,
                     kw.get("status", "done"), (kw.get("summary") or "")[:SUMMARY_CAP],
                     json.dumps(snap) if isinstance(snap, dict) else (snap or ""),
                     json.dumps(after) if isinstance(after, dict) else (after or "")))
                con.commit()
            finally:
                con.close()
        return {"ok": True, "tool_call_id": tcid}
    except Exception as e:
        _rec_log("record_local: %r" % e)
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------
# leg 2 — state.db reconciler (all surfaces, mode=ro, cursor + dedupe)
# --------------------------------------------------------------------------
_rec_gc_last = 0.0
_rec_retain_last = 0.0          # 0.0 -> the first loop tick sweeps
_rec_retain_state = {}          # the last sweep's result, for /api/recorder/retention


def _reconcile_once():
    if not os.path.exists(STATE_DB):
        return
    try:
        cursor_f = float(_meta_get("statedb_cursor", "0") or 0)
    except Exception:
        cursor_f = 0.0
    try:
        uri = "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error:
        return
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT m.id AS mid, m.role AS role, m.content AS content, "
            "m.tool_calls AS tc, m.tool_name AS tname, m.tool_call_id AS tcid, "
            "m.timestamp AS ts, s.source AS src, m.session_id AS sess "
            "FROM messages m JOIN sessions s ON m.session_id = s.id "
            "WHERE m.timestamp > ? AND (m.tool_calls IS NOT NULL OR m.tool_name IS NOT NULL) "
            "ORDER BY m.timestamp LIMIT 500", (cursor_f,)).fetchall()
    except sqlite3.Error:
        con.close()
        return
    con.close()
    if not rows:
        return

    max_ts = cursor_f
    calls = []       # dicts for assistant tool_calls
    results = []     # (tcid, summary) for tool result rows
    for r in rows:
        ts = r["ts"] or 0
        if ts > max_ts:
            max_ts = ts
        if r["tc"]:
            try:
                parsed = json.loads(r["tc"])
            except Exception:
                parsed = []
            for c in (parsed if isinstance(parsed, list) else []):
                if not isinstance(c, dict):
                    continue
                fn = c.get("function") or {}
                name = fn.get("name") or c.get("name") or "tool"
                arguments = fn.get("arguments")
                if arguments is None:
                    arguments = c.get("arguments") or c.get("input")
                cid = c.get("id") or ("sd-%s-%s" % (r["mid"], name))
                kind = _kind_for(name)
                calls.append({
                    "cid": cid, "ts": ts, "sess": r["sess"] or "",
                    "src": r["src"] or "cli", "name": name, "kind": kind,
                    "target": _target_for(name, arguments),
                    "args": _cap_args(arguments) if arguments is not None else ""})
        elif r["tname"]:
            if r["tcid"]:
                results.append((r["tcid"], (r["content"] or "").strip()[:SUMMARY_CAP]))

    with _rec_lock:
        con = _rec_conn()
        try:
            for c in calls:
                con.execute(
                    "INSERT OR IGNORE INTO actions(tool_call_id, ts, session, source, "
                    "tool, args, target, kind, reversible, status, origin) "
                    "VALUES(?,?,?,?,?,?,?,?,?, 'running', 'statedb')",
                    (c["cid"], c["ts"], c["sess"], c["src"], c["name"], c["args"],
                     c["target"], c["kind"], _reversible_for(c["kind"], False)))
                # fill only columns still empty on an existing ws row
                con.execute(
                    "UPDATE actions SET "
                    "args=CASE WHEN args='' THEN ? ELSE args END, "
                    "target=CASE WHEN target='' THEN ? ELSE target END, "
                    "session=CASE WHEN session='' THEN ? ELSE session END "
                    "WHERE tool_call_id=?",
                    (c["args"], c["target"], c["sess"], c["cid"]))
            for tcid, summ in results:
                con.execute(
                    "UPDATE actions SET status='done', "
                    "summary=CASE WHEN summary='' THEN ? ELSE summary END "
                    "WHERE tool_call_id=?", (summ, tcid))
            con.commit()
        finally:
            con.close()

    _meta_set("statedb_cursor", repr(max_ts))


def _finalize_pass():
    """Attach snapshot + after_state to recent write/shell rows; after 15 min
    with none, mark them plainly non-undoable.  Bounded subprocess spawns."""
    now = time.time()
    with _rec_lock:
        con = _rec_conn()
        try:
            cands = [dict(x) for x in con.execute(
                "SELECT id, tool, kind, target, ts, session, after_state "
                "FROM actions WHERE kind IN ('write','shell') AND snapshot_ref='' "
                "AND undo_note='' AND status!='undone' AND ts > ? "
                "ORDER BY ts DESC LIMIT 40", (now - 1800,)).fetchall()]
        finally:
            con.close()
    for c in cands:
        cwd = _cwd_for_session(c["session"]) if c["kind"] == "shell" else ""
        wd = _row_workdir(c["kind"], c["target"], cwd)
        snap = _associate(wd, c["ts"]) if wd else None
        after = None
        if c["kind"] == "write" and _looks_path(c["target"]) and not c["after_state"]:
            after = _after_state(c["target"])
        sets, vals = [], []
        if snap:
            sets += ["snapshot_ref=?", "reversible=?"]
            vals += [json.dumps(snap), _reversible_for(c["kind"], True)]
        elif c["ts"] < now - 900:
            sets += ["undo_note=?", "reversible='no'"]
            vals += ["no snapshot captured"]
        if after is not None:
            sets += ["after_state=?"]
            vals += [json.dumps(after)]
        if not sets:
            continue
        vals.append(c["id"])
        with _rec_lock:
            con = _rec_conn()
            try:
                con.execute("UPDATE actions SET " + ", ".join(sets) + " WHERE id=?",
                            tuple(vals))
                con.commit()
            finally:
                con.close()


def _sweep_running():
    with _rec_lock:
        con = _rec_conn()
        try:
            con.execute(
                "UPDATE actions SET status='error', "
                "undo_note=CASE WHEN undo_note='' THEN 'no completion observed' "
                "ELSE undo_note END WHERE status='running' AND ts < ?",
                (time.time() - RUNNING_TIMEOUT,))
            con.commit()
        finally:
            con.close()


def _gc_trash():
    cutoff = time.time() - UNDO_TRASH_TTL
    try:
        for e in os.scandir(UNDO_TRASH):
            try:
                if e.stat(follow_symlinks=False).st_mtime < cutoff:
                    if e.is_dir(follow_symlinks=False):
                        shutil.rmtree(e.path, ignore_errors=True)
                    else:
                        os.remove(e.path)
            except OSError:
                pass
    except OSError:
        pass


# --------------------------------------------------------------------------
# retention — a TTL for the `actions` table (settings.json recorder.retain_days)
# --------------------------------------------------------------------------
# recorder.db had none: three observation legs write a row per tool call from
# every surface and nothing ever removed one.  A window is swept once a day
# now, and it is deliberately NOT the whole table.  Two kinds of row are undo
# material and are never swept, whatever their age:
#
#   * status='undone' — the RECEIPT for a restore the user performed.  It is
#     the only record that the undo happened, /api/undo's "already undone"
#     guard reads it, and the undo itself wrote a second row pointing at the
#     pre-rollback snapshot that makes the undo re-undoable.
#   * a non-empty snapshot_ref — the row still names a checkpoint to restore
#     from.  Whether that checkpoint is still in the agent's store is upstream's
#     business (a pruned one is discovered at undo time and the row is marked
#     `reversible='no', undo_note='snapshot pruned'`); keeping such a row costs
#     one row, deleting a row whose checkpoint IS still there costs the undo.
#   * reversible IN ('yes','partial') for kind IN ('write','shell') even with
#     an EMPTY snapshot_ref. Two ways a row can be in that state: (1)
#     recorder_record_local()'s `reversible="yes"` default lets a caller log a
#     dashboard-local mutation before it has a snapshot_ref to hand over —
#     /api/undo's lazy re-association (recorder_undo_handler, the "if not
#     snap: re-attempt association once" branch) is exactly the path meant to
#     fill that in later, so the row must survive long enough to reach it; (2)
#     a row synced from state.db (leg 2) whose ORIGINAL event `ts` was already
#     more than 30 min old the moment it was inserted — _finalize_pass() below
#     only ever looks at `ts > now - 1800`, so a backlog import (cursor reset,
#     recorder down for a while) never gets a single finalize attempt and is
#     stuck wherever it started. Widening the predicate (retention-side) was
#     chosen over widening _finalize_pass's window (reconciler-side): removing
#     _finalize_pass's age cap would mean every sweep re-running `_ckpt list`
#     subprocess calls against a potentially unbounded backlog of ancient
#     rows, which is a live-thread cost every 5s tick pays forever, versus
#     this predicate, which only ever costs one extra OR clause on a query
#     that already runs once a day.
#
# The floor comes from the undo trash.  `_gc_trash` keeps everything under
# ~/.hermes/dashboard/undo-trash for UNDO_TRASH_TTL (14 days) regardless of how
# old the action that produced it was — a file moved aside by an undo is
# recoverable for a fortnight.  A retention window shorter than that would
# delete the row explaining a file still sitting in the trash, so any non-zero
# window is clamped up to the same 14 days.  0 means keep forever and skips the
# sweep entirely.
REC_RETAIN_DEFAULT  = 90
REC_RETAIN_MIN_DAYS = UNDO_TRASH_TTL // 86400        # 14 — the undo-trash window
REC_RETAIN_MAX_DAYS = 3650
REC_RETAIN_EVERY_S  = 86400                          # one sweep a day

# The rows retention must never touch, as one SQL predicate. Kept in one place
# so the count, the preview and the DELETE cannot drift apart.
_REC_KEEP_SQL = ("status='undone' OR (snapshot_ref IS NOT NULL AND snapshot_ref!='') "
                 "OR (reversible IN ('yes','partial') AND kind IN ('write','shell'))")


def _rec_retain_clamp(v):
    """days -> the window actually used.  0 (or a negative) means keep forever;
    anything else is clamped into [REC_RETAIN_MIN_DAYS, REC_RETAIN_MAX_DAYS];
    an absent or unparsable value falls back to the default.  PURE, so the whole
    policy is testable without a database.

    bool is rejected even though it's an int subclass in Python: True/False
    passing silently as 1/0 would mean a settings.json `"retain_days": false`
    (hand-edited, or written by a future bug) turns retention off entirely
    (0 = keep forever) instead of falling back to the default. The one
    existing caller reachable from a request (recorder_retention_post) already
    checks this at the HTTP layer, but _rec_retain_days() reads settings.json
    straight through this function with no such guard, so the check belongs
    here too."""
    if v is None or isinstance(v, bool):
        return REC_RETAIN_DEFAULT
    try:
        n = int(v)
    except (TypeError, ValueError):
        return REC_RETAIN_DEFAULT
    if n <= 0:
        return 0
    return max(REC_RETAIN_MIN_DAYS, min(REC_RETAIN_MAX_DAYS, n))


def _rec_retain_days():
    """The configured window, read FRESH every time (settings.json is tiny), so
    a change takes effect with no restart and no cache to invalidate."""
    try:
        cfg = (get_settings() or {}).get("recorder")
    except Exception:
        return REC_RETAIN_DEFAULT
    if not isinstance(cfg, dict):
        return REC_RETAIN_DEFAULT
    return _rec_retain_clamp(cfg.get("retain_days"))


def _rec_set_retain_days(v):
    """Write the window through server.py's settings_update() — one locked
    read-modify-write, so every other key in settings.json survives
    (2026-09-10 audit A01).  Returns the CLAMPED value, which the caller shows
    back to the user (a typed 7 becomes 14 and the field must say so, the
    lesson aux_watchtower's time inputs already learned)."""
    days = _rec_retain_clamp(v)

    def _apply(s):
        cfg = s.get("recorder")
        s["recorder"] = {**(cfg if isinstance(cfg, dict) else {}),
                         "retain_days": days}

    settings_update(_apply)
    return days


def _rec_retention_counts(con, cutoff):
    """(sweepable, kept_undone, kept_snapshot) older than `cutoff`."""
    doomed = con.execute(
        "SELECT COUNT(*) FROM actions WHERE ts < ? AND NOT (%s)" % _REC_KEEP_SQL,
        (cutoff,)).fetchone()[0]
    kept_undone = con.execute(
        "SELECT COUNT(*) FROM actions WHERE ts < ? AND status='undone'",
        (cutoff,)).fetchone()[0]
    kept_snap = con.execute(
        "SELECT COUNT(*) FROM actions WHERE ts < ? AND status!='undone' "
        "AND snapshot_ref IS NOT NULL AND snapshot_ref!=''", (cutoff,)).fetchone()[0]
    return doomed, kept_undone, kept_snap


def _rec_sweep_retention(days=None, dry=False, now=None, db=None):
    """Delete `actions` rows older than the window, keeping the undo material.

    `dry=True` counts without deleting (that is also what the API's preview
    uses); `db` points the sweep at another file, which is how it is tested
    against a COPY instead of the owner's live recorder.db.  Never raises: a
    failed sweep must not take the reconciler thread down."""
    global _rec_retain_state
    days = _rec_retain_days() if days is None else _rec_retain_clamp(days)
    now = time.time() if now is None else float(now)
    if not days:
        out = {"ok": True, "days": 0, "forever": True, "deleted": 0,
               "would_delete": 0, "kept_undone": 0, "kept_snapshot": 0,
               "dry": bool(dry), "ts": now, "note": "retention off — nothing is swept"}
        _rec_retain_state = out
        return out
    cutoff = now - days * 86400
    try:
        with _rec_lock:
            con = sqlite3.connect(db or REC_DB, timeout=8.0)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA busy_timeout=8000")
                doomed, kept_undone, kept_snap = _rec_retention_counts(con, cutoff)
                if doomed and not dry:
                    con.execute("DELETE FROM actions WHERE ts < ? AND NOT (%s)"
                                % _REC_KEEP_SQL, (cutoff,))
                    con.commit()
            finally:
                con.close()
        # `deleted` used to report the pre-DELETE count even when dry=True —
        # nothing was actually deleted on a dry run, so that lied to any
        # caller reading `deleted` without also checking `dry`. `would_delete`
        # is always the count either way (matches recorder_retention_get's
        # own next_sweep.would_delete preview); `deleted` is only ever
        # nonzero on a real sweep.
        out = {"ok": True, "days": days, "forever": False,
               "deleted": 0 if dry else doomed, "would_delete": doomed,
               "kept_undone": kept_undone, "kept_snapshot": kept_snap,
               "cutoff": round(cutoff, 3), "dry": bool(dry), "ts": now}
    except Exception as e:                                   # pragma: no cover
        out = {"ok": False, "days": days, "deleted": 0, "dry": bool(dry),
               "ts": now, "error": "%s: %s" % (type(e).__name__, e)}
        _rec_log("retention sweep failed: %r" % e)
        if db is None:
            _rec_retain_state = out
        return out
    # One line per sweep, deleted count included, so the log says what a TTL
    # took away — the thing nobody can reconstruct afterwards.
    _rec_log("retention sweep: %s %d row(s) older than %d day(s)%s; kept %d undone "
             "+ %d with a snapshot"
             % ("would delete" if dry else "deleted", doomed, days,
                (" [%s]" % os.path.basename(db)) if db else "",
                kept_undone, kept_snap))
    if db is None:
        _rec_retain_state = out
    return out


def recorder_loop():
    global _rec_gc_last, _rec_retain_last
    _rec_init()
    _rec_log("reconciler started")
    while True:
        try:
            _reconcile_once()
            _finalize_pass()
            _sweep_running()
            if time.time() - _rec_gc_last > 3600:
                _gc_trash()
                _rec_gc_last = time.time()
            # Daily TTL. `_rec_retain_last` starts at 0.0, so the first pass
            # after a restart sweeps; a settings change resets it to 0.0 and
            # the next 5 s tick applies the new window.
            if time.time() - _rec_retain_last > REC_RETAIN_EVERY_S:
                _rec_retain_last = time.time()
                _rec_sweep_retention()
        except Exception as e:                               # pragma: no cover
            _rec_log("loop: %r" % e)
        time.sleep(5)


# --------------------------------------------------------------------------
# config gate — checkpoints.enabled (cheap yaml regex, cached 60s)
# --------------------------------------------------------------------------
def _checkpoints_enabled():
    def build():
        try:
            with open(CONFIG_YAML, encoding="utf-8") as f:
                txt = f.read()
        except OSError:
            return False
        return bool(re.search(
            r"checkpoints:\s*\n(?:[ \t].*\n)*?[ \t]+enabled:\s*true", txt))
    try:
        return _cached("recorder_ckpt_enabled", 60, build)
    except Exception:
        return build()


# --------------------------------------------------------------------------
# GET /api/recorder  (list + detail)
# --------------------------------------------------------------------------
def _row_public(r):
    return {"id": r["id"], "ts": r["ts"], "source": r["source"], "tool": r["tool"],
            "target": r["target"], "kind": r["kind"], "reversible": r["reversible"],
            "status": r["status"], "duration_s": r["duration_s"],
            "summary": r["summary"], "has_snapshot": bool(r["snapshot_ref"]),
            "undone_ts": r["undone_ts"], "origin": r["origin"]}


def _api_detail(aid):
    try:
        con = _rec_conn()
        try:
            r = con.execute("SELECT * FROM actions WHERE id=?", (aid,)).fetchone()
        finally:
            con.close()
    except Exception as e:
        return {"error": "internal: " + str(e)}
    if not r:
        return {"error": "not found"}
    r = dict(r)
    out = _row_public(r)
    out["args"] = _rec_json(r["args"]) or r["args"]
    out["after_state"] = _rec_json(r["after_state"])
    out["snapshot_ref"] = _rec_json(r["snapshot_ref"])
    out["undo_note"] = r["undo_note"]
    snap = out["snapshot_ref"]
    if snap.get("commit") and snap.get("workdir"):
        d = _ckpt("diff", workdir=snap["workdir"], commit=snap["commit"])
        if isinstance(d, dict) and d.get("success"):
            diff = (d.get("diff") or "")[:20480]
            out["diff"] = diff
            out["diff_stat"] = (d.get("stat") or "")[:4096]
        elif isinstance(d, dict) and d.get("error"):
            out["diff_error"] = d["error"]
    return out


def recorder_api_handler(ctx):
    try:
        _rec_init()
    except Exception as e:
        out = {"recorder_ok": False, "error": "init: " + str(e), "actions": [],
               "counts": {"total": 0, "reversible": 0, "undone": 0},
               "checkpoints_enabled": _checkpoints_enabled()}
        out.update(_rec_init_state())
        return out
    try:
        aid = ctx.q1("id", "")
        if aid:
            try:
                return _api_detail(int(aid))
            except (TypeError, ValueError):
                return {"error": "not found"}
        try:
            limit = min(200, max(1, int(ctx.q1("limit", "50") or 50)))
        except ValueError:
            limit = 50
        before = ctx.q1("before", "")
        kinds = [k for k in (ctx.q1("kind", "") or "").split(",") if k]
        q = ctx.q1("q", "").strip()

        where, params = ["1=1"], []
        if before:
            try:
                where.append("id < ?")
                params.append(int(before))
            except ValueError:
                pass
        if kinds:
            if "undone" in kinds:
                others = [k for k in kinds if k != "undone"]
                if others:
                    where.append("(status='undone' OR kind IN (%s))" %
                                 ",".join("?" * len(others)))
                    params += others
                else:
                    where.append("status='undone'")
            else:
                where.append("kind IN (%s)" % ",".join("?" * len(kinds)))
                params += kinds
        if q:
            where.append("(target LIKE ? OR tool LIKE ?)")
            params += ["%" + q + "%", "%" + q + "%"]

        con = _rec_conn()
        try:
            rows = con.execute(
                "SELECT * FROM actions WHERE " + " AND ".join(where) +
                " ORDER BY ts DESC, id DESC LIMIT ?", tuple(params) + (limit,)).fetchall()
            total = con.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
            reversible = con.execute(
                "SELECT COUNT(*) FROM actions WHERE reversible IN ('yes','partial') "
                "AND status!='undone'").fetchone()[0]
            undone = con.execute(
                "SELECT COUNT(*) FROM actions WHERE status='undone'").fetchone()[0]
        finally:
            con.close()
        out = {"actions": [_row_public(x) for x in rows],
               "counts": {"total": total, "reversible": reversible, "undone": undone},
               "checkpoints_enabled": _checkpoints_enabled(),
               "recorder_ok": True}
        out.update(_rec_init_state())
        return out
    except Exception as e:
        out = {"recorder_ok": False, "error": "internal: " + str(e), "actions": [],
               "counts": {"total": 0, "reversible": 0, "undone": 0},
               "checkpoints_enabled": _checkpoints_enabled()}
        out.update(_rec_init_state())
        return out


# --------------------------------------------------------------------------
# POST /api/undo  (whitelist machine — refuses on any doubt)
# --------------------------------------------------------------------------
def _irreversible_detail(kind):
    return {"computer": "computer-use actions cannot be undone",
            "net": "network sends cannot be recalled",
            "memory": "memory edits are versioned separately (see the Mind view)",
            "read": "reads change nothing, so there is nothing to undo",
            "agent": "delegation / skill calls are not directly reversible",
            "other": "this action has no snapshot and cannot be undone",
            }.get(kind, "this action cannot be undone")


def _to_trash(target):
    p = os.path.expanduser(target)
    base = os.path.basename(p.rstrip("/")) or "file"
    dest = os.path.join(UNDO_TRASH, "%d-%s" % (int(time.time()), base))
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(UNDO_TRASH, "%d-%d-%s" % (int(time.time()), n, base))
    shutil.move(p, dest)
    return dest


def _finish_undo(con, aid, row, note, snap_for_undo_row=None, scope=""):
    """Flip the row to undone and log the undo itself as its own action row."""
    now = time.time()
    con.execute("UPDATE actions SET status='undone', undone_ts=?, undo_note=? WHERE id=?",
                (now, note, aid))
    con.execute(
        "INSERT OR IGNORE INTO actions(tool_call_id, ts, session, source, tool, "
        "target, kind, reversible, status, summary, snapshot_ref, origin) "
        "VALUES(?,?,?,?,?,?,?,?, 'done', ?, ?, 'dashboard')",
        ("undo-%d-%d" % (aid, int(now * 1000)), now, "", "dashboard", "undo",
         row["target"], "write", "yes",
         ("undo of #%d — %s" % (aid, note))[:SUMMARY_CAP],
         json.dumps(snap_for_undo_row) if snap_for_undo_row else "",
         ))
    con.commit()


def recorder_undo_handler(ctx):
    try:
        _rec_init()
        body = ctx.body or {}
        try:
            aid = int(body.get("id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "unknown action"}
        force = bool(body.get("force"))

        with _rec_lock:
            con = _rec_conn()
            try:
                r = con.execute("SELECT * FROM actions WHERE id=?", (aid,)).fetchone()
                if not r:
                    return {"ok": False, "error": "unknown action"}
                row = dict(r)
                if row["status"] == "undone":
                    return {"ok": False, "error": "already undone"}
                if row["kind"] not in UNDO_WHITELIST or row["reversible"] == "no":
                    if row["kind"] in UNDO_WHITELIST and row["reversible"] == "no":
                        detail = row["undo_note"] or "no snapshot was captured for this action"
                    else:
                        detail = _irreversible_detail(row["kind"])
                    return {"ok": False, "error": "irreversible", "detail": detail}

                target = row["target"]
                after = _rec_json(row["after_state"])
                tp = os.path.expanduser(target) if target else ""

                def _conflict():
                    """None when the target still matches `after`, else details.

                    Audit 2026-09-10 A06: this used to check ONLY the hash, and
                    `_after_state()` deliberately stores sha256=None above
                    HASH_CAP_BYTES (32 MiB) — so every edit the owner made to a
                    large file was restored over with force:false. It degrades
                    to existence/type/size/mtime, and anything it CANNOT
                    establish now (stat denied, bytes unreadable) counts as an
                    unresolved conflict rather than as "unchanged".
                    """
                    if not tp or "exists" not in after:
                        return None          # no recorded state (shell rows): nothing to compare
                    was = bool(after.get("exists"))
                    try:
                        st = os.stat(tp)
                    except FileNotFoundError:
                        st = None
                    except OSError as e:
                        return {"reason": "unreadable",
                                "detail": "cannot read the current state of %s (%s)"
                                          % (target, e.strerror or e)}
                    if bool(st) != was:
                        return {"reason": "created" if st else "deleted",
                                "detail": ("this file was created after the agent "
                                           "removed it") if st else
                                          "this file no longer exists"}
                    if st is None:
                        return None          # gone then, gone now
                    was_type = after.get("type") or ("file" if after.get("sha256") else "")
                    cur_type = ("file" if os.path.isfile(tp)
                                else "dir" if os.path.isdir(tp) else "other")
                    if was_type and cur_type != was_type:
                        return {"reason": "type",
                                "detail": "this path is now a %s, not a %s"
                                          % (cur_type, was_type)}
                    if after.get("sha256"):
                        cur = _sha256_file(tp)
                        if not cur:
                            return {"reason": "unreadable",
                                    "detail": "could not read %s to compare it" % target}
                        if cur != after["sha256"]:
                            return {"reason": "hash",
                                    "detail": "contents differ from what the agent wrote"}
                        return None
                    # No hash (large file, or a non-regular target) — size+mtime.
                    if "size" in after and st.st_size != after.get("size"):
                        return {"reason": "size",
                                "detail": "size is %d bytes, was %s when the agent "
                                          "finished" % (st.st_size, after.get("size"))}
                    if after.get("mtime") is not None:
                        try:
                            drift = abs(st.st_mtime - float(after["mtime"]))
                        except (TypeError, ValueError):
                            return {"reason": "unreadable",
                                    "detail": "recorded modification time is unusable"}
                        if drift > 0.001:
                            return {"reason": "mtime",
                                    "detail": "modified since the agent finished"}
                    return None

                clash = _conflict()
                if clash and not force:
                    return {"ok": False, "conflict": True,
                            "error": "file changed since the agent wrote it",
                            "reason": clash["reason"], "detail": clash["detail"],
                            "target": target,
                            "hint": "re-send with force: true to restore anyway"}

                snap = _rec_json(row["snapshot_ref"])
                if not snap:                     # re-attempt association once
                    cwd = _cwd_for_session(row["session"]) if row["kind"] == "shell" else ""
                    wd = _row_workdir(row["kind"], target, cwd)
                    snap = _associate(wd, row["ts"]) if wd else None
                    if snap:
                        con.execute("UPDATE actions SET snapshot_ref=? WHERE id=?",
                                    (json.dumps(snap), aid))
                        con.commit()
                if not snap:
                    return {"ok": False, "error": "no snapshot available"}

                workdir = snap.get("workdir") or ""
                commit = snap.get("commit") or ""

                if row["kind"] == "write" and target:
                    try:
                        rel = os.path.relpath(os.path.realpath(tp),
                                              os.path.realpath(workdir))
                    except Exception:
                        rel = os.path.basename(tp)
                    res = _ckpt("restore", workdir=workdir, commit=commit, file=rel)
                    if isinstance(res, dict) and res.get("success"):
                        undo_snap = _newest_prerollback(workdir)
                        note = "restored to " + (res.get("restored_to") or snap.get("short", ""))
                        _finish_undo(con, aid, row, note, snap_for_undo_row=undo_snap)
                        return {"ok": True, "restored_to": res.get("restored_to"),
                                "file": target,
                                "note": "pre-rollback snapshot taken — undo is itself undoable"}
                    err = ((res.get("error") or "") + " " + (res.get("debug") or "")).lower()
                    if after.get("exists") and ("did not match" in err or "pathspec" in err):
                        # created-file undo — move aside, never hard-delete
                        try:
                            trash = _to_trash(target)
                        except Exception as e:
                            return {"ok": False, "error": "could not move file: " + str(e)}
                        undo_snap = _newest_prerollback(workdir)
                        _finish_undo(con, aid, row, "created file moved to " + trash,
                                     snap_for_undo_row=undo_snap)
                        return {"ok": True, "restored_to": "(removed)", "file": target,
                                "trash": trash,
                                "note": "the agent had created this file — moved to undo-trash (not deleted)"}
                    if "not found" in err or "no checkpoints" in err:
                        con.execute("UPDATE actions SET reversible='no', "
                                    "undo_note='snapshot pruned' WHERE id=?", (aid,))
                        con.commit()
                        return {"ok": False, "error": "snapshot was pruned"}
                    return {"ok": False, "error": res.get("error") or "restore failed"}

                # shell / no single target -> whole-directory restore
                res = _ckpt("restore", workdir=workdir, commit=commit)
                if isinstance(res, dict) and res.get("success"):
                    undo_snap = _newest_prerollback(workdir)
                    note = "directory restored to " + (res.get("restored_to") or snap.get("short", ""))
                    _finish_undo(con, aid, row, note, snap_for_undo_row=undo_snap,
                                 scope="directory")
                    return {"ok": True, "restored_to": res.get("restored_to"),
                            "scope": "directory", "directory": workdir,
                            "note": "restored every file in the directory to turn start; "
                                    "command side effects are NOT reversed"}
                err = (res.get("error") or "").lower()
                if "not found" in err or "no checkpoints" in err:
                    con.execute("UPDATE actions SET reversible='no', "
                                "undo_note='snapshot pruned' WHERE id=?", (aid,))
                    con.commit()
                    return {"ok": False, "error": "snapshot was pruned"}
                return {"ok": False, "error": res.get("error") or "restore failed"}
            finally:
                con.close()
    except Exception as e:
        _rec_log("undo: %r" % e)
        return {"ok": False, "error": "internal: " + str(e)}


def recorder_retention_get(ctx):
    """The window, its bounds, what the last sweep did — and a DRY PREVIEW of
    what the next one would remove.

    The preview is the point of having a GET at all: a TTL whose effect cannot
    be seen before it runs is a data-loss button with a friendly label."""
    try:
        _rec_init()
        days = _rec_retain_days()
        raw = None
        try:
            cfg = (get_settings() or {}).get("recorder")
            raw = cfg.get("retain_days") if isinstance(cfg, dict) else None
        except Exception:
            pass
        try:
            same = raw is not None and not isinstance(raw, bool) and int(raw) == days
        except (TypeError, ValueError):
            same = False
        out = {"ok": True, "retain_days": days, "forever": days == 0,
               "configured": raw, "clamped": (raw is not None and not same),
               "min_days": REC_RETAIN_MIN_DAYS, "max_days": REC_RETAIN_MAX_DAYS,
               "default_days": REC_RETAIN_DEFAULT,
               "undo_trash_days": UNDO_TRASH_TTL // 86400,
               "sweep_every_hours": REC_RETAIN_EVERY_S // 3600,
               "last_sweep": (_rec_retain_state or None)}
        con = _rec_conn()
        try:
            out["total"] = con.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
            if days:
                cutoff = time.time() - days * 86400
                d, ku, ks = _rec_retention_counts(con, cutoff)
                out["next_sweep"] = {"cutoff": round(cutoff, 3), "would_delete": d,
                                     "kept_undone": ku, "kept_snapshot": ks}
            else:
                out["next_sweep"] = None
        finally:
            con.close()
        out.update(_rec_init_state())
        return out
    except Exception as e:                                   # pragma: no cover
        return {"ok": False, "error": "internal: " + str(e)}


def recorder_retention_post(ctx):
    """Set the window.  `{"retain_days": 0}` = keep forever; anything else is
    clamped into [min, max] and the CLAMPED value comes back, so a field that
    typed 7 can redraw itself as 14 instead of lying about what was stored."""
    global _rec_retain_last
    try:
        _rec_init()
        body = ctx.body if isinstance(ctx.body, dict) else {}
        if "retain_days" not in body:
            return {"ok": False,
                    "error": "retain_days required (0 = keep forever)"}, 400
        want = body.get("retain_days")
        if isinstance(want, bool):
            return {"ok": False, "error": "retain_days must be a number of days"}, 400
        try:
            asked = int(want)
        except (TypeError, ValueError):
            return {"ok": False, "error": "retain_days must be a number of days"}, 400
        days = _rec_set_retain_days(asked)
        # Apply it through the ONE code path that sweeps — the reconciler's next
        # 5 s tick.  Deleting inline would put an unbounded DELETE on a request
        # thread and give the sweep a second implementation to drift from.
        _rec_retain_last = 0.0
        out = recorder_retention_get(ctx)
        if isinstance(out, dict):
            out["asked"] = asked
            out["clamped"] = (asked != days)
        return out
    except Exception as e:                                   # pragma: no cover
        return {"ok": False, "error": "internal: " + str(e)}


# --------------------------------------------------------------------------
# wiring — init, routes, hub-live hook, reconciler thread (all guarded)
# --------------------------------------------------------------------------
_rec_init()

register_get("/api/recorder", recorder_api_handler)
register_post("/api/undo", recorder_undo_handler)
register_get("/api/recorder/retention", recorder_retention_get)
register_post("/api/recorder/retention", recorder_retention_post)

try:                          # leg 1: hub-live feed (server.py imports hermes_rpc
    import hermes_rpc         # function-locally, so we import it ourselves here)
    hermes_rpc.RECORDER_HOOK = recorder_ws_event
except Exception as _e:       # pragma: no cover
    _rec_log("hermes_rpc hook not wired: %r" % _e)

if not globals().get("_recorder_thread_started"):
    globals()["_recorder_thread_started"] = True
    try:
        threading.Thread(target=recorder_loop, daemon=True).start()
    except Exception as _e:                                  # pragma: no cover
        _rec_log("reconciler thread failed to start: %r" % _e)
