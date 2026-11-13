#!/usr/bin/env python3
"""Headless UI harness for aux_github.js (round 3, R3-GH3; github-sandbox §7).

Serves the REAL dashboard/index.html on the dashboard ORIGIN through Playwright
routes (relative /aux_*.js and /vendor/... loads resolve to this tree's files)
and answers every /api/** call from a fixture. No dashboard is started, no
request leaves the page (anything not on ORIGIN is aborted and counted), no
model is woken and GitHub is never contacted. index.html gains the one
<script src="/aux_github.js"> tag the integrator adds (after aux_network.js)
only if this tree does not have it yet.

Covers:
  * the Settings › Connections GitHub card, connected and not, light + dark
  * the review sheet, light + dark: header, checks, diff, PR text
  * the sheet paints the app's --ground token (inherits the palette)
  * approve stays disabled until every warning is ticked and the public
    confirm is given; the decide POST carries the digest and the acks
  * the focus trap (Tab / Shift+Tab never leave the sheet)
  * Esc and the close button close WITHOUT a decide request
  * the #github-review=<id> deep link
  * >=40 px targets, no horizontal page scroll, no page error from the file
  * the onboarding step (through the hook when this tree has it)

SKIPs cleanly (exit 0, a SKIP line) when Playwright or its Chromium is not
installed. The fixtures are plain functions so a scratch server can reuse them.
"""
import json
import os
import re
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
DASH = os.path.join(REPO, "dashboard")
ORIGIN = "http://127.0.0.1:7788"       # an origin STRING only: every request is intercepted

FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, extra))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
NOW = int(time.time())
ID_PEND = "ghc-0123456789ab"
ID_DONE = "ghc-ba9876543210"
HOSTILE = "<img src=x onerror=alert(1)>"


def status_on(**kw):
    s = {
        "ok": True, "connected": True, "kind": "fine_grained_pat", "login": "octo-owner",
        "user_id": 42, "expires_at": NOW + 29 * 86400 + 3600, "expires_in_days": 29.0,
        "no_expiry": False, "writes_paused": False,
        "paused": {"by_owner_switch": False, "switch": None, "holds": []},
        "repos": [
            {"full_name": "octo-owner/site", "private": True, "default_branch": "main",
             "protection": {"state": "protected",
                            "text": "main protected: blocks force-push · blocks deletion · requires PR"},
             "enabled": True, "listed": True, "writable": True, "writes_ok": True,
             "writes_blocked": None,
             "sandbox": "/Users/you/HermesSandbox/github/octo-owner/site", "prepared": True,
             "last_check": NOW - 3600},
            {"full_name": "octo-owner/pub", "private": False, "default_branch": "main",
             "protection": {"state": "not_protected", "text": "main not protected",
                            "gap_text": "GitHub isn't enforcing rules on main for this repository. "
                                        "Hermes still never pushes to main, but GitHub itself "
                                        "won't stop a stolen key."},
             "enabled": True, "listed": True, "writable": True, "writes_ok": True,
             "sandbox": "/Users/you/HermesSandbox/github/octo-owner/pub", "prepared": True,
             "last_check": NOW - 3600}],
        "enabled": ["octo-owner/site", "octo-owner/pub"], "pending": 1,
        "drift": [{"repo": "octo-owner/site", "branch": "hermes/odd", "sha": "a" * 40,
                   "kind": "unknown", "seen_at": NOW - 900, "acked": False}],
        "attention": [{"id": "drift:octo-owner/site:hermes/odd", "level": "bad",
                       "since": NOW - 900, "repo": "octo-owner/site",
                       "text": "Something other than Hermes pushed to hermes/odd on "
                               "octo-owner/site. If that wasn't you, revoke the GitHub key "
                               "in Settings › Connections."}],
        "ambient": None,
        "push_budget": {"limit": 10, "used": 1, "left": 9},
        "check": {"last": NOW - 1200, "ok": True, "error": None, "next": NOW + 20400},
        "ruleset": {
            "recommended": {"name": "Hermes: protect the default branch", "target": "branch",
                            "enforcement": "active",
                            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"],
                                                        "exclude": []}},
                            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
                            "bypass_actors": []},
            "light": {"name": "Hermes: no force-push or deletion of the default branch",
                      "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]},
            "path": "Settings › Rules › Rulesets › New ruleset › Import a ruleset",
            "text": "With this rule nobody, including you, can push straight to main, "
                    "force-push it or delete it. You merge through pull requests, and you can "
                    "still merge your own."},
        "headline": "Hermes keeps your GitHub key in a file only your account can read and "
                    "never gives it to the agent.",
        "sandbox_root": "/Users/you/HermesSandbox/github",
    }
    s.update(kw)
    return s


