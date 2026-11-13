// E22 (docs/audits/2026-11-09-codebase-audit.md) — the setup sheet's half.
//
// BEFORE: a model download started from the setup sheet that then FAILED
// showed "queued" forever: pollModels read only `downloaded`/`downloading`
// and ignored /api/models' `download_error`, the 2.5 s poll never stopped
// (its stop condition needed every row downloaded), the action row kept
// saying Continue, and the Ready step said "Downloading … in the background".
//
// AFTER: a failed row says "failed: <reason>" with a Retry, the error line
// says so, the poll stops once nothing is downloading, the action row offers
// the download again, and the Ready step says the download failed. When the
// download completes, apply's pending primary (aux_onboarding.py, E22 server
// half) is offered as "Make it the assistant" — a second apply, never an
// automatic switch.
//
// Runs the REAL dashboard/aux_onboarding.js in the unit tier's mini DOM, every
// /api/** answered by a fixture.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const { createWindow } = require(path.join(REPO, "tools/tests/unit/_a11y_dom.js"));
const ONB = fs.readFileSync(path.join(REPO, "dashboard/aux_onboarding.js"), "utf8");
const M27B = "mlx-community/Qwen3.8-27B-4bit", M9B = "mlx-community/Qwen3.5-9B-4bit";
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}
function state(extra) {
  return Object.assign({ ok: true, done: true,
    sysinfo: { chip: "M", ram_gb: 64, disk_free_gb: 412, macos: "26", laptop: true },
    detect: { hermes_cli: true, claude_cli: false, models_downloaded: [] },
    recommendation: { primary: M27B, background: M9B, why: "x", primary_label: "Qwen3.8-27B",
      background_label: "Qwen3.5-9B",
      fits: [{ id: M27B, label: "Qwen3.8-27B", ram: 19, size_gb: 17, fit: "ok", downloaded: false },
             { id: M9B, label: "Qwen3.5-9B", ram: 7, size_gb: 6, fit: "ok", downloaded: false }] },
    prefs: { briefings: true, news: true } }, extra || {});
}

async function scenario(models, opts) {
  opts = opts || {};
  const w = createWindow({});
  const posts = [];
  let polls = 0;
  w.__routes = {
    "/api/onboarding/state": function () { return state(opts.state); },
    "/api/onboarding/apply": function (u, init) {
      const b = JSON.parse(init.body); posts.push(b);
      if (b.download === false && b.primary === M27B && !b.background) {
        return { ok: true, errors: [], applied: { primary: M27B, primary_active: true } };
      }
      return { ok: true, errors: [], applied: { primary: M27B, background: M9B,
        download: [M27B, M9B], download_gb: 23, primary_active: false, primary_pending: true,
        primary_note: "downloading — once it finishes, make it the assistant from the setup sheet or the model menu" } };
    },
    "/api/claude/foryou": { enabled: false },
    "/api/models": function () { polls++; return { models: models() }; }
  };
  w.__run(ONB, "aux_onboarding.js"); await w.__tick();
  const ob = w.hermesOnboarding;
  await ob.open({ rerun: true }); await w.__tick();
  const sheet = () => w.document.getElementById("onb-sheet");
  return { w, ob, sheet, posts, polls: () => polls };
}
async function toModels(s) {
  s.ob.go(s.ob.steps().indexOf("models")); await s.w.__tick();
}
async function drain(s, n) {
  for (let i = 0; i < n; i++) { s.w.__intervals.slice().forEach((t) => t.fn()); await s.w.__tick(8); }
}

