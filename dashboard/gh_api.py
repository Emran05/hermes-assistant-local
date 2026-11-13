"""gh_api.py — the GitHub REST client and scope wall behind the GitHub sandbox
connector (docs/plans/github-sandbox-2026-10.md §1.4, §2.1, §3.6, §8.1).

A real sibling module, imported like permissions.py: plain stdlib, no dashboard
globals, so every rule here is unit-testable against a fake GitHub on a
loopback port (tools/tests/unit/_gh_fake.py) and never against the real one.

What lives here
    classify_token(tok)        token kind from its prefix; only github_pat_ is kept
    normalize_repo / parse_repo_list   "owner/repo" validation
    scrub(text, *secrets)      the token, its base64 and its basic-auth form -> [token]
    parse_expiry(value)        GitHub-Authentication-Token-Expiration -> unix ts
    git_auth_env(token)        the GIT_CONFIG_COUNT env triple for ONE network git call
    resolve_bases(env)         api/git base; a test override only when loopback + offline tier
    GitHubAPI                  requests with no redirects, no proxy on loopback, bounded reads
    probe(api, repos)          the scope wall (§1.4 rows 1-12)
    probe_forbidden(api, repo) rows 8-12 alone (the 6-hourly re-probe)
    check_repo(api, ...)       one repository's 6-hourly read-only check
    protection_summary(...)    branch rules -> "main protected: ..." / "main not protected"
    ci_exposure(files)         the workflow heuristic (owner decision: a repo whose CI
                               runs with secrets on a hermes/* push is refused)
    revoke(base, token)        POST /credentials/revoke, UNAUTHENTICATED
    create_url(host, date)     the prefilled fine-grained token form

Laws (the design's, restated where the code enforces them)
  * The token never appears in a return value except where a caller passed it
    in; every upstream string that is surfaced runs through scrub() and is cut
    to a short line. An upstream body is never forwarded.
  * No redirect is ever followed (urllib would forward Authorization to the
    redirect target, which can be any host).
  * When unsure, refuse: any 5xx, timeout, rate limit, redirect or unexpected
    status on a refuse-probe is a refusal with retry=True, never a pass.
"""
import base64
import datetime as _gha_dt
import ipaddress
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

API_DEFAULT = "https://api.github.com"
GIT_DEFAULT = "https://github.com"
TOKEN_PAGE = "https://github.com/settings/personal-access-tokens"
CREATE_PAGE = "https://github.com/settings/personal-access-tokens/new"
GH_MAX_REPOS = 10
USER_AGENT = "hermes-assistant-github-sandbox/1"
API_VERSION = "2022-11-28"
MAX_BODY = 4 * 1024 * 1024          # bytes read from any one response
MAX_PAGES = 3                       # /user/repos pages read (100 each)
PROBE_BLOB = "hermes-probe"         # the unreferenced blob probes 5-6 create

# ---------------------------------------------------------------------------
# exact copy (§1.3). The UI shows these lines verbatim, one per finding.
# ---------------------------------------------------------------------------
LINES = {
    "classic": ("This is a classic token. Classic tokens reach every repository "
                "you own, so Hermes will not keep it. Create a fine-grained token "
                "with the button above."),
    "app_token": ("This token came from an app sign-in (for example the GitHub "
                  "CLI), not from the token form. Hermes only accepts a "
                  "fine-grained token you created for it."),
    "unknown_kind": ("This does not look like a GitHub fine-grained token (they "
                     "start with github_pat_). Create one with the button above "
                     "and paste it here."),
    "administration": ("It can change repository settings and branch rules "
                       "(Administration). Remove that permission."),
    "secrets": ("It can read your Actions secrets or variables. Remove the "
                "Secrets and Variables permissions."),
    "webhooks": ("It can add webhooks, which send repository events to other "
                 "servers. Remove the Webhooks permission."),
    "actions": ("It can see or run your GitHub Actions. Hermes does not need "
                "Actions. Remove that permission."),
    "git_ssh_keys": ("It can read or add SSH keys on your GitHub account (account "
                     "permission Git SSH keys). A key that can add an SSH key can reach "
                     "every repository you have. Remove that permission."),
    "gpg_keys": ("It can read or add GPG keys on your GitHub account (account "
                 "permission GPG keys). Remove that permission."),
    "ssh_signing_keys": ("It can read or add SSH signing keys on your GitHub account "
                         "(account permission SSH signing keys). Remove that permission."),
    "unlisted_private": ("It reaches {n} private repositories you did not list "
                         "({first}). Edit the token on GitHub, choose Only select "
                         "repositories, and pick just the ones Hermes may use."),
    "unlisted_write": ("It can write to {repo}, which you did not list. That "
                       "usually means All repositories is selected. Choose Only "
                       "select repositories."),
    "too_many": ("It reaches more than 10 repositories. Hermes accepts at most "
                 "10. Choose Only select repositories."),
    "no_contents": ("It cannot write to {repo}. Give it Contents: Read and write. "
                    "(If the repository belongs to an organization, an owner may "
                    "need to approve the token first.)"),
    "no_pulls": ("It cannot open pull requests on {repo}. Give it Pull requests: "
                 "Read and write. Hermes can still push a branch there."),
    "unauthorized": "GitHub did not accept this token. It may be expired or revoked.",
    "network": ("Hermes could not finish checking this token with GitHub, so "
                "nothing was stored. Try again in a minute."),
    "no_repos": ("Hermes could not see which repositories this token is for. "
                 "List them above (owner/repo, one per line) and check again."),
    "no_writable": ("This token cannot write to any of the repositories you "
                    "listed, so there is nothing Hermes could do with it."),
    "empty_repo": ("{repo} is empty on GitHub, so Hermes cannot check it or work "
                   "on it yet. Add a first commit (a README is enough) on GitHub, "
                   "then add it again."),
    "moved": ("This repository was renamed or transferred on GitHub: remove it and add "
              "it under its new name. Hermes will not write to it until then."),
    "write_reach_unchecked": ("Hermes could not check whether this key can write to "
                              "repositories you did not pick: it found no other public "
                              "repository of yours to test against. On GitHub, make sure "
                              "the key is set to Only select repositories."),
    "no_expiry": ("This token never expires. Hermes works with it, but a token "
                  "that expires in 30 days limits the damage if it ever leaks."),
}
FINAL_LINE = ("Nothing was stored. Delete this token on GitHub (Settings › "
              "Developer settings › Personal access tokens › Fine-grained tokens) "
              "and create a new one with the button above.")
WORKFLOW_NOTE = ("Workflows cannot be checked through GitHub's API, so Hermes "
                 "refuses any change to .github/workflows.")
UNPROTECTED_TEXT = ("GitHub isn't enforcing rules on {b} for this repository. "
                    "Hermes still never pushes to {b}, but GitHub itself won't "
                    "stop a stolen key.")

# ---------------------------------------------------------------------------
# recommended rulesets (§3.6) — Copy / Import on the card
# ---------------------------------------------------------------------------
RULESET_RECOMMENDED = {
    "name": "Hermes: protect the default branch", "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
    "rules": [{"type": "deletion"}, {"type": "non_fast_forward"},
              {"type": "pull_request", "parameters": {
                  "required_approving_review_count": 0,
                  "dismiss_stale_reviews_on_push": False,
                  "require_code_owner_review": False,
                  "require_last_push_approval": False,
                  "required_review_thread_resolution": False}}],
    "bypass_actors": []}
