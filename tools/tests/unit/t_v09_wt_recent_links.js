// V09 (docs/audits/2026-11-12-visual-audit.md) — Settings › Proactive ›
// "Recent fires" printed a fired brief as raw text, so every Markdown link
// read as `[S&P 500](https://finance.yahoo.com/quote/SPY)`.
//
// Now `[text](url)` renders as a link showing only the text — but the brief is
// THIRD-PARTY text (feed titles), so: every character is still escaped, a link
// is made only for an absolute http(s) URL that index.html's REAL httpUrl()
// accepts, the href is never the URL (it is "#", the URL rides in data-url and
// opens through window.open after a second httpUrl() check, the wireLinks()
// pattern), and anything that does not parse as a link stays escaped text.
//
// Runs the REAL aux_watchtower.js on the mini DOM with index.html's real
// esc() and httpUrl(); fetch answers from a route table.
"use strict";
const fs = require("fs");
const path = require("path");
const { createWindow, harness } = require("./_a11y_dom.js");
const { indexFunction } = require(path.join(__dirname, "..", "lib", "index_helpers.js"));
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const FILE = process.env.WT_JS_PATH || path.join(REPO, "dashboard", "aux_watchtower.js");
const T = harness("V09 recent fires links");

const LONG = "x".repeat(2500) + "[a](https://example.com/" + "y".repeat(100) + ")" + "[".repeat(1200) + "](".repeat(400);
const CASES = [
  // [label, text line (after the first line), expected link list [text, url], expected visible text]
  ["plain link", "Up: [S&P 500](https://finance.yahoo.com/quote/SPY) today",
    [["S&P 500", "https://finance.yahoo.com/quote/SPY"]], "Up: S&P 500 today"],
  ["javascript: URL", "[x](javascript:alert(1))", [], "[x](javascript:alert(1))"],
  ["javascript: no parens", "[x](javascript:alert`1`)", [], "[x](javascript:alert`1`)"],
  ["data: URL", "[x](data:text/html,<script>alert(1)</script>)", [], "[x](data:text/html,<script>alert(1)</script>)"],
  ["relative URL", "[x](/api/chat)", [], "[x](/api/chat)"],
  ["protocol-relative", "[x](//evil.example/a)", [], "[x](//evil.example/a)"],
  ["mixed-case scheme JAVASCRIPT", "[x](JaVaScRiPt:alert(1))", [], "[x](JaVaScRiPt:alert(1))"],
  ["img in the title", '[<img src=x onerror=alert(1)>](https://a.example/)',
    [['<img src=x onerror=alert(1)>', "https://a.example/"]], '<img src=x onerror=alert(1)>'],
  ["img outside a link", '<img src=x onerror=alert(1)> and "quotes" & \'apos\'', [],
    '<img src=x onerror=alert(1)> and "quotes" & \'apos\''],
  ["quote in the URL", '[x](https://a.example/"onmouseover="alert(1))', null, null],
  ["> in the URL", "[x](https://a.example/a>b)", null, null],
  ["space in the URL", "[x](https://a.example/a b)", [], "[x](https://a.example/a b)"],
  // a label may not hold brackets: nested ones stay text, never a half-link
  ["nested brackets", "[[x]](https://a.example/)", [], "[[x]](https://a.example/)"],
  ["nested link in a label", "[a [b](https://b.example/) c](https://a.example/)",
    [["b", "https://b.example/"]], "[a b c](https://a.example/)"],
  ["unbalanced open", "[x(https://a.example/)", [], "[x(https://a.example/)"],
  ["unbalanced close", "x](https://a.example/)", [], "x](https://a.example/)"],
  ["empty label", "[](https://a.example/)", [], "[](https://a.example/)"],
  ["two links", "[a](http://a.example/) and [b](https://b.example/x?y=1&z=2)",
    [["a", "http://a.example/"], ["b", "https://b.example/x?y=1&z=2"]], "a and b"],
  ["5,000-character line", LONG, null, null],
];

