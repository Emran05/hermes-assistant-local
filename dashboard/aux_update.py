# aux_update.py — version + self-update service for the dashboard.
#
# Answers three questions the Settings › System card asks:
#   1. what am I running?            GET  /api/version
#   2. is there something newer?     GET  /api/update/check[?force=1]
#   3. put it on this machine.       POST /api/update/apply   -> runs ../update.sh
#                                    GET  /api/update/status
#                                    POST /api/update/channel
#
# Sources for "what's newer", tried in order (each falls through on failure):
#   a. GitHub Releases API, unauthenticated + ETag, 6h cache in
#      ~/.hermes/dashboard/update-check.json.
#   b. the same call WITH a token (GITHUB_TOKEN / HERMES_UPDATE_TOKEN env, or
#      `gh auth token`) — the repo may still be private.
#   c. `git ls-remote --tags origin` on a git checkout: highest vX.Y.Z tag.
# On the `main` channel (git checkouts only) "latest" is origin/main's head
# commit instead of a tag.
#
# Everything is stdlib. Nothing here ever touches ~/.hermes DATA other than the
# two files it owns (update-check.json, update-state.json) — applying an update
# is entirely update.sh's job, started detached so it survives the dashboard
# restart it performs.
#
# AUX MODULE GOTCHA (CLAUDE.md): never `from datetime import datetime` in an
# aux module — it rebinds the shared global. Private alias only.
import datetime as _upd_datetime          # noqa: F401  (aliased per CLAUDE.md)
import json as _upd_json
import os as _upd_os
import re as _upd_re
import shutil as _upd_shutil
import subprocess as _upd_subprocess
import threading as _upd_threading
import time as _upd_time
import urllib.error as _upd_urlerr
import urllib.request as _upd_urlreq

# --------------------------------------------------------------------------
# paths / constants
# --------------------------------------------------------------------------
_UPD_ROOT = _upd_os.path.dirname(_upd_os.path.dirname(_upd_os.path.abspath(__file__)))

# The repo releases are published from. Resolution order:
#   1. HERMES_UPDATE_REPO env  (an owner pointing at a different repo)
#   2. the slug parsed out of `git remote get-url origin`, when that remote is
#      a github.com URL — so anyone who cloned checks the repo they cloned
#   3. the public default below.
_UPD_REPO_DEFAULT = "Emran05/hermes-assistant-local"
_UPD_SLUG_RE = _upd_re.compile(r"github\.com[:/]+([^/\s]+)/(.+?)(?:\.git)?/?$")


def _upd_origin_slug():
    """'owner/repo' from origin's URL, or None when it isn't a GitHub remote."""
    try:
        p = _upd_subprocess.run(["git", "-C", _UPD_ROOT, "remote", "get-url", "origin"],
                                capture_output=True, text=True, timeout=4)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    m = _UPD_SLUG_RE.search((p.stdout or "").strip())
    if not m:
        return None
    owner, repo = m.group(1).strip(), m.group(2).strip()
    return (owner + "/" + repo) if owner and repo else None


def _upd_repo():
    env = (_upd_os.environ.get("HERMES_UPDATE_REPO") or "").strip()
    if env and "/" in env:
        return env
    return _upd_origin_slug() or _UPD_REPO_DEFAULT


_UPD_REPO = _upd_repo()
_UPD_API = "https://api.github.com/repos/%s/releases/latest" % _UPD_REPO
_UPD_UA = "hermes-assistant-updater"
_UPD_TTL = 6 * 3600                      # cache lifetime for a successful check
_UPD_BG_EVERY = 6 * 3600                 # background re-check interval
_UPD_BG_FIRST = 60                       # first background check, after boot
_UPD_BUDGET = 5.0                        # max seconds a foreground check blocks

_UPD_DATA = DATA if "DATA" in globals() else _upd_os.path.join(  # noqa: F821
    _upd_os.path.expanduser("~"), ".hermes", "dashboard")
_UPD_CACHE_FILE = _upd_os.path.join(_UPD_DATA, "update-check.json")
_UPD_STATE_FILE = _upd_os.path.join(_UPD_DATA, "update-state.json")
_UPD_LOG = _upd_os.path.join(_upd_os.path.expanduser("~"), ".hermes", "logs",
                             "update.log")
_UPD_SCRIPT = _upd_os.path.join(_UPD_ROOT, "update.sh")

_UPD_CHANNELS = ("stable", "main")
_UPD_LOCK = _upd_threading.Lock()        # serialises network checks
_UPD_TOKEN_CACHE = {"tok": None, "at": 0.0}

