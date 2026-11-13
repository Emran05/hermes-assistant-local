/* aux_anim.js — the dashboard's motion runtime, window.HermesAnim (v 1).
 *
 * Spec: docs/plans/motion-system-2026-10.md §3 (roles), §6 (this module) and §9
 * (budget). Loaded right after the Motion bundle and BEFORE the inline shell.
 *
 *   ROLES  Motion (loaded at startup) runs one element or a flat list entering,
 *          leaving or nudged. GSAP core + Flip + CustomEase run layout FLIPs and
 *          multi-track sequences. anime.js runs values, SVG draw-ins and word
 *          reveals. CSS keeps every infinite loop, hover/focus/press and the
 *          popover entrances. GSAP and anime are injected from /vendor/... on
 *          first need or by one idle prefetch, and NEVER under reduced motion.
 *   CALLS  synchronous; never wait for a library; never throw. Without the
 *          library, or when !live(), a call takes the INSTANT path: the final
 *          state at once (an exit's done() runs before the call returns).
 *   RULES  live() = !reduced() && !document.hidden && outside quiet(), where
 *          reduced() = :root[data-reduce] OR the OS query, read on every call.
 *          Going hidden settles everything (no catch-up) and nothing here sets
 *          a timer or rAF while hidden. Only transform and opacity animate.
 *          GSAP/anime never touch the app <header> (aux_window re-measures on
 *          every mutation there). Every animation dispatches exactly one
 *          hermes:animstart and one hermes:animend (bubbling, detail {ms,kind})
 *          so aux_glass's pointer light re-measures a surface a library moves.
 *   No interval timer, no HTML-string writes, no index.html globals. ES5 IIFE.
 */
