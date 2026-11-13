// B18 (UI half) — only http(s) reaches window.open, and a Recent Activity row
// reveals its file instead of opening it.
//
// window.open in the app shell goes to NSWorkspace.open, which opens ANY
// scheme with its default handler: a feed's file:///…/x.command or a saved
// x-apple…:/custom-app link ran on one click, and the Recent Activity pop-out
// built file:// URLs on purpose ("click a row to open in Finder" — it opened
// the file itself). Runs the REAL httpUrl()/wireLinks() from index.html and
// the REAL expand.js renderers against the DOM in _k14b_dom.js.
"use strict";
const fs = require("fs");
const path = require("path");
const D = require("./_k14b_dom.js");
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
let fails = [], checks = 0;
function check(name, cond, detail) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : " " + JSON.stringify(detail === undefined ? "" : detail)));
  if (!cond) fails.push(name);
}
const flush = () => new Promise((r) => setImmediate(r));
async function settle(n) { for (let i = 0; i < (n || 15); i++) await flush(); }

const HTML = fs.readFileSync(path.join(REPO, "dashboard", "index.html"), "utf8");
const EXPAND = fs.readFileSync(path.join(REPO, "dashboard", "expand.js"), "utf8");

function setup() {
  const env = D.makeEnv();
  const opened = [], posts = [], toasts = [];
  D.install(env, {
    fetch: async function (url, o) {
      const u = String(url).split("?")[0];
      if (o && o.method === "POST") posts.push({ url: u, body: o.body ? JSON.parse(o.body) : null });
      return { status: 200, ok: true, json: async () => ({ ok: true }) };
    },
    EXPAND_RENDER: {}, ICONS: {}, toast: function (m) { toasts.push(m); }
  });
  window.open = function (u) { opened.push(u); return null; };
  const fns = ["esc", "relTime", "icon", "barRows", "statGrid", "wireLinks"];
  let names = fns.slice();
  if (/\nfunction httpUrl\(/.test(HTML)) names.unshift("httpUrl");
  // eslint-disable-next-line no-eval
  (0, eval)(names.map((n) => D.extractFunction(HTML, n)).join("\n") +
    "\n;" + names.map((n) => "window." + n + "=" + n + ";").join(""));
  // eslint-disable-next-line no-eval
  (0, eval)(EXPAND);
  return { env, opened, posts, toasts };
}

async function main() {
  console.log("wireLinks: the one place every widget link opens");
  let s = setup();
  const box = document.createElement("div");
  document.body.appendChild(box);
  const URLS = ["https://example.com/a", "http://example.com/b", "file:///Users/you/run.command",
    "x-apple.systempreferences:com.apple.preference.security", "javascript:alert(1)",
    "obsidian://open?vault=x", "  https://example.com/c  ", "/api/foryou", "", "HTTPS://EXAMPLE.COM/D"];
  box.innerHTML = URLS.map((u) => '<a href="#" data-url="' + u.replace(/"/g, "&quot;") + '">x</a>').join("");
  wireLinks(box);
  box.querySelectorAll("a").forEach((a) => a.click());
  check("https and http open", s.opened.indexOf("https://example.com/a") >= 0 && s.opened.indexOf("http://example.com/b") >= 0, s.opened);
  check("an uppercase scheme still opens", s.opened.some((u) => /^https:\/\/example\.com\/D$/.test(u)), s.opened);
  check("surrounding whitespace is tolerated", s.opened.indexOf("https://example.com/c") >= 0, s.opened);
  check("nothing else reaches window.open (file:, x-apple, javascript:, app URLs, relative, empty)",
    s.opened.length === 4 && s.opened.every((u) => /^https?:\/\//i.test(u)), s.opened);

  console.log("Recent Activity pop-out: rows reveal, never open");
  s = setup();
  const el = document.createElement("div");
  document.body.appendChild(el);
  const now = Date.now() / 1000;
  EXPAND_RENDER.recent(el, { available: true, count: 2, total_size: 300, last_24h: 2, last_1h: 1,
    folder_count: 1, scanned: 40, window_h: 48, types: [{ ext: "command", count: 1, size: 100 }],
    groups: [{ parent: "/Users/you/Projects", name: "Projects", rel: "Projects", count: 2, size: 300,
      files: [{ path: "/Users/you/Projects/run.command", name: "run.command", ext: "command", size: 100, mtime: now - 60 },
              { path: "/Users/you/Projects/notes & stuff.md", name: "notes & stuff.md", ext: "md", size: 200, mtime: now - 90 }] }] });
  // the pop-out shell also runs wireLinks over the body (index.html openPop)
  wireLinks(el);
  check("no row carries a file:// URL", !/file:\/\//.test(el.innerHTML), el.innerHTML.match(/data-url="[^"]*"/g));
  const rows = el.querySelectorAll("a[data-reveal]");
  check("each file row is a reveal row", rows.length === 2, rows.length);
  rows[0].click();
  rows[1].click();
  await settle();
  check("a click asks the server to reveal that exact path",
    s.posts.length === 2 && s.posts[0].url === "/api/files/reveal" &&
    s.posts[0].body.path === "/Users/you/Projects/run.command" &&
    s.posts[1].body.path === "/Users/you/Projects/notes & stuff.md", s.posts);
  check("and nothing is window.open'ed", s.opened.length === 0, s.opened);
  check("the hint says Finder shows it", /show it in Finder/.test(el.textContent), el.textContent.slice(-80));

  console.log("Quick Links editor: Add refuses a scheme that would never open");
  s = setup();
  const ql = document.createElement("div");
  document.body.appendChild(ql);
  EXPAND_RENDER.quicklinks(ql, { links: [{ label: "GH", url: "https://github.com", domain: "github.com", mono: "G", hue: 1 }], suggestions: [] });
  function add(u) {
    ql.querySelector("#ql-url").value = u;
    ql.querySelector("#ql-lb").value = "L";
    ql.querySelector("#ql-add").click();
  }
  add("file:///Users/you/Documents");
  await settle();
  check("a file:// link is not saved", s.posts.length === 0, s.posts);
  check("the owner is told why", s.toasts.length === 1 && /http/.test(s.toasts[0]), s.toasts);
  add("obsidian://open?vault=x");
  await settle();
  check("nor an app URL", s.posts.length === 0, s.posts);
  add("example.com/docs");
  await settle();
  const saved = s.posts.length === 1 && s.posts[0].body.quicklinks;
  check("a bare domain is still added as https", !!saved && saved.length === 2 && saved[1].url === "https://example.com/docs", s.posts);
  ql.querySelectorAll("a[data-url]").forEach((a) => a.click());
  check("and its tile opens", s.opened.indexOf("https://example.com/docs") >= 0, s.opened);

  console.log("\nTESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
}
main().catch(function (e) {
  console.log("  FAIL crashed " + (e && e.stack || e));
  console.log("\nTESTS " + (checks - fails.length) + " passed " + (fails.length + 1) + " failed");
  process.exit(1);
});
