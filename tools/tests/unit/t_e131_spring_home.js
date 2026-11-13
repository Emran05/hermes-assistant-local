// t_e131_spring_home.js — 2026-11-09 audit E131.
//
// index.html's springHome() clears the identity end values HermesAnim.spring()
// leaves inline (an inline transform makes `.chatcol` a stacking context and a
// containing block for good). It cleared them on the spring's `finished`, a
// microtask AFTER the spring ended. When an enter interrupted the spring (a
// quick double toggle of Expand chat: springHome(col, …) then
// enter(col, 'slideR')), the spring's end values were painted inline, the
// enter recorded them as the element's prior inline style and restored them
// when it ended, and the late clean-up saw the enter's translate and did
// nothing: `transform: scale(1); opacity: 1` stayed for the page's life.
// Now springHome also clears on the spring's own (synchronous) hermes:animend,
// before the interrupting animation records anything.
//
// The REAL aux_anim.js in the mini DOM with the recording library stubs, and
// the REAL springHome cut out of index.html.
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, harness } = require("./_a11y_dom.js");
const S = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const ANIM = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");
const PAGE = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");

function fnSrc(name) {
  const lines = PAGE.split("\n");
  const i = lines.findIndex(function (l) { return l.startsWith("function " + name + "("); });
  if (i < 0) return null;
  for (let j = i + 1; j < lines.length; j++) if (lines[j].startsWith("}")) return lines.slice(i, j + 1).join("\n");
  return null;
}
const FIX = '<!doctype html><html><head></head><body><main class="stage">' +
  '<div class="chatcol" id="col">chat</div><div class="chatcol" id="c2">x</div><div id="c3">y</div><div id="c4">z</div>' +
  '</main></body></html>';
function boot() {
  const w = createWindow({ html: FIX });
  const L = S.install(w, { motion: true });
  w.__run(ANIM, "aux_anim.js");
  const SH = fnSrc("springHome");
  w.__run("const HA=()=>window.HermesAnim;\n" + SH + "\nwindow.springHome=springHome;", "springHome");
  return { w: w, d: w.document, L: L, A: w.HermesAnim, ok: !!SH };
}
const tick = function () { return new Promise(function (r) { setImmediate(r); }); };
async function flush() { for (let i = 0; i < 10; i++) await tick(); }
function inl(el) { return [el.style.transform || "", el.style.opacity || ""]; }

(async function main() {
  console.log("springHome: identity end values never stick (E131)");
  const { w, d, L, A, ok } = boot();
  T.check("index.html has springHome and the runtime loaded", ok && !!A);
  if (!ok || !A) T.done();

  // the audit's sequence: setChatMode('full') then, quickly, setChatMode('normal')
  const col = d.getElementById("col");
  w.springHome(col, { opacity: [0.4, 1], scale: [0.985, 1] }, "soft");
  A.enter(col, "slideR");
  L.finishAll();
  await flush();
  T.check("THE FIX: spring interrupted by an enter leaves no inline transform/opacity",
    JSON.stringify(inl(col)) === '["",""]', inl(col));

  // control: the spring alone lands and is cleared
  const c2 = d.getElementById("c2");
  w.springHome(c2, { opacity: [0.4, 1], scale: [0.985, 1] }, "soft");
  L.finishAll();
  await flush();
  T.check("a spring that lands on its own is cleared", JSON.stringify(inl(c2)) === '["",""]', inl(c2));

  // spring over spring (the Send-button nudge pressed twice)
  const c3 = d.getElementById("c3");
  w.springHome(c3, { scale: [1, 1.04, 1] });
  w.springHome(c3, { scale: [1, 1.04, 1] });
  L.finishAll();
  await flush();
  T.check("spring over spring is cleared", JSON.stringify(inl(c3)) === '["",""]', inl(c3));

  // a NON-identity end value is the caller's and stays
  const c4 = d.getElementById("c4");
  w.springHome(c4, { scale: [1, 1.5] });
  L.finishAll();
  await flush();
  T.check("a non-identity end value is left alone", c4.style.transform === "scale(1.5)", inl(c4));

  // an animend bubbling up from a CHILD's spring must not use up the parent's
  // listener: the parent, interrupted afterwards, is still cleared
  const kid = d.createElement("div"); col.appendChild(kid);
  w.springHome(col, { opacity: [0.4, 1], scale: [0.985, 1] }, "soft");
  A.spring(kid, { scale: [0.5, 1] });
  A.spring(kid, { scale: [0.5, 1] });            // ends the first kid spring -> a bubbling animend
  A.enter(col, "slideR");
  L.finishAll();
  await flush();
  T.check("a child's animend does not stand in for the parent's",
    JSON.stringify(inl(col)) === '["",""]', inl(col));

  T.done();
})().catch(function (e) { console.log("FAIL threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
