// aux_recorder.js — Flight Recorder + Undo panel (P1.2). Served at
// /aux_recorder.js, loaded after expand.js so it sees every inline helper
// (esc, HA, REDUCE, relTime). Injects a "Flight Recorder" card at the top
// of the Console view and wraps loadConsole so it rides the existing 3s poll.
// Zero emoji; bespoke two-tone SVG only; 12-hour times; strings via esc().
//
// Public (top-level so the headless render harness can call renderRecorderRows
// directly, and so inline nothing is needed): renderRecorderRows(actions) and
// renderRecorderRetention(payload) return pure HTML; loadRecorder / recUndo /
// recDetail drive the live panel.
//
// The card's one settings surface is the retention footer — "Keep history
// <n days>", GET/POST /api/recorder/retention — deliberately BELOW the feed
// and in --faint, so it reads as a setting about the log rather than as
// another line of it.

var recState = {filter: "all", expanded: null, detail: {}, last: null,
                ckpt: true, err: false, busy: {},
                dayOpen: null,     // {dayKey: bool} the owner's own day toggles (localStorage)
                runOpen: {},       // {action id: 1} members of runs the owner opened (page life)
                dayStick: {}};     // {dayKey: true} days ever painted open by default (page life, E88)

// ---- helpers -------------------------------------------------------------
function recE(s){ return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }

// The motion runtime (docs/plans/motion-system-2026-10.md §6.5): index.html's
// HA() is window.HermesAnim, or ANIM_NULL (every call instant) when aux_anim.js
// did not load. A headless harness has neither, and then nothing here moves.
function recHA(){
  try { if (typeof HA === "function") return HA(); } catch (e) {}
  try { if (typeof window !== "undefined" && window.HermesAnim) return window.HermesAnim; } catch (e) {}
  return null;
}
function recReduced(){
  var H = recHA();
  try { if (H && typeof H.reduced === "function") return !!H.reduced(); } catch (e) {}
  return typeof REDUCE !== "undefined" && !!REDUCE;
}

function recWhen(ts){
  if (!ts) return "";
  try { return (typeof relTime === "function") ? relTime(ts) : ""; }
  catch (e) { return ""; }
}
function recClock(ts){                       // absolute 12-hour, for the tooltip
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleString("en-US",
      {month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true});
  } catch (e) { return ""; }
}

// ---- bespoke two-tone glyphs (accent fill @ .16 + currentColor stroke) ----
var recGLY = {
  box:'<rect x="3" y="5" width="18" height="14" rx="2.4" fill="currentColor" opacity=".16"/>'+
      '<rect x="3" y="5" width="18" height="14" rx="2.4"/>'+
      '<path d="M12 9.5a2.5 2.5 0 1 0 2.5 2.5" stroke-linecap="round"/>'+
      '<path d="M12 6.7V9.5L9.6 8.3" stroke-linecap="round" stroke-linejoin="round"/>',
  write:'<path d="M6 3.5h7l5 5v11a1.4 1.4 0 0 1-1.4 1.4H6A1.4 1.4 0 0 1 4.6 19.5V4.9A1.4 1.4 0 0 1 6 3.5z" fill="currentColor" opacity=".14"/>'+
        '<path d="M13 3.5v5h5" stroke-linejoin="round"/>'+
        '<path d="M6 3.5h7l5 5v11a1.4 1.4 0 0 1-1.4 1.4H6A1.4 1.4 0 0 1 4.6 19.5V4.9A1.4 1.4 0 0 1 6 3.5z" stroke-linejoin="round"/>'+
        '<path d="M8.4 13h6M8.4 16h4" stroke-linecap="round"/>',
  shell:'<rect x="3" y="4.5" width="18" height="15" rx="2.2" fill="currentColor" opacity=".16"/>'+
        '<rect x="3" y="4.5" width="18" height="15" rx="2.2"/>'+
        '<path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5" stroke-linecap="round" stroke-linejoin="round"/>',
  computer:'<rect x="3" y="4.5" width="18" height="12" rx="2" fill="currentColor" opacity=".16"/>'+
           '<rect x="3" y="4.5" width="18" height="12" rx="2"/>'+
           '<path d="M9 20h6M12 16.5V20" stroke-linecap="round"/>',
  memory:'<rect x="5" y="5" width="14" height="14" rx="2.4" fill="currentColor" opacity=".16"/>'+
         '<rect x="5" y="5" width="14" height="14" rx="2.4"/>'+
         '<path d="M9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3" stroke-linecap="round"/>',
  read:'<path d="M3.5 5.5s3-1.5 8.5-1.5V19c-5.5 0-8.5 1.5-8.5 1.5z" fill="currentColor" opacity=".16"/>'+
       '<path d="M3.5 5.5s3-1.5 8.5-1.5 8.5 1.5 8.5 1.5V19S17 17.5 12 17.5 3.5 19 3.5 19z" stroke-linejoin="round"/>'+
       '<path d="M12 4v13.5" stroke-linecap="round"/>',
  net:'<circle cx="12" cy="12" r="8.5" fill="currentColor" opacity=".14"/>'+
      '<circle cx="12" cy="12" r="8.5"/>'+
      '<path d="M3.5 12h17M12 3.5c2.6 2.3 2.6 14.7 0 17M12 3.5c-2.6 2.3-2.6 14.7 0 17" stroke-linecap="round"/>',
  agent:'<circle cx="12" cy="8" r="3.4" fill="currentColor" opacity=".16"/>'+
        '<circle cx="12" cy="8" r="3.4"/>'+
        '<path d="M5.5 20a6.5 6.5 0 0 1 13 0" stroke-linecap="round"/>',
  other:'<circle cx="12" cy="12" r="8" fill="currentColor" opacity=".14"/><circle cx="12" cy="12" r="8"/>'+
        '<circle cx="12" cy="12" r="1.4" fill="currentColor"/>',
  undo:'<path d="M4.2 9.4a8 8 0 1 1-1 5.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'+
       '<path d="M3.6 4.4v5h5" stroke-linecap="round" stroke-linejoin="round"/>'
};
function recIco(kind, cls){
  var g = recGLY[kind] || recGLY.other;
  return '<svg class="ric ' + (cls || "") + '" viewBox="0 0 24 24" fill="none" ' +
         'stroke="currentColor" stroke-width="1.7" aria-hidden="true">' + g + '</svg>';
}
function recKindIco(a){
  if (a.tool === "undo") return recIco("undo");
  return recIco(a.kind || "other");
}

// Kinds that change NOTHING. Labelling a web_search or a browser_snapshot
// "irreversible" is technically true and completely misleading — there is
// nothing to undo because nothing happened. aux_recorder.py now classifies
// these as reversible="n/a"; we also key off `kind` so rows already written to
// recorder.db with the old "no" render correctly WITHOUT a service restart.
var REC_READONLY = {read: 1, net: 1, agent: 1};
// Same list as aux_recorder.py's browser_* TOOL_KIND entries. Keyed by tool as
// well as kind so rows already in recorder.db (written when these classified
// as "other") render correctly the moment the page reloads, without waiting
// for the service restart that runs the db migration. Tools that DRIVE the
// page (browser_click/_fill/_type/_press/_dialog/_close) are deliberately NOT
// here — they change something and keep their honest "irreversible".
var REC_READONLY_TOOLS = {
  browser_snapshot: 1, browser_console: 1, browser_screenshot: 1,
  browser_take_screenshot: 1, browser_get_images: 1,
  browser_navigate: 1, browser_back: 1
};

// What the reversibility chip says, as a key: the row renderer, the run
// folder and the run's own chip all read this one function, so "the same
// reversibility" means exactly "the same chip on screen".
function recRevKey(a){
  a = a || {};
  if (a.status === "undone") return "undone";
  if (a.reversible === "n/a" || REC_READONLY[a.kind] || REC_READONLY_TOOLS[a.tool]) return "none";
  if (a.reversible === "yes") return "yes";
  if (a.reversible === "partial") return "partial";
  return "no";
}
function recRevChip(a){
  var k = recRevKey(a);
  if (k === "undone") return '<span class="rchip undone">undone</span>';
  if (k === "none") return "";                                     // no badge
  if (k === "yes")  return '<span class="rchip ok">reversible</span>';
  // the full phrase is ~130px of a 268px rail row — keep it in the tooltip
  if (k === "partial") return '<span class="rchip warn" title="partial &mdash; files only">partial</span>';
  // E01: a row the server judged not undoable (undo_why) says why on hover
  var why = a.no_snapshot_why || a.undo_why;
  return '<span class="rchip bad"' +
         (why ? ' title="' + recE(why) + '"' : "") +
         '>irreversible</span>';
}

// Does this row get an Undo button? (One answer for the renderer and the
// run folder: a row with an Undo is never folded away.)
function recCanUndo(a){
  a = a || {};
  return a.status === "done" &&
         (a.reversible === "yes" || a.reversible === "partial") &&
         a.kind !== "read" && a.kind !== "computer" &&
         a.kind !== "net" && a.kind !== "memory" && a.kind !== "agent";
}

