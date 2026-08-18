/* aux_agent.js — the flagship Agent page (UI restructure B1 + B2).
 *
 * Loaded LAST (after expand.js + every aux_*.js) so its retab sees the final
 * tab DOM and it can re-mount sibling renderers later (B5). It WRAPS existing
 * hooks — it never rewrites them:
 *
 *   B1 SHELL
 *     · injects #view-agent into .stage (left rail 232 · stream 860 · right
 *       rail 320 · heartbeat ticker), all CSS in one injected <style>.
 *     · wraps setView() to add an 'agent' view + an "Agent" tab between Hub and
 *       Settings; aliases old keys (console/desktop -> agent, settings -> mind);
 *       migrates localStorage hermes_view.
 *     · on entering Agent, RE-PARENTS the live chat DOM (.chatcol = #msgs +
 *       composer) into #agent-stream and the #chat-side sidebar into #agent-side
 *       (reusing renderChatSide, not rewriting it); RETURNS both on exit so the
 *       Hub split-chat (deck[data-chat]) path is never touched.
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
 * glyphs bespoke two-tone SVG); all discrete motion via animate()+SPRING with
 * REDUCE fallbacks (matchMedia reduced-motion -> orbits frozen, fades only).
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
      /* single-column deck while Agent is up so the stage spans full width */
      ':root[data-agentview] .deck{grid-template-columns:1fr!important}',
      ':root[data-agentview] #chat-restore{display:none!important}',
      /* neutralise whatever chat-mode the deck was in when we entered Agent:
         data-chat="full" hides .stage, data-chat="hidden" hides .chatcol —
         both would blank the re-parented Agent view. */
      ':root[data-agentview] .stage{display:flex!important}',
      ':root[data-agentview] #agent-stream .chatcol{display:flex!important}',

      "#agent-side{width:232px;flex:none;min-height:0;display:flex;flex-direction:column}",
      "@media (max-width:1100px){#agent-side{display:none}}",
      /* reuse the moved #chat-side verbatim, just show it here */
      "#agent-side .chatside{display:flex!important;flex-direction:column;flex:1;min-height:0;overflow:hidden;margin:0}",

      "#agent-center{flex:1;min-width:0;display:flex;flex-direction:column;min-height:0}",
      "#agent-stream{flex:1;min-height:0;display:flex;justify-content:center;overflow:hidden}",
      "#agent-stream .chatcol{flex:1;max-width:860px;width:100%;min-height:0;display:flex;opacity:1}",
      "#agent-stream .chatcol .chat{flex:1;height:100%}",
      "#agent-stream .chat-head #chat-max,#agent-stream .chat-head #chat-hide{display:none}",

      "#agent-rail{width:320px;flex:none;min-height:0;display:flex;flex-direction:column}",
      "@media (max-width:1280px){#agent-rail{display:none}}",

      /* ---------- status hero ---------- */
      "#agent-hero{display:flex;align-items:center;gap:13px;padding:9px 14px;margin-bottom:8px;"
        + "border-radius:var(--radius-sm);min-height:56px;box-sizing:border-box}",
      "#agent-hero .sig{width:36px;height:36px;flex:none;position:relative}",
      "#agent-hero .sig svg{width:36px;height:36px;overflow:visible}",
      "#agent-hero .state{display:flex;flex-direction:column;gap:1px;min-width:0;flex:1}",
      "#agent-hero .state .s1{font-size:13px;font-weight:640;color:var(--ink);letter-spacing:-.01em}",
      "#agent-hero .state .s2{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",

      /* the Sigil */
      ".sig-ring{transform-origin:18px 18px}",
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
      /* Screen rail = watch-only: hide the desktop panel's task-compose card */
      "#agent-rail #desk-ctl-card{display:none}",
      "#agent-rail #recorder-card{border:none;background:transparent;box-shadow:none}",
      "#agent-rail #recorder-card>h2{font-size:12.5px;padding:0 2px 6px}",
      "#agent-rail .desk-card{margin-bottom:8px}",
      /* red LIVE dot on the Screen tab while computer_use runs */
      '#agent-rail .railtabs b[data-rail="screen"].live .udot{display:block;background:var(--bad);'
        + "box-shadow:0 0 0 3px color-mix(in srgb,var(--bad) 26%,transparent);animation:lpulse 1.1s infinite}",

      /* B5-hide the now-redundant top tabs (DOM kept; views reachable via setView).
         Net top bar: Hub · Agent. Settings/Console/Desktop fold into rails/prefs. */
      "#tab-mind,#tab-console,#tab-desktop{display:none!important}",

      /* ---------- Deep-thinking affordance + Claude-dialogue viewer ---------- */
      ".deepbtn{display:none;align-items:center;gap:5px;flex:none;font-size:11px;font-weight:600;"
        + "padding:4px 10px;border-radius:99px;cursor:pointer;color:var(--claude);"
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
      ".dcall .dc-head{display:flex;align-items:center;gap:8px;padding:9px 12px;cursor:pointer}",
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
      "#agent-ticker{display:flex;align-items:center;gap:9px;height:28px;padding:0 14px;margin-top:8px;"
        + "border-radius:99px;font-size:11.5px;color:var(--muted);cursor:pointer;overflow:hidden}",
      "#agent-ticker .ekg{width:6px;height:6px;border-radius:50%;background:var(--ok);flex:none;"
        + "box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 20%,transparent)}",
      "#agent-ticker .tk-txt{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:opacity .5s}",

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
      + '      <div class="s2" id="ag-s2">Ask, or just watch — your assistant is on watch.</div></div>'
      + '    <button class="deepbtn" id="ag-deepbtn" title="See the full dialogues the agent had with Claude">'
      + svg("bridge") + '<span id="ag-deepbtn-t">Claude dialogues</span></button>'
      + '    ' + brainBadgeHtml()
      + '  </div>'
      + '  <div id="agent-stream"></div>'
      + '  <div id="agent-ticker" title="Open the Pulse feed"><span class="ekg" id="ag-ekg"></span>'
      + '    <span class="tk-txt" id="ag-tick">On watch — nothing needs you right now</span></div>'
      + '</div>'
      + '<div id="agent-rail" class="glass" style="padding:11px">'
      + '  <div class="railtabs" role="tablist">'
      + '    <b data-rail="record" class="on" role="tab">' + svg("record") + 'Record<span class="udot"></span></b>'
      + '    <b data-rail="screen" role="tab">' + svg("screen") + 'Screen<span class="udot"></span></b>'
      + '    <b data-rail="pulse" role="tab">' + svg("pulse") + 'Pulse<span class="udot"></span></b>'
      + '  </div>'
      + '  <div class="railpane" data-rail="record" id="rail-record"><div class="railstub">Loading the Flight Recorder…</div></div>'
      + '  <div class="railpane" data-rail="screen" id="rail-screen" hidden><div class="railstub">Loading the Screen panel…</div></div>'
      + '  <div class="railpane" data-rail="pulse" id="rail-pulse" hidden><div class="railstub" id="ag-pulse-stub">The autonomous feed — what the agent did while you were away.</div></div>'
      + '</div>';
    stage.appendChild(v);

    // Claude-dialogue viewer overlay (display-only; read-only transcript list)
    var dm = document.createElement("div");
    dm.id = "ag-deep";
    dm.innerHTML =
      '<section class="dsheet glass">'
      + '<div class="dhead">' + svg("bridge") + 'Claude dialogues'
      + '<button class="dclose" id="ag-deep-x" aria-label="Close">' + svg("chevron") + '</button></div>'
      + '<div class="dsub">Every time the agent escalated to Claude — the exact question it asked and Claude\'s full response. Display only.</div>'
      + '<div class="dlist" id="ag-deep-list"><div class="railstub">No deep escalations yet.</div></div>'
      + '</section>';
    (document.body || document.documentElement).appendChild(dm);
    dm.addEventListener("click", function (e) { if (e.target === dm) closeDeep(); });
    var dx = $("ag-deep-x"); if (dx) dx.addEventListener("click", closeDeep);
    var db = $("ag-deepbtn"); if (db) db.addEventListener("click", openDeep);

    // rail tab switching (local, no view change)
    var tabs = v.querySelectorAll(".railtabs b");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener("click", function () {
        openRail(this.getAttribute("data-rail"));
      });
    }
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
    _parked = true;
    try { if (typeof window.renderChatSide === "function") window.renderChatSide(); } catch (e) {}
    try { mountRecordRail(); } catch (e) {}          // default rail = the flight recorder
    startAgentPolls();
  }

  function exitAgent() {
    if (!_parked) return;
    // return the chat DOM to the deck at its exact original position so Hub's
    // split-chat (deck[data-chat]) is byte-for-byte what it was.
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
    _parked = false;
    stopAgentPolls();
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
        return;
      }
      // leaving Agent (or never in it): tear down our view, then delegate
      var va2 = $("view-agent"); if (va2) va2.hidden = true;
      var ta2 = $("tab-agent");
      if (ta2) { ta2.classList.remove("on"); ta2.setAttribute("aria-selected", "false"); }
      exitAgent();
      if (typeof prev === "function") prev(v);
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
  function tickSigil() {                    // one 12deg spring tick per tool event
    var h = $("agent-hero"); if (!h || REDUCE) return;
    var ring = h.querySelector(".sig-ring");
    if (!ring) return;
    h.__tick = ((h.__tick || 0) + 12) % 360;
    try { agAnimate(ring, { rotate: [h.__tick - 12 + "deg", h.__tick + "deg"] }, { duration: 0.4, easing: SPRING }); } catch (e) {}
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
      else { setSigil("idle"); setStateLine("Watching", idleDetail()); }
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
  function idleDetail() {
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
    // status arrives as "using <name>" (hermes_rpc) or a free status string.
    var m = String(txt).match(/using\s+([a-z0-9_.\-]+)/i);
    var name = m ? m[1].toLowerCase() : String(txt).toLowerCase();
    return TOOL_MAP_fn(name);
  }
  function TOOL_MAP_fn(name) {
    if (/shell|bash|terminal|command|\bexec\b|run_/.test(name)) return "terminal";
    if (/web_?search|websearch|search|\bweb\b|browse/.test(name)) return "web_search";
    if (/read_file|write_file|edit_file|str_replace|patch|apply|create_file|\bfile\b/.test(name)) return "file";
    if (/computer_use|computer|screenshot|click|cua/.test(name)) return "computer_use";
    if (/skill|invoke_skill/.test(name)) return "skill";
    if (/memor|remember|recall/.test(name)) return "memory";
    if (/delegate|sub_?agent|subagent|\btask\b/.test(name)) return "delegate";
    if (/claude|bridge|escalat|deep_think|think/.test(name)) return "claude-bridge";
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

    // spring enter + one head-icon tick (fade only under reduced motion)
    if (!REDUCE) {
      try {
        agAnimate(card.el, { opacity: [0, 1], transform: ["translateY(8px)", "none"] },
          { duration: 0.42, easing: SPRING });
        var ic = card.el.querySelector(".showin-head .ic");
        if (ic) agAnimate(ic, { rotate: ["0deg", "360deg"] }, { duration: 0.5, easing: SPRING });
      } catch (e) {}
    }
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

  function blipEkg() {
    var d = $("ag-ekg"); if (!d || REDUCE) return;
    try { agAnimate(d, { transform: ["scale(1)", "scale(1.5)", "scale(1)"] }, { duration: 0.3, easing: SPRING }); } catch (e) {}
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
    var next = _tickFacts[_tickIdx];
    if (REDUCE) { el.textContent = next; return; }
    el.style.opacity = "0";
    setTimeout(function () { el.textContent = next; el.style.opacity = "1"; }, 260);
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
        try { window.loadRecorder(); } catch (e) {}
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
      pane.innerHTML = window.renderDesktop(st || {}, tl || {}, full);
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
  function renderDeepList() {
    var list = $("ag-deep-list"); if (!list) return;
    var calls = window.__claudeRecent || [];
    if (!calls.length) { list.innerHTML = '<div class="railstub">No deep escalations yet. When the agent asks Claude to reason, the full exchange appears here.</div>'; return; }
    var h = "";
    for (var i = 0; i < calls.length; i++) {
      var c = calls[i] || {};
      var bad = c.ok === false || c.error;
      var task = (c.task || "").trim() || "(no task)";
      var resp = bad ? (c.error || c.response || "Claude did not answer.") : (c.response || "(empty response)");
      var secs = c.ms ? (c.ms / 1000).toFixed(c.ms < 10000 ? 1 : 0) + "s" : "";
      h += '<div class="dcall' + (bad ? " err" : "") + '" data-i="' + i + '">'
        + '<div class="dc-head">'
        + '<span class="dc-task">' + esc(task) + '</span>'
        + '<span class="dc-model">' + esc(c.model || c.depth || "claude") + '</span>'
        + '<span class="dc-meta">' + esc([t12(c.ts), secs].filter(Boolean).join(" · ")) + '</span>'
        + '</div>'
        + '<div class="dc-body">'
        + '<div class="dc-k">Asked Claude</div><div class="dc-q">' + esc(task) + '</div>'
        + '<div class="dc-k">' + (bad ? "Result" : "Claude · " + esc(c.depth || "deep")) + '</div><div class="dc-a">' + esc(resp) + '</div>'
        + '</div></div>';
    }
    list.innerHTML = h;
    var rows = list.querySelectorAll(".dcall");
    for (var j = 0; j < rows.length; j++) {
      rows[j].querySelector(".dc-head").addEventListener("click", function () { this.parentNode.classList.toggle("open"); });
    }
  }
  function openDeep() {
    var dm = $("ag-deep"); if (!dm) return;
    renderDeepList();
    dm.classList.add("open");
    loadClaudeRecent();
    if (!REDUCE) { try { agAnimate(dm.querySelector(".dsheet"), { opacity: [0, 1], transform: ["translateY(10px)", "none"] }, { duration: 0.4, easing: SPRING }); } catch (e) {} }
  }
  function closeDeep() { var dm = $("ag-deep"); if (dm) dm.classList.remove("open"); }

  // ==========================================================================
  // On-demand "Escalate to Claude" on a normal bot reply (display/answer-only)
  // ==========================================================================
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
    setSigil("idle"); setStateLine("Watching", idleDetail());
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
    setSigil("idle"); setStateLine("Watching", idleDetail());
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
    addTab();
    wrapSetView();
    wrapAgentState();
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
    decorateEscalate: decorateEscalate, doEscalate: doEscalate, t12: t12
  };
})();
