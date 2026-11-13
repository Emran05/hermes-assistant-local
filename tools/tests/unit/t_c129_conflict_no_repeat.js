// C129 follow-up (round 2 of the 2026-10-21 audit): the force-restore prompt
// no longer says the same thing twice.
//
// For the 'created' and 'mtime' conflict reasons the prompt was its own head
// plus the server's detail, and the two are the same sentence in other words:
//   "This file was created again after the agent removed it: this file was
//    created after the agent removed it."
//   "This file was modified after the agent finished: modified since the
//    agent finished."
// The server detail is dropped when it repeats the head; every detail that
// adds something (sizes, the new type, the read error) is still quoted, and so
// is any OTHER detail a future server sends for those two reasons.
//
// The two server sentences are read out of the REAL aux_recorder.py, so a
// change there fails this test until the prompt is checked again; the prompt
// is built by the REAL recConflictMsg in aux_recorder.js. Fails on the old
// code: both prompts carry the repeat.
"use strict";
const fs = require("fs");
const path = require("path");
const ai = process.argv.indexOf("--repo");
const REPO = process.env.HERMES_REPO ||
             (ai > 0 ? path.resolve(process.argv[ai + 1]) : path.resolve(__dirname, "..", "..", ".."));
const JS = path.join(REPO, "dashboard", "aux_recorder.js");
const PY = path.join(REPO, "dashboard", "aux_recorder.py");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
global.relTime = () => "just now";
(0, eval)(fs.readFileSync(JS, "utf8"));

console.log("\n1. the server's own sentences (aux_recorder.py _conflict)");
const py = fs.readFileSync(PY, "utf8");
// adjacent Python string literals concatenate: join every "…" piece
const lit = src => (src.match(/"[^"]*"/g) || []).map(q => q.slice(1, -1)).join("");
const cm = /"detail":\s*\(((?:\s*"[^"]*")+)\s*\)\s*if st else/.exec(py);
const mm = /"reason":\s*"mtime",\s*"detail":\s*((?:\s*"[^"]*")+)/.exec(py);
const SERVER = { created: cm ? lit(cm[1]) : "", mtime: mm ? lit(mm[1]) : "" };
check("found the 'created' detail (" + SERVER.created + ")", !!SERVER.created);
check("found the 'mtime' detail (" + SERVER.mtime + ")", !!SERVER.mtime);

console.log("\n2. a detail that repeats the head is not quoted");
for (const reason of ["created", "mtime"]) {
  const msg = recConflictMsg({ reason, detail: SERVER[reason], target: "/p/notes.txt" });
  const first = msg.split("\n")[0];
  check(reason + ": the server sentence is gone (" + first + ")",
        first.toLowerCase().indexOf(SERVER[reason].toLowerCase()) < 0 &&
        first.indexOf(": ") < 0, first);
  check(reason + ": the head is still there, once",
        first === REC_CONFLICT_HEAD[reason] + ".", first);
  check(reason + ": the file and the question follow",
        msg.indexOf("/p/notes.txt") >= 0 && /before the agent's turn/.test(msg), msg);
}
const del = recConflictMsg({ reason: "deleted", detail: "this file no longer exists" });
check("deleted: still one sentence", del.split("\n")[0] === "This file no longer exists.", del);

console.log("\n3. a detail that adds something is still quoted");
const KEEP = {
  hash: "contents differ from what the agent wrote",
  size: "size is 10 bytes, was 7 when the agent finished",
  type: "this path is now a dir, not a file",
  unreadable: "cannot read the current state of /p/notes.txt (Permission denied)",
  mtime: "modified 3 minutes after the agent finished, by another app",
};
for (const reason of Object.keys(KEEP)) {
  const first = recConflictMsg({ reason, detail: KEEP[reason] }).split("\n")[0];
  check(reason + ": keeps \"" + KEEP[reason] + "\"", first.indexOf(": " + KEEP[reason]) > 0, first);
}

console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);
