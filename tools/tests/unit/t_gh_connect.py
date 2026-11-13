#!/usr/bin/env python3
"""GitHub sandbox connector (round 3, R3-GH1) — dashboard/aux_github.py routes
(docs/plans/github-sandbox-2026-10.md §1.3-§1.5, §2.1, §5.1, §7 t_gh_connect).

The REAL aux_github.py (and the real gh_api.py it imports) is exec'd on a
throwaway HOME by _gh_fake.load_connector, with GH_API_BASE forced to a fake
GitHub on 127.0.0.1:0 that this suite owns. Proves:

  * every refusal row of §1.3 produces its exact line, and NOTHING is written
    (no credential.json, no state.json); a 5xx / rate limit fails closed;
  * a clean token is stored 0600 in a 0700 dir, with login, expiry and the
    per-repository protection + CI verdict;
  * the token string and its base64 forms never appear in any response body,
    stderr, recorder row, Telegram text or state.json;
  * GET /status has the documented shape, never a `token` key, and makes no
    GitHub request;
  * pause on/off asymmetry, owner-only routes refuse when F05's nonce check
    fails (ctx.nonce_ok is False) and the gate predicate marks them;
  * revoke calls /credentials/revoke WITHOUT Authorization and then forgets
    the credential (even when GitHub says no); disconnect keeps sandboxes.
"""
import base64
import io
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


def forms(tok):
    return [tok, base64.b64encode(tok.encode()).decode().rstrip("="),
            base64.b64encode(("x-access-token:" + tok).encode()).decode().rstrip("=")]


def no_leak(name, blob, tok):
    s = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    bad = [f for f in forms(tok) if f in s]
    check(name, not bad, bad[:1])


