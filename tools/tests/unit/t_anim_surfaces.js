// t_anim_surfaces.js — the Needs-you and Agent surfaces on the motion runtime
// (docs/plans/motion-system-2026-10.md 8.8, 8.10 and test plan 10.1, C4
// without onboarding).
//
// The REAL index.html (its §6.7 animate()/HA()/ANIM_NULL shim), the REAL
// aux_anim.js and the REAL aux_needsyou.js / aux_agent.js run in the mini DOM
// (_a11y_dom.js) against the recording library stubs (_anim_stubs.js), which
// schedule no timers: an animation ends only when the test ends it
// (L.finishAll()). Every /api/* answer is a local route. No dashboard, no
// network, no model.
//
//   Needs-you  an OK Done gives exit('dismiss'), an OK Snooze / re-file gives
//              exit('defer'); only once the row has gone AND the new list is
//              here does one Flip (keyed by data-flip-id) close the gap, and
//              the widget's Now count moves through HA().count. A refused or
//              failed act moves nothing and keeps the row. The hub's refresh
//              repaint never animates. Reduced motion, a hidden window or no
//              runtime land the same end state at once.
//   Agent      the ticker crossfade is exit('fadeOut') then enter('fade') with
//              no setTimeout; the sigil tick, the EKG blip and the show-in
//              head icon are spring()s, a show-in card is enter('rise'); the
//              Record rail's 3 s poll runs inside quiet().
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, harness, Event } = require("./_a11y_dom.js");
const S = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const ANIM = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");
const AGENT_SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_agent.js"), "utf8");
const NY_SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_needsyou.js"), "utf8");
const OS_REDUCE = function (q) { return /prefers-reduced-motion/.test(q); };

async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}
const J = function (x) { return JSON.stringify(x); };
function deferred() { let res; const p = new Promise(function (r) { res = r; }); return { p: p, resolve: res }; }

// a page: index.html + (optionally) the runtime on the stubs + the aux file(s)
async function page(o) {
  o = o || {};
  let L = null;
  const before = function (w) {
    if (o.runtime === false) { if (o.motion) L = S.install(w, {}); return; }
    L = S.install(w, { gsap: o.gsap !== false, anime: o.anime !== false });
    w.__run(ANIM, "aux_anim.js");
  };
  const w = await bootDashboard({ aux: o.aux, routes: o.routes, before: before, media: o.media });
  return { w: w, d: w.document, L: L };
}
// animation calls of any library, minus the bookkeeping ones
function moves(L) {
  if (!L) return [];
  return L.calls.filter(function (c) {
    return (c.lib === "motion" && c.fn === "animate") || c.lib === "flip" ||
      (c.lib === "gsap" && /^(timeline|tl\.|fromTo|to|delayedCall)/.test(c.fn)) || (c.lib === "anime" && c.fn === "animate");
  });
}
function on(L, el) { return L.of("motion", "animate").filter(function (c) { return c.el === el; }); }
function flipIds(list) { return (list || []).map(function (el) { return el.getAttribute("data-flip-id"); }); }

