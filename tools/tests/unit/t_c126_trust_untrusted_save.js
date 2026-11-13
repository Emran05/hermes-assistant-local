// C126 review (audit 2026-10-21): over an UNTRUSTED policy, the Trust panel's
// Clear button and the class Auto/Ask/Never control no longer re-trust the
// outside edit without the same review "Review & re-trust" shows.
//
// permissions.py _save() re-signs the whole file on every write, so clearing
// one pattern (the new Clear button) or moving one class while the file is
// untrusted silently blessed everything else in it — including a planted
// class-level Auto — which is exactly the blind click C126 is about. This
// drives the REAL aux_trust.js in node over a small fake DOM: confirm() is
// declined, then accepted, and every POST is captured. It also checks that a
// class change keeps the header's "N custom" count (updateSummary dropped it).
"use strict";
const fs = require("fs");
const path = require("path");
const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_trust.js");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name +
              (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}

// ---- a fake DOM just deep enough for renderData/wire/onPick/updateSummary ----
function classList(owner) {
  return {
    add(c) { owner._cls.add(c); }, remove(c) { owner._cls.delete(c); },
    contains(c) { return owner._cls.has(c); },
  };
}
function el(tag) {
  const e = {
    tag, id: "", className: "", disabled: false, listeners: {}, attrs: {},
    style: {}, textContent: "", _cls: new Set(), _kids: null, _html: "",
    getAttribute(k) { return this.attrs[k]; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    addEventListener(t, f) { this.listeners[t] = f; },
    appendChild(c) { if (c.id === "mind-extra-trust") mounted = c; return c; },
    remove() { if (mounted === this) mounted = null; },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; this._kids = null; },
    querySelector(sel) {
      if (sel === "#tp-retrust") return /id="tp-retrust"/.test(this._html) ? (this._rt ||= el("button")) : null;
      if (sel === "h2 .tiny") return (this._tiny ||= Object.assign(el("span"), {
        textContent: (/<span class="tiny"[^>]*>([^<]*)</.exec(this._html) || [])[1] || "" }));
      if (sel === ".tp-seg") return this._seg || null;
      if (sel === ".tp-eb") return this._eb || null;
      if (sel === ".tp-thumb") return this._thumb || null;
      return null;
    },
    querySelectorAll(sel) {
      if (sel === "[data-pclear]") {
        if (!this._pc) {
          this._pc = [];
          const re = /data-pclear="(\d+)"/g; let m;
          while ((m = re.exec(this._html))) {
            const b = el("button"); b.attrs["data-pclear"] = m[1]; this._pc.push(b);
          }
        }
        return this._pc;
      }
      if (sel === ".tp-row") {
        if (!this._rows) {
          this._rows = [];
          const re = /<div class="tp-row" data-class="([^"]+)" data-tier="([^"]+)"/g; let m;
          while ((m = re.exec(this._html))) {
            const row = el("div");
            row.attrs["data-class"] = m[1]; row.attrs["data-tier"] = m[2];
            const seg = el("div"); seg._thumb = el("span"); seg._btns = [];
            for (const t of ["auto", "ask", "never"]) {
              const b = el("b"); b.attrs["data-tier"] = t; b.classList = classList(b);
              seg._btns.push(b);
            }
            seg.querySelectorAll = s => (s === "b[data-tier]" ? seg._btns : []);
            row._seg = seg; row._eb = el("div");
            this._rows.push(row);
          }
        }
        return this._rows;
      }
      return [];
    },
  };
  e.classList = classList(e);
  return e;
}
let mounted = null;
const grid = el("div");
global.window = global;
global.document = {
  getElementById(id) {
    if (id === "view-mind") return grid;
    if (id === "trust-css") return {};
    if (id === "mind-extra-trust") return mounted;
    return null;
  },
  createElement: el, head: el("head"),
};
global.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
global.REDUCE = true;
global.localStorage = { getItem: () => "1", setItem() {} };   // "Never" already acknowledged

const confirms = [], posts = [];
let answer = false, gets = 0;
global.confirm = m => { confirms.push(m); return answer; };

