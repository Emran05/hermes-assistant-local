// aux_watchtower.js — Watchtower management card + hub signal lane (P2.1).
//
// Auto-served at /aux_watchtower.js.  Loaded AFTER /expand.js and the other aux
// JS so it can wrap the existing Mind-extras entry point (window.mindExtras)
// instead of editing index.html.  Renders one card (#mind-extra-watchtower)
// into #view-mind: rule CRUD, a test-rule live preview, per-rule precision
// meters, useful/noise reactions, mute, and quiet-hours/cap/8am-brief controls.
//
// Reuses global helpers from index.html: esc(), animate() (Motion One),
// revealStagger(), REDUCE — all typeof-guarded so a headless render harness
// never throws.  Zero emoji, bespoke SVG only, 12-hour time (CLAUDE.md laws).

(function () {
  "use strict";

  // ---- self-hook: chain onto the existing Mind-extras entry point ----------
  var prev = window.mindExtras;
  window.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await watchtowerPanel(); } catch (e) {}
  };

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
  function num(v, d) { var n = Number(v); return isFinite(n) ? n : (d == null ? 0 : d); }

  // absolute 12-hour clock, e.g. "3:42 PM"
  function t12(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) return "";
    var d = new Date(n * 1000), h = d.getHours(), m = d.getMinutes();
    var ap = h >= 12 ? "PM" : "AM";
    h = h % 12; if (h === 0) h = 12;
    return h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
  }
  function pct(v) {
    var n = Number(v);
    if (!isFinite(n)) return "?";
    return (n >= 0 ? "+" : "") + n.toFixed(1) + "%";
  }

  var TYPE_META = {
    ticker_move:   { label: "Stock move", cat: "markets" },
    index_move:    { label: "Index move", cat: "markets" },
    crypto_move:   { label: "Crypto move", cat: "markets" },
    system_metric: { label: "System metric", cat: "system" },
    rss_keyword:   { label: "News keyword", cat: "news" },
    email_important: { label: "Important email", cat: "comms" },
    calendar_gap:  { label: "Calendar gap", cat: "life" },
    agent_run_done: { label: "Agent run done", cat: "agent" },
  };
  var CAT_ACCENT = {
    markets: "var(--ok)", system: "var(--iris)", news: "var(--warn)",
    comms: "var(--iris)", life: "var(--ok)", agent: "var(--iris)",
  };

  function typeGlyph(cat) {
    var c = CAT_ACCENT[cat] || "var(--iris)";
    if (cat === "markets")
      return svg('<path d="M3 16l5-5 4 4 8-9" fill="none" stroke="' + c +
        '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
        '<path d="M17 6h4v4" fill="none" stroke="' + c +
        '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>');
    if (cat === "system")
      return svg('<rect x="7" y="7" width="10" height="10" rx="2" fill="none" stroke="' + c +
        '" stroke-width="1.7"/><path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3" ' +
        'stroke="' + c + '" stroke-width="1.6" stroke-linecap="round"/>');
    if (cat === "news")
      return svg('<rect x="4" y="4" width="16" height="16" rx="2" fill="none" stroke="' + c +
        '" stroke-width="1.6"/><path d="M7 9h10M7 12h10M7 15h6" stroke="' + c +
        '" stroke-width="1.5" stroke-linecap="round"/>');
    return svg('<circle cx="12" cy="12" r="8" fill="none" stroke="' + c + '" stroke-width="1.7"/>' +
      '<path d="M12 8v4l3 2" fill="none" stroke="' + c + '" stroke-width="1.7" stroke-linecap="round"/>');
  }
  function svg(inner) {
    return '<svg class="wt-glyph" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">' +
      inner + "</svg>";
  }
  var EYE_SVG = '<svg class="ic wt-eye" viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" fill="none" ' +
    'stroke="currentColor" stroke-width="1.5"/>' +
    '<circle cx="12" cy="12" r="3.2" fill="color-mix(in srgb,var(--iris) 30%,transparent)" ' +
    'stroke="var(--iris)" stroke-width="1.5"/></svg>';

  // ---- one-time CSS --------------------------------------------------------
  function injectCss() {
    var d = doc(); if (!d || d.getElementById("watchtower-css")) return;
    var s = d.createElement("style");
    s.id = "watchtower-css";
    s.textContent = [
      "#mind-extra-watchtower .wt-eye{color:var(--muted)}",
      ".wt-controls{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px}",
      ".wt-master{margin-bottom:10px}",
      ".wt-master .wt-ctl{background:color-mix(in srgb,var(--iris) 9%,var(--glass-2));",
      "border-color:color-mix(in srgb,var(--iris) 30%,var(--hairline))}",
      ".wt-ctl{flex:1 1 150px;min-width:140px;background:var(--glass-2);border:1px solid var(--hairline);",
      "border-radius:12px;padding:9px 11px}",
      ".wt-ctl label{display:block;font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;",
      "color:var(--faint);margin-bottom:5px}",
      ".wt-ctl .wt-row2{display:flex;align-items:center;gap:8px}",
      ".wt-inp,.wt-sel{background:var(--glass);border:1px solid var(--hairline);border-radius:8px;",
      "color:var(--ink);font-size:12.5px;padding:5px 8px;min-width:0;width:100%;font-family:inherit}",
      ".wt-inp.wt-time{width:64px;text-align:center;font-variant-numeric:tabular-nums}",
      ".wt-inp.wt-nn{width:70px}",
      ".wt-band{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);",
      "margin:16px 0 8px;display:flex;align-items:center;gap:8px}",
      ".wt-band .wt-brule{flex:1;height:1px;background:var(--hairline)}",
      ".wt-row{display:flex;align-items:center;gap:11px;padding:9px 2px;border-bottom:1px solid var(--hairline)}",
      ".wt-row:last-child{border-bottom:none}",
      ".wt-gl{flex:0 0 auto;width:30px;height:30px;border-radius:9px;display:flex;align-items:center;",
      "justify-content:center;background:var(--glass-2);border:1px solid var(--hairline)}",
      ".wt-main{flex:1;min-width:0}",
      ".wt-lbl{font-size:13px;font-weight:560;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".wt-sub{font-size:11px;color:var(--muted);margin-top:1px;display:flex;gap:8px;flex-wrap:wrap}",
      ".wt-meter{flex:0 0 auto;width:74px}",
      ".wt-meter .wt-mtrack{height:5px;border-radius:99px;background:var(--glass-2);overflow:hidden;border:1px solid var(--hairline)}",
      ".wt-meter .wt-mfill{height:100%;border-radius:99px;background:var(--ok)}",
      ".wt-meter .wt-mtxt{font-size:9.5px;color:var(--faint);margin-top:3px;text-align:center;letter-spacing:.02em}",
      // .wt-tog is now the SAME switch as aux_prefs' .pf-tog — the canonical
      // rule (38x22, 18px knob, on = --iris, knob = --iris-ink, 150ms on
      // background-color/transform only) lives in index.html. This module used
      // to paint it green on a 16px knob at 180ms; re-declaring anything here
      // would win on source order and re-fork the two implementations.

      ".wt-icobtn{flex:0 0 auto;width:26px;height:26px;border-radius:7px;border:1px solid var(--hairline);",
      "background:var(--glass-2);color:var(--muted);cursor:pointer;display:inline-flex;align-items:center;",
      "justify-content:center;padding:0}",
      ".wt-icobtn:hover{color:var(--ink)}",
      ".wt-icobtn.wt-danger:hover{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,transparent)}",
      ".wt-add{background:var(--glass-2);border:1px solid var(--hairline);border-radius:13px;padding:12px;margin-top:4px}",
      ".wt-add .wt-fields{display:flex;flex-wrap:wrap;gap:9px;margin:9px 0}",
      ".wt-add .wt-fld{display:flex;flex-direction:column;gap:4px}",
      ".wt-add .wt-fld label{font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--faint)}",
      ".wt-add .wt-actions{display:flex;gap:8px;align-items:center;margin-top:4px}",
      ".wt-btn{font-family:inherit;font-size:12px;font-weight:560;padding:6px 13px;border-radius:9px;cursor:pointer;",
      "border:1px solid var(--hairline);background:var(--glass);color:var(--ink)}",
      ".wt-btn.primary{background:color-mix(in srgb,var(--iris) 24%,transparent);border-color:color-mix(in srgb,var(--iris) 45%,transparent)}",
      ".wt-btn:disabled{opacity:.5;cursor:default}",
      ".wt-preview{margin-top:9px;font-size:12px;border-radius:9px;padding:9px 11px;",
      "background:var(--glass);border:1px solid var(--hairline);white-space:pre-wrap;color:var(--muted)}",
      ".wt-preview b{color:var(--ink)}",
      ".wt-preview .wt-yes{color:var(--ok)}.wt-preview .wt-no{color:var(--faint)}",
      ".wt-fire{display:flex;gap:9px;padding:7px 2px;font-size:12px;align-items:flex-start}",
      ".wt-fire .wt-ft{flex:0 0 auto;color:var(--faint);width:64px;font-variant-numeric:tabular-nums;padding-top:1px}",
      ".wt-fire .wt-fb{flex:1;min-width:0}",
      ".wt-fire .wt-fl{color:var(--ink);font-weight:540}",
      ".wt-fire .wt-fx{color:var(--muted);margin-top:1px;overflow:hidden;text-overflow:ellipsis}",
      ".wt-fire .wt-react{display:flex;gap:5px;flex:0 0 auto}",
      ".wt-react .wt-rb{font-size:10.5px;padding:3px 8px;border-radius:7px;border:1px solid var(--hairline);",
      "background:var(--glass-2);color:var(--muted);cursor:pointer}",
      ".wt-react .wt-rb.on-useful{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 45%,transparent)}",
      ".wt-react .wt-rb.on-noise{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,transparent)}",
      ".wt-empty{font-size:12px;color:var(--muted);padding:8px 2px}",
      ".wt-eb{color:var(--bad);font-size:11.5px;margin:6px 0;min-height:0}",
      ".wt-hint{font-size:11.5px;color:var(--muted);margin:2px 0 12px}",
      ".wt-skel{height:44px;border-radius:11px;margin:8px 0;",
      "background:linear-gradient(90deg,var(--glass-2),var(--glass),var(--glass-2));",
      "background-size:200% 100%;animation:wtsh 1.3s linear infinite}",
      "@keyframes wtsh{0%{background-position:200% 0}100%{background-position:-200% 0}}",
      ".wt-glyph{display:block}",
      "@media (prefers-reduced-motion:reduce){.wt-skel{animation:none}}",   // toggle: see index.html
    ].join("\n");
    (d.head || d.body || d.documentElement).appendChild(s);
  }

  // ---- card mount (replaces any existing instance) -------------------------
  function mount(grid, bodyHtml, tinyText) {
    var d = doc(); if (!d) return null;
    var old = d.getElementById("mind-extra-watchtower");
    if (old && old.remove) old.remove();
    var s = d.createElement("section");
    s.className = "card glass span2";
    s.id = "mind-extra-watchtower";
    s.innerHTML =
      "<h2>" + EYE_SVG + "Watchtower" +
      '<span class="tiny" style="margin-left:auto">' + E(tinyText || "") + "</span></h2>" +
      '<div class="body">' + bodyHtml + "</div>";
    grid.appendChild(s);
    return s;
  }

  // ---- entry point ---------------------------------------------------------
  async function watchtowerPanel() {
    var d = doc(); if (!d) return;
    var grid = d.getElementById("view-mind");
    if (!grid) return;
    injectCss();
    mount(grid, '<div class="wt-skel"></div><div class="wt-skel"></div><div class="wt-skel"></div>', "");

    var data;
    try {
      var r = await fetch("/api/watchtower", { cache: "no-store" });
      data = await r.json();
    } catch (e) { renderError(grid); return; }
    if (!data || data.ok === false) { renderError(grid); return; }
    renderData(grid, data);
  }

  function renderError(grid) {
    var s = mount(grid,
      '<div class="wt-empty">Couldn’t load Watchtower. ' +
      '<button class="wt-btn" id="wt-retry" style="margin-left:6px">Retry</button></div>', "");
    if (!s) return;
    var b = s.querySelector("#wt-retry");
    if (b) b.addEventListener("click", function () { watchtowerPanel().catch(function () {}); });
  }

  // ---- main render ---------------------------------------------------------
  function renderData(grid, data) {
    var rules = data.rules || [];
    var stats = data.stats || {};
    var brief = data.brief || {};
    var qh = data.quiet_hours || {};
    var enabledCount = rules.filter(function (r) { return r.enabled; }).length;
    var tiny = rules.length + (rules.length === 1 ? " rule · " : " rules · ") + enabledCount + " on";

    var html = "";
    html += masterHtml(data.master || {});
    html += controlsHtml(brief, qh, data.daily_cap, data.midday || {}, data.breaking || {},
      data.evening || {});
    html += '<div class="wt-eb" id="wt-ctl-eb"></div>';
    html += '<div class="wt-band">Watches<span class="wt-brule"></span></div>';
    html += rulesHtml(rules, stats);
    html += '<div class="wt-band">Add a watch<span class="wt-brule"></span></div>';
    html += composerHtml();
    html += '<div class="wt-band">Recent fires<span class="wt-brule"></span></div>';
    html += recentHtml(data.recent || []);
    html += '<div class="wt-hint">Watchtower only notifies — it never runs a tool, never acts, ' +
      "and delivers to your Telegram home channel only. The two switches up top silence all " +
      "daily briefings or all news updates in one flip; quiet hours, per-rule cooldown, and a " +
      "daily cap keep the rest polite.</div>";

    var s = mount(grid, html, tiny);
    if (!s) return;
    wire(s, rules, brief);
    try {
      if (typeof revealStagger === "function") revealStagger(s.querySelectorAll(".wt-row"), 30);
    } catch (e) {}
  }

  function masterHtml(master) {
    var bOn = master.briefings !== false;
    var nOn = master.news !== false;
    return '<div class="wt-controls wt-master">' +
      '<div class="wt-ctl"><label>Daily briefings</label><div class="wt-row2">' +
      toggle("wt-master-briefs", bOn) +
      '<span class="tiny" style="color:var(--faint)">8am · midday · evening</span></div></div>' +
      '<div class="wt-ctl"><label>News updates</label><div class="wt-row2">' +
      toggle("wt-master-news", nOn) +
      '<span class="tiny" style="color:var(--faint)">breaking · news keywords</span></div></div></div>';
  }

  function controlsHtml(brief, qh, cap, midday, breaking, evening) {
    var briefOn = brief.enabled !== false;
    var h = num(brief.hour, 8), m = num(brief.minute, 0);
    var hh = (h < 10 ? "0" : "") + h, mm = (m < 10 ? "0" : "") + m;
    var midOn = midday.enabled !== false;
    var mh = num(midday.hour, 15), mmin = num(midday.minute, 0);
    var mhh = (mh < 10 ? "0" : "") + mh, mmm = (mmin < 10 ? "0" : "") + mmin;
    var brkOn = breaking.enabled !== false;
    var brkQuiet = breaking.override_quiet !== false;
    var eveOn = evening.enabled !== false;
    var eh = num(evening.hour, 18), emin = num(evening.minute, 0);
    var ehh = (eh < 10 ? "0" : "") + eh, emm = (emin < 10 ? "0" : "") + emin;
    return '<div class="wt-controls">' +
      '<div class="wt-ctl"><label>8am World Brief</label><div class="wt-row2">' +
      toggle("wt-brief-on", briefOn) +
      '<input class="wt-inp wt-time" id="wt-brief-time" value="' + E(hh + ":" + mm) +
      '" placeholder="08:00"></div></div>' +
      '<div class="wt-ctl"><label>Midday pulse</label><div class="wt-row2">' +
      toggle("wt-mid-on", midOn) +
      '<input class="wt-inp wt-time" id="wt-mid-time" value="' + E(mhh + ":" + mmm) +
      '" placeholder="15:00" title="11:00-17:59. Sends only when something noteworthy changed"></div></div>' +
      '<div class="wt-ctl"><label>Evening wrap</label><div class="wt-row2">' +
      toggle("wt-eve-on", eveOn) +
      '<input class="wt-inp wt-time" id="wt-eve-time" value="' + E(ehh + ":" + emm) +
      '" placeholder="18:00" title="16:00-23:59. Short end-of-day wrap; sends only when noteworthy"></div></div>' +
      '<div class="wt-ctl"><label>Breaking alerts</label><div class="wt-row2">' +
      toggle("wt-brk-on", brkOn) +
      '<span class="tiny" style="color:var(--faint)">cap ' +
      E(String(num(breaking.daily_cap, 5))) + "/day</span></div>" +
      '<div class="wt-row2" style="margin-top:6px">' +
      toggle("wt-brk-quiet", brkQuiet) +
      '<span class="tiny" style="color:var(--faint)" title="On: urgent alerts page you even during quiet hours">can override quiet hours</span></div></div>' +
      '<div class="wt-ctl"><label>Quiet hours</label><div class="wt-row2">' +
      '<input class="wt-inp wt-time" id="wt-qh-start" value="' + E(qh.start || "22:00") + '">' +
      '<span class="tiny" style="color:var(--faint)">to</span>' +
      '<input class="wt-inp wt-time" id="wt-qh-end" value="' + E(qh.end || "07:00") + '"></div></div>' +
      '<div class="wt-ctl"><label>Daily cap</label><div class="wt-row2">' +
      '<input class="wt-inp wt-nn" id="wt-cap" type="number" min="1" max="200" value="' +
      E(String(num(cap, 20))) + '">' +
      '<button class="wt-btn" id="wt-preview-brief" style="margin-left:auto">Preview brief</button>' +
      "</div></div></div>";
  }

  function toggle(id, on) {
    return '<span class="wt-tog' + (on ? " on" : "") + '" id="' + id +
      '" role="switch" aria-checked="' + (on ? "true" : "false") + '"><span class="wt-knob"></span></span>';
  }

  function rulesHtml(rules, stats) {
    if (!rules.length)
      return '<div class="wt-empty">No watches yet — add one below to get a heads-up when something moves.</div>';
    return rules.map(function (r) { return ruleRow(r, stats[r.id] || {}); }).join("");
  }

  function ruleRow(r, st) {
    var meta = TYPE_META[r.type] || { label: r.type, cat: "agent" };
    var fired = num(st.fired, 0);
    var prec = st.precision;
    var meterPct = (typeof prec === "number") ? Math.round(prec * 100) : 0;
    var meterTxt = fired ? (meterPct + "% useful") : "no fires";
    var subs = [];
    subs.push(E(meta.label));
    subs.push("cooldown " + E(String(num(r.cooldown_min, 0))) + "m");
    if (fired) subs.push(fired + " fired");
    if (st.last_fired) subs.push("last " + E(t12(st.last_fired)));
    return '<div class="wt-row" data-id="' + E(r.id) + '">' +
      '<span class="wt-gl">' + typeGlyph(meta.cat) + "</span>" +
      '<div class="wt-main"><div class="wt-lbl">' + E(r.label) + "</div>" +
      '<div class="wt-sub">' + subs.map(function (x) { return "<span>" + x + "</span>"; }).join("·&nbsp;") + "</div></div>" +
      '<div class="wt-meter"><div class="wt-mtrack"><div class="wt-mfill" style="width:' +
      meterPct + '%"></div></div><div class="wt-mtxt">' + E(meterTxt) + "</div></div>" +
      toggle("wt-tog-" + E(r.id), r.enabled) +
      '<button class="wt-icobtn" data-mute="' + E(r.id) + '" title="Mute (disable + mark noise)">' +
      muteSvg() + "</button>" +
      '<button class="wt-icobtn wt-danger" data-del="' + E(r.id) + '" title="Delete">' +
      xSvg() + "</button></div>";
  }

  function muteSvg() {
    return '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">' +
      '<path d="M5 9v6h4l5 4V5L9 9H5z" fill="none" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linejoin="round"/><path d="M17 9l4 6M21 9l-4 6" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round"/></svg>';
  }
  function xSvg() {
    return '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">' +
      '<path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
  }

  var LIVE_TYPES = ["ticker_move", "index_move", "crypto_move", "system_metric", "rss_keyword"];

  function composerHtml() {
    var opts = LIVE_TYPES.map(function (t) {
      return '<option value="' + t + '">' + E((TYPE_META[t] || {}).label || t) + "</option>";
    }).join("");
    return '<div class="wt-add">' +
      '<div class="wt-fld"><label>Type</label>' +
      '<select class="wt-sel" id="wt-type" style="max-width:220px">' + opts + "</select></div>" +
      '<div class="wt-fields" id="wt-fields"></div>' +
      '<div class="wt-eb" id="wt-add-eb"></div>' +
      '<div class="wt-actions">' +
      '<button class="wt-btn" id="wt-test">Test now</button>' +
      '<button class="wt-btn primary" id="wt-add">Add watch</button></div>' +
      '<div class="wt-preview" id="wt-preview" style="display:none"></div></div>';
  }

  function fieldsHtml(type) {
    function fld(inner) { return '<div class="wt-fld">' + inner + "</div>"; }
    function txt(id, label, val, ph, w) {
      return fld("<label>" + E(label) + "</label><input class='wt-inp' id='" + id + "' value='" +
        E(val || "") + "' placeholder='" + E(ph || "") + "' style='width:" + (w || 120) + "px'>");
    }
    function nn(id, label, val, w) {
      return fld("<label>" + E(label) + "</label><input class='wt-inp' id='" + id +
        "' type='number' value='" + E(String(val)) + "' style='width:" + (w || 80) + "px'>");
    }
    function sel(id, label, options, cur) {
      var o = options.map(function (x) {
        return "<option value='" + x[0] + "'" + (x[0] === cur ? " selected" : "") + ">" + E(x[1]) + "</option>";
      }).join("");
      return fld("<label>" + E(label) + "</label><select class='wt-sel' id='" + id + "'>" + o + "</select>");
    }
    var dir = [["any", "any direction"], ["up", "up only"], ["down", "down only"]];
    if (type === "ticker_move")
      return txt("wt-p-symbol", "Symbol", "NVDA", "NVDA", 100) +
        nn("wt-p-threshold", "Threshold %", 5) + sel("wt-p-direction", "Direction", dir, "any");
    if (type === "index_move")
      return sel("wt-p-symbol", "Index", [["SPY", "S&P 500 (SPY)"], ["QQQ", "Nasdaq (QQQ)"],
        ["DIA", "Dow (DIA)"], ["IWM", "Russell (IWM)"]], "SPY") +
        nn("wt-p-threshold", "Threshold %", 2) + sel("wt-p-direction", "Direction", dir, "any");
    if (type === "crypto_move")
      return txt("wt-p-coin", "Coin id", "bitcoin", "bitcoin", 120) +
        nn("wt-p-threshold", "Threshold %", 5) + sel("wt-p-direction", "Direction", dir, "any");
    if (type === "system_metric")
      return sel("wt-p-metric", "Metric", [["ram_pct", "RAM %"], ["cpu_pct", "CPU %"],
        ["disk_pct", "Disk %"], ["battery_pct", "Battery %"]], "ram_pct") +
        sel("wt-p-op", "When", [[">", "above"], ["<", "below"]], ">") + nn("wt-p-value", "Value %", 90);
    if (type === "rss_keyword")
      return txt("wt-p-keywords", "Keywords (comma)", "outage, acquires", "outage, acquires", 200) +
        txt("wt-p-sections", "Sections (comma, optional)", "", "Tech, World", 160);
    return "";
  }

  function readParams(card, type) {
    function v(id) { var el = card.querySelector("#" + id); return el ? el.value : ""; }
    if (type === "ticker_move" || type === "index_move")
      return { symbol: v("wt-p-symbol").trim(), threshold_pct: Number(v("wt-p-threshold")),
        direction: v("wt-p-direction") };
    if (type === "crypto_move")
      return { coin: v("wt-p-coin").trim(), threshold_pct: Number(v("wt-p-threshold")),
        direction: v("wt-p-direction") };
    if (type === "system_metric")
      return { metric: v("wt-p-metric"), op: v("wt-p-op"), value: Number(v("wt-p-value")) };
    if (type === "rss_keyword") {
      var kws = v("wt-p-keywords").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      var secs = v("wt-p-sections").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      return { keywords: kws, sections: secs };
    }
    return {};
  }

  function recentHtml(recent) {
    if (!recent || !recent.length)
      return '<div class="wt-empty">No fires yet — when a watch trips, it lands here (and on your phone).</div>';
    return recent.slice(0, 12).map(function (r) {
      var when = t12(r.ts);
      var body;
      if (r.suppressed) {
        body = '<div class="wt-fl">' + E(r.label) + '</div><div class="wt-fx">suppressed · ' +
          E(r.suppressed) + "</div>";
      } else {
        var txt = (r.text || "").split("\n").slice(1).join(" ") || r.label;
        body = '<div class="wt-fl">' + E(r.label) + '</div><div class="wt-fx">' + E(txt) + "</div>";
      }
      var react = r.suppressed ? "" :
        '<div class="wt-react"><button class="wt-rb' + (r.reaction === "useful" ? " on-useful" : "") +
        '" data-react="useful" data-ts="' + E(String(r.ts)) + '">Useful</button>' +
        '<button class="wt-rb' + (r.reaction === "noise" ? " on-noise" : "") +
        '" data-react="noise" data-ts="' + E(String(r.ts)) + '">Noise</button></div>';
      return '<div class="wt-fire"><span class="wt-ft">' + E(when) + '</span>' +
        '<div class="wt-fb">' + body + "</div>" + react + "</div>";
    }).join("");
  }

  // ---- networking ----------------------------------------------------------
  async function postJSON(url, body) {
    var r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    var j; try { j = await r.json(); } catch (e) { j = { ok: false, error: "bad response" }; }
    j.__status = r.status;
    return j;
  }
  function op(o) { return postJSON("/api/watchtower", o); }

  // ---- interactivity -------------------------------------------------------
  function wire(card, rules, brief) {
    var d = doc(); if (!d) return;

    // master toggles — one flip silences a whole family of pushes
    var mBriefs = card.querySelector("#wt-master-briefs");
    if (mBriefs) mBriefs.addEventListener("click", function () {
      var on = !mBriefs.classList.contains("on");
      setTog(mBriefs, on);
      op({ op: "set_master", briefings: on }).catch(function () {});
    });
    var mNews = card.querySelector("#wt-master-news");
    if (mNews) mNews.addEventListener("click", function () {
      var on = !mNews.classList.contains("on");
      setTog(mNews, on);
      op({ op: "set_master", news: on }).catch(function () {});
    });

    // brief toggle + time
    var briefTog = card.querySelector("#wt-brief-on");
    if (briefTog) briefTog.addEventListener("click", function () {
      var on = !briefTog.classList.contains("on");
      setTog(briefTog, on);
      op({ op: "set_brief", enabled: on }).catch(function () {});
    });
    var briefTime = card.querySelector("#wt-brief-time");
    if (briefTime) briefTime.addEventListener("change", function () {
      var m = /^(\d{1,2}):(\d{2})$/.exec(briefTime.value.trim());
      if (!m) { ctlErr(card, "Time must be HH:MM"); return; }
      var th = Number(m[1]), tm = Number(m[2]);
      op({ op: "set_brief", hour: th, minute: tm })
        .then(function (r) {
          if (!r || r.ok === false) { ctlErr(card, (r && r.error) || "bad time"); return; }
          timeSaved(card, briefTime, r.brief, "Brief time (00:00-23:59)", th, tm);
        })
        .catch(function () {});
    });

    // midday pulse toggle + time
    var midTog = card.querySelector("#wt-mid-on");
    if (midTog) midTog.addEventListener("click", function () {
      var on = !midTog.classList.contains("on");
      setTog(midTog, on);
      op({ op: "set_midday", enabled: on }).catch(function () {});
    });
    var midTime = card.querySelector("#wt-mid-time");
    if (midTime) midTime.addEventListener("change", function () {
      var m = /^(\d{1,2}):(\d{2})$/.exec(midTime.value.trim());
      if (!m) { ctlErr(card, "Time must be HH:MM"); return; }
      var th = Number(m[1]), tm = Number(m[2]);
      op({ op: "set_midday", hour: th, minute: tm })
        .then(function (r) {
          if (!r || r.ok === false) { ctlErr(card, (r && r.error) || "bad time"); return; }
          timeSaved(card, midTime, r.midday, "Midday runs 11:00-17:59 —", th, tm);
        })
        .catch(function () {});
    });

    // evening wrap toggle + time
    var eveTog = card.querySelector("#wt-eve-on");
    if (eveTog) eveTog.addEventListener("click", function () {
      var on = !eveTog.classList.contains("on");
      setTog(eveTog, on);
      op({ op: "set_evening", enabled: on }).catch(function () {});
    });
    var eveTime = card.querySelector("#wt-eve-time");
    if (eveTime) eveTime.addEventListener("change", function () {
      var m = /^(\d{1,2}):(\d{2})$/.exec(eveTime.value.trim());
      if (!m) { ctlErr(card, "Time must be HH:MM"); return; }
      var th = Number(m[1]), tm = Number(m[2]);
      op({ op: "set_evening", hour: th, minute: tm })
        .then(function (r) {
          if (!r || r.ok === false) { ctlErr(card, (r && r.error) || "bad time"); return; }
          timeSaved(card, eveTime, r.evening, "Evening runs 16:00-23:59 —", th, tm);
        })
        .catch(function () {});
    });

    // breaking alerts toggle + quiet-hours override
    var brkTog = card.querySelector("#wt-brk-on");
    if (brkTog) brkTog.addEventListener("click", function () {
      var on = !brkTog.classList.contains("on");
      setTog(brkTog, on);
      op({ op: "set_breaking", enabled: on }).catch(function () {});
    });
    var brkQuietTog = card.querySelector("#wt-brk-quiet");
    if (brkQuietTog) brkQuietTog.addEventListener("click", function () {
      var on = !brkQuietTog.classList.contains("on");
      setTog(brkQuietTog, on);
      op({ op: "set_breaking", override_quiet: on }).catch(function () {});
    });

    // quiet hours
    function saveQuiet() {
      var st = card.querySelector("#wt-qh-start"), en = card.querySelector("#wt-qh-end");
      if (!st || !en) return;
      op({ op: "set_quiet_hours", start: st.value.trim(), end: en.value.trim() })
        .then(function (r) { if (!r || r.ok === false) ctlErr(card, (r && r.error) || "bad time"); })
        .catch(function () {});
    }
    each(card.querySelectorAll("#wt-qh-start,#wt-qh-end"), function (el) {
      el.addEventListener("change", saveQuiet);
    });

    // daily cap
    var cap = card.querySelector("#wt-cap");
    if (cap) cap.addEventListener("change", function () {
      op({ op: "set_daily_cap", n: Number(cap.value) })
        .then(function (r) { if (!r || r.ok === false) ctlErr(card, (r && r.error) || "bad cap"); })
        .catch(function () {});
    });

    // preview brief (safe, dry — never sends)
    var pv = card.querySelector("#wt-preview-brief");
    if (pv) pv.addEventListener("click", async function () {
      pv.disabled = true; pv.textContent = "…";
      try {
        var r = await fetch("/api/brief/preview", { cache: "no-store" });
        var j = await r.json();
        showBriefPreview(card, j);
      } catch (e) { ctlErr(card, "preview failed"); }
      pv.disabled = false; pv.textContent = "Preview brief";
    });

    // per-rule toggles / mute / delete
    each(card.querySelectorAll(".wt-row"), function (row) {
      var id = row.getAttribute("data-id");
      var tog = row.querySelector(".wt-tog");
      if (tog) tog.addEventListener("click", function () {
        var on = !tog.classList.contains("on");
        setTog(tog, on);
        op({ op: "toggle_rule", id: id, enabled: on }).catch(function () {});
      });
    });
    each(card.querySelectorAll("[data-mute]"), function (b) {
      b.addEventListener("click", function () {
        op({ op: "mute_rule", id: b.getAttribute("data-mute") })
          .then(function () { watchtowerPanel().catch(function () {}); }).catch(function () {});
      });
    });
    each(card.querySelectorAll("[data-del]"), function (b) {
      b.addEventListener("click", function () {
        var okDel = (typeof confirm !== "function") || confirm("Delete this watch?");
        if (!okDel) return;
        op({ op: "delete_rule", id: b.getAttribute("data-del") })
          .then(function () { watchtowerPanel().catch(function () {}); }).catch(function () {});
      });
    });

    // reactions
    each(card.querySelectorAll(".wt-rb"), function (b) {
      b.addEventListener("click", function () {
        var reaction = b.getAttribute("data-react");
        var ts = Number(b.getAttribute("data-ts"));
        each(b.parentNode.querySelectorAll(".wt-rb"), function (x) {
          x.classList.remove("on-useful"); x.classList.remove("on-noise");
        });
        b.classList.add("on-" + reaction);
        op({ op: "mark_reaction", ts: ts, reaction: reaction }).catch(function () {});
      });
    });

    // composer
    var typeSel = card.querySelector("#wt-type");
    var fields = card.querySelector("#wt-fields");
    function refreshFields() { if (fields) fields.innerHTML = fieldsHtml(typeSel ? typeSel.value : "ticker_move"); }
    if (typeSel) typeSel.addEventListener("change", function () { refreshFields(); hidePreview(card); });
    refreshFields();

    var testBtn = card.querySelector("#wt-test");
    if (testBtn) testBtn.addEventListener("click", async function () {
      addErr(card, "");
      var rule = { type: typeSel.value, params: readParams(card, typeSel.value) };
      testBtn.disabled = true;
      var r = await op({ op: "test_rule", rule: rule }).catch(function () { return { ok: false, error: "network" }; });
      testBtn.disabled = false;
      if (!r || r.ok === false) { addErr(card, (r && r.error) || "test failed"); hidePreview(card); return; }
      showTest(card, r);
    });

    var addBtn = card.querySelector("#wt-add");
    if (addBtn) addBtn.addEventListener("click", async function () {
      addErr(card, "");
      var rule = { type: typeSel.value, params: readParams(card, typeSel.value) };
      addBtn.disabled = true;
      var r = await op({ op: "add_rule", rule: rule }).catch(function () { return { ok: false, error: "network" }; });
      addBtn.disabled = false;
      if (!r || r.ok === false) { addErr(card, (r && r.error) || "couldn’t add"); return; }
      watchtowerPanel().catch(function () {});
    });
  }

  function setTog(el, on) {
    if (!el) return;
    if (on) el.classList.add("on"); else el.classList.remove("on");
    el.setAttribute("aria-checked", on ? "true" : "false");
  }
  // After a schedule-time save: show what the backend actually stored. The
  // ops clamp the hour to a window (brief 0-23, midday 11-17, evening 16-23)
  // and always answer ok:true, so a typed 06:00 evening silently became 16:00
  // while the field kept showing 06:00. Write the stored value back and say so.
  function timeSaved(card, input, cfg, label, typedH, typedM) {
    if (!cfg || typeof cfg.hour !== "number") return;
    var h = num(cfg.hour, typedH), m = num(cfg.minute, typedM);
    var txt = (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
    if (input) input.value = txt;
    if (h !== typedH || m !== typedM) ctlErr(card, label + " saved as " + txt);
  }
  function ctlErr(card, msg) {
    var eb = card.querySelector("#wt-ctl-eb"); if (!eb) return;
    eb.textContent = msg || "";
    if (msg) setTimeout(function () { if (eb) eb.textContent = ""; }, 3200);
  }
  function addErr(card, msg) {
    var eb = card.querySelector("#wt-add-eb"); if (!eb) return;
    eb.textContent = msg || "";
  }
  function hidePreview(card) {
    var p = card.querySelector("#wt-preview"); if (p) p.style.display = "none";
  }
  function showTest(card, r) {
    var p = card.querySelector("#wt-preview"); if (!p) return;
    p.style.display = "";
    if (r.would_fire) {
      p.innerHTML = '<span class="wt-yes"><b>Would fire now.</b></span>\n' + E(r.text || "");
    } else {
      var why = (r.context && (r.context.error || r.context.reason || r.context.note)) || "conditions not met";
      p.innerHTML = '<span class="wt-no"><b>Would not fire right now</b> — ' + E(why) + ".</span>" +
        (r.live === false ? "\n(This trigger type is not live yet — it will stay quiet until enabled.)" : "");
    }
  }
  function showBriefPreview(card, j) {
    var p = card.querySelector("#wt-preview"); if (!p) return;
    p.style.display = "";
    var secs = (j && j.sections) || {};
    var order = [["foryou", "For you"], ["day", "Your day"], ["world", "World front page"],
      ["ai", "AI & Labs"], ["underground", "Underground signal"], ["lookahead", "Look-ahead"]];
    var out = order.map(function (kv) {
      var s = secs[kv[0]] || {};
      var lines = (s.lines && s.lines.length) ? s.lines.slice(0, 4).join("\n") : (s.note || "nothing yet");
      return "<b>" + E(kv[1]) + "</b>\n" + E(lines);
    }).join("\n\n");
    p.innerHTML = out + "\n\n<span class='wt-no'>(preview only — the 8am push and the Briefing widget " +
      "get the full brief)</span>";
  }

  // expose for the headless render harness / manual invocation
  window.watchtowerPanel = watchtowerPanel;
})();
