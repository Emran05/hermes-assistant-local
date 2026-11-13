// t_anim_popouts.js — motion cluster C5 (docs/plans/motion-system-2026-10.md
// §8.11, §10.1 "t_anim_popouts.js"): pop-out numbers and paint clean-up.
//
//   * the Flight Recorder animates only rows it has never shown (E13: its 3 s
//     poll used to re-fade every row) — at most 12 on the first paint, none on
//     a repaint of the same ids, none while reduced or quiet (the key is still
//     spent), exactly the new one when one arrives;
//   * its Undo flash is a CSS class (an ::after overlay fading by opacity), not
//     a backgroundColor tween through Motion;
//   * the Claude Usage pop-out and the Vitals card count their headline
//     numbers up with HermesAnim.count — once per open / on the owner's own
//     24h-7d switch, never on the compact widget or a poll;
//   * the aux_memory and aux_youmodel meters scale (transform:scaleX), never a
//     width; their A() wrappers go through HA(), so either reduce switch lands
//     the end state at once; memory's background poll repaints quietly and a
//     refused delete brings the chip back.
//
// Boots the REAL index.html + aux_anim.js (the runtime) + the five cluster
// files in the mini DOM (_a11y_dom.js) over the recording library stubs
// (_anim_stubs.js: nothing schedules a timer; a test ends an animation
// itself). Fetches are answered by in-memory routes — no dashboard, no model.
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, harness } = require("./_a11y_dom.js");
const S = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const DASH = path.join(REPO, "dashboard");
const src = function (f) { return fs.readFileSync(path.join(DASH, f), "utf8"); };
const ANIM = src("aux_anim.js");
const AUX = ["expand.js", "aux_memory.js", "aux_recorder.js", "aux_metrics.js", "aux_claude_usage.js", "aux_youmodel.js"];

let L = null;
function withRuntime(w0) { L = S.install(w0, { anime: true }); w0.__run(ANIM, "aux_anim.js"); }
function noRuntime(w0) { L = S.install(w0, { anime: true }); }
async function boot(routes, opts) {
  opts = opts || {};
  return bootDashboard({ aux: AUX, routes: routes || {}, media: opts.media, before: opts.runtime === false ? noRuntime : withRuntime });
}
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}
const hasCls = function (el, c) { return !!(el && el.classList && el.classList.contains(c)); };
// every Motion call, one entry per ELEMENT it moved (a call on a NodeList — what
// the old recorder did on every poll — counts once per element in it)
const motionOn = function (pred) {
  const out = [];
  L.of("motion", "animate").forEach(function (c) {
    S.listOf(c.el).forEach(function (el) { if (pred(el)) out.push({ el: el, kf: c.kf, opt: c.opt, h: c.h }); });
  });
  return out;
};
const animeCounts = function () { return L.of("anime", "animate").filter(function (c) { return c.params && c.params.v !== undefined; }); };
function styleOf(w, id) { const s = w.document.getElementById(id); return s ? s.textContent : ""; }
function rule(css, sel) {           // the declaration block of the first rule whose selector is exactly sel
  const i = css.indexOf(sel + "{");
  if (i < 0) return null;
  return css.slice(i + sel.length + 1, css.indexOf("}", i));
}

// ---------------------------------------------------------------------------
// Flight Recorder
// ---------------------------------------------------------------------------
function act(id, extra) {
  return Object.assign({ id: id, kind: "write", reversible: "yes", status: "done", tool: "write_file",
    target: "/p/" + id + ".txt", source: "hub", ts: 1.8e9 - id }, extra || {});
}
function recPayload(ids, undone) {
  return { recorder_ok: true, checkpoints_enabled: true, counts: { reversible: ids.length, undone: 0 },
    actions: ids.map(function (i) { return act(i, undone && undone.indexOf(i) >= 0 ? { status: "undone" } : null); }) };
}
function range(a, b) { const o = []; for (let i = a; i >= b; i--) o.push(i); return o; }

