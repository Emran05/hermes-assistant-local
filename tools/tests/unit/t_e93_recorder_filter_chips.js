// E93 (2026-11-09 audit, P3) — the Flight Recorder's filter chips are real
// buttons.
//
// All / Writes / Shell / Computer / Undone were <span>s with a click handler:
// not in the tab order, no role, no pressed state, no Enter/Space. They are
// now <button type="button" aria-pressed> in a labelled group, Enter/Space are
// the browser's own activation (a held key's auto-repeat is ignored), and the
// 3 s poll no longer rebuilds them, so focus on a chip survives it — as does
// the press itself, which flips the pressed state in place. Drives the REAL
// aux_recorder.js in the real page (mini DOM, _a11y_dom.js).
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

const NOW = Math.floor(Date.now() / 1000);
const rows = [
  { id: 2, kind: "write", tool: "write_file", reversible: "yes", status: "done", source: "hub", target: "/p/a", ts: NOW - 10 },
  { id: 1, kind: "shell", tool: "terminal", reversible: "no", status: "done", source: "hub", target: "ls", ts: NOW - 20 }
];

async function main() {
  const w = await bootDashboard({
    aux: ["aux_recorder.js"],
    routes: {
      "/api/recorder": function (url) {
        const k = (/[?&]kind=([^&]+)/.exec(url) || [])[1];
        return { recorder_ok: true, checkpoints_enabled: true, counts: {},
          actions: rows.filter(function (a) { return !k || a.kind === k; }) };
      },
      "/api/recorder/retention": { ok: true, retain_days: 90, total: 2 }
    }
  });
  const d = w.document;
  await w.__run("loadRecorder()"); await w.__tick();
  d.getElementById("view-console").hidden = false;
  const box = d.getElementById("rec-filters");
  const chip = function (f) { return box.querySelector('[data-f="' + f + '"]'); };
  const chips = function () { return Array.from(box.querySelectorAll("[data-f]")); };

  console.log("\n1. semantics");
  T.check("five chips, each a <button type=button> with aria-pressed",
    chips().length === 5 && chips().every(function (b) { return b.localName === "button" && b.getAttribute("type") === "button" && /^(true|false)$/.test(b.getAttribute("aria-pressed")); }),
    chips().map(function (b) { return b.localName + ":" + b.getAttribute("aria-pressed"); }));
  T.check("All is pressed, the rest are not", chip("all").getAttribute("aria-pressed") === "true" &&
    chips().filter(function (b) { return b.getAttribute("aria-pressed") === "true"; }).length === 1);
  T.check("the row is a labelled group", box.getAttribute("role") === "group" && !!box.getAttribute("aria-label"));
  T.check("they keep the chip look (class rec-fchip / .on)", chips().every(function (b) { return /\brec-fchip\b/.test(b.className); }) && /\bon\b/.test(chip("all").className));
  const css = d.getElementById("reccss").textContent;
  T.check("the global button hover lift and border are neutralised for the chips",
    /\.rec-fchip:hover\{[^}]*transform:none/.test(css) && /\.rec-fchip\{[^}]*margin:0/.test(css));
  // (review) rendered in headless Chromium: with line-height:normal a chip was
  // 20px tall where the <span> it replaced was 23.75px. The inherited
  // line-height gives the old box back.
  T.check("the chips keep the inherited line-height (same height as the old <span> chips)",
    /\.rec-fchip\{[^}]*line-height:inherit/.test(css) && !/\.rec-fchip\{[^}]*line-height:normal/.test(css));

  console.log("\n2. keyboard");
  const sh = chip("shell");
  sh.focus();
  T.check("a chip takes focus", d.activeElement === sh);
  const kd = w.__key(sh, "Enter");
  T.check("a plain Enter is not swallowed (the browser's button activation runs)", !kd.defaultPrevented);
  sh.click(); await w.__tick();                   // what Enter / Space activate
  T.check("Shell is pressed now, All is not", sh.getAttribute("aria-pressed") === "true" && chip("all").getAttribute("aria-pressed") === "false" && /\bon\b/.test(sh.className));
  T.check("focus stayed on the SAME chip (flipped in place, not rebuilt)", d.activeElement === sh && chip("shell") === sh);
  T.check("the list was filtered", d.querySelectorAll("#rec-list .rec-row").length === 1);
  const rep = w.__key(sh, "Enter", { repeat: true });
  const rep2 = w.__key(sh, " ", { repeat: true });
  T.check("a held key's auto-repeat is ignored (Enter and Space)", rep.defaultPrevented && rep2.defaultPrevented);

  console.log("\n3. the 3 s poll");
  await w.__run("loadRecorder()"); await w.__tick();
  T.check("focus on the chip survives a poll, and so does the node", d.activeElement === sh && chip("shell") === sh);
  T.check("no page errors", w.__errors.length === 0, w.__errors.map(String));
  T.done();
}
main().catch(function (e) { console.log("  FAIL suite threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
