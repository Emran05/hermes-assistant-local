// t_overlays_real.js — the REAL index.html's overlays and the GitHub review
// sheet (dashboard/aux_github.js, #gh-review) on the REAL Motion 14, GSAP 3 and
// anime 4, in headless Chromium, driven with REAL mouse and key events over
// the DevTools protocol (round-3 seams, the motion-overlays findings; the unit
// tier's twin is t_r3_seams.js).
//
// The unit tier runs the same code in a mini DOM on recording animation stubs,
// whose focus fixup and event order its author wrote. What is pinned here is
// what a real engine does with them:
//   * motion-overlays-1: the review sheet opened from the Needs-you pop-out's
//     Open (a real click on a real card), or by the deep link over an open
//     pop-out, keeps keyboard focus — the pop-out's ~180 ms exit used to keep
//     it an open modal to index.html's overlay watcher, which pulled focus
//     back into it and then handed it to the card BEHIND the sheet, where
//     Enter re-opened a pop-out nobody could see. OS reduced motion is the
//     control (the instant path never had the bug).
//   * motion-overlays-2: a chat turn that ends while the sheet is open (sendMsg
//     focuses the composer) used to move the keyboard to the composer BEHIND
//     the aria-modal sheet: what the owner typed next went there and Enter
//     POSTed /api/chat. Focus now goes back to the field they were typing in.
//   * motion-overlays-3: Esc gives focus back to the control that opened the
//     sheet with the KEYBOARD even when a repaint replaced it meanwhile (the
//     hub's refresh, the Settings card's poll) or it was hidden with the
//     pop-out it sat in — it used to land on <body>.
//
//   * Hermetic: no dashboard, no model, no real /api: this file serves the real
//     dashboard/index.html, the aux_*.js and vendor/ files from the repo, and
//     answers /api/* from tables, all on a 127.0.0.1 port of its own. No
//     GitHub: every /api/github answer is a table below.
//   * Needs, and SKIPs without (never fails for lack of them): the vendored
//     libraries, a Chromium (HERMES_CHROME, or the one Playwright installs
//     under ~/Library/Caches/ms-playwright or ~/.cache/ms-playwright) and a
//     node with a global WebSocket (22+). No Playwright needed.
//   * Asserts where focus IS (sampled every frame while the overlays settle,
//     and at the end), what is hidden, which requests went out — never a frame
//     budget or a pixel.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const cp = require("child_process");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const DASH = path.join(REPO, "dashboard");
const NEED = [
  "index.html", "aux_anim.js", "aux_github.js", "aux_needsyou.js",
  "vendor/motion/motion-14.6.0.min.js", "vendor/gsap/gsap-3.15.0.min.js", "vendor/gsap/Flip-3.15.0.min.js",
  "vendor/gsap/CustomEase-3.15.0.min.js", "vendor/anime/anime-4.5.0.umd.min.js"
];

