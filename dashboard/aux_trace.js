// aux_trace.js — the "Traces" card for Settings › System & Data.
//
// What it does: picks a time range, shows what the tracing export would
// contain for it (GET /api/trace/summary), and downloads the file in either
// shape (GET /api/trace/export?format=jsonl|otel).
//
// PLACEMENT. Same constraint aux_network.js and aux_promptbudget.js document:
// aux_settings_shell.js builds its rail and its twelve panels ONCE from a
// PANELS array captured in its IIFE, ensureShell() early-returns on the second
// call, and window.SETTINGS_PANELS is a read-out, not a hook — a module cannot
// register a new Settings panel. So this is a CARD mounted DIRECTLY into
// #sec-system, next to Health (aux_doctor.js), which is where the other
// "what this Mac recorded about itself" surfaces live.
//
// DOWNLOADING. Copied verbatim from index.html's cvExport(): the Swift shell
// (app/main.swift, frozen) registers no WKDownloadDelegate, so an <a download>
// of an attachment is silently dropped inside the app — there we hand the text
// to the `hermesClipWrite` bridge instead, and fall back to a Blob link (real
// browser) and then to navigator.clipboard. The endpoint is a plain GET either
// way, so the URL works on its own if all three fail.
//
// Design laws (CLAUDE.md): zero emoji, 12-hour clock, esc() on every
// interpolation, tabular numerals on every number, explicit
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
  function T(msg, ms) {
    if (typeof toast === "function") { try { toast(msg, ms); return; } catch (e) {} }
  }

  var CARD_ID = "mind-extra-trace";
  var PANEL_ID = "sec-system";
  var SEL = "#" + CARD_ID;
  var MAX_DAYS = 31;                  // must match _TR_MAX_DAYS in aux_trace.py

  var S = {
    range: "today",     // "today" | "7d" | "30d" | "custom"
    from: "",           // YYYY-MM-DD, custom only
    to: "",             // YYYY-MM-DD, custom only
    sum: null,          // the /api/trace/summary payload
    loading: false,
    busy: "",           // "jsonl" | "otel" while a download is in flight
    err: ""
  };

  // ---- glyph (two-tone: accent fill + currentColor stroke; zero emoji) ------
  // A span waterfall: one long bar with two shorter children under it.
  var GLY_TRACE =
    '<svg class="tric" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<rect x="3" y="4.6" width="18" height="4.2" rx="2.1" fill="var(--iris)" opacity=".2" ' +
    'stroke="currentColor" stroke-width="1.4"/>' +
    '<rect x="6.4" y="10.9" width="10.4" height="3.4" rx="1.7" fill="none" ' +
    'stroke="currentColor" stroke-width="1.4"/>' +
    '<rect x="9.6" y="16.4" width="8" height="3.4" rx="1.7" fill="var(--iris)" opacity=".2" ' +
    'stroke="currentColor" stroke-width="1.4"/></svg>';

  // ---- pure helpers (exported for the headless harness) --------------------
  function num(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return v.toLocaleString ? v.toLocaleString("en-US") : String(v);
  }

  function secs(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return (Math.round(v * 100) / 100).toFixed(2) + "s";
  }

  function pct(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return Math.round(v) + "%";
  }

  function ymd(d) {
    return d.getFullYear() + "-" +
      ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
  }

  function midnight(str) {
    // local midnight of a YYYY-MM-DD string; the Date(y,m,d) form is local,
    // Date.parse("2026-09-07") is UTC and would shift the range by a timezone.
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(str || ""));
    if (!m) return null;
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime() / 1000;
  }

  // PURE: the epoch range a state means, or {error} when the custom pair is
  // unusable. `now` is injected so the harness can drive every branch.
  function rangeFor(state, now) {
    now = now || (Date.now() / 1000);
    var day = 86400;
    if (state.range === "7d") return { since: now - 7 * day, until: now };
    if (state.range === "30d") return { since: now - 30 * day, until: now };
    if (state.range === "custom") {
      var a = midnight(state.from), b = midnight(state.to);
      if (a === null || b === null) return { error: "Pick both dates." };
      var until = b + day;                       // the To day, included whole
      if (until > now) until = now;
      if (until <= a) return { error: "The end date must be on or after the start." };
      if (until - a > MAX_DAYS * day) {
        return { error: "At most " + MAX_DAYS + " days per export." };
      }
      return { since: a, until: until };
    }
    var t = new Date();
    return { since: new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime() / 1000,
             until: now };
  }

  function rangeLabel(state) {
    if (state.range === "7d") return "the last 7 days";
    if (state.range === "30d") return "the last 30 days";
    if (state.range === "custom") return "that range";
    return "today";
  }

  // ---- styles --------------------------------------------------------------
  function CSS() {
    return "<style>" +
      SEL + " .tric{flex:0 0 auto;color:var(--muted)}" +
      SEL + " .trlede{margin:2px 0 14px;font-size:12.5px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty;max-width:64ch}" +
      // range picker — a segmented row of equal buttons
      SEL + " .trseg{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}" +
      SEL + " .trseg button{flex:1 1 88px;min-height:40px;padding:9px 12px;" +
        "border-radius:10px;font-size:12.5px;font-weight:600;cursor:pointer;" +
        "color:var(--muted);border:1px solid var(--hairline);" +
        "background:var(--chip,rgba(255,255,255,.04));" +
        "transition-property:border-color,background-color,color;" +
        "transition-duration:150ms;transition-timing-function:ease-out}" +
      SEL + " .trseg button:hover{border-color:var(--iris)}" +
      SEL + " .trseg button.is-on{border-color:var(--iris);color:var(--ink);" +
        "background:color-mix(in srgb,var(--iris) 12%,transparent)}" +
      SEL + " .trdates{display:flex;gap:10px;align-items:center;flex-wrap:wrap;" +
        "margin:0 0 12px;font-size:12px;color:var(--muted)}" +
      SEL + " .trdates input{min-height:40px;padding:8px 10px;border-radius:9px;" +
        "border:1px solid var(--hairline);background:var(--chip,rgba(255,255,255,.04));" +
        "color:var(--ink);font-size:12.5px;font-variant-numeric:tabular-nums}" +
      // flat stat cells, never cards inside a card
      SEL + " .trstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));" +
        "gap:2px 18px;margin:0 0 14px;padding:0 0 14px;" +
        "border-bottom:1px solid var(--hairline)}" +
      SEL + " .trstat b{display:block;font-size:19px;font-weight:640;color:var(--ink);" +
        "font-variant-numeric:tabular-nums;line-height:1.25}" +
      SEL + " .trstat span{display:block;font-size:10.5px;letter-spacing:.05em;" +
        "text-transform:uppercase;color:var(--faint);margin-top:2px}" +
      SEL + " .trstat.is-quiet b{color:var(--muted)}" +
      SEL + " .trline{margin:0 0 14px;font-size:12px;line-height:1.6;color:var(--muted);" +
        "font-variant-numeric:tabular-nums;text-wrap:pretty}" +
      SEL + " .trbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:2px 0 0}" +
      SEL + " button.trb{padding:9px 16px;border-radius:10px;font-size:12.5px;" +
        "font-weight:600;cursor:pointer;border:0;background:var(--iris);color:#fff;" +
        "min-height:40px;transition-property:opacity;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " button.trb.trb-2{background:transparent;color:var(--ink);" +
        "border:1px solid var(--hairline)}" +
      SEL + " button.trb:disabled{opacity:.45;cursor:default}" +
      SEL + " .trerr{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--bad)}" +
      SEL + " .trfoot{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--hairline);" +
        "font-size:11px;line-height:1.55;color:var(--faint);text-wrap:pretty}" +
      SEL + " .trcode{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;" +
        "padding:1px 5px;border-radius:5px;background:var(--chip,rgba(255,255,255,.05))}" +
      "</style>";
  }

  // ---- markup --------------------------------------------------------------
  function stat(value, label, quiet) {
    return '<div class="trstat' + (quiet ? " is-quiet" : "") + '"><b>' + E(value) +
      "</b><span>" + E(label) + "</span></div>";
  }

  function segHTML(state) {
    var opts = [["today", "Today"], ["7d", "7 days"], ["30d", "30 days"],
                ["custom", "Custom"]];
    var out = '<div class="trseg">';
    for (var i = 0; i < opts.length; i++) {
      out += '<button type="button" data-range="' + E(opts[i][0]) + '"' +
        (state.range === opts[i][0] ? ' class="is-on"' : "") + ">" +
        E(opts[i][1]) + "</button>";
    }
    return out + "</div>";
  }

  function datesHTML(state) {
    if (state.range !== "custom") return "";
    return '<div class="trdates"><label>From <input type="date" data-from value="' +
      E(state.from) + '"></label><label>To <input type="date" data-to value="' +
      E(state.to) + '"></label></div>';
  }

  function statsHTML(state) {
    var s = state.sum;
    if (!s) {
      return '<div class="trstats">' + stat("—", "traces", true) +
        stat("—", "turns", true) + stat("—", "tool calls", true) +
        stat("—", "truncations", true) + "</div>";
    }
    return '<div class="trstats">' +
      stat(num(s.traces), "traces") + stat(num(s.turns), "turns") +
      stat(num(s.tool_spans), "tool calls") +
      stat(num(s.truncations), "truncations", !s.truncations) +
      stat(num(s.undone), "undone", !s.undone) + "</div>";
  }

  function summaryLine(state) {
    var s = state.sum;
    if (!s) return "";
    var bits = [];
    if (s.tokens_in || s.tokens_out) {
      bits.push(num(s.tokens_in) + " tokens in · " + num(s.tokens_out) + " out" +
        (s.turns_estimated_tokens ? " (" + num(s.turns_estimated_tokens) +
          (s.turns_estimated_tokens === 1 ? " turn" : " turns") +
          " estimated from reply length)" : ""));
    }
    if (s.mean_prefill_s != null) bits.push("mean prefill " + secs(s.mean_prefill_s));
    if (s.mean_cached_pct != null) bits.push("mean cached " + pct(s.mean_cached_pct));
    if (!bits.length) {
      return '<p class="trline">Nothing was recorded in ' + E(rangeLabel(state)) +
        ".</p>";
    }
    return '<p class="trline">' + E(bits.join(" · ")) + "</p>";
  }

  function cardHTML(state) {
    state = state || {};
    var busy = !!state.busy;
    return CSS() + "<h2>" + GLY_TRACE + "Traces</h2>" +
      '<div class="body">' +
      '<p class="trlede">Every turn this Mac ran, as spans: the chat turn with ' +
      "its tokens, prefill and context share, each tool call underneath it, and " +
      "the tool results that were truncated to fit. Assembled from the metrics " +
      "log, the flight recorder and the model server's own log — nothing new is " +
      "collected, and tool arguments never leave this Mac.</p>" +
      segHTML(state) + datesHTML(state) + statsHTML(state) + summaryLine(state) +
      '<div class="trbar">' +
      '<button type="button" class="trb" data-dl="jsonl"' + (busy ? " disabled" : "") +
      ">" + E(state.busy === "jsonl" ? "Preparing…" : "Download JSONL") + "</button>" +
      '<button type="button" class="trb trb-2" data-dl="otel"' + (busy ? " disabled" : "") +
      ">" + E(state.busy === "otel" ? "Preparing…" : "Download OTLP JSON") + "</button>" +
      "</div>" +
      (state.err ? '<p class="trerr">' + E(state.err) + "</p>" : "") +
      '<p class="trfoot">Load the OTLP file into Jaeger, Tempo or OpenObserve, ' +
      'or read the JSONL with <span class="trcode">jq</span>. At most ' +
      MAX_DAYS + " days per export.</p></div>";
  }

  // ---- data ----------------------------------------------------------------
  async function load() {
    var r = rangeFor(S);
    if (r.error) { S.sum = null; S.err = r.error; return; }
    S.loading = true;
    try {
      var res = await fetch("/api/trace/summary?since=" + encodeURIComponent(r.since.toFixed(3)) +
                            "&until=" + encodeURIComponent(r.until.toFixed(3)));
      var d = await res.json();
      if (d && d.ok) { S.sum = d; S.err = ""; }
      else { S.sum = null; S.err = (d && d.error) || "Could not read the trace summary."; }
    } catch (e) {
      S.sum = null;
      S.err = "Could not reach the dashboard.";
    }
    S.loading = false;
  }

  // ---- download: the cvExport() mechanism, verbatim -------------------------
  function clipBridge() {
    if (typeof cvClipBridge === "function") { try { return cvClipBridge(); } catch (e) {} }
    try {
      return !!(W.webkit && W.webkit.messageHandlers && W.webkit.messageHandlers.hermesClipWrite);
    } catch (e) { return false; }
  }

  async function download(fmt) {
    var r = rangeFor(S);
    if (r.error) { S.err = r.error; paint(); return; }
    var url = "/api/trace/export?since=" + encodeURIComponent(r.since.toFixed(3)) +
              "&until=" + encodeURIComponent(r.until.toFixed(3)) +
              "&format=" + encodeURIComponent(fmt);
    var label = (fmt === "otel") ? "OTLP JSON" : "JSONL";
    var mime = (fmt === "otel") ? "application/json" : "application/x-ndjson";
    var fname = "hermes-trace." + (fmt === "otel" ? "otlp.json" : "jsonl");
    var text = "";
    S.busy = fmt; S.err = ""; paint();
    try {
      var res = await fetch(url);
      if (!res.ok) {
        var msg = "Export failed.";
        try { var j = await res.json(); if (j && j.error) msg = j.error; } catch (e) {}
        S.busy = ""; S.err = msg; paint(); return;
      }
      text = await res.text();
      var cd = res.headers.get("Content-Disposition") || "";
      var m = cd.match(/filename="([^"]+)"/);
      if (m) fname = m[1];
    } catch (e) {
      S.busy = ""; S.err = "Could not reach the dashboard."; paint(); return;
    }
    S.busy = ""; paint();
    if (!text.length) { T("Nothing recorded in " + rangeLabel(S) + "."); return; }
    // The Swift shell (app/main.swift, frozen) registers no WKDownloadDelegate,
    // so an <a download> of an attachment is silently dropped inside the app —
    // copy there, download in a real browser. The endpoint is a plain GET
    // either way, so the URL above works on its own.
    if (clipBridge()) {
      try {
        W.webkit.messageHandlers.hermesClipWrite.postMessage({ action: "write", text: text });
        T("Copied as " + label); return;
      } catch (e) {}
    }
    try {
      var d = D();
      var a = d.createElement("a");
      a.href = URL.createObjectURL(new Blob([text], { type: mime }));
      a.download = fname; d.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      T("Exported as " + fname); return;
    } catch (e) {}
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text); T("Copied as " + label); return;
      }
    } catch (e) {}
    T("Could not export — open " + url + " to save it.", 5000);
  }

  // ---- wiring --------------------------------------------------------------
  function wire(card) {
    if (!card || !card.querySelectorAll) return;
    Array.prototype.slice.call(card.querySelectorAll("button[data-range]"))
      .forEach(function (b) {
        b.onclick = async function () {
          S.range = b.getAttribute("data-range");
          S.err = "";
          if (S.range === "custom" && !S.from) {
            var t = new Date();
            S.to = ymd(t);
            S.from = ymd(new Date(t.getFullYear(), t.getMonth(), t.getDate() - 6));
          }
          S.sum = null;
          paint();
          await load();
          paint();
        };
      });
    ["from", "to"].forEach(function (k) {
      var el = card.querySelector("input[data-" + k + "]");
      if (!el) return;
      el.onchange = async function () {
        S[k] = el.value || "";
        S.err = "";
        await load();
        paint();
      };
    });
    Array.prototype.slice.call(card.querySelectorAll("button[data-dl]"))
      .forEach(function (b) {
        b.onclick = function () { download(b.getAttribute("data-dl")); };
      });
  }

  var relocatedOnce = false;

  function paint() {
    var d = D(); if (!d) return;
    var panel = d.getElementById(PANEL_ID);
    // No panel yet => wait for the next mindExtras() pass. Deliberately NOT
    // falling back to #view-mind: the shell's relocator would send us to
    // sec-system anyway, and the extra round trip only risks a flash.
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
    try { el.innerHTML = cardHTML(S); } catch (e) { return; }
    wire(el);
    if (!relocatedOnce) {
      relocatedOnce = true;
      // one pass so the shell re-indexes its search and drops the panel's
      // "no cards yet" placeholder. Idempotent, and it never touches us (we
      // are not a direct child of #view-mind).
      try { if (typeof W.settingsRelocate === "function") W.settingsRelocate(); } catch (e) {}
    }
  }

  var loading = false;

  async function mount() {
    var d = D();
    if (!d || !d.getElementById(PANEL_ID)) return;
    if (!S.sum && !S.err && !loading) {
      loading = true;
      paint();                       // the dashes shell, so the panel is never
      try { await load(); }          // empty while the summary is counted
      catch (e) { S.err = "Could not read the trace summary."; }
      loading = false;
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
  W.hermesTrace = {
    cardHTML: cardHTML, CSS: CSS, rangeFor: rangeFor, rangeLabel: rangeLabel,
    midnight: midnight, ymd: ymd, num: num, secs: secs, pct: pct,
    mount: mount, paint: paint, load: load, download: download, state: S,
    refresh: async function () { S.sum = null; S.err = ""; await load(); paint(); }
  };
})();
