// t_agent_rail_collapse.js — the Agent page's owner fixes (2026-10-27):
//   * the Flight Recorder in the 296px Record rail cut every row down to
//     "terminal ech…" and ran its header off the edge; it was one flat list
//     of near-identical rows;
//   * the left sidebar was crowded, its last conversation sliced under TOOLS;
//   * the chat card stopped at 860px, narrower than the status bar above it.
//
// What only a browser can measure (row widths, edges, overlap) is
// tools/tests/browser/t_agent_rail_real.js. This suite pins the logic and the
// DOM contract on the REAL files in the mini DOM (_a11y_dom.js):
//   1. grouping and folding are pure: day groups (Today / Yesterday / date),
//      which day starts open, runs of 3+ same-tool same-chip rows without an
//      Undo, filters applied BEFORE grouping, run state by membership;
//   2. every field interpolated into the new markup is escaped;
//   3. the live card: real <button aria-expanded aria-controls> headers that
//      flip in place (focus stays), state that survives the 3 s poll and a
//      rebuild (focus restored to the same header), days remembered per
//      viewer (and defaults when storage is unreadable), the detail pane and
//      Undo working inside groups, a stale poll answer filtered on arrival;
//   4. motion: nothing moves on a poll; an owner's expand fades only the rows
//      it revealed, through the runtime; either reduce switch = nothing;
//   5. the sidebar's Tools / Local AI headers (aux_agent.js), and the chat
//      card's width rule.
// Fetches answer from in-memory routes; nothing touches the network.
"use strict";
const fs = require("fs");
const path = require("path");
const { bootDashboard, createWindow, harness } = require("./_a11y_dom.js");
const S = require("./_anim_stubs.js");
const T = harness();
const REPO = process.env.HERMES_REPO || path.resolve(__dirname, "..", "..", "..");
const DASH = path.join(REPO, "dashboard");
const REC_SRC = fs.readFileSync(path.join(DASH, "aux_recorder.js"), "utf8");
const AGENT_SRC = fs.readFileSync(path.join(DASH, "aux_agent.js"), "utf8");
const ANIM = fs.readFileSync(path.join(DASH, "aux_anim.js"), "utf8");

async function scenario(name, fn) {
  try { await fn(); } catch (e) { T.check(name + " (ran without throwing)", false, String(e && e.stack || e).split("\n").slice(0, 4).join(" | ")); }
}

// a fixed local "now" (this machine's zone): Tue 27 Oct 2026, 15:00
const NOW = new Date(2026, 9, 27, 15, 0, 0).getTime() / 1000;
const at = function (y, m, d, h, mi) { return new Date(y, m, d, h, mi || 0, 0).getTime() / 1000; };
let ID = 1000;
function act(tool, kind, rev, ts, extra) {
  return Object.assign({ id: ID--, tool: tool, kind: kind, reversible: rev, status: "done", source: "agent",
    target: tool === "clarify" ? "" : tool + " target " + ID, ts: ts }, extra || {});
}
// newest first, the way /api/recorder answers
function fixture() {
  ID = 1000;
  return [
    act("clarify", "agent", "n/a", NOW - 60),
    act("terminal", "shell", "no", NOW - 120), act("terminal", "shell", "no", NOW - 122),
    act("terminal", "shell", "no", NOW - 124), act("terminal", "shell", "no", NOW - 126),
    act("write_file", "write", "yes", NOW - 600, { target: "/Users/you/notes.md" }),
    act("terminal", "shell", "no", NOW - 1400), act("terminal", "shell", "no", NOW - 1405),   // only two: not folded
    act("patch", "write", "partial", NOW - 7200),
    act("web_search", "net", "n/a", at(2026, 9, 26, 18)),                                     // yesterday
    act("web_search", "net", "n/a", at(2026, 8, 7, 11)), act("web_search", "net", "n/a", at(2026, 8, 7, 10, 59)),
    act("web_search", "net", "n/a", at(2026, 8, 7, 10, 58)),                                  // Sep 7: a run of 3
    act("write_file", "write", "yes", at(2025, 11, 31, 9), { status: "undone" })              // last year
  ];
}