let pass = 0, fail = 0;
function check(name, ok, extra) {
  if (ok) { pass++; console.log("  ok   " + name); } else { fail++; console.log("  FAIL " + name + (extra === undefined ? "" : "  <- " + (typeof extra === "string" ? extra : JSON.stringify(extra)))); }
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

// ---- the dashboard's API, as tables ------------------------------------------
const NOW = Math.floor(Date.now() / 1000);
const GH = "ghc-0123456789ab";
function ghRow(o) {
  return Object.assign({ id: GH, repo: "octo/app", private: true, branch: "hermes/fix-copy", slug: "fix-copy", mode: "new",
    state: "pending", title: "Fix the login copy", stat: { files: 1, add: 1, del: 1, bytes: 120 }, approvable: true,
    blocked: false, needs_ack: [], confirm_public: false, requested_at: NOW - 300, expires_at: NOW + 80000, decided_at: null,
    decision: null, pushed_sha: null, pr: { number: null, url: null, draft: true }, error: null, default_branch: "main",
    source: "api", retracted_at: null, review_url: "#github-review=" + GH }, o || {});
}
function ghFull() {
  return Object.assign(ghRow(), { body: "Fixes a typo.", checks: [],
    files: [{ path: "src/copy.js", old_path: null, status: "M", old_mode: "100644", new_mode: "100644", add: 1, del: 1, binary: false, size: 100 }],
    diff: "diff --git a/src/copy.js b/src/copy.js\n--- a/src/copy.js\n+++ b/src/copy.js\n@@ -1 +1 @@\n-a\n+b\n", diff_truncated: false,
    digest: "d".repeat(64), tree: "c".repeat(40), commit: "e".repeat(40), base: { branch: "main", sha: "b".repeat(40), at: NOW - 7200 },
    pr_footer: "Opened by Hermes.", sanitized: { comments_stripped: 0, mentions: 0, title_cut: false, body_cut: false, hidden_chars: 0 },
    target_ref: "refs/heads/hermes/fix-copy", author: { name: "octo", email: "x@users.noreply.github.com" } });
}
const GH_STATUS = { ok: true, connected: true, kind: "fine_grained_pat", login: "octo", user_id: 42, expires_at: NOW + 29 * 86400,
  expires_in_days: 29, no_expiry: false, writes_paused: false, paused: { by_owner_switch: false, switch: null, holds: [] },
  repos: [{ full_name: "octo/app", private: true, default_branch: "main", protection: { state: "protected", text: "main protected" },
    enabled: true, listed: true, writable: true, writes_ok: true, writes_blocked: null, sandbox: "/x", prepared: true, last_check: NOW - 3600 }],
  enabled: ["octo/app"], pending: 1, drift: [], attention: [], ambient: null, push_budget: { limit: 10, used: 0, left: 10 },
  check: { last: NOW - 1200, ok: true, error: null, next: NOW + 20000 }, ruleset: { recommended: {}, light: {}, path: "", text: "" },
  headline: "Hermes keeps your key in a file.", sandbox_root: "/x" };
function nyData() {
  return { building: false, now: [{ id: "github:" + GH, source: "github", title: "Fix the login copy", sender: "",
    summary: "octo/app hermes/fix-copy", reason: "A change is waiting for your decision.", bucket: "now", received_at: NOW - 400,
    deadline_ts: null, confidence: 0.9 }], today: [], later: [], never_count: 0, sources: { github: { present: true } },
    metrics: {}, generated_at: NOW, next_brief: null };
}
const IDS = ["needsyou", "system", "worldclock", "notes"];
function makeState() { return { posts: [], reqs: [] }; }
function api(st, method, u, query, body) {
  st.reqs.push(method + " " + u + (query ? "?" + query : ""));
  if (method === "POST") st.posts.push([u, body]);
  switch (u) {
    case "/api/hub": {
      const widgets = {};
      IDS.forEach((id, i) => { widgets[id] = { meta: { title: id === "needsyou" ? "Needs you" : "W " + id, size: i < 2 ? "wide" : "s", cat: id === "needsyou" ? "assistant" : "info" },
        data: id === "needsyou" ? nyData() : id === "system" ? { cpu_pct: 20, ram_pct: 50, disk_pct: 70 } : {} }; });
      return [200, { order: IDS.slice(), widgets, model_online: true }];
    }
    case "/api/catalog": return [200, { catalog: IDS.map((id) => ({ id, title: "W " + id, cat: "markets", size: "s" })) }];
    case "/api/sys": return [200, { cpu_pct: 33, ram_pct: 44, disk_pct: 55 }];
    case "/api/capabilities": return [200, { skills: { total: 1, categories: [], items: [] }, memory: { count: 0, facts: [] },
      insights: { sessions: 1, tool_calls: 1, messages: 1, input_tokens: 1, output_tokens: 1, by_day: [], platforms: [] } }];
    case "/api/needsyou": return [200, nyData()];
    case "/api/expand": return [200, /id=needsyou/.test(query) ? Object.assign({ rich: true }, nyData()) : { title: "x" }];
    case "/api/needsyou/act": return [200, (body || {}).action === "open" ? { ok: true, open: "#github-review=" + GH } : { ok: true }];
    case "/api/models": return [200, { models: [], active: "a/b", online: true }];
    case "/api/onboarding/state": return [200, { ok: true, done: true, sysinfo: {}, detect: {}, recommendation: {}, prefs: {} }];
    case "/api/github/status": return [200, GH_STATUS];
    case "/api/github/ambient": return [200, { ok: true, ambient: { checked_at: NOW, any: false, items: [] } }];
    case "/api/github/changes": {
      if (/(?:^|&)id=ghc-/.test(query || "")) return [200, { ok: true, change: ghFull() }];
      return [200, { ok: true, pending: 1, changes: [ghRow()] }];
    }
    case "/api/chat": st.chatT0 = Date.now(); return [200, { ok: true, job: "j1" }];
    case "/api/chat/poll": return [200, { ok: true, done: Date.now() - (st.chatT0 || 0) > (st.chatDelay || 2500), text: "Working on it",
      state: "running", persisted: true }];
    case "/api/actions": return [200, { actions: [] }];
    case "/api/access": return [200, { dirs: [] }];
    case "/api/history": return [200, { messages: [] }];
    case "/api/sessions": return [200, { sessions: [] }];
    default: return [200, {}];
  }
}
function serve(st) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const [u, query] = req.url.split("?");
      if (u === "/" || u === "/index.html") { res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" }); return res.end(fs.readFileSync(path.join(DASH, "index.html"))); }
      if (/^\/vendor\/(motion|gsap|anime)\/[A-Za-z0-9._-]+\.js$/.test(u) || /^\/(aux_[a-z_]+|expand)\.js$/.test(u)) {
        try { const b = fs.readFileSync(path.join(DASH, u)); res.writeHead(200, { "Content-Type": "application/javascript", "Cache-Control": "no-store" }); return res.end(b); } catch (e) {}
      }
      if (u === "/motion.min.js") { res.writeHead(200, { "Content-Type": "application/javascript" }); return res.end(fs.readFileSync(path.join(DASH, "vendor", "motion", "motion-14.6.0.min.js"))); }
      if (u.startsWith("/api/")) {
        let b = ""; req.on("data", (d) => (b += d));
        req.on("end", () => {
          let o = {}; try { o = JSON.parse(b || "{}"); } catch (e) {}
          const r = api(st, req.method, u, query || "", o);
          res.writeHead(r[0], { "Content-Type": "application/json" }); res.end(JSON.stringify(r[1]));
        });
        return;
      }
      res.writeHead(404); res.end();
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

// ---- DevTools protocol, minimal, with real input -----------------------------
async function openPage(chrome, base, opts) {
  opts = opts || {};
  const port = 9300 + Math.floor(Math.random() * 600);
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-ovl-chrome-"));
  // anything that is not loopback goes to a dead proxy: the page cannot reach the network
  const args = ["--no-sandbox", "--disable-gpu", "--no-first-run", "--remote-debugging-port=" + port, "--user-data-dir=" + prof,
    "--window-size=1400,1000", "--proxy-server=http://127.0.0.1:9"];
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
  let id = 0; const pend = {}; const errs = [];
  ws.addEventListener("message", (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pend[d.id]) { pend[d.id](d); delete pend[d.id]; }
    else if (d.method === "Runtime.exceptionThrown") {
      const x = d.params.exceptionDetails;
      errs.push(String((x.exception && x.exception.description) || x.text).split("\n")[0].slice(0, 240));
    }
  });
  const send = (method, params) => new Promise((r) => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable"); await send("Runtime.enable");
  if (opts.reduce) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await send("Page.navigate", { url: base + "/" });
  const ev = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result && r.result.exceptionDetails) throw new Error("page: " + JSON.stringify((r.result.exceptionDetails.exception && r.result.exceptionDetails.exception.description) || r.result.exceptionDetails.text));
    return r.result && r.result.result && r.result.result.value;
  };
  const KEYS = { Escape: [27, ""], Tab: [9, ""], Enter: [13, "\r"], " ": [32, " "] };
  const key = async (k, mods) => {
    const [vk, text] = KEYS[k];
    const base = { key: k, code: k === " " ? "Space" : k, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers: mods || 0 };
    await send("Input.dispatchKeyEvent", Object.assign({ type: text ? "keyDown" : "rawKeyDown" }, text ? { text } : {}, base));
    await send("Input.dispatchKeyEvent", Object.assign({ type: "keyUp" }, base));
  };
  const type = (text) => send("Input.insertText", { text });
  const click = async (sel) => {
    const xy = await ev("(()=>{const e=document.querySelector(" + JSON.stringify(sel) + ");if(!e)return null;e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()");
    if (!xy) throw new Error("click: no element " + sel);
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: xy[0], y: xy[1] });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: xy[0], y: xy[1], button: "left", clickCount: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: xy[0], y: xy[1], button: "left", clickCount: 1 });
  };
  const until = async (expr, ms) => {
    const t0 = Date.now();
    while (Date.now() - t0 < (ms || 5000)) { try { if (await ev(expr)) return true; } catch (e) {} await sleep(40); }
    return false;
  };
  const close = () => {
    try { ws.close(); } catch (e) {}
    try { proc.kill(); } catch (e) {}
    try { fs.rmSync(prof, { recursive: true, force: true }); } catch (e) {}
  };
  return { ev, key, type, click, until, close, errs };
}