AMBIENT = {"checked_at": NOW - 60, "any": True, "gh": True, "keychain": True, "ssh": False,
           "env": False,
           "items": [
               {"id": "gh", "present": True, "title": "GitHub CLI",
                "detail": "The GitHub CLI is signed in. Any program you run, including the "
                          "agent's terminal, can get its token with `gh auth token`.",
                "commands": [{"label": "Sign it out",
                              "cmd": "gh auth logout --hostname github.com"}]},
               {"id": "keychain", "present": True, "title": "git saved password",
                "detail": "git has a saved github.com password in your Keychain, and git hands "
                          "it to any `git push` without asking.",
                "commands": [{"label": "Forget it",
                              "cmd": "printf 'protocol=https\\nhost=github.com\\n\\n' | "
                                     "git credential reject"}]},
               {"id": "ssh", "present": False, "title": "SSH key", "detail": "",
                "commands": []}],
           "note": "Hermes only reads names and settings here. It never reads a password or "
                   "token, and it never changes any of this for you.",
           "accept_text": "Or accept it knowingly: this access is broader than the key you "
                          "connect here, and Hermes cannot see or limit it."}

DIFF = "\n".join([
    "diff --git a/src/copy.js b/src/copy.js",
    "index 1111111..2222222 100644",
    "--- a/src/copy.js",
    "+++ b/src/copy.js",
    "@@ -1,6 +1,7 @@",
    " export const title = 'Sign in';",
    "-export const help = 'Forgot your passwrod?';",
    "+export const help = 'Forgot your password?';",
    "+// " + HOSTILE,
    "+const s = 'admin‮ user';",
    " export const cta = 'Continue';",
    " export function render(el) {",
    "   el.textContent = title;",
    "diff --git a/public/badge.png b/public/badge.png",
    "new file mode 100644",
    "index 0000000..3333333",
    "Binary files /dev/null and b/public/badge.png differ",
    ""])


def summary(cid, **kw):
    s = {"id": cid, "repo": "octo-owner/pub", "private": False, "branch": "hermes/fix-login-copy",
         "slug": "fix-login-copy", "mode": "new", "state": "pending",
         "title": "Fix the login help text", "stat": {"files": 2, "add": 3, "del": 1, "bytes": 3100},
         "approvable": True, "blocked": False, "needs_ack": ["C-unicode", "C-mode"],
         "confirm_public": True, "requested_at": NOW - 420, "expires_at": NOW + 85000,
         "decided_at": None, "decision": None, "pushed_sha": None,
         "pr": {"number": None, "url": None, "draft": True}, "error": None,
         "default_branch": "main", "parent": "abc1234" + "0" * 33, "source": "hub",
         "retracted_at": None, "review_url": "#github-review=" + cid}
    s.update(kw)
    return s


