#!/usr/bin/env python3
"""Regression suite for the recorder's undo conflict check (A06).

Audit 2026-09-10 finding A06 ("Large-file undo skips conflict detection"):
`_after_state()` deliberately omits the sha256 above HASH_CAP_BYTES (32 MiB)
and keeps only existence/size/mtime, but `recorder_undo_handler()` checked the
HASH ONLY — so an edit the owner made to a large file after the agent wrote it
was restored over with `force: false` and no warning. Hash-read failures and
existence/type changes bypassed the check for the same reason.

This suite drives the REAL handler with the real `_after_state()` over a
throwaway directory. The audit's own probe is the first section: a sparse file
just over the threshold, its real after-state, a size change, and the handler
must now REFUSE without force and proceed with it. The checkpoint restore is
mocked exactly as the probe mocks it — nothing is ever restored, and the
owner's recorder.db is never opened (`_rec_init`/`_rec_conn` are stubs).

Definitions are lifted out of aux_recorder.py by AST rather than exec'ing the
module: aux_recorder starts its retention thread and needs server.py's globals
at import, and the audit probe uses the same loader.

Run:  python3 t_a06_undo_conflict.py [--repo /path/to/repo]
"""
import ast
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def section(t):
    print("\n== %s ==" % t)


# --------------------------------------------------------------------------
# locate the repo (never hardcode a home path — CI greps for those)
# --------------------------------------------------------------------------
def _find_repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    env = os.environ.get("HERMES_REPO")
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    if os.path.exists(os.path.join(cand, "dashboard", "server.py")):
        return cand
    return os.path.join(os.path.expanduser("~"), "HermesAssistant")


REPO = _find_repo()
REC = os.path.join(REPO, "dashboard", "aux_recorder.py")


def definitions(path, names, namespace):
    tree = ast.parse(open(path).read(), filename=os.path.basename(path))
    found = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    missing = set(names) - {n.name for n in found}
    if missing:
        raise SystemExit("SKIP: aux_recorder.py no longer defines %s" % sorted(missing))
    exec(compile(ast.Module(body=found, type_ignores=[]), path, "exec"), namespace)


def constant(path, name):
    for node in ast.parse(open(path).read()).body:
        if isinstance(node, ast.Assign):
            for i, t in enumerate(node.targets[0].elts
                                  if isinstance(node.targets[0], ast.Tuple)
                                  else node.targets):
                if isinstance(t, ast.Name) and t.id == name:
                    v = node.value
                    v = v.elts[i] if isinstance(v, ast.Tuple) else v
                    # the real one is `32 * 1024 * 1024` — an expression, not a
                    # literal, so evaluate it with no builtins in reach
                    return eval(compile(ast.Expression(body=v), REC, "eval"),
                                {"__builtins__": {}}, {})
    raise SystemExit("SKIP: aux_recorder.py no longer defines %s" % name)


CAP = constant(REC, "HASH_CAP_BYTES")
TMP = tempfile.mkdtemp(prefix="hermes-a06-")
RESTORES = []

NS = dict(os=os, json=json, time=time, shutil=shutil, hashlib=hashlib,
          HASH_CAP_BYTES=CAP, SUMMARY_CAP=500, UNDO_WHITELIST={"write"},
          _rec_init=lambda: None, _rec_lock=threading.Lock(),
          _rec_json=lambda s: json.loads(s) if s else {},
          _newest_prerollback=lambda wd: {},
          _finish_undo=lambda *a, **kw: None,
          _rec_log=lambda msg: None,
          _irreversible_detail=lambda kind: "n/a")
definitions(REC, ["_sha256_file", "_after_state", "recorder_undo_handler"], NS)


def _ckpt(op, **kwargs):
    RESTORES.append(kwargs)
    return {"success": True, "restored_to": "fake-commit"}


NS["_ckpt"] = _ckpt


def undo(target, after, force=False):
    """Run the real handler over one fake `write` row. -> (result, restores)."""
    row = dict(status="done", kind="write", reversible="yes", target=str(target),
               after_state=json.dumps(after), undo_note="", session="fake", ts=1,
               snapshot_ref=json.dumps({"workdir": TMP, "commit": "fake",
                                        "short": "fake"}))
    con = SimpleNamespace(
        execute=lambda *a: SimpleNamespace(fetchone=lambda: row),
        commit=lambda: None, close=lambda: None)
    NS["_rec_conn"] = lambda: con
    del RESTORES[:]
    res = NS["recorder_undo_handler"](
        SimpleNamespace(body={"id": 1, "force": force}))
    return res, list(RESTORES)


def newfile(name, data=b"hello"):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


# --------------------------------------------------------------------------
section("the audit probe: a changed file over the 32 MiB hash cap")
# --------------------------------------------------------------------------
big = os.path.join(TMP, "large-output.bin")
with open(big, "wb") as f:
    f.truncate(CAP + 1)                       # sparse: no large allocation
big_after = NS["_after_state"](big)
check("_after_state still omits the hash above the cap",
      big_after["sha256"] is None and big_after["exists"] is True, big_after)

