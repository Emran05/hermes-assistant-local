// t_anim_hub_real.js — index.html's hub surfaces on the REAL Motion 14, GSAP 3
// (+ Flip) and anime 4, in headless Chromium (docs/plans/motion-system-2026-10.md
// §8.1-8.7, test plan §10.3).
//
// t_anim_hub.js (unit tier) drives the real index.html against recording stubs
// of the three libraries, which cannot show what a library really does to a
// page. Two review findings of cluster C3 were invisible to it and are pinned
// here, in a real engine:
//   * a Customize remove hid the leaving card with display:none, so the other
//     cards jumped into the gap BEFORE Flip measured and the Flip had nothing
//     to slide (a ~230 px jump, then no motion);
//   * anime 4.5's draw() leaves an inline `stroke-linecap: butt` on the path,
//     which beats the page's round caps for good (sparkline, swap check mark);
// next to the first C3 finding (GSAP folds a running CSS `rotate` jiggle into
// the Flip's state and leaves it inline as a permanent tilt).
//
//   * Hermetic: no dashboard, no model, no real /api: this file serves the real
//     dashboard/index.html, the aux_*.js and vendor/ files from the repo, and
//     answers /api/* from tables, all on a 127.0.0.1 port of its own.
//   * Needs, and SKIPs without (never fails for lack of them): the vendored
//     libraries, a Chromium (HERMES_CHROME, or the one Playwright installs
//     under ~/Library/Caches/ms-playwright or ~/.cache/ms-playwright) and a
//     node with a global WebSocket (22+). Drives the browser over the DevTools
//     protocol; no Playwright needed here.
//   * Checks end states, handles, what is left inline, positions between a
//     start and an end; never a frame budget or a pixel.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const cp = require("child_process");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const DASH = path.join(REPO, "dashboard");
const NEED = [
  "index.html", "aux_anim.js",
  "vendor/motion/motion-14.6.0.min.js", "vendor/gsap/gsap-3.15.0.min.js", "vendor/gsap/Flip-3.15.0.min.js",
  "vendor/gsap/CustomEase-3.15.0.min.js", "vendor/anime/anime-4.5.0.umd.min.js"
];

