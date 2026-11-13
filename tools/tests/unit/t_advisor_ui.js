// t_advisor_ui.js — the model advisor's UI (round 3, C3; design
// docs/plans/model-advisor-2026-10.md §8, §10 and shared contract 2).
//
// Runs the REAL dashboard/aux_advisor.js on the REAL index.html (markup +
// inline script) in the mini DOM (_a11y_dom.js), with every /api answered
// locally. The advisor payloads are not hand-written: a python3 child loads
// the REAL server.py (aux_advisor.py + aux_advisor_hw.py included) under the
// unit tier's offline guard, against a throwaway HOME, and ranks three
// SYNTHETIC Macs (16 GB M2 Air, 36 GB M3 Pro, 64 GB M5 Max) through the real
// route functions — so a shape change on the server side breaks this suite
// instead of silently drifting past it. Nothing reaches a dashboard, a model
// or the network.
//
// Covers: the step contract shape; the pure formatters (12-hour time,
// half-even rounding that matches the server's own sentences); esc() on
// every field (a hostile fixture); no request on load beyond GET state; the
// 150 ms debounce and a re-rank that replaces only the results; setPick
// calls; the uncensored chip held off until the STORED acknowledgement and
// an uncensored alternative locked until then; save() resolving {err} on a
// 400 and {} on 404 / 5xx / network; the Settings card mounted first in
// #sec-models with order:-1 and never in #view-mind; every per-plan button
// firing only on its own click, in the right order (Download behind
// confirm()), the 409 needs_ack contract for the background lane; the
// modelDetailsHTML wrapper (four rows only with advice, idempotent across two
// loads, a no-op without the function); and index.html's model menu: the
// Uncensored badge + data-uncensored, the badge-aware onModelClick (one
// combined confirm, ack on the first request; the 409 fallback retries once),
// with the fit/disk refusals unchanged; and (R3-seams motion-overlays-4) a
// plan action pressed from the keyboard gives focus back to its button (or the
// same action of a repainted plan), never where the owner moved it.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const cp = require("child_process");
const { bootDashboard, createWindow, harness } = require("./_a11y_dom.js");

const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_advisor.js"), "utf8");
const M27 = "mlx-community/Qwen3.8-27B-4bit", M9 = "mlx-community/Qwen3.5-9B-4bit";
const M4 = "mlx-community/Qwen3.5-4B-4bit", ORCA = "orcarouter/Qwen3.8-27B-Uncensored-MLX";
const VANCH = "vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit";
const EVIL = '<img src=x onerror="alert(1)">';
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F000}-\u{1F2FF}]/u;

// --------------------------------------------------------------------------
// fixtures: the real ranking code, synthetic hardware
// --------------------------------------------------------------------------
const GEN = String.raw`
import contextlib, importlib.util, io, json, os, shutil, sys, tempfile, urllib.parse
REPO = sys.argv[1]
TMP = tempfile.mkdtemp(prefix="hermes-advui-fx-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hsrv_advui", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec); sys.modules["hsrv_advui"] = srv
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(srv)
M27 = "mlx-community/Qwen3.8-27B-4bit"; M9 = "mlx-community/Qwen3.5-9B-4bit"
MACS = {
  "m16": ("Apple M2", 16, 10, "MacBook Air", 48.0),
  "m36": ("Apple M3 Pro", 36, 18, "MacBook Pro", 210.0),
  "m64": ("Apple M5 Max", 64, 40, "MacBook Pro", 412.0),
}
CUR = {"m16": (M27, None), "m36": (M27, M9), "m64": (M27, M9)}
DL = {"m16": set(), "m36": {M27}, "m64": {M27}}
HW = {}
def fake_local(answers=None, hw=None, bust=False):
    k = HW["k"]; h = HW[k]
    inputs = {"measured": {}, "prefix": {}, "drills": {}, "bg_wanted": True,
              "downloaded": set(DL[k]), "current": CUR[k], "profile": None}
    res = srv._adv_rank(h, answers if answers is not None else srv._adv_answers_stored(),
                        downloaded=set(DL[k]), current=CUR[k])
    return res, h, inputs
for k, m in MACS.items():
    HW[k] = srv._adv_machine(m[0], m[1], gpu_cores=m[2], machine_name=m[3],
                             disk_free_gb=m[4], macos="26.1")
srv._adv_rank_local = fake_local      # the routes resolve it by name: no detection runs
class Ctx:
    def __init__(self, query=None, body=None):
        self.query, self.body, self.raw_path, self.headers = query or {}, body, "", {}
def rank(q):
    o = srv._adv_rank_route(Ctx(urllib.parse.parse_qs(q)))
    return o if isinstance(o, dict) else o[0]
out = {}
for k in MACS:
    HW["k"] = k
    out[k] = {"state": srv._adv_state_route(Ctx({})),
              "rank_code_best": rank("uses=chat,agent,code&priority=best&power=auto")}
HW["k"] = "m64"
srv._adv_answers_save({"uncensored_ack": True})
out["m64"]["rank_unc"] = rank("uses=chat,agent,uncensored&priority=balanced&power=auto")
out["m64"]["state_ack"] = srv._adv_state_route(Ctx({}))
srv._adv_answers_save({"uncensored_ack": False})
srv._machine_ram_gb = lambda: 64
srv.bg_online = lambda *a, **k: False     # no :8081 probe (the guard would refuse it anyway)
mp = srv.models_payload()
out["models64"] = {"models": mp["models"], "advisor": mp.get("advisor"), "mem": mp.get("mem"),
                   "disk_free_gb": 412.0, "disk_headroom_gb": 5, "paused": False,
                   "idle_suspended": False, "thinking": {"supported": False},
                   "bg": {"model": M9, "label": "Qwen3.5-9B", "online": False}, "ram_gb": None}
shutil.rmtree(TMP, True)
sys.stdout.write(json.dumps(out, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))
`;

function genFixtures() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-advui-gen-"));
  const script = path.join(dir, "gen.py");
  fs.writeFileSync(script, GEN);
  const env = Object.assign({}, process.env, {
    HERMES_TESTS_OFFLINE: "1",                       // the offline guard, in the child too
    PYTHONPATH: path.join(REPO, "tools", "tests", "lib") + (process.env.PYTHONPATH ? ":" + process.env.PYTHONPATH : "")
  });
  delete env.PYTHONNOUSERSITE;
  delete env.NODE_OPTIONS;
  const r = cp.spawnSync("python3", [script, REPO], { env: env, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, timeout: 120000 });
  try { fs.rmSync(dir, { recursive: true, force: true }); } catch (e) {}
  if (r.status !== 0) throw new Error("fixture generator failed: " + (r.stderr || "").slice(-1500));
  return JSON.parse(r.stdout);
}
const clone = (o) => JSON.parse(JSON.stringify(o));

