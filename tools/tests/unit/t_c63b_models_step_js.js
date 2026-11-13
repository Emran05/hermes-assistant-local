// Regression test for C63 (round 2, w9; follow-up to audit C63), client half.
//
// BEFORE: the first-run sheet's saveStep() had no "models" branch. "Continue"
// (shown when every pick is already on this Mac) and a re-run that picked a
// DIFFERENT downloaded primary never sent primary/background to
// /api/onboarding/apply, so nothing was applied — while the closing summary
// still said "Assistant model: <pick>". "I'll choose later" was the same
// "next" action, so it could not be told apart either.
//
// AFTER: Continue on the models step posts {primary, background,
// download:false} and the summary reports what apply did with it (including a
// deferred background lane, R2-42, and a refused background, R2-41). "I'll
// choose later" is its own action: it posts nothing and the summary says the
// model is unchanged. A download that already started is not re-posted.
//
// Brace-extracts the REAL act(), saveStep() and summarize() from
// dashboard/aux_onboarding.js and runs them in node:vm with jpost/paint
// stubbed; the full file is also loaded headless to read the footer markup.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_onboarding.js");
const SRC = fs.readFileSync(FILE, "utf8");
const fails = [];
let checks = 0;
function check(name, cond, extra) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : "  " + JSON.stringify(extra === undefined ? "" : extra)));
  if (!cond) fails.push(name);
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

const M27B = "mlx-community/Qwen3.8-27B-4bit";
const M9B = "mlx-community/Qwen3.5-9B-4bit";
const M2B = "mlx-community/Qwen3.5-2B-4bit";
const FITS = [
  { id: M27B, label: "Qwen3.8-27B", downloaded: true },
  { id: M9B, label: "Qwen3.5-9B", downloaded: true },
  { id: M2B, label: "Qwen3.5-2B", downloaded: true }
];

const parts = ["async function act(a)", "async function saveStep()", "function summarize()",
               "function dlCovers(primary, background)"]
  .map((h) => [h, extract(SRC, h)]);
parts.forEach(([h, body]) => check(h + " found", !!body));
// before the review the check was inline `S.dl && S.dl.started` (any started
// download covered any pick); stand that in when the helper is absent, so the
// behavioural checks below still run (and fail) on that code
if (!parts[3][1]) parts[3][1] = "function dlCovers() { return !!(S.dl && S.dl.started); }";
// round 3: act() advances through advance(), saveStep() delegates extra steps
// and the advisor's answers, and summarize() is each step's own summary —
// the helpers ride along (absent on older code, which never called them)
["function liveSteps()", "function curStep()", "function stepCount()", "function advance()",
 "async function saveExt(st)", "async function advisorSave()", "function sumModels()",
 "function sumPrefs()", "function sumWelcome()", "function extLines(st)", "function fitsById()",
 "function labelOf(id)", "function gb(n)",
 // E22 (2026-11-09 audit): the download-failure helpers dlCovers/sumModels use
 "function dlFailed(id)", "function dlAnyFailed()", "function dlAllDone()",
 "function primaryNote(primary, ap)",
 // E23: act() refuses Continue on welcome/prefs until a state has loaded
 "function stateBlocked()"].forEach((h) => {
  const b = extract(SRC, h);
  if (b) parts.push([h, b]);
});

