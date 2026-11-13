// t_climb_trust_keyboard.js — design climb round 3 (2026-11-12): the Trust
// table's Auto / Ask / Never options were bare <b> elements with click
// handlers only — no tab stop, no role, no keyboard.
//
// AFTER (real index.html + aux_trust.js in the mini DOM): each segment is a
// radiogroup named for its rule, each option a radio whose accessible name
// carries the rule's name, with ONE tab stop (the chosen option); arrows and
// Home/End move focus and skip the floor-locked Auto; Space/Enter picks
// through the same save path as a click (same POST body, same untrusted
// review, same first-Never confirm); a held key (repeat) never picks; the
// options have a visible focus ring.
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_trust.js"), "utf8");

function perms(trusted) {
  return {
    ok: true, trusted: trusted, exists: true, recent: [], patterns: {}, pattern_overrides: [], sha: "abc",
    classes: [
      { id: "credentials", label: "Credential & SSH writes", risk: "critical", tier: "never",
        "default": "ask", floor: "ask", auto_allowed: false, desc: "Writes to SSH keys.", pattern_count: 4 },
      { id: "git-destructive", label: "Git destructive", risk: "med", tier: "ask",
        "default": "ask", floor: "", auto_allowed: true, desc: "", pattern_count: 3 }
    ]
  };
}
function posts(w) {
  return (w.__fetches || []).filter(function (f) { return f.init && f.init.method === "POST"; })
    .map(function (f) { return JSON.parse(f.init.body); });
}
async function boot(trusted) {
  const w = await bootDashboard({ aux: ["aux_trust.js"], routes: { "/api/permissions": perms(trusted) } });
  await w.__run("window.trustPanel && window.trustPanel()");
  for (let i = 0; i < 8; i++) await w.__tick();
  // the Settings view is on screen (hidden elements cannot take focus)
  const vm = w.document.getElementById("view-mind");
  if (vm) vm.removeAttribute("hidden");
  return w;
}
const opt = (row, t) => row.querySelector('.tp-seg b[data-tier="' + t + '"]');