let pass = 0, fail = 0;
function check(name, ok, extra) {
  if (ok) { pass++; console.log("  ok   " + name); } else { fail++; console.log("  FAIL " + name + (extra === undefined ? "" : "  <- " + extra)); }
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
const IDS = ["system", "worldclock", "notes", "quicklinks", "a4", "a5", "a6", "a7", "a8", "a9", "a10", "a11", "a12", "a13", "a14", "a15"];
function makeState() { return { order: IDS.slice(0, 14), posts: [] }; }
function api(st, u, body) {
  const widgets = {};
  st.order.forEach((id, i) => { widgets[id] = { meta: { title: "W " + id, size: i % 5 === 0 ? "wide" : "s", cat: "info" }, data: id === "system" ? { cpu_pct: 20, ram_pct: 50, disk_pct: 70 } : {} }; });
  const days = []; for (let i = 0; i < 14; i++) days.push({ d: "2026-10-" + (10 + i), n: (i * 7) % 11 + 1 });
  switch (u) {
    case "/api/hub": return { order: st.order.slice(), widgets, model_online: true };
    case "/api/layout": {
      const o = body || {}; st.posts.push(o);
      if (o.op === "remove") st.order = st.order.filter((x) => x !== o.id);
      if (o.op === "add" && !st.order.includes(o.id)) st.order.push(o.id);
      if (o.op === "set" && Array.isArray(o.order)) st.order = o.order;
      return { ok: true };
    }
    case "/api/catalog": return { catalog: IDS.map((id) => ({ id, title: "W " + id, cat: "markets", size: "s" })) };
    case "/api/sys": return { cpu_pct: 33, ram_pct: 44, disk_pct: 55 };
    case "/api/capabilities": return { skills: { total: 12, categories: [], items: [] }, memory: { count: 3, facts: [] },
      insights: { sessions: 40, tool_calls: 1234, messages: 2500, input_tokens: 1500000, output_tokens: 800, by_day: days, platforms: [] } };
    case "/api/expand": return { title: "x" };
    case "/api/models": return { models: [], active: "a/b", online: true };
    default: return {};
  }
}
function serve(st) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const u = req.url.split("?")[0];
      const js = (o) => { res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(o)); };
      if (u === "/" || u === "/index.html") { res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" }); return res.end(fs.readFileSync(path.join(DASH, "index.html"))); }
      if (/^\/vendor\/(motion|gsap|anime)\/[A-Za-z0-9._-]+\.js$/.test(u) || /^\/(aux_[a-z_]+|expand)\.js$/.test(u)) {
        try { const b = fs.readFileSync(path.join(DASH, u)); res.writeHead(200, { "Content-Type": "application/javascript", "Cache-Control": "no-store" }); return res.end(b); } catch (e) {}
      }
      if (u === "/motion.min.js") { res.writeHead(200, { "Content-Type": "application/javascript" }); return res.end(fs.readFileSync(path.join(DASH, "vendor", "motion", "motion-14.6.0.min.js"))); }
      if (u.startsWith("/api/")) {
        if (req.method === "POST") { let b = ""; req.on("data", (d) => (b += d)); req.on("end", () => { let o = {}; try { o = JSON.parse(b); } catch (e) {} js(api(st, u, o)); }); return; }
        return js(api(st, u));
      }
      res.writeHead(404); res.end();
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

// ---- DevTools protocol, minimal ------------------------------------------------
async function openPage(chrome, base, reduceOS) {
  const port = 9300 + Math.floor(Math.random() * 600);
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), "hermes-hub-chrome-"));
  const args = ["--no-sandbox", "--disable-gpu", "--no-first-run", "--remote-debugging-port=" + port, "--user-data-dir=" + prof, "--window-size=1400,1000"];
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
  let id = 0; const pend = {};
  ws.addEventListener("message", (m) => { const d = JSON.parse(m.data); if (d.id && pend[d.id]) { pend[d.id](d); delete pend[d.id]; } });
  const send = (method, params) => new Promise((r) => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable"); await send("Runtime.enable");
  if (reduceOS) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await send("Page.navigate", { url: base + "/" });
  const ev = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result && r.result.exceptionDetails) throw new Error("page: " + JSON.stringify((r.result.exceptionDetails.exception && r.result.exceptionDetails.exception.description) || r.result.exceptionDetails.text));
    return r.result && r.result.result && r.result.result.value;
  };
  const close = () => {
    try { ws.close(); } catch (e) {}
    try { proc.kill(); } catch (e) {}
    try { fs.rmSync(prof, { recursive: true, force: true }); } catch (e) {}
  };
  return { ev, close };
}

// every hub card still carrying an inline style (a tilt, a transform, an opacity)
const DIRTY = "[...document.querySelectorAll('#wgrid .w')].filter(c=>c.style.cssText.trim()!=='').map(c=>c.dataset.id+':'+c.style.cssText.slice(0,80))";
const ORDER = "[...document.querySelectorAll('#wgrid .w')].map(c=>c.dataset.id).join()";

