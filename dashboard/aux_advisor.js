// aux_advisor.js — the model advisor's UI (round 3, C3 "advisor-ui").
//
// Spec: docs/plans/model-advisor-2026-10.md §8 (UI) over the §9 routes that
// aux_advisor.py serves:
//   GET  /api/advisor/state[?bust=1]   hw, limits, answers, questions, result…
//   GET  /api/advisor/rank?uses=&priority=&power=   a PURE re-rank, no writes
//   POST /api/advisor/answers {uses?, priority?, power?, uncensored_ack?}
//
// window.hermesAdvisor = { v:1, step, mountCard(host?), explain(row), fmt, ackText() }
//
//   step      the onboarding "models" step BODY (shared contract 2 of round 3):
//             step.render(ctx) -> html, step.wire(node, ctx), step.save(ctx).
//             aux_onboarding.js keeps rendering its own action row (Download
//             recommended / I'll choose later / Continue) BELOW this body and
//             falls back to its legacy body when `step` is absent or render
//             throws. ctx = {rerun, data, repaint, setErr, getPick, setPick}.
//   mountCard the Settings › Agent & Models "Model advisor" card (§8.2):
//             mounted DIRECTLY into #sec-models as its first card (order:-1),
//             never into #view-mind — the aux_network.js discipline.
//   explain   the four <dt>/<dd> pairs the model menu's Details panel gains
//             (§8.3) for one /api/models row that carries `advice`.
//   fmt       the pure formatters (numbers, 12-hour time, the plan lines).
//
// WHAT NEVER HAPPENS (§8.4). Nothing here switches, downloads, activates or
// restarts anything on its own. The only request on load is GET state (and
// only once something mounts). Answer changes re-rank through the PURE rank
// route (debounced 150 ms). Every write is an explicit click: the
// acknowledgement checkbox, "Use these answers", and the card's per-plan
// buttons (Add to model menu, Download behind confirm(), Use as background
// lane, Make it the assistant through the model menu's own switch path).
// The advisor is ADVISORY: the model menu still refuses only fit === "no" and
// a short disk (D7) — the modelDetailsHTML wrapper below only adds text.
//
// UNCENSORED (owner decision): those builds are alternatives only, never the
// Recommended slot (the server guarantees it), and appear only after the
// STORED acknowledgement. Ticking the "Unfiltered research" chip shows the
// server's own acknowledgement copy with a checkbox; checking it POSTs
// {uncensored_ack:true}, and only then does the chip turn on. An uncensored
// alternative's "Use this instead" stays locked until that ack is stored.
//
// RE-RENDERS. A fetch callback re-renders only this module's own nodes
// ([data-adv-root] and its parts) and never calls ctx.repaint() — no repaint
// loops with the onboarding sheet. Rank results replace .adv-res only, so a
// keyboard user's focus in the questionnaire survives every re-rank.
//
// MOTION. Only through window.HermesAnim, transform/opacity only, and only
// after an owner's own action (a re-rank they caused, the acknowledgement
// panel opening). Nothing animates on load, on a state refresh or inside the
// app <header> (the model-menu wrapper is text only).
//
// Design laws (CLAUDE.md): zero emoji, bespoke two-tone SVG, esc() on every
// interpolation, every global typeof-guarded so a headless harness can eval
// this file, explicit transition-property, >=40 px targets, 12-hour times,
// theme tokens inherited from index.html and never re-declared, flat rows.
(function () {
  "use strict";

  var W = (typeof window !== "undefined") ? window
        : (typeof globalThis !== "undefined") ? globalThis : null;
  if (!W) return;

  // ---- the modelDetailsHTML wrapper (§8.3) ----------------------------------
  // Guarded: typeof check, !fn.__adv (a second load never double-wraps), and
  // "loaded later wins" (expand.js's pattern): reassigning the global replaces
  // what loadModels() calls by name. Re-tried at DOMContentLoaded in case a
  // page defines it after this file. Runs before the second-load early return
  // below on purpose, so a reload of this file still fixes a lost wrap.
  function wrapDetails() {
    var fn;
    try { fn = W.modelDetailsHTML; } catch (e) { fn = null; }
    if (typeof fn !== "function" || fn.__adv) return false;
    var wrapped = function (m) {
      var h = fn.apply(this, arguments);
      try {
        var add = (W.hermesAdvisor && typeof W.hermesAdvisor.explain === "function")
          ? W.hermesAdvisor.explain(m) : "";
        if (add && typeof h === "string" && /<\/dl>\s*$/.test(h)) {
          // a FUNCTION replacer: a string replacement would read "$&", "$`", "$'"
          // or "$1" inside the advice text (a model label, a note) as patterns
          // and duplicate or splice the markup around them
          return h.replace(/<\/dl>\s*$/, function () { return add + "</dl>"; });
        }
      } catch (e) {}
      return h;
    };
    wrapped.__adv = true;
    wrapped.__advOrig = fn;
    try { W.modelDetailsHTML = wrapped; } catch (e) { return false; }
    return true;
  }

  if (W.hermesAdvisor && W.hermesAdvisor.__adv) {
    wrapDetails();            // the file ran twice: keep the first instance and its state
    return;
  }

  function D() { return (typeof document !== "undefined") ? document : null; }
  function E(s) {
    if (typeof esc === "function") { try { return esc(s); } catch (e) {} }
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function warnOnce(k, msg) {
    if (warnOnce[k]) return;
    warnOnce[k] = 1;
    try { if (typeof console !== "undefined") console.warn("hermesAdvisor: " + msg); } catch (e) {}
  }
  // the motion runtime, or a stand-in that does nothing (no runtime = no motion)
  var ANULL = { enter: function () { return null; } };
  function A() {
    try {
      var h = W.HermesAnim;
      if (h && typeof h.enter === "function") return h;
    } catch (e) {}
    return ANULL;
  }

  var CARD_ID = "mind-extra-advisor";
  var PANEL_ID = "sec-models";
  var CSS_ID = "adv-css";
  var DEBOUNCE_MS = 150;
  var STALE_MS = 5 * 60 * 1000;     // the card re-reads state on a Settings visit after this

  // The six uses, mirrored from aux_advisor.py only for the moment before the
  // state answers (the state's own `questions` win as soon as it does).
  var DEFAULT_USES = [
    ["chat", "Everyday chat and writing"], ["agent", "Getting things done on this Mac"],
    ["code", "Coding help"], ["docs", "Long documents and big files"],
    ["vision", "Screenshots and images"], ["uncensored", "Unfiltered research (no refusals)"]];
  var PRIORITIES = [["faster", "Faster replies"], ["balanced", "Balanced"], ["best", "Best answers"]];
  var POWERS = [["ac", "Mostly plugged in"], ["battery", "Often on battery"]];

  // ---- bespoke two-tone glyphs (accent fill at low opacity + currentColor stroke)
  function svg(body, cls) {
    return '<svg class="' + (cls || "adv-ic") + '" viewBox="0 0 24 24" aria-hidden="true" ' +
      'focusable="false">' + body + "</svg>";
  }
  var G = {
    chip: svg('<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" opacity=".18"/>' +
      '<rect x="6" y="6" width="12" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
      '<rect x="10" y="10" width="4" height="4" rx="1" fill="currentColor"/>' +
      '<path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" fill="none" ' +
      'stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'),
    compass: svg('<circle cx="12" cy="12" r="9" fill="currentColor" opacity=".16"/>' +
      '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
      '<path d="M15.5 8.5l-2 5-5 2 2-5z" fill="currentColor" stroke="currentColor" ' +
      'stroke-width="1.2" stroke-linejoin="round"/>', "ic"),
    unlock: svg('<rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" opacity=".25"/>' +
      '<rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.7"/>' +
      '<path d="M8.5 11V8a3.5 3.5 0 0 1 6.7-1.4" fill="none" stroke="currentColor" ' +
      'stroke-width="1.7" stroke-linecap="round"/>', "adv-ic adv-ic-s"),
    check: svg('<circle cx="12" cy="12" r="9" fill="currentColor" opacity=".18"/>' +
      '<path d="M8 12.4l2.6 2.6L16.2 9" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round"/>', "adv-ic adv-ic-s"),
    tick: svg('<path d="M6.5 12.5l3.6 3.6 7.4-7.6" fill="none" stroke="currentColor" ' +
      'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>', "adv-tick"),
    chev: svg('<path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round"/>', "adv-chev")
  };

  // ==========================================================================
  // pure formatters (window.hermesAdvisor.fmt)
  // ==========================================================================
  var NB = " ";
  function num(x) { return typeof x === "number" && isFinite(x); }
  // "36" / "4.5" — the way aux_advisor.py's plan lines quote GB
  // Python's round(): half to even, so a figure here reads exactly like the
  // same figure in the server's own "why" sentence (32.5 -> 32 in both)
  function rint(x) {
    var f = Math.floor(x), d = x - f;
    if (Math.abs(d - 0.5) < 1e-9) return (f % 2 === 0) ? f : f + 1;
    return Math.round(x);
  }
  function gbN(x) {
    if (!num(x)) return "?";
    if (x >= 10) return String(rint(x));
    return String(rint(x * 10) / 10);
  }
  function gb(x) { return gbN(x) + NB + "GB"; }
  function tps(x) { return num(x) ? String(rint(x)) : "?"; }
  function secs(s) {
    if (!num(s)) return "?";
    if (s < 90) return Math.max(1, rint(s)) + NB + "s";
    return rint(s / 60) + NB + "min";
  }
  // tokens -> "25K"; a power-of-two context (131072) reads as 128K. dec: one
  // decimal under 100K ("20.6K"), for the setup prompt.
  function ctxK(t, dec) {
    if (!num(t) || t <= 0) return "typical";
    var k = (t % 1024) ? t / 1000 : t / 1024;
    if (dec && k < 100) return String(Math.round(k * 10) / 10) + "K";
    return String(Math.round(k)) + "K";
  }
  // 12-hour clock, always: "3:04 PM". Accepts a Date, ms or unix seconds.
  function time12(ts) {
    var d = null;
    if (ts instanceof Date) d = ts;
    else if (num(ts)) d = new Date(ts < 1e12 ? ts * 1000 : ts);
    if (!d || isNaN(d.getTime())) return "";
    var h = d.getHours(), m = d.getMinutes(), ap = h < 12 ? "AM" : "PM";
    h = h % 12; if (h === 0) h = 12;
    return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
  }
  function rnd(x, nd) { var p = Math.pow(10, nd || 0); return Math.round(x * p) / p; }
  function rangeText(r, unit, nd) {
    if (!Array.isArray(r) || r.length < 2 || !num(r[0]) || !num(r[1])) return "";
    return rnd(r[0], nd) + "–" + rnd(r[1], nd) + (unit ? NB + unit : "");
  }
  var BASIS = { "measured-here": "measured here", reference: "measured on the reference Mac",
                formula: "formula" };
  function basisText(b) { return BASIS[b] || (b ? String(b) : ""); }

  // "~49 tok/s short · ~32 in a 25K turn (estimate)" or
  // "41 tok/s measured here in a 25K turn (23 replies)". e = an estimate
  // object or a models_payload `advice` row (same field names).
  function speedLine(e) {
    if (!e || typeof e !== "object") return "";
    var b = e.basis || {}, m = (e.measured && typeof e.measured === "object") ? e.measured : {};
    var k = ctxK(e.turn_ctx);
    if (b.decode === "measured-here" && num(e.decode_turn_tps)) {
      return tps(e.decode_turn_tps) + " tok/s measured here in a " + k + " turn" +
        (m.n_turn ? " (" + m.n_turn + " replies)" : "");
    }
    var parts = [], est = false;
    if (num(e.decode_short_tps)) {
      if (b.decode_short === "measured-here") {
        parts.push(tps(e.decode_short_tps) + " tok/s short, measured here" +
          (m.n_short ? " (" + m.n_short + " replies)" : ""));
      } else { parts.push("~" + tps(e.decode_short_tps) + " tok/s short"); est = true; }
    }
    if (num(e.decode_turn_tps)) { parts.push("~" + tps(e.decode_turn_tps) + " in a " + k + " turn"); est = true; }
    if (!parts.length) return "";
    return parts.join(" · ") + (est ? " (estimate)" : "");
  }
  // "~27 s to read the 20.6K-token setup"
  function wakeLine(e) {
    if (!e || !num(e.prefill_s)) return "";
    var meas = (e.basis || {}).prefill === "measured-here";
    return (meas ? "" : "~") + secs(e.prefill_s) + " to read the " + ctxK(e.prefix_tokens, true) +
      "-token setup" + (meas ? ", measured here" : "");
  }
  // "~26 GB of a 48 GB budget"
  function memLine(e, limit) {
    if (!e || !num(e.resident_gb)) return "";
    var lim = num(limit) ? limit : e.limit_gb;
    return "~" + gb(e.resident_gb) + (num(lim) ? " of a " + gb(lim) + " budget" : " with its cache");
  }
  function laneLabel(l) { return (l && (l.label || l.id)) || "?"; }
  function planHeading(p) {
    if (!p || !p.primary) return "";
    var a = laneLabel(p.primary);
    return p.background ? a + " as the assistant, " + laneLabel(p.background) + " for background work"
      : a + " as the assistant, with no background lane";
  }
  function planKey(p) {
    if (!p || !p.primary) return "";
    return String(p.primary.id || "") + "|" + String((p.background && p.background.id) || "");
  }
  // the model menu's 1.0.3 phrasing, unchanged; without the roster `ram` only
  // the verdict half can be said honestly
  function fitLine(fit, ram, machineGb) {
    var tail = fit === "no" ? " · too big to run here"
      : (fit === "tight" ? " · tight, expect swapping" : "");
    if (num(ram) && num(machineGb) && machineGb > 0) {
      return "needs ~" + ram + NB + "GB · this Mac has " + Math.round(machineGb) + NB + "GB" + tail;
    }
    if (fit === "no") return "too big to run here";
    if (fit === "tight") return "tight on this Mac, expect swapping";
    return "";
  }
  function verdictText(v) {
    return v === "fits" ? "fits" : v === "tight" ? "tight: expect swapping"
      : v === "no" ? "does not fit" : "no estimate";
  }
  function rankQuery(a) {
    a = a || {};
    var uses = Array.isArray(a.uses) ? a.uses : [];
    return "uses=" + encodeURIComponent(uses.join(",")) +
      "&priority=" + encodeURIComponent(a.priority || "balanced") +
      "&power=" + encodeURIComponent(a.power || "auto");
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  var fmt = { gb: gb, gbN: gbN, tps: tps, secs: secs, ctxK: ctxK, time12: time12,
              rangeText: rangeText, rint: rint, basisText: basisText, speedLine: speedLine,
              wakeLine: wakeLine, memLine: memLine, planHeading: planHeading,
              planKey: planKey, fitLine: fitLine, verdictText: verdictText,
              rankQuery: rankQuery };

  // ==========================================================================
  // state
  // ==========================================================================
  var ST = {
    state: null,        // the last /api/advisor/state answer (ok)
    stateErr: "",       // a failure with nothing to show yet
    recheckErr: "",
    loading: null,      // the in-flight state Promise
    fetchedAt: 0,
    answers: null,      // what the questionnaire shows NOW {uses, priority, power}
    saved: null,        // the stored answers, same shape
    everSaved: false,   // settings.json has an `advisor` with a saved_at
    ackStored: false,   // settings.json advisor.uncensored_ack === true
    ackBusy: false,
    ackErr: "",
    pendingUnc: false,  // the uncensored chip was ticked; the acknowledgement is showing
    result: null,       // the ranking on screen
    rankErr: "",
    rankSeq: 0,
    rankTimer: null,
    status: "",
    sel: null,          // the plan key the owner picked; null = the Recommended plan
    userPicked: false,
    lastPickKey: "",
    wireSent: { key: "", at: 0 },
    ctx: null,          // the onboarding ctx while the step is on screen
    openData: null,     // the ctx.data of the sheet open this state was read for
    msg: {},            // card: per-plan status line {key: [text, bad]}
    busy: "",           // card: the action in flight
    saveBusy: false,
    saveErr: ""
  };

  function ackText() {
    var q = ST.state && ST.state.questions;
    return (q && typeof q.uncensored_ack_text === "string") ? q.uncensored_ack_text : "";
  }
  // The acknowledgement copy for a caller that is about to ASK (the model
  // menu's confirm on an uncensored row): the text if this page already has it,
  // otherwise one read-only GET /api/advisor/state, bounded to 3 s, never
  // rejecting. "" = the server did not say (the caller has its own sentence).
  // The copy carries the part that matters most — the approval gate is a path
  // denylist, not a sandbox — so a confirm must not quote a shorter one just
  // because nobody opened Settings yet.
  function ensureAckText() {
    var t = ackText();
    if (t || ST.state) return Promise.resolve(t);
    return new Promise(function (resolve) {
      var done = false, timer = null;
      function fin() {
        if (done) return;
        done = true;
        if (timer) { try { clearTimeout(timer); } catch (e) {} }
        resolve(ackText());
      }
      try { timer = setTimeout(fin, 3000); } catch (e) { timer = null; }
      try { loadState(false).then(fin, fin); } catch (e) { fin(); }
    });
  }
  function hw() { return (ST.state && ST.state.hw) || {}; }
  function limits() { return (ST.state && ST.state.limits) || (ST.result && ST.result.limits) || {}; }
  function laptop() {
    var q = ST.state && ST.state.questions;
    if (q && typeof q.power_asked === "boolean") return q.power_asked;
    var f = hw().form;
    return f === "laptop" || f === "laptop_fanless";
  }
  function snapAnswers(a) {
    a = a || {};
    var uses = Array.isArray(a.uses) ? a.uses.filter(function (u) { return typeof u === "string"; }) : [];
    return { uses: uses.length ? uses.slice() : ["chat", "agent"],
             priority: typeof a.priority === "string" ? a.priority : "balanced",
             power: (a.power === "ac" || a.power === "battery") ? a.power : null };
  }
  function sameAnswers(a, b) {
    if (!a || !b) return false;
    return a.priority === b.priority && a.power === b.power &&
      a.uses.slice().sort().join(",") === b.uses.slice().sort().join(",");
  }
  // the body POST /api/advisor/answers accepts: `uncensored` only with the
  // STORED acknowledgement (the server 400s it otherwise)
  function answersBody() {
    var a = snapAnswers(ST.answers);
    if (!ST.ackStored) a.uses = a.uses.filter(function (u) { return u !== "uncensored"; });
    if (!a.uses.filter(function (u) { return u !== "uncensored"; }).length) a.uses = ["chat", "agent"];
    return a;
  }
  function allPlans() {
    var r = ST.result || {}, out = [];
    if (r.recommended) out.push(r.recommended);
    (r.alternatives || []).forEach(function (p) { if (p && p.primary) out.push(p); });
    return out;
  }
  function findPlan(key) {
    var ps = allPlans();
    for (var i = 0; i < ps.length; i++) if (planKey(ps[i]) === key) return ps[i];
    return null;
  }
  function selectedPlan() {
    var r = ST.result || {};
    if (ST.sel) { var p = findPlan(ST.sel); if (p) return p; }
    return r.recommended || null;
  }
  function currentIds() {
    var c = (ST.state && ST.state.current) || {};
    return { active: c.active || null, bg: c.background || null };
  }

  // ---- fetch helpers --------------------------------------------------------
  async function jreq(url, body) {
    var init;
    if (body !== undefined) {
      init = { method: "POST", headers: { "Content-Type": "application/json" },
               body: JSON.stringify(body) };
    }
    var r;
    try { r = await fetch(url, init); }
    catch (e) { return { ok: false, status: 0, data: null, net: true }; }
    var d = null;
    try { d = await r.json(); } catch (e) { d = null; }
    return { ok: !!r.ok && !(d && d.ok === false), status: r.status || 0, data: d };
  }
  function errOf(r, what) {
    if (r && r.data && Array.isArray(r.data.errors) && r.data.errors.length) return r.data.errors.join(" · ");
    if (r && r.data && typeof r.data.error === "string" && r.data.error) return r.data.error;
    if (r && r.net) return what + ": the dashboard did not answer";
    return what + (r && r.status ? " (HTTP " + r.status + ")" : "");
  }

  // ---- loading the state (the only request a mount makes) -------------------
  function loadState(bust) {
    if (ST.loading) return ST.loading;
    ST.loading = (async function () {
      var r = await jreq("/api/advisor/state" + (bust ? "?bust=1" : ""));
      ST.loading = null;
      if (r.ok && r.data && r.data.result && typeof r.data.result === "object") {
        applyState(r.data);
        return true;
      }
      var why = (r.status === 404) ? "this dashboard does not have the model advisor yet"
        : errOf(r, "the advisor did not answer");
      if (ST.state) ST.recheckErr = "Could not re-check: " + why + ".";
      else ST.stateErr = why;
      paintRoots("all");
      return false;
    })();
    return ST.loading;
  }

  function applyState(d) {
    var first = !ST.state;
    ST.state = d;
    ST.stateErr = ""; ST.recheckErr = "";
    ST.fetchedAt = Date.now();
    var a = d.answers || {};
    ST.ackStored = a.uncensored_ack === true;
    ST.saved = snapAnswers(a);
    if (!ST.ackStored) ST.saved.uses = ST.saved.uses.filter(function (u) { return u !== "uncensored"; });
    ST.everSaved = a.saved_at != null;
    if (first || !ST.answers) ST.answers = snapAnswers(ST.saved);
    if (sameAnswers(answersBody(), ST.saved)) {
      ST.result = d.result;
      reconcilePick(true);
      paintRoots("all");
    } else {
      // the owner has unsaved answers on screen: the state ranked the STORED
      // ones, so keep the questionnaire and re-rank what it shows (Re-check)
      paintRoots("all");
      doRank(false);
    }
  }

  // ---- the onboarding pick (shared contract 2) ------------------------------
  function pickOf(p) {
    var o = { primary: p.primary.id, background: p.background ? p.background.id : null,
              uncensored: !!p.uncensored };
    if (!p.download_unknown && num(p.download_gb)) o.download_gb = p.download_gb;
    return o;
  }
  // the onboarding sheet's step body is in the document (a closed sheet's
  // ctx must not be handed picks by a Settings re-rank hours later)
  function stepLive() {
    var rs = roots();
    for (var i = 0; i < rs.length; i++) if (rs[i].getAttribute("data-adv-root") === "step") return true;
    return false;
  }
  function sendPick(p) {
    var c = ST.ctx;
    if (!c || typeof c.setPick !== "function" || !p || !p.primary) return;
    if (!stepLive()) { ST.ctx = null; return; }
    if (p.uncensored && !ST.ackStored) return;            // never without the STORED ack
    try { c.setPick(pickOf(p)); ST.lastPickKey = planKey(p); }
    catch (e) { warnOnce("setpick", "setPick threw: " + e); }
  }
  // After a new result: a picked plan that is gone falls back to the
  // Recommended one; while the Recommended plan is the pick, a new one is
  // handed to the sheet (first arrival, or answers that changed it).
  function reconcilePick(arrival) {
    var r = ST.result || {};
    // the owner's picked alternative is now THE Recommended plan (answers
    // changed): one selection, shown on the Recommended card, and the sheet
    // already holds exactly that plan, so nothing is re-sent
    if (ST.sel && r.recommended && planKey(r.recommended) === ST.sel) {
      ST.sel = null; ST.userPicked = false;
    }
    if (ST.sel && (!findPlan(ST.sel) || (findPlan(ST.sel).uncensored && !ST.ackStored))) {
      ST.sel = null; ST.userPicked = false;
      if (r.recommended) sendPick(r.recommended);
      return;
    }
    if (ST.sel || !r.recommended) return;
    if (arrival && ST.userPicked) return;
    if (planKey(r.recommended) !== ST.lastPickKey) sendPick(r.recommended);
  }

  // ---- re-ranking (debounced, pure) ----------------------------------------
  function scheduleRank() {
    if (ST.rankTimer) { try { clearTimeout(ST.rankTimer); } catch (e) {} }
    ST.rankTimer = setTimeout(function () { ST.rankTimer = null; doRank(true); }, DEBOUNCE_MS);
  }
  async function doRank(user) {
    var seq = ++ST.rankSeq;
    var r = await jreq("/api/advisor/rank?" + rankQuery(answersBody()));
    if (seq !== ST.rankSeq) return;                       // a newer answer is on its way
    if (r.ok && r.data && r.data.result && r.data.result.ok !== false) {
      ST.result = r.data.result;
      ST.rankErr = "";
      ST.status = statusText(r.data.result);
      reconcilePick(false);
    } else {
      ST.rankErr = r.status === 400 ? errOf(r, "Those answers were not accepted")
        : "Could not re-rank: " + errOf(r, "the advisor did not answer") + ".";
    }
    paintRoots("status");
    paintRoots("res", user);
    paintRoots("save");
  }
  function statusText(res) {
    var a = (res && res.answers) || {};
    var labels = {};
    usesList().forEach(function (u) { labels[u[0]] = u[1]; });
    var uses = (a.uses_ranked || a.uses || []).map(function (u) { return labels[u] || u; });
    return "Ranked for " + (uses.join(", ") || "everyday chat") + ".";
  }

  // ==========================================================================
  // markup
  // ==========================================================================
  function usesList() {
    var q = ST.state && ST.state.questions;
    if (q && Array.isArray(q.uses) && q.uses.length) {
      return q.uses.filter(function (u) { return u && typeof u.key === "string"; })
        .map(function (u) { return [u.key, String(u.label || u.key)]; });
    }
    return DEFAULT_USES;
  }

  // a value breaks only BETWEEN its phrases ("On battery, 23%," / "Low Power
  // Mode"), never inside one ("40-/core GPU")
  function phrases(v) {
    return String(v == null ? "" : v).split(/(, | · )/).map(function (p, i) {
      return (i % 2) ? E(p) : '<span class="adv-ph">' + E(p) + "</span>";
    }).join("");
  }
  function statRow(k, v, extra, title) {
    return '<li><span class="adv-k">' + E(k) + '</span><span class="adv-v"' +
      (title ? ' title="' + E(title) + '"' : "") + ">" + phrases(v) +
      (extra ? ' <span class="adv-assumed">' + E(extra) + "</span>" : "") + "</span></li>";
  }
  function detectedHTML(ctx) {
    var h = hw(), rows = [];
    if (ST.state) {
      var c = h.chip || {};
      if (c.brand || h.machine_name) {
        rows.push(statRow("Chip", (c.brand || "Unknown chip") +
          (num(c.gpu_cores) ? " · " + c.gpu_cores + "-core GPU" : ""),
          c.cores_source === "assumed" ? "(assumed)" : "",
          c.cores_source === "assumed" ? "GPU core count not detected: the lower " + (c.row || "") +
            " bin is assumed, so estimates err on the slow side." : (c.row ? "chip table row " + c.row : "")));
      }
      if (num(h.ram_gb)) rows.push(statRow("Memory", Math.round(h.ram_gb) + NB + "GB"));
      var gbud = h.gpu_budget || {};
      if (num(gbud.gb)) {
        rows.push(statRow("GPU budget", gb(gbud.gb), gbud.source === "assumed" ? "(assumed)" : "",
          gbud.note || ""));
      }
      if (num(c.bandwidth_gbs)) rows.push(statRow("Bandwidth", Math.round(c.bandwidth_gbs) + NB + "GB/s", "",
        "Apple's published memory bandwidth for this chip"));
      if (num(h.disk_free_gb)) rows.push(statRow("Free disk", gb(h.disk_free_gb)));
      var pw = h.power || {};
      if (laptop() && (pw.source === "ac" || pw.source === "battery")) {
        rows.push(statRow("Power", (pw.source === "battery" ? "On battery" : "Plugged in") +
          (num(pw.pct) ? ", " + pw.pct + "%" : "") + (pw.low_power ? ", Low Power Mode" : "")));
      }
      var form = h.machine_name || ({ laptop: "Laptop", laptop_fanless: "Laptop without a fan",
                                      desktop: "Desktop" })[h.form] || "";
      if (form) rows.push(statRow("Form", form));
      if (h.macos) rows.push(statRow("macOS", h.macos));
    } else {
      var si = (ctx && ctx.data && ctx.data.sysinfo) || {};
      if (si.chip) rows.push(statRow("Chip", si.chip));
      if (num(si.ram_gb)) rows.push(statRow("Memory", Math.round(si.ram_gb) + NB + "GB"));
      if (num(si.disk_free_gb)) rows.push(statRow("Free disk", gb(si.disk_free_gb)));
      if (si.macos) rows.push(statRow("macOS", si.macos));
      if (typeof si.laptop === "boolean") rows.push(statRow("Form", si.laptop ? "Laptop" : "Desktop"));
    }
    if (!rows.length) return "";
    return '<div class="adv-det"><h3 class="adv-h">' + G.chip + "Detected on this Mac</h3>" +
      '<ul class="adv-stats">' + rows.join("") + "</ul></div>";
  }

  function segHTML(kind, name, opts, cur, label, uid) {
    var id = "adv-" + kind + "-" + uid;
    return '<div class="adv-qh" id="' + id + '">' + E(label) + "</div>" +
      '<div class="adv-seg" role="radiogroup" aria-labelledby="' + id + '">' +
      opts.map(function (o) {
        return '<label class="adv-seg-o"><input type="radio" name="' + E(name) + '" value="' +
          E(o[0]) + '" data-' + kind + '="' + E(o[0]) + '"' + (o[0] === cur ? " checked" : "") +
          "><span>" + E(o[1]) + "</span></label>";
      }).join("") + "</div>";
  }

  function questionsHTML(uid) {
    if (!ST.state) return "";
    var a = ST.answers || snapAnswers(null);
    var gid = "adv-q1-" + uid;
    var chips = usesList().map(function (u) {
      var on = a.uses.indexOf(u[0]) >= 0 && (u[0] !== "uncensored" || ST.ackStored);
      return '<label class="adv-chip' + (u[0] === "uncensored" ? " adv-chip-unc" : "") + '">' +
        '<input type="checkbox" data-use="' + E(u[0]) + '"' + (on ? " checked" : "") +
        (u[0] === "uncensored" ? ' aria-controls="adv-ack-' + uid + '"' : "") + ">" +
        G.tick + "<span>" + E(u[1]) + "</span></label>";
    }).join("");
    var pw = a.power || ((hw().power || {}).source === "battery" ? "battery" : "ac");
    return '<div class="adv-q">' +
      '<div class="adv-qh" id="' + gid + '">What will you use it for?</div>' +
      '<div class="adv-chips" role="group" aria-labelledby="' + gid + '">' + chips + "</div>" +
      '<div class="adv-ack-slot" data-adv-part="ack" id="adv-ack-' + uid + '">' + ackHTML() + "</div>" +
      segHTML("pri", "adv-pri-" + uid, PRIORITIES, a.priority, "What matters more?", uid) +
      (laptop() ? segHTML("pow", "adv-pow-" + uid, POWERS, pw, "Where do you use it?", uid) : "") +
      "</div>";
  }

  function ackHTML() {
    var on = ST.answers && ST.answers.uses.indexOf("uncensored") >= 0 && ST.ackStored;
    if (!ST.pendingUnc && !on) return "";
    var t = ackText() || "Uncensored models have had their refusal behaviour removed.";
    return '<div class="adv-ack" role="group" aria-label="Uncensored models">' +
      '<p class="adv-ack-t">' + E(t) + "</p>" +
      '<div class="adv-ack-row"><label class="adv-check"><input type="checkbox" data-ack="1"' +
      (ST.ackStored ? " checked" : "") + (ST.ackBusy ? " disabled" : "") + ">" +
      "<span>Show uncensored options</span></label>" +
      (ST.pendingUnc && !ST.ackStored
        ? '<button type="button" class="adv-b" data-adv-act="unc-cancel">Cancel</button>' : "") +
      "</div>" +
      (ST.ackErr ? '<p class="adv-err" role="alert">' + E(ST.ackErr) + "</p>" : "") +
      "</div>";
  }

  function tag(cls, text, title) {
    return '<span class="adv-tag ' + cls + '"' + (title ? ' title="' + E(title) + '"' : "") + ">" +
      E(text) + "</span>";
  }
  function uncChip() {
    return '<span class="adv-unc" title="' + E("No refusals: the approval gate is the only brake") + '">' +
      G.unlock + "Uncensored</span>";
  }
  function ramOf(id, ctx) {
    var fits = ctx && ctx.data && ctx.data.recommendation && ctx.data.recommendation.fits;
    if (!Array.isArray(fits)) return null;
    for (var i = 0; i < fits.length; i++) if (fits[i] && fits[i].id === id) return fits[i].ram;
    return null;
  }

  function laneHTML(role, lane, est, ctx) {
    if (!lane) {
      return '<div class="adv-lane"><span class="adv-lane-r">' + E(role) + "</span>" +
        '<span class="adv-lane-s">' + E("none: briefings and news run on the assistant") + "</span></div>";
    }
    var bits = [];
    if (est) {
      if (num(est.resident_gb)) bits.push("~" + gb(est.resident_gb) + " with its cache");
      var sp = speedLine(est); if (sp) bits.push(sp);
      var wk = wakeLine(est); if (wk) bits.push("first reply after wake " + wk);
    }
    bits.push(lane.downloaded ? "on this Mac" : (num(lane.download_gb) ? "~" + gb(lane.download_gb) + " download" : "download size unknown"));
    var fl = fitLine(lane.fit, ramOf(lane.id, ctx), hw().ram_gb);
    var title = est && est.formula ? [est.formula.resident, est.formula.decode, est.formula.prefill]
      .filter(Boolean).join("\n") : "";
    return '<div class="adv-lane"><span class="adv-lane-r">' + E(role) + "</span>" +
      '<span class="adv-lane-n">' + E(laneLabel(lane)) + "</span>" +
      (lane.uncensored ? uncChip() : "") +
      (lane.verified ? tag("ok", "verified for tools", lane.verified_detail || "")
        : tag("warn", "not yet verified", lane.verified_detail || "not drilled")) +
      '<span class="adv-lane-s"' + (title ? ' title="' + E(title) + '"' : "") + ">" +
      E(bits.join(" · ")) + "</span>" +
      (fl ? '<span class="adv-fit' + (lane.fit === "no" ? " bad" : lane.fit === "tight" ? " warn" : "") +
        '">' + E(fl) + "</span>" : "") + "</div>";
  }

  function totalHTML(p) {
    var free = hw().disk_free_gb;
    if (p.downloaded) return '<p class="adv-total ok">' + E("Everything in this plan is already on this Mac.") + "</p>";
    var need = p.download_unknown && !p.download_gb ? "a download of unknown size"
      : "~" + gb(p.download_gb) + " to download" + (p.download_unknown ? " (plus one of unknown size)" : "");
    var h = '<p class="adv-total">' + E(need + (num(free) ? " · " + gb(free) + " free" : "")) + "</p>";
    if (p.disk_ok === false) {
      h += '<p class="adv-bad">' + E("Not enough free disk: a download must leave 5 GB to spare.") + "</p>";
    }
    return h;
  }

  function badgesHTML(p) {
    var b = Array.isArray(p.badges) ? p.badges : [], out = "";
    if (b.indexOf("tight") >= 0) out += tag("warn", "tight", "Loads, but expect swapping under heavy use");
    if (b.indexOf("slow") >= 0) out += tag("warn", "slow here", "Under 8 tok/s in a typical turn");
    if (p.uncensored) out += uncChip();
    return out;
  }

  function pickHTML(p, key, isRec, uid) {
    var checked = isRec ? !ST.sel : ST.sel === key;
    var locked = p.uncensored && !ST.ackStored;
    var lid = "adv-lock-" + uid + "-" + key.replace(/[^A-Za-z0-9_-]/g, "_");
    return '<label class="adv-pick' + (locked ? " is-locked" : "") + '">' +
      '<input type="radio" name="adv-pick-' + uid + '" value="' + E(key) + '" data-pick="' + E(key) + '"' +
      (checked ? " checked" : "") + (locked ? ' disabled aria-describedby="' + E(lid) + '"' : "") + ">" +
      "<span>" + (isRec ? "Use this plan" : "Use this instead") + "</span></label>" +
      (locked ? '<span class="adv-off" id="' + E(lid) + '">' +
        E("Acknowledge uncensored models first") + "</span>" : "");
  }

  // ---- card: the per-plan buttons (§8.2) ------------------------------------
  function btn(act, key, label, off, why, cls) {
    var wid = off && why ? "adv-off-" + act + "-" + key.replace(/[^A-Za-z0-9_-]/g, "_") : "";
    return '<button type="button" class="adv-b' + (cls ? " " + cls : "") + '" data-plan-act="' + E(act) +
      '" data-plan="' + E(key) + '"' + (off ? " disabled" : "") +
      (wid ? ' aria-describedby="' + E(wid) + '"' : "") + ">" + E(label) + "</button>" +
      (wid ? '<span class="adv-off" id="' + E(wid) + '">' + E(why) + "</span>" : "");
  }
  function actsHTML(p, key) {
    var cur = currentIds(), free = hw().disk_free_gb, out = [];
    out.push(btn("add", key, "Add to model menu"));
    if (!p.downloaded) {
      var size = (p.download_unknown && !p.download_gb) ? "" : "~" + gb(p.download_gb);
      var label = "Download" + (size || num(free) ? " (" + [size, num(free) ? gb(free) + " free" : ""]
        .filter(Boolean).join(" · ") + ")" : "");
      out.push(btn("download", key, label, p.disk_ok === false,
        p.disk_ok === false ? "not enough free disk (a download must leave 5 GB to spare)" : ""));
    }
    if (p.background && p.background.id && p.background.id !== cur.bg) {
      out.push(btn("bg", key, "Use " + laneLabel(p.background) + " as background lane"));
    }
    if (p.primary.id !== cur.active) {
      out.push(btn("switch", key, "Make it the assistant", !p.primary.downloaded,
        p.primary.downloaded ? "" : "download it first"));
    }
    var m = ST.msg[key];
    return '<div class="adv-acts">' + out.join("") + "</div>" +
      '<p class="adv-msg' + (m && m[1] ? " bad" : "") + '" role="status" data-msg="' + E(key) + '">' +
      E(m ? m[0] : "") + "</p>";
  }

  function recHTML(p, mode, uid, ctx) {
    var key = planKey(p);
    var notes = Array.isArray(p.notes) ? p.notes : [];
    return '<section class="adv-plan adv-rec' + (!ST.sel ? " is-picked" : "") + '" data-plan="' + E(key) + '">' +
      '<div class="adv-plan-top"><span class="adv-eyebrow">' + G.check + E(p.label || "Recommended") + "</span>" +
      badgesHTML(p) + (mode === "step" ? pickHTML(p, key, true, uid) : "") + "</div>" +
      '<h3 class="adv-plan-h">' + E(planHeading(p)) + "</h3>" +
      (p.why ? '<p class="adv-why">' + E(p.why) + "</p>" : "") +
      laneHTML("Assistant", p.primary, (p.estimates || {}).primary, ctx) +
      laneHTML("Background", p.background, (p.estimates || {}).background, ctx) +
      totalHTML(p) +
      (notes.length ? '<ul class="adv-notes">' + notes.map(function (n) { return "<li>" + E(n) + "</li>"; }).join("") + "</ul>" : "") +
      (mode === "card" ? actsHTML(p, key) : "") +
      "</section>";
  }

  function altHTML(p, mode, uid) {
    var key = planKey(p), s = p.summary || {};
    var nums = [];
    if (num(p.total_gb)) nums.push("~" + gb(p.total_gb) + (num(p.limit_gb) ? " of a " + gb(p.limit_gb) + " budget" : ""));
    if (num(s.decode_turn_tps)) nums.push("~" + tps(s.decode_turn_tps) + " tok/s in a " + ctxK(s.turn_ctx) + " turn");
    if (num(s.prefill_s)) nums.push("first reply after wake ~" + secs(s.prefill_s));
    nums.push(p.downloaded ? "on this Mac" : (num(p.download_gb) && p.download_gb > 0 ? "~" + gb(p.download_gb) + " to download" : "download size unknown"));
    return '<li class="adv-alt' + (ST.sel === key ? " is-picked" : "") + '" data-plan="' + E(key) + '">' +
      '<div class="adv-alt-top"><span class="adv-alt-l">' + E(p.label || "Another option") + "</span>" +
      badgesHTML(p) + (p.verified ? "" : tag("warn", "not yet verified",
        (p.primary && p.primary.verified_detail) || "not drilled")) + "</div>" +
      '<div class="adv-alt-n">' + E(planHeading(p)) + "</div>" +
      (p.why ? '<p class="adv-why">' + E(p.why) + "</p>" : "") +
      '<p class="adv-alt-s">' + E(nums.join(" · ")) + "</p>" +
      (mode === "step" ? '<div class="adv-alt-pick">' + pickHTML(p, key, false, uid) + "</div>" : "") +
      (mode === "card" ? actsHTML(p, key) : "") +
      "</li>";
  }

  function wontHTML(list) {
    if (!Array.isArray(list) || !list.length) return "";
    return '<details class="adv-wont"><summary>' + G.chev +
      E(plural(list.length, "model won't work well here", "models won't work well here")) + "</summary>" +
      '<ul class="adv-wont-l">' + list.map(function (w) {
        return "<li><b>" + E(w.label || w.id) + "</b>" + (w.uncensored ? uncChip() : "") +
          "<span>" + E(w.reason || "") + "</span></li>";
      }).join("") + "</ul></details>";
  }

  function howHTML(p) {
    var f = (ST.state && ST.state.formulas) || {}, lim = limits(), rows = [];
    function row(k, code, extra) {
      if (!code && !extra) return;
      rows.push("<dt>" + E(k) + "</dt><dd>" + (code ? "<code>" + E(code) + "</code>" : "") +
        (extra ? "<span>" + E(extra) + "</span>" : "") + "</dd>");
    }
    row("Memory budget", lim.formula || f.limit, lim.note || "");
    if (p) {
      [["primary", p.primary], ["background", p.background]].forEach(function (pair) {
        var e = (p.estimates || {})[pair[0]], lane = pair[1];
        if (!e || !lane) return;
        var fo = e.formula || {}, b = e.basis || {}, r = e.range || {}, nm = laneLabel(lane);
        row(nm + " · speed in a " + ctxK(e.turn_ctx) + " turn", fo.decode,
          "basis: " + basisText(b.decode) + (rangeText(r.decode, "tok/s", 1) ? " · range " + rangeText(r.decode, "tok/s", 1) : ""));
        row(nm + " · short replies", fo.decode_short,
          "basis: " + basisText(b.decode_short) + (rangeText(r.decode_short, "tok/s", 1) ? " · range " + rangeText(r.decode_short, "tok/s", 1) : ""));
        row(nm + " · first reply after wake", fo.prefill,
          "basis: " + basisText(b.prefill) + (rangeText(r.prefill, "s", 1) ? " · range " + rangeText(r.prefill, "s", 1) : ""));
        row(nm + " · memory with its cache", fo.resident,
          "basis: " + basisText(b.resident) + (rangeText(r.resident, "GB", 1) ? " · range " + rangeText(r.resident, "GB", 1) : ""));
      });
    }
    row("Ranking", f.score, [f.recommended, f.relaxation].filter(Boolean).join(" "));
    return '<details class="adv-how"><summary>' + G.chev + "How we estimate</summary>" +
      '<div class="adv-how-b"><p>' + E("Every figure here is an estimate (marked ~) unless it says measured here. " +
        (f.range ? "Ranges: " + f.range + "." : "")) + "</p>" +
      (rows.length ? "<dl>" + rows.join("") + "</dl>" : "") + "</div></details>";
  }

  function resultHTML(mode, uid, ctx) {
    var r = ST.result;
    if (!r) return "";
    var h = "";
    if (ST.rankErr) h += '<p class="adv-err" role="alert">' + E(ST.rankErr) + "</p>";
    if (r.recommended && r.recommended.primary) h += recHTML(r.recommended, mode, uid, ctx);
    else {
      h += '<p class="adv-none">' + E("No plan in the catalog fits this Mac" +
        ((r.notes || [])[0] ? ": " + r.notes[0] : ".")) + "</p>";
    }
    var alts = (r.alternatives || []).filter(function (p) { return p && p.primary; });
    if (alts.length) {
      h += '<div class="adv-h2">' + E(mode === "step" ? "Or use one of these instead" : "Alternatives") + "</div>" +
        '<ul class="adv-alts">' + alts.map(function (p) { return altHTML(p, mode, uid); }).join("") + "</ul>";
    }
    h += wontHTML(r.wont);
    var planNotes = (r.recommended && r.recommended.notes) || [];
    var notes = (r.notes || []).filter(function (n) { return n && planNotes.indexOf(n) < 0; });
    if (notes.length) {
      h += '<ul class="adv-notes adv-gnotes">' + notes.map(function (n) { return "<li>" + E(n) + "</li>"; }).join("") + "</ul>";
    }
    h += howHTML(r.recommended);
    return h;
  }

  function nowHTML() {
    var cur = (ST.result && ST.result.current) || (ST.state && ST.state.current && ST.state.current.plan);
    var last = "";
    var hwt = hw(), t = Math.max(num(hwt.dynamic_at) ? hwt.dynamic_at : 0, num(hwt.static_at) ? hwt.static_at : 0);
    last = time12(t > 0 ? t : (ST.fetchedAt || Date.now()));
    var h = '<div class="adv-now"><div class="adv-plan-top"><span class="adv-eyebrow">What you run now</span>';
    if (cur && cur.primary) {
      h += '<span class="adv-verdict v-' + E(cur.verdict || "none") + '">' + E(verdictText(cur.verdict)) + "</span>" +
        (cur.uncensored ? uncChip() : "") + "</div>" +
        '<p class="adv-now-h">' + E(planHeading(cur)) + "</p>" +
        (cur.why ? '<p class="adv-why">' + E(cur.why) + "</p>" : "");
    } else {
      h += "</div>" + '<p class="adv-why">' + E("No model is active yet.") + "</p>";
    }
    h += '<p class="adv-last">' + E("Last analysed " + last) + ' <span aria-hidden="true">·</span> ' +
      '<button type="button" class="adv-link" data-adv-act="recheck"' + (ST.loading ? " disabled" : "") + ">" +
      (ST.loading ? "Checking…" : "Re-check") + "</button></p>" +
      (ST.recheckErr ? '<p class="adv-err" role="alert">' + E(ST.recheckErr) + "</p>" : "") + "</div>";
    return h;
  }

  function saveBarHTML() {
    if (!ST.state || !ST.answers) return "";
    var dirty = !ST.everSaved || !sameAnswers(answersBody(), ST.saved);
    if (!dirty && !ST.saveErr) return "";
    return '<div class="adv-savebar">' +
      (dirty ? '<button type="button" class="adv-b go" data-adv-act="save-answers"' + (ST.saveBusy ? " disabled" : "") + ">" +
        (ST.saveBusy ? "Saving…" : "Use these answers") + "</button>" +
        '<span class="adv-off">' + E("The model menu's advice follows the saved answers.") + "</span>" : "") +
      (ST.saveErr ? '<p class="adv-err" role="alert">' + E(ST.saveErr) + "</p>" : "") + "</div>";
  }

  // the degraded body when the advisor cannot answer: the memory-based
  // recommendation the onboarding state already carries, said plainly
  function fallbackHTML(ctx) {
    var rec = (ctx && ctx.data && ctx.data.recommendation) || null;
    var h = '<p class="adv-err" role="status">' + E("The model advisor is not available (" + ST.stateErr +
      ")" + (rec ? ", so this is the memory-based recommendation." : ".")) + "</p>";
    if (rec && rec.primary) {
      h += '<section class="adv-plan adv-rec is-picked"><h3 class="adv-plan-h">' +
        E((rec.primary_label || rec.primary) + " as the assistant" +
          (rec.background ? ", " + (rec.background_label || rec.background) + " for background work"
            : ", with no background lane")) + "</h3>" +
        (rec.why ? '<p class="adv-why">' + E(rec.why) + "</p>" : "") + "</section>";
    }
    return h;
  }

  function innerHTML(mode, uid, ctx) {
    if (ST.stateErr && !ST.state) {
      return detectedHTML(ctx) + fallbackHTML(ctx) +
        (mode === "card" ? '<p><button type="button" class="adv-b" data-adv-act="recheck">Try again</button></p>' : "");
    }
    if (!ST.state) {
      var compat = ctx && ctx.data && ctx.data.recommendation && ctx.data.recommendation.advisor;
      if (!ST.result && compat && compat.result && compat.result.recommended) ST.result = compat.result;
      return '<div class="adv-top">' + detectedHTML(ctx) +
        '<p class="adv-wait" role="status">Reading this Mac…</p></div>' +
        '<div class="adv-res" data-adv-part="res">' + resultHTML(mode, uid, ctx) + "</div>";
    }
    var h = hw();
    var unsupported = h.supported === false
      ? '<p class="adv-bad">' + E(h.unsupported_reason || "Local models need Apple silicon.") + "</p>" : "";
    return (mode === "card" ? nowHTML() : "") + unsupported +
      '<div class="adv-top">' + detectedHTML(ctx) + questionsHTML(uid) + "</div>" +
      (mode === "card" ? '<div data-adv-part="save">' + saveBarHTML() + "</div>" : "") +
      // outside .adv-res on purpose: a live region that is itself replaced is
      // not announced, so the re-rank status keeps its own stable node
      '<p class="adv-status" role="status" data-adv-part="status">' + E(ST.status) + "</p>" +
      '<div class="adv-res" data-adv-part="res">' + resultHTML(mode, uid, ctx) + "</div>";
  }

  function rootHTML(mode, ctx) {
    var uid = mode === "card" ? "c" : "s";
    return '<div class="adv" data-adv-root="' + mode + '" data-uid="' + uid + '">' +
      innerHTML(mode, uid, ctx) + "</div>";
  }

  // ---- painting OUR nodes only ----------------------------------------------
  function focusKey(root) {
    var d = D(), a = d && d.activeElement;
    if (!a || !root.contains || !root.contains(a) || !a.getAttribute) return null;
    var keys = ["data-use", "data-pri", "data-pow", "data-ack", "data-pick", "data-adv-act"];
    for (var i = 0; i < keys.length; i++) {
      var v = a.getAttribute(keys[i]);
      if (v != null) return { k: keys[i], v: v };
    }
    if (a.getAttribute("data-plan-act") != null) {
      return { k: "data-plan-act", v: a.getAttribute("data-plan-act"), plan: a.getAttribute("data-plan") };
    }
    return null;
  }
  function refocus(root, fk) {
    if (!fk) return;
    try {
      var list = root.querySelectorAll("[" + fk.k + "]");
      for (var i = 0; i < list.length; i++) {
        if (list[i].getAttribute(fk.k) === fk.v &&
            (fk.plan == null || list[i].getAttribute("data-plan") === fk.plan)) {
          if (!list[i].disabled) list[i].focus();
          return;
        }
      }
    } catch (e) {}
  }
  function roots() {
    var d = D();
    if (!d || typeof d.querySelectorAll !== "function") return [];
    try { return Array.prototype.slice.call(d.querySelectorAll("[data-adv-root]")); }
    catch (e) { return []; }
  }
  // part: "all" | "res" | "ack" | "save"; animate: the owner caused it
  function paintRoots(part, animate) {
    ensureCSS();
    roots().forEach(function (root) {
      var mode = root.getAttribute("data-adv-root") === "card" ? "card" : "step";
      var uid = root.getAttribute("data-uid") || (mode === "card" ? "c" : "s");
      var ctx = mode === "step" ? ST.ctx : null;
      var fk = focusKey(root);
      try {
        if (part === "all") {
          root.innerHTML = innerHTML(mode, uid, ctx);
        } else {
          var node = root.querySelector('[data-adv-part="' + part + '"]');
          if (!node) return;
          if (part === "status") { node.textContent = ST.status || ""; return; }
          node.innerHTML = part === "res" ? resultHTML(mode, uid, ctx)
            : part === "ack" ? ackHTML() : saveBarHTML();
          if (animate && node.firstElementChild !== undefined) {
            try {
              if (part === "res") A().enter(node, "fade");
              else if (part === "ack" && node.firstElementChild) A().enter(node.firstElementChild, "rise");
            } catch (e) {}
          }
        }
      } catch (e) { warnOnce("paint", "could not render: " + e); return; }
      refocus(root, fk);
    });
  }

  // ---- interactions (one delegated listener per root) -----------------------
  function onUse(input) {
    var key = input.getAttribute("data-use"), on = !!input.checked;
    var a = ST.answers || (ST.answers = snapAnswers(null));
    if (key === "uncensored") {
      if (on && !ST.ackStored) {
        // the chip does not turn on until the acknowledgement is STORED
        input.checked = false;
        ST.pendingUnc = true; ST.ackErr = "";
        paintRoots("ack", true);
        var r = input.closest ? input.closest("[data-adv-root]") : null;
        var ack = r && r.querySelector('input[data-ack]');
        if (ack) { try { ack.focus(); } catch (e) {} }
        return;
      }
      ST.pendingUnc = false;
    }
    var uses = a.uses.filter(function (u) { return u !== key; });
    if (on) uses.push(key);
    if (!uses.filter(function (u) { return u !== "uncensored"; }).length) {
      // at least one real use: refuse to clear the last one
      input.checked = true;
      return;
    }
    a.uses = canonUses(uses);
    if (key === "uncensored") paintRoots("ack");
    syncChips();
    paintRoots("save");
    scheduleRank();
  }
  function canonUses(xs) {
    var order = usesList().map(function (u) { return u[0]; });
    return order.filter(function (u) { return xs.indexOf(u) >= 0; });
  }
  // every root's chip boxes follow the answers (the step and the card can
  // both be in the DOM; a native toggle only moves the one clicked)
  function syncChips() {
    roots().forEach(function (root) {
      try {
        Array.prototype.slice.call(root.querySelectorAll("input[data-use]")).forEach(function (i) {
          var k = i.getAttribute("data-use");
          var on = ST.answers.uses.indexOf(k) >= 0 && (k !== "uncensored" || ST.ackStored);
          if (!!i.checked !== on) i.checked = on;
        });
      } catch (e) {}
    });
  }
  async function onAck(input) {
    var want = !!input.checked;
    if (ST.ackBusy) { input.checked = !want; return; }
    ST.ackBusy = true; ST.ackErr = "";
    var r = await jreq("/api/advisor/answers", { uncensored_ack: want });
    ST.ackBusy = false;
    if (!r.ok) {
      ST.ackErr = (want ? "Could not store the acknowledgement: " : "Could not withdraw it: ") +
        errOf(r, "the advisor did not answer") + ".";
      paintRoots("ack");
      return;
    }
    var ans = (r.data && r.data.answers) || {};
    ST.ackStored = ans.uncensored_ack === true;
    if (ST.state && ST.state.answers) ST.state.answers.uncensored_ack = ST.ackStored;
    if (ans.saved_at != null) ST.everSaved = true;
    ST.saved = snapAnswers(ans);
    if (!ST.ackStored) ST.saved.uses = ST.saved.uses.filter(function (u) { return u !== "uncensored"; });
    var a = ST.answers || (ST.answers = snapAnswers(null));
    if (ST.ackStored) {
      ST.pendingUnc = false;
      if (a.uses.indexOf("uncensored") < 0) a.uses = canonUses(a.uses.concat(["uncensored"]));
    } else {
      a.uses = a.uses.filter(function (u) { return u !== "uncensored"; });
      ST.pendingUnc = false;
      // withdrawn: an uncensored pick the sheet already holds goes back to the
      // Recommended plan NOW, not when the re-rank answers (it may never: the
      // sheet tells apply ack_uncensored for an uncensored pick, and apply
      // trusts that flag)
      reconcilePick(false);
      paintRoots("res");
    }
    syncChips();
    paintRoots("ack");
    paintRoots("save");
    scheduleRank();
  }
  function onPick(input) {
    var key = input.getAttribute("data-pick");
    var r = ST.result || {};
    var p = findPlan(key);
    if (!p) return;
    if (p.uncensored && !ST.ackStored) {          // belt and braces: the radio is disabled
      input.checked = false;
      return;
    }
    var isRec = r.recommended && planKey(r.recommended) === key;
    ST.sel = isRec ? null : key;
    ST.userPicked = !isRec;
    sendPick(p);
    roots().forEach(function (root) {
      try {
        Array.prototype.slice.call(root.querySelectorAll("[data-plan]")).forEach(function (n) {
          if (n.tagName && n.tagName.toLowerCase() === "button") return;
          var mine = n.getAttribute("data-plan") === key;
          if (n.classList) n.classList.toggle("is-picked", mine);
        });
        Array.prototype.slice.call(root.querySelectorAll("input[data-pick]")).forEach(function (i) {
          var on = i.getAttribute("data-pick") === key;
          if (!!i.checked !== on) i.checked = on;
        });
      } catch (e) {}
    });
  }

  function wireRoot(root) {
    if (!root) return;
    root.onchange = function (ev) {
      var t = ev && ev.target;
      if (!t || !t.getAttribute) return;
      if (t.getAttribute("data-use") != null) return onUse(t);
      if (t.getAttribute("data-ack") != null) return onAck(t);
      if (t.getAttribute("data-pri") != null) {
        if (!t.checked) return;
        (ST.answers || (ST.answers = snapAnswers(null))).priority = t.value || t.getAttribute("data-pri");
        paintRoots("save"); scheduleRank(); return;
      }
      if (t.getAttribute("data-pow") != null) {
        if (!t.checked) return;
        (ST.answers || (ST.answers = snapAnswers(null))).power = t.value || t.getAttribute("data-pow");
        paintRoots("save"); scheduleRank(); return;
      }
      if (t.getAttribute("data-pick") != null) return onPick(t);
    };
    root.onclick = function (ev) {
      var t = ev && ev.target;
      var b = t && t.closest ? t.closest("button") : null;
      if (!b || !root.contains(b) || b.disabled) return;
      var act = b.getAttribute("data-adv-act");
      if (act === "unc-cancel") {
        ST.pendingUnc = false; ST.ackErr = "";
        paintRoots("ack");
        try { var c = root.querySelector('input[data-use="uncensored"]'); if (c) c.focus(); } catch (e) {}
        return;
      }
      if (act === "recheck") { recheck(); return; }
      if (act === "save-answers") { saveAnswersClick(); return; }
      var pa = b.getAttribute("data-plan-act");
      if (pa) planAction(pa, b.getAttribute("data-plan"), b);
    };
  }

  // ==========================================================================
  // the onboarding step (shared contract 2)
  // ==========================================================================
  var step = {
    key: "models",
    title: "Your Mac, and the brain that fits it",
    sub: "Detected on this Mac, then matched to what you want to do. Nothing downloads until you press Download.",
    render: function (ctx) {
      ensureCSS();
      ST.ctx = ctx || null;
      return rootHTML("step", ctx);
    },
    wire: function (node, ctx) {
      if (ctx) ST.ctx = ctx;
      if (!node || typeof node.querySelector !== "function") return;
      var root = (node.getAttribute && node.getAttribute("data-adv-root") != null) ? node
        : node.querySelector('[data-adv-root="step"]');
      if (!root) return;
      wireRoot(root);
      // an owner's earlier pick of an alternative, made before this paint
      try {
        if (!ST.userPicked && ctx && typeof ctx.getPick === "function" && ST.result) {
          var gp = ctx.getPick() || {};
          var k = String(gp.primary || "") + "|" + String(gp.background || "");
          var p = findPlan(k), rec = ST.result.recommended;
          if (p && rec && planKey(rec) !== k && (!p.uncensored || ST.ackStored)) {
            ST.sel = k; ST.userPicked = true; ST.lastPickKey = k;
            paintRoots("res");
          }
        }
      } catch (e) {}
      // A NEW sheet open (a re-run) over a state this page read earlier: the
      // sheet's own state is fresh (open() re-reads it, a new ctx.data each
      // time) and ours may predate a download that finished since — the body
      // would quote "~23 GB to download" next to an action row that says
      // Continue. Re-read once per open (the server re-checks what is local).
      var fresh = !!(ctx && ctx.data && typeof ctx.data === "object" && ST.openData !== ctx.data);
      if (fresh) ST.openData = ctx.data;
      if (!ST.state && !ST.stateErr) {
        loadState(false);
      } else if (fresh && ST.state && !ST.loading) {
        loadState(false);
      }
      if (ST.state && ctx && typeof ctx.getPick === "function") {
        // a sheet whose pick is not the plan this body shows (a re-run over a
        // state this page already has, answers changed in Settings): hand it
        // over once, outside the paint that called us. Idempotent: a pick
        // that already matches sends nothing.
        var sp = selectedPlan(), gk = "";
        try { var g = ctx.getPick() || {}; gk = String(g.primary || "") + "|" + String(g.background || ""); }
        catch (e) { gk = ""; }
        // ...and at most once per plan per second: a sheet whose setPick
        // repaints the body AND declines to keep the pick would otherwise turn
        // render -> wire -> setPick -> render into a timer loop
        if (sp && planKey(sp) !== gk && !(ST.wireSent.key === planKey(sp) && Date.now() - ST.wireSent.at < 1000)) {
          ST.wireSent = { key: planKey(sp), at: Date.now() };
          setTimeout(function () { sendPick(sp); }, 0);
        }
      }
    },
    save: async function () {
      try {
        if (!ST.state || !ST.answers) return {};
        var body = answersBody();
        if (ST.everSaved && sameAnswers(body, ST.saved)) return {};
        var r = await jreq("/api/advisor/answers", body);
        if (r.status === 400) return { err: errOf(r, "The answers were not accepted") };
        if (!r.ok) return {};                          // advisory: never blocks onboarding
        var ans = (r.data && r.data.answers) || body;
        ST.saved = snapAnswers(ans);
        if (!ST.ackStored) ST.saved.uses = ST.saved.uses.filter(function (u) { return u !== "uncensored"; });
        ST.everSaved = true;
        return {};
      } catch (e) { return {}; }
    }
  };

  // ==========================================================================
  // the Settings card (§8.2)
  // ==========================================================================
  function cardShell() {
    return "<h2>" + G.compass + 'Model advisor <span class="tiny">advisory: nothing changes until you click</span></h2>' +
      '<div class="body">' + rootHTML("card", null) + "</div>";
  }
  var relocatedOnce = false;
  function mountCard(host) {
    var d = D();
    if (!d) return null;
    ensureCSS();
    var body = host || null;
    if (!body) {
      var panel = d.getElementById(PANEL_ID);
      // no panel yet => wait for the next mindExtras() pass; deliberately NO
      // #view-mind fallback (the relocator would file us under System & Data)
      if (!panel) return null;
      body = panel.querySelector(".set-body") || panel;
    }
    var el = d.getElementById(CARD_ID);
    if (!el) {
      el = d.createElement("section");
      el.id = CARD_ID;
      el.className = "card glass set-legacy";
      el.style.order = "-1";
      body.insertBefore(el, body.firstChild);
    } else if (el.parentNode !== body) {
      try { body.insertBefore(el, body.firstChild); } catch (e) {}
      el.style.order = "-1";
    }
    try { el.innerHTML = cardShell(); } catch (e) { return el; }
    wireRoot(el.querySelector("[data-adv-root]"));
    if (!relocatedOnce) {
      relocatedOnce = true;
      try { if (typeof W.settingsRelocate === "function") W.settingsRelocate(); } catch (e) {}
    }
    if ((!ST.state && !ST.loading) || (ST.state && !ST.loading && Date.now() - ST.fetchedAt > STALE_MS)) {
      if (!ST.state) ST.stateErr = "";
      loadState(false);
    }
    return el;
  }
  function recheck() {
    if (ST.loading) return;
    ST.recheckErr = ""; ST.stateErr = "";
    var p = loadState(true);
    paintRoots("all");
    return p;
  }
  async function saveAnswersClick() {
    if (ST.saveBusy) return;
    ST.saveBusy = true; ST.saveErr = "";
    paintRoots("save");
    var r = await jreq("/api/advisor/answers", answersBody());
    ST.saveBusy = false;
    if (r.ok) {
      var ans = (r.data && r.data.answers) || answersBody();
      ST.saved = snapAnswers(ans);
      if (!ST.ackStored) ST.saved.uses = ST.saved.uses.filter(function (u) { return u !== "uncensored"; });
      ST.everSaved = true;
    } else {
      ST.saveErr = "Not saved: " + errOf(r, "the advisor did not answer") + ".";
    }
    paintRoots("save");
  }

  function setMsg(key, text, bad) {
    ST.msg[key] = [text, !!bad];
    roots().forEach(function (root) {
      try {
        Array.prototype.slice.call(root.querySelectorAll("p[data-msg]")).forEach(function (p) {
          if (p.getAttribute("data-msg") !== key) return;
          p.textContent = text;
          if (p.classList) p.classList.toggle("bad", !!bad);
        });
      } catch (e) {}
    });
  }
  function ask(text) {
    try { return typeof W.confirm === "function" ? !!W.confirm(text) : false; } catch (e) { return false; }
  }
  // the model menu's row for an id, or null (the menu is built on its first open)
  function liveRow(id) {
    var d = D();
    if (!d || typeof d.querySelectorAll !== "function") return null;
    try {
      var rows = d.querySelectorAll(".mmi");
      for (var i = 0; i < rows.length; i++) if (rows[i].getAttribute("data-id") === id) return rows[i];
    } catch (e) {}
    return null;
  }
  function lanes(p) { return [p.primary, p.background].filter(function (l) { return l && l.id; }); }

  // A focused button that turns disabled drops focus to <body>, and turning it
  // back on does not return it, so a keyboard user lost their place in
  // Settings on every plan action (motion-overlays-4). Focus goes back to the
  // button — or, if a repaint replaced it, to the same action of the same
  // plan, then that plan's first enabled action — but only when it is still
  // on <body>: focus the owner moved on purpose is never taken (paintFoot's
  // rule in aux_onboarding.js).
  function planRefocus(button, act, key, mode) {
    var d = D(), a = d && d.activeElement;
    if (a && a !== d.body) return;
    var to = (button && button.isConnected !== false && !button.disabled) ? button : null;
    if (!to) {
      roots().forEach(function (root) {
        if (to || (root.getAttribute("data-adv-root") || "") !== mode) return;
        var list = Array.prototype.slice.call(root.querySelectorAll("button[data-plan-act]")).filter(function (b) {
          return b.getAttribute("data-plan") === key && !b.disabled;
        });
        to = list.filter(function (b) { return b.getAttribute("data-plan-act") === act; })[0] || list[0] || null;
      });
    }
    if (to) { try { to.focus({ preventScroll: true }); } catch (e) {} }
  }

  async function planAction(act, key, button) {
    var p = findPlan(key);
    if (!p || ST.busy) return;
    ST.busy = act;
    var had = false, mode = "";
    try {
      var d0 = D();
      had = !!(button && d0 && d0.activeElement === button);
      var r0 = button && button.closest ? button.closest("[data-adv-root]") : null;
      mode = r0 ? r0.getAttribute("data-adv-root") || "" : "";
    } catch (e) {}
    try { if (button) button.disabled = true; } catch (e) {}
    try {
      if (act === "add") await actAdd(p, key);
      else if (act === "download") await actDownload(p, key);
      else if (act === "bg") await actBackground(p, key);
      else if (act === "switch") await actSwitch(p, key);
    } catch (e) {
      setMsg(key, "That did not work: " + (e && e.message ? e.message : "unexpected error") + ".", true);
    } finally {
      ST.busy = "";
      try { if (button && button.isConnected !== false) button.disabled = false; } catch (e) {}
      try { if (had) planRefocus(button, act, key, mode); } catch (e) {}
    }
  }
  async function addOne(id) {
    var r = await jreq("/api/models/add", { id: id });
    return r.ok ? "" : errOf(r, "the model menu did not take it");
  }
  async function actAdd(p, key) {
    var errs = [];
    var ls = lanes(p);
    for (var i = 0; i < ls.length; i++) {
      var e = await addOne(ls[i].id);
      if (e) errs.push(laneLabel(ls[i]) + ": " + e);
    }
    if (errs.length) setMsg(key, "Not added: " + errs.join(" · ") + ".", true);
    else setMsg(key, "In the model menu: " + ls.map(laneLabel).join(" and ") + ".", false);
  }
  async function actDownload(p, key) {
    if (p.disk_ok === false) return;
    var todo = lanes(p).filter(function (l) { return !l.downloaded; });
    if (!todo.length) { setMsg(key, "Everything in this plan is already on this Mac.", false); return; }
    var free = hw().disk_free_gb;
    var size = (p.download_unknown && !p.download_gb) ? "a download of unknown size" : "~" + gb(p.download_gb) + " download";
    if (!ask("Download " + todo.map(laneLabel).join(" and ") + "?\n" + size +
             (num(free) ? " · " + gb(free) + " free" : "") +
             "\nIt keeps downloading in the background — keep the app open. Nothing is switched.")) return;
    var started = [], errs = [];
    for (var i = 0; i < todo.length; i++) {
      var l = todo[i];
      var e = await addOne(l.id);
      if (e) { errs.push(laneLabel(l) + ": " + e); continue; }
      var r = await jreq("/api/models/download", { id: l.id });
      if (r.ok) started.push(laneLabel(l));
      else errs.push(laneLabel(l) + ": " + (r.data && r.data.error === "not enough free disk"
        ? "not enough free disk" : errOf(r, "the download did not start")));
    }
    if (errs.length) setMsg(key, (started.length ? "Downloading " + started.join(" and ") + ". " : "") +
      "Did not start: " + errs.join(" · ") + ".", true);
    else setMsg(key, "Downloading " + started.join(" and ") + ". The model menu shows the progress.", false);
  }
  async function actBackground(p, key) {
    var l = p.background;
    if (!l || !l.id) return;
    // bg-model is written whether or not the weights are here, and the lane
    // pulls a missing model itself when it starts (past the dashboard's disk
    // check): say so BEFORE the click is confirmed, not after
    var nm = laneLabel(l), note = " It takes effect the next time the background lane starts." +
      (l.downloaded ? "" : " " + nm + " is not on this Mac yet, so the lane would have to download it when it starts: press Download first.");
    var body = { background: l.id };
    if (l.uncensored) {
      if (!ask((ackText() || nm + " is an uncensored model.") + "\n\nUse " + nm +
               " as the background lane anyway?" + note)) return;
      body.ack_uncensored = true;
    } else if (!ask("Use " + nm + " as the background lane?" + note)) return;
    var r = await jreq("/api/onboarding/apply", body);
    if (r.status === 409 && r.data && r.data.needs_ack && body.ack_uncensored !== true) {
      // a lane the advisor did not know was uncensored: acknowledge, then ONE retry
      if (!ask((ackText() || nm + " is an uncensored model.") + "\n\nUse " + nm +
               " as the background lane anyway?")) { setMsg(key, "Nothing changed.", false); return; }
      r = await jreq("/api/onboarding/apply", { background: l.id, ack_uncensored: true });
    }
    var ap = (r.data && r.data.applied) || {};
    if (r.ok && (ap.background || ap.background_lane || ap.background_pending)) {
      setMsg(key, ap.background_note ? String(ap.background_note) + "."
        : "Background lane set to " + nm + "." + note, false);
    } else {
      setMsg(key, "The background lane was not changed: " + errOf(r, "the dashboard refused") + ".", true);
    }
  }
  async function actSwitch(p, key) {
    var l = p.primary;
    if (!l || !l.downloaded) return;
    var nm = laneLabel(l);
    // This card's own state can be stale (it is read on a Settings visit and on
    // Re-check): when the model menu has a LIVE row for this model, its
    // data-* are the truth. Never restart the model server for a model that is
    // already the assistant, and never let the menu turn this button into a
    // download because it does not see the weights yet.
    var live = liveRow(l.id);
    if (live) {
      if (live.getAttribute("data-active") === "1") { setMsg(key, nm + " is already the assistant.", false); return; }
      if (live.getAttribute("data-busy") === "1") { setMsg(key, nm + " is busy (downloading or loading): wait for it to finish.", true); return; }
      if (live.getAttribute("data-dl") !== "1") { setMsg(key, "The model menu does not see " + nm + " on this Mac yet: download it first.", true); return; }
    }
    // the model menu's own switch path (its confirm, the swap overlay, the
    // uncensored acknowledgement) — a row-shaped stand-in, as t_b29 drives it
    if (typeof W.onModelClick === "function") {
      var liveAttr = function (a) { return live ? (live.getAttribute(a) || "") : ""; };
      var fake = {
        dataset: { id: l.id, dl: "1", active: "0", busy: "0",
                   uncensored: (l.uncensored || liveAttr("data-uncensored") === "1") ? "1" : "",
                   fit: (l.fit === "no" || liveAttr("data-fit") === "no") ? "no" : "",
                   disk: liveAttr("data-disk") },
        querySelector: function (s) { return s === ".nm" ? { textContent: nm } : null; }
      };
      await W.onModelClick(fake);
      return;
    }
    var unc = !!l.uncensored;
    var text = "Switch the agent to " + nm + "? The model server restarts (~30–60s to load).";
    if (!ask(unc ? (ackText() || nm + " is an uncensored model.") + "\n\n" + text : text)) return;
    var r = await jreq("/api/models/switch", unc ? { id: l.id, ack_uncensored: true } : { id: l.id });
    if (r.status === 409 && r.data && r.data.needs_ack && !unc) {
      if (!ask((r.data.ack_text || r.data.message || ackText() || nm + " is an uncensored model.") +
               "\n\nSwitch to it anyway?")) { setMsg(key, "The switch was cancelled.", false); return; }
      r = await jreq("/api/models/switch", { id: l.id, ack_uncensored: true });
    }
    if (r.ok) setMsg(key, "Switching to " + nm + ": the model server is restarting.", false);
    else setMsg(key, "The switch did not start: " + errOf(r, "the dashboard refused") + ".", true);
  }

  // ==========================================================================
  // the model menu's Details panel (§8.3)
  // ==========================================================================
  function explain(row) {
    var a = row && row.advice;
    if (!a || typeof a !== "object") return "";
    var f = a.formula || {}, out = [];
    function pair(k, v, title) {
      if (!v) return;
      out.push("<dt>" + E(k) + "</dt><dd" + (title ? ' title="' + E(title) + '"' : "") + ">" + E(v) + "</dd>");
    }
    pair("Speed here", speedLine(a), [f.decode, f.decode_short].filter(Boolean).join("\n"));
    pair("First reply after wake", wakeLine(a), f.prefill || "");
    pair("Memory with cache", memLine(a, a.limit_gb), f.resident || "");
    pair("Advisor", a.why ? String(a.why) : "",
      "Advisory only: the menu still lets you pick any model that fits this Mac.");
    return out.join("");
  }

  // ==========================================================================
  // CSS (scoped to .adv; tokens come from index.html, never re-declared)
  // ==========================================================================
  function css() {
    var S = ".adv";
    return [
      S + "{container-type:inline-size;container-name:adv;min-width:0;font-size:13px;line-height:1.5;color:var(--ink)}",
      S + " *{box-sizing:border-box}",
      S + " .adv-ic{width:15px;height:15px;flex:0 0 auto;color:var(--iris);vertical-align:-3px}",
      S + " .adv-ic-s{width:13px;height:13px}",
      S + " .adv-top{display:grid;grid-template-columns:minmax(0,1fr);gap:16px 26px;margin:0 0 18px}",
      "@container adv (min-width:620px){" + S + " .adv-top{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}",
      "@supports not (container-type:inline-size){@media (min-width:760px){" + S +
        " .adv-top{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}}",
      // two label styles in the card: section labels (.adv-h, .adv-eyebrow,
      // .adv-alt-l: 11px caps, muted) and questions (.adv-qh: 13px, ink).
      // The first line of each top column sits in the same 20px line box, so
      // "Detected on this Mac" and the first question share a top line.
      S + " .adv-h{display:flex;align-items:center;gap:7px;min-height:20px;margin:0 0 9px;font-size:11px;font-weight:600;" +
        "line-height:20px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}",
      // the detected facts: one aligned label / value list, a hairline between
      // rows; a value wraps only between its phrases (.adv-ph is nowrap)
      S + " ul.adv-stats{list-style:none;margin:0;padding:0}",
      S + " ul.adv-stats li{display:flex;align-items:baseline;gap:12px;padding:6px 0;border-top:1px solid var(--hairline)}",
      S + " ul.adv-stats li:first-child{border-top:0;padding-top:0}",
      S + " .adv-k{flex:0 0 88px;font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}",
      S + " .adv-v{flex:1 1 auto;min-width:0;font-size:13px;font-weight:600;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}",
      S + " .adv-ph{white-space:nowrap}",
      "@container adv (max-width:300px){" + S + " .adv-ph{white-space:normal}}",
      S + " .adv-assumed{font-weight:500;color:var(--muted);cursor:help}",
      S + " .adv-qh{margin:14px 0 7px;font-size:13px;line-height:20px;font-weight:600;color:var(--ink)}",
      S + " .adv-q .adv-qh:first-child{margin-top:0;min-height:20px;margin-bottom:9px}",
      // the uses: a regular two-column grid of equal-width options (one
      // column when the card is narrow)
      S + " .adv-chips{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}",
      "@container adv (max-width:360px){" + S + " .adv-chips{grid-template-columns:minmax(0,1fr)}}",
      // unselected = available: ink text and an outline mixed off --muted
      // (>= 3:1), never grey-on-grey; selected keeps its tinted fill + tick
      S + " .adv-chip{position:relative;display:flex;align-items:center;gap:6px;min-height:40px;padding:6px 12px;" +
        "border-radius:12px;border:1px solid color-mix(in srgb,var(--muted) 62%,transparent);background:transparent;" +
        "font-size:12.5px;font-weight:500;line-height:1.3;" +
        "color:var(--ink);cursor:pointer;user-select:none;min-width:0;" +
        "transition-property:background-color,border-color,color;transition-duration:150ms;transition-timing-function:ease-out}",
      S + " .adv-chip:hover{border-color:var(--muted)}",
      S + " .adv-chip input,.adv .adv-seg-o input{position:absolute;opacity:0;width:1px;height:1px;margin:0;pointer-events:none}",
      S + " .adv-chip:has(input:checked){color:var(--ink);font-weight:600;border-color:color-mix(in srgb,var(--iris) 55%,transparent);" +
        "background:color-mix(in srgb,var(--iris) 15%,transparent)}",
      S + " .adv-chip:has(input:focus-visible),.adv .adv-seg-o:has(input:focus-visible){outline:2px solid var(--iris);outline-offset:2px}",
      S + " .adv-tick{width:14px;height:14px;flex:0 0 auto;display:none;color:var(--iris)}",
      S + " .adv-chip:has(input:checked) .adv-tick{display:inline-block}",
      S + " .adv-chip-unc:has(input:checked){border-color:color-mix(in srgb,var(--warn) 55%,transparent);" +
        "background:color-mix(in srgb,var(--warn) 14%,transparent)}",
      S + " .adv-chip-unc .adv-tick{color:var(--warn)}",
      S + " .adv-seg{display:inline-flex;flex-wrap:wrap;padding:2px;gap:2px;border-radius:11px;max-width:100%;" +
        "border:1px solid var(--field-edge);background:var(--field)}",
      S + " .adv-seg-o{position:relative;display:flex;align-items:center;justify-content:center;min-height:40px;" +
        "padding:0 12px;border-radius:9px;font-size:12.5px;font-weight:500;color:var(--ink);cursor:pointer;" +
        "transition-property:background-color,color;transition-duration:150ms;transition-timing-function:ease-out}",
      S + " .adv-seg-o:hover{color:var(--ink)}",
      S + " .adv-seg-o:has(input:checked){background:var(--iris);color:var(--iris-ink);font-weight:600}",
      S + " .adv-ack{margin:10px 0 2px;padding:10px 0 4px 12px;border-left:2px solid color-mix(in srgb,var(--warn) 60%,transparent)}",
      S + " .adv-ack-t{margin:0 0 8px;font-size:12px;line-height:1.55;color:var(--muted);text-wrap:pretty;max-width:68ch}",
      S + " .adv-ack-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px}",
      S + " .adv-check{display:inline-flex;align-items:center;gap:8px;min-height:40px;font-size:12.5px;font-weight:600;cursor:pointer}",
      S + " .adv-check input,.adv .adv-pick input{width:16px;height:16px;margin:0;accent-color:var(--iris);flex:0 0 auto}",
      S + " .adv-b{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:0 14px;border-radius:10px;" +
        "border:1px solid var(--field-edge);background:var(--field);color:var(--ink);font:inherit;font-size:12.5px;font-weight:600;" +
        "cursor:pointer;-webkit-appearance:none;appearance:none;" +
        "transition-property:background-color,border-color,opacity;transition-duration:150ms;transition-timing-function:ease-out}",
      S + " .adv-b:hover:not(:disabled){border-color:color-mix(in srgb,var(--iris) 45%,transparent)}",
      S + " .adv-b:focus-visible,.adv .adv-link:focus-visible,.adv summary:focus-visible{outline:2px solid var(--iris);outline-offset:2px}",
      S + " .adv-b:disabled{opacity:.5;cursor:default}",
      S + " .adv-b.go{background:var(--iris);border-color:var(--iris);color:var(--iris-ink)}",
      S + " .adv-link{min-height:40px;padding:0 4px;border:0;background:none;color:var(--iris);font:inherit;font-weight:600;cursor:pointer}",
      S + " .adv-link:disabled{color:var(--faint);cursor:default}",
      S + " .adv-off{font-size:12px;color:var(--muted);text-wrap:pretty}",
      S + " .adv-err{margin:8px 0 0;font-size:12px;color:var(--bad);text-wrap:pretty}",
      S + " .adv-bad{margin:6px 0 0;font-size:12px;color:var(--bad);text-wrap:pretty}",
      S + " .adv-wait{margin:0;font-size:12px;color:var(--muted)}",
      S + " .adv-status{margin:0 0 8px;font-size:12px;color:var(--muted);min-height:1em}",
      S + " .adv-plan{margin:0 0 14px;padding:2px 0 2px 14px;border-left:2px solid color-mix(in srgb,var(--iris) 60%,transparent)}",
      S + " .adv-plan-top{display:flex;align-items:center;flex-wrap:wrap;gap:6px 9px;margin:0 0 4px}",
      S + " .adv-plan-top .adv-pick{margin-left:auto}",
      S + " .adv-eyebrow{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;letter-spacing:.08em;" +
        "text-transform:uppercase;color:var(--muted)}",
      S + " h3.adv-plan-h{margin:0 0 4px;font-size:15px;font-weight:640;letter-spacing:-.01em;line-height:1.3;text-wrap:balance}",
      S + " .adv-why{margin:0 0 8px;font-size:12.5px;line-height:1.55;color:var(--muted);text-wrap:pretty}",
      S + " .adv-lane{display:flex;align-items:baseline;flex-wrap:wrap;gap:3px 8px;padding:8px 0;border-top:1px solid var(--hairline)}",
      S + " .adv-lane-r{flex:0 0 84px;font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}",
      S + " .adv-lane-n{font-size:13px;font-weight:620}",
      S + " .adv-lane-s{flex:1 1 100%;padding-left:92px;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;text-wrap:pretty}",
      S + " .adv-fit{flex:1 1 100%;padding-left:92px;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}",
      "@container adv (max-width:460px){" + S + " .adv-lane-s," + S + " .adv-fit{padding-left:0}}",
      S + " .adv-fit.warn{color:var(--warn)}" + S + " .adv-fit.bad{color:var(--bad)}",
      S + " .adv-tag{display:inline-flex;align-items:center;font-size:11px;font-weight:700;letter-spacing:.06em;" +
        "text-transform:uppercase;padding:1px 7px;border-radius:20px}",
      S + " .adv-tag.ok{color:var(--ok);background:color-mix(in srgb,var(--ok) 14%,transparent)}",
      S + " .adv-tag.warn{color:var(--warn);background:color-mix(in srgb,var(--warn) 15%,transparent)}",
      S + " .adv-unc{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:700;letter-spacing:.05em;" +
        "text-transform:uppercase;padding:1px 7px;border-radius:20px;color:var(--warn);" +
        "background:color-mix(in srgb,var(--warn) 15%,transparent)}",
      S + " .adv-unc .adv-ic{color:var(--warn);width:11px;height:11px;vertical-align:0}",
      S + " .adv-total{margin:8px 0 0;font-size:12.5px;font-variant-numeric:tabular-nums}",
      S + " .adv-total.ok{color:var(--ok)}",
      S + " ul.adv-notes{margin:8px 0 0;padding:0 0 0 16px;font-size:12px;line-height:1.5;color:var(--muted)}",
      S + " ul.adv-notes li{margin:2px 0;text-wrap:pretty}",
      S + " ul.adv-gnotes{margin:10px 0}",
      S + " .adv-pick{display:inline-flex;align-items:center;gap:7px;min-height:40px;padding:0 4px;font-size:12px;font-weight:600;cursor:pointer}",
      S + " .adv-pick.is-locked{cursor:default;color:var(--faint)}",
      S + " .adv-h2{margin:18px 0 4px;padding-bottom:6px;border-bottom:1px solid var(--hairline);font-size:12px;font-weight:680}",
      S + " ul.adv-alts{list-style:none;margin:0;padding:0}",
      S + " li.adv-alt{padding:10px 0;border-bottom:1px solid var(--hairline)}",
      S + " li.adv-alt:last-child{border-bottom:0}",
      S + " .adv-alt-top{display:flex;flex-wrap:wrap;align-items:center;gap:5px 8px}",
      S + " .adv-alt-l{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}",
      S + " .adv-alt-n{margin:3px 0 2px;font-size:13px;font-weight:620;text-wrap:balance}",
      S + " .adv-alt .adv-why{margin:0 0 3px;font-size:12px}",
      S + " .adv-alt-s{margin:0;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;text-wrap:pretty}",
      S + " .adv-alt-pick{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px;margin-top:2px}",
      S + " li.adv-alt.is-picked .adv-alt-n," + S + " .adv-rec.is-picked h3.adv-plan-h{color:var(--ink)}",
      S + " li.adv-alt.is-picked{box-shadow:inset 2px 0 0 var(--iris);padding-left:10px}",
      S + " .adv-acts{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:10px 0 0}",
      S + " .adv-msg{margin:6px 0 0;min-height:1em;font-size:12px;color:var(--muted);text-wrap:pretty}",
      S + " .adv-msg.bad{color:var(--bad)}",
      S + " details.adv-wont," + S + " details.adv-how{margin:12px 0 0;border-top:1px solid var(--hairline)}",
      S + " summary{display:flex;align-items:center;gap:7px;min-height:40px;font-size:12px;font-weight:620;cursor:pointer;list-style:none}",
      S + " summary::-webkit-details-marker{display:none}",
      S + " .adv-chev{width:13px;height:13px;color:var(--faint);transition-property:transform;transition-duration:180ms;" +
        "transition-timing-function:cubic-bezier(.22,1,.36,1)}",
      S + " details[open]>summary .adv-chev{transform:rotate(90deg)}",
      S + " ul.adv-wont-l{list-style:none;margin:0 0 8px;padding:0}",
      S + " ul.adv-wont-l li{display:flex;flex-wrap:wrap;align-items:baseline;gap:3px 8px;padding:6px 0;font-size:12px}",
      S + " ul.adv-wont-l b{font-weight:620}",
      S + " ul.adv-wont-l span{flex:1 1 220px;color:var(--muted);text-wrap:pretty}",
      S + " .adv-how-b{padding:0 0 10px}",
      S + " .adv-how-b p{margin:0 0 8px;font-size:11.5px;color:var(--muted);text-wrap:pretty}",
      S + " .adv-how-b dl{margin:0;display:grid;grid-template-columns:minmax(0,1fr);gap:2px}",
      S + " .adv-how-b dt{margin-top:8px;font-size:11px;font-weight:680;color:var(--ink)}",
      S + " .adv-how-b dd{margin:0;font-size:11px;color:var(--muted)}",
      S + " .adv-how-b code{display:block;margin:2px 0;padding:5px 8px;border-radius:7px;background:var(--chip);" +
        "font:11px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink);white-space:pre-wrap;overflow-wrap:anywhere}",
      S + " .adv-none{margin:0 0 10px;font-size:12.5px;color:var(--muted)}",
      S + " .adv-now{margin:0 0 16px;padding:0 0 12px;border-bottom:1px solid var(--hairline)}",
      S + " .adv-now-h{margin:2px 0 3px;font-size:14px;font-weight:620;text-wrap:balance}",
      S + " .adv-eyebrow+.adv-verdict{margin-left:2px}",
      S + " .adv-verdict{font-size:11px;font-weight:700;padding:1px 8px;border-radius:20px;color:var(--muted);background:var(--chip)}",
      S + " .adv-verdict.v-fits{color:var(--ok);background:color-mix(in srgb,var(--ok) 14%,transparent)}",
      S + " .adv-verdict.v-tight{color:var(--warn);background:color-mix(in srgb,var(--warn) 15%,transparent)}",
      S + " .adv-verdict.v-no{color:var(--bad);background:color-mix(in srgb,var(--bad) 14%,transparent)}",
      S + " .adv-last{margin:2px 0 0;font-size:12px;color:var(--muted);display:flex;align-items:center;flex-wrap:wrap;gap:0 4px}",
      S + " .adv-savebar{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin:-6px 0 16px}",
      "@media (prefers-reduced-motion:reduce){" + S + " *{transition:none!important}}",
      ":root[data-reduce] " + S + " *{transition:none!important}"
    ].join("\n");
  }
  function ensureCSS() {
    var d = D();
    if (!d || typeof d.getElementById !== "function" || d.getElementById(CSS_ID)) return;
    try {
      var s = d.createElement("style");
      s.id = CSS_ID;
      s.textContent = css();
      (d.head || d.documentElement || d.body).appendChild(s);
    } catch (e) {}
  }

  // ==========================================================================
  // export + wiring into the page
  // ==========================================================================
  W.hermesAdvisor = {
    v: 1,
    __adv: true,
    step: step,
    mountCard: mountCard,
    explain: explain,
    fmt: fmt,
    ackText: ackText,
    ensureAckText: ensureAckText,
    css: css,
    recheck: recheck,
    // headless-harness surface: read-only views of the state
    get state() { return ST.state; },
    get result() { return ST.result; },
    get answers() { return ST.answers ? snapAnswers(ST.answers) : null; },
    get ackStored() { return ST.ackStored; },
    _st: ST
  };

  wrapDetails();
  try {
    var dd = D();
    if (dd && dd.readyState === "loading" && dd.addEventListener) {
      dd.addEventListener("DOMContentLoaded", function () { wrapDetails(); }, { once: true });
    }
  } catch (e) {}

  // chain window.mindExtras exactly like the other aux settings cards
  var prev = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { mountCard(); } catch (e) {}
  };
})();
