// S2 seam (E22): a re-run of the setup sheet whose pending primary's download
// FAILED.  aux_onboarding.py (S1) adds `primary_pending.error` to GET
// /api/onboarding/state; the sheet used to say "still downloading" for a pull
// that had died.  Now it says the download failed (with the server's reason)
// and offers Retry, which asks POST /api/models/download for that one model
// and applies nothing else.  Coded defensively: without the field, with a
// blank or non-string one, and once the model is complete on disk.
//
// Runs the REAL dashboard/aux_onboarding.js in the unit tier's mini DOM (the
// t_e22_onboarding_sheet.js harness), every /api/** answered by a fixture.
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
    "/api/models/download": function (u, init) {
      const b = JSON.parse(init.body); posts.push(Object.assign({ route: "download" }, b));
      return opts.dlAnswer || { ok: true, status: "started" };
    },
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
  const ERR = "OSError: [Errno 28] No space left on device";
  const dlTxt = (s) => s.sheet().querySelector(".onb-dl").textContent;
  const retryBtn = (s) => s.sheet().querySelector('.onb-dl button[data-dlact="ppretry"]');

  console.log("== a re-run: the pending pick's download FAILED (S1's primary_pending.error) ==");
  let s = await scenario(() => [], { state: { primary_pending: { id: M27B, label: "Qwen3.8-27B", ready: false, error: ERR } } });
  await toModels(s);
  check("it says the download failed, not 'still downloading'",
        /download failed/i.test(dlTxt(s)) && !/still downloading/i.test(dlTxt(s)), dlTxt(s));
  check("...names the model and the server's reason", /Qwen3\.8-27B/.test(dlTxt(s)) && dlTxt(s).indexOf("No space left") >= 0, dlTxt(s));
  check("...says nothing was switched to it", /nothing was switched/i.test(dlTxt(s)), dlTxt(s));
  check("...offers Retry and no 'Make it the assistant'",
        !!retryBtn(s) && !s.sheet().querySelector('.onb-dl button[data-dlact="activate"]'));
  s.w.__click(retryBtn(s)); await s.w.__tick(12);
  const dp = s.posts.filter((p) => p.route === "download");
  check("Retry asks for that model's download again (and nothing else)",
        dp.length === 1 && dp[0].id === M27B && Object.keys(dp[0]).sort().join() === "id,route", s.posts);
  check("...applies no picks", s.posts.filter((p) => p.route !== "download").length === 0, s.posts);
  check("...and then says it is downloading again", /downloading again/i.test(dlTxt(s)) && !retryBtn(s), dlTxt(s));

  console.log("== Retry refused by the server ==");
  s = await scenario(() => [], { state: { primary_pending: { id: M27B, label: "Qwen3.8-27B", ready: false, error: ERR } },
                                 dlAnswer: { ok: false, error: "not enough free disk" } });
  await toModels(s);
  s.w.__click(retryBtn(s)); await s.w.__tick(12);
  check("a refused retry keeps the failure and Retry, and says why",
        /download failed/i.test(dlTxt(s)) && !!retryBtn(s) &&
        /not enough free disk/.test(s.sheet().textContent), s.sheet().textContent.slice(0, 600));

  console.log("== defensive: before S1's field exists, and odd values ==");
  s = await scenario(() => [], { state: { primary_pending: { id: M27B, label: "Qwen3.8-27B", ready: false } } });
  await toModels(s);
  check("no error field: still 'still downloading', no Retry", /still downloading/i.test(dlTxt(s)) && !retryBtn(s), dlTxt(s));
  s = await scenario(() => [], { state: { primary_pending: { id: M27B, label: "Qwen3.8-27B", ready: false, error: "   " } } });
  await toModels(s);
  check("a blank error is no error", /still downloading/i.test(dlTxt(s)) && !retryBtn(s), dlTxt(s));
  s = await scenario(() => [], { state: { primary_pending: { id: M27B, label: "Qwen3.8-27B", ready: false, error: true } } });
  await toModels(s);
  check("a non-string truthy error still reads as failed (no 'true' printed)",
        /download failed/i.test(dlTxt(s)) && !/\btrue\b/.test(dlTxt(s)) && !!retryBtn(s), dlTxt(s));
  s = await scenario(() => [], { state: { primary_pending: { id: M27B, label: "Qwen3.8-27B", ready: true, error: ERR } } });
  await toModels(s);
  check("complete on disk wins over a stale error: 'Make it the assistant'",
        !!s.sheet().querySelector('.onb-dl button[data-dlact="activate"]') && !retryBtn(s), dlTxt(s));
  s = await scenario(() => [], { state: { primary_pending: { id: M27B, label: "<b>x</b>", ready: false, error: "<img src=x>" } } });
  await toModels(s);
  check("label and reason are text, never markup",
        !s.sheet().querySelector(".onb-dl img") && !s.sheet().querySelector(".onb-dl b") && /<img src=x>/.test(dlTxt(s)), dlTxt(s));
  check("no handler threw", s.w.__errors.length === 0, s.w.__errors.map(String));
})().catch((e) => { check("harness ran: " + (e && e.stack || e), false); })
  .then(() => {
    console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
    process.exit(fails.length ? 1 : 0);
  });