async function recorderSuite() {
  console.log("\nFlight Recorder: only new rows enter (E13)");
  const REC = { p: recPayload(range(120, 101)) };              // 20 rows, newest first
  const w = await boot({ "/api/recorder": function () { return REC.p; }, "/api/recorder/retention": { ok: true, retain_days: 90, total: 20 },
    "/api/undo": { ok: true } });
  const d = w.document, A = w.HermesAnim;
  const rowsAnimated = function () { return motionOn(function (el) { return hasCls(el, "rec-row"); }); };
  const cap0 = A.stats().instant.cap;
  await w.__run("loadRecorder()"); await w.__tick();
  const rows = d.querySelectorAll("#rec-list .rec-row");
  let first = rowsAnimated();
  T.check("the first paint built every row", rows.length === 20, rows.length);
  T.check("...and animated at most 12 of them (T.stagger.max), the newest first", first.length === 12 &&
    first[0].el.getAttribute("data-id") === "120" && first[11].el.getAttribute("data-id") === "109",
    first.map(function (c) { return c.el.getAttribute("data-id"); }));
  T.check("...with the 'fade' preset (opacity only) as one staggered list", first.every(function (c) {
    return JSON.stringify(c.kf) === '{"opacity":[0,1]}'; }) && first[1].opt.delay > first[0].opt.delay, first.map(function (c) { return c.opt.delay; }));
  T.check("...the tail of the list landed at once (the runtime's cap counter)", A.stats().instant.cap === cap0 + 1);
  T.check("no row was pre-hidden with an inline opacity beyond the animated ones",
    Array.prototype.slice.call(rows, 12).every(function (r) { return r.style.opacity === ""; }));
  L.finishAll(); await w.__tick();
  T.check("finished rows are handed back to CSS (no inline opacity/transform left)",
    Array.prototype.every.call(rows, function (r) { return r.style.opacity === "" && r.style.transform === ""; }));

  L.clear();
  await w.__run("loadRecorder()"); await w.__tick();
  const rebuilt = d.querySelectorAll("#rec-list .rec-row");
  T.check("a poll with the same ids repaints with zero animations", rowsAnimated().length === 0 && L.of("motion", "animate").length === 0,
    L.calls.map(function (c) { return c.lib + "." + c.fn; }));
  T.check("(the poll really did repaint: the same ids, rebuilt)", rebuilt.length === 20);

  L.clear();
  REC.p = recPayload([121].concat(range(120, 101)));
  await w.__run("loadRecorder()"); await w.__tick();
  let a = rowsAnimated();
  T.check("one new action: exactly its row enters", a.length === 1 && a[0].el.getAttribute("data-id") === "121",
    a.map(function (c) { return c.el.getAttribute("data-id"); }));
  L.finishAll();

  L.clear();
  w.__run("recSetFilter('write')"); await w.__tick();
  w.__run("recSetFilter('all')"); await w.__tick();
  T.check("switching a filter away and back does not replay rows already shown", rowsAnimated().length === 0);

  console.log("\nFlight Recorder: reduced and quiet spend the key too");
  L.clear();
  d.documentElement.setAttribute("data-reduce", "1"); await w.__tick();
  REC.p = recPayload([122, 121].concat(range(120, 101)));
  await w.__run("loadRecorder()"); await w.__tick();
  T.check("reduced: a new row lands with no animation", rowsAnimated().length === 0 && !!d.querySelector('.rec-row[data-id="122"]'));
  d.documentElement.removeAttribute("data-reduce"); await w.__tick();
  w.__run("recRender()");
  T.check("...and does not enter later either (its key was spent while reduced)", rowsAnimated().length === 0);
  L.clear();
  w.__run("HA().quiet(function(){ recState.last = " + JSON.stringify(recPayload([123, 122, 121].concat(range(120, 101)))) + "; recRender(); })");
  T.check("quiet (the Agent rail's poll): a new row lands still", rowsAnimated().length === 0 && !!d.querySelector('.rec-row[data-id="123"]'));
  w.__run("recRender()");
  T.check("...and a later live repaint does not replay it", rowsAnimated().length === 0);

  console.log("\nFlight Recorder: the Undo flash is a class, not a tween");
  L.clear();
  REC.p = recPayload([123, 122, 121].concat(range(120, 101)), [121]);
  await w.__run("recUndo('121', true)"); await w.__tick();
  const r121 = d.querySelector('.rec-row[data-id="121"]');
  T.check("after the reload the undone row carries .rec-flash", hasCls(r121, "rec-flash") && hasCls(r121, "is-undone"),
    r121 && r121.className);
  T.check("no library animated a colour (no backgroundColor through Motion)", !L.of("motion", "animate").some(function (c) {
    return c.kf && typeof c.kf === "object" && ("backgroundColor" in c.kf || "background" in c.kf); }));
  T.check("the flash itself is not a Motion call at all", motionOn(function (el) { return el === r121; }).length === 0);
  const css = styleOf(w, "reccss");
  const after = rule(css, ".rec-row.rec-flash::after") || "";
  T.check("CSS: an ::after overlay at rest opacity 0, behind the content, not hit-testable",
    /opacity:0/.test(after) && /z-index:-1/.test(after) && /pointer-events:none/.test(after) &&
    /position:absolute/.test(rule(css, ".rec-row.rec-flash::after") || "") && /isolation:isolate/.test(rule(css, ".rec-row.rec-flash") || ""), after);
  T.check("CSS: it fades over --d-micro (the design's .15) and the keyframe moves opacity only",
    /animation:rec-flash var\(--d-micro,150ms\)/.test(after) && css.indexOf("@keyframes rec-flash{from{opacity:1}to{opacity:0}}") >= 0);
  T.check("CSS: the OS reduce query switches the flash off", /@media \(prefers-reduced-motion:reduce\)\{\.rec-row\.rec-flash::after\{animation:none\}\}/.test(css));
  const ev = new w.Event("animationend", { bubbles: true }); ev.animationName = "rec-flash";
  r121.dispatchEvent(ev);
  T.check("animationend takes the class off again", !hasCls(r121, "rec-flash"));
  d.documentElement.setAttribute("data-reduce", "1"); await w.__tick();
  await w.__run("recUndo('120', true)"); await w.__tick();
  T.check("reduced (the in-app switch): no flash at all", !hasCls(d.querySelector('.rec-row[data-id="120"]'), "rec-flash"));
  d.documentElement.removeAttribute("data-reduce");
  T.check("the recorder raised no page error", w.__errors.length === 0, w.__errors.map(String));
}

