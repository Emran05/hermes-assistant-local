// t_anim_surfaces_real.js — Needs-you and the Agent page on the REAL motion
// libraries, in a real browser (docs/plans/motion-system-2026-10.md 8.8 and
// 8.10, test plan 10.1/10.3; companion to the unit suite t_anim_surfaces.js).
//
// The unit tier drives aux_needsyou.js / aux_agent.js against recording stubs,
// and stubs can only confirm what their author believed. Two things only a
// browser shows were found here and are pinned by this file:
//   * the ticker crossfade: a fade-in started inside exit()'s done() is undone
//     by exit's own clean-up (the line stayed at opacity ~0), so the fade-in
//     starts from the exit handle's finished promise;
//   * Done slides its row 16 px to the right, which made the card's scroll body
//     scroll sideways for the length of the slide: a horizontal scrollbar
//     flashed in and out (15 px of layout) for anyone on classic scrollbars.
//
//   * Hermetic: no dashboard, no model, no /api call, no network beyond the
//     127.0.0.1 server this file starts. fetch() is stubbed in the fixture.
//   * The page shim (animate / ANIM_NULL / HA) is cut out of dashboard/index.html
//     itself, so the real one is what runs. Motion 14.6, GSAP 3.15 (+ Flip,
//     CustomEase) and anime 4.5 are the vendored files.
//   * Needs, and SKIPs without (never fails for lack of them): the vendored
//     libraries, a Chromium (HERMES_CHROME, or the one Playwright installs) and
//     a node with a global WebSocket (22+).
//
// Only things that hold at any frame rate are asserted: end states, handles,
// inline styles left behind, which element moved first, what a flight did to
// layout. No pixel or frame budget (that is §10.3's WebKit probe).
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const cp = require("child_process");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const VENDOR = process.env.HERMES_ANIM_VENDOR_DIR || path.join(REPO, "dashboard", "vendor");
const DASH = path.join(REPO, "dashboard");
const FILES = {
  "/vendor/motion/motion-14.6.0.min.js": path.join(VENDOR, "motion", "motion-14.6.0.min.js"),
  "/vendor/gsap/gsap-3.15.0.min.js": path.join(VENDOR, "gsap", "gsap-3.15.0.min.js"),
  "/vendor/gsap/Flip-3.15.0.min.js": path.join(VENDOR, "gsap", "Flip-3.15.0.min.js"),
  "/vendor/gsap/CustomEase-3.15.0.min.js": path.join(VENDOR, "gsap", "CustomEase-3.15.0.min.js"),
  "/vendor/anime/anime-4.5.0.umd.min.js": path.join(VENDOR, "anime", "anime-4.5.0.umd.min.js"),
  "/aux_anim.js": path.join(DASH, "aux_anim.js"),
  "/aux_needsyou.js": path.join(DASH, "aux_needsyou.js"),
  "/aux_agent.js": path.join(DASH, "aux_agent.js")
};

let pass = 0, fail = 0;
function check(name, ok, extra) {
  if (ok) { pass++; console.log("  ok   " + name); } else { fail++; console.log("  FAIL " + name + (extra === undefined ? "" : "  <- " + extra)); }
}
function skipAll(why) { console.log("SKIP " + why); console.log("TESTS 0 passed 0 failed"); process.exit(0); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  if (process.env.HERMES_CHROME) return fs.existsSync(process.env.HERMES_CHROME) ? process.env.HERMES_CHROME : null;
  const roots = [path.join(os.homedir(), "Library", "Caches", "ms-playwright"), path.join(os.homedir(), ".cache", "ms-playwright")];
  const cands = [];
  roots.forEach((root) => {
    let names = [];
    try { names = fs.readdirSync(root); } catch (e) { return; }
    names.sort().reverse().forEach((n) => {
      const dir = path.join(root, n);
      if (/^chromium_headless_shell-/.test(n)) {
        try { fs.readdirSync(dir).forEach((s) => cands.push(path.join(dir, s, "chrome-headless-shell"))); } catch (e) {}
      } else if (/^chromium-/.test(n)) {
        try {
          fs.readdirSync(dir).forEach((s) => {
            cands.push(path.join(dir, s, "Chromium.app", "Contents", "MacOS", "Chromium"));
            cands.push(path.join(dir, s, "chrome"));
          });
        } catch (e) {}
      }
    });
  });
  return cands.find((c) => fs.existsSync(c)) || null;
}

