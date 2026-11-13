// t_r3_seams.js — the round-3 SEAMS: defects that exist only because two
// clusters' halves did not line up once they were merged. Every scenario runs
// the REAL index.html (markup + inline script) with the REAL aux_anim.js (on
// the recording library stubs), aux_settings_shell.js, aux_update.js,
// aux_onboarding.js, aux_network.js, aux_github.js and aux_advisor.js in
// index.html's own order, in the mini DOM (_a11y_dom.js).
//
// The onboarding and advisor payloads are not hand-written: a python3 child
// loads the REAL server.py (aux_onboarding.py + aux_advisor.py included) under
// the unit tier's offline guard, against a throwaway HOME, and answers the
// real route functions for a SYNTHETIC 64 GB M5 Max — once with nothing
// downloaded, once with the 27B + 9B already on the Mac.
//
//   onboarding-1  Enter on the advisor's <summary> disclosures opens them; it
//                 never presses the sheet's primary action (Download)
//   onboarding-2  the GitHub step repaints its own body (Check token): focus
//                 parked on <body> still gets Esc (a re-run closes) and Tab
//                 (back into the sheet); Enter from <body> presses nothing
//   onboarding-3  a GitHub connect made from the Settings card (and a later
//                 status read either way) reaches a re-run's GitHub step, its
//                 prefs status row and the Ready summary
//   onboarding-4  Back or Esc while the advisor's answers POST is pending: the
//                 models apply is never posted afterwards, and the step Back
//                 landed on keeps a usable primary action
//   onboarding-5  withdrawing the uncensored acknowledgement drops an
//                 uncensored pick the sheet holds AT ONCE (whether the
//                 re-rank answers or fails): Download never sends
//                 ack_uncensored without the stored acknowledgement
//   onboarding-6  connected in the GitHub step, then the footer's Skip (which
//                 never calls save()): the Ready summary still says so, once
//   onboarding-8  a re-run in the same page life re-reads the advisor state
//                 once per open, so its body agrees with the fresh action row
//                 (no "~23 GB to download" next to Continue)
//   github-4      the Settings GitHub card stops offering Review on a change
//                 the agent withdrew (or that expired) at the next 60 s poll
//   github-1      the card's half of the unconfirmed-push seam: a failed list
//                 row WITH pushed_sha (what the server now emits for a push
//                 that may have landed) renders as "Unconfirmed" under Recent
//                 pushes (the server half is in t_gh_changes_review.py)
//   motion-overlays-1  the review sheet opened over a pop-out whose exit is
//                 still running (deep link, Needs-you Open) hides the pop-out
//                 at once and keeps focus; the animated exit used to keep it an
//                 open modal to the overlay watcher, which pulled focus back
//                 and then handed it to the card behind the sheet (the real-
//                 engine twin is tools/tests/browser/t_overlays_real.js)
//   motion-overlays-2  focus that leaves the open review sheet comes back: a
//                 chat turn ending under it (sendMsg focuses the composer) no
//                 longer takes the keyboard, a Tab that escaped wraps, and a
//                 pop-out opened beneath it is closed rather than fought over
//   motion-overlays-3  Esc gives focus back to the control that opened the
//                 sheet even after a repaint replaced it (the Settings card's
//                 poll, the hub's refresh: the same control again, else the
//                 card's title) or it was hidden with the pop-out it sat in
//                 (the hub card's same row)
//
// No dashboard, no network (every /api answer is a route below), no model,
// no GitHub.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const cp = require("child_process");
const { bootDashboard, harness } = require("./_a11y_dom.js");
const STUBS = require("./_anim_stubs.js");

const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const ANIM_SRC = fs.readFileSync(path.join(REPO, "dashboard", "aux_anim.js"), "utf8");
const M27 = "mlx-community/Qwen3.8-27B-4bit", M9 = "mlx-community/Qwen3.5-9B-4bit";
const AUX = ["aux_settings_shell.js", "aux_update.js", "aux_onboarding.js", "aux_network.js",
             "aux_github.js", "aux_advisor.js"];
const clone = function (o) { return JSON.parse(JSON.stringify(o)); };

// --------------------------------------------------------------------------
// fixtures: the real route functions, a synthetic Mac
// --------------------------------------------------------------------------
const GEN = String.raw`
import contextlib, importlib.util, io, json, os, shutil, sys, tempfile, urllib.parse
REPO = sys.argv[1]
TMP = tempfile.mkdtemp(prefix="hermes-seams-fx-")
os.environ["HOME"] = TMP
os.makedirs(os.path.join(TMP, ".hermes", "dashboard"), exist_ok=True)
DASH = os.path.join(REPO, "dashboard")
sys.path.insert(0, DASH)
spec = importlib.util.spec_from_file_location("hsrv_seams", os.path.join(DASH, "server.py"))
srv = importlib.util.module_from_spec(spec); sys.modules["hsrv_seams"] = srv
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(srv)
M27 = "mlx-community/Qwen3.8-27B-4bit"; M9 = "mlx-community/Qwen3.5-9B-4bit"
HWX = srv._adv_machine("Apple M5 Max", 64, gpu_cores=40, machine_name="MacBook Pro",
                       disk_free_gb=412.0, macos="26.1")
CACHED = set()
srv._machine_ram_gb = lambda: 64
srv._model_downloaded = lambda mid: mid in CACHED
srv._draft_ready = lambda e: True
def fake_local(answers=None, hw=None, bust=False):
    inputs = {"measured": {}, "prefix": {}, "drills": {}, "bg_wanted": True,
              "downloaded": set(CACHED), "current": None, "profile": None}
    res = srv._adv_rank(HWX, answers if answers is not None else srv._adv_answers_stored(),
                        downloaded=set(CACHED), current=None)
    return res, HWX, inputs
srv._adv_rank_local = fake_local      # the routes resolve it by name: no detection runs
srv._adv_hw = lambda bust=False: HWX
srv._onb_sysinfo = lambda: {"chip": "Apple M5 Max", "ram_gb": 64, "disk_free_gb": 412.0,
                            "macos": "26.1", "laptop": True, "cores": 12}
class Ctx:
    def __init__(self, query=None, body=None):
        self.query, self.body, self.raw_path, self.headers = query or {}, body, "", {}
def unwrap(o): return o if isinstance(o, dict) else o[0]
out = {}
for name, have in (("none", set()), ("dl", {M27, M9})):
    CACHED.clear(); CACHED.update(have)
    o = {"onb_state": unwrap(srv._onb_state_handler(Ctx({}))),
         "adv_state": unwrap(srv._adv_state_route(Ctx({})))}
    srv._adv_answers_save({"uncensored_ack": True})
    o["rank_unc"] = unwrap(srv._adv_rank_route(Ctx(urllib.parse.parse_qs(
        "uses=chat,agent,uncensored&priority=balanced&power=auto"))))
    srv._adv_answers_save({"uncensored_ack": False})
    out[name] = o
shutil.rmtree(TMP, True)
sys.stdout.write(json.dumps(out, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))
`;

