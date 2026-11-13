// B26 — the Agent page's Screen rail "Send to agent" box works, survives the
// rail's 4 s poll, and the Vitals card is reachable (Settings › Insights).
//
// Both had been unreachable since the Console and Desktop views were folded
// into the Agent page: setView('desktop') and setView('console') both alias to
// 'agent', so #view-desktop (the only place the compose box was wired) and
// #view-console (the only place the Vitals card was mounted) are never shown,
// and the Screen rail rendered the same compose markup hidden and unwired.
//
// Evals the REAL aux_desktop.js, aux_agent.js and aux_metrics.js against the
// small DOM in _k14b_dom.js with fetch mocked — no dashboard, no model.
"use strict";
const fs = require("fs");
const path = require("path");
const D = require("./_k14b_dom.js");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}
const flush = () => new Promise((r) => setImmediate(r));
async function settle(n) { for (let i = 0; i < (n || 15); i++) await flush(); }
const src = (f) => fs.readFileSync(path.join(REPO, "dashboard", f), "utf8");

function reset() {
  ["sendMsg", "attachments", "__auxAgent", "__agentTest", "renderDesktop", "deskWireCompose",
   "__deskSetView", "mindExtras", "loadConsole", "esc", "animate", "REDUCE"].forEach(function (k) {
    try { delete global[k]; } catch (e) {}
  });
}

// ---- Screen rail ------------------------------------------------------------
async function rail(opts) {
  opts = opts || {};
  reset();
  const env = D.makeEnv();
  env.document.readyState = "loading";          // neither module boots its shell
  const posts = [], sent = [];
  const state = { shots: { shots: [], count: 0, capturable: true, note: "" } };
  const extra = {
    fetch: async function (url, o) {
      const u = String(url).split("?")[0];
      if (o && o.method === "POST") posts.push({ url: u, body: o.body });
      let body = {};
      if (u === "/api/desktop/shots") body = state.shots;
      else if (u === "/api/desktop/timeline") body = { available: true, rows: [] };
      else if (u === "/api/chat") body = { ok: true, job: "j1" };
      else if (u === "/api/chat/poll") body = { done: true, reply: "opened it" };
      return { status: 200, ok: true, json: async () => body };
    }
  };
  if (!opts.noChat) extra.sendMsg = async function (t) { sent.push(t); };
  D.install(env, extra);
  env.document.body.innerHTML =
    '<div class="composer"><textarea id="input"></textarea><button id="send">Send</button></div>' +
    '<div id="view-agent"><div id="agent-rail"><div class="railpane" data-rail="screen" id="rail-screen">' +
    '<div class="railstub">Loading the Screen panel…</div></div></div></div>';
  // eslint-disable-next-line no-eval
  (0, eval)(src("aux_desktop.js"));
  // eslint-disable-next-line no-eval
  (0, eval)(src("aux_agent.js"));
  const T = window.__agentTest;
  await T.mountScreenRail(true);
  await settle();
  const pane = document.getElementById("rail-screen");
  return { env, T, pane, posts, sent, state, q: (s) => pane.querySelector(s) };
}

