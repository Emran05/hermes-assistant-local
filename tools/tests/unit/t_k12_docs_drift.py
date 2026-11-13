#!/usr/bin/env python3
"""Docs drift where the code is the truth — C62, C66, C68, C69, C73.

Each check reads a fact out of the CODE (doctor's registered checks, the
permission engine's class list, the on-demand gate's own message, the paths
update.sh really writes, the binary the calendar provider shells out to) and
holds the README / RUNBOOK / installer text against it, so the next change to
the code that the docs do not follow fails here instead of shipping.

  C62  README / RUNBOOK numbers: doctor check count, action-class count.
  C66  "both lanes on demand" — nothing ever starts the background lane.
  C68  RUNBOOK vs the A09 on-demand gate, the pinned venv and Python 3.12+.
  C69  "the updater never touches ~/.hermes" — it links a plugin, edits
       config.yaml and holds a lock there.
  C73  "Google Calendar replaces Apple Calendar for today" — every surface but
       the chat preamble reads icalBuddy, which no doc declared.
  R2-59 "No transcript leaves the machine" — a Telegram turn transits
       Telegram and an escalation sends the question to Claude.

Pure file reads plus one import of doctor.py under a temp HOME.  Offline.
"""
import ast, importlib.util, io, os, re, shutil, sys, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
CHECKS = [0]
WORDS = {"eighteen": 18, "nineteen": 19, "twenty": 20, "twenty-one": 21, "twenty-two": 22,
         "twenty-three": 23, "twenty-four": 24, "twenty-five": 25}


