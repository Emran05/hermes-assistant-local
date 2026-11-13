// t_onboarding_steps.js — the first-run sheet's round-3 seams, on the REAL
// dashboard/aux_onboarding.js in the mini DOM (_a11y_dom.js).
//
//   HOOK    window.hermesOnboardingSteps / hermesOnboarding.registerStep
//           (round-3 shared contract 1): pushes made before the file loads
//           are seen (the array is adopted, never replaced), a same-key entry
//           replaces the earlier one in place, extra steps sit after prefs and
//           before done in registration order, "Step n of m" counts them, a
//           throwing render/wire/save/statusRow/summary costs only that step,
//           save()'s err keeps the sheet on the step (Skip always goes on),
//           applied + summary() lines reach the Ready step, statusRow() joins
//           the Connections rows, setErr() updates the error line in place,
//           and the first-run Esc rule is unchanged.
//   MODELS  the models step delegates to window.hermesAdvisor.step (shared
//           contract 2): its body renders above the sheet's own action row,
//           ctx.setPick() repaints ONLY that row ("Download recommended
//           (~17 GB)"), Next awaits the advisor's save() and stops on its err,
//           then posts the picks to /api/onboarding/apply as before (with
//           ack_uncensored when the pick is uncensored); a 409 needs_ack keeps
//           the step. No advisor, or one whose render throws: the legacy body.
//   MOTION  motion-system §8.9 through HermesAnim (the REAL aux_anim.js on the
//           recording library stubs, _anim_stubs.js): opening the sheet is one
//           sequence on the sheet; ONLY a step change animates (the column from
//           24 px on the side it came from, the new rail dot, the heading's
//           words, the Ready step's count-ups); a toggle, the busy label,
//           setPick and the 2.5 s download poll make zero animation calls; both
//           reduce switches and a missing runtime land the end state at once.
//
// No dashboard, no network (every /api answer is a route below), no model.
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, bootDashboard, harness } = require("./_a11y_dom.js");
const STUBS = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const ONB_SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_onboarding.js"), "utf8");
const ANIM_SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");
const OS_REDUCE = function (q) { return /prefers-reduced-motion/.test(q); };

const M27B = "mlx-community/Qwen3.8-27B-4bit";
const M9B = "mlx-community/Qwen3.5-9B-4bit";
const M4B = "mlx-community/Qwen3.5-4B-4bit";
const M2B = "mlx-community/Qwen3.5-2B-4bit";
const UNC = "orcarouter/Qwen3.8-27B-Uncensored-MLX";

function state(o) {
  o = o || {};
  const have = o.downloaded || [];
  return {
    ok: true, version: "1.4.0", done: o.done !== undefined ? o.done : true, done_at: null,
    sysinfo: { chip: "Apple M5 Max", ram_gb: 64, disk_free_gb: 412, macos: "26.1", laptop: true, cores: 18 },
    detect: { hermes_cli: true, hermes_path: "/x/hermes", claude_cli: true, mlx_venv: true,
              models_downloaded: have, fda: null, telegram_configured: false, google_configured: false },
    recommendation: {
      tier: o.tier || "full", primary: M27B, background: M9B, tight: false, alt: null,
      why: "Room for the full roster.", note: "", primary_label: "Qwen3.8-27B",
      background_label: "Qwen3.5-9B", download_gb: 23, total_gb: 23, already_downloaded: false,
      fits: [[M2B, "Qwen3.5-2B", 3, 1.7], [M4B, "Qwen3.5-4B", 4, 3.1], [M9B, "Qwen3.5-9B", 7, 6],
             [M27B, "Qwen3.8-27B", 19, 17]].map(function (r) {
        return { id: r[0], label: r[1], ram: r[2], size_gb: r[3], note: "", role: "primary",
                 fit: "ok", downloaded: have.indexOf(r[0]) >= 0 };
      }),
      advisor: o.advisorResult ? { ok: true, result: o.advisorResult } : undefined
    },
    prefs: { theme: null, idle_min: 10, idle_enabled: true, prewarm: true, claude_escalation: false,
             briefings: true, news: true, web_research: false,
             quiet_hours: { start: "22:00", end: "07:00" } }
  };
}

// a page: a blank document, (optionally) the motion runtime on the stubs,
// then aux_onboarding.js. o.pre(w) runs before the file (pre-load pushes, an
// advisor stub); st.posts collects every apply body.
async function page(o) {
  o = o || {};
  const w = createWindow({ media: o.media });
  const st = { posts: [], state: o.state || state(), reply: o.reply || null, done: 0 };
  w.__routes = {
    "/api/onboarding/state": function () { return JSON.parse(JSON.stringify(st.state)); },
    "/api/onboarding/apply": function (url, init) {
      const b = JSON.parse(init.body || "{}");
      st.posts.push(b);
      return st.reply ? st.reply(b) : { ok: true, errors: [], applied: Object.assign({}, b) };
    },
    "/api/onboarding/done": function () { st.done++; return { ok: true, done: true, ts: 1 }; },
    "/api/claude/foryou": { enabled: false },
    "/api/models": function () { return st.models || { models: [] }; }
  };
  let L = null;
  if (o.runtime !== false) {
    L = STUBS.install(w, { gsap: true, anime: true });
    w.__run(ANIM_SRC, "aux_anim.js");
  }
  if (o.reduce) w.document.documentElement.setAttribute("data-reduce", "1");
  if (o.pre) o.pre(w);
  w.__run(ONB_SRC, "aux_onboarding.js");
  await w.__tick();
  const P = { w: w, d: w.document, L: L, st: st, ob: w.hermesOnboarding };
  P.sheet = function () { return w.document.getElementById("onb-sheet"); };
  P.q = function (sel) { const n = P.sheet(); return n ? n.querySelector(sel) : null; };
  P.qa = function (sel) { const n = P.sheet(); return n ? Array.prototype.slice.call(n.querySelectorAll(sel)) : []; };
  P.text = function () { const n = P.sheet(); return n ? n.textContent : ""; };
  P.go = function () { return P.q(".onb-go"); };
  P.btn = function (a) { return P.q('.onb-foot button[data-act="' + a + '"]'); };
  P.click = async function (a) { const b = P.btn(a); if (!b) throw new Error("no " + a + " button"); w.__click(b); await w.__tick(12); };
  P.stepN = function () { const s = P.q(".onb-step-n"); return s ? s.textContent : ""; };
  P.key = function () { return P.ob.steps()[P.ob.state.step]; };
  P.open = async function (rerun) { await P.ob.open({ rerun: !!rerun }); await w.__tick(); };
  P.moves = function () { return moves(L); };
  return P;
}

