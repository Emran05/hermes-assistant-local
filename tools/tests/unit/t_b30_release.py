#!/usr/bin/env python3
"""Regression test for B30 (docs/audits/2026-10-08-codebase-audit.md).

tools/release.sh ran its checks, THEN `git add -A` (shipping any untracked
file, unchecked), pushed main to origin and public, and only then found out
whether the tag already existed. release.yml built and published any tag
without a CI gate, and `git push public main` published every local commit —
history included — with nothing looking at it.

This drives the REAL script (or $T_B30_SCRIPT) inside throwaway repos whose
`origin` and `public` remotes are local bare repositories, with `gh` and
`sleep` shimmed on PATH and a throwaway HOME/git config — nothing here can
reach GitHub or the owner's repo. It asserts that:
  * only the index ships (untracked files and unstaged edits stay behind);
  * every refusal — home path in the history to be published, never-publish
    path, unconfirmed or mismatched commit range, existing tag, failing unit
    tier, unparseable inline script, diverged remote — happens BEFORE any push
    and leaves HEAD, the index and both remotes exactly as they were;
  * the script's home-path regex is CI's, and release.yml runs ci.yml as a
    required job with no ${{ }} step outputs interpolated into run: scripts.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
SCRIPT = os.environ.get("T_B30_SCRIPT") or os.path.join(REPO, "tools", "release.sh")
CHECKER = os.path.join(REPO, "tools", "tests", "lib", "check_inline_scripts.py")
CI_YML = os.path.join(REPO, ".github", "workflows", "ci.yml")
REL_YML = os.path.join(REPO, ".github", "workflows", "release.yml")

FAILS = []
PASSES = [0]
HOMEP = "/" + "Users" + "/" + "somebody"     # built, so this file never matches CI's grep


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s\n    | %s" % (name, str(extra)[-1500:].replace("\n", "\n    | ")))


TMP = tempfile.mkdtemp(prefix="t-b30-")
SHIMS = os.path.join(TMP, "bin")
os.makedirs(SHIMS)
GH_LOG = os.path.join(TMP, "gh.log")
for name, body in (
        ("gh", "#!/bin/sh\necho \"gh $*\" >> \"%s\"\nexit 0\n" % GH_LOG),
        ("sleep", "#!/bin/sh\nexit 0\n")):
    p = os.path.join(SHIMS, name)
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)
GITCONFIG = os.path.join(TMP, "gitconfig")
with open(GITCONFIG, "w") as f:
    f.write("[user]\n\tname = Release Test\n\temail = release-test@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n")
HOME = os.path.join(TMP, "home")
os.makedirs(HOME)

ENV = dict(os.environ)
for k in list(ENV):
    if k.startswith("GIT_") or k in ("HERMES_REPO", "RELEASE_PUBLISH_COMMITS", "SHIP_STAGED_ONLY"):
        ENV.pop(k)
ENV.update({"HOME": HOME, "GIT_CONFIG_GLOBAL": GITCONFIG, "GIT_CONFIG_NOSYSTEM": "1",
            # a stray HERMES_REPO (run.sh exports the real repo's) must not
            # redirect the candidate's checks — release.sh pins it itself
            "HERMES_REPO": REPO,
            "PATH": SHIMS + os.pathsep + os.environ.get("PATH", ""),
            "HERMES_PUBLIC_REPO": "example/not-a-repo"})


def git(cwd, *args, check_rc=True):
    p = subprocess.run(["git", "-C", cwd] + list(args), env=ENV, capture_output=True,
                       text=True, timeout=60)
    if check_rc and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), p.stderr))
    return p.stdout.strip()


def write(root, rel, text, mode=None):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    if mode:
        os.chmod(p, mode)


RUNSH = """#!/usr/bin/env bash
# stand-in for the unit tier: fails when the candidate carries a marker file
cd "$(dirname "$0")/../.."
if [ -f unit-tier-fails ]; then echo "FAILED: unit/t_fake.py"; exit 1; fi
echo "         TOTAL                         5      0      0"
echo "all green"
"""


def make_repo(name):
    root = os.path.join(TMP, name)
    work = os.path.join(root, "work")
    os.makedirs(work)
    for bare in ("origin.git", "public.git"):
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", os.path.join(root, bare)],
                       env=ENV, check=True)
    git(work, "init", "-q", "-b", "main")
    shutil.copy(SCRIPT, _mk(work, "tools/release.sh"))
    os.chmod(os.path.join(work, "tools", "release.sh"), 0o755)
    shutil.copy(CHECKER, _mk(work, "tools/tests/lib/check_inline_scripts.py"))
    write(work, "tools/tests/run.sh", RUNSH, 0o755)
    write(work, "VERSION", "1.0.0\n")
    write(work, "CHANGELOG.md", "# Changelog\n\n## [1.0.0]\n\n- first\n")
    write(work, "dashboard/app.py", "X = 1\n")
    write(work, "dashboard/app.js", "var x = 1;\n")
    write(work, "dashboard/index.html", "<!doctype html>\n<script>\nvar a = 1;\n</script>\n")
    write(work, "install.sh", "#!/bin/sh\necho hi\n", 0o755)
    write(work, ".gitignore", "*.pyc\n__pycache__/\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "1.0.0")
    for r in ("origin", "public"):
        git(work, "remote", "add", r, os.path.join(root, r + ".git"))
        git(work, "push", "-q", r, "main")
    return work, os.path.join(root, "origin.git"), os.path.join(root, "public.git")


def _mk(work, rel):
    p = os.path.join(work, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def stage_release(work, version="1.0.1"):
    write(work, "VERSION", version + "\n")
    with open(os.path.join(work, "CHANGELOG.md"), "a") as f:
        f.write("\n## [%s]\n\n- second\n" % version)
    write(work, "dashboard/app.py", "X = 2\n")
    git(work, "add", "VERSION", "CHANGELOG.md", "dashboard/app.py")


def run_release(work, version="1.0.1", publish=None, title=None):
    env = dict(ENV)
    if publish is not None:
        env["RELEASE_PUBLISH_COMMITS"] = str(publish)
    p = subprocess.run(["bash", os.path.join(work, "tools", "release.sh"), version,
                        title or "%s: test release" % version],
                       input="The body line.\n", env=env, capture_output=True, text=True,
                       timeout=300, cwd=work, start_new_session=True)   # no /dev/tty
    return p.returncode, p.stdout + p.stderr


def snapshot(work, origin, public):
    return {
        "head": git(work, "rev-parse", "HEAD"),
        "index": git(work, "write-tree"),
        "origin": git(origin, "rev-parse", "main"),
        "public": git(public, "rev-parse", "main"),
        "origin_tags": git(origin, "tag"),
        "public_tags": git(public, "tag"),
        "local_tags": git(work, "tag"),
    }


def refused(label, work, origin, public, before, rc, out, want=None):
    after = snapshot(work, origin, public)
    check("[%s] refused (non-zero exit)" % label, rc != 0, out)
    if want:
        check("[%s] says why (%s)" % (label, want), want in out, out)
    check("[%s] nothing pushed: origin main and tags unchanged" % label,
          after["origin"] == before["origin"] and after["origin_tags"] == before["origin_tags"], out)
    check("[%s] nothing pushed: public main and tags unchanged" % label,
          after["public"] == before["public"] and after["public_tags"] == before["public_tags"], out)
    check("[%s] HEAD, the index and local tags are untouched" % label,
          after["head"] == before["head"] and after["index"] == before["index"]
          and after["local_tags"] == before["local_tags"], out)


print("\n1. the happy path ships exactly the index")
work, origin, public = make_repo("happy")
stage_release(work)
write(work, "notes-private.txt", "my notes from %s/Desktop\n" % HOMEP)     # untracked
write(work, "dashboard/app.js", "var x = 2;  // unstaged edit\n")          # unstaged
staged = sorted(git(work, "diff", "--cached", "--name-only").split())
rc, out = run_release(work, publish=1)
check("the release succeeds", rc == 0, out)
head = git(work, "rev-parse", "HEAD")
check("origin main is the release commit", git(origin, "rev-parse", "main") == head, out)
check("public main is the release commit", git(public, "rev-parse", "main") == head, out)
check("tag v1.0.1 is on both remotes",
      git(origin, "tag") == "v1.0.1" and git(public, "tag") == "v1.0.1", out)
shipped = sorted(git(work, "show", "--name-only", "--format=", "HEAD").split())
check("the commit holds exactly the staged files", shipped == staged, "%r vs %r" % (shipped, staged))
check("an untracked file did not ship (no `git add -A`)",
      "notes-private.txt" not in git(public, "ls-tree", "-r", "--name-only", "main"), out)
check("an unstaged edit did not ship",
      git(work, "show", "HEAD:dashboard/app.js") == "var x = 1;", out)
check("both are still in the working tree, untouched",
      os.path.exists(os.path.join(work, "notes-private.txt"))
      and "unstaged edit" in open(os.path.join(work, "dashboard/app.js")).read(), out)
check("title and body are the commit message",
      git(work, "log", "-1", "--format=%s%n%b") == "1.0.1: test release\nThe body line.", out)
check("the release workflow was watched (gh shim called)",
      os.path.exists(GH_LOG) and "run list" in open(GH_LOG).read(), out)
check("the unit tier ran on the candidate", "TOTAL" in out, out)

print("\n2. refusals happen before any push")
work, origin, public = make_repo("nothing")
rc, out = run_release(work, publish=1)
check("[nothing staged] refused", rc != 0 and "nothing is staged" in out, out)

work, origin, public = make_repo("tagpublic")
git(public, "tag", "v1.0.1", "main")                     # someone already tagged public
stage_release(work)
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=1)
refused("tag on public", work, origin, public, before, rc, out, "already exists on public")

work, origin, public = make_repo("taglocal")
git(work, "tag", "v1.0.1")
stage_release(work)
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=1)
refused("tag exists locally", work, origin, public, before, rc, out, "already exists locally")

work, origin, public = make_repo("history")
write(work, "docs/debug.md", "log at %s/Library/Logs\n" % HOMEP)
git(work, "add", "docs/debug.md")
git(work, "commit", "-q", "-m", "debug notes")
git(work, "rm", "-q", "docs/debug.md")
git(work, "commit", "-q", "-m", "drop debug notes")       # the tree is clean again
stage_release(work)
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=3)
refused("home path in history", work, origin, public, before, rc, out, "home path is in the history")

work, origin, public = make_repo("history-msg")
write(work, "docs/x.md", "fine\n")
git(work, "add", "docs/x.md")
git(work, "commit", "-q", "-m", "copied from %s/Desktop/x.md" % HOMEP)
stage_release(work)
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=2)
refused("home path in a commit message", work, origin, public, before, rc, out,
        "home path is in the history")

work, origin, public = make_repo("never")
write(work, "data/state.db", "binary-ish\n")
git(work, "add", "-f", "data/state.db")
git(work, "commit", "-q", "-m", "oops, a database")
git(work, "rm", "-q", "data/state.db")
git(work, "commit", "-q", "-m", "remove it again")
stage_release(work)
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=3)
refused("never-publish path in history", work, origin, public, before, rc, out, "never-publish")

for kind, rel, text, want in (
        ("home path", "docs/resolved.md", "merged by hand in %s/src\n" % HOMEP,
         "home path is in the history"),
        ("never-publish path", ".env", "TOKEN=x\n", "never-publish")):
    # what only a MERGE commit adds (a conflict resolution): `git log -p` and
    # `--name-only` print nothing for a merge, so this rode along unseen
    work, origin, public = make_repo("merge-" + kind.split()[0])
    git(work, "checkout", "-q", "-b", "side")
    write(work, "docs/side.md", "side work\n")
    git(work, "add", "docs/side.md")
    git(work, "commit", "-q", "-m", "side work")
    git(work, "checkout", "-q", "main")
    write(work, "docs/main.md", "main work\n")
    git(work, "add", "docs/main.md")
    git(work, "commit", "-q", "-m", "main work")
    git(work, "merge", "-q", "--no-ff", "--no-commit", "side")
    write(work, rel, text)
    git(work, "add", "-f", rel)
    git(work, "commit", "-q", "-m", "merge side")
    git(work, "rm", "-q", rel)
    git(work, "commit", "-q", "-m", "tidy after the merge")       # the tree is clean again
    stage_release(work)
    before = snapshot(work, origin, public)
    rc, out = run_release(work, publish=5)
    refused("%s only a merge commit adds" % kind, work, origin, public, before, rc, out, want)

work, origin, public = make_repo("count")
write(work, "docs/wip.md", "private work in progress\n")
git(work, "add", "docs/wip.md")
git(work, "commit", "-q", "-m", "wip: not for the public repo")
stage_release(work)
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=1)
refused("range larger than confirmed", work, origin, public, before, rc, out,
        "public would receive 2 commit(s)")
check("[range larger than confirmed] the extra commit is listed", "wip: not for the public repo" in out, out)

work, origin, public = make_repo("noconfirm")
stage_release(work)
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=None)
refused("no terminal and no RELEASE_PUBLISH_COMMITS", work, origin, public, before, rc, out,
        "RELEASE_PUBLISH_COMMITS=1")

work, origin, public = make_repo("unitfail")
stage_release(work)
write(work, "unit-tier-fails", "x\n")
git(work, "add", "unit-tier-fails")
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=1)
refused("unit tier fails", work, origin, public, before, rc, out, "preflight failed")

work, origin, public = make_repo("inline")
stage_release(work)
write(work, "dashboard/index.html", "<!doctype html>\n<script>\nvar a = ;\n</script>\n")
git(work, "add", "dashboard/index.html")
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=1)
refused("inline script does not parse", work, origin, public, before, rc, out, "inline <script>")

work, origin, public = make_repo("homepath-tree")
stage_release(work)
write(work, "docs/setup.md", "cd %s/HermesAssistant\n" % HOMEP)
git(work, "add", "docs/setup.md")
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=1)
refused("home path in the staged tree", work, origin, public, before, rc, out, "personal home path")

work, origin, public = make_repo("version")
stage_release(work, version="1.0.1")
before = snapshot(work, origin, public)
rc, out = run_release(work, version="1.0.2", publish=1)
refused("VERSION mismatch", work, origin, public, before, rc, out, "not 1.0.2")

work, origin, public = make_repo("diverged")
other = os.path.join(TMP, "diverged", "other")
subprocess.run(["git", "clone", "-q", public, other], env=ENV, check=True)
write(other, "README", "someone else\n")
git(other, "add", "README")
git(other, "commit", "-q", "-m", "a commit only public has")
git(other, "push", "-q", "origin", "main")
stage_release(work)
before = snapshot(work, origin, public)
rc, out = run_release(work, publish=1)
refused("public diverged", work, origin, public, before, rc, out, "public/main is not an ancestor")

print("\n3. the gates match CI")
with open(SCRIPT) as f:
    rel = f.read()
with open(CI_YML) as f:
    ci = f.read()
m_ci = re.search(r"git grep -InE '([^']+)'.*?grep -vE '([^']+)'", ci, re.S)
m_re = re.search(r"^HOME_RE='([^']+)'", rel, re.M)
m_ok = re.search(r"^HOME_OK='([^']+)'", rel, re.M)
check("release.sh's home-path regex is CI's",
      m_ci and m_re and m_ci.group(1) == m_re.group(1), (m_ci and m_ci.groups(), m_re and m_re.group(1)))
check("release.sh's placeholder exclusion is CI's",
      m_ci and m_ok and m_ci.group(2) == m_ok.group(1), (m_ci and m_ci.groups(), m_ok and m_ok.group(1)))
code = "\n".join(ln for ln in rel.splitlines() if not ln.lstrip().startswith("#"))
check("release.sh never stages for you (no git add -A/--all/-u/.)",
      not re.search(r"git add (-A|--all|-u|--update|\.)(\s|$)", code), code[:0])
check("ci.yml can be called as a workflow (workflow_call)",
      re.search(r"^on:\n(?:  .*\n|\s*#.*\n)*?  workflow_call:", ci, re.M) is not None)

with open(REL_YML) as f:
    ry = f.read()


def job_block(text, job):
    m = re.search(r"^  %s:\n((?:    .*\n|\s*\n|\s*#.*\n)*)" % re.escape(job), text, re.M)
    return m.group(1) if m else ""


ci_job = job_block(ry, "ci")
rel_job = job_block(ry, "release")
check("release.yml runs ci.yml as a job",
      re.search(r"^    uses: \./\.github/workflows/ci\.yml\s*$", ci_job, re.M) is not None, ci_job)
check("the release job needs it",
      re.search(r"^    needs: \[?\s*ci\s*\]?\s*$", rel_job, re.M) is not None, rel_job[:400])
check("only the release job can write contents",
      re.search(r"^permissions:\n  contents: read", ry, re.M) is not None
      and "contents: write" in rel_job)
bad = []
in_run, run_indent = False, 0
for ln in ry.splitlines():
    stripped = ln.lstrip()
    ind = len(ln) - len(stripped)
    if in_run and stripped and ind <= run_indent:
        in_run = False
    if re.match(r"(- )?run: \|", stripped):
        in_run, run_indent = True, ind
        continue
    if in_run and "${{" in ln:
        bad.append(ln.strip())
check("no ${{ }} interpolation inside a run: script (env only)", not bad, bad)

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