// ---- pure row renderer (verified headless — no DOM, no throw on numbers) --
// One row, two layouts from the same markup: a single line where the card is
// wide (the Console), and — through the @container rule in recInjectCss — two
// lines where it is narrow (the 296px Agent rail): name, chip and time on the
// first, the full-width target (and the Undo at its end) under the name.
function recRowHtml(a){
  a = a || {};
  var undone = a.status === "undone";
  var canUndo = recCanUndo(a);
  var raw = a.target == null ? "" : String(a.target);
  var tgt = recE(raw || (a.tool === "undo" ? "" : "—"));
  var btn = "";
  if (canUndo){
    var lbl = (a.reversible === "partial") ? "Undo…" : "Undo";
    btn = '<button class="rec-undo" data-undo="' + recE(a.id) + '">' + lbl + '</button>';
  }
  return '<div class="rec-row' + (undone ? " is-undone" : "") + (canUndo ? " has-undo" : "") +
         '" data-id="' + recE(a.id) + '" data-kind="' + recE(a.kind) + '">' +
         '<span class="rec-ic">' + recKindIco(a) + '</span>' +
         '<div class="rec-main">' +
           '<div class="rec-top">' +
             // title: a long name can still ellipsise; hover gives it back
             // without opening the detail pane.
             '<span class="rec-tool" title="' + recE(a.tool) + '">' + recE(a.tool) + '</span>' +
             // the target is one line and ellipsises in the rail — the whole
             // command/path rides in the tooltip
             '<span class="rec-tgt' + (undone ? " struck" : "") + (raw ? "" : " empty") + '"' +
               (raw ? ' title="' + recE(raw) + '"' : "") + '>' + tgt + '</span>' +
           '</div>' +
         '</div>' +
         '<span class="rec-src">' + recE(a.source) + '</span>' +
         recRevChip(a) +
         '<span class="rec-when" title="' + recE(recClock(a.ts)) + '">' +
           recE(recWhen(a.ts)) + '</span>' +
         '<span class="rec-act">' + btn + '</span>' +
         '</div>';
}
var REC_EMPTY = '<div class="rec-empty">No recorded actions yet. Every file the ' +
                'agent writes, every command it runs, lands here with an Undo where ' +
                'one is possible.</div>';
function renderRecorderRows(actions){
  actions = actions || [];
  if (!actions.length) return REC_EMPTY;
  var h = "";
  for (var i = 0; i < actions.length; i++) h += recRowHtml(actions[i]);
  return h;
}

// ---- grouping: day groups + folded runs (pure) ---------------------------
// The rail used to be one flat list: nine near-identical "terminal echo…"
// rows, then rows from 50 days ago. Now the (already filtered) rows are cut
// into day groups — Today, Yesterday, then a date — and inside a day, 3 or
// more CONSECUTIVE rows with the same tool, the same chip and no Undo fold
// into one summary row. The open/closed state is the caller's (recState),
// so these stay pure and testable without a DOM.
var REC_FOLD_MIN = 3;
var REC_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function recPad2(n){ return (n < 10 ? "0" : "") + n; }
// local calendar day of a unix-seconds timestamp, "YYYY-MM-DD"; "undated"
// for a row with no usable ts (never thrown away, never merged into a day)
function recDayKey(ts){
  var t = Number(ts);
  if (!ts || !isFinite(t)) return "undated";
  var d = new Date(t * 1000);
  if (isNaN(d.getTime())) return "undated";
  return d.getFullYear() + "-" + recPad2(d.getMonth() + 1) + "-" + recPad2(d.getDate());
}
function recDayLabel(key, now){
  if (!key || key === "undated") return "Earlier";
  var n = new Date((now != null ? now : Date.now() / 1000) * 1000);
  if (key === recDayKey(n.getTime() / 1000)) return "Today";
  // local noon of the previous calendar day — DST-proof, unlike now - 86400
  var y = new Date(n.getFullYear(), n.getMonth(), n.getDate() - 1, 12);
  if (key === recDayKey(y.getTime() / 1000)) return "Yesterday";
  var p = key.split("-");
  var s = (REC_MONTHS[(+p[1]) - 1] || p[1]) + " " + (+p[2]);
  return (+p[0] === n.getFullYear()) ? s : s + ", " + p[0];
}
// The filter chips (All / Writes / Shell / Computer / Undone), applied BEFORE
// grouping. The server already filters (?kind=), this is the same rule on the
// client so a poll answered for the previous chip can never be grouped as
// if it were this one.
function recFilterRows(actions, f){
  actions = actions || [];
  if (!f || f === "all") return actions.slice();
  return actions.filter(function(a){
    a = a || {};
    return f === "undone" ? a.status === "undone" : a.kind === f;
  });
}
function recFoldKey(a){
  return recCanUndo(a) ? null : String((a || {}).tool) + "\u0001" + recRevKey(a);
}
function recDomId(s){ return String(s).replace(/[^A-Za-z0-9_-]/g, "_"); }
// rows of ONE day (newest first) -> [{row: a} | {run: {...}}]. A run is open
// when ANY of its member ids is in runOpen, so it stays open while it grows
// at the new end and while old rows drop out of the 50-row window.
function recFoldRuns(rows, runOpen, dayKey){
  runOpen = runOpen || {};
  var out = [], i = 0;
  while (i < rows.length){
    var k = recFoldKey(rows[i]), j = i + 1;
    if (k !== null) while (j < rows.length && recFoldKey(rows[j]) === k) j++;
    if (k !== null && j - i >= REC_FOLD_MIN){
      var rs = rows.slice(i, j), last = rs[rs.length - 1] || {};
      var ids = rs.map(function(r){ return String((r || {}).id); });
      out.push({run: {
        key: "run-" + recDomId(last.id != null ? last.id : dayKey + "-" + i),  // the oldest member: stable while the run grows
        tool: rs[0].tool, rev: recRevKey(rs[0]), rows: rs, ids: ids,
        open: ids.some(function(id){ return !!runOpen[id]; })
      }});
    } else {
      for (var m = i; m < j; m++) out.push({row: rows[m]});
    }
    i = j;
  }
  return out;
}
// opts: {filter, now (unix s), dayOpen {dayKey: bool}, runOpen {id: true},
//        stickyOpen {dayKey: true}}
function recGroupRows(actions, opts){
  opts = opts || {};
  var now = opts.now != null ? opts.now : Date.now() / 1000;
  var dayOpen = opts.dayOpen || {};
  var sticky = opts.stickyOpen || {};
  var rows = recFilterRows(actions, opts.filter);
  var byKey = {}, groups = [];
  rows.forEach(function(a){
    a = a || {};
    var k = recDayKey(a.ts), g = byKey[k];
    if (!g){ g = byKey[k] = {key: k, label: recDayLabel(k, now), rows: []}; groups.push(g); }
    g.rows.push(a);
  });
  // Newest day first whatever order the rows arrived in, and the undated
  // rows ("Earlier") last: they must never be mistaken for the newest day.
  var dated = groups.filter(function(g){ return g.key !== "undated"; })
                    .sort(function(x, y){ return x.key < y.key ? 1 : x.key > y.key ? -1 : 0; });
  groups = dated.concat(groups.filter(function(g){ return g.key === "undated"; }));
  var today = recDayKey(now), hasToday = !!byKey[today];
  groups.forEach(function(g, i){
    g.count = g.rows.length;
    // Today is open; with nothing today, the newest day is. Older days start
    // collapsed. An owner's own click (dayOpen) beats either default. E88: a
    // day the page has already shown open by default (stickyOpen) stays open
    // — "Yesterday" must not fold away under the owner when Today's first row
    // arrives.
    g.defaultOpen = hasToday ? g.key === today : i === 0;
    g.open = Object.prototype.hasOwnProperty.call(dayOpen, g.key) ? !!dayOpen[g.key]
           : (sticky[g.key] === true || g.defaultOpen);
    g.items = recFoldRuns(g.rows, opts.runOpen, g.key);
  });
  return groups;
}
// "2m ago" + "3m ago" -> "2–3m ago"; different units -> "23m–2h ago". Short on
// purpose: this label shares the run header with the tool name, the count and
// the chip in a 288px rail, and "23m ago – 2h ago" (~100px) is what used to
// push a long name's "×5" out of sight.
function recSpanWhen(newTs, oldTs){
  var a = recWhen(newTs), b = recWhen(oldTs);
  if (!a || !b || a === b) return a || b || "";
  var ma = /^(\d+)(\D.*)$/.exec(a), mb = /^(\d+)(\D.*)$/.exec(b);
  if (ma && mb && ma[2] === mb[2]) return ma[1] + "–" + mb[1] + ma[2];
  var sa = /^(\d+)(\S+) ago$/.exec(a), sb = /^(\d+)(\S+) ago$/.exec(b);
  if (sa && sb) return sa[1] + sa[2] + "–" + sb[1] + sb[2] + " ago";
  return a + " – " + b;
}
var REC_CHEV = '<svg class="rec-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
               'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
               '<polyline points="9 6 15 12 9 18"/></svg>';
