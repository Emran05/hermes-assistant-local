# aux_github.py — the GitHub sandbox CONNECTOR (round 3, cluster R3-GH1).
#
# docs/plans/github-sandbox-2026-10.md §1 (auth, scope wall, kill switch,
# revoke), §2 (token custody, ambient access), §3.6 (the 6-hourly read-only
# check + drift detector), §5.1 (routes, agent-callable vs owner-only), §8.1.
#
# Exec'd into server.py's globals by the aux loader (sorted, so this runs
# BEFORE aux_needsyou/aux_network/aux_watchtower and AFTER aux_ghchanges if
# that module exists): every foreign global (recorder_record_local,
# _wt_send_telegram, _ghc_pending, _ghc_cancel_all, _ssl_context) is resolved
# BY NAME AT CALL TIME, never captured at load.
#
# What it owns
#   ~/.hermes/dashboard/github/            (0700)
#       credential.json   0600  {kind, token, login, user_id, created,
#                                expires_at, verified_at, probe{}}
#       state.json        0600  allowlist + per-repo facts + settings + holds
#       ledger.jsonl      0600  append-only: every ref Hermes pushed / PR it
#                               opened (WRITTEN by the change pipeline through
#                               _gh_ledger_append; READ here by the drift
#                               detector)
#
# Token custody (the design's law, enforced here):
#   * the token is read ONLY from credential.json, ONLY by this process;
#   * it never appears in a response, a log line, a recorder row, state.json,
#     settings.json, a config export, argv or a git config — it reaches git
#     only through _gh_git_env() (the GIT_CONFIG_COUNT triple, for the
#     seconds of one network git call; gh_git.py's job), and HTTP only as an
#     Authorization header to the resolved API base;
#   * every upstream string that is surfaced runs through gh_api.scrub().
#
# Owner-only vs agent-callable (§5.1 asymmetry rule): a request that REDUCES
# capability (pause on, remove a repo, disconnect, revoke, narrower settings)
# needs no proof of owner. A request that INCREASES it (connect, add a repo,
# pause off, wider settings, check — it can clear a hold, drift ack) is
# OWNER-ONLY: each such handler starts with `_gh_owner_refused(ctx)`, which is
# the F05 hook (`ctx.nonce_ok is False` -> 403; None = pre-F05, tolerated),
# and `_gh_route_gated(method, path, body)` is registered in
# ROUTE_OWNER_GATES so F05's server-side gate can enforce the same split
# without reading this file. Until F05 ships, same-uid callers can reach the
# owner-only routes (B02): the token's scope, expiry, GitHub's branch rules,
# the drift detector and the Telegram notice per push are the bound.
#
# Stdlib only. Every new name is GH_/_gh_ prefixed. datetime only under a
# private alias (CLAUDE.md aux-module gotcha). No settings.json writes — this
# module keeps its own store under one lock, and nothing slow runs under it.

import os
import re
import sys
import json
import time
import shutil
import socket as _gh_socket
import tempfile
import threading
import subprocess
import datetime as _gh_datetime

try:
    import gh_api as _gh_mod
except Exception:                                    # pragma: no cover
    import importlib.util as _gh_ilu
    _gh_spec = _gh_ilu.spec_from_file_location(
        "gh_api", os.path.join(globals().get("HERE") or os.path.dirname(
            os.path.abspath(__file__ if "__file__" in globals() else ".")), "gh_api.py"))
    _gh_mod = _gh_ilu.module_from_spec(_gh_spec)
    _gh_spec.loader.exec_module(_gh_mod)

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
GH_DIR = os.path.join(globals().get("DATA") or os.path.join(HOME, ".hermes", "dashboard"),
                      "github")                                     # noqa: F821
GH_SANDBOX_ROOT = os.path.join(HOME, "HermesSandbox", "github")    # noqa: F821
GH_API_BASE, GH_GIT_BASE, _GH_BASES_OVERRIDDEN = _gh_mod.resolve_bases(os.environ)

GH_MAX_REPOS = _gh_mod.GH_MAX_REPOS          # 10
GH_MAX_PUSHES_DAY = 10                       # owner decision: hard ceiling
GH_CHECK_INTERVAL = 6 * 3600                 # the 6-hourly read-only check
GH_CHECK_START_DELAY = 90                    # first look after dashboard start
GH_LOOP_TICK = 600
GH_DRIFT_MAX = GH_MAX_REPOS * 300            # list_hermes_heads reads <= 300 per repo (E54)
GH_DRIFT_RETRY_REPOS = 5                     # undelivered summaries retried per check
# GitHub drift and push notices are SECURITY notices: they go out whatever
# the watchtower notification masters (briefings/news) say, and in quiet
# hours (owner decision, D8: ping_on_push is always true). E77.
GH_SECURITY_NOTICES_IGNORE_MASTERS = True
GH_CHECK_RETRY_BASE = 600                    # after a failed check: 10, 20, 40… min (E52)
GH_CHECK_MIN_GAP = 30                        # POST /check spam guard
GH_EXPIRY_WARN_DAYS = 7
GH_AMBIENT_TTL = 300
GH_TOKEN_MAX = 300
GH_TIMEOUT = 15
GH_FORGOTTEN_MAX = 50                        # removed repositories remembered (E07)
# where the read-only ambient detector finds git / security / ssh-add. A
# module constant (tests point it at stubs); never taken from the request.
GH_AMBIENT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
GH_ENV_TOKEN_NAMES = ("GITHUB_TOKEN", "GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_PAT",
                      "HERMES_UPDATE_TOKEN")
GH_LEDGER_EVENTS = ("push", "pr_open", "pr_close", "ref_delete")
# wall findings that mean "the key can do MORE than Hermes accepts" -> hold
GH_FORBIDDEN_FINDINGS = ("administration", "secrets", "variables", "webhooks", "actions",
                         "unlisted_private", "unlisted_write", "too_many",
                         "git_ssh_keys", "gpg_keys", "ssh_signing_keys")
# repository permissions a GET-only check can re-observe on any connected repo
# (a fine-grained key carries ONE permission set for all its repositories)
GH_REPO_PERMISSION_CAUSES = ("administration", "secrets", "variables", "webhooks", "actions")
# the change pipeline's C-size / C-rate / TTL limits (design §3.3-§3.4, D12),
# kept here so the card, the pipeline and the status agree on one set
GH_LIMITS = {"max_repos": GH_MAX_REPOS, "max_pushes_day": GH_MAX_PUSHES_DAY,
             "max_files": 200, "max_bytes": 5 * 1024 * 1024, "max_file": 2 * 1024 * 1024,
             "max_diff": 1024 * 1024, "max_repo_kb": 2 * 1024 * 1024,
             "pending_ttl_s": 24 * 3600, "repo_requests_per_10min": 3}

GH_ROUTES_OWNER_ONLY = (
    "POST /api/github/connect", "POST /api/github/repos {op:add}",
    "POST /api/github/pause {paused:false}", "POST /api/github/settings (widening)",
    "POST /api/github/check", "POST /api/github/drift/ack",
    "POST /api/github/changes/decide {decision:push_pr|push_only}",
    "POST /api/github/changes/retract")
GH_ROUTES_ANYONE = (
    "GET /api/github/status", "GET /api/github/ambient", "GET /api/github/create_url",
    "POST /api/github/pause {paused:true}", "POST /api/github/repos {op:remove}",
    "POST /api/github/settings (narrowing)", "POST /api/github/disconnect",
    "POST /api/github/revoke", "POST /api/github/sandbox/prepare",
    "POST /api/github/sandbox/refresh", "POST /api/github/changes",
    "GET /api/github/changes", "POST /api/github/changes/withdraw",
    "POST /api/github/changes/decide {decision:reject}")

_GH_LOCK = globals().get("_GH_LOCK") or threading.RLock()          # store
_GH_NET_LOCK = globals().get("_GH_NET_LOCK") or threading.Lock()   # one wall/check at a time
_GH_LEDGER_LOCK = globals().get("_GH_LEDGER_LOCK") or threading.Lock()
_GH_AMBIENT = globals().get("_GH_AMBIENT") or {"at": 0.0, "val": None}
_GH_CHECK_LAST = globals().get("_GH_CHECK_LAST") or {"at": 0.0}
# when a request last went to GitHub, in memory (aux_google's _GOOG_NET): a
# REFUSED connect writes nothing to disk, but it did leave this Mac
_GH_NET = globals().get("_GH_NET") or {"last_out": 0.0}


# --------------------------------------------------------------------------
# paths + atomic files
# --------------------------------------------------------------------------
def _gh_p(name):
    return os.path.join(GH_DIR, name)


def _gh_ensure_dir():
    os.makedirs(GH_DIR, mode=0o700, exist_ok=True)
    try:
        if os.stat(GH_DIR).st_mode & 0o077:
            os.chmod(GH_DIR, 0o700)
    except OSError:
        pass


