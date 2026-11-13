// t_anim_glass.js — aux_glass.js living with the motion runtime (aux_anim.js),
// docs/plans/motion-system-2026-10.md §6.6 ("Pointer light stays on the
// glass", "No JS library on the header subtree") plus the two aux_glass items
// the 2026-10-21 review left open:
//   * E7: the light re-measured only on CSS transition/animation events, and
//     GSAP, anime and Motion's JS-driven values fire none — so during a Flip
//     or a view entrance it lit a stale box. aux_anim now dispatches
//     hermes:animstart/animend and aux_glass listens for both;
//   * a press on a control INSIDE a card set the card's transform-origin and
//     nothing ever cleared it (no scale transition ran, so no transitionend):
//     the next rotate/scale (the Customize jiggle, a pop entrance) swung
//     around the old press point;
//   * a lit, scrolled scroller whose content shrank through a class toggle
//     alone kept its scroll range propped open (the --gsy observer watched
//     child nodes, text and [hidden] only).
//
// The REAL aux_glass.js and aux_anim.js run in the mini DOM with a clock the
// test controls; the libraries are the recording stubs (_anim_stubs.js).
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, harness, PointerEvent, Event, CustomEvent } = require("./_a11y_dom.js");
const S = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const ANIM = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}
function animEvent(w, type, ms) { return new w.CustomEvent(type, { bubbles: true, detail: { ms: ms, kind: "flip" } }); }

