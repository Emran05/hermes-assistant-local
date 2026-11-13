// aux_trust.js — Trust & Permissions panel (P1.3).
//
// Auto-served at /aux_trust.js.  Loaded AFTER /expand.js so it can wrap the
// existing Mind-extras entry point (window.mindExtras) instead of editing
// index.html.  Renders one card (#mind-extra-trust) into #view-mind showing the
// 19 permission classes with a 3-state Auto/Ask/Never control, floor locks, an
// untrusted-policy banner, and recent policy decisions.
//
// Reuses the global helpers defined in index.html: esc(), animate() (Motion
// One), revealStagger(), and REDUCE.  All are typeof-guarded so a headless
// render harness (canned fixtures) never throws.  Zero emoji, bespoke SVG only,
// 12-hour time — per CLAUDE.md design laws.

(function () {
  "use strict";

  // ---- self-hook: chain onto the existing Mind-extras entry point ----------
  var prev = window.mindExtras;
  window.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await trustPanel(); } catch (e) {}
  };

  // ---- tiny helpers --------------------------------------------------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
  function RM() {
    if (typeof REDUCE !== "undefined") return !!REDUCE;
    try { return !!(window.matchMedia && matchMedia("(prefers-reduced-motion:reduce)").matches); }
    catch (e) { return false; }
  }
  function doc() { return (typeof document !== "undefined") ? document : null; }
  // iterate an array / NodeList / anything array-like, safely (older WKWebView
  // NodeLists may lack .forEach; slice.call normalizes them).
  function each(list, cb) {
    if (!list) return;
    try { Array.prototype.slice.call(list).forEach(cb); } catch (e) {}
  }

  // absolute 12-hour clock, e.g. "3:42 PM"
  function t12(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) return "";
    var d = new Date(n * 1000), h = d.getHours(), m = d.getMinutes();
    var ap = h >= 12 ? "PM" : "AM";
    h = h % 12; if (h === 0) h = 12;
    return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
  }

  var RISK_LABEL = { critical: "Critical", high: "High", med: "Medium", low: "Low" };
  var RISK_ACCENT = { critical: "var(--bad)", high: "var(--warn)", med: "var(--iris)", low: "var(--ok)" };
  var TIER_IDX = { auto: 0, ask: 1, never: 2 };
  var LOCK_SVG = '<svg class="tp-lock" viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">' +
    '<rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor"/>' +
    '<path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="2"/></svg>';
  var SHIELD_SVG = '<svg class="ic tp-shield" viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M12 2 4 5v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V5z" ' +
    'fill="color-mix(in srgb,var(--iris) 22%,transparent)" stroke="currentColor" stroke-width="1.4"/>' +
    '<path d="M8.6 12.2l2.2 2.2 4.4-4.6" fill="none" stroke="var(--iris)" stroke-width="1.7" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var NEVER_ACK_KEY = "hermes_trust_never_ack";
  function neverAcked() {
    try { return window.localStorage && localStorage.getItem(NEVER_ACK_KEY) === "1"; }
    catch (e) { return false; }
  }
  function markNeverAcked() {
    try { window.localStorage && localStorage.setItem(NEVER_ACK_KEY, "1"); } catch (e) {}
  }

  // ---- one-time CSS --------------------------------------------------------
  function injectCss() {
    var d = doc(); if (!d || d.getElementById("trust-css")) return;
    var s = d.createElement("style");
    s.id = "trust-css";
    s.textContent = [
      "#mind-extra-trust .tp-shield{color:var(--muted)}",
      ".tp-banner{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:12px;margin-bottom:12px;",
      "background:color-mix(in srgb,var(--warn) 14%,transparent);border:1px solid color-mix(in srgb,var(--warn) 40%,transparent)}",
      ".tp-banner .tp-btxt{flex:1;font-size:12.5px;color:var(--ink)}",
      ".tp-banner .tp-btxt b{color:var(--warn)}",
      ".tp-warn-ico{width:18px;height:18px;flex:0 0 auto;color:var(--warn)}",
      ".tp-hint{font-size:12px;color:var(--muted);margin:-2px 0 12px}",
      ".tp-band{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);",
      "margin:14px 0 7px;display:flex;align-items:center;gap:8px}",
      ".tp-band:first-child{margin-top:2px}",
      ".tp-band .tp-brule{flex:1;height:1px;background:var(--hairline)}",
      ".tp-row{display:flex;align-items:center;gap:12px;padding:9px 2px;border-bottom:1px solid var(--hairline)}",
      ".tp-row:last-child{border-bottom:none}",
      // a fixed-width slot, so every row title starts on one left edge whatever
      // the severity word ("Critical" is wider than "Low")
      ".tp-chip{flex:0 0 72px;width:72px;box-sizing:border-box;text-align:center;font-size:11px;font-weight:640;",
      "text-transform:uppercase;letter-spacing:.06em;padding:3px 0;border-radius:99px;",
      "color:var(--rc);background:color-mix(in srgb,var(--rc) 15%,transparent);border:1px solid color-mix(in srgb,var(--rc) 32%,transparent)}",
      ".tp-main{flex:1;min-width:0}",
      ".tp-lbl{font-size:13px;font-weight:560;color:var(--ink);display:flex;align-items:baseline;gap:7px}",
      ".tp-pc{font-weight:400;color:var(--muted)}",
      ".tp-desc{color:var(--muted);margin-top:1px;overflow:hidden;text-overflow:ellipsis}",
      ".tp-recent{color:var(--muted);margin-top:2px}",
      ".tp-seg{position:relative;display:inline-flex;flex:0 0 auto;padding:2px;border-radius:10px;",
      "background:var(--glass-2);border:1px solid var(--hairline)}",
      ".tp-seg b{position:relative;z-index:1;font-weight:560;font-size:12px;color:var(--muted);",
      "padding:4px 0;width:52px;border-radius:8px;cursor:pointer;display:inline-flex;align-items:center;",
      "justify-content:center;gap:4px;user-select:none;transition:color .2s}",
      ".tp-seg b.on{color:var(--ink)}",
      // keyboard: the segment is a radio group with one tab stop
      ".tp-seg b:focus{outline:none}",
      ".tp-seg b:focus-visible{outline:2px solid var(--iris);outline-offset:1px;z-index:2}",
      // locked by the safety floor: a disabled control, so dimmer than the
      // live options but still >= 3:1 on the segment in both themes (the old
      // opacity:.45 was ~1.6:1), with the lock glyph at full strength
      ".tp-seg b.tp-off{cursor:not-allowed;color:color-mix(in srgb,var(--muted) 35%,var(--faint))}",
      // the lock itself is a full-strength muted glyph (>= 3:1 on the segment)
      ".tp-seg b.tp-off .tp-lock{color:var(--muted);flex:0 0 auto}",
      ".tp-vh{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}",
      ".tp-thumb{position:absolute;top:2px;left:2px;width:52px;height:calc(100% - 4px);border-radius:8px;",
      "background:var(--glass);box-shadow:inset 0 1px 0 var(--specular),0 2px 8px -4px var(--cast);",
      "transition:transform .18s cubic-bezier(.22,.61,.36,1);z-index:0}",
      ".tp-eb{color:var(--bad);font-size:11px;margin-top:3px;min-height:0}",
      ".tp-sub{font-size:11px;font-weight:600;color:var(--muted);margin:16px 0 6px;letter-spacing:.02em}",
      ".tp-drow{display:flex;align-items:center;gap:9px;padding:5px 2px;font-size:12px}",
      ".tp-dtime{flex:0 0 auto;color:var(--muted);font-variant-numeric:tabular-nums;width:64px}",
      ".tp-badge{flex:0 0 auto;font-size:9.5px;font-weight:640;text-transform:uppercase;letter-spacing:.03em;",
      "padding:2px 6px;border-radius:5px;color:var(--bd);background:color-mix(in srgb,var(--bd) 16%,transparent)}",
      ".tp-dkey{flex:0 0 auto;color:var(--ink);max-width:38%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".tp-dcmd{flex:1;min-width:0;color:var(--muted);font-family:ui-monospace,Menlo,monospace;font-size:11px;",
      "overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".tp-empty{font-size:12px;color:var(--muted);padding:8px 2px}",
      ".tp-cust{font-size:9.5px;font-weight:640;text-transform:uppercase;letter-spacing:.03em;padding:1px 6px;",
      "border-radius:5px;color:var(--warn);background:color-mix(in srgb,var(--warn) 16%,transparent)}",
      ".tp-cline{color:var(--warn);margin-top:2px}",
      ".tp-prow{display:flex;align-items:center;gap:10px;padding:7px 2px;border-bottom:1px solid var(--hairline)}",
      ".tp-prow:last-child{border-bottom:none}",
      ".tp-pkey{font-size:12.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis}",
      ".tp-prow button{flex:0 0 auto;min-height:32px}",
      ".tp-skel{height:46px;border-radius:10px;margin:8px 0;",
      "background:linear-gradient(90deg,var(--glass-2),var(--glass),var(--glass-2));",
      "background-size:200% 100%;animation:tpsh 1.3s linear infinite}",
      "@keyframes tpsh{0%{background-position:200% 0}100%{background-position:-200% 0}}",
      "@media (prefers-reduced-motion:reduce){.tp-thumb{transition:none}.tp-skel{animation:none}}",
    ].join("\n");
    (d.head || d.body || d.documentElement).appendChild(s);
  }

  // ---- card mount (replaces any existing instance) -------------------------
  function mount(grid, bodyHtml, tinyText) {
    var d = doc(); if (!d) return null;
    var old = d.getElementById("mind-extra-trust");
    if (old && old.remove) old.remove();
    var s = d.createElement("section");
    s.className = "card glass span2";
    s.id = "mind-extra-trust";
    s.innerHTML =
      '<h2>' + SHIELD_SVG + "Trust &amp; Permissions" +
      '<span class="tiny" style="margin-left:auto">' + E(tinyText || "") + "</span></h2>" +
      '<div class="body">' + bodyHtml + "</div>";
    grid.appendChild(s);
    return s;
  }

  // ---- entry point ---------------------------------------------------------
  async function trustPanel() {
    var d = doc(); if (!d) return;
    var grid = d.getElementById("view-mind");
    if (!grid) return;
    injectCss();

    // skeleton first, so the card is present while we fetch
    mount(grid,
      '<div class="tp-skel"></div><div class="tp-skel"></div><div class="tp-skel"></div>', "");

    var data;
    try {
      var r = await fetch("/api/permissions", { cache: "no-store" });
      data = await r.json();
    } catch (e) {
      renderError(grid);
      return;
    }
    if (!data || data.ok === false) { renderError(grid); return; }
    renderData(grid, data);
  }

  function renderError(grid) {
    var s = mount(grid,
      '<div class="tp-empty">Couldn’t load permissions. ' +
      '<button class="ghost" id="tp-retry" style="margin-left:6px">Retry</button></div>', "");
    if (!s) return;
    var b = s.querySelector("#tp-retry");
    if (b) b.addEventListener("click", function () { trustPanel().catch(function () {}); });
  }

  // ---- main render ---------------------------------------------------------
  function renderData(grid, data) {
    var classes = (data && data.classes) || [];
    var trusted = data.trusted !== false;
    var counts = { auto: 0, ask: 0, never: 0 };
    classes.forEach(function (c) { if (counts[c.tier] != null) counts[c.tier]++; });
    var summary = counts.auto + " auto · " + counts.ask + " ask · " + counts.never + " never";
    // C126: pattern-level tiers win over the class tier in decide(), so a
    // class row alone can read "Ask" while one of its patterns auto-runs.
    var overrides = overrideRows(data);
    customCount = overrides.length;
    if (overrides.length) summary += " · " + overrides.length + " custom";

    var html = "";

    // untrusted banner
    if (!trusted) {
      html +=
        '<div class="tp-banner">' +
        '<svg class="tp-warn-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 20h20z" ' +
        'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>' +
        '<path d="M12 9v5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' +
        '<circle cx="12" cy="17.3" r="1.05" fill="currentColor"/></svg>' +
        '<span class="tp-btxt"><b>Policy changed outside the dashboard.</b> ' +
        "Auto-run is suspended until you review it.</span>" +
        '<button class="primary" id="tp-retrust">Review &amp; re-trust</button></div>';
    }

    // subtitle for the defaults-only state
    if (data.exists === false) {
      html += '<div class="tp-hint">Using safe defaults — change any tier to create the policy file. ' +
        "Nothing runs silently until you opt a class into Auto.</div>";
    } else if (data.policy_error) {
      html += '<div class="tp-hint">Policy file could not be parsed (' + E(data.policy_error) +
        ") — safe defaults are in effect.</div>";
    }

    // class rows grouped by risk band
    var band = null;
    classes.forEach(function (c) {
      if (c.risk !== band) {
        band = c.risk;
        html += '<div class="tp-band">' + E(RISK_LABEL[band] || band) +
          '<span class="tp-brule"></span></div>';
      }
      html += classRow(c, overrides.filter(function (o) { return o.class === c.id && !o.invalid; }));
    });

    // every pattern-level tier, with a way to clear it (C126)
    if (overrides.length) {
      html += '<div class="tp-sub">Pattern overrides</div>' +
        '<div class="tp-hint" style="margin:0 0 6px">These win over their class setting above.</div>' +
        overrides.map(overrideRow).join("");
    }

    // recent decisions
    html += '<div class="tp-sub">Recent decisions</div>' + recentList(data.recent || []);

    // footnote
    html += '<div class="tp-hint" style="margin:12px 0 0">This governs the dashboard chat surface. ' +
      "Telegram and CLI keep Hermes’s own manual approvals.</div>";

    var s = mount(grid, html, summary);
    if (!s) return;
    wire(s, classes, data, overrides);

    // staggered reveal (frozen under reduced-motion)
    try {
      if (typeof revealStagger === "function") revealStagger(s.querySelectorAll(".tp-row"), 34);
    } catch (e) {}
  }

  function recentText(rc) {
    rc = rc || {};
    var parts = [];
    if (rc.asked) parts.push(rc.asked + " asked");
    if (rc["auto-approved"]) parts.push(rc["auto-approved"] + " auto-run");
    if (rc["auto-denied"]) parts.push(rc["auto-denied"] + " blocked");
    return parts.length ? parts.join(" · ") + " this week" : "";
  }

  // the payload's display rows; an older server only sent `patterns`
  function overrideRows(data) {
    var rows = data && data.pattern_overrides;
    if (Array.isArray(rows)) return rows;
    var pats = (data && data.patterns) || {};
    return Object.keys(pats).map(function (k) {
      return { pattern: k, tier: pats[k], "class": "", class_label: "", effective: pats[k] };
    });
  }

  var TIER_WORD = { auto: "Auto", ask: "Ask", never: "Never" };
  var customCount = 0;     // pattern overrides on the last render, for updateSummary
  function tierWord(t) { return TIER_WORD[t] || String(t || ""); }

  function overrideRow(o, i) {
    var col = o.tier === "auto" ? "--warn" : (o.tier === "never" ? "--bad" : "--muted");
    var note = (o.class_label ? "overrides " + o.class_label +
      (o.class_tier ? " (class: " + tierWord(o.class_tier) + ")" : "") : "no class match — asks");
    if (o.invalid) {
      // C126: a hand-edited key with stray spaces never matches an approval
      col = "--muted";
      note = (o.clamped_by || "never matches") + " · " +
        (o.class_label ? o.class_label + " runs as " + tierWord(o.effective) : "its class decides") +
        " · Clear removes it";
    } else if (o.effective && o.effective !== o.tier)
      note += " · runs as " + tierWord(o.effective) + (o.clamped_by ? " — " + o.clamped_by : "");
    var key = o.invalid ? "\u201c" + o.pattern + "\u201d" : o.pattern;
    return '<div class="tp-prow"' + (o.invalid ? ' data-invalid="1"' : "") + ">" +
      '<span class="tp-badge" style="--bd:var(' + col + ')">' +
      E(o.invalid ? "Ignored" : tierWord(o.tier)) + "</span>" +
      '<div class="tp-main"><div class="tp-pkey"' + (o.invalid ? ' style="white-space:pre"' : "") +
      ">" + E(key) + "</div>" +
      '<div class="tp-desc tiny">' + E(note) + "</div></div>" +
      '<button class="ghost" data-pclear="' + i + '">Clear</button></div>';
  }

  function classRow(c, ovr) {
    var accent = RISK_ACCENT[c.risk] || "var(--iris)";
    var idx = TIER_IDX[c.tier] != null ? TIER_IDX[c.tier] : 1;
    var autoAllowed = c.auto_allowed !== false;
    var rc = recentText(c.recent);
    ovr = ovr || [];
    var cust = "";
    if (ovr.length) {
      cust = ovr.length + " pattern" + (ovr.length === 1 ? "" : "s") + " set differently: " +
        ovr.map(function (o) { return o.pattern + " → " + tierWord(o.tier); }).join("; ");
    }

    // Each segment is a radio group (keyboard: one tab stop on the chosen
    // option, arrows move between the options, Space/Enter picks). The
    // option's accessible name carries the rule's name.
    var stop = tabStop(c.tier, autoAllowed);
    function segB(tier, label) {
      var name = label + " for " + (c.label || "this class");
      if (tier === "auto" && !autoAllowed) {
        // the reason is a tooltip AND the element's accessible description
        var why = "Auto is locked for " + (c.label || "this class") +
          ": its safety floor is Ask, so it can never run silently.";
        var did = "tp-why-" + String(c.id || "").replace(/[^A-Za-z0-9_-]/g, "_");
        return '<b class="tp-off" data-tier="auto" role="radio" aria-checked="false" tabindex="-1"' +
          ' aria-label="' + E(name + " (locked)") + '" aria-disabled="true" aria-describedby="' + E(did) +
          '" title="' + E(why) + '">' + label + " " + LOCK_SVG +
          '<span class="tp-vh" id="' + E(did) + '">' + E(why) + "</span></b>";
      }
      return '<b data-tier="' + tier + '"' + (c.tier === tier ? ' class="on"' : "") +
        ' role="radio" aria-checked="' + (c.tier === tier ? "true" : "false") + '"' +
        ' tabindex="' + (tier === stop ? "0" : "-1") + '" aria-label="' + E(name) + '">' + label + "</b>";
    }

    return '<div class="tp-row" data-class="' + E(c.id) + '" data-tier="' + E(c.tier) +
      '" data-autoallowed="' + (autoAllowed ? "1" : "0") + '">' +
      '<span class="tp-chip" style="--rc:' + accent + '">' + E(RISK_LABEL[c.risk] || c.risk) + "</span>" +
      '<div class="tp-main">' +
      '<div class="tp-lbl">' + E(c.label) +
      '<span class="tp-pc tiny">' + (c.pattern_count || 0) + " pattern" + (c.pattern_count === 1 ? "" : "s") + "</span>" +
      (cust ? '<span class="tp-cust" title="' + E(cust) + '">custom</span>' : "") + "</div>" +
      '<div class="tp-desc tiny">' + E(c.desc) + "</div>" +
      (cust ? '<div class="tp-cline tiny">' + E(cust) + "</div>" : "") +
      (rc ? '<div class="tp-recent tiny">' + E(rc) + "</div>" : "") +
      "</div>" +
      '<div class="tp-seg" role="radiogroup" aria-label="' + E((c.label || "This class") + ": when to run") + '">' +
      '<span class="tp-thumb" style="transform:translateX(' + (idx * 100) + '%)"></span>' +
      segB("auto", "Auto") + segB("ask", "Ask") + segB("never", "Never") +
      "</div>" +
      '<div class="tp-eb" data-eb="' + E(c.id) + '"></div></div>';
  }

  // Every action hermes_rpc / aux_shortcuts / permissions.py write to the
  // audit log (permissions.audit() lists them). An action missing here used to
  // render as its raw id on a muted badge (R2-16).
  var ACT_BADGE = {
    "auto-approved": ["Auto", "--ok"], "asked": ["Ask", "--warn"],
    "auto-denied": ["Blocked", "--bad"], "user-approve": ["Approved", "--ok"],
    "user-deny": ["Denied", "--bad"], "policy-error": ["Error", "--bad"],
    "policy-change": ["Change", "--iris"],
    "expired": ["Expired", "--muted"], "timeout": ["Timed out", "--warn"],
    "desync-denied": ["Denied (out of sync)", "--bad"],
    "stopped-denied": ["Denied (you stopped)", "--bad"],
    "respond-failed": ["Not delivered", "--bad"],
  };

  function recentList(recent) {
    if (!recent || !recent.length) {
      return '<div class="tp-empty">No policy decisions yet — the first time Hermes ' +
        "needs approval, it lands here.</div>";
    }
    var rows = recent.slice(0, 8).map(function (e) {
      var meta = ACT_BADGE[e.action] || [e.action || "", "--muted"];
      var key = e.pattern_key || e.op || e.action || "";
      var cmd = (e.command || "").slice(0, 200);
      return '<div class="tp-drow">' +
        '<span class="tp-dtime">' + E(t12(e.ts)) + "</span>" +
        '<span class="tp-badge" style="--bd:var(' + meta[1] + ')">' + E(meta[0]) + "</span>" +
        '<span class="tp-dkey">' + E(key) + "</span>" +
        (cmd ? '<code class="tp-dcmd">' + E(cmd) + "</code>" : "") + "</div>";
    }).join("");
    return rows;
  }

  // ---- interactivity -------------------------------------------------------
  function slideThumb(seg, idx) {
    var thumb = seg.querySelector(".tp-thumb");
    if (!thumb) return;
    var tx = "translateX(" + (idx * 100) + "%)";
    if (RM() || typeof animate !== "function") { thumb.style.transform = tx; return; }
    var a;
    try { a = animate(thumb, { transform: tx }, { duration: 0.18, easing: [0.22, 0.61, 0.36, 1] }); }
    catch (e) { a = null; }
    if (!a) thumb.style.transform = tx;
  }

  // the option that takes the group's one tab stop: the chosen tier, unless
  // it is the floor-locked Auto, then Ask
  function tabStop(tier, autoAllowed) {
    if (TIER_IDX[tier] == null) tier = "ask";
    if (tier === "auto" && !autoAllowed) tier = "ask";
    return tier;
  }

  function setActive(seg, tier) {
    var stop = null;
    each(seg.querySelectorAll("b[data-tier]"), function (b) {
      var t = b.getAttribute("data-tier");
      var off = b.classList && b.classList.contains("tp-off");
      if (t === tier) b.classList.add("on");
      else b.classList.remove("on");
      if (!off) {
        b.setAttribute("aria-checked", t === tier ? "true" : "false");
        if (t === tier) stop = t;
      }
    });
    if (stop == null) stop = "ask";
    each(seg.querySelectorAll("b[data-tier]"), function (b) {
      var off = b.classList && b.classList.contains("tp-off");
      b.setAttribute("tabindex", (!off && b.getAttribute("data-tier") === stop) ? "0" : "-1");
    });
    slideThumb(seg, TIER_IDX[tier] != null ? TIER_IDX[tier] : 1);
  }

  async function postJSON(url, body) {
    var r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    try { return await r.json(); } catch (e) { return { ok: false, error: "bad response" }; }
  }

  // What a re-trust would bless, Autos first. "Review & re-trust" used to POST
  // the moment it was clicked, re-signing an out-of-band edit nobody had seen
  // (C126). When the payload carries `signed` (the last policy the dashboard
  // signed, verified against the sidecar hash) the review is the DIFF against
  // it — what the outside edit changed — plus the already-signed Autos that a
  // re-trust resumes; without one it lists every class tier off its shipped
  // default and every pattern-level tier.
  // `what` (optional) names a single edit about to be saved over an UNTRUSTED
  // policy: every save re-signs the whole file (permissions.py _save), so a
  // Clear or a class change there re-trusts the outside edit too.
  // "ignored": a key with stray spaces (o.invalid) never matches an approval,
  // so it is reviewed as what it is, not counted as an Auto that will run
  var CHANGE_RANK = { auto: 0, ask: 1, removed: 1, ignored: 1, never: 2 };
  function changeTier(o) { return o.invalid ? "ignored" : o.tier; }
  function ignoredNote(o) { return o.invalid ? " \u2014 ignored, never matches" : ""; }
  function own(o, k) { return !!o && Object.prototype.hasOwnProperty.call(o, k); }
  function patLabel(o) {
    return "pattern \u201c" + o.pattern + "\u201d" + (o.class_label ? " (" + o.class_label + ")" : "");
  }
  function retrustSummary(data, overrides, what) {
    var lines = [], resumed = [];
    var classes = (data && data.classes) || [];
    var signed = data && data.signed && typeof data.signed === "object" ? data.signed : null;
    if (signed) {
      var sc = signed.classes || {}, sp = signed.patterns || {}, now = {};
      classes.forEach(function (c) {
        var was = own(sc, c.id) ? sc[c.id] : c["default"];
        if (c.tier !== was)
          lines.push({ t: c.tier, s: c.label + ": " + tierWord(was) + " \u2192 " + tierWord(c.tier) });
        else if (c.tier === "auto") resumed.push(c.label + ": Auto");
      });
      (overrides || []).forEach(function (o) {
        now[o.pattern] = true;
        if (!own(sp, o.pattern))
          lines.push({ t: changeTier(o), s: patLabel(o) + ": " + tierWord(o.tier) + " (new)" +
                       ignoredNote(o) });
        else if (sp[o.pattern] !== o.tier)
          lines.push({ t: changeTier(o), s: patLabel(o) + ": " + tierWord(sp[o.pattern]) +
                       " \u2192 " + tierWord(o.tier) + ignoredNote(o) });
        else if (o.tier === "auto" && !o.invalid) resumed.push(patLabel(o) + ": Auto");
      });
      Object.keys(sp).forEach(function (k) {
        if (!own(now, k))
          lines.push({ t: "removed", s: "pattern \u201c" + k + "\u201d: removed (was " +
                       tierWord(sp[k]) + ")" });
      });
    } else {
      classes.forEach(function (c) {
        if (c.tier !== c["default"])
          lines.push({ t: c.tier, s: c.label + ": " + tierWord(c.tier) +
                       " (default " + tierWord(c["default"]) + ")" });
      });
      (overrides || []).forEach(function (o) {
        lines.push({ t: changeTier(o), s: patLabel(o) + ": " + tierWord(o.tier) + ignoredNote(o) });
      });
    }
    lines.sort(function (a, b) { return (CHANGE_RANK[a.t] || 0) - (CHANGE_RANK[b.t] || 0); });
    var autos = lines.filter(function (l) { return l.t === "auto"; }).length;
    var what_in = signed ? "the changes below" : "everything below";
    var head = (what
      ? "The policy file was changed outside the dashboard. " + what + " saves it, which " +
        "also re-trusts it and puts " + what_in + " into effect"
      : "The policy file was changed outside the dashboard. Re-trusting it puts " +
        what_in + " into effect") +
      (signed ? " (compared with the last policy the dashboard signed)" : "") +
      (autos ? " — including " + autos + " Auto (runs without asking)" : "") + ".";
    var body = lines.length ? lines.map(function (l) { return "\u2022 " + l.s; }).join("\n")
      : (signed ? "Nothing differs from the last signed policy."
                : "Nothing differs from the shipped defaults.");
    if (resumed.length)
      body += "\n\nAuto-run also resumes for what was already signed:\n" +
        resumed.map(function (r) { return "\u2022 " + r; }).join("\n");
    return head + "\n\n" + body + "\n\n" + (what ? "Save and re-trust?" : "Re-trust this policy?");
  }

  // true when a save may go ahead: always over a trusted policy; over an
  // untrusted one only after the same review "Review & re-trust" shows (fail
  // closed — no confirm(), no save)
  function untrustedSaveOk(data, overrides, what) {
    if (!data || data.trusted !== false) return true;
    try { return typeof confirm === "function" && !!confirm(retrustSummary(data, overrides, what)); }
    catch (e) { return false; }
  }

  function wire(card, classes, data, overrides) {
    var d = doc(); if (!d) return;

    var retrust = card.querySelector("#tp-retrust");
    if (retrust) retrust.addEventListener("click", async function () {
      var ok = false;
      try { ok = typeof confirm === "function" && confirm(retrustSummary(data, overrides)); }
      catch (e) { ok = false; }
      if (!ok) return;                 // fail closed: no review, no re-trust
      retrust.disabled = true;
      // E115: bind the re-trust to the file this review was built from; the
      // server refuses (409) if it changed since, and the re-render below
      // shows the new file for a fresh review
      var rr = null;
      try { rr = await postJSON("/api/permissions", { op: "retrust", sha: data && data.sha }); }
      catch (e) {}
      if (rr && rr.stale && typeof alert === "function") {
        try { alert("The policy file changed while you were reviewing it. Nothing was re-trusted — review it again."); }
        catch (e) {}
      }
      trustPanel().catch(function () {});
    });

    each(card.querySelectorAll("[data-pclear]"), function (b) {
      var o = (overrides || [])[Number(b.getAttribute("data-pclear"))];
      if (!o) return;
      b.addEventListener("click", async function () {
        var rest = (overrides || []).filter(function (x) { return x !== o; });
        if (!untrustedSaveOk(data, rest, "Clearing “" + o.pattern + "”")) return;
        b.disabled = true;
        try { await postJSON("/api/permissions", { op: "clear_pattern", pattern: o.pattern,
                                                   sha: data && data.sha }); }
        catch (e) {}
        trustPanel().catch(function () {});
      });
    });

    each(card.querySelectorAll(".tp-row"), function (row) {
      var seg = row.querySelector(".tp-seg");
      if (!seg) return;
      each(seg.querySelectorAll("b[data-tier]"), function (b) {
        if (b.classList && b.classList.contains("tp-off")) return;   // floor-locked
        b.addEventListener("click", function () { onPick(row, seg, b, data, overrides); });
      });
      seg.addEventListener("keydown", function (ev) { onSegKey(ev, row, seg, data, overrides); });
    });
  }

  // keyboard for one segment: arrows / Home / End move focus between the
  // options the floor allows (the locked Auto is skipped); Space or Enter
  // picks the focused option through the same path as a click. A held key
  // (KeyboardEvent.repeat) never picks twice.
  function onSegKey(ev, row, seg, data, overrides) {
    if (!ev) return;
    var opts = [];
    each(seg.querySelectorAll("b[data-tier]"), function (b) {
      if (!(b.classList && b.classList.contains("tp-off"))) opts.push(b);
    });
    if (!opts.length) return;
    var d = doc();
    var cur = opts.indexOf(ev.target);
    if (cur < 0 && d) cur = opts.indexOf(d.activeElement);
    var k = ev.key, next = -1;
    if (k === "ArrowRight" || k === "ArrowDown") next = cur < 0 ? 0 : Math.min(opts.length - 1, cur + 1);
    else if (k === "ArrowLeft" || k === "ArrowUp") next = cur < 0 ? 0 : Math.max(0, cur - 1);
    else if (k === "Home") next = 0;
    else if (k === "End") next = opts.length - 1;
    else if (k === " " || k === "Spacebar" || k === "Enter") {
      if (ev.preventDefault) ev.preventDefault();
      if (ev.repeat || cur < 0) return;
      onPick(row, seg, opts[cur], data, overrides);
      return;
    } else return;
    if (ev.preventDefault) ev.preventDefault();
    opts.forEach(function (b, i) { b.setAttribute("tabindex", i === next ? "0" : "-1"); });
    try { opts[next].focus(); } catch (e) {}
  }

  async function onPick(row, seg, b, data, overrides) {
    var cls = row.getAttribute("data-class");
    var tier = b.getAttribute("data-tier");
    var prevTier = row.getAttribute("data-tier");
    if (tier === prevTier) return;

    // over an untrusted policy this save re-signs the whole file — review first
    var untrusted = !!data && data.trusted === false;
    if (untrusted) {
      var after = {
        trusted: false,
        signed: data.signed,
        classes: ((data && data.classes) || []).map(function (c) {
          if (c.id !== cls) return c;
          var n = {}; for (var k in c) n[k] = c[k];
          n.tier = tier;
          return n;
        })
      };
      if (!untrustedSaveOk(after, overrides, "Setting this class to " + tierWord(tier))) return;
    }

    // first-ever Never: confirm (works in the WKWebView JS-dialog fix)
    if (tier === "never" && !neverAcked() && typeof confirm === "function") {
      if (!confirm("Hermes will be refused these actions automatically — set Never?")) {
        return;   // leave state untouched
      }
      markNeverAcked();
    }

    // optimistic
    setActive(seg, tier);
    row.setAttribute("data-tier", tier);
    var eb = row.querySelector(".tp-eb");
    if (eb) eb.textContent = "";

    var res;
    try { res = await postJSON("/api/permissions", { op: "set_class", "class": cls, tier: tier,
                                                     sha: data && data.sha }); }
    catch (e) { res = { ok: false, error: "network error" }; }

    if (res && res.stale) {
      // E115: the file changed since the review this save was approved on —
      // nothing was saved; re-render so the owner reviews the new file
      trustPanel().catch(function () {});
      return;
    }
    if (!res || res.ok === false) {
      // snap back
      setActive(seg, prevTier);
      row.setAttribute("data-tier", prevTier);
      if (eb) {
        eb.textContent = (res && res.error) ? res.error : "Couldn’t save.";
        setTimeout(function () { if (eb) eb.textContent = ""; }, 3000);
      }
      return;
    }
    // the save re-signed the file: re-render without the untrusted banner
    if (untrusted) { trustPanel().catch(function () {}); return; }
    // refresh header summary from the authoritative response
    updateSummary(res.classes);
  }

  function updateSummary(classes) {
    if (!classes || !classes.length) return;
    var d = doc(); if (!d) return;
    var card = d.getElementById("mind-extra-trust");
    if (!card) return;
    var tiny = card.querySelector("h2 .tiny");
    if (!tiny) return;
    var counts = { auto: 0, ask: 0, never: 0 };
    classes.forEach(function (c) { if (counts[c.tier] != null) counts[c.tier]++; });
    tiny.textContent = counts.auto + " auto · " + counts.ask + " ask · " + counts.never + " never" +
      (customCount ? " · " + customCount + " custom" : "");   // a class change leaves patterns alone
  }

  // expose for the headless render harness / manual invocation
  window.trustPanel = trustPanel;
})();