function moves(L) {
  if (!L) return [];
  return L.calls.filter(function (c) {
    return (c.lib === "motion" && c.fn === "animate") || c.lib === "flip" ||
      (c.lib === "gsap" && /^(timeline|tl\.|fromTo|to)$|^tl\./.test(c.fn)) ||
      (c.lib === "anime" && (c.fn === "animate" || c.fn === "splitText"));
  });
}

async function scenario(name, fn) {
  try { await fn(); } catch (e) {
    T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | "));
  }
}

// an extra-step entry that records what the sheet does with it
function entry(key, o) {
  o = o || {};
  const E = {
    key: key, title: o.title || ("Step " + key), sub: o.sub || "A sub line", after: "prefs", optional: true,
    renders: 0, wires: [], saves: 0, ctx: null,
    render: function (ctx) {
      E.renders++; E.ctx = ctx;
      if (o.throwRender) throw new Error("render boom");
      return o.html || '<p class="x-' + key + '">Body of ' + key + '</p><input class="x-in" type="text">';
    },
    wire: function (node, ctx) {
      E.wires.push(node);
      if (o.throwWire) throw new Error("wire boom");
      const i = node.querySelector(".x-in");
      if (i) i.onchange = function () { E.changed = (E.changed || 0) + 1; };
    }
  };
  if (o.save) E.save = function (ctx) { E.saves++; return o.save(ctx); };
  if (o.statusRow) E.statusRow = o.statusRow;
  if (o.summary) E.summary = o.summary;
  if (o.eyebrow) E.eyebrow = o.eyebrow;
  return E;
}

function inSheetOf(P) { const a = P.d.activeElement; return !!a && a !== P.d.body && P.sheet().contains(a); }
function deferred() { let res, rej; const p = new Promise(function (a, b) { res = a; rej = b; }); return { p: p, resolve: res, reject: rej }; }

// a stand-in for aux_advisor.js's window.hermesAdvisor (contract 2)
function advisorStub(o) {
  o = o || {};
  const A = { renders: 0, wires: [], saves: 0, ctx: null, saveReply: o.saveReply || {} };
  A.step = {
    render: function (ctx) {
      A.renders++; A.ctx = ctx;
      if (o.throwRender) throw new Error("advisor render boom");
      if (o.html !== undefined) return o.html;
      return '<div class="adv-x"><ul class="adv-facts"><li>Apple M5 Max</li></ul>' +
        '<fieldset role="group"><label><input type="checkbox" data-use="code"> Coding help</label></fieldset>' +
        '<p class="adv-plan">Qwen3.8-27B as the assistant, Qwen3.5-9B for background work</p></div>';
    },
    wire: function (node, ctx) { A.wires.push({ node: node, ctx: ctx }); },
    save: function (ctx) {
      A.saves++;
      if (o.throwSave) throw new Error("advisor save boom");
      return Promise.resolve(A.saveReply);
    }
  };
  return { v: 1, step: A.step, rec: A };
}

