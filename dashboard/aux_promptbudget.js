// aux_promptbudget.js — the "Prompt budget" card for Settings › Agent & Models.
//
// What it shows: the fixed prefix every FRESH conversation prefills before the
// first token — measured, not guessed — and the one knob that shrinks it. On
// this Mac that prefix is ~20.5k tokens and prefill is compute-bound at ~750
// tok/s, so it is 25-28 seconds of waiting, once per new conversation. The tool
// schemas are the biggest single item in it, bigger than the whole system
// prompt, and they are pure config.
//
// PLACEMENT. Same constraint aux_network.js documents: aux_settings_shell.js
// builds its rail and its twelve panels ONCE from a PANELS array captured in its
// IIFE, ensureShell() early-returns on the second call, and
// window.SETTINGS_PANELS is a read-out, not a hook — so a module cannot register
// a new Settings panel. This is therefore a CARD, mounted DIRECTLY into
// #sec-models (Agent & Models), the panel that already owns the model, its
// memory ceiling and its thinking toggle. It deliberately does NOT fall back to
// #view-mind: the shell's relocator sends an unknown id to sec-system, which
// would silently file this card under System & Data. If the panel is not built
// yet we simply wait for the next mindExtras() pass.
//
// Design laws (CLAUDE.md): zero emoji — including in the toolset labels, whose
// CLI-side names ship with one (the server strips it) — 12-hour clock, esc() on
// every interpolation, tabular numerals on every number, explicit
// transition-property, >=40px targets, colours only through tokens all four
// palette blocks re-declare, and every global helper typeof-guarded so this file
// can be eval'd in a headless harness.
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

  var CARD_ID = "mind-extra-promptbudget";
  var PANEL_ID = "sec-models";
  var SEL = "#" + CARD_ID;

  var S = {
    data: null,       // the GET /api/prompt/budget payload
    loaded: false,
    busy: false,      // an apply is in flight
    err: "",
    choice: null,     // "lean" | "full" | "custom" — null until first paint
    picked: null,     // Set-like object {key: true} for the Advanced checkboxes
    open: false,      // Advanced disclosure state (survives repaints)
    restart: false,   // "restart the agent service too" checkbox
    result: null      // {before, after, changed} of the last apply
  };

  // ---- glyph (two-tone: accent fill + currentColor stroke; zero emoji) ------
  var GLY_BUDGET =
    '<svg class="pbic" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<rect x="2.8" y="5" width="18.4" height="14" rx="3.2" fill="var(--iris)" opacity=".14"/>' +
    '<rect x="2.8" y="5" width="18.4" height="14" rx="3.2" fill="none" ' +
    'stroke="currentColor" stroke-width="1.5"/>' +
    '<path d="M6.4 15.4V11M10.2 15.4V8.6M14 15.4v-3M17.8 15.4V9.8" fill="none" ' +
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';

  // ---- pure helpers (exported for the headless harness) --------------------
  function num(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return v.toLocaleString ? v.toLocaleString("en-US") : String(v);
  }

  function secs(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return (Math.round(v * 10) / 10).toFixed(1) + "s";
  }

  function kb(bytes) {
    var v = Number(bytes);
    if (!isFinite(v)) return "—";
    return (Math.round(v / 102.4) / 10).toFixed(1) + " KB";
  }

  // The keys a given choice would send. PURE — the harness drives every branch.
  function selectionFor(state) {
    var d = state.data || {};
    if (state.choice === "full") return null;                 // profile: full
    // "lean" is the wire name of the Balanced profile — it shipped under that
    // name and the config key is kept so an older config still resolves.
    if (state.choice === "lean") return (d.lean_profile || []).slice();
    if (state.choice === "focused") return (d.focused_profile || []).slice();
    var out = [];
    var rows = d.toolsets || [];
    for (var i = 0; i < rows.length; i++) {
      if (state.picked && state.picked[rows[i].key]) out.push(rows[i].key);
    }
    return out;
  }

  // Which of the three the current checkbox set corresponds to. Comparing SETS,
  // not order, so a hand-ticked list that happens to equal a named profile
  // reads as that profile rather than as a nameless Custom.
  function classify(picked, lean, keys, focused) {
    var on = [], i;
    for (i = 0; i < (keys || []).length; i++) if (picked[keys[i]]) on.push(keys[i]);
    var all = (keys || []).slice().sort().join(",");
    var sel = on.slice().sort().join(",");
    if (sel === (lean || []).slice().sort().join(",")) return "lean";
    if (sel === (focused || []).slice().sort().join(",")) return "focused";
    if (sel === all) return "full";
    return "custom";
  }

  // Estimated prefix for an arbitrary checkbox set, so the numbers move BEFORE
  // Apply. Deliberately additive over the per-toolset schema sizes: those are
  // measured standalone by the server and a couple of tools appear in two
  // toolsets, so this is an estimate for the preview only — the authoritative
  // before/after comes back from the POST, resolved by the agent itself.
  function estimate(data, picked) {
    if (!data || !data.current) return null;
    var rows = data.toolsets || [], i, r;
    var delta = 0, tools = 0, changed = false;
    for (i = 0; i < rows.length; i++) {
      r = rows[i];
      var want = !!(picked && picked[r.key]);
      if (want === !!r.enabled) continue;
      changed = true;
      delta += (want ? 1 : -1) * (Number(r.bytes) || 0);
      tools += (want ? 1 : -1) * (Number(r.tools) || 0);
    }
    if (!changed) return null;
    var bytes = (Number(data.current.tools_json_bytes) || 0) + delta;
    var bpt = Number(data.bytes_per_token) || 3.6;
    var rate = Number(data.prefill_tok_s) || 750;
    var sp = 0;
    try { sp = Number(data.prompt_size.system_prompt.bytes) || 0; } catch (e) { sp = 0; }
    var toks = Math.round((sp + bytes) / bpt);
    return {
      tools_json_bytes: bytes,
      tool_count: (Number(data.current.tool_count) || 0) + tools,
      est_tokens: toks,
      est_seconds: Math.round((toks / rate) * 10) / 10
    };
  }

  // ---- styles --------------------------------------------------------------
  function CSS() {
    return "<style>" +
      SEL + " .pbic{flex:0 0 auto;color:var(--muted)}" +
      SEL + " .pblede{margin:2px 0 14px;font-size:12.5px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty;max-width:64ch}" +
      // the measured prefix — flat stat cells, never cards inside a card
      SEL + " .pbstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));" +
        "gap:2px 18px;margin:0 0 16px;padding:0 0 14px;" +
        "border-bottom:1px solid var(--hairline)}" +
      SEL + " .pbstat b{display:block;font-size:19px;font-weight:640;color:var(--ink);" +
        "font-variant-numeric:tabular-nums;line-height:1.25}" +
      SEL + " .pbstat span{display:block;font-size:10.5px;letter-spacing:.05em;" +
        "text-transform:uppercase;color:var(--faint);margin-top:2px}" +
      SEL + " .pbstat.is-est b{color:var(--warn)}" +
      // three-way choice. A grid, not a flex row: three equal columns fit the
      // ~700px panel, and auto-fit collapses them cleanly on a narrow window
      // instead of leaving one option orphaned on its own line.
      // 165px, measured: the panel renders ~593px wide, and 3x190+2x10 = 590
      // is close enough to the limit that auto-fit dropped to two columns and
      // orphaned Focused on its own row.
      SEL + " .pbpick{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));" +
        "gap:10px;margin:0 0 12px}" +
      // align-content:start — the options are equal-height grid items, and
      // without it the shorter one's rows stretch to fill and the radio drifts
      // away from its own label.
      SEL + " label.pbopt{display:grid;grid-template-columns:18px minmax(0,1fr);" +
        "align-content:start;align-items:start;" +
        "gap:2px 10px;padding:11px 13px;border-radius:11px;cursor:pointer;" +
        "border:1px solid var(--hairline);background:var(--chip,rgba(255,255,255,.04));" +
        "transition-property:border-color,background-color;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " label.pbopt:hover{border-color:var(--iris)}" +
      SEL + " label.pbopt.is-on{border-color:var(--iris);" +
        "background:color-mix(in srgb,var(--iris) 10%,transparent)}" +
      SEL + " label.pbopt input{grid-row:1;margin:2px 0 0;accent-color:var(--iris)}" +
      SEL + " label.pbopt b{grid-column:2;font-size:12.5px;font-weight:640;color:var(--ink)}" +
      SEL + " label.pbopt span{grid-column:2;font-size:11.5px;line-height:1.45;" +
        "color:var(--muted);text-wrap:pretty}" +
      // em/.pbsub live INSIDE the option's <span>, so they are not grid items —
      // display:block is what stacks them, not grid-column.
      SEL + " label.pbopt em{display:block;margin-top:7px;" +
        "font-style:normal;font-size:12px;font-variant-numeric:tabular-nums;" +
        "color:var(--ink);font-weight:640}" +
      SEL + " label.pbopt .pbsub{display:block;margin-top:1px;font-style:normal;" +
        "font-size:11px;font-variant-numeric:tabular-nums;color:var(--faint)}" +
      // advanced disclosure
      SEL + " details.pbadv{margin:0 0 12px}" +
      SEL + " details.pbadv > summary{list-style:none;cursor:pointer;font-size:12px;" +
        "font-weight:620;color:var(--muted);padding:9px 0;display:flex;" +
        "align-items:center;gap:7px;min-height:40px;box-sizing:border-box}" +
      SEL + " details.pbadv > summary::-webkit-details-marker{display:none}" +
      SEL + " details.pbadv > summary::after{content:'';width:6px;height:6px;" +
        "border-right:1.6px solid currentColor;border-bottom:1.6px solid currentColor;" +
        "transform:rotate(45deg);margin-left:2px;" +
        "transition-property:transform;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " details.pbadv[open] > summary::after{transform:rotate(-135deg)}" +
      SEL + " ul.pbts{list-style:none;margin:0;padding:0}" +
      SEL + " ul.pbts li{border-top:1px solid var(--hairline)}" +
      SEL + " ul.pbts label{display:grid;grid-template-columns:18px minmax(0,1fr) auto;" +
        "gap:0 10px;align-items:center;padding:10px 2px;min-height:40px;" +
        "box-sizing:border-box;cursor:pointer}" +
      SEL + " ul.pbts input{accent-color:var(--iris);margin:0}" +
      SEL + " ul.pbts .pbname{font-size:12.5px;color:var(--ink);font-weight:600}" +
      SEL + " ul.pbts .pbdet{grid-column:2;font-size:11px;color:var(--faint);" +
        "line-height:1.4;text-wrap:pretty}" +
      SEL + " ul.pbts .pbsz{font-size:11.5px;color:var(--muted);" +
        "font-variant-numeric:tabular-nums;white-space:nowrap}" +
      SEL + " ul.pbts li.is-empty .pbname,"
          + SEL + " ul.pbts li.is-empty .pbsz{color:var(--faint)}" +
      // actions + result
      SEL + " .pbbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:4px 0 0}" +
      SEL + " button.pbb{padding:9px 16px;border-radius:10px;font-size:12.5px;" +
        "font-weight:600;cursor:pointer;border:0;background:var(--iris);color:#fff;" +
        "min-height:40px;transition-property:opacity;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " button.pbb:disabled{opacity:.45;cursor:default}" +
      SEL + " label.pbrs{display:flex;align-items:center;gap:7px;font-size:11.5px;" +
        "color:var(--muted);cursor:pointer;min-height:40px}" +
      SEL + " label.pbrs input{accent-color:var(--iris)}" +
      SEL + " .pbres{margin:12px 0 0;font-size:12px;line-height:1.55;color:var(--ink);" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " .pbres b{font-weight:640}" +
      SEL + " .pbwarn{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--warn)}" +
      SEL + " .pberr{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--bad)}" +
      SEL + " .pbfoot{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--hairline);" +
        "font-size:11px;line-height:1.55;color:var(--faint);text-wrap:pretty}" +
      SEL + " .pbcode{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;" +
        "padding:1px 5px;border-radius:5px;background:var(--chip,rgba(255,255,255,.05))}" +
      "</style>";
  }

  // ---- markup --------------------------------------------------------------
  function stat(value, label, est) {
    return '<div class="pbstat' + (est ? " is-est" : "") + '"><b>' + E(value) +
      "</b><span>" + E(label) + "</span></div>";
  }

  function optHTML(key, on, title, body) {
    return '<label class="pbopt' + (on ? " is-on" : "") + '">' +
      '<input type="radio" name="pbprofile" value="' + E(key) + '"' +
      (on ? " checked" : "") + ">" +
      "<b>" + E(title) + "</b><span>" + body + "</span></label>";
  }

  function cardHTML(state) {
    state = state || {};
    var d = state.data;
    if (!d) {
      return CSS() + "<h2>" + GLY_BUDGET + "Prompt budget</h2>" +
        '<div class="body"><p class="pblede">Measuring the prompt every fresh ' +
        "conversation pays…</p></div>";
    }
    if (d.ok === false) {
      return CSS() + "<h2>" + GLY_BUDGET + "Prompt budget</h2>" +
        '<div class="body"><p class="pberr">' + E(d.error || "Could not measure.") +
        "</p></div>";
    }

    var cur = d.current || {};
    var ps = d.prompt_size || {};
    var skIdx = (ps.skills_index || {});
    var est = estimate(d, state.picked || {});
    var live = est || cur;

    // A measurement that half failed still answers ok:true — the config and the
    // profile table are real — but the numbers are then NOT what the labels
    // say, so both failures are stated at the TOP of the body, never folded
    // into the collapsed Advanced section.
    //   prompt_size failed -> the system prompt was counted as zero, so the
    //     prefix/first-token figures are the tool schemas alone. Say so on the
    //     labels, not only in the notice.
    //   probe failed -> the toolset list and every per-toolset size are this
    //     module's fallback constants, not the agent's own answer.
    var psBad = !!d.prompt_size_error;
    var probeBad = !!d.probe_error;
    var notices = "";
    if (psBad) {
      notices += '<p class="pberr">The system prompt could not be measured, so ' +
        "the prefix and first-token figures below count the tool schemas only " +
        "— the real prefix is larger. <span class=\"pbcode\">hermes " +
        "prompt-size</span> said: " + E(d.prompt_size_error) + "</p>";
    }
    if (probeBad) {
      notices += '<p class="pberr">The agent’s tool registry could not be read, ' +
        "so the toolset list and its sizes are this dashboard’s built-in " +
        "fallback, not what your agent would load. The probe said: " +
        E(d.probe_error) + "</p>";
    }

    var tokLabel = psBad ? "tool schemas only" : "prefix tokens";
    var secLabel = psBad ? "schemas only" : "first token";
    var stats = '<div class="pbstats">' +
      stat(num(live.est_tokens), tokLabel, !!est || psBad) +
      stat(secs(live.est_seconds), secLabel, !!est || psBad) +
      stat(num(live.tool_count), "tools", !!est) +
      stat(kb(live.tools_json_bytes), "tool schemas", !!est) +
      stat(kb(skIdx.bytes), "skills index") +
      "</div>";

    // the three-way choice, straight off the server's profile table so the
    // card, the README and the plan doc quote one piece of arithmetic
    var order = d.profile_order || ["full", "lean", "focused"];
    var byKey = {};
    (d.profiles || []).forEach(function (r) { byKey[r.key] = r; });
    var pick = '<div class="pbpick">' + order.map(function (k) {
      var r = byKey[k];
      if (!r || r.error) return "";
      var saved = Number(r.pct_saved) || 0;
      return optHTML(k, state.choice === k, r.label,
        E(r.gives_up || "") +
        '<em>' + E(num(r.est_tokens)) + " tokens · ~" + E(secs(r.est_seconds)) +
        (saved > 0 ? " · &minus;" + E(saved.toFixed(0)) + "%" : "") + "</em>" +
        '<i class="pbsub">' + E(num(r.tool_count)) + " tools · " +
        E(kb(r.tools_json_bytes)) + " of schema</i>");
    }).join("") + "</div>";

    // advanced: one checkbox per configurable toolset
    var rows = (d.toolsets || []).slice().sort(function (a, b) {
      return (Number(b.bytes) || 0) - (Number(a.bytes) || 0) ||
             String(a.label).localeCompare(String(b.label));
    });
    var list = rows.map(function (r) {
      var on = !!(state.picked && state.picked[r.key]);
      var empty = !(Number(r.bytes) > 0);
      return '<li class="' + (empty ? "is-empty" : "") + '"><label>' +
        '<input type="checkbox" data-ts="' + E(r.key) + '"' + (on ? " checked" : "") + ">" +
        '<span class="pbname">' + E(r.label) + "</span>" +
        '<span class="pbsz">' + E(empty ? "not available" : kb(r.bytes)) + "</span>" +
        (r.detail ? '<span class="pbdet">' + E(r.detail) + "</span>" : "") +
        "</label></li>";
    }).join("");
    // The rows are ALWAYS non-empty (the server falls back to a static key
    // list when the probe dies), so `rows.length` could never surface a probe
    // failure — `probe_ok` is what says whether these sizes were measured.
    var adv = '<details class="pbadv"' + (state.open ? " open" : "") + ">" +
      "<summary>Advanced — choose the toolsets yourself</summary>" +
      (probeBad
        ? '<p class="pberr">' + E(d.probe_error ||
            "The toolset list could not be read.") + "</p>"
        : "") +
      (rows.length
        ? '<ul class="pbts">' + list + "</ul>"
        : '<p class="pberr">The toolset list could not be read.</p>') +
      "</details>";

    // actions
    var dirty = !!est || (state.choice && state.choice !== d.profile);
    var bar = '<div class="pbbar">' +
      '<button class="pbb" data-act="apply"' +
      ((state.busy || !dirty) ? " disabled" : "") + ">" +
      (state.busy ? "Applying…" : "Apply") + "</button>" +
      '<label class="pbrs"><input type="checkbox" data-restart' +
      (state.restart ? " checked" : "") + ">Restart the agent service too</label>" +
      "</div>";

    var out = "";
    if (state.result) {
      var b = state.result.before || {}, a = state.result.after || {};
      if (!state.result.changed) {
        out = '<p class="pbres">Nothing to change — that is already the ' +
          "setting in use.</p>";
      } else {
        var savedT = (Number(b.est_tokens) || 0) - (Number(a.est_tokens) || 0);
        out = '<p class="pbres"><b>' + E(num(b.est_tokens)) + " tokens · " +
          E(secs(b.est_seconds)) + "</b> → <b>" + E(num(a.est_tokens)) +
          " tokens · " + E(secs(a.est_seconds)) + "</b>" +
          (savedT > 0
            ? " — " + E(num(savedT)) + " tokens off every new conversation."
            : ".") +
          "</p>";
      }
      if (state.result.restarted) {
        out += '<p class="pbwarn">The agent service was restarted.</p>';
      } else if (state.result.changed) {
        out += '<p class="pbwarn">' + E(d.restart_note || "") + "</p>";
      }
    }

    return CSS() +
      "<h2>" + GLY_BUDGET + "Prompt budget</h2>" +
      '<div class="body">' +
      '<p class="pblede">Every new conversation prefills the same fixed prompt ' +
      "before the model writes a word — the system prompt, the skills index " +
      "and a JSON schema for every tool the agent can call. Prefill is " +
      "compute-bound, so those tokens are seconds you wait, once per " +
      "conversation. The tool schemas are the largest part, and they are the " +
      "part you can turn off.</p>" +
      notices + stats + pick + adv + bar + out +
      (state.err ? '<p class="pberr">' + E(state.err) + "</p>" : "") +
      '<p class="pbfoot">Measured with <span class="pbcode">hermes prompt-size ' +
      '--platform tui --json</span> plus the agent’s own tool registry. ' +
      "Written to <span class=\"pbcode\">" + E(d.config_key || "") + "</span> in " +
      "your Hermes config, with a timestamped backup. It applies to background " +
      "runs (briefings, the watchtower) and to Hermes in a terminal as well " +
      "— they load the same tools.</p>" +
      "</div>";
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

  // Seed the checkbox set + the radio from what the server says is live.
  function seed(d) {
    var picked = {};
    (d.toolsets || []).forEach(function (r) { if (r.enabled) picked[r.key] = true; });
    S.picked = picked;
    S.choice = d.profile || "custom";
  }

  async function load(fresh) {
    var j = await jget("/api/prompt/budget" + (fresh ? "?fresh=1" : ""));
    if (!j) {
      S.err = "The dashboard did not answer /api/prompt/budget.";
      S.loaded = true;
      return;
    }
    S.data = j;
    S.err = (j.ok === false) ? String(j.error || "Could not measure.") : "";
    if (j.ok !== false) seed(j);
    S.loaded = true;
  }

  async function apply() {
    var d = S.data || {};
    if (S.restart) {
      var ok = true;
      try {
        if (typeof W.confirm === "function") {
          ok = W.confirm("Restart the agent service now?\n\nAny answer the " +
                         "agent is writing right now will be interrupted.");
        }
      } catch (e) { ok = true; }
      if (!ok) return;
    }
    S.busy = true;
    S.err = "";
    S.result = null;
    paint();

    var body;
    if (S.choice === "full") body = { profile: "full" };
    else body = { toolsets: selectionFor(S) };
    if (S.restart) body.restart = true;

    var j = await jpost("/api/prompt/budget", body);
    S.busy = false;
    if (!j || j.ok === false) {
      S.err = (j && j.error) ? String(j.error)
                             : "The change could not be written.";
      paint();
      return;
    }
    S.result = { before: j.before, after: j.after, changed: !!j.changed,
                 restarted: !!j.restarted };
    if (j.restart_error) S.err = "The service restart failed: " + j.restart_error;
    if (j.budget) { S.data = j.budget; seed(j.budget); }
    S.restart = false;
    paint();
    try {
      if (typeof W.toast === "function") {
        W.toast(j.changed ? "Prompt budget updated" : "Prompt budget unchanged");
      }
    } catch (e) {}
  }

  // ---- mount ---------------------------------------------------------------
  function wire(card) {
    if (!card || !card.querySelectorAll) return;
    var d = S.data || {};

    Array.prototype.slice.call(card.querySelectorAll('input[name="pbprofile"]'))
      .forEach(function (el) {
        el.onchange = function () {
          if (!el.checked) return;
          S.choice = el.value;
          var picked = {};
          if (el.value === "lean") {
            (d.lean_profile || []).forEach(function (k) { picked[k] = true; });
          } else if (el.value === "focused") {
            (d.focused_profile || []).forEach(function (k) { picked[k] = true; });
          } else {
            (d.toolsets || []).forEach(function (r) { picked[r.key] = true; });
          }
          S.picked = picked;
          S.result = null;
          paint();
        };
      });

    Array.prototype.slice.call(card.querySelectorAll("input[data-ts]"))
      .forEach(function (el) {
        el.onchange = function () {
          var k = el.getAttribute("data-ts");
          S.picked = S.picked || {};
          if (el.checked) S.picked[k] = true; else delete S.picked[k];
          S.open = true;
          S.result = null;
          var keys = (d.toolsets || []).map(function (r) { return r.key; });
          S.choice = classify(S.picked, d.lean_profile || [], keys,
                              d.focused_profile || []);
          paint();
        };
      });

    var det = card.querySelector("details.pbadv");
    if (det) det.ontoggle = function () { S.open = !!det.open; };

    var rs = card.querySelector("input[data-restart]");
    if (rs) rs.onchange = function () { S.restart = !!rs.checked; };

    var b = card.querySelector('button[data-act="apply"]');
    if (b) b.onclick = function () { apply(); };
  }

  var relocatedOnce = false;

  function paint() {
    var d = D(); if (!d) return;
    var panel = d.getElementById(PANEL_ID);
    // No panel yet => wait. Deliberately NOT falling back to #view-mind: the
    // shell's relocator sends an id it does not know to sec-system, and this
    // card belongs with the model settings, not with logs and backups.
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
    if (!S.loaded && !loading) {
      loading = true;
      paint();                       // the "measuring…" shell, so the panel is
      try { await load(false); }     // never empty while the probe runs
      catch (e) { S.err = "Could not measure the prompt budget."; S.loaded = true; }
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
  W.hermesPromptBudget = {
    cardHTML: cardHTML, CSS: CSS, estimate: estimate, classify: classify,
    selectionFor: selectionFor, num: num, kb: kb, secs: secs,
    mount: mount, paint: paint, apply: apply, state: S,
    refresh: async function () { S.loaded = false; await mount(); }
  };
})();
