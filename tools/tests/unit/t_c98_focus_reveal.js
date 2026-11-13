// C98 (docs/audits/2026-10-21-codebase-audit.md) — controls that sit at
// opacity:0 until the pointer hovers their row (chat Copy, Quick Links
// remove, task Delete) stayed invisible when the keyboard focused them.
//
// Evaluates the page's REAL stylesheets (every <style> in index.html, plus
// what expand.js renders) with the mini cascade in _a11y_dom.js: the opacity
// each control resolves to at rest, under the pointer, and under focus.
"use strict";
const { bootDashboard, computedValue, harness } = require("./_a11y_dom.js");
const T = harness();
function opacity(w, el) { const v = computedValue(w, el, "opacity"); return v ? v.value : "(unset)"; }

(async function main() {
  console.log("C98: hover-only controls show for the keyboard too");
  const w = await bootDashboard({
    aux: ["expand.js"],
    routes: { "/api/hub": { order: ["tasks"], model_online: true, widgets: { tasks: {
      meta: { title: "Tasks", size: "card", cat: "productivity", icon: "spark" },
      data: { tasks: [{ id: "a", text: "Buy milk", done: false }] } } } } }
  });
  const d = w.document;
  await w.__tick();

  // ---- task Delete -------------------------------------------------------
  const row = d.querySelector("#wgrid .task"), x = row && row.querySelector("button"), chk = row && row.querySelector(".task-check");
  T.check("a task row rendered", !!x && !!chk);
  d.__hover = null; if (d.activeElement && d.activeElement.blur) d.activeElement.blur();
  T.check("task Delete is hidden at rest", opacity(w, x) === "0", opacity(w, x));
  d.__hover = row;
  T.check("task Delete shows under the pointer", opacity(w, x) === "1", opacity(w, x));
  d.__hover = null;
  x.focus();
  T.check("task Delete shows when it has keyboard focus", d.activeElement === x && opacity(w, x) === "1", opacity(w, x));
  chk.focus();
  T.check("...and while its row's check box has focus (the next Tab stop)", opacity(w, x) === "1", opacity(w, x));

  // ---- chat Copy ---------------------------------------------------------
  w.__run("addBubble('Hello there','bot',false)");
  const bub = Array.from(d.querySelectorAll("#msgs .bubble.bot")).pop(), cp = bub && bub.querySelector(".msg-copy");
  T.check("a bot bubble with a Copy button rendered", !!cp);
  d.getElementById("input").focus();
  T.check("chat Copy is hidden at rest", opacity(w, cp) === "0", opacity(w, cp));
  cp.focus();
  T.check("chat Copy shows when it has keyboard focus", d.activeElement === cp && opacity(w, cp) === "1", opacity(w, cp));

  // ---- Quick Links remove (expand.js's own markup) -----------------------
  const host = d.createElement("div"); d.body.appendChild(host);
  let ok = true;
  try { w.__run("EXPAND_RENDER.quicklinks"); w.__qlHost = host;
        w.__run("EXPAND_RENDER.quicklinks(window.__qlHost,{links:[{label:'GitHub',url:'https://github.com'}]})"); }
  catch (e) { ok = false; T.check("the Quick Links pop-out rendered", false, String(e)); }
  const ql = host.querySelector(".vk-ql"), qx = ql && ql.querySelector(".vk-qlx");
  if (ok) T.check("the Quick Links pop-out rendered", !!qx);
  if (qx) {
    d.getElementById("input").focus();
    T.check("Quick Links remove is hidden at rest", opacity(w, qx) === "0", opacity(w, qx));
    ql.focus();
    T.check("Quick Links remove shows when its link has focus (Tab lands there next)", opacity(w, qx) === "1", opacity(w, qx));
    qx.focus();
    T.check("Quick Links remove shows when it has focus", d.activeElement === qx && opacity(w, qx) === "1", opacity(w, qx));
  }

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
