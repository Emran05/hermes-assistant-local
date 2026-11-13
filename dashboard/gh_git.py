"""gh_git.py — the hardened git runner behind the GitHub sandbox change pipeline
(docs/plans/github-sandbox-2026-10.md §3.1, §3.2, §3.5, §8.1).

A real sibling module, imported like gh_api.py and permissions.py: plain stdlib,
no dashboard globals, so every rule here is testable against local bare repos
and file:// remotes in a tempdir — never against real GitHub.

Two git stores, one trusted (§3.1):

    ~/.hermes/dashboard/github/mirrors/<owner>__<repo>.git   bare, DASHBOARD-OWNED
    ~/HermesSandbox/github/<owner>/<repo>/                    work tree + .git, AGENT-OWNED

The dashboard never runs git with the sandbox's .git: no hooks, no
core.fsmonitor, no filter or diff drivers, no insteadOf. It reads only the
work tree's FILES, through a separate index file in the mirror (snapshot()).

Every invocation (Mirror.run) uses
  * an argv list, shell=False, cwd = the mirror, GIT_DIR = the mirror;
  * an environment built from nothing: PATH=/usr/bin:/bin, HOME = an empty
    dashboard-owned directory, GIT_CONFIG_NOSYSTEM=1, GIT_CONFIG_GLOBAL=/dev/null,
    GIT_TERMINAL_PROMPT=0, GIT_ASKPASS=/usr/bin/false, GIT_NO_REPLACE_OBJECTS=1,
    GIT_PROTOCOL_FROM_USER=0, LANG/LC_ALL=C — nothing of the dashboard's own
    environment (a proxy, a GIT_* variable, an ssh agent) leaks in;
  * HARD_FLAGS (core.hooksPath=/dev/null, core.fsmonitor=false, diff.external=,
    credential.helper=, protocol.allow=never + https always + file never,
    http.followRedirects=false, transfer.fsckObjects=true, ...), plus
    --no-ext-diff --no-textconv on every diff;
  * a timeout (TIMEOUT, NET_TIMEOUT for fetch/push).

The mirror's own config is REGENERATED from TEMPLATE before every operation
(the agent runs as the same user and can write to the mirror: a planted
url.*.insteadOf would redirect the token, a planted filter.*.clean would run
during a snapshot), and the files that let a repository borrow another one's
objects, config or history — objects/info/alternates, commondir,
config.worktree, info/grafts, shallow, info/attributes — are removed.

The token reaches git ONLY as the GIT_CONFIG_COUNT env triple a caller passes
as `auth_env` to ONE network call (fetch / push / delete_branch). It is never
in argv, a config file or a remote URL, and every error string is scrubbed.

file:// remotes are accepted only when HERMES_TESTS_OFFLINE=1 (the unit tier);
the only file:// URL production ever uses is the dashboard's own mirror, for
cloning the sandbox.
"""
import base64
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse

try:
    import gh_api as _gg_api
except Exception:                                    # pragma: no cover
    _gg_api = None

GIT_BIN = "/usr/bin/git"
SAFE_PATH = "/usr/bin:/bin"
TIMEOUT = 120
NET_TIMEOUT = 600
GITHUB_GIT = "https://github.com"
DEAD_PUSHURL = "hermes-no-push://see-the-github-sandbox-skill"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# branch names the mirror will carry (a default branch or a hermes/* head);
# git allows far more, and these strings are put in argv
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

# files that make a repository borrow objects, config, refs or history from
# somewhere else; the mirror never has any of them legitimately
_FOREIGN_FILES = ("objects/info/alternates", "objects/info/http-alternates", "commondir",
                  "config.worktree", "info/grafts", "shallow", "info/attributes", "gitdir")

TEMPLATE = """[core]
\trepositoryformatversion = 0
\tfilemode = true
\tbare = true
\tignorecase = {ignorecase}
\tprecomposeunicode = true
\tlogallrefupdates = false
\thooksPath = /dev/null
\tfsmonitor = false
\tsymlinks = true
[gc]
\tauto = 0
[maintenance]
\tauto = false
[transfer]
\tfsckObjects = true
[protocol]
\tallow = never
"""


def test_mode(env=None):
    return (env if env is not None else os.environ).get("HERMES_TESTS_OFFLINE") == "1"


_AVAILABLE = {}


def available():
    """True when a real git is installed. On macOS /usr/bin/git is only a shim
    until the Command Line Tools or Xcode exist, and running it then pops an
    installer — so the shim is never run to find out: the developer directory
    is looked up (`xcode-select -p` prints a path and never prompts)."""
    if GIT_BIN in _AVAILABLE:
        return _AVAILABLE[GIT_BIN]
    ok = os.path.exists(GIT_BIN)
    if ok and GIT_BIN == "/usr/bin/git" and os.path.exists("/usr/bin/xcode-select"):
        dirs = ["/Library/Developer/CommandLineTools",
                "/Applications/Xcode.app/Contents/Developer"]
        try:
            p = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True,
                               timeout=10, stdin=subprocess.DEVNULL,
                               env={"PATH": SAFE_PATH, "HOME": "/nonexistent"})
            if p.returncode == 0 and p.stdout.strip().startswith("/"):
                dirs.insert(0, p.stdout.strip())
        except Exception:
            pass
        ok = any(os.path.exists(os.path.join(d, "usr", "bin", "git")) for d in dirs)
    _AVAILABLE[GIT_BIN] = ok
    return ok