(async function () {
  let w;
  try { w = await boot(true); }
  catch (e) { T.check("booted", false, String(e && e.stack || e)); T.done(); return; }
  const d = w.document;
  const rowL = d.querySelector('.tp-row[data-class="credentials"]');
  const rowA = d.querySelector('.tp-row[data-class="git-destructive"]');
  T.check("the Trust rows rendered", !!rowL && !!rowA);
  if (!rowL || !rowA) { T.done(); return; }

  // ---- roles and names --------------------------------------------------
  const segL = rowL.querySelector(".tp-seg");
  T.check("the segment is a radiogroup", segL.getAttribute("role") === "radiogroup");
  T.check("...named for its rule", /Credential & SSH writes/.test(segL.getAttribute("aria-label") || ""), segL.getAttribute("aria-label"));
  ["auto", "ask", "never"].forEach(function (t) {
    const b = opt(rowL, t);
    T.check(t + " is a radio", b && b.getAttribute("role") === "radio");
    const name = b && b.getAttribute("aria-label") || "";
    T.check(t + "'s accessible name says the option and the rule",
      name.indexOf(b.textContent.trim().split(" ")[0]) === 0 && /Credential & SSH writes/.test(name), name);
  });
  T.check("aria-checked marks the chosen tier only",
    opt(rowL, "never").getAttribute("aria-checked") === "true" && opt(rowL, "ask").getAttribute("aria-checked") === "false");

  // ---- one tab stop per group, never the locked Auto ---------------------
  function stops(row) {
    return ["auto", "ask", "never"].filter(function (t) { return opt(row, t).getAttribute("tabindex") === "0"; });
  }
  T.check("one tab stop, on the chosen option (Never)", JSON.stringify(stops(rowL)) === '["never"]', stops(rowL));
  T.check("one tab stop, on the chosen option (Ask)", JSON.stringify(stops(rowA)) === '["ask"]', stops(rowA));
  const off = opt(rowL, "auto");
  T.check("the locked Auto is out of the tab order and aria-disabled",
    off.getAttribute("tabindex") === "-1" && off.getAttribute("aria-disabled") === "true");
  T.check("the options take focus", (opt(rowL, "never").focus(), d.activeElement === opt(rowL, "never")));

  // ---- arrows move focus, skipping the locked Auto, and save nothing ------
  w.__key(d.activeElement, "ArrowLeft");
  T.check("ArrowLeft moves focus Never -> Ask", d.activeElement === opt(rowL, "ask"));
  w.__key(d.activeElement, "ArrowLeft");
  T.check("ArrowLeft again skips the locked Auto (stays on Ask)", d.activeElement === opt(rowL, "ask"));
  w.__key(d.activeElement, "Home");
  T.check("Home lands on the first allowed option (Ask)", d.activeElement === opt(rowL, "ask"));
  w.__key(d.activeElement, "End");
  T.check("End lands on Never", d.activeElement === opt(rowL, "never"));
  w.__key(d.activeElement, "ArrowLeft");
  for (let i = 0; i < 4; i++) await w.__tick();
  T.check("moving focus posts nothing", posts(w).length === 0, posts(w));

  // ---- Space picks; a held Space does not ------------------------------
  w.__key(d.activeElement, " ", { repeat: true });
  for (let i = 0; i < 4; i++) await w.__tick();
  T.check("a repeated Space picks nothing", posts(w).length === 0, posts(w));
  const ev = w.__key(d.activeElement, " ");
  for (let i = 0; i < 8; i++) await w.__tick();
  T.check("Space is consumed (no page scroll)", ev.defaultPrevented);
  let p = posts(w);
  T.check("Space on Ask saves exactly what a click would",
    p.length === 1 && p[0].op === "set_class" && p[0]["class"] === "credentials" && p[0].tier === "ask" && p[0].sha === "abc", p);
  T.check("...the row now reads Ask and the tab stop moved with it",
    rowL.getAttribute("data-tier") === "ask" && JSON.stringify(stops(rowL)) === '["ask"]' &&
    opt(rowL, "ask").getAttribute("aria-checked") === "true" && opt(rowL, "never").getAttribute("aria-checked") === "false", stops(rowL));

  // ---- Enter on an allowed Auto -------------------------------------------
  opt(rowA, "ask").focus();
  w.__key(d.activeElement, "ArrowLeft");
  T.check("ArrowLeft reaches an allowed Auto", d.activeElement === opt(rowA, "auto"));
  w.__key(d.activeElement, "Enter", { repeat: true });
  for (let i = 0; i < 4; i++) await w.__tick();
  T.check("a repeated Enter picks nothing", posts(w).length === 1);
  w.__key(d.activeElement, "Enter");
  for (let i = 0; i < 8; i++) await w.__tick();
  p = posts(w);
  T.check("Enter on Auto saves Auto", p.length === 2 && p[1]["class"] === "git-destructive" && p[1].tier === "auto", p);

  // ---- the locked Auto cannot be picked from the keyboard either ----------
  off.focus();   // not focusable by tab, but even if something focuses it
  w.__key(off, " ");
  for (let i = 0; i < 6; i++) await w.__tick();
  T.check("Space on the locked Auto posts nothing", posts(w).length === 2, posts(w));

  // ---- focus ring ------------------------------------------------------
  T.check("the options have a visible focus ring", /\.tp-seg b:focus-visible\{outline:2px solid var\(--iris\)/.test(SRC));
  T.check("no page error", w.__errors.length === 0, w.__errors.map(String));

  // ---- untrusted: a keyboard pick goes through the re-trust review ---------
  let w2;
  try { w2 = await boot(false); }
  catch (e) { T.check("booted untrusted", false, String(e && e.stack || e)); T.done(); return; }
  const d2 = w2.document;
  const rowU = d2.querySelector('.tp-row[data-class="git-destructive"]');
  const asked = [];
  w2.confirm = function (m) { asked.push(String(m)); return false; };
  opt(rowU, "ask").focus();
  w2.__key(d2.activeElement, "ArrowRight");
  w2.__key(d2.activeElement, " ");
  for (let i = 0; i < 8; i++) await w2.__tick();
  T.check("over an untrusted policy, Space shows the re-trust review first",
    asked.length === 1 && /changed outside the dashboard/.test(asked[0]) && /Never/.test(asked[0]), asked);
  T.check("...and declining it saves nothing", posts(w2).length === 0, posts(w2));
  T.check("...and leaves the row as it was", rowU.getAttribute("data-tier") === "ask");
  T.check("no page error (untrusted)", w2.__errors.length === 0, w2.__errors.map(String));
  T.done();
})();