// ---------------------------------------------------------------------------
// 1. pure grouping
// ---------------------------------------------------------------------------
async function pureSuite() {
  console.log("\n1. grouping and folding (pure)");
  const w = createWindow({});
  w.esc = require(require("path").join(__dirname, "..", "lib", "index_helpers.js")).indexFunction("esc");
  w.relTime = function () { return "1m ago"; };
  w.__run(REC_SRC, "aux_recorder.js");
  const G = function (rows, o) { return w.recGroupRows(rows, Object.assign({ now: NOW }, o || {})); };

  await scenario("day keys and labels", async function () {
    T.check("a local day key is YYYY-MM-DD", w.recDayKey(NOW) === "2026-10-27", w.recDayKey(NOW));
    T.check("no usable ts is 'undated' (labelled Earlier), never a throw", w.recDayKey(null) === "undated" && w.recDayKey("x") === "undated" && w.recDayLabel("undated", NOW) === "Earlier");
    T.check("Today / Yesterday / 'Sep 7' / a date with its year when it is not this year",
      w.recDayLabel("2026-10-27", NOW) === "Today" && w.recDayLabel("2026-10-26", NOW) === "Yesterday" &&
      w.recDayLabel("2026-09-07", NOW) === "Sep 7" && w.recDayLabel("2025-12-31", NOW) === "Dec 31, 2025",
      [w.recDayLabel("2026-10-26", NOW), w.recDayLabel("2026-09-07", NOW), w.recDayLabel("2025-12-31", NOW)]);
    // "Yesterday" is the previous CALENDAR day, measured from local noon (DST-proof)
    const justAfterMidnight = at(2026, 9, 27, 0, 5);
    T.check("just after midnight, 23:50 the night before is Yesterday", w.recDayLabel(w.recDayKey(at(2026, 9, 26, 23, 50)), justAfterMidnight) === "Yesterday");
  });

  await scenario("groups and their default open state", async function () {
    const g = G(fixture());
    T.check("four day groups plus last year's, newest first",
      g.map(function (x) { return x.label; }).join("|") === "Today|Yesterday|Sep 7|Dec 31, 2025", g.map(function (x) { return x.label; }));
    T.check("each group counts its rows", g.map(function (x) { return x.count; }).join(",") === "9,1,3,1", g.map(function (x) { return x.count; }));
    T.check("Today is open, every older day collapsed", g.map(function (x) { return x.open; }).join(",") === "true,false,false,false");
    const old = G(fixture().slice(9));
    T.check("with nothing today, the NEWEST day is the one open", old[0].label === "Yesterday" && old[0].open && !old[1].open, old.map(function (x) { return x.label + ":" + x.open; }));
    const ov = G(fixture(), { dayOpen: { "2026-10-27": false, "2026-09-07": true } });
    T.check("the owner's own toggles beat both defaults", !ov[0].open && ov[2].open && !ov[1].open);
  });

  await scenario("group order: newest day first, undated last, and never the default-open day", async function () {
    ID = 300;
    const undated = act("mystery", "other", "no", 0);
    const old = [act("web_search", "net", "n/a", at(2026, 9, 25, 9)), act("web_search", "net", "n/a", at(2026, 9, 26, 9))];   // arrived oldest-first
    const g = G([undated].concat(old));
    T.check("rows with no ts become 'Earlier' and sort LAST, even when they arrive first",
      g.map(function (x) { return x.label; }).join("|") === "Yesterday|Oct 25|Earlier", g.map(function (x) { return x.label; }));
    T.check("days are newest first whatever order the rows arrived in", g[0].key === "2026-10-26" && g[1].key === "2026-10-25");
    T.check("with nothing today, the newest DATED day is the open one (not 'Earlier')", g[0].open && !g[1].open && !g[2].open, g.map(function (x) { return x.label + ":" + x.open; }));
    const only = G([undated]);
    T.check("a list of only undated rows still opens its one group", only.length === 1 && only[0].open && only[0].label === "Earlier");
    T.check("a row dated in the future is the newest day, and Today is still open", (function () {
      const f = G([act("a", "net", "n/a", NOW + 2 * 86400), act("b", "net", "n/a", NOW - 10)]);
      return f[0].key === "2026-10-29" && !f[0].open && f[1].label === "Today" && f[1].open;
    })());
  });

  await scenario("folding", async function () {
    const today = G(fixture())[0].items;
    const kinds = today.map(function (it) { return it.run ? "run:" + it.run.tool + "x" + it.run.rows.length : it.row.tool; });
    T.check("4 consecutive irreversible terminals fold; the 2-row terminal pair and the Undo rows stay rows",
      kinds.join(",") === "clarify,run:terminalx4,write_file,terminal,terminal,patch", kinds);
    const run = today[1].run;
    T.check("the run carries its member ids, newest first, and its chip key", run.ids.length === 4 && run.ids[0] === String(today[1].run.rows[0].id) && run.rev === "no");
    T.check("its key is the OLDEST member (stable while the run grows at the new end)", run.key === "run-" + run.rows[3].id, run.key);
    T.check("Sep 7's three read-only searches fold too", G(fixture())[2].items.length === 1 && G(fixture())[2].items[0].run.rows.length === 3);
    // rows with an Undo are never folded, however many
    ID = 10;
    const writes = [1, 2, 3, 4, 5].map(function (i) { return act("write_file", "write", "yes", NOW - i); });
    T.check("five undoable writes in a row: five rows, no run", G(writes)[0].items.every(function (it) { return !!it.row; }) && G(writes)[0].items.length === 5);
    // a different chip breaks a run
    const mixed = [act("terminal", "shell", "no", NOW - 1), act("terminal", "shell", "no", NOW - 2),
      act("terminal", "shell", "no", NOW - 3, { status: "undone" }), act("terminal", "shell", "no", NOW - 4)];
    T.check("an 'undone' chip in the middle splits the run (2 + 1 + 1 are all too short)", G(mixed)[0].items.every(function (it) { return !!it.row; }));
    // runs never cross a day
    const span = [act("terminal", "shell", "no", at(2026, 9, 27, 0, 2)), act("terminal", "shell", "no", at(2026, 9, 27, 0, 1)),
      act("terminal", "shell", "no", at(2026, 9, 26, 23, 59)), act("terminal", "shell", "no", at(2026, 9, 26, 23, 58))];
    const sg = G(span);
    T.check("4 terminals across midnight are 2 + 2 in two days, nothing folded", sg.length === 2 && sg.every(function (g) { return g.items.length === 2 && !!g.items[0].row; }));
    T.check("REC_FOLD_MIN is 3", w.REC_FOLD_MIN === 3);
  });

  await scenario("filters apply BEFORE grouping", async function () {
    ID = 50;
    const rows = [act("terminal", "shell", "no", NOW - 1), act("terminal", "shell", "no", NOW - 2),
      act("write_file", "write", "yes", NOW - 3),
      act("terminal", "shell", "no", NOW - 4), act("terminal", "shell", "no", NOW - 5)];
    const all = G(rows)[0].items, sh = G(rows, { filter: "shell" })[0].items;
    T.check("All: the write in the middle keeps the terminals apart (2,1,2 — no run)", all.length === 5 && all.every(function (it) { return !!it.row; }));
    T.check("Shell: the write is filtered first, so the four terminals are ONE run", sh.length === 1 && sh[0].run && sh[0].run.rows.length === 4);
    T.check("Writes: only the write is grouped", G(rows, { filter: "write" })[0].count === 1);
    T.check("Undone keys off the status, the others off kind (the server's rule)",
      w.recFilterRows([{ kind: "write", status: "undone" }, { kind: "write", status: "done" }], "undone").length === 1 &&
      w.recFilterRows([{ kind: "computer" }, { kind: "shell" }], "computer").length === 1 && w.recFilterRows(rows, "all").length === 5);
    T.check("filtering to nothing renders the empty message", /rec-empty/.test(w.renderRecorderList(rows, { filter: "computer", now: NOW })));
  });

  await scenario("run open state is membership", async function () {
    const rows = fixture(), run = G(rows)[0].items[1].run;
    const one = {}; one[run.ids[2]] = 1;
    T.check("a run is open when ANY member id is in the open set", G(rows, { runOpen: one })[0].items[1].run.open === true);
    ID = 2000;
    const grown = rows.slice(0, 1).concat([act("terminal", "shell", "no", NOW - 100)], rows.slice(1));   // one newer terminal on top of the run
    const gi = G(grown, { runOpen: one })[0].items;
    T.check("...so it stays open when it grows at the new end (and keeps its key)", gi[1].run.rows.length === 5 && gi[1].run.open && gi[1].run.key === run.key, gi.map(function (x) { return x.run ? "run" + x.run.rows.length : x.row.tool; }));
    T.check("a closed run with no members in the set is closed", G(rows)[0].items[1].run.open === false);
  });

  await scenario("the run summary's time span", async function () {
    w.relTime = function (ts) { return ({ 1: "2m ago", 2: "3m ago", 3: "23m ago", 4: "2h ago" })[ts] || "x"; };
    T.check("same unit: '2–3m ago'", w.recSpanWhen(1, 2) === "2–3m ago", w.recSpanWhen(1, 2));
    T.check("different units: '23m–2h ago' (short: it shares the run header with the name, count and chip)", w.recSpanWhen(3, 4) === "23m–2h ago", w.recSpanWhen(3, 4));
    T.check("one moment: just it", w.recSpanWhen(1, 1) === "2m ago");
    w.relTime = function () { return "1m ago"; };
  });
}

