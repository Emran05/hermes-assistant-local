// t_agent_rail_real.js — the Agent page's layout, measured in a real browser
// (companion to the unit suite tools/tests/unit/t_agent_rail_collapse.js,
// which pins the logic; only a layout engine can say how wide a row is).
//
// The owner's three complaints, as geometry:
//   * the Flight Recorder in the Record rail cut each target to 3-4
//     characters ("terminal ech…") and ran its header off the rail edge,
//     icon outdented past the filter row;
//   * the left sidebar's last conversation was sliced under the TOOLS heading;
//   * the chat card (max 860px, centred) was narrower than the status bar.
// Asserted here, in light and dark, at 1600 and 1290 CSS px (and at the
// owner's ~122% page zoom): every visible recorder row is two lines in the
// rail with its target at least ~60% of the rail's inner width; the header's
// right edge is inside the card and its icon lines up with the filter row;
// nothing below the conversation list overlaps it (Tools collapsed or open);
// .chatcol's left/right edges equal #agent-hero's (±1px); a wide card keeps
// the one-line row; real Enter/Space on a group header toggle it with focus
// staying put, through the rail's own 3 s poll; an owner's expand fades rows
// in through the real Motion runtime, and nothing does under reduced motion.
//
//   * Hermetic: the REAL dashboard/index.html and its aux files are served
//     from a 127.0.0.1 server this file starts; every /api/* answer is a
//     fixture (fetch is stubbed in the page, and the server answers /api/*
//     itself); every request for any other origin is failed by the DevTools
//     Fetch domain before it leaves the browser. No dashboard, no model.
//   * Needs, and SKIPs without (never fails for lack of them): a Chromium
//     (HERMES_CHROME, or the one Playwright installs), node 22+ (a global
//     WebSocket) and the vendored Motion file.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const cp = require("child_process");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const DASH = path.join(REPO, "dashboard");

let pass = 0, fail = 0;
function check(name, ok, extra) {
  if (ok) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra === undefined ? "" : "  <- " + (typeof extra === "string" ? extra : JSON.stringify(extra)))); }
}
function skipAll(why) { console.log("SKIP " + why); console.log("TESTS 0 passed 0 failed"); process.exit(0); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  if (process.env.HERMES_CHROME) return fs.existsSync(process.env.HERMES_CHROME) ? process.env.HERMES_CHROME : null;
  const roots = [path.join(os.homedir(), "Library", "Caches", "ms-playwright"), path.join(os.homedir(), ".cache", "ms-playwright")];
  const cands = [];
  roots.forEach((root) => {
    let names = [];
    try { names = fs.readdirSync(root); } catch (e) { return; }
    names.sort().reverse().forEach((n) => {
      const dir = path.join(root, n);
      if (/^chromium_headless_shell-/.test(n)) {
        try { fs.readdirSync(dir).forEach((s) => cands.push(path.join(dir, s, "chrome-headless-shell"))); } catch (e) {}
      } else if (/^chromium-/.test(n)) {
        try {
          fs.readdirSync(dir).forEach((s) => {
            cands.push(path.join(dir, s, "Chromium.app", "Contents", "MacOS", "Chromium"));
            cands.push(path.join(dir, s, "chrome"));
          });
        } catch (e) {}
      }
    });
  });
  return cands.find((c) => fs.existsSync(c)) || null;
}

