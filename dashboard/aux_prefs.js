// aux_prefs.js — Preferences dropdown (replaces the standalone theme toggle).
//
// Auto-served at /aux_prefs.js, loaded after the inline dashboard script. Part
// of the Hub·Agent·Settings restructure: the Settings tab is leaving the tab
// bar (handled by aux_agent.js) and the full System Settings page is now
// reached from THIS menu's "System Settings…" row via setView('mind').
//
// What it does:
//   * Hides the standalone #themebtn and drops a bespoke gear button in its
//     place. The gear opens a macOS-style Preferences dropdown that mirrors the
//     .modelmenu.glass look, positioning, click-outside-to-close and Esc.
//   * Every control ACTUALLY works — no placeholder toggles:
//       - Appearance  : Light / Dark / Auto segmented control, wired to the
//                        existing theme mechanism (data-theme + 'hermes_theme').
//                        Auto removes data-theme so :root color-scheme:light dark
//                        + the prefers-color-scheme media query drive the palette.
//       - Reduce motion: sets :root[data-reduce] + injects a CSS rule that kills
//                        animation/transition/scroll-behavior. Persisted, applied
//                        on load. NOTE: this covers CSS/aurora motion only —
//                        Motion One JS honours the OS reduce-motion setting
//                        separately (the REDUCE const), which we do NOT override.
//       - Agent       : Pause / Resume kill switch (POST /api/agent/pause|resume,
//                        pause gated by confirm()), reflecting .paused polled from
//                        /api/models while open, with a 12-hour "Paused H:MM AM/PM"
//                        caption. Mirrors the model-menu power row.
//       - System Settings… : setView('mind') → the 12-panel aux_settings_shell.
//       - Proactive & quiet hours : setView('mind') + deep-link #settings/proactive.
//       - About       : Hermes Assistant · local-first · on your Mac · active model
//                        (from /api/models), display-only.
//
// Self-contained IIFE; all shared helpers ($, esc, animate, SPRING, REDUCE,
// setView, localStorage, fetch, confirm) are typeof-guarded so a headless render
// harness can never throw. CLAUDE.md laws: zero emoji, bespoke SVG, 12-hour time,
// Motion One animate() + REDUCE fallback for open/close.