// ---------------------------------------------------------------------------
// 2. escaping
// ---------------------------------------------------------------------------
async function escapeSuite() {
  console.log("\n2. every interpolated field in the new markup is escaped");
  const w = createWindow({});
  // the REAL esc() from index.html
  w.esc = require(path.join(REPO, "tools", "tests", "lib", "index_helpers.js")).indexFunction("esc");
  w.relTime = function () { return '<b>"1m ago"'; };
  w.__run(REC_SRC, "aux_recorder.js");
  const X = '"><img src=x onerror=alert(1)><x a="';
  ID = 9;
  const hostile = function (extra) {
    return Object.assign({ id: X, tool: "terminal" + X, kind: "shell" + X, reversible: "no", status: "done",
      source: X, target: "echo " + X, ts: NOW - 30, no_snapshot_why: "why" + X }, extra || {});
  };
  // three of them fold into a run; one more with an Undo stays a row
  const rows = [hostile({ id: "a" + X }), hostile({ id: "b" + X }), hostile({ id: "c" + X }),
    hostile({ id: "u" + X, kind: "write", reversible: "yes", tool: "write_file" + X })];
  const html = w.renderRecorderList(rows, { now: NOW, runOpen: {} });
  T.check("no raw tag from the data survives (no <img, no <x)", !/<img|<x\b/i.test(html), html.slice(0, 300));
  const box = w.document.createElement("div");
  box.innerHTML = html;
  const run = box.querySelector(".rec-runhd"), body = run && box.querySelector("#" + run.getAttribute("aria-controls"));
  T.check("the markup parses back into the intended structure: one day, one run of 3, one undo row",
    box.querySelectorAll(".rec-dayhd").length === 1 && !!run && box.querySelectorAll(".rec-runbody .rec-row").length === 3 &&
    box.querySelectorAll(".rec-daybody > .rec-row").length === 1 && box.querySelectorAll("img,x").length === 0);
  T.check("the run's aria-controls finds its own body (the key went through recDomId)", !!body && /^rec-run-[A-Za-z0-9_-]+$/.test(run.getAttribute("aria-controls")), run && run.getAttribute("aria-controls"));
  T.check("run header: data-run is recDomId-safe, data-kind round-trips", /^run-[A-Za-z0-9_-]+$/.test(run.getAttribute("data-run")) && run.getAttribute("data-kind") === "shell" + X);
  T.check("run header: the tool name is text, and the aria-label carries it verbatim",
    run.querySelector(".rec-tool").textContent === "terminal" + X && run.getAttribute("aria-label").indexOf("terminal" + X) === 0);
  T.check("run header: the time span is text (the relTime string's markup is inert)", run.querySelector(".rec-when").textContent === '<b>"1m ago"');
  const r = box.querySelector(".rec-runbody .rec-row");
  T.check("row: data-id and data-kind round-trip", r.getAttribute("data-id") === "a" + X && r.getAttribute("data-kind") === "shell" + X);
  const tg = r.querySelector(".rec-tgt");
  T.check("row: the target is text AND its full value rides in the title", tg.textContent === "echo " + X && tg.getAttribute("title") === "echo " + X);
  T.check("row: tool, source and chip tooltip round-trip", r.querySelector(".rec-tool").textContent === "terminal" + X &&
    r.querySelector(".rec-src").textContent === X && r.querySelector(".rchip").getAttribute("title") === "why" + X);
  const u = box.querySelector("[data-undo]");
  T.check("the Undo button's id round-trips", u && u.getAttribute("data-undo") === "u" + X);
  const day = box.querySelector(".rec-dayhd");
  T.check("day header: the button sits in an <h3> under the card's <h2>, so the heading list reads Flight Recorder > Today",
    day.parentNode.localName === "h3" && /\brec-dayh\b/.test(day.parentNode.className) && day.parentNode.parentNode.className === "rec-day");
  T.check("run header: the tool name rides in a title (it can ellipsise), escaped", run.querySelector(".rec-tool").getAttribute("title") === "terminal" + X);
  T.check("day header: the label and count are text, and its aria-label says both",
    day.querySelector(".rec-daylbl").textContent === "Today" && day.querySelector(".rec-dayn").textContent === "4" && day.getAttribute("aria-label") === "Today, 4 actions");
  T.check("...and its aria-controls names its own body", (box.querySelector("#" + day.getAttribute("aria-controls")) || {}).className === "rec-daybody");
  const odd = w.renderRecorderList([{ id: 1, tool: null, kind: undefined, target: 42, ts: NOW, reversible: 7 }], { now: NOW });
  T.check("numbers, nulls and undefined render without throwing", /rec-row/.test(odd) && />42</.test(odd));
}

