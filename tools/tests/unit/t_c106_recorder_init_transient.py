#!/usr/bin/env python3
"""C106 (audit 2026-10-21): a transient sqlite error at recorder init does not
rename a healthy recorder.db aside.

`_rec_init()` caught sqlite3.DatabaseError as "corrupt" and renamed the file to
recorder.db.corrupt-<ts> — but OperationalError ("database is locked",
"unable to open database file", disk I/O) is a DatabaseError subclass, so one
lock at the wrong moment moved the whole flight log out of the UI. Genuine
corruption ("file is not a database", "malformed") is a plain DatabaseError
and must still be recovered by renaming aside.

Drives the REAL _rec_init / _rec_chmod (lifted by AST, the t_a06 loader) over
a throwaway directory. The "locked" case is a real sqlite3.OperationalError
raised by the first connect, as a busy database raises it.
"""
import ast
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import types

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


REC = os.path.join(_find_repo(), "dashboard", "aux_recorder.py")
tree = ast.parse(open(REC).read())
body = [n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name in ("_rec_init", "_rec_chmod"))
        or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
            ("_SCHEMA", "_REV_MIGRATION", "_RECLASSIFIED", "READONLY_KINDS",
             "TOOL_KIND") for t in n.targets))]
TMP = tempfile.mkdtemp(prefix="hermes-c106-")


def load(db):
    """A fresh namespace per case: _rec_inited is module state."""
    fails_left = [0]

    def connect(*a, **kw):
        if fails_left[0]:
            fails_left[0] -= 1
            raise sqlite3.OperationalError("database is locked")
        return sqlite3.connect(*a, **kw)

    fake = types.SimpleNamespace(connect=connect, DatabaseError=sqlite3.DatabaseError,
                                 OperationalError=sqlite3.OperationalError)
    ns = dict(os=os, time=time, sqlite3=fake, DATA=os.path.dirname(db),
              UNDO_TRASH=os.path.join(os.path.dirname(db), "undo-trash"),
              REC_DB=db, _rec_inited=False, _rec_init_error="",
              _rec_log=lambda m: None)
    exec(compile(ast.Module(body=body, type_ignores=[]), REC, "exec"), ns)
    return ns, fails_left


def asides(d):
    return [x for x in os.listdir(d) if ".corrupt-" in x]


print("\n== a locked (healthy) recorder.db is left where it is ==")
d1 = os.path.join(TMP, "a")
os.makedirs(d1)
db1 = os.path.join(d1, "recorder.db")
ns, fails_left = load(db1)
ns["_rec_init"]()                                   # creates a healthy store
c = sqlite3.connect(db1)
c.execute("INSERT INTO actions(ts, tool, kind, reversible, status) "
          "VALUES (1, 'write_file', 'write', 'yes', 'done')")
c.commit()
c.close()
ns, fails_left = load(db1)                          # a fresh process
fails_left[0] = 1                                   # the first open hits a lock
ns["_rec_init"]()
check("no .corrupt-* rename on a transient OperationalError", not asides(d1),
      os.listdir(d1))
check("init reports it and will retry", ns["_rec_inited"] is False and
      "retry" in (ns["_rec_init_error"] or ""), ns["_rec_init_error"])
ns["_rec_init"]()                                   # the lock is gone
check("the retry succeeds", ns["_rec_inited"] is True, ns["_rec_init_error"])
n = sqlite3.connect(db1).execute("SELECT COUNT(*) FROM actions").fetchone()[0]
check("and the recorded history is still there", n == 1, n)

print("\n== a genuinely corrupt file is still renamed aside and recreated ==")
d2 = os.path.join(TMP, "b")
os.makedirs(d2)
db2 = os.path.join(d2, "recorder.db")
with open(db2, "wb") as f:
    f.write(b"not a sqlite database " * 64)
ns, _ = load(db2)
ns["_rec_init"]()
check("corrupt file moved aside", len(asides(d2)) == 1, os.listdir(d2))
check("a fresh store took its place", ns["_rec_inited"] is True and
      sqlite3.connect(db2).execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