async function main() {
  console.log("Screen rail: the compose card is live");
  let r = await rail();
  check("the rail renders the compose card", !!r.q("#desk-ctl-card") && !!r.q("#desk-send") && !!r.q("#desk-task"));
  check("and no agent CSS hides it in the rail",
    !/#agent-rail\s+#desk-ctl-card\s*\{\s*display\s*:\s*none/.test(src("aux_agent.js")));
  r.q("#desk-task").value = "Open Safari";
  r.q("#desk-send").click();
  await settle();
  check("Send hands the task to the visible chat (sendMsg)", r.sent.length === 1 && r.sent[0] === "Open Safari", r.sent);
  check("the box clears", r.q("#desk-task").value === "", r.q("#desk-task").value);
  check("the status line says where it went", /chat/i.test(r.q("#desk-status").textContent), r.q("#desk-status").textContent);
  check("no second job is started behind the chat's back", !r.posts.some((p) => p.url === "/api/chat"), r.posts);
  check("Send is re-enabled when the turn ends", r.q("#desk-send").disabled === false);

  console.log("Screen rail: the 4 s poll keeps what is being typed");
  const task = r.q("#desk-task");
  task.focus();
  task.value = "Screenshot my scr";
  const oldLive = r.q("#desk-live-card");
  r.state.shots = { shots: [{ name: "s1.png", ts: 1, uri: "data:image/png;base64,AA==" }], count: 1, capturable: true, note: "" };
  await r.T.mountScreenRail(false);
  await settle();
  check("the same textarea is still in the pane", r.q("#desk-task") === task);
  check("its text survives", task.value === "Screenshot my scr", task.value);
  check("and it keeps keyboard focus", document.activeElement === task);
  check("the live card itself was refreshed", r.q("#desk-live-card") !== oldLive && !!r.q("#desk-live-card"));
  // several more polls, then one click must send exactly once (idempotent wiring)
  await r.T.mountScreenRail(false); await settle();
  await r.T.mountScreenRail(true); await settle();
  r.q("#desk-send").click();
  await settle();
  check("one click after many re-renders sends once", r.sent.length === 2 && r.sent[1] === "Screenshot my scr", r.sent);
  check("Cmd+Enter sends too", (function () {
    r.q("#desk-task").value = "What is on my screen?";
    r.q("#desk-task").dispatchEvent(D.makeEvent("keydown", { key: "Enter", metaKey: true }));
    return true;
  })());
  await settle();
  check("... once", r.sent.length === 3 && r.sent[2] === "What is on my screen?", r.sent);

  console.log("Screen rail: never clobbers the chat composer");
  document.getElementById("send").disabled = true;
  r.q("#desk-task").value = "Open Notes";
  r.q("#desk-send").click();
  await settle();
  check("while a turn is running nothing is sent", r.sent.length === 3, r.sent);
  check("the status says why", /still answering/i.test(r.q("#desk-status").textContent), r.q("#desk-status").textContent);
  check("and the typed task is kept", r.q("#desk-task").value === "Open Notes");
  document.getElementById("send").disabled = false;
  document.getElementById("input").value = "a half-written chat message";
  r.q("#desk-send").click();
  await settle();
  check("an unsent chat message is not thrown away", r.sent.length === 3, r.sent);
  check("the status says so", /unsent message/i.test(r.q("#desk-status").textContent), r.q("#desk-status").textContent);
  document.getElementById("input").value = "";
  r.q("#desk-task").value = "   ";
  r.q("#desk-send").click();
  await settle();
  check("an empty task is refused", r.sent.length === 3 && /type a task/i.test(r.q("#desk-status").textContent));

  console.log("Screen rail: without index.html's sendMsg it still runs (job path)");
  r = await rail({ noChat: true });
  r.q("#desk-task").value = "Open Chrome";
  r.q("#desk-send").click();
  await settle(30);
  const chat = r.posts.find((p) => p.url === "/api/chat");
  check("POST /api/chat carries the task", !!chat && JSON.parse(chat.body).message === "Open Chrome", r.posts);
  check("the reply lands in the status line", /opened it/.test(r.q("#desk-status").textContent), r.q("#desk-status").textContent);

  // ---- Vitals ---------------------------------------------------------------
  console.log("Vitals: reachable in Settings › Insights");
  const METRICS = { ok: true, turns: { n: 3, ttft_ms: { p50: 900, p95: 1400 }, turn_ms: { p50: 4000, p95: 9000 } },
    hub_api: { p50: 4, p95: 20 }, ram: {}, model: {}, approvals: {}, undo: {}, targets: {} };
  function vitalsEnv(shellFirst) {
    reset();
    const env = D.makeEnv();
    D.install(env, {
      fetch: async function (url) {
        const u = String(url).split("?")[0];
        return { status: 200, ok: true, json: async () => (u === "/api/metrics" ? METRICS : {}) };
      }
    });
    window.mindExtras = async function () {};          // expand.js's base
    env.document.body.innerHTML =
      '<div class="view" id="view-mind"></div><div class="view" id="view-console" hidden></div>';
    if (shellFirst) buildShell();
    return env;
  }
  function buildShell() {                               // what aux_settings_shell builds
    const vm = document.getElementById("view-mind");
    vm.insertAdjacentHTML("beforeend",
      '<div id="set-shell"><main id="set-panels"><section class="set-panel" id="sec-insights">' +
      '<header class="set-head"></header><div class="set-body"><div class="set-empty"></div>' +
      '<div data-legacy-slot></div></div></section></main></div>');
  }
  const inInsights = (card) => !!card && !!card.parentNode &&
    card.parentNode.parentNode === document.getElementById("sec-insights");

  // Settings restored at load: aux_metrics' first paint runs before the shell exists
  vitalsEnv(false);
  // eslint-disable-next-line no-eval
  (0, eval)(src("aux_metrics.js"));
  await settle();
  buildShell();                                         // DOMContentLoaded: the shell arrives
  await window.mindExtras();                            // loadCapabilities -> mindExtras
  await settle();
  let card = document.getElementById("vitals-card");
  check("the card exists", !!card);
  check("it is inside Settings › Insights", inInsights(card), card && card.parentNode && card.parentNode.id);
  check("not left in the never-shown #view-console", !document.getElementById("view-console").contains(card));
  check("it sits before the legacy-slot anchor (top of the panel)",
    !!card && card.nextSibling === document.querySelector("#sec-insights [data-legacy-slot]"));

  // the usual order: the shell exists, the owner opens Settings
  vitalsEnv(true);
  // eslint-disable-next-line no-eval
  (0, eval)(src("aux_metrics.js"));
  await window.mindExtras();
  await settle(30);
  card = document.getElementById("vitals-card");
  check("opening Settings mounts it in Insights", inInsights(card));
  check("and fills the tiles from /api/metrics",
    !!card && card.querySelectorAll(".vt-tile").length === 8, card && card.querySelectorAll(".vt-tile").length);
  check("the TTFT tile shows the p50", !!card && /900ms/.test(card.querySelector("#vt-grid").textContent));
  check("exactly one card", document.querySelectorAll("#vitals-card").length === 1);
  await window.mindExtras();
  await settle();
  check("a second Settings render reuses it", document.querySelectorAll("#vitals-card").length === 1 &&
    inInsights(document.getElementById("vitals-card")));

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
}
main().catch(function (e) {
  console.log("  FAIL crashed " + (e && e.stack || e));
  console.log("\nTESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
