// aux_settings_shell.js — Settings page shell + relocator (UI-restructure B3).
//
// Auto-served at /aux_settings_shell.js.  Loaded LAST in index.html's aux list
// (immediately BEFORE aux_agent.js) so it wraps the whole window.mindExtras
// chain after every other aux module has appended its card.
//
// What it does — WITHOUT editing any existing aux module or expand.js:
//   1. Injects #set-shell INSIDE the existing #view-mind: a 236px sticky glass
//      nav rail (5 groups / 12 panels, sliding spring indicator, always-visible
//      kill switch) + main#set-panels holding the 12 settings panels.
//   2. THE RELOCATOR — moves every legacy Mind card (the 4 static base sections
//      + every [id^="mind-base-"] / [id^="mind-extra-"] card the aux modules
//      render into #view-mind) into its target panel per CARD_MAP.  Unknown ids
//      fall back to the System panel — never dropped.  Cards keep their existing
//      .card.glass markup untouched; only DOM nodes are moved.
//   3. Re-runs the relocation after every mindExtras() chain (expand.js recreates
//      the mind-extra cards each render) AND on a #view-mind childList
//      MutationObserver, so late async mounts are re-homed too.
//   4. Nav switching + spring indicator, hash routing (#settings/<panel>[@row]),
//      a search box (/ shortcut) indexing panel + card headings with a flash-ring
//      on the hit, and empty-panel placeholders.
//
// INVARIANTS: idempotent + fail-open (a relocate() exception must never break the
// Mind/Settings view — try/catch everywhere, degrade to today's flat cards).
// Removing this one <script> tag restores today's flat #view-mind exactly.
//
// Design laws (CLAUDE.md): zero emoji (bespoke two-tone SVG only), 12-hour time,
// Motion One animate()+SPRING with a REDUCE fallback, Liquid Glass.  All global
// helpers ($, esc, animate, SPRING, REDUCE, revealStagger, setView) are
// typeof-guarded so a headless render harness never throws.

