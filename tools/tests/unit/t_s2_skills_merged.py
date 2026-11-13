#!/usr/bin/env python3
"""S2 seam: the agent-facing skills say what the MERGED code does.

Fifteen clusters changed behaviour in parallel with the one that corrected
skills-snapshot/**, so several sentences went stale.  Each check below reads
the fact from the real code and requires the skill to state it (or no longer
state the old claim):

  * watchtower-author — the op list is exactly _WT_OPS; the logged suppression
    reasons are the ones _wt_supp_should_log lets through (cooldown / dedupe /
    backoff never reach the log); /api/brief/send defaults to a dry run; News
    off returns before any rule is evaluated.
  * think-with-claude — every refusal reason claude_think() can return is named
    (secret_in_prompt / secret_check_unavailable are new).
  * hub-cartographer / cron-conductor — the export path is aux_config's.
  * screen-oracle / mac-control — the computer_use read-only actions the
    approval-gate plugin leaves ungated are the ones the skills call free.
  * github-sandbox — 413 too_large, the binary / long-line warnings (with the
    server's own numbers), a renamed repository's `moved` row flag, removed
    repositories losing their mirror, the 422 on an over-long owner edit; and
    gh-sandbox.sh trims text previews the way it trims the diff.

Offline: reads files, runs the helper's pretty-printer on a canned answer.
"""
import ast
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
SK = os.path.join(REPO, "skills-snapshot")
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s  -- %s" % (name, str(extra)[:500]))


def rd(*p):
    with open(os.path.join(*p), encoding="utf-8") as f:
        return f.read()


def flat(s):
    return re.sub(r"\s+", " ", s)


print("== watchtower-author")
wt = rd(DASH, "aux_watchtower.py")
sk = rd(SK, "autonomous-ai-agents", "watchtower-author", "SKILL.md")
ops = re.findall(r'"([a-z_]+)": _op_', wt[wt.index("_WT_OPS = {"):wt.index("def watchtower_post_handler")])
m = re.search(r"The full op list \(verified from source\) is:\s*\n`([^`]+)`", sk)
listed = [x.strip() for x in m.group(1).split(",")] if m else []
check("the skill's op list is exactly _WT_OPS", sorted(listed) == sorted(ops), (listed, ops))
supp = ast.literal_eval(re.search(r"_SUPPRESS_OK = (\([^)]*\))", wt).group(1))
silent = ast.literal_eval(re.search(r'def _wt_supp_should_log[^\n]*\n\s+if reason in (\([^)]*\))', wt).group(1))
fs = flat(sk)
check("the code still never logs cooldown / dedupe / backoff", set(silent) == {"cooldown", "dedupe", "backoff"}, silent)
check("the skill no longer says every suppressed fire lands in the log",
      "Suppressed fires still land in the log" not in fs)
check("...it says cooldown/dedupe/backoff log nothing new",
      all("`%s`" % r in fs for r in silent) and "logs nothing new" in fs)
logged = [r for r in supp if r and r not in silent and r != "disabled"]
check("...and names every reason that IS logged (%s)" % ", ".join(logged),
      all("`%s`" % r in fs for r in logged))
check("/api/brief/send defaults to a dry run in code", 'dry_run = bool(b.get("dry_run", True))' in wt)
check("...and the skill says so", "`dry_run` defaults to true" in fs)
check("News off returns before any rule is evaluated (code)",
      re.search(r'def _rule_pass[\s\S]{0,200}if not _master_on\(cfg, "news"\):\s*\n\s*return', wt) is not None)
check("...and the skill says no rule of any type is evaluated", "no rule of any type is evaluated" in fs)
check("set_master takes strict booleans (code)", re.search(
    r'def _op_set_master\(b\):\s*\n\s*e = _wt_bool_args\(b, \("briefings", "news"\)\)', wt) is not None)
check("...and the skill says a string is refused", '`"false"` is refused' in fs)

print("== think-with-claude")
cb = rd(DASH, "aux_claudebridge.py")
tk = flat(rd(SK, "autonomous-ai-agents", "think-with-claude", "SKILL.md"))
reasons = set(re.findall(r'"reason": "([a-z_]+)"', cb)) | set(re.findall(r'return \("([a-z_]+)", CB_MSG', cb))
reasons |= set(re.findall(r'"(secret_[a-z_]+)"', cb))
check("the bridge's refusal reasons include the new credential pair",
      {"secret_in_prompt", "secret_check_unavailable"} <= reasons, reasons)
check("the skill names every refusal reason claude_think can return",
      all('"%s"' % r in tk for r in reasons), [r for r in reasons if '"%s"' % r not in tk])

print("== hub-cartographer / cron-conductor")
cfg = rd(DASH, "aux_config.py")
rel = re.search(r'CFG_SNAPSHOT_RELPATH = "([^"]+)"', cfg).group(1)
for name in ("hub-cartographer", "cron-conductor"):
    s = rd(SK, "autonomous-ai-agents", name, "SKILL.md")
    check("%s names the export path %s" % (name, rel), rel in s and "docs/state-snapshot.json" not in s)

