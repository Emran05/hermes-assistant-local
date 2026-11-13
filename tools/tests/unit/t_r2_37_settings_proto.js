// R2-37 — Settings deep links are looked up in two maps keyed by panel id and
// short name (aux_settings_shell.js). They were plain objects, so
// #settings/__proto__ (or /constructor — both pass the hash pattern) resolved
// to Object.prototype / Object: a truthy "panel" with no id. settingsShow then
// hid EVERY panel and Settings went blank.
//
// The REAL index.html + aux_settings_shell.js run in the mini DOM
// (_a11y_dom.js); no dashboard, no model.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

function shown(w) {
  return Array.from(w.document.querySelectorAll(".set-panel")).filter(function (p) { return !p.hidden; }).map(function (p) { return p.id; });
}

(async function main() {
  console.log("R2-37: #settings/__proto__ and friends fall back to Overview");
  const w = await bootDashboard({
    aux: ["aux_settings_shell.js"],
    before: function (w0) { w0.location.hash = "#settings/__proto__"; }
  });
  T.check("the shell built its panels", w.document.querySelectorAll(".set-panel").length >= 12,
    w.document.querySelectorAll(".set-panel").length);
  T.check("#settings/__proto__ on boot shows Overview, not a blank Settings", JSON.stringify(shown(w)) === '["sec-overview"]', shown(w));
  T.check("...and rewrites the hash to a real panel", w.location.hash === "#settings/overview", w.location.hash);

  ["constructor", "__proto__", "hasOwnProperty", "toString", "valueOf", "__defineGetter__"].forEach(function (k) {
    w.settingsShow(k);
    T.check("settingsShow(" + JSON.stringify(k) + ") shows exactly Overview", JSON.stringify(shown(w)) === '["sec-overview"]', shown(w));
  });

  w.location.hash = "#settings/constructor";
  w.dispatchEvent(new w.Event("hashchange"));
  await w.__tick();
  T.check("a hashchange to #settings/constructor shows Overview", JSON.stringify(shown(w)) === '["sec-overview"]', shown(w));

  w.settingsShow("models");
  T.check("a real short name still works", JSON.stringify(shown(w)) === '["sec-models"]', shown(w));
  w.settingsShow("sec-bridge");
  T.check("a real panel id still works", JSON.stringify(shown(w)) === '["sec-bridge"]', shown(w));
  const on = Array.from(w.document.querySelectorAll(".set-item.on")).map(function (b) { return b.getAttribute("data-sec"); });
  T.check("the rail marks the shown panel", JSON.stringify(on) === '["sec-bridge"]', on);

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
