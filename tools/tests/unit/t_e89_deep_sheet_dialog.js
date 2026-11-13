// E89 (docs/audits/2026-11-09-codebase-audit.md) — the Agent page's "Claude
// dialogues" sheet was a full-screen overlay with no dialog semantics: Esc did
// nothing, focus stayed behind it and Tab walked the page underneath.
// Now: role=dialog + aria-modal, focus moves to its close button on open,
// Tab stays inside, Esc (and the close button) close it and focus returns to
// the button that opened it.
//
// The REAL index.html + aux_agent.js run in the mini DOM (_a11y_dom.js).
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

function call(ts, task) { return { ts: ts, depth: "deep", model: "opus", ms: 4200, ok: true, task: task, response: "answer to " + task }; }

(async function main() {
  console.log("E89: the Claude dialogues sheet is a real dialog");
  const w = await bootDashboard({
    aux: ["aux_agent.js"],
    routes: { "/api/claude/recent": function () { return { ok: true, calls: [call(1700000200, "b"), call(1700000100, "a")] }; } }
  });
  const d = w.document;
  w.__run("setView('agent')");
  await w.__tick();
  const dm = d.getElementById("ag-deep");
  const sheet = dm && dm.querySelector(".dsheet");
  const btn = d.getElementById("ag-deepbtn");
  T.check("the sheet has dialog semantics", sheet && sheet.getAttribute("role") === "dialog" && sheet.getAttribute("aria-modal") === "true",
    sheet && [sheet.getAttribute("role"), sheet.getAttribute("aria-modal")]);
  T.check("...and a name", sheet && /Claude dialogues/.test(sheet.getAttribute("aria-label") || ""), sheet && sheet.getAttribute("aria-label"));

  btn.focus();
  w.__click(btn);
  await w.__tick();
  T.check("the sheet opens", dm.classList.contains("open"));
  T.check("focus moves into the sheet (its close button)", d.activeElement === d.getElementById("ag-deep-x"),
    d.activeElement && (d.activeElement.id || d.activeElement.tagName));

  // Tab stays inside: from the last focusable, Tab wraps to the first
  const inside = function () { return sheet.contains(d.activeElement); };
  let ok = true;
  for (let i = 0; i < 6; i++) {
    const ev = w.__key(d.activeElement, "Tab");
    if (!ev.defaultPrevented) {
      // the mini DOM does not move focus by itself; emulate the browser's
      // default move to the next focusable in the document
      const all = Array.from(d.querySelectorAll("button,input,select,textarea,a[href],[tabindex]")).filter(function (x) { return x.tabIndex >= 0 && !x.disabled; });
      const n = all[(all.indexOf(d.activeElement) + 1) % all.length];
      if (n) n.focus();
    }
    if (!inside()) { ok = false; break; }
  }
  T.check("Tab never leaves the open sheet", ok, d.activeElement && (d.activeElement.id || d.activeElement.className));
  let shift = true;
  for (let i = 0; i < 6; i++) {
    const ev = w.__key(d.activeElement, "Tab", { shiftKey: true });
    if (!ev.defaultPrevented) {
      const all = Array.from(d.querySelectorAll("button,input,select,textarea,a[href],[tabindex]")).filter(function (x) { return x.tabIndex >= 0 && !x.disabled; });
      const n = all[(all.indexOf(d.activeElement) - 1 + all.length) % all.length];
      if (n) n.focus();
    }
    if (!inside()) { shift = false; break; }
  }
  T.check("Shift+Tab never leaves it either", shift);

  const esc = w.__key(d.activeElement, "Escape");
  await w.__tick();
  T.check("Esc closes the sheet", !dm.classList.contains("open"));
  T.check("...and is consumed", esc.defaultPrevented);
  T.check("...and focus goes back to the Claude dialogues button", d.activeElement === btn,
    d.activeElement && (d.activeElement.id || d.activeElement.tagName));

  const later = w.__key(btn, "Escape");
  T.check("Esc with the sheet closed is not consumed by it", !later.defaultPrevented);
  const tab = w.__key(btn, "Tab");
  T.check("Tab with the sheet closed is not trapped", !tab.defaultPrevented);

  w.__click(btn); await w.__tick();
  w.__click(d.getElementById("ag-deep-x")); await w.__tick();
  T.check("the close button closes it and returns focus", !dm.classList.contains("open") && d.activeElement === btn,
    d.activeElement && (d.activeElement.id || d.activeElement.tagName));

  w.__click(btn); await w.__tick();
  w.__click(dm); await w.__tick();
  T.check("a click on the backdrop closes it and returns focus", !dm.classList.contains("open") && d.activeElement === btn);

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