// ===========================================================================
async function hook() {
  console.log("--- the extra-steps hook (contract 1) ---");

  await scenario("pre-load pushes", async function () {
    let pre = null;
    const a = entry("alpha");
    const P = await page({ runtime: false, pre: function (w) { pre = w.hermesOnboardingSteps = [a]; } });
    T.check("a push made BEFORE the file loaded is adopted: the same array object, never reassigned",
      P.w.hermesOnboardingSteps === pre && pre.length === 1);
    T.check("the live order: welcome, models, prefs, alpha, done",
      P.ob.steps().join(",") === "welcome,models,prefs,alpha,done", P.ob.steps());
    const b = entry("beta");
    T.check("registerStep accepts a valid entry", P.ob.registerStep(b) === true);
    T.check("...and pushes it onto the SAME array", P.w.hermesOnboardingSteps === pre && pre.indexOf(b) >= 0);
    T.check("extra steps keep registration order, before done",
      P.ob.steps().join(",") === "welcome,models,prefs,alpha,beta,done", P.ob.steps());
    const a2 = entry("alpha", { title: "Alpha, version two" });
    P.ob.registerStep(a2);
    T.check("a same-key entry REPLACES the earlier one in place (no duplicate, same slot)",
      P.ob.steps().join(",") === "welcome,models,prefs,alpha,beta,done" && pre.indexOf(a) < 0 && pre.indexOf(a2) === 0,
      P.ob.steps());
    const c = entry("gamma");
    P.w.hermesOnboardingSteps.push(c);            // a raw push after load
    await P.open(false);
    T.check("a raw push after load is seen at the next open", P.ob.steps().indexOf("gamma") === 5, P.ob.steps());
    T.check("'Step n of m' counts the extra steps (Step 1 of 7)", P.stepN() === "Step 1 of 7", P.stepN());
    T.check("the rail has one dot per live step", P.qa(".onb-rail li").length === 7, P.qa(".onb-rail li").length);
    P.ob.go(3);
    T.check("the replaced entry's step renders the NEW entry", /Alpha, version two/.test(P.text()) && a2.renders >= 1 && a.renders === 0);
    P.ob.go(2);
    const d = entry("delta");
    P.ob.registerStep(d);
    T.check("a late registration while open repaints: still on prefs, now 'of 8'",
      P.key() === "prefs" && P.stepN() === "Step 3 of 8", [P.key(), P.stepN()]);
  });

  await scenario("refusals", async function () {
    const warns = [];
    const P = await page({ runtime: false, pre: function (w) {
      w.console = { log: console.log, error: console.error, warn: function (m) { warns.push(String(m)); } };
      w.hermesOnboardingSteps = [{ key: "Bad Key", title: "x", render: function () { return ""; } }];
    } });
    const bad = [
      ["a built-in key", { key: "welcome", title: "t", render: function () { return ""; } }],
      ["the done key", { key: "done", title: "t", render: function () { return ""; } }],
      ["a one-letter key", { key: "x", title: "t", render: function () { return ""; } }],
      ["a 25-character key", { key: "abcdefghijklmnopqrstuvwxy", title: "t", render: function () { return ""; } }],
      ["a key with a capital", { key: "gitHub", title: "t", render: function () { return ""; } }],
      ["after something else", { key: "after-x", title: "t", after: "models", render: function () { return ""; } }],
      ["no render", { key: "norender", title: "t" }],
      ["no title", { key: "notitle", render: function () { return ""; } }],
      ["not an object", "github"]
    ];
    bad.forEach(function (b) { T.check("registerStep refuses " + b[0], P.ob.registerStep(b[1]) === false); });
    T.check("...and none of them became a step", P.ob.steps().join(",") === "welcome,models,prefs,done", P.ob.steps());
    T.check("a bad PRE-load push is ignored too, with a warning", warns.some(function (m) { return /extra step was ignored/.test(m); }), warns);
    T.check("the 24-character limit admits exactly 24", P.ob.registerStep(entry("abcdefghijklmnopqrstuvwx")) === true);
  });

  await scenario("render, wire, ctx", async function () {
    const g = entry("github", { title: "Connect <GitHub>", sub: "Optional & reversible", eyebrow: "Connections" });
    const P = await page({ runtime: false, pre: function (w) { w.hermesOnboardingSteps = [g]; } });
    await P.open(false);
    P.ob.go(3);
    const body = P.q(".onb-body");
    T.check("the extra step's body is the entry's render() output", !!P.q(".onb-body .x-github"));
    T.check("the head escapes the entry's title, sub and eyebrow",
      P.q("h1.onb-t").textContent === "Connect <GitHub>" && /Optional &amp; reversible/.test(P.sheet().innerHTML) &&
      /Connections/.test(P.q(".onb-eyebrow").textContent), P.q("h1.onb-t").innerHTML);
    T.check("wire(node) gets the step's .onb-body element", g.wires.length === 1 && g.wires[0] === body);
    T.check("ctx carries rerun, data (the state payload), repaint and setErr",
      g.ctx && g.ctx.rerun === false && g.ctx.data && g.ctx.data.sysinfo && typeof g.ctx.repaint === "function" &&
      typeof g.ctx.setErr === "function");
    T.check("an extra step offers Skip next to Continue", !!P.btn("skip") && P.go().textContent === "Continue");
    const input = P.q(".x-in");
    input.value = "typed by the owner";
    g.ctx.setErr("That key cannot see the repository.");
    T.check("ctx.setErr shows the message in the sheet's error line",
      P.q(".onb-err").textContent === "That key cannot see the repository." && !P.q(".onb-err").hidden);
    T.check("...IN PLACE: the body was not re-rendered (what the owner typed survives)",
      P.q(".onb-body") === body && P.q(".x-in").value === "typed by the owner" && g.renders === 1, g.renders);
    g.ctx.setErr("");
    T.check("setErr('') hides the line again", P.q(".onb-err").hidden === true);
    g.ctx.repaint();
    T.check("ctx.repaint re-renders the current step (render and wire again)", g.renders === 2 && g.wires.length === 2);
    T.check("our own wiring never runs inside an extra step's body (its inputs are its own)",
      !P.q(".onb-body .x-in").onchange || P.q(".onb-body .x-in").onchange.toString().indexOf("setPref") < 0);
  });

  await scenario("a throwing entry degrades, never fatal", async function () {
    const warns = [];
    const r = entry("rbad", { throwRender: true, save: function () { return Promise.resolve({}); } });
    const wb = entry("wbad", { throwWire: true });
    const P = await page({ runtime: false, pre: function (w) {
      w.console = { log: console.log, error: console.error, warn: function (m) { warns.push(String(m)); } };
      w.hermesOnboardingSteps = [r, wb];
    } });
    await P.open(false);
    P.ob.go(3);
    T.check("render throws: one muted line 'This step could not be shown.'",
      P.q(".onb-body p.onb-muted") && P.q(".onb-body").textContent === "This step could not be shown.", P.q(".onb-body") && P.q(".onb-body").innerHTML);
    T.check("...its wire() is not called on a body it did not render", r.wires.length === 0);
    P.ob.go(3); P.ob.go(3);
    T.check("...and the failure is logged ONCE however often it repaints",
      warns.filter(function (m) { return /"rbad" step could not render/.test(m); }).length === 1, warns);
    await P.click("next");
    T.check("Next still works past it (and skips the save of a step that could not be shown)",
      P.key() === "wbad" && r.saves === 0, [P.key(), r.saves]);
    T.check("wire throws: the body is still shown, logged once", !!P.q(".x-wbad") &&
      warns.filter(function (m) { return /"wbad" step could not wire/.test(m); }).length === 1);
    await P.click("next");
    T.check("...and Next goes on to the Ready step", P.key() === "done", P.key());
  });

  await scenario("save(): err keeps the step, Skip goes on", async function () {
    let reply = { err: "GitHub said that key is expired." };
    const g = entry("github", { save: function () { return Promise.resolve(reply); } });
    const P = await page({ runtime: false, pre: function (w) { w.hermesOnboardingSteps = [g]; } });
    await P.open(false);
    P.ob.go(3);
    await P.click("next");
    T.check("save() is awaited by Next", g.saves === 1);
    T.check("its err keeps the sheet on the step", P.key() === "github", P.key());
    T.check("...shown in the sheet's error line", P.q(".onb-err").textContent === "GitHub said that key is expired." &&
      !P.q(".onb-err").hidden);
    T.check("...without re-rendering the step", g.renders === 1, g.renders);
    T.check("...and the action row is usable again (not stuck busy)", !P.go().disabled && P.ob.state.busy === "");
    await P.click("skip");
    T.check("Skip goes on WITHOUT calling save()", P.key() === "done" && g.saves === 1, [P.key(), g.saves]);

    const g2 = entry("github", { save: function () { return Promise.reject(new Error("network down")); } });
    const P2 = await page({ runtime: false, pre: function (w) { w.hermesOnboardingSteps = [g2]; } });
    await P2.open(false);
    P2.ob.go(3);
    await P2.click("next");
    T.check("a save() that rejects keeps the step with a message", P2.key() === "github" &&
      /could not be saved \(network down\)/.test(P2.q(".onb-err").textContent), P2.q(".onb-err").textContent);
    const g3 = entry("github", { save: function () { throw new Error("sync boom"); } });
    const P3 = await page({ runtime: false, pre: function (w) { w.hermesOnboardingSteps = [g3]; } });
    await P3.open(false);
    P3.ob.go(3);
    await P3.click("next");
    T.check("...so does one that throws synchronously (never fatal)", P3.key() === "github" && !!P3.btn("skip"));
    await P3.click("skip");
    T.check("...and Skip still finishes the step", P3.key() === "done");
  });

  await scenario("a save in flight: Skip and Back win", async function () {
    let pending = deferred();
    const g = entry("github", { save: function () { return pending.p; } });
    const P = await page({ runtime: false, pre: function (w) { w.hermesOnboardingSteps = [g]; } });
    await P.open(false);
    P.ob.go(3);
    P.w.__click(P.btn("next"));
    await P.w.__tick();
    T.check("while save() runs, Next is disabled (busy) but Skip is not", P.go().disabled === true && !P.btn("skip").disabled);
    await P.click("skip");
    T.check("Skip during the save goes on at once", P.key() === "done");
    pending.resolve({ err: "late answer" });
    await P.w.__tick(12);
    T.check("...and the late answer is dropped (no jump, no error shown)", P.key() === "done" && !P.ob.state.err, [P.key(), P.ob.state.err]);

    pending = deferred();
    P.ob.go(3);
    P.w.__click(P.btn("next"));
    await P.w.__tick();
    await P.click("back");
    T.check("Back during the save goes back", P.key() === "prefs");
    pending.resolve({});
    await P.w.__tick(12);
    T.check("...and the save finishing later does not advance from where the owner went", P.key() === "prefs", P.key());
  });

  await scenario("summary, applied and statusRow", async function () {
    const g = entry("github", {
      save: function () { return Promise.resolve({ applied: ["GitHub: connected to you/notes", "  ", 7, "GitHub: connected to you/notes"] }); },
      summary: function () { return ["GitHub: pushes are paused until you approve one", 3, ""]; },
      statusRow: function (det) { return ["ok", "GitHub", "Connected" + (det.hermes_cli ? " (agent found)" : "")]; }
    });
    const bad = entry("zbad", {
      summary: function () { throw new Error("sum boom"); },
      statusRow: function () { throw new Error("row boom"); }
    });
    const odd = entry("odd", { statusRow: function () { return ['" onmouseover="x', "Odd", "<b>raw</b>"]; } });
    const P = await page({ runtime: false, pre: function (w) { w.hermesOnboardingSteps = [g, bad, odd]; } });
    await P.open(false);
    P.ob.go(2);
    const rows = P.qa(".onb-st");
    const last = rows.slice(-2);
    T.check("statusRow(det) is appended to the Connections rows (det = the detect object)",
      rows.some(function (r) { return /GitHub/.test(r.textContent) && /Connected \(agent found\)/.test(r.textContent); }), rows.map(function (r) { return r.textContent; }));
    T.check("...its class is a fixed vocabulary (a foreign class is dropped, text escaped)",
      last[1] && last[1].className === "onb-st" && /&lt;b&gt;raw/.test(last[1].innerHTML), last[1] && last[1].outerHTML);
    T.check("...a throwing statusRow is skipped, the rest still render", rows.length === 4 + 2, rows.length);
    T.check("hermesOnboarding.statusRows(det) carries them too",
      /GitHub/.test(P.ob.statusRows({ hermes_cli: true })));
    P.ob.go(3);
    await P.click("next");                        // github: save -> applied
    await P.click("next");                        // zbad: no save
    await P.click("next");                        // odd
    T.check("reached the Ready step", P.key() === "done", P.key());
    const sum = P.ob.state.applied;
    const iA = sum.indexOf("GitHub: connected to you/notes");
    const iS = sum.indexOf("GitHub: pushes are paused until you approve one");
    T.check("save()'s applied lines and summary() lines reach the Ready step", iA >= 0 && iS > iA, sum);
    T.check("...once each, only non-empty strings", sum.filter(function (l) { return /connected to you\/notes/.test(l); }).length === 1 &&
      sum.indexOf("") < 0 && sum.indexOf(3) < 0 && sum.indexOf("7") < 0);
    T.check("...AFTER every built-in step's own lines (models, prefs, welcome)",
      sum.findIndex(function (l) { return /^Assistant model/.test(l); }) < iA &&
      sum.findIndex(function (l) { return /^Appearance/.test(l); }) < iA &&
      sum.findIndex(function (l) { return /^Agent web research/.test(l); }) < iA, sum);
    T.check("a throwing summary() is skipped, never fatal", sum.length > 5 && /Ready/.test(P.text()));
    const items = P.qa("ul.onb-sum li").map(function (li) { return li.textContent; });
    T.check("the Ready step lists them", items.indexOf("GitHub: pushes are paused until you approve one") >= 0, items);
  });

  await scenario("a wire() that repaints cannot loop", async function () {
    const warns = [];
    const g = entry("loopy");
    const realWire = g.wire;
    g.wire = function (node, ctx) { realWire(node, ctx); ctx.repaint(); };
    const P = await page({ runtime: false, pre: function (w) {
      w.console = { log: console.log, error: console.error, warn: function (m) { warns.push(String(m)); } };
      w.hermesOnboardingSteps = [g];
    } });
    await P.open(false);
    P.ob.go(3);
    T.check("a repaint asked for from inside wire() runs once more, then stops",
      g.renders === 2 && g.wires.length === 2, [g.renders, g.wires.length]);
    T.check("...with one warning", warns.filter(function (m) { return /repaint from inside/.test(m); }).length === 1, warns);
    T.check("...and the sheet is intact", !!P.q(".x-loopy") && P.key() === "loopy");
  });

  await scenario("focus stays inside the modal across a busy / failed action row (review)", async function () {
    // Next disables the primary button while an extra step saves, the row is
    // repainted alone, and the browser parks focus on <body> — outside the
    // modal, where Tab leaves the sheet and Esc never reaches its keydown.
    // paint() used to refocus the primary action; paintFoot() must too.
    let reply = { err: "That key is not accepted." };
    const g = entry("github", { save: function () { return Promise.resolve(reply); } });
    const P = await page({ runtime: false, pre: function (w) { w.hermesOnboardingSteps = [g]; } });
    await P.open(true);
    P.ob.go(3);
    const inSheet = function () { const a = P.d.activeElement; return !!a && a !== P.d.body && P.sheet().contains(a); };
    P.go().focus();
    T.check("the primary button has focus before the click", P.d.activeElement === P.go());
    await P.click("next");
    T.check("after a save err the focus is back on the action row, not on <body>",
      inSheet() && P.d.activeElement.getAttribute("data-act") === "next", P.d.activeElement.tagName);
    P.w.__key(P.d.activeElement, "Escape");
    T.check("...so Esc reaches the sheet (a re-run closes)", P.sheet().hidden === true);

    // a field the owner moved to while it saved keeps the focus
    const slow = deferred();
    const g2 = entry("github", { save: function () { return slow.p; } });
    const P2 = await page({ runtime: false, pre: function (w) { w.hermesOnboardingSteps = [g2]; } });
    await P2.open(true);
    P2.ob.go(3);
    P2.go().focus();
    await P2.click("next");
    const fld = P2.q(".x-in");
    fld.focus();
    slow.resolve({ err: "Nope." });
    await P2.w.__tick(12);
    T.check("focus the owner moved into a field meanwhile is never taken back",
      P2.d.activeElement === fld, P2.d.activeElement && P2.d.activeElement.tagName);

    // a download: the focused Download button goes disabled, then becomes Continue
    const P3 = await page({ runtime: false });
    await P3.open(true);
    P3.ob.go(1);
    P3.btn("download").focus();
    await P3.click("download");
    await P3.w.__tick(12);
    T.check("after a download starts the focus is on the action row (now Continue)",
      inSheetOf(P3) && P3.d.activeElement.getAttribute("data-act") === "next", P3.d.activeElement.tagName);
    // a focus left on <body> on purpose (a click on plain text) is not stolen by a row repaint
    const adv = advisorStub();
    const P4 = await page({ runtime: false, pre: function (w) { w.hermesAdvisor = adv; } });
    await P4.open(true);
    P4.ob.go(1);
    P4.go().focus(); P4.d.activeElement.blur();
    T.check("(setup) nothing holds focus", P4.d.activeElement === P4.d.body);
    adv.rec.ctx.setPick({ primary: M27B, background: M9B, download_gb: 23 });
    T.check("setPick repaints the row; focus parked on <body> by the owner is left alone",
      P4.go().textContent === "Download recommended (~23 GB)" && P4.d.activeElement === P4.d.body,
      P4.d.activeElement && P4.d.activeElement.tagName);
    P4.go().focus();
    adv.rec.ctx.setPick({ primary: M27B, background: M9B, download_gb: 17 });
    T.check("...while a focused primary button keeps focus through the same repaint",
      P4.d.activeElement === P4.go() && P4.go().textContent === "Download recommended (~17 GB)");
  });

  await scenario("first-run Esc rule, with extra steps", async function () {
    const g = entry("github");
    const P = await page({ runtime: false, pre: function (w) { w.hermesOnboardingSteps = [g]; } });
    await P.open(false);
    P.ob.go(3);
    P.w.__key(P.go(), "Escape");
    T.check("Esc on an extra step of a FIRST run does nothing", !P.sheet().hidden && P.key() === "github");
    P.ob.go(4);
    P.w.__key(P.go(), "Escape");
    T.check("Esc on the final step (now step 5 of 5) closes", P.sheet().hidden === true);
    await P.open(true);
    P.ob.go(3);
    P.w.__key(P.go(), "Escape");
    T.check("a RE-RUN closes from the extra step", P.sheet().hidden === true);
  });
}