(function () {
  "use strict";
  try {
    var D = (typeof document !== "undefined") ? document : null;
    if (!D || !D.createElement) return;

    // ---- guarded helpers ---------------------------------------------------
    function byId(id) {
      try { if (typeof $ === "function") { var r = $(id); if (r) return r; } } catch (e) {}
      try { return D.getElementById ? D.getElementById(id) : null; } catch (e) { return null; }
    }
    function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
    var HAS_ANIM = (typeof animate === "function");
    var SPR = (typeof SPRING !== "undefined") ? SPRING : "cubic-bezier(.22,1,.36,1)";
    var LOWMO = (typeof REDUCE !== "undefined") ? REDUCE : false;
    var HTML = D.documentElement || null;

    function lsGet(k) { try { return (typeof localStorage !== "undefined") ? localStorage.getItem(k) : null; } catch (e) { return null; } }
    function lsSet(k, v) { try { if (typeof localStorage !== "undefined") localStorage.setItem(k, v); } catch (e) {} }
    function lsDel(k) { try { if (typeof localStorage !== "undefined") localStorage.removeItem(k); } catch (e) {} }
    function t12(ms) { try { return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); } catch (e) { return ""; } }

    // element factory (createElement-based — no innerHTML for queryable nodes)
    function mk(tag, o) {
      var el = D.createElement(tag); o = o || {};
      if (o.cls) el.className = o.cls;
      if (o.id) el.id = o.id;
      if (o.css) { try { el.style.cssText = o.css; } catch (e) {} }
      if (o.text != null) el.textContent = o.text;
      if (o.html != null) el.innerHTML = o.html;
      if (o.attrs) for (var k in o.attrs) { try { el.setAttribute(k, o.attrs[k]); } catch (e) {} }
      if (o.on) el.onclick = o.on;
      if (o.kids) for (var i = 0; i < o.kids.length; i++) if (o.kids[i]) el.appendChild(o.kids[i]);
      return el;
    }
    function svg(inner, extraCls) {
      return '<svg class="ic' + (extraCls ? " " + extraCls : "") + '" viewBox="0 0 24 24" aria-hidden="true">' + inner + "</svg>";
    }

    // ---- bespoke icons (zero emoji) ----------------------------------------
    var ICON = {
      gear: '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"/>' +
        '<path d="M19.4 13a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
      sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
      moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
      auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/>',
      pause: '<rect x="6" y="5" width="4" height="14" rx="1.3"/><rect x="14" y="5" width="4" height="14" rx="1.3"/>',
      play: '<path d="M7 4l12 8-12 8z"/>',
      bell: '<path d="M12 3a5 5 0 0 0-5 5c0 5-2 6-2 6h14s-2-1-2-6a5 5 0 0 0-5-5z"/><path d="M10 18.5a2 2 0 0 0 4 0"/>',
      chev: '<polyline points="9 6 15 12 9 18"/>'
    };

    // ---- one-time CSS (includes the real reduce-motion rule) ---------------
    function injectCSS() {
      if (byId("aux-prefs-css")) return;
      var css = [
        // the honest reduce-motion kill switch — active only when data-reduce is set
        ":root[data-reduce] *{animation:none!important;transition:none!important;scroll-behavior:auto!important}",
        ".prefswrap{position:relative;display:inline-flex}",
        ".prefsbtn{padding:7px;border-radius:10px}",
        ".prefsbtn .ic{width:16px;height:16px}",
        ".prefsmenu{position:absolute;top:calc(100% + 8px);right:0;width:286px;z-index:41;padding:8px;" +
          "border-radius:14px;background:color-mix(in srgb,var(--ground) 92%,transparent);" +
          "-webkit-backdrop-filter:blur(40px) saturate(140%);backdrop-filter:blur(40px) saturate(140%);" +
          "border:1px solid var(--edge);box-shadow:0 20px 50px -12px rgba(0,0,0,.6),inset 0 1px 0 var(--specular)}",
        ".prefsmenu[hidden]{display:none}",
        ".pf-h{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--faint);font-weight:640;padding:9px 8px 5px}",
        ".pf-seg{display:flex;gap:4px;padding:3px;border-radius:11px;background:var(--chip);border:1px solid var(--hairline)}",
        ".pf-seg b{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:5px;font-size:11.5px;" +
          "font-weight:600;color:var(--muted);padding:6px 4px;border-radius:8px;cursor:pointer;user-select:none;" +
          "transition:background .15s,color .15s}",
        ".pf-seg b .ic{width:13px;height:13px}",
        ".pf-seg b:hover{color:var(--ink)}",
        ".pf-seg b.on{background:linear-gradient(180deg,var(--iris-2),var(--iris));color:var(--iris-ink);" +
          "box-shadow:inset 0 1px 0 rgba(255,255,255,.35),0 2px 8px -3px color-mix(in srgb,var(--iris) 70%,transparent)}",
        ".pf-row{display:flex;align-items:center;gap:10px;padding:9px 8px;border-radius:10px;cursor:pointer;transition:background .15s}",
        ".pf-row:hover{background:var(--chip)}",
        ".pf-row .txt{min-width:0;flex:1}",
        ".pf-row .nm{font-size:12.5px;font-weight:600}",
        ".pf-row .rm2{font-size:10.5px;color:var(--faint)}",
        // .pf-tog geometry/colour/motion now lives in index.html next to
        // .wt-tog — ONE switch, two legacy class names. Re-declaring it here
        // would silently win (an aux <style> is appended after the inline one),
        // which is exactly how the two implementations drifted apart: this one
        // had a hardcoded #fff knob and 200ms, aux_watchtower's was green/180ms.
        // Anything genuinely local to the prefs popover goes below.
        ".pf-tog{margin-left:auto}",
        ".pf-power{display:flex;align-items:center;gap:10px;padding:9px 8px;border-radius:10px;cursor:pointer;" +
          "transition:background .15s;color:var(--warn)}",
        ".pf-power:hover{background:var(--chip)}",
        ".pf-power.resume{color:var(--ok)}",
        ".pf-power .ic{width:15px;height:15px;flex:none;fill:currentColor;stroke:currentColor}",
        ".pf-power .nm{font-size:12.5px;font-weight:600}",
        ".pf-power .rm2{font-size:10.5px;color:var(--faint)}",
        ".pf-div{height:1px;background:var(--hairline);margin:6px 4px}",
        ".pf-nav{display:flex;align-items:center;gap:10px;padding:10px 8px;border-radius:10px;cursor:pointer;transition:background .15s}",
        ".pf-nav:hover{background:var(--chip)}",
        ".pf-nav .ic{width:16px;height:16px;flex:none;color:var(--iris)}",
        ".pf-nav.sub .ic{width:14px;height:14px;color:var(--muted)}",
        ".pf-nav .txt{min-width:0;flex:1}",
        ".pf-nav .nm{font-size:12.5px;font-weight:640}",
        ".pf-nav.sub .nm{font-weight:600}",
        ".pf-nav .rm2{font-size:10.5px;color:var(--faint)}",
        ".pf-nav .chev{width:14px;height:14px;color:var(--faint);flex:none;stroke-width:2}",
        ".pf-about{padding:9px 8px 5px}",
        ".pf-about .t{font-size:12px;font-weight:660}",
        ".pf-about .s{font-size:10.5px;color:var(--faint);margin-top:2px}",
        ".pf-about .m{font-size:10.5px;color:var(--muted);margin-top:5px;display:flex;align-items:center;gap:6px}"
      ].join("");
      var st = mk("style", { id: "aux-prefs-css", html: css });
      (D.head || D.body || HTML).appendChild(st);
    }

    // ---- theme (Light / Dark / Auto) ---------------------------------------
    function currentTheme() {
      var s = lsGet("hermes_theme");
      if (s === "light" || s === "dark") return s;
      var a = HTML ? HTML.getAttribute("data-theme") : null;
      if (a === "light" || a === "dark") return a;
      return "auto";
    }
    function applyTheme(mode) {
      if (mode === "light") { if (HTML) HTML.setAttribute("data-theme", "light"); lsSet("hermes_theme", "light"); }
      else if (mode === "dark") { if (HTML) HTML.setAttribute("data-theme", "dark"); lsSet("hermes_theme", "dark"); }
      else { if (HTML) HTML.removeAttribute("data-theme"); lsSet("hermes_theme", "auto"); }
      reflectSeg(mode);
    }
    function reflectSeg(mode) {
      for (var k in segBtns) {
        if (!segBtns[k]) continue;
        try { segBtns[k].classList.toggle("on", k === mode); } catch (e) {}
      }
    }

    // ---- reduce motion (CSS/aurora only; Motion One JS uses OS REDUCE) ------
    function reduceOn() {
      try { if (HTML && HTML.getAttribute("data-reduce")) return true; } catch (e) {}
      return lsGet("hermes_reduce") === "1";
    }
    function applyReduce(on) {
      if (HTML) { if (on) HTML.setAttribute("data-reduce", "1"); else HTML.removeAttribute("data-reduce"); }
      lsSet("hermes_reduce", on ? "1" : "0");
      if (togEl) { try { togEl.classList.toggle("on", on); } catch (e) {} }
    }

    // ---- agent pause / resume (mirrors the model-menu power row) ------------
    var agentPaused = false;
    function updateAgent(paused) {
      agentPaused = !!paused;
      if (!powerRow) return;
      powerRow.className = "pf-power" + (agentPaused ? " resume" : "");
      if (powerIcon) powerIcon.innerHTML = agentPaused ? ICON.play : ICON.pause;
      if (powerName) powerName.textContent = agentPaused ? "Resume agent" : "Pause agent";
      if (powerSub) {
        if (agentPaused) {
          var at = lsGet("hermes_paused_at");
          powerSub.textContent = at ? ("Paused " + t12(+at) + " · chat & Telegram are down") : "Paused · chat & Telegram are down";
        } else {
          powerSub.textContent = "parks the model — chat & Telegram pause too";
        }
      }
    }
    function onPower() {
      if (agentPaused) {
        doResume();
      } else {
        if (typeof confirm === "function" &&
          !confirm("Pause the agent? This unloads the local model to free its RAM. Dashboard chat and Telegram replies won't work until you resume.")) return;
        doPause();
      }
    }
    function doPause() {
      lsSet("hermes_paused_at", String(Date.now()));
      updateAgent(true);
      if (typeof fetch === "function") { try { fetch("/api/agent/pause", { method: "POST" }).catch(function () {}); } catch (e) {} }
    }
    function doResume() {
      lsDel("hermes_paused_at");
      updateAgent(false);
      if (typeof fetch === "function") { try { fetch("/api/agent/resume", { method: "POST" }).catch(function () {}); } catch (e) {} }
      try { if (typeof loadModels === "function") loadModels(); } catch (e) {}
    }

    // ---- live poll while open: agent .paused + active model name -----------
    function poll() {
      if (typeof fetch !== "function") return;
      var p;
      try { p = fetch("/api/models"); } catch (e) { return; }
      if (!p || !p.then) return;
      p.then(function (r) { return r.json(); }).then(function (d) {
        if (!d) return;
        updateAgent(!!d.paused);
        var act = null, ms = d.models || [];
        for (var i = 0; i < ms.length; i++) { if (ms[i] && ms[i].active) { act = ms[i]; break; } }
        var label = act ? (act.label || act.id) : (d.active ? String(d.active).split("/").pop() : "");
        if (aboutModel) aboutModel.textContent = label || "local model";
      }).catch(function () {});
    }

    // ---- open / close (Motion One + REDUCE fallback) -----------------------
    var menuOpen = false, pollTimer = null;
    function openMenu() {
      if (menuOpen || !menu) return;
      menuOpen = true;
      reflectSeg(currentTheme());
      if (togEl) { try { togEl.classList.toggle("on", reduceOn()); } catch (e) {} }
      menu.hidden = false;
      if (HAS_ANIM && !LOWMO) {
        // NB: the end keyframe must be an explicit identity, not "none" —
        // Motion One decomposes a scale()-carrying pair against "none" into a
        // ZERO matrix, which rendered this whole menu at 0x0 (the gear looked
        // dead). Translate-only pairs elsewhere in the app are unaffected.
        try { animate(menu, { opacity: [0, 1], transform: ["translateY(-6px) scale(.98)", "translateY(0px) scale(1)"] }, { duration: 0.2, easing: SPR }); } catch (e) {}
      }
      poll();
      try { pollTimer = setInterval(poll, 4000); } catch (e) {}
    }
    function closeMenu() {
      if (!menuOpen || !menu) return;
      menuOpen = false;
      if (pollTimer) { try { clearInterval(pollTimer); } catch (e) {} pollTimer = null; }
      var hide = function () { try { menu.hidden = true; } catch (e) {} };
      if (HAS_ANIM && !LOWMO) {
        var a;
        try { a = animate(menu, { opacity: [1, 0], transform: ["translateY(0px) scale(1)", "translateY(-6px) scale(.98)"] }, { duration: 0.15, easing: SPR }); } catch (e) {}
        var fin = a && a.finished ? a.finished : (a && a.then ? a : null);
        if (fin && fin.then) fin.then(hide, hide); else setTimeout(hide, 160);
      } else hide();
    }
    function toggleMenu() { menuOpen ? closeMenu() : openMenu(); }

    // ---- build the dropdown once -------------------------------------------
    var menu, segBtns = { light: null, dark: null, auto: null };
    var togEl, powerRow, powerIcon, powerName, powerSub, aboutModel;

    function segItem(mode, label, icon) {
      // icon + label are display-only (never queried) → innerHTML is fine here.
      var b = mk("b", {
        attrs: { role: "button", "aria-label": label },
        html: svg(icon) + "<span>" + E(label) + "</span>",
        on: function () { applyTheme(mode); }
      });
      segBtns[mode] = b;
      return b;
    }

    function buildMenu() {
      menu = mk("div", { cls: "prefsmenu glass", id: "prefs-menu", attrs: { role: "menu" } });
      menu.hidden = true;

      // Appearance
      menu.appendChild(mk("div", { cls: "pf-h", text: "Appearance" }));
      var seg = mk("div", { cls: "pf-seg", attrs: { role: "group", "aria-label": "Theme" } });
      seg.appendChild(segItem("light", "Light", ICON.sun));
      seg.appendChild(segItem("dark", "Dark", ICON.moon));
      seg.appendChild(segItem("auto", "Auto", ICON.auto));
      menu.appendChild(seg);

      // General — reduce motion
      menu.appendChild(mk("div", { cls: "pf-h", text: "General" }));
      togEl = mk("div", { cls: "pf-tog", attrs: { role: "switch" }, kids: [mk("i")] });
      var reduceRow = mk("div", {
        cls: "pf-row", attrs: { role: "menuitemcheckbox" },
        kids: [
          mk("div", { cls: "txt", kids: [
            mk("div", { cls: "nm", text: "Reduce motion" }),
            mk("div", { cls: "rm2", text: "Stills the aurora and UI animation" })
          ] }),
          togEl
        ]
      });
      reduceRow.onclick = function () { applyReduce(!reduceOn()); };
      menu.appendChild(reduceRow);

      // Agent — pause / resume
      menu.appendChild(mk("div", { cls: "pf-h", text: "Agent" }));
      powerIcon = mk("span", { html: svg(ICON.pause) });
      powerName = mk("div", { cls: "nm", text: "Pause agent" });
      powerSub = mk("div", { cls: "rm2", text: "parks the model — chat & Telegram pause too" });
      powerRow = mk("div", {
        cls: "pf-power",
        kids: [powerIcon, mk("div", { cls: "txt", kids: [powerName, powerSub] })]
      });
      powerRow.onclick = onPower;
      menu.appendChild(powerRow);

      // divider
      menu.appendChild(mk("div", { cls: "pf-div" }));

      // System Settings… (the main entry now the tab is hidden)
      var sysRow = mk("div", {
        cls: "pf-nav",
        kids: [
          mk("span", { html: svg(ICON.gear) }),
          mk("div", { cls: "txt", kids: [
            mk("div", { cls: "nm", text: "System Settings…" }),
            mk("div", { cls: "rm2", text: "All controls · appearance, bridge, autonomy" })
          ] }),
          mk("span", { cls: "chev", html: svg(ICON.chev) })
        ]
      });
      sysRow.onclick = function () { closeMenu(); try { if (typeof setView === "function") setView("mind"); } catch (e) {} };
      menu.appendChild(sysRow);

      // Proactive & quiet hours — deep-link into the settings page
      var proRow = mk("div", {
        cls: "pf-nav sub",
        kids: [
          mk("span", { html: svg(ICON.bell) }),
          mk("div", { cls: "txt", kids: [
            mk("div", { cls: "nm", text: "Proactive & quiet hours" }),
            mk("div", { cls: "rm2", text: "Briefings, watchtower, notifications" })
          ] }),
          mk("span", { cls: "chev", html: svg(ICON.chev) })
        ]
      });
      proRow.onclick = function () {
        closeMenu();
        try { if (typeof setView === "function") setView("mind"); } catch (e) {}
        try { if (typeof location !== "undefined" && location) location.hash = "#settings/proactive"; } catch (e) {}
      };
      menu.appendChild(proRow);

      // divider + About
      menu.appendChild(mk("div", { cls: "pf-div" }));
      aboutModel = mk("b", { text: "…" });
      var about = mk("div", {
        cls: "pf-about",
        kids: [
          mk("div", { cls: "t", text: "Hermes Assistant" }),
          mk("div", { cls: "s", text: "local-first · on your Mac" }),
          mk("div", { cls: "m", kids: [document.createTextNode("Model · "), aboutModel] })
        ]
      });
      menu.appendChild(about);

      return menu;
    }

    // ---- install -----------------------------------------------------------
    function install() {
      if (byId("prefs-btn")) return;             // idempotent
      var tb = byId("themebtn");
      var host = tb ? tb.parentNode : null;
      if (!host) return;                          // no header → do nothing

      injectCSS();

      // hide the standalone theme toggle it replaces
      try { tb.style.display = "none"; tb.setAttribute("aria-hidden", "true"); tb.tabIndex = -1; } catch (e) {}

      // apply persisted reduce-motion on load (before first paint of anything new)
      if (reduceOn() && HTML) { try { HTML.setAttribute("data-reduce", "1"); } catch (e) {} }

      var wrap = mk("div", { cls: "prefswrap" });
      var btn = mk("button", {
        cls: "ghost prefsbtn", id: "prefs-btn",
        attrs: { type: "button", title: "Preferences", "aria-label": "Preferences", "aria-haspopup": "true" },
        html: svg(ICON.gear)
      });
      btn.onclick = function (e) { try { e.stopPropagation(); } catch (x) {} toggleMenu(); };
      wrap.appendChild(btn);
      wrap.appendChild(buildMenu());
      host.appendChild(wrap);

      // reflect current state now (so first open is instant/correct even w/o poll)
      reflectSeg(currentTheme());
      if (togEl) { try { togEl.classList.toggle("on", reduceOn()); } catch (e) {} }

      // click-outside + Esc to close (mirror the model menu)
      try {
        D.addEventListener("click", function (e) {
          if (!menuOpen) return;
          var inside = false;
          try { inside = !!(e.target && e.target.closest && e.target.closest(".prefswrap")); } catch (x) {}
          if (!inside) closeMenu();
        });
        D.addEventListener("keydown", function (e) {
          if (menuOpen && (e.key === "Escape" || e.key === "Esc")) closeMenu();
        });
      } catch (e) {}
    }

    if (D.readyState === "loading" && D.addEventListener) {
      D.addEventListener("DOMContentLoaded", install, { once: true });
    } else {
      install();
    }

    // expose a tiny hook for the headless harness (no effect in the browser UI)
    try {
      if (typeof module !== "undefined" && module.exports) {
        module.exports = { install: install, _openMenu: function () { openMenu(); }, _closeMenu: closeMenu };
      }
    } catch (e) {}
  } catch (e) {
    // never let a failure here break the header
    try { if (typeof console !== "undefined") console.warn("aux_prefs failed:", e); } catch (x) {}
  }
})();
