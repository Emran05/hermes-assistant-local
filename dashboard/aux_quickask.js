// aux_quickask.js — Menu-bar Quick-Ask (P2.2).
//
// Auto-served at /aux_quickask.js.  ONE file, TWO roles, chosen by context:
//
//   * In the menu-bar popover document (a tiny HTML shell loaded by main.swift
//     via loadHTMLString with baseURL = the dashboard origin, so every fetch is
//     same-origin), window.__HERMES_QUICKASK__ is set, and this file BUILDS the
//     whole popover chat UI: it reuses the dashboard's job-based chat API
//     (/api/chat -> /api/chat/poll) with the reserved session "menubar".
//
//   * In the main window (index.html), __HERMES_QUICKASK__ is undefined, so this
//     file adds nothing visible — it only defines window.hermesQuickAskResume,
//     the hand-off shim the popover calls (via a Swift bridge) to resume an
//     approval in the real chat surface with working Approve/Deny.
//
// Read-only by posture: the popover has NO Approve/Deny control.  When a turn
// needs approval it shows a non-actionable "Approve in the main window" card and
// hands the SAME job id to index.html's streamJob, where the existing approval
// renderer completes the turn.  Zero emoji, bespoke SVG, 12-hour time.
(function () {
  "use strict";
  var W = (typeof window !== "undefined") ? window : {};

  if (W.__HERMES_QUICKASK__) { try { buildPopover(); } catch (e) { showFatal(e); } }
  else { W.hermesQuickAskResume = resumeInMain; }

  // ==========================================================================
  // MAIN-WINDOW SHIM  — resume a handed-off job in index.html's real chat
  // ==========================================================================
  // Reuses index.html's top-level globals.  Classic <script>s share one global
  // lexical scope, so `session` (a top-level `let`) and the `function`
  // declarations (setChatMode/loadHistory/addBubble/streamJob) are reachable and
  // (for session) reassignable from here.
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
  function E(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  var M = W.Motion || null;
  function anim(el, kf, opt) {
    try {
      if (RM()) return;
      if (M) return M.animate(el, kf, opt);
      if (el && el.animate) return el.animate(kf, opt);
    } catch (e) {}
  }
  function RM() { try { return !!(W.matchMedia && matchMedia("(prefers-reduced-motion:reduce)").matches); } catch (e) { return false; } }
  function bridge(action, extra) {
    try {
      var mh = W.webkit && W.webkit.messageHandlers && W.webkit.messageHandlers.hermes;
      if (mh) { var m = { action: action }; if (extra) for (var k in extra) m[k] = extra[k]; mh.postMessage(m); }
    } catch (e) {}
  }

  // escape-FIRST, then a short format pass (mirrors index.html's renderMd order).
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

  var SPARK = '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9z"/></svg>';
  var IC_CLIP = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="3" width="8" height="4" rx="1"/><path d="M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/></svg>';
  var IC_SEND = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>';
  var IC_WARN = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';

  var CSS = [
    "*{box-sizing:border-box}html,body{margin:0;height:100%}",
    "body{font:13px/1.5 -apple-system,BlinkMacSystemFont,'SF Pro Text',sans-serif;color:#0F172A;background:transparent}",
    "@media(prefers-color-scheme:dark){body{color:#E7ECF5}}",
    "#qa{display:flex;flex-direction:column;height:100vh;overflow:hidden}",
    ".qa-hd{display:flex;align-items:center;gap:8px;padding:11px 13px;border-bottom:1px solid rgba(120,130,150,.16)}",
    ".qa-hd .mk{color:#8B5CF6;display:flex}",
    ".qa-hd .t{font-weight:650;font-size:13.5px}",
    ".qa-hd .sp{margin-left:auto;display:flex;gap:6px}",
    ".qa-hd button{border:0;background:transparent;color:inherit;cursor:pointer;font-size:11.5px;opacity:.72;padding:4px 7px;border-radius:8px;display:inline-flex;align-items:center;gap:4px}",
    ".qa-hd button:hover{opacity:1;background:rgba(120,130,150,.16)}",
    "#qa-strip{flex:1;overflow:auto;padding:12px 13px;display:flex;flex-direction:column;gap:9px}",
    ".qa-b{max-width:88%;padding:8px 11px;border-radius:13px;white-space:pre-wrap;word-break:break-word;font-size:12.8px}",
    ".qa-b.user{align-self:flex-end;background:linear-gradient(135deg,#6366F1,#8B5CF6);color:#fff;border-bottom-right-radius:5px}",
    ".qa-b.bot{align-self:flex-start;background:rgba(120,130,150,.13);border-bottom-left-radius:5px}",
    ".qa-b.bot .md p{margin:0 0 6px}.qa-b.bot .md p:last-child{margin:0}",
    ".qa-b.bot .md code{background:rgba(120,130,150,.22);padding:1px 4px;border-radius:4px;font-size:12px}",
    ".qa-b.bot .md pre{background:rgba(120,130,150,.16);padding:8px;border-radius:8px;overflow:auto}",
    ".qa-b.bot .md ul,.qa-b.bot .md ol{margin:4px 0;padding-left:18px}",
    ".qa-b.bot .md a{color:#8B5CF6}",
    ".qa-b.err{background:rgba(220,38,38,.1);color:#DC2626}",
    ".caret{opacity:.6}",
    ".qa-note{align-self:center;font-size:11px;opacity:.55;text-align:center;padding:2px 6px}",
    ".qa-appr{align-self:stretch;border:1px solid rgba(217,119,6,.45);background:rgba(217,119,6,.10);border-radius:12px;padding:10px 11px}",
    ".qa-appr .h{display:flex;align-items:center;gap:6px;font-weight:650;font-size:12px;color:#B45309;margin-bottom:5px}",
    "@media(prefers-color-scheme:dark){.qa-appr .h{color:#FBBF24}}",
    ".qa-appr code{display:block;font-size:11.5px;background:rgba(120,130,150,.16);padding:6px 8px;border-radius:7px;margin:4px 0;white-space:pre-wrap;word-break:break-word}",
    ".qa-appr button{width:100%;margin-top:6px;border:0;border-radius:9px;padding:8px;font-weight:650;font-size:12px;cursor:pointer;background:linear-gradient(135deg,#D97706,#B45309);color:#fff}",
    ".qa-cmp{display:flex;gap:7px;align-items:flex-end;padding:10px 11px;border-top:1px solid rgba(120,130,150,.16)}",
    ".qa-cmp textarea{flex:1;resize:none;border:1px solid rgba(120,130,150,.3);border-radius:11px;padding:8px 11px;max-height:110px;",
    "font:12.8px/1.45 -apple-system,sans-serif;color:inherit;background:rgba(255,255,255,.5)}",
    "@media(prefers-color-scheme:dark){.qa-cmp textarea{background:rgba(0,0,0,.24)}}",
    ".qa-cmp textarea:disabled{opacity:.5}",
    ".qa-cmp .go{flex:0 0 auto;width:34px;height:34px;border:0;border-radius:10px;cursor:pointer;display:flex;align-items:center;justify-content:center;",
    "background:linear-gradient(135deg,#6366F1,#8B5CF6);color:#fff}",
    ".qa-cmp .go:disabled{opacity:.45;cursor:default}",
    ".qa-foot{font-size:10.5px;opacity:.5;text-align:center;padding:0 0 7px}",
    ".dots i{display:inline-block;width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.35;margin:0 1px;animation:qb 1.2s infinite}",
    ".dots i:nth-child(2){animation-delay:.16s}.dots i:nth-child(3){animation-delay:.32s}",
    "@keyframes qb{0%,100%{opacity:.25}45%{opacity:.85}}"
  ].join("");

  var POP = { strip: null, input: null, send: null, foot: null, job: null, polling: false, ready: false };

  function showFatal(e) {
    var d = document.getElementById("qa");
    if (d) d.textContent = "Quick Ask failed to load. Reopen to retry.";
  }

  function buildPopover() {
    var d = document;
    var st = d.createElement("style"); st.textContent = CSS; d.head.appendChild(st);
    var root = d.getElementById("qa") || d.body;
    root.textContent = "";
    root.innerHTML =
      '<div class="qa-hd"><span class="mk">' + SPARK + '</span><span class="t">Quick Ask</span>' +
      '<span class="sp"><button id="qa-clip" title="Clipboard actions">' + IC_CLIP + 'Clipboard</button>' +
      '<button id="qa-main" title="Open in main window">Main window</button></span></div>' +
      '<div id="qa-strip"></div>' +
      '<div class="qa-cmp"><textarea id="qa-in" rows="1" placeholder="Ask Hermes…"></textarea>' +
      '<button class="go" id="qa-go" aria-label="Send">' + IC_SEND + '</button></div>' +
      '<div class="qa-foot" id="qa-foot">⌃⌥Space to toggle · Esc to close</div>';

    POP.strip = d.getElementById("qa-strip");
    POP.input = d.getElementById("qa-in");
    POP.send = d.getElementById("qa-go");
    POP.foot = d.getElementById("qa-foot");

    d.getElementById("qa-main").onclick = function () { bridge("openMain", { session: "menubar" }); };
    d.getElementById("qa-clip").onclick = function () { if (W.hermesClip && W.hermesClip.open) W.hermesClip.open(); };
    POP.send.onclick = doSend;
    POP.input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { bridge("close"); return; }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doSend(); }
    });
    POP.input.addEventListener("input", function () { this.style.height = "auto"; this.style.height = Math.min(this.scrollHeight, 110) + "px"; });

    // Swift calls this after showing the popover to focus the input.
    W.__qaFocus = function () { try { POP.input.focus(); } catch (e) {} };

    anim(root, [{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 220, easing: "cubic-bezier(.22,1,.36,1)" });

    checkHealth();
    loadHistory();
  }

  function bubble(text, who, isMd) {
    var d = document;
    var b = d.createElement("div"); b.className = "qa-b " + who;
    if (isMd) { var m = d.createElement("div"); m.className = "md"; m.innerHTML = qaMd(text); b.appendChild(m); }
    else b.textContent = text;
    POP.strip.appendChild(b);
    POP.strip.scrollTop = POP.strip.scrollHeight;
    return b;
  }
  function note(text) {
    var n = document.createElement("div"); n.className = "qa-note"; n.textContent = text;
    POP.strip.appendChild(n); POP.strip.scrollTop = POP.strip.scrollHeight; return n;
  }

  function checkHealth() {
    fetch("/api/health").then(function (r) { return r.json(); }).then(function (h) {
      if (h && h.model_online) { setReady(true); return; }
      setReady(false);
      note("The local model is starting…");
      setTimeout(checkHealth, 2000);
    }).catch(function () { setReady(false); setTimeout(checkHealth, 2000); });
  }
  function setReady(on) {
    POP.ready = on;
    if (POP.input) POP.input.disabled = !on;
    if (POP.send) POP.send.disabled = !on;
  }

  function loadHistory() {
    fetch("/api/history?session=menubar").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.messages || !d.messages.length) return;
      // keep the strip compact: show the last few turns
      var msgs = d.messages.slice(-8);
      msgs.forEach(function (m) {
        var b = bubble(m.text, m.role === "user" ? "user" : "bot", m.role !== "user" && !m.err);
        if (m.err) b.classList.add("err");
      });
    }).catch(function () {});
  }

  function doSend() {
    if (!POP.ready || POP.polling) return;
    var text = (POP.input.value || "").trim();
    if (!text) return;
    POP.input.value = ""; POP.input.style.height = "auto";
    bubble(text, "user", false);
    var thinking = bubble("", "bot", false);
    thinking.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
    setReady(false); POP.polling = true;
    fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session: "menubar" })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.job) { POP.job = d.job; pollJob(d.job, thinking); }
      else {
        // paused fast-path or error: no job, show the reply verbatim
        thinking.remove();
        var b = bubble((d && d.reply) || "The assistant is unavailable.", "bot", !!(d && d.ok));
        if (!(d && d.ok)) b.classList.add("err");
        if (d && !d.ok) note("Resume from the model menu in the main window.");
        POP.polling = false; setReady(true); POP.input.focus();
      }
    }).catch(function () {
      thinking.remove(); bubble("Could not reach the dashboard.", "bot", false).classList.add("err");
      POP.polling = false; setReady(true);
    });
  }

  function pollJob(job, thinking) {
    var bub = null, handed = false;
    var ensure = function () { if (!bub) { thinking.remove(); bub = bubble("", "bot", true); } return bub; };
    var loop = function () {
      fetch("/api/chat/poll?job=" + encodeURIComponent(job)).then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.gone) { thinking.remove(); bubble("That request was lost (dashboard restarted?) — ask again.", "bot", false).classList.add("err"); finish(); return; }
        if (d && d.text) { ensure().querySelector(".md").innerHTML = qaMd(d.text) + (d.done ? "" : ' <span class="caret">▌</span>'); POP.strip.scrollTop = POP.strip.scrollHeight; }
        if (d && d.state === "approval" && d.approval && !handed) {
          handed = true;
          ensure();
          var what = d.approval.command || d.approval.summary || d.approval.tool || d.approval.name || "a sensitive action";
          var card = document.createElement("div"); card.className = "qa-appr";
          card.innerHTML = '<div class="h">' + IC_WARN + 'Needs your approval</div><code></code>' +
            '<div class="sub" style="font-size:11px;opacity:.75">Approvals are only granted in the main window.</div>' +
            '<button>Approve in the main window</button>';
          card.querySelector("code").textContent = what;
          card.querySelector("button").onclick = function () {
            card.querySelector("button").disabled = true;
            bridge("openApproval", { job: job });
            note("Handed to the main window.");
          };
          POP.strip.appendChild(card); POP.strip.scrollTop = POP.strip.scrollHeight;
          finish();   // stop polling in the popover; the main window owns it now
          return;
        }
        if (d && d.done) {
          var b = ensure();
          b.querySelector(".md").innerHTML = qaMd(d.reply || d.text || "");
          if (d.err) b.classList.add("err");
          finish(); return;
        }
        setTimeout(loop, 650);
      }).catch(function () { setTimeout(loop, 900); });
    };
    var finish = function () { POP.polling = false; setReady(true); if (POP.input) POP.input.focus(); };
    loop();
  }
})();
