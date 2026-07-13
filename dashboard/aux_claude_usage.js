// aux_claude_usage.js — "Claude Usage" widget renderers (P: Claude Max tracker).
//
// Auto-served at /aux_claude_usage.js. Loaded AFTER /expand.js and the other
// aux JS, so these map assignments win. Adds:
//   * WICONS.claude_usage       — bespoke two-tone gauge glyph (widgetIcon)
//   * RENDER.claude_usage       — compact widget body (current 5h window +
//                                 reset countdown + today + 7-day sparkline)
//   * EXPAND_RENDER.claude_usage — pop-out (5h block detail + reset, today/7-day
//                                 chart, per-model, per-project, block history,
//                                 ≈ API-equivalent cost, soft-cap setter).
//
// Reuses global helpers from index.html (esc, kfmt, fmtNum, statGrid, barRows,
// animate) — all typeof-guarded so a headless render harness never throws.
// CLAUDE.md laws: zero emoji, bespoke SVG only, 12-hour time, esc() coercion.

(function () {
  "use strict";

  // ---- tiny helpers (guarded so headless eval can't throw) -----------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
  function KF(n) {
    if (typeof kfmt === "function") return kfmt(n || 0);
    n = +n || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
    return "" + n;
  }
  function NF(n) {
    if (typeof fmtNum === "function") return fmtNum(n == null ? 0 : n);
    return (+n || 0).toLocaleString();
  }
  function money(n) {
    n = +n;
    if (!isFinite(n)) n = 0;
    if (n >= 100) return "$" + Math.round(n).toLocaleString();
    return "$" + n.toFixed(2);
  }
  function t12(sec) {           // epoch seconds -> "3:45 PM" (12-hour)
    if (sec == null) return "";
    try {
      return new Date(sec * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    } catch (e) { return ""; }
  }
  function dur(sec) {           // seconds -> "2h 10m"
    sec = Math.max(0, Math.round(sec || 0));
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
    if (h > 0) return h + "h " + m + "m";
    if (m > 0) return m + "m";
    return sec + "s";
  }
  function sub(t) {
    return '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;' +
      'color:var(--muted);font-weight:600;margin:12px 0 6px">' + E(t) + "</div>";
  }
  function gaugeBar(pct, label, color) {
    pct = Math.max(2, Math.min(100, pct || 0));
    return '<div style="height:8px;border-radius:5px;background:var(--hairline);overflow:hidden;margin:2px 0 5px">' +
      '<i style="display:block;height:100%;width:' + pct + '%;border-radius:5px;background:' + color + '"></i></div>' +
      '<div class="w-sub" style="font-size:10.5px;margin-bottom:10px">' + E(label) + "</div>";
  }
  function famName(f) {
    f = String(f || "");
    return f ? (f.charAt(0).toUpperCase() + f.slice(1)) : "Other";
  }
  // full-width 7-day bar sparkline (bespoke SVG; last bar = accent)
  function cuBars(arr, peak, accent) {
    if (!arr || !arr.length) return "";
    var mx = peak || Math.max.apply(null, arr.concat([1])) || 1;
    var n = arr.length, gap = 2.5, bw = (100 - (n - 1) * gap) / n, bars = "";
    for (var i = 0; i < n; i++) {
      var pv = mx > 0 ? (arr[i] / mx) : 0;
      var hh = Math.max(3, pv * 27), last = i === n - 1;
      var col = last ? accent : "color-mix(in srgb," + accent + " 40%,transparent)";
      bars += '<rect x="' + (i * (bw + gap)).toFixed(2) + '" y="' + (29 - hh).toFixed(2) +
        '" width="' + bw.toFixed(2) + '" height="' + hh.toFixed(2) + '" rx="1.3" fill="' + col + '"/>';
    }
    return '<svg viewBox="0 0 100 30" preserveAspectRatio="none" style="width:100%;height:26px;display:block">' +
      bars + "</svg>";
  }

  // ---- bespoke widget icon: a usage gauge -----------------------------------
  if (typeof WICONS !== "undefined") {
    WICONS.claude_usage =
      '<path d="M3.5 17.5a8.5 8.5 0 0 1 17 0Z" fill="currentColor" opacity=".15"/>' +
      '<path d="M3.5 17.5a8.5 8.5 0 0 1 17 0" stroke-linecap="round"/>' +
      '<path d="M12 17.5l4.4-5.6" stroke-linecap="round"/>' +
      '<circle cx="12" cy="17.5" r="1.5" fill="currentColor"/>';
  }

  // ---- compact widget body --------------------------------------------------
  if (typeof RENDER !== "undefined") {
    RENDER.claude_usage = function (body, data, mslot) {
      var d = data || {};
      if (d.available === false) {
        body.innerHTML = '<div class="hint">' + E(d.reason || "No Claude Code logs found yet.") + "</div>";
        return;
      }
      var w = d.window || {}, t = d.today || {};
      var spark = d.spark || [], peak = d.day_peak || 0, accent = "var(--wac)";

      var resetLine;
      if (w.active && w.reset_at) {
        resetLine = "resets " + t12(w.reset_at) + " · " + dur(w.reset_at - Date.now() / 1000) + " left";
      } else {
        resetLine = "window clear · a new prompt starts one";
      }
      if (mslot) mslot.textContent = (d.block_hours || 5) + "h window";

      var capPct = null;
      if (d.cap && d.cap > 0 && w.total != null) capPct = Math.min(100, Math.round(w.total / d.cap * 100));

      var h = "";
      h += '<div style="display:flex;align-items:baseline;gap:8px;margin:2px 0 1px">' +
        '<span class="num" style="font-size:26px;font-weight:720;line-height:1;color:' + accent + '">' + KF(w.total || 0) + "</span>" +
        '<span class="w-sub" style="font-size:11px">tok this 5h</span>' +
        '<span class="num" style="margin-left:auto;font-weight:640;font-size:12.5px">' + money(w.cost) + "</span></div>";
      h += '<div class="w-sub" style="font-size:11px;display:flex;align-items:center;gap:6px;margin-bottom:8px">' +
        (w.active ? '<span class="livedot"></span>' : "") + E(resetLine) +
        (w.cache ? '<span style="margin-left:auto;opacity:.7" title="Cached context re-served each message — not new work, mostly free">+' + KF(w.cache) + " cache</span>" : "") + "</div>";

      if (capPct != null) {
        var cc = capPct >= 90 ? "var(--bad)" : capPct >= 70 ? "var(--warn)" : accent;
        h += '<div style="height:6px;border-radius:4px;background:var(--hairline);overflow:hidden;margin-bottom:4px">' +
          '<i style="display:block;height:100%;width:' + capPct + '%;border-radius:4px;background:' + cc + '"></i></div>' +
          '<div class="w-sub" style="font-size:10.5px;margin-bottom:8px">' + capPct + "% of your " + KF(d.cap) + " soft cap</div>";
      }

      h += '<div style="display:flex;gap:14px;font-size:11px;margin-bottom:6px">' +
        '<span class="w-sub">Today <b class="num" style="color:var(--ink)">' + KF(t.total || 0) + "</b></span>" +
        '<span class="w-sub">7-day <b class="num" style="color:var(--ink)">' + KF(d.week_total || 0) + "</b></span>" +
        '<span class="w-sub" style="margin-left:auto">≈' + money(t.cost || 0) + " today</span></div>";
      h += cuBars(spark, peak, accent);

      body.innerHTML = h;
    };
  }

  // ---- rich pop-out ---------------------------------------------------------
  if (typeof EXPAND_RENDER !== "undefined") {
    EXPAND_RENDER.claude_usage = function (el, d) {
      d = d || {};
      if (d.available === false) {
        el.innerHTML = '<div class="hint">' + E(d.reason || "No Claude Code session logs found.") + "</div>";
        return;
      }
      var w = d.window || {}, t = d.today || {}, wk = d.week || {}, accent = "var(--wac)";
      var h = "";

      // --- current 5-hour window hero ---
      var resetLine;
      if (w.active && w.reset_at) {
        resetLine = "Resets " + t12(w.reset_at) + " · " + dur(w.reset_at - Date.now() / 1000) + " remaining";
      } else {
        resetLine = "No active window — your next prompt opens a fresh 5-hour block.";
      }
      h += '<div style="display:flex;align-items:baseline;gap:10px;margin:2px 0 3px">' +
        '<span class="num" style="font-size:40px;font-weight:730;line-height:1;color:' + accent + '">' + KF(w.total || 0) + "</span>" +
        '<span style="font-size:13px;color:var(--muted)">tokens · 5-hour window</span>' +
        '<span class="num" style="margin-left:auto;font-weight:660;font-size:15px">' + money(w.cost) + "</span></div>";
      h += '<div class="w-sub" style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:10px">' +
        (w.active ? '<span class="livedot"></span>' : "") + E(resetLine) + "</div>";

      // --- rate-limit gauge: soft cap if set, else vs. own busiest block ---
      if (d.cap && d.cap > 0) {
        var cp = Math.min(100, Math.round((w.total || 0) / d.cap * 100));
        var cc = cp >= 90 ? "var(--bad)" : cp >= 70 ? "var(--warn)" : accent;
        h += gaugeBar(cp, KF(w.total || 0) + " / " + KF(d.cap) + " soft cap (" + cp + "%)", cc);
      } else if (d.block_peak) {
        var bp = Math.min(100, Math.round((w.total || 0) / d.block_peak * 100));
        h += gaugeBar(bp, "vs your busiest 5-hour block (" + KF(d.block_peak) + " tok) — no official Max cap published", accent);
      }

      // --- window breakdown ---
      if (typeof statGrid === "function") {
        h += statGrid([
          ["Input", KF(w.in || 0)], ["Output", KF(w.out || 0)],
          ["Cache read", KF(w.cr || 0)], ["Cache write", KF(w.cc || 0)],
          ["Messages", NF(w.msgs || 0)], ["≈ Cost", money(w.cost)],
        ]);
      }

      // --- today vs 7-day ---
      h += sub("Today vs last 7 days");
      if (typeof statGrid === "function") {
        h += statGrid([
          ["Today tok", KF(t.total || 0)], ["Today ≈", money(t.cost)], ["Today msgs", NF(t.msgs || 0)],
          ["7-day tok", KF(wk.total || 0)], ["7-day ≈", money(wk.cost)], ["7-day msgs", NF(wk.msgs || 0)],
        ]);
      }

      // --- 7-day daily bar chart ---
      var days = d.days || [];
      if (days.length) {
        var peak = Math.max.apply(null, days.map(function (x) { return x.total; }).concat([1]));
        h += sub("Daily tokens (7 days)");
        h += '<div style="display:flex;align-items:flex-end;gap:6px;height:92px">';
        days.forEach(function (x, i) {
          var hh = Math.max(3, Math.round((x.total / peak) * 100)), last = i === days.length - 1;
          h += '<div class="cu-bar" title="' + E(x.date + " · " + KF(x.total) + " tok") + '" style="flex:1;height:' + hh +
            "%;border-radius:5px 5px 2px 2px;transform-origin:bottom;background:" +
            (last ? accent : "color-mix(in srgb," + accent + " 42%,transparent)") + '"></div>';
        });
        h += "</div>";
        h += '<div style="display:flex;gap:6px;margin-top:4px">';
        days.forEach(function (x) {
          var dt = new Date((x.date || "") + "T00:00");
          var lab = isNaN(dt.getTime()) ? "" : dt.toLocaleDateString([], { weekday: "short" });
          h += '<div style="flex:1;text-align:center;font-size:9.5px;color:var(--faint)">' + E(lab) + "</div>";
        });
        h += "</div>";
      }

      // --- per-model ---
      var models = d.models || [];
      if (models.length && typeof barRows === "function") {
        h += sub("By model (7 days)");
        h += barRows(models.map(function (m) {
          return { label: famName(m.family), val: m.total, sub: money(m.cost) };
        }), 96, 60);
      }

      // --- per-project ---
      var projs = d.projects || [];
      if (projs.length && typeof barRows === "function") {
        h += sub("By project (7 days)");
        h += barRows(projs.map(function (p) {
          return { label: p.name, val: p.total, sub: money(p.cost) };
        }), 130, 56);
      }

      // --- block history ---
      var blocks = (d.blocks || []).slice().reverse();
      if (blocks.length) {
        h += sub("Recent 5-hour blocks");
        blocks.forEach(function (b) {
          var day = "";
          try { day = new Date(b.start * 1000).toLocaleDateString([], { month: "short", day: "numeric" }); } catch (e) {}
          h += '<div style="display:flex;align-items:center;gap:9px;padding:6px 0;border-bottom:1px solid var(--hairline);font-size:12px">' +
            (b.active ? '<span class="livedot"></span>' : '<span style="width:7px;flex:0 0 7px"></span>') +
            '<span class="num" style="width:126px;color:var(--muted)">' + E(day + " · " + t12(b.start) + "–" + t12(b.end)) + "</span>" +
            '<span class="num" style="font-weight:640;min-width:52px;text-align:right">' + KF(b.total) + "</span>" +
            '<span class="w-sub" style="flex:1;text-align:right;font-size:11px">' + NF(b.msgs) + " msgs · " + money(b.cost) + "</span></div>";
        });
      }

      // --- totals + cost caveat ---
      h += '<div class="w-sub" style="margin:11px 0 4px;font-size:11px">' +
        NF(d.sessions || 0) + " sessions · " + NF(d.messages || 0) +
        " assistant messages in the last " + (d.scan_days || 8) + " days</div>";
      h += '<div class="w-sub" style="font-size:10.5px;color:var(--faint);line-height:1.5;margin-bottom:10px">' +
        "Cost is <b>≈ API-equivalent</b> (published per-token prices) — your Max plan is a flat subscription, " +
        "so this is what the same tokens would bill on the API, not a charge. Max publishes no hard token limit; the " +
        "5-hour window above is the rolling reset Claude enforces.</div>";

      // --- optional soft-cap setter ---
      h += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">' +
        '<input id="cu-cap" placeholder="Soft cap (tokens / 5h)" ' +
        'style="flex:1;padding:7px 10px;border-radius:9px;border:1px solid var(--hairline);background:var(--glass-2);color:var(--ink);font:inherit" ' +
        'value="' + (d.cap ? E(d.cap) : "") + '">' +
        '<button class="primary" id="cu-cap-go" style="padding:7px 14px">Save</button></div>';
      h += '<div class="w-sub" id="cu-cap-msg" style="font-size:10.5px;min-height:14px"></div>';

      el.innerHTML = h;

      // wire the soft-cap control
      var inp = el.querySelector("#cu-cap"), go = el.querySelector("#cu-cap-go"), msg = el.querySelector("#cu-cap-msg");
      if (go) {
        go.onclick = function () {
          var v = (inp.value || "").trim();
          var cap = v === "" ? null : parseInt(v.replace(/[,_\s]/g, ""), 10);
          if (v !== "" && (isNaN(cap) || cap < 0)) {
            if (msg) msg.textContent = "Enter a whole number of tokens (or clear to remove).";
            return;
          }
          go.disabled = true;
          fetch("/api/claude_usage/config", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cap: cap })
          }).then(function (r) { return r.json(); }).then(function (x) {
            go.disabled = false;
            if (x && x.ok) {
              if (msg) msg.textContent = cap ? ("Soft cap set to " + cap.toLocaleString() + " tokens per 5-hour window.") : "Soft cap cleared.";
            } else if (msg) { msg.textContent = "Could not save."; }
          }).catch(function () { go.disabled = false; if (msg) msg.textContent = "Could not save."; });
        };
      }

      // subtle reveal on the daily bars (Motion One, fully guarded)
      try {
        if (typeof animate === "function") {
          var bars = el.querySelectorAll(".cu-bar");
          for (var i = 0; i < bars.length; i++) {
            animate(bars[i], { opacity: [0, 1], transform: ["scaleY(0.35)", "scaleY(1)"] },
              { duration: 0.5, delay: i * 0.035, easing: [0.2, 0.7, 0.3, 1] });
          }
        }
      } catch (e) {}
    };
  }
})();
