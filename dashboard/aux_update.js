// aux_update.js — "Software Update" card for Settings › System & Data.
// Backend: aux_update.py (/api/version, /api/update/check|apply|status|channel).
//
// Placement WITHOUT touching aux_settings_shell.js: the card is rendered as a
// #mind-extra-update section. If the settings shell has already built its
// panels we append straight into #sec-system; otherwise we append to
// #view-mind and the shell's relocator re-homes it (an id that isn't in its
// CARD_MAP falls back to sec-system by design). Either way it lands in
// System & Data, and re-renders are idempotent — the same node is reused.
//
// It also puts a small dot on the header Settings gear (#tab-mind) when an
// update is waiting: a positioned <i> appended to the existing tab, plus one
// scoped style rule. No markup of anyone else's is modified.
//
// "What's new": the release body is markdown; parseNotes() splits it into
// Keep-a-Changelog sections and inlineMD() escapes every character before
// re-introducing <strong>/<code>. Server HTML is never injected. When there is
// no update to offer the block shows the RUNNING version's section instead,
// from /api/update/check's payload when it happens to be that release, else
// from GET /api/update/notes?version= (CHANGELOG.md, read-only).
//
// Design laws (CLAUDE.md): zero emoji (bespoke two-tone SVG), 12-hour clock,
// esc() on every interpolation, every global helper typeof-guarded so a
// headless harness can eval this file.
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

  var CARD_ID = "mind-extra-update";
  var POLL_MS = 2000;

  var GLYPH =
    '<svg class="updic" viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
    'stroke="currentColor" stroke-width="1.6" style="flex:0 0 auto">' +
    '<path d="M12 3v11" stroke-linecap="round"/>' +
    '<path d="M8.2 10.4 12 14.2l3.8-3.8" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<path d="M4 16.5v2A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5v-2" ' +
    'stroke-linecap="round"/>' +
    '<rect x="4" y="16.5" width="16" height="4.5" rx="2" fill="currentColor" opacity=".14" stroke="none"/>' +
    '</svg>';

  // ---- pure helpers (exported for the headless harness) --------------------

  // absolute 12-hour clock, per the repo's design law (never "3 minutes ago")
  function fmtWhen(ts) {
    if (!ts) return "never";
    try {
      var d = new Date(ts * (ts > 1e12 ? 1 : 1000));
      if (isNaN(d.getTime())) return "never";
      var h = d.getHours(), m = d.getMinutes(), ap = h >= 12 ? "PM" : "AM";
      h = h % 12; if (h === 0) h = 12;
      var t = h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
      var today = new Date();
      var same = d.getFullYear() === today.getFullYear() &&
                 d.getMonth() === today.getMonth() && d.getDate() === today.getDate();
      if (same) return t;
      return (d.getMonth() + 1) + "/" + d.getDate() + " " + t;
    } catch (e) { return "never"; }
  }

  // ISO-8601 from the GitHub API -> "9/10/2026". The card itself now uses the
  // long form (fmtDayLong) everywhere; this stays on the exported surface for
  // the console and the headless harness.
  function fmtDay(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return "";
      return (d.getMonth() + 1) + "/" + d.getDate() + "/" + d.getFullYear();
    } catch (e) { return ""; }
  }

  // release notes: ESCAPED text, first `max` lines, never rendered as markup
  function notesPreview(notes, max) {
    max = max || 12;
    var lines = String(notes == null ? "" : notes).replace(/\r/g, "").split("\n");
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    var head = lines.slice(0, max).map(function (l) { return E(l); }).join("\n");
    var rest = lines.length - max;
    if (rest > 0) head += "\n" + E("… " + rest + " more line" + (rest === 1 ? "" : "s") + " in the release notes");
    return head;
  }

  // ---- "What's new": release-notes markdown -> escaped, sectioned HTML -----
  //
  // The server hands us the release BODY as plain markdown (GitHub's release
  // notes, or CHANGELOG.md's `## [x.y.z]` section via /api/update/notes). It is
  // NEVER injected as HTML: parseNotes() only ever produces plain strings, and
  // inlineMD() escapes first and then re-introduces exactly two tags of its own
  // (<strong> for **lead-ins**, <code> for `backticks`).

  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
             "Sep", "Oct", "Nov", "Dec"];

  // "2026-09-04" / "2026-09-04T12:00:00Z" -> "Sep 4, 2026"; "2026-07" -> "Jul 2026".
  // Date-only strings are read as LOCAL days on purpose: new Date("2026-09-04")
  // is UTC midnight, which prints as the 3rd anywhere west of Greenwich.
  function fmtDayLong(v) {
    if (!v) return "";
    try {
      var str = String(v).trim(), d;
      var ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(str);
      var ym = /^(\d{4})-(\d{2})$/.exec(str);
      if (ymd) d = new Date(+ymd[1], +ymd[2] - 1, +ymd[3]);
      else if (ym) d = new Date(+ym[1], +ym[2] - 1, 1);
      else d = new Date(str);
      if (isNaN(d.getTime())) return "";
      if (ym) return MON[d.getMonth()] + " " + d.getFullYear();
      return MON[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
    } catch (e) { return ""; }
  }

  // Keep-a-Changelog section names we know how to label and colour.
  var SEC_LABEL = {
    added: "Added", changed: "Changed", fixed: "Fixed", security: "Security",
    removed: "Removed", deprecated: "Deprecated"
  };

  var NOTES_HEAD = 3;          // items shown before "Show all"
  var NOTES_MAX_CHARS = 24000; // hard ceiling on notes we will parse at all

  // markdown inline -> HTML. Escapes EVERYTHING first, then promotes the two
  // constructs the changelog actually uses. Unbalanced ** or ` stay literal.
  function inlineMD(str) {
    var parts = String(str == null ? "" : str).split(/(`[^`\n]+`)/g);
    var out = "";
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (!p) continue;
      if (i % 2 === 1) {
        out += "<code>" + E(p.slice(1, -1)) + "</code>";
      } else {
        // E() emits none of * ` so the bold pass is safe on escaped text
        out += E(p).replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
      }
    }
    return out;
  }

  // release body -> { version, date, intro, sections:[{key,label,items:[{text,para}]}] }
  // Wrapped continuation lines are joined back into one item; blank lines end
  // an item; anything before the first heading becomes the intro.
  function parseNotes(md) {
    var src = String(md == null ? "" : md).replace(/\r\n?/g, "\n");
    if (src.length > NOTES_MAX_CHARS) src = src.slice(0, NOTES_MAX_CHARS);
    var out = { version: "", date: "", intro: "", sections: [] };
    var lines = src.split("\n");
    var intro = [], cur = null, buf = null, para = false;

    function open(label) {
      var raw = String(label || "").trim();
      var key = raw.toLowerCase().replace(/[^a-z]+/g, "");
      if (!SEC_LABEL[key]) key = "other";
      cur = { key: key, label: SEC_LABEL[key] || raw, items: [] };
      out.sections.push(cur);
    }
    function flush() {
      if (buf === null) return;
      var t = buf.replace(/[ \t]+/g, " ").trim();
      buf = null;
      if (!t) return;
      if (!cur && para) { intro.push(t); return; }
      if (!cur) open("");
      cur.items.push({ text: t, para: !!para });
    }

    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      var head = /^ {0,3}#{1,6} +(.*?) *#* *$/.exec(ln);
      if (head) {
        flush();
        var lbl = head[1].trim();
        // "## [1.0.1] - 2026-09-04" names the release, it is not a section
        var vm = /^\[([^\]]+)\] *(?:[-–] *(\S+))?$/.exec(lbl);
        if (vm) {
          out.version = vm[1].trim();
          if (vm[2]) out.date = vm[2];
          cur = null;
          continue;
        }
        open(lbl);
        continue;
      }
      var bul = /^ {0,3}[-*+] +(.*)$/.exec(ln);
      if (bul) { flush(); buf = bul[1]; para = false; continue; }
      if (!ln.trim()) { flush(); continue; }
      if (buf !== null) { buf += " " + ln.trim(); continue; }
      buf = ln.trim(); para = true;
    }
    flush();
    out.intro = intro.join(" ");
    out.sections = out.sections.filter(function (x) { return x.items.length; });
    return out;
  }

  // the block itself. opts: {notes|parsed, version, date, url, open}
  // `version` is omitted by the caller when the surrounding box already says it.
  function whatsNewHTML(opts) {
    opts = opts || {};
    var p = opts.parsed || parseNotes(opts.notes);
    if (!p.sections.length && !p.intro) return "";
    var open = opts.open || {};
    var ver = opts.version || "";
    var day = fmtDayLong(opts.date || p.date || "");

    var h = '<div class="upd-new"><div class="upd-new-head">' +
      '<span class="upd-new-t">What’s new</span>' +
      (ver ? '<span class="upd-new-v upd-mono">' + E(ver) + "</span>" : "") +
      (day ? '<span class="upd-new-d">' + E(day) + "</span>" : "") +
      (opts.url
        ? '<a class="upd-new-link" href="' + E(opts.url) +
          '" target="_blank" rel="noreferrer noopener">Release page</a>'
        : "") +
      "</div>";
    if (p.intro) h += '<p class="upd-new-intro">' + inlineMD(p.intro) + "</p>";

    for (var i = 0; i < p.sections.length; i++) {
      var sec = p.sections[i];
      var hid = sec.items.length - NOTES_HEAD;
      var isOpen = !!open[i];
      h += '<div class="upd-sec upd-k-' + E(sec.key) + (isOpen ? " open" : "") +
           '" data-sec="' + i + '">';
      if (sec.label) {
        h += '<div class="upd-sec-h"><i class="upd-dotk" aria-hidden="true"></i>' +
             E(sec.label) + "</div>";
      }
      h += '<ul class="upd-items">';
      for (var j = 0; j < sec.items.length; j++) {
        var it = sec.items[j];
        var cls = (it.para ? "upd-para" : "") + (j >= NOTES_HEAD ? " upd-hid" : "");
        h += "<li" + (cls.trim() ? ' class="' + cls.trim() + '"' : "") + ">" +
             inlineMD(it.text) + "</li>";
      }
      h += "</ul>";
      if (hid > 0) {
        var label = "Show all (" + hid + " more)";
        h += '<button type="button" class="upd-more" data-more="' + i +
             '" data-label="' + E(label) + '" aria-expanded="' +
             (isOpen ? "true" : "false") + '">' +
             E(isOpen ? "Show less" : label) + "</button>";
      }
      h += "</div>";
    }
    return h + "</div>";
  }

  // does this release replace the app bundle? (a macOS app asset, or notes that
  // say so). Pre-update this is a heuristic; after an update the backend's
  // last_result.app_changed is authoritative.
  function releaseTouchesApp(check) {
    if (!check) return false;
    var assets = check.assets || [];
    for (var i = 0; i < assets.length; i++) {
      var n = String((assets[i] && assets[i].name) || "");
      if (/macos|\.app\.zip$|-arm64\.zip$/i.test(n)) return true;
    }
    return /(^|\s)app\//.test(String(check.notes || ""));
  }

  function bytes(n) {
    n = Number(n || 0);
    if (!n) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function CSS() {
    return '<style>' +
      '#' + CARD_ID + ' .upd-head{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;margin:2px 0 4px}' +
      '#' + CARD_ID + ' .upd-ver{font-size:15px;font-weight:600}' +
      '#' + CARD_ID + ' .upd-sub{font-size:11.5px;color:var(--muted)}' +
      '#' + CARD_ID + ' .upd-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}' +
      '#' + CARD_ID + ' .upd-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 0}' +
      '#' + CARD_ID + ' .upd-row label{font-size:12px;color:var(--muted)}' +
      '#' + CARD_ID + ' select.upd-ch{padding:5px 9px;border-radius:8px;font-size:12.5px;color:inherit;' +
        'border:1px solid var(--hairline,rgba(255,255,255,.12));background:var(--glass-2,rgba(255,255,255,.04))}' +
      '#' + CARD_ID + ' button.upd-b{padding:6px 13px;border-radius:9px;font-size:12.5px;cursor:pointer;' +
        'border:1px solid var(--hairline,rgba(255,255,255,.12));background:var(--glass-2,rgba(255,255,255,.05));color:inherit}' +
      '#' + CARD_ID + ' button.upd-b:disabled{opacity:.5;cursor:default}' +
      '#' + CARD_ID + ' button.upd-go{border:0;background:var(--iris,#6b8afd);color:#fff;font-weight:600}' +
      '#' + CARD_ID + ' .upd-avail{margin-top:12px;padding:11px 13px;border-radius:12px;' +
        'border:1px solid color-mix(in srgb,var(--iris,#6b8afd) 45%,transparent);' +
        'background:color-mix(in srgb,var(--iris,#6b8afd) 12%,transparent)}' +
      '#' + CARD_ID + ' .upd-avail h3{margin:0 0 2px;font-size:13.5px;font-weight:650}' +
      '#' + CARD_ID + ' .upd-notes{margin:8px 0 0;padding:8px 10px;border-radius:9px;max-height:190px;overflow:auto;' +
        'background:var(--glass-2,rgba(255,255,255,.04));font-family:ui-monospace,SFMono-Regular,Menlo,monospace;' +
        'font-size:11.5px;line-height:1.5;white-space:pre-wrap;word-break:break-word}' +
      '#' + CARD_ID + ' .upd-warn{margin-top:9px;font-size:11.5px;line-height:1.5;color:var(--warn,#d99a2e)}' +
      '#' + CARD_ID + ' .upd-note{margin-top:9px;font-size:11.5px;line-height:1.5;color:var(--muted)}' +
      '#' + CARD_ID + ' .upd-log{margin-top:9px;padding:8px 10px;border-radius:9px;max-height:200px;overflow:auto;' +
        'background:rgba(0,0,0,.28);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;' +
        'line-height:1.45;white-space:pre-wrap;word-break:break-word}' +
      '#' + CARD_ID + ' .upd-err{font-size:11.5px;color:var(--bad,#d24c3c);margin-top:8px}' +
      '#' + CARD_ID + ' .upd-dirty{font-size:11.5px;color:var(--muted);margin-top:8px}' +
      '#' + CARD_ID + ' .upd-assets{margin-top:7px;display:flex;flex-wrap:wrap;gap:5px}' +
      '#' + CARD_ID + ' .upd-asset{font-size:10.5px;padding:1px 8px;border-radius:20px;color:var(--muted);' +
        'background:var(--glass-2,rgba(255,255,255,.06))}' +
      // ---- "What's new" -------------------------------------------------
      '#' + CARD_ID + ' .upd-new{margin-top:10px;padding:9px 11px 10px;border-radius:var(--radius-xs,9px);' +
        'border:1px solid var(--hairline,rgba(255,255,255,.12));background:var(--chip,rgba(255,255,255,.05))}' +
      '#' + CARD_ID + ' .upd-new-head{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;' +
        'padding-bottom:7px;margin-bottom:8px;border-bottom:1px solid var(--hairline,rgba(255,255,255,.12))}' +
      '#' + CARD_ID + ' .upd-new-t{font-size:12px;font-weight:650;color:var(--ink);text-wrap:balance}' +
      '#' + CARD_ID + ' .upd-new-v{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}' +
      '#' + CARD_ID + ' .upd-new-d{font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}' +
      '#' + CARD_ID + ' .upd-new-link{margin-left:auto;font-size:11px;color:var(--muted);text-decoration:none;' +
        'border-bottom:1px solid transparent;padding-bottom:1px;' +
        'transition-property:color,border-color;transition-duration:150ms;transition-timing-function:ease-out}' +
      '#' + CARD_ID + ' .upd-new-link:hover{color:var(--iris,#6b8afd);border-bottom-color:currentColor}' +
      '#' + CARD_ID + ' .upd-new-intro{margin:0 0 9px;font-size:11.5px;line-height:1.55;color:var(--muted);' +
        'text-wrap:pretty}' +
      '#' + CARD_ID + ' .upd-sec + .upd-sec{margin-top:9px}' +
      '#' + CARD_ID + ' .upd-sec-h{display:flex;align-items:center;gap:6px;margin:0 0 4px;font-size:9.5px;' +
        'font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}' +
      '#' + CARD_ID + ' .upd-dotk{flex:0 0 auto;width:5px;height:5px;border-radius:50%;background:var(--faint)}' +
      '#' + CARD_ID + ' .upd-k-added .upd-dotk{background:var(--ok,#2E9E68)}' +
      '#' + CARD_ID + ' .upd-k-changed .upd-dotk{background:var(--iris,#6b8afd)}' +
      '#' + CARD_ID + ' .upd-k-fixed .upd-dotk{background:var(--quick,#2E93C4)}' +
      '#' + CARD_ID + ' .upd-k-security .upd-dotk{background:var(--warn,#B9821A)}' +
      '#' + CARD_ID + ' .upd-k-removed .upd-dotk,#' + CARD_ID + ' .upd-k-deprecated .upd-dotk' +
        '{background:var(--bad,#D24C3C)}' +
      '#' + CARD_ID + ' .upd-items{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:4px}' +
      '#' + CARD_ID + ' .upd-items li{position:relative;padding-left:13px;font-size:11.5px;line-height:1.55;' +
        'color:var(--ink);text-wrap:pretty}' +
      '#' + CARD_ID + ' .upd-items li::before{content:"";position:absolute;left:3px;top:.66em;width:3px;' +
        'height:3px;border-radius:50%;background:var(--faint)}' +
      '#' + CARD_ID + ' .upd-items li.upd-para{padding-left:0;color:var(--muted)}' +
      '#' + CARD_ID + ' .upd-items li.upd-para::before{content:none}' +
      '#' + CARD_ID + ' .upd-items strong{font-weight:650;color:var(--ink)}' +
      '#' + CARD_ID + ' .upd-new code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10.5px;' +
        'padding:1px 4px;border-radius:5px;word-break:break-word;color:var(--ink);' +
        'background:color-mix(in srgb,var(--ink) 9%,transparent)}' +
      '#' + CARD_ID + ' .upd-sec:not(.open) li.upd-hid{display:none}' +
      '#' + CARD_ID + ' button.upd-more{position:relative;margin:5px 0 0;padding:2px 0;border:0;' +
        'background:none;color:var(--muted);font-size:11px;line-height:1.4;cursor:pointer;' +
        'transition-property:color;transition-duration:150ms;transition-timing-function:ease-out}' +
      // the label is 19px tall; the pseudo-element gives it a 41x41 hit area
      '#' + CARD_ID + ' button.upd-more::after{content:"";position:absolute;inset:-11px -14px}' +
      '#' + CARD_ID + ' button.upd-more:hover{color:var(--iris,#6b8afd);transform:none;border-color:transparent}' +
      '@keyframes upd-in{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}' +
      '#' + CARD_ID + ' .upd-sec.open.upd-just li.upd-hid{animation:upd-in 180ms ease-out both}' +
      '@media (prefers-reduced-motion:reduce){#' + CARD_ID +
        ' .upd-sec.open.upd-just li.upd-hid{animation:none}}' +
      '#tab-mind{position:relative}' +
      '#tab-mind .upd-dot{position:absolute;top:2px;right:6px;width:7px;height:7px;border-radius:50%;' +
        'background:var(--iris,#6b8afd);box-shadow:0 0 0 2px var(--bg-2,rgba(0,0,0,.35));display:block}' +
      '</style>';
  }

  // Release notes for the version we are RUNNING. /api/update/check only
  // carries the newest release's body, so that only helps while it happens to
  // be the one installed; otherwise we use the CHANGELOG.md section fetched
  // from /api/update/notes. Returns null when neither source has anything.
  function verKey(v) { return String(v == null ? "" : v).trim().replace(/^v/i, ""); }

  function currentNotes(state) {
    state = state || {};
    var ver = state.ver || {}, chk = state.check || {}, cn = state.notes || null;
    var mine = verKey(ver.version);
    if (!mine) return null;
    if (cn && cn.notes && verKey(cn.version) === mine) return cn;
    if (chk.notes && chk.source !== "main" && verKey(chk.latest) === mine) {
      return { version: chk.latest || ver.version, notes: chk.notes,
               date: chk.published_at || "", url: chk.url || "" };
    }
    return (cn && cn.notes) ? cn : null;
  }

  // ---- the card body -------------------------------------------------------
  // state = { ver, check, status, busy, msg, err, notes, notesOpen }
  function cardHTML(state) {
    state = state || {};
    var ver = state.ver || {};
    var chk = state.check || {};
    var st = state.status || {};
    var running = !!st.running;

    var verLine = "Hermes Assistant v" + E(ver.version || "?") +
      (ver.commit ? ' <span class="upd-sub upd-mono">(' + E(ver.commit) + ")</span>" : "");
    var bits = [];
    bits.push(ver.checkout === "tarball" ? "tarball install" : "git checkout");
    if (ver.dirty) bits.push("local changes present");
    bits.push("checked " + E(fmtWhen(chk.checked_at)));

    var h = CSS() +
      '<h2 style="display:flex;align-items:center;gap:7px">' + GLYPH + "Software update</h2>" +
      '<div class="upd-head"><span class="upd-ver">' + verLine + "</span>" +
      '<span class="upd-sub">' + E(bits.join(" · ")) + "</span></div>";

    // channel + check row
    var tarball = ver.checkout === "tarball";
    h += '<div class="upd-row">' +
      '<label for="upd-channel">Channel</label>' +
      '<select class="upd-ch" id="upd-channel"' + (running ? " disabled" : "") + ">" +
      '<option value="stable"' + (ver.channel !== "main" ? " selected" : "") + ">Stable — release tags</option>" +
      '<option value="main"' + (ver.channel === "main" ? " selected" : "") +
      (tarball ? " disabled" : "") + ">Main — development branch" + (tarball ? " (git checkouts only)" : "") + "</option>" +
      "</select>" +
      '<button type="button" class="upd-b" data-act="check"' + (state.busy || running ? " disabled" : "") + ">" +
      (state.busy === "check" ? "Checking…" : "Check for updates") + "</button>" +
      "</div>";

    if (chk.error && !chk.update_available) {
      h += '<div class="upd-err">Update check: ' + E(chk.error) +
        (chk.stale ? " (showing the last successful check)" : "") + "</div>";
    }

    // the offer
    if (chk.update_available && chk.latest) {
      var appWarn = releaseTouchesApp(chk);
      var parsed = parseNotes(chk.notes);
      var haveNotes = !!(parsed.sections.length || parsed.intro);
      // "What's new" carries the date and the release link once notes render,
      // so the summary line below the headline stands down rather than saying
      // the same thing twice (the main channel has no date, so it keeps it).
      var showSub = !haveNotes || chk.source === "main";
      h += '<div class="upd-avail">' +
        "<h3>" + E(chk.latest) + " is available</h3>";
      if (showSub) {
        h += '<div class="upd-sub">' +
          E(chk.source === "main"
            ? "origin/main is ahead of this checkout"
            : ("released " + (fmtDayLong(chk.published_at) || "recently"))) +
          (chk.url ? ' · <a href="' + E(chk.url) + '" target="_blank" rel="noreferrer noopener">Release page</a>' : "") +
          "</div>";
      }
      if (haveNotes) {
        h += whatsNewHTML({ parsed: parsed, date: chk.published_at,
                            url: showSub ? "" : chk.url, open: state.notesOpen });
      } else if (chk.notes) {
        h += '<pre class="upd-notes">' + notesPreview(chk.notes, 12) + "</pre>";
      }
      if ((chk.assets || []).length) {
        h += '<div class="upd-assets">' + chk.assets.slice(0, 6).map(function (a) {
          return '<span class="upd-asset">' + E(a.name) +
            (a.size ? " · " + E(bytes(a.size)) : "") + "</span>";
        }).join("") + "</div>";
      }
      h += '<div class="upd-row">' +
        '<button type="button" class="upd-b upd-go" data-act="apply" data-target="' +
        E(chk.source === "main" ? "latest" : chk.latest) + '"' +
        (running || state.busy || ver.dirty ? " disabled" : "") + ">" +
        (running ? "Updating…" : "Update to " + E(chk.latest)) + "</button></div>";
      if (ver.dirty) {
        h += '<div class="upd-dirty">This checkout has uncommitted changes, so the ' +
          "updater will not move it. Commit or stash them (or run " +
          '<span class="upd-mono">./update.sh --force</span> in a terminal, which stashes ' +
          "them for you) and check again.</div>";
      }
      if (appWarn) {
        h += '<div class="upd-warn">This release also ships a new app bundle. ' +
          "Updating replaces the dashboard code only; to replace the window itself run " +
          '<span class="upd-mono">./update.sh --rebuild-app</span>. Replacing the app bundle ' +
          "DROPS its Full Disk Access grant — you must re-add Hermes Assistant.app under " +
          "System Settings › Privacy &amp; Security › Full Disk Access afterwards, or the " +
          "Message Center goes blank.</div>";
      }
      h += '<div class="upd-note">When you update, the dashboard restarts itself; the app ' +
        "window reloads when it reconnects. Your data in ~/.hermes is never touched, and the " +
        "model servers stay asleep.</div>";
      h += "</div>";
    } else if (!running) {
      h += '<div class="upd-note">' +
        E(chk.latest && !chk.error
          ? "Up to date — " + chk.latest + " is the newest release."
          : "No newer release found.") +
        "</div>";
      // nothing to offer: show what the version you ARE running brought.
      var cn = currentNotes(state);
      if (cn) {
        h += whatsNewHTML({ notes: cn.notes, version: cn.version, date: cn.date,
                            url: cn.url, open: state.notesOpen });
      }
    }

    // live progress
    if (running || state.busy === "apply") {
      h += '<div class="upd-note">Updating' +
        (st.target ? " to " + E(st.target) : "") +
        " — the dashboard restarts itself when it finishes; this page reloads once it " +
        "reconnects. Safe to leave open.</div>";
      h += '<pre class="upd-log">' + E(st.log_tail || "starting…") + "</pre>";
    } else if (st.last_result) {
      var lr = st.last_result;
      h += '<div class="' + (lr.ok ? "upd-note" : "upd-err") + '">Last update: ' +
        E(lr.message || (lr.ok ? "ok" : "failed")) +
        (lr.finished_at ? " · " + E(fmtWhen(lr.finished_at)) : "") + "</div>";
      if (lr.ok && lr.app_changed) {
        h += '<div class="upd-warn">That release changed app/. Run ' +
          '<span class="upd-mono">./update.sh --rebuild-app</span> to replace the app bundle, ' +
          "then re-add Hermes Assistant.app under System Settings › Privacy &amp; Security › " +
          "Full Disk Access (an ad-hoc rebuild always drops that grant).</div>";
      }
    }

    if (state.msg) h += '<div class="upd-note">' + E(state.msg) + "</div>";
    if (state.err) h += '<div class="upd-err">' + E(state.err) + "</div>";
    h += '<div class="upd-note upd-sub">Terminal equivalent: ' +
      '<span class="upd-mono">./update.sh</span> in the install directory.</div>';
    return h;
  }

  // ---- live wiring ---------------------------------------------------------
  var S = { ver: null, check: null, status: null, busy: "", msg: "", err: "",
            notes: null, notesOpen: {} };
  var timer = null, mounted = false;

  async function jget(url) {
    var r = await fetch(url, { headers: { "Accept": "application/json" } });
    return await r.json();
  }
  async function jpost(url, body) {
    var r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    var j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    return j || { ok: false, error: "HTTP " + r.status };
  }

  function host() {
    var d = D();
    if (!d) return null;
    return d.getElementById("sec-system") || d.getElementById("view-mind");
  }

  function paint() {
    var d = D();
    if (!d) return;
    var el = d.getElementById(CARD_ID);
    if (!el) {
      var h = host();
      if (!h) return;
      el = d.createElement("section");
      el.id = CARD_ID;
      el.className = "card glass";
      h.appendChild(el);
    } else if (el.parentNode && el.parentNode.id === "view-mind") {
      // the shell built its panels after we mounted — move ourselves in
      var sys = d.getElementById("sec-system");
      if (sys && sys !== el.parentNode) { try { sys.appendChild(el); } catch (e) {} }
    }
    try { el.innerHTML = cardHTML(S); } catch (e) { return; }
    wire(el);
    gearDot(!!(S.check && S.check.update_available));
  }

  function wire(el) {
    if (!el || !el.querySelector) return;
    var sel = el.querySelector("#upd-channel");
    if (sel) sel.onchange = function () { setChannel(sel.value); };
    var btns = el.querySelectorAll ? el.querySelectorAll("button[data-act]") : [];
    Array.prototype.slice.call(btns).forEach(function (b) {
      b.onclick = function () {
        var act = b.getAttribute("data-act");
        if (act === "check") doCheck(true);
        else if (act === "apply") doApply(b.getAttribute("data-target") || "latest");
      };
    });
    // "Show all" expands one notes section in place — no repaint, so nothing
    // else in the card flickers; S.notesOpen keeps it open across repaints.
    var mores = el.querySelectorAll ? el.querySelectorAll("button.upd-more") : [];
    Array.prototype.slice.call(mores).forEach(function (b) {
      b.onclick = function () {
        var sec = b.parentNode;
        if (!sec || !sec.classList) return;
        var open = !sec.classList.contains("open");
        sec.classList.toggle("open", open);
        sec.classList.toggle("upd-just", open);   // one-shot entrance, click only
        b.setAttribute("aria-expanded", open ? "true" : "false");
        b.textContent = open ? "Show less" : (b.getAttribute("data-label") || "Show all");
        S.notesOpen[b.getAttribute("data-more")] = open;
      };
    });
  }

  function gearDot(on) {
    var d = D();
    if (!d) return;
    var tab = d.getElementById("tab-mind");
    if (!tab) return;
    var dot = tab.querySelector ? tab.querySelector(".upd-dot") : null;
    if (on && !dot) {
      dot = d.createElement("i");
      dot.className = "upd-dot";
      dot.setAttribute("title", "An update is available");
      dot.setAttribute("aria-hidden", "true");
      tab.appendChild(dot);
    } else if (!on && dot && dot.parentNode) {
      dot.parentNode.removeChild(dot);
    }
  }

  async function loadVersion() {
    try { S.ver = await jget("/api/version"); } catch (e) {}
  }

  // CHANGELOG.md section for the running version — only fetched when the
  // update check did not already hand us that release's body.
  async function loadCurrentNotes() {
    var mine = verKey((S.ver || {}).version);
    if (!mine || currentNotes(S)) return;
    try {
      var j = await jget("/api/update/notes?version=" + encodeURIComponent(mine));
      if (j && j.ok && j.notes) S.notes = j;
    } catch (e) {}
  }

  async function doCheck(force) {
    S.busy = "check"; S.err = ""; S.msg = ""; S.notesOpen = {}; paint();
    try {
      S.check = await jget("/api/update/check" + (force ? "?force=1" : ""));
    } catch (e) {
      S.err = "Could not reach the update service.";
    }
    S.busy = ""; paint();
    if (mounted) { try { await loadCurrentNotes(); paint(); } catch (e) {} }
  }

  async function setChannel(ch) {
    S.busy = "channel"; S.err = ""; paint();
    var r = await jpost("/api/update/channel", { channel: ch });
    S.busy = "";
    if (r && r.ok) {
      S.msg = "Channel set to " + ch + ".";
      await loadVersion();
      await doCheck(true);
    } else {
      S.err = (r && (r.error || r.reason)) || "Could not change the channel.";
      paint();
    }
  }

  async function doApply(target) {
    S.busy = "apply"; S.err = ""; S.msg = ""; paint();
    var r = await jpost("/api/update/apply", { target: target });
    S.busy = "";
    if (!r || !r.ok) {
      var msg = (r && (r.error || r.reason)) || "Could not start the update.";
      if (r && r.dirty && r.dirty.length) {
        msg += "  Uncommitted: " + r.dirty.slice(0, 8).join(", ") +
               (r.dirty.length > 8 ? " (+" + (r.dirty.length - 8) + " more)" : "");
      }
      S.err = msg; paint(); return;
    }
    S.msg = ""; S.status = { running: true, target: target, log_tail: "starting…" };
    paint();
    pollStatus();
  }

  var reconnecting = false;

  function pollStatus() {
    if (timer) { clearTimeout(timer); timer = null; }
    timer = setTimeout(async function () {
      var st = null;
      try {
        st = await jget("/api/update/status");
      } catch (e) {
        // the dashboard is restarting itself — expected, mid-update
        reconnecting = true;
        S.status = S.status || {};
        S.status.running = true;
        S.status.log_tail = (S.status.log_tail || "") + "\n[dashboard restarting…]";
        paint();
        pollStatus();
        return;
      }
      S.status = st;
      if (reconnecting && !st.running) {
        // came back after the restart: reload so the page matches the new code
        reconnecting = false;
        paint();
        try { if (W.location && W.location.reload) W.location.reload(); } catch (e) {}
        return;
      }
      paint();
      if (st && st.running) pollStatus();
      else {
        await loadVersion();
        await doCheck(true);
      }
    }, POLL_MS);
  }

  async function mount() {
    var d = D();
    if (!d || !host()) return;
    if (!mounted) {
      mounted = true;
      await loadVersion();
      await doCheck(false);          // cached: costs nothing, never blocks
      await loadCurrentNotes();      // local file read; no network
      try {
        var st = await jget("/api/update/status");
        S.status = st;
        if (st && st.running) { paint(); pollStatus(); return; }
      } catch (e) {}
    }
    paint();
  }

  // chain window.mindExtras exactly like the other aux settings cards
  var prev = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await mount(); } catch (e) {}
  };

  // the gear dot should appear even if the user never opens Settings
  async function boot() {
    try {
      await loadVersion();
      var c = await jget("/api/update/check");
      S.check = c;
      gearDot(!!(c && c.update_available));
    } catch (e) {}
  }
  try {
    if (typeof document !== "undefined" && document.readyState === "loading" &&
        document.addEventListener) {
      document.addEventListener("DOMContentLoaded", function () { boot(); }, { once: true });
    } else if (typeof document !== "undefined") {
      boot();
    }
  } catch (e) {}

  // headless-harness surface (also handy from the console)
  W.hermesUpdate = {
    cardHTML: cardHTML, notesPreview: notesPreview, fmtWhen: fmtWhen,
    fmtDay: fmtDay, fmtDayLong: fmtDayLong, releaseTouchesApp: releaseTouchesApp,
    bytes: bytes, parseNotes: parseNotes, inlineMD: inlineMD,
    whatsNewHTML: whatsNewHTML, currentNotes: currentNotes,
    check: doCheck, state: S
  };
})();
