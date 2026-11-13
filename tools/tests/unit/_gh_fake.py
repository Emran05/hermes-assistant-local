"""_gh_fake.py — a fake GitHub REST API on a loopback port the test owns.

docs/plans/github-sandbox-2026-10.md §7: the GitHub sandbox suites NEVER talk
to real GitHub. This is a ThreadingHTTPServer on 127.0.0.1:0 implementing the
subset of the REST API the connector and the change pipeline use, with
per-token PERMISSION PROFILES that answer the way GitHub does for a
fine-grained token:

    private repo the token cannot reach        -> 404
    reachable repo, permission missing         -> 403 + X-Accepted-GitHub-Permissions
    public repo, read the public may do        -> 200 (with or without a token)
    write by an anonymous caller               -> 401
    unknown / revoked / expired token          -> 401 "Bad credentials"
    GitHub-Authentication-Token-Expiration     -> on every authenticated answer

Routes
    GET  /user  /user/repos  /users/{u}/repos
    GET  /repos/{o}/{r}  /branches/{b}  /rules/branches/{b}
    GET  /git/matching-refs/heads/hermes/  /git/ref/heads/{b}
    POST /git/refs   PATCH|DELETE /git/refs/heads/{b}   POST /git/blobs
    GET  /contents/{path}
    GET|POST /pulls   PATCH /pulls/{n}
    GET  /actions/permissions /actions/secrets /actions/variables /hooks /actions/runs
    POST /credentials/revoke  (403 when an Authorization header is sent)

Use
    fake = FakeGitHub(); fake.user("octo", 7)
    fake.repo("octo/app", private=True)
    tok = fake.profile("clean", "octo", ["octo/app"])
    base = fake.start()            # "http://127.0.0.1:<port>"
    ... fake.calls("POST", r"/git/blobs") ...
    fake.stop()

Every request is logged (method, path, query, headers, body) for
assertions — `fake.log`. Mutations happen under one lock. The change-pipeline
suites can bind a repository's heads to a local bare repo with
`fake.repo(..., heads_fn=lambda: {...})`.
"""
import json
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LEVEL = {None: 0, "read": 1, "write": 2}
CLEAN = {"contents": "write", "pull_requests": "write", "metadata": "read"}
PROFILE_EXTRA = {
    "clean": {},
    "admin": {"administration": "write"},
    "secrets": {"secrets": "read"},
    "variables": {"variables": "read"},
    "hooks": {"webhooks": "read"},
    "actions": {"actions": "read"},
    "all-repos": {},
    "pending-org": {},
    "flaky-5xx": {},
    "no-expiry": {},
    "read-only": {"contents": "read", "pull_requests": "read"},
    "no-pulls": {"pull_requests": None},
}


def fg_token():
    """A token shaped like a real fine-grained PAT (github_pat_ + 82)."""
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    a = "".join(secrets.choice(alpha) for _ in range(22))
    b = "".join(secrets.choice(alpha) for _ in range(59))
    return "github_pat_11" + a + "_" + b


