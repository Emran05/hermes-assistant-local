#!/usr/bin/env python3
"""GitHub sandbox connector (round 3, R3-GH1) — the ambient-credential
DETECTOR (docs/plans/github-sandbox-2026-10.md §2.4).

Owner decision: ambient GitHub credentials on this Mac (the GitHub CLI's
login, git's osxkeychain/store helper, SSH keys, tokens in the environment)
are DETECTED and the owner is guided with exact commands — never changed
automatically. The detector reads names, attributes and fingerprints only.

Runs the REAL aux_github.py on a fake HOME whose gh hosts.yml, ~/.ssh,
~/.git-credentials and environment all hold canary secrets, with stub
`git` / `security` / `ssh-add` / `gh` binaries on GH_AMBIENT_PATH (the t_k01
grab.sh stub pattern). Every stub logs its argv; `security` prints a password
ONLY if asked with -g/-w, and `gh` drops a marker if it is ever executed.
Proves: every finding is detected with its exact guidance commands, no canary
reaches the result, `security` is never asked for a secret, `gh` is never run,
and nothing in HOME changes.
"""
import hashlib
import json
import os
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _gh_fake  # noqa: E402

PASS, FAIL = [0], []


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print("  ok   " + name)
    else:
        FAIL.append(name)
        print("  FAIL %s %r" % (name, extra))


CANARIES = ("gho_GHSECRETVALUE1234567890abcdef", "KEYCHAIN-SECRET-VALUE",
            "SSHSECRETMATERIAL", "GITSTORESECRET", "ghp_ENVSECRETVALUE0987654321")
for k in ("GH_CONFIG_DIR", "XDG_CONFIG_HOME", "GITHUB_TOKEN", "GH_TOKEN",
          "GH_ENTERPRISE_TOKEN", "GITHUB_PAT", "HERMES_UPDATE_TOKEN", "SSH_AUTH_SOCK"):
    os.environ.pop(k, None)

fake = _gh_fake.FakeGitHub()          # never called by the detector; load needs a base
BASE = fake.start()


def stub(bindir, name, body):
    p = os.path.join(bindir, name)
    with open(p, "w") as f:
        f.write("#!/bin/bash\n" + body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)


def world(git_out, sec_rc=0, ssh_rc=0, gh_hosts=True, ssh_cfg=True, store=True):
    ns = _gh_fake.load_connector(BASE)
    home = ns["HOME"]
    bindir = os.path.join(home, "stub-bin")
    os.makedirs(bindir)
    log = os.path.join(home, "argv.log")
    open(log, "w").close()
    marker = os.path.join(home, "GH-WAS-RUN")
    gitout = os.path.join(home, "git.out")
    with open(gitout, "w") as f:
        f.write(git_out)
    stub(bindir, "git", 'printf "git %%s\\n" "$*" >> "%s"\n'
                        'if [ "$1" = "config" ]; then\n'
                        '  [ -s "%s" ] || exit 1\n  /bin/cat "%s"; exit 0\nfi\n'
                        'echo "unexpected git use" >> "%s"; exit 9\n' % (log, gitout, gitout, log))
    stub(bindir, "security", 'printf "security %%s\\n" "$*" >> "%s"\n'
                             'for a in "$@"; do case "$a" in -g|-w) '
                             'echo "password: \\"KEYCHAIN-SECRET-VALUE\\""; '
                             'echo "KEYCHAIN-SECRET-VALUE" >&2;; esac; done\n'
                             'echo \'keychain: "/Users/you/Library/Keychains/login.keychain-db"\'\n'
                             'echo \'    "acct"<blob>="octo"\'\n'
                             'exit %d\n' % (log, sec_rc))
    stub(bindir, "ssh-add", 'printf "ssh-add %%s\\n" "$*" >> "%s"\n'
                            'echo "256 SHA256:abcdEFGH octo@mac (ED25519)"\nexit %d\n'
                            % (log, ssh_rc))
    stub(bindir, "gh", '/usr/bin/touch "%s"; echo gho_GHSECRETVALUE1234567890abcdef\n' % marker)
    if gh_hosts:
        d = os.path.join(home, ".config", "gh")
        os.makedirs(d)
        with open(os.path.join(d, "hosts.yml"), "w") as f:
            f.write("github.com:\n    oauth_token: gho_GHSECRETVALUE1234567890abcdef\n"
                    "    user: octo\n    git_protocol: https\n")
    if ssh_cfg:
        d = os.path.join(home, ".ssh")
        os.makedirs(d)
        with open(os.path.join(d, "config"), "w") as f:
            f.write("Host github.com\n  IdentityFile ~/.ssh/id_ed25519\n  UseKeychain yes\n")
        with open(os.path.join(d, "id_ed25519"), "w") as f:
            f.write("-----BEGIN OPENSSH PRIVATE KEY-----\nSSHSECRETMATERIAL\n"
                    "-----END OPENSSH PRIVATE KEY-----\n")
        with open(os.path.join(d, "id_ed25519.pub"), "w") as f:
            f.write("ssh-ed25519 AAAA octo@mac\n")
    if store:
        with open(os.path.join(home, ".git-credentials"), "w") as f:
            f.write("https://octo:GITSTORESECRET@github.com\n")
    ns["GH_AMBIENT_PATH"] = bindir
    return ns, log, marker