print("== screen-oracle / mac-control")
gate = rd(REPO, "hermes-plugins", "approval-gate", "__init__.py")
ro = ast.literal_eval(re.search(r"_CU_READ_ONLY = frozenset\((\{[^}]*\})\)", gate).group(1))
mc = flat(rd(SK, "apple", "mac-control", "SKILL.md"))
so = flat(rd(SK, "apple", "screen-oracle", "SKILL.md"))
check("mac-control calls exactly the gate's read-only actions free",
      all("`%s`" % a in mc for a in ro) and "are not gated" in mc, ro)
check("screen-oracle's only computer_use action is `capture`, a read-only one",
      "capture" in ro and "zero `computer_use` actions except `capture`" in so)
check("neither names an action that does not exist (screenshot / screen_capture)",
      not re.search(r"action[`\s]+`?(screenshot|screen_capture)`", mc + so))

print("== github-sandbox")
gc = rd(DASH, "aux_ghchanges.py")
gs = flat(rd(SK, "autonomous-ai-agents", "github-sandbox", "SKILL.md"))
num = lambda n: int(eval(re.search(r"^%s = (.+)$" % n, gc, re.M).group(1).split("#")[0]))  # noqa: E731
check("413 too_large exists before the snapshot (code)", '_ghc_refuse(413, "too_large"' in gc)
check("...the skill names it", "413 `too_large`" in gs and "before anything is saved" in gs)
check("C-binary is a warning in code", '"C-binary", "warn"' in gc and '"C-longline", "warn"' in gc)
check("the skill says binaries and long lines are warnings the owner must tick, and not to include them",
      "WARNING the owner must tick" in gs and "Do not include such files or lines" in gs)
# review R4-seams: a skill is the agent's instructions, so a sentence about a
# check must never read as the way around it ("avoid the warning: wrap ...")
check("...and never to reshape content to get under a warning",
      "never encode, split, pad, re-wrap or rename content to get under one" in gs
      and "leave it as it is" in gs)
check("...with no 'avoid the warning by ...' recipe", "Avoid them" not in gs
      and "wrap long lines" not in gs)
check("...with the server's own numbers (%d characters, %d blanks, %d columns)"
      % (num("GHC_LONGLINE"), num("GHC_WS_RUN"), num("GHC_WS_COLS")),
      "longer than %d characters" % num("GHC_LONGLINE") in gs
      and "%d blank characters" % num("GHC_WS_RUN") in gs and "%d columns" % num("GHC_WS_COLS") in gs)
check("...and the caps (title %d, description %d KB)" % (num("GHC_TITLE_MAX"), num("GHC_BODY_MAX") // 1024),
      "%d characters" % num("GHC_TITLE_MAX") in gs and "%d KB" % (num("GHC_BODY_MAX") // 1024) in gs)
check("an over-long owner edit is a 422 in code", '_ghc_refuse(422, "body_too_long"' in gc)
check("...the skill says so", "refused there with a 422" in gs)
gh = rd(DASH, "aux_github.py")
check("a status row carries moved / moved_reason / removed (code)",
      all('"%s":' % k in gh for k in ("moved", "moved_reason", "removed")))
check("...the skill documents them", "`moved: true`" in gs and "`moved_reason`" in gs and "`removed: true`" in gs)
check("removing a repository deletes its mirror (code)", "_gh_delete_mirrors(repo)" in gh)
check("...the skill says a removed repository loses the mirror", "loses the dashboard's mirror" in gs)

sh = rd(SK, "autonomous-ai-agents", "github-sandbox", "scripts", "gh-sandbox.sh")
py = re.search(r"IFS= read -r -d '' SHOW_PY <<'PY' \|\| true\n(.*?)\nPY\n", sh, re.S).group(1)
canned = {"ok": True, "change": {"diff": "d" * 5000, "files": [
    {"path": "a.bin", "text_preview": "p" * 3000}, {"path": "b.bin", "text_preview": None}, "junk"]}}
r = subprocess.run([sys.executable, "-I", "-c", py], input=json.dumps(canned),
                   capture_output=True, text=True, timeout=30)
out = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
f0 = ((out.get("change") or {}).get("files") or [{}])[0]
check("gh-sandbox.sh trims a long text preview to 1,000 characters",
      isinstance(f0.get("text_preview"), str) and f0["text_preview"].startswith("p" * 1000)
      and "p" * 1001 not in f0["text_preview"] and "2000 more characters" in f0["text_preview"], r.stderr[-400:])
check("...and still trims the diff, and survives odd rows",
      "1000 more characters; the owner sees the full diff" in (out.get("change") or {}).get("diff", ""), out)

print("TESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