function genFixtures() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-seams-gen-"));
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

function ghStatus(o) {
  return Object.assign({ ok: true, connected: false, configured: false, pending: 0, drift: [], attention: [],
    repos: [], writes_paused: false }, o || {});
}

// one pending change, as the list row and as the full record the review sheet
// reads (/api/github/changes?id=), for the overlay seams below
const GH_ID = "ghc-0123456789ab";
function ghRow(o) {
  const NOW = Math.floor(Date.now() / 1000);
  return Object.assign({ id: GH_ID, repo: "octo/app", private: true, branch: "hermes/fix-copy", slug: "fix-copy", mode: "new",
    state: "pending", title: "Fix the login copy", stat: { files: 1, add: 1, del: 1, bytes: 120 }, approvable: true, blocked: false,
    needs_ack: [], confirm_public: false, requested_at: NOW - 300, expires_at: NOW + 80000, decided_at: null, decision: null,
    pushed_sha: null, pr: { number: null, url: null, draft: true }, error: null, default_branch: "main", source: "api",
    retracted_at: null, review_url: "#github-review=" + GH_ID }, o || {});
}
function ghFull(o) {
  return Object.assign(ghRow(), { body: "Fixes a typo.", checks: [],
    files: [{ path: "src/copy.js", old_path: null, status: "M", old_mode: "100644", new_mode: "100644", add: 1, del: 1, binary: false, size: 100 }],
    diff: "diff --git a/src/copy.js b/src/copy.js\n--- a/src/copy.js\n+++ b/src/copy.js\n@@ -1 +1 @@\n-a\n+b\n", diff_truncated: false,
    digest: "d".repeat(64), tree: "c".repeat(40), commit: "e".repeat(40), base: { branch: "main", sha: "b".repeat(40), at: 1 },
    pr_footer: "Opened by Hermes.", sanitized: { comments_stripped: 0, mentions: 0, title_cut: false, body_cut: false, hidden_chars: 0 },
    target_ref: "refs/heads/hermes/fix-copy", author: { name: "octo", email: "x@users.noreply.github.com" } }, o || {});
}
function ghRoutes(extra) {
  return Object.assign({
    "/api/github/status": ghStatus({ connected: true, login: "octo", pending: 1, enabled: ["octo/app"],
      repos: [{ full_name: "octo/app", private: true, enabled: true, writable: true, writes_ok: true, prepared: true }] }),
    "/api/github/changes": function (url) {
      if (/[?&]id=ghc-/.test(url)) return { ok: true, change: ghFull() };
      return { ok: true, pending: 1, changes: [ghRow()] };
    }
  }, extra || {});
}

// the page. opts: fx ("none"|"dl"), done (the onboarding done flag), github
// (status), routes (override), apply(body, st) / rank(url, st) (override answers)
let FX = null;
async function boot(opts) {
  opts = opts || {};
  const F = FX[opts.fx || "none"];
  const log = { applies: [], answers: [], ranks: [], advState: 0 };
  const st = { onb: clone(F.onb_state), adv: clone(F.adv_state), F: F, done: opts.done === true,
               ackStored: !!(F.adv_state.answers && F.adv_state.answers.uncensored_ack) };
  let L = null;
  const routes = {
    "/api/onboarding/state": function (url) {
      const o = clone(st.onb); o.done = st.done;
      if (/brief=1/.test(url)) return { ok: true, version: o.version, brief: true, done: st.done, done_at: null, done_version: null };
      return o;
    },
    "/api/onboarding/apply": function (url, init) {
      const b = JSON.parse(init.body || "{}"); log.applies.push(b);
      if (opts.apply) { const r = opts.apply(b, st); if (r) return r; }
      return { ok: true, errors: [], applied: Object.assign({}, b, { primary_active: true }), prefs: {} };
    },
    "/api/onboarding/done": function () { st.done = true; return { ok: true, done: true, ts: 1 }; },
    "/api/claude/foryou": { enabled: false },
    "/api/advisor/state": function () {
      log.advState++;
      const s = clone(st.adv); s.answers = s.answers || {}; s.answers.uncensored_ack = st.ackStored; return s;
    },
    "/api/advisor/rank": function (url) {
      log.ranks.push(url);
      if (opts.rank) { const r = opts.rank(url, st); if (r) return r; }
      return { ok: true, result: clone(st.adv.result) };
    },
    "/api/advisor/answers": function (url, init) {
      const b = JSON.parse(init.body || "{}"); log.answers.push(b);
      if (b.uncensored_ack === true || b.uncensored_ack === false) st.ackStored = b.uncensored_ack;
      return { ok: true, answers: Object.assign({}, st.adv.answers || {}, b, { uncensored_ack: st.ackStored, saved_at: 1 }) };
    },
    "/api/github/status": ghStatus(opts.github),
    "/api/github/ambient": { ok: true, ambient: {} },
    "/api/github/changes": { ok: true, pending: 0, changes: [] },
    "/api/access": { dirs: [] },
    "/api/models": { models: [] }
  };
  Object.assign(routes, opts.routes || {});
  const w = await bootDashboard({
    routes: routes, aux: opts.aux || AUX, media: opts.media,
    before: function (x) {
      delete x.toast;
      L = STUBS.install(x, { gsap: true, anime: true });
      x.__run(ANIM_SRC, "aux_anim.js");
    }
  });
  await w.__tick(14);
  const d = w.document;
  const P = { w: w, d: d, L: L, st: st, log: log, ob: w.hermesOnboarding,
    sheet: function () { return d.getElementById("onb-sheet"); },
    q: function (s) { const n = d.getElementById("onb-sheet"); return n ? n.querySelector(s) : null; },
    qa: function (s) { const n = d.getElementById("onb-sheet"); return n ? Array.prototype.slice.call(n.querySelectorAll(s)) : []; },
    text: function () { const n = d.getElementById("onb-sheet"); return n ? n.textContent : ""; },
    key: function () { return w.hermesOnboarding.steps()[w.hermesOnboarding.state.step]; },
    btn: function (a) { return P.q('.onb-foot button[data-act="' + a + '"]'); },
    click: async function (a) {
      const b = P.btn(a);
      if (!b) throw new Error("no " + a + " button on " + P.key());
      w.__click(b); await w.__tick(14);
    },
    foot: function () { const f = P.q(".onb-foot"); return f ? f.textContent.replace(/\s+/g, " ").trim() : ""; },
    inSheet: function () { const a = d.activeElement, n = d.getElementById("onb-sheet"); return !!(a && a !== d.body && n && n.contains(a)); },
    errs: function () { return w.__errors.map(function (e) { return String(e && e.stack || e).split("\n").slice(0, 3).join(" | "); }); }
  };
  return P;
}

