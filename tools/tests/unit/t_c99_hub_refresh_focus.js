// C99 (hub widgets) — the 60 s hub refresh keeps keyboard focus.
//
// refreshHub() re-fills every widget body with innerHTML each minute. It
// spared a widget holding a focused INPUT/TEXTAREA, but a Tab-focused link or
// button (an RSS headline, a Quick Link, a task checkbox) was destroyed and
// focus fell back to <body>. A widget holding keyboard focus (:focus-visible)
// is now skipped too; a mouse-focused element is not (it would otherwise
// freeze that widget until the next click elsewhere).
// Evals the REAL refreshHub() from index.html against _k14b_dom.js.
"use strict";
const fs = require("fs");
const path = require("path");
const D = require("./_k14b_dom.js");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const HTML = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

// refreshHub() calls the motion runtime through HA() (docs/plans/motion-
// system-2026-10.md §6.7: its fills run inside HA().quiet). With no
// aux_anim.js loaded that is index.html's own ANIM_NULL, taken verbatim.
function animNull(src) {
  const a = src.indexOf("\nconst ANIM_NULL="), tail = "\nconst HA=()=>window.HermesAnim||ANIM_NULL;";
  const b = a < 0 ? -1 : src.indexOf(tail, a);
  if (b < 0) throw new Error("index.html's ANIM_NULL/HA() not found");
  return src.slice(a + 1, b + tail.length) + "\n";
}
const env = D.makeEnv();
const filled = [];
D.install(env, {
  fetch: async () => ({ ok: true, status: 200, json: async () => ({
    model_online: true, order: ["rss", "quicklinks"],
    widgets: { rss: { data: {} }, quicklinks: { data: {} } } }) }),
  $: (id) => document.getElementById(id),
  hubEditing: false, hubOrder: ["rss", "quicklinks"],
  setModel: () => {}, renderHub: () => { filled.push("FULL"); },
  fillWidget: (body, meta, id) => { filled.push(id); body.innerHTML = '<a href="#" class="x">new ' + id + "</a>"; }
});
// eslint-disable-next-line no-eval
(0, eval)(animNull(HTML) + D.extractFunction(HTML, "refreshHub") + "\nwindow.refreshHub=refreshHub;");

function grid() {
  document.body.innerHTML = '<div id="wcenter" hidden></div><div id="wgrid">' +
    '<div class="w" data-id="rss"><span class="meta"></span><div class="w-body"><a href="#" id="h1">Headline</a></div></div>' +
    '<div class="w" data-id="quicklinks"><span class="meta"></span><div class="w-body"><a href="#" id="q1">GitHub</a></div></div></div>';
  filled.length = 0;
}

async function main() {
  console.log("a Tab-focused headline");
  grid();
  const h1 = document.getElementById("h1");
  h1.focus(); document._focusVisible = true;
  await refreshHub();
  check("its widget is not re-rendered", filled.indexOf("rss") < 0, filled);
  check("the other widgets still refresh", filled.indexOf("quicklinks") >= 0, filled);
  check("focus stays on the headline", document.activeElement === h1 && h1.isConnected);

  console.log("mouse focus (no focus ring)");
  grid();
  document.getElementById("h1").focus(); document._focusVisible = false;
  await refreshHub();
  check("is not a reason to freeze the widget", filled.indexOf("rss") >= 0 && filled.indexOf("quicklinks") >= 0, filled);

  console.log("typing still wins, nothing focused still refreshes everything");
  grid();
  const inp = document.createElement("input");
  document.querySelector('.w[data-id="quicklinks"] .w-body').appendChild(inp);
  inp.focus(); document._focusVisible = false;
  await refreshHub();
  check("a focused input keeps its widget", filled.indexOf("quicklinks") < 0 && filled.indexOf("rss") >= 0, filled);
  grid();
  document.activeElement = document.body; document._focusVisible = false;
  await refreshHub();
  check("no focus: every widget refreshes", filled.join() === "rss,quicklinks", filled);

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
}
main().catch(function (e) {
  console.log("  FAIL crashed " + (e && e.stack || e));
  console.log("\nTESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
