// C90 — the core UI timers stop fetching while the window is hidden, and the
// System meters stop outrunning their own server cache.
//
// liveSystem polled /api/sys every 3 s (each call spawns vm_stat + sysctl;
// with a single caller the 2 s server cache never hits) whatever view was
// shown, and loadModels (30 s), refreshHub (60 s) and the full-chat AI panel
// (5 s) all kept fetching with the window hidden. Evals the REAL liveSystem()
// and the REAL timer statements from index.html against _k14b_dom.js.
// (2026-11-12: the Hub's full-screen chat is gone, and with it its AI-panel
// timer; the Agent page polls that panel itself, only while it is visible —
// aux_agent.js isAgentVisible(). This suite now checks the timer is gone.)
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
const flush = () => new Promise((r) => setImmediate(r));
async function settle(n) { for (let i = 0; i < (n || 10); i++) await flush(); }

const env = D.makeEnv();
const fetched = [];
const n = { models: 0, hub: 0, render: 0, ai: 0 };
D.install(env, {
  fetch: async function (url) {
    fetched.push(String(url));
    return { ok: true, status: 200, json: async () => ({ cpu_pct: 12, ram_pct: 70, disk_pct: 90 }) };
  },
  $: (id) => document.getElementById(id),
  loadModels: () => { n.models++; }, refreshHub: () => { n.hub++; },
  renderHub: () => { n.render++; }, renderAIInfo: () => { n.ai++; },
  // the overlay keyboard/focus watcher (C92/C95) between the timer statements
  // observes [hidden] changes; nothing here opens an overlay, so a no-op will do
  MutationObserver: class { observe() {} disconnect() {} takeRecords() { return []; } }
});
document.body.innerHTML =
  // the overlays that watcher wires itself to at load
  '<div class="modelwrap"><button id="model-pill"></button><div id="model-menu" hidden></div></div>' +
  '<div id="wpop" hidden></div><div id="wcenter" hidden></div>' +
  '<div class="deck"></div>' +
  '<div class="view" id="view-hub"><div class="w" data-id="system"><div class="w-body">' +
  '<i class="sysm" data-k="cpu"></i><b class="sysv" data-k="cpu"></b>' +
  '<i class="sysm" data-k="ram"></i><b class="sysv" data-k="ram"></b></div></div></div>';
// _k14b_dom's style is a plain object; the meters now write the custom
// property --p through setProperty (motion-system §8.1: a scaleX, never a width)
document.querySelectorAll(".sysm").forEach(function (i) { i.style.setProperty = function (k, v) { this[k] = String(v); }; });

// --- the real code ------------------------------------------------------------
const live = D.extractFunction(HTML, "liveSystem");
const liveTimer = /\nsetInterval\(liveSystem,(\d+)\);/.exec(HTML);
const aiTimer = /\nsetInterval\([^\n]*renderAIInfo\(\)/.exec(HTML);
// the timer block sits right after the model menu's outside-click listener
// (R3 motion: the menu now closes through closeModelMenu(), a short exit)
const ANCHOR = "if(!e.target.closest('.modelwrap'))closeModelMenu();});\n";
const a = HTML.indexOf(ANCHOR) < 0 ? -1 : HTML.indexOf(ANCHOR) + ANCHOR.length;
const b = HTML.indexOf("// ---------- capabilities (Mind) ----------");
check("found the timer statements in index.html", !!liveTimer && a > 0 && b > a);
check("no Hub-side AI-panel timer is left (the Agent page polls its own)", !aiTimer, aiTimer && aiTimer[0]);
// eslint-disable-next-line no-eval
(0, eval)(live + "\nwindow.liveSystem=liveSystem;\n" + HTML.slice(a, b));
const sysCalls = () => fetched.filter((u) => u === "/api/sys").length;

async function main() {
  console.log("liveSystem");
  check("polls no faster than the server samples (>= 5 s)", Number(liveTimer[1]) >= 5000, liveTimer[1]);
  await liveSystem(); await settle();
  check("visible + Hub shown: fetches and paints", sysCalls() === 1 &&
    document.querySelector('.sysv[data-k="ram"]').textContent === "70%", fetched);
  const bar = document.querySelector('.sysm[data-k="ram"]');
  check("...the bar as --p (0.7, its scaleX), never as a width", bar.style["--p"] === "0.7" && !bar.style.width, bar.style);
  document.hidden = true;
  await liveSystem(); await settle();
  check("window hidden: no /api/sys call", sysCalls() === 1, fetched);
  document.hidden = false;
  document.getElementById("view-hub").hidden = true;
  await liveSystem(); await settle();
  check("Agent or Settings in front: no /api/sys call", sysCalls() === 1, fetched);
  document.getElementById("view-hub").hidden = false;

  console.log("loadModels / refreshHub");
  check("the models menu still loads at start", n.models === 1, n);
  check("the first hub render still runs", n.render === 1, n);
  env.tick();
  check("visible: each periodic loader runs", n.models === 2 && n.hub === 1 && n.ai === 0, n);
  document.hidden = true;
  env.tick(); env.tick(); env.tick();
  check("hidden: none of them fetch", n.models === 2 && n.hub === 1 && n.ai === 0, n);
  check("and no timer threw", env.errors.length === 0, env.errors.map(String));
  const sysBefore = sysCalls();
  document.hidden = false;
  document.dispatchEvent(D.makeEvent("visibilitychange"));
  await settle();
  check("shown again: the models menu and the hub catch up once", n.models === 3 && n.hub === 2, n);
  check("and the System meters refresh right away", sysCalls() === sysBefore + 1, fetched);
  document.dispatchEvent(D.makeEvent("visibilitychange"));
  await settle();
  check("a second visibility flip with nothing missed fetches nothing extra", n.models === 3 && n.hub === 2, n);

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
}
main().catch(function (e) {
  console.log("  FAIL crashed " + (e && e.stack || e));
  console.log("\nTESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
