// aux_shortcuts.js — Shortcuts action-bus card (P3.1).
//
// Auto-served at /aux_shortcuts.js.  Loaded AFTER /expand.js so it chains the
// Mind-extras entry point (window.mindExtras) — no index.html surgery beyond
// the one <script> tag.  Renders one card (#mind-extra-shortcuts) into
// #view-mind: every installed macOS Shortcut with an expose toggle + risk
// chip, pending agent-requested approvals (Approve / Deny), a per-shortcut
// Run button that flows through the SAME gated endpoint the agent uses
// (needs_approval -> confirm() -> single-use ticket), and recent bus runs
// from the flight recorder.
//
// Reuses index.html globals esc(), animate() (Motion One), revealStagger(),
// REDUCE — all typeof-guarded so a headless node harness never throws.
// Zero emoji, bespoke SVG only, 12-hour time — per CLAUDE.md design laws.

(function () {
  "use strict";

  // ---- self-hook: chain onto the existing Mind-extras entry point ----------
  var prev = window.mindExtras;
  window.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await shortcutsPanel(); } catch (e) {}
  };

  // ---- tiny helpers ---------------------------------------------------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
  function doc() { return (typeof document !== "undefined") ? document : null; }
  function each(list, cb) {
    if (!list) return;
    try { Array.prototype.slice.call(list).forEach(cb); } catch (e) {}
  }
  function t12(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) return "";
    var d = new Date(n * 1000), h = d.getHours(), m = d.getMinutes();
    var ap = h >= 12 ? "PM" : "AM";
    h = h % 12; if (h === 0) h = 12;
    return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
  }
  async function postJSON(url, body) {
    var r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    try { return await r.json(); } catch (e) { return { ok: false, error: "bad response" }; }
  }

  // risk chips: high = red (sends / deletes), med = amber, unknown = iris,
  // low = green.  Different scale than the Trust panel on purpose: "high"
  // here means "sends messages" and deserves the strongest color.
  var RISK_ACC = { high: "var(--bad)", med: "var(--warn)", unknown: "var(--iris)", low: "var(--ok)" };
  var RISK_TXT = { high: "High", med: "Medium", unknown: "Unknown", low: "Low" };

  var BOLT_SVG = '<svg class="ic sb-bolt" viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M13 2 5 13.4h4.6L8.4 22 19 10.2h-5.2z" ' +
    'fill="color-mix(in srgb,var(--iris) 22%,transparent)" stroke="currentColor" ' +
    'stroke-width="1.4" stroke-linejoin="round"/></svg>';
  var PLAY_SVG = '<svg viewBox="0 0 24 24" width="10" height="10" aria-hidden="true" style="margin-right:4px">' +
    '<path d="M7 4.8v14.4L19.2 12z" fill="currentColor"/></svg>';

  var STATUS_BADGE = {
    done: ["Ran", "--ok"], pending: ["Waiting", "--warn"], denied: ["Denied", "--bad"],
    blocked: ["Blocked", "--bad"], expired: ["Expired", "--warn"], error: ["Error", "--bad"],
  };

  // ---- one-time CSS ----------------------------------------------------------
  function injectCss() {
    var d = doc(); if (!d || d.getElementById("shortcuts-css")) return;
    var s = d.createElement("style");
    s.id = "shortcuts-css";
    s.textContent = [
      "#mind-extra-shortcuts .sb-bolt{color:var(--muted)}",
      ".sb-hint{font-size:12px;color:var(--muted);margin:-2px 0 10px}",
      ".sb-pend{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:12px;margin:0 0 10px;",
      "background:color-mix(in srgb,var(--warn) 13%,transparent);border:1px solid color-mix(in srgb,var(--warn) 38%,transparent)}",
      ".sb-pend .sb-ptxt{flex:1;font-size:12.5px;color:var(--ink)}",
      ".sb-pend .sb-ptxt b{color:var(--warn)}",
      ".sb-row{display:flex;align-items:center;gap:12px;padding:9px 2px;border-bottom:1px solid var(--hairline)}",
      ".sb-row:last-child{border-bottom:none}",
      ".sb-chip{flex:0 0 auto;font-size:10px;font-weight:640;letter-spacing:.03em;padding:3px 8px;border-radius:99px;",
      "color:var(--rc);background:color-mix(in srgb,var(--rc) 15%,transparent);border:1px solid color-mix(in srgb,var(--rc) 32%,transparent)}",
      ".sb-main{flex:1;min-width:0}",
      ".sb-name{font-size:13px;font-weight:560;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".sb-desc{color:var(--muted);margin-top:1px}",
      ".sb-last{color:var(--faint);margin-top:2px}",
      ".sb-run{flex:0 0 auto;display:inline-flex;align-items:center;font-size:11px;font-weight:600;padding:4px 10px;",
      "border-radius:8px;border:1px solid var(--hairline);background:var(--glass-2);color:var(--ink);cursor:pointer}",
      ".sb-run:disabled{opacity:.4;cursor:not-allowed}",
      ".sb-tog{position:relative;flex:0 0 auto;width:34px;height:20px;border-radius:99px;cursor:pointer;",
      "background:var(--glass-2);border:1px solid var(--hairline);transition:background .18s}",
      ".sb-tog .sb-knob{position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;",
      "background:var(--muted);transition:transform .18s cubic-bezier(.22,.61,.36,1),background .18s}",
      ".sb-tog.on{background:color-mix(in srgb,var(--ok) 26%,transparent);border-color:color-mix(in srgb,var(--ok) 45%,transparent)}",
      ".sb-tog.on .sb-knob{transform:translateX(14px);background:var(--ok)}",
      ".sb-status{font-size:11px;margin:2px 0 4px;color:var(--muted);white-space:pre-wrap;word-break:break-word}",
      ".sb-status.err{color:var(--bad)}",
      ".sb-sub{font-size:11px;font-weight:600;color:var(--muted);margin:14px 0 6px;letter-spacing:.02em}",
      ".sb-drow{display:flex;align-items:center;gap:9px;padding:5px 2px;font-size:12px}",
      ".sb-dtime{flex:0 0 auto;color:var(--faint);font-variant-numeric:tabular-nums;width:64px}",
      ".sb-badge{flex:0 0 auto;font-size:9.5px;font-weight:640;text-transform:uppercase;letter-spacing:.03em;",
      "padding:2px 6px;border-radius:5px;color:var(--bd);background:color-mix(in srgb,var(--bd) 16%,transparent)}",
      ".sb-dname{flex:0 0 auto;color:var(--ink);max-width:34%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".sb-dsum{flex:1;min-width:0;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".sb-empty{font-size:12px;color:var(--muted);padding:8px 2px}",
      ".sb-skel{height:44px;border-radius:10px;margin:8px 0;",
      "background:linear-gradient(90deg,var(--glass-2),var(--glass),var(--glass-2));",
      "background-size:200% 100%;animation:sbsh 1.3s linear infinite}",
      "@keyframes sbsh{0%{background-position:200% 0}100%{background-position:-200% 0}}",
      "@media (prefers-reduced-motion:reduce){.sb-skel{animation:none}.sb-tog .sb-knob{transition:none}}",
    ].join("\n");
    (d.head || d.body || d.documentElement).appendChild(s);
  }

  // ---- card mount ------------------------------------------------------------
  function mount(grid, bodyHtml, tinyText) {
    var d = doc(); if (!d) return null;
    var old = d.getElementById("mind-extra-shortcuts");
    if (old && old.remove) old.remove();
    var s = d.createElement("section");
    s.className = "card glass span2";
    s.id = "mind-extra-shortcuts";
    s.innerHTML =
      "<h2>" + BOLT_SVG + "Shortcuts" +
      '<span class="tiny" style="margin-left:auto">' + E(tinyText || "") + "</span></h2>" +
      '<div class="body">' + bodyHtml + "</div>";
    grid.appendChild(s);
    return s;
  }

  // ---- entry point -----------------------------------------------------------
  async function shortcutsPanel() {
    var d = doc(); if (!d) return;
    var grid = d.getElementById("view-mind");
    if (!grid) return;
    injectCss();
    mount(grid, '<div class="sb-skel"></div><div class="sb-skel"></div>', "");
    await refresh(grid);
    armPoll();
  }

  async function refresh(grid) {
    var data;
    try {
      var r = await fetch("/api/shortcuts", { cache: "no-store" });
      data = await r.json();
    } catch (e) { renderError(grid); return; }
    if (!data || data.ok === false) { renderError(grid); return; }
    renderData(grid, data);
  }

  function renderError(grid) {
    var s = mount(grid,
      '<div class="sb-empty">Couldn’t load Shortcuts. ' +
      '<button class="ghost" id="sb-retry" style="margin-left:6px">Retry</button></div>', "");
    if (!s) return;
    var b = s.querySelector("#sb-retry");
    if (b) b.addEventListener("click", function () { shortcutsPanel().catch(function () {}); });
  }

  // ---- render ----------------------------------------------------------------
  function pendingSig(data) {
    return ((data && data.pending) || []).map(function (p) { return p.ticket; }).join(",");
  }

  function renderData(grid, data) {
    var list = (data && data.shortcuts) || [];
    var pending = (data && data.pending) || [];
    var tiny = data.available === false ? "unavailable"
      : (data.exposed_count || 0) + " of " + list.length + " exposed";
    var html = "";

    if (data.available === false) {
      html += '<div class="sb-empty">The macOS <code>shortcuts</code> CLI is not available' +
        (data.error ? " (" + E(data.error) + ")" : "") + ".</div>";
      mount(grid, html, tiny);
      return;
    }

    html += '<div class="sb-hint">Nothing is agent-runnable until you expose it here. ' +
      "Every run — yours or the agent’s — asks for your approval first.</div>";

    pending.forEach(function (p) {
      html += '<div class="sb-pend" data-ticket="' + E(p.ticket) + '">' +
        '<span class="sb-ptxt"><b>Approval needed:</b> run “' + E(p.name) + "” " +
        "(from " + E(p.source || "agent") + ", expires in " + (p.expires_in || 0) + "s)</span>" +
        '<button class="primary sb-ok">Approve</button>' +
        '<button class="ghost sb-no">Deny</button></div>';
    });

    if (!list.length) {
      html += '<div class="sb-empty">No Shortcuts installed.</div>';
    }
    list.forEach(function (s, i) {
      var risk = s.risk || {};
      var acc = RISK_ACC[risk.level] || "var(--iris)";
      var meta = risk.label || "";
      if (s.missing) meta = "No longer installed" + (meta ? " · " + meta : "");
      var last = s.last_run
        ? "Last: " + t12(s.last_run.ts) + " · " + (s.last_run.status || "") : "";
      html += '<div class="sb-row" data-id="' + E(s.id) + '" data-name="' + E(s.name) + '">' +
        '<span class="sb-chip" style="--rc:' + acc + '">' + E(RISK_TXT[risk.level] || "?") + "</span>" +
        '<div class="sb-main">' +
        '<div class="sb-name">' + E(s.name) + "</div>" +
        '<div class="sb-desc tiny">' + E(meta) + (s.exposed ? " · tier: always ask" : "") + "</div>" +
        (last ? '<div class="sb-last tiny">' + E(last) + "</div>" : "") +
        '<div class="sb-status" data-st="' + E(s.id) + '"></div>' +
        "</div>" +
        (s.exposed && !s.missing
          ? '<button class="sb-run" title="Runs through the gated bus — you will confirm first">' + PLAY_SVG + "Run</button>"
          : "") +
        '<span class="sb-tog' + (s.exposed ? " on" : "") + '" role="switch" aria-checked="' +
        (s.exposed ? "true" : "false") + '" title="' +
        (s.exposed ? "Exposed to the agent (click to remove)" : "Not exposed (click to expose)") +
        '"><span class="sb-knob"></span></span>' +
        "</div>";
    });

    html += '<div class="sb-sub">Recent bus activity</div>' + recentList(data.recent || []);
    html += '<div class="sb-hint" style="margin:12px 0 0">The agent can only request runs through ' +
      "this bus — requests appear above for your approval. Raw <code>shortcuts run</code> in a " +
      "terminal is outside this gate (see FINDINGS).</div>";

    var card = mount(grid, html, tiny);
    if (!card) return;
    card.setAttribute("data-pending", pendingSig(data));
    wire(card, grid);
    try {
      if (typeof revealStagger === "function") revealStagger(card.querySelectorAll(".sb-row"), 30);
    } catch (e) {}
  }

  function recentList(recent) {
    if (!recent || !recent.length) {
      return '<div class="sb-empty">No bus runs yet — every attempt lands here and in the flight recorder.</div>';
    }
    return recent.slice(0, 8).map(function (r) {
      var meta = STATUS_BADGE[r.status] || [r.status || "", "--muted"];
      return '<div class="sb-drow">' +
        '<span class="sb-dtime">' + E(t12(r.ts)) + "</span>" +
        '<span class="sb-badge" style="--bd:var(' + meta[1] + ')">' + E(meta[0]) + "</span>" +
        '<span class="sb-dname">' + E(r.name || "") + "</span>" +
        '<span class="sb-dsum">' + E(r.summary || "") + "</span></div>";
    }).join("");
  }

  // ---- interactivity ---------------------------------------------------------
  function setStatus(card, id, text, isErr) {
    var el = card.querySelector('.sb-status[data-st="' + id + '"]');
    if (!el) return;
    el.textContent = text || "";
    el.className = "sb-status" + (isErr ? " err" : "");
  }

  function wire(card, grid) {
    // expose / unexpose toggles
    each(card.querySelectorAll(".sb-row"), function (row) {
      var id = row.getAttribute("data-id");
      var name = row.getAttribute("data-name");
      var tog = row.querySelector(".sb-tog");
      if (tog) tog.addEventListener("click", async function () {
        var want = !tog.classList.contains("on");
        if (want && typeof confirm === "function") {
          if (!confirm('Expose “' + name + '” to the agent?\n\nIt still cannot run ' +
            "silently — every run will ask for your approval.")) return;
        }
        tog.classList.toggle("on", want);
        var res = await postJSON("/api/shortcuts/config", { id: id, exposed: want, source: "ui" });
        if (!res || res.ok === false) {
          tog.classList.toggle("on", !want);
          setStatus(card, id, (res && res.error) || "Couldn’t save.", true);
          return;
        }
        refresh(grid).catch(function () {});
      });

      // gated run button (same endpoint + ticket flow the agent uses)
      var btn = row.querySelector(".sb-run");
      if (btn) btn.addEventListener("click", async function () {
        btn.disabled = true;
        setStatus(card, id, "Requesting…");
        var res;
        try { res = await postJSON("/api/shortcuts/run", { id: id, source: "ui" }); }
        catch (e) { res = { ok: false, error: "network error" }; }
        if (res && res.needs_approval && res.ticket) {
          var ok = (typeof confirm === "function") &&
            confirm('Run “' + name + '” now?\n\nPermission tier: ask — nothing runs without this confirmation.');
          setStatus(card, id, ok ? "Running…" : "Denying…");
          try { res = await postJSON("/api/shortcuts/run", { ticket: res.ticket, approved: !!ok, source: "ui" }); }
          catch (e) { res = { ok: false, error: "network error" }; }
        }
        btn.disabled = false;
        showResult(card, grid, id, res);
      });
    });

    // pending agent-requested approvals
    each(card.querySelectorAll(".sb-pend"), function (p) {
      var ticket = p.getAttribute("data-ticket");
      var okB = p.querySelector(".sb-ok"), noB = p.querySelector(".sb-no");
      async function answer(approved) {
        if (approved && typeof confirm === "function") {
          var nm = (p.textContent || "").trim();
          if (!confirm("Approve this Shortcut run?\n\n" + nm)) return;
        }
        if (okB) okB.disabled = true;
        if (noB) noB.disabled = true;
        try { await postJSON("/api/shortcuts/run", { ticket: ticket, approved: approved, source: "ui" }); }
        catch (e) {}
        refresh(grid).catch(function () {});
      }
      if (okB) okB.addEventListener("click", function () { answer(true); });
      if (noB) noB.addEventListener("click", function () { answer(false); });
    });
  }

  function showResult(card, grid, id, res) {
    if (!res) { setStatus(card, id, "No response.", true); return; }
    if (res.status === "done" || (res.ok && !res.needs_approval && res.status !== "denied")) {
      var out = (res.output || "").trim();
      setStatus(card, id, "Ran in " + (res.duration_s || 0) + "s" +
        (out ? " · " + out.slice(0, 300) : ""), false);
    } else if (res.status === "denied") {
      setStatus(card, id, "Denied — nothing was run.", false);
    } else {
      setStatus(card, id, (res.error || res.message || "Failed.").slice(0, 300), true);
    }
    setTimeout(function () { refresh(grid).catch(function () {}); }, 1200);
  }

  // ---- light poll: surface agent-requested approvals while the card is visible
  var pollTimer = null;
  function armPoll() {
    var d = doc(); if (!d) return;
    if (pollTimer) return;
    pollTimer = setInterval(async function () {
      var card = d.getElementById("mind-extra-shortcuts");
      if (!card || !card.isConnected) { clearInterval(pollTimer); pollTimer = null; return; }
      if (card.offsetParent === null) return;      // Mind view not visible
      var data;
      try { data = await (await fetch("/api/shortcuts", { cache: "no-store" })).json(); }
      catch (e) { return; }
      if (!data || data.ok === false) return;
      if (pendingSig(data) !== (card.getAttribute("data-pending") || "")) {
        var grid = d.getElementById("view-mind");
        if (grid) renderData(grid, data);
      }
    }, 10000);
  }

  // expose for the headless render harness / manual invocation
  window.shortcutsPanel = shortcutsPanel;
})();