// ---- fixtures: rows like the owner's rail, a long conversation list --------
const NOW = Math.floor(Date.now() / 1000);
function A(id, tool, target, kind, rev, ago, extra) {
  return Object.assign({ id: id, tool: tool, target: target, kind: kind, status: "done", reversible: rev, source: "agent", ts: NOW - ago }, extra || {});
}
const ACTIONS = [
  A(40, "clarify", "", "agent", "n/a", 60),
  A(39, "terminal", "echo \"hello from the agent\" > /tmp/hermes-check.txt", "shell", "no", 120),
  A(38, "terminal", "echo $PATH | tr ':' '\\n' | head", "shell", "no", 122),
  A(37, "terminal", "echo done && ls -la ~/Documents", "shell", "no", 124),
  A(36, "terminal", "echo $HOME", "shell", "no", 126),
  A(35, "terminal", "echo test > /tmp/a.txt", "shell", "no", 128),
  A(34, "terminal", "echo 1 2 3", "shell", "no", 130),
  A(33, "terminal", "curl -s http://127.0.0.1:7788/api/version", "shell", "no", 132),
  A(32, "write_file", "/Users/you/Documents/notes/meeting-2026-10-27.md", "write", "yes", 600),
  A(31, "terminal", "echo again", "shell", "no", 1380),
  A(30, "terminal", "echo and again", "shell", "no", 1385),
  A(29, "patch", "/Users/you/HermesAssistant/dashboard/aux_recorder.js", "write", "partial", 2400),
  A(28, "session_search", "what did we decide about the lease", "agent", "no", 2500),
  // long tool names in runs, with a chip and a span that crosses units: the header
  // must keep its x5 / x3 however long the name is
  A(58, "browser_click", "button#go-0", "other", "no", 4 * 3600), A(57, "browser_click", "button#go-1", "other", "no", 4 * 3600 + 60),
  A(56, "browser_click", "button#go-2", "other", "no", 4 * 3600 + 120), A(55, "browser_click", "button#go-3", "other", "no", 4 * 3600 + 180),
  A(54, "browser_click", "button#go-4", "other", "no", 4 * 3600 + 240),
  A(53, "browser_snapshot", "page 0", "other", "n/a", 5 * 3600), A(52, "browser_snapshot", "page 1", "other", "n/a", 5 * 3600 + 1200),
  A(51, "browser_snapshot", "page 2", "other", "n/a", 7 * 3600),
  A(27, "web_search", "weather in Boston this weekend", "net", "n/a", 50 * 86400),
  A(26, "web_search", "Ryan Routh sentencing", "net", "n/a", 50 * 86400 + 30),
  A(25, "web_extract", "", "net", "n/a", 50 * 86400 + 60),
  A(24, "terminal", "curl -s https://example.com/feed.xml | head -40", "shell", "no", 52 * 86400),
  A(23, "terminal", "ls -la /Users/you/Projects", "shell", "no", 52 * 86400 + 4),
  A(22, "terminal", "curl -I https://example.org", "shell", "no", 52 * 86400 + 8),
  A(21, "search_files", "/Users/you/Projects/hermes-assistant-local/dashboard", "read", "n/a", 52 * 86400 + 20),
  A(20, "write_file", "/tmp/x.txt", "write", "yes", 52 * 86400 + 40, { status: "undone" })
];
const SESSIONS = [];
for (let i = 0; i < 40; i++) SESSIONS.push({ id: "chat-" + i, title: ["Weather this weekend in Boston", "Draft the lease reply", "What changed in the recorder", "Plan my day"][i % 4] + " " + (i + 1), pinned: i < 2 });
const FIX = {
  "/api/recorder": { ok: true, recorder_ok: true, checkpoints_enabled: true, actions: ACTIONS, counts: { reversible: 2, undone: 1 } },
  "/api/recorder/retention": { ok: true, retain_days: 90, min_days: 14, total: 21 },
  "/api/onboarding/state": { ok: true, done: true, onboarded: true },
  "/api/sessions": { sessions: SESSIONS, total: 40 },
  "/api/actions": { actions: [{ label: "Plan my day", prompt: "x" }, { label: "Summarise my inbox", prompt: "x" }, { label: "What's on my calendar", prompt: "x" }, { label: "Catch me up on news", prompt: "x" }, { label: "Check the system", prompt: "x" }] }
};
function shim() {
  return `<script>
(function(){
  var q = new URLSearchParams(location.search);
  try { localStorage.clear(); localStorage.setItem("hermes_view","agent"); localStorage.setItem("hermes_theme", q.get("theme") || "dark"); } catch (e) {}
  var F = ${JSON.stringify(FIX)};
  window.fetch = function (u) {
    var p = String(u).replace(/^https?:\\/\\/[^/]+/, "").split("?")[0];
    var body = Object.prototype.hasOwnProperty.call(F, p) ? F[p] : { ok: true };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  };
})();
</script>`;
}
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json" };
function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let p = req.url.split("?")[0];
      if (p === "/") p = "/index.html";
      if (p.indexOf("/api/") === 0) { res.writeHead(200, { "Content-Type": "application/json" }); return res.end(JSON.stringify(FIX[p] || { ok: true })); }
      const f = path.join(DASH, p.replace(/^\/+/, ""));
      if (f.indexOf(DASH + path.sep) !== 0 || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end(); }
      let body = fs.readFileSync(f);
      if (p === "/index.html") body = Buffer.from(String(body).replace("<head>", "<head>" + shim()));
      res.writeHead(200, { "Content-Type": TYPES[path.extname(p)] || "application/octet-stream", "Cache-Control": "no-store" });
      res.end(body);
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