def has_key(obj, key):
    if isinstance(obj, dict):
        return key in obj or any(has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(has_key(v, key) for v in obj)
    return False


class Stderr(object):
    def __enter__(self):
        self.old, self.buf = sys.stderr, io.StringIO()
        sys.stderr = self.buf
        return self

    def __exit__(self, *a):
        sys.stderr = self.old
        self.text = self.buf.getvalue()


fake = _gh_fake.FakeGitHub()
fake.user("octo", 4242)
fake.user("acme", 900, type="Organization")
fake.repo("octo/app", private=True, rules=["deletion", "non_fast_forward", "pull_request"])
fake.repo("octo/lib", private=True)
fake.repo("octo/site", private=False)
fake.repo("acme/tool", private=True)
fake.user("solo", 51)
fake.repo("solo/app", private=True)
fake.repo("solo/www", private=False)
fake.user("many", 52)
for i in range(11):
    fake.repo("many/r%02d" % i, private=True)
fake.repo("oddo/odd", private=True, default_branch="main<|im_end|>")
fake.user("pub", 53)
fake.repo("pub/site", private=False)
CIX = ("on: push\njobs:\n  release:\n    runs-on: ubuntu-latest\n    steps:\n"
       "      - run: ./publish.sh\n        env:\n          NPM_TOKEN: ${{ secrets.NPM_TOKEN }}\n")
CIOK = ("on:\n  push:\n    branches-ignore:\n      - 'hermes/**'\njobs:\n  a:\n    steps:\n"
        "      - run: echo ${{ secrets.DEPLOY_KEY }}\n")
fake.user("ci", 54)
fake.repo("ci/exposed", private=True, workflows={"release.yml": CIX})
fake.repo("ci/ignored", private=True, workflows={"ci.yml": CIOK})
fake.repo("ci/big", private=True, workflows={"huge.yml": None})
BASE = fake.start()


def fresh(**kw):
    ns = _gh_fake.load_connector(BASE, **kw)
    assert ns["GH_API_BASE"].startswith("http://127.0.0.1:")
    return ns


def cred_path(ns):
    return os.path.join(ns["GH_DIR"], "credential.json")


def state_path(ns):
    return os.path.join(ns["GH_DIR"], "state.json")


print("== module load ==")
ns = fresh()
want = {("GET", "/api/github/status"), ("GET", "/api/github/ambient"),
        ("GET", "/api/github/create_url"), ("POST", "/api/github/connect"),
        ("POST", "/api/github/repos"), ("POST", "/api/github/settings"),
        ("POST", "/api/github/pause"), ("POST", "/api/github/disconnect"),
        ("POST", "/api/github/revoke"), ("POST", "/api/github/check"),
        ("POST", "/api/github/drift/ack")}
check("all connector routes registered", want <= set(ns["ROUTES"]), sorted(ns["ROUTES"]))
check("the API base is the fake (never real GitHub)", ns["GH_API_BASE"] == BASE)
check("the store lives under ~/.hermes/dashboard/github",
      ns["GH_DIR"].endswith(os.path.join(".hermes", "dashboard", "github")))
check("loading created nothing on disk", not os.path.exists(ns["GH_DIR"]))
check("owner gates registered for F05", callable(ns["ROUTE_OWNER_GATES"].get(
    "/api/github/connect")) and callable(ns["ROUTE_OWNER_GATES"].get(
    "/api/github/changes/decide")))
_mine = lambda m, p, b=None: "theirs"                                   # noqa: E731
nsx = _gh_fake.load_connector(BASE, extra={"ROUTE_OWNER_GATES": {
    "/api/github/changes/decide": _mine}})
check("a gate the change pipeline registered first is not overwritten",
      nsx["ROUTE_OWNER_GATES"]["/api/github/changes/decide"] is _mine
      and nsx["ROUTE_OWNER_GATES"]["/api/github/connect"] is nsx["_gh_route_gated"])

print("== refusals: exact copy, nothing stored ==")
L = ns["_gh_mod"].LINES
FINAL = ns["_gh_mod"].FINAL_LINE


def refusal(name, tok, repos, want_ids, want_line, retry=False, ns_=None):
    n = ns_ or fresh()
    before = len(fake.log)
    with Stderr() as e:
        st, r = n["call"]("POST", "/api/github/connect", {"token": tok, "repos": repos})
    lines = r.get("lines") or []
    check("%s: refused (%s)" % (name, st), r.get("ok") is False and r.get("refused")
          and st == (503 if retry else 422), (st, r))
    check("%s: exact line" % name, want_line in lines, lines)
    check("%s: finding ids %s" % (name, want_ids),
          set(want_ids) <= {f["id"] for f in r.get("findings") or []}, r.get("findings"))
    if retry:
        check("%s: retry, and no 'delete this token' line" % name,
              r.get("retry") is True and r.get("final") is None, r)
    else:
        check("%s: final line" % name, r.get("final") == FINAL, r.get("final"))
    check("%s: no credential.json" % name, not os.path.exists(cred_path(n)))
    check("%s: no state.json either" % name, not os.path.exists(state_path(n)))
    no_leak("%s: token not in the response" % name, r, tok)
    no_leak("%s: token not on stderr" % name, e.text, tok)
    check("%s: no recorder row" % name, not n["RECORDS"])
    return r, len(fake.log) - before


tok = fake.profile("classic", "octo")
r, n = refusal("classic ghp_", tok, ["octo/app"], ["kind"], L["classic"])
check("classic: refused before ANY request to GitHub", n == 0, n)
tok = fake.profile("app", "octo")
r, n = refusal("gho_ app token", tok, [], ["kind"], L["app_token"])
check("gho_: no request either", n == 0, n)
refusal("Administration", fake.profile("admin", "octo", ["octo/app"]), ["octo/app"],
        ["administration"], L["administration"])
refusal("Secrets", fake.profile("secrets", "octo", ["octo/app"]), ["octo/app"],
        ["secrets"], L["secrets"])
refusal("Variables", fake.profile("variables", "octo", ["octo/app"]), ["octo/app"],
        ["variables"], L["secrets"])
refusal("Webhooks", fake.profile("hooks", "octo", ["octo/app"]), ["octo/app"],
        ["webhooks"], L["webhooks"])
refusal("Actions on a private repo", fake.profile("actions", "octo", ["octo/app"]),
        ["octo/app"], ["actions"], L["actions"])
refusal("unlisted private repos", fake.profile("all-repos", "octo"), ["octo/app"],
        ["unlisted_private"],
        "It reaches 1 private repository you did not list (octo/lib). Edit the token on "
        "GitHub, choose Only select repositories, and pick just the ones Hermes may use.")
r, _ = refusal("writes to an unlisted repo", fake.profile("all-repos", "solo"), ["solo/app"],
               ["unlisted_write"], L["unlisted_write"].format(repo="solo/www"))
check("the write-reach probe was an unreferenced blob, never a ref",
      not fake.calls("POST", r"/git/refs") and fake.calls("POST", r"^/repos/solo/www/git/blobs$"))
refusal("more than 10 repositories", fake.profile("all-repos", "many"), [], ["too_many"],
        L["too_many"])
refusal("no Contents write", fake.profile("read-only", "octo", ["octo/app"]), ["octo/app"],
        ["no_contents", "no_writable"], L["no_writable"])
r, _ = refusal("org has not approved the token",
               fake.profile("pending-org", "octo", ["acme/tool"], target="acme"), ["acme/tool"],
               ["no_contents"], L["no_writable"])
check("the per-repo line mentions organization approval",
      any("organization" in f["line"] for f in r["findings"] if f["id"] == "no_contents"))
refusal("expired token", fake.profile("expired", "octo", ["octo/app"]), ["octo/app"],
        ["unauthorized"], L["unauthorized"])
refusal("unknown / revoked token", _gh_fake.fg_token(), ["octo/app"], ["unauthorized"],
        L["unauthorized"])
refusal("5xx on a refuse-probe fails closed",
        fake.profile("flaky-5xx", "octo", ["octo/app"]), ["octo/app"], ["network"],
        L["network"], retry=True)
refusal("a rate limit is not a pass",
        fake.profile("clean", "octo", ["octo/app"], rate_limit_after=2), ["octo/app"],
        ["network"], L["network"], retry=True)
fake.echo_auth = True
r, _ = refusal("an upstream error quoting the token",
               fake.profile("flaky-5xx", "octo", ["octo/app"]), ["octo/app"], ["network"],
               L["network"], retry=True)
fake.echo_auth = False
n0 = fresh()
st, r = n0["call"]("POST", "/api/github/connect", {"token": "", "repos": []})
check("an empty token is a 400, not a probe", st == 400 and r.get("error") == "no_token")
st, r = n0["call"]("POST", "/api/github/connect",
                   {"token": _gh_fake.fg_token(), "repos": ["o/r%d" % i for i in range(11)]})
check("more than 10 listed repositories is a 400 before any request", st == 400
      and r.get("error") == "bad_repos", (st, r))
st, r = n0["call"]("POST", "/api/github/connect",
                   {"token": _gh_fake.fg_token(), "repos": ["../main"]})
check("a malformed repository name is a 400", st == 400 and r.get("bad") == ["../main"], r)
check("none of that wrote anything", not os.path.exists(n0["GH_DIR"]))

print("== a public-only repo: Actions is the documented residual ==")
n1 = fresh()
tok = fake.profile("actions", "pub", ["pub/site"])
st, r = n1["call"]("POST", "/api/github/connect", {"token": tok, "repos": ["pub/site"]})
check("accepted", st == 200 and r.get("ok"), (st, r.get("lines")))
check("actions reported as unchecked on public repos",
      r.get("checks_passed", {}).get("actions") == "public_unchecked", r.get("checks_passed"))

print("== clean connect ==")
ns = fresh()
tok = fake.profile("clean", "octo", ["octo/app"])
blobs0 = len(fake.blobs)
heads0 = json.dumps({k: v["heads"] for k, v in fake.repos.items()}, sort_keys=True)
with Stderr() as err:
    st, r = ns["call"]("POST", "/api/github/connect", {"token": tok, "repos": "octo/app\n"})
check("stored", st == 200 and r.get("ok") and r.get("stored") and r.get("connected"), (st, r))
check("login + id", r.get("login") == "octo" and r.get("user_id") == 4242)
check("expiry parsed (~30 days)", 29 <= (r.get("expires_in_days") or 0) <= 30.1,
      r.get("expires_in_days"))
mode_d = stat.S_IMODE(os.stat(ns["GH_DIR"]).st_mode)
mode_c = stat.S_IMODE(os.stat(cred_path(ns)).st_mode)
mode_s = stat.S_IMODE(os.stat(state_path(ns)).st_mode)
check("dir is 0700", mode_d == 0o700, oct(mode_d))
check("credential.json is 0600", mode_c == 0o600, oct(mode_c))
check("state.json is 0600", mode_s == 0o600, oct(mode_s))
cred = json.load(open(cred_path(ns)))
check("credential holds kind/token/login/expiry", cred.get("kind") == "fine_grained_pat"
      and cred.get("token") == tok and cred.get("login") == "octo"
      and isinstance(cred.get("expires_at"), float))
repo = r["repos"][0]
check("the listed repo is enabled and writable", repo["full_name"] == "octo/app"
      and repo["enabled"] and repo["writable"] and repo["writes_ok"], repo)
check("its protection is read at connect", repo["protection"]["state"] == "protected"
      and "requires PR" in repo["protection"]["text"], repo.get("protection"))
check("its CI verdict is known (no workflows: not exposed)",
      repo["ci"] and repo["ci"]["exposed"] is False, repo.get("ci"))
check("sandbox path is ~/HermesSandbox/github/<owner>/<repo>",
      repo["sandbox"] == os.path.join(ns["HOME"], "HermesSandbox", "github", "octo", "app")
      and repo["prepared"] is False)
check("probes created blobs only: no ref changed on 'GitHub'",
      len(fake.blobs) > blobs0 and json.dumps({k: v["heads"] for k, v in fake.repos.items()},
                                               sort_keys=True) == heads0)
check("the workflow note is shown", "Workflows cannot be checked" in r.get("workflow_note", ""))
check("a recorder row says who connected", any(x["tool"] == "github_connect"
                                               for x in ns["RECORDS"]))
no_leak("token not in the connect response", r, tok)
no_leak("token not in state.json", open(state_path(ns)).read(), tok)
no_leak("token not on stderr", err.text, tok)
no_leak("token not in any recorder row", ns["RECORDS"], tok)
check("_gh_writes_allowed agrees", ns["_gh_writes_allowed"]("octo/app") == (True, ""))
check("_gh_identity is the noreply address",
      ns["_gh_identity"]()["email"] == "4242+octo@users.noreply.github.com")
env = ns["_gh_git_env"]()
check("_gh_git_env carries the token only as the extraheader triple",
      env.get("GIT_CONFIG_KEY_0") == "http.https://github.com/.extraheader" and tok not in
      json.dumps(env))

txt = ns["_gh_push_notice_text"]("octo/app", "hermes/fix-login-copy", 3, 12, True)
check("the push notice is the design's sentence",
      txt == "Hermes pushed 3 files to octo/app on hermes/fix-login-copy and opened draft PR "
             "#12. If you did not approve this, pause GitHub writes in Settings › Connections.",
      txt)
check("...and a push without a PR says so", ns["_gh_push_notice_text"](
    "octo/app", "hermes/x1", 1).startswith("Hermes pushed 1 file to octo/app on hermes/x1."))
lim = ns["_gh_state"]()["settings"]["limits"]
check("limits travel with the settings", lim["max_files"] == 200 and lim["max_bytes"] ==
      5 * 1024 * 1024 and lim["pending_ttl_s"] == 86400 and lim["max_pushes_day"] == 10, lim)

print("== status: documented shape, no token, no network ==")
n_before = len(fake.log)
st, s = ns["call"]("GET", "/api/github/status")
check("200 + connected", st == 200 and s["connected"] and s["login"] == "octo")
for k in ("connected", "login", "expires_at", "writes_paused", "repos", "pending", "ambient"):
    check("status has %s" % k, k in s)
for k in ("full_name", "private", "default_branch", "protection", "sandbox", "prepared"):
    check("repo row has %s" % k, k in s["repos"][0])
for k in ("gh", "keychain", "ssh", "env"):
    check("ambient has %s" % k, k in (s["ambient"] or {}))
check("no `token` key anywhere in the status", not has_key(s, "token"))
no_leak("token not in the status", s, tok)
check("status made no GitHub request", len(fake.log) == n_before, fake.log[n_before:])
check("writes not paused, pending 0", s["writes_paused"] is False and s["pending"] == 0)
check("ruleset JSON offered", s["ruleset"]["recommended"]["rules"][0] == {"type": "deletion"}
      and "Import a ruleset" in s["ruleset"]["path"])
check("owner-only routes are listed for the UI",
      "POST /api/github/connect" in s["routes"]["owner_only"])
st, u = ns["call"]("GET", "/api/github/create_url", query={"repos": ["octo/app,octo/lib"]})
check("create_url adds target_name when every repo shares an owner",
      st == 200 and u["target_name"] == "octo" and "target_name=octo" in u["url"], u)

print("== pause: anyone on, owner-only off ==")
st, r = ns["call"]("POST", "/api/github/pause", {"paused": True, "source": "agent"},
                   nonce_ok=False)
check("pause ON works without owner proof", st == 200 and r["writes_paused"] is True, (st, r))
check("...and stops writes", ns["_gh_writes_allowed"]("octo/app")[0] is False)
check("...recorded", any(x["tool"] == "github_pause" for x in ns["RECORDS"]))
st, r = ns["call"]("POST", "/api/github/pause", {"paused": False}, nonce_ok=False)
check("pause OFF without owner proof is 403", st == 403 and r.get("error") == "owner_only",
      (st, r))
st, r = ns["call"]("POST", "/api/github/pause", {"paused": "false"})
check("a string 'false' is a 400 (strict JSON boolean)", st == 400, (st, r))
check("still paused", ns["_gh_paused"]())
st, r = ns["call"]("POST", "/api/github/pause", {"paused": False}, nonce_ok=True)
check("pause OFF with owner proof works", st == 200 and r["writes_paused"] is False, (st, r))
st, r = ns["call"]("POST", "/api/github/pause", {"paused": False})
check("pre-F05 (nonce_ok absent) is tolerated", st == 200)

print("== owner-only routes refuse when F05's check fails ==")
for path, body in (("/api/github/connect", {"token": tok}),
                   ("/api/github/repos", {"op": "add", "repo": "octo/lib"}),
                   ("/api/github/check", {}),
                   ("/api/github/settings", {"max_pushes_day": 10, "checks_enabled": False}),
                   ("/api/github/drift/ack", {"repo": "octo/app", "branch": "hermes/x"})):
    st, r = ns["call"]("POST", path, body, nonce_ok=False)
    check("%s -> 403 owner_only" % path, st == 403 and r.get("error") == "owner_only", (st, r))
G = ns["_gh_route_gated"]
for args, want in ((("POST", "/api/github/connect", {}), True),
                   (("POST", "/api/github/changes/decide", {"decision": "push_pr"}), True),
                   (("POST", "/api/github/changes/retract", {}), True),
                   (("POST", "/api/github/check", {}), True),
                   (("POST", "/api/github/repos", {"op": "add"}), True),
                   (("POST", "/api/github/repos", {"op": "remove"}), False),
                   (("POST", "/api/github/repos", {"op": "???"}), True),
                   (("POST", "/api/github/pause", {"paused": True}), False),
                   (("POST", "/api/github/pause", {"paused": False}), True),
                   (("POST", "/api/github/pause", {"paused": "true"}), True),
                   (("POST", "/api/github/settings", {"max_pushes_day": 3}), False),
                   (("POST", "/api/github/settings", {"max_pushes_day": 99}), True),
                   (("POST", "/api/github/settings", {"checks_enabled": False}), True),
                   (("POST", "/api/github/disconnect", {}), False),
                   (("POST", "/api/github/revoke", {}), False),
                   (("GET", "/api/github/status", None), False)):
    check("gate %s %s %s -> %s" % (args[0], args[1], json.dumps(args[2]), want),
          G(*args) is want, G(*args))

print("== settings ==")
st, r = ns["call"]("POST", "/api/github/settings", {"max_pushes_day": 11})
check("max_pushes_day above 10 is refused", st == 400, (st, r))
st, r = ns["call"]("POST", "/api/github/settings", {"max_pushes_day": "5"})
check("a string count is refused", st == 400)
st, r = ns["call"]("POST", "/api/github/settings", {"max_pushes_day": 4}, nonce_ok=False)
check("narrowing needs no owner proof", st == 200 and r["settings"]["max_pushes_day"] == 4)
st, r = ns["call"]("POST", "/api/github/settings", {"max_pushes_day": 6}, nonce_ok=False)
check("widening without owner proof is 403", st == 403)
st, r = ns["call"]("POST", "/api/github/settings", {"max_pushes_day": 10})
check("widening by the owner works", st == 200 and r["settings"]["max_pushes_day"] == 10)
st, r = ns["call"]("POST", "/api/github/settings", {"checks_enabled": "no"})
check("checks_enabled must be a JSON boolean", st == 400)
st, r = ns["call"]("POST", "/api/github/settings", {"ping_on_push": False})
check("the Telegram notice per push cannot be turned off", st == 400)
st, r = ns["call"]("POST", "/api/github/settings", {"draft_prs": False})
check("draft PRs cannot be turned off", st == 400)
check("settings report the two owner decisions",
      r.get("error") or ns["_gh_state"]()["settings"]["ping_on_push"] is True)

print("== repositories: add (owner, re-runs the wall), remove (anyone) ==")
st, r = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/lib"})
lib = [x for x in r.get("repos", []) if x["full_name"] == "octo/lib"]
check("a repo the key cannot reach is listed but not enabled",
      st == 200 and lib and lib[0]["enabled"] is False and "cannot write" in
      (lib[0]["enable_blocked"] or ""), (st, lib))
fake.tokens[tok]["repos"].append("octo/lib")
st, r = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/lib"})
lib = [x for x in r.get("repos", []) if x["full_name"] == "octo/lib"]
check("once the key covers it, adding enables it", st == 200 and lib and lib[0]["enabled"],
      (st, lib))
st, r = ns["call"]("POST", "/api/github/repos", {"op": "remove", "repo": "octo/lib"},
                   nonce_ok=False)
lib = [x for x in r.get("repos", []) if x["full_name"] == "octo/lib"]
# E07/E53: remove now DROPS a row nothing refers to (it used to stay, switched off)
check("remove needs no owner proof and removes it", st == 200 and (
    not lib or lib[0]["enabled"] is False), (st, lib))
fake.tokens[tok]["perms"]["administration"] = "read"
st, r = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/lib"})
check("a key widened on GitHub is caught at the next add", st == 422 and r.get("held")
      and L["administration"] in r.get("lines", []), (st, r))
check("...and writes are held", ns["_gh_paused"]() and ns["_gh_writes_allowed"](
    "octo/app")[0] is False)
st, r = ns["call"]("POST", "/api/github/pause", {"paused": False})
check("the owner cannot switch a hold off", st == 409 and r.get("error") == "held", (st, r))
fake.tokens[tok]["perms"]["administration"] = None
st, r = ns["call"]("POST", "/api/github/check", {})
check("a passing check lifts the permission hold", st == 200 and not ns["_gh_paused"](),
      (st, r.get("check"), r.get("paused")))
st, r = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "nope"})
check("a malformed repo is a 400", st == 400)
fake.tokens[tok]["perms"]["contents"] = "read"
st, r = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/lib"})
check("an under-scoped key is refused at add but NOT held (nothing dangerous)",
      st == 422 and r.get("held") is False and not ns["_gh_paused"](), (st, r))
