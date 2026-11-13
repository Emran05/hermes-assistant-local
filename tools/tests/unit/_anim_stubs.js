// _anim_stubs.js — recording stand-ins for Motion 14, GSAP 3 (+ Flip,
// CustomEase) and anime.js 4, for the motion-runtime suites (t_anim_*).
//
// Not a suite (run.sh skips files that start with "_"). The real GSAP and
// anime do not run faithfully in the mini DOM (docs/plans/motion-system-
// 2026-10.md E20: GSAP's CSSPlugin does not recognise mini-DOM elements and an
// anime complete() writes no style), so the unit tier drives aux_anim.js
// against these and the optional browser tier runs the real libraries.
//
// Rules the stubs keep, because the runtime's own contract depends on them:
//   * NOTHING here schedules a timer or a frame. A test ends an animation
//     itself: h.complete()/h.cancel() (Motion), tl.progress(1) (GSAP),
//     a.complete() (anime), or L.finishAll() for everything still running.
//   * every call is recorded in L.calls as {lib, fn, ...} in call order;
//     L.of(lib, fn) filters them.
//   * a Motion animation that ends naturally writes its LAST keyframe inline
//     (what Motion's WAAPI onfinish does), so a suite can prove the runtime
//     puts the element back under CSS control afterwards.
"use strict";

function listOf(t) {
  if (!t) return [];
  if (t.nodeType === 1) return [t];
  if (Array.isArray(t)) return t.slice();
  const out = [];
  if (typeof t.length === "number") for (let i = 0; i < t.length; i++) out.push(t[i]);
  return out;
}
function lastOf(a) { return Array.isArray(a) ? a[a.length - 1] : a; }

function makeLog() {
  const L = {
    calls: [], live: [], flipping: new Set(), eases: {},
    of(lib, fn) { return L.calls.filter(function (c) { return c.lib === lib && (fn == null || c.fn === fn); }); },
    clear() { L.calls.length = 0; },
    // end everything still running, the way each library ends on its own
    finishAll() {
      for (let round = 0; round < 10 && L.live.length; round++) {
        const batch = L.live.splice(0);
        batch.forEach(function (h) { try { h.__natural(); } catch (e) { L.calls.push({ lib: "stub", fn: "error", e: e }); } });
      }
    }
  };
  return L;
}
function track(L, h) { L.live.push(h); return h; }
function untrack(L, h) { const i = L.live.indexOf(h); if (i >= 0) L.live.splice(i, 1); }

// --------------------------------------------------------------------------
// Motion 14 (window.Motion)
// --------------------------------------------------------------------------
function installMotion(w, L, opts) {
  opts = opts || {};
  const M = {
    MotionGlobalConfig: {},
    animate(el, kf, opt) {
      let res, state = "running";
      const h = {
        el: el, kf: kf, opt: opt, log: [],
        finished: new Promise(function (r) { res = r; }),
        get state() { return state; },
        complete() { h.log.push("complete"); L.calls.push({ lib: "motion", fn: "complete", h: h }); h.__natural(); },
        cancel() { h.log.push("cancel"); L.calls.push({ lib: "motion", fn: "cancel", h: h }); if (state === "running") state = "cancelled"; untrack(L, h); },
        stop() { h.log.push("stop"); if (state === "running") state = "stopped"; untrack(L, h); },
        then(a, b) { return h.finished.then(a, b); },
        // the library ends it on its own: commit the last keyframe inline
        __natural() {
          if (state !== "running") return;
          state = "finished"; untrack(L, h);
          if (el && el.style && kf && typeof kf === "object" && !Array.isArray(kf)) {
            if (kf.opacity != null) el.style.opacity = String(lastOf(kf.opacity));
            if (kf.transform != null) el.style.transform = String(lastOf(kf.transform));
            else if (kf.x != null || kf.y != null || kf.scale != null || kf.rotate != null) el.style.transform = "motion-final";
          }
          res();
        }
      };
      L.calls.push({ lib: "motion", fn: "animate", el: el, kf: kf, opt: opt, h: h });
      if (opt && (opt.duration === 0 || M.MotionGlobalConfig.skipAnimations)) h.__natural();
      else track(L, h);
      return h;
    },
    stagger(step, o) { const f = function (i) { return ((o && o.startDelay) || 0) + i * step; }; f.__stagger = step; return f; },
    releaseElement(el) { L.calls.push({ lib: "motion", fn: "releaseElement", el: el }); }
  };
  if (opts.legacy) { delete M.MotionGlobalConfig; delete M.releaseElement; }
  w.Motion = M;
  return M;
}

