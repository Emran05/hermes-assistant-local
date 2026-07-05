# aux_memory.py — Editable Memory (P1.1).
#
# Self-contained CRUD over ~/.hermes/memories/*.md, exposed under /api/memory/*.
# exec'd into server.py's globals by the aux-module loader (after
# expanders_extra.py, before class Handler), so it may use these server.py
# globals: HOME, HERE, read_json, write_json, _state_lock, _widget_cache,
# register_get, register_post.  It imports ALL its own stdlib deps (exec'd code
# cannot rely on server.py's function-local imports) and defines only new names
# (_mem_*, _Mem*, MEM_*, CORE_FILES, ENTRY_DELIM) so it can clobber nothing.
#
# Safety story (mirrors ~/.hermes/hermes-agent/tools/memory_tool.py):
#   * core files (USER.md/MEMORY.md) serialize entries joined by "\n§\n",
#     byte-identical to the agent's _write_file, so the agent's drift detector
#     never treats a dashboard edit as tampering;
#   * per-file flock sidecar (same protocol as the agent), non-blocking with
#     bounded retries -> 423 rather than piling handler threads behind a writer;
#   * atomic temp+fsync+rename at 0o600 -> readers never see a partial file;
#   * every save/delete snapshots a pre-image and appends a recorder line, so
#     every mutation is undoable (the P1.2 flight-recorder contract);
#   * delete = move to dashboard trash (never os.remove); core delete refused.

import os
import re
import sys
import json
import time
import fcntl
import shutil
import hashlib
import tempfile
import contextlib

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
MEM_DIR      = os.path.join(HOME, ".hermes", "memories")
DASH_DIR     = os.path.join(HOME, ".hermes", "dashboard")
MEM_TRASH    = os.path.join(DASH_DIR, "memory-trash")
MEM_SNAP     = os.path.join(DASH_DIR, "snapshots", "memory")
MEM_META     = os.path.join(DASH_DIR, "memory-meta.json")
MEM_RECORDER = os.path.join(DASH_DIR, "recorder", "memory-edits.jsonl")
MEM_CONFIG   = os.path.join(HOME, ".hermes", "config.yaml")

ENTRY_DELIM  = "\n§\n"                 # == memory_tool.ENTRY_DELIMITER ("\n§\n")
CORE_FILES   = {"USER.md": ("user_char_limit", 1375),
                "MEMORY.md": ("memory_char_limit", 2200)}
MEM_NAME_RE  = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,62}\.md$")
MEM_TRASH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,62}\.md\.\d{10}\.md$")
MAX_FREEFORM = 131072          # 128 KB per freeform file
MAX_EDITABLE = 524288          # refuse to open >512 KB in the editor at all
MAX_FILES    = 500
TRASH_MAX_N  = 100             # prune oldest trashed copies beyond this
TRASH_MAX_B  = 20 * 1024 * 1024
SNAP_MAX_N   = 200             # interim GC until P1.2 owns snapshots
LOCK_TRIES   = 4              # 4 x 150 ms non-blocking flock attempts -> 423
LOCK_WAIT    = 0.15


class _MemBadName(Exception):
    pass


class _MemLocked(Exception):
    pass


# --------------------------------------------------------------------------
# module-load side effects (never let them take the hub down)
# --------------------------------------------------------------------------
for _d in (MEM_TRASH, MEM_SNAP, os.path.dirname(MEM_RECORDER)):
    try:
        os.makedirs(_d, mode=0o700, exist_ok=True)
    except Exception as _e:                               # pragma: no cover
        print("[aux_memory] mkdir %s failed: %s" % (_d, _e), file=sys.stderr)
try:                                                     # GC orphaned temps
    for _e in os.scandir(MEM_DIR):
        if _e.name.startswith(".dashmem_"):
            try:
                os.remove(_e.path)
            except OSError:
                pass
except OSError:
    pass


# --------------------------------------------------------------------------
# validation / paths
# --------------------------------------------------------------------------
def _mem_valid_name(name):
    if not isinstance(name, str) or not name:
        return False
    if ".." in name or "/" in name or "\\" in name:
        return False
    if name.endswith(".lock") or name.endswith(".tmp"):
        return False
    return bool(MEM_NAME_RE.match(name))


