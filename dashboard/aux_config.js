// aux_config.js — Config as Code: snapshot / restore panel (P1.6).
//
// Auto-served at /aux_config.js.  Loaded AFTER /expand.js so it can wrap the
// existing Mind-extras entry point (window.mindExtras) instead of editing
// index.html.  Renders one card (#mind-extra-config) into #view-mind: snapshot
// file status + per-section drift pills, an Export button (writes
// docs/state-snapshot.json), Preview restore (dry-run plan table) and Apply
// restore (confirm()-gated — works in the WKWebView JS-dialog fix).
//
// Reuses global helpers when present (esc, animate, REDUCE, renderHub) — all
// typeof-guarded so a headless render harness (canned fixtures) never throws.
// cfgPlanRows(plan) is a PURE, DOM-free function returning an HTML string, so it
// is unit-testable in node.  Zero emoji (bespoke SVG only), 12-hour time — per
// CLAUDE.md design laws.

(function () {
  "use strict";

  // ---- self-hook: chain onto the existing Mind-extras entry point ----------
  var prev = (typeof window !== "undefined") ? window.mindExtras : undefined;
  if (typeof window !== "undefined") {
    window.mindExtras = async function () {
      if (typeof prev === "function") { try { await prev(); } catch (e) {} }
      try { await configPanel(); } catch (e) {}
    };
  }

  // ---- tiny helpers --------------------------------------------------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
  function doc() { return (typeof document !== "undefined") ? document : null; }
  function RM() {
    if (typeof REDUCE !== "undefined") return !!REDUCE;
    try { return !!(window.matchMedia && matchMedia("(prefers-reduced-motion:reduce)").matches); }
    catch (e) { return false; }
  }
  function each(list, cb) {
    if (!list) return;
    try { Array.prototype.slice.call(list).forEach(cb); } catch (e) {}
  }
  function shortId(x) {
    x = String(x == null ? "" : x);
    var i = x.lastIndexOf("/");
    return i >= 0 ? x.slice(i + 1) : x;
  }
  function shortVal(v) {
    if (v == null) return "∅";                       // ∅
    if (typeof v === "object") { try { return JSON.stringify(v); } catch (e) { return String(v); } }
    return String(v);
  }

  // absolute 12-hour date/time, e.g. "Jul 5, 2:14 AM"
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmt12(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    var h = d.getHours(), m = d.getMinutes(), ap = h >= 12 ? "PM" : "AM";
    h = h % 12; if (h === 0) h = 12;
    return MON[d.getMonth()] + " " + d.getDate() + ", " + h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
  }
  function humanBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    return (n / 1024).toFixed(1) + " KB";
  }

  var ARCHIVE_SVG = '<svg class="ic cf-arch" viewBox="0 0 24 24" aria-hidden="true">' +
    '<rect x="3" y="7" width="18" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="1.4"/>' +
    '<path d="M2.5 4.5h19v3.2h-19z" fill="color-mix(in srgb,var(--iris) 34%,transparent)" ' +
    'stroke="currentColor" stroke-width="1.2"/>' +
    '<path d="M9.5 11.5h5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

  var DRIFT_LABEL = { layout: "Layout", settings: "Settings", models: "Models",
                      permissions: "Permissions", agent_config: "Agent config" };
  var DRIFT_ORDER = ["layout", "settings", "models", "permissions", "agent_config"];
  var DRIFT_TONE = { in_sync: "--ok", drifted: "--warn", missing: "--muted", invalid: "--bad" };
  var DRIFT_TEXT = { in_sync: "in sync", drifted: "drifted", missing: "no file", invalid: "invalid" };

  // ---- one-time CSS --------------------------------------------------------
  function injectCss() {
    var d = doc(); if (!d || d.getElementById("cfg-css")) return;
    var s = d.createElement("style");
    s.id = "cfg-css";
    s.textContent = [
      "#mind-extra-config .cf-arch{color:var(--muted)}",
      ".cf-file{display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--ink);",
      "padding:9px 11px;border-radius:11px;background:var(--glass-2);border:1px solid var(--hairline);margin-bottom:12px}",
      ".cf-file code{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:var(--ink)}",
      ".cf-file .cf-meta{color:var(--faint);margin-left:auto;white-space:nowrap}",
      ".cf-pills{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:14px}",
      ".cf-pill{font-size:11px;font-weight:560;padding:4px 10px;border-radius:99px;",
      "color:var(--pc);background:color-mix(in srgb,var(--pc) 15%,transparent);",
      "border:1px solid color-mix(in srgb,var(--pc) 32%,transparent);display:inline-flex;gap:6px;align-items:center}",
      ".cf-pill .cf-pt{opacity:.72;font-weight:400}",
      ".cf-band{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);",
      "margin:16px 0 8px;display:flex;align-items:center;gap:8px}",
      ".cf-band .cf-rule{flex:1;height:1px;background:var(--hairline)}",
      ".cf-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}",
      ".cf-note{flex:1;min-width:120px;background:var(--glass-2);border:1px solid var(--hairline);",
      "border-radius:9px;padding:7px 10px;color:var(--ink);font-size:12.5px;font-family:inherit}",
      ".cf-hint{font-size:12px;color:var(--muted);margin:8px 0 0}",
      ".cf-cmd{display:block;margin-top:8px;font-family:ui-monospace,Menlo,monospace;font-size:11px;",
      "color:var(--ink);background:var(--glass-2);border:1px solid var(--hairline);border-radius:9px;",
      "padding:8px 10px;cursor:pointer;overflow-x:auto;white-space:nowrap}",
      ".cf-msg{font-size:12px;margin-top:8px;min-height:0}",
      ".cf-msg.ok{color:var(--ok)}.cf-msg.bad{color:var(--bad)}",
      ".cf-plan{width:100%;border-collapse:collapse;margin-top:6px;font-size:12px}",
      ".cf-prow td{padding:6px 4px;border-bottom:1px solid var(--hairline);vertical-align:top}",
      ".cf-prow:last-child td{border-bottom:none}",
      ".cf-plabel{color:var(--ink);font-weight:560;white-space:nowrap;width:1%;padding-right:12px!important}",
      ".cf-pdetail{color:var(--muted);word-break:break-word}",
      ".cf-chg .cf-plabel{color:var(--warn)}",
      ".cf-warn .cf-plabel{color:var(--faint)}.cf-warn .cf-pdetail{color:var(--faint)}",
      ".cf-from{color:var(--faint)}.cf-to{color:var(--ink)}",
      ".cf-empty{font-size:12px;color:var(--muted);padding:8px 2px}",
      ".cf-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}",
      ".cf-chk{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--muted);margin-top:10px}",
      ".cf-chk input{accent-color:var(--iris)}",
      ".cf-skel{height:42px;border-radius:10px;margin:8px 0;",
      "background:linear-gradient(90deg,var(--glass-2),var(--glass),var(--glass-2));",
      "background-size:200% 100%;animation:cfsh 1.3s linear infinite}",
      "@keyframes cfsh{0%{background-position:200% 0}100%{background-position:-200% 0}}",
      "@media (prefers-reduced-motion:reduce){.cf-skel{animation:none}}",
    ].join("\n");
    (d.head || d.body || d.documentElement).appendChild(s);
  }

  // ---- card mount (replaces any existing instance) -------------------------
  function mount(grid, bodyHtml, tinyText) {
    var d = doc(); if (!d) return null;
    var old = d.getElementById("mind-extra-config");
    if (old && old.remove) old.remove();
    var s = d.createElement("section");
    s.className = "card glass span2";
    s.id = "mind-extra-config";
    s.innerHTML =
      "<h2>" + ARCHIVE_SVG + "Config as code" +
      '<span class="tiny" style="margin-left:auto">' + E(tinyText || "") + "</span></h2>" +
      '<div class="body">' + bodyHtml + "</div>";
    grid.appendChild(s);
    return s;
  }

  // ---- PURE plan renderer (DOM-free, harness-testable) ---------------------
  function planRow(label, detailHtml, changed) {
    return '<tr class="cf-prow' + (changed ? " cf-chg" : "") + '">' +
      '<td class="cf-plabel">' + E(label) + "</td>" +
      '<td class="cf-pdetail">' + detailHtml + "</td></tr>";
  }
  function warnRow(text) {
    return '<tr class="cf-prow cf-warn"><td class="cf-plabel">note</td>' +
      '<td class="cf-pdetail">' + E(text) + "</td></tr>";
  }
  function fromTo(a, b) {
    return '<span class="cf-from">' + E(shortVal(a)).slice(0, 160) + "</span> → " +
      '<span class="cf-to">' + E(shortVal(b)).slice(0, 160) + "</span>";
  }

  function cfgPlanRows(plan) {
    plan = plan || {};
    var rows = [], any = false;

    var L = plan.layout;
    if (L) {
      var bits = [];
      if (L.adds && L.adds.length) bits.push("+" + L.adds.length + " (" + L.adds.map(E).join(", ") + ")");
      if (L.removes && L.removes.length) bits.push("−" + L.removes.length + " (" + L.removes.map(E).join(", ") + ")");
      if (L.reordered) bits.push("reordered");
      if (L.changed) any = true;
      rows.push(planRow("Layout", E(bits.length ? bits.join(" · ") : (L.changed ? "changed" : "in sync")), !!L.changed));
      if (L.dropped_unknown && L.dropped_unknown.length)
        rows.push(warnRow("Unknown widgets dropped: " + L.dropped_unknown.join(", ")));
    }

    var S = plan.settings;
    if (S) {
      var ck = S.changed_keys || {}, keys = Object.keys(ck);
      if (keys.length) {
        any = true;
        keys.forEach(function (k) { rows.push(planRow("settings." + k, fromTo(ck[k].from, ck[k].to), true)); });
      } else {
        rows.push(planRow("Settings", "in sync", false));
      }
    }

    var M = plan.models;
    if (M) {
      var mbits = [], act = M.active || {};
      if (M.added && M.added.length) mbits.push("+" + M.added.length + " model" + (M.added.length === 1 ? "" : "s"));
      if (M.removed && M.removed.length) mbits.push("−" + M.removed.length);
      var actChanged = act.from !== act.to;
      if (actChanged) mbits.push("active " + shortId(act.from) + " → " + shortId(act.to) +
        (act.will_apply ? "" : " (roster only)"));
      var changedM = !!M.roster_changed || actChanged;
      if (changedM) any = true;
      rows.push(planRow("Models", E(mbits.length ? mbits.join(" · ") : "in sync"), changedM));
    }

    var P = plan.permissions;
    if (P) {
      if (P.changed) any = true;
      rows.push(planRow("Permissions", E(P.changed ? "policy differs" : "in sync"), !!P.changed));
    }

    var A = plan.agent_config;
    if (A) {
      var ch = A.changes || {}, ak = Object.keys(ch);
      if (ak.length) {
        any = true;
        ak.forEach(function (k) { rows.push(planRow(k, fromTo(ch[k].from, ch[k].to), true)); });
      } else {
        rows.push(planRow("Agent config", "in sync", false));
      }
      var vm = (A.verify_only || {})["approvals.mode"];
      rows.push(warnRow("approvals.mode verified = " + shortVal(vm) + " (never written)"));
    }

    if (!rows.length || !any)
      return '<div class="cf-empty">Snapshot matches live state — nothing to restore.</div>' +
        (rows.length ? '<table class="cf-plan">' + rows.join("") + "</table>" : "");
    return '<table class="cf-plan">' + rows.join("") + "</table>";
  }

  // ---- net -----------------------------------------------------------------
  async function getJSON(url) {
    var r = await fetch(url, { cache: "no-store" });
    return await r.json();
  }
  async function postJSON(url, body) {
    var r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    try { return await r.json(); } catch (e) { return { ok: false, error: "bad response" }; }
  }

  // ---- entry point ---------------------------------------------------------
  var _lastPlan = null, _lastApplyActive = false;

  async function configPanel() {
    var d = doc(); if (!d) return;
    var grid = d.getElementById("view-mind");
    if (!grid) return;
    injectCss();
    mount(grid, '<div class="cf-skel"></div><div class="cf-skel"></div><div class="cf-skel"></div>', "");

    var data;
    try { data = await getJSON("/api/config/snapshot"); }
    catch (e) { renderError(grid); return; }
    if (!data || data.ok === false) { renderError(grid, data && data.error); return; }
    renderData(grid, data);
  }

  function renderError(grid, msg) {
    var s = mount(grid,
      '<div class="cf-empty">Couldn’t load config snapshot' +
      (msg ? " (" + E(msg) + ")" : "") + ". " +
      '<button class="ghost" id="cf-retry" style="margin-left:6px">Retry</button></div>', "");
    if (!s) return;
    var b = s.querySelector("#cf-retry");
    if (b) b.addEventListener("click", function () { configPanel().catch(function () {}); });
  }

  function renderData(grid, data) {
    _lastPlan = null;
    var file = data.file || {}, drift = data.drift || {};
    var html = "";

    // status line
    if (file.exists) {
      html += '<div class="cf-file">' + ARCHIVE_SVG + "<code>" + E(file.path || "docs/state-snapshot.json") +
        "</code>" + (file.valid === false ? ' <span style="color:var(--bad)">invalid</span>' : "") +
        '<span class="cf-meta">' + humanBytes(file.bytes) +
        (file.exported_at ? " · exported " + E(fmt12(file.exported_at)) : "") + "</span></div>";
    } else {
      html += '<div class="cf-file">' + ARCHIVE_SVG +
        "<span>No snapshot yet — Export writes <code>docs/state-snapshot.json</code> so your layout, " +
        "watchlists, model roster and permission policy are versioned with the repo.</span></div>";
    }

    // drift pills
    html += '<div class="cf-pills">';
    DRIFT_ORDER.forEach(function (k) {
      var st = drift[k] || "missing", tone = DRIFT_TONE[st] || "--muted";
      html += '<span class="cf-pill" style="--pc:var(' + tone + ')">' + E(DRIFT_LABEL[k] || k) +
        '<span class="cf-pt">' + E(DRIFT_TEXT[st] || st) + "</span></span>";
    });
    html += "</div>";

    // export
    html += '<div class="cf-band">Export<span class="cf-rule"></span></div>' +
      '<div class="cf-row"><input class="cf-note" id="cf-note" maxlength="200" ' +
      'placeholder="Optional note (e.g. before Phase 1 tag)"/>' +
      '<button class="primary" id="cf-export">Export snapshot</button></div>' +
      '<div class="cf-msg" id="cf-exmsg"></div>';

    // restore
    html += '<div class="cf-band">Restore<span class="cf-rule"></span></div>' +
      '<div class="cf-actions"><button class="ghost" id="cf-preview"' +
      (file.exists ? "" : " disabled") + ">Preview restore</button>" +
      '<button class="primary" id="cf-apply" style="display:none">Apply restore</button></div>' +
      '<label class="cf-chk" id="cf-chk-wrap" style="display:none">' +
      '<input type="checkbox" id="cf-active"/> Also switch active model</label>' +
      '<div id="cf-plan"></div><div class="cf-msg" id="cf-rmsg"></div>';

    var s = mount(grid, html, snapshotTiny(drift));
    if (!s) return;
    wire(s, data);
    each(s.querySelectorAll(".cf-pill"), function (p, i) { pulse(p, i); });
  }

  function snapshotTiny(drift) {
    var syn = 0, tot = 0;
    DRIFT_ORDER.forEach(function (k) { tot++; if (drift[k] === "in_sync") syn++; });
    return syn + "/" + tot + " in sync";
  }

  function pulse(el, i) {
    if (RM() || typeof animate !== "function") return;
    try { animate(el, { transform: ["scale(.9)", "scale(1)"] }, { duration: 0.3, delay: i * 0.03, easing: "ease-out" }); }
    catch (e) {}
  }

  // ---- interactivity -------------------------------------------------------
  function wire(card, data) {
    var d = doc(); if (!d) return;
    var exBtn = card.querySelector("#cf-export");
    var exMsg = card.querySelector("#cf-exmsg");
    if (exBtn) exBtn.addEventListener("click", async function () {
      exBtn.disabled = true;
      if (exMsg) { exMsg.className = "cf-msg"; exMsg.textContent = "Exporting…"; }
      var note = (card.querySelector("#cf-note") || {}).value || "";
      var res;
      try { res = await postJSON("/api/config/export", { note: note }); }
      catch (e) { res = { ok: false, error: "network error" }; }
      exBtn.disabled = false;
      if (!res || res.ok === false) {
        if (exMsg) { exMsg.className = "cf-msg bad"; exMsg.textContent = (res && res.error) || "Export failed."; }
        return;
      }
      if (exMsg) {
        exMsg.className = "cf-msg ok";
        exMsg.innerHTML = "Wrote " + E(res.path) + " (" + humanBytes(res.bytes) + "). " +
          "Commit it to version this state:" +
          '<code class="cf-cmd" title="Click to copy">git add ' + E(res.path) +
          ' &amp;&amp; git commit -m "ops: state snapshot"</code>';
        var cmd = exMsg.querySelector(".cf-cmd");
        if (cmd) cmd.addEventListener("click", function () { copyText(cmd.textContent); });
      }
      configPanel().catch(function () {});                // refresh drift
    });

    var pvBtn = card.querySelector("#cf-preview");
    var applyBtn = card.querySelector("#cf-apply");
    var chkWrap = card.querySelector("#cf-chk-wrap");
    var activeChk = card.querySelector("#cf-active");
    var planBox = card.querySelector("#cf-plan");
    var rMsg = card.querySelector("#cf-rmsg");

    if (pvBtn) pvBtn.addEventListener("click", async function () {
      pvBtn.disabled = true;
      if (rMsg) { rMsg.className = "cf-msg"; rMsg.textContent = ""; }
      if (planBox) planBox.innerHTML = '<div class="cf-skel"></div>';
      var res;
      try { res = await postJSON("/api/config/import", { dry_run: true }); }
      catch (e) { res = { ok: false, error: "network error" }; }
      pvBtn.disabled = false;
      if (!res || res.ok === false) {
        if (planBox) planBox.innerHTML = "";
        if (rMsg) { rMsg.className = "cf-msg bad"; rMsg.textContent = (res && res.error) || "Preview failed."; }
        return;
      }
      _lastPlan = res.plan || {};
      if (planBox) planBox.innerHTML = cfgPlanRows(_lastPlan);
      var pm = (_lastPlan.models && _lastPlan.models.active) || {};
      var canSwitch = pm.from !== pm.to;
      var downloaded = pm.downloaded !== false;
      if (chkWrap) chkWrap.style.display = canSwitch ? "flex" : "none";
      if (activeChk) {
        activeChk.disabled = !downloaded;
        if (!downloaded && chkWrap) chkWrap.title = "Target model isn’t downloaded";
      }
      var hasChange = planHasChange(_lastPlan);
      if (applyBtn) applyBtn.style.display = hasChange ? "" : "none";
      staggerRows(planBox);
    });

    if (applyBtn) applyBtn.addEventListener("click", async function () {
      var when = (data.file && data.file.exported_at) ? fmt12(data.file.exported_at) : "the snapshot";
      if (typeof confirm === "function" &&
          !confirm("Restore snapshot from " + when + "? A pre-restore backup will be kept.")) return;
      applyBtn.disabled = true;
      if (rMsg) { rMsg.className = "cf-msg"; rMsg.textContent = "Applying…"; }
      var body = { dry_run: false };
      if (activeChk && activeChk.checked && !activeChk.disabled) body.apply_active_model = true;
      var res;
      try { res = await postJSON("/api/config/import", body); }
      catch (e) { res = { ok: false, error: "network error" }; }
      applyBtn.disabled = false;
      if (!res || res.ok === false) {
        if (rMsg) { rMsg.className = "cf-msg bad"; rMsg.textContent = (res && res.error) || "Restore failed."; }
        return;
      }
      if (rMsg) { rMsg.className = "cf-msg ok"; rMsg.textContent = applySummary(res); }
      if (typeof renderHub === "function") { try { renderHub(); } catch (e) {} }
      if (typeof loadModels === "function") { try { loadModels(); } catch (e) {} }
      setTimeout(function () { configPanel().catch(function () {}); }, 700);
    });
  }

  function planHasChange(plan) {
    if (!plan) return false;
    if (plan.layout && plan.layout.changed) return true;
    if (plan.settings && Object.keys(plan.settings.changed_keys || {}).length) return true;
    if (plan.models && (plan.models.roster_changed ||
        (plan.models.active && plan.models.active.from !== plan.models.active.to))) return true;
    if (plan.permissions && plan.permissions.changed) return true;
    if (plan.agent_config && Object.keys(plan.agent_config.changes || {}).length) return true;
    return false;
  }

  function applySummary(res) {
    var a = res.applied || {}, parts = [];
    if (a.layout) parts.push("layout");
    if (a.settings && a.settings.length) parts.push("settings (" + a.settings.join(", ") + ")");
    if (a.models) parts.push("models");
    if (a.active_model && a.active_model !== "skipped") parts.push("active: " + a.active_model);
    if (a.agent_config && a.agent_config.length) parts.push("agent config");
    if (a.permissions) parts.push("permissions");
    var msg = parts.length ? "Restored " + parts.join(" · ") + "." : "Nothing to restore.";
    if (res.warnings && res.warnings.length) msg += " (" + res.warnings.join("; ") + ")";
    if (res.backup) msg += " Backup: " + res.backup;
    return msg;
  }

  function staggerRows(box) {
    if (!box || RM() || typeof animate !== "function") return;
    each(box.querySelectorAll(".cf-prow"), function (r, i) {
      try { animate(r, { opacity: [0, 1], transform: ["translateY(6px)", "translateY(0)"] },
        { duration: 0.24, delay: i * 0.03, easing: "ease-out" }); } catch (e) {}
    });
  }

  function copyText(t) {
    try {
      if (navigator && navigator.clipboard) { navigator.clipboard.writeText(t); return; }
    } catch (e) {}
    try {
      var d = doc(); if (!d) return;
      var ta = d.createElement("textarea"); ta.value = t; d.body.appendChild(ta);
      ta.select(); d.execCommand("copy"); d.body.removeChild(ta);
    } catch (e) {}
  }

  // ---- expose for the headless render harness / manual invocation ----------
  if (typeof window !== "undefined") { window.configPanel = configPanel; window.cfgPlanRows = cfgPlanRows; }
  if (typeof globalThis !== "undefined") { globalThis.configPanel = configPanel; globalThis.cfgPlanRows = cfgPlanRows; }
})();