(async function main() {
  console.log("aux_glass.js with the motion runtime");
  let now = 1000;
  const clock = function (w0) { w0.performance = { now: function () { return now; } }; };

  await scenario("hermes:animstart re-measures the lit surface", async function () {
    const w = await bootDashboard({ aux: ["aux_glass.js"], before: clock });
    const d = w.document;
    const host = d.createElement("div"); host.id = "host";
    const surf = d.createElement("section"); surf.className = "glass"; surf.innerHTML = "<p>card</p>";
    const other = d.createElement("div"); other.id = "other";
    host.appendChild(surf); d.body.appendChild(host); d.body.appendChild(other);
    const move = function (x, y) { surf.firstChild.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: x, clientY: y, pointerType: "mouse" })); };
    const my = function () { return surf.style.getPropertyValue("--my"); };
    const frame = function () { w.__flushTimers(1); };
    surf.__rect = { left: 100, top: 100, width: 300, height: 200 };
    move(150, 160);
    now += 2000;                                   // long after entry: the box is cached
    surf.__rect = { left: 100, top: 140, width: 300, height: 200 };
    move(150, 170); frame();
    T.check("(control) a JS-moved surface keeps its stale box with no event", my() === "70.0px", my());
    other.dispatchEvent(animEvent(w, "hermes:animstart", 400));
    move(150, 171); frame();
    T.check("(control) an animation on an unrelated element changes nothing", my() === "71.0px", my());
    host.dispatchEvent(animEvent(w, "hermes:animstart", 1200));
    move(150, 180); frame();
    T.check("hermes:animstart on an ancestor re-measures the lit surface", my() === "40.0px", my());
    now += 900;                                    // past the 600 ms CSS window, inside the 1200 ms animation
    surf.__rect = { left: 100, top: 160, width: 300, height: 200 };
    move(150, 180); frame();
    T.check("detail.ms stretches the window to the animation's own length", my() === "20.0px", my());
    now += 2000;
    surf.__rect = { left: 100, top: 170, width: 300, height: 200 };
    move(150, 180); frame();
    T.check("...and it closes after it", my() === "20.0px", my());
    host.dispatchEvent(animEvent(w, "hermes:animend", 0));
    frame();
    T.check("hermes:animend repaints once where it landed, under a still pointer", my() === "10.0px", my());
    host.dispatchEvent(animEvent(w, "hermes:animstart", 99999));
    now += 1600;
    surf.__rect = { left: 100, top: 100, width: 300, height: 200 };
    move(150, 150); frame();
    now += 10;
    surf.__rect = { left: 100, top: 120, width: 300, height: 200 };
    move(150, 150); frame();
    T.check("a huge detail.ms is capped (1.5 s), not a per-frame measure forever", my() === "50.0px", my());
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("the real runtime drives it (flip, enter)", async function () {
    let L = null;
    const w = await bootDashboard({ aux: ["aux_glass.js"], before: function (w0) {
      clock(w0);
      L = S.install(w0, { gsap: true });
      w0.__run(ANIM, "aux_anim.js");               // index.html order: Motion, aux_anim, the inline shell
    } });
    const d = w.document, A = w.HermesAnim;
    T.check("(the runtime loaded before the shell)", !!A && A.v === 1);
    const grid = d.createElement("div"); grid.id = "fgrid";
    const card = d.createElement("div"); card.className = "w glass"; card.setAttribute("data-flip-id", "a"); card.innerHTML = "<p>a</p>";
    const card2 = d.createElement("div"); card2.className = "w"; card2.setAttribute("data-flip-id", "b");
    grid.appendChild(card); grid.appendChild(card2); d.body.appendChild(grid);
    const move = function (x, y) { card.firstChild.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: x, clientY: y, pointerType: "mouse" })); };
    const mx = function () { return card.style.getPropertyValue("--mx"); };
    card.__rect = { left: 0, top: 0, width: 200, height: 100 };
    move(50, 50);
    now += 2000;
    card.__rect = { left: 40, top: 0, width: 200, height: 100 };
    move(50, 50); w.__flushTimers(1);
    T.check("(control) a reorder with no event leaves the light on the old box", mx() === "50.0px", mx());
    const h = A.flip(grid, function () { grid.insertBefore(card2, card); });
    move(50, 50); w.__flushTimers(1);
    T.check("HermesAnim.flip() makes aux_glass re-measure the card it moves", !h.done && mx() === "10.0px", mx());
    A.settle();
    now += 2000;
    card.__rect = { left: 0, top: 0, width: 200, height: 100 };
    w.__flushTimers(2);
    T.check("...and repaint where it lands when the flip ends", mx() === "50.0px", mx());
    now += 2000;
    card.__rect = { left: 20, top: 0, width: 200, height: 100 };
    A.enter(card, "pop");
    move(50, 50); w.__flushTimers(1);
    T.check("HermesAnim.enter() (Motion, JS-driven scale) does the same", mx() === "30.0px", mx());
    L.finishAll(); await w.__tick();
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("a press that never scales the card leaves no pivot behind", async function () {
    const w = await bootDashboard({ aux: ["aux_glass.js"], before: clock });
    const d = w.document;
    const card = d.createElement("div"); card.className = "w"; card.innerHTML = "<button id='inner'>Run</button>";
    d.getElementById("wgrid").appendChild(card);
    card.__rect = { left: 100, top: 200, width: 400, height: 150 };
    const btn = card.querySelector("#inner");
    const te = function (type, prop) { const e = new Event(type, { bubbles: true }); e.propertyName = prop; card.dispatchEvent(e); };
    btn.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 130, clientY: 220, button: 0 }));
    T.check("(control) a press on a control inside the card still sets the pivot", card.style.transformOrigin === "30.0px 20.0px", card.style.transformOrigin);
    btn.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
    T.check("not cleared at once (a quick tap's run event may still be on its way)", card.style.transformOrigin !== "");
    w.__flushTimers(1);
    T.check("no scale transition ran: the pivot is cleared shortly after the release", card.style.transformOrigin === "", card.style.transformOrigin);
    // control: a real gel press keeps the pivot until its release spring ends
    card.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 120, clientY: 210, button: 0 }));
    te("transitionrun", "scale");
    d.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
    w.__flushTimers(1);
    T.check("a press that DID scale keeps the pivot past that point", card.style.transformOrigin === "20.0px 10.0px", card.style.transformOrigin);
    te("transitionend", "scale");
    T.check("...and its finished release spring clears it as before", card.style.transformOrigin === "");
    // pressed again before the check runs: the new press owns the pivot
    btn.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 130, clientY: 220, button: 0 }));
    d.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
    card.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 140, clientY: 230, button: 0 }));
    w.__flushTimers(1);
    T.check("a press held when the check runs keeps its pivot", card.style.transformOrigin === "40.0px 30.0px", card.style.transformOrigin);
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("a class toggle that shrinks a lit, scrolled scroller", async function () {
    const w = await bootDashboard({ aux: ["aux_glass.js"], before: clock });
    const d = w.document;
    const sc = d.createElement("section"); sc.className = "glass"; sc.innerHTML = "<div class='rows'>rows</div>";
    d.body.appendChild(sc);
    sc.__rect = { left: 500, top: 100, width: 300, height: 200 };
    let st = 0;
    const content = function () { return sc.firstChild.classList.contains("collapsed") ? 500 : 2000; };
    const layer = function () { return parseFloat(sc.style.getPropertyValue("--gsy")) || 0; };
    Object.defineProperty(sc, "scrollHeight", { configurable: true, get: function () { return Math.max(content(), layer() + 200); } });
    Object.defineProperty(sc, "scrollTop", { configurable: true,
      get: function () { st = Math.max(0, Math.min(st, sc.scrollHeight - 200)); return st; }, set: function (v) { st = v; } });
    sc.scrollTop = 900;
    sc.firstChild.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 600, clientY: 150, pointerType: "mouse" }));
    T.check("(control) the lit, scrolled scroller carries its offset", layer() === 900);
    sc.firstChild.classList.add("collapsed");       // no child node, text or [hidden] change
    await w.__tick(); w.__flushTimers(1);
    T.check("the class toggle is seen: the range clamps to the real end", sc.scrollTop === 300 && sc.scrollHeight === 500, { st: sc.scrollTop, sh: sc.scrollHeight });
    T.check("...and the light layer follows", layer() === 300, sc.style.getPropertyValue("--gsy"));
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  await scenario("no observer feedback, one pointer listener", async function () {
    let before = [], L = null;
    const posts = [];
    const w = await bootDashboard({ aux: ["aux_glass.js", "aux_window.js"], before: function (w0) {
      clock(w0);
      w0.webkit = { messageHandlers: { hermesWindow: { postMessage: function (m) { posts.push(m); } } } };
      L = S.install(w0, { gsap: true, anime: true });
      before = w0.document.__observers.slice();
      w0.__run(ANIM, "aux_anim.js");
    } });
    const d = w.document, A = w.HermesAnim;
    const mine = d.__observers.filter(function (o) { return before.indexOf(o) < 0; });
    const regs = [];
    mine.forEach(function (o) { o.__targets.forEach(function (t) { regs.push(t); }); });
    // aux_anim's observers are created before index.html's script runs, so the
    // set above is aux_anim's plus whatever the shell/aux_glass/aux_window add
    // later; aux_anim's own are the ones on <html> and on .stage>.view / .stage
    const ownRegs = regs.filter(function (r) { return r.target === d.documentElement && r.opt.attributeFilter && r.opt.attributeFilter.indexOf("data-reduce") >= 0 && r.opt.attributeFilter.length === 1 ||
      (r.target.classList && (r.target.classList.contains("view") || r.target.classList.contains("stage"))); });
    T.check("aux_anim's observers: <html>[data-reduce], .stage children, each view's [hidden]", ownRegs.length >= 3, ownRegs.map(function (r) { return [r.target.localName + "#" + (r.target.id || ""), r.opt]; }));
    T.check("...none of them watches style, class or data-glint (so nothing aux_glass/aux_window write can wake them)", ownRegs.every(function (r) {
      const f = r.opt.attributeFilter;
      return !r.opt.attributes || (f && f.indexOf("style") < 0 && f.indexOf("class") < 0 && f.indexOf("data-glint") < 0);
    }));
    T.check("...and none is subtree-wide", ownRegs.every(function (r) { return !r.opt.subtree; }));
    T.check("one pointermove listener in the page (aux_glass's)", (d.__l.pointermove || []).length === 1, (d.__l.pointermove || []).length);
    // the JS libraries never write into the header: watch it the way aux_window does
    const hdr = d.querySelector("header"), recs = [];
    new w.MutationObserver(function (r) { r.forEach(function (x) { recs.push(x); }); }).observe(hdr, { subtree: true, childList: true, attributes: true, characterData: true });
    const warns = []; const cw = console.warn; console.warn = function (m) { warns.push(String(m)); };
    const pill = d.getElementById("model-pill") || hdr.firstElementChild;
    const c0 = L.calls.length;
    A.flip(hdr, function () {});
    A.sequence("hdr", function (tl) { tl.from(pill, { y: 6, opacity: 0 }); });
    A.draw(hdr.querySelector("svg path") || pill); A.words(pill);
    console.warn = cw;
    await w.__tick();
    T.check("flip/sequence/draw/words on the header create no tween at all", !L.calls.slice(c0).some(function (c) {
      return c.fn === "from" || c.fn === "tl.from" || c.fn === "animate" || c.fn === "getState";
    }), L.calls.slice(c0).map(function (c) { return c.lib + "." + c.fn; }));
    T.check("...and make no header mutation for aux_window to re-measure", recs.length === 0, recs.map(function (r) { return r.type + ":" + r.attributeName; }));
    // the light itself on the header: aux_window ignores data-glint and the --mx/--my writes
    const seg = hdr.querySelector(".seg");
    seg.__rect = { left: 10, top: 10, width: 200, height: 30 };
    const p0 = posts.length;
    for (let i = 0; i < 20; i++) { seg.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 20 + i, clientY: 20, pointerType: "mouse" })); w.__flushTimers(1); }
    await w.__tick(); w.__flushTimers(3);
    T.check("20 light moves over the header post no drag-region message", posts.length === p0, posts.length - p0);
    T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  });

  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
