// t_climb_trust_locked_auto.js — design climb (2026-11-12): the Trust
// table's floor-locked "Auto" was opacity:.45 (~1.6:1, nearly invisible) and
// said why only in a mouse tooltip.
//
// AFTER (real index.html + aux_trust.js in the mini DOM): the locked option
// is aria-disabled, carries the reason as its title AND as its accessible
// description (aria-describedby -> a visually hidden span naming the class),
// is no longer faded by opacity, still refuses a click (no POST), and a class
// whose Auto is allowed is untouched. The severity pills stay.
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_trust.js"), "utf8");

const PERMS = {
  ok: true, trusted: true, exists: true, recent: [], patterns: {}, pattern_overrides: [],
  classes: [
    { id: "credentials", label: "Credential & SSH writes", risk: "critical", tier: "never",
      "default": "ask", floor: "ask", auto_allowed: false, desc: "Writes to SSH keys.", pattern_count: 4 },
    { id: "git-destructive", label: "Git destructive", risk: "med", tier: "ask",
      "default": "ask", floor: "", auto_allowed: true, desc: "", pattern_count: 3 }
  ]
};

(async function () {
  let w;
  try {
    w = await bootDashboard({ aux: ["aux_trust.js"], routes: { "/api/permissions": PERMS } });
    await w.__run("window.trustPanel && window.trustPanel()");
    for (let i = 0; i < 8; i++) await w.__tick();
  } catch (e) { T.check("booted", false, String(e && e.stack || e)); T.done(); return; }
  const d = w.document;
  const rowL = d.querySelector('.tp-row[data-class="credentials"]');
  const rowA = d.querySelector('.tp-row[data-class="git-destructive"]');
  T.check("the Trust rows rendered", !!rowL && !!rowA);
  if (!rowL || !rowA) { T.done(); return; }
  const off = rowL.querySelector('.tp-seg b[data-tier="auto"]');
  T.check("the locked Auto is marked tp-off and aria-disabled", off && off.classList.contains("tp-off") && off.getAttribute("aria-disabled") === "true");
  const why = "Auto is locked for Credential & SSH writes: its safety floor is Ask, so it can never run silently.";
  T.check("...its title says why, naming the class", off && off.getAttribute("title") === why, off && off.getAttribute("title"));
  const desc = off && d.getElementById(off.getAttribute("aria-describedby"));
  T.check("...and so does its accessible description", !!desc && desc.textContent === why && off.contains(desc), desc && desc.textContent);
  T.check("...the description is visually hidden, not displayed", desc && desc.classList.contains("tp-vh"));
  const free = rowA.querySelector('.tp-seg b[data-tier="auto"]');
  T.check("an allowed Auto has none of it", free && !free.classList.contains("tp-off") && !free.hasAttribute("aria-disabled") && !free.hasAttribute("aria-describedby"));
  T.check("the severity pills stay", !!rowL.querySelector(".tp-chip") && !!rowA.querySelector(".tp-chip"));
  // the CSS: no opacity fade on the locked option any more
  T.check("the locked option is not faded by opacity", !/\.tp-seg b\.tp-off\{[^}]*opacity/.test(SRC));
  // clicking the locked option does nothing
  const posts0 = (w.__fetches || []).filter(function (f) { return f.init && f.init.method === "POST"; }).length;
  off.click(); for (let i = 0; i < 4; i++) await w.__tick();
  const posts1 = (w.__fetches || []).filter(function (f) { return f.init && f.init.method === "POST"; }).length;
  T.check("a click on the locked Auto posts nothing", posts1 === posts0, [posts0, posts1]);
  T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})();
