#!/usr/bin/env python3
"""C27 follow-up for needsyou.json (review X-http-plumbing-1).

BEFORE: _ny_load() turned "absent", "unparsable" and "unreadable" into the
same empty store, and the 60 s build loop writes through _ny_update() every
pass.  One stray comma, a bad restore or a file left at mode 000 was replaced
with a fresh store within a minute: every done_at / snoozed_until /
reclassified_to and every act row gone (this file is the ONLY record a
decision ever existed — tag, never move), no .corrupt copy, no log line.

AFTER:
  * absent or empty            -> defaults, as before;
  * present but not a JSON dict -> moved aside as needsyou.json.corrupt-<ts>
    (bytes intact) and the store restarts from defaults;
  * present but unreadable      -> _ny_update() writes NOTHING; the build
    still serves the inbox (with `store_error`), and the act route answers
    503 instead of reporting a decision saved over a file it could not read.

Real dashboard/aux_needsyou.py in a throwaway HOME (tools/tests/unit/
_ny_env.py).  Against the pre-fix module (git stash the aux_needsyou.py
change) the "kept" checks fail: the store is rewritten with acts=0 and no
.corrupt copy exists.
"""
import contextlib
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ny_env as E                                             # noqa: E402

NOW = time.time()
ANA = "message:+15550002701"


def fresh():
    g, data = E.load()
    E.write_messages(data, [
        E.convo("+15550002701", "can you call me back?", NOW - 900, name="Ana"),
        E.convo("+15550002702", "are you around later?", NOW - 600, name="Ben"),
    ])
    p = g["_ny_build"](NOW)
    g["_ny_cache"]["payload"] = p
    g["_ny_cache"]["at"] = time.time()
    r = g["_ny_act_handler"](E.Ctx(body={"id": ANA, "action": "done"}))
    assert isinstance(r, dict) and r.get("ok"), r
    return g, data


def raw(path):
    with open(path, "rb") as f:
        return f.read()


def corrupt_copies(data):
    return sorted(n for n in os.listdir(data)
                  if n.startswith("needsyou.json.corrupt-"))


def quiet(fn, *a, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn(*a, **kw)
    return out, err.getvalue()


print("=== 1. a corrupt store is moved aside, never written over ===")
g, data = fresh()
path = g["NY_STORE"]
good = raw(path)
E.check("setup: the Done is in the store",
        bool(json.loads(good)["items"][ANA].get("done_at")))
broken = good[:-1] + b",}"                         # one stray trailing comma
with open(path, "wb") as f:
    f.write(broken)
_p, log = quiet(g["_ny_build"], NOW + 61)          # what the 60 s loop does
copies = corrupt_copies(data)
E.check("a .corrupt copy was kept", len(copies) == 1, copies)
E.check("the copy holds the original bytes",
        bool(copies) and raw(os.path.join(data, copies[0])) == broken)
E.check("the copy is owner-only",
        bool(copies) and (os.stat(os.path.join(data, copies[0])).st_mode
                          & 0o077) == 0)
E.check("one [store] line names it", "[store]" in log and "needsyou.json" in log,
        log)
E.check("the live store restarted from defaults (and parses)",
        isinstance(json.loads(raw(path)), dict))

print("=== 2. a JSON value that is not an object counts as corrupt too ===")
g, data = fresh()
path = g["NY_STORE"]
with open(path, "w") as f:
    f.write("[1, 2, 3]")
quiet(g["_ny_build"], NOW + 61)
E.check("a list is moved aside", len(corrupt_copies(data)) == 1,
        corrupt_copies(data))

print("=== 3. an UNREADABLE store is never replaced ===")
g, data = fresh()
path = g["NY_STORE"]
good = raw(path)
real_open = open


def denied(p, *a, **kw):
    if os.path.abspath(str(p)) == os.path.abspath(path):
        raise PermissionError(13, "Permission denied", p)
    return real_open(p, *a, **kw)


g["open"] = denied                  # the module resolves open() via its globals
p, log = quiet(g["_ny_build"], NOW + 61)
E.check("the build still serves an inbox", p.get("ok") is True, p)
E.check("the payload says decisions are not being saved",
        bool(p.get("store_error")), p.get("store_error"))
E.check("one log line says why", "store not updated" in log, log)
r, _ = quiet(g["_ny_act_handler"],
             E.Ctx(body={"id": "message:+15550002702", "action": "done"}))
E.check("a Done is refused with 503, not reported saved",
        isinstance(r, tuple) and r[1] == 503 and r[0].get("ok") is False, r)
E.check("_ny_record_act reports the write as not done",
        quiet(g["_ny_record_act"], ANA, "open")[0] is False)
m = g["_ny_metrics_handler"]()
E.check("metrics carry the store error", bool(m.get("store_error")), m)
del g["open"]
E.check("the file is byte-for-byte what it was", raw(path) == good)
E.check("no .corrupt copy for a store that only could not be read",
        corrupt_copies(data) == [])
quiet(g["_ny_build"], NOW + 62)
E.check("readable again -> the error clears", not g["_ny_stats"].get("store_error"))
E.check("...and the original Done survived",
        bool(g["_ny_load"]()["items"][ANA].get("done_at")))

print("=== 4. a real mode-000 file ===")
if os.geteuid() == 0:
    # root reads a mode-000 file anyway; section 3 covers the same code path
    E.check("mode 000 (root: covered by section 3)", True)
else:
    g, data = fresh()
    path = g["NY_STORE"]
    good = raw(path)
    os.chmod(path, 0)
    try:
        quiet(g["_ny_build"], NOW + 61)
        mode = os.stat(path).st_mode & 0o777
    finally:
        os.chmod(path, 0o600)
    E.check("the mode was left alone (not replaced by a 0600 rewrite)",
            mode == 0, oct(mode))
    E.check("the acts survived", raw(path) == good)

print("=== 5. a directory where the store should be ===")
g, data = fresh()
path = g["NY_STORE"]
os.remove(path)
os.mkdir(path)
p, _ = quiet(g["_ny_build"], NOW + 61)
E.check("served, with store_error", p.get("ok") and bool(p.get("store_error")), p)
E.check("still a directory", os.path.isdir(path))

print("=== 6. absent and empty still mean 'start fresh' ===")
g, data = fresh()
path = g["NY_STORE"]
os.remove(path)
p, _ = quiet(g["_ny_build"], NOW + 61)
E.check("absent -> written from defaults", os.path.isfile(path)
        and not p.get("store_error"))
with open(path, "w") as f:
    f.write("  \n")
p, _ = quiet(g["_ny_build"], NOW + 62)
E.check("empty -> rewritten, no .corrupt copy",
        isinstance(json.loads(raw(path)), dict) and corrupt_copies(data) == [])
r = g["_ny_act_handler"](E.Ctx(body={"id": ANA, "action": "done"}))
E.check("a Done on a fresh store saves", isinstance(r, dict) and r.get("ok"), r)

sys.exit(E.finish())
