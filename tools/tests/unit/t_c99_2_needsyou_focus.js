// C99.2 (C99 partial, k14b): the Needs-you list is rebuilt with innerHTML
// after every Done, Snooze, Open failure or toggle, so the button a keyboard
// user had just pressed was destroyed and focus fell to <body> — they had to
// Tab from the top of the page again after each action.
//
// AFTER: the control that had focus is remembered (row id + action +
// data-to/data-u) and, after the redraw, focus goes back to the same control
// — or, when the row has gone, to the first button of the row that took its
// place, else the row above.  A mouse click (WebKit never focuses a button
// on click) and a user who has moved focus elsewhere are left alone.
//
// Renders the REAL dashboard/aux_needsyou.js in a vm sandbox with a tiny fake
// DOM (buttons with focus(), document.activeElement, detach-on-innerHTML).
// Offline.  Against the pre-fix file every "focus lands on …" check sees
// <body>.
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

// ---- a fake DOM just deep enough for wire() + the focus helpers ----------
const document = { body: { tag: "BODY" }, activeElement: null,
                   visibilityState: "visible", addEventListener: function () {} };
document.activeElement = document.body;
const OUTSIDE = { tag: "INPUT", getAttribute: function () { return null; } };

function host() {
  const h = { textContent: "", _html: "", _btns: [] };
  function parse() {
    const out = [];
    const re = /<button\b([^>]*)>/g;
    let m;
    while ((m = re.exec(h._html))) {
      const attrs = m[1];
      const a = (attrs.match(/data-a="([^"]*)"/) || [])[1];
      if (!a) continue;
      const before = h._html.slice(0, m.index);
      // every row control sits inside the last .ny-row opened before it; the
      // two toggles (.ny-more, and the pop-out's foot) sit in no row
      const rows = before.match(/class="ny-row" data-id="[^"]*"/g) || [];
      const lastRow = rows.length
        ? unesc(rows[rows.length - 1].replace(/^.*data-id="/, "").slice(0, -1)) : "";
      const id = (a === "toggle-today" || a === "toggle-why") ? "" : lastRow;
      const b = {
        tag: "BUTTON", disabled: false, onclick: null,
        getAttribute: function (k) {
          if (k === "data-a") return a;
          const mm = attrs.match(new RegExp(k + '="([^"]*)"'));
          return mm ? unesc(mm[1]) : null;
        },
        closest: function () {
          return id ? { getAttribute: function () { return id; } } : null;
        },
        focus: function () { if (!b.disabled) document.activeElement = b; },
        _id: id, _a: a,
      };
      out.push(b);
    }
    return out;
  }
  Object.defineProperty(h, "innerHTML", {
    get: function () { return h._html; },
    set: function (v) {
      const had = h._btns.indexOf(document.activeElement) >= 0;
      h._html = String(v);
      h._btns = parse();
      if (had) document.activeElement = document.body;   // the old node is gone
    },
  });
  h.querySelectorAll = function (sel) { return sel === "button[data-a]" ? h._btns : []; };
  h.querySelector = function () { return null; };
  h.contains = function (x) { return h._btns.indexOf(x) >= 0; };
  h.getClientRects = function () { return []; };
  return h;
}

let actAnswer = { ok: true };
let nextPayload = null;
const sandbox = {
  console, esc, URL, document,
  relTime: function () { return "2m ago"; },
  RENDER: {}, EXPAND_RENDER: {}, WICONS: {},
  setTimeout: function () { return 0; },
  openPop: function () {},
  fetch: function (url) {
    const body = url === "/api/needsyou/act" ? actAnswer : nextPayload;
    return Promise.resolve({ ok: true, json: function () { return Promise.resolve(body); } });
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, "utf8"), sandbox, { filename: FILE });

function rowOf(id, title) {
  return { id: id, source: "message", sender: title, title: title,
           summary: "can you call me?", bucket: "now", received_at: 1,
           reason: "r" };
}
function pay(ids) {
  return { ok: true, never_count: 0, metrics: {}, next_brief: null,
           generated_at: Date.now() / 1000, today: [], later: [],
           now: ids.map(function (i) { return rowOf("message:" + i, "P" + i); }) };
}
function btn(h, a, id, attr, val) {
  return h._btns.find(function (b) {
    return b._a === a && b._id === id && (!attr || b.getAttribute(attr) === val);
  }) || null;
}
function key(b, h) {
  if (b === document.body) return "<body>";
  if (b === OUTSIDE) return "<outside>";
  return b && b._a ? (b._a + "@" + (b._id || "-") +
                      (h && h._btns.indexOf(b) < 0 ? " (detached)" : "")) : String(b);
}
async function flush() {
  for (let i = 0; i < 6; i++) await new Promise(function (r) { setImmediate(r); });
}
// a keyboard press: the button is focused, the click event has detail 0
async function press(h, b) {
  b.focus();
  b.onclick({ preventDefault: function () {}, stopPropagation: function () {}, detail: 0 });
  await flush();
}

(async function () {
  console.log("=== C99.2: keyboard focus survives the rebuild ===");
  let h = host();
  sandbox.EXPAND_RENDER.needsyou(h, pay([1, 2, 3]));
  actAnswer = { ok: true };
  nextPayload = pay([2, 3]);                       // row 1 was filed
  await press(h, btn(h, "done", "message:1"));
  let ae = document.activeElement;
  check("Done on row 1 -> focus lands on row 2's first button",
        ae && ae._a === "done" && ae._id === "message:2" && h.contains(ae), key(ae, h));

  nextPayload = pay([2]);                          // the last row was filed
  await press(h, btn(h, "done", "message:3"));
  ae = document.activeElement;
  check("Done on the last row -> focus lands on the row above",
        ae && ae._id === "message:2" && h.contains(ae), key(ae, h));

  h = host();
  sandbox.EXPAND_RENDER.needsyou(h, pay([1, 2, 3]));
  await press(h, btn(h, "snz", "message:2"));
  ae = document.activeElement;
  check("Snooze (opens the choices) -> focus stays on that Snooze",
        ae && ae._a === "snz" && ae._id === "message:2" && h.contains(ae), key(ae, h));
  nextPayload = pay([1, 3]);
  await press(h, btn(h, "snooze", "message:2", "data-u", "1h"));
  ae = document.activeElement;
  check("a snooze choice -> focus lands on the row that took its place",
        ae && ae._id === "message:3" && h.contains(ae), key(ae, h));

  h = host();
  sandbox.EXPAND_RENDER.needsyou(h, pay([1, 2]));
  actAnswer = { ok: false, error: "store write failed" };
  await press(h, btn(h, "done", "message:1"));
  ae = document.activeElement;
  check("a failed Done -> focus back on that same Done (now re-enabled)",
        ae && ae._a === "done" && ae._id === "message:1" && h.contains(ae), key(ae, h));
  check("...with the error shown", h.innerHTML.indexOf("store write failed") >= 0);

  h = host();
  sandbox.EXPAND_RENDER.needsyou(h, pay([1, 2]));
  await press(h, btn(h, "toggle-why", ""));
  ae = document.activeElement;
  check("Why? toggle -> focus stays on the toggle",
        ae && ae._a === "toggle-why" && h.contains(ae), key(ae, h));

  h = host();
  sandbox.RENDER.needsyou(h, Object.assign(pay([1]), {
    today: [rowOf("message:7", "P7")] }), host());
  await press(h, btn(h, "toggle-today", ""));
  ae = document.activeElement;
  check("Today (n) toggle in the widget -> focus stays on it",
        ae && ae._a === "toggle-today" && h.contains(ae), key(ae, h));

  console.log("=== C99.2: what it must leave alone ===");
  h = host();
  sandbox.EXPAND_RENDER.needsyou(h, pay([1, 2, 3]));
  actAnswer = { ok: true };
  nextPayload = pay([2, 3]);
  document.activeElement = document.body;          // WebKit: a click focuses nothing
  btn(h, "done", "message:1").onclick({ preventDefault: function () {},
                                        stopPropagation: function () {}, detail: 1 });
  await flush();
  check("a mouse click (no focus) moves no focus", document.activeElement === document.body,
        key(document.activeElement, h));

  h = host();
  sandbox.EXPAND_RENDER.needsyou(h, pay([1, 2, 3]));
  const b1 = btn(h, "done", "message:1");
  b1.focus();
  b1.onclick({ preventDefault: function () {}, stopPropagation: function () {}, detail: 0 });
  document.activeElement = OUTSIDE;                // user tabbed to the chat box
  await flush();
  check("focus moved elsewhere meanwhile -> not stolen back",
        document.activeElement === OUTSIDE, key(document.activeElement, h));

  h = host();
  sandbox.EXPAND_RENDER.needsyou(h, pay([1, 2, 3]));
  btn(h, "open", "message:3").focus();
  sandbox.EXPAND_RENDER.needsyou(h, pay([1, 2, 3]));   // a status / poll re-render
  ae = document.activeElement;
  check("a re-render while a row control is focused keeps it",
        ae && ae._a === "open" && ae._id === "message:3" && h.contains(ae), key(ae, h));

  console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})();
