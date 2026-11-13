// aux_hfhub.js — "Browse Hugging Face": find an MLX model on the Hub, see
// whether it runs on THIS Mac, and add it to the model menu (2026-11-12).
//
// window.hermesHF = { v:1, open(), mount(host?), idFromInput(s), verdictWord(v) }
//
// A card in Settings › Agent & Models (#sec-models), mounted directly into
// that panel's .set-body right after the Model advisor card — never
// #view-mind (the relocator would file it under System & Data). The model
// menu gets a "Browse Hugging Face…" row that calls open().
//
// Every request is a same-origin /api route (aux_hfhub.py); nothing in the
// page talks to huggingface.co. Nothing is fetched until the owner types a
// query, presses "Most downloaded", or pastes a repo link. Hub strings reach
// the page already reduced to ids, numbers and dates, and are escaped here
// again with index.html's esc(). The only outbound link is
// https://huggingface.co/<validated id>, opened inside the click.
// Adding asks confirm() first; "Add and download" also starts the pull, whose
// progress the model menu already shows. Switching stays in the model menu
// (an uncensored model asks for the acknowledgement there).
(function () {
  "use strict";
  var W = window;
  if (W.__auxHFHub) return;
  W.__auxHFHub = true;

  var CARD_ID = "mind-extra-hfhub", CSS_ID = "hfx-css", PANEL_ID = "sec-models";
  var ID_RE = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?\/[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?$/;
  var URL_RE = /^(?:https?:\/\/)?(?:www\.)?huggingface\.co\/([^\/?#\s]+\/[^\/?#\s]+)/i;
  var DEBOUNCE_MS = 450;

  var S = {
    q: "", label: "", results: null, loading: false, err: "",
    open: {}, insp: {}, busy: {}, msg: {}, seq: 0, timer: null, n: 0, ids: {}
  };

  function D() { return W.document; }
  function E(s) {
    if (typeof W.esc === "function") return W.esc(s);
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function HA() {
    var a = W.HermesAnim;
    return a && typeof a.enter === "function" ? a : null;
  }

  // ---- pure helpers (the unit suite drives these) -------------------------
  function idFromInput(s) {
    s = String(s == null ? "" : s).trim();
    var m = s.match(URL_RE);
    if (m) s = m[1];
    return (ID_RE.test(s) && s.indexOf("..") < 0 && s.indexOf("--") < 0) ? s : null;
  }
  function verdictWord(v) {
    return v === "ok" ? "Runs on this Mac" : v === "warn" ? "Runs on this Mac, with caveats"
      : v === "no" ? "Can’t run here" : "";
  }
  function num(n) {
    if (typeof n !== "number" || !isFinite(n)) return "";
    if (n >= 1e6) return (Math.round(n / 1e5) / 10) + "M";
    if (n >= 1e3) return (Math.round(n / 1e2) / 10) + "k";
    return String(n);
  }
  function gb(x) {
    return (typeof x === "number" && isFinite(x) && x > 0) ? ("~" + (Math.round(x * 10) / 10) + " GB") : "";
  }
  function day(iso) {
    if (typeof iso !== "string") return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    try { return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); }
    catch (e) { return iso.slice(0, 10); }
  }
  function hubUrl(id) {
    return idFromInput(id) === id ? "https://huggingface.co/" + id : null;
  }
  function rowKey(id) {
    if (!S.ids[id]) S.ids[id] = "hfx-d-" + (++S.n);
    return S.ids[id];
  }

  // ---- network (same-origin only) ----------------------------------------
  function headers(json) {
    var h = { "Accept": "application/json" };
    if (json) h["Content-Type"] = "application/json";
    if (typeof W.hermesNonce === "string" && W.hermesNonce) h["X-Hermes-Nonce"] = W.hermesNonce;
    return h;
  }
  async function jreq(url, body) {
    try {
      var r = await W.fetch(url, body === undefined
        ? { credentials: "same-origin", headers: headers(false) }
        : { method: "POST", credentials: "same-origin", headers: headers(true), body: JSON.stringify(body) });
      var d = null;
      try { d = await r.json(); } catch (e) { d = null; }
      return { ok: r.ok && !!d && d.ok !== false, status: r.status, data: d || {} };
    } catch (e) {
      return { ok: false, status: 0, data: { error: "the dashboard did not answer" } };
    }
  }

  // ---- search --------------------------------------------------------------
  async function runSearch(q, label) {
    var seq = ++S.seq;
    S.loading = true; S.err = ""; S.q = q; S.label = label || "";
    paint();
    var r = await jreq("/api/models/hf/search?q=" + encodeURIComponent(q) + "&limit=30");
    if (seq !== S.seq) return;
    S.loading = false;
    if (!r.ok) { S.err = (r.data && r.data.error) || "the search failed"; S.results = []; }
    else S.results = Array.isArray(r.data.results) ? r.data.results : [];
    paint();
  }
  function onQuery(raw, now) {
    if (S.timer) { clearTimeout(S.timer); S.timer = null; }
    var q = String(raw || "").trim();
    var id = idFromInput(q);
    if (id && q.indexOf("/") > 0) {
      // a pasted repo id or link: show that one repo and check it straight away
      S.seq++;
      S.loading = false; S.err = ""; S.q = q; S.label = "";
      S.results = [{ id: id, direct: true }];
      S.open = {}; S.open[id] = true;
      paint();
      inspect(id);
      return;
    }
    if (!q) { S.seq++; S.results = null; S.loading = false; S.err = ""; paint(); return; }
    if (now) runSearch(q);
    else S.timer = setTimeout(function () { S.timer = null; runSearch(q); }, DEBOUNCE_MS);
  }

  // ---- inspect / import ----------------------------------------------------
  async function inspect(id, fresh) {
    if (S.busy[id] === "inspect") return;
    S.busy[id] = "inspect";
    delete S.msg[id];
    paintRow(id);
    var r = await jreq("/api/models/hf/inspect?id=" + encodeURIComponent(id) + (fresh ? "&fresh=1" : ""));
    delete S.busy[id];
    S.insp[id] = r.ok ? r.data : { error: (r.data && r.data.error) || "could not check this repo" };
    paintRow(id);
  }
  function ask(text) {
    try { return typeof W.confirm === "function" ? !!W.confirm(text) : false; } catch (e) { return false; }
  }
  async function addModel(id, download) {
    var it = S.insp[id];
    if (!it || it.error || it.verdict === "no" || S.busy[id]) return;
    var f = it.facts || {};
    var lines = [(it.in_roster ? "Update the settings of " : "Add ") + id +
      (it.in_roster ? " in the model menu?" : " to the model menu?"), ""];
    if (!it.curated) lines.push("Not curated: nobody has checked this build. It runs only MLX weights " +
      "from the repo, never its code, and stays “unverified until drilled”.");
    if (f.uncensored) lines.push("Uncensored: its refusals were removed — the approval gate is the only " +
      "brake. Switching to it asks you to acknowledge that once.");
    if (download) lines.push("", "Download " + (gb(f.download_gb) || "the weights") + " now." +
      (f.machine_gb && f.ram_gb ? " It needs about " + f.ram_gb + " GB of memory; this Mac has " +
        f.machine_gb + " GB." : ""));
    if (!ask(lines.join("\n"))) return;
    S.busy[id] = download ? "download" : "add";
    delete S.msg[id];
    paintRow(id);
    var body = { id: id, confirm: true, download: !!download };
    if (typeof it.sha === "string") body.sha = it.sha;
    var r = await jreq("/api/models/hf/import", body);
    delete S.busy[id];
    var d = r.data || {};
    if (!r.ok) {
      if (d.stale) { S.msg[id] = ["The repo changed since you looked — check it again.", "bad"]; inspect(id, true); return; }
      S.msg[id] = [d.error || "Not added.", "bad"];
    } else {
      it.in_roster = true;
      var dl = d.download;
      if (download && dl && dl.ok === false) {
        S.msg[id] = ["Added to the model menu, but the download did not start: " + (dl.error || "unknown error") + ".", "bad"];
      } else if (download) {
        S.msg[id] = ["Added. Downloading " + (gb(f.download_gb) || "") + " — progress shows in the model menu; switch to it there when it is done.", "ok"];
      } else {
        S.msg[id] = [(d.updated ? "Updated. " : "Added. ") + "Download it and switch to it from the model menu.", "ok"];
      }
      (S.results || []).forEach(function (x) { if (x.id === id) x.in_roster = true; });
      try { if (typeof W.loadModels === "function") W.loadModels(); } catch (e) {}
    }
    paintRow(id);
  }
  function openHub(id) {
    var u = hubUrl(id);
    if (!u) return;
    try { W.open(u, "_blank", "noopener"); } catch (e) {}
  }

  // ---- rendering -----------------------------------------------------------
  function chip(text, cls) { return '<span class="hfx-chip' + (cls ? " " + cls : "") + '">' + E(text) + "</span>"; }
  function rowHead(x) {
    var parts = x.id.split("/");
    var meta = [];
    if (typeof x.downloads === "number") meta.push(num(x.downloads) + " downloads");
    if (typeof x.likes === "number" && x.likes > 0) meta.push(num(x.likes) + " likes");
    if (x.updated) meta.push("updated " + day(x.updated));
    var chips = "";
    if (x.bits) chips += chip(x.bits + "-bit");
    if (x.vision) chips += chip("images");
    if (x.uncensored) chips += chip("Uncensored", "warn");
    if (x.gated) chips += chip("gated", "bad");
    if (x.curated) chips += chip("curated", "ok");
    if (x.in_roster) chips += chip("in your menu", "ok");
    return '<span class="hfx-nm"><span class="hfx-org">' + E(parts[0]) + "/</span>" + E(parts[1] || "") + "</span>" +
      (meta.length ? '<span class="hfx-meta">' + E(meta.join(" · ")) + "</span>" : "") +
      (chips ? '<span class="hfx-chips">' + chips + "</span>" : "");
  }
  function list(items, cls) {
    if (!items || !items.length) return "";
    return '<ul class="hfx-l ' + cls + '">' + items.map(function (t) { return "<li>" + E(t) + "</li>"; }).join("") + "</ul>";
  }
  function detailHTML(id) {
    if (S.busy[id] === "inspect") return '<p class="hfx-wait">Checking the repo on Hugging Face…</p>';
    var it = S.insp[id];
    if (!it) return "";
    if (it.error) {
      return '<p class="hfx-bad">' + E(it.error) + '</p><div class="hfx-acts"><button type="button" data-hfx="retry" data-id="' +
        E(id) + '">Try again</button></div>';
    }
    var f = it.facts || {};
    var v = it.verdict;
    var dl = [];
    function fact(k, val) { if (val) dl.push("<div><dt>" + E(k) + "</dt><dd>" + E(val) + "</dd></div>"); }
    fact("Download", gb(f.download_gb) + (f.files ? " · " + f.files + " files" : ""));
    fact("Memory", f.ram_gb ? "~" + f.ram_gb + " GB" + (f.machine_gb ? " · this Mac has " + f.machine_gb + " GB" : "") +
      (f.fit === "tight" ? " · tight" : f.fit === "no" ? " · too big" : "") : "");
    fact("Context", f.ctx ? (f.ctx >= 1000 ? Math.round(f.ctx / 1024) + "k" : f.ctx) + " tokens" : "");
    if (v !== "no") fact("Runtime", f.backend === "mlx_vlm" ? "mlx-vlm" : f.backend === "mlx_lm" ? "mlx-lm fallback" : "");
    fact("Model type", f.model_type || "");
    fact("Precision", f.bits ? f.bits + "-bit" : (f.model_type ? "full precision" : ""));
    fact("Images", f.model_type ? (f.vision ? "yes" : "no (text-only)") : "");
    fact("Thinking", f.model_type ? (f.thinking ? "can think · starts off" : "no switch") : "");
    fact("Licence", f.license || (f.model_type ? "not stated" : ""));
    fact("Updated", day(f.updated));
    fact("Commit", typeof it.sha === "string" ? it.sha.slice(0, 7) : "");
    var acts = "";
    var busy = S.busy[id];
    if (v !== "no") {
      if (it.in_roster && it.curated) {
        acts += '<button type="button" data-hfx="menu">Open the model menu</button>';
      } else {
        acts += '<button type="button" class="primary" data-hfx="add-dl" data-id="' + E(id) + '"' + (busy ? " disabled" : "") + ">" +
          (busy === "download" ? "Adding…" : (it.in_roster ? "Update and download" : "Add and download") +
            (f.download_gb ? " · " + gb(f.download_gb) : "")) + "</button>";
        acts += '<button type="button" data-hfx="add" data-id="' + E(id) + '"' + (busy ? " disabled" : "") + ">" +
          (busy === "add" ? "Adding…" : (it.in_roster ? "Update its settings" : "Add to model menu")) + "</button>";
      }
    }
    acts += '<button type="button" class="ghost" data-hfx="hub" data-id="' + E(id) + '">View on Hugging Face</button>';
    var alts = "";
    if (it.alternatives && it.alternatives.length) {
      alts = '<p class="hfx-sub">MLX builds of this model</p><ul class="hfx-alts">' +
        it.alternatives.map(function (x) {
          return '<li><button type="button" class="hfx-alt" data-hfx="pick" data-id="' + E(x.id) + '">' + rowHead(x) + "</button></li>";
        }).join("") + "</ul>";
    }
    var msg = S.msg[id];
    return '<p class="hfx-verdict ' + E(v) + '"><span class="hfx-dot" aria-hidden="true"></span>' + E(verdictWord(v)) + "</p>" +
      list(it.blocks, "bad") + list(it.warns, "warn") +
      (dl.length ? '<dl class="hfx-facts">' + dl.join("") + "</dl>" : "") +
      list(it.notes, "note") +
      '<div class="hfx-acts">' + acts + "</div>" +
      (msg ? '<p class="hfx-msg ' + E(msg[1]) + '" role="status">' + E(msg[0]) + "</p>" : "") + alts;
  }
  function itemHTML(x) {
    var open = !!S.open[x.id], did = rowKey(x.id);
    return '<li class="hfx-item" data-id="' + E(x.id) + '">' +
      '<button type="button" class="hfx-row" data-hfx="toggle" data-id="' + E(x.id) + '" aria-expanded="' + (open ? "true" : "false") +
      '" aria-controls="' + did + '">' + rowHead(x) +
      '<svg class="hfx-chev" viewBox="0 0 24 24" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button>' +
      '<div class="hfx-det" id="' + did + '"' + (open ? "" : " hidden") + ">" + (open ? detailHTML(x.id) : "") + "</div></li>";
  }
  function resultsHTML() {
    if (S.loading) return '<p class="hfx-wait" role="status">Searching Hugging Face…</p>';
    if (S.err) return '<p class="hfx-bad" role="status">' + E(S.err) + "</p>";
    if (S.results === null) return "";
    if (!S.results.length) return '<p class="hfx-wait" role="status">No MLX chat models match “' + E(S.q) + "”.</p>";
    return (S.label ? '<p class="hfx-sub">' + E(S.label) + "</p>" : "") +
      '<ul class="hfx-res-l">' + S.results.map(itemHTML).join("") + "</ul>";
  }
  function cardHTML() {
    return '<h2><svg class="hfx-ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>' +
      'Browse Hugging Face <span class="tiny">MLX builds only · nothing downloads until you click</span></h2>' +
      '<div class="body"><div class="hfx">' +
      '<p class="hfx-lede">Search the Hub, or paste a model’s link. Each result is checked against this Mac and the runtime it has before you can add it.</p>' +
      '<div class="hfx-bar"><input type="search" class="hfx-q" placeholder="e.g. qwen3.5 9b, or a huggingface.co link" ' +
      'aria-label="Search Hugging Face for MLX models" autocomplete="off" spellcheck="false" maxlength="200">' +
      '<button type="button" data-hfx="popular">Most downloaded</button></div>' +
      '<div class="hfx-res" aria-live="polite">' + resultsHTML() + "</div></div></div>";
  }

  function card() { var d = D(); return d && d.getElementById(CARD_ID); }
  function paint() {
    var el = card();
    var res = el && el.querySelector(".hfx-res");
    if (res) res.innerHTML = resultsHTML();
  }
  function paintRow(id) {
    var el = card();
    if (!el) return;
    var items = el.querySelectorAll(".hfx-item");
    for (var i = 0; i < items.length; i++) {
      if (items[i].getAttribute("data-id") !== id) continue;
      var det = items[i].querySelector(".hfx-det");
      var btn = items[i].querySelector(".hfx-row");
      var open = !!S.open[id];
      if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (det) { det.hidden = !open; det.innerHTML = open ? detailHTML(id) : ""; }
    }
  }

  function onClick(e) {
    var b = e.target && e.target.closest ? e.target.closest("[data-hfx]") : null;
    if (!b) return;
    var act = b.getAttribute("data-hfx"), id = b.getAttribute("data-id");
    if (act === "popular") { var inp = card().querySelector(".hfx-q"); if (inp) inp.value = ""; runSearch("", "Most downloaded MLX chat models"); return; }
    if (act === "menu") { openMenu(); return; }
    if (!id || idFromInput(id) !== id) return;
    if (act === "toggle") {
      S.open[id] = !S.open[id];
      paintRow(id);
      if (S.open[id] && !S.insp[id] && !S.busy[id]) inspect(id);
      else if (S.open[id]) {
        var a = HA(), det = b.parentNode && b.parentNode.querySelector(".hfx-det");
        if (a && det) try { a.enter(det, "fade"); } catch (x) {}
      }
    } else if (act === "retry") inspect(id, true);
    else if (act === "add") addModel(id, false);
    else if (act === "add-dl") addModel(id, true);
    else if (act === "hub") openHub(id);
    else if (act === "pick") {
      var q = card().querySelector(".hfx-q");
      if (q) q.value = id;
      onQuery(id, true);
    }
  }
  function openMenu() {
    try {
      var pill = D().getElementById("model-pill"), menu = D().getElementById("model-menu");
      if (pill && menu && menu.hidden) pill.click();
    } catch (e) {}
  }

  function ensureCSS() {
    var d = D();
    if (!d || d.getElementById(CSS_ID)) return;
    var s = d.createElement("style");
    s.id = CSS_ID;
    s.textContent = css();
    (d.head || d.documentElement).appendChild(s);
  }
  function css() {
    var C = "#" + CARD_ID;
    return [
      C + " .hfx{font-size:13px;line-height:1.5;color:var(--ink);min-width:0}",
      C + " .hfx *{box-sizing:border-box}",
      C + " .hfx-ic{width:15px;height:15px;fill:none;stroke:var(--iris);stroke-width:2;stroke-linecap:round;vertical-align:-2px}",
      C + " .hfx-lede{margin:0 0 12px;color:var(--muted)}",
      C + " .hfx-bar{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}",
      C + " .hfx-q{flex:1 1 220px;min-width:0;min-height:40px;padding:0 12px;border-radius:var(--radius-xs);" +
        "border:1px solid var(--field-edge);background:var(--field);color:var(--ink);font:inherit}",
      C + " .hfx-res-l," + C + " .hfx-alts{list-style:none;margin:0;padding:0}",
      C + " .hfx-item{border-top:1px solid var(--hairline)}",
      C + " .hfx-item:first-child{border-top:0}",
      C + " .hfx-row," + C + " .hfx-alt{all:unset;box-sizing:border-box;display:grid;grid-template-columns:minmax(0,1fr) auto;" +
        "gap:2px 10px;width:100%;min-height:44px;padding:9px 6px;cursor:pointer;border-radius:var(--radius-xs);" +
        "transition-property:background-color;transition-duration:150ms;transition-timing-function:ease-out}",
      C + " .hfx-row:hover," + C + " .hfx-alt:hover{background:var(--chip)}",
      C + " .hfx-row:focus-visible," + C + " .hfx-alt:focus-visible{outline:2px solid var(--iris);outline-offset:2px}",
      C + " .hfx-nm{grid-column:1;font-weight:620;overflow-wrap:anywhere}",
      C + " .hfx-org{font-weight:450;color:var(--muted)}",
      C + " .hfx-meta{grid-column:1;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}",
      C + " .hfx-chips{grid-column:1;display:flex;flex-wrap:wrap;gap:5px;margin-top:2px}",
      C + " .hfx-chev{grid-column:2;grid-row:1/span 3;align-self:center;width:16px;height:16px;fill:none;stroke:var(--muted);" +
        "stroke-width:2;transition-property:rotate;transition-duration:180ms;transition-timing-function:ease-out}",
      C + " .hfx-row[aria-expanded=true] .hfx-chev{rotate:180deg}",
      C + " .hfx-chip{display:inline-flex;align-items:center;min-height:20px;padding:0 8px;border-radius:10px;font-size:11px;" +
        "font-weight:600;color:var(--muted);border:1px solid var(--field-edge);background:var(--field)}",
      C + " .hfx-chip.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,transparent)}",
      C + " .hfx-chip.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 45%,transparent)}",
      C + " .hfx-chip.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,transparent)}",
      C + " .hfx-det{padding:4px 6px 14px}",
      C + " .hfx-verdict{display:flex;align-items:center;gap:8px;margin:4px 0 8px;font-weight:680}",
      C + " .hfx-dot{width:9px;height:9px;border-radius:50%;background:var(--muted);flex:0 0 auto}",
      C + " .hfx-verdict.ok .hfx-dot{background:var(--ok)}",
      C + " .hfx-verdict.warn .hfx-dot{background:var(--warn)}",
      C + " .hfx-verdict.no .hfx-dot{background:var(--bad)}",
      C + " .hfx-l{margin:0 0 10px;padding-left:18px}",
      C + " .hfx-l li{margin:2px 0}",
      C + " .hfx-l.bad{color:var(--bad)}",
      C + " .hfx-l.warn{color:var(--warn)}",
      C + " .hfx-l.note{color:var(--muted);font-size:12px}",
      C + " .hfx-facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px 16px;margin:0 0 12px}",
      C + " .hfx-facts dt{font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--faint)}",
      C + " .hfx-facts dd{margin:0;font-weight:560;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}",
      C + " .hfx-acts{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 0}",
      C + " .hfx-acts button{min-height:40px}",
      C + " .hfx-msg{margin:10px 0 0;font-weight:560}",
      C + " .hfx-msg.ok{color:var(--ok)}",
      C + " .hfx-msg.bad," + C + " .hfx-bad{color:var(--bad)}",
      C + " .hfx-wait{color:var(--muted)}",
      C + " .hfx-sub{margin:14px 0 4px;font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}"
    ].join("\n");
  }

  var wired = false;
  function mount(host) {
    var d = D();
    if (!d) return null;
    ensureCSS();
    var body = host || null;
    if (!body) {
      var panel = d.getElementById(PANEL_ID);
      if (!panel) return null;          // wait for the next mindExtras() pass
      body = panel.querySelector(".set-body") || panel;
    }
    var el = card();
    if (!el) {
      el = d.createElement("section");
      el.id = CARD_ID;
      el.className = "card glass set-legacy";
      el.style.order = "-1";
      el.innerHTML = cardHTML();
      var adv = d.getElementById("mind-extra-advisor");
      if (adv && adv.parentNode === body) body.insertBefore(el, adv.nextSibling);
      else body.insertBefore(el, body.firstChild);
      wired = false;
    } else if (el.parentNode !== body) {
      try {
        var adv2 = d.getElementById("mind-extra-advisor");
        if (adv2 && adv2.parentNode === body) body.insertBefore(el, adv2.nextSibling);
        else body.insertBefore(el, body.firstChild);
      } catch (e) {}
    }
    if (!wired) {
      wired = true;
      el.addEventListener("click", onClick);
      var inp = el.querySelector(".hfx-q");
      if (inp) {
        inp.value = S.q;
        inp.addEventListener("input", function () { onQuery(inp.value, false); });
        inp.addEventListener("keydown", function (e) {
          if (e.key === "Enter" && !e.repeat) { e.preventDefault(); onQuery(inp.value, true); }
        });
      }
    }
    return el;
  }
  function focusSearch(el) {
    var inp = el && el.querySelector(".hfx-q");
    if (inp) try { inp.focus({ preventScroll: true }); } catch (e) { try { inp.focus(); } catch (x) {} }
  }
  function open(tries) {
    tries = tries || 0;
    if (!tries) {
      try { if (typeof W.closeModelMenu === "function") W.closeModelMenu(); } catch (e) {}
      try { if (typeof W.setView === "function") W.setView("mind"); } catch (e) {}
      try { if (typeof W.settingsEnsureShell === "function") W.settingsEnsureShell(); } catch (e) {}
      try { if (typeof W.settingsShow === "function") W.settingsShow("models"); } catch (e) {}
    }
    var el = mount();
    if (!el) {
      // the Settings shell builds its panels lazily: try again for ~1 s
      if (tries < 6) setTimeout(function () { open(tries + 1); }, 160);
      return false;
    }
    try { el.scrollIntoView({ block: "start", behavior: "auto" }); } catch (e) {}
    focusSearch(el);
    // closing the model menu hands focus back to its pill when its exit
    // animation ends, after this call: take it back unless the owner has
    // since moved to another field of their own
    [260, 650].forEach(function (ms) {
      setTimeout(function () {
        var d = D(), a = d && d.activeElement, menu = d && d.getElementById("model-menu");
        // card(), not `el`: opening Settings re-renders the panel and
        // mindExtras() mounts a fresh card a moment later
        if (!a || a === d.body || a.id === "model-pill" || (menu && menu.contains(a))) focusSearch(card());
      }, ms);
    });
    return true;
  }

  W.hermesHF = { v: 1, open: open, mount: mount, idFromInput: idFromInput, verdictWord: verdictWord,
                 _st: S, _detailHTML: detailHTML, _itemHTML: itemHTML, _add: addModel, _inspect: inspect };

  var prev = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { mount(); } catch (e) {}
  };
})();
