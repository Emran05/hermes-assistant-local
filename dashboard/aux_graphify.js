// aux_graphify.js — Settings/Mind card for the code knowledge-graph memory layer
// (backend: aux_graphify.py). Shows graph stats + hub ("god") nodes and a live
// query box (/api/graph/query) so you can traverse the repo graph from the
// dashboard instead of grepping. Chains window.mindExtras exactly like
// aux_youmodel.js; the settings relocator re-homes #mind-extra-graphify.
// Design laws: no emoji (bespoke two-tone SVG), esc() on every interpolation.
(function () {
  "use strict";
  if (typeof window === "undefined") return;

  function D() { return (typeof document !== "undefined") ? document : null; }
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }

  var GLYPH =
    '<svg class="gfyic" viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
    'stroke="currentColor" stroke-width="1.6" style="flex:0 0 auto">' +
    '<circle cx="6" cy="6" r="2.4" fill="currentColor" opacity=".18"/>' +
    '<circle cx="18" cy="7" r="2.4"/>' +
    '<circle cx="12" cy="17" r="2.4" fill="currentColor" opacity=".18"/>' +
    '<path d="M7.6 7.4 10.6 15M16.4 8.6 13.4 15M8 6.4h8"/></svg>';

  function shortFile(f) { f = String(f || ""); return f.length > 42 ? "…" + f.slice(-41) : f; }

  function renderStats(s) {
    if (!s || s.available === false || s.ok === false) {
      return '<div class="gfy-empty">No graph yet — build it:<br>' +
        '<code>~/.hermes/graphify-venv/bin/graphify update .</code></div>';
    }
    var gods = (s.god_nodes || []).slice(0, 6).map(function (n) {
      return '<li><span class="gfy-lbl">' + E(n.label || n.id) + '</span>' +
        '<span class="gfy-deg">' + E(n.degree) + '</span>' +
        '<span class="gfy-f">' + E(shortFile(n.file)) + '</span></li>';
    }).join("");
    return '<div class="gfy-stats">' +
      '<div class="gfy-stat"><b>' + E(s.nodes) + '</b><span>nodes</span></div>' +
      '<div class="gfy-stat"><b>' + E(s.edges) + '</b><span>edges</span></div>' +
      '<div class="gfy-stat"><b>' + E(s.communities) + '</b><span>communities</span></div>' +
      '</div><div class="gfy-h">Hub nodes</div><ul class="gfy-gods">' + gods + '</ul>';
  }

  function renderMatches(r) {
    if (!r || r.ok === false || r.available === false)
      return '<div class="gfy-empty">Graph unavailable.</div>';
    var m = r.matches || [];
    if (!m.length) return '<div class="gfy-empty">No matches for “' + E(r.query || "") + '”.</div>';
    return m.map(function (x) {
      var neigh = (x.neighbors || []).slice(0, 8).map(function (nb) {
        return '<span class="gfy-chip" title="' + E(nb.relation) + " · " + E(nb.file || "") + '">' +
          E(nb.label) + '</span>';
      }).join("");
      return '<div class="gfy-match"><div class="gfy-mh">' +
        '<span class="gfy-lbl">' + E(x.label || x.id) + '</span>' +
        '<span class="gfy-f">' + E(shortFile(x.file)) + (x.location ? " · " + E(x.location) : "") + '</span></div>' +
        (neigh ? '<div class="gfy-neigh">' + neigh + '</div>' : '') + '</div>';
    }).join("");
  }

  function CSS() {
    return '<style>' +
      '#mind-extra-graphify .gfy-stats{display:flex;gap:18px;margin:6px 0 10px}' +
      '#mind-extra-graphify .gfy-stat{display:flex;flex-direction:column}' +
      '#mind-extra-graphify .gfy-stat b{font-size:20px;font-variant-numeric:tabular-nums}' +
      '#mind-extra-graphify .gfy-stat span{font-size:11px;color:var(--muted)}' +
      '#mind-extra-graphify .gfy-h,#mind-extra-graphify .gfy-qh{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:8px 0 4px}' +
      '#mind-extra-graphify .gfy-gods{list-style:none;margin:0;padding:0;font-size:12.5px}' +
      '#mind-extra-graphify .gfy-gods li{display:flex;align-items:center;gap:8px;padding:2px 0}' +
      '#mind-extra-graphify .gfy-lbl{font-weight:600}' +
      '#mind-extra-graphify .gfy-deg{font-size:11px;color:var(--muted);min-width:24px}' +
      '#mind-extra-graphify .gfy-f{font-size:11px;color:var(--muted);font-family:ui-monospace,monospace;opacity:.8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      '#mind-extra-graphify .gfy-q{display:flex;gap:6px;margin-top:6px}' +
      '#mind-extra-graphify .gfy-q input{flex:1;min-width:0;padding:6px 9px;border-radius:8px;border:1px solid var(--hair,rgba(255,255,255,.12));background:rgba(255,255,255,.04);color:inherit;font-size:13px}' +
      '#mind-extra-graphify .gfy-q button{padding:6px 12px;border-radius:8px;border:0;background:var(--accent,#6b8afd);color:#fff;font-size:13px;cursor:pointer}' +
      '#mind-extra-graphify .gfy-match{margin:6px 0;padding:6px 8px;border-radius:8px;background:rgba(255,255,255,.03)}' +
      '#mind-extra-graphify .gfy-mh{display:flex;justify-content:space-between;gap:8px;align-items:baseline}' +
      '#mind-extra-graphify .gfy-neigh{margin-top:4px;display:flex;flex-wrap:wrap;gap:4px}' +
      '#mind-extra-graphify .gfy-chip{font-size:11px;padding:1px 7px;border-radius:20px;background:rgba(255,255,255,.06);color:var(--muted)}' +
      '#mind-extra-graphify .gfy-empty{font-size:12.5px;color:var(--muted);padding:6px 0}' +
      '#mind-extra-graphify .gfy-empty code{font-family:ui-monospace,monospace;font-size:11.5px}' +
      '#mind-extra-graphify .gfy-res{margin-top:8px}' +
      '</style>';
  }

  async function runQuery(host) {
    var input = host.querySelector(".gfy-q input");
    var res = host.querySelector(".gfy-res");
    if (!input || !res) return;
    var q = (input.value || "").trim();
    if (!q) { res.innerHTML = ""; return; }
    res.innerHTML = '<div class="gfy-empty">Searching…</div>';
    try {
      var r = await (await fetch("/api/graph/query?q=" + encodeURIComponent(q))).json();
      res.innerHTML = renderMatches(r);
    } catch (e) { res.innerHTML = '<div class="gfy-empty">Query failed.</div>'; }
  }

  async function graphPanel() {
    var doc = D();
    if (!doc) return;
    var host = doc.getElementById("view-mind");
    if (!host) return;
    var stats = null;
    try { stats = await (await fetch("/api/graph/stats")).json(); } catch (e) {}
    var old = doc.getElementById("mind-extra-graphify");
    var s = old || doc.createElement("section");
    s.className = "card glass";
    s.id = "mind-extra-graphify";
    s.innerHTML = CSS() +
      '<h2 style="display:flex;align-items:center;gap:7px">' + GLYPH +
      'Code graph <span style="font-weight:400;color:var(--muted);font-size:12px">· memory layer</span></h2>' +
      '<div class="gfy-body">' + renderStats(stats) + '</div>' +
      '<div class="gfy-qh">Ask the graph</div>' +
      '<div class="gfy-q"><input type="text" placeholder="a function, file, or symbol…" autocomplete="off">' +
      '<button type="button">Explain</button></div><div class="gfy-res"></div>';
    if (!old) host.appendChild(s);
    var btn = s.querySelector(".gfy-q button");
    var inp = s.querySelector(".gfy-q input");
    if (btn) btn.onclick = function () { runQuery(s); };
    if (inp) inp.onkeydown = function (e) { if (e.key === "Enter") { e.preventDefault(); runQuery(s); } };
  }

  var prev = window.mindExtras;
  window.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await graphPanel(); } catch (e) {}
  };
})();