// ---- DevTools protocol, minimal ---------------------------------------------
async function openBrowser(chrome) {
  const port = 9300 + Math.floor(Math.random() * 600);
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-agentrail-chrome-"));
  const args = ["--no-sandbox", "--disable-gpu", "--no-first-run", "--hide-scrollbars", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", "--disable-default-apps", "--disable-extensions", "--metrics-recording-only",
    "--remote-debugging-port=" + port, "--user-data-dir=" + prof, "--window-size=1600,1000"];
  if (!/headless_shell|chrome-headless-shell/.test(chrome)) args.unshift("--headless=new");
  const proc = cp.spawn(chrome, args.concat(["about:blank"]), { stdio: "ignore" });
  let targets = null;
  for (let i = 0; i < 60 && !targets; i++) {
    try { const t = await (await fetch("http://127.0.0.1:" + port + "/json")).json(); if (t.length) targets = t; } catch (e) {}
    if (!targets) await sleep(200);
  }
  if (!targets) { try { proc.kill(); } catch (e) {} throw new Error("Chromium did not open its debugging port"); }
  const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.addEventListener("open", r); ws.addEventListener("error", j); });
  let id = 0; const pend = {}; const handlers = [];
  ws.addEventListener("message", (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pend[d.id]) { pend[d.id](d); delete pend[d.id]; }
    else if (d.method) handlers.forEach((h) => h(d));
  });
  const send = (method, params) => new Promise((r) => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
  return {
    send, onEvent: (h) => handlers.push(h),
    close: () => { try { ws.close(); } catch (e) {} try { proc.kill(); } catch (e) {} try { fs.rmSync(prof, { recursive: true, force: true }); } catch (e) {} }
  };
}
async function evalJSON(B, expr) {
  const r = await B.send("Runtime.evaluate", { expression: "(async function(){ return JSON.stringify(await (" + expr + ")); })()", awaitPromise: true, returnByValue: true });
  const v = r.result && r.result.result && r.result.result.value;
  if (v === undefined) throw new Error("evaluate failed: " + JSON.stringify(r.result && (r.result.exceptionDetails || r.result)).slice(0, 400));
  return JSON.parse(v);
}
async function key(B, k) {
  const code = k === " " ? "Space" : k;
  const vk = k === " " ? 32 : 13;
  await B.send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code: code, windowsVirtualKeyCode: vk, text: k === "Enter" ? "\r" : " " });
  await B.send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code: code, windowsVirtualKeyCode: vk });
}
async function load(B, base, o) {
  const z = o.zoom || 1;
  await B.send("Emulation.setDeviceMetricsOverride", { width: Math.round(o.w / z), height: Math.round(o.h / z), deviceScaleFactor: z, mobile: false });
  await B.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: o.theme }, { name: "prefers-reduced-motion", value: o.reduce ? "reduce" : "no-preference" }] });
  await B.send("Page.navigate", { url: base + "/?theme=" + o.theme });
  const t0 = Date.now();
  while (Date.now() - t0 < 20000) {
    await sleep(250);
    let ok = false;
    try { ok = await evalJSON(B, "document.querySelectorAll('#agent-rail #rec-list .rec-row').length > 3 && !!document.querySelector('#agent-stream .chatcol') && document.querySelectorAll('#cs-convos .cs-item').length > 10"); } catch (e) {}
    if (ok) { await sleep(500); return true; }
  }
  return false;
}

