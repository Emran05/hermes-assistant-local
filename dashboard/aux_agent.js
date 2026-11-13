/* aux_agent.js — the flagship Agent page (UI restructure B1 + B2).
 *
 * Loaded LAST (after expand.js + every aux_*.js) so its retab sees the final
 * tab DOM and it can re-mount sibling renderers later (B5). It WRAPS existing
 * hooks — it never rewrites them:
 *
 *   B1 SHELL
 *     · injects #view-agent into .stage (left rail 232 · stream (fills) · right
 *       rail 320 · heartbeat ticker), all CSS in one injected <style>.
 *     · wraps setView() to add an 'agent' view + an "Agent" tab between Hub and
 *       Settings; aliases old keys (console/desktop -> agent, settings -> mind);
 *       migrates localStorage hermes_view.
 *     · on entering Agent, RE-PARENTS the live chat DOM (.chatcol = #msgs +
 *       composer) into #agent-stream and the #chat-side sidebar into #agent-side
 *       (reusing renderChatSide, not rewriting it); RETURNS both on exit, where
 *       the deck keeps them unshown: the chat lives on this page only (owner
 *       decision 2026-11-12; index.html openChat() brings the owner here).
 *
 *   B2 THE ALIVE MOMENT
 *     · Status hero: the live Sigil (idle orbit -> local thinking -> Claude-deep
 *       split + clay warm + bloom -> acting ticks) + a labelled state line
 *       (never a bare spinner) + a DISPLAY-ONLY brain badge (LOCAL tok/s <->
 *       DEEP clay, /api/claude/bridge poll).
 *     · SHOWIN_RENDER dispatcher wrapping the chat-poll status sink
 *       (setAgentState) so tool activity becomes inline cards. Terminal marquee
 *       card built in full; the other 8 taxonomy types are generic collapsed
 *       stubs (full renderers land in B4). Unknown tool types FAIL OPEN to the
 *       original plain status line. The whole dispatch is try/catch-wrapped so a
 *       renderer bug degrades to today's UI, not a dead stream.
 *     · Heartbeat ticker v1: 8s crossfade of live facts from /api/agent/pulse
 *       (fallback: /api/recorder + /api/foryou), a 6px EKG dot blipping on each
 *       recorder event. Visibility + view gated.
 *
 * INVARIANTS honoured here: the approval path (streamJob's d.state==='approval'
 * -> /api/chat/approve) is UNTOUCHED and un-bypassable; NO show-in card contains
 * an approval control; the brain badge + hero are DISPLAY-ONLY; no emoji (all
 * glyphs bespoke two-tone SVG). Discrete motion goes through the motion
 * runtime, HA() (docs/plans/motion-system-2026-10.md 8.10): the ticker
 * crossfade is exit('fadeOut') then enter('fade') with no timer, the sigil
 * tick / EKG blip / show-in head icon are spring()s, a show-in card is
 * enter('rise'), and the Record rail's 3 s poll runs inside quiet(). Either
 * reduce switch or a hidden window gives the end state at once. The orbits,
 * blooms and shimmers stay CSS (frozen by the reduced-motion block below);
 * only the Claude-dialogues sheet still uses the legacy animate()+SPRING.
 */
