// aux_dictation.js — the "Dictation" card for Settings › Agent & Models.
//
// What it shows: whether the "Hermes Dictation" helper is built and running, the
// three permissions it needs as three-state dots reported BY THE HELPER ITSELF
// (nothing here probes the microphone — a launchd python cannot and must not
// pretend to), how transcripts are cleaned up, the per-app style and dictionary
// rules, and what today actually cost.
//
// PLACEMENT. Same constraint aux_promptbudget.js and aux_network.js document:
// aux_settings_shell.js builds its rail and its twelve panels ONCE from a PANELS
// array captured in its IIFE, ensureShell() early-returns on the second call and
// window.SETTINGS_PANELS is a read-out, not a hook — a module cannot register a
// new Settings panel. So this is a CARD mounted directly into #sec-models, next
// to Prompt budget. It deliberately does NOT fall back to #view-mind: the
// shell's relocator files an id it does not know under System & Data.
//
// THE STATUS IS THE HELPER'S CLAIM, NOT OURS. Every permission dot comes from
// POST /api/dictation/status, sent by the process that actually holds (or does
// not hold) the grant. When the heartbeat is older than 90s the server says
// running:false and this card says "not running" — a stale claim is worse than
// no claim, because the whole point of the card is to be believed.
//
// Design laws (CLAUDE.md): zero emoji, 12-hour clock, esc() on every
// interpolation, tabular numerals on every number, explicit transition-property,
// >=40px targets, colours only through tokens all four palette blocks
// re-declare, and every global helper typeof-guarded so this file can be eval'd
// in a headless harness.
(function () {
  "use strict";

  var W = (typeof window !== "undefined") ? window
        : (typeof globalThis !== "undefined") ? globalThis : null;
  if (!W) return;

  function D() { return (typeof document !== "undefined") ? document : null; }
  function E(s) {
    if (typeof esc === "function") { try { return esc(s); } catch (e) {} }
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var CARD_ID = "mind-extra-dictation";
  var PANEL_ID = "sec-models";
  var SEL = "#" + CARD_ID;

  var S = {
    data: null,      // the GET /api/dictation payload
    loaded: false,
    busy: false,
    err: "",
    dict: null,      // [[from, to], ...] — the editor's working copy
    styles: null,    // [[bundle, style], ...]
    dirty: false,
    open: false,     // the rules disclosure — survives repaints, so a repaint
                     // (a save, a row added) never collapses what the user opened
    saved: 0         // timestamp of the last successful save, for the "Saved" line
  };

  // ---- glyph (two-tone: accent fill + currentColor stroke; zero emoji) ------
  var GLY_MIC =
    '<svg class="dcic" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<rect x="9" y="2.6" width="6" height="11.2" rx="3" fill="var(--iris)" opacity=".16"/>' +
    '<rect x="9" y="2.6" width="6" height="11.2" rx="3" fill="none" ' +
    'stroke="currentColor" stroke-width="1.6"/>' +
    '<path d="M5.6 11.4a6.4 6.4 0 0 0 12.8 0M12 17.8V21M8.6 21h6.8" fill="none" ' +
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

  // ---- pure helpers (exported for the headless harness) --------------------

  function num(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return v.toLocaleString ? v.toLocaleString("en-US") : String(v);
  }

  function ms(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    if (v >= 1000) return (Math.round(v / 100) / 10).toFixed(1) + "s";
    return Math.round(v) + " ms";
  }

  // 12-hour, per the design law. Seconds are never shown — nobody reads them.
  function clock(ts) {
    var v = Number(ts);
    if (!isFinite(v) || v <= 0) return "";
    try {
      return new Date(v * 1000).toLocaleTimeString("en-US",
        { hour: "numeric", minute: "2-digit" });
    } catch (e) { return ""; }
  }

  function ago(sec) {
    var v = Number(sec);
    if (!isFinite(v) || v < 0) return "";
    if (v < 60) return Math.round(v) + "s ago";
    if (v < 3600) return Math.round(v / 60) + " min ago";
    return Math.round(v / 3600) + " h ago";
  }

  // PURE. The one sentence the header says, derived from the payload alone —
  // the harness drives every branch without a helper or a dashboard.
  function headline(d) {
    if (!d) return { word: "checking", tone: "unknown", detail: "" };
    var inst = d.install || {};
    if (!inst.built) {
      return { word: "not built", tone: "unknown",
               detail: "the helper has not been compiled on this Mac yet" };
    }
    if (d.running) {
      var bits = [];
      if (d.status && d.status.engine) bits.push(d.status.engine);
      if (d.status && d.status.hotkey) bits.push("hold " + d.status.hotkey);
      return { word: "running", tone: "ok", detail: bits.join(" · ") };
    }
    if (d.stale) {
      return { word: "not running", tone: "warn",
               detail: "last heard from it " + ago(d.age_s) };
    }
    return { word: "not running", tone: "warn",
             detail: "built, but it has never checked in — launch it once" };
  }

  // PURE. A permission dot is three-state and NEVER guesses: unknown is a real,
  // shown state, because "we have not been told" and "denied" are different
  // problems with different fixes.
  function dotTone(v) {
    if (v === "granted") return "ok";
    if (v === "denied") return "bad";
    return "unknown";
  }

  function rowsFromMap(map) {
    var out = [], k;
    if (!map) return out;
    for (k in map) if (Object.prototype.hasOwnProperty.call(map, k)) out.push([k, map[k]]);
    out.sort(function (a, b) { return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0; });
    return out;
  }

  function mapFromRows(rows) {
    var out = {}, i, k;
    for (i = 0; i < (rows || []).length; i++) {
      k = String(rows[i][0] || "").trim();
      if (!k) continue;
      out[k] = String(rows[i][1] == null ? "" : rows[i][1]).trim();
    }
    return out;
  }

  // ---- CSS ----------------------------------------------------------------
  function CSS() {
    return "<style>" +
      SEL + " h2{display:flex;align-items:center;gap:8px}" +
      SEL + " .dcic{flex:0 0 auto}" +
      SEL + " .dclede{margin:0 0 14px;font-size:12px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty}" +
      // status strip
      SEL + " .dchead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;" +
        "margin:0 0 12px}" +
      SEL + " .dcpill{display:inline-flex;align-items:center;gap:7px;padding:6px 11px;" +
        "border-radius:999px;font-size:11.5px;font-weight:640;" +
        "border:1px solid var(--hairline);background:var(--chip,rgba(255,255,255,.05))}" +
      SEL + " .dcpill .dcdot{width:8px;height:8px;border-radius:50%}" +
      SEL + " .dcpill.is-ok{color:var(--ok)} " + SEL + " .dcpill.is-ok .dcdot{background:var(--ok)}" +
      SEL + " .dcpill.is-warn{color:var(--warn)} " + SEL + " .dcpill.is-warn .dcdot{background:var(--warn)}" +
      SEL + " .dcpill.is-bad{color:var(--bad)} " + SEL + " .dcpill.is-bad .dcdot{background:var(--bad)}" +
      SEL + " .dcpill.is-unknown{color:var(--faint)} " +
        SEL + " .dcpill.is-unknown .dcdot{background:var(--faint)}" +
      SEL + " .dcdetail{font-size:11.5px;color:var(--faint);" +
        "font-variant-numeric:tabular-nums}" +
      // permissions
      SEL + " .dcperms{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));" +
        "gap:8px;margin:0 0 14px}" +
      SEL + " .dcperm{display:flex;align-items:center;gap:8px;padding:9px 11px;" +
        "border-radius:10px;border:1px solid var(--hairline);" +
        "background:var(--chip,rgba(255,255,255,.04));min-height:40px;box-sizing:border-box}" +
      SEL + " .dcperm i{width:9px;height:9px;border-radius:50%;flex:0 0 auto}" +
      SEL + " .dcperm.is-ok i{background:var(--ok)}" +
      SEL + " .dcperm.is-bad i{background:var(--bad)}" +
      SEL + " .dcperm.is-unknown i{background:var(--faint)}" +
      SEL + " .dcperm b{font-size:12px;font-weight:620;color:var(--ink)}" +
      SEL + " .dcperm span{font-size:11px;color:var(--muted);margin-left:auto;" +
        "text-transform:lowercase}" +
      // build block
      SEL + " .dcbuild{margin:0 0 14px;padding:12px 13px;border-radius:11px;" +
        "border:1px solid var(--hairline);background:var(--chip,rgba(255,255,255,.04))}" +
      SEL + " .dcbuild p{margin:0 0 8px;font-size:12px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty}" +
      SEL + " .dcbuild p:last-child{margin-bottom:0}" +
      SEL + " code.dccode{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;" +
        "font-size:11.5px;padding:2px 6px;border-radius:6px;" +
        "background:var(--field,rgba(255,255,255,.06));color:var(--ink)}" +
      // section heads
      SEL + " h3.dch3{margin:16px 0 8px;font-size:11px;letter-spacing:.06em;" +
        "text-transform:uppercase;color:var(--faint);font-weight:660}" +
      // disclosure — the two editors are long and most people never touch them,
      // so the card stays one screen tall until they do
      SEL + " details.dcadv{margin:14px 0 0;border-top:1px solid var(--hairline)}" +
      SEL + " details.dcadv > summary{list-style:none;cursor:pointer;font-size:12px;" +
        "font-weight:620;color:var(--muted);padding:11px 0;display:flex;" +
        "align-items:center;gap:7px;min-height:40px;box-sizing:border-box}" +
      SEL + " details.dcadv > summary::-webkit-details-marker{display:none}" +
      SEL + " details.dcadv > summary::after{content:'';width:6px;height:6px;" +
        "border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;" +
        "transform:rotate(45deg);margin-left:2px;" +
        "transition-property:transform;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " details.dcadv[open] > summary::after{transform:rotate(-135deg)}" +
      SEL + " details.dcadv h3.dch3:first-of-type{margin-top:4px}" +
      // cleanup choice
      SEL + " .dcpick{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));" +
        "gap:10px;margin:0 0 6px}" +
      SEL + " label.dcopt{display:grid;grid-template-columns:18px minmax(0,1fr);" +
        "align-content:start;align-items:start;gap:2px 10px;padding:11px 13px;" +
        "border-radius:11px;cursor:pointer;border:1px solid var(--hairline);" +
        "background:var(--chip,rgba(255,255,255,.04));" +
        "transition-property:border-color,background-color;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " label.dcopt:hover{border-color:var(--iris)}" +
      SEL + " label.dcopt.is-on{border-color:var(--iris);" +
        "background:color-mix(in srgb,var(--iris) 10%,transparent)}" +
      SEL + " label.dcopt input{grid-row:1;margin:2px 0 0;accent-color:var(--iris)}" +
      SEL + " label.dcopt b{grid-column:2;font-size:12.5px;font-weight:640;color:var(--ink)}" +
      SEL + " label.dcopt span{grid-column:2;font-size:11.5px;line-height:1.45;" +
        "color:var(--muted);text-wrap:pretty}" +
      // editors
      SEL + " ul.dcrows{list-style:none;margin:0;padding:0}" +
      SEL + " ul.dcrows li{display:grid;gap:8px;align-items:center;padding:6px 0;" +
        "border-top:1px solid var(--hairline)}" +
      SEL + " ul.dcrows li:first-child{border-top:0}" +
      SEL + " ul.dcrows.dcdict li{grid-template-columns:minmax(0,1fr) 14px minmax(0,1fr) 40px}" +
      SEL + " ul.dcrows.dcstyles li{grid-template-columns:minmax(0,1fr) 132px 40px}" +
      SEL + " ul.dcrows .dcarrow{font-size:11px;color:var(--faint);text-align:center}" +
      SEL + " .dcinput{width:100%;box-sizing:border-box;min-height:40px;padding:8px 10px;" +
        "border-radius:9px;font-size:12px;color:var(--ink);" +
        "border:1px solid var(--field-edge,var(--hairline));" +
        "background:var(--field,rgba(255,255,255,.06))}" +
      SEL + " select.dcinput{appearance:none;-webkit-appearance:none;" +
        "background-image:var(--caret);background-repeat:no-repeat;" +
        "background-position:right 9px center;background-size:15px;padding-right:28px}" +
      SEL + " button.dcx{min-width:40px;min-height:40px;border-radius:9px;border:0;" +
        "cursor:pointer;background:transparent;color:var(--faint);font-size:15px;" +
        "line-height:1;transition-property:color,background-color;" +
        "transition-duration:150ms;transition-timing-function:ease-out}" +
      SEL + " button.dcx:hover{color:var(--bad);" +
        "background:var(--chip,rgba(255,255,255,.06))}" +
      SEL + " .dcempty{font-size:11.5px;color:var(--faint);padding:6px 0}" +
      // toggles
      SEL + " label.dctog{display:flex;align-items:center;gap:9px;min-height:40px;" +
        "font-size:12px;color:var(--ink);cursor:pointer}" +
      SEL + " label.dctog input{accent-color:var(--iris);margin:0}" +
      SEL + " label.dctog em{font-style:normal;color:var(--muted);font-size:11.5px}" +
      // stats
      SEL + " .dcstats{display:flex;gap:22px;flex-wrap:wrap;margin:6px 0 0}" +
      SEL + " .dcstat b{display:block;font-size:17px;font-weight:660;color:var(--ink);" +
        "font-variant-numeric:tabular-nums;line-height:1.2}" +
      SEL + " .dcstat span{display:block;font-size:10.5px;letter-spacing:.05em;" +
        "text-transform:uppercase;color:var(--faint);margin-top:2px}" +
      // recent
      SEL + " ul.dcrecent{list-style:none;margin:8px 0 0;padding:0}" +
      SEL + " ul.dcrecent li{display:flex;align-items:baseline;gap:10px;padding:7px 0;" +
        "border-top:1px solid var(--hairline);font-size:11.5px;color:var(--muted);" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " ul.dcrecent .dcwhen{color:var(--faint);white-space:nowrap}" +
      SEL + " ul.dcrecent .dcapp{color:var(--ink);font-weight:600}" +
      SEL + " ul.dcrecent .dctext{color:var(--muted);flex:1 1 auto;overflow:hidden;" +
        "text-overflow:ellipsis;white-space:nowrap}" +
      // bar + footer
      SEL + " .dcbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:12px 0 0}" +
      SEL + " button.dcb{padding:9px 16px;border-radius:10px;font-size:12.5px;" +
        "font-weight:600;cursor:pointer;border:0;background:var(--iris);color:#fff;" +
        "min-height:40px;transition-property:opacity;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " button.dcb:disabled{opacity:.45;cursor:default}" +
      SEL + " button.dcadd{background:transparent;color:var(--iris);" +
        "border:1px solid var(--hairline);padding:8px 13px;min-height:40px;" +
        "border-radius:9px;font-size:12px;font-weight:600;cursor:pointer}" +
      SEL + " .dcok{font-size:11.5px;color:var(--ok)}" +
      SEL + " .dcerr{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--bad)}" +
      SEL + " .dcfoot{margin:16px 0 0;padding-top:12px;" +
        "border-top:1px solid var(--hairline);font-size:11px;line-height:1.55;" +
        "color:var(--faint);text-wrap:pretty}" +
      "</style>";
  }

  // ---- markup -------------------------------------------------------------

  function permHTML(label, value) {
    var tone = dotTone(value);
    return '<div class="dcperm is-' + tone + '"><i></i><b>' + E(label) + "</b>" +
      "<span>" + E(value || "unknown") + "</span></div>";
  }

  function optHTML(key, on, title, body) {
    return '<label class="dcopt' + (on ? " is-on" : "") + '">' +
      '<input type="radio" name="dccleanup" value="' + E(key) + '"' +
      (on ? " checked" : "") + ">" +
      "<b>" + E(title) + "</b><span>" + E(body) + "</span></label>";
  }

  function dictHTML(rows) {
    if (!rows.length) {
      return '<p class="dcempty">No replacements yet. Add one for a name or a ' +
        "command the transcriber keeps mishearing.</p>";
    }
    var out = '<ul class="dcrows dcdict">', i;
    for (i = 0; i < rows.length; i++) {
      out += "<li>" +
        '<input class="dcinput" data-dict="from" data-i="' + i + '" value="' +
          E(rows[i][0]) + '" placeholder="heard as" spellcheck="false">' +
        '<span class="dcarrow">to</span>' +
        '<input class="dcinput" data-dict="to" data-i="' + i + '" value="' +
          E(rows[i][1]) + '" placeholder="written as" spellcheck="false">' +
        '<button class="dcx" type="button" data-drop="dict" data-i="' + i +
          '" aria-label="Remove this replacement">&times;</button>' +
        "</li>";
    }
    return out + "</ul>";
  }

  function stylesHTML(rows, styles) {
    if (!rows.length) {
      return '<p class="dcempty">No per-app styles. Everything is cleaned as prose.</p>';
    }
    var out = '<ul class="dcrows dcstyles">', i, j;
    for (i = 0; i < rows.length; i++) {
      out += "<li>" +
        '<input class="dcinput" data-style="bundle" data-i="' + i + '" value="' +
          E(rows[i][0]) + '" placeholder="com.apple.Notes" spellcheck="false">' +
        '<select class="dcinput" data-style="style" data-i="' + i + '">';
      for (j = 0; j < styles.length; j++) {
        out += '<option value="' + E(styles[j]) + '"' +
          (rows[i][1] === styles[j] ? " selected" : "") + ">" + E(styles[j]) + "</option>";
      }
      out += "</select>" +
        '<button class="dcx" type="button" data-drop="style" data-i="' + i +
          '" aria-label="Remove this app style">&times;</button>' +
        "</li>";
    }
    return out + "</ul>";
  }

  function recentHTML(rows, keep) {
    if (!rows.length) {
      return '<p class="dcempty">Nothing dictated in the kept window.</p>';
    }
    var out = '<ul class="dcrecent">', i, r;
    for (i = 0; i < rows.length; i++) {
      r = rows[i];
      out += "<li><span class=\"dcwhen\">" + E(clock(r.ts)) + "</span>" +
        '<span class="dcapp">' + E(r.app || "unknown app") + "</span>" +
        (keep && r.text ? '<span class="dctext">' + E(r.text) + "</span>"
                        : '<span class="dctext"></span>') +
        "<span>" + E(num(r.words)) + " words · " + E(r.mode || "") + "</span></li>";
    }
    return out + "</ul>";
  }

  function cardHTML(state) {
    state = state || {};
    var d = state.data;
    if (!d) {
      return CSS() + "<h2>" + GLY_MIC + "Dictation</h2>" +
        '<div class="body"><p class="dclede">Checking the dictation helper…</p></div>';
    }
    if (d.ok === false) {
      return CSS() + "<h2>" + GLY_MIC + "Dictation</h2>" +
        '<div class="body"><p class="dcerr">' +
        E(d.error || "Could not read the dictation state.") + "</p></div>";
    }

    var inst = d.install || {};
    var st = d.status || {};
    var set = d.settings || {};
    var styles = d.styles || ["prose", "casual", "verbatim"];
    var head = headline(d);
    var today = d.today || {};
    var dictRows = state.dict || rowsFromMap(set.dictionary);
    var styleRows = state.styles || rowsFromMap(set.app_styles);

    var html = CSS() + "<h2>" + GLY_MIC + "Dictation</h2><div class=\"body\">";

    html += '<p class="dclede">Hold the hotkey anywhere on this Mac and talk; ' +
      "let go and the text lands where your cursor is. Speech is transcribed on " +
      "device by a separate helper app — the audio never leaves this Mac and no " +
      "recording is kept.</p>";

    // --- status strip
    html += '<div class="dchead">' +
      '<span class="dcpill is-' + E(head.tone) + '"><i class="dcdot"></i>' +
      E(head.word) + "</span>";
    if (head.detail) html += '<span class="dcdetail">' + E(head.detail) + "</span>";
    if (d.running && st.version) {
      html += '<span class="dcdetail">v' + E(st.version) + "</span>";
    }
    html += "</div>";

    if (d.running && st.note) {
      html += '<p class="dcerr">' + E(st.note) + "</p>";
    }

    // --- permissions (the helper's own report, never our guess)
    html += '<div class="dcperms">' +
      permHTML("Microphone", st.mic) +
      permHTML("Accessibility", st.accessibility) +
      permHTML("Speech model", st.speech) +
      "</div>";

    // --- build / launch
    if (!inst.built) {
      html += '<div class="dcbuild">' +
        "<p>The helper is not built yet. From the repository:</p>" +
        '<p><code class="dccode">' + E(inst.build_cmd || "bash app/build-dictation.sh") +
        "</code></p>" +
        "<p>Then launch it yourself: <code class=\"dccode\">" +
        E(inst.launch_cmd || 'open "app/build/Hermes Dictation.app"') + "</code> — " +
        E(inst.launch_note || "") + "</p></div>";
    } else if (!d.running) {
      html += '<div class="dcbuild">' +
        "<p>Built, but not running. Launch it: <code class=\"dccode\">" +
        E(inst.launch_cmd || 'open "app/build/Hermes Dictation.app"') + "</code></p>" +
        "<p>" + E(inst.launch_note || "") + "</p></div>";
    }

    // --- cleanup mode
    html += '<h3 class="dch3">Cleanup</h3><div class="dcpick">' +
      optHTML("off", set.cleanup === "off", "Off",
              "Insert exactly what was heard. The honest baseline.") +
      optHTML("rules", set.cleanup === "rules", "Rules",
              "Filler words, doubled words and false starts out; sentence case " +
              "and punctuation in. Instant, no model.") +
      optHTML("model", set.cleanup === "model", "Model",
              "The rules first, then a short pass on a model lane that is " +
              "already awake. Never wakes one, and falls back to the rules.") +
      "</div>";

    // --- per-app styles + dictionary, behind one disclosure
    var nStyles = styleRows.length, nDict = dictRows.length;
    html += '<details class="dcadv"' + (state.open ? " open" : "") + ">" +
      "<summary>Style and dictionary rules — " + E(num(nStyles)) +
      (nStyles === 1 ? " app, " : " apps, ") + E(num(nDict)) +
      (nDict === 1 ? " replacement" : " replacements") + "</summary>";

    html += '<h3 class="dch3">Per-app style</h3>' +
      stylesHTML(styleRows, styles) +
      '<div class="dcbar"><button class="dcadd" type="button" data-add="style">' +
      "Add an app</button></div>";

    html += '<h3 class="dch3">Dictionary</h3>' +
      dictHTML(dictRows) +
      '<div class="dcbar"><button class="dcadd" type="button" data-add="dict">' +
      "Add a replacement</button></div>";

    html += '<div class="dcbar">' +
      '<button class="dcb" type="button" data-act="save"' +
      (state.busy || !state.dirty ? " disabled" : "") + ">" +
      (state.busy ? "Saving…" : "Save changes") + "</button>" +
      (state.saved && !state.dirty
        ? '<span class="dcok">Saved ' + E(clock(state.saved / 1000)) + "</span>" : "") +
      "</div></details>";

    // --- history
    html += '<h3 class="dch3">History</h3>' +
      '<label class="dctog"><input type="checkbox" data-keep' +
      (set.keep_history ? " checked" : "") + ">" +
      "<span>Keep what was dictated</span>" +
      "<em>off by default — the counts below are kept either way</em></label>";

    html += '<div class="dcstats">' +
      '<div class="dcstat"><b>' + E(num(today.dictations || 0)) +
        "</b><span>dictations today</span></div>" +
      '<div class="dcstat"><b>' + E(num(today.words || 0)) +
        "</b><span>words today</span></div>" +
      '<div class="dcstat"><b>' + E(today.mean_ms == null ? "—" : ms(today.mean_ms)) +
        "</b><span>mean round trip</span></div></div>";

    html += recentHTML(d.recent || [], !!set.keep_history);

    if (state.err) html += '<p class="dcerr">' + E(state.err) + "</p>";

    html += '<p class="dcfoot">' +
      E(inst.rebuild_note || "") + " Microphone and Accessibility are granted in " +
      "System Settings › Privacy &amp; Security; the helper asks for them the " +
      "first time you use it. Nothing here reaches the network: transcription " +
      "runs on device and the cleanup model, when it is used at all, is the " +
      "local lane on this Mac.</p>";

    return html + "</div>";
  }

  // ---- data ---------------------------------------------------------------

  function jget(path) {
    if (typeof j === "function") { try { return j(path); } catch (e) {} }
    return fetch(path, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); });
  }

  function jpost(path, body) {
    if (typeof j === "function") { try { return j(path, body); } catch (e) {} }
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  async function load() {
    var d = await jget("/api/dictation");
    S.data = d && typeof d === "object" ? d : { ok: false };
    S.loaded = true;
    if (!S.dirty) {
      S.dict = rowsFromMap((S.data.settings || {}).dictionary);
      S.styles = rowsFromMap((S.data.settings || {}).app_styles);
    }
    return S.data;
  }

  async function save(patch) {
    S.busy = true; S.err = ""; paint();
    try {
      var res = await jpost("/api/dictation/settings", patch);
      if (res && res.ok === false) throw new Error(res.error || "save failed");
      S.dirty = false;
      S.saved = Date.now();
      await load();
    } catch (e) {
      S.err = "Could not save the dictation settings.";
    }
    S.busy = false;
    paint();
  }

  // ---- wiring -------------------------------------------------------------

  function wire(card) {
    var d = D(); if (!d || !card) return;

    Array.prototype.slice.call(card.querySelectorAll('input[name="dccleanup"]'))
      .forEach(function (el) {
        el.onchange = function () {
          if (!el.checked) return;
          save({ cleanup: el.value });
        };
      });

    var keep = card.querySelector("input[data-keep]");
    if (keep) keep.onchange = function () { save({ keep_history: !!keep.checked }); };

    Array.prototype.slice.call(card.querySelectorAll("input[data-dict]"))
      .forEach(function (el) {
        el.oninput = function () {
          var i = Number(el.getAttribute("data-i"));
          var col = el.getAttribute("data-dict") === "from" ? 0 : 1;
          if (!S.dict || !S.dict[i]) return;
          S.dict[i][col] = el.value;
          S.dirty = true;
          markDirty(card);
        };
      });

    Array.prototype.slice.call(card.querySelectorAll("[data-style]"))
      .forEach(function (el) {
        el.oninput = el.onchange = function () {
          var i = Number(el.getAttribute("data-i"));
          var col = el.getAttribute("data-style") === "bundle" ? 0 : 1;
          if (!S.styles || !S.styles[i]) return;
          S.styles[i][col] = el.value;
          S.dirty = true;
          markDirty(card);
        };
      });

    Array.prototype.slice.call(card.querySelectorAll("button[data-drop]"))
      .forEach(function (el) {
        el.onclick = function () {
          var i = Number(el.getAttribute("data-i"));
          var which = el.getAttribute("data-drop");
          var rows = which === "dict" ? S.dict : S.styles;
          if (!rows || i < 0 || i >= rows.length) return;
          rows.splice(i, 1);
          S.dirty = true;
          S.open = true;
          paint();
        };
      });

    Array.prototype.slice.call(card.querySelectorAll("button[data-add]"))
      .forEach(function (el) {
        el.onclick = function () {
          if (el.getAttribute("data-add") === "dict") {
            S.dict = (S.dict || []).concat([["", ""]]);
          } else {
            S.styles = (S.styles || []).concat([["", "prose"]]);
          }
          S.dirty = true;
          S.open = true;
          paint();
        };
      });

    var det = card.querySelector("details.dcadv");
    if (det) det.ontoggle = function () { S.open = !!det.open; };

    var b = card.querySelector('button[data-act="save"]');
    if (b) {
      b.onclick = function () {
        save({ dictionary: mapFromRows(S.dict), app_styles: mapFromRows(S.styles) });
      };
    }
  }

  // Enable Save without repainting — a repaint on every keystroke would move
  // the caret out of the field the user is typing in.
  function markDirty(card) {
    var b = card.querySelector('button[data-act="save"]');
    if (b) { b.disabled = false; b.textContent = "Save changes"; }
  }

  var relocatedOnce = false;

  function paint() {
    var d = D(); if (!d) return;
    var panel = d.getElementById(PANEL_ID);
    // No panel yet => wait. Deliberately NOT falling back to #view-mind: the
    // shell's relocator sends an id it does not know to sec-system, and this
    // card belongs with the model settings, next to Prompt budget.
    if (!panel) return;
    var body = panel.querySelector(".set-body") || panel;
    var el = d.getElementById(CARD_ID);
    if (!el) {
      el = d.createElement("section");
      el.id = CARD_ID;
      el.className = "card glass set-legacy";
      body.appendChild(el);
    } else if (el.parentNode !== body) {
      try { body.appendChild(el); } catch (e) {}
    }
    try { el.innerHTML = cardHTML(S); } catch (e) { return; }
    wire(el);
    if (!relocatedOnce) {
      relocatedOnce = true;
      try { if (typeof W.settingsRelocate === "function") W.settingsRelocate(); } catch (e) {}
    }
  }

  var loading = false;

  async function mount() {
    var d = D();
    if (!d || !d.getElementById(PANEL_ID)) return;
    if (!S.loaded && !loading) {
      loading = true;
      paint();                        // the "checking…" shell, so the panel is
      try { await load(); }           // never empty while the fetch runs
      catch (e) { S.data = { ok: false, error: "The dashboard did not answer." };
                  S.loaded = true; }
      loading = false;
    }
    paint();
  }

  // chain window.mindExtras exactly like the other aux settings cards
  var prev = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await mount(); } catch (e) {}
  };

  // headless-harness surface (also handy from the console)
  W.hermesDictation = {
    cardHTML: cardHTML, CSS: CSS, headline: headline, dotTone: dotTone,
    rowsFromMap: rowsFromMap, mapFromRows: mapFromRows,
    num: num, ms: ms, clock: clock, ago: ago,
    mount: mount, paint: paint, save: save, state: S,
    refresh: async function () { S.loaded = false; S.dirty = false; await mount(); }
  };
})();
