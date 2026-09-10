// aux_recorder.js — Flight Recorder + Undo panel (P1.2). Served at
// /aux_recorder.js, loaded after expand.js so it sees every inline helper
// (esc, animate, REDUCE, relTime). Injects a "Flight Recorder" card at the top
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
                ckpt: true, err: false, busy: {}};

// ---- helpers -------------------------------------------------------------
function recE(s){ return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }

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

function recRevChip(a){
  if (a.status === "undone")
    return '<span class="rchip undone">undone</span>';
  if (a.reversible === "n/a" || REC_READONLY[a.kind] || REC_READONLY_TOOLS[a.tool])
    return "";                                                     // no badge
  var r = a.reversible;
  if (r === "yes")     return '<span class="rchip ok">reversible</span>';
  // the full phrase is ~130px of a 268px rail row — keep it in the tooltip
  if (r === "partial") return '<span class="rchip warn" title="partial &mdash; files only">partial</span>';
  return '<span class="rchip bad">irreversible</span>';
}

// ---- pure row renderer (verified headless — no DOM, no throw on numbers) --
function renderRecorderRows(actions){
  actions = actions || [];
  if (!actions.length)
    return '<div class="rec-empty">No recorded actions yet. Every file the ' +
           'agent writes, every command it runs, lands here with an Undo where ' +
           'one is possible.</div>';
  var h = "";
  for (var i = 0; i < actions.length; i++){
    var a = actions[i] || {};
    var undone = a.status === "undone";
    var canUndo = !undone && a.status === "done" &&
                  (a.reversible === "yes" || a.reversible === "partial") &&
                  a.kind !== "read" && a.kind !== "computer" &&
                  a.kind !== "net" && a.kind !== "memory" && a.kind !== "agent";
    var tgt = recE(a.target || (a.tool === "undo" ? "" : "—"));
    var btn = "";
    if (canUndo){
      var lbl = (a.reversible === "partial") ? "Undo…" : "Undo";
      btn = '<button class="rec-undo" data-undo="' + recE(a.id) + '">' + lbl + '</button>';
    }
    h += '<div class="rec-row' + (undone ? " is-undone" : "") +
         '" data-id="' + recE(a.id) + '" data-kind="' + recE(a.kind) + '">' +
         '<span class="rec-ic">' + recKindIco(a) + '</span>' +
         '<div class="rec-main">' +
           '<div class="rec-top">' +
             // title: in the 296px Agent rail an undo-able row can ellipsise the
             // name ("write_file" -> "write…"); hover gives it back without
             // opening the detail pane.
             '<span class="rec-tool" title="' + recE(a.tool) + '">' + recE(a.tool) + '</span>' +
             '<span class="rec-tgt' + (undone ? " struck" : "") + '">' + tgt + '</span>' +
           '</div>' +
         '</div>' +
         '<span class="rec-src">' + recE(a.source) + '</span>' +
         recRevChip(a) +
         '<span class="rec-when" title="' + recE(recClock(a.ts)) + '">' +
           recE(recWhen(a.ts)) + '</span>' +
         '<span class="rec-act">' + btn + '</span>' +
         '</div>';
  }
  return h;
}