fake.tokens[tok]["perms"]["contents"] = "write"
fake.revoked.append(tok)
st, r = ns["call"]("POST", "/api/github/repos", {"op": "add", "repo": "octo/lib"})
check("a dead key at add holds writes as unauthorized", st == 422 and r.get("held") is True
      and "unauthorized" in ns["_gh_holds"]() and ns["_gh_paused"](), (st, r))
fake.revoked.remove(tok)
ns["_GH_CHECK_LAST"]["at"] = 0.0
ns["_gh_run_check"](force=True)
check("...and a passing check releases it", not ns["_gh_paused"]())

print("== a default branch name Hermes does not handle ==")
n6 = fresh()
tok6 = fake.profile("clean", "oddo", ["oddo/odd"])
st, r = n6["call"]("POST", "/api/github/connect", {"token": tok6, "repos": ["oddo/odd"]})
odd = (r.get("repos") or [{}])[0]
check("stored, but that repo is not enabled", st == 200 and odd.get("enabled") is False
      and "does not handle" in (odd.get("enable_blocked") or ""), (st, odd))
check("...and the odd name never reaches the status", "<|im_end|>" not in json.dumps(
    n6["call"]("GET", "/api/github/status")[1]))

print("== CI exposure: refused unless it ignores hermes/** ==")
n2 = fresh()
tok_ci = fake.profile("clean", "ci", ["ci/exposed", "ci/ignored", "ci/big"])
st, r = n2["call"]("POST", "/api/github/connect",
                   {"token": tok_ci, "repos": ["ci/exposed", "ci/ignored", "ci/big"]})