async function scenario(name, fn) {
  console.log("\n" + name);
  try { await fn(); }
  catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}

(async function main() {
  try { FX = genFixtures(); }
  catch (e) { T.check("fixtures from the real server.py", false, String(e).slice(0, 900)); T.done(); return; }
  T.check("fixtures from the real routes: a 64 GB Mac recommends the 27B + 9B, nothing downloaded",
    FX.none.onb_state.recommendation.primary === M27 && FX.none.onb_state.recommendation.background === M9 &&
    FX.none.adv_state.result.recommended.downloaded === false, FX.none.onb_state.recommendation);
  T.check("...and with both downloaded, every lane is local in both payloads",
    FX.dl.onb_state.recommendation.already_downloaded === true &&
    FX.dl.adv_state.result.recommended.download_gb === 0, [FX.dl.onb_state.recommendation.already_downloaded,
      FX.dl.adv_state.result.recommended.download_gb]);

  // ======================================================================
  await scenario("[onboarding-1] Enter on the advisor's <summary> never presses Download", async function () {
    const P = await boot({ fx: "none" });
    const w = P.w;
    T.check("the first-run sheet is open on welcome", P.sheet() && !P.sheet().hidden && P.key() === "welcome", P.key());
    await P.click("next");
    T.check("the models step renders the advisor's body, with Download as the primary action",
      !!P.q('[data-adv-root="step"]') && P.btn("download") && P.btn("download").classList.contains("onb-go"), P.foot());
    const sums = P.qa(".onb-adv summary");
    T.check("...and at least one <summary> disclosure ('How we estimate')",
      sums.length >= 1 && sums.some(function (s) { return /How we estimate/.test(s.textContent); }),
      sums.map(function (s) { return s.textContent.trim(); }));
    const n0 = P.log.applies.length;
    let bad = [];
    for (const s of sums) {
      s.focus();
      const ev = w.__key(s, "Enter");
      await w.__tick(12);
      if (ev.defaultPrevented || P.log.applies.length !== n0 || P.key() !== "models") {
        bad.push({ s: s.textContent.trim(), prevented: ev.defaultPrevented, applies: P.log.applies.slice(n0), key: P.key() });
      }
    }
    T.check("Enter on every <summary> posts nothing to /api/onboarding/apply and is not cancelled (so it toggles)",
      bad.length === 0, bad);
    T.check("...the sheet stays on the models step with Download still offered",
      P.key() === "models" && !!P.btn("download") && !P.ob.state.dl, P.foot());
    // the rule is unchanged everywhere else
    const p = P.q(".onb-body p") || P.q(".onb-body");
    const ev = w.__key(p, "Enter");
    await w.__tick(12);
    T.check("Enter on plain text in the sheet still presses the primary action (one download apply)",
      ev.defaultPrevented === true && P.log.applies.length === n0 + 1 && P.log.applies[n0].download === true,
      P.log.applies.slice(n0));
    T.check("no page error", P.errs().length === 0, P.errs());
  });

  // ======================================================================
  await scenario("[onboarding-2] the GitHub step's own repaint cannot strand the keyboard outside the sheet", async function () {
    const cases = [
      ["refused", { __status: 400, body: { ok: false, error: "bad_token", hint: "That is not a GitHub token." } }, /not a GitHub token/],
      ["stored", { ok: true, stored: true, connected: true, login: "octo", repos: [{ full_name: "octo/app", private: true }], pending: 0 }, /Connected as/]
    ];
    for (const c of cases) {
      for (const how of ["enter", "check"]) {
        const P = await boot({ fx: "dl", done: true, routes: { "/api/github/connect": c[1] } });
        const w = P.w, d = P.d;
        await P.ob.open({ rerun: true }); await w.__tick(10);
        P.ob.go(P.ob.steps().indexOf("github")); await w.__tick(12);
        const tok = P.q("input[data-gh-token]");
        if (!tok) { T.check("[" + c[0] + "/" + how + "] the GitHub step shows its token field", false, P.text().slice(0, 200)); continue; }
        tok.value = "github_pat_FAKE_FOR_TESTS_ONLY";
        if (how === "enter") { tok.focus(); w.__key(tok, "Enter"); }
        else { const b = P.q('button[data-gh="check"]'); b.focus(); w.__click(b); }
        await w.__tick(16);
        T.check("[" + c[0] + "/" + how + "] the connect answered and the step repainted (" + c[2] + ")",
          c[2].test(P.text()), P.text().slice(0, 160));
        const tab = w.__key(d.activeElement, "Tab");
        await w.__tick(2);
        T.check("[" + c[0] + "/" + how + "] Tab from wherever focus was left lands INSIDE the sheet",
          P.inSheet(), [d.activeElement && d.activeElement.tagName, tab.defaultPrevented]);
        // back out to body (what the repaint left behind) and press Esc there
        d.activeElement.blur();
        if (d.activeElement !== d.body) { T.check("[" + c[0] + "/" + how + "] (blur parks focus on body)", false); continue; }
        w.__key(d.activeElement, "Escape");
        await w.__tick(4);
        T.check("[" + c[0] + "/" + how + "] Esc with focus on <body> still closes a re-run",
          P.sheet().hidden === true && P.ob.state.open === false, [P.sheet().hidden, P.ob.state.open]);
        T.check("[" + c[0] + "/" + how + "] no page error", P.errs().length === 0, P.errs());
      }
    }
    // a first run still swallows Esc, from <body> too, and Enter from <body>
    // presses nothing
    const P = await boot({ fx: "none" });
    const w = P.w, d = P.d;
    d.activeElement.blur();
    const esc = w.__key(d.body, "Escape");
    const ent = w.__key(d.body, "Enter");
    await w.__tick(6);
    T.check("first run: Esc from <body> is swallowed (the sheet stays open)",
      P.sheet().hidden === false && P.ob.state.open === true && esc.defaultPrevented === true);
    T.check("...and Enter from <body> presses nothing (still on welcome, no apply)",
      P.key() === "welcome" && P.log.applies.length === 0 && ent.defaultPrevented === false, [P.key(), P.log.applies]);
  });

  // ======================================================================
  await scenario("[onboarding-3] a connect made from the Settings card reaches a later re-run's GitHub step", async function () {
    let connected = false;
    const OKC = { ok: true, stored: true, connected: true, login: "octo", repos: [{ full_name: "octo/app", private: true }], pending: 0 };
    const P = await boot({ fx: "dl", done: true, routes: {
      "/api/github/status": function () {
        return connected ? ghStatus({ connected: true, login: "octo", repos: [{ full_name: "octo/app", enabled: true }] }) : ghStatus();
      },
      "/api/github/connect": function () { connected = true; return OKC; }
    } });
    const w = P.w, d = P.d, ob = P.ob;
    await ob.open({ rerun: true }); await w.__tick(10);
    ob.go(ob.steps().indexOf("github")); await w.__tick(12);
    T.check("visit 1 (not connected): the step shows its token field", !!P.q("input[data-gh-token]"));
    ob.close(); await w.__tick(4);
    w.__run("setView('mind')"); await w.__run("mindExtras()"); await w.__tick(16);
    const tok = d.getElementById("ghcard-token");
    T.check("the Settings card shows its own token field", !!tok);
    tok.value = "github_pat_FAKE_FOR_TESTS_ONLY";
    w.__click(d.querySelector('#mind-extra-github button[data-gh="check"]')); await w.__tick(20);
    T.check("...and connects from there", /Connected as/.test(d.getElementById("mind-extra-github").textContent) &&
      w.hermesGitHub.state.status && w.hermesGitHub.state.status.connected === true);
    await ob.open({ rerun: true }); await w.__tick(10);
    ob.go(ob.steps().indexOf("prefs")); await w.__tick(10);
    const row = P.qa(".onb-st").filter(function (r) { return /GitHub/.test(r.textContent); })
      .map(function (r) { return r.textContent.replace(/\s+/g, " "); });
    T.check("visit 2: the prefs step's GitHub row says connected as @octo",
      row.length === 1 && /Connected as @octo/.test(row[0]) && !/Not connected/.test(row[0]), row);
    ob.go(ob.steps().indexOf("github")); await w.__tick(12);
    T.check("...the GitHub step says it is already connected, and asks for no token",
      /already connected as @octo/.test(P.text()) && !P.q("input[data-gh-token]"), P.text().slice(0, 200));
    await P.click("next");
    const lines = P.qa(".onb-sum li").map(function (l) { return l.textContent; }).filter(function (l) { return /github/i.test(l); });
    T.check("...and the Ready summary says it is unchanged, never 'not connected'",
      lines.length === 1 && /already connected as @octo/.test(lines[0]), lines);
    T.check("no page error", P.errs().length === 0, P.errs());

    // the other direction: a status read that says disconnected corrects a
    // connected copy (no stale "connected" either)
    const P2 = await boot({ fx: "dl", done: true, github: { connected: true, login: "octo" } });
    await P2.ob.open({ rerun: true }); await P2.w.__tick(10);
    P2.ob.go(P2.ob.steps().indexOf("github")); await P2.w.__tick(12);
    T.check("(a step that read 'connected')", /already connected/.test(P2.text()), P2.text().slice(0, 120));
    P2.ob.close();
    P2.w.__routes["/api/github/status"] = ghStatus();
    await P2.w.hermesGitHub.poll(true); await P2.w.__tick(8);
    await P2.ob.open({ rerun: true }); await P2.w.__tick(10);
    P2.ob.go(P2.ob.steps().indexOf("github")); await P2.w.__tick(12);
    T.check("...then a poll that reads 'disconnected': the next re-run offers the token field again",
      !!P2.q("input[data-gh-token]") && !/already connected/.test(P2.text()), P2.text().slice(0, 160));
  });

  // ======================================================================
  await scenario("[onboarding-4] Back or Esc while the advisor's answers save is pending applies nothing", async function () {
    for (const how of ["back", "esc"]) {
      let release = null;
      const gate = new Promise(function (r) { release = r; });
      const P = await boot({ fx: "dl", done: how === "esc", routes: {
        "/api/advisor/answers": async function (url, init) {
          P.log.answers.push(JSON.parse(init.body || "{}"));
          await gate;
          return { ok: true, answers: { uses: ["chat", "agent"], priority: "best", power: null, saved_at: 1 } };
        }
      } });
      const w = P.w, ob = P.ob;
      if (how === "esc") { await ob.open({ rerun: true }); await w.__tick(8); ob.go(1); await w.__tick(8); }
      else await P.click("next");
      T.check("[" + how + "] the models step offers Continue (both models are on this Mac)",
        P.key() === "models" && P.btn("next") && P.btn("next").classList.contains("onb-go"), P.foot());
      // answers that differ from the stored ones, so save() really posts
      const pri = P.qa("input[data-pri]").filter(function (i) { return i.getAttribute("data-pri") === "best"; })[0];
      pri.checked = true; pri.dispatchEvent(new w.Event("change", { bubbles: true })); await w.__tick(6);
      w.__click(P.btn("next"));
      await w.__tick(4);
      T.check("[" + how + "] Continue is waiting on the answers POST (busy, no models apply yet)",
        ob.state.busy === "save" && P.log.answers.length >= 1 &&
        P.log.applies.filter(function (b) { return b.primary !== undefined; }).length === 0,
        [ob.state.busy, P.log.answers.length, P.log.applies]);
      if (how === "back") { w.__click(P.btn("back")); await w.__tick(4); }
      else { w.__key(P.q(".onb-go"), "Escape"); await w.__tick(4); }
      T.check("[" + how + "] " + (how === "back" ? "Back went to welcome" : "Esc closed the re-run"),
        how === "back" ? P.key() === "welcome" : (P.sheet().hidden === true && ob.state.open === false), P.key());
      release(); await w.__tick(20);
      const models = P.log.applies.filter(function (b) { return b.primary !== undefined; });
      T.check("[" + how + "] ...and when the answers POST settles, no models apply is posted",
        models.length === 0, models);
      if (how === "back") {
        const go = P.q(".onb-go");
        T.check("[back] ...and welcome's Continue is usable (a stale save does not leave it disabled)",
          ob.state.busy === "" && go && !go.disabled && P.key() === "welcome", [ob.state.busy, go && go.disabled, P.foot()]);
      }
      T.check("[" + how + "] no page error", P.errs().length === 0, P.errs());
    }
    // the normal path is unchanged: Continue with a prompt answer applies once
    const P = await boot({ fx: "dl" });
    await P.click("next");
    await P.click("next");
    const models = P.log.applies.filter(function (b) { return b.primary !== undefined; });
    T.check("Continue with the save answering: one models apply with the picks and no download, then prefs",
      models.length === 1 && models[0].primary === M27 && models[0].background === M9 && models[0].download === false &&
      P.key() === "prefs", [models, P.key()]);
  });

  // ======================================================================
  await scenario("[onboarding-5] withdrawing the uncensored acknowledgement drops an uncensored pick at once", async function () {
    const UNC = function (id) { return /abliterated|uncensored/i.test(String(id || "")); };
    for (const rankFails of [false, true]) {
      const tag = rankFails ? "[re-rank fails]" : "[re-rank answers]";
      let failing = false;
      const P = await boot({ fx: "none",
        rank: function (url, st) {
          if (failing) return { __status: 500, body: { ok: false, error: "boom" } };
          if (/uncensored/.test(decodeURIComponent(url)) && st.ackStored) return clone(st.F.rank_unc);
          return null;
        },
        // the server's gate (_onb_ack_gate): a per-request ack goes through,
        // otherwise an uncensored pick needs the STORED acknowledgement
        apply: function (b, st) {
          if (b.ack_uncensored === true) return null;
          if ([b.primary, b.background].some(UNC) && !st.ackStored) {
            return { __status: 409, body: { ok: false, needs_ack: true, error: "uncensored_ack", errors: ["needs the acknowledgement"], applied: {} } };
          }
          return null;
        } });
      const w = P.w, ob = P.ob;
      await P.click("next");
      const chip = P.q('input[data-use="uncensored"]');
      chip.checked = true; chip.dispatchEvent(new w.Event("change", { bubbles: true })); await w.__tick(10);
      const ack = P.q("input[data-ack]");
      ack.checked = true; ack.dispatchEvent(new w.Event("change", { bubbles: true })); await w.__tick(10);
      w.__flushTimers(2); await w.__tick(14);
      const u = P.qa("input[data-pick]").filter(function (i) { return UNC(i.getAttribute("data-pick")); })[0];
      T.check(tag + " with the acknowledgement stored, an uncensored alternative is offered", !!u && !u.disabled && P.st.ackStored === true);
      if (!u) continue;
      u.checked = true; u.dispatchEvent(new w.Event("change", { bubbles: true })); await w.__tick(10);
      T.check(tag + " ...and picking it hands the sheet an uncensored pick",
        ob.state.pick && UNC(ob.state.pick.primary) && ob.state.pick.uncensored === true, ob.state.pick);
      failing = rankFails;
      const ack2 = P.q("input[data-ack]");
      ack2.checked = false; ack2.dispatchEvent(new w.Event("change", { bubbles: true })); await w.__tick(10);
      T.check(tag + " withdrawing it: the stored acknowledgement is gone, and the sheet's pick is stock AT ONCE (before any re-rank)",
        P.st.ackStored === false && ob.state.pick && !UNC(ob.state.pick.primary) && ob.state.pick.uncensored === false &&
        ob.state.pick.primary === M27, ob.state.pick);
      w.__flushTimers(3); await w.__tick(14);
      T.check(tag + " ...still stock once the re-rank " + (rankFails ? "failed" : "answered"),
        ob.state.pick && !UNC(ob.state.pick.primary) && ob.state.pick.uncensored === false, ob.state.pick);
      const picked = P.qa("input[data-pick]").filter(function (i) { return i.checked; }).map(function (i) { return i.getAttribute("data-pick"); });
      T.check(tag + " ...and the advisor body shows the Recommended plan picked, not the locked uncensored one",
        picked.length >= 1 && picked.every(function (k) { return !UNC(k); }), picked);
      const n0 = P.log.applies.length;
      await P.click("download");
      const b = P.log.applies[n0] || {};
      T.check(tag + " Download posts the stock picks with no ack_uncensored",
        b.primary === M27 && b.background === M9 && b.download === true && !("ack_uncensored" in b), b);
      T.check(tag + " no page error", P.errs().length === 0, P.errs());
    }
  });

  // ======================================================================
  await scenario("[onboarding-6] connected in the GitHub step, then Skip: the Ready summary still says so", async function () {
    const OK = { ok: true, stored: true, connected: true, login: "octo", repos: [{ full_name: "octo/app", private: true }], pending: 0 };
    for (const how of ["next", "skip"]) {
      const P = await boot({ fx: "dl", done: true, routes: { "/api/github/connect": OK } });
      const w = P.w, ob = P.ob;
      await ob.open({ rerun: true }); await w.__tick(10);
      ob.go(ob.steps().indexOf("github")); await w.__tick(10);
      P.q("input[data-gh-token]").value = "github_pat_FAKE_FOR_TESTS_ONLY";
      w.__click(P.q('button[data-gh="check"]')); await w.__tick(16);
      T.check("[" + how + "] the step connected (its success panel), and the footer still offers Skip",
        /Connected as/.test(P.text()) && !!P.btn("skip"), P.foot());
      await P.click(how);
      const lines = P.qa(".onb-sum li").map(function (l) { return l.textContent; }).filter(function (l) { return /github/i.test(l); });
      T.check("[" + how + "] the Ready step lists exactly one line 'GitHub connected as @octo'",
        P.key() === "done" && lines.length === 1 && lines[0] === "GitHub connected as @octo", [P.key(), lines]);
      T.check("[" + how + "] no page error", P.errs().length === 0, P.errs());
    }
  });

  // ======================================================================
  await scenario("[onboarding-8] a re-run's advisor body is re-read, so it agrees with the fresh action row", async function () {
    const P = await boot({ fx: "none" });
    const w = P.w, ob = P.ob;
    await P.click("next");
    const rec1 = P.q(".adv-rec");
    T.check("first run: the Recommended plan quotes its download (~23 GB) and the row says Download",
      rec1 && /~23\sGB to download/.test(rec1.textContent) && /Download recommended/.test(P.foot()), P.foot());
    await P.click("download");
    await P.click("next"); await P.click("next"); await P.click("skip"); await P.click("finish");
    T.check("...the first run finished", P.sheet().hidden === true && P.st.done === true);
    // the download completes: the server now reports both models on this Mac
    P.st.onb = clone(FX.dl.onb_state); P.st.adv = clone(FX.dl.adv_state);
    const g0 = P.log.advState;
    await ob.open({ rerun: true }); await w.__tick(14);
    ob.go(1); await w.__tick(14);
    const rec = P.q(".adv-rec");
    const t = rec ? rec.textContent.replace(/\s+/g, " ") : "";
    T.check("re-run, same page life (well inside 5 minutes): the action row says Continue",
      /Continue/.test(P.foot()) && !/Download recommended/.test(P.foot()), P.foot());
    T.check("...the advisor re-read its state once for this open", P.log.advState === g0 + 1, P.log.advState - g0);
    T.check("...and its Recommended plan says it is already on this Mac (no '~23 GB to download')",
      /already on this Mac/.test(t) && !/GB to download/.test(t) && !/GB download/.test(t), t.slice(0, 400));
    T.check("...the advisor's own result agrees", w.hermesAdvisor.result.recommended.downloaded === true &&
      w.hermesAdvisor.result.recommended.download_gb === 0, w.hermesAdvisor.result.recommended.download_gb);
    // repaints inside the SAME open do not re-read
    ob.go(2); await w.__tick(8); ob.go(1); await w.__tick(14);
    T.check("...Back and forth within the same open does not re-read again", P.log.advState === g0 + 1, P.log.advState - g0);
    T.check("no page error", P.errs().length === 0, P.errs());
  });

  // ======================================================================
  await scenario("[github-4] the Settings card drops a change that left the pending set (withdrawn, expired)", async function () {
    const ID = "ghc-0123456789ab";
    const NOW = Math.floor(Date.now() / 1000);
    const change = function (state) {
      return { id: ID, repo: "octo/app", private: true, branch: "hermes/fix-copy", slug: "fix-copy", mode: "new", state: state,
        title: "Fix the login copy", stat: { files: 1, add: 1, del: 1, bytes: 120 }, approvable: state === "pending", blocked: false,
        needs_ack: [], confirm_public: false, requested_at: NOW - 300, expires_at: NOW + 80000, decided_at: null, decision: null,
        pushed_sha: null, pr: { number: null, url: null, draft: true }, error: null, default_branch: "main", source: "api",
        retracted_at: null, review_url: "#github-review=" + ID };
    };
    for (const gone of ["withdrawn", "expired"]) {
      let state = "pending";
      const status = function () {
        return ghStatus({ connected: true, login: "octo", pending: state === "pending" ? 1 : 0,
          repos: [{ full_name: "octo/app", private: true, enabled: true, writable: true, writes_ok: true, prepared: true }],
          enabled: ["octo/app"] });
      };
      const P = await boot({ fx: "dl", done: true, routes: {
        "/api/github/status": status,
        "/api/github/changes": function (url) {
          if (/state=pending/.test(url)) return { ok: true, pending: state === "pending" ? 1 : 0, changes: state === "pending" ? [change("pending")] : [] };
          return { ok: true, pending: state === "pending" ? 1 : 0, changes: [change(state)] };
        }
      } });
      const w = P.w, d = P.d;
      w.__run("setView('mind')"); await w.__run("mindExtras()"); await w.__tick(20);
      const card = function () { return d.getElementById("mind-extra-github"); };
      const review = function () { return card() && card().querySelector('button[data-gh="review"][data-id="' + ID + '"]'); };
      T.check("[" + gone + "] the card shows '1 waiting' with a Review button",
        !!card() && /1 waiting/.test(card().textContent) && !!review(), card() && card().textContent.slice(0, 200));
      state = gone;
      await w.hermesGitHub.poll(true); await w.__tick(20);
      T.check("[" + gone + "] after the next poll: no '1 waiting', no Review button for it",
        !/1 waiting/.test(card().textContent) && !/Waiting for your review/.test(card().textContent) && !review(),
        card().textContent.slice(0, 240));
      T.check("[" + gone + "] no page error", P.errs().length === 0, P.errs());
    }
  });

  // ======================================================================
  await scenario("[github-1] a push GitHub could not confirm is listed under Recent pushes as Unconfirmed", async function () {
    // the list row aux_ghchanges.py's _ghc_summary now emits for it (state
    // failed WITH pushed_sha; t_gh_changes_review.py proves the server half)
    const ID = "ghc-00000000ffff", SHA = "a".repeat(40), NOW = Math.floor(Date.now() / 1000);
    const row = { id: ID, repo: "octo/app", private: true, branch: "hermes/unconfirmed", slug: "unconfirmed", mode: "new",
      state: "failed", title: "Unconfirmed push", stat: { files: 1, add: 1, del: 1, bytes: 21 }, approvable: true,
      blocked: false, needs_ack: [], confirm_public: false, requested_at: NOW - 60, expires_at: NOW + 86000,
      decided_at: NOW - 30, decision: "push_only", pushed_sha: SHA, pr: { number: null, url: null, draft: true },
      error: "git reported the push, but GitHub does not show it on hermes/unconfirmed yet.", default_branch: "main",
      parent: "b".repeat(40), source: "api", retracted_at: null, review_url: "#github-review=" + ID };
    const P = await boot({ fx: "dl", done: true, routes: {
      "/api/github/status": ghStatus({ connected: true, login: "octo", repos: [{ full_name: "octo/app", enabled: true }] }),
      "/api/github/changes": { ok: true, pending: 0, changes: [row] }
    } });
    const w = P.w, d = P.d;
    w.__run("setView('mind')"); await w.__run("mindExtras()"); await w.__tick(20);
    const card = d.getElementById("mind-extra-github");
    const t = card ? card.textContent.replace(/\s+/g, " ") : "";
    T.check("the card lists it under Recent pushes with an Unconfirmed chip, its branch and short sha",
      /Recent pushes/.test(t) && /Unconfirmed push\s*Unconfirmed/.test(t) && /hermes\/unconfirmed @ aaaaaaa/.test(t), t.slice(0, 400));
    T.check("...with a Details button to its review sheet",
      !!(card && card.querySelector('button[data-gh="review"][data-id="' + ID + '"]')));
    T.check("no page error", P.errs().length === 0, P.errs());
  });

  // ======================================================================
  await scenario("[motion-overlays-1] the review sheet opened over a CLOSING pop-out keeps the keyboard", async function () {
    const cases = [
      ["deep link over an open pop-out", false, false],
      ["Needs-you Open (closePop() first, as aux_needsyou does)", true, false],
      ["OS reduced motion (the instant path, the control)", true, true]
    ];
    for (const c of cases) {
      const tag = "[" + c[0] + "]";
      const P = await boot({ fx: "dl", done: true, routes: ghRoutes(), media: c[2] ? function (q) { return /reduce/.test(q); } : null });
      const w = P.w, d = P.d, pop = d.getElementById("wpop");
      const inGh = function () { const s = d.getElementById("gh-review"), a = d.activeElement; return !!(s && !s.hidden && a && a !== d.body && s.contains(a)); };
      w.__run("openPop('system')"); await w.__tick(8);
      T.check(tag + " the pop-out is open, focus in its ask field", !pop.hidden && d.activeElement && d.activeElement.id === "pop-ask",
        d.activeElement && d.activeElement.id);
      if (c[1]) w.__run("closePop()");
      const opened = w.hermesGitHub.openReview(GH_ID);
      T.check(tag + " the pop-out is hidden the moment the sheet paints, and the sheet holds focus",
        pop.hidden === true && inGh(), [pop.hidden, d.activeElement && (d.activeElement.id || d.activeElement.className)]);
      await opened; await w.__tick(12);
      T.check(tag + " ...still in the sheet once the change has loaded", inGh(), d.activeElement && (d.activeElement.id || d.activeElement.className));
      P.L.finishAll(); await w.__tick(12);
      T.check(tag + " ...and once every animation has ended (never the card behind the sheet)",
        inGh() && !(d.activeElement.closest && d.activeElement.closest("#wgrid")), d.activeElement && (d.activeElement.id || d.activeElement.className));
      w.__key(d.activeElement, "Enter"); await w.__tick(8);
      T.check(tag + " Enter there opens no pop-out beneath the sheet", pop.hidden === true && inGh());
      T.check(tag + " the pop-out was not left half-faded (no inline opacity/transform)",
        !d.getElementById("wpop").style.opacity && !d.getElementById("wpop-sheet").style.transform, [pop.style.cssText, d.getElementById("wpop-sheet").style.cssText]);
      T.check(tag + " no page error", P.errs().length === 0, P.errs());
    }
  });

  // ======================================================================
  await scenario("[motion-overlays-2] a chat turn that ends under the review sheet cannot take the keyboard", async function () {
    let release = null;
    const gate = new Promise(function (r) { release = r; });
    const P = await boot({ fx: "dl", done: true, routes: ghRoutes({
      "/api/chat": async function () { await gate; return { ok: true, reply: "Done." }; }
    }) });
    const w = P.w, d = P.d;
    const chats = function () { return w.__fetches.filter(function (f) { return f.url.split("?")[0] === "/api/chat"; }).length; };
    const sheet = function () { return d.getElementById("gh-review"); };
    const where = function () { const a = d.activeElement; return !a || a === d.body ? "body" : (a.id || a.getAttribute("data-ghr-body") != null && "body-field" || a.tagName); };
    const input = d.getElementById("input");
    input.focus();
    input.value = "deploy the fix";
    const turn = w.sendMsg();                       // the turn is running: /api/chat waits on the gate
    await w.__tick(4);
    T.check("a turn is in flight from the composer", chats() === 1);
    input.focus();
    await w.hermesGitHub.openReview(GH_ID); await w.__tick(12);
    T.check("the review sheet opens over it with focus on its heading", sheet() && !sheet().hidden && d.activeElement.id === "gh-rv-h", where());
    const ta = sheet().querySelector("textarea[data-ghr-body]");
    T.check("...and has the PR description field", !!ta);
    ta.focus(); ta.value = "Fixes a typo. first line";
    release(); await turn; await w.__tick(12);
    T.check("the turn ends (sendMsg focuses the composer): focus goes BACK to the field being typed in",
      d.activeElement === ta, where());
    // the owner keeps typing (into whatever has focus) and presses Enter
    const typing = d.activeElement;
    if (typing && "value" in typing) typing.value += "\nsecond";
    w.__key(d.activeElement, "Enter"); await w.__tick(8);
    T.check("...so what they type next lands in the description", /first line\nsecond$/.test(ta.value), ta.value);
    T.check("...the composer stays empty", input.value === "", input.value);
    T.check("...and Enter sends no chat message from behind the sheet", chats() === 1, chats());
    // focus moved by a script with nothing in the sheet to go back to: the heading
    d.getElementById("gh-rv-h").focus();
    d.getElementById("send").focus();
    T.check("a .focus() on another control behind the sheet comes back to the sheet", sheet().contains(d.activeElement), where());
    // a Tab the trap let through (WebKit skips buttons): wraps, never sticks or leaves
    ta.focus();
    const tab = w.__key(ta, "Tab");
    T.check("(a Tab from the field is not the trap's to wrap)", tab.defaultPrevented === false);
    input.focus();                                  // where the browser's default move would land
    const f = Array.prototype.slice.call(sheet().querySelectorAll("button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex]:not([tabindex=\"-1\"])"))
      .filter(function (x) { return x.getClientRects().length > 0; });
    T.check("...a Tab that escaped wraps to the sheet's first control", d.activeElement === f[0], [where(), f[0] && f[0].outerHTML.slice(0, 80)]);
    // a pop-out opened beneath the sheet is closed, so the watcher and the sheet never trade focus
    w.__run("openPop('system')"); await w.__tick(8);
    T.check("a pop-out opened beneath the sheet is closed, and focus stays in the sheet",
      d.getElementById("wpop").hidden === true && sheet().contains(d.activeElement), [d.getElementById("wpop").hidden, where()]);
    // closing: the guard is off, so the sheet's own focus return wins
    input.focus();
    T.check("(focus is back in the sheet)", sheet().contains(d.activeElement));
    w.__key(d.activeElement, "Escape"); await w.__tick(4);
    P.L.finishAll(); await w.__tick(8);
    T.check("Esc closes it and focus goes back to the composer that opened it (the guard does not fight the close)",
      sheet().hidden === true && d.activeElement === input, [sheet().hidden, where()]);
    input.focus(); d.getElementById("send").focus();
    T.check("...and with the sheet closed nothing pulls focus anywhere", d.activeElement === d.getElementById("send"), where());
    T.check("no page error", P.errs().length === 0, P.errs());
  });

  // ======================================================================
  await scenario("[motion-overlays-3] Esc returns focus to the opener even after a repaint replaced it", async function () {
    const NY_ROW = "github:" + GH_ID;
    const ny = function (withRow) {
      const NOW = Math.floor(Date.now() / 1000);
      return { building: false, today: [], later: [], never_count: 0, sources: { github: { present: true } }, metrics: {},
        generated_at: NOW, next_brief: null, now: withRow ? [{ id: NY_ROW, source: "github", title: "Fix the login copy",
          sender: "", summary: "octo/app hermes/fix-copy", reason: "A change is waiting for your decision.", bucket: "now",
          received_at: NOW - 400, deadline_ts: null, confidence: 0.9 }] : [] };
    };
    let rowThere = true;
    const hub = function () {
      return { order: ["needsyou", "system"], model_online: true, widgets: {
        needsyou: { meta: { title: "Needs you", size: "wide", cat: "assistant" }, data: ny(rowThere) },
        system: { meta: { title: "System", size: "s", cat: "system" }, data: { cpu_pct: 20, ram_pct: 50, disk_pct: 70 } } } };
    };
    const aux = AUX.slice(0, AUX.indexOf("aux_github.js") + 1).concat(["aux_needsyou.js"], AUX.slice(AUX.indexOf("aux_github.js") + 1));
    const routes = ghRoutes({
      "/api/hub": hub,
      "/api/needsyou": function () { return ny(rowThere); },
      "/api/expand": function (url) { return /id=needsyou/.test(url) ? Object.assign({ rich: true }, ny(rowThere)) : { title: "x" }; },
      "/api/needsyou/act": function (url, init) {
        const b = JSON.parse(init.body || "{}");
        return b.action === "open" ? { ok: true, open: "#github-review=" + GH_ID } : { ok: true };
      },
      "/api/needsyou/seen": { ok: true }
    });
    const desc = function (a, d) { return !a || a === d.body ? "BODY" : (a.tagName + "." + (a.className || "") + (a.closest && a.closest("#wgrid") ? " (hub)" : "")); };

    // (a) the Settings card's Review button, then the card's own poll repaints it
    {
      const P = await boot({ fx: "dl", done: true, routes: routes, aux: aux });
      const w = P.w, d = P.d;
      await w.hermesGitHub.openCard(); await w.__tick(20);       // Settings › Connections, the card on screen
      const sel = '#mind-extra-github button[data-gh="review"][data-id="' + GH_ID + '"]';
      const btn = d.querySelector(sel);
      T.check("[settings card] it offers Review for the change", !!btn);
      btn.focus();
      T.check("[settings card] (the keyboard is on that button)", d.activeElement === btn, desc(d.activeElement, d));
      w.__click(btn); await w.__tick(16);
      T.check("[settings card] Review (keyboard) opens the sheet", !d.getElementById("gh-review").hidden && d.getElementById("gh-review").contains(d.activeElement));
      w.hermesGitHub.paintCard(false); await w.__tick(8);
      const btn2 = d.querySelector(sel);
      T.check("[settings card] (a repaint while it is open replaces that button)", btn.isConnected === false && !!btn2 && btn2 !== btn);
      w.__key(d.activeElement, "Escape"); await w.__tick(4); P.L.finishAll(); await w.__tick(8);
      T.check("[settings card] Esc: focus lands on the NEW Review button for the same change, never <body>",
        d.getElementById("gh-review").hidden === true && d.activeElement === btn2, desc(d.activeElement, d));
      T.check("[settings card] no page error", P.errs().length === 0, P.errs());
    }

    // (b) the hub's Needs-you row, then the hub's 60 s refresh re-renders the card
    for (const gone of [false, true]) {
      const tag = gone ? "[hub row, the row gone after the refresh]" : "[hub row]";
      rowThere = true;
      const P = await boot({ fx: "dl", done: true, routes: routes, aux: aux });
      const w = P.w, d = P.d;
      await w.__tick(20);
      const sel = '#wgrid .w[data-id="needsyou"] .ny-row[data-id="' + NY_ROW + '"] button[data-a="open"]';
      const open = d.querySelector(sel);
      T.check(tag + " the hub's Needs-you card shows the change with Open", !!open, d.querySelector('#wgrid .w[data-id="needsyou"]') ? "card, no row" : "no card");
      if (!open) continue;
      open.focus(); w.__click(open); await w.__tick(20);
      T.check(tag + " Open (keyboard) opens the review sheet", !d.getElementById("gh-review").hidden && d.getElementById("gh-review").contains(d.activeElement),
        desc(d.activeElement, d));
      if (gone) rowThere = false;
      await w.__run("refreshHub()"); await w.__tick(20);
      T.check(tag + " (the refresh replaced the card's rows)", open.isConnected === false);
      w.__key(d.activeElement, "Escape"); await w.__tick(4); P.L.finishAll(); await w.__tick(8);
      const want = gone ? d.querySelector('#wgrid .w[data-id="needsyou"] .w-title') : d.querySelector(sel);
      T.check(tag + " Esc: focus lands on " + (gone ? "the card's title (the row is gone)" : "the same row's NEW Open button") + ", never <body>",
        !!want && d.activeElement === want, desc(d.activeElement, d));
      T.check(tag + " no page error", P.errs().length === 0, P.errs());
    }

    // (c) the Needs-you POP-OUT's row: it is hidden with the pop-out, so focus
    // goes to the same row on the hub card
    {
      rowThere = true;
      const P = await boot({ fx: "dl", done: true, routes: routes, aux: aux });
      const w = P.w, d = P.d;
      await w.__tick(20);
      w.__run("openPop('needsyou')"); await w.__tick(20);
      const po = d.querySelector('#wpop-sheet .ny-row[data-id="' + NY_ROW + '"] button[data-a="open"]');
      T.check("[pop-out row] the pop-out lists the change with Open", !!po);
      if (po) {
        po.focus(); w.__click(po); await w.__tick(20);
        T.check("[pop-out row] Open (keyboard) opens the sheet and closes the pop-out",
          d.getElementById("wpop").hidden === true && d.getElementById("gh-review").contains(d.activeElement), desc(d.activeElement, d));
        w.__key(d.activeElement, "Escape"); await w.__tick(4); P.L.finishAll(); await w.__tick(8);
        const hubOpen = d.querySelector('#wgrid .w[data-id="needsyou"] .ny-row[data-id="' + NY_ROW + '"] button[data-a="open"]');
        T.check("[pop-out row] Esc: focus lands on the hub card's Open for the same row, never <body>",
          !!hubOpen && d.activeElement === hubOpen, desc(d.activeElement, d));
      }
      T.check("[pop-out row] no page error", P.errs().length === 0, P.errs());
    }
  });

  T.done();
})().catch(function (e) { T.check("suite ran", false, String(e && e.stack || e)); T.done(); });
