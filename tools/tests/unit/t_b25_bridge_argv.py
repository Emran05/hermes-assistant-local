#!/usr/bin/env python3
"""Regression test for B25 (docs/audits/2026-10-08-codebase-audit.md; still
open in 2026-10-21's status table).

BEFORE: the Claude-bridge child ran `claude -p <msg> --model --effort
--system-prompt-file --output-format text --disallowedTools "Bash Edit ..."`
and nothing else. A deny-list is not a lockout: every built-in tool NOT on
it stayed available, the owner's ~/.claude settings (hooks, plugins,
permission allow-rules) loaded, and every user-scope MCP server started —
with attacker-writable feed titles in the same prompt (C35).

AFTER: an allow-list on top of the deny-list — `--tools ""`,
`--strict-mcp-config --mcp-config {"mcpServers":{}}`, `--setting-sources ""`,
`--permission-mode dontAsk`, `--disable-slash-commands`,
`--no-session-persistence` — every flag checked against `claude --help` of
the installed CLI (Claude Code 2.1.304). A CLI that rejects one of them makes
the call FAIL with a clear error; it is never retried unhardened.

The REAL aux_claudebridge.py is exec'd on a throwaway HOME and claude_think()
is driven end to end with subprocess.run replaced by a recorder. `claude` is
never executed (not even --help: the unit tier is offline and CI has no CLI).
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


# Long options of the installed CLI (`claude --help`, Claude Code 2.1.304)
# that this argv may use. A flag outside this set is unverified: adding one
# means re-checking `claude --help` and extending the set on purpose.
VERIFIED_FLAGS = {
    "-p", "--model", "--effort", "--system-prompt-file", "--output-format",
    "--tools", "--strict-mcp-config", "--mcp-config", "--setting-sources",
    "--permission-mode", "--disable-slash-commands", "--no-session-persistence",
    "--disallowedTools",
}
VARIADIC = {"--tools", "--mcp-config", "--disallowedTools"}   # <x...> in --help

HOME = tempfile.mkdtemp(prefix="hermes-b25-")
DATA = os.path.join(HOME, ".hermes", "dashboard")
os.makedirs(DATA)
SETTINGS = os.path.join(DATA, "settings.json")
with open(SETTINGS, "w") as f:
    json.dump({"claude_escalation": {"enabled": True}}, f)
with open(os.path.join(HOME, ".hermes", "claude-bridge-prompt.md"), "w") as f:
    f.write("persona")

ns = {"__name__": "aux_harness", "HOME": HOME, "SETTINGS_FILE": SETTINGS,
      "register_get": lambda p, fn: None, "register_post": lambda p, fn: None,
      "settings_update": lambda fn: None,
      # E80: claude_think now refuses unless aux_memlayer's credential test
      # can run; these prompts carry no credential (t_e80_bridge_secrets.py
      # drives the real test)
      "_ml_looks_secret": lambda text: False}
path = os.path.join(REPO, "dashboard", "aux_claudebridge.py")
with open(path, encoding="utf-8") as f:
    exec(compile(f.read(), path, "exec"), ns)

fake_claude = os.path.join(HOME, "bin", "claude")
os.makedirs(os.path.dirname(fake_claude))
with open(fake_claude, "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(fake_claude, 0o755)
ns["_CB_BIN_CACHE"]["path"] = fake_claude

CALLS = []
REPLY = {"rc": 0, "out": "a reasoned answer", "err": ""}
_real_run = subprocess.run


def fake_run(argv, **kw):
    if argv and argv[0] == fake_claude:
        cwd = kw.get("cwd")
        CALLS.append({"argv": list(argv), "kw": kw,
                      "cwd_listing": os.listdir(cwd) if cwd else None})
        return types.SimpleNamespace(returncode=REPLY["rc"], stdout=REPLY["out"],
                                     stderr=REPLY["err"])
    return _real_run(argv, **kw)


subprocess.run = fake_run
os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-real"
try:
    res = ns["claude_think"]("Should I take the offer?", user_context="ctx",
                             depth="quick")
finally:
    os.environ.pop("ANTHROPIC_API_KEY", None)

check("the call went through", res.get("ok") is True and len(CALLS) == 1, res)
argv = CALLS[0]["argv"] if CALLS else []
kw = CALLS[0]["kw"] if CALLS else {}


def value(flag):
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return None


print("== the prompt goes on stdin, argv carries flags only (C133) ==")
check("argv[1] is -p and argv[2] is already a flag",
      argv[1:2] == ["-p"] and argv[2:3] == ["--model"], argv[:3])
check("the message is the child's stdin",
      str(kw.get("input") or "").startswith("USER-CONTEXT:\nctx\n\nTASK:\n"),
      kw.get("input"))

print("== the allow-list (B25) ==")
check('--tools "" disables every built-in tool', value("--tools") == "", value("--tools"))
check("--strict-mcp-config is set", "--strict-mcp-config" in argv)
mcp = value("--mcp-config")
try:
    mcp_obj = json.loads(mcp) if mcp else None
except ValueError:
    mcp_obj = None
check("--mcp-config names NO servers", mcp_obj == {"mcpServers": {}}, mcp)
check('--setting-sources "" loads none of ~/.claude settings',
      value("--setting-sources") == "", value("--setting-sources"))
check("--permission-mode is dontAsk (deny anything not pre-approved)",
      value("--permission-mode") == "dontAsk", value("--permission-mode"))
check("--disable-slash-commands is set", "--disable-slash-commands" in argv)
check("--no-session-persistence is set (the prompt is not kept under ~/.claude)",
      "--no-session-persistence" in argv)

print("== the deny-list stays as belt-and-braces ==")
deny = (value("--disallowedTools") or "").split()
check("--disallowedTools still lists Bash Edit Write WebFetch WebSearch Task",
      all(t in deny for t in ("Bash", "Edit", "Write", "WebFetch", "WebSearch", "Task")),
      deny)

print("== nothing that widens access ==")
for bad in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
            "--add-dir", "--allowedTools", "--allowed-tools", "--plugin-dir",
            "--chrome", "--agents", "--settings"):
    check("no %s" % bad, bad not in argv)
check("permission mode is not a bypass/accept mode",
      value("--permission-mode") not in ("bypassPermissions", "acceptEdits", "auto"))

print("== argv shape ==")
flags = [a for a in argv[1:] if a.startswith("-")]
check("every flag is one verified against the installed CLI's --help",
      set(flags) <= VERIFIED_FLAGS, sorted(set(flags) - VERIFIED_FLAGS))
ok_shape = True
for i, a in enumerate(argv):
    if a in VARIADIC:
        nxt = argv[i + 2] if i + 2 < len(argv) else None
        if nxt is not None and not nxt.startswith("--"):
            ok_shape = False
check("every variadic option is followed by another option (cannot swallow args)",
      ok_shape)
check("--disallowedTools is last", argv[-2:-1] == ["--disallowedTools"], argv[-2:])

print("== the child's surroundings (unchanged, still asserted) ==")
check("runs in an empty scratch cwd", CALLS and CALLS[0]["cwd_listing"] == [],
      CALLS[0]["cwd_listing"] if CALLS else None)
check("the scratch cwd is removed afterwards",
      bool(kw.get("cwd")) and not os.path.exists(kw.get("cwd")))
check("ANTHROPIC_API_KEY is stripped from the child env",
      "ANTHROPIC_API_KEY" not in (kw.get("env") or {}))
check("stdin is the message, nothing inherited from the dashboard",
      kw.get("stdin") is None and isinstance(kw.get("input"), str))

print("== an old CLI that rejects a hardening flag fails closed, clearly ==")
CALLS.clear()
REPLY.update(rc=1, out="", err="error: unknown option '--setting-sources'")
r = ns["claude_think"]("Should I take the offer?", depth="quick")
check("the call fails (it is not retried without the flags)",
      r.get("ok") is False and len(CALLS) == 1, (r, len(CALLS)))
check("the error says a hardening flag was rejected",
      "hardening flag" in (r.get("error") or ""), r.get("error"))

print("== the master switch still short-circuits before any child ==")
CALLS.clear()
with open(SETTINGS, "w") as f:
    json.dump({"claude_escalation": {"enabled": False}}, f)
r = ns["claude_think"]("Should I take the offer?")
check("escalation off -> refused, no child", r.get("reason") == "escalation_off"
      and not CALLS, r)

subprocess.run = _real_run
shutil.rmtree(HOME, ignore_errors=True)
print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