def hard_flags(allow_file=False):
    """The -c flags every invocation carries (they win over any config file)."""
    pairs = (
        ("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"),
        ("core.untrackedCache", "false"), ("core.splitIndex", "false"),
        ("diff.external", ""), ("core.pager", "cat"), ("credential.helper", ""),
        ("core.askPass", "/usr/bin/false"), ("core.sshCommand", "false"),
        ("core.attributesFile", "/dev/null"), ("core.excludesFile", "/dev/null"),
        ("core.autocrlf", "false"), ("core.safecrlf", "false"), ("core.symlinks", "true"),
        ("core.protectHFS", "true"), ("core.protectNTFS", "true"),
        ("protocol.allow", "never"), ("protocol.https.allow", "always"),
        ("protocol.file.allow", "always" if allow_file else "never"),
        # E56: a per-protocol key in a planted config beats protocol.allow, so
        # every other transport is pinned off here, where a config file
        # cannot override it (ext:: would run a command with the token env)
        ("protocol.ext.allow", "never"), ("protocol.fd.allow", "never"),
        ("protocol.ssh.allow", "never"), ("protocol.git.allow", "never"),
        ("protocol.http.allow", "never"), ("protocol.ftp.allow", "never"),
        ("protocol.ftps.allow", "never"),
        # ...and the size above which git calls a blob binary (the review diff)
        ("core.bigFileThreshold", "512m"),
        # diff headers carry non-ASCII paths as they are, so the review sheet
        # can match each file to its diff and preview
        ("core.quotePath", "false"),
        ("http.followRedirects", "false"), ("http.sslVerify", "true"),
        ("http.extraHeader", ""),
        ("transfer.fsckObjects", "true"), ("fetch.fsckObjects", "true"),
        ("receive.fsckObjects", "true"),
        ("submodule.recurse", "false"), ("fetch.recurseSubmodules", "false"),
        ("push.recurseSubmodules", "no"), ("push.followTags", "false"),
        ("push.gpgSign", "false"), ("commit.gpgSign", "false"), ("tag.gpgSign", "false"),
        ("gc.auto", "0"), ("maintenance.auto", "false"), ("fetch.writeCommitGraph", "false"),
        ("advice.addEmbeddedRepo", "false"), ("advice.detachedHead", "false"),
        ("init.defaultBranch", "main"),
    )
    out = []
    for k, v in pairs:
        out += ["-c", "%s=%s" % (k, v)]
    return out


class GitError(Exception):
    """A git step failed. str() is one scrubbed, printable line."""


class GitTampered(GitError):
    """The mirror's config (or a borrow-from-elsewhere file) changed while a
    git call ran: something raced the regeneration (E56)."""


# callables(mirror_path, why) told about every GitTampered, before it raises
# (the change pipeline registers one that holds writes and tells the owner)
TAMPER_HOOKS = []


def _scrub(text, secrets):
    t = "" if text is None else str(text)
    if _gg_api is not None:
        return _gg_api.short(t, 300, *[s for s in secrets if s])
    for s in secrets:
        if s:
            t = t.replace(s, "[token]")
    return re.sub(r"\s+", " ", t).strip()[:300]


def _secrets_of(auth_env):
    """Every form of the token an auth_env carries (for scrubbing errors)."""
    out = []
    v = (auth_env or {}).get("GIT_CONFIG_VALUE_0") or ""
    m = re.search(r"basic\s+(\S+)", v, re.I)
    if m:
        out.append(m.group(1))
        try:
            raw = base64.b64decode(m.group(1)).decode("utf-8", "replace")
            if raw.startswith("x-access-token:"):
                out.append(raw[len("x-access-token:"):])
        except Exception:
            pass
    return out


def normalize_repo(full):
    if not isinstance(full, str):
        return None
    parts = full.strip().split("/")
    if len(parts) != 2:
        return None
    o, n = parts
    if not _OWNER_RE.fullmatch(o) or not _NAME_RE.fullmatch(n) or n in (".", ".."):
        return None
    if n.endswith(".git"):
        return None
    return o + "/" + n


