#!/usr/bin/env python3
"""Regression test for C77 (docs/audits/2026-10-21-codebase-audit.md).

Two of the updater's safety properties could be deleted with every test green:

1. update.sh's tarball path verifies the download against SHA256SUMS and
   REFUSES when the sums are missing, have no line for the file, or do not
   match — and --force does not bypass it. Nothing ran that path.
2. The A08 lock handoff: _upd_apply() takes the flock and hands the LOCKED fd
   to the detached update.sh (Popen(pass_fds=...)); update.sh skips its own
   lock when HERMES_UPDATE_FROM=dashboard; the dashboard closes its copy. The
   A08 suite fakes Popen (so pass_fds was never exercised) and never ran the
   lock block in the dashboard-spawned shape.

Part 1 runs the REAL update.sh from a throwaway install dir (no .git, so the
tarball path) with a throwaway HOME, `curl` and `gh` shimmed on PATH to serve
local fixtures: no network, and the "install-services.sh" it ends with is a
stub from the fixture tarball that only writes a marker.  Part 2 runs the
REAL _upd_apply() (ast-extracted) with a REAL Popen against a stand-in
update.sh made of update.sh's REAL lock block, and checks the lock from the
outside while the child runs.
"""
import ast
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(_HERE)))
UPDATE_SH = os.path.join(REPO, "update.sh")
AUX_UPDATE = os.path.join(REPO, "dashboard", "aux_update.py")

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s\n    | %s" % (name, str(extra)[-1500:].replace("\n", "\n    | ")))


TMP = tempfile.mkdtemp(prefix="t-c77-")

# --------------------------------------------------------------------------
# part 1: the tarball path's checksum
# --------------------------------------------------------------------------
CURL = r'''#!/usr/bin/env python3
import json, os, shutil, sys
fx = os.environ["C77_FIXTURES"]
a = sys.argv[1:]
out, url, i, hdrs = None, None, 0, []
while i < len(a):
    if a[i] == "-o":
        out = a[i + 1]; i += 2; continue
    if a[i] in ("-H", "--header"):
        if a[i + 1] == "@-":      # real curl reads header lines from stdin
            hdrs += [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
        else:
            hdrs.append(a[i + 1])
        i += 2; continue
    if a[i].startswith("-"):
        i += 1; continue
    url = a[i]; i += 1
with open(os.path.join(fx, "requests.log"), "a") as f:
    f.write(url + "\n")
with open(os.path.join(fx, "curl-calls.jsonl"), "a") as f:
    f.write(json.dumps({"argv": a, "headers": hdrs}) + "\n")
name = {"https://api.github.com/repos/example/hermes/releases/latest": "release.json",
        "https://example.invalid/dl/HermesAssistant-1.0.1-source.tar.gz": "src.tar.gz",
        "https://example.invalid/dl/SHA256SUMS": "SHA256SUMS"}.get(url)
src = os.path.join(fx, name) if name else None
if not src or not os.path.exists(src):
    sys.stderr.write("curl: (22) The requested URL returned error: 404\n")
    sys.exit(22)
shutil.copy(src, out)
'''


def tarball(version, marker_note="installed", svc_rc=0, extra=None):
    """A minimal source tree, as release.yml's `git archive` would ship it."""
    buf = io.BytesIO()
    prefix = "HermesAssistant-%s/" % version
    files = {
        "VERSION": version + "\n",
        "install-services.sh": "#!/bin/sh\necho %s > \"$HOME/install-services-ran\"\nexit %d\n"
                               % (marker_note, svc_rc),
        "dashboard/server.py": "X = 1\n",
    }
    files.update(extra or {})
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for rel, text in files.items():
            data = text.encode()
            ti = tarfile.TarInfo(prefix + rel)
            ti.size = len(data)
            ti.mode = 0o755 if rel.endswith(".sh") else 0o644
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


SRC_URL = "https://example.invalid/dl/HermesAssistant-1.0.1-source.tar.gz"


