// t_anim_runtime.js — the motion runtime (dashboard/aux_anim.js), section 6
// and test plan 10.1 of docs/plans/motion-system-2026-10.md.
//
// The REAL aux_anim.js runs in the mini DOM (_a11y_dom.js) against the
// recording library stubs in _anim_stubs.js, which schedule no timers: every
// animation ends only when the test ends it. Each scenario boots a fresh
// window, so no state leaks between them.
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, harness, Event, PointerEvent } = require("./_a11y_dom.js");
const S = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");

const FIX = '<!doctype html><html><head></head><body>' +
  '<header><div class="seg"><b class="on" id="tab-hub">Hub</b><b id="tab-mind">Settings</b></div>' +
  '<button id="model-pill" class="pill"><span id="hnum">1</span></button></header>' +
  '<main class="stage">' +
  '<div class="view" id="view-hub"><div class="wgrid" id="wgrid">' +
  '<div class="w glass" data-flip-id="a" id="wa">A</div><div class="w" data-flip-id="b" id="wb">B</div>' +
  '<div class="w" data-flip-id="c" id="wc">C</div></div></div>' +
  '<div class="view" id="view-mind" hidden><nav id="set-rail">rail</nav><div id="set-panels">' +
  '<section class="set-panel" id="sec-a">a</section><section class="set-panel" id="sec-b" hidden>b</section></div></div>' +
  '<div class="view" id="view-quiet" hidden data-anim="off"><p>q</p></div>' +
  '</main>' +
  '<button id="edit-toggle">Customize</button>' +
  '<span class="num" id="num">0</span><span class="num" id="num2">0</span>' +
  '<svg id="spark"><path id="p1" stroke="#000" d="M0 0L9 9"></path><path id="p2" stroke="#000" d="M0 9L9 0"></path>' +
  '<path id="p3" stroke="#000" d="M0 4L9 4"></path></svg>' +
  '<h2 id="hd">Good morning there</h2><h2 id="hrich">Hi <b>there</b></h2>' +
  '</body></html>';

// a fresh window with the stubs and the runtime. o: {media, gsap, anime,
// motion, motionLegacy, before(w), readyState}
function boot(o) {
  o = o || {};
  const w = createWindow({ html: FIX, media: o.media });
  if (o.readyState) w.document.readyState = o.readyState;
  const L = S.install(w, { motion: o.motion !== false, motionLegacy: o.motionLegacy, gsap: !!o.gsap, anime: !!o.anime, animeOpts: o.animeOpts });
  if (o.before) o.before(w, L);
  w.__run(SRC, "aux_anim.js");
  return { w: w, d: w.document, L: L, A: w.HermesAnim };
}
const OS_REDUCE = function (q) { return /prefers-reduced-motion/.test(q); };
function events(d) {
  const ev = [];
  d.addEventListener("hermes:animstart", function (e) { ev.push({ t: "start", target: e.target, detail: e.detail }); });
  d.addEventListener("hermes:animend", function (e) { ev.push({ t: "end", target: e.target, detail: e.detail }); });
  return ev;
}
function anyDurAbove0(L) {
  return L.calls.some(function (c) {
    const o = c.opt || (c.params) || null;
    if (!o) return false;
    if (o.duration > 0 || o.delay > 0) return true;
    for (const k in o) if (o[k] && typeof o[k] === "object" && (o[k].duration > 0 || o[k].visualDuration > 0)) return true;
    return false;
  });
}
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}