def change(cid, **kw):
    c = summary(cid)
    c.update({
        "body": "The help line said \"passwrod\". This fixes the typo; nothing else changes.\n\n"
                "Thanks `@design-team` for spotting it.",
        "checks": [
            {"id": "C-unicode", "level": "warn", "ack": "C-unicode",
             "text": "1 added line contains hidden or direction-changing characters, shown as "
                     "⟦U+…⟧ in the samples.",
             "samples": [{"path": "src/copy.js", "line": 4, "sample": "const s = 'admin⟦U+202E⟧ user';"}],
             "total": 1},
            {"id": "C-mode", "level": "warn", "ack": "C-mode", "text": "scripts/build.sh becomes executable.",
             "path": "scripts/build.sh"},
            {"id": "C-binary", "level": "info",
             "text": "public/badge.png is a binary file (2048 bytes, sha256 9f2c1a7b3d4e5f60).",
             "path": "public/badge.png", "size": 2048, "sha256": "9f2c1a7b3d4e5f60" * 4},
            {"id": "C-public", "level": "confirm",
             "text": "I understand this is public. It is visible to everyone the moment it is pushed."}],
        "files": [
            {"path": "src/copy.js", "old_path": None, "status": "M", "old_mode": "100644",
             "new_mode": "100644", "add": 3, "del": 1, "binary": False, "size": 1052},
            {"path": "public/badge.png", "old_path": None, "status": "A", "old_mode": "000000",
             "new_mode": "100644", "add": None, "del": None, "binary": True, "size": 2048}],
        "diff": DIFF, "diff_truncated": False, "digest": "d" * 64, "tree": "c" * 40,
        "commit": "e" * 40, "base": {"branch": "main", "sha": "abc1234" + "0" * 33, "at": NOW - 7200},
        "pr_footer": "Opened by Hermes Assistant from a local sandbox, after you approved it on "
                     "studio-mac at <the time you approve>. Change %s." % cid,
        "sanitized": {"comments_stripped": 0, "mentions": 1, "title_cut": False,
                      "body_cut": False, "hidden_chars": 0},
        "target_ref": "refs/heads/hermes/fix-login-copy",
        "author": {"name": "octo-owner", "email": "42+octo-owner@users.noreply.github.com"}})
    c.update(kw)
    return c


CALLS = {"decide": [], "retract": [], "foreign": [], "connect": 0}
CUR = {"status": status_on(), "after_decide": None}


def api_answer(path, method, body, query=""):
    """-> (status, payload). Every route this page may touch, answered locally."""
    if path == "/api/github/status":
        return 200, CUR["status"]
    if path == "/api/github/ambient":
        return 200, {"ok": True, "ambient": AMBIENT}
    if path == "/api/github/create_url":
        return 200, {"ok": True, "url": "https://github.com/settings/personal-access-tokens/new?"
                                        "name=Hermes&contents=write&pull_requests=write",
                     "target_name": None, "repos": []}
    if path == "/api/github/changes" and method == "GET":
        m = re.search(r"(?:^|&)id=(ghc-[0-9a-f]{12})", query)
        if m:
            if m.group(1) == ID_PEND:
                return 200, {"ok": True, "change": CUR["after_decide"] or change(ID_PEND)}
            if m.group(1) == ID_DONE:
                return 200, {"ok": True, "change": change(
                    ID_DONE, state="pr_open", decided_at=NOW - 86000, pushed_sha="f" * 40,
                    title="Tighten the README wording", needs_ack=[], confirm_public=False,
                    private=True, repo="octo-owner/site",
                    pr={"number": 11, "url": "https://github.com/octo-owner/site/pull/11",
                        "draft": True})}
            return 404, {"ok": False, "error": "not_found"}
        return 200, {"ok": True, "pending": 1, "changes": [
            summary(ID_PEND),
            summary(ID_DONE, state="pr_open", decided_at=NOW - 86000, pushed_sha="f" * 40,
                    title="Tighten the README wording", repo="octo-owner/site",
                    branch="hermes/readme-wording",
                    pr={"number": 11, "url": "https://github.com/octo-owner/site/pull/11",
                        "draft": True})]}
    if path == "/api/github/changes/decide":
        CALLS["decide"].append(body)
        if body.get("decision") == "reject":
            return 200, {"ok": True, "state": "rejected", "change": summary(ID_PEND, state="rejected")}
        CUR["after_decide"] = change(
            ID_PEND, state="pr_open", decided_at=NOW, pushed_sha="f" * 40,
            pr={"number": 12, "url": "https://github.com/octo-owner/pub/pull/12", "draft": True})
        return 200, {"ok": True, "state": "pr_open", "pushed_sha": "f" * 40, "notified": True,
                     "pr": {"number": 12, "url": "https://github.com/octo-owner/pub/pull/12",
                            "draft": True},
                     "change": summary(ID_PEND, state="pr_open")}
    if path == "/api/github/changes/retract":
        CALLS["retract"].append(body)
        return 200, {"ok": True, "state": "retracted", "pr_closed": True}
    if path == "/api/github/connect":
        CALLS["connect"] += 1
        return 422, {"ok": False, "refused": True, "stored": False, "lines": ["STUB"], "final": ""}
    if path == "/api/access":
        return 200, {"dirs": ["/Users/you/HermesSandbox"]}
    if path == "/api/onboarding/state":
        return 200, {"ok": True, "done": True, "sysinfo": {}, "detect": {}, "recommendation": {},
                     "prefs": {}}
    if path.startswith("/api/models/download"):
        return 500, {"ok": False, "error": "STUB: no real downloads"}
    if path.startswith("/api/"):
        return 200, {"ok": True}
    return None


