#!/usr/bin/env python3
"""R2-13 (round 2 of the 2026-10-21 audit): claude-recent.json (the full text
of every Claude dialogue) is written under a lock, through a unique temp
file that is 0600 from the moment it exists.

_cb_recent_add read the ring, prepended, and wrote it back with no lock,
through ONE shared `<path>.tmp` opened at the default umask and chmod'ed only
after the dialogue was already in it. Two escalations finishing together (the
auto-router and the Escalate button) each prepended to the same old list, so
one dialogue was lost — or both wrote into the same temp file at once.

Execs the REAL dashboard/aux_claudebridge.py in a throwaway HOME. Its `json`
is wrapped so a load pauses (forcing the two writers to overlap) and a dump
records what the half-written temp file looks like. On the old code a
dialogue is lost and the temp file is world-readable while it holds the text.
"""
import json as _json
import os
import stat
import sys
import tempfile
import threading
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
HOME = tempfile.mkdtemp(prefix="hermes-r2-13-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


ns = {"__name__": "aux_harness", "HOME": HOME,
      "SETTINGS_FILE": os.path.join(DATA, "settings.json"),
      "register_get": lambda p, fn: None, "register_post": lambda p, fn: None,
      "settings_update": lambda fn: None}
SRC = os.path.join(REPO, "dashboard", "aux_claudebridge.py")
exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)
PATH = ns["CB_RECENT_PATH"]
SEEN = {"modes": [], "tmp_names": []}


class SlowJson(object):
    """json, with a pause after each read and a look at each write."""
    loads, dumps = staticmethod(_json.loads), staticmethod(_json.dumps)

    @staticmethod
    def load(f, *a, **kw):
        out = _json.load(f, *a, **kw)
        time.sleep(0.15)                      # let the other writer read too
        return out

    @staticmethod
    def dump(obj, f, *a, **kw):
        try:
            target = f.fileno()
            SEEN["modes"].append(stat.S_IMODE(os.fstat(target).st_mode))
            SEEN["tmp_names"].append(sorted(n for n in os.listdir(DATA)
                                            if n.endswith(".tmp")))
        except Exception as e:
            SEEN["modes"].append("error %r" % e)
        return _json.dump(obj, f, *a, **kw)


def test_two_writers_keep_both():
    ns["json"] = SlowJson
    old_umask = os.umask(0o022)
    try:
        with open(PATH, "w") as f:
            _json.dump([{"task": "older"}], f)
        ts = [threading.Thread(target=ns["_cb_recent_add"], args=({"task": t},))
              for t in ("from the auto-router", "from the Escalate button")]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
    finally:
        os.umask(old_umask)
        ns["json"] = _json
    with open(PATH) as f:
        items = _json.load(f)
    tasks = [i.get("task") for i in items]
    check("THE FIX: two escalations finishing together both keep their dialogue",
          sorted(tasks[:2]) == ["from the Escalate button", "from the auto-router"]
          and tasks[2:] == ["older"], tasks)
    check("THE FIX: the temp file is 0600 while the dialogue is written into it",
          SEEN["modes"] and all(m == 0o600 for m in SEEN["modes"]), SEEN["modes"])
    names = [n for snap in SEEN["tmp_names"] for n in snap]
    check("THE FIX: never the shared '<path>.tmp'",
          names and os.path.basename(PATH) + ".tmp" not in names, SEEN["tmp_names"])
    check("the store itself is 0600",
          stat.S_IMODE(os.stat(PATH).st_mode) == 0o600,
          oct(stat.S_IMODE(os.stat(PATH).st_mode)))
    check("no temp file is left behind",
          not [n for n in os.listdir(DATA) if n.endswith(".tmp")], os.listdir(DATA))


def test_ring_cap_and_corrupt():
    with open(PATH, "w") as f:
        f.write("{not json")
    ns["_cb_recent_add"]({"task": "after a corrupt file"})
    with open(PATH) as f:
        items = _json.load(f)
    check("a corrupt store is replaced by a fresh ring (as before)",
          [i["task"] for i in items] == ["after a corrupt file"], items)
    for i in range(ns["CB_RECENT_MAX"] + 5):
        ns["_cb_recent_add"]({"task": "t%d" % i})
    with open(PATH) as f:
        items = _json.load(f)
    check("the ring stays capped at CB_RECENT_MAX, newest first",
          len(items) == ns["CB_RECENT_MAX"]
          and items[0]["task"] == "t%d" % (ns["CB_RECENT_MAX"] + 4), len(items))
    check("_cb_recent reads it back", ns["_cb_recent"](3)[0] == items[0])


def main():
    for t in (test_two_writers_keep_both, test_ring_cap_and_corrupt):
        print("-- %s" % t.__name__)
        try:
            t()
        except Exception:
            FAILS.append(t.__name__ + " raised")
            print("  FAIL %s raised:\n%s" % (t.__name__, traceback.format_exc()))
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
