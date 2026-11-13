// Review R4-seams (S2-4 x c04): the GitHub review sheet opened over the Agent
// page's "Claude dialogues" sheet.
//
// S2-4 put #ag-deep into index.html's OVL registry as a MODAL: focus that
// leaves it is pulled back in.  The review sheet (#gh-review, aux_github.js)
// has its own pull-back and, to keep the two from trading focus, closes the
// modals the registry knew when it was written — the pop-out and the Widget
// Center — but not the new one.  So a review opened while the dialogues sheet
// was up (the "Hermes has a GitHub change for you" toast, a #github-review=
// link) had its focus taken by the sheet underneath, and each pull-back
// answered the other.  closeOtherModals() now closes #ag-deep too, and the
// review sheet's guard treats focus landing in it as it treats the other two.
//
// The REAL index.html + aux_agent.js + aux_github.js run in the mini DOM.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

const GH_ID = "ghc-0123456789ab";
const NOW = Math.floor(Date.now() / 1000);
const ROW = { id: GH_ID, repo: "octo/app", private: true, branch: "hermes/fix-copy", slug: "fix-copy", mode: "new",
  state: "pending", title: "Fix the login copy", stat: { files: 1, add: 1, del: 1, bytes: 120 }, approvable: true, blocked: false,
  needs_ack: [], confirm_public: false, requested_at: NOW - 300, expires_at: NOW + 80000, decided_at: null, decision: null,
  pushed_sha: null, pr: { number: null, url: null, draft: true }, error: null, default_branch: "main", source: "api",
  retracted_at: null, review_url: "#github-review=" + GH_ID };
const FULL = Object.assign({}, ROW, { body: "Fixes a typo.", checks: [],
  files: [{ path: "src/copy.js", old_path: null, status: "M", old_mode: "100644", new_mode: "100644", add: 1, del: 1, binary: false, size: 100 }],
  diff: "diff --git a/src/copy.js b/src/copy.js\n--- a/src/copy.js\n+++ b/src/copy.js\n@@ -1 +1 @@\n-a\n+b\n", diff_truncated: false,
  diff_lines: 6, digest: "d".repeat(64), tree: "c".repeat(40), commit: "e".repeat(40), base: { branch: "main", sha: "b".repeat(40), at: 1 },
  pr_footer: "Opened by Hermes.", sanitized: { comments_stripped: 0, mentions: 0, title_cut: false, body_cut: false, hidden_chars: 0 },
  target_ref: "refs/heads/hermes/fix-copy", author: { name: "octo", email: "x@users.noreply.github.com" } });

(async function main() {
  console.log("S3: the review sheet over the Claude dialogues sheet");
  const errs = [];
  const w = await bootDashboard({
    aux: ["aux_agent.js", "aux_github.js"],
    routes: {
      "/api/claude/recent": function () { return { ok: true, calls: [] }; },
      "/api/github/status": { ok: true, connected: true, login: "octo", pending: 1, pending_ids: [GH_ID], drift: [], attention: [],
        enabled: ["octo/app"], writes_paused: false,
        repos: [{ full_name: "octo/app", private: true, enabled: true, writable: true, writes_ok: true, prepared: true }] },
      "/api/github/ambient": { ok: true, ambient: {} },
      "/api/github/changes": function (url) {
        if (/[?&]id=ghc-/.test(url)) return { ok: true, change: FULL };
        return { ok: true, pending: 1, changes: [ROW] };
      },
      "/api/access": { dirs: [] }
    },
    before: function (x) { x.addEventListener && x.addEventListener("error", function (e) { errs.push(String(e && e.message)); }); }
  });
  const d = w.document;
  const where = function () { const a = d.activeElement; return !a || a === d.body ? "body" : (a.id || a.tagName); };
  w.__run("setView('agent')");
  await w.__tick();
  const dm = d.getElementById("ag-deep"), btn = d.getElementById("ag-deepbtn");
  btn.focus(); w.__click(btn); await w.__tick();
  T.check("(premise) the dialogues sheet is open and holds focus", dm.classList.contains("open") && dm.contains(d.activeElement), where());

  // count focus moves while the review opens: two pull-backs answering each
  // other show up as a storm of focusin events (or a stack overflow)
  let moves = 0;
  d.addEventListener("focusin", function () { moves++; }, true);
  let threw = null;
  try { await w.hermesGitHub.openReview(GH_ID); await w.__tick(12); } catch (e) { threw = e; }
  const sheet = d.getElementById("gh-review");
  T.check("the review sheet opened", !!sheet && !sheet.hidden && !threw, threw && String(threw).slice(0, 200));
  T.check("the dialogues sheet underneath was closed", !dm.classList.contains("open"));
  T.check("focus is in the review sheet", !!sheet && sheet.contains(d.activeElement), where());
  T.check("...and got there without the two sheets trading it", moves <= 6, moves);

  // focus that lands back in the dialogues sheet (reopened by a script while
  // the review is up) is treated like the pop-out: closed, focus kept
  moves = 0;
  w.__click(btn); await w.__tick(6);
  T.check("a dialogues sheet opened beneath the review is closed again", !dm.classList.contains("open"), where());
  T.check("...focus stays in the review sheet", sheet.contains(d.activeElement), where());
  T.check("...without a focus storm", moves <= 8, moves);

  // closing the review gives the keyboard back; nothing pulls it afterwards
  w.__key(d.activeElement, "Escape"); await w.__tick(10);
  T.check("Esc closes the review sheet", sheet.hidden === true, where());
  btn.focus();
  T.check("...and nothing pulls focus with both sheets closed", d.activeElement === btn, where());
  T.check("no page error", errs.length === 0, errs);
  T.done();
})().catch(function (e) { console.log("  FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