def check(name, cond, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  <- %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def text(rel):
    return io.open(os.path.join(REPO, rel), encoding="utf-8").read()


def flat(s):
    return re.sub(r"\s+", " ", s)


def doctor_checks(tmp):
    os.environ["HOME"] = os.path.join(tmp, "home")
    spec = importlib.util.spec_from_file_location("doctor_docs", os.path.join(REPO, "dashboard", "doctor.py"))
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    return [c[0] for c in d.CHECKS]


def class_order():
    tree = ast.parse(text("dashboard/permissions.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CLASS_ORDER" for t in node.targets):
            return list(ast.literal_eval(node.value))
    return None


def main(tmp):
    readme, runbook = text("README.md"), text("RUNBOOK.md")
    fr, fb = flat(readme), flat(runbook)

    print("\nC62. the numbers in the docs are the numbers in the code")
    n = len(doctor_checks(tmp))
    m = re.search(r"\b([A-Z][a-z]+(?:-[a-z]+)?) checks in about", readme)
    said = WORDS.get(m.group(1).lower()) if m else None
    check("README's doctor check count is len(CHECKS) = %d" % n, said == n, m.group(1) if m else None)
    m = re.search(r"(\d+) read-only checks", runbook)
    check("RUNBOOK's doctor check count is %d" % n, m is not None and int(m.group(1)) == n,
          m.group(1) if m else None)
    co = class_order()
    m = re.search(r"(\d+) action classes", readme)
    check("README's action-class count is len(CLASS_ORDER) = %s" % (len(co) if co else "?"),
          co is not None and m is not None and int(m.group(1)) == len(co), m.group(1) if m else None)

    print("\nC66. nothing starts the background lane, so nothing says it is on demand")
    bgsrc = text("mlx-server-bg.sh")
    check("premise: mlx-server-bg.sh says nothing mints its start token",
          "Nothing in the dashboard mints" in flat(bgsrc))
    inst = text("install-services.sh")
    bgline = [ln for ln in inst.splitlines() if ln.lstrip().startswith("echo") and "$BG_LABEL" in ln]
    check("install-services.sh has a summary line for the background lane", len(bgline) == 1, bgline)
    check("and it does not call the lane ON-DEMAND", bgline and "ON-DEMAND" not in bgline[0], bgline)
    check("README no longer says both lanes are on demand", "both on demand" not in fr.lower())
    mer = re.search(r"bg\[mlx server :8081[^\]]*\]", readme)
    check("the architecture diagram does not call the 9B lane ON DEMAND",
          mer is not None and "ON DEMAND" not in mer.group(0), mer.group(0) if mer else None)

    print("\nC68. RUNBOOK follows the on-demand gate, the pinned venv and the Python floor")
    gate = re.search(r'echo "\[mlx-server\] (on-demand mode: no fresh start token — not loading the model)"',
                     text("mlx-server.sh"))
    check("premise: mlx-server.sh prints the gate message", gate is not None)
    if gate:
        check("RUNBOOK quotes what mlx-server.sh really prints", gate.group(1) in fb)
    check("RUNBOOK no longer says to leave ./mlx-server.sh running", "leave running" not in fb)
    check("RUNBOOK never says pip install --upgrade mlx-lm as a step",
          not re.search(r"^\s*pip install --upgrade mlx-lm", runbook, re.M))
    pin = re.search(r'^MLX_VLM_VERSION="\$\{MLX_VLM_VERSION:-([\d.]+)\}"', text("install-mlx-vlm-venv.sh"), re.M)
    pinv = pin.group(1) if pin else None
    check("RUNBOOK names the pinned mlx-vlm the venv script installs (%s)" % pinv,
          pinv is not None and ("mlx-vlm %s" % pinv) in fb, pinv)
    py = re.search(r"No Python (\d+\.\d+) or newer", text("install.sh"))
    check("premise: install.sh states its Python floor", py is not None)
    if py:
        check("RUNBOOK asks for Python %s+" % py.group(1), ("Python %s+" % py.group(1)) in fb)
        check("and not for an older one", "Python 3.11" not in fb)

    print("\nC69. what the updater writes under ~/.hermes is what update.sh says it writes")
    up = text("update.sh")
    head, _, body = up.partition("set -euo pipefail")
    check("update.sh no longer claims it NEVER touches ~/.hermes", "NEVER touches ~/.hermes" not in head)
    for needle, code_re in (("update.log", r'LOG="\$LOG_DIR/update\.log"'),
                            ("update-state.json", r'"\$STATE_DIR/update-state\.json"'),
                            ("update.lock", r'"\$STATE_DIR/update\.lock"'),
                            ("plugins/tool-budget", r'"\$HOME/\.hermes/plugins/tool-budget"'),
                            ("config.yaml", r'plugin_enable\.py" tool-budget')):
        writes = re.search(code_re, body) is not None
        check("update.sh writes %s" % needle, writes)
        check("and its header says so", needle in head)
        check("and so does the README's Updating section", needle in fr)
    check("README no longer says the updater never touches ~/.hermes",
          "never touches anything under `~/.hermes`" not in fr)
    check("the closing summary no longer says ~/.hermes was not touched",
          "Your data in ~/.hermes was not touched" not in body)

    print("\nC73. calendar context comes from icalBuddy, and the docs say so")
    srv = text("dashboard/server.py")
    check("premise: macos_calendar() shells out to icalBuddy", 'which("icalBuddy")' in srv)
    check("README no longer says there is no Apple Calendar fallback",
          "no Apple Calendar fallback" not in fr)
    req = readme.split("## Requirements", 1)[-1].split("\n## ", 1)[0]
    check("README's Requirements declare icalBuddy", "icalBuddy" in req and "ical-buddy" in req)
    check("RUNBOOK's prerequisites declare it too", "ical-buddy" in fb)

    print("\nR2-59. the pitch does not promise that no transcript leaves the Mac")
    check("premise: the Claude bridge exists (claude_think shells out to Claude)",
          "def claude_think(" in text("dashboard/aux_claudebridge.py"))
    check("README no longer says no transcript leaves the machine",
          "no transcript leaves the machine" not in fr.lower())
    check("nor that no inference does (an escalation is inference by Claude)",
          "no inference leaves" not in fr.lower())
    why = flat(readme.split("## Why", 1)[-1].split("\n## ", 1)[0])
    check("the Why section names what does leave: Telegram and Claude escalation",
          "Telegram" in why and "escalate to Claude" in why, why[-300:])


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="t-k12docs-")
    try:
        main(tmp)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check("the suite ran to the end", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nTESTS %d passed %d failed" % (CHECKS[0] - len(FAILS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
