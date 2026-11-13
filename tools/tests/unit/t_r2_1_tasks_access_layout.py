#!/usr/bin/env python3
"""Round-2 regression test for R2-1 (2026-10-08 audit P3 prose, HTTP /
persistence; k04a review #6): /api/tasks, /api/access and the layout reader.

BEFORE:
  * /api/tasks and /api/access read the whole store, changed it and wrote it
    back with only the WRITE under _state_lock — the A01/A03 lost update: two
    concurrent adds kept one.
  * task ids were f"{time.time():.0f}-{len(tasks)}", which repeats after a
    delete and an add in the same second, so toggle/delete hit two tasks.
  * get_tasks()/get_access()/_layout_read() used read_json(), so a transient
    EMFILE read as "empty" and the next write replaced a healthy file
    (write_json only protects a target that fails to PARSE).

AFTER: tasks_update()/access_update() read with read_json_for_update and
write under _state_lock; ids are os.urandom(6).hex(); _layout_read uses
read_json_for_update — an unreadable store answers 500 and nothing is written.

REAL Handler + store functions, ast-extracted from dashboard/server.py, on an
ephemeral loopback port; temp dirs. A read of the store is slowed (to widen
the old race) and can fail ONCE with EMFILE through an `open` shadowed in the
extracted namespace. HERMES_T_SERVER_SRC=<old server.py> shows it failing.
"""
import builtins
import errno
import io
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _http_harness as H  # noqa: E402

check = H.check


class Faults:
    """`open` for the namespace: slows reads of the stores and can raise EMFILE
    once for one path (a transient fd-pressure moment)."""

    def __init__(self):
        self.slow = set()
        self.emfile_once = {}          # path -> how many reads fail

    def open(self, path, mode="r", *a, **kw):
        p = os.path.abspath(str(path))
        if "r" in mode and "+" not in mode:
            if self.emfile_once.get(p):
                self.emfile_once[p] -= 1
                raise OSError(errno.EMFILE, "Too many open files", p)
            if p in self.slow:
                f = builtins.open(path, mode, *a, **kw)
                data = f.read()
                f.close()
                time.sleep(0.05)
                return io.BytesIO(data) if "b" in mode else io.StringIO(data)
        return builtins.open(path, mode, *a, **kw)


def namespace(d, faults):
    data = os.path.join(d, "data")
    os.makedirs(data)
    ns = H.http_namespace(
        d, DATA=data, _state_lock=threading.Lock(),
        TASKS_FILE=os.path.join(data, "tasks.json"),
        ACCESS_FILE=os.path.join(data, "access.json"),
        LAYOUT_FILE=os.path.join(data, "layout.json"),
        WIDGETS={"clock": {}, "weather": {}, "tasks": {}, "notes": {}})
    H.extract(["read_json", "write_json", "get_tasks", "save_tasks",
               "get_access", "_layout_ids", "_layout_read", "_layout_merge",
               "get_layout", "layout_update", "DEFAULT_LAYOUT",
               "LAYOUT_LEGACY_INJECTED"], ns,
              optional=["_json_unparsable", "_quarantine_corrupt",
                        "read_json_for_update", "tasks_update", "access_update",
                        "_STORE_VIEW_LOG_GAP", "_store_view_logged",
                        "_log_store_view"])
    ns["open"] = faults.open
    return ns


def post(port, path, body):
    st, _, raw = H.req(port, "POST", path, json.dumps(body).encode())
    return st, H.as_json(raw) or {}