def page_html():
    with open(os.path.join(DASH, "index.html"), encoding="utf-8") as f:
        h = f.read()
    if 'src="/aux_github.js"' not in h:
        h = h.replace('<script src="/aux_network.js"></script>',
                      '<script src="/aux_network.js"></script>\n<script src="/aux_github.js"></script>', 1)
    return h


MIME = {".js": "application/javascript", ".html": "text/html", ".css": "text/css",
        ".json": "application/json"}


def static_answer(path):
    """-> (status, content_type, bytes) for this tree's dashboard files, or None."""
    if path in ("/", "/index.html"):
        return 200, "text/html", page_html().encode("utf-8")
    rel = None
    if re.match(r"^/[A-Za-z0-9_.-]+\.(js|css|html)$", path):
        rel = path.lstrip("/")
    elif re.match(r"^/vendor/[a-z]+/[A-Za-z0-9_.-]+\.js$", path):
        rel = path.lstrip("/")
    if rel:
        fp = os.path.realpath(os.path.join(DASH, rel))
        if fp.startswith(os.path.realpath(DASH) + os.sep) and os.path.isfile(fp):
            with open(fp, "rb") as f:
                return 200, MIME.get(os.path.splitext(fp)[1], "text/plain"), f.read()
    return None


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def run():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:                                  # pragma: no cover
        print("SKIP Playwright is not installed (%s)" % type(e).__name__)
        print("TESTS 0 passed 0 failed")
        sys.exit(0)

    shots_dir = os.environ.get("HERMES_TEST_SHOTS") or tempfile.mkdtemp(prefix="hermes-gh-shots-")
    os.makedirs(shots_dir, exist_ok=True)
    shots = []

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:
            print("SKIP Playwright's Chromium is not installed (%s)" % str(e).splitlines()[0][:120])
            print("TESTS 0 passed 0 failed")
            sys.exit(0)
        ctx = browser.new_context(viewport={"width": 1180, "height": 860}, device_scale_factor=2)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append({"msg": str(e), "stack": getattr(e, "stack", "") or ""}))

        def handle(route):
            req = route.request
            url = req.url
            if not url.startswith(ORIGIN + "/"):
                CALLS["foreign"].append(url)
                return route.abort()
            rest = url[len(ORIGIN):]
            path, _, query = rest.partition("?")
            path = path.split("#")[0] or "/"
            body = {}
            if req.method == "POST":
                try:
                    body = json.loads(req.post_data or "{}")
                except Exception:
                    body = {}
            a = api_answer(path, req.method, body, query)
            if a is not None:
                return route.fulfill(status=a[0], content_type="application/json",
                                     body=json.dumps(a[1]))
            s = static_answer(path)
            if s is not None:
                return route.fulfill(status=s[0], content_type=s[1], body=s[2])
            return route.fulfill(status=404, body="")

        page.route("**/*", handle)

        def shot(name, locator=None):
            p = os.path.join(shots_dir, name + ".png")
            if locator is not None:
                locator.screenshot(path=p)
            else:
                page.screenshot(path=p, full_page=False)
            shots.append(p)

        def theme(t):
            page.evaluate("""(t) => { document.documentElement.setAttribute('data-theme', t);
              try { localStorage.setItem('hermes_theme', t); } catch (e) {} }""", t)
            page.wait_for_timeout(150)

        # ==================================================================
        print("\n[1] the Settings › Connections card")
        page.goto(ORIGIN + "/", wait_until="domcontentloaded")
        page.wait_for_function("() => !!window.hermesGitHub", timeout=10000)
        check("window.hermesGitHub is loaded (v1)", page.evaluate("() => window.hermesGitHub.v") == 1)
        ok = page.evaluate("() => window.hermesGitHub.openCard()")
        page.wait_for_selector("#mind-extra-github .gh-row", timeout=8000)
        check("openCard() shows the card", ok is True)
        check("the card sits in #sec-connections",
              page.evaluate("() => !!document.querySelector('#sec-connections #mind-extra-github')"))
        t = page.inner_text("#mind-extra-github")
        check("account, expiry, pause, disconnect, revoke",
              all(w in t for w in ("@octo-owner", "Expires", "Pause GitHub writes", "Disconnect",
                                   "Revoke now")), t[:300])
        check("repositories with Private/Public + protected chips",
              "Private" in t and "Public" in t and "main protected" in t and "main not protected" in t)
        check("other GitHub access on this Mac", "GitHub CLI" in t and "gh auth logout" in t)
        check("a change waits for review", "Waiting for your review" in t and "Review" in t)
        for th in ("light", "dark"):
            theme(th)
            shot("gh-card-%s" % th, page.locator("#mind-extra-github"))
        check("the card never scrolls the page sideways",
              page.evaluate("() => document.documentElement.scrollWidth <= "
                            "document.documentElement.clientWidth + 1"))
        small = page.evaluate("""() => {
          const bad = [];
          document.querySelectorAll('#mind-extra-github button, #mind-extra-github .gh-sw').forEach(e => {
            const r = e.getBoundingClientRect();
            if (r.width && r.height < 39.5) bad.push((e.dataset.gh || e.className) + ':' + Math.round(r.height));
          });
          return bad;
        }""")
        check("every card control is >=40px tall", small == [], small)

        # not connected: the wizard
        CUR["status"] = {"ok": True, "connected": False, "repos": [], "pending": 0, "drift": [],
                         "attention": [], "paused": {"holds": []}}
        page.evaluate("() => window.hermesGitHub.refresh()")
        page.wait_for_selector("#mind-extra-github input[data-gh-token]", timeout=8000)
        tok = page.locator("#mind-extra-github input[data-gh-token]")
        check("not connected: a password token field (autocomplete off, no spellcheck)",
              tok.get_attribute("type") == "password" and tok.get_attribute("autocomplete") == "off"
              and tok.get_attribute("spellcheck") == "false")
        for th in ("light", "dark"):
            theme(th)
            shot("gh-card-connect-%s" % th, page.locator("#mind-extra-github"))
        CUR["status"] = status_on()

        # ==================================================================
        print("\n[2] the review sheet")
        theme("light")
        page.evaluate("(id) => window.hermesGitHub.openReview(id)", ID_PEND)
        page.wait_for_selector("#gh-review:not([hidden]) input[data-ghr-title]", timeout=8000)
        page.wait_for_timeout(450)
        s = page.inner_text("#gh-review")
        check("dialog semantics", page.get_attribute("#gh-review", "role") == "dialog" and
              page.get_attribute("#gh-review", "aria-modal") == "true")
        check("header: repo, Public chip, hermes/<slug>, based on main @ sha7",
              "octo-owner/pub" in s and "Public" in s and "hermes/fix-login-copy" in s and
              "based on main @ abc1234" in s, s[:400])
        check("'What happens if you approve'", "What happens if you approve" in s)
        check("hidden characters are visible", "⟦U+202E⟧" in s)
        check("the diff made no live markup",
              page.evaluate("() => document.querySelectorAll('#gh-review img, #gh-review script').length") == 0)
        check("the binary file is collapsed", "Binary file — not shown" in s)
        check("...and no header says the change is all shown (E02)",
              "the whole change" not in s and "nothing blocks this change" not in s and "1 file not shown" in s)
        check("diff lines wrap: nothing sits past the right edge of the diff box (E02)",
              page.evaluate("""() => [...document.querySelectorAll('#gh-review .ghd-body')]
                .every(b => b.scrollWidth <= b.clientWidth + 1)"""))
        for th in ("light", "dark"):
            theme(th)
            shot("gh-review-%s" % th)
            got = page.evaluate("""() => {
              const s = getComputedStyle(document.getElementById('gh-review'));
              const probe = document.createElement('div');
              probe.style.background = 'var(--ground)';
              document.body.appendChild(probe);
              const g = getComputedStyle(probe).backgroundColor;
              probe.remove();
              return [s.backgroundColor, g];
            }""")
            check("%s: the sheet paints the app's --ground token" % th, got[0] == got[1], got)
        theme("light")
        small = page.evaluate("""() => {
          const bad = [];
          document.querySelectorAll('#gh-review button, #gh-review .ghr-ck label').forEach(e => {
            const r = e.getBoundingClientRect();
            if (r.width && r.height < 39.5) bad.push((e.dataset.ghr || e.className) + ':' + Math.round(r.height));
          });
          return bad;
        }""")
        check("every sheet control is >=40px tall", small == [], small)
        check("the page never scrolls sideways (the diff scrolls inside itself)",
              page.evaluate("() => document.documentElement.scrollWidth <= "
                            "document.documentElement.clientWidth + 1"))

        # ---- the focus trap ---------------------------------------------------
        inside = []
        for _ in range(40):
            page.keyboard.press("Tab")
            inside.append(page.evaluate(
                "() => !!document.activeElement && document.getElementById('gh-review').contains(document.activeElement)"))
        check("Tab x40 never leaves the sheet", all(inside), inside.count(False))
        back = []
        for _ in range(25):
            page.keyboard.press("Shift+Tab")
            back.append(page.evaluate(
                "() => document.getElementById('gh-review').contains(document.activeElement)"))
        check("Shift+Tab x25 never leaves the sheet", all(back), back.count(False))

        # ---- Esc closes without deciding ----------------------------------------
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        check("Esc closes the sheet", page.evaluate("() => document.getElementById('gh-review').hidden"))
        check("...without a decide request", CALLS["decide"] == [], CALLS["decide"])
        page.evaluate("(id) => window.hermesGitHub.openReview(id)", ID_PEND)
        page.wait_for_selector("#gh-review:not([hidden]) input[data-ghr-title]", timeout=8000)
        page.click("#gh-review button[data-ghr='close']")
        page.wait_for_timeout(400)
        check("the close button closes without deciding",
              page.evaluate("() => document.getElementById('gh-review').hidden") and CALLS["decide"] == [])

        # ---- the deep link ------------------------------------------------------
        # a fresh document (a hash-only goto would be a same-document navigation)
        page.goto("about:blank")
        page.goto(ORIGIN + "/#github-review=" + ID_PEND, wait_until="domcontentloaded")
        page.wait_for_selector("#gh-review:not([hidden]) input[data-ghr-title]", timeout=10000)
        check("#github-review=<id> opens the sheet on load", True)

        # ---- approve gating + the decide body ---------------------------------
        go = "#gh-review button[data-ghr='push_pr']"
        check("approve starts disabled", page.is_disabled(go))
        page.click("#gh-review input[data-ack='C-unicode']")
        check("one tick of two: still disabled", page.is_disabled(go))
        page.click("#gh-review input[data-ack='C-mode']")
        check("every server warning ticked, the unseen binary file not: still disabled (E02)",
              page.is_disabled(go))
        page.click("#gh-review input[data-ack='ui:binary']")
        check("every warning ticked, public not confirmed: still disabled", page.is_disabled(go))
        page.click("#gh-review input[data-confirm-public]")
        check("all ticks + the public confirm: enabled", not page.is_disabled(go))
        shot("gh-review-ready-light")
        page.evaluate("() => { window.__opened = []; window.open = (u, t, f) => { window.__opened.push([u, t, f]); return null; }; }")
        page.click(go)
        page.wait_for_selector("#gh-review [data-ghr-result]", timeout=8000)
        check("one decide POST with the digest, every ack and confirm_public",
              CALLS["decide"] == [{"id": ID_PEND, "digest": "d" * 64, "decision": "push_pr",
                                   "acks": ["C-unicode", "C-mode"], "confirm_public": True}],
              CALLS["decide"])
        check("the result names the draft PR", "draft pull request #12" in page.inner_text("#gh-review"))
        shot("gh-review-done-light")
        page.click("#gh-review [data-ghr-result] button[data-ghr='open-pr']")
        opened = page.evaluate("() => window.__opened")
        check("the PR link goes to the default browser (window.open _blank noopener)",
              opened == [["https://github.com/octo-owner/pub/pull/12", "_blank", "noopener"]], opened)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # ==================================================================
        print("\n[3] the onboarding step")
        has_hook = page.evaluate("() => !!(window.hermesOnboarding && window.hermesOnboarding.registerStep)")
        steps = page.evaluate("() => (window.hermesOnboardingSteps || []).map(e => e && e.key)")
        check("the github step is registered", "github" in (steps or []) or has_hook, steps)
        if has_hook:
            page.evaluate("() => window.hermesOnboarding.open({rerun: true})")
            page.wait_for_selector("#onb-sheet:not([hidden])", timeout=8000)
            # steps() is the LIVE key order (base steps + registered ones); STEPS is
            # only the built-in list and never contains an extra step
            idx = page.evaluate("""() => {
              const o = window.hermesOnboarding;
              const keys = typeof o.steps === 'function' ? o.steps()
                : (o.STEPS || []).map(s => s.key);
              return keys.indexOf('github');
            }""")
            check("the live step list has the github step (after prefs, before done)",
                  idx is not None and idx > 0, idx)
            if idx is not None and idx >= 0:
                page.evaluate("(i) => window.hermesOnboarding.go(i)", idx)
                page.wait_for_timeout(300)
            body = page.inner_text("#onb-sheet")
            check("the sheet shows the GitHub step (eyebrow + title)",
                  "Let Hermes propose code changes on GitHub" in body, body[:200])
            for th in ("light", "dark"):
                theme(th)
                shot("gh-onboarding-%s" % th)
        else:
            # the hook lands with the onboarding cluster: render the entry in
            # place so its markup is still screenshotted in both themes
            page.evaluate("""() => {
              const e = (window.hermesOnboardingSteps || []).find(x => x && x.key === 'github');
              const host = document.createElement('div');
              host.id = 'gh-step-preview';
              host.style.cssText = 'position:fixed;inset:0;z-index:9500;overflow:auto;padding:34px 24px;' +
                'background:var(--ground);color:var(--ink)';
              host.innerHTML = '<div style="max-width:680px;margin:0 auto"><p class="ghr-eye">' +
                e.eyebrow + '</p><h1 style="font-size:26px;margin:0 0 6px">' + e.title + '</h1>' +
                '<p class="gh-note" style="font-size:13.5px;margin:0 0 22px">' + e.sub + '</p>' +
                '<div class="onb-body"></div></div>';
              document.body.appendChild(host);
              const ctx = {rerun: true, data: {}, repaint() {}, setErr() {}};
              const b = host.querySelector('.onb-body');
              b.innerHTML = e.render(ctx);
              e.wire(b, ctx);
            }""")
            page.wait_for_timeout(250)
            body = page.inner_text("#gh-step-preview")
            check("the step renders the §1.3 copy", "Open GitHub to create the token" in body and
                  "Skip — connect later in Settings › Connections" in body, body[:200])
            for th in ("light", "dark"):
                theme(th)
                shot("gh-onboarding-step-%s" % th)
            print("       (the onboarding hook is not in this tree yet: the step was "
                  "rendered through its own entry)")

        # ==================================================================
        print("\n[4] hygiene")
        check("no request left the page's origin", CALLS["foreign"] == [], CALLS["foreign"][:3])
        check("no connect request was made by any of this", CALLS["connect"] == 0)
        mine = [e for e in errs if "aux_github" in (e["stack"] or "")]
        check("no page error originates in aux_github.js", mine == [], mine[:3])
        others = sorted(set(e["msg"] for e in errs))
        print("       (%d unrelated page error(s) from modules the catch-all fixture "
              "under-feeds: %s)" % (len(errs), others[:3]))
        browser.close()

    print("\nscreenshots (%d) in %s" % (len(shots), shots_dir))
    for p in shots:
        print("   " + p)


if __name__ == "__main__":
    try:
        run()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        FAILS.append("EXCEPTION")
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    for f in FAILS:
        print("   - %s" % f)
    sys.exit(1 if FAILS else 0)