def _gh_atomic_write(path, obj):
    """tmp + fchmod(600) + fsync + os.replace (the _goog_atomic_write idiom):
    never a partial file, never wider than 0600, in a 0700 directory."""
    _gh_ensure_dir()
    raw = json.dumps(obj, indent=1, sort_keys=True).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".gh_")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _gh_read_json(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _gh_unlink(path):
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def _gh_log(msg):
    """One stderr line, scrubbed of anything token-shaped."""
    try:
        print("[aux_github] " + _gh_mod.short(msg, 300, _gh_token_quiet() or ""),
              file=sys.stderr, flush=True)
    except Exception:
        pass


def _gh_sslctx():
    try:
        fn = globals().get("_ssl_context")
        if callable(fn):
            return fn()
    except Exception:
        pass
    return None


def _gh_now():
    return time.time()


def _gh_iso(ts):
    try:
        return _gh_datetime.datetime.fromtimestamp(
            float(ts), tz=_gh_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


# --------------------------------------------------------------------------
# credential (never leaves this module except through _gh_token/_gh_api/
# _gh_git_env, and never in a response)
# --------------------------------------------------------------------------
def _gh_cred():
    """The stored credential dict, or None. Tightens a loosened mode."""
    path = _gh_p("credential.json")
    try:
        if os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o600)
    except OSError:
        return None
    c = _gh_read_json(path)
    if not isinstance(c, dict) or c.get("kind") != "fine_grained_pat":
        return None
    if _gh_mod.classify_token(c.get("token")) != "fine_grained_pat":
        return None
    return c


def _gh_token_quiet():
    try:
        c = _gh_cred()
        return c.get("token") if c else None
    except Exception:
        return None


def _gh_token():
    """INTERNAL. The connector's token, or None. Never put it in a response,
    a log, a recorder row, argv, a git config or a remote URL."""
    return _gh_token_quiet()


def _gh_scrub(text):
    return _gh_mod.short(text, 300, _gh_token_quiet() or "")


def _gh_api(token=None):
    """A GitHubAPI bound to the stored token (or `token`), or None."""
    tok = token or _gh_token()
    if not tok:
        return None
    return _gh_mod.GitHubAPI(GH_API_BASE, tok, ssl_context=_gh_sslctx(), timeout=GH_TIMEOUT)


def _gh_git_base():
    return GH_GIT_BASE


def _gh_git_env():
    """The env triple that carries the token to ONE network git call
    (fetch/push in the dashboard-owned mirror). {} when not connected."""
    tok = _gh_token()
    return _gh_mod.git_auth_env(tok, GH_GIT_BASE) if tok else {}


def _gh_identity():
    """The owner's GitHub noreply identity for commits Hermes builds (D6)."""
    c = _gh_cred() or {}
    login, uid = c.get("login"), c.get("user_id")
    if not login:
        return None
    email = ("%s+%s@users.noreply.github.com" % (uid, login)) if uid else \
        ("%s@users.noreply.github.com" % login)
    return {"login": login, "user_id": uid, "name": login, "email": email}


# --------------------------------------------------------------------------
# state.json (allowlist, per-repo facts, settings, holds, drift)
# --------------------------------------------------------------------------
# one sentence for "the CI scan could not run", shared by connect, add and check
# (the check recognises its own earlier block by this prefix)
GH_CI_UNREADABLE = ("Hermes could not read this repository's workflows, so it cannot tell "
                    "whether a hermes/ branch would run CI with your secrets. Press Check "
                    "to try again.")

_GH_DEFAULT_SETTINGS = {"writes_paused": False, "checks_enabled": True,
                        "max_pushes_day": GH_MAX_PUSHES_DAY}


def _gh_norm_state(s):
    s = s if isinstance(s, dict) else {}
    out = {"v": 1}
    repos = s.get("repos") if isinstance(s.get("repos"), dict) else {}
    out["repos"] = {k: v for k, v in repos.items()
                    if isinstance(k, str) and isinstance(v, dict) and v.get("full_name")}
    st = dict(_GH_DEFAULT_SETTINGS)
    raw = s.get("settings") if isinstance(s.get("settings"), dict) else {}
    for k in ("writes_paused", "checks_enabled"):
        if isinstance(raw.get(k), bool):
            st[k] = raw[k]
    try:
        st["max_pushes_day"] = max(0, min(GH_MAX_PUSHES_DAY, int(raw.get(
            "max_pushes_day", GH_MAX_PUSHES_DAY))))
    except (TypeError, ValueError):
        pass
    # owner decisions, not settings: every push pings Telegram, PRs are drafts
    st["ping_on_push"] = True
    st["draft_prs"] = True
    st["limits"] = dict(GH_LIMITS, max_pushes_day=st["max_pushes_day"])
    out["settings"] = st
    out["pause"] = s.get("pause") if isinstance(s.get("pause"), dict) else None
    out["holds"] = {k: v for k, v in (s.get("holds") or {}).items()
                    if isinstance(v, dict)} if isinstance(s.get("holds"), dict) else {}
    # every row is kept (each repository holds at most 300 hermes/ heads), so
    # an alert already sent is never "new" again (E54)
    out["drift"] = [d for d in (s.get("drift") or []) if isinstance(d, dict)][-GH_DRIFT_MAX:] \
        if isinstance(s.get("drift"), list) else []
    for k in ("last_check", "last_read", "last_revoke", "connected_at"):
        v = s.get(k)
        out[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    out["last_check_ok"] = s.get("last_check_ok") if isinstance(s.get("last_check_ok"), bool) \
        else None
    out["last_check_error"] = str(s.get("last_check_error") or "")[:300] or None
    cf = s.get("check_fails")
    out["check_fails"] = max(0, min(1000, cf)) if isinstance(cf, int) and \
        not isinstance(cf, bool) else 0
    out["probe"] = s.get("probe") if isinstance(s.get("probe"), dict) else None
    # repositories the owner removed (E07/E53): their names and ids stay KNOWN
    # to the reach check (the owner listed them once) until the next connect
    fg = []
    for f in (s.get("forgotten") or []) if isinstance(s.get("forgotten"), list) else []:
        if isinstance(f, dict) and isinstance(f.get("full_name"), str) and f["full_name"]:
            fid = f.get("id")
            fg.append({"full_name": f["full_name"].lower()[:140],
                       "id": fid if isinstance(fid, int) and not isinstance(fid, bool) else None,
                       "at": f.get("at") if isinstance(f.get("at"), (int, float)) else None})
    out["forgotten"] = fg[-GH_FORGOTTEN_MAX:]
    return out


def _gh_state():
    """A normalised COPY of state.json (mutating it changes nothing)."""
    with _GH_LOCK:
        return _gh_norm_state(_gh_read_json(_gh_p("state.json")))


def _gh_state_update(fn):
    """Read-modify-write state.json under the module lock. `fn(state)`
    mutates in place (or returns a replacement). Nothing slow inside."""
    with _GH_LOCK:
        s = _gh_norm_state(_gh_read_json(_gh_p("state.json")))
        r = fn(s)
        if isinstance(r, dict):
            s = r
        _gh_atomic_write(_gh_p("state.json"), _gh_norm_state(s))
        return s


def _gh_key(full):
    return str(full or "").lower()


def _gh_repo(full_name):
    """A copy of one repository's row (or None)."""
    return (_gh_state()["repos"].get(_gh_key(full_name)) or None)


def _gh_row_active(row):
    """A row Hermes still uses: listed or enabled, and not removed by the owner
    (E07/E53). Only these are re-probed, checked and counted toward the limit."""
    return bool(isinstance(row, dict) and not row.get("removed")
                and (row.get("enabled") or row.get("listed")))


def _gh_known(state):
    """{"names", "ids"} of every repository the owner connected at some point
    (rows, removed rows, forgotten ones, the ids a moved row points at): the
    reach check does not call these "unlisted"."""
    names, ids = set(), set()
    for k, row in (state.get("repos") or {}).items():
        names.add(_gh_key(k))
        for v in (row.get("id"), (row.get("moved") or {}).get("to_id")
                  if isinstance(row.get("moved"), dict) else None):
            if isinstance(v, int) and not isinstance(v, bool):
                ids.add(v)
    for f in state.get("forgotten") or []:
        names.add(_gh_key(f.get("full_name")))
        if isinstance(f.get("id"), int):
            ids.add(f["id"])
    return {"names": names, "ids": ids}


def _gh_moved_reason(row):
    mv = row.get("moved") if isinstance(row.get("moved"), dict) else {}
    to = mv.get("to")
    return ("%s was renamed or transferred on GitHub%s: remove it and add it under its new "
            "name. Hermes will not write to it until then." % (
                row.get("full_name") or "This repository",
                " (it is now %s)" % to if isinstance(to, str) and to else ""))


def _gh_repo_refs(full_name):
    """What still refers to a repository row: pending changes and a prepared
    sandbox. Read OUTSIDE the store lock (it may call the pipeline)."""
    refs = []
    fn = globals().get("_ghc_pending")
    try:
        pend = fn() if callable(fn) else []
        if any(isinstance(x, dict) and _gh_key(x.get("repo")) == _gh_key(full_name)
               for x in (pend if isinstance(pend, list) else [])):
            refs.append("pending")
    except Exception:
        refs.append("pending")                 # cannot tell: keep the row
    sb = _gh_sandbox_path(full_name)
    if sb and os.path.isdir(os.path.join(sb, ".git")):
        refs.append("sandbox")
    return refs


def _gh_drop_row(s, k, refs, now):
    """Inside a state mutation: forget row `k` (E07/E53). Deleted when nothing
    refers to it, else kept switched off and marked removed. Its name and id
    stay known to the reach check until the next connect."""
    row = s["repos"].get(k)
    if row is None:
        return
    if refs:
        row["enabled"] = False
        row["listed"] = False
        row["removed"] = True
        row["removed_at"] = now
        return
    del s["repos"][k]
    s["forgotten"] = [f for f in s.get("forgotten") or [] if f.get("full_name") != k] + [
        {"full_name": k, "id": row.get("id") if isinstance(row.get("id"), int) else None,
         "at": now}]


_GH_REPO_UPDATABLE = ("last_fetch", "prepared_at", "fetch_error", "mirror_ok", "head_sha")


def _gh_repo_update(full_name, **fields):
    """For the change pipeline: record fetch/prepare facts on a KNOWN row.
    Whitelisted keys only — nothing here can enable a repository or widen
    anything."""
    bad = [k for k in fields if k not in _GH_REPO_UPDATABLE]
    if bad:
        raise ValueError("not updatable: %s" % ", ".join(sorted(bad)))

    def mut(s):
        row = s["repos"].get(_gh_key(full_name))
        if row is not None:
            row.update(fields)
            if "last_fetch" in fields and isinstance(fields["last_fetch"], (int, float)):
                s["last_read"] = max(s.get("last_read") or 0, float(fields["last_fetch"]))
    _gh_state_update(mut)


def _gh_repo_visibility_fresh(full_name):
    """E55: ONE repository's visibility read from GitHub now (a single GET
    /repos/{o}/{r} with the stored key), for the change pipeline's decide
    path, so the public-repository confirmation never rests on a flag up to
    six hours old. Never takes _GH_NET_LOCK (decide already holds it), never
    follows a redirect, never raises; updates the stored row when the answer
    changed. -> {ok, private: bool|None, changed, moved, error}. ok False =
    could not tell: the caller must fail closed (treat as stale/public)."""
    out = {"ok": False, "private": None, "changed": False, "moved": False, "error": None}
    full = _gh_mod.normalize_repo(full_name)
    if not full:
        out["error"] = "bad repository name"
        return out
    c = _gh_cred()
    if not c:
        out["error"] = "not connected"
        return out
    try:
        api = _gh_api(c["token"])
        meta, r = _gh_mod._repo_meta(api, full)
        _GH_NET["last_out"] = max(_GH_NET["last_out"], api.last_out or 0)
    except Exception as e:
        out["error"] = "internal: " + type(e).__name__
        return out
    if meta is None:
        out["moved"] = _gh_mod.is_moved(r)
        out["error"] = ("renamed or transferred on GitHub" if out["moved"] else
                        "GitHub did not answer (status %s)" % r.status)
        return out
    priv = bool(meta["private"])
    prev = (_gh_repo(full) or {}).get("private")
    out.update({"ok": True, "private": priv, "changed": prev is not None and prev != priv})
    if prev is not None and prev != priv:
        def mut(s):
            row = s["repos"].get(_gh_key(full))
            if row is not None:
                row["private"] = priv
        _gh_state_update(mut)
    return out


def _gh_sandbox_root():
    return GH_SANDBOX_ROOT


def _gh_sandbox_path(full_name):
    r = _gh_mod.normalize_repo(full_name)
    if not r:
        return None
    o, n = r.split("/", 1)
    return os.path.join(GH_SANDBOX_ROOT, o, n)


def _gh_connected():
    return _gh_cred() is not None


def _gh_holds(state=None, now=None):
    """Active system holds (each pauses writes): stored ones + expiry, which
    is computed from the credential so it never waits for the 6 h thread."""
    now = _gh_now() if now is None else now
    s = state or _gh_state()
    holds = dict(s.get("holds") or {})
    c = _gh_cred()
    exp = (c or {}).get("expires_at")
    if c and isinstance(exp, (int, float)) and exp <= now and "expired" not in holds:
        holds["expired"] = {"text": "Your GitHub key has expired. GitHub writes are paused "
                                    "until you connect a new one.", "since": exp}
    return holds


def _gh_cause(fid, line, repo=None, repos=None):
    c = {"kind": str(fid)[:40], "line": str(line or fid)[:400]}
    if repo:
        c["repo"] = str(repo)[:140]
    if repos:
        c["repos"] = [str(x).lower()[:140] for x in repos][:10]
    return c


def _gh_cause_gone(c, obs):
    """E51: True only when THIS check re-observed the cause as gone. A cause no
    GET can observe (unlisted_write: write reach needs a POST) never is: only
    a reconnect or a passing add (the full wall) clears it."""
    kind = c.get("kind") if isinstance(c, dict) else None
    if kind == "unlisted_private":
        return bool(obs.get("reach_ok")) and not (
            set(c.get("repos") or []) & set(obs.get("extras") or ()))
    if kind == "too_many":
        return bool(obs.get("reach_ok")) and not obs.get("too_many")
    if kind in GH_REPO_PERMISSION_CAUSES:
        seen = (obs.get("repo") or {}).get(kind) or []
        return "absent" in seen and "present" not in seen
    if kind in [p[0] for p in _gh_mod.ACCOUNT_PROBES]:
        return (obs.get("acct") or {}).get(kind) == "absent"
    return False


def _gh_set_permission_hold(s, causes, now):
    """Merge `causes` into the permission hold (dedupe; `since` kept)."""
    prev = s["holds"].get("permission") or {}
    old = prev.get("causes") if isinstance(prev.get("causes"), list) else []
    merged, seen = [], set()
    for c in list(old) + list(causes):
        if not isinstance(c, dict):
            continue
        key = (c.get("kind"), c.get("repo") or "", c.get("line"))
        if key not in seen:
            seen.add(key)
            merged.append(c)
    if not merged:
        s["holds"].pop("permission", None)
        return
    s["holds"]["permission"] = {"text": " ".join(c["line"] for c in merged)[:600],
                                "since": prev.get("since") or now, "causes": merged[:20]}


# holds another module may set through _gh_hold(): none of them can be tested
# by the GET-only check, so only a reconnect (connect's full wall) clears them
GH_EXTERNAL_HOLDS = ("mirror_tamper",)


def _gh_hold(hold_id, text):
    """Store a hold that pauses GitHub writes (aux_ghchanges' tamper hook calls
    this by name for `mirror_tamper`). Only ids in GH_EXTERNAL_HOLDS; the
    first `since` is kept; the cause is recorded like the permission hold's.
    -> True when stored. Never raises."""
    if hold_id not in GH_EXTERNAL_HOLDS or not isinstance(text, str) or not text.strip():
        return False
    try:
        now = _gh_now()
        line = text.strip()[:600]

        def mut(s):
            prev = s["holds"].get(hold_id) or {}
            s["holds"][hold_id] = {"text": line, "since": prev.get("since") or now,
                                   "causes": [_gh_cause(hold_id, line)]}
        _gh_state_update(mut)
        _gh_log("writes held (%s)" % hold_id)
        return True
    except Exception as e:
        _gh_log("hold %s not stored: %s" % (hold_id, type(e).__name__))
        return False


def _gh_paused(state=None):
    """True when GitHub writes must not happen: the owner's kill switch OR
    any system hold (expired, unauthorized, forbidden permission)."""
    s = state or _gh_state()
    return bool(s["settings"].get("writes_paused")) or bool(_gh_holds(s))


def _gh_writes_allowed(full_name, state=None):
    """(ok, reason) — the connector half of the change pipeline's C-scope
    check. Trust tier (permissions.decide) is the pipeline's own half."""
    s = state or _gh_state()
    if not _gh_connected():
        return False, "GitHub is not connected."
    if s["settings"].get("writes_paused"):
        return False, "GitHub writes are paused."
    holds = _gh_holds(s)
    if holds:
        return False, "GitHub writes are paused: " + "; ".join(
            str(h.get("text") or k) for k, h in sorted(holds.items()))
    row = s["repos"].get(_gh_key(full_name))
    if not row or row.get("removed"):
        return False, "%s is not connected to Hermes." % _gh_mod.short(full_name, 120)
    if isinstance(row.get("moved"), dict):
        return False, _gh_moved_reason(row)
    if not row.get("enabled"):
        return False, "%s is not enabled for Hermes." % row["full_name"]
    if not row.get("writable"):
        return False, row.get("enable_blocked") or ("Hermes cannot write to %s."
                                                    % row["full_name"])
    if row.get("enable_blocked"):
        return False, row["enable_blocked"]
    ci = row.get("ci")
    if not isinstance(ci, dict):
        return False, ("Hermes could not read %s's workflows, so it cannot tell whether a "
                       "hermes/ branch would run CI with your secrets." % row["full_name"])
    if ci.get("exposed"):
        return False, _gh_ci_block_text(row)
    return True, ""


def _gh_ci_block_text(row):
    ci = row.get("ci") or {}
    paths = [w["path"] for w in (ci.get("workflows") or []) if w.get("push_exposed")]
    names = []
    for w in ci.get("workflows") or []:
        if w.get("push_exposed"):
            for n in w.get("secrets") or []:
                if n not in names:
                    names.append(n)
    return ("Pushing a hermes/ branch to %s would run %s with secrets (%s) on GitHub, so "
            "Hermes will not push there. %s" % (
                row["full_name"], ", ".join(paths[:4]) or "a workflow",
                ", ".join(names[:6]) or "secrets",
                _gh_mod.CI_FIX.format(paths=", ".join(paths[:4]) or "that workflow")))


# --------------------------------------------------------------------------
# ledger (append-only; the change pipeline writes, the drift detector reads)
# --------------------------------------------------------------------------
_GH_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_GH_BRANCH_RE = re.compile(r"^hermes/[a-z0-9][a-z0-9-]{1,48}$")


def _gh_ledger_append(row):
    """Append one ledger row. row = {event: push|pr_open|pr_close|ref_delete,
    repo: "o/r", branch: "hermes/<slug>", sha?: 40-hex, change_id?, pr_number?,
    pr_url?}. Validated; `ts` is added. Returns the stored row. Raises
    ValueError on a malformed row (the caller must not push unrecorded)."""
    if not isinstance(row, dict) or row.get("event") not in GH_LEDGER_EVENTS:
        raise ValueError("ledger: bad event")
    repo = _gh_mod.normalize_repo(row.get("repo"))
    br = str(row.get("branch") or "")
    if not repo or not _GH_BRANCH_RE.fullmatch(br):
        raise ValueError("ledger: bad repo/branch")
    try:
        ts = float(row.get("ts") or _gh_now())
    except (TypeError, ValueError):
        raise ValueError("ledger: bad ts")
    out = {"ts": ts, "event": row["event"], "repo": repo, "branch": br}
    sha = row.get("sha")
    if row["event"] == "push" and not (isinstance(sha, str) and _GH_SHA_RE.fullmatch(sha)):
        raise ValueError("ledger: a push needs its 40-hex sha")
    if isinstance(sha, str) and _GH_SHA_RE.fullmatch(sha):
        out["sha"] = sha
    for k, lim in (("change_id", 40), ("pr_url", 300)):
        if isinstance(row.get(k), str):
            out[k] = row[k][:lim]
    if isinstance(row.get("pr_number"), int) and not isinstance(row.get("pr_number"), bool):
        out["pr_number"] = row["pr_number"]
    line = (json.dumps(out, sort_keys=True) + "\n").encode("utf-8")
    with _GH_LEDGER_LOCK:
        _gh_ensure_dir()
        fd = os.open(_gh_p("ledger.jsonl"),
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                     0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
    return out


def _gh_ledger_rows(limit=None):
    rows = []
    try:
        with open(_gh_p("ledger.jsonl"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if isinstance(r, dict) and r.get("event") in GH_LEDGER_EVENTS:
                    rows.append(r)
    except OSError:
        return []
    return rows[-limit:] if limit else rows


def _gh_ledger_heads(full_name, rows=None):
    """{branch: sha | None} — what Hermes last left on each hermes/* branch
    of one repository (None = Hermes deleted it)."""
    key = _gh_key(full_name)
    out = {}
    for r in (rows if rows is not None else _gh_ledger_rows()):
        if _gh_key(r.get("repo")) != key:
            continue
        if r.get("event") == "push" and r.get("sha"):
            out[r["branch"]] = r["sha"]
        elif r.get("event") == "ref_delete":
            out[r["branch"]] = None
    return out


def _gh_push_budget(now=None, state=None):
    """{limit, used, left} — approved pushes in the last 24 h (C-rate)."""
    now = _gh_now() if now is None else now
    s = state or _gh_state()
    lim = int(s["settings"].get("max_pushes_day", GH_MAX_PUSHES_DAY))
    # a '<id>:undo' push row is the compensation for a push that provably did
    # not land (aux_ghchanges): it is not a push of its own
    used = sum(1 for r in _gh_ledger_rows() if r.get("event") == "push"
               and not str(r.get("change_id") or "").endswith(":undo")
               and isinstance(r.get("ts"), (int, float)) and now - r["ts"] < 86400)
    return {"limit": lim, "used": used, "left": max(0, lim - used)}


def _gh_last_read_ts():
    """Newest moment Hermes READ from GitHub (fetch, check, connect) — the
    Data & Network card's github_repos evidence. File reads only."""
    s = _gh_state()
    vals = [s.get("last_read"), s.get("last_check"), s.get("last_revoke"),
            _GH_NET.get("last_out")]
    for row in s["repos"].values():
        for k in ("last_fetch", "last_check"):
            if isinstance(row.get(k), (int, float)):
                vals.append(row[k])
    vals = [float(v) for v in vals if isinstance(v, (int, float)) and v > 0]
    return max(vals) if vals else None


def _gh_last_push_ts():
    """Newest ledger push/PR (the github_push evidence)."""
    ts = [r["ts"] for r in _gh_ledger_rows() if r.get("event") in ("push", "pr_open")
          and not str(r.get("change_id") or "").endswith(":undo")
          and isinstance(r.get("ts"), (int, float))]
    return max(ts) if ts else None


# --------------------------------------------------------------------------
# side channels: recorder + Telegram (both resolved by name)
# --------------------------------------------------------------------------
def _gh_record(event, target, summary, args=None, status="done"):
    fn = globals().get("recorder_record_local")
    if not callable(fn):
        return False
    try:
        r = fn(tool="github_" + event, target=str(target or "")[:200], kind="other",
               reversible="no", source="dashboard", args=args or {}, status=status,
               summary=_gh_scrub(summary)[:300],
               tool_call_id="gh-%s-%d" % (event, int(_gh_now() * 1000)))
        return bool(isinstance(r, dict) and r.get("ok"))
    except Exception:
        return False


def _gh_notify(text):
    """A Telegram notice through the ONE sender (_wt_send_telegram, stdin,
    never argv). Security notices send in quiet hours too (D8). Scrubbed.
    -> (ok, detail)."""
    # deliberately NOT gated on the watchtower masters (_wt_push_allowed):
    # see GH_SECURITY_NOTICES_IGNORE_MASTERS (E77)
    assert GH_SECURITY_NOTICES_IGNORE_MASTERS
    fn = globals().get("_wt_send_telegram")
    if not callable(fn):
        return False, "telegram sender unavailable"
    try:
        ok, det = fn(_gh_mod.short(text, 1500, _gh_token_quiet() or ""))
        return bool(ok), _gh_scrub(det)
    except Exception as e:
        return False, type(e).__name__


def _gh_push_notice_text(repo, branch, files, pr_number=None, draft=True):
    """The Telegram line after every push (design §3.5 step 10). The change
    pipeline sends it with _gh_notify(); it is a security notice, so it goes
    out in quiet hours too, and it never carries a secret."""
    n = int(files) if isinstance(files, int) and not isinstance(files, bool) else 0
    what = "%d file%s" % (n, "" if n == 1 else "s")
    pr = ""
    if isinstance(pr_number, int) and not isinstance(pr_number, bool):
        pr = " and opened %sPR #%d" % ("draft " if draft else "", pr_number)
    return ("Hermes pushed %s to %s on %s%s. If you did not approve this, pause GitHub "
            "writes in Settings › Connections." % (
                what, _gh_line(repo, 140), _gh_line(branch, 80), pr))


def _gh_pending_count():
    fn = globals().get("_ghc_pending")
    if not callable(fn):
        return 0
    try:
        v = fn()
        if isinstance(v, list):
            return len(v)
        return int(v or 0)
    except Exception:
        return 0


def _gh_pending_ids():
    """Ids of the changes waiting for the owner (newest first, at most 50):
    the UI's toast poll seeds and diffs on them without a second request."""
    fn = globals().get("_ghc_pending")
    try:
        v = fn() if callable(fn) else []
        return [x["id"] for x in v if isinstance(x, dict) and isinstance(x.get("id"), str)][:50] \
            if isinstance(v, list) else []
    except Exception:
        return []


def _gh_cancel_pending(reason):
    fn = globals().get("_ghc_cancel_all")
    if not callable(fn):
        return 0
    try:
        return int(fn(reason) or 0)
    except Exception:
        return 0


# --------------------------------------------------------------------------
# F05 hooks: owner-only marking
# --------------------------------------------------------------------------
def _gh_owner_refused(ctx):
    """True when F05's nonce check has run AND failed. None (pre-F05) passes."""
    return getattr(ctx, "nonce_ok", None) is False


_GH_OWNER_403 = ({"ok": False, "error": "owner_only",
                  "hint": "Only the owner can do this, from the dashboard window."}, 403)


def _gh_settings_widen(body, state=None):
    """True when a /settings body would WIDEN what Hermes may do."""
    b = body if isinstance(body, dict) else {}
    s = (state or _gh_state())["settings"]
    if b.get("checks_enabled") is False and s.get("checks_enabled"):
        return True
    if "max_pushes_day" in b:
        try:
            if int(b["max_pushes_day"]) > int(s.get("max_pushes_day", GH_MAX_PUSHES_DAY)):
                return True
        except (TypeError, ValueError):
            return True                        # unparseable: treat as widening
    return False


def _gh_route_gated(method, path, body=None):
    """The F05 predicate (§5.4): True = this request must carry the owner's
    proof. Unknown shapes fail CLOSED (gated)."""
    if str(method or "").upper() != "POST":
        return False
    b = body if isinstance(body, dict) else {}
    if path in ("/api/github/connect", "/api/github/check", "/api/github/drift/ack",
                "/api/github/changes/decide", "/api/github/changes/retract"):
        return True
    if path == "/api/github/repos":
        return b.get("op") not in ("remove", "disable")
    if path == "/api/github/pause":
        return b.get("paused") is not True
    if path == "/api/github/settings":
        return _gh_settings_widen(b)
    return False


# {path: predicate(method, path, body) -> True when the owner's proof is
# required}. F05's server-side gate consults it. This module's own paths are
# set; the change pipeline's (aux_ghchanges sorts BEFORE this file) are only
# defaulted, so its own, finer predicate wins when it registered one.
_gh_gates = globals().setdefault("ROUTE_OWNER_GATES", {})
for _gh_gp in ("/api/github/connect", "/api/github/check", "/api/github/drift/ack",
               "/api/github/repos", "/api/github/pause", "/api/github/settings"):
    _gh_gates[_gh_gp] = _gh_route_gated
for _gh_gp in ("/api/github/changes/decide", "/api/github/changes/retract"):
    _gh_gates.setdefault(_gh_gp, _gh_route_gated)


def _gh_safe(fn):
    """Never 500 with a raw exception string (it could carry a token)."""
    def wrapped(ctx):
        try:
            return fn(ctx)
        except Exception as e:
            _gh_log("%s failed: %s" % (getattr(fn, "__name__", "route"), type(e).__name__))
            return ({"ok": False, "error": "internal: " + type(e).__name__}, 500)
    wrapped.__name__ = getattr(fn, "__name__", "wrapped")
    return wrapped


def _gh_body(ctx):
    return ctx.body if isinstance(getattr(ctx, "body", None), dict) else {}


# --------------------------------------------------------------------------
# ambient GitHub access (§2.4) — DETECTED, never changed. Reads names,
# attributes and fingerprints only; never a secret value.
# --------------------------------------------------------------------------
def _gh_which(name):
    return shutil.which(name, path=GH_AMBIENT_PATH)


def _gh_git_bin():
    """A git that will not pop the Command Line Tools installer: /usr/bin/git
    is only a shim until CLT or Xcode is installed."""
    g = _gh_which("git")
    if not g:
        return None
    if os.path.realpath(g) == "/usr/bin/git" and not (
            os.path.exists("/Library/Developer/CommandLineTools/usr/bin/git") or
            os.path.exists("/Applications/Xcode.app/Contents/Developer/usr/bin/git")):
        return None
    return g


def _gh_run_ro(argv, timeout=3, keep_out=True):
    """Run one read-only probe. Minimal env (PATH from GH_AMBIENT_PATH, HOME,
    SSH_AUTH_SOCK only), no stdin, cwd "/" (never inside a repository).
    -> (rc, stdout or "") — rc None when it could not run."""
    env = {"PATH": GH_AMBIENT_PATH, "HOME": HOME, "LANG": "C",     # noqa: F821
           "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/usr/bin/false"}
    if os.environ.get("SSH_AUTH_SOCK"):
        env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, errors="replace", timeout=timeout, env=env, cwd="/")
    except Exception:
        return None, ""
    return p.returncode, (p.stdout if keep_out else "")


def _gh_gh_config_dirs():
    out = []
    for k in ("GH_CONFIG_DIR",):
        if os.environ.get(k):
            out.append(os.environ[k])
    if os.environ.get("XDG_CONFIG_HOME"):
        out.append(os.path.join(os.environ["XDG_CONFIG_HOME"], "gh"))
    out.append(os.path.join(HOME, ".config", "gh"))                 # noqa: F821
    seen, res = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            res.append(d)
    return res


def _gh_ambient_gh():
    item = {"id": "gh", "present": False, "title": "GitHub CLI", "detail": "",
            "plaintext": False, "commands": []}
    for d in _gh_gh_config_dirs():
        path = os.path.join(d, "hosts.yml")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read(65536).splitlines()
        except OSError:
            continue
        host = False
        in_gh = False
        for ln in lines:
            if re.match(r"^github\.com\s*:\s*$", ln):
                host, in_gh = True, True
                continue
            if ln and not ln[0].isspace():
                in_gh = False
            # the KEY's presence only — its value is never read into anything
            if in_gh and re.match(r"^\s+oauth_token\s*:", ln):
                item["plaintext"] = True
        if host:
            item["present"] = True
            break
    if item["present"]:
        item["detail"] = ("The GitHub CLI is signed in. Any program you run, including "
                          "the agent's terminal, can get its token with `gh auth token`.")
        if item["plaintext"]:
            item["detail"] += " Its token is stored in plain text in hosts.yml."
        item["commands"] = [
            {"label": "See what it is signed in as", "cmd": "gh auth status"},
            {"label": "Sign it out", "cmd": "gh auth logout --hostname github.com"},
            {"label": "Or sign in again with a narrower token",
             "cmd": "gh auth login --hostname github.com --with-token < token.txt"}]
    return item


def _gh_helper_label(val):
    """A credential.helper VALUE reduced to what it is, never what it says. A
    helper line can be a shell snippet (`!f() { echo password=... }`) or carry
    arguments with a path or a secret in them; this status is readable by the
    agent, and the detector's law is names and attributes only. -> '' (the
    reset), 'osxkeychain', 'store', 'cache', 'manager', 'gh auth git-credential'
    or '(custom helper)'."""
    v = str(val or "").strip()
    if not v:
        return ""
    if re.search(r"(^|[!/ ])gh\s+auth\s+git-credential", v):
        return "gh auth git-credential"
    base = os.path.basename(v.split()[0].lstrip("!"))
    base = base[len("git-credential-"):] if base.startswith("git-credential-") else base
    if base in ("osxkeychain", "store", "cache", "manager", "manager-core", "libsecret"):
        return base
    return "(custom helper)"


def _gh_ambient_keychain():
    item = {"id": "keychain", "present": False, "title": "git saved password",
            "detail": "", "helpers": [], "saved": None, "store_file": False,
            "commands": []}
    git = _gh_git_bin()
    helpers = []
    if git:
        rc, out = _gh_run_ro([git, "config", "--show-origin", "--get-regexp",
                              r"^credential\..*helper$"])
        if rc == 0:
            for ln in out.splitlines()[:20]:
                origin, _, rest = ln.partition("\t")
                key, _, val = rest.partition(" ")
                helpers.append({"origin": _gh_mod.short(origin, 160),
                                "key": _gh_mod.short(key, 120),
                                "value": _gh_helper_label(val)})
    item["helpers"] = helpers
    # an empty value resets the list: only helpers AFTER the last reset count
    active = []
    for h in helpers:
        if h["value"] == "":
            active = []
        else:
            active.append(h)
    uses_kc = any(h["value"] == "osxkeychain" for h in active)
    uses_store = any(h["value"] == "store" for h in active)
    # `gh auth setup-git` makes git ask the GitHub CLI for github.com
    uses_gh = any(h["value"] == "gh auth git-credential" for h in active)
    item["gh_helper"] = uses_gh
    sec = _gh_which("security")
    if sec and uses_kc:
        # attributes only: NO -g, NO -w — the password is never requested
        rc, _ = _gh_run_ro([sec, "find-internet-password", "-s", "github.com"],
                           keep_out=False)
        item["saved"] = (rc == 0) if rc is not None else None
    store = os.path.join(HOME, ".git-credentials")                  # noqa: F821
    item["store_file"] = os.path.isfile(store)
    item["present"] = bool((uses_kc and item["saved"]) or (uses_store and item["store_file"])
                           or uses_gh)
    if item["present"]:
        bits = []
        if uses_gh:
            bits.append("git is set to borrow the GitHub CLI's login for github.com (gh auth "
                        "setup-git), so any `git push` there works without asking while gh "
                        "is signed in.")
        if uses_kc and item["saved"]:
            bits.append("git has a saved github.com password in your Keychain, and git "
                        "hands it to any `git push` without asking.")
        if uses_store and item["store_file"]:
            bits.append("git keeps a github.com password in plain text in "
                        "~/.git-credentials.")
        item["detail"] = " ".join(bits)
        item["commands"] = [
            {"label": "See where the helper is set",
             "cmd": "git config --show-origin --get-all credential.helper"},
            {"label": "Remove the saved github.com password",
             "cmd": "printf 'protocol=https\\nhost=github.com\\n\\n' | git credential reject"},
            {"label": "Stop git saving new ones (an empty helper resets the list)",
             "cmd": "git config --global credential.helper \"\""},
            {"label": "Or delete it by hand",
             "cmd": "open -a 'Keychain Access'   # search github.com, delete the item"}]
        if uses_gh:
            item["commands"].insert(1, {
                "label": "Stop git borrowing the GitHub CLI's login",
                "cmd": "git config --global --unset-all credential.https://github.com.helper"})
    elif uses_kc and item["saved"] is None:
        item["detail"] = ("git is set to use the macOS Keychain helper; Hermes could not "
                          "check whether a github.com password is saved.")
        item["present"] = None
    return item


def _gh_ambient_ssh():
    item = {"id": "ssh", "present": False, "title": "SSH key", "detail": "",
            "agent": "unknown", "keys": 0, "key_files": [], "github_host": False,
            "use_keychain": False, "commands": []}
    sa = _gh_which("ssh-add")
    if sa and os.environ.get("SSH_AUTH_SOCK"):
        rc, out = _gh_run_ro([sa, "-l"])
        if rc == 0:
            item["agent"] = "keys"
            item["keys"] = len([ln for ln in out.splitlines() if ln.strip()])
        elif rc == 1:
            item["agent"] = "empty"
        elif rc == 2:
            item["agent"] = "unreachable"
    sshd = os.path.join(HOME, ".ssh")                               # noqa: F821
    try:
        names = sorted(os.listdir(sshd))
    except OSError:
        names = []
    item["key_files"] = [n for n in names if n.startswith("id_") and not n.endswith(".pub")
                         and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", n)][:12]
    try:
        with open(os.path.join(sshd, "config"), encoding="utf-8", errors="replace") as f:
            cfg = f.read(65536)
    except OSError:
        cfg = ""
    for ln in cfg.splitlines():
        s = ln.strip()
        m = re.match(r"(?i)^host\s+(.+)$", s)
        if m and any(h.lower() in ("github.com", "*.github.com") for h in m.group(1).split()):
            item["github_host"] = True
        if re.match(r"(?i)^usekeychain\s+yes\b", s):
            item["use_keychain"] = True
    item["present"] = item["agent"] == "keys" or bool(item["github_host"] and item["key_files"])
    if item["present"]:
        item["detail"] = ("An SSH key is %s, so `git push git@github.com:…` works without "
                          "asking." % ("loaded" if item["agent"] == "keys" else "set up for "
                                                                            "github.com"))
        if item["use_keychain"]:
            item["detail"] += (" ~/.ssh/config has UseKeychain yes, so its passphrase is "
                               "filled in from the Keychain.")
        item["commands"] = [
            {"label": "See the loaded keys", "cmd": "ssh-add -l"},
            {"label": "Unload every key from the agent (until the next login)",
             "cmd": "ssh-add -D"},
            {"label": "Give a key a passphrase",
             "cmd": "ssh-keygen -p -f ~/.ssh/%s" % (item["key_files"][0]
                                                    if item["key_files"] else "id_ed25519")}]
        if item["use_keychain"]:
            item["commands"].append({"label": "Stop the Keychain filling in the passphrase",
                                     "cmd": "remove the `UseKeychain yes` line from "
                                            "~/.ssh/config"})
    return item


def _gh_ambient_env():
    names = [k for k in GH_ENV_TOKEN_NAMES if (os.environ.get(k) or "").strip()]
    item = {"id": "env", "present": bool(names), "title": "Token in the environment",
            "names": names, "detail": "", "commands": []}
    if names:
        item["detail"] = ("A GitHub token is set in the environment (%s)." % ", ".join(names))
        item["commands"] = [
            {"label": "Find where it is set",
             "cmd": "grep -n 'GITHUB_TOKEN\\|GH_TOKEN' ~/.zshrc ~/.zprofile ~/.bash_profile "
                    "~/Library/LaunchAgents/com.hermes.*.plist 2>/dev/null"},
            {"label": "Then remove that line and restart the dashboard",
             "cmd": "launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard"}]
    return item


def _gh_ambient(fresh=False):
    """The read-only ambient-credential finding (cached GH_AMBIENT_TTL s).
    Nothing is ever changed; the owner gets exact commands."""
    now = _gh_now()
    if not fresh and _GH_AMBIENT["val"] and now - _GH_AMBIENT["at"] < GH_AMBIENT_TTL:
        return _GH_AMBIENT["val"]
    items = []
    for fn in (_gh_ambient_gh, _gh_ambient_keychain, _gh_ambient_ssh, _gh_ambient_env):
        try:
            items.append(fn())
        except Exception as e:
            items.append({"id": fn.__name__.replace("_gh_ambient_", ""), "present": None,
                          "detail": "check failed: " + type(e).__name__, "commands": []})
    by = {i["id"]: i for i in items}
    val = {"checked_at": now, "items": items,
           "gh": by.get("gh", {}).get("present"),
           "keychain": by.get("keychain", {}).get("present"),
           "ssh": by.get("ssh", {}).get("present"),
           "env": by.get("env", {}).get("present"),
           "any": any(i.get("present") for i in items),
           "accept_text": ("Or accept it knowingly: this access is broader than the key "
                           "you connect here, and Hermes cannot see or limit it."),
           "note": ("Hermes only reads names and settings here. It never reads a "
                    "password or token, and it never changes any of this for you.")}
    _GH_AMBIENT["at"], _GH_AMBIENT["val"] = now, val
    return val


# --------------------------------------------------------------------------
# applying a wall result to the store
# --------------------------------------------------------------------------
def _gh_ci_scan(api, rows):
    """CI exposure per writable repository (network, OUTSIDE the lock).
    -> {full_lower: ci | None}. None = could not read = blocked (fail closed)."""
    out = {}
    for row in rows:
        if not row.get("writable") or not row.get("default_branch"):
            continue
        files, err = _gh_mod.fetch_workflows(api, row["full_name"], row["default_branch"])
        if files is None:
            out[_gh_key(row["full_name"])] = None
        else:
            ci = _gh_mod.ci_exposure(files)
            ci["checked_at"] = _gh_now()
            out[_gh_key(row["full_name"])] = ci
    return out


def _gh_protection_scan(api, rows):
    """{full_lower: protection_summary} for every reachable row (reads)."""
    out = {}
    for row in rows:
        if row.get("default_branch") and row.get("private") is not None:
            out[_gh_key(row["full_name"])] = _gh_mod.repo_protection(
                api, row["full_name"], row["default_branch"])
    return out


def _gh_row_from_probe(prow, prev, ci, ci_known, enable):
    row = dict(prev or {})
    for k in ("full_name", "private", "default_branch", "size_kb", "archived", "writable",
              "pulls", "checks", "accepted", "listed"):
        if k in prow:
            row[k] = prow[k]
    if isinstance(prow.get("id"), int):
        row["id"] = prow["id"]
    if isinstance(prow.get("moved"), dict):
        row["moved"] = dict(prow["moved"], since=_gh_now())
    else:
        row.pop("moved", None)
    row["enable_blocked"] = prow.get("enable_blocked")
    if ci_known:
        row["ci"] = ci
    if row.get("writable") and not row.get("enable_blocked"):
        if not isinstance(row.get("ci"), dict):
            row["enable_blocked"] = GH_CI_UNREADABLE
        elif row["ci"].get("exposed"):
            row["enable_blocked"] = _gh_ci_block_text(row)
    if enable is not None:
        row["enabled"] = bool(enable) and bool(row.get("writable")) and not row.get(
            "enable_blocked")
    elif row.get("enable_blocked") or not row.get("writable"):
        row["enabled"] = False
    row.setdefault("enabled", False)
    row["sandbox"] = _gh_sandbox_path(row["full_name"])
    row["last_probe"] = _gh_now()
    return row


def _gh_refusal_payload(res, status_hint=None):
    """The refusal answer: one line per finding, then the final line."""
    refuse = [f for f in res.get("findings") or [] if f.get("level") == "refuse"]
    lines = [f["line"] for f in refuse]
    out = {"ok": False, "refused": True, "retry": bool(res.get("retry")),
           "stored": False,
           "findings": [{k: v for k, v in f.items() if k in ("id", "level", "line", "repo")}
                        for f in res.get("findings") or []],
           "lines": lines, "final": res.get("final")}
    if res.get("login"):
        out["login"] = res["login"]
    return out, (503 if res.get("retry") else 422)


# --------------------------------------------------------------------------
# routes: connect / repos / settings / pause / disconnect / revoke / check
# --------------------------------------------------------------------------
@_gh_safe
def _gh_connect_handler(ctx):
    """POST /api/github/connect {token, repos?} — OWNER-ONLY. Runs the scope
    wall; stores the token ONLY when it passes. On refusal nothing is
    written anywhere and the token is forgotten."""
    if _gh_owner_refused(ctx):
        return _GH_OWNER_403
    b = _gh_body(ctx)
    tok = b.get("token")
    if not isinstance(tok, str) or not tok.strip():
        return ({"ok": False, "error": "no_token", "hint": "Paste the token first."}, 400)
    tok = tok.strip()
    if len(tok) > GH_TOKEN_MAX:
        return ({"ok": False, "error": "too_long", "hint": _gh_mod.LINES["unknown_kind"]}, 400)
    repos, bad = _gh_mod.parse_repo_list(b.get("repos"))
    if bad:
        return ({"ok": False, "error": "bad_repos", "bad": bad[:10],
                 "hint": "Write each repository as owner/repo, one per line, at most %d."
                         % GH_MAX_REPOS}, 400)
    kind = _gh_mod.classify_token(tok)
    if kind != "fine_grained_pat":           # refused before any network call
        return _gh_refusal_payload({"findings": [
            {"id": "kind", "level": "refuse", "line": _gh_mod.kind_line(kind)}],
            "final": _gh_mod.FINAL_LINE})
    if not _GH_NET_LOCK.acquire(blocking=False):
        return ({"ok": False, "error": "busy",
                 "hint": "Hermes is already checking with GitHub. Try again in a moment."}, 409)
    try:
        api = _gh_mod.GitHubAPI(GH_API_BASE, tok, ssl_context=_gh_sslctx(),
                                timeout=GH_TIMEOUT)
        res = _gh_mod.probe(api, repos)
        ci = _gh_ci_scan(api, res["repos"]) if res["ok"] else {}
        prot = _gh_protection_scan(api, res["repos"]) if res["ok"] else {}
        out_ts = api.last_out or _gh_now()
    finally:
        _GH_NET_LOCK.release()
    _GH_NET["last_out"] = max(_GH_NET["last_out"], out_ts)
    if not res["ok"]:                       # nothing is written anywhere
        _gh_log("connect refused: %s" % ",".join(
            f["id"] for f in res["findings"] if f["level"] == "refuse"))
        return _gh_refusal_payload(res)

    now = _gh_now()
    cred = {"kind": "fine_grained_pat", "token": tok, "login": res["login"],
            "user_id": res["user_id"], "created": now, "expires_at": res["expires_at"],
            "verified_at": now,
            "probe": {"checks": res["checks"], "no_expiry": res["no_expiry"]}}
    explicit = bool(repos)
    reached = {_gh_key(p["full_name"]) for p in res["repos"]}
    refs = {k: _gh_repo_refs(r.get("full_name") or k)
            for k, r in _gh_state()["repos"].items() if k not in reached}

    def mut(s):
        known = s["repos"]
        for prow in res["repos"]:
            k = _gh_key(prow["full_name"])
            row = _gh_row_from_probe(prow, known.get(k), ci.get(k), k in ci,
                                     enable=(explicit or bool(known.get(k, {}).get("enabled"))))
            if prot.get(k):
                row["protection"] = prot[k]
            row["last_check"] = now
            row.pop("removed", None)
            row.pop("removed_at", None)
            known[k] = row
        # a repo the new wall did not cover can never stay enabled; a reconnect
        # starts clean (E07): such a row is dropped, or kept removed while a
        # pending change or a prepared sandbox still refers to it
        for k in [k for k in known if k not in reached]:
            row = known[k]
            row["enable_blocked"] = "The connected key does not cover this repository."
            _gh_drop_row(s, k, refs.get(k, ["pending"]), now)
        # the new key passed the wall against the new list: nothing removed
        # earlier stays "known" to the reach check
        s["forgotten"] = []
        for h in ("expired", "unauthorized", "permission") + GH_EXTERNAL_HOLDS:
            s["holds"].pop(h, None)
        s["connected_at"] = now
        s["last_read"] = out_ts
        s["probe"] = {"at": now, "checks": res["checks"],
                      "warnings": [f["line"] for f in res["findings"]
                                   if f["level"] == "warn"]}
    with _GH_LOCK:
        _gh_atomic_write(_gh_p("credential.json"), cred)
        _gh_state_update(mut)
    _gh_record("connect", "github:@" + res["login"],
               "connected GitHub as @%s · %d repositories" % (res["login"], len(res["repos"])),
               args={"login": res["login"], "repos": [r["full_name"] for r in res["repos"]],
                     "checks": res["checks"]})
    payload = _gh_status_payload()
    payload.update({"ok": True, "stored": True, "checks_passed": res["checks"],
                    "warnings": [f["line"] for f in res["findings"] if f["level"] == "warn"],
                    "workflow_note": _gh_mod.WORKFLOW_NOTE})
    return payload


@_gh_safe
def _gh_repos_handler(ctx):
    """POST /api/github/repos {op: add|remove, repo}. add (= enable) re-runs
    the whole wall over the allowlist + the new repository and is OWNER-ONLY;
    remove (= disable) reduces capability and is not."""
    b = _gh_body(ctx)
    op = b.get("op")
    repo = _gh_mod.normalize_repo(b.get("repo"))
    if op not in ("add", "enable", "remove", "disable"):
        return ({"ok": False, "error": "bad_op", "hint": "op is add or remove"}, 400)
    if not repo:
        return ({"ok": False, "error": "bad_repo", "hint": "Write it as owner/repo."}, 400)
    if op in ("remove", "disable"):
        # reduces capability: no owner proof. Switch it off FIRST, cancel its
        # pending changes, then drop the row unless something still refers to
        # it (E07/E53): a removed repository frees its slot and is never
        # probed or checked again
        def off(s):
            row = s["repos"].get(_gh_key(repo))
            if row:
                row["enabled"] = False
        _gh_state_update(off)
        _gh_cancel_pending_repo(repo)
        refs = _gh_repo_refs(repo)
        now = _gh_now()
        _gh_state_update(lambda s: _gh_drop_row(s, _gh_key(repo), refs, now))
        deleted, left = _gh_delete_mirrors(repo)
        _gh_record("repo_remove", repo, "removed %s from Hermes%s" % (
            repo, "; its mirror was deleted" if deleted else ""))
        p = _gh_status_payload()
        sb = _gh_sandbox_path(repo)
        p.update({"mirrors_deleted": deleted, "mirrors_left": left,
                  "sandboxes_kept": [sb] if sb and os.path.isdir(sb) else [],
                  "cleanup_note": GH_CLEANUP_NOTE})
        return p
    if _gh_owner_refused(ctx):
        return _GH_OWNER_403
    if not _gh_connected():
        return ({"ok": False, "error": "not_connected"}, 400)
    st = _gh_state()
    # only the rows Hermes still uses count and are re-probed (E53); a row
    # renamed on GitHub (moved) cannot be probed under its old name (E07)
    listed = [r["full_name"] for k, r in st["repos"].items()
              if _gh_row_active(r) and not isinstance(r.get("moved"), dict)
              and k != _gh_key(repo)]
    listed.append(repo)
    if len(listed) > GH_MAX_REPOS:
        return ({"ok": False, "error": "too_many",
                 "hint": "Hermes works on at most %d repositories." % GH_MAX_REPOS}, 400)
    if not _GH_NET_LOCK.acquire(blocking=False):
        return ({"ok": False, "error": "busy"}, 409)
    try:
        api = _gh_api()
        res = _gh_mod.probe(api, listed, known=_gh_known(st))
        new_rows = [r for r in res["repos"] if _gh_key(r["full_name"]) == _gh_key(repo)]
        ci = _gh_ci_scan(api, new_rows) if res["ok"] else {}
        prot = _gh_protection_scan(api, new_rows) if res["ok"] else {}
        out_ts = api.last_out or _gh_now()
    finally:
        _GH_NET_LOCK.release()
    _GH_NET["last_out"] = max(_GH_NET["last_out"], out_ts)
    if not res["ok"] and not res["retry"]:
        refuse = [f for f in res["findings"] if f["level"] == "refuse"]
        forbidden = [f["line"] for f in refuse if f["id"] in GH_FORBIDDEN_FINDINGS]
        dead = any(f["id"] == "unauthorized" for f in refuse)
        causes = [_gh_cause(f["id"], f["line"], f.get("repo")) for f in refuse
                  if f["id"] in GH_FORBIDDEN_FINDINGS]
        if forbidden or dead:
            def hold(s):
                s["last_read"] = out_ts
                if forbidden:
                    # each cause recorded (E51): a GET-only check later lifts
                    # only what it can re-observe as gone
                    _gh_set_permission_hold(s, causes, _gh_now())
                if dead:
                    s["holds"]["unauthorized"] = {"text": _gh_mod.LINES["unauthorized"],
                                                  "since": _gh_now()}
            _gh_state_update(hold)
            _gh_log("repo add: writes held (%s)" % ("permission" if forbidden else "unauthorized"))
        p, code = _gh_refusal_payload(res)
        p["held"] = bool(forbidden or dead)
        return p, code
    if not res["ok"]:
        return _gh_refusal_payload(res)

    def mut(s):
        s["last_read"] = out_ts
        # the add re-ran the WHOLE wall (reach, write reach, every permission
        # probe) and it passed: whatever a permission hold recorded is fixed
        s["holds"].pop("permission", None)
        # ...and its result is now the one to show
        s["probe"] = {"at": _gh_now(), "checks": res["checks"],
                      "warnings": [f["line"] for f in res["findings"] if f["level"] == "warn"]}
        for prow in res["repos"]:
            k = _gh_key(prow["full_name"])
            if k == _gh_key(repo):
                s["repos"][k] = _gh_row_from_probe(prow, s["repos"].get(k), ci.get(k),
                                                   k in ci, enable=True)
                for f in ("removed", "removed_at", "moved"):
                    s["repos"][k].pop(f, None)
                s["forgotten"] = [f for f in s.get("forgotten") or []
                                  if f.get("full_name") != k]
                if prot.get(k):
                    s["repos"][k]["protection"] = prot[k]
            elif k in s["repos"]:
                s["repos"][k] = _gh_row_from_probe(prow, s["repos"][k], None, False,
                                                   enable=None)
    _gh_state_update(mut)
    row = _gh_repo(repo) or {}
    _gh_record("repo_add", repo, "enabled %s for Hermes%s" % (
        repo, "" if row.get("enabled") else " (blocked: %s)" % (row.get("enable_blocked") or "")))
    payload = _gh_status_payload()
    payload["added"] = {"full_name": row.get("full_name") or repo,
                        "enabled": bool(row.get("enabled")),
                        "enable_blocked": row.get("enable_blocked")}
    return payload


def _gh_cancel_pending_repo(repo):
    fn = globals().get("_ghc_cancel_repo")
    if callable(fn):
        try:
            fn(repo, "repository disabled")
        except Exception:
            pass


@_gh_safe
def _gh_settings_handler(ctx):
    """POST /api/github/settings {checks_enabled?: bool, max_pushes_day?: int}.
    Widening is OWNER-ONLY; narrowing is not. ping_on_push and draft_prs are
    owner decisions, not settings: they are always true."""
    b = _gh_body(ctx)
    upd = {}
    if "checks_enabled" in b:
        if not isinstance(b["checks_enabled"], bool):
            return ({"ok": False, "error": "checks_enabled must be true or false"}, 400)
        upd["checks_enabled"] = b["checks_enabled"]
    if "max_pushes_day" in b:
        v = b["max_pushes_day"]
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= GH_MAX_PUSHES_DAY:
            return ({"ok": False, "error": "max_pushes_day must be a whole number from 0 to %d"
                                           % GH_MAX_PUSHES_DAY}, 400)
        upd["max_pushes_day"] = v
    for k in ("ping_on_push", "draft_prs"):
        if k in b and b[k] is not True:
            return ({"ok": False, "error": "%s is always on (owner decision)" % k}, 400)
    if not upd:
        return _gh_status_payload()
    if _gh_settings_widen(upd) and _gh_owner_refused(ctx):
        return _GH_OWNER_403
    _gh_state_update(lambda s: s["settings"].update(upd))
    _gh_record("settings", "github", "GitHub settings: " + ", ".join(
        "%s=%s" % (k, upd[k]) for k in sorted(upd)), args=dict(upd))
    return _gh_status_payload()


@_gh_safe
def _gh_pause_handler(ctx):
    """POST /api/github/pause {paused: bool} — the kill switch. ON: anyone
    (it only reduces capability; the agent may). OFF: OWNER-ONLY, and refused
    while a system hold (expired, revoked, forbidden permission) stands."""
    b = _gh_body(ctx)
    paused = b.get("paused")
    if not isinstance(paused, bool):
        return ({"ok": False, "error": "paused must be true or false"}, 400)
    src = str(b.get("source") or "api")
    src = src if re.fullmatch(r"[a-z][a-z0-9_-]{0,23}", src) else "api"
    if paused:
        now = _gh_now()

        def on(s):
            s["settings"]["writes_paused"] = True
            s["pause"] = {"by": src, "at": now}
        _gh_state_update(on)
        _gh_record("pause", "github", "GitHub writes paused (%s)" % src)
        return _gh_status_payload()
    if _gh_owner_refused(ctx):
        return _GH_OWNER_403
    holds = _gh_holds()
    if holds:
        return ({"ok": False, "error": "held",
                 "holds": [{"id": k, "text": v.get("text")} for k, v in sorted(holds.items())],
                 "hint": "GitHub writes stay paused until this is fixed."}, 409)

    def off(s):
        s["settings"]["writes_paused"] = False
        s["pause"] = None
    _gh_state_update(off)
    _gh_record("unpause", "github", "GitHub writes resumed")
    return _gh_status_payload()


# --------------------------------------------------------------------------
# mirrors (E09): the change pipeline's bare copies of the connected
# repositories. Deleted on remove / disconnect / revoke through the pipeline's
# own exports (by name, at call time); a safe fallback of our own otherwise.
# Sandbox clones under ~/HermesSandbox are the OWNER's and are never deleted.
# --------------------------------------------------------------------------
_GH_MIRROR_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}__(?!\.\.?\.git$)[a-z0-9._-]{1,100}\.git$")
GH_CLEANUP_NOTE = ("Hermes deleted its own copies of these repositories (the mirrors it "
                   "keeps under ~/.hermes/dashboard/github/mirrors). The agent's working "
                   "copies under ~/HermesSandbox/github are yours and were kept: delete them "
                   "yourself if you no longer need them.")


def _gh_mirrors_dir():
    return os.path.join(GH_DIR, "mirrors")


def _gh_mirror_path(full_name):
    r = _gh_mod.normalize_repo(full_name)
    if not r:
        return None
    o, n = r.split("/", 1)
    name = ("%s__%s.git" % (o, n)).lower()
    return os.path.join(_gh_mirrors_dir(), name) if _GH_MIRROR_NAME_RE.match(name) else None


def _gh_rm_mirror(path):
    """Fallback deletion of ONE mirror: only a real directory (never a
    symlink) that is a direct child of a real mirrors folder and has the
    mirror name shape. -> True when it is gone afterwards."""
    import stat as _gh_stat
    d = _gh_mirrors_dir()
    try:
        if os.path.islink(d) or not os.path.isdir(d):
            return False
        if os.path.dirname(path) != d or not _GH_MIRROR_NAME_RE.match(os.path.basename(path)):
            return False
        st = os.lstat(path)
        if not _gh_stat.S_ISDIR(st.st_mode):           # a symlink or a file: never touched
            return False
        if os.path.realpath(os.path.dirname(path)) != os.path.realpath(d):
            return False
        shutil.rmtree(path)
    except FileNotFoundError:
        return True
    except OSError as e:
        _gh_log("mirror delete failed: %s" % type(e).__name__)
    return not os.path.lexists(path)


def _gh_delete_mirrors(full_name=None):
    """Delete one repository's mirror (full_name) or all of them (None).
    The pipeline's `_ghc_delete_mirror(owner, repo)` / `_ghc_delete_all_mirrors()`
    win when present (they know about in-flight work); when present but they
    refuse or fail, nothing is deleted behind their back. -> (deleted, left):
    paths, judged from what is on disk afterwards."""
    if full_name is not None:
        p = _gh_mirror_path(full_name)
        before = [p] if p and os.path.lexists(p) else []
    else:
        d = _gh_mirrors_dir()
        try:
            before = [os.path.join(d, n) for n in sorted(os.listdir(d))
                      if _GH_MIRROR_NAME_RE.match(n)] if os.path.isdir(d) else []
        except OSError:
            before = []
    if not before:
        return [], []
    name = "_ghc_delete_mirror" if full_name is not None else "_ghc_delete_all_mirrors"
    fn = globals().get(name)
    if callable(fn):
        try:
            if full_name is not None:
                o, n = _gh_mod.normalize_repo(full_name).split("/", 1)
                fn(o, n)
            else:
                fn()
        except Exception as e:
            _gh_log("%s failed: %s" % (name, type(e).__name__))
    else:
        for p in before:
            _gh_rm_mirror(p)
    deleted = [p for p in before if not os.path.lexists(p)]
    left = [p for p in before if os.path.lexists(p)]
    return deleted, left


def _gh_forget(reason):
    """Delete the credential, cancel pending changes, disable every repo,
    keep sandboxes (the owner's work). -> list of sandbox paths kept."""
    with _GH_LOCK:
        _gh_unlink(_gh_p("credential.json"))

        def mut(s):
            for row in s["repos"].values():
                row["enabled"] = False
            for h in ("expired", "unauthorized", "permission"):
                s["holds"].pop(h, None)
            s["connected_at"] = None
        _gh_state_update(mut)
    n = _gh_cancel_pending(reason)
    kept = []
    for row in _gh_state()["repos"].values():
        p = row.get("sandbox") or _gh_sandbox_path(row.get("full_name"))
        if p and os.path.isdir(p):
            kept.append(p)
    return kept, n


def _gh_forget_mirrors(p):
    """E09: after a disconnect/revoke, delete every mirror and say what was
    deleted and what was left (a symlink, an entry the pipeline refused)."""
    deleted, left = _gh_delete_mirrors(None)
    d = _gh_mirrors_dir()
    try:
        # shaped like a mirror but not one we may delete (e.g. a symlink)
        extra = [os.path.join(d, x) for x in sorted(os.listdir(d))
                 if _GH_MIRROR_NAME_RE.match(x) and os.path.join(d, x) not in deleted
                 and os.path.join(d, x) not in left] if os.path.isdir(d) else []
    except OSError:
        extra = []
    p.update({"mirrors_deleted": deleted, "mirrors_left": left + extra,
              "cleanup_note": GH_CLEANUP_NOTE})
    return p


@_gh_safe
def _gh_disconnect_handler(ctx):
    """POST /api/github/disconnect — anyone (reduces capability). Deletes
    the key from this Mac; the token stays valid on GitHub until it expires
    or the owner deletes it (or uses Revoke now)."""
    had = _gh_connected()
    login = (_gh_cred() or {}).get("login")
    kept, n = _gh_forget("disconnected")
    if had:
        _gh_record("disconnect", "github:@%s" % (login or "?"),
                   "disconnected GitHub; %d pending change(s) cancelled" % n)
    p = _gh_status_payload()
    _gh_forget_mirrors(p)
    p.update({"ok": True, "disconnected": had, "sandboxes_kept": kept, "cancelled": n,
              "manage_url": _gh_mod.TOKEN_PAGE,
              "hint": ("The key is gone from this Mac. It still works on GitHub until it "
                       "expires: delete it at Settings › Developer settings › Personal "
                       "access tokens › Fine-grained tokens, or use Revoke now next time.")})
    return p


@_gh_safe
def _gh_revoke_handler(ctx):
    """POST /api/github/revoke — anyone (reduces capability). Calls GitHub's
    credential revocation API UNAUTHENTICATED, then disconnects whatever the
    answer. Irreversible; GitHub emails the owner."""
    tok = _gh_token()
    if not tok:
        return ({"ok": False, "error": "not_connected"}, 400)
    login = (_gh_cred() or {}).get("login")
    ok, st = _gh_mod.revoke(GH_API_BASE, tok, ssl_context=_gh_sslctx(), timeout=GH_TIMEOUT)
    now = _gh_now()
    _gh_state_update(lambda s: s.__setitem__("last_revoke", now))
    kept, n = _gh_forget("revoked")
    _gh_record("revoke", "github:@%s" % (login or "?"),
               "revoked the GitHub key on GitHub (%s) and disconnected"
               % ("accepted" if ok else "status %s" % st),
               status="done" if ok else "error")
    p = _gh_status_payload()
    _gh_forget_mirrors(p)
    p.update({"ok": True, "revoked": bool(ok), "revoke_status": st, "disconnected": True,
              "sandboxes_kept": kept, "cancelled": n, "manage_url": _gh_mod.TOKEN_PAGE})
    if ok:
        p["message"] = ("GitHub accepted the revocation. The key no longer works anywhere, "
                        "and GitHub will email you about it.")
    else:
        p["message"] = ("GitHub did not confirm the revocation (status %s). Hermes forgot "
                        "the key anyway. Delete it yourself on GitHub: Settings › Developer "
                        "settings › Personal access tokens › Fine-grained tokens." % st)
    return p


@_gh_safe
def _gh_check_handler(ctx):
    """POST /api/github/check — OWNER-ONLY (a passing check can lift a
    hold). Runs the 6-hourly read-only check now."""
    if _gh_owner_refused(ctx):
        return _GH_OWNER_403
    if not _gh_connected():
        return ({"ok": False, "error": "not_connected"}, 400)
    if _gh_now() - _GH_CHECK_LAST["at"] < GH_CHECK_MIN_GAP:
        return ({"ok": False, "error": "too_soon",
                 "hint": "Hermes checked a moment ago. Try again in half a minute."}, 429)
    r = _gh_run_check(force=True)
    if r.get("error") == "busy":
        return ({"ok": False, "error": "busy"}, 409)
    p = _gh_status_payload()
    p["check"] = r
    return p


@_gh_safe
def _gh_drift_ack_handler(ctx):
    """POST /api/github/drift/ack {repo, branch} — OWNER-ONLY ("that push was
    me"): hides one drift alert until the branch moves again."""
    if _gh_owner_refused(ctx):
        return _GH_OWNER_403
    b = _gh_body(ctx)
    repo = _gh_mod.normalize_repo(b.get("repo"))
    br = b.get("branch")
    if not repo or not isinstance(br, str) or not br.startswith("hermes/"):
        return ({"ok": False, "error": "bad_args"}, 400)

    def mut(s):
        for d in s["drift"]:
            if _gh_key(d.get("repo")) == _gh_key(repo) and d.get("branch") == br:
                d["acked"] = True
    _gh_state_update(mut)
    _gh_record("drift_ack", "%s@%s" % (repo, br[:80]), "drift alert acknowledged")
    return _gh_status_payload()


@_gh_safe
def _gh_status_handler(ctx):
    # ?scan=0 (the UI's 60 s poll and the onboarding step): no local ambient
    # probes from a background read; the card asks /api/github/ambient itself
    return _gh_status_payload(fresh_ambient=(ctx.q1("fresh", "") == "1"),
                              scan=(ctx.q1("scan", "1") != "0"))


@_gh_safe
def _gh_ambient_handler(ctx):
    return {"ok": True, "ambient": _gh_ambient(fresh=(ctx.q1("fresh", "") == "1"))}


@_gh_safe
def _gh_create_url_handler(ctx):
    """GET /api/github/create_url?repos=o/a,o/b -> the prefilled token form.
    Pure: no network, no state. target_name only when every repo shares one
    owner (GitHub's form cannot be prefilled with the repositories)."""
    repos, _bad = _gh_mod.parse_repo_list(ctx.q1("repos", ""))
    owners = {r.split("/")[0].lower(): r.split("/")[0] for r in repos}
    target = list(owners.values())[0] if len(owners) == 1 else None
    try:
        host = _gh_socket.gethostname()
    except Exception:
        host = ""
    date = _gh_datetime.date.today().isoformat()
    return {"ok": True, "url": _gh_mod.create_url(host, date, target), "target_name": target,
            "repos": repos}


# --------------------------------------------------------------------------
# status (file reads + the cached ambient finding; NO GitHub network call)
# --------------------------------------------------------------------------
def _gh_attention(state=None, now=None):
    """Needs-attention lines: [{id, level: warn|bad, text, since, repo?}]."""
    now = _gh_now() if now is None else now
    s = state or _gh_state()
    out = []
    c = _gh_cred()
    if c:
        exp = c.get("expires_at")
        if isinstance(exp, (int, float)):
            days = (exp - now) / 86400.0
            if 0 < days <= GH_EXPIRY_WARN_DAYS:
                n = int(days)
                when = ("within a day" if n < 1 else
                        "in 1 day" if n == 1 else "in %d days" % n)
                # stable: the moment the warning window opened, not "now" (a
                # moving `since` reads as a new item on every poll)
                out.append({"id": "expiring", "level": "warn",
                            "since": exp - GH_EXPIRY_WARN_DAYS * 86400,
                            "text": "Your GitHub key expires %s. Create a new one with the "
                                    "button in Settings › Connections and paste it there."
                                    % when})
    for k, h in sorted(_gh_holds(s, now).items()):
        out.append({"id": "hold:" + k, "level": "bad", "since": h.get("since"),
                    "text": str(h.get("text") or k)[:600]})
    for d in s.get("drift") or []:
        if not d.get("acked") and not d.get("resolved"):
            out.append({"id": "drift:%s:%s" % (d.get("repo"), d.get("branch")),
                        "level": "bad", "since": d.get("seen_at"), "repo": d.get("repo"),
                        "text": _gh_drift_text(d)})
    return out


def _gh_line(text, limit=160):
    """Third-party text as one inert line (dashboard/untrusted.py, imported
    lazily per the aux load-order rule; short() as the fallback)."""
    try:
        import untrusted as _gh_ut
        return _gh_ut.line(_gh_mod.short(text, limit * 2), limit)
    except Exception:
        return _gh_mod.short(text, limit)


def _gh_drift_summary_text(repo, rows):
    """ONE notice for one repository's new drift rows (E54)."""
    if len(rows) == 1:
        return _gh_drift_text(rows[0])
    names = ", ".join(_gh_line(d.get("branch"), 60) for d in rows[:3])
    return ("Something other than Hermes pushed to %d hermes/ branches on %s (%s%s). If "
            "that wasn't you, revoke the GitHub key in Settings › Connections." % (
                len(rows), _gh_line(repo, 140), names, ", …" if len(rows) > 3 else ""))


def _gh_send_drift_notices(groups):
    """Send one notice per repository and mark its rows notified. Runs AFTER
    _GH_NET_LOCK is released (E54): a send is a subprocess that may take a
    minute, and a Push waits on that lock."""
    for repo, rows in groups:
        ok, det = _gh_notify(_gh_drift_summary_text(repo, rows))
        keys = {(d.get("branch"), d.get("sha")) for d in rows}

        def mark(s, repo=repo, keys=keys, ok=ok):
            for x in s["drift"]:
                if _gh_key(x.get("repo")) == _gh_key(repo) and \
                        (x.get("branch"), x.get("sha")) in keys:
                    x["notified"] = bool(ok)
        _gh_state_update(mark)
        if not ok:
            _gh_log("drift notice not delivered: %s" % det)


def _gh_drift_text(d):
    return ("Something other than Hermes pushed to %s on %s. If that wasn't you, revoke the "
            "GitHub key in Settings › Connections." % (
                _gh_line(d.get("branch"), 120), _gh_line(d.get("repo"), 140)))


def _gh_public_row(row, state=None):
    full = row.get("full_name")
    ok, why = _gh_writes_allowed(full, state)
    sb = row.get("sandbox") or _gh_sandbox_path(full)
    prot = row.get("protection") if isinstance(row.get("protection"), dict) else None
    ci = row.get("ci") if isinstance(row.get("ci"), dict) else None
    return {"full_name": full, "private": row.get("private"),
            "default_branch": row.get("default_branch"), "size_kb": row.get("size_kb"),
            "archived": bool(row.get("archived")), "enabled": bool(row.get("enabled")),
            "listed": bool(row.get("listed")), "writable": bool(row.get("writable")),
            "pulls": row.get("pulls"), "enable_blocked": row.get("enable_blocked"),
            "protection": prot, "ci": ci, "sandbox": sb,
            "prepared": bool(sb and os.path.isdir(os.path.join(sb, ".git"))),
            "last_check": row.get("last_check"), "last_fetch": row.get("last_fetch"),
            "check_error": row.get("check_error"), "checks": row.get("checks") or {},
            "moved": isinstance(row.get("moved"), dict),
            "moved_reason": _gh_moved_reason(row) if isinstance(row.get("moved"), dict)
            else None,
            "removed": bool(row.get("removed")),
            "writes_ok": ok, "writes_blocked": None if ok else why}


def _gh_write_reach_note(wr):
    """One plain sentence when the wall's write-reach result is not a proof
    (E08). Values as stored: "sampled" (and the older "contained"),
    "unchecked"; None = not connected / not recorded."""
    if wr in ("sampled", "contained"):
        return ("Hermes tried writing to one other public repository of yours and could "
                "not, so this key is not set to All repositories. That is a sample, not a "
                "proof: on GitHub, check that only the repositories you picked are selected.")
    if wr == "unchecked":
        return _gh_mod.LINES["write_reach_unchecked"]
    if wr:
        return ("Hermes could not tell whether this key can write to repositories you did "
                "not pick. On GitHub, check that only the repositories you picked are "
                "selected.")
    return None


def _gh_status_payload(fresh_ambient=False, scan=False):
    """GET /api/github/status. Never the token, never a network call to
    GitHub. `scan=True` (only the GET routes) lets the ambient finding run its
    local read-only probes when its 5-minute cache is cold; every other caller,
    the kill switch and the agent's per-turn preamble included, only ever
    carries the cached finding (None before the first scan) and never waits on
    a subprocess."""
    now = _gh_now()
    s = _gh_state()
    c = _gh_cred()
    exp = (c or {}).get("expires_at")
    holds = _gh_holds(s, now)
    try:
        ambient = (_gh_ambient(fresh=fresh_ambient) if scan or fresh_ambient
                   else _GH_AMBIENT["val"])
    except Exception:
        ambient = None
    repos = [_gh_public_row(r, s) for _k, r in sorted(s["repos"].items())]
    pchecks = (s.get("probe") or {}).get("checks") if c else None
    wr = pchecks.get("write_reach") if isinstance(pchecks, dict) else None
    wr = wr if isinstance(wr, str) and re.fullmatch(r"[a-z_]{1,32}", wr) else None
    if wr == "contained":
        # an older build's label for ONE refused sample: never a proof, and
        # the card treats "contained" as proven, so say what it really was
        wr = "sampled"
    out = {
        "ok": True, "connected": bool(c),
        "kind": (c or {}).get("kind"), "login": (c or {}).get("login"),
        "user_id": (c or {}).get("user_id"),
        "expires_at": exp, "expires_at_iso": _gh_iso(exp) if exp else None,
        "expires_in_days": (round((exp - now) / 86400.0, 1)
                            if isinstance(exp, (int, float)) else None),
        "no_expiry": bool(c) and not isinstance(exp, (int, float)),
        "verified_at": (c or {}).get("verified_at"),
        "writes_paused": _gh_paused(s),
        "paused": {"by_owner_switch": bool(s["settings"].get("writes_paused")),
                   "switch": s.get("pause"),
                   "holds": [{"id": k, "text": v.get("text"), "since": v.get("since")}
                             for k, v in sorted(holds.items())]},
        "repos": repos,
        "enabled": [r["full_name"] for r in repos if r["enabled"]],
        "pending": _gh_pending_count(),
        "pending_ids": _gh_pending_ids(),
        "drift": [d for d in s.get("drift") or [] if not d.get("resolved")][:50],
        "attention": _gh_attention(s, now),
        "ambient": ambient,
        "settings": dict(s["settings"]),
        "push_budget": _gh_push_budget(now, s),
        "check": {"last": s.get("last_check"), "ok": s.get("last_check_ok"),
                  "error": s.get("last_check_error"),
                  "next": ((s.get("last_check") or now) + _gh_check_gap(s))
                  if c and s["settings"].get("checks_enabled") else None},
        "probe": s.get("probe"),
        "write_reach": wr,
        "write_reach_note": _gh_write_reach_note(wr),
        "last_read": _gh_last_read_ts(),
        "workflow_note": _gh_mod.WORKFLOW_NOTE,
        "links": {"manage": _gh_mod.TOKEN_PAGE, "create": _gh_mod.CREATE_PAGE},
        "ruleset": {"recommended": _gh_mod.RULESET_RECOMMENDED,
                    "light": _gh_mod.RULESET_LIGHT, "path": _gh_mod.RULESET_PATH,
                    "text": _gh_mod.RULESET_TEXT},
        "sandbox_root": GH_SANDBOX_ROOT,
        "routes": {"owner_only": list(GH_ROUTES_OWNER_ONLY),
                   "anyone": list(GH_ROUTES_ANYONE)},
        # says what the wall OBSERVES, never more (E08/E50): reach beyond the
        # picked repositories is sampled, see write_reach_note
        "headline": ("Hermes keeps your GitHub key in a file only your account can read and "
                     "never gives it to the agent. On this Mac the agent runs as your "
                     "account, so a determined or tricked agent could still read that file "
                     "or approve its own change. That is why Hermes refuses a key that it "
                     "sees can change settings, read secrets, add webhooks or reach "
                     "private repositories you did not pick; why the key "
                     "should expire; why GitHub's own branch rules matter; and why every "
                     "push messages you."),
    }
    return out


# --------------------------------------------------------------------------
# the 6-hourly read-only check (§3.6): protection, expiry, permission
# re-probe, private reach, CI exposure, drift
# --------------------------------------------------------------------------
def _gh_drift_scan(full, heads, ledger_heads):
    """[{branch, sha, kind: unknown|moved}] for one repository's hermes/*
    heads against what the ledger says Hermes left there."""
    out = []
    for br, sha in sorted((heads or {}).items()):
        lh = ledger_heads.get(br, Ellipsis)
        if lh is Ellipsis or lh is None:
            out.append({"branch": br, "sha": sha, "kind": "unknown"})
        elif lh != sha:
            out.append({"branch": br, "sha": sha, "kind": "moved", "ledger_sha": lh})
    return out


def _gh_run_check(force=False, now=None):
    """One full check. Network OUTSIDE the store lock; one writer at a time.
    -> {ok, error?, repos, drift_new, holds}. Never raises."""
    if not _GH_NET_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "busy"}
    try:
        res = _gh_run_check_locked(force, now)
    except Exception as e:
        _gh_log("check failed: %s" % type(e).__name__)
        err = "internal: " + type(e).__name__
        try:
            # the attempt counts, or the loop would retry every ten minutes forever
            def failed(s, t=_gh_now() if now is None else now):
                s["last_check"] = t
                s["check_fails"] = (s.get("check_fails") or 0) + 1
                s["last_check_ok"] = False
                s["last_check_error"] = err
            _gh_state_update(failed)
        except Exception:
            pass
        return {"ok": False, "error": err}
    finally:
        _GH_NET_LOCK.release()
    # notices go out with the lock released (E54)
    try:
        _gh_send_drift_notices(res.pop("_notices", None) or [])
    except Exception as e:
        _gh_log("drift notices: %s" % type(e).__name__)
    return res


def _gh_run_check_locked(force, now):
    now = _gh_now() if now is None else now
    _GH_CHECK_LAST["at"] = now
    c = _gh_cred()
    if not c:
        return {"ok": False, "error": "not_connected"}
    st = _gh_state()
    if not force and not st["settings"].get("checks_enabled"):
        return {"ok": False, "error": "disabled"}
    api = _gh_api(c["token"])
    # "expired" is never STORED (E51): _gh_holds() computes it from the
    # credential on every read, so a check run under a wrong clock cannot
    # leave it behind; a copy stored by an older build is dropped here
    set_holds, clear_holds = {}, {"expired"}
    exp = c.get("expires_at")
    causes_now, perm_obs = [], None
    r = api.get("/user")
    result = {"ok": True, "repos": {}, "drift_new": [], "holds": []}
    private_seen_all, seen_ids_all = [], {}
    if r.status == 401:
        set_holds["unauthorized"] = {"text": _gh_mod.LINES["unauthorized"] +
                                     " GitHub writes are paused until you connect a new key.",
                                     "since": now}
        repos_res = {}
        net_err = "GitHub did not accept the key (401)"
    elif r.status != 200 or r.error:
        net_err = "GitHub did not answer (status %s)" % r.status
        repos_res = {}
    else:
        net_err = None
        clear_holds.add("unauthorized")
        hexp = _gh_mod.parse_expiry(r.headers.get("github-authentication-token-expiration", ""))
        if hexp:
            exp = hexp
        # private reach: the token may have been widened on GitHub since
        kn = _gh_known(st)
        known, known_ids = kn["names"], kn["ids"]
        private_seen, seen_ids, more, reach_err = [], {}, False, None
        for page in range(1, _gh_mod.MAX_PAGES + 1):
            rr = api.get("/user/repos", query={"visibility": "private", "per_page": "100",
                                               "page": str(page)})
            if rr.status != 200 or not isinstance(rr.data, list):
                reach_err = "private reach unreadable (status %s)" % rr.status
                break
            for it in rr.data:
                if isinstance(it, dict) and it.get("private"):
                    fn = _gh_mod.normalize_repo(str(it.get("full_name") or ""))
                    if fn:
                        private_seen.append(fn)
                        seen_ids[_gh_key(fn)] = _gh_mod._repo_id(it.get("id"))
            if len(rr.data) < 100:
                break
            if page == _gh_mod.MAX_PAGES:
                more = True
        private_seen_all, seen_ids_all = private_seen, seen_ids
        perm_obs = {"reach_ok": not reach_err, "extras": set(), "too_many": False,
                    "repo": {}, "acct": {}}
        # account permissions that widen reach (E50): reads only
        acct_err = None
        af, ac, _aretry = _gh_mod.probe_account(api)
        perm_obs["acct"] = ac
        for f in af:
            if f["id"] == "unauthorized":
                set_holds["unauthorized"] = {"text": _gh_mod.LINES["unauthorized"],
                                             "since": now}
            elif f["id"] == "network":
                acct_err = "account permissions unreadable (status %s)" % f.get("status")
            else:
                causes_now.append(_gh_cause(f["id"], f["line"]))
        repos_res = {}
        for k, row in st["repos"].items():
            if not _gh_row_active(row):
                continue
            try:
                repos_res[k] = _gh_mod.check_repo(api, row["full_name"])
            except Exception as e:
                # one odd repository must never abort the check for the others
                # (holds, expiry, drift): report it and carry on
                _gh_log("check of one repository failed: %s" % type(e).__name__)
                repos_res[k] = {"ok": False, "error": "internal: " + type(e).__name__,
                                "meta": None, "protection": None, "forbidden": [],
                                "checks": {}, "heads": None, "ci": None,
                                "unauthorized": False}
        if not reach_err:
            # a repository renamed or transferred keeps its id (E07): the new
            # name of one the owner connected is not "unlisted". The id a
            # redirect seen in THIS check points at counts too: a row stored
            # before ids were kept has none of its own yet
            known_ids = set(known_ids) | {
                cr["moved_to_id"] for cr in repos_res.values()
                if cr.get("moved") and isinstance(cr.get("moved_to_id"), int)}
            extra = [p for p in private_seen if _gh_key(p) not in known
                     and not (seen_ids.get(_gh_key(p)) in known_ids)]
            if more or len(private_seen) > GH_MAX_REPOS:
                perm_obs["too_many"] = True
                causes_now.append(_gh_cause("too_many", _gh_mod.LINES["too_many"]))
            elif extra:
                perm_obs["extras"] = {_gh_key(x) for x in extra}
                causes_now.append(_gh_cause("unlisted_private", _gh_mod._plural_fix(
                    _gh_mod.LINES["unlisted_private"].format(
                        n=len(extra), first=", ".join(extra[:3]))), repos=extra))
        any_err = bool(reach_err or acct_err)
        for k, cr in repos_res.items():
            if cr.get("unauthorized"):
                set_holds["unauthorized"] = {"text": _gh_mod.LINES["unauthorized"],
                                             "since": now}
            for pid, v in (cr.get("checks") or {}).items():
                perm_obs["repo"].setdefault(pid, []).append(v)
            for f in cr.get("forbidden") or []:
                if f["id"] != "unauthorized":
                    causes_now.append(_gh_cause(f["id"], "%s (%s)" % (f["line"], f.get("repo")),
                                                repo=f.get("repo")))
            if cr.get("error") and not cr.get("gone") and not cr.get("moved"):
                # a repository the key no longer reaches is already disabled
                # (and shows its own check_error), one renamed on GitHub is
                # marked moved (E07): counting either here would keep the
                # check "failing" for ever
                any_err = True
        perm_obs["any_err"] = any_err
        if any_err and not net_err:
            net_err = "; ".join([e for e in [reach_err, acct_err] + [
                "%s: %s" % (cr.get("meta", {}) and cr["meta"].get("full_name") or k,
                            cr["error"]) for k, cr in repos_res.items()
                if cr.get("error") and not cr.get("gone") and not cr.get("moved")]
                if e])[:300]
    out_ts = api.last_out or now
    # E57: a push interrupted between its ledger row and the push left the
    # ledger naming a commit GitHub never received; settle it BEFORE the
    # drift scan reads the ledger, or Hermes' own branch reads as drift. The
    # caller holds _GH_NET_LOCK, which is the lock net_held=True stands for
    rec = globals().get("_ghc_reconcile")
    if callable(rec):
        try:
            rec(None, net_held=True)
        except Exception as e:
            _gh_log("check: reconcile failed: %s" % type(e).__name__)
    ledger = _gh_ledger_rows()
    new_drift = []
    perm_after = [False]

    def mut(s):
        s["last_check"] = now
        s["last_read"] = max(s.get("last_read") or 0, out_ts)
        s["last_check_ok"] = net_err is None
        s["last_check_error"] = net_err
        s["check_fails"] = 0 if net_err is None else (s.get("check_fails") or 0) + 1
        for h in clear_holds:
            if h not in set_holds:
                s["holds"].pop(h, None)
        for h, v in set_holds.items():
            prev = s["holds"].get(h)
            s["holds"][h] = dict(v, since=(prev or {}).get("since") or v["since"])
        if perm_obs is not None:
            # E51: drop only the causes this check re-observed as gone, add
            # what it saw now. A hold stored before causes were recorded keeps
            # the old rule (cleared by a check with no finding and no error)
            prev = s["holds"].get("permission")
            if prev is not None and not isinstance(prev.get("causes"), list):
                if causes_now:
                    s["holds"].pop("permission", None)
                    _gh_set_permission_hold(s, causes_now, prev.get("since") or now)
                elif not perm_obs["any_err"]:
                    s["holds"].pop("permission", None)
            else:
                if prev is not None:
                    prev["causes"] = [c for c in prev["causes"]
                                      if not _gh_cause_gone(c, perm_obs)]
                _gh_set_permission_hold(s, causes_now, now)
        perm_after[0] = "permission" in s["holds"]
        for k, cr in repos_res.items():
            row = s["repos"].get(k)
            if row is None:
                continue
            row["last_check"] = now
            row["check_error"] = cr.get("error")
            if cr.get("moved"):
                # renamed or transferred (E07): writes there stop, nothing else
                prev = row.get("moved") if isinstance(row.get("moved"), dict) else {}
                to_id = cr.get("moved_to_id") or prev.get("to_id") or row.get("id")
                to = next((p for p in private_seen_all
                           if to_id and seen_ids_all.get(_gh_key(p)) == to_id), None)
                row["moved"] = {"since": prev.get("since") or now, "to_id": to_id,
                                "to": to or prev.get("to")}
            if cr.get("meta"):
                row.pop("moved", None)
                for f in ("private", "default_branch", "size_kb", "archived"):
                    row[f] = cr["meta"][f]
                if isinstance(cr["meta"].get("id"), int):
                    row["id"] = cr["meta"]["id"]
            if cr.get("protection"):
                row["protection"] = cr["protection"]
            if cr.get("checks"):
                row["checks"] = cr["checks"]
            blocked_by_ci = str(row.get("enable_blocked") or "").startswith(
                ("Pushing a hermes/ branch", GH_CI_UNREADABLE[:40]))
            if cr.get("ci") is not None:
                ci = dict(cr["ci"], checked_at=now)
                row["ci"] = ci
                if ci.get("exposed"):
                    row["enable_blocked"] = _gh_ci_block_text(row)
                elif blocked_by_ci:
                    row["enable_blocked"] = None
            elif cr.get("ci_unreadable"):
                # too many workflow files, an odd name, a file GitHub will not
                # inline, a 403: fails CLOSED, as it does at connect. (A 5xx or a
                # timeout says nothing about the workflows: the last verdict stays.)
                row["ci"] = None
                if blocked_by_ci or not row.get("enable_blocked"):
                    row["enable_blocked"] = GH_CI_UNREADABLE
            if cr.get("gone"):
                row["enabled"] = False
                row["enable_blocked"] = "The connected key no longer reaches this repository."
            heads = cr.get("heads")
            if heads is None:
                continue
            found = _gh_drift_scan(row["full_name"], heads,
                                   _gh_ledger_heads(row["full_name"], ledger))
            keep = []
            for d in s["drift"]:
                if _gh_key(d.get("repo")) != k:
                    keep.append(d)
                    continue
                still = any(x["branch"] == d.get("branch") and x["sha"] == d.get("sha")
                            for x in found)
                if still:
                    keep.append(d)
            have = {(d.get("branch"), d.get("sha")) for d in keep
                    if _gh_key(d.get("repo")) == k}
            for x in found:
                if (x["branch"], x["sha"]) not in have:
                    d = {"repo": row["full_name"], "branch": x["branch"], "sha": x["sha"],
                         "kind": x["kind"], "seen_at": now, "notified": False,
                         "acked": False}
                    if x.get("ledger_sha"):
                        d["ledger_sha"] = x["ledger_sha"]
                    keep.append(d)
                    new_drift.append(dict(d))
            s["drift"] = keep
    _gh_state_update(mut)
    _GH_NET["last_out"] = max(_GH_NET["last_out"], out_ts)
    if isinstance(exp, (int, float)) and exp != c.get("expires_at"):
        with _GH_LOCK:
            cc = _gh_cred()
            if cc and cc.get("token") == c.get("token"):
                cc["expires_at"] = exp
                _gh_atomic_write(_gh_p("credential.json"), cc)
    # notices: ONE per repository per check (E54), sent by _gh_run_check
    # AFTER the network lock is released. An alert whose Telegram never went
    # out (the sender was down, Telegram not set up yet) is tried again at the
    # next check, a few repositories at a time: the attention line stays until
    # the owner acks it either way
    groups = {}
    for d in new_drift:
        groups.setdefault(_gh_key(d["repo"]), (d["repo"], []))[1].append(d)
    older = {}
    for d in _gh_state()["drift"]:
        if d.get("notified") or d.get("acked") or d.get("resolved"):
            continue
        k = _gh_key(d.get("repo"))
        if k in groups or (k not in older and len(older) >= GH_DRIFT_RETRY_REPOS):
            continue
        older.setdefault(k, (d.get("repo"), []))[1].append(d)
    result["_notices"] = list(groups.values()) + list(older.values())
    if set_holds or perm_after[0]:
        _gh_log("check: writes held (%s)" % ", ".join(sorted(
            set(set_holds) | ({"permission"} if perm_after[0] else set()))))
    result["ok"] = net_err is None
    if net_err:
        result["error"] = net_err
    result["repos"] = {k: {"ok": bool(v.get("ok")), "error": v.get("error")}
                       for k, v in repos_res.items()}
    result["drift_new"] = [{"repo": d["repo"], "branch": d["branch"], "kind": d["kind"]}
                           for d in new_drift]
    result["holds"] = sorted(set(set_holds) | ({"permission"} if perm_after[0] else set()))
    return result


def _gh_check_gap(s):
    """Seconds from the last check to the next (E52): the 6-hour interval
    after a good check; after a failed one 10, 20, 40… minutes, capped."""
    if s.get("last_check_ok") is False:
        n = max(1, int(s.get("check_fails") or 1))
        return min(GH_CHECK_INTERVAL, GH_CHECK_RETRY_BASE * (2 ** min(n - 1, 20)))
    return GH_CHECK_INTERVAL


def _gh_check_due(now=None):
    now = _gh_now() if now is None else now
    if not _gh_connected():
        return False
    s = _gh_state()
    if not s["settings"].get("checks_enabled"):
        return False
    last = s.get("last_check") or 0
    return now - last >= _gh_check_gap(s)


def _gh_check_loop():
    time.sleep(GH_CHECK_START_DELAY)
    while True:
        try:
            if _gh_check_due():
                _gh_run_check()
        except Exception as e:
            _gh_log("check loop: %s" % type(e).__name__)
        time.sleep(GH_LOOP_TICK)


# --------------------------------------------------------------------------
# the agent's one stable context line (wired by the agent-surface cluster,
# which wraps access_preamble the aux_shortcuts way — NOT done here, so no
# prompt ever mentions a skill before it exists)
# --------------------------------------------------------------------------
def _gh_preamble_line():
    """'' unless connected with at least one enabled, prepared sandbox."""
    if not _gh_connected():
        return ""
    paths = []
    for r in _gh_status_payload().get("repos") or []:
        if r.get("enabled") and r.get("prepared") and r.get("sandbox"):
            paths.append(r["sandbox"])
    if not paths:
        return ""
    try:
        import untrusted as _gh_ut
        fmt = lambda p: _gh_ut.line(p, 200)                      # noqa: E731
    except Exception:
        fmt = lambda p: re.sub(r"\s+", " ", str(p))[:200]        # noqa: E731
    return ("[context] GitHub sandboxes you may edit freely: %s. Publishing is the owner's "
            "decision: request a change with the github-sandbox skill, then stop. Never git "
            "push, never run gh, never read Hermes's GitHub key.\n\n"
            % "; ".join(fmt(p) for p in sorted(paths)))


# access_preamble wrap (round 3, agent surface, design §5.2) — the
# aux_shortcuts runtime-override pattern, with one difference that matters:
# the line is INSERTED among server.py's stable invariant lines (right after
# the local-index line, the last of them) instead of appended. The mlx prompt
# cache reuses the longest common token prefix (P3.B3,
# docs/plans/b3-prefix-ttft-findings.md), so a stable line placed after the
# tasks / calendar / memory / clock lines would be re-prefilled every minute;
# placed here it changes only when the owner connects, enables or prepares
# something. Disconnected (or nothing prepared) the preamble is returned
# byte-for-byte as server.py built it. aux files exec SORTED, so this wraps
# server.py's own function and aux_shortcuts then wraps this one.
_GH_PREAMBLE_AFTER = "[context] You can search the user's OWN local index"
_GH_PREAMBLE_BEFORE = ("[context] The user's open tasks", "[context] Today's events",
                       "[context] Google Calendar could not be read",
                       "[context] Local time:")
_GH_PREAMBLE_TTL = 300
_GH_PREAMBLE_CACHE = globals().get("_GH_PREAMBLE_CACHE") or {"key": None, "at": 0.0,
                                                                "line": ""}


def _gh_preamble_key():
    """What the line depends on, cheaply: the credential and state files."""
    out = []
    for n in ("credential.json", "state.json"):
        try:
            st = os.stat(_gh_p(n))
            out.append((st.st_ino, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


def _gh_preamble_cached():
    """_gh_preamble_line() as ONE line, recomputed only when the credential or
    state file changes (or every few minutes, for a sandbox folder made or
    removed by hand): the status payload behind it reads every change record,
    and this runs on every chat turn."""
    key, now = _gh_preamble_key(), time.time()
    c = _GH_PREAMBLE_CACHE
    if c["key"] == key and now - c["at"] < _GH_PREAMBLE_TTL:
        return c["line"]
    line = " ".join(str(_gh_preamble_line() or "").split("\n")).strip()
    c.update(key=key, at=now, line=line)
    return line


def _gh_preamble_insert(base, line):
    """`base` with `line` among the stable lines: after the local-index line,
    else before the first volatile line, else at the end."""
    if not line:
        return base
    rows = base.split("\n")
    at = None
    for i, r in enumerate(rows):
        if r.startswith(_GH_PREAMBLE_AFTER):
            at = i + 1
            break
    if at is None:
        for i, r in enumerate(rows):
            if r.startswith(_GH_PREAMBLE_BEFORE):
                at = i
                break
    if at is None:
        return base.rstrip("\n") + ("\n" if base.strip() else "") + line + "\n\n"
    return "\n".join(rows[:at] + [line] + rows[at:])


try:
    _gh_prev_access_preamble                     # noqa: B018 — rewrap guard
except NameError:
    try:
        _gh_prev_access_preamble = access_preamble   # noqa: F821
    except NameError:
        _gh_prev_access_preamble = None

if _gh_prev_access_preamble is not None:
    # The signature MIRRORS server.py's access_preamble(user_text="") (1.2.2:
    # the memory layer retrieves against the message being sent).
    def access_preamble(user_text=""):
        base = _gh_prev_access_preamble(user_text)
        try:
            return _gh_preamble_insert(base, _gh_preamble_cached())
        except Exception as e:
            _gh_log("preamble line skipped: %s" % type(e).__name__)
            return base


# --------------------------------------------------------------------------
# routes + thread
# --------------------------------------------------------------------------
register_get("/api/github/status", _gh_status_handler)              # noqa: F821
register_get("/api/github/ambient", _gh_ambient_handler)            # noqa: F821
register_get("/api/github/create_url", _gh_create_url_handler)      # noqa: F821
register_post("/api/github/connect", _gh_connect_handler)           # noqa: F821
register_post("/api/github/repos", _gh_repos_handler)               # noqa: F821
register_post("/api/github/settings", _gh_settings_handler)         # noqa: F821
register_post("/api/github/pause", _gh_pause_handler)               # noqa: F821
register_post("/api/github/disconnect", _gh_disconnect_handler)     # noqa: F821
register_post("/api/github/revoke", _gh_revoke_handler)             # noqa: F821
register_post("/api/github/check", _gh_check_handler)               # noqa: F821
register_post("/api/github/drift/ack", _gh_drift_ack_handler)       # noqa: F821

if not globals().get("_gh_checks_thread_started"):
    globals()["_gh_checks_thread_started"] = True
    try:
        threading.Thread(target=_gh_check_loop, daemon=True, name="gh-checks").start()
    except Exception as _gh_e:                                       # pragma: no cover
        print("[aux_github] check thread failed to start: %r" % (_gh_e,), file=sys.stderr)