// ---- the page shim, cut out of index.html (the real animate()/HA()) ----------
function pageShim() {
  let html = "";
  try { html = fs.readFileSync(path.join(DASH, "index.html"), "utf8"); } catch (e) {}
  const m = /const M=window\.Motion\|\|null;[\s\S]*?const HA=\(\)=>window\.HermesAnim\|\|ANIM_NULL;/.exec(html);
  return m ? m[0] : "";
}

// ---- the shared fixture head: libraries, shim, stubs, helpers ---------------
const HEAD = (shim) => String.raw`<!doctype html><html><head><meta charset="utf-8"><title>fixture</title>
<style>:root{--ink:#222;--muted:#666;--faint:#999;--hairline:#ddd;--glass:#fff;--glass-2:#eee;--chip:#eee;--field:#fff;--field-edge:#ccc;--edge:#bbb;--iris:#66f;--claude:#f60;--ok:#2a2;--bad:#d22;--warn:#da2;--quick:#29f;--radius-sm:8px;--specular:#fff;--cast:#000}
body{font:14px sans-serif;margin:0}</style></head><body>
<header><span class="seg"><b id="tab-hub" class="on" role="tab">Hub</b><b id="tab-mind" role="tab">Mind</b></span></header>
<script>
var RAF = 0, WARN = [], ERR = [], R = [];
console.warn = function () { WARN.push(Array.prototype.join.call(arguments, " ")); };
window.addEventListener("error", function (e) { ERR.push(String(e.message)); });
window.addEventListener("unhandledrejection", function (e) { ERR.push("rejection " + e.reason); });
function ck(n, c, x) { R.push({ name: n, ok: !!c, extra: c || x === undefined ? "" : JSON.stringify(x) }); }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function until(f, ms) { return new Promise(function (res) { var t0 = Date.now(); (function p() { var v; try { v = f(); } catch (e) { v = false; } if (v || Date.now() - t0 > (ms || 4000)) res(!!v); else setTimeout(p, 25); })(); }); }
// one value per animation frame while fn() runs: what a flight did, frame by frame
function frames(sample, stopWhen, maxMs) {
  return new Promise(function (res) {
    var out = [], t0 = performance.now();
    (function tick() { out.push(sample()); if ((stopWhen && stopWhen()) || performance.now() - t0 > (maxMs || 1500)) res(out); else requestAnimationFrame(tick); })();
  });
}
</script>
<script src="/vendor/motion/motion-14.6.0.min.js"></script>
<script src="/aux_anim.js"></script>
<script>
const $ = function (id) { return document.getElementById(id); };
${shim}
</script>`;

