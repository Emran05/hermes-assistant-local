// aux_mind_drill.js — P2.6a: multi-day drill-downs for the Mind analytics.
//
// Auto-served at /aux_mind_drill.js.  Loaded AFTER /expand.js so it wraps the
// existing Mind-extras entry point (window.mindExtras) — same chain pattern as
// aux_trust/aux_config — instead of editing index.html.  It enhances the two
// EXISTING Mind analytics cards mindExtras builds:
//   * #mind-extra-fuel   (stacked in/out token bars)  -> 14d/30d/60d toggle +
//                        a "Busiest day" callout (from /api/mind_drill)
//   * #mind-extra-models (model-mix donut)            -> 14d/30d/60d toggle
// Picking a range fetches /api/mind_drill?days=N and re-renders the chart in
// place with the exact same SVG idioms mindExtras uses (mx-chart stacked bars,
// mx-arc donut).  14d restores the original mind_extra render (snapshot).
// States: loading shimmer, error hint with retry.  Reduced-motion guarded.
//
// Reuses index.html globals (esc, kfmt, REDUCE) — all typeof-guarded so a
// headless render harness never throws.  CLAUDE.md laws: zero emoji, bespoke
// SVG only, 12-hour time (no clock times shown here — dates only), density.

(function () {
  "use strict";

  // ---- self-hook: chain onto the existing Mind-extras entry point ----------
  if (typeof window !== "undefined") {
    var prev = window.mindExtras;
    window.mindExtras = async function () {
      if (typeof prev === "function") { try { await prev(); } catch (e) {} }
      try { await drillEnhance(); } catch (e) {}
    };
  }

  // ---- tiny guarded helpers -------------------------------------------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
  function KF(n) {
    if (typeof kfmt === "function") return kfmt(n || 0);
    n = +n || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
    return "" + n;
  }
  function RM() {
    if (typeof REDUCE !== "undefined") return !!REDUCE;
    try { return !!(window.matchMedia && matchMedia("(prefers-reduced-motion:reduce)").matches); }
    catch (e) { return false; }
  }
  function doc() { return (typeof document !== "undefined") ? document : null; }
  var MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function mdLabel(iso) {           // "2026-07-03" -> "Jul 3"
    var p = String(iso || "").split("-");
    var m = MN[(+p[1]) - 1];
    return m ? (m + " " + (+p[2])) : String(iso || "");
  }

  // ---- module state -----------------------------------------------------------
  var SEL = { fuel: 14, models: 14 };   // survives mindExtras re-renders
  var SNAP = {};                        // 14d snapshots {body, tiny}
  var CACHE = {};                       // days -> {t, data} (server caches 300s too)
  var CACHE_MS = 120000;

  async function fetchDrill(days) {
    var hit = CACHE[days];
    if (hit && Date.now() - hit.t < CACHE_MS) return hit.data;
    var r = await fetch("/api/mind_drill?days=" + days, { cache: "no-store" });
    var d = await r.json();
    if (!d || d.error) throw new Error((d && d.error) || "empty response");
    CACHE[days] = { t: Date.now(), data: d };
    return d;
  }

  // ---- one-time CSS -----------------------------------------------------------
  function injectCss() {
    var d = doc(); if (!d || d.getElementById("mdr-css")) return;
    var s = d.createElement("style");
    s.id = "mdr-css";
    s.textContent = [
      ".mdr-seg{display:inline-flex;gap:2px;padding:2px;border-radius:9px;background:var(--glass-2);",
      "border:1px solid var(--hairline);flex:0 0 auto;margin-left:8px}",
      ".mdr-seg b{font-size:9.5px;font-weight:620;color:var(--muted);padding:2px 7px;border-radius:7px;",
      "cursor:pointer;user-select:none;letter-spacing:.02em;transition:color .15s,background .15s}",
      ".mdr-seg b.on{color:var(--ink);background:var(--glass);box-shadow:inset 0 1px 0 var(--specular)}",
      ".mdr-callout{display:flex;align-items:center;gap:8px;margin-top:9px;padding:6px 10px;border-radius:10px;",
      "background:color-mix(in srgb,var(--iris) 9%,transparent);",
      "border:1px solid color-mix(in srgb,var(--iris) 22%,transparent);font-size:11.5px;color:var(--muted)}",
      ".mdr-callout b{color:var(--ink);font-weight:620}",
      ".mdr-callout svg{width:13px;height:13px;flex:0 0 auto;color:var(--iris);fill:none;",
      "stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}",
      ".mdr-skel{height:34px;border-radius:9px;margin:7px 0;",
      "background:linear-gradient(90deg,var(--glass-2),var(--glass),var(--glass-2));",
      "background-size:200% 100%;animation:mdrsh 1.3s linear infinite}",
      "@keyframes mdrsh{0%{background-position:200% 0}100%{background-position:-200% 0}}",
      "@media (prefers-reduced-motion:reduce){.mdr-skel{animation:none}.mdr-seg b{transition:none}}",
    ].join("\n");
    (d.head || d.body || d.documentElement).appendChild(s);
  }

  // bolt glyph for the busiest-day callout (bespoke SVG, no emoji)
  var BOLT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6z"/></svg>';

  function calloutHtml(bd) {
    if (!bd || !bd.d) return "";
    return '<div class="mdr-callout">' + BOLT +
      "<span>Busiest day <b>" + E(mdLabel(bd.d)) + "</b> — <b class=\"num\">" +
      KF(bd.tokens || 0) + "</b> tokens · <b class=\"num\">" + (bd.sessions || 0) +
      "</b> session" + (bd.sessions === 1 ? "" : "s") + "</span></div>";
  }

  // ---- renderers (same SVG idioms as mindExtras in expand.js) ----------------

  // stacked in/out token bars over N days
  function renderFuel(card, d) {
    var body = card.querySelector(".body"), tiny = card.querySelector("h2 .tiny");
    if (!body) return;
    var days = d.tokens_by_day || [];
    var sess = {};
    (d.sessions_by_day || []).forEach(function (x) { sess[x.d] = x.n || 0; });
    var tin = 0, tout = 0;
    days.forEach(function (x) { tin += x.in_tok || 0; tout += x.out_tok || 0; });
    var html;
    if (days.length && (tin + tout) > 0) {
      var W = 620, H = 148, padL = 38, padR = 8, padT = 10, padB = 22;
      var plotW = W - padL - padR, plotH = H - padT - padB, y0 = padT + plotH;
      var n = days.length, slot = plotW / n;
      var barW = Math.max(2, Math.min(24, Math.floor(slot - (n > 20 ? 2 : 8))));
      var rawMax = 1;
      days.forEach(function (x) { rawMax = Math.max(rawMax, (x.in_tok || 0) + (x.out_tok || 0)); });
      var p = Math.pow(10, Math.floor(Math.log10(rawMax))), mmul = rawMax / p;
      var niceMax = (mmul <= 1 ? 1 : mmul <= 1.5 ? 1.5 : mmul <= 2 ? 2 : mmul <= 2.5 ? 2.5 :
        mmul <= 3 ? 3 : mmul <= 4 ? 4 : mmul <= 5 ? 5 : mmul <= 7.5 ? 7.5 : 10) * p;
      var roundTop = function (x, y, w, h, r) {
        r = Math.min(r, h / 2, w / 2);
        return '<path d="M' + x + " " + (y + h) + "v" + (-(h - r)) + "a" + r + " " + r +
          " 0 0 1 " + r + " " + (-r) + "h" + (w - 2 * r) + "a" + r + " " + r + " 0 0 1 " +
          r + " " + r + "v" + (h - r) + 'z"';
      };
      var labEvery = Math.max(1, Math.ceil(n / 13));      // keep x-axis legible at 30/60d
      var dly = Math.min(40, Math.max(6, Math.round(620 / n)));
      var svg = '<svg class="mx-chart" viewBox="0 0 ' + W + " " + H +
        '" preserveAspectRatio="xMidYMid meet" width="100%" aria-hidden="true">';
      [0, 0.5, 1].forEach(function (f) {
        var gy = y0 - f * plotH;
        svg += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy +
          '" stroke="var(--hairline)" stroke-width="1"/>';
        if (f > 0) svg += '<text x="' + (padL - 5) + '" y="' + (gy + 3) +
          '" text-anchor="end" class="mx-tick num">' + KF(niceMax * f) + "</text>";
      });
      days.forEach(function (x, i) {
        var cx = padL + slot * i + slot / 2, bx = cx - barW / 2;
        var hIn = (x.in_tok || 0) / niceMax * plotH, hOut = (x.out_tok || 0) / niceMax * plotH;
        var parts = String(x.d || "").split("-"), lab = (+parts[1]) + "/" + (+parts[2]);
        var ns = sess[x.d] || 0;
        var tip = mdLabel(x.d) + " · " + KF(x.in_tok || 0) + " in · " + KF(x.out_tok || 0) +
          " out · " + ns + " session" + (ns === 1 ? "" : "s");
        svg += '<g class="mx-grow" style="animation-delay:' + (i * dly) + 'ms"><title>' + E(tip) + "</title>";
        if (hIn < 0.5 && hOut < 0.5) {
          svg += '<rect x="' + bx + '" y="' + (y0 - 2) + '" width="' + barW + '" height="2" fill="var(--hairline)"/>';
        } else if (hOut < 0.5) {
          svg += roundTop(bx, y0 - hIn, barW, hIn, 4) + ' fill="var(--iris)"/>';
        } else {
          // in (bottom, square baseline) + 2px surface gap + out (top, rounded data-end)
          if (hIn >= 0.5) svg += '<rect x="' + bx + '" y="' + (y0 - hIn) + '" width="' + barW +
            '" height="' + hIn + '" fill="var(--iris)"/>';
          svg += roundTop(bx, y0 - hIn - (hIn >= 0.5 ? 2 : 0) - hOut, barW, hOut, 4) + ' fill="var(--quick)"/>';
        }
        svg += "</g>";
        if (i % labEvery === 0) svg += '<text x="' + cx + '" y="' + (H - 7) +
          '" text-anchor="middle" class="mx-tick num">' + lab + "</text>";
      });
      svg += '<line x1="' + padL + '" y1="' + y0 + '" x2="' + (W - padR) + '" y2="' + y0 +
        '" stroke="var(--hairline)" stroke-width="1"/></svg>';
      html = '<div class="mx-legend"><span><i style="background:var(--iris)"></i>Tokens in</span>' +
        '<span><i style="background:var(--quick)"></i>Tokens out</span></div>' + svg;
    } else {
      html = '<div class="hint">No token usage recorded in the last ' + d.days + " days.</div>";
    }
    html += calloutHtml(d.busiest_day);
    body.innerHTML = html;
    if (tiny) tiny.textContent = d.days + " days · " + KF(tin) + " in · " + KF(tout) + " out";
  }

  // model-mix donut over N days
  function renderModels(card, d) {
    var body = card.querySelector(".body"), tiny = card.querySelector("h2 .tiny");
    if (!body) return;
    var COLS = ["var(--iris)", "var(--quick)", "var(--ok)", "var(--warn)", "var(--bad)"];
    var mm = (d.model_mix || []).slice();
    if (mm.length > 5) {
      var rest = 0;
      mm.slice(4).forEach(function (m) { rest += m.sessions || 0; });
      mm = mm.slice(0, 4).concat([{ name: "Other", sessions: rest }]);
    }
    var mTot = 0;
    mm.forEach(function (m) { mTot += m.sessions || 0; });
    var html;
    if (mTot > 0) {
      var R = 46, C = 2 * Math.PI * R, GAP = (mm.length > 1 ? 2.5 : 0);
      var acc = 0, arcs = "", rm = RM();
      mm.forEach(function (m, i) {
        var frac = (m.sessions || 0) / mTot;
        var len = Math.max(0, frac * C - GAP);
        arcs += '<circle class="mx-arc" cx="63" cy="63" r="' + R + '" fill="none" stroke="' +
          COLS[i % COLS.length] + '" stroke-width="13" stroke-dasharray="' +
          (rm ? (len + " " + (C - len)) : ("0 " + C)) + '" data-dash="' + len + " " + (C - len) +
          '" stroke-dashoffset="' + (-acc * C + C * 0.25) + '" style="transition-delay:' + (i * 110) + 'ms">' +
          "<title>" + E(m.name) + " · " + (m.sessions || 0) + " sessions (" + Math.round(frac * 100) + "%)</title></circle>";
        acc += frac;
      });
      html = '<div class="mx-donutwrap">' +
        '<svg class="mx-donut" viewBox="0 0 126 126" aria-hidden="true">' +
        '<circle cx="63" cy="63" r="' + R + '" fill="none" stroke="var(--hairline)" stroke-width="13"/>' + arcs +
        '<text x="63" y="60" text-anchor="middle" class="mx-ctr num">' + mTot + "</text>" +
        '<text x="63" y="74" text-anchor="middle" class="mx-ctrsub">sessions</text></svg>' +
        '<div class="mx-mlegend">' + mm.map(function (m, i) {
          return '<div class="mx-mrow"><i class="mx-dot" style="background:' + COLS[i % COLS.length] + '"></i>' +
            '<span class="mx-mname" title="' + E(m.name) + '">' + E(m.name) + "</span>" +
            '<span class="mx-n num">' + (m.sessions || 0) + "</span>" +
            '<span class="mx-pct num">' + Math.round((m.sessions || 0) / mTot * 100) + "%</span></div>";
        }).join("") + "</div></div>";
    } else {
      html = '<div class="hint">No sessions in the last ' + d.days + " days.</div>";
    }
    body.innerHTML = html;
    if (tiny) tiny.textContent = "last " + d.days + " days";
    if (mTot > 0 && !RM() && typeof requestAnimationFrame === "function") {
      requestAnimationFrame(function () { requestAnimationFrame(function () {
        var arcs2 = body.querySelectorAll(".mx-arc");
        for (var i = 0; i < arcs2.length; i++) arcs2[i].style.strokeDasharray = arcs2[i].dataset.dash;
      }); });
    }
  }

  var RENDERERS = { fuel: renderFuel, models: renderModels };
  var CARD_ID = { fuel: "mind-extra-fuel", models: "mind-extra-models" };

  // ---- range switch ----------------------------------------------------------
  function setSegOn(card, days) {
    var bs = card.querySelectorAll(".mdr-seg b");
    for (var i = 0; i < bs.length; i++) {
      if (+bs[i].getAttribute("data-days") === days) bs[i].classList.add("on");
      else bs[i].classList.remove("on");
    }
  }

  async function applyRange(kind, days) {
    var d = doc(); if (!d) return;
    var card = d.getElementById(CARD_ID[kind]);
    if (!card) return;
    SEL[kind] = days;
    setSegOn(card, days);
    var body = card.querySelector(".body");
    if (!body) return;

    if (days === 14 && SNAP[kind]) {
      // restore the original mind_extra 14-day render
      body.innerHTML = SNAP[kind].body;
      var tiny = card.querySelector("h2 .tiny");
      if (tiny) tiny.textContent = SNAP[kind].tiny;
      if (kind === "fuel") {
        // keep the busiest-day callout in the restored view (from cache; quiet on miss)
        try {
          var d14 = await fetchDrill(14);
          if (SEL.fuel === 14) body.insertAdjacentHTML("beforeend", calloutHtml(d14.busiest_day));
        } catch (e) {}
      } else if (!RM() && typeof requestAnimationFrame === "function") {
        // replay the donut sweep on the restored arcs
        var arcs = body.querySelectorAll(".mx-arc");
        for (var i = 0; i < arcs.length; i++) arcs[i].style.strokeDasharray = "0 " + (2 * Math.PI * 46);
        requestAnimationFrame(function () { requestAnimationFrame(function () {
          for (var j = 0; j < arcs.length; j++) arcs[j].style.strokeDasharray = arcs[j].dataset.dash;
        }); });
      }
      return;
    }

    // loading shimmer while we fetch the window
    body.innerHTML = '<div class="mdr-skel"></div><div class="mdr-skel"></div><div class="mdr-skel"></div>';
    var data;
    try { data = await fetchDrill(days); }
    catch (e) {
      if (SEL[kind] !== days) return;
      body.innerHTML = '<div class="hint">Couldn’t load the ' + days + "-day view. " +
        '<button class="ghost" data-mdr-retry="1" style="margin-left:6px">Retry</button></div>';
      var rb = body.querySelector("[data-mdr-retry]");
      if (rb) rb.addEventListener("click", function () { applyRange(kind, days).catch(function () {}); });
      return;
    }
    if (SEL[kind] !== days) return;    // user already switched again
    RENDERERS[kind](card, data);
  }

  // ---- enhancement pass (runs after every mindExtras render) -----------------
  function addSeg(card, kind) {
    var h2 = card.querySelector("h2");
    if (!h2 || h2.querySelector(".mdr-seg")) return;
    var d = doc(); if (!d) return;
    var seg = d.createElement("span");
    seg.className = "mdr-seg";
    seg.innerHTML = [14, 30, 60].map(function (n) {
      return "<b data-days=\"" + n + "\"" + (SEL[kind] === n ? ' class="on"' : "") + ">" + n + "d</b>";
    }).join("");
    var bs = seg.querySelectorAll("b");
    for (var i = 0; i < bs.length; i++) {
      (function (b) {
        b.addEventListener("click", function () {
          var n = +b.getAttribute("data-days");
          if (n !== SEL[kind]) applyRange(kind, n).catch(function () {});
        });
      })(bs[i]);
    }
    h2.appendChild(seg);
  }

  async function drillEnhance() {
    var d = doc(); if (!d) return;
    injectCss();
    var kinds = ["fuel", "models"], found = false;
    kinds.forEach(function (kind) {
      var card = d.getElementById(CARD_ID[kind]);
      if (!card) return;
      found = true;
      // snapshot the fresh 14-day render (before any callout is appended)
      var body = card.querySelector(".body"), tiny = card.querySelector("h2 .tiny");
      if (body) SNAP[kind] = { body: body.innerHTML, tiny: tiny ? tiny.textContent : "" };
      addSeg(card, kind);
    });
    if (!found) return;

    // busiest-day callout on the 14d fuel card, or re-apply a sticky range
    var jobs = [];
    kinds.forEach(function (kind) {
      var card = d.getElementById(CARD_ID[kind]);
      if (!card) return;
      if (SEL[kind] !== 14) {
        jobs.push(applyRange(kind, SEL[kind]).catch(function () {}));
      } else if (kind === "fuel") {
        jobs.push(fetchDrill(14).then(function (d14) {
          var body = card.querySelector(".body");
          if (body && SEL.fuel === 14 && !body.querySelector(".mdr-callout")) {
            body.insertAdjacentHTML("beforeend", calloutHtml(d14.busiest_day));
          }
        }).catch(function () {}));      // quiet: callout is an extra, not a state
      }
    });
    await Promise.all(jobs);
  }

  // expose for the headless render harness / manual invocation
  if (typeof window !== "undefined") {
    window.mindDrill = { enhance: drillEnhance, apply: applyRange,
                         renderFuel: renderFuel, renderModels: renderModels };
  }
})();