async function surfaces(label, reduced, chrome, base, st) {
  console.log("[" + label + "]");
  const b = await openPage(chrome, base, reduced);
  try {
    for (let i = 0; i < 80; i++) { if ((await b.ev("document.querySelectorAll('#wgrid .w').length")) >= 14) break; await sleep(250); }
    check("the hub renders its 14 cards", (await b.ev("document.querySelectorAll('#wgrid .w').length")) === 14);
    check("...each with a data-flip-id", await b.ev("[...document.querySelectorAll('#wgrid .w')].every(c=>c.getAttribute('data-flip-id')===c.dataset.id)"));
    await b.ev("HermesAnim.load('gsap').then(()=>HermesAnim.load('anime'))");
    await sleep(1400);
    check("after the boot entrance no card keeps an inline style", (await b.ev(DIRTY)).length === 0, await b.ev(DIRTY));

    // ---- Settings Overview: counts and the sparkline draw (the FIRST visit: the draw runs once per change in the data,
    // and every later visit rebuilds the sparkline whole)
    await b.ev("setView('mind'),1"); await sleep(1700);
    const sp = await b.ev(`(()=>{const p=document.querySelector('#spark path[stroke]');return {cap:getComputedStyle(p).strokeLinecap,inline:p.style.strokeLinecap,attrs:['pathLength','stroke-dasharray','stroke-dashoffset','draw'].filter(a=>p.hasAttribute(a))}})()`);
    check("the growth figures land on the real numbers", (await b.ev("['g-skills','g-mem','g-sessions','g-tools','a-msgs','a-in','a-out'].map(i=>document.getElementById(i).textContent).join('|')")) === "12|3|40|1,234|2.5k|1.5M|800");
    check("...and the aria-label they carried while counting is gone", (await b.ev("document.getElementById('g-tools').hasAttribute('aria-label')")) === false);
    check("the drawn sparkline is whole again (no dash attributes left) and keeps its ROUND line caps", sp.attrs.length === 0 && sp.cap === "round" && sp.inline === "", sp);
    await b.ev("setView('hub'),1");

    // ---- Customize on: in place, no rebuild
    const same = await b.ev("(()=>{window.__cards=[...document.querySelectorAll('#wgrid .w')];document.getElementById('edit-toggle').click();return 1})()");
    await sleep(300);
    check("Customize keeps the very same card nodes (no rebuild)", same === 1 && await b.ev("[...document.querySelectorAll('#wgrid .w')].every((c,i)=>c===window.__cards[i])"));
    check("...and the hub opts out of the view entrance while cards jiggle", (await b.ev("document.getElementById('view-hub').getAttribute('data-anim')")) === "off");

    // ---- Customize remove: the others SLIDE into the gap
    const rm = await b.ev(`(async()=>{
      const grid=document.getElementById('wgrid'), cards=[...grid.querySelectorAll('.w')];
      const victim=cards[1], probeId=cards[2].dataset.id, r0=cards[2].getBoundingClientRect();
      const xs=[];let go=true,t0=performance.now(),ctlMin=1;
      (function tick(){ if(!go)return; const c=grid.querySelector('.w[data-id="'+probeId+'"]'); if(c){xs.push(Math.round(c.getBoundingClientRect().left));
        const k=c.querySelector('.w-ctl');if(k)ctlMin=Math.min(ctlMin,+getComputedStyle(k).opacity);} requestAnimationFrame(tick); })();
      victim.querySelector('.rm').click();
      await new Promise(r=>setTimeout(r,1300)); go=false;
      const end=Math.round(grid.querySelector('.w[data-id="'+probeId+'"]').getBoundingClientRect().left);
      return {start:Math.round(r0.left),end,xs,ctlMin,victimGone:!victim.isConnected};
    })()`);
    const between = rm.xs.filter((x) => Math.abs(x - rm.start) > 3 && Math.abs(x - rm.end) > 3 && x > Math.min(rm.start, rm.end) && x < Math.max(rm.start, rm.end));
    check("remove: the layout POST went out", st.posts.some((p) => p.op === "remove" && p.id === "worldclock"), st.posts);
    check("remove: the card after it ends in the gap", rm.start !== rm.end && rm.victimGone, [rm.start, rm.end]);
    if (reduced) {
      check("remove, reduced: it lands at once, never in between", between.length === 0, between);
    } else {
      check("remove: the card after it SLIDES there (seen between its start and end x), not a jump", between.length >= 2, [rm.start, rm.end, rm.xs.filter((x, i) => i === 0 || x !== rm.xs[i - 1]).slice(0, 14)]);
    }
    check("remove: the other cards' remove buttons do not blink again when the grid is rebuilt (Customize's pop-in plays once)", rm.ctlMin === 1, rm.ctlMin);
    check("remove: every card is clean afterwards (no tilt, no translate/rotate/scale left inline)", (await b.ev(DIRTY)).length === 0, await b.ev(DIRTY));

    // ---- Alt+Arrow move keeps focus on the moved card
    const alt = await b.ev(`(async()=>{
      const t=document.querySelector('#wgrid .w .w-title');const id=t.closest('.w').dataset.id;t.focus();
      t.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',altKey:true,bubbles:true,cancelable:true}));
      await new Promise(r=>setTimeout(r,900));
      const a=document.activeElement;return {id,on:a&&a.closest('.w')&&a.closest('.w').dataset.id};
    })()`);
    check("Alt+Arrow: the layout POST is a move and focus stays on the card", st.posts.some((p) => p.op === "move") && alt.on === alt.id, [alt, st.posts.slice(-1)]);

    // ---- drag reflow (synthetic drag events): reorders, saves, leaves nothing behind
    const dr = await b.ev(`(async()=>{
      const g=document.getElementById('wgrid'),cards=()=>[...g.querySelectorAll('.w')],ids=()=>cards().map(c=>c.dataset.id).join();
      const src=cards()[0],before=ids(),dt=new DataTransfer();
      src.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:dt}));
      await new Promise(r=>setTimeout(r,80));
      const r=cards()[4].getBoundingClientRect();
      for(let i=0;i<6;i++){g.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,clientX:r.left+r.width*0.6,clientY:r.top+r.height*0.5,dataTransfer:dt}));await new Promise(r=>setTimeout(r,160));}
      src.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:dt}));
      await new Promise(r=>setTimeout(r,900));
      return {before,after:ids(),flipping:g.classList.contains('flipping')};
    })()`);
    check("drag: the grid re-slots around the cursor", dr.before !== dr.after, dr);
    check("...the drop saves the new order", st.posts.some((p) => p.op === "set" && p.order.join() === dr.after), st.posts.slice(-1));
    check("...the jiggle comes back (the grid is not left flipping) and no card keeps an inline style", dr.flipping === false && (await b.ev(DIRTY)).length === 0, await b.ev(DIRTY));

    // ---- add from the Widget Center
    await b.ev("document.getElementById('add-widget').click(),1");
    await sleep(500);
    const add = await b.ev(`(async()=>{
      const tile=[...document.querySelectorAll('#wc-grid .mcard')].find(x=>!x.classList.contains('on'));if(!tile)return null;
      const id=tile.dataset.id;tile.click();await new Promise(r=>setTimeout(r,1000));
      const n=document.querySelector('#wgrid .w[data-id="'+id+'"]');
      return {id,present:!!n,opacity:n?getComputedStyle(n).opacity:null,badge:tile.querySelector('.add').style.cssText};
    })()`);
    check("add: the new card is in the grid at full opacity", !!add && add.present && add.opacity === "1", add);
    check("...no card keeps an inline style, and the badge is handed back to CSS", (await b.ev(DIRTY)).length === 0 && add && add.badge === "", [await b.ev(DIRTY), add && add.badge]);
    await b.ev("document.getElementById('wc-close').click(),1");

    // ---- views and leaving Customize
    await b.ev("setView('mind'),1"); await sleep(700);
    await b.ev("setView('hub'),1"); await sleep(900);
    check("a view round trip while editing leaves the cards clean", (await b.ev(DIRTY)).length === 0, await b.ev(DIRTY));
    await b.ev("document.getElementById('edit-toggle').click(),1"); await sleep(500);
    check("leaving Customize gives the hub its view entrance back", (await b.ev("document.getElementById('view-hub').hasAttribute('data-anim')")) === false);
    await b.ev("setView('mind'),1"); await sleep(700);
    await b.ev("setView('hub'),1"); await sleep(900);
    check("...and a view round trip leaves the cards and the view clean",
      (await b.ev(DIRTY)).length === 0 && (await b.ev("document.getElementById('view-hub').style.cssText")) === "", await b.ev(DIRTY));

    // ---- model-swap overlay: ready, then fail, then a re-show mid-goodbye
    await b.ev("showModelSwap('Small 9B','Switching model'),1"); await sleep(700);
    const shown = await b.ev("(()=>{const o=document.getElementById('model-swap');return {hid:o.hidden,op:getComputedStyle(o).opacity,css:o.style.cssText,card:o.querySelector('.swapcard').style.cssText}})()");
    check("swap: the overlay is up, opaque, and nothing is left inline", !shown.hid && shown.op === "1" && shown.css === "" && shown.card === "", shown);
    await b.ev("hideModelSwap(true,'Small 9B'),1"); await sleep(700);
    const ready = await b.ev(`(()=>{const p=document.querySelector('#swap-ring path');return {title:document.getElementById('swap-title').textContent,
      cap:p?getComputedStyle(p).strokeLinecap:null,attrs:p?['pathLength','stroke-dasharray','stroke-dashoffset','draw'].filter(a=>p.hasAttribute(a)):null,hid:document.getElementById('model-swap').hidden}})()`);
    check("swap, ready: it reads Ready, the check mark is whole with round caps, the overlay is still up", ready.title === "Ready" && ready.cap === "round" && ready.attrs && ready.attrs.length === 0 && !ready.hid, ready);
    await sleep(1700);
    const gone = await b.ev("(()=>{const o=document.getElementById('model-swap');return {hid:o.hidden,css:o.style.cssText,busy:document.querySelector('.deck').hasAttribute('aria-busy')}})()");
    check("swap, ready: after its reading time (1.3 s, motion or not) it goes, clean, and the deck is no longer busy", gone.hid && gone.css === "" && !gone.busy, gone);
    await b.ev("showModelSwap('X','Switching model'),1"); await sleep(600);
    await b.ev("failModelSwap('nope'),1"); await sleep(3600);
    check("swap, fail: it shakes (unless reduced), reads for 2.6 s, then goes and leaves nothing inline", await b.ev("(()=>{const o=document.getElementById('model-swap');return o.hidden&&o.style.cssText===''&&o.querySelector('.swapcard').style.cssText===''})()"));
    await b.ev("showModelSwap('Y','Switching model'),hideModelSwap(true,'Y'),1"); await sleep(1750);
    await b.ev("showModelSwap('Z','Again'),1"); await sleep(3500);
    check("swap: a new switch during the goodbye keeps the overlay up (the old steps are retired)", await b.ev("(()=>{const o=document.getElementById('model-swap');return !o.hidden&&document.getElementById('swap-title').textContent==='Again'})()"));
    await b.ev("hideModelSwap(false),1"); await sleep(3300);

    // ---- pop-out: a reopen during the exit stays open and opaque
    await b.ev("openPop('system'),1"); await sleep(500);
    await b.ev("closePop(),1"); await sleep(60);
    await b.ev("openPop('system'),1"); await sleep(800);
    const pop = await b.ev("(()=>{const p=document.getElementById('wpop'),s=document.getElementById('wpop-sheet');return {hid:p.hidden,op:getComputedStyle(p).opacity,sop:getComputedStyle(s).opacity,css:p.style.cssText}})()");
    check("pop-out: a reopen mid-exit stays open at full opacity", !pop.hid && pop.op === "1" && pop.sop === "1" && pop.css === "", pop);
    await b.ev("closePop(),1"); await sleep(500);
    check("...and a plain close hides it, clean", await b.ev("(()=>{const p=document.getElementById('wpop');return p.hidden&&p.style.cssText===''})()"));

    // ---- model menu and toast
    await b.ev("document.getElementById('model-pill').click(),1"); await sleep(400);
    await b.ev("document.body.click(),1"); await sleep(60);
    await b.ev("document.getElementById('model-pill').click(),1"); await sleep(500);
    check("model menu: the pill during its close takes it back (it stays open)", await b.ev("(()=>{const m=document.getElementById('model-menu');return !m.hidden&&getComputedStyle(m).opacity==='1'})()"));
    await b.ev("document.body.click(),1"); await sleep(500);
    check("...and a click outside closes it, clean", await b.ev("(()=>{const m=document.getElementById('model-menu');return m.hidden&&m.style.cssText===''})()"));
    await b.ev("toast('first',3000),1"); await sleep(400);
    await b.ev("toast('second line',3000),1"); await sleep(600);
    const ts = await b.ev("(()=>{const t=document.getElementById('toast'),r=t.getBoundingClientRect();return {on:t.classList.contains('on'),cx:Math.round(r.left+r.width/2),vw:Math.round(innerWidth/2),op:getComputedStyle(t).opacity}})()");
    check("toast: a replaced toast stays centred (CSS translate composes with the nudge) and visible", ts.on && Math.abs(ts.cx - ts.vw) <= 2 && ts.op === "1", ts);
  } finally { b.close(); }
}

(async function main() {
  console.log("index.html hub surfaces against the real Motion 14, GSAP 3 and anime 4 (headless Chromium)");
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
      st.order = IDS.slice(0, 14); st.posts = [];
      await surfaces(label, reduced, chrome, base, st);
    }
  } finally { srv.close(); }
  console.log("TESTS " + pass + " passed " + fail + " failed");
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("FAIL crashed: " + (e && e.stack || e)); console.log("TESTS " + pass + " passed " + (fail + 1) + " failed"); process.exit(1); });