// where focus is, as one word: "sheet" | "pop" | "body" | <a short description>
const WHERE = `(()=>{const a=document.activeElement,s=document.getElementById('gh-review'),p=document.getElementById('wpop');
  if(!a||a===document.body)return 'body';if(s&&s.contains(a))return 'sheet';if(p&&p.contains(a))return 'pop';
  return (a.id?'#'+a.id:a.tagName+'.'+String(a.className||'').split(' ')[0])+(a.closest('#wgrid')?' (hub)':'')})()`;
const STATE = `(()=>{const s=document.getElementById('gh-review'),p=document.getElementById('wpop');
  return {where:${WHERE},sheet:!!s&&!s.hidden,pop:!p.hidden}})()`;
// sample every frame for ms: [{t, where, sheet, pop}]
const TRACE = (ms) => `new Promise(res=>{const out=[],t0=performance.now();(function f(){const s=document.getElementById('gh-review'),p=document.getElementById('wpop');
  out.push({t:Math.round(performance.now()-t0),where:${WHERE},sheet:!!s&&!s.hidden,pop:!p.hidden});
  if(performance.now()-t0<${ms})requestAnimationFrame(f);else res(out);})();})`;

async function hubReady(b) {
  return b.until("document.querySelectorAll('#wgrid .w').length>=4 && !!document.querySelector('#wgrid .w[data-id=\"needsyou\"] .ny-row') && !!window.hermesGitHub && !!window.HermesAnim", 15000);
}
// back to a clean page between cases: no sheet, no pop-out, focus on <body>
async function reset(b) {
  await b.ev("(()=>{try{hermesGitHub.closeReview()}catch(e){};try{closePop()}catch(e){};return 1})()");
  await sleep(500);
  await b.ev("(()=>{if(location.hash)history.replaceState(null,'','/');if(document.activeElement)document.activeElement.blur();return 1})()");
}