by = {x["full_name"]: x for x in r.get("repos", [])}
check("connect still succeeds", st == 200 and r.get("ok"), (st, r.get("lines")))
ex = by.get("ci/exposed") or {}
check("a repo whose push CI reads secrets is not enabled", ex.get("enabled") is False
      and ex.get("writes_ok") is False, ex)
check("...and the reason names the workflow, the secret and the fix",
      "release.yml" in (ex.get("enable_blocked") or "") and "NPM_TOKEN" in
      (ex.get("enable_blocked") or "") and "branches-ignore" in (ex.get("enable_blocked") or ""),
      ex.get("enable_blocked"))
check("a repo that ignores hermes/** is enabled", (by.get("ci/ignored") or {}).get("enabled"))
bg = by.get("ci/big") or {}
check("unreadable workflows fail closed", bg.get("enabled") is False and "could not read"
      in (bg.get("enable_blocked") or ""), bg)

print("== revoke: unauthenticated, then forgotten ==")
n3 = fresh()
tok3 = fake.profile("clean", "octo", ["octo/app"])
n3["call"]("POST", "/api/github/connect", {"token": tok3, "repos": ["octo/app"]})
st, r = n3["call"]("POST", "/api/github/revoke", {}, nonce_ok=False)
rv = fake.calls("POST", r"^/credentials/revoke$")[-1]
check("revoke needs no owner proof and is accepted", st == 200 and r.get("revoked") is True,
      (st, r))
