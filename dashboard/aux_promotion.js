// aux_promotion.js — Model Promotion Gate UI (P3.2 B2).
//
// Auto-served at /aux_promotion.js (the /aux_*.js static rule). Loaded after
// index.html's inline scripts, so it wraps window.loadModels the way the
// other aux JS wraps window.mindExtras — ZERO index.html edits beyond the
// one script tag.
//
// What it adds to the MODEL MENU (#model-menu, rebuilt by loadModels()), in
// each row's Details panel (the .mmdd <dl> sibling index.html builds; the row
// itself shows ONE verification state on its title line — climb round 3):
//   * "Drill": the record  ✓ drilled 6/6  /  ✗ failed 2/6  /  — undrilled
//   * a per-model "Drill" / "Re-drill" button (POST /api/models/drill, polls)
//   * "Licence": the per-model license note (Apache-2.0, "Built with Llama", …)
// index.html keeps a row's Details open across the 30 s rebuild (mmOpen), so a
// drill started there stays in view.
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
      ".pro-line{display:flex;align-items:center;gap:7px;margin-top:3px;font-size:12px;line-height:1.35}" +
      ".pro-badge{font-weight:600;white-space:nowrap}" +
      ".pro-badge.pass{color:var(--ok)}" +
      ".pro-badge.fail{color:var(--bad)}" +
      ".pro-badge.none{color:var(--muted)}" +
      ".pro-badge.busy{color:var(--iris)}" +
      ".pro-lic{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}" +
      // 26px plate (was ~20px), 36px target: the ::after grows 5px into the
      // row's 9px padding and 4px into the 10px gap before the action word
      ".pro-drill{margin-left:auto;flex:none;font-size:12px;font-weight:600;min-height:26px;padding:4px 10px;" +
      "border-radius:8px;justify-content:center;position:relative;" +
      "border:1px solid var(--hairline);background:var(--chip);color:var(--muted);cursor:pointer}" +
      ".pro-drill::after{content:'';position:absolute;inset:-5px -4px}" +
      ".pro-drill:hover{color:var(--ink);border-color:var(--iris)}" +
      "#pro-llama{margin-top:8px;font-size:12px;color:var(--muted)}";
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
      // the row's Details panel: the <dl> sibling right after it
      var dl = el.nextElementSibling;
      if (!dl || !dl.classList || !dl.classList.contains("mmdd")) dl = null;
      var b = badgeFor(m);
      var html = '<dt class="pro-dt">Drill</dt><dd class="pro-dd pro-line">' +
                 '<span class="pro-badge ' + b.cls + '">' + E(b.txt) + "</span>";
      // Drill affordance: downloaded models only; hidden while any drill runs
      if (m.downloaded && !PRO.drill_running) {
        html += '<button class="pro-drill" type="button">' +
                (m.drilled ? "Re-drill" : "Drill") + "</button>";
      }
      html += "</dd>";
      if (m.license_note) html += '<dt class="pro-dt">Licence</dt><dd class="pro-dd pro-lic">' + E(m.license_note) + "</dd>";
      var tmp = d.createElement("dl");
      tmp.innerHTML = html;
      var btn = tmp.querySelector(".pro-drill");
      if (btn) btn.onclick = function (ev) {
        if (ev && ev.stopPropagation) ev.stopPropagation();
        startDrill(m, btn);
      };
      var host = dl || el;
      while (tmp.firstChild) host.appendChild(tmp.firstChild);
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

  // E119: what the owner must be told when a drill ends — "" when nothing.
  // A finished drill shows in its badge; an aborted run (no verdict) and a
  // restore that failed or was left alone (Pause, the owner's own switch, a
  // dashboard restart) showed nowhere, and a failed restore leaves Telegram
  // and the hub on the drilled model.
  function short(x) { return String(x == null ? "" : x).replace(/\s+/g, " ").slice(0, 160); }
  function nameOf(id) { var s = String(id || ""); return s.split("/").pop() || s; }
  function drillOutcome(st) {
    var last = st && st.last;
    if (!last || typeof last !== "object") return "";
    var rs = (last.restore && typeof last.restore === "object") ? last.restore : null;
    var who = nameOf(last.model);
    var orig = rs ? nameOf(rs.model) : "";
    var parts = [];
    if (last.error) parts.push("Drill of " + who + " stopped — no verdict stored: " + short(last.error) + ".");
    if (rs) {
      if (rs.ok === false) {
        parts.push("Could not switch back to " + orig + (rs.error ? " (" + short(rs.error) + ")" : "") +
                   " — pick it in the model menu.");
      } else if (rs.skipped === "paused") {
        parts.push("The agent was paused, so it stays paused; " + orig + " loads on Resume.");
      } else if (rs.skipped === "asleep" || rs.skipped === "down") {
        parts.push(orig + " is the assistant's model again; it loads on the next start.");
      } else if (rs.skipped === "switched") {
        parts.push("You switched to " + nameOf(rs.active) + " during the drill — left as you set it.");
      }
      if (rs.after_restart && !last.error) parts.unshift("The dashboard restarted during a drill.");
    }
    return parts.join(" ");
  }

  function say(msg) {
    if (!msg) return;
    var w = W();
    var t = (w && typeof w.toast === "function") ? w.toast
          : (typeof toast === "function" ? toast : null);
    if (t) { try { t(msg, 9000); return; } catch (e) {} }
    try { if (typeof alert === "function") alert(msg); } catch (e) {}
  }

  function pollDrill() {
    var w = W(); if (!w) return;
    if (pollTimer) return;
    // review R4 (E119): a dashboard restarted mid-drill answers running:null
    // with no `last` until its boot recovery has put the model back, so a
    // result counts only once it is from this drill (finished after the poll
    // began); with none after 15 minutes the owner is told so.
    var since = Date.now() / 1000 - 5;
    pollTimer = w.setInterval(async function () {
      var st = null;
      try { st = await (await fetch("/api/models/drill")).json(); } catch (e) {}
      if (st && !st.running) {
        var last = st.last;
        var mine = last && typeof last === "object" &&
                   !(typeof last.finished === "number" && last.finished < since);
        var gaveUp = !mine && Date.now() / 1000 - since > 900;
        if (!mine && !gaveUp) return;
        w.clearInterval(pollTimer); pollTimer = null;
        try {
          say(mine ? drillOutcome(st)
                   : "The drill did not report a result (the dashboard may have restarted) — " +
                     "no verdict stored. Check the model menu for the active model.");
        } catch (e) {}
        await proFetch(true);
        redecorate();
        if (typeof w.loadModels === "function") { try { w.loadModels(); } catch (e) {} }
      }
    }, 5000);
  }

  // A drill the previous dashboard process did not finish is restored at the
  // next start with nobody polling: say so once per page life when it is fresh.
  async function restartNotice() {
    var st = null;
    try { st = await (await fetch("/api/models/drill")).json(); } catch (e) { return ""; }
    var last = st && st.last, rs = last && last.restore;
    if (!rs || !rs.after_restart || st.running) return "";
    if (!last.finished || Date.now() / 1000 - last.finished > 900) return "";
    var msg = drillOutcome(st);
    say(msg);
    return msg;
  }

  function redecorate() {
    var d = D(); if (!d) return;
    var menu = d.getElementById("model-menu"); if (!menu) return;
    var rows = menu.querySelectorAll(".mmi[data-pro]");
    for (var i = 0; i < rows.length; i++) {
      rows[i].removeAttribute("data-pro");
      var dl = rows[i].nextElementSibling;
      var host = (dl && dl.classList && dl.classList.contains("mmdd")) ? dl : rows[i];
      var old = host.querySelectorAll(".pro-dt,.pro-dd");
      for (var j = 0; j < old.length; j++) if (old[j].parentNode) old[j].parentNode.removeChild(old[j]);
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
                        llama: llamaAttribution, outcome: drillOutcome,
                        poll: pollDrill, restartNotice: restartNotice };
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
            // decorate at once from the payload already held, so an open
            // Details panel never shows a frame without its Drill row, then
            // again once a fresh payload is in (only when it changed)
            var before = PRO;
            decorate();
            proFetch(false).then(function () { if (PRO !== before) redecorate(); else decorate(); });
          }
        } catch (e) {}
      }).observe(menu, { childList: true });
    }
    // first paint (menu may already be populated)
    proFetch(false).then(function () { decorate(); llamaAttribution(); });
    restartNotice();
  }
})();