// ---------------------------------------------------------------------------
// 3 + 4. the live card in the real page, with the motion runtime
// ---------------------------------------------------------------------------
let L = null;
async function boot(o) {
  o = o || {};
  const realNow = Date.now;
  return bootDashboard({
    aux: ["aux_recorder.js"].concat(o.aux || []),
    routes: Object.assign({
      "/api/recorder": function (url) {
        if (/[?&]id=/.test(url)) return { id: 1, kind: "shell", summary: "ran it", args: { command: "echo" } };
        if (o.stale) return { recorder_ok: true, checkpoints_enabled: true, counts: {}, actions: o.stale() };
        const k = (/[?&]kind=([^&]+)/.exec(url) || [])[1];
        return { recorder_ok: true, checkpoints_enabled: true, counts: { reversible: 2, undone: 1 }, actions: w0().recFilterRows(o.rows(), k ? decodeURIComponent(k) : "all") };
      },
      "/api/recorder/retention": { ok: true, retain_days: 90, total: 14 },
      "/api/undo": function () { o.undos = (o.undos || 0) + 1; return { ok: true }; }
    }, o.routes || {}),
    media: o.media,
    before: function (w) {
      L = S.install(w, { anime: true }); w.__run(ANIM, "aux_anim.js");
      if (o.storageBefore) o.storageBefore(w);
      CUR = w; void realNow;
    }
  }).then(function (w) { if (o.storage) o.storage(w); return w; });
}
let CUR = null;
function w0() { return CUR; }
const rowsMoved = function () {
  const out = [];
  L.of("motion", "animate").forEach(function (c) { S.listOf(c.el).forEach(function (el) { if (el && el.classList && (el.classList.contains("rec-row") || el.classList.contains("rec-runhd") || el.classList.contains("rec-dayhd") || el.classList.contains("rec-daybody") || el.classList.contains("rec-runbody"))) out.push(el); }); });
  return out;
};

