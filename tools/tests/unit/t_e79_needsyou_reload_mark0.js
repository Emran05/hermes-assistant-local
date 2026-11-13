// E79 + E04 (docs/audits/2026-11-09-codebase-audit.md), the widget half.
//
// E79: after a Done / Snooze / re-file in the HUB WIDGET, reload() fetched a
// plain GET /api/needsyou, which records EVERY row of the payload as shown —
// collapsed Today and Later included — bypassing the visible-only beacon
// (C06).  Now the widget reloads with ?mark=0 and reports what it drew through
// POST /api/needsyou/seen.  The pop-out keeps /api/expand (it draws every row
// that call marks).
//
// E04: the pop-out lists the Never rows (collapsed until opened), each with a
// "Why?" reason and buttons that move it back to Today / Later; the widget
// never draws them; the Never rows are reported as seen only once the owner
// opened that group.
//
// Renders the REAL dashboard/aux_needsyou.js in a vm sandbox (no DOM).
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

function unesc(s) {
  return s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

// a host element whose buttons can be clicked through wire()'s onclick
function host(laidOut) {
  const h = { innerHTML: "", textContent: "", __btns: [], style: {} };
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
      const rows = before.match(/class="ny-row" data-id="([^"]*)"/g) || [];
      const id = rows.length ? unesc(rows[rows.length - 1].match(/data-id="([^"]*)"/)[1]) : "";
      out.push({
        style: {},
        getAttribute: function (k) {
          if (k === "data-a") return a;
          const mm = attrs.match(new RegExp(k + '="([^"]*)"'));
          return mm ? unesc(mm[1]) : null;
        },
        closest: function () {
          return id ? { getAttribute: function () { return id; }, style: {} } : null;
        }
      });
    }
    h.__btns = out;
    return out;
  };
  h.querySelector = function () { return null; };
  h.contains = function () { return false; };
  h.getClientRects = function () { return laidOut ? [{}] : []; };
  return h;
}

const now = Date.now() / 1000;
function payload() {
  return {
    ok: true, metrics: {}, next_brief: null, generated_at: now, sources: {},
    now: [{ id: "approval:j1", source: "approval", title: "A", bucket: "now",
            reason: "r" }],
    today: [{ id: "message:+1", source: "message", sender: "B", bucket: "today",
              summary: "can you?", received_at: now - 60, reason: "r" }],
    later: [{ id: "watchtower:c", source: "watchtower", title: "C",
              bucket: "later", reason: "r" }],
    never: [{ id: "message:262966", source: "message", sender: "262966",
              bucket: "never", summary: "Your code is 1", received_at: now - 30,
              reason: "automated sender, no deadline — digest only" },
            { id: "message:noreply@x", source: "message", sender: "noreply@x",
              bucket: "never", summary: "shipped", received_at: now - 90,
              reason: "automated sender, no deadline — digest only" }],
    never_count: 7, today_count: 1, later_count: 1,
  };
}

const calls = [];
let answer = payload();
const sandbox = {
  console, relTime: function () { return "2m ago"; },
  esc: require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc"),
  RENDER: {}, EXPAND_RENDER: {}, WICONS: {},
  setTimeout: function (f) { f(); return 0; },
  Promise: Promise,
  fetch: function (url, opt) {
    calls.push({ url: url, body: opt && opt.body ? JSON.parse(opt.body) : null });
    let body = { ok: true };
    if (url.indexOf("/api/needsyou/act") === 0) body = { ok: true };
    else if (url.indexOf("/api/needsyou") === 0 || url.indexOf("/api/expand") === 0) body = answer;
    return Promise.resolve({ ok: true, json: function () { return body; } });
  },
};
sandbox.window = sandbox;
sandbox.document = {
  visibilityState: "visible",
  getElementById: function () { return null; },
  createElement: function () { return {}; },
  head: { appendChild: function () {} },
  addEventListener: function () {},
  body: {},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });

function click(h, action, id, attr, val) {
  const b = h.__btns.find(function (x) {
    return x.getAttribute("data-a") === action &&
      (!id || (x.closest() && x.closest().getAttribute("data-id") === id)) &&
      (!attr || x.getAttribute(attr) === val);
  });
  if (!b) return null;
  b.onclick({ preventDefault: function () {}, stopPropagation: function () {} });
  return b;
}
function flush() { return new Promise(function (r) { setImmediate(r); }); }
async function settle() { for (let i = 0; i < 12; i++) await flush(); }

