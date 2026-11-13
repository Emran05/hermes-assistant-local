// t_p3_dictation_toggle_edits.js — flipping a dictation switch keeps the
// dictionary edits that have not been saved yet.
//
// 2026-10-08 audit, P3 frontend: "dictation toggle discards unsaved dictionary
// edits".  The cleanup radio and the "Keep what was dictated" switch each save
// ONE key through the same save(), which cleared `dirty` and reloaded — and
// load() only keeps the editor's working copy while `dirty` is set, so every
// row typed (or removed) since the last "Save changes" was replaced by the
// server's copy.
//
// The REAL aux_dictation.js is eval'd in node (the module documents the
// headless-harness contract) against a stubbed j(); nothing touches a dashboard.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + " " + JSON.stringify(detail === undefined ? "" : detail)); }
}

// The dashboard, as far as this card can see it.
const server = { cleanup: "rules", keep_history: false,
                 dictionary: { "kew bee": "kubectl" }, app_styles: {} };
const posts = [];
global.window = global;
global.document = undefined;
global.j = function (p, body) {
  if (body !== undefined) {
    posts.push([p, JSON.parse(JSON.stringify(body))]);
    Object.assign(server, body);
    return Promise.resolve({ ok: true });
  }
  return Promise.resolve({
    ok: true, running: true, install: { built: true },
    status: {}, settings: JSON.parse(JSON.stringify(server)), today: {}, recent: [] });
};
eval(fs.readFileSync(path.join(REPO, "dashboard", "aux_dictation.js"), "utf8"));
const DC = global.hermesDictation;
const S = DC.state;

(async function () {
  // The editor after the user added a replacement and has not saved it.
  S.dict = [["kew bee", "kubectl"], ["hermes", "Hermes"]];
  S.styles = [["com.apple.Terminal", "verbatim"]];
  S.dirty = true;

  await DC.save({ cleanup: "off" });
  check("the cleanup switch was saved", server.cleanup === "off", server.cleanup);
  check("it sent only its own key", posts.length === 1
        && JSON.stringify(Object.keys(posts[0][1])) === '["cleanup"]', posts);
  check("the unsaved replacement is still in the editor",
        JSON.stringify(S.dict) === JSON.stringify([["kew bee", "kubectl"], ["hermes", "Hermes"]]),
        S.dict);
  check("the unsaved per-app style is still in the editor",
        JSON.stringify(S.styles) === JSON.stringify([["com.apple.Terminal", "verbatim"]]),
        S.styles);
  check("and Save changes is still armed", S.dirty === true, S.dirty);

  await DC.save({ keep_history: true });
  check("the history switch keeps them too",
        S.dict.length === 2 && S.styles.length === 1 && S.dirty === true, [S.dict, S.styles]);

  // Save changes: the rules themselves are settled and re-read from the server.
  await DC.save({ dictionary: DC.mapFromRows(S.dict), app_styles: DC.mapFromRows(S.styles) });
  check("Save changes clears the dirty flag", S.dirty === false, S.dirty);
  check("and the editor now shows the saved copy",
        S.dict.length === 2 && server.dictionary.hermes === "Hermes", [S.dict, server.dictionary]);

  // A failed toggle save never claims anything was saved, and keeps the edits.
  S.dict = S.dict.concat([["foo", "bar"]]);
  S.dirty = true;
  const okJ = global.j;
  global.j = function (p, body) {
    if (body !== undefined) return Promise.resolve({ ok: false, error: "nope" });
    return okJ(p, body);
  };
  await DC.save({ cleanup: "model" });
  check("a failed save keeps the edits and the dirty flag",
        S.dict.length === 3 && S.dirty === true && /Could not save/.test(S.err), [S.dict, S.err]);
  global.j = okJ;

  console.log("\nTESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})().catch(function (e) {
  console.log("  FAIL harness threw " + (e && e.stack || e));
  console.log("\nTESTS " + pass + " passed " + (fail + 1) + " failed");
  process.exit(1);
});
