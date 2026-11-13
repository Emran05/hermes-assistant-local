#!/usr/bin/env python3
"""E80 — the Claude bridge refuses a credential in the task or the context,
and its usage log keeps only a redacted summary.

2026-11-09 audit E80: _cb_gate judged WORDING only ("send my password"), so a
key or password pasted into a judgment question passed it and — with
escalation on and auto-route `auto` — went to Claude; and the usage log kept
the first 160 characters of the task verbatim although it promises "no
secrets".  Now claude_think() runs the shared credential test
(aux_memlayer._ml_looks_secret) over task AND context after the master switch
and before anything is spawned, refuses with reason "secret_in_prompt" (or
"secret_check_unavailable" when the test cannot run: fail closed), and every
log summary goes through aux_convos' redactor before it is cut.

REAL code: aux_convos.py exec'd, aux_memlayer's credential test AST-extracted,
aux_claudebridge.py exec'd on a throwaway HOME; subprocess.run is replaced by
a recorder, so `claude` is never executed.  Offline.  Every credential here
is a fake with the right shape, assembled from pieces.
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
DASHSRC = os.path.join(REPO, "dashboard")

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (("  — " + str(detail)[:300]) if detail and not cond else ""))


HOME = tempfile.mkdtemp(prefix="hermes-e80-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
SETTINGS = os.path.join(DATA, "settings.json")


def escalation(on):
    with open(SETTINGS, "w") as f:
        json.dump({"claude_escalation": {"enabled": on}}, f)


escalation(True)
with open(os.path.join(HOME, ".hermes", "claude-bridge-prompt.md"), "w") as f:
    f.write("persona")

ns = {"__name__": "aux_harness", "HOME": HOME, "SETTINGS_FILE": SETTINGS,
      "register_get": lambda p, fn: None, "register_post": lambda p, fn: None,
      "settings_update": lambda fn: None, "re": re, "os": os, "json": json,
      "time": time, "sys": sys, "PREWARM_SESSION": "__prewarm__",
      "SESSION_RE": re.compile(r"^[A-Za-z0-9._-]{1,80}$"),
      "load_chat": lambda sid: {}, "chat_path": lambda sid: os.path.join(HOME, sid),
      "save_chat_update": lambda sid, fn: None, "CHATS": HOME}


def exec_file(name):
    p = os.path.join(DASHSRC, name)
    exec(compile(open(p, encoding="utf-8").read(), p, "exec"), ns)


exec_file("aux_claudebridge.py")
exec_file("aux_convos.py")
ML = os.path.join(DASHSRC, "aux_memlayer.py")
tree = ast.parse(open(ML, encoding="utf-8").read(), ML)
WANT = {"_ml_log", "_ml_g", "_MlNoScrubber", "_ml_scrubber", "_ml_looks_secret"}
ns["_ml_state"] = {"ready": False, "error": "", "logged": set()}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(
    n, (ast.FunctionDef, ast.ClassDef)) and n.name in WANT], type_ignores=[]), ML, "exec"), ns)

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
        CALLS.append(kw.get("input"))
        return types.SimpleNamespace(returncode=0, stdout="a reasoned answer", stderr="")
    return _real_run(argv, **kw)


subprocess.run = fake_run
LOG = ns["CB_LOG_PATH"]
RECENT = ns["CB_RECENT_PATH"]


def log_rows():
    try:
        return [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
    except OSError:
        return []


def raw_files():
    out = ""
    for p in (LOG, RECENT):
        try:
            out += open(p, encoding="utf-8").read()
        except OSError:
            pass
    return out


ANT = "sk-" "ant-api03-AbCdEf0123456789abcdefGHIJKLMN"
OAI = "sk-" "proj-abcdefghijklmnopqrstuvwxyz0123"
PG = "S3cret" "Passw0rd"
HF = "hf_" "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1Zq"
TASKS = {
    "Bearer key in a curl line": (
        "Why does my deploy keep failing with 401? I'm calling curl -H 'Authorization: "
        "Bearer %s' https://api.anthropic.com/v1/messages and should I rotate it or is "
        "the header format wrong? Think hard about the tradeoffs here." % ANT, ANT),
    "OPENAI_API_KEY= in the question": (
        "Should I worry that my .env has OPENAI_API_KEY=%s committed in the repo "
        "history? What are the tradeoffs of rewriting history versus rotating?" % OAI, OAI),
    "a URL with user:password": (
        "Please summarise this and tell me what to do: postgres://admin:%s@db.example."
        "com:5432/app connection refused" % PG, PG),
    "prose password": ("my bank password is Hunter22!x — should I enable 2FA by SMS "
                       "or an authenticator app, and why?", "Hunter22!x"),
    "a bare Hugging Face token": ("Is it safe to keep %s in my shell history? What are "
                                  "the tradeoffs?" % HF, HF),
}

print("--- a credential in the task is refused before anything is spawned")
for name, (task, secret) in sorted(TASKS.items()):
    del CALLS[:]
    n0 = len(log_rows())
    res = ns["claude_think"](task, user_context="goals: ship v2", depth="quick")
    check("%s: refused" % name, res.get("ok") is False and res.get("refused") is True and
          res.get("reason") == "secret_in_prompt", res)
    check("%s: nothing spawned" % name, CALLS == [], CALLS)
    check("%s: the refusal text does not echo the secret" % name,
          secret not in (res.get("text") or ""), res.get("text"))
    rows = log_rows()[n0:]
    check("%s: one usage-log row with the reason" % name,
          len(rows) == 1 and rows[0].get("reason") == "secret_in_prompt" and
          rows[0].get("model") is None, rows)
    check("%s: the log never holds the secret" % name, secret not in raw_files())

print("--- a credential in the CONTEXT is refused too")
del CALLS[:]
res = ns["claude_think"]("Should I take the Berlin offer?",
                         user_context="notes: GITHUB_TOKEN=ghp_" "abcdefghijklmnopqrstuvwxyz0123456789",
                         depth="quick")
check("context secret: refused secret_in_prompt",
      res.get("refused") is True and res.get("reason") == "secret_in_prompt", res)
check("context secret: nothing spawned", CALLS == [])
check("context secret: never in the log", "abcdefghijklmnopqrstuvwxyz0123456789" not in raw_files())

print("--- ordinary questions still go through")
for task in ("Should I take the Berlin offer or stay? Think hard about the tradeoffs.",
             "My password manager is 1Password; is a hardware key worth it for my "
             "GitHub token rotation every 90 days?",
             "The token is expired every morning — why might a refresh flow fail?"):
    del CALLS[:]
    res = ns["claude_think"](task, user_context="goals: ship v2", depth="quick")
    check("allowed: %r" % task[:50], res.get("ok") is True and len(CALLS) == 1, res)

print("--- the usage-log summary is redacted before it is cut")
pad = "x " * 70                                          # 140 chars
s = ns["_cb_summary"](pad + "and the key is %s ok" % OAI)
check("a key straddling the 160-character cut leaves no part behind",
      OAI[:6] not in s and len(s) <= ns["CB_SUMMARY_MAX"], s)
s = ns["_cb_summary"]("export OPENAI_API_KEY=%s please" % OAI)
check("a labelled key is redacted in the summary", OAI not in s and "[redacted]" in s, s)
red = ns.pop("_cv_redact")
check("no redactor -> no summary text", ns["_cb_summary"]("anything at all") == "")
ns["_cv_redact"] = red

print("--- fail closed when the credential test cannot run")
real = ns.pop("_ml_looks_secret")
del CALLS[:]
res = ns["claude_think"]("Should I take the Berlin offer?", depth="quick")
check("missing test: refused secret_check_unavailable",
      res.get("refused") is True and res.get("reason") == "secret_check_unavailable", res)
check("missing test: nothing spawned", CALLS == [])


def boom(text):
    raise RuntimeError("regex broke")


ns["_ml_looks_secret"] = boom
res = ns["claude_think"]("Should I take the Berlin offer?", depth="quick")
check("raising test: refused secret_check_unavailable",
      res.get("reason") == "secret_check_unavailable" and CALLS == [], res)
ns["_ml_looks_secret"] = real

print("--- the master switch is still the first check")
escalation(False)
n0 = len(log_rows())
res = ns["claude_think"](TASKS["prose password"][0], depth="quick")
check("escalation off answers escalation_off (not a secret verdict)",
      res.get("reason") == "escalation_off", res)
check("...and writes no log row", len(log_rows()) == n0)

print()
print("TESTS %d passed %d failed" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAILED: " + b)
sys.exit(1 if BAD else 0)
