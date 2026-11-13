// C99.1 (round 2 of the 2026-10-21 audit): the Flight Recorder's 3 s poll no
// longer throws keyboard focus off an Undo button.
//
// recRender() rebuilt #rec-list with innerHTML on every poll, so a keyboard
// user who had tabbed to a row's Undo lost focus to <body> every three
// seconds. Now an unchanged list is left alone while focus is inside it, and
// a list that did change is rebuilt and focus goes back to the same Undo (or
// to its row, once the Undo is gone).
//
// Drives the REAL aux_recorder.js (recRender) in node over a small fake DOM
// that, like a browser, drops focus to <body> when the focused element is
// removed by an innerHTML rebuild. Fails on the old code: after a poll the
// active element is <body>.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             (process.argv.indexOf("--repo") > 0 ? path.resolve(process.argv[process.argv.indexOf("--repo") + 1])
                                                 : path.resolve(__dirname, "..", "..", ".."));
const FILE = path.join(REPO, "dashboard", "aux_recorder.js");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

const body = { tag: "body" };
const doc = { activeElement: body, focusCalls: [] };

function el(tag, cls, attrs, parent) {
  const e = {
    tag, className: cls || "", attrs: Object.assign({}, attrs || {}), parentNode: parent || null,
    kids: [], textContent: "", listeners: {},
    classList: { contains(c) { return (" " + e.className + " ").indexOf(" " + c + " ") >= 0; } },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); },
    addEventListener(t, f) { this.listeners[t] = f; },
    focus(opts) { doc.activeElement = this; doc.focusCalls.push({ el: this, opts }); },
    closest(sel) {
      for (let n = this; n; n = n.parentNode) if (sel === ".rec-row" && /\brec-row\b/.test(n.className || "")) return n;
      return null;
    },
    get nextSibling() {
      const k = this.parentNode ? this.parentNode.kids : [];
      return k[k.indexOf(this) + 1] || null;
    },
  };
  return e;
}