// the run header's accessible name says what its chip shows, too
var REC_REV_WORD = {yes: "reversible", partial: "partial", no: "irreversible", undone: "undone", none: ""};
function recRunHtml(run){
  var rs = run.rows, a = rs[0] || {}, n = rs.length;
  var rw = REC_REV_WORD[recRevKey(a)] || "";
  var when = recSpanWhen(a.ts, (rs[n - 1] || {}).ts);
  var clock = recClock(a.ts), oclock = recClock((rs[n - 1] || {}).ts);
  var body = "";
  for (var i = 0; i < n; i++) body += recRowHtml(rs[i]);
  return '<div class="rec-run">' +
    '<button type="button" class="rec-runhd" data-run="' + recE(run.key) + '" data-kind="' + recE(a.kind) + '"' +
      ' aria-expanded="' + (run.open ? "true" : "false") + '" aria-controls="rec-' + recE(run.key) + '"' +
      ' aria-label="' + recE(a.tool) + ", " + n + " actions" + (rw ? ", " + rw : "") + (when ? ", " + recE(when) : "") + '">' +
      '<span class="rec-ic">' + recKindIco(a) + '</span>' +
      '<span class="rec-runname"><span class="rec-tool" title="' + recE(a.tool) + '">' + recE(a.tool) + '</span>' +
        '<span class="rec-runn">×' + n + '</span></span>' +
      recRevChip(a) +
      '<span class="rec-when"' + (clock ? ' title="' + recE(oclock && oclock !== clock ? oclock + " – " + clock : clock) + '"' : "") + '>' +
        recE(when) + '</span>' +
      REC_CHEV +
    '</button>' +
    '<div class="rec-runbody" id="rec-' + recE(run.key) + '"' + (run.open ? "" : " hidden") + '>' + body + '</div>' +
    '</div>';
}
// the list: day groups of rows and folded runs. opts as recGroupRows.
function renderRecorderList(actions, opts){
  var groups = recGroupRows(actions, opts);
  if (!groups.length) return REC_EMPTY;
  var h = "";
  groups.forEach(function(g){
    var did = "rec-day-" + recDomId(g.key);
    var body = "";
    g.items.forEach(function(it){ body += it.run ? recRunHtml(it.run) : recRowHtml(it.row); });
    // the button sits in an <h3> (under the card's <h2>) so a screen reader's
    // heading list reads Flight Recorder > Today, Yesterday, Sep 7 ...
    h += '<div class="rec-day"><h3 class="rec-dayh">' +
      '<button type="button" class="rec-dayhd" data-day="' + recE(recDomId(g.key)) + '"' +
        ' aria-expanded="' + (g.open ? "true" : "false") + '" aria-controls="' + recE(did) + '"' +
        ' aria-label="' + recE(g.label) + ", " + g.count + (g.count === 1 ? " action" : " actions") + '">' +
        REC_CHEV + '<span class="rec-daylbl">' + recE(g.label) + '</span>' +
        '<span class="rec-dayn">' + g.count + '</span>' +
      '</button></h3>' +
      '<div class="rec-daybody" id="' + recE(did) + '" role="group" aria-label="' + recE(g.label) + '"' +
        (g.open ? "" : " hidden") + '>' + body + '</div>' +
      '</div>';
  });
  return h;
}

// ---- one-time CSS --------------------------------------------------------
// The narrow (two-line) row layout, for a selector prefix. Used twice: inside
// the @container query (any narrow host — the Agent rail today) and, where
// container queries are missing, under #agent-rail directly.
//   line 1: [icon] [tool name ......] [chip] [time]
//   line 2:        [target / command, mono, one line ...] [Undo]
// .rec-main/.rec-top dissolve (display:contents) so the name and the target
// become grid cells of the row itself; the row stays one flat element.
function recNarrowCss(p){
  return [
    p + '.rec-row{display:grid;grid-template-columns:30px minmax(0,1fr) auto auto;' +
      'column-gap:0;row-gap:3px;align-items:center;padding:8px 4px}',
    p + '.rec-main,' + p + '.rec-top{display:contents}',
    p + '.rec-ic{width:22px}',                              // the 19px glyph, a 30px column
    p + '.rec-row>.rec-ic{grid-column:1;grid-row:1}',
    p + '.rec-row .rec-tool{grid-column:2;grid-row:1;justify-self:start;max-width:100%}',
    p + '.rec-row>.rchip{grid-column:3;grid-row:1;margin-left:8px}',
    p + '.rec-row>.rec-when{grid-column:4;grid-row:1;margin-left:8px;width:46px}',
    p + '.rec-row .rec-tgt{grid-column:2/-1;grid-row:2;min-width:0;line-height:1.45}',
    p + '.rec-row.has-undo .rec-tgt{grid-column:2/4}',
    p + '.rec-row>.rec-act{grid-column:4;grid-row:2;justify-self:end;margin-left:6px}',
    p + '.rec-row .rec-tgt.empty{display:none}',          // no "—" line under a name
    p + '.rec-src{display:none}',                           // the least load-bearing cell
    p + '.rec-undo{padding:3px 8px}',
    p + '.rec-runhd{gap:0;padding:8px 4px}',
    p + '.rec-runhd>.rec-ic{margin-right:8px}',
    p + '.rec-runhd>.rchip,' + p + '.rec-runhd>.rec-when{margin-left:8px}',
    p + '.rec-runhd>.rec-chev{margin-left:6px}',
    p + '.rec-dayhd{padding:6px 4px}',
    p + '.rec-detail,' + p + '.rec-rowerr{padding-left:34px}'
  ];
}
function recInjectCss(){
  if (typeof document === "undefined" || document.getElementById("reccss")) return;
  var s = document.createElement("style");
  s.id = "reccss";
  s.textContent = [
    // the card is the size container its rows ask about (wide Console vs.
    // narrow rail) — never the viewport, which says nothing about the rail
    '#recorder-card{container-type:inline-size;container-name:rec}',
    // Header: the title and the count share a line when they fit, and the
    // count wraps UNDER the title (indented to the title text: 13px icon +
    // 7px gap) when they do not — it used to be forced onto one nowrap line
    // and ran off the right edge of the 296px rail, clipped by the card.
    '#recorder-card .rec-head{display:flex;align-items:center;gap:7px;margin-bottom:2px;' +
      'flex-wrap:wrap;row-gap:3px;min-width:0}',
    '#recorder-card .rec-head .ic{flex:none}',
    '#recorder-card .rec-htitle{flex:1 0 auto;white-space:nowrap}',
    '#recorder-card .rec-count{margin-left:20px;flex:0 1 auto;min-width:0;font-size:12px;color:var(--muted);' +
      'font-weight:500;text-transform:none;letter-spacing:.01em;white-space:nowrap;' +
      'overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}',
    // Filters stay on ONE row — they scroll horizontally instead of wrapping
    // into a second/third line (page scrollbars are globally hidden, so this
    // reads as a quiet overflow rail rather than a scroller).
    '#recorder-card .rec-filters{display:flex;flex-wrap:nowrap;gap:5px;margin:10px 0 6px;' +
      'overflow-x:auto;overscroll-behavior-x:contain;scrollbar-width:none;padding-bottom:1px}',
    '#recorder-card .rec-filters::-webkit-scrollbar{display:none}',
    // E93: real <button>s now — the global button rule's padding, field
    // background, hover lift and hover colour must not change the chip look
    // (line-height stays the inherited one: "normal" made every chip 3.75px
    // shorter than the <span> it replaced — measured in headless Chromium)
    '.rec-fchip{font-size:12px;padding:3px 8px;border-radius:999px;border:1px solid var(--hairline);' +
      'background:var(--chip);color:var(--muted);cursor:pointer;user-select:none;' +
      'white-space:nowrap;flex:0 0 auto;margin:0;line-height:inherit;display:inline-block}',
    '#recorder-card .rec-fchip:hover{transform:none;border-color:var(--hairline);color:var(--muted)}',
    '.rec-fchip.on{background:var(--iris);border-color:var(--iris);color:var(--iris-ink);font-weight:600}',
    '#recorder-card .rec-fchip.on:hover{border-color:var(--iris);color:var(--iris-ink)}',
    '#recorder-card .rec-fchip:focus-visible{outline:2px solid var(--iris);outline-offset:1px}',
    '.rec-banner{font-size:12px;line-height:1.5;color:var(--ink);background:color-mix(in srgb,var(--warn) 16%,transparent);' +
      'border:1px solid color-mix(in srgb,var(--warn) 45%,transparent);border-radius:10px;padding:9px 11px;margin:4px 0 6px;user-select:text}',
    '.rec-banner code{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:11px;user-select:all}',
    '.rec-banner div+div{margin-top:5px}',
    '#rec-list{display:flex;flex-direction:column;gap:2px;margin-top:4px}',
    // overflow:hidden is the backstop for the original bug: whatever the mix of
    // badge/time/Undo, a long name now clips at the row edge instead of being
    // painted on top of the cell next to it.
    '.rec-row{display:flex;align-items:center;gap:10px;padding:8px 6px;border-radius:9px;cursor:pointer;' +
      'border-bottom:1px solid var(--hairline);overflow:hidden}',
    '.rec-row:hover{background:var(--glass-2)}',
    // the Undo flash (recFlash): an overlay BEHIND the row's content, so the
    // text stays crisp, faded by opacity alone. Its resting opacity is 0, so
    // either reduce kill rule (animation:none or ~0 duration) shows nothing.
    '.rec-row.rec-flash{position:relative;isolation:isolate}',
    '.rec-row.rec-flash::after{content:"";position:absolute;inset:0;z-index:-1;border-radius:inherit;' +
      'pointer-events:none;background:color-mix(in srgb,var(--iris) 30%,transparent);opacity:0;' +
      'animation:rec-flash var(--d-micro,150ms) var(--press,ease-out)}',
    '@keyframes rec-flash{from{opacity:1}to{opacity:0}}',
    '@media (prefers-reduced-motion:reduce){.rec-row.rec-flash::after{animation:none}}',
    '.rec-ic{width:26px;height:26px;flex:none;display:inline-flex;align-items:center;justify-content:center;color:var(--iris)}',
    '.rec-ic .ric{width:19px;height:19px}',
    ':is(.rec-row,.rec-runhd)[data-kind="shell"] .rec-ic{color:var(--warn)}',
    ':is(.rec-row,.rec-runhd)[data-kind="computer"] .rec-ic{color:var(--bad)}',
    ':is(.rec-row,.rec-runhd)[data-kind="read"] .rec-ic,:is(.rec-row,.rec-runhd)[data-kind="net"] .rec-ic,' +
      ':is(.rec-row,.rec-runhd)[data-kind="agent"] .rec-ic{color:var(--faint)}',
    // The tool name used to be an unshrinkable nowrap span inside a min-width:0
    // parent with no overflow guard, so "browser_console" simply painted OVER
    // the badge to its right. Now: the text column owns the free space and
    // ellipsises, the badge/meta columns are flex:none.
    '.rec-main{flex:1 1 auto;min-width:44px;overflow:hidden}',   // never collapses to nothing
    '.rec-top{display:flex;align-items:baseline;gap:8px;min-width:0}',
    // flex-shrink:0 + max-width:100% => the target path gives up its width
    // first and the tool name only ellipsises when it alone overflows the row
    // (with 0 1 auto both shrank proportionally and "web_search" became "we…").
    '.rec-tool{font-weight:660;font-size:12.5px;white-space:nowrap;' +
      'flex:0 0 auto;max-width:100%;min-width:0;overflow:hidden;text-overflow:ellipsis}',
    '.rec-tgt{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:11.5px;color:var(--muted);' +
      'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 0;min-width:0}',
    '.rec-tgt.struck{text-decoration:line-through;opacity:.7}',
    '.rec-src{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);' +
      'flex:0 1 auto;max-width:52px;min-width:0;text-align:right;overflow:hidden;' +
      'text-overflow:ellipsis;white-space:nowrap}',
    // Empty meta cells were still eating a fixed column AND a 10px gap each —
    // in the 296px Agent rail that left ~16px for the name, which is the real
    // reason the tool name painted over the badge. Collapse them instead.
    '.rec-src:empty,.rec-act:empty{display:none}',
    '.rchip{font-size:12px;font-weight:600;padding:3px 8px;border-radius:999px;white-space:nowrap;flex:0 0 auto}',
    '.rchip.ok{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}',
    '.rchip.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}',
    '.rchip.bad{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)}',
    '.rchip.undone{background:color-mix(in srgb,var(--iris) 20%,transparent);color:var(--iris)}',
    '.rec-when{font-size:12px;color:var(--muted);flex:0 0 auto;width:58px;text-align:right;' +
      'white-space:nowrap;font-variant-numeric:tabular-nums}',
    '.rec-act{flex:0 0 auto;width:auto;min-width:0;text-align:right}',
    '.rec-undo{position:relative;font-size:12px;font-weight:600;padding:4px 10px;min-height:24px;border-radius:8px;cursor:pointer;' +
      'border:1px solid color-mix(in srgb,var(--iris) 55%,transparent);background:transparent;color:var(--iris)}',
    // a 24px plate with a 40px target: the hit area grows into the row's own
    // 8px padding, never over the next row
    '.rec-undo::after{content:"";position:absolute;inset:-8px -4px}',
    '.rec-undo:hover{background:color-mix(in srgb,var(--iris) 14%,transparent)}',
    // ---- day groups and folded runs: flat rows, real <button> headers -----
    '#rec-list .rec-daybody{display:flex;flex-direction:column;gap:2px}',
    '#rec-list .rec-daybody[hidden],#rec-list .rec-runbody[hidden]{display:none}',
    ':is(.rec-dayhd,.rec-runhd){width:100%;min-height:40px;margin:0;border:0;border-radius:9px;' +
      'background:none;color:inherit;font:inherit;text-align:left;cursor:pointer;' +
      'transition-property:background-color,color;transition-duration:var(--d-micro,150ms);' +
      'transition-timing-function:ease-out}',
    '#rec-list :is(.rec-dayhd,.rec-runhd):hover{background:var(--glass-2);transform:none;border-color:transparent;color:inherit}',
    // rows, not controls: no gel squash, and the ring sits inside the row
    '#rec-list :is(.rec-dayhd,.rec-runhd):active{scale:none}',
    '#rec-list :is(.rec-dayhd,.rec-runhd):focus-visible{outline:2px solid var(--iris);outline-offset:-2px}',
    '.rec-dayhd{display:flex;align-items:center;gap:7px;padding:6px 6px;font-size:12px;font-weight:600;' +
      'letter-spacing:.02em;color:var(--muted)}',
    '#rec-list .rec-dayhd:hover{color:var(--ink)}',
    '.rec-daylbl{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.rec-dayn{flex:none;font-size:12px;font-weight:500;color:var(--muted);font-variant-numeric:tabular-nums}',
    '.rec-day+.rec-day{margin-top:4px}',
    '#rec-list .rec-dayh{display:block;margin:0;padding:0;border:0;background:none;font:inherit;letter-spacing:inherit;text-transform:none}',
    '.rec-runhd{display:flex;align-items:center;gap:10px;padding:8px 6px;border-bottom:1px solid var(--hairline)}',
    '.rec-runname{flex:1 1 auto;min-width:0;display:flex;align-items:baseline;gap:6px;overflow:hidden}',
    // the NAME gives way (ellipsis, full name in its title), never the count:
    // ".rec-tool" is flex:0 0 auto elsewhere, which pushed "×5" out of the clip
    '.rec-runname .rec-tool{flex:0 1 auto;min-width:0}',
    '.rec-runn{flex:none;font-size:12px;font-weight:600;color:var(--muted);font-variant-numeric:tabular-nums}',
    '.rec-runhd .rec-when{width:auto;min-width:46px}',
    // the members of an open run hang off a hairline under the run's icon
    '.rec-runbody{margin-left:13px;padding-left:6px;border-left:1px solid var(--hairline)}',
    '.rec-chev{width:12px;height:12px;flex:none;color:var(--faint);transition-property:transform;' +
      'transition-duration:var(--d-micro,150ms);transition-timing-function:var(--press,ease-out)}',
    '[aria-expanded="true"]>.rec-chev{transform:rotate(90deg)}',
    // both reduce switches: the chevron and the hover land at once
    '@media (prefers-reduced-motion:reduce){.rec-chev,.rec-dayhd,.rec-runhd{transition:none}}',
    ':root[data-reduce] :is(.rec-chev,.rec-dayhd,.rec-runhd){transition:none}',
    // the two-line layout wherever the card is narrow …
    '@container rec (max-width:520px){' + recNarrowCss("#recorder-card ").join("") + '}',
    // … and, in an engine without container queries, in the rail at least
    // (the only narrow host there is) — never worse than the old one-liner
    '@supports not (container-type:inline-size){' + recNarrowCss("#agent-rail #recorder-card ").join("") + '}',
    '.rec-rowerr{font-size:12px;color:var(--bad);padding:2px 6px 8px 42px}',
    '.rec-rowerr.note{color:var(--muted);user-select:text;overflow-wrap:anywhere}',
    '.rec-detail{padding:10px 6px 12px 42px;font-size:12px;color:var(--muted);border-bottom:1px solid var(--hairline)}',
    '.rec-detail .k{color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.05em;font-size:11px;margin:8px 0 3px}',
    '.rec-detail pre{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:11px;line-height:1.5;' +
      'white-space:pre-wrap;word-break:break-word;background:var(--glass-2);border:1px solid var(--hairline);' +
      'border-radius:8px;padding:8px 10px;margin:0;max-height:280px;overflow:auto}',
    '.rec-detail .diff .add{color:var(--ok)}.rec-detail .diff .del{color:var(--bad)}',
    '.rec-empty,.rec-hint{font-size:12px;color:var(--muted);line-height:1.55;padding:8px 2px}',
    '.rec-hint.bad{color:var(--bad)}',
    // Retention: one quiet footer line under the feed. It must READ as
    // settings, not as another row of the log, so it sits below a hairline in
    // --faint, and it never wraps into two lines in the 320px Agent rail.
    '#recorder-card .rec-retain{display:flex;align-items:center;gap:8px;margin-top:10px;' +
      'padding-top:9px;border-top:1px solid var(--hairline);font-size:12px;' +
      'color:var(--muted);line-height:1.5;flex-wrap:wrap}',
    '#recorder-card .rec-retain select{font:inherit;font-size:12px;color:var(--ink);' +
      'background:var(--chip);border:1px solid var(--hairline);border-radius:8px;' +
      'padding:5px 8px;min-height:32px;cursor:pointer}',
    '#recorder-card .rec-retain .rr-note{flex:1 1 100%;color:var(--muted);font-size:12px}',
    '#recorder-card .rec-retain .rr-note.bad{color:var(--bad)}',
    '#recorder-card .skel i{height:26px}'
  ].join("");
  document.head.appendChild(s);
}