(async () => {
  if (parts.some(([, b]) => !b)) throw new Error("functions not found");
  const ctx = {
    S: null, POSTS: [], REPLY: null,
    STEPS: [{ key: "welcome" }, { key: "models" }, { key: "prefs" }, { key: "done" }],
    console, JSON, Promise, Math, Number, String, Object, Array, Boolean
  };
  vm.createContext(ctx);
  vm.runInContext(
    "function stepKey() { return (STEPS[S.step] || STEPS[0]).key; }\n" +
    "function lastStep() { return S.step === STEPS.length - 1; }\n" +
    "function paint() {}\nfunction paintFoot() {}\nfunction warnOnce() {}\n" +
    "function hookCtx() { return {}; }\nfunction advisor() { return null; }\n" +
    "function setErr(m) { S.err = m == null ? '' : String(m); }\n" +
    "function currentTheme() { return 'system'; }\n" +
    "async function startDownload() { POSTS.push({ download: true }); }\n" +
    "async function finish() {}\n" +
    "async function jget() { return {}; }\n" +
    "async function jpost(url, body) { POSTS.push({ url: url, body: body });\n" +
    "  return REPLY ? REPLY(body) : { ok: true, applied: Object.assign({}, body), errors: [] }; }\n" +
    parts.map(([, b]) => b).join("\n") +
    "\nthis.act = act; this.summarize = summarize;", ctx, { filename: FILE });

  function fresh(pick, extra) {
    ctx.POSTS.length = 0;
    ctx.REPLY = null;
    ctx.S = Object.assign({
      open: true, rerun: false, step: 1, busy: "", err: "", dl: null, applied: [],
      modelsLater: false, modelsApplied: null,
      stateOk: true,                 // E23: open() loaded a real state
      prefs: { idle_min: 10, idle_enabled: true },
      data: { detect: {}, recommendation: { primary: M27B, background: M9B, fits: FITS } },
      pick: pick || { primary: M27B, background: M9B, alt: false }
    }, extra || {});
  }
  const applyPosts = () => ctx.POSTS.filter((p) => p.url === "/api/onboarding/apply");
  const modelLine = () => (ctx.S.applied || []).find((l) => l.indexOf("Assistant model:") === 0) || "";

  console.log("== Continue on the models step sends the picks ==");
  fresh();
  await ctx.act("next");
  let ap = applyPosts();
  check("Continue (all downloaded) posts the picks to apply (was: nothing)",
        ap.length === 1 && ap[0].body.primary === M27B && ap[0].body.background === M9B,
        ap);
  check("...with download:false, never a download", ap[0] && ap[0].body.download === false, ap);
  check("...and nothing else from another step", ap[0] &&
        Object.keys(ap[0].body).sort().join(",") === "background,download,primary", ap);
  check("...then the sheet advances", ctx.S.step === 2, ctx.S.step);

  fresh({ primary: M9B, background: M2B, alt: true }, { rerun: true });
  await ctx.act("next");
  ap = applyPosts();
  check("a re-run that picked a different downloaded primary sends THAT pick",
        ap.length === 1 && ap[0].body.primary === M9B && ap[0].body.background === M2B, ap);

  fresh({ primary: M2B, background: null, alt: false });
  await ctx.act("next");
  ap = applyPosts();
  check("no background lane at this size: background is sent as null",
        ap.length === 1 && ap[0].body.background === null, ap);

  console.log("== I'll choose later sends nothing ==");
  fresh();
  await ctx.act("later");
  check("'later' posts nothing to apply", applyPosts().length === 0, ctx.POSTS);
  check("...but still advances", ctx.S.step === 2, ctx.S.step);
  ctx.S.step = 2;
  await ctx.act("next");                       // prefs -> done
  check("...and the summary says the model is unchanged (was: 'Assistant model: <pick>')",
        /^Assistant model: unchanged/.test(modelLine()), modelLine());

  console.log("== a started download is not re-posted ==");
  fresh(null, { dl: { started: true, ids: [M27B, M9B], done: {}, downloading: {},
                      applied: { primary: M27B, background: M9B, primary_active: true } } });
  await ctx.act("next");
  check("download already carried the picks: Continue posts nothing more",
        applyPosts().length === 0, ctx.POSTS);

  console.log("== the summary reports what apply did ==");
  const note = "the assistant model is running now — switch to it from the model menu to use it";
  fresh();
  ctx.REPLY = (b) => ({ ok: true, errors: [],
                        applied: { primary: b.primary, background: b.background,
                                   primary_active: false, primary_note: note } });
  await ctx.act("next");
  ctx.S.step = 2; await ctx.act("next");
  check("primary not activated: the Continue result's note reaches the summary",
        modelLine().indexOf(note) >= 0, modelLine());

  const bnote = "the background lane is running Qwen3.5-9B right now — Qwen3.5-2B takes over";
  fresh({ primary: M27B, background: M2B, alt: false });
  ctx.REPLY = (b) => ({ ok: true, errors: [],
                        applied: { primary: b.primary, background: b.background,
                                   primary_active: true, background_lane: M9B,
                                   background_pending: M2B, background_note: bnote } });
  await ctx.act("next");
  ctx.S.step = 2; await ctx.act("next");
  check("a deferred background lane (R2-42) is said in the summary",
        modelLine().indexOf(bnote) >= 0, modelLine());

  fresh({ primary: M27B, background: M27B, alt: false });
  ctx.REPLY = (b) => ({ ok: false, errors: ["background: Qwen3.8-27B does not fit"],
                        applied: { primary: b.primary, primary_active: true } });
  await ctx.act("next");
  check("a refused background shows the error", /does not fit/.test(ctx.S.err), ctx.S.err);
  ctx.S.step = 2; await ctx.act("next");
  check("...and the summary does not claim that lane",
        /background lane unchanged/.test(modelLine()) && !/background lane: /.test(modelLine()),
        modelLine());

  fresh();
  ctx.REPLY = () => ({ ok: false, errors: ["unknown model: x"], applied: {} });
  await ctx.act("next");
  ctx.S.step = 2; await ctx.act("next");
  check("a refused primary reads 'unchanged', not the pick",
        /^Assistant model: unchanged/.test(modelLine()), modelLine());

  console.log("== review: a download only covers the picks it carried ==");
  // download started for 27B + 9B, then the battery pair (all local) is toggled
  fresh({ primary: M9B, background: M2B, alt: true },
        { dl: { started: true, ids: [M27B, M9B], done: {}, downloading: {},
                applied: { primary: M27B, background: M9B, primary_active: true } } });
  await ctx.act("next");
  ap = applyPosts();
  check("pair changed after the download started: Continue posts the NEW pair "
        + "(was: nothing, the download's old pair stood)",
        ap.length === 1 && ap[0].body.primary === M9B && ap[0].body.background === M2B, ap);
  ctx.S.step = 2; await ctx.act("next");
  check("...and the summary reports that request, not the download's",
        /^Assistant model: Qwen3\.5-9B, background lane: Qwen3\.5-2B/.test(modelLine()),
        modelLine());

  // Continue applied 27B + 9B; Back; a download then applied the battery pair
  fresh();
  await ctx.act("next");                       // Continue: 27B + 9B posted
  ctx.S.step = 1;                              // Back
  ctx.S.pick = { primary: M9B, background: M2B, alt: true };
  ctx.S.dl = { started: true, ids: [M9B, M2B], done: {}, downloading: {},
               applied: { primary: M9B, background: M2B, primary_active: true } };
  ctx.S.modelsApplied = null;                  // what startDownload() now does
  const dlSrc = extract(SRC, "async function startDownload()");
  check("startDownload() makes its result the newest one (clears modelsApplied)",
        !!dlSrc && /S\.modelsApplied = null/.test(dlSrc), dlSrc && dlSrc.slice(-300));
  await ctx.act("next");
  check("...so Continue after it posts nothing more", applyPosts().length === 1, ctx.POSTS);
  ctx.S.step = 2; await ctx.act("next");
  check("...and the summary names the downloaded pair (was: 'unchanged', from the "
        + "stale Continue result)",
        /^Assistant model: Qwen3\.5-9B, background lane: Qwen3\.5-2B/.test(modelLine()),
        modelLine());

  console.log("== review: 'later' after an earlier Continue, and a failed save ==");
  fresh();
  await ctx.act("next");                       // Continue: 27B + 9B posted
  ctx.S.step = 1;
  ctx.S.pick = { primary: M9B, background: M2B, alt: true };
  await ctx.act("later");
  ctx.S.step = 2; await ctx.act("next");
  check("'later' after an earlier Continue reports what that Continue applied",
        /^Assistant model: Qwen3\.8-27B, background lane: Qwen3\.5-9B/.test(modelLine()),
        modelLine());

  fresh();
  ctx.REPLY = () => { throw new Error("network down"); };
  await ctx.act("next");
  check("a save that threw shows the error", /Could not save/.test(ctx.S.err), ctx.S.err);
  ctx.S.step = 2; ctx.REPLY = null; await ctx.act("next");
  check("...and the summary does not claim the pick",
        /^Assistant model: unchanged/.test(modelLine()), modelLine());

  console.log("== the footer gives 'later' its own action ==");
  const full = { console, setTimeout, clearTimeout, setInterval, clearInterval, Promise,
                 JSON, Date, Math, Number, String, Array, Object, Boolean, isNaN,
                 fetch: () => Promise.resolve({ json: () => Promise.resolve(null) }) };
  full.window = full; full.globalThis = full;
  vm.createContext(full);
  vm.runInContext(SRC, full, { filename: FILE });
  const ob = full.hermesOnboarding;
  Object.assign(ob.state, {
    step: 1, dl: null, pick: { primary: M27B, background: M9B },
    data: { sysinfo: { ram_gb: 64 }, detect: {},
            recommendation: { primary: M27B, background: M9B,
                              fits: FITS.map((f) => Object.assign({}, f, { downloaded: false })) } }
  });
  const html = ob.sheetHTML();
  check("'I'll choose later' carries data-act=\"later\" (was: \"next\", same as Continue)",
        /data-act="later"[^>]*>I&rsquo;ll choose later/.test(html), html.slice(-600));
  check("...and the primary action is still the download",
        /data-act="download"/.test(html), html.slice(-600));

  // everything local: a first run offers Continue only; a RE-RUN also offers
  // keeping the current models, so Continue is never a silent revert
  Object.assign(ob.state, { rerun: false, dl: null, pick: { primary: M27B, background: M9B },
    data: { sysinfo: { ram_gb: 64 }, detect: {},
            recommendation: { primary: M27B, background: M9B, fits: FITS } } });
  let h2 = ob.sheetHTML();
  check("first run, all downloaded: Continue and no secondary model action",
        /data-act="next"[^>]*>Continue/.test(h2) && !/data-act="later"/.test(h2),
        h2.slice(-600));
  ob.state.rerun = true;
  h2 = ob.sheetHTML();
  check("re-run, all downloaded: 'Keep current models' is offered as 'later' (review)",
        /data-act="later"[^>]*>Keep current models/.test(h2) &&
        /data-act="next"[^>]*>Continue/.test(h2), h2.slice(-600));
  ob.state.dl = { started: true, ids: [M27B, M9B], done: {}, downloading: {}, applied: {} };
  h2 = ob.sheetHTML();
  check("...but not once a download has carried these picks",
        !/data-act="later"/.test(h2), h2.slice(-600));
  ob.state.dl = { started: true, ids: [M9B, M2B], done: {}, downloading: {}, applied: {} };
  ob.state.rerun = false;
  ob.state.data.recommendation.fits = FITS.map((f) =>
    Object.assign({}, f, { downloaded: f.id !== M27B }));
  h2 = ob.sheetHTML();
  check("a download of ANOTHER pair does not turn a missing pick into Continue",
        /data-act="download"/.test(h2), h2.slice(-600));
})().catch((e) => {
  check("harness ran: " + (e && e.stack || e), false);
}).then(() => {
  console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
});