// ---------------------------------------------------------------------------
// Claude Usage pop-out
// ---------------------------------------------------------------------------
const USAGE = {
  available: true, block_hours: 5, cap: 2000000, block_peak: 2500000,
  window: { total: 1234567, active: true, reset_at: Math.floor(Date.now() / 1000) + 3600, cost: 12.5,
    in: 1000, out: 2000, cr: 300000, cc: 4000, msgs: 42, cache: 5000 },
  today: { total: 45678, cost: 1.2, msgs: 10 }, week: { total: 987654, cost: 30, msgs: 400 },
  days: [1, 2, 3, 4, 5, 6, 7].map(function (i) { return { date: "2026-10-2" + i, total: i * 1000 }; }),
  spark: [1, 2, 3], day_peak: 3, week_total: 987654, models: [], projects: [], blocks: [], sessions: 3, messages: 40, scan_days: 8
};
async function usageSuite() {
  console.log("\nClaude Usage pop-out: the token totals count up once per open");
  const w = await boot();
  const d = w.document, A = w.HermesAnim;
  const EXP = w.__run("EXPAND_RENDER.claude_usage"), RND = w.__run("RENDER.claude_usage");
  const open = function () { const el = d.createElement("div"); d.body.appendChild(el); EXP(el, JSON.parse(JSON.stringify(USAGE))); return el; };
  let el = open();
  const hero = el.querySelector('[data-cu-count="window"]'), tot = el.querySelectorAll(".cu-totals .num");
  let c = animeCounts();
  T.check("three count-ups: the 5-hour window, today and the last 7 days", c.length === 3 &&
    c[0].params.v === 1234567 && c[1].params.v === 45678 && c[2].params.v === 987654, c.map(function (x) { return x.params.v; }));
  T.check("...each from 0", c.every(function (x) { return x.targets && x.targets.v === 0; }));
  T.check("...the cells are the right ones (hero, 'Today tok', '7-day tok')", c[0].a && hero &&
    tot.length === 6 && tot[0].nextSibling.textContent === "Today tok" && tot[3].nextSibling.textContent === "7-day tok");
  T.check("while counting, the number starts at 0 in the final unit (no frame of the final value first)",
    hero.textContent === "0.0M" && tot[0].textContent === "0.0k" && tot[3].textContent === "0.0k", [hero.textContent, tot[0].textContent]);
  T.check("...and screen readers get the final value", hero.getAttribute("aria-label") === "1.2M" && tot[0].getAttribute("aria-label") === "45.7k");
  c[0].a.step(431000);
  T.check("an intermediate frame keeps the final value's unit (one width)", hero.textContent === "0.4M", hero.textContent);
  T.check("...and its width is fixed in ch for the run", hero.style.minWidth === "4ch", hero.style.minWidth);
  L.finishAll(); await w.__tick();
  T.check("finished: exactly the text the pop-out renders without motion", hero.textContent === "1.2M" &&
    tot[0].textContent === "45.7k" && tot[3].textContent === "987.7k", [hero.textContent, tot[0].textContent, tot[3].textContent]);
  T.check("...the aria-label and min-width are handed back", !hero.hasAttribute("aria-label") && hero.style.minWidth === "");
  T.check("the daily bars still reveal through the legacy animate() shim (transform/opacity)",
    motionOn(function (x) { return hasCls(x, "cu-bar"); }).length === 7);

  L.clear();
  const body = d.createElement("div"); d.body.appendChild(body);
  RND(body, JSON.parse(JSON.stringify(USAGE)), null);
  RND(body, JSON.parse(JSON.stringify(USAGE)), null);
  T.check("the compact widget (refreshed by the hub every 60 s) never counts or animates", L.calls.length === 0,
    L.calls.map(function (x) { return x.lib + "." + x.fn; }));

  L.clear();
  el = open();
  T.check("a second open counts again (once per OPEN)", animeCounts().length === 3);
  L.finishAll();

  console.log("\nClaude Usage pop-out: instant end states");
  L.clear();
  d.documentElement.setAttribute("data-reduce", "1"); await w.__tick();
  el = open();
  T.check("reduced: no count-up, the final text at once", animeCounts().length === 0 &&
    el.querySelector('[data-cu-count="window"]').textContent === "1.2M");
  d.documentElement.removeAttribute("data-reduce"); await w.__tick();
  L.clear();
  d.hidden = true;
  el = open();
  T.check("hidden window: no count-up, the final text at once", animeCounts().length === 0 &&
    el.querySelectorAll(".cu-totals .num")[3].textContent === "987.7k");
  d.hidden = false;

  console.log("\nClaude Usage: gauge fills scale, never a width");
  const fills = Array.prototype.slice.call(el.querySelectorAll("i")).filter(function (i) { return i.hasAttribute("style"); });
  T.check("the pop-out's soft-cap gauge is transform:scaleX from the left", fills.length >= 1 && fills.every(function (i) {
    const s = i.getAttribute("style"); return /transform:scaleX\(0\.62\)/.test(s) && /transform-origin:left center/.test(s) && !/width:/.test(s); }),
    fills.map(function (i) { return i.getAttribute("style"); }));
  RND(body, JSON.parse(JSON.stringify(USAGE)), null);
  const cf = body.querySelector("i");
  T.check("...and so is the compact widget's cap bar", !!cf && /transform:scaleX\(0\.62\)/.test(cf.getAttribute("style")) && !/width:/.test(cf.getAttribute("style")));
  T.check("the usage renderers raised no page error", w.__errors.length === 0, w.__errors.map(String));
}

