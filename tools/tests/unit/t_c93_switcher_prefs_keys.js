// C93 (docs/audits/2026-10-21-codebase-audit.md) — the Hub/Agent switcher and
// the Preferences menu (the only route to Settings) could not be operated
// from the keyboard: the tabs were <b role=tab> nothing could focus, and the
// menu rows were click-only <div>s under a role=menu with no menu items.
//
// The REAL index.html + aux_prefs.js + aux_agent.js run in the mini DOM
// (_a11y_dom.js). Which tabs are hidden comes from the page's own CSS
// (aux_agent.js hides Settings/Console), evaluated by the mini cascade.
"use strict";
const { bootDashboard, computedValue, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}
function cssDisplay(w, el) {
  const v = computedValue(w, el, "display");
  el.__cssHidden = !!(v && v.value === "none");
}

(async function main() {
  console.log("C93: view switcher + Preferences menu from the keyboard");
  let w = await bootDashboard({ aux: ["aux_prefs.js", "aux_agent.js"] });
  let d = w.document;
  const $ = function (id) { return d.getElementById(id); };

  await scenario("view switcher", async function () {
    const tabs = ["tab-hub", "tab-agent", "tab-mind", "tab-console"].map($);
    tabs.forEach(function (t) { if (t) cssDisplay(w, t); });
    const hub = $("tab-hub"), agent = $("tab-agent");
    T.check("Settings and Console tabs are hidden by the page CSS", $("tab-mind").__cssHidden && $("tab-console").__cssHidden);
    T.check("the active tab is the switcher's one Tab stop", hub.tabIndex === 0 && agent.tabIndex === -1,
      tabs.map(function (t) { return t.id + ":" + t.tabIndex; }));
    hub.focus();
    T.check("the Hub tab can take focus", d.activeElement === hub);
    w.__key(hub, "ArrowRight");
    T.check("ArrowRight moves to Agent (skipping hidden tabs)", d.activeElement === agent, d.activeElement && d.activeElement.id);
    T.check("...and the Tab stop moves with it", agent.tabIndex === 0 && hub.tabIndex === -1);
    T.check("moving does not switch the view by itself", hub.classList.contains("on"));
    const ev = w.__key(agent, "Enter");
    T.check("Enter switches to the Agent view", agent.classList.contains("on") && agent.getAttribute("aria-selected") === "true" && !$("view-agent").hidden);
    T.check("Enter is consumed", ev.defaultPrevented);
    T.check("focus stays on the Agent tab", d.activeElement === agent);
    w.__key(agent, "ArrowRight");
    T.check("ArrowRight from the last visible tab wraps to Hub", d.activeElement === hub);
    w.__key(hub, " ");
    T.check("Space switches back to Hub", hub.classList.contains("on") && !$("view-hub").hidden && hub.getAttribute("aria-selected") === "true");
    T.check("the Tab stop follows the active tab", hub.tabIndex === 0 && agent.tabIndex === -1);
    w.__key(hub, "End");
    T.check("End goes to the last visible tab", d.activeElement === agent);
    w.__key(agent, "Home");
    T.check("Home goes to the first", d.activeElement === hub);
    // review: the auto-repeat tail of a held key is the same press
    w.__key(hub, "ArrowRight");
    const rep = w.__key(agent, "Enter", { repeat: true });
    T.check("an auto-repeated Enter does not switch views", hub.classList.contains("on") && rep.defaultPrevented);
  });

  await scenario("Preferences menu", async function () {
    const gear = $("prefs-btn"), menu = $("prefs-menu");
    T.check("the gear says it opens a menu", gear.getAttribute("aria-haspopup") === "menu" && gear.getAttribute("aria-expanded") === "false");
    const items = Array.from(menu.querySelectorAll('[role^="menuitem"]'));
    T.check("the menu has menu items", items.length >= 6, items.length);
    T.check("every menu item is a Tab stop", items.every(function (x) { return x.tabIndex === 0; }),
      items.map(function (x) { return x.getAttribute("role") + ":" + x.tabIndex; }));
    const radios = items.filter(function (x) { return x.getAttribute("role") === "menuitemradio"; });
    T.check("the theme choices are radio items, exactly one checked",
      radios.length === 3 && radios.filter(function (x) { return x.getAttribute("aria-checked") === "true"; }).length === 1);
    const reduce = items.find(function (x) { return x.getAttribute("role") === "menuitemcheckbox"; });
    T.check("Reduce motion is a checkbox item", !!reduce && reduce.getAttribute("aria-checked") === "false");
    T.check("its decorative switch is hidden from VoiceOver", reduce.querySelector(".pf-tog").getAttribute("aria-hidden") === "true");

    gear.focus();
    gear.click();                                 // Enter/Space on a <button>: detail 0
    T.check("Enter on the gear opens the menu", !menu.hidden && gear.getAttribute("aria-expanded") === "true");
    T.check("focus moves to the first item", d.activeElement === items[0], d.activeElement && d.activeElement.className);
    w.__key(d.activeElement, "ArrowRight");
    T.check("ArrowRight moves between the theme choices", d.activeElement === radios[1]);
    w.__key(d.activeElement, "Enter");
    T.check("Enter picks that theme", d.documentElement.getAttribute("data-theme") === "dark" && radios[1].getAttribute("aria-checked") === "true");
    w.__key(d.activeElement, "ArrowDown");
    T.check("ArrowDown moves to the next row", d.activeElement === items[items.indexOf(radios[1]) + 1]);
    reduce.focus();
    const sp = w.__key(reduce, " ");
    T.check("Space toggles Reduce motion", d.documentElement.hasAttribute("data-reduce") && reduce.getAttribute("aria-checked") === "true");
    T.check("Space does not scroll the page", sp.defaultPrevented);
    w.__key(reduce, " ");
    T.check("...and back", !d.documentElement.hasAttribute("data-reduce") && reduce.getAttribute("aria-checked") === "false");
    // review: a held Space flipped the whole page's motion on and off with
    // every auto-repeat
    const held = w.__key(reduce, " ", { repeat: true });
    T.check("an auto-repeated Space does not flip Reduce motion again", !d.documentElement.hasAttribute("data-reduce") && held.defaultPrevented);
    w.__key(d.activeElement, "End");
    T.check("End reaches the last row", d.activeElement === items[items.length - 1]);
    w.__key(d.activeElement, "Escape");
    T.check("Esc closes the menu", menu.hidden || menu.getAttribute("aria-hidden") === "true" || gear.getAttribute("aria-expanded") === "false");
    T.check("Esc gives focus back to the gear", d.activeElement === gear, d.activeElement && (d.activeElement.id || d.activeElement.tagName));
  });

  await scenario("System Settings from the keyboard", async function () {
    w = await bootDashboard({ aux: ["aux_prefs.js", "aux_agent.js"] }); d = w.document;
    const gear = d.getElementById("prefs-btn"), menu = d.getElementById("prefs-menu");
    gear.focus(); gear.click();
    const sys = Array.from(menu.querySelectorAll('[role="menuitem"]')).find(function (x) { return /System Settings/.test(x.textContent); });
    T.check("System Settings is a menu item", !!sys);
    sys.focus();
    w.__key(sys, "Enter");
    await w.__tick(); w.__flushTimers(2);
    T.check("Enter on System Settings opens Settings", !d.getElementById("view-mind").hidden);
    T.check("...and closes the menu", gear.getAttribute("aria-expanded") === "false");
    T.check("...and focus is not left on a hidden row", d.activeElement === gear || !menu.contains(d.activeElement));

    gear.focus(); gear.click();
    d.activeElement.focus();
    d.getElementById("input").focus();
    await w.__tick(); w.__flushTimers(2);
    T.check("tabbing out of the menu closes it", gear.getAttribute("aria-expanded") === "false");
    T.check("...leaving focus where it went", d.activeElement === d.getElementById("input"));
  });

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
