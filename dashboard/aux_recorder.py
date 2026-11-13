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
import stat as _rec_stat
import collections as _rec_collections
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
# Serialises undos against EACH OTHER only (C08). recorder_undo_handler used
# to hold _rec_lock across its checkpoint subprocesses (a restore + a list,
# up to 60 s each), and every recorder writer — the live hub-turn hook, the
# reconciler, recorder_record_local and so the Shortcuts bus — waited on
# it. _rec_lock is now taken only around the undo's own short DB writes.
_rec_undo_lock = threading.Lock()

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
    except sqlite3.OperationalError as e:
        # "database is locked", "unable to open database file", a disk I/O
        # hiccup: the file is NOT known to be corrupt, and OperationalError is a
        # DatabaseError subclass, so it used to fall into the branch below and
        # rename a healthy recorder.db aside — the whole flight log gone from
        # the UI over a transient lock (2026-10-21 audit C106). Leave it in
        # place; _rec_init() runs again on the next request/event and retries.
        _rec_init_error = "recorder.db unavailable (will retry): %r" % e
        _rec_log("recorder.db unavailable, will retry: %r" % e)
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


def _probe(workdir, commit, rel):
    # E01: what the snapshot says about ONE path, read straight from the
    # store with the store's own git environment (upstream _git_env: GIT_DIR
    # = the store, GIT_WORK_TREE = the project, no global/system config), so
    # check-ignore sees exactly what `git add -A` saw: the store's
    # info/exclude AND the project's own .gitignore files.
    import os as _os
    import re as _re
    import subprocess as _sp
    import unicodedata as _ud
    from tools import checkpoint_manager as _cm
    if not _re.fullmatch(r"[0-9a-fA-F]{4,64}", commit or ""):
        return {"ok": False, "error": "bad snapshot id"}
    parts = (rel or "").split("/")
    if not rel or rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
        return {"ok": False, "error": "path is not inside the snapshotted folder"}
    wd = str(_cm._normalize_path(workdir))
    env = _cm._git_env(_cm._store_path(_cm.CHECKPOINT_BASE), wd)

    def git(args, data=None):
        return _sp.run(["git"] + args, input=data, capture_output=True, env=env,
                       cwd=wd, timeout=60)

    t = git(["cat-file", "-t", commit])
    if t.returncode != 0 or t.stdout.strip() != b"commit":
        return {"ok": False, "error": "snapshot not found"}
    ls = git(["ls-tree", "-r", "-z", "--full-tree", commit])
    if ls.returncode != 0:
        return {"ok": False, "error": "could not list the snapshot"}
    blobs = {}
    for ent in ls.stdout.split(b"\0"):
        meta, tab, name = ent.partition(b"\t")
        if tab:
            blobs[name.decode("utf-8", "surrogateescape")] = meta.split()[-1]
    names = list(blobs)
    exact = rel in blobs

    def fold(s):
        return _ud.normalize("NFC", s).casefold()

    twin = "" if exact else next((n for n in names if fold(n) == fold(rel)), "")
    ci = git(["check-ignore", "-v", "-n", "-z", "--stdin", "--no-index"],
             rel.encode("utf-8", "surrogateescape") + b"\0")
    f = ci.stdout.split(b"\0")
    if ci.returncode not in (0, 1) or len(f) < 4:
        return {"ok": False, "error": "could not ask git whether the file is excluded"}
    src = f[0].decode("utf-8", "replace")
    pat = f[2].decode("utf-8", "replace")
    ignored = bool(src) and not pat.startswith("!")
    # check-ignore reads the .gitignore files as they are NOW. "Not ignored"
    # says something about the snapshot only if every .gitignore between the
    # folder and the file is byte-identical to the snapshot's own copy (a
    # line removed since would make a file the snapshot skipped look like
    # one it would have held).
    rules_changed = ""
    pre = ""
    for part in [""] + parts[:-1]:
        pre = (pre + part + "/") if part else pre
        gi = pre + ".gitignore"
        now = b""
        if _os.path.lexists(_os.path.join(wd, gi)):
            h = git(["hash-object", "--no-filters", "--", gi])
            now = h.stdout.strip() if h.returncode == 0 and h.stdout.strip() else b"?"
        if now != blobs.get(gi, b""):
            rules_changed = gi
            break
    return {"ok": True, "rel": rel, "in_snapshot": exact, "case_twin": twin,
            "ignored": ignored, "ignore_by": src if ignored else "",
            "ignore_pattern": pat if ignored else "",
            "rules_changed": rules_changed}


req = json.load(sys.stdin)
mgr = CheckpointManager(enabled=True)
op = req.get("op")
try:
    if op == "list":
        out = mgr.list_checkpoints(req["workdir"])
    elif op == "diff":
        out = mgr.diff(req["workdir"], req["commit"])
    elif op == "restore":
        if req.get("file"):
            # ONE file means that file: without this git reads the name as a
            # pathspec, and a file called "*.txt", "[id].tsx" or ":(glob)*"
            # restored every file its pattern matched (upstream _git_env
            # copies this environment)
            import os as _os
            _os.environ["GIT_LITERAL_PATHSPECS"] = "1"
        out = mgr.restore(req["workdir"], req["commit"], req.get("file") or None)
    elif op == "probe":
        out = _probe(req["workdir"], req["commit"], req.get("file") or "")
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
    except subprocess.TimeoutExpired:
        # Never str() this exception: it quotes the whole command line, which
        # is the driver's source. That put 5 KB of Python into the owner's
        # error line and — the source holds the words "not found" — made the
        # undo handler read every timed-out restore as "snapshot pruned" and
        # mark the row not undoable for good.
        return {"error": "checkpoint driver: timed out after 60 s"}
    except Exception as e:
        return {"error": "checkpoint driver: %s: %s" % (type(e).__name__, e)}


