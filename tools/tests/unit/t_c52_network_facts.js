// C52 (docs/audits/2026-10-21-codebase-audit.md) — "What leaves this Mac".
//
// BEFORE: NETWORK_FACTS had five rows ("Five things ever reach the internet")
// while the code talked to Hugging Face on every model start, ~22 news feeds
// hourly whatever the news switch said, web-search engines, the always-on
// Telegram gateway, Microsoft's Edge voices (the agent's TTS default) and
// Apple's speech-model server; the news switch sat on the feeds row it does
// not stop; the update row said "No identity" while the check signs in with a
// GitHub token; and "Last outbound" for feeds was the newest ALERT.
//
// AFTER: one row per destination, measured against the code; the news switch
// sits on the Telegram alerts row; counts come from the list; Last outbound
// comes from /api/network/last and says "not tracked" where the dashboard
// cannot see. The REAL aux_onboarding.js and aux_network.js run in node:vm.
//
// R2-43 (round 2) re-measured three rows after the merge, and this suite now
// asserts the merged behaviour: News off stops the feed gather (C48), so the
// feeds row carries the News switch; the unattended research pass and its
// 6-hourly test search have their own off-by-default switch (C50) on a row of
// their own; a fully downloaded model starts offline (C49).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}
function makeCtx() {
  const ctx = { console, setTimeout, clearTimeout, Promise, JSON, Date, Math, Number,
                String, Array, Object, Boolean, isNaN,
                fetch: () => Promise.resolve({ json: () => Promise.resolve(null) }) };
  ctx.window = ctx; ctx.globalThis = ctx;
  return vm.createContext(ctx);
}
function load(ctx, rel) {
  const file = path.join(REPO, rel);
  vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
}

try {
  const ctx = makeCtx();
  load(ctx, "dashboard/aux_onboarding.js");
  const ob = ctx.hermesOnboarding;
  const F = ob.NETWORK_FACTS;
  const by = {};
  F.forEach((f) => { by[f.id] = f; });
  const txt = (f) => f ? [f.what, f.when, f.data].join(" ") : "";

  console.log("== every destination the code talks to has a row ==");
  check("Hugging Face: downloads, and a start whose files are still missing (C49)",
        /Hugging Face/.test(txt(by.models)) && /download/.test(txt(by.models)) &&
        /missing/.test(txt(by.models)) && /offline/.test(txt(by.models)) &&
        !/each time a model server starts/.test(txt(by.models)), by.models);
  check("News sites: hourly, only while News is on (C48)",
        /hour/.test(txt(by.feeds)) && /while News is on/.test(txt(by.feeds)) &&
        !/whether or not news alerts are on/.test(txt(by.feeds)), by.feeds);
  check("...and the feeds row carries the News switch that now stops it",
        by.feeds && by.feeds.toggle && by.feeds.toggle.key === "news",
        by.feeds && by.feeds.toggle);
  check("Hub widget sources have their own row", /Open-Meteo/.test(txt(by.widgets)),
        by.widgets);
  check("Web search engines: the agent's own tool when you ask (no switch)",
        /DuckDuckGo/.test(txt(by.websearch)) && !by.websearch.toggle, by.websearch);
  // review C52: _intel_web_ok() (aux_watchtower.py) runs a real "AI news"
  // search every 6 hours whatever the model is doing; since C50 only while
  // Agent web research (off by default) and News are on — its own row
  check("the unattended research pass + 6-hourly test search have their own row",
        /unattended/.test(txt(by.webresearch)) && /6 hours/.test(txt(by.webresearch)) &&
        /AI news/.test(txt(by.webresearch)) && /model or not/.test(txt(by.webresearch)),
        by.webresearch);
  check("...behind the off-by-default Agent web research switch (C50)",
        by.webresearch && by.webresearch.toggle &&
        by.webresearch.toggle.key === "web_research" && by.webresearch.toggle.def === false,
        by.webresearch && by.webresearch.toggle);
  check("Telegram: the gateway is connected around the clock",
        /Around the clock/.test(txt(by.telegram)) && !by.telegram.toggle, by.telegram);
  check("Telegram briefings say what a brief contains",
        /calendar/.test(txt(by.briefings)) && /tasks/.test(txt(by.briefings)) &&
        by.briefings.toggle && by.briefings.toggle.key === "briefings", by.briefings);
  check("the news switch now sits on the Telegram alerts row",
        by.alerts && by.alerts.toggle && by.alerts.toggle.key === "news", by.alerts);
  check("Microsoft Edge voices: the agent's text-to-speech default",
        /Edge/.test(txt(by.tts)) && /text-to-speech/.test(txt(by.tts)), by.tts);
  check("Apple: the on-device speech model download on first dictation",
        /Apple/.test(txt(by.dictation)) && /dictate/.test(txt(by.dictation)), by.dictation);
  check("For-You's Claude tier and Google keep their rows",
        !!(by.foryou_claude && by.google && by.claude));
  check("the update row no longer claims 'No identity'",
        by.updates && !/No identity/.test(by.updates.data) && /token/.test(by.updates.data),
        by.updates && by.updates.data);
  const ids = F.map((f) => f.id);
  check("row ids are unique", new Set(ids).size === ids.length, ids);

  console.log("== the Switch column says how to stop a row without a switch ==");
  const html = ob.networkTableHTML({ prefs: {}, inputs: true });
  check("'Remove the widget' is shown for the widget sources",
        /Remove the widget/.test(html));
  check("'hermes gateway stop' is shown for the gateway", /hermes gateway stop/.test(html));
  check("rows with neither still read 'always'", /onb-net-always">always</.test(html));
  check("one body row per fact",
        (html.match(/<tr><th scope="row">/g) || []).length === F.length);

  console.log("== the card ==");
  const nctx = makeCtx();
  nctx.hermesOnboarding = ob;
  load(nctx, "dashboard/aux_network.js");
  const net = nctx.hermesNetwork;
  const card = net.cardHTML({ prefs: {}, last: null });
  check("the lede no longer says 'Five things'", !/Five things/.test(card));
  check("...it counts the rows instead", card.indexOf(F.length + " destinations") >= 0);

  const map = net.lastMap({ ok: true, updates_signed: true, google_connected: false,
    last: { updates: 1792600000, feeds: 1792600100, briefings: null, alerts: null,
            claude: null, foryou_claude: null, google: null } });
  check("Last outbound for feeds is the gather time from the route",
        map && map.feeds && map.feeds !== "no record" && map.feeds !== "not tracked", map);
  check("an untracked destination says 'not tracked' (never a guess)",
        map && map.telegram === "not tracked" && map.models === "not tracked" &&
        map.websearch === "not tracked" && map.webresearch === "not tracked", map);
  check("a tracked one with nothing yet says 'no record'",
        map && map.claude === "no record", map);
  check("a signed-in update check says so", map && / · signed in$/.test(map.updates), map);
  check("Google not connected and no record reads 'not connected'",
        map && map.google === "not connected", map);
  check("no answer from the route drops the column (null)",
        net.lastMap(null) === null && net.lastMap({ ok: false }) === null);
} catch (e) {
  check("harness ran: " + (e && e.stack || e), false);
}
console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);
