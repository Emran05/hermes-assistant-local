// Headless render check for dashboard/aux_evals.js — no DOM, no fetch, no model.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_evals.js");
const fails = [];
let checks = 0;
function check(name, cond, extra) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : "  " + (extra || "")));
  if (!cond) fails.push(name);
}

const win = {};
const sandbox = {
  window: win, globalThis: win, console,
  // no document => paint() returns early; cardHTML is still pure
  setTimeout: () => 0, clearTimeout: () => {},
  fetch: () => Promise.reject(new Error("no network in the harness")),
};
sandbox.window.window = win;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });

const H = win.hermesEvals;
console.log("\n== module surface ==");
check("hermesEvals exported", !!H);
check("mindExtras chained", typeof win.mindExtras === "function");

console.log("\n== formatters ==");
check("ms(450)", H.ms(450) === "450 ms", H.ms(450));
check("ms(2340)", H.ms(2340) === "2.3 s", H.ms(2340));
check("ms(null)", H.ms(null) === "—", H.ms(null));
check("hhmm12(13,0)", H.hhmm12(13, 0) === "1:00 PM", H.hhmm12(13, 0));
check("hhmm12(0,5)", H.hhmm12(0, 5) === "12:05 AM", H.hhmm12(0, 5));
check("hhmm12(12,0)", H.hhmm12(12, 0) === "12:00 PM", H.hhmm12(12, 0));
check("when(0)", H.when(0) === "never", H.when(0));
const now = Date.now();
check("when(today)", /^today at /.test(H.when(now / 1000 - 3600, now)), H.when(now / 1000 - 3600, now));
check("when(yesterday)", /^yesterday at /.test(H.when(now / 1000 - 26 * 3600, now)),
      H.when(now / 1000 - 26 * 3600, now));
check("tone 9/9 -> ok", H.tone(9, 9) === "ok");
check("tone 7/9 -> ''", H.tone(7, 9) === "");
check("tone 5/9 -> warn", H.tone(5, 9) === "warn");
check("tone 2/9 -> bad", H.tone(2, 9) === "bad");
check("niceMax(4200)", H.niceMax(4200) === 5000, H.niceMax(4200));

console.log("\n== bucketByDay ==");
const day = 86400;
const t0 = Math.floor(now / 1000);
const hist = [
  { id: 1, ts: t0 - 6 * day, passed: 7, total: 9, median_latency_ms: 900, trigger: "scheduled" },
  { id: 2, ts: t0 - 3 * day, passed: 9, total: 9, median_latency_ms: 700, trigger: "scheduled" },
  { id: 3, ts: t0 - 3 * day + 120, passed: 8, total: 9, median_latency_ms: 800, trigger: "manual" },
  { id: 4, ts: t0 - 90 * day, passed: 1, total: 9, median_latency_ms: 5000, trigger: "manual" },
];
let b = H.bucketByDay(hist, 14, now);
check("14 buckets", b.length === 14, b.length);
check("out-of-range run dropped", b.every((x) => x.rate === null || x.rate > 0.5),
      JSON.stringify(b.filter((x) => x.runs)));
const twoRuns = b.filter((x) => x.runs === 2);
check("two runs on one day merged", twoRuns.length === 1 && twoRuns[0].total === 18,
      JSON.stringify(twoRuns));
check("merged pass rate averaged", twoRuns.length === 1 &&
      Math.abs(twoRuns[0].rate - 17 / 18) < 1e-9, twoRuns[0] && twoRuns[0].rate);
check("merged median averaged", twoRuns.length === 1 && twoRuns[0].median === 750,
      twoRuns[0] && twoRuns[0].median);
check("empty days present with rate null", b.filter((x) => x.rate === null).length === 12,
      b.filter((x) => x.rate === null).length);
check("60d range keeps 3, drops the 90d-old run",
      H.bucketByDay(hist, 60, now).filter((x) => x.runs).reduce((a, x) => a + x.runs, 0) === 3);
check("empty history -> all null", H.bucketByDay([], 30, now).every((x) => x.rate === null));

console.log("\n== chartHTML ==");
let svg = H.chartHTML(H.bucketByDay(hist, 14, now));
check("renders an svg", svg.indexOf("<svg") >= 0);
check("has a legend", svg.indexOf("Median latency") >= 0);
check("has a latency polyline", svg.indexOf("evline") >= 0);
check("no NaN in the svg", svg.indexOf("NaN") < 0, svg.slice(0, 200));
check("no undefined in the svg", svg.indexOf("undefined") < 0);
check("empty history -> hint, no svg",
      H.chartHTML(H.bucketByDay([], 14, now)).indexOf("<svg") < 0);

