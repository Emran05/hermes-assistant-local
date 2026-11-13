// C04 (docs/audits/2026-10-21-codebase-audit.md) — in the stacked layout
// (window <= 1120px) the header is position:sticky at top:0 and the page
// scrolls under it; the Settings rail (aux_settings_shell.js) is ALSO sticky
// at top:0, so once the page scrolled it slid behind the header and its
// search box was unreachable.
//
// The REAL index.html + aux_settings_shell.js + aux_glass.js run in the mini
// DOM (_a11y_dom.js); positions come from the mini cascade over the page's
// real stylesheets (the inline <style> and the one the settings shell
// injects) at each window width.
"use strict";
const { bootDashboard, computedValue, harness } = require("./_a11y_dom.js");
const T = harness();
function v(w, el, prop, width) { const r = computedValue(w, el, prop, null, { width: width }); return r ? r.value : "(unset)"; }
// resolve "calc(var(--hdr-h,56px) + 8px)" / "0" / "12px" against :root's --hdr-h
function px(w, expr) {
  const root = w.document.documentElement.style.getPropertyValue("--hdr-h");
  let s = String(expr).replace(/var\(--hdr-h(?:,\s*([^)]+))?\)/g, function (m, fb) { return root || fb || "0px"; });
  s = s.replace(/^calc\((.*)\)$/, "$1").replace(/px/g, "");
  if (!/^[\d.\s+\-*/()]+$/.test(s)) return NaN;
  return Function("return (" + s + ")")();
}

(async function main() {
  console.log("C04: the sticky Settings rail clears the sticky header in the stacked layout");
  let ro = null;
  const w = await bootDashboard({
    width: 1000, aux: ["aux_settings_shell.js", "aux_glass.js"],
    before: function (w0) { w0.document.querySelector("header").__rect = { left: 0, top: 0, width: 1000, height: 56 }; }
  });
  const d = w.document, hdr = d.querySelector("header"), rail = d.getElementById("set-rail");
  T.check("the Settings rail is built inside the Settings view", !!rail && !!rail.closest("#view-mind"));
  T.check("the header height is published as --hdr-h", d.documentElement.style.getPropertyValue("--hdr-h") === "56px",
    d.documentElement.style.getPropertyValue("--hdr-h"));

  [900, 1000, 1120].forEach(function (W) {
    T.check(W + "px: the header is sticky (stacked layout)", v(w, hdr, "position", W) === "sticky");
    T.check(W + "px: the rail is sticky", v(w, rail, "position", W) === "sticky");
    const top = px(w, v(w, rail, "top", W));
    T.check(W + "px: the rail sticks BELOW the header (top " + top + " >= 56)", top >= 56, v(w, rail, "top", W));
    T.check(W + "px: anchored jumps land below the header too",
      px(w, v(w, d.documentElement, "scroll-padding-top", W)) >= 56, v(w, d.documentElement, "scroll-padding-top", W));
  });
  T.check("1300px: the header is not sticky, so the rail keeps top:0", v(w, hdr, "position", 1300) !== "sticky" && v(w, rail, "top", 1300) === "0");
  T.check("800px: the rail is a static chip row (nothing to clear)", v(w, rail, "position", 800) === "static");

  // the header grows (a second row of chips, a larger text size): the rail follows
  hdr.__rect = { left: 0, top: 0, width: 1000, height: 72 };
  (w.__ros || []).forEach(function (r) { if ((r.t || []).indexOf(hdr) >= 0) r.cb([]); });
  T.check("a taller header republishes --hdr-h", d.documentElement.style.getPropertyValue("--hdr-h") === "72px",
    d.documentElement.style.getPropertyValue("--hdr-h"));
  T.check("...and the rail moves down with it", px(w, v(w, rail, "top", 1000)) >= 72, px(w, v(w, rail, "top", 1000)));

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