def snapshot(home):
    out = {}
    for root, _d, files in os.walk(home):
        for fn in files:
            p = os.path.join(root, fn)
            if fn in ("argv.log",):
                continue
            with open(p, "rb") as f:
                out[p] = (hashlib.sha256(f.read()).hexdigest(), os.stat(p).st_mode)
    return out


def item(amb, iid):
    return next((i for i in amb["items"] if i["id"] == iid), {})


def cmds(it):
    return [c["cmd"] for c in it.get("commands") or []]


print("== everything present ==")
SYS_GIT = ("file:/Library/Developer/CommandLineTools/usr/share/git-core/gitconfig\t"
           "credential.helper osxkeychain\n")
ns, log, marker = world(SYS_GIT)
os.environ["SSH_AUTH_SOCK"] = os.path.join(ns["HOME"], "agent.sock")
os.environ["GITHUB_TOKEN"] = CANARIES[4]
before = snapshot(ns["HOME"])
amb = ns["_gh_ambient"](fresh=True)
after = snapshot(ns["HOME"])
dump = json.dumps(amb)
argv = open(log).read()
check("any = True", amb["any"] is True)
check("summary keys gh/keychain/ssh/env", all(k in amb for k in ("gh", "keychain", "ssh", "env")))
g = item(amb, "gh")
check("GitHub CLI login detected", g.get("present") is True and amb["gh"] is True, g)
check("...with the design's sentence", "`gh auth token`" in g.get("detail", ""))
check("...and that its token sits in plain text", g.get("plaintext") is True)
check("...and the exact commands", "gh auth logout --hostname github.com" in cmds(g)
      and "gh auth status" in cmds(g), cmds(g))
k = item(amb, "keychain")
check("git's Keychain password detected", k.get("present") is True and k.get("saved") is True, k)
check("...the helper is named with where it is set",
      k["helpers"] and k["helpers"][0]["value"] == "osxkeychain"
      and "CommandLineTools" in k["helpers"][0]["origin"], k.get("helpers"))
check("...with the exact commands",
      "printf 'protocol=https\\nhost=github.com\\n\\n' | git credential reject" in cmds(k)
      and 'git config --global credential.helper ""' in cmds(k), cmds(k))
s = item(amb, "ssh")
check("SSH key detected (agent has keys)", s.get("present") is True and s.get("agent") == "keys"
      and s.get("keys") == 1, s)
check("...key FILE NAMES only", s.get("key_files") == ["id_ed25519"], s.get("key_files"))
check("...github.com host block + UseKeychain noticed", s.get("github_host") is True
      and s.get("use_keychain") is True)
check("...with the exact commands", "ssh-add -D" in cmds(s)
      and "ssh-keygen -p -f ~/.ssh/id_ed25519" in cmds(s), cmds(s))
e = item(amb, "env")
check("env token detected by NAME", e.get("present") is True and e.get("names") == ["GITHUB_TOKEN"])
for c in CANARIES:
    check("canary %s… never in the finding" % c[:10], c not in dump)
check("security was asked for attributes only (no -g / -w)",
      "security find-internet-password -s github.com" in argv
      and " -g" not in argv and " -w" not in argv, argv)
check("git was only asked for its helper config", "git config --show-origin --get-regexp" in argv
      and "unexpected git use" not in argv and "credential fill" not in argv, argv)