(function () {
  "use strict";
  if (typeof window === "undefined" || typeof document === "undefined") return;
  var W = window, D = document, HTML = D.documentElement;
  if (W.__auxAnim) return;
  W.__auxAnim = true;

  // A version bump edits these URLs, server.py's VENDOR_JS and the vendor
  // MANIFEST.json together; t_anim_runtime.js fails until all three agree.
  var MOTION_V = "14.6.0";
  var LIBS = {
    gsap: { files: ["/vendor/gsap/gsap-3.15.0.min.js", "/vendor/gsap/Flip-3.15.0.min.js",
                    "/vendor/gsap/CustomEase-3.15.0.min.js"],
            ok: function () { return !!(W.gsap && W.Flip && W.CustomEase); }, init: initGsap },
    anime: { files: ["/vendor/anime/anime-4.5.0.umd.min.js"],
             ok: function () { return !!(W.anime && W.anime.animate); }, init: initAnime }
  };
  LIBS.gsap.state = LIBS.anime.state = "idle";

  function deepFreeze(o) { for (var k in o) if (o[k] && typeof o[k] === "object") deepFreeze(o[k]); return Object.freeze(o); }
  // §6.4 — the one source; index.html's :root mirrors the curves and durations
  // (t_anim_tokens.js locks the two together). Seconds.
  var T = deepFreeze({
    ease: { out: [.22, 1, .36, 1], gel: [.34, 1.56, .64, 1], pop: [.22, 1.28, .42, 1],
            press: [.2, 0, .4, 1], expo: [.16, 1, .3, 1] },
    dur: { press: .09, micro: .15, small: .22, pop: .32, view: .36, layout: .42,
           surface: .45, rise: .46, count: .8, draw: .9 },
    spring: { snappy: { type: "spring", visualDuration: .22, bounce: .15 },
              gel: { type: "spring", visualDuration: .34, bounce: .3 },
              soft: { type: "spring", visualDuration: .4, bounce: .12 } },
    stagger: { step: .035, max: 12, total: .4 },     // at most 12 animate; the tail lands at once
    hold: { ready: 1.3, slow: 2.2, fail: 2.6 }        // swap-overlay reading time, NOT motion
  });
  var GFALL = { out: "power3.out", gel: "back.out(1.6)", pop: "back.out(1.2)", press: "power2.inOut", expo: "expo.out" };

  // ---- small helpers -------------------------------------------------------
  function noop() {}
  function own(o) { var r = {}, k; for (k in o) r[k] = Array.isArray(o[k]) ? o[k].slice() : o[k]; return r; }
  function list(t) {
    if (!t) return [];
    if (t.nodeType === 1) return [t];
    if (typeof t === "string") { try { t = D.querySelectorAll(t); } catch (e) { return []; } }
    var out = [];
    try { for (var i = 0; i < t.length; i++) if (t[i] && t[i].nodeType === 1 && out.indexOf(t[i]) < 0) out.push(t[i]); } catch (e) {}
    return out;
  }
  var warned = {};
  function warn(k, msg) { if (warned[k]) return; warned[k] = 1; try { console.warn("HermesAnim: " + msg); } catch (e) {} }
  function call(fn, a) { if (typeof fn !== "function") return; try { fn(a); } catch (e) { warn("cb", "a callback threw: " + e); } }
  function common(els) {
    var a = els[0], i;
    for (i = 1; a && i < els.length; i++) while (a && !a.contains(els[i])) a = a.parentNode;
    return a && a.nodeType === 1 ? a : HTML;
  }
  var hdr = null;
  function inHeader(els) {
    if (!hdr || !hdr.isConnected) hdr = D.querySelector("header");
    if (!hdr) return false;
    for (var i = 0; i < els.length; i++) if (els[i] === hdr || hdr.contains(els[i])) return true;
    return false;
  }

  // ---- §6.2 switches -------------------------------------------------------
  var MQ = null;
  try { MQ = W.matchMedia ? W.matchMedia("(prefers-reduced-motion: reduce)") : null; } catch (e) {}
  // the in-app switch, from the first script (aux_prefs sets the same later)
  try { if (W.localStorage.getItem("hermes_reduce") === "1" && !HTML.hasAttribute("data-reduce")) HTML.setAttribute("data-reduce", "1"); } catch (e) {}
  function reduced() { try { return HTML.hasAttribute("data-reduce") || !!(MQ && MQ.matches); } catch (e) { return false; } }
  var quietDepth = 0;
  function why() { return reduced() ? "reduced" : D.hidden ? "hidden" : quietDepth > 0 ? "quiet" : null; }
  function live() { return !why(); }
  var SEEN = Object.create(null);
  function once(k) { k = String(k); if (SEEN[k]) return false; SEEN[k] = 1; return true; }

  // ---- handles -------------------------------------------------------------
  // rec = {kind, evt, ms, hold?, stop(fin), after(fin, natural)}: one per animation
  var ST = { started: 0, instant: { reduced: 0, hidden: 0, quiet: 0, nolib: 0, header: 0, key: 0, cap: 0 } };
  var RUN = [];
  function doneH() { return { done: true, finished: Promise.resolve(), cancel: noop, finish: noop }; }
  function instant(reason, fn) { if (reason) ST.instant[reason]++; call(fn); return doneH(); }
  function fire(type, el, rec) {
    if (!el || !el.dispatchEvent || !W.CustomEvent) return;
    try { el.dispatchEvent(new W.CustomEvent(type, { bubbles: true, detail: { ms: Math.round(rec.ms || 0), kind: rec.kind } })); } catch (e) {}
  }
  function start(rec) {
    var res;
    rec.state = "run";
    rec.h = { done: false, finished: new Promise(function (r) { res = r; }),
              cancel: function () { end(rec, false); }, finish: function () { end(rec, true); } };
    rec.res = res;
    RUN.push(rec);
    if (!rec.hold) { ST.started++; fire("hermes:animstart", rec.evt, rec); }
    return rec.h;
  }
  // fin: finished (done runs) vs cancelled (done never runs); natural: the
  // library already ended it, so there is nothing to stop
  function end(rec, fin, natural) {
    if (rec.state !== "run") return;
    rec.state = fin ? "finished" : "cancelled";
    var i = RUN.indexOf(rec); if (i >= 0) RUN.splice(i, 1);
    if (!natural) { try { rec.stop(fin); } catch (e) {} }
    try { rec.after(fin, !!natural); } catch (e) { warn("after", "cleanup threw: " + e); }
    rec.h.done = true;
    if (!rec.hold) fire("hermes:animend", rec.evt, rec);
    rec.res();
  }
  function settle(holds) { RUN.slice().forEach(function (r) { if (holds || !r.hold) end(r, true); }); }
  // a new enter/exit/spring on an element ends what this runtime runs there:
  // an enter over an exit is a reopen (cancel: no done), anything else finishes
  function interrupt(els, entering) {
    els.forEach(function (el) { var r = el.__haRec; if (r && r.state === "run") end(r, !(entering && r.kind === "exit")); });
  }

  // ---- library loader (§6.3) -----------------------------------------------
  function ready(name) {
    var L = LIBS[name];
    if (!L) return false;
    if (L.state === "idle" && L.ok()) { try { L.init(); } catch (e) {} L.state = "ready"; }
    return L.state === "ready";
  }
  function load(name) {
    var L = LIBS[name];
    if (!L) return Promise.resolve(false);
    if (ready(name)) return Promise.resolve(true);
    if (L.state === "failed") return Promise.resolve(false);
    if (L.p) return L.p;
    if (reduced()) return Promise.resolve(false);      // nothing would animate: never pay for it
    L.state = "loading";
    L.p = new Promise(function (resolve) {
      var left = L.files.length, over = false;
      function fail(msg) {
        if (over) return; over = true; L.state = "failed";        // one attempt per page life
        warn("load:" + name, name + " did not load (" + msg + "); its calls take the instant path");
        resolve(false);
      }
      L.files.forEach(function (src) {
        var s = D.createElement("script");
        s.async = false;                                   // dynamic scripts run in insertion order
        s.setAttribute("src", src);
        s.setAttribute("data-hermes-anim", name);
        s.addEventListener("load", function () {
          if (over || --left > 0) return;
          if (!L.ok()) return fail("globals missing");
          over = true;
          try { L.init(); } catch (e) { warn("init:" + name, "init failed: " + e); }
          L.state = "ready"; resolve(true);
        });
        s.addEventListener("error", function () { fail("error " + src); });
        (D.head || HTML).appendChild(s);
      });
    });
    return L.p;
  }
  function kick(name) { if (!reduced()) load(name); }
  var customOK = false;
  function initGsap() {
    var G = W.gsap, k, ok = true;
    try { G.registerPlugin(W.Flip, W.CustomEase); } catch (e) {}
    for (k in T.ease) { try { W.CustomEase.create("hermes." + k, T.ease[k].join(",")); } catch (e) { ok = false; } }
    customOK = ok;
    try { G.defaults({ ease: gEase("out"), duration: T.dur.small, overwrite: "auto" }); } catch (e) {}
    try { G.config({ nullTargetWarn: false, autoSleep: 60 }); } catch (e) {}
    try { G.ticker.lagSmoothing(500, 33); } catch (e) {}
  }
  function initAnime() {
    var E = W.anime.engine;
    if (E && E.pauseOnDocumentHidden !== true) { try { E.pauseOnDocumentHidden = true; } catch (e) {} }
  }
  function gEase(n) {
    if (typeof n !== "string") return n;
    var k = n.indexOf("hermes.") === 0 ? n.slice(7) : n;
    return T.ease[k] ? (customOK ? "hermes." + k : GFALL[k]) : n;
  }
  function aEase(n) {
    var e = T.ease[n] || T.ease.out, A = W.anime;
    return A && A.cubicBezier ? A.cubicBezier(e[0], e[1], e[2], e[3]) : undefined;
  }

  // ---- Motion: enter / exit / stagger / spring (§6.5 presets) --------------
  var PRE = {
    rise:   { kf: { opacity: [0, 1], y: [10, 0] }, t: { duration: T.dur.rise, ease: T.ease.out } },
    fade:   { kf: { opacity: [0, 1] }, t: { duration: T.dur.small, ease: T.ease.out } },
    bubble: { kf: { opacity: [0, 1], y: [6, 0] }, t: { duration: T.dur.small, ease: T.ease.out } },
    pop:    { kf: { opacity: [0, 1], scale: [.92, 1] }, per: { scale: T.spring.gel } },
    slideL: { kf: { opacity: [0, 1], x: [-18, 0] }, per: { x: T.spring.soft } },
    slideR: { kf: { opacity: [0, 1], x: [24, 0] }, per: { x: T.spring.soft } },
    fab:    { kf: { opacity: [0, 1], scale: [.5, 1] }, per: { scale: T.spring.gel } },
    toast:  { kf: { opacity: [0, 1], y: [8, 0] }, per: { y: T.spring.snappy } },
    popOut:    { exit: 1, kf: { opacity: [1, 0], scale: [1, .95] }, t: { duration: .18, ease: T.ease.press } },
    fadeOut:   { exit: 1, kf: { opacity: [1, 0] }, t: { duration: T.dur.micro, ease: T.ease.press } },
    dismiss:   { exit: 1, kf: { opacity: [1, 0], x: [0, 16] }, t: { duration: .2, ease: T.ease.press } },
    defer:     { exit: 1, kf: { opacity: [1, 0], y: [0, -6], scale: [1, .98] }, t: { duration: .2, ease: T.ease.press } },
    shrinkOut: { exit: 1, kf: { opacity: [1, 0], scale: [1, .86] }, t: { duration: T.dur.small, ease: T.ease.press } }
  };
  var TF = { x: "px", y: "px", scale: "", rotate: "deg" };
  function motionOK() { return !!(W.Motion && typeof W.Motion.animate === "function"); }
  function release(el) { try { if (W.Motion && W.Motion.releaseElement) W.Motion.releaseElement(el); } catch (e) {} }
  function u(v, unit) { return typeof v === "number" ? v + unit : String(v); }
  // the inline opacity/transform of a keyframe map at its first (0) or last (-1) frame
  function frameAt(kf, idx) {
    var out = {}, tf = "", k, a, v;
    for (k in kf) {
      a = kf[k]; v = idx < 0 ? a[a.length - 1] : a[0];
      if (k === "opacity") out.opacity = String(v);
      else if (k === "transform") out.transform = String(v);
    }
    if (kf.x || kf.y) tf += "translate(" + u(kf.x ? frameV(kf.x, idx) : 0, "px") + ", " + u(kf.y ? frameV(kf.y, idx) : 0, "px") + ") ";
    if (kf.scale) tf += "scale(" + frameV(kf.scale, idx) + ") ";
    if (kf.rotate) tf += "rotate(" + u(frameV(kf.rotate, idx), "deg") + ")";
    if (tf) out.transform = tf.trim();
    return out;
  }
  function frameV(a, idx) { return idx < 0 ? a[a.length - 1] : a[0]; }
  function paint(el, f) { try { if (f.opacity != null) el.style.opacity = f.opacity; if (f.transform != null) el.style.transform = f.transform; } catch (e) {} }
  function kfCopy(kf) { var o = {}, k; for (k in kf) o[k] = kf[k].slice(); return o; }
  function mopts(P, delay, dur) {
    var o, k;
    if (P.per) {
      o = { opacity: { duration: T.dur.small, ease: T.ease.out.slice(), delay: delay } };
      for (k in P.per) { o[k] = own(P.per[k]); o[k].delay = delay; }
    } else { o = own(P.t); if (dur != null && isFinite(dur)) o.duration = +dur; }
    o.delay = delay;
    return o;
  }
  function pdur(P) { if (P.t) return P.t.duration; var m = T.dur.small, k; for (k in P.per) m = Math.max(m, P.per[k].visualDuration * 1.6); return m; }
  // one Motion call per element (numeric delays: a per-value transition cannot
  // take a stagger function), one handle for the lot. persist: the end values
  // stay (spring); otherwise the element is handed back to CSS at the end
  function mrun(kind, els, kf, optAt, ms, done, prePaint, persist) {
    var M = W.Motion, ctrls = [], last = frameAt(kf, -1), first = prePaint ? frameAt(kf, 0) : null;
    var rec = { kind: kind, evt: els.length === 1 ? els[0] : common(els), ms: ms };
    var prev = els.map(function (el) { return [el.style.opacity, el.style.transform]; });
    rec.stop = function () { ctrls.forEach(function (c) { try { c.cancel(); } catch (e) {} }); };   // never a late commit
    rec.after = function (fin) {
      els.forEach(function (el, i) {
        if (el.__haRec === rec) el.__haRec = null;
        if (fin && persist) paint(el, last);
        else { try { el.style.opacity = prev[i][0]; el.style.transform = prev[i][1]; } catch (e) {} }
        release(el);                 // also drops Motion's pending render, so nothing lands after this
      });
      // done() runs after the clean-up (same task, so no frame in between):
      // an enter it starts on the same element (a fade-in after a fade-out)
      // begins from the page's own state, and nothing here undoes it
      if (fin) call(done);
    };
    els.forEach(function (el, i) {
      if (first) paint(el, first);   // the from-state now: no frame of the end state first
      try { var c = M.animate(el, kfCopy(kf), optAt(i)); if (c) ctrls.push(c); } catch (e) { warn("m", "Motion.animate threw: " + e); }
      el.__haRec = rec;
    });
    if (!ctrls.length) { rec.after(true); return instant("nolib"); }
    var h = start(rec), fin = function () { end(rec, true, true); };
    Promise.all(ctrls.map(function (c) { return c.finished; })).then(fin, fin);
    return h;
  }
  // enter / exit / stagger: a §6.5 preset on an element or a flat list
  function presetRun(kind, target, preset, done, opts) {
    opts = opts || {};
    var els = list(target), n = els.length, P = PRE[preset], isExit = kind === "exit";
    var seen = !isExit && opts.key != null && !once(opts.key);   // consumed even when quiet: once per page life
    var r = why() || (seen ? "key" : null);
    if (!r && (!P || !!P.exit !== isExit)) { warn("p:" + preset, "unknown " + (isExit ? "exit" : "enter") + " preset " + preset); r = "nolib"; }
    if (!r && !motionOK()) r = "nolib";
    interrupt(els, !isExit);
    if (r || !n) return instant(r, done);
    var base = +opts.delay || 0, step = 0, d = opts.dur != null && isFinite(opts.dur) ? +opts.dur : null;
    if (kind === "stagger") {
      if (n > T.stagger.max) ST.instant.cap++;               // the tail is already at its CSS state
      els = els.slice(0, T.stagger.max);
      step = Math.min(opts.step != null && isFinite(opts.step) ? +opts.step : T.stagger.step, T.stagger.total / n);
    }
    var delays = els.map(function (el, i) { return base + i * step; });
    return mrun(kind, els, P.kf, function (i) { return mopts(P, delays[i], d); },
      1000 * (delays[delays.length - 1] + (d != null ? d : pdur(P))), done, !isExit, false);
  }
  function enter(target, preset, opts) { return presetRun("enter", target, preset, null, opts); }
  function exit(target, preset, done, opts) { return presetRun("exit", target, preset, done, opts); }
  function stagger(targets, preset, opts) { return presetRun("stagger", targets, preset, null, opts); }
  // a one-off value change: the end values PERSIST (unlike enter/exit)
  function spring(target, values, name, opts) {
    opts = opts || {};
    var els = list(target), kf = {}, multi = false, k, a, r = why();
    for (k in values || {}) {
      if (k !== "opacity" && k !== "transform" && !HAS.call(TF, k)) { warn("sp:" + k, "spring() animates transform and opacity only; dropped " + k); continue; }
      a = Array.isArray(values[k]) ? values[k].slice() : [values[k]];
      if (a.length > 2) multi = true;
      kf[k] = a;
    }
    if (!r && !motionOK()) r = "nolib";
    interrupt(els, false);
    if (r || !els.length) { var last = frameAt(kf, -1); els.forEach(function (el) { paint(el, last); release(el); }); return instant(r); }
    // Motion springs take two keyframes; three or more are a tween
    var tr = multi ? { duration: T.dur.small, ease: T.ease.out.slice() } : own(T.spring[name] || T.spring.snappy);
    tr.delay = +opts.delay || 0;
    return mrun("spring", els, kf, function () { return own(tr); },
      1000 * (tr.delay + (tr.duration || tr.visualDuration * 1.6)), null, false, true);
  }

  // ---- §6.7 legacy animate() ------------------------------------------------
  var ENAME = { "ease-out": "easeOut", "ease-in": "easeIn", "ease-in-out": "easeInOut", "linear": "linear" };
  function isNum(x) { return typeof x === "number" && isFinite(x); }
  function cssEase(s) {
    if (Array.isArray(s)) return s.length === 4 && s.every(isNum) ? s.slice() : undefined;
    if (typeof s !== "string") return undefined;
    s = s.trim();
    var m = /^cubic-bezier\(([^)]*)\)$/i.exec(s);
    if (m) { var a = m[1].split(",").map(Number); return a.length === 4 && a.every(isNum) ? a : undefined; }
    if (s === "ease") return [.25, .1, .25, 1];
    return HAS.call(ENAME, s) ? ENAME[s] : undefined;      // "constructor" is not an easing
  }
  function legacyT(o, isLive, top) {
    var k, e;
    if ("easing" in o) { if (!("ease" in o)) { e = cssEase(o.easing); if (e !== undefined) o.ease = e; } delete o.easing; }
    if ("iterations" in o) {
      if (!("repeat" in o)) o.repeat = o.iterations === Infinity ? Infinity : Math.max(0, (+o.iterations || 1) - 1);
      delete o.iterations;
    }
    delete o.fill; delete o.direction;
    if (!isLive) { o.duration = 0; o.delay = 0; o.repeat = 0; }
    if (top) for (k in o) if (o[k] && typeof o[k] === "object" && !Array.isArray(o[k])) o[k] = legacyT(own(o[k]), isLive, false);
    return o;
  }
  function legacy(el, kf, opt) {
    var isLive = live(), o = {}, k, res;
    if (!isLive) ST.instant[why()]++;
    if (typeof opt === "number") o.duration = opt / 1000;
    else if (opt && typeof opt === "object") for (k in opt) o[k] = opt[k];
    var css = typeof o.easing === "string" ? o.easing : null;
    o = legacyT(o, isLive, true);
    var M = W.Motion;                       // per call: aux_prefs' reduced-motion wrapper stays in the path
    try {
      if (M && typeof M.animate === "function") res = M.animate(el, kf, o);
      else if (el && typeof el.animate === "function") {
        res = el.animate(kf, { duration: 1000 * (o.duration != null ? o.duration : .3), delay: 1000 * (o.delay || 0),
          easing: css || "ease", iterations: (o.repeat || 0) + 1, fill: opt && opt.fill });
      }
    } catch (e) { return undefined; }
    if (isLive && res && res.finished && el && el.nodeType === 1) {
      var rec = { kind: "legacy", ms: 1000 * ((o.delay || 0) + (o.duration != null ? o.duration : .3)) };
      fire("hermes:animstart", el, rec);
      var endEv = function () { fire("hermes:animend", el, rec); };
      try { res.finished.then(endEv, endEv); } catch (e) {}
    }
    return res;
  }

  // ---- GSAP: flip / sequence / hold -----------------------------------------
  var GKEYS = { opacity: 1, x: 1, y: 1, xPercent: 1, yPercent: 1, scale: 1, scaleX: 1, scaleY: 1, rotate: 1, rotation: 1,
    duration: 1, delay: 1, ease: 1, stagger: 1, clearProps: 1, immediateRender: 1, overwrite: 1, repeat: 1,
    yoyo: 1, repeatDelay: 1, onStart: 1, onComplete: 1, onUpdate: 1, id: 1, keyframes: 1, easeEach: 1 };
  var HAS = Object.prototype.hasOwnProperty;
  function gframes(f) { return Array.isArray(f) ? f.map(gvars) : gvars(f); }
  function gvars(v) {
    if (!v || typeof v !== "object") return v;
    var o = {}, k;
    for (k in v) {
      if (!HAS.call(GKEYS, k)) { warn("g:" + k, "GSAP may animate transform and opacity only; dropped " + k); continue; }
      // keyframes carry their own property maps: the same allowlist, frame by frame
      o[k] = k === "ease" || k === "easeEach" ? gEase(v[k]) : k === "keyframes" ? gframes(v[k]) : v[k];
    }
    return o;
  }
  // build(tl, T) gets this, not the timeline: it sees every target (header
  // refusal, event target) and gives from-tweens clearProps
  function proxy(tl, seen, bad) {
    function ok(t) {
      var els = list(t);
      if (inHeader(els)) { bad.v = true; return false; }
      els.forEach(function (el) { if (seen.indexOf(el) < 0) seen.push(el); });
      return true;
    }
    function fromV(v) { v = gvars(v) || {}; if (v.clearProps == null) v.clearProps = "opacity,transform"; return v; }
    var p = {
      from: function (t, v, pos) { if (ok(t)) tl.from(t, fromV(v), pos); return p; },
      fromTo: function (t, a, b, pos) { if (ok(t)) tl.fromTo(t, gvars(a), fromV(b), pos); return p; },
      to: function (t, v, pos) { if (ok(t)) tl.to(t, gvars(v), pos); return p; },
      set: function (t, v, pos) { if (ok(t)) tl.set(t, gvars(v), pos); return p; },
      call: function (fn, args, pos) { tl.call(fn, args, pos); return p; },
      addLabel: function (l, pos) { tl.addLabel(l, pos); return p; },
      duration: function () { return tl.duration(); }
    };
    return p;
  }
  var SEQ = Object.create(null);
  function sequence(name, build, opts) {
    opts = opts || {};
    name = String(name);
    if (SEQ[name]) end(SEQ[name], true);              // one live sequence per name: the old one lands
    function fallback(r) { if (r) ST.instant[r]++; call(opts.instant, T); call(opts.done); return doneH(); }
    var r = why();
    if (!r && !ready("gsap")) { kick("gsap"); r = "nolib"; }
    if (r) return fallback(r);
    var G = W.gsap, tl = null, ctx = null, seen = [], bad = { v: false };
    try {
      ctx = G.context(function () {
        tl = G.timeline({ paused: true, defaults: { ease: gEase("out") } });
        build(proxy(tl, seen, bad), T);
      });
    } catch (e) {
      warn("seq:" + name, "sequence " + name + " failed: " + e);
      try { ctx && ctx.revert(); } catch (x) {}
      return fallback("nolib");
    }
    if (bad.v) { try { ctx.revert(); } catch (x) {} warn("hdr", "no JS library animates inside <header>"); return fallback("header"); }
    var dur = 0; try { dur = tl.duration(); } catch (e) {}
    var rec = { kind: "sequence", evt: seen.length ? common(seen) : null, ms: dur * 1000 };
    rec.stop = function (fin) { if (fin) tl.progress(1); else ctx.revert(); };
    rec.after = function (fin, natural) {
      if (SEQ[name] === rec) delete SEQ[name];
      if (!fin) return;
      var clean = function () { try { ctx.revert(); } catch (e) {} try { tl.kill(); } catch (e) {} };
      // A natural end is reported from inside GSAP's own onComplete, and GSAP's
      // context owns everything made inside its animations' callbacks: a
      // sequence or a hold() that done() started there became a child of this
      // context, and the revert below took it with it (verified against GSAP
      // 3.15: a chained same-name sequence never ran, a hold never fired).
      // So leave GSAP's stack first, then clear what the context wrote, then
      // done(), which now starts from the element's own CSS state. A forced
      // end (a newer sequence, settle) is already outside GSAP: done, then clean.
      if (natural) Promise.resolve().then(function () { clean(); call(opts.done); });
      else { call(opts.done); clean(); }
    };
    try { tl.eventCallback("onComplete", function () { end(rec, true, true); }); } catch (e) {}
    SEQ[name] = rec;
    var h = start(rec);
    // nothing with a length (only set/call steps): land it now rather than
    // wait on a completion an empty timeline may never report
    if (!(dur > 0)) { end(rec, true); return h; }
    try { tl.play(); } catch (e) { end(rec, true); }
    return h;
  }
  // a widget's own [data-flip-id] rows inside a flipped card would move twice
  // (with the card, then on their own) and eat into the cap: outermost only
  function outer(sc, els) {
    return els.filter(function (el) {
      var p = el.parentNode && el.parentNode.closest ? el.parentNode.closest("[data-flip-id]") : null;
      return !p || p === sc || !sc.contains(p);
    });
  }
  function flip(scope, mutate, opts) {
    opts = opts || {};
    var ran = false, run = function () { if (!ran) { ran = true; call(mutate); } };
    var r = why(), sc = scope && scope.nodeType === 1 ? scope : null, max = opts.max || 24;
    var pick = function () {
      var t = opts.targets ? list(opts.targets) : (sc ? outer(sc, list(sc.querySelectorAll("[data-flip-id]"))) : []);
      return opts.exclude ? t.filter(function (el) { return el !== opts.exclude; }) : t;
    };
    if (!r && (!sc || inHeader([sc]))) { if (sc) warn("hdr", "no JS library animates inside <header>"); r = sc ? "header" : "nolib"; }
    if (!r && !ready("gsap")) { kick("gsap"); r = "nolib"; }
    var before = r ? [] : pick();
    // the scope may sit outside the header while opts.targets reach into it
    if (!r && inHeader(before)) { warn("hdr", "no JS library animates inside <header>"); r = "header"; }
    if (!r && before.length > max) r = "cap";
    if (r) return instant(r, run);
    var G = W.gsap, F = W.Flip, state, ctx = null, tl = null;
    try { state = F.getState(before); } catch (e) { return instant("nolib", run); }
    run();
    var rec = { kind: "flip", evt: sc, ms: 1000 * (opts.duration != null ? +opts.duration : T.dur.layout) };
    rec.stop = function (fin) { if (fin) tl.progress(1); else ctx.revert(); };
    rec.after = function (fin) { if (fin) { try { tl.kill(); } catch (e) {} } };   // Flip restores its own props
    var h = start(rec);
    try {
      ctx = G.context(function () {
        tl = F.from(state, {
          targets: pick(), duration: rec.ms / 1000, ease: gEase(opts.ease || "out"),
          simple: true, scale: true,           // a size change is a scale, never width/height
          toggleClass: opts.lite ? "anim-lite" : undefined,   // §9: solid glass for the flight, if the probe asks
          onEnter: opts.enter ? function (els) {
            return G.fromTo(els, { opacity: 0, scale: .9 }, { opacity: 1, scale: 1, duration: .38, ease: gEase("pop"), clearProps: "opacity,transform" });
          } : undefined,
          onComplete: function () { end(rec, true, true); }
        });
      });
      if (!tl) end(rec, true);
    } catch (e) { warn("flip", "Flip failed: " + e); end(rec, true); }
    return h;
  }
  function isFlipping(el) { try { return ready("gsap") && !!W.Flip.isFlipping(el); } catch (e) { return false; } }
  // reading time, not motion: it applies under reduced motion too, and fires
  // at once while (or as soon as) the page is hidden
  function hold(s, fn) {
    if (D.hidden) return instant("hidden", fn);
    var rec = { kind: "hold", hold: true }, id = 0, dc = null;
    rec.stop = function () { try { if (dc) dc.kill(); } catch (e) {} if (id) { clearTimeout(id); id = 0; } };
    rec.after = function (fin) { if (fin) call(fn); };
    var h = start(rec), fire1 = function () { id = 0; end(rec, true, true); };
    s = Math.max(0, +s || 0);
    if (ready("gsap")) { try { dc = W.gsap.delayedCall(s, fire1); } catch (e) { dc = null; } }
    if (!dc) id = setTimeout(fire1, s * 1000);
    return h;
  }

  // ---- anime: count / draw / words -----------------------------------------
  function kindRunning(k) { var n = 0; RUN.forEach(function (r) { if (r.kind === k) n++; }); return n; }
  function fmtOf(opts) {
    var f = typeof opts.fmt === "function" ? opts.fmt : function (n) { return Math.round(n).toLocaleString(); };
    return function (n) { try { return String(f(n)); } catch (e) { return String(n); } };
  }
  function count(el, to, opts) {
    opts = opts || {};
    if (!el || el.nodeType !== 1) return doneH();
    to = +to; if (!isFinite(to)) to = 0;
    var fmt = fmtOf(opts), fin = fmt(to), cur = el.__haRec;
    var write = function (s) { if (el.textContent !== s) el.textContent = s; };
    if (cur && cur.kind === "count" && cur.state === "run") { if (cur.to === to) return cur.h; end(cur, false); }
    var from = opts.from != null && isFinite(opts.from) ? +opts.from : (el.__haVal != null ? el.__haVal : 0);
    var r = why();
    if (!r && inHeader([el])) { warn("hdr", "no JS library animates inside <header>"); r = "header"; }
    if (!r && from === to) { write(fin); el.__haVal = to; return doneH(); }   // nothing changed: nothing moves
    if (!r && !ready("anime")) { kick("anime"); r = "nolib"; }
    if (!r && kindRunning("count") >= 8) r = "cap";
    if (r) { write(fin); el.__haVal = to; return instant(r); }
    var A = W.anime, obj = { v: from }, last = null, anim = null;
    var aria = el.getAttribute("aria-label"), minW = el.style.minWidth;
    var rec = { kind: "count", evt: el, to: to, ms: 1000 * ((+opts.delay || 0) + (opts.dur != null ? +opts.dur : T.dur.count)) };
    rec.stop = function (f) { if (anim) { if (f) anim.complete(); else anim.cancel(); } };
    rec.after = function (f) {
      if (el.__haRec === rec) el.__haRec = null;
      if (f) { write(fin); el.__haVal = to; }
      if (aria == null) el.removeAttribute("aria-label"); else el.setAttribute("aria-label", aria);
      el.style.minWidth = minW;
    };
    el.setAttribute("aria-label", fin);            // the value it is going to, while the digits run
    el.style.minWidth = fin.length + "ch";         // set once: no width change per frame
    el.__haRec = rec;
    var h = start(rec);
    try {
      anim = A.animate(obj, { v: to, duration: rec.ms - 1000 * (+opts.delay || 0), delay: 1000 * (+opts.delay || 0),
        ease: aEase(opts.ease || "expo"),
        onUpdate: function () { el.__haVal = obj.v; var s = fmt(obj.v); if (s !== last) { last = s; write(s); } },
        onComplete: function () { end(rec, true, true); } });
    } catch (e) { end(rec, true); }
    return h;
  }
  // createDrawable() writes these attributes onto the path AT ONCE, and its
  // initial state is "0 0": an invisible path. anime's revert() puts back that
  // initial state, not the page's, so reverting after a finished (or cancelled,
  // or failed) draw left the path blank for good. The page's own attributes are
  // snapshotted instead and restored, which is the fully drawn default.
  var DRAW_ATTRS = ["pathLength", "stroke-dasharray", "stroke-dashoffset", "draw"];
  function draw(paths, opts) {
    opts = opts || {};
    var els = list(paths), r = why();
    if (!r && inHeader(els)) { warn("hdr", "no JS library animates inside <header>"); r = "header"; }
    if (!r && els.length + kindRunning("draw") > 2) r = "cap";
    if (!r && !ready("anime")) { kick("anime"); r = "nolib"; }
    if (r || !els.length) return instant(r);         // fully drawn is the default: nothing to touch
    var A = W.anime, anim = null;
    var snap = els.map(function (el) { return DRAW_ATTRS.map(function (a) { return el.getAttribute(a); }); });
    var caps = els.map(function (el) { return el.style ? el.style.strokeLinecap : ""; });   // anime's revert leaves an inline butt cap
    var rec = { kind: "draw", evt: els.length === 1 ? els[0] : common(els), ms: 1000 * ((+opts.delay || 0) + (opts.dur != null ? +opts.dur : T.dur.draw)) };
    rec.stop = function (f) { if (anim && f) anim.complete(); };
    rec.after = function () {
      if (anim) { try { anim.revert(); } catch (e) {} }
      els.forEach(function (el, i) {                 // whatever the library left: back to the page's own (fully drawn)
        DRAW_ATTRS.forEach(function (a, j) {
          try { if (snap[i][j] == null) el.removeAttribute(a); else el.setAttribute(a, snap[i][j]); } catch (e) {}
        });
        try { el.style.strokeLinecap = caps[i]; } catch (e) {}
      });
    };
    var h = start(rec);
    try {
      var mk = A.svg && A.svg.createDrawable ? A.svg.createDrawable : A.createDrawable;
      anim = A.animate(mk(els), { draw: ["0 0", "0 1"], duration: rec.ms - 1000 * (+opts.delay || 0),
        delay: 1000 * (+opts.delay || 0), ease: aEase("out"), onComplete: function () { end(rec, true, true); } });
    } catch (e) { end(rec, true); }
    return h;
  }
  function words(el, opts) {
    opts = opts || {};
    if (!el || el.nodeType !== 1) return doneH();
    var r = why();
    if (!r && inHeader([el])) { warn("hdr", "no JS library animates inside <header>"); r = "header"; }
    if (!r && el.children && el.children.length) { warn("words", "words() takes plain-text headings only"); r = "cap"; }
    if (!r && !ready("anime")) { kick("anime"); r = "nolib"; }
    if (r) return instant(r);
    var A = W.anime, split = null, anim = null, dl = 1000 * (+opts.delay || 0);
    var rec = { kind: "words", evt: el, ms: dl + 2000 * T.dur.small + 300 };
    rec.stop = function (f) { if (anim) { if (f) anim.complete(); else anim.cancel(); } };
    rec.after = function () { if (split) { try { split.revert(); } catch (e) {} } };   // plain text again
    var h = start(rec);
    try {
      split = A.splitText(el, { words: true });
      anim = A.animate(split.words || [], { translateY: ["0.5em", 0], opacity: [0, 1], duration: 2000 * T.dur.small,
        delay: A.stagger ? A.stagger(30, { start: dl }) : dl, ease: aEase("out"),
        onComplete: function () { end(rec, true, true); } });
    } catch (e) { end(rec, true); }
    return h;
  }

  // ---- §6.2 install-time effects ---------------------------------------------
  function syncReduce() {
    var r = reduced();
    try { var C = W.Motion && W.Motion.MotionGlobalConfig; if (C) C.skipAnimations = r; } catch (e) {}
    if (r) { settle(false); if (pf.timer) { clearTimeout(pf.timer); pf.timer = 0; } }
    else schedulePrefetch();
  }
  var pf = { timer: 0, ran: false };
  function schedulePrefetch() {
    if (pf.ran || pf.timer || reduced() || D.hidden) return;
    if (ready("gsap") && ready("anime")) { pf.ran = true; return; }   // already here: nothing to fetch
    pf.timer = setTimeout(function () {
      pf.timer = 0;
      if (reduced() || D.hidden) return;              // visible again later re-schedules it
      pf.ran = true;
      load("gsap").then(function () { return load("anime"); });
    }, 2500);
  }
  try { var MC = W.Motion && W.Motion.MotionGlobalConfig; if (MC) MC.skipAnimations = reduced(); } catch (e) {}
  try {
    if (MQ) { if (MQ.addEventListener) MQ.addEventListener("change", syncReduce); else if (MQ.addListener) MQ.addListener(syncReduce); }
    if (W.MutationObserver) new W.MutationObserver(syncReduce).observe(HTML, { attributes: true, attributeFilter: ["data-reduce"] });
  } catch (e) {}
  D.addEventListener("visibilitychange", function () {
    if (D.hidden) { if (pf.timer) { clearTimeout(pf.timer); pf.timer = 0; } settle(true); }
    else schedulePrefetch();
  });
  W.addEventListener("load", schedulePrefetch);
  // the libraries a gesture is about to need, before its click lands
  function onKick(e) {
    var t = e.target;
    if (reduced() || !t || !t.closest) return;
    if (t.closest("#edit-toggle,header .seg,#model-pill")) { load("gsap"); if (t.closest("#model-pill")) load("anime"); }
    if (LIBS.gsap.state !== "idle" && LIBS.anime.state !== "idle") D.removeEventListener("pointerdown", onKick, true);
  }
  D.addEventListener("pointerdown", onKick, { capture: true, passive: true });

  // the view entrance (§8.3): one observer per .stage>.view [hidden], so it
  // follows index.html's setView and aux_agent's wrapper alike
  var vmo = null, stage = null;
  function viewParts(v) {
    var p = v.id === "view-hub" ? list(v.querySelectorAll(".wgrid>.w"))
          : v.id === "view-mind" ? list(v.querySelectorAll("#set-rail,#set-panels>.set-panel:not([hidden])")) : [];
    if (!p.length) p = list(v.children);
    return p.slice(0, T.stagger.max);
  }
  function viewEnter(v) {
    if (v.getAttribute("data-anim") === "off") return;
    if (D.readyState !== "complete") { once("view:boot"); return; }      // the boot view (restored during load)
    var parts = viewParts(v);
    sequence("view", function (tl) {
      tl.fromTo(v, { opacity: 0 }, { opacity: 1, duration: .18 });
      if (parts.length) tl.from(parts, { y: 8, opacity: 0, duration: .32, stagger: { each: Math.min(.03, .2 / parts.length) } }, .02);
    });
  }
  function addViews() {
    list(stage.children).forEach(function (v) {
      if (v.classList.contains("view") && !v.__haObs) { v.__haObs = 1; vmo.observe(v, { attributes: true, attributeFilter: ["hidden"], attributeOldValue: true }); }
    });
  }
  function watchViews() {
    if (vmo || !W.MutationObserver) return;
    stage = D.querySelector(".stage");
    if (!stage) return;
    vmo = new W.MutationObserver(function (recs) {
      var hit = [], seen = [];
      recs.forEach(function (r) {
        var t = r.target;
        if (r.type === "childList") { addViews(); return; }
        if (t.parentNode !== stage || seen.indexOf(t) >= 0) return;
        seen.push(t);
        // a view's FIRST record in the batch holds its state at the batch's
        // start (no old value = it was showing): enter only a view that was
        // hidden then and shows now, never one hidden and re-shown in a breath
        if (r.oldValue != null && !t.hasAttribute("hidden")) hit.push(t);
      });
      hit.forEach(viewEnter);
    });
    vmo.observe(stage, { childList: true });
    addViews();
  }
  if (D.querySelector(".stage")) watchViews();
  else D.addEventListener("DOMContentLoaded", watchViews, { once: true });

  // ---- the API ---------------------------------------------------------------
  function guard(fn, fallback) {
    return function () { try { return fn.apply(null, arguments); } catch (e) { warn("api", "internal error: " + e); return fallback ? fallback(arguments) : doneH(); } };
  }
  // done/mutate/instant callbacks run AT MOST once, even if an internal error
  // sends the call down the fallback path after one of them already ran
  function once1(fn) { var did = false; return typeof fn !== "function" ? fn : function (a) { if (did) return; did = true; return fn(a); }; }
  function cbs(args, idx, keys) {
    var a = Array.prototype.slice.call(args), o, k;
    if (keys) { o = {}; for (k in a[idx] || {}) o[k] = a[idx][k]; keys.forEach(function (n) { o[n] = once1(o[n]); }); a[idx] = o; }
    else a[idx] = once1(a[idx]);
    return a;
  }
  function wrapCb(fn, idx, keys, fallback) {
    var g = guard(fn, fallback);
    return function () { return g.apply(null, cbs(arguments, idx, keys)); };
  }
  function motionVersion() {
    var s = D.querySelector('script[src*="/vendor/motion/motion-"]'), m = s && /motion-(\d+\.\d+\.\d+)/.exec(s.getAttribute("src"));
    return m ? m[1] : MOTION_V;
  }
  W.HermesAnim = Object.freeze({
    v: 1,
    T: T,
    reduced: reduced,
    live: live,
    quiet: function (fn) { quietDepth++; try { return fn(); } finally { quietDepth--; } },   // rethrows fn's error
    once: once,
    ready: function (n) { return ready(n); },
    load: function (n) { try { return load(n); } catch (e) { return Promise.resolve(false); } },
    enter: guard(enter),
    exit: wrapCb(exit, 2, null, function (a) { return instant(null, a[2]); }),
    stagger: guard(stagger),
    spring: guard(spring),
    legacy: guard(legacy, function () { return undefined; }),
    flip: wrapCb(flip, 1, null, function (a) { return instant(null, a[1]); }),
    isFlipping: isFlipping,
    sequence: wrapCb(sequence, 2, ["done", "instant"], function (a) { var o = a[2] || {}; call(o.instant, T); call(o.done); return doneH(); }),
    hold: wrapCb(hold, 1, null, function (a) { return instant(null, a[1]); }),
    count: guard(count),
    draw: guard(draw),
    words: guard(words),
    settle: function () { settle(true); },
    stats: function () {
      var M = W.Motion, n = 0;
      RUN.forEach(function (r) { if (!r.hold) n++; });
      return { started: ST.started, instant: own(ST.instant), running: n, holds: RUN.length - n,
               libs: { motion: M ? (M.MotionGlobalConfig ? motionVersion() : "legacy") : null,
                       gsap: LIBS.gsap.state, anime: LIBS.anime.state } };
    }
  });
})();
