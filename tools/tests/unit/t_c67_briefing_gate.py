#!/usr/bin/env python3
"""C67 + C16 (docs/audits/2026-10-21-codebase-audit.md): briefing_loop's gates.

C67: the memory-ceiling gate lived INSIDE server.py's _generate_briefing, and
aux_watchtower rebinds that name to _wt_generate_briefing (a 3200-token
synthesis, no admission check, no chat-active check).  So after every wake the
loop started that generation concurrently with the user's first turn, over the
soft memory ceiling, for a widget the layout might not even show.
C16: the loop body had no exception guard; one raise ended the thread.

AFTER: the gates live on the caller (briefing_loop -> _briefing_skip_reason),
so they hold whichever function the name is bound to, and the loop survives
a bad tick.  The real server.py code is extracted with `ast`; every dependency
is a stub, so nothing is probed or generated.
"""
import ast
import json
import os
import re
import sys
import tempfile
import time as _time
from pathlib import Path
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE))))

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[:300]))


def extract(relative, names, ns):
    tree = ast.parse((REPO / relative).read_text(), filename=relative)
    keep = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in names)
            or (isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in n.targets))]
    exec(compile(ast.fix_missing_locations(ast.Module(body=keep, type_ignores=[])),
                 relative, "exec"), ns)


class Stop(BaseException):
    """Ends the loop from inside time.sleep — not an Exception, so the loop's
    own `except Exception` cannot swallow it."""


def run_loop(d, ticks=1, briefing=None, layout=None, admission=True,
             chat_active=False, user_idle_s=3600, external_idle_s=3600,
             online=True, read_json_raises_once=False):
    calls = {"generate": 0, "probe": 0, "admission": 0, "sleeps": 0}
    bfile = os.path.join(d, "briefing.json")
    lfile = os.path.join(d, "layout.json")
    for path, obj in ((bfile, briefing), (lfile, layout)):
        if os.path.exists(path):
            os.remove(path)
        if obj is not None:
            with open(path, "w") as f:
                f.write(obj if isinstance(obj, str) else json.dumps(obj))

    def sleep(s):
        calls["sleeps"] += 1
        if calls["sleeps"] >= ticks:
            raise Stop()

    def probe():
        calls["probe"] += 1
        return online

    def adm():
        calls["admission"] += 1
        return (admission, 10.0, 50.0)

    def gen():
        calls["generate"] += 1

    now = _time.time()
    ns = {"json": json, "os": os, "re": re, "sys": sys,
          "time": SimpleNamespace(time=_time.time, sleep=sleep),
          "BRIEFING_FILE": bfile, "LAYOUT_FILE": lfile,
          "model_online": probe, "mlx_admission": adm,
          "_generate_briefing": gen,
          "_chat_jobs_active": lambda: chat_active,
          "_last_user_activity": now - user_idle_s,
          "_newest_external_turn_ts": lambda: now - external_idle_s,
          "_briefing_generating": False}
    extract("dashboard/server.py",
            {"read_json", "BRIEFING_REFRESH_MIN", "DEFAULT_LAYOUT",
             "BRIEFING_QUIET_MIN", "_briefing_skip_reason", "briefing_loop"}, ns)
    if read_json_raises_once:
        real = ns["read_json"]
        state = {"n": 0}

        def flaky(path, default):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("disk hiccup")
            return real(path, default)
        ns["read_json"] = flaky
    died = None
    try:
        ns["briefing_loop"]()
    except Stop:
        pass
    except Exception as e:          # the loop let an error out: thread dead
        died = "%s: %s" % (type(e).__name__, e)
    calls["died"] = died
    return calls


STALE = {"ok": True, "reply": "## old", "generated_at": _time.time() - 7200}
FRESH = {"ok": True, "reply": "## new", "generated_at": _time.time() - 60}
WITH = {"order": ["clock", "briefing"]}
WITHOUT = {"order": ["clock", "weather", "needsyou"]}


def main():
    print("C67/C16: the background briefing refresh is gated on the caller")
    with tempfile.TemporaryDirectory(prefix="hermes-c67-") as d:
        c = run_loop(d, briefing=STALE, layout=WITH)
        check("baseline: stale + shown + quiet + headroom + online -> refresh",
              c["generate"] == 1 and c["died"] is None, c)

        c = run_loop(d, briefing=STALE, layout=WITH, admission=False)
        check("THE FIX: over the memory ceiling -> no refresh "
              "(the rebound generator has no gate of its own)",
              c["generate"] == 0, c)
        check("...and the model is not even probed", c["probe"] == 0, c)

        c = run_loop(d, briefing=STALE, layout=WITH, chat_active=True)
        check("THE FIX: a chat turn in flight -> no refresh", c["generate"] == 0, c)

        c = run_loop(d, briefing=STALE, layout=WITH, user_idle_s=30)
        check("THE FIX: the user sent a turn 30 s ago (e.g. the one that woke "
              "the model) -> no refresh", c["generate"] == 0, c)
        c = run_loop(d, briefing=STALE, layout=WITH, external_idle_s=30)
        check("...nor 30 s after a Telegram/hub turn", c["generate"] == 0, c)

        c = run_loop(d, briefing=STALE, layout=WITHOUT)
        check("THE FIX: no briefing widget in the layout -> no refresh",
              c["generate"] == 0, c)
        check("...and nothing probed for an unshown widget",
              c["probe"] == 0 and c["admission"] == 0, c)
        c = run_loop(d, briefing=STALE, layout=None)
        check("no layout.json -> the default layout (which shows it) -> refresh",
              c["generate"] == 1, c)
        check("...and the layout file is NOT written by the loop",
              not os.path.exists(os.path.join(d, "layout.json")))

        c = run_loop(d, briefing=FRESH, layout=WITH)
        check("a fresh briefing -> no refresh, no probe",
              c["generate"] == 0 and c["probe"] == 0, c)
        c = run_loop(d, briefing=STALE, layout=WITH, online=False)
        check("model down -> no refresh (the loop never wakes anything)",
              c["generate"] == 0 and c["probe"] == 1, c)

        c = run_loop(d, briefing="[]", layout=WITH)
        check("C16: a briefing.json that is a JSON list does not kill the loop",
              c["died"] is None, c)
        c = run_loop(d, ticks=2, briefing=STALE, layout=WITH,
                     read_json_raises_once=True)
        check("C16: an error in one tick does not end the thread",
              c["died"] is None, c)
        check("...the next tick still refreshes", c["generate"] == 1, c)
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