RULESET_LIGHT = {
    "name": "Hermes: no force-push or deletion of the default branch",
    "target": "branch", "enforcement": "active",
    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
    "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    "bypass_actors": []}
RULESET_PATH = "Settings › Rules › Rulesets › New ruleset › Import a ruleset"
RULESET_TEXT = ("With this rule nobody, including you, can push straight to main, "
                "force-push it or delete it. You merge through pull requests, and "
                "you can still merge your own. A stolen key could still merge a "
                "pull request it opened; only the separate-identity mode (coming) "
                "stops that.")

# ---------------------------------------------------------------------------
# tokens and repositories
# ---------------------------------------------------------------------------
_FG_RE = re.compile(r"^github_pat_[A-Za-z0-9_]{30,240}$")
_APP_PREFIXES = ("gho_", "ghu_", "ghs_", "ghr_")
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
# branch names Hermes will carry around: git allows far more (`<|im_end|>` is a
# legal ref name), and these strings reach the agent through /status
SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
# anything token-shaped, for scrubbing text whose token we were not handed
_TOKENISH_RE = re.compile(r"(?:github_pat_[A-Za-z0-9_]{10,}|gh[pousr]_[A-Za-z0-9]{10,})")
_BASIC_RE = re.compile(r"(?i)(authorization\s*:\s*(?:basic|bearer|token)\s+)\S+")


def classify_token(tok):
    """'fine_grained_pat' | 'classic' | 'app_token' | 'unknown' | 'empty'.
    Only the first is ever stored. Whitespace inside a token is 'unknown' (a
    header-splitting paste), never trimmed into something else."""
    if not isinstance(tok, str):
        return "empty"
    t = tok.strip()
    if not t:
        return "empty"
    if t.startswith("ghp_") or re.fullmatch(r"[0-9a-f]{40}", t):
        return "classic"
    if t.startswith(_APP_PREFIXES):
        return "app_token"
    if _FG_RE.fullmatch(t):
        return "fine_grained_pat"
    return "unknown"


def kind_line(kind):
    return {"classic": LINES["classic"], "app_token": LINES["app_token"]}.get(
        kind, LINES["unknown_kind"])


def normalize_repo(s):
    """'owner/repo' (case kept for display) or None. Accepts a pasted
    https://github.com/owner/repo(.git) URL; refuses '.', '..' and anything
    that is not exactly two path segments."""
    if not isinstance(s, str):
        return None
    t = s.strip()
    for pre in ("https://github.com/", "http://github.com/", "github.com/",
                "git@github.com:"):
        if t.lower().startswith(pre):
            t = t[len(pre):]
            break
    t = t.strip("/")
    if t.endswith(".git"):
        t = t[:-4]
    parts = t.split("/")
    if len(parts) != 2:
        return None
    o, n = parts
    if not _OWNER_RE.fullmatch(o) or not _NAME_RE.fullmatch(n) or n in (".", ".."):
        return None
    return o + "/" + n


def parse_repo_list(value, cap=GH_MAX_REPOS):
    """(repos, bad) from a list or a newline/comma/space separated string.
    Deduplicated case-insensitively, order kept. `bad` lists rejected entries
    (truncated) so the UI can say which line is wrong."""
    if value is None:
        return [], []
    if isinstance(value, str):
        items = re.split(r"[\s,]+", value)
    elif isinstance(value, list):
        items = value
    else:
        return [], ["(not a list)"]
    out, seen, bad = [], set(), []
    for it in items:
        if isinstance(it, str) and not it.strip():
            continue
        r = normalize_repo(it)
        if not r:
            bad.append(str(it)[:80])
            continue
        if r.lower() in seen:
            continue
        seen.add(r.lower())
        out.append(r)
    if cap and len(out) > cap:
        bad.append("more than %d repositories" % cap)
    return out, bad


def scrub(text, *secrets):
    """Replace every form a token can take in text with [token]: raw, URL-
    quoted, base64 of the token and of 'x-access-token:<token>' (the git
    extraheader), plus anything token-shaped and any Authorization value."""
    t = "" if text is None else (text if isinstance(text, str) else str(text))
    for s in secrets:
        if not isinstance(s, str) or len(s) < 8:
            continue
        forms = {s, urllib.parse.quote(s, safe="")}
        for raw in (s, "x-access-token:" + s):
            b = base64.b64encode(raw.encode("utf-8")).decode("ascii")
            forms.add(b)
            forms.add(b.rstrip("="))
            forms.add(base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii"))
        for f in sorted(forms, key=len, reverse=True):
            if f:
                t = t.replace(f, "[token]")
    t = _TOKENISH_RE.sub("[token]", t)
    return _BASIC_RE.sub(lambda m: m.group(1) + "[redacted]", t)


def short(text, limit=200, *secrets):
    """One scrubbed, printable line — the only form an upstream string leaves in."""
    t = scrub(text, *secrets)
    t = re.sub(r"[\x00-\x1f\x7f-\x9f  ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


_EXP_FORMATS = ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d")


def parse_expiry(value):
    """GitHub-Authentication-Token-Expiration -> unix ts (float) or None.

    Observed in practice as '2023-11-29 09:20:16 UTC' (and '... -0800'); not
    in GitHub's REST docs, so several shapes are accepted and anything else is
    None (the caller treats None as 'no expiry seen', a warning, never a pass
    on an expired token — GET /user's 401 catches that)."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    if v.endswith(" UTC") or v.endswith(" GMT"):
        v = v[:-4] + " +0000"
    for fmt in _EXP_FORMATS:
        try:
            d = _gha_dt.datetime.strptime(v, fmt)
        except ValueError:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=_gha_dt.timezone.utc)
        return d.timestamp()
    try:
        d = _gha_dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_gha_dt.timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def git_auth_env(token, git_base=GIT_DEFAULT):
    """The ONLY way the token reaches git: three env vars for the few seconds
    of one fetch/push (§2.1). Keyed to the git base's own prefix, so a remote
    on any other host never receives it. Never write these into a config
    file, a remote URL or argv. Empty dict for a non-https base (the test
    tier's file:// remote needs no credential)."""
    if not isinstance(token, str) or not token or not str(git_base).startswith("https://"):
        return {}
    b64 = base64.b64encode(("x-access-token:" + token).encode("utf-8")).decode("ascii")
    return {"GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.%s/.extraheader" % str(git_base).rstrip("/"),
            "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + b64}


def _is_loopback_url(url, schemes=("http",)):
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if u.scheme not in schemes or not u.hostname:
        return False
    h = u.hostname.lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def resolve_bases(env):
    """(api_base, git_base, overridden). HERMES_GITHUB_API / HERMES_GITHUB_GIT
    are honoured ONLY when HERMES_TESTS_OFFLINE=1 (the unit tier) and the
    value is loopback http (API) or file:// / loopback http (git). Anything
    else is ignored, so neither an env var nor a plist edit can point the
    token at another host."""
    api, git, over = API_DEFAULT, GIT_DEFAULT, False
    env = env or {}
    if env.get("HERMES_TESTS_OFFLINE") == "1":
        a = (env.get("HERMES_GITHUB_API") or "").strip().rstrip("/")
        if a and _is_loopback_url(a):
            api, over = a, True
        g = (env.get("HERMES_GITHUB_GIT") or "").strip().rstrip("/")
        if g and (g.startswith("file://") or _is_loopback_url(g)):
            git, over = g, True
    return api, git, over


def create_url(host="", date="", target_name=None):
    """The prefilled fine-grained token form (§1.2 step 2). There is no
    workflows/administration/actions/secrets/webhooks parameter, by design;
    metadata:read is added by GitHub itself."""
    h = re.sub(r"[^A-Za-z0-9-]", "", str(host or "").split(".")[0])[:21] or "Mac"
    name = "Hermes Assistant (%s)" % h
    desc = ("Hermes Assistant sandbox: pushes only approved changes to hermes/* "
            "branches and opens PRs.")
    if date:
        desc += " Created %s." % date
    q = [("name", name[:40]), ("description", desc), ("expires_in", "30"),
         ("contents", "write"), ("pull_requests", "write")]
    if target_name and _OWNER_RE.fullmatch(str(target_name)):
        q.append(("target_name", str(target_name)))
    return CREATE_PAGE + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Resp(object):
    """One response, reduced. `data` is parsed JSON or None; the raw body is
    never kept. `error`: None | 'network' | 'timeout' | 'rate_limited' |
    'redirect' | 'bad_json'."""
    __slots__ = ("status", "headers", "data", "error", "message")

    def __init__(self, status=0, headers=None, data=None, error=None, message=""):
        self.status = status
        self.headers = headers or {}
        self.data = data
        self.error = error
        self.message = message

    @property
    def accepted(self):
        return self.headers.get("x-accepted-github-permissions", "")[:160]

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def transient(self):
        """True when the status says nothing about the token's permissions."""
        return (self.status == 0 or self.status >= 500 or self.status == 429
                or self.error in ("network", "timeout", "rate_limited", "redirect")
                or 300 <= self.status < 400)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None          # urllib then raises HTTPError(3xx): fail closed


def _rate_limited(status, headers, message):
    if status == 429:
        return True
    if status == 403:
        if str(headers.get("x-ratelimit-remaining", "")).strip() == "0":
            return True
        if "rate limit" in (message or "").lower():
            return True
    return False


class GitHubAPI(object):
    """Minimal REST client. Never raises from request(); never follows a
    redirect; never sends Authorization when auth=False."""

    def __init__(self, base=API_DEFAULT, token=None, ssl_context=None, timeout=15):
        self.base = str(base or API_DEFAULT).rstrip("/")
        self._token = token
        self.timeout = timeout
        self.count = 0
        self.last_out = 0.0
        handlers = [_NoRedirect()]
        if self.base.startswith("https://"):
            handlers.append(urllib.request.HTTPSHandler(
                context=ssl_context or ssl.create_default_context()))
        else:
            handlers.append(urllib.request.ProxyHandler({}))   # loopback: no proxy
        self._opener = urllib.request.build_opener(*handlers)

    def __repr__(self):                     # never print the token
        return "<GitHubAPI %s%s>" % (self.base, " +token" if self._token else "")

    def scrub(self, text):
        return scrub(text, self._token or "")

    def request(self, method, path, query=None, body=None, auth=True, timeout=None):
        if not isinstance(path, str) or not path.startswith("/") or "://" in path \
                or any(c in path for c in "\r\n \t"):
            return Resp(0, error="network", message="bad path")
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": API_VERSION,
                   "User-Agent": USER_AGENT}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth and self._token:
            headers["Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        self.count += 1
        self.last_out = time.time()
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as r:
                status = r.status
                hdrs = {k.lower(): v for k, v in r.headers.items()}
                raw = r.read(MAX_BODY + 1)
        except urllib.error.HTTPError as e:
            status = e.code
            hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
            try:
                raw = e.read(MAX_BODY + 1)
            except Exception:
                raw = b""
            if 300 <= status < 400:
                return Resp(status, hdrs, None, "redirect", "redirect refused")
        except (TimeoutError, ssl.SSLError) as e:
            return Resp(0, error="timeout" if isinstance(e, TimeoutError) else "network",
                        message=short(type(e).__name__, 80))
        except Exception as e:                # URLError, socket errors, refusals
            reason = getattr(e, "reason", None)
            kind = "timeout" if "timed out" in str(reason or e).lower() else "network"
            return Resp(0, error=kind, message=short(type(e).__name__, 80))
        parsed, err = None, None
        if raw:
            if len(raw) > MAX_BODY:
                err = "bad_json"
            else:
                try:
                    parsed = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    err = "bad_json"
        msg = ""
        if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
            msg = self.scrub(parsed["message"])[:200]
        if _rate_limited(status, hdrs, msg):
            err = "rate_limited"
        return Resp(status, hdrs, parsed, err, short(msg, 200, self._token or ""))

    def get(self, path, query=None, auth=True):
        return self.request("GET", path, query=query, auth=auth)

    def post(self, path, body=None, auth=True):
        return self.request("POST", path, body=body if body is not None else {}, auth=auth)


def _rp(full_name):
    """'/repos/<o>/<r>' with each segment quoted (validated names only)."""
    o, n = full_name.split("/", 1)
    return "/repos/%s/%s" % (urllib.parse.quote(o, safe=""), urllib.parse.quote(n, safe=""))


def _refq(ref):
    return urllib.parse.quote(ref, safe="/")


# ---------------------------------------------------------------------------
# the scope wall
# ---------------------------------------------------------------------------
FORBIDDEN_PROBES = (
    # (id, path suffix, finding/line key, private only)
    ("administration", "/actions/permissions", "administration", False),
    ("secrets", "/actions/secrets", "secrets", False),
    ("variables", "/actions/variables", "secrets", False),
    ("webhooks", "/hooks", "webhooks", False),
    ("actions", "/actions/runs", "actions", True),
)


# ACCOUNT permissions that widen reach beyond the picked repositories (E50).
# A read answers 200 only when the key carries the permission (read or write).
ACCOUNT_PROBES = (
    # (id, path)
    ("git_ssh_keys", "/user/keys"),
    ("gpg_keys", "/user/gpg_keys"),
    ("ssh_signing_keys", "/user/ssh_signing_keys"),
)


def probe_account(api):
    """-> (findings, checks, retry). 200 = present = refuse; 403/404 = absent;
    401 = unauthorized (refuse, no retry); anything else = cannot tell =
    refuse with retry (fail closed). Reads only."""
    findings, checks, retry = [], {}, False
    for pid, path in ACCOUNT_PROBES:
        r = api.get(path, query={"per_page": "1"})
        if r.status == 200 and not r.error:
            checks[pid] = "present"
            findings.append(_finding(pid, "refuse", LINES[pid]))
        elif r.status in (403, 404) and r.error != "rate_limited":
            checks[pid] = "absent"
        elif r.status == 401:
            checks[pid] = "unknown"
            findings.append(_finding("unauthorized", "refuse", LINES["unauthorized"]))
            break
        else:
            checks[pid] = "unknown"
            retry = True
            findings.append(_finding("network", "refuse", LINES["network"],
                                     probe=pid, status=r.status))
            break
    return findings, checks, retry


def _plural_fix(line):
    return line.replace("reaches 1 private repositories", "reaches 1 private repository")


def _finding(fid, level, line, **kw):
    d = {"id": fid, "level": level, "line": line}
    d.update(kw)
    return d


def probe_forbidden(api, full_name, private):
    """Probes 8-12 for one repository -> (findings, checks, accepted, retry).

    200 = the permission is present = refuse. 403/404 = absent = pass.
    Anything else = cannot tell = refuse with retry (fail closed). Probe 12
    (Actions) runs on private repositories only: run listings are public on a
    public one, so Actions on a public repo is the documented residual."""
    findings, checks, accepted, retry = [], {}, {}, False
    for pid, suffix, key, priv_only in FORBIDDEN_PROBES:
        if priv_only and not private:
            checks[pid] = "public_unchecked"
            continue
        r = api.get(_rp(full_name) + suffix,
                    query={"per_page": "1"} if pid == "actions" else None)
        if r.accepted:
            accepted[pid] = r.accepted
        if r.status == 200 and not r.error:
            checks[pid] = "present"
            findings.append(_finding(pid, "refuse", LINES[key], repo=full_name))
        elif r.status in (403, 404) and r.error != "rate_limited":
            checks[pid] = "absent"
        elif r.status == 401:
            checks[pid] = "unknown"
            findings.append(_finding("unauthorized", "refuse", LINES["unauthorized"]))
            retry = False
            break
        else:
            checks[pid] = "unknown"
            retry = True
            findings.append(_finding("network", "refuse", LINES["network"],
                                     probe=pid, repo=full_name, status=r.status))
            break
    return findings, checks, accepted, retry


def _dedupe(findings):
    out, seen = [], set()
    for f in findings:
        k = (f.get("id"), f.get("line"), f.get("level"))   # a warn must not hide its refusal
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def _repo_meta(api, full_name):
    """(meta dict | None, Resp)."""
    r = api.get(_rp(full_name))
    if r.status == 200 and isinstance(r.data, dict):
        d = r.data
        return ({"full_name": normalize_repo(str(d.get("full_name") or full_name)) or full_name,
                 "private": bool(d.get("private")),
                 "default_branch": (str(d.get("default_branch") or "main")
                                    if SAFE_BRANCH_RE.fullmatch(str(d.get("default_branch")
                                                                    or "main")) else None),
                 "size_kb": int(d.get("size") or 0) if str(d.get("size") or "0").isdigit() else 0,
                 "archived": bool(d.get("archived")),
                 "fork": bool(d.get("fork")),
                 "id": _repo_id(d.get("id")),
                 "owner_type": str(((d.get("owner") or {}).get("type")) or "")[:20]}, r)
    return None, r


def _repo_id(v):
    """GitHub's numeric repository id, or None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and 0 < v < 10 ** 15:
        return v
    if isinstance(v, str) and re.fullmatch(r"[1-9][0-9]{0,14}", v):
        return int(v)
    return None


_MOVED_RE = re.compile(r"/repositories/([1-9][0-9]{0,14})/?$")


def is_moved(resp):
    """True when GitHub answered a repository read with a redirect: the
    repository was renamed or transferred (E07). The redirect is NEVER
    followed; this only reads what the answer says."""
    return resp is not None and 300 <= int(resp.status or 0) < 400


def moved_to_id(resp):
    """The repository id a redirect points at (Location or the body's url,
    `/repositories/<id>`), or None. Read, never requested."""
    if not is_moved(resp):
        return None
    cands = [str((resp.headers or {}).get("location") or "")]
    if isinstance(resp.data, dict):
        cands.append(str(resp.data.get("url") or ""))
    for c in cands:
        m = _MOVED_RE.search(c.split("?", 1)[0])
        if m:
            return int(m.group(1))
    return None


def _public_repos(api, owner):
    """(list | None, Resp): an account's public repositories. Asked without the
    key first (the design's choice: nothing about the key is needed); when that
    is throttled (60 an hour per address) or flaky, asked once WITH the key, so
    a shared address cannot make the wall unrunnable."""
    path = "/users/%s/repos" % urllib.parse.quote(owner, safe="")
    q = {"per_page": "100", "sort": "pushed"}
    r = api.get(path, query=q, auth=False)
    if r.status != 200 and (r.transient or r.status in (401, 403)):
        r = api.get(path, query=q, auth=True)
    if r.status == 200 and isinstance(r.data, list) and not r.error:
        return r.data, r
    return None, r


def probe_repo(api, full):
    """Probes 6-12 for ONE repository (also used when the owner adds one).
    -> (row, findings, stop); stop is None | 'retry' | 'unauthorized'.
    A missing Contents write / Pull requests permission is a per-repository
    WARNING (the repository cannot be enabled); a forbidden permission is a
    REFUSAL of the whole token."""
    findings = []
    row = {"full_name": full, "listed": True, "private": None,
           "default_branch": None, "size_kb": 0, "archived": False,
           "writable": False, "pulls": None, "enable_blocked": None,
           "checks": {}, "accepted": {}}
    unauth = _finding("unauthorized", "refuse", LINES["unauthorized"])
    meta, r = _repo_meta(api, full)
    if meta is None:
        if r.status == 401:
            return row, [unauth], "unauthorized"
        if is_moved(r):
            # renamed or transferred (E07): a fact about this one repository,
            # not a network blip and not a token problem
            row["moved"] = {"to_id": moved_to_id(r)}
            row["enable_blocked"] = LINES["moved"]
            return row, [_finding("moved", "warn", "%s: %s" % (full, LINES["moved"]),
                                  repo=full)], None
        if r.transient or r.error:
            return row, [_finding("network", "refuse", LINES["network"], probe="repo",
                                  repo=full, status=r.status)], "retry"
        row["enable_blocked"] = LINES["no_contents"].format(repo=full)
        return row, [_finding("no_contents", "warn", row["enable_blocked"], repo=full)], None
    row.update(meta)
    name = row["full_name"]
    if not meta["default_branch"]:
        row["enable_blocked"] = ("%s's default branch has a name Hermes does not handle, so "
                                 "it will not work there." % name)
    # 6. contents write: an unreferenced blob (invisible, no event, GC'd)
    r = api.post(_rp(name) + "/git/blobs", {"content": PROBE_BLOB, "encoding": "utf-8"})
    if r.accepted:
        row["accepted"]["contents"] = r.accepted
    if r.status == 201 and not r.error:
        row["writable"] = True
    elif r.status in (403, 404) and r.error != "rate_limited":
        row["enable_blocked"] = LINES["no_contents"].format(repo=name)
        findings.append(_finding("no_contents", "warn", row["enable_blocked"], repo=name))
    elif r.status == 409 and not r.error:
        # an EMPTY repository: GitHub's Git database API answers 409 ("Git
        # Repository is empty"). There is nothing to test and nothing to push
        # on top of yet, which is the owner's to fix, not a failed check
        row["empty"] = True
        row["enable_blocked"] = LINES["empty_repo"].format(repo=name)
        findings.append(_finding("empty_repo", "warn", row["enable_blocked"], repo=name))
    elif r.status == 401:
        return row, [unauth], "unauthorized"
    else:
        return row, [_finding("network", "refuse", LINES["network"], probe="contents",
                              repo=name, status=r.status)], "retry"
    if meta["archived"] and not row["enable_blocked"]:
        row["enable_blocked"] = "%s is archived on GitHub." % name
    # 7. pull requests (read; write itself is verified at the first PR)
    r = api.get(_rp(name) + "/pulls", query={"per_page": "1"})
    if r.accepted:
        row["accepted"]["pulls"] = r.accepted
    if r.status == 200 and not r.error:
        row["pulls"] = True
    elif r.status in (403, 404) and r.error != "rate_limited":
        row["pulls"] = False
        findings.append(_finding("no_pulls", "warn", LINES["no_pulls"].format(repo=name),
                                 repo=name))
    elif r.status == 401:
        return row, [unauth], "unauthorized"
    else:
        return row, [_finding("network", "refuse", LINES["network"], probe="pulls",
                              repo=name, status=r.status)], "retry"
    # 8-12
    f, c, acc, retry = probe_forbidden(api, name, meta["private"])
    row["checks"] = c
    row["accepted"].update(acc)
    findings.extend(f)
    if any(x["id"] == "unauthorized" for x in f):
        return row, findings, "unauthorized"
    return row, findings, ("retry" if retry else None)


def probe(api, repos=None, now=None, known=None):
    """The scope wall (§1.4). `api` carries the candidate token. `repos` is
    the owner's list ("owner/repo" strings, already validated) or empty.

    Returns a dict; the caller stores the token ONLY when result["ok"]:
      ok, refused, retry, kind, login, user_id, expires_at, no_expiry,
      findings [{id, level: refuse|warn|info, line, repo?}],
      repos [{full_name, private, default_branch, size_kb, archived, listed,
              writable, pulls, enable_blocked, checks{}, accepted{}}],
      checks {contents_write, pull_requests, administration, secrets,
              variables, webhooks, actions, workflows, write_reach},
      requests (count), final (FINAL_LINE when refused and not retry)

    `known` ({"names": {lower full names}, "ids": {repository ids}}, optional):
    private repositories the owner connected EARLIER (removed or renamed
    since). Re-adding with the same key does not call them "unlisted"; they
    are not probed and do not count as listed. connect never passes it.
    """
    now = time.time() if now is None else now
    tok = getattr(api, "_token", None)
    kind = classify_token(tok)
    res = {"ok": False, "refused": True, "retry": False, "kind": kind,
           "login": None, "user_id": None, "expires_at": None, "no_expiry": False,
           "findings": [], "repos": [], "checks": {}, "requests": 0, "final": None}

    def done(retry=False):
        res["findings"] = _dedupe(res["findings"])
        refuse = [f for f in res["findings"] if f["level"] == "refuse"]
        res["ok"] = not refuse
        res["refused"] = bool(refuse)
        res["retry"] = bool(refuse) and bool(retry)
        # the "delete this token" line follows every refusal except "try
        # again" (nothing is wrong with the token) and "list your repos"
        res["final"] = (FINAL_LINE if refuse and not res["retry"]
                        and not any(f["id"] in ("no_repos", "empty_repo") for f in refuse)
                        else None)
        res["requests"] = getattr(api, "count", 0)
        return res

    # 1. kind: refused before any network call
    if kind != "fine_grained_pat":
        res["findings"].append(_finding("kind", "refuse", kind_line(kind)))
        return done()

    # 2-3. identity + expiry
    r = api.get("/user")
    if r.status == 401:
        res["findings"].append(_finding("unauthorized", "refuse", LINES["unauthorized"]))
        return done()
    if r.status != 200 or not isinstance(r.data, dict) or r.error:
        res["findings"].append(_finding("network", "refuse", LINES["network"],
                                        probe="user", status=r.status))
        return done(retry=True)
    login = str(r.data.get("login") or "")
    if not _OWNER_RE.fullmatch(login):
        res["findings"].append(_finding("network", "refuse", LINES["network"], probe="user"))
        return done(retry=True)
    res["login"] = login
    try:
        res["user_id"] = int(r.data.get("id"))
    except (TypeError, ValueError):
        res["user_id"] = None
    exp = parse_expiry(r.headers.get("github-authentication-token-expiration", ""))
    if exp is not None and exp <= now:
        res["findings"].append(_finding("unauthorized", "refuse", LINES["unauthorized"]))
        return done()
    res["expires_at"] = exp
    if exp is None:
        res["no_expiry"] = True
        res["findings"].append(_finding("no_expiry", "warn", LINES["no_expiry"]))

    # 3b. account permissions that widen reach (E50)
    f, c, retry = probe_account(api)
    res["checks"].update(c)
    if f:
        res["findings"].extend(f)
        return done(retry=retry)

    listed = list(repos or [])
    listed_l = {x.lower() for x in listed}

    known = known if isinstance(known, dict) else {}
    known_names = {str(x).lower() for x in known.get("names") or ()}
    known_ids = {x for x in known.get("ids") or () if isinstance(x, int)}

    # 4. private reach
    private_seen, more = [], False
    seen_ids = {}
    for page in range(1, MAX_PAGES + 1):
        r = api.get("/user/repos", query={"visibility": "private", "per_page": "100",
                                          "page": str(page)})
        if r.status == 401:
            res["findings"].append(_finding("unauthorized", "refuse", LINES["unauthorized"]))
            return done()
        if r.status != 200 or not isinstance(r.data, list) or r.error:
            res["findings"].append(_finding("network", "refuse", LINES["network"],
                                            probe="private_reach", status=r.status))
            return done(retry=True)
        for it in r.data:
            if isinstance(it, dict) and it.get("private"):
                fn = normalize_repo(str(it.get("full_name") or ""))
                if fn and fn.lower() not in {x.lower() for x in private_seen}:
                    private_seen.append(fn)
                    seen_ids[fn.lower()] = _repo_id(it.get("id"))
        if len(r.data) < 100:
            break
        if page == MAX_PAGES:
            more = True
    if more or len(private_seen) > GH_MAX_REPOS:
        res["findings"].append(_finding("too_many", "refuse", LINES["too_many"]))
        return done()
    if listed:
        extra = [p for p in private_seen if p.lower() not in listed_l
                 and p.lower() not in known_names
                 and not (seen_ids.get(p.lower()) and seen_ids[p.lower()] in known_ids)]
        if extra:
            res["findings"].append(_finding(
                "unlisted_private", "refuse",
                _plural_fix(LINES["unlisted_private"].format(
                    n=len(extra), first=", ".join(extra[:3]) + ("…" if len(extra) > 3 else "")))))
            return done()
    else:
        listed = list(private_seen)
        listed_l = {x.lower() for x in listed}
        if not listed:
            res["findings"].append(_finding("no_repos", "refuse", LINES["no_repos"]))
            return done()
    if len(set(listed_l) | {p.lower() for p in private_seen}) > GH_MAX_REPOS:
        res["findings"].append(_finding("too_many", "refuse", LINES["too_many"]))
        return done()

    # 5. write reach beyond the list: one PUBLIC repo per owner (the token's
    # login + the owners of listed repos, at most two), probed with an
    # unreferenced blob. 201 means "All repositories". Stored values (E08):
    # "sampled" (a sampled repository refused the write), "unchecked" (nothing
    # to sample: also a warning); "all_repositories" is a refusal, never
    # stored. Older stores may hold "contained" (= "sampled").
    owners = [login] + [x.split("/")[0] for x in listed]
    seen_o, cand_owners = set(), []
    for o in owners:
        if o.lower() not in seen_o:
            seen_o.add(o.lower())
            cand_owners.append(o)
    write_reach = "unchecked"
    for o in cand_owners[:2]:
        items, r = _public_repos(api, o)
        if items is None:
            if r.status == 401:
                res["findings"].append(_finding("unauthorized", "refuse", LINES["unauthorized"]))
                return done()
            if r.status == 404 and not r.error:
                continue         # no public listing for this account: nothing to probe
            # throttled, flaky or redirected: without the listing the "All
            # repositories" check cannot run, and an unchecked wall is not a pass
            res["findings"].append(_finding("network", "refuse", LINES["network"],
                                            probe="public_listing", status=r.status))
            return done(retry=True)
        tried = 0
        for it in items:
            if tried >= 3:
                break
            if not isinstance(it, dict) or it.get("private") or it.get("archived"):
                continue
            cand = normalize_repo(str(it.get("full_name") or ""))
            if not cand or cand.lower() in listed_l:
                continue
            tried += 1
            r = api.post(_rp(cand) + "/git/blobs", {"content": PROBE_BLOB, "encoding": "utf-8"})
            if r.status == 201 and not r.error:
                res["findings"].append(_finding("unlisted_write", "refuse",
                                                LINES["unlisted_write"].format(repo=cand)))
                res["checks"]["write_reach"] = "all_repositories"
                return done()
            if r.status in (403, 404) and r.error != "rate_limited":
                # one refused write: the key is not "All repositories", but
                # that is a SAMPLE, not a proof of containment (E08)
                write_reach = "sampled"
                break
            if r.status == 409 and not r.error:
                continue         # an empty repository tells nothing: try the next one
            if r.status == 401:
                res["findings"].append(_finding("unauthorized", "refuse", LINES["unauthorized"]))
                return done()
            res["findings"].append(_finding("network", "refuse", LINES["network"],
                                            probe="write_reach", status=r.status))
            return done(retry=True)
    res["checks"]["write_reach"] = write_reach
    if write_reach == "unchecked":
        # nothing to sample: stored (the wall is sample-based by design), but
        # said out loud, never as a silent pass (E08)
        res["findings"].append(_finding("write_reach_unchecked", "warn",
                                        LINES["write_reach_unchecked"]))

    # 6-12, per listed repository
    agg = {"administration": "absent", "secrets": "absent", "variables": "absent",
           "webhooks": "absent", "actions": "absent"}
    writable_n = pulls_missing = 0
    any_private = False
    for full in listed:
        row, f, stop = probe_repo(api, full)
        res["repos"].append(row)
        res["findings"].extend(f)
        if stop == "unauthorized":
            return done()
        if stop == "retry":
            return done(retry=True)
        any_private = any_private or bool(row["private"])
        writable_n += 1 if row["writable"] else 0
        pulls_missing += 1 if row["pulls"] is False else 0
        for k, v in row["checks"].items():
            if v == "present":
                agg[k] = "present"
        if any(x["level"] == "refuse" for x in f):
            return done()
    if not any_private:
        agg["actions"] = "public_unchecked"
    res["checks"].update(agg)
    res["checks"]["contents_write"] = ("verified" if writable_n == len(listed) else
                                       "partial" if writable_n else "missing")
    res["checks"]["pull_requests"] = ("ok" if not pulls_missing else
                                      "partial" if pulls_missing < len(listed) else "missing")
    res["checks"]["workflows"] = "not_observable"
    if writable_n == 0:
        empties = [r["full_name"] for r in res["repos"] if r.get("empty")]
        if empties:
            res["findings"].append(_finding("empty_repo", "refuse",
                                            LINES["empty_repo"].format(repo=empties[0]),
                                            repo=empties[0]))
        else:
            res["findings"].append(_finding("no_writable", "refuse", LINES["no_writable"]))
    return done()


# ---------------------------------------------------------------------------
# branch protection (§3.6)
# ---------------------------------------------------------------------------
def protection_summary(branch, branch_resp, rules_resp):
    """-> {state: protected|not_protected|unknown, classic, rules[],
           blocks_force_push, blocks_deletion, requires_pr, blocks_update, text,
           gap_text}. A 403 on the rules endpoint is 'unknown', never protected."""
    b = str(branch or "main")
    out = {"state": "unknown", "classic": None, "rules": [],
           "blocks_force_push": False, "blocks_deletion": False,
           "requires_pr": False, "blocks_update": False, "text": "", "gap_text": ""}
    if branch_resp is not None and branch_resp.status == 200 and isinstance(branch_resp.data, dict):
        out["classic"] = bool(branch_resp.data.get("protected"))
    rules_known = False
    if rules_resp is not None and rules_resp.status == 200 and isinstance(rules_resp.data, list):
        rules_known = True
        types = []
        for it in rules_resp.data:
            if isinstance(it, dict) and isinstance(it.get("type"), str) and \
                    re.fullmatch(r"[a-z_]{1,40}", it["type"]):
                t = it["type"]
                if t not in types:
                    types.append(t)
        out["rules"] = types
        out["blocks_force_push"] = "non_fast_forward" in types
        out["blocks_deletion"] = "deletion" in types
        out["requires_pr"] = "pull_request" in types
        out["blocks_update"] = "update" in types
    strong = (out["blocks_force_push"] or out["blocks_deletion"] or out["requires_pr"]
              or out["blocks_update"])
    if strong:
        out["state"] = "protected"
        bits = []
        if out["blocks_force_push"]:
            bits.append("blocks force-push")
        if out["blocks_deletion"]:
            bits.append("blocks deletion")
        if out["requires_pr"]:
            bits.append("requires PR")
        if out["blocks_update"]:
            bits.append("blocks direct pushes")
        out["text"] = "%s protected: %s" % (b, " · ".join(bits))
    elif out["classic"]:
        out["state"] = "protected"
        out["text"] = ("%s protected by a classic branch rule (its details need "
                       "admin access to read)" % b)
    elif rules_known and out["classic"] is False:
        out["state"] = "not_protected"
        out["text"] = "%s not protected" % b
        out["gap_text"] = UNPROTECTED_TEXT.format(b=b)
    else:
        out["state"] = "unknown"
        out["text"] = "%s protection could not be read" % b
    return out


# ---------------------------------------------------------------------------
# CI exposure (owner decision: a repository whose CI runs with secrets on a
# push to hermes/* is refused unless its workflows ignore hermes/**)
# ---------------------------------------------------------------------------
# Events a push of hermes/<x> reaches: `push`, `create` (branch creation) and
# `workflow_run` (it fires when ANY workflow finishes on that branch, with the
# default branch's secrets, and is the classic "check out the branch's code"
# pattern; its own `branches`/`branches-ignore` filter names the TRIGGERING
# branch). check_suite/check_run/status are left out on purpose: they run the
# default branch's workflow file and give a hermes/ push no way to supply code.
_PUSH_EVENTS = ("push", "create", "workflow_run")
_PR_EVENTS = ("pull_request", "pull_request_target")
_KNOWN_EVENTS = _PUSH_EVENTS + _PR_EVENTS + ("workflow_dispatch", "schedule",
                                             "workflow_run", "workflow_call",
                                             "release", "issues", "issue_comment")
_ON_RE = re.compile(r"""^(?:on|"on"|'on'|true)\s*:(.*)$""")
_MAPKEY_RE = re.compile(r"""^["']?([A-Za-z_][A-Za-z0-9_-]*)["']?\s*:(.*)$""")
_FILTER_KEYS = ("branches", "branches-ignore", "tags", "tags-ignore", "paths",
                "paths-ignore", "types")
_SECRET_RE = re.compile(r"secrets\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)|secrets\s*\[\s*['\"]([^'\"]{1,100})['\"]")
# secrets[matrix.name] / secrets[format(...)]: a dynamic lookup reads SOME secret
_DYN_SECRET_RE = re.compile(r"secrets\s*\[\s*[^'\"\s\]]")
# github.token IS secrets.GITHUB_TOKEN (`env: GH_TOKEN: ${{ github.token }}`)
_GHTOKEN_RE = re.compile(r"\bgithub\s*\.\s*token\b")
_INHERIT_RE = re.compile(r"^\s*secrets\s*:\s*inherit\b", re.M)
_TOJSON_RE = re.compile(r"toJSON\(\s*secrets\s*\)", re.I)
# Branch names a filter is tried against. Slugs are ^[a-z0-9][a-z0-9-]{1,48}$,
# so one sample per possible first character covers `branches-ignore:
# ['hermes/a*']`-style filters that only exclude part of the namespace.
HERMES_SAMPLES = ("hermes/x", "hermes/fix-login-copy", "hermes/a1") + tuple(
    "hermes/%sx" % c for c in "abcdefghijklmnopqrstuvwxyz0123456789")
_EVENT_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,39}")


def _strip_comment(s):
    out, q = [], None
    for ch in s:
        if q:
            out.append(ch)
            if ch == q:
                q = None
            continue
        if ch in ("'", '"'):
            q = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def _indent(ln):
    return len(ln) - len(ln.lstrip(" \t"))


def _flow_items(v):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    return [x.strip().strip("'\"") for x in v.split(",") if x.strip()]


def _glob_re(pat):
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c in "?+":
            out.append(c)
        elif c == "[":
            j = pat.find("]", i)
            if j > i:
                out.append(pat[i:j + 1])
                i = j + 1
                continue
            out.append(re.escape(c))
        else:
            out.append(re.escape(c))
        i += 1
    try:
        return re.compile("^" + "".join(out) + "$")
    except re.error:
        return None


def _filter_matches(patterns, name):
    """GitHub filter semantics: patterns in order, a leading ! excludes."""
    matched = False
    for p in patterns:
        neg = p.startswith("!")
        rx = _glob_re(p[1:] if neg else p)
        if rx is None:
            continue
        if rx.match(name):
            matched = not neg
    return matched


def _workflow_events(text):
    """{event: {filter_key: [patterns]}} from a workflow's top-level `on:`.
    A text heuristic, labelled as one: no YAML parser in a stdlib dashboard.
    Unknown shapes err towards 'triggered, no filter' (exposed)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, raw in enumerate(lines):
        if not raw or raw[0] in " \t#-":
            continue
        m = _ON_RE.match(_strip_comment(raw))
        if not m:
            continue
        inline = m.group(1).strip()
        if inline:
            if inline.startswith("{"):
                return {n: {} for n in re.findall(r"[A-Za-z_]+", inline) if n in _KNOWN_EVENTS}
            names = [n for n in _flow_items(inline) if n]
            if not names or not all(_EVENT_NAME_RE.fullmatch(n) for n in names):
                # an alias (`on: *triggers`), a tag, prose: could not tell. The
                # name also reaches the agent through /status, so it is never
                # carried: {} makes ci_exposure treat the workflow as triggered.
                return {}
            return {n: {} for n in names}
        block = []
        for ln in lines[i + 1:]:
            s = _strip_comment(ln)
            if not s.strip():
                continue
            if ln[0] not in " \t":
                break
            block.append(s)
        if not block:
            return {}
        base = min(_indent(b) for b in block)
        events, cur, cur_ind = {}, None, None
        filt_key, filt_ind = None, None
        weird = False       # a top-level line that is neither `event:` nor `- event`
        for s in block:
            ind = _indent(s)
            st = s.lstrip()
            is_item = st.startswith("-")
            if ind == base:
                filt_key = None
                if is_item:                       # on: [- push, - pull_request]
                    name = st[1:].strip().strip("'\"")
                    if _EVENT_NAME_RE.fullmatch(name):
                        events[name] = {}
                    else:
                        weird = True
                    cur = None
                    continue
                m2 = _MAPKEY_RE.match(st)
                if not m2 or not _EVENT_NAME_RE.fullmatch(m2.group(1)):
                    weird = True                  # `<<: *base`, `*alias`, a huge key
                    cur = None
                    continue
                cur, cur_ind = m2.group(1), ind
                events[cur] = {}
                continue
            if cur is None:
                continue
            if is_item:
                # YAML lets a block sequence sit at its key's own indentation
                if filt_key and ind >= filt_ind:
                    item = st[1:].strip().strip("'\"")
                    if item:
                        events[cur].setdefault(filt_key, []).append(item)
                continue
            m2 = _MAPKEY_RE.match(st)
            if not m2:
                continue
            name, rest = m2.group(1), m2.group(2).strip()
            if name in _FILTER_KEYS and ind > cur_ind:
                filt_key, filt_ind = name, ind
                events[cur].setdefault(name, [])
                if rest:
                    events[cur][name].extend(_flow_items(rest))
            else:
                filt_key = None
        # a shape this scanner cannot read means events it did not see: say so
        # (the caller reads {} as "could not tell", i.e. triggered)
        return {} if weird else events
    return {}


def _literal_prefix(pat):
    """The part of a glob before its first wildcard character."""
    for i, ch in enumerate(pat):
        if ch in "*?+[":
            return pat[:i]
    return pat


def _push_exposed(filters):
    br = filters.get("branches")
    ign = filters.get("branches-ignore")
    if br is not None and len(br) > 0:
        if any(_filter_matches(br, s) for s in HERMES_SAMPLES):
            return True
        # a filter that names part of the hermes/ namespace (`hermes/release-*`)
        # runs for the slugs it matches, which the agent chooses: exposed,
        # unless a later `!` pattern excludes the whole namespace
        names_hermes = any(not p.startswith("!") and _literal_prefix(p).startswith("hermes/")
                           for p in br)
        excluded = any(p.startswith("!") and _glob_re(p[1:]) is not None and all(
            _glob_re(p[1:]).match(x) for x in HERMES_SAMPLES) for p in br)
        return bool(names_hermes and not excluded)
    if ign is not None and len(ign) > 0:
        return not all(_filter_matches(ign, s) for s in HERMES_SAMPLES)
    if filters.get("tags") or filters.get("tags-ignore"):
        return False        # a tags-only push filter never runs for a branch push
    return True


def workflow_secrets(text):
    names = []
    for a, b in _SECRET_RE.findall(text or ""):
        # a repository's workflow is third-party text and the name reaches
        # the agent: keep identifier characters only
        n = re.sub(r"[^A-Za-z0-9_.-]", "_", (a or b))[:60]
        if n and n not in names:
            names.append(n)
    if _DYN_SECRET_RE.search(text or "") and "(dynamic)" not in names:
        names.append("(dynamic)")
    if _GHTOKEN_RE.search(text or "") and "GITHUB_TOKEN" not in names:
        names.append("GITHUB_TOKEN")
    if _INHERIT_RE.search(text or "") and "inherit" not in names:
        names.append("(inherit)")
    if _TOJSON_RE.search(text or "") and "(all)" not in names:
        names.append("(all)")
    return names


def ci_exposure(files):
    """files: {path: workflow text}. -> {exposed, pr_exposed, workflows[...],
    heuristic: True}. `exposed` = some workflow that references secrets would
    run on a push (or branch creation) of hermes/<x>; `pr_exposed` = some
    such workflow runs on pull_request(_target) — a warning, because a draft
    PR fires those events too."""
    out = {"exposed": False, "pr_exposed": False, "workflows": [], "heuristic": True}
    for path in sorted(files or {}):
        text = files[path] if isinstance(files[path], str) else ""
        ev = _workflow_events(text)
        sec = workflow_secrets(text)
        # every real workflow has triggers: none found means "could not tell",
        # and that counts as triggered (fail closed)
        unparsed = not ev and bool(text.strip())
        push = unparsed or any(e in ev and (e == "create" or _push_exposed(ev[e]))
                               for e in _PUSH_EVENTS)
        pr = unparsed or any(e in ev for e in _PR_EVENTS)
        row = {"path": str(path)[:200], "events": sorted(ev)[:12], "secrets": sec[:12],
               "push_exposed": bool(push and sec), "pr_exposed": bool(pr and sec),
               "unparsed": unparsed}
        out["workflows"].append(row)
        out["exposed"] = out["exposed"] or row["push_exposed"]
        out["pr_exposed"] = out["pr_exposed"] or row["pr_exposed"]
    return out


_WORKFLOW_PATH_RE = re.compile(r"^\.github/workflows(?:/|$)", re.IGNORECASE)


def touches_workflows(paths):
    """The C-workflow rule in one place: every path (of a change) under
    .github/workflows/. The token has no Workflows permission and Hermes
    never asks for it; GitHub would also reject such a push. Matched
    case-insensitively (a case-insensitive Mac can hold either spelling),
    after normalising './' prefixes and backslashes."""
    out = []
    for p in paths or []:
        if not isinstance(p, str):
            continue
        q = p.replace("\\", "/")
        while q.startswith("./"):
            q = q[2:]
        q = q.lstrip("/")
        if _WORKFLOW_PATH_RE.match(q):
            out.append(p)
    return out


CI_FIX = ("Add `branches-ignore: ['hermes/**']` under `push:` (and under "
          "`workflow_run:` if it has one) in {paths} (or `- '!hermes/**'` as the "
          "last `branches:` entry), so a hermes/ branch never runs a workflow that "
          "can read your secrets. Then press Check.")


def fetch_workflows(api, full_name, ref=None, cap=30):
    """({path: text}, error|None) for .github/workflows/*.y(a)ml via the
    Contents API (Contents: read). No directory = {} (nothing runs)."""
    q = {"ref": ref} if ref else None
    r = api.get(_rp(full_name) + "/contents/.github/workflows", query=q)
    if r.status == 404 and not r.error:
        return {}, None
    if r.error == "rate_limited":
        return None, "rate limited"          # says nothing about the workflows (E52)
    if r.status != 200 or not isinstance(r.data, list):
        return None, "status %s" % r.status
    files = {}
    wf = [it for it in r.data if isinstance(it, dict) and it.get("type") == "file"
          and re.search(r"\.ya?ml$", str(it.get("name") or ""), re.IGNORECASE)]
    if len(wf) > cap:
        return None, "more than %d workflow files" % cap        # fail closed, never skip
    for it in wf:
        name = str(it.get("name") or "")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}\.ya?ml", name):
            return None, "a workflow file name Hermes does not handle"
        path = ".github/workflows/" + name
        f = api.get(_rp(full_name) + "/contents/" + _refq(path), query=q)
        if f.error == "rate_limited":
            return None, "rate limited"
        if f.status != 200 or not isinstance(f.data, dict):
            return None, "status %s" % f.status
        content = f.data.get("content")
        if f.data.get("encoding") != "base64" or not isinstance(content, str):
            return None, "unreadable %s" % name          # > 1 MB: fail closed
        try:
            files[path] = base64.b64decode(content).decode("utf-8", "replace")
        except Exception:
            return None, "undecodable %s" % name
    return files, None


def list_hermes_heads(api, full_name):
    """({branch: sha}, error|None) — every ref under refs/heads/hermes/."""
    heads = {}
    for page in range(1, MAX_PAGES + 1):
        r = api.get(_rp(full_name) + "/git/matching-refs/heads/hermes/",
                    query={"per_page": "100", "page": str(page)})
        if r.status in (404, 409) and not r.error:       # 409: an empty repository
            return heads, None
        if r.status != 200 or not isinstance(r.data, list):
            return None, "status %s" % r.status
        for it in r.data:
            if not isinstance(it, dict):
                continue
            ref = str(it.get("ref") or "")
            sha = str(((it.get("object") or {}).get("sha")) or "")
            if ref.startswith("refs/heads/hermes/") and re.fullmatch(r"[0-9a-f]{40}", sha):
                name = ref[len("refs/heads/"):]
                if not SAFE_BRANCH_RE.fullmatch(name):
                    # deterministic stand-in: the same odd name maps to the
                    # same key every check, and never matches a ledger slug
                    import hashlib
                    name = "hermes/(unusual-name-%s)" % hashlib.sha256(
                        name.encode("utf-8", "replace")).hexdigest()[:10]
                heads[name] = sha
        if len(r.data) < 100:
            break
        if page == MAX_PAGES:
            # more than 300 refs: a partial list would let a foreign branch hide
            # in the rest and would "resolve" drift that is merely off the page
            return None, "more than %d hermes/ branches" % (MAX_PAGES * 100)
    return heads, None


def repo_protection(api, full_name, branch):
    """protection_summary() for one repository's default branch (2 reads)."""
    br = api.get(_rp(full_name) + "/branches/" + _refq(branch))
    ru = api.get(_rp(full_name) + "/rules/branches/" + _refq(branch))
    return protection_summary(branch, br, ru)


def check_repo(api, full_name):
    """The 6-hourly read-only check for one repository (§3.6). Reads only.
    -> {ok, error, meta, protection, forbidden[], checks{}, accepted{},
        heads{branch: sha} | None, ci{} | None, unauthorized}"""
    out = {"ok": False, "error": None, "meta": None, "protection": None,
           "forbidden": [], "checks": {}, "accepted": {}, "heads": None,
           "ci": None, "ci_error": None, "ci_unreadable": False, "unauthorized": False}
    meta, r = _repo_meta(api, full_name)
    if meta is None and is_moved(r):
        # renamed or transferred (E07): never followed, never an error that
        # keeps the check failing; the connector marks that one row
        out["moved"] = True
        out["moved_to_id"] = moved_to_id(r)
        out["error"] = "renamed or transferred on GitHub (status %s)" % r.status
        return out
    if meta is None:
        out["unauthorized"] = r.status == 401
        out["error"] = "repository unreachable (status %s)" % r.status
        out["gone"] = r.status in (403, 404) and not r.transient
        return out
    out["meta"] = meta
    b = meta["default_branch"]
    # a default branch with a name Hermes does not carry (None) cannot be put in
    # a request path: that one repository must not abort the whole check
    out["protection"] = (repo_protection(api, full_name, b) if b else
                         protection_summary("default branch", None, None))
    f, c, acc, retry = probe_forbidden(api, full_name, meta["private"])
    out["forbidden"] = [x for x in f if x["id"] not in ("network",)]
    out["checks"] = c
    out["accepted"] = acc
    if any(x["id"] == "unauthorized" for x in f):
        out["unauthorized"] = True
    heads, herr = list_hermes_heads(api, full_name)
    out["heads"] = heads
    files, werr = fetch_workflows(api, full_name, b)
    out["ci"] = ci_exposure(files) if files is not None else None
    # why the scan could not run: a 5xx / timeout / throttle says nothing about
    # the workflows (keep the last verdict), but too many files, an odd name, a
    # file GitHub will not inline or a 401/403 are facts about the repository
    # and fail CLOSED, exactly as at connect
    out["ci_error"] = werr
    out["ci_unreadable"] = bool(werr) and not re.match(
        r"(status (0|429|5\d\d)\b|rate limited)", werr)
    errs = [e for e in (herr, werr) if e] + (["probe: cannot tell"] if retry else [])
    out["error"] = "; ".join(errs) if errs else None
    out["ok"] = not errs
    return out


# ---------------------------------------------------------------------------
# revoke (§1.5)
# ---------------------------------------------------------------------------
def revoke(base, token, ssl_context=None, timeout=15):
    """POST <api>/credentials/revoke {"credentials":[token]} WITHOUT an
    Authorization header (authenticated calls get 403). 202 = accepted.
    -> (ok, status). Irreversible; GitHub notifies the owner."""
    if classify_token(token) == "empty":
        return False, 0
    api = GitHubAPI(base=base, token=None, ssl_context=ssl_context, timeout=timeout)
    r = api.request("POST", "/credentials/revoke", body={"credentials": [token]}, auth=False)
    return (r.status == 202), r.status
