// 2026-10-08 audit, P3 "Frontend": the You-Model card's error line survives the
// re-render that follows every write, and "Add person" is usable again after a
// name it refused.
//
// Every write ends in refresh(), which rebuilds the whole card — including the
// #ym-err line the failure had just been written to — so "A card for X already
// exists" flashed for one frame. And the Add-person button was disabled before
// addPerson() ran; a refused name returns before any request (no refresh), so
// the button stayed disabled until the view was reopened. Drives the REAL
// aux_youmodel.js in node over a small fake DOM.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_youmodel.js");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

const FIELDS = { nm: "", nt: "" };
const STATE = { card: null, errEl: null, renders: 0, createAnswer: { ok: true } };
function el(tag) {
  return {
    tag, listeners: {}, innerHTML: "", disabled: false, id: "", textContent: "",
    addEventListener(t, f) { this.listeners[t] = f; },
    appendChild(c) {
      if (c.id === "mind-extra-youmodel") { STATE.card = c; STATE.errEl = el("div"); STATE.renders++; }
      return c;
    },
    remove() {}, getAttribute() { return null; },
    querySelector(sel) {
      if (sel === "#ym-paddb" && /id="ym-paddb"/.test(this.innerHTML)) {
        if (!this._addb) {
          this._addb = el("button");
          this._addb.parentNode = { querySelector: s => s === ".nm" ? { value: FIELDS.nm }
                                    : s === ".nt" ? { value: FIELDS.nt } : null };
        }
        return this._addb;
      }
      return null;
    },
    querySelectorAll() { return []; },
  };
}
const grid = el("div");
global.window = global;
global.document = {
  getElementById(id) {
    if (id === "view-mind") return grid;
    if (id === "ym-css") return {};
    if (id === "ym-err") return STATE.card && /id="ym-err"/.test(STATE.card.innerHTML) ? STATE.errEl : null;
    return null;
  },
  createElement: el, head: el("head"),
};
global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
global.REDUCE = true;
const DATA = { ok: true, filled: 0, total: 5, empty: false, files: [], people: [] };
global.fetch = async (url, opt) => {
  if (opt && opt.method === "POST")
    return { status: STATE.createAnswer.ok ? 200 : 409, json: async () => STATE.createAnswer };
  return { status: 200, json: async () => DATA };
};
(0, eval)(fs.readFileSync(FILE, "utf8"));
const tick = () => new Promise(r => setTimeout(r, 5));

(async () => {
  console.log("\n1. a failed write's message is still on the rebuilt card");
  await window.youModelPanel();
  const before = STATE.renders;
  STATE.createAnswer = { ok: false, error: "exists" };
  FIELDS.nm = "Jane Kim"; FIELDS.nt = "";
  await STATE.card.querySelector("#ym-paddb").listeners.click();
  await tick(); await tick();
  check("the card was rebuilt after the write", STATE.renders > before, STATE.renders);
  check("and its error line says why the write failed",
        /already exists/.test(STATE.errEl.textContent), STATE.errEl.textContent);

  console.log("\n2. a refused name leaves Add person usable");
  await window.youModelPanel();
  const btn = STATE.card.querySelector("#ym-paddb");
  FIELDS.nm = "   ";
  await btn.listeners.click();
  await tick();
  check("the error explains it", /name first/.test(STATE.errEl.textContent),
        STATE.errEl.textContent);
  check("the button is enabled again", btn.disabled === false, btn.disabled);

  console.log("\n3. a later write that succeeds does not bring the old error back");
  await window.youModelPanel();
  STATE.createAnswer = { ok: false, error: "exists" };
  FIELDS.nm = "Jane Kim"; FIELDS.nt = "";
  await STATE.card.querySelector("#ym-paddb").listeners.click();
  await tick(); await tick();
  check("the failure is shown", /already exists/.test(STATE.errEl.textContent),
        STATE.errEl.textContent);
  STATE.createAnswer = { ok: true };
  FIELDS.nm = "Ann Lee";
  await STATE.card.querySelector("#ym-paddb").listeners.click();
  await tick(); await tick();
  check("the card rebuilt after the success says nothing about the old name",
        !/already exists/.test(STATE.errEl.textContent), STATE.errEl.textContent);

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.log("  FAIL harness threw " + e); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