// ---- card injection ------------------------------------------------------
function recEnsureCard(){
  if (typeof document === "undefined") return null;
  var card = document.getElementById("recorder-card");
  if (card) return card;
  var host = document.getElementById("view-console");
  if (!host) return null;
  recInjectCss();
  card = document.createElement("section");
  card.className = "card glass";
  card.id = "recorder-card";
  card.style.gridColumn = "1/3";
  card.innerHTML =
    '<h2 class="rec-head"><svg class="ic" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="1.7">' + recGLY.box + '</svg>' +
      '<span class="rec-htitle">Flight Recorder</span><span class="rec-count" id="rec-count"></span></h2>' +
    '<div class="body"><div id="rec-banner"></div>' +
    '<div class="rec-filters" id="rec-filters"></div>' +
    '<div id="rec-list"><div class="skel"><i></i><i></i><i></i></div></div>' +
    '<div class="rec-retain" id="rec-retain" hidden></div></div>';
  host.insertBefore(card, host.firstChild);
  return card;
}

var REC_FILTERS = [["all", "All"], ["write", "Writes"], ["shell", "Shell"],
                   ["computer", "Computer"], ["undone", "Undone"]];

// E93: the chips are real <button type="button" aria-pressed> in a labelled
// group (they were <span>s: no tab stop, no Enter/Space, no pressed state).
// Built ONCE; a press or a poll only flips their pressed state in place, so
// keyboard focus on a chip survives both. A held key's auto-repeat is
// ignored (CLAUDE.md keyboard invariant).
function recRenderFilters(){
  var el = document.getElementById("rec-filters");
  if (!el) return;
  var have = el.querySelectorAll("[data-f]");
  if (have.length !== REC_FILTERS.length){
    el.setAttribute("role", "group");
    el.setAttribute("aria-label", "Show");
    el.innerHTML = REC_FILTERS.map(function(f){
      return '<button type="button" class="rec-fchip" data-f="' + f[0] + '" aria-pressed="false">' +
             recE(f[1]) + '</button>';
    }).join("");
    [].forEach.call(el.querySelectorAll("[data-f]"), function(b){
      b.addEventListener("click", function(){ recSetFilter(b.getAttribute("data-f")); });
      b.addEventListener("keydown", function(ev){
        if (ev.repeat && (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar")) ev.preventDefault();
      });
    });
    have = el.querySelectorAll("[data-f]");
  }
  [].forEach.call(have, function(b){
    var on = recState.filter === b.getAttribute("data-f");
    if (b.classList.contains("on") !== on) b.classList.toggle("on", on);
    if (b.getAttribute("aria-pressed") !== String(on)) b.setAttribute("aria-pressed", String(on));
  });
}
function recSetFilter(f){
  if (recState.filter === f) return;
  recState.filter = f;
  recState.expanded = null;
  recRenderFilters();
  loadRecorder();
}

// ---- fetch + render ------------------------------------------------------
async function loadRecorder(){
  var card = recEnsureCard();
  if (!card) return;
  recRenderFilters();
  recLoadRetention();          // own cadence (60s), not the 3s console poll
  var url = "/api/recorder?limit=50";
  if (recState.filter !== "all") url += "&kind=" + encodeURIComponent(recState.filter);
  var d;
  try {
    d = await (await fetch(url, {cache: "no-store"})).json();
  } catch (e) {
    recState.err = true;
    if (!recState.last) {                 // never blank; keep any prior render
      var lst0 = document.getElementById("rec-list");
      if (lst0) lst0.innerHTML = '<div class="rec-hint bad">recorder feed unreachable &mdash; retrying</div>';
      recState.listHtml = null;
    } else {
      recAppendHint("recorder feed unreachable — retrying");
    }
    return;
  }
  recState.err = false;
  if (d && d.recorder_ok === false){
    var lst1 = document.getElementById("rec-list");
    if (lst1) lst1.innerHTML = '<div class="rec-hint bad">recorder unavailable: ' +
      recE(d.error || "not loaded") + '</div>';
    recState.listHtml = null;
    return;
  }
  recState.last = d;
  recState.ckpt = !!(d && d.checkpoints_enabled);
  recRender();
}

function recRender(){
  var d = recState.last || {};
  var actions = d.actions || [];
  var lst = document.getElementById("rec-list");
  if (!lst) return;

  var cnt = document.getElementById("rec-count");
  if (cnt){
    var c = d.counts || {};
    cnt.textContent = (c.reversible || 0) + " reversible · " + (c.undone || 0) + " undone";
  }

  var ban = document.getElementById("rec-banner");
  if (ban){
    var lines = recBannerLines(d);
    ban.innerHTML = lines.length
      ? '<div class="rec-banner"><div>' + lines.join("</div><div>") + '</div></div>'
      : "";
  }

  // C99.1: this runs on every 3 s poll, and rebuilding the list dropped
  // keyboard focus from an Undo button onto <body> each time. Nothing new
  // and focus inside the list: leave the DOM alone (no rebuild, no detail
  // refetch, nothing re-announced). Otherwise rebuild, then put focus back
  // on the same control — or on its row once the Undo is gone, or on the same
  // day / run header.
  recKeepRunsOpen(actions);
  recStickDays(actions);
  var html = renderRecorderList(actions, recViewOpts());
  // E33: "nothing new" leaves the list alone WHATEVER has focus. It used to
  // need keyboard focus inside the list, and a mouse click on a row focuses
  // nothing, so an open detail pane was torn down, refetched (a checkpoint
  // diff subprocess on the server) and scrolled back to the top every 3 s.
  var fk = recFocusKey(lst);
  if (html === recState.listHtml && !Object.keys(recState.rowErr || {}).length){
    var lh = lst.querySelector(".rec-livehint");     // the feed is back
    if (lh && lh.parentNode) lh.parentNode.removeChild(lh);
    recSyncPane(lst);
    return;
  }
  // a rebuild keeps the open pane: the same node goes back under its row
  var keep = recTakePane(lst);
  lst.innerHTML = html;
  recState.listHtml = html;

  // re-open an expanded detail pane after a refresh
  [].forEach.call(lst.querySelectorAll(".rec-row"), function(row){
    var id = row.getAttribute("data-id");
    row.addEventListener("click", function(ev){
      if (ev.target.closest(".rec-undo")) return;   // button handled below
      recToggleDetail(id, row);
    });
  });
  [].forEach.call(lst.querySelectorAll("[data-undo]"), function(b){
    b.addEventListener("click", function(ev){
      ev.stopPropagation();
      recUndo(b.getAttribute("data-undo"), false);
    });
  });
  // Day and run headers are real <button>s: Enter and Space are the
  // browser's own activation, and toggling never rebuilds the list, so focus
  // stays on the header the owner pressed.
  [].forEach.call(lst.querySelectorAll(".rec-dayhd"), function(b){
    b.addEventListener("click", function(){ recToggleDay(b); });
  });
  [].forEach.call(lst.querySelectorAll(".rec-runhd"), function(b){
    b.addEventListener("click", function(){ recToggleRun(b); });
  });
  if (recState.expanded != null){
    var er = lst.querySelector('.rec-row[data-id="' + recState.expanded + '"]');
    if (er && keep && String(keep.id) === String(recState.expanded)){
      er.parentNode.insertBefore(keep.node, er.nextSibling);
      keep.scroll.forEach(function(s){ try { s[0].scrollTop = s[1]; } catch (e) {} });
      recSyncPane(lst);                 // refetched only if ITS row changed
    } else if (er) recToggleDetail(recState.expanded, er, true);
  }
  recShowRowErrors();
  recRefocus(lst, fk);
  recEnterNew(lst);
}

// ---- day / run open state ------------------------------------------------
// Days: the owner's own toggles, remembered per viewer (localStorage, keyed by
// the calendar day, so "Today" closed today is still that day tomorrow).
// Anything unreadable is simply the defaults. Runs: for the page's life.
var REC_DAYS_KEY = "hermes_rec_days";
var REC_DAYS_MAX = 60, REC_RUNS_MAX = 600;
function recDays(){
  if (recState.dayOpen) return recState.dayOpen;
  var o = {};
  try {
    var raw = (typeof localStorage !== "undefined" && localStorage) ? localStorage.getItem(REC_DAYS_KEY) : null;
    var v = raw ? JSON.parse(raw) : null;
    if (v && typeof v === "object" && !Array.isArray(v))
      Object.keys(v).forEach(function(k){
        if (/^(\d{4}-\d{2}-\d{2}|undated)$/.test(k) && typeof v[k] === "boolean") o[k] = v[k];
      });
  } catch (e) { o = {}; }
  recState.dayOpen = o;
  return o;
}
function recSetDay(key, open){
  var o = recDays();
  delete o[key]; o[key] = !!open;                  // newest last, so the cap drops the oldest
  var ks = Object.keys(o);
  while (ks.length > REC_DAYS_MAX) delete o[ks.shift()];
  try { if (typeof localStorage !== "undefined" && localStorage) localStorage.setItem(REC_DAYS_KEY, JSON.stringify(o)); }
  catch (e) {}
}
function recSetRun(ids, open){
  var m = recState.runOpen || (recState.runOpen = {});
  (ids || []).forEach(function(id){ if (open) m[id] = 1; else delete m[id]; });
  var ks = Object.keys(m);
  while (ks.length > REC_RUNS_MAX) delete m[ks.shift()];
}
// an open run that grew (a new row of the same tool on top) stays open: its
// new members join the open set before the render that shows them
function recKeepRunsOpen(actions){
  try {
    recGroupRows(actions, recViewOpts()).forEach(function(g){
      g.items.forEach(function(it){ if (it.run && it.run.open) recSetRun(it.run.ids, true); });
    });
  } catch (e) {}
}
function recViewOpts(){
  return {filter: recState.filter, now: Date.now() / 1000,
          dayOpen: recDays(), runOpen: recState.runOpen || {},
          stickyOpen: recState.dayStick || {}};
}
// E88: every day this render shows open by default stays open for the page's
// life, and so does the day holding the open detail pane or keyboard focus.
// Only the owner's own toggle (dayOpen) closes one.
function recStickDays(actions){
  var st = recState.dayStick || (recState.dayStick = {});
  try {
    recGroupRows(actions, recViewOpts()).forEach(function(g){
      if (g.open && g.defaultOpen) st[g.key] = true;
    });
    var lst = (typeof document !== "undefined") ? document.getElementById("rec-list") : null;
    var fk = lst ? recFocusKey(lst) : null;
    [recState.expanded, fk && (fk.undo != null ? fk.undo : fk.row)].forEach(function(id){
      var a = id != null ? recFind(id) : null;
      if (a) st[recDayKey(a.ts)] = true;
    });
  } catch (e) {}
}
function recRunIds(btn){
  var box = btn && btn.parentNode && btn.parentNode.querySelector ? btn.parentNode.querySelector(".rec-runbody") : null;
  if (!box) return [];
  return [].map.call(box.querySelectorAll(".rec-row"), function(r){ return r.getAttribute("data-id"); });
}
// The toggles flip the header and its body IN PLACE — no rebuild, so focus
// and any open detail pane stay put — and then record what the list now
// looks like, so the next poll's "nothing changed" check still holds.
function recFlip(btn){
  var open = btn.getAttribute("aria-expanded") !== "true";
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  var box = document.getElementById(btn.getAttribute("aria-controls"));
  if (box) box.hidden = !open;
  return {open: open, box: box};
}
function recToggleDay(btn){
  var f = recFlip(btn);
  recSetDay(btn.getAttribute("data-day"), f.open);
  recSyncListHtml();
  if (f.open && f.box) recReveal(f.box);
}
function recToggleRun(btn){
  var ids = recRunIds(btn);
  var f = recFlip(btn);
  recSetRun(ids, f.open);
  recSyncListHtml();
  if (f.open && f.box) recReveal(f.box);
}
function recSyncListHtml(){
  try { recState.listHtml = renderRecorderList((recState.last || {}).actions || [], recViewOpts()); }
  catch (e) { recState.listHtml = null; }
}
function recHiddenIn(el, stop){
  for (var n = el && el.parentNode; n && n !== stop; n = n.parentNode)
    if (n.hasAttribute && n.hasAttribute("hidden")) return true;
  return false;
}
// The one entrance the collapsibility adds: the rows an owner's click just
// revealed fade in as one quick stagger through the motion runtime (opacity
// only, never a height). Either reduce switch lands them at once (the runtime
// decides). Their "rec:<id>" keys are spent here so a later poll does not
// replay them. Polls and every other repaint reveal nothing, so nothing moves.
function recReveal(box){
  var H = recHA();
  if (!H || typeof H.stagger !== "function" || !box || !box.querySelectorAll) return;
  try {
    var shown = [].filter.call(box.querySelectorAll(".rec-row,.rec-runhd"), function(r){ return !recHiddenIn(r, box); });
    shown.forEach(function(r){
      var id = r.getAttribute("data-id");
      if (id != null && typeof H.once === "function") H.once("rec:" + id);
    });
    if (shown.length) H.stagger(shown, "fade");
  } catch (e) {}
}

// Only rows this page has never shown move (§8.11, E13). This render runs on
// every 3 s poll and used to re-fade EVERY row each time. Now each id spends
// its "rec:<id>" key the first time it is painted, even while reduced, hidden
// or quiet (the Agent rail's poll runs inside HA().quiet), so a poll, a filter
// switched back, or an Undo that rebuilds the list never replays an entrance
// on a row already on screen. The new rows enter as one list: the runtime
// animates at most 12 (T.stagger.max) and lands the rest at once. A row inside
// a collapsed day or run is not painted yet: it keeps its key until an
// owner's click reveals it (recReveal), and a run's summary row never enters.
function recEnterNew(lst){
  var H = recHA();
  if (!H || typeof H.once !== "function" || typeof H.stagger !== "function") return;
  var fresh = [];
  try {
    [].forEach.call(lst.querySelectorAll(".rec-row"), function(row){
      if (recHiddenIn(row, lst)) return;
      var id = row.getAttribute("data-id");
      if (id != null && H.once("rec:" + id)) fresh.push(row);
    });
    if (fresh.length) H.stagger(fresh, "fade");
  } catch (e) {}
}

// Where keyboard focus sits inside #rec-list, as something that survives a
// rebuild: {undo: id} on an Undo button, {day: key} / {run: key, ids} on a
// group header, {row: id} anywhere else in a row (C99.1 — index.html's
// mmFocusKey/mmRefocus do the same for the model menu).
function recHas(el, c){ return !!(el && el.classList && el.classList.contains(c)); }
function recFocusKey(lst){
  var a = (typeof document !== "undefined") ? document.activeElement : null;
  if (!a || a === lst || typeof lst.contains !== "function" || !lst.contains(a)) return null;
  var u = a.getAttribute ? a.getAttribute("data-undo") : null;
  if (u != null) return {undo: u};
  if (recHas(a, "rec-dayhd")) return {day: a.getAttribute("data-day")};
  if (recHas(a, "rec-runhd")) return {run: a.getAttribute("data-run"), ids: recRunIds(a)};
  var row = a.closest ? a.closest(".rec-row") : null;
  return {row: row ? row.getAttribute("data-id") : null};
}
function recRefocus(lst, k){
  if (!k) return;
  var t = null, id = (k.undo != null) ? k.undo : k.row;
  // header keys are recDomId()-safe ([A-Za-z0-9_-]), so they quote as-is
  if (k.day != null) t = lst.querySelector('.rec-dayhd[data-day="' + k.day + '"]');
  if (k.run != null){
    t = lst.querySelector('.rec-runhd[data-run="' + k.run + '"]');
    // the run lost its oldest member (it scrolled out of the window): find it
    // by any member it still has; if it fell apart, stay on that member's row
    for (var i = 0; !t && k.ids && i < k.ids.length; i++){
      var r = lst.querySelector('.rec-row[data-id="' + k.ids[i] + '"]');
      var run = r && r.closest ? r.closest(".rec-run") : null;
      t = run ? run.querySelector(".rec-runhd") : null;
      if (!t && r) id = k.ids[i];
    }
  }
  if (k.undo != null) t = lst.querySelector('[data-undo="' + k.undo + '"]');
  if (!t && id != null){
    // the Undo is gone (it just ran, or the row stopped being undoable):
    // stay on its row rather than fall out of the list onto <body>
    t = lst.querySelector('.rec-row[data-id="' + id + '"]');
    if (t && t.setAttribute && !(t.hasAttribute && t.hasAttribute("tabindex")))
      t.setAttribute("tabindex", "-1");
  }
  if (t && typeof t.focus === "function") try { t.focus({preventScroll: true}); } catch (e) {}
}

// Pure: /api/recorder payload -> banner lines (HTML, static text only).
// C114: config.yaml's checkpoints.enabled reaches the terminal CLI and nothing
// else, so a green key used to hide a recorder that could never offer an Undo
// for a dashboard chat (hermes serve snapshots only with HERMES_TUI_CHECKPOINTS
// in its LaunchAgent environment) or a Telegram turn (the gateway never does).
// `snapshots` says what each surface can do; an older server without it gets
// the old single line.
function recBannerLines(d){
  d = d || {};
  var s = d.snapshots, lines = [];
  if (!s || typeof s !== "object"){
    if (d.checkpoints_enabled === false)
      lines.push('Snapshots are off. Run <code>hermes config set checkpoints.enabled true</code> ' +
                 'and restart the agent services — until then nothing new is undoable.');
    return lines;
  }
  var by = s.by_source || {}, hub = by.hub || {};
  // the LaunchAgent is the authority; with it unreadable, fall back to evidence
  if (s.serve === false || (s.serve == null && (hub.rows || 0) > 0 && !hub.with_snapshot))
    lines.push('Dashboard chats are not snapshotted, so their file changes cannot be undone. ' +
               'The agent service (hermes serve) takes snapshots only when started with ' +
               '<code>HERMES_TUI_CHECKPOINTS=1</code> — <code>checkpoints.enabled</code> in ' +
               'config.yaml reaches the terminal CLI only. Re-run <code>./install-services.sh</code> ' +
               'to turn them on.');
  var gw = (s.gateway || []).map(function(k){
    var n = String(k).replace(/_/g, " ");
    return recE(n.charAt(0).toUpperCase() + n.slice(1));
  });
  if (gw.length)
    lines.push('Undo is unavailable for ' + gw.join(", ") + ' turns: the messaging gateway ' +
               'never takes snapshots, so what the agent changes from there cannot be rolled back.');
  if (s.cli === false)
    lines.push('Terminal sessions are not snapshotted. Run ' +
               '<code>hermes config set checkpoints.enabled true</code> to turn them on.');
  return lines;
}

function recAppendHint(msg){
  var lst = document.getElementById("rec-list");
  if (!lst || lst.querySelector(".rec-livehint")) return;
  var h = document.createElement("div");
  h.className = "rec-hint rec-livehint";
  h.textContent = msg;
  lst.appendChild(h);
}

// ---- retention -----------------------------------------------------------
// recorder.db used to grow forever. The window lives in settings.json
// (recorder.retain_days) and is swept once a day by the reconciler; this is
// the control for it. Fetched ONCE, not on the 3s console poll — the feed
// refreshes constantly and a settings row that re-renders under the cursor
// is how a select gets closed mid-choice.
var recRetain = {data: null, loaded: false, busy: false, err: "", at: 0};
var REC_RETAIN_TTL_MS = 60000;      // the counts move slowly; the feed does not

var REC_RETAIN_CHOICES = [14, 30, 60, 90, 180, 365];

function recRetainLabel(days){
  if (!days) return "forever";
  return days + " day" + (days === 1 ? "" : "s");
}

// Pure: last_sweep (server.py's _rec_retain_state, or null) -> one line for
// the footer. The reconciler thread sweeps once a day (recorder_loop) — if it
// ever dies, retention just silently stops working with nothing to say so.
// Surfacing WHEN the last sweep ran (not just what it did) is what makes a
// dead thread visible: "last swept 6 days ago" next to a once-a-day policy is
// the tell, long before the row count itself looks obviously wrong.
function recLastSweepNote(ls){
  if (!ls || !ls.ts) return "No retention sweep has run yet.";
  var when = recWhen(ls.ts) || "recently";
  if (ls.ok === false) {
    return "Last sweep " + when + " failed: " + (ls.error || "unknown error") + ".";
  }
  if (ls.forever) return "Last checked " + when + " — retention is off.";
  var n = ls.deleted || 0;
  return "Last swept " + when + " — removed " + n + " row" + (n === 1 ? "" : "s") + ".";
}

// Pure: (payload) -> the control's inner HTML. Exported for the headless
// render harness, like renderRecorderRows.
function renderRecorderRetention(d){
  if (!d || d.ok === false) {
    return '<span class="rr-note bad">' +
      recE((d && d.error) || "retention settings unavailable") + "</span>";
  }
  var cur = (typeof d.retain_days === "number") ? d.retain_days : 90;
  var min = d.min_days || 14;
  var choices = REC_RETAIN_CHOICES.filter(function(n){ return n >= min; });
  if (cur && choices.indexOf(cur) < 0) choices.push(cur);
  choices.sort(function(a, b){ return a - b; });

  var opts = choices.map(function(n){
    return '<option value="' + n + '"' + (n === cur ? " selected" : "") + ">" +
           recE(recRetainLabel(n)) + "</option>";
  }).join("") +
    '<option value="0"' + (cur ? "" : " selected") + ">" + recE("keep forever") + "</option>";

  var next = d.next_sweep || null;
  var note;
  if (!cur) {
    note = "Nothing is ever deleted. The database grows with every tool call.";
  } else if (next && next.would_delete) {
    note = "The next daily sweep removes " + next.would_delete + " row" +
           (next.would_delete === 1 ? "" : "s") + ".";
  } else {
    note = "Nothing is old enough to sweep yet.";
  }
  note += " Undone actions and anything still holding a snapshot are kept" +
          " whatever their age — undo material outlives the window.";
  if (d.clamped) {
    note = min + " days is the minimum, to match how long undo material is" +
           " kept. " + note;
  }

  return '<label for="rec-retain-sel">Keep history</label>' +
    '<select id="rec-retain-sel"' + (recRetain.busy ? " disabled" : "") + ">" +
    opts + "</select>" +
    '<span>' + recE(String(d.total == null ? "" : d.total)) +
    (d.total == null ? "" : (d.total === 1 ? " row" : " rows")) + "</span>" +
    '<span class="rr-note' + (recRetain.err ? " bad" : "") + '">' +
    recE(recRetain.err || note) + "</span>" +
    '<span class="rr-note' + (d.last_sweep && d.last_sweep.ok === false ? " bad" : "") +
    '">' + recE(recLastSweepNote(d.last_sweep)) + "</span>";
}

function recRenderRetention(){
  var el = document.getElementById("rec-retain");
  if (!el) return;
  if (!recRetain.loaded) { el.hidden = true; return; }
  // Never rebuild the row while the user is IN the select — replacing the
  // element mid-choice closes the dropdown and loses the click.
  var open = document.getElementById("rec-retain-sel");
  if (open && document.activeElement === open && !recRetain.busy) return;
  el.hidden = false;
  el.innerHTML = renderRecorderRetention(recRetain.data);
  var sel = document.getElementById("rec-retain-sel");
  if (sel) sel.addEventListener("change", function(){ recSetRetention(sel.value); });
}

async function recLoadRetention(force){
  var fresh = recRetain.loaded &&
              (Date.now() - recRetain.at) < REC_RETAIN_TTL_MS;
  if (fresh && !force) return;
  try {
    var d = await (await fetch("/api/recorder/retention", {cache: "no-store"})).json();
    recRetain.data = d;
    recRetain.loaded = true;
    recRetain.at = Date.now();
    recRetain.err = "";
  } catch (e) {
    // A card that cannot read its own setting keeps whatever it last read and
    // shows nothing at all on a cold start: the feed above it is the point of
    // the card, and a broken settings row must not eat it.
    if (!recRetain.loaded) return;
  }
  recRenderRetention();
}

async function recSetRetention(v){
  if (recRetain.busy) return;
  recRetain.busy = true;
  recRenderRetention();
  try {
    var r = await fetch("/api/recorder/retention", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({retain_days: Number(v)})
    });
    var d = await r.json();
    recRetain.busy = false;
    if (d && d.ok === false) { recRetain.err = d.error || "could not save"; }
    else {
      recRetain.err = ""; recRetain.data = d;
      recRetain.loaded = true; recRetain.at = Date.now();
    }
  } catch (e) {
    recRetain.busy = false;
    recRetain.err = "could not save — the dashboard did not answer";
  }
  recRenderRetention();
}

// ---- detail pane (lazy diff) ---------------------------------------------
// E33: a pane remembers which row it shows and what that row looked like when
// it was fetched (`__recSig`), so a poll can keep it — same node, same scroll —
// and refetch only when that row really changed.
function recRowSig(a){
  try { return JSON.stringify(a || null); } catch (e) { return String(Math.random()); }
}
function recPaneFor(row){
  var n = row && row.nextSibling;
  while (n && n.classList && n.classList.contains("rec-rowerr")) n = n.nextSibling;  // an error line may sit between
  return (n && n.classList && n.classList.contains("rec-detail")) ? n : null;
}
// Lift the open pane out of the list before a rebuild: {id, node, scroll}.
function recTakePane(lst){
  if (recState.expanded == null || !lst || !lst.querySelector) return null;
  var p = lst.querySelector(".rec-detail");
  if (!p || p.getAttribute("data-for") !== String(recState.expanded)) return null;
  var scroll = [p].concat([].slice.call(p.querySelectorAll ? p.querySelectorAll("pre") : []))
    .map(function(n){ return [n, n.scrollTop || 0]; });
  if (p.parentNode) p.parentNode.removeChild(p);
  return {id: recState.expanded, node: p, scroll: scroll};
}
// After a poll: refetch the open pane only if its row's data changed.
function recSyncPane(lst){
  if (recState.expanded == null || !lst || !lst.querySelector) return;
  var p = lst.querySelector(".rec-detail");
  if (!p || p.getAttribute("data-for") !== String(recState.expanded)) return;
  var sig = recRowSig(recFind(recState.expanded));
  if (p.__recSig === sig) return;
  p.__recSig = sig;
  recDetail(recState.expanded, p);
}
function recToggleDetail(id, row, keepOpen){
  var existing = recPaneFor(row);
  if (existing && !keepOpen){
    existing.parentNode.removeChild(existing);
    recState.expanded = null;
    return;
  }
  if (existing && keepOpen) return;
  // one pane at a time: polls no longer rebuild the list, so a pane left
  // under a previously opened row would otherwise stay there for good
  var lst = row.parentNode && row.closest ? row.closest("#rec-list") : null;
  [].forEach.call(lst ? lst.querySelectorAll(".rec-detail") : [], function(p){
    if (p.parentNode) p.parentNode.removeChild(p);
  });
  recState.expanded = id;
  var pane = document.createElement("div");
  pane.className = "rec-detail";
  pane.setAttribute("data-for", String(id));
  pane.__recSig = recRowSig(recFind(id));
  pane.innerHTML = '<div class="rec-hint">loading…</div>';
  var after = row;
  while (after.nextSibling && after.nextSibling.classList &&
         after.nextSibling.classList.contains("rec-rowerr")) after = after.nextSibling;
  row.parentNode.insertBefore(pane, after.nextSibling);
  recDetail(id, pane);
}

var recDetailSeq = 0;
async function recDetail(id, pane){
  var d, tok = pane.__recTok = ++recDetailSeq;     // a slower, older answer never paints over a newer one
  try { d = await (await fetch("/api/recorder?id=" + encodeURIComponent(id), {cache: "no-store"})).json(); }
  catch (e) { if (pane.__recTok === tok) pane.innerHTML = '<div class="rec-hint bad">detail unavailable</div>'; return; }
  if (pane.__recTok !== tok) return;
  if (!d || d.error){ pane.innerHTML = '<div class="rec-hint">' + recE((d && d.error) || "not found") + '</div>'; return; }
  var h = "";
  if (d.summary){ h += '<div class="k">summary</div><div>' + recE(d.summary) + '</div>'; }
  var snap = d.snapshot_ref || {};
  if (snap.short || snap.commit){
    h += '<div class="k">snapshot</div><div>' + recE(snap.short || (snap.commit || "").slice(0, 8)) +
         ' — snapshot taken at turn start (one per turn; undo restores turn-start bytes)</div>';
    // E01: a snapshot that does not hold THIS file (an excluded .env, a log,
    // a gitignored or >10 MB file) — say so where the owner looks for Undo
    if (d.undo_why) h += '<div class="k">undo</div><div>' + recE(d.undo_why) + '</div>';
  } else if (d.kind === "write" || d.kind === "shell"){
    h += '<div class="k">snapshot</div><div>' + recE(d.undo_note || "no snapshot captured") +
         (d.no_snapshot_why ? " — " + recE(d.no_snapshot_why) : "") + '</div>';
  }
  if (d.args && (typeof d.args === "object" ? Object.keys(d.args).length : String(d.args).length)){
    var argstr;
    try { argstr = (typeof d.args === "object") ? JSON.stringify(d.args, null, 2) : String(d.args); }
    catch (e) { argstr = String(d.args); }
    h += '<div class="k">arguments</div><pre>' + recE(argstr) + '</pre>';
  }
  if (d.diff){
    h += '<div class="k">diff (current vs. snapshot)</div><pre class="diff">' +
         recDiffColor(d.diff) + '</pre>';
  } else if (d.diff_error){
    h += '<div class="k">diff</div><div class="rec-hint">' + recE(d.diff_error) + '</div>';
  }
  pane.innerHTML = h || '<div class="rec-hint">No extra detail.</div>';
}
function recDiffColor(diff){
  return String(diff).split("\n").map(function(ln){
    var e = recE(ln);
    if (ln.charAt(0) === "+" && ln.slice(0, 3) !== "+++") return '<span class="add">' + e + '</span>';
    if (ln.charAt(0) === "-" && ln.slice(0, 3) !== "---") return '<span class="del">' + e + '</span>';
    return e;
  }).join("\n");
}

// ---- undo ----------------------------------------------------------------
function recFind(id){
  var acts = (recState.last && recState.last.actions) || [];
  for (var i = 0; i < acts.length; i++) if (String(acts[i].id) === String(id)) return acts[i];
  return null;
}
function recConfirmMsg(a){
  if (!a) return "Undo this action?";
  if (a.reversible === "partial" || a.kind === "shell"){
    return "Undo this command's file changes?\n\nThis restores every file the " +
           "agent touched this turn to its turn-start state. Command side effects " +
           "(network requests, sends, launched processes) are NOT reversed.";
  }
  var f = a.target || "The file";
  // E01: say what will really happen. The server reports it per row
  // (undo_effect); a row it has not checked yet gets the honest either/or.
  // ...and an undo of a RESTORE goes the other way: it brings back what the
  // undo replaced (the agent's version), not "the version from before the
  // agent's turn".
  if (a.tool === "undo" && a.undo_effect !== "trash" && a.undo_effect !== "untrash")
    return "Undo this undo?\n\n" + f + " will be put back to what it was just before " +
           "that undo — the version the undo replaced. A snapshot of the file as it is " +
           "now is taken first, so this is itself undoable.";
  if (a.undo_effect === "trash")
    return "Undo this write?\n\nThe agent created " + f + ". Undo will move it to the " +
           "undo trash (kept 14 days, not deleted). Undoing this undo puts it back.";
  if (a.undo_effect === "untrash")
    return "Put this file back?\n\n" + f + " will be moved back from the undo trash " +
           "to where it was. Nothing at that path is ever overwritten.";
  if (a.undo_effect === "restore")
    return "Undo this write?\n\n" + f +
           " will be restored to the version from before the agent's turn. " +
           "A pre-rollback snapshot is taken first, so this undo is itself undoable.";
  return "Undo this write?\n\n" + f +
         " will be restored to the version from before the agent's turn. If the " +
         "agent created it, it is moved to the undo trash instead (kept 14 days). " +
         "If Hermes has no earlier copy of it, nothing is changed.";
}
// C129: the force-restore prompt says what the server actually found. It
// used to read "changed since the agent wrote it" for every conflict — a file
// that is GONE, one that is now a directory or one that could not be read
// included — and offered "the agent's version", when a restore brings back
// the bytes from BEFORE the agent's turn. Plain text: confirm() renders none.
var REC_CONFLICT_HEAD = {
  hash: "This file changed since the agent wrote it",
  size: "This file changed since the agent wrote it",
  mtime: "This file was modified after the agent finished",
  created: "This file was created again after the agent removed it",
  deleted: "This file no longer exists",
  type: "This path is no longer the kind of item the agent left there",
  unreadable: "The file's current state could not be checked"
};
// For these reasons the server's detail is a fixed sentence (aux_recorder.py
// _conflict()) that the head above already says in other words, so quoting
// it read the same thing twice ("…created again after the agent removed it:
// this file was created after the agent removed it"). Only that exact
// sentence is dropped; any other detail for them is still shown.
var REC_CONFLICT_SAID = {
  created: "this file was created after the agent removed it",
  mtime: "modified since the agent finished"
};
function recConflictMsg(res, a){
  res = res || {};
  var head = REC_CONFLICT_HEAD[res.reason] || "This file changed since the agent wrote it";
  var det = String(res.detail || "");
  var dl = det.trim().replace(/\.$/, "").toLowerCase();
  var said = dl === head.toLowerCase() ||
             (Object.prototype.hasOwnProperty.call(REC_CONFLICT_SAID, res.reason) &&
              dl === REC_CONFLICT_SAID[res.reason]);
  var msg = head + (det && !said ? ": " + det : "") + ".";
  var tgt = res.target || (a && a.target) || "";
  if (tgt) msg += "\n\n" + tgt;
  // E01: a forced Undo of a file the agent created does not restore anything —
  // it moves the file AS IT IS NOW, the owner's later changes included, to
  // the undo trash. Say that, not "force-restore the version from before".
  if (a && a.undo_effect === "trash")
    return msg + "\n\nThe agent created this file, so there is no earlier version to " +
           "restore. Undo will move it as it is now, later changes included, to the " +
           "undo trash (kept 14 days, then deleted; undoing this undo puts it back). " +
           "Move it to the undo trash anyway?";
  if (res.reason === "deleted")
    return msg + "\n\nRestore the version from before the agent's turn anyway?";
  if (res.reason === "unreadable")
    return msg + "\n\nRestore the version from before the agent's turn without " +
           "comparing? Whatever is there now will be replaced.";
  return msg + "\n\nForce-restore the version from before the agent's turn anyway? " +
         "Any later changes will be lost." +
         ((a && a.undo_effect === "restore") ? "" :
          " (If the agent created this file, it is moved to the undo trash instead, " +
          "as it is now.)");
}
// E01: for these two the server never forces anything, so no force prompt is
// offered — the owner is told what is in the way instead. Putting a file back
// from the undo trash never overwrites what is at its path now, and a created
// file that is already gone leaves nothing to undo.
function recConflictNoForce(res, a){
  var eff = a && a.undo_effect;
  var tgt = (res && res.target) || (a && a.target) || "that path";
  if (eff === "untrash")
    return "Something is at " + tgt + " now. Hermes never overwrites it — move it aside, " +
           "then undo again to put the file back from the undo trash.";
  if (eff === "trash" && res && res.reason === "deleted")
    return "The file the agent created is already gone from " + tgt + " — there is nothing to undo.";
  return "";
}

async function recUndo(id, force){
  var a = recFind(id);
  if (recState.busy[id]) return;
  if (!force){
    var ok = true;
    try { ok = confirm(recConfirmMsg(a)); } catch (e) { ok = true; }
    if (!ok) return;
  }
  recState.busy[id] = true;
  var res;
  try {
    res = await (await fetch("/api/undo", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id: parseInt(id, 10), force: !!force})
    })).json();
  } catch (e) {
    recState.busy[id] = false;
    recRowError(id, "undo failed — recorder unreachable");
    return;
  }
  recState.busy[id] = false;
  if (res && res.ok){
    recClearRowError(id);
    recState.expanded = null;
    // the row is about to read "undone", which has no Undo button and so may
    // fold into a run of undone rows: open that run, so the row the owner just
    // acted on does not vanish into a collapsed summary
    recSetRun([String(id)], true);
    await loadRecorder();
    // after the reload: it rebuilds the list (the row is now "undone"), and a
    // flash started on the old row would be thrown away with it
    recFlash(id);
    // E01: where a created file went (or that it came back) is said, not
    // left in the API answer only
    if (res.trash) recRowError(id, "Moved to the undo trash: " + res.trash, true);
    else if (res.restored_to === "(from the undo trash)") recRowError(id, "Put back from the undo trash.", true);
    return;
  }
  if (res && res.conflict){
    var stop = recConflictNoForce(res, a);
    if (stop){ recRowError(id, stop); return; }
    var go = false;
    try { go = confirm(recConflictMsg(res, a)); } catch (e) { go = false; }
    if (go) return recUndo(id, true);
    return;
  }
  var msg = (res && res.error) || "undo failed";
  if (res && res.detail) msg += " — " + res.detail;
  recRowError(id, msg);
}
// A refused Undo's reason used to be wiped by the very next 3 s poll, which
// rebuilds the list (2026-10-08 audit, P3 Frontend). It is kept for 8 s and
// put back under its row after each render.
var REC_ROWERR_MS = 8000;
function recRowError(id, msg, note){
  recState.rowErr = recState.rowErr || {};
  recState.rowErr[id] = {msg: msg, until: Date.now() + REC_ROWERR_MS, note: !!note};
  recShowRowError(id, msg, note);
}
// drop a row's message from the state AND the screen: a poll with nothing
// new no longer rebuilds the list (E33), so the line would otherwise stay
function recClearRowError(id){
  if (recState.rowErr) delete recState.rowErr[id];
  var lst = (typeof document !== "undefined") ? document.getElementById("rec-list") : null;
  var row = lst && lst.querySelector('.rec-row[data-id="' + id + '"]');
  var n = row && row.nextSibling;
  while (n && n.classList && n.classList.contains("rec-rowerr")){
    var nx = n.nextSibling;
    if (n.parentNode) n.parentNode.removeChild(n);
    n = nx;
  }
}
function recShowRowErrors(){
  var errs = recState.rowErr || {};
  Object.keys(errs).forEach(function(id){
    if (Date.now() < errs[id].until) recShowRowError(id, errs[id].msg, errs[id].note);
    else delete errs[id];
  });
}
// `note`: an informational line (E01: where an undone file went), not an error
function recShowRowError(id, msg, note){
  var lst = document.getElementById("rec-list");
  if (!lst) return;
  var row = lst.querySelector('.rec-row[data-id="' + id + '"]');
  if (!row) return;
  var old = row.nextSibling;
  if (old && old.classList && old.classList.contains("rec-rowerr")) old.parentNode.removeChild(old);
  var e = document.createElement("div");
  e.className = "rec-rowerr" + (note ? " note" : "");
  e.textContent = msg;
  row.parentNode.insertBefore(e, row.nextSibling);
}
// The Undo confirmation is a class, not a tween (§8.11): .rec-flash puts an
// iris ::after overlay behind the row's content and a CSS keyframe fades it
// out over --d-micro. It used to tween backgroundColor through Motion, a paint
// property, towards a color-mix(var(--iris)…) string no JS library can
// interpolate. Either reduce switch means no flash at all; the "undone" chip
// and the struck-through target already say what happened.
var REC_FLASH = "rec-flash";
function recFlash(id){
  if (typeof document === "undefined" || recReduced()) return;
  var lst = document.getElementById("rec-list");
  var row = lst && lst.querySelector('.rec-row[data-id="' + id + '"]');
  var cl = row && row.classList;
  if (!cl || typeof cl.add !== "function") return;
  try {
    if (cl.contains(REC_FLASH)) { cl.remove(REC_FLASH); void row.offsetWidth; }   // replay on the same node
    cl.add(REC_FLASH);
    var off = function(ev){
      if (ev && ev.animationName && ev.animationName !== REC_FLASH) return;
      cl.remove(REC_FLASH);
      if (row.removeEventListener) row.removeEventListener("animationend", off);
    };
    row.addEventListener("animationend", off);
  } catch (e) {}
}

// ---- wrap loadConsole so the panel rides the existing 3s poll ------------
(function(){
  var orig = (typeof loadConsole !== "undefined") ? loadConsole : null;
  loadConsole = async function(){
    if (orig){ try { await orig.apply(this, arguments); } catch (e) {} }
    try { await loadRecorder(); } catch (e) {}
  };
})();
