// C72 (docs/audits/2026-10-21-codebase-audit.md) — the Data & Network card
// read its state ONCE per page load (mount() loaded only while !S.loaded), so
// a switch flipped in the Watchtower card, the For-You switch, and every new
// "Last outbound" stayed stale until a reload.
//
// AFTER: every mount re-reads (throttled), and coming back to the window
// refreshes a card that is on screen. The REAL aux_network.js runs in node:vm
// with fetch mocked; nothing reaches the dashboard.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

const STATE = { news: true, foryou: false, feeds: 1792600000 };
let GETS = 0;
function route(url) {
  const u = url.split("?")[0];
  if (u === "/api/watchtower") return { ok: true, master: { briefings: true, news: STATE.news }, recent: [] };
  if (u === "/api/claude/foryou") return { ok: true, enabled: STATE.foryou };
  if (u === "/api/claude/escalate") return { ok: true, enabled: false };
  if (u === "/api/network/last") return { ok: true, last: { feeds: STATE.feeds } };
  // the pre-C52 card's reads, so the same harness can run the old file
  if (u === "/api/update/check") return { checked_at: 0 };
  if (u === "/api/claude/bridge") return { ok: true, recent: [] };
  if (u === "/api/google/status") return { ok: true, connected: false };
  return null;
}
let HOLD = null;            // a POST the test settles by hand: {resolve}
function fakeFetch(url, opts) {
  if (!opts || !opts.method || opts.method === "GET") GETS++;
  if (opts && opts.method === "POST" && HOLD) {
    const h = HOLD;
    return new Promise((r) => { h.resolve = (body) => r({ ok: true, json: () => Promise.resolve(body) }); });
  }
  const res = route(url);
  return Promise.resolve({ ok: !!res, json: () => Promise.resolve(res) });
}

let visHandler = null;
let cardOnScreen = false;
const doc = {
  visibilityState: "visible",
  getElementById: (id) => (id === "mind-extra-network" && cardOnScreen ? {} : null),
  querySelector: () => null,
  createElement: () => ({}),
  addEventListener: (ev, fn) => { if (ev === "visibilitychange") visHandler = fn; }
};
const ctx = { console, setTimeout, clearTimeout, Promise, JSON, Date, Math, Number,
              String, Array, Object, Boolean, isNaN, fetch: fakeFetch, document: doc };
ctx.window = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);
for (const rel of ["dashboard/aux_onboarding.js", "dashboard/aux_network.js"]) {
  const file = path.join(REPO, rel);
  vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
}
const net = ctx.hermesNetwork;
const tick = () => new Promise((r) => setTimeout(r, 0));

(async function () {
  await net.mount();
  check("the first mount reads the state", net.state.loaded === true &&
        net.state.prefs.news === true && net.state.prefs.foryou_claude === false,
        net.state.prefs);

  // elsewhere: the Watchtower card turns news off, For-You's switch goes on,
  // and the feeds are gathered again
  STATE.news = false; STATE.foryou = true; STATE.feeds = 1792603600;
  const before = GETS;
  await net.mount();
  check("an immediate re-mount is throttled (no request storm)", GETS === before,
        GETS - before);

  net.state.loadedAt = Date.now() - 60000;       // the view is opened again later
  await net.mount();
  check("a later mount re-reads: the news switch flipped elsewhere shows Off",
        net.state.prefs.news === false, net.state.prefs);
  check("...the For-You switch shows its new state",
        net.state.prefs.foryou_claude === true, net.state.prefs);

  STATE.news = true;
  net.state.loadedAt = Date.now() - 60000;
  cardOnScreen = true;
  check("coming back to the window is watched", typeof visHandler === "function");
  if (visHandler) { visHandler(); await tick(); await tick(); await tick(); }
  check("...and refreshes the card that is on screen", net.state.prefs.news === true,
        net.state.prefs);

  // review C72: a re-read that lands while a flip's POST is in flight must
  // keep the value being switched TO (it used to keep the old one, so the
  // repaint flipped the switch back mid-POST) — and a failed write reverts
  STATE.foryou = false;
  cardOnScreen = false;                     // this fake document has no card DOM
  await net.refresh();
  HOLD = {};
  let p = net.flip("foryou_claude", true, "For-You via Claude");
  await tick();
  await net.refresh();                      // the server still says false
  check("a re-read during the POST keeps the switch where the user put it",
        net.state.prefs.foryou_claude === true, net.state.prefs);
  HOLD.resolve({ ok: false, error: "nope" }); HOLD = null;
  await p;
  check("...and a write that failed puts it back to the stored state",
        net.state.prefs.foryou_claude === false, net.state.prefs);
  HOLD = {};
  p = net.flip("foryou_claude", true, "For-You via Claude");
  await tick();
  await net.refresh();
  HOLD.resolve({ ok: true, enabled: true }); HOLD = null;
  await p;
  check("...while one that landed stays on", net.state.prefs.foryou_claude === true,
        net.state.prefs);

  console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.log("  FAIL harness crashed: " + (e && e.stack || e));
  console.log("TESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
