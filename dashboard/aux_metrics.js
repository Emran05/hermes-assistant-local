// aux_metrics.js — Vitals strip (P1.5 Metrics Baseline).
//
// Auto-served at /aux_metrics.js.  Loaded AFTER /expand.js so it can wrap the
// existing Console poller (window.loadConsole) instead of editing index.html.
// Mounts one dense "Vitals" card (#vitals-card) at the top of Settings ›
// Insights (#view-console, its first home, is no longer shown anywhere) showing
// TTFT, turn latency, hub API latency, RAM envelope, est tok/s, approvals,
// undo, and last model load — each with target badges — plus a 24h/7d toggle.
//
// Reuses index.html globals (esc, animate, HA, REDUCE), all typeof-guarded so
// the headless render harness never throws.  Values pass through fmt* helpers
// that coerce/guard null, so the esc-on-number throw class can't fire.  Zero
// emoji, bespoke two-tone SVG only, 12-hour time — per CLAUDE.md.
//
// Motion (docs/plans/motion-system-2026-10.md §8.11): the headline numbers
// count up (HermesAnim.count) and the grid gives one soft pulse when the card
// first fills and when the owner switches 24h/7d. The polls (the Console's
// 3 s loadConsole, throttled to 15 s, and every Settings re-entry) repaint in
// place with no motion at all — they used to pulse the grid every time.