// ---- Needs-you ---------------------------------------------------------------
const PAGE_NY = (shim) => HEAD(shim) + String.raw`
<main class="stage"><div class="view" id="view-hub">
 <div class="w" id="card"><span class="meta" id="meta"></span><div class="w-body" id="body"></div></div>
 <div class="w" id="card2" style="display:none"><div class="w-body" id="pop"></div></div>
</div></main>
<style>.w{width:560px;margin:10px;height:300px;display:flex;flex-direction:column;overflow:hidden}.meta{display:inline-block}
.w-body{padding:2px 12px 10px;overflow-y:auto;min-height:0;flex:1}</style>
<script>
const RENDER = {}, EXPAND_RENDER = {};
const NOW = Math.floor(Date.now() / 1000);
function item(n) { return { id: "message:" + n, source: "message", sender: "P" + n, title: "P" + n, summary: "can you call me? " + n, bucket: "now", received_at: NOW - 120, deadline_ts: 0, reason: "a question" }; }
function pay(ids) { return { ok: true, never_count: 0, later_count: 0, metrics: {}, next_brief: null, generated_at: NOW, now: ids.map(item), today: [], later: [], sources: { message: "present" } }; }
var ST = { act: { ok: true }, next: pay([2, 3]), posts: [], reloads: 0 };
window.fetch = function (url, init) {
  var u = String(url), body = null;
  function J(o) { return Promise.resolve({ ok: true, json: function () { return Promise.resolve(JSON.parse(JSON.stringify(o))); } }); }
  if (/\/api\/needsyou\/act/.test(u)) { ST.posts.push(JSON.parse(init.body)); return J(ST.act); }
  if (/\/api\/needsyou\/seen/.test(u)) return J({ ok: true });
  if (/\/api\/needsyou|\/api\/expand/.test(u)) { ST.reloads++; return J(ST.next); }
  return Promise.reject(new Error("fixture: no route for " + u));
};
</script>
<script src="/aux_needsyou.js"></script>
<script>
var A = window.HermesAnim, body = $("body"), meta = $("meta");
function rowEl(n, host) { return (host || body).querySelector('.ny-row[data-id="message:' + n + '"]'); }
function rowTop(n, host) { var r = rowEl(n, host); return r ? Math.round(r.getBoundingClientRect().top) : null; }
function btn(n, a, host) { var r = rowEl(n, host); return r && r.querySelector('button[data-a="' + a + '"]'); }
function ids(host) { return Array.prototype.map.call((host || body).querySelectorAll(".ny-row"), function (r) { return r.getAttribute("data-id"); }).join(); }
function dirty(host) { return Array.prototype.filter.call((host || body).querySelectorAll("[style]"), function (e) { return /opacity|transform|translate|visibility/.test(e.style.cssText); }).map(function (e) { return e.className + ": " + e.style.cssText; }); }
function metaClean() { return meta.style.minWidth === "" && meta.getAttribute("aria-label") === null; }
function idle() { return until(function () { return A.stats().running === 0; }, 4000); }
var REDUCED = location.search.indexOf("reduce") >= 0;

async function main() {
  await A.load("gsap"); await A.load("anime");                  // loaded, so the live run never takes the first-use instant path
  RENDER.needsyou(body, pay([1, 2, 3]), meta);
  await sleep(40);
  ck("the widget renders three Now rows and '3 now'", body.querySelectorAll(".ny-row").length === 3 && meta.textContent === "3 now", meta.textContent);
  ck("every row carries data-flip-id = its item id", Array.prototype.every.call(body.querySelectorAll(".ny-row"), function (r) { return r.getAttribute("data-flip-id") === r.getAttribute("data-id"); }));
  ck("the list body may not scroll sideways (a Done slides its row 16px right)", getComputedStyle(body).overflowX === "hidden", getComputedStyle(body).overflowX);

  // --- Done: the row leaves, the rest close the gap, the count ticks down
  var t1 = rowTop(1), r1 = rowEl(1), seen = { exit: false, flip: false, bar: 0 }, removedHidden = null;
  // was the leaving row already hidden (its exit done) when the repaint took it out of the DOM?
  new MutationObserver(function () { if (removedHidden === null && !r1.isConnected) removedHidden = r1.style.visibility === "hidden"; }).observe(body, { childList: true, subtree: true });
  var started0 = A.stats().started;
  var watch = frames(function () {
    // computed, not inline: Motion runs an exit on the compositor (no inline opacity), GSAP's Flip writes inline transforms
    if (r1.isConnected && r1.style.visibility !== "hidden" && +getComputedStyle(r1).opacity < 0.98) seen.exit = true;
    var r2 = rowEl(2), tf = r2 ? getComputedStyle(r2).transform : "none";
    if (tf !== "none" && tf !== "matrix(1, 0, 0, 1, 0, 0)") seen.flip = true;
    seen.bar = Math.max(seen.bar, body.offsetHeight - body.clientHeight);
    return 0;
  }, function () { return !rowEl(1) && A.stats().running === 0 && !!seen.done; }, 2500);
  ST.next = pay([2, 3]);
  btn(1, "done").click();
  await until(function () { return !rowEl(1) && A.stats().running === 0; }, 4000);
  seen.done = true;
  await watch;
  ck("Done: row 1 is gone and row 2 sits where row 1 was", !rowEl(1) && rowTop(2) === t1, [rowTop(2), t1]);
  ck("...nothing it started is left on any element (no opacity, transform or visibility)", dirty().length === 0, dirty());
  ck("...the Now count read '2 now' and took its helpers off", meta.textContent === "2 now" && metaClean(), meta.outerHTML);
  ck("...nothing is still running", A.stats().running === 0, A.stats());
  if (REDUCED) {
    ck("Reduce motion: nothing was started for the act (the exit, the Flip and the count all landed at once)", A.stats().started === started0, A.stats().started - started0);
  } else {
    ck("the row left on an opacity/transform exit before it was hidden", seen.exit);
    ck("the rows that stay flew into the gap (a Flip transform on row 2)", seen.flip);
    ck("no horizontal scrollbar flashed in the list body while the row slid out", seen.bar === 0, seen.bar);
  }
  ck("the repaint took the old row out only after its exit had ended (it was hidden in place first: exit, then Flip)", removedHidden === true, removedHidden);
  ck("the post carried the id", ST.posts.length === 1 && ST.posts[0].id === "message:1" && ST.posts[0].action === "done", ST.posts);

  // --- two acts 120 ms apart
  RENDER.needsyou(body, pay([1, 2, 3]), meta); await sleep(40); ST.posts.length = 0;
  ST.next = pay([2, 3]); btn(1, "done").click();
  await sleep(120);
  ST.next = pay([3]); btn(2, "done").click();
  await until(function () { return ids() === "message:3" && A.stats().running === 0; }, 5000);
  await sleep(150);
  ck("two quick Dones: only row 3 is left, nothing running", ids() === "message:3" && A.stats().running === 0, [ids(), A.stats().running]);
  ck("...no stuck inline style, no hidden row, and '1 now'", dirty().length === 0 && meta.textContent === "1 now" && metaClean(), [dirty(), meta.textContent]);

  // --- a refused act moves nothing
  RENDER.needsyou(body, pay([1, 2, 3]), meta); await sleep(40);
  ST.act = { ok: false, error: "store busy" }; ST.reloads = 0; var s1 = A.stats().started;
  btn(1, "done").click();
  await sleep(300);
  ck("a refused Done: the row stays with the message, Done is live, no reload, nothing started",
    !!rowEl(1) && /store busy/.test(body.textContent) && !btn(1, "done").disabled && ST.reloads === 0 && A.stats().started === s1, [ST.reloads, A.stats().started - s1]);
  ST.act = { ok: true };

  // --- Snooze
  RENDER.needsyou(body, pay([1, 2, 3]), meta); await sleep(40);
  ST.next = pay([2, 3]); btn(1, "snz").click(); await sleep(20);
  var sn = rowEl(1).querySelector('button[data-a="snooze"]');
  if (sn) sn.click();
  await until(function () { return ids() === "message:2,message:3" && A.stats().running === 0; }, 4000);
  ck("Snooze: the row lifts away, the rest close up, clean", !!sn && ids() === "message:2,message:3" && dirty().length === 0 && rowTop(2) !== null, [ids(), dirty()]);

  // --- the last item: the empty state
  RENDER.needsyou(body, pay([1]), meta); await sleep(40);
  ST.next = pay([]); btn(1, "done").click();
  await until(function () { return !rowEl(1) && A.stats().running === 0; }, 4000);
  ck("the last Done: the empty state, 'clear', clean", /Nothing needs you/.test(body.textContent) && meta.textContent === "clear" && metaClean() && dirty().length === 0, [meta.textContent, dirty()]);

  // --- the pop-out
  var pop = $("pop"); $("card2").style.display = "block";
  EXPAND_RENDER.needsyou(pop, pay([1, 2, 3])); await sleep(40);
  var p1 = rowTop(1, pop);
  ST.next = pay([2, 3]); btn(1, "done", pop).click();
  await until(function () { return ids(pop) === "message:2,message:3" && A.stats().running === 0; }, 4000);
  ck("the pop-out: row 2 slid into row 1's place, clean", rowTop(2, pop) === p1 && dirty(pop).length === 0, [rowTop(2, pop), p1, dirty(pop)]);

  ck("no page error, no unhandled rejection", ERR.length === 0, ERR);
  ck("no warning but the runtime's own allowlist ones", WARN.every(function (w) { return /no JS library animates inside <header>|may animate transform and opacity only/.test(w); }), WARN);
}
window.addEventListener("load", function () { main().catch(function (e) { ck("the fixture ran to the end", false, String(e && e.stack || e)); }).then(function () { window.__results = R; window.__done = true; }); });
</script></body></html>`;