check("the revoke request carried NO Authorization header", "authorization" not in rv["headers"])
check("...and the token in its body", rv["body"] == {"credentials": [tok3]})
check("credential.json is gone", not os.path.exists(cred_path(n3)))
check("status says disconnected", n3["call"]("GET", "/api/github/status")[1]["connected"]
      is False)
check("GitHub no longer accepts the token",
      n3["_gh_mod"].GitHubAPI(BASE, tok3).get("/user").status == 401)
check("a recorder row, without the token", any(x["tool"] == "github_revoke"
                                               for x in n3["RECORDS"]))
no_leak("revoke response has no token", r, tok3)
no_leak("recorder rows have no token", n3["RECORDS"], tok3)
n4 = fresh()
tok4 = fake.profile("clean", "octo", ["octo/app"])
n4["call"]("POST", "/api/github/connect", {"token": tok4, "repos": ["octo/app"]})
fake.revoke_status = 500
st, r = n4["call"]("POST", "/api/github/revoke", {})
fake.revoke_status = None
check("GitHub refusing the revoke still forgets the key", st == 200 and r.get("revoked") is
      False and not os.path.exists(cred_path(n4)), (st, r))
check("...and says where to delete it", "Fine-grained tokens" in r.get("message", "")
      and r.get("manage_url", "").startswith("https://github.com/settings/personal-access-"))