# launchctl and pgrep are ALWAYS shimmed: update.sh restarts the Telegram
# gateway through launchctl, and the real one would act on this Mac's own.
LAUNCHCTL = r'''#!/bin/sh
printf '%s\n' "$*" >> "$C77_FIXTURES/launchctl.calls"
case "$1:${C77_GW:-absent}" in
  print:loaded|print:kick-fails) exit 0 ;;
  print:user-loaded) case "$2" in user/*) exit 0 ;; esac
                     echo "Could not find service" >&2; exit 113 ;;
  print:*) echo "Could not find service" >&2; exit 113 ;;
  kickstart:loaded) exit 0 ;;
  kickstart:user-loaded) case "$3" in user/*) exit 0 ;; esac; exit 1 ;;
  kickstart:*) exit 1 ;;
esac
exit 0
'''
PGREP = '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$C77_FIXTURES/pgrep.calls"\nexit "${C77_PGREP_RC:-1}"\n'


def run_update(name, *, sums="good", force=False, tag="v1.0.1", src_url=SRC_URL, token=None,
               local=None, svc_rc=0, extra=None, pre_backups=(), gateway="absent",
               pgrep_rc=1, dry=False, unreadable=()):
    root = os.path.join(TMP, name)
    install = os.path.join(root, "install")
    home = os.path.join(root, "home")
    fx = os.path.join(root, "fixtures")
    shim = os.path.join(root, "bin")
    tmpd = os.path.join(root, "tmp")
    for d in (install, home, fx, shim, tmpd):
        os.makedirs(d)
    shutil.copy(UPDATE_SH, os.path.join(install, "update.sh"))
    with open(os.path.join(install, "VERSION"), "w") as f:
        f.write("1.0.0\n")
    with open(os.path.join(install, "install-services.sh"), "w") as f:
        f.write("#!/bin/sh\necho old > \"$HOME/install-services-ran\"\n")
    os.chmod(os.path.join(install, "install-services.sh"), 0o755)
    for rel, text in (local or {}).items():      # what this install holds locally
        os.makedirs(os.path.dirname(os.path.join(install, rel)), exist_ok=True)
        with open(os.path.join(install, rel), "w") as f:
            f.write(text)
    for rel in unreadable:                        # a file the backup cannot copy
        os.chmod(os.path.join(install, rel), 0)
    for d in pre_backups:                         # backups left by earlier updates
        os.makedirs(os.path.join(home, ".hermes", "dashboard", "update-backups", d))

    tgz = tarball("1.0.1", svc_rc=svc_rc, extra=extra)
    with open(os.path.join(fx, "src.tar.gz"), "wb") as f:
        f.write(tgz)
    good = hashlib.sha256(tgz).hexdigest()
    assets = [{"name": "HermesAssistant-1.0.1-source.tar.gz",
               "browser_download_url": src_url}]
    line = None
    if sums == "good":
        line = "%s  HermesAssistant-1.0.1-source.tar.gz\n" % good
    elif sums == "mismatch":
        line = "%s  HermesAssistant-1.0.1-source.tar.gz\n" % hashlib.sha256(b"something else").hexdigest()
    elif sums == "no-entry":
        line = "%s  HermesAssistant-1.0.1-macos-arm64.zip\n" % good
    if line is not None:
        with open(os.path.join(fx, "SHA256SUMS"), "w") as f:
            f.write(line)
        assets.append({"name": "SHA256SUMS",
                       "browser_download_url": "https://example.invalid/dl/SHA256SUMS"})
    with open(os.path.join(fx, "release.json"), "w") as f:
        json.dump({"tag_name": tag, "assets": assets}, f)

    p = os.path.join(shim, "curl")
    with open(p, "w") as f:
        f.write(CURL)
    os.chmod(p, 0o755)
    for tool, body in (("gh", "#!/bin/sh\nexit 1\n"), ("launchctl", LAUNCHCTL), ("pgrep", PGREP)):
        p = os.path.join(shim, tool)
        with open(p, "w") as f:
            f.write(body)
        os.chmod(p, 0o755)

    env = {k: v for k, v in os.environ.items()
           if k not in ("HERMES_UPDATE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "HERMES_UPDATE_FROM")}
    env.update({"HOME": home, "C77_FIXTURES": fx, "HERMES_UPDATE_REPO": "example/hermes",
                "TMPDIR": tmpd, "C77_GW": gateway, "C77_PGREP_RC": str(pgrep_rc),
                "PATH": ":".join([shim, os.path.dirname(sys.executable), "/usr/bin", "/bin",
                                  "/usr/sbin", "/sbin"])})
    if token:
        env["HERMES_UPDATE_TOKEN"] = token
    which = subprocess.run(["bash", "-c", "command -v curl launchctl pgrep"], env=env,
                           capture_output=True, text=True).stdout.splitlines()
    if which != [os.path.join(shim, t) for t in ("curl", "launchctl", "pgrep")]:
        print("  refusing to run: curl/launchctl/pgrep do not resolve to the shims (%r)" % which)
        sys.exit(1)
    args = (["bash", os.path.join(install, "update.sh")] + (["--force"] if force else [])
            + (["--dry-run"] if dry else []))
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=120, cwd=install)
    try:
        with open(os.path.join(home, ".hermes", "dashboard", "update-state.json")) as f:
            state = json.load(f).get("last_result") or {}
    except (OSError, ValueError):
        state = {}
    with open(os.path.join(install, "VERSION")) as f:
        ver = f.read().strip()
    ran = os.path.exists(os.path.join(home, "install-services-ran"))
    r.leftover_tmp = [n for n in os.listdir(tmpd) if n.startswith("hermes-update.")]
    r.pwned = sorted(n for n in os.listdir(home) if n.startswith("pwned"))
    r.install, r.home, r.env = install, home, env
    bdir = os.path.join(home, ".hermes", "dashboard", "update-backups")
    r.backups = sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []
    r.backup_root = bdir
    try:
        with open(os.path.join(fx, "curl-calls.jsonl")) as f:
            r.curl_calls = [json.loads(ln) for ln in f if ln.strip()]
    except OSError:
        r.curl_calls = []
    for tool in ("launchctl", "pgrep"):
        try:
            with open(os.path.join(fx, tool + ".calls")) as f:
                setattr(r, tool, f.read().splitlines())
        except OSError:
            setattr(r, tool, [])
    try:
        with open(os.path.join(home, ".hermes", "dashboard", "update-state.json")) as f:
            r.state_file = json.load(f)
    except (OSError, ValueError):
        r.state_file = {}
    return r, state, ver, ran


print("\n1. update.sh's tarball path installs only a verified download")
r, state, ver, ran = run_update("good")
out = r.stdout + r.stderr
check("a download that matches SHA256SUMS installs (VERSION 1.0.0 -> 1.0.1)", ver == "1.0.1", out)
check("...it says 'checksum ok'", "checksum ok" in out, out)
check("...install-services.sh ran", ran, out)
check("...and the state file records success", state.get("ok") is True, state)
check("...and update.sh exits 0", r.returncode == 0, (r.returncode, out))
check("...and its temp dir is cleaned up", r.leftover_tmp == [], r.leftover_tmp)

for label, sums, force, why in (
        ("a SHA256 mismatch", "mismatch", False, "SHA256 mismatch"),
        ("a SHA256 mismatch with --force", "mismatch", True, "SHA256 mismatch"),
        ("a release with no SHA256SUMS asset", "absent", False, "no SHA256SUMS asset"),
        ("a release with no SHA256SUMS asset, --force", "absent", True, "no SHA256SUMS asset"),
        ("SHA256SUMS with no line for the tarball", "no-entry", False, "has no entry")):
    r, state, ver, ran = run_update(label.replace(" ", "-").replace(",", ""), sums=sums, force=force)
    out = r.stdout + r.stderr
    check("[%s] nothing installed (VERSION still 1.0.0)" % label, ver == "1.0.0", out)
    check("[%s] install-services.sh never ran" % label, not ran, out)
    check("[%s] the refusal says why (%s)" % (label, why), why in out, out)
    check("[%s] the state file records a failure" % label,
          state.get("ok") is False and why in (state.get("message") or ""), state)
    # R2-50: the tarball path re-trapped EXIT as 'cleanup; on_exit', so the rc
    # on_exit read was cleanup's (0) and every refusal here exited 0.
    check("[%s] update.sh exits non-zero" % label, r.returncode != 0, (r.returncode, out))
    check("[%s] and still cleans up its temp dir" % label, r.leftover_tmp == [], r.leftover_tmp)

# R2-51: the tag and URLs came out of the release JSON into a plan.env that
# was SOURCED before any checksum check, so a tag (a legal git refname) or an
# asset URL holding $(...) / ${IFS} ran as shell code on this Mac.
print("\n1b. the release JSON's tag and URLs are data, never shell code")
for label, kw, why in (
        ("a tag holding $(...)", {"tag": 'v1.0.1$(touch "$HOME/pwned-tag")'}, "not a version tag"),
        ("a tag holding ${IFS}", {"tag": "v1.0.1${IFS}$(touch${IFS}$HOME/pwned-ifs)"}, "not a version tag"),
        ("a tag holding backticks", {"tag": "v1.0.1`touch $HOME/pwned-bt`"}, "not a version tag"),
        ("an asset URL holding $(...)",
         {"src_url": "https://example.invalid/dl/$(touch${IFS}$HOME/pwned-url)HermesAssistant-1.0.1-source.tar.gz"},
         "not a plain https URL"),
        ("a plain-http asset URL", {"src_url": SRC_URL.replace("https://", "http://")},
         "not a plain https URL")):
    r, state, ver, ran = run_update("r251-" + "".join(c for c in label if c.isalnum()), **kw)
    out = r.stdout + r.stderr
    check("[%s] nothing it carried was executed" % label, r.pwned == [], (r.pwned, out))
    check("[%s] nothing installed (VERSION still 1.0.0)" % label, ver == "1.0.0" and not ran, out)
    check("[%s] refused with the reason (%s), exit non-zero" % (label, why),
          why in out and r.returncode != 0, (r.returncode, out))
    check("[%s] the state file records the refusal" % label,
          state.get("ok") is False and why in (state.get("message") or ""), state)
r, state, ver, ran = run_update("r251-prerelease", tag="v1.0.1-rc.1")
check("a legitimate prerelease tag (v1.0.1-rc.1) still installs",
      ver == "1.0.1" and ran and r.returncode == 0, r.stdout + r.stderr)

# R2-52: the token went into curl's argv as -H "Authorization: Bearer $TOK",
# readable by any local user with ps for as long as a download ran.
print("\n1c. a GitHub token reaches curl on stdin, never in its argv")
SECRET = "ghp_r252secretTOKENvalue0123456789"
r, state, ver, ran = run_update("r252-token", token=SECRET)
calls = r.curl_calls
check("a tokened update still installs", ver == "1.0.1" and ran and r.returncode == 0,
      r.stdout + r.stderr)
check("...through all three requests (release JSON, tarball, SHA256SUMS)", len(calls) == 3, calls)
check("...and the token is in NO curl argv",
      calls and not any(SECRET in arg for c in calls for arg in c["argv"]),
      [c["argv"] for c in calls])
check("...yet every request carried it as a header (curl -H @- on stdin)",
      calls and all("Authorization: Bearer " + SECRET in c["headers"] for c in calls),
      [c["headers"] for c in calls])
check("...and the log never prints it", SECRET not in r.stdout + r.stderr)
r, state, ver, ran = run_update("r252-no-token")
check("with no token, no Authorization header is sent and curl's stdin is not used",
      len(r.curl_calls) == 3 and all(
          "@-" not in c["argv"] and not any(h.lower().startswith("authorization:")
                                            for h in c["headers"]) for c in r.curl_calls),
      r.curl_calls)

# R2-53: the tarball rsync --delete had no exclude for the legacy config
# export (export-ignored from the tarball, so every update deleted it), kept
# no copy of what it overwrote, and offered no way back when install-services
# failed afterwards.
print("\n1d. a tarball update keeps the legacy export, backs up, and can be rolled back")


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


LOCAL = {"docs/state-snapshot.json": '{"kind": "hermes-state-snapshot"}\n',
         "dashboard/server.py": "X = 'a local edit'\n",
         "dashboard/mine.txt": "only on this Mac\n"}
OLD_BACKUPS = ("20250101-000000-v0.9.0", "20250201-000000-v0.9.1",
               "20250301-000000-v0.9.2", "20250401-000000-v0.9.3", "keep-me-not-a-backup")
r, state, ver, ran = run_update("r253-good", local=LOCAL, pre_backups=OLD_BACKUPS)
out = r.stdout + r.stderr
check("the update installs", ver == "1.0.1" and ran and r.returncode == 0, out)
check("docs/state-snapshot.json survives rsync --delete",
      read(os.path.join(r.install, "docs", "state-snapshot.json")) == LOCAL["docs/state-snapshot.json"], out)
new_b = [b for b in r.backups if b.endswith("-v1.0.0")]
check("a backup of the old install was made first", len(new_b) == 1, r.backups)
b = os.path.join(r.backup_root, new_b[0]) if new_b else "/nonexistent"
check("...holding the overwritten local edit and the deleted local file",
      read(os.path.join(b, "dashboard", "server.py")) == LOCAL["dashboard/server.py"]
      and read(os.path.join(b, "dashboard", "mine.txt")) == LOCAL["dashboard/mine.txt"]
      and (read(os.path.join(b, "VERSION")) or "").strip() == "1.0.0", os.listdir(b) if new_b else b)
check("only the newest three backups are kept; an unrelated folder is left alone",
      r.backups == sorted(["20250301-000000-v0.9.2", "20250401-000000-v0.9.3", new_b[0] if new_b else "?",
                           "keep-me-not-a-backup"]), r.backups)
check("the summary says where the backup is", new_b and new_b[0] in out, out)

r, state, ver, ran = run_update("r253 svc fails", local=LOCAL, svc_rc=1,
                                extra={"dashboard/aux_new_in_101.py": "Y = 2\n"})
out = r.stdout + r.stderr
check("install-services.sh failing after the install exits non-zero and records it",
      r.returncode != 0 and state.get("ok") is False, (r.returncode, out))
cmd = (state.get("message") or "").split("to put v1.0.0 back:  ", 1)
check("...and the failure carries the rollback command", len(cmd) == 2 and "rsync -a --delete" in cmd[-1],
      state.get("message"))
if len(cmd) == 2:
    os.remove(os.path.join(r.home, "install-services-ran"))
    rb = subprocess.run(["bash", "-c", cmd[1]], env=r.env, capture_output=True, text=True, timeout=60)
    check("running that command (from a path with a space) puts the old install back",
          rb.returncode == 0 and read(os.path.join(r.install, "VERSION")).strip() == "1.0.0"
          and read(os.path.join(r.install, "dashboard", "server.py")) == LOCAL["dashboard/server.py"]
          and read(os.path.join(r.install, "dashboard", "mine.txt")) == LOCAL["dashboard/mine.txt"],
          (rb.returncode, rb.stdout, rb.stderr))
    check("...removes what the failed release added",
          not os.path.exists(os.path.join(r.install, "dashboard", "aux_new_in_101.py")))
    check("...keeps the legacy export",
          read(os.path.join(r.install, "docs", "state-snapshot.json")) == LOCAL["docs/state-snapshot.json"])
    check("...and re-runs the OLD install-services.sh",
          (read(os.path.join(r.home, "install-services-ran")) or "").strip() == "old")

# A backup that fails part-way (a file it cannot read; in real life, a full
# disk) refuses the install, and must not leave its partial copy behind: that
# copy would sit on the full disk and count as one of the three kept backups,
# pushing a complete one out at the next good update.
r, state, ver, ran = run_update("r253-backup-fails", local={"dashboard/locked.txt": "x\n"},
                                unreadable=("dashboard/locked.txt",),
                                pre_backups=("20250401-000000-v0.9.3",))
out = r.stdout + r.stderr
check("a backup that fails refuses the install (exit non-zero, VERSION still 1.0.0)",
      r.returncode != 0 and ver == "1.0.0" and not ran and "could not back up" in out
      and state.get("ok") is False, (r.returncode, out))
check("...and removes its partial copy, leaving the older backups alone",
      r.backups == ["20250401-000000-v0.9.3"], r.backups)

# X-model-lifecycle-1: Hermes loads plugins once per process and
# install-services.sh never restarts the gateway, so an update that enabled
# approval-gate left the running Telegram gateway without it until a reboot.
print("\n1e. an update restarts the Telegram gateway, or says loudly that it did not")
GW = "gui/%d/ai.hermes.gateway" % os.getuid()
PLUG = {"hermes-plugins/approval-gate/__init__.py": "# stub plugin\n"}
r, state, ver, ran = run_update("gw-loaded", gateway="loaded", extra=PLUG)
out = r.stdout + r.stderr
check("with the gateway loaded in launchd, the update kickstarts it (-k) after installing",
      ver == "1.0.1" and r.returncode == 0 and ("kickstart -k " + GW) in r.launchctl,
      (r.launchctl, out))
check("...records gateway=restarted in update-state.json", state.get("gateway") == "restarted", state)
check("...and the summary says so", "Telegram gateway was restarted" in out, out)
r, state, ver, ran = run_update("gw-kick-fails", gateway="kick-fails", extra=PLUG)
out = r.stdout + r.stderr
check("a failed restart does not fail the update, but warns with the command to run",
      ver == "1.0.1" and r.returncode == 0 and state.get("ok") is True
      and "WARNING: could not restart the Telegram gateway" in out
      and ("launchctl kickstart -k " + GW) in out, (r.returncode, out))
check("...and is recorded (gateway=restart_failed) and repeated in the summary",
      state.get("gateway") == "restart_failed" and "TELEGRAM GATEWAY WAS NOT RESTARTED" in out, state)
# Hermes' own `hermes gateway install` loads the job into user/<uid> when the
# session is not Aqua (SSH, Background; its default on macOS 26+), and its
# gateway commands probe gui/<uid> then user/<uid>. Probing gui/ alone took
# that launchd job for a hand-run gateway and told the owner to start a second.
UGW = "user/%d/ai.hermes.gateway" % os.getuid()
r, state, ver, ran = run_update("gw-user-domain", gateway="user-loaded", pgrep_rc=0, extra=PLUG)
out = r.stdout + r.stderr
check("a gateway loaded in the user/<uid> domain is kickstarted there",
      ver == "1.0.1" and r.returncode == 0 and ("kickstart -k " + UGW) in r.launchctl
      and state.get("gateway") == "restarted", (r.launchctl, state, out))
check("...and is not reported as running outside launchd", "outside launchd" not in out, out)
r, state, ver, ran = run_update("gw-manual", gateway="absent", pgrep_rc=0, extra=PLUG)
out = r.stdout + r.stderr
check("a gateway running outside launchd is not killed, but the update says to restart it",
      not any(c.startswith("kickstart") for c in r.launchctl)
      and state.get("gateway") == "restart_by_hand" and "running outside launchd" in out,
      (r.launchctl, state, out))
r, state, ver, ran = run_update("gw-none", gateway="absent", pgrep_rc=1, extra=PLUG)
check("no gateway at all: nothing is kickstarted, gateway=not_running",
      not any(c.startswith("kickstart") for c in r.launchctl)
      and state.get("gateway") == "not_running" and r.returncode == 0, (r.launchctl, state))
r, state, ver, ran = run_update("gw-refused", sums="mismatch", gateway="loaded", extra=PLUG)
check("a refused update does not touch the gateway",
      r.returncode != 0 and r.launchctl == [] and r.pgrep == [], (r.launchctl, r.pgrep))
r, state, ver, ran = run_update("gw-dry", gateway="loaded", dry=True, local=PLUG)
out = r.stdout + r.stderr
check("--dry-run touches no gateway, only says it would restart it",
      r.returncode == 0 and r.launchctl == [] and ver == "1.0.0"
      and "would restart the Telegram gateway" in out, (r.launchctl, out))
r, state, ver, ran = run_update("gw-noplug", gateway="loaded")
check("a build with no agent plugins leaves the gateway alone (gateway=not_needed)",
      ver == "1.0.1" and r.launchctl == [] and state.get("gateway") == "not_needed",
      (r.launchctl, state))
check("...and the plugin link landed in the throwaway HOME, never this Mac's",
      os.path.islink(os.path.join(TMP, "gw-loaded", "home", ".hermes", "plugins", "approval-gate")))

# --------------------------------------------------------------------------
# part 2: the lock handoff, with a real child process
# --------------------------------------------------------------------------
print("\n2. the dashboard's lock is held by the detached updater for its whole run")


def lock_block():
    """The top-level `if ... fi` in update.sh that opens update.lock — found
    by its content, so an edit to its condition is tested, not tripped over."""
    lines = open(UPDATE_SH).read().splitlines()
    at = next(i for i, ln in enumerate(lines) if 'LOCK_FILE="$STATE_DIR/update.lock"' in ln)
    start = max(i for i in range(at) if lines[i].startswith("if "))
    end = next(i for i in range(at, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start:end + 1])


def lock_is_free(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def wait_for(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


data = os.path.join(TMP, "lock", "dashboard")
os.makedirs(data)
LOCK = os.path.join(data, "update.lock")
GO = os.path.join(TMP, "lock", "go")
REACHED = os.path.join(TMP, "lock", "reached")
stand_in = os.path.join(TMP, "lock", "update.sh")
with open(stand_in, "w") as f:
    f.write("set -euo pipefail\n"
            "die(){ echo \"DIE: $*\" >&2; exit 1; }\n"
            "DRY=0\nSTATE_DIR=%s\n%s\n"
            "echo $$ >> %s\n"
            # bounded: a broken handoff (or a failed check) must not leave
            # this polling forever once TMP, and so GO, is gone
            "n=0; while [ ! -e %s ] && [ $n -lt 600 ]; do sleep 0.05; n=$((n+1)); done\n"
            % (data, lock_block(), REACHED, GO))

tree = ast.parse(open(AUX_UPDATE).read())
fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_upd_apply"]
assert fn, "_upd_apply not found"
spawned = []
PROCS = []


def real_popen(cmd, **kw):
    spawned.append(kw)
    proc = subprocess.Popen(cmd, **kw)
    PROCS.append(proc)
    return proc


ns = dict(
    _upd_os=os, _upd_fcntl=fcntl, _upd_time=time,
    _UPD_ROOT=os.path.join(TMP, "lock"), _UPD_SCRIPT=stand_in, _UPD_DATA=data,
    _UPD_LOCK_FILE=LOCK, _UPD_LOG=os.path.join(TMP, "lock", "update.log"),
    _upd_channel=lambda: "stable", _upd_parse_version=lambda t: (1, 0, 0),
    _upd_known_tags=lambda: set(), _upd_dirty_files=lambda: [],
    _upd_subprocess=SimpleNamespace(Popen=real_popen, DEVNULL=subprocess.DEVNULL),
    _upd_state_write=lambda obj: None, _upd_state_read=lambda: {},
    _upd_version=lambda: "c77-test",
    _upd_chat_busy=lambda: False)     # no chat turn running (E18 has its own suite)
exec(compile(ast.Module(body=fn, type_ignores=[]), AUX_UPDATE, "exec"), ns)

fds_before = len(os.listdir("/dev/fd"))
r1 = ns["_upd_apply"](SimpleNamespace(body={}))
check("the first apply spawns the updater", isinstance(r1, dict) and r1.get("ok") is True, r1)
check("the dashboard-spawned updater gets past update.sh's own lock block "
      "(HERMES_UPDATE_FROM=dashboard skips it — the inherited fd already holds the lock)",
      wait_for(lambda: os.path.exists(REACHED)), open(ns["_UPD_LOG"]).read()
      if os.path.exists(ns["_UPD_LOG"]) else "no log")
check("the dashboard closed its own copy of the lock fd",
      len(os.listdir("/dev/fd")) == fds_before, (fds_before, len(os.listdir("/dev/fd"))))
check("WHILE the updater runs, the lock is held (it inherited the locked fd)",
      not lock_is_free(LOCK))
r2 = ns["_upd_apply"](SimpleNamespace(body={}))
check("...so a second apply is refused 409 already_running",
      isinstance(r2, tuple) and r2[1] == 409 and r2[0].get("reason") == "already_running", r2)
bare = subprocess.run(["bash"], input="set -euo pipefail\ndie(){ echo \"DIE: $*\" >&2; exit 1; }\n"
                      "DRY=0\nSTATE_DIR=%s\n%s\necho PAST\n" % (data, lock_block()),
                      text=True, capture_output=True,
                      env={k: v for k, v in os.environ.items() if k != "HERMES_UPDATE_FROM"})
check("...and so is a bare terminal ./update.sh (its own lock block)",
      "PAST" not in bare.stdout and bare.returncode != 0 and "update.lock" in bare.stderr,
      (bare.stdout, bare.stderr))
open(GO, "w").close()
check("when the updater exits, the lock is released (no leak)", wait_for(lambda: lock_is_free(LOCK)))
r3 = ns["_upd_apply"](SimpleNamespace(body={}))
check("...and the next apply goes through", isinstance(r3, dict) and r3.get("ok") is True, r3)
check("every spawn passed the lock fd and detached",
      len(spawned) == 2 and all(kw.get("pass_fds") and kw.get("start_new_session") for kw in spawned),
      [{k: kw.get(k) for k in ("pass_fds", "start_new_session")} for kw in spawned])
wait_for(lambda: lock_is_free(LOCK))
for proc in PROCS:          # never leave a detached child behind, pass or fail
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass

shutil.rmtree(TMP, ignore_errors=True)
print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
sys.exit(1 if FAILS else 0)
