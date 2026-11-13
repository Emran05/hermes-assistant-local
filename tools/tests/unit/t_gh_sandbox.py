#!/usr/bin/env python3
"""GitHub sandbox: the hardened git runner and the two git stores (round 3,
R3-GH2) — dashboard/gh_git.py + the prepare/refresh half of
dashboard/aux_ghchanges.py (docs/plans/github-sandbox-2026-10.md §3.1, §3.2,
§3.5, §7 t_gh_sandbox).

Shares t_gh_changes.py's harness (real modules, a fake GitHub whose heads are
read from file:// bare repos). Proves:

  * gh_git's rules on their own: the remote URL shape (https://github.com, or
    file:// / loopback only in the unit tier), the slug and ref checks, the
    fetch refspec check, the hard -c flags, an environment built from nothing,
    and the base64/hex/url-encoded secret needles at every byte alignment;
  * prepare creates a 0700 mirror + a sandbox whose push URL is dead, whose
    credential helper is empty and whose hooks/ssh are off; an existing
    sandbox is never touched again; refresh only fetches;
  * PLANTED TRAPS HAVE NO EFFECT: sandbox hooks, core.fsmonitor, filter and
    diff drivers, a sandbox credential helper and insteadOf; a mirror config
    with url.*.insteadOf (wiped before the push: the "evil" remote receives
    nothing), a filter driver, hooksPath, alternates, commondir and
    info/attributes; an agent push into the mirror's refs/heads/main does not
    change the parent (GitHub's API is the truth) and rev-list integrity holds;
  * symlinks are stored as link text (a link to the .env is not read), a
    nested repository (gitlink) is refused;
  * the token reaches git ONLY as the GIT_CONFIG_COUNT env triple of the
    fetch/push calls: never in argv, never in any other call's env, never in
    the mirror's or the sandbox's .git/config.
"""
import base64
import json
import os
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t_gh_changes as H  # noqa: E402
import gh_git  # noqa: E402
import gh_api  # noqa: E402

check = H.check


