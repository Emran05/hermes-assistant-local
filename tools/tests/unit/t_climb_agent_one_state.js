// t_climb_agent_one_state.js — design climb round 3 (2026-11-12): the Agent
// view said "asleep" four times on one screen (banner, Local AI, the sidebar
// footer chip, the line under the composer).
//
// AFTER (real index.html + aux_agent.js in the mini DOM):
//   * the ticker line lives INSIDE the banner (#agent-hero), not floating
//     under the composer;
//   * asleep/paused, its idle fact is the bare fact ("Nothing needs you right
//     now") — the state word is the banner's alone; awake again, the line
//     goes back to "On watch — …", with one fact or several;
//   * the sidebar shows the state once: the Agent view hides the footer chip
//     (a copy of the header pill), the Local AI line stays.
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, harness, DEFAULT_MODELS } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_agent.js"), "utf8");
async function settle(w, n) { for (let i = 0; i < (n || 6); i++) await w.__tick(); }
const IDLE = "On watch — nothing needs you right now";

(async function () {
  const m = JSON.parse(JSON.stringify(DEFAULT_MODELS));
  m.idle_suspended = true;
  let w;
  try { w = await bootDashboard({ aux: ["aux_agent.js"], routes: { "/api/models": m } }); }
  catch (e) { T.check("booted", false, String(e && e.stack || e)); T.done(); return; }
  const d = w.document;
  w.__run("loadModels()"); await settle(w);
  w.__run("renderAIInfo()"); await settle(w);
  w.__run("window.__agentTest.refreshIdle()"); await settle(w, 2);

  const hero = d.getElementById("agent-hero"), tk = d.getElementById("agent-ticker");
  T.check("the ticker sits inside the banner", !!hero && !!tk && hero.contains(tk));
  T.check("...not as a loose line under the composer", tk && tk.parentNode !== d.getElementById("agent-center"));
  const line = d.getElementById("ag-tick").textContent;
  T.check("asleep: the line does not restate the state", line === "Nothing needs you right now" && !/asleep|paused|on watch/i.test(line), line);
  T.check("asleep: the banner names it", d.getElementById("ag-s1").textContent === "Asleep");
  T.check("the Local AI line keeps the sidebar's one state line", /Model asleep/.test((d.getElementById("ag-ai-note") || {}).textContent || ""));
  T.check("the Agent view hides the sidebar footer chip (the header pill says it)", /"#agent-side \.cs-foot\{display:none\}"/.test(SRC));

  // awake again: the "On watch" fact comes back (one fact)
  w.__run("agentIdle=false; agentPaused=false; window.__agentTest.refreshIdle()"); await settle(w, 2);
  T.check("awake, one fact: 'On watch — …' again", d.getElementById("ag-tick").textContent === IDLE, d.getElementById("ag-tick").textContent);

  // several facts, the stripped one on screen, then awake
  w.__run("agentIdle=true; window.__agentTest.refreshIdle()"); await settle(w, 2);
  if (w.__agentTest.setFacts) {
    w.__agentTest.setFacts([IDLE, "Last action 2m ago — ran terminal"]);
    d.getElementById("ag-tick").textContent = "Nothing needs you right now";
    w.__run("agentIdle=false; window.__agentTest.refreshIdle()"); await settle(w, 2);
    T.check("awake, several facts: the stripped line gets its 'On watch' back", d.getElementById("ag-tick").textContent === IDLE, d.getElementById("ag-tick").textContent);
  }
  T.check("stateFact leaves other facts alone while asleep",
    (w.__run("agentIdle=true"), w.__agentTest.stateFact("Last action 2m ago — ran terminal")) === "Last action 2m ago — ran terminal");
  T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})();
