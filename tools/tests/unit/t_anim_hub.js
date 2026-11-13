// t_anim_hub.js — index.html's surfaces on the motion runtime
// (docs/plans/motion-system-2026-10.md §8.1-8.7, test plan §10.1 "t_anim_hub.js").
//
// The REAL index.html (markup, CSS and inline shell) boots in the mini DOM
// (_a11y_dom.js) after the REAL aux_anim.js (the §6.1 order), against the
// recording library stubs (_anim_stubs.js: Motion, GSAP + Flip + CustomEase,
// anime), which schedule no timers — an animation ends only when the test ends
// it (L.finishAll). Then the same surfaces again under both Reduce-motion
// switches (every one lands at its end state at once) and with no runtime at
// all (index.html's ANIM_NULL). Nothing here fetches or wakes a model: every
// /api route is a table in this file.
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, computedValue, parseCSS, harness, Event, MouseEvent } = require("./_a11y_dom.js");
const S = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const ANIM = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");
const PAGE = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
const OS_REDUCE = function (q) { return /prefers-reduced-motion/.test(q); };

async function scenario(name, fn) {
  console.log(name);
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}

// ---- the dashboard's API, as tables ------------------------------------------
const IDS = ["w0", "w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8", "w9", "w10", "w11", "w12", "w13"];
const CAPS = { skills: { total: 12, categories: [], items: [] }, memory: { count: 3, facts: [] },
  insights: { sessions: 40, tool_calls: 1234, messages: 2500, input_tokens: 1500000, output_tokens: 800, platforms: [],
    by_day: [{ n: 1 }, { n: 4 }, { n: 2 }, { n: 6 }] } };
function state(order) {
  return { order: order.slice(), posts: [], sys: { cpu_pct: 12, ram_pct: 70, disk_pct: 90 }, caps: JSON.parse(JSON.stringify(CAPS)),
    poll: [], approves: [] };
}
function widgets(st) {
  const out = {};
  st.order.forEach(function (id) {
    out[id] = { meta: { title: "Card " + id, size: "card", cat: "life", icon: "spark" }, data: id === "system" ? st.sys : {} };
  });
  return out;
}
function routes(st) {
  return {
    "/api/hub": function () { return { order: st.order.slice(), model_online: true, widgets: widgets(st) }; },
    "/api/layout": function (u, init) {
      const b = JSON.parse(init.body); st.posts.push(b);
      if (b.op === "remove") st.order = st.order.filter(function (x) { return x !== b.id; });
      else if (b.op === "add") st.order.push(b.id);
      else if (b.op === "set") st.order = b.order.slice();
      return { ok: true };
    },
    "/api/catalog": function () { return { catalog: [{ id: "wnew", title: "New card", cat: "life" }, { id: "w0", title: "Card w0", cat: "life" }] }; },
    "/api/sys": function () { return st.sys; },
    "/api/capabilities": function () { return st.caps; },
    "/api/chat": function () { return { job: "J1" }; },
    "/api/chat/poll": function () { return st.poll.length ? st.poll.shift() : { state: "done", done: true, reply: "ok", text: "ok" }; },
    "/api/chat/approve": function (u, init) { st.approves.push(JSON.parse(init.body)); return { ok: true }; }
  };
}
// o: {runtime (default true), media, reduce (in-app switch), order, clock}
async function boot(o) {
  o = o || {};
  const st = state(o.order || IDS);
  let L = null;
  const clock = { now: 1000 };
  const w = await bootDashboard({ html: PAGE, routes: routes(st), media: o.media, before: function (w0) {
    w0.performance = { now: function () { return clock.now; } };
    if (o.reduce) w0.document.documentElement.setAttribute("data-reduce", "1");
    // browser "named access" makes the #toast element shadow index.html's
    // toast() in the mini DOM (a browser lets the function win)
    delete w0.toast;
    L = S.install(w0, o.runtime === false ? {} : { gsap: true, anime: true });
    if (o.runtime !== false) w0.__run(ANIM, "aux_anim.js");
  } });
  return { w: w, d: w.document, L: L, A: w.HermesAnim, st: st, clock: clock };
}
// The stub's draw leaves the path exactly as the page set it, but the real anime
// 4.5 leaves an inline `stroke-linecap: butt` when its revert() runs (found in
// Chromium; it beats the page's round caps for good). Emulate that on the stub.
function drawLeavesButt(w) {
  const an0 = w.anime.animate;
  w.anime.animate = function (targets, params) {
    const a = an0.apply(this, arguments);
    if (params && params.draw && Array.isArray(targets)) {
      const rv = a.revert;
      a.revert = function () { const r = rv.apply(this, arguments); targets.forEach(function (x) { if (x && x.el) x.el.style.strokeLinecap = "butt"; }); return r; };
    }
    return a;
  };
}
// end everything the stubs are running, round after round: a sequence's done()
// (and a hold) start the next step from a microtask
async function drain(c, n) { for (let i = 0; i < (n || 8); i++) { c.L.finishAll(); await c.w.__tick(); } }
const cards = function (d) { return Array.from(d.querySelectorAll("#wgrid .w")); };
const ids = function (d) { return cards(d).map(function (x) { return x.dataset.id; }); };
const motionOn = function (L, el) { return L.of("motion", "animate").filter(function (c) { return c.el === el; }); };
const kfIs = function (c, kf) { return !!c && JSON.stringify(c.kf) === JSON.stringify(kf); };
const counts = function (L) { return L.of("anime", "animate").filter(function (c) { return c.targets && typeof c.targets === "object" && "v" in c.targets; }); };
const RISE = { opacity: [0, 1], y: [10, 0] };