// ===========================================================================
async function models() {
  console.log("--- the models step through the advisor (contract 2) ---");

  await scenario("delegation", async function () {
    const adv = advisorStub();
    const P = await page({ runtime: false, pre: function (w) { w.hermesAdvisor = adv; } });
    await P.open(false);
    P.ob.go(1);
    T.check("the advisor renders the models step's body", !!P.q(".onb-adv .adv-x") && adv.rec.renders === 1);
    T.check("...instead of the legacy recommendation block", !P.q(".onb-rec") && !P.q("ul.onb-alts"));
    T.check("...under the sheet's own head", P.q("h1.onb-t").textContent === "Your Mac, and the brain that fits it");
    T.check("...whose sub line no longer says nothing was asked of the owner (a questionnaire follows)",
      !/nothing here was asked/.test(P.q("p.onb-s").textContent) && /matched to what you want to do/.test(P.q("p.onb-s").textContent),
      P.q("p.onb-s").textContent);
    T.check("the sheet keeps its action row BELOW the advisor body",
      !!P.q(".onb-foot .onb-go") && P.go().getAttribute("data-act") === "download" && !!P.btn("later") &&
      !P.q(".onb-adv .onb-go"));
    const w0 = adv.rec.wires[0];
    T.check("wire(node, ctx): node is the advisor's own body element", w0 && w0.node === P.q(".onb-adv"));
    T.check("ctx = hook ctx + getPick/setPick", w0 && typeof w0.ctx.getPick === "function" && typeof w0.ctx.setPick === "function" &&
      typeof w0.ctx.repaint === "function" && typeof w0.ctx.setErr === "function" && w0.ctx.data.sysinfo.ram_gb === 64);
    const pk = w0.ctx.getPick();
    T.check("getPick() is the sheet's pick (seeded from the recommendation), as a copy",
      pk.primary === M27B && pk.background === M9B && pk !== P.ob.state.pick);
    pk.primary = "evil/x";
    T.check("...mutating the copy changes nothing", P.ob.state.pick.primary === M27B);

    const body = P.q(".onb-adv");
    const renders = adv.rec.renders;
    T.check("setPick({primary, background, download_gb}) is accepted",
      w0.ctx.setPick({ primary: M27B, background: M9B, download_gb: 17 }) === true);
    T.check("...the Download button now quotes the plan: 'Download recommended (~17 GB)'",
      P.go().textContent === "Download recommended (~17 GB)", P.go().textContent);
    T.check("...and ONLY the action row repainted (same advisor body node, no re-render)",
      P.q(".onb-adv") === body && adv.rec.renders === renders, adv.rec.renders);
    T.check("setPick with no primary is refused", w0.ctx.setPick({ background: M9B }) === false &&
      w0.ctx.setPick({ primary: M27B, background: 5 }) === false && P.ob.state.pick.download_gb === 17);
    w0.ctx.setPick({ primary: M9B, background: M2B, download_gb: 0 });
    T.check("a pick that is not on this Mac yet keeps 'Download recommended' (0 GB quotes no size)",
      P.go().textContent === "Download recommended", P.go().textContent);
  });

  await scenario("the advisor's own sub line wins when it has one (and is escaped)", async function () {
    const adv = advisorStub();
    adv.step.sub = "Sub <b>from</b> the advisor";
    const P = await page({ runtime: false, pre: function (w) { w.hermesAdvisor = adv; } });
    await P.open(false);
    P.ob.go(1);
    T.check("the head carries the advisor's sub, as text", P.q("p.onb-s").textContent === "Sub <b>from</b> the advisor" &&
      !P.q("p.onb-s b"), P.q("p.onb-s").innerHTML);
  });

  await scenario("a pick already on this Mac", async function () {
    const adv = advisorStub();
    const P = await page({ runtime: false, state: state({ downloaded: [M9B, M2B] }), pre: function (w) { w.hermesAdvisor = adv; } });
    await P.open(false);
    P.ob.go(1);
    adv.rec.wires[0].ctx.setPick({ primary: M9B, background: M2B, download_gb: 0 });
    T.check("everything picked is local (the detected list): Continue, no download",
      P.go().textContent === "Continue" && P.go().getAttribute("data-act") === "next", P.go().textContent);
  });

  await scenario("Next: the advisor's save, then the picks", async function () {
    const order = [];
    const adv = advisorStub();
    const realSave = adv.step.save;
    adv.step.save = function (ctx) { order.push("advisor"); return realSave(ctx); };
    const P = await page({ runtime: false, state: state({ downloaded: [M27B, M9B] }), pre: function (w) { w.hermesAdvisor = adv; } });
    const realApply = P.w.__routes["/api/onboarding/apply"];
    P.w.__routes["/api/onboarding/apply"] = function (u, i) { order.push("apply"); return realApply(u, i); };
    await P.open(false);
    P.ob.go(1);
    P.st.posts.length = 0;
    await P.click("next");
    T.check("Next awaits the advisor's save() BEFORE posting the picks", order.join(",") === "advisor,apply", order);
    const b = P.st.posts[0] || {};
    T.check("...and posts the picks exactly as before", b.primary === M27B && b.background === M9B && b.download === false &&
      !("ack_uncensored" in b), b);
    T.check("...then moves on", P.key() === "prefs");
  });

  await scenario("the advisor's err keeps the step", async function () {
    const adv = advisorStub({ saveReply: { err: "uses: 'uncensored' needs the acknowledgement" } });
    const P = await page({ runtime: false, pre: function (w) { w.hermesAdvisor = adv; } });
    await P.open(false);
    P.ob.go(1);
    P.st.posts.length = 0;
    await P.click("later");
    T.check("an advisor save() {err} stops the step: nothing posted to apply, still on models",
      P.st.posts.length === 0 && P.key() === "models", [P.st.posts, P.key()]);
    T.check("...the error is shown", /needs the acknowledgement/.test(P.q(".onb-err").textContent));
    adv.rec.saveReply = {};
    await P.click("later");
    T.check("'later' with a clean save: the answers saved, still nothing posted, moves on",
      P.st.posts.length === 0 && P.key() === "prefs" && adv.rec.saves === 2);

    const adv2 = advisorStub({ throwSave: true });
    const P2 = await page({ runtime: false, state: state({ downloaded: [M27B, M9B] }), pre: function (w) { w.hermesAdvisor = adv2; } });
    await P2.open(false);
    P2.ob.go(1);
    await P2.click("next");
    T.check("an advisor save() that throws never blocks onboarding (advisory)",
      P2.key() === "prefs" && P2.st.posts.length >= 1 && P2.st.posts[P2.st.posts.length - 1].primary === M27B);
  });

  await scenario("uncensored picks carry the acknowledgement", async function () {
    const adv = advisorStub();
    const P = await page({ runtime: false, state: state({ downloaded: [UNC, M9B] }), pre: function (w) { w.hermesAdvisor = adv; } });
    await P.open(false);
    P.ob.go(1);
    adv.rec.wires[0].ctx.setPick({ primary: UNC, background: M9B, uncensored: true });
    P.st.posts.length = 0;
    await P.click("next");
    T.check("setPick({uncensored:true}): apply gets ack_uncensored: true",
      P.st.posts.length === 1 && P.st.posts[0].primary === UNC && P.st.posts[0].ack_uncensored === true, P.st.posts);

    const P2 = await page({ runtime: false, state: state(), pre: function (w) { w.hermesAdvisor = advisorStub(); } });
    await P2.open(false);
    P2.ob.go(1);
    P2.w.hermesAdvisor.rec.wires[0].ctx.setPick({ primary: UNC, background: null, uncensored: "yes", download_gb: 17 });
    P2.st.posts.length = 0;
    await P2.click("download");
    T.check("only a strict true counts: no ack sent for 'yes'", P2.st.posts[0] && !("ack_uncensored" in P2.st.posts[0]), P2.st.posts);
    adv.rec.wires.length = 0;

    const P3 = await page({ runtime: false, state: state({ downloaded: [UNC, M9B] }), pre: function (w) { w.hermesAdvisor = advisorStub(); },
      reply: function (b) {
        return { __status: 409, body: { ok: false, needs_ack: true, error: "uncensored_ack", id: b.primary,
          errors: ["Qwen3.8-27B Uncensored is an uncensored model. Acknowledge it first."], applied: {} } };
      } });
    await P3.open(false);
    P3.ob.go(1);
    P3.w.hermesAdvisor.rec.wires[0].ctx.setPick({ primary: UNC, background: M9B });
    await P3.click("next");
    T.check("apply's 409 needs_ack keeps the sheet on the models step", P3.key() === "models", P3.key());
    T.check("...with apply's own words in the error line",
      /is an uncensored model\. Acknowledge it first\./.test(P3.q(".onb-err").textContent), P3.q(".onb-err").textContent);

    const P4 = await page({ runtime: false, state: state(), pre: function (w) { w.hermesAdvisor = advisorStub(); } });
    await P4.open(false);
    P4.ob.go(1);
    P4.w.hermesAdvisor.rec.wires[0].ctx.setPick({ primary: UNC, background: null, uncensored: true, download_gb: 17 });
    P4.st.posts.length = 0;
    await P4.click("download");
    T.check("Download carries ack_uncensored: true too", P4.st.posts[0] && P4.st.posts[0].download === true &&
      P4.st.posts[0].ack_uncensored === true, P4.st.posts);
  });

  await scenario("legacy fallback", async function () {
    const P = await page({ runtime: false });
    await P.open(false);
    P.ob.go(1);
    T.check("no hermesAdvisor: the legacy body, unchanged", !!P.q(".onb-rec") && !P.q(".onb-adv") &&
      P.go().textContent === "Download recommended" && /Qwen3\.8-27B/.test(P.q(".onb-rec").textContent));
    const P2 = await page({ runtime: false, pre: function (w) { w.hermesAdvisor = advisorStub({ throwRender: true }); } });
    await P2.open(false);
    P2.ob.go(1);
    T.check("an advisor whose render throws: the legacy body", !!P2.q(".onb-rec") && !P2.q(".onb-adv"));
    T.check("...and its wire/save are not used", P2.w.hermesAdvisor.rec.wires.length === 0);
    await P2.click("later");
    T.check("...nor its save() on Next", P2.w.hermesAdvisor.rec.saves === 0 && P2.key() === "prefs");
    for (const bad of [42, "", "   ", null]) {
      const P3 = await page({ runtime: false, pre: function (w) { w.hermesAdvisor = advisorStub({ html: bad }); } });
      await P3.open(false);
      P3.ob.go(1);
      T.check("an advisor render returning " + JSON.stringify(bad) + ": the legacy body", !!P3.q(".onb-rec"));
    }
    const P4 = await page({ runtime: false, pre: function (w) { w.hermesAdvisor = { v: 1 }; } });
    await P4.open(false);
    P4.ob.go(1);
    T.check("an advisor without a step: the legacy body", !!P4.q(".onb-rec"));
  });

  await scenario("download progress repaints only its own block", async function () {
    const adv = advisorStub();
    const P = await page({ runtime: false, pre: function (w) { w.hermesAdvisor = adv; } });
    await P.open(false);
    P.ob.go(1);
    adv.rec.wires[0].ctx.setPick({ primary: M27B, background: M9B, download_gb: 23 });
    await P.click("download");
    const body = P.q(".onb-adv"), renders = adv.rec.renders;
    T.check("Download posts the picks with download:true", P.st.posts.some(function (b) {
      return b.download === true && b.primary === M27B && b.background === M9B; }));
    T.check("...the progress block appears under the advisor body", /Downloading/.test(P.q(".onb-dl").textContent) &&
      /Qwen3\.8-27B/.test(P.q(".onb-dl").textContent));
    T.check("...without re-rendering the advisor body", P.q(".onb-adv") === body && adv.rec.renders === renders);
    P.st.models = { models: [{ id: M27B, downloading: true, downloaded: false }, { id: M9B, downloaded: true }] };
    P.w.__intervals.slice().forEach(function (t) { t.fn(); });
    await P.w.__tick(10);
    T.check("the poll updates the progress lines", /downloading…/.test(P.q(".onb-dl").textContent) &&
      /downloaded/.test(P.q(".onb-dl").textContent), P.q(".onb-dl").textContent);
    T.check("...and still never re-renders the advisor body", P.q(".onb-adv") === body && adv.rec.renders === renders, adv.rec.renders);
    T.check("...the action row says Continue (this download carries the picks)", P.go().textContent === "Continue");
  });
}