def _associate(workdir, ts, listings=None):
    """Newest checkpoint whose reason starts 'before ' with an ISO timestamp
    inside [ts-900, ts+60].  Returns a snapshot_ref dict or None.

    `listings` (a dict, workdir -> the driver's list answer) lets one pass ask
    once per workdir instead of once per row (C87)."""
    if not workdir:
        return None
    if listings is not None and workdir in listings:
        res = listings[workdir]
    else:
        res = _ckpt("list", workdir=workdir)
        if listings is not None:
            listings[workdir] = res
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
# E01 — "did the agent CREATE this file?" is decided from evidence
# --------------------------------------------------------------------------
# Hermes' checkpoint store never snapshots some files: its info/exclude list
# (.env, .env.*, *.log, build/, dist/, venv/ … — upstream
# tools/checkpoint_manager.py DEFAULT_EXCLUDES), anything the project's own
# .gitignore names (the store runs `git add -A` with the project as its work
# tree), and files over max_file_size_mb (default 10, pruned after staging).
# Undo used to read git's "pathspec did not match" — which only says "this
# path is not in the snapshot" — as "the agent created this file", and moved
# the owner's .env to the undo trash, where the sweep deleted it 14 days
# later. A file is now removed by Undo only when creation is PROVEN; anything
# else is refused and the file is left exactly where it is.
REC_NO_COPY = "Hermes has no earlier copy of this file, so it cannot be undone here"
_CKPT_MAX_FILE_MB = 10          # upstream CheckpointManager(max_file_size_mb=10)
# What Hermes' write_file leaves on a file it CREATES: it writes a `mktemp`
# file (0600) and renames it over the target, copying the mode of a target
# that already exists (upstream tools/file_operations.py _atomic_write;
# measured on the installed checkout 2026-11-10: new file 0600, an existing
# 0644 / 0755 file keeps its mode). So any other mode is evidence the file was
# there before the write — the one trace a file the snapshot skipped for its
# SIZE leaves behind. Re-verify on a Hermes upgrade: if a created file ever
# lands with another mode, Undo refuses every created file (it fails closed).
_REC_NEW_FILE_MODE = 0o600


def _ckpt_size_cap():
    """Bytes above which the store drops a file from a snapshot. config.yaml
    `checkpoints.max_file_size_mb` can lower it for the CLI; a higher value
    (or 0, "no cap") is NOT trusted upward — the serve surface builds its
    manager with the default — so a bigger file is simply never proven."""
    mb = _CKPT_MAX_FILE_MB
    try:
        with open(CONFIG_YAML, encoding="utf-8") as f:
            m = re.search(r"checkpoints:\s*\n(?:[ \t].*\n)*?[ \t]+max_file_size_mb:\s*(\d+)",
                          f.read())
        if m and 0 < int(m.group(1)) < mb:
            mb = int(m.group(1))
    except (OSError, ValueError):
        pass
    return mb * 1024 * 1024


def _rec_ignore_words(probe):
    pat = (probe or {}).get("ignore_pattern") or ""
    src = (probe or {}).get("ignore_by") or ""
    if not src or src.replace("\\", "/").endswith("info/exclude"):
        return ("Hermes' snapshots never include files matching %s"
                % (("'" + pat + "'") if pat else "this name"))
    return "the project's %s lists '%s', so snapshots skip it" % (os.path.basename(src), pat)


def _rec_created_proof(tool, after, probe, size_now, cap, touched_before,
                       mode_now=None, source=""):
    """PURE: (proven, why_not). Proven only when EVERY piece of evidence agrees
    that nothing was at this path when the snapshot was taken:
      * the action was a write_file (a patch, or anything else, edits a file
        that was already there — V4A "add file" patches are refused too);
      * the turn ran on a surface that takes snapshots (a Telegram / gateway
        turn never does: a snapshot found for its folder was taken up to 15
        minutes earlier for ANOTHER turn and says nothing about this write);
      * the agent left a regular file there, and one is there now;
      * the probe answered: the path is not in the snapshot, no name in the
        snapshot differs from it only in letter case / Unicode form, git says
        the store would NOT have skipped it (excluded or gitignored), and the
        .gitignore files it asked are the ones the snapshot was taken under;
      * it is not over the size cap, now or when the agent finished;
      * its permissions are the ones write_file gives a file it creates
        (_REC_NEW_FILE_MODE) — a file that was already there keeps its own;
      * nothing recorded touched this path before this write.
    The size rule judges the size the agent left; a file that was over the
    cap BEFORE and is small now is caught by its permissions or an earlier
    touch — unless it was itself 0600 and never touched (the one gap left)."""
    if tool != "write_file":
        if tool == "patch":
            return False, "a patch edits a file that was already there"
        return False, "only a file the agent wrote from nothing can be removed by Undo"
    src = (source or "").strip().lower()
    if src in _REC_GATEWAY_SOURCES:
        return False, ("%s turns are never snapshotted, so the snapshot found for this "
                       "folder was taken for another turn" % src.replace("_", " "))
    if not isinstance(after, dict) or not after.get("exists"):
        return False, "the agent left no file at this path"
    if size_now is None:
        return False, "there is no file at this path now"
    if not isinstance(probe, dict) or probe.get("ok") is not True:
        return False, "the snapshot could not be checked (%s)" % (
            (probe or {}).get("error") if isinstance(probe, dict) else "no answer")
    if probe.get("in_snapshot") is not False:
        return False, "the snapshot holds this file"
    if probe.get("case_twin"):
        return False, ("the snapshot holds %s, whose name differs only in letter case"
                       % probe["case_twin"])
    if probe.get("ignored") is True:
        return False, _rec_ignore_words(probe)
    if probe.get("ignored") is not False:
        return False, "it could not be checked whether snapshots skip this file"
    if probe.get("rules_changed") != "":
        return False, ("%s changed since the snapshot was taken, so it cannot be told "
                       "whether the snapshot skipped this file"
                       % (probe.get("rules_changed") or "a .gitignore"))
    mb = cap // (1024 * 1024)
    try:
        big = size_now > cap or int(after.get("size") or 0) > cap
    except (TypeError, ValueError):
        big = True
    if big:
        return False, "snapshots skip files over %d MB" % mb
    if mode_now != _REC_NEW_FILE_MODE:
        return False, ("its permissions (%s) are not the ones Hermes gives a file it "
                       "creates, so it was there before the agent wrote it"
                       % (oct(mode_now)[2:] if isinstance(mode_now, int) else "unknown"))
    if touched_before:
        return False, "it was used before this write, so it was already there"
    return True, ""