def _mem_path(name):
    """Validated absolute path inside MEM_DIR; refuses traversal + symlinks."""
    if not _mem_valid_name(name):
        raise _MemBadName(name)
    p = os.path.join(MEM_DIR, name)
    base = os.path.realpath(MEM_DIR)
    rp = os.path.realpath(p)
    if not (rp == base or rp.startswith(base + os.sep)):
        raise _MemBadName(name)
    if os.path.islink(p):
        raise _MemBadName(name)
    return p


def _mem_etag(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _mem_char_limit(name):
    """Core-file char budget: scan config.yaml (stdlib, no yaml dep), else default."""
    spec = CORE_FILES.get(name)
    if not spec:
        return MAX_FREEFORM
    key, default = spec
    try:
        with open(MEM_CONFIG, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^\s*(user_char_limit|memory_char_limit)\s*:\s*(\d+)", line)
                if m and m.group(1) == key:
                    return int(m.group(2))
    except OSError:
        pass
    return default


# --------------------------------------------------------------------------
# locking (mirrors the agent's sidecar-flock protocol, but non-blocking)
# --------------------------------------------------------------------------
@contextlib.contextmanager
def _mem_lock(name):
    lockpath = os.path.join(MEM_DIR, name + ".lock")
    fd = open(lockpath, "a+")
    got = False
    try:
        for i in range(LOCK_TRIES):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if i < LOCK_TRIES - 1:
                    time.sleep(LOCK_WAIT)
        if not got:
            raise _MemLocked(name)
        yield
    finally:
        if got:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            fd.close()
        except OSError:
            pass


def _mem_locked_probe(name):
    """True only if someone HOLDS the flock right now (presence of the sidecar
    means nothing).  Never creates a sidecar for a file that has none."""
    lockpath = os.path.join(MEM_DIR, name + ".lock")
    if not os.path.exists(lockpath):
        return False
    try:
        fd = open(lockpath, "a+")
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        try:
            fd.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# atomic write / snapshot / recorder / meta
# --------------------------------------------------------------------------
def _mem_atomic_write(path, raw):
    """temp+fchmod(600)+fsync+os.replace — the agent's _write_file discipline."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=MEM_DIR, prefix=".dashmem_")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _mem_gc(dirpath, max_n, max_b=None):
    try:
        stats = []
        for e in os.scandir(dirpath):
            if e.name.startswith(".") or not e.is_file(follow_symlinks=False):
                continue
            try:
                st = e.stat(follow_symlinks=False)
                stats.append((st.st_mtime, st.st_size, e.path))
            except OSError:
                pass
    except OSError:
        return
    stats.sort()                                    # oldest first
    while len(stats) > max_n:
        _, _, p = stats.pop(0)
        try:
            os.remove(p)
        except OSError:
            pass
    if max_b is not None:
        total = sum(s for _, s, _ in stats)
        while stats and total > max_b:
            _, sz, p = stats.pop(0)
            try:
                os.remove(p)
            except OSError:
                pass
            total -= sz


def _mem_snapshot(name, raw):
    """Write a pre-image under snapshots/memory/ (the undo guarantee); GC old."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    ts = int(time.time())
    snap = os.path.join(MEM_SNAP, "%s.%d.md" % (name, ts))
    n = 1
    while os.path.exists(snap):
        n += 1
        snap = os.path.join(MEM_SNAP, "%s.%d.%d.md" % (name, ts, n))
    _mem_atomic_write_dir(MEM_SNAP, snap, raw)
    _mem_gc(MEM_SNAP, SNAP_MAX_N)
    return snap


def _mem_atomic_write_dir(dirpath, path, raw):
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".snap_")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _mem_record(op, name, by, pre_etag, post_etag, snap, nbytes):
    """Append one JSONL line; a failure here is logged, never fails the request."""
    try:
        line = json.dumps({
            "ts": time.time(), "surface": "dashboard", "domain": "memory",
            "op": op, "file": name, "by": by,
            "pre_etag": pre_etag, "post_etag": post_etag,
            "pre_snapshot": snap, "bytes": nbytes,
        }, ensure_ascii=False)
        with open(MEM_RECORDER, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:                               # pragma: no cover
        print("[aux_memory] recorder write failed: %s" % e, file=sys.stderr)


def _mem_bust():
    try:
        _widget_cache.pop("mind_extra", None)
    except Exception:
        pass


def _mem_meta():
    m = read_json(MEM_META, {"v": 1, "files": {}, "trash": {}})
    if not isinstance(m, dict):
        m = {"v": 1, "files": {}, "trash": {}}
    return m


def _mem_meta_edit(mutate):
    """Read-modify-write the provenance index atomically under _state_lock."""
    with _state_lock:
        m = read_json(MEM_META, {"v": 1, "files": {}, "trash": {}})
        if not isinstance(m, dict):
            m = {}
        m.setdefault("v", 1)
        m.setdefault("files", {})
        m.setdefault("trash", {})
        mutate(m)
        write_json(MEM_META, m)
    return m


def _mem_writer(name, raw, mtime, meta):
    """Derive provenance at read time (never a stale stored flag)."""
    finfo = (meta.get("files") or {}).get(name)
    if finfo:
        created_by = finfo.get("created_by", "agent")
        lue = finfo.get("last_user_edit") or {}
        if lue.get("etag") and lue.get("etag") == _mem_etag(raw):
            return ("user", lue.get("at", mtime), created_by)
        return ("agent", mtime, created_by)
    return ("agent", mtime, "agent")


# --------------------------------------------------------------------------
# row / list builders
# --------------------------------------------------------------------------
def _mem_row(name, meta=None):
    if meta is None:
        meta = _mem_meta()
    path = os.path.join(MEM_DIR, name)
    st = os.stat(path)
    with open(path, "rb") as f:
        raw = f.read()
    core = name in CORE_FILES
    row = {"name": name, "kind": "entries" if core else "freeform", "core": core,
           "size": st.st_size, "mtime": st.st_mtime, "etag": _mem_etag(raw)}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    if core:
        ents = [s.strip() for s in text.split(ENTRY_DELIM) if s.strip()]
        row["entry_count"] = len(ents)
        row["char_used"] = len(text)
        row["char_limit"] = _mem_char_limit(name)
        row["preview"] = ents[0][:120] if ents else ""
    else:
        prev = ""
        for ln in text.splitlines():
            if ln.strip():
                prev = ln.strip()[:120]
                break
        row["preview"] = prev
    lw, lwat, cby = _mem_writer(name, raw, st.st_mtime, meta)
    row["last_writer"], row["last_writer_at"], row["created_by"] = lw, lwat, cby
    row["locked"] = _mem_locked_probe(name)
    return row


def _mem_trash_list(meta):
    out = []
    for tname, info in (meta.get("trash") or {}).items():
        if not os.path.isfile(os.path.join(MEM_TRASH, tname)):
            continue                                    # reconcile: file gone
        out.append({"trash_name": tname, "orig": info.get("orig", tname),
                    "deleted_at": info.get("deleted_at", 0),
                    "size": info.get("size", 0)})
    out.sort(key=lambda r: -r["deleted_at"])
    return out[:20]


# --------------------------------------------------------------------------
# handlers  (each returns dict -> 200, or (dict, status) tuple)
# --------------------------------------------------------------------------
def _mem_list_handler(ctx):
    try:
        try:
            os.makedirs(MEM_DIR, mode=0o700, exist_ok=True)
        except OSError:
            pass
        meta = _mem_meta()
        files = []
        try:
            entries = list(os.scandir(MEM_DIR))
        except OSError:
            entries = []
        for e in entries:
            name = e.name
            if name.startswith(".") or not name.endswith(".md"):
                continue
            try:
                if e.is_symlink() or not e.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            try:
                files.append(_mem_row(name, meta))
            except OSError:
                continue
        files.sort(key=lambda r: (0 if r["core"] else 1, -r["mtime"]))
        return {"ok": True, "dir": MEM_DIR, "files": files,
                "trash": _mem_trash_list(meta),
                "limits": {"max_file_bytes": MAX_FREEFORM, "max_files": MAX_FILES}}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


def _mem_file_handler(ctx):
    try:
        name = ctx.q1("name", "")
        if not _mem_valid_name(name):
            return ({"ok": False, "error": "bad_name"}, 400)
        try:
            path = _mem_path(name)
        except _MemBadName:
            return ({"ok": False, "error": "bad_name"}, 400)
        if not os.path.isfile(path):
            return ({"ok": False, "error": "missing"}, 404)
        st = os.stat(path)
        if st.st_size > MAX_EDITABLE:
            return ({"ok": False, "error": "too_big_to_edit", "size": st.st_size,
                     "hint": "This file is too large to edit here — open it in a text editor."}, 413)
        with open(path, "rb") as f:
            raw = f.read()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ({"ok": False, "error": "not_utf8",
                     "hint": "This file isn't valid UTF-8 text and can't be edited here."}, 422)
        core = name in CORE_FILES
        meta = _mem_meta()
        lw, lwat, cby = _mem_writer(name, raw, st.st_mtime, meta)
        out = {"ok": True, "name": name, "kind": "entries" if core else "freeform",
               "core": core, "content": content, "etag": _mem_etag(raw),
               "mtime": st.st_mtime, "size": st.st_size,
               "last_writer": lw, "last_writer_at": lwat, "created_by": cby}
        if core:
            out["entries"] = [s.strip() for s in content.split(ENTRY_DELIM) if s.strip()]
            out["char_used"] = len(content)
            out["char_limit"] = _mem_char_limit(name)
        return out
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


def _mem_build_payload(name, b):
    """Returns (raw_bytes, err_tuple).  err_tuple is None on success."""
    core = name in CORE_FILES
    if core:
        entries = b.get("entries")
        if not isinstance(entries, list):
            return (None, ({"ok": False, "error": "bad_entries"}, 400))
        ents = []
        for s in entries:
            if not isinstance(s, str):
                return (None, ({"ok": False, "error": "bad_entries"}, 400))
            s = s.strip()
            if not s:
                continue
            if s == "§" or ENTRY_DELIM in s:
                return (None, ({"ok": False, "error": "bad_entry",
                                "hint": "An entry can't contain the § delimiter."}, 400))
            ents.append(s)
        payload = ENTRY_DELIM.join(ents)
        limit = _mem_char_limit(name)
        if len(payload) > limit:
            return (None, ({"ok": False, "error": "over_limit",
                            "char_used": len(payload), "char_limit": limit}, 400))
        return (payload.encode("utf-8"), None)
    content = b.get("content")
    if not isinstance(content, str):
        return (None, ({"ok": False, "error": "bad_content"}, 400))
    raw = content.encode("utf-8")
    if len(raw) > MAX_FREEFORM:
        return (None, ({"ok": False, "error": "too_big",
                        "limit": MAX_FREEFORM}, 413))
    return (raw, None)


def _mem_create_handler(ctx):
    try:
        b = ctx.body or {}
        name = (b.get("name") or "").strip()
        if not _mem_valid_name(name):
            return ({"ok": False, "error": "bad_name"}, 400)
        try:
            path = _mem_path(name)
        except _MemBadName:
            return ({"ok": False, "error": "bad_name"}, 400)
        existing = []
        try:
            for e in os.scandir(MEM_DIR):
                if e.name.endswith(".md") and not e.name.startswith("."):
                    existing.append(e.name)
        except OSError:
            pass
        if len(existing) >= MAX_FILES:
            return ({"ok": False, "error": "too_many_files"}, 400)
        low = name.lower()
        if any(x.lower() == low for x in existing):
            return ({"ok": False, "error": "exists"}, 409)
        if "content" not in b:
            b = dict(b)
            b["content"] = ""
        raw, err = _mem_build_payload(name, b)
        if err is not None:
            return err
        etag = _mem_etag(raw)
        now = int(time.time())
        try:
            with _mem_lock(name):
                if os.path.exists(path):
                    return ({"ok": False, "error": "exists"}, 409)
                _mem_atomic_write(path, raw)

                def _mut(m):
                    m.setdefault("files", {})[name] = {
                        "created_by": "user", "created_at": now,
                        "last_user_edit": {"at": now, "etag": etag},
                        "ops": [{"op": "create", "at": now, "pre_etag": None,
                                 "post_etag": etag, "snapshot": None}]}
                _mem_meta_edit(_mut)
        except _MemLocked:
            return ({"ok": False, "error": "locked",
                     "hint": "Hermes is writing to this file — try again in a moment."}, 423)
        _mem_record("create", name, "user", None, etag, None, len(raw))
        _mem_bust()
        return {"ok": True, "file": _mem_row(name)}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


def _mem_save_handler(ctx):
    try:
        b = ctx.body or {}
        name = (b.get("name") or "").strip()
        base_etag = b.get("base_etag", "")
        core = name in CORE_FILES
        if not _mem_valid_name(name):
            return ({"ok": False, "error": "bad_name"}, 400)
        try:
            path = _mem_path(name)
        except _MemBadName:
            return ({"ok": False, "error": "bad_name"}, 400)
        # 1. build + validate payload (limits enforced BEFORE lock/etag)
        raw_new, err = _mem_build_payload(name, b)
        if err is not None:
            return err
        new_etag = _mem_etag(raw_new)
        # 2. lock  3. read+compare  4. snapshot  5. write  6. meta/record
        try:
            with _mem_lock(name):
                if not os.path.isfile(path):
                    return ({"ok": False, "error": "missing"}, 404)
                with open(path, "rb") as f:
                    cur = f.read()
                cur_etag = _mem_etag(cur)
                if cur_etag != base_etag:
                    try:
                        cur_text = cur.decode("utf-8")
                    except UnicodeDecodeError:
                        cur_text = cur.decode("utf-8", "replace")
                    cst = os.stat(path)
                    lw, _, _ = _mem_writer(name, cur, cst.st_mtime, _mem_meta())
                    current = {"content": cur_text, "etag": cur_etag,
                               "mtime": cst.st_mtime, "last_writer": lw}
                    if core:
                        current["entries"] = [s.strip() for s in cur_text.split(ENTRY_DELIM) if s.strip()]
                    return ({"ok": False, "error": "conflict", "current": current}, 409)
                snap = _mem_snapshot(name, cur)
                _mem_atomic_write(path, raw_new)
                st = os.stat(path)
                now = int(time.time())

                def _mut(m):
                    fm = m.setdefault("files", {}).setdefault(
                        name, {"created_by": "agent", "created_at": now, "ops": []})
                    fm["last_user_edit"] = {"at": now, "etag": new_etag}
                    ops = fm.setdefault("ops", [])
                    ops.append({"op": "save", "at": now, "pre_etag": cur_etag,
                                "post_etag": new_etag, "snapshot": snap})
                    if len(ops) > 20:
                        fm["ops"] = ops[-20:]
                _mem_meta_edit(_mut)
        except _MemLocked:
            return ({"ok": False, "error": "locked",
                     "hint": "Hermes is writing to this file — try again in a moment."}, 423)
        _mem_record("save", name, "user", cur_etag, new_etag, snap, len(raw_new))
        _mem_bust()
        resp = {"ok": True, "etag": new_etag, "mtime": st.st_mtime,
                "size": st.st_size, "last_writer": "user"}
        if core:
            resp["char_used"] = len(raw_new.decode("utf-8"))
        return resp
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


def _mem_delete_handler(ctx):
    try:
        b = ctx.body or {}
        name = (b.get("name") or "").strip()
        if not _mem_valid_name(name):
            return ({"ok": False, "error": "bad_name"}, 400)
        if name in CORE_FILES:
            return ({"ok": False, "error": "core_file",
                     "hint": "USER.md and MEMORY.md are Hermes's core memory and can be emptied but never deleted."}, 403)
        try:
            path = _mem_path(name)
        except _MemBadName:
            return ({"ok": False, "error": "bad_name"}, 400)
        try:
            with _mem_lock(name):
                if not os.path.isfile(path):
                    return ({"ok": False, "error": "missing"}, 404)
                st = os.stat(path)
                with open(path, "rb") as f:
                    raw = f.read()
                etag = _mem_etag(raw)
                epoch = int(time.time())
                tname = "%s.%d.md" % (name, epoch)
                while os.path.exists(os.path.join(MEM_TRASH, tname)):
                    epoch += 1                          # keep name regex-valid
                    tname = "%s.%d.md" % (name, epoch)
                dest = os.path.join(MEM_TRASH, tname)
                shutil.move(path, dest)

                def _mut(m):
                    m.setdefault("trash", {})[tname] = {
                        "orig": name, "deleted_at": epoch,
                        "size": st.st_size, "etag": etag}
                _mem_meta_edit(_mut)
        except _MemLocked:
            return ({"ok": False, "error": "locked",
                     "hint": "Hermes is writing to this file — try again in a moment."}, 423)
        _mem_record("delete", name, "user", etag, None, dest, st.st_size)
        _mem_bust()
        _mem_gc(MEM_TRASH, TRASH_MAX_N, TRASH_MAX_B)     # only hard-delete in the feature
        return {"ok": True, "trash_name": tname}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


def _mem_restore_handler(ctx):
    try:
        b = ctx.body or {}
        tname = (b.get("trash_name") or "").strip()
        if not MEM_TRASH_RE.match(tname):
            return ({"ok": False, "error": "bad_name"}, 400)
        src = os.path.join(MEM_TRASH, tname)
        base = os.path.realpath(MEM_TRASH)
        if not os.path.realpath(src).startswith(base + os.sep):
            return ({"ok": False, "error": "bad_name"}, 400)
        if not os.path.isfile(src):
            return ({"ok": False, "error": "missing"}, 404)
        meta = _mem_meta()
        info = (meta.get("trash") or {}).get(tname) or {}
        orig = info.get("orig")
        if not orig or not _mem_valid_name(orig):
            m = re.match(r"^(.*\.md)\.\d{10}\.md$", tname)
            orig = m.group(1) if m else None
        if not orig or not _mem_valid_name(orig):
            return ({"ok": False, "error": "bad_name"}, 400)
        existing = set()
        try:
            for e in os.scandir(MEM_DIR):
                if e.name.endswith(".md"):
                    existing.add(e.name.lower())
        except OSError:
            pass
        target = orig
        if target.lower() in existing:                  # agent recreated it
            stem = orig[:-3]                            # strip ".md"
            k, cand = 1, stem + "-restored.md"
            while cand.lower() in existing:
                k += 1
                cand = "%s-restored-%d.md" % (stem, k)
            target = cand
        try:
            dstpath = _mem_path(target)
        except _MemBadName:
            return ({"ok": False, "error": "bad_name"}, 400)
        try:
            with _mem_lock(target):
                if os.path.exists(dstpath):
                    return ({"ok": False, "error": "exists"}, 409)
                shutil.move(src, dstpath)
                raw = b""
                try:
                    with open(dstpath, "rb") as f:
                        raw = f.read()
                except OSError:
                    pass
                etag = _mem_etag(raw)
                now = int(time.time())

                def _mut(m):
                    m.setdefault("trash", {}).pop(tname, None)
                    fm = m.setdefault("files", {}).setdefault(
                        target, {"created_by": info.get("created_by", "user"),
                                 "created_at": now, "ops": []})
                    fm.setdefault("ops", []).append(
                        {"op": "restore", "at": now, "pre_etag": None,
                         "post_etag": etag, "snapshot": src})
                _mem_meta_edit(_mut)
        except _MemLocked:
            return ({"ok": False, "error": "locked",
                     "hint": "Hermes is writing to this file — try again in a moment."}, 423)
        _mem_record("restore", target, "user", None, etag, None, len(raw))
        _mem_bust()
        return {"ok": True, "name": target}
    except Exception as e:
        return ({"ok": False, "error": "internal: " + str(e)}, 500)


# --------------------------------------------------------------------------
# route registration (register_get/register_post live in server.py globals)
# --------------------------------------------------------------------------
register_get("/api/memory/list", _mem_list_handler)
register_get("/api/memory/file", _mem_file_handler)
register_post("/api/memory/create", _mem_create_handler)
register_post("/api/memory/save", _mem_save_handler)
register_post("/api/memory/delete", _mem_delete_handler)
register_post("/api/memory/restore", _mem_restore_handler)
