// aux_topbar.js — clean header chips: weather · Claude plan usage · date (MM/DD/YYYY).
// Injects a .topchips cluster into <header> (left of the model pill, visible on
// every view), polls /api/topbar for weather + usage, renders the date client-side.
// Design laws: no emoji (bespoke two-tone stroke SVG), tabular numerals, glass chip.
(function () {
  "use strict";
  if (typeof document === "undefined") return;

  var ICON = {
    weather: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    claude:  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9z"/></svg>',
    date:    '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9h18M8 2.5v4M16 2.5v4"/></svg>'
  };

  function css() {
    return '<style>' +
      '.topchips{display:inline-flex;align-items:center;gap:6px;margin:0 6px}' +
      '.topchip{display:inline-flex;align-items:center;gap:5px;font-size:12px;line-height:1;' +
        'padding:5px 9px;border-radius:999px;color:inherit;' +
        'background:rgba(255,255,255,.05);border:1px solid var(--hair,rgba(255,255,255,.10));' +
        '-webkit-backdrop-filter:blur(8px) saturate(1.2);backdrop-filter:blur(8px) saturate(1.2);white-space:nowrap}' +
      '.topchip svg{opacity:.7;flex:0 0 auto}' +
      '.topchip b{font-weight:600;font-variant-numeric:tabular-nums}' +
      '.topchip .mut{color:var(--muted);font-size:11px}' +
      '.topchip[hidden]{display:none}' +
      '@media (max-width:900px){.topchips .tc-city,.topchips .tc-lbl{display:none}}' +
      '@media (max-width:700px){.topchips{display:none}}' +
      '</style>';
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function mmddyyyy(d) { return pad(d.getMonth() + 1) + "/" + pad(d.getDate()) + "/" + d.getFullYear(); }

  var host = null;
  function ensure() {
    if (host && document.body && document.body.contains(host)) return host;
    var header = document.querySelector("header");
    if (!header) return null;
    host = document.createElement("div");
    host.className = "topchips";
    host.innerHTML = css() +
      '<span class="topchip" id="tc-weather" hidden title="">' + ICON.weather +
        '<b class="tc-temp">--</b><span class="mut tc-city"></span></span>' +
      '<span class="topchip" id="tc-claude" hidden title="">' + ICON.claude +
        '<span class="tc-lbl mut">Claude</span><b class="tc-pct">--</b></span>' +
      '<span class="topchip" id="tc-date" title="">' + ICON.date + '<b class="tc-date">--</b></span>';
    var anchor = header.querySelector(".modelwrap") || header.querySelector("#model-pill");
    if (anchor && anchor.parentNode === header) header.insertBefore(host, anchor);
    else header.appendChild(host);
    return host;
  }

  function renderDate() {
    var h = ensure(); if (!h) return;
    var d = new Date();
    var el = h.querySelector(".tc-date");
    if (el) el.textContent = mmddyyyy(d);
    var chip = h.querySelector("#tc-date");
    if (chip) chip.title = d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  }

  async function poll() {
    var h = ensure(); if (!h) return;
    var d = null;
    try { d = await (await fetch("/api/topbar")).json(); } catch (e) { return; }
    var w = d && d.weather, c = d && d.claude;
    var wc = h.querySelector("#tc-weather");
    if (wc) {
      if (w && w.configured && !w.error && w.temp != null) {
        wc.querySelector(".tc-temp").textContent = Math.round(w.temp) + "°";
        wc.querySelector(".tc-city").textContent = w.city || "";
        wc.title = (w.desc || "") + (w.hi != null ? "  ·  H " + Math.round(w.hi) + "° / L " + Math.round(w.lo) + "°" : "");
        wc.hidden = false;
      } else { wc.hidden = true; }
    }
    var cc = h.querySelector("#tc-claude");
    if (cc) {
      if (c && c.available && (c.pct != null || c.msgs != null)) {
        cc.querySelector(".tc-pct").textContent =
          (c.pct != null) ? (c.pct + "%") : (c.msgs != null ? c.msgs + " msg" : "--");
        var tip = [];
        if (c.cost != null) tip.push("$" + (Math.round(c.cost * 100) / 100));
        if (c.msgs != null) tip.push(c.msgs + " msgs");
        if (c.reset_in != null) {
          var hh = Math.floor(c.reset_in / 3600), mm = Math.round((c.reset_in % 3600) / 60);
          tip.push("resets in " + (hh ? hh + "h " : "") + mm + "m");
        }
        cc.title = "Claude usage" + (tip.length ? " — " + tip.join(" · ") : "");
        cc.hidden = false;
      } else { cc.hidden = true; }
    }
  }

  function start() { renderDate(); poll(); setInterval(renderDate, 60000); setInterval(poll, 60000); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