// everything measured in the page, in one place
const GEOM = `(function(){
  function R(e){ if(!e) return null; var b = e.getBoundingClientRect(); return {l:b.left, r:b.right, t:b.top, b:b.bottom, w:b.width, h:b.height}; }
  function shown(e){ return !!e && e.getClientRects().length > 0; }
  var rail = document.getElementById("agent-rail"), cs = rail && getComputedStyle(rail);
  var card = document.getElementById("recorder-card"), h2 = card && card.querySelector("h2");
  var o = {
    theme: document.documentElement.getAttribute("data-theme"),
    hero: R(document.getElementById("agent-hero")), chatcol: R(document.querySelector("#agent-stream .chatcol")),
    rail: R(rail), railInner: rail ? rail.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) : 0,
    card: R(card), h2: R(h2), h2over: h2 ? h2.scrollWidth - h2.clientWidth : 99,
    icon: R(h2 && h2.querySelector(".ic")), count: R(document.getElementById("rec-count")),
    filters: R(document.querySelector("#rec-filters .rec-fchip")),
    docOverflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    rows: [].filter.call(document.querySelectorAll("#rec-list .rec-row"), shown).map(function (r) {
      var t = r.querySelector(".rec-tgt"), n = r.querySelector(".rec-tool");
      return { id: r.getAttribute("data-id"), display: getComputedStyle(r).display, row: R(r),
        tgt: shown(t) ? R(t) : null, tool: R(n), toolCut: n.scrollWidth > n.clientWidth + 1 };
    }),
    convos: R(document.getElementById("cs-convos")),
    below: ["cs-tools-hd", "cs-tools", "cs-ai-hd", "cs-ai"].map(function (id) { var e = document.getElementById(id); return { id: id, r: shown(e) ? R(e) : null }; }),
    side: R(document.querySelector("#agent-side .chatside"))
  };
  return o;
})()`;

function layoutChecks(label, g) {
  check(label + ": the page is in the " + g.theme + " theme it asked for", !!g.theme);
  // E — the chat card fills the status bar's width
  check(label + ": .chatcol's left and right edges are #agent-hero's (±1px)",
    g.hero && g.chatcol && Math.abs(g.hero.l - g.chatcol.l) <= 1 && Math.abs(g.hero.r - g.chatcol.r) <= 1,
    g.hero && g.chatcol && { hero: [g.hero.l, g.hero.r], chatcol: [g.chatcol.l, g.chatcol.r] });
  check(label + ": no horizontal page scroll", g.docOverflowX <= 0, g.docOverflowX);
  // A — two lines in the rail, targets get the width
  const withT = g.rows.filter((r) => r.tgt);
  check(label + ": (setup) the rail shows recorder rows with targets", withT.length >= 4, g.rows.length);
  check(label + ": every visible row in the rail is the two-line grid", g.rows.every((r) => r.display === "grid"), g.rows.map((r) => r.display));
  check(label + ": each target sits on its own line, under the name", withT.every((r) => r.tgt.t >= r.tool.b - 2 && Math.abs(r.tgt.l - r.tool.l) <= 1),
    withT.map((r) => [r.id, Math.round(r.tool.l), Math.round(r.tgt.l), Math.round(r.tool.b), Math.round(r.tgt.t)]));
  const minFrac = Math.min.apply(null, withT.map((r) => r.tgt.w / g.railInner));
  check(label + ": no target is narrower than 60% of the rail's inner width (was 3-4 characters)", minFrac >= 0.6,
    { minFrac: +minFrac.toFixed(3), railInner: g.railInner, widths: withT.map((r) => Math.round(r.tgt.w)) });
  check(label + ": no tool name is cut", g.rows.every((r) => !r.toolCut), g.rows.filter((r) => r.toolCut).map((r) => r.id));
  check(label + ": every row stays inside the card", g.rows.every((r) => r.row.l >= g.card.l - 0.5 && r.row.r <= g.card.r + 0.5));
  // B — the header
  check(label + ": the header does not overflow (title + count fit, or the count wraps)", g.h2over <= 0, g.h2over);
  check(label + ": the header's right edge (and the count's) is inside the card", g.h2.r <= g.card.r + 0.5 && g.count.r <= g.card.r + 0.5, { h2: g.h2.r, count: g.count.r, card: g.card.r });
  check(label + ": the header icon lines up with the filter row (not outdented)", Math.abs(g.icon.l - g.filters.l) <= 1 && g.icon.l >= g.card.l, { icon: g.icon.l, filters: g.filters.l, card: g.card.l });
  // D — the sidebar
  if (g.convos && g.convos.h) {
    const below = g.below.filter((x) => x.r);
    check(label + ": nothing below the conversation list overlaps it", below.length >= 3 && below.every((x) => x.r.t >= g.convos.b - 0.5),
      { convosBottom: g.convos.b, below: below.map((x) => [x.id, x.r.t]) });
    check(label + ": the list keeps its floor (>= 160px)", g.convos.h >= 160, g.convos.h);
    check(label + ": everything in the sidebar fits inside it", below.every((x) => x.r.b <= g.side.b + 0.5), below.map((x) => [x.id, x.r.b, g.side.b]));
  }
}