def _rec_touched_before(con, row, snap):
    """Did any recorded action touch this path before THIS write — up to the
    row's own time, or the snapshot's, whichever is later? (Evidence the file
    was already there; conservative: any read/write/undo row.) The snapshot's
    time alone is not enough: the snapshot a row is matched with can be up to
    15 minutes older than the write (another turn's, when this turn took
    none), and a read-then-rewrite of a file made in between would pass."""
    tgt = row.get("target") or ""
    if not tgt:
        return False
    cut = _iso_epoch((snap or {}).get("iso") or "")
    # the ISO stamp has whole seconds: anything in the snapshot's own second
    # counts as before it (the conservative side)
    try:
        row_ts = float(row.get("ts") or 0)
    except (TypeError, ValueError):
        return True                    # cannot tell -> treat as evidence it existed
    cut = max(row_ts, 0.0 if cut is None else cut + 1.0)
    names = {tgt, os.path.expanduser(tgt)}
    try:
        names.add(os.path.realpath(os.path.expanduser(tgt)))
    except Exception:
        pass
    names = sorted(names)
    try:
        r = con.execute(
            "SELECT 1 FROM actions WHERE id<>? AND ts<=? AND target IN (%s) "
            "AND tool IN ('read_file','write_file','patch','undo') LIMIT 1"
            % ",".join("?" * len(names)), (row.get("id") or -1, cut) + tuple(names)).fetchone()
    except Exception:
        return True                    # cannot tell -> treat as evidence it existed
    return bool(r)


def _rec_write_verdict(con, row, snap, after):
    """-> (verdict, detail, probe) for a write row with a snapshot:
      'in_snapshot' — restore it from the snapshot;
      'created'     — proven: Undo moves it to the undo trash;
      'refuse'      — not in the snapshot and creation not proven (detail says why);
      'unknown'     — the probe did not answer (transient; nothing is decided).
    Runs one checkpoint-driver subprocess; never call it under _rec_lock (C08)."""
    target = row.get("target") or ""
    workdir = (snap or {}).get("workdir") or ""
    tp = os.path.expanduser(target)
    try:
        rel = os.path.relpath(os.path.realpath(tp), os.path.realpath(workdir))
    except Exception:
        rel = ""
    if not rel or rel == "." or os.path.isabs(rel) or rel.split(os.sep)[0] == "..":
        return ("refuse", REC_NO_COPY + ": the file is outside the folder the snapshot covers",
                None)
    probe = _ckpt("probe", workdir=workdir, commit=(snap or {}).get("commit") or "",
                  file=rel.replace(os.sep, "/"))
    if not isinstance(probe, dict) or probe.get("ok") is not True:
        return ("unknown", (probe or {}).get("error") if isinstance(probe, dict)
                else "no answer", probe)
    if probe.get("in_snapshot") is True:
        return "in_snapshot", "", probe
    try:
        st = os.lstat(tp)
        size_now = st.st_size if _rec_stat.S_ISREG(st.st_mode) else None
        mode_now = _rec_stat.S_IMODE(st.st_mode)
    except OSError:
        size_now = mode_now = None
    proven, why = _rec_created_proof(row.get("tool"), after, probe, size_now,
                                     _ckpt_size_cap(), _rec_touched_before(con, row, snap),
                                     mode_now=mode_now, source=row.get("source"))
    if proven:
        return "created", "", probe
    return "refuse", REC_NO_COPY + ": " + why, probe


