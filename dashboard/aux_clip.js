// aux_clip.js — Clipboard Actions (P2.3).
//
// Auto-served at /aux_clip.js.  Loaded after the other aux scripts.  Injects a
// Liquid-Glass command sheet that runs local, capability-free text transforms
// (summarize / explain / translate / rewrite / extract / proofread) on whatever
// is on the clipboard, shows the result inline, and copies it back on request.
//
// Reads the clipboard in three tiers, NEVER server-side:
//   1. Swift app bridge  (window.webkit.messageHandlers.hermesClip -> NSPasteboard)
//   2. navigator.clipboard.readText()  (127.0.0.1 is a secure context)
//   3. manual paste box
// Copy-back is explicit only, via the Swift bridge or navigator.clipboard.
//
// Everything is typeof-guarded (esc, animate, matchMedia, document) so the
// headless render harness can eval it with stubs.  Zero emoji, bespoke SVG only,
// 12-hour time.  Wrapped in an IIFE with try/catch so a throw can't break the
// rest of index.html.
(function () {
  "use strict";
  var W = (typeof window !== "undefined") ? window : {};

  // ---- tiny guarded helpers -------------------------------------------------
  function DOC() { return (typeof document !== "undefined") ? document : null; }
  function E(s) {
    if (typeof esc === "function") return esc(s);
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function RM() {
    try { return !!(W.matchMedia && matchMedia("(prefers-reduced-motion:reduce)").matches); }
    catch (e) { return false; }
  }
  // Motion One's global animate() wants ONE keyframe object of arrays and a
  // duration in SECONDS; the WAAPI fallback wants the same object but ms.
  // The call sites used to pass a two-element keyframe ARRAY, which Motion One
  // tried to write as style[0] — a real console error on every sheet open
  // ("Failed to set an indexed property [0] on 'CSSStyleDeclaration'").
  function anim(node, frames, opts) {
    if (!node) return;
    if (RM()) return;
    var o = opts || {}, ms = o.duration || 240;
    try {
      if (typeof animate === "function") { animate(node, frames, { duration: ms / 1000, easing: o.easing }); return; }
      if (node.animate) node.animate(frames, { duration: ms, easing: o.easing, fill: "both" });
    } catch (e) {}
  }
  function LS() { try { return W.localStorage || null; } catch (e) { return null; } }

  // ---- fallback catalog (works even if /api/clip/actions never answers) -----
  var FALLBACK = {
    order: ["summarize", "explain", "rewrite", "proofread", "translate", "extract"],
    actions: {
      summarize: { label: "Summarize", opts: [] },
      explain: { label: "Explain", opts: [] },
      rewrite: {
        label: "Rewrite", opts: [
          { id: "tone", label: "Tone", type: "choice",
            choices: ["clearer", "more concise", "more formal", "friendlier", "more assertive", "simpler"], default: "clearer" },
          { id: "format", label: "As", type: "choice",
            choices: ["prose", "bullet points", "an email", "a message"], default: "prose" }]
      },
      proofread: { label: "Proofread", opts: [] },
      translate: { label: "Translate", opts: [{ id: "to", label: "Into", type: "lang", default: "English" }] },
      extract: {
        label: "Extract", opts: [{ id: "what", label: "Pull out", type: "choice",
          choices: ["action items", "key points", "dates & times", "names & entities", "emails & links", "numbers & figures"], default: "action items" }]
      }
    },
    defaults: { default_translate_to: "English", last_action: "summarize" }
  };

  // bespoke two-tone SVGs (no emoji)
  var IC_CLIP = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="3" width="8" height="4" rx="1"/><path d="M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><path d="M9 12h6M9 16h4"/></svg>';
  var IC_X = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  var IC_COPY = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  var IC_CHECK = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

  // ---- theme --------------------------------------------------------------
  // The sheet used to hard-code a light palette + ONE @media(prefers-color-
  // scheme:dark) override, so the app's own theme toggle (which sets
  // data-theme on <html> and stores localStorage.hermes_theme) had no effect:
  // dark-OS + light-app painted a dark sheet over a light dashboard. Everything
  // below is expressed in the app's tokens instead, and FALLBACK_TOKENS is
  // appended only when this file runs in a document that has none of its own
  // (the menu-bar popover shell), following index.html's 4-block pattern so an
  // explicit theme wins over the OS in BOTH directions (CLAUDE.md gotcha).
  var _LIGHT = "--ink:#10131D;--muted:#565E72;--faint:#868DA1;--ground:#E7EAF3;" +
    "--iris:#5B63E6;--iris-2:#7A6BEF;--iris-ink:#fff;--ok:#2E9E68;--warn:#B9821A;--bad:#D24C3C;" +
    "--glass-2:rgba(255,255,255,.40);--edge:rgba(255,255,255,.85);--hairline:rgba(16,19,29,.10);" +
    "--cast:rgba(24,28,48,.16);--field:rgba(255,255,255,.55);--field-edge:rgba(16,19,29,.12);" +
    "--chip:rgba(255,255,255,.5);";
  var _DARK = "--ink:#EDEFF7;--muted:#9AA2B6;--faint:#6A7186;--ground:#080A11;" +
    "--iris:#98A2FF;--iris-2:#B3A7FF;--iris-ink:#0a0c14;--ok:#46D392;--warn:#F3BC55;--bad:#F27063;" +
    "--glass-2:rgba(158,168,210,.07);--edge:rgba(205,215,255,.20);--hairline:rgba(200,210,255,.10);" +
    "--cast:rgba(0,0,0,.55);--field:rgba(150,160,200,.12);--field-edge:rgba(200,210,255,.16);" +
    "--chip:rgba(158,168,210,.14);";
  var FALLBACK_TOKENS =
    ":root{color-scheme:light dark;" + _LIGHT + "}" +
    "@media(prefers-color-scheme:dark){:root{" + _DARK + "}}" +
    ':root[data-theme="light"]{color-scheme:light;' + _LIGHT + "}" +
    ':root[data-theme="dark"]{color-scheme:dark;' + _DARK + "}";

  var CSS = [
    "#clip-scrim{position:fixed;inset:0;z-index:9000;background:color-mix(in srgb,var(--ground) 55%,transparent);",
    "backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;padding:24px}",
    "#clip-sheet{width:min(560px,94vw);max-height:min(78vh,720px);display:flex;flex-direction:column;",
    "border-radius:var(--radius,18px);overflow:hidden;color:var(--ink);",
    "background:color-mix(in srgb,var(--ground) 92%,transparent);backdrop-filter:blur(26px) saturate(1.5);-webkit-backdrop-filter:blur(26px) saturate(1.5);",
    "border:1px solid var(--edge);box-shadow:0 24px 70px -12px var(--cast);transform:translateZ(0)}",
    "#clip-sheet .ch{display:flex;align-items:center;gap:9px;padding:14px 16px;border-bottom:1px solid var(--hairline)}",
    "#clip-sheet .ch .ttl{font-weight:650;font-size:14px;letter-spacing:.1px}",
    "#clip-sheet .ch .src{margin-left:auto;font-size:11.5px;color:var(--muted)}",
    "#clip-sheet .cx{border:0;background:transparent;color:var(--muted);cursor:pointer;padding:4px;border-radius:8px;display:flex;",
    "transition-property:background-color,color;transition-duration:150ms;transition-timing-function:ease-out}",
    "#clip-sheet .cx:hover{color:var(--ink);background:var(--chip)}",
    "#clip-sheet .body{padding:12px 16px;overflow:auto}",
    "#clip-sheet .paste{width:100%;box-sizing:border-box;min-height:70px;resize:vertical;border-radius:var(--radius-xs,10px);padding:9px 11px;",
    "font:13px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;border:1px solid var(--field-edge);background:var(--field);color:var(--ink)}",
    "#clip-sheet .chips{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0 4px}",
    "#clip-sheet .chip{border:1px solid var(--hairline);background:var(--chip);color:var(--muted);cursor:pointer;",
    "font-size:12.5px;font-weight:550;padding:6px 12px;border-radius:999px;",
    "transition-property:transform,background-color,border-color,color;transition-duration:120ms;transition-timing-function:ease-out}",
    "#clip-sheet .chip:hover{color:var(--ink);border-color:color-mix(in srgb,var(--iris) 45%,transparent)}",
    "#clip-sheet .chip.on{background:linear-gradient(135deg,var(--iris),var(--iris-2));color:var(--iris-ink);border-color:transparent}",
    "#clip-sheet .opts{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 2px}",
    "#clip-sheet .opts label{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted)}",
    "#clip-sheet select,#clip-sheet .langin{border:1px solid var(--field-edge);background:var(--field);color:var(--ink);",
    "border-radius:8px;padding:4px 8px;font:12px -apple-system,sans-serif}",
    "#clip-sheet .res{margin-top:12px;min-height:44px;border-radius:12px;padding:12px 13px;font:13px/1.6 -apple-system,BlinkMacSystemFont,sans-serif;",
    "white-space:pre-wrap;word-break:break-word;background:var(--glass-2);border:1px solid var(--hairline);color:var(--ink)}",
    "#clip-sheet .res.hint{color:var(--muted);font-style:italic}",
    "#clip-sheet .res.err{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,transparent);background:color-mix(in srgb,var(--bad) 9%,transparent)}",
    "#clip-sheet .shim{height:12px;border-radius:6px;margin:7px 0;background:linear-gradient(90deg,var(--glass-2),var(--hairline),var(--glass-2));",
    "background-size:200% 100%;animation:clipShim 1.1s linear infinite}",
    "@keyframes clipShim{0%{background-position:200% 0}100%{background-position:-200% 0}}",
    "#clip-sheet .row{display:flex;gap:8px;align-items:center;padding:12px 16px;border-top:1px solid var(--hairline)}",
    "#clip-sheet .row button{border:1px solid var(--hairline);background:var(--glass-2);color:var(--ink);cursor:pointer;",
    "font-size:12.5px;font-weight:600;padding:7px 13px;border-radius:9px;display:inline-flex;align-items:center;gap:6px;",
    "transition-property:background-color,border-color,color;transition-duration:150ms;transition-timing-function:ease-out}",
    "#clip-sheet .row button:hover:not(:disabled){background:var(--chip);border-color:color-mix(in srgb,var(--iris) 45%,transparent)}",
    "#clip-sheet .row button:disabled{opacity:.45;cursor:default}",
    "#clip-sheet .row .go{background:linear-gradient(135deg,var(--iris),var(--iris-2));color:var(--iris-ink);border-color:transparent;margin-left:auto}",
    "@media (prefers-reduced-motion:reduce){#clip-sheet .shim{animation:none}}",
    "#clip-launch{display:inline-flex;align-items:center;gap:6px}"
  ].join("");

  // ---- module state ---------------------------------------------------------
  var CAT = null;                 // catalog {order, actions, defaults}
  var STATE = { text: "", action: "summarize", opts: {}, busy: false, result: "" };
  var SHEET = null, els = {};
  var clipWaiters = [];           // pending Swift-bridge read resolvers

  // Swift read bridge delivers here: window.__clipDeliver("<pasteboard text>")
  W.__clipDeliver = function (t) {
    var s = (typeof t === "string") ? t : "";
    var ws = clipWaiters; clipWaiters = [];
    ws.forEach(function (r) { try { r(s); } catch (e) {} });
  };

  function hasSwiftRead() {
    try { return !!(W.webkit && W.webkit.messageHandlers && W.webkit.messageHandlers.hermesClip); }
    catch (e) { return false; }
  }
  function hasSwiftWrite() {
    try { return !!(W.webkit && W.webkit.messageHandlers && W.webkit.messageHandlers.hermesClipWrite); }
    catch (e) { return false; }
  }

  // ---- clipboard read: 3 tiers, never server-side ---------------------------
  function clipRead() {
    // tier 1: Swift app bridge (no TCC prompt)
    if (hasSwiftRead()) {
      return new Promise(function (resolve) {
        var done = false;
        clipWaiters.push(function (s) { if (!done) { done = true; resolve(s); } });
        try { W.webkit.messageHandlers.hermesClip.postMessage({ action: "read" }); }
        catch (e) { if (!done) { done = true; resolve(null); } }
        setTimeout(function () { if (!done) { done = true; resolve(null); } }, 900);
      }).then(function (s) { return (s != null) ? s : clipReadTier2(); });
    }
    return clipReadTier2();
  }
  function clipReadTier2() {
    // tier 2: async clipboard API on the secure loopback context
    try {
      if (W.navigator && navigator.clipboard && navigator.clipboard.readText) {
        return navigator.clipboard.readText().then(
          function (t) { return (typeof t === "string") ? t : null; },
          function () { return null; });
      }
    } catch (e) {}
    return Promise.resolve(null);   // tier 3 (manual paste) handled by the UI
  }

  // ---- clipboard write: 2 tiers, explicit only ------------------------------
  function clipWrite(text) {
    if (hasSwiftWrite()) {
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

  // ---- catalog --------------------------------------------------------------
  function ensureCatalog() {
    if (CAT) return Promise.resolve(CAT);
    return fetch("/api/clip/actions").then(function (r) { return r.json(); }).then(function (d) {
      CAT = (d && d.ok && d.order) ? d : FALLBACK;
      return CAT;
    }).catch(function () { CAT = FALLBACK; return CAT; });
  }

  // ---- error copy -----------------------------------------------------------
  function errText(err) {
    var map = {
      empty: "Nothing to act on — the clipboard looks empty.",
      not_text: "That doesn't look like text.",
      too_long: "That selection is too long — trim it and try again.",
      bad_action: "Unknown action.",
      model_offline: "The local model is offline — start it from the model menu.",
      disabled: "Clipboard actions are turned off in settings.",
      model_error: "The model is busy or slow — try again."
    };
    return map[err] || ("Something went wrong (" + E(err) + ").");
  }

  // ---- build the sheet ------------------------------------------------------
  function injectCSS() {
    var d = DOC(); if (!d) return;
    if (d.getElementById("clip-css")) return;
    // In index.html the tokens already exist; in any other host document (the
    // menu-bar popover shell) they do not, so seed them AND mirror the app's
    // stored theme choice so the sheet matches the dashboard, not the OS.
    var hasTokens = false;
    try {
      hasTokens = !!(d.defaultView && d.defaultView.getComputedStyle(d.documentElement)
        .getPropertyValue("--ink").trim());
    } catch (e) {}
    if (!hasTokens) {
      var tk = d.createElement("style"); tk.id = "clip-tokens"; tk.textContent = FALLBACK_TOKENS;
      (d.head || d.documentElement).appendChild(tk);
      try {
        var t = d.defaultView.localStorage.getItem("hermes_theme");
        if (t === "light" || t === "dark") d.documentElement.setAttribute("data-theme", t);
      } catch (e) {}
    }
    var st = d.createElement("style"); st.id = "clip-css"; st.textContent = CSS;
    (d.head || d.documentElement).appendChild(st);
  }

  function currentAction() { return (CAT.actions && CAT.actions[STATE.action]) || { label: STATE.action, opts: [] }; }

  function renderChips() {
    var d = DOC(); if (!d || !els.chips) return;
    els.chips.innerHTML = "";
    (CAT.order || []).forEach(function (k) {
      var a = CAT.actions[k]; if (!a) return;
      var b = d.createElement("button");
      b.className = "chip" + (k === STATE.action ? " on" : "");
      b.textContent = a.label || k;
      b.onclick = function () { STATE.action = k; saveLast(); renderChips(); renderOpts(); };
      els.chips.appendChild(b);
    });
  }

  function renderOpts() {
    var d = DOC(); if (!d || !els.opts) return;
    els.opts.innerHTML = "";
    var a = currentAction();
    (a.opts || []).forEach(function (o) {
      var wrap = d.createElement("label");
      wrap.appendChild(d.createTextNode((o.label || o.id) + ":"));
      var cur = STATE.opts[o.id];
      if (o.type === "choice") {
        var sel = d.createElement("select");
        (o.choices || []).forEach(function (c) {
          var op = d.createElement("option"); op.value = c; op.textContent = c;
          if (c === (cur || o.default)) op.selected = true;
          sel.appendChild(op);
        });
        sel.onchange = function () { STATE.opts[o.id] = sel.value; saveLast(); };
        if (!STATE.opts[o.id]) STATE.opts[o.id] = o.default;
        wrap.appendChild(sel);
      } else {  // lang / free text
        var inp = d.createElement("input");
        inp.className = "langin"; inp.type = "text"; inp.maxLength = 40;
        inp.value = cur || o.default || "";
        inp.oninput = function () { STATE.opts[o.id] = inp.value.slice(0, 40); saveLast(); };
        if (!STATE.opts[o.id]) STATE.opts[o.id] = inp.value;
        wrap.appendChild(inp);
      }
      els.opts.appendChild(wrap);
    });
  }

  function setSource() {
    if (!els.src) return;
    var n = (STATE.text || "").length;
    els.src.textContent = n ? (n.toLocaleString() + " chars from clipboard") : "";
    // show a manual-paste box only when no text was read
    if (els.paste) {
      els.paste.style.display = n ? "none" : "block";
    }
  }

  function buildSheet() {
    var d = DOC(); if (!d) return null;
    injectCSS();
    var scrim = d.createElement("div"); scrim.id = "clip-scrim";
    scrim.onclick = function (e) { if (e.target === scrim) closeSheet(); };
    var sheet = d.createElement("div"); sheet.id = "clip-sheet";
    sheet.innerHTML =
      '<div class="ch">' + IC_CLIP + '<span class="ttl">Clipboard Actions</span>' +
      '<span class="src"></span>' +
      '<button class="cx" aria-label="Close">' + IC_X + '</button></div>' +
      '<div class="body">' +
      '<textarea class="paste" placeholder="Paste text here (⌘V), then pick an action"></textarea>' +
      '<div class="chips"></div><div class="opts"></div>' +
      '<div class="res hint">Pick an action to transform the clipboard text.</div>' +
      '</div>' +
      '<div class="row">' +
      '<button class="copy" disabled>' + IC_COPY + 'Copy</button>' +
      '<button class="rerun" disabled>Re-run</button>' +
      '<button class="chat">Open in chat</button>' +
      '<button class="go">Run</button>' +
      '</div>';
    scrim.appendChild(sheet);
    (d.body || d.documentElement).appendChild(scrim);

    els = {
      scrim: scrim, sheet: sheet,
      src: sheet.querySelector(".src"),
      paste: sheet.querySelector(".paste"),
      chips: sheet.querySelector(".chips"),
      opts: sheet.querySelector(".opts"),
      res: sheet.querySelector(".res"),
      copy: sheet.querySelector(".copy"),
      rerun: sheet.querySelector(".rerun"),
      chat: sheet.querySelector(".chat"),
      go: sheet.querySelector(".go")
    };
    sheet.querySelector(".cx").onclick = closeSheet;
    els.paste.oninput = function () { STATE.text = els.paste.value; if (els.src) els.src.textContent = STATE.text.length ? (STATE.text.length + " chars") : ""; };
    els.go.onclick = runTransform;
    els.rerun.onclick = runTransform;
    els.copy.onclick = doCopy;
    els.chat.onclick = escalate;
    // explicit identity, not "none" — see aux_prefs.js: Motion One turns a
    // scale()-vs-"none" pair into a zero matrix and the sheet never appears
    anim(sheet, { opacity: [0, 1], transform: ["translateY(10px) scale(.98)", "translateY(0px) scale(1)"] }, { duration: 260, easing: "cubic-bezier(.22,1,.36,1)" });
    return scrim;
  }

  // ---- persistence of last action/opts (never clipboard text) ---------------
  function saveLast() {
    var s = LS(); if (!s) return;
    try { s.setItem("hermes_clip", JSON.stringify({ action: STATE.action, opts: STATE.opts })); } catch (e) {}
  }
  function loadLast() {
    var s = LS(); if (!s) return;
    try {
      var v = JSON.parse(s.getItem("hermes_clip") || "null");
      if (v && v.action) { STATE.action = v.action; STATE.opts = v.opts || {}; }
    } catch (e) {}
  }

  // ---- run / copy / escalate ------------------------------------------------
  function runTransform() {
    if (STATE.busy) return;
    var text = (STATE.text || "").trim();
    if (!text) {
      if (els.res) { els.res.className = "res err"; els.res.textContent = "Nothing on the clipboard yet."; }
      if (els.paste) els.paste.focus();
      return;
    }
    STATE.busy = true;
    if (els.go) els.go.disabled = true;
    if (els.rerun) els.rerun.disabled = true;
    if (els.copy) els.copy.disabled = true;
    if (els.res) { els.res.className = "res"; els.res.innerHTML = '<div class="shim" style="width:90%"></div><div class="shim" style="width:70%"></div><div class="shim" style="width:80%"></div>'; }
    fetch("/api/clip/transform", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: STATE.action, text: text, opts: STATE.opts, source: (W.__HERMES_QUICKASK__ ? "menubar" : "dashboard") })
    }).then(function (r) { return r.json().then(function (j) { return { status: r.status, j: j }; }); })
      .then(function (o) {
        var j = o.j || {};
        if (j.ok) {
          STATE.result = j.result || "";
          if (els.res) { els.res.className = "res"; els.res.textContent = STATE.result; anim(els.res, { opacity: [0, 1] }, { duration: 200 }); }
          if (els.copy) els.copy.disabled = false;
        } else {
          if (els.res) { els.res.className = "res err"; els.res.textContent = errText(j.error); }
        }
      }).catch(function () {
        if (els.res) { els.res.className = "res err"; els.res.textContent = "Could not reach the local model."; }
      }).then(function () {
        STATE.busy = false;
        if (els.go) els.go.disabled = false;
        if (els.rerun) els.rerun.disabled = false;
      });
  }

  function doCopy() {
    if (!STATE.result) return;
    clipWrite(STATE.result).then(function () {
      if (!els.copy) return;
      els.copy.innerHTML = IC_CHECK + "Copied";
      setTimeout(function () { if (els.copy) els.copy.innerHTML = IC_COPY + "Copy"; }, 1000);
    });
  }

  // escalation to the real, approval-gated agent — reuse existing contextual ask
  function escalate() {
    var t = (STATE.text || "").trim(); if (!t) { closeSheet(); return; }
    closeSheet();
    if (typeof W.askAbout === "function") W.askAbout(t, false);
    else if (typeof askAbout === "function") askAbout(t, false);
  }

  // ---- open / close ---------------------------------------------------------
  // Esc == clicking the scrim. The listener is installed ONLY while the sheet
  // is up and torn down in closeSheet, so when we're shut the key is untouched
  // and reaches whoever else wants it — in the menu-bar popover that's
  // aux_quickask's POP.input keydown, the popover's only Escape handler (it
  // "detects" us implicitly: while the sheet is open focus lives in the sheet,
  // so that input handler never fires). CAPTURE + stopPropagation makes that
  // implicit rule airtight for the one case focus is still in the popover
  // field: the topmost surface wins, exactly one layer closes per press.
  function onEscKey(e) {
    if (e.key !== "Escape" && e.key !== "Esc") return;
    if (!SHEET) return;                     // belt & braces: never swallow when shut
    e.preventDefault(); e.stopPropagation();
    closeSheet();
  }
  function openSheet() {
    var d = DOC(); if (!d) return;
    if (SHEET) return;   // already open
    ensureCatalog().then(function () {
      loadLast();
      if (STATE.action && !(CAT.actions && CAT.actions[STATE.action])) STATE.action = (CAT.order && CAT.order[0]) || "summarize";
      SHEET = buildSheet();
      d.addEventListener("keydown", onEscKey, true);
      renderChips(); renderOpts();
      // read the clipboard (tiers 1/2); tier 3 (manual paste) shows if empty
      clipRead().then(function (t) {
        STATE.text = (typeof t === "string") ? t : "";
        if (els.paste && STATE.text) els.paste.value = STATE.text;
        setSource();
        if (!STATE.text && els.paste) els.paste.focus();
      });
    });
  }
  function closeSheet() {
    var d = DOC();
    // remove first: a no-op when it was never added (⌘⇧V toggle, double close)
    if (d) d.removeEventListener("keydown", onEscKey, true);
    if (els && els.scrim && els.scrim.parentNode) els.scrim.parentNode.removeChild(els.scrim);
    SHEET = null; els = {}; STATE.result = ""; STATE.text = "";
  }

  // ---- entry points: bridge object + ⌘⇧V + launcher pill --------------------
  W.hermesClip = { open: openSheet, close: closeSheet };

  function wireHotkey() {
    var d = DOC(); if (!d) return;
    d.addEventListener("keydown", function (e) {
      if (!(e.metaKey && e.shiftKey && (e.key === "v" || e.key === "V"))) return;
      var t = e.target, tag = (t && t.tagName || "").toLowerCase();
      var typing = tag === "input" || tag === "textarea" || (t && t.isContentEditable);
      if (typing && !SHEET) return;         // don't hijack paste while typing
      e.preventDefault();
      if (SHEET) closeSheet(); else openSheet();
    });
  }

  function injectLauncher() {
    var d = DOC(); if (!d) return;
    var mount = d.getElementById("actions") || d.querySelector(".composer");
    if (!mount || d.getElementById("clip-launch")) return;
    var b = d.createElement("button");
    b.id = "clip-launch"; b.className = "chip";
    b.title = "Clipboard actions (⌘⇧V)";
    // Text only: this chip sits in the quick-action cluster where every other
    // chip is a bare label, and being the ONE with a glyph read as a mistake
    // rather than as emphasis. (index.html also hides .chip svg as a backstop.)
    b.textContent = "Clipboard";
    b.onclick = openSheet;
    mount.appendChild(b);
    // the cluster is capped at two rows — re-measure now that we added one
    try { if (typeof W.fitActionChips === "function") W.fitActionChips(); } catch (e) {}
  }

  function init() {
    try { wireHotkey(); } catch (e) {}
    try { injectLauncher(); } catch (e) {}
  }
  try {
    var d0 = DOC();
    if (d0 && !W.__HERMES_QUICKASK__) {   // in the main window, wire launcher + hotkey
      if (d0.readyState === "loading") d0.addEventListener("DOMContentLoaded", init);
      else init();
    }
  } catch (e) {}
})();
