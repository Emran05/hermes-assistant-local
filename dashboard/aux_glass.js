/* aux_glass.js — Liquid Glass motion (2026-10-08).
 *
 * Three small behaviours that make the glass answer the person, in the spirit
 * of macOS Tahoe's Liquid Glass. Everything VISUAL lives in index.html's CSS;
 * this file only measures and toggles. Cheap by construction:
 *
 *   1. Pointer light. ONE delegated, passive pointermove listener on document,
 *      rAF-throttled. It finds the glass surface under the cursor
 *      (.glass, and the header's .pill / .topchip / .seg), marks it
 *      [data-glint] (CSS fades its ::after light in) and writes --mx/--my in
 *      that surface's own px box. The layout read is a getBoundingClientRect
 *      when the pointer enters a surface, cached and dropped on scroll/resize.
 *      A surface that is still MOVING (a gel press, a menu growing out of
 *      its anchor) is re-measured every painted frame for SETTLE ms after it
 *      was entered or a transition/animation started on it or an ancestor
 *      (CSS events, plus aux_anim.js's hermes:animstart/animend for the
 *      motion a JS library drives — those fire no CSS event, and their
 *      detail.ms stretches the window to the animation's own length),
 *      and repainted once more where it lands; a scaled box is mapped back
 *      into the surface's own px — a box cached mid-transform put the light
 *      off the pointer until the next scroll (C05). A scrolled surface
 *      (pop-out, model menu) also gets --gsy = its scrollTop: its ::after is
 *      absolutely positioned, so it scrolls away with the content and the
 *      light vanished once the surface scrolled past one screen; CSS
 *      translates the ::after back by --gsy.
 *   1b. Gel press origin. A card's :active scale shrinks it toward its centre,
 *      so a press near an edge ended outside the card and the click was lost
 *      (C02). On pointerdown the card's transform-origin is set to the pressed
 *      point (a scale leaves that point where it is); it is cleared after the
 *      release spring ends so a later jiggle/entrance pivots on the centre —
 *      or shortly after the release when no scale transition ran at all (a
 *      press on a control inside the card), which used to leave the pivot
 *      behind for the next rotate/scale to swing around.
 *   2. Morphing segmented control. A decorative .seg-thumb glides to the
 *      active header tab with a spring and a brief horizontal stretch
 *      mid-travel (FLIP: the thumb takes its final box at once, a WAAPI
 *      transform animation carries it there). Re-measured on resize, on font
 *      load and whenever the tabs or their labels change (aux_agent.js adds
 *      the Agent tab after this file may have run).
 *   3. Scroll-edge glass. header.scrolled while the active view (or the page,
 *      in the stacked <=1120px layout) is scrolled >4px; .chat-head.scrolled
 *      while the transcript under it is. One passive capture-phase scroll
 *      listener, rAF-throttled, one scrollTop read per frame that scrolled.
 *      The header's height is published as --hdr-h on :root (ResizeObserver)
 *      so things that stick under it in the stacked layout (the Settings
 *      rail, scroll-padding) clear it instead of sliding behind it (C04).
 *
 * Reduced motion: nothing lights or glides while EITHER the OS asks for
 * reduced motion OR the app's own pref is on (:root[data-reduce], set by
 * aux_prefs.js). The CSS carries the same two switches.
 *
 * Load order does not matter (it waits for DOMContentLoaded and observes the
 * header for late tabs). In a headless harness without a DOM it no-ops.
 */