async function liveSuite() {
  console.log("\n3. the live card");
  const realNow = Date.now;
  Date.now = function () { return NOW * 1000; };
  try {
    const st = { rows: fixture };
    const w = await boot(st);
    const d = w.document, A = w.HermesAnim;
    // the card lives in the Console view in the bare page (the rail move is
    // aux_agent's); show that view so its controls can take focus
    await w.__run("loadRecorder()"); await w.__tick();
    d.getElementById("view-console").hidden = false;
    L.finishAll(); await w.__tick();
    const list = d.getElementById("rec-list");
    const dayhd = function (label) { return Array.from(list.querySelectorAll(".rec-dayhd")).find(function (b) { return b.querySelector(".rec-daylbl").textContent === label; }); };
    const body = function (b) { return d.getElementById(b.getAttribute("aria-controls")); };
    const runhd = function () { return list.querySelector(".rec-runhd"); };

    await scenario("semantics", async function () {
      const hds = Array.from(list.querySelectorAll(".rec-dayhd,.rec-runhd"));
      T.check("every group header is a real <button type=button> with aria-expanded + aria-controls naming its body",
        hds.length === 6 && hds.every(function (b) { return b.localName === "button" && b.getAttribute("type") === "button" && /^(true|false)$/.test(b.getAttribute("aria-expanded")) && !!body(b); }),
        hds.map(function (b) { return b.localName + ":" + b.getAttribute("aria-expanded"); }));
      T.check("Today open, Yesterday / Sep 7 / last year collapsed (bodies hidden)",
        dayhd("Today").getAttribute("aria-expanded") === "true" && !body(dayhd("Today")).hidden &&
        ["Yesterday", "Sep 7", "Dec 31, 2025"].every(function (l) { return dayhd(l).getAttribute("aria-expanded") === "false" && body(dayhd(l)).hidden; }));
      T.check("the run shows 'terminal ×4' and its rows are in the DOM but hidden", runhd().textContent.indexOf("terminal") >= 0 && runhd().querySelector(".rec-runn").textContent === "×4" && body(runhd()).hidden && body(runhd()).querySelectorAll(".rec-row").length === 4);
      T.check("the header's count wraps under the title instead of being forced onto one nowrap line",
        /\.rec-head\{[^}]*flex-wrap:wrap/.test(d.getElementById("reccss").textContent) && !!d.querySelector("#recorder-card .rec-head .rec-htitle"));
    });

    await scenario("a toggle flips in place, focus stays on the header", async function () {
      const hd = runhd();
      hd.focus();
      const kids = Array.from(list.children);
      const kd = w.__key(hd, "Enter");
      T.check("no key handler swallows Enter (the browser's own button activation runs)", !kd.defaultPrevented);
      hd.click();                                 // what Enter / Space activate on a <button>
      T.check("the run opened: aria-expanded true, its body shown", hd.getAttribute("aria-expanded") === "true" && !body(hd).hidden);
      T.check("...in place: the list was not rebuilt and focus is still on the same header", d.activeElement === hd && Array.from(list.children).every(function (k, i) { return k === kids[i]; }));
      hd.click();
      T.check("a second activation closes it, focus still there", hd.getAttribute("aria-expanded") === "false" && body(hd).hidden && d.activeElement === hd);
      hd.click();
    });

    await scenario("state survives the 3 s poll", async function () {
      const hd = runhd(), y = dayhd("Yesterday");
      y.click();                                   // open Yesterday too
      L.finishAll();
      hd.focus();
      const before = list.innerHTML.length, kids = Array.from(list.children);
      await w.__run("loadRecorder()"); await w.__tick();
      T.check("same data + focus inside: nothing rebuilt (C99.1 still holds after an in-place toggle)", Array.from(list.children).every(function (k, i) { return k === kids[i]; }) && list.innerHTML.length === before);
      d.activeElement.blur();
      await w.__run("loadRecorder()"); await w.__tick();
      T.check("a poll that DOES rebuild keeps the run open and Yesterday open", runhd().getAttribute("aria-expanded") === "true" && !body(runhd()).hidden && dayhd("Yesterday").getAttribute("aria-expanded") === "true");
      // a new terminal on top of the open run, with focus on its header
      runhd().focus();
      const old = st.rows;
      st.rows = function () { const r = old(); ID = 5000; r.splice(1, 0, act("terminal", "shell", "no", NOW - 100)); return r; };
      await w.__run("loadRecorder()"); await w.__tick();
      T.check("the run grew to ×5, stayed open, and focus came back to its (rebuilt) header",
        runhd().querySelector(".rec-runn").textContent === "×5" && runhd().getAttribute("aria-expanded") === "true" && d.activeElement === runhd(), [runhd().querySelector(".rec-runn").textContent, d.activeElement && d.activeElement.className]);
      dayhd("Sep 7").focus();
      st.rows = old;
      await w.__run("loadRecorder()"); await w.__tick();
      T.check("a rebuild with focus on a day header puts it back on that day's header", d.activeElement === dayhd("Sep 7"));
    });

    await scenario("days are remembered per viewer", async function () {
      const saved = JSON.parse(w.localStorage.getItem("hermes_rec_days") || "null");
      T.check("Yesterday's open state is in localStorage under its calendar day", saved && saved["2026-10-26"] === true, saved);
      dayhd("Today").click();
      T.check("closing Today stores false for 2026-10-27", JSON.parse(w.localStorage.getItem("hermes_rec_days"))["2026-10-27"] === false);
      dayhd("Today").click();
    });

    await scenario("the detail pane and Undo work inside groups", async function () {
      const row = body(runhd()).querySelector(".rec-row");
      w.__click(row); await w.__tick();
      const pane = row.nextSibling;
      T.check("clicking a row inside an open run opens its detail right under it, inside the run", pane && pane.classList.contains("rec-detail") && pane.parentNode === body(runhd()) && /ran it/.test(pane.textContent), pane && pane.className);
      w.__click(row);
      const ub = list.querySelector("[data-undo]");
      T.check("(setup) an undoable row is visible in Today", !!ub && ub.closest(".rec-daybody") === body(dayhd("Today")));
      w.__confirm = true;
      w.__click(ub); await w.__tick(); await w.__tick();
      T.check("its Undo posts /api/undo", st.undos === 1, st.undos);
      T.check("no page error so far", w.__errors.length === 0, w.__errors.map(String));
    });

    console.log("\n4. motion");
    await scenario("nothing moves on a poll; an owner's expand fades only what it revealed", async function () {
      L.finishAll(); L.clear();
      d.activeElement && d.activeElement.blur && d.activeElement.blur();
      await w.__run("loadRecorder()"); await w.__tick();
      await w.__run("HA().quiet(function(){ return loadRecorder(); })"); await w.__tick();
      T.check("two polls (one live, one quiet, the rail's): zero animations on rows, headers or bodies", rowsMoved().length === 0, rowsMoved().map(function (e) { return e.className; }));
      const sep = dayhd("Sep 7");
      L.clear();
      sep.click();
      const moved = rowsMoved();
      T.check("opening Sep 7 animated exactly what it revealed: its run summary (its rows stay folded)",
        moved.length === 1 && moved[0].classList.contains("rec-runhd") && body(sep).contains(moved[0]), moved.map(function (e) { return e.className; }));
      T.check("...with the 'fade' preset (opacity only, never a height)", L.of("motion", "animate").every(function (c) { return JSON.stringify(c.kf) === '{"opacity":[0,1]}'; }), L.of("motion", "animate").map(function (c) { return JSON.stringify(c.kf); }));
      L.finishAll(); L.clear();
      const srun = body(sep).querySelector(".rec-runhd");
      srun.click();
      const m2 = rowsMoved();
      T.check("opening that run fades its 3 rows in, nothing else", m2.length === 3 && m2.every(function (e) { return e.classList.contains("rec-row") && body(srun).contains(e); }), m2.length);
      L.finishAll(); L.clear();
      srun.click(); sep.click();
      T.check("collapsing moves nothing", rowsMoved().length === 0);
      await w.__run("loadRecorder()"); await w.__tick();
      T.check("...and the poll after it moves nothing either (the revealed rows spent their keys)", rowsMoved().length === 0);
      d.documentElement.setAttribute("data-reduce", "1"); await w.__tick();
      const s0 = A.stats().started;
      L.clear();
      sep.click(); body(sep).querySelector(".rec-runhd").click();
      T.check("in-app reduce: expanding a day and a run starts no animation at all", A.stats().started === s0 && rowsMoved().length === 0, [A.stats().started - s0, rowsMoved().length]);
      T.check("...and what it revealed is simply there", !body(sep).hidden && !body(body(sep).querySelector(".rec-runhd")).hidden);
      d.documentElement.removeAttribute("data-reduce");
      T.check("CSS: the chevron rotates by transform with an explicit transition-property, off under both reduce switches",
        /\.rec-chev\{[^}]*transition-property:transform/.test(d.getElementById("reccss").textContent) &&
        /@media \(prefers-reduced-motion:reduce\)\{\.rec-chev,\.rec-dayhd,\.rec-runhd\{transition:none\}\}/.test(d.getElementById("reccss").textContent) &&
        /:root\[data-reduce\] :is\(\.rec-chev,\.rec-dayhd,\.rec-runhd\)\{transition:none\}/.test(d.getElementById("reccss").textContent));
      T.check("CSS: no transition on height anywhere in the recorder's sheet", !/transition[^;}]*height/.test(d.getElementById("reccss").textContent));
      T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
    });
  } finally { Date.now = realNow; }
}

