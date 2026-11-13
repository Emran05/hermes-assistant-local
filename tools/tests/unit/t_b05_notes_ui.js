// B05 + C19, the UI half — the REAL Scratchpad editors, headless in node.
//
//   * index.html's hub card (RENDER.notes) and expand.js's pop-out
//     (EXPAND_RENDER.notes) must send the revision they were loaded at as
//     base_rev, so the server can refuse a stale copy (B05);
//   * a 409 shows "Changed elsewhere" with Load latest / Keep mine and keeps
//     the typed text; a 413 shows the limit; a network failure says "Not
//     saved" (C19: the hub never even looked at the response);
//   * a pop-out save reaches the hub card at once (hermes:notes);
//   * refreshHub() never repaints a Scratchpad holding unsaved text.
//
// The renderers are extracted from the shipped files by brace matching and run
// against tools/tests/unit/_fakedom.js. No network, no dashboard, no browser.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { El, install, extract } = require("./_fakedom.js");

const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const DASH = path.join(REPO, "dashboard");
const HTML = fs.readFileSync(path.join(DASH, "index.html"), "utf8");
const EXPAND = fs.readFileSync(path.join(DASH, "expand.js"), "utf8");

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? "  <- " + JSON.stringify(extra) : "")); }
}
process.on("exit", () => { console.log("TESTS " + pass + " passed " + fail + " failed"); if (fail) process.exitCode = 1; });

function ctx() {
  const G = { console, Promise, JSON, Math, String, Number, Array, Object, Error, setImmediate };
  install(G);
  G.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
  G.icon = () => "";
  G.relTime = () => "now";
  vm.createContext(G);
  return G;
}
const last = (G, m) => G.__fetches.filter(f => f.method === m).slice(-1)[0];
// refreshHub() calls the motion runtime through HA() (docs/plans/motion-
// system-2026-10.md §6.7: its fills run inside HA().quiet). With no
// aux_anim.js loaded that is index.html's own ANIM_NULL, taken verbatim.
function animNull(src) {
  const a = src.indexOf("\nconst ANIM_NULL="), tail = "\nconst HA=()=>window.HermesAnim||ANIM_NULL;";
  const b = a < 0 ? -1 : src.indexOf(tail, a);
  if (b < 0) throw new Error("index.html's ANIM_NULL/HA() not found");
  return src.slice(a + 1, b + tail.length) + "\n";
}

