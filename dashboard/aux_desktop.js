// aux_desktop.js — "Agent Desktop" surface (hands-3, desktop-view half).
//
// A first-class #view-desktop tab that shows what the agent is doing on the Mac:
//   * a LOCAL-ONLY live screenshot stream (poll /api/desktop/shots, refresh)
//   * the computer_use flight-recorder timeline (every on-screen action,
//     irreversible-marked, 12-hour times)
//   * "Capture now" + a compose box to hand the agent a Mac task (-> /api/chat)
//
// This panel owns no dangerous capability. It exposes NO merge / approve /
// approve-and-restart control and no self-approvable action (SECURITY §4.9):
// approvals stay out-of-band in the main chat. Screenshots here are local-only
// and never sent anywhere.
//
// Auto-served at /aux_desktop.js. Self-injects its tab + view + setView
// integration (idempotent), so the only required index.html hook is the
// <script> tag; baked-in tab/view hooks are detected and reused, not doubled.
//
// CLAUDE.md laws: zero emoji, bespoke two-tone SVG, Liquid Glass, Motion One,
// 12-hour times, esc() coercion. renderDesktop() is pure + headless-safe.

(function () {
  "use strict";

  // ---- guarded helpers (headless eval must never throw) --------------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
  function t12(sec) {
    if (sec == null) return "";
    try {
      return new Date(sec * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true });
    } catch (e) { return ""; }
  }
  function rel(sec) {
    if (sec == null) return "";
    var s = Date.now() / 1000 - sec;
    if (s < 60) return "just now";
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function anim(el, kf, opt) {
    try { if (typeof animate === "function") return animate(el, kf, opt); } catch (e) {}
  }

  // ---- bespoke two-tone SVG glyphs (accent fill + currentColor stroke) ------
  var GLY = {
    monitor: '<rect x="2.5" y="4" width="19" height="12.5" rx="2" fill="currentColor" opacity=".12"/>' +
             '<rect x="2.5" y="4" width="19" height="12.5" rx="2"/><path d="M9 20.5h6M12 16.5v4" stroke-linecap="round"/>',
    camera:  '<path d="M4 8.5h3l1.4-2h7.2L20 8.5h0a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-7.5a2 2 0 0 1 2-2z" fill="currentColor" opacity=".12"/>' +
             '<path d="M4 8.5h3l1.4-2h7.2L20 8.5a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-7.5a2 2 0 0 1 2-2z"/><circle cx="12" cy="13.5" r="3.2"/>',
    activity:'<path d="M3 12h4l2.5-6 4 13 2.5-7H21" stroke-linecap="round" stroke-linejoin="round"/>',
    task:    '<rect x="3" y="4" width="18" height="16" rx="2" fill="currentColor" opacity=".12"/>' +
             '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 10l3 2.5-3 2.5M12.5 15.5H17" stroke-linecap="round" stroke-linejoin="round"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-1.5 5" stroke-linecap="round"/><path d="M20 5v6h-6" stroke-linecap="round" stroke-linejoin="round"/>',
    lock:    '<rect x="5" y="10.5" width="14" height="10" rx="2" fill="currentColor" opacity=".12"/>' +
             '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>'
  };
  function ic(name, sz) {
    sz = sz || 18;
    return '<svg class="ic" viewBox="0 0 24 24" width="' + sz + '" height="' + sz +
      '" fill="none" stroke="currentColor" stroke-width="1.7" style="flex:0 0 auto">' + (GLY[name] || "") + "</svg>";
  }

  // ---- one-time CSS --------------------------------------------------------
  function injectCss() {
    if (typeof document === "undefined" || document.getElementById("desk-css")) return;
    var s = document.createElement("style");
    s.id = "desk-css";
    s.textContent = [
      "#view-desktop{--dac:var(--iris)}",
      ".desk-card{grid-column:1/3;margin-bottom:16px}",
      ".desk-h{display:flex;align-items:center;gap:9px;font-weight:640;font-size:15px;margin:0 0 2px}",
      ".desk-h .ic{color:var(--dac)}",
      ".desk-h .desk-note{margin-left:auto;display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:500;color:var(--faint)}",
      ".desk-actbar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 4px}",
      ".desk-preview{position:relative;border-radius:14px;overflow:hidden;background:var(--glass-2);" +
        "border:1px solid var(--hairline);min-height:180px;display:flex;align-items:center;justify-content:center}",
      ".desk-preview img{width:100%;height:auto;display:block;max-height:60vh;object-fit:contain}",
      ".desk-empty{color:var(--faint);font-size:12.5px;padding:34px 18px;text-align:center;line-height:1.6}",
      ".desk-meta{display:flex;align-items:center;gap:10px;font-size:11.5px;color:var(--muted);margin:8px 2px 2px}",
      ".desk-strip{display:flex;gap:7px;overflow-x:auto;padding:9px 0 2px}",
      ".desk-strip img{height:52px;width:auto;border-radius:7px;border:1px solid var(--hairline);cursor:pointer;flex:0 0 auto;opacity:.82;transition:opacity .15s}",
      ".desk-strip img:hover,.desk-strip img.on{opacity:1;border-color:var(--dac)}",
      ".desk-tl{display:flex;flex-direction:column;gap:2px;max-height:360px;overflow-y:auto;margin-top:6px}",
      ".desk-row{display:flex;align-items:center;gap:10px;padding:8px 2px;border-bottom:1px solid var(--hairline);font-size:12.5px}",
      ".desk-row .dv{font-weight:620;min-width:96px;color:var(--ink)}",
      ".desk-row .ds{flex:1;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".desk-row .dt{color:var(--faint);font-size:11px;white-space:nowrap}",
      ".desk-chip{font-size:9.5px;font-weight:640;letter-spacing:.03em;text-transform:uppercase;padding:2px 6px;border-radius:6px;" +
        "background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn);white-space:nowrap}",
      ".desk-src{font-size:9.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.04em}",
      ".desk-compose{display:flex;flex-direction:column;gap:8px;margin-top:8px}",
      ".desk-compose textarea{width:100%;box-sizing:border-box;min-height:62px;resize:vertical;padding:10px 12px;border-radius:11px;" +
        "border:1px solid var(--hairline);background:var(--glass-2);color:var(--ink);font:inherit;font-size:13px}",
      ".desk-compose .row{display:flex;align-items:center;gap:10px}",
      ".desk-status{font-size:11.5px;color:var(--muted);min-height:15px;flex:1}",
      ".desk-hint{font-size:11px;color:var(--faint);line-height:1.55;margin-top:2px}",
      ".desk-health{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600}",
      ".desk-dot{width:7px;height:7px;border-radius:50%;background:var(--ok);flex:0 0 auto}",
      ".desk-dot.warn{background:var(--warn)}"
    ].join("");
    document.head.appendChild(s);
  }

  // ---- inner renderers (pure) ----------------------------------------------
  function renderLive(state, fullUri) {
    state = state || {};
    var shots = state.shots || [];
    var health = state.capturable
      ? '<span class="desk-health"><span class="desk-dot"></span>capture ready</span>'
      : '<span class="desk-health"><span class="desk-dot warn"></span>Screen Recording not granted</span>';

    var h = "";
    h += '<div class="desk-h">' + ic("monitor") + "Live desktop" +
      '<span class="desk-note">' + ic("lock", 13) + "local only</span></div>";
    h += '<div class="desk-actbar">' +
      '<button class="primary" id="desk-cap">' + ic("camera", 15) + "Capture now</button>" +
      '<button class="ghost" id="desk-refresh">' + ic("refresh", 15) + "Refresh</button>" +
      '<span style="margin-left:auto;align-self:center">' + health + "</span></div>";

    var newest = shots[0];
    if (fullUri || (newest && newest.thumb)) {
      h += '<div class="desk-preview"><img id="desk-full" alt="latest desktop capture" src="' +
        E(fullUri || newest.thumb) + '"></div>';
    } else {
      h += '<div class="desk-preview"><div class="desk-empty">No captures yet.<br>' +
        'Click <b>Capture now</b> to grab the current screen — it stays on this Mac.</div></div>';
    }

    if (newest) {
      h += '<div class="desk-meta"><span>' + E(t12(newest.ts)) + " · " + E(rel(newest.ts)) + "</span>" +
        '<span>·</span><span>' + (state.count || shots.length) + " frame" + ((state.count || shots.length) === 1 ? "" : "s") + " buffered</span></div>";
    }
    if (state.err) {
      h += '<div class="desk-meta" style="color:var(--warn)">' + E(state.err) + "</div>";
    }
    h += '<div class="desk-meta" style="color:var(--faint)">' + E(state.note || "") + "</div>";

    if (shots.length) {
      h += '<div class="desk-strip">';
      shots.forEach(function (s, i) {
        h += '<img data-name="' + E(s.name) + '"' + (i === 0 ? ' class="on"' : "") +
          ' title="' + E(t12(s.ts)) + '" src="' + E(s.thumb) + '">';
      });
      h += "</div>";
    }
    return h;
  }

  function renderTimeline(tl) {
    tl = tl || {};
    if (tl.available === false) {
      return '<div class="desk-h">' + ic("activity") + "On-screen activity</div>" +
        '<div class="desk-empty">' + E(tl.reason || "The flight recorder is not available.") + "</div>";
    }
    var rows = tl.rows || [];
    var h = '<div class="desk-h">' + ic("activity") + "On-screen activity" +
      '<span class="desk-note">every computer_use action · irreversible</span></div>';
    if (!rows.length) {
      h += '<div class="desk-empty">No desktop-control actions recorded yet.<br>' +
        "When the agent clicks, types, or captures on screen, it appears here.</div>";
      return h;
    }
    h += '<div class="desk-tl">';
    rows.forEach(function (r) {
      var detail = (r.target && r.action && r.target.toLowerCase() !== r.action.toLowerCase())
        ? r.summary || r.target : (r.summary || "");
      h += '<div class="desk-row">' +
        '<span class="dv">' + E(r.action || r.tool) + "</span>" +
        '<span class="ds">' + E(detail) + "</span>" +
        '<span class="desk-chip">irreversible</span>' +
        '<span class="desk-src">' + E(r.source || "") + "</span>" +
        '<span class="dt">' + E(t12(r.ts)) + "</span></div>";
    });
    h += "</div>";
    return h;
  }

  function renderControls() {
    // NOTE: no approve/merge/approve-and-restart control here (SECURITY §4.9).
    var h = '<div class="desk-h">' + ic("task") + "Give the agent a Mac task</div>";
    h += '<div class="desk-compose">' +
      '<textarea id="desk-task" placeholder="e.g. Open Chrome and play some lo-fi on YouTube · Screenshot my screen and send it to me · What&#39;s on my screen?"></textarea>' +
      '<div class="row"><span class="desk-status" id="desk-status"></span>' +
      '<button class="primary" id="desk-send">' + ic("task", 15) + "Send to agent</button></div>" +
      '<div class="desk-hint">Runs through your assistant on the normal path — every on-screen action is recorded above, ' +
      "and anything consequential (a send, a purchase, a delete) still asks for your approval in the chat. " +
      "This panel never approves anything on your behalf.</div></div>";
    return h;
  }

  // ---- full panel (PURE, headless-safe entry point) ------------------------
  function renderDesktop(state, tl, fullUri) {
    return '<section class="card glass desk-card" id="desk-live-card"><div class="body" id="desk-live">' +
        renderLive(state, fullUri) + "</div></section>" +
      '<section class="card glass desk-card" id="desk-tl-card"><div class="body" id="desk-timeline">' +
        renderTimeline(tl) + "</div></section>" +
      '<section class="card glass desk-card" id="desk-ctl-card"><div class="body">' +
        renderControls() + "</div></section>";
  }

  // expose for headless harness + setView integration
  if (typeof window !== "undefined") {
    window.renderDesktop = renderDesktop;
  }

  // ===========================================================================
  // Everything below touches the DOM — fully guarded so headless eval skips it.
  // ===========================================================================
  if (typeof document === "undefined") return;

  var $ = function (id) { return document.getElementById(id); };
  var _deskTimer = null;
  var _deskNewest = "";   // name of the frame currently shown big
  var _deskBusy = false;

  function ensureView() {
    var v = $("view-desktop");
    if (!v) {
      var stage = document.querySelector(".stage");
      if (!stage) return null;
      v = document.createElement("div");
      v.className = "view";
      v.id = "view-desktop";
      v.setAttribute("role", "tabpanel");
      v.hidden = true;
      stage.appendChild(v);
    }
    return v;
  }

  function ensureTab() {
    var b = $("tab-desktop");
    if (!b) {
      var seg = document.querySelector(".seg");
      if (!seg) return;
      b = document.createElement("b");
      b.id = "tab-desktop";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", "false");
      b.innerHTML = '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">' +
        GLY.monitor + "</svg>Desktop";
      seg.appendChild(b);
    }
    // always (re)wire the click — covers both JS-injected and HTML-baked tabs
    b.onclick = function () { if (typeof window.setView === "function") window.setView("desktop"); };
  }

  function markTabs() {
    var seg = document.querySelector(".seg");
    if (!seg) return;
    var bs = seg.querySelectorAll("b");
    for (var i = 0; i < bs.length; i++) {
      var on = bs[i].id === "tab-desktop";
      bs[i].classList.toggle("on", on);
      bs[i].setAttribute("aria-selected", on ? "true" : "false");
    }
  }

  // Wrap the global setView so it also handles 'desktop' (idempotent). We manage
  // desktop ourselves and delegate every other view to the original.
  function wrapSetView() {
    if (window.__deskSetView) return;
    window.__deskSetView = true;
    var orig = window.setView;
    window.setView = function (v) {
      if (v === "desktop") {
        ["view-hub", "view-mind", "view-console"].forEach(function (id) {
          var e = $(id); if (e) e.hidden = true;
        });
        var vd = $("view-desktop"); if (vd) vd.hidden = false;
        markTabs();
        var hc = $("hubctl"); if (hc) hc.style.display = "none";
        try { localStorage.setItem("hermes_view", "desktop"); } catch (e) {}
        startPolling();
        loadDesktop(true);
        return;
      }
      stopPolling();
      var vd2 = $("view-desktop"); if (vd2) vd2.hidden = true;
      var td = $("tab-desktop");
      if (td) { td.classList.remove("on"); td.setAttribute("aria-selected", "false"); }
      if (typeof orig === "function") orig(v);
    };
  }

  // ---- data + render -------------------------------------------------------
  function loadDesktop(force) {
    var host = $("view-desktop");
    if (!host) return;
    if (!host.querySelector("#desk-live-card")) {
      host.innerHTML = renderDesktop({ shots: [], count: 0, capturable: true, note: "" },
                                     { available: true, rows: [] }, null);
      wireStatic();
      revealCards(host);
    }
    refresh(force);
  }

  function refresh(force) {
    fetch("/api/desktop/shots").then(function (r) { return r.json(); }).then(function (st) {
      var live = $("desk-live");
      if (!live || !st) return;
      var shots = st.shots || [];
      var newest = shots[0];
      // re-render the live inner (preserves compose box; separate card)
      live.innerHTML = renderLive(st, (newest && newest.name === _deskNewest) ? _lastFull : null);
      wireLive();
      if (newest && (force || newest.name !== _deskNewest)) {
        pinFrame(newest.name);
      }
    }).catch(function () {});

    fetch("/api/desktop/timeline").then(function (r) { return r.json(); }).then(function (tl) {
      var el = $("desk-timeline");
      if (el) el.innerHTML = renderTimeline(tl);
    }).catch(function () {});
  }

  var _lastFull = null;
  function pinFrame(name) {
    fetch("/api/desktop/shot?name=" + encodeURIComponent(name))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok || !d.data_uri) return;
        _deskNewest = name;
        _lastFull = d.data_uri;
        var img = $("desk-full");
        if (img) {
          img.src = d.data_uri;
          if (!REDUCEo()) anim(img, { opacity: [0.4, 1] }, { duration: 0.35 });
        }
        var strip = document.querySelectorAll(".desk-strip img");
        for (var i = 0; i < strip.length; i++) {
          strip[i].classList.toggle("on", strip[i].getAttribute("data-name") === name);
        }
      }).catch(function () {});
  }
  function REDUCEo() { try { return typeof REDUCE !== "undefined" && REDUCE; } catch (e) { return false; } }

  // ---- wiring --------------------------------------------------------------
  function wireStatic() {
    var send = $("desk-send"), task = $("desk-task");
    if (send && task) {
      send.onclick = function () { submitTask(task, send); };
      task.addEventListener("keydown", function (e) {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); submitTask(task, send); }
      });
    }
  }

  function wireLive() {
    var cap = $("desk-cap"), ref = $("desk-refresh");
    if (cap) cap.onclick = doCapture;
    if (ref) ref.onclick = function () { refresh(true); };
    var strip = document.querySelectorAll(".desk-strip img");
    for (var i = 0; i < strip.length; i++) {
      strip[i].onclick = function () { pinFrame(this.getAttribute("data-name")); };
    }
  }

  function doCapture() {
    if (_deskBusy) return;
    _deskBusy = true;
    var cap = $("desk-cap");
    if (cap) cap.disabled = true;
    fetch("/api/desktop/capture", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(function (r) { return r.json(); })
      .then(function () { refresh(true); })
      .catch(function () {})
      .then(function () { _deskBusy = false; if ($("desk-cap")) $("desk-cap").disabled = false; });
  }

  function newSession() {
    try {
      if (typeof window.session === "string" && /^[A-Za-z0-9._-]{1,80}$/.test(window.session)) return window.session;
    } catch (e) {}
    return "desktop-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  function submitTask(task, send) {
    var text = (task.value || "").trim();
    var status = $("desk-status");
    if (!text) { if (status) status.textContent = "Type a task first."; return; }
    send.disabled = true;
    if (status) status.textContent = "Sending to your assistant…";
    var sid = newSession();
    fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session: sid })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ok || !d.job) {
        if (status) status.textContent = (d && d.reply) ? d.reply : "Could not start the task.";
        send.disabled = false;
        return;
      }
      task.value = "";
      pollJob(d.job, status, send);
    }).catch(function () {
      if (status) status.textContent = "Could not reach the assistant.";
      send.disabled = false;
    });
  }

  function pollJob(job, status, send) {
    var tries = 0;
    (function step() {
      tries++;
      fetch("/api/chat/poll?job=" + encodeURIComponent(job))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d || d.gone) { if (status) status.textContent = "Task finished."; send.disabled = false; return; }
          if (d.approval) {
            // Consequential — approval is out-of-band in the chat, never here (§4.9).
            if (status) status.textContent = "Needs your approval — open the chat (right) to approve.";
            send.disabled = false;
            refresh(true);
            return;
          }
          if (d.done) {
            if (status) status.textContent = d.reply ? ("Done: " + String(d.reply).slice(0, 120)) : "Done.";
            send.disabled = false;
            refresh(true);
            return;
          }
          if (status) status.textContent = d.status ? ("Working… " + d.status) : "Working…";
          if (tries < 120) setTimeout(step, 1200);
          else { send.disabled = false; }
        }).catch(function () { send.disabled = false; });
    })();
  }

  function revealCards(host) {
    if (REDUCEo()) return;
    var cards = host.querySelectorAll(".desk-card");
    if (typeof revealStagger === "function") { revealStagger(cards, 60); return; }
    for (var i = 0; i < cards.length; i++) anim(cards[i], { opacity: [0, 1], transform: ["translateY(10px)", "none"] }, { duration: 0.4, delay: i * 0.06 });
  }

  // ---- polling lifecycle ---------------------------------------------------
  function startPolling() {
    stopPolling();
    _deskTimer = setInterval(function () {
      var vd = $("view-desktop");
      if (!vd || vd.hidden) { stopPolling(); return; }
      refresh(false);
    }, 4000);
  }
  function stopPolling() { if (_deskTimer) { clearInterval(_deskTimer); _deskTimer = null; } }

  // ---- integrate at load ---------------------------------------------------
  function integrate() {
    injectCss();
    ensureView();
    ensureTab();
    wrapSetView();
    // if the page booted with the desktop view stored, render it now
    try {
      if (localStorage.getItem("hermes_view") === "desktop") window.setView("desktop");
    } catch (e) {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", integrate);
  } else {
    integrate();
  }
})();
