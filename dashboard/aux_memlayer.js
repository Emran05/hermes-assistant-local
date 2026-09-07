// aux_memlayer.js — the "What I know about you" card for Settings › Memory.
//
// The visible half of memory layer v1 (aux_memlayer.py). The agent's own
// MEMORY.md / USER.md are a frozen per-session snapshot; this is the other
// half — a facts store retrieved per message and injected as a tiny block at
// the end of the [context] preamble. The card exists so that "what does it
// know about me, and what is that costing me per turn" is answerable by
// looking, not by reading the source: every fact is listed, editable, pinnable
// and archivable, and the Preview box shows the EXACT bytes a given message
// would inject, with the token count.
//
// PLACEMENT. Same constraint aux_network.js and aux_promptbudget.js document:
// aux_settings_shell.js builds its rail and its twelve panels ONCE from a
// PANELS array captured in its IIFE, ensureShell() early-returns on the second
// call, and window.SETTINGS_PANELS is a read-out, not a hook — so a module
// cannot register a new Settings panel. This is therefore a CARD mounted
// DIRECTLY into #sec-memory (Memory & You-Model), next to the editable-memory
// card (mind-base-memory) and the You-Model card (mind-extra-youmodel) that the
// shell's CARD_MAP relocates there. It deliberately does NOT fall back to
// #view-mind: the relocator sends an unknown id to sec-system, which would file
// this card under System & Data.
//
// Design laws (CLAUDE.md): zero emoji, esc() on every interpolation, tabular
// numerals on every number, explicit transition-property, >=40px targets,
// colours only through tokens all four palette blocks re-declare, and every
// global helper typeof-guarded so this file can be eval'd in a headless
// harness. Repaints are SCOPED — the filter box and the preview box update
// only their own subtree, because a full innerHTML swap on every keystroke
// takes the caret with it.
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

  var CARD_ID = "mind-extra-memlayer";
  var PANEL_ID = "sec-memory";
  var SEL = "#" + CARD_ID;
  var BPT = 3.6;                      // bytes per token, the repo's constant

  var S = {
    facts: [],
    stats: null,
    settings: null,
    loaded: false,
    err: "",
    busy: false,
    filter: "",
    kind: "",                          // "" = all
    showArchived: false,
    editing: null,                     // fact id being edited inline
    adding: false,
    preview: { text: "", block: "", chars: 0, tokens: 0, facts: 0,
               episodic: 0, ran: false },
    imported: null                     // last import counts
  };

  // ---- glyph (two-tone: accent fill + currentColor stroke; zero emoji) ------
  var GLY =
    '<svg class="mlic" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="3.2" fill="var(--iris)" opacity=".5"/>' +
    '<circle cx="12" cy="12" r="8.4" fill="none" stroke="currentColor" ' +
    'stroke-width="1.5" stroke-dasharray="3.2 2.6"/>' +
    '<circle cx="12" cy="3.6" r="1.7" fill="var(--iris)"/></svg>';

  // ---- pure helpers (exported for the headless harness) --------------------
  function num(n) {
    var v = Number(n);
    if (!isFinite(v)) return "—";
    return v.toLocaleString ? v.toLocaleString("en-US") : String(v);
  }

  function toks(chars) {
    var v = Number(chars);
    if (!isFinite(v)) return "—";
    return num(Math.round(v / BPT));
  }

  // "3 days ago" / "just now" / "never". 12-hour clock is irrelevant here —
  // this is an age, and an age is the only thing the ranking cares about.
  function ago(ts) {
    var v = Number(ts);
    if (!isFinite(v) || v <= 0) return "never";
    var d = (Date.now() / 1000) - v;
    if (d < 90) return "just now";
    if (d < 5400) return Math.round(d / 60) + " min ago";
    if (d < 129600) return Math.round(d / 3600) + " h ago";
    var days = Math.round(d / 86400);
    if (days < 31) return days + (days === 1 ? " day ago" : " days ago");
    var mo = Math.round(days / 30);
    if (mo < 24) return mo + (mo === 1 ? " month ago" : " months ago");
    return Math.round(mo / 12) + " years ago";
  }

  // What the list shows for the current filter. PURE — the harness drives it.
  function visible(state) {
    var q = String(state.filter || "").trim().toLowerCase();
    var kind = state.kind || "";
    return (state.facts || []).filter(function (f) {
      if (kind && f.kind !== kind) return false;
      if (!q) return true;
      return String(f.text || "").toLowerCase().indexOf(q) >= 0 ||
             String(f.kind || "").toLowerCase().indexOf(q) >= 0 ||
             String(f.source || "").toLowerCase().indexOf(q) >= 0;
    });
  }

  // ---- styles --------------------------------------------------------------
  function CSS() {
    return "<style>" +
      SEL + " .mlic{flex:0 0 auto;color:var(--muted)}" +
      SEL + " .mllede{margin:2px 0 14px;font-size:12.5px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty;max-width:64ch}" +
      SEL + " .mlstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(92px,1fr));" +
        "gap:2px 18px;margin:0 0 16px;padding:0 0 14px;" +
        "border-bottom:1px solid var(--hairline)}" +
      SEL + " .mlstat b{display:block;font-size:19px;font-weight:640;color:var(--ink);" +
        "font-variant-numeric:tabular-nums;line-height:1.25}" +
      SEL + " .mlstat span{display:block;font-size:10.5px;letter-spacing:.05em;" +
        "text-transform:uppercase;color:var(--faint);margin-top:2px}" +
      // budget row
      SEL + " .mlrow{display:flex;align-items:center;gap:12px;flex-wrap:wrap;" +
        "margin:0 0 12px;min-height:40px}" +
      SEL + " .mlsw{display:flex;align-items:center;gap:8px;font-size:12.5px;" +
        "font-weight:600;color:var(--ink);cursor:pointer;min-height:40px}" +
      SEL + " .mlsw input{accent-color:var(--iris);margin:0}" +
      SEL + " .mlbud{display:flex;gap:6px;flex-wrap:wrap}" +
      SEL + " label.mlb{display:inline-flex;flex-direction:column;gap:1px;" +
        "padding:7px 11px;border-radius:10px;cursor:pointer;min-height:40px;" +
        "box-sizing:border-box;justify-content:center;" +
        "border:1px solid var(--hairline);background:var(--chip,rgba(255,255,255,.04));" +
        "transition-property:border-color,background-color;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " label.mlb:hover{border-color:var(--iris)}" +
      SEL + " label.mlb.is-on{border-color:var(--iris);" +
        "background:color-mix(in srgb,var(--iris) 10%,transparent)}" +
      SEL + " label.mlb input{position:absolute;opacity:0;pointer-events:none}" +
      SEL + " label.mlb b{font-size:12px;font-weight:640;color:var(--ink);" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " label.mlb span{font-size:10.5px;color:var(--faint);" +
        "font-variant-numeric:tabular-nums}" +
      // controls
      SEL + " .mlctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;" +
        "margin:14px 0 8px;padding-top:14px;border-top:1px solid var(--hairline)}" +
      SEL + " input.mlin,"
          + SEL + " select.mlsel,"
          + SEL + " textarea.mlta{font:inherit;font-size:12.5px;color:var(--ink);" +
        "background:var(--chip,rgba(255,255,255,.05));border:1px solid var(--hairline);" +
        "border-radius:10px;padding:9px 11px;min-height:40px;box-sizing:border-box;" +
        "transition-property:border-color;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " input.mlin:focus,"
          + SEL + " select.mlsel:focus,"
          + SEL + " textarea.mlta:focus{outline:none;border-color:var(--iris)}" +
      SEL + " input.mlin{flex:1 1 180px;min-width:120px}" +
      // the preview box is its own row, not part of the .mlctl flex line
      SEL + " input.mlin.mlwide{display:block;width:100%;flex:none}" +
      SEL + " textarea.mlta{width:100%;resize:vertical;min-height:60px;line-height:1.5}" +
      SEL + " button.mlb2{padding:9px 14px;border-radius:10px;font-size:12.5px;" +
        "font-weight:600;cursor:pointer;border:1px solid var(--hairline);" +
        "background:var(--chip,rgba(255,255,255,.05));color:var(--ink);min-height:40px;" +
        "transition-property:border-color,opacity;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " button.mlb2:hover{border-color:var(--iris)}" +
      SEL + " button.mlb2.is-primary{border-color:transparent;background:var(--iris);" +
        "color:#fff}" +
      SEL + " button.mlb2:disabled{opacity:.45;cursor:default}" +
      // list
      SEL + " ul.mllist{list-style:none;margin:0;padding:0}" +
      SEL + " ul.mllist li{border-top:1px solid var(--hairline);padding:10px 2px;" +
        "display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;" +
        "align-items:start}" +
      SEL + " ul.mllist li.is-snap{opacity:.6}" +
      SEL + " ul.mllist li.is-arch .mltext{text-decoration:line-through;color:var(--faint)}" +
      SEL + " .mltext{grid-column:1;font-size:12.5px;line-height:1.5;color:var(--ink);" +
        "text-wrap:pretty;word-break:break-word}" +
      SEL + " .mlmeta{grid-column:1;display:flex;gap:6px;align-items:center;" +
        "flex-wrap:wrap;font-size:10.5px;color:var(--faint);" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " .mlchip{padding:1px 7px;border-radius:999px;font-size:10px;" +
        "font-weight:640;letter-spacing:.03em;text-transform:uppercase;" +
        "background:color-mix(in srgb,var(--iris) 14%,transparent);color:var(--ink)}" +
      SEL + " .mlchip.is-snap{background:var(--chip,rgba(255,255,255,.06));" +
        "color:var(--muted)}" +
      SEL + " .mlacts{grid-column:2;grid-row:1 / span 2;display:flex;gap:4px;" +
        "align-items:center}" +
      SEL + " button.mla{min-width:40px;min-height:40px;padding:0 9px;border-radius:9px;" +
        "border:1px solid transparent;background:transparent;cursor:pointer;" +
        "font-size:11px;font-weight:600;color:var(--muted);" +
        "transition-property:color,border-color;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " button.mla:hover{color:var(--ink);border-color:var(--hairline)}" +
      SEL + " button.mla.is-on{color:var(--iris)}" +
      SEL + " .mledit{grid-column:1 / -1;display:flex;flex-direction:column;gap:8px;" +
        "margin-top:6px}" +
      SEL + " .mledit .mlrow2{display:flex;gap:8px;align-items:center;flex-wrap:wrap}" +
      // preview
      SEL + " .mlprev{margin:16px 0 0;padding-top:14px;" +
        "border-top:1px solid var(--hairline)}" +
      SEL + " .mlh3{margin:0 0 8px;font-size:12.5px;font-weight:640;color:var(--ink)}" +
      SEL + " pre.mlblock{margin:10px 0 0;padding:11px 13px;border-radius:10px;" +
        "background:var(--chip,rgba(255,255,255,.05));border:1px solid var(--hairline);" +
        "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;" +
        "line-height:1.6;color:var(--ink);white-space:pre-wrap;word-break:break-word;" +
        "max-height:230px;overflow:auto}" +
      SEL + " .mlcost{margin:8px 0 0;font-size:11.5px;color:var(--muted);" +
        "font-variant-numeric:tabular-nums}" +
      SEL + " .mlnone{margin:10px 0 0;font-size:12px;color:var(--faint)}" +
      SEL + " .mlerr{margin:10px 0 0;font-size:11.5px;line-height:1.5;color:var(--bad)}" +
      SEL + " .mlfoot{margin:16px 0 0;padding-top:12px;" +
        "border-top:1px solid var(--hairline);font-size:11px;line-height:1.55;" +
        "color:var(--faint);text-wrap:pretty}" +
      SEL + " .mlcode{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;" +
        "padding:1px 5px;border-radius:5px;background:var(--chip,rgba(255,255,255,.05))}" +
      "</style>";
  }

  // ---- markup --------------------------------------------------------------
  function stat(value, label) {
    return '<div class="mlstat"><b>' + E(value) + "</b><span>" + E(label) +
      "</span></div>";
  }

  function budgetHTML(cfg, choices) {
    return '<div class="mlbud">' + (choices || []).map(function (n) {
      var on = Number(cfg.budget_chars) === Number(n);
      return '<label class="mlb' + (on ? " is-on" : "") + '">' +
        '<input type="radio" name="mlbudget" value="' + E(n) + '"' +
        (on ? " checked" : "") + ">" +
        "<b>" + (Number(n) === 0 ? "Off" : E(num(n)) + " chars") + "</b>" +
        "<span>" + (Number(n) === 0 ? "no memory injected"
                                    : "≈ " + E(toks(n)) + " tokens") +
        "</span></label>";
    }).join("") + "</div>";
  }

  function factHTML(f, editing) {
    var cls = [];
    if (f.in_snapshot) cls.push("is-snap");
    if (f.archived) cls.push("is-arch");
    var meta = '<div class="mlmeta">' +
      '<span class="mlchip' + (f.in_snapshot ? " is-snap" : "") + '">' +
      E(f.kind) + "</span>" +
      "<span>" + E(f.source) + "</span>" +
      "<span>·</span><span>used " + E(ago(f.last_used_ts)) +
      (Number(f.uses) > 0 ? " · " + E(num(f.uses)) + "×" : "") + "</span>" +
      (f.in_snapshot
        ? "<span>·</span><span>already in the system prompt — never injected here</span>"
        : "") +
      "</div>";

    if (editing) {
      return '<li class="' + cls.join(" ") + '" data-id="' + E(f.id) + '">' +
        '<div class="mledit">' +
        '<textarea class="mlta" data-edit-text>' + E(f.text) + "</textarea>" +
        '<div class="mlrow2">' +
        '<select class="mlsel" data-edit-kind>' +
        ["fact", "preference", "person", "project", "note"].map(function (k) {
          return '<option value="' + E(k) + '"' +
            (f.kind === k ? " selected" : "") + ">" + E(k) + "</option>";
        }).join("") + "</select>" +
        '<button class="mlb2 is-primary" data-act="save">Save</button>' +
        '<button class="mlb2" data-act="cancel">Cancel</button>' +
        "</div></div></li>";
    }

    return '<li class="' + cls.join(" ") + '" data-id="' + E(f.id) + '">' +
      '<div class="mltext">' + E(f.text) + "</div>" + meta +
      '<div class="mlacts">' +
      '<button class="mla' + (f.pinned ? " is-on" : "") + '" data-act="pin" ' +
      'title="' + (f.pinned ? "Unpin" : "Pin — always injected") + '">' +
      (f.pinned ? "Pinned" : "Pin") + "</button>" +
      '<button class="mla" data-act="edit">Edit</button>' +
      '<button class="mla" data-act="arch">' +
      (f.archived ? "Restore" : "Archive") + "</button>" +
      "</div></li>";
  }

  function listHTML(state) {
    var rows = visible(state);
    if (!rows.length) {
      return '<p class="mlnone">' +
        (state.facts && state.facts.length
          ? "Nothing matches that filter."
          : "No facts yet. Add one below, or press Import to pull in what is " +
            "already in your memory files and You-Model.") + "</p>";
    }
    return '<ul class="mllist">' + rows.map(function (f) {
      return factHTML(f, state.editing === f.id);
    }).join("") + "</ul>";
  }

  function previewHTML(state) {
    var p = state.preview || {};
    if (!p.ran) {
      return '<p class="mlnone">Type a message above to see exactly what it ' +
        "would inject.</p>";
    }
    if (!p.block) {
      return '<p class="mlnone">Nothing would be injected for that message.</p>';
    }
    return '<pre class="mlblock">' + E(p.block) + "</pre>" +
      '<div class="mlcost">' + E(num(p.chars)) + " chars · ≈ " +
      E(num(p.tokens)) + " tokens · " + E(num(p.facts)) +
      (p.facts === 1 ? " fact" : " facts") +
      (p.episodic ? " · " + E(num(p.episodic)) +
        (p.episodic === 1 ? " past conversation" : " past conversations") : "") +
      "</div>";
  }

  function cardHTML(state) {
    state = state || {};
    if (!state.loaded) {
      return CSS() + "<h2>" + GLY + "What I know about you</h2>" +
        '<div class="body"><p class="mllede">Reading your facts…</p></div>';
    }
    var cfg = state.settings || { enabled: true, budget_chars: 600, episodic: true };
    var st = state.stats || {};
    var choices = state.budget_choices || [0, 300, 600, 1200];

    var stats = '<div class="mlstats">' +
      stat(num(st.live), "injectable") +
      stat(num(st.pinned), "pinned") +
      stat(num(st.in_snapshot), "in system prompt") +
      stat(num(st.archived), "archived") +
      stat("≤ " + toks(cfg.budget_chars), "tokens per turn") +
      "</div>";

    var controls = '<div class="mlrow">' +
      '<label class="mlsw"><input type="checkbox" data-ml="enabled"' +
      (cfg.enabled ? " checked" : "") + ">Inject memory into every turn</label>" +
      '<label class="mlsw"><input type="checkbox" data-ml="episodic"' +
      (cfg.episodic ? " checked" : "") + ">Mention past conversations</label>" +
      "</div>" + budgetHTML(cfg, choices);

    var addRow = state.adding
      ? '<div class="mledit"><textarea class="mlta" data-add-text ' +
        'placeholder="One durable fact, in a sentence."></textarea>' +
        '<div class="mlrow2">' +
        '<select class="mlsel" data-add-kind>' +
        ["fact", "preference", "person", "project", "note"].map(function (k) {
          return '<option value="' + E(k) + '">' + E(k) + "</option>";
        }).join("") + "</select>" +
        '<label class="mlsw"><input type="checkbox" data-add-pin>Pin it</label>' +
        '<button class="mlb2 is-primary" data-act="add-save">Add</button>' +
        '<button class="mlb2" data-act="add-cancel">Cancel</button>' +
        "</div></div>"
      : "";

    var imported = "";
    if (state.imported) {
      var im = state.imported;
      imported = '<p class="mlcost">Imported: ' + E(num(im.added)) + " new, " +
        E(num(im.updated)) + " updated, " + E(num(im.same)) + " unchanged" +
        (Number(im.skipped) ? ", " + E(num(im.skipped)) + " skipped" : "") +
        ".</p>";
    }

    return CSS() +
      "<h2>" + GLY + "What I know about you</h2>" +
      '<div class="body">' +
      '<p class="mllede">A small set of durable facts. Before each message ' +
      "goes to the model, the ones that match it are looked up and added to " +
      "the end of the prompt as one short block — recent and pinned " +
      "facts first. It is deliberately tiny: every character here is prefilled " +
      "again on every turn. Facts the agent already carries in its own system " +
      "prompt are listed but never injected twice.</p>" +
      stats + controls +
      '<div class="mlctl">' +
      '<input class="mlin" type="search" data-ml-filter placeholder="Filter facts" ' +
      'value="' + E(state.filter) + '">' +
      '<select class="mlsel" data-ml-kind>' +
      [["", "all kinds"], ["fact", "fact"], ["preference", "preference"],
       ["person", "person"], ["project", "project"], ["note", "note"]]
        .map(function (p) {
          return '<option value="' + E(p[0]) + '"' +
            (state.kind === p[0] ? " selected" : "") + ">" + E(p[1]) + "</option>";
        }).join("") + "</select>" +
      '<label class="mlsw"><input type="checkbox" data-ml-arch' +
      (state.showArchived ? " checked" : "") + ">Archived</label>" +
      '<button class="mlb2" data-act="add"' + (state.adding ? " disabled" : "") +
      ">Add a fact</button>" +
      '<button class="mlb2" data-act="import"' + (state.busy ? " disabled" : "") +
      ">" + (state.busy ? "Importing…" : "Import") + "</button>" +
      "</div>" + imported + addRow +
      '<div data-ml-list>' + listHTML(state) + "</div>" +
      '<div class="mlprev">' +
      '<p class="mlh3">Preview for a message</p>' +
      '<input class="mlin mlwide" type="text" data-ml-prev ' +
      'placeholder="e.g. what should I work on today?" value="' +
      E(state.preview.text) + '">' +
      '<div data-ml-prevout>' + previewHTML(state) + "</div>" +
      "</div>" +
      (state.err ? '<p class="mlerr">' + E(state.err) + "</p>" : "") +
      '<p class="mlfoot">Stored in <span class="mlcode">~/.hermes/dashboard/' +
      "memory.db</span> (0600), searched with SQLite FTS5 — the same engine " +
      "as Search everything. No embeddings, no model call, nothing leaves this " +
      "Mac. Facts marked <em>agent</em> come from your memory files and are " +
      "read-only here; edit those in the Memory card above.</p>" +
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

  async function load() {
    var j = await jget("/api/memory/facts?include_archived=" +
                       (S.showArchived ? "1" : "0"));
    if (!j) {
      S.err = "The dashboard did not answer /api/memory/facts.";
      S.loaded = true;
      return;
    }
    if (j.ok === false) {
      S.err = String(j.error || "The memory store is unavailable.");
      S.loaded = true;
      return;
    }
    S.err = "";
    S.facts = j.facts || [];
    S.stats = j.stats || null;
    S.settings = j.settings || null;
    S.budget_choices = j.budget_choices || [0, 300, 600, 1200];
    if (j.bytes_per_token) BPT = Number(j.bytes_per_token) || BPT;
    S.loaded = true;
  }

  async function saveSettings(patch) {
    var j = await jpost("/api/memory/layer", patch);
    if (!j || j.ok === false) {
      S.err = (j && j.error) ? String(j.error) : "The setting could not be saved.";
    } else {
      S.err = "";
      S.settings = j.settings;
      S.stats = j.stats || S.stats;
    }
    paint();
    if (S.preview.ran) runPreview(S.preview.text, true);
  }

  var prevTimer = null;

  async function runPreview(text, now) {
    S.preview.text = text;
    if (prevTimer) { clearTimeout(prevTimer); prevTimer = null; }
    var go = async function () {
      if (!String(text || "").trim()) {
        S.preview.ran = false;
        S.preview.block = "";
        paintPreview();
        return;
      }
      var j = await jget("/api/memory/preview?text=" + encodeURIComponent(text));
      if (!j || j.ok === false) {
        S.preview.ran = true;
        S.preview.block = "";
        S.preview.chars = 0; S.preview.tokens = 0;
        S.preview.facts = 0; S.preview.episodic = 0;
      } else {
        S.preview.ran = true;
        S.preview.block = j.block || "";
        S.preview.chars = j.chars || 0;
        S.preview.tokens = j.tokens || 0;
        S.preview.facts = j.fact_count || 0;
        S.preview.episodic = j.episodic_count || 0;
      }
      paintPreview();
    };
    if (now) { await go(); return; }
    prevTimer = setTimeout(go, 300);
  }

  // ---- mount ---------------------------------------------------------------
  function paintList() {
    var d = D(); if (!d) return;
    var host = d.querySelector(SEL + " [data-ml-list]");
    if (!host) return;
    host.innerHTML = listHTML(S);
    wireList(host);
  }

  function paintPreview() {
    var d = D(); if (!d) return;
    var host = d.querySelector(SEL + " [data-ml-prevout]");
    if (host) host.innerHTML = previewHTML(S);
  }

  function factById(id) {
    for (var i = 0; i < S.facts.length; i++) {
      if (String(S.facts[i].id) === String(id)) return S.facts[i];
    }
    return null;
  }

  async function mutate(id, patch) {
    var j = await jpost("/api/memory/facts/update",
                        Object.assign({ id: Number(id) }, patch));
    if (!j || j.ok === false) {
      S.err = (j && j.error) ? String(j.error) : "That change did not save.";
      paint();
      return false;
    }
    S.err = "";
    S.stats = j.stats || S.stats;
    await load();
    return true;
  }

  function wireList(host) {
    if (!host || !host.querySelectorAll) return;
    Array.prototype.slice.call(host.querySelectorAll("li[data-id]"))
      .forEach(function (li) {
        var id = li.getAttribute("data-id");
        Array.prototype.slice.call(li.querySelectorAll("button[data-act]"))
          .forEach(function (b) {
            b.onclick = async function () {
              var act = b.getAttribute("data-act");
              var f = factById(id);
              if (!f) return;
              if (act === "pin") {
                if (await mutate(id, { pinned: !f.pinned })) paint();
              } else if (act === "arch") {
                if (await mutate(id, { archived: !f.archived })) paint();
              } else if (act === "edit") {
                S.editing = f.id;
                paintList();
              } else if (act === "cancel") {
                S.editing = null;
                paintList();
              } else if (act === "save") {
                var ta = li.querySelector("[data-edit-text]");
                var ks = li.querySelector("[data-edit-kind]");
                var patch = { text: ta ? ta.value : f.text };
                if (ks) patch.kind = ks.value;
                S.editing = null;
                if (await mutate(id, patch)) paint(); else paintList();
              }
            };
          });
      });
  }

  function wire(card) {
    if (!card || !card.querySelectorAll) return;

    Array.prototype.slice.call(card.querySelectorAll("input[data-ml]"))
      .forEach(function (el) {
        el.onchange = function () {
          var patch = {};
          patch[el.getAttribute("data-ml")] = !!el.checked;
          saveSettings(patch);
        };
      });

    Array.prototype.slice.call(card.querySelectorAll('input[name="mlbudget"]'))
      .forEach(function (el) {
        el.onchange = function () {
          if (!el.checked) return;
          saveSettings({ budget_chars: Number(el.value) });
        };
      });

    var flt = card.querySelector("[data-ml-filter]");
    if (flt) {
      flt.oninput = function () { S.filter = flt.value; paintList(); };
    }
    var kind = card.querySelector("[data-ml-kind]");
    if (kind) {
      kind.onchange = function () { S.kind = kind.value; paintList(); };
    }
    var arch = card.querySelector("[data-ml-arch]");
    if (arch) {
      arch.onchange = async function () {
        S.showArchived = !!arch.checked;
        await load();
        paint();
      };
    }
    var prev = card.querySelector("[data-ml-prev]");
    if (prev) {
      prev.oninput = function () { runPreview(prev.value); };
    }

    var addBtn = card.querySelector('button[data-act="add"]');
    if (addBtn) {
      addBtn.onclick = function () { S.adding = true; paint(); };
    }
    var addCancel = card.querySelector('button[data-act="add-cancel"]');
    if (addCancel) {
      addCancel.onclick = function () { S.adding = false; paint(); };
    }
    var addSave = card.querySelector('button[data-act="add-save"]');
    if (addSave) {
      addSave.onclick = async function () {
        var ta = card.querySelector("[data-add-text]");
        var ks = card.querySelector("[data-add-kind]");
        var pin = card.querySelector("[data-add-pin]");
        var text = ta ? String(ta.value || "").trim() : "";
        if (!text) return;
        var j = await jpost("/api/memory/facts", {
          text: text, kind: ks ? ks.value : "fact", pinned: !!(pin && pin.checked)
        });
        if (!j || j.ok === false) {
          S.err = (j && j.error) ? String(j.error) : "That fact was not added.";
          paint();
          return;
        }
        S.err = "";
        S.adding = false;
        await load();
        paint();
        try { if (typeof W.toast === "function") W.toast("Fact added"); } catch (e) {}
      };
    }

    var imp = card.querySelector('button[data-act="import"]');
    if (imp) {
      imp.onclick = async function () {
        S.busy = true;
        paint();
        var j = await jpost("/api/memory/facts/import", {});
        S.busy = false;
        if (!j || j.ok === false) {
          S.err = (j && j.error) ? String(j.error) : "The import failed.";
        } else {
          S.err = "";
          S.imported = { added: j.added || 0, updated: j.updated || 0,
                         same: j.same || 0, skipped: j.skipped || 0 };
          await load();
        }
        paint();
      };
    }

    wireList(card.querySelector("[data-ml-list]") || card);
  }

  var relocatedOnce = false;

  function paint() {
    var d = D(); if (!d) return;
    var panel = d.getElementById(PANEL_ID);
    // No panel yet => wait. Deliberately NOT falling back to #view-mind: the
    // shell's relocator sends an id it does not know to sec-system, and this
    // card belongs beside the memory editor, not with logs and backups.
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
      try { if (typeof W.settingsRelocate === "function") W.settingsRelocate(); } catch (e) {}
    }
  }

  var loading = false;

  async function mount() {
    var d = D();
    if (!d || !d.getElementById(PANEL_ID)) return;
    if (!S.loaded && !loading) {
      loading = true;
      paint();                       // the "reading…" shell, so the panel is
      try { await load(); }          // never empty while the fetch runs
      catch (e) { S.err = "Could not read the memory store."; S.loaded = true; }
      loading = false;
    }
    paint();
  }

  // chain window.mindExtras exactly like the other aux settings cards
  var prev2 = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev2 === "function") { try { await prev2(); } catch (e) {} }
    try { await mount(); } catch (e) {}
  };

  // headless-harness surface (also handy from the console)
  W.hermesMemLayer = {
    cardHTML: cardHTML, CSS: CSS, listHTML: listHTML, factHTML: factHTML,
    previewHTML: previewHTML, budgetHTML: budgetHTML, visible: visible,
    num: num, toks: toks, ago: ago,
    mount: mount, paint: paint, state: S,
    refresh: async function () { S.loaded = false; await mount(); }
  };
})();
