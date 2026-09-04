// aux_quickask.js — Menu-bar Quick Ask (P2.2, rebuilt 2026-09-03).
//
// Auto-served at /aux_quickask.js.  ONE file, TWO roles, chosen by context:
//
//   * In the menu-bar popover document (a 6-line HTML shell loaded by
//     main.swift via loadHTMLString with baseURL = the dashboard origin, so
//     every fetch is same-origin), window.__HERMES_QUICKASK__ is set and this
//     file BUILDS THE WHOLE POPOVER: status strip, ask field, action chips and
//     the live thread.  The Swift side is frozen — width is pinned at 380 and
//     the only knob we have is the `resize` bridge (clamped 320..620), so the
//     layout measures itself and asks for the height it needs.
//
//   * In the main window (index.html), __HERMES_QUICKASK__ is undefined, so
//     this file adds nothing visible — it only defines
//     window.hermesQuickAskResume, the hand-off shim the popover calls (via the
//     Swift bridge) to resume a job in the real chat surface.
//
// Design: a quiet native menu-bar utility.  Flat surfaces, hairline dividers,
// system type, zero emoji, bespoke SVG, 12-hour times, tabular numerals.  The
// status strip IS the control surface: the model dot wakes/pauses, the Claude
// pill flips escalation, the update dot opens the main window.
//
// What changed from the read-only P2.2 popover:
//   · the input is NEVER disabled by model state (the chat worker wakes an
//     asleep model by itself — the old health gate locked the field ~most of
//     the time now that models are on-demand);
//   · Approve / Deny are wired INLINE (POST /api/chat/approve) instead of
//     bouncing every approval to the main window;
//   · `deep` (Claude auto-route) answers are rendered as a Claude card instead
//     of being dropped on the floor;
//   · one-tap clipboard actions (summarize / explain / rewrite) run in place;
//   · the duplicated "The local model is starting…" note is gone — state lives
//     in the strip and is never appended to the thread;
//   · the popover resizes itself to its content.
// Escape-first everywhere (E() before any innerHTML; qaMd escapes, then
// formats), so nothing a model or the clipboard produces can inject markup.
(function () {
  "use strict";
  var W = (typeof window !== "undefined") ? window : {};
  // NB: the entry point lives at the BOTTOM of this IIFE. Function
  // declarations hoist but `var` INITIALISERS do not, and buildPopover()
  // touches module-level `var`s on its first statement.

  // ==========================================================================
  // MAIN-WINDOW SHIM  — resume a handed-off job in index.html's real chat
  // ==========================================================================
  // Reuses index.html's top-level globals. Classic <script>s share one global
  // lexical scope, so `session` (a top-level `let`) and the `function`
  // declarations (setChatMode/loadHistory/addBubble/streamJob) are reachable
  // and (for session) reassignable from here.
  function resumeInMain(job) {
    try {
      session = "menubar";                                   // reassigns index.html's let session
      try { localStorage.setItem("hermes_session", "menubar"); } catch (e) {}
      if (typeof setChatMode === "function") setChatMode("full");
      (async function () {
        try {
          if (typeof loadSessions === "function") await loadSessions();
          if (typeof loadHistory === "function") await loadHistory();
          if (job && typeof addBubble === "function" && typeof streamJob === "function") {
            var thinking = addBubble("", "bot", false);
            await streamJob(job, thinking);
          }
        } catch (e) { /* non-fatal: user can re-ask in the main window */ }
      })();
    } catch (e) { /* ignore */ }
  }

  // ==========================================================================
  // POPOVER  — self-contained; index.html globals are NOT available here
  // ==========================================================================
  var SESSION = "menubar";

  function E(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function RM() {
    try { return !!(W.matchMedia && matchMedia("(prefers-reduced-motion:reduce)").matches); }
    catch (e) { return false; }
  }
  var M = W.Motion || null;
  // Motion One takes ONE keyframe object of arrays + seconds; WAAPI takes the
  // same object + ms. Passing a keyframe ARRAY made Motion One write style[0]
  // ("Indexed property setter is not supported") — same bug aux_clip had.
  function anim(el, kf, opt) {
    try {
      if (!el || RM()) return;
      var o = opt || {}, ms = o.duration || 200;
      if (M && M.animate) return M.animate(el, kf, { duration: ms / 1000, easing: o.easing });
      if (el.animate) return el.animate(kf, { duration: ms, easing: o.easing, fill: "both" });
    } catch (e) {}
  }
  function bridge(action, extra) {
    try {
      var mh = W.webkit && W.webkit.messageHandlers && W.webkit.messageHandlers.hermes;
      if (!mh) return false;
      var m = { action: action };
      if (extra) for (var k in extra) m[k] = extra[k];
      mh.postMessage(m);
      return true;
    } catch (e) { return false; }
  }
  function j(url, body) {                       // fetch → json, never throws
    var opt = body ? {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    } : undefined;
    return fetch(url, opt).then(function (r) {
      return r.json().then(function (d) { return d; }, function () { return null; });
    }, function () { return null; });
  }
  function t12(ts) {                              // 12-hour, never 24 (CLAUDE.md)
    try {
      var d = new Date((ts || Date.now() / 1000) * 1000);
      var h = d.getHours(), m = d.getMinutes();
      var ap = h >= 12 ? "PM" : "AM"; h = h % 12; if (!h) h = 12;
      return h + ":" + (m < 10 ? "0" : "") + m + " " + ap;
    } catch (e) { return ""; }
  }

  // escape-FIRST, then a short format pass (mirrors index.html's renderMd order)
  function qaMd(src) {
    var lines = E(src).split("\n"), html = "", list = null, inCode = false;
    var close = function () { if (list) { html += "</" + list + ">"; list = null; } };
    var inline = function (s) {
      s = s.replace(/`([^`]+)`/g, function (_, c) { return "<code>" + c + "</code>"; });
      s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
      s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
      return s;
    };
    for (var i = 0; i < lines.length; i++) {
      var raw = lines[i];
      if (/^```/.test(raw)) { if (inCode) { html += "</code></pre>"; inCode = false; } else { close(); html += "<pre><code>"; inCode = true; } continue; }
      if (inCode) { html += raw + "\n"; continue; }
      var m;
      if (m = raw.match(/^\s*[-*]\s+(.*)$/)) { if (list !== "ul") { close(); list = "ul"; html += "<ul>"; } html += "<li>" + inline(m[1]) + "</li>"; continue; }
      if (m = raw.match(/^\s*\d+\.\s+(.*)$/)) { if (list !== "ol") { close(); list = "ol"; html += "<ol>"; } html += "<li>" + inline(m[1]) + "</li>"; continue; }
      if (raw.trim() === "") { close(); continue; }
      close(); html += "<p>" + inline(raw) + "</p>";
    }
    if (inCode) html += "</code></pre>";
    close();
    return html;
  }

  // ---- bespoke glyphs (no emoji, ever) --------------------------------------
  var PATHS = {
    send: '<path d="M12 19V5"/><path d="M5.5 11.5 12 5l6.5 6.5"/>',
    clip: '<rect x="8.5" y="3" width="7" height="3.6" rx="1.1"/><path d="M8.5 4.8H6.4A1.9 1.9 0 0 0 4.5 6.7v12.4A1.9 1.9 0 0 0 6.4 21h11.2a1.9 1.9 0 0 0 1.9-1.9V6.7a1.9 1.9 0 0 0-1.9-1.9h-2.1"/>',
    sum: '<path d="M4.5 7h15M4.5 12h15M4.5 17h8.5"/>',
    explain: '<circle cx="12" cy="12" r="8.6"/><path d="M9.7 9.4a2.4 2.4 0 1 1 3.2 2.3c-.7.3-1 .8-1 1.5v.4"/><path d="M12 16.8h.01"/>',
    rewrite: '<path d="M12.5 20.5H21"/><path d="M16.3 3.6a2 2 0 0 1 2.8 2.8L7.6 18H4.8v-2.8z"/>',
    cal: '<rect x="3.5" y="5.2" width="17" height="15.3" rx="2.2"/><path d="M3.5 10h17M8.4 3v4M15.6 3v4"/><path d="M8 14h3"/>',
    main: '<path d="M20.5 9.4V4.5h-4.9"/><path d="M20.5 4.5 13 12"/><path d="M19 14.4v4.4a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7V6.7A1.7 1.7 0 0 1 5.2 5h4.4"/>',
    copy: '<rect x="9.2" y="9.2" width="11.3" height="11.3" rx="2.2"/><path d="M5.4 14.8h-.9a2 2 0 0 1-2-2v-8.3a2 2 0 0 1 2-2h8.3a2 2 0 0 1 2 2v.9"/>',
    check: '<path d="M20 6.5 9.4 17.1 4 11.7"/>',
    warn: '<path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9.3v4.2M12 17.1h.01"/>',
    down: '<path d="M12 4.5v11"/><path d="M7.2 11l4.8 4.8 4.8-4.8"/><path d="M4.8 19.5h14.4"/>',
    trash: '<path d="M4.5 7h15"/><path d="M9.5 7V5.4A1.4 1.4 0 0 1 10.9 4h2.2a1.4 1.4 0 0 1 1.4 1.4V7"/><path d="M6.6 7l.8 12a1.6 1.6 0 0 0 1.6 1.5h6a1.6 1.6 0 0 0 1.6-1.5l.8-12"/>'
  };
  function svg(n, size, sw) {
    return '<svg viewBox="0 0 24 24" width="' + size + '" height="' + size + '" fill="none" stroke="currentColor" ' +
      'stroke-width="' + (sw || 1.7) + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (PATHS[n] || "") + "</svg>";
  }
  // the one filled mark: the app's iris spark, used for Claude / the app mark
  function spark(size) {
    return '<svg viewBox="0 0 24 24" width="' + size + '" height="' + size + '" fill="currentColor" aria-hidden="true">' +
      '<path d="M12 3.8C12.7 10.4 13.6 11.3 20.2 12 13.6 12.7 12.7 13.6 12 20.2 11.3 13.6 10.4 12.7 3.8 12 10.4 11.3 11.3 10.4 12 3.8Z"/></svg>';
  }
  function dots() { return '<span class="qa-dots"><i></i><i></i><i></i></span>'; }

  // ---- theme ----------------------------------------------------------------
  // This is its OWN document (loadHTMLString with the dashboard origin as
  // baseURL), so index.html's token block does not reach it. Same 4-block
  // pattern as index.html — CLAUDE.md's "theme toggle trap": each explicit
  // block re-declares the FULL palette, or a dark-OS media query leaves the
  // color tokens dark while only color-scheme flips. Values are the app's.
  var _LIGHT =
    "--iris:#5B63E6;--iris-2:#7A6BEF;--iris-ink:#fff;--quick:#2E93C4;" +
    "--ok:#2E9E68;--warn:#B9821A;--bad:#D24C3C;" +
    "--ground:#E7EAF3;--ink:#10131D;--muted:#565E72;--faint:#868DA1;" +
    "--hairline:rgba(16,19,29,.10);--edge:rgba(255,255,255,.85);" +
    "--field:rgba(255,255,255,.55);--field-edge:rgba(16,19,29,.12);" +
    "--chip:rgba(255,255,255,.5);--glass-2:rgba(255,255,255,.40);" +
    "--cast:rgba(24,28,48,.16);--user-bubble:rgba(91,99,230,.16);--user-ink:#1c2050;";
  var _DARK =
    "--iris:#98A2FF;--iris-2:#B3A7FF;--iris-ink:#0a0c14;--quick:#7CD6F5;" +
    "--ok:#46D392;--warn:#F3BC55;--bad:#F27063;" +
    "--ground:#080A11;--ink:#EDEFF7;--muted:#9AA2B6;--faint:#6A7186;" +
    "--hairline:rgba(200,210,255,.10);--edge:rgba(205,215,255,.20);" +
    "--field:rgba(150,160,200,.12);--field-edge:rgba(200,210,255,.16);" +
    "--chip:rgba(158,168,210,.14);--glass-2:rgba(158,168,210,.07);" +
    "--cast:rgba(0,0,0,.55);--user-bubble:rgba(120,130,255,.22);--user-ink:#dfe3ff;";
  var _RADII = "--radius:20px;--radius-sm:13px;--radius-xs:9px;";

  function qaApplyTheme() {
    try {
      var t = W.localStorage.getItem("hermes_theme");
      if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
      else document.documentElement.removeAttribute("data-theme");
    } catch (e) {}
  }

  var FONT = "-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',system-ui,sans-serif";
  var MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,monospace";

  var CSS = [
    ":root{color-scheme:light dark;" + _RADII + _LIGHT + "}",
    "@media(prefers-color-scheme:dark){:root{" + _DARK + "}}",
    ':root[data-theme="light"]{color-scheme:light;' + _RADII + _LIGHT + "}",
    ':root[data-theme="dark"]{color-scheme:dark;' + _RADII + _DARK + "}",
    "*{box-sizing:border-box}",
    // .qa-sbtn sets display:inline-flex, which outranks the UA sheet's
    // [hidden]{display:none} — without this the (hidden) update pill still
    // painted its dot in the strip.
    "[hidden]{display:none!important}",
    "html{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}",
    "html,body{margin:0;height:100%}",
    "body{font:12.5px/1.5 " + FONT + ";color:var(--ink);background:transparent;overflow:hidden;",
    "font-variant-numeric:tabular-nums}",
    // The native popover backdrop follows the OS, not the app. When the user
    // has overridden the theme we must paint our own ground or light ink lands
    // on a dark vibrancy view; when they agree, stay transparent and let the
    // real NSPopover material show through (that is what makes it look native).
    '@media(prefers-color-scheme:dark){:root[data-theme="light"] body{background:var(--ground)}}',
    '@media(prefers-color-scheme:light){:root[data-theme="dark"] body{background:var(--ground)}}',
    "#qa{display:flex;flex-direction:column;height:100vh;overflow:hidden;font:inherit;color:var(--ink);padding:0}",
    "button{font:inherit;color:inherit}",

    // ---------- 1. status strip: the control surface ----------
    ".qa-strip{flex:0 0 auto;display:flex;align-items:center;gap:4px;height:31px;padding:0 6px 0 7px;",
    "border-bottom:1px solid var(--hairline)}",
    ".qa-sbtn{display:inline-flex;align-items:center;gap:5px;height:23px;padding:0 7px;border:0;border-radius:7px;",
    "background:transparent;color:var(--muted);font-size:11px;font-weight:500;cursor:pointer;white-space:nowrap;",
    "transition-property:background-color,color,transform;transition-duration:150ms;transition-timing-function:ease-out}",
    ".qa-sbtn:hover{background:var(--chip);color:var(--ink)}",
    ".qa-sbtn:active{transform:scale(.97)}",
    ".qa-sbtn:focus-visible{outline:2px solid color-mix(in srgb,var(--iris) 60%,transparent);outline-offset:1px}",
    ".qa-model{flex:1 1 auto;min-width:0;padding-left:5px}",
    ".qa-model .txt{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".qa-model .txt b{color:var(--ink);font-weight:600}",
    ".qa-dot{flex:0 0 auto;width:7px;height:7px;border-radius:50%;background:var(--faint)}",
    ".qa-dot.ok{background:var(--ok)}.qa-dot.warn{background:var(--warn)}.qa-dot.bad{background:var(--bad)}",
    ".qa-dot.busy{background:var(--iris);animation:qaPulse 1.4s ease-in-out infinite}",
    "@keyframes qaPulse{0%,100%{opacity:1}50%{opacity:.3}}",
    ".qa-upd{color:var(--iris)}.qa-upd .qa-dot{background:var(--iris)}",
    ".qa-cl{border:1px solid var(--hairline);background:transparent;height:22px;padding:0 8px 0 6px;border-radius:999px}",
    '.qa-cl[aria-pressed="true"]{background:var(--chip);border-color:color-mix(in srgb,var(--iris) 34%,transparent);color:var(--ink)}',
    '.qa-cl[aria-pressed="true"] .qa-dot{background:var(--iris)}',
    ".qa-cl i{font-style:normal;font-size:10.5px;color:var(--faint)}",
    '.qa-cl[aria-pressed="true"] i{color:var(--iris)}',
    ".qa-more{padding:0 5px;color:var(--faint)}",
    ".qa-menu{position:absolute;top:31px;right:7px;z-index:50;min-width:168px;padding:4px;border-radius:11px;",
    "background:color-mix(in srgb,var(--ground) 96%,transparent);border:1px solid var(--edge);",
    "box-shadow:0 12px 34px -8px var(--cast),0 1px 2px rgba(0,0,0,.14);display:flex;flex-direction:column;gap:1px}",
    ".qa-menu button{display:flex;align-items:center;gap:7px;width:100%;height:28px;padding:0 8px;border:0;border-radius:7px;",
    "background:transparent;color:var(--ink);font-size:12px;text-align:left;cursor:pointer;",
    "transition-property:background-color;transition-duration:120ms;transition-timing-function:ease-out}",
    ".qa-menu button:hover{background:var(--chip)}",
    ".qa-menu button svg{color:var(--faint)}",

    // ---------- 2. ask field ----------
    ".qa-ask{flex:0 0 auto;padding:9px 11px 0}",
    ".qa-field{display:flex;align-items:flex-end;gap:5px;padding:4px 4px 4px 9px;border-radius:12px;",
    "border:1px solid var(--field-edge);background:var(--field);",
    "transition-property:border-color,box-shadow;transition-duration:150ms;transition-timing-function:ease-out}",
    ".qa-field:focus-within{border-color:color-mix(in srgb,var(--iris) 55%,transparent);",
    "box-shadow:0 0 0 3px color-mix(in srgb,var(--iris) 13%,transparent)}",
    ".qa-field textarea{flex:1;min-width:0;border:0;outline:none;background:transparent;resize:none;color:var(--ink);",
    "font:13px/1.45 " + FONT + ";padding:5px 0;max-height:78px;overflow-y:auto}",
    ".qa-field textarea::placeholder{color:var(--faint)}",
    ".qa-go{flex:0 0 auto;width:26px;height:26px;border:0;border-radius:7px;cursor:pointer;display:flex;",
    "align-items:center;justify-content:center;background:var(--iris);color:var(--iris-ink);",
    "transition-property:transform,background-color,color;transition-duration:150ms;transition-timing-function:ease-out}",
    ".qa-go:active{transform:scale(.94)}",
    // resting state is a quiet ghost button, not a faded primary one
    ".qa-go[disabled]{background:var(--chip);color:var(--faint);cursor:default}",
    ".qa-go[data-busy]{background:var(--chip);color:var(--muted)}",

    // ---------- 3. action chips ----------
    ".qa-acts{flex:0 0 auto;display:flex;gap:6px;padding:8px 11px;overflow-x:auto;overflow-y:hidden;",
    "scrollbar-width:none;-ms-overflow-style:none;border-bottom:1px solid var(--hairline)}",
    ".qa-acts::-webkit-scrollbar{display:none}",
    ".qa-acts.fade-r{-webkit-mask-image:linear-gradient(90deg,#000 0,#000 calc(100% - 34px),transparent);",
    "mask-image:linear-gradient(90deg,#000 0,#000 calc(100% - 34px),transparent)}",
    ".qa-acts.fade-l{-webkit-mask-image:linear-gradient(90deg,transparent,#000 26px);",
    "mask-image:linear-gradient(90deg,transparent,#000 26px)}",
    ".qa-acts.fade-l.fade-r{-webkit-mask-image:linear-gradient(90deg,transparent,#000 26px,#000 calc(100% - 34px),transparent);",
    "mask-image:linear-gradient(90deg,transparent,#000 26px,#000 calc(100% - 34px),transparent)}",
    ".qa-chip{position:relative;flex:0 0 auto;display:inline-flex;align-items:center;gap:5px;height:30px;padding:0 10px;",
    "border:1px solid var(--hairline);border-radius:9px;background:var(--chip);color:var(--muted);cursor:pointer;",
    "font-size:11.5px;font-weight:550;white-space:nowrap;",
    "transition-property:transform,background-color,border-color,color;transition-duration:150ms;transition-timing-function:ease-out}",
    // ≥40px hit target without letting neighbours overlap (gap is 6px)
    '.qa-chip::after{content:"";position:absolute;inset:-6px -2px}',
    ".qa-chip:hover{color:var(--ink);border-color:color-mix(in srgb,var(--iris) 40%,transparent)}",
    ".qa-chip:active{transform:scale(.97)}",
    ".qa-chip:focus-visible{outline:2px solid color-mix(in srgb,var(--iris) 60%,transparent);outline-offset:1px}",
    ".qa-chip.sel{color:var(--ink);border-color:color-mix(in srgb,var(--iris) 60%,transparent);",
    "background:color-mix(in srgb,var(--iris) 12%,transparent)}",
    ".qa-chip svg{opacity:.7}",
    ".qa-chip.sel svg{opacity:1;color:var(--iris)}",
    ".qa-chip[disabled]{opacity:.5;cursor:default}",

    // ---------- 4. thread ----------
    ".qa-th{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;padding:9px 11px 11px;",
    "display:flex;flex-direction:column;align-items:stretch;gap:1px}",
    ".qa-th::-webkit-scrollbar{width:8px}",
    ".qa-th::-webkit-scrollbar-thumb{background:var(--hairline);border-radius:4px;border:2px solid transparent;background-clip:padding-box}",
    ".qa-turn{display:flex;flex-direction:column;align-items:flex-start;max-width:100%;margin-bottom:2px}",
    ".qa-turn.user{align-items:flex-end}",
    ".qa-b{max-width:87%;padding:7px 10px;border-radius:12px;font-size:12.5px;line-height:1.5;",
    "word-break:break-word;overflow-wrap:anywhere;text-wrap:pretty}",
    ".qa-b.user{background:var(--user-bubble);color:var(--user-ink);border-bottom-right-radius:5px}",
    ".qa-b.bot{background:color-mix(in srgb,var(--ink) 6%,transparent);border-bottom-left-radius:5px}",
    ".qa-b .md p{margin:0 0 6px}.qa-b .md p:last-child{margin:0}",
    ".qa-b .md code{font:11.5px/1.4 " + MONO + ";background:color-mix(in srgb,var(--ink) 11%,transparent);padding:1px 4px;border-radius:4px}",
    ".qa-b .md pre{background:color-mix(in srgb,var(--ink) 9%,transparent);padding:8px;border-radius:8px;overflow-x:auto;margin:5px 0}",
    ".qa-b .md pre code{background:none;padding:0}",
    ".qa-b .md ul,.qa-b .md ol{margin:4px 0;padding-left:17px}",
    ".qa-b .md li{margin:1px 0}",
    ".qa-b .md a{color:var(--quick)}",
    ".qa-b .md strong{font-weight:640}",
    // streaming caret: inline at the end of the LAST block, not a stray block
    // of its own on a new line (which is what appending a <span> after the
    // rendered markdown produced)
    '.qa-b.stream .md>*:last-child::after{content:"\\258C";color:var(--faint);opacity:.8;margin-left:1px}',
    '.qa-b.stream .md:empty::after{content:"\\258C";color:var(--faint);opacity:.8}',
    ".qa-ba{display:flex;gap:6px;height:19px;margin:1px 2px 0;opacity:0;",
    "transition-property:opacity;transition-duration:150ms;transition-timing-function:ease-out}",
    ".qa-turn:hover .qa-ba,.qa-ba:focus-within{opacity:1}",
    ".qa-ba button{position:relative;border:0;background:transparent;color:var(--faint);cursor:pointer;",
    "font-size:10.5px;font-weight:500;padding:0;display:inline-flex;align-items:center;gap:3px;",
    "transition-property:color;transition-duration:120ms;transition-timing-function:ease-out}",
    '.qa-ba button::after{content:"";position:absolute;inset:-7px -4px}',
    ".qa-ba button:hover{color:var(--ink)}",
    ".qa-line{display:flex;align-items:center;gap:6px;padding:3px 2px;font-size:11px;color:var(--faint);",
    "align-self:flex-start;max-width:100%;text-wrap:pretty}",
    ".qa-line.err{color:var(--bad)}",
    ".qa-line .lk{border:0;background:transparent;color:var(--iris);cursor:pointer;font-size:11px;padding:0;text-decoration:underline;",
    "text-underline-offset:2px}",
    ".qa-dots{display:inline-flex;gap:2px;align-items:center}",
    ".qa-dots i{width:4px;height:4px;border-radius:50%;background:currentColor;opacity:.3;animation:qaBlink 1.2s infinite}",
    ".qa-dots i:nth-child(2){animation-delay:.16s}.qa-dots i:nth-child(3){animation-delay:.32s}",
    "@keyframes qaBlink{0%,100%{opacity:.22}45%{opacity:.85}}",

    // approval + Claude card — top-level thread blocks, never nested in a bubble
    ".qa-appr{align-self:stretch;margin:5px 0;padding:9px 10px;border-radius:11px;",
    "border:1px solid color-mix(in srgb,var(--warn) 42%,transparent);",
    "background:color-mix(in srgb,var(--warn) 10%,transparent)}",
    ".qa-appr .h{display:flex;align-items:center;gap:6px;font-size:11.5px;font-weight:650;color:var(--warn)}",
    ".qa-appr code{display:block;margin:6px 0 8px;padding:6px 8px;border-radius:7px;max-height:92px;overflow:auto;",
    "font:11px/1.5 " + MONO + ";background:color-mix(in srgb,var(--ink) 9%,transparent);white-space:pre-wrap;word-break:break-word}",
    ".qa-appr .row{display:flex;gap:6px}",
    ".qa-appr .row button{flex:1;height:30px;border-radius:8px;cursor:pointer;font-size:12px;font-weight:600;",
    "border:1px solid var(--hairline);background:var(--glass-2);color:var(--ink);",
    "transition-property:transform,background-color,border-color;transition-duration:150ms;transition-timing-function:ease-out}",
    ".qa-appr .row button:hover:not([disabled]){background:var(--chip)}",
    ".qa-appr .row button:active:not([disabled]){transform:scale(.97)}",
    ".qa-appr .row button.ok{background:var(--iris);border-color:transparent;color:var(--iris-ink)}",
    ".qa-appr .row button[disabled]{opacity:.45;cursor:default}",
    ".qa-appr .sub{margin-top:7px;text-align:center}",
    ".qa-appr .sub button{border:0;background:transparent;color:var(--muted);font-size:10.5px;cursor:pointer;",
    "text-decoration:underline;text-underline-offset:2px;padding:2px 4px}",
    ".qa-deep{align-self:stretch;margin:5px 0;padding:9px 10px;border-radius:11px;",
    "border:1px solid color-mix(in srgb,var(--iris) 30%,transparent);",
    "background:color-mix(in srgb,var(--iris) 7%,transparent)}",
    ".qa-deep .h{display:flex;align-items:center;gap:5px;font-size:11px;font-weight:650;color:var(--iris)}",
    ".qa-deep .h .meta{margin-left:auto;font-weight:500;color:var(--faint)}",
    ".qa-deep .bd{margin-top:6px;font-size:12.5px;line-height:1.5;color:var(--ink);text-wrap:pretty;word-break:break-word}",
    ".qa-deep .bd p{margin:0 0 6px}.qa-deep .bd p:last-child{margin:0}",
    ".qa-deep .bd ul,.qa-deep .bd ol{margin:4px 0;padding-left:17px}",
    ".qa-deep .bd code{font:11.5px/1.4 " + MONO + ";background:color-mix(in srgb,var(--ink) 11%,transparent);padding:1px 4px;border-radius:4px}",
    ".qa-deep .bd a{color:var(--quick)}",
    ".qa-deep.err{border-color:color-mix(in srgb,var(--bad) 40%,transparent);background:color-mix(in srgb,var(--bad) 8%,transparent)}",
    ".qa-deep.err .h{color:var(--bad)}",

    // empty state
    ".qa-empty{margin:auto 0;padding:14px 6px;text-align:center;display:flex;flex-direction:column;gap:4px;align-items:center}",
    ".qa-empty .t{font-size:12px;color:var(--muted);text-wrap:balance}",
    ".qa-empty .k{font-size:10.5px;color:var(--faint)}",
    ".qa-empty kbd{font:10.5px/1 " + FONT + ";background:var(--chip);border:1px solid var(--hairline);border-radius:4px;",
    "padding:2px 4px;color:var(--muted)}",

    "@media (prefers-reduced-motion:reduce){*{animation:none!important}",
    ".qa-chip:active,.qa-go:active,.qa-sbtn:active,.qa-appr .row button:active{transform:none}}"
  ].join("");

  // ---- module state ---------------------------------------------------------
  var POP = {
    root: null, strip: null, thread: null, ask: null, acts: null,
    input: null, go: null, model: null, claude: null, upd: null, menu: null,
    built: false, busy: false, job: null
  };
  var STATE = {
    modelLabel: "Local model", modelState: "loading", waking: false, wakeAt: 0,
    claude: null,             // null = unknown, true/false = escalation switch
    update: null,             // {latest} when an update is available
    lastPrompt: "", filter: "", sel: 0, clipCat: null, lastTouch: Date.now()
  };
  var RZ = { t: null, last: 0 };
  var CLIP = { hooked: false, waiters: [] };

  function showFatal(e) {
    try { console.error("[quickask] fatal:", (e && e.stack) || e); } catch (x) {}
    var d = document.getElementById("qa");
    if (d) d.textContent = "Quick Ask failed to load. Reopen to retry.";
  }

  // ==========================================================================
  // dynamic height  — measure, then ask Swift for it (clamped 320..620)
  // ==========================================================================
  // The thread is `flex:1`, so its scrollHeight can never report LESS than the
  // box it was given — measuring that way pinned the popover at whatever height
  // it already had and it could never shrink. Sum the children instead.
  function threadH() {
    var th = POP.thread; if (!th) return 0;
    var cs = W.getComputedStyle(th);
    var h = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
    var gap = parseFloat(cs.rowGap) || 0, kids = th.children;
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      h += k.offsetHeight;
      if (i) h += gap;
      if (k.classList && k.classList.contains("qa-empty")) continue;   // auto margins would lie
      var ks = W.getComputedStyle(k);
      h += (parseFloat(ks.marginTop) || 0) + (parseFloat(ks.marginBottom) || 0);
    }
    return h;
  }
  function measureH() {
    try {
      var h = 0;
      if (POP.strip) h += POP.strip.offsetHeight;
      if (POP.ask) h += POP.ask.offsetHeight;
      if (POP.acts) h += POP.acts.offsetHeight;
      h += threadH();
      return Math.round(h);
    } catch (e) { return 460; }
  }
  function fit() {
    clearTimeout(RZ.t);
    RZ.t = setTimeout(function () {
      syncFade();
      var h = Math.max(320, Math.min(620, measureH()));
      if (Math.abs(h - RZ.last) < 4) return;
      RZ.last = h;
      try { document.documentElement.setAttribute("data-qa-h", String(h)); } catch (e) {}
      bridge("resize", { h: h });
    }, 80);
  }

  // ==========================================================================
  // thread primitives
  // ==========================================================================
  function atBottom() {
    var t = POP.thread; if (!t) return true;
    return (t.scrollHeight - t.scrollTop - t.clientHeight) < 44;
  }
  function scrollDown(force) {
    var t = POP.thread; if (!t) return;
    if (force || atBottom()) t.scrollTop = t.scrollHeight;
  }
  function clearEmpty() {
    var e = POP.thread && POP.thread.querySelector(".qa-empty");
    if (e) e.remove();
  }
  function place(node, opt) {
    clearEmpty();
    POP.thread.appendChild(node);
    if (!(opt && opt.quiet)) anim(node, { opacity: [0, 1], transform: ["translateY(4px)", "translateY(0px)"] }, { duration: 160, easing: "ease-out" });
    scrollDown(true); fit();
    return node;
  }

  // one turn = bubble + (bot only) its hover actions
  function turn(text, who, asMd, opt) {
    var d = document;
    var wrap = d.createElement("div"); wrap.className = "qa-turn " + who;
    var b = d.createElement("div"); b.className = "qa-b " + who;
    if (asMd) { var m = d.createElement("div"); m.className = "md"; m.innerHTML = qaMd(text); b.appendChild(m); }
    else b.textContent = text;
    wrap.appendChild(b);
    if (who === "bot") wrap.appendChild(botActions(b));
    place(wrap, opt);
    return b;
  }
  function botActions(bubble) {
    var d = document;
    var row = d.createElement("div"); row.className = "qa-ba";
    var copy = d.createElement("button");
    copy.type = "button"; copy.innerHTML = svg("copy", 11) + "<span>Copy</span>";
    copy.title = "Copy this answer";
    copy.onclick = function () {
      clipWrite(bubble.textContent || "").then(function () {
        copy.innerHTML = svg("check", 11) + "<span>Copied</span>";
        setTimeout(function () { copy.innerHTML = svg("copy", 11) + "<span>Copy</span>"; }, 1100);
      });
    };
    var main = d.createElement("button");
    main.type = "button"; main.textContent = "Continue in main";
    main.title = "Open this conversation in the main window";
    main.onclick = function () { bridge("openMain", { session: SESSION }); };
    row.appendChild(copy); row.appendChild(main);
    return row;
  }
  function line(text, kind) {
    var d = document, n = d.createElement("div");
    n.className = "qa-line" + (kind ? " " + kind : "");
    n.textContent = text;
    return place(n);
  }
  function busyLine(text) {
    var d = document, n = d.createElement("div");
    n.className = "qa-line";
    n.innerHTML = dots() + '<span class="t"></span>';
    n.querySelector(".t").textContent = text;
    n.setLabel = function (t) { var s = n.querySelector(".t"); if (s) s.textContent = t; fit(); };
    return place(n);
  }
  function emptyState() {
    var d = document, n = d.createElement("div");
    n.className = "qa-empty";
    n.innerHTML = '<div class="t">Ask anything, or run an action on what you just copied.</div>' +
      '<div class="k"><kbd>&#8963;</kbd><kbd>&#8997;</kbd><kbd>Space</kbd> opens this from anywhere</div>';
    POP.thread.appendChild(n);
    fit();
  }

  // ==========================================================================
  // Claude (deep) card — its own block; never a card inside a bubble
  // ==========================================================================
  function deepCard(d) {
    var el = document.createElement("div");
    el.className = "qa-deep";
    el.innerHTML = '<div class="h">' + spark(11) + '<span class="lab"></span><span class="meta"></span></div>' +
      '<div class="bd"></div>';
    place(el);
    fillDeep(el, d);
    return el;
  }
  function fillDeep(el, d) {
    if (!el || !d) return;
    var lab = el.querySelector(".lab"), meta = el.querySelector(".meta"), bd = el.querySelector(".bd");
    var thinking = d.state === "thinking";
    el.classList.toggle("err", !thinking && d.ok === false);
    if (thinking) {
      lab.textContent = "Claude";
      meta.textContent = "thinking…";
      bd.innerHTML = ""; bd.appendChild(document.createTextNode(""));
      bd.innerHTML = dots();
    } else if (d.ok === false) {
      lab.textContent = d.refused ? "Claude declined" : "Claude unavailable";
      meta.textContent = t12();
      bd.textContent = d.text || d.error || d.reason || "Claude could not answer this.";
    } else {
      lab.textContent = "Claude · " + (d.model || "sonnet");
      var secs = d.ms ? (d.ms / 1000).toFixed(d.ms < 10000 ? 1 : 0) + "s" : "";
      meta.textContent = [secs, t12(d.ts)].filter(Boolean).join(" · ");
      bd.innerHTML = qaMd(d.text || "");
    }
    scrollDown(); fit();
  }

  // ==========================================================================
  // status strip
  // ==========================================================================
  var STATE_TEXT = {
    ready: "ready",
    asleep: "asleep · wakes on demand",
    paused: "paused · tap to resume",
    waking: "waking…",
    loading: "loading…",
    offline: "offline"
  };
  // when the update pill takes room in the strip, the state loses its coda
  var STATE_SHORT = { asleep: "asleep", paused: "paused" };
  var STATE_DOT = { ready: "ok", asleep: "", paused: "warn", waking: "busy", loading: "busy", offline: "bad" };

  function renderStrip() {
    if (!POP.model) return;
    var s = STATE.modelState;
    var dot = POP.model.querySelector(".qa-dot");
    dot.className = "qa-dot " + (STATE_DOT[s] || "");
    var txt = POP.model.querySelector(".txt");
    txt.innerHTML = "";
    var b = document.createElement("b"); b.textContent = STATE.modelLabel;
    txt.appendChild(b);
    var phrase = (STATE.update && STATE_SHORT[s]) || STATE_TEXT[s] || s;
    txt.appendChild(document.createTextNode(" · " + phrase));
    POP.model.title =
      s === "asleep" ? "The model is suspended to free memory — tap to wake it now (it also wakes on your next question)"
        : s === "paused" ? "The model is paused — tap to resume it"
          : s === "ready" ? "Tap to pause the model and free its memory"
            : "The model server is starting";

    if (POP.claude) {
      var on = STATE.claude === true;
      POP.claude.setAttribute("aria-pressed", on ? "true" : "false");
      POP.claude.querySelector("i").textContent = STATE.claude == null ? "…" : (on ? "on" : "off");
      POP.claude.title = on ? "Claude escalation is on — tap to turn it off"
        : "Claude escalation is off — tap to turn it on";
    }
    if (POP.upd) {
      var u = STATE.update;
      POP.upd.hidden = !u;
      if (u) {
        POP.upd.querySelector(".v").textContent = "v" + u;
        POP.upd.title = "Version " + u + " is available — open the main window to update";
      }
    }
    renderChips();
    fit();
  }

  function beginWake() {
    STATE.waking = true; STATE.wakeAt = Date.now();
    STATE.modelState = "waking"; renderStrip();
  }
  function refreshState() {
    j("/api/health").then(function (h) {
      j("/api/models").then(function (m) {
        if (m && m.active) {
          var lab = null;
          (m.models || []).forEach(function (x) { if (x && x.id === m.active) lab = x.label || x.id; });
          STATE.modelLabel = lab || String(m.active).split("/").pop();
        }
        var online = !!(h && h.model_online);
        // a cold start is ~30-50s; give up on the optimistic "waking…" after
        // two minutes so a refused/failed start does not lie forever
        if (STATE.waking && (Date.now() - STATE.wakeAt) > 120000) STATE.waking = false;
        if (online) { STATE.waking = false; STATE.modelState = "ready"; }
        else if (m && m.paused) { STATE.waking = false; STATE.modelState = "paused"; }
        else if (STATE.waking) STATE.modelState = "waking";
        else if (m && m.idle_suspended) STATE.modelState = "asleep";
        else if (m) STATE.modelState = "loading";
        else STATE.modelState = "offline";
        renderStrip();
      });
    });
  }
  function modelTap() {
    var s = STATE.modelState;
    if (s === "asleep") {
      beginWake();
      j("/api/agent/wake", {}).then(function () { setTimeout(refreshState, 900); });
    } else if (s === "paused") {
      beginWake();
      j("/api/agent/resume", {}).then(function () { setTimeout(refreshState, 900); });
    } else if (s === "ready") {
      STATE.modelState = "paused"; renderStrip();
      j("/api/agent/pause", {}).then(function () {
        line("Model paused — its memory is free again. Tap the dot to resume.");
        setTimeout(refreshState, 700);
      });
    }
  }
  function claudeTap() {
    var next = !(STATE.claude === true);
    STATE.claude = next; renderStrip();                    // optimistic
    j("/api/claude/escalate", { enabled: next }).then(function (d) {
      if (!d || d.ok === false) { STATE.claude = !next; line("Could not change the Claude switch.", "err"); }
      else STATE.claude = !!d.enabled;
      renderStrip();
    });
  }
  function refreshUpdate() {
    j("/api/update/check").then(function (d) {
      STATE.update = (d && d.update_available && d.latest) ? String(d.latest).replace(/^v/, "") : null;
      renderStrip();
    });
  }

  // ---- overflow menu --------------------------------------------------------
  function closeMenu() { if (POP.menu) { POP.menu.remove(); POP.menu = null; } }
  function openMenu(anchor) {
    if (POP.menu) { closeMenu(); return; }
    var d = document, m = d.createElement("div");
    m.className = "qa-menu";
    var items = [
      ["main", "Open main window", function () { bridge("openMain", { session: SESSION }); }],
      ["trash", "Clear conversation", clearThread]
    ];
    items.forEach(function (it) {
      var b = d.createElement("button");
      b.type = "button";
      b.innerHTML = svg(it[0], 13) + "<span></span>";
      b.querySelector("span").textContent = it[1];
      b.onclick = function () { closeMenu(); it[2](); };
      m.appendChild(b);
    });
    POP.root.appendChild(m);
    POP.menu = m;
    anim(m, { opacity: [0, 1], transform: ["translateY(-4px)", "translateY(0px)"] }, { duration: 130, easing: "ease-out" });
    setTimeout(function () {
      d.addEventListener("mousedown", function once(ev) {
        if (POP.menu && !POP.menu.contains(ev.target) && ev.target !== anchor) closeMenu();
        if (!POP.menu) d.removeEventListener("mousedown", once);
      });
    }, 0);
  }
  function clearThread() {
    j("/api/sessions/delete", { session: SESSION }).then(function () {
      POP.thread.innerHTML = "";
      emptyState();
      fit();
    });
  }

  // ==========================================================================
  // clipboard  (read via the Swift bridge, then navigator; write the same way)
  // ==========================================================================
  // aux_clip.js loads AFTER this file and installs its own window.__clipDeliver,
  // clobbering anything we define at load time. So hook it lazily, on first use
  // (long after both files ran), and CHAIN to whatever is already there so the
  // Clipboard Actions sheet keeps working.
  function hookClip() {
    if (CLIP.hooked) return;
    CLIP.hooked = true;
    var prev = W.__clipDeliver;
    W.__clipDeliver = function (t) {
      try { if (typeof prev === "function") prev(t); } catch (e) {}
      var ws = CLIP.waiters; CLIP.waiters = [];
      ws.forEach(function (r) { try { r(typeof t === "string" ? t : ""); } catch (e) {} });
    };
  }
  function hasBridge(name) {
    try { return !!(W.webkit && W.webkit.messageHandlers && W.webkit.messageHandlers[name]); }
    catch (e) { return false; }
  }
  function clipRead() {
    if (hasBridge("hermesClip")) {
      hookClip();
      return new Promise(function (resolve) {
        var done = false;
        var fin = function (s) { if (!done) { done = true; resolve(s); } };
        CLIP.waiters.push(fin);
        try { W.webkit.messageHandlers.hermesClip.postMessage({ action: "read" }); }
        catch (e) { fin(null); }
        setTimeout(function () { fin(null); }, 900);
      }).then(function (s) { return (s != null) ? s : clipRead2(); });
    }
    return clipRead2();
  }
  function clipRead2() {
    try {
      if (W.navigator && navigator.clipboard && navigator.clipboard.readText) {
        return navigator.clipboard.readText().then(
          function (t) { return (typeof t === "string") ? t : null; },
          function () { return null; });
      }
    } catch (e) {}
    return Promise.resolve(null);
  }
  function clipWrite(text) {
    if (hasBridge("hermesClipWrite")) {
      try { W.webkit.messageHandlers.hermesClipWrite.postMessage({ action: "write", text: text }); return Promise.resolve(true); }
      catch (e) {}
    }
    try {
      if (W.navigator && navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return false; });
      }
    } catch (e) {}
    return Promise.resolve(false);
  }

  var CLIP_ERR = {
    empty: "Nothing to act on — the clipboard looks empty.",
    not_text: "That doesn't look like text.",
    too_long: "That selection is too long — trim it and try again.",
    bad_action: "Unknown action.",
    disabled: "Clipboard actions are turned off in settings.",
    model_error: "The model is busy or slow — try again."
  };

  function runClip(action, label) {
    if (POP.busy) return;
    POP.busy = true; syncGo();
    var src = null;
    clipRead().then(function (t) {
      var text = (typeof t === "string" ? t : "").trim();
      var fromField = false;
      if (!text) {                                  // fall back to what's typed
        text = (POP.input.value || "").trim();
        fromField = !!text && text.charAt(0) !== "/";
        if (!fromField) text = "";
      }
      if (!text) {
        POP.busy = false; syncGo();
        line("Nothing on the clipboard to " + label.toLowerCase() + ".", "err");
        return;
      }
      src = text;
      var head = document.createElement("div");
      head.className = "qa-turn user";
      var hb = document.createElement("div"); hb.className = "qa-b user";
      hb.textContent = label + " · " + text.length.toLocaleString() + " chars" +
        (fromField ? " from the ask field" : "");
      head.appendChild(hb); place(head);
      var pend = busyLine("Running " + label.toLowerCase() + "…");
      return j("/api/clip/transform", { action: action, text: text, source: "menubar" })
        .then(function (d) {
          pend.remove();
          POP.busy = false; syncGo();
          if (d && d.ok) {
            turn(d.result || "", "bot", true);
            return;
          }
          var err = d && d.error;
          if (err === "model_offline") {
            var n = document.createElement("div");
            n.className = "qa-line err";
            n.innerHTML = '<span>The local model is asleep. </span>';
            var b = document.createElement("button");
            b.className = "lk"; b.type = "button"; b.textContent = "Wake it";
            b.onclick = function () {
              b.disabled = true; b.textContent = "waking…";
              beginWake();
              j("/api/agent/wake", {}).then(function () { setTimeout(refreshState, 900); });
            };
            n.appendChild(b);
            place(n);
          } else {
            line(CLIP_ERR[err] || "Could not reach the local model.", "err");
          }
        });
    }).catch(function () {
      POP.busy = false; syncGo();
      line("Could not read the clipboard.", "err");
    });
  }

  // ==========================================================================
  // Ask Claude (quick) — a direct bridge call, rendered as a Claude card
  // ==========================================================================
  function askClaude() {
    if (POP.busy) return;
    var typed = (POP.input.value || "").trim();
    if (typed.charAt(0) === "/") typed = "";
    var go = function (task, note) {
      if (!task) { line("Type a question (or copy some text) first, then ask Claude.", "err"); return; }
      POP.busy = true; syncGo();
      if (typed) { POP.input.value = ""; grow(); setFilter(""); }
      turn(task.length > 400 ? task.slice(0, 400) + "…" : task, "user", false);
      if (note) line(note);
      var card = deepCard({ state: "thinking" });
      j("/api/claude/think", { task: task, depth: "quick" }).then(function (d) {
        POP.busy = false; syncGo();
        if (!d) { fillDeep(card, { ok: false, error: "Could not reach Claude." }); return; }
        fillDeep(card, {
          ok: d.ok !== false && !!(d.text || d.response), refused: d.refused,
          text: d.text || d.response, model: d.model, ms: d.ms,
          error: d.error, reason: d.reason, ts: Date.now() / 1000
        });
      });
    };
    if (typed) { go(typed, null); return; }
    clipRead().then(function (t) {
      var c = (typeof t === "string" ? t : "").trim();
      go(c, c ? "Asked about " + c.length.toLocaleString() + " chars from the clipboard." : null);
    });
  }

  // ==========================================================================
  // action chips
  // ==========================================================================
  function actionList() {
    var cat = STATE.clipCat, acts = [];
    // Labels come from /api/clip/actions when it answers ("Summarize",
    // "Explain", "Rewrite"); the shared clipboard glyph is what says *what*
    // they act on, which keeps three one-tap actions readable at 380px instead
    // of two-and-a-half. The full sentence lives in the tooltip.
    ["summarize", "explain", "rewrite"].forEach(function (k) {
      if (cat && cat.actions && !cat.actions[k]) return;      // catalog says no
      var a = cat && cat.actions && cat.actions[k];
      var label = (a && a.label) || (k.charAt(0).toUpperCase() + k.slice(1));
      acts.push({
        id: "clip:" + k, icon: "clip", label: label,
        title: label + " what's on the clipboard",
        run: function () { runClip(k, label + " clipboard"); }
      });
    });
    acts.push({
      id: "plan", icon: "cal", label: "Plan my day", title: "Ask for a plan for today",
      run: function () { send("Plan my day: what should I focus on today, and in what order?"); }
    });
    if (STATE.claude === true) {
      acts.push({
        id: "claude", icon: null, label: "Ask Claude",
        title: "Send what you typed (or the clipboard) straight to Claude",
        run: askClaude
      });
    }
    acts.push({
      id: "main", icon: "main", label: "Continue in main",
      title: "Open this conversation in the main window",
      run: function () { bridge("openMain", { session: SESSION }); }
    });
    return acts;
  }
  function visibleActions() {
    var q = STATE.filter.replace(/^\//, "").trim().toLowerCase();
    return actionList().filter(function (a) {
      return !q || a.label.toLowerCase().indexOf(q) >= 0 || a.id.indexOf(q) >= 0;
    });
  }
  function renderChips() {
    if (!POP.acts) return;
    var d = document, list = visibleActions();
    POP.acts.innerHTML = "";
    if (!list.length) {
      var n = d.createElement("div");
      n.className = "qa-chip"; n.setAttribute("disabled", "");
      n.textContent = "No action matches “" + STATE.filter.replace(/^\//, "") + "”";
      POP.acts.appendChild(n);
    }
    if (STATE.sel >= list.length) STATE.sel = 0;
    list.forEach(function (a, i) {
      var b = d.createElement("button");
      b.type = "button";
      b.className = "qa-chip" + (STATE.filter && i === STATE.sel ? " sel" : "");
      b.innerHTML = (a.icon ? svg(a.icon, 13) : spark(13)) + "<span></span>";
      b.querySelector("span").textContent = a.label;
      b.title = a.title || a.label;
      b.onclick = function () {
        if (STATE.filter) { POP.input.value = ""; grow(); setFilter(""); }
        a.run();
      };
      POP.acts.appendChild(b);
    });
    syncFade();
  }
  // fade an edge only when there is really more to scroll to
  function syncFade() {
    var a = POP.acts; if (!a) return;
    try {
      var more = a.scrollWidth - a.clientWidth;
      a.classList.toggle("fade-r", more > 2 && a.scrollLeft < more - 2);
      a.classList.toggle("fade-l", a.scrollLeft > 2);
    } catch (e) {}
  }
  function setFilter(v) {
    STATE.filter = v; STATE.sel = 0; renderChips();
    if (v) POP.acts.scrollLeft = 0;
    fit();
  }

  // ==========================================================================
  // chat
  // ==========================================================================
  function syncGo() {
    if (!POP.go) return;
    var has = !!(POP.input.value || "").trim();
    POP.go.disabled = POP.busy || !has;
    if (POP.busy) { POP.go.setAttribute("data-busy", "1"); POP.go.innerHTML = dots(); }
    else { POP.go.removeAttribute("data-busy"); POP.go.innerHTML = svg("send", 15, 2); }
  }
  function grow() {
    var t = POP.input; if (!t) return;
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 78) + "px";
    fit();
  }

  // one entry point for Return and the go button: "/query" runs the highlighted
  // action, anything else is a message
  function submit() {
    if (STATE.filter) {
      var list = visibleActions();
      if (!list.length) return;
      var a = list[Math.min(STATE.sel, list.length - 1)];
      POP.input.value = ""; grow(); setFilter(""); syncGo();
      a.run();
      return;
    }
    send();
  }

  function send(text) {
    if (POP.busy) return;
    text = (text == null ? (POP.input.value || "") : text).trim();
    if (!text) return;
    STATE.lastPrompt = text;
    try { W.localStorage.setItem("hermes_qa_last", text); } catch (e) {}
    POP.input.value = ""; grow(); setFilter("");
    POP.busy = true; syncGo();
    turn(text, "user", false);
    var asleep = STATE.modelState === "asleep" || STATE.modelState === "waking" ||
      STATE.modelState === "loading" || STATE.modelState === "offline";
    // The chat worker wakes an asleep model itself — never block the send.
    var pend = busyLine(asleep ? "waking the model (~40 s)…" : "thinking…");
    if (asleep) { beginWake(); }
    j("/api/chat", { message: text, session: SESSION }).then(function (d) {
      if (d && d.job) { POP.job = d.job; poll(d.job, pend); return; }
      pend.remove();
      POP.busy = false; syncGo();
      if (d && d.reply) line(d.reply, d.ok ? "" : "err");
      else line("Could not reach the dashboard.", "err");
      refreshState();
      focusInput();
    });
  }

  function poll(job, pend) {
    var bubble = null, appr = null, deep = null, deepSeen = false;
    var ensure = function () {
      if (!bubble) {
        bubble = turn("", "bot", true);
        POP.thread.appendChild(pend);       // keep the status line last
      }
      return bubble;
    };
    var finish = function () {
      if (pend && pend.parentNode) pend.remove();
      POP.busy = false; POP.job = null; syncGo(); fit(); focusInput();
    };
    var loop = function () {
      j("/api/chat/poll?job=" + encodeURIComponent(job)).then(function (d) {
        if (!d) { setTimeout(loop, 1200); return; }
        if (d.gone) {
          finish();
          line("That request was lost (did the dashboard restart?) — ask again.", "err");
          return;
        }
        if (d.text) {
          var b0 = ensure();
          b0.querySelector(".md").innerHTML = qaMd(d.text);
          b0.classList.toggle("stream", !d.done);
          scrollDown(); fit();
        }
        if (!d.done && pend && pend.setLabel) {
          var st = d.status ? String(d.status) : "";
          if (st && !/[.…!?]$/.test(st)) st += "…";           // "running web_search" reads as live
          pend.setLabel(st || (d.state === "approval" ? "waiting for your approval…"
            : (d.text ? "writing…" : "thinking…")));
        }
        if (d.deep && !deep) deep = deepCard(d.deep);
        else if (d.deep && deep) fillDeep(deep, d.deep);
        if (d.deep && d.deep.state !== "thinking") deepSeen = true;

        if (d.state === "approval" && d.approval && !appr) {
          appr = approvalBox(job, d.approval, function () { appr = null; });
          POP.thread.appendChild(pend);
          scrollDown(true); fit();
        }
        if (d.state !== "approval" && appr) { appr.remove(); appr = null; }

        if (d.done) {
          var b = ensure();
          b.classList.remove("stream");
          b.querySelector(".md").innerHTML = qaMd(d.reply || d.text || "");
          if (d.err) b.classList.add("err");
          if (appr) { appr.remove(); appr = null; }
          finish();
          refreshState();
          // the Claude answer can land AFTER the local turn (aux_autoroute runs
          // it in parallel) — keep watching so it is never dropped
          if (d.deep && d.deep.state === "thinking") pollDeep(job, deep);
          else if (!deepSeen && d.deep) fillDeep(deep, d.deep);
          return;
        }
        setTimeout(loop, 700);
      });
    };
    loop();
  }
  function pollDeep(job, card) {
    var n = 0;
    var tick = function () {
      if (n++ > 180) return;                       // ≤ 3 min in the popover
      j("/api/claude/autoroute/job?job=" + encodeURIComponent(job)).then(function (d) {
        if (!d || d.gone || !d.deep) return;
        fillDeep(card || (card = deepCard(d.deep)), d.deep);
        if (d.deep.state === "thinking") setTimeout(tick, 1000);
      });
    };
    setTimeout(tick, 1000);
  }

  function approvalBox(job, a, onGone) {
    var d = document, box = d.createElement("div");
    box.className = "qa-appr";
    box.innerHTML = '<div class="h">' + svg("warn", 13) + "<span>Needs your approval</span></div>" +
      "<code></code><div class=\"row\"></div>" +
      '<div class="sub"><button type="button">Review it in the main window</button></div>';
    box.querySelector("code").textContent =
      a.command || a.summary || a.tool || a.name || "a sensitive action";
    var row = box.querySelector(".row");
    [["approve", "Approve", "ok"], ["deny", "Deny", ""]].forEach(function (c) {
      var b = d.createElement("button");
      b.type = "button"; b.textContent = c[1]; if (c[2]) b.className = c[2];
      b.onclick = function () {
        row.querySelectorAll("button").forEach(function (x) { x.disabled = true; });
        j("/api/chat/approve", { job: job, choice: c[0] }).then(function (r) {
          if (!r || r.ok === false) {
            row.querySelectorAll("button").forEach(function (x) { x.disabled = false; });
            line("That approval could not be recorded — try the main window.", "err");
            return;
          }
          box.remove(); onGone && onGone();
          line(c[0] === "approve" ? "Approved." : "Denied.");
        });
      };
      row.appendChild(b);
    });
    box.querySelector(".sub button").onclick = function () {
      bridge("openApproval", { job: job });
    };
    place(box);
    return box;
  }

  // ==========================================================================
  // history
  // ==========================================================================
  function loadHistory() {
    return j("/api/history?session=" + encodeURIComponent(SESSION)).then(function (d) {
      var msgs = (d && d.messages) || [];
      POP.thread.innerHTML = "";
      if (!msgs.length) { emptyState(); return; }
      msgs.slice(-12).forEach(function (m) {                  // ~6 turns
        if (m.role === "user") { turn(m.text || "", "user", false, { quiet: true }); return; }
        if (m.deep) {                                        // an auto-routed Claude answer
          deepCard({ ok: true, text: m.text || "", model: m.deep.model, ms: m.deep.ms, ts: m.ts });
          return;
        }
        var b = turn(m.text || "", "bot", !m.err, { quiet: true });
        if (m.err) b.classList.add("err");
      });
      scrollDown(true); fit();
    });
  }

  // ==========================================================================
  // build
  // ==========================================================================
  function focusInput() { try { POP.input && POP.input.focus(); } catch (e) {} }

  function buildPopover() {
    var d = document;
    qaApplyTheme();                                  // before first paint
    var st = d.createElement("style"); st.textContent = CSS;
    (d.head || d.documentElement).appendChild(st);
    try {
      W.addEventListener("storage", function (ev) {
        if (!ev || ev.key === "hermes_theme") qaApplyTheme();
      });
    } catch (e) {}
    try {
      W.addEventListener("hermes:claude-escalation", function (ev) {
        var on = ev && ev.detail && typeof ev.detail.enabled === "boolean" ? ev.detail.enabled : null;
        if (on !== null) { STATE.claude = on; renderStrip(); }
      });
    } catch (e) {}

    var root = d.getElementById("qa") || d.body;
    root.textContent = "";
    root.style.cssText = "";
    root.style.position = "relative";
    root.innerHTML =
      '<div class="qa-strip">' +
        '<button class="qa-sbtn qa-model" type="button"><span class="qa-dot"></span><span class="txt"></span></button>' +
        '<button class="qa-sbtn qa-upd" type="button" hidden><span class="qa-dot"></span><span class="v"></span></button>' +
        '<button class="qa-sbtn qa-cl" type="button" aria-pressed="false"><span class="qa-dot"></span>Claude <i>…</i></button>' +
        '<button class="qa-sbtn qa-more" type="button" aria-label="More" title="More">' +
          '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">' +
          '<circle cx="5.5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18.5" cy="12" r="1.5"/></svg>' +
        "</button>" +
      "</div>" +
      '<div class="qa-ask"><div class="qa-field">' +
        '<textarea id="qa-in" rows="1" spellcheck="false" placeholder="Ask Hermes — / for actions"></textarea>' +
        '<button class="qa-go" id="qa-go" type="button" aria-label="Send" title="Send (Return)"></button>' +
      "</div></div>" +
      '<div class="qa-acts" id="qa-acts"></div>' +
      '<div class="qa-th" id="qa-th"></div>';

    POP.root = root;
    POP.strip = root.querySelector(".qa-strip");
    POP.ask = root.querySelector(".qa-ask");
    POP.acts = root.querySelector(".qa-acts");
    POP.thread = root.querySelector(".qa-th");
    POP.input = root.querySelector("#qa-in");
    POP.go = root.querySelector("#qa-go");
    POP.model = root.querySelector(".qa-model");
    POP.claude = root.querySelector(".qa-cl");
    POP.upd = root.querySelector(".qa-upd");
    POP.built = true;

    POP.model.onclick = modelTap;
    POP.claude.onclick = claudeTap;
    POP.upd.onclick = function () { bridge("openMain", { session: SESSION }); };
    root.querySelector(".qa-more").onclick = function (e) {
      e.stopPropagation(); openMenu(e.currentTarget);
    };
    // While the field holds a "/…" action query the go button must run the
    // highlighted action, not post "/re" to the model as a message.
    POP.go.onclick = function () { submit(); };

    POP.input.addEventListener("input", function () {
      STATE.lastTouch = Date.now();
      grow(); syncGo();
      var v = POP.input.value;
      setFilter(v.charAt(0) === "/" ? v : "");
    });
    POP.input.addEventListener("keydown", function (e) {
      STATE.lastTouch = Date.now();
      if (e.key === "Escape") { closeMenu(); bridge("close"); return; }
      var filtering = !!STATE.filter;
      if (filtering && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        var n = visibleActions().length; if (!n) return;
        STATE.sel = (STATE.sel + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
        renderChips();
        return;
      }
      if (e.key === "ArrowUp" && !filtering && !(POP.input.value || "").trim()) {
        var last = STATE.lastPrompt;
        if (!last) { try { last = W.localStorage.getItem("hermes_qa_last") || ""; } catch (x) {} }
        if (last) { e.preventDefault(); POP.input.value = last; grow(); syncGo(); }
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
    });
    POP.thread.addEventListener("scroll", function () { STATE.lastTouch = Date.now(); });
    POP.acts.addEventListener("scroll", function () { STATE.lastTouch = Date.now(); syncFade(); });

    // Swift calls this ~150ms after showing the popover — treat it as "opened".
    W.__qaFocus = function () {
      STATE.lastTouch = Date.now();
      focusInput();
      scrollDown(true);
      refreshState(); refreshUpdate(); refreshClaude();
      fit();
    };

    syncGo();
    renderStrip();
    emptyState();
    anim(root, { opacity: [0, 1] }, { duration: 160, easing: "ease-out" });

    refreshState();
    refreshClaude();
    refreshUpdate();
    loadCatalog();
    loadHistory();
    startPolling();
    try { W.addEventListener("resize", fit); } catch (e) {}
    fit();
  }

  function refreshClaude() {
    j("/api/claude/escalate").then(function (d) {
      STATE.claude = d ? !!d.enabled : null;
      renderStrip();
    });
  }
  function loadCatalog() {
    j("/api/clip/actions").then(function (d) {
      if (d && d.ok && d.actions) STATE.clipCat = d;
      renderChips();
    });
  }
  // Adaptive: 6s while the popover is being used, 30s once it has been idle.
  function startPolling() {
    var tick = function () {
      var idle = (Date.now() - STATE.lastTouch) > 90000;
      var hidden = false;
      try { hidden = document.visibilityState === "hidden"; } catch (e) {}
      if (!hidden) refreshState();
      setTimeout(tick, idle ? 30000 : 6000);
    };
    setTimeout(tick, 6000);
  }

  // ---- entry point (see the note at the top of this IIFE) -------------------
  if (W.__HERMES_QUICKASK__) {
    W.__qaFocus = function () {};                    // safe even if the build throws
    try { buildPopover(); } catch (e) { showFatal(e); }
  } else {
    W.hermesQuickAskResume = resumeInMain;
  }
})();