def _rec_write_hint(tcid=None, aid=None, snap=None, after=None):
    """Finalize-time verdict so the UI can say BEFORE the click what Undo
    would do. -> dict of column updates (possibly empty). The undo handler
    re-decides from scratch at click time; this is only the label."""
    if not snap:
        return {}
    try:
        con = _rec_conn()
        try:
            r = con.execute("SELECT id, tool, target, ts, kind, source FROM actions WHERE "
                            + ("tool_call_id=?" if tcid else "id=?"),
                            (tcid if tcid else aid,)).fetchone()
            row = dict(r) if r else None
            if not row or row["kind"] != "write" or not _looks_path(row["target"]):
                return {}
            verdict, detail, _p = _rec_write_verdict(con, row, snap, after or {})
        finally:
            con.close()
    except Exception as e:                                   # pragma: no cover
        _rec_log("write hint: %r" % e)
        return {}
    if verdict == "created":
        return {"snapshot_ref": json.dumps(dict(snap, undo_effect="trash"))}
    if verdict == "in_snapshot":
        return {"snapshot_ref": json.dumps(dict(snap, undo_effect="restore"))}
    if verdict == "refuse":
        return {"reversible": "no", "undo_note": detail}
    return {}


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
        # E01: what Undo would do, decided now (outside _rec_lock — a driver
        # subprocess) so the row can say it before anyone clicks
        hint = _rec_write_hint(tcid=tcid, snap=snap, after=after) \
            if (kind == "write" and snap) else {}
        snapj = hint.get("snapshot_ref") or (json.dumps(snap) if snap else "")
        note = ""
        if hint.get("reversible") == "no":
            rev, note = "no", hint.get("undo_note") or ""
        with _rec_lock:
            con = _rec_conn()
            try:
                con.execute(
                    "UPDATE actions SET after_state=?, snapshot_ref=?, reversible=?, "
                    "undo_note=CASE WHEN ?<>'' THEN ? ELSE undo_note END "
                    "WHERE tool_call_id=?",
                    (json.dumps(after) if after else "",
                     snapj, rev, note, note, tcid))
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
RECONCILE_PAGE = 500           # state.db rows read per reconcile tick
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
    # The cursor is (timestamp, message id), not the timestamp alone: with
    # "timestamp > cursor" a message sharing the cursor's timestamp that the
    # previous read did not reach (the LIMIT cut between equal timestamps, or
    # it landed after the read) was skipped for good (2026-10-08 audit, P3
    # Stores). A missing id cursor reads as 0, so the first pass after an
    # upgrade re-reads the cursor's own timestamp once — INSERT OR IGNORE on
    # tool_call_id makes that a no-op.
    try:
        cursor_id = int(_meta_get("statedb_cursor_id", "0") or 0)
    except Exception:
        cursor_id = 0
    # C91.1: state.db (Hermes's, read-only here — no index can be added) has
    # no messages(timestamp) index, so the cursor predicate alone full-scanned
    # messages every 5 s tick, linearly in the history.  `floor` is a rowid
    # lower bound: every message with id <= floor is either at/below the
    # cursor or not a tool row, so `m.id > floor` is a rowid RANGE scan over
    # what is new.  It is its own key, not statedb_cursor_id: that one is the
    # id of the newest-by-TIMESTAMP row, and a row with an older id but a
    # newer timestamp (an explicit timestamp, a LIMIT cut between them) would
    # fall under it unread.  The floor only advances to `hi` (MAX(id) read
    # BEFORE the page, which is bounded by it) when the page was not cut, so
    # nothing in (floor, hi] is left behind; and ids are AUTOINCREMENT, so
    # every later row lands above hi.  A MAX(id) below the floor means the
    # database was replaced (ids restarted): fall back to a full scan once.
    try:
        stored_floor = int(_meta_get("statedb_floor_id", "0") or 0)
    except Exception:
        stored_floor = 0
    floor = stored_floor
    try:
        uri = "file:" + urllib.parse.quote(STATE_DB) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error:
        return
    con.row_factory = sqlite3.Row
    try:
        hi = con.execute("SELECT MAX(id) FROM messages").fetchone()[0] or 0
        if hi < floor:
            floor = 0
        rows = con.execute(
            "SELECT m.id AS mid, m.role AS role, m.content AS content, "
            "m.tool_calls AS tc, m.tool_name AS tname, m.tool_call_id AS tcid, "
            "m.timestamp AS ts, s.source AS src, m.session_id AS sess "
            "FROM messages m JOIN sessions s ON m.session_id = s.id "
            "WHERE m.id > ? AND m.id <= ? "
            "AND (m.timestamp > ? OR (m.timestamp = ? AND m.id > ?)) "
            "AND (m.tool_calls IS NOT NULL OR m.tool_name IS NOT NULL) "
            "ORDER BY m.timestamp, m.id LIMIT ?",
            (floor, hi, cursor_f, cursor_f, cursor_id, RECONCILE_PAGE)).fetchall()
    except sqlite3.Error:
        con.close()
        return
    con.close()
    new_floor = hi if len(rows) < RECONCILE_PAGE else floor
    if not rows:
        if new_floor != stored_floor:
            _meta_set("statedb_floor_id", str(new_floor))
        return

    max_ts, max_id = cursor_f, cursor_id
    calls = []       # dicts for assistant tool_calls
    results = []     # (tcid, summary) for tool result rows
    for r in rows:
        ts = r["ts"] or 0
        if (ts, r["mid"] or 0) > (max_ts, max_id):
            max_ts, max_id = ts, r["mid"] or 0
        if r["tc"]:
            try:
                parsed = json.loads(r["tc"])
            except Exception:
                parsed = []
            for j, c in enumerate(parsed if isinstance(parsed, list) else []):
                if not isinstance(c, dict):
                    continue
                fn = c.get("function") or {}
                name = fn.get("name") or c.get("name") or "tool"
                arguments = fn.get("arguments")
                if arguments is None:
                    arguments = c.get("arguments") or c.get("input")
                # no id: key it by position too, or two same-named calls in
                # one message collapse into one row (10-08 P3). The first
                # keeps the old key, so nothing already recorded duplicates.
                cid = c.get("id") or ("sd-%s-%s" % (r["mid"], name) if j == 0
                                      else "sd-%s-%s-%d" % (r["mid"], name, j))
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
                    "WHERE tool_call_id=? AND status!='undone'", (summ, tcid))
            con.commit()
        finally:
            con.close()

    _meta_set("statedb_cursor", repr(max_ts))
    _meta_set("statedb_cursor_id", str(max_id))
    _meta_set("statedb_floor_id", str(new_floor))