try:
    _UPD_SSL = _ssl_context()            # noqa: F821  (certifi-backed, server.py)
except Exception:                        # pragma: no cover - defensive
    import ssl as _upd_ssl_mod
    _UPD_SSL = _upd_ssl_mod.create_default_context()


# --------------------------------------------------------------------------
# semver — pure, unit-tested. Tolerates a leading "v", 1-3 numeric parts,
# SemVer prereleases ("-rc.1") and build metadata ("+abc", ignored).
# --------------------------------------------------------------------------
_UPD_VER_RE = _upd_re.compile(
    r"^[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.\-]+))?(?:\+[0-9A-Za-z.\-]+)?$")


def _upd_parse_version(s):
    """'v0.10.0-rc.2' -> ((0,10,0), ('rc',2)).  None when it isn't a version."""
    if not s:
        return None
    m = _UPD_VER_RE.match(str(s).strip())
    if not m:
        return None
    nums = tuple(int(m.group(i) or 0) for i in (1, 2, 3))
    pre = m.group(4)
    if not pre:
        return (nums, ())
    ids = []
    for part in pre.split("."):
        ids.append(int(part) if part.isdigit() else part)
    return (nums, tuple(ids))


def _upd_cmp_pre(a, b):
    """SemVer prerelease ordering: no-prerelease > any prerelease; numeric
    identifiers rank below alphanumeric ones; more fields wins a tie."""
    if not a and not b:
        return 0
    if not a:
        return 1          # 1.0.0 > 1.0.0-rc.1
    if not b:
        return -1
    for x, y in zip(a, b):
        xn, yn = isinstance(x, int), isinstance(y, int)
        if xn and yn:
            if x != y:
                return -1 if x < y else 1
        elif xn != yn:
            return -1 if xn else 1        # numeric < alphanumeric
        else:
            if x != y:
                return -1 if x < y else 1
    if len(a) != len(b):
        return -1 if len(a) < len(b) else 1
    return 0


def _upd_cmp(a, b):
    """Compare two version strings. Unparseable sorts below anything parseable."""
    pa, pb = _upd_parse_version(a), _upd_parse_version(b)
    if pa is None and pb is None:
        return 0
    if pa is None:
        return -1
    if pb is None:
        return 1
    if pa[0] != pb[0]:
        return -1 if pa[0] < pb[0] else 1
    return _upd_cmp_pre(pa[1], pb[1])


def _upd_newer(latest, current):
    """True when `latest` is a strictly newer release than `current`."""
    if not latest or not current:
        return False
    return _upd_cmp(latest, current) > 0


def _upd_tags_from_ls_remote(text, allow_prerelease=False):
    """Parse `git ls-remote --tags origin` output -> version tags, newest last.

    Handles the peeled `refs/tags/v1.2.3^{}` duplicates git emits for
    annotated tags, and ignores anything that isn't a version."""
    seen = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "refs/tags/" not in line:
            continue
        ref = line.split("refs/tags/", 1)[1].strip()
        if ref.endswith("^{}"):
            ref = ref[:-3]
        p = _upd_parse_version(ref)
        if p is None:
            continue
        if p[1] and not allow_prerelease:
            continue
        seen[ref] = p
    tags = list(seen)
    tags.sort(key=lambda t: (seen[t][0], _UpdPreKey(seen[t][1])))
    return tags


class _UpdPreKey(object):
    """Sort adapter so _upd_cmp_pre can drive list.sort()."""
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __lt__(self, other):
        return _upd_cmp_pre(self.v, other.v) < 0

    def __eq__(self, other):
        return _upd_cmp_pre(self.v, other.v) == 0


def _upd_parse_release(obj):
    """GitHub release JSON -> the subset the UI needs."""
    if not isinstance(obj, dict):
        return None
    tag = obj.get("tag_name") or obj.get("name") or ""
    if not tag:
        return None
    assets = []
    for a in (obj.get("assets") or []):
        if not isinstance(a, dict):
            continue
        assets.append({"name": a.get("name") or "",
                       "url": a.get("browser_download_url") or "",
                       "size": a.get("size") or 0})
    return {"tag": str(tag),
            "notes": obj.get("body") or "",
            "url": obj.get("html_url") or
                   ("https://github.com/%s/releases/tag/%s" % (_UPD_REPO, tag)),
            "published_at": obj.get("published_at") or obj.get("created_at") or "",
            "prerelease": bool(obj.get("prerelease")),
            "assets": assets}


