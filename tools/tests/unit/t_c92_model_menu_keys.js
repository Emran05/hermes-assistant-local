// C92 (docs/audits/2026-10-21-codebase-audit.md) — the header model menu must
// be operable from the keyboard and readable by VoiceOver: switch model, Pause
// agent, Wake, Thinking and Claude escalation used to be click-only <div>s.
//
// Runs the REAL index.html (markup + inline script) in the mini DOM
// (_a11y_dom.js) with every API answered locally — no dashboard, no model.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

function post(w, path) {
  return w.__fetches.filter(function (f) { return f.url.split("?")[0] === path && f.init && f.init.method === "POST"; });
}
function body(f) { try { return JSON.parse(f.init.body || "{}"); } catch (e) { return {}; } }
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}

(async function main() {
  console.log("C92: model menu rows are keyboard-operable menu items");
  let w = await bootDashboard();
  const d = w.document, $ = function (id) { return d.getElementById(id); };
  const menu = $("model-menu"), pill = $("model-pill");

  await scenario("semantics", async function () {
    T.check("the menu is role=menu", menu.getAttribute("role") === "menu", menu.getAttribute("role"));
    T.check("the pill says it opens a menu", pill.getAttribute("aria-haspopup") === "menu", pill.getAttribute("aria-haspopup"));
    T.check("the pill reports collapsed while closed", pill.getAttribute("aria-expanded") === "false", pill.getAttribute("aria-expanded"));
    const rows = Array.from(menu.querySelectorAll(".mmi"));
    T.check("one row per roster model", rows.length === 3, rows.length);
    T.check("every model row is a focusable menu item",
      rows.length > 0 && rows.every(function (r) { return r.getAttribute("role") === "menuitem" && r.tabIndex === 0; }),
      rows.map(function (r) { return [r.getAttribute("role"), r.getAttribute("tabindex")]; }));
    const huge = rows.find(function (r) { return r.dataset.id === "mlx-community/Huge-70B"; });
    T.check("a row that cannot fit is reachable but aria-disabled", huge && huge.getAttribute("aria-disabled") === "true" && huge.tabIndex === 0);
    const act = rows.find(function (r) { return r.dataset.active === "1"; });
    T.check("the running model is marked current", act && act.getAttribute("aria-current") === "true");
    const pw = $("mm-power"), tk = $("mm-think"), es = $("mm-esc"), bg = $("mm-bg");
    T.check("Pause agent is a focusable menu item", pw && pw.getAttribute("role") === "menuitem" && pw.tabIndex === 0);
    T.check("Thinking is a menuitemcheckbox carrying its state",
      tk && tk.getAttribute("role") === "menuitemcheckbox" && tk.getAttribute("aria-checked") === "false" && tk.tabIndex === 0);
    T.check("Claude escalation is a menuitemcheckbox carrying its state",
      es && es.getAttribute("role") === "menuitemcheckbox" && es.getAttribute("aria-checked") === "false" && es.tabIndex === 0);
    T.check("the read-only background-lane row is not a control", bg && bg.getAttribute("role") === "none" && bg.tabIndex < 0);
  });

  await scenario("open from the keyboard", async function () {
    pill.focus();
    pill.click();                               // Enter/Space on a <button> = click with detail 0
    T.check("Enter on the pill opens the menu", !menu.hidden);
    await w.__tick();
    T.check("the pill reports expanded while open", pill.getAttribute("aria-expanded") === "true", pill.getAttribute("aria-expanded"));
    const first = menu.querySelector('[role^="menuitem"]');
    T.check("focus moves into the menu (first row)", d.activeElement === first, d.activeElement && (d.activeElement.id || d.activeElement.className));
    T.check("focus is still inside after the open-time rebuild", menu.contains(d.activeElement) && d.activeElement.dataset.id === "mlx-community/Primary-27B",
      d.activeElement && d.activeElement.className);
  });

  await scenario("arrow keys", async function () {
    const items = Array.from(menu.querySelectorAll('[role^="menuitem"]')).filter(function (x) { return !x.closest("[hidden]"); });
    w.__key(d.activeElement, "ArrowDown");
    T.check("ArrowDown moves to the next row", d.activeElement === items[1], d.activeElement && d.activeElement.className);
    w.__key(d.activeElement, "End");
    T.check("End moves to the last row", d.activeElement === items[items.length - 1], d.activeElement && d.activeElement.id);
    w.__key(d.activeElement, "Home");
    T.check("Home moves to the first row", d.activeElement === items[0]);
    w.__key(d.activeElement, "ArrowUp");
    T.check("ArrowUp from the first row wraps to the last", d.activeElement === items[items.length - 1]);
    const det = items[0].querySelector(".mmdt");
    det.focus();
    w.__key(det, "ArrowDown");
    T.check("ArrowDown from a row's Details button moves to the next row", d.activeElement === items[1]);
  });

  await scenario("focus survives the 30s rebuild", async function () {
    const small = menu.querySelector('.mmi[data-id="mlx-community/Small-9B"]');
    small.focus();
    await w.__run("loadModels()");
    await w.__tick();
    const now = d.activeElement;
    T.check("the rebuilt menu has new nodes", now !== small);
    T.check("focus is back on the same model's row", now && now.classList.contains("mmi") && now.dataset.id === "mlx-community/Small-9B",
      now && now.className);
    const det = menu.querySelector('.mmdt[data-det="mlx-community/Small-9B"]');
    det.focus();
    await w.__run("loadModels()"); await w.__tick();
    T.check("...and on the same Details toggle", d.activeElement && d.activeElement.classList.contains("mmdt") &&
      d.activeElement.dataset.det === "mlx-community/Small-9B");
  });

  await scenario("Enter / Space activate rows", async function () {
    const small = menu.querySelector('.mmi[data-id="mlx-community/Small-9B"]');
    small.focus();
    w.__key(small, "Enter");
    await w.__tick();
    const sw = post(w, "/api/models/switch");
    T.check("Enter on a model row switches to it", sw.length === 1 && body(sw[0]).id === "mlx-community/Small-9B", sw.map(body));
    w.__flushTimers(1);

    // reopen for the next rows
    w = await bootDashboard();
    const d2 = w.document, m2 = d2.getElementById("model-menu"), p2 = d2.getElementById("model-pill");
    p2.focus(); p2.click(); await w.__tick();
    const huge = m2.querySelector('.mmi[data-id="mlx-community/Huge-70B"]');
    huge.focus(); w.__key(huge, "Enter"); await w.__tick();
    T.check("Enter on a row that cannot fit does nothing", post(w, "/api/models/download").length === 0 && post(w, "/api/models/switch").length === 0);
    const det = huge.querySelector(".mmdt");
    det.focus(); const ev = w.__key(det, "Enter"); await w.__tick();
    T.check("Enter on a nested Details button is left to the button", !ev.defaultPrevented && post(w, "/api/models/download").length === 0);

    const tk = d2.getElementById("mm-think");
    tk.focus(); const sp = w.__key(tk, " "); await w.__tick();
    const th = post(w, "/api/models/thinking");
    T.check("Space on Thinking toggles it", th.length === 1 && body(th[0]).enabled === true, th.map(body));
    T.check("Space does not also scroll the page", sp.defaultPrevented);
  });

  await scenario("Claude escalation + Pause from the keyboard", async function () {
    w = await bootDashboard();
    const d3 = w.document, m3 = d3.getElementById("model-menu"), p3 = d3.getElementById("model-pill");
    p3.focus(); p3.click(); await w.__tick();
    const es = d3.getElementById("mm-esc");
    es.focus();
    w.__key(es, "Enter"); w.__key(es, "Enter");          // a key repeat must not flip it twice
    await w.__tick();
    const ce = post(w, "/api/claude/escalate");
    T.check("Enter on Claude escalation flips it once", ce.length === 1 && body(ce[0]).enabled === true, ce.map(body));

    p3.focus(); if (m3.hidden) p3.click(); await w.__tick();
    const pw = d3.getElementById("mm-power");
    pw.focus(); w.__key(pw, "Enter"); await w.__tick();
    T.check("Enter on Pause agent pauses it", post(w, "/api/agent/pause").length === 1);
    T.check("the menu closes", m3.hidden);
    T.check("focus goes back to the pill, not <body>", d3.activeElement === p3, d3.activeElement && d3.activeElement.tagName);
  });

  await scenario("Wake row", async function () {
    const r = JSON.parse(JSON.stringify(require("./_a11y_dom.js").DEFAULT_MODELS));
    r.idle_suspended = true;
    w = await bootDashboard({ routes: { "/api/models": r } });
    const d4 = w.document, p4 = d4.getElementById("model-pill");
    p4.focus(); p4.click(); await w.__tick();
    const wk = d4.getElementById("mm-wake");
    T.check("Wake model now is a focusable menu item", wk && wk.getAttribute("role") === "menuitem" && wk.tabIndex === 0);
    wk.focus(); w.__key(wk, "Enter"); await w.__tick();
    T.check("Enter on Wake wakes the model", post(w, "/api/agent/wake").length === 1);
  });

  // review: Enter/Space fire on keydown, and a held key auto-repeats. The
  // tail of one press must not fire a row again (Thinking, Resume, Wake have
  // no busy guard), nor re-open the menu from the pill once a row closed it.
  await scenario("a held key fires a row once", async function () {
    const r = JSON.parse(JSON.stringify(require("./_a11y_dom.js").DEFAULT_MODELS));
    r.idle_suspended = true;
    w = await bootDashboard({ routes: { "/api/models": r } });
    const d6 = w.document, m6 = d6.getElementById("model-menu"), p6 = d6.getElementById("model-pill");
    p6.focus(); p6.click(); await w.__tick();
    const tk = d6.getElementById("mm-think");
    tk.focus();
    const ev = w.__key(tk, " ", { repeat: true }); await w.__tick();
    T.check("an auto-repeated key on a row does not fire it", post(w, "/api/models/thinking").length === 0 && ev.defaultPrevented,
      post(w, "/api/models/thinking").map(body));
    const wk = d6.getElementById("mm-wake");
    wk.focus(); w.__key(wk, "Enter"); await w.__tick();
    T.check("(control) Enter on Wake wakes, closes the menu and returns to the pill",
      post(w, "/api/agent/wake").length === 1 && m6.hidden && d6.activeElement === p6);
    const ev2 = w.__key(p6, "Enter", { repeat: true });
    T.check("the rest of the held Enter, now on the pill, is swallowed (no re-open)", ev2.defaultPrevented);
  });

  await scenario("Esc, Tab-out, ArrowDown on the pill", async function () {
    w = await bootDashboard();
    const d5 = w.document, m5 = d5.getElementById("model-menu"), p5 = d5.getElementById("model-pill");
    p5.focus(); p5.click(); await w.__tick();
    w.__key(d5.activeElement, "ArrowDown");
    w.__key(d5.activeElement, "Escape");
    await w.__tick();
    T.check("Esc closes the menu", m5.hidden);
    T.check("Esc hands focus back to the pill", d5.activeElement === p5);
    T.check("the pill reports collapsed again", p5.getAttribute("aria-expanded") === "false");

    w.__key(p5, "ArrowDown"); await w.__tick();
    T.check("ArrowDown on the pill opens the menu", !m5.hidden);
    T.check("...with focus on its first row", m5.contains(d5.activeElement) && d5.activeElement.getAttribute("role") === "menuitem");

    const input = d5.getElementById("input");
    input.focus(); await w.__tick();
    T.check("tabbing out of the menu closes it", m5.hidden);
    T.check("...and leaves focus where it went", d5.activeElement === input, d5.activeElement && d5.activeElement.id);
  });

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
