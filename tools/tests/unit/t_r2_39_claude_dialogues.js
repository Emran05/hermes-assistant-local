// R2-39 — the Agent page's "Claude dialogues" sheet (aux_agent.js) polls
// /api/claude/recent every 12 s and used to rebuild the whole list on every
// poll, so a row the user had expanded snapped shut and the list jumped back
// while they were reading it. The row heads were click-only <div>s as well.
// Now: no rebuild unless the set of calls changed (keyed by id + ts), a
// rebuild keeps expanded rows and the row in view where it was, and each head
// is a real <button> carrying aria-expanded.
//
// The REAL index.html + aux_agent.js run in the mini DOM (_a11y_dom.js);
// /api/claude/recent is a local route. No dashboard, no model, no Claude.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

function call(ts, task) { return { ts: ts, depth: "deep", model: "opus", ms: 4200, ok: true, task: task, response: "answer to " + task }; }

(async function main() {
  console.log("R2-39: Claude dialogues keep expanded rows and scroll across polls");
  const state = { calls: [call(1700000300, "third"), call(1700000200, "second"), call(1700000100, "first")] };
  const w = await bootDashboard({
    aux: ["aux_agent.js"],
    routes: { "/api/claude/recent": function () { return { ok: true, calls: state.calls.slice() }; } }
  });
  const d = w.document;
  w.__run("setView('agent')");
  await w.__tick();
  const A = w.__agentTest;
  A.openDeep();
  await w.__tick();
  const list = d.getElementById("ag-deep-list");
  const rows = function () { return Array.from(list.querySelectorAll(".dcall")); };
  const poll = async function () {
    const iv = w.__intervals.find(function (t) { return t.ms === 12000; });
    if (iv) iv.fn(); else await A.loadClaudeRecent();
    await w.__tick();
  };
  T.check("the sheet is open with one row per call", d.getElementById("ag-deep").classList.contains("open") && rows().length === 3, rows().length);
  T.check("the 12 s poll is running", !!w.__intervals.find(function (t) { return t.ms === 12000; }));

  const heads = rows().map(function (r) { return r.querySelector(".dc-head"); });
  T.check("every row head is a <button type=button>", heads.every(function (h) { return h && h.tagName === "BUTTON" && h.getAttribute("type") === "button"; }),
    heads.map(function (h) { return h && h.tagName; }));
  T.check("row heads start collapsed (aria-expanded=false)", heads.every(function (h) { return h.getAttribute("aria-expanded") === "false"; }));
  T.check("each head names the body it controls",
    heads.every(function (h) { const b = d.getElementById(h.getAttribute("aria-controls")); return !!b && b.classList.contains("dc-body") && h.parentNode.contains(b); }));
  heads[1].focus();
  T.check("a row head can take keyboard focus", d.activeElement === heads[1]);

  w.__click(heads[1]);
  T.check("clicking a head expands its row", rows()[1].classList.contains("open") && heads[1].getAttribute("aria-expanded") === "true");
  list.scrollTop = 120;
  const before = rows()[1];

  await poll();
  T.check("an unchanged poll leaves the DOM alone (same row node)", rows()[1] === before, rows().length);
  T.check("...the expanded row stays expanded", rows()[1].classList.contains("open") && rows()[1].querySelector(".dc-head").getAttribute("aria-expanded") === "true");
  T.check("...the scroll position is kept", list.scrollTop === 120, list.scrollTop);
  T.check("...and focus stays on the row head", d.activeElement === heads[1]);

  // a new escalation arrives at the top
  state.calls.unshift(call(1700000400, "fourth"));
  await poll();
  const r2 = rows();
  T.check("a changed list is re-rendered", r2.length === 4 && r2[0].querySelector(".dc-task").textContent === "fourth", r2.length);
  const second = r2.find(function (r) { return r.querySelector(".dc-task").textContent === "second"; });
  T.check("the row the user expanded is still expanded after the rebuild",
    second && second.classList.contains("open") && second.querySelector(".dc-head").getAttribute("aria-expanded") === "true");
  T.check("no other row was opened", r2.filter(function (r) { return r.classList.contains("open"); }).length === 1);
  T.check("keyboard focus moves to the rebuilt head of the same row (not back to <body>)",
    !!second && d.activeElement === second.querySelector(".dc-head"),
    d.activeElement && (d.activeElement.className || d.activeElement.tagName));
  T.check("the scroll position survives a rebuild (no layout: kept as is)", list.scrollTop === 120, list.scrollTop);

  // with layout: rows 40 px tall, the list's top at 100 px. The user scrolled
  // 45 px, so "fourth" (0-40) is above the fold and "third" (40-80) is the
  // first row in view, 5 px under the top edge. A new row is prepended: the
  // list must scroll by one row so "third" is exactly where it was.
  const proto = Object.getPrototypeOf(list);
  const realRect = proto.getBoundingClientRect;
  proto.getBoundingClientRect = function () {
    if (this === list) return { left: 0, top: 100, width: 600, height: 300, right: 600, bottom: 400 };
    if (this.classList && this.classList.contains("dcall") && this.parentNode === list) {
      const i = Array.prototype.indexOf.call(list.children, this);
      const top = 100 + i * 40 - list.scrollTop;
      return { left: 0, top: top, width: 600, height: 40, right: 600, bottom: top + 40 };
    }
    return realRect.call(this);
  };
  try {
    list.scrollTop = 45;
    state.calls.unshift(call(1700000500, "fifth"));
    await poll();
    const third = rows().find(function (r) { return r.querySelector(".dc-task").textContent === "third"; });
    const b = third.getBoundingClientRect();
    T.check("a rebuild keeps the row in view at the same place on screen", list.scrollTop === 85 && b.top === 95, { scrollTop: list.scrollTop, top: b.top });
  } finally { proto.getBoundingClientRect = realRect; }

  // and an emptied list still says so
  state.calls = [];
  await poll();
  T.check("an empty list shows the placeholder", rows().length === 0 && /No deep escalations yet/.test(list.textContent));

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
