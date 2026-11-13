#!/usr/bin/env python3
"""skills-snapshot: the agent's operating contract must match the code.

Audit 2026-10-21, "What is still not covered" item 1 (the completeness critic):
  (a) cron-conductor / hub-cartographer told the agent to POST
      /api/config/export, which writes docs/state-snapshot.json into the
      install checkout and then blocks the next software update (C64);
  (b) cron-conductor / skill-forge said `hermes -z` "cannot approve anything"
      and fails closed — it runs in YOLO mode and auto-approves (C36);
  (c) hub-cartographer called /api/chat/approve "user-only" — nothing enforces
      that (B02);
  (d) screen-oracle/scripts/grab.sh put the app name (agent-supplied, or the
      frontmost process's own name) unescaped into an AppleScript heredoc.
Plus the critic's check: every route row in a SKILL.md table exists in the
dashboard with that method.

Audit 2026-11-09 E65: every computer_use action a skill names is in Hermes'
enum, every skill a SKILL.md sends the agent to exists (and none is one the
owner suppressed), watchtower-author's op list is the dispatcher's and it
states the News switch and the brief dry-run default, the export path and the
escalation default are today's, mirror-check proposes its cron job, and every
/api route any skill or script mentions exists.

Offline: grab.sh runs against stub `osascript` / `screencapture` binaries on
PATH and a sandbox HERMES_HOME — nothing touches the real screen.
"""
import glob
import os
import re
import stat
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SKILLS = os.path.join(REPO, "skills-snapshot")
DASH = os.path.join(REPO, "dashboard")

PASSES, FAILS = [0], []


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  PASS  " + name)
    else:
        FAILS.append(name)
        print("  FAIL  " + name + (("  — " + str(extra)[:400]) if extra else ""))


def skill_texts():
    out = {}
    for p in glob.glob(os.path.join(SKILLS, "**", "*"), recursive=True):
        if not os.path.isfile(p) or "you-model" in p:      # k09 owns you-model*
            continue
        if not p.endswith((".md", ".sh", ".py", ".applescript", ".jxa")):
            continue
        with open(p, encoding="utf-8", errors="replace") as fh:
            out[os.path.relpath(p, SKILLS)] = fh.read()
    return out


# ---------------------------------------------------------------------------
# (d) grab.sh — the app name never becomes AppleScript source
# ---------------------------------------------------------------------------
STUB_OSA = r'''#!/bin/bash
# stub osascript: log how it was called; never runs AppleScript
log="$STUB_LOG"
if [ "${1:-}" = "-e" ]; then
  printf 'E\t%s\n' "$2" >> "$log"
  printf '%s\n' "$STUB_FRONT"
  exit 0
fi
script="$(cat)"
printf 'SCRIPT\t%s\n' "$(printf '%s' "$script" | tr '\n' ' ')" >> "$log"
[ "${1:-}" = "-" ] && shift            # `osascript - arg...`: script on stdin
for a in "$@"; do printf 'ARG\t%s\n' "$(printf '%s' "$a" | tr '\n' ' ')" >> "$log"; done
echo "10,20,300,200"
'''
STUB_CAP = r'''#!/bin/bash
# stub screencapture: write a non-empty PNG-sized file at the last argument
for last in "$@"; do :; done
head -c 30000 /dev/zero > "$last"
'''


def run_grab(args, front_name="Safari"):
    work = tempfile.mkdtemp(prefix="k01-grab-")
    bindir = os.path.join(work, "bin")
    os.makedirs(bindir)
    for name, body in (("osascript", STUB_OSA), ("screencapture", STUB_CAP)):
        p = os.path.join(bindir, name)
        with open(p, "w") as fh:
            fh.write(body)
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    log = os.path.join(work, "osa.log")
    open(log, "w").close()
    env = {"PATH": bindir + ":/usr/bin:/bin", "HOME": work,
           "HERMES_HOME": os.path.join(work, ".hermes"),
           "STUB_LOG": log, "STUB_FRONT": front_name}
    grab = os.path.join(SKILLS, "apple", "screen-oracle", "scripts", "grab.sh")
    p = subprocess.run(["bash", grab] + args, env=env, capture_output=True,
                       text=True, timeout=60)
    with open(log) as fh:
        rows = [l.rstrip("\n").split("\t", 1) for l in fh if "\t" in l]
    return p, rows