console.log("\n== cardHTML ==");
const payload = {
  ok: true,
  settings: { enabled: true, at_hour: 13, at_minute: 0, require_ac: true, wake_if_ac: false, days: 1 },
  defaults: {}, model: "mlx-community/Qwen3.8-27B", cases_total: 9,
  running: false, note: "",
  last: { id: 4, ts: t0 - 3600, model: "mlx-community/Qwen3.8-27B", trigger: "scheduled",
          total: 9, passed: 8, median_latency_ms: 820, latency_ms_total: 9400, error: null },
  results: [
    { case: "simple_call", label: "calls a simple function on request", passed: true, latency_ms: 640, detail: "called get_weather(city='Tokyo')" },
    { case: "restraint", label: "no tool call when none needed", passed: false, latency_ms: 410, detail: "called a tool for trivia: [\"get_weather\"]" },
    { case: "json_strict", label: "a JSON object with exactly three named keys", passed: true, latency_ms: 700, detail: "exactly the 3 required keys: city, country, population" },
  ],
  history: hist,
  power: { ac: false, battery: true, pct: 71 },
  can_run: false, reason: "on battery — plug in to run the suite", wakeable: false,
  next_guard: "",
};
let html = H.cardHTML({ data: payload, loaded: true, days: 14, err: "", busy: false, notice: "" });
check("card renders", html.indexOf("Evals") >= 0 && html.length > 3000, html.length);
check("no NaN", html.indexOf("NaN") < 0);
check("no undefined", html.indexOf("undefined") < 0);
check("Run now is disabled on battery", /data-act="run" disabled/.test(html));
check("reason shown", html.indexOf("plug in to run the suite") >= 0);
check("fails sorted first",
      html.indexOf("is-fail") < html.indexOf('class="is-pass"'), "fail after pass");