// ---------------------------------------------------------------------------
// Vitals card (aux_metrics)
// ---------------------------------------------------------------------------
function metrics(k) {
  return { ok: true, turns: { n: 3, ttft_ms: { p50: 1400 * k, p95: 2000 }, turn_ms: { p50: 4000 * k, p95: 9000 }, est_tok_per_sec: { p50: 42.5 * k } },
    hub_api: { p50: 4, p95: 20 * k }, ram: {}, model: {}, approvals: { requested: 5 * k, approved: 4, denied: 1 }, undo: { count: 3 * k }, targets: {} };
}
async function vitalsSuite() {
  console.log("\nVitals: the headline numbers count up on the first fill and the owner's 24h/7d switch");
  const M = { k: 1 };
  const w = await boot({ "/api/metrics": function (url) { return metrics(/days=7/.test(url) ? 2 : M.k); } });
  const d = w.document;
  d.getElementById("view-console").removeAttribute("hidden");        // the card's host here: on screen, so the count-ups run now
  const grid = function () { return d.getElementById("vt-grid"); };
  const val = function (i) { return grid().querySelectorAll(".vt-val")[i]; };
  const pulses = function () { return motionOn(function (el) { return el && el.id === "vt-grid"; }); };
  try { w.localStorage.setItem("hermes_metrics_win", "24h"); } catch (e) {}
  await w.__run("vitalsRefresh()"); await w.__tick();
  let c = animeCounts();
  T.check("first fill: six count-ups (TTFT, Turn, Hub API, Tok/s, Approvals, Undo)", c.length === 6 &&
    JSON.stringify(c.map(function (x) { return x.params.v; })) === JSON.stringify([1400, 4000, 20, 42.5, 5, 3]), c.map(function (x) { return x.params.v; }));
  T.check("...each from 0", c.every(function (x) { return x.targets.v === 0; }));
  T.check("...one soft grid pulse with them", pulses().length === 1);
  T.check("the TTFT tile counts in the final unit ('0.0s' ... '1.4s'), aria-label = the final value",
    val(0).textContent === "0.0s" && val(0).getAttribute("aria-label") === "1.4s", [val(0).textContent, val(0).getAttribute("aria-label")]);
  c[0].a.step(999);
  T.check("...an intermediate frame never switches unit ('1.0s', not '999ms')", val(0).textContent === "1.0s", val(0).textContent);
  T.check("the RAM tile (an em-dash) is written as text and never counts", val(3).textContent === "—" && !c.some(function (x) { return x.a && x.params && x.params.v === null; }));
  L.finishAll(); await w.__tick();
  T.check("finished: the tiles' own text", val(0).textContent === "1.4s" && val(1).textContent === "4.0s" && val(2).textContent === "20ms" &&
    val(4).textContent === "42.5" && val(5).textContent === "5" && val(6).textContent === "3", [0, 1, 2, 4, 5, 6].map(function (i) { return val(i).textContent; }));

  L.clear();
  M.k = 3;
  await w.__run("vitalsRefresh()"); await w.__tick();
  T.check("a poll repaints with NO motion: no count-up, no grid pulse", animeCounts().length === 0 && pulses().length === 0 &&
    L.of("motion", "animate").length === 0, L.calls.map(function (x) { return x.lib + "." + x.fn; }));
  T.check("...the new values are simply there", val(0).textContent === "4.2s" && val(5).textContent === "15", [val(0).textContent, val(5).textContent]);

  L.clear();
  d.querySelector('#vt-seg b[data-win="7d"]').click(); await w.__tick(20);
  c = animeCounts();
  T.check("the 7d switch counts from what was on screen to the new values", c.length === 6 &&
    c[0].targets.v === 4200 && c[0].params.v === 2800 && c[5].targets.v === 9 && c[5].params.v === 6,
    c.map(function (x) { return [x.targets.v, x.params.v]; }));
  T.check("...with one grid pulse", pulses().length === 1);
  L.finishAll(); await w.__tick();

  L.clear();
  d.documentElement.setAttribute("data-reduce", "1"); await w.__tick();
  d.querySelector('#vt-seg b[data-win="24h"]').click(); await w.__tick(20);
  T.check("reduced: the switch lands the numbers at once", animeCounts().length === 0 && val(0).textContent === "4.2s", val(0).textContent);
  T.check("...the pulse is instant too (duration 0)", pulses().length === 1 && pulses()[0].opt.duration === 0, pulses().map(function (x) { return x.opt; }));
  d.documentElement.removeAttribute("data-reduce");
  T.check("CSS: a counting value is contained (layout + paint)", /\.vt-val\{[^}]*contain:layout paint/.test(styleOf(w, "vitals-css")));
  T.check("the Vitals card raised no page error", w.__errors.length === 0, w.__errors.map(String));
}