def grab_checks():
    print("grab.sh: the app name is an osascript argument, never script text")
    evil = 'Safari" to quit\ntell application "Finder" to delete every item of home\n--'
    p, rows = run_grab(["window", evil])
    scripts = [r[1] for r in rows if r[0] == "SCRIPT"]
    argv = [r[1] for r in rows if r[0] == "ARG"]
    check("(d) window \"<name>\": the name is not in the AppleScript source",
          scripts and all("Finder" not in s and "delete every" not in s for s in scripts),
          scripts)
    check("(d) ... it arrives intact as an argument",
          argv == [evil.replace("\n", " ")], argv)
    check("(d) ... and the capture still happens with the returned bounds",
          p.returncode == 0 and "/window-" in p.stdout, (p.returncode, p.stdout, p.stderr))
    p, rows = run_grab(["frontmost"], front_name=evil)
    scripts = [r[1] for r in rows if r[0] == "SCRIPT"]
    check("(d) frontmost: a hostile process name is not in the AppleScript source",
          scripts and all("Finder" not in s for s in scripts), scripts)
    p, rows = run_grab(["window", "Safari"])
    check("(d) an ordinary app name still works",
          p.returncode == 0 and [r[1] for r in rows if r[0] == "ARG"] == ["Safari"],
          (p.returncode, rows))


# ---------------------------------------------------------------------------
# (a)(b)(c) what the skills tell the agent
# ---------------------------------------------------------------------------
def text_checks():
    print("SKILL.md: owner-only routes and approval claims")
    texts = skill_texts()
    md = {k: v for k, v in texts.items() if k.endswith(".md")}
    calls = []
    for k, v in texts.items():
        for m in re.finditer(r"curl[^\n]*?/api/(config/export|config/import|chat/approve)", v):
            calls.append((k, m.group(0)[:120]))
        for m in re.finditer(r"-X\s*POST[^\n]*/api/(config/export|config/import|chat/approve)", v):
            calls.append((k, m.group(0)[:120]))
    check("(a)(c) no skill or script tells the agent to call config/export, "
          "config/import or chat/approve", not calls, calls)

    hub = md.get(os.path.join("autonomous-ai-agents", "hub-cartographer", "SKILL.md"), "")
    row = [l for l in hub.splitlines() if l.startswith("| `/api/chat/approve`")]
    # "owner-only, never call" is the rule whether or not B02's fix has landed;
    # do not pin the "NOT enforced" wording, which that fix will make stale.
    check("(c) hub-cartographer: /api/chat/approve is owner-only and never called",
          row and "owner-only" in row[0] and "Never call it" in row[0], row)
    row = [l for l in hub.splitlines() if l.startswith("| `/api/config/export`")]
    check("(a) hub-cartographer: /api/config/export is marked owner-only, never call",
          row and "owner-only" in row[0] and "Never call it" in row[0], row)
    row = [l for l in hub.splitlines() if l.startswith("| `/api/shortcuts/run`")]
    check("(c) hub-cartographer: redeeming your own Shortcuts ticket is forbidden",
          row and re.search(r"redeem[^|]*\bnever\b", row[0], re.I), row)

    bad = []
    for k, v in md.items():
        flat = re.sub(r"\s+", " ", v)
        for pat in (r"cannot approve anything", r"approvals? fail closed there",
                    r"in cron/oneshot it fails closed",
                    r"-z[^.]{0,80}fail(s)? closed"):
            m = re.search(pat, flat)
            if m and "does NOT" not in flat[max(0, m.start() - 60):m.end() + 60]:
                bad.append((k, m.group(0)))
    check("(b) no skill claims `hermes -z` fails closed / cannot approve", not bad, bad)
    cc = re.sub(r"\s+", " ", md.get(os.path.join(
        "autonomous-ai-agents", "cron-conductor", "SKILL.md"), ""))
    check("(b) cron-conductor says -z runs in YOLO mode",
          "YOLO" in cc and "hermes -z" in cc, "")
    mc = re.sub(r"\s+", " ", md.get(os.path.join("apple", "mac-control", "SKILL.md"), ""))
    check("C111: mac-control says where computer_use approval really comes from",
          "approval-gate" in mc and "only in the interactive CLI" in mc, "")