# Per-row retry schedule for _finalize_pass (C87): row id -> (tries, next
# monotonic time it may be looked at again). It used to re-run the checkpoint
# driver (a Python + git subprocess) for EVERY unmatched write/shell row on
# EVERY 5 s tick until the row was 15 minutes old — ~180 spawns per row, and
# on a Mac whose dashboard/Telegram turns are never checkpointed (C114) that
# was every write the agent made. Now a row is retried on a widening
# schedule, a workdir is listed at most once per pass, and the 15-minute
# verdict still lands on time.
_FIN_BACKOFF_S = (15, 30, 60, 120, 240)
_fin_tries = {}


def _finalize_pass():
    """Attach snapshot + after_state to recent write/shell rows; after 15 min
    with none, mark them plainly non-undoable.  Bounded subprocess spawns."""
    now = time.time()
    mono = time.monotonic()
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
    live = {c["id"] for c in cands}
    for k in [k for k in _fin_tries if k not in live]:
        _fin_tries.pop(k, None)              # undone, matched or aged out
    listings = {}                            # workdir -> one list per pass
    for c in cands:
        final = c["ts"] < now - 900
        tried = _fin_tries.get(c["id"])
        if tried and not final and mono < tried[1]:
            continue                         # backed off (C87)
        cwd = _cwd_for_session(c["session"]) if c["kind"] == "shell" else ""
        wd = _row_workdir(c["kind"], c["target"], cwd)
        snap = _associate(wd, c["ts"], listings) if wd else None
        if snap or final:
            _fin_tries.pop(c["id"], None)
        else:
            n = (tried[0] if tried else 0) + 1
            _fin_tries[c["id"]] = (
                n, mono + _FIN_BACKOFF_S[min(n, len(_FIN_BACKOFF_S)) - 1])
        after = None
        if c["kind"] == "write" and _looks_path(c["target"]) and not c["after_state"]:
            after = _after_state(c["target"])
        sets, vals = [], []
        if snap:
            # E01: label what Undo would do (see _finalize_ws)
            hint = _rec_write_hint(aid=c["id"], snap=snap,
                                   after=after if after is not None
                                   else _rec_json(c["after_state"])) \
                if c["kind"] == "write" else {}
            if hint.get("reversible") == "no":
                sets += ["snapshot_ref=?", "reversible='no'", "undo_note=?"]
                vals += [json.dumps(snap), hint.get("undo_note") or REC_NO_COPY]
            else:
                sets += ["snapshot_ref=?", "reversible=?"]
                vals += [hint.get("snapshot_ref") or json.dumps(snap),
                         _reversible_for(c["kind"], True)]
        elif final:
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


_TRASH_STAMP_RE = re.compile(r"^(\d{9,11})-")


def _trashed_at(entry):
    """When `entry` was moved into the undo trash.

    `_to_trash()` names every entry "<unix-ts>-[<n>-]<name>", and that prefix is
    the only trustworthy clock: shutil.move is a rename, which keeps the file's
    ORIGINAL mtime, so an undone file last modified a month ago used to be
    hard-deleted by the next hourly sweep instead of 14 days later (audit
    2026-10-08 B14). A name without the prefix (hand-placed) falls back to the
    later of mtime and ctime — never earlier than the move could have been."""
    m = _TRASH_STAMP_RE.match(entry.name)
    if m:
        return float(m.group(1))
    st = entry.stat(follow_symlinks=False)
    return max(st.st_mtime, st.st_ctime)


def _gc_trash():
    cutoff = time.time() - UNDO_TRASH_TTL
    try:
        for e in os.scandir(UNDO_TRASH):
            try:
                if _trashed_at(e) < cutoff:
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
# which surfaces can take a snapshot at all (C114)
# --------------------------------------------------------------------------
# config.yaml's checkpoints.enabled reaches ONE surface: the classic terminal
# CLI. Every other surface builds its agents elsewhere:
#   * dashboard chats ("hub") run in `hermes serve`, whose in-process gateway
#     (tui_gateway.server._make_agent) passes
#     checkpoints_enabled=HERMES_TUI_CHECKPOINTS from its ENVIRONMENT only — so
#     it is the com.hermes.serve LaunchAgent's EnvironmentVariables that decide;
#   * the TUI takes them only when launched with --checkpoints;
#   * the messaging gateway (Telegram etc.) never passes the flag at all.
# So a green config key used to hide a recorder that could never offer an
# Undo for a dashboard or Telegram write. The banner and each row now say
# which of these is the reason.
SERVE_PLIST = os.path.join(HOME, "Library", "LaunchAgents", "com.hermes.serve.plist")
# gateway/config.py Platform members (minus "local"): sessions.source for a
# turn that came in over the messaging gateway.
_REC_GATEWAY_SOURCES = frozenset((
    "telegram", "discord", "whatsapp", "whatsapp_cloud", "slack", "signal",
    "mattermost", "matrix", "homeassistant", "email", "sms", "dingtalk",
    "api_server", "webhook", "msgraph_webhook", "feishu", "wecom",
    "wecom_callback", "weixin", "bluebubbles", "qqbot", "yuanbao", "relay"))


def _serve_checkpoints():
    """True / False from the serve LaunchAgent's environment; None = unknown
    (no plist, or unreadable)."""
    def build():
        try:
            import plistlib
            with open(SERVE_PLIST, "rb") as f:
                pl = plistlib.load(f)
        except Exception:
            return None
        env = pl.get("EnvironmentVariables") if isinstance(pl, dict) else None
        v = env.get("HERMES_TUI_CHECKPOINTS") if isinstance(env, dict) else None
        return str(v or "").strip().lower() in ("1", "true", "yes", "on")
    try:
        return _cached("recorder_serve_ckpt", 60, build)
    except Exception:
        return build()


