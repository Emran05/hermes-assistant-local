// Regression test for R2-43 (round 2, w9) and the two cross-cluster findings on
// the Data & Network table (X-prompts-egress-2, X-frontend-1).
//
// BEFORE: NETWORK_FACTS was written against the pre-merge code, and the merge
// changed what it describes. The feeds row said "About every hour, whether or
// not news alerts are on" with no switch, but News off now stops the gather
// (C48). The web-search row said an unattended pass runs hourly and a test
// search every 6 hours "model or not", with no switch, but both now run only
// while Agent web research (default OFF) and News are on (C50). The models row
// said every model-server start checks Hugging Face, but a complete snapshot
// now starts offline (C49). The widgets row said "whether or not a window is
// open", but the Hub refreshes only while someone looks (C53), and weather is
// also fetched for the header chip and the brief. The For-You row said
// "hourly" (FY_INTERVAL is 2 h). The Settings card could not show or flip the
// web research switch at all.
//
// AFTER: those rows are re-measured; the feeds row carries the News switch and
// a new "Web search, unattended" row carries `web_research` (def:false), read
// from GET /api/watchtower and written with op set_web_research — in the
// Settings card (aux_network.js) and the first-run sheet (aux_onboarding.js).
//
// Part 1 is the CROSS-CHECK the finding asked for: it reads the gates in
// aux_watchtower.py / mlx-server*.sh / server.py / aux_topbar.py /
// aux_foryou.py and asserts the rows say what that code does, so the table
// cannot silently drift from the watchtower again. Part 2 runs the REAL
// aux_onboarding.js + aux_network.js in node:vm with fetch mocked.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const read = (rel) => fs.readFileSync(path.join(REPO, rel), "utf8");
const fails = [];
let checks = 0;
function check(name, cond, extra) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : "  " + JSON.stringify(extra === undefined ? "" : extra)));
  if (!cond) fails.push(name);
}