def remote_url(git_base, full_name, env=None):
    """The ONLY push/fetch URL shape: <git_base>/<owner>/<repo>.git, where the
    base is exactly https://github.com — or, in the unit tier only, a file://
    directory or a loopback http server. Raises GitError otherwise."""
    full = normalize_repo(full_name)
    if not full:
        raise GitError("bad repository name")
    base = str(git_base or "").rstrip("/")
    ok = base == GITHUB_GIT
    if not ok and test_mode(env):
        if base.startswith("file:///") and ".." not in base.split("/"):
            ok = True
        elif re.fullmatch(r"http://(127\.0\.0\.1|localhost)(:\d{1,5})?", base):
            ok = True
    if not ok:
        raise GitError("refusing a git remote that is not https://github.com")
    o, n = full.split("/", 1)
    return "%s/%s/%s.git" % (base, o, n)


def url_is_file(url):
    return str(url or "").startswith("file://")


def branch_ref(slug):
    """refs/heads/hermes/<slug> for a validated slug, else GitError."""
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise GitError("bad branch name")
    return "refs/heads/hermes/" + slug


def check_ref(ref):
    """`git check-ref-format` on top of the regexes. True only for
    refs/heads/hermes/<slug> with a valid slug."""
    if not isinstance(ref, str) or not ref.startswith("refs/heads/hermes/"):
        return False
    if not SLUG_RE.fullmatch(ref[len("refs/heads/hermes/"):]):
        return False
    try:
        p = subprocess.run([GIT_BIN, "check-ref-format", ref], env=_env(None, "/", {}),
                           cwd="/", stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=20)
    except Exception:
        return False
    return p.returncode == 0


def _env(git_dir, home, extra):
    env = {"PATH": SAFE_PATH, "HOME": home or "/nonexistent", "LANG": "C", "LC_ALL": "C",
           "TZ": "UTC",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/usr/bin/false",
           "SSH_ASKPASS": "/usr/bin/false", "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_OPTIONAL_LOCKS": "0", "GIT_PROTOCOL_FROM_USER": "0",
           "GIT_ATTR_NOSYSTEM": "1", "GIT_ADVICE": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    if git_dir:
        env["GIT_DIR"] = git_dir
    for k, v in (extra or {}).items():
        if isinstance(k, str) and isinstance(v, str):
            env[k] = v
    return env


class Result(object):
    __slots__ = ("rc", "out", "err", "truncated")

    def __init__(self, rc, out, err, truncated=False):
        self.rc, self.out, self.err, self.truncated = rc, out, err, truncated

    @property
    def ok(self):
        return self.rc == 0

    def text(self):
        return self.out.decode("utf-8", "replace") if isinstance(self.out, bytes) else self.out


def _exec(argv, env, cwd, inp=None, timeout=TIMEOUT, cap=None):
    """Run argv; stdout as bytes, capped at `cap` bytes when given (the child
    is killed past it, so a huge diff cannot exhaust memory)."""
    try:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if inp is not None
                             else subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, shell=False, close_fds=True)
    except OSError as e:
        return Result(127, b"", "cannot run git: %s" % type(e).__name__)
    out, err, truncated = bytearray(), bytearray(), [False]

    def pump_err():
        try:
            while True:
                b = p.stderr.read(65536)
                if not b:
                    break
                if len(err) < 65536:
                    err.extend(b[:65536 - len(err)])
        except Exception:
            pass

    th = threading.Thread(target=pump_err, daemon=True)
    th.start()
    if inp is not None:
        def feed():
            try:
                p.stdin.write(inp if isinstance(inp, bytes) else inp.encode("utf-8"))
            except Exception:
                pass
            finally:
                try:
                    p.stdin.close()
                except Exception:
                    pass
        threading.Thread(target=feed, daemon=True).start()
    deadline = time.time() + timeout
    killer = threading.Timer(timeout, lambda: _kill(p))
    killer.daemon = True
    killer.start()
    try:
        while True:
            b = p.stdout.read(65536)
            if not b:
                break
            if cap is not None and len(out) + len(b) > cap:
                out.extend(b[:max(0, cap - len(out))])
                truncated[0] = True
                _kill(p)
                break
            out.extend(b)
        p.wait(timeout=max(1, deadline - time.time() + 5))
    except Exception:
        _kill(p)
    finally:
        killer.cancel()
    th.join(timeout=5)
    rc = p.returncode
    if rc is None:
        rc = -9
    if time.time() > deadline and rc != 0:
        err.extend(b" (timed out)")
    return Result(rc if not truncated[0] else 0, bytes(out), err.decode("utf-8", "replace"),
                  truncated[0])


def _kill(p):
    try:
        p.kill()
    except Exception:
        pass


_IGNORECASE = {}


def _probe_ignorecase(d):
    """core.ignorecase for the filesystem holding `d` (APFS is usually
    case-insensitive; a case-sensitive volume must not be told otherwise).
    Probed once per directory per process."""
    if d in _IGNORECASE:
        return _IGNORECASE[d]
    _IGNORECASE[d] = val = _probe_ignorecase_now(d)
    return val


