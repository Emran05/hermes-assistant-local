// aux_newsdesk.js — frontend for the news-desk widgets:
//   * framing ("Every Lens") — one story, framed by each outlet's lean
//   * trends  ("Trend Radar") — what's accelerating across the feeds  [added later]
// Mirrors the aux_claude_usage.js widget idiom: WICONS.<id> (glyph paths),
// RENDER.<id> (compact body), EXPAND_RENDER.<id> (pop-out). No emoji.
(function () {
  "use strict";
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }

  // lean → accent colour (matches the World-Brief lean tags)
  var LEAN_C = { factual: "#6b8afd", public: "#14b8a6", left: "#3b82f6",
                 right: "#ef4444", Mideast: "#f59e0b" };

  if (typeof WICONS !== "undefined") {
    // two overlapping lenses — "different views of the same thing"
    WICONS.framing =
      '<circle cx="9.5" cy="12" r="5.6" fill="currentColor" opacity=".18"/>' +
      '<circle cx="14.5" cy="12" r="5.6"/>';
  }

  if (typeof RENDER !== "undefined") {
    RENDER.framing = function (body, data, mslot) {
      var d = data || {}, stories = d.stories || [];
      if (d.error) { body.innerHTML = '<div class="hint">framing unavailable</div>'; return; }
      if (!stories.length) {
        body.innerHTML = '<div class="hint">No cross-outlet stories in the last day yet — the hourly gather is still filling in.</div>';
        return;
      }
      if (mslot) mslot.textContent = stories.length + " stories";
      var h = "";
      stories.slice(0, 3).forEach(function (s) {
        h += '<div style="margin:0 0 9px">' +
          '<div style="font-weight:650;font-size:12.5px;margin-bottom:3px">' + E(s.label) + "</div>";
        (s.angles || []).slice(0, 4).forEach(function (a) {
          var c = LEAN_C[a.lean] || "var(--muted)";
          h += '<div style="display:flex;gap:6px;align-items:baseline;font-size:11px;line-height:1.35;margin:1px 0">' +
            '<span style="flex:0 0 auto;color:' + c + ';font-weight:600;min-width:46px">' + E(a.lean) + "</span>" +
            '<span style="color:var(--ink);opacity:.9">' + E(a.title) +
            ' <span class="w-sub" style="opacity:.6">' + E(a.source) + "</span></span></div>";
        });
        h += "</div>";
      });
      body.innerHTML = h;
    };
  }

  if (typeof EXPAND_RENDER !== "undefined") {
    EXPAND_RENDER.framing = function (el, d) {
      d = d || {}; var stories = d.stories || [];
      if (!stories.length) { el.innerHTML = '<div class="hint">No cross-outlet stories yet.</div>'; return; }
      var h = '<div style="font-size:12px;color:var(--muted);margin-bottom:12px">' +
        'The same story, framed by each outlet’s lean — read across to see the spin.</div>';
      stories.forEach(function (s) {
        h += '<div style="margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid var(--hairline)">' +
          '<div style="font-weight:650;font-size:14px;margin-bottom:6px">' + E(s.label) +
          ' <span class="w-sub" style="font-weight:400">· ' + E(s.sources) + " outlets</span></div>";
        (s.angles || []).forEach(function (a) {
          var c = LEAN_C[a.lean] || "var(--muted)";
          h += '<div style="display:flex;gap:8px;align-items:baseline;margin:4px 0">' +
            '<span style="flex:0 0 auto;color:' + c + ';font-weight:600;font-size:11px;min-width:54px">' + E(a.lean) + "</span>" +
            '<a href="' + E(a.url) + '" onclick="event.stopPropagation()" style="color:var(--ink);text-decoration:none;font-size:13px">' +
            E(a.title) + ' <span class="w-sub">— ' + E(a.source) + "</span></a></div>";
        });
        h += "</div>";
      });
      el.innerHTML = h;
    };
  }

  // ==== Trend Radar ========================================================
  var KIND_C = { "new": "#14b8a6", rising: "#ef4444", steady: "var(--muted)" };

  function _tspark(arr, color) {
    arr = arr || [];
    if (arr.length < 2) return '<span style="display:inline-block;width:34px"></span>';
    var max = Math.max.apply(null, arr) || 1, w = 34, hh = 12, n = arr.length;
    var pts = arr.map(function (v, i) {
      return (i / (n - 1) * w).toFixed(1) + "," + (hh - (v / max) * hh).toFixed(1);
    }).join(" ");
    return '<svg width="' + w + '" height="' + hh + '" style="flex:0 0 auto"><polyline points="' +
      pts + '" fill="none" stroke="' + color + '" stroke-width="1.3"/></svg>';
  }

  if (typeof WICONS !== "undefined") {
    WICONS.trends =
      '<path d="M4 15l4-4 3 3 5-6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<path d="M13.5 8h3.5v3.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>';
  }

  if (typeof RENDER !== "undefined") {
    RENDER.trends = function (body, data, mslot) {
      var d = data || {}, rising = d.rising || [];
      if (d.error) { body.innerHTML = '<div class="hint">trends unavailable</div>'; return; }
      if (!rising.length) { body.innerHTML = '<div class="hint">Gathering — trends appear once the hourly feed pass runs.</div>'; return; }
      if (mslot) mslot.textContent = (d.days > 1 ? d.days + "d history" : "today");
      var h = "";
      rising.slice(0, 7).forEach(function (e) {
        var c = KIND_C[e.kind] || "var(--muted)";
        h += '<div style="display:flex;align-items:center;gap:8px;margin:3px 0">' +
          '<span style="flex:1;font-weight:600;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + E(e.entity) + "</span>" +
          _tspark(e.spark, c) +
          '<span style="flex:0 0 auto;font-size:9.5px;color:' + c + ';text-transform:uppercase;letter-spacing:.03em;min-width:34px;text-align:right">' + E(e.kind) + "</span>" +
          '<span class="num" style="flex:0 0 auto;font-weight:640;min-width:18px;text-align:right">' + E(e.today) + "</span></div>";
      });
      body.innerHTML = h;
    };
  }

  if (typeof EXPAND_RENDER !== "undefined") {
    EXPAND_RENDER.trends = function (el, d) {
      d = d || {}; var rising = d.rising || [];
      if (!rising.length) { el.innerHTML = '<div class="hint">No trends yet.</div>'; return; }
      var note = d.days > 1
        ? "Mentions today vs the last " + (d.days - 1) + "-day baseline — rising = accelerating."
        : "Today's hottest topics. Acceleration sharpens as the daily ledger fills.";
      var h = '<div style="font-size:12px;color:var(--muted);margin-bottom:12px">' + note + "</div>";
      rising.forEach(function (e) {
        var c = KIND_C[e.kind] || "var(--muted)";
        h += '<div style="display:flex;align-items:center;gap:10px;margin:6px 0">' +
          '<span style="flex:1;font-weight:600;font-size:13.5px">' + E(e.entity) + "</span>" +
          _tspark(e.spark, c) +
          '<span style="flex:0 0 auto;font-size:10.5px;color:' + c + ';text-transform:uppercase;min-width:44px;text-align:right">' + E(e.kind) + "</span>" +
          '<span class="w-sub" style="flex:0 0 auto;min-width:70px;text-align:right">' + E(e.today) + " today · " + E(e.avg) + " avg</span></div>";
      });
      el.innerHTML = h;
    };
  }
})();
