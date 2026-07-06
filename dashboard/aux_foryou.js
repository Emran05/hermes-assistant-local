// aux_foryou.js — "For You" Agent-Inbox widget renderers (WS 1.5).
//
// Auto-served at /aux_foryou.js. Loaded AFTER /expand.js so these map
// assignments win. Adds:
//   * WICONS.foryou        — bespoke two-tone compass glyph
//   * RENDER.foryou        — compact widget body: top ranked moves, each with
//                            its why-you line, suggested action, source link
//                            and Useful/Noise buttons (-> /api/foryou/react)
//   * EXPAND_RENDER.foryou — pop-out: the full ranked queue + reaction stats +
//                            a "personalize me" onboarding nudge when
//                            personalized:false
//
// Reuses index.html globals (esc, relTime, wireLinks, animate, askAbout) —
// all typeof-guarded so a headless render harness never throws.
// CLAUDE.md laws: zero emoji, bespoke SVG only, 12-hour times.
// NOTIFY-ONLY: this panel never pings anything; reactions only log.

(function () {
  "use strict";

  // ---- guarded helpers ------------------------------------------------------
  function E(s) { return (typeof esc === "function") ? esc(s) : String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function T12(sec) {                     // epoch secs -> "3:45 PM"
    if (sec == null) return "";
    try { return new Date(sec * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
    catch (e) { return ""; }
  }
  function REL(sec) {                     // epoch secs -> "3h ago" (guarded)
    if (sec == null) return "";
    if (typeof relTime === "function") { try { return relTime(sec); } catch (e) {} }
    var d = Math.max(0, Date.now() / 1000 - sec);
    if (d < 3600) return Math.max(1, Math.round(d / 60)) + "m ago";
    if (d < 86400) return Math.round(d / 3600) + "h ago";
    return Math.round(d / 86400) + "d ago";
  }
  function WL(el) { if (typeof wireLinks === "function") { try { wireLinks(el); } catch (e) {} } }
  function sub(t) {
    return '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;' +
      'color:var(--muted);font-weight:600;margin:12px 0 6px">' + E(t) + "</div>";
  }

  // small inline SVG glyphs (bespoke, stroke = currentColor)
  var IC_UP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:11px;height:11px;display:inline-block;vertical-align:-1px"><path d="M12 19V5M5 12l7-7 7 7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var IC_DN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:11px;height:11px;display:inline-block;vertical-align:-1px"><path d="M12 5v14M19 12l-7 7-7-7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var IC_ARROW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:11px;height:11px;display:inline-block;vertical-align:-1px;margin-right:3px"><path d="M5 12h14M13 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var BTN_CSS = "border:1px solid var(--hairline);background:var(--glass-2);color:var(--muted);" +
    "border-radius:7px;padding:2px 8px;font:inherit;font-size:10px;cursor:pointer;line-height:1.5";

  function reactBtns(mv) {
    var u = E(mv.url || "");
    if (mv.reaction) {
      return '<span class="w-sub" style="font-size:10px;color:var(--faint)">marked ' + E(mv.reaction) + "</span>";
    }
    return '<button class="fy-react" data-url="' + u + '" data-re="useful" title="Useful — more like this" style="' + BTN_CSS + '">' + IC_UP + " Useful</button>" +
      '<button class="fy-react" data-url="' + u + '" data-re="noise" title="Noise — less like this" style="' + BTN_CSS + '">' + IC_DN + " Noise</button>";
  }

  function wireReacts(root) {
    if (!root || !root.querySelectorAll) return;
    var btns = root.querySelectorAll(".fy-react");
    for (var i = 0; i < btns.length; i++) {
      (function (btn) {
        btn.onclick = function (ev) {
          if (ev && ev.stopPropagation) ev.stopPropagation();
          var url = btn.getAttribute("data-url"), re = btn.getAttribute("data-re");
          if (!url || !re) return;
          btn.disabled = true;
          fetch("/api/foryou/react", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: url, reaction: re })
          }).then(function (r) { return r.json(); }).then(function (x) {
            var row = btn.parentNode;
            if (x && x.ok && row) {
              row.innerHTML = '<span class="w-sub" style="font-size:10px;color:var(--faint)">marked ' + E(re) + " — tunes future picks</span>";
            } else { btn.disabled = false; }
          }).catch(function () { btn.disabled = false; });
        };
      })(btns[i]);
    }
  }

  function scoreBar(score, accent) {
    if (score == null) return "";
    var pct = Math.max(4, Math.min(100, Math.round(score * 100)));
    return '<div title="relevance ' + pct + '%" style="height:3px;border-radius:2px;background:var(--hairline);overflow:hidden;margin:5px 0 0">' +
      '<i style="display:block;height:100%;width:' + pct + '%;border-radius:2px;background:' + accent + '"></i></div>';
  }

  function moveCard(mv, compact) {
    var h = '<div class="fy-move" style="padding:8px 0;border-bottom:1px solid var(--hairline)">';
    h += '<div style="min-width:0"><a href="#" data-url="' + E(mv.url || "") + '" style="font-weight:600;font-size:' + (compact ? "12px" : "13px") + ';line-height:1.35">' + E(mv.title || "(untitled)") + "</a></div>";
    if (mv.why_you) {
      h += '<div class="w-sub" style="font-size:' + (compact ? "10.5px" : "11.5px") + ';line-height:1.45;margin-top:2px;color:var(--muted)">' + E(mv.why_you) + "</div>";
    }
    if (mv.suggested_action) {
      h += '<div style="font-size:' + (compact ? "10.5px" : "11.5px") + ';margin-top:3px;color:var(--wac);font-weight:600">' + IC_ARROW + E(mv.suggested_action) + "</div>";
    }
    h += '<div style="display:flex;align-items:center;gap:6px;margin-top:5px;flex-wrap:wrap">' +
      '<span class="w-sub" style="font-size:10px;color:var(--faint)">' + E(mv.source || "") + (mv.ts ? " · " + E(REL(mv.ts)) : "") + "</span>" +
      '<span style="margin-left:auto;display:flex;gap:5px;align-items:center">' + reactBtns(mv) + "</span></div>";
    if (!compact) h += scoreBar(mv.score, "var(--wac)");
    h += "</div>";
    return h;
  }

  function nudge(full) {
    return '<div class="hint" style="margin-top:8px;padding:8px 10px;border:1px dashed var(--hairline);border-radius:9px">' +
      "Generic picks for now — Hermes has no model of you yet. " +
      (full
        ? 'Run the onboarding interview so these become moves tied to <i>your</i> goals and people. <button id="fy-onboard" class="primary" style="margin-top:6px;display:block;padding:5px 12px">Personalize me</button>'
        : "Complete onboarding to personalize.") +
      "</div>";
  }

  // ---- bespoke widget icon: a compass (find your move) ----------------------
  if (typeof WICONS !== "undefined") {
    WICONS.foryou =
      '<circle cx="12" cy="12" r="8.6" fill="currentColor" opacity=".13"/>' +
      '<circle cx="12" cy="12" r="8.6"/>' +
      '<path d="M15.2 8.8l-1.7 4.4-4.4 1.7 1.7-4.4z" fill="currentColor" stroke="none"/>' +
      '<circle cx="12" cy="12" r="1.1" fill="currentColor" stroke="none"/>';
  }

  // ---- compact widget body ---------------------------------------------------
  if (typeof RENDER !== "undefined") {
    RENDER.foryou = function (body, data, mslot) {
      var d = data || {};
      if (d.available === false) {
        body.innerHTML = '<div class="hint">For You is unavailable.</div>';
        return;
      }
      if (mslot) {
        mslot.textContent = d.building ? "thinking…"
          : d.personalized ? (d.reasoned ? "personalized" : "matched") : "generic";
      }
      var moves = d.moves || [];
      if (d.building && !moves.length) {
        body.innerHTML = '<div class="hint">Reasoning over the latest world signal for you — first pass lands shortly.</div>';
        return;
      }
      if (!moves.length) {
        body.innerHTML = '<div class="hint">' + E(d.note || "No moves matched your model this pass — the loop retries every 2 hours.") + "</div>";
        return;
      }
      var h = "";
      for (var i = 0; i < Math.min(3, moves.length); i++) h += moveCard(moves[i], true);
      if (!d.personalized) h += nudge(false);
      else if (d.generated_at) h += '<div class="w-sub" style="font-size:10px;color:var(--faint);margin-top:6px">' + E(d.count || moves.length) + " moves · updated " + E(T12(d.generated_at)) + "</div>";
      body.innerHTML = h;
      WL(body);
      wireReacts(body);
    };
  }

  // ---- rich pop-out: the full Agent-Inbox queue -------------------------------
  if (typeof EXPAND_RENDER !== "undefined") {
    EXPAND_RENDER.foryou = function (el, d) {
      d = d || {};
      var moves = d.moves || [], rx = d.reactions || {};
      var h = "";
      h += '<div style="display:flex;align-items:baseline;gap:10px;margin:2px 0 2px">' +
        '<span class="num" style="font-size:34px;font-weight:730;line-height:1;color:var(--wac)">' + moves.length + "</span>" +
        '<span style="font-size:13px;color:var(--muted)">move' + (moves.length === 1 ? "" : "s") + " for you</span>" +
        (d.generated_at ? '<span class="w-sub" style="margin-left:auto;font-size:11px">updated ' + E(T12(d.generated_at)) + " · " + E(REL(d.generated_at)) + "</span>" : "") +
        "</div>";
      h += '<div class="w-sub" style="font-size:11px;margin-bottom:4px">' +
        (d.personalized
          ? (d.reasoned ? "Ranked by your goals, projects and people — each with why it is for you."
            : "Matched to your model lexically" + (d.note ? " (" + E(d.note) + ")" : "") + ".")
          : "Not yet personalized — showing the raw top of the intel feed.") +
        ((rx.useful || rx.noise) ? " · feedback so far: " + (rx.useful || 0) + " useful / " + (rx.noise || 0) + " noise" : "") +
        "</div>";
      if (!d.personalized) h += nudge(true);
      if (moves.length) {
        h += sub(d.personalized ? "Your queue — do / meet / go" : "Top of the world feed");
        for (var i = 0; i < moves.length; i++) {
          var mv = moves[i];
          h += '<div class="fy-xmove" style="display:flex;gap:10px;padding:2px 0">' +
            '<span class="num" style="flex:0 0 18px;text-align:right;color:var(--faint);font-size:11px;padding-top:10px">' + (i + 1) + "</span>" +
            '<div style="flex:1;min-width:0">' + moveCard(mv, false) + "</div></div>";
          if (mv.matched_goal || mv.matched_person) {
            h += '<div style="margin:-4px 0 4px 28px;display:flex;gap:5px;flex-wrap:wrap">' +
              (mv.matched_goal ? '<span class="w-sub" style="font-size:10px;border:1px solid var(--hairline);border-radius:6px;padding:1px 7px">goal: ' + E(mv.matched_goal) + "</span>" : "") +
              (mv.matched_person ? '<span class="w-sub" style="font-size:10px;border:1px solid var(--hairline);border-radius:6px;padding:1px 7px">person: ' + E(mv.matched_person) + "</span>" : "") +
              "</div>";
          }
        }
      } else {
        h += '<div class="hint" style="margin-top:8px">' + E(d.note || "Nothing in the queue yet.") + "</div>";
      }
      h += '<div class="w-sub" style="font-size:10.5px;color:var(--faint);line-height:1.5;margin-top:12px">' +
        "Moves queue here instead of pinging you — the loop re-reasons every ~2 hours and when fresh intel lands. " +
        "Useful/Noise tunes which sources and goals surface next. Nothing here acts on your behalf.</div>";

      el.innerHTML = h;
      WL(el);
      wireReacts(el);

      var ob = el.querySelector ? el.querySelector("#fy-onboard") : null;
      if (ob) {
        ob.onclick = function (ev) {
          if (ev && ev.stopPropagation) ev.stopPropagation();
          if (typeof askAbout === "function") {
            askAbout("Run my onboarding interview: ask me one question at a time about my goals, current projects, what I'm looking for, my interests, and the key people in my life — then save them to GOALS.md, NOW.md, LOOKING-FOR.md, INTERESTS.md and people cards in memory.", true);
            if (typeof closePop === "function") { try { closePop(); } catch (e) {} }
          } else {
            ob.textContent = "Open the chat and ask Hermes to run your onboarding interview.";
          }
        };
      }

      // subtle staggered reveal (Motion One, fully guarded)
      try {
        if (typeof animate === "function" && el.querySelectorAll) {
          var rows = el.querySelectorAll(".fy-xmove");
          for (var k = 0; k < rows.length; k++) {
            animate(rows[k], { opacity: [0, 1], transform: ["translateY(6px)", "translateY(0)"] },
              { duration: 0.4, delay: k * 0.04, easing: [0.2, 0.7, 0.3, 1] });
          }
        }
      } catch (e) {}
    };
  }
})();