// ===========================================================================
// Needs-you
// ===========================================================================
const NOW = Math.floor(Date.now() / 1000);
function item(n, bucket) {
  return { id: "message:" + n, source: "message", sender: "P" + n, title: "P" + n, summary: "can you call me?",
    bucket: bucket || "now", received_at: NOW - 120, deadline_ts: 0, reason: "a question from someone you write to" };
}
function pay(ids, extra) {
  return Object.assign({ ok: true, never_count: 0, later_count: 0, metrics: {}, next_brief: null, generated_at: NOW,
    now: ids.map(function (n) { return item(n); }), today: [], later: [], sources: { message: "present" } }, extra || {});
}
function hub(p) {
  return { order: ["needsyou"], model_online: true,
    widgets: { needsyou: { meta: { title: "Needs you", size: "wide", cat: "assistant", icon: "needsyou" }, data: p } } };
}
// the routes one Needs-you page answers; st.act / st.next are swapped per step
function nyRoutes(st) {
  return {
    "/api/hub": function () { return hub(st.hub); },
    "/api/needsyou/act": function (url, init) { st.posts.push(JSON.parse(init.body || "{}")); return typeof st.act === "function" ? st.act() : st.act; },
    "/api/needsyou": function () { st.reloads++; return typeof st.next === "function" ? st.next() : st.next; },
    "/api/expand": function (url) { if (/id=needsyou/.test(url)) { st.reloads++; return typeof st.next === "function" ? st.next() : st.next; } return {}; },
    "/api/needsyou/seen": { ok: true }
  };
}
async function nyPage(o) {
  o = o || {};
  const st = { hub: o.hub || pay([1, 2, 3]), act: { ok: true }, next: pay([2, 3]), posts: [], reloads: 0 };
  const P = await page({ aux: ["aux_needsyou.js"], routes: nyRoutes(st), runtime: o.runtime, motion: o.motion, media: o.media, gsap: o.gsap, anime: o.anime });
  const card = P.d.querySelector('#wgrid .w[data-id="needsyou"]');
  P.st = st;
  P.card = card;
  P.body = card && card.querySelector(".w-body");
  P.meta = card && card.querySelector(".meta");
  P.row = function (n, host) { return (host || P.body).querySelector('.ny-row[data-id="message:' + n + '"]'); };
  P.btn = function (n, a, host, attr, val) {
    const r = P.row(n, host);
    if (!r) return null;
    return Array.prototype.find.call(r.querySelectorAll('button[data-a="' + a + '"]'), function (b) { return !attr || b.getAttribute(attr) === val; }) || null;
  };
  if (P.L) { P.L.finishAll(); await P.w.__tick(); P.L.clear(); }   // the hub's own boot entrance is not under test
  return P;
}