res, calls = undo(big, big_after)
check("an unchanged large file still restores", res.get("ok") and len(calls) == 1,
      res)

with open(big, "ab") as f:
    f.write(b"new owner edit")
check("the file really changed size",
      os.stat(big).st_size != big_after["size"])

res, calls = undo(big, big_after)
check("a changed large file is REFUSED without force",
      res.get("ok") is False and res.get("conflict") is True and not calls, res)
check("the refusal names the size as the reason", res.get("reason") == "size",
      res.get("reason"))
check("the refusal carries details for the API/UI",
      bool(res.get("detail")) and res.get("target") == big and bool(res.get("hint")),
      res)

res, calls = undo(big, big_after, force=True)
check("force: true restores the changed large file",
      res.get("ok") is True and len(calls) == 1, res)

# --------------------------------------------------------------------------
section("mtime, existence and type changes")
# --------------------------------------------------------------------------
with open(big, "wb") as f:
    f.truncate(big_after["size"])             # same size again
os.utime(big, (time.time(), big_after["mtime"] + 5))
res, calls = undo(big, big_after)
check("same size but a newer mtime is a conflict",
      res.get("conflict") is True and res.get("reason") == "mtime" and not calls,
      res)
os.utime(big, (time.time(), big_after["mtime"]))
res, calls = undo(big, big_after)
check("restoring the recorded mtime clears the conflict", res.get("ok") is True,
      res)

gone = newfile("vanished.txt")
gone_after = NS["_after_state"](gone)
os.remove(gone)
res, calls = undo(gone, gone_after)
check("a target that no longer exists is a conflict",
      res.get("conflict") is True and res.get("reason") == "deleted" and not calls,
      res)

created = os.path.join(TMP, "was-removed.txt")
res, calls = undo(created, {"exists": False, "size": 0, "mtime": 0, "sha256": None})
check("agent-deleted, still absent: no conflict", res.get("ok") is True, res)
newfile("was-removed.txt", b"the owner made a new one")
res, calls = undo(created, {"exists": False, "size": 0, "mtime": 0, "sha256": None})
check("agent-deleted but re-created by the owner is a conflict",
      res.get("conflict") is True and res.get("reason") == "created" and not calls,
      res)

swap = newfile("swapped", b"file")
swap_after = NS["_after_state"](swap)
os.remove(swap)
os.mkdir(swap)
res, calls = undo(swap, swap_after)
check("a file replaced by a directory is a conflict",
      res.get("conflict") is True and res.get("reason") == "type" and not calls,
      res)

# --------------------------------------------------------------------------
section("hashed (small) files keep working")
# --------------------------------------------------------------------------
small = newfile("small.txt", b"agent wrote this")
small_after = NS["_after_state"](small)
check("_after_state hashes a small file", bool(small_after["sha256"]))
res, calls = undo(small, small_after)
check("an untouched small file restores", res.get("ok") is True and len(calls) == 1,
      res)
with open(small, "wb") as f:
    f.write(b"owner wrote this")             # same length, different bytes
res, calls = undo(small, small_after)
check("a small file with different contents is a conflict",
      res.get("conflict") is True and res.get("reason") == "hash" and not calls,
      res)
res, calls = undo(small, small_after, force=True)
check("force: true restores the changed small file", res.get("ok") is True, res)

# --------------------------------------------------------------------------
section("state we cannot establish counts as a conflict")
# --------------------------------------------------------------------------
if os.geteuid() == 0:
    print("  SKIP unreadable-file checks (running as root)")
else:
    locked = newfile("locked.txt", b"agent wrote this")
    locked_after = NS["_after_state"](locked)
    os.chmod(locked, 0o000)
    try:
        res, calls = undo(locked, locked_after)
        check("an unreadable file is a conflict, not an 'unchanged' pass",
              res.get("conflict") is True and res.get("reason") == "unreadable"
              and not calls, res)
    finally:
        os.chmod(locked, 0o600)

    hidden_dir = os.path.join(TMP, "nostat")
    os.mkdir(hidden_dir)
    hidden = os.path.join(hidden_dir, "big.bin")
    with open(hidden, "wb") as f:
        f.truncate(CAP + 1)
    hidden_after = NS["_after_state"](hidden)
    os.chmod(hidden_dir, 0o000)
    try:
        res, calls = undo(hidden, hidden_after)
        check("a stat we are not allowed to make is a conflict",
              res.get("conflict") is True and res.get("reason") == "unreadable"
              and not calls, res)
    finally:
        os.chmod(hidden_dir, 0o700)

# --------------------------------------------------------------------------
section("rows with no recorded after-state are unaffected")
# --------------------------------------------------------------------------
plain = newfile("shell-touched.txt", b"whatever")
res, calls = undo(plain, {})
check("an empty after_state (shell rows) still restores",
      res.get("ok") is True and len(calls) == 1, res)

# --------------------------------------------------------------------------
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
for f in FAILS:
    print("  FAILED: %s" % f)
try:
    shutil.rmtree(TMP)
except Exception:
    pass
sys.exit(1 if FAILS else 0)