(async function main() {
  console.log("index.html surfaces on the motion runtime (§8.1-8.7)");
  T.check("the page loads the vendored Motion, then aux_anim.js, before its inline shell (§6.1)", (function () {
    const iM = PAGE.indexOf('src="/vendor/motion/motion-'), iA = PAGE.indexOf('src="/aux_anim.js"'), iI = PAGE.indexOf("<script>\nconst $=");
    return iM > 0 && iM < iA && iA < iI;
  })());

  // -------------------------------------------------------------------------
  await scenario("§8.1 hub boot and refresh", async function () {
    const c = await boot();
    const { w, d, L } = c;
    T.check("the page boots with the runtime and no error", !!c.A && w.__errors.length === 0, w.__errors.map(String));
    const boot1 = L.of("motion", "animate").filter(function (x) { return x.el && x.el.classList && x.el.classList.contains("w"); });
    T.check("the first render is one rise stagger, capped at 12 of the 14 cards", boot1.length === 12 && boot1.every(function (x) { return kfIs(x, RISE); }), boot1.length);
    const delays = boot1.map(function (x) { return x.opt.delay; });
    T.check("...one step apart, the whole tail inside .4 s", delays[0] === 0 && delays.every(function (v, i) { return i === 0 || v > delays[i - 1]; }) && delays[11] <= .4,
      delays);
    T.check("every card carries data-flip-id (Flip matches cards across rebuilds)", cards(d).every(function (x) { return x.getAttribute("data-flip-id") === x.dataset.id; }));
    await drain(c);
    T.check("...and the cards end under CSS control", cards(d).every(function (x) { return x.style.opacity === "" && x.style.transform === ""; }));

    const before = cards(d); L.clear();
    await w.__run("refreshHub()"); await w.__tick();
    T.check("refreshHub with the same order and data makes zero library calls", L.calls.length === 0, L.calls.map(function (x) { return x.lib + "." + x.fn; }));
    T.check("...and patches the same card nodes (no rebuild)", cards(d).every(function (x, i) { return x === before[i]; }));
    c.st.order = IDS.slice().reverse();
    await w.__run("refreshHub()"); await w.__tick();
    T.check("refreshHub with a new order rebuilds in that order", ids(d).join() === IDS.slice().reverse().join(), ids(d));
    T.check("...quietly: no Flip, no entrance, no library call at all", L.calls.length === 0 && L.of("flip").length === 0, L.calls.map(function (x) { return x.lib + "." + x.fn; }));
    await w.__run("renderHub()"); await w.__tick();
    T.check("a later plain renderHub() (a task added, a chat turn) is quiet too", L.calls.length === 0, L.calls.length);
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  await scenario("§8.2 Customize: no rebuild, the jiggle on rotate", async function () {
    const c = await boot({ order: ["w0", "w1", "w2"] });
    const { w, d, L } = c;
    await drain(c);
    const before = cards(d); L.clear();
    d.getElementById("edit-toggle").click(); await w.__tick();
    T.check("Customize keeps the card nodes (no rebuild)", cards(d).length === 3 && cards(d).every(function (x, i) { return x === before[i]; }));
    T.check("...so nothing re-enters", L.of("motion", "animate").length === 0, L.of("motion", "animate").length);
    T.check("...the grid is editing and every card draggable", d.getElementById("wgrid").classList.contains("editing") && cards(d).every(function (x) { return x.draggable === true; }));
    const t = before[1].querySelector(".w-title");
    T.check("...and the hub view opts out of the view entrance while the cards jiggle (data-anim=off)", d.getElementById("view-hub").getAttribute("data-anim") === "off");
    T.check("...and the titles say how to move them (C96)", /Alt\+arrow/.test(t.getAttribute("aria-label")) && t.getAttribute("aria-keyshortcuts") === "Alt+ArrowUp Alt+ArrowDown" &&
      !t.hasAttribute("aria-haspopup"), [t.getAttribute("aria-label"), t.getAttribute("aria-haspopup")]);
    const anim = computedValue(w, before[0], "animation");
    T.check("the jiggle is the wjig animation", !!anim && /wjig/.test(anim.value), anim && anim.value);
    const kf = /@keyframes\s+wjig\s*\{([^@]*?\})\s*\}/.exec(d.querySelectorAll("style").map ? "" : Array.from(d.querySelectorAll("style")).map(function (s) { return s.textContent; }).join("\n"));
    T.check("...whose keyframes animate `rotate`, never `transform` (so a Flip's transform composes)", !!kf && /rotate\s*:/.test(kf[1]) && !/transform/.test(kf[1]), kf && kf[1]);
    const grid0 = d.getElementById("wgrid"), css0 = Array.from(d.querySelectorAll("style")).map(function (s) { return s.textContent; }).join("\n");
    const ctl = before[1].querySelector(".w-ctl");
    T.check("the remove buttons pop in when Customize turns on (wctlIn, via .editing-in)", grid0.classList.contains("editing-in") &&
      /wctlIn/.test((computedValue(w, ctl, "animation") || {}).value || ""), (computedValue(w, ctl, "animation") || {}).value);
    T.check("...and the OS reduce rule beats the entrance rule (same specificity, later wins)", /prefers-reduced-motion:reduce\)\{[^}]*\.wgrid\.editing-in \.w-ctl\{animation:none/.test(css0.replace(/\s+/g, "")) || /\.wgrid\.editing-in \.w-ctl\{animation:none\}/.test(css0));
    d.getElementById("edit-toggle").click(); await w.__tick();
    T.check("leaving Customize drops .editing-in", !d.getElementById("wgrid").classList.contains("editing-in"));
    T.check("leaving Customize keeps the nodes too", cards(d).every(function (x, i) { return x === before[i]; }));
    T.check("...and gives the hub its view entrance back", !d.getElementById("view-hub").hasAttribute("data-anim"));
    T.check("...and puts the titles back", t.getAttribute("aria-label") === "Open Card w1" && t.getAttribute("aria-haspopup") === "dialog" && !t.hasAttribute("aria-keyshortcuts") &&
      before[1].draggable === false, [t.getAttribute("aria-label"), before[1].draggable]);
    d.getElementById("edit-toggle").click(); await w.__tick();       // Customize again, for the rebuild check
    T.check("...but not on a rebuild inside Customize (a remove/add/move makes new cards: the buttons must not blink again)", (function () {
      w.__run("buildFrom(window.lastHub)");
      return !d.getElementById("wgrid").classList.contains("editing-in") && d.getElementById("wgrid").classList.contains("editing") &&
        !/wctlIn/.test((computedValue(w, d.querySelector("#wgrid .w-ctl"), "animation") || {}).value || "x");
    })(), d.getElementById("wgrid").className);
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  await scenario("§8.2 remove: a shrinkOut exit, then a Flip", async function () {
    const c = await boot();
    const { w, d, L } = c;
    await drain(c);
    d.getElementById("edit-toggle").click(); await w.__tick();
    // what the grid looks like at the moment Flip measures (before) and starts (after)
    const grid = d.getElementById("wgrid"), atFlip = [];
    const look = function (k) { const c0 = grid.querySelector(".w"); atFlip.push({ k: k, cls: grid.classList.contains("flipping"), anim: (computedValue(w, c0, "animation") || {}).value }); };
    const gs0 = w.Flip.getState, fr0 = w.Flip.from;
    w.Flip.getState = function () { look("getState"); return gs0.apply(this, arguments); };
    w.Flip.from = function () { look("from"); return fr0.apply(this, arguments); };
    L.clear();
    // a widget whose own renderer would animate: is the runtime live while the Flip's rebuild runs it?
    // ...and one that carries [data-flip-id] rows of its own, as Needs-you does
    w.__run("window.__liveAtFill=[];RENDER.w1=function(b){window.__liveAtFill.push(HermesAnim.live());b.innerHTML='<i data-flip-id=\"ny-row-a\"></i><i data-flip-id=\"ny-row-b\"></i>';}");
    const card = d.querySelector('#wgrid .w[data-id="w2"]');
    card.querySelector(".rm").click(); await w.__tick();
    const ex = motionOn(L, card)[0];
    T.check("the card shrinks out (Motion, opacity + scale only)", kfIs(ex, { opacity: [1, 0], scale: [1, .86] }), ex && ex.kf);
    T.check("...while the remove POST runs in parallel", c.st.posts.some(function (p) { return p.op === "remove" && p.id === "w2"; }), c.st.posts);
    T.check("...and the card is still shown while it leaves", card.style.display === "" && card.style.visibility === "" && card.isConnected);
    T.check("nothing moves before the exit is done", L.of("flip").length === 0);
    await drain(c);
    const gs = L.of("flip", "getState"), fr = L.of("flip", "from");
    T.check("then ONE Flip: the state before, the rebuild, the slide", gs.length === 1 && fr.length === 1, [gs.length, fr.length]);
    // display:none here made the others jump into the gap when the exit ended, BEFORE
    // Flip measured, so the Flip had nothing to slide (seen in real Chromium + GSAP 3.15)
    T.check("...measured with the leaving card hidden but still holding its slot (visibility, never display:none)",
      gs[0] && gs[0].targets.some(function (x) { return x === card && x.style.visibility === "hidden" && x.style.display === ""; }));
    T.check("the Flip's targets are the cards only (a widget's own [data-flip-id] rows would move twice and count against the 24 cap)",
      gs[0] && gs[0].targets.length === IDS.length && gs[0].targets.every(function (x) { return x.classList.contains("w"); }) &&
      fr[0] && fr[0].vars.targets.every(function (x) { return x.classList.contains("w"); }), gs[0] && gs[0].targets.length);
    T.check("...over the layout duration, with no pop-in for anyone", fr[0] && fr[0].vars.duration === .42 && !fr[0].vars.onEnter, fr[0] && fr[0].vars);
    T.check("the jiggle is paused when Flip measures and while it runs (else GSAP writes the tilt back inline)",
      atFlip.length === 2 && atFlip.every(function (x) { return x.cls && x.anim === "none"; }), atFlip);
    T.check("...and resumes once it has landed", !grid.classList.contains("flipping") && /wjig/.test((computedValue(w, grid.querySelector(".w"), "animation") || {}).value), grid.className);
    T.check("the rebuild inside the Flip is quiet: a widget's own renderer sees the runtime NOT live (only the layout moves)",
      w.__liveAtFill.length === 1 && w.__liveAtFill[0] === false, w.__liveAtFill);
    T.check("the grid is the server's new order", ids(d).join() === IDS.filter(function (x) { return x !== "w2"; }).join(), ids(d));
    T.check("...still in Customize", d.getElementById("wgrid").classList.contains("editing") && cards(d).every(function (x) { return x.draggable; }));
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  await scenario("§8.2 add from the Widget Center: badge spring + Flip with enter", async function () {
    const c = await boot({ order: ["w0", "w1"] });
    const { w, d, L } = c;
    await drain(c);
    d.getElementById("add-widget").click(); await w.__tick();
    const tile = d.querySelector('#wc-grid .mcard[data-id="wnew"]');
    T.check("(the Widget Center lists the new card)", !!tile);
    L.clear();
    tile.click(); await w.__tick();
    const badge = tile.querySelector(".add"), sp = motionOn(L, badge)[0];
    T.check("the tile's badge springs (gel) to say it is added", kfIs(sp, { scale: [.6, 1] }) && sp.opt.type === "spring" && sp.opt.visualDuration === .34, sp && [sp.kf, sp.opt]);
    const fr = L.of("flip", "from")[0];
    T.check("behind the modal, the grid Flips with a pop-in for the new card", !!fr && typeof fr.vars.onEnter === "function", fr && fr.vars);
    T.check("...and holds it", ids(d).join() === "w0,w1,wnew", ids(d));
    await drain(c);
    T.check("the badge is handed back to CSS when it lands (no identity transform left inline)", badge.style.transform === "", badge.style.transform);
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  await scenario("§8.2 drag reflow: Flip, with 140 ms hysteresis and isFlipping", async function () {
    const c = await boot({ order: ["w0", "w1", "w2", "w3", "w4", "w5"] });
    const { w, d, L } = c;
    await drain(c);
    d.getElementById("edit-toggle").click(); await w.__tick();
    const cs = cards(d), drag = cs[0];
    let under = cs[3];
    d.elementFromPoint = function () { return under; };
    cs.forEach(function (x) { x.__rect = { left: 0, top: 0, width: 100, height: 100 }; });
    const ds = new Event("dragstart", { bubbles: true }); ds.dataTransfer = { setData: function () {}, effectAllowed: "" };
    drag.dispatchEvent(ds);
    T.check("(the drag started)", w.__dragEl === drag);
    L.clear();
    const over = function (x, y) { const e = new MouseEvent("dragover", { bubbles: true, cancelable: true, clientX: x, clientY: y }); d.getElementById("wgrid").dispatchEvent(e); return e; };
    let e = over(10, 10);
    let fl = L.of("flip", "getState");
    T.check("a drag over another card re-slots it through a Flip", fl.length === 1 && ids(d).join() === "w1,w2,w0,w3,w4,w5", [fl.length, ids(d)]);
    T.check("...that leaves the dragged card itself out", fl[0] && fl[0].targets.indexOf(drag) < 0 && fl[0].targets.length === 5, fl[0] && fl[0].targets.length);
    T.check("...on the short layout duration", L.of("flip", "from")[0].vars.duration === .32);
    T.check("...with the jiggle paused for the flight", d.getElementById("wgrid").classList.contains("flipping"));
    L.finishAll(); await w.__tick();
    T.check("...and back once it lands", !d.getElementById("wgrid").classList.contains("flipping"));
    c.clock.now += 50; under = cs[5];
    over(10, 10);
    T.check("within 140 ms nothing re-slots again (the hysteresis)", L.of("flip", "getState").length === 1 && ids(d).join() === "w1,w2,w0,w3,w4,w5", ids(d));
    c.clock.now += 100;
    L.flipping.add(cs[5]);
    over(10, 10);
    T.check("past 140 ms, still nothing while the card under the cursor is mid-flip", L.of("flip", "getState").length === 1, ids(d));
    L.flipping.clear();
    over(10, 10);
    T.check("...and once it has landed, the next re-slot Flips", L.of("flip", "getState").length === 2 && ids(d).join() === "w1,w2,w3,w4,w0,w5", ids(d));
    c.clock.now += 200; under = cs[5];
    over(10, 10);
    T.check("a drop point that would not change moves nothing", L.of("flip", "getState").length === 2, ids(d));
    drag.dispatchEvent(new Event("dragend", { bubbles: true })); await w.__tick();
    T.check("the drop saves the new order", c.st.posts.some(function (p) { return p.op === "set" && p.order.join() === "w1,w2,w3,w4,w0,w5"; }), c.st.posts);
    d.getElementById("edit-toggle").click(); await w.__tick();
    // (a stray drag while not editing: a link dragged out of a card, say)
    w.__dragEl = cs[1]; const n0 = L.of("flip", "getState").length, o0 = ids(d).join();
    over(10, 10); w.__dragEl = null;
    T.check("outside Customize a dragover re-slots nothing", L.of("flip", "getState").length === n0 && ids(d).join() === o0, ids(d));
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  await scenario("§8.3 view switch", async function () {
    const c = await boot({ order: ["w0"] });
    const v = computedValue(c.w, c.d.getElementById("view-hub"), "animation");
    T.check("the CSS .view has no entrance of its own (sequence('view') owns it)", !v || !/fade/.test(v.value), v && v.value);
  });

  // -------------------------------------------------------------------------
  await scenario("§8.1 meters on scaleX", async function () {
    const c = await boot({ order: ["system"] });
    const { w, d } = c;
    const bar = d.querySelector('.sysm[data-k="ram"]');
    // (the mini DOM keeps a parsed style="" attribute apart from el.style)
    T.check("the System card's bar is drawn as --p, not a width", bar && /^--p:0\.7$/.test(bar.getAttribute("style")) && bar.style.width === "", bar && bar.getAttribute("style"));
    const w0 = computedValue(w, bar, "width"), tf = computedValue(w, bar, "transform"), tr = computedValue(w, bar, "transition");
    T.check("...full width, scaled from the left by --p", w0 && w0.value === "100%" && tf && /scaleX\(var\(--p/.test(tf.value) &&
      computedValue(w, bar, "transform-origin").value === "left", [w0 && w0.value, tf && tf.value]);
    T.check("...and only the transform transitions", tr && /transform/.test(tr.value) && !/width/.test(tr.value), tr && tr.value);
    c.st.sys = { cpu_pct: 5, ram_pct: 30, disk_pct: 91 };
    await w.__run("liveSystem()"); await w.__tick();
    T.check("the 5 s liveSystem writes --p", bar.style.getPropertyValue("--p") === "0.3" && d.querySelector('.sysv[data-k="ram"]').textContent === "30%", bar.style.cssText);
    T.check("...while the computed width never changes", computedValue(w, bar, "width").value === "100%" && bar.style.width === "");
    const rules = parseCSS(Array.from(d.querySelectorAll("style")).map(function (s) { return s.textContent; }).join("\n"));
    const bad = rules.filter(function (r) { return /(\.meter i|\.mmmem-bar i|\.cs-ai-membar i)\b/.test(r.selector) && r.decls.some(function (x) { return /^transition/.test(x.prop) && /width/.test(x.value); }); });
    T.check("no meter (system, memory menu, Local AI panel) transitions its width", bad.length === 0, bad.map(function (r) { return r.selector; }));
    const mx = /@keyframes\s+mxS\s*\{\s*from\s*\{([^}]*)\}/.exec(Array.from(d.querySelectorAll("style")).map(function (s) { return s.textContent; }).join("\n"));
    T.check("the Mind bars' entrance is a scaleX keyframe (mxS), not a width one", !!mx && /scaleX\(0\)/.test(mx[1]) && !/@keyframes\s+mxW/.test(PAGE), mx && mx[1]);
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  await scenario("§8.6 model-swap overlay: GSAP sequences, hold() and a draw", async function () {
    const c = await boot({ order: ["w0"] });
    const { w, d, L } = c;
    drawLeavesButt(w);
    await drain(c);
    const t0 = w.__timers.length; L.clear();
    w.__run("showModelSwap('Small 9B','Switching model')");
    const o = d.getElementById("model-swap"), card = o.querySelector(".swapcard");
    const tl = L.of("gsap", "timeline");
    T.check("show is ONE sequence('swap')", tl.length === 1 && !o.hidden, tl.length);
    const steps = L.of("gsap").filter(function (x) { return /^tl\./.test(x.fn); });
    T.check("...overlay fade, card pop (hermes.pop), then the words rise",
      steps.length === 3 && steps[0].t === o && steps[1].t === card && steps[1].b.ease === "hermes.pop" && steps[2].fn === "tl.from" && steps[2].t.length === 3,
      steps.map(function (x) { return x.fn; }));
    await drain(c);
    T.check("...and it stays up, nothing left inline", !o.hidden && o.style.opacity === "");

    L.clear();
    w.__run("hideModelSwap(true,'Small 9B')");
    const ring = d.getElementById("swap-ring"), path = ring.querySelector("path");
    T.check("ready: the ring clears through a class and a check mark is drawn in (anime)", ring.classList.contains("done") && !!path &&
      L.of("anime", "svg.createDrawable").length === 1 && L.of("anime", "svg.createDrawable")[0].t[0] === path, L.of("anime").map(function (x) { return x.fn; }));
    T.check("...the title fades out first (a sequence)", L.of("gsap", "timeline").length === 1 && d.getElementById("swap-title").textContent === "Switching model");
    L.of("anime", "animate")[0].a.complete(); await w.__tick();
    T.check("...and the drawn check keeps the page's own line caps (anime's inline butt is taken off)", path.style.strokeLinecap === "", path.style.cssText);
    await drain(c, 2);
    T.check("...then reads Ready and fades back in, from the first one's done()", d.getElementById("swap-title").textContent === "Ready" && L.of("gsap", "timeline").length === 2);
    await drain(c);
    const dc = L.of("gsap", "delayedCall");
    T.check("its reading time is a hold() (gsap.delayedCall of T.hold.ready)", dc.length === 1 && dc[0].s === 1.3, dc.map(function (x) { return x.s; }));
    T.check("...the exit is a third sequence, and done() hides it", o.hidden === true && L.of("gsap", "timeline").length === 3 && !d.querySelector(".deck").hasAttribute("aria-busy"));
    T.check("...with nothing left inline on the overlay", o.style.opacity === "", o.style.cssText);

    L.clear();
    w.__run("showModelSwap('Small 9B','Switching model')"); await drain(c);
    w.__run("hideModelSwap(false,'Small 9B')");
    T.check("slow: the ring turns --warn through a class, not an inline colour", d.getElementById("swap-ring").classList.contains("warn") && d.getElementById("swap-ring").style.borderTopColor === "");
    await drain(c);
    T.check("...holds T.hold.slow, then the same exit", L.of("gsap", "delayedCall").pop().s === 2.2 && o.hidden === true);

    L.clear();
    w.__run("showModelSwap('Small 9B','Switching model')"); await drain(c);
    w.__run("failModelSwap('The switch did not start.')");
    const shake = L.of("gsap", "tl.to").pop();
    T.check("fail: the ring turns --bad (a class) and the card shakes on x", d.getElementById("swap-ring").classList.contains("bad") && shake && shake.t === o.querySelector(".swapcard") &&
      JSON.stringify(shake.a.keyframes) === '{"x":[0,-6,6,-4,4,0]}' && shake.a.duration === .42, shake && shake.a);
    await drain(c);
    T.check("...then holds T.hold.fail and goes", L.of("gsap", "delayedCall").pop().s === 2.6 && o.hidden === true);

    w.__run("showModelSwap('Small 9B','Switching model')"); await drain(c);
    w.__run("hideModelSwap(true,'Small 9B')"); await drain(c, 2);
    w.__run("showModelSwap('Big 27B','Switching model')"); await drain(c);
    T.check("a new switch mid-goodbye keeps the overlay up (the old steps are retired)", o.hidden === false && /Big 27B/.test(o.textContent), o.hidden);
    w.__run("hideModelSwap(true,'Big 27B')");                    // its title fade is running...
    w.__run("failModelSwap('The next switch did not start.')");  // ...and lands when the fail's shake starts
    T.check("a fail mid-'Ready' fade: the landed fade's done() does not write Ready over it",
      d.getElementById("swap-title").textContent === "That didn’t start", d.getElementById("swap-title").textContent);
    await drain(c);
    T.check("...and the overlay goes once, after the fail's reading time", o.hidden === true && L.of("gsap", "delayedCall").pop().s === 2.6);
    T.check("not one setTimeout was set while GSAP was there", w.__timers.length === t0, w.__timers.slice(t0).map(function (x) { return x.ms; }));
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  await scenario("§8.7 Settings Overview: count() and the sparkline draw", async function () {
    const c = await boot({ order: ["w0"] });
    const { w, d, L } = c;
    drawLeavesButt(w);
    await drain(c); L.clear();
    w.__run("setView('mind')"); await w.__tick();
    const n1 = counts(L);
    T.check("first entry: the seven figures count up (anime)", n1.length === 7, n1.length);
    T.check("...the four growth stats a little apart", n1.slice(0, 4).map(function (x) { return x.params.delay; }).join() === "0,60,120,180", n1.map(function (x) { return x.params.delay; }));
    T.check("...from 0, saying the final value meanwhile", n1[3].targets.v === 0 && d.getElementById("g-tools").getAttribute("aria-label") === "1,234");
    const dr = L.of("anime", "svg.createDrawable");
    const spark = d.getElementById("spark");
    T.check("the sparkline's line draws in", dr.length === 1 && dr[0].t[0] === spark.querySelector("path[stroke]"), dr.length);
    const area = motionOn(L, spark.querySelector('path[fill^="url"]'))[0], dot = motionOn(L, spark.querySelector("circle"))[0];
    T.check("...the area fades in after it and the end dot pops last", kfIs(area, { opacity: [0, 1] }) && area.opt.delay === .3 &&
      kfIs(dot, { opacity: [0, 1], scale: [.92, 1] }) && dot.opt.delay === .7, [area && area.opt, dot && dot.opt]);
    await drain(c);
    const txt = ["g-skills", "g-mem", "g-sessions", "g-tools", "a-msgs", "a-in", "a-out"].map(function (id) { return d.getElementById(id).textContent; });
    T.check("they land on the real figures (kfmt for the activity ones)", txt.join("|") === "12|3|40|1,234|2.5k|1.5M|800", txt);
    T.check("...and the aria-label they carried while counting is gone", !d.getElementById("g-tools").hasAttribute("aria-label"));
    T.check("the drawn sparkline keeps its round line caps (anime's inline butt is taken off)", spark.querySelector("path[stroke]").style.strokeLinecap === "", spark.querySelector("path[stroke]").style.cssText);
    L.clear();
    w.__run("setView('hub')"); w.__run("setView('mind')"); await w.__tick();
    T.check("re-entry with the same values: no count, no draw", counts(L).length === 0 && L.of("anime", "svg.createDrawable").length === 0, [counts(L).length]);
    c.st.caps.skills.total = 13;
    w.__run("setView('hub')"); w.__run("setView('mind')"); await w.__tick();
    const n3 = counts(L);
    T.check("a changed figure counts from the value shown before (12 -> 13), the rest stay still", n3.length === 1 && n3[0].targets.v === 12 && n3[0].params.v === 13, n3.map(function (x) { return [x.targets.v, x.params.v]; }));
    T.check("the growth figures are contained (a count repaints only itself)", (computedValue(w, d.getElementById("g-skills"), "contain") || {}).value === "layout paint");
    T.check("animateSpark() is gone", !/function animateSpark\(/.test(PAGE));
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  await scenario("§8.5 pop-out: Motion exit, reopen mid-exit", async function () {
    const c = await boot({ order: ["w0"] });
    const { w, d, L } = c;
    await drain(c);
    const pop = d.getElementById("wpop"), sheet = d.getElementById("wpop-sheet");
    w.__run("openPop('w0')"); await w.__tick();
    T.check("(the pop-out opens)", !pop.hidden);
    const t0 = w.__timers.length; L.clear();
    w.__run("closePop()");
    const a = motionOn(L, sheet)[0], b = motionOn(L, pop)[0];
    T.check("close: the sheet pops out and the backdrop fades (Motion)", kfIs(a, { opacity: [1, 0], scale: [1, .95] }) && kfIs(b, { opacity: [1, 0] }), [a && a.kf, b && b.kf]);
    T.check("...with no hide timer", w.__timers.length === t0, w.__timers.slice(t0).map(function (x) { return x.ms; }));
    T.check("...still shown while it leaves", !pop.hidden);
    w.__run("openPop('w0')"); await w.__tick();
    T.check("a reopen mid-exit cancels both exits", a.h.state === "cancelled" && b.h.state === "cancelled", [a.h.state, b.h.state]);
    await drain(c);
    T.check("...and stays open, nothing faded left inline", !pop.hidden && sheet.style.opacity !== "0" && pop.style.opacity !== "0", [pop.hidden, sheet.style.opacity]);
    w.__run("closePop()"); await drain(c);
    T.check("a plain close ends hidden, with nothing left inline", pop.hidden && sheet.style.opacity === "" && pop.style.opacity === "", [pop.hidden, sheet.style.cssText]);
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  await scenario("§8.5 model menu: a short Motion exit", async function () {
    const c = await boot({ order: ["w0"] });
    const { w, d, L } = c;
    await drain(c);
    const pill = d.getElementById("model-pill"), menu = d.getElementById("model-menu");
    w.__click(pill); await w.__tick();
    T.check("(the menu opens)", !menu.hidden);
    L.clear();
    d.body.click(); await w.__tick();
    const ex = motionOn(L, menu)[0];
    T.check("a click outside closes it through popOut at dur.micro", kfIs(ex, { opacity: [1, 0], scale: [1, .95] }) && ex.opt.duration === .15, ex && ex.opt);
    T.check("...shown until the exit ends", !menu.hidden);
    await drain(c);
    T.check("...then hidden, nothing left inline", menu.hidden && menu.style.opacity === "" && menu.style.transform === "");
    w.__click(pill); await w.__tick();
    L.clear();
    d.body.click(); await w.__tick();
    const ex2 = motionOn(L, menu)[0];
    w.__click(pill); await w.__tick();
    T.check("a click on the pill mid-close takes it back", ex2 && ex2.h.state === "cancelled" && !menu.hidden);
    await drain(c);
    T.check("...and it stays open", !menu.hidden);
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  // (the Hub's chat modes and their motion are gone since 2026-11-12: the
  // chat lives on the Agent page only — t_hub_no_chat.js)
  await scenario("§8.4 chat: bubbles, the approval and Claude cards, the toast", async function () {
    const c = await boot({ order: ["w0", "w1"] });
    const { w, d, L } = c;
    await drain(c); L.clear();

    // a turn: the sent bubble, the reply bubble at its first token, an approval, a Claude card
    w.hermesDeep = function (jid, bubble) { if (!bubble.querySelector(".deepcard")) { const k = d.createElement("div"); k.className = "deepcard"; bubble.appendChild(k); } };
    c.st.poll = [
      { state: "running", done: false, text: "Hel" },
      { state: "running", done: false, text: "Hello the" },
      { state: "approval", done: false, text: "Hello the", approval: { command: "rm -rf build", approval_id: "A1" } },
      { state: "approval", done: false, text: "Hello the", approval: { command: "rm -rf build", approval_id: "A1" } },
      { state: "running", done: false, text: "Hello there", deep: { state: "done", text: "deep" } },
      { state: "done", done: true, text: "Hello there", reply: "Hello there", deep: { state: "done", text: "deep" } }
    ];
    L.clear();
    d.getElementById("input").value = "hi";
    const sent = w.__run("sendMsg()");
    const user = d.querySelector("#msgs .bubble.user");
    T.check("the sent line enters as a bubble", kfIs(motionOn(L, user)[0], { opacity: [0, 1], y: [6, 0] }));
    for (let i = 0; i < 12; i++) { w.__flushTimers(1); await w.__tick(); }
    await sent;
    const bot = Array.from(d.querySelectorAll("#msgs .bubble.bot")).pop();
    const botIn = motionOn(L, bot);
    T.check("the reply bubble enters once, at its first token — streaming never animates", botIn.length === 1 && kfIs(botIn[0], { opacity: [0, 1], y: [6, 0] }), botIn.length);
    const ap = L.of("motion", "animate").filter(function (x) { return x.el && x.el.classList && x.el.classList.contains("approval"); });
    T.check("the approval card pops in once for its request", ap.length === 1 && kfIs(ap[0], { opacity: [0, 1], scale: [.92, 1] }), ap.length);
    const dk = L.of("motion", "animate").filter(function (x) { return x.el && x.el.classList && x.el.classList.contains("deepcard"); });
    T.check("the Claude card rises in once", dk.length === 1 && kfIs(dk[0], RISE), dk.length);

    L.clear();
    w.__routes["/api/history"] = { messages: [{ role: "user", text: "a" }, { role: "assistant", text: "b" }, { role: "assistant", text: "c", deep: { model: "x" } }] };
    await w.__run("loadHistory()"); await w.__tick();
    T.check("a history load animates nothing", d.querySelectorAll("#msgs .bubble").length === 3 && L.of("motion", "animate").length === 0, L.of("motion", "animate").length);

    L.clear();
    w.__run("toast('first')");
    T.check("a toast shows through its CSS transition alone", L.of("motion", "animate").length === 0 && d.getElementById("toast").classList.contains("on"));
    w.__run("toast('second')");
    const nudge = motionOn(L, d.getElementById("toast"))[0];
    T.check("...replaced while up, it nudges (a three-keyframe tween of dur.small)", kfIs(nudge, { scale: [1, 1.04, 1] }) && nudge.opt.duration === .22, nudge && nudge.opt);
    const tr = computedValue(w, d.getElementById("toast"), "translate");
    T.check("...which composes with its centring: CSS `translate`, JS `transform`", tr && /-50%/.test(tr.value) && !/translateX/.test((computedValue(w, d.getElementById("toast"), "transform") || {}).value || ""), tr && tr.value);
    await drain(c);
    T.check("...and leaves nothing inline", d.getElementById("toast").style.transform === "");
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  // -------------------------------------------------------------------------
  // the same surfaces with either Reduce-motion switch: every one at its end
  // state at once, and not one animation call reaches a library
  async function stillRun(label, o) {
    await scenario("reduced (" + label + "): every surface lands at once", async function () {
      const c = await boot(Object.assign({ order: ["w0", "w1", "w2"] }, o));
      const { w, d, L } = c;
      const anims = function () { return L.of("motion", "animate").length + L.of("anime", "animate").length + L.of("flip").length + L.of("gsap", "timeline").length; };
      T.check("(the runtime reads reduced)", c.A.reduced() === true);
      T.check("boot: no stagger, the cards simply there", cards(d).length === 3 && anims() === 0 && cards(d).every(function (x) { return x.style.opacity === ""; }), anims());
      d.getElementById("edit-toggle").click(); await w.__tick();
      const card = d.querySelector('#wgrid .w[data-id="w1"]');
      card.querySelector(".rm").click();
      T.check("remove: the card is out of sight at the click", card.style.visibility === "hidden");
      await w.__tick();
      T.check("...and the grid is rebuilt without it, no Flip", ids(d).join() === "w0,w2" && anims() === 0, ids(d));
      d.getElementById("edit-toggle").click(); await w.__tick();
      w.__run("showModelSwap('Small 9B','Switching model')");
      const o2 = d.getElementById("model-swap");
      w.__run("hideModelSwap(true,'Small 9B')");
      T.check("swap: shown and Ready at once, no sequence", !o2.hidden && d.getElementById("swap-title").textContent === "Ready" && anims() === 0);
      T.check("...the reading time still applies (reading is not motion)", o2.hidden === false && L.of("gsap", "delayedCall").length + w.__timers.filter(function (x) { return x.ms === 1300; }).length === 1);
      L.finishAll(); w.__flushTimers(1); await w.__tick();
      T.check("...then it goes", o2.hidden === true);
      w.__run("setView('mind')"); await w.__tick();
      T.check("counts: the final figures at once", d.getElementById("g-tools").textContent === "1,234" && d.getElementById("a-in").textContent === "1.5M" && anims() === 0);
      w.__run("setView('hub')");
      w.__run("openPop('w0')"); await w.__tick(); w.__run("closePop()");
      T.check("pop-out: closed at once", d.getElementById("wpop").hidden === true);
      w.__click(d.getElementById("model-pill")); await w.__tick(); d.body.click();
      T.check("model menu: closed at once", d.getElementById("model-menu").hidden === true);
      w.__run("toast('a')"); w.__run("toast('b')");
      await w.__tick();
      T.check("the toast: nothing animates, nothing left inline", anims() === 0 && d.getElementById("toast").style.transform === "", anims());
      T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
    });
  }
  await stillRun("the OS setting", { media: OS_REDUCE });
  await stillRun("the in-app switch", { reduce: true });

  // -------------------------------------------------------------------------
  await scenario("no runtime (aux_anim.js absent): ANIM_NULL, everything still works", async function () {
    const c = await boot({ runtime: false, order: ["w0", "w1", "w2"] });
    const { w, d } = c;
    T.check("the page boots without HermesAnim and HA() is ANIM_NULL", !w.HermesAnim && w.__run("HA()===ANIM_NULL") && cards(d).length === 3 && w.__errors.length === 0, w.__errors.map(String));
    d.getElementById("edit-toggle").click(); await w.__tick();
    const card = d.querySelector('#wgrid .w[data-id="w1"]');
    card.querySelector(".rm").click(); await w.__tick();
    T.check("Customize remove: the card goes and the grid is rebuilt", ids(d).join() === "w0,w2", ids(d));
    d.getElementById("edit-toggle").click(); await w.__tick();
    w.__run("showModelSwap('Small 9B','Switching model')"); w.__run("hideModelSwap(true,'Small 9B')");
    const o = d.getElementById("model-swap");
    T.check("swap: Ready at once, its reading time a setTimeout (1.3 s)", d.getElementById("swap-title").textContent === "Ready" && !o.hidden &&
      w.__timers.some(function (x) { return x.ms === 1300; }));
    w.__flushTimers(2); await w.__tick();
    T.check("...then it goes", o.hidden === true);
    w.__run("setView('mind')"); await w.__tick();
    T.check("counts: the final figures", d.getElementById("g-tools").textContent === "1,234" && d.getElementById("a-msgs").textContent === "2.5k");
    w.__run("setView('hub')");
    w.__run("openPop('w0')"); await w.__tick(); w.__run("closePop()");
    T.check("pop-out: the native fade, then hidden by its timer", !d.getElementById("wpop").hidden);
    w.__flushTimers(1); await w.__tick();
    T.check("...hidden", d.getElementById("wpop").hidden === true);
    w.__click(d.getElementById("model-pill")); await w.__tick(); d.body.click();
    T.check("model menu: closed at once", d.getElementById("model-menu").hidden === true);
    w.__run("toast('a')"); w.__run("toast('b')"); await w.__tick();
    T.check("the toast runs without the runtime", w.__errors.length === 0, w.__errors.map(String));
  });

  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
