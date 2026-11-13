// B04 (docs/audits/2026-10-08-codebase-audit.md), review follow-up: a Done /
// Snooze / re-file on a conversation must cover the message the row SHOWS,
// not a follow-up that reached the server after this render.  The server
// takes the row's received_at from its cache; the widget sends the one it
// displayed as `seen`, because the cache can be newer than the screen.
//
// Renders the REAL dashboard/aux_needsyou.js in a vm sandbox, clicks the row
// actions through wire()'s real onclick handlers and checks the POST bodies.
// Offline, no DOM.
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

// a host element whose buttons can be clicked through wire()'s onclick
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
      const before = h.innerHTML.slice(0, m.index);
      const rows = before.match(/data-id="([^"]*)"/g) || [];
      const id = rows.length ? unesc(rows[rows.length - 1].slice(9, -1)) : "";
      out.push({
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
    h.__btns = out;                   // the objects wire() put onclick on
    return out;
  };
  h.getClientRects = function () { return []; };
  return h;
}

const posts = [];
const sandbox = {
  console, esc, relTime: function () { return "2m ago"; },
  RENDER: {}, EXPAND_RENDER: {}, WICONS: {},
  setTimeout: function () { return 0; },
  fetch: function (url, opt) {
    posts.push({ url: url, body: opt && opt.body ? JSON.parse(opt.body) : null });
    return new Promise(function () {});          // never settles: no reload
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });

const now = Date.now() / 1000;
const SHOWN = now - 120;
const payload = {
  ok: true, never_count: 0, metrics: {}, next_brief: null, generated_at: now,
  now: [
    { id: "message:+15550000081", source: "message", sender: "Dana Ruiz",
      title: "Dana Ruiz", summary: "can you send it by 5pm?", bucket: "now",
      received_at: SHOWN, deadline_ts: now + 3600, reason: "r" },
    { id: "calendar:evt|y", source: "calendar", sender: "Calendar",
      title: "Design review", bucket: "now", deadline_ts: now + 1800,
      reason: "r" },
  ],
  today: [], later: [],
};

function click(h, action, id, attr, val) {
  const b = h.__btns.find(function (x) {
    return x.getAttribute("data-a") === action &&
      x.closest() && x.closest().getAttribute("data-id") === id &&
      (!attr || x.getAttribute(attr) === val);
  });
  if (!b) return null;
  b.onclick({ preventDefault: function () {}, stopPropagation: function () {} });
  return b;
}

function lastAct() {
  const acts = posts.filter(function (p) { return p.url === "/api/needsyou/act"; });
  return acts.length ? acts[acts.length - 1].body : null;
}

console.log("=== B04: a row action says which message it covers ===");
const DANA = "message:+15550000081";
let w = host();
sandbox.RENDER.needsyou(w, payload, host());
check("Done clicked", !!click(w, "done", DANA));
let body = lastAct();
check("Done posts the received_at the row shows",
      body && body.action === "done" && body.seen === SHOWN, JSON.stringify(body));

w = host();
sandbox.RENDER.needsyou(w, payload, host());
click(w, "snz", DANA);                           // opens the snooze choices
check("Snooze choice clicked", !!click(w, "snooze", DANA, "data-u", "1h"));
body = lastAct();
check("Snooze posts it too",
      body && body.action === "snooze" && body.seen === SHOWN, JSON.stringify(body));

const p = host();
sandbox.EXPAND_RENDER.needsyou(p, payload);
check("Never clicked in the pop-out", !!click(p, "rc", DANA, "data-to", "never"));
body = lastAct();
check("a re-file posts it too",
      body && body.action === "reclassify" && body.to === "never" &&
      body.seen === SHOWN,
      JSON.stringify(body));

w = host();
sandbox.RENDER.needsyou(w, payload, host());
click(w, "done", "calendar:evt|y");
body = lastAct();
check("a row without a received_at sends no seen",
      body && body.id === "calendar:evt|y" && !("seen" in body), JSON.stringify(body));

console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);
