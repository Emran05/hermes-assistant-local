// C22 (UI half) — a cut-off clipboard result is shown as cut off.
//
// The server now flags truncated_output when the model stopped at its
// max_tokens cap. Both surfaces that show a transform result must say so:
// the main window's Clipboard Actions sheet (aux_clip.js) and the menu-bar
// Quick Ask thread (aux_quickask.js). Evals the REAL files against the small
// DOM in _k14b_dom.js with fetch mocked — no dashboard, no model.
"use strict";
const fs = require("fs");
const path = require("path");
const D = require("./_k14b_dom.js");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}
const flush = () => new Promise((r) => setImmediate(r));
async function settle(n) { for (let i = 0; i < (n || 12); i++) await flush(); }

function mockFetch(map) {
  return async function (url, opts) {
    const u = String(url).split("?")[0];
    const body = typeof map[u] === "function" ? map[u](opts) : map[u];
    return { status: 200, ok: true, json: async () => body };
  };
}

const CATALOG = { ok: true, order: ["proofread", "summarize", "rewrite"],
  actions: { proofread: { label: "Proofread", opts: [] }, summarize: { label: "Summarize", opts: [] },
             rewrite: { label: "Rewrite", opts: [] } },
  defaults: {} };

async function sheet(truncated) {
  const env = D.makeEnv();
  D.install(env, {
    navigator: { clipboard: { readText: async () => "Some long text to proofread." } },
    fetch: mockFetch({
      "/api/clip/actions": CATALOG,
      "/api/clip/transform": { ok: true, action: "proofread", result: "Some long text",
        truncated_output: truncated, finish_reason: truncated ? "length" : "stop" }
    })
  });
  global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
  global.animate = undefined;
  // eslint-disable-next-line no-eval
  (0, eval)(fs.readFileSync(path.join(REPO, "dashboard", "aux_clip.js"), "utf8"));
  window.hermesClip.open();
  await settle();
  const sh = document.getElementById("clip-sheet");
  sh.querySelector(".go").click();
  await settle();
  return { res: sh.querySelector(".res"), copy: sh.querySelector(".copy"), note: window.hermesClip.CUT_NOTE };
}

async function quickAsk(truncated) {
  const env = D.makeEnv();
  const posted = [];
  D.install(env, {
    __HERMES_QUICKASK__: true,
    navigator: { clipboard: { readText: async () => "A very long email body." } },
    webkit: undefined,
    fetch: async function (url, opts) {
      const u = String(url).split("?")[0];
      if (opts && opts.method === "POST") posted.push(u);
      let body = {};
      if (u === "/api/clip/actions") body = CATALOG;
      else if (u === "/api/clip/transform")
        body = { ok: true, action: "rewrite", result: "A very long", truncated_output: truncated };
      return { status: 200, ok: true, json: async () => body };
    }
  });
  // eslint-disable-next-line no-eval
  (0, eval)(fs.readFileSync(path.join(REPO, "dashboard", "aux_quickask.js"), "utf8"));
  await settle();
  // the action row is the real entry point: a clip chip runs runClip()
  const chip = document.querySelectorAll("button.qa-chip").find((b) => /^rewrite$/i.test(b.textContent.trim()));
  return { chip: chip, posted: posted, after: async () => { await settle(); return document; } };
}

(async function () {
  console.log("\n1. Clipboard Actions sheet: a cut-off result carries the warning");
  let s = await sheet(true);
  check("the result text is shown", s.res && s.res.textContent.indexOf("Some long text") === 0, s.res && s.res.textContent);
  const cut = s.res && s.res.querySelector(".cut");
  check("a .cut note is appended under it", !!cut);
  check("it says the result is incomplete", cut && /incomplete/i.test(cut.textContent), cut && cut.textContent);
  check("and is the exported CUT_NOTE", cut && cut.textContent === s.note);
  check("Copy still works (the partial text is still usable)", s.copy && !s.copy.disabled);

  console.log("\n2. a complete result has no warning");
  s = await sheet(false);
  check("no .cut note", s.res && !s.res.querySelector(".cut"), s.res && s.res.innerHTML);
  check("result text intact", s.res && s.res.textContent === "Some long text", s.res && s.res.textContent);

  console.log("\n3. Quick Ask thread: a cut-off result gets an error line");
  let q = await quickAsk(true);
  check("the popover rendered its Rewrite clipboard action", !!q.chip);
  if (q.chip) {
    q.chip.click();
    const doc = await q.after();
    check("the transform was requested", q.posted.indexOf("/api/clip/transform") >= 0, q.posted);
    const lines = doc.querySelectorAll(".qa-line.err").map((n) => n.textContent);
    check("an error line says the result is incomplete", lines.some((t) => /incomplete/i.test(t)), lines);
  }
  q = await quickAsk(false);
  if (q.chip) {
    q.chip.click();
    const doc = await q.after();
    const lines = doc.querySelectorAll(".qa-line.err").map((n) => n.textContent);
    check("a complete result adds no such line", !lines.some((t) => /incomplete/i.test(t)), lines);
  }

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch(function (e) { console.log("  FAIL threw " + (e && e.stack || e)); console.log("\nTESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed"); process.exit(1); });
