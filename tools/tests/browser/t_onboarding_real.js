// t_onboarding_real.js — the first-run sheet (dashboard/aux_onboarding.js) on
// the REAL motion libraries, in a real browser (motion-system §8.9, test plan
// 10.3; companion to the unit suite t_onboarding_steps.js).
//
// The unit tier drives the sheet against recording stubs, which can only
// confirm what their author believed about GSAP and anime. Here Motion 14.6,
// GSAP 3.15 (+ Flip, CustomEase) and anime 4.5 run for real in headless
// Chromium, with the page shim (animate / ANIM_NULL / HA) cut out of
// dashboard/index.html itself:
//   * opening the sheet fades and settles it in, and leaves nothing inline;
//   * a step change slides the column in from the side it came from, pops
//     the new rail dot and raises the heading's words — and when it ends the
//     column, the dot and the heading are back under CSS control (no inline
//     transform/opacity, no individual `scale`/`rotate` baked in, the heading
//     plain text again), with no horizontal scrollbar on the way;
//   * a toggle repaint starts nothing; the Ready step's figure counts up and
//     ends on its real value with its helpers (aria-label, min-width) gone;
//   * the extra-steps hook and a stub model advisor work in a real DOM too;
//   * under the OS reduced-motion switch nothing starts and every end state
//     is the same.
//
//   * Hermetic: no dashboard, no model, no /api call (fetch is stubbed in the
//     fixture), no network beyond the 127.0.0.1 server this file starts.
//   * Needs, and SKIPs without (never fails for lack of them): the vendored
//     libraries, a Chromium (HERMES_CHROME, or the one Playwright installs)
//     and a node with a global WebSocket (22+). No Playwright needed.
//
// Only frame-rate-independent facts are asserted: end states, inline styles
// left behind, what moved at all, callbacks, errors.
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
  "/aux_onboarding.js": path.join(DASH, "aux_onboarding.js")
};