// --------------------------------------------------------------------------
// helpers
// --------------------------------------------------------------------------
function adv(w) { return w.__fetches.filter(function (f) { return /\/api\/advisor\//.test(f.url); }); }
function posts(w, p) {
  return w.__fetches.filter(function (f) { return f.url.split("?")[0] === p && f.init && f.init.method === "POST"; });
}
function body(f) { try { return JSON.parse(f.init.body || "{}"); } catch (e) { return {}; } }
function change(w, el, checked) {
  if (checked !== undefined) el.checked = checked;
  el.dispatchEvent(new w.Event("change", { bubbles: true }));
}
function recCtx(data) {
  const c = { calls: [], repaints: 0, pick: null, rerun: false, data: data || {},
    repaint() { c.repaints++; }, setErr(m) { c.err = m; },
    getPick() { return c.pick; }, setPick(p) { c.calls.push(p); c.pick = p; } };
  return c;
}
function animRecorder(w) {
  const calls = [];
  w.HermesAnim = { v: 1, enter(el, preset, o) { calls.push({ el: el, preset: preset }); return { done: true }; } };
  return calls;
}
function onboardingData(fx, ram) {
  return { sysinfo: { chip: fx.state.hw.chip.brand, ram_gb: ram, disk_free_gb: fx.state.hw.disk_free_gb,
                      macos: "26.1", laptop: true },
           recommendation: { primary: fx.state.result.recommended.primary.id, fits: [] } };
}
async function boot(routes, opts) {
  opts = opts || {};
  return bootDashboard({ repo: REPO, aux: opts.noAdvisor ? [] : ["aux_advisor.js"], routes: routes || {} });
}
async function mountStep(w, ctx) {
  const d = w.document, host = d.createElement("div");
  host.className = "onb-step-body";
  d.body.appendChild(host);
  host.innerHTML = w.hermesAdvisor.step.render(ctx);
  w.hermesAdvisor.step.wire(host, ctx);
  await w.__tick(); await w.__tick();
  return host;
}
async function scenario(name, fn) {
  console.log("\n" + name);
  try { await fn(); }
  catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}

(async function main() {
  let FX;
  try { FX = genFixtures(); }
  catch (e) { T.check("fixtures from the real server.py", false, String(e).slice(0, 900)); T.done(); return; }
  T.check("fixtures from the real ranking code: 16/36/64 GB states",
    ["m16", "m36", "m64"].every(function (k) { return FX[k].state && FX[k].state.ok === true && FX[k].state.result.recommended; }),
    Object.keys(FX));
  T.check("...the 64 GB Mac recommends the 27B + 9B, the 16 GB Mac the 4B alone",
    FX.m64.state.result.recommended.primary.id === M27 && FX.m64.state.result.recommended.background.id === M9 &&
    FX.m16.state.result.recommended.primary.id === M4 && !FX.m16.state.result.recommended.background);
  T.check("...and an uncensored alternative exists only after the stored acknowledgement",
    !FX.m64.state.result.alternatives.some(function (a) { return a.uncensored; }) &&
    FX.m64.rank_unc.result.alternatives.some(function (a) { return a.uncensored; }));

  // ======================================================================
  await scenario("[1] load: the contract shape, and no request on load", async function () {
    const w = await boot({ "/api/advisor/state": FX.m64.state });
    const A = w.hermesAdvisor;
    T.check("window.hermesAdvisor.v === 1", A && A.v === 1);
    T.check("step has render / wire / save", A && typeof A.step.render === "function" &&
      typeof A.step.wire === "function" && typeof A.step.save === "function");
    T.check("mountCard and explain are functions", typeof A.mountCard === "function" && typeof A.explain === "function");
    T.check("fmt is an object of pure functions", A.fmt && Object.keys(A.fmt).length >= 10 &&
      Object.keys(A.fmt).every(function (k) { return typeof A.fmt[k] === "function"; }));
    T.check("loading the file makes NO advisor request", adv(w).length === 0, adv(w).map(function (f) { return f.url; }));
    T.check("...and no POST of any kind", w.__fetches.filter(function (f) { return f.init && f.init.method === "POST"; }).length === 0);
    T.check("no page error on boot", w.__errors.length === 0, w.__errors.map(String).slice(0, 3));
  });

  // ======================================================================
  await scenario("[2] the pure formatters", async function () {
    const w = await boot({});
    const f = w.hermesAdvisor.fmt;
    const NB = " ";
    T.check("gb quotes like the server's plan lines: 9.58 -> 9.6 GB, 4.5 -> 4.5 GB, 16.1 -> 16 GB, 35.76 -> 36 GB",
      f.gb(9.58) === "9.6" + NB + "GB" && f.gb(4.5) === "4.5" + NB + "GB" && f.gb(16.1) === "16" + NB + "GB" &&
      f.gb(35.76) === "36" + NB + "GB", [f.gb(9.58), f.gb(16.1), f.gb(35.76)]);
    T.check("tps rounds half to even like the server's sentences (32.5 -> 32, 33.5 -> 34)",
      f.tps(32.5) === "32" && f.tps(33.5) === "34" && f.tps(49.4) === "49");
    T.check("secs: 27.5 -> 28 s, 181 -> 3 min", f.secs(27.5) === "28" + NB + "s" && f.secs(181) === "3" + NB + "min");
    T.check("ctxK: 25000 -> 25K, 131072 -> 128K, 20600 (one decimal) -> 20.6K",
      f.ctxK(25000) === "25K" && f.ctxK(131072) === "128K" && f.ctxK(20600, true) === "20.6K");
    T.check("time12 is a 12-hour clock: 15:04 -> 3:04 PM, 0:05 -> 12:05 AM, 12:00 -> 12:00 PM",
      f.time12(new Date(2026, 9, 26, 15, 4)) === "3:04 PM" && f.time12(new Date(2026, 9, 26, 0, 5)) === "12:05 AM" &&
      f.time12(new Date(2026, 9, 26, 12, 0)) === "12:00 PM");
    T.check("time12 takes unix seconds too", f.time12(new Date(2026, 9, 26, 9, 30).getTime() / 1000) === "9:30 AM");
    T.check("rangeText", f.rangeText([27.6, 37.3], "tok/s", 1) === "27.6–37.3" + NB + "tok/s");
    const est = FX.m64.state.result.recommended.estimates.primary;
    T.check("speedLine of an estimate says so", f.speedLine(est) === "~49 tok/s short · ~32 in a 25K turn (estimate)", f.speedLine(est));
    const meas = Object.assign({}, est, { basis: Object.assign({}, est.basis, { decode: "measured-here" }),
                                          decode_turn_tps: 41.2, measured: { n_turn: 23 } });
    T.check("speedLine of a measured turn: '41 tok/s measured here ... (23 replies)'",
      f.speedLine(meas) === "41 tok/s measured here in a 25K turn (23 replies)", f.speedLine(meas));
    T.check("wakeLine: '~28 s to read the 20.6K-token setup'", f.wakeLine(est) === "~28" + NB + "s to read the 20.6K-token setup", f.wakeLine(est));
    T.check("memLine: '~26 GB of a 48 GB budget'", f.memLine(est, 48) === "~26" + NB + "GB of a 48" + NB + "GB budget", f.memLine(est, 48));
    T.check("planHeading names both lanes", f.planHeading(FX.m64.state.result.recommended) ===
      "Qwen3.8-27B as the assistant, Qwen3.5-9B for background work");
    T.check("planHeading with no lane says so", f.planHeading(FX.m16.state.result.recommended) ===
      "Qwen3.5-4B as the assistant, with no background lane");
    T.check("fitLine keeps the model menu's 1.0.3 phrasing",
      f.fitLine("tight", 19, 36) === "needs ~19" + NB + "GB · this Mac has 36" + NB + "GB · tight, expect swapping");
    T.check("rankQuery", f.rankQuery({ uses: ["chat", "code"], priority: "best", power: null }) ===
      "uses=chat%2Ccode&priority=best&power=auto");
  });

  // ======================================================================
  await scenario("[3] the onboarding step on a 64 GB Mac", async function () {
    const w = await boot({ "/api/advisor/state": FX.m64.state, "/api/advisor/rank": FX.m64.rank_code_best });
    const d = w.document, anim = animRecorder(w);
    const ctx = recCtx(onboardingData(FX.m64, 64));
    const html0 = w.hermesAdvisor.step.render(ctx);
    T.check("render before the state: the detected facts from the onboarding payload",
      html0.indexOf("Apple M5 Max") >= 0 && html0.indexOf("Reading this Mac") >= 0, html0.slice(0, 300));
    T.check("render makes no request by itself", adv(w).length === 0);
    const host = await mountStep(w, ctx);
    T.check("wire makes exactly ONE request: GET /api/advisor/state",
      adv(w).length === 1 && adv(w)[0].url === "/api/advisor/state" && !(adv(w)[0].init || {}).method, adv(w).map(function (f) { return f.url; }));
    // a repaint by the sheet renders + wires again: no second fetch
    host.innerHTML = w.hermesAdvisor.step.render(ctx);
    w.hermesAdvisor.step.wire(host, ctx);
    await w.__tick();
    T.check("a repaint (render + wire again) does not fetch again", adv(w).length === 1);
    T.check("ctx.repaint() is never called by the advisor", ctx.repaints === 0);
    const t = host.textContent;
    T.check("Detected: chip, GPU cores, memory, GPU budget (assumed), bandwidth, free disk, power",
      ["Apple M5 Max · 40-core GPU", "64 GB", "48 GB", "(assumed)", "614 GB/s", "412 GB", "Plugged in"]
        .every(function (s) { return t.indexOf(s) >= 0; }), t.slice(0, 400));
    // design climb 2026-11-12: one label/value row per fact, and a value
    // breaks only between its phrases (each phrase is a nowrap .adv-ph)
    const chipRow = Array.prototype.find.call(host.querySelectorAll(".adv-det ul.adv-stats > li"),
      function (li) { return li.querySelector(".adv-k").textContent === "Chip"; });
    const ph = chipRow ? Array.prototype.map.call(chipRow.querySelectorAll(".adv-v .adv-ph"), function (e) { return e.textContent; }) : [];
    T.check("the Chip fact is one row: label, then its phrases ['Apple M5 Max', '40-core GPU']",
      !!chipRow && chipRow.querySelectorAll(".adv-k").length === 1 && JSON.stringify(ph) === JSON.stringify(["Apple M5 Max", "40-core GPU"]), ph);
    const chips = host.querySelectorAll('input[type="checkbox"][data-use]');
    T.check("the six uses are real checkboxes", chips.length === 6);
    T.check("...inside a role=group labelled by its question", chips.length && chips[0].closest('[role="group"]') &&
      d.getElementById(chips[0].closest('[role="group"]').getAttribute("aria-labelledby")).textContent === "What will you use it for?");
    const pri = host.querySelectorAll('input[type="radio"][data-pri]');
    T.check("priority is a radio group (Faster / Balanced / Best) in role=radiogroup",
      pri.length === 3 && pri[0].closest('[role="radiogroup"]') && pri[1].hasAttribute("checked"));
    T.check("the power question is asked on a laptop", host.querySelectorAll('input[data-pow]').length === 2);
    T.check("the Recommended plan heading", t.indexOf("Qwen3.8-27B as the assistant, Qwen3.5-9B for background work") >= 0);
    T.check("...with its why line", t.indexOf(FX.m64.state.result.recommended.why) >= 0);
    T.check("lane tags: verified for tools", host.querySelectorAll(".adv-rec .adv-tag.ok").length === 2);
    T.check("the total line: '~6 GB to download · 412 GB free'", t.indexOf("~6 GB to download · 412 GB free") >= 0);
    const segs = [].concat.apply([], Array.prototype.slice.call(host.querySelectorAll(".adv-lane-s")).map(function (s) { return s.textContent.split(" · "); }));
    const bare = segs.filter(function (s) { return /\d/.test(s) && !/~|measured here/.test(s); });
    T.check("every number in the lane lines carries ~ or 'measured here'", segs.length > 6 && bare.length === 0, bare);
    const alts = host.querySelectorAll("li.adv-alt");
    T.check("up to two alternatives, flat rows", alts.length >= 1 && alts.length <= 2 && !host.querySelector(".adv-alt .adv-plan"));
    T.check("each alternative has a 'Use this instead' radio",
      Array.prototype.every.call(alts, function (li) { const r = li.querySelector('input[type="radio"][data-pick]'); return r && /Use this instead/.test(li.textContent); }));
    T.check("the won't-fit list is collapsed (<details> without open)", (function () {
      const det = host.querySelector("details.adv-wont"); return det && !det.hasAttribute("open") && /won.t work well here/.test(det.textContent); })());
    const how = host.querySelector("details.adv-how");
    const pf = FX.m64.state.result.recommended.estimates.primary.formula;
    T.check("'How we estimate' carries the substituted formulas", how && how.textContent.indexOf(pf.decode) >= 0 &&
      how.textContent.indexOf(pf.resident) >= 0 && how.textContent.indexOf(FX.m64.state.limits.formula) >= 0);
    T.check("...with the basis and the range", how && /basis: measured on the reference Mac/.test(how.textContent) && /range 27\.6/.test(how.textContent));
    T.check("the body leaves the sheet's own action row out (no Download recommended / Continue)",
      !/Download recommended|I.ll choose later|Continue/.test(t) && !host.querySelector("button[data-act]"));
    T.check("setPick once, with the Recommended plan", ctx.calls.length === 1 &&
      ctx.calls[0].primary === M27 && ctx.calls[0].background === M9 && ctx.calls[0].download_gb === 6 &&
      ctx.calls[0].uncensored === false, ctx.calls);
    T.check("nothing animated on the state arriving", anim.length === 0, anim.length);
    T.check("zero emoji in the step", !EMOJI.test(host.innerHTML));
    T.check("the glyphs are two-tone SVG (a low-opacity fill under a stroke)", (function () {
      const s = host.querySelector("svg.adv-ic"); return s && /opacity/.test(s.innerHTML) && /stroke="currentColor"/.test(s.innerHTML); })());

    // ---- the debounce: three changes, one re-rank, after 150 ms --------------
    const before = adv(w).length;
    const keep = host.querySelector('input[data-use="code"]');
    change(w, keep, true);
    change(w, host.querySelector('input[data-use="docs"]'), true);
    change(w, host.querySelector('input[data-pri="best"]'), true);
    await w.__tick();
    T.check("no re-rank before the debounce fires", adv(w).length === before);
    const deb = w.__timers.filter(function (x) { return x.ms === 150; });
    T.check("one pending 150 ms timer for three changes", deb.length === 1, w.__timers.map(function (x) { return x.ms; }));
    keep.focus();
    w.__flushTimers(1); await w.__tick(); await w.__tick();
    const rk = adv(w).slice(before);
    T.check("exactly one GET /api/advisor/rank after the debounce", rk.length === 1 && rk[0].url.indexOf("/api/advisor/rank?") === 0, rk.map(function (f) { return f.url; }));
    T.check("...with the answers on screen", rk.length && rk[0].url === "/api/advisor/rank?uses=chat%2Cagent%2Ccode%2Cdocs&priority=best&power=auto", rk.length && rk[0].url);
    T.check("the rank is a GET (pure, no writes)", rk.length && !(rk[0].init || {}).method);
    T.check("the re-rank replaced only the results: the chip node survived", keep.isConnected !== false && host.contains(keep));
    T.check("...and kept the keyboard focus on it", d.activeElement === keep);
    T.check("the results faded in once, through HermesAnim, on .adv-res", anim.length === 1 &&
      anim[0].preset === "fade" && anim[0].el.classList.contains("adv-res"), anim.map(function (a) { return a.preset; }));
    T.check("the status line says what was ranked", /Ranked for .*Coding help/.test(host.querySelector('[data-adv-part="status"]').textContent));
    T.check("no POST anywhere yet", w.__fetches.filter(function (f) { return f.init && f.init.method === "POST"; }).length === 0);

    // ---- picking an alternative ---------------------------------------------
    const altRadio = host.querySelector("li.adv-alt input[data-pick]");
    const n0 = ctx.calls.length;
    change(w, altRadio, true);
    const want = altRadio.getAttribute("data-pick").split("|");
    T.check("'Use this instead' hands the sheet that plan (setPick)", ctx.calls.length === n0 + 1 &&
      ctx.calls[n0].primary === want[0] && (ctx.calls[n0].background || "") === want[1], ctx.calls.slice(n0));
    T.check("...and marks the row picked", altRadio.closest("li").classList.contains("is-picked"));
    const recRadio = host.querySelector(".adv-rec input[data-pick]");
    change(w, recRadio, true);
    T.check("going back to the Recommended plan hands it back", ctx.calls[ctx.calls.length - 1].primary ===
      w.hermesAdvisor.result.recommended.primary.id);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String).slice(0, 3));
  });

  // ======================================================================
  await scenario("[4] the onboarding step on a 16 GB Mac (and a desktop)", async function () {
    const st = clone(FX.m16.state);
    const w = await boot({ "/api/advisor/state": st });
    const ctx = recCtx(onboardingData(FX.m16, 16));
    const host = await mountStep(w, ctx);
    const t = host.textContent;
    T.check("16 GB recommends the 4B", t.indexOf("Qwen3.5-4B as the assistant, with no background lane") >= 0, t.slice(0, 600));
    T.check("...says there is no background lane", t.indexOf("none: briefings and news run on the assistant") >= 0);
    T.check("...and is honest that the 4B is not yet verified", host.querySelector(".adv-rec .adv-tag.warn") &&
      /not yet verified/.test(host.querySelector(".adv-rec").textContent));
    T.check("...counts what will not work", /\d+ models won.t work well here/.test(t));
    T.check("...and the 27B is in that list for memory", /Qwen3\.8-27B/.test(host.querySelector("details.adv-wont").textContent) &&
      /budget/.test(host.querySelector("details.adv-wont").textContent));
    T.check("setPick hands over the 4B alone", ctx.calls.length === 1 && ctx.calls[0].primary === M4 && ctx.calls[0].background === null);
    T.check("the MacBook Air is named", t.indexOf("MacBook Air") >= 0);

    const desk = clone(FX.m64.state);
    desk.hw.form = "desktop"; desk.hw.machine_name = "Mac Studio"; desk.questions.power_asked = false;
    const w2 = await boot({ "/api/advisor/state": desk });
    const h2 = await mountStep(w2, recCtx({}));
    T.check("a desktop is never asked where it is used", h2.querySelectorAll("input[data-pow]").length === 0 && !/Where do you use it/.test(h2.textContent));
    T.check("...and shows no Power fact", !/Plugged in|On battery/.test(h2.querySelector(".adv-det").textContent));
  });

  // ======================================================================
  await scenario("[5] the uncensored chip waits for the STORED acknowledgement", async function () {
    let acked = false;
    const w = await boot({
      "/api/advisor/state": function () { return acked ? FX.m64.state_ack : FX.m64.state; },
      "/api/advisor/rank": function (url) {
        return url.indexOf("uncensored") >= 0 && acked ? FX.m64.rank_unc : { ok: true, result: FX.m64.state.result, saved: false };
      },
      "/api/advisor/answers": function (url, init) {
        const b = JSON.parse(init.body || "{}");
        if (b.uncensored_ack === true) acked = true;
        if (b.uncensored_ack === false) acked = false;
        return { ok: true, answers: { uses: ["chat", "agent"], priority: "balanced", power: null,
                                      uncensored_ack: acked, saved_at: 1798000000 } };
      }
    });
    const d = w.document, anim = animRecorder(w);
    const ctx = recCtx(onboardingData(FX.m64, 64));
    const host = await mountStep(w, ctx);
    const chip = host.querySelector('input[data-use="uncensored"]');
    const r0 = adv(w).length;
    change(w, chip, true);
    await w.__tick();
    T.check("ticking the chip does NOT turn it on", chip.checked === false);
    const panel = host.querySelector(".adv-ack");
    T.check("...it shows the server's own acknowledgement copy", panel &&
      panel.textContent.indexOf(FX.m64.state.questions.uncensored_ack_text) >= 0);
    const box = panel && panel.querySelector('input[type="checkbox"][data-ack]');
    T.check("...with an acknowledgement checkbox, unchecked", box && !box.checked && /Show uncensored options/.test(panel.textContent));
    T.check("...and focus moves to it", d.activeElement === box);
    T.check("...the panel opening is the one entrance (rise)", anim.length === 1 && anim[0].preset === "rise");
    T.check("...and nothing is posted or re-ranked yet", adv(w).length === r0 && w.__timers.filter(function (x) { return x.ms === 150; }).length === 0);
    host.querySelector('button[data-adv-act="unc-cancel"]').click();
    T.check("Cancel closes the panel, the chip stays off", !host.querySelector(".adv-ack") && !chip.checked);

    change(w, host.querySelector('input[data-use="uncensored"]'), true);
    const box2 = host.querySelector('input[data-ack]');
    change(w, box2, true);
    await w.__tick(); await w.__tick();
    const ap = posts(w, "/api/advisor/answers");
    T.check("checking it POSTs exactly {uncensored_ack: true}", ap.length === 1 && JSON.stringify(body(ap[0])) === '{"uncensored_ack":true}', ap.map(body));
    T.check("...then the chip is on", host.querySelector('input[data-use="uncensored"]').checked === true);
    w.__flushTimers(1); await w.__tick(); await w.__tick();
    const last = adv(w).filter(function (f) { return /rank/.test(f.url); }).pop();
    T.check("...and the re-rank asks for the uncensored use", last && /uses=chat%2Cagent%2Cuncensored/.test(last.url), last && last.url);
    const ualt = Array.prototype.find.call(host.querySelectorAll("li.adv-alt"), function (li) { return /Uncensored/.test(li.textContent); });
    T.check("an uncensored alternative now appears, badged", !!ualt && !!ualt.querySelector(".adv-unc"));
    const ur = ualt && ualt.querySelector("input[data-pick]");
    T.check("...and is pickable with the stored acknowledgement", ur && !ur.disabled);
    const n0 = ctx.calls.length;
    change(w, ur, true);
    T.check("...setPick carries uncensored:true", ctx.calls.length === n0 + 1 && ctx.calls[n0].uncensored === true &&
      ctx.calls[n0].primary === ur.getAttribute("data-pick").split("|")[0], ctx.calls.slice(n0));
    T.check("the Recommended plan is never uncensored", !w.hermesAdvisor.result.recommended.uncensored &&
      !w.hermesAdvisor.result.recommended.primary.uncensored);

    // revoking: the use goes, the pick falls back to the Recommended plan
    change(w, host.querySelector('input[data-ack]'), false);
    await w.__tick(); await w.__tick();
    const ap2 = posts(w, "/api/advisor/answers");
    T.check("unchecking POSTs {uncensored_ack: false}", ap2.length === 2 && JSON.stringify(body(ap2[1])) === '{"uncensored_ack":false}');
    T.check("...the chip goes off", host.querySelector('input[data-use="uncensored"]').checked === false);
    w.__flushTimers(1); await w.__tick(); await w.__tick();
    const lp = ctx.calls[ctx.calls.length - 1];
    T.check("...and the sheet gets the Recommended plan back (not uncensored)", lp && lp.uncensored === false && lp.primary === M27, lp);
  });

  await scenario("[6] an uncensored alternative is LOCKED without the stored acknowledgement", async function () {
    // the server never sends this; the UI must not trust that it never will
    const st = clone(FX.m64.state);
    st.result = clone(FX.m64.rank_unc.result);
    st.answers.uncensored_ack = false;
    const w = await boot({ "/api/advisor/state": st });
    const ctx = recCtx({});
    const host = await mountStep(w, ctx);
    const ualt = Array.prototype.find.call(host.querySelectorAll("li.adv-alt"), function (li) { return /Uncensored/.test(li.textContent); });
    const ur = ualt && ualt.querySelector("input[data-pick]");
    T.check("its 'Use this instead' radio is disabled", ur && ur.disabled === true);
    T.check("...and says why", ualt && /Acknowledge uncensored models first/.test(ualt.textContent) &&
      w.document.getElementById(ur.getAttribute("aria-describedby")));
    const n0 = ctx.calls.length;
    change(w, ur, true);
    T.check("forcing a change on it never reaches setPick", ctx.calls.length === n0 &&
      ctx.calls.every(function (p) { return p.uncensored === false; }));
  });

  // ======================================================================
  await scenario("[7] step.save(): advisory, never blocks onboarding", async function () {
    let mode = "ok";
    const w = await boot({
      "/api/advisor/state": FX.m64.state,
      "/api/advisor/rank": FX.m64.rank_code_best,
      "/api/advisor/answers": function (url, init) {
        if (mode === "400") return { __status: 400, body: { ok: false, errors: ["uses: unknown 'x'", "priority: one of balanced, best, faster"] } };
        if (mode === "404") return { __status: 404, body: { error: "not found" } };
        if (mode === "500") return { __status: 500, body: { ok: false, error: "boom" } };
        if (mode === "net") return { __reject: "Load failed" };
        return { ok: true, answers: Object.assign({}, JSON.parse(init.body), { uncensored_ack: false, saved_at: 1798000001 }) };
      }
    });
    const S = w.hermesAdvisor.step;
    const r0 = await S.save(recCtx({}));
    T.check("before the state answers: resolves {} and posts nothing", JSON.stringify(r0) === "{}" && posts(w, "/api/advisor/answers").length === 0);
    const host = await mountStep(w, recCtx({}));
    const r1 = await S.save(recCtx({}));
    const p1 = posts(w, "/api/advisor/answers");
    T.check("never-saved answers are POSTed on Next", p1.length === 1 &&
      JSON.stringify(body(p1[0])) === '{"uses":["chat","agent"],"priority":"balanced","power":null}', p1.map(body));
    T.check("...and it resolves {}", JSON.stringify(r1) === "{}");
    const r2 = await S.save(recCtx({}));
    T.check("unchanged and saved: no second POST", posts(w, "/api/advisor/answers").length === 1 && JSON.stringify(r2) === "{}");
    change(w, host.querySelector('input[data-use="code"]'), true);
    mode = "400";
    const r3 = await S.save(recCtx({}));
    T.check("a 400 resolves {err: errors.join(' · ')}", r3 && r3.err === "uses: unknown 'x' · priority: one of balanced, best, faster", r3);
    for (const m of ["404", "500", "net"]) {
      mode = m;
      const r = await S.save(recCtx({}));
      T.check("a " + m + " resolves {} (the advisor never blocks onboarding)", r && JSON.stringify(r) === "{}", r);
    }
  });

  // ======================================================================
  await scenario("[8] esc() on every field", async function () {
    const st = clone(FX.m64.state);
    st.hw.chip.brand = EVIL; st.hw.machine_name = EVIL; st.hw.macos = EVIL; st.hw.gpu_budget.note = EVIL;
    st.questions.uncensored_ack_text = EVIL;
    st.questions.uses = st.questions.uses.map(function (u) { return { key: u.key, label: u.label + EVIL }; });
    const r = st.result;
    [r.recommended].concat(r.alternatives).forEach(function (p) {
      p.label = EVIL; p.why = EVIL; p.notes = [EVIL];
      p.primary.label = EVIL; p.primary.verified_detail = EVIL;
      if (p.background) p.background.label = EVIL;
      p.estimates.primary.formula.decode = EVIL;
    });
    r.wont.forEach(function (x) { x.label = EVIL; x.reason = EVIL; });
    r.notes = [EVIL];
    st.limits.formula = EVIL; st.limits.note = EVIL;
    st.formulas.score = EVIL;
    st.answers.uncensored_ack = true; st.answers.uses = ["chat", "uncensored"];
    const w = await boot({ "/api/advisor/state": st });
    const host = await mountStep(w, recCtx({}));
    const html = host.innerHTML;
    T.check("no <img> element is ever created", host.querySelectorAll("img").length === 0);
    T.check("the hostile text is shown as text", host.textContent.indexOf(EVIL) >= 0);
    // outside attribute values (where a quoted "<" is inert) no tag is spelled out
    T.check("...and escaped in the markup", html.indexOf("&lt;img") >= 0 && html.replace(/="[^"]*"/g, "").indexOf("<img") < 0);
    T.check("no element anywhere has an onerror attribute",
      !Array.prototype.some.call(host.querySelectorAll("*"), function (n) { return n.hasAttribute("onerror"); }));
    // the card renders the same fields plus the per-plan buttons
    const d = w.document;
    const panel = d.createElement("section"); panel.id = "sec-models";
    panel.innerHTML = '<div class="set-body"><div data-legacy-slot></div></div>';
    d.body.appendChild(panel);
    const card = w.hermesAdvisor.mountCard();
    T.check("the card is inert too", card && card.querySelectorAll("img").length === 0 && card.textContent.indexOf(EVIL) >= 0);
    const ex = w.hermesAdvisor.explain({ advice: { why: EVIL, resident_gb: 3, limit_gb: 10, formula: { resident: EVIL } } });
    T.check("explain() escapes the advice too", ex.indexOf("<img") < 0 && ex.indexOf("&lt;img") >= 0);
  });

  // ======================================================================
  await scenario("[9] the Settings card: placement, What you run now, Re-check", async function () {
    const w = await boot({ "/api/advisor/state": FX.m64.state });
    const d = w.document, anim = animRecorder(w);
    T.check("no panel yet: mountCard() waits and puts nothing anywhere",
      w.hermesAdvisor.mountCard() === null && !d.getElementById("mind-extra-advisor") && adv(w).length === 0);
    const vm = d.getElementById("view-mind");
    const panel = d.createElement("section"); panel.id = "sec-models";
    panel.innerHTML = '<div class="set-body"><section class="card glass set-legacy" id="mind-extra-promptbudget"></section><div data-legacy-slot></div></div>';
    (vm || d.body).appendChild(panel);
    await w.__run("mindExtras()");
    await w.__tick(); await w.__tick();
    const card = d.getElementById("mind-extra-advisor");
    const sb = panel.querySelector(".set-body");
    T.check("window.mindExtras() mounts the card", !!card);
    T.check("...as the FIRST card of #sec-models", card && card.parentNode === sb && sb.firstElementChild === card);
    T.check("...with order:-1", card && card.style.order === "-1");
    T.check("...the app's card + glass classes", card && /\bcard\b/.test(card.className) && /\bglass\b/.test(card.className));
    T.check("...never a direct child of #view-mind", !vm || card.parentNode !== vm);
    T.check("mounting read the state once, nothing else", adv(w).length === 1 && adv(w)[0].url === "/api/advisor/state" &&
      w.__fetches.filter(function (f) { return f.init && f.init.method === "POST"; }).length === 0);
    const t = card.textContent;
    T.check("What you run now: the plan and its verdict", /What you run now/.test(t) && /fits/.test(card.querySelector(".adv-verdict").textContent) &&
      t.indexOf("Qwen3.8-27B as the assistant, Qwen3.5-9B for background work") >= 0);
    T.check("'Last analysed <12-hour time> · Re-check'", /Last analysed \d{1,2}:\d\d (AM|PM)/.test(t) &&
      !!card.querySelector('button[data-adv-act="recheck"]'));
    T.check("the same component: questionnaire + Recommended plan", card.querySelectorAll("input[data-use]").length === 6 && !!card.querySelector(".adv-rec"));
    T.check("the card shows no onboarding pick radios", card.querySelectorAll("input[data-pick]").length === 0);
    T.check("nothing animated on mount", anim.length === 0);
    card.querySelector('button[data-adv-act="recheck"]').click();
    await w.__tick(); await w.__tick();
    T.check("Re-check busts the cache: GET /api/advisor/state?bust=1", adv(w).length === 2 && adv(w)[1].url === "/api/advisor/state?bust=1",
      adv(w).map(function (f) { return f.url; }));
    await w.__run("mindExtras()");
    await w.__tick();
    T.check("a second Settings visit inside 5 minutes reads nothing", adv(w).length === 2);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String).slice(0, 3));
  });

  // ======================================================================
  await scenario("[10] the card's per-plan buttons: explicit clicks only", async function () {
    const st = clone(FX.m64.state);
    // a second alternative whose background lane is uncensored and whose
    // primary is already downloaded, so every button kind is on screen
    const alt2 = clone(st.result.alternatives[0]);
    alt2.label = "Lighter";
    alt2.primary = Object.assign(clone(alt2.primary), { id: M9, label: "Qwen3.5-9B", downloaded: true, uncensored: false });
    alt2.background = Object.assign(clone(alt2.background), { id: VANCH, label: "Qwen3.5-9B Abliterated", uncensored: true, downloaded: false });
    alt2.disk_ok = false; alt2.downloaded = false;
    // the first alternative's lane is not the one running, so it gets a lane button
    st.result.alternatives[0].background = Object.assign(clone(st.result.alternatives[0].background),
      { id: M4, label: "Qwen3.5-4B", uncensored: false });
    st.result.alternatives = [st.result.alternatives[0], alt2];
    let applyMode = "ok";
    const w = await boot({
      "/api/advisor/state": st,
      "/api/models/add": { ok: true },
      "/api/models/download": { ok: true },
      "/api/models/switch": { ok: true, loading: true },
      "/api/onboarding/apply": function (url, init) {
        const b = JSON.parse(init.body || "{}");
        if (applyMode === "409" && b.ack_uncensored !== true) {
          return { __status: 409, body: { ok: false, needs_ack: true, error: "uncensored_ack", id: b.background,
                                          errors: ["X is an uncensored model. Acknowledge it first."] } };
        }
        return { ok: true, applied: { background: b.background, background_lane: b.background }, errors: [] };
      }
    });
    const d = w.document;
    const panel = d.createElement("section"); panel.id = "sec-models";
    panel.innerHTML = '<div class="set-body"><div data-legacy-slot></div></div>';
    d.body.appendChild(panel);
    const card = w.hermesAdvisor.mountCard();
    await w.__tick(); await w.__tick();
    const confirms = [];
    let answer = true;
    w.confirm = function (t) { confirms.push(String(t)); return answer; };
    const mutating = function () { return w.__fetches.filter(function (f) { return f.init && f.init.method === "POST"; }); };
    T.check("rendering every button fires nothing", mutating().length === 0 && confirms.length === 0);
    const recKey = M27 + "|" + M9;
    const rb = function (act, key) { return card.querySelector('button[data-plan-act="' + act + '"][data-plan="' + key + '"]'); };
    T.check("the Recommended plan offers Add and Download (~6 GB · 412 GB free)", !!rb("add", recKey) &&
      rb("download", recKey) && rb("download", recKey).textContent === "Download (~6 GB · 412 GB free)",
      rb("download", recKey) && rb("download", recKey).textContent);
    T.check("...but no 'Make it the assistant' (it already is) and no lane button (it already is)", !rb("switch", recKey) && !rb("bg", recKey));
    const k1 = st.result.alternatives[0].primary.id + "|" + st.result.alternatives[0].background.id;
    T.check("an alternative whose primary is not downloaded: 'Make it the assistant' is disabled with the reason",
      rb("switch", k1) && rb("switch", k1).disabled && /download it first/.test(rb("switch", k1).parentNode.textContent));
    const k2 = M9 + "|" + VANCH;
    T.check("a plan without the disk for it: Download is disabled with the reason",
      rb("download", k2) && rb("download", k2).disabled && /not enough free disk/.test(rb("download", k2).parentNode.textContent));

    // Download: confirm first, then add + download, in order
    answer = false;
    rb("download", recKey).click(); await w.__tick(); await w.__tick();
    T.check("Download asks first (confirm) and a No sends nothing", confirms.length === 1 && mutating().length === 0);
    T.check("...the confirm quotes the size and the free space", /~6 GB download · 412 GB free/.test(confirms[0]), confirms[0]);
    answer = true;
    rb("download", recKey).click(); await w.__tick(); await w.__tick(); await w.__tick();
    const dl = mutating().map(function (f) { return f.url + " " + (body(f).id || ""); });
    T.check("Download = POST /api/models/add then /api/models/download, for the lane not on this Mac",
      JSON.stringify(dl) === JSON.stringify(["/api/models/add " + M9, "/api/models/download " + M9]), dl);
    T.check("...and says so on the plan", /Downloading Qwen3\.5-9B/.test(card.querySelector('p[data-msg="' + recKey + '"]').textContent));

    // Add to model menu: every lane of the plan
    const m0 = mutating().length;
    rb("add", recKey).click(); await w.__tick(); await w.__tick(); await w.__tick();
    const ad = mutating().slice(m0).map(function (f) { return f.url + " " + body(f).id; });
    T.check("Add to model menu POSTs /api/models/add {id} for each lane, nothing else",
      JSON.stringify(ad) === JSON.stringify(["/api/models/add " + M27, "/api/models/add " + M9]), ad);

    // Use as background lane, uncensored: the combined confirm, ack on the first request
    const c0 = confirms.length, m1 = mutating().length;
    rb("bg", k2).click(); await w.__tick(); await w.__tick();
    const bg = mutating().slice(m1);
    T.check("an uncensored lane: one confirm carrying the acknowledgement copy", confirms.length === c0 + 1 &&
      confirms[c0].indexOf(st.questions.uncensored_ack_text) >= 0 && /next time the background lane starts/.test(confirms[c0]));
    T.check("...and says plainly that the lane is not on this Mac yet (it would pull the weights itself)",
      /not on this Mac yet/.test(confirms[c0]) && /Download first/.test(confirms[c0]), confirms[c0]);
    T.check("...and ONE POST /api/onboarding/apply {background, ack_uncensored:true}", bg.length === 1 &&
      bg[0].url === "/api/onboarding/apply" && JSON.stringify(body(bg[0])) === JSON.stringify({ background: VANCH, ack_uncensored: true }), bg.map(body));

    // a lane the UI did not know was uncensored: 409 needs_ack -> confirm -> ONE retry
    applyMode = "409";
    const st2 = w.hermesAdvisor.result.alternatives[0];
    const c1 = confirms.length, m2 = mutating().length;
    rb("bg", k1).click(); await w.__tick(); await w.__tick(); await w.__tick();
    const bg2 = mutating().slice(m2).map(body);
    T.check("a 409 needs_ack: the acknowledgement is asked, then exactly one retry with ack_uncensored:true",
      confirms.length === c1 + 2 && bg2.length === 2 && bg2[0].ack_uncensored === undefined &&
      bg2[1].ack_uncensored === true && bg2[1].background === st2.background.id, bg2);
    answer = false;
    const m3 = mutating().length;
    rb("bg", k1).click(); await w.__tick(); await w.__tick();
    T.check("declining the first confirm sends nothing", mutating().length === m3);
    answer = true;

    // Make it the assistant: the model menu's own switch path (onModelClick)
    const c2 = confirms.length, m4 = mutating().length;
    rb("switch", k2).click(); await w.__tick(); await w.__tick();
    const sw = mutating().slice(m4);
    T.check("Make it the assistant goes through onModelClick's confirm", confirms.length === c2 + 1 && /^Switch the agent to Qwen3\.5-9B\?/.test(confirms[c2]), confirms.slice(c2));
    T.check("...and POSTs /api/models/switch {id} once", sw.length === 1 && sw[0].url === "/api/models/switch" &&
      JSON.stringify(body(sw[0])) === JSON.stringify({ id: M9 }), sw.map(body));
    T.check("no button ever reached a download it was not clicked for", w.__fetches.filter(function (f) { return /\/api\/models\/download/.test(f.url); }).length === 1);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String).slice(0, 3));
  });

  await scenario("[11] the card's answers are stored only by 'Use these answers'", async function () {
    const w = await boot({
      "/api/advisor/state": FX.m64.state, "/api/advisor/rank": FX.m64.rank_code_best,
      "/api/advisor/answers": function (url, init) { return { ok: true, answers: Object.assign(JSON.parse(init.body), { uncensored_ack: false, saved_at: 1798000002 }) }; }
    });
    const d = w.document;
    const panel = d.createElement("section"); panel.id = "sec-models";
    panel.innerHTML = '<div class="set-body"></div>';
    d.body.appendChild(panel);
    const card = w.hermesAdvisor.mountCard();
    await w.__tick(); await w.__tick();
    change(w, card.querySelector('input[data-use="code"]'), true);
    w.__flushTimers(1); await w.__tick(); await w.__tick();
    T.check("a changed answer re-ranks but stores nothing", posts(w, "/api/advisor/answers").length === 0 &&
      adv(w).some(function (f) { return /rank/.test(f.url); }));
    const sv = card.querySelector('button[data-adv-act="save-answers"]');
    T.check("...and offers 'Use these answers'", sv && /Use these answers/.test(sv.textContent));
    sv.click(); await w.__tick(); await w.__tick();
    const p = posts(w, "/api/advisor/answers");
    T.check("one click stores exactly the answers on screen", p.length === 1 &&
      JSON.stringify(body(p[0])) === '{"uses":["chat","agent","code"],"priority":"balanced","power":null}', p.map(body));
    T.check("...then the button goes away", !card.querySelector('button[data-adv-act="save-answers"]'));
  });

  // ======================================================================
  await scenario("[12] the modelDetailsHTML wrapper (§8.3)", async function () {
    const w = await boot({ "/api/models": FX.models64 });
    const fn = w.modelDetailsHTML;
    T.check("wrapped once, marked __adv, the original kept", typeof fn === "function" && fn.__adv === true && typeof fn.__advOrig === "function");
    const plain = { id: "a/b", label: "Plain", ctx: 1000, backend: "mlx_vlm", ram: 5 };
    T.check("a row without advice renders exactly what it did before", fn(plain, 64, 100) === fn.__advOrig(plain, 64, 100));
    const row = FX.models64.models.find(function (m) { return m.id === M27; });
    const out = fn(row, 64, 412), orig = fn.__advOrig(row, 64, 412);
    T.check("with advice: the original rows are all still there", out.indexOf(orig.replace(/<\/dl>$/, "")) === 0);
    const dts = (out.match(/<dt>([^<]*)<\/dt>/g) || []).map(function (x) { return x.replace(/<\/?dt>/g, ""); });
    const extra = dts.slice((orig.match(/<dt>/g) || []).length);
    T.check("...plus exactly the four advisor rows", JSON.stringify(extra) === JSON.stringify(["Speed here", "First reply after wake", "Memory with cache", "Advisor"]), extra);
    T.check("...each value titled with its formula", out.indexOf('title="' + w.esc(row.advice.formula.resident)) >= 0 &&
      out.indexOf('title="' + w.esc(row.advice.formula.prefill)) >= 0 && out.indexOf("Advisory only") >= 0);
    T.check("...'Advisor' says recommended for you", /recommended for you/.test(out));
    w.__run(SRC, "aux_advisor.js#2");
    T.check("a second load of the file does not double-wrap", w.modelDetailsHTML === fn &&
      (w.modelDetailsHTML(row, 64, 412).match(/Speed here/g) || []).length === 1);
    await w.__run("loadModels()"); await w.__tick();
    const dd = w.document.getElementById("mmdd-mlx-community_Qwen3_8-27B-4bit");
    T.check("the live menu's Details panel shows the advisor rows", dd && /Speed here/.test(dd.textContent) && /Memory with cache/.test(dd.textContent));
    const row1 = w.document.querySelector('.mmi[data-id="' + M27 + '"]');
    T.check("...and the row itself did not grow a line (the panel is a sibling)", row1 && !/Speed here/.test(row1.textContent));
    // the advisor verdict is advisory: onModelClick still refuses only fit=no / disk-short
    const won = Object.assign(clone(row), { advice: Object.assign(clone(row.advice), { role: "wont", why: "won't fit here" }), active: false, downloaded: true });
    const fx2 = clone(FX.models64); fx2.models = [won]; fx2.models[0].active = false;
    w.__routes["/api/models"] = fx2;
    await w.__run("loadModels()"); await w.__tick();
    const r = w.document.querySelector('.mmi[data-id="' + M27 + '"]');
    T.check("an advisor 'won't fit' does not make a row inert (D7)", r && !r.hasAttribute("aria-disabled") && /switch/.test(r.querySelector(".act").textContent));

    const bare = createWindow({});
    bare.__run(SRC, "aux_advisor.js");
    T.check("no modelDetailsHTML on the page: a no-op, nothing defined, no throw",
      bare.modelDetailsHTML === undefined && bare.hermesAdvisor && bare.hermesAdvisor.v === 1 && bare.__errors.length === 0);
    bare.modelDetailsHTML = function () { return "<dl></dl>"; };
    bare.document.dispatchEvent(new bare.Event("DOMContentLoaded"));
    bare.__run(SRC, "aux_advisor.js#2");
    T.check("...a page that defines it later still gets the wrap", bare.modelDetailsHTML.__adv === true);
  });

  // ======================================================================
  await scenario("[13] index.html's model menu: the Uncensored badge", async function () {
    const fx = clone(FX.models64);
    const w = await boot({ "/api/models": fx });
    await w.__run("loadModels()"); await w.__tick();
    const d = w.document;
    const orca = d.querySelector('.mmi[data-id="' + ORCA + '"]');
    const stock = d.querySelector('.mmi[data-id="' + M27 + '"]');
    T.check("an uncensored row carries data-uncensored=\"1\"", orca && orca.getAttribute("data-uncensored") === "1");
    T.check("...the stock 27B does not", stock && !stock.hasAttribute("data-uncensored"));
    const chip = orca && orca.querySelector(".mmunc");
    T.check("...an 'Uncensored' chip with a two-tone inline SVG", chip && /Uncensored/.test(chip.textContent) &&
      chip.querySelector("svg") && /opacity/.test(chip.innerHTML) && /stroke="currentColor"/.test(chip.innerHTML));
    T.check("...the menu line 'no refusals — the approval gate is the only brake'",
      orca && orca.querySelector(".mmunc-l") && orca.querySelector(".mmunc-l").textContent === "no refusals — the approval gate is the only brake");
    T.check("...and 'unverified until drilled' (its roster says verified:false)", orca && /unverified until drilled/.test(orca.querySelector(".mmtags").textContent));
    T.check("the stock 27B (verified:true) has no tags at all", stock && !stock.querySelector(".mmtags") && !stock.querySelector(".mmunc-l"));
    const unv = d.querySelector('.mmi[data-id="mlx-community/Qwen3.6-35B-A3B-4bit"]');
    T.check("a stock but undrilled model: 'unverified until drilled' and no Uncensored chip", unv &&
      /unverified until drilled/.test(unv.textContent) && !unv.querySelector(".mmunc") && !unv.hasAttribute("data-uncensored"));
    T.check("the name element stays the bare label (onModelClick reads it)", orca.querySelector(".nm").textContent === "Qwen3.8-27B Uncensored");
    T.check("zero emoji in the menu", !EMOJI.test(d.getElementById("model-menu").innerHTML));
    const css = (d.querySelector("style") || {}).textContent || "";
    T.check("the badge CSS uses the --warn token and declares no token of its own",
      /\.mmi \.mmunc\{[^}]*var\(--warn\)/.test(css) && !/\.mmi \.mmunc[^{]*\{[^}]*--[a-z-]+\s*:/.test(css));
  });

  await scenario("[14] onModelClick: the badge-aware acknowledgement", async function () {
    const fx = clone(FX.models64);
    fx.models.forEach(function (m) { if (m.id === ORCA || m.id === M9) m.downloaded = true; });
    let mode = "ok";
    const w = await boot({
      "/api/models": fx, "/api/advisor/state": FX.m64.state,
      "/api/models/switch": function (url, init) {
        const b = JSON.parse(init.body || "{}");
        if (mode === "gate" && b.ack_uncensored !== true) {
          return { __status: 409, body: { ok: false, needs_ack: true, error: "uncensored_ack", id: b.id, label: "x",
                                          message: "This is an uncensored model", ack_text: FX.m64.state.questions.uncensored_ack_text } };
        }
        return { ok: true, loading: true };
      }
    });
    const d = w.document;
    // the advisor has read its state once (Settings or onboarding), so the
    // confirm can quote the server's own copy
    const host = await mountStep(w, recCtx({}));
    host.remove();
    await w.__run("loadModels()"); await w.__tick();
    const confirms = []; let answers = [];
    w.confirm = function (t) { confirms.push(String(t)); return answers.length ? answers.shift() : true; };
    const sw = function () { return posts(w, "/api/models/switch"); };
    const row = function (id) { return d.querySelector('.mmi[data-id="' + id + '"]'); };

    await w.__run("onModelClick(document.querySelector('.mmi[data-id=\"" + ORCA + "\"]'))"); await w.__tick();
    T.check("a row known to be uncensored asks ONCE", confirms.length === 1, confirms.length);
    T.check("...one confirm with the switch AND the server's acknowledgement copy", /^Switch the agent to Qwen3\.8-27B Uncensored\?/.test(confirms[0]) &&
      confirms[0].indexOf(FX.m64.state.questions.uncensored_ack_text) >= 0 && /model server restarts/.test(confirms[0]), confirms[0]);
    T.check("...and its FIRST (only) request carries ack_uncensored:true", sw().length === 1 &&
      JSON.stringify(body(sw()[0])) === JSON.stringify({ id: ORCA, ack_uncensored: true }), sw().map(body));
    w.__flushTimers(3);

    // a plain row: one confirm, no ack key
    confirms.length = 0;
    const n1 = sw().length;
    row(M9) && await w.__run("onModelClick(document.querySelector('.mmi[data-id=\"" + M9 + "\"]'))"); await w.__tick();
    T.check("a stock row: one plain confirm and {id} only", confirms.length === 1 && !/uncensored/i.test(confirms[0]) &&
      sw().length === n1 + 1 && JSON.stringify(body(sw()[n1])) === JSON.stringify({ id: M9 }), sw().slice(n1).map(body));
    w.__flushTimers(3);

    // the fallback: the server knows better than the menu (409) -> confirm -> ONE retry
    mode = "gate";
    await w.__run("loadModels()"); await w.__tick();
    confirms.length = 0;
    const n2 = sw().length;
    await w.__run("onModelClick(document.querySelector('.mmi[data-id=\"" + M9 + "\"]'))"); await w.__tick(); await w.__tick();
    const s2 = sw().slice(n2).map(body);
    T.check("a 409 uncensored_ack on an unflagged row: confirm, then exactly one retry with the ack",
      confirms.length === 2 && s2.length === 2 && s2[0].ack_uncensored === undefined && s2[1].ack_uncensored === true, s2);
    T.check("...the second confirm is the acknowledgement", confirms[1] && confirms[1].indexOf(FX.m64.state.questions.uncensored_ack_text) >= 0);
    w.__flushTimers(3);
    await w.__run("loadModels()"); await w.__tick();
    confirms.length = 0; answers = [true, false];
    const n3 = sw().length;
    await w.__run("onModelClick(document.querySelector('.mmi[data-id=\"" + M9 + "\"]'))"); await w.__tick(); await w.__tick();
    T.check("declining the acknowledgement: no retry, the overlay says it was cancelled",
      sw().length === n3 + 1 && /cancelled/.test(d.getElementById("swap-sub").textContent), d.getElementById("swap-sub").textContent);
    w.__flushTimers(3);

    // the refusals stay exactly fit === "no" and disk-short (D7)
    const n4 = w.__fetches.length;
    confirms.length = 0;
    const fake = function (ds) { return { dataset: Object.assign({ id: "a/b", dl: "1", active: "0", busy: "0" }, ds), querySelector: function () { return { textContent: "X" }; } }; };
    await w.onModelClick(fake({ fit: "no" }));
    await w.onModelClick(fake({ dl: "0", disk: "short" }));
    await w.onModelClick(fake({ fit: "no", uncensored: "1" }));
    T.check("fit=no and disk-short rows are still refused before any confirm or request",
      confirms.length === 0 && w.__fetches.length === n4);
  });

  // ======================================================================
  await scenario("[15] CSS and motion hygiene", async function () {
    const w = await boot({});
    const css = w.hermesAdvisor.css();
    T.check("no 'transition: all'", !/transition\s*:\s*all/.test(css));
    T.check("every transition names its properties", (css.match(/transition-property:/g) || []).length >= 4 &&
      !/transition\s*:\s*(?!none)[a-z]/.test(css));
    T.check("both reduced-motion switches are honoured", /prefers-reduced-motion:reduce/.test(css) && /:root\[data-reduce\]/.test(css));
    T.check("no theme token is re-declared", !/(^|[;{\s])--[a-z0-9-]+\s*:/.test(css));
    T.check(">=40 px targets: chips, segments, picks, buttons, summaries", [".adv .adv-chip{", ".adv .adv-seg-o{", ".adv .adv-pick{",
      ".adv .adv-b{", ".adv summary{", ".adv .adv-check{", ".adv .adv-link{"]
      .every(function (k) { const i = css.indexOf(k); return i >= 0 && /min-height:40px/.test(css.slice(i, css.indexOf("}", i))); }));
    T.check("the module never uses an animation library directly", !/gsap|anime\.|Motion\.animate|\.animate\(/.test(SRC.replace(/\/\/.*$/gm, "")));
  });

  // ======================================================================
  // review fixes (R3-c-advisor-ui review)
  await scenario("[16] review: the wrapper keeps '$' patterns literal", async function () {
    const w = await boot({ "/api/models": FX.models64 });
    const row = clone(FX.models64.models.find(function (m) { return m.id === M27; }));
    row.advice.why = "costs $& and $` and $' and $1 and $$";
    row.advice.formula.resident = "x = $& $`";
    const out = w.modelDetailsHTML(row, 64, 412), orig = w.modelDetailsHTML.__advOrig(row, 64, 412);
    T.check("exactly one closing </dl>: no match or prefix was spliced back in", (out.match(/<\/dl>/g) || []).length === 1, out.slice(-300));
    T.check("exactly one opening <dl>", (out.match(/<dl\b/g) || []).length === 1);
    T.check("the advice text arrives as written", out.indexOf("costs $&amp; and $` and $' and $1 and $$") >= 0 ||
      out.indexOf("costs $& and $` and $' and $1 and $$") >= 0, out.slice(-400));
    T.check("the original rows are still the prefix", out.indexOf(orig.replace(/<\/dl>$/, "")) === 0);
  });

  await scenario("[17] review: a closed onboarding sheet is never handed a pick", async function () {
    const w = await boot({ "/api/advisor/state": FX.m64.state, "/api/advisor/rank": FX.m64.rank_code_best });
    const d = w.document;
    const ctx = recCtx(onboardingData(FX.m64, 64));
    const host = await mountStep(w, ctx);
    T.check("(the open sheet got its pick)", ctx.calls.length === 1);
    host.remove();                                   // the sheet closed
    // a re-rank whose Recommended plan differs from what the sheet was handed
    const sw = clone(FX.m64.state.result);
    const alt = sw.alternatives[0];
    sw.alternatives = [sw.recommended].concat(sw.alternatives.slice(1));
    sw.recommended = alt;
    w.__routes["/api/advisor/rank"] = { ok: true, result: sw, saved: false };
    const panel = d.createElement("section"); panel.id = "sec-models";
    panel.innerHTML = '<div class="set-body"></div>';
    d.body.appendChild(panel);
    const card = w.hermesAdvisor.mountCard();
    await w.__tick();
    change(w, card.querySelector('input[data-use="code"]'), true);
    w.__flushTimers(1); await w.__tick(); await w.__tick();
    T.check("the Settings re-rank landed with a different Recommended plan",
      w.hermesAdvisor.result.recommended.primary.id === alt.primary.id && alt.primary.id + "|" + ((alt.background || {}).id || "") !==
      FX.m64.state.result.recommended.primary.id + "|" + FX.m64.state.result.recommended.background.id);
    T.check("...and the closed sheet's setPick was NOT called again", ctx.calls.length === 1, ctx.calls);
    T.check("...its ctx is dropped", w.hermesAdvisor._st.ctx === null);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String).slice(0, 3));
  });

  await scenario("[18] review: a picked alternative that becomes Recommended is one selection", async function () {
    const w = await boot({ "/api/advisor/state": FX.m64.state, "/api/advisor/rank": FX.m64.rank_code_best });
    const ctx = recCtx(onboardingData(FX.m64, 64));
    const host = await mountStep(w, ctx);
    const altRadio = host.querySelector("li.adv-alt input[data-pick]");
    change(w, altRadio, true);
    const altKey = altRadio.getAttribute("data-pick");
    const nCalls = ctx.calls.length;
    const sw = clone(FX.m64.state.result);
    const pick = sw.alternatives.find(function (p) { return p.primary.id + "|" + ((p.background || {}).id || "") === altKey; });
    sw.alternatives = [sw.recommended].concat(sw.alternatives.filter(function (p) { return p !== pick; }));
    sw.recommended = pick;
    w.__routes["/api/advisor/rank"] = { ok: true, result: sw, saved: false };
    change(w, host.querySelector('input[data-pri="best"]'), true);
    w.__flushTimers(1); await w.__tick(); await w.__tick();
    const rec = host.querySelector(".adv-rec");
    T.check("the answers moved the picked plan into the Recommended slot", rec && rec.getAttribute("data-plan") === altKey);
    T.check("...which shows as the picked one (its radio is on, the card is marked)",
      rec.classList.contains("is-picked") && rec.querySelector("input[data-pick]").checked === true);
    T.check("...and no alternative claims to be picked too", host.querySelectorAll("li.adv-alt.is-picked").length === 0 &&
      Array.prototype.every.call(host.querySelectorAll("li.adv-alt input[data-pick]"), function (i) { return !i.checked; }));
    T.check("...the sheet already holds that plan: nothing is re-sent", ctx.calls.length === nCalls, ctx.calls.slice(nCalls));
  });

  await scenario("[19] review: 'Make it the assistant' believes the live model menu over its own old state", async function () {
    const fx = clone(FX.models64);
    fx.models.forEach(function (m) { m.active = (m.id === M9); if (m.id === M9) m.downloaded = true; });
    const st = clone(FX.m64.state);                  // this page's old read: the 27B is the assistant
    const alt = clone(st.result.alternatives[0]);
    alt.primary = Object.assign(clone(alt.primary), { id: M9, label: "Qwen3.5-9B", downloaded: true, uncensored: false });
    alt.background = null;
    st.result.alternatives = [alt];
    const w = await boot({ "/api/advisor/state": st, "/api/models": fx, "/api/models/switch": { ok: true, loading: true } });
    const d = w.document;
    await w.__run("loadModels()"); await w.__tick();
    const panel = d.createElement("section"); panel.id = "sec-models";
    panel.innerHTML = '<div class="set-body"></div>';
    d.body.appendChild(panel);
    const card = w.hermesAdvisor.mountCard();
    await w.__tick(); await w.__tick();
    const confirms = [];
    w.confirm = function (t) { confirms.push(String(t)); return true; };
    const key = M9 + "|";
    const btn = card.querySelector('button[data-plan-act="switch"][data-plan="' + key + '"]');
    T.check("the card (old state) still offers 'Make it the assistant' for the 9B", !!btn && !btn.disabled);
    btn.click(); await w.__tick(); await w.__tick();
    T.check("...but the menu says it is active: no confirm, no request", confirms.length === 0 && posts(w, "/api/models/switch").length === 0, confirms);
    T.check("...and the plan says so", /already the assistant/.test(card.querySelector('p[data-msg="' + key + '"]').textContent));
    // the menu does not see the weights yet: never turn the button into a download
    const row = d.querySelector('.mmi[data-id="' + M9 + '"]');
    row.setAttribute("data-active", "0"); row.setAttribute("data-dl", "0");
    btn.click(); await w.__tick(); await w.__tick();
    T.check("a row the menu has as not downloaded: refused with a reason, nothing downloaded or switched",
      confirms.length === 0 && posts(w, "/api/models/switch").length === 0 &&
      w.__fetches.filter(function (f) { return /\/api\/models\/download/.test(f.url); }).length === 0 &&
      /download it first/.test(card.querySelector('p[data-msg="' + key + '"]').textContent));
    // a normal live row goes the model menu's way
    row.setAttribute("data-dl", "1");
    btn.click(); await w.__tick(); await w.__tick();
    T.check("a downloaded, idle live row: onModelClick's confirm, then ONE POST /api/models/switch {id}",
      confirms.length === 1 && /^Switch the agent to /.test(confirms[0]) && posts(w, "/api/models/switch").length === 1 &&
      JSON.stringify(body(posts(w, "/api/models/switch")[0])) === JSON.stringify({ id: M9 }), confirms);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String).slice(0, 3));
  });

  await scenario("[20] review: the uncensored confirm quotes the SERVER's acknowledgement even if no one opened Settings", async function () {
    const fx = clone(FX.models64);
    fx.models.forEach(function (m) { if (m.id === ORCA) m.downloaded = true; });
    const SERVER_TEXT = FX.m64.state.questions.uncensored_ack_text;
    const orcaClick = function () { return "onModelClick(document.querySelector('.mmi[data-id=\"" + ORCA + "\"]'))"; };
    let stateMode = "ok";
    const w = await boot({
      "/api/models": fx,
      "/api/advisor/state": function () { return stateMode === "500" ? { __status: 500, body: { ok: false, error: "boom" } } : FX.m64.state; },
      "/api/models/switch": { ok: true, loading: true }
    });
    await w.__run("loadModels()"); await w.__tick();
    const confirms = [];
    w.confirm = function (t) { confirms.push(String(t)); return true; };
    T.check("(nothing on this page has read the advisor yet)", adv(w).length === 0 && w.hermesAdvisor.ackText() === "");
    await w.__run(orcaClick()); await w.__tick(); await w.__tick();
    T.check("the click read the acknowledgement once (a GET, no write)", adv(w).length === 1 &&
      adv(w)[0].url === "/api/advisor/state" && !(adv(w)[0].init || {}).method, adv(w).map(function (f) { return f.url; }));
    T.check("...then ONE confirm quoting the server's own copy, including the denylist caveat", confirms.length === 1 &&
      confirms[0].indexOf(SERVER_TEXT) >= 0 && /path denylist, not a sandbox/.test(confirms[0]), confirms[0]);
    T.check("...and ONE switch POST carrying ack_uncensored:true", posts(w, "/api/models/switch").length === 1 &&
      JSON.stringify(body(posts(w, "/api/models/switch")[0])) === JSON.stringify({ id: ORCA, ack_uncensored: true }));
    w.__flushTimers(3);
    await w.__run("loadModels()"); await w.__tick();
    confirms.length = 0;
    await w.__run(orcaClick()); await w.__tick(); await w.__tick();
    T.check("a second click reads nothing again (this page has the text)", adv(w).length === 1 && confirms[0].indexOf(SERVER_TEXT) >= 0);
    w.__flushTimers(3);

    // a double click while the text is being read: one confirm
    const w2 = await boot({ "/api/models": fx, "/api/advisor/state": FX.m64.state, "/api/models/switch": { ok: true, loading: true } });
    await w2.__run("loadModels()"); await w2.__tick();
    const c2 = [];
    w2.confirm = function (t) { c2.push(String(t)); return true; };
    const row2 = w2.document.querySelector('.mmi[data-id="' + ORCA + '"]');
    const p1 = w2.onModelClick(row2), p2 = w2.onModelClick(row2);
    await Promise.all([p1, p2]); await w2.__tick(); await w2.__tick();
    T.check("a double click on the row stacks no second confirm", c2.length === 1 && posts(w2, "/api/models/switch").length === 1, c2.length);
    w2.__flushTimers(3);

    // the advisor cannot answer: the short sentence, still ONE confirm, still gated by the click
    const w3 = await boot({ "/api/models": fx, "/api/advisor/state": { __status: 500, body: { ok: false, error: "boom" } },
                            "/api/models/switch": { ok: true, loading: true } });
    await w3.__run("loadModels()"); await w3.__tick();
    const c3 = [];
    w3.confirm = function (t) { c3.push(String(t)); return true; };
    await w3.__run(orcaClick()); await w3.__tick(); await w3.__tick();
    T.check("no answer from the advisor: one confirm with the short acknowledgement sentence",
      c3.length === 1 && /is an uncensored model: its refusal behaviour was removed/.test(c3[0]) && /approval gate is the only brake/.test(c3[0]), c3);
    T.check("...and the switch request still carries the ack (the confirm was the acknowledgement)",
      posts(w3, "/api/models/switch").length === 1 && body(posts(w3, "/api/models/switch")[0]).ack_uncensored === true);
    w3.__flushTimers(3);

    // aux_advisor.js absent altogether: the same short sentence, nothing throws
    const w4 = await boot({ "/api/models": fx, "/api/models/switch": { ok: true, loading: true } }, { noAdvisor: true });
    await w4.__run("loadModels()"); await w4.__tick();
    const c4 = [];
    w4.confirm = function (t) { c4.push(String(t)); return true; };
    await w4.__run(orcaClick()); await w4.__tick(); await w4.__tick();
    T.check("without aux_advisor.js: one confirm with the short sentence, no page error",
      c4.length === 1 && /approval gate is the only brake/.test(c4[0]) && w4.__errors.length === 0, [c4, w4.__errors.map(String)]);
    w4.__flushTimers(3);
  });

  await scenario("[21] review: a sheet that repaints on setPick and keeps nothing cannot start a timer loop", async function () {
    const w = await boot({ "/api/advisor/state": FX.m64.state, "/api/advisor/rank": FX.m64.rank_code_best });
    const d = w.document;
    const calls = [];
    const host = d.createElement("div");
    d.body.appendChild(host);
    const ctx = { rerun: false, data: {}, repaint() {}, setErr() {},
      getPick() { return null; },                    // declines to keep any pick
      setPick(p) { calls.push(p); paint(); } };      // ...and repaints the whole body
    function paint() {
      host.innerHTML = w.hermesAdvisor.step.render(ctx);
      w.hermesAdvisor.step.wire(host, ctx);
    }
    paint();
    await w.__tick(); await w.__tick();
    for (let i = 0; i < 12; i++) { w.__flushTimers(1); await w.__tick(); }
    T.check("the pick is handed over a bounded number of times (no render/setPick loop)", calls.length >= 1 && calls.length <= 3, calls.length);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String).slice(0, 3));
  });

  // R3-seams motion-overlays-4: planAction disables the button that has focus
  // for the whole round trip; a disabled focused button drops focus to <body>
  // and turning it back on does not return it. The mini DOM only REPORTS that
  // fixup (activeElement reads <body> while the button is disabled, and the
  // button again once it is not); Chromium and WebKit COMMIT it at the next
  // rendering update during the wait, so the test commits it at that point.
  await scenario("[22] seams: a plan action pressed from the keyboard gives focus back to its button", async function () {
    for (const how of ["the button survives", "a repaint replaced it meanwhile", "focus moved on purpose"]) {
      let release = null;
      const gate = new Promise(function (r) { release = r; });
      const w = await boot({
        "/api/advisor/state": FX.m64.state,
        "/api/models/add": async function () { await gate; return { ok: true }; }
      });
      const d = w.document;
      const panel = d.createElement("section"); panel.id = "sec-models";
      panel.innerHTML = '<div class="set-body"><div data-legacy-slot></div></div>';
      d.body.appendChild(panel);
      const card = w.hermesAdvisor.mountCard();
      await w.__tick(); await w.__tick();
      const key = M27 + "|" + M9;
      const sel = 'button[data-plan-act="add"][data-plan="' + key + '"]';
      const add = card.querySelector(sel);
      add.focus();
      T.check("[" + how + "] (the keyboard is on 'Add to model menu')", d.activeElement === add);
      add.click(); await w.__tick(2);
      T.check("[" + how + "] while the add is in flight the button is disabled and focus has fallen to <body>",
        add.disabled === true && d.activeElement === d.body, [add.disabled, d.activeElement && d.activeElement.tagName]);
      if (d.activeElement === d.body) d.__active = null;          // the engine's fixup, committed (see above)
      let other = null;
      if (how === "a repaint replaced it meanwhile") { await w.hermesAdvisor.recheck(); await w.__tick(4); }
      if (how === "focus moved on purpose") { other = card.querySelector('button[data-adv-act="recheck"]'); other.focus(); }
      release(); await w.__tick(8);
      const now = card.querySelector(sel);
      if (how === "focus moved on purpose") {
        T.check("[" + how + "] the action never takes focus back from where the owner put it", d.activeElement === other,
          d.activeElement && d.activeElement.outerHTML.slice(0, 80));
      } else {
        T.check("[" + how + "] after it: focus is on " + (how === "the button survives" ? "that button" : "the new 'Add to model menu' of the same plan") +
          ", enabled again, never <body>", now && !now.disabled && d.activeElement === now &&
          (how === "the button survives" ? now === add : now !== add && add.isConnected === false),
          [d.activeElement && d.activeElement.outerHTML.slice(0, 80), now === add]);
      }
      T.check("[" + how + "] no page error", w.__errors.length === 0, w.__errors.map(String).slice(0, 3));
    }
  });

  T.done();
})().catch(function (e) { T.check("suite ran", false, String(e && e.stack || e)); T.done(); });
