// E119 (2026-11-09 audit, P3): when a model drill ends, the owner is told if
// it produced no verdict, or if the assistant's model was not put back (the
// restore failed, or was left alone because of a Pause, the owner's own
// switch, or a dashboard restart).
//
// BEFORE: aux_promotion.js only re-read /api/models when its poll ended; it
// never read last.error or last.restore, and no other code read
// /api/models/drill — an aborted drill, and a failed restore that left
// Telegram and the hub on the drilled model, showed nowhere.
//
// AFTER: drillOutcome(st) builds one plain-text line from st.last, the poll
// toasts it when the drill ends, and a restore made after a dashboard restart
// is announced once per page life while it is fresh.
//
// Runs the REAL dashboard/aux_promotion.js in a vm with a stub window: no
// DOM beyond what the module guards for, no network, no model.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_promotion.js");
const fails = [];
let passed = 0;
function check(name, cond, extra) {
  if (cond) { passed++; console.log("  ok   " + name); }
  else { fails.push(name); console.log("  FAIL " + name + "  " + (extra === undefined ? "" : JSON.stringify(extra))); }
}

function boot(drillState) {
  const toasts = [];
  const intervals = [];
  const win = {
    toast: (m, ms) => toasts.push(m),
    setInterval: (fn) => { intervals.push(fn); return intervals.length; },
    clearInterval: () => {},
  };
  const sandbox = {
    window: win, console, Date, JSON, String, Math, Promise,
    fetch: async (url) => ({
      json: async () => (url === "/api/models/drill" ? drillState() : { models: [] }),
    }),
  };
  win.window = win;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });
  return { win, toasts, intervals };
}

