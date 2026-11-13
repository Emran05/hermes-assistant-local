// C128 (audit 2026-10-21): editing a multi-line You-Model entry keeps its lines.
//
// The entry editor was an <input type="text" value="...">. An input's value
// drops every CR/LF (the HTML value-sanitisation algorithm for type=text), so
// opening a two-line entry and pressing Save wrote "line oneline two" back to
// the memory file. This drives the REAL aux_youmodel.js in node: a small fake
// DOM renders the card, clicks Edit on the entry and then Save, and captures
// the /api/memory/save body. The fake <input> applies that same sanitisation
// rule, so the old code fails here exactly as it did in the WebView.
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

// ---- a fake DOM just big enough for render() + wire() ----------------------
function unescapeHtml(s) {
  return String(s).replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}
function el(tag, attrs) {
  const e = {
    tag, attrs: attrs || {}, listeners: {}, classList: { add() {}, remove() {}, contains() { return false; } },
    _html: "", _kids: {}, disabled: false, value: "",
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; this._kids = {}; },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(t, f) { this.listeners[t] = f; },
    appendChild(c) { if (c.id === "mind-extra-youmodel") STATE.card = c; return c; },
    remove() {}, focus() {}, select() {},
    querySelector(sel) {
      if (this._kids[sel] !== undefined) return this._kids[sel];
      let out = null;
      if (sel === "input") {
        const m = /<input[^>]*?value="([^"]*)"/.exec(this._html);
        if (m) { out = el("input"); out.value = unescapeHtml(m[1]).replace(/[\r\n]/g, ""); }
        else if (/<input/.test(this._html)) out = el("input");
      } else if (sel === "textarea") {
        if (/<textarea/.test(this._html)) out = el("textarea");
      } else {
        const m = /^\[([\w-]+)\]$/.exec(sel);
        if (m && new RegExp(m[1] + '="').test(this._html)) out = el("button");
      }
      this._kids[sel] = out;
      return out;
    },
    querySelectorAll(sel) {
      if (sel !== ".ym-row") return [];
      if (this._kids[sel]) return this._kids[sel];
      const rows = [], re = /<div class="ym-row" data-f="(\d+)" data-i="(\d+)">([\s\S]*?)<\/div>/g;
      let m;
      while ((m = re.exec(this._html))) {
        const r = el("div", { "data-f": m[1], "data-i": m[2] });
        r.innerHTML = m[3];
        rows.push(r);
      }
      this._kids[sel] = rows;
      return rows;
    },
  };
  return e;
}
const STATE = { card: null, posts: [] };
const grid = el("div");
global.window = global;
global.document = {
  getElementById(id) { return id === "view-mind" ? grid : (id === "ym-css" ? {} : null); },
  createElement: el, head: el("head"),
};
global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
global.REDUCE = true;
const TWO = "met Jane at the meetup\nfollow up about the robotics role";
const DATA = { ok: true, filled: 1, total: 5, empty: false, people: [],
  files: [{ name: "NOW.md", label: "Now", exists: true, etag: "e1", char_used: 90,
            char_budget: 4000, hint: "",
            entries: ["<!-- what you are doing now -->", TWO, "ship the 1.3 release"] }] };
global.fetch = async (url, opt) => {
  if (opt && opt.method === "POST") STATE.posts.push({ url, body: JSON.parse(opt.body) });
  return { status: 200, json: async () => (opt && opt.method === "POST") ? { ok: true } : DATA };
};
(0, eval)(fs.readFileSync(FILE, "utf8"));

async function editAndSave(index) {
  STATE.posts.length = 0;
  await window.youModelPanel();
  const rows = STATE.card.querySelectorAll(".ym-row");
  const row = rows[index];
  row.querySelector("[data-edit]").listeners.click();
  const field = row.querySelector("textarea") || row.querySelector("input");
  row.querySelector("[data-sv]").listeners.click();
  await new Promise(r => setTimeout(r, 0));
  return { row, field, post: STATE.posts.find(p => p.url === "/api/memory/save") };
}

(async () => {
  console.log("\n1. a two-line entry survives Edit -> Save untouched");
  let r = await editAndSave(0);
  check("it edits in a textarea", r.field && r.field.tag === "textarea", r.field && r.field.tag);
  check("the editor holds both lines", r.field && r.field.value === TWO, r.field && r.field.value);
  const want = ["<!-- what you are doing now -->", TWO, "ship the 1.3 release"].join("\n§\n");
  check("the saved file keeps the newline", r.post && r.post.body.content === want,
        r.post && r.post.body.content);

  console.log("\n2. a one-line entry keeps the one-line field");
  r = await editAndSave(1);
  check("it edits in an <input>", r.field && r.field.tag === "input", r.field && r.field.tag);
  check("and saves unchanged", r.post && r.post.body.content === want,
        r.post && r.post.body.content);

  console.log("\n3. Enter adds a line in the textarea; Cmd+Enter saves");
  STATE.posts.length = 0;
  await window.youModelPanel();
  const row = STATE.card.querySelectorAll(".ym-row")[0];
  row.querySelector("[data-edit]").listeners.click();
  const ta = row.querySelector("textarea");
  let prevented = 0;
  if (ta && ta.listeners.keydown) {
    ta.listeners.keydown({ key: "Enter", preventDefault() { prevented++; } });
    await new Promise(r => setTimeout(r, 0));
    check("plain Enter does not save", STATE.posts.length === 0 && prevented === 0, STATE.posts);
    ta.listeners.keydown({ key: "Enter", metaKey: true, preventDefault() { prevented++; } });
    await new Promise(r => setTimeout(r, 0));
    check("Cmd+Enter saves", STATE.posts.length === 1, STATE.posts.length);
  } else {
    check("plain Enter does not save", false, "no textarea");
    check("Cmd+Enter saves", false, "no textarea");
  }

  console.log("\n4. a typed line holding only the \u00a7 delimiter is refused");
  STATE.posts.length = 0;
  await window.youModelPanel();
  const row4 = STATE.card.querySelectorAll(".ym-row")[0];
  row4.querySelector("[data-edit]").listeners.click();
  const ta4 = row4.querySelector("textarea");
  if (ta4) ta4.value = "met Jane at the meetup\n\u00a7\nsplit me";
  row4.querySelector("[data-sv]").listeners.click();
  await new Promise(r => setTimeout(r, 0));
  check("nothing is saved (it would split the entry in two)",
        !STATE.posts.some(p => p.url === "/api/memory/save"), STATE.posts);

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.log("  FAIL harness threw " + e); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
