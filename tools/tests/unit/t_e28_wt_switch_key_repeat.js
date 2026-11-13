// E28, Watchtower half (docs/audits/2026-11-09-codebase-audit.md): wireSwitch
// forwarded EVERY keydown of a held Space/Enter to el.click(), auto-repeats
// included.  The click path dropped repeats only while a write was in flight,
// so a held key flipped the switch again each time a response landed — the
// end state was the parity of the accepted presses (News could end back ON).
//
// AFTER: a keydown with `repeat` is ignored (still preventDefault'ed, so a
// held Space does not scroll), and so is any key while a flip is in flight;
// B24's revert-on-failure is unchanged.
//
// Evals the REAL dashboard/aux_watchtower.js in node on the B24 suite's DOM
// stub, fetch mocked; WT_JS_PATH points it at another copy.
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const FILE = process.env.WT_JS_PATH || path.join(REPO, "dashboard", "aux_watchtower.js");

let fails = [], passes = 0;
function check(name, cond, detail) {
  if (cond) { passes++; console.log("  ok   " + name); }
  else { fails.push(name); console.log("  FAIL " + name + "  <- " + JSON.stringify(detail === undefined ? "" : detail)); }
}

// ---------------------------------------------------------------- DOM stub
const VOID = new Set(["input", "br", "img", "hr", "meta", "link", "path", "circle", "rect"]);
function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(), attrs: {}, children: [], parentNode: null,
    style: {}, listeners: {}, _text: "", value: "", disabled: false,
    get id() { return this.attrs.id || ""; }, set id(v) { this.attrs.id = String(v); },
    get className() { return this.attrs.class || ""; }, set className(v) { this.attrs.class = String(v); },
    get textContent() { return this._text; }, set textContent(v) { this._text = String(v); this.children = []; },
    set innerHTML(html) { this._html = String(html); this.children = []; parseInto(this, String(html)); },
    get innerHTML() { return ""; },
    classList: null,
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    remove() { const p = this.parentNode; if (p) p.children = p.children.filter((x) => x !== this); this.parentNode = null; },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
    dispatch(t, ev) { (this.listeners[t] || []).forEach((fn) => fn.call(this, ev || {})); },
    click() { this.dispatch("click", { target: this }); },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    querySelectorAll(sel) {
      const out = [];
      const sels = sel.split(",").map((s) => s.trim());
      (function walk(n) {
        n.children.forEach((c) => { if (sels.some((s) => matches(c, s))) out.push(c); walk(c); });
      })(this);
      return out;
    },
  };
  el.classList = {
    _get() { return (el.attrs.class || "").split(/\s+/).filter(Boolean); },
    contains(c) { return this._get().indexOf(c) >= 0; },
    add(c) { const l = this._get(); if (l.indexOf(c) < 0) l.push(c); el.attrs.class = l.join(" "); },
    remove(c) { el.attrs.class = this._get().filter((x) => x !== c).join(" "); },
  };
  return el;
}
function matches(el, s) {
  if (s[0] === "#") return el.attrs.id === s.slice(1);
  if (s[0] === ".") return el.classList.contains(s.slice(1));
  if (s[0] === "[") return el.hasAttribute(s.slice(1, -1).split("=")[0]);
  return el.tagName === s.toUpperCase();
}
function parseInto(root, html) {
  const re = /<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/g;
  const stack = [root];
  let m;
  while ((m = re.exec(html))) {
    const close = m[1] === "/", tag = m[2].toLowerCase(), rest = m[3] || "";
    if (close) {
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === tag.toUpperCase()) { stack.length = i; break; }
      }
      continue;
    }
    const el = makeEl(tag);
    const ar = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
    let a;
    while ((a = ar.exec(rest))) {
      if (a[1] === "/") continue;
      el.attrs[a[1]] = a[2] != null ? a[2] : (a[3] != null ? a[3] : (a[4] != null ? a[4] : ""));
    }
    if (el.attrs.value != null) el.value = el.attrs.value;
    stack[stack.length - 1].appendChild(el);
    if (!VOID.has(tag) && !/\/\s*$/.test(rest)) stack.push(el);
  }
}

const grid = makeEl("div"); grid.id = "view-mind";
const head = makeEl("head");
global.window = global;
global.document = {
  head, body: makeEl("body"), documentElement: makeEl("html"),
  createElement: makeEl,
  getElementById(id) {
    if (id === "view-mind") return grid;
    if (id === "watchtower-css") return head.querySelector("#watchtower-css");
    return grid.querySelector("#" + id);
  },
};
global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
global.REDUCE = true;
global.confirm = () => true;

