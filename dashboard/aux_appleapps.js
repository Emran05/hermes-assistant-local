// aux_appleapps.js — the "Apple apps" card for Settings › Connections.
//
// Two switches, both OFF by default: Reminders and Notes automation. Turning
// one OFF (the default) means the dashboard never shells out to
// `osascript -e 'tell application "Reminders"/"Notes" ...'` — that call
// LAUNCHES the app and leaves it open, which is exactly what the owner asked
// to stop. Google Calendar (the card just above this one, aux_google.js) is
// used for calendar/"today" context instead. Turning a switch back on is a
// real trade the card says out loud: the app will open on the next refresh.
//
// PLACEMENT. Same constraint aux_promptbudget.js documents: a module cannot
// register a new Settings panel (aux_settings_shell.js builds its rail once
// from a captured PANELS array). This is therefore a CARD, mounted DIRECTLY
// into #sec-connections (Connections — Accounts and data access), right
// alongside Google/Messages. Never falls back to #view-mind.
//
// Design laws (CLAUDE.md): zero emoji, esc() on every interpolation,
// explicit transition-property, >=40px targets, colours only through the
// tokens all four palette blocks re-declare, every global helper
// typeof-guarded so this file can be eval'd in a headless harness.
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

  var CARD_ID = "mind-extra-appleapps";
  var PANEL_ID = "sec-connections";
  var SEL = "#" + CARD_ID;

  var S = {
    data: null,        // {reminders, notes} last known-good from the server
    loaded: false,
    busy: {},          // {reminders:true} while a toggle POST is in flight
    err: ""
  };

  var GLY =
    '<svg class="aaic" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<rect x="3" y="3" width="18" height="18" rx="4.5" fill="var(--iris)" opacity=".14"/>' +
    '<rect x="3" y="3" width="18" height="18" rx="4.5" fill="none" ' +
    'stroke="currentColor" stroke-width="1.5"/>' +
    '<path d="M9 12.5l2 2 4-4.5" fill="none" stroke="currentColor" ' +
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function CSS() {
    return "<style>" +
      SEL + " .aaic{flex:0 0 auto;color:var(--muted)}" +
      SEL + " .aalede{margin:2px 0 14px;font-size:12.5px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty;max-width:64ch}" +
      SEL + " ul.aarows{list-style:none;margin:0;padding:0}" +
      SEL + " ul.aarows li{border-top:1px solid var(--hairline)}" +
      SEL + " ul.aarows label{display:grid;" +
        "grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;" +
        "align-items:center;padding:11px 2px;min-height:40px;" +
        "box-sizing:border-box;cursor:pointer}" +
      SEL + " .aaname{font-size:12.5px;color:var(--ink);font-weight:640}" +
      SEL + " .aadet{grid-column:1;font-size:11.5px;color:var(--faint);" +
        "line-height:1.4;text-wrap:pretty;margin-top:1px}" +
      // switch, self-contained (no shared component in this codebase)
      SEL + " .aasw{position:relative;flex:0 0 auto;width:38px;height:23px;" +
        "border-radius:12px;background:var(--hairline);cursor:pointer;" +
        "transition-property:background-color;transition-duration:150ms;" +
        "transition-timing-function:ease-out;grid-row:1 / span 2}" +
      SEL + " .aasw.on{background:var(--iris)}" +
      SEL + " .aasw.busy{opacity:.55;cursor:default}" +
      SEL + " .aasw i{position:absolute;top:2.5px;left:2.5px;width:18px;" +
        "height:18px;border-radius:50%;background:#fff;" +
        "box-shadow:0 1px 2px rgba(0,0,0,.35);" +
        "transition-property:transform;transition-duration:150ms;" +
        "transition-timing-function:ease-out}" +
      SEL + " .aasw.on i{transform:translateX(15px)}" +
      SEL + " .aawarn{margin:12px 0 0;font-size:11.5px;line-height:1.5;" +
        "color:var(--warn)}" +
      SEL + " .aaerr{margin:10px 0 0;font-size:11.5px;line-height:1.5;" +
        "color:var(--bad)}" +
      "</style>";
  }

  var ROWS = [
    { key: "reminders", name: "Reminders",
      detail: "Widget, pop-out and the Needs-you inbox read Apple Reminders "
              + "via a script that opens the Reminders app." },
    { key: "notes", name: "Notes",
      detail: "The Scratchpad pop-out's “Recent Apple Notes” list "
              + "reads Apple Notes via a script that opens the Notes app." }
  ];

  function switchHTML(key, on, busy) {
    return '<span class="aasw' + (on ? " on" : "") + (busy ? " busy" : "") +
      '" data-k="' + E(key) + '" role="switch" aria-checked="' +
      (on ? "true" : "false") + '"><i></i></span>';
  }

  function cardHTML(state) {
    state = state || {};
    var d = state.data;
    var anyOn = !!(d && (d.reminders || d.notes));
    var rows = ROWS.map(function (r) {
      var on = !!(d && d[r.key]);
      var busy = !!(state.busy && state.busy[r.key]);
      return "<li><label>" +
        '<span class="aaname">' + E(r.name) + "</span>" +
        switchHTML(r.key, on, busy) +
        '<span class="aadet">' + E(r.detail) + "</span>" +
        "</label></li>";
    }).join("");
    return CSS() +
      "<h2>" + GLY + "Apple apps</h2>" +
      '<div class="body">' +
      '<p class="aalede">Off by default. Each of these reads its app through ' +
      "a script that macOS treats as opening it — the app launches and stays " +
      "open, even for a background widget refresh. Google Calendar (above) " +
      "covers calendar/task context without that, so both start off.</p>" +
      (d ? '<ul class="aarows">' + rows + "</ul>" : "") +
      (anyOn
        ? '<p class="aawarn">The app for anything switched on here will open ' +
          "the next time its widget or pop-out refreshes."
        : "") +
      (state.err ? '<p class="aaerr">' + E(state.err) + "</p>" : "") +
      "</div>";
  }

  // ---- data ------------------------------------------------------------
  async function jget(url) {
    try { var r = await fetch(url); return await r.json(); }
    catch (e) { return null; }
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
    var j = await jget("/api/apple_apps");
    if (!j || j.ok === false) {
      S.err = (j && j.error) ? String(j.error)
                             : "The dashboard did not answer /api/apple_apps.";
      S.loaded = true;
      return;
    }
    S.data = { reminders: !!j.reminders, notes: !!j.notes };
    S.err = "";
    S.loaded = true;
  }

  async function flip(key) {
    if (!S.data || S.busy[key]) return;
    var want = !S.data[key];
    S.busy[key] = true;
    paint();
    var j = await jpost("/api/apple_apps", (function () {
      var b = {}; b[key] = want; return b;
    })());
    delete S.busy[key];
    if (!j || j.ok === false) {
      S.err = (j && j.error) ? String(j.error) : "Could not save that change.";
      paint();
      return;
    }
    S.data = { reminders: !!j.reminders, notes: !!j.notes };
    S.err = "";
    paint();
    try {
      if (typeof W.toast === "function") {
        W.toast((key === "reminders" ? "Reminders" : "Notes") +
                (want ? " automation on" : " automation off"));
      }
    } catch (e) {}
  }

  // ---- mount -------------------------------------------------------------
  function wire(card) {
    if (!card || !card.querySelectorAll) return;
    Array.prototype.slice.call(card.querySelectorAll(".aasw"))
      .forEach(function (el) {
        el.onclick = function () {
          if (el.classList.contains("busy")) return;
          flip(el.getAttribute("data-k"));
        };
      });
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
      paint();
      try { await load(); }
      catch (e) { S.err = "Could not load Apple apps settings."; S.loaded = true; }
      loading = false;
    }
    paint();
  }

  var prev = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await mount(); } catch (e) {}
  };

  // headless-harness surface
  W.hermesAppleApps = {
    cardHTML: cardHTML, CSS: CSS, mount: mount, paint: paint, flip: flip,
    state: S, refresh: async function () { S.loaded = false; await mount(); }
  };
})();
