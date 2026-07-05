// aux_google.js — Google connection card (P2.5, safety-narrowed).
//
// Auto-served at /aux_google.js.  Chains onto window.mindExtras (same pattern
// as aux_trust.js) and renders one card (#mind-extra-google) into #view-mind:
// a status chip (Not connected / Connected · read-only) plus a 3-step connect
// wizard (paste client_secret JSON → open Google consent → paste the redirect
// URL back).  Talks only to the local /api/google/* routes; the pasted client
// secret goes straight to the backend and is never re-displayed.
//
// Reuses index.html globals esc(), animate() (Motion One), REDUCE — all
// typeof-guarded so a headless harness never throws.  Zero emoji, bespoke SVG
// only, 12-hour time — per CLAUDE.md design laws.

(function () {
  "use strict";

  // ---- self-hook: chain onto the existing Mind-extras entry point ----------
  var prev = window.mindExtras;
  window.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await googlePanel(); } catch (e) {}
  };

  // ---- wizard state (survives re-renders; never holds secret bytes) --------
  var GW = { open: false, step: 1, authUrl: "", err: "", lastSt: null };

  // ---- tiny helpers --------------------------------------------------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
  function doc() { return (typeof document !== "undefined") ? document : null; }
  function RM() {
    if (typeof REDUCE !== "undefined") return !!REDUCE;
    try { return !!(window.matchMedia && matchMedia("(prefers-reduced-motion:reduce)").matches); }
    catch (e) { return false; }
  }
  function t12(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) return "";
    var d = new Date(n * 1000), h = d.getHours(), m = d.getMinutes();
    var ap = h >= 12 ? "PM" : "AM";
    h = h % 12; if (h === 0) h = 12;
    return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
  }
  function slideIn(el) {
    if (!el || RM() || typeof animate !== "function") return;
    try {
      animate(el, { opacity: [0, 1], transform: ["translateY(7px)", "translateY(0)"] },
        { duration: 0.28, easing: [0.22, 0.61, 0.36, 1] });
    } catch (e) {}
  }

  // ---- bespoke SVG (accent fill + currentColor stroke; no emoji) -----------
  var G_MARK = '<svg class="ic gc-mark" viewBox="0 0 24 24" aria-hidden="true">' +
    '<rect x="3" y="5" width="18" height="15" rx="3" ' +
    'fill="color-mix(in srgb,var(--iris) 20%,transparent)" stroke="currentColor" stroke-width="1.4"/>' +
    '<path d="M3.6 7.2 12 13.4l8.4-6.2" fill="none" stroke="var(--iris)" ' +
    'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<path d="M8 2.8v3.4M16 2.8v3.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
  var SHIELD_SVG = '<svg class="gc-sh" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">' +
    '<path d="M12 2 4 5v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V5z" ' +
    'fill="color-mix(in srgb,var(--ok) 20%,transparent)" stroke="currentColor" stroke-width="1.4"/>' +
    '<path d="M8.6 12.2l2.2 2.2 4.4-4.6" fill="none" stroke="var(--ok)" ' +
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var CHECK_SVG = '<svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">' +
    '<path d="M4.5 12.5l5 5 10-11" fill="none" stroke="currentColor" ' +
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var OPEN_SVG = '<svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">' +
    '<path d="M9 5h10v10M19 5 8 16" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  // ---- one-time CSS --------------------------------------------------------
  function injectCss() {
    var d = doc(); if (!d || d.getElementById("google-css")) return;
    var s = d.createElement("style");
    s.id = "google-css";
    s.textContent = [
      "#mind-extra-google .gc-mark{color:var(--muted)}",
      ".gc-chip{display:inline-flex;align-items:center;gap:6px;font-size:10.5px;font-weight:640;",
      "letter-spacing:.03em;padding:3px 9px;border-radius:99px;color:var(--gcc);",
      "background:color-mix(in srgb,var(--gcc) 15%,transparent);border:1px solid color-mix(in srgb,var(--gcc) 32%,transparent)}",
      ".gc-chip i{width:6px;height:6px;border-radius:99px;background:var(--gcc);font-style:normal}",
      ".gc-note{display:flex;align-items:center;gap:9px;padding:9px 11px;border-radius:11px;margin:10px 0 2px;",
      "font-size:12px;color:var(--ink);background:color-mix(in srgb,var(--ok) 9%,transparent);",
      "border:1px solid color-mix(in srgb,var(--ok) 26%,transparent)}",
      ".gc-note .gc-sh{flex:0 0 auto;color:var(--muted)}",
      ".gc-note b{color:var(--ok);font-weight:640}",
      ".gc-lede{font-size:12.5px;color:var(--muted);margin:6px 0 10px;line-height:1.45}",
      ".gc-scopes{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}",
      ".gc-scope{font-size:10.5px;font-weight:600;padding:3px 8px;border-radius:99px;color:var(--muted);",
      "background:var(--glass-2);border:1px solid var(--hairline);display:inline-flex;align-items:center;gap:5px}",
      ".gc-scope svg{color:var(--ok)}",
      ".gc-band{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);",
      "margin:13px 0 5px;display:flex;align-items:center;gap:8px}",
      ".gc-band .gc-rule{flex:1;height:1px;background:var(--hairline)}",
      ".gc-miss{font-size:11.5px;color:var(--faint);padding:2px 0;line-height:1.4}",
      ".gc-row{display:flex;align-items:center;gap:10px;margin-top:12px}",
      ".gc-step{border:1px solid var(--hairline);border-radius:12px;margin:8px 0;overflow:hidden}",
      ".gc-step.gc-on{border-color:color-mix(in srgb,var(--iris) 38%,transparent)}",
      ".gc-shead{display:flex;align-items:center;gap:9px;padding:8px 11px;font-size:12.5px;",
      "font-weight:580;color:var(--muted);background:var(--glass-2)}",
      ".gc-step.gc-on .gc-shead{color:var(--ink)}",
      ".gc-dot{flex:0 0 auto;width:18px;height:18px;border-radius:99px;display:inline-flex;align-items:center;",
      "justify-content:center;font-size:10px;font-weight:700;color:var(--muted);",
      "background:var(--glass);border:1px solid var(--hairline)}",
      ".gc-step.gc-on .gc-dot{color:#fff;background:var(--iris);border-color:var(--iris)}",
      ".gc-step.gc-done .gc-dot{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 45%,transparent)}",
      ".gc-sbody{padding:10px 12px 12px;font-size:12px;color:var(--muted);line-height:1.5}",
      ".gc-sbody a{color:var(--iris);text-decoration:none}",
      ".gc-sbody a:hover{text-decoration:underline}",
      ".gc-ta{width:100%;min-height:74px;resize:vertical;margin-top:8px;border-radius:10px;padding:8px 10px;",
      "font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--ink);",
      "background:var(--glass-2);border:1px solid var(--hairline);outline:none;box-sizing:border-box}",
      ".gc-ta:focus{border-color:color-mix(in srgb,var(--iris) 45%,transparent)}",
      ".gc-in{width:100%;margin-top:8px;border-radius:10px;padding:8px 10px;box-sizing:border-box;",
      "font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--ink);",
      "background:var(--glass-2);border:1px solid var(--hairline);outline:none}",
      ".gc-in:focus{border-color:color-mix(in srgb,var(--iris) 45%,transparent)}",
      ".gc-url{margin-top:8px;padding:7px 9px;border-radius:9px;background:var(--glass-2);",
      "border:1px solid var(--hairline);font-family:ui-monospace,Menlo,monospace;font-size:10.5px;",
      "color:var(--muted);word-break:break-all;max-height:56px;overflow:auto}",
      ".gc-err{color:var(--bad);font-size:11.5px;margin-top:8px;line-height:1.4}",
      ".gc-tiny{font-size:11px;color:var(--faint);margin-top:7px;line-height:1.4}",
      ".gc-btnrow{display:flex;gap:8px;margin-top:10px;align-items:center;flex-wrap:wrap}",
      ".gc-skel{height:44px;border-radius:10px;margin:8px 0;",
      "background:linear-gradient(90deg,var(--glass-2),var(--glass),var(--glass-2));",
      "background-size:200% 100%;animation:gcsh 1.3s linear infinite}",
      "@keyframes gcsh{0%{background-position:200% 0}100%{background-position:-200% 0}}",
      "@media (prefers-reduced-motion:reduce){.gc-skel{animation:none}}",
    ].join("\n");
    (d.head || d.body || d.documentElement).appendChild(s);
  }

  // ---- card mount (replaces any existing instance) -------------------------
  function mount(grid, bodyHtml, tinyText) {
    var d = doc(); if (!d) return null;
    var old = d.getElementById("mind-extra-google");
    if (old && old.remove) old.remove();
    var s = d.createElement("section");
    s.className = "card glass span2";
    s.id = "mind-extra-google";
    s.innerHTML =
      "<h2>" + G_MARK + "Google" +
      '<span class="tiny" style="margin-left:auto">' + E(tinyText || "") + "</span></h2>" +
      '<div class="body">' + bodyHtml + "</div>";
    grid.appendChild(s);
    return s;
  }

  async function jpost(url, data) {
    var r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}),
    });
    var j = null;
    try { j = await r.json(); } catch (e) { j = { ok: false, error: "bad_response" }; }
    return j || { ok: false, error: "bad_response" };
  }

  function errText(j) {
    if (!j) return "Something went wrong.";
    var bits = [];
    if (j.hint) bits.push(j.hint);
    if (j.upstream) bits.push("Google said: " + j.upstream);
    if (!bits.length && j.error) bits.push(String(j.error).replace(/_/g, " "));
    return bits.join(" ") || "Something went wrong.";
  }

  // ---- entry point ---------------------------------------------------------
  async function googlePanel() {
    var d = doc(); if (!d) return;
    var grid = d.getElementById("view-mind");
    if (!grid) return;
    injectCss();
    mount(grid, '<div class="gc-skel"></div><div class="gc-skel"></div>', "");
    var st;
    try {
      var r = await fetch("/api/google/status", { cache: "no-store" });
      st = await r.json();
    } catch (e) { renderError(grid); return; }
    if (!st || st.ok === false) { renderError(grid); return; }
    render(grid, st);
  }

  function renderError(grid) {
    var s = mount(grid,
      '<div class="gc-tiny">Couldn’t load the Google connection status. ' +
      '<button class="ghost" id="gc-retry" style="margin-left:6px">Retry</button></div>', "");
    if (!s) return;
    var b = s.querySelector("#gc-retry");
    if (b) b.addEventListener("click", function () { googlePanel().catch(function () {}); });
  }

  // ---- render --------------------------------------------------------------
  var SCOPE_LABEL = {
    "https://www.googleapis.com/auth/gmail.readonly": "Read mail",
    "https://www.googleapis.com/auth/calendar": "Calendar",
    "https://www.googleapis.com/auth/contacts.readonly": "Contacts · read-only",
  };

  function chip(text, color) {
    return '<span class="gc-chip" style="--gcc:' + color + '"><i></i>' + E(text) + "</span>";
  }

  function safetyNote() {
    return '<div class="gc-note">' + SHIELD_SVG +
      "<span><b>Read-only Gmail</b> — sending is impossible by authorization design. " +
      "Hermes can draft text for you; only you can send it.</span></div>";
  }

  function render(grid, st) {
    GW.lastSt = st;
    if (st.connected) { renderConnected(grid, st); return; }
    renderDisconnected(grid, st);
  }

  // ---- connected -----------------------------------------------------------
  function renderConnected(grid, st) {
    var scopes = st.scopes || [];
    var chips = "";
    scopes.forEach(function (sc) {
      var lbl = SCOPE_LABEL[sc];
      if (!lbl) {
        if (/openid|userinfo/.test(sc)) return;
        lbl = String(sc).split("/").pop();
      }
      chips += '<span class="gc-scope">' + CHECK_SVG + E(lbl) + "</span>";
    });
    var miss = "";
    (st.missing_features || []).forEach(function (m) {
      miss += '<div class="gc-miss">' + E(m) + "</div>";
    });
    var html =
      '<div class="gc-row" style="margin-top:2px">' +
      chip("Connected · read-only", "var(--ok)") +
      (st.email ? '<span class="gc-tiny" style="margin-top:0">' + E(st.email) + "</span>" : "") +
      "</div>" +
      '<div class="gc-scopes">' + chips + "</div>" +
      safetyNote() +
      (miss ? '<div class="gc-band">Not granted<span class="gc-rule"></span></div>' + miss : "") +
      '<div class="gc-btnrow"><button class="ghost" id="gc-disc">Disconnect</button>' +
      '<span class="gc-tiny" style="margin-top:0">Token refreshes itself from here.</span></div>';
    var s = mount(grid, html, st.checked_at ? "checked " + t12(st.checked_at) : "");
    if (!s) return;
    var b = s.querySelector("#gc-disc");
    if (b) b.addEventListener("click", async function () {
      var okgo = true;
      try { if (typeof confirm === "function") okgo = confirm("Disconnect Google? The grant is revoked with Google and the local token is deleted."); } catch (e) {}
      if (!okgo) return;
      b.disabled = true; b.textContent = "Disconnecting…";
      try { await jpost("/api/google/disconnect", {}); } catch (e) {}
      GW.open = false; GW.step = 1; GW.authUrl = ""; GW.err = "";
      googlePanel().catch(function () {});
    });
  }

  // ---- disconnected + wizard ------------------------------------------------
  function stepShell(n, title, state, bodyHtml) {
    var cls = state === "on" ? " gc-on" : (state === "done" ? " gc-done" : "");
    var dot = state === "done" ? CHECK_SVG : String(n);
    return '<div class="gc-step' + cls + '" data-step="' + n + '">' +
      '<div class="gc-shead"><span class="gc-dot">' + dot + "</span>" + E(title) + "</div>" +
      (state === "on" ? '<div class="gc-sbody">' + bodyHtml + "</div>" : "") +
      "</div>";
  }

  function renderDisconnected(grid, st) {
    if (!GW.open) {
      var intro =
        '<div class="gc-row" style="margin-top:2px">' + chip("Not connected", "var(--faint)") + "</div>" +
        '<div class="gc-lede">Connect Gmail (read-only), Calendar and Contacts so Hermes can ' +
        "read your mail, keep the agenda fresh and draft replies for you to send.</div>" +
        safetyNote() +
        '<div class="gc-btnrow"><button class="primary" id="gc-go">Connect Google</button></div>';
      var s0 = mount(grid, intro, "");
      if (!s0) return;
      var go = s0.querySelector("#gc-go");
      if (go) go.addEventListener("click", function () {
        GW.open = true;
        GW.err = "";
        GW.step = st.awaiting_code ? 3 : (st.has_client_secret ? 2 : 1);
        render(grid, GW.lastSt || st);
      });
      return;
    }

    var step = GW.step;
    var s1state = step === 1 ? "on" : (st.has_client_secret ? "done" : "todo");
    var s2state = step === 2 ? "on" : (step > 2 ? "done" : "todo");
    var s3state = step === 3 ? "on" : "todo";

    var body1 =
      "In <a href=\"https://console.cloud.google.com/apis/credentials\" target=\"_blank\" rel=\"noopener\">Google Cloud Console</a>: " +
      "enable the <b>Gmail API</b>, <b>Google Calendar API</b> and <b>People API</b>, create an " +
      "<b>OAuth client ID</b> of type <b>Desktop app</b>, download its JSON, then paste the file’s contents here. " +
      "It is stored only on this Mac (~/.hermes, private permissions) and never shown again." +
      '<textarea class="gc-ta" id="gc-secret" spellcheck="false" autocomplete="off" ' +
      'placeholder=\'{ "installed": { "client_id": "…", … } }\'></textarea>' +
      '<div class="gc-btnrow"><button class="primary" id="gc-save">Save client file</button></div>' +
      (GW.err && step === 1 ? '<div class="gc-err">' + E(GW.err) + "</div>" : "");

    var body2 =
      "Open Google’s consent page in your browser and approve the three read items. " +
      "Hermes asks only for <b>read mail</b>, <b>calendar</b> and <b>read-only contacts</b> — nothing that can send." +
      '<div class="gc-btnrow"><button class="primary" id="gc-consent">Open Google consent ' + OPEN_SVG + "</button>" +
      (GW.authUrl ? '<button class="ghost" id="gc-copy">Copy link</button>' : "") + "</div>" +
      (GW.authUrl ? '<div class="gc-url" id="gc-urlbox">' + E(GW.authUrl) + "</div>" : "") +
      (GW.err && step === 2 ? '<div class="gc-err">' + E(GW.err) + "</div>" : "");

    var body3 =
      "After you approve, the browser tries to load <b>http://localhost:1</b> and fails — " +
      "<b>that’s expected</b>. Copy the entire address from the address bar and paste it here." +
      '<input class="gc-in" id="gc-code" spellcheck="false" autocomplete="off" ' +
      'placeholder="http://localhost:1/?code=…" />' +
      '<div class="gc-btnrow"><button class="primary" id="gc-finish">Finish connection</button>' +
      '<button class="ghost" id="gc-back">Get a fresh link</button></div>' +
      (GW.err && step === 3 ? '<div class="gc-err">' + E(GW.err) + "</div>" : "");

    var html =
      '<div class="gc-row" style="margin-top:2px">' +
      chip(step === 3 ? "Awaiting code" : "Not connected", step === 3 ? "var(--warn)" : "var(--faint)") +
      "</div>" +
      safetyNote() +
      stepShell(1, "Paste the OAuth client file", s1state, body1) +
      stepShell(2, "Approve in your browser", s2state, body2) +
      stepShell(3, "Paste the redirect URL", s3state, body3) +
      '<div class="gc-btnrow"><button class="ghost" id="gc-cancel">Cancel</button></div>';

    var s = mount(grid, html, "step " + step + " of 3");
    if (!s) return;
    var active = s.querySelector('.gc-step.gc-on .gc-sbody');
    slideIn(active);

    var cancel = s.querySelector("#gc-cancel");
    if (cancel) cancel.addEventListener("click", function () {
      GW.open = false; GW.err = "";
      render(grid, GW.lastSt || st);
    });

    var save = s.querySelector("#gc-save");
    if (save) save.addEventListener("click", async function () {
      var ta = s.querySelector("#gc-secret");
      var txt = ta && typeof ta.value === "string" ? ta.value.trim() : "";
      if (!txt) { GW.err = "Paste the JSON file contents first."; render(grid, st); return; }
      var parsed = null;
      try { parsed = JSON.parse(txt); } catch (e) {
        GW.err = "That paste isn’t valid JSON — copy the whole file, from { to }.";
        render(grid, st); return;
      }
      save.disabled = true; save.textContent = "Saving…";
      var j;
      try { j = await jpost("/api/google/client_secret", { json: parsed }); }
      catch (e) { j = { ok: false, error: "network" }; }
      if (ta) ta.value = "";                       // never keep the secret around
      if (j && j.ok) {
        GW.err = ""; GW.step = 2;
        googlePanel().catch(function () {});       // refetch: has_client_secret flips
      } else {
        GW.err = errText(j);
        render(grid, st);
      }
    });

    var consent = s.querySelector("#gc-consent");
    if (consent) consent.addEventListener("click", async function () {
      consent.disabled = true; consent.textContent = "Preparing…";
      var j;
      try {
        var r = await fetch("/api/google/auth_url", { cache: "no-store" });
        j = await r.json();
      } catch (e) { j = { ok: false, error: "network" }; }
      if (j && j.ok && j.auth_url) {
        GW.authUrl = j.auth_url; GW.err = ""; GW.step = 3;
        try { if (typeof window.open === "function") window.open(j.auth_url); } catch (e) {}
        render(grid, st);
      } else {
        GW.err = errText(j);
        if (j && j.error === "no_client_secret") GW.step = 1;
        render(grid, st);
      }
    });

    var copy = s.querySelector("#gc-copy");
    if (copy) copy.addEventListener("click", function () {
      var u = GW.authUrl || "";
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(u);
          copy.textContent = "Copied";
          setTimeout(function () { try { copy.textContent = "Copy link"; } catch (e) {} }, 1400);
        }
      } catch (e) {}
    });

    var back = s.querySelector("#gc-back");
    if (back) back.addEventListener("click", function () {
      GW.step = 2; GW.err = ""; GW.authUrl = "";
      render(grid, st);
    });

    var finish = s.querySelector("#gc-finish");
    if (finish) finish.addEventListener("click", async function () {
      var inp = s.querySelector("#gc-code");
      var code = inp && typeof inp.value === "string" ? inp.value.trim() : "";
      if (!code) { GW.err = "Paste the redirected URL (or the code) first."; render(grid, st); return; }
      finish.disabled = true; finish.textContent = "Connecting…";
      var j;
      try { j = await jpost("/api/google/auth_code", { code: code }); }
      catch (e) { j = { ok: false, error: "network" }; }
      if (j && j.ok && j.connected) {
        GW.open = false; GW.step = 1; GW.authUrl = ""; GW.err = "";
        googlePanel().catch(function () {});
      } else {
        GW.err = errText(j);
        if (j && (j.error === "no_pending" || j.error === "state_mismatch")) {
          GW.step = 2; GW.authUrl = "";
        }
        render(grid, st);
      }
    });
  }
})();