(async function () {
  const w = createWindow({ html: '<!doctype html><html><head></head><body><div id="view-mind"></div></body></html>' });
  w.esc = indexFunction("esc");
  w.httpUrl = indexFunction("httpUrl");
  w.REDUCE = true;
  const opened = [];
  w.open = function (u, t, f) { opened.push([u, t, f]); return null; };
  const recent = CASES.map((c, i) => ({ ts: 1789000000 - i, label: "Brief " + i, text: "Morning brief\n" + c[1] }));
  w.__routes = { "/api/watchtower": {
    ok: true, rules: [], stats: {}, brief: { enabled: true, hour: 8, minute: 0 },
    quiet_hours: { start: "22:00", end: "07:00" }, daily_cap: 20,
    midday: { enabled: true, hour: 15, minute: 0 }, evening: { enabled: true, hour: 18, minute: 0 },
    breaking: { enabled: true, override_quiet: true, daily_cap: 5 }, master: { briefings: true, news: true },
    web_research: { enabled: false }, config_error: "", recent: recent } };
  w.__run(fs.readFileSync(FILE, "utf8"), "aux_watchtower.js");
  const t0 = Date.now();
  await w.watchtowerPanel();
  const ms = Date.now() - t0;
  const d = w.document;
  const card = d.getElementById("mind-extra-watchtower");
  T.check("the card mounted", !!card);
  if (!card) return T.done();
  const fires = Array.from(card.querySelectorAll(".wt-fire .wt-fx"));
  T.check("one row per fire (12 shown)", fires.length === 12, fires.length);
  T.check("the 5,000-character line renders quickly (<500 ms for the card)", ms < 500, ms);

  // across every row: nothing but text and <a> survives inside .wt-fx
  function walk(n, out) { for (const c of n.children) { out.push(c); walk(c, out); } return out; }
  // render each case on its own so the 12-row cap does not hide one
  for (let i = 0; i < CASES.length; i++) {
    const c = CASES[i];
    w.__routes["/api/watchtower"].recent = [{ ts: 1789000000, label: "Brief", text: "Morning brief\n" + c[1] }];
    await w.watchtowerPanel();
    const fx = d.querySelector("#mind-extra-watchtower .wt-fire .wt-fx");
    const els = walk(fx, []);
    const tags = els.map((e) => e.tagName);
    T.check(c[0] + ": no element but <a> in the row", tags.every((t) => t === "A"), tags);
    const as = els.filter((e) => e.tagName === "A");
    T.check(c[0] + ": every link's href is '#' and its URL passes httpUrl()",
      as.every((a) => a.getAttribute("href") === "#" && w.httpUrl(a.getAttribute("data-url")) === a.getAttribute("data-url") &&
        /^https?:\/\//.test(a.getAttribute("data-url"))),
      as.map((a) => [a.getAttribute("href"), a.getAttribute("data-url")]));
    T.check(c[0] + ": no on* attribute anywhere", els.every((e) => Object.keys(e.attributes || {}).every((k) => !/^on/i.test(k)) &&
      !Array.from(e.getAttributeNames ? e.getAttributeNames() : []).some((k) => /^on/i.test(k))));
    if (c[2]) {
      T.check(c[0] + ": links are " + JSON.stringify(c[2]),
        JSON.stringify(as.map((a) => [a.textContent, a.getAttribute("data-url")])) === JSON.stringify(c[2]),
        as.map((a) => [a.textContent, a.getAttribute("data-url")]));
    }
    if (c[3] != null) {
      T.check(c[0] + ": visible text is exactly the escaped source minus link syntax",
        fx.textContent === c[3], fx.textContent);
    }
    if (c[0] === "5,000-character line") {
      T.check(c[0] + ": all of the text is there, one link made", fx.textContent.length > 4000 && as.length === 1,
        [fx.textContent.length, as.length]);
    }
  }

  // a click opens through window.open, after httpUrl(), with noopener
  w.__routes["/api/watchtower"].recent = [{ ts: 1789000000, label: "Brief",
    text: "Morning brief\n[S&P 500](https://finance.yahoo.com/quote/SPY)" }];
  await w.watchtowerPanel();
  const a = d.querySelector("#mind-extra-watchtower .wt-fire .wt-fx a");
  T.check("a link was rendered for the click test", !!a);
  if (a) {
    let prevented = false;
    const ev = new w.MouseEvent("click", { bubbles: true, cancelable: true });
    const orig = ev.preventDefault.bind(ev);
    ev.preventDefault = function () { prevented = true; orig(); };
    a.dispatchEvent(ev);
    T.check("click opens the URL in a new window with noopener",
      opened.length === 1 && opened[0][0] === "https://finance.yahoo.com/quote/SPY" &&
      opened[0][1] === "_blank" && /noopener/.test(opened[0][2] || ""), opened);
    T.check("click never navigates the app itself (default prevented)", prevented);
    // a data-url tampered after render is re-checked at click time
    opened.length = 0;
    a.setAttribute("data-url", "javascript:alert(1)");
    a.dispatchEvent(new w.MouseEvent("click", { bubbles: true, cancelable: true }));
    T.check("a non-http(s) data-url opens nothing", opened.length === 0, opened);
  }

  // without index.html's httpUrl() (it failed to load) nothing becomes a link
  delete w.httpUrl;
  await w.watchtowerPanel();
  const fx2 = d.querySelector("#mind-extra-watchtower .wt-fire .wt-fx");
  T.check("no httpUrl(): no link, the text stays escaped plain text",
    fx2 && fx2.querySelectorAll("a").length === 0 && /\[S&P 500\]\(https:/.test(fx2.textContent), fx2 && fx2.textContent);
  T.done();
})().catch((e) => { console.log("  FAIL threw " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
