// S2 seam (E89 / C95): the Agent page's "Claude dialogues" sheet (#ag-deep,
// aux_agent.js) joins index.html's OVL Esc/focus registry.
//
// E89 gave the sheet its own Esc + Tab handling because the registry only knew
// `hidden` overlays.  What it lacked is the registry's modal pull-back
// (R2-35): a script that moved focus out of the open sheet (the composer
// focusing itself when a turn ends) left focus on the page underneath.  The
// registry now takes an entry with its own isOpen (the .open class) and
// watches the node for class changes.  Both keys must still be handled ONCE:
// Esc closes the sheet (one close, focus back on the button) and not anything
// else; a context menu opened over the sheet takes the first Esc; Tab stays
// inside; and with the sheet closed neither key is consumed.
//
// The REAL index.html + aux_agent.js run in the mini DOM (_a11y_dom.js).
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

(async function main() {
  console.log("S2: #ag-deep is in the OVL registry, and each key is handled once");
  const w = await bootDashboard({
    aux: ["aux_agent.js"],
    routes: { "/api/claude/recent": function () { return { ok: true, calls: [] }; } }
  });
  const d = w.document;
  w.__run("setView('agent')");
  await w.__tick();
  const dm = d.getElementById("ag-deep");
  const btn = d.getElementById("ag-deepbtn");
  const x = d.getElementById("ag-deep-x");
  // count real closes: wrap the close button's handler path by watching the class
  let closes = 0;
  const realRemove = dm.classList.remove.bind(dm.classList);
  dm.classList.remove = function (c) { if (c === "open" && dm.classList.contains("open")) closes++; return realRemove(c); };

  btn.focus(); w.__click(btn); await w.__tick();
  T.check("the sheet opens with focus on its close button", dm.classList.contains("open") && d.activeElement === x);

  // R2-35 pull-back: a script focuses something on the page underneath
  const outside = btn;          // behind the sheet, focusable
  outside.focus(); await w.__tick();
  T.check("focus moved out of the open sheet by a script is pulled back in",
    dm.contains(d.activeElement), d.activeElement && (d.activeElement.id || d.activeElement.tagName));

  // Esc: one close, consumed, focus back on the opener
  x.focus();
  const esc = w.__key(d.activeElement, "Escape");
  await w.__tick();
  T.check("Esc closes the sheet", !dm.classList.contains("open"));
  T.check("...exactly once", closes === 1, closes);
  T.check("...is consumed", esc.defaultPrevented);
  T.check("...and focus goes back to the Claude dialogues button", d.activeElement === btn,
    d.activeElement && (d.activeElement.id || d.activeElement.tagName));
  T.check("Esc with the sheet closed is not consumed", !w.__key(btn, "Escape").defaultPrevented);
  T.check("Tab with the sheet closed is not trapped", !w.__key(btn, "Tab").defaultPrevented);

  // a context menu over the open sheet takes the first Esc
  w.__click(btn); await w.__tick();
  closes = 0;
  w.__run("showCtx(10,10,[{label:'Ask Hermes about this',fn:function(){}}])");
  await w.__tick();
  const ctx = d.querySelector("body>.ctxmenu");
  T.check("a context menu is up over the sheet", !!ctx && dm.classList.contains("open"));
  w.__key(ctx ? (ctx.querySelector("button") || d.body) : d.body, "Escape");
  await w.__tick();
  T.check("the first Esc closes the context menu, not the sheet",
    !d.querySelector("body>.ctxmenu") && dm.classList.contains("open") && closes === 0, closes);
  x.focus();
  w.__key(x, "Escape"); await w.__tick();
  T.check("the second Esc closes the sheet, once", !dm.classList.contains("open") && closes === 1, closes);

  // Tab stays inside while open (the only focusable is the close button here)
  w.__click(btn); await w.__tick();
  const tab = w.__key(d.activeElement, "Tab");
  T.check("Tab inside the open sheet is kept there", tab.defaultPrevented && dm.contains(d.activeElement));
  const stab = w.__key(d.activeElement, "Tab", { shiftKey: true });
  T.check("...Shift+Tab too", stab.defaultPrevented && dm.contains(d.activeElement));
  w.__click(dm); await w.__tick();
  T.check("a backdrop click closes it and returns focus", !dm.classList.contains("open") && d.activeElement === btn);

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
