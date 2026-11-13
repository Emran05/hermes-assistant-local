#!/usr/bin/env python3
"""B15 (audit 2026-10-08): the memory editor's trash.

Three defects, each driven through the REAL handlers (aux_memory.py, then
aux_youmodel.py, exec'd into one fake-server globals dict in load order,
against a throwaway HOME):

  1. a just-deleted file with an OLD mtime was hard-deleted by the GC in the
     same request — shutil.move keeps the original mtime and _mem_gc sorted
     the trash by st_mtime, so the copy the delete had just made was "oldest";
  2. a deleted people/ card could not be restored: its trash name carries the
     "people/" prefix and the restore regex refused it (400);
  3. memory-trash/people/ was never garbage-collected at all.

Run:  python3 t_b15_memory_trash.py [--repo /path/to/repo]
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
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
DASH = os.path.join(REPO, "dashboard")
TMP = tempfile.mkdtemp(prefix="hermes-b15-")
HOME = os.path.join(TMP, "home")
os.makedirs(os.path.join(HOME, ".hermes", "memories"))


class Ctx(object):
    def __init__(self, body=None, query=None):
        self.body = body or {}
        self.query = query or {}

    def q1(self, k, default=""):
        v = self.query.get(k)
        return v[0] if v else default


def read_json(p, default):
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)


G = {"__name__": "aux_memory_b15", "HOME": HOME, "HERE": DASH,
     "read_json": read_json, "write_json": write_json,
     "_state_lock": threading.RLock(), "_widget_cache": {},
     "register_get": lambda p, f: None, "register_post": lambda p, f: None,
     "RouteCtx": Ctx}

# E114: aux_memory's index edit reads through server.py's REAL
# read_json_for_update (C27), exec'd here with its two helpers.
sys.path.insert(0, os.path.join(REPO, "tools", "tests", "lib"))
import server_real  # noqa: E402
G.update({"json": json, "os": os, "time": time, "sys": sys})
server_real.server_functions(("read_json_for_update", "_json_unparsable",
                              "_quarantine_corrupt"), G)
for fn in ("aux_memory.py", "aux_youmodel.py"):
    path = os.path.join(DASH, fn)
    exec(compile(io.open(path, encoding="utf-8").read(), path, "exec"), G)

MEM_DIR, TRASH = G["MEM_DIR"], G["MEM_TRASH"]


def body(res):
    return res[0] if isinstance(res, tuple) else res


def status(res):
    return res[1] if isinstance(res, tuple) else 200


def write_mem(name, text, age_days=0):
    p = os.path.join(MEM_DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))
    return p


def fill_trash(sub, n):
    """n older trashed copies, each named with a deletion stamp 1..n days ago
    but with a FRESH mtime — the opposite of what the bug relied on."""
    d = os.path.join(TRASH, sub) if sub else TRASH
    out = []
    for i in range(n):
        stamp = int(time.time()) - (n - i) * 86400
        p = os.path.join(d, "filler%d.md.%d.md" % (i, stamp))
        with open(p, "w") as f:
            f.write("x")
        out.append(p)
    return out


print("\n== 1. deleting an old file at the trash cap keeps the copy just made ==")
G["TRASH_MAX_N"] = 3                 # read at call time by the delete handler
fillers = fill_trash("", 3)
write_mem("old-notes.md", "a note from last year", age_days=400)
r = G["_mem_delete_handler"](Ctx(body={"name": "old-notes.md"}))
check("delete ok", body(r).get("ok") is True, r)
tname = body(r).get("trash_name", "")
check("the just-trashed copy survived the same request's GC",
      os.path.isfile(os.path.join(TRASH, tname)), sorted(os.listdir(TRASH)))
check("the cap still holds (oldest by DELETION time went instead)",
      not os.path.exists(fillers[0]) and os.path.exists(fillers[-1]),
      sorted(os.listdir(TRASH)))
r = G["_mem_restore_handler"](Ctx(body={"trash_name": tname}))
check("and it can be restored", body(r).get("ok") is True and
      os.path.isfile(os.path.join(MEM_DIR, "old-notes.md")), r)

print("\n== 2. a deleted people/ card can be restored ==")
r = G["_mem_create_handler"](Ctx(body={"name": "people/jane.md",
                                       "content": "Jane — met at the meetup"}))
check("card created", body(r).get("ok") is True, r)
r = G["_mem_delete_handler"](Ctx(body={"name": "people/jane.md"}))
check("card deleted", body(r).get("ok") is True, r)
ptname = body(r).get("trash_name", "")
listed = [t["trash_name"] for t in body(G["_mem_list_handler"](Ctx()))["trash"]]
check("the trash list offers it", ptname in listed, listed)
r = G["_mem_restore_handler"](Ctx(body={"trash_name": ptname}))
check("restoring it is not a 400", status(r) != 400, r)
check("restore ok, back under people/", body(r).get("ok") is True and
      body(r).get("name") == "people/jane.md" and
      os.path.isfile(os.path.join(MEM_DIR, "people", "jane.md")), r)
G["_mem_delete_handler"](Ctx(body={"name": "people/jane.md"}))
write_mem("people/jane.md", "the agent recreated her card")
ptname2 = [t["trash_name"] for t in body(G["_mem_list_handler"](Ctx()))["trash"]
           if t["orig"] == "people/jane.md"][0]
r = G["_mem_restore_handler"](Ctx(body={"trash_name": ptname2}))
check("a name clash under people/ restores beside it, never over it",
      body(r).get("ok") is True and body(r).get("name") == "people/jane-restored.md"
      and open(os.path.join(MEM_DIR, "people", "jane.md")).read()
      == "the agent recreated her card", r)
r = G["_mem_restore_handler"](Ctx(body={"trash_name": "people/../x.md.1700000000.md"}))
check("traversal in a people/ trash name is still refused", status(r) == 400, r)

print("\n== 3. memory-trash/people/ is garbage-collected too ==")
pfill = fill_trash("people", 4)
write_mem("people/bob.md", "Bob")
r = G["_mem_delete_handler"](Ctx(body={"name": "people/bob.md"}))
pool = []
for d in (TRASH, os.path.join(TRASH, "people")):
    pool += [x for x in os.listdir(d) if os.path.isfile(os.path.join(d, x))]
check("the trash (top level + people/) is held to the cap",
      len(pool) <= G["TRASH_MAX_N"], sorted(pool))
check("the oldest people/ copies went first", not os.path.exists(pfill[0]),
      sorted(pool))
check("bob's fresh copy is kept",
      os.path.isfile(os.path.join(TRASH, body(r).get("trash_name", "-"))), r)

print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