# ---------------------------------------------------------------------------
# the critic's route check: every table row exists with that method
# ---------------------------------------------------------------------------
def dashboard_routes():
    get, post = set(), set()
    for f in glob.glob(os.path.join(DASH, "*.py")):
        with open(f, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        for m in re.finditer(r'register_(get|post)\(\s*["\'](/api/[^"\']+)["\']', src):
            (get if m.group(1) == "get" else post).add(m.group(2))
    with open(os.path.join(DASH, "server.py"), encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    where = None
    for l in lines:
        m = re.match(r"    def (do_GET|do_POST|do_\w+|\w+)\(", l)
        if m:
            where = m.group(1)
        for r in re.findall(r'path\s*==\s*["\'](/api/[^"\']+)["\']', l):
            if where == "do_GET":
                get.add(r)
            elif where == "do_POST":
                post.add(r)
    return get, post


def route_checks():
    print("SKILL.md route tables vs the dashboard's registered routes")
    get, post = dashboard_routes()
    rows, bad = [], []
    for k, v in skill_texts().items():
        if not k.endswith(".md"):
            continue
        for m in re.finditer(r"^\| `(/api/[^`?]+)[^`]*` \| (GET|POST) \|", v, re.M):
            rows.append((k, m.group(1), m.group(2)))
    for k, path, meth in rows:
        if path not in (get if meth == "GET" else post):
            bad.append((k, meth, path))
    check("every route row (%d) exists in the dashboard with that method" % len(rows),
          rows and not bad, bad)


# ---------------------------------------------------------------------------
# E65 (2026-11-09 audit): action names, skills and facts a skill states
# ---------------------------------------------------------------------------
# Hermes 0.18's computer_use action enum (tools/computer_use/schema.py), copied
# so the unit tier needs no Hermes checkout. An unknown action is answered
# "unknown action" by the tool AND sent to an approval card by the gate.
CU_ACTIONS = frozenset((
    "capture", "click", "double_click", "right_click", "middle_click", "drag",
    "scroll", "type", "key", "set_value", "wait", "list_apps", "focus_app"))
# Skills the owner archived and suppressed on this install (the curator's
# .curator_suppressed list): a skill must never send the agent to them.
SUPPRESSED = ("computer-use", "claude-code", "google-workspace")


def wt_ops():
    with open(os.path.join(DASH, "aux_watchtower.py"), encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"_WT_OPS = \{(.*?)\n\}", src, re.S)
    return set(re.findall(r'"([a-z_]+)":', m.group(1))) if m else set()


def e65_checks():
    print("E65: action names, skill references and stated facts match the code")
    texts = skill_texts()
    md = {k: v for k, v in texts.items() if k.endswith(".md")}

    # computer_use actions
    bad = []
    for k, v in md.items():
        for line in v.splitlines():
            if "computer_use" not in line:
                continue
            for m in re.finditer(r"computer_use`?\*{0,2}\s*(?:tool,?\s*)?(?:action\s+)?`([a-z_]+)`", line):
                if m.group(1) not in CU_ACTIONS:
                    bad.append((k, m.group(1)))
            for m in re.finditer(r"computer_use`? (?:screenshot|switch_display)\b", line):
                bad.append((k, m.group(0)))
            for m in re.finditer(r"actions? except ((?:`[a-z_]+`[/, ]*(?:and |or )?)+)", line):
                for a in re.findall(r"`([a-z_]+)`", m.group(1)):
                    if a not in CU_ACTIONS:
                        bad.append((k, a))
            for m in re.finditer(r'"action"\s*:\s*"([a-z_]+)"', line):
                if m.group(1) not in CU_ACTIONS:
                    bad.append((k, m.group(1)))
    check("every computer_use action a skill names is in Hermes' enum", not bad, bad)

    # skill references
    have = set()
    for p in glob.glob(os.path.join(SKILLS, "**", "SKILL.md"), recursive=True):
        have.add(os.path.basename(os.path.dirname(p)))
    bad, sup = [], []
    for k, v in md.items():
        body = v.split("\n---", 2)[-1] if v.startswith("---") else v
        for m in re.finditer(r"`(?:[a-z0-9-]+/)?([a-z0-9-]+)` skill\b|\bskill_view `?([a-z0-9-]+)|"
                             r"\(see `[a-z0-9-]+/([a-z0-9-]+)`\)", body):
            name = m.group(1) or m.group(2) or m.group(3)
            if name and name not in have and name not in ("apple",):
                bad.append((k, name))
        # a skill reference: a related_skills entry, or the name in backticks
        # (alone or as a path segment) — not prose ("a computer-use session")
        # and not a tag
        refs = []
        for m in re.finditer(r"related_skills:\s*\[([^\]]*)\]", v):
            refs += [x.strip() for x in m.group(1).split(",")]
        for m in re.finditer(r"`([^`\n]+)`", v):
            if m.group(1).startswith("hermes "):      # a CLI command, not a skill
                continue
            refs += [seg for seg in re.split(r"[/\s]+", m.group(1)) if seg]
        for s_ in SUPPRESSED:
            if s_ in refs:
                sup.append((k, s_))
    check("every skill a SKILL.md sends the agent to is in skills-snapshot", not bad, bad)
    check("no skill names an owner-suppressed skill (%s)" % ", ".join(SUPPRESSED), not sup, sup)

    # watchtower ops
    ops = wt_ops()
    wa = md.get(os.path.join("autonomous-ai-agents", "watchtower-author", "SKILL.md"), "")
    used = set(re.findall(r'"op"\s*:\s*"([a-z_]+)"', wa))
    check("watchtower-author: every op it sends exists (%d)" % len(used),
          ops and used and used <= ops, sorted(used - ops))
    m = re.search(r"full op list[^:]*:\s*\n?(.*?)\.\s*\n", wa, re.S)
    listed = set(re.findall(r"`?([a-z_]+)`?", m.group(1))) & (ops | set(re.findall(r"[a-z_]+", m.group(1)))) if m else set()
    check("watchtower-author: its 'full op list' is the dispatcher's", listed == ops,
          (sorted(ops - listed), sorted(listed - ops)))
    flat = re.sub(r"\s+", " ", wa)
    check("watchtower-author: says the News switch silences every rule",
          re.search(r"master", flat) and re.search(r"News[^.]{0,120}(off|OFF)[^.]{0,200}(no rule|nothing|never)", flat), "")
    check("watchtower-author: /api/brief/send is a dry run unless dry_run is false",
          "without `dry_run`" not in flat and re.search(r'"dry_run"\s*:\s*false', flat)
          and re.search(r"dry.run[^.]{0,80}default", flat, re.I), "")
    check("watchtower-author: no POST to /api/settings (user-owned config)",
          not re.search(r"POST[^\n]{0,60}/api/settings", wa), "")

    # stated facts
    for name in ("hub-cartographer", "cron-conductor"):
        t = md.get(os.path.join("autonomous-ai-agents", name, "SKILL.md"), "")
        check("%s: the export path is today's (~/.hermes/dashboard), not docs/" % name,
              "docs/state-snapshot.json" not in t and "install directory" not in t
              and ("state-snapshot.json" not in t or "~/.hermes/dashboard/state-snapshot.json" in t), "")
    twc = re.sub(r"\s+", " ", md.get(os.path.join("autonomous-ai-agents", "think-with-claude", "SKILL.md"), ""))
    check("think-with-claude: escalation is OFF by default and refuses with escalation_off",
          "escalation_off" in twc and re.search(r"OFF by default|off by default", twc), "")
    mc = md.get(os.path.join("autonomous-ai-agents", "mirror-check", "SKILL.md"), "")
    sec3 = mc.split("## 3.", 1)[-1].split("## 4.", 1)[0]
    check("mirror-check: the nightly cron job is proposed, created only after the owner says yes",
          re.search(r"PROPOSE", sec3) and re.search(r"only after", sec3), sec3[:200])

    # routes: every /api path a skill or script mentions exists (a prefix
    # like "/api/memory endpoints" must prefix a real route)
    get, post = dashboard_routes()
    allr = get | post
    bad = []
    for k, v in texts.items():
        for m in re.finditer(r"/api/[A-Za-z0-9_/.\-]+", v):
            p = m.group(0).rstrip(".,)/`'\"")
            if p == "/api" or p in allr or any(r.startswith(p + "/") for r in allr):
                continue
            bad.append((k, p))
    check("every /api route a skill or script mentions exists", not bad, bad)


def main():
    grab_checks()
    text_checks()
    route_checks()
    e65_checks()
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
