// B18.2 — the "Every Lens" (framing) pop-out in aux_newsdesk.js rendered each
// feed item as a raw <a href="<feed url>">, a NAVIGATION that bypassed
// index.html's httpUrl()/wireLinks(). In the app shell a navigation is handed
// to macOS (decidePolicyFor, B18.3), which opens any scheme with its default
// handler — so a feed item pointing at file:///…/x.command or an app URL ran
// on one click. Now only an absolute http(s) URL becomes a link, opened with
// window.open(httpUrl(u)); anything else renders as plain text.
//
// The REAL index.html (its httpUrl/wireLinks/openPop) + aux_newsdesk.js run in
// the mini DOM (_a11y_dom.js); /api/expand is a local route.
"use strict";
const { bootDashboard, harness } = require("./_a11y_dom.js");
const T = harness();

const ANGLES = [
  { lean: "factual", url: "https://example.com/a", title: "Safe https", source: "Wire" },
  { lean: "public", url: "http://example.com/b", title: "Safe http", source: "Radio" },
  { lean: "left", url: "file:///Users/you/run.command", title: "Local file", source: "Evil" },
  { lean: "right", url: "javascript:alert(1)", title: "Script", source: "Evil" },
  { lean: "Mideast", url: "x-apple.systempreferences:com.apple.preference.security", title: "App URL", source: "Evil" },
  { lean: "factual", url: "/api/foryou", title: "Relative", source: "Evil" },
  { lean: "public", url: "", title: "No url", source: "Blog" },
  { lean: "left", url: '  https://example.com/c?q="x"  ', title: "Spaced", source: "Wire" }
];
const DATA = { rich: true, stories: [{ label: "One story", sources: 5, angles: ANGLES }] };

(async function main() {
  console.log("B18.2: Every Lens links are http(s)-only and never navigate");
  const w = await bootDashboard({ aux: ["aux_newsdesk.js"], routes: { "/api/expand": DATA } });
  const d = w.document;
  const opened = [];
  w.open = function (u, t, f) { opened.push({ u: u, t: t, f: f }); return null; };
  const ER = w.__run("EXPAND_RENDER");           // a top-level const in index.html, not a window property
  T.check("(setup) the framing pop-out renderer is registered", typeof ER.framing === "function");

  function check(el, where) {
    const anchors = Array.from(el.querySelectorAll("a"));
    T.check(where + ": only the http(s) items are links", anchors.length === 3, anchors.map(function (a) { return a.textContent; }));
    T.check(where + ": no anchor carries a real href (no navigation)", anchors.every(function (a) { return a.getAttribute("href") === "#"; }),
      anchors.map(function (a) { return a.getAttribute("href"); }));
    T.check(where + ": no file:, javascript: or app URL anywhere in the markup",
      !/file:|javascript:|x-apple/i.test(el.innerHTML), (el.innerHTML.match(/(?:href|data-url)="[^"]*"/g) || []));
    T.check(where + ": the other items are still shown, as text",
      ["Local file", "Script", "App URL", "Relative", "No url"].every(function (t) { return el.textContent.indexOf(t) >= 0; }));
    opened.length = 0;
    const evs = anchors.map(function (a) { return w.__click(a); });
    T.check(where + ": every click is a window.open, never a navigation", evs.every(function (e) { return e.defaultPrevented; }));
    T.check(where + ": window.open gets exactly the three http(s) URLs",
      JSON.stringify(opened.map(function (o) { return o.u; })) === JSON.stringify(["https://example.com/a", "http://example.com/b", "https://example.com/c?q=%22x%22"]),
      opened.map(function (o) { return o.u; }));
    T.check(where + ": ...in a new browsing context with noopener", opened.every(function (o) { return o.t === "_blank" && /noopener/.test(o.f || ""); }));
  }

  // 1. the renderer on its own (it must not depend on the caller's wireLinks)
  const box = d.createElement("div");
  d.body.appendChild(box);
  ER.framing(box, DATA);
  check(box, "renderer");

  // 2. the real pop-out path (openPop -> /api/expand -> EXPAND_RENDER -> wireLinks)
  await w.__run("openPop('framing')");
  await w.__tick();
  const body = d.querySelector("#wpop-sheet .w-body");
  T.check("(setup) the pop-out rendered the framing body", !!body && body.textContent.indexOf("One story") >= 0, body && body.textContent.slice(0, 80));
  if (body) check(body, "pop-out");
  T.check("a link click does not close the pop-out", !d.getElementById("wpop").hidden);

  T.check("no handler threw", w.__errors.length === 0, w.__errors.map(String));
  T.done();
})().catch(function (e) { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
