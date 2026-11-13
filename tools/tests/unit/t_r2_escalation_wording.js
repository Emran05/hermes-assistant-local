// Round-2 R2-34 — with Claude escalation off, the model menu's escalation row
// said "local model only — nothing leaves the Mac". That is false: the update
// check, news feeds, network widgets and Telegram all still go out with the
// switch off. The row now says only what the switch governs (Claude).
//
// Boots the REAL index.html in the mini DOM (_a11y_dom.js) and reads the row
// loadModels() renders for both switch states.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

(async function main() {
  console.log("R2-34: the escalation row claims only what the switch controls");
  try {
    for (const on of [false, true]) {
      const w = await bootDashboard({ routes: { "/api/claude/escalate": { enabled: on } } });
      await w.__run("loadClaudeEscalation()"); await w.__tick();
      await w.__run("loadModels()"); await w.__tick();
      const row = w.document.getElementById("mm-esc");
      const sub = row ? (row.querySelector(".rm2") || {}).textContent || "" : "";
      const st = on ? "on" : "off";
      T.check("[" + st + "] the escalation row renders", !!row);
      T.check("[" + st + "] it never claims nothing leaves the Mac", !/nothing leaves/i.test(sub), sub);
      if (!on) T.check("[off] it says no question goes to Claude", /no question goes to Claude/.test(sub), sub);
      else T.check("[on] it says hard questions also go to Claude", /hard questions also go to Claude/.test(sub), sub);
    }
  } catch (e) {
    T.check("ran without throwing", false, String(e && e.stack || e));
  }
  T.done();
})();