// ---------------------------------------------------------------- fetch mock
const STATE = {
  ok: true, rules: [{ id: "r1", type: "system_metric", label: "RAM high", enabled: true,
                       cooldown_min: 30, channels: ["telegram"] }],
  stats: {}, brief: { enabled: true, hour: 8, minute: 0 }, quiet_hours: { start: "22:00", end: "07:00" },
  daily_cap: 20, midday: { enabled: true, hour: 15, minute: 0 }, evening: { enabled: true, hour: 18, minute: 0 },
  breaking: { enabled: true, override_quiet: true, daily_cap: 5 }, master: { briefings: true, news: false },
  web_research: { enabled: false }, config_error: "", recent: [],
};
const posts = [];
let REPLY = null;                 // function(body) -> {status, json} | "reject" | "hang"
const pending = [];
global.fetch = function (url, opts) {
  if (!opts || opts.method !== "POST")
    return Promise.resolve({ status: 200, json: async () => JSON.parse(JSON.stringify(STATE)) });
  const body = JSON.parse(opts.body);
  posts.push(body);
  const rep = REPLY ? REPLY(body) : { status: 200, json: { ok: true } };
  if (rep === "reject") return Promise.reject(new Error("network down"));
  if (rep === "hang") return new Promise((res) => pending.push(() => res({ status: 200, json: async () => ({ ok: true }) })));
  return Promise.resolve({ status: rep.status, json: async () => rep.json });
};
const flush = () => new Promise((r) => setTimeout(r, 0));

eval(fs.readFileSync(FILE, "utf8"));

(async function () {
  console.log("E28: a held key flips a Watchtower switch once");
  await window.watchtowerPanel();
  const card = grid.querySelector("#mind-extra-watchtower");
  const mn = card.querySelector("#wt-master-news");
  check("News master starts OFF", mn && !mn.classList.contains("on"));

  // a held Space: the first keydown, then auto-repeats while the write hangs,
  // then more auto-repeats after it lands
  posts.length = 0;
  REPLY = () => "hang";
  let prevented = 0;
  const key = (repeat, k) => mn.dispatch("keydown", {
    key: k || " ", repeat: repeat, target: mn, preventDefault() { prevented++; } });
  key(false);
  for (let i = 0; i < 5; i++) key(true);
  check("one write for the press, none for the repeats in flight", posts.length === 1, posts);
  check("the switch shows ON while saving", mn.classList.contains("on"));
  pending.splice(0).forEach((f) => f()); await flush(); await flush();
  for (let i = 0; i < 6; i++) { key(true); await flush(); await flush(); }
  pending.splice(0).forEach((f) => f()); await flush(); await flush();
  check("repeats after the write landed send nothing", posts.length === 1, posts);
  check("News stays ON — one flip for one press", mn.classList.contains("on") &&
        mn.getAttribute("aria-checked") === "true", mn.attrs);
  check("every key event was preventDefault'ed (no page scroll)", prevented === 12, prevented);

  // Enter held the same way
  posts.length = 0;
  REPLY = (b) => ({ status: 200, json: { ok: true, master: { briefings: true, news: !!b.news } } });
  key(false, "Enter");
  await flush(); await flush();
  for (let i = 0; i < 6; i++) { key(true, "Enter"); await flush(); await flush(); }
  check("held Enter: one write", posts.length === 1, posts);
  check("held Enter: one flip (OFF again)", !mn.classList.contains("on"), mn.attrs);

  // a fresh press (not a repeat) while a write is in flight is ignored too
  posts.length = 0;
  REPLY = () => "hang";
  key(false);
  key(false);
  check("a second press while saving is ignored", posts.length === 1, posts);
  pending.splice(0).forEach((f) => f()); await flush(); await flush();
  posts.length = 0;
  REPLY = (b) => ({ status: 200, json: { ok: true, master: { briefings: true, news: !!b.news } } });
  key(false);
  await flush(); await flush();
  check("a new press after it landed flips again", posts.length === 1 &&
        !mn.classList.contains("on"), posts);

  // B24 still: a failed write by keyboard puts the switch back
  REPLY = () => "reject";
  key(false);
  check("optimistic flip", mn.classList.contains("on"));
  await flush(); await flush();
  check("network failure reverts it (B24)", !mn.classList.contains("on") &&
        mn.getAttribute("aria-checked") === "false", mn.attrs);
  posts.length = 0;
  REPLY = (b) => ({ status: 200, json: { ok: true, master: { briefings: true, news: !!b.news } } });
  key(false);
  await flush(); await flush();
  check("and the switch works again after a failure", posts.length === 1 &&
        mn.classList.contains("on"), posts);

  console.log("TESTS " + passes + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.log("  FAIL crashed: " + (e && e.stack || e));
  console.log("TESTS " + passes + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