(function () {
  "use strict";

  // ---- tiny helpers (all guarded for the headless harness) -----------------
  function doc() { return (typeof document !== "undefined") ? document : null; }
  function win() { return (typeof window !== "undefined") ? window : (typeof globalThis !== "undefined" ? globalThis : {}); }
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function RM() {
    if (typeof REDUCE !== "undefined") return !!REDUCE;
    try { return !!(win().matchMedia && win().matchMedia("(prefers-reduced-motion:reduce)").matches); } catch (e) { return false; }
  }
  function SP() { return (typeof SPRING !== "undefined") ? SPRING : "cubic-bezier(.22,1,.36,1)"; }
  function ANIM(el, kf, opt) {
    try { if (typeof animate === "function") return animate(el, kf, opt); } catch (e) {}
    try { if (el && el.animate) return el.animate(kf, opt); } catch (e) {}
    return null;
  }
  function each(list, cb) {
    if (!list) return;
    try { Array.prototype.slice.call(list).forEach(cb); } catch (e) {}
  }
  function byId(id) { var d = doc(); return d ? d.getElementById(id) : null; }

  // absolute 12-hour clock, e.g. "3:42 PM"
  function t12(d) {
    try {
      var h = d.getHours(), m = d.getMinutes();
      var ap = h >= 12 ? "PM" : "AM";
      h = h % 12; if (h === 0) h = 12;
      return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
    } catch (e) { return ""; }
  }

  // ==========================================================================
  // CARD_MAP — every legacy Mind card id → its Settings panel id.
  // Confirmed against the live DOM: these are the ids that actually appear as
  // direct children of #view-mind (4 static base sections + expand.js's three
  // mind-extra cards + one mind-extra card per aux module that mounts into
  // #view-mind).  Any id NOT in this map is relocated to sec-system (fail-open).
  // ==========================================================================
  var CARD_MAP = {
    // static base sections (index.html) --------------------------------------
    "mind-base-hero":      "sec-overview",   // greeting + 4 gstat counters (masthead)
    "mind-base-skills":    "sec-skills",     // #skill-list + category bar
    "mind-base-memory":    "sec-memory",     // aux_memory editor (richest card)
    "mind-base-activity":  "sec-insights",   // spark + tokens in/out + platforms
    // expand.js mindExtras() -------------------------------------------------
    "mind-extra-skills":   "sec-insights",   // skills-in-action leaderboard
    "mind-extra-models":   "sec-insights",   // model-mix donut
    "mind-extra-fuel":     "sec-insights",   // tokens/day + 14/30/60d drills
    // one card per aux module ------------------------------------------------
    "mind-extra-trust":    "sec-permissions",// 17/18-class Auto/Ask/Never matrix
    "mind-extra-shortcuts":"sec-permissions",// action-bus allowlist
    "mind-extra-youmodel": "sec-memory",     // goals/now/looking-for/interests/people
    "mind-extra-watchtower":"sec-proactive", // rules + brief schedule
    "mind-extra-google":   "sec-connections",// Gmail/Calendar status + wizard
    "mind-extra-config":   "sec-system",     // export/import snapshot
    // Documented future homes (these ids are NOT direct #view-mind children
    // today — aux_promotion's line lives inside the hero's .greet, #cu-cap is a
    // hub-widget input, aux_foryou/aux_messages are hub widgets — so they are
    // not moved by the DOM relocator.  Listed for when a module later mounts a
    // root card into #view-mind under one of these ids.)
    "mind-extra-promotion":"sec-models",
    "mind-extra-usage":    "sec-bridge",
    "mind-extra-foryou":   "sec-proactive",
    "mind-extra-messages": "sec-connections"
  };

  // ==========================================================================
  // Panel + group metadata (order matters — drives rail + panel build).
  // ==========================================================================
  var PANELS = [
    { id: "sec-overview",    short: "overview",    group: "",            title: "Overview",             sub: "Your assistant at a glance",              accent: "var(--iris)" },
    // Intelligence
    { id: "sec-models",      short: "models",      group: "Intelligence", title: "Agent & Models",      sub: "Local model, memory ceiling, downloads",  accent: "#7A6BEF" },
    { id: "sec-bridge",      short: "bridge",      group: "Intelligence", title: "Claude Bridge",       sub: "When and how the deep brain engages",     accent: "#D97757" },
    { id: "sec-memory",      short: "memory",      group: "Intelligence", title: "Memory & You-Model",  sub: "What it remembers about you",             accent: "#5B8DEF" },
    { id: "sec-skills",      short: "skills",      group: "Intelligence", title: "Skills",              sub: "Learned skills and what runs",            accent: "#C79A2E" },
    // Autonomy
    { id: "sec-permissions", short: "permissions", group: "Autonomy",     title: "Permissions & Trust", sub: "What can run without asking",             accent: "#D24C3C" },
    { id: "sec-proactive",   short: "proactive",   group: "Autonomy",     title: "Proactive",           sub: "Briefings, watchtower, notifications",    accent: "#2FA6A0" },
    // World
    { id: "sec-connections", short: "connections", group: "World",        title: "Connections",         sub: "Accounts and data access",               accent: "#2E9E68" },
    { id: "sec-sources",     short: "sources",     group: "World",        title: "Data & Sources",      sub: "Tickers, feeds, links, clocks",           accent: "#3E9BD6" },
    // Dashboard
    { id: "sec-appearance",  short: "appearance",  group: "Dashboard",    title: "Appearance & Layout", sub: "Theme, density, motion, widgets",         accent: "#9B6BEF" },
    { id: "sec-insights",    short: "insights",    group: "Dashboard",    title: "Insights",            sub: "Usage, fuel, model mix, activity",        accent: "#7E879B" },
    // System
    { id: "sec-system",      short: "system",      group: "System",       title: "System & Data",       sub: "Services, logs, backups, danger zone",    accent: "#8A93A6" }
  ];
  var PANEL_BY_ID = {}, PANEL_BY_SHORT = {};
  PANELS.forEach(function (p) { PANEL_BY_ID[p.id] = p; PANEL_BY_SHORT[p.short] = p; });
  var DEFAULT_SEC = "sec-overview";

  // Bespoke two-tone SVG glyphs (accent fill via --sac + currentColor stroke).
  // Zero emoji, per design law.
  var GLY = {
    "sec-overview":    '<rect x="3" y="3" width="8" height="8" rx="1.6" fill="var(--sac)" opacity=".35"/><rect x="13" y="3" width="8" height="8" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.6"/><rect x="3" y="13" width="8" height="8" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.6"/><rect x="13" y="13" width="8" height="8" rx="1.6" fill="var(--sac)" opacity=".35"/>',
    "sec-models":      '<rect x="6" y="6" width="12" height="12" rx="2" fill="var(--sac)" opacity=".3" stroke="currentColor" stroke-width="1.5"/><rect x="9.5" y="9.5" width="5" height="5" rx="1" fill="currentColor"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    "sec-bridge":      '<circle cx="5" cy="16" r="2.4" fill="currentColor"/><circle cx="19" cy="16" r="2.4" fill="var(--sac)"/><path d="M5 16C5 8.5 19 8.5 19 16" fill="none" stroke="currentColor" stroke-width="1.6"/>',
    "sec-memory":      '<circle cx="12" cy="12" r="3" fill="var(--sac)"/><circle cx="5" cy="6" r="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="19" cy="7" r="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="17" cy="18" r="1.8" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M9.3 10.5 6 7M14.8 11 17.4 8M13.4 14.4 16 17" fill="none" stroke="currentColor" stroke-width="1.3"/>',
    "sec-skills":      '<path d="M12 3l2.4 6.1 6.6.4-5.1 4.2 1.7 6.3L12 16.9 6.4 20.3l1.7-6.3L3 9.8l6.6-.4z" fill="var(--sac)" opacity=".35" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>',
    "sec-permissions": '<path d="M12 3 5 5.5v5c0 4.3 3 7.4 7 8.5 4-1.1 7-4.2 7-8.5v-5z" fill="var(--sac)" opacity=".3" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9 12l2 2 4-4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "sec-proactive":   '<path d="M12 3a5 5 0 0 0-5 5c0 5-2 6-2 6h14s-2-1-2-6a5 5 0 0 0-5-5z" fill="var(--sac)" opacity=".3" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M10 18.5a2 2 0 0 0 4 0" fill="none" stroke="currentColor" stroke-width="1.5"/>',
    "sec-connections": '<circle cx="7" cy="7" r="2.6" fill="var(--sac)" opacity=".45"/><path d="M9.5 14.5l5-5M13 7.5l1.3-1.3a3.3 3.3 0 0 1 4.7 4.7L17.5 12M11 16.5l-1.3 1.3a3.3 3.3 0 0 1-4.7-4.7L6.5 11.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    "sec-sources":     '<ellipse cx="12" cy="6" rx="7" ry="2.6" fill="var(--sac)" opacity=".35" stroke="currentColor" stroke-width="1.4"/><path d="M5 6v6c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M5 12v6c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6v-6" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    "sec-appearance":  '<circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M12 4a8 8 0 0 1 0 16z" fill="var(--sac)" opacity=".45"/>',
    "sec-insights":    '<path d="M4 20V4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><rect x="7" y="12" width="3.2" height="6" rx="1" fill="var(--sac)" opacity=".55"/><rect x="12" y="8" width="3.2" height="10" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/><rect x="17" y="5" width="3.2" height="13" rx="1" fill="var(--sac)" opacity=".55"/>',
    "sec-system":      '<rect x="4" y="4" width="16" height="6" rx="1.6" fill="var(--sac)" opacity=".3" stroke="currentColor" stroke-width="1.4"/><rect x="4" y="14" width="16" height="6" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="7.5" cy="7" r="1" fill="currentColor"/><circle cx="7.5" cy="17" r="1" fill="currentColor"/>'
  };
  function svg(inner, cls) { return '<svg class="' + (cls || "set-gly") + '" viewBox="0 0 24 24" aria-hidden="true">' + inner + "</svg>"; }
  var SEARCH_GLY = '<svg class="set-gly" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M20 20l-4-4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
  var POWER_GLY = '<svg class="set-gly" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v8" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><path d="M7.2 6.4a7 7 0 1 0 9.6 0" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>';

  // ==========================================================================
  // One-time CSS.
  // ==========================================================================
  function injectCss() {
    var d = doc(); if (!d || d.getElementById("set-shell-css")) return;
    var s = d.createElement("style");
    s.id = "set-shell-css";
    s.textContent = [
      "#set-shell{grid-column:1/-1;display:grid;grid-template-columns:236px minmax(0,1fr);gap:15px;align-items:start;min-height:100%}",
      // ---- rail ----
      "#set-rail{position:sticky;top:0;align-self:start;max-height:calc(100vh - 96px);display:flex;flex-direction:column;",
      "padding:10px 10px 8px;border-radius:16px;overflow:hidden}",
      "#set-rail>*{flex:none}",
      ".set-searchwrap{position:relative;margin-bottom:8px}",
      ".set-searchwrap .set-sicon{position:absolute;left:9px;top:50%;transform:translateY(-50%);width:14px;height:14px;color:var(--faint);pointer-events:none}",
      "#set-search{width:100%;box-sizing:border-box;padding:7px 10px 7px 30px;font-size:12.5px;border-radius:10px}",
      ".set-hits{position:absolute;left:0;right:0;top:calc(100% + 5px);z-index:40;max-height:300px;overflow:auto;",
      "padding:5px;border-radius:12px;background:var(--glass);-webkit-backdrop-filter:blur(18px) saturate(160%);backdrop-filter:blur(18px) saturate(160%);",
      "border:1px solid var(--hairline);box-shadow:0 12px 32px -12px var(--cast)}",
      ".set-hits[hidden]{display:none}",
      ".set-hit{display:flex;flex-direction:column;gap:1px;padding:6px 9px;border-radius:8px;cursor:pointer}",
      ".set-hit .h-t{font-size:12.5px;color:var(--ink);font-weight:560}",
      ".set-hit .h-s{font-size:10.5px;color:var(--faint)}",
      ".set-hit.on,.set-hit:hover{background:color-mix(in srgb,var(--iris) 14%,transparent)}",
      ".set-nohit{padding:8px 9px;font-size:12px;color:var(--muted)}",
      // ---- nav ----
      ".set-nav{position:relative;flex:1 1 auto;min-height:0;overflow-y:auto;padding:2px 0;margin:0 -2px}",
      ".set-glab{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);font-weight:640;padding:12px 10px 4px}",
      ".set-item{position:relative;z-index:1;display:flex;align-items:center;gap:9px;width:100%;box-sizing:border-box;",
      "padding:7px 10px;border:1px solid transparent;background:transparent;border-radius:10px;cursor:pointer;",
      "font-size:13px;font-weight:540;color:var(--muted);text-align:left;-webkit-backdrop-filter:none;backdrop-filter:none;transition:color .18s}",
      ".set-item:hover{color:var(--ink);transform:none;border-color:transparent;background:transparent}",
      ".set-item .set-gly{width:15px;height:15px;flex:0 0 auto;color:var(--faint);transition:color .18s}",
      ".set-item:hover .set-gly,.set-item.on .set-gly{color:var(--sac)}",
      ".set-item.on{color:var(--ink)}",
      ".set-item .set-ilbl{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".set-ind{position:absolute;left:0;right:0;top:0;height:0;z-index:0;border-radius:10px;",
      "background:var(--glass);box-shadow:inset 0 1px 0 var(--specular),inset 0 0 0 .5px var(--hairline),0 4px 14px -8px var(--cast);opacity:0}",
      ".set-ind.show{opacity:1}",
      // ---- kill switch ----
      ".set-kill{display:flex;align-items:center;gap:9px;margin-top:8px;padding:9px 11px;border-radius:12px;cursor:pointer;",
      "border:1px solid var(--hairline);background:var(--glass-2);transition:border-color .2s,background .2s}",
      ".set-kill:hover{border-color:color-mix(in srgb,var(--bad) 45%,transparent)}",
      ".set-kill .set-gly{width:17px;height:17px;flex:0 0 auto;color:var(--muted)}",
      ".set-killtext{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}",
      ".set-killttl{font-size:12.5px;font-weight:600;color:var(--ink)}",
      ".set-killcap{font-size:10.5px;color:var(--faint)}",
      ".set-killdot{width:8px;height:8px;border-radius:50%;flex:0 0 auto;background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 22%,transparent)}",
      ".set-kill.paused{border-color:color-mix(in srgb,var(--bad) 48%,transparent);background:color-mix(in srgb,var(--bad) 12%,transparent)}",
      ".set-kill.paused .set-gly{color:var(--bad)}",
      ".set-kill.paused .set-killdot{background:var(--bad);box-shadow:0 0 0 3px color-mix(in srgb,var(--bad) 22%,transparent)}",
      ".set-kill.busy .set-killdot{background:var(--warn);box-shadow:0 0 0 3px color-mix(in srgb,var(--warn) 22%,transparent)}",
      // ---- panels ----
      "#set-panels{min-width:0;display:block}",
      ".set-panel{display:block}",
      ".set-panel[hidden]{display:none}",
      ".set-head{display:flex;align-items:flex-start;gap:12px;padding:2px 2px 14px;border-bottom:1px solid var(--hairline);margin-bottom:15px}",
      ".set-htext{flex:1;min-width:0}",
      ".set-head h2{margin:0;font-size:19px;font-weight:680;letter-spacing:-.01em;color:var(--ink);display:flex;align-items:center;gap:9px;text-transform:none}",
      ".set-head h2 .set-gly{width:19px;height:19px;flex:0 0 auto;color:var(--sac)}",
      ".set-sub{margin:3px 0 0;font-size:12.5px;color:var(--muted)}",
      ".set-hact{flex:0 0 auto;display:flex;align-items:center;gap:8px}",
      ".set-body{display:flex;flex-direction:column;gap:15px;min-width:0}",
      // relocated legacy cards: neutralize the 2-col grid spans, go full width
      ".set-body>.set-legacy{grid-column:auto!important;width:auto;max-width:none;margin:0}",
      // empty placeholder
      ".set-empty{display:flex;flex-direction:column;align-items:center;gap:10px;padding:46px 20px;text-align:center;",
      "border:1px dashed var(--hairline);border-radius:16px;color:var(--muted)}",
      ".set-panel.has-cards .set-empty{display:none}",
      ".set-empty .set-gly{width:34px;height:34px;color:var(--sac);opacity:.4}",
      ".set-empty span{font-size:12.5px;max-width:280px}",
      // flash ring on search/anchor hit
      ".set-flash{animation:setflash 1.2s ease}",
      "@keyframes setflash{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--iris) 0%,transparent)}",
      "22%{box-shadow:0 0 0 3px color-mix(in srgb,var(--iris) 55%,transparent)}",
      "100%{box-shadow:0 0 0 0 color-mix(in srgb,var(--iris) 0%,transparent)}}",
      // narrow: rail becomes a horizontal chip row
      "@media (max-width:880px){#set-shell{grid-template-columns:1fr;gap:12px}",
      "#set-rail{position:static;max-height:none;flex-direction:row;flex-wrap:nowrap;overflow-x:auto;align-items:center;gap:6px}",
      ".set-searchwrap{flex:1 1 160px;margin:0}.set-hits{max-height:240px}",
      ".set-nav{display:flex;flex-direction:row;overflow-x:auto;overflow-y:visible;flex:1 1 auto}",
      ".set-ind{display:none}.set-glab{display:none}",
      ".set-item{width:auto;white-space:nowrap;flex:0 0 auto}.set-item .set-ilbl{max-width:none}",
      ".set-kill{margin-top:0;flex:0 0 auto}.set-killtext{display:none}}",
      "@media (prefers-reduced-motion:reduce){.set-flash{animation:none;outline:2px solid color-mix(in srgb,var(--iris) 55%,transparent);outline-offset:2px}}"
    ].join("\n");
    (d.head || d.body || d.documentElement).appendChild(s);
  }

  // ==========================================================================
  // Shell injection (idempotent).
  // ==========================================================================
  function ensureShell(host) {
    var d = doc(); if (!d || !host) return false;
    if (d.getElementById("set-shell")) return true;   // already built (or harness-provided)
    injectCss();

    // rail markup ------------------------------------------------------------
    var railInner = '<div class="set-searchwrap"><span class="set-sicon">' + SEARCH_GLY +
      '</span><input id="set-search" type="search" placeholder="Search settings" aria-label="Search settings" autocomplete="off" spellcheck="false">' +
      '<div class="set-hits" id="set-hits" role="listbox" hidden></div></div>' +
      '<div class="set-nav" id="set-nav" role="tablist" aria-orientation="vertical">' +
      '<span class="set-ind" id="set-ind" aria-hidden="true"></span>';
    var lastGroup = null;
    PANELS.forEach(function (p) {
      if (p.group && p.group !== lastGroup) railInner += '<div class="set-glab">' + E(p.group) + "</div>";
      lastGroup = p.group;
      railInner += '<button class="set-item" type="button" role="tab" data-sec="' + p.id +
        '" style="--sac:' + p.accent + '" aria-controls="' + p.id + '" aria-selected="false">' +
        svg(GLY[p.id] || "") + '<span class="set-ilbl">' + E(p.title) + "</span></button>";
    });
    railInner += "</div>" +
      '<div class="set-kill" id="set-kill" role="button" tabindex="0" aria-pressed="false" title="Pause the agent — unloads the model, dashboard chat and Telegram pause too">' +
      POWER_GLY +
      '<div class="set-killtext"><span class="set-killttl" id="set-killttl">Pause agent</span>' +
      '<span class="set-killcap" id="set-killcap">Agent is running</span></div>' +
      '<span class="set-killdot" id="set-killdot"></span></div>';

    // panels markup ----------------------------------------------------------
    var panelsInner = "";
    PANELS.forEach(function (p) {
      panelsInner +=
        '<section class="set-panel' + (p.id === DEFAULT_SEC ? "" : "") + '" id="' + p.id +
        '" data-short="' + p.short + '" style="--sac:' + p.accent + '" role="tabpanel" aria-labelledby="' + p.id + '-h"' +
        (p.id === DEFAULT_SEC ? "" : " hidden") + ">" +
        '<header class="set-head"><div class="set-htext"><h2 id="' + p.id + '-h">' + svg(GLY[p.id] || "") + E(p.title) +
        '</h2><p class="set-sub">' + E(p.sub) + "</p></div>" +
        '<div class="set-hact" data-set-hact></div></header>' +
        '<div class="set-body">' +
        '<div class="set-empty" aria-hidden="true">' + svg(GLY[p.id] || "") +
        "<span>These controls arrive in a later update. Nothing is missing &mdash; this panel just has no cards yet.</span></div>" +
        "<div data-legacy-slot></div></div></section>";
    });

    var shell = d.createElement("div");
    shell.id = "set-shell";
    shell.innerHTML =
      '<nav id="set-rail" class="glass" aria-label="Settings">' + railInner + "</nav>" +
      '<main id="set-panels">' + panelsInner + "</main>";
    host.appendChild(shell);

    wireRail(shell);
    return true;
  }

  // ==========================================================================
  // Nav wiring + panel switching + sliding indicator.
  // ==========================================================================
  var curSec = DEFAULT_SEC;
  var hashLock = false;

  function wireRail(shell) {
    var d = doc(); if (!d) return;
    each(shell.querySelectorAll(".set-item"), function (b) {
      b.addEventListener("click", function () { settingsShow(b.getAttribute("data-sec")); });
    });
    var kill = shell.querySelector("#set-kill");
    if (kill) {
      kill.addEventListener("click", onKill);
      kill.addEventListener("keydown", function (ev) {
        if (ev && (ev.key === "Enter" || ev.key === " ")) { if (ev.preventDefault) ev.preventDefault(); onKill(); }
      });
    }
    var search = shell.querySelector("#set-search");
    if (search) {
      search.addEventListener("input", function () { runSearch(search.value); });
      search.addEventListener("keydown", onSearchKey);
      search.addEventListener("focus", function () { if (search.value) runSearch(search.value); });
      search.addEventListener("blur", function () { setTimeout(hideHits, 140); });
    }
    // reflect the current kill state
    refreshKill();
  }

  function activeItem() {
    var d = doc(); if (!d) return null;
    return d.querySelector('.set-item[data-sec="' + curSec + '"]');
  }

  function moveIndicator(item, animateIt) {
    var ind = byId("set-ind");
    if (!ind || !item) return;
    var top = item.offsetTop, h = item.offsetHeight;
    if (typeof top !== "number" || !isFinite(top) || !h) { ind.classList.remove("show"); return; }
    ind.classList.add("show");
    if (RM() || animateIt === false || typeof top !== "number") {
      ind.style.top = top + "px"; ind.style.height = h + "px"; return;
    }
    if (ind.style.height === "0px" || ind.style.height === "") {
      // first placement — no travel animation, just size in
      ind.style.top = top + "px"; ind.style.height = h + "px"; return;
    }
    ind.style.height = h + "px";
    var a = ANIM(ind, { top: top + "px" }, { duration: 0.42, easing: SP() });
    if (!a) ind.style.top = top + "px";
  }

  function settingsShow(secOrShort, rowId) {
    try {
      var d = doc(); if (!d) return;
      var p = PANEL_BY_ID[secOrShort] || PANEL_BY_SHORT[secOrShort];
      var sec = p ? p.id : DEFAULT_SEC;
      curSec = sec;

      each(d.querySelectorAll(".set-panel"), function (pan) {
        pan.hidden = (pan.id !== sec);
      });
      each(d.querySelectorAll(".set-item"), function (b) {
        var on = b.getAttribute("data-sec") === sec;
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });

      var item = activeItem();
      moveIndicator(item, true);

      var panel = byId(sec);
      if (panel) {
        if (!RM()) {
          ANIM(panel, { opacity: [0, 1], transform: ["translateY(8px)", "none"] }, { duration: 0.28, easing: [0.22, 1, 0.36, 1] });
          try { if (typeof revealStagger === "function") revealStagger(panel.querySelectorAll(".set-legacy,.set-block,.card"), 60); } catch (e) {}
        }
      }

      // hash sync (guarded against feedback)
      hashLock = true;
      try {
        var newHash = "#settings/" + (p ? p.short : "overview") + (rowId ? "@" + rowId : "");
        if (win().location && win().location.hash !== newHash) win().location.hash = newHash;
      } catch (e) {}
      setTimeout(function () { hashLock = false; }, 0);

      if (rowId) flashRow(sec, rowId);
    } catch (e) {}
  }

  function flashRow(sec, rowId) {
    try {
      var el = byId(rowId) || byId(sec + "-" + rowId);
      if (!el) { var panel = byId(sec); if (panel && panel.querySelector) el = panel.querySelector('[data-row="' + rowId + '"]'); }
      if (!el) return;
      if (el.scrollIntoView) el.scrollIntoView({ block: "center", behavior: RM() ? "auto" : "smooth" });
      el.classList.remove("set-flash");
      // reflow to restart the animation
      void (el.offsetWidth);
      el.classList.add("set-flash");
      setTimeout(function () { el.classList.remove("set-flash"); }, 1300);
    } catch (e) {}
  }

  // ==========================================================================
  // THE RELOCATOR.
  // ==========================================================================
  function relocate() {
    var host = byId("view-mind");
    if (!host) return;
    if (!ensureShell(host)) return;

    // Move every direct-child element of #view-mind that is not the shell into
    // its target panel.  Idempotent: after the first pass the only direct child
    // left is #set-shell, so subsequent passes are no-ops until a module
    // re-appends a card at the root (which they do on every render).
    var kids = [];
    try { kids = Array.prototype.slice.call(host.children); } catch (e) { kids = []; }
    kids.forEach(function (n) {
      if (!n || n.nodeType !== 1) return;
      if (n.id === "set-shell") return;
      if (n.id === "set-shell-css") return;                 // stray style, leave
      if (n.tagName === "STYLE" || n.tagName === "SCRIPT") return;
      var dest = CARD_MAP[n.id] || "sec-system";            // unknown → System, never lost
      var slot = byId(dest);
      if (!slot) slot = byId("sec-system");
      if (!slot) return;
      var anchor = null;
      try { anchor = slot.querySelector ? slot.querySelector("[data-legacy-slot]") : null; } catch (e) { anchor = null; }
      try {
        if (anchor && slot.insertBefore) slot.insertBefore(n, anchor);
        else slot.appendChild(n);
        if (n.classList) n.classList.add("set-legacy");
      } catch (e) {}
    });

    markEmpty();
    try { buildSearchIndex(); } catch (e) {}
  }

  // toggle .has-cards on each panel so the empty placeholder shows only when a
  // panel truly holds no relocated (or native) card
  function markEmpty() {
    PANELS.forEach(function (p) {
      var panel = byId(p.id);
      if (!panel) return;
      var has = false;
      try {
        var cards = panel.querySelectorAll(".set-legacy,.card,.set-block");
        has = !!(cards && cards.length);
      } catch (e) { has = false; }
      if (panel.classList) panel.classList.toggle("has-cards", has);
    });
  }

  // ==========================================================================
  // Search — auto-index panel + card headings, flash-ring the hit.
  // ==========================================================================
  var registry = win().settingsRegistry = win().settingsRegistry || [];

  function buildSearchIndex() {
    // keep any richer entries modules pushed (marked _pushed); rebuild auto ones
    var kept = [];
    try { for (var i = 0; i < registry.length; i++) if (registry[i] && registry[i]._pushed) kept.push(registry[i]); } catch (e) {}
    var out = kept.slice();
    PANELS.forEach(function (p) {
      var panel = byId(p.id);
      out.push({ sec: p.id, secTitle: p.title, title: p.title, keywords: (p.title + " " + (p.sub || "") + " " + p.group).toLowerCase() });
      if (!panel) return;
      // relocated legacy card headings (their <h2> label)
      try {
        each(panel.querySelectorAll(".set-legacy>h2,.card>h2,.set-block>h2"), function (h) {
          var label = (h.textContent || "").replace(/\s+/g, " ").trim();
          if (!label) return;
          out.push({ sec: p.id, secTitle: p.title, title: label.slice(0, 60), keywords: label.toLowerCase() });
        });
      } catch (e) {}
      // native settings rows
      try {
        each(panel.querySelectorAll(".set-row"), function (r) {
          var lbl = r.querySelector ? r.querySelector(".set-row-label,.set-row-title,label,h3") : null;
          var label = lbl ? (lbl.textContent || "").trim() : (r.getAttribute && r.getAttribute("data-label")) || "";
          if (!label) return;
          out.push({ sec: p.id, secTitle: p.title, title: label.slice(0, 60), row: r.id || "", keywords: label.toLowerCase() });
        });
      } catch (e) {}
    });
    registry.length = 0;
    Array.prototype.push.apply(registry, out);
    win().settingsRegistry = registry;
  }

  var hitIdx = -1, curHits = [];
  function runSearch(q) {
    q = (q || "").trim().toLowerCase();
    var box = byId("set-hits"); if (!box) return;
    if (q.length < 2) { hideHits(); return; }
    var seen = {}, scored = [];
    registry.forEach(function (e) {
      if (!e || !e.title) return;
      var kw = e.keywords || e.title.toLowerCase();
      var pos = kw.indexOf(q);
      if (pos < 0) return;
      var key = e.sec + "|" + e.title;
      if (seen[key]) return; seen[key] = 1;
      scored.push({ e: e, rank: (kw.indexOf(q) === 0 ? 0 : 1) * 1000 + pos });
    });
    scored.sort(function (a, b) { return a.rank - b.rank; });
    curHits = scored.slice(0, 8).map(function (x) { return x.e; });
    hitIdx = -1;
    if (!curHits.length) {
      box.innerHTML = '<div class="set-nohit">No matching settings.</div>';
      box.hidden = false; return;
    }
    box.innerHTML = curHits.map(function (e, i) {
      return '<div class="set-hit" data-i="' + i + '" role="option">' +
        '<span class="h-t">' + E(e.title) + "</span>" +
        '<span class="h-s">' + E(e.secTitle || "") + "</span></div>";
    }).join("");
    each(box.querySelectorAll(".set-hit"), function (h) {
      h.addEventListener("mousedown", function (ev) { if (ev && ev.preventDefault) ev.preventDefault(); });
      h.addEventListener("click", function () { pickHit(Number(h.getAttribute("data-i"))); });
    });
    box.hidden = false;
  }
  function pickHit(i) {
    var e = curHits[i]; if (!e) return;
    hideHits();
    var search = byId("set-search"); if (search) search.value = "";
    settingsShow(e.sec, e.row || null);
  }
  function hideHits() { var box = byId("set-hits"); if (box) box.hidden = true; hitIdx = -1; }
  function onSearchKey(ev) {
    var box = byId("set-hits");
    if (!box || box.hidden) { if (ev.key === "Escape") { var s = byId("set-search"); if (s) s.blur(); } return; }
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      if (ev.preventDefault) ev.preventDefault();
      hitIdx += (ev.key === "ArrowDown" ? 1 : -1);
      if (hitIdx < 0) hitIdx = curHits.length - 1;
      if (hitIdx >= curHits.length) hitIdx = 0;
      each(box.querySelectorAll(".set-hit"), function (h) { h.classList.toggle("on", Number(h.getAttribute("data-i")) === hitIdx); });
    } else if (ev.key === "Enter") {
      if (ev.preventDefault) ev.preventDefault();
      pickHit(hitIdx < 0 ? 0 : hitIdx);
    } else if (ev.key === "Escape") {
      hideHits();
    }
  }

  // ==========================================================================
  // Kill switch — pauses/resumes the agent via the existing endpoints.
  // ==========================================================================
  var killPaused = false, killBusy = false, pausedAt = null;

  function settingsActive() {
    var v = byId("view-mind");
    return !!(v && !v.hidden);
  }

  function refreshKill() {
    var f = win().fetch;
    if (typeof f !== "function") { paintKill(); return; }
    f("/api/models", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (d) {
      var p = !!(d && d.paused);
      if (p && !killPaused && !pausedAt) pausedAt = new Date();
      if (!p) pausedAt = null;
      killPaused = p; killBusy = false; paintKill();
    }).catch(function () { paintKill(); });
  }

  function paintKill() {
    var kill = byId("set-kill"); if (!kill) return;
    var ttl = byId("set-killttl"), cap = byId("set-killcap");
    kill.classList.toggle("paused", killPaused && !killBusy);
    kill.classList.toggle("busy", killBusy);
    kill.setAttribute("aria-pressed", killPaused ? "true" : "false");
    if (killBusy) { if (ttl) ttl.textContent = killPaused ? "Waking…" : "Pausing…"; if (cap) cap.textContent = "Reloading the model"; return; }
    if (killPaused) {
      if (ttl) ttl.textContent = "Resume agent";
      if (cap) cap.textContent = pausedAt ? ("Paused " + t12(pausedAt)) : "Agent is paused";
    } else {
      if (ttl) ttl.textContent = "Pause agent";
      if (cap) cap.textContent = "Agent is running";
    }
  }

  function onKill() {
    if (killBusy) return;
    var w = win();
    if (!killPaused) {
      // reuse the app's existing pause flow (native confirm + pill sync) when present
      if (typeof w.pauseAgent === "function") {
        killBusy = true; paintKill();
        try { w.pauseAgent(); } catch (e) {}
        // reflect after the confirm resolves; poll picks up the truth
        pausedAt = new Date();
        setTimeout(refreshKill, 1200);
        return;
      }
      if (typeof w.confirm === "function" && !w.confirm("Pause the agent? This unloads the model to free its RAM. Dashboard chat and Telegram replies won't work until you resume.")) return;
      killBusy = true; killPaused = true; pausedAt = new Date(); paintKill();
      safePost("/api/agent/pause", function () { killBusy = false; refreshKill(); });
    } else {
      if (typeof w.resumeAgent === "function") {
        killBusy = true; paintKill();
        try { w.resumeAgent(); } catch (e) {}
        setTimeout(refreshKill, 1500);
        return;
      }
      killBusy = true; paintKill();
      safePost("/api/agent/resume", function () { killBusy = false; killPaused = false; pausedAt = null; refreshKill(); });
    }
  }
  function safePost(url, done) {
    var f = win().fetch;
    if (typeof f !== "function") { if (done) done(); return; }
    f(url, { method: "POST" }).then(function () { if (done) done(); }).catch(function () { if (done) done(); });
  }

  // ==========================================================================
  // Hash routing + / shortcut.
  // ==========================================================================
  function applyHash() {
    if (hashLock) return;
    var h = "";
    try { h = (win().location && win().location.hash) || ""; } catch (e) {}
    var m = /^#settings\/([a-z0-9_-]+)(?:@([a-zA-Z0-9_-]+))?/.exec(h);
    if (!m) return;
    var short = m[1], row = m[2] || null;
    var w = win();
    if (typeof w.setView === "function") { try { if (!settingsActive()) w.setView("mind"); } catch (e) {} }
    // ensure shell exists before showing
    var host = byId("view-mind"); if (host) ensureShell(host);
    settingsShow(short, row);
  }

  function onGlobalKey(ev) {
    try {
      if (ev.key !== "/" || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (!settingsActive()) return;
      var t = ev.target || {};
      var tag = (t.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || t.isContentEditable) return;
      var s = byId("set-search"); if (!s) return;
      if (ev.preventDefault) ev.preventDefault();
      s.focus();
    } catch (e) {}
  }

  // ==========================================================================
  // mindExtras wrap + MutationObserver + re-entrancy-safe scheduler.
  // ==========================================================================
  var observer = null, scheduled = false;

  function runRelocate() {
    var host = byId("view-mind"); if (!host) return;
    if (observer && observer.disconnect) { try { observer.disconnect(); } catch (e) {} }
    try { relocate(); } catch (e) {}
    // re-place the indicator after a relayout
    try { moveIndicator(activeItem(), false); } catch (e) {}
    if (observer && observer.observe) {
      try { if (observer.takeRecords) observer.takeRecords(); observer.observe(host, { childList: true }); } catch (e) {}
    }
  }
  function scheduleRelocate() {
    if (scheduled) return;
    scheduled = true;
    var raf = win().requestAnimationFrame;
    var run = function () { scheduled = false; runRelocate(); };
    if (typeof raf === "function") { try { raf(run); return; } catch (e) {} }
    setTimeout(run, 16);
  }

  function install() {
    var w = win();
    // wrap the whole mindExtras chain — our relocate runs AFTER every module.
    var prev = w.mindExtras;
    w.mindExtras = async function () {
      if (typeof prev === "function") { try { await prev(); } catch (e) {} }
      try { runRelocate(); } catch (e) {}
    };

    // safety net for async late mounts
    var host = byId("view-mind");
    if (host && typeof w.MutationObserver === "function") {
      try {
        observer = new w.MutationObserver(function () { scheduleRelocate(); });
        observer.observe(host, { childList: true });
      } catch (e) { observer = null; }
    }

    // events
    try {
      if (w.addEventListener) {
        w.addEventListener("hashchange", applyHash);
        w.addEventListener("keydown", onGlobalKey, true);
      }
    } catch (e) {}

    // initial build + relocation (shell present from first paint) + hash entry
    try { if (host) { ensureShell(host); runRelocate(); } } catch (e) {}
    try { applyHash(); } catch (e) {}
    // set the initial indicator/active state
    try {
      each((doc() && doc().querySelectorAll(".set-item")) || [], function (b) {
        b.classList.toggle("on", b.getAttribute("data-sec") === curSec);
      });
      moveIndicator(activeItem(), false);
    } catch (e) {}

    // keep the kill switch honest while Settings is on screen
    try {
      if (w.setInterval) w.setInterval(function () {
        try {
          var vis = (doc() && doc().visibilityState) ? doc().visibilityState === "visible" : true;
          if (vis && settingsActive() && !killBusy) refreshKill();
        } catch (e) {}
      }, 15000);
    } catch (e) {}
  }

  // ==========================================================================
  // Public surface (also lets the headless harness drive it).
  // ==========================================================================
  var W = win();
  W.settingsShow = settingsShow;
  W.settingsRelocate = function () { try { runRelocate(); } catch (e) {} };
  W.settingsEnsureShell = function () { var h = byId("view-mind"); if (h) ensureShell(h); };
  W.SETTINGS_CARD_MAP = CARD_MAP;
  W.SETTINGS_PANELS = PANELS;

  // boot ---------------------------------------------------------------------
  try {
    if (typeof document !== "undefined" && document.readyState === "loading" && document.addEventListener) {
      document.addEventListener("DOMContentLoaded", install, { once: true });
    } else {
      install();
    }
  } catch (e) { try { install(); } catch (e2) {} }
})();
