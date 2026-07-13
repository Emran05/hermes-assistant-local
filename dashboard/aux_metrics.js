// aux_metrics.js — Vitals strip (P1.5 Metrics Baseline).
//
// Auto-served at /aux_metrics.js.  Loaded AFTER /expand.js so it can wrap the
// existing Console poller (window.loadConsole) instead of editing index.html.
// Prepends one dense "Vitals" card (#vitals-card) into #view-console showing
// TTFT, turn latency, hub API latency, RAM envelope, est tok/s, approvals,
// undo, and last model load — each with target badges — plus a 24h/7d toggle.
//
// Reuses index.html globals (esc, animate [Motion One], REDUCE), all typeof-
// guarded so the headless render harness never throws.  Values pass through
// fmt* helpers that coerce/guard null, so the esc-on-number throw class can't
// fire.  Zero emoji, bespoke two-tone SVG only, 12-hour time — per CLAUDE.md.

(function () {
  "use strict";

  // ---- self-hook: chain onto the existing Console poller --------------------
  var prevLoad = window.loadConsole;
  window.loadConsole = async function () {
    if (typeof prevLoad === "function") { try { await prevLoad(); } catch (e) {} }
    try { vitalsMaybeRefresh(false); } catch (e) {}
  };

  // ---- tiny helpers ---------------------------------------------------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
  function doc() { return (typeof document !== "undefined") ? document : null; }
  function RM() {
    if (typeof REDUCE !== "undefined") return !!REDUCE;
    try { return !!(window.matchMedia && matchMedia("(prefers-reduced-motion:reduce)").matches); }
    catch (e) { return false; }
  }
  function LS() { try { return window.localStorage || null; } catch (e) { return null; } }
  function getWin() {
    var s = LS(); try { return (s && s.getItem("hermes_metrics_win")) || "24h"; }
    catch (e) { return "24h"; }
  }
  function setWin(w) { var s = LS(); if (s) try { s.setItem("hermes_metrics_win", w); } catch (e) {} }

  // duration: >=1s shown as seconds, else ms; null/NaN -> em-dash
  function fmtDur(ms) {
    var n = Number(ms);
    if (ms == null || !isFinite(n)) return "—";
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "s";
    return Math.round(n) + "ms";
  }
  function fmtGb(v) {
    var n = Number(v);
    if (v == null || !isFinite(n)) return "—";
    return n.toFixed(1) + "GB";
  }
  function fmtNum(v, d) {
    var n = Number(v);
    if (v == null || !isFinite(n)) return "—";
    return (d != null) ? n.toFixed(d) : String(n);
  }
  // absolute 12-hour clock, e.g. "9:41 PM"
  function t12(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) return "";
    var dt = new Date(n * 1000), h = dt.getHours(), m = dt.getMinutes();
    var ap = h >= 12 ? "PM" : "AM";
    h = h % 12; if (h === 0) h = 12;
    return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
  }
  // badge tone from a value against green/amber ceilings
  function tone(v, green, amber) {
    var n = Number(v);
    if (v == null || !isFinite(n)) return "";
    if (n <= green) return "ok";
    if (n <= amber) return "warn";
    return "bad";
  }

  var GAUGE_SVG =
    '<svg class="ic vt-gauge" viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M3 13a9 9 0 0 1 18 0" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round"/>' +
    '<path d="M3 13a9 9 0 0 1 6.4-8.6" fill="none" stroke="var(--iris)" ' +
    'stroke-width="1.8" stroke-linecap="round"/>' +
    '<path d="M12 13l4-2.5" fill="none" stroke="currentColor" stroke-width="1.6" ' +
    'stroke-linecap="round"/>' +
    '<circle cx="12" cy="13" r="1.7" fill="color-mix(in srgb,var(--iris) 55%,transparent)" ' +
    'stroke="currentColor" stroke-width="1.2"/></svg>';

  // ---- one-time CSS ---------------------------------------------------------
  function injectCss() {
    var d = doc(); if (!d || d.getElementById("vitals-css")) return;
    var s = d.createElement("style");
    s.id = "vitals-css";
    s.textContent = [
      "#vitals-card .vt-gauge{color:var(--muted)}",
      ".vt-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}",
      ".vt-sub{font-size:11.5px;color:var(--muted);margin:-2px 0 10px}",
      ".vt-sub b{color:var(--warn)}",
      ".vt-seg{margin-left:auto;display:inline-flex;padding:2px;border-radius:9px;",
      "background:var(--glass-2);border:1px solid var(--hairline)}",
      ".vt-seg b{font-weight:560;font-size:11px;color:var(--muted);padding:3px 10px;",
      "border-radius:7px;cursor:pointer;user-select:none;transition:color .2s,background .2s}",
      ".vt-seg b.on{color:var(--ink);background:var(--glass);",
      "box-shadow:inset 0 1px 0 var(--specular),0 2px 8px -4px var(--cast)}",
      ".vt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:10px}",
      ".vt-tile{padding:10px 12px;border-radius:12px;background:var(--glass-2);",
      "border:1px solid var(--hairline);min-width:0}",
      ".vt-lbl{font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--faint);",
      "display:flex;align-items:center;gap:6px}",
      ".vt-dot{width:7px;height:7px;border-radius:99px;flex:0 0 auto;background:var(--faint)}",
      ".vt-dot.ok{background:var(--ok)}.vt-dot.warn{background:var(--warn)}.vt-dot.bad{background:var(--bad)}",
      ".vt-val{font-size:19px;font-weight:640;color:var(--ink);margin-top:3px;",
      "font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".vt-tsub{font-size:11px;color:var(--muted);margin-top:2px;",
      "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".vt-note{font-size:11px;color:var(--faint);margin-top:10px}",
      ".vt-note.err{color:var(--bad)}",
      ".vt-skel{height:64px;border-radius:12px;background:linear-gradient(90deg,",
      "var(--glass-2),var(--glass),var(--glass-2));background-size:200% 100%;",
      "animation:vtsh 1.3s linear infinite}",
      "@keyframes vtsh{0%{background-position:200% 0}100%{background-position:-200% 0}}",
      "@media (prefers-reduced-motion:reduce){.vt-skel{animation:none}}",
    ].join("\n");
    (d.head || d.body || d.documentElement).appendChild(s);
  }

  // ---- card mount (built once, reused) -------------------------------------
  function ensureCard() {
    var d = doc(); if (!d) return null;
    var host = d.getElementById("view-console");
    if (!host) return null;
    var card = d.getElementById("vitals-card");
    if (card) return card;
    injectCss();
    card = d.createElement("section");
    card.className = "card glass";
    card.id = "vitals-card";
    card.style.gridColumn = "1/3";
    card.innerHTML =
      '<h2 class="vt-head">' + GAUGE_SVG + "Vitals" +
      '<span class="vt-seg" id="vt-seg">' +
      '<b data-win="24h">24h</b><b data-win="7d">7d</b></span></h2>' +
      '<div class="body">' +
      '<div class="vt-sub" id="vt-sub"></div>' +
      '<div class="vt-grid" id="vt-grid">' +
      '<div class="vt-skel"></div><div class="vt-skel"></div>' +
      '<div class="vt-skel"></div><div class="vt-skel"></div>' +
      '<div class="vt-skel"></div><div class="vt-skel"></div>' +
      '<div class="vt-skel"></div><div class="vt-skel"></div></div>' +
      '<div class="vt-note" id="vt-note" hidden></div></div>';
    if (host.insertBefore) host.insertBefore(card, host.firstChild);
    else host.appendChild(card);
    wireToggle(card);
    return card;
  }

  function wireToggle(card) {
    var seg = card.querySelector ? card.querySelector("#vt-seg") : null;
    if (!seg) return;
    paintToggle(seg);
    var bs = seg.querySelectorAll ? seg.querySelectorAll("b[data-win]") : [];
    Array.prototype.slice.call(bs).forEach(function (b) {
      b.addEventListener("click", function () {
        var w = b.getAttribute("data-win");
        if (w === getWin()) return;
        setWin(w); paintToggle(seg);
        vitalsMaybeRefresh(true);
      });
    });
  }
  function paintToggle(seg) {
    var cur = getWin();
    var bs = seg.querySelectorAll ? seg.querySelectorAll("b[data-win]") : [];
    Array.prototype.slice.call(bs).forEach(function (b) {
      if (b.getAttribute("data-win") === cur) b.classList.add("on");
      else b.classList.remove("on");
    });
  }

  // ---- render ---------------------------------------------------------------
  function tiles(data) {
    var t = data.turns || {}, hub = data.hub_api || {}, ram = data.ram || {},
        model = data.model || {}, ap = data.approvals || {}, undo = data.undo || {},
        tg = data.targets || {};
    var ttft = t.ttft_ms || {}, turn = t.turn_ms || {}, tps = t.est_tok_per_sec || {};
    var last = ram.last || null;

    // RAM target: MoE (30B A3B) gets the 20GB ceiling, dense 8B the 6GB one
    var moe = /A3B/i.test(String(model.active || ""));
    var ramTgt = moe ? (tg.moe_idle_gb || 20) : (tg.idle_gb || 6);
    var ramVal, ramSub, ramTone;
    if (last && last.state === "paused") {
      ramVal = "—"; ramSub = "model paused"; ramTone = "";
    } else {
      ramVal = last ? (fmtGb(last.gb) + (last.state ? " · " + last.state : "")) : "—";
      ramSub = "idle p95 " + fmtGb(ram.idle_gb_p95) + " · target ≤" + ramTgt + "GB";
      ramTone = last ? tone(last.gb, ramTgt, ramTgt * 1.5) : "";
    }

    var ll = model.last_load || null;

    return [
      { lbl: "TTFT", val: fmtDur(ttft.p50),
        sub: "p95 " + fmtDur(ttft.p95) + " · target <1.5s",
        dot: tone(ttft.p50, tg.ttft_p50_ms || 1500, (tg.ttft_p50_ms || 1500) * 1.5) },
      { lbl: "Turn", val: fmtDur(turn.p50), sub: "p95 " + fmtDur(turn.p95), dot: "" },
      { lbl: "Hub API", val: fmtDur(hub.p95),
        sub: "p50 " + fmtDur(hub.p50) + " · target <100ms",
        dot: tone(hub.p95, tg.hub_p95_ms || 100, (tg.hub_p95_ms || 100) * 1.5) },
      { lbl: "RAM", val: ramVal, sub: ramSub, dot: ramTone },
      { lbl: "Tok/s (est)", val: fmtNum(tps.p50, 1), sub: "estimated · chars/4", dot: "" },
      { lbl: "Approvals", val: fmtNum(ap.requested),
        sub: fmtNum(ap.approved) + " approved · " + fmtNum(ap.denied) + " denied", dot: "" },
      { lbl: "Undo", val: fmtNum(undo.count != null ? undo.count : 0),
        sub: "lifetime", dot: "" },
      { lbl: "Model load", val: ll ? (fmtDur(ll.ms) + " · " + E(ll.trigger || "")) : "—",
        sub: ll ? t12(ll.ts) : "no load recorded", dot: "" },
    ];
  }

  function render(card, data) {
    var grid = card.querySelector ? card.querySelector("#vt-grid") : null;
    var sub = card.querySelector ? card.querySelector("#vt-sub") : null;
    var note = card.querySelector ? card.querySelector("#vt-note") : null;
    if (!grid) return;

    var t = data.turns || {};
    var empty = !(Number(t.n) > 0);

    if (sub) {
      if (empty) {
        sub.innerHTML = "No turns recorded yet — send a chat message to log the first TTFT.";
      } else {
        sub.innerHTML = E(t.n + " turn" + (t.n === 1 ? "" : "s") +
          (t.err ? " · " + t.err + " error" + (t.err === 1 ? "" : "s") : "") +
          " · " + (getWin() === "7d" ? "last 7 days" : "last 24h"));
      }
    }

    grid.innerHTML = tiles(data).map(function (x) {
      return '<div class="vt-tile">' +
        '<div class="vt-lbl">' + (x.dot ? '<span class="vt-dot ' + x.dot + '"></span>' : "") +
        E(x.lbl) + "</div>" +
        '<div class="vt-val">' + E(x.val) + "</div>" +
        '<div class="vt-tsub">' + E(x.sub) + "</div></div>";
    }).join("");

    if (note) {
      if (data.persist_error) {
        note.hidden = false; note.className = "vt-note";
        note.textContent = "in-memory only — persistence error (" + String(data.persist_error) + ")";
      } else {
        note.hidden = true; note.textContent = "";
      }
    }

    if (!RM() && typeof animate === "function") {
      try { animate(grid, { opacity: [0.4, 1], transform: ["translateY(2px)", "none"] }, { duration: 0.3 }); }
      catch (e) {}
    }
  }

  function markError(card, data) {
    if (!card) return;
    var note = card.querySelector ? card.querySelector("#vt-note") : null;
    var grid = card.querySelector ? card.querySelector("#vt-grid") : null;
    // keep the last good tile render; only surface a hairline error line
    if (grid && grid.querySelector && grid.querySelector(".vt-skel")) {
      // never rendered yet -> show em-dashes so the card isn't a skeleton forever
      render(card, { turns: { n: 0 }, targets: {} });
    }
    if (note) {
      note.hidden = false; note.className = "vt-note err";
      note.textContent = (data && data.error) ? ("metrics unavailable — " + String(data.error))
        : "metrics unavailable";
    }
  }

  // ---- fetch (throttled to one call per 15s) --------------------------------
  var _vtLast = 0, _vtBusy = false;
  function vitalsMaybeRefresh(force) {
    var now = Date.now();
    if (!force && now - _vtLast < 15000) return;
    _vtLast = now;
    vitalsRefresh().catch(function () {});
  }

  async function vitalsRefresh() {
    if (_vtBusy) return;
    _vtBusy = true;
    try {
      var card = ensureCard();
      if (!card) return;
      var win = getWin();
      var data;
      try {
        var r = await fetch("/api/metrics?days=" + (win === "7d" ? 7 : 1), { cache: "no-store" });
        if (r.status === 404) { if (card.remove) card.remove(); return; }
        data = await r.json();
      } catch (e) { markError(card); return; }
      if (!data || data.ok === false) { markError(card, data); return; }
      render(card, data);
    } finally { _vtBusy = false; }
  }

  // first paint if the Console is the restored view on load
  try {
    var vc = doc() && doc().getElementById("view-console");
    if (vc && !vc.hidden) vitalsMaybeRefresh(true);
  } catch (e) {}

  // expose for the headless render harness / manual invocation
  window.vitalsRefresh = vitalsRefresh;
})();