st, r = n4["call"]("POST", "/api/github/revoke", {})
check("revoke when not connected is a 400", st == 400)

print("== disconnect: anyone, keeps sandboxes, cancels pending ==")
cancelled = []
n5 = fresh(extra={"_ghc_cancel_all": lambda reason: cancelled.append(reason) or 2,
                  "_ghc_pending": lambda: [1, 2]})
tok5 = fake.profile("clean", "octo", ["octo/app"])
n5["call"]("POST", "/api/github/connect", {"token": tok5, "repos": ["octo/app"]})
check("pending count comes from the change pipeline",
      n5["call"]("GET", "/api/github/status")[1]["pending"] == 2)
sb = os.path.join(n5["HOME"], "HermesSandbox", "github", "octo", "app")
os.makedirs(os.path.join(sb, ".git"))
open(os.path.join(sb, "work.txt"), "w").write("the owner's work")
st, r = n5["call"]("POST", "/api/github/disconnect", {}, nonce_ok=False)
check("disconnect needs no owner proof", st == 200 and r.get("disconnected"), (st, r))
check("credential gone", not os.path.exists(cred_path(n5)))
check("pending changes cancelled through the pipeline hook",
      cancelled == ["disconnected"] and r.get("cancelled") == 2, (cancelled, r.get("cancelled")))
check("the sandbox (the owner's work) is kept", os.path.isfile(os.path.join(sb, "work.txt"))
      and sb in r.get("sandboxes_kept", []))
check("every repo is disabled", all(not x["enabled"] for x in r["repos"]))
check("writes are refused", n5["_gh_writes_allowed"]("octo/app")[0] is False)

fake.stop()
print("TESTS %d passed %d failed" % (PASS[0], len(FAIL)))
sys.exit(1 if FAIL else 0)