async function overPopout(label, reduced, chrome, base, st) {
  console.log("[motion-overlays-1, " + label + "]");
  const b = await openPage(chrome, base, { reduce: reduced });
  try {
    check("the hub renders, with the Needs-you card's GitHub row", await hubReady(b));
    check("motion is " + (reduced ? "OFF (OS reduced motion)" : "live"),
      (await b.ev("HermesAnim.live()")) === !reduced, await b.ev("HermesAnim.live()"));

    // (a) a real click on the Needs-you card's title, then a real click on the
    // GitHub row's Open in the pop-out
    await b.click('#wgrid .w[data-id="needsyou"] .w-title');
    const popped = await b.until("!document.getElementById('wpop').hidden && !!document.querySelector('#wpop-sheet .ny-row button[data-a=\"open\"]')", 4000);
    check("Needs-you pop-out: a click on the card's title opens it, with the GitHub row's Open", popped);
    await sleep(400);
    const acts0 = st.posts.filter((p) => p[0] === "/api/needsyou/act").length;
    await b.click('#wpop-sheet .ny-row button[data-a="open"]');
    const tr = await b.ev(TRACE(900));
    const firstSheet = tr.findIndex((s) => s.sheet);
    const after = firstSheet < 0 ? [] : tr.slice(firstSheet).filter((s) => s.where !== "sheet");
    const end = await b.ev(STATE);
    check("...Open asked the server once and the review sheet opened", st.posts.filter((p) => p[0] === "/api/needsyou/act").length === acts0 + 1 && firstSheet >= 0, tr.slice(0, 3));
    check("...from the frame the sheet is on screen, focus is never outside it (not the pop-out, not the card behind)",
      after.length === 0, after.slice(0, 4));
    check("...and at the end: sheet open, pop-out hidden, focus in the sheet", end.sheet && !end.pop && end.where === "sheet", end);
    await b.key("Enter");
    await sleep(500);
    const ent = await b.ev(STATE);
    check("...Enter there opens no pop-out beneath the sheet", ent.sheet && !ent.pop && ent.where === "sheet", ent);
    await reset(b);

    // (b) the deep link over an open pop-out (the System card)
    await b.ev("openPop('system'),1");
    check("deep link: the System pop-out is open with focus in its ask field",
      await b.until("!document.getElementById('wpop').hidden && document.activeElement && document.activeElement.id==='pop-ask'", 3000));
    await sleep(300);
    await b.ev("(location.hash='#github-review=" + GH + "'),1");
    const tr2 = await b.ev(TRACE(900));
    const f2 = tr2.findIndex((s) => s.sheet);
    const after2 = f2 < 0 ? [] : tr2.slice(f2).filter((s) => s.where !== "sheet");
    const end2 = await b.ev(STATE);
    check("deep link: from the frame the sheet is on screen, focus stays in it", f2 >= 0 && after2.length === 0, after2.slice(0, 4));
    check("...and at the end: sheet open, pop-out hidden, focus in the sheet", end2.sheet && !end2.pop && end2.where === "sheet", end2);
    await b.key("Enter");
    await sleep(500);
    const ent2 = await b.ev(STATE);
    check("...Enter opens nothing beneath it either", ent2.sheet && !ent2.pop && ent2.where === "sheet", ent2);
    check("...and the pop-out was not left half-faded (no inline style)",
      (await b.ev("document.getElementById('wpop').style.cssText+document.getElementById('wpop-sheet').style.opacity")) === "",
      await b.ev("[document.getElementById('wpop').style.cssText,document.getElementById('wpop-sheet').style.cssText]"));
    await reset(b);
    check("no page error", b.errs.length === 0, b.errs.slice(0, 3));
  } finally { b.close(); }
}