// ---- the Agent page ----------------------------------------------------------
const PAGE_AG = (shim) => HEAD(shim) + String.raw`
<main class="stage"><div class="view" id="view-hub"></div><div class="view" id="view-mind" hidden></div></main>
<div id="msgs" style="height:200px;overflow:auto"></div>
<script>
var PULSE = { ok: true, facts: ["Fact one", "Fact two", "Fact three"], last_action: { id: 1 }, events: [] };
window.fetch = function (url) {
  function J(o) { return Promise.resolve({ ok: true, json: function () { return Promise.resolve(JSON.parse(JSON.stringify(o))); } }); }
  if (/\/api\/agent\/pulse/.test(String(url))) return J(PULSE);
  return J({ ok: false });
};
window.setAgentState = function () {};
</script>
<script src="/aux_agent.js"></script>
<script>
var A = window.HermesAnim, T = window.__agentTest, REDUCED = location.search.indexOf("reduce") >= 0;
function idle() { return until(function () { return A.stats().running === 0; }, 4000); }
function opa(el) { return +getComputedStyle(el).opacity; }

async function main() {
  await A.load("gsap"); await A.load("anime");
  var va = $("view-agent");
  ck("the Agent shell is built", !!va && !!$("ag-tick") && !!$("agent-hero") && !!$("ag-ekg"));
  va.hidden = false;
  await T.loadPulse();
  var el = $("ag-tick"), s0 = A.stats().started;

  // --- the ticker crossfade
  var lo = 1;
  var fr = frames(function () { lo = Math.min(lo, opa(el)); return 0; }, function () { return A.stats().running === 0 && el.textContent === "Fact two"; }, 2500);
  T.crossfade();
  await idle(); await fr;
  ck("the crossfade swapped the words", el.textContent === "Fact two", el.textContent);
  ck("...ends fully visible with no inline style (a fade-in started inside exit's done() ends at opacity ~0)", opa(el) === 1 && el.style.opacity === "" && el.style.cssText === "", [opa(el), el.style.cssText]);
  if (REDUCED) ck("Reduce motion: the crossfade started nothing", A.stats().started === s0, A.stats().started - s0);
  else ck("...and it dipped on the way (it really crossfaded)", lo < 0.95, lo);
  T.crossfade(); await sleep(60); T.crossfade();
  await idle(); await sleep(100);
  ck("two crossfades 60 ms apart: still fully visible, a real fact on the line", opa(el) === 1 && el.style.opacity === "" && ["Fact one", "Fact two", "Fact three"].indexOf(el.textContent) >= 0, [el.textContent, el.style.cssText]);

  // --- the sigil ring: ticks add up and the pivot stays on its centre
  var ring = document.querySelector(".sig-ring");
  function ctr() { var b = ring.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }
  var c0 = ctr();
  T.driveHero("using terminal"); await idle();
  var tr1 = ring.style.transform, c1 = ctr();
  T.driveHero("using terminal"); await idle();
  var tr2 = ring.style.transform, c2 = ctr();
  ck("the sigil ring ends at 12 deg, then 24 deg (the ticks add up)", /(^|[^\d])12/.test(tr1) && /(^|[^\d])24/.test(tr2), [tr1, tr2]);
  ck("...and its centre does not walk (transform-box:fill-box)", Math.abs(c1[0] - c0[0]) < 0.6 && Math.abs(c1[1] - c0[1]) < 0.6 && Math.abs(c2[0] - c0[0]) < 0.6 && Math.abs(c2[1] - c0[1]) < 0.6, [c0, c1, c2]);

  // --- the EKG dot
  var dot = $("ag-ekg"), hi = 1;
  PULSE.last_action = { id: 2 };
  var s1 = A.stats().started;
  await T.loadPulse();                                  // the blip starts inside it, before it resolves
  await frames(function () { hi = Math.max(hi, +(/matrix\(([\d.]+)/.exec(getComputedStyle(dot).transform) || [0, 1])[1]); return 0; }, function () { return A.stats().running === 0; }, 1500);
  ck("a new recorder event blips the EKG and it ends at scale 1", /^(scale\(1\)|none|)$/.test(dot.style.transform) && (REDUCED || (hi > 1.2 && A.stats().started > s1)), [dot.style.transform, hi]);
  var s2 = A.stats().started; await T.loadPulse(); await sleep(60);
  ck("...the same event again does not", A.stats().started === s2, A.stats().started - s2);

  // --- a show-in card
  var s3 = A.stats().started;
  T.showinDispatch("using terminal ls -la");
  var card = document.querySelector(".showin"), ic = card && card.querySelector(".showin-head .ic");
  await idle();
  ck("a show-in card appeared, rose in and ended under CSS control", !!card && card.style.opacity === "" && (card.style.transform === "" || card.style.transform === "none") && opa(card) === 1, card && card.style.cssText);
  ck("...its head icon turned a full turn (rotate persisted)", !!ic && /rotate\(360deg\)/.test(ic.style.transform), ic && ic.style.transform);
  if (REDUCED) ck("Reduce motion: the card and its icon started nothing", A.stats().started === s3, A.stats().started - s3);

  // --- the Record rail poll is quiet
  var calls = [];
  window.loadRecorder = function () { calls.push(A.live()); return Promise.resolve(); };
  T.openRail("record");
  await until(function () { return calls.length >= 2; }, 7000);
  ck("the rail's first mount is the user's own act (live), its 3 s poll runs inside quiet()", calls.length >= 2 && (REDUCED ? true : calls[0] === true) && calls[calls.length - 1] === false, calls);

  ck("no page error, no unhandled rejection", ERR.length === 0, ERR);
  ck("no warning but the runtime's own allowlist ones", WARN.every(function (w) { return /no JS library animates inside <header>|may animate transform and opacity only/.test(w); }), WARN);
}
window.addEventListener("load", function () { main().catch(function (e) { ck("the fixture ran to the end", false, String(e && e.stack || e)); }).then(function () { window.__results = R; window.__done = true; }); });
</script></body></html>`;

