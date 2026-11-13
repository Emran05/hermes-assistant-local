#!/usr/bin/env python3
"""E56 (audit 2026-11-09): "config regenerated before every git call" was
check-then-use: a same-uid writer could rewrite the mirror's config between
the regeneration and git reading it.

Drives the REAL gh_git.Mirror against local bare repos. The race is made
deterministic with Mirror.trace, which runs after regen_config() and right
before git starts — exactly the window a racing writer uses.

  * every transport but https (and file:// only when asked) is pinned off on
    the command line, and so is core.bigFileThreshold, so a planted
    `[protocol "ext"] allow = always` + `url.ext::….insteadOf` runs nothing
    and a planted `core.bigFileThreshold = 1` cannot turn the review diff
    into "Binary files differ";
  * a config (or a borrow-from-elsewhere file) that differs after the call is
    TAMPERING: the call raises GitTampered and the registered tamper hooks
    are told (the pipeline holds writes and tells the owner);
  * aux_ghchanges registers that hook.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "dashboard"))
os.environ["HERMES_TESTS_OFFLINE"] = "1"
import gh_git  # noqa: E402

PASS, FAIL = [0], []


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print("  ok   " + name)
    else:
        FAIL.append(name)
        print("  FAIL %s %r" % (name, str(extra)[:400]))


BASE = tempfile.mkdtemp(prefix="e56-")
ENV = {"PATH": "/usr/bin:/bin", "HOME": BASE, "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid"}


def tgit(args, cwd=BASE):
    p = subprocess.run(["/usr/bin/git", "-c", "init.defaultBranch=main"] + args, cwd=cwd,
                       env=ENV, capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout.strip()


def main():
    print("== hard flags pin every transport and the binary threshold ==")
    fl = gh_git.hard_flags()
    for proto in ("ext", "fd", "ssh", "git", "http", "ftp", "ftps"):
        check("protocol.%s.allow=never is pinned" % proto, "protocol.%s.allow=never" % proto in fl)
    check("https stays allowed", "protocol.https.allow=always" in fl)
    check("file stays refused unless asked", "protocol.file.allow=never" in fl
          and "protocol.file.allow=always" in gh_git.hard_flags(allow_file=True))
    check("core.bigFileThreshold is pinned", any(a.startswith("core.bigFileThreshold=")
                                                for a in fl))

    # a remote with one commit, mirrored
    src = os.path.join(BASE, "src")
    tgit(["init", "-q", src])
    with open(os.path.join(src, "login.py"), "w") as f:
        f.write("COPY = 'Log in'\n")
    tgit(["add", "-A"], cwd=src)
    tgit(["commit", "-q", "-m", "one"], cwd=src)
    with open(os.path.join(src, "login.py"), "w") as f:
        f.write("COPY = 'Sign in'\n")
    tgit(["commit", "-q", "-am", "two"], cwd=src)
    a, b = tgit(["rev-parse", "HEAD~1", "HEAD"], cwd=src).split()
    work = os.path.join(BASE, "work")
    m = gh_git.Mirror(os.path.join(BASE, "m.git"), work)
    m.ensure()
    m.fetch("file://" + src, ["+refs/heads/main:refs/hermes-remote/main"])
    check("(the mirror fetched)", m.rev("refs/hermes-remote/main") == b)

    told = []
    gh_git.TAMPER_HOOKS.append(lambda path, why: told.append((path, why)))
    marker = os.path.join(BASE, "planted-ran")
    planted = ('[protocol "ext"]\n\tallow = always\n[url "ext::sh -c touch\\ %s"]\n'
               '\tinsteadOf = file://\n[core]\n\tbigFileThreshold = 1\n' % marker)

    def race(path, text):
        def tr(argv, env):
            with open(os.path.join(path, "config"), "a") as f:
                f.write(text)
        return tr

    print("\n== a writer racing regen_config ==")
    m.trace = race(m.path, planted)
    raised = None
    try:
        m.fetch("file://" + src, ["+refs/heads/main:refs/hermes-remote/main"])
    except gh_git.GitError as e:
        raised = e
    check("a planted ext:: insteadOf runs nothing", not os.path.exists(marker))
    check("...and the call fails as tampering", isinstance(raised, gh_git.GitTampered), raised)
    check("...the tamper hooks were told", told and told[-1][0] == m.path, told)
    raised = None
    try:
        ns = m.numstat(a, m.tree_of(b))
    except gh_git.GitError as e:
        raised, ns = e, None
    check("a planted bigFileThreshold=1 during a diff -> tampering, not a silent 'binary'",
          isinstance(raised, gh_git.GitTampered), (raised, ns))
    m.trace = None
    ns = m.numstat(a, m.tree_of(b))
    check("without the race the diff is text again (and regen restored the config)",
          ns.get("login.py") == (1, 1), ns)

    m.trace = race(m.path, "")
    alt = os.path.join(m.path, "objects", "info", "alternates")

    def plant_alt(argv, env):
        os.makedirs(os.path.dirname(alt), exist_ok=True)
        with open(alt, "w") as f:
            f.write("/tmp\n")
    m.trace = plant_alt
    raised = None
    try:
        m.rev("refs/hermes-remote/main")
    except gh_git.GitError as e:
        raised = e
    check("a borrow-from-elsewhere file planted during a call -> tampering",
          isinstance(raised, gh_git.GitTampered), raised)
    m.trace = None
    check("...and it is gone again on the next call", m.rev("refs/hermes-remote/main") == b
          and not os.path.exists(alt))

    print("\n== the pipeline registers a tamper hook ==")
    with open(os.path.join(REPO, "dashboard", "aux_ghchanges.py"), encoding="utf-8") as f:
        src_txt = f.read()
    check("aux_ghchanges.py registers _ghc_on_tamper in gh_git.TAMPER_HOOKS",
          "TAMPER_HOOKS" in src_txt and "def _ghc_on_tamper" in src_txt)

    print("\nTESTS %d passed %d failed" % (PASS[0], len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