check("ssh-add only listed fingerprints", "ssh-add -l" in argv)
check("gh was never executed (`gh auth token` would print the token)", not os.path.exists(marker))
check("nothing in HOME changed", before == after)
check("the owner is told nothing is changed for them", "never changes" in amb["note"])

print("== cached; ?fresh=1 re-runs ==")
n_lines = len(open(log).read().splitlines())
st, s1 = ns["call"]("GET", "/api/github/status")
check("status carries the ambient finding", st == 200 and s1["ambient"]["any"] is True)
check("...from the cache (no new probes)", len(open(log).read().splitlines()) == n_lines)
st, a2 = ns["call"]("GET", "/api/github/ambient", query={"fresh": "1"})
check("?fresh=1 runs the probes again", st == 200 and
      len(open(log).read().splitlines()) > n_lines)
os.environ.pop("GITHUB_TOKEN", None)

print("== an empty helper resets the list (no Keychain lookup at all) ==")
ns, log, _m = world(SYS_GIT + "file:/u/.gitconfig\tcredential.helper \n", store=False)
amb = ns["_gh_ambient"](fresh=True)
k = item(amb, "keychain")
check("reset helper -> not present", k.get("present") is False, k)
check("security was not even asked", "security" not in open(log).read())

print("== store helper + ~/.git-credentials ==")
ns, log, _m = world("file:/u/.gitconfig\tcredential.helper store\n")
amb = ns["_gh_ambient"](fresh=True)
k = item(amb, "keychain")
check("plain-text store detected", k.get("present") is True and k.get("store_file") is True, k)
check("...said plainly", "plain text" in k.get("detail", ""))
check("...the stored password never read", "GITSTORESECRET" not in json.dumps(amb))

print("== gh auth setup-git: git borrows the CLI's login ==")
ns, log, _m = world("file:/u/.gitconfig\tcredential.https://github.com.helper "
                    "!/opt/homebrew/bin/gh auth git-credential\n", store=False)
amb = ns["_gh_ambient"](fresh=True)
k = item(amb, "keychain")
check("the gh credential helper is a finding", k.get("present") is True
      and k.get("gh_helper") is True, k)
check("...with the command that removes it",
      "git config --global --unset-all credential.https://github.com.helper" in cmds(k), cmds(k))
check("...and gh itself was still never run", not os.path.exists(_m))

print("== Keychain helper but no saved password ==")
ns, log, _m = world(SYS_GIT, sec_rc=44, store=False)
amb = ns["_gh_ambient"](fresh=True)
check("security exit 44 -> not present", item(amb, "keychain").get("present") is False
      and item(amb, "keychain").get("saved") is False)

print("== nothing at all ==")
os.environ.pop("SSH_AUTH_SOCK", None)
ns, log, _m = world("", ssh_rc=2, gh_hosts=False, ssh_cfg=False, store=False)
amb = ns["_gh_ambient"](fresh=True)
check("any = False", amb["any"] is False, [(i["id"], i.get("present")) for i in amb["items"]])
check("no agent socket: ssh-add is not run, agent unknown",
      item(amb, "ssh").get("agent") == "unknown" and "ssh-add" not in open(log).read())
check("no commands offered when nothing is found",
      all(not i.get("commands") for i in amb["items"]))

print("== a missing tool is skipped, never fatal ==")
ns = _gh_fake.load_connector(BASE)
amb = ns["_gh_ambient"](fresh=True)
check("empty PATH: still a well-formed answer", isinstance(amb.get("items"), list)
      and len(amb["items"]) == 4 and amb["any"] is False)

print("== ?scan=0: a background status read runs no local probe ==")
ns, log, _m = world(SYS_GIT)
os.environ["SSH_AUTH_SOCK"] = os.path.join(ns["HOME"], "agent.sock")
n0 = len(open(log).read().splitlines())
st, sb = ns["call"]("GET", "/api/github/status", query={"scan": "0"})
check("status?scan=0 answers 200", st == 200, st)
check("...and ran no git/security/ssh-add probe (the UI's 60 s poll)",
      len(open(log).read().splitlines()) == n0, open(log).read())
check("...and carries pending_ids as a list", isinstance(sb.get("pending_ids"), list), sb.get("pending_ids"))
st, sd = ns["call"]("GET", "/api/github/status")
check("a plain status read still runs them on a cold cache",
      st == 200 and len(open(log).read().splitlines()) > n0)

fake.stop()
print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
