// Headless render check for dashboard/aux_trace.js — no DOM, no dashboard.
// Covers the two review fixes on the client side: a capped export must be
// announced (it used to be silently short), and inferred turns must be counted
// apart from measured ones.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_trace.js");
const fails = [];
let checks = 0;
function check(name, cond, extra) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : "  " + (extra || "")));
  if (!cond) fails.push(name);
}

const toasts = [];
let nextRes = null;
const win = {};
const sandbox = {
  window: win, globalThis: win, console,
  // no document => paint() returns early; every pure function still runs
  setTimeout: () => 0, clearTimeout: () => {},
  URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
  navigator: { clipboard: { writeText: async () => {} } },
  toast: (m) => toasts.push(String(m)),
  fetch: async () => nextRes,
};
sandbox.window.window = win;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });

const H = win.hermesTrace;
check("hermesTrace exported", !!H);

function res(headers, body) {
  return {
    ok: true,
    headers: { get: (k) => (Object.prototype.hasOwnProperty.call(headers, k) ? headers[k] : null) },
    text: async () => body,
  };
}

console.log("\n== inferred turns are shown apart from measured ones ==");
const sum = {
  ok: true, traces: 2, turns: 4, turns_synthetic: 2, tool_spans: 6,
  truncations: 1, undone: 1, tokens_in: 1200, tokens_out: 520,
  turns_estimated_tokens: 2, mean_prefill_s: 0.42, mean_cached_pct: 88.1,
  models: {}, max_days: 31, days: 1, spans: 10, truncated: false,
};
const stats = H.statsHTML({ sum: sum });
check("a 'turns' cell and an 'inferred turns' cell",
      stats.indexOf(">turns<") >= 0 && stats.indexOf(">inferred turns<") >= 0, stats);
check("turns shows the 2 measured ones, not all 4",
      /<b>2<\/b><span>turns<\/span>/.test(stats), stats);
check("inferred shows the 2 synthetic ones",
      /<b>2<\/b><span>inferred turns<\/span>/.test(stats), stats);
check("the summary line names them too",
      H.summaryLine({ sum: sum }).indexOf("2 inferred from tool calls alone") >= 0,
      H.summaryLine({ sum: sum }));
const noSyn = H.statsHTML({ sum: Object.assign({}, sum, { turns_synthetic: 0 }) });
check("with none inferred, turns is the whole count and the cell is quiet",
      /<b>4<\/b><span>turns<\/span>/.test(noSyn) && noSyn.indexOf("is-quiet") >= 0, noSyn);
check("an older payload without the field still renders",
      /<b>4<\/b><span>turns<\/span>/.test(H.statsHTML({ sum: { traces: 1, turns: 4 } })));

(async function () {
  console.log("\n== a capped export is announced ==");
  toasts.length = 0;
  nextRes = res({
    "Content-Disposition": 'attachment; filename="hermes-trace-20260908-20260908.jsonl"',
    "X-Hermes-Trace-Truncated": "1",
    "X-Hermes-Trace-Span-Cap": "50000",
  }, '{"type":"span"}\n');
  await H.download("jsonl");
  check("the toast warns about the cap",
        toasts.length === 1 && /50,000-span cap/.test(toasts[0]), JSON.stringify(toasts));
  check("the toast says what to do about it", /narrow the range/.test(toasts[0] || ""), toasts[0]);
  check("no 'saved fine' toast alongside it",
        !toasts.some((t) => /^Exported as|^Copied as/.test(t)), JSON.stringify(toasts));
  check("the warning also stays in the card (a toast disappears)",
        H.state.warn.indexOf("cap") >= 0 &&
        H.cardHTML(H.state).indexOf('<p class="trwarn">') >= 0,
        H.state.warn);
  check("and it is a warning, not an error", H.state.err === "", H.state.err);

  toasts.length = 0;
  nextRes = res({ "Content-Disposition": 'attachment; filename="hermes-trace.jsonl"' },
                '{"type":"span"}\n');
  await H.download("jsonl");
  check("a complete export toasts normally",
        toasts.length === 1 && /^Copied as|^Exported as/.test(toasts[0]), JSON.stringify(toasts));
  check("and clears the previous warning", H.state.warn === "", H.state.warn);
  // `.trwarn` is always in the <style> block; the LINE is what must be gone
  check("no warning line in the card",
        H.cardHTML(H.state).indexOf('<p class="trwarn">') < 0);

  console.log("\n=========================================");
  if (fails.length) { console.log("FAILED " + fails.length + ":"); fails.forEach((f) => console.log("  - " + f)); }
  else console.log("ALL TRACE JS TESTS PASSED");
  console.log("=========================================");
  console.log("TESTS " + (checks - fails.length) + " passed " +
            fails.length + " failed");
process.exit(fails.length ? 1 : 0);
})();
