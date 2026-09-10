// aux_context.js — the header chip must never keep asserting a stale number.
// Evals the module in node against a hand-rolled DOM stub (the pattern the
// repo uses for expand.js / aux_quickask.js), with fetch mocked so nothing
// touches the dashboard and no model is ever started.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_context.js");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail || "")));
  if (!cond) fails.push(name);
}

function makeEl(tag) {
  return {
    tagName: tag, id: "", className: "", title: "", textContent: "",
    hidden: false, children: [], parentNode: null, style: {},
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    insertBefore(c) { c.parentNode = this; this.children.push(c); return c; },
    querySelector() { return null; },
    querySelectorAll() { return []; }
  };
}

const byId = {};
const header = makeEl("header");
const wrap = makeEl("div");
wrap.className = "modelwrap";
header.appendChild(wrap);
const doc = {
  readyState: "complete",
  head: makeEl("head"),
  documentElement: makeEl("html"),
  getElementById(id) { return byId[id] || null; },
  createElement(tag) { return makeEl(tag); },
  querySelector(sel) { return sel === "header .modelwrap" ? wrap : null; },
  querySelectorAll() { return []; },
  addEventListener() {}
};
// createElement + appendChild register ids the way a real document would
const realAppend = header.appendChild.bind(header);
wrap.parentNode = header;
header.insertBefore = function (c) { if (c.id) byId[c.id] = c; c.parentNode = header; return c; };
doc.head.appendChild = function (c) { if (c.id) byId[c.id] = c; return c; };

global.window = global;
global.document = doc;
global.setTimeout = setTimeout;
global.esc = function (s) { return String(s == null ? "" : s); };

let NEXT = null;
global.fetch = async function () {
  return { json: async function () { return NEXT; } };
};

eval(fs.readFileSync(FILE, "utf8"));
const CX = global.hermesContext;

(async function () {
  console.log("\n1. a measured turn shows its numbers");
  NEXT = { ok: true, found: true, prompt_tokens: 24100, cached_tokens: 23100,
           cache_pct: 95.9, prefill_s: 1.2, window: 65536, pct_of_window: 36.8,
           requests: 2 };
  await CX.measure({ job: "j1" });
  const chip = doc.getElementById("ctx-chip");
  check("chip is visible", chip && chip.hidden === false);
  check("it reads the fresh number", /24\.1k ctx/.test(chip.textContent),
        chip.textContent);
  check("no stale class", chip.className.indexOf("is-stale") < 0, chip.className);
  const fresh = chip.textContent;

  console.log("\n2. found:false -> 'not measured', with the server's note");
  NEXT = { ok: true, found: false, requests: 0,
           note: "no model request is logged for this turn" };
  await CX.measure({ job: "j2" });
  check("it no longer asserts the old number", chip.textContent !== fresh,
        chip.textContent);
  check("it says so", /not measured/.test(chip.textContent), chip.textContent);
  check("dimmed", chip.className.indexOf("is-stale") >= 0, chip.className);
  check("the note is the tooltip", chip.title === NEXT.note, chip.title);
  check("still visible, not silently hidden", chip.hidden === false);
  check("LAST was cleared", CX.last() === null, CX.last());

  console.log("\n3. ok:false -> the last number is marked stale, not restated");
  NEXT = { ok: true, found: true, prompt_tokens: 30000, cache_pct: 90,
           prefill_s: 2, window: 65536, pct_of_window: 45.8 };
  await CX.measure({ job: "j3" });
  const good = chip.textContent;
  check("a good measurement clears the stale state",
        chip.className.indexOf("is-stale") < 0, chip.className);
  NEXT = { ok: false, error: "could not read the log" };
  await CX.measure({ job: "j4" });
  check("text is kept but dimmed", chip.textContent === good &&
        chip.className.indexOf("is-stale") >= 0, chip.className);
  check("the tooltip says what happened",
        /could not read the log/.test(chip.title), chip.title);
  check("and still labels it as the LAST measured turn",
        /Last measured turn/.test(chip.title), chip.title);

  console.log("\n4. a dead fetch is the same story, and never throws");
  NEXT = null;
  let threw = false;
  try { await CX.measure({ job: "j5" }); } catch (e) { threw = true; }
  check("no throw", !threw);
  check("still marked stale", chip.className.indexOf("is-stale") >= 0);

  console.log("\n5. nothing ever measured -> the chip stays hidden");
  byId["ctx-chip"] = null;
  const el2 = CX.markStale("measurement failed");
  check("markStale on a hidden chip shows nothing", el2 && el2.hidden === true,
        el2 && el2.hidden);

  console.log("");
  console.log("\nTESTS " + (checks - fails.length) + " passed " +
              fails.length + " failed");
  if (fails.length) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
  console.log("ALL PASS");
})();
