// aux_needsyou.js — the "Needs you" attention stream (release 1.1.2).
//
// Auto-served at /aux_needsyou.js; the <script> tag lives at the end of
// index.html's aux list so these map assignments win. Adds:
//   * WICONS.needsyou        — bespoke two-tone glyph (widgetIcon)
//   * RENDER.needsyou        — the hub stream: a "Now" group (max 5), a
//                              collapsed "Today (n)", a "Later / Never"
//                              footer, and one quiet trust line
//   * EXPAND_RENDER.needsyou — the pop-out: every bucket, same actions, plus
//                              a "Why?" toggle that shows the RULE reason
//                              even when the optional model pass moved it
//
// GitHub rows (round 3) carry their own glyph and chip; "Open" on a pending
// change opens aux_github.js's review sheet (window.hermesGitHub.openReview,
// else #github-review=<id>), and on a connector notice Settings › Connections.
//
// Reads /api/needsyou (cached server-side, never blocks) and posts to
// /api/needsyou/act. Nothing here ever wakes the model: "Draft reply" posts
// the action and, when the server answers {error:"model asleep"}, offers the
// existing wake affordance instead of forcing a 30 s cold start.
//
// House rules honoured: zero emoji (bespoke SVG only), 12-hour times, esc()
// on every interpolation, tokens only (--bad = now, --warn = today,
// --muted = later), no cards inside cards (rows are flat, separated by a
// hairline), text-wrap: pretty, >=40px hit targets, explicit
// transition-property, and a light/dark palette that comes entirely from the
// shell's tokens so both themes are automatic.
//
// Motion (docs/plans/motion-system-2026-10.md 8.8), all through HA(): an OK
// Done / Snooze / re-file lets the row leave (Done: "dismiss", the others:
// "defer"), and once it has gone AND the new list is here the rows that stay
// glide into the gap (a Flip keyed by data-flip-id) while the widget's Now
// count ticks to its new number.  An error answer moves nothing: the row
// stays, with its message.  Every other repaint (the hub's 60 s refresh, a
// toggle, a draft status) is still: no exit, no Flip, and the count only
// writes its text.  Reduced motion, a hidden window or no runtime land the
// same end state at once.