// ---- one-time CSS --------------------------------------------------------
function recInjectCss(){
  if (typeof document === "undefined" || document.getElementById("reccss")) return;
  var s = document.createElement("style");
  s.id = "reccss";
  s.textContent = [
    // Header is ONE line in the 320px Agent rail: the title never wraps and the
    // count sits right-aligned as quiet meta (it inherits the card h2's
    // uppercase/letter-spacing otherwise, which is what made it look like a
    // second title and wrap "FLIGHT RECORDER / 0 REVERSIBLE · 0 UNDONE").
    '#recorder-card .rec-head{display:flex;align-items:center;gap:7px;margin-bottom:2px;' +
      'flex-wrap:nowrap;white-space:nowrap;min-width:0}',
    '#recorder-card .rec-head .ic{flex:none}',
    '#recorder-card .rec-count{margin-left:auto;flex:none;font-size:10px;color:var(--faint);' +
      'font-weight:500;text-transform:none;letter-spacing:.01em;white-space:nowrap;' +
      'font-variant-numeric:tabular-nums}',
    // Filters stay on ONE row — they scroll horizontally instead of wrapping
    // into a second/third line (page scrollbars are globally hidden, so this
    // reads as a quiet overflow rail rather than a scroller).
    '#recorder-card .rec-filters{display:flex;flex-wrap:nowrap;gap:5px;margin:10px 0 6px;' +
      'overflow-x:auto;overscroll-behavior-x:contain;scrollbar-width:none;padding-bottom:1px}',
    '#recorder-card .rec-filters::-webkit-scrollbar{display:none}',
    '.rec-fchip{font-size:10.5px;padding:3px 7px;border-radius:999px;border:1px solid var(--hairline);' +
      'background:var(--chip);color:var(--muted);cursor:pointer;user-select:none;' +
      'white-space:nowrap;flex:0 0 auto}',
    '.rec-fchip.on{background:var(--iris);border-color:var(--iris);color:var(--iris-ink);font-weight:600}',
    '.rec-banner{font-size:11.5px;line-height:1.5;color:var(--ink);background:color-mix(in srgb,var(--warn) 16%,transparent);' +
      'border:1px solid color-mix(in srgb,var(--warn) 45%,transparent);border-radius:10px;padding:9px 11px;margin:4px 0 6px;user-select:text}',
    '.rec-banner code{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:11px;user-select:all}',
    '#rec-list{display:flex;flex-direction:column;gap:2px;margin-top:4px}',
    // overflow:hidden is the backstop for the original bug: whatever the mix of
    // badge/time/Undo, a long name now clips at the row edge instead of being
    // painted on top of the cell next to it.
    '.rec-row{display:flex;align-items:center;gap:10px;padding:8px 6px;border-radius:9px;cursor:pointer;' +
      'border-bottom:1px solid var(--hairline);overflow:hidden}',
    '.rec-row:hover{background:var(--glass-2)}',
    '.rec-ic{width:26px;height:26px;flex:none;display:inline-flex;align-items:center;justify-content:center;color:var(--iris)}',
    '.rec-ic .ric{width:19px;height:19px}',
    '.rec-row[data-kind="shell"] .rec-ic{color:var(--warn)}',
    '.rec-row[data-kind="computer"] .rec-ic{color:var(--bad)}',
    '.rec-row[data-kind="read"] .rec-ic,.rec-row[data-kind="net"] .rec-ic,' +
      '.rec-row[data-kind="agent"] .rec-ic{color:var(--faint)}',
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
    '.rec-src{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);' +
      'flex:0 1 auto;max-width:52px;min-width:0;text-align:right;overflow:hidden;' +
      'text-overflow:ellipsis;white-space:nowrap}',
    // Empty meta cells were still eating a fixed column AND a 10px gap each —
    // in the 296px Agent rail that left ~16px for the name, which is the real
    // reason the tool name painted over the badge. Collapse them instead.
    '.rec-src:empty,.rec-act:empty{display:none}',
    '.rchip{font-size:10px;font-weight:600;padding:3px 8px;border-radius:999px;white-space:nowrap;flex:0 0 auto}',
    '.rchip.ok{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}',
    '.rchip.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}',
    '.rchip.bad{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)}',
    '.rchip.undone{background:color-mix(in srgb,var(--iris) 20%,transparent);color:var(--iris)}',
    '.rec-when{font-size:10.5px;color:var(--faint);flex:0 0 auto;width:58px;text-align:right;' +
      'white-space:nowrap;font-variant-numeric:tabular-nums}',
    '.rec-act{flex:0 0 auto;width:auto;min-width:0;text-align:right}',
    '.rec-undo{font-size:11px;font-weight:600;padding:4px 10px;border-radius:8px;cursor:pointer;' +
      'border:1px solid color-mix(in srgb,var(--iris) 55%,transparent);background:transparent;color:var(--iris)}',
    '.rec-undo:hover{background:color-mix(in srgb,var(--iris) 14%,transparent)}',
    '.rec-rowerr{font-size:11px;color:var(--bad);padding:2px 6px 8px 42px}',
    '.rec-detail{padding:10px 6px 12px 42px;font-size:11.5px;color:var(--muted);border-bottom:1px solid var(--hairline)}',
    '.rec-detail .k{color:var(--faint);text-transform:uppercase;letter-spacing:.05em;font-size:9.5px;margin:8px 0 3px}',
    '.rec-detail pre{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:11px;line-height:1.5;' +
      'white-space:pre-wrap;word-break:break-word;background:var(--glass-2);border:1px solid var(--hairline);' +
      'border-radius:8px;padding:8px 10px;margin:0;max-height:280px;overflow:auto}',
    '.rec-detail .diff .add{color:var(--ok)}.rec-detail .diff .del{color:var(--bad)}',
    '.rec-empty,.rec-hint{font-size:11.5px;color:var(--muted);line-height:1.55;padding:8px 2px}',
    '.rec-hint.bad{color:var(--bad)}',
    // Retention: one quiet footer line under the feed. It must READ as
    // settings, not as another row of the log, so it sits below a hairline in
    // --faint, and it never wraps into two lines in the 320px Agent rail.
    '#recorder-card .rec-retain{display:flex;align-items:center;gap:8px;margin-top:10px;' +
      'padding-top:9px;border-top:1px solid var(--hairline);font-size:11px;' +
      'color:var(--faint);line-height:1.5;flex-wrap:wrap}',
    '#recorder-card .rec-retain select{font:inherit;font-size:11px;color:var(--ink);' +
      'background:var(--chip);border:1px solid var(--hairline);border-radius:8px;' +
      'padding:5px 8px;min-height:32px;cursor:pointer}',
    '#recorder-card .rec-retain .rr-note{flex:1 1 100%;color:var(--faint);font-size:10.5px}',
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
      'Flight Recorder <span class="rec-count" id="rec-count"></span></h2>' +
    '<div class="body"><div id="rec-banner"></div>' +
    '<div class="rec-filters" id="rec-filters"></div>' +
    '<div id="rec-list"><div class="skel"><i></i><i></i><i></i></div></div>' +
    '<div class="rec-retain" id="rec-retain" hidden></div></div>';
  host.insertBefore(card, host.firstChild);
  return card;
}

var REC_FILTERS = [["all", "All"], ["write", "Writes"], ["shell", "Shell"],
                   ["computer", "Computer"], ["undone", "Undone"]];

function recRenderFilters(){
  var el = document.getElementById("rec-filters");
  if (!el) return;
  el.innerHTML = REC_FILTERS.map(function(f){
    return '<span class="rec-fchip' + (recState.filter === f[0] ? " on" : "") +
           '" data-f="' + f[0] + '">' + recE(f[1]) + '</span>';
  }).join("");
  [].forEach.call(el.querySelectorAll("[data-f]"), function(b){
    b.addEventListener("click", function(){ recSetFilter(b.getAttribute("data-f")); });
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
    if (!recState.ckpt){
      ban.innerHTML = '<div class="rec-banner">Snapshots are off. Run ' +
        '<code>hermes config set checkpoints.enabled true</code> and restart the ' +
        'agent services — until then nothing new is undoable.</div>';
    } else { ban.innerHTML = ""; }
  }

  lst.innerHTML = renderRecorderRows(actions);

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
  if (recState.expanded != null){
    var er = lst.querySelector('.rec-row[data-id="' + recState.expanded + '"]');
    if (er) recToggleDetail(recState.expanded, er, true);
  }

  if (typeof REDUCE === "undefined" || !REDUCE){
    try {
      if (typeof animate === "function")
        animate(lst.querySelectorAll(".rec-row"),
                {opacity: [0, 1]}, {duration: 0.28});
    } catch (e) {}
  }
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
function recToggleDetail(id, row, keepOpen){
  var existing = row.nextSibling;
  if (existing && existing.classList && existing.classList.contains("rec-detail") && !keepOpen){
    existing.parentNode.removeChild(existing);
    recState.expanded = null;
    return;
  }
  if (existing && existing.classList && existing.classList.contains("rec-detail") && keepOpen) return;
  recState.expanded = id;
  var pane = document.createElement("div");
  pane.className = "rec-detail";
  pane.innerHTML = '<div class="rec-hint">loading…</div>';
  row.parentNode.insertBefore(pane, row.nextSibling);
  recDetail(id, pane);
}

async function recDetail(id, pane){
  var d;
  try { d = await (await fetch("/api/recorder?id=" + encodeURIComponent(id), {cache: "no-store"})).json(); }
  catch (e) { pane.innerHTML = '<div class="rec-hint bad">detail unavailable</div>'; return; }
  if (!d || d.error){ pane.innerHTML = '<div class="rec-hint">' + recE((d && d.error) || "not found") + '</div>'; return; }
  var h = "";
  if (d.summary){ h += '<div class="k">summary</div><div>' + recE(d.summary) + '</div>'; }
  var snap = d.snapshot_ref || {};
  if (snap.short || snap.commit){
    h += '<div class="k">snapshot</div><div>' + recE(snap.short || (snap.commit || "").slice(0, 8)) +
         ' — snapshot taken at turn start (one per turn; undo restores turn-start bytes)</div>';
  } else if (d.kind === "write" || d.kind === "shell"){
    h += '<div class="k">snapshot</div><div>' + recE(d.undo_note || "no snapshot captured") + '</div>';
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
  return "Undo this write?\n\n" + (a.target || "The file") +
         " will be restored to the version from before the agent's turn. " +
         "A pre-rollback snapshot is taken first, so this undo is itself undoable.";
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
    recFlash(id);
    recState.expanded = null;
    await loadRecorder();
    return;
  }
  if (res && res.conflict){
    var go = false;
    try {
      go = confirm("This file changed since the agent wrote it. Force-restore to " +
                   "the agent's version anyway? Any later changes will be lost.");
    } catch (e) { go = false; }
    if (go) return recUndo(id, true);
    return;
  }
  var msg = (res && res.error) || "undo failed";
  if (res && res.detail) msg += " — " + res.detail;
  recRowError(id, msg);
}
function recRowError(id, msg){
  var lst = document.getElementById("rec-list");
  if (!lst) return;
  var row = lst.querySelector('.rec-row[data-id="' + id + '"]');
  if (!row) return;
  var old = row.nextSibling;
  if (old && old.classList && old.classList.contains("rec-rowerr")) old.parentNode.removeChild(old);
  var e = document.createElement("div");
  e.className = "rec-rowerr";
  e.textContent = msg;
  row.parentNode.insertBefore(e, row.nextSibling);
}
function recFlash(id){
  if (typeof REDUCE !== "undefined" && REDUCE) return;
  if (typeof document === "undefined") return;
  var lst = document.getElementById("rec-list");
  var row = lst && lst.querySelector('.rec-row[data-id="' + id + '"]');
  if (!row) return;
  try {
    if (typeof animate === "function")
      animate(row, {backgroundColor: ["color-mix(in srgb,var(--iris) 30%,transparent)", "transparent"]},
              {duration: 0.15});
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
