// Regression test for E18 (update half, UI) — docs/audits/2026-11-09-codebase-audit.md.
//
// When POST /api/update/apply answers 409 reason "chat_busy", the update card
// shows the server's message in a confirm ending "Update anyway?". Cancel
// sends nothing more and shows the message; OK re-posts the SAME target with
// force: true (and only then). Runs the REAL dashboard/aux_update.js in node
// with a stubbed fetch/confirm (no document, so nothing paints).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_update.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? "  -- " + JSON.stringify(extra) : "")); }
}

function boot(answers, confirmAnswer) {
  const posts = [];
  const asked = [];
  const ctx = {
    console,
    setTimeout: () => 0, clearTimeout: () => {},   // no status polling in a test
    fetch: async (url, opts) => {
      const body = opts && opts.body ? JSON.parse(opts.body) : null;
      if (opts && opts.method === "POST") posts.push({ url, body });
      const a = answers.length ? answers.shift() : { status: 500, json: { ok: false } };
      return { status: a.status, json: async () => a.json };
    },
    confirm: (msg) => { asked.push(msg); return confirmAnswer; },
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx, { filename: "aux_update.js" });
  return { api: ctx.hermesUpdate, posts, asked };
}

const BUSY = { status: 409, json: { ok: false, reason: "chat_busy", busy: true,
  error: "A chat turn is still running. Updating now restarts the dashboard and the agent backend, which stops that turn and loses its reply. Wait for it to finish, or choose Update anyway." } };
const OK = { status: 200, json: { ok: true, started: true } };

(async () => {
  console.log("\n1. busy, the owner cancels");
  let t = boot([BUSY], false);
  check("the card exposes apply()", t.api && typeof t.api.apply === "function");
  await t.api.apply("v1.2.3");
  check("one POST, no force", t.posts.length === 1 && t.posts[0].body.target === "v1.2.3"
        && !("force" in t.posts[0].body), t.posts);
  check("the confirm quotes the server's message and asks Update anyway?",
        t.asked.length === 1 && t.asked[0].indexOf("A chat turn is still running") === 0
        && /Update anyway\?$/.test(t.asked[0]), t.asked);
  check("the card shows the message after a cancel",
        String(t.api.state.err).indexOf("A chat turn is still running") === 0, t.api.state.err);

  console.log("\n2. busy, the owner confirms");
  t = boot([BUSY, OK], true);
  await t.api.apply("latest");
  check("a second POST with the same target and force: true",
        t.posts.length === 2 && t.posts[1].body.target === "latest" && t.posts[1].body.force === true,
        t.posts);
  check("asked exactly once", t.asked.length === 1, t.asked);
  check("the card is now running the update", t.api.state.status && t.api.state.status.running === true,
        t.api.state.status);

  console.log("\n3. still busy after confirming: no loop");
  t = boot([BUSY, BUSY], true);
  await t.api.apply("latest");
  check("no third POST, no second confirm", t.posts.length === 2 && t.asked.length === 1, t.posts);
  check("the error is shown", String(t.api.state.err).indexOf("chat turn") >= 0, t.api.state.err);

  console.log("\n4. other refusals are unchanged (no confirm)");
  t = boot([{ status: 409, json: { ok: false, reason: "dirty", error: "uncommitted", dirty: ["a.py"] } }], true);
  await t.api.apply("latest");
  check("dirty: one POST, no confirm, message shown",
        t.posts.length === 1 && t.asked.length === 0 && /uncommitted.*a\.py/.test(t.api.state.err),
        t.api.state.err);

  console.log("\nTESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