def main():
    print("== gh_git: the remote URL, the slug, the ref ==")
    T = {"HERMES_TESTS_OFFLINE": "1"}
    check("https://github.com/<o>/<r>.git is the production shape",
          gh_git.remote_url("https://github.com", "octo/app", env={})
          == "https://github.com/octo/app.git")
    for base in ("https://github.com.evil.example", "https://evil.example", "http://github.com",
                 "ssh://git@github.com", "git@github.com:", "https://github.com@evil.example"):
        try:
            gh_git.remote_url(base, "octo/app", env=T)
            check("refused base %s" % base, False)
        except gh_git.GitError:
            check("refused base %s" % base, True)
    for base, env, ok in (("file:///tmp/r", {}, False), ("file:///tmp/r", T, True),
                          ("http://127.0.0.1:8765", T, True), ("http://127.0.0.1:8765", {}, False),
                          ("http://example.com", T, False), ("file:///tmp/../etc", T, False)):
        try:
            gh_git.remote_url(base, "octo/app", env=env)
            got = True
        except gh_git.GitError:
            got = False
        check("%s with %s -> %s" % (base, "test mode" if env else "production",
                                    "accepted" if ok else "refused"), got is ok)
    for full in ("../x", "octo/app/x", "octo", "octo/app.git", "-x/app", "octo/..", "", None):
        try:
            gh_git.remote_url("https://github.com", full, env={})
            check("bad repository %r refused" % (full,), False)
        except gh_git.GitError:
            check("bad repository %r refused" % (full,), True)
    check("branch_ref builds refs/heads/hermes/<slug>",
          gh_git.branch_ref("fix-login") == "refs/heads/hermes/fix-login")
    for slug in ("../main", "hermes/../x", "-f", "--force", "a", "Fix", "fix_login", "fix login",
                 "x" * 50, "fïx", "fix\nmain", "fix:main", "+main", "fix/main", "", None, 7):
        try:
            gh_git.branch_ref(slug)
            check("slug %r refused" % (slug,), False)
        except gh_git.GitError:
            check("slug %r refused" % (slug,), True)
    check("check_ref accepts refs/heads/hermes/<slug>",
          gh_git.check_ref("refs/heads/hermes/fix-login"))
    for ref in ("refs/heads/main", "refs/heads/hermes/../main", "refs/heads/hermes/-f",
                "refs/heads/hermes/a..b", "refs/tags/hermes/x", "refs/heads/hermes/x.lock",
                "HEAD", "refs/heads/hermes/"):
        check("check_ref refuses %s" % ref, not gh_git.check_ref(ref))

    print("\n== gh_git: flags and environment ==")
    flags = gh_git.hard_flags()
    pairs = {flags[i + 1] for i in range(0, len(flags), 2)}
    for want in ("core.hooksPath=/dev/null", "core.fsmonitor=false", "diff.external=",
                 "credential.helper=", "protocol.allow=never", "protocol.https.allow=always",
                 "protocol.file.allow=never", "http.followRedirects=false",
                 "transfer.fsckObjects=true", "submodule.recurse=false",
                 "core.attributesFile=/dev/null", "core.sshCommand=false"):
        check("hard flag %s" % want, want in pairs)
    check("file:// only when asked", "protocol.file.allow=always" in gh_git.hard_flags(True)
          and "protocol.file.allow=always" not in gh_git.hard_flags())
    os.environ.update({"GIT_DIR": "/evil", "HTTPS_PROXY": "http://evil:1",
                       "GIT_SSH_COMMAND": "evil", "GIT_CONFIG_COUNT": "1",
                       "GIT_CONFIG_KEY_0": "core.pager", "GIT_CONFIG_VALUE_0": "evil"})
    env = gh_git._env("/m", "/h", {})
    check("the environment is built from nothing (no inherited GIT_*/proxy)",
          env["GIT_DIR"] == "/m" and "HTTPS_PROXY" not in env and "GIT_SSH_COMMAND" not in env
          and "GIT_CONFIG_COUNT" not in env and env["PATH"] == "/usr/bin:/bin"
          and env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == "/dev/null"
          and env["GIT_NO_REPLACE_OBJECTS"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0", env)
    for k in ("GIT_DIR", "HTTPS_PROXY", "GIT_SSH_COMMAND", "GIT_CONFIG_COUNT",
              "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        os.environ.pop(k, None)
    m = gh_git.Mirror(os.path.join(H.BASE, "m.git"), os.path.join(H.BASE, "mw"))
    for bad in ("refs/heads/*:refs/heads/*", "+HEAD:refs/hermes-remote/x",
                "+refs/heads/main:refs/heads/main", "+refs/heads/../x:refs/hermes-remote/x",
                "+refs/heads/main:refs/hermes-remote/a b", "--upload-pack=evil"):
        try:
            m.fetch("file:///nonexistent", [bad])
            check("fetch refspec %r refused" % bad, False)
        except gh_git.GitError:
            check("fetch refspec %r refused" % bad, True)
    for bad_commit in ("HEAD", "main", "0" * 39, "--all"):
        try:
            m.push("file:///nonexistent", bad_commit, "fix")
            check("push of %r refused" % bad_commit, False)
        except gh_git.GitError:
            check("push of %r refused" % bad_commit, True)

    print("\n== gh_git: secret needles ==")
    secret = "Zx9-ab_CD12efGH34ijKL"
    needles = gh_git.secret_forms(secret)
    check("raw, url-encoded and hex forms", secret in needles
          and secret.encode().hex() in needles and secret.encode().hex().upper() in needles)
    ok_all = True
    for pre in (b"", b"a", b"ab", b"abc", b"abcd"):
        for post in (b"", b"!", b"!!", b"!!!"):
            blob = pre + secret.encode() + post
            for enc in (base64.b64encode(blob).decode(), base64.urlsafe_b64encode(blob).decode()):
                if not any(n in enc for n in needles):
                    ok_all = False
    check("a needle is found in the base64/base64url of the secret at EVERY alignment", ok_all)
    check("...and not in the base64 of something else", not any(
        n in base64.b64encode(b"x" * 64 + secret[::-1].encode()).decode() for n in needles))

    print("\n== prepare: two git stores ==")
    w = H.standard_world("sandbox")
    w.repo("octo/lib", {"lib.py": "x = 1\n"}, private=True)
    w.repo("octo/big", {"a": "a"}, private=True, size=3 * 1024 * 1024)
    ns = H.load(w)
    tok = H.connect(w, ns, ["octo/app", "octo/lib", "octo/big"])
    gg = ns["_ghc_git"]
    trace = []
    orig_exec = gg._exec

    def tracing_exec(argv, env, cwd, inp=None, timeout=gh_git.TIMEOUT, cap=None):
        trace.append({"argv": list(argv), "env": dict(env), "cwd": cwd})
        return orig_exec(argv, env, cwd, inp=inp, timeout=timeout, cap=cap)
    gg._exec = tracing_exec
    # the token as it would travel to GitHub: the https-keyed env triple
    ns["_gh_git_env"] = lambda: gh_api.git_auth_env(ns["_gh_token"](), "https://github.com")

    st, r = ns["call"]("POST", "/api/github/sandbox/refresh", {"repo": "octo/app"})
    check("refresh before prepare -> 409 not_prepared", st == 409
          and r.get("error") == "not_prepared", (st, r))
    st, r = ns["call"]("POST", "/api/github/sandbox/prepare", {"repo": "octo/app"})
    check("prepare answers ok + created", st == 200 and r.get("created") is True
          and r.get("prepared") is True, (st, r))
    sb = r.get("sandbox")
    mirror = ns["_ghc_mirror_path"]("octo/app")
    check("the sandbox is ~/HermesSandbox/github/octo/app",
          sb == os.path.join(ns["HOME"], "HermesSandbox", "github", "octo", "app"), sb)
    check("~/HermesSandbox is 0700", stat.S_IMODE(os.stat(os.path.join(
        ns["HOME"], "HermesSandbox")).st_mode) == 0o700)
    check("the mirror is a bare repo under github/mirrors, 0700",
          mirror == os.path.join(ns["GH_DIR"], "mirrors", "octo__app.git")
          and os.path.isfile(os.path.join(mirror, "HEAD"))
          and stat.S_IMODE(os.stat(mirror).st_mode) == 0o700, mirror)
    main_sha = w.heads("octo/app")["main"]
    mg = lambda *a: H.tgit(list(a), git_dir=mirror, check_ok=False)       # noqa: E731
    check("remote truth lands in refs/hermes-remote/main",
          mg("rev-parse", "refs/hermes-remote/main") == main_sha)
    check("...and is published to refs/heads/main for the sandbox to pull",
          mg("rev-parse", "refs/heads/main") == main_sha
          and mg("symbolic-ref", "HEAD") == "refs/heads/main")
    cfg = open(os.path.join(sb, ".git", "config")).read()
    check("sandbox: dead push URL", "pushurl = hermes-no-push://see-the-github-sandbox-skill"
          in cfg, cfg)
    check("sandbox: empty credential helper, hooks off, ssh off",
          "helper = \n" in cfg.replace("helper =\n", "helper = \n")
          and "hookspath = /dev/null" in cfg.lower() and "sshcommand = false" in cfg.lower(),
          cfg)
    check("sandbox: origin fetches from the dashboard's mirror",
          "url = file://" + mirror in cfg, cfg)
    p = subprocess.run(["/usr/bin/git", "-C", sb, "push", "origin", "HEAD:refs/heads/x"],
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": H.GIT_HOME,
                            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})
    check("a plain `git push` from the sandbox goes nowhere", p.returncode != 0
          and "hermes-no-push" in (p.stderr + p.stdout), p.stderr[-200:])
    check("status shows the sandbox prepared", [x for x in ns["_gh_status_payload"]()["repos"]
                                                if x["full_name"] == "octo/app"][0]["prepared"])
    check("the repo row records the fetch", isinstance(ns["_gh_repo"]("octo/app").get(
        "last_fetch"), float))

    H.write(sb, "NOTES.md", "the agent's uncommitted work\n")
    st, r = ns["call"]("POST", "/api/github/sandbox/prepare", {"repo": "octo/app"})
    check("prepare again: ok, not created", st == 200 and r.get("created") is False, (st, r))
    check("...the existing work tree is untouched",
          open(os.path.join(sb, "NOTES.md")).read() == "the agent's uncommitted work\n")
    os.unlink(os.path.join(sb, "NOTES.md"))
    new_main = w.remote_commit("octo/app", "main", {"CHANGELOG.md": "1\n"})
    st, r = ns["call"]("POST", "/api/github/sandbox/refresh", {"repo": "octo/app"})
    check("refresh fetches GitHub's new main into the mirror", st == 200
          and r.get("head") == new_main and mg("rev-parse", "refs/heads/main") == new_main, (st, r))
    check("...and never touches the sandbox", H.tgit(["rev-parse", "HEAD"], cwd=sb) == main_sha)
    H.tgit(["pull", "-q", "origin", "main"], cwd=sb)
    check("the agent's own `git pull` gets it from the mirror",
          H.tgit(["rev-parse", "HEAD"], cwd=sb) == new_main)
    for bad, code in (({"repo": "octo/nope"}, 403), ({"repo": "../x"}, 400), ({}, 400)):
        st, r = ns["call"]("POST", "/api/github/sandbox/prepare", bad)
        check("prepare %r -> %d" % (bad, code), st == code, (st, r))
    ns["GH_LIMITS"]["max_repo_kb"] = 2 * 1024 * 1024
    st, r = ns["call"]("POST", "/api/github/sandbox/prepare", {"repo": "octo/big"})
    check("a repository over 2 GB cannot be prepared (413)", st == 413
          and r.get("error") == "too_big", (st, r))
    os.makedirs(os.path.join(ns["HOME"], "elsewhere"))
    os.symlink(os.path.join(ns["HOME"], "elsewhere"),
               os.path.join(ns["HOME"], "HermesSandbox", "github", "octo", "lib"))
    st, r = ns["call"]("POST", "/api/github/sandbox/prepare", {"repo": "octo/lib"})
    check("a planted symlink on the sandbox path is refused (409)", st == 409
          and r.get("error") == "sandbox_symlink" and not os.listdir(
              os.path.join(ns["HOME"], "elsewhere")), (st, r))

    print("\n== planted traps ==")
    marks = os.path.join(w.tmp, "markers")
    os.makedirs(marks)

    def marker_script(name):
        # filters pass their input through; everything else just leaves a mark
        p = os.path.join(w.tmp, "trap-" + name + ".sh")
        tail = "exec cat\n" if name.startswith(("filter", "mirror-filter")) else "exit 1\n"
        with open(p, "w") as f:
            f.write("#!/bin/sh\ntouch '%s/%s'\n%s" % (marks, name, tail))
        os.chmod(p, 0o755)
        return p
    # an agent push into the mirror's refs/heads/main (and refs/hermes-remote/main):
    # hidden commits that must never ride along
    H.write(sb, "hidden.txt", "smuggled\n")
    H.tgit(["add", "hidden.txt"], cwd=sb)
    H.tgit(["-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "hidden"], cwd=sb)
    hidden_sha = H.tgit(["rev-parse", "HEAD"], cwd=sb)
    H.tgit(["-c", "core.hooksPath=/dev/null", "push", "-q", "file://" + mirror,
            "HEAD:refs/heads/main", "+HEAD:refs/hermes-remote/main"], cwd=sb)
    H.tgit(["rm", "-q", "hidden.txt"], cwd=sb)
    check("premise: the mirror's main now points at the agent's hidden commit",
          mg("rev-parse", "refs/heads/main") == hidden_sha)
    hooks = os.path.join(sb, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    for h in ("pre-commit", "post-commit", "post-checkout", "pre-push", "post-index-change",
              "reference-transaction", "fsmonitor-watchman", "pre-auto-gc", "post-update",
              "pre-receive", "update"):
        H.write(hooks, h, "#!/bin/sh\ntouch '%s/hook-%s'\n" % (marks, h), mode=0o755)
    evil = os.path.join(w.tmp, "evil")
    os.makedirs(os.path.join(evil, "octo"))
    H.tgit(["init", "-q", "--bare", os.path.join(evil, "octo", "app.git")])
    with open(os.path.join(sb, ".git", "config"), "a") as f:
        f.write('[core]\n\tfsmonitor = %s\n\thooksPath = %s\n\tpager = %s\n'
                '[filter "x"]\n\tclean = %s\n\tsmudge = %s\n'
                '[diff "x"]\n\ttextconv = %s\n[diff]\n\texternal = %s\n'
                '[credential]\n\thelper = !%s\n[url "file://%s/"]\n\tinsteadOf = file://%s/\n'
                % (marker_script("fsmonitor"), hooks, marker_script("pager"),
                   marker_script("filter-clean"), marker_script("filter-smudge"),
                   marker_script("textconv"),
                   marker_script("ext-diff"), marker_script("credential"), evil,
                   w.remote_root))
    H.write(sb, ".gitattributes", "* filter=x diff=x\n*.py filter=y\n")
    H.write(sb, "src/login.py", "COPY = 'Sign in'\n")
    env_file = os.path.join(ns["HOME"], ".hermes", ".env")
    os.symlink(env_file, os.path.join(sb, "env-link"))     # a link, never followed

    def plant_mirror():
        with open(os.path.join(mirror, "config"), "a") as f:
            f.write('[url "file://%s/"]\n\tinsteadOf = file://%s/\n'
                    '[filter "y"]\n\tclean = %s\n[core]\n\thooksPath = %s\n\tfsmonitor = %s\n'
                    % (evil, w.remote_root, marker_script("mirror-filter"), hooks,
                       marker_script("mirror-fsmonitor")))
        H.write(mirror, "info/attributes", "* filter=y\n")
        H.write(mirror, "objects/info/alternates", os.path.join(evil, "octo", "app.git",
                                                                 "objects") + "\n")
        H.write(mirror, "commondir", os.path.join(evil, "octo", "app.git") + "\n")
    plant_mirror()
    check("premise: the planted mirror config is in place before the request",
          "insteadOf" in open(os.path.join(mirror, "config")).read()
          and os.path.exists(os.path.join(mirror, "commondir")))
    trace.clear()
    st, r = H.request(ns, "octo/app", "trap-test", title="Copy")
    check("the change request goes through", st == 200, (st, r))
    cid = r.get("id")
    ch = H.get_change(ns, cid) or {}
    check("its parent is GitHub's main, not the mirror's planted refs/heads/main",
          ch.get("parent") == w.heads("octo/app")["main"] != hidden_sha, ch.get("parent"))
    paths = sorted(f["path"] for f in ch.get("files") or [])
    check("the diff is the work tree against GitHub's main (no hidden.txt)",
          "hidden.txt" not in paths and "src/login.py" in paths, paths)
    link = [f for f in ch.get("files") or [] if f["path"] == "env-link"]
    check("a symlink is stored as a link (mode 120000)", link and link[0]["new_mode"] == "120000",
          link)
    check("...its target text is in the diff, the .env content is not",
          "+" + env_file in ch.get("diff", "") and "sk-test-" not in ch.get("diff", ""))
    check("...so a link to the .env does not trip the known-secret block, but is flagged "
          "as leaving the repository", ch.get("approvable") and any(
              c["id"] == "C-symlink" and c["level"] == "warn" for c in ch.get("checks") or []),
          ch.get("checks"))
    req_trace = list(trace)
    plant_mirror()                       # again, right before the push
    check("premise: planted again right before the push",
          "insteadOf" in open(os.path.join(mirror, "config")).read())
    trace.clear()
    st, r = H.decide(ns, cid, "push_only")
    check("the push goes through", st == 200 and r.get("state") == "pushed", (st, r))
    push_trace = list(trace)
    pushed = r.get("pushed_sha")
    check("hermes/trap-test landed on the real remote",
          w.heads("octo/app").get("hermes/trap-test") == pushed)
    check("the planted insteadOf was wiped: the evil remote received nothing",
          H.tgit(["for-each-ref"], git_dir=os.path.join(evil, "octo", "app.git"),
                 check_ok=False) == "")
    hist = H.tgit(["rev-list", "refs/heads/hermes/trap-test"], git_dir=w.bare_path("octo/app"))
    check("the agent's hidden commit is not in what was pushed", hidden_sha not in hist)
    check("rev-list integrity: one new commit on GitHub's main",
          H.tgit(["rev-list", pushed, "--not", ch["parent"]],
                 git_dir=w.bare_path("octo/app")).split() == [pushed])
    check("NO trap fired (hooks, fsmonitor, filters, textconv, ext-diff, pager, credential "
          "helper, mirror filter/fsmonitor)", os.listdir(marks) == [], sorted(os.listdir(marks)))
    check("the mirror's foreign files were removed (alternates, commondir, info/attributes)",
          not any(os.path.exists(os.path.join(mirror, x)) for x in (
              "objects/info/alternates", "commondir", "info/attributes")))
    mcfg = open(os.path.join(mirror, "config")).read()
    check("the mirror's config is the template again", "insteadOf" not in mcfg
          and "filter" not in mcfg and "hooksPath = /dev/null" in mcfg, mcfg)

    # control: the same traps DO fire for a naive git run in the sandbox, so the
    # "no trap fired" check above can fail
    H.write(sb, "control.txt", "c\n")
    try:
        subprocess.run(["/usr/bin/git", "-C", sb, "add", "-A"], capture_output=True,
                       timeout=60, stdin=subprocess.DEVNULL,
                       env={"PATH": "/usr/bin:/bin", "HOME": H.GIT_HOME,
                            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})
    except subprocess.TimeoutExpired:
        pass
    fired = sorted(os.listdir(marks))
    check("control: a naive `git add` in the sandbox runs the planted filter / fsmonitor",
          "filter-clean" in fired or "fsmonitor" in fired, fired)
    os.unlink(os.path.join(sb, "control.txt"))
    for m_ in fired:
        os.unlink(os.path.join(marks, m_))

    print("\n== every git call: mirror-only, hardened, token only in the network env ==")
    calls = req_trace + push_trace
    sb_git = os.path.join(sb, ".git")
    check("no call ever used the sandbox's .git", not any(
        c["env"].get("GIT_DIR", "").startswith(sb_git) or c["cwd"].startswith(sb)
        or any(a.startswith("--git-dir") for a in c["argv"]) for c in calls))
    check("every call's GIT_DIR is the mirror", all(c["env"].get("GIT_DIR") == mirror
                                                    for c in calls),
          {c["env"].get("GIT_DIR") for c in calls})
    check("every call carries -c core.hooksPath=/dev/null and an empty credential helper",
          all("core.hooksPath=/dev/null" in c["argv"] and "credential.helper=" in c["argv"]
              for c in calls))
    # E09 adds a read-only `ls-files -o --exclude-standard` before the snapshot
    # (it measures what `add -A` would store, so over-limit content is refused
    # unwritten); it runs with the same private index and the mirror's GIT_DIR
    check("a work tree is only ever passed to `add` (snapshot) or `ls-files` (the size "
          "pre-check)", all(
              any(a.startswith("--work-tree=") for a in c["argv"]) ==
              ("add" in c["argv"] or "ls-files" in c["argv"]) for c in calls))
    check("...and every work-tree call uses a private GIT_INDEX_FILE", all(
        c["env"].get("GIT_INDEX_FILE") for c in calls
        if any(a.startswith("--work-tree=") for a in c["argv"])))
    pushes = [c for c in push_trace if "push" in c["argv"]]
    check("exactly one push", len(pushes) == 1, len(pushes))
    if pushes:
        tail = pushes[0]["argv"][pushes[0]["argv"].index("push"):]
        check("the push sends exactly <sha>:refs/heads/hermes/trap-test, nothing else",
              tail == ["push", "--porcelain", "--no-verify",
                       "file://%s/octo/app.git" % w.remote_root,
                       "%s:refs/heads/hermes/trap-test" % pushed], tail)
        check("...no force / mirror / tags / all", not any(
            a in pushes[0]["argv"] for a in ("--force", "-f", "--mirror", "--tags", "--all",
                                             "--delete")))
    net = [c for c in calls if any(x in c["argv"] for x in ("fetch", "push"))]
    rest = [c for c in calls if c not in net]
    check("the fetch and push calls carry the GIT_CONFIG_COUNT triple",
          net and all(c["env"].get("GIT_CONFIG_COUNT") == "1" and c["env"].get(
              "GIT_CONFIG_KEY_0") == "http.https://github.com/.extraheader" for c in net))
    check("no other call carries any of it", rest and not any(
        "GIT_CONFIG_COUNT" in c["env"] or "GIT_CONFIG_VALUE_0" in c["env"] for c in rest))
    argv_blob = json.dumps([c["argv"] for c in calls])
    check("the token is in no argv (raw or base64)", not H.leaks(argv_blob, tok))
    check("...and no git config file", not H.leaks(open(os.path.join(mirror, "config")).read()
                                                   + open(os.path.join(sb, ".git", "config"))
                                                   .read(), tok))
    gg._exec = orig_exec

    print("\n== nested repositories are refused ==")
    nested = os.path.join(sb, "vendor", "nested")
    os.makedirs(nested)
    H.tgit(["init", "-q", nested])
    H.write(nested, "z.txt", "z\n")
    H.tgit(["add", "z.txt"], cwd=nested)
    H.tgit(["-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "z"], cwd=nested)
    st, r = H.request(ns, "octo/app", "nested", title="Nested")
    check("a gitlink in the snapshot blocks the change", st == 200 and r.get("blocked") is True
          and any(c["id"] == "C-gitlink" for c in r.get("checks") or []), (st, r))
    st, r = H.decide(ns, r.get("id"), "push_pr")
    check("...and it cannot be approved", st == 422 and r.get("error") == "blocked", (st, r))
    check("...nothing reached the remote", "hermes/nested" not in w.heads("octo/app"))
    w.stop()
    H.finish()


if __name__ == "__main__":
    main()
