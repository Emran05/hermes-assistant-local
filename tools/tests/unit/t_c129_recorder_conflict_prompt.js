// C129 (audit 2026-10-21): the Flight Recorder's force-restore prompt says
// what /api/undo actually found.
//
// recUndo() used to answer EVERY conflict with "This file changed since the
// agent wrote it. Force-restore to the agent's version anyway?" — for a file
// that is gone, a path that is now a directory, or a state that could not be
// read, and it named the wrong version (a restore brings back the bytes from
// BEFORE the agent's turn). This drives the REAL recUndo in node: fetch is
// stubbed to answer a conflict with each server reason, confirm() captures the
// prompt and declines, so nothing is re-sent.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_recorder.js");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
global.relTime = () => "just now";
(0, eval)(fs.readFileSync(FILE, "utf8"));

const prompts = [];
let answer = null;
global.confirm = msg => { prompts.push(msg); return prompts.length === 1; };
global.fetch = async () => ({ json: async () => answer });

// the server's own reason/detail pairs (aux_recorder.py _conflict())
const CASES = {
  hash: "contents differ from what the agent wrote",
  size: "size is 10 bytes, was 7 when the agent finished",
  mtime: "modified since the agent finished",
  deleted: "this file no longer exists",
  created: "this file was created after the agent removed it",
  type: "this path is now a dir, not a file",
  unreadable: "cannot read the current state of /p/notes.txt (Permission denied)",
};

async function promptFor(reason) {
  prompts.length = 0;
  answer = { ok: false, conflict: true, error: "file changed since the agent wrote it",
             reason: reason, detail: CASES[reason], target: "/p/notes.txt" };
  recState.last = { actions: [{ id: 5, kind: "write", reversible: "yes",
                                target: "/p/notes.txt", status: "done" }] };
  recState.busy = {};
  await recUndo("5", false);
  return prompts[1] || "";
}

(async () => {
  console.log("\n1. each conflict reason gets its own prompt");
  const got = {};
  for (const r of Object.keys(CASES)) got[r] = await promptFor(r);
  check("a vanished file is not called 'changed'",
        /no longer exists/.test(got.deleted) && !/changed since/.test(got.deleted),
        got.deleted);
  check("a path that became a directory says so",
        /now a dir, not a file/.test(got.type) && !/changed since/.test(got.type), got.type);
  check("an unreadable state says it could not be checked",
        /could not be checked/.test(got.unreadable) && /Permission denied/.test(got.unreadable),
        got.unreadable);
  check("a re-created file says so", /created again/.test(got.created), got.created);
  check("a size change quotes the sizes", /size is 10 bytes, was 7/.test(got.size), got.size);
  check("a content change keeps the familiar wording",
        /changed since the agent wrote it/.test(got.hash), got.hash);
  check("every prompt names the file",
        Object.values(got).every(m => m.indexOf("/p/notes.txt") >= 0), got);

  console.log("\n2. it offers the right version");
  check("no prompt calls the restore target 'the agent's version'",
        Object.values(got).every(m => !/agent's version/.test(m)), got);
  check("every prompt says the version from before the agent's turn",
        Object.values(got).every(m => /before the agent's turn/.test(m)), got);

  console.log("\n3. declining sends nothing more");
  check("one confirm for the undo, one for the conflict, no third",
        prompts.length === 2, prompts.length);

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})();
