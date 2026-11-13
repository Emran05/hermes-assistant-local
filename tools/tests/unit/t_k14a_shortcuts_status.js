// 2026-10-08 audit, P3 prose ("Frontend"): "shortcuts ... error messages are
// wiped by the immediate re-render" — a Shortcuts run that failed showed its
// error under the row, and the card re-rendered 1.2s later and erased it,
// usually before it could be read (and VoiceOver never announced it).
//
// The REAL aux_shortcuts.js on the real index.html in the mini DOM.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

(async function main() {
  console.log("Shortcuts: a run's result survives the card's refresh");
  const w = await bootDashboard({
    aux: ["aux_shortcuts.js"],
    routes: {
      "/api/shortcuts": { ok: true, available: true, exposed_count: 1, pending: [], recent: [],
        shortcuts: [{ id: "s1", name: "Log Water", exposed: true, risk: { level: "low" } }] },
      "/api/shortcuts/run": { ok: false, error: "The shortcut could not be found." }
    }
  });
  const d = w.document;
  w.__run("setView('mind')");
  await w.__run("shortcutsPanel()"); await w.__tick();
  const st = function () { return d.querySelector('#mind-extra-shortcuts .sb-status[data-st="s1"]'); };
  T.check("the status line is a live status region", st() && st().getAttribute("role") === "status");
  d.querySelector('#mind-extra-shortcuts .sb-run').click();
  await w.__tick();
  T.check("a failed run shows its error", /could not be found/.test(st().textContent) && st().classList.contains("err"), st().textContent);
  const before = st();
  w.__flushTimers(1); await w.__tick();                 // the 1.2s refresh
  T.check("the card re-rendered", st() !== before);
  T.check("...and the error is still there to read", /could not be found/.test(st().textContent) && st().classList.contains("err"), st().textContent);
  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
