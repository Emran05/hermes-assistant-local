// aux_evals.js — the "Evals" card for Settings › Agent & Models.
//
// What it shows: the local eval suite (aux_evals.py) — nine deterministic
// cases run against the model that is already loaded, appended to a history
// you can chart. Last run, the trend, the per-case table, a Run-now button
// that tells you WHY it is disabled, and the schedule.
//
// PLACEMENT. Same constraint aux_network.js and aux_promptbudget.js document:
// aux_settings_shell.js builds its rail and its twelve panels ONCE from a
// PANELS array captured in its IIFE, ensureShell() early-returns on the second
// call, and window.SETTINGS_PANELS is a read-out, not a hook — so a module
// cannot register a new Settings panel. This is therefore a CARD, mounted
// DIRECTLY into #sec-models, and it deliberately does NOT fall back to
// #view-mind (the shell's relocator sends an unknown id to sec-system, which
// would file the eval suite under System & Data). Cards append in mount order,
// so the <script> tag goes LAST in index.html and the card lands after
// Context & compaction.
//
// The history chart is aux_mind_drill.js's chart, structurally: same SVG
// idioms, same "nice" rounded y-max, same rounded-top bars, same sparse x-axis
// labels, same 14/30/60-day segmented control. Bars are pass rate per DAY
// (runs bucketed by local date, averaged); the line over them is that day's
// median case latency on its own right-hand scale.
//
// Design laws (CLAUDE.md): zero emoji, bespoke SVG only, 12-hour clock,
// tabular numerals on every number, esc() on every interpolation, explicit
// transition-property, >=40px targets, colours only through tokens all four
// palette blocks re-declare, and every global helper typeof-guarded so this
// file can be eval'd in a headless harness.
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
  function RM() {
    if (typeof REDUCE !== "undefined") return !!REDUCE;
    try {
      return !!(W.matchMedia && W.matchMedia("(prefers-reduced-motion:reduce)").matches);
    } catch (e) { return false; }
  }

  var CARD_ID = "mind-extra-evals";
  var PANEL_ID = "sec-models";
  var SEL = "#" + CARD_ID;

  var S = {
    data: null,      // the GET /api/evals payload
    loaded: false,
    err: "",
    days: 14,        // history range
    busy: false,     // a run was just started
    saving: false,
    notice: ""
  };

  // ---- glyph (two-tone: accent fill + currentColor stroke; zero emoji) ------
  var GLY =
    '<svg class="evic" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<rect x="3.6" y="2.8" width="16.8" height="18.4" rx="3.2" fill="var(--iris)" opacity=".14"/>' +
    '<rect x="3.6" y="2.8" width="16.8" height="18.4" rx="3.2" fill="none" ' +
    'stroke="currentColor" stroke-width="1.5"/>' +
    '<path d="M7.6 9.2 9.4 11l3-3.4M7.6 15.6l1.8 1.8 3-3.4" fill="none" ' +
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<path d="M14.6 10.2h3M14.6 16.6h3" fill="none" stroke="currentColor" ' +
    'stroke-width="1.7" stroke-linecap="round"/></svg>';

  // ---- pure formatters (exported for the headless harness) -----------------
  function num(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return v.toLocaleString ? v.toLocaleString("en-US") : String(v);
  }

  function ms(n) {
    if (n == null || n === "") return "—";
    var v = Number(n);
    if (!isFinite(v) || v < 0) return "—";
    if (v < 1000) return Math.round(v) + " ms";
    return (Math.round(v / 100) / 10).toFixed(1) + " s";
  }

  // One scale for the stat colour AND the bar colour, so a green number can
  // never sit above an amber bar. 8/9 is the neutral bar: the drill's own
  // promotion threshold is 5/6, i.e. one case may fail without alarm.
  function rateTone(rate) {
    var r = Number(rate);
    if (!isFinite(r)) return "";
    if (r >= 1) return "ok";
    if (r >= 0.75) return "";
    if (r >= 0.5) return "warn";
    return "bad";
  }

  function clock(epoch) {
    var v = Number(epoch);
    if (!isFinite(v) || v <= 0) return "—";
    try {
      return new Date(v * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    } catch (e) { return "—"; }
  }

  // "today at 1:04 PM" / "yesterday at …" / "Sep 3 at …" — 12-hour, always.
  var MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function when(epoch, nowMs) {
    var v = Number(epoch);
    if (!isFinite(v) || v <= 0) return "never";
    var d, now;
    try {
      d = new Date(v * 1000);
      now = new Date(nowMs == null ? Date.now() : nowMs);
    } catch (e) { return "—"; }
    var same = function (a, b) {
      return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()
        && a.getDate() === b.getDate();
    };
    var y = new Date(now.getTime() - 86400000);
    if (same(d, now)) return "today at " + clock(v);
    if (same(d, y)) return "yesterday at " + clock(v);
    return MN[d.getMonth()] + " " + d.getDate() + " at " + clock(v);
  }

  function hhmm12(h, m) {
    var hr = Number(h), mi = Number(m);
    if (!isFinite(hr)) hr = 0;
    if (!isFinite(mi)) mi = 0;
    var ap = hr < 12 ? "AM" : "PM";
    var h12 = hr % 12; if (h12 === 0) h12 = 12;
    return h12 + ":" + (mi < 10 ? "0" : "") + mi + " " + ap;
  }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  function dayKey(epoch) {
    var d = new Date(Number(epoch) * 1000);
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  // PURE: history rows -> one bucket per LOCAL DAY inside the range, oldest
  // first, with an entry for every day so the x-axis is a real calendar and a
  // gap reads as a gap rather than as a shorter bar.
  function bucketByDay(history, days, nowMs) {
    var out = [], by = {}, i;
    var now = new Date(nowMs == null ? Date.now() : nowMs);
    var start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    start.setDate(start.getDate() - (days - 1));
    var first = start.getTime() / 1000;
    for (i = 0; i < (history || []).length; i++) {
      var r = history[i];
      if (!r || !isFinite(Number(r.ts)) || Number(r.ts) < first) continue;
      var k = dayKey(r.ts);
      if (!by[k]) by[k] = { d: k, runs: 0, passed: 0, total: 0, lat: 0, last: null };
      by[k].runs += 1;
      by[k].passed += Number(r.passed) || 0;
      by[k].total += Number(r.total) || 0;
      by[k].lat += Number(r.median_latency_ms) || 0;
      by[k].last = r;
    }
    var cur = new Date(start.getTime());
    for (i = 0; i < days; i++) {
      var key = cur.getFullYear() + "-" + pad2(cur.getMonth() + 1) + "-" + pad2(cur.getDate());
      var b = by[key];
      out.push(b
        ? { d: key, runs: b.runs, rate: b.total ? b.passed / b.total : 0,
            passed: b.passed, total: b.total,
            median: Math.round(b.lat / b.runs), trigger: (b.last || {}).trigger || "" }
        : { d: key, runs: 0, rate: null, passed: 0, total: 0, median: null, trigger: "" });
      cur.setDate(cur.getDate() + 1);
    }
    return out;
  }

  function dLabel(iso) {
    var p = String(iso || "").split("-");
    var m = MN[(+p[1]) - 1];
    return m ? (m + " " + (+p[2])) : String(iso || "");
  }

  function niceMax(raw) {
    var v = Number(raw);
    if (!isFinite(v) || v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), mm = v / p;
    return (mm <= 1 ? 1 : mm <= 1.5 ? 1.5 : mm <= 2 ? 2 : mm <= 2.5 ? 2.5
      : mm <= 3 ? 3 : mm <= 4 ? 4 : mm <= 5 ? 5 : mm <= 7.5 ? 7.5 : 10) * p;
  }

  // ---- the history chart (aux_mind_drill.js's idioms) ----------------------
  function chartHTML(buckets) {
    var withRuns = (buckets || []).filter(function (b) { return b.runs > 0; });
    if (!withRuns.length) {
      return '<div class="evhint">No eval runs recorded in the last ' +
        E(String((buckets || []).length)) + " days.</div>";
    }
    var Wd = 620, H = 156, padL = 34, padR = 40, padT = 12, padB = 22;
    var plotW = Wd - padL - padR, plotH = H - padT - padB, y0 = padT + plotH;
    var n = buckets.length, slot = plotW / n;
    var barW = Math.max(2, Math.min(22, Math.floor(slot - (n > 20 ? 2 : 8))));
    var latMax = niceMax(withRuns.reduce(function (a, b) {
      return Math.max(a, b.median || 0);
    }, 1));
    var labEvery = Math.max(1, Math.ceil(n / 13));
    var dly = Math.min(40, Math.max(6, Math.round(620 / n)));
    var reduce = RM();

    function roundTop(x, y, w, h, r) {
      r = Math.min(r, h / 2, w / 2);
      return '<path d="M' + x + " " + (y + h) + "v" + (-(h - r)) + "a" + r + " " + r +
        " 0 0 1 " + r + " " + (-r) + "h" + (w - 2 * r) + "a" + r + " " + r + " 0 0 1 " +
        r + " " + r + "v" + (h - r) + 'z"';
    }

    var svg = '<svg class="evchart" viewBox="0 0 ' + Wd + " " + H +
      '" preserveAspectRatio="xMidYMid meet" width="100%" aria-hidden="true">';
    [0, 0.5, 1].forEach(function (f) {
      var gy = y0 - f * plotH;
      svg += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (Wd - padR) + '" y2="' + gy +
        '" stroke="var(--hairline)" stroke-width="1"/>';
      if (f > 0) {
        svg += '<text x="' + (padL - 5) + '" y="' + (gy + 3) +
          '" text-anchor="end" class="evtick num">' + Math.round(f * 100) + "%</text>";
        svg += '<text x="' + (Wd - padR + 5) + '" y="' + (gy + 3) +
          '" text-anchor="start" class="evtick evtick-r num">' +
          Math.round(latMax * f / 1000 * 10) / 10 + "s</text>";
      }
    });

    // pass-rate bars
    buckets.forEach(function (b, i) {
      var cx = padL + slot * i + slot / 2, bx = cx - barW / 2;
      var lab = dLabel(b.d), p = String(b.d).split("-");
      var xlab = (i % labEvery === 0)
        ? '<text x="' + cx + '" y="' + (H - 7) + '" text-anchor="middle" ' +
          'class="evtick num">' + (+p[1]) + "/" + (+p[2]) + "</text>"
        : "";
      if (b.runs === 0) { svg += xlab; return; }
      var h = Math.max(1.5, (b.rate || 0) * plotH);
      var t = rateTone(b.rate);
      var col = t === "ok" ? "var(--ok)" : (t === "warn" ? "var(--warn)"
        : (t === "bad" ? "var(--bad)" : "var(--iris)"));
      var tip = lab + " · " + b.passed + "/" + b.total + " passed · median " +
        ms(b.median) + (b.runs > 1 ? " · " + b.runs + " runs" : "");
      svg += '<g class="evgrow"' + (reduce ? "" : ' style="animation-delay:' + (i * dly) + 'ms"') +
        "><title>" + E(tip) + "</title>" +
        roundTop(bx, y0 - h, barW, h, 4) + ' fill="' + col + '"/></g>';
      svg += xlab;
    });

    // median-latency line on the right-hand scale (only over days with runs)
    var pts = [];
    buckets.forEach(function (b, i) {
      if (b.runs === 0 || !isFinite(Number(b.median))) return;
      pts.push([padL + slot * i + slot / 2, y0 - (b.median / latMax) * plotH]);
    });
    if (pts.length > 1) {
      svg += '<path class="evline" d="M' + pts.map(function (p) {
        return (Math.round(p[0] * 10) / 10) + " " + (Math.round(p[1] * 10) / 10);
      }).join("L") + '" fill="none" stroke="var(--quick)" stroke-width="1.8" ' +
        'stroke-linecap="round" stroke-linejoin="round"/>';
    }
    pts.forEach(function (p) {
      svg += '<circle cx="' + (Math.round(p[0] * 10) / 10) + '" cy="' +
        (Math.round(p[1] * 10) / 10) + '" r="2.4" fill="var(--quick)"/>';
    });

    svg += '<line x1="' + padL + '" y1="' + y0 + '" x2="' + (Wd - padR) + '" y2="' + y0 +
      '" stroke="var(--hairline)" stroke-width="1"/></svg>';

    return '<div class="evlegend"><span><i style="background:var(--ok)"></i>Pass rate</span>' +
      '<span><i class="evline-key" style="background:var(--quick)"></i>Median latency</span>' +
      "</div>" + svg +
      // the bars are colour-coded by rate, so the legend swatch alone would be
      // a lie — say what the colours mean, once, in words
      '<p class="evcap">A bar is one day. Green means all nine cases passed, ' +
      "amber that fewer than three quarters did. The line is that day's median " +
      "case latency on the right-hand scale.</p>";
  }

  function segHTML(days) {
    return '<span class="evseg">' + [14, 30, 60].map(function (d) {
      return '<b data-days="' + d + '"' + (d === days ? ' class="on"' : "") + ">" + d + "d</b>";
    }).join("") + "</span>";
  }

  // ---- styles --------------------------------------------------------------
  function CSS() {
    return "<style>" +
      SEL + " .evic{flex:0 0 auto;color:var(--muted)}" +
      SEL + " h2{display:flex;align-items:center;gap:7px;flex-wrap:wrap}" +
      SEL + " .evlede{margin:2px 0 14px;font-size:12.5px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty;max-width:64ch}" +
      SEL + " .evstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));" +
        "gap:2px 18px;margin:0 0 14px;padding:0 0 14px;border-bottom:1px solid var(--hairline)}" +
      SEL + " .evstat b{display:block;font-size:19px;font-weight:640;color:var(--ink);" +
        "font-variant-numeric:tabular-nums;line-height:1.25}" +
      // a stat whose value is a phrase ("today at 1:33 PM"), not a figure —
      // at 19px it wrapped to two lines and pushed its own caption out of the
      // row, so the words get their own smaller size
      SEL + " .evstat.is-text b{font-size:14px;line-height:1.35;font-weight:620}" +
      SEL + " .evstat span{display:block;font-size:10.5px;letter-spacing:.05em;" +
        "text-transform:uppercase;color:var(--faint);margin-top:2px}" +
      SEL + " .evstat.is-ok b{color:var(--ok)}" +
      SEL + " .evstat.is-warn b{color:var(--warn)}" +
      SEL + " .evstat.is-bad b{color:var(--bad)}" +
      // chart
      SEL + " .evhead{display:flex;align-items:center;justify-content:space-between;" +
        "gap:10px;margin:0 0 4px}" +
      SEL + " .evhead h3{margin:0;font-size:12px;font-weight:620;color:var(--ink);" +
        "letter-spacing:.01em}" +
      SEL + " .evseg{display:inline-flex;gap:2px;padding:2px;border-radius:9px;" +
        "background:var(--glass-2);border:1px solid var(--hairline);flex:0 0 auto}" +
      SEL + " .evseg b{font-size:9.5px;font-weight:620;color:var(--muted);padding:4px 8px;" +
        "border-radius:7px;cursor:pointer;user-select:none;letter-spacing:.02em;" +
        "font-variant-numeric:tabular-nums;transition-property:color,background-color;" +
        "transition-duration:150ms;transition-timing-function:ease-out}" +
      SEL + " .evseg b.on{color:var(--ink);background:var(--glass);" +
        "box-shadow:inset 0 1px 0 var(--specular)}" +
      SEL + " .evlegend{display:flex;gap:14px;margin:2px 0 2px;font-size:10.5px;" +
        "color:var(--faint)}" +
      SEL + " .evlegend i{display:inline-block;width:8px;height:8px;border-radius:2px;" +
        "margin-right:5px;vertical-align:middle}" +
      SEL + " .evlegend i.evline-key{height:2.5px;width:12px;border-radius:2px}" +
      SEL + " .evchart{display:block;margin:2px 0 0;overflow:visible}" +
      SEL + " .evtick{font-size:8.5px;fill:var(--faint);font-variant-numeric:tabular-nums}" +
      SEL + " .evgrow{transform-origin:center bottom;animation:evgrow .5s cubic-bezier(.2,.8,.3,1) both}" +
      "@keyframes evgrow{from{transform:scaleY(0);opacity:0}to{transform:scaleY(1);opacity:1}}" +
      SEL + " .evskel{height:120px;border-radius:11px;margin:8px 0;" +
        "background:linear-gradient(90deg,var(--glass-2),var(--glass),var(--glass-2));" +
        "background-size:200% 100%;animation:evsh 1.3s linear infinite}" +
      "@keyframes evsh{0%{background-position:200% 0}100%{background-position:-200% 0}}" +
      // per-case table
      SEL + " ul.evcases{list-style:none;margin:14px 0 0;padding:0}" +
      SEL + " ul.evcases li{display:grid;grid-template-columns:16px minmax(0,1fr) auto;" +
        "gap:3px 10px;align-items:start;padding:9px 0;border-top:1px solid var(--hairline)}" +
      SEL + " ul.evcases li .evmark{grid-row:1/span 2;margin-top:2px;width:14px;height:14px}" +
      SEL + " ul.evcases li b{font-size:12.5px;font-weight:620;color:var(--ink);min-width:0;" +
        "overflow-wrap:anywhere}" +
      SEL + " ul.evcases li .evms{font-size:11px;color:var(--faint);" +
        "font-variant-numeric:tabular-nums;white-space:nowrap}" +
      SEL + " ul.evcases li .evdetail{grid-column:2/span 2;font-size:11px;line-height:1.5;" +
        "color:var(--muted);text-wrap:pretty;overflow-wrap:anywhere}" +
      SEL + " ul.evcases li.is-fail .evdetail{color:var(--bad)}" +
      // controls
      SEL + " .evbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:14px 0 0;" +
        "padding-top:12px;border-top:1px solid var(--hairline)}" +
      SEL + " button.evb{padding:9px 16px;border-radius:10px;font-size:12.5px;font-weight:600;" +
        "cursor:pointer;border:0;background:var(--iris);color:#fff;min-height:40px;" +
        "transition-property:opacity;transition-duration:150ms;transition-timing-function:ease-out}" +
      SEL + " button.evb:disabled{opacity:.45;cursor:default}" +
      SEL + " .evwhy{font-size:11.5px;line-height:1.5;color:var(--muted);text-wrap:pretty}" +
      SEL + " .evrow{display:flex;align-items:center;justify-content:space-between;gap:14px;" +
        "padding:11px 0;min-height:40px;box-sizing:border-box;border-top:1px solid var(--hairline)}" +
      SEL + " .evrow .evl{min-width:0}" +
      SEL + " .evrow .evl b{display:block;font-size:12.5px;font-weight:620;color:var(--ink)}" +
      SEL + " .evrow .evl span{display:block;font-size:11.5px;line-height:1.45;color:var(--muted);" +
        "text-wrap:pretty;margin-top:2px}" +
      SEL + " .evsw{position:relative;flex:0 0 auto;width:44px;height:26px;border-radius:13px;" +
        "border:1px solid var(--hairline);cursor:pointer;padding:0;" +
        "background:var(--chip,rgba(255,255,255,.06));" +
        "transition-property:background-color,border-color;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " .evsw::after{content:'';position:absolute;top:2px;left:2px;width:20px;height:20px;" +
        "border-radius:50%;background:var(--ink);opacity:.55;" +
        "transition-property:transform,opacity;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + ' .evsw[aria-checked="true"]{background:var(--iris);border-color:var(--iris)}' +
      SEL + ' .evsw[aria-checked="true"]::after{transform:translateX(18px);background:#fff;opacity:1}' +
      SEL + " .evsw:disabled{opacity:.45;cursor:default}" +
      SEL + " input.evtime,select.evsel{font:inherit;font-size:12.5px;min-height:40px;" +
        "padding:6px 10px;border-radius:10px;color:var(--ink);background:var(--glass-2);" +
        "border:1px solid var(--hairline);font-variant-numeric:tabular-nums}" +
      SEL + " .evfoot{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--hairline);" +
        "font-size:11px;line-height:1.55;color:var(--faint);text-wrap:pretty}" +
      SEL + " .everr{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--bad);" +
        "text-wrap:pretty}" +
      SEL + " .evnote{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--muted);" +
        "text-wrap:pretty}" +
      SEL + " .evhint{margin:10px 0;font-size:11.5px;line-height:1.5;color:var(--faint)}" +
      SEL + " .evcap{margin:6px 0 0;font-size:10.5px;line-height:1.5;color:var(--faint);" +
        "text-wrap:pretty;max-width:64ch}" +
      "@media (prefers-reduced-motion:reduce){" + SEL + " .evskel{animation:none}" +
        SEL + " .evgrow{animation:none}" + SEL + " .evseg b{transition:none}}" +
      "</style>";
  }

  // ---- markup --------------------------------------------------------------
  var TICK = '<svg class="evmark" viewBox="0 0 16 16" aria-hidden="true">' +
    '<circle cx="8" cy="8" r="7" fill="none" stroke="var(--ok)" stroke-width="1.5"/>' +
    '<path d="M4.8 8.2 7 10.4l4.2-4.6" fill="none" stroke="var(--ok)" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var CROSS = '<svg class="evmark" viewBox="0 0 16 16" aria-hidden="true">' +
    '<circle cx="8" cy="8" r="7" fill="none" stroke="var(--bad)" stroke-width="1.5"/>' +
    '<path d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2" fill="none" stroke="var(--bad)" ' +
    'stroke-width="1.8" stroke-linecap="round"/></svg>';

  function stat(value, label, tone, text) {
    return '<div class="evstat' + (tone ? " is-" + tone : "") +
      (text ? " is-text" : "") + '"><b>' + E(value) +
      "</b><span>" + E(label) + "</span></div>";
  }

  function tone(passed, total) {
    if (!total) return "";
    return rateTone(passed / total);
  }

  function sw(act, on, label, disabled) {
    return '<button class="evsw" role="switch" data-act="' + E(act) + '" aria-checked="' +
      (on ? "true" : "false") + '" aria-label="' + E(label) + '"' +
      (disabled ? " disabled" : "") + "></button>";
  }

  function row(title, sub, control) {
    return '<div class="evrow"><div class="evl"><b>' + E(title) + "</b>" +
      (sub ? "<span>" + E(sub) + "</span>" : "") + "</div>" + control + "</div>";
  }

  function casesHTML(results) {
    var rows = (results || []).slice();
    if (!rows.length) return "";
    // fails first, then in run order — the point of the table is what broke
    rows.sort(function (a, b) {
      return (a.passed === b.passed) ? 0 : (a.passed ? 1 : -1);
    });
    return '<ul class="evcases">' + rows.map(function (c) {
      return '<li class="' + (c.passed ? "is-pass" : "is-fail") + '">' +
        (c.passed ? TICK : CROSS) +
        "<b>" + E(c.label || c.case) + "</b>" +
        '<span class="evms num">' + E(ms(c.latency_ms)) + "</span>" +
        '<span class="evdetail">' + E(c.detail || "") + "</span></li>";
    }).join("") + "</ul>";
  }

  function scheduleHTML(cfg, d) {
    var t = pad2(Number(cfg.at_hour) || 0) + ":" + pad2(Number(cfg.at_minute) || 0);
    var every = Number(cfg.days) === 1 ? "every day"
      : "every " + Number(cfg.days) + " days";
    return row("Run on a schedule",
               "The suite runs " + every + " at " + hhmm12(cfg.at_hour, cfg.at_minute) +
               ", or at the first moment after that when the conditions below are met.",
               sw("enabled", !!cfg.enabled, "Run evals on a schedule")) +
      row("Time of day", "First attempt of the day.",
          '<input class="evtime" type="time" data-k="time" value="' + E(t) +
          '" aria-label="Scheduled time">') +
      row("How often", "",
          '<select class="evsel" data-k="days" aria-label="How often">' +
          [1, 2, 3, 7, 14].map(function (n) {
            return '<option value="' + n + '"' + (Number(cfg.days) === n ? " selected" : "") +
              ">" + (n === 1 ? "Every day" : "Every " + n + " days") + "</option>";
          }).join("") + "</select>") +
      row("Only on AC power",
          "On battery the suite is skipped and the day keeps being retried.",
          sw("require_ac", !!cfg.require_ac, "Only run on AC power")) +
      row("Wake the model if it is asleep",
          "Off by default: the suite runs only when the model is already loaded, so it " +
          "costs nothing until you are plugged in and using it. Waking never happens on " +
          "battery, whatever this is set to.",
          sw("wake_if_ac", !!cfg.wake_if_ac, "Wake the model on AC power"));
  }

  function cardHTML(state) {
    state = state || {};
    var d = state.data;
    if (!d) {
      return CSS() + "<h2>" + GLY + "Evals</h2>" +
        '<div class="body"><p class="evlede">Reading the eval history…</p>' +
        '<div class="evskel"></div></div>';
    }
    if (d.ok === false) {
      return CSS() + "<h2>" + GLY + "Evals</h2>" +
        '<div class="body"><p class="everr">' +
        E(d.error || "Could not read the eval suite.") + "</p></div>";
    }

    var cfg = d.settings || {};
    var last = d.last;
    var total = last ? (Number(last.total) || 0) : 0;
    var passed = last ? (Number(last.passed) || 0) : 0;

    var head = CSS() + "<h2>" + GLY + "Evals" +
      '<span class="tiny num">' + E(String(d.cases_total || 9)) + " cases</span></h2>";

    var lede = '<p class="evlede">Nine deterministic cases against the model that is ' +
      "already loaded — six tool-calling cases borrowed from the model Drill, plus three " +
      "format contracts (strict JSON, a Markdown table, one sentence). Nothing is ever " +
      "executed, no model is switched, and on battery nothing runs at all.</p>";

    var stats = last
      ? '<div class="evstats">' +
        stat(passed + "/" + total, "passed", tone(passed, total)) +
        stat(ms(last.median_latency_ms), "median case") +
        stat(when(last.ts), "last run", "", true) +
        stat(last.trigger === "scheduled" ? "Scheduled" : "Manual", "trigger", "", true) +
        "</div>"
      : '<div class="evstats">' + stat("—", "passed") + stat("—", "median case") +
        stat("never", "last run", "", true) + stat("—", "trigger", "", true) + "</div>";

    var chart = '<div class="evhead"><h3>History</h3>' + segHTML(state.days) + "</div>" +
      chartHTML(bucketByDay(d.history || [], state.days));

    var runNote = "";
    if (state.busy || d.running) {
      runNote = '<span class="evwhy">' + E(d.note || "Running the suite…") + "</span>";
    } else if (!d.can_run && d.reason) {
      runNote = '<span class="evwhy">' + E(d.reason) +
        (d.wakeable ? " — it will wake for a run once you are plugged in." : "") + "</span>";
    }
    var bar = '<div class="evbar"><button class="evb" data-act="run"' +
      ((d.can_run && !state.busy && !d.running) ? "" : " disabled") + ">" +
      ((state.busy || d.running) ? "Running…" : "Run now") + "</button>" + runNote + "</div>";

    var cases = last ? casesHTML(d.results || []) : "";

    var err = state.err ? '<p class="everr">' + E(state.err) + "</p>" : "";
    var notice = state.notice ? '<p class="evnote">' + E(state.notice) + "</p>" : "";
    var lastErr = (last && last.error)
      ? '<p class="everr">Last run reported: ' + E(last.error) + "</p>" : "";

    var foot = '<p class="evfoot">Results live in ' +
      "<span>~/.hermes/dashboard/evals.db</span> (0600) and never leave this Mac. The " +
      "model Drill and its N/6 badge are untouched — this suite keeps its own history " +
      "so a scheduled run can never overwrite a drill result.</p>";

    return head + '<div class="body">' + lede + stats + chart + cases + lastErr +
      bar + scheduleHTML(cfg, d) + notice + err + foot + "</div>";
  }

  // ---- data ----------------------------------------------------------------
  async function jget(url) {
    try {
      var r = await fetch(url, { cache: "no-store" });
      return await r.json();
    } catch (e) { return null; }
  }
  async function jpost(url, body) {
    try {
      var r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {})
      });
      return await r.json();
    } catch (e) { return null; }
  }

  async function load() {
    var j = await jget("/api/evals");
    if (!j) {
      S.err = "The dashboard did not answer /api/evals.";
      S.loaded = true;
      return;
    }
    S.data = j;
    S.err = (j.ok === false) ? String(j.error || "Could not read the eval suite.") : "";
    S.loaded = true;
  }

  var pollTimer = null;

  function stopPoll() {
    if (pollTimer && typeof W.clearTimeout === "function") W.clearTimeout(pollTimer);
    pollTimer = null;
  }

  function poll() {
    stopPoll();
    if (typeof W.setTimeout !== "function") return;
    pollTimer = W.setTimeout(async function () {
      await load();
      paint();
      if (S.data && S.data.running) poll();
      else { S.busy = false; paint(); }
    }, 5000);
  }

  async function runNow(force) {
    S.busy = true;
    S.err = "";
    S.notice = "";
    paint();
    var j = await jpost("/api/evals/run", force ? { force: true } : {});
    if (!j || j.ok === false) {
      S.busy = false;
      S.err = (j && (j.error || j.reason)) || "Could not start the run.";
      paint();
      return;
    }
    S.notice = j.note || "";
    poll();
  }

  async function save(patch) {
    if (S.saving) return;
    S.saving = true;
    var j = await jpost("/api/evals/settings", patch);
    S.saving = false;
    if (j && j.settings && S.data) {
      S.data.settings = j.settings;
      S.err = "";
    } else {
      S.err = (j && j.error) || "Could not save the schedule.";
    }
    paint();
  }

  // ---- mount ---------------------------------------------------------------
  function wire(card) {
    if (!card || !card.querySelectorAll) return;

    Array.prototype.slice.call(card.querySelectorAll(".evseg b")).forEach(function (b) {
      b.onclick = function () {
        var n = +b.getAttribute("data-days");
        if (n && n !== S.days) { S.days = n; paint(); }
      };
    });

    Array.prototype.slice.call(card.querySelectorAll("button.evsw")).forEach(function (b) {
      b.onclick = function () {
        if (b.disabled) return;
        var k = b.getAttribute("data-act");
        var on = b.getAttribute("aria-checked") !== "true";
        b.setAttribute("aria-checked", on ? "true" : "false");   // optimistic
        var patch = {}; patch[k] = on;
        save(patch);
      };
    });

    var t = card.querySelector('input[data-k="time"]');
    if (t) t.onchange = function () {
      var p = String(t.value || "").split(":");
      var h = Math.max(0, Math.min(23, parseInt(p[0], 10) || 0));
      var m = Math.max(0, Math.min(59, parseInt(p[1], 10) || 0));
      // write the CLAMPED value back into the field — the server clamps too,
      // and a field still showing a rejected value is the watchtower bug.
      t.value = pad2(h) + ":" + pad2(m);
      save({ at_hour: h, at_minute: m });
    };

    var sel = card.querySelector('select[data-k="days"]');
    if (sel) sel.onchange = function () { save({ days: parseInt(sel.value, 10) || 1 }); };

    var b = card.querySelector('button[data-act="run"]');
    if (b) b.onclick = function () { runNow(false); };
  }

  var relocatedOnce = false;

  function paint() {
    var d = D(); if (!d) return;
    var panel = d.getElementById(PANEL_ID);
    // No panel yet => wait. Deliberately NOT falling back to #view-mind: the
    // shell's relocator sends an id it does not know to sec-system, and the
    // eval suite belongs with the model, not with logs and backups.
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
    // never repaint under the user's cursor
    var focused = null;
    try {
      var a = d.activeElement;
      if (a && el.contains(a) && a.getAttribute) focused = a.getAttribute("data-k");
    } catch (e) {}
    try { el.innerHTML = cardHTML(S); } catch (e) { return; }
    wire(el);
    if (focused) {
      try {
        var again = el.querySelector('[data-k="' + focused + '"]');
        if (again && again.focus) again.focus();
      } catch (e) {}
    }
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
      paint();                       // the skeleton, so the panel is never empty
      try { await load(); }
      catch (e) { S.err = "Could not read the eval suite."; S.loaded = true; }
      loading = false;
    }
    paint();
    if (S.data && S.data.running && !pollTimer) { S.busy = true; poll(); }
  }

  // chain window.mindExtras exactly like the other aux settings cards
  var prev = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await mount(); } catch (e) {}
  };

  // headless-harness surface (also handy from the console). `seed` is the test
  // seam the screenshot harness uses: hand it a payload and it renders it
  // without ever touching /api/evals — or a model.
  W.hermesEvals = {
    cardHTML: cardHTML, CSS: CSS, chartHTML: chartHTML, casesHTML: casesHTML,
    scheduleHTML: scheduleHTML, bucketByDay: bucketByDay, niceMax: niceMax,
    num: num, ms: ms, when: when, clock: clock, hhmm12: hhmm12, tone: tone,
    rateTone: rateTone,
    mount: mount, paint: paint, runNow: runNow, state: S,
    seed: function (payload, days) {
      S.data = payload; S.loaded = true; S.err = ""; S.busy = false;
      if (days) S.days = days;
      paint();
      return S;
    },
    refresh: async function () { S.loaded = false; await mount(); }
  };
})();
