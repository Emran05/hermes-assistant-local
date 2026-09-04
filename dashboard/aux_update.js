// aux_update.js — "Software Update" card for Settings › System & Data.
// Backend: aux_update.py (/api/version, /api/update/check|apply|status|channel).
//
// Placement WITHOUT touching aux_settings_shell.js: the card is rendered as a
// #mind-extra-update section. If the settings shell has already built its
// panels we append straight into #sec-system; otherwise we append to
// #view-mind and the shell's relocator re-homes it (an id that isn't in its
// CARD_MAP falls back to sec-system by design). Either way it lands in
// System & Data, and re-renders are idempotent — the same node is reused.
//
// It also puts a small dot on the header Settings gear (#tab-mind) when an
// update is waiting: a positioned <i> appended to the existing tab, plus one
// scoped style rule. No markup of anyone else's is modified.
//
// Design laws (CLAUDE.md): zero emoji (bespoke two-tone SVG), 12-hour clock,
// esc() on every interpolation, every global helper typeof-guarded so a
// headless harness can eval this file.
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

  var CARD_ID = "mind-extra-update";
  var POLL_MS = 2000;

  var GLYPH =
    '<svg class="updic" viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
    'stroke="currentColor" stroke-width="1.6" style="flex:0 0 auto">' +
    '<path d="M12 3v11" stroke-linecap="round"/>' +
    '<path d="M8.2 10.4 12 14.2l3.8-3.8" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<path d="M4 16.5v2A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5v-2" ' +
    'stroke-linecap="round"/>' +
    '<rect x="4" y="16.5" width="16" height="4.5" rx="2" fill="currentColor" opacity=".14" stroke="none"/>' +
    '</svg>';

  // ---- pure helpers (exported for the headless harness) --------------------

  // absolute 12-hour clock, per the repo's design law (never "3 minutes ago")
  function fmtWhen(ts) {
    if (!ts) return "never";
    try {
      var d = new Date(ts * (ts > 1e12 ? 1 : 1000));
      if (isNaN(d.getTime())) return "never";
      var h = d.getHours(), m = d.getMinutes(), ap = h >= 12 ? "PM" : "AM";
      h = h % 12; if (h === 0) h = 12;
      var t = h + ":" + (m < 10 ? "0" + m : m) + " " + ap;
      var today = new Date();
      var same = d.getFullYear() === today.getFullYear() &&
                 d.getMonth() === today.getMonth() && d.getDate() === today.getDate();
      if (same) return t;
      return (d.getMonth() + 1) + "/" + d.getDate() + " " + t;
    } catch (e) { return "never"; }
  }

  // ISO-8601 from the GitHub API -> "9/10/2026"
  function fmtDay(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return "";
      return (d.getMonth() + 1) + "/" + d.getDate() + "/" + d.getFullYear();
    } catch (e) { return ""; }
  }

  // release notes: ESCAPED text, first `max` lines, never rendered as markup
  function notesPreview(notes, max) {
    max = max || 12;
    var lines = String(notes == null ? "" : notes).replace(/\r/g, "").split("\n");
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    var head = lines.slice(0, max).map(function (l) { return E(l); }).join("\n");
    var rest = lines.length - max;
    if (rest > 0) head += "\n" + E("… " + rest + " more line" + (rest === 1 ? "" : "s") + " in the release notes");
    return head;
  }

  // does this release replace the app bundle? (a macOS app asset, or notes that
  // say so). Pre-update this is a heuristic; after an update the backend's
  // last_result.app_changed is authoritative.
  function releaseTouchesApp(check) {
    if (!check) return false;
    var assets = check.assets || [];
    for (var i = 0; i < assets.length; i++) {
      var n = String((assets[i] && assets[i].name) || "");
      if (/macos|\.app\.zip$|-arm64\.zip$/i.test(n)) return true;
    }
    return /(^|\s)app\//.test(String(check.notes || ""));
  }

  function bytes(n) {
    n = Number(n || 0);
    if (!n) return "";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function CSS() {
    return '<style>' +
      '#' + CARD_ID + ' .upd-head{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;margin:2px 0 4px}' +
      '#' + CARD_ID + ' .upd-ver{font-size:15px;font-weight:600}' +
      '#' + CARD_ID + ' .upd-sub{font-size:11.5px;color:var(--muted)}' +
      '#' + CARD_ID + ' .upd-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}' +
      '#' + CARD_ID + ' .upd-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 0}' +
      '#' + CARD_ID + ' .upd-row label{font-size:12px;color:var(--muted)}' +
      '#' + CARD_ID + ' select.upd-ch{padding:5px 9px;border-radius:8px;font-size:12.5px;color:inherit;' +
        'border:1px solid var(--hairline,rgba(255,255,255,.12));background:var(--glass-2,rgba(255,255,255,.04))}' +
      '#' + CARD_ID + ' button.upd-b{padding:6px 13px;border-radius:9px;font-size:12.5px;cursor:pointer;' +
        'border:1px solid var(--hairline,rgba(255,255,255,.12));background:var(--glass-2,rgba(255,255,255,.05));color:inherit}' +
      '#' + CARD_ID + ' button.upd-b:disabled{opacity:.5;cursor:default}' +
      '#' + CARD_ID + ' button.upd-go{border:0;background:var(--iris,#6b8afd);color:#fff;font-weight:600}' +
      '#' + CARD_ID + ' .upd-avail{margin-top:12px;padding:11px 13px;border-radius:12px;' +
        'border:1px solid color-mix(in srgb,var(--iris,#6b8afd) 45%,transparent);' +
        'background:color-mix(in srgb,var(--iris,#6b8afd) 12%,transparent)}' +
      '#' + CARD_ID + ' .upd-avail h3{margin:0 0 2px;font-size:13.5px;font-weight:650}' +
      '#' + CARD_ID + ' .upd-notes{margin:8px 0 0;padding:8px 10px;border-radius:9px;max-height:190px;overflow:auto;' +
        'background:var(--glass-2,rgba(255,255,255,.04));font-family:ui-monospace,SFMono-Regular,Menlo,monospace;' +
        'font-size:11.5px;line-height:1.5;white-space:pre-wrap;word-break:break-word}' +
      '#' + CARD_ID + ' .upd-warn{margin-top:9px;font-size:11.5px;line-height:1.5;color:var(--warn,#d99a2e)}' +
      '#' + CARD_ID + ' .upd-note{margin-top:9px;font-size:11.5px;line-height:1.5;color:var(--muted)}' +
      '#' + CARD_ID + ' .upd-log{margin-top:9px;padding:8px 10px;border-radius:9px;max-height:200px;overflow:auto;' +
        'background:rgba(0,0,0,.28);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;' +
        'line-height:1.45;white-space:pre-wrap;word-break:break-word}' +
      '#' + CARD_ID + ' .upd-err{font-size:11.5px;color:var(--bad,#d24c3c);margin-top:8px}' +
      '#' + CARD_ID + ' .upd-dirty{font-size:11.5px;color:var(--muted);margin-top:8px}' +
      '#' + CARD_ID + ' .upd-assets{margin-top:7px;display:flex;flex-wrap:wrap;gap:5px}' +
      '#' + CARD_ID + ' .upd-asset{font-size:10.5px;padding:1px 8px;border-radius:20px;color:var(--muted);' +
        'background:var(--glass-2,rgba(255,255,255,.06))}' +
      '#tab-mind{position:relative}' +
      '#tab-mind .upd-dot{position:absolute;top:2px;right:6px;width:7px;height:7px;border-radius:50%;' +
        'background:var(--iris,#6b8afd);box-shadow:0 0 0 2px var(--bg-2,rgba(0,0,0,.35));display:block}' +
      '</style>';
  }

  // ---- the card body -------------------------------------------------------
  // state = { ver, check, status, busy, msg, err }
  function cardHTML(state) {
    state = state || {};
    var ver = state.ver || {};
    var chk = state.check || {};
    var st = state.status || {};
    var running = !!st.running;

    var verLine = "Hermes Assistant v" + E(ver.version || "?") +
      (ver.commit ? ' <span class="upd-sub upd-mono">(' + E(ver.commit) + ")</span>" : "");
    var bits = [];
    bits.push(ver.checkout === "tarball" ? "tarball install" : "git checkout");
    if (ver.dirty) bits.push("local changes present");
    bits.push("checked " + E(fmtWhen(chk.checked_at)));

    var h = CSS() +
      '<h2 style="display:flex;align-items:center;gap:7px">' + GLYPH + "Software update</h2>" +
      '<div class="upd-head"><span class="upd-ver">' + verLine + "</span>" +
      '<span class="upd-sub">' + E(bits.join(" · ")) + "</span></div>";

    // channel + check row
    var tarball = ver.checkout === "tarball";
    h += '<div class="upd-row">' +
      '<label for="upd-channel">Channel</label>' +
      '<select class="upd-ch" id="upd-channel"' + (running ? " disabled" : "") + ">" +
      '<option value="stable"' + (ver.channel !== "main" ? " selected" : "") + ">Stable — release tags</option>" +
      '<option value="main"' + (ver.channel === "main" ? " selected" : "") +
      (tarball ? " disabled" : "") + ">Main — development branch" + (tarball ? " (git checkouts only)" : "") + "</option>" +
      "</select>" +
      '<button type="button" class="upd-b" data-act="check"' + (state.busy || running ? " disabled" : "") + ">" +
      (state.busy === "check" ? "Checking…" : "Check for updates") + "</button>" +
      "</div>";

    if (chk.error && !chk.update_available) {
      h += '<div class="upd-err">Update check: ' + E(chk.error) +
        (chk.stale ? " (showing the last successful check)" : "") + "</div>";
    }

    // the offer
    if (chk.update_available && chk.latest) {
      var appWarn = releaseTouchesApp(chk);
      h += '<div class="upd-avail">' +
        "<h3>" + E(chk.latest) + " is available</h3>" +
        '<div class="upd-sub">' +
        E(chk.source === "main"
          ? "origin/main is ahead of this checkout"
          : ("released " + (fmtDay(chk.published_at) || "recently"))) +
        (chk.url ? ' · <a href="' + E(chk.url) + '" target="_blank" rel="noreferrer noopener">release page</a>' : "") +
        "</div>";
      if (chk.notes) h += '<pre class="upd-notes">' + notesPreview(chk.notes, 12) + "</pre>";
      if ((chk.assets || []).length) {
        h += '<div class="upd-assets">' + chk.assets.slice(0, 6).map(function (a) {
          return '<span class="upd-asset">' + E(a.name) +
            (a.size ? " · " + E(bytes(a.size)) : "") + "</span>";
        }).join("") + "</div>";
      }
      h += '<div class="upd-row">' +
        '<button type="button" class="upd-b upd-go" data-act="apply" data-target="' +
        E(chk.source === "main" ? "latest" : chk.latest) + '"' +
        (running || state.busy || ver.dirty ? " disabled" : "") + ">" +
        (running ? "Updating…" : "Update to " + E(chk.latest)) + "</button></div>";
      if (ver.dirty) {
        h += '<div class="upd-dirty">This checkout has uncommitted changes, so the ' +
          "updater will not move it. Commit or stash them (or run " +
          '<span class="upd-mono">./update.sh --force</span> in a terminal, which stashes ' +
          "them for you) and check again.</div>";
      }
      if (appWarn) {
        h += '<div class="upd-warn">This release also ships a new app bundle. ' +
          "Updating replaces the dashboard code only; to replace the window itself run " +
          '<span class="upd-mono">./update.sh --rebuild-app</span>. Replacing the app bundle ' +
          "DROPS its Full Disk Access grant — you must re-add Hermes Assistant.app under " +
          "System Settings › Privacy &amp; Security › Full Disk Access afterwards, or the " +
          "Message Center goes blank.</div>";
      }
      h += '<div class="upd-note">When you update, the dashboard restarts itself; the app ' +
        "window reloads when it reconnects. Your data in ~/.hermes is never touched, and the " +
        "model servers stay asleep.</div>";
      h += "</div>";
    } else if (!running) {
      h += '<div class="upd-note">' +
        E(chk.latest && !chk.error
          ? "Up to date — " + chk.latest + " is the newest release."
          : "No newer release found.") +
        "</div>";
    }

    // live progress
    if (running || state.busy === "apply") {
      h += '<div class="upd-note">Updating' +
        (st.target ? " to " + E(st.target) : "") +
        " — the dashboard restarts itself when it finishes; this page reloads once it " +
        "reconnects. Safe to leave open.</div>";
      h += '<pre class="upd-log">' + E(st.log_tail || "starting…") + "</pre>";
    } else if (st.last_result) {
      var lr = st.last_result;
      h += '<div class="' + (lr.ok ? "upd-note" : "upd-err") + '">Last update: ' +
        E(lr.message || (lr.ok ? "ok" : "failed")) +
        (lr.finished_at ? " · " + E(fmtWhen(lr.finished_at)) : "") + "</div>";
      if (lr.ok && lr.app_changed) {
        h += '<div class="upd-warn">That release changed app/. Run ' +
          '<span class="upd-mono">./update.sh --rebuild-app</span> to replace the app bundle, ' +
          "then re-add Hermes Assistant.app under System Settings › Privacy &amp; Security › " +
          "Full Disk Access (an ad-hoc rebuild always drops that grant).</div>";
      }
    }

    if (state.msg) h += '<div class="upd-note">' + E(state.msg) + "</div>";
    if (state.err) h += '<div class="upd-err">' + E(state.err) + "</div>";
    h += '<div class="upd-note upd-sub">Terminal equivalent: ' +
      '<span class="upd-mono">./update.sh</span> in the install directory.</div>';
    return h;
  }

  // ---- live wiring ---------------------------------------------------------
  var S = { ver: null, check: null, status: null, busy: "", msg: "", err: "" };
  var timer = null, mounted = false;

  async function jget(url) {
    var r = await fetch(url, { headers: { "Accept": "application/json" } });
    return await r.json();
  }
  async function jpost(url, body) {
    var r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    var j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    return j || { ok: false, error: "HTTP " + r.status };
  }

  function host() {
    var d = D();
    if (!d) return null;
    return d.getElementById("sec-system") || d.getElementById("view-mind");
  }

  function paint() {
    var d = D();
    if (!d) return;
    var el = d.getElementById(CARD_ID);
    if (!el) {
      var h = host();
      if (!h) return;
      el = d.createElement("section");
      el.id = CARD_ID;
      el.className = "card glass";
      h.appendChild(el);
    } else if (el.parentNode && el.parentNode.id === "view-mind") {
      // the shell built its panels after we mounted — move ourselves in
      var sys = d.getElementById("sec-system");
      if (sys && sys !== el.parentNode) { try { sys.appendChild(el); } catch (e) {} }
    }
    try { el.innerHTML = cardHTML(S); } catch (e) { return; }
    wire(el);
    gearDot(!!(S.check && S.check.update_available));
  }

  function wire(el) {
    if (!el || !el.querySelector) return;
    var sel = el.querySelector("#upd-channel");
    if (sel) sel.onchange = function () { setChannel(sel.value); };
    var btns = el.querySelectorAll ? el.querySelectorAll("button[data-act]") : [];
    Array.prototype.slice.call(btns).forEach(function (b) {
      b.onclick = function () {
        var act = b.getAttribute("data-act");
        if (act === "check") doCheck(true);
        else if (act === "apply") doApply(b.getAttribute("data-target") || "latest");
      };
    });
  }

  function gearDot(on) {
    var d = D();
    if (!d) return;
    var tab = d.getElementById("tab-mind");
    if (!tab) return;
    var dot = tab.querySelector ? tab.querySelector(".upd-dot") : null;
    if (on && !dot) {
      dot = d.createElement("i");
      dot.className = "upd-dot";
      dot.setAttribute("title", "An update is available");
      dot.setAttribute("aria-hidden", "true");
      tab.appendChild(dot);
    } else if (!on && dot && dot.parentNode) {
      dot.parentNode.removeChild(dot);
    }
  }

  async function loadVersion() {
    try { S.ver = await jget("/api/version"); } catch (e) {}
  }

  async function doCheck(force) {
    S.busy = "check"; S.err = ""; S.msg = ""; paint();
    try {
      S.check = await jget("/api/update/check" + (force ? "?force=1" : ""));
    } catch (e) {
      S.err = "Could not reach the update service.";
    }
    S.busy = ""; paint();
  }

  async function setChannel(ch) {
    S.busy = "channel"; S.err = ""; paint();
    var r = await jpost("/api/update/channel", { channel: ch });
    S.busy = "";
    if (r && r.ok) {
      S.msg = "Channel set to " + ch + ".";
      await loadVersion();
      await doCheck(true);
    } else {
      S.err = (r && (r.error || r.reason)) || "Could not change the channel.";
      paint();
    }
  }

  async function doApply(target) {
    S.busy = "apply"; S.err = ""; S.msg = ""; paint();
    var r = await jpost("/api/update/apply", { target: target });
    S.busy = "";
    if (!r || !r.ok) {
      var msg = (r && (r.error || r.reason)) || "Could not start the update.";
      if (r && r.dirty && r.dirty.length) {
        msg += "  Uncommitted: " + r.dirty.slice(0, 8).join(", ") +
               (r.dirty.length > 8 ? " (+" + (r.dirty.length - 8) + " more)" : "");
      }
      S.err = msg; paint(); return;
    }
    S.msg = ""; S.status = { running: true, target: target, log_tail: "starting…" };
    paint();
    pollStatus();
  }

  var reconnecting = false;

  function pollStatus() {
    if (timer) { clearTimeout(timer); timer = null; }
    timer = setTimeout(async function () {
      var st = null;
      try {
        st = await jget("/api/update/status");
      } catch (e) {
        // the dashboard is restarting itself — expected, mid-update
        reconnecting = true;
        S.status = S.status || {};
        S.status.running = true;
        S.status.log_tail = (S.status.log_tail || "") + "\n[dashboard restarting…]";
        paint();
        pollStatus();
        return;
      }
      S.status = st;
      if (reconnecting && !st.running) {
        // came back after the restart: reload so the page matches the new code
        reconnecting = false;
        paint();
        try { if (W.location && W.location.reload) W.location.reload(); } catch (e) {}
        return;
      }
      paint();
      if (st && st.running) pollStatus();
      else {
        await loadVersion();
        await doCheck(true);
      }
    }, POLL_MS);
  }

  async function mount() {
    var d = D();
    if (!d || !host()) return;
    if (!mounted) {
      mounted = true;
      await loadVersion();
      await doCheck(false);          // cached: costs nothing, never blocks
      try {
        var st = await jget("/api/update/status");
        S.status = st;
        if (st && st.running) { paint(); pollStatus(); return; }
      } catch (e) {}
    }
    paint();
  }

  // chain window.mindExtras exactly like the other aux settings cards
  var prev = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prev === "function") { try { await prev(); } catch (e) {} }
    try { await mount(); } catch (e) {}
  };

  // the gear dot should appear even if the user never opens Settings
  async function boot() {
    try {
      await loadVersion();
      var c = await jget("/api/update/check");
      S.check = c;
      gearDot(!!(c && c.update_available));
    } catch (e) {}
  }
  try {
    if (typeof document !== "undefined" && document.readyState === "loading" &&
        document.addEventListener) {
      document.addEventListener("DOMContentLoaded", function () { boot(); }, { once: true });
    } else if (typeof document !== "undefined") {
      boot();
    }
  } catch (e) {}

  // headless-harness surface (also handy from the console)
  W.hermesUpdate = {
    cardHTML: cardHTML, notesPreview: notesPreview, fmtWhen: fmtWhen,
    fmtDay: fmtDay, releaseTouchesApp: releaseTouchesApp, bytes: bytes,
    check: doCheck, state: S
  };
})();