const FORCE = "git force push (rewrites remote history)";
const KILL = "kill a process";
function payload(trusted) {
  return {
    ok: true, trusted, exists: true, recent: [],
    classes: [
      { id: "git-destructive", label: "Git destructive", risk: "med", tier: "ask",
        "default": "ask", floor: "", auto_allowed: true, desc: "", pattern_count: 3 },
      { id: "process-control", label: "Process control", risk: "med", tier: "auto",
        "default": "ask", floor: "", auto_allowed: true, desc: "", pattern_count: 2 },
    ],
    patterns: { [FORCE]: "auto", [KILL]: "never" },
    pattern_overrides: [
      { pattern: FORCE, tier: "auto", "class": "git-destructive", class_label: "Git destructive",
        class_tier: "ask", effective: trusted ? "auto" : "ask", clamped_by: "" },
      { pattern: KILL, tier: "never", "class": "process-control", class_label: "Process control",
        class_tier: "auto", effective: "never", clamped_by: "" },
    ],
  };
}
let current = payload(false);
global.fetch = async (url, opt) => {
  if (opt && opt.method === "POST") {
    const b = JSON.parse(opt.body); posts.push(b);
    if (b.op === "set_class")
      return { json: async () => ({ ok: true, classes: current.classes.map(c =>
        c.id === b["class"] ? Object.assign({}, c, { tier: b.tier }) : c) }) };
    return { json: async () => ({ ok: true }) };
  }
  gets++;
  return { json: async () => current };
};
(0, eval)(fs.readFileSync(FILE, "utf8"));

// onPick is not awaited by its click listener: let its fetch chain settle
const tick = () => new Promise(r => setTimeout(r, 0));
const clearBtn = i => (mounted.querySelectorAll("[data-pclear]") || [])[i];
const segBtn = (cls, tier) => {
  const row = mounted.querySelectorAll(".tp-row").find(r => r.attrs["data-class"] === cls);
  return row && row._seg._btns.find(b => b.attrs["data-tier"] === tier);
};

(async () => {
  console.log("\n== an untrusted policy: every save is a re-trust, so it is reviewed ==");
  await window.trustPanel();
  let b = clearBtn(1);                              // the "kill" pattern
  answer = false; confirms.length = 0; posts.length = 0;
  await b.listeners.click();
  check("declining the review on Clear sends nothing", posts.length === 0, posts);
  const m = confirms[0] || "";
  check("the Clear review says the save re-trusts the file",
        /re-trusts/.test(m) && /Save and re-trust\?/.test(m), m);
  check("…lists the other Auto pattern and the Auto class it would bless",
        m.includes(FORCE) && /Process control: Auto/.test(m), m);
  check("…but not the pattern being cleared", !m.includes("pattern \u201c" + KILL), m);
  answer = true;
  await b.listeners.click();
  check("accepting it clears that one pattern",
        posts.length === 1 && posts[0].op === "clear_pattern" && posts[0].pattern === KILL, posts);

  await window.trustPanel();
  answer = false; confirms.length = 0; posts.length = 0;
  b = segBtn("git-destructive", "never");
  await b.listeners.click();
  await tick();
  check("declining the review on a class change sends nothing", posts.length === 0, posts);
  check("…and the review shows the class at its NEW tier",
        /Setting this class to Never/.test(confirms[0] || "") &&
        /Git destructive: Never/.test(confirms[0] || ""), confirms[0]);
  const row = mounted.querySelectorAll(".tp-row").find(r => r.attrs["data-class"] === "git-destructive");
  check("…and leaves the row where it was", row.attrs["data-tier"] === "ask", row.attrs["data-tier"]);
  answer = true;
  const before = gets;
  await b.listeners.click();
  await tick();
  check("accepting it saves the class", posts.length === 1 && posts[0].op === "set_class"
        && posts[0].tier === "never", posts);
  check("…and re-renders the panel (the banner was for the old file)", gets > before, gets - before);

  console.log("\n== a trusted policy saves directly ==");
  current = payload(true);
  await window.trustPanel();
  answer = false; confirms.length = 0; posts.length = 0;
  await clearBtn(0).listeners.click();
  check("Clear needs no review when the file is trusted",
        confirms.length === 0 && posts.length === 1 && posts[0].op === "clear_pattern", [confirms, posts]);
  await window.trustPanel();
  posts.length = 0;
  await segBtn("git-destructive", "never").listeners.click();
  await tick();
  check("a class change needs none either", confirms.length === 0 && posts.length === 1, [confirms, posts]);
  const tiny = mounted.querySelector("h2 .tiny");
  check("the header keeps its custom-pattern count after a class change",
        /^1 auto · 0 ask · 1 never · 2 custom$/.test(tiny.textContent || ""), tiny.textContent);

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})().catch(e => {
  console.log("  FAIL harness threw " + (e && e.stack || e));
  console.log("\nTESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