let rebuilds = 0;
const list = el("div", "", { id: "rec-list" });
function all(n, out) { for (const k of n.kids) { out.push(k); all(k, out); } return out; }
Object.defineProperty(list, "innerHTML", {
  set(h) {
    rebuilds++;
    const had = all(this, []);
    if (had.indexOf(doc.activeElement) >= 0) doc.activeElement = body;   // browser behaviour
    this.kids = [];
    const re = /<div class="(rec-row[^"]*)" data-id="([^"]+)"[^>]*>([\s\S]*?)(?=<div class="rec-row|$)/g;
    let m;
    while ((m = re.exec(h))) {
      const r = el("div", m[1], { "data-id": m[2] }, this);
      const b = /<button class="rec-undo" data-undo="([^"]+)">/.exec(m[3]);
      if (b) r.kids.push(el("button", "rec-undo", { "data-undo": b[1] }, r));
      this.kids.push(r);
    }
  },
  get() { return ""; },
});
list.contains = function (n) { return all(this, []).indexOf(n) >= 0; };
list.querySelector = function (sel) {
  let m;
  if ((m = /^\.rec-row\[data-id="([^"]+)"\]$/.exec(sel)))
    return this.kids.find(k => k.getAttribute("data-id") === m[1]) || null;
  if ((m = /^\[data-undo="([^"]+)"\]$/.exec(sel)))
    return all(this, []).find(k => k.getAttribute("data-undo") === m[1]) || null;
  if (sel === ".rec-livehint") return this.kids.find(k => /rec-livehint/.test(k.className)) || null;
  return null;
};
list.querySelectorAll = function (sel) {
  if (sel === ".rec-row") return this.kids.filter(k => /\brec-row\b/.test(k.className));
  if (sel === "[data-undo]") return all(this, []).filter(k => k.hasAttribute("data-undo"));
  return [];
};
list.insertBefore = function (n, ref) {
  n.parentNode = this;
  const i = ref ? this.kids.indexOf(ref) : -1;
  if (i < 0) this.kids.push(n); else this.kids.splice(i, 0, n);
};
list.removeChild = function (n) { this.kids = this.kids.filter(k => k !== n); };
list.appendChild = function (n) { this.insertBefore(n, null); };

global.document = {
  getElementById(id) { return id === "rec-list" ? list : null; },
  createElement() { return el("div", ""); },
  get activeElement() { return doc.activeElement; },
};
global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
global.relTime = () => "just now";
global.REDUCE = true;
global.confirm = () => true;
global.fetch = async () => ({ json: async () => ({}) });
(0, eval)(fs.readFileSync(FILE, "utf8"));

const row = (id, extra) => Object.assign({ id, kind: "write", reversible: "yes", status: "done",
  tool: "write_file", target: "/p/" + id + ".txt", source: "hub", ts: 1.8e9 }, extra || {});
const undoBtn = id => list.querySelector('[data-undo="' + id + '"]');

recState.last = { actions: [row(5), row(6)], counts: {} };
recRender();
console.log("\n1. an unchanged poll keeps focus where it is");
undoBtn("5").focus();
const before = undoBtn("5"), n0 = rebuilds;
recRender();                                       // the 3 s poll, same data
check("focus is still on row 5's Undo", doc.activeElement === before &&
      doc.activeElement.getAttribute("data-undo") === "5",
      doc.activeElement.tag + " " + (doc.activeElement.getAttribute ? doc.activeElement.getAttribute("data-undo") : ""));
check("…and the list was not rebuilt under it", rebuilds === n0, rebuilds - n0);

console.log("\n2. a poll that changed the list puts focus back on the same Undo");
recState.last = { actions: [row(7), row(5), row(6)], counts: {} };
doc.focusCalls = [];
recRender();
check("the list was rebuilt (a new row arrived)", list.kids.length === 3, list.kids.length);
check("focus is on the NEW row 5 Undo, not <body>",
      doc.activeElement !== body && doc.activeElement !== before &&
      doc.activeElement.getAttribute("data-undo") === "5", doc.activeElement.tag);
check("…without scrolling the rail", doc.focusCalls.length === 1 &&
      doc.focusCalls[0].opts && doc.focusCalls[0].opts.preventScroll === true, doc.focusCalls.length);

console.log("\n3. once the Undo is gone, focus stays on its row");
recState.last = { actions: [row(7), row(5, { status: "undone" }), row(6)], counts: {} };
recRender();
const r5 = list.querySelector('.rec-row[data-id="5"]');
check("row 5 has no Undo any more", !undoBtn("5"));
check("focus is on row 5 itself", doc.activeElement === r5, doc.activeElement.tag);
check("…made focusable without joining the tab order", r5 && r5.getAttribute("tabindex") === "-1",
      r5 && r5.attrs);

console.log("\n4. focus outside the list is never pulled into it");
const outside = el("button", "elsewhere");
outside.focus();
recState.last = { actions: [row(8), row(7), row(6)], counts: {} };
recRender();
check("the list rebuilt and focus stayed outside", list.kids.length === 3 && doc.activeElement === outside,
      doc.activeElement.className);

console.log("\n5. a stale 'unreachable' hint goes even when nothing is rebuilt");
undoBtn("6").focus();
const hint = el("div", "rec-hint rec-livehint");
list.appendChild(hint);
const n1 = rebuilds;
recRender();
check("no rebuild, focus kept", rebuilds === n1 && doc.activeElement.getAttribute("data-undo") === "6");
check("…and the hint is gone", !list.querySelector(".rec-livehint"));

console.log("\n6. a pending row error still forces the rebuild that shows it");
recRowError("6", "undo failed — recorder unreachable");
const n2 = rebuilds;
recRender();
check("rebuilt, the error is under its row, focus back on that Undo",
      rebuilds === n2 + 1 && list.kids.some(k => k.className === "rec-rowerr")
      && doc.activeElement.getAttribute && doc.activeElement.getAttribute("data-undo") === "6",
      list.kids.map(k => k.className));

console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);
