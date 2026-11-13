// t_e132_sidebar_once.js — 2026-11-09 audit E132.
//
// In full chat mode renderChatSide() rebuilds the conversation list after
// every reply, rename, pin and delete, and called revealStagger() on every row
// with no key: the whole list blinked out and rose back in each time, against
// the motion rule "entrances on re-rendered lists use {key}, so each item
// animates once per page life" (docs/plans/motion-system-2026-10.md §6.6).
// Now each row is keyed on its conversation id: a row rises the first time it
// is drawn, and a repaint moves nothing but a conversation that is new.
//
// The REAL index.html boots in the mini DOM after the REAL aux_anim.js, against
// the recording library stubs (no timers). /api/sessions is a table here.
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, harness } = require("./_a11y_dom.js");
const S = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const ANIM = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");
const PAGE = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
const RISE = JSON.stringify({ opacity: [0, 1], y: [10, 0] });

(async function main() {
  console.log("full-chat sidebar rows rise once per page life (E132)");
  const sessions = [{ id: "chat-a", title: "Alpha", updated: 3 }, { id: "chat-b", title: "Beta", updated: 2 },
                    { id: "chat-c", title: "Gamma", updated: 1 }];
  let L = null;
  const w = await bootDashboard({ html: PAGE, routes: { "/api/sessions": { sessions: sessions, total: 3 } },
    before: function (w0) { delete w0.toast; L = S.install(w0, {}); w0.__run(ANIM, "aux_anim.js"); } });
  const d = w.document;
  const risen = function () {
    return L.of("motion", "animate").filter(function (c) {
      return c.el && c.el.classList && c.el.classList.contains("cs-item") && JSON.stringify(c.kf) === RISE;
    }).map(function (c) { return (c.el.querySelector(".t") || {}).textContent; });
  };
  const drain = async function () { for (let i = 0; i < 6; i++) { L.finishAll(); await w.__tick(); } };
  T.check("the page has renderChatSide and the runtime", typeof w.renderChatSide === "function" && !!w.HermesAnim);
  d.querySelector(".deck").dataset.chat = "full";

  L.clear();
  await w.renderChatSide(); await w.__tick();
  const first = risen();
  T.check("first paint: the rows rise in", first.indexOf("Alpha") >= 0 && first.indexOf("Gamma") >= 0, first);
  await drain();

  L.clear();
  await w.renderChatSide(); await w.__tick();
  T.check("THE FIX: a repaint after a turn / rename / pin moves no row", risen().length === 0, risen());
  await drain();

  sessions.unshift({ id: "chat-d", title: "Delta", updated: 4 });
  L.clear();
  await w.renderChatSide(); await w.__tick();
  T.check("a conversation new to the list rises in, alone", JSON.stringify(risen().filter(function (t) { return t !== "(new conversation)"; })) === '["Delta"]', risen());
  await drain();
  T.check("no row is left with an inline opacity or transform",
    Array.from(d.querySelectorAll("#cs-convos .cs-item")).every(function (el) { return !el.style.opacity && !el.style.transform; }));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
