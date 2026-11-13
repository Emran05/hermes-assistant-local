// aux_network.js — "Data & Network" card for Settings › Connections (1.1.3,
// backlog #16). The trust claim this whole product rests on — "it runs on your
// Mac" — written down as an auditable table with the real off switches in it.
//
// PLACEMENT. The Settings shell (aux_settings_shell.js) builds its rail and its
// twelve panels ONCE, from a PANELS array captured inside its IIFE, in
// ensureShell() at install time. `window.SETTINGS_PANELS` is exposed but
// pushing to it afterwards changes nothing: the markup is already built and
// ensureShell() early-returns on the second call. There is no registration hook
// and no re-build path, so **a module cannot add a new Settings panel id
// without editing aux_settings_shell.js** — which this release does not do.
// Hence a CARD at the top of an existing panel: Connections ("Accounts and data
// access"), which is exactly the panel a reader looking for "what leaves this
// Mac" opens.
//
// It mounts DIRECTLY into #sec-connections and never into #view-mind — unlike
// aux_update.js, which can fall back to the root because the shell's relocator
// sends unknown ids to sec-system BY DESIGN, and sec-system is where the update
// card belongs. For us that fallback would be a bug: the card would silently
// land in System & Data. If the panel is not built yet we simply wait for the
// next mindExtras() pass.
//
// CONTENT. The table is `hermesOnboarding.networkTableHTML(...)` over
// `NETWORK_FACTS` — the SAME constant and the SAME function the first-run sheet
// uses, with `netCSS()` re-scoped to this card's id. Adding an egress path to
// Hermes means adding one row to that constant and both surfaces update. The
// markup is never copied here.
//
// The toggles are LIVE, and they are the switches that already exist:
//   claude_escalation -> window.setClaudeEscalation() (POST /api/claude/escalate)
//   foryou_claude     -> POST /api/claude/foryou (For-You's own switch, C35)
//   briefings / news  -> POST /api/watchtower {op:"set_master"}
//   web_research      -> POST /api/watchtower {op:"set_web_research"} (R2-43:
//                        the unattended research pass + its test search, C50)
// The News switch sits on two rows (the feeds it stops, C48, and the alerts),
// so a flip settles EVERY checkbox with its key.
// so a flip here is the same flip as in the model menu, the Claude Bridge panel
// and the Watchtower card, and the hermes:claude-escalation broadcast keeps all
// of them in step.
//
// "Last outbound" is EVIDENCE, not status: when a request really LEFT this Mac
// for that destination, from GET /api/network/last (aux_network.py, C52),
// which reads the stores the dashboard already keeps — the last real update
// check, the last feed gather (it used to show the newest watchtower ALERT
// here), Telegram deliveries, bridge calls that spawned `claude -p`, the last
// request to Google. A destination the dashboard cannot observe reads "not
// tracked", never a guess. If the route does not answer, the column is
// dropped rather than filled with dashes.
//
// FRESHNESS (C72): the state is re-read every time the Settings view mounts
// the card (at most every REFRESH_MS) and when the window comes back into
// view, so a switch flipped in another card and a new Last outbound show up
// without a reload.
//
// Design laws (CLAUDE.md): zero emoji (bespoke two-tone SVG), 12-hour clock,
// esc() on every interpolation, explicit transition-property, >=40px targets,
// every global helper typeof-guarded so a headless harness can eval this file.
(function () {
  "use strict";

  var W = (typeof window !== "undefined") ? window
        : (typeof globalThis !== "undefined") ? globalThis : null;
  if (!W) return;

  function D() { return (typeof document !== "undefined") ? document : null; }
  function E(s) {
    if (typeof esc === "function") { try { return esc(s); } catch (e) {} }
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var CARD_ID = "mind-extra-network";
  var PANEL_ID = "sec-connections";
  var SEL = "#" + CARD_ID;

  // ---- state ---------------------------------------------------------------
  var S = {
    prefs: {},        // {claude_escalation, foryou_claude, briefings, news, web_research}
    last: null,       // {factId: "3:42 PM"} or null => no Last outbound column
    loaded: false,
    loadedAt: 0,      // ms; mount() re-reads when older than REFRESH_MS (C72)
    busy: null,       // pref key currently in flight
    busyOn: null      // ...and the value it is being switched to
  };
  var REFRESH_MS = 15000;

  // ---- glyphs (two-tone: accent fill + currentColor stroke; zero emoji) -----
  var GLY_NET =
    '<svg class="netic" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="8.6" fill="var(--iris)" opacity=".14"/>' +
    '<circle cx="12" cy="12" r="8.6" fill="none" stroke="currentColor" stroke-width="1.5"/>' +
    '<path d="M3.4 12h17.2M12 3.4c2.3 2.4 3.4 5.4 3.4 8.6s-1.1 6.2-3.4 8.6' +
    'c-2.3-2.4-3.4-5.4-3.4-8.6S9.7 5.8 12 3.4Z" fill="none" stroke="currentColor" ' +
    'stroke-width="1.4" stroke-linejoin="round"/></svg>';
  var GLY_LOCK =
    '<svg class="netlk" viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">' +
    '<rect x="5" y="10.5" width="14" height="9.5" rx="2.2" fill="var(--iris)" opacity=".18"/>' +
    '<rect x="5" y="10.5" width="14" height="9.5" rx="2.2" fill="none" stroke="currentColor" ' +
    'stroke-width="1.5"/><path d="M8.4 10.5V8a3.6 3.6 0 0 1 7.2 0v2.5" fill="none" ' +
    'stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';

  // ==========================================================================
  // What never leaves — the read-only half of the disclosure. Every line names
  // a store that exists on disk, so a reader can go and look at it.
  // ==========================================================================
  var LOCAL_FACTS = [
    ["Model inference", "Every answer is generated by the model running on this Mac, " +
      "except a Claude answer while a Claude row above is on."],
    ["Search index", "Chats, notes, messages, calendar and news, indexed locally in SQLite."],
    ["Conversations", "Every chat is a file under ~/.hermes/dashboard/chats."],
    ["Notes", "The Scratchpad is a local file. Nothing syncs."],
    ["Flight Recorder", "The undo trail of what the agent did stays on this Mac."],
    // Real on-disk exposure, so it is written down here rather than only in the
    // Tool output budget card: a tool result over the cap is cached WHOLE, and
    // a tool result can be a file, a page or a command's output. The plugin
    // deletes day-directories older than seven days
    // (hermes-plugins/tool-budget/__init__.py, _gc_spill) — if that retention
    // or that path changes, change this line with it.
    ["Tool output cache", "A tool result over the budget is kept in full for 7 " +
      "days under ~/.hermes/dashboard/spill (0600), so the assistant can read " +
      "the part that was trimmed."]
  ];

  // ---- pure helpers (exported for the headless harness) --------------------

  // absolute 12-hour clock, per the repo's design law (never "3 minutes ago").
  // Reuses aux_update.js's formatter when it is loaded so the two cards phrase
  // a timestamp identically; the fallback is the same algorithm.
  function fmtWhen(ts) {
    try {
      var u = W.hermesUpdate;
      if (u && typeof u.fmtWhen === "function") return u.fmtWhen(ts);
    } catch (e) {}
    if (!ts) return "never";
    try {
      var d = new Date(ts * (ts > 1e12 ? 1 : 1000));
      if (isNaN(d.getTime())) return "never";
      var h = d.getHours(), m = d.getMinutes(), ap = h >= 12 ? "PM" : "AM";
      h = h % 12; if (h === 0) h = 12;
      var t = h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
      var now = new Date();
      if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() &&
          d.getDate() === now.getDate()) return t;
      return (d.getMonth() + 1) + "/" + d.getDate() + " " + t;
    } catch (e) { return "never"; }
  }

  // Build the {factId: string} map for the Last outbound column from
  // GET /api/network/last. PURE so the harness can drive every branch without
  // a network. A fact the route does not track reads "not tracked"; a tracked
  // one with no record yet reads "no record". Returns null when the route did
  // not answer — the column is then dropped rather than printing dashes.
  function lastMap(net) {
    if (!net || net.ok !== true || !net.last || typeof net.last !== "object") return null;
    var src = net.last, out = {};
    var ids = [];
    try {
      var ob = W.hermesOnboarding;
      ids = (ob && ob.NETWORK_FACTS ? ob.NETWORK_FACTS : []).map(function (f) { return f.id; });
    } catch (e) { ids = []; }
    Object.keys(src).forEach(function (k) { if (ids.indexOf(k) < 0) ids.push(k); });
    ids.forEach(function (id) {
      if (!Object.prototype.hasOwnProperty.call(src, id)) { out[id] = "not tracked"; return; }
      var ts = Number(src[id] || 0);
      out[id] = ts > 0 ? fmtWhen(ts) : "no record";
    });
    // the update check that signed in with a token / git credentials says so
    if (net.updates_signed === true && out.updates && out.updates !== "no record") {
      out.updates += " · signed in";
    }
    if (net.google_connected === false && out.google === "no record") out.google = "not connected";
    return out;
  }

  // ---- CSS -----------------------------------------------------------------
  // netCSS() from aux_onboarding.js styles the table + its switches, re-scoped
  // to this card. Everything else here is this card's own chrome.
  function CSS() {
    var tbl = "";
    try {
      var ob = W.hermesOnboarding;
      if (ob && typeof ob.netCSS === "function") tbl = ob.netCSS(SEL);
    } catch (e) { tbl = ""; }
    return "<style>" + tbl +
      SEL + " .netic{flex:0 0 auto;color:var(--muted)}" +
      SEL + " .body{padding-bottom:14px}" +
      // FITTING THE TABLE. The Settings panel is ~700px, not the ~680px column
      // of the onboarding sheet, and this card adds a fifth column — measured,
      // the sheet's own min-widths plus the nowrap row header come to 732px of
      // intrinsic minimum against 673px of room, so .onb-tablewrap silently
      // scrolled and hid the Switch column, the one thing this card exists to
      // show. A max-width on the table does NOT fix that (table-layout:auto
      // overflows a max-width below its minimum). Relaxing the three minimums
      // does: the row header wraps, and When/What size to their content.
      SEL + ' table.onb-net tbody th{white-space:normal}' +
      SEL + " .onb-net-when," + SEL + " .onb-net-data{min-width:0}" +
      SEL + ' table.onb-net thead th.onb-net-lasth{white-space:nowrap}' +
      // ROW CARDS (visual audit V01, 2026-11-12). Even fitted, five columns
      // in 673px left When and What 82 and 112px wide (rows up to 919px
      // tall) while one long "off" line held the Switch column at 272px.
      // Here each row is a two-line grid instead: name, last outbound and
      // the switch on top, When and What side by side under it at a
      // readable measure. The Switch column is a fixed 132px track whose
      // "off" text wraps, so no text can push it out of view again.
      // display changes drop table semantics in WebKit, so tableRoles()
      // puts explicit ARIA table roles on the same elements after render.
      SEL + " .onb-tablewrap{position:relative;overflow-x:visible}" +
      SEL + " table.onb-net," + SEL + " table.onb-net tbody{display:block;width:100%;min-width:0}" +
      SEL + " table.onb-net thead{position:absolute;width:1px;height:1px;margin:-1px;padding:0;" +
        "overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;border:0}" +
      SEL + " table.onb-net tbody tr{display:grid;" +
        "grid-template-columns:minmax(0,1fr) minmax(0,1.3fr) 132px;" +
        'grid-template-areas:"goes last ctl" "when what what";' +
        "column-gap:16px;row-gap:6px;padding:10px 0 12px;" +
        "border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}" +
      SEL + " table.onb-net tbody tr:last-child{border-bottom:0}" +
      SEL + " table.onb-net tbody th," + SEL + " table.onb-net tbody td{display:block;" +
        "padding:0;border-bottom:0;min-width:0}" +
      SEL + " table.onb-net tbody th{grid-area:goes;align-self:center}" +
      SEL + " table.onb-net tbody .onb-net-last{grid-area:last;align-self:center;white-space:normal}" +
      SEL + ' table.onb-net tbody .onb-net-last::before{content:"Last outbound: ";' +
        'content:"Last outbound: " / ""}' +
      SEL + " table.onb-net tbody .onb-net-ctl{grid-area:ctl;align-self:center;" +
        "justify-self:end;text-align:right;white-space:normal}" +
      SEL + " table.onb-net tbody .onb-net-ctl .onb-net-always{display:block;line-height:1.4}" +
      SEL + " table.onb-net tbody .onb-net-when{grid-area:when;line-height:1.5}" +
      SEL + " table.onb-net tbody .onb-net-data{grid-area:what;line-height:1.5}" +
      SEL + " table.onb-net tbody .onb-net-when::before," +
        SEL + " table.onb-net tbody .onb-net-data::before{display:block;margin:0 0 2px;" +
        "font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;" +
        "color:var(--faint,#868DA1)}" +
      SEL + ' table.onb-net tbody .onb-net-when::before{content:"When";content:"When" / ""}' +
      SEL + ' table.onb-net tbody .onb-net-data::before{content:"What";content:"What" / ""}' +
      SEL + " .netlede{margin:2px 0 14px;font-size:12.5px;line-height:1.55;" +
        "color:var(--muted);text-wrap:pretty;max-width:62ch}" +
      // the one-line promise under the table — quiet, but the point of the card
      SEL + " .netfoot{margin:10px 0 0;font-size:12px;color:var(--ink);font-weight:600}" +
      SEL + " .neterr{margin:8px 0 0;font-size:11px;color:var(--bad)}" +
      // "What stays local" — a rule + a label, never a card inside a card
      SEL + " .neth{display:flex;align-items:center;gap:7px;margin:22px 0 10px;" +
        "padding-bottom:7px;border-bottom:1px solid var(--hairline);" +
        "font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;" +
        "color:var(--faint)}" +
      SEL + " .netlk{flex:0 0 auto;color:var(--faint)}" +
      SEL + " ul.netlocal{list-style:none;margin:0;padding:0;display:grid;gap:8px}" +
      SEL + " ul.netlocal li{display:grid;grid-template-columns:minmax(0,148px) minmax(0,1fr);" +
        "gap:4px 14px;font-size:12px;line-height:1.45}" +
      SEL + " ul.netlocal b{font-weight:620;color:var(--ink)}" +
      SEL + " ul.netlocal span{color:var(--muted);text-wrap:pretty}" +
      "@media (max-width:620px){" + SEL + " ul.netlocal li{grid-template-columns:1fr}}" +
      "</style>";
  }

  // ---- table semantics -----------------------------------------------------
  // The card's CSS lays each row out as a grid (V01), and WebKit drops a
  // table's semantics once its parts stop being display:table-*. Explicit
  // ARIA roles on the SAME elements keep it a table with row and column
  // headers for VoiceOver. Done on the DOM after render so the shared
  // networkTableHTML() markup (and the sheet, which stays a real table)
  // is unchanged.
  var ROLES = [["table.onb-net", "table"], ["table.onb-net thead", "rowgroup"],
    ["table.onb-net tbody", "rowgroup"], ["table.onb-net tr", "row"],
    ["table.onb-net thead th", "columnheader"], ["table.onb-net tbody th", "rowheader"],
    ["table.onb-net td", "cell"]];
  function tableRoles(el) {
    if (!el || typeof el.querySelectorAll !== "function") return;
    ROLES.forEach(function (r) {
      try {
        var ns = el.querySelectorAll(r[0]) || [];
        for (var i = 0; i < ns.length; i++) {
          if (ns[i] && typeof ns[i].setAttribute === "function") ns[i].setAttribute("role", r[1]);
        }
      } catch (e) {}
    });
  }

  // ---- markup --------------------------------------------------------------
  function cardHTML(state) {
    state = state || {};
    var ob = null;
    try { ob = W.hermesOnboarding; } catch (e) { ob = null; }
    var table = "";
    if (ob && typeof ob.networkTableHTML === "function") {
      try {
        table = ob.networkTableHTML({
          prefs: state.prefs || {},
          inputs: true,
          last: state.last || null,
          caption: ""            // the card's own lede already says it
        });
      } catch (e) { table = ""; }
    }
    if (!table) {
      // aux_onboarding.js is the single source of this table. If it did not
      // load, say so rather than rendering a second, drifting copy.
      table = '<p class="neterr">The network table could not be rendered ' +
        "(aux_onboarding.js did not load). Nothing here is disabled — reload the page.</p>";
    }

    // <h2> + .body is the repo's card convention (see aux_google.js): .body
    // carries the 10/14 padding and its own overflow, so the last row is never
    // clipped by .card{overflow:hidden}.
    return CSS() +
      "<h2>" + GLY_NET + "Data &amp; Network</h2>" +
      '<div class="body">' +
      '<p class="netlede">' + E(factCount()) + " destinations: what the dashboard, " +
      "its schedules, the gateway and the agent&rsquo;s own tools can send to the " +
      "internet. Each row is a real outbound path in the shipping code &mdash; here " +
      "is what it sends, when, the last time this dashboard saw it happen, and the " +
      "switch that stops it, where there is one. The agent&rsquo;s tools row has " +
      "no switch: a command it runs goes wherever the command sends it.</p>" +
      table +
      '<p class="netfoot">The model, your stores and the local index never leave ' +
      "this Mac on their own; only the paths above carry anything out.</p>" +
      '<div class="neth">' + GLY_LOCK + "What stays local</div>" +
      '<ul class="netlocal">' + LOCAL_FACTS.map(function (f) {
        return "<li><b>" + E(f[0]) + "</b><span>" + E(f[1]) + "</span></li>";
      }).join("") + "</ul>" +
      "</div>";
  }

  function factCount() {
    try {
      var ob = W.hermesOnboarding;
      var n = ob && ob.NETWORK_FACTS ? ob.NETWORK_FACTS.length : 0;
      return n ? String(n) : "These";
    } catch (e) { return "These"; }
  }

  // ---- data ----------------------------------------------------------------
  async function jget(url) {
    try {
      var r = await fetch(url);
      return await r.json();
    } catch (e) { return null; }
  }
  async function jpost(url, body) {
    try {
      var r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {})
      });
      return await r.json();
    } catch (e) { return null; }
  }

  async function load() {
    // Cheap local reads only: watchtower config, the network evidence route
    // (file reads, aux_network.py) and the two Claude switches. Nothing here
    // fetches, and nothing here can wake the model.
    var res = await Promise.all([
      jget("/api/watchtower"),
      jget("/api/network/last"),
      jget("/api/claude/foryou")
    ]);
    var wt = res[0] || {}, net = res[1], fy = res[2];

    var master = wt.master || {};
    var prefs = {
      briefings: master.briefings !== false,
      news: master.news !== false,
      // C50: off unless watchtower.json says an explicit true
      web_research: !!(wt.web_research && wt.web_research.enabled === true),
      // index.html owns the escalation state and broadcasts every change; fall
      // back to the route only when it has not fetched yet.
      claude_escalation: escState()
    };
    if (prefs.claude_escalation === null) {
      var e = await jget("/api/claude/escalate");
      // an unread state is OFF, never On (the backend enforces off anyway)
      prefs.claude_escalation = !!(e && e.enabled === true);
    }
    prefs.foryou_claude = !!(fy && fy.enabled === true);
    // a flip in flight owns its key: do not let a re-read undo it mid-POST
    // (keep the value being switched TO — S.prefs still holds the old one
    // until the POST lands, and a repaint from it flipped the switch back)
    if (S.busy) prefs[S.busy] = S.busyOn;
    S.prefs = prefs;
    S.last = lastMap(net);
    S.loaded = true;
    S.loadedAt = Date.now();
  }

  function escState() {
    try {
      if (typeof W.claudeEscalationState === "function") {
        var v = W.claudeEscalationState();
        return (v === null || v === undefined) ? null : !!v;
      }
    } catch (e) {}
    return null;
  }

  // ---- flipping a switch ---------------------------------------------------
  // Optimistic: the toggle moves at once (it is a checkbox; a lag reads as a
  // dead control), and reverts with a message if the write did not land.
  async function flip(key, on, label) {
    var d = D(); if (!d) return;
    var el = d.querySelector(SEL + ' input[data-pref="' + key + '"]');
    var wrap = el && el.parentNode;
    if (wrap && wrap.classList) wrap.classList.add("is-busy");
    setErr("");
    var prev = !!S.prefs[key];    // the stored state, until the write lands
    S.busy = key;
    S.busyOn = on;
    var ok = false;
    try {
      if (key === "claude_escalation") {
        // the shared helper, so the model menu / Bridge panel hear the
        // hermes:claude-escalation broadcast and repaint with us
        if (typeof W.setClaudeEscalation === "function") ok = await W.setClaudeEscalation(on);
        else { var r = await jpost("/api/claude/escalate", { enabled: on }); ok = !!(r && r.ok !== false); }
      } else if (key === "foryou_claude") {
        var f = await jpost("/api/claude/foryou", { enabled: on });
        ok = !!(f && f.ok === true && f.enabled === on);
      } else if (key === "briefings" || key === "news") {
        var body = { op: "set_master" };
        body[key] = on;
        var j = await jpost("/api/watchtower", body);
        ok = !!(j && j.ok);
      } else if (key === "web_research") {
        var wr = await jpost("/api/watchtower", { op: "set_web_research", enabled: on });
        ok = !!(wr && wr.ok && wr.web_research && wr.web_research.enabled === on);
      }
    } catch (e) { ok = false; }
    S.busy = null;
    if (wrap && wrap.classList) wrap.classList.remove("is-busy");
    // a re-read (C72) may have repainted the card while the POST was in
    // flight: settle the switch that is on screen NOW, not the one we started
    // from
    var live = d.querySelector(SEL + ' input[data-pref="' + key + '"]') || el;
    var lwrap = (live && live.parentNode) || wrap;
    if (lwrap && lwrap !== wrap && lwrap.classList) lwrap.classList.remove("is-busy");
    // a re-read during the POST kept `on` for this key; a failed write did
    // not change anything, so the switch goes back to what it was
    S.prefs[key] = ok ? on : prev;
    if (live) live.checked = S.prefs[key];
    // keep the visible "On"/"Off" word next to the switch honest
    var lbl = lwrap && lwrap.querySelector ? lwrap.querySelector(".onb-sw-l") : null;
    if (lbl) lbl.textContent = S.prefs[key] ? "On" : "Off";
    // ...and every other row that carries the same switch (News: two rows)
    if (typeof d.querySelectorAll === "function") {
      try {
        Array.prototype.slice.call(
          d.querySelectorAll(SEL + ' input[data-pref="' + key + '"]')).forEach(function (x) {
          x.checked = S.prefs[key];
          var l = x.parentNode && x.parentNode.querySelector
            ? x.parentNode.querySelector(".onb-sw-l") : null;
          if (l) l.textContent = S.prefs[key] ? "On" : "Off";
        });
      } catch (e) {}
    }
    if (!ok) setErr("Could not change " + (label || key) + " — nothing was switched.");
  }

  function setErr(msg) {
    var d = D(); if (!d) return;
    var card = d.getElementById(CARD_ID); if (!card) return;
    var p = card.querySelector(".neterr-live");
    if (!msg) { if (p && p.parentNode) p.parentNode.removeChild(p); return; }
    if (!p) {
      p = d.createElement("p");
      p.className = "neterr neterr-live";
      p.setAttribute("role", "status");
      var foot = card.querySelector(".netfoot");
      if (foot && foot.parentNode) foot.parentNode.insertBefore(p, foot);
      else card.appendChild(p);
    }
    p.textContent = msg;
  }

  // ---- mount ---------------------------------------------------------------
  function wire(card) {
    if (!card || !card.querySelectorAll) return;
    var facts = {};
    try {
      var ob = W.hermesOnboarding;
      (ob && ob.NETWORK_FACTS ? ob.NETWORK_FACTS : []).forEach(function (f) {
        if (f && f.toggle) facts[f.toggle.key] = f.toggle.label;
      });
    } catch (e) {}
    Array.prototype.slice.call(card.querySelectorAll("input[data-pref]")).forEach(function (el) {
      el.onchange = function () { flip(el.getAttribute("data-pref"), !!el.checked, facts[el.getAttribute("data-pref")]); };
    });
  }

  var relocatedOnce = false;

  function paint() {
    var d = D(); if (!d) return;
    var panel = d.getElementById(PANEL_ID);
    // No panel yet => wait. Deliberately NOT falling back to #view-mind: the
    // shell's relocator sends an id it does not know to sec-system, which is
    // the wrong panel for this card (see the header note).
    if (!panel) return;
    var body = panel.querySelector(".set-body") || panel;
    var el = d.getElementById(CARD_ID);
    if (!el) {
      el = d.createElement("section");
      el.id = CARD_ID;
      el.className = "card glass set-legacy";
      // FIRST card in Connections. Relocated legacy cards insert before
      // [data-legacy-slot] i.e. after us, and .set-body is a flex column, so
      // order:-1 keeps us on top even if a later pass re-appends above.
      el.style.order = "-1";
      var anchor = body.querySelector("[data-legacy-slot]");
      if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(el, anchor);
      else body.appendChild(el);
    } else if (el.parentNode !== body) {
      try { body.appendChild(el); el.style.order = "-1"; } catch (e) {}
    }
    try { el.innerHTML = cardHTML(S); } catch (e) { return; }
    tableRoles(el);
    wire(el);
    if (!relocatedOnce) {
      relocatedOnce = true;
      // one pass so the shell re-indexes its search and drops the panel's
      // "no cards yet" placeholder. Idempotent, and it never touches us (we
      // are not a direct child of #view-mind).
      try { if (typeof W.settingsRelocate === "function") W.settingsRelocate(); } catch (e) {}
    }
  }

  // C72: the card used to read its state ONCE per page load, so a switch
  // flipped in the Watchtower card or the model menu, and every new Last
  // outbound, stayed stale until a reload. Re-read on every mount (the
  // Settings view mounts us each time it opens), throttled to REFRESH_MS.
  async function mount() {
    if (!S.loaded || (Date.now() - (S.loadedAt || 0)) >= REFRESH_MS) {
      try { await load(); } catch (e) {}
    }
    paint();
  }

  // chain window.mindExtras exactly like the other aux settings cards
  var prev = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await mount(); } catch (e) {}
  };

  // stay in step with the model menu / Claude Bridge panel: both broadcast
  // after their own successful POST, and our checkbox must not lie.
  try {
    if (W.addEventListener) W.addEventListener("hermes:claude-escalation", function () {
      var st = escState();
      if (st === null) return;
      S.prefs.claude_escalation = st;
      var d = D(); if (!d) return;
      var el = d.querySelector(SEL + ' input[data-pref="claude_escalation"]');
      if (el) {
        el.checked = st;
        var lbl = el.parentNode && el.parentNode.querySelector
          ? el.parentNode.querySelector(".onb-sw-l") : null;
        if (lbl) lbl.textContent = st ? "On" : "Off";
      }
    });
  } catch (e) {}

  // coming back to the window: refresh if the card is on screen
  try {
    var dd = D();
    if (dd && dd.addEventListener) dd.addEventListener("visibilitychange", function () {
      var d = D();
      if (!d || d.visibilityState !== "visible" || !d.getElementById(CARD_ID)) return;
      if (S.busy || (Date.now() - (S.loadedAt || 0)) < REFRESH_MS) return;
      load().then(paint).catch(function () {});
    });
  } catch (e) {}

  // headless-harness surface (also handy from the console)
  W.hermesNetwork = {
    cardHTML: cardHTML, CSS: CSS, lastMap: lastMap, fmtWhen: fmtWhen,
    LOCAL_FACTS: LOCAL_FACTS, mount: mount, paint: paint, state: S, flip: flip,
    refresh: async function () { S.loaded = false; await mount(); }
  };
})();