async function osReduceSuite() {
  const realNow = Date.now;
  Date.now = function () { return NOW * 1000; };
  try {
    const st = { rows: fixture };
    const w = await boot(Object.assign(st, { media: function (q) { return /prefers-reduced-motion/.test(q); } }));
    const d = w.document, A = w.HermesAnim;
    await w.__run("loadRecorder()"); await w.__tick();
    const s0 = A.stats().started;
    const sep = Array.from(d.querySelectorAll(".rec-dayhd")).find(function (b) { return /Sep 7/.test(b.textContent); });
    sep.click();
    d.getElementById(sep.getAttribute("aria-controls")).querySelector(".rec-runhd").click();
    T.check("OS reduced motion: expanding starts nothing (the runtime lands it at once)", A.stats().started === s0, A.stats().started - s0);
  } finally { Date.now = realNow; }
}

async function storageSuite() {
  console.log("\n3b. unreadable storage is the defaults");
  const realNow = Date.now;
  Date.now = function () { return NOW * 1000; };
  try {
    for (const [label, install] of [
      // (installed after the page booted: index.html's own boot reads localStorage unguarded)
      ["corrupt JSON", function (w) { w.localStorage.setItem("hermes_rec_days", "{nope"); }],
      ["the wrong shape", function (w) { w.localStorage.setItem("hermes_rec_days", JSON.stringify(["2026-09-07"])); }],
      ["non-boolean values and junk keys", function (w) { w.localStorage.setItem("hermes_rec_days", JSON.stringify({ "2026-09-07": "yes", "<b>": true })); }],
      ["a throwing localStorage", function (w) { w.localStorage.getItem = function () { throw new Error("denied"); }; w.localStorage.setItem = function () { throw new Error("denied"); }; }]]) {
      const w = await boot({ rows: fixture, storage: install });
      const d = w.document;
      await w.__run("loadRecorder()"); await w.__tick();
      const hds = Array.from(d.querySelectorAll(".rec-dayhd")).map(function (b) { return b.getAttribute("aria-expanded"); });
      T.check(label + ": defaults (Today open, older closed), no page error", hds.join(",") === "true,false,false,false" && w.__errors.length === 0, [hds, w.__errors.map(String)]);
      d.querySelectorAll(".rec-dayhd")[1].click();
      T.check(label + ": a toggle still works for the page's life", d.querySelectorAll(".rec-dayhd")[1].getAttribute("aria-expanded") === "true" && w.__errors.length === 0);
    }
  } finally { Date.now = realNow; }
}

async function staleSuite() {
  console.log("\n3c. a poll answered for the previous filter is filtered on arrival");
  const realNow = Date.now;
  Date.now = function () { return NOW * 1000; };
  try {
    const st = { rows: fixture, stale: fixture };     // the server ignores ?kind= (a late 'all' answer)
    const w = await boot(st);
    const d = w.document;
    w.__run("recSetFilter('shell')"); await w.__tick();
    const kinds = Array.from(d.querySelectorAll("#rec-list .rec-row")).map(function (r) { return r.getAttribute("data-kind"); });
    T.check("Shell shows shell rows only, even from an unfiltered answer", kinds.length === 6 && kinds.every(function (k) { return k === "shell"; }), kinds);
    const run = d.querySelector("#rec-list .rec-runhd");
    T.check("...and with the write filtered out first, all six of today's terminals are ONE run",
      run && run.querySelector(".rec-runn").textContent === "×6", run && run.textContent);
  } finally { Date.now = realNow; }
}

