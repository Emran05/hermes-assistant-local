// B18.1 (B18 follow-through, 2026-10-08 audit): the Needs-you "Open" button
// passed a stored item ref straight to window.open() behind a bare
// /^https?:/ prefix test, not the rule every other link in the shell uses
// (index.html httpUrl(): parse, require http:/https:, open the normalised
// href).  The app hands window.open's URL to NSWorkspace, so the rule is the
// guard.  The prefix test admitted strings the URL parser rejects
// ("https:javascript:alert(1)", "http:\njavascript:…") and opened the raw,
// untrimmed string.
//
// Renders the REAL dashboard/aux_needsyou.js in a vm sandbox, clicks Open
// through wire()'s real onclick, answers the act POST, and records what
// window.open / openPop received.  Offline, no DOM.  Against the pre-fix file
// the "refused" checks see window.open called with the raw ref.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = process.env.HERMES_REPO ||
             path.resolve(__dirname, "..", "..", "..");
const FILE = path.join(REPO, "dashboard", "aux_needsyou.js");
const fails = [];
let checks = 0;
function check(name, cond, extra) {
  checks++;
  console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : "  " + (extra || "")));
  if (!cond) fails.push(name);
}
const esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
function unesc(s) {
  return s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}
function host() {
  const h = { innerHTML: "", textContent: "", __btns: [] };
  h.querySelectorAll = function (sel) {
    if (sel !== "button[data-a]") return [];
    const out = [];
    const re = /<button\b([^>]*)>/g;
    let m;
    while ((m = re.exec(h.innerHTML))) {
      const attrs = m[1];
      const a = (attrs.match(/data-a="([^"]*)"/) || [])[1];
      if (!a) continue;
      const rows = h.innerHTML.slice(0, m.index).match(/data-id="([^"]*)"/g) || [];
      const id = rows.length ? unesc(rows[rows.length - 1].slice(9, -1)) : "";
      out.push({
        getAttribute: function (k) {
          if (k === "data-a") return a;
          const mm = attrs.match(new RegExp(k + '="([^"]*)"'));
          return mm ? unesc(mm[1]) : null;
        },
        closest: function () {
          return id ? { getAttribute: function () { return id; } } : null;
        }
      });
    }
    h.__btns = out;
    return out;
  };
  h.getClientRects = function () { return []; };
  return h;
}

function sandboxFor(answer, withShellHelper) {
  const opened = [], popped = [];
  const sb = {
    console, esc, URL, relTime: function () { return "2m ago"; },
    RENDER: {}, EXPAND_RENDER: {}, WICONS: {},
    setTimeout: function () { return 0; },
    openPop: function (t) { popped.push(t); },
    fetch: function (url) {
      if (url === "/api/needsyou/act") {
        return Promise.resolve({ json: function () { return Promise.resolve(answer); } });
      }
      return new Promise(function () {});
    },
  };
  sb.window = sb;
  sb.window.open = function (u, t, f) { opened.push([u, t, f]); };
  if (withShellHelper) {
    // index.html's httpUrl, verbatim, plus a spy
    sb.__httpUrlCalls = 0;
    sb.httpUrl = function (u) {
      sb.__httpUrlCalls++;
      try { const x = new URL(String(u || "").trim());
            return (x.protocol === "http:" || x.protocol === "https:") ? x.href : ""; }
      catch (e) { return ""; }
    };
  }
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(FILE, "utf8"), sb, { filename: FILE });
  return { sb: sb, opened: opened, popped: popped };
}

const ID = "watchtower:brk-1";
const payload = {
  ok: true, never_count: 0, metrics: {}, next_brief: null, generated_at: 1,
  now: [], later: [],
  today: [{ id: ID, source: "watchtower", title: "Fed cuts rates", sender: "Watchtower",
            bucket: "today", reason: "breaking", open: "framing" }],
};

async function clickOpen(ref, withShellHelper) {
  const env = sandboxFor({ ok: true, id: ID, action: "open", open: "framing", ref: ref },
                         withShellHelper);
  const w = host();
  env.sb.EXPAND_RENDER.needsyou(w, payload);
  const b = w.__btns.find(function (x) {
    return x.getAttribute("data-a") === "open" && x.closest() &&
      x.closest().getAttribute("data-id") === ID;
  });
  if (!b) return null;
  b.onclick({ preventDefault: function () {}, stopPropagation: function () {} });
  for (let i = 0; i < 5; i++) await new Promise(function (r) { setImmediate(r); });
  return env;
}

(async function () {
  console.log("=== B18.1: only an absolute http(s) URL is ever opened ===");
  for (const bad of ["https:javascript:alert(1)", "http:\njavascript:alert(1)",
                     "https:", "javascript:alert(1)", "file:///etc/passwd",
                     "x-apple.systempreferences:com.apple.preference"]) {
    for (const shell of [false, true]) {
      const env = await clickOpen(bad, shell);
      check("refused " + JSON.stringify(bad) + (shell ? " (shell httpUrl)" : ""),
            env && env.opened.length === 0, env && JSON.stringify(env.opened));
      check("...the pop-out opens instead" + (shell ? " (shell)" : ""),
            env && env.popped.length === 1 && env.popped[0] === "framing",
            env && JSON.stringify(env.popped));
    }
  }
  let env = await clickOpen("  https://example.test/a b  ", false);
  check("a real URL opens, normalised (trimmed, encoded)",
        env && env.opened.length === 1 &&
        env.opened[0][0] === "https://example.test/a%20b" &&
        env.opened[0][2] === "noopener", env && JSON.stringify(env.opened));
  env = await clickOpen("https://example.test/fed", true);
  check("with the shell present, its httpUrl() is the one used",
        env && env.sb.__httpUrlCalls === 1 && env.opened.length === 1 &&
        env.opened[0][0] === "https://example.test/fed",
        env && JSON.stringify([env.sb.__httpUrlCalls, env.opened]));

  console.log("TESTS " + (checks - fails.length) + " passed " + fails.length + " failed");
  process.exit(fails.length ? 1 : 0);
})();
