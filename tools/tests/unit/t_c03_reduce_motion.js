// C03 (docs/audits/2026-10-21-codebase-audit.md) — Reduce motion, the app's
// own switch AND the OS setting, left two kinds of motion running:
//   * the aurora drift, which lives on body::before — the in-app kill rule
//     `:root[data-reduce] *` never matched a pseudo-element;
//   * every Web Animation / Motion One animation (card entrances, pop-out and
//     menu fades, the model-swap card). CSS cannot reach those, and the page's
//     REDUCE const is the OS value at load, consulted by only some callers.
//
// Real index.html + aux_prefs.js in the mini DOM (_a11y_dom.js); the CSS half
// is evaluated by the mini cascade over the page's real <style> sheets.
"use strict";
const { bootDashboard, computedValue, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}
function auroraAnim(w, env) {
  const v = computedValue(w, w.document.body, "animation", "::before", env);
  return v ? v.value : "(unset)";
}
function lastAnim(el) { const a = el.__anims || []; return a[a.length - 1]; }

(async function main() {
  console.log("C03: Reduce motion stills the aurora and script animations");
  let motionCalls = [];
  const spyMotion = function (w) {
    // stands in for motion.min.js's Motion.animate (loaded by a <script src>
    // the mini DOM does not fetch): records what actually reaches it
    w.Motion = { animate: function (el, kf, opt) { motionCalls.push(opt || {}); return el.animate ? el.animate([], { duration: 0 }) : null; } };
  };
  let w = await bootDashboard({ aux: ["aux_prefs.js"], before: spyMotion });
  let d = w.document, html = d.documentElement;

  await scenario("aurora (body::before)", async function () {
    html.removeAttribute("data-reduce");
    T.check("the aurora drifts normally", /drift/.test(auroraAnim(w)), auroraAnim(w));
    html.setAttribute("data-reduce", "1");
    T.check("the in-app switch stills the aurora", auroraAnim(w) === "none", auroraAnim(w));
    html.removeAttribute("data-reduce");
    T.check("the OS setting stills the aurora", !/drift/.test(auroraAnim(w, { reduce: true })), auroraAnim(w, { reduce: true }));
  });

  await scenario("Web Animations", async function () {
    const el = d.createElement("div"); d.body.appendChild(el);
    el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 460, delay: 120, easing: "ease", fill: "backwards" });
    T.check("without reduced motion an animation keeps its timing", lastAnim(el).timing.duration === 460 && lastAnim(el).timing.delay === 120);
    html.setAttribute("data-reduce", "1");
    el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 460, delay: 120, fill: "backwards" });
    const a = lastAnim(el);
    T.check("with the in-app switch it takes no time", a.timing.duration === 0 && a.timing.delay === 0, a.timing);
    T.check("...but keeps its keyframes and fill (same end state)", a.keyframes.length === 2 && a.timing.fill === "backwards");
    el.animate([{ transform: "rotate(0)" }, { transform: "rotate(1turn)" }], { duration: 900, iterations: Infinity });
    T.check("an endless one runs once, instantly", lastAnim(el).timing.iterations === 1 && lastAnim(el).timing.duration === 0);
    // the page's own entrance helper (index.html revealStagger)
    const host = d.createElement("div"); d.body.appendChild(host);
    host.innerHTML = "<div class=w></div><div class=w></div>";
    w.__host = host;
    w.__run("revealStagger(window.__host.querySelectorAll('.w'),35)");
    const kids = Array.from(host.querySelectorAll(".w"));
    T.check("the hub's card entrance lands at once", kids.every(function (k) { const x = lastAnim(k); return x && x.timing.duration === 0 && x.timing.delay === 0; }),
      kids.map(function (k) { const x = lastAnim(k); return x && x.timing; }));
    html.removeAttribute("data-reduce");
  });

  await scenario("Motion One", async function () {
    const el = d.createElement("div"); d.body.appendChild(el);
    motionCalls = [];
    w.__el = el;
    w.__run("animate(window.__el,{opacity:[0,1],scale:[0.92,1]},{duration:0.45,delay:0.1})");
    T.check("without reduced motion Motion One gets the page's timing", motionCalls.length === 1 && motionCalls[0].duration === 0.45, motionCalls);
    html.setAttribute("data-reduce", "1");
    w.__run("animate(window.__el,{opacity:[0,1]},{duration:0.45,delay:0.1,repeat:Infinity})");
    const o = motionCalls[motionCalls.length - 1];
    T.check("with the in-app switch Motion One is asked for no time", o && o.duration === 0 && o.delay === 0 && o.repeat === 0, o);
    html.removeAttribute("data-reduce");
  });

  await scenario("turning it on settles what is running", async function () {
    const el = d.createElement("div"); d.body.appendChild(el);
    const long = el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 5000 });
    const spin = el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 900, iterations: Infinity });
    T.check("two script animations are running", long.playState === "running" && spin.playState === "running");
    const gear = d.getElementById("prefs-btn"), row = d.querySelector('#prefs-menu [role="menuitemcheckbox"]');
    gear.click(); row.click();
    T.check("the Preferences switch turned Reduce motion on", html.hasAttribute("data-reduce"));
    T.check("a running animation jumps to its end", long.playState === "finished", long.playState);
    T.check("an endless one is cancelled", spin.playState === "idle", spin.playState);
    row.click();
    T.check("...and off again", !html.hasAttribute("data-reduce"));
  });

  await scenario("the OS setting changing while the app runs", async function () {
    const el = d.createElement("div"); d.body.appendChild(el);
    const run = el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 5000 });
    const mqls = (w.__mqls || []).filter(function (m) { return /prefers-reduced-motion/.test(m.media); });
    T.check("the OS reduce-motion query is watched", mqls.some(function (m) { return m.__ls.length > 0; }));
    mqls.forEach(function (m) { m.matches = true; m.__ls.forEach(function (fn) { fn({ matches: true }); }); });
    T.check("switching it on settles running animations", run.playState === "finished", run.playState);
    el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 460 });
    T.check("...and later animations take no time", lastAnim(el).timing.duration === 0);
    mqls.forEach(function (m) { m.matches = false; });
    el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 460 });
    T.check("switching it off restores normal timing", lastAnim(el).timing.duration === 460);
  });

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
