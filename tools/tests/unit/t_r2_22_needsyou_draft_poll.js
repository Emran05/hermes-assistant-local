// R2-22 (2026-10-08 P3 prose "draft reply", k02 review #7): the Needs-you
// widget's pollDraft.
//
// BEFORE:
//   * it ignored d.err, so a failed draft job's ERROR text ("The agent hit an
//     error before it could reply.") was rendered as the drafted reply;
//   * an approval inside a draft job (state "approval") showed only a status
//     line — the command was never shown and nothing could answer it, while
//     the job held the one-draft lock (every other row's Draft got 409);
//   * the ~2-minute give-up counted approval waits too.
//
// AFTER: a done+err job shows "No draft — <error>" (no draft box); an
// approval shows the command it asked to run and a Deny button that posts
// /api/chat/approve {job, choice:"deny", approval_id}; a denied request is
// not shown again on the next poll; approval waits do not spend the draft's
// own time budget.  (hermes_rpc now denies a draft's requests itself — see
// t_x_af5_draft_never_acts.py — so this card is the fallback path.)
//
// Renders the REAL dashboard/aux_needsyou.js in a vm sandbox, clicks through
// wire()'s real onclick handlers, drives the poll with a manual timer queue.
// Offline, no DOM.  Against the pre-fix file the error is shown as a draft
// and no Deny button exists.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_needsyou.js");
const fails = [];
let checks = 0;
function check(name, cond, extra) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : "  " + (extra || "")));
  if (!cond) fails.push(name);
}
const esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
function unesc(s) {
  return s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}
function host() {
  const h = { innerHTML: "", textContent: "", __btns: [] };
  h.querySelectorAll = function (sel) {
    if (sel !== "button[data-a]") return [];
    const out = [];
    const re = /<button\b([^>]*)>/g;
    let m;
    while ((m = re.exec(h.innerHTML))) {
      const attrs = m[1];
      const a = (attrs.match(/data-a="([^"]*)"/) || [])[1];
      if (!a) continue;
      const rows = h.innerHTML.slice(0, m.index).match(/data-id="([^"]*)"/g) || [];
      const id = rows.length ? unesc(rows[rows.length - 1].slice(9, -1)) : "";
      out.push({
        disabled: false,
        getAttribute: function (k) {
          if (k === "data-a") return a;
          const mm = attrs.match(new RegExp(k + '="([^"]*)"'));
          return mm ? unesc(mm[1]) : null;
        },
        closest: function () {
          return id ? { getAttribute: function () { return id; } } : null;
        }
      });
    }
    h.__btns = out;
    return out;
  };
  h.querySelector = function () { return null; };
  h.getClientRects = function () { return []; };
  return h;
}

const timers = [];
const posts = [];
let polls = [];          // scripted /api/chat/poll answers, in order
let approveAnswer = { ok: true };
const sandbox = {
  console, esc, relTime: function () { return "2m ago"; },
  RENDER: {}, EXPAND_RENDER: {}, WICONS: {},
  setTimeout: function (fn) { timers.push(fn); return timers.length; },
  fetch: function (url, opt) {
    const body = opt && opt.body ? JSON.parse(opt.body) : null;
    posts.push({ url: url, body: body });
    let ans = {};
    if (url === "/api/needsyou/act") ans = { ok: true, action: "draft", job: CUR_JOB };
    else if (url.indexOf("/api/chat/poll") === 0) ans = polls.length ? polls.shift() : { ok: true, state: "running", done: false };
    else if (url === "/api/chat/approve") ans = approveAnswer;
    else return new Promise(function () {});
    return Promise.resolve({ json: function () { return Promise.resolve(ans); } });
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });

