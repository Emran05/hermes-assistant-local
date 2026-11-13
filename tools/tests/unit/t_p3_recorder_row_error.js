// 2026-10-08 audit, P3 "Frontend": a refused Undo's reason stays under its row
// across the Flight Recorder's 3 s refresh.
//
// recRowError() inserted the message after the row, and the next poll rebuilt
// the list with innerHTML, so "irreversible — no snapshot captured …" was gone
// within three seconds, usually before it could be read. Drives the REAL
// aux_recorder.js (recUndo + recRender) in node over a tiny fake list element.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_recorder.js");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

function node(cls, attrs) {
  return { className: cls, attrs: attrs || {}, textContent: "", parentNode: null,
    classList: { contains(c) { return (" " + this._o.className + " ").indexOf(" " + c + " ") >= 0; } },
    getAttribute(k) { return this.attrs[k]; }, addEventListener() {},
    get nextSibling() {
      const k = this.parentNode ? this.parentNode.kids : [];
      return k[k.indexOf(this) + 1] || null;
    } };
}
function mk(cls, attrs) { const n = node(cls, attrs); n.classList._o = n; return n; }
const list = {
  kids: [],
  set innerHTML(h) {
    this.kids = [];
    const re = /<div class="(rec-row[^"]*)" data-id="([^"]+)"/g; let m;
    while ((m = re.exec(h))) {
      const r = mk(m[1], { "data-id": m[2] }); r.parentNode = this; this.kids.push(r);
    }
  },
  get innerHTML() { return ""; },
  querySelector(sel) {
    const m = /^\.rec-row\[data-id="([^"]+)"\]$/.exec(sel);
    return m ? this.kids.find(k => k.attrs["data-id"] === m[1]) || null : null;
  },
  querySelectorAll(sel) { return sel === ".rec-row" ? this.kids.filter(k => /rec-row/.test(k.className)) : []; },
  insertBefore(n, ref) {
    n.parentNode = this;
    const i = ref ? this.kids.indexOf(ref) : -1;
    if (i < 0) this.kids.push(n); else this.kids.splice(i, 0, n);
  },
  removeChild(n) { this.kids = this.kids.filter(k => k !== n); },
  appendChild(n) { this.insertBefore(n, null); },
};
global.document = {
  getElementById(id) { return id === "rec-list" ? list : null; },
  createElement() { return mk(""); },
};
global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
global.relTime = () => "just now";
global.REDUCE = true;
global.confirm = () => true;
let answer = null;
global.fetch = async () => ({ json: async () => answer });
(0, eval)(fs.readFileSync(FILE, "utf8"));

function errText() {
  const e = list.kids.find(k => k.className === "rec-rowerr");
  return e ? e.textContent : "";
}

(async () => {
  recState.last = { actions: [{ id: 5, kind: "write", reversible: "yes", status: "done",
                                tool: "write_file", target: "/p/a.txt", source: "telegram" }],
                    counts: {} };
  recRender();
  console.log("\n1. the refusal is shown, and is still there after the next refresh");
  answer = { ok: false, error: "irreversible", detail: "no snapshot captured" };
  await recUndo("5", false);
  check("shown under the row", /no snapshot captured/.test(errText()), errText());
  recRender();                                   // what the 3 s poll does
  check("still there after the list is rebuilt", /no snapshot captured/.test(errText()),
        list.kids.map(k => k.className));
  check("directly under its row", list.kids.length === 2 &&
        list.kids[1].className === "rec-rowerr", list.kids.map(k => k.className));

  console.log("\n2. a later successful undo clears it");
  answer = { ok: true };
  global.loadRecorder = async () => recRender();
  await recUndo("5", false);
  recRender();
  check("gone once the undo succeeded", errText() === "", errText());

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.log("  FAIL harness threw " + e); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