(async function () {
  console.log("== a download that fails ==");
  const ERR = "OSError: [Errno 28] No space left on device";
  let s = await scenario(() => [
    { id: M27B, downloaded: false, downloading: false, download_error: ERR },
    { id: M9B, downloaded: false, downloading: false, download_error: ERR }]);
  await toModels(s);
  s.w.__click(s.sheet().querySelector(".onb-go")); await s.w.__tick(12);
  check("the download was requested", s.posts.length === 1 && s.posts[0].download === true, s.posts);
  await drain(s, 3);
  const dl = () => s.sheet().querySelector(".onb-dl");
  const txt = dl().textContent;
  check("the rows say failed with the reason (was: 'queued' forever)",
        /failed/.test(txt) && /No space left/.test(txt) && !/queued/.test(txt), txt);
  check("a Retry button sits in the progress block",
        !!dl().querySelector('button[data-dlact="retry"]'), dl().innerHTML.slice(0, 300));
  const err = s.sheet().querySelector(".onb-err");
  check("the error line says the download failed", err && !err.hidden && /fail/i.test(err.textContent),
        err && err.textContent);
  const pollsAt = s.polls();
  await drain(s, 3);
  check("the 2.5 s poll stopped once nothing was downloading",
        s.w.__intervals.length === 0 && s.polls() === pollsAt, { left: s.w.__intervals.length, polls: s.polls() });
  check("the action row offers the download again, not Continue",
        /Download/.test(s.sheet().querySelector(".onb-go").textContent),
        s.sheet().querySelector(".onb-go").textContent);
  check("no 'Make it the assistant' for a model that did not download",
        !dl().querySelector('button[data-dlact="activate"]'));
  // Retry re-posts the same picks with download:true, and polls again
  s.w.__click(dl().querySelector('button[data-dlact="retry"]')); await s.w.__tick(12);
  check("Retry posts the download again", s.posts.length === 2 && s.posts[1].download === true &&
        s.posts[1].primary === M27B, s.posts);
  check("...and polls again", s.w.__intervals.length === 1, s.w.__intervals.length);
  await drain(s, 1);
  // continue past the step to Ready (I'll choose later)
  s.w.__click(s.sheet().querySelector('.onb-foot [data-act="later"]')); await s.w.__tick(12);
  s.ob.go(s.ob.steps().length - 2); await s.w.__tick();
  s.w.__click(s.sheet().querySelector(".onb-go")); await s.w.__tick(12);
  const sum = Array.from(s.sheet().querySelectorAll(".onb-sum li")).map((l) => l.textContent);
  check("the Ready step says the download failed (was: 'Downloading … in the background')",
        sum.some((l) => /failed/i.test(l)) && !sum.some((l) => /^Downloading/.test(l)), sum);
  check("...and does not claim the pending pick is about to be the assistant",
        !sum.some((l) => /once it finishes/.test(l)), sum);

  console.log("== a partial failure keeps the good row and stops polling ==");
  s = await scenario(() => [
    { id: M27B, downloaded: true, downloading: false, download_error: null },
    { id: M9B, downloaded: false, downloading: false, download_error: "b: HTTP 503" }]);
  await toModels(s);
  s.w.__click(s.sheet().querySelector(".onb-go")); await s.w.__tick(12);
  await drain(s, 2);
  const t2 = s.sheet().querySelector(".onb-dl").textContent;
  check("27B downloaded, 9B failed: HTTP 503", /downloaded/.test(t2) && /HTTP 503/.test(t2), t2);
  check("...the poll stopped", s.w.__intervals.length === 0, s.w.__intervals.length);

  console.log("== a download still running keeps polling ==");
  let phase = 0;
  s = await scenario(() => phase === 0
    ? [{ id: M27B, downloaded: false, downloading: true }, { id: M9B, downloaded: true, downloading: false }]
    : [{ id: M27B, downloaded: true, downloading: false }, { id: M9B, downloaded: true, downloading: false }]);
  await toModels(s);
  s.w.__click(s.sheet().querySelector(".onb-go")); await s.w.__tick(12);
  await drain(s, 3);
  check("still polling while a row downloads", s.w.__intervals.length === 1, s.w.__intervals.length);
  check("...no error line", s.sheet().querySelector(".onb-err").hidden);
  check("...no activate button yet",
        !s.sheet().querySelector('.onb-dl button[data-dlact="activate"]'));
  phase = 1;
  await drain(s, 1);
  check("finished: the poll stops", s.w.__intervals.length === 0, s.w.__intervals.length);
  const act = s.sheet().querySelector('.onb-dl button[data-dlact="activate"]');
  check("...and 'Make it the assistant' is offered for the pending primary", !!act,
        s.sheet().querySelector(".onb-dl").innerHTML.slice(0, 400));
  check("nothing was switched on its own (only the one download apply)", s.posts.length === 1, s.posts);
  s.w.__click(act); await s.w.__tick(12);
  check("pressing it sends apply {primary, download:false} and no background",
        s.posts.length === 2 && s.posts[1].primary === M27B && s.posts[1].download === false &&
        !s.posts[1].background, s.posts[1]);
  check("...and the block says it is now the assistant",
        /now the assistant/i.test(s.sheet().querySelector(".onb-dl").textContent) &&
        !s.sheet().querySelector('.onb-dl button[data-dlact="activate"]'),
        s.sheet().querySelector(".onb-dl").textContent);
  s.ob.go(s.ob.steps().length - 2); await s.w.__tick();
  s.w.__click(s.sheet().querySelector(".onb-go")); await s.w.__tick(12);
  const sum3 = Array.from(s.sheet().querySelectorAll(".onb-sum li")).map((l) => l.textContent);
  check("Ready: the assistant model line has no stale 'switch to it' note, and says Downloaded",
        sum3.some((l) => /^Assistant model: Qwen3.8-27B/.test(l) && !/ — /.test(l)) &&
        sum3.some((l) => /^Downloaded/.test(l)), sum3);

  console.log("== a re-run offers a pending pick that has finished ==");
  s = await scenario(() => [], { state: { primary_pending: { id: M27B, label: "Qwen3.8-27B", ready: true } } });
  await toModels(s);
  const a2 = s.sheet().querySelector('.onb-dl button[data-dlact="activate"]');
  check("the models step shows 'Make it the assistant' from the state read", !!a2,
        s.sheet().querySelector(".onb-dl").innerHTML.slice(0, 300));
  s = await scenario(() => [], { state: { primary_pending: { id: M27B, label: "Qwen3.8-27B", ready: false } } });
  await toModels(s);
  check("...but not while it is still downloading",
        !s.sheet().querySelector('.onb-dl button[data-dlact="activate"]') &&
        /still downloading/i.test(s.sheet().querySelector(".onb-dl").textContent),
        s.sheet().querySelector(".onb-dl").textContent);
})().catch((e) => { check("harness ran: " + (e && e.stack || e), false); })
  .then(() => {
    console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
    process.exit(fails.length ? 1 : 0);
  });
