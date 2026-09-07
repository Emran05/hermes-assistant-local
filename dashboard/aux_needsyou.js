// aux_needsyou.js — the "Needs you" attention stream (release 1.1.2).
//
// Auto-served at /aux_needsyou.js; the <script> tag lives at the end of
// index.html's aux list so these map assignments win. Adds:
//   * WICONS.needsyou        — bespoke two-tone glyph (widgetIcon)
//   * RENDER.needsyou        — the hub stream: a "Now" group (max 5), a
//                              collapsed "Today (n)", a "Later / Never"
//                              footer, and one quiet trust line
//   * EXPAND_RENDER.needsyou — the pop-out: every bucket, same actions, plus
//                              a "Why?" toggle that shows the RULE reason
//                              even when the optional model pass moved it
//
// Reads /api/needsyou (cached server-side, never blocks) and posts to
// /api/needsyou/act. Nothing here ever wakes the model: "Draft reply" posts
// the action and, when the server answers {error:"model asleep"}, offers the
// existing wake affordance instead of forcing a 30 s cold start.
//
// House rules honoured: zero emoji (bespoke SVG only), 12-hour times, esc()
// on every interpolation, tokens only (--bad = now, --warn = today,
// --muted = later), no cards inside cards (rows are flat, separated by a
// hairline), text-wrap: pretty, >=40px hit targets, explicit
// transition-property, and a light/dark palette that comes entirely from the
// shell's tokens so both themes are automatic.