def _no_snapshot_why(source):
    """Why a write/shell row from `source` cannot have a snapshot, or "" when
    nothing structural stops it (the CLI with the key on, the dashboard with
    the env set, or an unknown)."""
    src = (source or "").strip().lower()
    if src in ("hub", "dashboard"):
        if _serve_checkpoints() is False:
            return ("dashboard chats run in the agent service (hermes serve), "
                    "which takes snapshots only when started with "
                    "HERMES_TUI_CHECKPOINTS=1 — re-run ./install-services.sh")
        return ""
    if src == "cli":
        return ("" if _checkpoints_enabled() else
                "terminal sessions take snapshots only with checkpoints.enabled "
                "set in config.yaml")
    if src == "tui":
        return "the terminal UI takes snapshots only when started with --checkpoints"
    if src in _REC_GATEWAY_SOURCES:
        name = src.replace("_", " ")
        return ("%s turns are never snapshotted — the messaging gateway builds "
                "its agents with checkpoints off, so Undo is unavailable for them"
                % (name[:1].upper() + name[1:]))
    return ""


def _snapshot_coverage(con):
    """Per source: write/shell rows recorded, and how many ever had a snapshot.
    Evidence the banner can stand on when the LaunchAgent cannot be read."""
    out = {}
    for src, n, m in con.execute(
            "SELECT source, COUNT(*), SUM(CASE WHEN snapshot_ref<>'' THEN 1 ELSE 0 END) "
            "FROM actions WHERE kind IN ('write','shell') AND tool<>'undo' "
            "GROUP BY source"):
        out[src or ""] = {"rows": int(n or 0), "with_snapshot": int(m or 0)}
    return out


# --------------------------------------------------------------------------
# GET /api/recorder  (list + detail)
# --------------------------------------------------------------------------
def _row_public(r):
    out = {"id": r["id"], "ts": r["ts"], "source": r["source"], "tool": r["tool"],
           "target": r["target"], "kind": r["kind"], "reversible": r["reversible"],
           "status": r["status"], "duration_s": r["duration_s"],
           "summary": r["summary"], "has_snapshot": bool(r["snapshot_ref"]),
           "undone_ts": r["undone_ts"], "origin": r["origin"]}
    if r["kind"] in UNDO_WHITELIST and not r["snapshot_ref"] and r["tool"] != "undo":
        why = _no_snapshot_why(r["source"])
        if why:
            out["no_snapshot_why"] = why
    # E01: what Undo would do, so the UI can say it BEFORE the click —
    # undo_why on a row that cannot be undone, undo_effect on one that can:
    # "restore" (from the snapshot), "trash" (a file the agent created goes to
    # the undo trash) or "untrash" (an undo of that puts it back). Absent =
    # not yet checked; the handler decides at click time either way.
    try:
        note = r["undo_note"]
        after = _rec_json(r["after_state"])
    except (IndexError, KeyError):
        note, after = "", {}
    if (r["kind"] in UNDO_WHITELIST and r["reversible"] == "no" and note
            and r["status"] != "undone"):
        out["undo_why"] = note
    if r["kind"] == "write" and r["status"] == "done" and r["reversible"] == "yes":
        eff = ""
        ours = r["tool"] == "undo" and r["origin"] == "dashboard"
        if ours and after.get("undo_by") == "restore_from_trash":
            eff = "untrash"
        elif ours and after.get("undo_by") == "move_to_trash":
            eff = "trash"
        elif r["snapshot_ref"]:
            eff = _rec_json(r["snapshot_ref"]).get("undo_effect") or ""
        if eff in ("restore", "trash", "untrash"):
            out["undo_effect"] = eff
    return out


# E33: the detail's diff is a checkpoint-driver subprocess (Python + git).
# The Agent rail used to refetch an open pane on every 3 s poll, so it ran
# every 3 s for as long as a pane stayed open. Cached per row, keyed on the
# row's own state (an Undo or a finalizer verdict changes it), dropped by any
# Undo (which changes files on disk under every row of that folder), bounded,
# and short-lived so a file edited outside Hermes shows up within the TTL.
# Only successful diffs are kept; an error is retried on the next read.
# Three limits on that (review): (1) an entry holds only what the pane shows —
# the driver's answer is the WHOLE folder's diff, megabytes for a big change,
# and 32 of those lived in the dashboard process; (2) the key carries the
# target file's size and mtime, so an edit the owner makes to the row's own
# file is never hidden behind a cached picture; (3) only single-file (write)
# rows are cached — a shell row's Undo restores the whole folder, its diff is
# the one picture the owner decides on, and it is always read fresh.
_REC_DIFF_CACHE = _rec_collections.OrderedDict()   # key -> (monotonic, payload)
_REC_DIFF_MAX = 32
_REC_DIFF_TTL = 120
_REC_DIFF_SHOW, _REC_STAT_SHOW = 20480, 4096       # characters the detail pane gets
_rec_diff_lock = threading.Lock()


def _rec_diff_clear():
    with _rec_diff_lock:
        _REC_DIFF_CACHE.clear()


