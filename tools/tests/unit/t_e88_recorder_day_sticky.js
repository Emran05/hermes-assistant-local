// E88 (2026-11-09 audit, P3) — a Flight Recorder day group that is open only
// by default no longer collapses under the owner when a new day begins.
//
// recGroupRows() re-evaluated "the newest day is open" on every 3 s poll. With
// nothing yet today, "Yesterday" is open by default; the agent's first action
// of the day put a row in "Today", and on the next poll Yesterday collapsed:
// its rows, an open detail pane and the keyboard focus on one of its Undo
// buttons all vanished (focus fell to <body>). Now a group painted open by
// default stays open for the page's life, and the owner's own toggle still
// wins. Drives the REAL aux_recorder.js in the real page (mini DOM).
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

// a fixed local "now": Tue 27 Oct 2026, 15:00
const NOW = new Date(2026, 9, 27, 15, 0, 0).getTime() / 1000;
const YDAY = new Date(2026, 9, 26, 18, 0, 0).getTime() / 1000;
function row(id, ts, extra) {
  return Object.assign({ id: id, kind: "write", tool: "write_file", reversible: "yes", status: "done",
    source: "hub", target: "/p/f" + id + ".txt", ts: ts, undo_effect: "restore" }, extra || {});
}

async function main() {
  const realNow = Date.now;
  Date.now = function () { return NOW * 1000; };
  try {
    const st = { rows: [row(3, YDAY), row(2, YDAY - 60)] };
    const w = await bootDashboard({
      aux: ["aux_recorder.js"],
      routes: {
        "/api/recorder": function (url) {
          if (/[?&]id=/.test(url)) return row(3, YDAY, { summary: "wrote it" });
          return { recorder_ok: true, checkpoints_enabled: true, counts: {}, actions: st.rows };
        },
        "/api/recorder/retention": { ok: true, retain_days: 90, total: 2 }
      }
    });
    const d = w.document;
    await w.__run("loadRecorder()"); await w.__tick();
    d.getElementById("view-console").hidden = false;
    const list = d.getElementById("rec-list");
    const dayhd = function (label) {
      return Array.from(list.querySelectorAll(".rec-dayhd")).find(function (b) { return b.querySelector(".rec-daylbl").textContent === label; });
    };
    const poll = async function () { await w.__run("loadRecorder()"); await w.__tick(); };

    T.check("with nothing today, Yesterday is open by default", dayhd("Yesterday").getAttribute("aria-expanded") === "true");
    w.__click(list.querySelector('.rec-row[data-id="3"]')); await w.__tick();
    const undo2 = list.querySelector('[data-undo="2"]');
    undo2.focus();
    T.check("a detail pane is open and focus is on row 2's Undo",
      !!list.querySelector(".rec-detail") && d.activeElement === undo2);

    console.log("\n1. the first action of a new day arrives");
    st.rows = [row(9, NOW - 30)].concat(st.rows);
    await poll();
    const y = dayhd("Yesterday");
    T.check("Today is there and open", dayhd("Today") && dayhd("Today").getAttribute("aria-expanded") === "true");
    T.check("Yesterday did NOT collapse", y.getAttribute("aria-expanded") === "true" &&
      !d.getElementById(y.getAttribute("aria-controls")).hidden, y.getAttribute("aria-expanded"));
    T.check("the open detail pane is still under row 3", !!list.querySelector(".rec-detail") &&
      list.querySelector('.rec-row[data-id="3"]').nextSibling === list.querySelector(".rec-detail"));
    T.check("focus came back to row 2's Undo, not <body>",
      d.activeElement && d.activeElement.getAttribute && d.activeElement.getAttribute("data-undo") === "2",
      d.activeElement && d.activeElement.localName);
    await poll(); await poll();
    T.check("...and it stays open on later polls", dayhd("Yesterday").getAttribute("aria-expanded") === "true");

    console.log("\n2. the owner's own toggle still wins");
    dayhd("Yesterday").click();
    T.check("a click closes it", dayhd("Yesterday").getAttribute("aria-expanded") === "false");
    st.rows = [row(10, NOW - 20)].concat(st.rows);
    await poll();
    T.check("a rebuilding poll keeps it closed", dayhd("Yesterday").getAttribute("aria-expanded") === "false");

    console.log("\n3. the pure grouping is unchanged without the page-life state");
    const g = w.recGroupRows([row(9, NOW - 30), row(3, YDAY)], { now: NOW });
    T.check("defaults: Today open, Yesterday closed", g[0].open === true && g[1].open === false && g[1].defaultOpen === false);
    const g2 = w.recGroupRows([row(9, NOW - 30), row(3, YDAY)], { now: NOW, stickyOpen: { "2026-10-26": true } });
    T.check("a sticky day stays open", g2[1].open === true);
    const k = {}; k["2026-10-26"] = false;
    const g3 = w.recGroupRows([row(9, NOW - 30), row(3, YDAY)], { now: NOW, stickyOpen: { "2026-10-26": true }, dayOpen: k });
    T.check("...unless the owner closed it", g3[1].open === false);
    T.check("no page errors", w.__errors.length === 0, w.__errors.map(String));
  } finally {
    Date.now = realNow;
  }
  T.done();
}
main().catch(function (e) { console.log("  FAIL suite threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
