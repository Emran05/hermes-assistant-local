// aux_doctor.js — the "Health" card for Settings › System & Data.
//
// One screen of read-only health checks, the same list dashboard/doctor.py
// prints on the command line and serves at GET /api/doctor. Nothing here can
// start or wake a model server: the whole card is one GET.
//
// PLACEMENT. Exactly aux_update.js's pattern, because this card belongs in the
// same panel as Updates: append into #sec-system when the Settings shell has
// already built its panels, otherwise append to #view-mind and let the shell's
// relocator re-home it — an id that is not in its CARD_MAP falls back to
// sec-system BY DESIGN, which for us is the right destination (aux_network.js
// deliberately refuses that fallback because Connections is not sec-system).
// aux_settings_shell.js is never edited.
//
// Design laws (CLAUDE.md): zero emoji (bespoke two-tone SVG), 12-hour clock,
// esc() on every interpolation, explicit transition-property, theme colours
// only through the tokens every one of the four palette blocks re-declares
// (--ok / --warn / --bad / --ink / --muted / --faint / --hairline), and every
// global helper typeof-guarded so this file can be eval'd in a headless
// harness.
//
// There is no topbar summary: the topbar (aux_topbar.js) exposes no
// registration hook, and inventing one would mean editing a module this
// feature has no business touching.
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

  var CARD_ID = "mind-extra-doctor";
  var SEL = "#" + CARD_ID;

  var S = {
    data: null,        // the /api/doctor payload
    busy: false,       // a run is in flight
    err: "",
    copied: 0          // epoch ms of the last successful copy
  };

  // ---- glyphs (two-tone: accent fill + currentColor stroke; zero emoji) -----
  var GLY_PULSE =
    '<svg class="docic" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<rect x="2.6" y="4.4" width="18.8" height="15.2" rx="3.4" fill="var(--ok,#2E9E68)" opacity=".14"/>' +
    '<rect x="2.6" y="4.4" width="18.8" height="15.2" rx="3.4" fill="none" stroke="currentColor" ' +
    'stroke-width="1.5"/>' +
    '<path d="M5.4 12.4h3l1.7-4.2 2.6 8 1.8-3.8h3.1" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  // ---- pure helpers (exported for the headless harness) --------------------

  // absolute 12-hour clock — reuses aux_update.js's formatter when it loaded so
  // the two cards in this panel phrase a timestamp identically.
  function fmtWhen(ts) {
    try {
      var u = W.hermesUpdate;
      if (u && typeof u.fmtWhen === "function") return u.fmtWhen(ts);
    } catch (e) {}
    if (!ts) return "never";
    try {
      var d = new Date(ts * (ts > 1e12 ? 1 : 1000));
      if (isNaN(d.getTime())) return "never";
      var h = d.getHours(), m = d.getMinutes(), ap = h >= 12 ? "PM" : "AM";
      h = h % 12; if (h === 0) h = 12;
      var t = h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
      var now = new Date();
      if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() &&
          d.getDate() === now.getDate()) return t;
      return (d.getMonth() + 1) + "/" + d.getDate() + " " + t;
    } catch (e) { return "never"; }
  }

  function summaryOf(data) {
    var s = (data && data.summary) || {};
    return { pass: Number(s.pass || 0), warn: Number(s.warn || 0), fail: Number(s.fail || 0) };
  }

  // The one-line verdict above the chips. Pure, so the harness can drive it.
  function verdict(data) {
    if (!data) return "Not run yet.";
    var s = summaryOf(data);
    if (s.fail) return s.fail === 1 ? "1 check failed." : s.fail + " checks failed.";
    if (s.warn) return s.warn === 1 ? "Everything works; 1 thing wants attention."
                                    : "Everything works; " + s.warn + " things want attention.";
    return "Everything checks out.";
  }

  // ---- styles --------------------------------------------------------------
  // Scoped to the card id. Colours come only from tokens that all four palette
  // blocks re-declare, so the card is correct in dark, light, and either of the
  // two system-preference variants without a second definition.
  function CSS() {
    return "<style>" +
      SEL + " .doclede{margin:2px 0 12px;font-size:12.5px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty;max-width:64ch}" +
      SEL + " .docbar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:0 0 12px}" +
      SEL + " button.docb{padding:6px 13px;border-radius:9px;font-size:12.5px;cursor:pointer;" +
        "border:1px solid var(--hairline,rgba(255,255,255,.12));" +
        "background:var(--glass-2,rgba(255,255,255,.05));color:inherit;" +
        "transition-property:background-color,border-color,opacity;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " button.docb:disabled{opacity:.5;cursor:default}" +
      SEL + " button.docgo{border:0;background:var(--iris,#6b8afd);color:#fff;font-weight:600}" +
      SEL + " .docwhen{font-size:11.5px;color:var(--faint);margin-left:auto;" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " .docsum{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:0 0 4px}" +
      SEL + " .docchip{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;" +
        "border-radius:20px;font-size:11px;font-variant-numeric:tabular-nums;" +
        "border:1px solid var(--hairline,rgba(255,255,255,.12));" +
        "background:var(--chip,rgba(255,255,255,.05));color:var(--muted)}" +
      SEL + " .docchip b{font-weight:650;color:var(--ink)}" +
      SEL + " .docchip i{width:7px;height:7px;border-radius:50%;background:var(--faint);" +
        "flex:0 0 auto}" +
      SEL + " .docchip.is-pass i{background:var(--ok,#2E9E68)}" +
      SEL + " .docchip.is-warn i{background:var(--warn,#B9821A)}" +
      SEL + " .docchip.is-fail i{background:var(--bad,#D24C3C)}" +
      SEL + " .docchip.is-zero{opacity:.5}" +
      SEL + " .docverdict{margin:0 0 12px;font-size:12px;font-weight:600;color:var(--ink)}" +
      // flat rows, never cards inside a card
      SEL + " ul.doclist{list-style:none;margin:0;padding:0}" +
      SEL + " ul.doclist li{display:grid;grid-template-columns:9px minmax(0,1fr);" +
        "gap:2px 10px;padding:9px 0;border-top:1px solid var(--hairline,rgba(255,255,255,.10))}" +
      SEL + " ul.doclist li:first-child{border-top:0}" +
      SEL + " .docdot{grid-row:1;width:8px;height:8px;border-radius:50%;margin-top:5px;" +
        "background:var(--faint)}" +
      SEL + " li.is-pass .docdot{background:var(--ok,#2E9E68)}" +
      SEL + " li.is-warn .docdot{background:var(--warn,#B9821A)}" +
      SEL + " li.is-fail .docdot{background:var(--bad,#D24C3C)}" +
      SEL + " .doclab{grid-column:2;font-size:12.5px;font-weight:620;color:var(--ink)}" +
      SEL + " li.is-warn .doclab{color:var(--warn,#B9821A)}" +
      SEL + " li.is-fail .doclab{color:var(--bad,#D24C3C)}" +
      SEL + " .docdet{grid-column:2;font-size:11.5px;line-height:1.5;color:var(--muted);" +
        "text-wrap:pretty;overflow-wrap:anywhere}" +
      SEL + " .docfix{grid-column:2;margin-top:5px;padding:5px 8px;border-radius:8px;" +
        "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;line-height:1.5;" +
        "color:var(--ink);background:var(--chip,rgba(255,255,255,.05));" +
        "border:1px solid var(--hairline,rgba(255,255,255,.12));overflow-wrap:anywhere}" +
      SEL + " .docfix b{font-weight:650;color:var(--faint);font-family:inherit}" +
      SEL + " .docerr{margin:8px 0 0;font-size:11.5px;color:var(--bad,#D24C3C)}" +
      SEL + " .docfoot{margin:12px 0 0;font-size:11px;line-height:1.5;color:var(--faint);" +
        "text-wrap:pretty}" +
      SEL + " .doccode{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;" +
        "padding:1px 5px;border-radius:5px;background:var(--chip,rgba(255,255,255,.05))}" +
      "</style>";
  }

  // ---- markup --------------------------------------------------------------
  function chip(kind, n, word) {
    return '<span class="docchip is-' + kind + (n ? "" : " is-zero") + '">' +
      "<i></i><b>" + E(String(n)) + "</b> " + E(word) + "</span>";
  }

  function rowHTML(c) {
    c = c || {};
    var st = (c.status === "warn" || c.status === "fail") ? c.status : "pass";
    var h = '<li class="is-' + st + '"><span class="docdot"></span>' +
      '<span class="doclab">' + E(c.label || c.id || "?") + "</span>" +
      '<span class="docdet">' + E(c.detail || "") + "</span>";
    // A fix line is shown only where it can be acted on — under WARN and FAIL.
    if (st !== "pass" && c.fix) {
      h += '<div class="docfix"><b>fix</b> &nbsp;' + E(c.fix) + "</div>";
    }
    return h + "</li>";
  }

  function cardHTML(state) {
    state = state || {};
    var d = state.data;
    var s = summaryOf(d);
    var checks = (d && Array.isArray(d.checks)) ? d.checks : [];

    var bar = '<div class="docbar">' +
      '<button class="docb docgo" data-act="run"' + (state.busy ? " disabled" : "") + ">" +
      (state.busy ? "Running…" : "Run checks") + "</button>" +
      '<button class="docb" data-act="copy"' + (d ? "" : " disabled") + ">" +
      (state.copied && (Date.now() - state.copied) < 4000 ? "Copied" : "Copy report") +
      "</button>" +
      (d ? '<span class="docwhen">last run ' + E(fmtWhen(d.generated)) + "</span>" : "") +
      "</div>";

    var sum = '<div class="docsum">' + chip("pass", s.pass, "pass") +
      chip("warn", s.warn, "warn") + chip("fail", s.fail, "fail") + "</div>" +
      '<p class="docverdict">' + E(verdict(d)) + "</p>";

    var list = checks.length
      ? '<ul class="doclist">' + checks.map(rowHTML).join("") + "</ul>"
      : '<p class="docdet">No checks have run yet.</p>';

    return CSS() +
      "<h2>" + GLY_PULSE + "Health</h2>" +
      '<div class="body">' +
      '<p class="doclede">Every part of the assistant, checked in one pass: services, the ' +
      "agent, the model roster, disk, permissions, config, the search index and the logs. " +
      "Read-only &mdash; nothing here starts or wakes a model, so it is safe on battery.</p>" +
      bar + sum + list +
      (state.err ? '<p class="docerr">' + E(state.err) + "</p>" : "") +
      '<p class="docfoot">The same checks run in a terminal: ' +
      '<span class="doccode">python3 dashboard/doctor.py</span> ' +
      "(exit 0 when nothing failed).</p>" +
      "</div>";
  }

  // ---- data ----------------------------------------------------------------
  async function jget(url) {
    try {
      var r = await fetch(url);
      return await r.json();
    } catch (e) { return null; }
  }

  async function run(fresh) {
    S.busy = true;
    S.err = "";
    paint();
    var j = await jget("/api/doctor" + (fresh ? "?fresh=1" : ""));
    S.busy = false;
    if (!j || j.ok === false) {
      S.err = (j && j.error) ? String(j.error)
                             : "The dashboard did not answer /api/doctor.";
    } else {
      S.data = j;
    }
    paint();
  }

  // clipboard with the textarea fallback (aux_config.js's copyText) — a
  // WKWebView can refuse the async clipboard API.
  function copyText(t) {
    try {
      if (W.navigator && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(t);
        return true;
      }
    } catch (e) {}
    try {
      var d = D(); if (!d) return false;
      var ta = d.createElement("textarea");
      ta.value = t;
      d.body.appendChild(ta);
      ta.select();
      d.execCommand("copy");
      d.body.removeChild(ta);
      return true;
    } catch (e) { return false; }
  }

  async function copyReport() {
    // the TEXT format, so what lands in the clipboard is the same report the
    // CLI prints — that is what someone pastes into an issue.
    var txt = "";
    try {
      var r = await fetch("/api/doctor?format=text");
      txt = await r.text();
    } catch (e) { txt = ""; }
    if (!txt) {
      S.err = "Could not fetch the text report.";
      paint();
      return;
    }
    if (copyText(txt)) {
      S.copied = Date.now();
      S.err = "";
      try { if (typeof W.toast === "function") W.toast("Health report copied"); } catch (e) {}
    } else {
      S.err = "Could not reach the clipboard.";
    }
    paint();
  }

  // ---- mount ---------------------------------------------------------------
  function host() {
    var d = D();
    if (!d) return null;
    return d.getElementById("sec-system") || d.getElementById("view-mind");
  }

  function wire(el) {
    if (!el || !el.querySelectorAll) return;
    Array.prototype.slice.call(el.querySelectorAll("button[data-act]")).forEach(function (b) {
      b.onclick = function () {
        var act = b.getAttribute("data-act");
        if (act === "run") run(true);
        else if (act === "copy") copyReport();
      };
    });
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
  }

  var mounted = false;

  async function mount() {
    var d = D();
    if (!d || !host()) return;
    if (!mounted) {
      mounted = true;
      // the cached run (<=10s server-side): opening Settings costs one GET and
      // never re-sweeps launchctl for a second card paint.
      await run(false);
      return;
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
  W.hermesDoctor = {
    cardHTML: cardHTML, rowHTML: rowHTML, CSS: CSS, verdict: verdict,
    summaryOf: summaryOf, fmtWhen: fmtWhen, mount: mount, paint: paint,
    run: run, copyReport: copyReport, state: S
  };
})();