// A 24h/7d switch that arrives while a fetch is in flight used to be dropped:
// the toggle showed 7d over 24h's numbers until some later poll. It is
// remembered and re-run, and the in-flight (other-window) answer is not painted.
async function vitalsSwitchSuite() {
  console.log("\nVitals: a 24h/7d switch during an in-flight fetch is not lost");
  const pend = [];
  const w = await boot({ "/api/metrics": function (url) {
    return new Promise(function (res) { pend.push({ url: url, release: function () { res(metrics(/days=7/.test(url) ? 2 : 1)); } }); });
  } });
  const d = w.document;
  d.getElementById("view-console").removeAttribute("hidden");
  const val = function (i) { return d.querySelectorAll("#vt-grid .vt-val")[i]; };
  try { w.localStorage.setItem("hermes_metrics_win", "24h"); } catch (e) {}
  const first = w.__run("vitalsRefresh()");                    // the first fill: the 24h fetch stays in flight
  await w.__tick();
  T.check("(the first fetch is in flight, for 24h)", pend.length === 1 && /days=1/.test(pend[0].url), pend.map(function (p) { return p.url; }));
  d.querySelector('#vt-seg b[data-win="7d"]').click(); await w.__tick(20);
  T.check("the switch did not start a second fetch while one was running", pend.length === 1);
  pend[0].release(); await first; await w.__tick(20);
  T.check("the 24h answer is for the window the owner left: not painted (the skeleton is still up)",
    !!d.querySelector("#vt-grid .vt-skel") && animeCounts().length === 0, animeCounts().length);
  T.check("...the switch is re-run once the fetch settles, for 7d", pend.length === 2 && /days=7/.test(pend[1].url), pend.map(function (p) { return p.url; }));
  pend[1].release(); await w.__tick(20);
  const c = animeCounts();
  T.check("the 7d numbers are what the card shows and counts up to (from 0: it is still the first fill)",
    c.length === 6 && c[0].params.v === 2800 && c[0].targets.v === 0 && !c.some(function (x) { return x.params.v === 1400; }),
    c.map(function (x) { return x.params.v; }));
  L.finishAll(); await w.__tick();
  T.check("...and they stand: 2.8s TTFT, 6 approvals", val(0).textContent === "2.8s" && val(5).textContent === "10", [val(0).textContent, val(5).textContent]);
  T.check("exactly two fetches were made (no loop)", pend.length === 2);
  T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
}

// The card mounts into Settings > Insights, a panel nobody has opened yet when
// the first fill lands, so its count-up would play to nobody. It waits for the
// first time the grid is on screen (once); polls meanwhile only refresh the
// numbers it will end on.
async function vitalsFirstViewSuite() {
  console.log("\nVitals: a first fill in an unopened panel counts when it is first seen");
  const M = { k: 1 };
  const w = await boot({ "/api/metrics": function () { return metrics(M.k); } });
  const d = w.document;
  const ios = [];
  w.IntersectionObserver = class {
    constructor(cb) { this.cb = cb; this.t = []; this.off = false; ios.push(this); }
    observe(t) { this.t.push(t); } unobserve() {} disconnect() { this.off = true; }
  };
  const grid = function () { return d.getElementById("vt-grid"); };
  const val = function (i) { return grid().querySelectorAll(".vt-val")[i]; };
  const pulses = function () { return motionOn(function (el) { return el && el.id === "vt-grid"; }); };
  try { w.localStorage.setItem("hermes_metrics_win", "24h"); } catch (e) {}
  T.check("(#view-console, the card's host in this page, is hidden: the grid has no box)", d.getElementById("view-console").hasAttribute("hidden"));
  await w.__run("vitalsRefresh()"); await w.__tick();
  T.check("hidden panel: the final numbers are in place, nothing counts or pulses", val(0).textContent === "1.4s" && val(5).textContent === "5" &&
    animeCounts().length === 0 && pulses().length === 0, [val(0).textContent, animeCounts().length]);
  T.check("...and one observer waits on the grid", ios.length === 1 && ios[0].t.length === 1 && ios[0].t[0] === grid());
  M.k = 2;
  await w.__run("vitalsRefresh()"); await w.__tick();
  T.check("a poll meanwhile repaints in place: no motion, still one observer", animeCounts().length === 0 && pulses().length === 0 &&
    ios.length === 1 && val(0).textContent === "2.8s", [ios.length, val(0).textContent]);
  d.getElementById("view-console").removeAttribute("hidden");
  ios[0].cb([{ isIntersecting: false, target: grid() }]);
  T.check("an observer callback that is not an intersection starts nothing", animeCounts().length === 0 && !ios[0].off);
  ios[0].cb([{ isIntersecting: true, target: grid() }]); await w.__tick();
  const c = animeCounts();
  T.check("first seen: the six counts run, from 0, to the LATEST numbers", c.length === 6 &&
    JSON.stringify(c.map(function (x) { return x.params.v; })) === JSON.stringify([2800, 8000, 40, 85, 10, 6]) &&
    c.every(function (x) { return x.targets.v === 0; }), c.map(function (x) { return x.params.v; }));
  T.check("...with the one grid pulse, and the observer let go", pulses().length === 1 && ios[0].off);
  T.check("...the tiles start at 0 in the final unit (no frame of the final value first)", val(0).textContent === "0.0s", val(0).textContent);
  L.finishAll(); await w.__tick();
  T.check("...and end on the tiles' own text", val(0).textContent === "2.8s" && val(5).textContent === "10", [val(0).textContent, val(5).textContent]);
  L.clear();
  ios[0].cb([{ isIntersecting: true, target: grid() }]); M.k = 3;
  await w.__run("vitalsRefresh()"); await w.__tick();
  T.check("once: a later callback and a later poll make no motion", animeCounts().length === 0 && pulses().length === 0 && ios.length === 1 && val(0).textContent === "4.2s");

  console.log("\nVitals: without an IntersectionObserver nothing waits");
  const w2 = await boot({ "/api/metrics": function () { return metrics(1); } });
  w2.IntersectionObserver = undefined;
  L.clear();
  await w2.__run("vitalsRefresh()"); await w2.__tick();
  T.check("the first fill counts at once (the final text is there regardless)", animeCounts().length === 6 &&
    w2.document.querySelectorAll("#vt-grid .vt-val")[0].getAttribute("aria-label") === "1.4s");
  T.check("no page error", w.__errors.length === 0 && w2.__errors.length === 0, w.__errors.concat(w2.__errors).map(String));
}