async function chatTurnEnds(chrome, base, st) {
  console.log("[motion-overlays-2, a chat turn ends under the open sheet]");
  const b = await openPage(chrome, base, {});
  const chats = () => st.posts.filter((p) => p[0] === "/api/chat");
  try {
    check("the hub renders", await hubReady(b));
    await sleep(600);
    st.chatDelay = 3000;
    const n0 = chats().length;
    await b.click("#input");
    await b.type("summarise my day");
    await b.click("#send");
    check("a turn is running (one /api/chat POST)", await b.until("document.getElementById('send').disabled", 2000) && chats().length === n0 + 1);
    await sleep(300);
    await b.ev("(location.hash='#github-review=" + GH + "'),1");
    check("the review sheet opens over it", await b.until("(()=>{const s=document.getElementById('gh-review');return s&&!s.hidden&&!!s.querySelector('textarea[data-ghr-body]')})()", 3000));
    await sleep(400);
    await b.click("#gh-review textarea[data-ghr-body]");
    await b.type(" first line");
    check("(focus is in the PR description field)", (await b.ev("document.activeElement.hasAttribute('data-ghr-body')")) === true);
    const ended = await b.until("!document.getElementById('send').disabled", 6000);
    await sleep(300);
    const st1 = await b.ev(STATE);
    check("the turn ends (sendMsg focuses the composer): focus is still in the sheet, on the field",
      ended && st1.sheet && st1.where === "sheet" && (await b.ev("document.activeElement.hasAttribute('data-ghr-body')")) === true, st1);
    await b.type(" second");
    await b.key("Enter");
    await sleep(600);
    const v = await b.ev("[document.querySelector('#gh-review textarea[data-ghr-body]').value, document.getElementById('input').value]");
    check("...what they type next lands in the description, not in the hidden composer",
      /first line second\n?$/.test(v[0]) && v[1] === "", v);
    check("...and Enter POSTs no chat message from behind the sheet", chats().length === n0 + 1, chats().slice(n0).map((p) => p[1]));
    check("no page error", b.errs.length === 0, b.errs.slice(0, 3));
  } finally { b.close(); }
}