def burst(port, path, bodies):
    out = [None] * len(bodies)

    def go(i, b):
        out[i] = post(port, path, b)
    ts = [threading.Thread(target=go, args=(i, b)) for i, b in enumerate(bodies)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(15)
    return out


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-r2-1-") as d:
        faults = Faults()
        ns = namespace(d, faults)
        tasks_f, access_f, layout_f = (ns["TASKS_FILE"], ns["ACCESS_FILE"],
                                       ns["LAYOUT_FILE"])
        srv = H.Server(ns["Handler"])
        port = srv.port
        log = io.StringIO()
        try:
            with redirect_stderr(log):
                print("\n1. concurrent /api/tasks adds all land")
                faults.slow = {tasks_f}
                res = burst(port, "/api/tasks",
                            [{"op": "add", "text": "task %d" % i} for i in range(8)])
                faults.slow = set()
                check("every add answered 200",
                      all(r and r[0] == 200 for r in res), res)
                on_disk = json.load(builtins.open(tasks_f))["tasks"]
                check("THE FIX: all 8 concurrent adds are on disk",
                      sorted(t["text"] for t in on_disk)
                      == sorted("task %d" % i for i in range(8)),
                      [t["text"] for t in on_disk])

                print("\n2. task ids stay unique after a delete + add")
                ids = [t["id"] for t in on_disk]
                post(port, "/api/tasks", {"op": "delete", "id": ids[-1]})
                post(port, "/api/tasks", {"op": "add", "text": "after delete"})
                post(port, "/api/tasks", {"op": "delete", "id": ids[0]})
                post(port, "/api/tasks", {"op": "add", "text": "after delete 2"})
                cur = json.load(builtins.open(tasks_f))["tasks"]
                cur_ids = [t["id"] for t in cur]
                check("THE FIX: no two tasks share an id",
                      len(cur_ids) == len(set(cur_ids)), cur_ids)
                check("...and a new id never reuses a deleted one",
                      ids[-1] not in cur_ids and ids[0] not in cur_ids, cur_ids)
                victim = cur[0]
                st, j = post(port, "/api/tasks", {"op": "toggle", "id": victim["id"]})
                cur = json.load(builtins.open(tasks_f))["tasks"]
                check("toggle flips exactly one task",
                      st == 200 and sum(1 for t in cur if t.get("done")) == 1, cur)

                print("\n3. a transient EMFILE on the read never replaces tasks.json")
                before = builtins.open(tasks_f, "rb").read()
                faults.emfile_once = {tasks_f: 1}
                st, j = post(port, "/api/tasks", {"op": "add", "text": "lost?"})
                faults.emfile_once = {}
                check("THE FIX: the add answers 500 JSON",
                      st == 500 and j.get("ok") is False, (st, j))
                check("...and tasks.json is untouched",
                      builtins.open(tasks_f, "rb").read() == before)

                print("\n4. /api/access: concurrent grants, and the EMFILE case")
                dirs = []
                for i in range(6):
                    p = os.path.join(d, "folder%d" % i)
                    os.makedirs(p)
                    dirs.append(os.path.realpath(p))
                faults.slow = {access_f}
                res = burst(port, "/api/access",
                            [{"op": "add", "path": p} for p in dirs])
                faults.slow = set()
                got = json.load(builtins.open(access_f))["dirs"]
                check("THE FIX: all 6 concurrent grants are on disk",
                      sorted(got) == sorted(dirs), got)
                before = builtins.open(access_f, "rb").read()
                faults.emfile_once = {access_f: 1}
                st, j = post(port, "/api/access", {"op": "remove", "path": dirs[0]})
                faults.emfile_once = {}
                check("THE FIX: an EMFILE read answers 500 and keeps the grants",
                      st == 500 and builtins.open(access_f, "rb").read() == before,
                      (st, j))
                st, j = post(port, "/api/access", {"op": "remove", "path": dirs[0]})
                check("a normal remove still works",
                      st == 200 and dirs[0] not in j.get("dirs", [dirs[0]]), (st, j))

                print("\n5. layout.json: a transient EMFILE read writes nothing")
                with builtins.open(layout_f, "w") as f:
                    json.dump({"order": ["tasks", "clock"],
                               "seen": ["tasks", "clock", "weather"]}, f)
                before = builtins.open(layout_f, "rb").read()
                faults.emfile_once = {layout_f: 1}
                st, j = post(port, "/api/layout", {"op": "add", "id": "notes"})
                faults.emfile_once = {}
                check("THE FIX: /api/layout answers 500 instead of writing the default",
                      st == 500 and builtins.open(layout_f, "rb").read() == before,
                      (st, j, builtins.open(layout_f).read()[:120]))
                faults.emfile_once = {layout_f: 2}    # its re-read under the lock too
                try:
                    view, err = ns["get_layout"](), None
                except Exception as e:
                    view, err = None, e
                faults.emfile_once = {}
                check("THE FIX: get_layout() over an EMFILE read shows a view and "
                      "writes nothing", err is None and
                      builtins.open(layout_f, "rb").read() == before, (view, err))
                st, j = post(port, "/api/layout", {"op": "add", "id": "notes"})
                check("the next layout op applies to the owner's order",
                      st == 200 and j.get("order") == ["tasks", "clock", "notes"], (st, j))

                print("\n6. readers tolerate a wrong-shaped store")
                with builtins.open(tasks_f, "w") as f:
                    json.dump({"tasks": [5, {"id": "a", "text": "ok"}]}, f)
                check("get_tasks() leaves out a non-object entry",
                      ns["get_tasks"]()["tasks"] == [{"id": "a", "text": "ok"}],
                      ns["get_tasks"]())
                with builtins.open(access_f, "w") as f:
                    json.dump(["/etc"], f)
                try:
                    a, err = ns["get_access"](), None
                except Exception as e:
                    a, err = None, e
                check("get_access() of a list file grants nothing (no crash)",
                      err is None and a.get("dirs") == [], (a, err))
            check("no request reached socketserver's handle_error",
                  not srv.errors, srv.errors[:1])
        finally:
            srv.close()
    H.finish()


if __name__ == "__main__":
    main()
