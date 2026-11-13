// E01 (2026-11-09 audit, P1) — the Flight Recorder's side of "Undo never
// moves a file it cannot prove the agent created".
//
// aux_recorder.py now says per row what Undo would do: `undo_why` on a write
// it cannot undo (an excluded .env, a log, a gitignored or >10 MB file, a
// patch to a file the snapshot never held) with reversible "no", and
// `undo_effect` ("restore" | "trash" | "untrash") on one it can. This suite
// drives the REAL aux_recorder.js in the real page (mini DOM, _a11y_dom.js):
//   1. a not-undoable row has NO Undo button and says why (chip tooltip and
//      the detail pane), BEFORE any click;
//   2. the confirm text is truthful: a created file "will be moved to the
//      undo trash", a snapshotted one "restored", an unchecked one says both
//      and that nothing changes without an earlier copy; the old text promised
//      a restore for every write;
//   3. an Undo that moved a file to the trash says where (the API's `trash`
//      used to be dropped), and a refusal shows the server's reason.
// Fetches answer from in-memory routes; nothing touches the network.
"use strict";
const path = require("path");
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
void REPO;

const NOW = Math.floor(Date.now() / 1000);
const WHY = "Hermes has no earlier copy of this file, so it cannot be undone here: " +
            "Hermes' snapshots never include files matching '.env'";
function row(id, extra) {
  return Object.assign({ id: id, kind: "write", tool: "write_file", reversible: "yes", status: "done",
    source: "hub", target: "/p/f" + id + ".txt", ts: NOW - id, has_snapshot: true }, extra || {});
}

