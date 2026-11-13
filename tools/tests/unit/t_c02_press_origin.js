// C02 (docs/audits/2026-10-21-codebase-audit.md) — the Liquid Glass hover lift
// (translate 0 -2px) and gel press (scale .985 about the card's centre) moved
// hub cards out from under the pointer, so a click near a card's edge was
// lost: the card flickered at its bottom edge, and a press 2-3px inside an
// edge ended outside the shrunken card, so mouseup missed it and no click
// fired.
//
// The CSS half runs through the mini cascade over index.html's real
// stylesheets; the press half drives the REAL aux_glass.js pointerdown
// handler in the mini DOM (_a11y_dom.js) and checks the geometry it leaves.
"use strict";
const { bootDashboard, computedValue, harness, PointerEvent, Event } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}
function val(w, el, prop) { const v = computedValue(w, el, prop); return v ? v.value : "none"; }
function px(s) { return parseFloat(s); }
// is client point (x,y) still on a card whose box is r, scaled by s about
// transform-origin o (in the card's own px; "" = its centre)?
function stillOn(r, o, s, x, y) {
  const parts = (o || "").split(/\s+/).filter(Boolean);
  const ox = parts.length ? px(parts[0]) : r.width / 2, oy = parts.length ? px(parts[1]) : r.height / 2;
  const L = r.left + ox - ox * s, Tp = r.top + oy - oy * s;
  return x >= L && x <= L + r.width * s && y >= Tp && y <= Tp + r.height * s;
}
const HUB = { order: ["clock"], model_online: true, widgets: { clock: {
  meta: { title: "Clock", size: "card", cat: "life", icon: "clock" }, data: { time: "9:41" } } } };

(async function main() {
  console.log("C02: hub cards stay under the pointer through hover and press");
  const w = await bootDashboard({ aux: ["aux_glass.js"], routes: { "/api/hub": HUB } });
  const d = w.document;
  const card = d.querySelector('#wgrid .w[data-id="clock"]');
  T.check("a hub card rendered", !!card);

  await scenario("hover", async function () {
    d.__hover = card;
    const tr = val(w, card, "translate"), tf = val(w, card, "transform");
    T.check("hovering a card does not move it (no translate)", tr === "none" || /^0(px)?( 0(px)?)?$/.test(tr), tr);
    T.check("...and no transform either", tf === "none", tf);
    T.check("hover still answers (deeper shadow)", /18px 40px/.test(val(w, card, "box-shadow")));
    const mc = d.createElement("div"); mc.className = "mcard"; d.getElementById("wc-grid").appendChild(mc);
    d.__hover = mc;
    T.check("a Widget Center card does not lift either", val(w, mc, "transform") === "none", val(w, mc, "transform"));
    d.__hover = null;
  });

  await scenario("press near an edge", async function () {
    card.__rect = { left: 100, top: 200, width: 400, height: 150 };
    d.__press = card;
    const s = parseFloat(val(w, card, "scale"));
    T.check("the gel press still scales the card", s > 0.95 && s < 1, s);
    const x = 498, y = 348;                         // 2px inside the bottom-right corner
    T.check("(control: about its centre, this press would end off the card)", !stillOn(card.__rect, "", s, x, y));
    card.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: x, clientY: y, button: 0 }));
    const o = card.style.transformOrigin;
    T.check("the press pivots on the pressed point", o === "398.0px 148.0px", o);
    T.check("so the pressed point stays on the card through the press", stillOn(card.__rect, o, s, x, y));
    // a second press while the release spring is mid-way: the client box is
    // the scaled one, the origin must still be in the card's own px
    card.__rect = { left: 103, top: 201.1, width: 394, height: 147.75, ow: 400, oh: 150 };
    card.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 300, clientY: 275, button: 0 }));
    // (300-103)*400/394 = 200.0 ; (275-201.1)*150/147.75 = 75.0
    T.check("a press on a still-scaled card maps into its own box", card.style.transformOrigin === "200.0px 75.0px", card.style.transformOrigin);
    d.__press = null;
  });

  await scenario("release", async function () {
    card.__rect = { left: 100, top: 200, width: 400, height: 150 };
    card.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 120, clientY: 210, button: 0 }));
    const te = function (type, prop) { const e = new Event(type, { bubbles: true }); e.propertyName = prop; card.dispatchEvent(e); };
    te("transitionend", "scale");                 // the press-in finished while still held
    T.check("held: the pivot stays", card.style.transformOrigin !== "");
    d.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
    te("transitioncancel", "scale");              // a quick tap reverses the press-in half-way
    T.check("a cancelled half-spring keeps the pivot (no jump)", card.style.transformOrigin !== "");
    te("transitionend", "box-shadow");
    T.check("another property ending does not clear it", card.style.transformOrigin !== "");
    te("transitionend", "scale");
    T.check("the finished release spring hands the pivot back to the centre", card.style.transformOrigin === "");
  });

  await scenario("not while editing or under reduced motion", async function () {
    d.getElementById("wgrid").classList.add("editing");
    card.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 120, clientY: 210, button: 0 }));
    T.check("Customize mode (cards jiggle on their centre) is left alone", card.style.transformOrigin === "");
    d.getElementById("wgrid").classList.remove("editing");
    d.documentElement.setAttribute("data-reduce", "1");
    card.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 120, clientY: 210, button: 0 }));
    T.check("reduced motion (no press scale) sets no pivot", card.style.transformOrigin === "");
    d.documentElement.removeAttribute("data-reduce");
  });

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