(function () {
  "use strict";
  if (typeof document === "undefined") return;          // headless import guard
  if (window.__auxAgent) return;                         // idempotent
  window.__auxAgent = true;

  // ---- shims so the render harness can eval this file without index.html ---
  var $ = (typeof window.$ === "function")
    ? window.$ : function (id) { return document.getElementById(id); };
  var esc = (typeof window.esc === "function")
    ? window.esc : function (s) {
      return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
    };
  // index.html's animate() is a top-level `const` (Motion One wrapper) — bare
  // accessible to later classic scripts but NOT a window property. Capture the
  // bare global (real motion); only fall back to a noop when truly absent (e.g.
  // the headless harness). Named agAnimate so it never shadows the bare global.
  var agAnimate;
  try { agAnimate = (typeof animate === "function") ? animate : null; } catch (e) { agAnimate = null; }
  if (!agAnimate && typeof window.animate === "function") agAnimate = window.animate;
  if (!agAnimate) agAnimate = function () { return null; };
  // The motion runtime (docs/plans/motion-system-2026-10.md 8.10). index.html's
  // HA() — aux_anim.js's window.HermesAnim, or its instant ANIM_NULL — is a
  // top-level const like animate(), so it is looked up by name, per call. The
  // runtime reads BOTH reduce switches live (the in-app one too, which the
  // REDUCE below never saw), skips a hidden window and keeps quiet() calls
  // still. A harness with neither gets AG_NULL: every call lands at once.
  var AG_H = { done: true, finished: Promise.resolve(), cancel: function () {}, finish: function () {} };
  var AG_NULL = {
    quiet: function (fn) { return fn(); },
    enter: function () { return AG_H; },
    spring: function () { return AG_H; },
    exit: function (t, p, done) { if (typeof done === "function") done(); return AG_H; }
  };
  function anim() {
    try { if (typeof HA === "function") { var a = HA(); if (a) return a; } } catch (e) {}
    return window.HermesAnim || AG_NULL;
  }
  var SPRING = (typeof window.SPRING === "string")
    ? window.SPRING : "cubic-bezier(.22,1,.36,1)";
  var REDUCE = (typeof window.REDUCE !== "undefined")
    ? window.REDUCE
    : (typeof matchMedia === "function"
      && matchMedia("(prefers-reduced-motion:reduce)").matches);

  function relTime(ts) {
    if (typeof window.relTime === "function") return window.relTime(ts);
    var s = (Date.now() / 1000 - ts);
    if (s < 90) return "just now";
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }

  // ==========================================================================
  // bespoke two-tone glyphs (accent fill @ .14 + currentColor stroke). NO emoji.
  // Rendered inside <svg viewBox="0 0 24 24"> with the shared stroke grammar.
  // ==========================================================================
  var AGLY = {
    // header/tab
    agent: '<circle cx="12" cy="12" r="8.4" fill="var(--tac,var(--iris))" fill-opacity=".14"/>'
      + '<circle cx="12" cy="12" r="8.4"/><circle cx="12" cy="12" r="2.5" fill="currentColor" stroke="none"/>'
      + '<circle cx="20" cy="12" r="1.5" fill="var(--tac,var(--iris))" stroke="none"/>',
    // rail tabs
    record: '<rect x="3" y="4" width="18" height="16" rx="2.5" fill="var(--tac,var(--iris))" fill-opacity=".13"/>'
      + '<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="12" cy="12" r="3"/>',
    screen: '<rect x="3" y="4" width="18" height="13" rx="2" fill="var(--tac,var(--iris))" fill-opacity=".13"/>'
      + '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    pulse: '<path d="M3 12h4l2.5-6 4 13 2.5-7H21" fill="none"/>',
    // tool-card icons (taxonomy)
    shell: '<rect x="3" y="4" width="18" height="16" rx="2.5" fill="var(--tac,var(--iris))" fill-opacity=".13"/>'
      + '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 9l3 3-3 3M13 16h4"/>',
    web: '<circle cx="12" cy="12" r="8.4" fill="var(--tac,var(--quick))" fill-opacity=".13"/>'
      + '<circle cx="12" cy="12" r="8.4"/><path d="M3.6 12h16.8M12 3.6c2.6 2.4 2.6 14.4 0 16.8M12 3.6c-2.6 2.4-2.6 14.4 0 16.8"/>',
    file: '<path d="M6 3h7l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" fill="var(--tac,var(--ok))" fill-opacity=".13"/>'
      + '<path d="M6 3h7l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M13 3v5h5"/>',
    computer: '<rect x="3" y="4" width="18" height="12" rx="2" fill="var(--tac,var(--warn))" fill-opacity=".13"/>'
      + '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/><circle cx="16" cy="10" r="1.4" fill="currentColor" stroke="none"/>',
    skill: '<path d="M12 3l7.4 4.3v8.6L12 20.2 4.6 15.9V7.3z" fill="var(--tac,var(--iris-2))" fill-opacity=".14"/>'
      + '<path d="M12 3l7.4 4.3v8.6L12 20.2 4.6 15.9V7.3z"/><path d="M9 12l2 2 4-4"/>',
    memory: '<path d="M12 3a4 4 0 0 0-4 4 4 4 0 0 0-1 7.9V17a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2v-2.1A4 4 0 0 0 16 7a4 4 0 0 0-4-4z" fill="var(--tac,#E0729C)" fill-opacity=".14"/>'
      + '<path d="M12 3a4 4 0 0 0-4 4 4 4 0 0 0-1 7.9V17a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2v-2.1A4 4 0 0 0 16 7a4 4 0 0 0-4-4z"/>',
    delegate: '<circle cx="8" cy="9" r="3.2" fill="var(--tac,#5FA8B8)" fill-opacity=".14"/><circle cx="8" cy="9" r="3.2"/>'
      + '<circle cx="16" cy="14" r="2.4"/><path d="M4 20c0-2.4 1.9-4 4-4"/>',
    bridge: '<path d="M4 15a5 5 0 0 1 5-5M20 15a5 5 0 0 0-5-5" fill="none"/>'
      + '<circle cx="12" cy="10" r="2" fill="var(--tac,var(--claude))" fill-opacity=".2"/><circle cx="12" cy="10" r="2"/><path d="M3 18h18"/>',
    tool: '<circle cx="12" cy="12" r="8.4" fill="var(--tac,var(--iris))" fill-opacity=".12"/><circle cx="12" cy="12" r="8.4"/><path d="M12 8v4l3 2"/>',
    check: '<path d="M20 6L9 17l-5-5"/>',
    warn: '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" fill="var(--bad)" fill-opacity=".14"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    chevron: '<polyline points="9 6 15 12 9 18"/>'
  };
  function svg(name, cls) {
    return '<svg class="ic ' + (cls || "") + '" viewBox="0 0 24 24" fill="none" '
      + 'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
      + 'stroke-linejoin="round">' + (AGLY[name] || AGLY.tool) + "</svg>";
  }

  // ==========================================================================
  // one-time CSS — everything the Agent page needs, isolated under #view-agent
  // ==========================================================================
  function injectCss() {
    if ($("agent-css")) return;
    var s = document.createElement("style");
    s.id = "agent-css";
    s.textContent = [
      /* new clay tokens for the second brain (Claude-deep) */
      ":root{--claude:#D97757;--claude-2:#E8926F}",
      '@media (prefers-color-scheme:dark){:root{--claude:#E8926F;--claude-2:#F0A98C}}',
      ':root[data-theme="dark"]{--claude:#E8926F;--claude-2:#F0A98C}',
      ':root[data-theme="light"]{--claude:#D97757;--claude-2:#E8926F}',

      /* the view fills the stage as a 3-column cockpit */
      "#view-agent{display:flex;gap:12px;min-height:0;flex:1}",
      "#view-agent[hidden]{display:none!important}",

      "#agent-side{width:232px;flex:none;min-height:0;display:flex;flex-direction:column}",
      "@media (max-width:1100px){#agent-side{display:none}}",
      /* reuse the moved #chat-side verbatim, just show it here */
      "#agent-side .chatside{display:flex!important;flex-direction:column;flex:1;min-height:0;overflow:hidden;margin:0}",
      /* The conversation list owns the leftover height and scrolls. Before,
         Tools (content-sized, up to 30%) and Local AI took their full height
         first and the list got whatever was left: at the owner's 122% zoom in
         WKWebView that was ~309px, its clip edge sliced a row through the
         middle and the TOOLS label sat flush under the cut, so the row read as
         tucked behind the heading. Now the list keeps a floor of ~5 rows (the
         sections below give way and scroll first), the sections start on a
         hairline, and the clip edge fades out instead of cutting a row. The
         floor is min(168px, 26vh): in a window too short to hold 168px of list
         plus the Local AI block and the status foot, a fixed floor pushed the
         foot out of the (overflow:hidden) sidebar. */
      "#agent-side #cs-convos{flex:1 1 0;min-height:min(168px,26vh);padding-bottom:20px;"
        + "-webkit-mask-image:linear-gradient(to bottom,#000 calc(100% - 20px),transparent);"
        + "mask-image:linear-gradient(to bottom,#000 calc(100% - 20px),transparent)}",
      "#agent-side #cs-tools{flex:0 1 auto;min-height:0}",
      /* Tools / Local AI: collapsible section headers (real buttons) */
      ".chatside .cs-sechd{display:flex;align-items:center;gap:6px;width:100%;min-height:40px;flex:none;"
        + "margin:0;padding:8px 14px 6px;border:0;border-top:1px solid var(--hairline);border-radius:0;"
        + "background:none;color:var(--muted);font:inherit;font-size:11px;font-weight:600;"
        + "text-transform:uppercase;letter-spacing:.07em;text-align:left;cursor:pointer;"
        + "transition-property:color;transition-duration:var(--d-micro,150ms);transition-timing-function:ease-out}",
      "#chat-side .cs-sechd:hover{color:var(--ink);background:none;border-color:var(--hairline);transform:none}",
      "#chat-side .cs-sechd:active{scale:none}",
      "#chat-side .cs-sechd:focus-visible{outline-offset:-2px}",
      ".chatside .cs-sechd .chev{width:11px;height:11px;flex:none;stroke-width:2.2;"
        + "transition-property:transform;transition-duration:var(--d-micro,150ms);transition-timing-function:var(--press,ease-out)}",
      '.chatside .cs-sechd[aria-expanded="true"] .chev{transform:rotate(90deg)}',
      "#chat-side #cs-tools[hidden],#chat-side #cs-ai[hidden]{display:none}",
      "@media (prefers-reduced-motion:reduce){.chatside .cs-sechd,.chatside .cs-sechd .chev{transition:none}}",
      ":root[data-reduce] .chatside :is(.cs-sechd,.cs-sechd .chev){transition:none}",

      "#agent-center{flex:1;min-width:0;display:flex;flex-direction:column;min-height:0}",
      "#agent-stream{flex:1;min-height:0;display:flex;justify-content:center;overflow:hidden}",
      /* the chat card spans the full width of #agent-center, so its edges are
         the status bar's (#agent-hero) at every width — it used to stop at
         860px, centred, visibly narrower than the bar above it. The reading
         measure is kept on the bubbles instead (the full-chat mode's 76%
         rule, plus a ceiling for very wide windows). */
      "#agent-stream .chatcol{flex:1;max-width:none;width:100%;min-height:0;display:flex;opacity:1}",
      "#agent-stream .bubble{max-width:min(76%,52rem)}",
      "#agent-stream .chatcol .chat{flex:1;height:100%}",

      "#agent-rail{width:320px;flex:none;min-height:0;display:flex;flex-direction:column}",
      "@media (max-width:1280px){#agent-rail{display:none}}",

      /* ---------- status hero ---------- */
      "#agent-hero{display:flex;align-items:center;gap:13px;padding:9px 14px;margin-bottom:8px;"
        + "border-radius:var(--radius-sm);min-height:56px;box-sizing:border-box}",
      "#agent-hero .sig{width:36px;height:36px;flex:none;position:relative}",
      "#agent-hero .sig svg{width:36px;height:36px;overflow:visible}",
      "#agent-hero .state{display:flex;flex-direction:column;gap:1px;min-width:0;flex:1}",
      "#agent-hero .state .s1{font-size:13px;font-weight:640;color:var(--ink);letter-spacing:-.01em}",
      "#agent-hero .state .s2{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",

      /* the Sigil */
      ".sig-ring{transform-box:fill-box;transform-origin:50% 50%}",
      ".sig-orbit{transform-origin:18px 18px;animation:agOrbit 18s linear infinite}",
      ".sig-orbit.two{animation:agOrbit 6s linear infinite}",
      ".sig-orbit.rev{animation:agOrbitRev 5s linear infinite}",
      "@keyframes agOrbit{to{transform:rotate(360deg)}}",
      "@keyframes agOrbitRev{to{transform:rotate(-360deg)}}",
      ".sig-bloom{opacity:0;transform-origin:18px 18px}",
      '#agent-hero[data-sig="local"] .sig-orbit{animation-duration:3s}',
      '#agent-hero[data-sig="local"] .sig-core{filter:drop-shadow(0 0 5px var(--iris))}',
      '#agent-hero[data-sig="deep"] .sig-orbit{animation-duration:2.6s}',
      '#agent-hero[data-sig="deep"] .sig-ring circle{stroke:var(--claude)}',
      '#agent-hero[data-sig="deep"] .sig-core{fill:var(--claude);stroke:var(--claude);filter:drop-shadow(0 0 6px var(--claude))}',
      '#agent-hero[data-sig="deep"] .sig-orbit .p{fill:var(--claude)}',
      '#agent-hero[data-sig="deep"] .sig-bloom{opacity:1;animation:agBloom 2.4s ease-in-out infinite}',
      '#agent-hero[data-sig="deep"] .sig-orbit2{display:block}',
      '#agent-hero[data-sig="await"] .sig-orbit{animation-play-state:paused}',
      '#agent-hero[data-sig="await"] .sig-ring circle{stroke:var(--warn)}',
      '#agent-hero[data-sig="acting"] .sig-orbit{animation-play-state:paused}',
      ".sig-orbit2{display:none}",
      "@keyframes agBloom{0%,100%{opacity:.15;transform:scale(.9)}50%{opacity:.4;transform:scale(1.15)}}",

      /* ---------- brain badge (segmented, DISPLAY-ONLY) ---------- */
      ".brainbadge{display:inline-flex;align-items:center;gap:0;flex:none;position:relative;"
        + "padding:3px;border-radius:99px;background:var(--glass-2);border:1px solid var(--hairline);"
        + "font-size:11px;font-weight:600;cursor:default;user-select:none}",
      ".brainbadge .thumb{position:absolute;top:3px;bottom:3px;left:3px;width:calc(50% - 3px);"
        + "border-radius:99px;background:var(--glass);box-shadow:inset 0 1px 0 var(--specular),0 2px 8px -4px var(--cast);"
        + "transition:transform .1s linear}",
      ".brainbadge.deep .thumb{transform:translateX(100%)}",
      ".brainbadge .half{position:relative;z-index:1;display:inline-flex;align-items:center;gap:5px;"
        + "padding:4px 11px;color:var(--muted);white-space:nowrap}",
      ".brainbadge.local .half.l,.brainbadge.deep .half.d{color:var(--ink)}",
      ".brainbadge .bd{width:6px;height:6px;border-radius:50%;background:var(--faint)}",
      ".brainbadge .half.l .bd{background:var(--quick)}",
      ".brainbadge.local .half.l .bd{box-shadow:0 0 0 3px color-mix(in srgb,var(--quick) 22%,transparent)}",
      ".brainbadge .half.d .bd{background:var(--claude)}",
      ".brainbadge.deep .half.d .bd{box-shadow:0 0 0 3px color-mix(in srgb,var(--claude) 26%,transparent);animation:agBreath 2s ease-in-out infinite alternate}",
      "@keyframes agBreath{from{opacity:.7}to{opacity:1}}",

      /* ---------- show-in cards ---------- */
      "#agent-stream .msgs{position:relative}",
      ".showin-group{display:flex;flex-direction:column;gap:6px;margin:2px 0}",
      ".showin-group .grp-head{display:none;font-size:11px;color:var(--muted);font-weight:600;padding:1px 2px}",
      ".showin-group.multi .grp-head{display:flex;align-items:center;gap:6px}",
      ".showin{position:relative;border-radius:var(--radius-sm);border:1px solid var(--hairline);"
        + "background:var(--glass-2);overflow:hidden;--tac:var(--iris)}",
      ".showin::before{content:'';position:absolute;left:0;top:0;bottom:0;width:2px;background:var(--tac);opacity:.85}",
      '.showin[data-tool="terminal"]{--tac:var(--iris)}',
      '.showin[data-tool="web_search"]{--tac:var(--quick)}',
      '.showin[data-tool="file"]{--tac:var(--ok)}',
      '.showin[data-tool="computer_use"]{--tac:var(--warn)}',
      '.showin[data-tool="skill"]{--tac:var(--iris-2)}',
      '.showin[data-tool="memory"]{--tac:#E0729C}',
      '.showin[data-tool="delegate"]{--tac:#5FA8B8}',
      '.showin[data-tool="claude-bridge"]{--tac:var(--claude)}',
      ".showin.err::before{background:var(--bad)}",
      /* running shimmer on the top hairline */
      ".showin.running::after{content:'';position:absolute;top:0;left:0;right:0;height:2px;"
        + "background:linear-gradient(90deg,transparent,var(--tac),transparent);background-size:200% 100%;"
        + "animation:agShim 1.4s linear infinite;opacity:.9}",
      ".showin-head{display:flex;align-items:center;gap:9px;padding:8px 11px;cursor:pointer;min-height:36px;box-sizing:border-box}",
      ".showin-head .ic{width:15px;height:15px;flex:none;color:var(--tac)}",
      ".showin-head .gist{font-size:12.5px;font-weight:560;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}",
      ".showin-head .chip{font-size:10.5px;font-weight:640;padding:2px 7px;border-radius:99px;flex:none;"
        + "background:color-mix(in srgb,var(--tac) 15%,transparent);color:var(--tac)}",
      ".showin-head .chip.ok{background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok)}",
      ".showin-head .chip.bad{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)}",
      ".showin-head .dur{font-size:10.5px;color:var(--faint);font-variant-numeric:tabular-nums;flex:none}",
      ".showin-head .chev{width:13px;height:13px;color:var(--faint);flex:none;transition:transform .18s}",
      ".showin.open .showin-head .chev{transform:rotate(90deg)}",
      ".showin-body{display:none;border-top:1px solid var(--hairline)}",
      ".showin.open .showin-body{display:block}",

      /* terminal marquee — dark in BOTH themes */
      '.showin[data-tool="terminal"]{background:#0B0E16;border-color:rgba(150,160,220,.18)}',
      '.showin[data-tool="terminal"] .showin-head .gist{color:#C9D2F0;'
        + "font-family:ui-monospace,'SF Mono',Menlo,monospace;font-weight:500}",
      '.showin[data-tool="terminal"] .showin-head .dur{color:#7f88ad}',
      ".term-body{padding:9px 12px 10px;font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:11.5px}",
      ".term-cmd{color:#8ea0e6;margin-bottom:5px;white-space:pre-wrap;word-break:break-all}",
      ".term-out{color:#C9D2F0;max-height:220px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;margin:0;line-height:1.5}",
      ".term-out .e{color:var(--bad)}",
      ".term-cur{display:inline-block;width:7px;height:13px;vertical-align:-2px;background:#C9D2F0;animation:agBlink 1s step-end infinite}",
      ".term-more{color:#8ea0e6;font-size:11px;cursor:pointer;padding:6px 12px;border-top:1px solid rgba(150,160,220,.14)}",
      "@keyframes agBlink{50%{opacity:0}}",
      "@keyframes agShim{0%{background-position:200% 0}100%{background-position:-200% 0}}",

      /* stub card body */
      ".showin-stub-body{padding:9px 12px;font-size:12px;color:var(--muted)}",

      /* ---------- right rail (stubs, filled in B5) ---------- */
      "#agent-rail .railtabs{display:inline-flex;padding:3px;border-radius:11px;gap:2px;"
        + "background:var(--glass-2);border:1px solid var(--hairline);margin-bottom:8px}",
      "#agent-rail .railtabs b{font-size:11.5px;font-weight:560;color:var(--muted);padding:5px 12px;border-radius:8px;"
        + "cursor:pointer;display:inline-flex;align-items:center;gap:5px;position:relative}",
      "#agent-rail .railtabs b .ic{width:13px;height:13px}",
      "#agent-rail .railtabs b.on{color:var(--ink);background:var(--glass);box-shadow:inset 0 1px 0 var(--specular)}",
      "#agent-rail .railtabs b .udot{position:absolute;top:2px;right:4px;width:6px;height:6px;border-radius:50%;background:var(--iris);display:none}",
      "#agent-rail .railpane{flex:1;min-height:0;overflow-y:auto}",
      "#agent-rail .railpane[hidden]{display:none}",
      "#agent-rail .railstub{font-size:12px;color:var(--muted);padding:16px 4px;text-align:center;line-height:1.5}",
      /* Screen rail compose card ("Give the agent a Mac task") is live: it was
         hidden and unwired here while #view-desktop, the only place it worked,
         became unreachable (B26). aux_desktop's wireCompose drives it. */
      /* Tall enough for the whole placeholder: at the rail's width it is four
         lines of 13px x 1.5 plus 10px padding and the border, and 54px cut it
         off mid-line (visual audit V07). */
      "#agent-rail .desk-compose textarea{min-height:100px}",
      /* V07: in the 296px rail "On-screen activity" and its caption do not fit
         one line; the title broke in two beside a cramped caption. The caption
         takes its own line, indented to the title text (18px icon + 9px gap). */
      "#agent-rail #desk-tl-card .desk-h{flex-wrap:wrap;row-gap:1px}",
      "#agent-rail #desk-tl-card .desk-h .desk-note{flex-basis:100%;margin-left:27px}",
      /* V07: the empty states are sized for the full-width Desktop view; in the
         rail they pushed the compose card and "Send to agent" below the fold. */
      "#agent-rail .desk-preview{min-height:120px}",
      "#agent-rail .desk-empty{padding:18px 12px}",
      "#agent-rail #recorder-card{border:none;background:transparent;box-shadow:none}",
      // the card is .glass, so inside the (already glass) rail it painted a
      // second blur and its own sheen: a lighter grey slab next to the dark
      // panels. In the rail it is the rail's own surface, nothing on top.
      "#agent-rail #recorder-card{-webkit-backdrop-filter:none;backdrop-filter:none}",
      "#agent-rail #recorder-card::before,#agent-rail #recorder-card::after{display:none}",
      // The header and the body share ONE 4px inset. They were 2px and 14px,
      // which put the header icon 12px left of the filter row (outdented to
      // the rail edge) and spent 24px of a 296px rail on empty padding. The
      // row layout itself (two lines when the card is narrow) is
      // aux_recorder's own @container rule, not a rail override any more.
      "#agent-rail #recorder-card>h2{font-size:11.5px;letter-spacing:.06em;padding:2px 4px 8px}",
      // Visual audit V07: the count ended 4px from the card's edge, inside its
      // rounded top-right corner, which clipped "0 undone". 8px more on its
      // right keeps it off the corner; with the room gone it wraps under the
      // title, as aux_recorder's header rule intends for a narrow card.
      "#agent-rail #recorder-card .rec-count{margin-right:8px}",
      "#agent-rail #recorder-card>.body{padding:8px 4px 10px}",
      "#agent-rail .desk-card{margin-bottom:8px}",
      /* red LIVE dot on the Screen tab while computer_use runs */
      '#agent-rail .railtabs b[data-rail="screen"].live .udot{display:block;background:var(--bad);'
        + "box-shadow:0 0 0 3px color-mix(in srgb,var(--bad) 26%,transparent);animation:lpulse 1.1s infinite}",

      /* B5-hide the now-redundant top tabs (DOM kept; views reachable via setView).
         Net top bar: Hub · Agent. Settings/Console/Desktop fold into rails/prefs. */
      "#tab-mind,#tab-console,#tab-desktop{display:none!important}",

      /* ---------- Deep-thinking affordance + Claude-dialogue viewer ---------- */
      ".deepbtn{display:none;align-items:center;gap:5px;flex:none;font-size:11px;font-weight:600;"
        + "padding:4px 10px;border-radius:99px;cursor:pointer;color:color-mix(in srgb,var(--claude) 65%,var(--ink));"
        + "background:color-mix(in srgb,var(--claude) 13%,transparent);border:1px solid color-mix(in srgb,var(--claude) 30%,transparent)}",
      ".deepbtn.show{display:inline-flex}",
      ".deepbtn .ic{width:13px;height:13px}",
      "#ag-deep{position:fixed;inset:0;z-index:60;display:none;align-items:center;justify-content:center;"
        + "background:color-mix(in srgb,var(--ground) 55%,transparent);backdrop-filter:blur(3px)}",
      "#ag-deep.open{display:flex}",
      "#ag-deep .dsheet{width:min(680px,92vw);max-height:82vh;display:flex;flex-direction:column;"
        + "border-radius:var(--radius);padding:16px 18px;overflow:hidden}",
      "#ag-deep .dhead{display:flex;align-items:center;gap:9px;font-size:14px;font-weight:640;color:var(--ink);margin-bottom:4px}",
      "#ag-deep .dhead .ic{width:17px;height:17px;color:var(--claude)}",
      "#ag-deep .dsub{font-size:11.5px;color:var(--muted);margin-bottom:12px}",
      "#ag-deep .dclose{margin-left:auto;cursor:pointer;color:var(--muted);background:none;border:none;padding:4px}",
      "#ag-deep .dlist{overflow-y:auto;min-height:0;display:flex;flex-direction:column;gap:8px}",
      ".dcall{border:1px solid var(--hairline);border-radius:var(--radius-sm);background:var(--glass-2);overflow:hidden}",
      // a <button> (R2-39): undo the global button chrome, keep the row look;
      // the focus ring sits inside because .dcall clips (overflow:hidden)
      ".dcall .dc-head{display:flex;align-items:center;gap:8px;padding:9px 12px;cursor:pointer;width:100%;"
        + "border:0;border-radius:0;background:none;color:inherit;font:inherit;text-align:left}",
      ".dcall .dc-head:hover{transform:none;color:inherit}",
      ".dcall .dc-head:focus-visible{outline-offset:-2px}",
      "#ag-deep .dcall .dc-head:active{scale:none}",
      ".dcall .dc-task{font-size:12.5px;font-weight:560;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}",
      ".dcall .dc-model{font-size:10px;font-weight:640;padding:2px 7px;border-radius:99px;flex:none;"
        + "background:color-mix(in srgb,var(--claude) 15%,transparent);color:var(--claude);text-transform:capitalize}",
      ".dcall .dc-meta{font-size:10.5px;color:var(--faint);flex:none;font-variant-numeric:tabular-nums}",
      ".dcall .dc-body{display:none;border-top:1px solid var(--hairline);padding:10px 12px;font-size:12.5px;line-height:1.55}",
      ".dcall.open .dc-body{display:block}",
      ".dcall .dc-k{font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--faint);margin:2px 0 4px}",
      ".dcall .dc-q{color:var(--muted);white-space:pre-wrap;margin-bottom:10px}",
      ".dcall .dc-a{color:var(--ink);white-space:pre-wrap}",
      ".dcall.err .dc-model{background:color-mix(in srgb,var(--bad) 15%,transparent);color:var(--bad)}",

      /* on-demand escalate button under a bot reply + the deep-answer card */
      ".esc-row{margin-top:6px}",
      ".esc-row[hidden]{display:none}",   // master switch off — see escSyncButtons
      ".esc-btn{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;cursor:pointer;"
        + "padding:4px 10px;border-radius:99px;color:var(--claude);background:color-mix(in srgb,var(--claude) 12%,transparent);"
        + "border:1px solid color-mix(in srgb,var(--claude) 28%,transparent)}",
      ".esc-btn[disabled]{opacity:.55;cursor:default}",
      ".esc-btn .ic{width:12px;height:12px}",
      ".deepcard{margin-top:8px;border:1px solid color-mix(in srgb,var(--claude) 30%,transparent);"
        + "border-radius:var(--radius-sm);background:color-mix(in srgb,var(--claude) 7%,transparent);overflow:hidden}",
      ".deepcard .dk-head{display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--hairline)}",
      ".deepcard .dk-head .ic{width:14px;height:14px;color:var(--claude)}",
      ".deepcard .dk-lab{font-size:11.5px;font-weight:640;color:var(--claude)}",
      ".deepcard .dk-meta{margin-left:auto;font-size:10.5px;color:var(--faint);font-variant-numeric:tabular-nums}",
      ".deepcard .dk-body{padding:11px 13px;font-size:13.5px;line-height:1.6;color:var(--ink)}",
      ".esc-btn.suggest{border-color:color-mix(in srgb,var(--claude) 55%,transparent);color:var(--claude);box-shadow:0 0 0 3px color-mix(in srgb,var(--claude) 14%,transparent)}",
      ".deepcard.err .dk-lab{color:var(--bad)}",
      ".deepcard.err .dk-head .ic{color:var(--bad)}",

      /* ---------- heartbeat ticker ---------- */
      /* the ticker is the banner's third line (it used to float under the
         composer, outside any card) */
      "#agent-ticker{display:flex;align-items:center;gap:7px;min-height:18px;padding:0;margin-top:2px;"
        + "font-size:12px;color:var(--muted);cursor:pointer;overflow:hidden;min-width:0}",
      "#agent-ticker:hover .tk-txt{color:var(--ink)}",
      "#agent-ticker .ekg{width:6px;height:6px;border-radius:50%;background:var(--ok);flex:none;"
        + "box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 20%,transparent)}",
      /* the model asleep or paused: the dot stops claiming "live" */
      '#agent-ticker[data-model="asleep"] .ekg{background:var(--muted);box-shadow:0 0 0 3px color-mix(in srgb,var(--muted) 20%,transparent)}',
      '#agent-ticker[data-model="paused"] .ekg{background:var(--bad);box-shadow:0 0 0 3px color-mix(in srgb,var(--bad) 20%,transparent)}',
      /* Local AI: with no figure to show, one line says why instead of two
         bare dashes (aiStateNote) */
      "#chat-side .cs-ai-row.ag-nodata{display:none}",
      /* the sidebar says the model's state once: the Local AI line above. The
         footer chip is a copy of the header pill's words, so the Agent view
         leaves it to the pill (it still shows in the chat's own sidebar) */
      "#agent-side .cs-foot{display:none}",
      "#chat-side .cs-ai-note{font-size:12px;line-height:1.45;color:var(--muted);padding:2.5px 0 4px;text-wrap:pretty}",
      /* no opacity transition: the crossfade is HA().exit/enter, and a CSS
         transition would chase every inline opacity the runtime writes */
      "#agent-ticker .tk-txt{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",

      /* reduced motion: freeze orbits/shimmers/blips — fades only */
      "@media (prefers-reduced-motion:reduce){"
        + ".sig-orbit,.sig-orbit.two,.sig-orbit.rev,.sig-bloom,"
        + "#agent-hero[data-sig] .sig-orbit,#agent-hero[data-sig] .sig-bloom,"
        + ".showin.running::after,.term-cur,.brainbadge.deep .half.d .bd{animation:none!important}"
        + ".sig-bloom{opacity:0!important}}"
    ].join("\n");
    (document.head || document.documentElement).appendChild(s);
  }

  // ==========================================================================
  // B1 — the shell markup
  // ==========================================================================
  function buildShell() {
    if ($("view-agent")) return;
    var stage = document.querySelector(".stage");
    if (!stage) return;
    injectCss();

    var v = document.createElement("div");
    v.id = "view-agent";
    v.setAttribute("role", "tabpanel");
    v.hidden = true;
    v.innerHTML =
      '<aside id="agent-side" aria-label="Sessions, tools and Local AI"></aside>'
      + '<div id="agent-center">'
      + '  <div id="agent-hero" class="glass" data-sig="idle">'
      + '    <div class="sig">' + sigilSvg() + '</div>'
      + '    <div class="state"><div class="s1" id="ag-s1">Watching</div>'
      + '      <div class="s2" id="ag-s2">Ask, or just watch — your assistant is on watch.</div>'
      + '      <div id="agent-ticker" title="Open the Pulse feed"><span class="ekg" id="ag-ekg"></span>'
      + '        <span class="tk-txt" id="ag-tick">On watch — nothing needs you right now</span></div></div>'
      + '    <button class="deepbtn" id="ag-deepbtn" title="See the full dialogues the agent had with Claude">'
      + svg("bridge") + '<span id="ag-deepbtn-t">Claude dialogues</span></button>'
      + '    ' + brainBadgeHtml()
      + '  </div>'
      + '  <div id="agent-stream"></div>'
      + '</div>'
      + '<div id="agent-rail" class="glass" style="padding:11px">'
      + '  <div class="railtabs" role="tablist" aria-label="Agent rail">'
      + '    <b data-rail="record" class="on" role="tab" id="ag-railtab-record" aria-controls="rail-record" aria-selected="true" tabindex="0">' + svg("record") + 'Record<span class="udot"></span></b>'
      + '    <b data-rail="screen" role="tab" id="ag-railtab-screen" aria-controls="rail-screen" aria-selected="false" tabindex="-1">' + svg("screen") + 'Screen<span class="udot"></span></b>'
      + '    <b data-rail="pulse" role="tab" id="ag-railtab-pulse" aria-controls="rail-pulse" aria-selected="false" tabindex="-1">' + svg("pulse") + 'Pulse<span class="udot"></span></b>'
      + '  </div>'
      + '  <div class="railpane" data-rail="record" id="rail-record" role="tabpanel" aria-labelledby="ag-railtab-record"><div class="railstub">Loading the Flight Recorder…</div></div>'
      + '  <div class="railpane" data-rail="screen" id="rail-screen" role="tabpanel" aria-labelledby="ag-railtab-screen" hidden><div class="railstub">Loading the Screen panel…</div></div>'
      + '  <div class="railpane" data-rail="pulse" id="rail-pulse" role="tabpanel" aria-labelledby="ag-railtab-pulse" hidden><div class="railstub" id="ag-pulse-stub">The autonomous feed — what the agent did while you were away.</div></div>'
      + '</div>';
    stage.appendChild(v);

    // Claude-dialogue viewer overlay (display-only; read-only transcript list)
    var dm = document.createElement("div");
    dm.id = "ag-deep";
    dm.innerHTML =
      '<section class="dsheet glass" role="dialog" aria-modal="true" aria-label="Claude dialogues">'
      + '<div class="dhead">' + svg("bridge") + 'Claude dialogues'
      + '<button class="dclose" id="ag-deep-x" aria-label="Close">' + svg("chevron") + '</button></div>'
      + '<div class="dsub">Every time the agent escalated to Claude — the exact question it asked and Claude\'s full response. Display only.</div>'
      + '<div class="dlist" id="ag-deep-list"><div class="railstub">No deep escalations yet.</div></div>'
      + '</section>';
    (document.body || document.documentElement).appendChild(dm);
    dm.addEventListener("click", function (e) { if (e.target === dm) closeDeep(); });
    var dx = $("ag-deep-x"); if (dx) dx.addEventListener("click", closeDeep);
    var db = $("ag-deepbtn"); if (db) db.addEventListener("click", openDeep);
    // E89 + S2 seam: index.html's OVL registry now lists this sheet too (its
    // isOpen reads the .open class), so its document handler, which runs
    // first, takes Esc/Tab for it and adds the modal focus pull-back; this
    // handler stands down on a key already taken (defaultPrevented) and is
    // kept for a page without the registry. A context menu opened over the
    // sheet still gets the first Esc.
    if (!document.__agDeepKeys) {
      document.__agDeepKeys = true;
      document.addEventListener("keydown", onDeepKey);
    }

    // rail tab switching (local, no view change)
    var tabs = v.querySelectorAll(".railtabs b");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener("click", function () {
        openRail(this.getAttribute("data-rail"));
      });
    }
    wireRailKeys(v.querySelector(".railtabs"));
    $("agent-ticker").addEventListener("click", function () {
      var t = v.querySelector('.railtabs b[data-rail="pulse"]');
      if (t) t.click();
    });
  }

  function sigilSvg() {
    // conic-ish ring + orbiting particle(s) + bloom + core. Two-tone, bespoke.
    return '<svg viewBox="0 0 36 36" fill="none" stroke="currentColor" stroke-width="1.6">'
      + '<circle class="sig-bloom" cx="18" cy="18" r="15" fill="var(--claude)" stroke="none"/>'
      + '<g class="sig-ring" stroke="var(--iris)" opacity=".55"><circle cx="18" cy="18" r="12"/></g>'
      + '<circle class="sig-core" cx="18" cy="18" r="3.4" fill="var(--iris)" stroke="var(--iris)"/>'
      + '<g class="sig-orbit"><circle class="p" cx="30" cy="18" r="2.1" fill="var(--iris)" stroke="none"/></g>'
      + '<g class="sig-orbit2 sig-orbit rev"><circle class="p" cx="6" cy="18" r="1.7" fill="var(--claude)" stroke="none"/></g>'
      + '</svg>';
  }

  function brainBadgeHtml() {
    return '<div class="brainbadge local" id="ag-brain" role="img" '
      + 'aria-label="Which brain is engaged (display only)" title="Local model — display only">'
      + '<span class="thumb"></span>'
      + '<span class="half l"><span class="bd"></span><span id="ag-brain-l">Local</span></span>'
      + '<span class="half d"><span class="bd"></span><span id="ag-brain-d">Claude</span></span>'
      + '</div>';
  }

  // ==========================================================================
  // B1 — retab: add the Agent tab between Hub and Settings
  // ==========================================================================
  function addTab() {
    if ($("tab-agent")) return;
    var seg = document.querySelector(".seg");
    if (!seg) return;
    var b = document.createElement("b");
    b.id = "tab-agent";
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", "false");
    b.innerHTML = svg("agent") + "Agent";
    var mind = $("tab-mind");           // insert BETWEEN Hub and Settings(=mind)
    if (mind && mind.parentNode === seg) seg.insertBefore(b, mind);
    else seg.appendChild(b);
    b.onclick = function () { if (typeof window.setView === "function") window.setView("agent"); };
    wireSegKeys(seg);
  }

  // --------------------------------------------------------------------------
  // keyboard for the header switcher (C93). The tabs are <b role=tab>, which
  // nothing could focus, so Hub/Agent was mouse-only. Now it is a tablist with
  // a roving tabindex: the active tab is the one Tab stop, ArrowLeft/Right/
  // Home/End move between the VISIBLE tabs (Settings and Console are hidden by
  // CSS), Enter/Space switch. Manual activation: switching to Agent re-parents
  // the chat, so it does not happen on every arrow press.
  // --------------------------------------------------------------------------
  function segTabs() {
    var seg = document.querySelector("header .seg") || document.querySelector(".seg");
    if (!seg) return [];
    return Array.prototype.filter.call(seg.querySelectorAll('[role="tab"]'), function (t) {
      return t.offsetParent !== null;               // display:none tabs have none
    });
  }
  function syncSegTabs() {
    var seg = document.querySelector("header .seg") || document.querySelector(".seg");
    if (!seg) return;
    var all = seg.querySelectorAll('[role="tab"]'), vis = segTabs(), on = null, i;
    for (i = 0; i < vis.length; i++) if (vis[i].classList.contains("on")) on = vis[i];
    on = on || vis[0] || null;
    for (i = 0; i < all.length; i++) all[i].tabIndex = (all[i] === on) ? 0 : -1;
  }
  function onSegKey(e) {
    var t = e.target;
    if (!t || !t.getAttribute || t.getAttribute("role") !== "tab") return;
    var tabs = segTabs(), i = tabs.indexOf(t), n = -1, k = e.key;
    if (k === "Enter" || k === " " || k === "Spacebar") {
      e.preventDefault();
      if (e.repeat) return;                     // a held key switches once
      try { t.click(); } catch (x) {}
      syncSegTabs();
      try { t.focus(); } catch (x) {}
      return;
    }
    if (i < 0) return;
    if (k === "ArrowRight" || k === "ArrowDown") n = (i + 1) % tabs.length;
    else if (k === "ArrowLeft" || k === "ArrowUp") n = (i - 1 + tabs.length) % tabs.length;
    else if (k === "Home") n = 0;
    else if (k === "End") n = tabs.length - 1;
    if (n < 0) return;
    e.preventDefault();
    for (var j = 0; j < tabs.length; j++) tabs[j].tabIndex = (j === n) ? 0 : -1;
    try { tabs[n].focus(); } catch (x) {}
  }
  function wireSegKeys(seg) {
    if (!seg || seg.__segKeys) return;
    seg.__segKeys = true;
    seg.addEventListener("keydown", onSegKey);
    // focus leaving the switcher puts the Tab stop back on the active tab
    seg.addEventListener("focusout", function (e) {
      var to = e && e.relatedTarget;
      if (!to || !seg.contains(to)) syncSegTabs();
    });
    syncSegTabs();
  }

  // ==========================================================================
  // B1 — re-parent the live chat DOM in/out of the Agent page
  // ==========================================================================
  var _orig = { chatcol: null, chatcolNext: null, chatside: null, chatsideNext: null, deck: null };
  var _parked = false;

  function snapshotOrigin() {
    if (_orig.deck) return;
    _orig.deck = document.querySelector(".deck");
    var cc = document.querySelector(".chatcol");
    var cs = $("chat-side");
    if (cc) { _orig.chatcol = cc; _orig.chatcolNext = cc.nextSibling; }
    if (cs) { _orig.chatside = cs; _orig.chatsideNext = cs.nextSibling; }
  }

  function enterAgent() {
    snapshotOrigin();
    if (_parked) return;
    var stream = $("agent-stream"), side = $("agent-side");
    if (_orig.chatcol && stream) stream.appendChild(_orig.chatcol);
    if (_orig.chatside && side) side.appendChild(_orig.chatside);
    document.documentElement.setAttribute("data-agentview", "1");
    try { upgradeSideSections(); } catch (e) {}      // the Agent page's section defaults
    _parked = true;
    try { if (typeof window.renderChatSide === "function") window.renderChatSide(); } catch (e) {}
    try { mountRecordRail(); } catch (e) {}          // default rail = the flight recorder
    startAgentPolls();
  }

  function exitAgent() {
    if (!_parked) return;
    // return the chat DOM to the deck at its exact original position (unshown
    // there: the Hub and Settings have no chat column).
    if (_orig.chatside && _orig.deck) {
      if (_orig.chatsideNext && _orig.chatsideNext.parentNode === _orig.deck)
        _orig.deck.insertBefore(_orig.chatside, _orig.chatsideNext);
      else _orig.deck.insertBefore(_orig.chatside, _orig.deck.firstChild);
    }
    if (_orig.chatcol && _orig.deck) {
      if (_orig.chatcolNext && _orig.chatcolNext.parentNode === _orig.deck)
        _orig.deck.insertBefore(_orig.chatcol, _orig.chatcolNext);
      else _orig.deck.appendChild(_orig.chatcol);
    }
    document.documentElement.removeAttribute("data-agentview");
    try { upgradeSideSections(); } catch (e) {}      // back to the Hub's defaults
    _parked = false;
    stopAgentPolls();
  }

  // ==========================================================================
  // the left sidebar's Tools / Local AI sections: collapsible (D)
  // ==========================================================================
  // index.html's #chat-side labels them with static <div class="cs-lab">s.
  // Each becomes a real <button aria-expanded aria-controls> over its section
  // (same text, same look, a chevron), once. An owner's choice is remembered
  // per viewer; anything unreadable in storage is simply the default. The
  // defaults on the Agent page: Local AI open, Tools COLLAPSED (the same
  // actions already sit as chips over the composer and the conversation list
  // needs the height). The sidebar is shown nowhere else since the Hub lost
  // its full-screen chat (2026-11-12); sideDefault()'s off-page value only
  // decides what the parked element holds.
  var SIDE_KEY = "hermes_agent_side";
  var SIDE_SECS = [{ name: "tools", body: "cs-tools", label: "Tools", agentOpen: false },
                   { name: "ai", body: "cs-ai", label: "Local AI", agentOpen: true }];
  function sideDefault(sec) {
    return document.documentElement.hasAttribute("data-agentview") ? sec.agentOpen : true;
  }
  function sideState() {
    var o = {};
    try {
      var v = JSON.parse(localStorage.getItem(SIDE_KEY) || "null");
      if (v && typeof v === "object" && !Array.isArray(v))
        SIDE_SECS.forEach(function (sec) { if (typeof v[sec.name] === "boolean") o[sec.name] = v[sec.name]; });
    } catch (e) { o = {}; }
    return o;
  }
  function saveSide(o) { try { localStorage.setItem(SIDE_KEY, JSON.stringify(o)); } catch (e) {} }
  function upgradeSideSections() {
    var st = sideState();
    SIDE_SECS.forEach(function (sec) {
      var body = $(sec.body);
      if (!body) return;
      var hd = $("cs-" + sec.name + "-hd");
      if (!hd) {
        var lab = body.previousElementSibling;
        if (!lab || !lab.classList || !lab.classList.contains("cs-lab")) return;
        hd = document.createElement("button");
        hd.type = "button";
        hd.id = "cs-" + sec.name + "-hd";
        hd.className = "cs-lab cs-sechd";
        hd.setAttribute("aria-controls", sec.body);
        hd.innerHTML = svg("chevron", "chev") + '<span class="t">'
          + esc((lab.textContent || "").trim() || sec.label) + "</span>";
        lab.parentNode.replaceChild(hd, lab);
        hd.addEventListener("click", function () { toggleSide(sec, hd); });
      }
      var open = Object.prototype.hasOwnProperty.call(st, sec.name) ? st[sec.name] : sideDefault(sec);
      hd.setAttribute("aria-expanded", open ? "true" : "false");
      body.hidden = !open;
    });
  }
  // the owner's click: flip in place (focus stays on the header — it is a
  // native button, Enter/Space are the browser's), remember it, and fade the
  // section in through the motion runtime (opacity only, never a height;
  // either reduce switch lands it at once)
  function toggleSide(sec, hd) {
    var open = hd.getAttribute("aria-expanded") !== "true";
    hd.setAttribute("aria-expanded", open ? "true" : "false");
    var body = $(sec.body);
    if (body) body.hidden = !open;
    var st = sideState();
    st[sec.name] = open;
    saveSide(st);
    if (open && body) { try { anim().enter(body, "fade"); } catch (e) {} }
  }

  // ==========================================================================
  // B1 — wrap setView (add 'agent', alias old keys, migrate hermes_view)
  // ==========================================================================
  function normView(v) {
    if (v === "settings") return "mind";          // internal key stays 'mind'
    if (v === "console" || v === "desktop") return "agent";
    return v;
  }
  function wrapSetView() {
    if (window.__agentSetView) return;
    window.__agentSetView = true;
    var prev = window.setView;
    window.setView = function (raw) {
      var v = normView(raw);
      // aliased tabs open the matching rail pane (Console->Record, Desktop->Screen)
      var wantRail = raw === "console" ? "record" : raw === "desktop" ? "screen" : null;
      if (v === "agent") {
        ["view-hub", "view-mind", "view-console", "view-desktop"].forEach(function (id) {
          var e = $(id); if (e) e.hidden = true;
        });
        ["tab-hub", "tab-mind", "tab-console", "tab-desktop"].forEach(function (id) {
          var t = $(id); if (t) { t.classList.remove("on"); t.setAttribute("aria-selected", "false"); }
        });
        var va = $("view-agent"); if (va) va.hidden = false;
        var ta = $("tab-agent");
        if (ta) { ta.classList.add("on"); ta.setAttribute("aria-selected", "true"); }
        var hc = $("hubctl"); if (hc) hc.style.display = "none";
        try { localStorage.setItem("hermes_view", "agent"); } catch (e) {}
        enterAgent();
        if (wantRail) { try { openRail(wantRail); } catch (e) {} }
        syncSegTabs();
        return;
      }
      // leaving Agent (or never in it): tear down our view, then delegate
      var va2 = $("view-agent"); if (va2) va2.hidden = true;
      var ta2 = $("tab-agent");
      if (ta2) { ta2.classList.remove("on"); ta2.setAttribute("aria-selected", "false"); }
      exitAgent();
      if (typeof prev === "function") prev(v);
      syncSegTabs();
    };
  }

  // ==========================================================================
  // B2 — the Sigil + state line + brain badge
  // ==========================================================================
  var agentBrain = "local";               // 'local' | 'deep' (from bridge poll)
  var _busy = false;

  function setSigil(state) {               // idle | local | deep | acting | await
    var h = $("agent-hero"); if (!h) return;
    h.setAttribute("data-sig", state);
  }
  // one 12deg tick per tool event, on the snappy spring. The end angle stays
  // (spring persists it), so the ticks add up; .sig-ring's fill-box origin
  // keeps the pivot on the ring's centre (Motion 14 renders SVG transforms).
  function tickSigil() {
    var h = $("agent-hero"); if (!h) return;
    var ring = h.querySelector(".sig-ring");
    if (!ring) return;
    h.__tick = ((h.__tick || 0) + 12) % 360;
    anim().spring(ring, { rotate: [h.__tick - 12, h.__tick] }, "snappy");
  }
  function setStateLine(label, detail) {
    var s1 = $("ag-s1"), s2 = $("ag-s2");
    if (s1 && label != null) s1.textContent = label;
    if (s2 && detail != null) s2.textContent = detail;
  }

  // map an agent-status string to a labelled phase (never a bare spinner)
  var TOOL_LABEL = {
    terminal: "Running terminal", web_search: "Searching",
    file: "Working with files", computer_use: "Reading the screen",
    skill: "Using a skill", memory: "Remembering",
    delegate: "Delegating", "claude-bridge": "Thinking with Claude"
  };

  function driveHero(txt) {
    // txt is whatever setAgentState was called with. Keep the hero honest.
    if (txt == null) {                       // idle / turn done
      _busy = false;
      if (agentBrain === "deep") { setSigil("deep"); setStateLine("Thinking with Claude", null); }
      else { setSigil("idle"); setStateLine(idleLabel(), idleDetail()); }
      return;
    }
    _busy = true;
    var low = String(txt).toLowerCase();
    if (low.indexOf("approv") >= 0) { setSigil("await"); setStateLine("Waiting on you", "an action needs your approval"); return; }
    var tool = toolFromStatus(txt);
    if (tool) {
      setSigil("acting"); tickSigil();
      setStateLine(TOOL_LABEL[tool] || "Working", String(txt));
      return;
    }
    if (low.indexOf("writ") >= 0) { setSigil(agentBrain === "deep" ? "deep" : "local"); setStateLine("Writing", agentBrain === "deep" ? "with Claude — deep" : null); return; }
    setSigil(agentBrain === "deep" ? "deep" : "local");
    setStateLine(agentBrain === "deep" ? "Thinking with Claude" : "Thinking", String(txt));
  }
  // The three real states of the local model, read from what the page
  // already knows (index.html's loadModels() keeps agentPaused/agentIdle
  // from /api/models; the header pill shows the same two). No poller here.
  function modelState() {
    var p = false, i = false;
    try { p = (typeof agentPaused !== "undefined") && !!agentPaused; } catch (e) {}
    try { i = (typeof agentIdle !== "undefined") && !!agentIdle; } catch (e) {}
    return p ? "paused" : (i ? "asleep" : "awake");
  }
  var IDLE_LABEL = { awake: "Watching", asleep: "Asleep", paused: "Paused" };
  function idleLabel() { return IDLE_LABEL[modelState()]; }
  // a ticker fact claiming "On watch" while the model sleeps or is paused
  // drops the claim: the banner right above already names the state, so the
  // line says only what the banner does not ("Nothing needs you right now")
  var ON_WATCH = "On watch — ";
  function stateFact(f) {
    var st = modelState();
    if (st === "awake" || typeof f !== "string" || f.indexOf(ON_WATCH) !== 0) return f;
    var rest = f.slice(ON_WATCH.length);
    return rest.charAt(0).toUpperCase() + rest.slice(1);
  }
  // refresh the idle line, the ticker dot and the Local AI note; only while
  // no turn is running (a busy or deep line is never overwritten)
  function refreshIdle() {
    var st = modelState();
    var tk = $("agent-ticker"); if (tk) tk.setAttribute("data-model", st);
    try { aiStateNote(); } catch (e) {}
    if (_busy || agentBrain === "deep") return;
    var s1 = $("ag-s1");
    if (s1 && (s1.textContent === "Watching" || s1.textContent === "Asleep" || s1.textContent === "Paused")) {
      setStateLine(idleLabel(), idleDetail());
    }
    var el = $("ag-tick");
    // the line on screen now (one fact, or the one the crossfade left up)
    if (el) {
      var cur = el.textContent, f = stateFact(_tickFacts.length === 1 ? _tickFacts[0] : cur);
      // awake again with several facts: put back the "On watch" fact the
      // line was showing without its prefix
      if (st === "awake" && _tickFacts.length !== 1) {
        var all = _tickFacts.length ? _tickFacts : [ON_WATCH + "nothing needs you right now"];
        for (var i = 0; i < all.length; i++) {
          var tf = all[i];
          if (typeof tf === "string" && tf.indexOf(ON_WATCH) === 0 && tf !== cur) {
            var bare = tf.slice(ON_WATCH.length);
            if (cur === bare.charAt(0).toUpperCase() + bare.slice(1)) { f = tf; break; }
          }
        }
      }
      if (cur !== f) el.textContent = f;
    }
  }
  // Local AI rows (index.html's renderAIInfo writes them): a "—" figure is
  // hidden and one line says what the state is instead
  function aiStateNote() {
    var box = $("cs-ai"), tps = $("ai-tps"), ttft = $("ai-ttft");
    if (!box || !tps || !ttft) return;
    var noT = tps.textContent === "—", noF = ttft.textContent === "—";
    if (tps.parentNode && tps.parentNode.classList) tps.parentNode.classList.toggle("ag-nodata", noT);
    if (ttft.parentNode && ttft.parentNode.classList) ttft.parentNode.classList.toggle("ag-nodata", noF);
    var note = $("ag-ai-note");
    var st = modelState();
    var txt = !(noT || noF) ? ""
      : st === "asleep" ? "Model asleep — it wakes on your next message."
      : st === "paused" ? "Agent paused — nothing runs until you resume it from the model menu."
      : "No reply measured yet — speed and first-reply time show after one.";
    if (!txt) { if (note) note.parentNode.removeChild(note); return; }
    if (!note) {
      note = document.createElement("div");
      note.className = "cs-ai-note"; note.id = "ag-ai-note";
      box.insertBefore(note, box.firstChild);
    }
    if (note.textContent !== txt) note.textContent = txt;
  }
  function watchAiRows() {
    if (typeof MutationObserver !== "function") return;
    var ids = ["ai-tps", "ai-ttft", "ai-ram"], mo = null;
    for (var i = 0; i < ids.length; i++) {
      var el = $(ids[i]); if (!el) continue;
      if (!mo) mo = new MutationObserver(function () { try { refreshIdle(); } catch (e) {} });
      mo.observe(el, { childList: true, characterData: true, subtree: true });
    }
  }

  function idleDetail() {
    var st = modelState();
    if (st === "asleep") return "The model is asleep — it wakes on your next message.";
    if (st === "paused") return "The agent is paused — nothing runs until you resume it.";
    var live = window.__liveTps;
    if (live != null) return "Local — " + live.toFixed(1) + " tok/s";
    var el = $("ai-tps");
    var t = el ? el.textContent : "";
    return t && t !== "—" ? "Local model — " + t : "Ask, or just watch — your assistant is on watch.";
  }

  // ---- brain badge poll (/api/claude/bridge) — DISPLAY-ONLY --------------
  var _brainTimer = null, _lastDeepTs = 0;
  async function pollBrain() {
    var d = null;
    try { d = await (await fetch("/api/claude/bridge", { cache: "no-store" })).json(); } catch (e) { return; }
    if (!d || !d.ok) return;
    var recent = d.recent || [];
    var newest = recent.length ? recent[0] : null;
    var newestTs = newest && newest.ts ? newest.ts : 0;
    // "deep engaged" = a fresh Claude call landed during/just-before this turn
    var fresh = newestTs && (Date.now() / 1000 - newestTs) < 25;
    var deep = !!(fresh && _busy);
    if (deep && newestTs > _lastDeepTs) _lastDeepTs = newestTs;
    agentBrain = deep ? "deep" : "local";
    var badge = $("ag-brain");
    if (badge) {
      badge.classList.toggle("deep", deep);
      badge.classList.toggle("local", !deep);
    }
    var lbl = $("ag-brain-l");
    if (lbl) {
      var live = window.__liveTps;
      var el = $("ai-tps");
      var tok = live != null ? live.toFixed(0) + " tok/s"
        : (el && el.textContent && el.textContent !== "—" ? el.textContent : "Local");
      lbl.textContent = deep ? "Local" : tok;
    }
    // tooltip = routing reason + today's spend (honest, from the tail)
    if (badge) {
      var spend = d.recent_24h != null ? d.recent_24h : (recent.length);
      var why = newest && newest.reason ? newest.reason : "";
      badge.title = (deep ? "Thinking with Claude — deep" : "Local model — display only")
        + (why ? " · " + why : "")
        + " · Claude calls today: " + spend;
    }
    if (deep) setSigil("deep");
  }

  // ==========================================================================
  // B2 — SHOWIN_RENDER dispatcher (tool activity -> inline cards)
  // ==========================================================================
  function toolFromStatus(txt) {
    // Only a real tool announcement ("using <name>", hermes_rpc) is classified.
    // Free status strings ("thinking…", "writing…") are NOT tool names — the old
    // fallback ran them through the regexes and "thinking…" matched the bare
    // `think` alternative, so every ordinary turn grew an "Escalated to Claude"
    // card even with escalation switched off.
    var m = String(txt).match(/using\s+([a-z0-9_.\-]+)/i);
    if (!m) return null;
    return TOOL_MAP_fn(m[1].toLowerCase());
  }
  function TOOL_MAP_fn(name) {
    if (/shell|bash|terminal|command|\bexec\b|run_/.test(name)) return "terminal";
    if (/web_?search|websearch|search|\bweb\b|browse/.test(name)) return "web_search";
    if (/read_file|write_file|edit_file|str_replace|patch|apply|create_file|\bfile\b/.test(name)) return "file";
    if (/computer_use|computer|screenshot|click|cua/.test(name)) return "computer_use";
    if (/skill|invoke_skill/.test(name)) return "skill";
    if (/memor|remember|recall/.test(name)) return "memory";
    if (/delegate|sub_?agent|subagent|\btask\b/.test(name)) return "delegate";
    if (/claude|bridge|escalat|deep_think|think_hard|deep_reason/.test(name)) {
      // With the master switch off the bridge refuses every call, so a card that
      // says "Escalated to Claude" would be a lie — show nothing instead.
      return escEnabled === false ? null : "claude-bridge";
    }
    return null;                              // unknown -> FAIL OPEN (no card)
  }

  var GIST = {
    web_search: "Searched the web", file: "Worked with a file",
    computer_use: "Controlled the screen", skill: "Used a skill",
    memory: "Wrote to memory", delegate: "Delegated a task",
    "claude-bridge": "Escalated to Claude"
  };

  // --- the marquee TERMINAL card (full) ---------------------------------
  function makeTerminalCard() {
    var el = document.createElement("div");
    el.className = "showin running open";
    el.setAttribute("data-tool", "terminal");
    el.innerHTML =
      '<div class="showin-head">' + svg("shell")
      + '<span class="gist">running command</span>'
      + '<span class="chip">running</span><span class="dur"></span>'
      + svg("chevron", "chev") + '</div>'
      + '<div class="showin-body"><div class="term-body">'
      + '<div class="term-cmd" hidden>$ </div>'
      + '<pre class="term-out"></pre><span class="term-cur"></span>'
      + '</div></div>';
    var head = el.querySelector(".showin-head");
    head.addEventListener("click", function () { el.classList.toggle("open"); });
    var out = el.querySelector(".term-out");
    var cmdEl = el.querySelector(".term-cmd");
    var t0 = Date.now();
    var pinned = true;
    out.addEventListener("scroll", function () {
      pinned = (out.scrollTop + out.clientHeight >= out.scrollHeight - 8);
    });
    return {
      el: el, type: "terminal", done: false,
      setGist: function (cmd) {
        if (!cmd) return;
        el.querySelector(".gist").textContent = cmd;
        cmdEl.hidden = false; cmdEl.textContent = "$ " + cmd;
      },
      pushChunk: function (text, isErr) {
        if (text == null) return;
        var span = document.createElement("span");
        if (isErr) span.className = "e";
        span.textContent = text;
        out.appendChild(span);
        if (pinned) out.scrollTop = out.scrollHeight;
      },
      settle: function (opts) {
        opts = opts || {};
        if (this.done) return; this.done = true;
        el.classList.remove("running");
        var cur = el.querySelector(".term-cur"); if (cur) cur.remove();
        var chip = el.querySelector(".chip");
        var code = (opts.exit == null) ? 0 : opts.exit;
        chip.textContent = "exit " + code;
        chip.className = "chip " + (code === 0 ? "ok" : "bad");
        if (code !== 0) el.classList.add("err");
        var dur = ((Date.now() - t0) / 1000);
        el.querySelector(".dur").textContent = dur.toFixed(dur < 10 ? 1 : 0) + "s";
        // collapse to last 3 lines + "show all N"
        var full = out.textContent || "";
        var lines = full.split("\n");
        var nonEmpty = lines.filter(function (l) { return l.length; });
        if (nonEmpty.length > 3) {
          var shown = nonEmpty.slice(-3).join("\n");
          out.textContent = shown;
          var more = document.createElement("div");
          more.className = "term-more";
          more.textContent = "show all " + nonEmpty.length + " lines";
          more.addEventListener("click", function () {
            out.textContent = full; more.remove();
          });
          el.querySelector(".term-body").appendChild(more);
        }
        el.classList.remove("open");        // collapse terminal once complete
      }
    };
  }

  // --- generic collapsed STUB card (the other 8 types; full render in B4) --
  function makeStubCard(type) {
    var el = document.createElement("div");
    el.className = "showin running";
    el.setAttribute("data-tool", type);
    var icon = ({ web_search: "web", file: "file", computer_use: "computer",
      skill: "skill", memory: "memory", delegate: "delegate",
      "claude-bridge": "bridge" })[type] || "tool";
    el.innerHTML =
      '<div class="showin-head">' + svg(icon)
      + '<span class="gist">' + esc(GIST[type] || "Working") + '</span>'
      + '<span class="chip">running</span><span class="dur"></span>'
      + svg("chevron", "chev") + '</div>'
      + '<div class="showin-body"><div class="showin-stub-body">Details land in the Record rail. Full inline render coming soon.</div></div>';
    var head = el.querySelector(".showin-head");
    head.addEventListener("click", function () { el.classList.toggle("open"); });
    var t0 = Date.now();
    return {
      el: el, type: type, done: false,
      setGist: function (g) { if (g) el.querySelector(".gist").textContent = g; },
      pushChunk: function () {},
      settle: function (opts) {
        opts = opts || {};
        if (this.done) return; this.done = true;
        el.classList.remove("running");
        var chip = el.querySelector(".chip");
        var bad = opts.exit != null && opts.exit !== 0;
        chip.textContent = bad ? "failed" : "done";
        chip.className = "chip " + (bad ? "bad" : "ok");
        if (bad) el.classList.add("err");
        var dur = ((Date.now() - t0) / 1000);
        el.querySelector(".dur").textContent = dur.toFixed(dur < 10 ? 1 : 0) + "s";
      }
    };
  }

  // SHOWIN_RENDER map — mirrors RENDER{}/EXPAND_RENDER{} convention
  var SHOWIN_RENDER = {
    terminal: makeTerminalCard,
    web_search: function () { return makeStubCard("web_search"); },
    file: function () { return makeStubCard("file"); },
    computer_use: function () { return makeStubCard("computer_use"); },
    skill: function () { return makeStubCard("skill"); },
    memory: function () { return makeStubCard("memory"); },
    delegate: function () { return makeStubCard("delegate"); },
    "claude-bridge": function () { return makeStubCard("claude-bridge"); }
  };
  window.SHOWIN_RENDER = SHOWIN_RENDER;

  // ---- the manager: create/settle cards, group consecutive same-tool ----
  var _active = null;              // current running card object
  var _grp = null;                 // {type, el, count}

  function showinBirth(type) {
    var msgs = $("msgs");
    if (!msgs) return null;
    var factory = SHOWIN_RENDER[type];
    if (!factory) return null;
    var card = factory();
    if (!card || !card.el) return null;

    // consecutive same-tool grouping under an "N steps" header
    if (_grp && _grp.type === type && _grp.el && msgs.lastElementChild === _grp.el) {
      _grp.el.appendChild(card.el);
      _grp.count++;
      _grp.el.classList.add("multi");
      var gh = _grp.el.querySelector(".grp-head");
      if (gh) gh.textContent = _grp.count + " steps";
    } else {
      var g = document.createElement("div");
      g.className = "showin-group";
      g.innerHTML = '<div class="grp-head"></div>';
      g.appendChild(card.el);
      msgs.appendChild(g);
      _grp = { type: type, el: g, count: 1 };
    }
    msgs.scrollTop = msgs.scrollHeight;

    // the card rises in and its head icon turns once on the gel spring
    // (reduced motion, a hidden window: both land at once)
    try {
      var A = anim();
      A.enter(card.el, "rise");
      var ic = card.el.querySelector(".showin-head .ic");
      if (ic) A.spring(ic, { rotate: [0, 360] }, "gel");
    } catch (e) {}
    // per-turn in-memory event log for the future scrubber (B8)
    (window.agentTurnLog = window.agentTurnLog || []).push({ type: type, ts: Date.now() });
    return card;
  }

  function showinSettle(opts) {
    if (_active && !_active.done) {
      try { _active.settle(opts || {}); } catch (e) {}
      if (_active.type === "computer_use") { try { screenLive(false); } catch (e) {} }
    }
    _active = null;
  }

  // the dispatch: called for every setAgentState(txt). Returns nothing; throws
  // are swallowed by the wrapper so the stream never dies.
  function showinDispatch(txt) {
    if (txt == null) {                               // turn done
      showinSettle({}); _grp = null;
      try { screenLive(false); } catch (e) {}
      _screenAutoSwitched = false;                   // re-arm auto-switch for next turn
      try { decorateEscalate(); } catch (e) {}       // offer on-demand deep-think
      return;
    }
    var low = String(txt).toLowerCase();
    if (low.indexOf("approv") >= 0) return;         // approval owns its own path
    var type = toolFromStatus(txt);
    if (!type) {                                     // not a tool -> settle any active
      if (low.indexOf("using") < 0) showinSettle({});
      return;                                        // FAIL OPEN: plain line covers it
    }
    if (_active && _active.type === type && !_active.done) return;  // same tool still running
    showinSettle({});                                // different tool -> settle old
    _active = showinBirth(type);
    if (_active && type === "computer_use") { try { screenLive(true); } catch (e) {} }
    if (_active && type === "terminal") {
      // best-effort command text if the status carried one after "using shell"
      var extra = String(txt).replace(/^.*using\s+[a-z0-9_.\-]+\s*/i, "").trim();
      if (extra) _active.setGist(extra);
    }
  }
  window.showinDispatch = showinDispatch;   // exposed for the render harness

  // ==========================================================================
  // B2 — wrap setAgentState: the chat-poll status sink. This is where tool
  // activity and hero state are driven. The ORIGINAL always runs (fail open).
  // ==========================================================================
  function wrapAgentState() {
    if (window.__agentStateWrap) return;
    if (typeof window.setAgentState !== "function"
      && typeof setAgentState === "undefined") return;
    window.__agentStateWrap = true;
    var prev = (typeof window.setAgentState === "function")
      ? window.setAgentState : setAgentState;
    var wrapped = function (txt) {
      try { showinDispatch(txt); } catch (e) { /* degrade to today's UI */ }
      try { driveHero(txt); } catch (e) {}
      return prev.apply(this, arguments);           // original UNTOUCHED
    };
    try { window.setAgentState = wrapped; } catch (e) {}
    try { setAgentState = wrapped; } catch (e) {}
  }

  // ==========================================================================
  // B2 — heartbeat ticker v1 + EKG blip
  // ==========================================================================
  var _tickFacts = [], _tickIdx = 0, _tickTimer = null, _pulseTimer = null, _lastRecId = null;

  // three keyframes: a short tween (spring() runs 3+ frames as one), not a spring
  function blipEkg() {
    var d = $("ag-ekg"); if (!d) return;
    anim().spring(d, { scale: [1, 1.5, 1] });
  }

  async function loadPulse() {
    if (!isAgentVisible()) return;
    var d = null;
    try { d = await (await fetch("/api/agent/pulse", { cache: "no-store" })).json(); } catch (e) {}
    if (d && d.ok) {
      _tickFacts = (d.facts && d.facts.length) ? d.facts : _tickFacts;
      var la = d.last_action;
      if (la && la.id != null && la.id !== _lastRecId) {
        if (_lastRecId !== null) blipEkg();          // new recorder event -> pulse
        _lastRecId = la.id;
      }
      window.__agentPulse = d;                        // Pulse rail reads this
      renderPulseRail();
    } else {
      await loadPulseFallback();                       // /api/agent/pulse absent
    }
    if (!_tickFacts.length) _tickFacts = ["On watch — nothing needs you right now"];
  }
  async function loadPulseFallback() {
    var facts = [];
    try {
      var r = await (await fetch("/api/recorder?limit=3", { cache: "no-store" })).json();
      var a = (r.actions || [])[0];
      if (a) {
        if (a.id !== _lastRecId) { if (_lastRecId !== null) blipEkg(); _lastRecId = a.id; }
        facts.push("Last action " + relTime(a.ts) + " — ran " + (a.tool || "tool")
          + (a.target && a.target !== "—" ? " " + a.target : ""));
      }
    } catch (e) {}
    try {
      var f = await (await fetch("/api/foryou", { cache: "no-store" })).json();
      var n = (f.moves || []).length;
      if (n) facts.push("Scanned your feeds · " + n + (n === 1 ? " move" : " moves") + " for you");
    } catch (e) {}
    if (facts.length) _tickFacts = facts;
  }

  function crossfade() {
    if (!isAgentVisible() || !_tickFacts.length) return;
    var el = $("ag-tick"); if (!el) return;
    _tickIdx = (_tickIdx + 1) % _tickFacts.length;
    var next = stateFact(_tickFacts[_tickIdx]);
    if (el.textContent === next) return;          // one fact: nothing changes, nothing moves
    // out, swap the words, in — no timer. The fade-in starts once the exit has
    // fully ended: its clean-up puts the element's inline opacity back and
    // releases it from Motion, which would undo a fade-in started inside done.
    // A cancelled exit never swapped the words, so it fades nothing in.
    var A = anim(), swapped = false;
    var h = A.exit(el, "fadeOut", function () { el.textContent = next; swapped = true; });
    Promise.resolve(h && h.finished).then(function () { if (swapped) anim().enter(el, "fade"); });
  }

  function renderPulseRail() {
    var pane = document.querySelector('#agent-rail .railpane[data-rail="pulse"]');
    if (!pane || pane.hidden) return;
    var d = window.__agentPulse;
    if (!d || !d.events || !d.events.length) return;
    var h = "";
    for (var i = 0; i < Math.min(30, d.events.length); i++) {
      var e = d.events[i];
      var ic = e.source === "watchtower" ? "screen" : e.source === "foryou" ? "pulse" : "record";
      h += '<div style="display:flex;gap:8px;padding:8px 4px;border-bottom:1px solid var(--hairline);align-items:flex-start">'
        + '<span style="color:var(--muted);flex:none;margin-top:1px">' + svg(ic) + '</span>'
        + '<div style="min-width:0;flex:1"><div style="font-size:12px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(e.gist) + '</div>'
        + '<div style="font-size:10.5px;color:var(--faint)">' + esc(e.rel || "") + '</div></div></div>';
    }
    pane.innerHTML = h;
  }

  // ==========================================================================
  // B5 — fold Console (Record) and Desktop (Screen) into the right rail.
  // Reuse the existing renderers/state; never rewrite them.
  // ==========================================================================
  function t12(ts) {
    if (!ts) return "";
    try {
      return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true });
    } catch (e) { return ""; }
  }

  // switch the rail to a named pane (and lazily mount it)
  // keyboard for the rail tabs (C96.2) — the same roving-tabindex pattern as
  // the header switcher (wireSegKeys, C93): the active tab is the one Tab
  // stop, ArrowLeft/Right/Up/Down/Home/End move focus, Enter/Space switch
  // (manual activation: Screen fetches on open, so arrows must not).
  function railTabs(list) {
    return list ? Array.prototype.slice.call(list.querySelectorAll('[role="tab"]')) : [];
  }
  function syncRailTabs(list) {
    var tabs = railTabs(list), on = null, i;
    for (i = 0; i < tabs.length; i++) if (tabs[i].classList.contains("on")) on = tabs[i];
    on = on || tabs[0] || null;
    for (i = 0; i < tabs.length; i++) {
      tabs[i].tabIndex = (tabs[i] === on) ? 0 : -1;
      tabs[i].setAttribute("aria-selected", tabs[i] === on ? "true" : "false");
    }
  }
  function onRailKey(e) {
    var t = e.target;
    if (!t || !t.getAttribute || t.getAttribute("role") !== "tab") return;
    var tabs = railTabs(this), i = tabs.indexOf(t), n = -1, k = e.key;
    if (i < 0) return;
    if (k === "Enter" || k === " " || k === "Spacebar") {
      e.preventDefault();
      if (e.repeat) return;                     // a held key switches once
      try { t.click(); } catch (x) {}
      try { t.focus(); } catch (x) {}
      return;
    }
    if (k === "ArrowRight" || k === "ArrowDown") n = (i + 1) % tabs.length;
    else if (k === "ArrowLeft" || k === "ArrowUp") n = (i - 1 + tabs.length) % tabs.length;
    else if (k === "Home") n = 0;
    else if (k === "End") n = tabs.length - 1;
    if (n < 0) return;
    e.preventDefault();
    for (var j = 0; j < tabs.length; j++) tabs[j].tabIndex = (j === n) ? 0 : -1;
    try { tabs[n].focus(); } catch (x) {}
  }
  function wireRailKeys(list) {
    if (!list || list.__railKeys) return;
    list.__railKeys = true;
    list.addEventListener("keydown", onRailKey);
    // focus leaving the tab row puts the Tab stop back on the active tab
    list.addEventListener("focusout", function (e) {
      var to = e && e.relatedTarget;
      if (!to || !list.contains(to)) syncRailTabs(list);
    });
    syncRailTabs(list);
  }

  function openRail(name) {
    var v = $("view-agent"); if (!v) return;
    var bs = v.querySelectorAll(".railtabs b");
    for (var j = 0; j < bs.length; j++) {
      var on = bs[j].getAttribute("data-rail") === name;
      bs[j].classList.toggle("on", on);
      // clear the *unread* dot on activate, but NOT the red LIVE state (that is
      // owned by screenLive() while computer_use runs — CSS shows it via .live).
      if (on) { var ud = bs[j].querySelector(".udot"); if (ud) ud.style.display = ""; }
    }
    syncRailTabs(v.querySelector(".railtabs"));     // aria-selected + the one Tab stop
    var ps = v.querySelectorAll(".railpane");
    for (var k = 0; k < ps.length; k++) ps[k].hidden = ps[k].getAttribute("data-rail") !== name;
    if (name === "record") mountRecordRail();
    else if (name === "screen") mountScreenRail(true);
    else if (name === "pulse") renderPulseRail();
  }

  // ---- Record rail: relocate the existing Flight Recorder card -----------
  // aux_recorder's recEnsureCard() returns #recorder-card wherever it lives and
  // loadRecorder() repopulates it in place + keeps recState fresh (so detail/undo
  // keep working). We create it, move it into our pane once, then ride our poll.
  var _recMoved = false, _recTimer = null;
  function mountRecordRail() {
    var pane = $("rail-record"); if (!pane) return;
    if (typeof window.loadRecorder !== "function") {
      pane.innerHTML = '<div class="railstub">The flight recorder module is not loaded.</div>';
      return;
    }
    try { window.loadRecorder(); } catch (e) {}          // creates/updates #recorder-card
    var card = $("recorder-card");
    if (card && card.parentNode !== pane) { pane.innerHTML = ""; pane.appendChild(card); _recMoved = true; }
    if (!_recTimer) {
      _recTimer = setInterval(function () {
        if (!isAgentVisible()) return;
        var rp = $("rail-record");
        if (!rp || rp.hidden) return;                     // only poll while shown
        // a repaint, not news: everything the recorder starts synchronously
        // here lands at once (8.10). Its rows paint after a fetch, outside
        // quiet(), so aux_recorder keys its own row entrance per id (8.11).
        try { anim().quiet(function () { return window.loadRecorder(); }); } catch (e) {}
        var c = $("recorder-card");
        if (c && c.parentNode !== rp) rp.appendChild(c);  // keep it home after any re-render
      }, 3000);
    }
  }

  // ---- Screen rail: render via aux_desktop's pure renderDesktop() ---------
  var _screenTimer = null, _screenBusy = false, _screenPinned = null;
  async function mountScreenRail(force) {
    var pane = $("rail-screen"); if (!pane) return;
    if (typeof window.renderDesktop !== "function") {
      pane.innerHTML = '<div class="railstub">The Screen module is not loaded.</div>';
      return;
    }
    if (_screenBusy) return; _screenBusy = true;
    try {
      var st = {}, tl = {};
      try { st = await (await fetch("/api/desktop/shots", { cache: "no-store" })).json(); } catch (e) {}
      try { tl = await (await fetch("/api/desktop/timeline", { cache: "no-store" })).json(); } catch (e) {}
      var shots = (st && st.shots) || [];
      var full = (_screenPinned && shots.some(function (s) { return s.name === _screenPinned.name; }))
        ? _screenPinned.uri : null;
      var html = window.renderDesktop(st || {}, tl || {}, full);
      if (pane.querySelector("#desk-ctl-card") && pane.querySelector("#desk-live-card")
          && pane.querySelector("#desk-tl-card")) {
        // swap only the two read-only cards: the compose card — what is being
        // typed into it, its focus and its status line — must survive the 4 s poll
        var tmp = document.createElement("div");
        tmp.innerHTML = html;
        ["desk-live-card", "desk-tl-card"].forEach(function (id) {
          var cur = pane.querySelector("#" + id), next = tmp.querySelector("#" + id);
          if (cur && next && cur.parentNode) cur.parentNode.replaceChild(next, cur);
        });
      } else {
        pane.innerHTML = html;
      }
      wireScreen(pane);
    } catch (e) {
      pane.innerHTML = '<div class="railstub">Screen panel unavailable.</div>';
    }
    _screenBusy = false;
    if (!_screenTimer) {
      _screenTimer = setInterval(function () {
        if (!isAgentVisible()) return;
        var rp = $("rail-screen");
        var live = document.querySelector('#agent-rail .railtabs b[data-rail="screen"].live');
        if ((!rp || rp.hidden) && !live) return;          // poll while shown OR computer_use live
        mountScreenRail(false);
      }, 4000);
    }
  }
  function wireScreen(pane) {
    var cap = pane.querySelector("#desk-cap");
    if (cap) cap.onclick = function () {
      cap.disabled = true;
      fetch("/api/desktop/capture", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
        .then(function () { return mountScreenRail(true); })
        .catch(function () {})
        .then(function () { var c = pane.querySelector("#desk-cap"); if (c) c.disabled = false; });
    };
    var ref = pane.querySelector("#desk-refresh");
    if (ref) ref.onclick = function () { mountScreenRail(true); };
    // "Send to agent": idempotent, so re-running it after every re-render is safe
    if (typeof window.deskWireCompose === "function") {
      try { window.deskWireCompose(pane, function () { mountScreenRail(true); }); } catch (e) {}
    }
    var strip = pane.querySelectorAll(".desk-strip img");
    for (var i = 0; i < strip.length; i++) {
      strip[i].onclick = function () {
        var name = this.getAttribute("data-name");
        var big = pane.querySelector("#desk-full");
        if (big && this.getAttribute("src")) { big.src = this.getAttribute("src"); _screenPinned = { name: name, uri: this.getAttribute("src") }; }
        var imgs = pane.querySelectorAll(".desk-strip img");
        for (var k = 0; k < imgs.length; k++) imgs[k].classList.toggle("on", imgs[k].getAttribute("data-name") === name);
      };
    }
  }
  // computer_use lifecycle: red LIVE dot + one auto-switch to Screen per turn
  var _cuLive = false, _screenAutoSwitched = false;
  function screenLive(on) {
    _cuLive = !!on;
    // the red LIVE dot is purely CSS-driven off the .live class (b.live .udot),
    // so it survives an openRail() activate and clears when we drop .live.
    var tab = document.querySelector('#agent-rail .railtabs b[data-rail="screen"]');
    if (tab) tab.classList.toggle("live", !!on);
    if (on) {
      mountScreenRail(true);
      if (!_screenAutoSwitched) { _screenAutoSwitched = true; openRail("screen"); }
    }
  }

  // ==========================================================================
  // Claude dialogues (full task + response) — /api/claude/recent, display-only
  // ==========================================================================
  var _claudeTimer = null;
  async function loadClaudeRecent() {
    if (!isAgentVisible()) return;
    var d = null;
    try { d = await (await fetch("/api/claude/recent?n=20", { cache: "no-store" })).json(); } catch (e) { return; }
    if (!d || !d.ok) return;
    window.__claudeRecent = d.calls || [];
    var btn = $("ag-deepbtn"), t = $("ag-deepbtn-t");
    var n = window.__claudeRecent.length;
    if (btn) btn.classList.toggle("show", n > 0);
    if (t) t.textContent = "Claude dialogues" + (n ? " · " + n : "");
    if ($("ag-deep") && $("ag-deep").classList.contains("open")) renderDeepList();
  }
  // The list is polled every 12 s while the sheet is open (R2-39): it is only
  // rebuilt when the set of calls changed (keyed by id + ts — entries are
  // immutable once written), and a rebuild keeps the rows the user expanded
  // and the row they were reading where it was on screen.
  function dcKeys(calls) {
    var seen = Object.create(null), out = [];
    for (var i = 0; i < calls.length; i++) {
      var c = calls[i] || {};
      var k = (c.id != null ? String(c.id) : "") + "@" + (c.ts != null ? String(c.ts) : "");
      seen[k] = (seen[k] || 0) + 1;
      out.push(k + "#" + seen[k]);
    }
    return out;
  }
  function dcRowByKey(list, k) {
    var rows = list.querySelectorAll(".dcall");
    for (var i = 0; i < rows.length; i++) if (rows[i].getAttribute("data-k") === k) return rows[i];
    return null;
  }
  function renderDeepList() {
    var list = $("ag-deep-list"); if (!list) return;
    var calls = window.__claudeRecent || [];
    var keys = dcKeys(calls), sig = keys.join("\n");
    if (list.__dcSig === sig) return;                 // nothing new: leave the DOM alone
    if (!calls.length) { list.innerHTML = '<div class="railstub">No deep escalations yet. When the agent asks Claude to reason, the full exchange appears here.</div>'; list.__dcSig = sig; return; }
    // what to keep: expanded rows, the focused row head, and the first row in view + its offset
    var open = Object.create(null), st = list.scrollTop || 0, anchor = null, lt = 0, old = list.querySelectorAll(".dcall"), j;
    for (j = 0; j < old.length; j++) if (old[j].classList.contains("open")) open[old[j].getAttribute("data-k")] = true;
    var fk = null, ae = document.activeElement;
    for (j = 0; j < old.length; j++) if (ae && old[j].contains(ae)) { fk = old[j].getAttribute("data-k"); break; }
    try {
      lt = list.getBoundingClientRect().top;
      for (j = 0; j < old.length; j++) {
        var b = old[j].getBoundingClientRect();
        if (b.height && b.bottom > lt) { anchor = { k: old[j].getAttribute("data-k"), off: b.top - lt }; break; }
      }
    } catch (e) { anchor = null; }
    var h = "";
    for (var i = 0; i < calls.length; i++) {
      var c = calls[i] || {};
      var bad = c.ok === false || c.error;
      var task = (c.task || "").trim() || "(no task)";
      var resp = bad ? (c.error || c.response || "Claude did not answer.") : (c.response || "(empty response)");
      var secs = c.ms ? (c.ms / 1000).toFixed(c.ms < 10000 ? 1 : 0) + "s" : "";
      var on = open[keys[i]] === true;
      h += '<div class="dcall' + (bad ? " err" : "") + (on ? " open" : "") + '" data-i="' + i + '" data-k="' + esc(keys[i]) + '">'
        + '<button type="button" class="dc-head" aria-expanded="' + (on ? "true" : "false") + '" aria-controls="ag-dc-' + i + '">'
        + '<span class="dc-task">' + esc(task) + '</span>'
        + '<span class="dc-model">' + esc(c.model || c.depth || "claude") + '</span>'
        + '<span class="dc-meta">' + esc([t12(c.ts), secs].filter(Boolean).join(" · ")) + '</span>'
        + '</button>'
        + '<div class="dc-body" id="ag-dc-' + i + '">'
        + '<div class="dc-k">Asked Claude</div><div class="dc-q">' + esc(task) + '</div>'
        + '<div class="dc-k">' + (bad ? "Result" : "Claude · " + esc(c.depth || "deep")) + '</div><div class="dc-a">' + esc(resp) + '</div>'
        + '</div></div>';
    }
    list.innerHTML = h;
    list.__dcSig = sig;                               // only once the rows are really there
    var rows = list.querySelectorAll(".dcall");
    for (j = 0; j < rows.length; j++) {
      rows[j].querySelector(".dc-head").addEventListener("click", function () {
        var row = this.parentNode;
        row.classList.toggle("open");
        this.setAttribute("aria-expanded", row.classList.contains("open") ? "true" : "false");
      });
    }
    list.scrollTop = st;
    if (anchor) {
      try {
        var nr = dcRowByKey(list, anchor.k);
        if (nr) list.scrollTop = st + (nr.getBoundingClientRect().top - lt - anchor.off);
      } catch (e) {}
    }
    // the rebuild replaced the focused head: hand focus to its replacement
    if (fk) {
      var fr = dcRowByKey(list, fk), fh = fr && fr.querySelector(".dc-head");
      if (fh) { try { fh.focus({ preventScroll: true }); } catch (e) {} }
    }
  }
  var deepOpener = null;
  function deepIsOpen() { var dm = $("ag-deep"); return !!dm && dm.classList.contains("open"); }
  function openDeep() {
    var dm = $("ag-deep"); if (!dm) return;
    var was = dm.classList.contains("open");
    renderDeepList();
    dm.classList.add("open");
    if (!was) {
      var a = document.activeElement;
      deepOpener = (a && a !== document.body && !dm.contains(a)) ? a : null;
      var x = $("ag-deep-x");
      if (x) { try { x.focus({ preventScroll: true }); } catch (e) {} }
    }
    loadClaudeRecent();
    if (!REDUCE) { try { agAnimate(dm.querySelector(".dsheet"), { opacity: [0, 1], transform: ["translateY(10px)", "none"] }, { duration: 0.4, easing: SPRING }); } catch (e) {} }
  }
  function closeDeep() {
    var dm = $("ag-deep"); if (!dm) return;
    var was = dm.classList.contains("open");
    dm.classList.remove("open");
    if (!was) return;
    // focus goes back where it came from (the Claude dialogues button)
    var back = (deepOpener && deepOpener.isConnected) ? deepOpener : $("ag-deepbtn");
    deepOpener = null;
    var a = document.activeElement;
    if (back && (!a || a === document.body || dm.contains(a))) { try { back.focus({ preventScroll: true }); } catch (e) {} }
  }
  function deepFocusables(root) {
    var all = root.querySelectorAll("button,input,select,textarea,a[href],[tabindex]"), out = [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.tabIndex >= 0 && !el.disabled && !(el.closest && el.closest("[hidden]"))) out.push(el);
    }
    return out;
  }
  function onDeepKey(e) {
    if (e.defaultPrevented || !deepIsOpen()) return;
    var dm = $("ag-deep"), sheet = dm.querySelector(".dsheet") || dm;
    if (e.key === "Escape" || e.key === "Esc") {
      e.preventDefault();
      closeDeep();
      return;
    }
    if (e.key === "Tab") {               // keep Tab inside the open sheet
      var f = deepFocusables(sheet);
      if (!f.length) { e.preventDefault(); return; }
      var i = f.indexOf(document.activeElement);
      if (i < 0 || (e.shiftKey && i === 0) || (!e.shiftKey && i === f.length - 1)) {
        e.preventDefault();
        f[e.shiftKey ? (i <= 0 ? f.length - 1 : 0) : 0].focus();
      }
    }
  }

  // ==========================================================================
  // On-demand "Escalate to Claude" on a normal bot reply (display/answer-only)
  // ==========================================================================
  // ---- "Escalate to Claude" master switch (index.html owns the state) ------
  // When escalation is off the manual button must not be offered — pressing it
  // would send the question to Claude anyway, which is exactly what the switch
  // says will not happen. `null`/absent (older backend, no route) means "no
  // switch exists", and we behave exactly as before.
  var escEnabled = true;
  function escSyncButtons() {
    var msgs = $("msgs"); if (!msgs) return;
    var rows = msgs.querySelectorAll(".esc-row");
    for (var i = 0; i < rows.length; i++) rows[i].hidden = !escEnabled;
  }
  function escReadState() {
    try {
      if (typeof window.claudeEscalationState === "function") escEnabled = window.claudeEscalationState() !== false;
    } catch (e) {}
  }
  try {
    escReadState();
    window.addEventListener("hermes:claude-escalation", function (ev) {
      var det = (ev && ev.detail) || {};
      escEnabled = !(det.available !== false && det.enabled === false);
      escSyncButtons();                       // hides immediately / re-shows
    });
  } catch (e) {}

  function decorateEscalate() {
    var msgs = $("msgs"); if (!msgs) return;
    var bots = msgs.querySelectorAll(".bubble.bot");
    var target = null;
    for (var i = bots.length - 1; i >= 0; i--) {
      var b = bots[i];
      if (b.classList.contains("err")) continue;
      if (b.getAttribute("data-esc") === "1") { target = null; break; }   // newest already decorated
      target = b; break;
    }
    if (!target) return;
    if (target.querySelector(".dots")) return;               // still the thinking placeholder
    target.setAttribute("data-esc", "1");
    if (target.querySelector(".deepcard")) return;          // auto-route already answered
    // find the user question that produced this reply (FULL text — the first
    // line alone lost multi-line questions)
    var q = "";
    var prev = target;
    while (prev) {
      prev = prev.previousSibling;
      if (prev && prev.classList && prev.classList.contains("user")) { q = (prev.textContent || "").trim().slice(0, 4000); break; }
    }
    var row = document.createElement("div");
    row.className = "esc-row";
    row.hidden = !escEnabled;      // built either way so the event can un-hide
    var btn = document.createElement("button");
    btn.className = "esc-btn";
    btn.innerHTML = svg("bridge") + "Escalate to Claude";
    btn.title = "Re-run this question through Claude for deeper reasoning";
    btn.addEventListener("click", function () { doEscalate(btn, target, q); });
    row.appendChild(btn);
    target.appendChild(row);
  }
  // ---- Claude auto-route (aux_autoroute) — render deep {state,...} on a bubble
  function deepIcon() { return svg("bridge"); }
  function ensureDeepCard(bubble) {
    var card = bubble.querySelector(".deepcard");
    if (card) return card;
    card = document.createElement("div");
    card.className = "deepcard";
    card.innerHTML = '<div class="dk-head">' + deepIcon() + '<span class="dk-lab">Claude</span>'
      + '<span class="dk-meta"></span></div><div class="dk-body"><span class="term-cur"></span></div>';
    bubble.appendChild(card);
    return card;
  }
  function fillDeepCard(card, d) {
    var meta = card.querySelector(".dk-meta"), body = card.querySelector(".dk-body"), lab = card.querySelector(".dk-lab");
    if (d.reason) card.title = d.reason;
    if (d.state === "thinking") {
      lab.textContent = "Claude · " + (d.depth === "deep" ? "deep" : "quick") + " · auto";
      meta.textContent = "thinking…";
      return;
    }
    if (d.ok && d.text) {
      var secs = d.ms ? (d.ms / 1000).toFixed(d.ms < 10000 ? 1 : 0) + "s" : "";
      lab.textContent = "Claude · " + (d.depth === "deep" ? "deep" : "quick") + (d.auto ? " · auto" : "");
      meta.textContent = [d.model || "", secs, t12(d.ts || Date.now() / 1000)].filter(Boolean).join(" · ");
      body.innerHTML = (typeof window.renderMd === "function") ? window.renderMd(d.text) : esc(d.text);
      card.classList.remove("err");
    } else {
      card.classList.add("err");
      lab.textContent = d.refused ? "Claude declined" : "Claude unavailable";
      meta.textContent = t12(Date.now() / 1000);
      body.textContent = d.error || "Claude could not answer this.";
    }
  }
  var _deepDone = {};                                  // jid -> rendered final state
  window.hermesDeep = function (jid, bubble, d) {
    if (!bubble || !d) return;
    if (d.state === "suggest") {                       // just flag the escalate button
      bubble.setAttribute("data-deep-suggest", d.reason || "");
      var b = bubble.querySelector(".esc-btn");
      if (b) { b.classList.add("suggest"); b.title = "Looks like a hard question — " + (d.reason || ""); }
      return;
    }
    var card = ensureDeepCard(bubble);
    bubble.setAttribute("data-esc", "1");
    var row = bubble.querySelector(".esc-row"); if (row) row.remove();
    if (d.state === "thinking") {
      fillDeepCard(card, d);
      setSigil("deep"); setStateLine("Thinking with Claude", "auto-routed — " + (d.reason || "hard question"));
      return;
    }
    if (_deepDone[jid]) return;
    _deepDone[jid] = true;
    fillDeepCard(card, d);
    setSigil("idle"); setStateLine(idleLabel(), idleDetail());
    if (d.ok) { try { loadClaudeRecent(); } catch (e) {} }
    else if (!d.refused) {                             // unavailable → offer manual retry
      bubble.removeAttribute("data-esc"); try { decorateEscalate(); } catch (e) {}
    }
    var m = $("msgs"); if (m) m.scrollTop = m.scrollHeight;
  };

  async function doEscalate(btn, bubble, question) {
    if (!question) { question = (bubble.textContent || "").slice(0, 400); }
    btn.disabled = true;
    btn.innerHTML = svg("bridge") + "Thinking with Claude…";
    setSigil("deep"); setStateLine("Thinking with Claude", "deep reasoning — on demand");
    var card = document.createElement("div");
    card.className = "deepcard";
    card.innerHTML = '<div class="dk-head">' + svg("bridge") + '<span class="dk-lab">Claude · deep</span>'
      + '<span class="dk-meta">thinking…</span></div><div class="dk-body"><span class="term-cur"></span></div>';
    bubble.appendChild(card);
    var task = question + "\n\n(Please reason more deeply and thoroughly than a quick answer — lay out the considerations and give your best-reasoned response.)";
    var d = null;
    try {
      d = await (await fetch("/api/claude/think", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: task, depth: "deep" })
      })).json();
    } catch (e) { d = { ok: false, error: "Could not reach Claude." }; }
    var meta = card.querySelector(".dk-meta");
    var body = card.querySelector(".dk-body");
    setSigil("idle"); setStateLine(idleLabel(), idleDetail());
    if (d && d.ok && (d.text || d.response)) {
      var secs = d.ms ? (d.ms / 1000).toFixed(d.ms < 10000 ? 1 : 0) + "s" : "";
      if (meta) meta.textContent = [esc(d.model || "opus"), secs, t12(Date.now() / 1000)].filter(Boolean).join(" · ");
      var ans = d.text || d.response;
      body.innerHTML = (typeof window.renderMd === "function") ? window.renderMd(ans) : esc(ans);
      btn.innerHTML = svg("check") + "Answered by Claude";
      loadClaudeRecent();
    } else {
      card.classList.add("err");
      card.querySelector(".dk-lab").textContent = (d && d.refused) ? "Claude declined" : "Claude unavailable";
      if (meta) meta.textContent = t12(Date.now() / 1000);
      body.textContent = (d && (d.text || d.reason || d.error)) || "Claude could not answer this.";
      btn.disabled = false;
      btn.innerHTML = svg("bridge") + "Escalate to Claude";
    }
    var m = $("msgs"); if (m) m.scrollTop = m.scrollHeight;
  }

  // ==========================================================================
  // polling lifecycle (visibility + view gated)
  // ==========================================================================
  function isAgentVisible() {
    var va = $("view-agent");
    return !!(va && !va.hidden
      && (typeof document.visibilityState === "undefined"
        || document.visibilityState === "visible"));
  }
  function startAgentPolls() {
    if (!_brainTimer) { pollBrain(); _brainTimer = setInterval(function () { if (isAgentVisible()) pollBrain(); }, 5000); }
    if (!_pulseTimer) { loadPulse(); _pulseTimer = setInterval(loadPulse, 10000); }
    if (!_tickTimer) { _tickTimer = setInterval(crossfade, 8000); }
    if (!_claudeTimer) { loadClaudeRecent(); _claudeTimer = setInterval(function () { if (isAgentVisible()) loadClaudeRecent(); }, 12000); }
    // keep the Local AI panel live in the Agent left rail
    if (!window.__agentAiTimer) window.__agentAiTimer = setInterval(function () {
      if (isAgentVisible() && typeof window.renderAIInfo === "function") window.renderAIInfo();
      if (isAgentVisible()) { try { refreshIdle(); } catch (e) {} }
    }, 5000);
  }
  function stopAgentPolls() {
    if (_brainTimer) { clearInterval(_brainTimer); _brainTimer = null; }
    if (_pulseTimer) { clearInterval(_pulseTimer); _pulseTimer = null; }
    if (_tickTimer) { clearInterval(_tickTimer); _tickTimer = null; }
    if (_claudeTimer) { clearInterval(_claudeTimer); _claudeTimer = null; }
    if (_recTimer) { clearInterval(_recTimer); _recTimer = null; }
    if (_screenTimer) { clearInterval(_screenTimer); _screenTimer = null; }
  }

  // ==========================================================================
  // boot
  // ==========================================================================
  function migrateStoredView() {
    var sv = null;
    try { sv = localStorage.getItem("hermes_view"); } catch (e) {}
    if (sv === "console" || sv === "desktop" || sv === "agent") {
      try { localStorage.setItem("hermes_view", "agent"); } catch (e) {}
      return "agent";
    }
    return sv;
  }

  function boot() {
    buildShell();
    try { upgradeSideSections(); } catch (e) {}
    addTab();
    wrapSetView();
    wrapAgentState();
    try { watchAiRows(); refreshIdle(); } catch (e) {}
    var target = migrateStoredView();
    if (target === "agent" && typeof window.setView === "function") window.setView("agent");
    document.addEventListener("visibilitychange", function () {
      if (isAgentVisible()) { pollBrain(); loadPulse(); }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  // exports for the headless render harness (expand.js pattern)
  window.__agentTest = {
    buildShell: buildShell, sigilSvg: sigilSvg, brainBadgeHtml: brainBadgeHtml,
    makeTerminalCard: makeTerminalCard, makeStubCard: makeStubCard,
    showinDispatch: showinDispatch, driveHero: driveHero, setSigil: setSigil,
    toolFromStatus: toolFromStatus, loadPulse: loadPulse, crossfade: crossfade,
    enterAgent: enterAgent, exitAgent: exitAgent, pollBrain: pollBrain,
    SHOWIN_RENDER: SHOWIN_RENDER,
    mountRecordRail: mountRecordRail, mountScreenRail: mountScreenRail,
    openRail: openRail, screenLive: screenLive,
    loadClaudeRecent: loadClaudeRecent, renderDeepList: renderDeepList,
    openDeep: openDeep, closeDeep: closeDeep,
    decorateEscalate: decorateEscalate, doEscalate: doEscalate, t12: t12,
    upgradeSideSections: upgradeSideSections, sideState: sideState,
    modelState: modelState, idleLabel: idleLabel, idleDetail: idleDetail,
    setFacts: function (a) { _tickFacts = Array.isArray(a) ? a.slice() : []; },
    stateFact: stateFact, refreshIdle: refreshIdle, aiStateNote: aiStateNote
  };
})();