async function undoRunSuite() {
  console.log("\n3d. the row an owner just undid never vanishes into a folded run");
  const realNow = Date.now;
  Date.now = function () { return NOW * 1000; };
  try {
    const st = { done: false };
    ID = 700;
    const rows = function () {
      ID = 700;
      return [act("write_file", "write", "yes", NOW - 60, { id: 30, status: st.done ? "undone" : "done" }),
        act("write_file", "write", "yes", NOW - 70, { id: 29, status: "undone" }),
        act("write_file", "write", "yes", NOW - 80, { id: 28, status: "undone" }),
        act("web_search", "net", "n/a", NOW - 90, { id: 27 })];
    };
    const w = await boot({ rows: rows, routes: { "/api/undo": function () { st.done = true; return { ok: true }; } } });
    const d = w.document;
    await w.__run("loadRecorder()"); await w.__tick();
    d.getElementById("view-console").hidden = false;      // the bare page keeps the card's host view hidden
    T.check("(setup) before the Undo: the row has its button and the two undone rows are plain rows (a pair does not fold)",
      !!d.querySelector('[data-undo="30"]') && d.querySelectorAll(".rec-runhd").length === 0);
    w.__confirm = true;
    w.__click(d.querySelector('[data-undo="30"]')); await w.__tick(); await w.__tick(); await w.__tick();
    const run = d.querySelector(".rec-runhd");
    T.check("after it: three undone writes in a row ARE a run...", !!run && run.querySelector(".rec-runn").textContent === "×3");
    T.check("...but that run is OPEN, so the row just acted on is on screen", run.getAttribute("aria-expanded") === "true" && !d.getElementById(run.getAttribute("aria-controls")).hidden);
    const row = d.querySelector('.rec-row[data-id="30"]');
    const hiddenUp = (function (n) { for (; n && n !== d.body; n = n.parentNode) if (n.hasAttribute && n.hasAttribute("hidden")) return n.className || n.localName; return ""; })(row);
    T.check("the row reads undone and none of its ancestors is hidden", !!row && /is-undone/.test(row.className) && !hiddenUp, [row && row.className, hiddenUp]);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  } finally { Date.now = realNow; }
}