def _rec_diff_cached(r, snap):
    if r["kind"] != "write":
        return _ckpt("diff", workdir=snap["workdir"], commit=snap["commit"])
    try:
        st = os.stat(os.path.expanduser(r["target"] or ""))
        on_disk = (st.st_size, st.st_mtime_ns)
    except OSError:
        on_disk = None
    key = (r["id"], r["status"], r["undone_ts"], r["reversible"], r["snapshot_ref"],
           r["after_state"], on_disk)
    now = time.monotonic()
    with _rec_diff_lock:
        hit = _REC_DIFF_CACHE.get(key)
        if hit and now - hit[0] <= _REC_DIFF_TTL:
            _REC_DIFF_CACHE.move_to_end(key)
            return hit[1]
    d = _ckpt("diff", workdir=snap["workdir"], commit=snap["commit"])
    if isinstance(d, dict) and d.get("success"):
        d = {"success": True, "diff": str(d.get("diff") or "")[:_REC_DIFF_SHOW],
             "stat": str(d.get("stat") or "")[:_REC_STAT_SHOW]}
        with _rec_diff_lock:
            for k in [k for k in _REC_DIFF_CACHE if k[0] == r["id"]]:
                _REC_DIFF_CACHE.pop(k, None)          # older states of the same row
            _REC_DIFF_CACHE[key] = (now, d)
            while len(_REC_DIFF_CACHE) > _REC_DIFF_MAX:
                _REC_DIFF_CACHE.popitem(last=False)
    return d


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
        d = _rec_diff_cached(r, snap)
        if isinstance(d, dict) and d.get("success"):
            diff = (d.get("diff") or "")[:_REC_DIFF_SHOW]
            out["diff"] = diff
            out["diff_stat"] = (d.get("stat") or "")[:_REC_STAT_SHOW]
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
            coverage = _snapshot_coverage(con)
        finally:
            con.close()
        out = {"actions": [_row_public(x) for x in rows],
               "counts": {"total": total, "reversible": reversible, "undone": undone},
               "checkpoints_enabled": _checkpoints_enabled(),
               # C114: what each SURFACE can snapshot — the config key above
               # only ever reached the terminal CLI
               "snapshots": {"serve": _serve_checkpoints(),
                             "cli": _checkpoints_enabled(),
                             "by_source": coverage,
                             "gateway": sorted(k for k, v in coverage.items()
                                               if k in _REC_GATEWAY_SOURCES
                                               and v["rows"])},
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


def _from_trash(trash, target):
    """Put a file the undo trash holds back at `target` (E01: undo of a
    created-file undo). Never overwrites: a link (same volume) or an exclusive
    create (another volume) fails if anything appeared at `target`.
    Returns None on success, else the reason (nothing was changed)."""
    try:
        troot = os.path.realpath(UNDO_TRASH)
        if not trash or not os.path.realpath(trash).startswith(troot + os.sep):
            return "the undo trash entry is not recorded"
        st = os.lstat(trash)
    except OSError:
        return "the file is no longer in the undo trash (it is kept for %d days)" % (
            UNDO_TRASH_TTL // 86400)
    if not _rec_stat.S_ISREG(st.st_mode):
        return "the undo trash entry is not a plain file"
    tp = os.path.expanduser(target)
    if os.path.lexists(tp):
        return "something is at %s now; move it aside first" % target
    if not os.path.isdir(os.path.dirname(tp) or "/"):
        return "the folder %s no longer exists" % os.path.dirname(tp)
    try:
        os.link(trash, tp)
    except FileExistsError:
        return "something is at %s now; move it aside first" % target
    except OSError:
        made = False
        try:                      # another volume: copy with O_EXCL, then drop the trash copy
            with open(trash, "rb") as src:
                with open(tp, "xb") as dst:
                    made = True
                    shutil.copyfileobj(src, dst)
            shutil.copystat(trash, tp)
        except FileExistsError:
            return "something is at %s now; move it aside first" % target
        except OSError as e:
            if made:
                try:
                    os.unlink(tp)     # our own half-written copy, never the owner's
                except OSError:
                    pass
            return "could not put the file back: %s" % (e.strerror or e)
    try:
        os.unlink(trash)
    except OSError:
        pass
    return None


def _finish_undo(con, aid, row, note, snap_for_undo_row=None, scope="",
                 undo_after=None, undo_row_note=""):
    """Flip the row to undone and log the undo itself as its own action row.

    `undo_after` (E01) is the new row's after_state; for a trash move it also
    carries `undo_by` + the trash path, which is what makes that row undoable
    without any snapshot, and `undo_row_note` keeps the reconciler's finalizer
    from later marking it "no snapshot captured" (it skips rows with a note)."""
    now = time.time()
    con.execute("UPDATE actions SET status='undone', undone_ts=?, undo_note=? WHERE id=?",
                (now, note, aid))
    con.execute(
        "INSERT OR IGNORE INTO actions(tool_call_id, ts, session, source, tool, "
        "target, kind, reversible, status, summary, snapshot_ref, after_state, "
        "undo_note, origin) "
        "VALUES(?,?,?,?,?,?,?,?, 'done', ?, ?, ?, ?, 'dashboard')",
        ("undo-%d-%d" % (aid, int(now * 1000)), now, "", "dashboard", "undo",
         row["target"], "write", "yes",
         ("undo of #%d — %s" % (aid, note))[:SUMMARY_CAP],
         json.dumps(snap_for_undo_row) if snap_for_undo_row else "",
         json.dumps(undo_after) if undo_after else "",
         undo_row_note or "",
         ))
    con.commit()
    _rec_diff_clear()          # E33: every cached diff in that folder may be stale


def _rec_trash_created(con, aid, row, target):
    """Move a file the agent PROVABLY created to the undo trash (never delete)
    and log an undo row that can put it back. Caller holds _rec_undo_lock and
    has already proven creation (E01) and run the A06 conflict check."""
    tp = os.path.expanduser(target)
    try:
        if not _rec_stat.S_ISREG(os.lstat(tp).st_mode):
            return {"ok": False, "error": "not undoable",
                    "detail": "only a plain file can be moved to the undo trash"}
    except OSError:
        return {"ok": False, "error": "not undoable",
                "detail": "there is no file at %s now" % target}
    try:
        trash = _to_trash(target)
    except Exception as e:
        return {"ok": False, "error": "could not move file: " + str(e)}
    back = {"exists": False, "size": 0, "mtime": 0, "sha256": None,
            "undo_by": "restore_from_trash", "trash": trash}
    with _rec_lock:
        _finish_undo(con, aid, row, "created file moved to " + trash,
                     undo_after=back,
                     undo_row_note="no snapshot needed — Undo puts the file back from "
                                   "the undo trash")
    return {"ok": True, "restored_to": "(removed)", "file": target, "trash": trash,
            "note": "the agent had created this file — moved to the undo trash (kept "
                    "%d days, not deleted); undo this to put it back"
                    % (UNDO_TRASH_TTL // 86400)}


def recorder_undo_handler(ctx):
    try:
        _rec_init()
        body = ctx.body or {}
        try:
            aid = int(body.get("id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "unknown action"}
        # C59.2: bool("false") is True, so the string "false" overrode the
        # conflict check below.  Absent means no; anything but a JSON
        # boolean is refused before the row is even read.
        force = body.get("force", False)
        if not isinstance(force, bool):
            return ({"ok": False, "error": "'force' must be a JSON boolean "
                                           "(true or false)"}, 400)

        with _rec_undo_lock:            # NOT _rec_lock — see its comment (C08)
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
                        why = "" if row["snapshot_ref"] else _no_snapshot_why(row["source"])
                        if why:
                            detail += ": " + why
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

                # E01: an undo of a created-file undo needs no snapshot — its
                # row records where in the undo trash the file went, and the
                # undo of THAT puts it back there (it provably did not exist).
                undo_by = (after.get("undo_by") if row["tool"] == "undo"
                           and row.get("origin") == "dashboard" else "")
                if undo_by == "restore_from_trash":
                    why = _from_trash(after.get("trash") or "", target)
                    if why:
                        return {"ok": False, "error": "could not put the file back",
                                "detail": why}
                    back = dict(_after_state(target), undo_by="move_to_trash")
                    with _rec_lock:
                        _finish_undo(con, aid, row, "put back from the undo trash",
                                     undo_after=back,
                                     undo_row_note="no snapshot needed — Undo moves this "
                                                   "file to the undo trash again")
                    return {"ok": True, "restored_to": "(from the undo trash)",
                            "file": target,
                            "note": "the file is back where it was; undo this to move it "
                                    "to the undo trash again"}
                if undo_by == "move_to_trash":
                    return _rec_trash_created(con, aid, row, target)

                snap = _rec_json(row["snapshot_ref"])
                if not snap:                     # re-attempt association once
                    cwd = _cwd_for_session(row["session"]) if row["kind"] == "shell" else ""
                    wd = _row_workdir(row["kind"], target, cwd)
                    snap = _associate(wd, row["ts"]) if wd else None
                    if snap:
                        with _rec_lock:
                            con.execute("UPDATE actions SET snapshot_ref=? WHERE id=?",
                                        (json.dumps(snap), aid))
                            con.commit()
                if not snap:
                    return {"ok": False, "error": "no snapshot available"}

                workdir = snap.get("workdir") or ""
                commit = snap.get("commit") or ""

                if row["kind"] == "write" and target:
                    # E01: ask the snapshot about THIS path before touching
                    # anything. Only a path it holds is restored; one it does
                    # not hold is removed only when creation is PROVEN, and is
                    # otherwise refused and left exactly where it is.
                    verdict, detail, probe = _rec_write_verdict(con, row, snap, after)
                    if verdict == "created":
                        return _rec_trash_created(con, aid, row, target)
                    if verdict == "refuse":
                        with _rec_lock:          # deterministic: no Undo button next poll
                            con.execute("UPDATE actions SET reversible='no', undo_note=? "
                                        "WHERE id=? AND status!='undone'", (detail, aid))
                            con.commit()
                        return {"ok": False, "error": "not undoable", "detail": detail,
                                "target": target}
                    rel = (probe or {}).get("rel") if verdict == "in_snapshot" else ""
                    if not rel:
                        try:
                            rel = os.path.relpath(os.path.realpath(tp),
                                                  os.path.realpath(workdir))
                        except Exception:
                            rel = os.path.basename(tp)
                    res = _ckpt("restore", workdir=workdir, commit=commit, file=rel)
                    if isinstance(res, dict) and res.get("success"):
                        undo_snap = _newest_prerollback(workdir)
                        note = "restored to " + (res.get("restored_to") or snap.get("short", ""))
                        with _rec_lock:
                            _finish_undo(con, aid, row, note, snap_for_undo_row=undo_snap)
                        return {"ok": True, "restored_to": res.get("restored_to"),
                                "file": target,
                                "note": "pre-rollback snapshot taken — undo is itself undoable"}
                    if not isinstance(res, dict):
                        res = {"error": "restore failed"}
                    err = ((res.get("error") or "") + " " + (res.get("debug") or "")).lower()
                    if "did not match" in err or "pathspec" in err:
                        # Not in the snapshot, and (the probe did not answer)
                        # creation is unproven: refuse, touch nothing. This is
                        # the branch that used to move the file to the trash.
                        return {"ok": False, "error": "not undoable",
                                "detail": REC_NO_COPY + " (the snapshot could not be "
                                          "checked: %s)" % (detail or "no answer"),
                                "target": target}
                    if "not found" in err or "no checkpoints" in err:
                        with _rec_lock:
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
                    with _rec_lock:
                        _finish_undo(con, aid, row, note, snap_for_undo_row=undo_snap,
                                     scope="directory")
                    return {"ok": True, "restored_to": res.get("restored_to"),
                            "scope": "directory", "directory": workdir,
                            "note": "restored every file in the directory to turn start; "
                                    "command side effects are NOT reversed"}
                err = (res.get("error") or "").lower()
                if "not found" in err or "no checkpoints" in err:
                    with _rec_lock:
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
