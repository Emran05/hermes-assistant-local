// Quick-action chips (design climb round 3): a wide row (the Agent page's
// centre column) shows ONE row of chips plus a "+N" chip, never a lone chip
// orphaned on a second row; the Hub's narrow chat column keeps two rows.
//
// Runs the REAL index.html's fitActionChips() in the mini DOM with a stubbed
// flow layout (chips wrap left to right inside the box width).
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
const LABELS = ["Clipboard", "Plan my day", "What's new in my folders", "Find a file", "Summarize a document",
  "Draft an email", "Weekly review", "Clean-up suggestions"];
const WIDTH = { "Clipboard": 76, "Plan my day": 92, "What's new in my folders": 158, "Find a file": 75,
  "Summarize a document": 150, "Draft an email": 101, "Weekly review": 100, "Clean-up suggestions": 142 };

function flow(box, boxW) {
  box.__rect = { left: 0, top: 0, width: boxW, height: 80 };
  const lay = () => {
    let x = 0, y = 0; const pos = new Map();
    [...box.children].forEach((c) => {
      if (c.hidden) return;
      const w = WIDTH[c.textContent] || 44;
      if (x > 0 && x + w > boxW) { x = 0; y += 37; }
      pos.set(c, { left: x, top: y, width: w, height: 28 }); x += w + 6;
    });
    return pos;
  };
  [...box.children].forEach((c) => Object.defineProperty(c, "__rect", { configurable: true, get() { return lay().get(c); } }));
  // the "+N"/"Less" chip is appended later: give every new child the same getter
  const ap = box.appendChild.bind(box);
  box.appendChild = (c) => { const r = ap(c); Object.defineProperty(c, "__rect", { configurable: true, get() { return lay().get(c); } }); return r; };
}
function state(box) {
  const vis = [...box.children].filter((c) => !c.hidden);
  return { tops: [...new Set(vis.map((c) => c.offsetTop))], more: (box.querySelector(".chip-more") || {}).textContent || "" };
}

(async function main() {
  console.log("quick-action chip rows");
  const w = await bootDashboard({ routes: { "/api/actions": { actions: LABELS.map((l) => ({ label: l, prompt: l })) } } });
  const box = w.document.getElementById("actions");
  T.check("all eight chips are there", box.querySelectorAll(".chip").length === 8);

  flow(box, 780);                       // Agent centre column
  w.fitActionChips(); let s = state(box);
  T.check("a wide row keeps ONE row, with a +N chip (no orphan on row 2)", s.tops.length === 1 && /^\+\d$/.test(s.more), s);

  flow(box, 400);                       // Hub split chat column
  w.fitActionChips(); s = state(box);
  T.check("the Hub's narrow column keeps two rows and a +N chip", s.tops.length === 2 && /^\+\d$/.test(s.more), s);

  w.__run("actionsExpanded=true"); flow(box, 780); w.fitActionChips(); s = state(box);
  T.check("expanded, every chip shows with a Less chip", box.querySelectorAll(".chip:not(.chip-more)").length === [...box.children].filter((c) => !c.hidden && !c.classList.contains("chip-more")).length && s.more === "Less");
  w.__run("actionsExpanded=false");

  box.innerHTML = ""; ["Clipboard", "Plan my day", "Find a file"].forEach((l) => { const b = w.document.createElement("button"); b.className = "chip"; b.textContent = l; box.appendChild(b); });
  flow(box, 780); w.fitActionChips(); s = state(box);
  T.check("a set that fits one row gets no +N chip", s.tops.length === 1 && s.more === "");
  T.done();
})().catch((e) => { console.log("FAIL threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
