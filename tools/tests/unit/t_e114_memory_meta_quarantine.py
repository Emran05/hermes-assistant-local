#!/usr/bin/env python3
"""E114 (audit 2026-11-09): the memory provenance index is never silently
replaced by defaults.

Before: _mem_meta_edit() read memory-meta.json with read_json(), which folds
"absent", "unreadable" and "not an object" into the default — so a transient
read failure (EACCES/EMFILE) or a file holding valid non-object JSON was
overwritten with {v:1, files:{}, trash:{}} plus one mutation, and no
.corrupt-* copy was kept (C27's read_json_for_update exists for exactly this).

Execs the REAL aux_memory.py with server.py's REAL read_json / write_json /
read_json_for_update (+ its quarantine helpers) against a throwaway HOME.

Run:  python3 t_e114_memory_meta_quarantine.py
"""
import glob
import json
import os
import shutil
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  <- %r" % (name, extra))


sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real                          # noqa: E402

HOME = tempfile.mkdtemp(prefix="hermes-e114-")
os.makedirs(os.path.join(HOME, ".hermes", "memories"))
os.makedirs(os.path.join(HOME, ".hermes", "dashboard"))
try:
    g = {"__name__": "aux_memory_e114", "json": json, "os": os, "sys": sys,
         "time": time, "threading": threading, "HOME": HOME,
         "HERE": os.path.join(REPO, "dashboard"),
         "_state_lock": threading.Lock(), "_widget_cache": {},
         "register_get": lambda *a, **k: None, "register_post": lambda *a, **k: None}
    server_real.server_functions(("read_json", "write_json", "read_json_for_update",
                                  "_json_unparsable", "_quarantine_corrupt"), g)
    exec(compile(open(os.path.join(REPO, "dashboard", "aux_memory.py"),
                      encoding="utf-8").read(), "aux_memory.py", "exec"), g)
    META = g["MEM_META"]
    edit = g["_mem_meta_edit"]

    def add(m):
        m["files"]["new.md"] = {"created_by": "user"}

    # 1. valid JSON that is not an object: moved aside, not overwritten silently
    with open(META, "w") as f:
        json.dump([{"files": "the owner's data"}], f)
    edit(add)
    side = glob.glob(META + ".corrupt-*")
    check("THE FIX: a wrong-typed memory-meta.json is moved aside (.corrupt-*)",
          len(side) == 1, side)
    check("…with the original bytes in it",
          side and json.load(open(side[0])) == [{"files": "the owner's data"}])
    m = json.load(open(META))
    check("…and the edit lands on a fresh index",
          m.get("files", {}).get("new.md") == {"created_by": "user"}, m)
    for p in side:
        os.remove(p)

    # 2. unparsable: same
    with open(META, "w") as f:
        f.write('{"files": {"a.md": ')
    edit(add)
    check("an unparsable index is moved aside too",
          len(glob.glob(META + ".corrupt-*")) == 1)

    # 3. present but unreadable (EACCES): nothing is written, the error surfaces
    good = {"v": 1, "files": {"keep.md": {"created_by": "agent"}},
            "trash": {"x.md.1700000000.md": {"orig": "x.md"}}}
    with open(META, "w") as f:
        json.dump(good, f)
    if os.geteuid() != 0:
        os.chmod(META, 0)
        raised = False
        try:
            edit(add)
        except OSError:
            raised = True
        os.chmod(META, 0o600)
        check("THE FIX: an unreadable index raises instead of being replaced", raised)
        check("…and its content is untouched", json.load(open(META)) == good,
              json.load(open(META)))
    else:                                    # root reads through mode 000
        check("an unreadable index raises instead of being replaced (root: n/a)", True)
        check("…and its content is untouched (root: n/a)", True)

    # 4. a healthy index keeps everything and gains the edit
    edit(add)
    m = json.load(open(META))
    check("a healthy index keeps its entries",
          m["files"].get("keep.md") == {"created_by": "agent"}
          and "x.md.1700000000.md" in m["trash"] and "new.md" in m["files"], m)

    # 5. absent: starts from the default
    os.remove(META)
    edit(add)
    m = json.load(open(META))
    check("an absent index starts from the default",
          m.get("v") == 1 and m.get("trash") == {} and "new.md" in m["files"], m)
finally:
    shutil.rmtree(HOME, ignore_errors=True)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