(async function main() {
  console.log("aux_anim.js: the motion runtime (HermesAnim v1)");

  await scenario("shape", async function () {
    const { w, d, A } = boot();
    const NAMES = ["T", "reduced", "live", "quiet", "once", "ready", "load", "enter", "exit", "stagger", "spring",
      "legacy", "flip", "isFlipping", "sequence", "hold", "count", "draw", "words", "settle", "stats"];
    T.check("window.HermesAnim exists with v === 1", !!A && A.v === 1);
    T.check("the API object is frozen", Object.isFrozen(A));
    T.check("every §6.5 member is present", NAMES.every(function (n) { return n in A; }), NAMES.filter(function (n) { return !(n in A); }));
    T.check("...and every method is a function", NAMES.slice(1).every(function (n) { return typeof A[n] === "function"; }));
    T.check("T is frozen all the way down", Object.isFrozen(A.T) && Object.isFrozen(A.T.ease) && Object.isFrozen(A.T.ease.out) && Object.isFrozen(A.T.spring.gel));
    T.check("the tokens are the §6.4 table", JSON.stringify(A.T.ease.out) === "[0.22,1,0.36,1]" && A.T.dur.layout === .42 && A.T.dur.rise === .46 &&
      A.T.spring.gel.visualDuration === .34 && A.T.spring.gel.bounce === .3 && A.T.stagger.max === 12 && A.T.hold.fail === 2.6);
    const vl = (d.__l && d.__l.visibilitychange || []).length, pd = (d.__l && d.__l.pointerdown || []).length;
    w.__run(SRC, "aux_anim.js#again");
    T.check("loading it twice keeps the same object (window.__auxAnim guard)", w.HermesAnim === A);
    T.check("...and adds no second set of listeners", (d.__l.visibilitychange || []).length === vl && (d.__l.pointerdown || []).length === pd);
    T.check("no pointermove listener of its own (aux_glass keeps the only one)", !(d.__l && d.__l.pointermove && d.__l.pointermove.length));
    T.check("source: no setInterval anywhere", !/setInterval/.test(SRC));
    T.check("source: no innerHTML anywhere", !/innerHTML/.test(SRC));
    T.check("source: no bare datetime/index.html global reads (no $, esc, animate)", !/[^.\w]\$\(|[^.\w]esc\(|[^.\w]revealStagger\(/.test(SRC));
    // garbage in: still synchronous, still never throws
    let threw = null, n = 0;
    try {
      A.enter(null, "rise"); A.enter(d.getElementById("wa"), "nope"); A.stagger([], "rise");
      A.exit(null, "popOut", function () { n++; });
      A.flip(null, function () { n++; });
      A.sequence("bad", function () { throw new Error("boom"); }, { done: function () { n++; } });
      A.count(null, 3); A.draw(null); A.words(null); A.spring(null, { x: [0, 1] });
      A.hold(0, function () { n++; });
      A.legacy(null, {}, {});
    } catch (e) { threw = e; }
    T.check("garbage input never throws", threw === null, String(threw));
    T.check("...and the callbacks that must run still ran (exit done, flip mutate, sequence done)", n >= 3, n);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("OS reduced motion", async function () {
    const { w, d, L, A } = boot({ media: OS_REDUCE, gsap: true, anime: true });
    const t0 = w.__timers.length, i0 = w.__intervals.length;
    T.check("reduced() reads the OS query", A.reduced() === true && A.live() === false);
    T.check("Motion's skipAnimations mirrors it", w.Motion.MotionGlobalConfig.skipAnimations === true);
    const wa = d.getElementById("wa"), wb = d.getElementById("wb");
    const h1 = A.enter(wa, "pop");
    T.check("enter: done at once, inline untouched", h1.done === true && wa.style.opacity === "" && wa.style.transform === "");
    let done = 0;
    const h2 = A.exit(wb, "popOut", function () { done++; });
    T.check("exit: done() ran before exit() returned, exactly once", done === 1 && h2.done === true, done);
    h2.finish(); h2.cancel();
    T.check("...and a later finish()/cancel() does not run it again", done === 1);
    A.stagger(d.querySelectorAll(".w"), "rise");
    A.spring(wa, { scale: [.6, 1] }, "gel");
    T.check("spring: the end value is applied synchronously", wa.style.transform === "scale(1)", wa.style.transform);
    let mut = 0;
    A.flip(d.getElementById("wgrid"), function () { mut++; });
    T.check("flip: mutate ran synchronously and nothing else", mut === 1 && L.of("flip").length === 0);
    const seq = [];
    A.sequence("swap", function () { seq.push("build"); }, { instant: function (tok) { seq.push(tok === A.T ? "instant(T)" : "instant"); }, done: function () { seq.push("done"); } });
    T.check("sequence: build skipped, instant(T) then done", seq.join(",") === "instant(T),done", seq);
    const num = d.getElementById("num");
    A.count(num, 1234);
    T.check("count: the final text at once", num.textContent === "1,234" && !num.hasAttribute("aria-label"), num.textContent);
    A.draw(d.getElementById("p1")); A.words(d.getElementById("hd"));
    T.check("draw/words: nothing touched", L.of("anime").length === 0 && d.getElementById("hd").textContent === "Good morning there");
    const lg = A.legacy(wa, { opacity: [0, 1] }, { duration: .45, delay: .1, repeat: Infinity, easing: "ease-out" });
    const lo = L.of("motion", "animate").pop().opt;
    T.check("legacy: Motion is asked for no time (the t_c03 shape)", lo.duration === 0 && lo.delay === 0 && lo.repeat === 0, lo);
    T.check("...with its easing translated", lo.ease === "easeOut" && !("easing" in lo), lo);
    T.check("no library call carries a duration above 0", !anyDurAbove0(L));
    T.check("no Motion animation was started by the presets", L.of("motion", "animate").length === 1 && !!lg);
    T.check("no timer and no interval was added", w.__timers.length === t0 && w.__intervals.length === i0, [w.__timers.length, t0]);
    await A.load("gsap"); await A.load("anime");
    T.check("load() fetches nothing under reduced motion", d.querySelectorAll("script").length === 0);
    w.dispatchEvent(new Event("load"));
    T.check("no idle prefetch is scheduled", w.__timers.length === t0);
    d.getElementById("model-pill").dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    T.check("no <script src=/vendor/...> was ever appended", d.querySelectorAll("script").length === 0);
    const st = A.stats();
    T.check("stats() counts the reduced instants", st.instant.reduced >= 8 && st.started === 0, st);
  });

  await scenario("OS reduced motion: the libraries are never fetched", async function () {
    const { w, d, A } = boot({ media: OS_REDUCE });
    let mut = 0;
    A.flip(d.getElementById("wgrid"), function () { mut++; });
    A.count(d.getElementById("num"), 5); A.sequence("view", function () {});
    w.dispatchEvent(new Event("load")); w.__flushTimers(3);
    T.check("flip/count/sequence and the prefetch fetch nothing", d.querySelectorAll("script").length === 0 && mut === 1);
    T.check("both libraries stay idle", A.stats().libs.gsap === "idle" && A.stats().libs.anime === "idle", A.stats().libs);
    T.check("hold() still waits: reading time is not motion (one timer)", (function () {
      const t0 = w.__timers.length; let fired = 0; A.hold(1.3, function () { fired++; });
      const ok = w.__timers.length === t0 + 1 && w.__timers[w.__timers.length - 1].ms === 1300 && fired === 0;
      w.__flushTimers(1); return ok && fired === 1;
    })());
  });

  await scenario("in-app reduce switch", async function () {
    const { w, d, L, A } = boot({ gsap: true });
    const wa = d.getElementById("wa");
    const h = A.enter(wa, "rise");
    let held = 0; const hh = A.hold(2, function () { held++; });
    T.check("(control) live: Motion runs and the handle is pending", L.of("motion", "animate").length === 1 && !h.done);
    d.documentElement.setAttribute("data-reduce", "1");
    await w.__tick();
    T.check("setting :root[data-reduce] flips skipAnimations on", w.Motion.MotionGlobalConfig.skipAnimations === true);
    T.check("...and settles what is running (its handle is done)", h.done === true && L.of("motion", "cancel").length === 1);
    T.check("...back under CSS control (inline cleared)", wa.style.opacity === "" && wa.style.transform === "", wa.style.cssText);
    T.check("...but a hold keeps its reading time", held === 0 && hh.done === false);
    T.check("reduced() follows the attribute live", A.reduced() === true && A.enter(wa, "rise").done === true);
    d.documentElement.removeAttribute("data-reduce");
    await w.__tick();
    T.check("removing it reverses skipAnimations", w.Motion.MotionGlobalConfig.skipAnimations === false && A.live() === true);
    // the OS switch turning on mid-animation does the same
    const h2 = A.enter(wa, "fade");
    const mq = (w.__mqls || []).filter(function (m) { return /reduced-motion/.test(m.media); });
    mq.forEach(function (m) { m.matches = true; m.__ls.forEach(function (fn) { fn({ matches: true }); }); });
    T.check("the OS query changing settles too", h2.done === true && w.Motion.MotionGlobalConfig.skipAnimations === true);
    hh.cancel();
  });

  await scenario("the in-app switch from the first script", async function () {
    const { d, A } = boot({ before: function (w) { w.localStorage.setItem("hermes_reduce", "1"); } });
    T.check("hermes_reduce=1 sets :root[data-reduce] before aux_prefs exists", d.documentElement.getAttribute("data-reduce") === "1");
    T.check("...so the first animation is already instant", A.reduced() && A.enter(d.getElementById("wa"), "rise").done === true);
    const b = boot({ before: function (w) { w.localStorage.setItem("hermes_reduce", "0"); } });
    T.check("hermes_reduce=0 leaves it off", !b.d.documentElement.hasAttribute("data-reduce") && b.A.live());
    const c = boot({ before: function (w) { w.localStorage = { getItem: function () { throw new Error("denied"); } }; } });
    T.check("a throwing localStorage is survived", !!c.A && c.A.v === 1);
  });

  await scenario("hidden page", async function () {
    const { w, d, L, A } = boot({ gsap: true, anime: true });
    const ev = events(d);
    const wa = d.getElementById("wa");
    const h1 = A.enter(wa, "rise");
    let exited = 0; const h2 = A.exit(d.getElementById("wb"), "popOut", function () { exited++; });
    let sdone = 0; const h3 = A.sequence("swap", function (tl) { tl.from(d.getElementById("wc"), { opacity: 0, duration: .3 }); }, { done: function () { sdone++; } });
    const h4 = A.count(d.getElementById("num"), 50);
    let held = 0; A.hold(2.2, function () { held++; });
    w.dispatchEvent(new Event("load"));
    T.check("(control) four animations and a hold are running", A.stats().running === 4 && A.stats().holds === 1, A.stats());
    d.hidden = true; d.visibilityState = "hidden";
    d.dispatchEvent(new Event("visibilitychange"));
    T.check("going hidden finishes every handle", h1.done && h2.done && h3.done && h4.done && A.stats().running === 0);
    T.check("...exit and sequence dones ran once each, the hold fired", exited === 1 && sdone === 1 && held === 1, [exited, sdone, held]);
    T.check("...the count landed on its final text", d.getElementById("num").textContent === "50");
    T.check("...via the libraries' own end (progress(1), complete(), Motion cancel)",
      L.of("gsap", "progress").some(function (c) { return c.p === 1; }) && L.of("anime", "complete").length === 1);
    T.check("...the hold's delayedCall was fired, not left for GSAP's ticker", L.of("gsap", "dc.kill").length === 1);
    T.check("...and nothing of this runtime's is left on a timer", w.__timers.length === 0, w.__timers.map(function (t) { return t.ms; }));
    T.check("one animstart and one animend for each of the four", ev.filter(function (e) { return e.t === "start"; }).length === 4 &&
      ev.filter(function (e) { return e.t === "end"; }).length === 4);
    const c0 = L.calls.length;
    const n1 = A.enter(wa, "pop"); let hd = 0; const n2 = A.hold(1, function () { hd++; });
    A.flip(d.getElementById("wgrid"), function () {});
    T.check("new calls while hidden take the instant path", n1.done && n2.done && hd === 1 && L.calls.length === c0);
    T.check("...and add no timer and no rAF", w.__timers.length === 0);
    w.dispatchEvent(new Event("load"));
    T.check("no prefetch is scheduled while hidden", w.__timers.length === 0);
    T.check("stats() counts them as hidden", A.stats().instant.hidden >= 3, A.stats().instant);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
    // without the libraries: the hold is a setTimeout and a prefetch is pending
    const b = boot();
    let bh = 0;
    b.A.hold(2.2, function () { bh++; });
    b.w.dispatchEvent(new Event("load"));
    T.check("(control) a hold timer and the prefetch timer are pending", b.w.__timers.length === 2, b.w.__timers.map(function (t) { return t.ms; }));
    b.d.hidden = true; b.d.dispatchEvent(new Event("visibilitychange"));
    T.check("going hidden fires the hold and clears both timers", bh === 1 && b.w.__timers.length === 0, b.w.__timers.map(function (t) { return t.ms; }));
    b.A.enter(b.d.getElementById("wa"), "rise"); b.A.hold(1, function () { bh++; }); b.A.count(b.d.getElementById("num"), 4);
    T.check("while hidden: zero timers, zero rAF, nothing fetched", b.w.__timers.length === 0 && bh === 2 && b.d.querySelectorAll("script").length === 0);
    b.d.hidden = false;
    b.d.dispatchEvent(new Event("visibilitychange"));
    T.check("becoming visible schedules exactly one prefetch (2.5 s)", b.w.__timers.length === 1 && b.w.__timers[0].ms === 2500, b.w.__timers.map(function (t) { return t.ms; }));
    b.d.dispatchEvent(new Event("visibilitychange")); b.w.dispatchEvent(new Event("load"));
    T.check("...and never a second one", b.w.__timers.length === 1);
    b.d.hidden = true; b.d.dispatchEvent(new Event("visibilitychange"));
    T.check("hidden again before it fired: the prefetch timer is cleared", b.w.__timers.length === 0);
    b.d.hidden = false; b.d.dispatchEvent(new Event("visibilitychange"));
    T.check("...and re-armed on the next visible", b.w.__timers.length === 1);
  });

  await scenario("quiet() and once()", async function () {
    const { d, L, A } = boot();
    const wa = d.getElementById("wa");
    let inner = null, nested = null;
    const v = A.quiet(function () { inner = A.live(); A.quiet(function () { nested = A.live(); }); return 42; });
    T.check("quiet returns fn's value", v === 42);
    T.check("inside quiet (and nested) nothing is live", inner === false && nested === false);
    T.check("live again after", A.live() === true);
    let caught = null;
    try { A.quiet(function () { throw new Error("x"); }); } catch (e) { caught = e; }
    T.check("an exception inside quiet still propagates...", caught && caught.message === "x");
    T.check("...and still restores the state", A.live() === true);
    const c0 = L.of("motion", "animate").length;
    A.quiet(function () { A.enter(wa, "rise"); A.stagger(d.querySelectorAll(".w"), "rise"); });
    T.check("calls inside quiet are instant", L.of("motion", "animate").length === c0 && A.stats().instant.quiet === 2);
    T.check("once(): true the first time only", A.once("k1") === true && A.once("k1") === false && A.once("k2") === true);
    A.enter(wa, "fade", { key: "rec:1" });
    T.check("enter {key}: the first time animates", L.of("motion", "animate").length === c0 + 1);
    A.enter(wa, "fade", { key: "rec:1" });
    T.check("...a key already seen is instant", L.of("motion", "animate").length === c0 + 1 && A.stats().instant.key === 1);
    A.quiet(function () { A.enter(wa, "fade", { key: "rec:2" }); });
    A.enter(wa, "fade", { key: "rec:2" });
    T.check("a key first seen inside quiet is spent there (no replay later)", L.of("motion", "animate").length === c0 + 1);
  });

  await scenario("library loader", async function () {
    const { w, d, L, A } = boot();
    let mut = 0;
    const h = A.flip(d.getElementById("wgrid"), function () { mut++; });
    const sc = d.querySelectorAll("script");
    const src = Array.prototype.map.call(sc, function (s) { return s.getAttribute("src"); });
    T.check("the first flip() without GSAP runs mutate synchronously", mut === 1 && h.done === true);
    T.check("...and appends gsap, Flip, CustomEase in that order", JSON.stringify(src) === JSON.stringify(["/vendor/gsap/gsap-3.15.0.min.js",
      "/vendor/gsap/Flip-3.15.0.min.js", "/vendor/gsap/CustomEase-3.15.0.min.js"]), src);
    T.check("...each with async=false (execution order kept), into <head>", Array.prototype.every.call(sc, function (s) { return s.async === false && s.parentNode === d.head; }));
    T.check("state is loading", A.stats().libs.gsap === "loading" && A.ready("gsap") === false);
    A.flip(d.getElementById("wgrid"), function () { mut++; });
    T.check("a second flip while loading: mutate again, no second fetch", mut === 2 && d.querySelectorAll("script").length === 3);
    const p = A.load("gsap");
    S.installGsap(w, L);
    Array.prototype.forEach.call(sc, function (s) { s.dispatchEvent(new Event("load")); });
    const ok = await p;
    T.check("a simulated load resolves load() true and the state is ready", ok === true && A.stats().libs.gsap === "ready");
    const reg = L.of("gsap", "registerPlugin")[0];
    T.check("initGsap: Flip and CustomEase registered", !!reg && reg.args[0] === w.Flip && reg.args[1] === w.CustomEase);
    T.check("initGsap: the five hermes.* eases, from the tokens", L.eases["hermes.out"] === "0.22,1,0.36,1" && L.eases["hermes.gel"] === "0.34,1.56,0.64,1" &&
      L.eases["hermes.pop"] === "0.22,1.28,0.42,1" && L.eases["hermes.press"] === "0.2,0,0.4,1" && L.eases["hermes.expo"] === "0.16,1,0.3,1", L.eases);
    const def = L.of("gsap", "defaults")[0].v, cfg = L.of("gsap", "config")[0].v, lag = L.of("gsap", "lagSmoothing")[0];
    T.check("initGsap: defaults, config and lag smoothing", def.ease === "hermes.out" && def.duration === .22 && def.overwrite === "auto" &&
      cfg.nullTargetWarn === false && cfg.autoSleep === 60 && lag.a === 500 && lag.b === 33, [def, cfg]);
    L.clear();
    const order = [];
    const h2 = A.flip(d.getElementById("wgrid"), function () { order.push("mutate:" + L.of("flip").map(function (c) { return c.fn; }).join("+")); });
    const fr = L.of("flip", "from")[0];
    T.check("the next flip goes through Flip.getState, mutate, Flip.from", order[0] === "mutate:getState" && !!fr && !h2.done, order);
    T.check("...over the scope's [data-flip-id] targets", L.of("flip", "getState")[0].targets.length === 3);
    T.check("...with simple:true, scale:true (no width/height), layout duration, hermes.out", fr.vars.simple === true && fr.vars.scale === true &&
      fr.vars.duration === .42 && fr.vars.ease === "hermes.out" && fr.vars.targets.length === 3, fr.vars);
    T.check("...and enter defaults off", fr.vars.onEnter === undefined);
    fr.tl.progress(1);
    T.check("Flip's own end finishes the handle", h2.done === true);
    // the failure path: one attempt, then permanent instant
    const b = boot();
    const pa = b.A.load("anime");
    const s1 = b.d.querySelectorAll("script");
    T.check("load('anime') appends the one anime file", s1.length === 1 && s1[0].getAttribute("src") === "/vendor/anime/anime-4.5.0.umd.min.js");
    const warns = []; const cw = console.warn; console.warn = function (m) { warns.push(String(m)); };
    s1[0].dispatchEvent(new Event("error"));
    const r = await pa;
    console.warn = cw;
    T.check("a script error resolves false and the state is failed", r === false && b.A.stats().libs.anime === "failed");
    T.check("...with one console.warn", warns.filter(function (m) { return /anime/.test(m); }).length === 1, warns);
    const num = b.d.getElementById("num");
    b.A.count(num, 7); b.A.draw(b.d.getElementById("p1"));
    T.check("later calls take the instant path, with no retry", num.textContent === "7" && b.d.querySelectorAll("script").length === 1);
    T.check("load() after failure resolves false at once", (await b.A.load("anime")) === false && b.d.querySelectorAll("script").length === 1);
    // loaded, but the globals never appeared
    const c = boot();
    const pc = c.A.load("anime");
    c.d.querySelector("script").dispatchEvent(new Event("load"));
    T.check("a load without the anime global is a failure too", (await pc) === false && c.A.stats().libs.anime === "failed");
    // the anime init
    const e = boot({ anime: true, animeOpts: { pauseOnHidden: false } });
    T.check("initAnime keeps anime paused while the document is hidden", e.A.ready("anime") && e.w.anime.engine.pauseOnDocumentHidden === true);
  });

  await scenario("idle prefetch and early kicks", async function () {
    const { w, d, L, A } = boot();
    w.dispatchEvent(new Event("load"));
    T.check("load schedules one prefetch at +2.5 s", w.__timers.length === 1 && w.__timers[0].ms === 2500);
    w.__flushTimers(1);
    T.check("it fetches GSAP first", d.querySelectorAll("script").length === 3);
    S.installGsap(w, L);
    Array.prototype.forEach.call(d.querySelectorAll("script"), function (s) { s.dispatchEvent(new Event("load")); });
    await w.__tick();
    S.installAnime(w, L);
    const s = d.querySelectorAll("script");
    T.check("...then anime", s.length === 4 && s[3].getAttribute("src") === "/vendor/anime/anime-4.5.0.umd.min.js");
    s[3].dispatchEvent(new Event("load")); await w.__tick();
    T.check("both ready", A.ready("gsap") && A.ready("anime"));
    const b = boot();
    b.d.getElementById("tab-mind").dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    T.check("a pointerdown on header .seg kicks GSAP before the click", b.d.querySelectorAll("script").length === 3);
    b.d.getElementById("hnum").dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    T.check("one on #model-pill kicks anime as well", b.d.querySelectorAll("script").length === 4);
    T.check("...after which the kick listener is gone", !(b.d.__l.pointerdown || []).length, (b.d.__l.pointerdown || []).length);
    const c = boot();
    c.d.getElementById("wa").dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    T.check("a pointerdown elsewhere loads nothing", c.d.querySelectorAll("script").length === 0);
    c.d.getElementById("edit-toggle").dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    T.check("#edit-toggle kicks GSAP", c.d.querySelectorAll("script").length === 3);
  });

  await scenario("URL agreement (aux_anim LIBS / server VENDOR_JS / MANIFEST / design)", async function () {
    const urls = (SRC.match(/"\/vendor\/[^"]+\.js"/g) || []).map(function (s) { return s.slice(1, -1); });
    T.check("aux_anim names the four on-demand files", urls.length === 4, urls);
    const doc = fs.readFileSync(path.join(REPO, "docs", "plans", "motion-system-2026-10.md"), "utf8");
    T.check("every LIBS URL is in the design's §4.1 vendoring table", urls.every(function (u) { return doc.indexOf("`" + u + "`") >= 0; }), urls);
    const mv = /var MOTION_V = "([^"]+)"/.exec(SRC);
    T.check("MOTION_V matches the design's Motion file", !!mv && doc.indexOf("`/vendor/motion/motion-" + mv[1] + ".min.js`") >= 0);
    const server = fs.readFileSync(path.join(REPO, "dashboard", "server.py"), "utf8");
    const m = /VENDOR_JS\s*=\s*\{([\s\S]*?)\n\}/.exec(server);
    if (!m) console.log("  SKIP server.py has no VENDOR_JS yet (the vendor cluster adds it; this check enforces once it lands)");
    else {
      const keys = (m[1].match(/"(\/vendor\/[^"]+)"\s*:/g) || []).map(function (s) { return s.replace(/"\s*:$/, "").slice(1); });
      T.check("every LIBS URL is a key of server.py VENDOR_JS", urls.every(function (u) { return keys.indexOf(u) >= 0; }), { urls: urls, keys: keys });
      T.check("...and so is the Motion file aux_anim reports", keys.indexOf("/vendor/motion/motion-" + mv[1] + ".min.js") >= 0);
    }
    const mf = path.join(REPO, "dashboard", "vendor", "MANIFEST.json");
    if (!fs.existsSync(mf)) console.log("  SKIP dashboard/vendor/MANIFEST.json not present yet (the vendor cluster adds it)");
    else {
      const raw = JSON.parse(fs.readFileSync(mf, "utf8"));
      const rows = Array.isArray(raw) ? raw : (raw.files || []);
      const paths = rows.map(function (r) { return "/" + String(r.path || "").replace(/^\/+/, "").replace(/^dashboard\//, ""); });
      T.check("every LIBS URL is a MANIFEST.json entry", urls.every(function (u) { return paths.indexOf(u) >= 0; }), { urls: urls, paths: paths });
    }
    const html = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
    const tag = /<script src="\/vendor\/motion\/motion-([\d.]+)\.min\.js"><\/script>/.exec(html);
    if (!tag) console.log("  SKIP index.html does not load the vendored Motion yet (merge note)");
    else T.check("index.html's Motion tag is the version aux_anim reports", tag[1] === mv[1], tag[1]);
    T.check("index.html never loads GSAP or anime itself (aux_anim injects them)", !/<script[^>]+\/vendor\/(gsap|anime)\//.test(html));
    if (/<script src="\/aux_anim\.js">/.test(html)) {
      const iM = html.indexOf('src="/vendor/motion/'), iA = html.indexOf('src="/aux_anim.js"'), iI = html.indexOf("<script>\nconst $=");
      T.check("index.html order: Motion, then aux_anim, then the inline shell (§6.1)", iM >= 0 && iM < iA && (iI < 0 || iA < iI), [iM, iA, iI]);
    } else console.log("  SKIP index.html has no /aux_anim.js tag yet (merge note)");
  });

  await scenario("legacy() — the old animate() contract", async function () {
    const { w, d, L, A } = boot();
    const wa = d.getElementById("wa");
    const easeOf = function (opt) { A.legacy(wa, { opacity: [0, 1] }, opt); const o = L.of("motion", "animate").pop().opt; return o; };
    const cases = [
      ["cubic-bezier(.22,1,.36,1)", [.22, 1, .36, 1]], ["cubic-bezier( 0.2 , 0.7, 0.3, 1 )", [.2, .7, .3, 1]],
      ["ease-out", "easeOut"], ["ease-in", "easeIn"], ["ease-in-out", "easeInOut"], ["linear", "linear"],
      ["ease", [.25, .1, .25, 1]]
    ];
    cases.forEach(function (c) {
      const o = easeOf({ duration: .3, easing: c[0] });
      T.check("easing " + c[0] + " -> ease " + JSON.stringify(c[1]), JSON.stringify(o.ease) === JSON.stringify(c[1]) && !("easing" in o), o);
    });
    let o = easeOf({ duration: .3, easing: [.2, .7, .3, 1] });
    T.check("a four-number easing array passes through", JSON.stringify(o.ease) === "[0.2,0.7,0.3,1]");
    o = easeOf({ duration: .3, easing: "constructor" });
    T.check("an Object.prototype name is not an easing (nothing is invented)", !("ease" in o) && !("easing" in o), o);
    o = easeOf({ duration: .3, easing: "steps(4, end)" });
    T.check("an unknown easing is dropped (Motion's default curve)", !("ease" in o) && !("easing" in o), o);
    o = easeOf({ duration: .3, easing: "linear", ease: "backOut" });
    T.check("an explicit ease is left untouched", o.ease === "backOut" && !("easing" in o));
    o = easeOf({ duration: .3, iterations: Infinity, fill: "both", direction: "alternate" });
    T.check("iterations Infinity -> repeat Infinity; fill and direction dropped", o.repeat === Infinity && !("iterations" in o) && !("fill" in o) && !("direction" in o), o);
    o = easeOf({ duration: .3, iterations: 3 });
    T.check("iterations 3 -> repeat 2 (Motion repeats AFTER the first play)", o.repeat === 2);
    o = easeOf({ duration: .3, opacity: { duration: .2, easing: "linear" } });
    T.check("a per-value transition is translated too", o.opacity.ease === "linear" && !("easing" in o.opacity), o.opacity);
    A.quiet(function () { o = easeOf({ duration: .45, delay: .1, repeat: Infinity, opacity: { duration: .2, delay: .3 } }); });
    T.check("not live: duration 0, delay 0, repeat 0 (the t_c03 contract)", o.duration === 0 && o.delay === 0 && o.repeat === 0, o);
    T.check("...per-value transitions too", o.opacity.duration === 0 && o.opacity.delay === 0);
    // looked up per call: a wrapper installed later (aux_prefs' gate) is used
    let wrapped = 0; const orig = w.Motion.animate;
    w.Motion.animate = function (a, b, c) { wrapped++; return orig.call(this, a, b, c); };
    A.legacy(wa, { opacity: [0, 1] }, { duration: .2 });
    T.check("Motion.animate is looked up per call (a later wrapper is in the path)", wrapped === 1);
    w.Motion.animate = orig;
    // events
    const ev = events(d);
    const res = A.legacy(wa, { opacity: [0, 1] }, { duration: .2 });
    T.check("a live legacy call dispatches one hermes:animstart", ev.length === 1 && ev[0].t === "start" && ev[0].target === wa);
    res.complete(); await w.__tick();
    T.check("...and one hermes:animend when it finishes", ev.length === 2 && ev[1].t === "end");
    T.check("it returns Motion's controls", typeof res.cancel === "function" && !!res.finished);
    // no Motion: the WAAPI fallback
    const b = boot({ motion: false });
    const el = b.d.getElementById("wa");
    const an = b.A.legacy(el, [{ opacity: 0 }, { opacity: 1 }], { duration: .46, delay: .1, easing: "cubic-bezier(.22,1,.36,1)" });
    const last = (el.__anims || []).pop();
    T.check("without Motion it falls back to el.animate", !!an && !!last && last === an);
    T.check("...in milliseconds, with the CSS easing kept", last.timing.duration === 460 && Math.round(last.timing.delay) === 100 && last.timing.easing === "cubic-bezier(.22,1,.36,1)", last.timing);
    T.check("legacy on nothing returns undefined", b.A.legacy(null, {}, {}) === undefined);
  });

  await scenario("handles: enter, exit, cancel, finish", async function () {
    const { w, d, L, A } = boot();
    const ev = events(d);
    const wa = d.getElementById("wa"), wb = d.getElementById("wb");
    wa.style.transform = "translateX(3px)";
    const h = A.enter(wa, "rise");
    T.check("enter pre-paints its from-state synchronously (no frame of the end state)", wa.style.opacity === "0" && wa.style.transform === "translate(0px, 10px)", wa.style.cssText);
    const call = L.of("motion", "animate").pop();
    T.check("rise: opacity [0,1], y [10,0], .46 s on ease.out", JSON.stringify(call.kf) === '{"opacity":[0,1],"y":[10,0]}' && call.opt.duration === .46 &&
      JSON.stringify(call.opt.ease) === "[0.22,1,0.36,1]", [call.kf, call.opt]);
    T.check("Motion gets fresh, unfrozen option objects (it mutates them)", !Object.isFrozen(call.opt) && !Object.isFrozen(call.opt.ease) && call.opt.ease !== A.T.ease.out);
    T.check("pending until the library ends it", h.done === false);
    call.h.complete(); await w.__tick();
    T.check("on finish the inline styles are put back as they were (CSS keeps control)", h.done === true && wa.style.opacity === "" && wa.style.transform === "translateX(3px)", wa.style.cssText);
    T.check("...and Motion lets go of the element (releaseElement)", L.of("motion", "releaseElement").some(function (c) { return c.el === wa; }));
    wa.style.transform = "";
    let done = 0;
    const x = A.exit(wb, "popOut", function () { done++; wb.hidden = true; });
    T.check("exit: done waits for the animation", done === 0 && !x.done);
    L.finishAll(); await w.__tick();
    T.check("...then runs exactly once", done === 1 && x.done && wb.hidden);
    T.check("...and after done() the inline styles it wrote are gone", wb.style.opacity === "" && wb.style.transform === "", wb.style.cssText);
    L.finishAll(); x.finish(); await w.__tick();
    T.check("...never twice", done === 1);
    wb.hidden = false;
    let d2 = 0;
    const y = A.exit(wb, "popOut", function () { d2++; });
    const yc = L.of("motion", "animate").pop();
    y.cancel(); await w.__tick();
    T.check("cancel(): done never runs", d2 === 0 && y.done === true);
    T.check("...Motion was cancelled and the inline styles removed", yc.h.state === "cancelled" && wb.style.opacity === "" && wb.style.transform === "");
    L.finishAll(); await w.__tick();
    T.check("...even after everything else finishes", d2 === 0);
    let d3 = 0;
    const z = A.exit(wb, "fadeOut", function () { d3++; });
    const zc = L.of("motion", "animate").pop();
    z.finish();
    T.check("finish(): jumps to the end and runs done synchronously", d3 === 1 && z.done === true);
    T.check("...by cancelling Motion (no late commit of the end keyframe)", zc.h.state === "cancelled" && wb.style.opacity === "");
    // a reopen during an exit (openPop's race) and a double close
    let d4 = 0;
    A.exit(wb, "popOut", function () { d4++; });
    A.enter(wb, "pop");
    T.check("an enter over a running exit cancels it (no done: it is being reopened)", d4 === 0);
    let d5 = 0, d6 = 0;
    A.exit(wb, "popOut", function () { d5++; });
    A.exit(wb, "popOut", function () { d6++; });
    T.check("an exit over a running exit finishes the first (its done runs once)", d5 === 1 && d6 === 0);
    L.finishAll(); await w.__tick();
    T.check("...and the second's done runs when it ends", d5 === 1 && d6 === 1);
    // events: one start/end pair per animation; none on the instant path
    const starts = ev.filter(function (e) { return e.t === "start"; }).length, ends = ev.filter(function (e) { return e.t === "end"; }).length;
    T.check("one hermes:animstart and one hermes:animend per animation", starts === ends && starts === 8, [starts, ends]);
    T.check("...detail carries the duration in ms and the kind", ev[0].detail.kind === "enter" && ev[0].detail.ms === 460, ev[0].detail);
    A.quiet(function () { A.enter(wa, "rise"); });
    T.check("...and none for an instant call", ev.length === starts + ends);
    // a fade-in started from an exit's done() on the same element (the Agent
    // page's crossfade): the exit's clean-up ran first, so it cannot undo it
    let d7 = 0;
    wb.style.opacity = ""; wb.style.transform = "";
    A.exit(wb, "fadeOut", function () { d7++; A.enter(wb, "fade"); });
    const xc = L.of("motion", "animate").pop();
    xc.h.complete(); await w.__tick();
    const rel = L.of("motion", "releaseElement").filter(function (c) { return c.el === wb; }).length;
    T.check("an enter started inside exit's done() runs: its from-state is painted and nothing released it afterwards",
      d7 === 1 && wb.style.opacity === "0" && L.of("motion", "animate").pop() !== xc, { css: wb.style.cssText, d7: d7 });
    L.finishAll(); await w.__tick();
    T.check("...and when it lands the element is back at the page's own state (fully visible), not the exit's end",
      wb.style.opacity === "" && wb.style.transform === "" &&
      L.of("motion", "releaseElement").filter(function (c) { return c.el === wb; }).length === rel + 1, wb.style.cssText);
  });

  await scenario("presets", async function () {
    const { d, L, A } = boot();
    const wa = d.getElementById("wa");
    function last(p, kind) { if (kind === "exit") A.exit(wa, p, null); else A.enter(wa, p); return L.of("motion", "animate").pop(); }
    let c = last("pop");
    T.check("pop: opacity .22 tween, scale on spring.gel", JSON.stringify(c.kf) === '{"opacity":[0,1],"scale":[0.92,1]}' && c.opt.opacity.duration === .22 &&
      c.opt.scale.type === "spring" && c.opt.scale.visualDuration === .34 && c.opt.scale.bounce === .3, c.opt);
    c = last("slideL");
    T.check("slideL: x [-18,0] on spring.soft", JSON.stringify(c.kf.x) === "[-18,0]" && c.opt.x.visualDuration === .4 && c.opt.x.bounce === .12);
    c = last("slideR");
    T.check("slideR: x [24,0]", JSON.stringify(c.kf.x) === "[24,0]");
    c = last("fab");
    T.check("fab: scale [.5,1] on spring.gel", JSON.stringify(c.kf.scale) === "[0.5,1]" && c.opt.scale.visualDuration === .34);
    c = last("toast");
    T.check("toast: y [8,0] on spring.snappy", JSON.stringify(c.kf.y) === "[8,0]" && c.opt.y.visualDuration === .22 && c.opt.y.bounce === .15);
    c = last("bubble");
    T.check("bubble: y [6,0], .22 s", JSON.stringify(c.kf.y) === "[6,0]" && c.opt.duration === .22);
    c = last("fade");
    T.check("fade: opacity only, .22 s", JSON.stringify(c.kf) === '{"opacity":[0,1]}' && c.opt.duration === .22);
    const ex = { popOut: ['{"opacity":[1,0],"scale":[1,0.95]}', .18], fadeOut: ['{"opacity":[1,0]}', .15],
      dismiss: ['{"opacity":[1,0],"x":[0,16]}', .2], defer: ['{"opacity":[1,0],"y":[0,-6],"scale":[1,0.98]}', .2],
      shrinkOut: ['{"opacity":[1,0],"scale":[1,0.86]}', .22] };
    Object.keys(ex).forEach(function (k) {
      c = last(k, "exit");
      T.check("exit " + k + ": keyframes and " + ex[k][1] + " s on ease.press", JSON.stringify(c.kf) === ex[k][0] && c.opt.duration === ex[k][1] &&
        JSON.stringify(c.opt.ease) === "[0.2,0,0.4,1]", [c.kf, c.opt]);
    });
    c = (A.exit(wa, "popOut", null, { dur: A.T.dur.micro }), L.of("motion", "animate").pop());
    T.check("exit {dur} overrides the preset's duration (the model menu's dur.micro)", c.opt.duration === .15);
    c = (A.enter(wa, "rise", { delay: .3 }), L.of("motion", "animate").pop());
    T.check("enter {delay}", c.opt.delay === .3);
    c = (A.enter(wa, "pop", { delay: .7 }), L.of("motion", "animate").pop());
    T.check("...reaches every per-value transition", c.opt.scale.delay === .7 && c.opt.opacity.delay === .7);
    const n0 = L.of("motion", "animate").length;
    A.enter(wa, "popOut"); A.exit(wa, "rise", null);
    T.check("an exit preset cannot enter and vice versa (instant)", L.of("motion", "animate").length === n0);
    A.enter(d.querySelectorAll(".w"), "fade");
    T.check("enter on a list: one Motion call per element, no delays", L.of("motion", "animate").length === n0 + 3 &&
      L.of("motion", "animate").slice(-3).every(function (x) { return x.opt.delay === 0; }));
  });

  await scenario("stagger", async function () {
    const { w, d, L, A } = boot();
    const host = d.getElementById("wgrid");
    for (let i = 0; i < 17; i++) { const e = d.createElement("div"); e.className = "w"; host.appendChild(e); }
    const els = host.querySelectorAll(".w");
    T.check("(20 cards)", els.length === 20);
    const ev = events(d);
    const h = A.stagger(els, "rise");
    const calls = L.of("motion", "animate");
    T.check("only the first 12 animate", calls.length === 12);
    const step = Math.min(.035, .4 / 20);
    T.check("delays are i * min(step, total/n)", calls.every(function (c, i) { return Math.abs(c.opt.delay - i * step) < 1e-9; }), calls.map(function (c) { return c.opt.delay; }));
    T.check("...so the last one starts by T.stagger.total", calls[11].opt.delay <= .4);
    T.check("the tail is counted, untouched (already at its CSS state)", A.stats().instant.cap === 1 && els[15].style.opacity === "");
    T.check("one handle, one event pair, on the common ancestor", ev.length === 1 && ev[0].target === host);
    L.finishAll(); await w.__tick();
    T.check("it ends when the last ends", h.done && ev.length === 2);
    L.clear();
    A.stagger(d.querySelectorAll("#wa,#wb,#wc"), "rise", { step: .055 });
    T.check("opts.step (revealStagger's 55 ms) is still capped by total/n", L.of("motion", "animate").map(function (c) { return c.opt.delay; }).join() === "0,0.055,0.11");
    L.clear();
    A.stagger(els, "rise", { key: "hub:boot" }); A.stagger(els, "rise", { key: "hub:boot" });
    T.check("{key}: the hub boot entrance runs once per page life", L.of("motion", "animate").length === 12);
  });

  await scenario("spring", async function () {
    const { w, d, L, A } = boot();
    const ring = d.getElementById("wa");
    const h = A.spring(ring, { rotate: [0, 12] }, "snappy");
    const c = L.of("motion", "animate").pop();
    T.check("a named token spring", c.opt.type === "spring" && c.opt.visualDuration === .22 && c.opt.bounce === .15, c.opt);
    c.h.complete(); await w.__tick();
    T.check("its end value PERSISTS (the sigil keeps its angle)", h.done && ring.style.transform === "rotate(12deg)", ring.style.transform);
    A.spring(ring, { scale: [1, 1.5, 1] });
    const c2 = L.of("motion", "animate").pop();
    T.check("three keyframes are a tween: dur.small on ease.out", c2.opt.type === undefined && c2.opt.duration === .22 && JSON.stringify(c2.opt.ease) === "[0.22,1,0.36,1]", c2.opt);
    const warns = []; const cw = console.warn; console.warn = function (m) { warns.push(String(m)); };
    A.spring(ring, { width: ["0px", "10px"], opacity: [0, 1] }, "gel");
    console.warn = cw;
    const c3 = L.of("motion", "animate").pop();
    T.check("layout properties are dropped, with a warning", !("width" in c3.kf) && "opacity" in c3.kf && warns.some(function (m) { return /width/.test(m); }), [c3.kf, warns]);
    A.spring(ring, { x: [0, 4] }, "unknown-name");
    T.check("an unknown spring name falls back to snappy", L.of("motion", "animate").pop().opt.visualDuration === .22);
    A.settle();
    ring.style.transform = "rotate(24deg)"; ring.style.opacity = "";
    const h4 = A.spring(ring, { y: [0, 8] }, "soft");
    const c4 = L.of("motion", "animate").pop();
    h4.cancel();
    T.check("cancel(): Motion cancelled, back to the inline values from before", h4.done && c4.h.state === "cancelled" &&
      ring.style.transform === "rotate(24deg)" && ring.style.opacity === "", ring.style.cssText);
    const h5 = A.spring(ring, { x: [0, 6] });
    h5.finish();
    T.check("finish(): the end value at once", h5.done && ring.style.transform === "translate(6px, 0px)", ring.style.transform);
    A.spring(ring, { scale: [1, 1.04, 1] });
    A.spring(ring, { rotate: [0, 30] });
    T.check("a new spring on the element lands the previous one first", ring.style.transform === "scale(1)", ring.style.transform);
  });

  await scenario("flip", async function () {
    const { w, d, L, A } = boot({ gsap: true });
    const grid = d.getElementById("wgrid"), wa = d.getElementById("wa");
    const ev = events(d);
    let mut = 0;
    const h = A.flip(grid, function () { mut++; grid.appendChild(wa); }, { duration: .32, exclude: d.getElementById("wb"), enter: "pop" });
    const st = L.of("flip", "getState")[0], fr = L.of("flip", "from")[0];
    T.check("exclude leaves the dragged card out of the state", st.targets.length === 2 && st.targets.indexOf(d.getElementById("wb")) < 0);
    T.check("opts.duration", fr.vars.duration === .32);
    T.check("enter:'pop' animates entering cards from .9 on hermes.pop", typeof fr.vars.onEnter === "function" && (function () {
      fr.vars.onEnter([wa]); const f = L.of("gsap", "fromTo").pop();
      return f.a.opacity === 0 && f.a.scale === .9 && f.b.scale === 1 && f.b.ease === "hermes.pop" && f.b.duration === .38 && f.b.clearProps === "opacity,transform";
    })());
    T.check("one hermes:animstart on the scope", ev.length === 1 && ev[0].target === grid && ev[0].detail.kind === "flip");
    T.check("isFlipping() asks Flip", A.isFlipping(wa) === false && (L.flipping.add(wa), A.isFlipping(wa) === true));
    h.cancel();
    T.check("cancel() reverts the GSAP context", L.of("gsap", "ctx.revert").length === 1 && h.done && ev.length === 2);
    for (let i = 0; i < 25; i++) { const e = d.createElement("div"); e.setAttribute("data-flip-id", "x" + i); grid.appendChild(e); }
    L.clear();
    let m2 = 0;
    const h2 = A.flip(grid, function () { m2++; });
    T.check("more than 24 targets: instant (mutate only)", m2 === 1 && h2.done && L.of("flip").length === 0 && A.stats().instant.cap === 1);
    const h3 = A.flip(grid, function () { m2++; }, { max: 40 });
    T.check("...opts.max raises the cap", m2 === 2 && L.of("flip", "from").length === 1);
    T.check("no solid-glass class for the flight unless asked", L.of("flip", "from")[0].vars.toggleClass === undefined);
    A.settle();
    A.flip(grid, function () {}, { max: 40, lite: true });
    T.check("{lite:true}: Flip toggles .anim-lite for the flight (the §9 glass gate)", L.of("flip", "from").pop().vars.toggleClass === "anim-lite");
    A.settle();
    T.check("settle() lands it with progress(1)", h3.done && L.of("gsap", "progress").some(function (c) { return c.p === 1; }));
    let m3 = 0; const warns = []; const cw = console.warn; console.warn = function (m) { warns.push(String(m)); };
    A.flip(grid, function () { m3++; throw new Error("mutate broke"); });
    console.warn = cw;
    T.check("a throwing mutate is survived", m3 === 1);
    // the header rule covers opts.targets too, not only the scope
    const hnum = d.getElementById("hnum"); let m4 = 0;
    L.clear();
    const h4 = A.flip(grid, function () { m4++; }, { targets: [hnum, wa] });
    T.check("a header element reached through opts.targets is refused too (mutate only, counted)", m4 === 1 && h4.done && L.of("flip").length === 0 && A.stats().instant.header >= 1, A.stats().instant);
    // a card's own [data-flip-id] rows (Needs-you) are not targets of a grid
    // flip: they would move twice and eat into the cap
    A.settle(); L.clear();
    const nest = d.createElement("div"); nest.setAttribute("data-flip-id", "row1"); wa.appendChild(nest);
    A.flip(grid, function () {}, { max: 40 });
    const ns = L.of("flip", "getState").pop();
    T.check("default targets are the outermost [data-flip-id] under the scope: a card's nested rows are left out",
      !!ns && ns.targets.indexOf(nest) < 0 && ns.targets.indexOf(wa) >= 0, ns && ns.targets.length);
    A.settle(); L.clear();
    A.flip(wa, function () {});
    const ns2 = L.of("flip", "getState").pop();
    T.check("...while a flip scoped inside the card still takes those rows (the card itself is the scope's ancestor)",
      !!ns2 && ns2.targets.length === 1 && ns2.targets[0] === nest, ns2 && ns2.targets.length);
    A.settle();
  });

  await scenario("sequence", async function () {
    const { w, d, L, A } = boot({ gsap: true });
    const wa = d.getElementById("wa"), wb = d.getElementById("wb");
    const ev = events(d);
    let got = null, done = 0;
    const h = A.sequence("swap", function (tl, tok) {
      got = tok;
      tl.fromTo(wa, { opacity: 0 }, { opacity: 1, duration: .2 })
        .from([wa, wb], { y: 6, opacity: 0, duration: .3, stagger: .05, ease: "hermes.pop" }, .12)
        .to(wb, { opacity: 0, backgroundColor: "red", width: 10, duration: .2 });
    }, { done: function () { done++; } });
    const tl = L.of("gsap", "timeline")[0].tl;
    T.check("build(tl, T) gets the tokens", got === A.T);
    T.check("the timeline is built paused with defaults {ease: hermes.out}, then played", tl.vars.paused === true && tl.vars.defaults.ease === "hermes.out" && tl.played);
    T.check("from-tweens get clearProps opacity,transform", tl.tweens[1].v.clearProps === "opacity,transform" && tl.tweens[0].v.clearProps === "opacity,transform");
    T.check("a to-tween does not (done hides, then the context reverts)", tl.tweens[2].v.clearProps === undefined);
    T.check("hermes.* eases pass through while CustomEase is up", tl.tweens[1].v.ease === "hermes.pop");
    T.check("colour and layout properties are dropped", !("backgroundColor" in tl.tweens[2].v) && !("width" in tl.tweens[2].v) && tl.tweens[2].v.opacity === 0);
    T.check("one hermes:animstart, on the targets' common ancestor", ev.length === 1 && ev[0].target === d.getElementById("wgrid"));
    T.check("pending until it completes", !h.done && done === 0);
    let d2 = 0;
    const h2 = A.sequence("swap", function (tl2) { tl2.from(wa, { opacity: 0, duration: .2 }); }, { done: function () { d2++; } });
    T.check("starting the same name again lands the old one (progress(1), then kill)", h.done && done === 1 &&
      L.of("gsap", "progress").some(function (c) { return c.tl === tl && c.p === 1; }) && L.of("gsap", "kill").some(function (c) { return c.tl === tl; }));
    T.check("...and the old context is reverted after its done", L.of("gsap", "ctx.revert").length === 1);
    const tl2 = L.of("gsap", "timeline")[1].tl;
    tl2.progress(1); await w.__tick();
    T.check("a natural end runs done once and then reverts", h2.done && d2 === 1 && L.of("gsap", "ctx.revert").length === 2);
    let d3 = 0;
    const h3 = A.sequence("other", function (t3) { t3.to(wa, { opacity: .5, duration: .2 }); }, { done: function () { d3++; } });
    h3.cancel();
    T.check("cancel(): reverts, never runs done", h3.done && d3 === 0 && L.of("gsap", "ctx.revert").length === 3);
    let d5 = 0, called = 0;
    const h5 = A.sequence("zero", function (t5) { t5.set(wa, { opacity: 1 }).call(function () { called++; }); }, { done: function () { d5++; } });
    T.check("a sequence with no length (set/call only) lands at once: calls run, done once", h5.done && called === 1 && d5 === 1, [h5.done, called, d5]);
    let d4 = 0, i4 = 0;
    const warns = []; const cw = console.warn; console.warn = function (m) { warns.push(String(m)); };
    A.sequence("bad", function () { throw new Error("bad build"); }, { instant: function () { i4++; }, done: function () { d4++; } });
    console.warn = cw;
    T.check("a build that throws takes the instant path (instant, done, once each)", i4 === 1 && d4 === 1);
    // the allowlist has no side door: keyframes carry their own property maps, and
    // a key from Object.prototype is not an allowed key
    const w6 = []; console.warn = function (m) { w6.push(String(m)); };
    A.sequence("kf", function (t6) {
      t6.to(wa, { keyframes: [{ x: 10, backgroundColor: "red" }, { opacity: .5, width: 4, ease: "hermes.out" }], duration: .3, toString: 1, constructor: 1 });
      t6.to(wb, { keyframes: { x: [0, 5], height: [1, 2], easeEach: "hermes.pop" }, duration: .2 });
    });
    console.warn = cw;
    const kt = L.of("gsap", "tl.to").filter(function (c) { return c.a && c.a.keyframes; });
    const k1 = kt[0].a, k2 = kt[1].a;
    T.check("keyframes frames are filtered like the top level (colour and layout dropped)", k1.keyframes.length === 2 && !("backgroundColor" in k1.keyframes[0]) &&
      k1.keyframes[0].x === 10 && !("width" in k1.keyframes[1]) && k1.keyframes[1].opacity === .5 && k1.keyframes[1].ease === "hermes.out", k1);
    T.check("...the object form too, and easeEach passes through", !("height" in k2.keyframes) && k2.keyframes.x[1] === 5 && k2.keyframes.easeEach === "hermes.pop", k2);
    const own = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };
    T.check("inherited names (toString, constructor) are not allowed keys", !own(k1, "toString") && !own(k1, "constructor") && own(k1, "duration"), Object.keys(k1));
    T.check("...and each dropped property is warned about (once per name)", w6.some(function (m) { return /dropped height/.test(m); }) &&
      w6.some(function (m) { return /dropped toString/.test(m); }) && w6.some(function (m) { return /dropped constructor/.test(m); }), w6);
    // follow-ups started from done(): GSAP's context owns whatever is made inside
    // its animations' callbacks, and the cleanup that reverts the finished
    // sequence would revert them too (found against real GSAP: a same-name
    // sequence chained from done never ran, and a hold() armed there never fired)
    let chained = null, d7 = 0, held = 0, hh = null;
    const h7 = A.sequence("chain", function (t7) { t7.fromTo(wa, { opacity: 0 }, { opacity: 1, duration: .2 }); }, { done: function () {
      d7++;
      chained = A.sequence("chain", function (t8) { t8.to(wa, { opacity: .3, duration: .5 }); });
      hh = A.hold(1.3, function () { held++; });
    } });
    L.of("gsap", "timeline").pop().tl.progress(1);
    await w.__tick();
    T.check("a sequence chained from done() runs: it is not reverted by the first one's cleanup", d7 === 1 && !!chained && !chained.done &&
      L.of("gsap", "timeline").pop().tl.killed === false && L.of("gsap", "timeline").pop().tl.reverted === false, [d7, chained && chained.done]);
    L.finishAll(); await w.__tick();
    T.check("...and it can finish on its own", chained.done === true);
    T.check("a hold() armed in done() is not killed with the finished sequence", !!hh && hh.done === true && held === 1, [hh && hh.done, held]);
    T.check("the first sequence's handle is done, and its done ran once", h7.done && d7 === 1);
    // no GSAP: instant, and a load is kicked
    const b = boot();
    let seq = [];
    b.A.sequence("view", function () { seq.push("build"); }, { instant: function () { seq.push("instant"); }, done: function () { seq.push("done"); } });
    T.check("without GSAP: instant, then done; build never runs", seq.join() === "instant,done");
    T.check("...and GSAP is fetched for next time", b.d.querySelectorAll("script").length === 3 && b.A.stats().instant.nolib === 1);
  });

  await scenario("hold", async function () {
    const { w, d, L, A } = boot({ gsap: true });
    let n = 0;
    const t0 = w.__timers.length;
    const h = A.hold(1.3, function () { n++; });
    const dc = L.of("gsap", "delayedCall")[0];
    T.check("with GSAP ready, hold is gsap.delayedCall (no timer)", !!dc && dc.s === 1.3 && w.__timers.length === t0);
    dc.dc.__natural();
    T.check("...and runs fn when it fires", n === 1 && h.done);
    const b = boot();
    let m = 0;
    const h2 = b.A.hold(2.2, function () { m++; });
    T.check("without GSAP, one setTimeout of 2200 ms", b.w.__timers.length === 1 && b.w.__timers[0].ms === 2200);
    h2.cancel();
    T.check("cancel(): the timer goes, fn never runs", b.w.__timers.length === 0 && m === 0 && h2.done);
    const h3 = b.A.hold(2.6, function () { m++; });
    h3.finish();
    T.check("finish(): fn runs now", m === 1 && b.w.__timers.length === 0);
    b.A.hold(2.6, function () { m++; });
    b.A.settle();
    T.check("settle() fires pending holds", m === 2);
    T.check("holds send no hermes:anim* events and are not 'running' animations", b.A.stats().running === 0);
  });

  await scenario("count", async function () {
    const { w, d, L, A } = boot({ anime: true });
    const el = d.getElementById("num");
    const ev = events(d);
    el.style.minWidth = "2px";
    const h = A.count(el, 1234);
    const c = L.of("anime", "animate")[0];
    T.check("anime tweens a plain object from 0", c.targets.v === 0 && c.params.v === 1234);
    T.check("...over T.dur.count on the expo curve", c.params.duration === 800 && JSON.stringify(c.params.ease.__bez) === "[0.16,1,0.3,1]", c.params);
    T.check("while counting, aria-label carries the final value", el.getAttribute("aria-label") === "1,234");
    T.check("...and min-width is fixed in ch from the final string", el.style.minWidth === "5ch");
    c.a.step(600.4);
    T.check("updates write the formatted value", el.textContent === "600");
    const writes = []; Object.defineProperty(el, "__w", { value: writes });
    c.a.step(600.2);
    T.check("...only when the string changes", el.textContent === "600");
    c.a.complete();
    T.check("at the end: final text, aria-label removed, min-width restored", h.done && el.textContent === "1,234" && !el.hasAttribute("aria-label") && el.style.minWidth === "2px");
    T.check("one event pair", ev.length === 2);
    L.clear();
    const h2 = A.count(el, 1234);
    T.check("the same value again: nothing moves (a 60 s refresh is still)", h2.done && L.of("anime").length === 0 && ev.length === 2);
    A.count(el, 2000, { fmt: function (n) { return "$" + Math.round(n); } });
    const c2 = L.of("anime", "animate")[0];
    T.check("from the last value shown, with fmt", c2.targets.v === 1234 && el.getAttribute("aria-label") === "$2000");
    const again = A.count(el, 2000);
    T.check("a second call to the same target while running is the same handle", again.done === false && L.of("anime", "animate").length === 1);
    c2.a.step(1500);
    A.count(el, 500);
    const c3 = L.of("anime", "animate")[1];
    T.check("a new target mid-count continues from the value on screen", c2.a.state === "cancelled" && c3.targets.v === 1500, [c2.a.state, c3.targets.v]);
    A.count(el, 10, { from: 0, delay: .12, dur: .5 });
    const c4 = L.of("anime", "animate").pop();
    T.check("opts.from, delay and dur (ms to anime)", c4.targets.v === 0 && c4.params.delay === 120 && c4.params.duration === 500, c4.params);
    L.finishAll();
    // the cap: 8 at once
    const host = d.body; const nums = [];
    for (let i = 0; i < 9; i++) { const s = d.createElement("span"); s.textContent = "0"; host.appendChild(s); nums.push(s); }
    L.clear();
    nums.forEach(function (s, i) { A.count(s, 100 + i); });
    T.check("at most 8 count-ups at once; the 9th lands at once", L.of("anime", "animate").length === 8 && nums[8].textContent === "108" && A.stats().instant.cap === 1);
    L.finishAll();
    // no anime yet
    const b = boot();
    const e2 = b.d.getElementById("num");
    b.A.count(e2, 42);
    T.check("without anime: the final text at once, and anime is fetched", e2.textContent === "42" && b.d.querySelectorAll("script").length === 1);
  });

  await scenario("draw and words", async function () {
    const { w, d, L, A } = boot({ anime: true });
    const p1 = d.getElementById("p1");
    const h = A.draw(p1, { dur: .36 });
    const c = L.of("anime", "animate")[0];
    T.check("draw: createDrawable, then draw ['0 0','0 1']", L.of("anime", "svg.createDrawable").length === 1 && JSON.stringify(c.params.draw) === '["0 0","0 1"]');
    T.check("...over opts.dur (ms)", c.params.duration === 360);
    T.check("...createDrawable blanks the path at once (the real library's behaviour, which the stub copies)", p1.getAttribute("draw") === "0 0" && p1.getAttribute("stroke-dasharray") === "0 1010");
    c.a.complete();
    // anime's revert() restores the drawable's own blank initial state, not the
    // page's: reverting alone left every finished draw invisible (checked in a
    // browser against anime 4.5.0). The page's attributes must come back.
    const bare = function (el) { return el.getAttribute("pathLength") === null && el.getAttribute("stroke-dasharray") === null &&
      el.getAttribute("stroke-dashoffset") === null && el.getAttribute("draw") === null && el.getAttribute("stroke") === "#000"; };
    T.check("...a finished draw leaves the path fully drawn: the page's own attributes, none of the drawable's", h.done && bare(p1), p1.outerHTML);
    // anime 4.5's revert() leaves an inline stroke-linecap:butt (checked in
    // Chromium), which beats the page's round caps: the inline value comes back
    L.clear();
    const hb = A.draw(p1);
    p1.style.strokeLinecap = "butt";
    L.of("anime", "animate")[0].a.complete();
    T.check("...and the path's inline stroke-linecap is the page's own again (anime's leftover butt cap is dropped)", hb.done && !p1.style.strokeLinecap, p1.style.cssText);
    L.clear();
    const p2 = d.getElementById("p2");
    p2.setAttribute("stroke-dasharray", "4 2");           // a page's own dash style survives untouched
    const hc = A.draw(p2); hc.cancel();
    T.check("a cancelled draw restores the page's own attributes (a dash style it had stays)", p2.getAttribute("stroke-dasharray") === "4 2" &&
      p2.getAttribute("draw") === null && p2.getAttribute("pathLength") === null, p2.outerHTML);
    p2.removeAttribute("stroke-dasharray");
    const hs = A.draw(p2); A.settle();
    T.check("settle() (the page going hidden) leaves it drawn, not blank", hs.done && bare(p2), p2.outerHTML);
    const p3 = d.getElementById("p3");
    const realAnimate = w.anime.animate;
    w.anime.animate = function () { throw new Error("boom"); };
    const hf = A.draw(p3);
    w.anime.animate = realAnimate;
    T.check("an animate() that throws after createDrawable blanked the path restores it too", hf.done && bare(p3), p3.outerHTML);
    L.clear();
    A.draw([p1, d.getElementById("p2"), d.getElementById("p3")]);
    T.check("more than 2 paths: instant", L.of("anime").length === 0 && A.stats().instant.cap === 1);
    A.draw(p1); A.draw(d.getElementById("p2"));
    A.draw(d.getElementById("p3"));
    T.check("...and at most 2 running at once", L.of("anime", "animate").length === 2);
    L.finishAll();
    L.clear();
    const hd = d.getElementById("hd");
    const hw = A.words(hd, { delay: .06 });
    const sp = L.of("anime", "splitText")[0], wa = L.of("anime", "animate")[0];
    T.check("words: splitText(el, {words:true})", !!sp && sp.opts.words === true && sp.el === hd);
    T.check("...the words rise .5em to 0 and fade in, 30 ms apart, dur.small*2", JSON.stringify(wa.params.translateY) === '["0.5em",0]' &&
      JSON.stringify(wa.params.opacity) === "[0,1]" && wa.params.duration === 440 && wa.params.delay.__stagger[0] === 30 && wa.params.delay.__stagger[1].start === 60, wa.params);
    wa.a.complete();
    T.check("...and split.revert() makes it plain text again", hw.done && sp.split.reverted === true);
    L.clear();
    A.words(d.getElementById("hrich"));
    T.check("words() refuses anything but a plain-text heading", L.of("anime").length === 0);
  });

  await scenario("the header is CSS's and Motion's only", async function () {
    const { w, d, L, A } = boot({ gsap: true, anime: true });
    const warns = []; const cw = console.warn; console.warn = function (m) { warns.push(String(m)); };
    const hn = d.getElementById("hnum"), seg = d.querySelector("header .seg");
    let mut = 0;
    A.flip(seg, function () { mut++; });
    T.check("flip refuses a header scope (mutate only)", mut === 1 && L.of("flip").length === 0 && A.stats().instant.header === 1);
    let done = 0, inst = 0;
    A.sequence("hdr", function (tl) { tl.from(d.getElementById("wa"), { opacity: 0 }).from(hn, { y: 4 }); }, { instant: function () { inst++; }, done: function () { done++; } });
    T.check("sequence refuses a header target (instant path, context reverted)", done === 1 && inst === 1 && A.stats().instant.header === 2 &&
      L.of("gsap", "ctx.revert").length === 1 && !L.of("gsap", "play").length);
    T.check("...the header tween was never even created", !L.of("gsap", "tl.from").some(function (c) { return c.t === hn; }));
    A.count(hn, 9);
    T.check("count refuses a header target (final text at once)", hn.textContent === "9" && L.of("anime").length === 0 && A.stats().instant.header === 3);
    A.words(d.getElementById("model-pill")); A.draw(hn);
    T.check("draw and words too", A.stats().instant.header === 5);
    console.warn = cw;
    T.check("one console warning, not one per call", warns.filter(function (m) { return /header/.test(m); }).length === 1, warns);
    A.enter(d.getElementById("model-pill"), "fade");
    T.check("Motion may still animate a header element (WAAPI, no per-frame inline writes)", L.of("motion", "animate").length === 1);
  });

  await scenario("the view entrance", async function () {
    const { w, d, L, A } = boot({ gsap: true });
    const hub = d.getElementById("view-hub"), mind = d.getElementById("view-mind");
    hub.hidden = true; mind.hidden = false;
    await w.__tick();
    let tls = L.of("gsap", "timeline");
    T.check("un-hiding a .stage>.view plays one sequence('view')", tls.length === 1);
    const tl = tls[0].tl;
    T.check("...the view fades in from 0 over .18", tl.tweens[0].type === "fromTo" && tl.tweens[0].t === mind && tl.tweens[0].from.opacity === 0 && tl.tweens[0].v.duration === .18);
    const parts = S.listOf(tl.tweens[1].t).map(function (e) { return e.id; });
    T.check("...Settings parts: the rail plus the visible panel", JSON.stringify(parts) === '["set-rail","sec-a"]', parts);
    T.check("...rising 8px, .32 s, stagger min(.03, .2/n), at .02", tl.tweens[1].v.y === 8 && tl.tweens[1].v.duration === .32 &&
      tl.tweens[1].v.stagger.each === .03 && tl.tweens[1].pos === .02 && tl.tweens[1].v.clearProps === "opacity,transform");
    mind.hidden = true; hub.hidden = false;
    await w.__tick();
    tls = L.of("gsap", "timeline");
    T.check("a fast second switch finishes the first", tls.length === 2 && L.of("gsap", "progress").some(function (c) { return c.tl === tl && c.p === 1; }));
    T.check("Hub parts: its .wgrid>.w cards", S.listOf(tls[1].tl.tweens[1].t).length === 3);
    d.getElementById("view-quiet").hidden = false;
    await w.__tick();
    T.check("a view with data-anim=off opts out", L.of("gsap", "timeline").length === 2);
    d.getElementById("sec-b").hidden = false;
    d.getElementById("wa").hidden = true; d.getElementById("wa").hidden = false;
    await w.__tick();
    T.check("[hidden] toggles below a view are not view switches", L.of("gsap", "timeline").length === 2);
    // a visible view hidden and shown again in one breath was never away: no entrance
    const n0 = L.of("gsap", "timeline").length;
    T.check("(setup: the hub is the visible view)", !hub.hidden && mind.hidden);
    hub.hidden = true; hub.hidden = false;
    await w.__tick();
    T.check("a view hidden then re-shown within one batch gets no entrance", L.of("gsap", "timeline").length === n0, L.of("gsap", "timeline").length - n0);
    hub.hidden = true; await w.__tick();
    hub.hidden = true; hub.hidden = false;
    await w.__tick();
    T.check("...while one that was really hidden and is shown still does", L.of("gsap", "timeline").length === n0 + 1, L.of("gsap", "timeline").length - n0);
    const agent = d.createElement("div"); agent.className = "view"; agent.id = "view-agent"; agent.hidden = true;
    agent.innerHTML = "<section>left</section><section>right</section>";
    d.querySelector(".stage").appendChild(agent);
    await w.__tick();
    agent.hidden = false;
    await w.__tick();
    const tla = L.of("gsap", "timeline");
    T.check("a view added later (aux_agent) is picked up", tla.length === n0 + 2 && S.listOf(tla[n0 + 1].tl.tweens[1].t).length === 2, tla.length);
    // boot: a view restored while the page is still loading does not animate
    const b = boot({ gsap: true, readyState: "loading" });
    b.d.getElementById("view-hub").hidden = true; b.d.getElementById("view-mind").hidden = false;
    await b.w.__tick();
    T.check("the boot view (restored during load) is skipped", b.L.of("gsap", "timeline").length === 0 && b.A.once("view:boot") === false);
    b.d.readyState = "complete";
    b.d.getElementById("view-mind").hidden = true; b.d.getElementById("view-hub").hidden = false;
    await b.w.__tick();
    T.check("...the first switch after load animates", b.L.of("gsap", "timeline").length === 1);
    const c = boot({ gsap: true, media: OS_REDUCE });
    c.d.getElementById("view-hub").hidden = true; c.d.getElementById("view-mind").hidden = false;
    await c.w.__tick();
    T.check("under reduced motion the view just appears", c.L.of("gsap", "timeline").length === 0);
  });

  await scenario("settle() and stats()", async function () {
    const { w, d, L, A } = boot({ gsap: true, anime: true });
    let done = 0;
    A.enter(d.getElementById("wa"), "rise");
    A.exit(d.getElementById("wb"), "dismiss", function () { done++; });
    A.flip(d.getElementById("wgrid"), function () {});
    A.count(d.getElementById("num"), 3);
    A.sequence("s", function (tl) { tl.from(d.getElementById("wc"), { opacity: 0 }); }, { done: function () { done++; } });
    const st = A.stats();
    T.check("stats(): started, running and the library states", st.started === 5 && st.running === 5 && st.libs.gsap === "ready" &&
      st.libs.anime === "ready" && st.libs.motion === "14.6.0", st);
    A.settle();
    T.check("settle() finishes every running handle and calls pending dones", A.stats().running === 0 && done === 2);
    const b = boot({ motionLegacy: true });
    T.check("Motion 11 (no MotionGlobalConfig) reports 'legacy'", b.A.stats().libs.motion === "legacy");
    const c = boot({ motion: false });
    T.check("no Motion reports null, and every Motion call is instant", c.A.stats().libs.motion === null && c.A.enter(c.d.getElementById("wa"), "rise").done);
    let x = 0; c.A.exit(c.d.getElementById("wa"), "popOut", function () { x++; });
    T.check("...exit's done still runs", x === 1 && c.A.stats().instant.nolib === 2);
  });

  // the real Motion 14 (the vendored bundle; E20: it runs in the mini DOM).
  // HERMES_ANIM_MOTION_FILE points at another copy for a local check.
  await scenario("real Motion 14: no late commit, CSS gets the element back", async function () {
    const file = process.env.HERMES_ANIM_MOTION_FILE || path.join(REPO, "dashboard", "vendor", "motion", "motion-14.6.0.min.js");
    if (!fs.existsSync(file)) { console.log("  SKIP the vendored Motion 14 bundle is not in this tree yet (the vendor cluster adds it)"); return; }
    let now = 0;
    const w = createWindow({ html: FIX });
    w.performance = { now: function () { return now; } };
    w.__run(fs.readFileSync(file, "utf8"), "motion.js");
    w.__run(SRC, "aux_anim.js");
    const d = w.document, A = w.HermesAnim;
    T.check("(real Motion loaded: v14 config and releaseElement present)", !!w.Motion.MotionGlobalConfig && typeof w.Motion.releaseElement === "function");
    // frames: advance the clock, run rAF callbacks, finish WAAPI animations whose time is up
    async function frames(n) {
      for (let i = 0; i < n; i++) {
        now += 16; w.__flushTimers(1); await w.__tick(2);
        (d.__allAnims || []).forEach(function (x) {
          if (x.playState !== "running") return;
          if (x.__t0 == null) x.__t0 = now;
          if (now - x.__t0 >= (x.timing.duration || 0) + (x.timing.delay || 0)) x.finish();
        });
      }
    }
    const wa = d.getElementById("wa"), wb = d.getElementById("wb");
    wa.style.transform = "translateX(3px)";
    const h = A.enter(wa, "pop");
    for (let i = 0; i < 80 && !h.done; i++) await frames(1);
    T.check("enter pop runs to its natural end", h.done === true);
    await frames(20);
    T.check("...and afterwards the element has only its own inline style back", wa.style.opacity === "" && wa.style.transform === "translateX(3px)", wa.style.cssText);
    let done = 0;
    const x = A.exit(wb, "popOut", function () { done++; });
    await frames(3); x.cancel(); await frames(40);
    T.check("a cancelled exit: no done, and nothing lands on the element later", done === 0 && wb.style.cssText === "", wb.style.cssText);
    const y = A.exit(wb, "popOut", function () { done++; });
    await frames(3); y.finish(); await frames(40);
    T.check("a finished exit: done once, and no late commit of opacity 0", done === 1 && wb.style.cssText === "", wb.style.cssText);
    const s = A.spring(wa, { rotate: [0, 12] }, "snappy");
    for (let i = 0; i < 80 && !s.done; i++) await frames(1);
    await frames(10);
    T.check("a spring's end value persists", s.done && /rotate\(12deg\)/.test(wa.style.transform), wa.style.transform);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  });

  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