(function () {
  "use strict";

  // ---- guarded helpers (headless node eval must never throw) --------------
  function E(s) {
    return (typeof esc === "function") ? esc(s)
      : String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
  }
  function REL(ts) {
    if (!ts) return "";
    if (typeof relTime === "function") return relTime(ts);
    var s = Date.now() / 1000 - ts;
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function T12(ts) {
    if (!ts) return "";
    try {
      return new Date(ts * 1000).toLocaleTimeString([],
        { hour: "numeric", minute: "2-digit" });
    } catch (e) { return ""; }
  }
  // a deadline reads better as a countdown than as a clock
  function DUE(ts) {
    if (!ts) return "";
    var s = ts - Date.now() / 1000;
    if (s < -3600) return "overdue " + Math.round(-s / 3600) + "h";
    if (s < 0) return "overdue";
    if (s < 3600) return "in " + Math.max(1, Math.round(s / 60)) + "m";
    if (s < 86400) return T12(ts);
    return "";
  }
  function PCT(v) {
    return (v == null) ? null : Math.round(v * 100) + "%";
  }

  // ---- bespoke glyphs, one per source ------------------------------------
  var GLYPH = {
    message:
      '<path d="M20 12a7 7 0 0 1-7 7H8l-4 3v-4.2A7 7 0 0 1 4 12a7 7 0 0 1 7-7h2a7 7 0 0 1 7 7z" fill="currentColor" opacity=".14"/>' +
      '<path d="M20 12a7 7 0 0 1-7 7H8l-4 3v-4.2A7 7 0 0 1 4 12a7 7 0 0 1 7-7h2a7 7 0 0 1 7 7z" stroke-linejoin="round"/>',
    calendar:
      '<rect x="3.5" y="5.5" width="17" height="15" rx="2.5" fill="currentColor" opacity=".14"/>' +
      '<rect x="3.5" y="5.5" width="17" height="15" rx="2.5"/>' +
      '<path d="M3.5 10h17M8 3.5v4M16 3.5v4" stroke-linecap="round"/>',
    watchtower:
      '<path d="M4.5 8.5h11l4-3v13l-4-3h-11z" fill="currentColor" opacity=".14"/>' +
      '<path d="M4.5 8.5h11l4-3v13l-4-3h-11z" stroke-linejoin="round"/>' +
      '<path d="M7.5 15.5v3.5" stroke-linecap="round"/>',
    approval:
      '<path d="M12 3.5l7 3v5.2c0 4.2-2.9 7.6-7 8.8-4.1-1.2-7-4.6-7-8.8V6.5z" fill="currentColor" opacity=".14"/>' +
      '<path d="M12 3.5l7 3v5.2c0 4.2-2.9 7.6-7 8.8-4.1-1.2-7-4.6-7-8.8V6.5z" stroke-linejoin="round"/>' +
      '<path d="M12 9v3.5M12 15.5h.01" stroke-linecap="round"/>',
    reminder:
      '<path d="M6.5 10a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5s1.5-1.5 1.5-5.5z" fill="currentColor" opacity=".14"/>' +
      '<path d="M6.5 10a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5s1.5-1.5 1.5-5.5z" stroke-linejoin="round"/>' +
      '<path d="M10 18.5a2 2 0 0 0 4 0" stroke-linecap="round"/>',
    email:
      '<rect x="3.5" y="5.5" width="17" height="13" rx="2.5" fill="currentColor" opacity=".14"/>' +
      '<rect x="3.5" y="5.5" width="17" height="13" rx="2.5"/>' +
      '<path d="M4 8l8 5.5L20 8" stroke-linecap="round" stroke-linejoin="round"/>'
  };
  function glyph(src) {
    var g = GLYPH[src] || GLYPH.message;
    return '<svg class="ny-ic" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="1.6" aria-hidden="true">' + g + "</svg>";
  }

  if (typeof WICONS !== "undefined") {
    // an inbox tray with one thing standing up out of it
    WICONS.needsyou =
      '<path d="M3.5 13.5h4l1.2 2.5h6.6l1.2-2.5h4v4a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" fill="currentColor" opacity=".16"/>' +
      '<path d="M3.5 13.5h4l1.2 2.5h6.6l1.2-2.5h4v4a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" stroke-linejoin="round"/>' +
      '<path d="M12 3.5v7M9 8l3 3 3-3" stroke-linecap="round" stroke-linejoin="round"/>';
  }

  // ---- styles: tokens only, injected once --------------------------------
  var NY_CSS = [
    ".ny-wrap{display:flex;flex-direction:column;gap:2px;text-wrap:pretty}",
    ".ny-grp{display:flex;align-items:center;gap:7px;font-size:10px;",
    "font-weight:680;letter-spacing:.07em;text-transform:uppercase;",
    "color:var(--muted);margin:9px 0 2px}",
    ".ny-grp:first-child{margin-top:1px}",
    ".ny-dot{width:7px;height:7px;border-radius:50%;flex:none}",
    ".ny-dot.now{background:var(--bad)}",
    ".ny-dot.today{background:var(--warn)}",
    ".ny-dot.later{background:var(--muted)}",
    // rows are FLAT — a card inside a card is the thing this hub already
    // gets wrong elsewhere; a hairline is enough separation.
    ".ny-row{display:flex;gap:10px;padding:9px 2px 8px;",
    "border-top:1px solid var(--hairline);align-items:flex-start}",
    ".ny-grp+.ny-row{border-top:none}",
    ".ny-ic{width:16px;height:16px;flex:none;margin-top:2px;color:var(--wac,var(--iris))}",
    ".ny-main{min-width:0;flex:1}",
    ".ny-top{display:flex;align-items:baseline;gap:8px}",
    ".ny-who{font-size:12.5px;font-weight:640;color:var(--ink);",
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".ny-when{margin-left:auto;font-size:10.5px;color:var(--faint);",
    "white-space:nowrap;flex:none;font-variant-numeric:tabular-nums}",
    ".ny-when.due{color:var(--bad);font-weight:640}",
    ".ny-sum{font-size:12px;color:var(--ink);opacity:.86;margin-top:1px;",
    "text-wrap:pretty;overflow-wrap:anywhere}",
    ".ny-why{font-size:11px;color:var(--muted);margin-top:3px;text-wrap:pretty}",
    ".ny-why b{font-weight:640;color:var(--muted)}",
    ".ny-acts{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}",
    // >=40px targets, and an explicit transition-property (never `all`)
    ".ny-b{min-height:40px;min-width:40px;padding:0 12px;border-radius:10px;",
    "border:1px solid var(--field-edge);background:var(--field);",
    "color:var(--ink);font-size:11.5px;font-weight:600;cursor:pointer;",
    "display:inline-flex;align-items:center;justify-content:center;",
    "transition-property:background-color,border-color,color,opacity;",
    "transition-duration:.16s;transition-timing-function:ease}",
    ".ny-b:hover{background:var(--chip);border-color:var(--edge)}",
    ".ny-b:disabled{opacity:.5;cursor:default}",
    ".ny-b.pri{background:color-mix(in srgb,var(--iris) 16%,transparent);",
    "border-color:color-mix(in srgb,var(--iris) 34%,transparent);color:var(--ink)}",
    ".ny-b.sm{font-size:11px;padding:0 10px}",
    ".ny-snz{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}",
    ".ny-more{min-height:40px;padding:0 12px;border-radius:10px;border:none;",
    "background:transparent;color:var(--muted);font-size:11.5px;",
    "font-weight:640;cursor:pointer;display:flex;align-items:center;gap:7px;",
    "width:100%;text-align:left;",
    "transition-property:color,background-color;transition-duration:.16s;",
    "transition-timing-function:ease}",
    ".ny-more:hover{color:var(--ink);background:var(--chip)}",
    ".ny-chev{width:13px;height:13px;flex:none;",
    "transition-property:transform;transition-duration:.18s;",
    "transition-timing-function:ease}",
    ".ny-more[aria-expanded=\"true\"] .ny-chev{transform:rotate(90deg)}",
    ".ny-foot{display:flex;align-items:center;gap:8px;flex-wrap:wrap;",
    "margin-top:9px;padding-top:8px;border-top:1px solid var(--hairline);",
    "font-size:11px;color:var(--muted);text-wrap:pretty}",
    ".ny-trust{margin-left:auto;font-size:10.5px;color:var(--faint);",
    "font-variant-numeric:tabular-nums;white-space:nowrap}",
    ".ny-empty{padding:14px 2px 8px;text-wrap:pretty}",
    ".ny-empty .h{font-size:13px;font-weight:620;color:var(--ink)}",
    ".ny-empty .s{font-size:11.5px;color:var(--muted);margin-top:4px}",
    ".ny-msg{font-size:11px;color:var(--muted);margin-top:6px;text-wrap:pretty}",
    ".ny-msg.bad{color:var(--bad)}",
    ".ny-src{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}",
    ".ny-chip{font-size:10px;padding:3px 8px;border-radius:999px;",
    "background:var(--chip);color:var(--muted);border:1px solid var(--hairline)}",
    ".ny-chip.off{opacity:.45}",
    ".ny-draft{margin-top:7px;padding:9px 10px;border-radius:10px;",
    "background:var(--chip);border:1px solid var(--hairline);font-size:11.5px;",
    "color:var(--ink);white-space:pre-wrap;text-wrap:pretty}"
  ].join("");

  function injectCSS() {
    try {
      if (document.getElementById("ny-style")) return;
      var s = document.createElement("style");
      s.id = "ny-style";
      s.textContent = NY_CSS;
      document.head.appendChild(s);
    } catch (e) {}
  }
  if (typeof document !== "undefined") injectCSS();

  var CHEV = '<svg class="ny-chev" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true"><polyline points="9 6 15 12 9 18"/></svg>';

  // ---- view state that must survive the hub's 60 s in-place re-render ----
  var UI = { snooze: "", today: false, why: false, draft: {}, msg: {} };

  function post(body) {
    return fetch("/api/needsyou/act", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  function reload(host, expanded) {
    return fetch(expanded ? "/api/expand?id=needsyou" : "/api/needsyou")
      .then(function (r) { return r.json(); })
      .then(function (d) { draw(host, d, host.__nyMeta, expanded); })
      .catch(function () {});
  }

  // ---- one row ------------------------------------------------------------
  // For people the SENDER is the identity; for everything else the thing
  // itself is ("Design review", "Renew passport", the headline) — showing
  // "Calendar" five times tells the reader nothing.
  function who(it) {
    return (it.source === "message" || it.source === "email")
      ? (it.sender || it.title) : (it.title || it.sender);
  }

  function row(it, expanded) {
    // an approval has no deadline of its own — it has been waiting, which is
    // a different and more honest thing to print than "overdue".
    var due = (it.source === "approval") ? "waiting" : DUE(it.deadline_ts);
    var when = (it.source === "approval")
      ? ("waiting " + (REL(it.received_at) || "")).trim()
      : (due || REL(it.received_at) || "");
    var h = '<div class="ny-row" data-id="' + E(it.id) + '">' +
      '<span class="ny-g">' + glyph(it.source) + "</span>" +
      '<div class="ny-main">' +
      '<div class="ny-top"><span class="ny-who">' + E(who(it)) + "</span>" +
      '<span class="ny-when' + (due ? " due" : "") + '">' + E(when) + "</span></div>";
    if (it.summary && it.summary !== who(it)) {
      h += '<div class="ny-sum">' + E(it.summary) + "</div>";
    }
    h += '<div class="ny-why"' + (expanded && !UI.why ? ' hidden' : '') + ">" +
      E(it.reason || "") +
      ((expanded && it.rule_reason && it.rule_reason !== it.reason)
        ? ' <b>&middot; rule said:</b> ' + E(it.rule_reason) : "") +
      "</div>";
    h += '<div class="ny-acts">' +
      '<button class="ny-b" data-a="done">Done</button>' +
      '<button class="ny-b" data-a="snz">Snooze</button>' +
      '<button class="ny-b" data-a="open">Open</button>';
    if (it.source === "message" || it.source === "email") {
      h += '<button class="ny-b pri" data-a="draft">Draft reply</button>';
    }
    if (expanded) {
      h += '<button class="ny-b sm" data-a="rc" data-to="' +
        (it.bucket === "later" ? "today" : "later") + '">Move to ' +
        (it.bucket === "later" ? "today" : "later") + "</button>" +
        '<button class="ny-b sm" data-a="rc" data-to="never">Never</button>';
    }
    h += "</div>";
    if (UI.snooze === it.id) {
      h += '<div class="ny-snz">' +
        '<button class="ny-b sm" data-a="snooze" data-u="1h">1 hour</button>' +
        '<button class="ny-b sm" data-a="snooze" data-u="evening">This evening</button>' +
        '<button class="ny-b sm" data-a="snooze" data-u="tomorrow">Tomorrow</button>' +
        "</div>";
    }
    if (UI.draft[it.id]) {
      h += '<div class="ny-draft">' + E(UI.draft[it.id]) + "</div>";
    }
    if (UI.msg[it.id]) {
      h += '<div class="ny-msg' + (UI.msg[it.id].bad ? " bad" : "") + '">' +
        E(UI.msg[it.id].text) + "</div>";
    }
    return h + "</div></div>";
  }

  // ---- the trust line -----------------------------------------------------
  function trust(m) {
    m = m || {};
    var p = PCT(m.now_precision);
    var bits = [];
    bits.push(p ? ("now precision " + p) : "now precision — not enough data yet");
    if (m.now_snoozed) bits.push(m.now_snoozed + " snoozed");
    if (m.reclassified) bits.push(m.reclassified + " re-filed");
    return bits.join(" · ");
  }

  // ---- the whole body -----------------------------------------------------
  function draw(host, d, mslot, expanded) {
    d = d || {};
    host.__nyData = d;
    host.__nyMeta = mslot;
    var now = d.now || [], today = d.today || [], later = d.later || [];
    var nNever = d.never_count || 0;

    if (mslot) {
      mslot.textContent = d.building ? "reading your day…"
        : (now.length ? (now.length + (now.length === 1 ? " now" : " now"))
          : (today.length ? today.length + " today" : "clear"));
    }

    var h = '<div class="ny-wrap">';

    if (d.building) {
      h += '<div class="ny-empty"><div class="h">Reading your day…</div>' +
        '<div class="s">Messages, calendar, reminders, alerts and anything ' +
        'waiting on your approval.</div></div>';
      host.innerHTML = h + "</div>";
      return;
    }

    if (!now.length && !today.length && !later.length) {
      var nb = d.next_brief;
      var label = { brief: "morning brief", midday: "midday pulse",
                    evening: "evening wrap" };
      h += '<div class="ny-empty"><div class="h">Nothing needs you right now</div>' +
        '<div class="s">' +
        (nb ? ("Next " + E(label[nb.which] || "brief") + " at " + E(T12(nb.ts)) + ".")
            : "Nothing scheduled — briefings are off.") +
        "</div></div>";
      h += sources(d);
      h += '<div class="ny-foot"><span>Never ' + nNever + "</span>" +
        '<span class="ny-trust">' + E(trust(d.metrics)) + "</span></div>";
      host.innerHTML = h + "</div>";
      wire(host, expanded);
      return;
    }

    // NOW — the only group that is open by default, capped at five
    if (now.length) {
      h += '<div class="ny-grp"><span class="ny-dot now"></span>Now</div>';
      now.slice(0, 5).forEach(function (it) { h += row(it, expanded); });
    }

    // TODAY — collapsed in the widget, always open in the pop-out
    if (today.length) {
      if (expanded) {
        h += '<div class="ny-grp"><span class="ny-dot today"></span>Today</div>';
        today.forEach(function (it) { h += row(it, expanded); });
      } else {
        h += '<button class="ny-more" data-a="toggle-today" aria-expanded="' +
          (UI.today ? "true" : "false") + '">' + CHEV +
          "Today (" + today.length + ")</button>";
        if (UI.today) today.forEach(function (it) { h += row(it, expanded); });
      }
    }

    // LATER — a line in the widget, a real group in the pop-out
    if (expanded && later.length) {
      h += '<div class="ny-grp"><span class="ny-dot later"></span>Later</div>';
      later.forEach(function (it) { h += row(it, expanded); });
    }

    h += '<div class="ny-foot"><span>Later ' + (d.later_count == null
      ? later.length : d.later_count) + " · Never " + nNever + "</span>";
    if (expanded) {
      h += '<button class="ny-b sm" data-a="toggle-why" style="min-height:40px">' +
        (UI.why ? "Hide why" : "Why?") + "</button>";
    }
    h += '<span class="ny-trust">' + E(trust(d.metrics)) + "</span></div>";
    if (expanded) h += sources(d);

    host.innerHTML = h + "</div>";
    wire(host, expanded);
  }

  function sources(d) {
    var s = d.sources || {};
    var order = ["message", "calendar", "reminder", "approval", "watchtower",
                 "email"];
    var name = { message: "Messages", calendar: "Calendar",
                 reminder: "Reminders", approval: "Approvals",
                 watchtower: "Watchtower", email: "Mail" };
    var out = "";
    order.forEach(function (k) {
      if (!(k in s)) return;
      var on = s[k] === "present";
      out += '<span class="ny-chip' + (on ? "" : " off") + '">' +
        E(name[k]) + (on ? "" : " — not connected") + "</span>";
    });
    return out ? '<div class="ny-src">' + out + "</div>" : "";
  }

  // ---- interaction --------------------------------------------------------
  function wire(host, expanded) {
    var btns = host.querySelectorAll("button[data-a]");
    for (var i = 0; i < btns.length; i++) {
      (function (b) {
        b.onclick = function (ev) {
          ev.preventDefault();
          ev.stopPropagation();              // never let the card pop out
          var a = b.getAttribute("data-a");
          var rowEl = b.closest ? b.closest(".ny-row") : null;
          var id = rowEl ? rowEl.getAttribute("data-id") : "";

          if (a === "toggle-today") { UI.today = !UI.today; return redraw(host, expanded); }
          if (a === "toggle-why") { UI.why = !UI.why; return redraw(host, expanded); }
          if (a === "snz") {
            UI.snooze = (UI.snooze === id) ? "" : id;
            return redraw(host, expanded);
          }
          if (!id) return;

          if (a === "open") {
            post({ id: id, action: "open" }).then(function (r) {
              openTarget(host, id, (r && r.open) || "", (r && r.ref) || "");
            });
            return;
          }
          if (a === "draft") {
            b.disabled = true;
            UI.msg[id] = { text: "Drafting a reply…" };
            redraw(host, expanded);
            post({ id: id, action: "draft" }).then(function (r) {
              if (r && r.ok && r.job) { pollDraft(host, expanded, id, r.job); return; }
              var asleep = r && r.error === "model asleep";
              UI.msg[id] = {
                bad: true,
                text: asleep ? "The model is asleep. Wake it to draft a reply."
                  : ((r && r.error) || "Could not start a draft.")
              };
              redraw(host, expanded);
              if (asleep) offerWake(host, expanded, id);
            }).catch(function () {
              UI.msg[id] = { bad: true, text: "Could not start a draft." };
              redraw(host, expanded);
            });
            return;
          }

          var body = { id: id, action: a === "rc" ? "reclassify" : a };
          if (a === "snooze") body.until = b.getAttribute("data-u") || "1h";
          if (a === "rc") body.to = b.getAttribute("data-to") || "later";
          b.disabled = true;
          UI.snooze = "";
          post(body).then(function () {
            delete UI.msg[id];
            delete UI.draft[id];
            reload(host, expanded);
          }).catch(function () { b.disabled = false; });
        };
      })(btns[i]);
    }
  }

  function redraw(host, expanded) {
    draw(host, host.__nyData, host.__nyMeta, expanded);
  }

  // "Open" routes to the surface that actually holds the thing.
  function openTarget(host, id, target, ref) {
    try {
      if (target === "main") {
        if (typeof setView === "function") setView("hub");
        if (typeof closePop === "function") closePop();
        var msgs = document.getElementById("msgs");
        if (msgs) msgs.scrollTop = msgs.scrollHeight;
        return;
      }
      if (target === "framing" && ref && /^https?:/i.test(ref)) {
        window.open(ref, "_blank", "noopener");
        return;
      }
      if (typeof openPop === "function" && target) { openPop(target); return; }
    } catch (e) {}
  }

  // The wake button is the shell's own affordance — we only surface it.
  function offerWake(host, expanded, id) {
    var rowEl = host.querySelector('.ny-row[data-id="' +
      String(id).replace(/["\\]/g, "\\$&") + '"]');
    if (!rowEl) return;
    var acts = rowEl.querySelector(".ny-acts");
    if (!acts || acts.querySelector('[data-a="wake"]')) return;
    var b = document.createElement("button");
    b.className = "ny-b pri";
    b.setAttribute("data-a", "wake");
    b.textContent = "Wake the model";
    b.onclick = function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      b.disabled = true;
      UI.msg[id] = { text: "Waking the model — about 30 seconds…" };
      redraw(host, expanded);
      fetch("/api/agent/wake", { method: "POST" })
        .then(function () {
          UI.msg[id] = { text: "Model is waking. Hit Draft reply again." };
          redraw(host, expanded);
        })
        .catch(function () {
          UI.msg[id] = { bad: true, text: "Could not wake the model." };
          redraw(host, expanded);
        });
    };
    acts.appendChild(b);
  }

  function pollDraft(host, expanded, id, job) {
    var tries = 0;
    var tick = function () {
      tries++;
      if (tries > 90) {
        UI.msg[id] = { bad: true, text: "The draft is taking too long." };
        return redraw(host, expanded);
      }
      fetch("/api/chat/poll?job=" + encodeURIComponent(job))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d || !d.ok) {
            UI.msg[id] = { bad: true, text: "The draft job went away." };
            return redraw(host, expanded);
          }
          if (d.done) {
            delete UI.msg[id];
            UI.draft[id] = d.reply || "(the agent returned nothing)";
            return redraw(host, expanded);
          }
          UI.msg[id] = { text: d.status || "Drafting a reply…" };
          redraw(host, expanded);
          setTimeout(tick, 1200);
        })
        .catch(function () {
          UI.msg[id] = { bad: true, text: "Lost the draft job." };
          redraw(host, expanded);
        });
    };
    setTimeout(tick, 900);
  }

  // ---- registry -----------------------------------------------------------
  if (typeof RENDER !== "undefined") {
    RENDER.needsyou = function (body, data, mslot) {
      injectCSS();
      draw(body, data, mslot, false);
    };
  }
  if (typeof EXPAND_RENDER !== "undefined") {
    EXPAND_RENDER.needsyou = function (el, d) {
      injectCSS();
      draw(el, d, null, true);
    };
  }

  // exported for the headless render harness
  if (typeof window !== "undefined") {
    window.__needsyou = { draw: draw, row: row, trust: trust, UI: UI };
  }
})();
