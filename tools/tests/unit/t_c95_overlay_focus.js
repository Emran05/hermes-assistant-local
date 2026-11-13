// C95 (docs/audits/2026-10-21-codebase-audit.md) — the widget pop-out, the
// Widget Center and the right-click menu had no Esc, no focus moved into them
// and none given back. They must now: close on Esc, take focus when they open,
// hand it back to what held it before (unless an action moved it on purpose),
// and the two modal sheets keep Tab inside themselves.
//
// The REAL index.html runs in the mini DOM (_a11y_dom.js); openPop/closePop/
// showCtx are the page's own, unedited.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}
function tabbables(root) {
  return Array.from(root.querySelectorAll("button,input,select,textarea,a[href],[tabindex]"))
    .filter(function (x) { return x.tabIndex >= 0 && !x.disabled && x.getClientRects().length; });
}

(async function main() {
  console.log("C95: Esc, focus in and focus back for the pop-out, Widget Center and context menu");
  let w;

  await scenario("widget pop-out", async function () {
    w = await bootDashboard({ routes: { "/api/expand": { rich: false } } });
    const d = w.document, $ = function (id) { return d.getElementById(id); };
    const opener = $("edit-toggle");
    opener.focus();
    w.__run("openPop('clock')"); await w.__tick();
    const pop = $("wpop"), sheet = $("wpop-sheet");
    T.check("the pop-out opens", !pop.hidden);
    T.check("focus moves into it", sheet.contains(d.activeElement), d.activeElement && d.activeElement.id);
    T.check("it is announced as a modal dialog", sheet.getAttribute("role") === "dialog" && sheet.getAttribute("aria-modal") === "true");
    T.check("the dialog is named after the widget", /clock/i.test(sheet.getAttribute("aria-label") || ""), sheet.getAttribute("aria-label"));
    // Tab stays inside the modal sheet
    const f = tabbables(sheet);
    f[f.length - 1].focus();
    const tab = w.__key(d.activeElement, "Tab");
    T.check("Tab from the last control wraps to the first", tab.defaultPrevented && d.activeElement === f[0], d.activeElement && d.activeElement.id);
    w.__key(d.activeElement, "Tab", { shiftKey: true });
    T.check("Shift+Tab from the first wraps to the last", d.activeElement === f[f.length - 1]);
    const esc = w.__key(d.activeElement, "Escape");
    T.check("Esc is handled", esc.defaultPrevented);
    w.__flushTimers(2); await w.__tick();
    T.check("Esc closes the pop-out", pop.hidden);
    T.check("focus goes back to what opened it", d.activeElement === opener, d.activeElement && (d.activeElement.id || d.activeElement.tagName));
  });

  await scenario("pop-out Ask keeps the focus it chose", async function () {
    w = await bootDashboard({ routes: { "/api/expand": { rich: false } } });
    const d = w.document, $ = function (id) { return d.getElementById(id); };
    $("edit-toggle").focus();
    w.__run("openPop('clock')"); await w.__tick();
    $("pop-ask").value = "what time is it in Paris";
    $("pop-ask-go").click(); await w.__tick();
    T.check("Ask closes the pop-out", $("wpop").hidden);
    T.check("...and the chat input keeps focus (not handed back)", d.activeElement === $("input"), d.activeElement && d.activeElement.id);
  });

  await scenario("a control that answers Esc itself", async function () {
    // the markets pop-out's symbol search (expand.js) closes its own dropdown
    // on Esc and lets go of focus; that Esc must not also close the pop-out
    w = await bootDashboard({ aux: ["expand.js"], routes: { "/api/expand": { rich: true, tickers: ["AAPL"], indices: [],
      quotes: [{ symbol: "AAPL", name: "Apple", price: 1, chg: 0, pct: 0, spark: [1, 2] }] } } });
    const d = w.document, $ = function (id) { return d.getElementById(id); };
    w.__run("openPop('markets')"); await w.__tick();
    const q = $("mkt-q");
    T.check("the markets pop-out has its symbol search", !!q);
    q.focus();
    w.__key(q, "Escape"); await w.__tick(); w.__flushTimers(2); await w.__tick();
    T.check("Esc in the search is the search's: the pop-out stays open", !$("wpop").hidden);
    w.__key(d.activeElement, "Escape"); w.__flushTimers(2); await w.__tick();
    T.check("a second Esc closes the pop-out", $("wpop").hidden);
  });

  await scenario("Widget Center", async function () {
    w = await bootDashboard({ routes: { "/api/catalog": { catalog: [{ id: "clock", title: "Clock", cat: "life" }] } } });
    const d = w.document, $ = function (id) { return d.getElementById(id); };
    const add = $("add-widget");
    add.focus(); add.click();
    await w.__tick();
    const wc = $("wcenter"), sheet = wc.querySelector(".sheet");
    T.check("the Widget Center opens", !wc.hidden);
    T.check("focus moves into it", sheet.contains(d.activeElement), d.activeElement && d.activeElement.id);
    T.check("it is announced as a modal dialog named Widget Center",
      sheet.getAttribute("role") === "dialog" && sheet.getAttribute("aria-modal") === "true" && sheet.getAttribute("aria-label") === "Widget Center");
    // the tiles are Tab stops too (C96.1): Tab from the LAST control wraps
    const wf = tabbables(sheet);
    wf[wf.length - 1].focus();
    const tab = w.__key(d.activeElement, "Tab");
    T.check("Tab stays inside", tab.defaultPrevented && sheet.contains(d.activeElement) && d.activeElement === wf[0]);
    w.__key(d.activeElement, "Escape"); await w.__tick();
    T.check("Esc closes it", wc.hidden);
    T.check("focus goes back to the Add button", d.activeElement === add, d.activeElement && d.activeElement.id);
  });

  await scenario("right-click menu", async function () {
    w = await bootDashboard();
    const d = w.document, $ = function (id) { return d.getElementById(id); };
    const opener = $("edit-toggle");
    opener.focus();
    w.__picked = [];
    w.__run("showCtx(10,10,[{icon:'',label:'Ask Hermes about this',fn:()=>{window.__picked.push('ask');$('input').focus();}}," +
            "{icon:'',label:'Expand',fn:()=>window.__picked.push('expand')}])");
    await w.__tick();
    const cm = d.querySelector(".ctxmenu");
    T.check("the context menu opens", !!cm);
    T.check("it is a menu of menu items", cm && cm.getAttribute("role") === "menu" &&
      Array.from(cm.querySelectorAll("button")).every(function (b) { return b.getAttribute("role") === "menuitem"; }));
    const items = cm ? Array.from(cm.querySelectorAll("button")) : [];
    T.check("focus moves to its first item", d.activeElement === items[0], d.activeElement && d.activeElement.tagName);
    w.__key(d.activeElement, "ArrowDown");
    T.check("ArrowDown moves to the next item", d.activeElement === items[1]);
    w.__key(d.activeElement, "ArrowDown");
    T.check("...and wraps", d.activeElement === items[0]);
    w.__key(d.activeElement, "Escape"); await w.__tick();
    T.check("Esc closes it", !d.querySelector(".ctxmenu"));
    T.check("focus goes back to what had it", d.activeElement === opener, d.activeElement && d.activeElement.id);

    opener.focus();
    w.__run("showCtx(10,10,[{icon:'',label:'Ask Hermes about this',fn:()=>{window.__picked.push('ask');$('input').focus();}}])");
    await w.__tick();
    d.querySelector(".ctxmenu button").click(); await w.__tick();
    T.check("choosing an item runs it", w.__picked.indexOf("ask") >= 0);
    T.check("...and focus stays where the item put it", d.activeElement === $("input"), d.activeElement && d.activeElement.id);
  });

  await scenario("Esc with nothing open", async function () {
    w = await bootDashboard();
    const d = w.document;
    d.getElementById("input").focus();
    const ev = w.__key(d.activeElement, "Escape");
    T.check("is left alone", !ev.defaultPrevented);
    T.check("focus does not move", d.activeElement === d.getElementById("input"));
  });

  T.check("no handler threw", !w || w.__errors.length === 0, w && w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