# --------------------------------------------------------------------------
# local version / checkout facts
# --------------------------------------------------------------------------
def _upd_version():
    try:
        with open(_upd_os.path.join(_UPD_ROOT, "VERSION")) as f:
            v = f.read().strip()
        return v or "0.0.0"
    except OSError:
        return "0.0.0"


def _upd_is_git():
    return _upd_os.path.isdir(_upd_os.path.join(_UPD_ROOT, ".git"))


def _upd_git(args, timeout=6):
    """Run git in the checkout. Returns (rc, stdout) — never raises."""
    if not _upd_shutil.which("git"):
        return (127, "")
    try:
        p = _upd_subprocess.run(["git", "-C", _UPD_ROOT] + list(args),
                                capture_output=True, text=True, timeout=timeout)
        return (p.returncode, (p.stdout or "").strip())
    except Exception:
        return (1, "")


def _upd_commit():
    if not _upd_is_git():
        return ""
    rc, out = _upd_git(["rev-parse", "--short", "HEAD"])
    return out if rc == 0 else ""


def _upd_dirty_files(limit=40):
    """Tracked-file changes that would block a checkout. [] when clean/tarball."""
    if not _upd_is_git():
        return []
    rc, out = _upd_git(["status", "--porcelain", "--untracked-files=no"])
    if rc != 0 or not out:
        return []
    files = []
    for line in out.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip())
    return files[:limit]


def _upd_channel():
    try:
        s = get_settings()               # noqa: F821
    except Exception:
        s = {}
    ch = ((s.get("update") or {}) if isinstance(s.get("update"), dict) else {}).get("channel")
    if ch not in _UPD_CHANNELS:
        return "stable"
    if ch == "main" and not _upd_is_git():
        return "stable"                  # main only makes sense on a checkout
    return ch


def _upd_set_channel(ch):
    if ch not in _UPD_CHANNELS:
        return {"ok": False, "error": "unknown channel: %s" % ch}, 400
    if ch == "main" and not _upd_is_git():
        return {"ok": False,
                "error": "the main channel needs a git checkout (this is a "
                         "tarball install)"}, 400
    try:
        s = get_settings()               # noqa: F821
        u = s.get("update")
        if not isinstance(u, dict):
            u = {}
        u["channel"] = ch
        s["update"] = u
        with _state_lock:                # noqa: F821
            write_json(SETTINGS_FILE, s)   # noqa: F821
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500
    return {"ok": True, "channel": ch}


