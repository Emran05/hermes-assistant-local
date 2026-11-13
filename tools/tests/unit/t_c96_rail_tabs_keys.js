// C96.2 (docs/audits/2026-10-21-codebase-audit.md) — the Agent page's right
// rail tabs (Record / Screen / Pulse, aux_agent.js) were <b role=tab> with no
// tabindex and no key handler: the keyboard could not reach them, and
// VoiceOver had no aria-selected to say which one was open.
// Now they are a tablist with a roving tabindex, the same pattern as the
// header switcher (wireSegKeys, C93): Arrow keys / Home / End move focus,
// Enter / Space switch (manual activation), aria-selected follows.
//
// The REAL index.html + aux_agent.js run in the mini DOM (_a11y_dom.js).
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}

(async function main() {
  console.log("C96.2: Agent rail tabs are keyboard-operable tabs");
  const w = await bootDashboard({ aux: ["aux_agent.js"] });
  const d = w.document;
  w.__run("setView('agent')");
  await w.__tick();
  const list = d.querySelector("#view-agent .railtabs");
  const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
  const tab = function (n) { return tabs.find(function (t) { return t.getAttribute("data-rail") === n; }); };
  const pane = function (n) { return d.getElementById("rail-" + n); };
  const active = function () { return tabs.filter(function (t) { return t.classList.contains("on"); }).map(function (t) { return t.getAttribute("data-rail"); }).join(","); };
  const stops = function () { return tabs.map(function (t) { return t.getAttribute("data-rail") + ":" + t.tabIndex; }).join(" "); };
  const selected = function () { return tabs.filter(function (t) { return t.getAttribute("aria-selected") === "true"; }).map(function (t) { return t.getAttribute("data-rail"); }).join(","); };

  await scenario("semantics", async function () {
    T.check("the rail tabs are a named tablist", list.getAttribute("role") === "tablist" && !!list.getAttribute("aria-label"), list.getAttribute("aria-label"));
    T.check("three tabs", tabs.length === 3, tabs.length);
    T.check("only the active tab is a Tab stop", stops() === "record:0 screen:-1 pulse:-1", stops());
    T.check("aria-selected marks the active tab only", selected() === "record", selected());
    T.check("each tab controls its pane, and each pane is labelled by its tab",
      tabs.every(function (t) {
        const p = d.getElementById(t.getAttribute("aria-controls"));
        return p && p.getAttribute("role") === "tabpanel" && p.getAttribute("aria-labelledby") === t.id && p.getAttribute("data-rail") === t.getAttribute("data-rail");
      }));
  });

  await scenario("arrows move focus, Enter/Space switch", async function () {
    tab("record").focus();
    T.check("the active tab can take focus", d.activeElement === tab("record"));
    const ev = w.__key(tab("record"), "ArrowRight");
    T.check("ArrowRight moves focus to Screen", d.activeElement === tab("screen"), d.activeElement && d.activeElement.id);
    T.check("...the Tab stop moves with it", stops() === "record:-1 screen:0 pulse:-1", stops());
    T.check("...the key is consumed", ev.defaultPrevented);
    T.check("...but does not switch the pane by itself", active() === "record" && !pane("record").hidden && pane("screen").hidden);
    const en = w.__key(tab("screen"), "Enter");
    T.check("Enter switches to Screen", active() === "screen" && !pane("screen").hidden && pane("record").hidden);
    T.check("...aria-selected follows", selected() === "screen", selected());
    T.check("...Enter is consumed and focus stays", en.defaultPrevented && d.activeElement === tab("screen"));
    w.__key(tab("screen"), "ArrowRight");
    T.check("ArrowRight to Pulse", d.activeElement === tab("pulse"));
    w.__key(tab("pulse"), "ArrowRight");
    T.check("ArrowRight from the last tab wraps to Record", d.activeElement === tab("record"));
    w.__key(tab("record"), "ArrowLeft");
    T.check("ArrowLeft from the first wraps to Pulse", d.activeElement === tab("pulse"));
    w.__key(tab("pulse"), "Home");
    T.check("Home goes to the first tab", d.activeElement === tab("record"));
    w.__key(tab("record"), "End");
    T.check("End goes to the last tab", d.activeElement === tab("pulse"));
    const sp = w.__key(tab("pulse"), " ");
    T.check("Space switches to Pulse", sp.defaultPrevented && active() === "pulse" && !pane("pulse").hidden && selected() === "pulse");
    w.__key(tab("pulse"), "ArrowDown");
    const rep = w.__key(tab("record"), "Enter", { repeat: true });
    T.check("an auto-repeated Enter does not switch", active() === "pulse" && rep.defaultPrevented);
  });

  await scenario("focus leaving the tabs", async function () {
    T.check("(setup) the Tab stop is on a tab that is not the active one", tab("record").tabIndex === 0 && active() === "pulse", stops());
    const out = Array.from(d.querySelectorAll("button")).find(function (b) { return !list.contains(b) && b.__focusable; });
    T.check("(setup) a control outside the tab row", !!out);
    if (out) out.focus();
    T.check("leaving the tab row puts the Tab stop back on the active tab", stops() === "record:-1 screen:-1 pulse:0", stops());
  });

  await scenario("mouse and programmatic switches keep the state in step", async function () {
    w.__click(tab("record"));
    T.check("a click switches and moves the Tab stop", active() === "record" && selected() === "record" && stops() === "record:0 screen:-1 pulse:-1", stops());
    w.__click(d.getElementById("agent-ticker"));
    T.check("the ticker opens Pulse with the same state", active() === "pulse" && selected() === "pulse" && tab("pulse").tabIndex === 0, stops());
    w.__agentTest.openRail("screen");
    T.check("openRail() updates aria-selected and the Tab stop", selected() === "screen" && stops() === "record:-1 screen:0 pulse:-1", stops());
  });

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
