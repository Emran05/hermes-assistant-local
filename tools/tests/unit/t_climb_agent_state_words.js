// t_climb_agent_state_words.js — design climb (2026-11-12): the model's
// state is said ONE way everywhere.
//
// BEFORE: with the model idle-suspended the header pill said "sleeping", the
// Settings power row "Pause agent — Agent is running" with a green dot, the
// Agent view "Watching … on watch", and the Local AI block two bare dashes.
//
// AFTER (real index.html + aux_agent.js / aux_settings_shell.js in the mini
// DOM, /api/models answering each state):
//   awake  -> Agent view "Watching"; Settings "Agent is running" (green dot)
//   asleep -> "Asleep" / "it wakes on your next message"; Settings caption
//             "Asleep — wakes on your next message", the dot not green; the
//             Local AI dashes give way to one line saying the model is asleep
//   paused -> "Paused"; Settings "Resume agent" / "Paused <time>" (unchanged)
// Round 3: an "On watch — …" ticker fact no longer restates the state while
// asleep or paused (the banner right above names it): it becomes the bare
// fact, "Nothing needs you right now" — never "On watch", never the state.
// The Pause control stays a Pause control (title "Pause agent") while asleep.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}
function models(over) {
  const m = require("./_a11y_dom.js").DEFAULT_MODELS;
  return Object.assign(JSON.parse(JSON.stringify(m)), over);
}
async function settle(w, n) { for (let i = 0; i < (n || 6); i++) await w.__tick(); }

async function agentCase(label, over, want) {
  const w = await bootDashboard({ aux: ["aux_agent.js"], routes: { "/api/models": models(over) } });
  const d = w.document;
  w.__run("loadModels()"); await settle(w);
  // index.html's renderAIInfo with no metrics: both figures are "—"
  w.__run("renderAIInfo()"); await settle(w);
  w.__run("window.__agentTest.refreshIdle()"); await settle(w, 2);
  const s1 = d.getElementById("ag-s1").textContent, s2 = d.getElementById("ag-s2").textContent;
  const note = d.getElementById("ag-ai-note");
  const tk = d.getElementById("agent-ticker");
  T.check(label + ": the Agent view says '" + want.s1 + "'", s1 === want.s1, s1);
  T.check(label + ": ...with a detail that matches", want.s2.test(s2), s2);
  T.check(label + ": the ticker carries the state for its dot", tk && tk.getAttribute("data-model") === want.st, tk && tk.getAttribute("data-model"));
  T.check(label + ": Local AI says the state instead of bare dashes",
    note && want.note.test(note.textContent) &&
    d.getElementById("ai-tps").parentNode.classList.contains("ag-nodata") &&
    d.getElementById("ai-ttft").parentNode.classList.contains("ag-nodata"),
    note && note.textContent);
  T.check(label + ": an 'On watch' ticker fact follows the state",
    w.__agentTest.stateFact("On watch — nothing needs you right now") === want.fact,
    w.__agentTest.stateFact("On watch — nothing needs you right now"));
  T.check(label + ": no page error", w.__errors.length === 0, w.__errors.map(String));
}

async function killCase(label, over, want) {
  const w = await bootDashboard({ aux: ["aux_settings_shell.js"], routes: { "/api/models": models(over) } });
  const d = w.document;
  w.__run("setView('mind')"); await settle(w, 10);
  const ttl = d.getElementById("set-killttl"), cap = d.getElementById("set-killcap"), k = d.getElementById("set-kill");
  T.check(label + ": the power row title is '" + want.ttl + "'", ttl && ttl.textContent === want.ttl, ttl && ttl.textContent);
  T.check(label + ": ...caption " + want.cap, cap && (want.cap.test ? want.cap.test(cap.textContent) : cap.textContent === want.cap), cap && cap.textContent);
  T.check(label + ": ...dot state " + want.cls, k && want.clsTest(k.classList), k && k.className);
}

(async function () {
  console.log("\n1. Agent view and Local AI");
  await scenario("awake", function () {
    return agentCase("awake", {}, { s1: "Watching", s2: /./, st: "awake",
      note: /No reply measured yet/, fact: "On watch — nothing needs you right now" });
  });
  await scenario("asleep", function () {
    return agentCase("asleep", { idle_suspended: true }, { s1: "Asleep", s2: /wakes on your next message/,
      st: "asleep", note: /^Model asleep — it wakes on your next message/, fact: "Nothing needs you right now" });
  });
  await scenario("paused", function () {
    return agentCase("paused", { paused: true }, { s1: "Paused", s2: /paused/, st: "paused",
      note: /^Agent paused/, fact: "Nothing needs you right now" });
  });

  console.log("\n2. Settings power row");
  await scenario("kill awake", function () {
    return killCase("awake", {}, { ttl: "Pause agent", cap: "Agent is running", cls: "plain",
      clsTest: function (c) { return !c.contains("asleep") && !c.contains("paused"); } });
  });
  await scenario("kill asleep", function () {
    return killCase("asleep", { idle_suspended: true }, { ttl: "Pause agent", cap: "Asleep — wakes on your next message",
      cls: "asleep", clsTest: function (c) { return c.contains("asleep") && !c.contains("paused"); } });
  });
  await scenario("kill paused", function () {
    return killCase("paused", { paused: true }, { ttl: "Resume agent", cap: /^(Agent is paused|Paused \d)/, cls: "paused",
      clsTest: function (c) { return c.contains("paused") && !c.contains("asleep"); } });
  });
  T.done();
})();