# --------------------------------------------------------------------------
# GitHub access
# --------------------------------------------------------------------------
def _upd_token():
    """A token for the private-repo case: env first, then `gh auth token`.
    Cached 10 minutes. Never logged, never returned over HTTP."""
    for k in ("HERMES_UPDATE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        v = _upd_os.environ.get(k)
        if v:
            return v.strip()
    c = _UPD_TOKEN_CACHE
    if c["tok"] and (_upd_time.time() - c["at"]) < 600:
        return c["tok"]
    if not _upd_shutil.which("gh"):
        return None
    try:
        p = _upd_subprocess.run(["gh", "auth", "token"], capture_output=True,
                                text=True, timeout=4)
        tok = (p.stdout or "").strip() if p.returncode == 0 else ""
    except Exception:
        tok = ""
    if tok:
        c["tok"] = tok
        c["at"] = _upd_time.time()
        return tok
    return None


def _upd_fetch(url, etag=None, token=None, timeout=4.0, opener=None):
    """GET a GitHub API URL. -> (status, obj_or_None, etag).

    status: 200 fresh body · 304 not modified · 401/403/404 no access ·
            0 network/parse failure."""
    headers = {"User-Agent": _UPD_UA,
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if etag:
        headers["If-None-Match"] = etag
    if token:
        headers["Authorization"] = "Bearer " + token
    req = _upd_urlreq.Request(url, headers=headers)
    op = opener or (lambda r, t: _upd_urlreq.urlopen(r, timeout=t, context=_UPD_SSL))
    try:
        resp = op(req, timeout)
        with resp:
            raw = resp.read().decode("utf-8", "replace")
            tag = None
            try:
                tag = resp.headers.get("ETag")
            except Exception:
                tag = None
            try:
                return (200, _upd_json.loads(raw), tag)
            except ValueError:
                return (0, None, None)
    except _upd_urlerr.HTTPError as e:
        code = getattr(e, "code", 0) or 0
        if code == 304:
            return (304, None, etag)
        return (code, None, None)
    except Exception:
        return (0, None, None)


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------
def _upd_cache_read():
    return read_json(_UPD_CACHE_FILE, {}) or {}    # noqa: F821


def _upd_cache_write(obj):
    try:
        _upd_os.makedirs(_UPD_DATA, exist_ok=True)
        write_json(_UPD_CACHE_FILE, obj)           # noqa: F821
    except Exception:
        pass


def _upd_payload(current, channel, latest=None, notes="", url="",
                 published_at="", assets=None, source="none", error=""):
    latest = latest or ""
    return {"current": current,
            "latest": latest,
            "update_available": _upd_newer(latest, current),
            "notes": notes or "",
            "url": url or ("https://github.com/%s/releases" % _UPD_REPO),
            "published_at": published_at or "",
            "assets": assets or [],
            "checked_at": _upd_time.time(),
            "channel": channel,
            "source": source,
            "error": error or ""}


def _upd_check_main_channel(current, budget):
    """`main` channel: newest origin/main commit vs local HEAD."""
    rc, out = _upd_git(["ls-remote", "origin", "refs/heads/main"],
                       timeout=max(2.0, min(budget, 6.0)))
    remote = out.split()[0] if (rc == 0 and out.split()) else ""
    if not remote:
        return _upd_payload(current, "main", source="none",
                            error="could not reach origin")
    rc2, head = _upd_git(["rev-parse", "HEAD"])
    head = head if rc2 == 0 else ""
    same = bool(head) and head == remote
    short = remote[:7]
    p = _upd_payload(current, "main", latest="main@" + short,
                     notes="Tracks origin/main — the development branch. "
                           "Newer than any release tag, and not release-tested.",
                     url="https://github.com/%s/commits/main" % _UPD_REPO,
                     source="main")
    p["update_available"] = not same
    p["remote_head"] = short
    p["local_head"] = head[:7]
    return p


def _upd_check(force=False, budget=_UPD_BUDGET, opener=None):
    """Resolve the newest available version. Cached; never blocks past budget."""
    current = _upd_version()
    channel = _upd_channel()
    now = _upd_time.time()
    cached = _upd_cache_read()
    fresh = (cached.get("payload") and cached.get("channel") == channel and
             (now - float(cached.get("checked_at") or 0)) < _UPD_TTL)
    if fresh and not force:
        p = dict(cached["payload"])
        p["current"] = current
        p["update_available"] = (p.get("update_available") if p.get("source") == "main"
                                 else _upd_newer(p.get("latest"), current))
        p["cached"] = True
        return p

    # Never queue behind another check (the 6-hourly background one runs with a
    # long budget): if someone else holds the lock, answer from cache.
    if not _UPD_LOCK.acquire(timeout=min(1.0, max(0.2, budget / 5.0))):
        p = dict(cached.get("payload") or
                 _upd_payload(current, channel, source="none", error="check in progress"))
        p["current"] = current
        p["checking"] = True
        return p
    try:
        deadline = _upd_time.time() + max(1.0, budget)

        if channel == "main":
            p = _upd_check_main_channel(current, deadline - _upd_time.time())
            if p.get("source") == "main":
                _upd_cache_write({"checked_at": now, "channel": channel,
                                  "etag": "", "payload": p})
            elif cached.get("payload"):
                stale = dict(cached["payload"])
                stale["stale"] = True
                stale["error"] = p.get("error") or ""
                return stale
            return p

        etag = cached.get("etag") if cached.get("channel") == channel else None
        remain = lambda: max(0.5, deadline - _upd_time.time())   # noqa: E731

        # a. unauthenticated
        status, obj, new_etag = _upd_fetch(_UPD_API, etag=etag,
                                           timeout=min(4.0, remain()), opener=opener)
        source = "github"

        # b. authenticated (private repo / rate limited)
        if status in (401, 403, 404) and _upd_time.time() < deadline:
            tok = _upd_token()
            if tok:
                status, obj, new_etag = _upd_fetch(
                    _UPD_API, etag=etag, token=tok,
                    timeout=min(4.0, remain()), opener=opener)
                source = "github-token"

        if status == 304 and cached.get("payload"):
            p = dict(cached["payload"])
            p["current"] = current
            p["checked_at"] = now
            p["update_available"] = _upd_newer(p.get("latest"), current)
            _upd_cache_write({"checked_at": now, "channel": channel,
                              "etag": etag, "payload": p})
            return p

        if status == 200 and obj is not None:
            rel = _upd_parse_release(obj)
            if rel:
                p = _upd_payload(current, channel, latest=rel["tag"],
                                 notes=rel["notes"], url=rel["url"],
                                 published_at=rel["published_at"],
                                 assets=rel["assets"], source=source)
                _upd_cache_write({"checked_at": now, "channel": channel,
                                  "etag": new_etag or etag, "payload": p})
                return p

        # c. git tags on a checkout (works while the repo is private, over ssh
        #    or an authenticated https helper)
        if _upd_is_git() and _upd_time.time() < deadline:
            rc, out = _upd_git(["ls-remote", "--tags", "origin"],
                               timeout=min(6.0, max(2.0, remain())))
            if rc == 0:
                tags = _upd_tags_from_ls_remote(out)
                if tags:
                    top = tags[-1]
                    p = _upd_payload(
                        current, channel, latest=top,
                        notes="Release notes are not readable without access to "
                              "the GitHub Releases API (private repo or offline). "
                              "Tag resolved from origin.",
                        url="https://github.com/%s/releases/tag/%s" % (_UPD_REPO, top),
                        source="git-tags")
                    p["tags"] = tags[-10:]
                    _upd_cache_write({"checked_at": now, "channel": channel,
                                      "etag": "", "payload": p})
                    return p

        # nothing worked — hand back the last good answer, marked stale
        err = {0: "network unreachable", 401: "not authorised",
               403: "rate limited or not authorised",
               404: "no release found (repo private, or no releases yet)"
               }.get(status, "check failed (HTTP %s)" % status)
        if cached.get("payload"):
            p = dict(cached["payload"])
            p["current"] = current
            p["stale"] = True
            p["error"] = err
            return p
        return _upd_payload(current, channel, source="none", error=err)
    finally:
        _UPD_LOCK.release()


# --------------------------------------------------------------------------
# apply / status
# --------------------------------------------------------------------------
def _upd_state_read():
    return read_json(_UPD_STATE_FILE, {}) or {}    # noqa: F821


def _upd_state_write(obj):
    try:
        _upd_os.makedirs(_UPD_DATA, exist_ok=True)
        write_json(_UPD_STATE_FILE, obj)           # noqa: F821
    except Exception:
        pass


def _upd_pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        _upd_os.kill(pid, 0)
        return True
    except OSError:
        return False


def _upd_running():
    """(running, state) — reconciles a stale `running` flag from a killed run."""
    st = _upd_state_read()
    if not st.get("running"):
        return False, st
    if _upd_pid_alive(st.get("pid")):
        # a run that has been "going" for over an hour is wedged, not running
        if (_upd_time.time() - float(st.get("started_at") or 0)) < 3600:
            return True, st
    st["running"] = False
    if not st.get("last_result"):
        st["last_result"] = {"ok": False, "message": "update process ended "
                                                     "without writing a result",
                             "finished_at": _upd_time.time(),
                             "target": st.get("target") or ""}
    _upd_state_write(st)
    return False, st


def _upd_log_tail(limit_bytes=12000, max_lines=120):
    try:
        size = _upd_os.path.getsize(_UPD_LOG)
        with open(_UPD_LOG, "rb") as f:
            if size > limit_bytes:
                f.seek(size - limit_bytes)
                f.readline()
            data = f.read()
    except OSError:
        return ""
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def _upd_known_tags():
    """Tags we are willing to accept as an apply target."""
    tags = set()
    cached = _upd_cache_read().get("payload") or {}
    if cached.get("latest"):
        tags.add(cached["latest"])
    for t in cached.get("tags") or []:
        tags.add(t)
    if _upd_is_git():
        rc, out = _upd_git(["tag", "--list"], timeout=5)
        if rc == 0:
            for t in out.split():
                if _upd_parse_version(t):
                    tags.add(t)
        rc, out = _upd_git(["ls-remote", "--tags", "origin"], timeout=6)
        if rc == 0:
            for t in _upd_tags_from_ls_remote(out, allow_prerelease=True):
                tags.add(t)
    return tags


def _upd_apply(ctx):
    body = ctx.body if isinstance(getattr(ctx, "body", None), dict) else {}
    target = str(body.get("target") or "latest").strip()
    channel = _upd_channel()

    running, _st = _upd_running()
    if running:
        return {"ok": False, "reason": "already_running",
                "error": "an update is already running", "log": _UPD_LOG}, 409

    if not _upd_os.path.exists(_UPD_SCRIPT):
        return {"ok": False, "reason": "no_script",
                "error": "update.sh is missing from %s" % _UPD_ROOT}, 500

    if target not in ("latest", "main"):
        if not _upd_parse_version(target):
            return {"ok": False, "reason": "bad_target",
                    "error": "not a version tag: %s" % target}, 400
        if target not in _upd_known_tags():
            return {"ok": False, "reason": "unknown_target",
                    "error": "no such release tag on origin: %s" % target}, 400

    dirty = _upd_dirty_files()
    if dirty:
        return {"ok": False, "reason": "dirty",
                "error": "this checkout has uncommitted changes — commit, stash "
                         "or discard them first (or run ./update.sh --force in a "
                         "terminal)",
                "dirty": dirty}, 409

    env = dict(_upd_os.environ)
    env["PATH"] = (env.get("PATH", "") +
                   ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
    env["HERMES_UPDATE_FROM"] = "dashboard"
    cmd = ["/bin/bash", _UPD_SCRIPT, "--yes", "--channel", channel,
           "--target", target]
    if _upd_os.path.exists("/usr/bin/nohup"):
        cmd = ["/usr/bin/nohup"] + cmd
    try:
        _upd_os.makedirs(_upd_os.path.dirname(_UPD_LOG), exist_ok=True)
        logf = open(_UPD_LOG, "ab")
    except OSError as e:
        return {"ok": False, "reason": "log_unwritable", "error": str(e)}, 500
    try:
        # start_new_session detaches it from this process group, so it survives
        # the dashboard restart that install-services.sh performs mid-update.
        # VERIFIED against a throwaway launchd job: a nohup + start_new_session
        # child keeps running (to completion) after `launchctl bootout` of the
        # job that spawned it — which is exactly the bootout/bootstrap pair
        # install-services.sh runs on com.hermes.dashboard.
        proc = _upd_subprocess.Popen(
            cmd, cwd=_UPD_ROOT, stdout=logf, stderr=logf,
            stdin=_upd_subprocess.DEVNULL, env=env, start_new_session=True)
    except Exception as e:
        try:
            logf.close()
        except Exception:
            pass
        return {"ok": False, "reason": "spawn_failed",
                "error": "%s: %s" % (type(e).__name__, e)}, 500
    try:
        logf.close()
    except Exception:
        pass

    _upd_state_write({"running": True, "pid": proc.pid, "target": target,
                      "channel": channel, "started_at": _upd_time.time(),
                      "from_version": _upd_version(),
                      "last_result": _upd_state_read().get("last_result")})
    return {"ok": True, "started": True, "log": _UPD_LOG, "pid": proc.pid,
            "target": target, "channel": channel}


def _upd_status(ctx=None):
    running, st = _upd_running()
    return {"running": running,
            "target": st.get("target") or "",
            "started_at": st.get("started_at") or 0,
            "last_result": st.get("last_result") or None,
            "log": _UPD_LOG,
            "log_tail": _upd_log_tail()}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def _upd_version_route(ctx=None):
    return {"version": _upd_version(),
            "commit": _upd_commit(),
            "dirty": bool(_upd_dirty_files(limit=1)),
            "checkout": "git" if _upd_is_git() else "tarball",
            "channel": _upd_channel(),
            "repo": _UPD_REPO}


def _upd_check_route(ctx):
    force = False
    try:
        force = str(ctx.q1("force", "")) in ("1", "true", "yes")
    except Exception:
        force = False
    return _upd_check(force=force)


def _upd_channel_route(ctx):
    body = ctx.body if isinstance(getattr(ctx, "body", None), dict) else {}
    return _upd_set_channel(str(body.get("channel") or "").strip())


register_get("/api/version", _upd_version_route)          # noqa: F821
register_get("/api/update/check", _upd_check_route)       # noqa: F821
register_get("/api/update/status", _upd_status)           # noqa: F821
register_post("/api/update/apply", _upd_apply)            # noqa: F821
register_post("/api/update/channel", _upd_channel_route)  # noqa: F821


# --------------------------------------------------------------------------
# quiet background re-check: once a minute after boot, then every 6h
# --------------------------------------------------------------------------
def _upd_bg_loop():
    _upd_time.sleep(_UPD_BG_FIRST)
    while True:
        try:
            _upd_check(force=True, budget=20.0)
        except Exception:
            pass
        _upd_time.sleep(_UPD_BG_EVERY)


if not globals().get("_UPD_BG_STARTED"):
    _UPD_BG_STARTED = True
    try:
        _upd_threading.Thread(target=_upd_bg_loop, daemon=True,
                              name="update-check").start()
    except Exception:
        pass
