// Round-2 review X-frontend-4 — aux_glass.js's pointer light threw a
// TypeError on every pointermove over a non-glass spot once nothing was lit:
// the first move off glass called unlight() (cur = null) and returned, and
// every later one skipped the `el !== cur` block (null === null) and reached
// `cur.hasAttribute(...)` on null. In the app that is one uncaught exception
// per pointer event while the pointer sits over the header, the gaps between
// widgets or the page edge.
//
// Drives the REAL aux_glass.js in the mini DOM (_a11y_dom.js), whose event
// dispatch collects listener exceptions in w.__errors.
"use strict";
const { bootDashboard, harness, PointerEvent } = require("./_a11y_dom.js");
const T = harness();

(async function main() {
  console.log("X-frontend-4: moving over non-glass never throws");
  try {
    const w = await bootDashboard({ aux: ["aux_glass.js"] });
    const d = w.document;
    const surf = d.createElement("section"); surf.className = "glass"; surf.innerHTML = "<p>content</p>";
    surf.__rect = { left: 100, top: 100, width: 300, height: 200 };
    const gap = d.createElement("div"); gap.className = "not-glass"; gap.textContent = "gap";
    d.body.appendChild(surf); d.body.appendChild(gap);
    const move = function (el, x, y) {
      el.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: x, clientY: y, pointerType: "mouse" }));
    };
    const before = w.__errors.length;

    // nothing lit yet: two moves over a gap
    move(gap, 5, 5); move(gap, 6, 6);
    T.check("two moves over a gap with nothing lit raise nothing", w.__errors.length === before,
      w.__errors.slice(before).map(String));

    // light a surface, then leave it for a gap and keep moving there
    move(surf.firstChild, 150, 160);
    T.check("entering a glass surface still lights it", surf.hasAttribute("data-glint"));
    move(gap, 5, 5);
    T.check("leaving it for a gap puts the light out", !surf.hasAttribute("data-glint"));
    for (let i = 0; i < 5; i++) move(gap, 5 + i, 5);
    T.check("...and further moves over the gap raise nothing", w.__errors.length === before,
      w.__errors.slice(before).map(String));

    // and coming back still works
    move(surf.firstChild, 160, 170);
    T.check("returning to the surface lights it again", surf.hasAttribute("data-glint"));
    T.check("no listener error at any point", w.__errors.length === before, w.__errors.slice(before).map(String));
  } catch (e) {
    T.check("ran without throwing", false, String(e && e.stack || e));
  }
  T.done();
})();
