// B24 + C94 — the Watchtower card's switches.
//
// B24: every switch (both masters included) flipped optimistically and never
// reverted on a failed write, so a 503 (unreadable watchtower.json), a 500 or a
// dead dashboard left the card showing a state that was never saved.
// C94: the switches were unnamed <span role=switch> with no tabindex and no
// key handler — unreachable from the keyboard, nameless to a screen reader.
//
// Evals the REAL dashboard/aux_watchtower.js in node against a small DOM stub
// (a tag-stack HTML parser, enough for the markup this module builds), with
// fetch mocked — nothing touches the dashboard. WT_JS_PATH points it at
// another copy (e.g. the pre-fix file) to show it fails there.
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
  console.log("B24/C94: watchtower switches");
  await window.watchtowerPanel();
  const card = grid.querySelector("#mind-extra-watchtower");
  check("the card mounted", !!card);
  const togs = card ? card.querySelectorAll(".wt-tog") : [];
  check("switches rendered", togs.length >= 9, togs.length);

  // ---- C94: named and focusable --------------------------------------------
  check("every switch is role=switch", togs.every((t) => t.getAttribute("role") === "switch"));
  check("every switch is in the tab order", togs.every((t) => t.getAttribute("tabindex") === "0"),
        togs.map((t) => t.getAttribute("tabindex")));
  check("every switch has a name", togs.every((t) => (t.getAttribute("aria-label") || "").trim() !== ""),
        togs.map((t) => t.getAttribute("aria-label")));
  const mb = card.querySelector("#wt-master-briefs");
  const mn = card.querySelector("#wt-master-news");
  check("the masters are named", mb && /briefing/i.test(mb.getAttribute("aria-label") || "") &&
        mn && /news/i.test(mn.getAttribute("aria-label") || ""));
  const rt = card.querySelector("#wt-tog-r1");
  check("a watch switch is named after its watch", rt && /RAM high/.test(rt.getAttribute("aria-label") || ""),
        rt && rt.getAttribute("aria-label"));

  // keyboard: Space flips a focused switch
  posts.length = 0;
  REPLY = (b) => ({ status: 200, json: { ok: true, master: { briefings: !!b.briefings, news: false } } });
  let prevented = false;
  mb.dispatch("keydown", { key: " ", target: mb, preventDefault() { prevented = true; } });
  await flush(); await flush();
  check("Space on a switch sends one write", posts.length === 1 && posts[0].op === "set_master", posts);
  check("Space does not scroll the page", prevented);
  check("... and the switch shows the saved state (off)",
        !mb.classList.contains("on") && mb.getAttribute("aria-checked") === "false");
  posts.length = 0;
  mb.dispatch("keydown", { key: "Enter", target: mb, preventDefault() {} });
  await flush(); await flush();
  check("Enter flips it back on", posts.length === 1 && mb.classList.contains("on"), posts);

  // ---- B24: a failed write puts the switch back ----------------------------
  REPLY = () => "reject";
  mb.click();
  check("the flip is immediate (optimistic)", !mb.classList.contains("on"));
  await flush(); await flush();
  check("network failure: the master switch is back ON", mb.classList.contains("on") &&
        mb.getAttribute("aria-checked") === "true", mb.attrs);
  const eb = card.querySelector("#wt-ctl-eb");
  check("network failure: the card says it was not saved", eb && /save/i.test(eb.textContent), eb && eb.textContent);

  REPLY = () => ({ status: 503, json: { ok: false, error: "watchtower.json is unreadable; nothing was saved" } });
  mn.click();
  await flush(); await flush();
  check("503: the News master is back OFF", !mn.classList.contains("on") &&
        mn.getAttribute("aria-checked") === "false", mn.attrs);
  check("503: the server's reason is shown", eb && /unreadable/.test(eb.textContent), eb && eb.textContent);

  REPLY = () => ({ status: 500, json: { ok: false, error: "internal" } });
  rt.click();
  await flush(); await flush();
  check("a watch's switch reverts on a failed write too", rt.classList.contains("on"), rt.attrs);

  const research = card.querySelector("#wt-research-on");
  REPLY = () => ({ status: 200, json: { ok: true, web_research: { enabled: false } } });
  research.click();
  await flush(); await flush();
  check("a saved write shows what the server STORED", !research.classList.contains("on"), research.attrs);

  // one write per switch at a time
  posts.length = 0;
  REPLY = () => "hang";
  const brk = card.querySelector("#wt-brk-on");
  brk.click(); brk.click(); brk.click();
  check("clicks while a write is in flight are ignored", posts.length === 1, posts.length);
  pending.forEach((f) => f()); await flush(); await flush();

  // ---- review: the C20 banner tells the truth about saving -----------------
  const bannerOf = () => {
    const c = grid.querySelector("#mind-extra-watchtower");
    return (c && c.querySelector("[data-wt-cfgerr]")) ? (c._html || "") : "";
  };
  STATE.config_error = "corrupt (JSONDecodeError)";
  await window.watchtowerPanel(); await flush();
  check("corrupt config: the banner says a save writes a fresh file",
        /writes a fresh one/.test(bannerOf()), bannerOf().slice(0, 300));
  STATE.config_error = "unreadable (PermissionError)";
  await window.watchtowerPanel(); await flush();
  check("unreadable config: the banner does not promise a save",
        bannerOf() !== "" && !/writes a fresh one/.test(bannerOf()) &&
        /Nothing can be saved/.test(bannerOf()), bannerOf().slice(0, 300));
  STATE.config_error = "";

  console.log("TESTS " + passes + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch((e) => {
  console.log("  FAIL crashed: " + (e && e.stack || e));
  console.log("TESTS " + passes + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
