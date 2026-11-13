// R2-38 — with Claude escalation OFF the Settings › Claude Bridge row said
// "Local model only — nothing leaves the Mac". Escalation only governs
// Claude: the update check, news feeds, widget providers and Telegram still
// go out (the Data & Network card lists them), so the row overstated what the
// switch does. It now says what it means: no question goes to Claude. Same
// scope as the model-menu wording (R2-34, index.html, another branch).
//
// The REAL index.html + aux_settings_shell.js run in the mini DOM
// (_a11y_dom.js) against a stubbed /api/claude/escalate.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

(async function main() {
  console.log("R2-38: the escalation row's off-state wording is scoped to Claude");
  const state = { enabled: false };
  const w = await bootDashboard({
    aux: ["aux_settings_shell.js"],
    routes: {
      "/api/claude/escalate": function (url, init) {
        if (init && init.method === "POST") state.enabled = JSON.parse(init.body).enabled === true;
        return { enabled: state.enabled };
      }
    }
  });
  await w.__tick();
  const d = w.document;
  const sub = function () { const el = d.getElementById("set-esc-sub"); return el ? el.textContent : null; };
  T.check("the escalation row is rendered", sub() !== null);
  const off = sub() || "";
  T.check("off: it no longer claims nothing leaves the Mac", !/nothing leaves/i.test(off), off);
  T.check("off: it says no question goes to Claude", /no question goes to Claude/.test(off), off);
  T.check("off: it still says the local model answers", /^Local model only/.test(off), off);

  await w.__run("setClaudeEscalation(true)");
  await w.__tick();
  const on = sub() || "";
  T.check("on: the wording is unchanged", on === "Hard questions also go to Claude for deeper reasoning; everything else stays on the local model.", on);

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