let CUR_JOB = "";
const ID = "message:+15550002201";
function payload() {
  return { ok: true, never_count: 0, metrics: {}, next_brief: null, generated_at: 1,
           today: [], later: [],
           now: [{ id: ID, source: "message", sender: "Ana", title: "Ana",
                   summary: "can you call me?", bucket: "now", received_at: 1,
                   reason: "r" }] };
}
async function flush() {
  for (let i = 0; i < 6; i++) await new Promise(function (r) { setImmediate(r); });
}
async function tick(n) {               // run the next n scheduled poll ticks
  for (let i = 0; i < (n || 1); i++) {
    const fn = timers.shift();
    if (!fn) return;
    fn();
    await flush();
  }
}
function click(h, a) {
  const b = h.__btns.find(function (x) {
    return x.getAttribute("data-a") === a && x.closest() &&
      x.closest().getAttribute("data-id") === ID;
  });
  if (!b) return false;
  b.onclick({ preventDefault: function () {}, stopPropagation: function () {} });
  return true;
}
async function startDraft(job) {
  CUR_JOB = job;
  timers.length = 0;
  const ui = sandbox.window.__needsyou.UI;
  delete ui.draft[ID]; delete ui.msg[ID]; if (ui.appr) delete ui.appr[ID];
  const h = host();
  sandbox.EXPAND_RENDER.needsyou(h, payload());
  click(h, "draft");
  await flush();
  return h;
}

(async function () {
  console.log("=== R2-22: a failed draft job is an error, not a draft ===");
  let h = await startDraft("J1");
  polls = [{ ok: true, state: "done", done: true, err: true,
             reply: "The agent hit an error before it could reply." }];
  await tick();
  check("the error is shown as an error",
        h.innerHTML.indexOf("No draft — The agent hit an error before it could reply.") >= 0 &&
        /class="ny-msg bad"/.test(h.innerHTML), h.innerHTML.slice(-500));
  check("...and NOT as a drafted reply", h.innerHTML.indexOf("ny-draft") < 0,
        h.innerHTML.slice(-500));

  console.log("=== R2-22: an approval inside a draft is shown and can be denied ===");
  h = await startDraft("J2");
  polls = [{ ok: true, state: "approval", done: false, status: "",
             approval: { command: "kill -9 1234 <b>", approval_id: "aaa111" } }];
  await tick();
  check("the command it asked to run is shown (escaped)",
        h.innerHTML.indexOf("The draft asked to run: kill -9 1234 &lt;b&gt;") >= 0,
        h.innerHTML.slice(-600));
  check("a Deny button is offered", click(h, "draft-deny"), h.innerHTML.slice(-600));
  await flush();
  const ap = posts.filter(function (p) { return p.url === "/api/chat/approve"; }).pop();
  check("Deny posts /api/chat/approve with the job and THIS card's id",
        ap && ap.body.job === "J2" && ap.body.choice === "deny" &&
        ap.body.approval_id === "aaa111", JSON.stringify(ap));
  check("...and never an approve", posts.every(function (p) {
    return !(p.body && p.body.choice === "approve"); }));
  check("'Denied' is shown", h.innerHTML.indexOf("Denied. Finishing the draft") >= 0,
        h.innerHTML.slice(-500));
  polls = [{ ok: true, state: "approval", done: false,
             approval: { command: "kill -9 1234 <b>", approval_id: "aaa111" } }];
  await tick();
  check("the same, already-denied request is not shown again",
        h.innerHTML.indexOf("The draft asked to run") < 0, h.innerHTML.slice(-500));
  polls = [{ ok: true, state: "approval", done: false,
             approval: { command: "rm -rf ~/x", approval_id: "bbb222" } }];
  await tick();
  check("a different request is shown", h.innerHTML.indexOf("The draft asked to run: rm -rf ~/x") >= 0,
        h.innerHTML.slice(-500));
  polls = [{ ok: true, state: "done", done: true, err: false, reply: "Sure, 5pm works." }];
  await tick();
  check("the finished draft replaces the card",
        h.innerHTML.indexOf("Sure, 5pm works.") >= 0 &&
        h.innerHTML.indexOf("The draft asked to run") < 0, h.innerHTML.slice(-500));

  console.log("=== R2-22: approval waits do not spend the draft's time ===");
  h = await startDraft("J3");
  polls = [];
  for (let i = 0; i < 120; i++) {
    polls.push({ ok: true, state: "approval", done: false,
                 approval: { command: "x", approval_id: "ccc333" } });
  }
  polls.push({ ok: true, state: "done", done: true, err: false, reply: "Draft text." });
  await tick(125);
  check("120 approval polls, then a draft -> the draft, not 'taking too long'",
        h.innerHTML.indexOf("Draft text.") >= 0 &&
        h.innerHTML.indexOf("taking too long") < 0, h.innerHTML.slice(-400));

  console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})();