(function () {
  "use strict";
  if (typeof document === "undefined" || typeof window === "undefined") return;
  if (window.__auxGlass) return;
  window.__auxGlass = true;

  var D = document, HTML = D.documentElement;
  var MQ = null;
  try { MQ = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null; } catch (e) {}
  function reduced() {
    return HTML.hasAttribute("data-reduce") || !!(MQ && MQ.matches);
  }
  var raf = window.requestAnimationFrame
    ? function (f) { return window.requestAnimationFrame(f); }
    : function (f) { return setTimeout(f, 16); };

  // ===========================================================================
  // 1. pointer light
  // ===========================================================================
  var LIT = ".glass,header .pill,header .topchip,header .seg";
  var SETTLE = 600;        // ms a surface is re-measured per frame after it starts moving
  var cur = null;          // the lit surface
  var box = null;          // its cached client rect (null = re-measure)
  var settleUntil = 0;     // re-measure every paint until then (it may be mid-transform)
  var sx = 1, sy = 1;       // its own px per client px (a surface mid-scale reports a scaled box)
  var px = 0, py = 0, pending = false;
  var clock = (window.performance && window.performance.now)
    ? function () { return window.performance.now(); }
    : function () { return Date.now(); };

  function unlight() {
    if (cur) {
      var was = cur;
      try { was.removeAttribute("data-glint"); } catch (e) {}
      // once its light has faded out (.5s), put a moved layer back: nobody
      // sees it, and a stale one would hold the scroll range open (setGsy)
      if (was.__gsy > 0) setTimeout(function () { if (was !== cur && was.__gsy) setGsy(was, 0); }, 600);
    }
    cur = null; box = null;
  }
  function measure() {
    box = cur.getBoundingClientRect();
    // --mx/--my live in the surface's own, untransformed box; a surface
    // caught mid-scale (a menu growing out of its anchor, a pressed card)
    // reports a scaled client box, so map client px back into its own px
    var w = cur.offsetWidth, h = cur.offsetHeight;
    sx = (w && box.width) ? w / box.width : 1;
    sy = (h && box.height) ? h / box.height : 1;
    if (Math.abs(sx - 1) < 0.01) sx = 1;      // offsetWidth is rounded: ignore that noise
    if (Math.abs(sy - 1) < 0.01) sy = 1;
  }
  // the ::after light is absolutely positioned, so inside a scroller it
  // scrolls away with the content; --gsy carries it back to the visible box.
  // The moved layer counts toward the scroller's scrollable overflow, so
  // --gsy must never outlive the scroll position and content it was set for:
  // left over from a long pop-out, or under content that got shorter (a new
  // chat-find query, a collapsed Details row), it held the scroll range open
  // over blank space. So it is dropped when the light goes out (unlight),
  // re-checked at the bottom of the range — where a propped range always
  // sits — and re-checked when the lit scroller's content changes.
  function setGsy(el, st) {
    el.__gsy = st;
    try {
      if (st) {
        el.style.setProperty("--gsy", st.toFixed(1) + "px");
        if (!el.__gsyMO && window.MutationObserver) {
          el.__gsyMO = new MutationObserver(function () {
            if (el !== cur) { if (el.__gsy) setGsy(el, 0); }
            else if (!pending) { pending = true; raf(paint); }
          });
          // not "style": paint() writes --mx/--my on el itself. "class":
          // content can shrink through a class toggle alone (a collapsed
          // row), which left the range propped until the next move
          el.__gsyMO.observe(el, { childList: true, subtree: true, characterData: true,
                                   attributes: true, attributeFilter: ["hidden", "class"] });
        }
      } else {
        el.style.removeProperty("--gsy");
        if (el.__gsyMO) { el.__gsyMO.disconnect(); el.__gsyMO = null; }
      }
    } catch (e) {}
  }
  function syncScroll(el) {
    // only a surface that already carries a layer offset pays these reads
    if (el.__gsy > 0 && (el.scrollTop || 0) + el.clientHeight >= el.scrollHeight - 1) {
      // let the browser clamp to the real range, then read it back below
      // (one layout, and only at the bottom); -1 forces the write
      try { el.style.removeProperty("--gsy"); } catch (e) {}
      el.__gsy = -1;
    }
    var st = el.scrollTop || 0;
    if (el.__gsy === st || (!el.__gsy && !st)) return;
    setGsy(el, st);
  }
  function paint() {
    pending = false;
    if (!cur) return;
    if (!box || clock() < settleUntil) measure();
    cur.style.setProperty("--mx", ((px - box.left) * sx).toFixed(1) + "px");
    cur.style.setProperty("--my", ((py - box.top) * sy).toFixed(1) + "px");
    syncScroll(cur);
  }
  function onMove(e) {
    // hover light is a mouse/trackpad thing; a touch has no hover to answer
    if (e.pointerType && e.pointerType !== "mouse" && e.pointerType !== "pen") return;
    if (reduced()) { if (cur) unlight(); return; }
    var t = e.target;
    var el = (t && t.closest) ? t.closest(LIT) : null;
    // off glass: nothing to light (and `cur` may already be null — every
    // later move over a gap used to reach cur.hasAttribute below and throw)
    if (!el) { if (cur) unlight(); return; }
    if (el !== cur) {
      unlight();
      cur = el; box = null;                  // paint() measures it
      settleUntil = clock() + SETTLE;
    }
    px = e.clientX; py = e.clientY;
    if (!cur.hasAttribute("data-glint")) {
      // position first, THEN fade in — so the light appears where the
      // pointer is instead of sliding in from the default spot
      paint();
      cur.setAttribute("data-glint", "");
    } else if (!pending) {
      pending = true; raf(paint);
    }
  }
  D.addEventListener("pointermove", onMove, { passive: true });
  // leaving the window / the app losing focus puts the light out
  D.addEventListener("mouseout", function (e) { if (!e.relatedTarget) unlight(); }, { passive: true });
  window.addEventListener("blur", unlight);
  window.addEventListener("resize", function () { box = null; }, { passive: true });
  // the lit surface (or something it sits in) starts or stops moving: measure
  // it every frame while it moves and once more where it lands
  function moves(e) {
    var t = e.target;
    return !!(cur && t && (t === cur || (t.contains && t.contains(cur))));
  }
  var SETTLE_MAX = 1500;    // a JS animation's own length, capped (aux_anim's are <= .9s)
  function onMotionStart(e) {
    if (!moves(e)) return;
    var ms = (e.detail && +e.detail.ms) || 0;
    box = null;
    settleUntil = Math.max(settleUntil, clock() + Math.max(SETTLE, Math.min(ms, SETTLE_MAX)));
  }
  // where it lands, under a pointer that may not move again: repaint once
  function onMotionEnd(e) { if (moves(e)) { box = null; if (!pending) { pending = true; raf(paint); } } }
  // hermes:anim* = aux_anim.js: GSAP, anime and Motion's JS-driven values fire
  // no transition/animation event, so without these the light measured a
  // stale box for the whole of a Flip or a view entrance
  ["transitionrun", "animationstart", "hermes:animstart"].forEach(function (n) {
    D.addEventListener(n, onMotionStart, { capture: true, passive: true });
  });
  ["transitionend", "transitioncancel", "animationend", "animationcancel", "hermes:animend"].forEach(function (n) {
    D.addEventListener(n, onMotionEnd, { capture: true, passive: true });
  });
  // flipping the app's Reduce-motion pref while a surface is lit
  try {
    new MutationObserver(function () { if (reduced()) unlight(); })
      .observe(HTML, { attributes: true, attributeFilter: ["data-reduce"] });
  } catch (e) {}

  // ===========================================================================
  // 1b. gel press origin (C02)
  // ===========================================================================
  var PRESS = ".w,.mcard";
  var pressed = null;
  D.addEventListener("pointerdown", function (e) {
    if (e.button || reduced()) return;          // primary button only; no press scale under reduced motion
    var t = e.target, el = (t && t.closest) ? t.closest(PRESS) : null;
    if (!el || (el.closest && el.closest(".editing"))) return;
    var r = el.getBoundingClientRect(), w = el.offsetWidth, h = el.offsetHeight;
    if (!w || !h || !r.width || !r.height) return;
    // the pressed point in the card's own (unscaled) box: a lift is a
    // translate, which the subtraction cancels; a press still springing back
    // is a scale, which the w/width ratio undoes
    var x = (e.clientX - r.left) * w / r.width, y = (e.clientY - r.top) * h / r.height;
    el.style.transformOrigin = x.toFixed(1) + "px " + y.toFixed(1) + "px";
    el.__pressOrigin = true;
    el.__pressRan = false;
    pressed = el;
  }, { capture: true, passive: true });
  // did the gel press actually run on the card? (a press on a button inside
  // it scales the button, not the card)
  function scaleEv(e) { return !e.propertyName || e.propertyName === "scale" || e.propertyName === "transform"; }
  function pressRan(e) { var el = e.target; if (el && el.__pressOrigin && scaleEv(e)) el.__pressRan = true; }
  ["transitionrun", "transitionstart", "transitionend", "transitioncancel"].forEach(function (n) {
    D.addEventListener(n, pressRan, { capture: true, passive: true });
  });
  var NOSCALE_MS = 160;     // a transition's run event lands within a couple of frames
  function release() {
    var el = pressed;
    pressed = null;
    if (!el || !el.__pressOrigin) return;
    // no scale transition ran: nothing will end to clear the pivot, so clear
    // it here — a stale origin would swing the next rotate/scale (the
    // Customize jiggle, a pop entrance) around the old press point
    setTimeout(function () {
      if (el !== pressed && el.__pressOrigin && !el.__pressRan) {
        el.__pressOrigin = false;
        try { el.style.transformOrigin = ""; } catch (x) {}
      }
    }, NOSCALE_MS);
  }
  D.addEventListener("pointerup", release, { capture: true, passive: true });
  D.addEventListener("pointercancel", release, { capture: true, passive: true });
  // once the release spring has run, the centre is the pivot again (the
  // Customize jiggle and entrance animations expect it). Only on a FINISHED
  // scale transition: a quick tap cancels the press-in half-way and the
  // release's own transitionend follows — clearing on that cancel would
  // jump the still-scaled card to a centre pivot.
  function clearOrigin(e) {
    var el = e.target;
    if (!el || !el.__pressOrigin || el === pressed) return;
    if (e.propertyName && e.propertyName !== "scale" && e.propertyName !== "transform") return;
    el.__pressOrigin = false;
    try { el.style.transformOrigin = ""; } catch (x) {}
  }
  D.addEventListener("transitionend", clearOrigin, { capture: true, passive: true });

  // ===========================================================================
  // 2. morphing segmented control (header .seg)
  // ===========================================================================
  var seg = null, thumb = null, last = null, lastOn = null, glide = null;

  function tf(L, W, base, sy) {
    return "translateX(" + L.toFixed(2) + "px) scale(" + (W / base).toFixed(4) + "," + (sy || 1) + ")";
  }
  function place(allowAnim) {
    if (!seg || !thumb) return;
    var on = seg.querySelector("b.on");
    // a hidden active tab (Settings is reached from the prefs menu now) has
    // no box: nothing to sit under, so the thumb fades out
    if (!on || !on.offsetWidth) {
      thumb.style.opacity = "0"; last = null; lastOn = on; return;
    }
    var x = on.offsetLeft, w = on.offsetWidth;
    var moved = !last || last.x !== x || last.w !== w;
    var switched = on !== lastOn;
    lastOn = on;
    if (!moved) { thumb.style.opacity = "1"; return; }
    var from = last;
    last = { x: x, w: w };
    if (glide) { try { glide.cancel(); } catch (e) {} glide = null; }
    thumb.style.width = w + "px";
    thumb.style.transform = "translateX(" + x + "px)";
    var wasHidden = thumb.style.opacity !== "1";
    thumb.style.opacity = "1";
    if (!allowAnim || !switched || !from || wasHidden || reduced() || !thumb.animate) return;
    // liquid travel: the leading edge runs ahead of the trailing one, so the
    // plate stretches mid-way (and squashes a touch vertically), then springs
    // into the new tab's box with a small overshoot
    var L0 = from.x, R0 = from.x + from.w, L1 = x, R1 = x + w, right = L1 > L0;
    var Lm = L0 + (L1 - L0) * (right ? 0.34 : 0.66);
    var Rm = R0 + (R1 - R0) * (right ? 0.66 : 0.34);
    try {
      glide = thumb.animate([
        { transform: tf(L0, R0 - L0, w), easing: "cubic-bezier(.45,0,.6,1)" },
        { transform: tf(Lm, Rm - Lm, w, 0.9), offset: 0.42, easing: "cubic-bezier(.25,1.5,.5,1)" },
        { transform: tf(L1, R1 - L1, w) }
      ], { duration: 480 });
      glide.onfinish = function () { glide = null; };
    } catch (e) { glide = null; }
  }

  function initSeg() {
    var s = D.querySelector("header .seg");
    if (!s || s === seg) return;
    seg = s;
    thumb = s.querySelector(".seg-thumb");
    if (!thumb) {
      thumb = D.createElement("span");
      thumb.className = "seg-thumb";
      thumb.setAttribute("aria-hidden", "true");
      s.insertBefore(thumb, s.firstChild);
    }
    s.classList.add("has-thumb");
    last = null; lastOn = null;
    place(false);
    // tab switches (class "on"), tabs added/hidden, labels changed. Style
    // is deliberately NOT observed: the pointer light rewrites the seg's own
    // style every frame and the thumb rewrites its own — size changes are the
    // ResizeObserver's job.
    try {
      new MutationObserver(function (recs) {
        for (var i = 0; i < recs.length; i++) {
          if (recs[i].target !== thumb) { place(true); refreshEdge(); return; }
        }
      }).observe(s, { subtree: true, childList: true, characterData: true,
                      attributes: true, attributeFilter: ["class", "hidden"] });
    } catch (e) {}
    try { if (window.ResizeObserver) new ResizeObserver(function () { place(false); }).observe(s); } catch (e) {}
  }
  window.addEventListener("resize", function () { place(false); }, { passive: true });
  try { if (D.fonts && D.fonts.ready) D.fonts.ready.then(function () { last = null; place(false); }); } catch (e) {}

  // ===========================================================================
  // 3. scroll-edge glass
  // ===========================================================================
  var EDGE = 4, hdr = null, sTarget = null, sPending = false;

  function setEdge(el, on) {
    if (el && el.classList.contains("scrolled") !== on) el.classList.toggle("scrolled", on);
  }
  function pageScroller() { return D.scrollingElement || HTML; }
  // the header reflects whatever is CURRENTLY under it: the visible view, or
  // the page itself in the stacked layout. Called on every view switch.
  function refreshEdge() {
    hdr = hdr || D.querySelector("header");
    if (!hdr) return;
    var y = pageScroller().scrollTop || 0;
    var v = D.querySelector(".stage>.view:not([hidden])");
    if (v) y = Math.max(y, v.scrollTop || 0);
    setEdge(hdr, y > EDGE);
  }
  function scrollFrame() {
    sPending = false;
    var t = sTarget; sTarget = null;
    if (!t) return;
    hdr = hdr || D.querySelector("header");
    if (t === D || t === HTML || t === D.body || t === D.scrollingElement) {
      setEdge(hdr, (pageScroller().scrollTop || 0) > EDGE);
      return;
    }
    if (!t.classList) return;
    if (t.classList.contains("msgs")) {
      var chat = t.closest ? t.closest(".chat") : null;
      setEdge(chat ? chat.querySelector(".chat-head") : null, t.scrollTop > EDGE);
    } else if (t.classList.contains("view") && t.parentNode && t.parentNode.classList &&
               t.parentNode.classList.contains("stage")) {
      setEdge(hdr, t.scrollTop > EDGE || (pageScroller().scrollTop || 0) > EDGE);
    }
  }
  D.addEventListener("scroll", function (e) {
    box = null;                               // the lit surface may have moved
    // it scrolled (or something it sits in did) under a still pointer:
    // repaint so the light stays put and --gsy follows the scroll
    if (moves(e) && !pending) { pending = true; raf(paint); }
    sTarget = e.target;
    if (!sPending) { sPending = true; raf(scrollFrame); }
  }, { capture: true, passive: true });

  // the header's own height, for whatever sticks under it (C04)
  function publishHeader() {
    hdr = hdr || D.querySelector("header");
    if (!hdr) return;
    var h = hdr.offsetHeight || 0;
    if (h > 0) HTML.style.setProperty("--hdr-h", h + "px");
  }

  // ===========================================================================
  var hdrRO = false;
  function boot() {
    initSeg(); refreshEdge(); publishHeader();
    if (!hdrRO && hdr) {
      try {
        if (window.ResizeObserver) { new ResizeObserver(publishHeader).observe(hdr); hdrRO = true; }
      } catch (e) {}
    }
  }
  if (D.readyState === "loading") D.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
  // aux_agent.js may insert its tab (and hide Settings) after boot — the
  // observer above catches that, but a late .seg replacement would not be
  window.addEventListener("load", function () { initSeg(); place(false); publishHeader(); });
  window.addEventListener("resize", publishHeader, { passive: true });
})();