// ---- the loopback server: the vendored files, three aux files, two fixtures ---
function serve(pages) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const u = req.url.split("?")[0];
      if (Object.prototype.hasOwnProperty.call(pages, u)) { res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" }); return res.end(pages[u]); }
      if (Object.prototype.hasOwnProperty.call(FILES, u)) {
        try { const b = fs.readFileSync(FILES[u]); res.writeHead(200, { "Content-Type": "application/javascript", "Cache-Control": "no-store" }); return res.end(b); } catch (e) {}
      }
      res.writeHead(404); res.end();
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

// ---- DevTools protocol, minimal -----------------------------------------------
async function runPage(chrome, base, pagePath, query, reduceOS) {
  const port = 9300 + Math.floor(Math.random() * 600);
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-surfaces-chrome-"));
  const args = ["--no-sandbox", "--disable-gpu", "--no-first-run", "--remote-debugging-port=" + port, "--user-data-dir=" + prof, "--window-size=1200,900"];
  if (!/headless_shell|chrome-headless-shell/.test(chrome)) args.unshift("--headless=new");
  const proc = cp.spawn(chrome, args.concat(["about:blank"]), { stdio: "ignore" });
  try {
    let targets = null;
    for (let i = 0; i < 60 && !targets; i++) {
      try { const t = await (await fetch("http://127.0.0.1:" + port + "/json")).json(); if (t.length) targets = t; } catch (e) {}
      if (!targets) await sleep(200);
    }
    if (!targets) throw new Error("Chromium did not open its debugging port");
    const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
    await new Promise((r, j) => { ws.addEventListener("open", r); ws.addEventListener("error", j); });
    let id = 0; const pend = {};
    ws.addEventListener("message", (m) => { const d = JSON.parse(m.data); if (d.id && pend[d.id]) { pend[d.id](d); delete pend[d.id]; } });
    const send = (method, params) => new Promise((r) => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
    await send("Page.enable"); await send("Runtime.enable");
    if (reduceOS) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
    await send("Page.navigate", { url: base + pagePath + (query ? "?" + query : "") });
    const t0 = Date.now();
    while (Date.now() - t0 < 45000) {
      await sleep(400);
      const r = await send("Runtime.evaluate", { expression: "window.__done === true ? JSON.stringify(window.__results) : null", returnByValue: true });
      const v = r.result && r.result.result && r.result.result.value;
      if (v) { ws.close(); return JSON.parse(v); }
    }
    ws.close();
    return [{ name: "the fixture finished within 45 s", ok: false, extra: "timeout" }];
  } finally {
    try { proc.kill(); } catch (e) {}
    try { fs.rmSync(prof, { recursive: true, force: true }); } catch (e) {}
  }
}

(async function main() {
  console.log("Needs-you and the Agent page against the real Motion 14, GSAP 3 and anime 4 (headless Chromium)");
  const missing = Object.keys(FILES).filter((k) => !fs.existsSync(FILES[k]));
  if (missing.length) skipAll("not all the vendored libraries / aux files are present (" + missing.join(", ") + "); set HERMES_ANIM_VENDOR_DIR or let the vendor cluster land");
  if (typeof WebSocket === "undefined") skipAll("this node has no global WebSocket (needs 22+)");
  const chrome = findChrome();
  if (!chrome) skipAll("no Chromium found (set HERMES_CHROME, or `playwright install chromium`)");
  const shim = pageShim();
  check("the page shim (animate / ANIM_NULL / HA) was found in dashboard/index.html", shim.length > 200 && /const HA=/.test(shim) && /ANIM_NULL/.test(shim));
  if (!shim) { console.log("TESTS " + pass + " passed " + fail + " failed"); process.exit(1); }
  const srv = await serve({ "/ny.html": PAGE_NY(shim), "/ag.html": PAGE_AG(shim) });
  const base = "http://127.0.0.1:" + srv.address().port;
  try {
    console.log("  (chromium: " + path.basename(path.dirname(chrome)) + ")");
    for (const [label, page, query, reduce] of [
      ["Needs-you, libraries live", "/ny.html", "", false], ["Agent page, libraries live", "/ag.html", "", false],
      ["Needs-you, OS reduced motion", "/ny.html", "reduce", true], ["Agent page, OS reduced motion", "/ag.html", "reduce", true]]) {
      console.log("[" + label + "]");
      const rs = await runPage(chrome, base, page, query, reduce);
      rs.forEach((r) => check(r.name, r.ok, r.extra));
    }
  } finally { srv.close(); }
  console.log("TESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS " + pass + " passed " + (fail + 1) + " failed"); process.exit(1); });
