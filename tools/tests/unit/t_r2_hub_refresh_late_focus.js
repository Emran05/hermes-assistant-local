// Round-2 review X-frontend-3 — refreshHub() read document.activeElement
// BEFORE awaiting /api/hub, so the C99 guards (a focused input/textarea, a
// keyboard-focused link or button) tested whatever had focus when the 60 s
// refresh STARTED — usually <body>. Typing into the Tasks widget's "Add a
// task" field, or Tab-focusing a headline, while the fetch was in flight was
// then wiped / dropped back to <body> when the response landed.
//
// Evals the REAL refreshHub() from index.html against _k14b_dom.js, with an
// /api/hub fetch the test holds open while focus moves.
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
let release = null;
const HUB = { model_online: true, order: ["tasks", "rss"], widgets: { tasks: { data: {} }, rss: { data: {} } } };
D.install(env, {
  // the response is held until the test lets it through
  fetch: () => new Promise(function (r) { release = function () { r({ ok: true, status: 200, json: async () => HUB }); }; }),
  $: (id) => document.getElementById(id),
  hubEditing: false, hubOrder: ["tasks", "rss"],
  setModel: () => {}, renderHub: () => { filled.push("FULL"); },
  fillWidget: (body, meta, id) => { filled.push(id); body.innerHTML = '<a href="#" class="x">new ' + id + "</a>"; }
});
// eslint-disable-next-line no-eval
(0, eval)(animNull(HTML) + D.extractFunction(HTML, "refreshHub") + "\nwindow.refreshHub=refreshHub;");

function grid() {
  document.body.innerHTML = '<div id="wcenter" hidden></div><div id="wgrid">' +
    '<div class="w" data-id="tasks"><span class="meta"></span><div class="w-body"><input class="newtask" id="nt"></div></div>' +
    '<div class="w" data-id="rss"><span class="meta"></span><div class="w-body"><a href="#" id="h1">Headline one</a></div></div></div>';
  filled.length = 0;
  document.activeElement = document.body; document._focusVisible = false;
}
const tick = () => new Promise(function (r) { setImmediate(r); });

async function main() {
  console.log("typing that starts while /api/hub is in flight");
  grid();
  let p = refreshHub();
  await tick();
  const nt = document.getElementById("nt");
  nt.focus(); nt.value = "buy milk and eggs";            // the owner starts typing mid-fetch
  release(); await p;
  check("the Tasks widget is not re-rendered", filled.indexOf("tasks") < 0, filled);
  check("the half-typed task survives", nt.isConnected && nt.value === "buy milk and eggs", nt.value);
  check("focus stays in the field", document.activeElement === nt);
  check("the other widget still refreshes", filled.indexOf("rss") >= 0, filled);

  console.log("a headline Tab-focused while /api/hub is in flight");
  grid();
  p = refreshHub();
  await tick();
  const h1 = document.getElementById("h1");
  h1.focus(); document._focusVisible = true;
  release(); await p;
  check("its widget is not re-rendered", filled.indexOf("rss") < 0, filled);
  check("keyboard focus stays on the headline", document.activeElement === h1 && h1.isConnected,
    document.activeElement && document.activeElement.tagName);

  console.log("focus that LEFT a widget during the fetch no longer freezes it");
  grid();
  document.getElementById("nt").focus();                    // focused when the refresh starts
  p = refreshHub();
  await tick();
  document.activeElement = document.body;                   // ...and blurred before it lands
  release(); await p;
  check("both widgets refresh", filled.indexOf("tasks") >= 0 && filled.indexOf("rss") >= 0, filled);

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
}
main().catch(function (e) {
  console.log("  FAIL crashed " + (e && e.stack || e));
  console.log("\nTESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
