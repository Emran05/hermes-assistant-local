// R2-27 — a failed tool-budget save keeps its error message on screen.
//
// send() set S.err after a failed POST and then awaited load() to re-sync the
// card (so the optimistic switch reverts). load() reset S.err to "" on every
// successful read, so the failure message vanished in the same tick and the
// switch just silently flipped back. load() now owns a separate S.loadErr;
// the card renders both, each on its own line.
//
// Evals the REAL dashboard/aux_toolbudget.js in node with a stubbed fetch and
// no document (paint() no-ops), the pattern t_toolbudget_js.js uses. No DOM,
// no dashboard, no network.
//
// Fails on the old code (with send/load exposed on window.hermesToolBudget,
// which this change also adds): S.err is "" after a failed save, and the card
// HTML does not carry the save error.
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const file = path.join(REPO, "dashboard", "aux_toolbudget.js");

global.window = global;
let GET = null, POST = null;            // what the stub answers, per verb
const calls = [];
global.fetch = async function (url, opts) {
  const verb = (opts && opts.method) || "GET";
  calls.push(verb + " " + url);
  const ans = verb === "POST" ? POST : GET;
  if (ans instanceof Error) throw ans;
  return { json: async () => ans };
};
(0, eval)(fs.readFileSync(file, "utf8"));
const TB = global.hermesToolBudget;

let pass = 0, fail = 0;
const ok = (n, c, d) => { if (c) { pass++; console.log("  ok   " + n); }
                          else { fail++; console.log("  FAIL " + n + (d !== undefined ? "  — " + JSON.stringify(d) : "")); } };

const LIVE = {
  ok: true, installed: true, enabled_in_config: true, restart_required: false,
  settings: { enabled: true, max_chars: 24000, spill: true },
  est_tokens: 6667, pct_window: 10.2, context_length: 65536,
  choices: [{ chars: 24000, est_tokens: 6667, pct_window: 10.2 }],
  stats: { today: { calls: 0 }, last: null }, observed: true
};

(async () => {
  ok("send and load are on the harness surface",
     typeof TB.send === "function" && typeof TB.load === "function");
  const S = TB.state;

  console.log("\n=== save fails, the re-sync read succeeds ===");
  S.data = LIVE; S.loaded = true;
  POST = { ok: false, error: "settings.json is read-only" };
  GET = LIVE;
  const r = await TB.send({ enabled: false }, "Saved.");
  ok("send() answers null", r === null, r);
  ok("it re-read the card after the failed save",
     calls.slice(-2).join(",") === "POST /api/tool/budget,GET /api/tool/budget", calls);
  ok("the save error SURVIVES the re-sync", S.err === "settings.json is read-only", S.err);
  ok("no read error is invented", S.loadErr === "", S.loadErr);
  let html = TB.cardHTML(S);
  ok("the card shows the save error", html.indexOf("settings.json is read-only") >= 0);
  ok("the switch reverted to the server's state (enabled)", S.data.settings.enabled === true);

  console.log("\n=== save fails AND the re-sync read fails ===");
  POST = null;                            // no JSON at all -> generic message
  GET = new Error("connection refused");
  await TB.send({ max_chars: 8000 }, "Saved.");
  ok("the generic save error is kept", S.err === "The change could not be saved.", S.err);
  ok("the read error is reported separately",
     S.loadErr === "The dashboard did not answer /api/tool/budget.", S.loadErr);
  html = TB.cardHTML(S);
  ok("the card shows both",
     html.indexOf("The change could not be saved.") >= 0 &&
     html.indexOf("The dashboard did not answer /api/tool/budget.") >= 0);

  console.log("\n=== the next good save and read clear them ===");
  POST = LIVE; GET = LIVE;
  await TB.send({ enabled: true }, "Saved.");
  ok("a successful save clears the save error", S.err === "", S.err);
  // review: the POST answers the full payload, so it is a fresh read too —
  // a stale "did not answer" line must not outlive a save that worked
  ok("a successful save clears a stale read error", S.loadErr === "", S.loadErr);
  ok("the card after a successful save carries no error line",
     TB.cardHTML(S).indexOf('class="tberr"') < 0);
  await TB.load();
  ok("a successful read clears the read error", S.loadErr === "", S.loadErr);
  html = TB.cardHTML(S);
  ok("and the card carries no error line", html.indexOf('class="tberr"') < 0);

  console.log("\n=== a later read never wipes a save error ===");
  POST = { ok: false, error: "nope" };
  await TB.send({ spill: false });
  GET = LIVE;
  await TB.load();
  ok("S.err is still the save error after another load()", S.err === "nope", S.err);

  console.log("\nTESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  console.log("  FAIL threw " + (e && e.stack || e));
  console.log("\nTESTS " + pass + " passed " + (fail + 1) + " failed");
  process.exit(1);
});