// a top-level python def, from its line to the next top-level statement
function pyFunc(src, name) {
  const m = new RegExp("^def " + name + "\\(", "m").exec(src);
  if (!m) return "";
  const rest = src.slice(m.index + 1);
  const end = rest.search(/\n(?=[^\s#\n])/);
  return end < 0 ? src.slice(m.index) : src.slice(m.index, m.index + 1 + end);
}

function extract(src, header) {
  const at = src.indexOf(header);
  if (at < 0) return null;
  let i = src.indexOf("{", at), depth = 0;
  for (; i < src.length; i++) {
    const c = src[i];
    if (c === '"' || c === "'" || c === "`") {
      for (i++; i < src.length && src[i] !== c; i++) if (src[i] === "\\") i++;
      continue;
    }
    if (c === "/" && src[i + 1] === "/") { i = src.indexOf("\n", i); continue; }
    if (c === "/" && src[i + 1] === "*") { i = src.indexOf("*/", i) + 1; continue; }
    if (c === "{") depth++;
    if (c === "}" && --depth === 0) return src.slice(at, i + 1);
  }
  return null;
}

function makeCtx(extra) {
  const ctx = Object.assign({ console, setTimeout, clearTimeout, setInterval, clearInterval,
    Promise, JSON, Date, Math, Number, String, Array, Object, Boolean, isNaN,
    fetch: () => Promise.resolve({ json: () => Promise.resolve(null) }) }, extra || {});
  ctx.window = ctx; ctx.globalThis = ctx;
  return vm.createContext(ctx);
}

(async () => {
  const ctx = makeCtx();
  vm.runInContext(read("dashboard/aux_onboarding.js"), ctx, { filename: "aux_onboarding.js" });
  const ob = ctx.hermesOnboarding;
  const by = {};
  ob.NETWORK_FACTS.forEach((f) => { by[f.id] = f; });
  const txt = (f) => (f ? [f.what, f.when, f.data, f.off || ""].join(" ") : "");

  // ======================================================================
  console.log("== part 1: the rows say what the gating code does ==");
  const wt = read("dashboard/aux_watchtower.py");
  const gather = pyFunc(wt, "_intel_gather");
  const agent = pyFunc(wt, "_intel_agent_pass");
  check("aux_watchtower.py: _intel_gather and _intel_agent_pass found",
        gather.length > 0 && agent.length > 0);

  const feedsGated = /if not _wt_news_on\(/.test(gather);
  check("feeds row's switch matches _intel_gather's News gate (C48)",
        feedsGated === !!(by.feeds && by.feeds.toggle && by.feeds.toggle.key === "news"),
        { feedsGated, toggle: by.feeds && by.feeds.toggle });
  check("...and its text agrees (gated -> 'while News is on', never 'whether or not')",
        feedsGated ? /while News is on/.test(txt(by.feeds)) &&
                     !/whether or not/.test(txt(by.feeds))
                   : !/while News is on/.test(txt(by.feeds)), by.feeds && by.feeds.when);

  const researchGated = /web_research/.test(agent) && /enabled/.test(agent) &&
                        /_wt_news_on\(/.test(agent);
  const probeAt = agent.indexOf("_intel_web_ok(");
  const gateAt = agent.search(/web_research/);
  check("_intel_agent_pass checks web_research + News BEFORE the probe search",
        researchGated && gateAt >= 0 && probeAt > gateAt, { gateAt, probeAt });
  check("...so the unattended row carries web_research, off by default (C50)",
        researchGated === !!(by.webresearch && by.webresearch.toggle &&
                             by.webresearch.toggle.key === "web_research" &&
                             by.webresearch.toggle.def === false), by.webresearch);
  check("...and says it needs News on too",
        /News is on/.test(txt(by.webresearch)), by.webresearch && by.webresearch.when);
  const probeCallers = (wt.match(/(?<!def )_intel_web_ok\(\)/g) || []).length;
  check("_intel_web_ok is called only from _intel_agent_pass",
        probeCallers === 1 && probeAt >= 0, probeCallers);
  check("...so the 6-hourly test search sits on the switched row, not the chat one",
        /6 hours/.test(txt(by.webresearch)) && !/6 hours/.test(txt(by.websearch)) &&
        !(by.websearch && by.websearch.toggle), [by.websearch, by.webresearch]);
  check("watchtower has op set_web_research (the switch the card writes)",
        /"set_web_research":\s*_op_set_web_research/.test(wt) &&
        /def _op_set_web_research\(/.test(wt));

  const prewarm = pyFunc(wt, "_prewarm");
  const tick = pyFunc(wt, "_brief_tick");
  const fetchesMarkets = /_provider\("markets"\)|w_crypto/.test(prewarm);
  check("_brief_tick returns on the briefings master before _prewarm()",
        tick.search(/_master_on\(cfg, "briefings"\)/) >= 0 &&
        tick.search(/_master_on\(cfg, "briefings"\)/) < tick.indexOf("_prewarm()"));
  check("briefings row discloses the pre-brief Yahoo/CoinGecko refresh iff _prewarm does it",
        fetchesMarkets === (/Yahoo Finance/.test(txt(by.briefings)) &&
                            /CoinGecko/.test(txt(by.briefings))),
        { fetchesMarkets, data: by.briefings && by.briefings.data });

  const fg = read("mlx-server.sh"), bg = read("mlx-server-bg.sh");
  const offline = [fg, bg].every((s) => /--local-ready/.test(s) && /HF_HUB_OFFLINE=1/.test(s));
  check("both model launchers start a complete snapshot offline (C49)", offline);
  check("...so the models row no longer says every start checks Hugging Face",
        offline === (/offline/.test(txt(by.models)) &&
                     !/each time a model server starts/.test(txt(by.models))), by.models);

  const srv = read("dashboard/server.py");
  const hubLoop = pyFunc(srv, "hub_prewarm_loop");
  const onlyWhileLooking = /_hub_client_at/.test(hubLoop);
  check("hub_prewarm_loop refreshes only while the Hub is polled (C53)", onlyWhileLooking);
  check("...so the widgets row no longer says 'whether or not a window is open'",
        onlyWhileLooking === !/whether or not a window is open/.test(txt(by.widgets)),
        by.widgets && by.widgets.when);
  const topbarWeather = /globals\(\)\.get\("weather"\)/.test(read("dashboard/aux_topbar.py"));
  const briefWeather = /weather/.test(pyFunc(wt, "_brief_lookahead"));
  check("the header chip (aux_topbar.py) and the brief's look-ahead call weather()",
        topbarWeather && briefWeather);
  check("...and the widgets row names both, and how to stop Open-Meteo",
        /header/.test(txt(by.widgets)) && /look-ahead/.test(txt(by.widgets)) &&
        /clear the city/.test(txt(by.widgets)), by.widgets);

  const fy = /^FY_INTERVAL\s*=\s*(\d+)\s*\*\s*3600/m.exec(read("dashboard/aux_foryou.py"));
  const hrs = fy ? Number(fy[1]) : 0;
  check("For-You via Claude says its real cadence (FY_INTERVAL = " + hrs + " h)",
        hrs > 0 && new RegExp("every " + hrs + " hours").test(txt(by.foryou_claude)) &&
        /new news/.test(txt(by.foryou_claude)), by.foryou_claude && by.foryou_claude.when);

  // ======================================================================
  console.log("== part 2: the switch is live in the table, the card and the sheet ==");
  const inputs = (h, key) => (h.match(new RegExp('<input[^>]*data-pref="' + key + '"[^>]*>', "g")) || []);
  let html = ob.networkTableHTML({ prefs: { news: true, web_research: false }, inputs: true });
  check("the News switch appears on BOTH rows it stops (feeds + alerts)",
        inputs(html, "news").length === 2 && inputs(html, "news").every((x) => / checked/.test(x)),
        inputs(html, "news"));
  check("web_research renders a live switch, Off for false",
        inputs(html, "web_research").length === 1 && !/ checked/.test(inputs(html, "web_research")[0]));
  html = ob.networkTableHTML({ prefs: { web_research: "true" }, inputs: true });
  check("...and Off for anything but an explicit true (def:false)",
        !/ checked/.test(inputs(html, "web_research")[0] || ""));
  html = ob.networkTableHTML({ prefs: { web_research: true }, inputs: true });
  check("...On for true", / checked/.test(inputs(html, "web_research")[0] || ""));

  // ---- the Settings card -------------------------------------------------
  const CALLS = [];
  let WT = { ok: true, master: { briefings: true, news: true }, web_research: { enabled: true } };
  let POSTREPLY = null;
  const fakeFetch = (url, opts) => {
    const u = url.split("?")[0];
    let body = null;
    try { body = opts && opts.body ? JSON.parse(opts.body) : null; } catch (e) {}
    CALLS.push({ url: u, method: (opts && opts.method) || "GET", body });
    let res = null;
    if (opts && opts.method === "POST") res = POSTREPLY ? POSTREPLY(u, body) : { ok: true };
    else if (u === "/api/watchtower") res = WT;
    else if (u === "/api/claude/foryou") res = { ok: true, enabled: false };
    else if (u === "/api/claude/escalate") res = { ok: true, enabled: false };
    else if (u === "/api/network/last") res = { ok: true, last: {} };
    return Promise.resolve({ ok: true, json: () => Promise.resolve(res) });
  };
  const mkInput = () => {
    const lbl = { textContent: "" };
    const inp = { checked: false, parentNode: null };
    inp.parentNode = { classList: { add() {}, remove() {} }, querySelector: () => lbl };
    inp.lbl = lbl;
    return inp;
  };
  const NEWS = [mkInput(), mkInput()], WR = [mkInput()];
  const pick = (sel) => (sel.indexOf('data-pref="news"') >= 0 ? NEWS
    : sel.indexOf('data-pref="web_research"') >= 0 ? WR : []);
  const doc = {
    getElementById: () => null, createElement: () => ({}),
    querySelector: (sel) => pick(sel)[0] || null,
    querySelectorAll: (sel) => pick(sel)
  };
  const nctx = makeCtx({ fetch: fakeFetch, document: doc });
  nctx.hermesOnboarding = ob;
  vm.runInContext(read("dashboard/aux_network.js"), nctx, { filename: "aux_network.js" });
  const net = nctx.hermesNetwork;
  await net.refresh();
  check("the card reads web_research from GET /api/watchtower",
        net.state.prefs.web_research === true, net.state.prefs);
  WT = { ok: true, master: { briefings: true, news: true }, web_research: { enabled: "yes" } };
  await net.refresh();
  check("...and reads anything but an explicit true as Off",
        net.state.prefs.web_research === false, net.state.prefs);

  CALLS.length = 0;
  POSTREPLY = (u, b) => ({ ok: true, web_research: { enabled: b.enabled === true } });
  await net.flip("web_research", true, "Agent web research");
  const p = CALLS.find((c) => c.method === "POST");
  check("flipping it POSTs /api/watchtower op set_web_research with a JSON boolean",
        !!(p && p.url === "/api/watchtower" && p.body.op === "set_web_research" &&
           p.body.enabled === true), p);
  check("...and the card follows", net.state.prefs.web_research === true &&
        WR[0].checked === true && WR[0].lbl.textContent === "On", net.state.prefs);
  POSTREPLY = () => ({ ok: true, web_research: { enabled: true } });   // did not take
  await net.flip("web_research", false, "Agent web research");
  check("a write that did not land reverts the switch",
        net.state.prefs.web_research === true && WR[0].checked === true, net.state.prefs);

  POSTREPLY = () => ({ ok: true, master: { news: false } });
  NEWS[0].checked = false;                     // the one the owner clicked
  NEWS[1].checked = true;
  await net.flip("news", false, "News");
  check("flipping News settles BOTH rows that carry it",
        NEWS.every((x) => x.checked === false && x.lbl.textContent === "Off"),
        NEWS.map((x) => [x.checked, x.lbl.textContent]));

  // ---- the first-run sheet ------------------------------------------------
  const src = read("dashboard/aux_onboarding.js");
  const fns = ["async function saveStep()", "function summarize()"].map((h) => extract(src, h));
  check("saveStep() and summarize() found", fns.every(Boolean));
  // round 3: saveStep() asks which step it is on (an extra step saves itself)
  // and summarize() is each step's own summary: their helpers ride along
  ["function liveSteps()", "function curStep()", "function sumModels()", "function sumPrefs()",
   "function sumWelcome()", "function extLines(st)", "function fitsById()", "function labelOf(id)",
   "function gb(n)"].forEach((h) => { const b = extract(src, h); if (b) fns.push(b); });
  const sctx = makeCtx({ POSTS: [], S: null,
                         STEPS: [{ key: "welcome" }, { key: "models" }, { key: "prefs" }, { key: "done" }] });
  vm.runInContext(
    "function stepKey() { return (STEPS[S.step] || STEPS[0]).key; }\n" +
    "function paint() {}\nfunction currentTheme() { return 'system'; }\n" +
    "function paintFoot() {}\nfunction warnOnce() {}\nfunction hookCtx() { return {}; }\n" +
    "function setErr(m) { S.err = m == null ? '' : String(m); }\n" +
    "async function jpost(url, body) { POSTS.push(body); return { ok: true, applied: body }; }\n" +
    fns.join("\n") + "\nthis.saveStep = saveStep; this.summarize = summarize;",
    sctx, { filename: "aux_onboarding.js" });
  for (const on of [true, false]) {
    sctx.POSTS.length = 0;
    sctx.S = { step: 0, prefs: { briefings: true, news: true, web_research: on },
               data: { detect: {}, recommendation: {} }, pick: {} };
    await sctx.saveStep();
    check("the sheet's step 1 sends web_research: " + on,
          sctx.POSTS.length === 1 && sctx.POSTS[0].web_research === on, sctx.POSTS);
  }
  sctx.S.prefs.web_research = "true";
  sctx.POSTS.length = 0;
  await sctx.saveStep();
  check("...and never a non-boolean", sctx.POSTS[0] && sctx.POSTS[0].web_research === false,
        sctx.POSTS);
  sctx.S.prefs.web_research = true;
  check("the summary says whether agent web research is on",
        (sctx.summarize() || []).indexOf("Agent web research: on") >= 0, sctx.summarize());
  check("the sheet seeds web_research from the state's prefs (explicit true only)",
        /web_research: p\.web_research === true/.test(extract(src, "async function load()") || ""));
})().catch((e) => {
  check("harness ran: " + (e && e.stack || e), false);
}).then(() => {
  console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
});