(async function main() {
  console.log("The Agent page's rail, sidebar and chat width, in headless Chromium (the real index.html)");
  if (typeof WebSocket === "undefined") skipAll("this node has no global WebSocket (needs 22+)");
  if (!fs.existsSync(path.join(DASH, "vendor", "motion", "motion-14.6.0.min.js"))) skipAll("the vendored Motion file is not present");
  const chrome = findChrome();
  if (!chrome) skipAll("no Chromium found (set HERMES_CHROME, or `playwright install chromium`)");
  console.log("  (chromium: " + path.basename(path.dirname(chrome)) + ")");
  const srv = await serve();
  const base = "http://127.0.0.1:" + srv.address().port;
  const B = await openBrowser(chrome);
  const blocked = [];
  try {
    await B.send("Page.enable"); await B.send("Runtime.enable");
    // hermetic: anything not for our loopback server fails inside the browser
    await B.send("Fetch.enable", { patterns: [{ urlPattern: "*" }] });
    B.onEvent((ev) => {
      if (ev.method !== "Fetch.requestPaused") return;
      const u = ev.params.request.url;
      if (u.indexOf(base + "/") === 0 || /^(data|blob|about):/.test(u)) B.send("Fetch.continueRequest", { requestId: ev.params.requestId });
      else { blocked.push(u); B.send("Fetch.failRequest", { requestId: ev.params.requestId, errorReason: "BlockedByClient" }); }
    });

    for (const [w, theme] of [[1600, "dark"], [1290, "dark"], [1600, "light"], [1290, "light"]]) {
      const label = w + " " + theme;
      console.log("[" + label + "]");
      const ok = await load(B, base, { w: w, h: 1000, theme: theme });
      check(label + ": the Agent page loaded with the recorder in the rail", ok);
      if (!ok) continue;
      const g = await evalJSON(B, GEOM);
      check(label + ": the theme is " + theme, g.theme === theme, g.theme);
      layoutChecks(label, g);
      // Tools open too: still nothing over the list
      await evalJSON(B, "(function(){ var b = document.getElementById('cs-tools-hd'); if (b.getAttribute('aria-expanded') !== 'true') b.click(); return true; })()");
      await sleep(250);
      const g2 = await evalJSON(B, GEOM);
      const below = g2.below.filter((x) => x.r);
      check(label + ": with Tools open, nothing below the list overlaps it either", below.length === 4 && below.every((x) => x.r.t >= g2.convos.b - 0.5) && g2.convos.h >= 160,
        { convos: [g2.convos.t, g2.convos.b, g2.convos.h], below: below.map((x) => [x.id, x.r.t]) });
    }

    console.log("[the owner's ~122% zoom, dark]");
    if (await load(B, base, { w: 1720, h: 1050, theme: "dark", zoom: 1.22 })) layoutChecks("122% zoom", await evalJSON(B, GEOM));
    else check("122% zoom: loaded", false);

    console.log("[a wide card keeps the one-line row]");
    if (await load(B, base, { w: 1600, h: 1000, theme: "dark" })) {
      const wide = await evalJSON(B, `(async function(){
        document.getElementById("agent-rail").style.width = "900px";
        await new Promise(function (r) { requestAnimationFrame(function () { requestAnimationFrame(r); }); });
        var rows = [].filter.call(document.querySelectorAll("#rec-list .rec-row"), function (r) { return r.getClientRects().length; });
        var out = rows.map(function (r) { var t = r.querySelector(".rec-tgt"), n = r.querySelector(".rec-tool"); var a = t.getBoundingClientRect(), b = n.getBoundingClientRect();
          return { d: getComputedStyle(r).display, same: Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2) <= 3 && a.left > b.right }; });
        document.getElementById("agent-rail").style.width = "";
        return out;
      })()`);
      check("a 900px card: rows are the one-line flex row, target beside the name", wide.length >= 4 && wide.every((r) => r.d === "flex" && r.same), wide);
      // a rail narrower than the header's one-line width (WebKit's wider
      // system font put the owner's 296px rail there): the count wraps
      const narrow = await evalJSON(B, `(async function(){
        document.getElementById("agent-rail").style.width = "250px";
        await new Promise(function (r) { requestAnimationFrame(function () { requestAnimationFrame(r); }); });
        var card = document.getElementById("recorder-card"), h2 = card.querySelector("h2"), t = h2.querySelector(".rec-htitle"), c = document.getElementById("rec-count");
        var cb = card.getBoundingClientRect(), tb = t.getBoundingClientRect(), kb = c.getBoundingClientRect(), ib = h2.querySelector(".ic").getBoundingClientRect();
        var out = { over: h2.scrollWidth - h2.clientWidth, countR: kb.right, cardR: cb.right, wrapped: kb.top >= tb.bottom - 1, indent: Math.round(kb.left - tb.left), titleR: tb.right, iconL: ib.left, cardL: cb.left };
        document.getElementById("agent-rail").style.width = "";
        return out;
      })()`);
      check("a 250px rail: the header never clips — the count wraps under the title, aligned with its text, inside the card",
        narrow.over <= 0 && narrow.countR <= narrow.cardR + 0.5 && narrow.titleR <= narrow.cardR + 0.5 && narrow.wrapped && Math.abs(narrow.indent) <= 1 && narrow.iconL >= narrow.cardL, narrow);
    }

    console.log("[run headers: the name gives way, the count and time never do]");
    for (const rail of ["", "262px"]) {
      if (!(await load(B, base, { w: 1600, h: 1000, theme: "dark" }))) { check("loaded", false); continue; }
      const hd = await evalJSON(B, `(async function(){
        ${rail ? 'document.getElementById("agent-rail").style.width = "' + rail + '";' : ""}
        [].forEach.call(document.querySelectorAll("#rec-list .rec-dayhd[aria-expanded=false]"), function (b) { b.click(); });
        await new Promise(function (r) { requestAnimationFrame(function () { requestAnimationFrame(r); }); });
        var card = document.getElementById("recorder-card").getBoundingClientRect();
        return [].filter.call(document.querySelectorAll("#rec-list .rec-runhd"), function (h) { return h.getClientRects().length; }).map(function (h) {
          var n = h.querySelector(".rec-runname").getBoundingClientRect(), c = h.querySelector(".rec-runn").getBoundingClientRect(),
              w = h.querySelector(".rec-when").getBoundingClientRect(), t = h.querySelector(".rec-tool"), hb = h.getBoundingClientRect();
          return { name: h.getAttribute("aria-label").split(",")[0], countIn: c.right <= n.right + 0.5 && c.left >= n.left, whenIn: w.right <= hb.right + 0.5 && w.left >= n.right - 0.5,
            inCard: hb.left >= card.left - 0.5 && hb.right <= card.right + 0.5, over: h.scrollWidth - h.clientWidth, title: t.getAttribute("title"), cut: t.scrollWidth > t.clientWidth + 1, h: hb.height };
        });
      })()`);
      const tag = "a " + (rail || "320px") + " rail";
      check(tag + ": the long-name runs are on screen (browser_click, browser_snapshot)", hd.some((x) => x.name === "browser_click") && hd.some((x) => x.name === "browser_snapshot"), hd.map((x) => x.name));
      check(tag + ": every run's xN count sits inside its name box, whatever the name (it used to be pushed out of the clip)", hd.every((x) => x.countIn), hd.filter((x) => !x.countIn).map((x) => x.name));
      check(tag + ": every run's time sits inside the header, after the name, and the header never overflows", hd.every((x) => x.whenIn && x.inCard && x.over <= 0), hd.filter((x) => !(x.whenIn && x.inCard && x.over <= 0)));
      check(tag + ": a name that is cut carries its full text in a title; every run header is a 40px+ target", hd.every((x) => (!x.cut || x.title === x.name) && x.h >= 39.5), hd.map((x) => [x.name, x.cut, x.title, x.h]));
    }

    console.log("[the sidebar in a short window]");
    for (const [w, h, z] of [[1500, 520, 1], [1500, 640, 1.22]]) {
      for (const toolsOpen of [false, true]) {
        if (!(await load(B, base, { w: w, h: h, theme: "dark", zoom: z }))) { check("loaded", false); continue; }
        const g = await evalJSON(B, `(async function(){
          var b = document.getElementById("cs-tools-hd"); if ((b.getAttribute("aria-expanded") === "true") !== ${toolsOpen}) b.click();
          await new Promise(function (r) { requestAnimationFrame(function () { requestAnimationFrame(r); }); });
          function R(e){ var x = e.getBoundingClientRect(); return {t:x.top, b:x.bottom, h:x.height}; }
          return { side: R(document.querySelector("#agent-side .chatside")), convos: R(document.getElementById("cs-convos")),
            foot: R(document.querySelector("#chat-side .cs-foot")), aihd: R(document.getElementById("cs-ai-hd")), vh: innerHeight };
        })()`);
        const tag = "a " + g.vh + "px-tall window" + (toolsOpen ? " with Tools open" : "");
        check(tag + ": the Local AI block and the status foot stay inside the sidebar (the list's floor gives way to them)", g.foot.b <= g.side.b + 0.5 && g.aihd.b <= g.side.b + 0.5, g);
        check(tag + ": ...and the list still shows rows (>= 100px)", g.convos.h >= 100, g.convos.h);
      }
    }

    console.log("[real keys on a group header, through the rail's own poll]");
    if (await load(B, base, { w: 1600, h: 1000, theme: "dark" })) {
      await evalJSON(B, "(function(){ document.querySelector('#rec-list .rec-runhd').focus(); return true; })()");
      const st = () => evalJSON(B, "(function(){ var b = document.querySelector('#rec-list .rec-runhd'); return { exp: b.getAttribute('aria-expanded'), focus: document.activeElement === b, body: !document.getElementById(b.getAttribute('aria-controls')).hidden, n: b.querySelector('.rec-runn').textContent }; })()");
      const s0 = await st();
      check("(setup) the terminal run is folded and focused", s0.exp === "false" && s0.focus && !s0.body && s0.n === "×7", s0);
      await key(B, "Enter"); await sleep(150);
      const s1 = await st();
      check("Enter opens it (once — no double toggle), focus stays on the header", s1.exp === "true" && s1.body && s1.focus, s1);
      await sleep(3400);                                     // the rail's 3 s poll runs
      const s2 = await st();
      check("...the rail's poll keeps it open and focus on it", s2.exp === "true" && s2.body && s2.focus, s2);
      await key(B, " "); await sleep(150);
      const s3 = await st();
      check("Space closes it, focus stays", s3.exp === "false" && !s3.body && s3.focus, s3);
      const day = await evalJSON(B, "(function(){ var b = [].find.call(document.querySelectorAll('#rec-list .rec-dayhd'), function (x) { return x.getAttribute('aria-expanded') === 'false'; }); b.focus(); return b.getAttribute('aria-controls'); })()");
      await key(B, "Enter"); await sleep(60);
      const moving = await evalJSON(B, "(function(){ var box = document.getElementById(" + JSON.stringify(day) + "); return { open: !box.hidden, anims: box.getAnimations({ subtree: true }).length, focus: document.activeElement && document.activeElement.getAttribute('aria-controls') }; })()");
      check("Enter on a collapsed day opens it, and its revealed rows fade in through the real Motion runtime", moving.open && moving.anims > 0 && moving.focus === day, moving);
    }

    console.log("[OS reduced motion]");
    if (await load(B, base, { w: 1600, h: 1000, theme: "dark", reduce: true })) {
      const r = await evalJSON(B, `(async function(){
        var b = [].find.call(document.querySelectorAll("#rec-list .rec-dayhd"), function (x) { return x.getAttribute("aria-expanded") === "false"; });
        b.click();
        var box = document.getElementById(b.getAttribute("aria-controls"));
        var run = box.querySelector(".rec-runhd"); if (run) run.click();
        await new Promise(function (r) { requestAnimationFrame(r); });
        return { open: !box.hidden, anims: box.getAnimations({ subtree: true }).length,
          dirty: [].filter.call(box.querySelectorAll("*"), function (e) { return e.style && /opacity|transform/.test(e.style.cssText); }).length };
      })()`);
      check("expanding a day and a run starts no animation and leaves no inline style", r.open && r.anims === 0 && r.dirty === 0, r);
    }
    check("hermetic: no request left for any origin but this file's loopback server", blocked.length === 0, blocked.slice(0, 5));
  } finally { B.close(); srv.close(); }
  console.log("TESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS " + pass + " passed " + (fail + 1) + " failed"); process.exit(1); });