(async () => {
  console.log("\n1. drillOutcome() — one plain line per case");
  const { win } = boot(() => ({}));
  const G = win.promotionGate;
  check("promotionGate.outcome is exported", G && typeof G.outcome === "function");
  if (!G || typeof G.outcome !== "function") {
    console.log("\nTESTS " + passed + " passed " + (fails.length) + " failed");
    process.exit(1);
  }
  const O = G.outcome;
  check("no last -> nothing to say", O({}) === "" && O(null) === "" && O({ last: null }) === "");
  check("a clean finish -> nothing to say (the badge shows it)",
        O({ last: { model: "org/cand", error: null, restore: { ok: true, model: "org/stock" } } }) === "");
  let m = O({ last: { model: "org/cand", error: "RuntimeError: model server error in case chain" } });
  check("an aborted run says so, names the model and the reason",
        /cand/.test(m) && /no verdict/.test(m) && /model server error/.test(m), m);
  m = O({ last: { model: "org/cand", error: null,
                  restore: { ok: false, model: "org/stock", error: "launchctl refused" } } });
  check("a FAILED restore says the original was not put back, and how to fix it",
        /Could not switch back to stock/.test(m) && /model menu/.test(m) && /launchctl refused/.test(m), m);
  m = O({ last: { model: "org/cand", error: "x",
                  restore: { ok: true, model: "org/stock", skipped: "paused", files_only: true } } });
  check("left paused: says the pause holds and what Resume loads",
        /stays paused/.test(m) && /stock loads on Resume/.test(m), m);
  m = O({ last: { model: "org/cand", error: "x",
                  restore: { ok: true, model: "org/stock", skipped: "switched", active: "org/third" } } });
  check("the owner switched: says it was left as they set it",
        /switched to third/.test(m) && /left as you set it/.test(m), m);
  m = O({ last: { model: "org/cand", error: null,
                  restore: { ok: true, model: "org/stock", skipped: "down", files_only: true,
                             after_restart: true } } });
  check("after a dashboard restart: says so",
        /dashboard restarted/.test(m) && /stock is the assistant's model again/.test(m), m);
  m = O({ last: { model: "<img src=x onerror=alert(1)>", error: "a\n\nb" } });
  check("plain text only (toast sets textContent) and whitespace collapsed",
        m.indexOf("\n") < 0 && /a b/.test(m), m);

  console.log("\n2. the poll toasts the outcome when the drill ends");
  let st = { running: "org/cand" };
  const env = boot(() => st);
  env.win.promotionGate.poll();
  check("the poll is scheduled", env.intervals.length === 1);
  await env.intervals[0]();
  check("no toast while it runs", env.toasts.length === 0, env.toasts);
  st = { running: null, last: { model: "org/cand", error: null,
                                restore: { ok: false, model: "org/stock", error: "boom" } } };
  await env.intervals[0]();
  check("one toast when it ends, carrying the failed restore",
        env.toasts.length === 1 && /Could not switch back/.test(env.toasts[0]), env.toasts);

  console.log("\n3. a restore made after a dashboard restart is announced once, while fresh");
  const now = Date.now() / 1000;
  const fresh = { running: null, last: { model: "org/cand", error: "the dashboard restarted during the drill — no verdict stored",
                  finished: now - 30, restore: { ok: true, model: "org/stock", skipped: "down", after_restart: true } } };
  const e3 = boot(() => fresh);
  const said = await e3.win.promotionGate.restartNotice();
  check("announced", /restarted/.test(said) && e3.toasts.length >= 1, e3.toasts);
  const stale = JSON.parse(JSON.stringify(fresh)); stale.last.finished = now - 3600;
  const e4 = boot(() => stale);
  check("an old one is not", (await e4.win.promotionGate.restartNotice()) === "");
  const normal = { running: null, last: { model: "org/cand", error: "x", finished: now,
                   restore: { ok: true, model: "org/stock" } } };
  const e5 = boot(() => normal);
  check("an ordinary drill's result is not re-announced on load",
        (await e5.win.promotionGate.restartNotice()) === "");

  console.log("\n4. the page is polling when the dashboard restarts mid-drill");
  // review R4: the new process answers running:null with NO last until its
  // boot recovery (3 s + a switch back + a load wait) stores one; the poll
  // used to stop at that first answer and say nothing at all.
  let s4 = { running: "org/cand" };
  let down = false;
  const e6 = boot(() => { if (down) throw new Error("connection refused"); return s4; });
  e6.win.promotionGate.poll();
  await e6.intervals[0]();
  down = true;
  await e6.intervals[0]();
  down = false;
  s4 = { running: null, last: null };
  await e6.intervals[0]();
  check("no last yet: nothing said, and the poll keeps going", e6.toasts.length === 0, e6.toasts);
  s4 = { running: null, last: { model: "org/old", error: "x", finished: Date.now() / 1000 - 86400,
                                 restore: { ok: false, model: "org/stock" } } };
  await e6.intervals[0]();
  check("a result from an EARLIER drill is not taken for this one", e6.toasts.length === 0, e6.toasts);
  s4 = { running: null, last: { model: "org/cand", error: "the dashboard restarted during the drill — no verdict stored",
                                finished: Date.now() / 1000,
                                restore: { ok: false, model: "org/stock", error: "launchctl refused", after_restart: true } } };
  await e6.intervals[0]();
  check("the restore the new process made is then told to the owner",
        e6.toasts.length === 1 && /Could not switch back to stock/.test(e6.toasts[0]), e6.toasts);
  const t0 = Date.now();
  const realNow = Date.now;
  let s7 = { running: "org/cand" };
  const e7 = boot(() => s7);
  e7.win.promotionGate.poll();
  await e7.intervals[0]();
  s7 = { running: null, last: null };
  Date.now = () => t0 + 16 * 60 * 1000;   // no result ever comes back
  try { await e7.intervals[0](); } finally { Date.now = realNow; }
  check("no result at all after 15 min: the owner is told no verdict was stored",
        e7.toasts.length === 1 && /no verdict/.test(e7.toasts[0]), e7.toasts);

  console.log("\nTESTS " + passed + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})();
