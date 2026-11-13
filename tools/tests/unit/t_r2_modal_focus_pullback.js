// Round-2 R2-35 — the pop-out / Widget Center Tab trap (C95) steps in only at
// the first and last control it knows. In WebKit with macOS keyboard
// navigation off (the default), Tab skips buttons: from the pop-out's ask
// field the next stop is a text field OUTSIDE the modal sheet (the chat
// composer), the trap sees a Tab from a middle control and lets it go, and
// focus leaves the aria-modal dialog. A document focusin listener now pulls
// focus that lands outside an open modal sheet back in — to its first control
// after a Tab, its last after a Shift+Tab — without rewriting where focus
// returns when the sheet closes.
//
// The REAL index.html runs in the mini DOM (_a11y_dom.js). The WebKit skip is
// modelled by doing exactly what the browser does there: the trap's keydown
// is dispatched, and when it does not prevent the default, focus moves to the
// outside text field.
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
// a Tab the trap does not take: WebKit moves on to the next TEXT FIELD
function webkitTab(w, from, to, shift) {
  const ev = w.__key(from, "Tab", { shiftKey: !!shift });
  if (!ev.defaultPrevented) to.focus();
  return ev;
}

(async function main() {
  console.log("R2-35: focus cannot leave an open modal sheet");

  await scenario("pop-out, Tab from the ask field", async function () {
    const w = await bootDashboard({ routes: { "/api/expand": { rich: false } } });
    const d = w.document, $ = function (id) { return d.getElementById(id); };
    const opener = $("edit-toggle"); opener.focus();
    w.__run("openPop('clock')"); await w.__tick();
    const sheet = $("wpop-sheet"), ask = $("pop-ask");
    T.check("the ask field has focus", d.activeElement === ask);
    webkitTab(w, ask, $("input"));
    T.check("focus is pulled back into the sheet", sheet.contains(d.activeElement), d.activeElement && (d.activeElement.id || d.activeElement.tagName));
    T.check("...onto its first control", d.activeElement === tabbables(sheet)[0], d.activeElement && d.activeElement.id);
    // Shift+Tab out of the sheet lands on its LAST control
    ask.focus();
    webkitTab(w, ask, $("input"), true);
    const f = tabbables(sheet);
    T.check("after a Shift+Tab focus comes back onto the last control", d.activeElement === f[f.length - 1], d.activeElement && d.activeElement.id);
    // programmatic focus elsewhere while it is open
    $("sessions").focus();
    T.check("any other focus outside is pulled back too", sheet.contains(d.activeElement));
    // the pull-backs did not rewrite where focus goes on close
    w.__key(d.activeElement, "Escape"); w.__flushTimers(2); await w.__tick();
    T.check("Esc closes it", $("wpop").hidden);
    T.check("...and focus returns to what opened it", d.activeElement === opener, d.activeElement && (d.activeElement.id || d.activeElement.tagName));
    $("input").focus();
    T.check("once it is closed, focus moves freely", d.activeElement === $("input"));
  });

  // review: a pull-back that is NOT a Tab (a script's .focus() — sendMsg
  // focuses the composer whenever a turn ends) used to land on the sheet's
  // first control, the Close button, so the next space typed into the ask
  // field closed the pop-out. It goes back to the control it was taken from.
  await scenario("pop-out, a script focuses the composer while you type", async function () {
    const w = await bootDashboard({ routes: { "/api/expand": { rich: false } } });
    const d = w.document, $ = function (id) { return d.getElementById(id); };
    w.__run("openPop('clock')"); await w.__tick();
    const sheet = $("wpop-sheet"), ask = $("pop-ask");
    T.check("the ask field has focus", d.activeElement === ask);
    $("input").focus();                                   // a chat turn just ended
    T.check("focus goes back to the ask field, not onto Close", d.activeElement === ask,
      d.activeElement && (d.activeElement.id || d.activeElement.tagName));
    w.__flushTimers(1);
    webkitTab(w, ask, $("input"));
    T.check("...while a Tab out of the sheet still wraps to its first control", d.activeElement === tabbables(sheet)[0],
      d.activeElement && d.activeElement.id);
    // a field that stops its own keydown (several pop-out fields do) still
    // counts as a Tab: the tracking listener is a capture one
    const own = d.createElement("input"); own.id = "own-field";
    own.addEventListener("keydown", function (e) { e.stopPropagation(); });
    sheet.querySelector(".w-body").appendChild(own);
    own.focus(); w.__flushTimers(1);
    webkitTab(w, own, $("input"));
    T.check("...also from a field that stops its keydown", d.activeElement === tabbables(sheet)[0],
      d.activeElement && d.activeElement.id);
  });

  await scenario("Widget Center", async function () {
    const w = await bootDashboard({ routes: { "/api/catalog": { catalog: [{ id: "clock", title: "Clock", cat: "life" }] } } });
    const d = w.document, $ = function (id) { return d.getElementById(id); };
    $("add-widget").click(); await w.__tick(); await w.__tick();
    const wc = $("wcenter");
    T.check("the Widget Center opens with focus inside", !wc.hidden && wc.contains(d.activeElement));
    $("input").focus();
    T.check("focus outside it is pulled back in", wc.contains(d.activeElement), d.activeElement && (d.activeElement.id || d.activeElement.tagName));
  });

  await scenario("not a modal: the model menu", async function () {
    const w = await bootDashboard();
    const d = w.document, $ = function (id) { return d.getElementById(id); };
    $("model-menu").hidden = false; await w.__tick();
    $("input").focus();
    T.check("focus may leave a non-modal menu", d.activeElement === $("input"));
  });
  T.done();
})();
