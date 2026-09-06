// aux_onboarding.js — first-run setup sheet (1.1.1).
//
// Backend: aux_onboarding.py (/api/onboarding/state|apply|done|reset).
//
// WHAT THIS IS. A full-viewport sheet that opens once, on the first launch,
// and does the three things a local assistant owes a new user before it is
// useful: say what leaves the Mac, look at the Mac and recommend a brain that
// actually fits it, and set the handful of preferences that otherwise sit
// buried in Settings. Four steps, one accent, no marketing.
//
// DESIGN DIRECTION. Quiet and native — this is a trust product, so the sheet
// reads like a system dialog, not a product tour. The memorable detail is
// deliberately step 1: a table of everything that ever leaves this Mac, shown
// BEFORE anything is asked for. A product whose whole argument is sovereignty
// should make that argument in the first viewport instead of claiming it in a
// headline. That table is generated from ONE constant list (NETWORK_FACTS) by
// ONE reusable function (networkTableHTML) precisely because it is the seed of
// the future Data & Network panel (purpose-and-direction §4.7) — when that
// panel lands it imports the function, not a copy of the markup.
//
// THEMING. This file mounts into index.html's own document, so `--ground`,
// `--ink`, `--iris` and friends resolve from the :root palette that index.html
// already re-declares in all four blocks (light, @media dark, [data-theme=
// light], [data-theme=dark]). There is therefore NOTHING to re-declare here and
// re-declaring would be a bug — a fifth copy of the palette is a fifth thing to
// forget when a token changes. VERIFIED headless in both themes; the sheet
// paints var(--ground) and inverts with the toggle. The only colours written
// literally are inside the fallbacks of var(--x, fallback), for the case where
// this file is evaluated outside index.html.
//
// ESC. On a FIRST RUN Esc does nothing until the final step: the setup exists
// because the defaults are not obvious, and a stray keypress on step 1 should
// not silently skip it. Every re-run (Settings -> "Run setup again") is
// dismissable from anywhere, because then the user chose to open it.
//
// DESIGN LAWS (CLAUDE.md): zero emoji (bespoke two-tone SVG), esc() on every
// interpolation, every global helper typeof-guarded so a headless harness can
// eval this file, explicit transition-property, >=40px hit areas,
// prefers-reduced-motion respected.
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

  var SHEET_ID = "onb-sheet";
  var CARD_ID = "mind-extra-onboarding";
  var STEPS = [
    { key: "welcome", title: "Hermes runs on this Mac",
      sub: "What that means, and the short list of things that ever leave it" },
    { key: "models", title: "Your Mac, and the brain that fits it",
      sub: "Detected automatically — nothing here was asked of you" },
    { key: "prefs", title: "How it should behave",
      sub: "All of this is changeable later in Settings" },
    { key: "done", title: "Ready",
      sub: "Here is what was set" }
  ];

  // ==========================================================================
  // NETWORK_FACTS — the single source of truth for "what leaves this Mac".
  //
  // This list is the seed of the Data & Network panel. Each row is a real
  // egress path in the shipping code, not a category:
  //   what    the destination, named
  //   when    the trigger, in the user's terms
  //   data    what is actually in the request
  //   toggle  the EXISTING switch that turns it off, or null when there is
  //           none yet (a null is a promise to add one, and reads as "always")
  // Adding a network call to Hermes means adding a row here.
  // ==========================================================================
  var NETWORK_FACTS = [
    { id: "updates", what: "GitHub", when: "Every 6 hours, and when you press Check",
      data: "The release list for this app. No identity, no usage.", toggle: null },
    { id: "feeds", what: "Weather, markets and news feeds",
      when: "While a widget that uses them is on your Hub",
      data: "A location or a ticker symbol. Never your content.",
      toggle: { key: "news", label: "News and breaking alerts" } },
    { id: "telegram", what: "Telegram", when: "When you message the bot, and for briefings",
      data: "The message text, to your own bot, for your own account.",
      toggle: { key: "briefings", label: "Briefings and daily wrap" } },
    { id: "claude", what: "Claude (Anthropic)",
      when: "Only when a question is escalated to the deep brain",
      data: "That one question. Never your files, keys or history.",
      toggle: { key: "claude_escalation", label: "Claude escalation" } },
    { id: "google", what: "Google", when: "Only if you connect an account",
      data: "Read-only calendar and mail. Sending is not implemented.", toggle: null }
  ];

  // Everything else — chat, tools, memory, search, the model itself — is local.
  var LOCAL_LINES = [
    "Every model that answers you runs on this Mac. Your conversations, notes, " +
      "messages and files are never uploaded, and there is no account to sign in to.",
    "The assistant asks before it does anything irreversible, and the Flight " +
      "Recorder keeps an undo trail of what it did.",
    "Five things reach the internet. They are listed below, and most of them " +
      "have an off switch on this screen."
  ];

  // ---- glyphs (two-tone, accent fill + currentColor stroke; zero emoji) -----
  var G = {
    shield: '<path d="M12 3 4.5 6.2v5.4c0 4.4 3 8.2 7.5 9.4 4.5-1.2 7.5-5 7.5-9.4V6.2Z" ' +
      'fill="var(--iris,#5B63E6)" opacity=".16"/>' +
      '<path d="M12 3 4.5 6.2v5.4c0 4.4 3 8.2 7.5 9.4 4.5-1.2 7.5-5 7.5-9.4V6.2Z" ' +
      'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>' +
      '<path d="M8.8 11.9 11.2 14.3 15.4 10" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    chip: '<rect x="6" y="6" width="12" height="12" rx="2.4" fill="var(--iris,#5B63E6)" ' +
      'opacity=".18" stroke="currentColor" stroke-width="1.5"/>' +
      '<rect x="9.6" y="9.6" width="4.8" height="4.8" rx="1.2" fill="currentColor"/>' +
      '<path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" fill="none" ' +
      'stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    sliders: '<path d="M4 8h10M18 8h2M4 16h4M12 16h8" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round"/>' +
      '<circle cx="16" cy="8" r="2.4" fill="var(--iris,#5B63E6)" opacity=".25" ' +
      'stroke="currentColor" stroke-width="1.5"/>' +
      '<circle cx="10" cy="16" r="2.4" fill="var(--iris,#5B63E6)" opacity=".25" ' +
      'stroke="currentColor" stroke-width="1.5"/>',
    check: '<circle cx="12" cy="12" r="9" fill="var(--iris,#5B63E6)" opacity=".16"/>' +
      '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.5"/>' +
      '<path d="M8.2 12.2 10.9 15 15.9 9.4" fill="none" stroke="currentColor" ' +
      'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    dot: '<circle cx="12" cy="12" r="4" fill="currentColor"/>'
  };

  function svg(path, size) {
    size = size || 16;
    return '<svg class="onb-ic" viewBox="0 0 24 24" width="' + size + '" height="' +
      size + '" aria-hidden="true" focusable="false">' + path + "</svg>";
  }

  // ==========================================================================
  // pure helpers (exported for the headless harness)
  // ==========================================================================

  // "16.1" -> "16.1 GB"; whole numbers lose the .0 ("23.0" -> "23 GB")
  function gb(n) {
    var v = Number(n || 0);
    if (!v) return "0 GB";
    return (Math.round(v * 10) % 10 === 0 ? String(Math.round(v)) : v.toFixed(1)) + " GB";
  }

  // the 1.0.3 fit sentence, verbatim in shape: "needs ~19 GB · this Mac has 64 GB"
  // Same three states and the same tokens as the model menu's .mmfit, so a fit
  // badge means exactly what it means there.
  function fitLine(ram, machineGb, fit) {
    if (!fit || !ram || !machineGb) return "";
    return "needs ~" + ram + " GB · this Mac has " + machineGb + " GB" +
      (fit === "no" ? " · too big to run here"
        : fit === "tight" ? " · tight, expect swapping" : "");
  }

  function fitClass(fit) {
    return "onb-fit" + (fit === "no" ? " bad" : fit === "tight" ? " warn" : "");
  }

  // "Apple M5 Max · 64 GB · 412 GB free · macOS 26.1 · laptop"
  function sysLine(si) {
    si = si || {};
    var bits = [];
    if (si.chip) bits.push(si.chip);
    if (si.ram_gb) bits.push(si.ram_gb + " GB memory");
    if (si.disk_free_gb != null) bits.push(gb(si.disk_free_gb) + " free");
    if (si.macos) bits.push("macOS " + si.macos);
    bits.push(si.laptop ? "laptop" : "desktop");
    return bits.join(" · ");
  }

  // ==========================================================================
  // THE NETWORK TABLE — one function, reused by the future Data & Network panel.
  //
  // opts.prefs  current pref values, so a row's toggle shows its real state
  // opts.inputs when true, render the toggles as live checkboxes (the sheet);
  //             when false, render them as read-only state words (a panel that
  //             only reports). Everything else is identical.
  // ==========================================================================
  function networkTableHTML(opts) {
    opts = opts || {};
    var prefs = opts.prefs || {};
    var live = opts.inputs !== false;
    var rows = (opts.facts || NETWORK_FACTS).map(function (f) {
      var ctl;
      if (!f.toggle) {
        ctl = '<span class="onb-net-always">always</span>';
      } else {
        var on = prefs[f.toggle.key] !== false;
        if (live) {
          ctl = '<label class="onb-sw" title="' + E(f.toggle.label) + '">' +
            '<input type="checkbox" data-pref="' + E(f.toggle.key) + '"' +
            (on ? " checked" : "") + ' aria-label="' + E(f.toggle.label) + '">' +
            '<span class="onb-sw-t" aria-hidden="true"></span>' +
            '<span class="onb-sw-l">' + (on ? "On" : "Off") + "</span></label>";
        } else {
          ctl = '<span class="onb-net-always">' + (on ? "On" : "Off") + "</span>";
        }
      }
      return '<tr><th scope="row">' + E(f.what) + "</th>" +
        '<td class="onb-net-when">' + E(f.when) + "</td>" +
        '<td class="onb-net-data">' + E(f.data) + "</td>" +
        '<td class="onb-net-ctl">' + ctl + "</td></tr>";
    }).join("");
    return '<div class="onb-tablewrap"><table class="onb-net">' +
      "<caption>Everything that ever leaves this Mac</caption>" +
      '<thead><tr><th scope="col">Goes to</th><th scope="col">When</th>' +
      '<th scope="col">What</th><th scope="col">Switch</th></tr></thead>' +
      "<tbody>" + rows + "</tbody></table></div>";
  }

  // ==========================================================================
  // CSS. One <style>, scoped to #onb-sheet (plus the Settings row). All colour
  // comes from index.html's tokens — see the THEMING note in the header.
  // ==========================================================================
  function CSS() {
    var S_ = "#" + SHEET_ID;
    return '<style id="onb-css">' +
      // ---- the sheet ------------------------------------------------------
      S_ + '{position:fixed;inset:0;z-index:9000;display:flex;flex-direction:column;' +
        'background:var(--ground,#E7EAF3);color:var(--ink,#10131D);' +
        '-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;' +
        'font:14px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif}' +
      S_ + '[hidden]{display:none!important}' +
      // a very quiet ground wash so the flat panel is not literally flat
      S_ + '::before{content:"";position:absolute;inset:0;pointer-events:none;' +
        'background:radial-gradient(120% 60% at 50% -10%,' +
        'color-mix(in srgb,var(--iris,#5B63E6) 9%,transparent),transparent 70%)}' +
      S_ + ' *{box-sizing:border-box}' +
      // ---- progress rail --------------------------------------------------
      S_ + ' .onb-rail{position:relative;display:flex;gap:4px;padding:0;margin:0;' +
        'flex:0 0 auto;list-style:none}' +
      S_ + ' .onb-rail li{flex:1 1 0;height:3px;border-radius:0;' +
        'background:color-mix(in srgb,var(--ink,#10131D) 10%,transparent)}' +
      S_ + ' .onb-rail li.on{background:var(--iris,#5B63E6)}' +
      S_ + ' .onb-rail li{transition-property:background-color;transition-duration:260ms;' +
        'transition-timing-function:ease-out}' +
      // ---- scroll body ----------------------------------------------------
      S_ + ' .onb-scroll{position:relative;flex:1 1 auto;overflow-y:auto;overflow-x:hidden;' +
        'display:flex;justify-content:center;padding:34px 24px 26px}' +
      S_ + ' .onb-col{width:100%;max-width:680px}' +
      // ---- header ---------------------------------------------------------
      S_ + ' .onb-eyebrow{display:flex;align-items:center;gap:7px;margin:0 0 12px;' +
        'font-size:10.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;' +
        'color:var(--muted,#565E72)}' +
      S_ + ' .onb-ic{flex:0 0 auto;color:var(--muted,#565E72)}' +
      S_ + ' h1.onb-t{margin:0 0 6px;font-size:26px;line-height:1.18;font-weight:640;' +
        'letter-spacing:-.015em;text-wrap:balance}' +
      S_ + ' p.onb-s{margin:0 0 22px;font-size:13.5px;line-height:1.55;' +
        'color:var(--muted,#565E72);text-wrap:pretty}' +
      S_ + ' p.onb-p{margin:0 0 11px;font-size:13.5px;line-height:1.62;text-wrap:pretty}' +
      S_ + ' p.onb-p:last-of-type{margin-bottom:20px}' +
      // ---- section heads (no cards in cards: a rule + a label, not a box) --
      S_ + ' .onb-h{display:flex;align-items:baseline;gap:9px;margin:24px 0 10px;' +
        'padding-bottom:7px;border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' .onb-h h2{margin:0;font-size:12px;font-weight:680;letter-spacing:.02em}' +
      S_ + ' .onb-h span{font-size:11.5px;color:var(--muted,#565E72)}' +
      // ---- the network table ----------------------------------------------
      S_ + ' .onb-tablewrap{overflow-x:auto;margin:0 0 4px}' +
      S_ + ' table.onb-net{width:100%;border-collapse:collapse;font-size:12.5px;' +
        'min-width:520px}' +
      S_ + ' table.onb-net caption{text-align:left;font-size:11px;color:var(--faint,#868DA1);' +
        'padding:0 0 8px}' +
      S_ + ' table.onb-net th,' + S_ + ' table.onb-net td{text-align:left;vertical-align:top;' +
        'padding:9px 12px 9px 0;border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' table.onb-net thead th{font-size:10px;font-weight:700;letter-spacing:.07em;' +
        'text-transform:uppercase;color:var(--faint,#868DA1);padding-top:0}' +
      S_ + ' table.onb-net tbody th{font-weight:620;white-space:nowrap;padding-right:16px}' +
      S_ + ' .onb-net-when{color:var(--muted,#565E72);text-wrap:pretty;min-width:150px}' +
      S_ + ' .onb-net-data{color:var(--muted,#565E72);text-wrap:pretty;min-width:170px}' +
      S_ + ' .onb-net-ctl{white-space:nowrap;padding-right:0}' +
      S_ + ' .onb-net-always{font-size:11px;color:var(--faint,#868DA1)}' +
      S_ + ' table.onb-net tbody tr:last-child th,' +
        S_ + ' table.onb-net tbody tr:last-child td{border-bottom:0}' +
      // ---- stats row (step 2) ---------------------------------------------
      S_ + ' .onb-stats{display:flex;flex-wrap:wrap;gap:0 26px;margin:0 0 22px;' +
        'padding:0;list-style:none}' +
      S_ + ' .onb-stats li{padding:0 0 4px}' +
      S_ + ' .onb-stat-k{display:block;font-size:10px;font-weight:700;letter-spacing:.07em;' +
        'text-transform:uppercase;color:var(--faint,#868DA1);margin-bottom:2px}' +
      S_ + ' .onb-stat-v{display:block;font-size:14px;font-weight:600;' +
        'font-variant-numeric:tabular-nums}' +
      // ---- the recommendation ---------------------------------------------
      S_ + ' .onb-rec{margin:0 0 6px;padding:16px 17px;border-radius:var(--radius-sm,13px);' +
        'border:1px solid color-mix(in srgb,var(--iris,#5B63E6) 42%,transparent);' +
        'background:color-mix(in srgb,var(--iris,#5B63E6) 10%,transparent)}' +
      S_ + ' .onb-rec h3{margin:0 0 5px;font-size:15px;font-weight:640;letter-spacing:-.01em;' +
        'text-wrap:balance}' +
      S_ + ' .onb-rec .onb-why{margin:0 0 13px;font-size:12.5px;line-height:1.6;' +
        'color:var(--muted,#565E72);text-wrap:pretty}' +
      S_ + ' .onb-lane{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;' +
        'padding:8px 0;border-top:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' .onb-lane:first-of-type{border-top:0;padding-top:0}' +
      S_ + ' .onb-lane-r{font-size:10px;font-weight:700;letter-spacing:.07em;' +
        'text-transform:uppercase;color:var(--faint,#868DA1);width:88px;flex:0 0 auto}' +
      S_ + ' .onb-lane-n{font-size:13px;font-weight:620}' +
      S_ + ' .onb-lane-s{font-size:11.5px;color:var(--muted,#565E72);' +
        'font-variant-numeric:tabular-nums}' +
      S_ + ' .onb-fit{width:100%;margin-top:2px;font-size:10.5px;line-height:1.4;' +
        'color:var(--faint,#868DA1);font-variant-numeric:tabular-nums}' +
      S_ + ' .onb-fit.warn{color:var(--warn,#B9821A)}' +
      S_ + ' .onb-fit.bad{color:var(--bad,#D24C3C)}' +
      S_ + ' .onb-tag{font-size:9.5px;font-weight:700;letter-spacing:.06em;' +
        'text-transform:uppercase;padding:1px 6px;border-radius:20px;' +
        'color:var(--warn,#B9821A);' +
        'background:color-mix(in srgb,var(--warn,#B9821A) 16%,transparent)}' +
      S_ + ' .onb-total{margin:12px 0 0;font-size:12.5px;font-variant-numeric:tabular-nums}' +
      S_ + ' .onb-done-note{margin:12px 0 0;font-size:12.5px;color:var(--ok,#2E9E68)}' +
      // alternatives: a compact list, never a second card inside the first
      S_ + ' ul.onb-alts{margin:0;padding:0;list-style:none}' +
      S_ + ' ul.onb-alts li{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;' +
        'padding:9px 0;border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' ul.onb-alts li:last-child{border-bottom:0}' +
      S_ + ' ul.onb-alts .onb-alt-n{font-size:12.5px;font-weight:600;min-width:110px}' +
      S_ + ' ul.onb-alts .onb-alt-d{flex:1 1 190px;font-size:11.5px;' +
        'color:var(--muted,#565E72);text-wrap:pretty}' +
      S_ + ' ul.onb-alts .onb-alt-s{font-size:11.5px;color:var(--faint,#868DA1);' +
        'font-variant-numeric:tabular-nums;white-space:nowrap}' +
      // ---- rows (preferences + status) ------------------------------------
      S_ + ' .onb-row{display:flex;align-items:center;gap:14px;flex-wrap:wrap;' +
        'min-height:48px;padding:8px 0;' +
        'border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' .onb-row:last-child{border-bottom:0}' +
      S_ + ' .onb-row-txt{flex:1 1 220px;min-width:0}' +
      S_ + ' .onb-row-l{display:block;font-size:13px;font-weight:600}' +
      S_ + ' .onb-row-d{display:block;margin-top:1px;font-size:11.5px;line-height:1.5;' +
        'color:var(--muted,#565E72);text-wrap:pretty}' +
      S_ + ' .onb-row-ctl{flex:0 0 auto;display:flex;align-items:center;gap:8px}' +
      // ---- segmented control (theme, sleep) -------------------------------
      S_ + ' .onb-seg{display:inline-flex;padding:2px;gap:2px;border-radius:10px;' +
        'border:1px solid var(--field-edge,rgba(16,19,29,.12));' +
        'background:var(--field,rgba(255,255,255,.55))}' +
      S_ + ' .onb-seg label{position:relative;display:flex;align-items:center;' +
        'justify-content:center;min-width:56px;min-height:34px;padding:0 11px;' +
        'border-radius:8px;font-size:12px;font-weight:560;cursor:pointer;' +
        'color:var(--muted,#565E72);' +
        'transition-property:background-color,color;transition-duration:150ms;' +
        'transition-timing-function:ease-out}' +
      // the visible pill is 34px tall; the pseudo-element makes the target 44px
      S_ + ' .onb-seg label::after{content:"";position:absolute;left:0;right:0;top:-5px;bottom:-5px}' +
      S_ + ' .onb-seg input{position:absolute;opacity:0;width:0;height:0;margin:0}' +
      S_ + ' .onb-seg label:hover{color:var(--ink,#10131D)}' +
      S_ + ' .onb-seg label:has(input:checked){background:var(--iris,#5B63E6);' +
        'color:var(--iris-ink,#fff)}' +
      S_ + ' .onb-seg label:has(input:focus-visible){outline:2px solid var(--iris,#5B63E6);' +
        'outline-offset:2px}' +
      // ---- switch ---------------------------------------------------------
      S_ + ' .onb-sw{position:relative;display:inline-flex;align-items:center;gap:8px;' +
        'min-height:40px;cursor:pointer;-webkit-user-select:none;user-select:none}' +
      S_ + ' .onb-sw input{position:absolute;opacity:0;width:0;height:0;margin:0}' +
      S_ + ' .onb-sw-t{position:relative;flex:0 0 auto;width:36px;height:21px;border-radius:11px;' +
        'background:color-mix(in srgb,var(--ink,#10131D) 16%,transparent);' +
        'transition-property:background-color;transition-duration:170ms;' +
        'transition-timing-function:ease-out}' +
      S_ + ' .onb-sw-t::after{content:"";position:absolute;top:2.5px;left:2.5px;width:16px;' +
        'height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.28);' +
        'transition-property:transform;transition-duration:170ms;' +
        'transition-timing-function:cubic-bezier(.2,.8,.3,1)}' +
      S_ + ' .onb-sw input:checked + .onb-sw-t{background:var(--iris,#5B63E6)}' +
      S_ + ' .onb-sw input:checked + .onb-sw-t::after{transform:translateX(15px)}' +
      S_ + ' .onb-sw input:focus-visible + .onb-sw-t{outline:2px solid var(--iris,#5B63E6);' +
        'outline-offset:2px}' +
      S_ + ' .onb-sw-l{font-size:11.5px;color:var(--muted,#565E72);min-width:22px}' +
      // ---- time inputs ----------------------------------------------------
      S_ + ' .onb-time{min-height:36px;padding:6px 9px;border-radius:9px;font-size:12.5px;' +
        'color:inherit;font-variant-numeric:tabular-nums;' +
        'border:1px solid var(--field-edge,rgba(16,19,29,.12));' +
        'background:var(--field,rgba(255,255,255,.55))}' +
      S_ + ' .onb-time:focus-visible{outline:2px solid var(--iris,#5B63E6);outline-offset:1px}' +
      S_ + ' .onb-to{font-size:11.5px;color:var(--muted,#565E72)}' +
      // ---- status list (read-only, next step as text) ---------------------
      S_ + ' .onb-st{display:flex;align-items:flex-start;gap:10px;padding:10px 0;' +
        'border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' .onb-st:last-child{border-bottom:0}' +
      S_ + ' .onb-st-dot{flex:0 0 auto;width:7px;height:7px;margin-top:6px;border-radius:50%;' +
        'background:var(--faint,#868DA1)}' +
      S_ + ' .onb-st.ok .onb-st-dot{background:var(--ok,#2E9E68)}' +
      S_ + ' .onb-st.wait .onb-st-dot{background:var(--warn,#B9821A)}' +
      S_ + ' .onb-st-b{min-width:0}' +
      S_ + ' .onb-st-l{font-size:12.5px;font-weight:600}' +
      S_ + ' .onb-st-v{font-size:11.5px;color:var(--muted,#565E72);line-height:1.55;' +
        'text-wrap:pretty}' +
      S_ + ' .onb-st-v code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;' +
        'font-size:10.5px;padding:1px 4px;border-radius:5px;' +
        'background:color-mix(in srgb,var(--ink,#10131D) 8%,transparent)}' +
      // ---- summary (step 4) -----------------------------------------------
      S_ + ' ul.onb-sum{margin:0 0 20px;padding:0;list-style:none}' +
      S_ + ' ul.onb-sum li{position:relative;padding:6px 0 6px 20px;font-size:13px;' +
        'line-height:1.55;text-wrap:pretty}' +
      S_ + ' ul.onb-sum li::before{content:"";position:absolute;left:5px;top:.95em;width:5px;' +
        'height:5px;border-radius:50%;background:var(--iris,#5B63E6)}' +
      S_ + ' .onb-key{display:inline-flex;align-items:center;gap:2px;padding:2px 7px;' +
        'border-radius:6px;font-size:12px;font-weight:600;' +
        'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;' +
        'border:1px solid var(--hairline,rgba(16,19,29,.10));' +
        'background:var(--chip,rgba(255,255,255,.5))}' +
      // ---- footer ---------------------------------------------------------
      S_ + ' .onb-foot{flex:0 0 auto;display:flex;align-items:center;gap:10px;' +
        'padding:14px 24px;border-top:1px solid var(--hairline,rgba(16,19,29,.10));' +
        'background:color-mix(in srgb,var(--ground,#E7EAF3) 88%,transparent);' +
        '-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px)}' +
      S_ + ' .onb-foot-in{display:flex;align-items:center;gap:10px;width:100%;' +
        'max-width:680px;margin:0 auto}' +
      S_ + ' .onb-step-n{font-size:11.5px;color:var(--faint,#868DA1);' +
        'font-variant-numeric:tabular-nums;margin-right:auto}' +
      S_ + ' button.onb-b{min-height:40px;padding:0 17px;border-radius:10px;font-size:13px;' +
        'font-weight:560;cursor:pointer;color:inherit;' +
        'border:1px solid var(--field-edge,rgba(16,19,29,.12));' +
        'background:var(--field,rgba(255,255,255,.55));' +
        'transition-property:background-color,border-color,color,transform,opacity;' +
        'transition-duration:150ms;transition-timing-function:ease-out}' +
      S_ + ' button.onb-b:hover:not(:disabled){border-color:var(--iris,#5B63E6)}' +
      S_ + ' button.onb-b:active:not(:disabled){transform:scale(.97)}' +
      S_ + ' button.onb-b:focus-visible{outline:2px solid var(--iris,#5B63E6);outline-offset:2px}' +
      S_ + ' button.onb-b:disabled{opacity:.45;cursor:default}' +
      S_ + ' button.onb-go{border-color:transparent;background:var(--iris,#5B63E6);' +
        'color:var(--iris-ink,#fff);font-weight:620}' +
      S_ + ' button.onb-go:hover:not(:disabled){border-color:transparent;' +
        'background:var(--iris-2,#7A6BEF)}' +
      S_ + ' .onb-err{margin:12px 0 0;font-size:12px;line-height:1.55;color:var(--bad,#D24C3C);' +
        'text-wrap:pretty}' +
      // ---- entrance (interruptible, and off under reduced motion) ---------
      '@keyframes onb-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}' +
      S_ + ' .onb-col{animation:onb-in 220ms cubic-bezier(.2,.8,.3,1) both}' +
      '@media (prefers-reduced-motion:reduce){' +
        S_ + ' .onb-col{animation:none}' +
        S_ + ' .onb-rail li,' + S_ + ' .onb-sw-t,' + S_ + ' .onb-sw-t::after,' +
        S_ + ' button.onb-b,' + S_ + ' .onb-seg label{transition-duration:1ms}' +
        S_ + ' button.onb-b:active:not(:disabled){transform:none}}' +
      // narrow windows: the footer stacks rather than clipping the primary action
      '@media (max-width:560px){' + S_ + ' .onb-scroll{padding:24px 16px 20px}' +
        S_ + ' h1.onb-t{font-size:22px}' +
        S_ + ' .onb-foot{padding:12px 16px}' +
        S_ + ' .onb-step-n{width:100%;margin:0 0 6px}}' +
      // ---- the Settings row ------------------------------------------------
      '#' + CARD_ID + ' .onb-set-row{display:flex;align-items:center;gap:14px;' +
        'flex-wrap:wrap;min-height:44px}' +
      '#' + CARD_ID + ' .onb-set-txt{flex:1 1 220px;min-width:0}' +
      '#' + CARD_ID + ' .onb-set-l{font-size:13px;font-weight:600}' +
      '#' + CARD_ID + ' .onb-set-d{margin-top:2px;font-size:11.5px;line-height:1.5;' +
        'color:var(--muted);text-wrap:pretty}' +
      '#' + CARD_ID + ' button.onb-set-b{min-height:40px;padding:0 15px;border-radius:10px;' +
        'font-size:12.5px;cursor:pointer;color:inherit;' +
        'border:1px solid var(--hairline,rgba(255,255,255,.12));' +
        'background:var(--glass-2,rgba(255,255,255,.05));' +
        'transition-property:background-color,border-color,transform;' +
        'transition-duration:150ms;transition-timing-function:ease-out}' +
      '#' + CARD_ID + ' button.onb-set-b:hover{border-color:var(--iris)}' +
      '#' + CARD_ID + ' button.onb-set-b:active{transform:scale(.97)}' +
      '@media (prefers-reduced-motion:reduce){' +
        '#' + CARD_ID + ' button.onb-set-b:active{transform:none}}' +
      "</style>";
  }

  // ==========================================================================
  // state
  // ==========================================================================
  var S = {
    open: false, rerun: false, step: 0,
    data: null,                 // last /api/onboarding/state
    prefs: null,                // working copy the sheet edits
    pick: null,                 // {primary, background, alt:bool}
    busy: "", err: "",
    dl: null,                   // {ids:[], done:{}, downloading:{}, started:bool}
    applied: [],                // human lines for the summary
    lastFocus: null
  };
  var dlTimer = null;

  function stepKey() { return (STEPS[S.step] || STEPS[0]).key; }
  function lastStep() { return S.step === STEPS.length - 1; }

  // ==========================================================================
  // network
  // ==========================================================================
  async function jget(url) {
    var r = await fetch(url, { headers: { Accept: "application/json" } });
    return await r.json();
  }

  async function jpost(url, body) {
    var r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    var j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    return j || { ok: false, errors: ["HTTP " + r.status] };
  }

  // ==========================================================================
  // theme — applied immediately, exactly the way index.html's toggle does it
  // ("system" removes the attribute and the key, falling back to the OS media
  // query; anything else stamps data-theme and persists it).
  // ==========================================================================
  function applyTheme(v) {
    var d = D();
    if (!d || !d.documentElement) return;
    try {
      if (v === "light" || v === "dark") {
        d.documentElement.setAttribute("data-theme", v);
        try { localStorage.setItem("hermes_theme", v); } catch (e) {}
      } else {
        d.documentElement.removeAttribute("data-theme");
        try { localStorage.removeItem("hermes_theme"); } catch (e) {}
      }
    } catch (e) {}
  }

  function currentTheme() {
    try {
      var v = localStorage.getItem("hermes_theme");
      if (v === "light" || v === "dark") return v;
    } catch (e) {}
    return "system";
  }

  // ==========================================================================
  // step bodies
  // ==========================================================================
  function head(icon, eyebrow, st) {
    return '<p class="onb-eyebrow">' + svg(icon, 15) + E(eyebrow) + "</p>" +
      '<h1 class="onb-t">' + E(st.title) + "</h1>" +
      '<p class="onb-s">' + E(st.sub) + "</p>";
  }

  function stepWelcome() {
    return head(G.shield, "Private by construction", STEPS[0]) +
      LOCAL_LINES.map(function (l) { return '<p class="onb-p">' + E(l) + "</p>"; }).join("") +
      networkTableHTML({ prefs: S.prefs, inputs: true });
  }

  function stepModels() {
    var d = S.data || {};
    var si = d.sysinfo || {};
    var rec = d.recommendation || {};
    var pick = S.pick || {};
    var h = head(G.chip, "Detected on this Mac", STEPS[1]);

    // quiet stats — facts, not a dashboard
    var stats = [
      ["Chip", si.chip || "—"],
      ["Memory", si.ram_gb ? si.ram_gb + " GB" : "—"],
      ["Free disk", si.disk_free_gb != null ? gb(si.disk_free_gb) : "—"],
      ["macOS", si.macos || "—"],
      ["Form", si.laptop ? "Laptop" : "Desktop"]
    ];
    h += '<ul class="onb-stats">' + stats.map(function (s) {
      return "<li><span class=\"onb-stat-k\">" + E(s[0]) + "</span>" +
        '<span class="onb-stat-v">' + E(s[1]) + "</span></li>";
    }).join("") + "</ul>";

    var byId = {};
    (rec.fits || []).forEach(function (f) { byId[f.id] = f; });
    var primary = pick.primary || rec.primary;
    var background = pick.background !== undefined ? pick.background : rec.background;
    var lanes = [["Assistant", primary], ["Background", background]];

    h += '<div class="onb-rec">';
    h += "<h3>" + E("Recommended for " + (si.ram_gb || "?") + " GB: " +
      ((byId[primary] || {}).label || "—") +
      (background ? " as the assistant, " + ((byId[background] || {}).label || "—") +
        " for background work" : " — one model, no background lane")) + "</h3>";
    h += '<p class="onb-why">' + E(rec.why || "") + "</p>";
    lanes.forEach(function (l) {
      var id = l[1];
      if (!id) {
        if (l[0] !== "Background") return;
        h += '<div class="onb-lane"><span class="onb-lane-r">' + E(l[0]) + "</span>" +
          '<span class="onb-lane-s">' +
          E("none at this memory size — briefings run on the assistant") + "</span></div>";
        return;
      }
      var f = byId[id] || {};
      var tight = l[0] === "Assistant" && rec.tight;
      h += '<div class="onb-lane"><span class="onb-lane-r">' + E(l[0]) + "</span>" +
        '<span class="onb-lane-n">' + E(f.label || id) + "</span>" +
        '<span class="onb-lane-s">' + E(gb(f.size_gb) + " download") + "</span>" +
        (tight ? '<span class="onb-tag">tight</span>' : "") +
        (f.downloaded ? '<span class="onb-lane-s">' + E("· already on this Mac") + "</span>" : "") +
        '<span class="' + fitClass(f.fit) + '">' +
        E(fitLine(f.ram, si.ram_gb, f.fit)) + "</span></div>";
    });

    var ids = [primary, background].filter(Boolean);
    var need = 0, allHave = true;
    ids.forEach(function (id) {
      var f = byId[id] || {};
      if (!f.downloaded) { need += Number(f.size_gb || 0); allHave = false; }
    });
    need = Math.round(need * 10) / 10;
    if (allHave && ids.length) {
      h += '<p class="onb-done-note">' +
        E("Everything recommended is already downloaded on this Mac — nothing to fetch.") +
        "</p>";
    } else {
      h += '<p class="onb-total">' + E(gb(need) + " to download") +
        (si.disk_free_gb != null
          ? ' <span class="onb-lane-s">' + E("· " + gb(si.disk_free_gb) + " free") + "</span>"
          : "") + "</p>";
    }
    h += "</div>";

    // the battery alternative, when the tier offers one
    if (rec.alt) {
      var altOn = !!pick.alt;
      h += '<div class="onb-row" style="margin-top:8px"><div class="onb-row-txt">' +
        '<span class="onb-row-l">' + E(rec.alt.label || "Lighter option") + "</span>" +
        '<span class="onb-row-d">' + E(rec.alt.why || "") + "</span></div>" +
        '<div class="onb-row-ctl"><label class="onb-sw">' +
        '<input type="checkbox" data-alt="1"' + (altOn ? " checked" : "") +
        ' aria-label="' + E(rec.alt.label || "Lighter option") + '">' +
        '<span class="onb-sw-t" aria-hidden="true"></span>' +
        '<span class="onb-sw-l">' + (altOn ? "On" : "Off") + "</span></label></div></div>";
    }

    // alternatives — a compact list, not a grid of cards
    var alts = (rec.fits || []).filter(function (f) { return ids.indexOf(f.id) < 0; });
    if (alts.length) {
      h += '<div class="onb-h"><h2>Other models</h2>' +
        "<span>" + E("Pick any of these later from the model menu") + "</span></div>" +
        '<ul class="onb-alts">' + alts.map(function (f) {
          return "<li><span class=\"onb-alt-n\">" + E(f.label) + "</span>" +
            '<span class="onb-alt-d">' + E(f.note || "") + "</span>" +
            '<span class="onb-alt-s">' + E(gb(f.size_gb)) + "</span>" +
            '<span class="' + fitClass(f.fit) + '">' +
            E(fitLine(f.ram, si.ram_gb, f.fit)) + "</span></li>";
        }).join("") + "</ul>";
    }

    // download progress, read off /api/models' own downloading/downloaded flags
    if (S.dl && S.dl.started) {
      var lines = S.dl.ids.map(function (id) {
        var f = byId[id] || {};
        var st = S.dl.done[id] ? "downloaded"
          : S.dl.downloading[id] ? "downloading…" : "queued";
        return "<li><span class=\"onb-alt-n\">" + E(f.label || id) + "</span>" +
          '<span class="onb-alt-s">' + E(st) + "</span></li>";
      }).join("");
      h += '<div class="onb-h"><h2>Downloading</h2>' +
        "<span>" + E("This continues in the background — you can keep going") +
        "</span></div><ul class=\"onb-alts\">" + lines + "</ul>";
    }
    return h;
  }

  function stepPrefs() {
    var d = S.data || {};
    var det = d.detect || {};
    var p = S.prefs || {};
    var h = head(G.sliders, "Preferences", STEPS[2]);

    // theme
    h += '<div class="onb-row"><div class="onb-row-txt">' +
      '<span class="onb-row-l">Appearance</span>' +
      '<span class="onb-row-d">' + E("Applies as you choose it.") + "</span></div>" +
      '<div class="onb-row-ctl">' + seg("theme", currentTheme(), [
        ["system", "System"], ["light", "Light"], ["dark", "Dark"]
      ]) + "</div></div>";

    // idle
    var idleVal = p.idle_enabled === false ? "never" : String(p.idle_min || 10);
    h += '<div class="onb-row"><div class="onb-row-txt">' +
      '<span class="onb-row-l">Sleep the model after</span>' +
      '<span class="onb-row-d">' +
      E("Frees its memory when you are not using it. Your next message wakes " +
        "it automatically.") + "</span></div>" +
      '<div class="onb-row-ctl">' + seg("idle", idleVal, [
        ["5", "5 min"], ["10", "10 min"], ["20", "20 min"], ["never", "Never"]
      ]) + "</div></div>";

    h += row("prewarm", "Prewarm after wake",
      "Warms the model the moment it wakes, so your first message is not slow.",
      p.prewarm !== false);

    // Claude escalation: only shown when the CLI is actually installed —
    // offering a switch for a binary that is not there is a lie about capability
    if (det.claude_cli) {
      h += row("claude_escalation", "Claude escalation",
        "Sends only the hard question to Claude, in parallel with the local " +
        "answer. Off means nothing ever leaves for Anthropic.",
        p.claude_escalation !== false);
    }

    h += '<div class="onb-h"><h2>Notifications</h2>' +
      "<span>" + E("Master switches — everything else is per-rule in Settings") +
      "</span></div>";
    h += row("briefings", "Briefings",
      "Morning brief, midday pulse and evening wrap.", p.briefings !== false);
    h += row("news", "News and breaking alerts",
      "Watchtower rules and breaking-news pushes.", p.news !== false);

    var qh = p.quiet_hours || {};
    h += '<div class="onb-row"><div class="onb-row-txt">' +
      '<span class="onb-row-l">Quiet hours</span>' +
      '<span class="onb-row-d">' +
      E("Nothing is pushed between these times.") + "</span></div>" +
      '<div class="onb-row-ctl">' +
      '<input class="onb-time" type="time" data-qh="start" value="' +
      E(qh.start || "22:00") + '" aria-label="Quiet hours start">' +
      '<span class="onb-to">to</span>' +
      '<input class="onb-time" type="time" data-qh="end" value="' +
      E(qh.end || "07:00") + '" aria-label="Quiet hours end">' +
      "</div></div>";

    // ---- status: read-only, each with the NEXT STEP as plain text ----------
    h += '<div class="onb-h"><h2>Connections</h2>' +
      "<span>" + E("Nothing here is required to start") + "</span></div>";
    h += statusRows(det);
    return h;
  }

  function statusRows(det) {
    det = det || {};
    var rows = [];
    rows.push(det.telegram_configured
      ? ["ok", "Telegram", "Configured. Message your bot from anywhere and it " +
         "answers with the same assistant."]
      : ["", "Telegram", "Not configured. Create a bot with @BotFather, then put " +
         "TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS in ~/.hermes/.env."]);
    rows.push(det.google_configured
      ? ["ok", "Google", "Connected. Calendar and mail are read-only; sending is " +
         "not implemented."]
      : ["", "Google", "Not connected. Settings › Connections walks through the " +
         "OAuth steps. Read and draft only — Hermes can never send mail."]);
    if (det.fda === true) {
      rows.push(["ok", "Full Disk Access", "Granted. Messages appear in the " +
        "Message Center and in search."]);
    } else if (det.fda === false) {
      rows.push(["wait", "Full Disk Access", "Not granted. System Settings › " +
        "Privacy & Security › Full Disk Access › add Hermes Assistant. " +
        "Without it the Message Center stays empty."]);
    } else {
      rows.push(["", "Full Disk Access", "Not reported yet. The app checks on " +
        "launch; if the Message Center stays empty, add Hermes Assistant under " +
        "System Settings › Privacy & Security › Full Disk Access."]);
    }
    rows.push(det.hermes_cli
      ? ["ok", "Hermes agent", "Installed at " + (det.hermes_path || "~/.local/bin/hermes") + "."]
      : ["wait", "Hermes agent", "Not found on PATH. Run ./install.sh from the " +
         "repo — chat cannot run without it."]);
    return rows.map(function (r) {
      return '<div class="onb-st' + (r[0] ? " " + r[0] : "") + '">' +
        '<span class="onb-st-dot" aria-hidden="true"></span>' +
        '<div class="onb-st-b"><div class="onb-st-l">' + E(r[1]) + "</div>" +
        '<div class="onb-st-v">' + E(r[2]) + "</div></div></div>";
    }).join("");
  }

  function seg(name, value, opts) {
    return '<div class="onb-seg" role="radiogroup" aria-label="' + E(name) + '">' +
      opts.map(function (o) {
        return '<label><input type="radio" name="onb-' + E(name) + '" data-seg="' +
          E(name) + '" value="' + E(o[0]) + '"' +
          (String(value) === String(o[0]) ? " checked" : "") + ">" +
          "<span>" + E(o[1]) + "</span></label>";
      }).join("") + "</div>";
  }

  function row(key, label, desc, on) {
    return '<div class="onb-row"><div class="onb-row-txt">' +
      '<span class="onb-row-l">' + E(label) + "</span>" +
      '<span class="onb-row-d">' + E(desc) + "</span></div>" +
      '<div class="onb-row-ctl"><label class="onb-sw">' +
      '<input type="checkbox" data-pref="' + E(key) + '"' + (on ? " checked" : "") +
      ' aria-label="' + E(label) + '">' +
      '<span class="onb-sw-t" aria-hidden="true"></span>' +
      '<span class="onb-sw-l">' + (on ? "On" : "Off") + "</span></label></div></div>";
  }

  function stepDone() {
    var h = head(G.check, "Setup complete", STEPS[3]);
    var lines = S.applied.length ? S.applied : ["Defaults kept — nothing was changed."];
    h += '<ul class="onb-sum">' + lines.map(function (l) {
      return "<li>" + E(l) + "</li>";
    }).join("") + "</ul>";
    h += '<p class="onb-p">' +
      'Quick Ask is on <span class="onb-key">' + E("⌃⌥Space") + "</span>" +
      E(" from anywhere on the Mac — ask without leaving what you are doing.") +
      "</p>";
    h += '<p class="onb-p">' +
      E("Everything on this screen lives in Settings, and you can run this setup " +
        "again from Settings › Overview.") + "</p>";
    return h;
  }

  var BODY = { welcome: stepWelcome, models: stepModels, prefs: stepPrefs, done: stepDone };

  // ==========================================================================
  // the sheet
  // ==========================================================================
  function sheetHTML() {
    var body = "";
    try { body = (BODY[stepKey()] || stepWelcome)(); } catch (e) { body = ""; }
    var rail = '<ol class="onb-rail" aria-hidden="true">' +
      STEPS.map(function (_, i) {
        return "<li" + (i <= S.step ? ' class="on"' : "") + "></li>";
      }).join("") + "</ol>";

    var nextLabel, nextAct;
    if (stepKey() === "models") {
      var rec = (S.data || {}).recommendation || {};
      var pick = S.pick || {};
      var ids = [pick.primary || rec.primary,
                 pick.background !== undefined ? pick.background : rec.background]
        .filter(Boolean);
      var byId = {};
      (rec.fits || []).forEach(function (f) { byId[f.id] = f; });
      var allHave = ids.length > 0 && ids.every(function (i) {
        return (byId[i] || {}).downloaded;
      });
      if (allHave || (S.dl && S.dl.started)) { nextLabel = "Continue"; nextAct = "next"; }
      else { nextLabel = "Download recommended"; nextAct = "download"; }
    } else if (lastStep()) {
      nextLabel = "Start"; nextAct = "finish";
    } else {
      nextLabel = "Continue"; nextAct = "next";
    }
    if (S.busy === "download") nextLabel = "Starting…";
    if (S.busy === "finish") nextLabel = "Finishing…";

    var secondary = "";
    if (stepKey() === "models" && nextAct === "download") {
      secondary = '<button type="button" class="onb-b" data-act="next">' +
        "I&rsquo;ll choose later</button>";
    }

    return CSS() + rail +
      '<div class="onb-scroll"><div class="onb-col">' + body +
      (S.err ? '<p class="onb-err" role="alert">' + E(S.err) + "</p>" : "") +
      "</div></div>" +
      '<div class="onb-foot"><div class="onb-foot-in">' +
      '<span class="onb-step-n">Step ' + (S.step + 1) + " of " + STEPS.length + "</span>" +
      (S.step > 0
        ? '<button type="button" class="onb-b" data-act="back">Back</button>' : "") +
      secondary +
      '<button type="button" class="onb-b onb-go" data-act="' + nextAct + '"' +
      (S.busy ? " disabled" : "") + ">" + nextLabel + "</button>" +
      "</div></div>";
  }

  function el() { var d = D(); return d ? d.getElementById(SHEET_ID) : null; }

  function paint() {
    var d = D();
    if (!d) return;
    var n = el();
    if (!n) {
      if (!d.body) return;
      n = d.createElement("div");
      n.id = SHEET_ID;
      n.setAttribute("role", "dialog");
      n.setAttribute("aria-modal", "true");
      n.setAttribute("aria-label", "Hermes setup");
      d.body.appendChild(n);
    }
    n.hidden = !S.open;
    if (!S.open) return;
    try { n.innerHTML = sheetHTML(); } catch (e) { return; }
    wire(n);
    // focus the primary action so Enter works immediately and a screen reader
    // lands inside the dialog rather than behind it
    try {
      var go = n.querySelector(".onb-go");
      if (go && (!d.activeElement || !n.contains(d.activeElement))) go.focus();
    } catch (e) {}
  }

  function wire(n) {
    if (!n || !n.querySelectorAll) return;
    var each = function (sel, fn) {
      Array.prototype.slice.call(n.querySelectorAll(sel)).forEach(fn);
    };
    each("button[data-act]", function (b) {
      b.onclick = function () { act(b.getAttribute("data-act")); };
    });
    each("input[data-pref]", function (i) {
      i.onchange = function () { setPref(i.getAttribute("data-pref"), !!i.checked); };
    });
    each("input[data-seg]", function (i) {
      i.onchange = function () {
        if (!i.checked) return;
        var k = i.getAttribute("data-seg");
        if (k === "theme") { applyTheme(i.value); S.prefs.theme = i.value; paint(); }
        else if (k === "idle") setIdle(i.value);
      };
    });
    each("input[data-qh]", function (i) {
      i.onchange = function () {
        S.prefs.quiet_hours = S.prefs.quiet_hours || {};
        S.prefs.quiet_hours[i.getAttribute("data-qh")] = i.value;
      };
    });
    each("input[data-alt]", function (i) {
      i.onchange = function () {
        var rec = (S.data || {}).recommendation || {};
        var alt = rec.alt || {};
        S.pick = i.checked
          ? { primary: alt.primary, background: alt.background, alt: true }
          : { primary: rec.primary, background: rec.background, alt: false };
        paint();
      };
    });
    n.onkeydown = onKey;
  }

  function setPref(key, val) {
    S.prefs = S.prefs || {};
    S.prefs[key] = val;
    paint();               // the switch label ("On"/"Off") is part of the markup
  }

  function setIdle(v) {
    S.prefs = S.prefs || {};
    if (v === "never") { S.prefs.idle_enabled = false; }
    else { S.prefs.idle_enabled = true; S.prefs.idle_min = Number(v); }
    paint();
  }

  // ---- keyboard ------------------------------------------------------------
  // Tab is trapped inside the sheet (it is modal); Esc obeys the rule in the
  // header comment; Enter on a non-button falls through to the primary action.
  function onKey(ev) {
    if (!S.open || !ev) return;
    if (ev.key === "Escape") {
      ev.stopPropagation();
      if (S.rerun || lastStep()) { ev.preventDefault(); close(); }
      else ev.preventDefault();      // first run: swallowed on purpose
      return;
    }
    if (ev.key === "Tab") { trap(ev); return; }
    if (ev.key === "Enter" && !ev.shiftKey) {
      var t = ev.target;
      var tag = t && t.tagName ? t.tagName.toLowerCase() : "";
      if (tag === "button" || tag === "input" || tag === "select" || tag === "textarea") return;
      ev.preventDefault();
      var n = el(), go = n && n.querySelector ? n.querySelector(".onb-go") : null;
      if (go && !go.disabled) go.click();
    }
  }

  var FOCUSABLE = 'button:not(:disabled),[href],input:not(:disabled),select,' +
    'textarea,[tabindex]:not([tabindex="-1"])';

  function trap(ev) {
    var n = el();
    if (!n || !n.querySelectorAll) return;
    var d = D();
    var list = Array.prototype.slice.call(n.querySelectorAll(FOCUSABLE))
      .filter(function (x) {
        return x.offsetParent !== null || x.getClientRects().length > 0 ||
          (x.type === "radio" || x.type === "checkbox");
      });
    if (!list.length) return;
    var first = list[0], last = list[list.length - 1];
    var cur = d ? d.activeElement : null;
    if (ev.shiftKey && (cur === first || !n.contains(cur))) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && cur === last) {
      ev.preventDefault(); first.focus();
    }
  }

  // ==========================================================================
  // actions
  // ==========================================================================
  async function act(a) {
    S.err = "";
    if (a === "back") {
      if (S.step > 0) { S.step--; paint(); }
      return;
    }
    if (a === "next") {
      await saveStep();
      if (S.step < STEPS.length - 1) S.step++;
      // the summary is computed at the moment step 4 is REACHED, so it reports
      // what was actually applied rather than re-deriving it on every repaint
      if (stepKey() === "done") S.applied = summarize();
      paint();
      return;
    }
    if (a === "download") { await startDownload(); return; }
    if (a === "finish") { await finish(); return; }
  }

  // Persist what the CURRENT step changed, through /api/onboarding/apply.
  // Each step sends only its own fields, so a validation error on one screen
  // cannot roll back a choice made on another.
  async function saveStep() {
    var k = stepKey(), body = null;
    var p = S.prefs || {};
    if (k === "welcome") {
      body = { briefings: p.briefings !== false, news: p.news !== false };
      if ((S.data || {}).detect && S.data.detect.claude_cli) {
        body.claude_escalation = p.claude_escalation !== false;
      }
    } else if (k === "prefs") {
      body = {
        theme: p.theme || currentTheme(),
        idle_min: p.idle_enabled === false ? "never" : (p.idle_min || 10),
        prewarm: p.prewarm !== false,
        briefings: p.briefings !== false,
        news: p.news !== false,
        quiet_hours: p.quiet_hours || undefined
      };
      if ((S.data || {}).detect && S.data.detect.claude_cli) {
        body.claude_escalation = p.claude_escalation !== false;
      }
    }
    if (!body) return;
    S.busy = "save"; paint();
    try {
      var r = await jpost("/api/onboarding/apply", body);
      if (r && r.ok === false && (r.errors || []).length) S.err = r.errors.join(" · ");
    } catch (e) {
      S.err = "Could not save: " + (e && e.message ? e.message : "network error");
    }
    S.busy = "";
  }

  async function startDownload() {
    var rec = (S.data || {}).recommendation || {};
    var pick = S.pick || {};
    var primary = pick.primary || rec.primary;
    var background = pick.background !== undefined ? pick.background : rec.background;
    S.busy = "download"; S.err = ""; paint();
    var r = await jpost("/api/onboarding/apply", {
      primary: primary, background: background || null, download: true
    });
    S.busy = "";
    if (r && r.ok === false) {
      S.err = (r.errors || ["Could not start the download."]).join(" · ");
      paint();
      return;
    }
    var ids = [primary, background].filter(Boolean);
    S.dl = { ids: ids, done: {}, downloading: {}, started: true };
    paint();
    pollModels();
  }

  // Progress comes from /api/models' OWN `downloading` / `downloaded` flags —
  // the same source the model menu reads. No second progress protocol.
  function pollModels() {
    if (dlTimer) { clearInterval(dlTimer); dlTimer = null; }
    dlTimer = setInterval(async function () {
      if (!S.open || !S.dl || !S.dl.started) {
        clearInterval(dlTimer); dlTimer = null; return;
      }
      try {
        var m = await jget("/api/models");
        var any = false;
        (m.models || []).forEach(function (x) {
          if (S.dl.ids.indexOf(x.id) < 0) return;
          S.dl.done[x.id] = !!x.downloaded;
          S.dl.downloading[x.id] = !!x.downloading;
          if (x.downloading) any = true;
        });
        if (stepKey() === "models") paint();
        if (!any && S.dl.ids.every(function (i) { return S.dl.done[i]; })) {
          clearInterval(dlTimer); dlTimer = null;
        }
      } catch (e) {}
    }, 2500);
  }

  function summarize() {
    var p = S.prefs || {};
    var rec = (S.data || {}).recommendation || {};
    var pick = S.pick || {};
    var byId = {};
    (rec.fits || []).forEach(function (f) { byId[f.id] = f; });
    var out = [];
    var primary = pick.primary || rec.primary;
    var background = pick.background !== undefined ? pick.background : rec.background;
    if (primary) {
      out.push("Assistant model: " + ((byId[primary] || {}).label || primary) +
        (background ? ", background lane: " + ((byId[background] || {}).label || background)
                    : ", no background lane"));
    }
    if (S.dl && S.dl.started) out.push("Downloading in the background — the model menu shows progress.");
    out.push("Appearance: " + ({ system: "follows macOS", light: "light", dark: "dark" }[
      p.theme || currentTheme()] || "follows macOS"));
    out.push(p.idle_enabled === false
      ? "The model stays loaded — it will not sleep on its own."
      : "The model sleeps after " + (p.idle_min || 10) +
        " minutes idle and wakes on your next message.");
    out.push("Prewarm after wake: " + (p.prewarm !== false ? "on" : "off"));
    if ((S.data || {}).detect && S.data.detect.claude_cli) {
      out.push("Claude escalation: " + (p.claude_escalation !== false ? "on" : "off"));
    }
    var q = p.quiet_hours || {};
    out.push("Briefings " + (p.briefings !== false ? "on" : "off") +
      ", news " + (p.news !== false ? "on" : "off") +
      ", quiet hours " + (q.start || "22:00") + " to " + (q.end || "07:00"));
    return out;
  }

  async function finish() {
    S.busy = "finish"; paint();
    try { await jpost("/api/onboarding/done", {}); } catch (e) {}
    S.busy = "";
    close();
    // hand the user straight to the thing the setup was for
    try {
      var d = D();
      var i = d && d.getElementById ? d.getElementById("input") : null;
      if (i && i.focus) i.focus();
    } catch (e) {}
    mountCard();
  }

  // ==========================================================================
  // open / close
  // ==========================================================================
  async function load() {
    var j = await jget("/api/onboarding/state");
    S.data = j || {};
    var p = (j && j.prefs) || {};
    S.prefs = {
      theme: currentTheme(),
      idle_min: p.idle_min || 10,
      idle_enabled: p.idle_enabled !== false,
      prewarm: p.prewarm !== false,
      claude_escalation: p.claude_escalation !== false,
      briefings: p.briefings !== false,
      news: p.news !== false,
      quiet_hours: {
        start: (p.quiet_hours || {}).start || "22:00",
        end: (p.quiet_hours || {}).end || "07:00"
      }
    };
    var rec = (j && j.recommendation) || {};
    S.pick = { primary: rec.primary, background: rec.background, alt: false };
    return j;
  }

  async function open(opts) {
    opts = opts || {};
    try { S.lastFocus = D() ? D().activeElement : null; } catch (e) {}
    S.rerun = !!opts.rerun;
    S.step = 0; S.err = ""; S.busy = ""; S.dl = null; S.applied = [];
    try { await load(); } catch (e) {
      S.err = "Could not read this Mac's setup state.";
      S.data = S.data || {}; S.prefs = S.prefs || {};
    }
    S.open = true;
    try { if (D() && D().body) D().body.style.overflow = "hidden"; } catch (e) {}
    paint();
  }

  function close() {
    S.open = false;
    if (dlTimer) { clearInterval(dlTimer); dlTimer = null; }
    try { if (D() && D().body) D().body.style.overflow = ""; } catch (e) {}
    var n = el();
    if (n) n.hidden = true;
    try { if (S.lastFocus && S.lastFocus.focus) S.lastFocus.focus(); } catch (e) {}
  }

  // ==========================================================================
  // Settings row — "Run setup again", injected the way aux_update injects its
  // card: straight into #sec-overview when the shell exists, otherwise into
  // #view-mind, whose relocator re-homes unknown ids (and the next paint moves
  // it back to Overview). aux_settings_shell.js is never edited.
  // ==========================================================================
  function cardHost() {
    var d = D();
    if (!d) return null;
    return d.getElementById("sec-overview") || d.getElementById("view-mind");
  }

  function cardHTML() {
    var done = (S.data && S.data.done) || false;
    var when = "";
    if (done && S.data && S.data.done_at) {
      try {
        var dt = new Date(S.data.done_at * 1000);
        if (!isNaN(dt.getTime())) {
          var hh = dt.getHours(), mm = dt.getMinutes(), ap = hh >= 12 ? "PM" : "AM";
          hh = hh % 12; if (hh === 0) hh = 12;
          when = " · last run " + (dt.getMonth() + 1) + "/" + dt.getDate() + " " +
            hh + ":" + (mm < 10 ? "0" + mm : mm) + " " + ap;
        }
      } catch (e) {}
    }
    return CSS() +
      '<h2 style="display:flex;align-items:center;gap:7px">' + svg(G.shield, 16) +
      "First-run setup</h2>" +
      '<div class="onb-set-row"><div class="onb-set-txt">' +
      '<div class="onb-set-l">Run setup again</div>' +
      '<div class="onb-set-d">' +
      E("Re-check this Mac, review what leaves it, and revisit the model " +
        "recommendation and preferences." + when) + "</div></div>" +
      '<button type="button" class="onb-set-b" id="onb-rerun">Run setup</button></div>';
  }

  function mountCard() {
    var d = D();
    if (!d) return;
    var host = cardHost();
    if (!host) return;
    var n = d.getElementById(CARD_ID);
    if (!n) {
      n = d.createElement("section");
      n.id = CARD_ID;
      n.className = "card glass";
      host.appendChild(n);
    } else if (n.parentNode && n.parentNode.id !== "sec-overview") {
      var ov = d.getElementById("sec-overview");
      if (ov && ov !== n.parentNode) { try { ov.appendChild(n); } catch (e) {} }
    }
    try { n.innerHTML = cardHTML(); } catch (e) { return; }
    var b = n.querySelector ? n.querySelector("#onb-rerun") : null;
    if (b) b.onclick = function () { open({ rerun: true }); };
  }

  // chain window.mindExtras exactly like the other aux settings cards
  var prevExtras = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prevExtras === "function") { try { await prevExtras(); } catch (e) {} }
    try {
      if (!S.data) { try { S.data = await jget("/api/onboarding/state"); } catch (e) {} }
      mountCard();
    } catch (e) {}
  };

  // ==========================================================================
  // boot — auto-open on a first run, and NEVER when done is true
  // ==========================================================================
  async function boot() {
    var j = null;
    try { j = await jget("/api/onboarding/state"); } catch (e) { return; }
    if (!j || j.ok === false) return;
    S.data = j;
    if (j.done === false) { try { await open({ rerun: false }); } catch (e) {} }
  }

  try {
    if (typeof document !== "undefined" && document.readyState === "loading" &&
        document.addEventListener) {
      document.addEventListener("DOMContentLoaded", function () { boot(); }, { once: true });
    } else if (typeof document !== "undefined") {
      boot();
    }
  } catch (e) {}

  // public + headless-harness surface
  W.hermesOnboarding = {
    open: function (o) { return open(o || { rerun: true }); },
    close: close,
    reset: async function () {
      var r = await jpost("/api/onboarding/reset", {});
      try { S.data = await jget("/api/onboarding/state"); } catch (e) {}
      mountCard();
      return r;
    },
    // reusable pieces (networkTableHTML is the seed of the Data & Network panel)
    networkTableHTML: networkTableHTML, NETWORK_FACTS: NETWORK_FACTS,
    statusRows: statusRows, sheetHTML: sheetHTML, cardHTML: cardHTML,
    gb: gb, fitLine: fitLine, fitClass: fitClass, sysLine: sysLine,
    summarize: summarize, applyTheme: applyTheme, currentTheme: currentTheme,
    STEPS: STEPS, state: S,
    go: function (i) { S.step = Math.max(0, Math.min(STEPS.length - 1, i | 0)); paint(); }
  };
})();