// --------------------------------------------------------------------------
// GSAP 3 (window.gsap, window.Flip, window.CustomEase)
// --------------------------------------------------------------------------
function installGsap(w, L) {
  let ctxStack = [];
  function tweenDur(v) {
    if (!v) return 0;
    let d = v.duration != null ? v.duration : 0.5;
    const n = v.__n || 1;
    if (v.stagger && typeof v.stagger === "object" && v.stagger.each) d += v.stagger.each * (n - 1);
    else if (typeof v.stagger === "number") d += v.stagger * (n - 1);
    return d;
  }
  function makeTimeline(vars, kind) {
    let prog = 0;
    const tl = {
      kind: kind || "timeline", vars: vars || {}, tweens: [], cbs: {}, played: false, completed: false,
      killed: false, reverted: false,
      __add(type, t, a, b, pos) {
        const v = type === "fromTo" ? b : a;
        if (v && typeof v === "object") v.__n = listOf(t).length || 1;
        tl.tweens.push({ type: type, t: t, from: type === "fromTo" ? a : (type === "from" ? a : null), v: v, pos: pos });
        L.calls.push({ lib: "gsap", fn: "tl." + type, tl: tl, t: t, a: a, b: b, pos: pos });
        return tl;
      },
      from(t, v, pos) { return tl.__add("from", t, v, null, pos); },
      fromTo(t, a, b, pos) { return tl.__add("fromTo", t, a, b, pos); },
      to(t, v, pos) { return tl.__add("to", t, v, null, pos); },
      set(t, v, pos) { return tl.__add("set", t, v, null, pos); },
      call(fn, args, pos) { tl.tweens.push({ type: "call", fn: fn, args: args, pos: pos }); return tl; },
      addLabel() { return tl; },
      duration() {
        let end = 0, cursor = 0;
        tl.tweens.forEach(function (tw) {
          if (tw.type === "call") return;
          const at = typeof tw.pos === "number" ? tw.pos : cursor;
          const e = at + (tw.type === "set" ? 0 : tweenDur(tw.v));
          cursor = e; if (e > end) end = e;
        });
        return tl.vars.duration != null ? tl.vars.duration : end;
      },
      play() { tl.played = true; L.calls.push({ lib: "gsap", fn: "play", tl: tl }); if (!tl.completed && !tl.killed) track(L, tl); return tl; },
      progress(p) {
        if (p === undefined) return prog;
        prog = p;
        L.calls.push({ lib: "gsap", fn: "progress", tl: tl, p: p });
        if (p >= 1 && !tl.completed && !tl.killed) {
          tl.completed = true; untrack(L, tl);
          // like real GSAP 3.11+: a callback of an animation made inside a context runs
          // INSIDE that context, so whatever it creates (a timeline, a nested context, a
          // delayedCall) belongs to it and goes when the context is reverted
          if (tl.__ctx) ctxStack.push(tl.__ctx);
          try {
            tl.tweens.forEach(function (tw) { if (tw.type === "call") tw.fn.apply(null, tw.args || []); });
            const cb = tl.cbs.onComplete || tl.vars.onComplete;
            if (cb) cb();
          } finally { if (tl.__ctx) ctxStack.pop(); }
        }
        return tl;
      },
      eventCallback(type, fn) { if (fn === undefined) return tl.cbs[type]; tl.cbs[type] = fn; return tl; },
      kill() { tl.killed = true; untrack(L, tl); L.calls.push({ lib: "gsap", fn: "kill", tl: tl }); return tl; },
      revert() { tl.reverted = true; tl.killed = true; untrack(L, tl); L.calls.push({ lib: "gsap", fn: "revert", tl: tl }); return tl; },
      isActive() { return tl.played && !tl.completed && !tl.killed; },
      __natural() { tl.progress(1); }
    };
    const ctx = ctxStack[ctxStack.length - 1];
    if (ctx) { ctx.items.push(tl); tl.__ctx = ctx; }
    return tl;
  }
  const gsap = {
    version: "stub",
    registerPlugin() { L.calls.push({ lib: "gsap", fn: "registerPlugin", args: Array.prototype.slice.call(arguments) }); },
    defaults(v) { L.calls.push({ lib: "gsap", fn: "defaults", v: v }); },
    config(v) { L.calls.push({ lib: "gsap", fn: "config", v: v }); },
    ticker: { lagSmoothing(a, b) { L.calls.push({ lib: "gsap", fn: "lagSmoothing", a: a, b: b }); } },
    timeline(vars) { const tl = makeTimeline(vars); L.calls.push({ lib: "gsap", fn: "timeline", tl: tl, vars: vars }); return tl; },
    context(fn) {
      const ctx = {
        items: [], reverted: false, killed: false,
        revert() { ctx.reverted = true; L.calls.push({ lib: "gsap", fn: "ctx.revert", ctx: ctx }); ctx.items.forEach(function (t) { if (t.revert) t.revert(); }); },
        kill() { ctx.killed = true; L.calls.push({ lib: "gsap", fn: "ctx.kill", ctx: ctx }); ctx.items.forEach(function (t) { if (t.kill) t.kill(); }); },
        add(f) { ctxStack.push(ctx); try { f(); } finally { ctxStack.pop(); } }
      };
      L.calls.push({ lib: "gsap", fn: "context", ctx: ctx });
      const parent = ctxStack[ctxStack.length - 1];
      if (parent) { parent.items.push(ctx); ctx.parent = parent; }      // real GSAP: a context made inside one is its child
      ctxStack.push(ctx);
      try { if (fn) fn(); } finally { ctxStack.pop(); }
      return ctx;
    },
    delayedCall(s, fn) {
      const dc = { s: s, fn: fn, killed: false, fired: false,
        kill() { dc.killed = true; untrack(L, dc); L.calls.push({ lib: "gsap", fn: "dc.kill", dc: dc }); },
        __natural() {
          if (dc.killed || dc.fired) return; dc.fired = true; untrack(L, dc);
          if (dc.__ctx) ctxStack.push(dc.__ctx);
          try { fn(); } finally { if (dc.__ctx) ctxStack.pop(); }
        } };
      L.calls.push({ lib: "gsap", fn: "delayedCall", dc: dc, s: s });
      track(L, dc);
      const owner = ctxStack[ctxStack.length - 1];
      if (owner) { owner.items.push(dc); dc.revert = dc.kill; dc.__ctx = owner; }   // a context reverts its delayedCalls too
      return dc;
    },
    set(t, v) { L.calls.push({ lib: "gsap", fn: "set", t: t, v: v }); },
    fromTo(t, a, b) { const tw = makeTimeline(b, "tween"); tw.tweens.push({ type: "fromTo", t: t, from: a, v: b }); L.calls.push({ lib: "gsap", fn: "fromTo", t: t, a: a, b: b, tw: tw }); return tw; },
    to(t, v) { const tw = makeTimeline(v, "tween"); L.calls.push({ lib: "gsap", fn: "to", t: t, v: v, tw: tw }); return tw; }
  };
  const Flip = {
    getState(targets) { const st = { targets: listOf(targets) }; L.calls.push({ lib: "flip", fn: "getState", targets: st.targets, state: st }); return st; },
    from(state, vars) {
      const tl = makeTimeline(vars, "flip");
      L.calls.push({ lib: "flip", fn: "from", state: state, vars: vars, tl: tl });
      tl.played = true; track(L, tl);
      return tl;
    },
    isFlipping(el) { return L.flipping.has(el); }
  };
  const CustomEase = {
    create(name, data) { L.eases[name] = data; L.calls.push({ lib: "customease", fn: "create", name: name, data: data }); return function (t) { return t; }; }
  };
  w.gsap = gsap; w.Flip = Flip; w.CustomEase = CustomEase;
  return gsap;
}