// ---------------------------------------------------------------------------
// aux_memory + aux_youmodel
// ---------------------------------------------------------------------------
function memRoutes(state) {
  return {
    "/api/memory/list": function () { return { ok: true, files: state.files, trash: [], limits: { max_file_bytes: 131072 } }; },
    "/api/memory/file": function (url) {
      const n = decodeURIComponent((/name=([^&]+)/.exec(url) || [])[1] || "");
      return n === "USER.md"
        ? { ok: true, name: n, kind: "entries", entries: state.facts, char_used: 420, char_limit: 1000, etag: state.etag, size: 420, last_writer: "user" }
        : { ok: true, name: n, kind: "freeform", content: "hello", etag: "f1", size: 5, last_writer: "agent" };
    },
    "/api/memory/delete": function () { return state.del(); }
  };
}
async function memorySuite() {
  console.log("\naux_memory: the meter scales, A() goes through HA()");
  const st = { files: [{ name: "USER.md", last_writer: "user" }, { name: "notes.md", last_writer: "agent" }],
    facts: ["likes tea", "lives in Paris", "writes Swift"], etag: "e1", del: function () { return { __status: 423, body: { ok: false } }; } };
  const w = await boot(memRoutes(st));
  const d = w.document;
  w.renderMemoryPanel({}); await w.__tick(20);
  const fillEl = d.querySelector(".mem-meter i");
  T.check("the meter fill carries its fraction as --p (0.42), not a width", !!fillEl && /--p:0\.42/.test(fillEl.getAttribute("style") || "") &&
    !/width/.test(fillEl.getAttribute("style") || ""), fillEl && fillEl.getAttribute("style"));
  const mcss = Array.prototype.map.call(d.querySelectorAll("style"), function (s) { return s.textContent; }).join("\n");
  const mi = rule(mcss, ".mem-meter i") || "";
  T.check("CSS: transform:scaleX(var(--p)) from the left, a transform transition, no radius on the fill",
    /transform:scaleX\(var\(--p,0\)\)/.test(mi) && /transform-origin:left center/.test(mi) && /transition:transform \.3s/.test(mi) &&
    !/width/.test(mi) && !/border-radius/.test(mi), mi);
  T.check("...the track still clips (radius + overflow:hidden)", /overflow:hidden/.test(mcss.slice(mcss.indexOf(".mem-meter{"), mcss.indexOf(".mem-meter i"))));
  const rowsIn = motionOn(function (el) { return hasCls(el, "mem-row"); });
  T.check("opening USER.md: the fact rows enter (revealStagger -> HermesAnim)", rowsIn.length === 3, rowsIn.length);
  const det = motionOn(function (el) { return el && el.id === "mem-detail"; });
  T.check("the detail pane's A() went through HA().legacy (its 'ease-out' arrives as ease, live)",
    det.length === 1 && det[0].opt.ease === "easeOut" && !("easing" in det[0].opt) && det[0].opt.duration === 0.22, det.map(function (x) { return x.opt; }));
  L.finishAll(); await w.__tick();

  console.log("\naux_memory: the 15 s poll repaints quietly");
  L.clear();
  st.etag = "e2"; st.facts = ["likes tea", "lives in Paris", "writes Swift", "plays chess"];
  w.__run("curView='mind'");
  const poll = w.__intervals.filter(function (t) { return t.ms === 15000; }).pop();
  T.check("(the poll is the panel's 15 s interval)", !!poll);
  await poll.fn(); await w.__tick(20);
  T.check("a background update repainted the rows", d.querySelectorAll(".mem-row").length === 4);
  T.check("...with no animation on the rows or the detail pane", motionOn(function (el) {
    return hasCls(el, "mem-row") || (el && el.id === "mem-detail"); }).every(function (x) { return x.opt && x.opt.duration === 0; }) &&
    motionOn(function (el) { return hasCls(el, "mem-row"); }).length === 0, L.of("motion", "animate").map(function (x) { return x.el && (x.el.id || x.el.className); }));
  const notice = motionOn(function (el) { return hasCls(el, "mem-banner"); });
  T.check("...only the 'Hermes just updated this' notice enters", notice.length === 1 && notice[0].opt.duration > 0);
  L.finishAll();

  console.log("\naux_memory: a repaint that throws is not hidden");
  L.clear();
  d.getElementById("mem-banner").innerHTML = "";         // (the stub timers never ran the previous notice's 4 s clear)
  st.etag = "e3"; st.facts = {};                         // not an array: memRenderEntries throws on .forEach
  const poll2 = w.__intervals.filter(function (t) { return t.ms === 15000; }).pop();
  let threw = null;
  try { await poll2.fn(); } catch (e) { threw = e; }
  await w.__tick(20);
  const bn = d.getElementById("mem-banner");
  T.check("the poll's error reaches the caller instead of being swallowed by quiet()", !!threw, String(threw));
  T.check("...and no 'Hermes just updated this' is claimed for a repaint that failed", !bn || !/just updated/.test(bn.textContent || ""), bn && bn.textContent);
  st.facts = ["likes tea", "lives in Paris", "writes Swift", "plays chess"]; st.etag = "e4";
  L.finishAll(); await w.__tick();
  w.__errors.length = 0;

  console.log("\naux_memory: a refused delete brings the chip back");
  w.__run("document.querySelector('#mem-strip [data-open=\"notes.md\"]').click()"); await w.__tick(20);
  L.finishAll(); await w.__tick();
  L.clear();
  let release = null;
  st.del = function () { return new Promise(function (r) { release = r; }); };
  d.getElementById("mem-del").click(); await w.__tick();
  const chip = d.querySelector('#mem-strip [data-open="notes.md"]');
  const out = motionOn(function (el) { return el === chip; });
  T.check("the chip leaves through HermesAnim.exit('shrinkOut') while the request runs", out.length === 1 &&
    JSON.stringify(out[0].kf) === '{"opacity":[1,0],"scale":[1,0.86]}', out.map(function (x) { return x.kf; }));
  L.finishAll(); await w.__tick();
  T.check("...its done() hides it and hands opacity/transform back to CSS", chip.style.visibility === "hidden" && chip.style.opacity === "" && chip.style.transform === "");
  release({ __status: 423, body: { ok: false } }); await w.__tick(20);
  T.check("the server refused (423): the chip is visible again", chip.style.visibility === "" && chip.style.opacity === "" && !!chip.parentNode);
  L.clear();
  st.del = function () { return new Promise(function (r) { release = r; }); };
  d.getElementById("mem-del").click(); await w.__tick();
  release({ __status: 423, body: { ok: false } }); await w.__tick(20);
  T.check("refused before the exit finished: the exit is cancelled (done never runs), the chip stays visible",
    chip.style.visibility === "" && chip.style.opacity === "" && L.of("motion", "cancel").length >= 1);
  L.finishAll(); await w.__tick();
  T.check("...and finishing the cancelled animation later changes nothing", chip.style.visibility === "" && chip.style.opacity === "");
  T.check("aux_memory raised no page error", w.__errors.length === 0, w.__errors.map(String));

  console.log("\naux_memory under the OS reduce setting (REDUCE read at load)");
  const w2 = await boot(memRoutes({ files: st.files, facts: st.facts, etag: "x", del: st.del }), { media: function (q) { return /reduced-motion: ?reduce/.test(q); } });
  w2.renderMemoryPanel({}); await w2.__tick(20);
  const det2 = L.of("motion", "animate").filter(function (x) { return x.el && x.el.id === "mem-detail"; });
  T.check("A() is no longer skipped by the stale flag: it lands the end state at once (duration 0)",
    det2.length === 1 && det2[0].opt.duration === 0 && w2.document.getElementById("mem-detail").style.opacity === "1", det2.map(function (x) { return x.opt; }));
}