def exp_header(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def _sha(seed):
    import hashlib
    return hashlib.sha1(str(seed).encode()).hexdigest()


class FakeGitHub(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.users = {}
        self.repos = {}
        self.tokens = {}
        self.log = []
        self.revoked = []
        self.blobs = []
        self.fail = []            # [method|None, compiled regex, status, times|None]
        self.echo_auth = False    # adversarial: error bodies quote the Authorization header
        self.revoke_status = None  # force a /credentials/revoke answer
        self._srv = None
        self._th = None
        self.base = None
        self._pr_n = 0
        # ---- c03b additions (E07/E50): off unless a suite sets them -----------
        self.repo_ids = {}        # full_lower -> GitHub's numeric repository id

    # ---- world -------------------------------------------------------------
    def user(self, login, uid=None, type="User"):
        with self.lock:
            self.users[login.lower()] = {"login": login, "id": uid or (len(self.users) + 100),
                                         "type": type}
        return self

    def repo(self, full, private=True, default_branch="main", size=10, archived=False,
             fork=False, protected=False, rules=None, rules_status=200, workflows=None,
             heads=None, heads_fn=None, draft_unsupported=False):
        o, n = full.split("/", 1)
        if o.lower() not in self.users:
            self.user(o)
        hs = {default_branch: _sha(full + default_branch)}
        hs.update(heads or {})
        with self.lock:
            self.repos[full.lower()] = {
                "full_name": full, "owner": o, "name": n, "private": private,
                "default_branch": default_branch, "size": size, "archived": archived,
                "fork": fork, "protected": protected, "rules": list(rules or []),
                "rules_status": rules_status, "workflows": dict(workflows or {}),
                "heads": hs, "heads_fn": heads_fn, "pulls": [],
                "draft_unsupported": draft_unsupported}
        return self

    def token(self, tok, login, repos="all", perms=None, expires="30d", pending_org=False,
              flaky=None, rate_limit_after=None, target=None):
        """Register `tok`. repos: 'all' (every repo of `target`, default the
        login) or a list. expires: '30d' | None (no header) | unix ts."""
        if expires == "30d":
            expires = time.time() + 30 * 86400
        with self.lock:
            self.tokens[tok] = {"login": login, "repos": repos,
                                "perms": dict(perms if perms is not None else CLEAN),
                                "expires": expires, "pending_org": pending_org,
                                "flaky": [(re.compile(rx), st) for rx, st in (flaky or [])],
                                "rate_after": rate_limit_after, "used": 0,
                                "target": target or login}
        return tok

    def profile(self, name, login, repos="all", **kw):
        """A named permission profile (§7): clean, admin, secrets, variables,
        hooks, actions, all-repos, pending-org, flaky-5xx, no-expiry, expired,
        read-only, no-pulls, classic, app."""
        if name == "classic":
            tok = "ghp_" + secrets.token_hex(18)
            perms = dict(CLEAN, administration="write")
            return self.token(tok, login, "all", perms)
        if name == "app":
            tok = "gho_" + secrets.token_hex(18)
            return self.token(tok, login, "all", dict(CLEAN))
        perms = dict(CLEAN)
        if name == "expired":
            return self.token(fg_token(), login, repos, perms,
                              expires=time.time() - 3600)
        perms.update(PROFILE_EXTRA[name])
        if name == "all-repos":
            repos = "all"
        if name == "pending-org":
            kw.setdefault("pending_org", True)
        if name == "flaky-5xx":
            kw.setdefault("flaky", [(r"/hooks$", 502)])
        if name == "no-expiry":
            kw.setdefault("expires", None)
        return self.token(fg_token(), login, repos, perms, **kw)

    def set_head(self, full, branch, sha):
        with self.lock:
            self.repos[full.lower()]["heads"][branch] = sha

    def fail_on(self, method, rx, status, times=None, unauth_only=False, message=None,
                headers=None):
        """Answer `status` for matching requests (`times` = how many, None =
        always). unauth_only: only when the request carries no Authorization
        (GitHub throttles anonymous callers separately). message/headers shape
        the error body, e.g. a rate limit: 403 + {"X-RateLimit-Remaining": "0"}."""
        with self.lock:
            self.fail.append([method, re.compile(rx), status, times, bool(unauth_only),
                              message, dict(headers or {})])

    def calls(self, method=None, rx=None):
        with self.lock:
            out = list(self.log)
        return [c for c in out if (method is None or c["method"] == method)
                and (rx is None or re.search(rx, c["path"]))]

    # ---- c03b additions (E07): a repository renamed/transferred on GitHub --
    def move(self, old, new, repo_id=None, location=True):
        """Rename `old` to `new` the way GitHub does: the old API path answers
        301 (Location /repositories/<id> when `location`), /user/repos lists the
        new name, and a token whose selection held `old` now holds `new`."""
        with self.lock:
            r = self.repos.pop(old.lower())
            o, n = new.split("/", 1)
            r.update({"full_name": new, "owner": o, "name": n})
            self.repos[new.lower()] = r
            rid = repo_id or self.repo_ids.pop(old.lower(), None)
            if rid:
                self.repo_ids[new.lower()] = rid
            for t in self.tokens.values():
                if isinstance(t["repos"], list) and old.lower() in [x.lower() for x in t["repos"]]:
                    t["repos"] = [x for x in t["repos"] if x.lower() != old.lower()] + [new]
        hdr = {"Location": "%s/repositories/%s" % (self.base or "", rid)} \
            if (location and rid) else {}
        o, n = old.split("/", 1)
        self.fail_on(None, r"^/repos/%s/%s(/.*)?$" % (re.escape(o), re.escape(n)), 301,
                     message="Moved Permanently", headers=hdr)
        return self

    # ---- server ------------------------------------------------------------
    def start(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _go(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                u = urllib.parse.urlsplit(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                hdrs = {k.lower(): v for k, v in self.headers.items()}
                try:
                    body = json.loads(raw.decode()) if raw else None
                except Exception:
                    body = None
                status, obj, extra = fake.handle(method, u.path, q, hdrs, body)
                data = b"" if obj is None else json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

            def do_PATCH(self):
                self._go("PATCH")

            def do_DELETE(self):
                self._go("DELETE")

            def do_PUT(self):
                self._go("PUT")

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._srv.daemon_threads = True
        self._th = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._th.start()
        self.base = "http://127.0.0.1:%d" % self._srv.server_address[1]
        return self.base

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    # ---- the API -----------------------------------------------------------
    def _err(self, status, msg, hdrs, extra=None):
        if self.echo_auth and hdrs.get("authorization"):
            msg = msg + " (you sent " + hdrs["authorization"] + ")"
        return status, {"message": msg, "documentation_url": "https://docs.github.com/rest"}, \
            (extra or {})

    def _reach(self, t, repo):
        if t is None:
            return False
        if t["repos"] == "all":
            return repo["owner"].lower() == t["target"].lower()
        return repo["full_name"].lower() in {x.lower() for x in t["repos"]}

    def _heads(self, repo):
        if callable(repo.get("heads_fn")):
            try:
                return dict(repo["heads_fn"]())
            except Exception:
                return {}
        return repo["heads"]

    def _repo_json(self, repo):
        d = self._repo_json_base(repo)
        if self.repo_ids.get(repo["full_name"].lower()):          # c03b (E07)
            d["id"] = self.repo_ids[repo["full_name"].lower()]
        return d

    def _repo_json_base(self, repo):
        return {"full_name": repo["full_name"], "name": repo["name"],
                "owner": {"login": repo["owner"],
                          "type": self.users.get(repo["owner"].lower(), {}).get("type", "User")},
                "private": repo["private"], "default_branch": repo["default_branch"],
                "size": repo["size"], "archived": repo["archived"], "fork": repo["fork"],
                "visibility": "private" if repo["private"] else "public"}

    def handle(self, method, path, q, hdrs, body):
        with self.lock:
            self.log.append({"method": method, "path": path, "query": dict(q),
                             "headers": dict(hdrs), "body": body, "ts": time.time()})
            for f in self.fail:
                if (f[0] is None or f[0] == method) and f[1].search(path):
                    if f[4] and hdrs.get("authorization"):
                        continue
                    if f[3] is not None:
                        if f[3] <= 0:
                            continue
                        f[3] -= 1
                    return self._err(f[2], f[5] or "Server Error", hdrs, f[6])
            return self._handle(method, path, q, hdrs, body)

    def _handle(self, method, path, q, hdrs, body):
        # credential revocation: deliberately unauthenticated
        if path == "/credentials/revoke" and method == "POST":
            if hdrs.get("authorization"):
                return self._err(403, "Must not be authenticated", {})
            if self.revoke_status:
                return self.revoke_status, {"message": "nope"}, {}
            creds = (body or {}).get("credentials") if isinstance(body, dict) else None
            if not isinstance(creds, list) or not creds:
                return 422, {"message": "Validation Failed"}, {}
            for c in creds:
                self.revoked.append(c)
            return 202, {}, {}

        t, extra = None, {}
        auth = hdrs.get("authorization")
        if auth:
            m = re.match(r"^(?:Bearer|token)\s+(\S+)$", auth)
            tok = m.group(1) if m else None
            t = self.tokens.get(tok) if tok else None
            if t is None or tok in self.revoked or (
                    isinstance(t["expires"], (int, float)) and t["expires"] < time.time()):
                return self._err(401, "Bad credentials", hdrs)
            if isinstance(t["expires"], (int, float)):
                extra["GitHub-Authentication-Token-Expiration"] = exp_header(t["expires"])
            t["used"] += 1
            if t["rate_after"] is not None and t["used"] > t["rate_after"]:
                return self._err(403, "API rate limit exceeded for user", hdrs,
                                 {"X-RateLimit-Remaining": "0"})
            for rx, st in t["flaky"]:
                if rx.search(path):
                    return self._err(st, "Server Error", hdrs, extra)

        # ---- c03b addition (E50): account permissions. Answered only when the
        # token carries the permission; otherwise falls through to the 404 the
        # fake always gave for these paths
        acct = {"/user/keys": "git_ssh_keys", "/user/gpg_keys": "gpg_keys",
                "/user/ssh_signing_keys": "ssh_signing_keys"}
        if path in acct and method == "GET" and t is not None and \
                LEVEL.get(t["perms"].get(acct[path])) >= LEVEL["read"]:
            return 200, [], extra

        if path == "/user" and method == "GET":
            if t is None:
                return self._err(401, "Requires authentication", hdrs)
            u = self.users.get(t["login"].lower()) or {"login": t["login"], "id": 1}
            return 200, {"login": u["login"], "id": u["id"], "type": u.get("type", "User")}, extra

        if path == "/user/repos" and method == "GET":
            if t is None:
                return self._err(401, "Requires authentication", hdrs)
            vis = q.get("visibility", "all")
            rows = [r for r in self.repos.values() if self._reach(t, r)
                    and (vis == "all" or (vis == "private") == r["private"])]
            rows.sort(key=lambda r: r["full_name"].lower())
            per = int(q.get("per_page") or 30)
            page = int(q.get("page") or 1)
            chunk = rows[(page - 1) * per:page * per]
            return 200, [self._repo_json(r) for r in chunk], extra

        m = re.match(r"^/users/([^/]+)/repos$", path)
        if m and method == "GET":
            owner = urllib.parse.unquote(m.group(1)).lower()
            rows = [self._repo_json(r) for r in sorted(self.repos.values(),
                                                         key=lambda r: r["full_name"].lower())
                    if r["owner"].lower() == owner and not r["private"]]
            return 200, rows, extra

        m = re.match(r"^/repos/([^/]+)/([^/]+)(/.*)?$", path)
        if not m:
            return self._err(404, "Not Found", hdrs, extra)
        full = urllib.parse.unquote(m.group(1)) + "/" + urllib.parse.unquote(m.group(2))
        rest = m.group(3) or ""
        repo = self.repos.get(full.lower())
        if repo is None:
            return self._err(404, "Not Found", hdrs, extra)
        reach = self._reach(t, repo)
        if repo["private"] and not reach:
            return self._err(404, "Not Found", hdrs, extra)

        def need(cat, level, public_read=False):
            """None when allowed, else the error triple."""
            if public_read and not repo["private"] and level == "read":
                return None
            if t is None:
                return self._err(401, "Requires authentication", hdrs, extra)
            if not reach:
                return self._err(403, "Resource not accessible by personal access token",
                                 hdrs, dict(extra, **{
                                     "X-Accepted-GitHub-Permissions": "%s=%s" % (cat, level)}))
            if level == "write" and t["pending_org"] and \
                    self.users.get(repo["owner"].lower(), {}).get("type") == "Organization":
                return self._err(403, "Resource not accessible by personal access token: "
                                      "the organization has not approved it", hdrs, extra)
            if LEVEL.get(t["perms"].get(cat)) < LEVEL[level]:
                return self._err(403, "Resource not accessible by personal access token",
                                 hdrs, dict(extra, **{
                                     "X-Accepted-GitHub-Permissions": "%s=%s" % (cat, level)}))
            return None

        if rest == "" and method == "GET":
            return need("metadata", "read", True) or (200, self._repo_json(repo), extra)

        m = re.match(r"^/branches/(.+)$", rest)
        if m and method == "GET":
            e = need("metadata", "read", True)
            if e:
                return e
            b = urllib.parse.unquote(m.group(1))
            heads = self._heads(repo)
            if b not in heads:
                return self._err(404, "Branch not found", hdrs, extra)
            return 200, {"name": b, "commit": {"sha": heads[b]},
                         "protected": bool(repo["protected"] and b == repo["default_branch"])}, extra

        m = re.match(r"^/rules/branches/(.+)$", rest)
        if m and method == "GET":
            e = need("metadata", "read", True)
            if e:
                return e
            if repo["rules_status"] != 200:
                return self._err(repo["rules_status"], "Resource not accessible", hdrs, extra)
            b = urllib.parse.unquote(m.group(1))
            rules = [r for r in repo["rules"]] if b == repo["default_branch"] else []
            return 200, [{"type": r} if isinstance(r, str) else r for r in rules], extra

        if rest.startswith("/git/matching-refs/heads/") and method == "GET":
            e = need("contents", "read", True)
            if e:
                return e
            prefix = urllib.parse.unquote(rest[len("/git/matching-refs/heads/"):])
            rows = [{"ref": "refs/heads/" + b, "object": {"sha": s, "type": "commit"}}
                    for b, s in sorted(self._heads(repo).items()) if b.startswith(prefix)]
            per = int(q.get("per_page") or 30)
            page = int(q.get("page") or 1)
            return 200, rows[(page - 1) * per:page * per], extra

        m = re.match(r"^/git/ref/heads/(.+)$", rest)
        if m and method == "GET":
            e = need("contents", "read", True)
            if e:
                return e
            b = urllib.parse.unquote(m.group(1))
            heads = self._heads(repo)
            if b not in heads:
                return self._err(404, "Not Found", hdrs, extra)
            return 200, {"ref": "refs/heads/" + b,
                         "object": {"sha": heads[b], "type": "commit"}}, extra

        if rest == "/git/refs" and method == "POST":
            e = need("contents", "write")
            if e:
                return e
            ref = str((body or {}).get("ref") or "")
            sha = str((body or {}).get("sha") or "")
            if not ref.startswith("refs/heads/"):
                return 422, {"message": "Reference name invalid"}, extra
            repo["heads"][ref[len("refs/heads/"):]] = sha
            return 201, {"ref": ref, "object": {"sha": sha}}, extra

        m = re.match(r"^/git/refs/heads/(.+)$", rest)
        if m and method in ("PATCH", "DELETE"):
            e = need("contents", "write")
            if e:
                return e
            b = urllib.parse.unquote(m.group(1))
            if b not in repo["heads"]:
                return 422, {"message": "Reference does not exist"}, extra
            if method == "DELETE":
                del repo["heads"][b]
                return 204, None, extra
            repo["heads"][b] = str((body or {}).get("sha") or "")
            return 200, {"ref": "refs/heads/" + b, "object": {"sha": repo["heads"][b]}}, extra

        if rest == "/git/blobs" and method == "POST":
            e = need("contents", "write")
            if e:
                return e
            self.blobs.append(repo["full_name"])
            return 201, {"sha": _sha(("blob", len(self.blobs))), "url": "x"}, extra

        m = re.match(r"^/contents/(.*)$", rest)
        if m and method == "GET":
            e = need("contents", "read", True)
            if e:
                return e
            import base64
            p = urllib.parse.unquote(m.group(1)).strip("/")
            wf = repo["workflows"]
            if p == ".github/workflows":
                if not wf:
                    return self._err(404, "Not Found", hdrs, extra)
                return 200, [{"name": k, "path": ".github/workflows/" + k, "type": "file",
                              "size": len(v) if v is not None else 2000000}
                             for k, v in sorted(wf.items())], extra
            if p.startswith(".github/workflows/") and p.split("/")[-1] in wf:
                text = wf[p.split("/")[-1]]
                if text is None:                 # "too large": no content
                    return 200, {"name": p, "type": "file", "encoding": "none",
                                 "content": ""}, extra
                return 200, {"name": p.split("/")[-1], "path": p, "type": "file",
                             "encoding": "base64",
                             "content": base64.b64encode(text.encode()).decode()}, extra
            return self._err(404, "Not Found", hdrs, extra)

        if rest == "/pulls" and method == "GET":
            e = need("pull_requests", "read", True)
            if e:
                return e
            return 200, [p for p in repo["pulls"] if p["state"] == "open"][:int(
                q.get("per_page") or 30)], extra

        if rest == "/pulls" and method == "POST":
            e = need("pull_requests", "write")
            if e:
                return e
            b = body or {}
            if b.get("draft") and repo["draft_unsupported"]:
                return 422, {"message": "Draft pull requests are not supported in this "
                                        "repository."}, extra
            head = str(b.get("head") or "")
            if head not in self._heads(repo):
                return 422, {"message": "Validation Failed", "errors": [{"field": "head"}]}, extra
            self._pr_n += 1
            pr = {"number": self._pr_n, "state": "open", "draft": bool(b.get("draft")),
                  "title": b.get("title"), "body": b.get("body"), "head": {"ref": head},
                  "base": {"ref": b.get("base")},
                  "maintainer_can_modify": b.get("maintainer_can_modify"),
                  "html_url": "https://github.com/%s/pull/%d" % (repo["full_name"], self._pr_n)}
            repo["pulls"].append(pr)
            return 201, pr, extra

        m = re.match(r"^/pulls/(\d+)$", rest)
        if m and method == "PATCH":
            e = need("pull_requests", "write")
            if e:
                return e
            for pr in repo["pulls"]:
                if pr["number"] == int(m.group(1)):
                    if (body or {}).get("state") in ("open", "closed"):
                        pr["state"] = body["state"]
                    return 200, pr, extra
            return self._err(404, "Not Found", hdrs, extra)

        probes = {"/actions/permissions": ("administration", False),
                  "/actions/secrets": ("secrets", False),
                  "/actions/variables": ("variables", False),
                  "/hooks": ("webhooks", False),
                  "/actions/runs": ("actions", True)}
        if rest in probes and method == "GET":
            cat, public = probes[rest]
            e = need(cat, "read", public)
            if e:
                return e
            return 200, ({"total_count": 0, "workflow_runs": []} if cat == "actions"
                         else {"total_count": 0, "secrets": []} if cat == "secrets"
                         else {"total_count": 0, "variables": []} if cat == "variables"
                         else [] if cat == "webhooks"
                         else {"enabled": True, "allowed_actions": "all"}), extra

        return self._err(404, "Not Found", hdrs, extra)


# ---------------------------------------------------------------------------
# loading the REAL connector for a suite (shared with the change-pipeline and
# UI suites of the next waves)
# ---------------------------------------------------------------------------
class Ctx(object):
    """RouteCtx stand-in. `nonce_ok` models F05: None = pre-F05 (absent),
    False = the owner's proof was checked and missing, True = present."""

    def __init__(self, body=None, query=None, nonce_ok=None):
        self.body = body if body is not None else {}
        self.query = query or {}
        if nonce_ok is not None:
            self.nonce_ok = nonce_ok

    def q1(self, k, d=""):
        v = self.query.get(k)
        if isinstance(v, list):
            return v[0] if v else d
        return v if v is not None else d


def load_connector(base, home=None, ambient_path=None, extra=None):
    """Exec dashboard/aux_github.py (which imports the real gh_api.py) into a
    fresh namespace on a throwaway HOME, with routes, recorder rows and
    Telegram notices captured. `base` is the fake's URL and is FORCED into
    GH_API_BASE after load, so no environment can send a suite's token to
    real GitHub. Returns the namespace; ns["call"](method, path, body,
    nonce_ok=None, query=None) -> (status, payload)."""
    import os
    import sys
    import tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(
        os.path.dirname(here)))
    dash = os.path.join(repo, "dashboard")
    if dash not in sys.path:
        sys.path.insert(0, dash)
    if not str(base).startswith("http://127.0.0.1:"):
        raise SystemExit("FAIL refusing to load the connector against a non-loopback base")
    home = home or tempfile.mkdtemp(prefix="hermes-gh-")
    data = os.path.join(home, ".hermes", "dashboard")
    os.makedirs(data, mode=0o700, exist_ok=True)
    stub_bin = ambient_path or os.path.join(home, "empty-bin")
    os.makedirs(stub_bin, exist_ok=True)
    routes, records, telegram = {}, [], []

    def rec(**kw):
        records.append(kw)
        return {"ok": True, "tool_call_id": kw.get("tool_call_id")}

    def tg(text):
        telegram.append(text)
        return True, ""

    ns = {"__name__": "aux_github_under_test", "HOME": home, "DATA": data, "HERE": dash,
          "register_get": lambda p, f: routes.__setitem__(("GET", p), f),
          "register_post": lambda p, f: routes.__setitem__(("POST", p), f),
          "_gh_checks_thread_started": True,
          "recorder_record_local": rec, "_wt_send_telegram": tg,
          "_ssl_context": lambda: None}
    ns.update(extra or {})
    src = os.path.join(dash, "aux_github.py")
    with open(src, encoding="utf-8") as f:
        exec(compile(f.read(), src, "exec"), ns)
    ns["GH_API_BASE"] = base
    ns["GH_AMBIENT_PATH"] = stub_bin
    ns["ROUTES"], ns["RECORDS"], ns["TELEGRAM"] = routes, records, telegram

    def call(method, path, body=None, nonce_ok=None, query=None):
        fn = routes.get((method, path))
        if fn is None:
            return 404, {"error": "no route"}
        r = fn(Ctx(body, query, nonce_ok))
        if isinstance(r, tuple):
            return r[1], r[0]
        return 200, r

    ns["call"] = call
    return ns
