// Round-2 C95 (left over from C95/C104) — three ARIA gaps:
//   * the model-swap overlay (a full-window modal shown for ~30-60 s while the
//     model server restarts) had no role, aria-live or aria-busy, so a screen
//     reader heard nothing of "Switching model" / "Ready" / "That didn't start";
//   * the page had no main landmark (the deck is the app's main content; the
//     Settings shell's own <main> would then nest inside it, so it is a <div>);
//   * Customize (#edit-toggle) is a toggle with no aria-pressed.
//
// Boots the REAL index.html (and the real aux_settings_shell.js) in the mini
// DOM (_a11y_dom.js) and drives showModelSwap/hideModelSwap/failModelSwap and
// the Customize button.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();
async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | ")); }
}

(async function main() {
  console.log("C95/C104: swap overlay announced, deck is <main>, Customize reports pressed");

  await scenario("main landmark", async function () {
    const w = await bootDashboard({ aux: ["aux_settings_shell.js"] });
    const d = w.document;
    const mains = d.querySelectorAll("main");
    const deck = d.querySelector(".deck");
    T.check("the deck is the page's <main>", !!deck && deck.tagName === "MAIN", deck && deck.tagName);
    T.check("it holds the hub, the chat and the sidebar",
      !!deck && deck.contains(d.getElementById("wgrid")) && deck.contains(d.getElementById("msgs")) && deck.contains(d.getElementById("chat-side")));
    T.check("...but not the header or the modal overlays",
      !!deck && !deck.contains(d.querySelector("header")) && !deck.contains(d.getElementById("wpop")) && !deck.contains(d.getElementById("wcenter")));
    T.check("the Settings shell is built", !!d.getElementById("set-panels"));
    T.check("there is exactly one <main> (Settings no longer nests a second)", mains.length === 1, mains.length);
  });

  await scenario("model-swap overlay", async function () {
    const w = await bootDashboard();
    const d = w.document, deck = d.querySelector(".deck");
    w.__run("showModelSwap('Small 9B')"); await w.__tick();
    const o = d.getElementById("model-swap"), card = o && o.querySelector(".swapcard");
    T.check("the overlay shows", !!o && !o.hidden);
    T.check("its card is a polite status region",
      !!card && card.getAttribute("role") === "status" && card.getAttribute("aria-live") === "polite",
      card && [card.getAttribute("role"), card.getAttribute("aria-live")]);
    T.check("the deck reports busy while it shows", deck.getAttribute("aria-busy") === "true", deck.getAttribute("aria-busy"));
    w.__run("hideModelSwap(true,'Small 9B')");
    T.check("'Ready' lands inside the live region", /Ready/.test(card.textContent));
    w.__flushTimers(3);
    T.check("the overlay goes away", o.hidden);
    T.check("...and the deck is no longer busy", !deck.hasAttribute("aria-busy"), deck.getAttribute("aria-busy"));

    w.__run("showModelSwap('Small 9B')");
    T.check("busy again on the next switch", deck.getAttribute("aria-busy") === "true");
    w.__run("failModelSwap('The switch did not start.')"); w.__flushTimers(3);
    T.check("a failed switch also clears busy", o.hidden && !deck.hasAttribute("aria-busy"));
    w.__run("showModelSwap('Small 9B')");
    w.__run("hideModelSwap(false,'Small 9B')"); w.__flushTimers(3);
    T.check("a slow load (Still loading...) also clears busy", o.hidden && !deck.hasAttribute("aria-busy"));
  });

  await scenario("Customize", async function () {
    const w = await bootDashboard();
    const b = w.document.getElementById("edit-toggle");
    T.check("Customize starts not pressed", b.getAttribute("aria-pressed") === "false", b.getAttribute("aria-pressed"));
    b.click(); await w.__tick();
    T.check("pressing it reports pressed", b.getAttribute("aria-pressed") === "true", b.getAttribute("aria-pressed"));
    b.click(); await w.__tick();
    T.check("pressing it again reports not pressed", b.getAttribute("aria-pressed") === "false", b.getAttribute("aria-pressed"));
  });
  T.done();
})();
