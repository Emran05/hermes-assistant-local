// V02/V03 (docs/audits/2026-11-12-visual-audit.md) — Settings cards with no
// inner padding and uneven spacing.
//
//   * "Code graph" (aux_graphify.js), "Software update" (aux_update.js) and
//     "First-run setup" (aux_onboarding.js) put their content straight after
//     the <h2>; the card convention is <h2> + <div class="body">, and .body is
//     what carries the 10/14 padding. Without it the content sat on the card
//     border and .card{overflow:hidden} clipped it.
//   * Update and First-run setup were appended to the panel <section> itself,
//     outside its .set-body (the gap:15px flex column that spaces the cards),
//     so they sat flush against their neighbours; and a card placed AFTER the
//     shell's empty [data-legacy-slot] div got the gap twice (30px).
//
// The REAL modules run on the mini DOM against a page shaped like the
// settings shell's panels; fetch answers from a route table.
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, harness } = require("./_a11y_dom.js");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const T = harness("V02/V03 settings card mounts");

const PANEL = (id) => '<section id="' + id + '" class="set-panel"><div class="set-head"></div>' +
  '<div class="set-body"><section id="mind-extra-config" class="card glass set-legacy"><h2>x</h2></section>' +
  "<div data-legacy-slot></div></div></section>";

async function settle(w) { for (let i = 0; i < 8; i++) { await new Promise((r) => setImmediate(r)); w.__flushTimers && w.__flushTimers(1); } }

(async function () {
  const w = createWindow({ html: "<!doctype html><html><head></head><body>" +
    '<div id="view-mind"><div id="set-shell">' + PANEL("sec-overview") + PANEL("sec-system") +
    "</div></div></body></html>" });
  w.__routes = {
    "/api/version": { version: "1.3.2", commit: "abc1234", checkout: "git" },
    "/api/update/check": { checked_at: 0, update_available: false },
    "/api/update/status": { running: false },
    "/api/graph/stats": { ok: true, available: true, nodes: 10, edges: 20, communities: 3, god_nodes: [] },
    "/api/onboarding/state": { ok: true, done: true, done_at: 1789000000 },
    "/api/trace/summary": { ok: true, turns: 0, spans: [] }
  };
  for (const rel of ["aux_onboarding.js", "aux_graphify.js", "aux_trace.js", "aux_update.js"]) {
    w.__run(fs.readFileSync(path.join(REPO, "dashboard", rel), "utf8"), rel);
  }
  await w.mindExtras();
  await settle(w);
  const d = w.document;

  function bodyWrapped(id) {
    const c = d.getElementById(id);
    if (!c) return "missing";
    const kids = Array.from(c.children).filter((k) => k.tagName !== "STYLE");
    if (kids.length !== 2) return "children: " + kids.map((k) => k.tagName + "." + k.className).join(",");
    if (kids[0].tagName !== "H2") return "first is " + kids[0].tagName;
    if (!kids[1].classList.contains("body")) return "second is ." + kids[1].className;
    return "ok";
  }
  for (const id of ["mind-extra-graphify", "mind-extra-update", "mind-extra-onboarding"]) {
    const r = bodyWrapped(id);
    T.check(id + ": content sits in <h2> + <div class=\"body\">", r === "ok", r);
  }
  const gfy = d.getElementById("mind-extra-graphify");
  T.check("graphify: the stats and the Explain button are inside .body",
    !!(gfy && gfy.querySelector(".body .gfy-stats") && gfy.querySelector(".body .gfy-q button")));
  const upd = d.getElementById("mind-extra-update");
  T.check("update: the version line and the terminal line are inside .body",
    !!(upd && upd.querySelector(".body .upd-head") && /update\.sh/.test(upd.querySelector(".body").textContent)));

  function placed(id, sec) {
    const c = d.getElementById(id);
    const body = d.querySelector("#" + sec + " > .set-body");
    if (!c) return "missing";
    if (c.parentNode !== body) return "parent is " + (c.parentNode.id || c.parentNode.className);
    const kids = Array.from(body.children);
    const slot = body.querySelector("[data-legacy-slot]");
    if (kids.indexOf(c) > kids.indexOf(slot)) return "after the legacy slot";
    return "ok";
  }
  for (const [id, sec] of [["mind-extra-update", "sec-system"], ["mind-extra-trace", "sec-system"],
                           ["mind-extra-onboarding", "sec-overview"]]) {
    const r = placed(id, sec);
    T.check(id + ": inside #" + sec + " .set-body, before the empty [data-legacy-slot]", r === "ok", r);
  }
  T.check("update and onboarding cards carry set-legacy like the relocated cards",
    !!(upd && upd.classList.contains("set-legacy")) &&
    d.getElementById("mind-extra-onboarding").classList.contains("set-legacy"));

  // a repaint keeps the place (no drift to the end of the panel)
  await w.mindExtras();
  await settle(w);
  T.check("after a second mindExtras() pass the cards are still before the slot",
    placed("mind-extra-update", "sec-system") === "ok" && placed("mind-extra-onboarding", "sec-overview") === "ok");

  // V03: the "last run" date wraps as one unit
  const when = d.querySelector("#mind-extra-onboarding .onb-set-when");
  T.check("onboarding: 'last run <date>' is its own nowrap span",
    !!when && /last run/.test(when.textContent) &&
    /\.onb-set-when\{white-space:nowrap\}/.test(d.getElementById("mind-extra-onboarding").innerHTML), when && when.textContent);

  // V02: the config card's file row wraps instead of being cut
  const cfg = fs.readFileSync(path.join(REPO, "dashboard", "aux_config.js"), "utf8");
  T.check("config: .cf-file wraps (the exported time is no longer cut off)",
    /"\.cf-file\{display:flex;flex-wrap:wrap;/.test(cfg));
  T.done();
})().catch((e) => { console.log("  FAIL threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
