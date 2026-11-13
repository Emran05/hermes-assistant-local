// C35 (docs/audits/2026-10-21-codebase-audit.md) — the UI half.
//
// BEFORE: NETWORK_FACTS had one Claude row, "That one question. Never your
// files, keys or history.", while the unattended For-You loop sent the
// You-Model profile + headlines to Claude whenever escalation was on; and a
// toggle whose state was unknown rendered On (`prefs[key] !== false`), so the
// Data & Network card showed escalation On whenever its read failed.
//
// AFTER: For-You's Claude tier is its own row with its own switch
// (foryou_claude, POST /api/claude/foryou); the escalation row says what is
// really sent; switches marked `def:false` render On only for an explicit
// true. The REAL aux_onboarding.js and aux_network.js are evaluated in node:vm
// with window stubbed and fetch mocked — nothing reaches the dashboard.
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

const CALLS = [];
const ROUTES = {};
function fakeFetch(url, opts) {
  const method = (opts && opts.method) || "GET";
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  CALLS.push({ url, method, body });
  const h = ROUTES[method + " " + url.split("?")[0]];
  const res = h ? h(body) : null;
  return Promise.resolve({
    ok: !!res, status: res ? 200 : 404,
    json: () => Promise.resolve(res || { ok: false, error: "not found" })
  });
}

function makeCtx(doc) {
  const ctx = { console, setTimeout, clearTimeout, setInterval, clearInterval,
                fetch: fakeFetch, Promise, JSON, Date, Math, Number, String, Array,
                Object, Boolean, isNaN };
  ctx.window = ctx; ctx.globalThis = ctx;
  if (doc) ctx.document = doc;
  return vm.createContext(ctx);
}
function load(ctx, rel) {
  const file = path.join(REPO, rel);
  vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
}

(async function () {
  // ---- aux_onboarding.js: the table ---------------------------------------
  const ctx = makeCtx(null);
  load(ctx, "dashboard/aux_onboarding.js");
  const ob = ctx.hermesOnboarding;
  check("hermesOnboarding loads headless", !!(ob && ob.NETWORK_FACTS));
  const facts = ob.NETWORK_FACTS;
  const fy = facts.find((f) => f.id === "foryou_claude");
  check("For-You's Claude tier has its own row", !!fy, facts.map((f) => f.id));
  check("...with its own switch, foryou_claude, off by default",
        !!(fy && fy.toggle && fy.toggle.key === "foryou_claude" && fy.toggle.def === false),
        fy && fy.toggle);
  check("...that names the You-Model profile and the headlines",
        !!(fy && /You-Model/.test(fy.data) && /headlines/.test(fy.data)), fy && fy.data);
  // R2-43: foryou_loop rebuilds every FY_INTERVAL (2 h) AND on new intel
  check("...and says when it runs: every 2 hours and on new news",
        !!(fy && /every 2 hours/i.test(fy.when) && /new news/i.test(fy.when)), fy && fy.when);
  const cl = facts.find((f) => f.id === "claude");
  check('the escalation row no longer claims "That one question"',
        !!(cl && !/That one question/.test(cl.data)), cl && cl.data);
  check("the escalation switch is off by default too",
        !!(cl && cl.toggle && cl.toggle.def === false), cl && cl.toggle);

  function inputFor(html, key) {
    const m = html.match(new RegExp('<input[^>]*data-pref="' + key + '"[^>]*>'));
    return m ? m[0] : null;
  }
  let html = ob.networkTableHTML({ prefs: {}, inputs: true });
  check("an UNKNOWN For-You state renders Off (unchecked)",
        inputFor(html, "foryou_claude") && !/ checked/.test(inputFor(html, "foryou_claude")),
        inputFor(html, "foryou_claude"));
  check("an UNKNOWN escalation state renders Off (was: On)",
        inputFor(html, "claude_escalation") && !/ checked/.test(inputFor(html, "claude_escalation")),
        inputFor(html, "claude_escalation"));
  html = ob.networkTableHTML({ prefs: { foryou_claude: "true", claude_escalation: 1 }, inputs: true });
  check("only an explicit true turns those switches On (not \"true\" / 1)",
        !/ checked/.test(inputFor(html, "foryou_claude")) &&
        !/ checked/.test(inputFor(html, "claude_escalation")));
  html = ob.networkTableHTML({ prefs: { foryou_claude: true, claude_escalation: true }, inputs: true });
  check("an explicit true renders On", / checked/.test(inputFor(html, "foryou_claude")) &&
        / checked/.test(inputFor(html, "claude_escalation")));
  html = ob.networkTableHTML({ prefs: {}, inputs: true });
  check("switches without def:false keep their old default (briefings On when unknown)",
        / checked/.test(inputFor(html, "briefings") || ""), inputFor(html, "briefings"));
  check("the welcome copy counts the rows instead of saying \"Five\"",
        ob.LOCAL_LINES.join(" ").indexOf(" " + facts.length + " things") >= 0 &&
        !/Five things/.test(ob.LOCAL_LINES.join(" ")), ob.LOCAL_LINES);

  // ---- aux_network.js: the card reads and flips the new switch -------------
  const input = { checked: true, parentNode: null };
  const lbl = { textContent: "" };
  const wrap = { classList: { add() {}, remove() {} }, querySelector: () => lbl };
  input.parentNode = wrap;
  const doc = {
    getElementById: () => null,
    querySelector: (sel) => (sel.indexOf('data-pref="foryou_claude"') >= 0 ? input : null),
    createElement: () => ({})
  };
  const nctx = makeCtx(doc);
  nctx.hermesOnboarding = ob;
  load(nctx, "dashboard/aux_network.js");
  const net = nctx.hermesNetwork;
  check("hermesNetwork loads headless", !!(net && typeof net.flip === "function"));

  ROUTES["GET /api/claude/foryou"] = () => ({ ok: true, enabled: true, escalation: false });
  ROUTES["GET /api/watchtower"] = () => ({ ok: true, master: { briefings: false, news: true }, recent: [] });
  ROUTES["GET /api/claude/bridge"] = () => ({ ok: true, recent: [] });
  ROUTES["GET /api/google/status"] = () => ({ ok: true, connected: false });
  ROUTES["GET /api/update/check"] = () => ({ checked_at: 0 });
  // /api/claude/escalate deliberately unanswered: an unread escalation state
  await net.refresh();
  check("the card reads For-You's switch from its route",
        net.state.prefs.foryou_claude === true, net.state.prefs);
  check("an unread escalation state is Off in the card (was: true)",
        net.state.prefs.claude_escalation === false, net.state.prefs);

  CALLS.length = 0;
  ROUTES["POST /api/claude/foryou"] = (b) => ({ ok: true, enabled: b.enabled === true });
  await net.flip("foryou_claude", false, "For-You via Claude");
  const post = CALLS.find((c) => c.method === "POST");
  check("flipping it POSTs /api/claude/foryou with a JSON boolean",
        !!(post && post.url === "/api/claude/foryou" && post.body.enabled === false), post);
  check("...and the card's state follows", net.state.prefs.foryou_claude === false);
  check("...and the visible word says Off", lbl.textContent === "Off", lbl.textContent);

  ROUTES["POST /api/claude/foryou"] = () => ({ ok: false, error: "nope" });
  input.checked = true;
  await net.flip("foryou_claude", true, "For-You via Claude");
  check("a refused flip reverts the checkbox to the stored state",
        input.checked === false && net.state.prefs.foryou_claude === false,
        { checked: input.checked, st: net.state.prefs.foryou_claude });

  console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.log("  FAIL harness crashed: " + (e && e.stack || e));
  console.log("TESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