async function focusReturn(chrome, base, st) {
  console.log("[motion-overlays-3, Esc after a repaint replaced the opener]");
  const b = await openPage(chrome, base, {});
  const ROW = '.ny-row[data-id="github:' + GH + '"] button[data-a="open"]';
  const HUB_OPEN = '#wgrid .w[data-id="needsyou"] ' + ROW;
  const CARD_REVIEW = '#mind-extra-github button[data-gh="review"][data-id="' + GH + '"]';
  // which of the candidate controls holds focus: its selector, or a description
  const IS = (sel) => `(()=>{const a=document.activeElement,t=document.querySelector(${JSON.stringify(sel)});
    return a&&t&&a===t?'it':(!a||a===document.body?'body':(a.tagName+'.'+String(a.className||'').split(' ')[0]+(a.closest('#gh-review')?' (sheet)':''))) })()`;
  try {
    check("the hub renders, with the Needs-you card's GitHub row", await hubReady(b));
    await sleep(600);

    // (a) the hub card's Open, by keyboard; the hub's refresh re-renders the card
    await b.ev("(()=>{window.__old=document.querySelector(" + JSON.stringify(HUB_OPEN) + ");window.__old.focus();return 1})()");
    await b.key("Enter");
    check("hub row: Enter on Open opens the sheet, focus in it",
      await b.until("(()=>{const s=document.getElementById('gh-review');return s&&!s.hidden&&s.contains(document.activeElement)})()", 3000));
    await sleep(300);
    await b.ev("refreshHub().then(()=>1)");
    await sleep(300);
    check("...(the refresh replaced the card's Open button)", (await b.ev("window.__old.isConnected")) === false);
    await b.key("Escape");
    await sleep(700);
    check("...Esc: focus is on the NEW Open button of the same row, not <body>",
      (await b.ev("document.getElementById('gh-review').hidden")) === true && (await b.ev(IS(HUB_OPEN))) === "it", await b.ev(IS(HUB_OPEN)));

    // (b) the Settings card's Review, by keyboard; the card repaints itself
    await b.ev("hermesGitHub.openCard().then(()=>1)");
    check("settings card: Review is on screen", await b.until("(()=>{const x=document.querySelector(" + JSON.stringify(CARD_REVIEW) + ");return !!x&&x.getClientRects().length>0})()", 4000));
    await sleep(400);
    await b.ev("(()=>{window.__old=document.querySelector(" + JSON.stringify(CARD_REVIEW) + ");window.__old.focus();return 1})()");
    await b.key("Enter");
    check("...Enter on Review opens the sheet",
      await b.until("(()=>{const s=document.getElementById('gh-review');return s&&!s.hidden&&s.contains(document.activeElement)})()", 3000));
    await sleep(300);
    await b.ev("(hermesGitHub.paintCard(false),1)");
    check("...(the repaint replaced that button)", (await b.ev("window.__old.isConnected")) === false);
    await b.key("Escape");
    await sleep(700);
    check("...Esc: focus is on the NEW Review button for the same change, not <body>",
      (await b.ev(IS(CARD_REVIEW))) === "it", await b.ev(IS(CARD_REVIEW)));

    // (c) the Needs-you POP-OUT's Open: hidden with the pop-out, so the hub card's
    await b.ev("(setView('hub'),1)");
    await sleep(500);
    await b.ev("(openPop('needsyou'),1)");
    check("pop-out row: the Needs-you pop-out lists the change",
      await b.until("!!document.querySelector('#wpop-sheet " + ROW.replace(/"/g, '\\"') + "')", 4000));
    await sleep(400);
    await b.ev("(()=>{document.querySelector('#wpop-sheet " + ROW.replace(/"/g, '\\"') + "').focus();return 1})()");
    await b.key("Enter");
    check("...Enter on its Open opens the sheet and closes the pop-out",
      await b.until("(()=>{const s=document.getElementById('gh-review');return s&&!s.hidden&&s.contains(document.activeElement)&&document.getElementById('wpop').hidden})()", 3000));
    await sleep(300);
    await b.key("Escape");
    await sleep(700);
    check("...Esc: focus is on the hub card's Open for the same row, not <body>",
      (await b.ev(IS(HUB_OPEN))) === "it", await b.ev(IS(HUB_OPEN)));
    check("no page error", b.errs.length === 0, b.errs.slice(0, 3));
  } finally { b.close(); }
}

(async function main() {
  console.log("the review sheet over index.html's overlays, real Motion 14 / GSAP 3 / anime 4 (headless Chromium)");
  const missing = NEED.filter((f) => !fs.existsSync(path.join(DASH, f)));
  if (missing.length) skipAll("not all of dashboard/ is present (" + missing.join(", ") + ")");
  if (typeof WebSocket === "undefined") skipAll("this node has no global WebSocket (needs 22+)");
  const chrome = findChrome();
  if (!chrome) skipAll("no Chromium found (set HERMES_CHROME, or `playwright install chromium`)");
  const st = makeState();
  const srv = await serve(st);
  const base = "http://127.0.0.1:" + srv.address().port;
  try {
    console.log("  (chromium: " + path.basename(path.dirname(chrome)) + ")");
    for (const [label, reduced] of [["libraries live", false], ["OS reduced motion", true]]) {
      await overPopout(label, reduced, chrome, base, st);
    }
    await chatTurnEnds(chrome, base, st);
    await focusReturn(chrome, base, st);
  } catch (e) {
    check("suite ran", false, String(e && e.stack || e).split("\n").slice(0, 3).join(" | "));
  } finally {
    srv.close();
  }
  console.log("TESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
