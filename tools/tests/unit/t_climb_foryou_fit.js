// For You fills its card with real picks (design climb round 3): the Hub card
// lists every pick the payload carries, then shows as many as the card's
// height holds (never fewer than three, never a fake one) with the note
// directly under the last pick shown.
//
// Runs the REAL index.html + aux_foryou.js in the mini DOM; layout is stubbed
// per element (the mini DOM has none), the fit logic is the real one.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

function moves(n) {
  const out = [];
  for (let i = 0; i < n; i++) out.push({ title: "Pick " + (i + 1), url: "https://example.com/" + i, source: "Src", ts: 0 });
  return out;
}
const HEIGHTS = [85, 85, 64, 85, 85, 85];

function layout(w, body, room) {
  // stacked picks; the list reports `room` while contain:size is on (phase 1)
  const list = body.querySelector(".fy-list");
  const kids = [...list.children];
  let y = 0;
  kids.forEach((k, i) => { const h = HEIGHTS[i] || 85; k.__rect = { left: 0, top: y, width: 300, height: h }; y += h; });
  Object.defineProperty(list, "__rect", { configurable: true, get() {
    if (list.style.contain === "size") return { left: 0, top: 0, width: 300, height: room };
    let hh = 0; kids.forEach((k) => { if (k.style.display !== "none") hh += k.__rect.height; });
    return { left: 0, top: 0, width: 300, height: hh };
  } });
}

function render(w, n, personalized) {
  const card = w.document.createElement("div");
  card.className = "w"; card.setAttribute("data-id", "foryou");
  const body = w.document.createElement("div"); body.className = "w-body";
  card.appendChild(body); w.document.body.appendChild(card);
  w.__fyArgs = [body, { available: true, personalized: !!personalized, moves: moves(n), count: n, generated_at: 1 }];
  w.__run("RENDER.foryou(__fyArgs[0], __fyArgs[1], null)");
  return body;
}
const shown = (body) => [...body.querySelectorAll(".fy-move")].filter((x) => x.style.display !== "none").length;

(async function main() {
  console.log("For You fits its card");
  const w = await bootDashboard({ aux: ["aux_foryou.js"] });
  T.check("the fit is exported for the page", typeof w.__fyFit === "function");

  let b = render(w, 4);
  T.check("every pick the payload carries is in the list (was capped at 3)", b.querySelectorAll(".fy-move").length === 4);
  layout(w, b, 320);
  T.check("a card with room for four shows four", w.__fyFit(b) === 4 && shown(b) === 4);
  T.check("the note follows the list directly (no pushed-down footer)",
    b.querySelector(".fy-list").nextElementSibling === b.querySelector(".fy-foot") && /margin-top:0(;|$)/.test(b.querySelector(".fy-foot").getAttribute("style")));
  T.check("phase 1's containment is gone once fitted", b.querySelector(".fy-list").style.contain === "" && b.querySelector(".fy-list").style.minHeight === "");

  b = render(w, 4);
  layout(w, b, 316);
  T.check("a pick that would be cut is not shown (3 of 4)", w.__fyFit(b) === 3 && shown(b) === 3 && b.querySelectorAll(".fy-move")[3].style.display === "none");

  b = render(w, 6);
  layout(w, b, 120);
  T.check("never fewer than three, even in a short card", w.__fyFit(b) === 3 && shown(b) === 3);

  b = render(w, 2);
  layout(w, b, 400);
  T.check("two picks: both shown, nothing stretched, the note under them",
    w.__fyFit(b) === 2 && shown(b) === 2 && b.querySelector(".fy-list").style.flex === "");

  b = render(w, 3, true);
  T.check("a personalized card keeps its 'moves · updated' footer under the list",
    b.querySelector(".fy-list").nextElementSibling && /moves · updated/.test(b.querySelector(".fy-foot").textContent));
  T.done();
})().catch((e) => { console.log("FAIL threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
