// t_climb_settings_glance.js — design climb round 3 (2026-11-12): Settings ›
// Overview was two short cards over ~400px of nothing. The shell now adds
// ONE "At a glance" card (#set-glance) of short rows, each a real fact and a
// button to the panel that owns it.
//
// The REAL index.html + aux_settings_shell.js run on the mini DOM with a route
// table. Pinned here:
//   * nothing is fetched for the card until the Overview is shown;
//   * the rows render from fixture payloads (assistant state + model, Needs
//     you counts, connections needing action, the doctor's LAST run from the
//     page's own state — /api/doctor is never called — and the version);
//   * a failed fetch drops only its own row (never a dash or a fake value);
//   * third-party text is escaped;
//   * a row opens its panel through settingsShow.
"use strict";
const path = require("path");
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

const MODELS = {
  active: "org/Evil-27B", paused: false, idle_suspended: true,
  models: [{ id: "org/Evil-27B", label: "Evil <img src=x onerror=alert(1)> 27B", downloaded: true, fit: "ok" }],
  thinking: { supported: false }, mem: { machine_gb: 64, soft_gb: 50 }, bg: null
};
const NY = { ok: true, now: [{ id: "a" }, { id: "b" }], today_count: 3, later_count: 0, never_count: 4 };
const MSG = { ok: true, available: false, reason: "Full Disk Access needed to read Messages.", conversations: [] };
const GOOG = { ok: true, connected: false, needs_reauth: false };
const GH = { ok: true, connected: true, attention: [] };
const VER = { version: "1.3.2<b>x</b>", commit: "abc1234" };
const GLANCE_URLS = ["/api/needsyou", "/api/messages", "/api/google/status", "/api/github/status", "/api/version"];

function routes(over) {
  return Object.assign({
    "/api/models": MODELS, "/api/needsyou": NY, "/api/messages": MSG,
    "/api/google/status": GOOG, "/api/github/status": GH, "/api/version": VER,
    "/api/doctor": function () { throw new Error("the glance must never run the doctor"); }
  }, over || {});
}
function glanceFetches(w) {
  return (w.__fetches || []).map(function (f) { return f.url; })
    .filter(function (u) { return GLANCE_URLS.some(function (g) { return u.indexOf(g) === 0; }) || u.indexOf("/api/doctor") === 0; });
}
async function settle(w) { for (let i = 0; i < 6; i++) { await w.__tick(); w.__flushTimers(1); } }
async function boot(over, before) {
  const w = await bootDashboard({ aux: ["aux_settings_shell.js"], routes: routes(over), before: before });
  await settle(w);
  return w;
}
async function showSettings(w) {
  w.__run("setView('mind')");
  await settle(w);
  await settle(w);
}
function rowText(d, key) {
  const r = d.querySelector('#set-glance .sg-row[data-glance="' + key + '"]');
  return r ? r.textContent : null;
}
function valText(d, key) {
  const r = d.querySelector('#set-glance .sg-row[data-glance="' + key + '"] .sg-v');
  return r ? r.textContent : null;
}