async function main() {
  const st = {
    rows: [row(1, { target: "/p/.env", reversible: "no", undo_why: WHY }),
           row(2, { target: "/p/new.txt", undo_effect: "trash" }),
           row(3, { target: "/p/app.py", tool: "patch", undo_effect: "restore" }),
           row(4, { target: "/p/old.txt" }),
           row(5, { target: "/p/back.txt", tool: "undo", undo_effect: "untrash" }),
           row(6, { target: "/p/refused.txt" }),
           row(7, { target: "/p/app.py", tool: "undo", source: "dashboard" }),
           row(8, { target: "/p/mine-now.txt", undo_effect: "trash" }),
           row(9, { target: "/p/gone.txt", undo_effect: "trash" })],
    undo: null, posts: 0
  };
  const w = await bootDashboard({
    aux: ["aux_recorder.js"],
    routes: {
      "/api/recorder": function (url) {
        if (/[?&]id=1\b/.test(url))
          return Object.assign(row(1, { target: "/p/.env", reversible: "no", undo_why: WHY }),
            { snapshot_ref: { short: "abc12345", commit: "abc12345" }, undo_note: WHY });
        return { recorder_ok: true, checkpoints_enabled: true, counts: {}, actions: st.rows };
      },
      "/api/recorder/retention": { ok: true, retain_days: 90, total: 9 },
      "/api/undo": function (url, init) {
        const b = JSON.parse(init.body || "{}");
        st.undo = b; st.posts++;
        if (b.id === 8 && !b.force) return { ok: false, conflict: true, reason: "hash",
          detail: "contents differ from what the agent wrote", target: "/p/mine-now.txt" };
        if (b.id === 8) return { ok: true, restored_to: "(removed)", file: "/p/mine-now.txt",
          trash: "/h/.hermes/dashboard/undo-trash/1795-mine-now.txt" };
        if (b.id === 5) return { ok: false, conflict: true, reason: "created",
          detail: "this file was created after the agent removed it", target: "/p/back.txt" };
        if (b.id === 9) return { ok: false, conflict: true, reason: "deleted",
          detail: "this file no longer exists", target: "/p/gone.txt" };
        if (b.id === 2) return { ok: true, restored_to: "(removed)", file: "/p/new.txt",
          trash: "/h/.hermes/dashboard/undo-trash/1794-new.txt", note: "moved" };
        if (b.id === 6) return { ok: false, error: "not undoable", detail: WHY };
        return { ok: true, restored_to: "abc12345" };
      }
    }
  });
  const d = w.document;
  await w.__run("loadRecorder()"); await w.__tick();
  d.getElementById("view-console").hidden = false;
  const list = d.getElementById("rec-list");
  const rowEl = function (id) { return list.querySelector('.rec-row[data-id="' + id + '"]'); };

  console.log("\n1. a write that cannot be undone says so before the click");
  T.check("row 1 (.env, reversible no) has NO Undo button", !rowEl(1).querySelector("[data-undo]"));
  const chip = rowEl(1).querySelector(".rchip");
  T.check("its chip reads irreversible and carries the reason as its tooltip",
    chip && chip.textContent === "irreversible" && chip.getAttribute("title") === WHY, chip && chip.getAttribute("title"));
  T.check("the undoable rows keep their Undo", [2, 3, 4, 5, 6, 7, 8, 9].every(function (i) { return !!rowEl(i).querySelector("[data-undo]"); }));
  w.__click(rowEl(1)); await w.__tick();
  const pane = list.querySelector(".rec-detail");
  T.check("the detail pane names the reason under 'undo'", !!pane && pane.textContent.indexOf(WHY) >= 0,
    pane && pane.textContent.slice(0, 200));

  console.log("\n2. the confirm text says what will really happen");
  const M = function (id) { return w.recConfirmMsg(w.recFind(id)); };
  T.check("a created file: it will be moved to the undo trash (and can be put back)",
    /moved? (it )?to the\s+undo trash/.test(M(2)) && /puts it back/.test(M(2)) && !/restored to the version/.test(M(2)), M(2));
  T.check("a snapshotted file: restored, nothing about the trash",
    /restored to the version from before/.test(M(3)) && !/trash/.test(M(3)), M(3));
  T.check("an unchecked row: restore, or the trash if created, or nothing without an earlier copy",
    /restored/.test(M(4)) && /undo trash/.test(M(4)) && /no earlier copy of it, nothing is changed/.test(M(4)), M(4));
  T.check("an undo of a trash move: put back, never overwriting", /back from the undo trash/.test(M(5)) && /Nothing at that path is ever overwritten/.test(M(5)), M(5));

  // (review) an undo row made by a restore: undoing it brings the agent's
  // version back, which is the opposite of "the version from before the agent's turn"
  T.check("an undo of a restore: 'what it was just before that undo', not 'before the agent's turn'",
    /^Undo this undo\?/.test(M(7)) && /the version the undo replaced/.test(M(7)) &&
    !/from before the agent's turn/.test(M(7)) && !/trash/.test(M(7)), M(7));

  console.log("\n3. the outcome is shown");
  let asked = null;
  w.confirm = function (m) { asked = m; return true; };
  await w.__run("recUndo('2', false)"); await w.__tick();
  T.check("the confirm the owner saw was the truthful one", asked === M(2));
  const note = list.querySelector(".rec-rowerr.note");
  T.check("after the undo the row says where the file went (the trash path)",
    !!note && note.textContent.indexOf("/h/.hermes/dashboard/undo-trash/1794-new.txt") >= 0, note && note.textContent);
  await w.__run("recUndo('6', false)"); await w.__tick();
  const err = Array.from(list.querySelectorAll(".rec-rowerr")).find(function (e) { return !/\bnote\b/.test(e.className); });
  T.check("a refusal shows the server's reason under its row", !!err && err.textContent.indexOf("no earlier copy") >= 0,
    err && err.textContent);

  console.log("\n4. the force prompt after a conflict says what a forced Undo of THIS row does");
  // (review) it read "Force-restore the version from before the agent's turn
  // anyway?" for every row — also for a created file, where the server moves
  // the file with the owner's later changes to the undo trash, and for an
  // undo-from-trash, where the server never overwrites and force does nothing.
  const C = function (id, reason, detail) {
    return w.recConflictMsg({ conflict: true, reason: reason, detail: detail || "", target: w.recFind(id).target }, w.recFind(id));
  };
  const c2 = C(8, "hash", "contents differ from what the agent wrote");
  T.check("a created file the owner edited since: moved AS IT IS NOW to the undo trash, not 'restored'",
    /no earlier version to\s+restore/.test(c2) && /later changes included/.test(c2) &&
    /undo trash/.test(c2) && !/Force-restore the version from before/.test(c2), c2);
  const c3 = C(3, "hash", "contents differ from what the agent wrote");
  T.check("a snapshotted file: the force-restore wording, nothing about the trash",
    /Force-restore the version from before the agent's turn anyway\?/.test(c3) && !/trash/.test(c3), c3);
  const c4 = C(4, "hash", "contents differ from what the agent wrote");
  T.check("an unchecked row: force-restore, or the trash if the agent created it",
    /Force-restore the version from before/.test(c4) && /moved to the undo trash instead/.test(c4), c4);

  let prompts = [];
  w.confirm = function (m) { prompts.push(m); return true; };
  st.undo = null; st.posts = 0;
  await w.__run("recUndo('8', false)"); await w.__tick();
  T.check("forcing a created file: the SECOND prompt is the truthful one, and only then force is sent",
    prompts.length === 2 && prompts[1] === c2 && st.undo && st.undo.force === true && st.posts === 2,
    JSON.stringify([prompts.length, st.undo, st.posts]));

  prompts = []; st.undo = null; st.posts = 0;
  await w.__run("recUndo('5', false)"); await w.__tick();
  const stopLine = Array.from(list.querySelectorAll(".rec-rowerr")).map(function (e) { return e.textContent; })
    .find(function (t) { return /never overwrites/.test(t); });
  T.check("put-back with something in the way: NO force prompt, no forced request, the row says what to do",
    prompts.length === 1 && st.posts === 1 && st.undo && st.undo.force === false &&
    !!stopLine && stopLine.indexOf("/p/back.txt") >= 0 && /move it aside/.test(stopLine),
    JSON.stringify([prompts.length, st.posts, st.undo, stopLine]));
  T.check("...and that row still has its Undo (nothing was undone)",
    !!rowEl(5) && !!rowEl(5).querySelector("[data-undo]") && !/undone/.test(rowEl(5).textContent));

  prompts = []; st.undo = null; st.posts = 0;
  await w.__run("recUndo('9', false)"); await w.__tick();
  const goneLine = Array.from(list.querySelectorAll(".rec-rowerr")).map(function (e) { return e.textContent; })
    .find(function (t) { return /already gone/.test(t); });
  T.check("a created file that is already gone: no force prompt, 'nothing to undo'",
    prompts.length === 1 && st.posts === 1 && !!goneLine, JSON.stringify([prompts.length, st.posts, goneLine]));

  T.check("no page errors", w.__errors.length === 0, w.__errors.map(String));
  T.done();
}
main().catch(function (e) { console.log("  FAIL suite threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
