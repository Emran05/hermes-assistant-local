// R2-40 — the Clipboard Actions sheet (aux_clip.js) set its "open" flag only
// after the catalog fetch (/api/clip/actions) resolved, so ⌘⇧V pressed twice,
// or the Clipboard launcher chip plus the hotkey, inside that window built
// TWO sheets — two scrims, two Esc listeners, and closing one left the other.
// Now an open in flight is remembered synchronously and a second open returns
// early; a close while the catalog is still loading cancels the open.
//
// The REAL index.html + aux_clip.js run in the mini DOM (_a11y_dom.js) with
// the catalog route held open by the test. No dashboard, no model.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

const CATALOG = { ok: true, order: ["summarize", "rewrite"],
  actions: { summarize: { label: "Summarize", opts: [] }, rewrite: { label: "Rewrite", opts: [] } }, defaults: {} };

(async function main() {
  console.log("R2-40: one Clipboard sheet however many times it is opened");
  // every catalog request is held until release() answers ALL of them, so an
  // implementation that fired several fetches gets every one of them back
  let pending = [], catalogCalls = 0;
  const release = function () { const p = pending; pending = []; p.forEach(function (r) { r(CATALOG); }); };
  const w = await bootDashboard({
    aux: ["aux_clip.js"],
    routes: {
      "/api/clip/actions": function () {
        catalogCalls++;
        return new Promise(function (r) { pending.push(r); });
      }
    }
  });
  const d = w.document;
  const sheets = function () { return d.querySelectorAll("#clip-sheet").length; };
  const scrims = function () { return d.querySelectorAll("#clip-scrim").length; };
  const hotkey = function () { return w.__key(d.body, "V", { metaKey: true, shiftKey: true }); };
  const launch = d.getElementById("clip-launch");
  T.check("(setup) the launcher chip is injected", !!launch);

  // 1. launcher + hotkey + hotkey + bridge open, all before the catalog answers
  launch.click();
  hotkey(); hotkey();
  w.hermesClip.open();
  await w.__tick();
  T.check("nothing is built while the catalog is loading", sheets() === 0, sheets());
  T.check("only one catalog fetch is in flight", catalogCalls === 1, catalogCalls);
  release(); await w.__tick();
  T.check("exactly one sheet once the catalog answers", sheets() === 1 && scrims() === 1, { sheets: sheets(), scrims: scrims() });

  // Esc closes it and leaves nothing behind
  const esc = w.__key(d.body, "Escape");
  T.check("Esc closes the sheet", sheets() === 0 && scrims() === 0 && esc.defaultPrevented, { sheets: sheets(), scrims: scrims() });
  const esc2 = w.__key(d.body, "Escape");
  T.check("...and its Esc listener is gone (a second Esc is not swallowed)", !esc2.defaultPrevented);

  // 2. the catalog is cached now: hotkey toggles open/closed as before
  hotkey(); await w.__tick();
  T.check("⌘⇧V opens the sheet (cached catalog)", sheets() === 1);
  hotkey(); await w.__tick();
  T.check("⌘⇧V again closes it", sheets() === 0);
  launch.click(); launch.click(); await w.__tick();
  T.check("a double click on the launcher opens one sheet", sheets() === 1, sheets());
  w.hermesClip.close();

  // 3. a close while the catalog is still loading cancels the open
  const w2 = await bootDashboard({
    aux: ["aux_clip.js"],
    routes: {
      "/api/clip/actions": function () { return new Promise(function (r) { pending.push(r); }); }
    }
  });
  const d2 = w2.document;
  w2.hermesClip.open();
  await w2.__tick();
  w2.hermesClip.close();
  release(); await w2.__tick();
  T.check("closing before the catalog answers means no sheet appears", d2.querySelectorAll("#clip-sheet").length === 0);
  w2.hermesClip.open(); await w2.__tick();
  T.check("...and the next open still works", d2.querySelectorAll("#clip-sheet").length === 1);

  T.check("no handler threw", w.__errors.length === 0 && w2.__errors.length === 0, w.__errors.concat(w2.__errors).map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
