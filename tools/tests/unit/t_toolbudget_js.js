// Headless render check for aux_toolbudget.js — the repo's documented pattern:
// eval the file in node with stubbed globals and drive every branch of the
// pure renderer. No DOM, no fetch, no dashboard.
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const file = path.join(REPO, "dashboard", "aux_toolbudget.js");

global.window = global;                 // the module picks W = window
// no document -> D() returns null -> paint() early-returns, mount() no-ops
(0, eval)(fs.readFileSync(file, "utf8"));

const TB = global.hermesToolBudget;
let pass = 0, fail = 0;
const ok = (n, c, d) => { if (c) { pass++; console.log("  ok   " + n); }
                          else { fail++; console.log("  FAIL " + n + (d !== undefined ? "  — " + JSON.stringify(d) : "")); } };

console.log("\n=== pure helpers ===");
ok("kchars(24000)", TB.kchars(24000) === "24k", TB.kchars(24000));
ok("kchars(8000)", TB.kchars(8000) === "8k", TB.kchars(8000));
ok("kchars(48000)", TB.kchars(48000) === "48k", TB.kchars(48000));
ok("kchars(1500)", TB.kchars(1500) === "1.5k", TB.kchars(1500));
ok("kchars(garbage)", TB.kchars("x") === "—");
ok("pct(10.16)", TB.pct(10.16) === "10.2%", TB.pct(10.16));
ok("num(87000)", TB.num(87000) === "87,000", TB.num(87000));
ok("bytes(0) is empty", TB.bytes(0) === "");
ok("bytes(2200000)", /MB$/.test(TB.bytes(2200000)), TB.bytes(2200000));

console.log("\n=== phase() ===");
ok("no data -> error", TB.phase(null) === "error");
ok("ok:false -> error", TB.phase({ ok: false }) === "error");
ok("not installed -> setup", TB.phase({ ok: true, installed: false, enabled_in_config: false }) === "setup");
ok("installed, not in config -> setup", TB.phase({ ok: true, installed: true, enabled_in_config: false }) === "setup");
ok("in config, not observed -> pending", TB.phase({ ok: true, installed: true, enabled_in_config: true, restart_required: true }) === "pending");
ok("observed -> live", TB.phase({ ok: true, installed: true, enabled_in_config: true, restart_required: false }) === "live");
// --- review fix 8: unknown is a state of its own, never a rounding of "off"
ok("helper_error -> unknown", TB.phase({ ok: true, installed: true, enabled_in_config: null, helper_error: "boom" }) === "unknown");
ok("null enabled_in_config -> unknown", TB.phase({ ok: true, installed: true, enabled_in_config: null }) === "unknown");
ok("missing enabled_in_config -> unknown", TB.phase({ ok: true, installed: true }) === "unknown");
ok("false is still setup, not unknown", TB.phase({ ok: true, installed: true, enabled_in_config: false }) === "setup");

console.log("\n=== savingsLine() ===");
// --- review fix 9: the reassuring sentence needs positive evidence
ok("no calls + observed -> the positive claim",
   /Nothing has gone over budget today/.test(TB.savingsLine({ observed: true, stats: { today: { calls: 0 } } })));
ok("no calls + NOT observed -> no claim",
   /No truncation recorded yet — cannot confirm the plugin is running\./
     .test(TB.savingsLine({ observed: false, stats: { today: { calls: 0 } } })),
   TB.savingsLine({ observed: false, stats: { today: { calls: 0 } } }));
ok("no calls + observed missing -> no claim",
   /cannot confirm/.test(TB.savingsLine({ stats: { today: { calls: 0 } } })));
ok("an empty log never asserts the positive claim",
   !/Nothing has gone over budget/.test(TB.savingsLine({ stats: {} })));
const line = TB.savingsLine({ stats: { today: { calls: 3, kept_chars: 17900, orig_chars: 104900, est_tokens_saved: 24166, spills: 2 }, spill_bytes: 2300000 } });
ok("quotes kept of total", /Kept 18k of 105k chars/.test(line), line);
ok("quotes the call count", /across 3 tool calls today/.test(line), line);
ok("quotes tokens", /about 24,166 tokens/.test(line), line);
ok("mentions the spills", /2 full outputs saved on disk \(2\.1 MB\)/.test(line), line);
ok("singular call", /1 tool call today/.test(TB.savingsLine({ stats: { today: { calls: 1, kept_chars: 100, orig_chars: 200, est_tokens_saved: 1, spills: 1 } } })));

const LIVE = {
  ok: true, installed: true, enabled_in_config: true, restart_required: false,
  settings: { enabled: true, max_chars: 24000, spill: true },
  est_tokens: 6667, pct_window: 10.2, context_length: 65536,
  choices: [
    { chars: 8000, est_tokens: 2222, pct_window: 3.4 },
    { chars: 16000, est_tokens: 4444, pct_window: 6.8 },
    { chars: 24000, est_tokens: 6667, pct_window: 10.2 },
    { chars: 48000, est_tokens: 13333, pct_window: 20.3 }
  ],
  stats: { today: { calls: 3, kept_chars: 17900, orig_chars: 104900, est_tokens_saved: 24166, spills: 2, tools: { terminal: 2, read_file: 1 } }, spill_bytes: 2300000, last: null },
  restart_note: "Plugins load when the agent service starts."
};

