// aux_toolbudget.js — the "Tool output budget" card for Settings › Agent & Models.
//
// The companion to Prompt budget. That card measures the FIXED prefix every
// fresh conversation pays; this one caps the VARIABLE cost paid per tool call
// for the rest of the conversation. Every tool result is appended to the
// transcript verbatim, so one read of a build log can spend a quarter of the
// window in a single turn — and every later turn in that conversation
// re-prefills it.
//
// PLACEMENT. Same constraint aux_network.js and aux_promptbudget.js document:
// aux_settings_shell.js builds its rail and its panels ONCE from a PANELS
// array captured in its IIFE and window.SETTINGS_PANELS is a read-out, not a
// hook — so a module cannot register a new Settings panel. This is a CARD,
// mounted directly into #sec-models, and because our <script> tag sits after
// aux_promptbudget.js our appendChild lands after theirs: the card renders
// immediately below Prompt budget and stays there (both modules only append
// when the element is not already a child of the panel body). It deliberately
// does NOT fall back to #view-mind — the shell's relocator files an unknown id
// under sec-system, which is wrong for this card.
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

  var CARD_ID = "mind-extra-toolbudget";
  var PANEL_ID = "sec-models";
  var SEL = "#" + CARD_ID;

  var S = {
    data: null,
    loaded: false,
    busy: false,
    err: "",
    note: "",
    restart: false      // "restart the agent backend too" checkbox
  };

  // ---- glyph (two-tone: accent fill + currentColor stroke; zero emoji) ------
  // A page with its middle folded out — the shape of what this card does.
  var GLY =
    '<svg class="tbic" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<path d="M5.2 3.6h9.1l4.5 4.5v11.3a1.6 1.6 0 0 1-1.6 1.6H5.2a1.6 1.6 0 0 1-1.6-1.6V5.2a1.6 1.6 0 0 1 1.6-1.6z" ' +
    'fill="var(--iris)" opacity=".14"/>' +
    '<path d="M5.2 3.6h9.1l4.5 4.5v11.3a1.6 1.6 0 0 1-1.6 1.6H5.2a1.6 1.6 0 0 1-1.6-1.6V5.2a1.6 1.6 0 0 1 1.6-1.6z" ' +
    'fill="none" stroke="currentColor" stroke-width="1.5"/>' +
    '<path d="M7.4 8h5M7.4 16.4h9.2" fill="none" stroke="currentColor" ' +
    'stroke-width="1.6" stroke-linecap="round"/>' +
    '<path d="M6.6 12.2h10.8" fill="none" stroke="currentColor" stroke-width="1.6" ' +
    'stroke-linecap="round" stroke-dasharray="2.4 2.6"/></svg>';

  // ---- pure helpers (exported for the headless harness) --------------------
  function num(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return v.toLocaleString ? v.toLocaleString("en-US") : String(v);
  }

  // 24000 -> "24k". Tool budgets are quoted in round thousands everywhere
  // (the four choices, the config key, the docs), so the short form is the
  // honest one and the exact number rides in the token line beneath it.
  function kchars(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    if (v < 1000) return String(Math.round(v));
    var k = v / 1000;
    return (k >= 10 ? Math.round(k) : Math.round(k * 10) / 10) + "k";
  }

  function pct(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return (Math.round(v * 10) / 10) + "%";
  }

  function bytes(n) {
    var v = Number(n);
    if (!isFinite(v) || v <= 0) return "";
    if (v < 1024) return v + " B";
    if (v < 1024 * 1024) return (Math.round(v / 102.4) / 10) + " KB";
    return (Math.round(v / (1024 * 104.8576)) / 10) + " MB";
  }

  // The one sentence the card exists to say. PURE — the harness drives every
  // branch without a DOM.
  //
  // THE EMPTY-LOG CLAIM. "Nothing has gone over budget today" was asserted
  // from an empty log whatever had emptied it — a plugin that never loaded, a
  // log the agent could not write, a file someone deleted — and the card's
  // other "is it live" signal is `restart_required`, which goes false on ANY
  // restart of the service, not on this plugin loading. So the reassuring
  // sentence is now spoken ONLY with positive evidence: `observed` is the
  // server's "a truncation was logged at or after the moment we enabled it".
  // With no evidence the card says what it actually knows.
  function savingsLine(data) {
    var st = (data && data.stats) || {};
    var t = st.today || {};
    var calls = Number(t.calls) || 0;
    if (!calls) {
      if (!(data && data.observed)) {
        return "No truncation recorded yet — cannot confirm the plugin is "
             + "running.";
      }
      return "Nothing has gone over budget today — tool results have all been "
           + "small enough to pass through untouched.";
    }
    var kept = Number(t.kept_chars) || 0;
    var orig = Number(t.orig_chars) || 0;
    var toks = Number(t.est_tokens_saved) || 0;
    var out = "Kept " + kchars(kept) + " of " + kchars(orig) + " chars across "
            + num(calls) + " tool call" + (calls === 1 ? "" : "s")
            + " today — about " + num(toks) + " tokens that never entered a "
            + "conversation.";
    var spills = Number(t.spills) || 0;
    if (spills > 0) {
      var sz = bytes(st.spill_bytes);
      out += " " + num(spills) + " full output" + (spills === 1 ? "" : "s")
           + " saved on disk" + (sz ? " (" + sz + ")" : "") + ", still readable "
           + "in full.";
    }
    return out;
  }

  // Which state the card is in. PURE.
  //
  // "unknown" is a real state, not a rounding of "off": when the config editor
  // could not be loaded the server sends enabled_in_config === null, and
  // offering an Install button over a state nobody has read would write to the
  // owner's Hermes config on a guess.
  function phase(data) {
    if (!data || data.ok === false) return "error";
    if (data.helper_error || data.enabled_in_config === null ||
        data.enabled_in_config === undefined) return "unknown";
    if (!data.installed || !data.enabled_in_config) return "setup";
    if (data.restart_required) return "pending";
    return "live";
  }

  // The copied-plugin-is-older-than-the-repo case. `install.linked` (a symlink
  // back into the checkout) can never go stale; a fallback COPY can, and then
  // the agent runs last release's plugin while this card describes this one.
  function staleHTML(data) {
    var inst = (data && data.install) || {};
    if (!inst.stale) return "";
    return '<p class="tbwarn">The installed copy of the plugin is older than ' +
      "the one in this version of Hermes Assistant. It was copied rather than " +
      "linked, so an update did not reach it — install it again to refresh " +
      "the copy.</p>";
  }

  // ---- styles --------------------------------------------------------------
  function CSS() {
    return "<style>" +
      SEL + " .tbic{flex:0 0 auto;color:var(--muted)}" +
      SEL + " .tblede{margin:2px 0 14px;font-size:12.5px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty;max-width:64ch}" +
      // measured strip — flat cells, never cards inside a card
      SEL + " .tbstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));" +
        "gap:2px 18px;margin:0 0 16px;padding:0 0 14px;" +
        "border-bottom:1px solid var(--hairline)}" +
      SEL + " .tbstat b{display:block;font-size:19px;font-weight:640;color:var(--ink);" +
        "font-variant-numeric:tabular-nums;line-height:1.25}" +
      SEL + " .tbstat span{display:block;font-size:10.5px;letter-spacing:.05em;" +
        "text-transform:uppercase;color:var(--faint);margin-top:2px}" +
      SEL + " .tbstat.is-off b{color:var(--faint)}" +
      // rows of controls
      SEL + " .tbrow{display:flex;align-items:center;justify-content:space-between;" +
        "gap:14px;padding:11px 0;min-height:40px;box-sizing:border-box;" +
        "border-top:1px solid var(--hairline)}" +
      SEL + " .tbrow:first-of-type{border-top:0}" +
      SEL + " .tbrow .tbl{min-width:0}" +
      SEL + " .tbrow .tbl b{display:block;font-size:12.5px;font-weight:620;color:var(--ink)}" +
      SEL + " .tbrow .tbl span{display:block;font-size:11.5px;line-height:1.45;" +
        "color:var(--muted);text-wrap:pretty;margin-top:2px}" +
      // switch — the shell's own look, rebuilt locally so this file has no
      // dependency on a class another module owns
      SEL + " .tbsw{position:relative;flex:0 0 auto;width:44px;height:26px;" +
        "border-radius:13px;border:1px solid var(--hairline);cursor:pointer;" +
        "background:var(--chip,rgba(255,255,255,.06));padding:0;" +
        "transition-property:background-color,border-color;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " .tbsw::after{content:'';position:absolute;top:2px;left:2px;width:20px;" +
        "height:20px;border-radius:50%;background:var(--ink);opacity:.55;" +
        "transition-property:transform,opacity;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " .tbsw[aria-checked=\"true\"]{background:var(--iris);border-color:var(--iris)}" +
      SEL + " .tbsw[aria-checked=\"true\"]::after{transform:translateX(18px);" +
        "background:#fff;opacity:1}" +
      SEL + " .tbsw:disabled{opacity:.45;cursor:default}" +
      // the four budget choices
      SEL + " .tbpick{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));" +
        "gap:8px;margin:10px 0 4px}" +
      SEL + " label.tbopt{display:block;padding:10px 12px;border-radius:11px;cursor:pointer;" +
        "border:1px solid var(--hairline);background:var(--chip,rgba(255,255,255,.04));" +
        "transition-property:border-color,background-color;transition-duration:150ms;" +
        "transition-timing-function:ease-out;min-height:40px;box-sizing:border-box}" +
      SEL + " label.tbopt:hover{border-color:var(--iris)}" +
      SEL + " label.tbopt.is-on{border-color:var(--iris);" +
        "background:color-mix(in srgb,var(--iris) 10%,transparent)}" +
      SEL + " label.tbopt input{position:absolute;opacity:0;pointer-events:none}" +
      SEL + " label.tbopt b{display:block;font-size:13px;font-weight:640;color:var(--ink);" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " label.tbopt span{display:block;font-size:11px;color:var(--muted);" +
        "font-variant-numeric:tabular-nums;margin-top:2px}" +
      SEL + " label.tbopt em{display:block;font-size:10.5px;color:var(--faint);" +
        "font-style:normal;font-variant-numeric:tabular-nums;margin-top:1px}" +
      // savings + notices
      SEL + " .tbsave{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--hairline);" +
        "font-size:12px;line-height:1.55;color:var(--ink);font-variant-numeric:tabular-nums;" +
        "text-wrap:pretty}" +
      SEL + " .tbtools{margin:6px 0 0;font-size:11px;color:var(--faint);" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " .tbwarn{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--warn);" +
        "text-wrap:pretty}" +
      SEL + " .tberr{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--bad);" +
        "text-wrap:pretty}" +
      SEL + " .tbok{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--muted)}" +
      SEL + " .tbbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:12px 0 0}" +
      SEL + " button.tbb{padding:9px 16px;border-radius:10px;font-size:12.5px;" +
        "font-weight:600;cursor:pointer;border:0;background:var(--iris);color:#fff;" +
        "min-height:40px;transition-property:opacity;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " button.tbb.is-quiet{background:var(--chip,rgba(255,255,255,.06));" +
        "color:var(--ink);border:1px solid var(--hairline)}" +
      SEL + " button.tbb:disabled{opacity:.45;cursor:default}" +
      SEL + " label.tbrs{display:flex;align-items:center;gap:7px;font-size:11.5px;" +
        "color:var(--muted);cursor:pointer;min-height:40px}" +
      SEL + " label.tbrs input{accent-color:var(--iris)}" +
      SEL + " .tbfoot{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--hairline);" +
        "font-size:11px;line-height:1.55;color:var(--faint);text-wrap:pretty}" +
      SEL + " .tbcode{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;" +
        "padding:1px 5px;border-radius:5px;background:var(--chip,rgba(255,255,255,.05))}" +
      SEL + "[data-off=\"1\"] .tbpick,"
          + SEL + "[data-off=\"1\"] .tbsave{opacity:.5}" +
      "</style>";
  }

  // ---- markup --------------------------------------------------------------
  function stat(value, label, off) {
    return '<div class="tbstat' + (off ? " is-off" : "") + '"><b>' + E(value) +
      "</b><span>" + E(label) + "</span></div>";
  }

  function sw(act, on, label, disabled) {
    return '<button class="tbsw" role="switch" data-act="' + E(act) + '" ' +
      'aria-checked="' + (on ? "true" : "false") + '" aria-label="' + E(label) + '"' +
      (disabled ? " disabled" : "") + "></button>";
  }

  function row(title, sub, control) {
    return '<div class="tbrow"><div class="tbl"><b>' + E(title) + "</b>" +
      (sub ? "<span>" + E(sub) + "</span>" : "") + "</div>" + control + "</div>";
  }

  function head() {
    return CSS() + "<h2>" + GLY + "Tool output budget</h2>";
  }

  function cardHTML(state) {
    state = state || {};
    var d = state.data;
    if (!d) {
      return head() + '<div class="body"><p class="tblede">Reading the tool ' +
        "output budget…</p></div>";
    }
    if (d.ok === false) {
      return head() + '<div class="body"><p class="tberr">' +
        E(d.error || "Could not read the tool output budget.") + "</p></div>";
    }

    var s = d.settings || {};
    var ph = phase(d);
    var lede =
      '<p class="tblede">A tool result goes into the conversation exactly as ' +
      "the tool produced it. One read of a long log, one chatty command, one " +
      "large page can spend a quarter of the context window in a single turn " +
      "— and every later turn in that conversation prefills it again. This " +
      "caps what any single result may spend. Over the cap, the start and the " +
      "end are kept, the middle is replaced by a note saying so, and the full " +
      "output is written to disk where the assistant can still read it.</p>";

    // --- unknown: we could not read whether it is switched on --------------
    // No Install button here. The state was never read, so "install and turn
    // on" would be a write decided by a failure.
    if (ph === "unknown") {
      return head() + '<div class="body">' + lede +
        '<p class="tberr">' + E(d.helper_error ||
          "Whether the tool budget is switched on in your Hermes config could " +
          "not be read, so nothing about it is shown here.") + "</p>" +
        '<p class="tbok">Nothing has been changed. Once that is fixed this ' +
        "card will show the real state; until then it will not offer to " +
        "install anything.</p>" + staleHTML(d) + "</div>";
    }

    // --- setup: the plugin is not installed or not enabled in the config ----
    if (ph === "setup") {
      var why = !d.installed
        ? "The budget runs inside the agent itself, as a plugin. It is not "
          + "installed yet."
        : "The plugin is installed but not switched on in your Hermes config.";
      return head() + '<div class="body">' + lede +
        staleHTML(d) +
        '<p class="tbok">' + E(why) + "</p>" +
        '<div class="tbbar"><button class="tbb" data-act="install"' +
        (state.busy ? " disabled" : "") + ">" +
        (state.busy ? "Installing…" : "Install and turn on") + "</button>" +
        '<label class="tbrs"><input type="checkbox" data-restart' +
        (state.restart ? " checked" : "") +
        ">Restart the agent backend too</label></div>" +
        (state.err ? '<p class="tberr">' + E(state.err) + "</p>" : "") +
        (state.note ? '<p class="tbok">' + E(state.note) + "</p>" : "") +
        '<p class="tbfoot">Installing links this copy of Hermes Assistant’s ' +
        '<span class="tbcode">hermes-plugins/tool-budget</span> into ' +
        '<span class="tbcode">~/.hermes/plugins</span> and adds it to ' +
        '<span class="tbcode">plugins.enabled</span> in your Hermes config, ' +
        "after making a timestamped backup of it.</p></div>";
    }

    // --- live (or waiting for the one restart that loads the plugin) --------
    var on = s.enabled !== false;
    var stats = '<div class="tbstats">' +
      stat(kchars(s.max_chars) + " chars", "per result", !on) +
      stat("~" + num(d.est_tokens), "tokens", !on) +
      stat(pct(d.pct_window), "of the window", !on) +
      stat(num(((d.stats || {}).today || {}).calls || 0), "trimmed today", !on) +
      "</div>";

    var choices = (d.choices || []).map(function (c) {
      var sel = Number(s.max_chars) === Number(c.chars);
      return '<label class="tbopt' + (sel ? " is-on" : "") + '">' +
        '<input type="radio" name="tbmax" value="' + E(c.chars) + '"' +
        (sel ? " checked" : "") + (on ? "" : " disabled") + ">" +
        "<b>" + E(kchars(c.chars)) + " chars</b>" +
        "<span>~" + E(num(c.est_tokens)) + " tokens</span>" +
        "<em>" + E(pct(c.pct_window)) + " of the window</em></label>";
    }).join("");

    var body =
      row("Cap tool results", "Off means every tool result enters the "
          + "conversation in full, however long it is.",
          sw("enabled", on, "Cap tool results")) +
      '<div class="tbpick">' + choices + "</div>" +
      row("Save the full output",
          "Writes the untrimmed result to ~/.hermes/dashboard/spill, kept for "
          + "seven days, so the assistant can read the rest on request.",
          sw("spill", s.spill !== false, "Save the full output", !on));

    var t = (d.stats || {}).today || {};
    var tools = Object.keys(t.tools || {}).sort(function (a, b) {
      return (t.tools[b] - t.tools[a]) || a.localeCompare(b);
    }).map(function (k) { return k + " " + t.tools[k]; }).join("  ·  ");

    var save = '<p class="tbsave">' + E(savingsLine(d)) + "</p>" +
      (tools ? '<p class="tbtools">' + E(tools) + "</p>" : "");

    var pending = "";
    if (ph === "pending") {
      pending = '<p class="tbwarn">' + E(d.restart_note || "") + "</p>" +
        '<div class="tbbar"><button class="tbb is-quiet" data-act="restart"' +
        (state.busy ? " disabled" : "") + ">" +
        (state.busy ? "Restarting…" : "Restart the agent backend") +
        "</button></div>";
    }

    return head() + '<div class="body"' + (on ? "" : ' data-off="1"') + ">" +
      lede + staleHTML(d) + stats + body + save + pending +
      (state.err ? '<p class="tberr">' + E(state.err) + "</p>" : "") +
      (state.note ? '<p class="tbok">' + E(state.note) + "</p>" : "") +
      '<p class="tbfoot">Enforced by the <span class="tbcode">tool-budget</span> ' +
      "plugin inside the agent, on the seam every tool result passes through, so " +
      "it applies to chat, Telegram and background runs alike. Size, on/off and " +
      "saving are read fresh on the next tool call — no restart. The agent’s " +
      "own per-tool caps still apply first (terminal 50k chars, read_file 100k, " +
      "web_extract 15k); this is the floor underneath them, and the only one " +
      "measured against your context window.</p></div>";
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

  async function load() {
    var j = await jget("/api/tool/budget");
    if (!j) {
      S.err = "The dashboard did not answer /api/tool/budget.";
      S.loaded = true;
      return;
    }
    S.data = j;
    S.err = (j.ok === false) ? String(j.error || "Could not read it.") : "";
    S.loaded = true;
  }

  async function send(body, okNote) {
    S.busy = true;
    S.err = "";
    S.note = "";
    paint();
    var j = await jpost("/api/tool/budget", body);
    S.busy = false;
    if (!j || j.ok === false) {
      S.err = (j && j.error) ? String(j.error) : "The change could not be saved.";
      await load();                       // re-sync, so an optimistic UI reverts
      paint();
      return null;
    }
    S.data = j;
    if (j.restart_error) S.err = "The service restart failed: " + j.restart_error;
    else if (okNote) S.note = okNote;
    paint();
    try {
      if (typeof W.toast === "function" && okNote) W.toast(okNote);
    } catch (e) {}
    return j;
  }

  // ---- mount ---------------------------------------------------------------
  function wire(card) {
    if (!card || !card.querySelectorAll) return;
    var d = S.data || {};
    var s = d.settings || {};

    Array.prototype.slice.call(card.querySelectorAll("button.tbsw"))
      .forEach(function (el) {
        el.onclick = function () {
          if (el.disabled || S.busy) return;
          var act = el.getAttribute("data-act");
          var next = el.getAttribute("aria-checked") !== "true";
          el.setAttribute("aria-checked", next ? "true" : "false");  // optimistic
          var patch = {};
          patch[act] = next;
          send(patch, act === "enabled"
            ? (next ? "Tool results will be capped" : "Tool results pass through in full")
            : (next ? "Full outputs will be saved" : "Full outputs will not be saved"));
        };
      });

    Array.prototype.slice.call(card.querySelectorAll('input[name="tbmax"]'))
      .forEach(function (el) {
        el.onchange = function () {
          if (!el.checked || S.busy) return;
          var v = Number(el.value);
          if (!isFinite(v) || v === Number(s.max_chars)) return;
          send({ max_chars: v }, "Budget set to " + kchars(v) + " chars");
        };
      });

    var rs = card.querySelector("input[data-restart]");
    if (rs) rs.onchange = function () { S.restart = !!rs.checked; };

    var ins = card.querySelector('button[data-act="install"]');
    if (ins) ins.onclick = function () {
      if (S.busy) return;
      var body = { install: true };
      if (S.restart) {
        var ok = true;
        try {
          if (typeof W.confirm === "function") {
            ok = W.confirm("Restart the agent backend now?\n\nAny answer the " +
                           "agent is writing right now will be interrupted.");
          }
        } catch (e) { ok = true; }
        if (!ok) return;
        body.restart = true;
      }
      S.restart = false;
      send(body, "Tool output budget installed");
    };

    var rb = card.querySelector('button[data-act="restart"]');
    if (rb) rb.onclick = function () {
      if (S.busy) return;
      var ok = true;
      try {
        if (typeof W.confirm === "function") {
          ok = W.confirm("Restart the agent backend now?\n\nAny answer the " +
                         "agent is writing right now will be interrupted.");
        }
      } catch (e) { ok = true; }
      if (!ok) return;
      send({ restart: true }, "Agent backend restarted");
    };
  }

  var relocatedOnce = false;

  function paint() {
    var doc = D(); if (!doc) return;
    var panel = doc.getElementById(PANEL_ID);
    // No panel yet => wait for the next mindExtras() pass. Deliberately NOT
    // falling back to #view-mind: the shell's relocator files an unknown id
    // under sec-system, and this card belongs beside Prompt budget.
    if (!panel) return;
    var body = panel.querySelector(".set-body") || panel;
    var el = doc.getElementById(CARD_ID);
    if (!el) {
      el = doc.createElement("section");
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
      try { if (typeof W.settingsRelocate === "function") W.settingsRelocate(); } catch (e) {}
    }
  }

  var loading = false;

  async function mount() {
    var doc = D();
    if (!doc || !doc.getElementById(PANEL_ID)) return;
    if (!S.loaded && !loading) {
      loading = true;
      paint();                            // the loading shell, never an empty panel
      try { await load(); }
      catch (e) { S.err = "Could not read the tool output budget."; S.loaded = true; }
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
  W.hermesToolBudget = {
    cardHTML: cardHTML, CSS: CSS, savingsLine: savingsLine, phase: phase,
    staleHTML: staleHTML, num: num, kchars: kchars, pct: pct, bytes: bytes,
    mount: mount, paint: paint, state: S,
    refresh: async function () { S.loaded = false; await mount(); }
  };
})();
