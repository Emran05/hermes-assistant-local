// E92 (docs/audits/2026-11-09-codebase-audit.md) — three polls kept running
// while the window was hidden: the header's 60 s /api/topbar (it calls the
// weather provider, so a hidden window kept the Open-Meteo egress alive), the
// Settings Shortcuts card's 10 s poll and the Memory panel's 15 s poll.
// Each now skips a tick while document.hidden and catches up once on
// visibilitychange when it missed one.
//
// The REAL index.html + aux_topbar.js / aux_shortcuts.js / aux_memory.js run
// in the mini DOM (_a11y_dom.js) against in-memory routes.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

function count(w, path) {
  return w.__fetches.filter(function (f) { return f.url.split("?")[0] === path; }).length;
}
function setHidden(w, hidden) {
  w.document.hidden = hidden;
  w.document.visibilityState = hidden ? "hidden" : "visible";
  w.document.dispatchEvent(new w.Event("visibilitychange"));
}
function interval(w, ms) { return w.__intervals.filter(function (t) { return t.ms === ms; }); }
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}

(async function main() {
  console.log("E92: background polls skip while the window is hidden");

  await scenario("topbar", async function () {
    const w = await bootDashboard({ aux: ["aux_topbar.js"], routes: {
      "/api/topbar": function () { return { weather: { configured: true, temp: 20, city: "X" }, claude: null }; } } });
    await w.__tick();
    const polls = interval(w, 60000);
    T.check("topbar: the 60 s intervals exist", polls.length >= 1, polls.length);
    const n0 = count(w, "/api/topbar");
    setHidden(w, true);
    for (const p of polls) await p.fn();
    await w.__tick();
    T.check("topbar: no /api/topbar while hidden", count(w, "/api/topbar") === n0, count(w, "/api/topbar") - n0);
    setHidden(w, false); await w.__tick();
    T.check("topbar: one catch-up poll on becoming visible", count(w, "/api/topbar") === n0 + 1, count(w, "/api/topbar") - n0);
    setHidden(w, true); setHidden(w, false); await w.__tick();
    T.check("topbar: no catch-up when nothing was missed", count(w, "/api/topbar") === n0 + 1, count(w, "/api/topbar") - n0);
    for (const p of polls) await p.fn();
    await w.__tick();
    T.check("topbar: polls normally while visible", count(w, "/api/topbar") === n0 + 2, count(w, "/api/topbar") - n0);
    T.check("topbar: no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("shortcuts", async function () {
    const w = await bootDashboard({ aux: ["aux_shortcuts.js"], routes: {
      "/api/shortcuts": function () { return { ok: true, available: true, exposed_count: 0, pending: [], recent: [],
        shortcuts: [{ id: "s1", name: "Log Water", exposed: false, risk: { level: "low", label: "local" } }] }; } } });
    w.__run("setView('mind')");
    await w.__run("shortcutsPanel()"); await w.__tick();
    const poll = interval(w, 10000).pop();
    T.check("shortcuts: the 10 s poll exists", !!poll);
    const n0 = count(w, "/api/shortcuts");
    setHidden(w, true);
    await poll.fn(); await poll.fn(); await w.__tick();
    T.check("shortcuts: no /api/shortcuts while hidden", count(w, "/api/shortcuts") === n0, count(w, "/api/shortcuts") - n0);
    setHidden(w, false); await w.__tick();
    T.check("shortcuts: one catch-up poll on becoming visible", count(w, "/api/shortcuts") === n0 + 1, count(w, "/api/shortcuts") - n0);
    await poll.fn(); await w.__tick();
    T.check("shortcuts: polls normally while visible", count(w, "/api/shortcuts") === n0 + 2, count(w, "/api/shortcuts") - n0);
    T.check("shortcuts: no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("memory", async function () {
    const st = { etag: "e1" };
    const w = await bootDashboard({ aux: ["aux_memory.js"], routes: {
      "/api/memory/list": function () { return { ok: true, files: [{ name: "USER.md", last_writer: "user" }], trash: [], limits: {} }; },
      "/api/memory/file": function () { return { ok: true, name: "USER.md", kind: "entries", entries: ["a"], char_used: 1, char_limit: 1000, etag: st.etag, size: 1 }; } } });
    w.renderMemoryPanel({}); await w.__tick(20);
    w.__run("curView='mind'");
    const poll = interval(w, 15000).pop();
    T.check("memory: the 15 s poll exists", !!poll);
    const n0 = count(w, "/api/memory/file");
    setHidden(w, true);
    await poll.fn(); await w.__tick();
    T.check("memory: no /api/memory/file while hidden", count(w, "/api/memory/file") === n0, count(w, "/api/memory/file") - n0);
    setHidden(w, false); await w.__tick(10);
    T.check("memory: one catch-up poll on becoming visible", count(w, "/api/memory/file") === n0 + 1, count(w, "/api/memory/file") - n0);
    await poll.fn(); await w.__tick();
    T.check("memory: polls normally while visible", count(w, "/api/memory/file") === n0 + 2, count(w, "/api/memory/file") - n0);
    T.check("memory: no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
