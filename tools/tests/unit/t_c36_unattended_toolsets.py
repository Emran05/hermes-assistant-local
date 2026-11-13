#!/usr/bin/env python3
"""C36 / F07 / C118 (docs/audits/2026-10-21-codebase-audit.md): unattended
`hermes -z` runs hold an explicit, reduced toolset.

`hermes -z` is fail-OPEN: hermes_cli/oneshot.py sets HERMES_YOLO_MODE=1 for the
call and tools/approval.py auto-approves every non-hardline command outside the
interactive CLI and the gateway.  run_agent() passed no --toolsets, so every
one-shot ran with the owner's whole `cli` set (terminal, file, code_execution,
delegation, computer_use ...).  It also passed `--continue <id>`, which the -z
branch ignores (C118).

AFTER: run_agent(..., toolsets=[...]) becomes `--toolsets a,b` before `-z`; an
empty list (or one that could only widen: "all", "*", "") becomes the inert
UNATTENDED_NO_TOOLS; None keeps the configured default; `--continue` is never
sent; the legacy briefing passes UNATTENDED_NO_TOOLS.

The real server.py code is extracted with `ast`; subprocess is a recorder, so
no hermes, no model.  Section 3 is an optional drift check against the
installed Hermes checkout (pure functions only, a throwaway HERMES_HOME), and
is skipped when there is none (CI).
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
import threading
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
    """Top-level defs AND assignments named in `names` (missing ones skipped,
    so the same harness runs against a build that predates them)."""
    tree = ast.parse((REPO / relative).read_text(), filename=relative)
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets):
            keep.append(node)
    exec(compile(ast.fix_missing_locations(ast.Module(body=keep, type_ignores=[])),
                 relative, "exec"), ns)
    return {getattr(n, "name", None) or n.targets[0].id for n in keep}


class Spawn(object):
    def __init__(self):
        self.cmds = []

    def run(self, cmd, **kw):
        self.cmds.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="fine", stderr="")


def namespace():
    sp = Spawn()
    ns = {"re": re, "os": os, "sys": sys, "HERMES": "/opt/hermes",
          "AGENT_TIMEOUT": 5, "_agent_lock": threading.Lock(),
          "_hermes_env": lambda: {},
          "bg_lane": lambda: {"hermes_args": ["--provider", "custom:bg",
                                              "-m", "bg-model"]},
          "subprocess": SimpleNamespace(run=sp.run,
                                        TimeoutExpired=subprocess.TimeoutExpired)}
    extract("dashboard/server.py",
            {"UNATTENDED_NO_TOOLS", "_TOOLSET_NAME_RE", "_toolsets_arg",
             "run_agent"}, ns)
    return ns, sp


def toolsets_of(cmd):
    if "--toolsets" in cmd:
        return cmd[cmd.index("--toolsets") + 1]
    return None


def test_run_agent():
    ns, sp = namespace()
    ra = ns["run_agent"]

    def last(*a, **kw):
        sp.cmds.clear()
        try:
            ra(*a, **kw)
        except TypeError as e:          # a build without the keyword
            return ["<TypeError: %s>" % e]
        return sp.cmds[-1] if sp.cmds else []

    cmd = last("brief me", toolsets=["web"])
    check("THE FIX: toolsets=['web'] becomes an explicit --toolsets web",
          toolsets_of(cmd) == "web", cmd)
    check("...placed before -z, which takes the prompt as its value",
          "-z" in cmd and cmd.index("--toolsets") < cmd.index("-z")
          and cmd[cmd.index("-z") + 1] == "brief me", cmd)

    nothing = ",".join(ns.get("UNATTENDED_NO_TOOLS") or ("<missing>",))
    check("UNATTENDED_NO_TOOLS is the inert clarify-only set",
          tuple(ns.get("UNATTENDED_NO_TOOLS") or ()) == ("clarify",),
          ns.get("UNATTENDED_NO_TOOLS"))
    cmd = last("x", toolsets=[])
    check("an EMPTY list means no tools, never 'the configured default'",
          toolsets_of(cmd) == nothing, cmd)
    for bad in (["all"], ["*"], [""], ["  "], ["all", "*"]):
        cmd = last("x", toolsets=bad)
        check("%r cannot widen the set (-> the inert set)" % (bad,),
              toolsets_of(cmd) == nothing, cmd)
    cmd = last("x", toolsets=["web", "all"])
    check("'all' inside a list is dropped, the rest kept",
          toolsets_of(cmd) == "web", cmd)
    cmd = last("x", toolsets=["--yolo", "-rf", "web"])
    check("a flag-shaped entry can never become an argument",
          toolsets_of(cmd) == "web" and "--yolo" not in cmd, cmd)
    cmd = last("x", toolsets="web, todo,web")
    check("a comma string is split, trimmed and de-duplicated",
          toolsets_of(cmd) == "web,todo", cmd)
    cmd = last("x", toolsets=("search",))
    check("a tuple works too", toolsets_of(cmd) == "search", cmd)

    cmd = last("x")
    check("toolsets=None keeps today's behaviour (no --toolsets at all)",
          "--toolsets" not in cmd and cmd[-2:] == ["-z", "x"], cmd)

    cmd = last("x", session="20261021_000000_abcd")
    check("C118: --continue is never sent (-z ignores it)",
          cmd[:1] == ["/opt/hermes"] and "--continue" not in cmd
          and "20261021_000000_abcd" not in cmd, cmd)

    cmd = last("x", lane="bg", toolsets=["web"])
    check("the bg lane keeps its provider args AND the toolset",
          cmd[1:5] == ["--provider", "custom:bg", "-m", "bg-model"]
          and toolsets_of(cmd) == "web", cmd)


def test_legacy_briefing():
    calls = []

    def run_agent(message, session=None, lane="primary", toolsets=None):
        calls.append({"lane": lane, "toolsets": toolsets})
        return True, "## Today\nnothing yet"

    with tempfile.TemporaryDirectory(prefix="hermes-c36-") as d:
        ns = {"run_agent": run_agent, "_state_lock": threading.Lock(),
              "_briefing_generating": False,
              "mlx_admission": lambda: (True, 1.0, 50.0),
              "access_preamble": lambda user_text="": "[context] x\n\n",
              "_briefing_is_sane": lambda t: True,
              "write_json": lambda p, o: None,
              "BRIEFING_FILE": os.path.join(d, "briefing.json"),
              "time": __import__("time")}
        extract("dashboard/server.py",
                {"UNATTENDED_NO_TOOLS", "BRIEFING_PROMPT", "_generate_briefing"}, ns)
        ns["_generate_briefing"]()
    check("the legacy briefing ran once", len(calls) == 1, calls)
    check("THE FIX: the unattended briefing passes the inert toolset",
          calls and calls[0]["toolsets"] is not None
          and tuple(calls[0]["toolsets"]) == ("clarify",), calls)
    check("...on the background lane", calls and calls[0]["lane"] == "bg", calls)
    src = (REPO / "dashboard/server.py").read_text()
    prompt = src.split("BRIEFING_PROMPT = (")[1].split("\n)\n")[0]
    check("the briefing prompt no longer asks it to verify from email/folders",
          "granted folders, or your memory" not in prompt
          and "no tools" in prompt, prompt[-400:])


_HERMES_PROBE = r'''
import inspect, json, os, sys, tempfile
home = tempfile.mkdtemp(prefix="hermes-c36-home-")
import atexit, shutil
atexit.register(shutil.rmtree, home, True)
os.environ["HERMES_HOME"] = home
os.environ["HOME"] = home
sys.path.insert(0, sys.argv[1])
out = {}
from hermes_cli.oneshot import _validate_explicit_toolsets, run_oneshot
import toolsets
out["clarify"] = _validate_explicit_toolsets("clarify")[0]
out["empty"] = _validate_explicit_toolsets("")[0]
out["all"] = _validate_explicit_toolsets("all")[0]
out["resolved"] = sorted(toolsets.resolve_toolset("clarify"))
out["oneshot_params"] = list(inspect.signature(run_oneshot).parameters)
try:
    from agent.memory_manager import memory_provider_tools_enabled
    out["memory_tools"] = memory_provider_tools_enabled(["clarify"])
except Exception as e:
    out["memory_tools"] = "n/a: %s" % type(e).__name__
from hermes_cli._parser import build_top_level_parser
p = build_top_level_parser()[0]
a = p.parse_args(["--provider", "custom:bg", "-m", "m", "--toolsets",
                  "clarify", "-z", "hi"])
out["parsed"] = [a.oneshot, a.toolsets, a.provider]
src = inspect.getsource(run_oneshot)
out["yolo"] = 'os.environ["HERMES_YOLO_MODE"] = "1"' in src
print(json.dumps(out))
'''


def test_installed_hermes():
    root = os.environ.get("HERMES_AGENT_DIR") or os.path.join(
        os.path.expanduser("~"), ".hermes", "hermes-agent")
    py = os.path.join(root, "venv", "bin", "python")
    if not (os.path.isfile(os.path.join(root, "hermes_cli", "oneshot.py"))
            and os.access(py, os.X_OK)):
        # a SKIP line (upper case), so run.sh counts it and CI's FAIL_ON_SKIP sees it
        print("  SKIP no Hermes checkout at %s (CI) — drift check not run" % root)
        return
    import json
    # -I: nothing from this repo or the env shadows Hermes; -B: never write
    # .pyc files into the owner's checkout (read-only use only).
    r = subprocess.run([py, "-I", "-B", "-c", _HERMES_PROBE, root], capture_output=True,
                       text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir()})
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        check("the Hermes probe ran", False, (r.returncode, r.stderr[-400:]))
        return
    check("hermes: -z still forces YOLO (why the toolset is the control)",
          out["yolo"] is True, out)
    check("hermes: --toolsets clarify validates to ['clarify']",
          out["clarify"] == ["clarify"], out)
    check("hermes: clarify resolves to exactly one tool, clarify",
          out["resolved"] == ["clarify"], out)
    check("hermes: '' and 'all' mean the CONFIGURED set (why run_agent never sends them)",
          out["empty"] is None and out["all"] is None, out)
    check("hermes: memory-provider tools stay off under clarify",
          out["memory_tools"] is False, out)
    check("hermes: run_agent's argv shape parses (provider, toolsets, -z)",
          out["parsed"] == ["hi", "clarify", "custom:bg"], out)
    check("hermes C118: run_oneshot still has no session parameter",
          "session" not in " ".join(out["oneshot_params"]), out)


def test_no_stale_claims():
    # the user-facing words must not promise what the code no longer does
    doctor = (REPO / "dashboard/doctor.py").read_text()
    check("doctor no longer says chat falls back to one-shot hermes -z",
          "falls back to one-shot" not in doctor)
    roadmap = (REPO / "docs/CAPABILITY-ROADMAP.md").read_text()
    check("the drill doctrine no longer says -z fails closed",
          "oneshot runs fail closed" not in roadmap)
    findings = (REPO / "docs/FINDINGS.md").read_text()
    check("FINDINGS no longer says a headless -z session can't approve",
          "session can't approve" not in findings)


def main():
    print("C36/F07/C118: unattended hermes -z runs hold an explicit, reduced toolset")
    print("\n1. run_agent's argv")
    test_run_agent()
    print("\n2. the legacy (pre-watchtower) briefing")
    test_legacy_briefing()
    print("\n3. drift check against the installed Hermes")
    test_installed_hermes()
    print("\n4. nothing user-facing still promises the old behaviour")
    test_no_stale_claims()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
