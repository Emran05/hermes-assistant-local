// Round-2 review X-frontend-5 — a pop-out reopened right after a close could
// show an invisible sheet. closePop() ran its exit through Motion (motion.min.js,
// v11.11.13); openPop() cancelled that exit and cleared the inline
// opacity/transform it had written, but Motion commits the exit's END value
// (opacity:0) inline once more AFTER the cancel. Measured in Chromium against
// the real page: a reopen 20–190 ms after a close left `opacity: 0` on
// #wpop-sheet for good (the dimmed backdrop showed, the sheet did not).
// The exit is now a native Web Animation, which never writes an inline style
// and whose cancel() is final; it holds its faded end (fill:'forwards') until
// openPop cancels it.
//
// The mini DOM does not load motion.min.js, so this suite installs a Motion
// stand-in that reproduces exactly that late commit (end value written inline
// when the animation ends, and again shortly after cancel()), boots the REAL
// index.html against it, and drives the real openPop/closePop.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

function lateCommitMotion(w) {
  w.Motion = {
    animate: function (el, kf, opt) {
      const end = {};
      if (kf && !Array.isArray(kf)) Object.keys(kf).forEach(function (k) { const v = kf[k]; end[k] = Array.isArray(v) ? v[v.length - 1] : v; });
      const commit = function () {
        if ("opacity" in end) el.style.opacity = String(end.opacity);
        if ("scale" in end) el.style.transform = "none";
      };
      w.setTimeout(commit, ((opt && opt.duration) || 0) * 1000);
      return { cancel: function () { w.setTimeout(commit, 40); }, stop: function () {}, finished: Promise.resolve() };
    }
  };
}

(async function main() {
  console.log("X-frontend-5: a pop-out reopened right after a close is visible");
  try {
    const w = await bootDashboard({ before: lateCommitMotion, routes: { "/api/expand": { rich: false } } });
    const d = w.document, pop = d.getElementById("wpop"), sheet = d.getElementById("wpop-sheet");
    T.check("the page picked up the Motion stand-in", w.__run("typeof M==='object'&&M!==null"));

    w.__run("openPop('clock')"); await w.__tick(); w.__flushTimers(3); await w.__tick();
    T.check("the pop-out opens visible", !pop.hidden && !sheet.style.opacity, sheet.getAttribute("style"));

    // close, let the 170 ms hide fire (and anything else queued), reopen
    w.__run("closePop()"); w.__flushTimers(1); await w.__tick();
    T.check("closing hides it", pop.hidden);
    w.__run("openPop('clock')"); await w.__tick();
    w.__flushTimers(4); await w.__tick();                     // every late write has its chance
    T.check("the reopened pop-out is shown", !pop.hidden);
    T.check("...and its sheet carries no inline opacity:0", sheet.style.opacity !== "0", "inline opacity=" + sheet.style.opacity);
    T.check("...nor does the backdrop", pop.style.opacity !== "0", "inline opacity=" + pop.style.opacity);

    // reopen while the exit is still running (before the hide timer)
    w.__run("closePop()"); await w.__tick();
    w.__run("openPop('clock')"); await w.__tick();
    w.__flushTimers(4); await w.__tick();
    T.check("a reopen mid-exit stays visible too", !pop.hidden && sheet.style.opacity !== "0", "inline opacity=" + sheet.style.opacity);

    // and a plain close still ends hidden
    w.__run("closePop()"); w.__flushTimers(2); await w.__tick();
    T.check("a close with no reopen ends hidden", pop.hidden);

    // review: the exit HOLDS its faded end (fill:'forwards') until openPop
    // cancels it. With fill:'none' a hide timer that fired after the 180 ms
    // exit (a busy main thread) let the sheet and backdrop snap back to full
    // opacity before they were hidden (measured in Chromium).
    w.__run("openPop('clock')"); await w.__tick(); w.__flushTimers(3); await w.__tick();
    const made = [];
    [sheet, pop].forEach(function (el) {
      el.animate = function (kf, opt) {
        const a = { el: el, opt: opt || {}, cancelled: false, cancel: function () { a.cancelled = true; } };
        made.push(a); return a;
      };
      el.getAnimations = function () { return made.filter(function (a) { return a.el === el && !a.cancelled; }); };
    });
    w.__run("closePop()"); await w.__tick();
    T.check("the exit is a Web Animation on the sheet and the backdrop", made.length === 2, made.length);
    T.check("...that holds its end until it is cancelled (fill:'forwards')",
      made.length === 2 && made.every(function (a) { return a.opt.fill === "forwards"; }), made.map(function (a) { return a.opt.fill; }));
    w.__flushTimers(1); await w.__tick();
    w.__run("openPop('clock')"); await w.__tick();
    T.check("a reopen cancels both, so nothing held stays faded", made.every(function (a) { return a.cancelled; }));
  } catch (e) {
    T.check("ran without throwing", false, String(e && e.stack || e));
  }
  T.done();
})();