let pass = 0, fail = 0;
function check(name, ok, extra) {
  if (ok) { pass++; console.log("  ok   " + name); } else { fail++; console.log("  FAIL " + name + (extra === undefined || extra === "" ? "" : "  <- " + extra)); }
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

const PAGE = (shim) => String.raw`<!doctype html><html><head><meta charset="utf-8"><title>fixture</title>
<style>:root{--ground:#E7EAF3;--ink:#10131D;--muted:#565E72;--faint:#868DA1;--hairline:rgba(16,19,29,.10);--iris:#5B63E6;--iris-ink:#fff;--ok:#2E9E68;--bad:#D24C3C;--warn:#B9821A;--field:#fff;--field-edge:#ccc;--chip:#eee}
body{font:14px sans-serif;margin:0;background:var(--ground)}</style></head><body>
<header><span class="seg"><b id="tab-hub" class="on" role="tab">Hub</b></span></header>
<main><input id="input" aria-label="chat"></main>
<script>
var WARN = [], ERR = [], R = [];
console.warn = function () { WARN.push(Array.prototype.join.call(arguments, " ")); };
window.addEventListener("error", function (e) { ERR.push(String(e.message)); });
window.addEventListener("unhandledrejection", function (e) { ERR.push("rejection " + e.reason); });
function ck(n, c, x) { R.push({ name: n, ok: !!c, extra: c || x === undefined ? "" : JSON.stringify(x) }); }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function until(f, ms) { return new Promise(function (res) { var t0 = Date.now(); (function p() { var v; try { v = f(); } catch (e) { v = false; } if (v || Date.now() - t0 > (ms || 4000)) res(!!v); else setTimeout(p, 25); })(); }); }
function frames(sample, stopWhen, maxMs) {
  return new Promise(function (res) {
    var out = [], t0 = performance.now();
    (function tick() { out.push(sample()); if ((stopWhen && stopWhen()) || performance.now() - t0 > (maxMs || 2000)) res(out); else requestAnimationFrame(tick); })();
  });
}
var REDUCED = location.search.indexOf("reduce") >= 0;
// ---- fixtures ----
var M27B = "mlx-community/Qwen3.8-27B-4bit", M9B = "mlx-community/Qwen3.5-9B-4bit";
var STATE = { ok: true, version: "1.4.0", done: true, done_at: null,
  sysinfo: { chip: "Apple M5 Max", ram_gb: 64, disk_free_gb: 412, macos: "26.1", laptop: true, cores: 18 },
  detect: { hermes_cli: true, hermes_path: "/x/hermes", claude_cli: true, mlx_venv: true, models_downloaded: [],
            fda: null, telegram_configured: false, google_configured: false },
  recommendation: { tier: "full", primary: M27B, background: M9B, tight: false, alt: null, why: "Room for the full roster.",
    note: "", primary_label: "Qwen3.8-27B", background_label: "Qwen3.5-9B", download_gb: 23, total_gb: 23,
    already_downloaded: false,
    fits: [{ id: M27B, label: "Qwen3.8-27B", ram: 19, size_gb: 17, note: "", role: "primary", fit: "ok", downloaded: false },
           { id: M9B, label: "Qwen3.5-9B", ram: 7, size_gb: 6, note: "", role: "background", fit: "ok", downloaded: false }] },
  prefs: { theme: null, idle_min: 10, idle_enabled: true, prewarm: true, claude_escalation: false, briefings: true,
           news: true, web_research: false, quiet_hours: { start: "22:00", end: "07:00" } } };
var POSTS = [], HX = { reply: { applied: ["Harness: saved"] }, saves: 0 };
window.fetch = function (url, init) {
  var u = String(url);
  function J(o, st) { return Promise.resolve({ ok: !st || st < 400, status: st || 200, json: function () { return Promise.resolve(JSON.parse(JSON.stringify(o))); } }); }
  if (/\/api\/onboarding\/state/.test(u)) return J(STATE);
  if (/\/api\/onboarding\/apply/.test(u)) { var b = JSON.parse(init.body || "{}"); POSTS.push(b); return J({ ok: true, errors: [], applied: b }); }
  if (/\/api\/onboarding\/done/.test(u)) return J({ ok: true, done: true, ts: 1 });
  if (/\/api\/claude\/foryou/.test(u)) return J({ enabled: false });
  if (/\/api\/models/.test(u)) return J({ models: [] });
  return Promise.reject(new Error("fixture: no route for " + u));
};
// a step another module pushed BEFORE the sheet loaded (contract 1)
(window.hermesOnboardingSteps = window.hermesOnboardingSteps || []).push({
  key: "harness", title: "Harness step", sub: "Registered before the sheet loaded", after: "prefs", optional: true,
  render: function () { return '<p class="onb-p hx">An extra step.</p><input class="hx-in" aria-label="field">'; },
  wire: function () {},
  save: function () { HX.saves++; return Promise.resolve(HX.reply); },
  summary: function () { return ["Harness: summary line"]; }
});
</script>
<script src="/vendor/motion/motion-14.6.0.min.js"></script>
<script src="/aux_anim.js"></script>
<script>
const $ = function (id) { return document.getElementById(id); };
${shim}
</script>
<script src="/aux_onboarding.js"></script>
<script>
var A = window.HermesAnim, OB = window.hermesOnboarding;
function sheet() { return $("onb-sheet"); }
function q(s) { return sheet() ? sheet().querySelector(s) : null; }
function key() { return OB.steps()[OB.state.step]; }
function idle() { return until(function () { return A.stats().running === 0; }, 5000); }
function inline(el) { return el ? el.style.cssText : "(gone)"; }
function clean(el) { return !!el && !/opacity|transform|translate|scale|rotate/.test(el.style.cssText); }
function tx(el) { if (!el) return 0; var m = new DOMMatrix(getComputedStyle(el).transform); return m.m41; }
async function click(act) { var b = q('.onb-foot button[data-act="' + act + '"]'); b.click(); await sleep(30); }

async function main() {
  if (!REDUCED) { await A.load("gsap"); await A.load("anime"); }
  var s0 = A.stats().started;

  // ---- opening
  var openSeen = { op: false };
  var p = OB.open({ rerun: false });
  var watch = frames(function () { var n = sheet(); if (n && !n.hidden && +getComputedStyle(n).opacity < 0.98) openSeen.op = true; return 0; },
                     function () { return !!openSeen.done; }, 1500);
  await p; await idle(); openSeen.done = true; await watch;
  ck("the sheet opened on step 1", !sheet().hidden && key() === "welcome");
  ck("...and nothing is left inline on it", clean(sheet()) && +getComputedStyle(sheet()).opacity === 1, inline(sheet()));
  if (REDUCED) ck("Reduce motion: opening started nothing", A.stats().started === s0, A.stats().started - s0);
  else ck("it faded in (opacity below 1 on some frame)", openSeen.op);

  // ---- a step change, forward
  var s1 = A.stats().started, seen = { right: false, op: false, bar: 0 };
  var w2 = frames(function () {
    var col = q(".onb-col"); if (!col) return 0;
    var x = tx(col); if (x > 2) seen.right = true;
    if (+getComputedStyle(col).opacity < 0.98) seen.op = true;
    var sc = q(".onb-scroll"); if (sc) seen.bar = Math.max(seen.bar, sc.scrollWidth - sc.clientWidth);
    return x;
  }, function () { return !!seen.done; }, 2000);
  await click("next"); await idle(); seen.done = true; await w2;
  var col = q(".onb-col"), dot = sheet().querySelectorAll(".onb-rail li")[1], h1 = q("h1.onb-t");
  ck("Continue moved to step 2", key() === "models");
  ck("the column, the rail dot and the heading end under CSS control (no inline transform/opacity/scale/rotate)",
     clean(col) && clean(dot) && clean(h1), [inline(col), inline(dot), inline(h1)]);
  ck("...no individual scale/rotate baked onto the dot (the wave-B GSAP finding)", !dot.style.scale && !dot.style.rotate &&
     getComputedStyle(dot).scale === "none", [dot.style.scale, dot.style.rotate, getComputedStyle(dot).scale]);
  ck("...the heading is plain text again (words() reverted its split)", h1.children.length === 0 &&
     h1.textContent === "Your Mac, and the brain that fits it", h1.innerHTML);
  ck("...the step-scroll never scrolled sideways during the slide", seen.bar === 0, seen.bar);
  if (REDUCED) ck("Reduce motion: the step change started nothing", A.stats().started === s1, A.stats().started - s1);
  else {
    ck("the column came in from the right (+x) and faded in", seen.right && seen.op, seen);
    ck("...through the runtime (sequence + words = at least two animations)", A.stats().started - s1 >= 2, A.stats().started - s1);
  }

  // ---- back
  var s2 = A.stats().started, left = { seen: false };
  var w3 = frames(function () { var c = q(".onb-col"); if (c && tx(c) < -2) left.seen = true; return 0; }, function () { return !!left.done; }, 2000);
  await click("back"); await idle(); left.done = true; await w3;
  ck("Back returned to step 1 and the column ended clean", key() === "welcome" && clean(q(".onb-col")), inline(q(".onb-col")));
  if (!REDUCED) ck("...coming in from the LEFT (-x)", left.seen);

  // ---- a toggle repaint starts nothing
  var s3 = A.stats().started;
  var sw = q('input[data-pref="news"]'); sw.click(); await sleep(60);
  ck("a toggle repainted the step (News off) and started nothing", OB.state.prefs.news === false && A.stats().started === s3,
     A.stats().started - s3);

  // ---- the extra step: Skip, an err that keeps the step
  OB.go(2); await idle();
  await click("next"); await idle();
  ck("prefs -> the registered extra step", key() === "harness" && /An extra step\./.test(q(".onb-body").textContent));
  q(".hx-in").value = "typed";
  HX.reply = { err: "Nope, not that key." };
  q('.onb-foot button[data-act="next"]').focus();
  await click("next"); await sleep(80);
  ck("...and focus is still inside the modal on the action row (a disabled-then-enabled row must not park it on <body>)",
     sheet().contains(document.activeElement) && document.activeElement !== document.body &&
     document.activeElement.getAttribute("data-act") === "next", document.activeElement.tagName);
  ck("its save() err keeps the step, shown in place, the typed value intact",
     key() === "harness" && q(".onb-err").textContent === "Nope, not that key." && !q(".onb-err").hidden && q(".hx-in").value === "typed");
  HX.reply = { applied: ["Harness: saved"] };
  await click("next"); await idle(); await sleep(60);
  ck("a clean save goes on to Ready", key() === "done");

  // ---- the Ready step's figure counts up
  var fig = q(".onb-sum .onb-fig"), vals = [];
  ck("the Ready step marks its figure (10 minutes)", !!fig && fig.getAttribute("data-fig") === "10");
  await idle();
  fig = q(".onb-sum .onb-fig");
  ck("...which ends on its real value, helpers gone (aria-label, min-width)", fig.textContent === "10" &&
     fig.getAttribute("aria-label") === null && fig.style.minWidth === "", [fig.textContent, fig.getAttribute("aria-label"), fig.style.minWidth]);
  ck("the extra step's applied + summary lines are listed", /Harness: saved/.test(q("ul.onb-sum").textContent) &&
     /Harness: summary line/.test(q("ul.onb-sum").textContent));
  // count-up observed: replay Ready from prefs and sample the text
  OB.go(2); await idle();
  var w4 = frames(function () { var f = q(".onb-sum .onb-fig"); if (f) vals.push(f.textContent); return 0; }, function () { return !!vals.done; }, 2500);
  OB.state.applied = []; await click("next"); await sleep(30);           // prefs -> harness
  HX.reply = {}; await click("skip"); await idle(); vals.done = true; await w4;
  if (REDUCED) ck("Reduce motion: the figure shows its value at once", vals.length > 0 && vals.every(function (v) { return v === "10"; }), vals.slice(0, 6));
  else ck("the figure counted up through lower values to 10", vals.some(function (v) { return +v < 10; }) && vals[vals.length - 1] === "10", vals.slice(0, 8));

  // ---- the models step through a stub advisor (contract 2)
  window.hermesAdvisor = { v: 1, step: {
    render: function () { return '<div class="adv-stub"><p class="onb-p">Detected: Apple M5 Max</p></div>'; },
    wire: function (node, ctx) { window.__advWired = (window.__advWired || 0) + 1; setTimeout(function () { ctx.setPick({ primary: M27B, background: M9B, download_gb: 23 }); }, 30); },
    save: function () { return Promise.resolve({}); } } };
  OB.go(1); await sleep(120); await idle();
  ck("with an advisor, its body renders above the sheet's action row", !!q(".onb-adv .adv-stub") && !q(".onb-rec") &&
     q(".onb-adv").getBoundingClientRect().top < q(".onb-foot").getBoundingClientRect().top);
  ck("...setPick repaints the action row: 'Download recommended (~23 GB)'", q(".onb-go").textContent === "Download recommended (~23 GB)",
     q(".onb-go").textContent);
  ck("...without re-wiring the advisor body", window.__advWired === 1, window.__advWired);

  OB.close();
  ck("no page error, no unhandled rejection", ERR.length === 0, ERR);
  ck("no warning but the runtime's own allowlist ones", WARN.every(function (w) {
    return /no JS library animates inside <header>|may animate transform and opacity only/.test(w); }), WARN);
}
window.addEventListener("load", function () { main().catch(function (e) { ck("the fixture ran to the end", false, String(e && e.stack || e)); }).then(function () { window.__results = R; window.__done = true; }); });
</script></body></html>`;

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

async function runPage(chrome, base, pagePath, query, reduceOS) {
  const port = 9300 + Math.floor(Math.random() * 600);
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-onb-chrome-"));
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
  console.log("The first-run sheet against the real Motion 14, GSAP 3 and anime 4 (headless Chromium)");
  const missing = Object.keys(FILES).filter((k) => !fs.existsSync(FILES[k]));
  if (missing.length) skipAll("not all the vendored libraries / aux files are present (" + missing.join(", ") + ")");
  if (typeof WebSocket === "undefined") skipAll("this node has no global WebSocket (needs 22+)");
  const chrome = findChrome();
  if (!chrome) skipAll("no Chromium found (set HERMES_CHROME, or `playwright install chromium`)");
  const shim = pageShim();
  check("the page shim (animate / ANIM_NULL / HA) was found in dashboard/index.html", shim.length > 200 && /const HA=/.test(shim));
  if (!shim) { console.log("TESTS " + pass + " passed " + fail + " failed"); process.exit(1); }
  const srv = await serve({ "/onb.html": PAGE(shim) });
  const base = "http://127.0.0.1:" + srv.address().port;
  try {
    console.log("  (chromium: " + path.basename(path.dirname(chrome)) + ")");
    for (const [label, query, reduce] of [["libraries live", "", false], ["OS reduced motion", "reduce", true]]) {
      console.log("[" + label + "]");
      const rs = await runPage(chrome, base, "/onb.html", query, reduce);
      rs.forEach((r) => check(r.name, r.ok, r.extra));
    }
  } finally { srv.close(); }
  console.log("TESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS " + pass + " passed " + (fail + 1) + " failed"); process.exit(1); });
