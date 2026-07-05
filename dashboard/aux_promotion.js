// aux_promotion.js — Model Promotion Gate UI (P3.2 B2).
//
// Auto-served at /aux_promotion.js (the /aux_*.js static rule). Loaded after
// index.html's inline scripts, so it wraps window.loadModels the way the
// other aux JS wraps window.mindExtras — ZERO index.html edits beyond the
// one script tag.
//
// What it adds to the MODEL MENU (#model-menu, rebuilt by loadModels()):
//   * a per-model drill badge:  ✓ drilled 6/6  /  ✗ failed 2/6  /  — undrilled
//   * a per-model "Drill" affordance (POST /api/models/drill, then polls)
//   * the per-model license note line (Apache-2.0, "Built with Llama", …)
// Because index.html's 30s setInterval captured the ORIGINAL loadModels
// reference, a MutationObserver on #model-menu re-decorates every rebuild,
// whichever code path triggered it.
//
// Mind view: chains onto window.mindExtras to surface the "Built with Llama"
// attribution (Llama 3.1 Community License) in the Mind hero when a
// Llama-family model is in the roster.
//
// All DOM access is typeof-guarded so the headless node render harness never
// throws. No emoji (the ✓/✗/— badge glyphs are text dingbats per spec), no
// new fetch paths beyond /api/models and /api/models/drill.