// ===========================================================================
async function motion() {
  console.log("--- motion (§8.9) ---");

  await scenario("open and step changes", async function () {
    const P = await page({});
    P.L.clear();
    await P.open(false);
    const open = P.L.of("gsap", "tl.from").filter(function (c) { return c.t === P.sheet(); });
    T.check("opening plays ONE sequence on the sheet: opacity 0 / scale .98 over dur.pop",
      open.length === 1 && open[0].a.opacity === 0 && open[0].a.scale === 0.98 && open[0].a.duration === 0.32, open.map(function (c) { return c.a; }));
    T.check("...and nothing else on the first paint (no step entrance, no words)",
      P.L.of("anime", "splitText").length === 0 && P.L.of("gsap", "tl.from").length === 1);
    P.L.finishAll(); await P.w.__tick(); P.L.clear();

    await P.click("next");                         // welcome -> models (forward)
    const col = P.q(".onb-col"), dot = P.qa(".onb-rail li")[1], h1 = P.q("h1.onb-t");
    const tw = P.L.of("gsap", "tl.from");
    const c1 = tw.find(function (c) { return c.t === col; });
    const c2 = tw.find(function (c) { return c.t === dot; });
    T.check("a step change: the column slides in from +24 px with opacity 0 over .32",
      c1 && c1.a.x === 24 && c1.a.opacity === 0 && c1.a.duration === 0.32, c1 && c1.a);
    T.check("...the NEW rail dot pops from scale .6 over .38 on hermes.gel, at the start",
      c2 && c2.a.scale === 0.6 && c2.a.duration === 0.38 && c2.a.ease === "hermes.gel" && c2.pos === 0, c2 && [c2.a, c2.pos]);
    T.check("...only transform and opacity", tw.every(function (c) {
      return Object.keys(c.a).every(function (k) { return /^(x|y|scale|opacity|duration|ease|delay|clearProps|__n)$/.test(k); }); }),
      tw.map(function (c) { return Object.keys(c.a); }));
    const sp = P.L.of("anime", "splitText");
    T.check("...and the step heading's words rise (words(h1, {delay:.06}))", sp.length === 1 && sp[0].el === h1);
    const wa = P.L.of("anime", "animate").find(function (c) { return Array.isArray(c.targets); });
    T.check("...with the .06 s delay", wa && wa.params && wa.params.delay && wa.params.delay(null, 0) === 60, wa && wa.params);
    P.L.finishAll(); await P.w.__tick(); P.L.clear();

    await P.click("back");                         // models -> welcome (back)
    const b1 = P.L.of("gsap", "tl.from").find(function (c) { return c.t === P.q(".onb-col"); });
    T.check("going back slides in from -24 px", b1 && b1.a.x === -24, b1 && b1.a);
    T.check("...and the dot that pops is the step now current", P.L.of("gsap", "tl.from").some(function (c) {
      return c.t === P.qa(".onb-rail li")[0]; }));
    P.L.finishAll(); await P.w.__tick(); P.L.clear();
    P.ob.go(2);
    T.check("a jump (go) is a step change too", P.L.of("gsap", "tl.from").some(function (c) { return c.t === P.q(".onb-col") && c.a.x === 24; }));
    P.L.finishAll(); await P.w.__tick();
  });

  await scenario("no motion on a same-step repaint (E12)", async function () {
    const adv = advisorStub();
    const P = await page({ pre: function (w) { w.hermesAdvisor = adv; } });
    await P.open(false);
    P.L.finishAll(); await P.w.__tick(); P.L.clear();
    const started = P.w.HermesAnim.stats().started;
    // a pref toggle on the welcome table repaints the whole step
    const sw = P.q('input[data-pref="news"]');
    sw.checked = false; sw.onchange();
    T.check("a toggle repainted the step...", P.ob.state.prefs.news === false && P.q('input[data-pref="news"]') !== sw);
    T.check("...with ZERO animation calls", P.moves().length === 0 && P.w.HermesAnim.stats().started === started, P.moves().map(function (c) { return c.fn; }));
    P.ob.go(0);
    T.check("go() to the step already shown is not a change either", P.moves().length === 0);
    // the busy label of a save
    P.ob.go(2); P.L.finishAll(); await P.w.__tick(); P.L.clear();
    const seg = P.q('input[data-seg="idle"][value="20"]');
    seg.checked = true; seg.onchange();
    T.check("the idle radio repaints with zero animation calls", P.moves().length === 0 && P.ob.state.prefs.idle_min === 20);
    // the models step: setPick, the download and its 2.5 s poll
    P.ob.go(1); P.L.finishAll(); await P.w.__tick(); P.L.clear();
    adv.rec.wires[adv.rec.wires.length - 1].ctx.setPick({ primary: M27B, background: M9B, download_gb: 23 });
    T.check("setPick repaints the action row with zero animation calls", P.moves().length === 0);
    await P.click("download");
    P.st.models = { models: [{ id: M27B, downloading: true }, { id: M9B, downloading: true }] };
    for (let i = 0; i < 3; i++) { P.w.__intervals.slice().forEach(function (t) { t.fn(); }); await P.w.__tick(6); }
    T.check("the download and three poll ticks make zero animation calls", P.moves().length === 0, P.moves().map(function (c) { return c.fn; }));
    T.check("the CSS keyframe entrance is gone from the file (it replayed on every repaint)",
      ONB_SRC.indexOf("onb-in") < 0 && !/@keyframes/.test(P.q("style#onb-css").textContent));
    T.check("...the reduced-motion block for the toggles stays", /prefers-reduced-motion:reduce/.test(P.q("style#onb-css").textContent));
  });

  await scenario("the Ready step counts its figures", async function () {
    const P = await page({});
    await P.open(false);
    P.ob.go(2); P.L.finishAll(); await P.w.__tick(); P.L.clear();
    await P.click("next");                         // prefs -> done
    const figs = P.qa(".onb-sum .onb-fig");
    T.check("the figures shown are marked ('10' minutes)", figs.length >= 1 && figs[0].getAttribute("data-fig") === "10" &&
      figs[0].textContent === "10", figs.map(function (f) { return f.outerHTML; }));
    const counts = P.L.of("anime", "animate").filter(function (c) { return c.params && c.params.v === 10; });
    T.check("count() runs on them on arrival (from 0)", counts.length === 1 && counts[0].targets.v === 0, counts.length);
    P.L.finishAll(); await P.w.__tick();
    T.check("...ending on the real figure", P.qa(".onb-sum .onb-fig")[0].textContent === "10");
    P.L.clear();
    P.ob.state.busy = "finish"; P.ob.go(P.ob.steps().length - 1);
    T.check("a repaint of the Ready step does not count again", P.L.of("anime", "animate").length === 0);
    P.ob.state.busy = "";
    const figVals = P.qa(".onb-sum .onb-fig").map(function (f) { return f.getAttribute("data-fig"); });
    T.check("model names and times are never figures (only '<n> minutes' / '<n> GB')",
      figVals.join(",") === "10" && /quiet hours 22:00 to 07:00/.test(P.q("ul.onb-sum").textContent), figVals);
    T.check("figLine marks '~23 GB' and '10 minutes', never 'Qwen3.8-27B' or '22:00'",
      (function () {
        P.ob.state.applied = ["Downloading ~23 GB in the background", "Assistant model: Qwen3.8-27B (3.5 GB)",
                              "quiet hours 22:00 to 07:00", "sleeps after 10 minutes"];
        P.ob.go(P.ob.steps().length - 1);
        return P.qa(".onb-sum .onb-fig").map(function (f) { return f.getAttribute("data-fig"); }).join(",") === "23,3.5,10";
      })(), P.qa(".onb-sum .onb-fig").map(function (f) { return f.getAttribute("data-fig"); }));
  });

  await scenario("reduce switches and no runtime: end state at once", async function () {
    for (const mode of ["data-reduce", "OS"]) {
      const P = await page(mode === "OS" ? { media: OS_REDUCE } : { reduce: true });
      P.L.clear();
      await P.open(false);
      await P.click("next");
      await P.click("back");
      P.ob.go(2);
      await P.click("next");
      T.check(mode + ": open + step changes make no library call", P.moves().length === 0, P.moves().map(function (c) { return c.fn; }));
      T.check(mode + ": ...the runtime counted them as instant (reduced)", P.w.HermesAnim.stats().instant.reduced >= 3,
        P.w.HermesAnim.stats().instant);
      T.check(mode + ": ...the Ready figures read their final value", P.qa(".onb-sum .onb-fig")[0] &&
        P.qa(".onb-sum .onb-fig")[0].textContent === "10");
      T.check(mode + ": ...and nothing is left inline on the column", !P.q(".onb-col").getAttribute("style"));
    }
    const P = await page({ runtime: false });
    await P.open(false);
    await P.click("next");
    P.ob.go(3);
    T.check("no motion runtime at all: the sheet still opens, moves and reaches Ready",
      P.key() === "done" && /Here is what was set/.test(P.text()) && P.w.__errors.length === 0, P.w.__errors.map(String));
  });

  await scenario("inside the real index.html (HA())", async function () {
    let L = null;
    const st = state({ done: false });
    const w = await bootDashboard({
      routes: {
        "/api/onboarding/state": st, "/api/claude/foryou": { enabled: false },
        "/api/onboarding/apply": function (u, i) { return { ok: true, errors: [], applied: JSON.parse(i.body || "{}") }; }
      },
      aux: ["aux_onboarding.js"],
      before: function (win) { L = STUBS.install(win, { gsap: true, anime: true }); win.__run(ANIM_SRC, "aux_anim.js"); }
    });
    await w.__tick(12);
    const sheet = w.document.getElementById("onb-sheet");
    T.check("a first run auto-opens the sheet in the real page", sheet && !sheet.hidden);
    T.check("...its opening went through HA() (one sequence on the sheet)",
      L.of("gsap", "tl.from").filter(function (c) { return c.t === sheet; }).length === 1);
    L.finishAll(); await w.__tick(); L.clear();
    w.__click(sheet.querySelector(".onb-go"));
    await w.__tick(12);
    T.check("...and Next animates the step through it", L.of("gsap", "tl.from").some(function (c) {
      return c.t === sheet.querySelector(".onb-col"); }));
    T.check("...without a page error from aux_onboarding.js",
      !w.__errors.some(function (e) { return /aux_onboarding/.test(String(e && e.stack)); }), w.__errors.map(String).slice(0, 3));
  });
}

(async function () {
  await hook();
  await models();
  await motion();
  T.done();
})().catch(function (e) { T.check("harness ran: " + (e && e.stack || e), false); T.done(); });
