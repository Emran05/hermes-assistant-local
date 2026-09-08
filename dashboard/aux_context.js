// aux_context.js — the context meter (chat header) and the "Context &
// compaction" card (Settings › Agent & Models).
//
// TWO SURFACES, ONE MODULE, because they read the same two routes:
//
//  1. THE METER. After every finished chat turn, a chip next to the model pill
//     reads "24.1k ctx · 96 % cached · 1.2 s prefill" — what the conversation
//     now costs to send, how much of it the prefix cache served for free, and
//     the seconds of prefill that bought. It stays hidden until the first turn
//     is measured, because an empty meter is worse than none, and it takes the
//     warning token at 70 % of the window and the bad token at 90 % — the
//     points where the next turn starts to be at risk of being summarised away.
//
//  2. THE CARD. The window, the four compression knobs, the model that does the
//     summarising, and the last ten turns as a table, so the knobs are set
//     against evidence rather than a feeling.
//
// HOW IT HOOKS THE CHAT WITHOUT EDITING streamJob. index.html's streamJob is a
// top-level function declaration, i.e. a writable property of window, and its
// only caller resolves it as a global at call time. So this module WRAPS it:
//   * the wrapper remembers the job id for the duration of the turn and, in a
//     `finally`, schedules the measurement with setTimeout(0) — after the
//     stream has already returned. The fetch is never awaited by the chat path,
//     so a slow or failed measurement cannot delay a single token.
//   * setAgentState is wrapped the same way, because it is the ONE function
//     streamJob hands every poll's status text to. That is where the gateway's
//     "Compacting context" arrives (kind:"compacting"), and where its emoji is
//     replaced by the plain state line this UI actually uses. The wrapper also
//     latches the observation for the turn, because job["status"] is transient
//     server-side — the next tool.start overwrites it — so the client that
//     watched the whole turn is the honest witness for `compacting=1`.
// Both wrappers are pass-through and fail-safe: any throw inside them is
// swallowed and the original is still called.
//
// PLACEMENT of the card. Same constraint aux_network.js and aux_promptbudget.js
// document: aux_settings_shell.js builds its rail and panels ONCE from a PANELS
// array captured in its IIFE, so a module cannot register a new Settings panel.
// This is a CARD mounted DIRECTLY into #sec-models, after the budget cards
// (script order decides), and it deliberately does NOT fall back to #view-mind:
// the shell's relocator files an unknown id under System & Data, which is the
// wrong home for a model setting.
//
// Design laws (CLAUDE.md): zero emoji, 12-hour clock, esc() on every
// interpolation, tabular numerals on every number, explicit transition-property,
// >=40px targets, colours only through tokens all four palette blocks
// re-declare, and every global helper typeof-guarded so this file can be
// eval'd in a headless harness.
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

  var CARD_ID = "mind-extra-context";
  var PANEL_ID = "sec-models";
  var SEL = "#" + CARD_ID;
  var CHIP_ID = "ctx-chip";

  var KEYS = ["threshold", "target_ratio", "protect_last_n", "protect_first_n"];

  var LABELS = {
    threshold: "Compact at",
    target_ratio: "Summarise down to",
    protect_last_n: "Keep the last",
    protect_first_n: "Keep the first"
  };
  var HINTS = {
    threshold: "When to compact, as a fraction of the window.",
    target_ratio: "How small the summary should come out, as a fraction of that threshold.",
    protect_last_n: "Recent messages that are never summarised.",
    protect_first_n: "Opening messages that are never summarised. The system prompt always is."
  };

  var S = {
    data: null,        // GET /api/context/compression
    recent: null,      // GET /api/context/recent?n=10
    loaded: false,
    busy: false,
    err: "",
    edits: {},         // key -> string, only while the user is typing
    result: null       // {before, after, changed}
  };

  // last measured turn, so a Settings repaint can show it without re-fetching
  var LAST = null;

  // ---- glyph (two-tone: accent fill + currentColor stroke; zero emoji) ------
  var GLY_CTX =
    '<svg class="cxic" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<rect x="2.8" y="4.4" width="18.4" height="6.2" rx="2.2" fill="var(--iris)" opacity=".16"/>' +
    '<rect x="2.8" y="4.4" width="18.4" height="6.2" rx="2.2" fill="none" ' +
    'stroke="currentColor" stroke-width="1.5"/>' +
    '<rect x="2.8" y="13.4" width="10.6" height="6.2" rx="2.2" fill="none" ' +
    'stroke="currentColor" stroke-width="1.5"/>' +
    '<path d="M17.4 13.6l3.4 2.9-3.4 2.9" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  // ---- pure helpers (exported for the headless harness) --------------------
  function tok(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + "k";
    return String(Math.round(v));
  }
  function full(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return Math.round(v).toLocaleString();
  }
  function pct(n, nd) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return v.toFixed(nd == null ? 0 : nd) + " %";
  }
  function secs(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return (v >= 10 ? v.toFixed(0) : v.toFixed(1)) + " s";
  }
  function clock(epoch) {
    var v = Number(epoch);
    if (!isFinite(v) || v <= 0) return "—";
    try {
      return new Date(v * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    } catch (e) { return "—"; }
  }

  // 70 % of the window is where the next turn starts to be at risk; 90 % is
  // where the compressor is about to take the conversation apart.
  function level(pctOfWindow) {
    var v = Number(pctOfWindow);
    if (!isFinite(v)) return "";
    if (v >= 90) return "bad";
    if (v >= 70) return "warn";
    return "";
  }

  function chipText(m) {
    m = m || {};
    var bits = [tok(m.prompt_tokens) + " ctx"];
    if (m.cache_pct != null) bits.push(pct(m.cache_pct) + " cached");
    if (m.prefill_s != null) bits.push(secs(m.prefill_s) + " prefill");
    return bits.join(" · ");
  }

  function chipTitle(m) {
    m = m || {};
    var L = [];
    L.push("Prompt " + full(m.prompt_tokens) + " tokens of " + full(m.window) +
           (m.pct_of_window != null ? " (" + pct(m.pct_of_window, 1) + " of the window)" : ""));
    if (m.cached_tokens != null) {
      L.push(full(m.cached_tokens) + " served from the prefix cache" +
             (m.cache_pct != null ? " (" + pct(m.cache_pct, 1) + ")" : "") +
             (m.new_tokens != null ? ", " + full(m.new_tokens) + " new" : ""));
    }
    var line3 = [];
    if (m.prefill_s != null) line3.push("Prefill " + secs(m.prefill_s));
    if (m.decode_tps != null) line3.push("decode " + Number(m.decode_tps).toFixed(1) + " tok/s");
    if (m.generated_tokens != null) line3.push(full(m.generated_tokens) + " tokens out");
    if (line3.length) L.push(line3.join(" · "));
    if (m.requests > 1) L.push(m.requests + " model requests in this turn");
    if (m.compacted) L.push("Context was compacted during this turn");
    return L.join("\n");
  }

  function noteText(m) {
    m = m || {};
    if (m.prev_prompt_tokens && m.prompt_tokens) {
      return "Context compacted — prompt went from " + tok(m.prev_prompt_tokens) +
             " to " + tok(m.prompt_tokens) + " tokens";
    }
    return "Context compacted";
  }

  // ---- the chip ------------------------------------------------------------
  function chipCSS() {
    return "#" + CHIP_ID + "{font-variant-numeric:tabular-nums;white-space:nowrap;" +
      "cursor:default;letter-spacing:-.005em;" +
      "transition-property:color,border-color;transition-duration:200ms;" +
      "transition-timing-function:ease-out}" +
      "#" + CHIP_ID + "[hidden]{display:none}" +
      "#" + CHIP_ID + ".is-warn{color:var(--warn);" +
      "border-color:color-mix(in srgb,var(--warn) 45%,transparent)}" +
      "#" + CHIP_ID + ".is-bad{color:var(--bad);" +
      "border-color:color-mix(in srgb,var(--bad) 55%,transparent)}" +
      // a number nobody re-measured is dimmed, never left looking fresh
      "#" + CHIP_ID + ".is-stale{opacity:.5;color:var(--muted);" +
      "border-color:var(--hairline)}" +
      "@media (max-width:1100px){#" + CHIP_ID + "{display:none}}" +
      ".ctx-note{align-self:center;max-width:88%;margin:2px 0;padding:5px 12px;" +
      "border-radius:99px;border:1px solid var(--hairline);background:var(--glass-2);" +
      "color:var(--muted);font-size:11.5px;line-height:1.4;text-align:center;" +
      "font-variant-numeric:tabular-nums}";
  }

  function ensureChipStyle() {
    var d = D(); if (!d) return;
    if (d.getElementById("ctx-chip-style")) return;
    var st = d.createElement("style");
    st.id = "ctx-chip-style";
    st.textContent = chipCSS();
    (d.head || d.documentElement).appendChild(st);
  }

  function chipEl() {
    var d = D(); if (!d) return null;
    var el = d.getElementById(CHIP_ID);
    if (el) return el;
    // sibling BEFORE .modelwrap, never inside it: .modelwrap is the positioning
    // context for the model popover, and a chip in there would move the menu.
    var wrap = d.querySelector("header .modelwrap");
    if (!wrap || !wrap.parentNode) return null;
    ensureChipStyle();
    el = d.createElement("div");
    el.id = CHIP_ID;
    el.className = "pill";
    el.hidden = true;
    el.setAttribute("aria-live", "polite");
    wrap.parentNode.insertBefore(el, wrap);
    return el;
  }

  function renderChip(m) {
    var el = chipEl();
    if (!el) return null;
    if (!m || m.found === false || m.prompt_tokens == null) { el.hidden = true; return el; }
    LAST = m;
    el.textContent = chipText(m);
    el.title = chipTitle(m);
    el.className = "pill" + (level(m.pct_of_window) ? " is-" + level(m.pct_of_window) : "");
    el.hidden = false;
    return el;
  }

  // The chip asserts a number about the turn that just ended. When a turn
  // cannot be measured, the ONE thing it must not do is keep showing the
  // previous turn's numbers as if they were this turn's — that is a wrong
  // answer, not a missing one. So:
  //   * found === false -> the server looked and there were no rows: say "not
  //     measured" and hand its own `note` over as the tooltip;
  //   * ok === false, or no answer at all -> dim what is there and say the
  //     measurement failed, or stay hidden if there was never a number.
  // Either way the next successful renderChip() clears the state.
  function renderNotMeasured(m) {
    var el = chipEl();
    if (!el) return null;
    LAST = null;
    el.textContent = "ctx not measured";
    el.title = (m && m.note) ? String(m.note)
                             : "No model request is logged for this turn.";
    el.className = "pill is-stale";
    el.hidden = false;
    return el;
  }

  function markStale(why) {
    var el = chipEl();
    if (!el) return null;
    if (!LAST || el.hidden) return el;      // nothing to mislead anyone with
    el.title = (why || "measurement failed") + "\n\nLast measured turn:\n" +
               chipTitle(LAST);
    if (el.className.indexOf("is-stale") < 0) {
      el.className = "pill is-stale";
    }
    return el;
  }

  function renderNote(m) {
    var d = D(); if (!d) return null;
    var msgs = d.getElementById("msgs");
    if (!msgs) return null;
    ensureChipStyle();
    var n = d.createElement("div");
    n.className = "ctx-note";
    n.setAttribute("role", "status");
    n.textContent = noteText(m);
    msgs.appendChild(n);
    msgs.scrollTop = msgs.scrollHeight;
    return n;
  }

  // ---- data ----------------------------------------------------------------
  async function jget(url) {
    try {
      var r = await fetch(url);
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

  // ---- the turn hook -------------------------------------------------------
  var CUR = null;          // {job, compacting} while a turn is streaming

  async function measure(turn) {
    if (!turn || !turn.job) return;
    var url = "/api/context/turn?job=" + encodeURIComponent(turn.job) +
              (turn.compacting ? "&compacting=1" : "");
    var m = await jget(url);
    if (!m || m.ok === false) {
      try {
        markStale(m && m.error ? String(m.error) : "measurement failed");
      } catch (e) {}
      return;
    }
    if (m.found === false) {
      try { renderNotMeasured(m); } catch (e) {}
      return;
    }
    try { renderChip(m); } catch (e) {}
    if (m.compacted) { try { renderNote(m); } catch (e) {} }
    // the card's table is now one turn out of date
    if (S.loaded) { S.recent = null; }
  }

  function install() {
    var prevSAS = W.setAgentState;
    if (typeof prevSAS === "function" && !prevSAS.__ctxWrapped) {
      var sas = function (txt) {
        var t = txt;
        try {
          if (t && /compact/i.test(String(t))) {
            if (CUR) CUR.compacting = true;
            t = "Compacting context";       // the gateway's text carries an emoji
          }
        } catch (e) { t = txt; }
        return prevSAS.call(this, t);
      };
      sas.__ctxWrapped = true;
      W.setAgentState = sas;
    }

    var prevStream = W.streamJob;
    if (typeof prevStream === "function" && !prevStream.__ctxWrapped) {
      var wrapped = async function (jid, thinking) {
        var mine = { job: jid, compacting: false };
        CUR = mine;
        try {
          return await prevStream.apply(this, arguments);
        } finally {
          if (CUR === mine) CUR = null;
          // AFTER the stream has returned, and never awaited by it: one fetch
          // per job, on its own turn of the event loop.
          try { setTimeout(function () { measure(mine); }, 0); } catch (e) {}
        }
      };
      wrapped.__ctxWrapped = true;
      W.streamJob = wrapped;
    }
  }

  // ---- card styles ---------------------------------------------------------
  function CSS() {
    return "<style>" +
      SEL + " .cxic{flex:0 0 auto;color:var(--muted)}" +
      SEL + " .cxlede{margin:2px 0 14px;font-size:12.5px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty;max-width:64ch}" +
      SEL + " .cxstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));" +
        "gap:2px 18px;margin:0 0 16px;padding:0 0 14px;" +
        "border-bottom:1px solid var(--hairline)}" +
      SEL + " .cxstat b{display:block;font-size:19px;font-weight:640;color:var(--ink);" +
        "font-variant-numeric:tabular-nums;line-height:1.25}" +
      SEL + " .cxstat span{display:block;font-size:10.5px;letter-spacing:.05em;" +
        "text-transform:uppercase;color:var(--faint);margin-top:2px}" +
      SEL + " .cxstat.is-soft b{color:var(--muted);font-size:15px;line-height:1.55}" +
      // the four knobs
      // 252px, measured: the panel renders ~593px wide, so auto-fit settles on
      // two columns (2x252 + 12 = 516) and the four knobs land as a tidy 2x2
      // instead of a 3+1 with an orphan. Any narrower and the third column
      // squeezes the hint into a four-line ribbon beside the number.
      SEL + " .cxknobs{display:grid;grid-template-columns:repeat(auto-fit,minmax(252px,1fr));" +
        "gap:12px;margin:0 0 14px}" +
      // the input sits on row 1 beside the title; hint and live preview run the
      // FULL width underneath, which is what keeps the sentence readable
      SEL + " label.cxknob{display:grid;grid-template-columns:minmax(0,1fr) 92px;" +
        "gap:6px 12px;align-items:center;align-content:start;padding:11px 13px;" +
        "border-radius:11px;border:1px solid var(--hairline);" +
        "background:var(--chip,rgba(255,255,255,.04))}" +
      SEL + " label.cxknob b{grid-column:1;grid-row:1;font-size:12.5px;" +
        "font-weight:640;color:var(--ink)}" +
      SEL + " label.cxknob input{grid-column:2;grid-row:1;width:100%;min-height:40px;" +
        "box-sizing:border-box;padding:8px 10px;border-radius:9px;font:inherit;" +
        "font-size:13px;font-variant-numeric:tabular-nums;text-align:right;" +
        "color:var(--ink);background:var(--glass-2);border:1px solid var(--hairline)}" +
      SEL + " label.cxknob input:focus{outline:2px solid var(--iris);outline-offset:1px}" +
      SEL + " label.cxknob span{grid-column:1/-1;grid-row:2;font-size:11.5px;" +
        "line-height:1.45;color:var(--muted);text-wrap:pretty}" +
      SEL + " label.cxknob em{grid-column:1/-1;grid-row:3;font-style:normal;" +
        "font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}" +
      // actions + result
      SEL + " .cxbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:4px 0 0}" +
      SEL + " button.cxb{padding:9px 16px;border-radius:10px;font-size:12.5px;" +
        "font-weight:600;cursor:pointer;border:0;background:var(--iris);color:#fff;" +
        "min-height:40px;transition-property:opacity;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " button.cxb:disabled{opacity:.45;cursor:default}" +
      SEL + " .cxres{margin:12px 0 0;font-size:12px;line-height:1.55;color:var(--ink);" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " .cxres b{font-weight:640}" +
      SEL + " .cxerr{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--bad)}" +
      // recent turns
      SEL + " h3.cxh{margin:20px 0 8px;padding-top:14px;font-size:12px;font-weight:660;" +
        "color:var(--ink);border-top:1px solid var(--hairline)}" +
      SEL + " .cxwrap{overflow-x:auto}" +
      SEL + " table.cxt{border-collapse:collapse;width:100%;font-size:11.5px;" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " table.cxt th{text-align:right;font-weight:600;color:var(--faint);" +
        "font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;" +
        "padding:0 0 6px 12px;white-space:nowrap}" +
      SEL + " table.cxt th:first-child,"
          + SEL + " table.cxt td:first-child{text-align:left;padding-left:0}" +
      SEL + " table.cxt td{text-align:right;padding:6px 0 6px 12px;color:var(--ink);" +
        "border-top:1px solid var(--hairline);white-space:nowrap}" +
      SEL + " table.cxt td.is-warn{color:var(--warn)}" +
      SEL + " table.cxt td.is-bad{color:var(--bad)}" +
      SEL + " table.cxt td.is-muted{color:var(--faint)}" +
      SEL + " .cxfoot{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--hairline);" +
        "font-size:11px;line-height:1.55;color:var(--faint);text-wrap:pretty}" +
      SEL + " .cxcode{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;" +
        "padding:1px 5px;border-radius:5px;background:var(--chip,rgba(255,255,255,.05))}" +
      "</style>";
  }

  // ---- card markup ---------------------------------------------------------
  function stat(value, label, soft) {
    return '<div class="cxstat' + (soft ? " is-soft" : "") + '"><b>' + E(value) +
      "</b><span>" + E(label) + "</span></div>";
  }

  function knobHTML(key, value, lim, sub) {
    var step = (lim && lim.kind === "int") ? "1" : "0.05";
    return '<label class="cxknob"><b>' + E(LABELS[key]) + "</b>" +
      '<input type="number" data-k="' + E(key) + '" value="' + E(value) +
      '" min="' + E(lim ? lim.min : "") + '" max="' + E(lim ? lim.max : "") +
      '" step="' + step + '" inputmode="decimal" ' +
      'aria-label="' + E(LABELS[key] + " — " + HINTS[key]) + '">' +
      "<span>" + E(HINTS[key]) + "</span>" +
      // the live preview line only exists where there is something to preview;
      // "messages" under "Recent messages that are never summarised" would just
      // be the hint said twice
      (sub ? "<em>" + E(sub) + "</em>" : "") + "</label>";
  }

  function value(key) {
    if (Object.prototype.hasOwnProperty.call(S.edits, key)) return S.edits[key];
    var v = ((S.data || {}).values || {})[key];
    return v == null ? "" : String(v);
  }

  function recentRows(rows, window_) {
    if (!rows || !rows.length) {
      return '<p class="cxlede" style="margin:0">No model requests are logged yet. ' +
             "The table fills in as soon as a conversation runs.</p>";
    }
    var out = '<div class="cxwrap"><table class="cxt"><thead><tr>' +
      "<th>Time</th><th>Prompt</th><th>Cached</th><th>Prefill</th><th>Compacted</th>" +
      "</tr></thead><tbody>";
    for (var i = rows.length - 1; i >= 0; i--) {
      var r = rows[i];
      var lv = level(r.pct_of_window);
      out += "<tr><td>" + E(clock(r.epoch)) + "</td>" +
        '<td class="' + (lv ? "is-" + lv : "") + '">' + E(full(r.prompt_tokens)) + "</td>" +
        "<td>" + E(r.cache_pct == null ? "—" : pct(r.cache_pct)) + "</td>" +
        "<td>" + E(r.prefill_s == null ? "—" : secs(r.prefill_s)) + "</td>" +
        '<td class="' + (r.compacted ? "" : "is-muted") + '">' +
        E(r.compacted ? "yes" : "no") + "</td></tr>";
    }
    return out + "</tbody></table></div>";
  }

  function cardHTML(state) {
    state = state || {};
    var d = state.data;
    if (!d) {
      return CSS() + "<h2>" + GLY_CTX + "Context &amp; compaction</h2>" +
        '<div class="body"><p class="cxlede">Reading the compression settings…</p></div>';
    }
    if (d.ok === false) {
      return CSS() + "<h2>" + GLY_CTX + "Context &amp; compaction</h2>" +
        '<div class="body"><p class="cxerr">' +
        E(state.err || d.error || "Could not read the compression settings.") +
        "</p></div>";
    }

    var v = d.values || {};
    var lim = d.limits || {};
    var aux = d.auxiliary || {};
    var auxName = String(aux.effective || "—").split("/").pop();

    var h = CSS() + "<h2>" + GLY_CTX + "Context &amp; compaction</h2><div class=\"body\">";

    h += '<p class="cxlede">A conversation grows until it fills the model\'s ' +
      "window. Before it does, the agent summarises the earlier part of it and " +
      "carries on — that is a compaction, and it is the moment detail gets lost. " +
      "These four settings decide when it happens and how much survives.</p>";

    h += '<div class="cxstats">' +
      stat(full(d.window), "window (tokens)") +
      stat(full(d.compact_at_tokens), "compacts at") +
      stat(full(d.target_tokens), "summary target") +
      stat(auxName, "summarised by", true) +
      "</div>";

    // both previews follow what is TYPED, not what is saved, so the sentence
    // under the box always describes the number in it
    var thr = Number(value("threshold"));
    if (!isFinite(thr) || thr <= 0) thr = Number(v.threshold) || 0;
    var tgt = Number(value("target_ratio"));
    if (!isFinite(tgt) || tgt <= 0) tgt = Number(v.target_ratio) || 0;
    var atTok = Math.round(d.window * thr);

    h += '<div class="cxknobs">' +
      knobHTML("threshold", value("threshold"), lim.threshold,
               "compacts at " + full(atTok) + " tokens") +
      knobHTML("target_ratio", value("target_ratio"), lim.target_ratio,
               "about " + full(Math.round(atTok * tgt)) + " tokens of summary") +
      knobHTML("protect_last_n", value("protect_last_n"), lim.protect_last_n, "") +
      knobHTML("protect_first_n", value("protect_first_n"), lim.protect_first_n, "") +
      "</div>";

    h += '<div class="cxbar"><button class="cxb" data-act="apply"' +
      (state.busy ? " disabled" : "") + ">" +
      (state.busy ? "Applying…" : "Apply") + "</button>" +
      '<span style="font-size:11.5px;color:var(--muted)">Applies to ' +
      E(d.applies_to || "new conversations") + "</span></div>";

    if (state.err) h += '<p class="cxerr">' + E(state.err) + "</p>";

    if (state.result) {
      var r = state.result;
      if (!r.changed) {
        h += '<p class="cxres">Already set to those values — ' +
          "<b>nothing was written</b>.</p>";
      } else {
        var parts = [];
        for (var i = 0; i < KEYS.length; i++) {
          var k = KEYS[i];
          if (String(r.before[k]) !== String(r.after[k])) {
            parts.push(E(LABELS[k]) + " <b>" + E(r.before[k]) + " &rarr; " +
                       E(r.after[k]) + "</b>");
          }
        }
        h += '<p class="cxres">' + (parts.join(" · ") || "Saved.") +
          ". The next new conversation uses them; open ones keep what they started with.</p>";
      }
    }

    h += '<h3 class="cxh">Recent turns</h3>' +
      recentRows((state.recent || {}).turns, d.window);

    h += '<p class="cxfoot">Read from the model server\'s own log — no model is ' +
      "started to fill this in. Compaction itself runs on " +
      E(aux.effective || "the main model") +
      (aux.inherits_main ? " (the main model — " : " (") +
      E(aux.inherits_main ? "no auxiliary.compression.model is set)" :
        "set as auxiliary.compression.model)") +
      ". Written to <span class=\"cxcode\">compression</span> in " +
      "<span class=\"cxcode\">config.yaml</span> after a timestamped backup.</p>";

    return h + "</div>";
  }

  // ---- mount ---------------------------------------------------------------
  function wire(card) {
    if (!card || !card.querySelectorAll) return;
    Array.prototype.slice.call(card.querySelectorAll("input[data-k]"))
      .forEach(function (el) {
        el.oninput = function () {
          S.edits[el.getAttribute("data-k")] = el.value;
          S.result = null;
        };
      });
    var b = card.querySelector('button[data-act="apply"]');
    if (b) b.onclick = function () { apply(); };
  }

  var relocatedOnce = false;

  function paint() {
    var d = D(); if (!d) return;
    var panel = d.getElementById(PANEL_ID);
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
    // repainting blows away focus, so never repaint under the user's cursor
    var focused = null;
    try {
      var a = d.activeElement;
      if (a && el.contains(a) && a.getAttribute) focused = a.getAttribute("data-k");
    } catch (e) {}
    try { el.innerHTML = cardHTML(S); } catch (e) { return; }
    wire(el);
    if (focused) {
      try {
        var again = el.querySelector('input[data-k="' + focused + '"]');
        if (again) { again.focus(); again.select(); }
      } catch (e) {}
    }
    if (!relocatedOnce) {
      relocatedOnce = true;
      try { if (typeof W.settingsRelocate === "function") W.settingsRelocate(); } catch (e) {}
    }
  }

  async function load() {
    var j = await jget("/api/context/compression");
    if (!j) {
      S.err = "The dashboard did not answer /api/context/compression.";
      S.loaded = true;
      return;
    }
    S.data = j;
    S.err = (j.ok === false) ? String(j.error || "Could not read the settings.") : "";
    S.edits = {};
    S.loaded = true;
  }

  async function loadRecent() {
    var j = await jget("/api/context/recent?n=10");
    if (j && j.ok !== false) S.recent = j;
  }

  async function apply() {
    var body = {};
    for (var i = 0; i < KEYS.length; i++) {
      var k = KEYS[i];
      var raw = value(k);
      if (raw === "" || raw == null) continue;
      var n = Number(raw);
      if (!isFinite(n)) { S.err = LABELS[k] + " must be a number."; paint(); return; }
      body[k] = n;
    }
    S.busy = true; S.err = ""; S.result = null;
    paint();

    var j = await jpost("/api/context/compression", body);
    S.busy = false;
    if (!j || j.ok === false) {
      S.err = (j && j.error) ? String(j.error) : "The change could not be written.";
      paint();
      return;
    }
    S.result = { before: j.before, after: j.after, changed: !!j.changed };
    if (j.compression) { S.data = j.compression; S.edits = {}; }
    paint();
    try {
      if (typeof W.toast === "function") {
        W.toast(j.changed ? "Compaction settings updated" : "Compaction settings unchanged");
      }
    } catch (e) {}
  }

  var loading = false;

  async function mount() {
    var d = D();
    if (!d || !d.getElementById(PANEL_ID)) return;
    if (!S.loaded && !loading) {
      loading = true;
      paint();
      try { await load(); }
      catch (e) { S.err = "Could not read the compression settings."; S.loaded = true; }
      loading = false;
    }
    if (!S.recent) { try { await loadRecent(); } catch (e) {} }
    paint();
  }

  // chain window.mindExtras exactly like the other aux settings cards
  var prev = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await mount(); } catch (e) {}
  };

  // Wrap now, so the hook exists even if nothing else ever runs — and wrap
  // AGAIN once the DOM is ready. aux_agent.js wraps setAgentState from its own
  // DOMContentLoaded boot, i.e. after this file's body: without the second
  // pass its hero and status dispatch would be handed the gateway's raw
  // "🗜️ Compacting context…" string, emoji and all. Registering the listener
  // here means it fires after aux_agent's (listeners run in registration
  // order), which puts the emoji-stripping wrapper on the outside. Wrapping
  // twice is harmless: the inner pass sees text that is already normalised,
  // and `__ctxWrapped` stops a third.
  try { install(); } catch (e) {}
  try {
    var doc0 = D();
    if (doc0 && doc0.readyState === "loading") {
      doc0.addEventListener("DOMContentLoaded", function () {
        try { install(); } catch (e) {}
      });
    }
    if (typeof W.setTimeout === "function") {
      W.setTimeout(function () { try { install(); } catch (e) {} }, 2000);
    }
  } catch (e) {}

  // headless-harness surface (also handy from the console)
  W.hermesContext = {
    cardHTML: cardHTML, CSS: CSS, chipCSS: chipCSS,
    chipText: chipText, chipTitle: chipTitle, noteText: noteText, level: level,
    tok: tok, full: full, pct: pct, secs: secs, clock: clock,
    renderChip: renderChip, renderNote: renderNote, measure: measure,
    renderNotMeasured: renderNotMeasured, markStale: markStale,
    install: install, mount: mount, paint: paint, apply: apply, state: S,
    last: function () { return LAST; },
    refresh: async function () { S.loaded = false; S.recent = null; await mount(); }
  };
})();