check("schedule sentence is 12-hour", html.indexOf("1:00 PM") >= 0);
check("no emoji", !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(html));
check("tabular numerals declared", html.indexOf("tabular-nums") >= 0);
check("only token colours (no hex besides #fff)",
      (html.match(/#[0-9a-fA-F]{3,6}/g) || []).every((h) => h.toLowerCase() === "#fff"),
      (html.match(/#[0-9a-fA-F]{3,6}/g) || []).join(","));
// `transition:none` inside the reduced-motion block is a reset, not a
// shorthand — aux_mind_drill.js does the same. Everything else must be explicit.
check("explicit transition-property everywhere",
      !/[^-]transition:(?!none)/.test(html), "bare `transition:` shorthand found");
check("rateTone 8/9 neutral, 6/9 warn",
      H.rateTone(8 / 9) === "" && H.rateTone(6 / 9) === "warn",
      H.rateTone(8 / 9) + "/" + H.rateTone(6 / 9));

// loading + error shells
check("loading shell", H.cardHTML({}).indexOf("evskel") >= 0);
check("error shell", H.cardHTML({ data: { ok: false, error: "boom" } }).indexOf("boom") >= 0);

// no last run at all
const empty = Object.assign({}, payload, { last: null, results: [], history: [] });
html = H.cardHTML({ data: empty, loaded: true, days: 30 });
check("empty state renders", html.indexOf("never") >= 0 && html.indexOf("NaN") < 0);

// can_run true -> button enabled
const live = Object.assign({}, payload, { can_run: true, reason: "", power: { ac: true } });
html = H.cardHTML({ data: live, loaded: true, days: 14 });
check("Run now enabled when can_run", !/data-act="run" disabled/.test(html));

console.log("\n== errors and skips are gaps, not zeroes ==");
const gapHist = [
  { id: 10, ts: t0 - 5 * day, passed: 9, total: 9, errors: 0, median_latency_ms: 700 },
  // every case failed to reach the model: measured nothing
  { id: 11, ts: t0 - 4 * day, passed: 0, total: 9, errors: 9, median_latency_ms: 12 },
  // the scheduler marked the day done without running
  { id: 12, ts: t0 - 3 * day, passed: 0, total: 0, errors: 0, median_latency_ms: 0 },
  // a partial infra failure: 6 measured, 5 of them passed
  { id: 13, ts: t0 - 2 * day, passed: 5, total: 9, errors: 3, median_latency_ms: 800 },
];
const gb = H.bucketByDay(gapHist, 7, now);
const byDay = {};
gb.forEach((b) => { if (b.runs) byDay[b.d] = b; });
const dayOf = (n) => {
  const d = new Date((t0 - n * day) * 1000);
  return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
};
check("clean run charts normally", byDay[dayOf(5)].rate === 1, byDay[dayOf(5)]);
check("all-error run is a gap (rate null) though the day has a run",
      byDay[dayOf(4)].rate === null && byDay[dayOf(4)].runs === 1 && byDay[dayOf(4)].errored === 1,
      JSON.stringify(byDay[dayOf(4)]));
check("skipped day is a gap and counted as skipped",
      byDay[dayOf(3)].rate === null && byDay[dayOf(3)].skipped === 1,
      JSON.stringify(byDay[dayOf(3)]));
check("partial errors leave the denominator at what was measured",
      Math.abs(byDay[dayOf(2)].rate - 5 / 6) < 1e-9 && byDay[dayOf(2)].total === 6,
      JSON.stringify(byDay[dayOf(2)]));
check("an all-error day never drags the latency line",
      byDay[dayOf(4)].median === null, byDay[dayOf(4)].median);
let gsvg = H.chartHTML(gb);
check("the gap days draw no bar", (gsvg.match(/<g class="evgrow"/g) || []).length === 2,
      (gsvg.match(/<g class="evgrow"/g) || []).length);
check("the caption explains the blanks", gsvg.indexOf("left blank") >= 0);
const onlyBad = H.bucketByDay([gapHist[1], gapHist[2]], 7, now);
check("a range with nothing measured says so, not 'no runs'",
      H.chartHTML(onlyBad).indexOf("Nothing was measured") >= 0, H.chartHTML(onlyBad).slice(0, 160));

console.log("\n== the per-case error state ==");
const three = H.casesHTML([
  { case: "a", label: "passes", passed: true, error: false, latency_ms: 10, detail: "ok" },
  { case: "b", label: "unreachable", passed: false, error: true, latency_ms: 20,
    detail: "infrastructure error: URLError: [Errno 61] Connection refused" },
  { case: "c", label: "wrong answer", passed: false, error: false, latency_ms: 30, detail: "bad keys" },
]);
check("three distinct row classes", /is-fail/.test(three) && /is-error/.test(three) && /is-pass/.test(three));
check("error rows carry a visible tag", three.indexOf(">error<") >= 0);
check("order is fail, then error, then pass",
      three.indexOf('class="is-fail"') < three.indexOf('class="is-error"') &&
      three.indexOf('class="is-error"') < three.indexOf('class="is-pass"'));
check("caseRank ranks fail<error<pass",
      H.caseRank({ passed: false }) === 0 && H.caseRank({ error: true }) === 1 &&
      H.caseRank({ passed: true }) === 2);

console.log("\n== store_error and skipped runs in the card ==");
const dead = Object.assign({}, payload, {
  last: null, results: [], history: [],
  store_error: "OperationalError: unable to open database file",
});
html = H.cardHTML({ data: dead, loaded: true, days: 14 });
check("a dead store says 'unavailable', never 'never'",
      html.indexOf("unavailable") >= 0 && html.indexOf(">never<") < 0, "still shows never");
check("and it names the sqlite error", html.indexOf("unable to open database file") >= 0);
check("no NaN with a dead store", html.indexOf("NaN") < 0);

const skipped = Object.assign({}, payload, {
  last: { id: 9, ts: t0 - 1800, model: "m", trigger: "skipped", total: 0, passed: 0,
          errors: 0, median_latency_ms: 0, latency_ms_total: 0,
          error: "slept through the window" },
  results: [],
});
html = H.cardHTML({ data: skipped, loaded: true, days: 14 });
check("a skipped day reads as skipped, not 0/0",
      html.indexOf("Skipped: slept through the window") >= 0 && html.indexOf("0/0") < 0,
      "0/0 or missing reason");
check("skipped is not painted as an error", html.indexOf('class="everr">Skipped') < 0);
check("no NaN on a skipped row", html.indexOf("NaN") < 0);

const allErr = Object.assign({}, payload, {
  last: { id: 8, ts: t0 - 900, model: "m", trigger: "scheduled", total: 9, passed: 0,
          errors: 9, median_latency_ms: 30, latency_ms_total: 270,
          error: "no case reached the model server — infrastructure error: URLError" },
  results: [{ case: "a", label: "unreachable", passed: false, error: true,
              latency_ms: 30, detail: "infrastructure error: URLError" }],
});
html = H.cardHTML({ data: allErr, loaded: true, days: 14 });
check("an all-error run reads 'no answer', not 0/9",
      html.indexOf("no answer") >= 0 && html.indexOf("0/9") < 0, "0/9 shown");

const partial = Object.assign({}, payload, {
  last: Object.assign({}, payload.last, { total: 9, passed: 5, errors: 3, error: null }),
});
html = H.cardHTML({ data: partial, loaded: true, days: 14 });
check("a partial infra failure scores over what was measured", html.indexOf("5/6") >= 0);
check("and says how many never reached the model",
      html.indexOf("3 of 9 cases could not reach the model") >= 0);

console.log("\n=========================================");
if (fails.length) { console.log("FAILED " + fails.length + ":"); fails.forEach((f) => console.log("  - " + f)); }
else console.log("ALL JS TESTS PASSED");
console.log("=========================================");
console.log("TESTS " + (checks - fails.length) + " passed " +
            fails.length + " failed");
process.exit(fails.length ? 1 : 0);
