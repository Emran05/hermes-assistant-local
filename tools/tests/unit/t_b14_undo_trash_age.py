#!/usr/bin/env python3
"""B14 (audit 2026-10-08): the recorder's undo trash is aged by WHEN the file
was trashed, not by the file's own mtime.

`_to_trash()` moves an agent-created file aside with shutil.move — a rename,
which keeps the file's ORIGINAL mtime — and `_gc_trash()` used to purge by
st_mtime. So undoing the creation of a file the agent wrote a month ago put it
in the "recoverable for 14 days" trash and the next hourly sweep hard-deleted
it. This drives the REAL `_to_trash` and `_gc_trash` (lifted out of
aux_recorder.py by AST, the loader t_a06 uses) over a throwaway directory.

Run:  python3 t_b14_undo_trash_age.py [--repo /path/to/repo]
"""
import ast
import os
import re
import shutil
import sys
import tempfile
import time

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


def _find_repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    env = os.environ.get("HERMES_REPO")
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


REPO = _find_repo()
REC = os.path.join(REPO, "dashboard", "aux_recorder.py")
TMP = tempfile.mkdtemp(prefix="hermes-b14-")
TRASH = os.path.join(TMP, "undo-trash")
os.makedirs(TRASH)
TTL = 14 * 86400

tree = ast.parse(open(REC).read(), filename="aux_recorder.py")
want_fns = {"_to_trash", "_gc_trash", "_trashed_at"}
body = []
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name in want_fns:
        body.append(n)
    elif isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.startswith("_TRASH") for t in n.targets):
        body.append(n)
names = {n.name for n in body if isinstance(n, ast.FunctionDef)}
if not {"_to_trash", "_gc_trash"} <= names:
    print("SKIP: aux_recorder.py no longer defines _to_trash/_gc_trash")
    sys.exit(0)
NS = dict(os=os, re=re, time=time, shutil=shutil,
          UNDO_TRASH=TRASH, UNDO_TRASH_TTL=TTL)
exec(compile(ast.Module(body=body, type_ignores=[]), REC, "exec"), NS)


def make(name, age_days):
    p = os.path.join(TMP, name)
    with open(p, "w") as f:
        f.write("written by the agent")
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    return p


print("\n== an undone file with an OLD mtime survives the next sweep ==")
old = make("month-old.txt", 30)
dest = NS["_to_trash"](old)
check("_to_trash moved it into the trash", os.path.exists(dest) and not os.path.exists(old))
check("the move kept the original mtime (the trap)",
      os.stat(dest).st_mtime < time.time() - 20 * 86400)
NS["_gc_trash"]()
check("a file trashed just now is NOT purged because its mtime is old",
      os.path.exists(dest), os.listdir(TRASH))

print("\n== the 14-day window is still enforced, by the trashed-at stamp ==")
expired = os.path.join(TRASH, "%d-expired.txt" % int(time.time() - TTL - 3600))
with open(expired, "w") as f:
    f.write("trashed 15 days ago")          # mtime = now: must not save it
fresh_dir = os.path.join(TRASH, "%d-somedir" % int(time.time() - 3600))
os.makedirs(fresh_dir)
old_dir = os.path.join(TRASH, "%d-2-olddir" % int(time.time() - TTL - 60))
os.makedirs(os.path.join(old_dir, "inner"))
NS["_gc_trash"]()
check("an entry trashed more than 14 days ago is purged (even with a new mtime)",
      not os.path.exists(expired), os.listdir(TRASH))
check("an expired directory entry (collision-suffixed name) is purged",
      not os.path.exists(old_dir), os.listdir(TRASH))
check("a directory trashed an hour ago is kept", os.path.isdir(fresh_dir))
check("and the month-old file trashed today is still there", os.path.exists(dest))

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