(async function () {
  console.log("=== E04: the widget shows only the Never count ===");
  const w = host(true);
  sandbox.RENDER.needsyou(w, payload(), host(true));
  check("widget draws no never row", w.innerHTML.indexOf("message:262966") < 0);
  check("widget has no Never toggle", w.innerHTML.indexOf("toggle-never") < 0);
  check("widget footer still says Never 7", w.innerHTML.indexOf("Never 7") >= 0);
  await settle();
  const firstSeen = calls.filter(function (c) { return c.url === "/api/needsyou/seen"; });
  check("the widget beacon never carries a never id",
        firstSeen.length === 1 && firstSeen[0].body.ids.indexOf("message:262966") < 0,
        JSON.stringify(firstSeen));

  console.log("=== E79: a Done in the widget reloads with ?mark=0 + the beacon ===");
  calls.length = 0;
  answer = payload();
  answer.today = [];                       // the row left; a new list arrives
  check("Done clicked", !!click(w, "done", "approval:j1"));
  await settle();
  const reads = calls.filter(function (c) {
    return c.url.indexOf("/api/needsyou") === 0 && c.url.indexOf("/act") < 0 &&
      c.url.indexOf("/seen") < 0;
  });
  check("the reload is a read-only ?mark=0",
        reads.length === 1 && reads[0].url === "/api/needsyou?mark=0",
        JSON.stringify(reads));
  const beacon = calls.filter(function (c) { return c.url === "/api/needsyou/seen"; });
  check("...and the beacon reports the redrawn rows",
        beacon.length === 1 &&
        JSON.stringify(beacon[0].body.ids.slice().sort()) ===
        JSON.stringify(["approval:j1", "watchtower:c"]),
        JSON.stringify(beacon));

  console.log("=== E04: the pop-out lists Never rows once opened ===");
  calls.length = 0;
  answer = payload();
  const p = host(true);
  sandbox.EXPAND_RENDER.needsyou(p, payload());
  check("pop-out has a collapsed Never (7) toggle",
        /data-a="toggle-never"[^>]*aria-expanded="false"[^>]*>[\s\S]*?Never \(7\)/.test(p.innerHTML),
        p.innerHTML.slice(0, 400));
  check("...rows hidden until opened", p.innerHTML.indexOf('data-id="message:262966"') < 0);
  await settle();
  check("nothing beaconed while collapsed",
        !calls.some(function (c) { return c.url === "/api/needsyou/seen"; }));
  check("open it", !!click(p, "toggle-never"));
  check("never rows drawn", p.innerHTML.indexOf('data-id="message:262966"') >= 0 &&
        p.innerHTML.indexOf('data-id="message:noreply@x"') >= 0);
  check("each carries its reason",
        p.innerHTML.indexOf("automated sender, no deadline") >= 0);
  check("says it shows the newest 2 of 7",
        p.innerHTML.indexOf("Showing the newest 2 of 7") >= 0);
  check("a never row offers Move to today and Move to later",
        !!p.__btns.find(function (b) {
          return b.getAttribute("data-a") === "rc" && b.getAttribute("data-to") === "today" &&
            b.closest() && b.closest().getAttribute("data-id") === "message:262966";
        }) && !!p.__btns.find(function (b) {
          return b.getAttribute("data-a") === "rc" && b.getAttribute("data-to") === "later" &&
            b.closest() && b.closest().getAttribute("data-id") === "message:262966";
        }));
  check("...and no 'Never' button of its own",
        !p.__btns.find(function (b) {
          return b.getAttribute("data-to") === "never" && b.closest() &&
            b.closest().getAttribute("data-id") === "message:262966";
        }));
  await settle();
  const nb = calls.filter(function (c) { return c.url === "/api/needsyou/seen"; });
  check("opening it beacons the drawn never ids",
        nb.length === 1 && JSON.stringify(nb[0].body.ids) ===
        JSON.stringify(["message:262966", "message:noreply@x"]), JSON.stringify(nb));
  calls.length = 0;
  check("re-file one to today", !!click(p, "rc", "message:262966", "data-to", "today"));
  await settle();
  const act = calls.find(function (c) { return c.url === "/api/needsyou/act"; });
  check("the act posts reclassify to today with the shown received_at",
        act && act.body.action === "reclassify" && act.body.to === "today" &&
        act.body.seen === now - 30, JSON.stringify(act));
  check("the pop-out reloads through /api/expand",
        calls.some(function (c) { return c.url === "/api/expand?id=needsyou"; }));

  console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})();