(function () {
  "use strict";

  // ---- self-hook: chain onto the existing Console poller --------------------
  var prevLoad = window.loadConsole;
  window.loadConsole = async function () {
    if (typeof prevLoad === "function") { try { await prevLoad(); } catch (e) {} }
    try { vitalsMaybeRefresh(false); } catch (e) {}
  };
  // ...and onto the Settings render (index.html's loadMind -> mindExtras), which
  // is where the card lives now: the Console view folded into the Agent page's
  // Record rail, nothing ever shows #view-console, and the card it was
  // prepended into was unreachable (B26).
  var prevMind = window.mindExtras;
  window.mindExtras = async function () {
    if (typeof prevMind === "function") { try { await prevMind(); } catch (e) {} }
    // re-home first, unthrottled: a Settings view restored at load paints
    // before the settings shell exists, so that first card lands in the
    // hidden #view-console and the 15 s throttle below would leave it there
    try { ensureCard(); } catch (e) {}
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
  // ---- motion helpers ---------------------------------------------------------
  // index.html's HA() is window.HermesAnim, or ANIM_NULL without aux_anim.js
  function ha() {
    try { if (typeof HA === "function") return HA(); } catch (e) {}
    return (typeof window !== "undefined" && window.HermesAnim) || null;
  }
  // A counting number is formatted in its FINAL value's unit on every frame
  // (a 1.4s TTFT counts "0.0s" ... "1.4s", never "999ms" on the way), so it
  // keeps one width; at the final value it is exactly the tile's own text.
  function exactAt(n, text, f) { return function (v) { return v === n ? text : f(v); }; }
  function durIn(n) {
    var f = n >= 1000 ? function (v) { return (v / 1000).toFixed(n >= 10000 ? 0 : 1) + "s"; }
                      : function (v) { return Math.round(v) + "ms"; };
    return exactAt(n, fmtDur(n), f);
  }
  function numIn(n, d) {
    return exactAt(n, fmtNum(n, d), function (v) { return d != null ? v.toFixed(d) : String(Math.round(v)); });
  }
  // a countable tile value: a finite number >= 0, else null (an em-dash or a
  // composite like "12.3GB · loaded" is written as text and never moves)
  function cnum(v) { var n = Number(v); return (v != null && isFinite(n) && n >= 0) ? n : null; }

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
      ".vt-sub{font-size:12px;color:var(--muted);margin:-2px 0 10px}",
      ".vt-sub b{color:var(--warn)}",
      ".vt-seg{margin-left:auto;display:inline-flex;padding:2px;border-radius:9px;",
      "background:var(--glass-2);border:1px solid var(--hairline)}",
      ".vt-seg b{font-weight:560;font-size:12px;color:var(--muted);padding:3px 10px;",
      "border-radius:7px;cursor:pointer;user-select:none;transition:color .2s,background .2s}",
      ".vt-seg b.on{color:var(--ink);background:var(--glass);",
      "box-shadow:inset 0 1px 0 var(--specular),0 2px 8px -4px var(--cast)}",
      // eight tiles, always (tiles()): four columns make two full rows; a
      // narrow card falls to two columns (four full rows). auto-fit used to
      // give 5 + 3 with two holes (V05).
      "#vitals-card>.body{container-type:inline-size}",
      ".vt-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}",
      "@container (max-width:460px){.vt-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}",
      ".vt-tile{padding:10px 12px;border-radius:12px;background:var(--glass-2);",
      "border:1px solid var(--hairline);min-width:0}",
      ".vt-lbl{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);",
      "display:flex;align-items:center;gap:6px}",
      ".vt-dot{width:7px;height:7px;border-radius:99px;flex:0 0 auto;background:var(--faint)}",
      ".vt-dot.ok{background:var(--ok)}.vt-dot.warn{background:var(--warn)}.vt-dot.bad{background:var(--bad)}",
      ".vt-val{font-size:19px;font-weight:640;color:var(--ink);margin-top:3px;",
      "font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;",
      "contain:layout paint}",
      // captions wrap (V05): the target a figure is judged against was cut
      // off by an ellipsis ("p95 — · target <1…")
      ".vt-tsub{font-size:12px;line-height:1.4;color:var(--muted);margin-top:2px;",
      "overflow-wrap:anywhere;text-wrap:pretty}",
      ".vt-note{font-size:12px;color:var(--muted);margin-top:10px}",
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
  // Settings > Insights (latency, RAM and throughput sit with usage and fuel);
  // #view-console only for a page without the settings shell.
  function cardHost(d) {
    var panel = d.getElementById("sec-insights");
    if (panel) return { el: panel.querySelector(".set-body") || panel, settings: true };
    var vc = d.getElementById("view-console");
    return vc ? { el: vc, settings: false } : null;
  }
  function ensureCard() {
    var d = doc(); if (!d) return null;
    var host = cardHost(d);
    if (!host) return null;
    var card = d.getElementById("vitals-card");
    if (card) {
      if (host.settings && card.parentNode !== host.el) mountIn(host, card);
      return card;
    }
    injectCss();
    card = d.createElement("section");
    card.className = "card glass" + (host.settings ? " set-legacy" : "");
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
    mountIn(host, card);
    wireToggle(card);
    return card;
  }
  function mountIn(host, card) {
    var el = host.el;
    if (host.settings) {
      // first card of the panel, the aux_network way: before the shell's
      // legacy-slot anchor, and order:-1 in the flex column
      card.style.order = "-1";
      if (card.classList) card.classList.add("set-legacy");
      var anchor = el.querySelector ? el.querySelector("[data-legacy-slot]") : null;
      if (anchor && anchor.parentNode) { anchor.parentNode.insertBefore(card, anchor); return; }
    }
    if (el.insertBefore) el.insertBefore(card, el.firstChild);
    else el.appendChild(card);
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
        vitalsMaybeRefresh(true, true);
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

    var undoN = undo.count != null ? undo.count : 0;
    // num + fmt: the headline numbers that count up (fmt(num) === val)
    return [
      { lbl: "TTFT", val: fmtDur(ttft.p50), num: cnum(ttft.p50), fmt: durIn,
        sub: "p95 " + fmtDur(ttft.p95) + " · target <1.5s",
        dot: tone(ttft.p50, tg.ttft_p50_ms || 1500, (tg.ttft_p50_ms || 1500) * 1.5) },
      { lbl: "Turn", val: fmtDur(turn.p50), num: cnum(turn.p50), fmt: durIn,
        sub: "p95 " + fmtDur(turn.p95), dot: "" },
      { lbl: "Hub API", val: fmtDur(hub.p95), num: cnum(hub.p95), fmt: durIn,
        sub: "p50 " + fmtDur(hub.p50) + " · target <100ms",
        dot: tone(hub.p95, tg.hub_p95_ms || 100, (tg.hub_p95_ms || 100) * 1.5) },
      { lbl: "RAM", val: ramVal, sub: ramSub, dot: ramTone },
      { lbl: "Tok/s (est)", val: fmtNum(tps.p50, 1), num: cnum(tps.p50),
        fmt: function (n) { return numIn(n, 1); }, sub: "estimated · chars/4", dot: "" },
      { lbl: "Approvals", val: fmtNum(ap.requested), num: cnum(ap.requested), fmt: numIn,
        sub: fmtNum(ap.approved) + " approved · " + fmtNum(ap.denied) + " denied", dot: "" },
      { lbl: "Undo", val: fmtNum(undoN), num: cnum(undoN), fmt: numIn,
        sub: "lifetime", dot: "" },
      { lbl: "Model load", val: ll ? (fmtDur(ll.ms) + " · " + E(ll.trigger || "")) : "—",
        sub: ll ? t12(ll.ts) : "no load recorded", dot: "" },
    ];
  }

  // the last number each headline tile showed, so a 24h/7d switch counts from
  // what was on screen (the tiles are rebuilt on every render)
  var VT_SHOWN = {};

  // moving: true only when the owner is looking at a change they caused (the
  // card's first fill, a 24h/7d switch); every other render is a poll
  function render(card, data, moving) {
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

    var ts = tiles(data);
    grid.innerHTML = ts.map(function (x) {
      return '<div class="vt-tile">' +
        '<div class="vt-lbl">' + (x.dot ? '<span class="vt-dot ' + x.dot + '"></span>' : "") +
        E(x.lbl) + "</div>" +
        '<div class="vt-val">' + E(x.val) + "</div>" +
        '<div class="vt-tsub">' + E(x.sub) + "</div></div>";
    }).join("");
    var prev = {}, k;                       // what was on screen before this paint
    for (k in VT_SHOWN) prev[k] = VT_SHOWN[k];
    // the card's first fill usually lands in a panel nobody has opened yet: its
    // count-up and pulse wait for the first time the grid is on screen
    var wait = false;
    if (VT_WAIT && VT_WAIT.grid !== grid) {            // the card was rebuilt: that wait is for a grid that is gone
      try { VT_WAIT.io.disconnect(); } catch (e) {}
      VT_WAIT = null;
    }
    if (VT_WAIT) { VT_WAIT.ts = ts; wait = true; }
    else if (moving && offScreen(grid)) wait = awaitView(grid, ts, prev);
    countTiles(grid, ts, !!moving && !wait, prev);

    if (note) {
      if (data.persist_error) {
        note.hidden = false; note.className = "vt-note";
        note.textContent = "in-memory only — persistence error (" + String(data.persist_error) + ")";
      } else {
        note.hidden = true; note.textContent = "";
      }
    }

    if (moving && !wait) pulse(grid);
  }

  function pulse(grid) {
    if (!RM() && typeof animate === "function") {
      try { animate(grid, { opacity: [0.4, 1], transform: ["translateY(2px)", "none"] }, { duration: 0.3 }); }
      catch (e) {}
    }
  }

  // ---- first view ---------------------------------------------------------------
  // The card mounts into Settings › Insights, a panel that stays hidden until the
  // owner opens it, so its first fill usually happens off screen and a count-up
  // would play to nobody. While the grid has no box (and the browser can say when
  // it gets one) that first count-up and pulse wait for it, once. Polls that land
  // in the meantime only refresh the numbers the count will end on. Without an
  // IntersectionObserver nothing waits, and a count that never starts costs
  // nothing: the tiles already hold their final text.
  var VT_WAIT = null;                      // { io, grid, ts, prev } while the first view is awaited
  function offScreen(grid) {
    try {
      if (typeof IntersectionObserver !== "function" || !grid.getClientRects) return false;
      return grid.getClientRects().length === 0;
    } catch (e) { return false; }
  }
  function awaitView(grid, ts, prev) {
    try {
      var io = new IntersectionObserver(function (es) {
        if (!VT_WAIT || !es.some(function (e) { return e.isIntersecting; })) return;
        var w = VT_WAIT; VT_WAIT = null;
        try { io.disconnect(); } catch (e) {}
        countTiles(grid, w.ts, true, w.prev);
        pulse(grid);
      });
      io.observe(grid);
      VT_WAIT = { io: io, grid: grid, ts: ts, prev: prev };
      return true;
    } catch (e) { return false; }
  }

  // The headline numbers count up (HermesAnim.count): from 0 on the card's
  // first fill, from the value that was on screen on a 24h/7d switch. On a poll
  // the text the tiles were built with simply stands. Each render rebuilds the
  // tiles, so the start value comes from VT_SHOWN, never the element.
  function countTiles(grid, ts, moving, prev) {
    var H = moving ? ha() : null, vals = grid.querySelectorAll ? grid.querySelectorAll(".vt-val") : [];
    var k = 0;
    prev = prev || VT_SHOWN;
    ts.forEach(function (x, i) {
      if (x.num == null || typeof x.fmt !== "function") { delete VT_SHOWN[x.lbl]; return; }
      var from = prev[x.lbl] != null ? prev[x.lbl] : 0, el = vals[i];
      VT_SHOWN[x.lbl] = x.num;
      if (!H || typeof H.count !== "function" || !el || from === x.num) return;
      var f = x.fmt(x.num);
      try {
        // the start value now, so no frame shows the final number first; any
        // instant path inside count() writes the final text before a paint
        if (typeof H.live === "function" && H.live()) el.textContent = f(from);
        H.count(el, x.num, { fmt: f, from: from, delay: (k++) * 0.04 });
      } catch (e) { el.textContent = x.val; }
    });
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
  var _vtLast = 0, _vtBusy = false, _vtAgain = false;
  function vitalsMaybeRefresh(force, user) {
    var now = Date.now();
    if (!force && now - _vtLast < 15000) return;
    _vtLast = now;
    vitalsRefresh(user).catch(function () {});
  }

  // user: the owner asked for this render (the 24h/7d switch). The card's
  // first fill (its skeleton is still up) moves as well; a poll never does.
  // A 24h/7d switch that lands while a fetch is in flight used to be dropped
  // (the toggle showed the new window over the old window's numbers until a
  // later poll): it is remembered instead and re-run once the fetch settles.
  async function vitalsRefresh(user) {
    if (_vtBusy) { if (user === true) _vtAgain = true; return; }
    _vtBusy = true;
    try {
      var card = ensureCard();
      if (!card) return;
      var g0 = card.querySelector ? card.querySelector("#vt-grid") : null;
      var first = !!(g0 && g0.querySelector && g0.querySelector(".vt-skel"));
      var win = getWin();
      var data;
      try {
        var r = await fetch("/api/metrics?days=" + (win === "7d" ? 7 : 1), { cache: "no-store" });
        if (r.status === 404) { if (card.remove) card.remove(); return; }
        data = await r.json();
      } catch (e) { markError(card); return; }
      if (!data || data.ok === false) { markError(card, data); return; }
      // the owner switched windows while this was in flight: it is the other
      // window's data, so it is not painted; the re-run below fetches the right one
      if (getWin() !== win) { _vtAgain = true; return; }
      render(card, data, first || user === true);
    } finally {
      _vtBusy = false;
      if (_vtAgain) { _vtAgain = false; vitalsRefresh(true).catch(function () {}); }
    }
  }

  // first paint if the Console or Settings is the restored view on load
  try {
    var vc = doc() && doc().getElementById("view-console");
    var vm = doc() && doc().getElementById("view-mind");
    if ((vc && !vc.hidden) || (vm && !vm.hidden)) vitalsMaybeRefresh(true);
  } catch (e) {}

  // expose for the headless render harness / manual invocation
  window.vitalsRefresh = vitalsRefresh;
})();
