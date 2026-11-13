// E110 (docs/audits/2026-11-09-codebase-audit.md) — a switch or radio on the
// setup sheet keeps keyboard focus.
//
// BEFORE: every switch/radio on the welcome and prefs steps repainted the
// sheet, which dropped focus to <body>, and paintNow then focused "Continue";
// a second Space press meant to toggle back pressed Continue instead (saved
// the step and advanced), and arrowing through a segmented control stopped
// after one move.
//
// AFTER: an in-place repaint gives focus back to the control that was used
// (same data-pref / data-seg+value / data-qh / data-alt); a step CHANGE still
// lands on the primary action.
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
const desc = (a) => a ? (a.tagName + " " + ["data-pref", "data-seg", "value", "data-act", "data-alt"]
  .map((k) => a.getAttribute && a.getAttribute(k) ? k + "=" + a.getAttribute(k) : "").join(" ")).trim() : "null";

(async function () {
  const w = createWindow({});
  const posts = [];
  w.__routes = {
    "/api/onboarding/state": { ok: true, done: true, sysinfo: {}, detect: { claude_cli: true },
      recommendation: {}, prefs: { prewarm: true, briefings: true, news: true } },
    "/api/onboarding/apply": function (u, init) { posts.push(JSON.parse(init.body)); return { ok: true, errors: [], applied: {} }; },
    "/api/claude/foryou": { enabled: false }, "/api/models": { models: [] }
  };
  w.__run(ONB, "aux_onboarding.js"); await w.__tick();
  const ob = w.hermesOnboarding;
  await ob.open({ rerun: true }); await w.__tick();
  const sheet = () => w.document.getElementById("onb-sheet");
  const ae = () => w.document.activeElement;
  async function change(el, checked) {
    el.focus();
    if (checked !== undefined) el.checked = checked;
    el.dispatchEvent(new w.Event("change", { bubbles: true }));
    await w.__tick();
  }

  console.log("== welcome: the network table's switches ==");
  let i = sheet().querySelector('input[data-pref="news"]');
  await change(i, false);
  check("toggling News keeps focus on the News switch (was: Continue)",
        ae().getAttribute("data-pref") === "news" && ae().checked === false, desc(ae()));
  check("...it is the NEW node (the sheet was repainted) and its label reads Off",
        ae() !== i && /Off/.test(ae().parentNode.textContent), ae().parentNode.textContent);
  await change(ae(), true);
  check("a second toggle flips it back instead of pressing Continue",
        ae().getAttribute("data-pref") === "news" && ae().checked === true && posts.length === 0,
        { f: desc(ae()), posts });
  const both = sheet().querySelectorAll('input[data-pref="news"]');
  await change(both[1], false);
  const after = sheet().querySelectorAll('input[data-pref="news"]');
  check("News is a switch on two rows: the SECOND one keeps focus when it is used",
        after.length === 2 && ae() === after[1], desc(ae()));
  await change(ae(), true);
  check("still on the welcome step", ob.steps()[0] === "welcome" &&
        !!sheet().querySelector('input[data-pref="news"]'));

  console.log("== prefs: switches, the idle radio group, the theme radio group ==");
  ob.go(ob.steps().indexOf("prefs")); await w.__tick();
  i = sheet().querySelector('input[data-pref="prewarm"]');
  await change(i, false);
  check("toggling Prewarm keeps focus on Prewarm", ae().getAttribute("data-pref") === "prewarm", desc(ae()));
  let r = sheet().querySelector('input[data-seg="idle"][value="5"]');
  await change(r, true);
  check("choosing idle 5 min keeps focus on that radio",
        ae().getAttribute("data-seg") === "idle" && ae().getAttribute("value") === "5", desc(ae()));
  r = sheet().querySelector('input[data-seg="idle"][value="20"]');
  await change(r, true);
  check("...and arrowing on to 20 min keeps it there too",
        ae().getAttribute("data-seg") === "idle" && ae().getAttribute("value") === "20", desc(ae()));
  r = sheet().querySelector('input[data-seg="theme"][value="dark"]');
  if (r) {
    await change(r, true);
    check("choosing the dark theme keeps focus on that radio",
          ae().getAttribute("data-seg") === "theme" && ae().getAttribute("value") === "dark", desc(ae()));
  }
  check("nothing was posted by any toggle", posts.length === 0, posts);

  console.log("== a step change still focuses the primary action ==");
  sheet().querySelector('input[data-pref="prewarm"]').focus();
  ob.go(ob.steps().indexOf("welcome")); await w.__tick();
  check("going to another step lands on Continue",
        ae() && ae().classList && ae().classList.contains("onb-go"), desc(ae()));
})().catch((e) => { check("harness ran: " + (e && e.stack || e), false); })
  .then(() => {
    console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
    process.exit(fails.length ? 1 : 0);
  });
