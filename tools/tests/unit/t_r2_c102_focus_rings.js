// Round-2 C102.1 — text fields whose own rule sets outline:none must still show
// the shared 2px --iris ring under keyboard focus, not only the 18%-alpha 3px
// soft ring (~1.3:1 against the field). Two fields in index.html lost it:
//   * the sidebar search (#cs-q): `.cs-search input:focus{outline:none}` ties
//     the shared :is(...):focus-visible rule on specificity and comes later;
//     the field is borderless inside its pill, so the pill now carries the ring;
//   * the Console's tool filter (#cp-toolq, built by expand.js, styled here):
//     an id rule with outline:none beats every class-level focus rule;
//   * the pop-out fields expand.js styles INLINE with outline:none (the task
//     box, the notes pad, the symbol search): an inline declaration beats
//     every stylesheet rule but an !important one.
// The ordinary fields (#input, the rename field) already resolve to the ring
// through the shared rule and must keep doing so.
//
// Resolves the cascade over the page's REAL <style> sheets with the mini DOM's
// computedValue() (_a11y_dom.js) while each field holds focus.
"use strict";
const { bootDashboard, computedValue, harness } = require("./_a11y_dom.js");
const T = harness();

function ring(w, el) {
  const r = computedValue(w, el, "outline");
  return r ? r.value : "";
}
const IRIS = /^2px solid var\(--iris\)$/;

(async function main() {
  console.log("C102.1: keyboard focus shows the 2px --iris ring on every text field");
  try {
    const w = await bootDashboard();
    const d = w.document;

    const q = d.getElementById("cs-q");
    const pill = q.closest(".cs-search");
    q.focus();
    T.check("the sidebar search takes focus", d.activeElement === q);
    T.check("its pill draws the 2px --iris ring while the field has keyboard focus",
      IRIS.test(ring(w, pill)) || IRIS.test(ring(w, q)), { pill: ring(w, pill), field: ring(w, q) });
    q.blur();
    T.check("...and only then", !IRIS.test(ring(w, pill)), ring(w, pill));

    const tq = d.createElement("input"); tq.id = "cp-toolq"; tq.type = "search";
    d.body.appendChild(tq); tq.focus();
    T.check("the Console tool filter draws the 2px --iris ring under keyboard focus", IRIS.test(ring(w, tq)), ring(w, tq));

    // expand.js's inline-styled pop-out fields, both spellings of the inline
    // value (a script touching el.style re-serialises it as "outline: none")
    // (a shown host: a field inside the hidden #wpop could not take focus)
    const sheet = d.createElement("section"); sheet.className = "popsheet"; d.body.appendChild(sheet);
    sheet.innerHTML = '<input class="tk-new" style="flex:1;border:1px solid var(--hairline);outline:none">' +
      '<textarea id="nt-ta" style="width:100%;border: 1px solid var(--hairline); outline: none; letter-spacing: .005em"></textarea>';
    const tk = sheet.querySelector(".tk-new"), nt = sheet.querySelector("#nt-ta");
    // in a browser the style attribute IS the inline style; the mini DOM keeps
    // them apart, so mirror it (computedValue lets inline beat non-!important)
    [tk, nt].forEach(function (el) { el.style.cssText = el.getAttribute("style"); });
    T.check("(the inline outline:none is in force)", tk.style.getPropertyValue("outline") === "none" && nt.style.getPropertyValue("outline") === "none");
    tk.focus();
    T.check("a pop-out field styled inline outline:none still draws the ring", IRIS.test(ring(w, tk)), ring(w, tk));
    nt.focus();
    T.check("...also when the inline style reads 'outline: none'", IRIS.test(ring(w, nt)), ring(w, nt));
    tk.blur();
    T.check("...and only under focus", !IRIS.test(ring(w, tk)), ring(w, tk));

    const inp = d.getElementById("input"); inp.focus();
    T.check("the composer keeps the shared ring", IRIS.test(ring(w, inp)), ring(w, inp));
    const rn = d.createElement("input"); rn.className = "cs-rename";
    d.getElementById("cs-convos").appendChild(rn); rn.focus();
    T.check("the conversation rename field keeps the shared ring", IRIS.test(ring(w, rn)), ring(w, rn));
  } catch (e) {
    T.check("ran without throwing", false, String(e && e.stack || e));
  }
  T.done();
})();