async function hubSuite() {
  console.log("\n=== hub card (index.html RENDER.notes) ===");
  const src = extract(HTML, /\n {2}notes\(b,d\)\{/);
  const G = ctx();
  vm.runInContext("var RENDER={" + src + "};", G);
  const pads = [];
  G.document.querySelectorAll = sel => (sel === "#wgrid .notespad" ? pads : []);
  const b = new El("div");
  G.RENDER.notes(b, { text: "base", rev: 3, limit: 131072 });
  const ta = b.querySelector(".notespad");
  pads.push(ta);
  ok("renders the textarea with the stored text", ta && ta.value === "base", ta && ta.value);

  // a normal save carries base_rev
  G.__respond = c => ({ status: 200, body: { ok: true, rev: 4 } });
  ta.value = "base!"; ta.fire("input"); G.runTimers(); await G.flush();
  const p1 = last(G, "POST");
  ok("save POSTs /api/notes", p1 && p1.url === "/api/notes", p1);
  ok("save sends base_rev = the revision the card was loaded at (B05)",
     p1 && p1.body && p1.body.base_rev === 3 && p1.body.text === "base!", p1 && p1.body);
  ok("after ok the card holds the new rev", ta._rev === 4, ta._rev);
  ok("after ok the pad is clean", ta._dirty === false, ta._dirty);

  // the next save uses the rev the server returned
  G.__respond = c => ({ status: 200, body: { ok: true, rev: 5 } });
  ta.value = "base!!"; ta.fire("input"); G.runTimers(); await G.flush();
  ok("the following save sends the returned rev", last(G, "POST").body.base_rev === 4, last(G, "POST").body);

  // 409: the pop-out changed it — refused, shown, text kept
  G.__respond = c => ({ status: 409, body: { ok: false, conflict: true, text: "base + popout paragraph", rev: 9 } });
  ta.value = "stale!"; ta.fire("input"); G.runTimers(); await G.flush();
  const st = b.querySelector(".notes-st");
  ok("conflict is shown, not swallowed", st && !st.hidden && /Changed elsewhere/.test(st.textContent), st && st.textContent);
  const btns = st.querySelectorAll("button");
  ok("conflict offers Load latest + Keep mine", btns.length === 2 &&
     btns[0].textContent === "Load latest" && btns[1].textContent === "Keep mine", btns.map(x => x.textContent));
  ok("typed text is kept on a conflict", ta.value === "stale!", ta.value);
  ok("a refused pad stays dirty (refreshHub must not repaint it)", ta._dirty === true, ta._dirty);
  btns[0].click();
  ok("Load latest adopts the server text + rev", ta.value === "base + popout paragraph" && ta._rev === 9,
     [ta.value, ta._rev]);
  ok("Load latest clears the notice", st.hidden === true, st.textContent);

  // Keep mine re-sends against the server's rev (a deliberate overwrite)
  G.__respond = c => ({ status: 409, body: { ok: false, conflict: true, text: "other", rev: 11 } });
  ta.value = "mine"; ta.fire("input"); G.runTimers(); await G.flush();
  G.__respond = c => ({ status: 200, body: { ok: true, rev: 12 } });
  b.querySelector(".notes-st").querySelectorAll("button")[1].click(); await G.flush();
  ok("Keep mine re-saves with base_rev = the conflicting rev",
     last(G, "POST").body.base_rev === 11 && last(G, "POST").body.text === "mine", last(G, "POST").body);
  ok("Keep mine lands", ta._rev === 12 && ta._dirty === false, [ta._rev, ta._dirty]);

  // 413 (C19)
  G.__respond = c => ({ status: 413, body: { ok: false, error: "too_long", limit: 131072, chars: 200000 } });
  ta.value = "x".repeat(10); ta.fire("input"); G.runTimers(); await G.flush();
  ok("413 says Too long and the limit", /Too long/.test(st.textContent) && /131,072/.test(st.textContent), st.textContent);
  ok("413 leaves the pad dirty", ta._dirty === true);

  // network failure (C19: the response used to be ignored entirely)
  G.__respond = c => ({ reject: true });
  ta.value = "offline"; ta.fire("input"); G.runTimers(); await G.flush();
  ok("unreachable dashboard says Not saved", /Not saved/.test(st.textContent), st.textContent);

  // one save in flight at a time: a second edit while busy does not race
  let release;
  G.__respond = c => ({ status: 200, body: { ok: true, rev: 20 } });
  const realFetch = G.fetch;
  G.fetch = (u, o) => { G.fetch = realFetch; return new Promise(r => { release = () => r(realFetch(u, o)); }); };
  ta.value = "one"; ta.fire("input"); G.runTimers();
  ta.value = "one two"; ta.fire("input"); G.runTimers();
  release(); await G.flush(); await G.flush();
  ok("an edit made during an in-flight save is sent after it, with the new rev",
     last(G, "POST").body.text === "one two" && last(G, "POST").body.base_rev === 20, last(G, "POST").body);

  // blur flushes a pending save at once
  G.__respond = c => ({ status: 200, body: { ok: true, rev: 30 } });
  const n0 = G.__fetches.length;
  ta.value = "blurred"; ta.fire("input"); ta.fire("blur"); await G.flush();
  ok("blur flushes the pending save", G.__fetches.length === n0 + 1 && last(G, "POST").body.text === "blurred",
     G.__fetches.length - n0);

  // the pop-out's save event reaches a clean card immediately
  G.dispatchEvent(new G.CustomEvent("hermes:notes", { detail: { text: "from the pop-out", rev: 31 } }));
  ok("hermes:notes updates a clean hub card", ta.value === "from the pop-out" && ta._rev === 31, [ta.value, ta._rev]);
  ta._dirty = true;
  G.dispatchEvent(new G.CustomEvent("hermes:notes", { detail: { text: "later", rev: 32 } }));
  ok("hermes:notes never overwrites unsaved hub text", ta.value === "from the pop-out", ta.value);
  ta._dirty = false;
  G.dispatchEvent(new G.CustomEvent("hermes:notes", { detail: { text: "older", rev: 20 } }));
  ok("hermes:notes older than the card's rev is ignored", ta.value === "from the pop-out" && ta._rev === 31,
     [ta.value, ta._rev]);
}

async function rerenderSuite() {
  // renderHub() rebuilds the whole grid after every chat turn (and on any
  // layout change), which calls RENDER.notes again on a NEW element. An edit
  // that is not on disk must survive that, and so must an in-flight save.
  console.log("\n=== hub card survives a full re-render (renderHub after a chat turn) ===");
  const src = extract(HTML, /\n {2}notes\(b,d\)\{/);
  const G = ctx();
  vm.runInContext("var RENDER={" + src + "};", G);
  const render = data => { const b = new El("div"); G.RENDER.notes(b, data); return b; };

  // a refused (413) paste, then a re-render from /api/hub's older copy
  let b = render({ text: "server", rev: 3, limit: 131072 });
  let ta = b.querySelector(".notespad");
  G.__respond = () => ({ status: 413, body: { ok: false, error: "too_long", limit: 131072 } });
  ta.value = "my long paste"; ta.fire("input"); G.runTimers(); await G.flush();
  b = render({ text: "server", rev: 3, limit: 131072 });
  ta = b.querySelector(".notespad");
  ok("refused text survives a re-render", ta.value === "my long paste" && ta._dirty === true, [ta.value, ta._dirty]);
  ok("... and so does its Too long notice", /Too long/.test(b.querySelector(".notes-st").textContent)
     && b.querySelector(".notes-st").hidden === false, b.querySelector(".notes-st").textContent);
  G.__respond = () => ({ status: 200, body: { ok: true, rev: 4 } });
  ta.value = "short"; ta.fire("input"); G.runTimers(); await G.flush();
  ok("fixing it saves from the new pad, against the base it was edited from",
     last(G, "POST").body.text === "short" && last(G, "POST").body.base_rev === 3, last(G, "POST").body);
  ok("... and the new pad is clean at the new rev", ta._dirty === false && ta._rev === 4, [ta._dirty, ta._rev]);
  b = render({ text: "short", rev: 4 });
  ok("a clean pad re-renders from the server copy, notice gone",
     b.querySelector(".notespad").value === "short" && b.querySelector(".notes-st").hidden === true);

  // a save still pending when the re-render lands
  ta = b.querySelector(".notespad");
  ta.value = "short + typed"; ta.fire("input");          // debounce timer pending
  const b2 = render({ text: "short", rev: 4 });          // /api/hub answered before the save
  const ta2 = b2.querySelector(".notespad");
  ok("the new pad keeps the typed text, not the older server copy", ta2.value === "short + typed", ta2.value);
  G.__respond = () => ({ status: 200, body: { ok: true, rev: 5 } });
  G.runTimers(); await G.flush();
  ok("the pending save lands", last(G, "POST").body.text === "short + typed" && last(G, "POST").body.base_rev === 4,
     last(G, "POST").body);
  ok("... and the NEW pad learns its rev (no false conflict on the next edit)", ta2._rev === 5 && ta2._dirty === false,
     [ta2._rev, ta2._dirty]);

  // a conflict notice and its buttons carry over and act on the new pad
  G.__respond = () => ({ status: 409, body: { ok: false, conflict: true, text: "from the pop-out", rev: 8 } });
  ta2.value = "stale"; ta2.fire("input"); G.runTimers(); await G.flush();
  const b3 = render({ text: "from the pop-out", rev: 8 });
  const ta3 = b3.querySelector(".notespad"), st3 = b3.querySelector(".notes-st");
  ok("a conflict survives a re-render with both buttons", ta3.value === "stale" && /Changed elsewhere/.test(st3.textContent)
     && st3.querySelectorAll("button").length === 2, [ta3.value, st3.textContent]);
  st3.querySelectorAll("button")[0].click();
  ok("Load latest acts on the pad on screen", ta3.value === "from the pop-out" && ta3._rev === 8 && ta3._dirty === false,
     [ta3.value, ta3._rev, ta3._dirty]);

  // openPop() falls back to fillWidget -> RENDER.notes inside #wpop when
  // /api/expand fails: that second pad must not take over the hub card's editor
  G.__respond = () => ({ status: 413, body: { ok: false, error: "too_long", limit: 131072 } });
  ta3.value = "hub draft"; ta3.fire("input"); G.runTimers(); await G.flush();
  const pop = new El("div"); pop.closest = sel => (sel === "#wpop" ? {} : null);
  G.RENDER.notes(pop, { text: "from the pop-out", rev: 8 });
  const pta = pop.querySelector(".notespad");
  ok("a fallback pop-out pad starts from its own data, not the hub's draft",
     pta.value === "from the pop-out" && pta._dirty === false, [pta.value, pta._dirty]);
  G.__respond = () => ({ status: 200, body: { ok: true, rev: 9 } });
  pta.value = "typed in the pop-out"; pta.fire("input"); G.runTimers(); await G.flush();
  ok("... saves its own text", last(G, "POST").body.text === "typed in the pop-out", last(G, "POST").body);
  G.__respond = () => ({ status: 409, body: { ok: false, conflict: true, text: "typed in the pop-out", rev: 9 } });
  ta3.value = "hub draft, edited"; ta3.fire("input"); G.runTimers(); await G.flush();
  ok("... and the hub card still saves ITS text, against its own base",
     last(G, "POST").body.text === "hub draft, edited" && last(G, "POST").body.base_rev === 8, last(G, "POST").body);
}

async function refreshSuite() {
  console.log("\n=== refreshHub() never repaints a dirty Scratchpad ===");
  const src = extract(HTML, /\nasync function refreshHub\(\)\{/);
  const G = ctx();
  const painted = [];
  const cards = {};
  ["notes", "clock"].forEach(id => {
    const el = new El("div"); el.dataset.id = id;
    const body = new El("div"); body.className = "w-body"; el.appendChild(body);
    const meta = new El("span"); meta.className = "meta"; el.appendChild(meta);
    if (id === "notes") { const pad = new El("textarea"); pad.className = "notespad"; pad._dirty = true; body.appendChild(pad); }
    cards[id] = el;
  });
  const grid = new El("div");
  grid.querySelector = sel => { const m = /data-id="([^"]+)"/.exec(sel); return m ? cards[m[1]] || null : null; };
  G.$ = id => (id === "wgrid" ? grid : id === "wcenter" ? { hidden: true } : new El("div"));
  G.hubEditing = false; G.hubOrder = ["notes", "clock"];
  G.setModel = () => {}; G.renderHub = () => painted.push("RENDERHUB");
  G.fillWidget = (body, meta, id) => painted.push(id);
  G.__respond = () => ({ status: 200, body: { order: ["notes", "clock"], model_online: false,
                                              widgets: { notes: { data: { text: "server", rev: 1 } }, clock: { data: {} } } } });
  vm.runInContext(animNull(HTML) + "var hubEditing=false,hubOrder=['notes','clock'];" + src, G);
  await G.refreshHub(); await G.flush();
  ok("the clean widget is refreshed", painted.indexOf("clock") >= 0, painted);
  ok("the dirty Scratchpad is NOT repainted (B05)", painted.indexOf("notes") < 0, painted);
  // clean pad that already saved rev 5: a /api/hub reply built before that
  // save (rev 4) must not paint the old text back; a newer one (rev 6) must
  const pad = cards.notes.querySelector(".notespad");
  pad._dirty = false; pad._rev = 5;
  const reply = rev => () => ({ status: 200, body: { order: ["notes", "clock"], model_online: false,
    widgets: { notes: { data: { text: "server", rev } }, clock: { data: {} } } } });
  painted.length = 0; G.__respond = reply(4);
  await G.refreshHub(); await G.flush();
  ok("a reply older than the card's last save is not painted", painted.indexOf("notes") < 0, painted);
  painted.length = 0; G.__respond = reply(6);
  await G.refreshHub(); await G.flush();
  ok("a newer reply repaints the clean card", painted.indexOf("notes") >= 0, painted);
}

async function popoutSuite() {
  console.log("\n=== pop-out (expand.js EXPAND_RENDER.notes) ===");
  const src = extract(EXPAND, /\nEXPAND_RENDER\.notes=function\(el,data\)\{/);
  const G = ctx();
  const events = [];
  G.addEventListener("hermes:notes", e => events.push(e.detail));
  G.__respond = c => (c.method === "GET" ? { status: 200, body: { text: "base", rev: 3, limit: 131072 } }
                                         : { status: 200, body: { ok: true, rev: 4 } });
  vm.runInContext("var EXPAND_RENDER={};" + src.replace(/^\n/, ""), G);
  const el = new El("div");
  G.EXPAND_RENDER.notes(el, { text: "base", apple: {} });
  await G.flush();
  const ta = el.querySelector("#nt-ta"), stxt = el.querySelector("#nt-savetxt"), sEl = el.querySelector("#nt-save");
  ok("pop-out loads its base revision from GET /api/notes",
     G.__fetches.some(f => f.method === "GET" && f.url === "/api/notes"), G.__fetches);
  ta.value = "base + popout paragraph"; ta.fire("input"); G.runTimers(); await G.flush();
  const p = last(G, "POST");
  ok("pop-out save sends base_rev (B05)", p && p.body.base_rev === 3 && p.body.text === "base + popout paragraph", p && p.body);
  ok("pop-out says Saved on ok", stxt.textContent === "Saved", stxt.textContent);
  ok("pop-out announces the save to the hub card", events.length === 1 && events[0].rev === 4 &&
     events[0].text === "base + popout paragraph", events);

  G.__respond = c => ({ status: 409, body: { ok: false, conflict: true, text: "hub text", rev: 7 } });
  ta.value = "stale pop-out"; ta.fire("input"); G.runTimers(); await G.flush();
  ok("409 shows Changed elsewhere, not Saved", /Changed elsewhere/.test(stxt.textContent), stxt.textContent);
  const bs = sEl.querySelectorAll("button");
  ok("409 offers Load latest + Keep mine", bs.length === 2, bs.map(x => x.textContent));
  ta.value = "stale pop-out, still typing"; ta.fire("input");
  ok("the conflict stays on screen while typing", /Changed elsewhere/.test(stxt.textContent), stxt.textContent);
  G.runTimers(); await G.flush();
  ok("no save is sent while the conflict is unresolved", last(G, "POST").body.text === "stale pop-out", last(G, "POST").body);
  bs[0].click();
  ok("Load latest adopts the server copy", ta.value === "hub text" && stxt.textContent === "Saved", [ta.value, stxt.textContent]);

  G.__respond = c => ({ status: 413, body: { ok: false, error: "too_long", limit: 131072 } });
  ta.value = "too much"; ta.fire("input"); G.runTimers(); await G.flush();
  ok("413 shows Too long with the limit (C19)", /Too long/.test(stxt.textContent) && /131,072/.test(stxt.textContent), stxt.textContent);

  // the counter shows the limit near it and goes red past it
  ta.value = "y".repeat(131073); ta.fire("input");
  const chars = el.querySelector("#nt-chars");
  ok("counter shows N / limit past 90%", /131,073 \/ 131,072/.test(chars.textContent), chars.textContent);
  ok("counter turns red past the limit", chars.style.color === "var(--bad)", chars.style.color);
}

async function legacySuite() {
  console.log("\n=== pop-out against a dashboard without GET /api/notes (not restarted yet) ===");
  const src = extract(EXPAND, /\nEXPAND_RENDER\.notes=function\(el,data\)\{/);
  const G = ctx();
  G.__respond = c => (c.method === "GET" ? { status: 404, body: null } : { status: 200, body: { ok: true } });
  vm.runInContext("var EXPAND_RENDER={};" + src.replace(/^\n/, ""), G);
  const el = new El("div");
  G.EXPAND_RENDER.notes(el, { text: "old server", apple: {} });
  await G.flush();
  const ta = el.querySelector("#nt-ta"), stxt = el.querySelector("#nt-savetxt");
  ok("a 404 on GET is not shown as an error", !/Not saved/.test(stxt.textContent), stxt.textContent);
  ta.value = "old server, edited"; ta.fire("input"); G.runTimers(); await G.flush();
  const p = last(G, "POST");
  ok("it still saves, without base_rev (the old route ignores it)", p && p.body.text === "old server, edited"
     && !("base_rev" in p.body), p && p.body);
  ok("... and says Saved", stxt.textContent === "Saved", stxt.textContent);
}

(async () => {
  try { await legacySuite(); } catch (e) { ok("legacy suite ran", false, String(e && e.stack || e)); }
  try { await hubSuite(); } catch (e) { ok("hub suite ran", false, String(e && e.stack || e)); }
  try { await rerenderSuite(); } catch (e) { ok("re-render suite ran", false, String(e && e.stack || e)); }
  try { await refreshSuite(); } catch (e) { ok("refresh suite ran", false, String(e && e.stack || e)); }
  try { await popoutSuite(); } catch (e) { ok("pop-out suite ran", false, String(e && e.stack || e)); }
})();