def _probe_ignorecase_now(d):
    try:
        fd, path = tempfile.mkstemp(dir=d, prefix=".hermes-CaSe-")
        os.close(fd)
        try:
            return os.path.exists(os.path.join(d, os.path.basename(path).lower()))
        finally:
            os.unlink(path)
    except OSError:
        return True


_MIRROR_LOCKS = {}
_MIRROR_LOCKS_LOCK = threading.Lock()


def mirror_lock(path):
    """One lock per mirror: a fetch, a snapshot and a push never interleave in
    the same object store and ref namespace."""
    with _MIRROR_LOCKS_LOCK:
        lk = _MIRROR_LOCKS.get(path)
        if lk is None:
            lk = _MIRROR_LOCKS[path] = threading.RLock()
        return lk


class Mirror(object):
    """The dashboard-owned bare mirror of one repository.

    path      <root>/<owner>__<repo>.git (validated by the caller to sit under root)
    work_dir  a dashboard-owned 0700 directory for HOME, index files and the
              empty template (never inside the sandbox)."""

    def __init__(self, path, work_dir, secrets=()):
        self.path = os.path.abspath(path)
        self.work_dir = os.path.abspath(work_dir)
        self.secrets = [s for s in secrets if isinstance(s, str) and s]
        self.home = os.path.join(self.work_dir, "home")
        self.template = os.path.join(self.work_dir, "empty-template")
        self.trace = None            # tests: callable(argv, env_keys) per invocation

    # ---- plumbing ------------------------------------------------------------
    def _dirs(self):
        for d in (self.work_dir, self.home, self.template):
            os.makedirs(d, mode=0o700, exist_ok=True)

    def exists(self):
        return os.path.isfile(os.path.join(self.path, "HEAD")) and \
            os.path.isdir(os.path.join(self.path, "objects"))

    def _assert_plain(self):
        """The mirror must be a real directory, not a symlink planted by
        something else on this Mac (it would make every write land there)."""
        try:
            st = os.lstat(self.path)
        except OSError:
            return
        if not os.path.isdir(self.path) or os.path.islink(self.path) or \
                (st.st_mode & 0o170000) != 0o040000:
            raise GitError("the mirror is not a plain directory")

    def ensure(self):
        """Create the bare mirror if missing (git init --bare, empty template)."""
        self._dirs()
        self._assert_plain()
        if self.exists():
            return False
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        r = self._raw(["init", "--quiet", "--bare", "--template=" + self.template, self.path],
                      git_dir=None, cwd=os.path.dirname(self.path))
        if not r.ok:
            raise GitError("git init failed: " + _scrub(r.err, self.secrets))
        os.chmod(self.path, 0o700)
        self.regen_config()
        return True

    def regen_config(self):
        """Rewrite config from TEMPLATE and drop every borrow-from-elsewhere
        file. Runs before every operation (see the module docstring)."""
        self._assert_plain()
        cfg = TEMPLATE.format(ignorecase="true" if _probe_ignorecase(self.path) else "false")
        self._cfg_expected = cfg.encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=self.path, prefix=".config-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(cfg)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, os.path.join(self.path, "config"))
            tmp = None
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        for rel in _FOREIGN_FILES:
            p = os.path.join(self.path, rel)
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    continue
                os.unlink(p)
            except OSError:
                pass
        info = os.path.join(self.path, "info")
        try:
            os.makedirs(info, mode=0o700, exist_ok=True)
            with open(os.path.join(info, "exclude"), "w", encoding="utf-8") as f:
                f.write("")
        except OSError:
            pass

    def _raw(self, args, git_dir="__self__", cwd=None, env_extra=None, inp=None,
             timeout=TIMEOUT, cap=None, allow_file=False):
        self._dirs()
        gd = self.path if git_dir == "__self__" else git_dir
        argv = [GIT_BIN] + hard_flags(allow_file) + list(args)
        env = _env(gd, self.home, env_extra)
        if callable(self.trace):
            try:
                self.trace(list(argv), dict(env))
            except Exception:
                pass
        return _exec(argv, env, cwd or (self.path if os.path.isdir(self.path) else "/"),
                     inp=inp, timeout=timeout, cap=cap)

    def run(self, args, env_extra=None, inp=None, timeout=TIMEOUT, cap=None,
            allow_file=False):
        """One hardened git invocation in the mirror (config regenerated first,
        and checked again after: E56)."""
        with mirror_lock(self.path):
            self.regen_config()
            r = self._raw(args, env_extra=env_extra, inp=inp, timeout=timeout, cap=cap,
                          allow_file=allow_file)
            self._check_untampered(args[0] if args else "")
            return r

    def _check_untampered(self, what):
        """Regeneration is check-then-use: a same-uid writer can rewrite the
        config between it and git reading it. So after every call the config
        must still be byte-identical to what was written and no borrow file
        may exist; otherwise the call is treated as tampered with (E56)."""
        why = None
        try:
            with open(os.path.join(self.path, "config"), "rb") as f:
                if f.read(65536) != getattr(self, "_cfg_expected", None):
                    why = "config"
        except OSError:
            why = "config"
        if why is None:
            for rel in _FOREIGN_FILES:
                p = os.path.join(self.path, rel)
                if os.path.lexists(p) and not (os.path.isdir(p) and not os.path.islink(p)):
                    why = rel
                    break
        if why is None:
            return
        for hook in list(TAMPER_HOOKS):
            try:
                hook(self.path, why)
            except Exception:
                pass
        raise GitTampered("the mirror's %s changed while git %s ran; Hermes stopped" % (
            why, re.sub(r"[^a-z-]", "", str(what))[:20]))

    def _ok(self, args, what, **kw):
        r = self.run(args, **kw)
        if not r.ok:
            raise GitError("%s failed: %s" % (what, _scrub(r.err, self.secrets +
                                                            _secrets_of(kw.get("env_extra")))))
        return r

    # ---- refs and objects ----------------------------------------------------
    def rev(self, ref):
        """The commit sha a LOCAL ref points at, or None. Local refs are never
        trusted as a parent; this is for comparisons only."""
        if not isinstance(ref, str) or not (ref.startswith("refs/") or SHA_RE.fullmatch(ref)):
            return None
        r = self.run(["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
        s = r.text().strip()
        return s if r.ok and SHA_RE.fullmatch(s) else None

    def has_commit(self, sha):
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            return False
        r = self.run(["cat-file", "-e", sha + "^{commit}"])
        return r.ok

    def refs(self, prefix):
        """{refname: sha} under a prefix (local refs)."""
        r = self.run(["for-each-ref", "--format=%(objectname) %(refname)", prefix])
        out = {}
        for ln in r.text().splitlines():
            sha, _, name = ln.partition(" ")
            if SHA_RE.fullmatch(sha) and name.startswith(prefix):
                out[name] = sha
        return out

    def ref_times(self, prefix):
        """{refname: committer time (unix)} under a prefix (local refs)."""
        r = self.run(["for-each-ref", "--format=%(committerdate:unix) %(refname)", prefix])
        out = {}
        for ln in r.text().splitlines():
            ts, _, name = ln.partition(" ")
            if name.startswith(prefix):
                out[name] = int(ts) if ts.isdigit() else 0
        return out

    def update_ref(self, ref, sha):
        if not SHA_RE.fullmatch(sha or "") or not ref.startswith("refs/"):
            raise GitError("bad ref update")
        self._ok(["update-ref", "--no-deref", ref, sha], "update-ref")

    def delete_ref(self, ref):
        if not ref.startswith("refs/"):
            raise GitError("bad ref")
        self.run(["update-ref", "--no-deref", "-d", ref])

    def set_head(self, branch):
        if not BRANCH_RE.fullmatch(branch or "") or ".." in branch:
            raise GitError("bad branch")
        self._ok(["symbolic-ref", "HEAD", "refs/heads/" + branch], "symbolic-ref")

    def tree_of(self, commit):
        r = self.run(["rev-parse", "--verify", "--quiet", commit + "^{tree}"])
        s = r.text().strip()
        return s if r.ok and SHA_RE.fullmatch(s) else None

    def commit_info(self, commit):
        """{tree, parents[]} straight from the commit object."""
        r = self._ok(["cat-file", "commit", commit], "cat-file")
        tree, parents = None, []
        for ln in r.text().split("\n"):
            if not ln:
                break
            if ln.startswith("tree "):
                tree = ln[5:].strip()
            elif ln.startswith("parent "):
                parents.append(ln[7:].strip())
        return {"tree": tree, "parents": parents}

    def blob(self, sha, cap):
        """(bytes, truncated) of one blob, at most `cap` bytes."""
        if not SHA_RE.fullmatch(sha or ""):
            return b"", False
        r = self.run(["cat-file", "blob", sha], cap=cap)
        return (r.out if r.ok else b""), r.truncated

    def sizes(self, shas):
        """{sha: size} for blobs (one cat-file --batch-check)."""
        shas = [s for s in shas if SHA_RE.fullmatch(s or "")]
        if not shas:
            return {}
        r = self.run(["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                     inp="\n".join(shas) + "\n")
        out = {}
        for ln in r.text().splitlines():
            parts = ln.split()
            if len(parts) == 3 and parts[1] == "blob" and parts[2].isdigit():
                out[parts[0]] = int(parts[2])
        return out

    def ls_tree(self, treeish, path):
        """[(mode, type, sha, name)] for one directory of a tree."""
        r = self.run(["ls-tree", "-z", treeish, "--", path.rstrip("/") + "/"])
        out = []
        for ent in r.out.split(b"\0") if r.ok else []:
            if not ent:
                continue
            meta, _, name = ent.partition(b"\t")
            parts = meta.decode("ascii", "replace").split()
            if len(parts) == 3:
                out.append((parts[0], parts[1], parts[2], name.decode("utf-8", "replace")))
        return out

    # ---- network -------------------------------------------------------------
    def fetch(self, url, refspecs, auth_env=None, prune=True):
        """git fetch <url> <refspecs...> — the token only via auth_env, for this
        one call. Refspecs are built by the caller from validated names."""
        for rs in refspecs:
            if not re.fullmatch(r"\+?refs/heads/[A-Za-z0-9._/*-]{1,200}:refs/hermes-remote/"
                                r"[A-Za-z0-9._/*-]{1,200}", rs) or ".." in rs:
                raise GitError("bad refspec")
        args = ["fetch", "--quiet", "--no-tags", "--no-recurse-submodules",
                "--no-write-fetch-head", "--no-auto-gc"]
        if prune:
            args.append("--prune")
        args.append(url)
        args += list(refspecs)
        self._ok(args, "fetch", env_extra=auth_env, timeout=NET_TIMEOUT,
                 allow_file=url_is_file(url) and test_mode())

    def push(self, url, commit, slug, auth_env=None):
        """Send exactly <commit>:refs/heads/hermes/<slug>. No other refspec, no
        --force, no --mirror, no --tags. -> (ok, porcelain_status_char, line)."""
        if not SHA_RE.fullmatch(commit or ""):
            raise GitError("bad commit")
        ref = branch_ref(slug)
        if not check_ref(ref):
            raise GitError("bad ref")
        r = self.run(["push", "--porcelain", "--no-verify", url, "%s:%s" % (commit, ref)],
                     env_extra=auth_env, timeout=NET_TIMEOUT,
                     allow_file=url_is_file(url) and test_mode())
        return self._porcelain(r, ref, auth_env)

    def delete_branch(self, url, slug, expect_sha, auth_env=None):
        """Delete refs/heads/hermes/<slug> on the remote ONLY if it still points
        at expect_sha (--force-with-lease=<ref>:<sha> is a compare-and-swap on
        the server; it rewrites nothing, and a branch that moved is left
        alone). -> (ok, status_char, line)."""
        if not SHA_RE.fullmatch(expect_sha or ""):
            raise GitError("bad sha")
        ref = branch_ref(slug)
        if not check_ref(ref):
            raise GitError("bad ref")
        r = self.run(["push", "--porcelain", "--no-verify",
                      "--force-with-lease=%s:%s" % (ref, expect_sha), url, ":" + ref],
                     env_extra=auth_env, timeout=NET_TIMEOUT,
                     allow_file=url_is_file(url) and test_mode())
        return self._porcelain(r, ref, auth_env)

    def _porcelain(self, r, ref, auth_env):
        sec = self.secrets + _secrets_of(auth_env)
        line, flag = "", ""
        for ln in r.text().splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2 and parts[1].endswith(":" + ref):
                flag, line = parts[0][:1], _scrub(ln, sec)
        if not line:
            line = _scrub(r.err, sec) or "no answer from git"
        return (r.ok and flag in ("*", " ", "-", "=")), flag, line

    # ---- snapshot ------------------------------------------------------------
    def snapshot(self, parent, work_tree, index_path):
        """Snapshot `work_tree`'s files on top of `parent` (§3.2 step 2):

            GIT_INDEX_FILE=<index_path> git read-tree <parent>
            git --work-tree=<sandbox> add -A
            git write-tree

        The sandbox's .git is never read (GIT_DIR is the mirror); its
        .gitignore files are data; symlinks are stored as link text. -> tree sha."""
        if not SHA_RE.fullmatch(parent or ""):
            raise GitError("bad parent")
        if not os.path.isdir(work_tree):
            raise GitError("the sandbox folder is missing")
        idx = {"GIT_INDEX_FILE": index_path}
        try:
            os.unlink(index_path)
        except OSError:
            pass
        try:
            with mirror_lock(self.path):
                self._ok(["read-tree", parent], "read-tree", env_extra=idx)
                self._ok(["--work-tree=" + work_tree, "add", "-A"], "add", env_extra=idx)
                r = self._ok(["write-tree"], "write-tree", env_extra=idx)
        finally:
            for p in (index_path, index_path + ".lock"):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        tree = r.text().strip()
        if not SHA_RE.fullmatch(tree):
            raise GitError("write-tree gave no tree")
        return tree

    def worktree_changes(self, parent, work_tree, index_path):
        """What snapshot() WOULD store, measured without writing one object
        (E09: refuse over-limit content before `add -A` copies it into the
        mirror). Untracked, not-ignored files come from `ls-files -o
        --exclude-standard` against a private index of `parent` (the same
        ignore rules `add -A` uses); a tracked file counts when it is gone or
        its size differs from the parent's blob (a same-size edit is not seen
        here, the post-snapshot C-size checks still are).
        -> {"files": [(path, size)], "deleted": n}."""
        if not SHA_RE.fullmatch(parent or ""):
            raise GitError("bad parent")
        if not os.path.isdir(work_tree):
            raise GitError("the sandbox folder is missing")
        idx = {"GIT_INDEX_FILE": index_path}
        try:
            os.unlink(index_path)
        except OSError:
            pass
        try:
            with mirror_lock(self.path):
                self._ok(["read-tree", parent], "read-tree", env_extra=idx)
                un = self._ok(["--work-tree=" + work_tree, "ls-files", "-z", "-o",
                               "--exclude-standard"], "ls-files", env_extra=idx)
                tr = self._ok(["ls-tree", "-r", "-z", "-l", "--full-tree", parent], "ls-tree")
        finally:
            for p in (index_path, index_path + ".lock"):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        files, deleted = [], 0

        def size_of(rel):
            try:
                st = os.lstat(os.path.join(work_tree, rel))
            except OSError:
                return None
            if (st.st_mode & 0o170000) not in (0o100000, 0o120000):
                return -1                                # a directory (nested repo)
            return st.st_size
        for ent in un.out.split(b"\0"):
            if not ent:
                continue
            rel = ent.decode("utf-8", "surrogateescape")
            sz = size_of(rel.rstrip("/"))
            if sz is not None:
                files.append((rel, max(sz, 0)))
        for ent in tr.out.split(b"\0"):
            if not ent:
                continue
            meta, _, name = ent.partition(b"\t")
            parts = meta.decode("ascii", "replace").split()
            if len(parts) != 4 or parts[1] != "blob":
                continue
            rel = name.decode("utf-8", "surrogateescape")
            sz = size_of(rel)
            if sz is None:
                deleted += 1
            elif sz >= 0 and (not parts[3].isdigit() or sz != int(parts[3])):
                files.append((rel, sz))
        return {"files": files, "deleted": deleted}

    def prune(self):
        """Delete every loose object no ref reaches (E09: the content of
        refused, withdrawn, rejected and expired changes). Callers hold a ref
        for every change that is still pending or pushing."""
        with mirror_lock(self.path):
            self._ok(["prune", "--expire=now"], "prune")

    def commit_tree(self, tree, parent, message, name, email, author_ts, committer_ts):
        """git commit-tree <tree> -p <parent>, message on stdin, identity and
        dates from the environment (never from any config)."""
        if not SHA_RE.fullmatch(tree or "") or not SHA_RE.fullmatch(parent or ""):
            raise GitError("bad tree/parent")
        ident = {"GIT_AUTHOR_NAME": str(name)[:100], "GIT_AUTHOR_EMAIL": str(email)[:200],
                 "GIT_COMMITTER_NAME": str(name)[:100], "GIT_COMMITTER_EMAIL": str(email)[:200],
                 "GIT_AUTHOR_DATE": "@%d +0000" % int(author_ts),
                 "GIT_COMMITTER_DATE": "@%d +0000" % int(committer_ts)}
        r = self._ok(["commit-tree", tree, "-p", parent], "commit-tree", env_extra=ident,
                     inp=message)
        sha = r.text().strip()
        if not SHA_RE.fullmatch(sha):
            raise GitError("commit-tree gave no commit")
        return sha

    def integrity(self, commit, parent, tree):
        """§3.2 step 4: rev-list <commit> --not <parent> is exactly [commit], and
        the commit object names exactly that tree and that one parent."""
        r = self.run(["rev-list", commit, "--not", parent])
        if not r.ok or r.text().split() != [commit]:
            return False
        info = self.commit_info(commit)
        return info["tree"] == tree and info["parents"] == [parent]

    def diff_raw(self, parent, tree):
        """[{status, old_mode, new_mode, old_sha, new_sha, path, old_path}]."""
        r = self._ok(["diff-tree", "-r", "-z", "-M", "--raw", "--no-ext-diff",
                      "--no-textconv", parent, tree], "diff-tree")
        toks = r.out.split(b"\0")
        out, i = [], 0
        while i < len(toks):
            t = toks[i]
            if not t.startswith(b":"):
                i += 1
                continue
            meta = t[1:].decode("ascii", "replace").split()
            if len(meta) < 5:
                i += 1
                continue
            om, nm, osha, nsha, st = meta[:5]
            code = st[:1]
            if code in ("R", "C"):
                old = toks[i + 1].decode("utf-8", "surrogateescape") if i + 1 < len(toks) else ""
                new = toks[i + 2].decode("utf-8", "surrogateescape") if i + 2 < len(toks) else ""
                i += 3
            else:
                old = None
                new = toks[i + 1].decode("utf-8", "surrogateescape") if i + 1 < len(toks) else ""
                i += 2
            out.append({"status": code, "score": st[1:], "old_mode": om, "new_mode": nm,
                        "old_sha": osha, "new_sha": nsha, "path": new, "old_path": old})
        return out

    def numstat(self, parent, tree):
        """{path: (add|None, del|None)} — None,None for a binary file."""
        r = self._ok(["diff-tree", "-r", "-z", "-M", "--numstat", "--no-ext-diff",
                      "--no-textconv", parent, tree], "numstat")
        toks = r.out.split(b"\0")
        out, i = {}, 0
        while i < len(toks):
            t = toks[i]
            if not t:
                i += 1
                continue
            # a file NAME may itself hold a tab: split off only the two counts
            parts = t.split(b"\t", 2)
            if len(parts) == 3 and parts[2] == b"":
                path = toks[i + 2].decode("utf-8", "surrogateescape") if i + 2 < len(toks) else ""
                i += 3
            elif len(parts) == 3:
                path = parts[2].decode("utf-8", "surrogateescape")
                i += 1
            else:
                i += 1
                continue
            a, d = parts[0].decode("ascii", "replace"), parts[1].decode("ascii", "replace")
            out[path] = ((int(a) if a.isdigit() else None), (int(d) if d.isdigit() else None))
        return out

    def patch(self, parent, tree, cap):
        """(text, truncated): the unified diff the card shows, computed once."""
        r = self.run(["diff-tree", "-r", "-M", "-p", "--no-ext-diff", "--no-textconv",
                      "--no-color", "--full-index", parent, tree], cap=cap)
        if not r.ok:
            raise GitError("diff failed: " + _scrub(r.err, self.secrets))
        return r.out.decode("utf-8", "surrogateescape"), r.truncated

    def patch_text(self, parent, tree, path, cap):
        """(text, truncated): the diff of ONE path with --text, i.e. what a file
        git calls binary actually contains (E02). The path is matched
        literally (GIT_LITERAL_PATHSPECS: a name like ':(glob)*' is a name)."""
        if not SHA_RE.fullmatch(parent or "") or not SHA_RE.fullmatch(tree or ""):
            raise GitError("bad tree/parent")
        if not isinstance(path, str) or not path:
            raise GitError("bad path")
        r = self.run(["diff-tree", "-r", "-p", "--text", "--no-ext-diff", "--no-textconv",
                      "--no-color", "--full-index", parent, tree, "--", path],
                     env_extra={"GIT_LITERAL_PATHSPECS": "1"}, cap=cap)
        if not r.ok:
            raise GitError("diff failed: " + _scrub(r.err, self.secrets))
        return r.out.decode("utf-8", "surrogateescape"), r.truncated


def clone_sandbox(mirror, dest, branch, work_dir):
    """Create the agent's sandbox: a clone of the dashboard's mirror with a dead
    push URL, no credential helper, no hooks and no ssh — defaults the agent can
    change (it owns that .git), which is why the dashboard never trusts it.
    Runs only when `dest` does not exist yet, so nothing agent-owned is ever
    executed. -> None, or raises GitError."""
    if not BRANCH_RE.fullmatch(branch or "") or ".." in branch:
        raise GitError("bad branch")
    if os.path.lexists(dest):
        raise GitError("the sandbox folder already exists")
    m = Mirror(mirror, work_dir)
    m._dirs()
    with mirror_lock(m.path):
        m.regen_config()
        args = ["clone", "--quiet", "--no-hardlinks", "--template=" + m.template,
                "--origin", "origin", "--branch", branch,
                "-c", "remote.origin.pushurl=" + DEAD_PUSHURL,
                "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null",
                "-c", "core.sshCommand=false",
                "file://" + m.path, dest]
        r = m._raw(args, git_dir=None, cwd=os.path.dirname(dest), allow_file=True,
                   timeout=NET_TIMEOUT)
    if not r.ok:
        raise GitError("clone failed: " + _scrub(r.err, ()))


def b64_needles(value):
    """Substrings that ANY base64 (and base64url) encoding containing `value`
    must contain, at each of the three byte alignments — so a secret embedded
    in a larger base64 blob is still found. Edge characters that depend on the
    neighbouring bytes are dropped."""
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    out = set()
    for k in (0, 1, 2):
        enc = base64.b64encode(b"\0" * k + raw).decode("ascii").rstrip("=")
        start = {0: 0, 1: 2, 2: 3}[k]
        rem = (k + len(raw)) % 3
        end = len(enc) - (1 if rem else 0)
        s = enc[start:end]
        if len(s) >= 8:
            out.add(s)
            out.add(s.replace("+", "-").replace("/", "_"))
    return out


def secret_forms(value):
    """Every text form of one known secret the canary scan looks for: raw,
    URL-encoded, hex (both cases) and the base64/base64url needles."""
    v = value if isinstance(value, str) else str(value)
    forms = {v, urllib.parse.quote(v, safe=""), urllib.parse.quote_plus(v)}
    hx = v.encode("utf-8").hex()
    forms.update({hx, hx.upper()})
    forms.update(b64_needles(v))
    return {f for f in forms if f and len(f) >= 8}