(function () {
  "use strict";

  var PRO = null;              // last /api/models payload (drill-decorated)
  var lastFetch = 0;
  var pollTimer = null;

  function W() { return (typeof window !== "undefined") ? window : null; }
  function D() { return (typeof document !== "undefined") ? document : null; }
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }

  // ---- data -----------------------------------------------------------------
  async function proFetch(force) {
    var now = Date.now();
    if (!force && PRO && now - lastFetch < 5000) return PRO;
    try {
      PRO = await (await fetch("/api/models")).json();
      lastFetch = Date.now();
    } catch (e) {}
    return PRO;
  }

  // ---- styles (injected once) -------------------------------------------------
  function ensureStyle() {
    var d = D(); if (!d || d.getElementById("pro-style")) return;
    var st = d.createElement("style");
    st.id = "pro-style";
    st.textContent =
      ".pro-line{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:10.5px;line-height:1.35}" +
      ".pro-badge{font-weight:600;white-space:nowrap}" +
      ".pro-badge.pass{color:var(--ok)}" +
      ".pro-badge.fail{color:var(--bad)}" +
      ".pro-badge.none{color:var(--faint)}" +
      ".pro-badge.busy{color:var(--iris)}" +
      ".pro-lic{color:var(--faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}" +
      ".pro-drill{margin-left:auto;flex:none;font-size:10px;font-weight:600;padding:2px 8px;border-radius:7px;" +
      "border:1px solid var(--hairline);background:var(--chip);color:var(--muted);cursor:pointer}" +
      ".pro-drill:hover{color:var(--ink);border-color:var(--iris)}" +
      "#pro-llama{margin-top:8px;font-size:10.5px;color:var(--faint)}";
    (d.head || d.body || d.documentElement).appendChild(st);
  }

  // ---- badges -----------------------------------------------------------------
  function badgeFor(m) {
    var of = m.drill_of || 6;
    if (m.drilling) return { cls: "busy", txt: "drilling…" };
    if (!m.drilled) return { cls: "none", txt: "— undrilled" };
    if (m.drill_pass) return { cls: "pass", txt: "✓ drilled " + m.drill_score + "/" + of };
    return { cls: "fail", txt: "✗ failed " + m.drill_score + "/" + of };
  }

  function decorate() {
    var d = D(); if (!d || !PRO) return;
    ensureStyle();
    var menu = d.getElementById("model-menu"); if (!menu) return;
    var rows = menu.querySelectorAll(".mmi");
    for (var i = 0; i < rows.length; i++) (function (el) {
      if (el.getAttribute("data-pro") === "1") return;
      var id = el.getAttribute("data-id");
      var m = ((PRO.models || []).filter(function (x) { return x.id === id; }))[0];
      if (!m) return;
      el.setAttribute("data-pro", "1");
      var mid = el.querySelector(".rm2");
      var host = (mid && mid.parentNode) ? mid.parentNode : el;
      var b = badgeFor(m);
      var line = d.createElement("div");
      line.className = "pro-line";
      var html = '<span class="pro-badge ' + b.cls + '">' + E(b.txt) + "</span>" +
                 '<span class="pro-lic" title="' + E(m.license_note || "") + '">' +
                 E(m.license_note || "") + "</span>";
      // Drill affordance: downloaded models only; hidden while any drill runs
      if (m.downloaded && !PRO.drill_running) {
        html += '<button class="pro-drill" type="button">' +
                (m.drilled ? "Re-drill" : "Drill") + "</button>";
      }
      line.innerHTML = html;
      var btn = line.querySelector(".pro-drill");
      if (btn) btn.onclick = function (ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        startDrill(m, btn);
      };
      host.appendChild(line);
    })(rows[i]);
  }

  // ---- drill launch + poll ------------------------------------------------------
  async function startDrill(m, btn) {
    var swap = PRO && PRO.active && PRO.active !== m.id;
    var msg = swap
      ? "Drill " + (m.label || m.id) + "? This temporarily switches the model " +
        "server to it (and back) — a few minutes. Chat stays usable afterwards."
      : "Drill " + (m.label || m.id) + "? Six quick tool-calling checks against " +
        "the running model (~1 minute).";
    try { if (typeof confirm === "function" && !confirm(msg)) return; } catch (e) {}
    var r = null;
    try {
      r = await (await fetch("/api/models/drill", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: m.id })
      })).json();
    } catch (e) {}
    if (!r || !r.ok) {
      var why = (r && r.error) ? r.error : "could not start the drill";
      try { if (typeof alert === "function") alert("Drill not started: " + why); } catch (e) {}
      return;
    }
    if (btn) { btn.disabled = true; btn.textContent = "drilling…"; }
    pollDrill();
  }

  function pollDrill() {
    var w = W(); if (!w) return;
    if (pollTimer) return;
    pollTimer = w.setInterval(async function () {
      var st = null;
      try { st = await (await fetch("/api/models/drill")).json(); } catch (e) {}
      if (st && !st.running) {
        w.clearInterval(pollTimer); pollTimer = null;
        await proFetch(true);
        redecorate();
        if (typeof w.loadModels === "function") { try { w.loadModels(); } catch (e) {} }
      }
    }, 5000);
  }

  function redecorate() {
    var d = D(); if (!d) return;
    var menu = d.getElementById("model-menu"); if (!menu) return;
    var rows = menu.querySelectorAll(".mmi[data-pro]");
    for (var i = 0; i < rows.length; i++) {
      rows[i].removeAttribute("data-pro");
      var old = rows[i].querySelector(".pro-line");
      if (old && old.parentNode) old.parentNode.removeChild(old);
    }
    decorate();
  }

  // ---- Mind view: Built with Llama attribution ---------------------------------
  function llamaAttribution() {
    var d = D(); if (!d || !PRO) return;
    var hasLlama = (PRO.models || []).some(function (m) {
      return /built with llama/i.test(m.license_note || "");
    });
    var ex = d.getElementById("pro-llama");
    if (!hasLlama) { if (ex && ex.parentNode) ex.parentNode.removeChild(ex); return; }
    if (ex) return;
    var hero = d.querySelector("#view-mind .greet");
    if (!hero) return;
    ensureStyle();
    var line = d.createElement("div");
    line.id = "pro-llama";
    line.textContent = "Built with Llama — this roster includes " +
      "Llama-family models (Llama 3.1 Community License attribution).";
    hero.appendChild(line);
  }

  // ---- hooks ---------------------------------------------------------------------
  var w = W();
  if (w) {
    // chain onto loadModels (pill click + our own calls resolve to this)
    var prevLoad = w.loadModels;
    w.loadModels = async function () {
      if (typeof prevLoad === "function") { try { await prevLoad(); } catch (e) {} }
      try { await proFetch(false); decorate(); } catch (e) {}
    };
    // chain onto mindExtras for the Mind-view attribution line
    var prevMX = w.mindExtras;
    w.mindExtras = async function () {
      if (typeof prevMX === "function") { try { await prevMX(); } catch (e) {} }
      try { await proFetch(false); llamaAttribution(); } catch (e) {}
    };
    // testable surface
    w.promotionGate = { decorate: decorate, redecorate: redecorate,
                        fetch: proFetch, badgeFor: badgeFor,
                        llama: llamaAttribution };
  }

  // the 30s poll in index.html captured the ORIGINAL loadModels reference, so
  // watch the menu for rebuilds and re-decorate (fresh rows carry no data-pro)
  var d = D();
  if (d && typeof MutationObserver === "function") {
    var menu = d.getElementById("model-menu");
    if (menu) {
      new MutationObserver(function () {
        try {
          if (menu.querySelector(".mmi:not([data-pro])")) {
            proFetch(false).then(decorate);
          }
        } catch (e) {}
      }).observe(menu, { childList: true });
    }
    // first paint (menu may already be populated)
    proFetch(false).then(function () { decorate(); llamaAttribution(); });
  }
})();
