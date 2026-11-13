// aux_youmodel.js — "Your Model" Mind card (Proactive-Intelligence WS 1.1/1.2).
//
// Auto-served at /aux_youmodel.js, loaded after /expand.js.  Chains onto the
// Mind-extras entry point (window.mindExtras) exactly like aux_trust.js and
// renders one card (#mind-extra-youmodel) into #view-mind grouping the typed
// You-Model files — Goals / Now / Looking for / Interests / Preferences — plus
// the people/*.md cards.  Each §-entry is an editable card; every write goes
// through the existing gated memory API (/api/youmodel/add, /api/memory/save,
// /api/memory/create, /api/memory/delete) — this file owns zero storage.
//
// Reuses index.html globals (esc, HA, animate, revealStagger, sendMsg,
// setView, openChat), all typeof-guarded so a headless render harness never
// throws.  Zero emoji, bespoke two-tone SVG only, 12-hour time, Motion One.

(function () {
  "use strict";

  // ---- self-hook: chain onto the existing Mind-extras entry point ----------
  var prev = window.mindExtras;
  window.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await youModelPanel(); } catch (e) {}
  };

  // The exact chat seed the "Run onboarding" button sends (WS 1.2 entry point).
  var ONBOARD_SEED =
    "Run my You-Model onboarding interview using the you-model-onboarding skill: " +
    "first seed priors from what you already know about me (USER.md, our past sessions), " +
    "then interview me one question at a time — about 8 to 12 questions. After each answer, " +
    "propose the exact memory write and wait for my yes before saving it.";

  // ---- tiny helpers --------------------------------------------------------
  function doc() { return (typeof document !== "undefined") ? document : null; }
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s); }
  // Motion goes through the runtime (docs/plans/motion-system-2026-10.md §6.5):
  // index.html's HA() is window.HermesAnim, or ANIM_NULL without aux_anim.js.
  // A() keeps animate()'s call shape (HA().legacy); the runtime lands the last
  // keyframe at once while EITHER reduce switch is on, the window is hidden or
  // the call is quiet. The load-time REDUCE flag it tested only saw the OS one.
  function ha() {
    try { if (typeof HA === "function") return HA(); } catch (e) {}
    return (typeof window !== "undefined" && window.HermesAnim) || null;
  }
  function A(node, kf, opt) {
    var H = ha();
    try { if (H) return H.legacy(node, kf, opt); if (typeof animate === "function") animate(node, kf, opt); } catch (e) {}
  }
  function each(list, cb) {
    if (!list) return;
    try { Array.prototype.slice.call(list).forEach(cb); } catch (e) {}
  }
  // 12-hour timestamp, e.g. "Jul 5, 11:43 PM"
  function ym12(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) return "";
    try {
      return new Date(n * 1000).toLocaleString("en-US",
        { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true });
    } catch (e) { return ""; }
  }
  function isHint(s) {
    s = String(s || "").trim();
    return s.indexOf("<!--") === 0 && s.lastIndexOf("-->") === s.length - 3;
  }
  var DELIM = "\n§\n";   // "\n§\n" — must match aux_memory ENTRY_DELIM

  // ---- bespoke two-tone SVG glyphs ----------------------------------------
  function ic(kind, cls) {
    var G = {
      model: '<circle cx="12" cy="8" r="3.4" fill="currentColor" opacity=".16"/>' +
        '<circle cx="12" cy="8" r="3.4"/><path d="M5.4 20a6.8 6.8 0 0 1 13.2 0" stroke-linecap="round"/>' +
        '<path d="M18.6 5.2l2 .6-.6 2M5.4 5.2l-2 .6.6 2" stroke-linecap="round" stroke-linejoin="round"/>',
      plus: '<path d="M12 5.5v13M5.5 12h13" stroke-linecap="round"/>',
      pencil: '<path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1z" fill="currentColor" opacity=".16"/>' +
        '<path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1z" stroke-linejoin="round"/>' +
        '<path d="M14.5 6.5l3 3" stroke-linecap="round"/>',
      x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11" stroke-linecap="round"/>',
      check: '<path d="M20 6.5L9.2 17.3 4.5 12.6" stroke-linecap="round" stroke-linejoin="round"/>',
      person: '<circle cx="12" cy="8.4" r="3.1" fill="currentColor" opacity=".16"/>' +
        '<circle cx="12" cy="8.4" r="3.1"/><path d="M6 19.4a6.2 6.2 0 0 1 12 0" stroke-linecap="round"/>',
      spark: '<path d="M12 3.5l1.8 5.4 5.4 1.8-5.4 1.8L12 18l-1.8-5.5-5.4-1.8 5.4-1.8z" ' +
        'fill="currentColor" opacity=".16"/><path d="M12 3.5l1.8 5.4 5.4 1.8-5.4 1.8L12 18l-1.8-5.5-5.4-1.8 5.4-1.8z" stroke-linejoin="round"/>'
    };
    return '<svg class="ymic ' + (cls || "") + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.7" aria-hidden="true">' + (G[kind] || "") + "</svg>";
  }

  // ---- one-time CSS --------------------------------------------------------
  function injectCss() {
    var d = doc(); if (!d || d.getElementById("ymcss")) return;
    var s = d.createElement("style");
    s.id = "ymcss";
    s.textContent = [
      "#mind-extra-youmodel .ymic{width:15px;height:15px;flex:0 0 auto}",
      "#mind-extra-youmodel h2 .ymic{width:17px;height:17px;color:var(--muted)}",
      ".ym-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}",
      ".ym-top .ym-sub{font-size:12px;color:var(--muted);flex:1;min-width:180px}",
      ".ym-onboard{display:inline-flex;align-items:center;gap:7px;font:inherit;font-size:12.5px;font-weight:640;",
      "padding:8px 15px;border-radius:10px;cursor:pointer;color:var(--ink);",
      "background:color-mix(in srgb,var(--iris) 24%,transparent);",
      "border:1px solid color-mix(in srgb,var(--iris) 55%,transparent);transition:background .15s}",
      ".ym-onboard:hover{background:color-mix(in srgb,var(--iris) 34%,transparent)}",
      ".ym-empty{padding:22px 14px;text-align:center;border:1px dashed var(--hairline);border-radius:14px;",
      "display:flex;flex-direction:column;align-items:center;gap:12px;margin-bottom:12px}",
      ".ym-empty .t{font-size:13.5px;color:var(--ink);max-width:420px;line-height:1.5}",
      ".ym-empty .bx{display:flex;gap:9px;flex-wrap:wrap;justify-content:center}",
      ".ym-sec{border-top:1px solid var(--hairline);padding:11px 0 13px}",
      ".ym-sec:first-of-type{border-top:none;padding-top:2px}",
      ".ym-sechead{display:flex;align-items:center;gap:9px;flex-wrap:wrap}",
      ".ym-sechead .lbl{font-size:12.5px;font-weight:650;color:var(--ink);display:inline-flex;align-items:center;gap:7px}",
      ".ym-sechead .cnt{font-size:10.5px;color:var(--faint)}",
      ".ym-meter{flex:0 1 110px;min-width:70px;height:4px;border-radius:3px;background:var(--hairline);",
      "overflow:hidden;position:relative;margin-left:auto}",
      // the fill scales from the left (compositor only, never a width); the
      // track's radius + overflow:hidden shape both ends, so the fill has none
      ".ym-meter i{position:absolute;inset:0;transform-origin:left center;transform:scaleX(var(--p,0));" +
        "background:var(--iris);transition:transform .3s ease}",
      ".ym-meter.warn i{background:var(--warn)}.ym-meter.bad i{background:var(--bad)}",
      ".ym-meter-n{font-size:10px;color:var(--faint);font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".ym-hint{font-size:11.5px;color:var(--faint);margin:3px 0 7px;line-height:1.45}",
      ".ym-rows{display:flex;flex-direction:column;gap:6px}",
      ".ym-row{display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border-radius:10px;",
      "background:var(--glass-2);border:1px solid var(--hairline);transition:opacity .2s}",
      ".ym-row .t{flex:1;min-width:0;font-size:12.5px;line-height:1.5;word-break:break-word;white-space:pre-wrap}",
      ".ym-row.saving{opacity:.5}",
      ".ym-row input,.ym-add input,.ym-ppl input{flex:1;min-width:0;font:inherit;font-size:12.5px;padding:6px 9px;",
      "border-radius:8px;border:1px solid color-mix(in srgb,var(--iris) 45%,transparent);",
      "background:var(--glass);color:var(--ink);outline:none}",
      ".ym-ib{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;width:25px;height:25px;",
      "border-radius:7px;border:1px solid transparent;background:transparent;color:var(--faint);cursor:pointer;",
      "transition:background .15s,color .15s}",
      ".ym-ib:hover{background:var(--glass);color:var(--ink)}",
      ".ym-ib.ok:hover{color:var(--ok)}.ym-ib.del:hover{color:var(--bad)}",
      ".ym-add{display:flex;align-items:center;gap:8px;margin-top:7px}",
      ".ym-btn{font:inherit;font-size:12px;font-weight:600;padding:6px 13px;border-radius:8px;",
      "border:1px solid var(--hairline);background:var(--glass-2);color:var(--ink);cursor:pointer;",
      "white-space:nowrap;transition:background .15s}",
      ".ym-btn:hover{background:var(--glass)}",
      ".ym-btn[disabled]{opacity:.45;cursor:not-allowed}",
      ".ym-ppl{display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap}",
      ".ym-ppl input.nm{flex:0 1 150px}",
      ".ym-prow{display:flex;align-items:flex-start;gap:9px;padding:8px 10px;border-radius:10px;",
      "background:var(--glass-2);border:1px solid var(--hairline)}",
      ".ym-prow .pmain{flex:1;min-width:0}",
      ".ym-prow .pn{font-size:12.5px;font-weight:640;color:var(--ink)}",
      ".ym-prow .pv{font-size:11.5px;color:var(--muted);margin-top:1px;word-break:break-word}",
      ".ym-prow .pw{font-size:10px;color:var(--faint);margin-top:2px}",
      ".ym-pta{width:100%;box-sizing:border-box;min-height:84px;resize:vertical;font-size:12px;line-height:1.5;",
      "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;padding:9px 10px;border-radius:9px;",
      "border:1px solid color-mix(in srgb,var(--iris) 45%,transparent);background:var(--glass);color:var(--ink);outline:none}",
      ".ym-err{color:var(--bad);font-size:11.5px;margin-top:6px;min-height:0}",
      ".ym-skel{height:44px;border-radius:10px;margin:8px 0;",
      "background:linear-gradient(90deg,var(--glass-2),var(--glass),var(--glass-2));",
      "background-size:200% 100%;animation:ymsh 1.3s linear infinite}",
      "@keyframes ymsh{0%{background-position:200% 0}100%{background-position:-200% 0}}",
      "@media (prefers-reduced-motion:reduce){.ym-skel{animation:none}.ym-meter i{transition:none}}"
    ].join("\n");
    (d.head || d.body || d.documentElement).appendChild(s);
  }

  // ---- card mount ----------------------------------------------------------
  function mount(grid, bodyHtml, tinyText) {
    var d = doc(); if (!d) return null;
    var old = d.getElementById("mind-extra-youmodel");
    if (old && old.remove) old.remove();
    var s = d.createElement("section");
    s.className = "card glass span2";
    s.id = "mind-extra-youmodel";
    s.innerHTML =
      "<h2>" + ic("model") + "Your Model" +
      '<span class="tiny" style="margin-left:auto">' + E(tinyText || "") + "</span></h2>" +
      '<div class="body">' + bodyHtml + "</div>";
    grid.appendChild(s);
    return s;
  }

  // ---- data + entry point --------------------------------------------------
  var YM = { data: null, lastErr: null };

  async function youModelPanel() {
    var d = doc(); if (!d) return;
    var grid = d.getElementById("view-mind");
    if (!grid) return;
    injectCss();
    mount(grid, '<div class="ym-skel"></div><div class="ym-skel"></div>', "");
    var data;
    try {
      var r = await fetch("/api/youmodel", { cache: "no-store" });
      data = await r.json();
    } catch (e) { return renderError(grid); }
    if (!data || data.ok === false) return renderError(grid);
    YM.data = data;
    render(grid, data);
  }

  function renderError(grid) {
    var s = mount(grid,
      '<div class="ym-hint">Couldn’t load your model. ' +
      '<button class="ym-btn" id="ym-retry" style="margin-left:6px">Retry</button></div>', "");
    if (!s) return;
    var b = s.querySelector("#ym-retry");
    if (b) b.addEventListener("click", function () { youModelPanel().catch(function () {}); });
  }

  // ---- render ---------------------------------------------------------------
  function meter(used, budget) {
    var pct = budget > 0 ? Math.min(100, Math.round(used / budget * 100)) : 0;
    var cls = pct >= 100 ? "bad" : (pct > 85 ? "warn" : "");
    return '<span class="ym-meter ' + cls + '"><i style="--p:' + (pct / 100) + '"></i></span>' +
      '<span class="ym-meter-n">' + used + " / " + budget + "</span>";
  }

  function render(grid, data) {
    var files = data.files || [], people = data.people || [];
    var tiny = data.filled + " of " + data.total + " areas · " +
      people.length + " " + (people.length === 1 ? "person" : "people");

    var h = '<div class="ym-top"><span class="ym-sub">What Hermes knows to think with — ' +
      "goals, current work, standing wants, interests, people. Yours to edit or delete, all local.</span>" +
      '<button class="ym-onboard" id="ym-onboard">' + ic("spark") + "Run onboarding</button></div>";

    if (data.empty) {
      h += '<div class="ym-empty">' +
        '<div class="t">Your model is empty — run onboarding so Hermes can start thinking for you.</div>' +
        '<div class="bx"><button class="ym-onboard" id="ym-onboard2">' + ic("spark") +
        "Run onboarding</button>" +
        (files.some(function (f) { return !f.exists; })
          ? '<button class="ym-btn" id="ym-seed">Create the empty files</button>' : "") +
        "</div></div>";
    }

    files.forEach(function (f, fi) {
      var real = (f.entries || []).filter(function (e) { return !isHint(e); });
      h += '<div class="ym-sec" data-file="' + E(f.name) + '">' +
        '<div class="ym-sechead"><span class="lbl">' + E(f.label) + "</span>" +
        '<span class="cnt">' + (f.exists ? real.length + (real.length === 1 ? " entry" : " entries")
          : "not created yet") + "</span>" +
        (f.exists ? meter(f.char_used || 0, f.char_budget || 4000) : "") + "</div>" +
        '<div class="ym-hint">' + E(f.hint || "") + "</div>" +
        '<div class="ym-rows">';
      real.forEach(function (t, i) {
        h += '<div class="ym-row" data-f="' + fi + '" data-i="' + i + '">' +
          '<span class="t">' + E(t) + "</span>" +
          '<button class="ym-ib" data-edit="1" title="Edit">' + ic("pencil") + "</button>" +
          '<button class="ym-ib del" data-rm="1" title="Remove">' + ic("x") + "</button></div>";
      });
      h += "</div>" +
        '<div class="ym-add"><input type="text" maxlength="2000" placeholder="Add to ' +
        E(f.label.toLowerCase()) + '…" autocomplete="off">' +
        '<button class="ym-btn" data-addto="' + E(f.name) + '">Add</button></div></div>';
    });

    // People
    h += '<div class="ym-sec" id="ym-people"><div class="ym-sechead">' +
      '<span class="lbl">' + ic("person") + "People</span>" +
      '<span class="cnt">' + people.length + (people.length === 1 ? " card" : " cards") + "</span></div>" +
      '<div class="ym-hint">One card per person — relationship, what they care about, open threads. ' +
      "Lives in memories/people/.</div>" +
      '<div class="ym-rows" id="ym-prows">';
    people.forEach(function (p, i) {
      h += '<div class="ym-prow" data-p="' + i + '">' +
        '<div class="pmain"><div class="pn">' + E(personTitle(p)) + "</div>" +
        '<div class="pv">' + E(p.preview || "") + "</div>" +
        (p.mtime ? '<div class="pw">updated ' + E(ym12(p.mtime)) + "</div>" : "") + "</div>" +
        '<button class="ym-ib" data-pedit="' + i + '" title="Edit card">' + ic("pencil") + "</button>" +
        '<button class="ym-ib del" data-pdel="' + i + '" title="Delete card">' + ic("x") + "</button></div>";
    });
    h += "</div>" +
      '<div class="ym-ppl"><input class="nm" type="text" maxlength="60" placeholder="Name" autocomplete="off">' +
      '<input class="nt" type="text" maxlength="300" placeholder="Who they are to you…" autocomplete="off">' +
      '<button class="ym-btn" id="ym-paddb">Add person</button></div></div>';

    h += '<div class="ym-err" id="ym-err"></div>';

    var s = mount(grid, h, tiny);
    if (!s) return;
    wire(s, data);
    // every write ends in refresh(), which rebuilds this card — so an error
    // reported a moment ago is shown again on the new card instead of being
    // wiped by it (2026-10-08 audit, P3 Frontend)
    if (YM.lastErr && Date.now() < YM.lastErr.until) showErr(YM.lastErr.msg, YM.lastErr.until);
    try {
      if (typeof revealStagger === "function")
        revealStagger(s.querySelectorAll(".ym-row,.ym-prow"), 28);
    } catch (e) {}
    A(s, { opacity: [0, 1], transform: ["translateY(8px)", "translateY(0)"] },
      { duration: 0.25, easing: "ease-out" });
  }

  // ---- error line ----------------------------------------------------------
  function err(msg) {
    YM.lastErr = msg ? { msg: msg, until: Date.now() + 5000 } : null;
    showErr(msg, YM.lastErr && YM.lastErr.until);
  }
  function showErr(msg, until) {
    var d = doc(); if (!d) return;
    var e = d.getElementById("ym-err");
    if (!e) return;
    e.textContent = msg || "";
    if (msg) setTimeout(function () { if (e) e.textContent = ""; },
                        Math.max(0, (until || Date.now() + 5000) - Date.now()));
  }

  async function postJSON(url, body) {
    YM.lastErr = null;     // a new write supersedes the last error (it is not re-shown)
    var r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    var j; try { j = await r.json(); } catch (e) { j = { ok: false, error: "bad response" }; }
    j._status = r.status;
    return j;
  }

  function refresh() { youModelPanel().catch(function () {}); }

  // ---- onboarding seed (WS 1.2 entry) ---------------------------------------
  function runOnboarding() {
    try {
      // the interview runs in the chat, which lives on the Agent page
      if (typeof openChat === "function") openChat();
      if (typeof sendMsg === "function") sendMsg(ONBOARD_SEED);
      else err("Chat isn’t available in this view.");
    } catch (e) { err("Couldn’t open the chat: " + String(e && e.message || e)); }
  }

  // ---- writes (all through the gated memory API) -----------------------------
  function fileRow(name) {
    var fs = (YM.data && YM.data.files) || [];
    for (var i = 0; i < fs.length; i++) if (fs[i].name === name) return fs[i];
    return null;
  }

  // rebuild a typed file's content from its full entry list (template preserved)
  async function saveEntries(f, nextReal) {
    var hints = (f.entries || []).filter(isHint);
    var all = hints.concat(nextReal);
    var res = await postJSON("/api/memory/save",
      { name: f.name, base_etag: f.etag, content: all.join(DELIM) });
    if (!res.ok) {
      if (res.error === "conflict") err("Hermes updated " + f.name + " meanwhile — reloaded.");
      else if (res.error === "locked") err("Hermes is writing to " + f.name + " — try again in a moment.");
      else err("Couldn’t save " + f.name + " — " + (res.error || "HTTP " + res._status));
    }
    refresh();
  }

  async function addEntry(name, text) {
    var res = await postJSON("/api/youmodel/add", { file: name, text: text });
    if (!res.ok) err("Couldn’t add — " + (res.error || "HTTP " + res._status));
    refresh();
  }

  async function seedFiles() {
    var res = await postJSON("/api/youmodel/seed", {});
    if (!res.ok) err("Couldn’t create the files — " + (res.error || "HTTP " + res._status));
    refresh();
  }

  // C127: the card's FILE name has to be ASCII (aux_memory's name rule), the
  // person's name does not. Accents fold to their base letter ("José García" ->
  // jose-garcia, not jos-garc-a), and a name with letters ASCII cannot spell —
  // Li Ming as 李明, Мария — gets a stable hash suffix instead of an empty slug
  // ("Give the person a name first") or a collision with every other name that
  // shares its Latin part. The name itself stays the card's first line.
  var SLUG_FOLD = { "ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "ł": "l", "đ": "d",
                    "ð": "d", "þ": "th", "ı": "i" };
  function nameHash(s) {                 // FNV-1a, 32 bit, 8 hex digits
    var h = 0x811c9dc5;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return ("0000000" + h.toString(16)).slice(-8);
  }
  function slugify(s) {
    var raw = String(s || "").trim();
    var low = raw.toLowerCase();
    try { low = low.normalize("NFKD").replace(/[\u0300-\u036f]/g, ""); } catch (e) {}
    low = low.replace(/[ßæœøłđðþı]/g, function (c) { return SLUG_FOLD[c] || c; })
      .replace(/['’]/g, "");
    var ascii = low.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    // anything left that is a letter or digit outside ASCII was dropped above
    var lossy = /[^\x00-\x7f]/.test(low.replace(/[\s\u2010-\u2015\u00b7.,;:!?()\[\]"“”«»]/g, ""));
    if (!lossy) return ascii.slice(0, 48).replace(/-+$/, "");
    var tag = nameHash(raw.normalize ? raw.normalize("NFC").toLowerCase() : raw.toLowerCase());
    return (ascii ? ascii.slice(0, 39).replace(/-+$/, "") + "-" : "person-") + tag;
  }
  // a card whose slug carries the hash shows the name from its first line
  function personTitle(p) {
    var sl = String(p && p.slug || "");
    var nm = String(p && p.preview || "").split(" — ")[0].trim();
    if (/-[0-9a-f]{8}$/.test(sl) && /[^\x00-\x7f]/.test(nm)) return nm;
    return sl;
  }

  async function addPerson(nameV, noteV) {
    var slug = slugify(nameV);
    if (!slug) { err("Give the person a name first."); return; }
    var content = nameV.trim() + (noteV.trim() ? " — " + noteV.trim() : "");
    var res = await postJSON("/api/memory/create",
      { name: "people/" + slug + ".md", content: content });
    if (!res.ok) {
      if (res.error === "exists") err("A card for “" + nameV.trim() + "” already exists.");
      else err("Couldn’t create the card — " + (res.error || "HTTP " + res._status));
    }
    refresh();
  }

  async function savePerson(p, content) {
    var res = await postJSON("/api/memory/save",
      { name: p.name, base_etag: p.etag, content: content });
    if (!res.ok) err("Couldn’t save " + p.slug + " — " + (res.error || "HTTP " + res._status));
    refresh();
  }

  async function deletePerson(p) {
    if (typeof confirm === "function" &&
        !confirm("Move the card for “" + personTitle(p) + "” to the dashboard trash?")) return;
    var res = await postJSON("/api/memory/delete", { name: p.name });
    if (!res.ok) err("Couldn’t delete — " + (res.error || "HTTP " + res._status));
    refresh();
  }

  // ---- wiring ----------------------------------------------------------------
  function wire(card, data) {
    var d = doc(); if (!d) return;

    each(["ym-onboard", "ym-onboard2"], function (id) {
      var b = card.querySelector("#" + id);
      if (b) b.addEventListener("click", runOnboarding);
    });
    var sb = card.querySelector("#ym-seed");
    if (sb) sb.addEventListener("click", function () { sb.disabled = true; seedFiles(); });

    // entry edit / remove
    each(card.querySelectorAll(".ym-row"), function (row) {
      var fi = parseInt(row.getAttribute("data-f"), 10);
      var i = parseInt(row.getAttribute("data-i"), 10);
      var f = (data.files || [])[fi];
      if (!f) return;
      function realList() {
        return (f.entries || []).filter(function (e) { return !isHint(e); });
      }
      var eb = row.querySelector("[data-edit]");
      if (eb) eb.addEventListener("click", function () {
        var cur = realList()[i] || "";
        // C128: an <input type=text> drops every newline from its value (the
        // HTML value-sanitisation rule), so editing a multi-line entry glued
        // its lines together on save. A multi-line entry edits in a textarea
        // whose value is set as a property; Enter adds a line, Cmd/Ctrl+Enter
        // saves. Single-line entries keep the one-line field.
        var multi = cur.indexOf("\n") >= 0;
        row.innerHTML = (multi
          ? '<textarea class="ym-pta" maxlength="2000" style="flex:1;min-width:0"></textarea>'
          : '<input type="text" maxlength="2000" value="' + E(cur) + '">') +
          '<button class="ym-ib ok" data-sv="1" title="Save">' + ic("check") + "</button>" +
          '<button class="ym-ib" data-cx="1" title="Cancel">' + ic("x") + "</button>";
        var inp = row.querySelector(multi ? "textarea" : "input");
        if (multi) inp.value = cur;
        try { inp.focus(); inp.select(); } catch (e) {}
        function save() {
          var v = (inp.value || "").trim();
          // a lone "§" line (typeable now that the editor is a textarea) is the
          // file's entry delimiter: it would split this entry in two on save —
          // refused like /api/youmodel/add refuses it
          if (("\n" + v + "\n").indexOf(DELIM) >= 0) {
            err("An entry can\u2019t contain a line with only \u00a7 on it.");
            return;
          }
          var next = realList();
          if (!v) next.splice(i, 1); else next[i] = v;
          row.classList.add("saving");
          saveEntries(f, next);
        }
        inp.addEventListener("keydown", function (e) {
          if (e.key === "Enter" && (!multi || e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
          if (e.key === "Escape") { e.preventDefault(); refresh(); }
        });
        row.querySelector("[data-sv]").addEventListener("click", save);
        row.querySelector("[data-cx]").addEventListener("click", refresh);
      });
      var rb = row.querySelector("[data-rm]");
      if (rb) rb.addEventListener("click", function () {
        var next = realList();
        next.splice(i, 1);
        row.classList.add("saving");
        saveEntries(f, next);
      });
    });

    // add-entry composers
    each(card.querySelectorAll("[data-addto]"), function (b) {
      var name = b.getAttribute("data-addto");
      var inp = b.parentNode ? b.parentNode.querySelector("input") : null;
      function go() {
        var v = inp && (inp.value || "").trim();
        if (!v) return;
        b.disabled = true;
        addEntry(name, v);
      }
      b.addEventListener("click", go);
      if (inp) inp.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); go(); }
      });
    });

    // people
    each(card.querySelectorAll("[data-pedit]"), function (b) {
      b.addEventListener("click", function () {
        var p = (data.people || [])[parseInt(b.getAttribute("data-pedit"), 10)];
        if (!p) return;
        var row = b.parentNode;
        if (p.truncated) { err("This card is large — edit it from the memory panel above."); return; }
        row.innerHTML = '<div class="pmain"><div class="pn">' + E(personTitle(p)) + "</div>" +
          '<textarea class="ym-pta"></textarea></div>' +
          '<button class="ym-ib ok" data-sv="1" title="Save">' + ic("check") + "</button>" +
          '<button class="ym-ib" data-cx="1" title="Cancel">' + ic("x") + "</button>";
        var ta = row.querySelector("textarea");
        ta.value = p.content || "";
        try { ta.focus(); } catch (e) {}
        row.querySelector("[data-sv]").addEventListener("click", function () {
          savePerson(p, ta.value);
        });
        row.querySelector("[data-cx]").addEventListener("click", refresh);
      });
    });
    each(card.querySelectorAll("[data-pdel]"), function (b) {
      b.addEventListener("click", function () {
        var p = (data.people || [])[parseInt(b.getAttribute("data-pdel"), 10)];
        if (p) deletePerson(p);
      });
    });
    var pb = card.querySelector("#ym-paddb");
    if (pb) pb.addEventListener("click", function () {
      var wrap = pb.parentNode;
      var nm = wrap.querySelector(".nm"), nt = wrap.querySelector(".nt");
      pb.disabled = true;   // addPerson refreshes; the button is rebuilt on render
      // ...except when the name is refused before any request (no refresh):
      // it used to stay disabled until the view was reopened (10-08 P3)
      Promise.resolve(addPerson(nm ? nm.value : "", nt ? nt.value : ""))
        .then(function () { pb.disabled = false; }, function () { pb.disabled = false; });
    });
  }

  // expose for the headless render harness / manual invocation
  window.youModelPanel = youModelPanel;
  window.YM_ONBOARD_SEED = ONBOARD_SEED;
})();