// ---------------------------------------------------------------------------
// 5. the sidebar sections and the chat card (aux_agent.js)
// ---------------------------------------------------------------------------
async function sidebarSuite() {
  console.log("\n5. the sidebar's Tools / Local AI sections");
  const routes = { "/api/actions": { actions: [{ label: "Plan my day", prompt: "x" }, { label: "Summarise my inbox", prompt: "y" }] } };
  let w = await bootDashboard({ aux: ["aux_agent.js"], routes: routes, before: function (x) { L = S.install(x, { anime: true }); x.__run(ANIM, "aux_anim.js"); } });
  let d = w.document;
  const hd = function (n) { return d.getElementById("cs-" + n + "-hd"); };
  await scenario("semantics and defaults", async function () {
    T.check("(Hub) both headers are real buttons over their sections, open by default",
      ["tools", "ai"].every(function (n) { const b = hd(n); return b && b.localName === "button" && b.getAttribute("type") === "button" && b.getAttribute("aria-expanded") === "true"; }) &&
      hd("tools").getAttribute("aria-controls") === "cs-tools" && hd("ai").getAttribute("aria-controls") === "cs-ai" && !d.getElementById("cs-tools").hidden);
    T.check("they keep the section labels as text", hd("tools").textContent.trim() === "Tools" && hd("ai").textContent.trim() === "Local AI");
    T.check("the order is unchanged: list, Tools header, Tools, Local AI header, Local AI",
      d.getElementById("cs-convos").nextElementSibling === hd("tools") && hd("tools").nextElementSibling === d.getElementById("cs-tools") &&
      d.getElementById("cs-tools").nextElementSibling === hd("ai") && hd("ai").nextElementSibling === d.getElementById("cs-ai"));
    w.__run("setView('agent')"); await w.__tick();
    T.check("on the Agent page Tools starts collapsed (the composer chips carry the same actions), Local AI open",
      hd("tools").getAttribute("aria-expanded") === "false" && d.getElementById("cs-tools").hidden && hd("ai").getAttribute("aria-expanded") === "true" && !d.getElementById("cs-ai").hidden);
    T.check("the sidebar is in #agent-side", d.getElementById("agent-side").contains(d.getElementById("chat-side")));
  });
  await scenario("toggle, remember, re-render", async function () {
    const b = hd("tools");
    b.focus();
    T.check("no key handler swallows Space (native button activation)", !w.__key(b, " ").defaultPrevented);
    L.clear();
    b.click();
    T.check("opening Tools: shown, aria-expanded true, focus still on the header", !d.getElementById("cs-tools").hidden && b.getAttribute("aria-expanded") === "true" && d.activeElement === b);
    T.check("...it faded in through the motion runtime (opacity only)", L.of("motion", "animate").some(function (c) { return S.listOf(c.el).indexOf(d.getElementById("cs-tools")) >= 0 && JSON.stringify(c.kf) === '{"opacity":[0,1]}'; }));
    T.check("...and the choice is stored", JSON.parse(w.localStorage.getItem("hermes_agent_side")).tools === true);
    hd("ai").click();
    T.check("closing Local AI hides it and stores false", d.getElementById("cs-ai").hidden && JSON.parse(w.localStorage.getItem("hermes_agent_side")).ai === false);
    L.clear();
    hd("ai").click(); hd("ai").click();
    T.check("collapsing starts no animation", L.of("motion", "animate").filter(function (c) { return S.listOf(c.el).indexOf(d.getElementById("cs-ai")) >= 0; }).length === 1);
    await w.__run("renderChatSide()"); await w.__tick();
    T.check("renderChatSide() rebuilding the Tools list keeps the section state", !d.getElementById("cs-tools").hidden && d.getElementById("cs-ai").hidden && d.querySelectorAll("#cs-tools .cs-item").length >= 2);
    w.__run("setView('hub')"); await w.__tick();
    T.check("the owner's choice holds in the Hub too", !d.getElementById("cs-tools").hidden && d.getElementById("cs-ai").hidden);
    T.check("no page error", w.__errors.length === 0, w.__errors.map(String));
  });
  await scenario("unreadable storage is the defaults", async function () {
    w = await bootDashboard({ aux: ["aux_agent.js"], routes: routes, before: function (x) { x.localStorage.setItem("hermes_agent_side", "[1,"); } });
    d = w.document;
    w.__run("setView('agent')"); await w.__tick();
    T.check("corrupt JSON: Agent defaults", d.getElementById("cs-tools").hidden && !d.getElementById("cs-ai").hidden && w.__errors.length === 0);
    w = await bootDashboard({ aux: ["aux_agent.js"], routes: routes });
    d = w.document;
    // after boot (index.html's own boot reads localStorage unguarded)
    w.localStorage.getItem = function () { throw new Error("denied"); };
    w.localStorage.setItem = function () { throw new Error("denied"); };
    w.__run("setView('agent')"); await w.__tick();
    hd("tools").click();
    T.check("a throwing localStorage: defaults, and the toggle still works", !d.getElementById("cs-tools").hidden && hd("tools").getAttribute("aria-expanded") === "true" && w.__errors.length === 0, w.__errors.map(String));
  });
  await scenario("the conversation list owns the leftover height", async function () {
    const css = d.getElementById("agent-css").textContent;
    T.check("#cs-convos: flex 1 1 0 with a row floor; Tools may shrink to 0 and scroll", /#agent-side #cs-convos\{flex:1 1 0;min-height:min\(\d+px,\d+vh\)/.test(css) && /#agent-side #cs-tools\{flex:0 1 auto;min-height:0\}/.test(css));
    T.check("section headers are flex:none (never squeezed into the list) and start on a hairline", /\.chatside \.cs-sechd\{[^}]*flex:none[^}]*border-top:1px solid var\(--hairline\)/.test(css));
    T.check("the run header's NAME gives way, never its count (a long tool name used to push ×N out of the clip)",
      /\.rec-runname \.rec-tool\{flex:0 1 auto;min-width:0\}/.test(REC_SRC) && /\.rec-runn\{flex:none/.test(REC_SRC));
    T.check("the header chevron rotates by transform, transition off under both reduce switches",
      /\.cs-sechd \.chev\{[^}]*transition-property:transform/.test(css) && /prefers-reduced-motion:reduce\)\{\.chatside \.cs-sechd,\.chatside \.cs-sechd \.chev\{transition:none\}\}/.test(css) && /:root\[data-reduce\] \.chatside/.test(css));
  });
  console.log("\n5b. the chat card spans the status bar");
  const agentCode = AGENT_SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");   // code, not the comments that tell the story
  T.check("#agent-stream .chatcol has no 860px cap (max-width:none)", /#agent-stream \.chatcol\{[^}]*max-width:none/.test(agentCode) && !/860px/.test(agentCode));
  T.check("...and nothing else in the page uses an 860px max-width", !/max-width:\s*860px/.test(fs.readFileSync(path.join(DASH, "index.html"), "utf8")));
  T.check("the bubbles keep a reading measure relative to the card", /#agent-stream \.bubble\{max-width:min\(76%,52rem\)\}/.test(AGENT_SRC));
  T.check("the rail's header and body share one inset", /#agent-rail #recorder-card>h2\{[^}]*padding:2px 4px 8px\}/.test(AGENT_SRC) && /#agent-rail #recorder-card>\.body\{padding:8px 4px 10px\}/.test(AGENT_SRC));
  T.check("the rail no longer overrides the row layout (aux_recorder's @container rule owns it)", !/#agent-rail #recorder-card \.rec-row\{/.test(AGENT_SRC));
  T.check("aux_recorder: two lines under 520px via a container query, with a rail fallback without one",
    /@container rec \(max-width:520px\)\{/.test(REC_SRC) && /@supports not \(container-type:inline-size\)\{/.test(REC_SRC) && /container-type:inline-size;container-name:rec/.test(REC_SRC));
}

(async function main() {
  console.log("Agent page: the Record rail, the sidebar sections and the chat width");
  await pureSuite();
  await escapeSuite();
  await liveSuite();
  await osReduceSuite();
  await storageSuite();
  await staleSuite();
  await undoRunSuite();
  await sidebarSuite();
  T.done();
})().catch(function (e) { console.log("  FAIL crashed: " + (e && e.stack || e)); console.log("TESTS 0 passed 1 failed"); process.exit(1); });
