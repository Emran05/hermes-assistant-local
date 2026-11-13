// R2-29 — the trace range view and the memory preview apply only the NEWEST
// response, never whichever one happened to land last.
//
// aux_trace.js load(): Today -> 30 days -> Today fires three summaries; a slow
// 30-day answer arriving after the Today one put 30 days of numbers under the
// Today button. aux_memlayer.js runPreview(): the debounce spaced the requests
// out, but a slow preview for "tea" still overwrote the one for
// "tea with milk" typed after it. Both now carry a per-view sequence number
// and drop any answer that is not the latest. A capped export's warning also
// names the range that was REQUESTED, not the one selected when it answered.
//
// Evals the REAL dashboard/aux_trace.js and aux_memlayer.js in node vm
// sandboxes with a fetch stub whose answers the test releases in any order.
// No DOM (paint() no-ops), no dashboard, no network.
//
// Fails on the old code: S.sum is the 30-day payload after the Today one
// answered first, and the preview block is the "tea" one.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");

let pass = 0, fail = 0;
const ok = (n, c, d) => { if (c) { pass++; console.log("  ok   " + n); }
                          else { fail++; console.log("  FAIL " + n + (d !== undefined ? "  — " + JSON.stringify(d) : "")); } };

// a fetch whose every call parks until the test releases it
function deferredFetch() {
  const pending = [];
  const fetch = (url) => new Promise((resolve) => pending.push({ url, resolve }));
  return { fetch, pending };
}
function jsonRes(body, headers) {
  return {
    ok: true,
    json: async () => body,
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
    headers: { get: (k) => (headers && Object.prototype.hasOwnProperty.call(headers, k) ? headers[k] : null) },
  };
}
const tick = () => new Promise((r) => setImmediate(r));

function sandboxFor(file, fetch, extra) {
  const win = {};
  const sb = Object.assign({
    window: win, globalThis: win, console,
    setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {},
    URL: { createObjectURL: () => "blob:x", revokeObjectURL: () => {} },
    navigator: { clipboard: { writeText: async () => {} } },
    fetch,
  }, extra || {});
  sb.window.window = win;
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(file, "utf8"), sb, { filename: file });
  return win;
}

(async () => {
  console.log("\n=== aux_trace.js: the range summary ===");
  const tf = deferredFetch();
  const toasts = [];
  const TR = sandboxFor(path.join(REPO, "dashboard", "aux_trace.js"), tf.fetch,
                        { toast: (m) => toasts.push(String(m)) }).hermesTrace;
  const S = TR.state;

  S.range = "30d";
  const slow = TR.load();                       // the 30-day count: slow
  S.range = "today";
  const fast = TR.load();                       // the Today count: fast
  await tick();
  ok("two summaries were requested", tf.pending.length === 2, tf.pending.map((p) => p.url));
  tf.pending[1].resolve(jsonRes({ ok: true, which: "today", traces: 3 }));
  await fast;
  ok("the Today answer is applied", S.sum && S.sum.which === "today", S.sum);
  ok("the card is no longer loading", S.loading === false, S.loading);
  tf.pending[0].resolve(jsonRes({ ok: true, which: "30d", traces: 900 }));
  await slow;
  ok("the late 30-day answer is DROPPED", S.sum && S.sum.which === "today", S.sum);

  S.range = "7d";
  const stale = TR.load();
  S.range = "today";
  const fresh = TR.load();
  await tick();
  tf.pending[3].resolve(jsonRes({ ok: true, which: "today-2" }));
  await fresh;
  tf.pending[2].resolve(jsonRes({ ok: false, error: "7d summary failed" }));
  await stale;
  ok("a late ERROR for an old range does not replace the newer summary",
     S.sum && S.sum.which === "today-2" && S.err === "", [S.sum, S.err]);

  S.range = "30d";
  const inflight = TR.load();
  S.range = "custom"; S.from = "2026-10-20"; S.to = "2026-09-01";   // to < from
  await TR.load();
  ok("a range error is shown at once", !!S.err && S.sum === null, [S.err, S.sum]);
  await tick();
  tf.pending[4].resolve(jsonRes({ ok: true, which: "30d-late" }));
  await inflight;
  ok("... and the in-flight answer for the previous range cannot clear it",
     !!S.err && S.sum === null && S.loading === false, [S.err, S.sum, S.loading]);

  console.log("\n=== aux_trace.js: a capped export names the range it asked for ===");
  S.range = "today"; S.err = "";
  toasts.length = 0;
  const dl = TR.download("jsonl");
  S.range = "30d";                              // the owner switches mid-download
  await tick();
  const last = tf.pending[tf.pending.length - 1];
  ok("the export asked for today's range", /\/api\/trace\/export\?/.test(last.url), last.url);
  last.resolve(jsonRes('{"type":"span"}\n', {
    "Content-Disposition": 'attachment; filename="t.jsonl"',
    "X-Hermes-Trace-Truncated": "1", "X-Hermes-Trace-Span-Cap": "50000" }));
  await dl;
  ok("the cap warning names Today, not the range selected afterwards",
     /start of today/i.test(S.warn) && !/30 days/i.test(S.warn), S.warn);

  console.log("\n=== aux_memlayer.js: the preview box ===");
  const mf = deferredFetch();
  const ML = sandboxFor(path.join(REPO, "dashboard", "aux_memlayer.js"), mf.fetch).hermesMemLayer;
  const P = ML.state.preview;
  ok("runPreview is on the harness surface", typeof ML.runPreview === "function");
  const p1 = ML.runPreview("tea", true);
  const p2 = ML.runPreview("tea with milk", true);
  await tick();
  ok("two previews were requested", mf.pending.length === 2, mf.pending.map((p) => p.url));
  mf.pending[1].resolve(jsonRes({ ok: true, block: "BLOCK for tea with milk", chars: 23, tokens: 7,
                                  fact_count: 2, episodic_count: 0 }));
  await p2;
  ok("the newer preview is painted", P.block === "BLOCK for tea with milk", P.block);
  mf.pending[0].resolve(jsonRes({ ok: true, block: "BLOCK for tea", chars: 13, tokens: 4,
                                  fact_count: 1, episodic_count: 0 }));
  await p1;
  ok("the late preview for the OLD text is dropped",
     P.block === "BLOCK for tea with milk" && P.facts === 2 && P.text === "tea with milk", P);

  const p3 = ML.runPreview("coffee", true);
  await tick();
  await ML.runPreview("", true);                 // the owner cleared the box
  ok("clearing the box empties the preview at once", P.ran === false && P.block === "", P);
  mf.pending[2].resolve(jsonRes({ ok: true, block: "BLOCK for coffee", chars: 16 }));
  await p3;
  ok("... and the in-flight answer cannot bring it back", P.ran === false && P.block === "", P);

  const p4 = ML.runPreview("late failure", true);
  const p5 = ML.runPreview("good text", true);
  await tick();
  mf.pending[4].resolve(jsonRes({ ok: true, block: "BLOCK good", chars: 10 }));
  await p5;
  mf.pending[3].resolve(jsonRes({ ok: false, error: "boom" }));
  await p4;
  ok("a late failure for old text does not blank the newer preview", P.block === "BLOCK good", P);

  console.log("\nTESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  console.log("  FAIL threw " + (e && e.stack || e));
  console.log("\nTESTS " + pass + " passed " + (fail + 1) + " failed");
  process.exit(1);
});