async function youmodelSuite() {
  console.log("\naux_youmodel: the meter scales, A() goes through HA()");
  const YM = { ok: true, filled: 1, total: 5, people: [], files: [
    { name: "GOALS.md", label: "Goals", exists: true, entries: ["ship 1.4"], char_used: 3000, char_budget: 4000, hint: "" },
    { name: "NOW.md", label: "Now", exists: false, entries: [], hint: "" }] };
  const w = await boot({ "/api/youmodel": YM });
  const d = w.document;
  await w.youModelPanel(); await w.__tick(20);
  const fi = d.querySelector(".ym-meter i");
  T.check("the meter fill carries --p (0.75), not a width", !!fi && /--p:0\.75/.test(fi.getAttribute("style") || "") && !/width/.test(fi.getAttribute("style") || ""),
    fi && fi.getAttribute("style"));
  const css = Array.prototype.map.call(d.querySelectorAll("style"), function (s) { return s.textContent; }).join("\n");
  const yi = (css.match(/\.ym-meter i\{[^}]*\}/) || [""])[0];
  T.check("CSS: scaleX(var(--p)) from the left with a transform transition, no width, no radius on the fill",
    /transform:scaleX\(var\(--p,0\)\)/.test(yi) && /transform-origin:left center/.test(yi) && /transition:transform \.3s/.test(yi) &&
    !/width/.test(yi) && !/border-radius/.test(yi), yi);
  T.check("CSS: the OS reduce query still drops the meter's transition", /\.ym-meter i\{transition:none\}/.test(css));
  const card = motionOn(function (el) { return el && el.id === "mind-extra-youmodel"; });
  T.check("the card entrance's A() went through HA().legacy (ease translated, live)", card.length === 1 && card[0].opt.ease === "easeOut" && card[0].opt.duration === 0.25,
    card.map(function (x) { return x.opt; }));
  L.clear();
  d.documentElement.setAttribute("data-reduce", "1"); await w.__tick();
  await w.youModelPanel(); await w.__tick(20);
  const card2 = motionOn(function (el) { return el && el.id === "mind-extra-youmodel"; });
  T.check("the in-app reduce switch reaches it: the end state lands at once", card2.length === 1 && card2[0].opt.duration === 0 &&
    d.getElementById("mind-extra-youmodel").style.opacity === "1");
  T.check("aux_youmodel raised no page error", w.__errors.length === 0, w.__errors.map(String));
}

