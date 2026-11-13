#!/usr/bin/env python3
"""C133 part 1 (2026-10-21 audit, taken in round 2): the Claude bridge never
puts the escalated question or its context in argv.

claude_think ran `claude -p <message> ...`, so the whole USER-CONTEXT + TASK
envelope — a chat question, the local reply quoted as context, For-You's
You-Model profile — sat in the child's argv for the length of the call, where
every local user can read it with `ps`. It now goes to the child on stdin
(`claude -p` with no prompt argument reads its prompt from there) and argv
carries flags only.

Execs the REAL dashboard/aux_claudebridge.py on a throwaway HOME and drives
claude_think end to end with subprocess.run replaced by a recorder; `claude`
is never executed. On the old code the marker words are in argv.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

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
        print("  FAIL %s %s" % (name, extra))


HOME = tempfile.mkdtemp(prefix="hermes-c133-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
SETTINGS = os.path.join(DATA, "settings.json")
with open(SETTINGS, "w") as f:
    json.dump({"claude_escalation": {"enabled": True},
               "foryou_claude": {"enabled": True}}, f)
with open(os.path.join(HOME, ".hermes", "claude-bridge-prompt.md"), "w") as f:
    f.write("persona")

ns = {"__name__": "aux_harness", "HOME": HOME, "SETTINGS_FILE": SETTINGS,
      "register_get": lambda p, fn: None, "register_post": lambda p, fn: None,
      "settings_update": lambda fn: None,
      # E80: claude_think now refuses unless aux_memlayer's credential test
      # can run; these prompts carry no credential (t_e80_bridge_secrets.py
      # drives the real test)
      "_ml_looks_secret": lambda text: False}
SRC = os.path.join(REPO, "dashboard", "aux_claudebridge.py")
exec(compile(open(SRC, encoding="utf-8").read(), SRC, "exec"), ns)

fake_claude = os.path.join(HOME, "bin", "claude")
os.makedirs(os.path.dirname(fake_claude))
with open(fake_claude, "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(fake_claude, 0o755)
ns["_CB_BIN_CACHE"]["path"] = fake_claude

CALLS = []
_real_run = subprocess.run


def fake_run(argv, **kw):
    if argv and argv[0] == fake_claude:
        CALLS.append({"argv": list(argv), "kw": kw})
        return types.SimpleNamespace(returncode=0, stdout="a reasoned answer",
                                     stderr="")
    return _real_run(argv, **kw)


TASK_MARK = "Zephyrine-offer-question"
CTX_MARK = "Dana-Quill-private-goal"


def main():
    subprocess.run = fake_run
    try:
        for depth, caller in (("quick", ""), ("deep", ""), ("quick", "foryou")):
            CALLS.clear()
            r = ns["claude_think"]("Should I take the %s now?" % TASK_MARK,
                                   user_context="my goal: %s" % CTX_MARK,
                                   depth=depth, caller=caller)
            tag = "%s%s" % (depth, " / foryou" if caller else "")
            check("%s: the call went through" % tag,
                  r.get("ok") is True and len(CALLS) == 1, r)
            if not CALLS:
                continue
            argv, kw = CALLS[0]["argv"], CALLS[0]["kw"]
            joined = "\x00".join(argv)
            check("THE FIX (%s): the task never appears in argv" % tag,
                  TASK_MARK not in joined, argv)
            check("THE FIX (%s): the context never appears in argv" % tag,
                  CTX_MARK not in joined, argv)
            check("THE FIX (%s): no USER-CONTEXT/TASK envelope in argv" % tag,
                  not any("USER-CONTEXT" in a or "TASK:" in a for a in argv), argv)
            check("%s: every argv element after the binary is -p, a flag or a "
                  "flag's value" % tag,
                  all(a.startswith("-") or argv[i - 1].startswith("--")
                      for i, a in enumerate(argv) if i >= 1), argv)
            msg = kw.get("input")
            check("%s: the whole envelope goes on stdin instead" % tag,
                  isinstance(msg, str) and TASK_MARK in msg and CTX_MARK in msg
                  and msg.startswith("USER-CONTEXT:\n"), msg)
            check("%s: as text (the CLI's default --input-format)" % tag,
                  kw.get("text") is True and "--input-format" not in argv, kw)
            check("%s: encoded as UTF-8 whatever the locale (argv always was)"
                  % tag, str(kw.get("encoding") or "").lower() in ("utf-8", "utf8"),
                  kw.get("encoding"))
        check("_cb_argv takes no message at all",
              ns["_cb_argv"].__code__.co_argcount == 3,
              ns["_cb_argv"].__code__.co_varnames[:4])
    finally:
        subprocess.run = _real_run
        shutil.rmtree(HOME, ignore_errors=True)
    print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
