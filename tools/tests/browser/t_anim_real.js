// t_anim_real.js — dashboard/aux_anim.js against the REAL libraries, in a real
// browser (docs/plans/motion-system-2026-10.md §10.3; E20).
//
// The unit tier (t_anim_*.js) drives the runtime against recording stubs,
// because real GSAP and anime do not run faithfully in the mini DOM. Stubs can
// only confirm what their author already believed about a library, and one such
// belief was wrong: anime's revert() restores a path's BLANK initial state, so
// every finished draw() left its path invisible (found here, not there). This
// suite closes that gap: Motion 14.6, GSAP 3.15 (+ Flip, CustomEase) and
// anime 4.5 run for real, in headless Chromium, on a fixture page the suite
// serves from a loopback port of its own.
//
//   * Hermetic: no dashboard, no model, no /api call, no network beyond the
//     127.0.0.1 server this file starts, which serves exactly six files.
//   * Needs, and SKIPs without (never fails for lack of them): the vendored
//     libraries (dashboard/vendor/..., or a tree laid out the same way named by
//     HERMES_ANIM_VENDOR_DIR), a Chromium (HERMES_CHROME, or the one Playwright
//     installs under ~/Library/Caches/ms-playwright or ~/.cache/ms-playwright)
//     and a node with a global WebSocket (22+).
//   * Drives the browser over the DevTools protocol; no Playwright needed here.
//
// Checks only things that hold at any frame rate: end states, handles, inline
// styles left behind, callbacks, events, what loads when. No pixel or frame
// budget is asserted (that is §10.3's WebKit probe, which needs WebKit).
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const cp = require("child_process");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const VENDOR = process.env.HERMES_ANIM_VENDOR_DIR || path.join(REPO, "dashboard", "vendor");
const FILES = {
  "/vendor/motion/motion-14.6.0.min.js": path.join(VENDOR, "motion", "motion-14.6.0.min.js"),
  "/vendor/gsap/gsap-3.15.0.min.js": path.join(VENDOR, "gsap", "gsap-3.15.0.min.js"),
  "/vendor/gsap/Flip-3.15.0.min.js": path.join(VENDOR, "gsap", "Flip-3.15.0.min.js"),
  "/vendor/gsap/CustomEase-3.15.0.min.js": path.join(VENDOR, "gsap", "CustomEase-3.15.0.min.js"),
  "/vendor/anime/anime-4.5.0.umd.min.js": path.join(VENDOR, "anime", "anime-4.5.0.umd.min.js"),
  "/aux_anim.js": path.join(REPO, "dashboard", "aux_anim.js")
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

// ---- the fixture page: the dashboard's skeleton, none of its code -----------
const PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>fixture</title>
<style>.w{height:30px;width:100px;background:#cde;margin:4px;transition-property:translate,scale}#grid{display:grid;grid-template-columns:repeat(3,110px)}
.view{padding:4px}.num{display:inline-block}</style></head><body>
<header><span class="seg"><b class="on">Hub</b><b>Mind</b></span><b id="hb">hdr</b></header>
<main class="stage">
 <section class="view" id="view-hub"><div id="grid"></div></section>
 <section class="view" id="view-mind" hidden><nav id="set-rail">rail</nav><div id="set-panels"><div class="set-panel">p</div></div></section>
</main>
<div id="pop" style="width:50px;height:50px;background:#f90"></div>
<h2 id="h2">Welcome to the dashboard heading</h2>
<span class="num" id="num">0</span>
<svg id="svg" width="120" height="20"><path id="pth" d="M0 5L100 5" stroke="#000" fill="none"/><path id="pth2" d="M0 15L100 15" stroke="#000" fill="none" stroke-dasharray="4 2"/></svg>
<script>var RAF = 0; (function () { var r = window.requestAnimationFrame.bind(window); window.requestAnimationFrame = function (f) { RAF++; return r(f); }; })();
var WARN = [], ERR = [], R = [];
console.warn = function () { WARN.push(Array.prototype.join.call(arguments, " ")); };
window.addEventListener("error", function (e) { ERR.push(String(e.message)); });
window.addEventListener("unhandledrejection", function (e) { ERR.push("rejection " + e.reason); });
</script>
<script src="/vendor/motion/motion-14.6.0.min.js"></script>
<script src="/aux_anim.js"></script>
<script>
var MODE = location.search.slice(1);
function ck(n, c, x) { R.push({ name: n, ok: !!c, extra: c || x === undefined ? "" : JSON.stringify(x) }); }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function until(f, ms) { return new Promise(function (res) { var t0 = Date.now(); (function p() { var v; try { v = f(); } catch (e) { v = false; } if (v || Date.now() - t0 > (ms || 4000)) res(!!v); else setTimeout(p, 25); })(); }); }
function $(i) { return document.getElementById(i); }
function css(el) { return el.style.cssText; }
function clean(el) { return (el.style.transform === "" || el.style.transform === "none") && el.style.opacity === ""; }
function allClean(list) { return Array.prototype.every.call(list, clean); }
function bare(p, keep) { return p.getAttribute("pathLength") === null && p.getAttribute("stroke-dashoffset") === null && p.getAttribute("draw") === null &&
  p.getAttribute("stroke-dasharray") === (keep || null); }
function box() { var d = document.createElement("div"); d.style.cssText = "width:30px;height:30px"; document.body.appendChild(d); return d; }
var EV = { s: 0, e: 0 };
document.addEventListener("hermes:animstart", function () { EV.s++; });
document.addEventListener("hermes:animend", function () { EV.e++; });

async function viaMotion(A) {
  var p = $("pop");
  ck("boot: HermesAnim v1 on Motion 14.6, skipAnimations off", A.v === 1 && A.stats().libs.motion === "14.6.0" && Motion.MotionGlobalConfig.skipAnimations === false, A.stats().libs);
  var h = A.enter(p, "pop");
  ck("enter pre-paints the from-state", !h.done && p.style.opacity === "0", css(p));
  ck("enter runs to its end and hands the element back to CSS", await until(function () { return h.done; }) && await until(function () { return clean(p); }), css(p));
  var n = 0, x = A.exit(p, "popOut", function () { n++; });
  await until(function () { return x.done; });
  ck("exit: done once, element handed back", n === 1 && clean(p), [n, css(p)]);
  var y = A.exit(p, "fadeOut", function () { n += 10; });
  await sleep(30); y.cancel(); await sleep(350);
  ck("a cancelled exit: no done, and no late commit lands", n === 1 && clean(p), [n, css(p)]);
  var z = A.exit(p, "fadeOut", function () { n += 100; });
  await sleep(30); var e = A.enter(p, "pop");
  ck("reopen during an exit: the exit is cancelled, the enter lands clean", (await until(function () { return e.done; })) && n === 1 && clean(p), [n, css(p)]);
  var g = $("grid"), i;
  for (i = 0; i < 20; i++) { var d = document.createElement("div"); d.className = "w"; d.setAttribute("data-flip-id", "k" + i); d.textContent = i; g.appendChild(d); }
  var ws = g.querySelectorAll(".w");
  var s = A.stagger(ws, "rise");
  await sleep(40);
  ck("stagger: at most 12 of 20 animate", Array.prototype.filter.call(ws, function (el) { return el.style.opacity !== ""; }).length <= 12);
  ck("...and all of them are handed back at the end", (await until(function () { return s.done; })) && allClean(ws), css(ws[0]));
  var sp = A.spring(p, { scale: [1, 1.2] }, "gel");
  await until(function () { return sp.done; });
  ck("a spring's end value persists", /scale\\(1\\.2\\)/.test(p.style.transform), p.style.transform);
  p.style.transform = "";
  var l = A.legacy(p, { opacity: [0, 1] }, { duration: .2, easing: "cubic-bezier(.22,1,.36,1)" });
  ck("legacy(): easing is delivered, controls come back", !!l && typeof l.then === "function" && (await Promise.race([l.finished, sleep(1500)]), true));
}

async function viaLibs(A) {
  var g = $("grid"), p = $("pop");
  ck("gsap (core, Flip, CustomEase) loads", (await A.load("gsap")) === true && A.ready("gsap"), A.stats().libs);
  ck("...CustomEase has the token curves, gel overshoots", typeof gsap.parseEase("hermes.out") === "function" && gsap.parseEase("hermes.gel")(.5) > 1);
  ck("anime loads, and pauses with the page", (await A.load("anime")) === true && anime.engine.pauseOnDocumentHidden === true, A.stats().libs);

  // flip
  var ran = 0, s0 = EV.s;
  var f = A.flip(g, function () { ran++; g.appendChild(g.firstElementChild); });
  ck("flip: mutate ran once, synchronously; the flight is running", ran === 1 && !f.done);
  await sleep(120);
  ck("...cards are mid-flight on a transform", Array.prototype.some.call(g.children, function (c) { return /translate|matrix/.test(c.style.transform); }), css(g.children[0]));
  ck("...and it lands, leaving no inline style behind", (await until(function () { return f.done; })) && (await until(function () { return allClean(g.children); })),
    Array.prototype.map.call(g.children, css).slice(0, 2));
  ck("one hermes:animstart per animation, and its end", EV.s > s0 && EV.e >= EV.s - 1, EV);
  var f1 = A.flip(g, function () { g.insertBefore(g.lastElementChild, g.firstChild); });
  await sleep(80);
  var f2 = A.flip(g, function () { g.insertBefore(g.lastElementChild, g.firstChild); });
  ck("two flips back to back: both land, clean", (await until(function () { return f1.done && f2.done; })) && (await until(function () { return allClean(g.children); })),
    [f1.done, f2.done, Array.prototype.map.call(g.children, css).slice(0, 2)]);
  var fe = A.flip(g, function () { var c = document.createElement("div"); c.className = "w"; c.setAttribute("data-flip-id", "z"); g.appendChild(c); }, { enter: "pop" });
  await until(function () { return fe.done; });
  ck("flip with enter: the new card ends visible and clean", (await until(function () { return allClean(g.children); })) && getComputedStyle(g.lastElementChild).opacity === "1", css(g.lastElementChild));
  var hm = 0, hh = A.flip(document.querySelector("header"), function () { hm++; });
  ck("flip inside the app header: refused (mutate only)", hm === 1 && hh.done && A.stats().instant.header >= 1);

  // sequence
  var done = [], b = box();
  var sq = A.sequence("t", function (tl) { tl.fromTo(b, { opacity: 0 }, { opacity: 1, duration: .2 }); }, { done: function () { done.push("t"); } });
  ck("sequence: done once, element handed back", (await until(function () { return sq.done; })) && (await until(function () { return clean(b); })) && done.join() === "t", [done, css(b)]);
  var s1 = A.sequence("sw", function (tl) { tl.to(b, { x: 100, duration: 1 }); }, { done: function () { done.push("old"); } });
  await sleep(100);
  var s2 = A.sequence("sw", function (tl) { tl.to(b, { x: 0, duration: .2 }); }, { done: function () { done.push("new"); } });
  await until(function () { return s2.done; });
  ck("the same name again lands the old one first (its done runs), then the new one", done.slice(1).join() === "old,new" && s1.done && s2.done, done);
  await until(function () { return clean(b); });
  var second = null, hin = null, heldIn = 0, chainDone = 0;
  A.sequence("chain", function (tl) { tl.fromTo(b, { opacity: 0 }, { opacity: 1, duration: .15 }); }, { done: function () {
    chainDone++;
    second = A.sequence("chain", function (tl) { tl.to(b, { opacity: .3, duration: .4 }); }, { done: function () { chainDone += 10; } });
    hin = A.hold(.3, function () { heldIn++; });
  } });
  ck("a sequence chained from done() runs to its end (GSAP's context must not take it with the first)", (await until(function () { return second && second.done; })) && chainDone === 11, [chainDone, second && second.done]);
  ck("...and a hold() armed in done() fires", (await until(function () { return heldIn === 1; })) && hin.done === true, heldIn);
  await until(function () { return clean(b); });
  var s3 = A.sequence("cc", function (tl) { tl.to(b, { x: 100, duration: 1 }); }, { done: function () { done.push("cancelled-ran"); } });
  await sleep(80); s3.cancel(); await sleep(150);
  ck("a cancelled sequence: reverted, done never runs", clean(b) && done.indexOf("cancelled-ran") < 0, [done, css(b)]);
  var sb = A.sequence("bad", function (tl) { tl.to(b, { width: 90, keyframes: [{ height: 90 }, { opacity: .5 }], duration: .8 }); });
  await sleep(300);                                   // mid-flight: the context's revert would hide a leak later
  ck("width/height never reach the element, even mid-flight (allowlist, keyframes included)", b.style.width === "30px" && b.style.height === "30px", css(b));
  await until(function () { return sb.done; }, 3000);

  // hold, count, draw, words
  var held = 0, hl = A.hold(.2, function () { held++; });
  await sleep(80);
  ck("hold waits its time, then fires once", held === 0 && (await until(function () { return hl.done; })) && held === 1);
  var num = $("num"), c = A.count(num, 1234);
  ck("count: the final value is the aria-label while it runs", num.getAttribute("aria-label") === "1,234");
  ck("...lands on the text and takes its helpers off", (await until(function () { return c.done; })) && num.textContent === "1,234" && num.getAttribute("aria-label") === null && num.style.minWidth === "", num.outerHTML);
  A.count(num, 2000); await sleep(150); A.count(num, 3000);
  ck("count retargeted mid-run lands on the latest value", await until(function () { return num.textContent === "3,000"; }), num.textContent);
  var pth = $("pth"), pth2 = $("pth2");
  var dr = A.draw(pth);
  await sleep(80);
  ck("draw: the path is being drawn", pth.getAttribute("draw") !== null && !dr.done);
  ck("...and afterwards it is FULLY DRAWN with the library's attributes gone (anime's own revert() leaves it blank)", (await until(function () { return dr.done; })) && bare(pth), pth.outerHTML);
  var dc = A.draw(pth2); await sleep(60); dc.cancel();
  ck("a cancelled draw restores the page's own dash style", bare(pth2, "4 2"), pth2.outerHTML);
  var h2 = $("h2"), wd = A.words(h2);
  await sleep(80);
  ck("words: split into spans while it runs", h2.querySelectorAll("span").length > 1);
  ck("...and plain text again afterwards", (await until(function () { return wd.done; })) && h2.children.length === 0 && h2.textContent === "Welcome to the dashboard heading", h2.outerHTML);

  // the view entrance
  var vh = $("view-hub"), vm = $("view-mind"), v0 = EV.s;
  vh.hidden = true; vm.hidden = false;
  await sleep(50);
  ck("un-hiding a .stage>.view plays one entrance", EV.s === v0 + 1, EV.s - v0);
  ck("...which hands the view and its parts back clean", (await until(function () { return clean($("set-rail")) && vm.style.opacity === ""; })), [css(vm), css($("set-rail"))]);
  vm.hidden = true; vh.hidden = false; await sleep(30); vh.hidden = true; vm.hidden = false;
  await sleep(1000);
  vm.hidden = true; vh.hidden = false;
  ck("a fast double switch ends clean", await until(function () { return vm.style.opacity === "" && vh.style.opacity === "" && clean($("set-rail")); }), [css(vm), css(vh)]);

  // quiet
  var st = A.stats().started;
  A.quiet(function () { A.enter(p, "rise"); A.flip(g, function () {}); A.count(num, 5); });
  ck("quiet(): nothing starts, the count's final text is written", A.stats().started === st && num.textContent === "5");

  // reduce switch, then the page going hidden: everything running settles
  var rr = A.sequence("rr", function (tl) { tl.to(b, { x: 100, duration: 2 }); }, { done: function () { done.push("rr"); } });
  await sleep(60);
  document.documentElement.setAttribute("data-reduce", "1");
  await sleep(60);
  ck("Reduce motion turned on: the running sequence lands, Motion skips", rr.done && done.indexOf("rr") >= 0 && Motion.MotionGlobalConfig.skipAnimations === true, done);
  var ex = 0; A.exit(p, "popOut", function () { ex++; });
  ck("...and an exit is instant", ex === 1 && A.enter(p, "pop").done);
  document.documentElement.removeAttribute("data-reduce");
  await sleep(60);
  ck("...turned off again: live, Motion animates", A.live() && Motion.MotionGlobalConfig.skipAnimations === false);

  var f3 = A.flip(g, function () { g.insertBefore(g.lastElementChild, g.firstChild); });
  var c3 = A.count(num, 999), d3 = A.draw(pth);
  await sleep(40);
  Object.defineProperty(document, "hidden", { configurable: true, get: function () { return true; } });
  document.dispatchEvent(new Event("visibilitychange"));
  await sleep(60);
  ck("the page goes hidden: flip, count and draw all land at once", f3.done && c3.done && d3.done, [f3.done, c3.done, d3.done]);
  ck("...cards clean, the count's final text, the path drawn", allClean(g.children) && num.textContent === "999" && bare(pth), [num.textContent, pth.outerHTML]);
  var hn = 0; A.hold(1, function () { hn++; });
  ck("...hold() fires at once while hidden", hn === 1);
  delete document.hidden;
  document.dispatchEvent(new Event("visibilitychange"));
  await sleep(1300);
  var r0 = RAF; await sleep(1000);
  ck("idle: next to no frame callbacks once the last animation has ended", RAF - r0 <= 2, RAF - r0);
}

async function osReduced(A) {
  ck("OS reduced motion: reduced() and skipAnimations", A.reduced() === true && A.live() === false && Motion.MotionGlobalConfig.skipAnimations === true);
  await sleep(3200);                                  // past the 2.5 s prefetch slot
  ck("...no library script was ever appended", document.querySelectorAll("script[data-hermes-anim]").length === 0 && !window.gsap && !window.anime);
  ck("...load() resolves false", (await A.load("gsap")) === false && (await A.load("anime")) === false);
  var g = $("grid"), p = $("pop"), n = 0, d = 0;
  A.flip(g, function () { n++; });
  A.exit(p, "popOut", function () { d++; });
  A.count($("num"), 77);
  var e = A.enter(p, "pop"), untouched = p.style.opacity === "" && p.style.transform === "";
  ck("...flip runs its mutate, exit its done, count writes the end, enter is a no-op", n === 1 && d === 1 && $("num").textContent === "77" && e.done && untouched, [n, d, css(p)]);
  var l0 = performance.now(), l = A.legacy(p, { opacity: [0, 1] }, { duration: 1 });
  await l.finished;
  ck("...legacy() takes no time", performance.now() - l0 < 300, performance.now() - l0);
}

async function main() {
  var A = window.HermesAnim;
  if (!A) { ck("HermesAnim is defined", false); return; }
  if (MODE === "os") await osReduced(A); else { await viaMotion(A); await viaLibs(A); }
  ck("no page error, no unhandled rejection", ERR.length === 0, ERR);
  ck("no warning but the expected allowlist and header ones", WARN.every(function (w) { return /no JS library animates inside <header>|may animate transform and opacity only/.test(w); }), WARN);
  window.__results = R; window.__done = true;
}
window.addEventListener("load", function () { main().catch(function (e) { ck("the fixture ran to the end", false, String(e && e.stack || e)); window.__results = R; window.__done = true; }); });
</script></body></html>`;

// ---- the loopback server: six files and the fixture, nothing else ----------
function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const u = req.url.split("?")[0];
      if (u === "/page.html") { res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" }); return res.end(PAGE); }
      if (Object.prototype.hasOwnProperty.call(FILES, u)) {
        try { const b = fs.readFileSync(FILES[u]); res.writeHead(200, { "Content-Type": "application/javascript", "Cache-Control": "no-store" }); return res.end(b); } catch (e) {}
      }
      res.writeHead(404); res.end();
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

// ---- DevTools protocol, minimal ---------------------------------------------
async function runPage(chrome, base, query, reduceOS) {
  const port = 9300 + Math.floor(Math.random() * 600);
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-anim-chrome-"));
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
    await send("Page.navigate", { url: base + "/page.html" + (query ? "?" + query : "") });
    const t0 = Date.now();
    while (Date.now() - t0 < 90000) {
      await sleep(400);
      const r = await send("Runtime.evaluate", { expression: "window.__done === true ? JSON.stringify(window.__results) : null", returnByValue: true });
      const v = r.result && r.result.result && r.result.result.value;
      if (v) { ws.close(); return JSON.parse(v); }
    }
    ws.close();
    return [{ name: "the fixture finished within 90 s", ok: false, extra: "timeout" }];
  } finally {
    try { proc.kill(); } catch (e) {}
    try { fs.rmSync(prof, { recursive: true, force: true }); } catch (e) {}
  }
}

(async function main() {
  console.log("aux_anim.js against the real Motion 14, GSAP 3 and anime 4 (headless Chromium)");
  const missing = Object.keys(FILES).filter((k) => !fs.existsSync(FILES[k]));
  if (missing.length) skipAll("not all the vendored libraries are present (" + missing.join(", ") + "); set HERMES_ANIM_VENDOR_DIR or let the vendor cluster land");
  if (typeof WebSocket === "undefined") skipAll("this node has no global WebSocket (needs 22+)");
  const chrome = findChrome();
  if (!chrome) skipAll("no Chromium found (set HERMES_CHROME, or `playwright install chromium`)");
  const srv = await serve();
  const base = "http://127.0.0.1:" + srv.address().port;
  try {
    console.log("  (chromium: " + path.basename(path.dirname(chrome)) + ")");
    for (const [label, query, reduce] of [["libraries live", "", false], ["OS reduced motion", "os", true]]) {
      console.log("[" + label + "]");
      const rs = await runPage(chrome, base, query, reduce);
      rs.forEach((r) => check(r.name, r.ok, r.extra));
    }
  } finally { srv.close(); }
  console.log("TESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS " + pass + " passed " + (fail + 1) + " failed"); process.exit(1); });