console.log("\n=== cardHTML() renders every phase without throwing ===");
const variants = {
  loading: null,
  error: { ok: false, error: "boom <script>x</script>" },
  setup_missing: { ok: true, installed: false, enabled_in_config: false },
  setup_disabled: { ok: true, installed: true, enabled_in_config: false },
  pending: Object.assign({}, LIVE, { restart_required: true }),
  live: LIVE,
  off: Object.assign({}, LIVE, { settings: { enabled: false, max_chars: 16000, spill: true } }),
  quiet: Object.assign({}, LIVE, { stats: { today: { calls: 0, tools: {} }, spill_bytes: 0 } }),
  // review fix 8/9
  unknown: { ok: true, installed: true, enabled_in_config: null,
             helper_error: "plugin_enable.py could not be loaded (ImportError)" },
  stale: Object.assign({}, LIVE, { install: { present: true, linked: false, stale: true } })
};
for (const [name, data] of Object.entries(variants)) {
  let html = null, threw = null;
  try { html = TB.cardHTML({ data }); } catch (e) { threw = e; }
  ok("renders " + name, !threw && typeof html === "string" && html.length > 100, threw && String(threw));
  if (html) {
    ok("  " + name + ": has the title", html.includes("Tool output budget"));
    ok("  " + name + ": no emoji", !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(html));
    ok("  " + name + ": balanced <style>", (html.match(/<style>/g) || []).length === (html.match(/<\/style>/g) || []).length);
  }
}

console.log("\n=== escaping + content ===");
const errHtml = TB.cardHTML({ data: variants.error });
ok("error text is escaped", errHtml.includes("&lt;script&gt;") && !errHtml.includes("<script>x"), errHtml.slice(0, 200));
const liveHtml = TB.cardHTML({ data: LIVE });
ok("shows the 24k budget", liveHtml.includes("24k chars"));
ok("marks 24k as chosen", /class="tbopt is-on"[\s\S]{0,200}24k chars/.test(liveHtml));
ok("offers all four choices", (liveHtml.match(/name="tbmax"/g) || []).length === 4);
ok("shows % of window", liveHtml.includes("10.2%"));
ok("cap switch is on", /data-act="enabled" aria-checked="true"/.test(liveHtml));
ok("spill switch is on", /data-act="spill" aria-checked="true"/.test(liveHtml));
ok("shows the per-tool breakdown", liveHtml.includes("terminal 2"));
ok("names the agent's own caps honestly", /terminal 50k chars, read_file 100k, web_extract 15k/.test(liveHtml));
const offHtml = TB.cardHTML({ data: variants.off });
ok("off dims the body", offHtml.includes('data-off="1"'));
ok("off disables the radios", (offHtml.match(/disabled>/g) || []).length >= 4);
ok("off disables the spill switch", /data-act="spill"[^>]*disabled/.test(offHtml));
const setupHtml = TB.cardHTML({ data: variants.setup_missing });
ok("setup offers install", setupHtml.includes('data-act="install"'));
ok("setup offers the restart opt-in", setupHtml.includes("data-restart"));
ok("setup has no budget radios", !setupHtml.includes('name="tbmax"'));
const pendHtml = TB.cardHTML({ data: variants.pending });
ok("pending offers a restart button", pendHtml.includes('data-act="restart"'));
ok("pending still shows the controls", pendHtml.includes('name="tbmax"'));
ok("busy disables install", TB.cardHTML({ data: variants.setup_missing, busy: true }).includes("Installing…"));

console.log("\n=== review fixes 8/9: unknown state + staleness ===");
const unkHtml = TB.cardHTML({ data: variants.unknown });
ok("unknown renders the helper error", unkHtml.includes("plugin_enable.py could not be loaded"), unkHtml.slice(0, 400));
ok("unknown never offers Install", !unkHtml.includes('data-act="install"'), unkHtml.slice(0, 600));
ok("unknown offers no budget radios", !unkHtml.includes('name="tbmax"'));
ok("unknown says nothing was changed", /Nothing has been changed/.test(unkHtml));
ok("setup still offers Install", TB.cardHTML({ data: variants.setup_missing }).includes('data-act="install"'));

ok("staleHTML empty when linked", TB.staleHTML({ install: { linked: true, stale: false } }) === "");
ok("staleHTML empty with no install block", TB.staleHTML({}) === "");
ok("staleHTML warns on a stale copy", /tbwarn/.test(TB.staleHTML({ install: { stale: true } })));
const staleHtml = TB.cardHTML({ data: variants.stale });
ok("live render carries the stale warning", /older than/.test(staleHtml), staleHtml.slice(0, 300));
ok("a fresh install shows no stale warning", !/older than/.test(TB.cardHTML({ data: LIVE })));

const quietHtml = TB.cardHTML({ data: variants.quiet });
ok("an empty log does not claim all-clear", /cannot confirm the plugin is running/.test(quietHtml), quietHtml.slice(0, 200));
ok("observed + empty log does claim all-clear",
   /Nothing has gone over budget today/.test(
     TB.cardHTML({ data: Object.assign({}, variants.quiet, { observed: true }) })));

console.log("\n=== chaining + no-DOM safety ===");
ok("exports mindExtras chain", typeof global.mindExtras === "function");
let threw = null;
try { TB.paint(); } catch (e) { threw = e; }
ok("paint() is a no-op without a document", threw === null, threw && String(threw));

console.log("\nTESTS " + pass + " passed " + fail + " failed");
process.exit(fail ? 1 : 0);