// ---------------------------------------------------------------------------
// without the runtime (ANIM_NULL) the surfaces still work and land at once
// ---------------------------------------------------------------------------
async function nullSuite() {
  console.log("\nwithout aux_anim.js (ANIM_NULL): every surface lands at once");
  const w = await boot({ "/api/recorder": recPayload([3, 2, 1]), "/api/metrics": metrics(1) }, { runtime: false });
  const d = w.document;
  await w.__run("loadRecorder()"); await w.__tick();
  T.check("the recorder renders and nothing enters", d.querySelectorAll(".rec-row").length === 3 &&
    motionOn(function (el) { return hasCls(el, "rec-row"); }).length === 0);
  const el = d.createElement("div"); d.body.appendChild(el);
  w.__run("EXPAND_RENDER.claude_usage")(el, JSON.parse(JSON.stringify(USAGE)));
  T.check("the usage pop-out shows its final totals", el.querySelector('[data-cu-count="window"]').textContent === "1.2M" && animeCounts().length === 0);
  await w.__run("vitalsRefresh()"); await w.__tick();
  T.check("the Vitals card shows its final numbers", d.querySelectorAll("#vt-grid .vt-val")[0].textContent === "1.4s" && animeCounts().length === 0);
  T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
}

// ---------------------------------------------------------------------------
// the source, statically: no width meters, no colour tweens, no stale gates
// ---------------------------------------------------------------------------
function sourceSuite() {
  console.log("\nthe five files, statically");
  const rec = src("aux_recorder.js"), mem = src("aux_memory.js"), ym = src("aux_youmodel.js"),
        cu = src("aux_claude_usage.js"), mt = src("aux_metrics.js");
  const recCode = rec.replace(/\/\/.*$/gm, "");          // code, not the comments that explain the change
  T.check("aux_recorder: no backgroundColor in its code, no animate() call left", !/backgroundColor/.test(recCode) && !/[^.\w]animate\(/.test(recCode));
  T.check("aux_recorder: rows enter by key ('rec:' + id) through HermesAnim", /H\.once\("rec:" \+ id\)/.test(rec) && /H\.stagger\(fresh, "fade"\)/.test(rec));
  [["aux_memory.js", mem], ["aux_youmodel.js", ym], ["aux_claude_usage.js", cu], ["aux_metrics.js", mt], ["aux_recorder.js", rec]].forEach(function (p) {
    const code = p[1].replace(/\/\/.*$/gm, "");
    T.check(p[0] + ": no meter takes a width (no width:' + pct, no transition:width)",
      !/width:'\s*\+\s*(pct|capPct)/.test(code) && !/width:"\s*\+\s*(pct|capPct)/.test(code) && !/transition:width/.test(code));
  });
  T.check("aux_memory/aux_youmodel: A() asks HA(), not the load-time REDUCE flag",
    /function A\(node, kf, opt\)\{\s*var H = ha\(\);/.test(mem) && /function A\(node, kf, opt\) \{\s*var H = ha\(\);/.test(ym) &&
    !/REDUCE\) return;/.test(mem) && !/REDUCE\) return;/.test(ym));
  T.check("aux_claude_usage + aux_metrics: count-ups go through HermesAnim.count", /H\.count\(x\.el, x\.n,/.test(cu) && /H\.count\(el, x\.num,/.test(mt));
  T.check("aux_metrics: the grid pulse is gated on a user-caused render that is on screen", /if \(moving && !wait\) pulse\(grid\);/.test(mt) &&
    /function pulse\(grid\) \{\s*if \(!RM\(\) && typeof animate === "function"\)/.test(mt));
}

(async function main() {
  console.log("C5: pop-out numbers and paint clean-up (motion-system §8.11)");
  await scenario("recorder", recorderSuite);
  await scenario("usage", usageSuite);
  await scenario("vitals", vitalsSuite);
  await scenario("vitals-switch", vitalsSwitchSuite);
  await scenario("vitals-first-view", vitalsFirstViewSuite);
  await scenario("memory", memorySuite);
  await scenario("youmodel", youmodelSuite);
  await scenario("anim-null", nullSuite);
  sourceSuite();
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
