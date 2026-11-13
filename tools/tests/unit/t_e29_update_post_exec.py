#!/usr/bin/env python3
"""Regression test for E29 — docs/audits/2026-11-09-codebase-audit.md.

bash reads update.sh through an open fd, and `git checkout` / rsync give the
file a NEW inode, so every step after the code swap ran from the OUTGOING
script's text. From the published 1.3.2 that meant approval-gate was never
linked or enabled and the Telegram gateway never restarted, and a second plain
update did nothing (already up to date).

Now:
  * after the swap update.sh execs the INCOMING update.sh with --post-update,
    so plugins, services, gateway and summary come from the new version; the
    update lock (fd 9, or the fd the dashboard passed) stays held across it;
  * for the first hop from an older update.sh (which still finishes on its own
    text but runs the new install-services.sh by path), install-services.sh
    asks the incoming update.sh for the plugin + gateway steps — only when an
    update runs it, never on a plain ./install-services.sh;
  * a target whose update.sh lacks the protocol (a downgrade) gets the steps
    from the outgoing text, as before.

Everything runs in scratch git repos with a scratch HOME. launchctl, pgrep,
gh, curl and sleep are shims on PATH (nothing reaches this Mac's launchd or the
network); the outgoing 1.3.2 script is the published text, kept as a fixture
(fixtures/e29_update_v1.3.2.sh) because CI checkouts carry no tags.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
OLD_UPDATE = os.path.join(_HERE, "fixtures", "e29_update_v1.3.2.sh")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s\n    | %s" % (name, str(extra)[-1500:].replace("\n", "\n    | ")))


TMP = tempfile.mkdtemp(prefix="t-e29-")
UID = os.getuid()

# launchctl: logs every call; the gateway is "loaded" (print answers 0); on a
# kickstart it records whether the update lock is still held by the update
LAUNCHCTL = r'''#!/bin/bash
printf '%s\n' "$*" >> "$E29_FX/launchctl.calls"
case "$1" in
  print) case "$2" in */ai.hermes.gateway) exit 0 ;; esac; exit 113 ;;
  kickstart)
    python3 - "$HOME/.hermes/dashboard/update.lock" >> "$E29_FX/lock.at-kickstart" <<'PY'
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("free")
except OSError:
    print("held")
PY
    exit 0 ;;
esac
exit 0
'''
SHIMS = {
    "launchctl": LAUNCHCTL,
    "pgrep": "#!/bin/sh\nexit 1\n",
    "gh": "#!/bin/sh\nexit 1\n",
    "curl": "#!/bin/sh\necho 'curl shim: no network in this test' >&2\nexit 7\n",
    "sleep": "#!/bin/sh\nexit 0\n",
}
SHIM = os.path.join(TMP, "shim")
os.makedirs(SHIM)
for n, body in SHIMS.items():
    p = os.path.join(SHIM, n)
    with open(p, "w") as f:
        f.write(body)
    os.chmod(p, 0o755)

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def git(*a, cwd=None):
    env = dict(os.environ, **GIT_ENV)
    p = subprocess.run(["git"] + list(a), cwd=cwd, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(a), p.stderr))
    return p.stdout


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def put_current(work, version, update_text=None):
    """the working tree as THIS checkout ships it (the files the hop uses)"""
    for d in ("hermes-plugins",):
        dst = os.path.join(work, d)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(os.path.join(REPO, d), dst,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in ("update.sh", "install-services.sh"):
        shutil.copy2(os.path.join(REPO, f), os.path.join(work, f))
    if update_text is not None:
        write(os.path.join(work, "update.sh"), update_text, 0o755)
    write(os.path.join(work, "VERSION"), version + "\n")


def put_old(work, version):
    """a 1.3.2-shaped tree: the published update.sh, a stub install-services.sh"""
    shutil.rmtree(os.path.join(work, "hermes-plugins"), ignore_errors=True)
    shutil.copy2(OLD_UPDATE, os.path.join(work, "update.sh"))
    os.chmod(os.path.join(work, "update.sh"), 0o755)
    write(os.path.join(work, "install-services.sh"),
          "#!/bin/bash\necho 'OLD install-services stub ran'\n", 0o755)
    write(os.path.join(work, "VERSION"), version + "\n")


def commit_tag(work, tag):
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", tag, "--allow-empty", cwd=work)
    git("tag", tag, cwd=work)


def scenario(name, steps, start_tag):
    """steps: [(tag, fn(work))] -> (install dir, home, fixtures dir)"""
    root = os.path.join(TMP, name)
    work = os.path.join(root, "work")
    os.makedirs(work)
    git("init", "-q", "-b", "main", cwd=work)
    for tag, fn in steps:
        fn(work)
        commit_tag(work, tag)
    origin = os.path.join(root, "origin.git")
    git("clone", "-q", "--bare", work, origin)
    install = os.path.join(root, "install")
    git("clone", "-q", origin, install)
    git("checkout", "-q", start_tag, cwd=install)
    home = os.path.join(root, "home")
    write(os.path.join(home, ".hermes", "config.yaml"),
          "model:\n  default: m\nplugins:\n  enabled:\n    - tool-budget\n")
    fx = os.path.join(root, "fx")
    os.makedirs(fx)
    return install, home, fx


def env_for(home, fx, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("HERMES_UPDATE_FROM", "HERMES_UPD_IN_POST", "HERMES_UPDATE_TOKEN",
                        "GITHUB_TOKEN", "GH_TOKEN")
           and not k.startswith("HERMES_UPD_")}
    env.update(GIT_ENV)
    env.update({"HOME": home, "E29_FX": fx, "TMPDIR": os.path.join(home, "tmp"),
                "PATH": ":".join([SHIM, os.path.dirname(sys.executable),
                                  "/usr/bin", "/bin", "/usr/sbin", "/sbin"])})
    os.makedirs(env["TMPDIR"], exist_ok=True)
    env.update(extra)
    return env


def run_terminal(install, home, fx, *args):
    p = subprocess.run(["/bin/bash", os.path.join(install, "update.sh")] + list(args),
                       env=env_for(home, fx), capture_output=True, text=True, timeout=180)
    return p.returncode, p.stdout + p.stderr


def run_dashboard(install, home, fx, *args):
    """the aux_update.py shape: lock taken first, fd passed, parent lets go"""
    lock = os.path.join(home, ".hermes", "dashboard", "update.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    p = subprocess.Popen(["/bin/bash", os.path.join(install, "update.sh")] + list(args),
                         env=env_for(home, fx, HERMES_UPDATE_FROM="dashboard"),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         pass_fds=(fd,))
    os.close(fd)
    out, _ = p.communicate(timeout=180)
    return p.returncode, out


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def enabled(home):
    sys.path.insert(0, os.path.join(REPO, "hermes-plugins"))
    import plugin_enable
    return plugin_enable.read_enabled(read(os.path.join(home, ".hermes", "config.yaml")))


def link(home, name):
    p = os.path.join(home, ".hermes", "plugins", name)
    return os.readlink(p) if os.path.islink(p) else None


def kickstarts(fx):
    return [l for l in read(os.path.join(fx, "launchctl.calls")).splitlines()
            if l.startswith("kickstart -k ") and l.endswith("/ai.hermes.gateway")]


def state(home):
    try:
        return json.loads(read(os.path.join(home, ".hermes", "dashboard", "update-state.json")))
    except ValueError:
        return {}


CUR_UPDATE = read(os.path.join(REPO, "update.sh"))
MARK_LINE = 'say "post-update steps from the incoming update.sh (mode $POST_UPDATE)"'
check("the current update.sh carries the protocol marker and the post-update banner",
      "\n# hermes-update-post-protocol: 1\n" in CUR_UPDATE and MARK_LINE in CUR_UPDATE)
check("the 1.3.2 fixture is the published text (no approval-gate, no gateway step)",
      "approval-gate" not in read(OLD_UPDATE) and "ai.hermes.gateway" not in read(OLD_UPDATE)
      and "tool-budget" in read(OLD_UPDATE))


# ---------------------------------------------------------------------------
print("\n1. the first hop from the published 1.3.2 update.sh (terminal run)")
# ---------------------------------------------------------------------------
for how in ("terminal", "dashboard"):
    install, home, fx = scenario("hop132-" + how, [
        ("v1.3.2", lambda w: put_old(w, "1.3.2")),
        ("v1.4.0", lambda w: put_current(w, "1.4.0")),
    ], "v1.3.2")
    runner = run_terminal if how == "terminal" else run_dashboard
    rc, out = runner(install, home, fx, "--yes", "--channel", "stable")
    check("[%s] the update succeeds and lands on 1.4.0" % how,
          rc == 0 and read(os.path.join(install, "VERSION")).strip() == "1.4.0", out)
    check("[%s] the OLD script finished the run (its own summary)" % how,
          "Your data in ~/.hermes was not touched." in out, out)
    check("[%s] the new install-services.sh asked the incoming update.sh for the steps" % how,
          "an older update.sh is running this file" in out
          and "post-update steps from the incoming update.sh (mode 2)" in out, out)
    check("[%s] approval-gate is linked to the installed tree" % how,
          link(home, "approval-gate") == os.path.join(install, "hermes-plugins", "approval-gate"),
          link(home, "approval-gate"))
    check("[%s] approval-gate is in plugins.enabled (tool-budget kept)" % how,
          "approval-gate" in enabled(home) and "tool-budget" in enabled(home), enabled(home))
    check("[%s] the Telegram gateway restart was attempted exactly once" % how,
          kickstarts(fx) == ["kickstart -k gui/%d/ai.hermes.gateway" % UID], kickstarts(fx))
    check("[%s] the update lock was still held when the gateway restarted" % how,
          read(os.path.join(fx, "lock.at-kickstart")).split() == ["held"],
          read(os.path.join(fx, "lock.at-kickstart")))
    st = state(home).get("last_result") or {}
    check("[%s] update-state.json still records the old run's result" % how,
          st.get("ok") is True and st.get("to_version") == "1.4.0", st)


# ---------------------------------------------------------------------------
print("\n2. a hop between two versions that both speak the protocol")
# ---------------------------------------------------------------------------
NEXT = CUR_UPDATE.replace(MARK_LINE, MARK_LINE + '\n  say "E29-MARK: this text is the INCOMING 1.5.0"', 1)
check("the 1.5.0 variant differs only by a marker line", NEXT != CUR_UPDATE)
for how in ("terminal", "dashboard"):
    install, home, fx = scenario("hop14-15-" + how, [
        ("v1.4.0", lambda w: put_current(w, "1.4.0")),
        ("v1.5.0", lambda w: put_current(w, "1.5.0", NEXT)),
    ], "v1.4.0")
    runner = run_terminal if how == "terminal" else run_dashboard
    rc, out = runner(install, home, fx, "--yes", "--channel", "stable")
    check("[%s] the update succeeds and lands on 1.5.0" % how,
          rc == 0 and read(os.path.join(install, "VERSION")).strip() == "1.5.0", out)
    check("[%s] the post-update steps ran from the INCOMING text" % how,
          "E29-MARK: this text is the INCOMING 1.5.0" in out
          and "continuing with the incoming update.sh (v1.5.0)" in out, out)
    check("[%s] install-services.sh did not call back into update.sh (no double run)" % how,
          "an older update.sh is running this file" not in out, out)
    check("[%s] plugins linked + enabled, gateway kickstarted exactly once" % how,
          link(home, "approval-gate") and "approval-gate" in enabled(home)
          and len(kickstarts(fx)) == 1, (kickstarts(fx), enabled(home)))
    check("[%s] the update lock was still held across the exec" % how,
          read(os.path.join(fx, "lock.at-kickstart")).split() == ["held"],
          read(os.path.join(fx, "lock.at-kickstart")))
    st = state(home).get("last_result") or {}
    check("[%s] state: ok, from 1.4.0 to 1.5.0, gateway restarted, mode git" % how,
          st.get("ok") is True and st.get("from_version") == "1.4.0"
          and st.get("to_version") == "1.5.0" and st.get("gateway") == "restarted"
          and st.get("mode") == "git", st)
    check("[%s] the summary names both versions" % how,
          "now at version 1.5.0" in out and "updated from\n  1.4.0" in out, out)
    check("[%s] the lock is free once the update is over" % how,
          (lambda fd: (fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB), os.close(fd), True)[-1])(
              os.open(os.path.join(home, ".hermes", "dashboard", "update.lock"), os.O_RDWR)))

# a failing post-update step still fails the run and records it
TB_LINE = 'TB_SRC="$ROOT/hermes-plugins/tool-budget"'
check("the plugin step's first line is where the test expects it", CUR_UPDATE.count(TB_LINE) == 1)
BAD = CUR_UPDATE.replace(TB_LINE, '[ "$POST_UPDATE" = "1" ] && die "E29 forced failure"\n' + TB_LINE, 1)
install, home, fx = scenario("hop-fail", [
    ("v1.4.0", lambda w: put_current(w, "1.4.0")),
    ("v1.5.0", lambda w: put_current(w, "1.5.0", BAD)),
], "v1.4.0")
rc, out = run_terminal(install, home, fx, "--yes", "--channel", "stable")
st = state(home).get("last_result") or {}
check("a post-update failure exits non-zero and update-state.json says so",
      rc != 0 and st.get("ok") is False and "E29 forced failure" in (st.get("message") or ""),
      (rc, st, out))


# ---------------------------------------------------------------------------
print("\n3. a downgrade to a release without the protocol: steps from the outgoing text")
# ---------------------------------------------------------------------------
install, home, fx = scenario("downgrade", [
    ("v1.3.2", lambda w: put_old(w, "1.3.2")),
    ("v1.4.0", lambda w: put_current(w, "1.4.0")),
], "v1.4.0")
# the old tree has no plugins dir, so give it the plugin sources the outgoing
# text links (the published 1.3.2 tree had tool-budget; approval-gate is new)
rc, out = run_terminal(install, home, fx, "--yes", "--target", "v1.3.2")
check("the downgrade completes on 1.3.2", rc == 0
      and read(os.path.join(install, "VERSION")).strip() == "1.3.2", out)
check("no hand-over was attempted (the target lacks the marker)",
      "continuing with the incoming update.sh" not in out, out)
check("the outgoing text ran the old tree's install-services.sh and its summary",
      "OLD install-services stub ran" in out and "SUMMARY" in out, out)


# ---------------------------------------------------------------------------
print("\n4. a plain ./install-services.sh never links plugins or restarts the gateway")
# ---------------------------------------------------------------------------
install, home, fx = scenario("plain", [("v1.4.0", lambda w: put_current(w, "1.4.0"))], "v1.4.0")
p = subprocess.run(["/bin/bash", os.path.join(install, "install-services.sh")],
                   env=env_for(home, fx), capture_output=True, text=True, timeout=120)
check("install-services.sh succeeds", p.returncode == 0, p.stdout + p.stderr)
check("no post-update call, no plugin link, no gateway kickstart",
      "older update.sh" not in p.stdout and link(home, "approval-gate") is None
      and kickstarts(fx) == [], (p.stdout, kickstarts(fx)))
p = subprocess.run(["/bin/bash", os.path.join(install, "install-services.sh")],
                   env=env_for(home, fx, HERMES_UPDATE_FROM="dashboard", HERMES_UPD_IN_POST="1"),
                   capture_output=True, text=True, timeout=120)
check("HERMES_UPD_IN_POST=1 (a current update.sh calling) suppresses the call-back",
      p.returncode == 0 and "older update.sh" not in p.stdout and kickstarts(fx) == [],
      p.stdout)

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
