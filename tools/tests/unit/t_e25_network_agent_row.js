// E25 / E84 / E77 (docs/audits/2026-11-09-codebase-audit.md) — the
// "what leaves this Mac" table.
//
// BEFORE: the card said its rows were "everything Hermes sends to the
// internet" and "Everything else runs on this Mac", but no row covered the
// agent's OWN tool traffic (its terminal — curl, git, any CLI — its browser
// tools and web extract), and Hermes' approval rules do not ask about a plain
// curl. The Claude row read as if its switch stopped "the agent asks Claude
// itself", which it cannot do for a `claude` the agent runs in its terminal.
// The alerts row described only the Telegram message, not the watch rules'
// 5-minute Yahoo Finance / CoinGecko polling (E84). The GitHub rows did not
// say their push and drift notices go out by Telegram with both notification
// masters off (E77, owner decision: they are security notices).
//
// AFTER: one honest row for the agent's tools (no switch: the approval card
// and the toolsets are its control), the headline/footers claim only what the
// table covers, and the three rows say what really happens. The REAL
// aux_onboarding.js and aux_network.js run in node:vm.
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
  const txt = (f) => f ? [f.what, f.when, f.data, f.off || ""].join(" ") : "";
  const by = {};
  F.forEach((f) => { by[f.id] = f; });

  console.log("== E25: the agent's own tools have a row ==");
  const ag = F.find((f) => /terminal/i.test(txt(f)) && /browser/i.test(txt(f)));
  check("a row names the agent's terminal and browser tools", !!ag, F.map((f) => f.id));
  const t = txt(ag);
  check("...and web extract / page fetch", /web extract|page fetch/i.test(t), t);
  check("...and curl / git / any command", /curl/.test(t) && /git/.test(t), t);
  check("...says it happens only inside a turn, when the agent decides to use a tool",
        /only inside a (chat )?turn|only during a turn/i.test(t) && /decides/i.test(t), t);
  check("...has NO switch (the approval card and toolsets are not a switch)",
        !!ag && !ag.toggle, ag && ag.toggle);
  check("...names its real control: the approval card for risky commands and the toolsets",
        !!ag && /approval/i.test(ag.off || "") && /toolset/i.test(ag.off || ""), ag && ag.off);
  // R4 review: `off` renders in the nowrap Switch column (.onb-net-ctl). The
  // implementer's 75-character text widened the Settings card's table from
  // 672 to 820 px in chrome-headless-shell (the panel has 672), so the
  // Switch column scrolled out of view — the CLAUDE.md "Table fit" failure.
  // 49 characters (the weather row's) is the longest measured to fit.
  const longOff = F.filter((f) => !f.toggle && String(f.off || "").length > 49)
    .map((f) => f.id + ":" + String(f.off).length);
  check("every switchless row's `off` fits the nowrap Switch column (<= 49 chars)",
        longOff.length === 0, longOff);
  check("...and says plainly that a plain curl is not asked about",
        /plain curl/i.test(t) && /not asked|never asked|is not asked/i.test(t), t);
  check("...and that a request can carry anything the agent has seen",
        /anything/i.test(ag ? ag.data : ""), ag && ag.data);

  console.log("== E25: the Claude row claims only what its switch stops ==");
  const cl = by.claude;
  check("the Claude row no longer says the switch covers 'when the agent asks Claude itself'",
        !!cl && !/when the agent asks Claude itself/.test(cl.when), cl && cl.when);
  check("...it says the switch does not stop the agent running the claude CLI in its terminal",
        /claude/.test(txt(cl)) && /terminal/i.test(txt(cl)) && /not stop|does not|doesn't/i.test(txt(cl)),
        txt(cl));
  check("...and the switch is still there, off by default",
        !!(cl && cl.toggle && cl.toggle.key === "claude_escalation" && cl.toggle.def === false));

  console.log("== E84: the alerts row discloses the watch rules' polling ==");
  const al = by.alerts;
  check("alerts row names Yahoo Finance and CoinGecko", /Yahoo Finance/.test(txt(al)) &&
        /CoinGecko/.test(txt(al)), txt(al));
  check("...every 5 minutes, while News is on and a price rule exists",
        /every 5 minutes/i.test(txt(al)) && /News is on/.test(txt(al)) && /rule/.test(txt(al)),
        txt(al));
  check("...and still carries the News switch", !!(al && al.toggle && al.toggle.key === "news"));
  // the code it describes: the rule pass is gated on News, and the price feeds are 300 s caches
  const wt = fs.readFileSync(path.join(REPO, "dashboard/aux_watchtower.py"), "utf8");
  const srv = fs.readFileSync(path.join(REPO, "dashboard/server.py"), "utf8");
  check("(code) _rule_pass returns when News is off",
        /def _rule_pass[\s\S]{0,300}_master_on\(cfg, "news"\)/.test(wt));
  check("(code) the markets and crypto feeds are 300 s caches",
        /_cached\("mkt:" \+ ",".join\(syms\), 300/.test(srv) &&
        /_cached\("crypto:" \+ ",".join\(coins\), 300/.test(srv));

  console.log("== E77: GitHub notices are sent by Telegram with both masters off ==");
  const gp = by.github_push, gr = by.github_repos;
  check("the push row says the Telegram notice goes out even with both masters off",
        /Telegram/.test(txt(gp)) && /both notification (master )?switches off|both masters off/i.test(txt(gp)),
        txt(gp));
  check("the repositories row says a drift notice goes by Telegram, masters off too",
        /Telegram/.test(txt(gr)) && /both notification (master )?switches off|both masters off/i.test(txt(gr)) &&
        /hermes\/ branch/.test(txt(gr)), txt(gr));
  // The connector names the exception (E77): GH_SECURITY_NOTICES_IGNORE_MASTERS.
  // The function's comment may mention _wt_push_allowed; its CODE must not call
  // any master gate.
  const ghSrc = fs.readFileSync(path.join(REPO, "dashboard/aux_github.py"), "utf8");
  const ghNotify = (ghSrc.match(/def _gh_notify[\s\S]*?\n\n\n/) || [""])[0];
  const ghNotifyCode = ghNotify.split("\n")
    .map(l => l.replace(/#.*$/, "")).join("\n").replace(/"""[\s\S]*?"""/, "");
  check("(code) the exception is named: GH_SECURITY_NOTICES_IGNORE_MASTERS = True",
        /^GH_SECURITY_NOTICES_IGNORE_MASTERS = True$/m.test(ghSrc));
  check("(code) _gh_notify is bound to that constant",
        /GH_SECURITY_NOTICES_IGNORE_MASTERS/.test(ghNotifyCode), ghNotify);
  check("(code) _gh_notify does not consult the masters (what the rows now say)",
        ghNotifyCode.length > 0 &&
        !/_wt_push_allowed|_master_on|_wt_news_on|\bmaster\b/.test(ghNotifyCode), ghNotifyCode);

  console.log("== E25: the headline and footers claim only what the table covers ==");
  const ll = ob.LOCAL_LINES.join(" ");
  check("the sheet's count line still counts the rows", ll.indexOf(" " + F.length + " ") >= 0, ll);
  check("...and points at the agent's tools row", /agent/i.test(ll) && /tools/i.test(ll), ll);
  const html = ob.networkTableHTML({ prefs: {}, inputs: true });
  check("the table caption no longer says 'Everything that ever leaves this Mac'",
        !/Everything that ever leaves/.test(html));
  check("one body row per fact",
        (html.match(/<tr><th scope="row">/g) || []).length === F.length);
  const nctx = makeCtx();
  nctx.hermesOnboarding = ob;
  load(nctx, "dashboard/aux_network.js");
  const card = nctx.hermesNetwork.cardHTML({ prefs: {}, last: null });
  check("the card lede no longer says the rows are 'everything Hermes sends'",
        !/everything Hermes/i.test(card), card.slice(0, 600));
  check("...still counts the rows", card.indexOf(F.length + " destinations") >= 0);
  check("the card footer no longer says 'Everything else runs on this Mac.'",
        !/Everything else runs on this Mac\./.test(card));
  const lede = (card.match(/<p class="netlede">([\s\S]*?)<\/p>/) || ["", ""])[1];
  check("...and names the agent's tools in the lede", /agent/i.test(lede) && /tools/.test(lede), lede);
  const map = nctx.hermesNetwork.lastMap({ ok: true, last: { updates: 0 } });
  check("Last outbound for the agent row reads 'not tracked' (no guess)",
        !!ag && map && map[ag.id] === "not tracked", map);
} catch (e) {
  check("harness ran: " + (e && e.stack || e), false);
}
console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);