(function () {
  "use strict";

  // ---- motion -------------------------------------------------------------
  // index.html's HA() (the motion runtime, aux_anim.js, or its instant
  // ANIM_NULL) is a top-level const: visible here by name, not on window.
  // Resolved per call.  A page or a headless harness with neither gets
  // NY_NULL, which lands every call at its end state before it returns.
  var NY_H = { done: true, finished: Promise.resolve(), cancel: function () {}, finish: function () {} };
  var NY_NULL = {
    exit: function (t, p, done) { if (typeof done === "function") done(); return NY_H; },
    flip: function (s, mutate) { if (typeof mutate === "function") mutate(); return NY_H; },
    count: function (el, to, o) {
      var s = (o && o.fmt) ? String(o.fmt(+to)) : String(Math.round(+to || 0));
      if (el && el.textContent !== s) el.textContent = s;
      if (el) el.__haVal = +to;
      return NY_H;
    }
  };
  function anim() {
    try { if (typeof HA === "function") { var a = HA(); if (a) return a; } } catch (e) {}
    return (typeof window !== "undefined" && window.HermesAnim) || NY_NULL;
  }
  function NOWFMT(n) { return Math.round(n) + " now"; }

  // ---- guarded helpers (headless node eval must never throw) --------------
  function E(s) {
    return (typeof esc === "function") ? esc(s)
      : String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
  }
  function REL(ts) {
    if (!ts) return "";
    if (typeof relTime === "function") return relTime(ts);
    var s = Date.now() / 1000 - ts;
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function T12(ts) {
    if (!ts) return "";
    try {
      return new Date(ts * 1000).toLocaleTimeString([],
        { hour: "numeric", minute: "2-digit" });
    } catch (e) { return ""; }
  }
  // a deadline reads better as a countdown than as a clock
  function DUE(ts) {
    if (!ts) return "";
    var s = ts - Date.now() / 1000;
    if (s < -3600) return "overdue " + Math.round(-s / 3600) + "h";
    if (s < 0) return "overdue";
    if (s < 3600) return "in " + Math.max(1, Math.round(s / 60)) + "m";
    if (s < 86400) return T12(ts);
    return "";
  }
  function PCT(v) {
    return (v == null) ? null : Math.round(v * 100) + "%";
  }

  // ---- bespoke glyphs, one per source ------------------------------------
  var GLYPH = {
    message:
      '<path d="M20 12a7 7 0 0 1-7 7H8l-4 3v-4.2A7 7 0 0 1 4 12a7 7 0 0 1 7-7h2a7 7 0 0 1 7 7z" fill="currentColor" opacity=".14"/>' +
      '<path d="M20 12a7 7 0 0 1-7 7H8l-4 3v-4.2A7 7 0 0 1 4 12a7 7 0 0 1 7-7h2a7 7 0 0 1 7 7z" stroke-linejoin="round"/>',
    calendar:
      '<rect x="3.5" y="5.5" width="17" height="15" rx="2.5" fill="currentColor" opacity=".14"/>' +
      '<rect x="3.5" y="5.5" width="17" height="15" rx="2.5"/>' +
      '<path d="M3.5 10h17M8 3.5v4M16 3.5v4" stroke-linecap="round"/>',
    watchtower:
      '<path d="M4.5 8.5h11l4-3v13l-4-3h-11z" fill="currentColor" opacity=".14"/>' +
      '<path d="M4.5 8.5h11l4-3v13l-4-3h-11z" stroke-linejoin="round"/>' +
      '<path d="M7.5 15.5v3.5" stroke-linecap="round"/>',
    approval:
      '<path d="M12 3.5l7 3v5.2c0 4.2-2.9 7.6-7 8.8-4.1-1.2-7-4.6-7-8.8V6.5z" fill="currentColor" opacity=".14"/>' +
      '<path d="M12 3.5l7 3v5.2c0 4.2-2.9 7.6-7 8.8-4.1-1.2-7-4.6-7-8.8V6.5z" stroke-linejoin="round"/>' +
      '<path d="M12 9v3.5M12 15.5h.01" stroke-linecap="round"/>',
    reminder:
      '<path d="M6.5 10a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5s1.5-1.5 1.5-5.5z" fill="currentColor" opacity=".14"/>' +
      '<path d="M6.5 10a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5s1.5-1.5 1.5-5.5z" stroke-linejoin="round"/>' +
      '<path d="M10 18.5a2 2 0 0 0 4 0" stroke-linecap="round"/>',
    email:
      '<rect x="3.5" y="5.5" width="17" height="13" rx="2.5" fill="currentColor" opacity=".14"/>' +
      '<rect x="3.5" y="5.5" width="17" height="13" rx="2.5"/>' +
      '<path d="M4 8l8 5.5L20 8" stroke-linecap="round" stroke-linejoin="round"/>',
    // a branch: two commits on a line and one forking off (no brand mark)
    github:
      '<circle cx="7" cy="5.5" r="2.3" fill="currentColor" opacity=".14"/>' +
      '<circle cx="7" cy="18.5" r="2.3" fill="currentColor" opacity=".14"/>' +
      '<circle cx="17" cy="8" r="2.3" fill="currentColor" opacity=".14"/>' +
      '<circle cx="7" cy="5.5" r="2.3"/><circle cx="7" cy="18.5" r="2.3"/>' +
      '<circle cx="17" cy="8" r="2.3"/>' +
      '<path d="M7 7.8v8.4M17 10.3c0 4.2-4.6 4.4-8.4 6.5" stroke-linecap="round"/>'
  };
  function glyph(src) {
    var g = GLYPH[src] || GLYPH.message;
    return '<svg class="ny-ic" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="1.6" aria-hidden="true">' + g + "</svg>";
  }

  if (typeof WICONS !== "undefined") {
    // an inbox tray with one thing standing up out of it
    WICONS.needsyou =
      '<path d="M3.5 13.5h4l1.2 2.5h6.6l1.2-2.5h4v4a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" fill="currentColor" opacity=".16"/>' +
      '<path d="M3.5 13.5h4l1.2 2.5h6.6l1.2-2.5h4v4a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" stroke-linejoin="round"/>' +
      '<path d="M12 3.5v7M9 8l3 3 3-3" stroke-linecap="round" stroke-linejoin="round"/>';
  }

  // ---- styles: tokens only, injected once --------------------------------
  var NY_CSS = [
    ".ny-wrap{display:flex;flex-direction:column;gap:2px;text-wrap:pretty}",
    // a Done slides its row 16px to the right (exit "dismiss"); inside the
    // card's scroll body that is sideways overflow for the length of the slide,
    // i.e. a horizontal scrollbar flashing in and out for anyone on classic
    // scrollbars.  This list never scrolls sideways (every line wraps or
    // ellipsises), so its body simply may not.
    ".w-body:has(>.ny-wrap){overflow-x:hidden}",
    ".ny-grp{display:flex;align-items:center;gap:7px;font-size:11px;",
    "font-weight:600;letter-spacing:.07em;text-transform:uppercase;",
    "color:var(--muted);margin:9px 0 2px}",
    ".ny-grp:first-child{margin-top:1px}",
    ".ny-dot{width:7px;height:7px;border-radius:50%;flex:none}",
    ".ny-dot.now{background:var(--bad)}",
    ".ny-dot.today{background:var(--warn)}",
    ".ny-dot.later{background:var(--muted)}",
    // rows are FLAT — a card inside a card is the thing this hub already
    // gets wrong elsewhere; a hairline is enough separation.
    ".ny-row{display:flex;gap:10px;padding:9px 2px 8px;",
    "border-top:1px solid var(--hairline);align-items:flex-start}",
    ".ny-grp+.ny-row{border-top:none}",
    ".ny-ic{width:16px;height:16px;flex:none;margin-top:2px;color:var(--wac,var(--iris))}",
    ".ny-main{min-width:0;flex:1}",
    ".ny-top{display:flex;align-items:baseline;gap:8px}",
    ".ny-who{font-size:12.5px;font-weight:640;color:var(--ink);",
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".ny-when{margin-left:auto;font-size:12px;color:var(--muted);",
    "white-space:nowrap;flex:none;font-variant-numeric:tabular-nums}",
    ".ny-when.due{color:var(--bad);font-weight:640}",
    ".ny-sum{font-size:12px;color:var(--ink);opacity:.86;margin-top:1px;",
    "text-wrap:pretty;overflow-wrap:anywhere}",
    ".ny-why{font-size:12px;color:var(--muted);margin-top:3px;text-wrap:pretty}",
    ".ny-why b{font-weight:640;color:var(--muted)}",
    ".ny-acts{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}",
    // >=40px targets, and an explicit transition-property (never `all`)
    ".ny-b{min-height:40px;min-width:40px;padding:0 12px;border-radius:10px;",
    "border:1px solid var(--field-edge);background:var(--field);",
    "color:var(--ink);font-size:12px;font-weight:600;cursor:pointer;",
    "display:inline-flex;align-items:center;justify-content:center;",
    "transition-property:background-color,border-color,color,opacity;",
    "transition-duration:.16s;transition-timing-function:ease}",
    ".ny-b:hover{background:var(--chip);border-color:var(--edge)}",
    ".ny-b:disabled{opacity:.5;cursor:default}",
    ".ny-b.pri{background:color-mix(in srgb,var(--iris) 16%,transparent);",
    "border-color:color-mix(in srgb,var(--iris) 34%,transparent);color:var(--ink)}",
    ".ny-b.sm{font-size:12px;padding:0 10px}",
    ".ny-snz{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}",
    ".ny-more{min-height:40px;padding:0 12px;border-radius:10px;border:none;",
    "background:transparent;color:var(--muted);font-size:12px;",
    "font-weight:640;cursor:pointer;display:flex;align-items:center;gap:7px;",
    "width:100%;text-align:left;",
    "transition-property:color,background-color;transition-duration:.16s;",
    "transition-timing-function:ease}",
    ".ny-more:hover{color:var(--ink);background:var(--chip)}",
    ".ny-chev{width:13px;height:13px;flex:none;",
    "transition-property:transform;transition-duration:.18s;",
    "transition-timing-function:ease}",
    ".ny-more[aria-expanded=\"true\"] .ny-chev{transform:rotate(90deg)}",
    ".ny-foot{display:flex;align-items:center;gap:8px;flex-wrap:wrap;",
    "margin-top:9px;padding-top:8px;border-top:1px solid var(--hairline);",
    "font-size:12px;color:var(--muted);text-wrap:pretty}",
    ".ny-trust{margin-left:auto;font-size:12px;color:var(--muted);",
    "font-variant-numeric:tabular-nums;white-space:nowrap}",
    // the empty state is one text row: headline and schedule note side by
    // side where they fit, the note wrapping under the headline when not
    ".ny-empty{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:10px;row-gap:2px;",
    "padding:8px 2px 2px;text-wrap:pretty}",
    ".ny-empty .h{font-size:13px;font-weight:620;color:var(--ink)}",
    ".ny-empty .s{font-size:12px;color:var(--muted)}",
    ".ny-msg{font-size:12px;color:var(--muted);margin-top:6px;text-wrap:pretty}",
    ".ny-msg.bad{color:var(--bad)}",
    ".ny-blind{font-size:12px;color:var(--warn);margin-top:8px;text-wrap:pretty}",
    ".ny-src{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:9px}",
    ".ny-chip{font-size:12px;line-height:1.3;padding:3px 9px;border-radius:999px;",
    "background:var(--chip);color:var(--muted);border:1px solid var(--hairline)}",
    // live sources: a quiet ok dot, so the row reads "these are being read"
    ".ny-chip.live::before{content:\"\";display:inline-block;width:6px;height:6px;",
    "border-radius:50%;background:var(--ok);margin-right:6px;vertical-align:1px}",
    // sources that are off or not connected fold behind one readable toggle;
    // the list itself stays in the DOM (hidden) so it is one click away
    ".ny-src .ny-more.ny-offt{width:auto;padding:0 8px;margin-left:auto}",
    ".ny-offl{display:flex;flex-direction:column;gap:4px;margin-top:4px;padding:2px 0 2px 2px}",
    ".ny-offl[hidden]{display:none}",
    ".ny-offl .ny-chip{background:transparent;border:none;padding:2px 0;border-radius:0}",
    // a source with a known reason (Full Disk Access, a stale sync) is a
    // problem to read, not a greyed-out absence
    ".ny-chip.off.note{opacity:1;color:var(--warn);border-color:color-mix(in srgb,var(--warn) 35%,transparent)}",
    ".ny-draft{margin-top:7px;padding:9px 10px;border-radius:10px;",
    "background:var(--chip);border:1px solid var(--hairline);font-size:12px;",
    "color:var(--ink);text-wrap:pretty}",
    ".ny-draft.raw{white-space:pre-wrap}",
    ".ny-draft.md p{margin:3px 0}.ny-draft.md p:first-child{margin-top:0}.ny-draft.md p:last-child{margin-bottom:0}"
  ].join("");

  function injectCSS() {
    try {
      if (document.getElementById("ny-style")) return;
      var s = document.createElement("style");
      s.id = "ny-style";
      s.textContent = NY_CSS;
      document.head.appendChild(s);
    } catch (e) {}
  }
  if (typeof document !== "undefined") injectCSS();

  var CHEV = '<svg class="ny-chev" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true"><polyline points="9 6 15 12 9 18"/></svg>';

  // ---- view state that must survive the hub's 60 s in-place re-render ----
  var UI = { snooze: "", today: false, never: false, why: false, src: false, draft: {}, msg: {}, appr: {} };

  function post(body) {
    return fetch("/api/needsyou/act", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  // o (the act path only): {flip, act, after, failed} — wait for `after` (the
  // leaving row is gone), then repaint inside a Flip so the rows that stay
  // glide into place; `failed` runs once the row has gone if the new list
  // never arrives.
  // E79: the widget's reload is a READ (?mark=0) — a plain GET records every
  // row in the payload as shown, collapsed Today and Later included — and the
  // visible-only beacon below then reports what this widget drew (C06).  The
  // pop-out's /api/expand marks as it always did: it draws every row it marks.
  function reload(host, expanded, o) {
    return fetch(expanded ? "/api/expand?id=needsyou" : "/api/needsyou?mark=0")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!(o && o.flip)) {
          draw(host, d, host.__nyMeta, expanded);
          if (!expanded) widgetSeen(host, d);
          return;
        }
        return Promise.resolve(o.after).then(function () {
          anim().flip(host, function () { draw(host, d, host.__nyMeta, expanded, o); },
                      { duration: 0.32 });
          if (!expanded) widgetSeen(host, d);
        });
      })
      .catch(function () {
        if (o && typeof o.failed === "function") {
          Promise.resolve(o.after).then(o.failed, o.failed);
        }
      });
  }

  // the row element showing `id` now: the hub's 60 s refresh may have rebuilt
  // the list while the POST was out, and the clicked row is then detached
  function rowFor(host, id, clicked) {
    try {
      if (clicked && host.contains && host.contains(clicked)) return clicked;
      var r = host.querySelector && host.querySelector('.ny-row[data-id="' +
        String(id).replace(/["\\]/g, "\\$&") + '"]');
      return r || clicked;
    } catch (e) { return clicked; }
  }

  // 8.8: the server took a Done / Snooze / re-file.  The row leaves (`preset`:
  // "dismiss" for Done, "defer" otherwise) and is hidden in place, so its gap
  // stays until the repaint closes it.  The new list is fetched meanwhile.
  // If it never arrives, the row comes back as it was (its button disabled),
  // which is what this widget did before it moved at all.
  function leave(host, expanded, rowEl, preset) {
    var gone;
    var after = new Promise(function (res) { gone = res; });
    function hide() {
      try { if (rowEl && rowEl.style) rowEl.style.visibility = "hidden"; } catch (e) {}
      gone();
    }
    var h = anim().exit(rowEl, preset, hide);
    // a cancelled exit never calls done(); the repaint must not wait forever
    if (h && h.finished && typeof h.finished.then === "function") h.finished.then(gone, gone);
    reload(host, expanded, {
      flip: true, act: true, after: after,
      failed: function () {
        try { if (rowEl && rowEl.style) rowEl.style.visibility = ""; } catch (e) {}
      }
    });
  }

  // ---- one row ------------------------------------------------------------
  // For people the SENDER is the identity; for everything else the thing
  // itself is ("Design review", "Renew passport", the headline) — showing
  // "Calendar" five times tells the reader nothing.
  function who(it) {
    return (it.source === "message" || it.source === "email")
      ? (it.sender || it.title) : (it.title || it.sender);
  }

  function row(it, expanded) {
    // an approval has no deadline of its own — it has been waiting, which is
    // a different and more honest thing to print than "overdue".
    var due = (it.source === "approval") ? "waiting" : DUE(it.deadline_ts);
    var when = (it.source === "approval")
      ? ("waiting " + (REL(it.received_at) || "")).trim()
      : (due || REL(it.received_at) || "");
    // data-flip-id: the Flip after an act matches the row that stays across
    // the repaint by it (the item id: always "source:key", so it can never be
    // one of the "ny-…" ids the group heads and the footer carry)
    var h = '<div class="ny-row" data-id="' + E(it.id) + '" data-flip-id="' + E(it.id) + '">' +
      '<span class="ny-g">' + glyph(it.source) + "</span>" +
      '<div class="ny-main">' +
      '<div class="ny-top"><span class="ny-who">' + E(who(it)) + "</span>" +
      '<span class="ny-when' + (due ? " due" : "") + '">' + E(when) + "</span></div>";
    if (it.summary && it.summary !== who(it)) {
      h += '<div class="ny-sum">' + E(it.summary) + "</div>";
    }
    h += '<div class="ny-why"' + (expanded && !UI.why ? ' hidden' : '') + ">" +
      E(it.reason || "") +
      ((expanded && it.rule_reason && it.rule_reason !== it.reason)
        ? ' <b>&middot; rule said:</b> ' + E(it.rule_reason) : "") +
      "</div>";
    // C103: every row repeats Done / Snooze / Open, so a screen reader's
    // button list read "Done, Done, Done".  The accessible name keeps the
    // visible word first (label-in-name) and adds whose row it is.
    var subj = who(it) || "";
    function AL(verb) {
      return ' aria-label="' + E(subj ? verb + ": " + subj : verb) + '"';
    }
    var to = (it.bucket === "later" ? "today" : "later");
    var isNever = it.bucket === "never";
    h += '<div class="ny-acts">' +
      '<button class="ny-b" data-a="done"' + AL("Done") + '>Done</button>' +
      '<button class="ny-b" data-a="snz"' + AL("Snooze") + '>Snooze</button>' +
      '<button class="ny-b" data-a="open"' + AL("Open") + '>Open</button>';
    if (it.source === "message" || it.source === "email") {
      h += '<button class="ny-b pri" data-a="draft"' + AL("Draft reply") +
        '>Draft reply</button>';
    }
    if (expanded && isNever) {
      // E04: a row filed under Never can be taken back out of it
      h += '<button class="ny-b sm" data-a="rc" data-to="today"' +
        AL("Move to today") + '>Move to today</button>' +
        '<button class="ny-b sm" data-a="rc" data-to="later"' +
        AL("Move to later") + '>Move to later</button>';
    } else if (expanded) {
      h += '<button class="ny-b sm" data-a="rc" data-to="' + to + '"' +
        AL("Move to " + to) + '>Move to ' + to + "</button>" +
        '<button class="ny-b sm" data-a="rc" data-to="never"' + AL("Never") +
        '>Never</button>';
    }
    h += "</div>";
    if (UI.snooze === it.id) {
      h += '<div class="ny-snz">' +
        '<button class="ny-b sm" data-a="snooze" data-u="1h"' +
        AL("Snooze 1 hour") + '>1 hour</button>' +
        '<button class="ny-b sm" data-a="snooze" data-u="evening"' +
        AL("Snooze until this evening") + '>This evening</button>' +
        '<button class="ny-b sm" data-a="snooze" data-u="tomorrow"' +
        AL("Snooze until tomorrow") + '>Tomorrow</button>' +
        "</div>";
    }
    if (UI.draft[it.id]) {
      h += (window.hermesMd && typeof window.hermesMd.render === "function")
        ? '<div class="ny-draft md">' + window.hermesMd.render(UI.draft[it.id]) + "</div>"
        : '<div class="ny-draft raw">' + E(UI.draft[it.id]) + "</div>";
    }
    if (UI.msg[it.id]) {
      h += '<div class="ny-msg' + (UI.msg[it.id].bad ? " bad" : "") + '">' +
        E(UI.msg[it.id].text) + "</div>";
    }
    // R2-22: a draft stopped on an approval.  Its session is hidden, so this
    // is the only place it can be answered — and a reply draft never needs
    // to run anything, so the one answer offered is Deny.
    if (UI.appr[it.id]) {
      h += '<div class="ny-msg bad">The draft asked to run: ' +
        E(UI.appr[it.id].cmd) + ". Writing a reply never needs that — it " +
        "has not run.</div>" +
        '<div class="ny-acts"><button class="ny-b" data-a="draft-deny"' +
        AL("Deny") + ">Deny</button></div>";
    }
    return h + "</div></div>";
  }

  // ---- the trust line -----------------------------------------------------
  function trust(m) {
    m = m || {};
    var p = PCT(m.now_precision);
    var bits = [];
    if (m.now_snoozed) bits.push(m.now_snoozed + " snoozed");
    if (m.reclassified) bits.push(m.reclassified + " re-filed");
    // nothing measured yet: the line says nothing worth its space, so it is
    // left out until there is something to report
    if (!p && !bits.length) return "";
    bits.unshift(p ? ("now precision " + p) : "now precision — not enough data yet");
    return bits.join(" · ");
  }

  // ---- the whole body -----------------------------------------------------
  // The widget's meta slot: "3 now", "2 today", "clear".  A Now count goes
  // through HA().count, which only MOVES on the repaint that answers the
  // user's own act (from the number this slot showed last).  Every other
  // repaint asks it to count from the number to itself, which writes the text
  // and moves nothing — the 60 s hub refresh never animates it.  A count still
  // running when the slot stops showing "N now" is cancelled first, so it
  // cannot write over the new words.
  function meta(slot, d, now, today, act) {
    var n = (!d.building && now.length) ? now.length : 0;
    var was = slot.__nyNow;
    if (n) {
      slot.__nyCount = anim().count(slot, n, { fmt: NOWFMT, from: (act && was) ? was : n });
    } else {
      var c = slot.__nyCount;
      if (c && !c.done && typeof c.cancel === "function") c.cancel();
      slot.__nyCount = null;
      slot.textContent = d.building ? "reading your day…"
        : (today.length ? today.length + " today" : "clear");
    }
    slot.__nyNow = n || null;
  }

  // E04: the pop-out lists what was filed under Never (newest first, capped
  // server-side), collapsed until the owner opens it, so a person's message
  // the rules misfiled can be found and moved back.  The widget keeps only
  // the "Never n" count.
  function neverGroup(d, expanded) {
    var never = (d && d.never) || [];
    if (!expanded || !never.length) return "";
    var n = d.never_count == null ? never.length : d.never_count;
    var h = '<button class="ny-more" data-a="toggle-never" data-flip-id="ny-more-never" aria-expanded="' +
      (UI.never ? "true" : "false") + '">' + CHEV + "Never (" + n + ")</button>";
    if (UI.never) {
      never.forEach(function (it) { h += row(it, expanded); });
      if (n > never.length) {
        h += '<div class="ny-why" data-flip-id="ny-never-more">Showing the newest ' +
          never.length + " of " + n + ".</div>";
      }
    }
    return h;
  }

  // o: {act} when this repaint answers the user's own Done / Snooze /
  // re-file (reload() passes it); every other caller leaves it out
  function draw(host, d, mslot, expanded, o) {
    d = d || {};
    // C99.2: the control a keyboard user is on survives the rebuild below
    var onCtl = focusedCtl(host);
    if (onCtl) keepFocus(host, onCtl);
    host.__nyData = d;
    host.__nyMeta = mslot;
    var now = d.now || [], today = d.today || [], later = d.later || [];
    var nNever = d.never_count || 0;

    if (mslot) meta(mslot, d, now, today, !!(o && o.act));

    var h = '<div class="ny-wrap">';

    if (d.building) {
      h += '<div class="ny-empty"><div class="h">Reading your day…</div>' +
        '<div class="s">Messages, calendar, reminders, alerts and anything ' +
        'waiting on your approval.</div></div>';
      host.innerHTML = h + "</div>";
      return;
    }

    // the store exists but could not be read: every Done/Snooze is refused
    // until it can be, so say so once, above everything (C27 follow-up)
    if (d.store_error) h += '<div class="ny-msg bad" data-flip-id="ny-err">' + E(d.store_error) + "</div>";

    if (!now.length && !today.length && !later.length) {
      var nb = d.next_brief;
      var label = { brief: "morning brief", midday: "midday pulse",
                    evening: "evening wrap" };
      // C18: an empty inbox over a source that is not being read is not
      // "nothing needs you" — say which source is blind, right in the headline
      var blind = blindNames(d);
      var age = (d.generated_at && Date.now() / 1000 - d.generated_at > 300)
        ? " Last checked " + REL(d.generated_at) + "." : "";
      h += '<div class="ny-empty"><div class="h">' +
        (blind ? ("Nothing needs you — but " + E(blind) +
                  (blind.indexOf(" and ") > 0 ? " are" : " is") +
                  " not being read")
               : "Nothing needs you right now") + "</div>" +
        '<div class="s">' +
        (nb ? ("Next " + E(label[nb.which] || "brief") + " at " + E(T12(nb.ts)) + ".")
            : "Nothing scheduled — briefings are off.") + E(age) +
        "</div></div>";
      h += neverGroup(d, expanded);
      h += sources(d, expanded);
      // the footer only when it has something to say: a Never count to
      // reach, or a trust figure that has been measured
      var tr0 = trust(d.metrics);
      if (nNever || tr0) {
        h += '<div class="ny-foot" data-flip-id="ny-foot">' +
          (nNever ? "<span>Never " + nNever + "</span>" : "") +
          (tr0 ? '<span class="ny-trust">' + E(tr0) + "</span>" : "") + "</div>";
      }
      host.innerHTML = h + "</div>";
      wire(host, expanded);
      restoreFocus(host);
      neverSeen(host, d, expanded);
      return;
    }

    // NOW — the only group that is open by default, capped at five
    if (now.length) {
      h += '<div class="ny-grp" data-flip-id="ny-grp-now"><span class="ny-dot now"></span>Now</div>';
      now.slice(0, 5).forEach(function (it) { h += row(it, expanded); });
    }

    // TODAY — collapsed in the widget, always open in the pop-out
    if (today.length) {
      if (expanded) {
        h += '<div class="ny-grp" data-flip-id="ny-grp-today"><span class="ny-dot today"></span>Today</div>';
        today.forEach(function (it) { h += row(it, expanded); });
      } else {
        h += '<button class="ny-more" data-a="toggle-today" data-flip-id="ny-more-today" aria-expanded="' +
          (UI.today ? "true" : "false") + '">' + CHEV +
          "Today (" + today.length + ")</button>";
        if (UI.today) today.forEach(function (it) { h += row(it, expanded); });
      }
    }

    // LATER — a line in the widget, a real group in the pop-out
    if (expanded && later.length) {
      h += '<div class="ny-grp" data-flip-id="ny-grp-later"><span class="ny-dot later"></span>Later</div>';
      later.forEach(function (it) { h += row(it, expanded); });
    }

    h += neverGroup(d, expanded);

    // C18.2: a non-empty inbox can be blind to a source too — say so on one
    // line, not only in the empty state's headline and the pop-out's chips
    h += blindLine(d);

    h += '<div class="ny-foot" data-flip-id="ny-foot"><span>Later ' + (d.later_count == null
      ? later.length : d.later_count) + " · Never " + nNever + "</span>";
    if (expanded) {
      h += '<button class="ny-b sm" data-a="toggle-why" style="min-height:40px">' +
        (UI.why ? "Hide why" : "Why?") + "</button>";
    }
    var tr = trust(d.metrics);
    if (tr) h += '<span class="ny-trust">' + E(tr) + "</span>";
    h += "</div>";
    if (expanded) h += sources(d, expanded);

    host.innerHTML = h + "</div>";
    wire(host, expanded);
    restoreFocus(host);
    neverSeen(host, d, expanded);
  }

  var SRC_ORDER = ["message", "calendar", "reminder", "approval", "watchtower",
                   "email", "github"];
  var SRC_NAME = { message: "Messages", calendar: "Calendar",
                   reminder: "Reminders", approval: "Approvals",
                   watchtower: "Watchtower", email: "Mail", github: "GitHub" };

  // a source the owner switched off (payload.source_off) is a choice, not a
  // blind spot: its chip still says why, but it never changes the headline
  function offByChoice(d, k) {
    var off = (d && d.source_off) || [];
    return !!(off.indexOf && off.indexOf(k) >= 0);
  }

  // "Messages" / "Messages and Reminders": the absent sources that carry a
  // reason (payload.source_notes) — absent-by-design ones (Mail) carry none
  function blindNames(d) {
    var notes = (d && d.source_notes) || {}, src = (d && d.sources) || {};
    var names = [];
    SRC_ORDER.forEach(function (k) {
      if (notes[k] && src[k] !== "present" && !offByChoice(d, k)) {
        names.push(SRC_NAME[k]);
      }
    });
    if (names.length < 2) return names[0] || "";
    return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
  }

  // "Messages — sync is 3 h old", not "Messages — Messages sync is …"
  function shortNote(k, note) {
    note = String(note || "");
    return (note.indexOf(SRC_NAME[k] + " ") === 0)
      ? note.slice(SRC_NAME[k].length + 1) : note;
  }

  // "Not being read: Messages (Full Disk Access needed) · Reminders (access
  // needed)" — the same sources blindNames() would put in the headline
  function blindLine(d) {
    var notes = (d && d.source_notes) || {}, src = (d && d.sources) || {};
    var bits = [];
    SRC_ORDER.forEach(function (k) {
      if (notes[k] && src[k] !== "present" && !offByChoice(d, k)) {
        bits.push(SRC_NAME[k] + " (" + shortNote(k, notes[k]) + ")");
      }
    });
    return bits.length ? '<div class="ny-blind" role="note" data-flip-id="ny-blind">Not being read: ' +
      E(bits.join(" · ")) + "</div>" : "";
  }

  // The sources row.  Live sources and BLIND ones (a reason the owner has to
  // act on: Full Disk Access, a stale sync — C18) stay in plain sight; the
  // ones that are off by choice or not connected fold behind ONE readable
  // "N sources off" toggle in the widget, their chips (each with its reason)
  // still in the DOM one click away.  The pop-out lists every chip open.
  function sources(d, expanded) {
    var s = d.sources || {};
    var notes = d.source_notes || {};
    var out = "", off = "", nOff = 0;
    SRC_ORDER.forEach(function (k) {
      if (!(k in s)) return;
      var on = s[k] === "present";
      var note = shortNote(k, notes[k]);
      var blind = !on && notes[k] && !offByChoice(d, k);
      var chip = '<span class="ny-chip' + (on ? " live" : " off") +
        (blind ? " note" : "") + '">' +
        E(SRC_NAME[k]) + (on ? "" : " — " + E(note || "not connected")) +
        "</span>";
      if (on || blind || expanded) out += chip;
      else { off += chip; nOff++; }
    });
    if (nOff) {
      out += '<button class="ny-more ny-offt" data-a="toggle-src" aria-expanded="' +
        (UI.src ? "true" : "false") + '" aria-controls="ny-offl">' + CHEV +
        nOff + (nOff === 1 ? " source" : " sources") + " off</button>";
    }
    if (!out) return "";
    return '<div class="ny-src" data-flip-id="ny-src">' + out + "</div>" +
      (nOff ? '<div class="ny-offl" id="ny-offl" data-flip-id="ny-offl"' +
        (UI.src ? "" : " hidden") + ">" + off + "</div>" : "");
  }

  // ---- interaction --------------------------------------------------------
  function wire(host, expanded) {
    var btns = host.querySelectorAll("button[data-a]");
    for (var i = 0; i < btns.length; i++) {
      (function (b) {
        b.onclick = function (ev) {
          ev.preventDefault();
          ev.stopPropagation();              // never let the card pop out
          // C99.2: remember it only when it really had focus (keyboard, or
          // a browser that focuses on click) — a mouse click in WebKit never
          // grows a focus ring because of this
          if ((typeof document !== "undefined" && document.activeElement === b) ||
              (ev && ev.detail === 0)) keepFocus(host, b);
          var a = b.getAttribute("data-a");
          var rowEl = b.closest ? b.closest(".ny-row") : null;
          var id = rowEl ? rowEl.getAttribute("data-id") : "";

          if (a === "toggle-today") { UI.today = !UI.today; return redraw(host, expanded); }
          if (a === "toggle-why") { UI.why = !UI.why; return redraw(host, expanded); }
          if (a === "toggle-never") { UI.never = !UI.never; return redraw(host, expanded); }
          if (a === "toggle-src") { UI.src = !UI.src; return redraw(host, expanded); }
          if (a === "snz") {
            UI.snooze = (UI.snooze === id) ? "" : id;
            return redraw(host, expanded);
          }
          if (!id) return;

          if (a === "draft-deny") {
            var ap = UI.appr[id];
            if (!ap) return;
            b.disabled = true;
            fetch("/api/chat/approve", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ job: ap.job, choice: "deny",
                                     approval_id: ap.aid })
            }).then(function (r) { return r.json(); }).then(function (r) {
              delete UI.appr[id];
              // `denied` keeps the next poll (which may still report this
              // same request for a moment) from putting the card back
              UI.msg[id] = (r && r.ok) ? { text: "Denied. Finishing the draft…",
                                           denied: ap.aid }
                : (r && r.stale) ? { text: "That request is no longer waiting.",
                                     denied: ap.aid }
                : { bad: true, text: (r && r.error) || "Could not deny it." };
              if (!(r && (r.ok || r.stale))) UI.appr[id] = ap;   // still answerable
              redraw(host, expanded);
            }).catch(function () {
              b.disabled = false;
              UI.msg[id] = { bad: true, text: "Could not deny it. Try again." };
              redraw(host, expanded);
            });
            return;
          }

          if (a === "open") {
            // The server records "you opened this" before it answers, so an
            // ok:false here is a real store failure, not a cosmetic one — say
            // so with the same .ny-msg.bad the draft path uses instead of
            // silently jumping to a surface as if the act had been logged.
            post({ id: id, action: "open" }).then(function (r) {
              if (!(r && r.ok)) {
                UI.msg[id] = {
                  bad: true,
                  text: (r && r.error) || "Could not open this. Try again."
                };
                return redraw(host, expanded);
              }
              delete UI.msg[id];
              openTarget(host, id, r.open || "", r.ref || "");
            }).catch(function () {
              UI.msg[id] = { bad: true, text: "Could not open this. Try again." };
              redraw(host, expanded);
            });
            return;
          }
          if (a === "draft") {
            b.disabled = true;
            UI.msg[id] = { text: "Drafting a reply…" };
            redraw(host, expanded);
            post({ id: id, action: "draft" }).then(function (r) {
              if (r && r.ok && r.job) { pollDraft(host, expanded, id, r.job); return; }
              var asleep = r && r.error === "model asleep";
              UI.msg[id] = {
                bad: true,
                text: asleep ? "The model is asleep. Wake it to draft a reply."
                  : ((r && r.error) || "Could not start a draft.")
              };
              redraw(host, expanded);
              if (asleep) offerWake(host, expanded, id);
            }).catch(function () {
              UI.msg[id] = { bad: true, text: "Could not start a draft." };
              redraw(host, expanded);
            });
            return;
          }

          var body = { id: id, action: a === "rc" ? "reclassify" : a };
          // B04: the message this row SHOWS, so a Done/Snooze/re-file never
          // also files a follow-up that arrived after this render
          var shownAt = receivedAt(host.__nyData, id);
          if (shownAt) body.seen = shownAt;
          if (a === "snooze") body.until = b.getAttribute("data-u") || "1h";
          if (a === "rc") body.to = b.getAttribute("data-to") || "later";
          b.disabled = true;
          UI.snooze = "";
          // TAG, NEVER MOVE means this module's store is the ONLY record the
          // decision happened: if the write failed, reloading would re-list the
          // row with no explanation and the user would read it as "my click did
          // nothing".  Check ok, keep the row, say why, leave the button live.
          post(body).then(function (r) {
            if (!(r && r.ok)) {
              b.disabled = false;
              UI.msg[id] = {
                bad: true,
                text: (r && r.error) || "That did not save. Try again."
              };
              return redraw(host, expanded);
            }
            delete UI.msg[id];
            delete UI.draft[id];
            leave(host, expanded, rowFor(host, id, rowEl),
                  a === "done" ? "dismiss" : "defer");
          }).catch(function () {
            b.disabled = false;
            UI.msg[id] = { bad: true, text: "That did not save. Try again." };
            redraw(host, expanded);
          });
        };
      })(btns[i]);
    }
  }

  function redraw(host, expanded) {
    draw(host, host.__nyData, host.__nyMeta, expanded);
  }

  // ---- keyboard focus across a re-render (C99.2) --------------------------
  // draw() replaces the whole list, so the button a keyboard user just
  // pressed is gone and focus fell to <body> after every Done, Snooze, Open
  // or toggle.  Remember WHICH control it was (row id + action + data-to /
  // data-u) and, after the redraw, put focus back on the same control — or,
  // when that row has gone (Done, Snooze, a re-file), on the first button of
  // the row that took its place, else of the row above it.  Never when the
  // user has moved focus somewhere else in the meantime.
  function ctlKey(b) {
    var row = (b && b.closest) ? b.closest(".ny-row") : null;
    return { id: row ? (row.getAttribute("data-id") || "") : "",
             a: b.getAttribute("data-a") || "",
             to: b.getAttribute("data-to") || "",
             u: b.getAttribute("data-u") || "" };
  }
  function rowOrder(btns) {
    var ids = [];
    for (var i = 0; i < btns.length; i++) {
      var id = ctlKey(btns[i]).id;
      if (id && ids.indexOf(id) < 0) ids.push(id);
    }
    return ids;
  }
  function keepFocus(host, b) {
    try {
      var k = ctlKey(b);
      k.idx = k.id ? rowOrder(host.querySelectorAll("button[data-a]")).indexOf(k.id) : -1;
      host.__nyFocus = k;
    } catch (e) {}
  }
  function focusedCtl(host) {
    try {
      if (typeof document === "undefined") return null;
      var ae = document.activeElement;
      if (!ae || !ae.getAttribute || !ae.getAttribute("data-a")) return null;
      return (host.contains && host.contains(ae)) ? ae : null;
    } catch (e) { return null; }
  }
  function restoreFocus(host) {
    var f = host.__nyFocus;
    host.__nyFocus = null;
    if (!f || typeof document === "undefined") return;
    try {
      var ae = document.activeElement;
      if (ae && ae !== document.body && !(host.contains && host.contains(ae))) return;
      var btns = host.querySelectorAll("button[data-a]");
      var first = {}, pick = null, i, k;
      for (i = 0; i < btns.length; i++) {
        k = ctlKey(btns[i]);
        if (!Object.prototype.hasOwnProperty.call(first, k.id)) first[k.id] = btns[i];
        if (!pick && k.id === f.id && k.a === f.a && k.to === f.to && k.u === f.u) {
          pick = btns[i];
        }
      }
      if (!pick && f.id && first[f.id]) pick = first[f.id];   // same row, control gone
      if (!pick && f.id && f.idx >= 0) {
        var ids = rowOrder(btns);
        var at = Math.min(f.idx, ids.length - 1);              // the next row, else the last
        if (at >= 0) pick = first[ids[at]];
      }
      if (pick && typeof pick.focus === "function") pick.focus();
    } catch (e) {}
  }

  function receivedAt(d, id) {
    var hit = 0;
    ["now", "today", "later", "never"].forEach(function (k) {
      ((d && d[k]) || []).forEach(function (it) {
        if (it && it.id === id && typeof it.received_at === "number") hit = it.received_at;
      });
    });
    return hit;
  }

  // index.html's httpUrl() by name when the shell has it; the same rule here
  // for a page (or a headless harness) that does not.
  function httpOnly(u) {
    if (typeof httpUrl === "function") {
      try { return httpUrl(u) || ""; } catch (e) { return ""; }
    }
    try {
      var x = new URL(String(u == null ? "" : u).trim());
      return (x.protocol === "http:" || x.protocol === "https:") ? x.href : "";
    } catch (e) { return ""; }
  }

  // ---- GitHub rows (round 3, shared contract 4) ---------------------------
  // A pending change opens aux_github.js's review sheet: through its API when
  // that module is loaded, else through the hash it listens for (on load and
  // on hashchange).  The connector's attention lines (holds, drift, an
  // expiring key) open Settings › Connections through the settings shell's
  // own opener.  The id is re-checked here: only a well-formed change id ever
  // reaches the review sheet or the address bar.
  var GH_REVIEW = /^#github-review=(ghc-[0-9a-f]{12})$/;

  function openGitHubReview(cid) {
    if (typeof closePop === "function") closePop();
    var gh = (typeof window !== "undefined") ? window.hermesGitHub : null;
    if (gh && typeof gh.openReview === "function") { gh.openReview(cid); return; }
    var h = "#github-review=" + cid;
    if (typeof location === "undefined" || !location) return;
    if (location.hash === h) {
      // the same hash again fires no hashchange: say it ourselves
      try { window.dispatchEvent(new HashChangeEvent("hashchange")); } catch (e) {}
      return;
    }
    location.hash = h;
  }

  function openConnections() {
    if (typeof closePop === "function") closePop();
    var w = (typeof window !== "undefined") ? window : {};
    // the GitHub card's own opener also scrolls to the card (aux_github.js)
    if (w.hermesGitHub && typeof w.hermesGitHub.openCard === "function") {
      try { w.hermesGitHub.openCard(); return; } catch (e) {}
    }
    if (typeof setView === "function") setView("mind");
    if (typeof w.settingsShow === "function") {
      if (typeof w.settingsEnsureShell === "function") w.settingsEnsureShell();
      w.settingsShow("connections");
      return;
    }
    if (typeof location !== "undefined" && location) location.hash = "#settings/connections";
  }

  // "Open" routes to the surface that actually holds the thing.
  function openTarget(host, id, target, ref) {
    try {
      var gm = GH_REVIEW.exec(String(target || ""));
      if (gm) { openGitHubReview(gm[1]); return; }
      if (String(target || "").indexOf("#github-review=") === 0) return;   // malformed: nothing
      if (target === "connections") { openConnections(); return; }
      if (target === "main") {
        if (typeof closePop === "function") closePop();
        // the chat (and any approval card in it) lives on the Agent page
        if (typeof openChat === "function") openChat();
        // an approval row's ref is its conversation: open THAT one, and the
        // main window attaches to its running turn so the card shows there
        if (ref && typeof window.hermesOpenConversation === "function") {
          window.hermesOpenConversation(ref);
          return;
        }
        var msgs = document.getElementById("msgs");
        if (msgs) msgs.scrollTop = msgs.scrollHeight;
        return;
      }
      // B18: a stored ref is opened only as an absolute http(s) URL, through
      // the same normaliser as every other link in the shell (index.html
      // httpUrl) — never the raw string.  The app hands window.open's URL to
      // NSWorkspace, so a prefix test that admits "https:javascript:…" or an
      // untrimmed string is not the rule; anything else opens the pop-out.
      var url = (target === "framing" && ref) ? httpOnly(ref) : "";
      if (url) {
        window.open(url, "_blank", "noopener");
        return;
      }
      if (typeof openPop === "function" && target) { openPop(target); return; }
    } catch (e) {}
  }

  // The wake button is the shell's own affordance — we only surface it.
  function offerWake(host, expanded, id) {
    var rowEl = host.querySelector('.ny-row[data-id="' +
      String(id).replace(/["\\]/g, "\\$&") + '"]');
    if (!rowEl) return;
    var acts = rowEl.querySelector(".ny-acts");
    if (!acts || acts.querySelector('[data-a="wake"]')) return;
    var b = document.createElement("button");
    b.className = "ny-b pri";
    b.setAttribute("data-a", "wake");
    b.textContent = "Wake the model";
    b.onclick = function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      b.disabled = true;
      UI.msg[id] = { text: "Waking the model — about 30 seconds…" };
      redraw(host, expanded);
      fetch("/api/agent/wake", { method: "POST" })
        .then(function () {
          UI.msg[id] = { text: "Model is waking. Hit Draft reply again." };
          redraw(host, expanded);
        })
        .catch(function () {
          UI.msg[id] = { bad: true, text: "Could not wake the model." };
          redraw(host, expanded);
        });
    };
    acts.appendChild(b);
  }

  // R2-22: a job that ended with ok:false carries its ERROR text in `reply`
  // ("The agent hit an error…", "(…interrupted…)") — never show that as the
  // drafted reply someone might copy and send.
  function draftError(reply) {
    var t = String(reply || "").replace(/\s+/g, " ").trim();
    if (t.length > 200) t = t.slice(0, 200).trim() + "…";
    return "No draft — " + (t || "the agent hit an error before it could write one.");
  }

  function pollDraft(host, expanded, id, job) {
    var tries = 0, waits = 0;
    var tick = function () {
      // an approval wait is bounded by its own TTL (about five minutes), so it
      // does not spend the ~two minutes a draft gets to be written
      if (tries > 90 || waits > 300) {
        delete UI.appr[id];
        UI.msg[id] = { bad: true, text: "The draft is taking too long." };
        return redraw(host, expanded);
      }
      fetch("/api/chat/poll?job=" + encodeURIComponent(job))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d || !d.ok) {
            delete UI.appr[id];
            UI.msg[id] = { bad: true, text: "The draft job went away." };
            return redraw(host, expanded);
          }
          if (d.done) {
            delete UI.appr[id];
            if (d.err) {
              delete UI.draft[id];
              UI.msg[id] = { bad: true, text: draftError(d.reply) };
              return redraw(host, expanded);
            }
            delete UI.msg[id];
            UI.draft[id] = d.reply || "(the agent returned nothing)";
            return redraw(host, expanded);
          }
          var ap = d.approval;
          if (d.state === "approval" && ap && ap.approval_id) {
            waits++;
            // a Deny already on its way: keep showing that, not a new card
            if (!(UI.appr[id] && UI.appr[id].aid === String(ap.approval_id)) &&
                !(UI.msg[id] && UI.msg[id].denied === String(ap.approval_id))) {
              UI.appr[id] = { job: job, aid: String(ap.approval_id),
                              cmd: String(ap.command || ap.description ||
                                          ap.tool || "an action") };
              delete UI.msg[id];
              redraw(host, expanded);
            }
            setTimeout(tick, 1200);
            return;
          }
          tries++;
          delete UI.appr[id];
          UI.msg[id] = { text: d.status || "Drafting a reply…" };
          redraw(host, expanded);
          setTimeout(tick, 1200);
        })
        .catch(function () {
          UI.msg[id] = { bad: true, text: "Lost the draft job." };
          redraw(host, expanded);
        });
    };
    setTimeout(tick, 900);
  }

  // ---- sightings: only what a person could actually see (C06) -------------
  // /api/hub and the server's 45 s prewarm no longer record anything, so the
  // hub widget says what it put on screen — and only while the page is
  // visible and the widget is laid out (not in a hidden view or chat-only
  // mode).  The server keeps one row per item per day and accepts only ids in
  // its own cached payload; the key below just avoids re-posting an
  // unchanged list every 60 s.
  var SEEN = { key: "", host: null, data: null };
  function seen(host, d) {
    try {
      if (!host || !d || d.building || typeof fetch !== "function") return;
      if (typeof document !== "undefined" && document.visibilityState &&
          document.visibilityState !== "visible") return;
      if (typeof host.getClientRects === "function" &&
          !host.getClientRects().length) return;
      var ids = [], sig = [];
      ["now", "today", "later"].forEach(function (b) {
        (d[b] || []).forEach(function (it) {
          if (it && it.id) { ids.push(it.id); sig.push(b + ":" + it.id); }
        });
      });
      if (!ids.length) return;
      var key = new Date().toDateString() + "|" + sig.join(",");
      if (key === SEEN.key) return;
      SEEN.key = key;
      fetch("/api/needsyou/seen", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: ids })
      }).then(function (r) { if (!r || !r.ok) SEEN.key = ""; })
        .catch(function () { SEEN.key = ""; });
    } catch (e) {}
  }
  // the widget's own beacon state follows its latest data (RENDER and the
  // act path's reload both draw into it)
  function widgetSeen(host, d) {
    SEEN.host = host;
    SEEN.data = d;
    seen(host, d);
  }

  // E04: the pop-out's /api/expand records Now/Today/Later (it draws them
  // all) but never the Never rows — those count as shown only once the owner
  // has opened that group and they are on screen.
  var NSEEN = { key: "" };
  function neverSeen(host, d, expanded) {
    try {
      if (!expanded || !UI.never || !d || d.building || typeof fetch !== "function") return;
      if (typeof document !== "undefined" && document.visibilityState &&
          document.visibilityState !== "visible") return;
      if (typeof host.getClientRects === "function" &&
          !host.getClientRects().length) return;
      var ids = [];
      (d.never || []).forEach(function (it) { if (it && it.id) ids.push(it.id); });
      if (!ids.length) return;
      var key = new Date().toDateString() + "|" + ids.join(",");
      if (key === NSEEN.key) return;
      NSEEN.key = key;
      fetch("/api/needsyou/seen", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: ids })
      }).then(function (r) { if (!r || !r.ok) NSEEN.key = ""; })
        .catch(function () { NSEEN.key = ""; });
    } catch (e) {}
  }

  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") seen(SEEN.host, SEEN.data);
    });
  }

  // ---- registry -----------------------------------------------------------
  if (typeof RENDER !== "undefined") {
    RENDER.needsyou = function (body, data, mslot) {
      injectCSS();
      draw(body, data, mslot, false);
      SEEN.host = body;
      SEEN.data = data;
      // renderHub fills a card before appending it to the grid, so check
      // visibility once the current task has finished laying it out
      if (typeof setTimeout === "function") setTimeout(function () { seen(body, data); }, 0);
      else seen(body, data);
    };
  }
  if (typeof EXPAND_RENDER !== "undefined") {
    EXPAND_RENDER.needsyou = function (el, d) {
      injectCSS();
      draw(el, d, null, true);
    };
  }

  // exported for the headless render harness
  if (typeof window !== "undefined") {
    window.__needsyou = { draw: draw, row: row, trust: trust, UI: UI };
  }
})();