async function needsyou() {
  console.log("--- Needs-you (8.8) ---");

  await scenario("markup", async function () {
    const P = await nyPage();
    T.check("the hub rendered the Needs-you widget with its three Now rows", !!P.card && P.body.querySelectorAll(".ny-row").length === 3);
    const rows = Array.prototype.slice.call(P.body.querySelectorAll(".ny-row"));
    T.check("every row carries data-flip-id = its item id (what the Flip matches it by)",
      rows.every(function (r) { return r.getAttribute("data-flip-id") === r.getAttribute("data-id"); }), flipIds(rows));
    T.check("...and data-id stays right after the class (the C99.2 / B04 suites read it there)",
      /class="ny-row" data-id="message:1" data-flip-id="message:1"/.test(P.body.innerHTML));
    T.check("the Now heading and the footer glide with the rows (their own flip ids)",
      !!P.body.querySelector('.ny-grp[data-flip-id="ny-grp-now"]') && !!P.body.querySelector('.ny-foot[data-flip-id="ny-foot"]'));
    const sty = P.d.getElementById("ny-style");
    T.check("the list's scroll body may not scroll sideways: a Done slides its row 16px right, which flashed a horizontal scrollbar (measured in Chromium)",
      !!sty && /\.w-body:has\(>\.ny-wrap\)\{overflow-x:hidden\}/.test(sty.textContent));
    T.check("the meta slot reads '3 now'", P.meta.textContent === "3 now", P.meta.textContent);
    T.check("the first paint moved nothing of its own (the count only wrote its text)", P.L.calls.length === 0, P.L.calls.map(function (c) { return c.lib + "." + c.fn; }));
  });

  await scenario("Done: exit('dismiss'), then one Flip and the count", async function () {
    const P = await nyPage(), w = P.w, L = P.L;
    const r1 = P.row(1), r2 = P.row(2);
    const t0 = w.__timers.length;
    w.__click(P.btn(1, "done"));
    await w.__tick();
    T.check("Done posted the act", P.st.posts.length === 1 && P.st.posts[0].action === "done" && P.st.posts[0].id === "message:1", P.st.posts);
    const ex = on(L, r1);
    T.check("the server said ok: the row leaves through exit('dismiss') (opacity out, x 0 -> 16, .2 s)",
      ex.length === 1 && J(ex[0].kf) === '{"opacity":[1,0],"x":[0,16]}' && ex[0].opt.duration === .2, ex.map(function (c) { return [c.kf, c.opt]; }));
    T.check("...and nothing else moves yet: no Flip while the row is still leaving", L.of("flip").length === 0 && moves(L).length === 1, moves(L).map(function (c) { return c.lib + "." + c.fn; }));
    T.check("...the row is still in its place, so its gap is kept", P.body.contains(r1) && r1.style.visibility !== "hidden");
    T.check("...while the new list is already fetched", P.st.reloads === 1);
    L.finishAll();
    await w.__tick();
    T.check("once it has gone, done() hid it (visibility, so the space stays until the repaint)", r1.style.visibility === "hidden");
    const gs = L.of("flip", "getState"), fr = L.of("flip", "from");
    T.check("then ONE Flip over the list", gs.length === 1 && fr.length === 1, [gs.length, fr.length]);
    T.check("...whose before-state is the old list, keyed by data-flip-id (rows 1-3, the heading, the footer)",
      gs.length === 1 && J(flipIds(gs[0].targets)) === J(["ny-grp-now", "message:1", "message:2", "message:3", "ny-foot"]), gs.length && flipIds(gs[0].targets));
    T.check("...and whose targets are the repainted ones: row 1 gone, 2 and 3 matched by id",
      fr.length === 1 && J(flipIds(fr[0].vars.targets)) === J(["ny-grp-now", "message:2", "message:3", "ny-foot"]) &&
      fr[0].vars.targets.indexOf(r2) < 0 && fr[0].vars.targets[1] === P.row(2), fr.length && flipIds(fr[0].vars.targets));
    T.check("...over .32 s, positions only (simple, scale)", fr.length === 1 && fr[0].vars.duration === .32 && fr[0].vars.simple === true && fr[0].vars.scale === true,
      fr.length && { d: fr[0].vars.duration, s: fr[0].vars.simple });
    T.check("the Flip runs on the widget body, not inside <header>", fr.length === 1 && !P.d.querySelector("header").contains(P.body));
    T.check("the repaint has rows 2 and 3 only", !P.row(1) && !!P.row(2) && !!P.row(3) && P.body.querySelectorAll(".ny-row").length === 2);
    const cnt = L.of("anime", "animate").filter(function (c) { return c.params && c.params.v === 2; });
    T.check("the Now count moves 3 -> 2 through HA().count", cnt.length === 1 && cnt[0].targets.v === 3, cnt.map(function (c) { return c.targets; }));
    T.check("...it names the value it is going to while the digits run", P.meta.getAttribute("aria-label") === "2 now", P.meta.getAttribute("aria-label"));
    L.finishAll();
    await w.__tick();
    T.check("...and lands on '2 now', its aria-label gone", P.meta.textContent === "2 now" && P.meta.getAttribute("aria-label") == null, [P.meta.textContent, P.meta.getAttribute("aria-label")]);
    T.check("everything has ended (no running handle)", w.HermesAnim.stats().running === 0, w.HermesAnim.stats());
    T.check("the act path set no timer", w.__timers.length === t0, w.__timers.length - t0);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("ordering: the Flip waits for both the exit and the data", async function () {
    const P = await nyPage(), w = P.w, L = P.L;
    const gate = deferred();
    P.st.next = function () { return gate.p; };
    const r1 = P.row(1);
    w.__click(P.btn(1, "done"));
    await w.__tick();
    L.finishAll();
    await w.__tick();
    T.check("the row has gone and is hidden in place while the new list is not here: no Flip yet",
      r1.style.visibility === "hidden" && P.body.contains(r1) && L.of("flip").length === 0);
    gate.resolve(pay([2, 3]));
    await w.__tick();
    T.check("the list arrives: the Flip runs at once", L.of("flip", "from").length === 1 && !P.row(1));
    L.finishAll(); await w.__tick();

    // the other order: the list is here first, the row is still leaving
    const Q = await nyPage();
    const q1 = Q.row(1);
    Q.w.__click(Q.btn(1, "done"));
    await Q.w.__tick();
    T.check("the list is here while the row is still leaving: no Flip, no repaint yet", Q.st.reloads === 1 && Q.L.of("flip").length === 0 && Q.body.contains(q1));
    Q.L.finishAll(); await Q.w.__tick();
    T.check("...the Flip follows the exit", Q.L.of("flip", "from").length === 1 && !Q.row(1));
  });

  await scenario("races: a rebuild mid-POST, a cancelled exit, a refresh mid-count, keyboard focus", async function () {
    // the hub's 60 s refresh rebuilt the list while the POST was out
    const P = await nyPage(), w = P.w, L = P.L;
    const gate = deferred();
    P.st.act = function () { return gate.p; };
    const old1 = P.row(1);
    w.__click(P.btn(1, "done"));
    await w.__tick();
    w.__run("refreshHub()");
    await w.__tick();
    const new1 = P.row(1);
    T.check("(a refresh rebuilt the rows while the Done was out)", !!new1 && new1 !== old1 && !P.body.contains(old1));
    gate.resolve({ ok: true });
    await w.__tick();
    T.check("the answer comes back: the row ON SCREEN leaves, not the detached one it was clicked in",
      on(L, new1).length === 1 && on(L, old1).length === 0, [on(L, new1).length, on(L, old1).length]);
    L.finishAll(); await w.__tick();
    T.check("...and the Flip closes its gap", L.of("flip", "from").length === 1 && !P.row(1));
    L.finishAll(); await w.__tick();

    // an enter over a running exit cancels it (no done): the list must still repaint
    const Q = await nyPage();
    const q1 = Q.row(1);
    Q.w.__click(Q.btn(1, "done"));
    await Q.w.__tick();
    Q.w.HermesAnim.enter(q1, "fade");
    await Q.w.__tick();
    T.check("a cancelled exit (done never runs) does not strand the list: the Flip still repaints it",
      Q.L.of("flip", "from").length === 1 && !Q.row(1) && !!Q.row(2));
    Q.L.finishAll(); await Q.w.__tick();

    // a refresh lands while the Now count is still running
    const R = await nyPage();
    R.w.__click(R.btn(1, "done"));
    await R.w.__tick();
    R.L.finishAll(); await R.w.__tick();
    T.check("(the count 3 -> 2 is running)", R.meta.getAttribute("aria-label") === "2 now" && R.meta.textContent === "3 now");
    R.st.hub = pay([], { today: [item(9, "today")] });
    R.w.__run("refreshHub()");
    await R.w.__tick();
    T.check("a refresh to '1 today' mid-count: the words are written at once, the count's aria-label is gone",
      R.meta.textContent === "1 today" && R.meta.getAttribute("aria-label") == null, [R.meta.textContent, R.meta.getAttribute("aria-label")]);
    R.L.finishAll(); await R.w.__tick();
    T.check("...and the cancelled count never writes over them", R.meta.textContent === "1 today", R.meta.textContent);

    // C99.2 through the Flip: a keyboard Done puts focus on the row that took its place
    const K = await nyPage();
    const kb = K.btn(1, "done");
    kb.focus();
    K.w.__click(kb, { detail: 0 });
    await K.w.__tick();
    K.L.finishAll(); await K.w.__tick();
    const ae = K.d.activeElement;
    T.check("a keyboard Done: after the exit and the Flip, focus is on row 2's first button",
      !!ae && ae.getAttribute && ae.getAttribute("data-a") === "done" && ae.closest(".ny-row") === K.row(2), ae && ae.outerHTML && ae.outerHTML.slice(0, 80));
    T.check("no page error", w.__errors.length === 0 && Q.w.__errors.length === 0 && R.w.__errors.length === 0 && K.w.__errors.length === 0,
      w.__errors.concat(Q.w.__errors, R.w.__errors, K.w.__errors).map(String));
  });

  await scenario("Snooze and re-file: exit('defer')", async function () {
    const P = await nyPage(), w = P.w, L = P.L;
    w.__click(P.btn(2, "snz"));
    await w.__tick();
    T.check("opening the snooze choices is a repaint: nothing moves", moves(L).length === 0 && !!P.btn(2, "snooze", null, "data-u", "1h"));
    const r2 = P.row(2);
    P.st.next = pay([1, 3]);
    w.__click(P.btn(2, "snooze", null, "data-u", "1h"));
    await w.__tick();
    const ex = on(L, r2);
    T.check("a snooze that saved leaves through exit('defer') (opacity out, lift 6px, scale .98)",
      P.st.posts.length === 1 && P.st.posts[0].action === "snooze" && ex.length === 1 && J(ex[0].kf) === '{"opacity":[1,0],"y":[0,-6],"scale":[1,0.98]}',
      ex.map(function (c) { return c.kf; }));
    L.finishAll(); await w.__tick();
    const fr = L.of("flip", "from");
    T.check("...then the Flip closes its gap (rows 1 and 3)", fr.length === 1 && J(flipIds(fr[0].vars.targets)) === J(["ny-grp-now", "message:1", "message:3", "ny-foot"]),
      fr.length && flipIds(fr[0].vars.targets));
    L.finishAll(); await w.__tick();

    // the pop-out: a re-file from the full list
    const pop = P.d.createElement("div"); P.d.body.appendChild(pop);
    w.__pop = pop; w.__d = pay([1, 3], { today: [item(7, "today")] });
    w.__run("EXPAND_RENDER.needsyou(window.__pop, window.__d)");
    L.clear();
    const p1 = P.row(1, pop);
    P.st.next = pay([3], { today: [item(7, "today")], never_count: 1 });
    w.__click(P.btn(1, "rc", pop, "data-to", "never"));
    await w.__tick();
    const pex = on(L, p1);
    T.check("pop-out 'Never': a re-file that saved leaves through exit('defer')",
      P.st.posts[1] && P.st.posts[1].action === "reclassify" && P.st.posts[1].to === "never" && pex.length === 1 && pex[0].kf.y && pex[0].kf.y[1] === -6, P.st.posts[1]);
    L.finishAll(); await w.__tick();
    const pf = L.of("flip", "from");
    T.check("...then the pop-out's own list re-flows (the Today group glides up with it)",
      pf.length === 1 && J(flipIds(pf[0].vars.targets)) === J(["ny-grp-now", "message:3", "ny-grp-today", "message:7", "ny-foot", "ny-src"]),
      pf.length && flipIds(pf[0].vars.targets));
    T.check("...and the pop-out has no count to move (it has no meta slot)", L.of("anime", "animate").length === 0);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("an error answer moves nothing", async function () {
    const P = await nyPage(), w = P.w, L = P.L;
    P.st.act = { ok: false, error: "store write failed" };
    w.__click(P.btn(1, "done"));
    await w.__tick();
    const r1 = P.row(1);
    T.check("a refused Done (ok:false): no exit, no Flip, no count, no reload", moves(L).length === 0 && P.st.reloads === 0,
      moves(L).map(function (c) { return c.lib + "." + c.fn; }));
    T.check("...the row stays, visible, with the server's message", !!r1 && r1.style.visibility !== "hidden" && r1.textContent.indexOf("store write failed") >= 0);
    T.check("...and its Done is live again", P.btn(1, "done") && P.btn(1, "done").disabled !== true);
    T.check("...the count did not change", P.meta.textContent === "3 now");

    P.st.act = { __reject: "offline" };
    w.__click(P.btn(2, "done"));
    await w.__tick();
    T.check("a Done that never reached the server: nothing moves either, the row stays with a message",
      moves(L).length === 0 && !!P.row(2) && P.row(2).textContent.indexOf("That did not save") >= 0);

    // the act saved, but the new list never comes
    P.st.act = { ok: true };
    P.st.next = { __reject: "offline" };
    const r3 = P.row(3);
    w.__click(P.btn(3, "done"));
    await w.__tick();
    L.finishAll(); await w.__tick();
    T.check("saved, but the new list never arrives: once the row has left it comes back as it was, no Flip",
      on(L, r3).length === 1 && L.of("flip").length === 0 && P.body.contains(r3) && r3.style.visibility === "", r3.style.visibility);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("the hub's refresh repaint never animates", async function () {
    const P = await nyPage(), w = P.w, L = P.L;
    w.__run("refreshHub()");
    await w.__tick();
    T.check("an unchanged refresh: zero animation calls", moves(L).length === 0 && P.meta.textContent === "3 now", moves(L).map(function (c) { return c.lib + "." + c.fn; }));
    P.st.hub = pay([1, 2, 3, 4]);
    w.__run("refreshHub()");
    await w.__tick();
    T.check("a refresh with a new item: the count reads '4 now' at once and nothing moves",
      P.meta.textContent === "4 now" && moves(L).length === 0 && P.body.querySelectorAll(".ny-row").length === 4, [P.meta.textContent, moves(L).length]);
    P.st.hub = pay([], { today: [item(9, "today")] });
    w.__run("refreshHub()");
    await w.__tick();
    T.check("...and from 'N now' to '1 today' it simply writes the words", P.meta.textContent === "1 today" && moves(L).length === 0, P.meta.textContent);
  });

  async function instantDone(P, label) {
    const w = P.w, L = P.L;
    w.__click(P.btn(1, "done"));
    await w.__tick();
    T.check(label + ": Done lands at once — row gone, '2 now'", !P.row(1) && !!P.row(2) && P.meta.textContent === "2 now", [!!P.row(1), P.meta.textContent]);
    T.check(label + ": no Motion, GSAP or anime call at all", L ? moves(L).length === 0 : true, L && moves(L).map(function (c) { return c.lib + "." + c.fn; }));
    T.check(label + ": no page error", w.__errors.length === 0, w.__errors.map(String));
  }
  await scenario("reduced motion, hidden, no runtime", async function () {
    const A = await nyPage();
    A.d.documentElement.setAttribute("data-reduce", "1");
    await A.w.__tick();
    await instantDone(A, "in-app Reduce motion");
    T.check("in-app Reduce motion: the runtime counted the act as reduced", A.w.HermesAnim.stats().instant.reduced >= 2, A.w.HermesAnim.stats().instant);
    await instantDone(await nyPage({ media: OS_REDUCE }), "the macOS Reduce motion setting");
    const H = await nyPage();
    H.d.hidden = true; H.d.visibilityState = "hidden"; H.d.dispatchEvent(new Event("visibilitychange"));
    await instantDone(H, "a hidden window");
    const N = await nyPage({ runtime: false, motion: true });
    T.check("without aux_anim the page's HA() is ANIM_NULL", N.w.__run("HA()!==window.HermesAnim && HA().v===0"));
    await instantDone(N, "no runtime (ANIM_NULL)");
    T.check("without the runtime GSAP and anime never loaded", !N.w.gsap && !N.w.anime);
  });

  await scenario("headless: no HA(), no HermesAnim", async function () {
    // the file on its own (the vm harness pattern of t_c99_2 / t_b04), with a real mini-DOM host
    const { createWindow } = require("./_a11y_dom.js");
    const w = createWindow({});
    w.RENDER = {}; w.EXPAND_RENDER = {}; w.WICONS = {};
    const st = { act: { ok: true }, next: pay([2]) , posts: [], reloads: 0 };
    w.__routes = nyRoutes(st);
    w.__run(NY_SRC, "aux_needsyou.js");
    const host = w.document.createElement("div"), slot = w.document.createElement("span");
    w.document.body.appendChild(host);
    w.RENDER.needsyou(host, pay([1, 2]), slot);
    T.check("it renders with no motion runtime at all", host.querySelectorAll(".ny-row").length === 2 && slot.textContent === "2 now");
    w.__click(host.querySelector('.ny-row[data-id="message:1"] button[data-a="done"]'));
    await w.__tick();
    T.check("Done still repaints: the instant fallback ran the exit's done and the Flip's mutate",
      host.querySelectorAll(".ny-row").length === 1 && !!host.querySelector('.ny-row[data-id="message:2"]') && slot.textContent === "1 now", [host.querySelectorAll(".ny-row").length, slot.textContent]);
    T.check("no error", w.__errors.length === 0, w.__errors.map(String));
  });

  T.check("aux_needsyou.js never calls Motion, GSAP or anime directly (only through HA())",
    !/window\.(Motion|gsap|anime|Flip)\b/.test(NY_SRC) && !/\banimate\(/.test(NY_SRC));
}

// ===========================================================================
// Agent
// ===========================================================================
function agentRoutes(st) {
  return {
    "/api/agent/pulse": function () { return { ok: true, facts: st.facts.slice(), last_action: { id: st.recId }, events: [] }; },
    "/api/claude/bridge": { ok: true, recent: [] },
    "/api/claude/recent": { ok: true, calls: [] }
  };
}
async function agentPage(o) {
  o = o || {};
  const st = { facts: o.facts || ["On watch — nothing needs you right now", "Second fact"], recId: 1, rec: [] };
  const P = await page({ aux: ["aux_agent.js"], routes: agentRoutes(st), runtime: o.runtime, motion: o.motion, media: o.media });
  const w = P.w;
  // a stand-in Flight Recorder that reports how it was called
  w.loadRecorder = function () {
    const A = w.HermesAnim;
    const probe = P.d.createElement("div"); P.d.body.appendChild(probe);
    const h = A ? A.enter(probe, "fade") : null;
    st.rec.push({ live: A ? A.live() : null, done: h ? h.done : null });
    return Promise.resolve();
  };
  w.__run("setView('agent')");
  await w.__tick();
  if (P.L) { P.L.finishAll(); await w.__tick(); P.L.clear(); }
  P.st = st;
  P.A = w.__agentTest;
  return P;
}

async function agent() {
  console.log("--- Agent page (8.10) ---");
  const css = (AGENT_SRC.match(/"#agent-ticker \.tk-txt\{[^}]*\}"/) || [""])[0];
  T.check("the ticker's CSS no longer transitions opacity (the runtime owns it now)", css && css.indexOf("transition") < 0, css);
  T.check("the sigil ring keeps the fill-box pivot (transform-box:fill-box;transform-origin:50% 50%)",
    /"\.sig-ring\{transform-box:fill-box;transform-origin:50% 50%\}"/.test(AGENT_SRC));

  await scenario("the ticker crossfade", async function () {
    const P = await agentPage(), w = P.w, L = P.L, d = P.d;
    const el = d.getElementById("ag-tick");
    T.check("the Agent page is up with its ticker", !!el && !d.getElementById("view-agent").hidden && el.textContent === "On watch — nothing needs you right now");
    const t0 = w.__timers.length;
    P.A.crossfade();
    T.check("the crossfade sets no setTimeout", w.__timers.length === t0, w.__timers.slice(t0).map(function (t) { return t.ms; }));
    const out = on(L, el);
    T.check("it fades the line out with exit('fadeOut') (.15 s)", out.length === 1 && J(out[0].kf) === '{"opacity":[1,0]}' && out[0].opt.duration === .15, out.map(function (c) { return [c.kf, c.opt]; }));
    T.check("...and keeps the old words until the line has gone", el.textContent === "On watch — nothing needs you right now");
    L.finishAll();
    await w.__tick();
    const all = on(L, el);
    T.check("once it has gone: the new words, then enter('fade') (.22 s)", el.textContent === "Second fact" && all.length === 2 && J(all[1].kf) === '{"opacity":[0,1]}' && all[1].opt.duration === .22,
      [el.textContent, all.map(function (c) { return c.kf; })]);
    T.check("...still no timer", w.__timers.length === t0);
    L.finishAll();
    await w.__tick();
    T.check("the line ends fully visible, under CSS control (no inline opacity left)", el.style.opacity === "" && el.style.transform === "", el.style.cssText);
    T.check("nothing is left running", w.HermesAnim.stats().running === 0);
    P.A.crossfade(); L.finishAll(); await w.__tick(); L.finishAll(); await w.__tick();
    T.check("the next tick crossfades back the same way", el.textContent === "On watch — nothing needs you right now" && el.style.opacity === "" && on(L, el).length === 4);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));

    const Q = await agentPage({ facts: ["On watch — nothing needs you right now"] });
    Q.A.crossfade();
    await Q.w.__tick();
    T.check("one fact: the 8 s tick changes nothing, so nothing moves", moves(Q.L).length === 0);
  });

  await scenario("sigil, EKG and show-in cards", async function () {
    const P = await agentPage(), w = P.w, L = P.L, d = P.d;
    const ring = d.querySelector("#agent-hero .sig-ring");
    P.A.driveHero("using terminal");
    let sp = on(L, ring);
    T.check("a tool event ticks the sigil ring 0 -> 12 on HA().spring's snappy spring",
      sp.length === 1 && J(sp[0].kf) === '{"rotate":[0,12]}' && sp[0].opt.type === "spring" && sp[0].opt.visualDuration === .22 && sp[0].opt.bounce === .15,
      sp.map(function (c) { return [c.kf, c.opt]; }));
    L.finishAll(); await w.__tick();
    T.check("...and the angle stays (the ticks add up)", ring.style.transform === "rotate(12deg)", ring.style.transform);
    P.A.driveHero("using terminal");
    sp = on(L, ring);
    T.check("the next tick goes 12 -> 24", sp.length === 2 && J(sp[1].kf) === '{"rotate":[12,24]}');
    L.finishAll(); await w.__tick();

    P.A.showinDispatch("using terminal ls -la");
    const card = d.querySelector("#msgs .showin");
    const ic = card && card.querySelector(".showin-head .ic");
    const ce = on(L, card), ie = on(L, ic);
    T.check("a show-in card rises in through enter('rise')", !!card && ce.length === 1 && J(ce[0].kf) === '{"opacity":[0,1],"y":[10,0]}', ce.map(function (c) { return c.kf; }));
    T.check("...and its head icon turns once on the gel spring", ie.length === 1 && J(ie[0].kf) === '{"rotate":[0,360]}' && ie[0].opt.visualDuration === .34 && ie[0].opt.bounce === .3,
      ie.map(function (c) { return [c.kf, c.opt]; }));
    L.finishAll(); await w.__tick();
    T.check("...the card ends under CSS control", card.style.opacity === "" && card.style.transform === "", card.style.cssText);

    const dot = d.getElementById("ag-ekg");
    P.st.recId = 2;
    await P.A.loadPulse();
    const be = on(L, dot);
    T.check("a new recorder event blips the EKG: scale 1 -> 1.5 -> 1, a .22 s tween (three keyframes)",
      be.length === 1 && J(be[0].kf) === '{"scale":[1,1.5,1]}' && be[0].opt.duration === .22 && !be[0].opt.type, be.map(function (c) { return [c.kf, c.opt]; }));
    await P.A.loadPulse();
    T.check("...the 10 s poll with no new event does not", on(L, dot).length === 1);
    L.finishAll(); await w.__tick();
    const header = d.querySelector("header");
    T.check("nothing animated sits inside <header>", L.of("motion", "animate").every(function (c) { return !header.contains(c.el); }));
    T.check("aux_agent never calls Motion directly for these (only through HA())", !/agAnimate\((ring|card\.el|ic|d),/.test(AGENT_SRC));
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("the Record rail poll runs inside quiet()", async function () {
    const P = await agentPage(), w = P.w;
    T.check("opening the rail (the user's own act) is not quiet", P.st.rec.length >= 1 && P.st.rec[0].live === true, P.st.rec);
    const iv = w.__intervals.find(function (t) { return t.ms === 3000 && String(t.fn).indexOf("loadRecorder") >= 0; });
    T.check("the Record rail polls every 3 s", !!iv);
    const q0 = w.HermesAnim.stats().instant.quiet;
    const n = P.st.rec.length;
    iv.fn();
    const r = P.st.rec[n];
    T.check("the poll calls loadRecorder inside quiet(): live() is false there", P.st.rec.length === n + 1 && r.live === false, r);
    T.check("...so an entrance it starts lands at once", r.done === true && w.HermesAnim.stats().instant.quiet === q0 + 1, w.HermesAnim.stats().instant);
    T.check("...and quiet ends with the call", w.HermesAnim.live() === true);
  });

  await scenario("reduced motion, no runtime", async function () {
    const P = await agentPage({ media: OS_REDUCE }), w = P.w, L = P.L, d = P.d;
    const el = d.getElementById("ag-tick"), ring = d.querySelector("#agent-hero .sig-ring");
    const t0 = w.__timers.length;
    P.A.crossfade();
    T.check("Reduce motion: the crossfade swaps the words at once", el.textContent === "Second fact" && w.__timers.length === t0);
    P.A.driveHero("using terminal");
    P.A.showinDispatch("using terminal pwd");
    P.st.recId = 5; await P.A.loadPulse();
    await w.__tick();
    T.check("Reduce motion: no Motion call for the ticker, the sigil, the card or the EKG", moves(L).length === 0, moves(L).map(function (c) { return c.el && c.el.className; }));
    T.check("...the sigil still lands on its end angle", ring.style.transform === "rotate(12deg)", ring.style.transform);
    T.check("...and the card is simply there", !!d.querySelector("#msgs .showin") && d.querySelector("#msgs .showin").style.opacity === "");

    const N = await agentPage({ runtime: false, motion: true });
    const el2 = N.d.getElementById("ag-tick"), t1 = N.w.__timers.length;
    N.A.crossfade();
    await N.w.__tick();
    T.check("no runtime (ANIM_NULL): the crossfade swaps the words with no timer", el2.textContent === "Second fact" && N.w.__timers.length === t1 && el2.style.opacity === "");
    N.A.driveHero("using terminal");
    T.check("...and the sigil tick lands through the page's animate() at once", N.d.querySelector("#agent-hero .sig-ring").style.transform !== "" &&
      N.L.of("motion", "animate").some(function (c) { return c.opt && c.opt.duration === 0; }));
    T.check("no page error", w.__errors.length === 0 && N.w.__errors.length === 0, w.__errors.concat(N.w.__errors).map(String));
  });
}

(async function main() {
  console.log("C4 surfaces: Needs-you and the Agent page on the motion runtime");
  await needsyou();
  await agent();
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