(async function () {
  // ---- 1. the full set ------------------------------------------------------
  let w;
  try {
    w = await boot({}, function (win) {
      // what the page already holds: aux_doctor's last run, aux_update's check
      win.hermesDoctor = { state: { data: { generated: 0, summary: { pass: 21, warn: 2, fail: 0 } } } };
      win.hermesUpdate = { state: { ver: null, check: { update_available: true, latest: "1.4.0", error: "" } } };
    });
  } catch (e) { T.check("booted", false, String(e && e.stack || e)); T.done(); return; }
  const d = w.document;
  T.check("nothing is fetched for the card before the Overview is shown", glanceFetches(w).length === 0, glanceFetches(w));
  T.check("no card before the Overview is shown", !d.getElementById("set-glance"));

  await showSettings(w);
  const card = d.getElementById("set-glance");
  T.check("showing Settings (Overview) renders the At a glance card", !!card);
  if (!card) { T.done(); return; }
  T.check("the card sits in the Overview panel's .set-body", card.parentNode && card.parentNode.classList.contains("set-body") &&
    card.closest("#sec-overview") === d.getElementById("sec-overview"));
  T.check("card markup is <h2> + <div class=\"body\">", card.children[0].tagName === "H2" && card.children[1].classList.contains("body"));
  T.check("it follows the Overview headline card", card.previousElementSibling && card.previousElementSibling.id === "mind-base-hero",
    card.previousElementSibling && card.previousElementSibling.id);
  const keys = Array.from(card.querySelectorAll(".sg-row")).map(function (r) { return r.getAttribute("data-glance"); });
  T.check("rows: assistant, needs you, connections, health, version", JSON.stringify(keys) ===
    '["assistant","needsyou","connections","health","version"]', keys);
  T.check("the assistant row says asleep and the model in use", /Asleep/.test(rowText(d, "assistant")) && /Evil/.test(rowText(d, "assistant")), rowText(d, "assistant"));
  T.check("third-party text is escaped (no element injected)", !card.querySelector("img") && !card.querySelector(".sg-v b") &&
    rowText(d, "assistant").indexOf("<img src=x onerror=alert(1)>") >= 0 && valText(d, "version").indexOf("1.3.2<b>x</b>") === 0,
    [rowText(d, "assistant"), rowText(d, "version")]);
  T.check("needs you: the counts", /2 now · 3 today/.test(rowText(d, "needsyou")), rowText(d, "needsyou"));
  T.check("connections: what needs action", /Messages needs Full Disk Access/.test(rowText(d, "connections")) &&
    /Google not connected/.test(rowText(d, "connections")) && !/GitHub/.test(rowText(d, "connections")), rowText(d, "connections"));
  T.check("health: the page's last doctor run, counted", /21 pass · 2 warn · 0 fail/.test(rowText(d, "health")), rowText(d, "health"));
  T.check("version: installed + the known update", /1\.4\.0 available/.test(rowText(d, "version")), rowText(d, "version"));
  T.check("the doctor is never run from here", !(w.__fetches || []).some(function (f) { return f.url.indexOf("/api/doctor") === 0; }));
  T.check("Needs-you is read without marking rows seen", (w.__fetches || []).some(function (f) { return f.url === "/api/needsyou?mark=0"; }));
  T.check("GitHub is read without the ambient scan", (w.__fetches || []).some(function (f) { return f.url === "/api/github/status?scan=0"; }));
  T.check("each glance URL is read once per showing", GLANCE_URLS.every(function (u) {
    return glanceFetches(w).filter(function (x) { return x.indexOf(u) === 0; }).length === 1; }), glanceFetches(w));
  // no timer re-reads it: fire every interval the page holds, flush timers
  const nT = glanceFetches(w).length;
  (w.__intervals || []).slice().forEach(function (t) { try { t.fn(); } catch (e) {} });
  await settle(w);
  T.check("no timer or interval re-reads it", glanceFetches(w).length === nT, [nT, glanceFetches(w)]);
  // a row opens its panel
  const conn = card.querySelector('.sg-row[data-glance="connections"]');
  T.check("rows are real buttons", conn && conn.tagName === "BUTTON" && conn.getAttribute("type") === "button");
  w.__click(conn); await settle(w);
  T.check("the connections row opens Connections", d.getElementById("sec-connections").hidden === false &&
    d.getElementById("sec-overview").hidden === true);
  const n0 = glanceFetches(w).length;
  w.__run("settingsShow('overview')"); await settle(w);
  T.check("coming back to the Overview reads the facts again (once)", glanceFetches(w).length === n0 + GLANCE_URLS.length - 1 ||
    glanceFetches(w).length === n0 + GLANCE_URLS.length, [n0, glanceFetches(w).length]);
  T.check("no page error", w.__errors.length === 0, w.__errors.map(String));

  // ---- 2. failures drop only their own row ----------------------------------
  let w2;
  try {
    w2 = await boot({ "/api/needsyou": { __status: 500, body: { ok: false } },
                      "/api/google/status": { __reject: "offline" } });
  } catch (e) { T.check("booted (failures)", false, String(e && e.stack || e)); T.done(); return; }
  await showSettings(w2);
  const d2 = w2.document;
  const keys2 = Array.from(d2.querySelectorAll("#set-glance .sg-row")).map(function (r) { return r.getAttribute("data-glance"); });
  T.check("a failed Needs-you read drops only that row (no doctor state, no health row)",
    JSON.stringify(keys2) === '["assistant","connections","version"]', keys2);
  T.check("a failed Google read is not reported as 'not connected'", !/Google/.test(rowText(d2, "connections") || ""), rowText(d2, "connections"));
  T.check("nothing renders a dash or a placeholder", !/(^|\s)[–—-](\s|$)/.test(
    Array.from(d2.querySelectorAll("#set-glance .sg-v")).map(function (v) { return v.textContent.replace(/ — wakes on your next message/, ""); }).join(" | ")));
  T.check("no page error (failures)", w2.__errors.length === 0, w2.__errors.map(String));

  // ---- 3. everything fails: no card at all ----------------------------------
  const dead = {};
  ["/api/models", "/api/needsyou", "/api/messages", "/api/google/status", "/api/github/status", "/api/version"]
    .forEach(function (u) { dead[u] = { __reject: "offline" }; });
  let w3;
  try { w3 = await boot(dead); } catch (e) { T.check("booted (dead)", false, String(e && e.stack || e)); T.done(); return; }
  await showSettings(w3);
  T.check("with every read failed there is no empty card", !w3.document.getElementById("set-glance"));
  T.done();
})();
