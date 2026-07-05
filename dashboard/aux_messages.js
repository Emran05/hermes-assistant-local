// aux_messages.js — Message Center renderers (P2.4, app-reads-chat.db).
//
// Auto-served at /aux_messages.js and loaded LAST via its index.html script
// tag, so these property assignments REBIND the existing renderers:
//   RENDER.messages        — replaces the old chat.db-direct card (index.html)
//   EXPAND_RENDER.messages — replaces the old "grant FDA to python" pop-out
//                            (expand.js), whose steps are now wrong: the FDA
//                            grant target is the Hermes Assistant APP.
// Data now comes from the aux_messages.py store (fed by the native app), with
// three states: never_synced (waiting for the app) / grant (FDA steps card) /
// available (live rows, stale badge, service chips).
// CLAUDE.md laws: zero emoji (bespoke SVG only), 12-hour/relative time via
// relTime, esc() on every dynamic string (numbers coerced), Motion One
// animate() for row entrance — everything typeof-guarded so a headless
// harness never throws.

(function () {
  "use strict";

  // ---- guarded helpers ------------------------------------------------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function RT(ts) {
    if (!ts) return "";
    if (typeof relTime === "function") { try { return relTime(ts); } catch (e) {} }
    try { return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); } catch (e) { return ""; }
  }
  function agoM(s) { return Math.max(1, Math.round((+s || 0) / 60)); }

  var FDA_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles";
  var PAL = ["var(--iris)", "var(--quick)", "var(--ok)", "var(--warn)", "var(--wac)", "var(--bad)"];
  function hue(name) { var n = 0, i, s = String(name || ""); for (i = 0; i < s.length; i++) n = (n * 31 + s.charCodeAt(i)) >>> 0; return PAL[n % PAL.length]; }
  function initials(name) {
    var s = String(name || "").replace(/^[^A-Za-z0-9]+/, "").trim();
    if (!s) return "#";
    var p = s.split(/\s+/);
    if (p.length >= 2) return (p[0][0] + p[1][0]).toUpperCase();
    return s.slice(0, 2).toUpperCase();
  }

  // ---- bespoke glyphs (SVG only, no emoji) -----------------------------------
  var SPIN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" style="flex:0 0 auto"><path d="M12 3a9 9 0 1 0 9 9"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.9s" repeatCount="indefinite"/></path></svg>';
  var WARN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:0 0 auto"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
  var LOCK = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>';
  var CLIP = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
  var GROUP = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M17 20v-2a4 4 0 0 0-3-3.87"/><path d="M9 20v-2a4 4 0 0 1 3-3.87"/><circle cx="9" cy="7" r="3"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>';

  function svcChip(s) {
    s = String(s || "");
    if (!s) return "";
    var im = /imessage/i.test(s);
    var col = im ? "var(--iris)" : "var(--ok)";
    var lab = im ? "iMessage" : (/sms/i.test(s) ? "SMS" : s.slice(0, 10));
    return '<span style="flex:0 0 auto;font-size:8.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;padding:1.5px 5px;border-radius:5px;color:' + col + ';background:color-mix(in srgb,' + col + ' 14%, transparent)">' + E(lab) + "</span>";
  }

  function avatar(c, sz) {
    var col = hue(c.name), fs = Math.round(sz * 0.34);
    var inner = c.group ? GROUP : E(initials(c.name));
    return '<span style="width:' + sz + "px;height:" + sz + "px;flex:0 0 " + sz + 'px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:' + fs + 'px;font-weight:700;letter-spacing:.02em;background:color-mix(in srgb, ' + col + ' 20%, transparent);color:' + col + '">' + inner + "</span>";
  }

  function preview(c) {
    if (c.attachment && (!c.last || c.last === "Attachment")) {
      return '<span style="display:inline-flex;align-items:center;gap:4px;color:var(--muted)">' + CLIP + "Attachment</span>";
    }
    if (c.reaction && (!c.last || c.last === "Reaction")) {
      return '<span style="color:var(--muted)">Reacted to a message</span>';
    }
    if (!c.last) return '<span style="color:var(--muted)">(attachment/rich message)</span>';
    var pre = "";
    if (c.from_me) pre = '<span style="color:var(--faint)">You: </span>';
    else if (c.group) pre = '<span style="color:' + hue(c.sender) + '">' + E(String(c.sender || "").split(/\s+/)[0]) + ": </span>";
    return pre + E(c.last);
  }

  function unreadBadge(c) {
    if (!(c.unread > 0)) return "";
    return '<span class="num" style="flex:0 0 auto;min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:var(--iris);color:#fff;font-size:10.5px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;line-height:1">' + E(c.unread > 99 ? "99+" : String(c.unread)) + "</span>";
  }

  // ---- state cards -----------------------------------------------------------
  function syncingCard(big) {
    return '<div style="display:flex;align-items:center;gap:9px;color:var(--muted);padding:' + (big ? "14px 2px" : "4px 0") + '">' + SPIN +
      '<div style="min-width:0"><div style="font-size:' + (big ? "13.5" : "12.5") + 'px;font-weight:620;color:var(--ink)">Waiting for the Hermes app to sync Messages&hellip;</div>' +
      '<div class="w-sub" style="font-size:11px;margin-top:2px">The Hermes Assistant app reads Messages locally and syncs about once a minute. Nothing leaves this Mac.</div></div></div>';
  }

  function grantNudge() {
    return '<div style="display:flex;align-items:center;gap:8px;color:var(--warn);font-weight:660;font-size:12.5px;margin:2px 0 5px">' + WARN + "Full Disk Access needed</div>" +
      '<div class="w-sub" style="font-size:11.5px;line-height:1.5">Grant <b style="color:var(--ink)">Hermes Assistant.app</b> Full Disk Access, then relaunch the app. Open this widget for the exact steps.</div>';
  }

  function grantCard() {
    return '<div style="padding:16px;border:1px solid var(--hairline);border-radius:14px;background:var(--glass-2)">' +
      '<div style="display:flex;align-items:center;gap:9px;margin-bottom:8px">' +
        '<span style="color:var(--warn);display:flex">' + WARN + "</span>" +
        '<span style="font-weight:680;font-size:14px">Full Disk Access needed</span></div>' +
      '<div class="w-sub" style="line-height:1.5;color:var(--muted)">The <b style="color:var(--ink)">Hermes Assistant app</b> reads your Messages database locally and hands the dashboard a few recent previews &mdash; nothing leaves this Mac. macOS requires you to grant the app Full Disk Access once:</div>' +
      '<ol style="margin:10px 0 4px;padding-left:20px;font-size:12px;color:var(--muted);line-height:1.7">' +
        '<li>Open <b style="color:var(--ink)">System Settings &rsaquo; Privacy &amp; Security &rsaquo; Full Disk Access</b></li>' +
        '<li>Click <b style="color:var(--ink)">+</b> and add <b style="color:var(--ink)">/Applications/Hermes Assistant.app</b> <span class="w-sub">(the app, not python3 &mdash; that is the whole workaround)</span></li>' +
        '<li>Toggle it <b style="color:var(--ink)">On</b></li>' +
        '<li><b style="color:var(--ink)">Quit and reopen</b> Hermes Assistant &mdash; the grant applies on next launch</li></ol>' +
      '<div class="w-sub" style="font-size:11px;margin:2px 0 10px">Within about a minute of relaunching, this widget fills with your recent conversations.</div>' +
      '<a href="' + FDA_URL + '" style="display:inline-flex;align-items:center;gap:7px;padding:8px 14px;border-radius:10px;background:var(--iris);color:#fff;font-size:12.5px;font-weight:650;text-decoration:none">Open Full Disk Access settings</a>' +
      "</div>";
  }

  function staleBadge(d) {
    if (!d.stale || d.age_s == null) return "";
    return '<div class="w-sub" style="display:inline-flex;align-items:center;gap:5px;font-size:10.5px;color:var(--faint);margin:6px 0 0">' + SPIN.replace('width="15" height="15"', 'width="10" height="10"') + "synced " + E(agoM(d.age_s)) + "m ago &mdash; waiting for the app&rsquo;s next sync</div>";
  }

  // ---- compact card body ------------------------------------------------------
  if (typeof RENDER !== "undefined") {
    RENDER.messages = function (b, d, mslot) {
      d = d || {};
      if (mslot) mslot.textContent = "";
      if (d.never_synced) { b.innerHTML = syncingCard(false); return; }
      if (!d.available) {
        if (d.grant) { if (mslot) mslot.textContent = "action needed"; b.innerHTML = grantNudge(); return; }
        b.innerHTML = '<div class="hint">' + E(d.reason || "Messages unavailable.") + "</div>";
        return;
      }
      var C = d.conversations || [];
      if (mslot) {
        var m = d.total_unread > 0 ? E(String(d.total_unread)) + " unread" : "up to date";
        if (d.stale && d.age_s != null) m = "synced " + agoM(d.age_s) + "m ago";
        mslot.textContent = m;
      }
      if (!C.length) { b.innerHTML = '<div class="hint">No recent conversations.</div>'; return; }
      b.innerHTML = C.slice(0, 3).map(function (c) {
        return '<div style="display:flex;align-items:center;gap:9px;padding:5px 0;border-bottom:1px solid var(--hairline)">' +
          avatar(c, 26) +
          '<div style="min-width:0;flex:1">' +
            '<div style="display:flex;align-items:baseline;gap:7px">' +
              '<span style="font-weight:' + (c.unread > 0 ? "700" : "620") + ';font-size:12.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + E(c.name) + "</span>" +
              '<span class="w-sub num" style="margin-left:auto;flex:0 0 auto;font-size:10.5px;color:' + (c.unread > 0 ? "var(--iris)" : "var(--faint)") + '">' + RT(c.ts) + "</span>" +
            "</div>" +
            '<div style="display:flex;align-items:center;gap:7px;margin-top:1px">' +
              '<span style="min-width:0;flex:1;font-size:11.5px;line-height:1.35;color:' + (c.unread > 0 ? "var(--ink)" : "var(--muted)") + ';overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + preview(c) + "</span>" +
              unreadBadge(c) +
            "</div>" +
          "</div></div>";
      }).join("");
    };
  }

  // ---- rich pop-out ------------------------------------------------------------
  if (typeof EXPAND_RENDER !== "undefined") {
    EXPAND_RENDER.messages = function (el, data) {
      var d = data || {};
      if (d.never_synced) { el.innerHTML = syncingCard(true); return; }
      if (!d.available) {
        el.innerHTML = d.grant ? grantCard() : '<div class="hint">' + E(d.reason || "Messages unavailable.") + "</div>";
        return;
      }
      var C = d.conversations || [];
      var h = "";
      if (typeof statGrid === "function") {
        h += statGrid([
          ["Unread", (d.total_unread || 0)],
          ["Chats", (d.convo_count || C.length)],
          ["Today", (d.today_count || 0)]
        ]);
      }
      h += staleBadge(d);
      if (!C.length) {
        el.innerHTML = h + '<div class="hint" style="margin-top:12px">No recent conversations.</div>';
        return;
      }
      h += '<div style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:700;margin:14px 0 6px">Conversations</div>';
      h += C.map(function (c) {
        var unread = c.unread > 0;
        return '<div data-msgrow style="display:flex;align-items:center;gap:11px;padding:9px 2px;border-bottom:1px solid var(--hairline)">' +
          avatar(c, 38) +
          '<div style="min-width:0;flex:1">' +
            '<div style="display:flex;align-items:baseline;gap:8px">' +
              '<span style="font-weight:' + (unread ? "700" : "600") + ';font-size:13.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + E(c.name) + "</span>" +
              (c.group ? '<span class="w-sub" style="font-size:10px;flex:0 0 auto">' + E(String(c.participants || "")) + "</span>" : "") +
              svcChip(c.service) +
              '<span class="w-sub num" style="margin-left:auto;flex:0 0 auto;font-size:11px;color:' + (unread ? "var(--iris)" : "var(--faint)") + '">' + RT(c.ts) + "</span>" +
            "</div>" +
            '<div style="display:flex;align-items:center;gap:8px;margin-top:2px">' +
              '<span style="min-width:0;flex:1;font-size:12px;line-height:1.35;color:' + (unread ? "var(--ink)" : "var(--muted)") + ';overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + preview(c) + "</span>" +
              unreadBadge(c) +
            "</div>" +
          "</div></div>";
      }).join("");
      h += '<div class="w-sub" style="margin-top:10px;font-size:10.5px;color:var(--faint);display:flex;align-items:center;gap:5px">' + LOCK +
        "Local-only &middot; read by the Hermes app, previews stored on this Mac, never sent anywhere</div>";
      el.innerHTML = h;

      // Motion One row entrance (respects prefers-reduced-motion; guarded)
      try {
        if (typeof animate === "function" && el.querySelectorAll) {
          var rows = el.querySelectorAll("[data-msgrow]");
          for (var i = 0; i < rows.length; i++) {
            animate(rows[i], { opacity: [0, 1], transform: ["translateY(6px)", "translateY(0px)"] },
              { duration: 0.35, delay: i * 0.024, easing: [0.2, 0.7, 0.3, 1] });
          }
        }
      } catch (e) {}
    };
  }
})();
