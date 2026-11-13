// E33 (2026-11-09 audit, P2) — an open Flight Recorder detail pane survives
// the 3 s poll.
//
// recRender() skipped its rebuild only while keyboard focus was inside the
// list (C99.1). A MOUSE click on a row focuses nothing, so every poll threw
// the open pane away, rebuilt it as "loading…" and fetched /api/recorder?id=
// again — the diff flickered, its scroll jumped to the top, and the server ran
// a checkpoint-diff subprocess every 3 s. Now:
//   1. an unchanged poll leaves the list (and the pane) alone whatever has focus;
//   2. a poll that DOES rebuild (a new row arrived) moves the SAME pane node
//      onto the rebuilt row: no refetch, the diff keeps its scroll;
//   3. the pane is refetched only when its own row changed (e.g. undone).
// Drives the REAL aux_recorder.js in the real page (mini DOM, _a11y_dom.js).
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

const NOW = Math.floor(Date.now() / 1000);
function row(id, extra) {
  return Object.assign({ id: id, kind: "write", tool: "write_file", reversible: "yes", status: "done",
    source: "hub", target: "/p/f" + id + ".txt", ts: NOW - 100 + id, has_snapshot: true,
    undo_effect: "restore" }, extra || {});
}

async function main() {
  const st = { rows: [row(9), row(8), row(7)] };
  const w = await bootDashboard({
    aux: ["aux_recorder.js"],
    routes: {
      "/api/recorder": function (url) {
        const m = /[?&]id=(\d+)/.exec(url);
        if (m) return Object.assign(row(+m[1]), { snapshot_ref: { short: "abc12345", commit: "abc12345" },
          diff: Array.from({ length: 200 }, function (_, i) { return "+line " + i; }).join("\n") });
        return { recorder_ok: true, checkpoints_enabled: true, counts: {}, actions: st.rows };
      },
      "/api/recorder/retention": { ok: true, retain_days: 90, total: 3 }
    }
  });
  const d = w.document;
  await w.__run("loadRecorder()"); await w.__tick();
  d.getElementById("view-console").hidden = false;
  const list = d.getElementById("rec-list");
  const detailFetches = function () { return w.__fetches.filter(function (f) { return /[?&]id=/.test(f.url); }).length; };
  const poll = async function () { await w.__run("loadRecorder()"); await w.__tick(); };

  // a MOUSE click on row 8 (a <div>: nothing takes focus)
  w.__click(list.querySelector('.rec-row[data-id="8"]')); await w.__tick();
  const pane0 = list.querySelector(".rec-detail"), pre0 = pane0 && pane0.querySelector("pre.diff");
  T.check("the pane opened with its diff, one detail fetch", !!pre0 && detailFetches() === 1, detailFetches());
  T.check("focus did not move into the list (the audit's trigger)", !list.contains(d.activeElement), d.activeElement && d.activeElement.localName);
  pre0.scrollTop = 400;

  console.log("\n1. an unchanged poll, no focus in the list");
  await poll(); await poll();
  T.check("no refetch of the detail", detailFetches() === 1, detailFetches());
  T.check("the same pane and the same <pre> are still on screen, scroll kept",
    list.querySelector(".rec-detail") === pane0 && pane0.querySelector("pre.diff") === pre0 && pre0.scrollTop === 400);

  console.log("\n2. a poll that rebuilds the list (a new row) keeps the pane");
  st.rows = [row(10)].concat(st.rows);
  await poll();
  const r8 = list.querySelector('.rec-row[data-id="8"]');
  T.check("the list was rebuilt (row 10 is there)", !!list.querySelector('.rec-row[data-id="10"]'));
  T.check("the SAME pane node sits right after the rebuilt row 8", !!r8 && r8.nextSibling === pane0, r8 && r8.nextSibling && r8.nextSibling.className);
  T.check("...with no refetch and its scroll kept", detailFetches() === 1 && pre0.scrollTop === 400, [detailFetches(), pre0.scrollTop]);
  await poll();
  T.check("and the next unchanged poll leaves it alone too", detailFetches() === 1 && list.querySelector(".rec-detail") === pane0);

  console.log("\n3. the pane refreshes when its own row changes");
  st.rows = st.rows.map(function (a) { return a.id === 8 ? Object.assign({}, a, { status: "undone" }) : a; });
  await poll();
  T.check("row 8 changed (undone): one refetch", detailFetches() === 2, detailFetches());
  T.check("exactly one detail pane in the list", list.querySelectorAll(".rec-detail").length === 1);
  await poll();
  T.check("…and then it is stable again", detailFetches() === 2, detailFetches());

  console.log("\n4. closing it still works");
  w.__click(list.querySelector('.rec-row[data-id="8"]')); await w.__tick();
  T.check("a click on the row closes the pane", !list.querySelector(".rec-detail"));
  await poll();
  T.check("and a poll does not bring it back", !list.querySelector(".rec-detail") && detailFetches() === 2);
  T.check("no page errors", w.__errors.length === 0, w.__errors.map(String));
  T.done();
}
main().catch(function (e) { console.log("  FAIL suite threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
