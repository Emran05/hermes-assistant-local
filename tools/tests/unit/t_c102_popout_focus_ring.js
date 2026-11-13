// C102.2 (docs/audits/2026-10-21-codebase-audit.md) — the text fields in the
// widget pop-outs (expand.js: Tasks "Add a task", Notes scratchpad, Quick
// Links label/url, Markets "Add symbol") carried an INLINE `outline:none`.
// An inline declaration beats every stylesheet rule, including the page's
// global `:focus-visible{outline:2px solid var(--iris)}`, and their inline
// border also beats `input:focus{border-color:var(--iris)}` — so all a
// keyboard user saw on focus was an 18%-alpha 3px glow (~1.3:1).
//
// The REAL index.html + expand.js run in the mini DOM (_a11y_dom.js); the
// focused field's outline is resolved by the mini cascade over the page's
// real stylesheets plus the element's inline style.
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, computedValue, harness } = require("./_a11y_dom.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");

(async function main() {
  console.log("C102.2: pop-out fields keep the global focus ring");
  const src = fs.readFileSync(path.join(REPO, "dashboard", "expand.js"), "utf8");
  const hits = src.split("\n").map(function (l, i) { return /outline\s*:\s*(none|0)\b/.test(l) ? i + 1 : 0; }).filter(Boolean);
  T.check("no inline outline:none left anywhere in expand.js (incl. the shadowed early renderers)", hits.length === 0, hits);

  const w = await bootDashboard({ aux: ["expand.js"], routes: { "/api/expand": { tasks: [], available: true } } });
  const d = w.document;
  const ER = w.__run("EXPAND_RENDER");
  function mount(id, data) {
    const el = d.createElement("div");
    d.body.appendChild(el);
    ER[id](el, data);
    return el;
  }
  function ring(name, field) {
    if (!field) { T.check(name + ": field rendered", false); return; }
    // the mini DOM does not map a style="" attribute onto el.style (a browser
    // does), so do that here: the inline declarations must take part in the cascade
    field.style.cssText = field.getAttribute("style") || "";
    field.focus();
    T.check(name + ": the field takes focus", d.activeElement === field);
    const o = computedValue(w, field, "outline");
    T.check(name + ": focused, it shows the global 2px iris outline", !!o && /^2px solid var\(--iris\)$/.test(o.value) && o.rule !== null,
      o && { value: o.value, from: o.rule ? o.rule.selector : "inline style" });
    field.blur();
  }

  ring("Tasks · Add a task", mount("tasks", { tasks: [{ id: "t1", text: "one", done: false, ts: Date.now() / 1000 }], available: true }).querySelector("input.tk-new"));
  const notes = mount("notes", { text: "hello" });
  ring("Notes · scratchpad", notes.querySelector("#nt-ta"));
  const ql = mount("quicklinks", { links: [{ label: "GH", url: "https://github.com", domain: "github.com", mono: "G", hue: 1 }], suggestions: [] });
  ring("Quick Links · label", ql.querySelector("#ql-lb"));
  ring("Quick Links · url", ql.querySelector("#ql-url"));
  ring("Markets · Add symbol", mount("markets", { indices: [], watchlist: [], tickers: [], starred: [] }).querySelector("#mkt-q"));

  T.check("the scratchpad keeps its save-state border (inline, not a focus style)",
    /var\(--hairline\)/.test(notes.querySelector("#nt-ta").getAttribute("style") || ""));
  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