// --------------------------------------------------------------------------
// anime.js 4 (window.anime)
// --------------------------------------------------------------------------
function installAnime(w, L, opts) {
  opts = opts || {};
  // like the real createDrawable (verified against anime 4.5.0 in a browser): it
  // writes its attributes onto the path AT ONCE and starts it "0 0" (blank);
  // the animation's own revert() returns to THAT state, not to the page's
  function drawState(el, a, b) {
    el.setAttribute("pathLength", "1000"); el.setAttribute("stroke-dashoffset", "0");
    el.setAttribute("stroke-dasharray", a); el.setAttribute("draw", b);
  }
  function drawable(t) {
    return listOf(t).map(function (el) { drawState(el, "0 1010", "0 0"); return { el: el, draw: "0 0" }; });
  }
  const anime = {
    engine: { pauseOnDocumentHidden: opts.pauseOnHidden === undefined ? true : opts.pauseOnHidden },
    animate(targets, params) {
      let state = "running";
      const a = {
        targets: targets, params: params, log: [],
        get state() { return state; },
        complete() { a.log.push("complete"); L.calls.push({ lib: "anime", fn: "complete", a: a }); a.__natural(); },
        cancel() { a.log.push("cancel"); L.calls.push({ lib: "anime", fn: "cancel", a: a }); if (state === "running") state = "cancelled"; untrack(L, a); },
        revert() {
          a.log.push("revert"); L.calls.push({ lib: "anime", fn: "revert", a: a }); if (state === "running") state = "reverted"; untrack(L, a);
          if (params && params.draw && Array.isArray(targets)) targets.forEach(function (d) { if (d && d.el) drawState(d.el, "0 1010", "0 0"); });
        },
        pause() { a.log.push("pause"); },
        // count(): drive the plain object through one update to its target
        step(v) { if (targets && typeof targets === "object" && !targets.nodeType && !Array.isArray(targets)) targets.v = v; if (params.onUpdate) params.onUpdate(a); },
        __natural() {
          if (state !== "running") return;
          state = "finished"; untrack(L, a);
          if (params && params.v !== undefined) a.step(params.v);
          if (params && params.draw && Array.isArray(targets)) targets.forEach(function (d) { if (d && d.el) drawState(d.el, "1000 0", "0 1"); });
          if (params && params.onComplete) params.onComplete(a);
        }
      };
      L.calls.push({ lib: "anime", fn: "animate", targets: targets, params: params, a: a });
      track(L, a);
      return a;
    },
    createDrawable(t) { L.calls.push({ lib: "anime", fn: "createDrawable", t: t }); return drawable(t); },
    svg: { createDrawable(t) { L.calls.push({ lib: "anime", fn: "svg.createDrawable", t: t }); return drawable(t); } },
    splitText(el, o) {
      const doc = el.ownerDocument;
      const words = String(el.textContent || "").split(/\s+/).filter(Boolean).map(function (t) { const s = doc.createElement("span"); s.textContent = t; return s; });
      const split = { el: el, opts: o, words: words, reverted: false,
        revert() { split.reverted = true; L.calls.push({ lib: "anime", fn: "split.revert", split: split }); return split; } };
      L.calls.push({ lib: "anime", fn: "splitText", el: el, opts: o, split: split });
      return split;
    },
    cubicBezier(a, b, c, d) { const f = function (t) { return t; }; f.__bez = [a, b, c, d]; return f; },
    stagger(v, o) { const f = function (el, i) { return ((o && o.start) || 0) + i * v; }; f.__stagger = [v, o]; return f; }
  };
  w.anime = anime;
  return anime;
}

// install(w, {motion, gsap, anime, motionLegacy}) -> L (motion defaults on)
function install(w, which) {
  which = which || {};
  const L = makeLog();
  if (which.motion !== false) installMotion(w, L, { legacy: !!which.motionLegacy });
  if (which.gsap) installGsap(w, L);
  if (which.anime) installAnime(w, L, which.animeOpts);
  w.__animLog = L;
  return L;
}

module.exports = { install, installMotion, installGsap, installAnime, makeLog, listOf };
