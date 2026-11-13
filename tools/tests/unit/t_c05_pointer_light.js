// C05 (docs/audits/2026-10-21-codebase-audit.md) — the Liquid Glass pointer
// light (aux_glass.js) measured a surface ONCE when the pointer entered it and
// kept that box until a scroll/resize, so:
//   * entering a surface mid-transform (a menu growing out of its anchor, a
//     pressed card) put the light off the pointer — the cached box was the
//     scaled, still-moving one;
//   * in a scrolled pop-out or menu the light vanished: its ::after is
//     absolutely positioned, scrolls away with the content, and --my was
//     measured in content coordinates past the ::after's own box.
//
// Drives the REAL aux_glass.js in the mini DOM (_a11y_dom.js) with a clock
// the test controls; the ::after rule comes from the real stylesheets via
// the mini cascade.
"use strict";
const { bootDashboard, computedValue, harness, PointerEvent, Event } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}

(async function main() {
  console.log("C05: the pointer light stays under the pointer");
  let now = 1000;
  const w = await bootDashboard({ aux: ["aux_glass.js"], before: function (w0) { w0.performance = { now: function () { return now; } }; } });
  const d = w.document;
  const surf = d.createElement("section"); surf.className = "glass"; surf.innerHTML = "<p>content</p>";
  d.body.appendChild(surf);
  const move = function (x, y) { surf.firstChild.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: x, clientY: y, pointerType: "mouse" })); };
  const mx = function () { return surf.style.getPropertyValue("--mx"); };
  const my = function () { return surf.style.getPropertyValue("--my"); };
  const frame = function () { w.__flushTimers(1); };
  const leave = function () { d.body.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 1, clientY: 1, pointerType: "mouse" })); };

  await scenario("basic", async function () {
    surf.__rect = { left: 100, top: 100, width: 300, height: 200 };
    move(150, 160);
    T.check("entering a surface lights it", surf.hasAttribute("data-glint"));
    T.check("the light sits under the pointer, in the surface's own px", mx() === "50.0px" && my() === "60.0px", [mx(), my()]);
    leave();
  });

  await scenario("entered mid-transform", async function () {
    // a menu caught at scale .92 of its 300x200 box, growing about its centre
    surf.__rect = { left: 112, top: 108, width: 276, height: 184, ow: 300, oh: 200 };
    move(250, 200);
    T.check("a scaled surface maps the pointer into its own box", mx() === "150.0px" && my() === "100.0px", [mx(), my()]);
    // the grow animation ends under a pointer that does not move
    surf.__rect = { left: 100, top: 100, width: 300, height: 200 };
    surf.dispatchEvent(new Event("animationend", { bubbles: true }));
    frame();
    T.check("when it lands the light is repainted where the pointer is", mx() === "150.0px" && my() === "100.0px", [mx(), my()]);
    leave();
  });

  await scenario("moves after the settle window", async function () {
    surf.__rect = { left: 100, top: 100, width: 300, height: 200 };
    move(150, 160);
    now += 2000;                                        // long after entry: the box is cached
    surf.__rect = { left: 100, top: 140, width: 300, height: 200 };
    move(150, 170); frame();
    T.check("(control: a still surface is not re-measured on every move)", my() === "70.0px", my());
    // a transition starts on an ancestor (the press on the card it sits in)
    d.body.dispatchEvent(new Event("transitionrun", { bubbles: true }));
    move(150, 180); frame();
    T.check("a transition starting re-measures the moving surface", my() === "40.0px", my());
    leave();
  });

  await scenario("scrolled surface", async function () {
    surf.__rect = { left: 100, top: 100, width: 300, height: 200 };
    surf.scrollTop = 500;
    move(150, 160);
    const gsy = surf.style.getPropertyValue("--gsy");
    T.check("a scrolled surface carries its scroll offset (--gsy)", gsy === "500.0px", gsy);
    T.check("--my is measured in the visible box", my() === "60.0px", my());
    const tr = computedValue(w, surf, "translate", "::after");
    T.check("the light layer (::after) is translated back by --gsy", !!tr && /var\(--gsy\)/.test(tr.value), tr && tr.value);
    const y = parseFloat(my()), h = surf.__rect.height;
    T.check("so the light centre is inside its layer's box (0.." + h + ")", y >= 0 && y <= h, y);
    surf.scrollTop = 700;
    surf.dispatchEvent(new Event("scroll", { bubbles: false }));
    frame();
    T.check("scrolling under a still pointer moves the layer with it", surf.style.getPropertyValue("--gsy") === "700.0px" && my() === "60.0px",
      [surf.style.getPropertyValue("--gsy"), my()]);
    surf.scrollTop = 0;
    leave();
  });

  // Review of the --gsy fix: the translated ::after counts toward the
  // scroller's scrollable overflow (measured in Chromium: a pop-out reopened
  // with 223px of content kept a 1723px scroll range from a stale 1500px
  // --gsy; content that got shorter under a lit, scrolled pop-out stayed
  // scrolled 400px past its end). The mini DOM has no layout, so this one
  // scroller models the two browser rules that matter: the range is the
  // larger of the content and the moved layer's bottom, and scrollTop is
  // clamped to that range when read.
  await scenario("a moved light layer never holds a scroll range open", async function () {
    const sc = d.createElement("section"); sc.className = "glass"; sc.innerHTML = "<p>rows</p>";
    d.body.appendChild(sc);
    sc.__rect = { left: 500, top: 100, width: 300, height: 200 };
    let content = 2000, st = 0;
    const layer = function () { return parseFloat(sc.style.getPropertyValue("--gsy")) || 0; };
    Object.defineProperty(sc, "scrollHeight", { configurable: true, get: function () { return Math.max(content, layer() + 200); } });
    Object.defineProperty(sc, "scrollTop", { configurable: true,
      get: function () { st = Math.max(0, Math.min(st, sc.scrollHeight - 200)); return st; },
      set: function (v) { st = v; } });
    const over = function (x, y) { sc.firstChild.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: x, clientY: y, pointerType: "mouse" })); };

    sc.scrollTop = 900;
    over(600, 150);
    T.check("(control) a lit, scrolled scroller carries its offset", layer() === 900, sc.style.getPropertyValue("--gsy"));
    // a re-render leaves less content under a pointer that does not move
    content = 500; sc.innerHTML = "<p>fewer rows</p>";
    await w.__tick(); frame();
    T.check("content that got shorter under a still pointer is not held open (scroll clamps to its real end)",
      sc.scrollTop === 300 && sc.scrollHeight === 500, { scrollTop: sc.scrollTop, scrollHeight: sc.scrollHeight });
    T.check("...and the light layer follows the clamped position", layer() === 300, sc.style.getPropertyValue("--gsy"));

    // scrolled again, then the pointer leaves: once the light has faded the
    // layer goes back, so a later, shorter fill is not held open either
    content = 2000; sc.scrollTop = 1500;
    sc.dispatchEvent(new Event("scroll", { bubbles: false })); frame();
    T.check("(control) the layer tracks a scroll under the pointer", layer() === 1500, sc.style.getPropertyValue("--gsy"));
    leave();
    w.__flushTimers(2);
    T.check("after the light goes out the layer offset is dropped", sc.style.getPropertyValue("--gsy") === "", sc.style.getPropertyValue("--gsy"));
    content = 200; sc.scrollTop = 0;                       // reopened with one screen of content
    T.check("so the reopened, short scroller has no blank scroll range", sc.scrollHeight === 200, sc.scrollHeight);
    sc.remove();
  });

  await scenario("reduced motion", async function () {
    d.documentElement.setAttribute("data-reduce", "1");
    move(150, 160);
    T.check("nothing lights under reduced motion", !surf.hasAttribute("data-glint"));
    d.documentElement.removeAttribute("data-reduce");
  });

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
