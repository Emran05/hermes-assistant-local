// E23 (docs/audits/2026-11-09-codebase-audit.md) — the setup sheet must not
// write defaults over the owner's settings when it could not read them.
//
// BEFORE: load() built the sheet's preferences from `j.prefs || {}` with
// hard-coded defaults (News on, briefings on, idle 10 min, prewarm on, quiet
// hours 22:00-07:00) and never checked `j.ok`; a 500 `{ok:false}` showed no
// error at all, and a thrown fetch opened the sheet anyway. Continue on the
// welcome and prefs steps then POSTed those defaults to /api/onboarding/apply,
// turning News and briefings back on for an owner who had them off.
//
// AFTER: until a real state has loaded in THIS open, Continue on the welcome
// and prefs steps is disabled (and act()/Enter refuse it too), `ok:false` is
// an error, the error line says so and a Retry button re-reads the state;
// once it loads, Continue posts the owner's real values.
//
// Runs the REAL dashboard/aux_onboarding.js in the unit tier's mini DOM.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const { createWindow } = require(path.join(REPO, "tools/tests/unit/_a11y_dom.js"));
const ONB = fs.readFileSync(path.join(REPO, "dashboard/aux_onboarding.js"), "utf8");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}
const REAL = { ok: true, done: true, sysinfo: { ram_gb: 64 }, detect: { claude_cli: false },
  recommendation: {}, prefs: { briefings: false, news: false, prewarm: false, idle_min: 30,
    idle_enabled: true, web_research: false, quiet_hours: { start: "21:00", end: "09:00" } } };

async function run(mode) {
  const w = createWindow({});
  const posts = [];
  let fail = true;
  w.__routes = {
    "/api/onboarding/state": function (u) {
      if (/brief=1/.test(u)) return { ok: true, done: true, brief: true };
      if (!fail) return REAL;
      if (mode === "throw") return { __reject: "Load failed" };
      if (mode === "500") return { __status: 500, body: { ok: false, error: "AttributeError" } };
      if (mode === "okfalse") return { ok: false, error: "boom" };
      return null;                               // "null": an empty answer
    },
    "/api/onboarding/apply": function (u, init) {
      const b = JSON.parse(init.body); posts.push(b); return { ok: true, errors: [], applied: b };
    },
    "/api/claude/foryou": { enabled: false }, "/api/models": { models: [] }
  };
  w.__run(ONB, "aux_onboarding.js"); await w.__tick();
  const ob = w.hermesOnboarding;
  await ob.open({ rerun: true }); await w.__tick();
  const sheet = () => w.document.getElementById("onb-sheet");
  const go = () => sheet().querySelector(".onb-go");
  const err = sheet().querySelector(".onb-err");
  check(mode + ": the error line says the state could not be read",
        err && !err.hidden && /could not read/i.test(err.textContent), err && err.textContent);
  check(mode + ": welcome Continue is disabled", go() && go().disabled === true, go() && go().outerHTML);
  w.__click(go()); await w.__tick(8);
  // Enter on the sheet, and the act() path, refuse too
  sheet().dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await w.__tick(8);
  check(mode + ": nothing was posted from the welcome step", posts.length === 0, posts);
  check(mode + ": still on the welcome step", ob.steps()[0] === "welcome" &&
        /Continue/.test(go().textContent) && go().disabled, go().textContent);
  // jump to prefs (the API go(), as a stuck page might): still refused
  ob.go(ob.steps().indexOf("prefs")); await w.__tick();
  check(mode + ": prefs Continue is disabled too", go().disabled === true);
  check(mode + ": nothing was posted from the prefs step", posts.length === 0, posts);
  const retry = sheet().querySelector('.onb-foot button[data-act="retry"]');
  check(mode + ": a Retry button is offered", !!retry && !retry.disabled);
  // Retry while still failing: still refused
  w.__click(retry); await w.__tick(12);
  check(mode + ": a failing Retry keeps Continue disabled", go().disabled === true &&
        !sheet().querySelector(".onb-err").hidden, go().outerHTML);
  // Retry once the dashboard answers
  fail = false;
  w.__click(sheet().querySelector('.onb-foot button[data-act="retry"]')); await w.__tick(12);
  check(mode + ": after a good Retry, Continue is enabled and the error is gone",
        go().disabled === false && sheet().querySelector(".onb-err").hidden, go().outerHTML);
  check(mode + ": ...and the switches show the REAL values (News off)",
        !sheet().querySelector('input[data-pref="news"]') ||
        !sheet().querySelector('input[data-pref="news"]').checked);
  w.__click(go()); await w.__tick(12);
  const b = posts[posts.length - 1] || {};
  check(mode + ": Continue now posts the owner's values, not defaults",
        posts.length === 1 && b.news === false && b.briefings === false && b.idle_min === 30 &&
        b.prewarm === false && b.quiet_hours && b.quiet_hours.start === "21:00", posts);
}

(async function () {
  for (const m of ["500", "okfalse", "throw", "null"]) await run(m);
  // a GOOD first load leaves Continue enabled from the start (no regression)
  const w = createWindow({});
  w.__routes = { "/api/onboarding/state": REAL, "/api/onboarding/apply": { ok: true, errors: [], applied: {} },
    "/api/claude/foryou": { enabled: false }, "/api/models": { models: [] } };
  w.__run(ONB, "aux_onboarding.js"); await w.__tick();
  await w.hermesOnboarding.open({ rerun: true }); await w.__tick();
  const s = w.document.getElementById("onb-sheet");
  check("a good load: Continue enabled, no Retry, no error",
        !s.querySelector(".onb-go").disabled && !s.querySelector('[data-act="retry"]') &&
        s.querySelector(".onb-err").hidden);
})().catch((e) => { check("harness ran: " + (e && e.stack || e), false); })
  .then(() => {
    console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
    process.exit(fails.length ? 1 : 0);
  });
