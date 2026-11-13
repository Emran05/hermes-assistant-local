// aux_onboarding.js — first-run setup sheet (1.1.1).
//
// Backend: aux_onboarding.py (/api/onboarding/state|apply|done|reset).
//
// WHAT THIS IS. A full-viewport sheet that opens once, on the first launch,
// and does the three things a local assistant owes a new user before it is
// useful: say what leaves the Mac, look at the Mac and recommend a brain that
// actually fits it, and set the handful of preferences that otherwise sit
// buried in Settings. Four steps, one accent, no marketing.
//
// DESIGN DIRECTION. Quiet and native — this is a trust product, so the sheet
// reads like a system dialog, not a product tour. The memorable detail is
// deliberately step 1: a table of everything that ever leaves this Mac, shown
// BEFORE anything is asked for. A product whose whole argument is sovereignty
// should make that argument in the first viewport instead of claiming it in a
// headline. That table is generated from ONE constant list (NETWORK_FACTS) by
// ONE reusable function (networkTableHTML) precisely because it is the seed of
// the future Data & Network panel (purpose-and-direction §4.7) — when that
// panel lands it imports the function, not a copy of the markup.
//
// THEMING. This file mounts into index.html's own document, so `--ground`,
// `--ink`, `--iris` and friends resolve from the :root palette that index.html
// already re-declares in all four blocks (light, @media dark, [data-theme=
// light], [data-theme=dark]). There is therefore NOTHING to re-declare here and
// re-declaring would be a bug — a fifth copy of the palette is a fifth thing to
// forget when a token changes. VERIFIED headless in both themes; the sheet
// paints var(--ground) and inverts with the toggle. The only colours written
// literally are inside the fallbacks of var(--x, fallback), for the case where
// this file is evaluated outside index.html.
//
// ESC. On a FIRST RUN Esc does nothing until the final step: the setup exists
// because the defaults are not obvious, and a stray keypress on step 1 should
// not silently skip it. Every re-run (Settings -> "Run setup again") is
// dismissable from anywhere, because then the user chose to open it.
//
// EXTRA STEPS (round 3). Other modules add their own optional steps — the
// GitHub connect step is the first — without editing this file: they push an
// entry onto window.hermesOnboardingSteps (before or after this file loads;
// the array is adopted, never replaced) or call
// hermesOnboarding.registerStep(entry). Entries are spliced in after "prefs"
// and before "done", in registration order (a later entry with the same key
// replaces the earlier one in place), so "Step n of m" counts them. An entry
// renders its own body and saves itself; anything it throws is caught, logged
// once, and costs only that step ("This step could not be shown."), never the
// sheet. Every extra step can be skipped. Contract: the ENTRY comment below.
//
// MODELS STEP (round 3, model advisor). When window.hermesAdvisor.step exists
// (aux_advisor.js) it renders the step's body — detected facts, the
// questionnaire, the recommended plan and its alternatives — and this file
// keeps the action row under it. The advisor hands its pick over through
// ctx.setPick, which repaints only that row ("Download recommended (~17 GB)").
// Without the advisor, or when its render throws, the legacy body below
// renders unchanged. Either way Next posts the picks to /api/onboarding/apply
// exactly as before; no model is switched, downloaded or woken by a render.
//
// MOTION (round 3, motion-system §8.9) goes through HA() / window.HermesAnim
// only: the sheet fades and settles in when it opens, and ONLY a step change
// animates the step (a GSAP sequence on the column and the new rail dot, the
// heading's words, and a count-up of the figures on the Ready step). A
// toggle, a busy state or the 2.5 s download poll repaints with no motion at
// all (E12: the old CSS keyframe entrance replayed on every repaint). Both
// reduce switches, a hidden window or no runtime land the end state at once.
//
// DESIGN LAWS (CLAUDE.md): zero emoji (bespoke two-tone SVG), esc() on every
// interpolation, every global helper typeof-guarded so a headless harness can
// eval this file, explicit transition-property, >=40px hit areas,
// prefers-reduced-motion respected.
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

  var SHEET_ID = "onb-sheet";
  var CARD_ID = "mind-extra-onboarding";
  var STEPS = [
    { key: "welcome", title: "Hermes runs on this Mac",
      sub: "What that means, and the short list of things that ever leave it" },
    { key: "models", title: "Your Mac, and the brain that fits it",
      sub: "Detected automatically — nothing here was asked of you" },
    { key: "prefs", title: "How it should behave",
      sub: "All of this is changeable later in Settings" },
    { key: "done", title: "Ready",
      sub: "Here is what was set" }
  ];

  // ==========================================================================
  // ENTRY — the extra-steps hook (round-3 shared contract 1).
  //
  //   window.hermesOnboardingSteps   an ARRAY of entries. Push onto it before
  //                                  or after this file loads; it is adopted
  //                                  as is and never reassigned.
  //   hermesOnboarding.registerStep(entry)
  //                                  pushes (or replaces, by key) and repaints
  //                                  an open sheet. Returns false when refused.
  //
  //   entry = { key        /^[a-z][a-z0-9_-]{1,23}$/, not a built-in step
  //             title, sub plain text (escaped here), eyebrow? plain text
  //             after      "prefs" (v1's only slot: after prefs, before done)
  //             optional   true (v1: every extra step has its own Skip)
  //             render(ctx) -> html     the entry escapes its own text
  //             wire(node, ctx)          node = this step's .onb-body element;
  //                                      listeners inside it only
  //             save?(ctx) -> Promise<{err?, applied?: string[]}>
  //                                      awaited by Next; err keeps the sheet
  //                                      on the step (Skip still goes on);
  //                                      applied lines reach the Ready step
  //             statusRow?(det) -> [cls, label, text] | null
  //                                      appended to the Connections status
  //                                      rows (statusRows), cls ok|wait|""
  //             summary?(ctx) -> string[] more lines for the Ready step }
  //   ctx = { rerun, data (the /api/onboarding/state payload),
  //           repaint() (re-render the current step), setErr(msg) }
  //
  // Anything an entry throws is caught and logged once per entry and phase.
  // ==========================================================================
  var BASE_KEYS = { welcome: 1, models: 1, prefs: 1, done: 1 };
  var KEY_RE = /^[a-z][a-z0-9_-]{1,23}$/;
  var REG = null;
  try { REG = Array.isArray(W.hermesOnboardingSteps) ? W.hermesOnboardingSteps : null; } catch (e) {}
  if (!REG) { REG = []; try { W.hermesOnboardingSteps = REG; } catch (e) {} }
  var SAVE_MS = 20000;            // an entry's save() that never answers must not hold Next

  var WARNED = {};
  function warnOnce(k, msg, err) {
    if (WARNED[k]) return;
    WARNED[k] = 1;
    try {
      if (typeof console !== "undefined" && console.warn) {
        console.warn("onboarding: " + msg + (err ? " (" + (err && err.message ? err.message : err) + ")" : ""));
      }
    } catch (e) {}
  }

  function entryProblem(e) {
    if (!e || typeof e !== "object") return "not an object";
    if (typeof e.key !== "string" || !KEY_RE.test(e.key)) return "bad key";
    if (BASE_KEYS[e.key] === 1) return "key " + e.key + " is a built-in step";
    if (e.after !== undefined && e.after !== "prefs") return "after must be \"prefs\"";
    if (typeof e.title !== "string" || !e.title.trim()) return "no title";
    if (typeof e.render !== "function") return "render is not a function";
    return "";
  }

  // the valid entries, in registration order; a later entry with the same key
  // takes the earlier one's place
  function extras() {
    var out = [], at = {};
    for (var i = 0; i < REG.length; i++) {
      var e = REG[i], why;
      try { why = entryProblem(e); } catch (x) { why = "unreadable"; }
      if (why) {
        var k = "";
        try { k = String(e && e.key); } catch (x) {}
        warnOnce("bad:" + k + ":" + why, "an extra step was ignored: " + why);
        continue;
      }
      if (at[e.key] !== undefined) out[at[e.key]] = e;
      else { at[e.key] = out.length; out.push(e); }
    }
    return out;
  }

  function buildSteps() {
    var list = STEPS.filter(function (s) { return s.key !== "done"; });
    extras().forEach(function (e) {
      list.push({ key: e.key, title: e.title.trim(),
                  sub: typeof e.sub === "string" ? e.sub : "",
                  eyebrow: typeof e.eyebrow === "string" && e.eyebrow.trim() ? e.eyebrow.trim() : "Optional",
                  ext: e });
    });
    list.push(STEPS[STEPS.length - 1]);
    return list;
  }

  function registerStep(entry) {
    var why;
    try { why = entryProblem(entry); } catch (x) { why = "unreadable"; }
    if (why) { warnOnce("reg:" + why, "registerStep refused an entry: " + why); return false; }
    // the LAST entry with this key is the one extras() shows: replace that one
    var at = -1;
    for (var i = REG.length - 1; i >= 0; i--) {
      var k = null;
      try { k = REG[i] && REG[i].key; } catch (x) {}
      if (k === entry.key) { at = i; break; }
    }
    if (at >= 0) REG[at] = entry; else REG.push(entry);
    // the live list follows at once; an open sheet stays on the step it shows
    var cur = stepKey();
    S.steps = buildSteps();
    var idx = -1;
    for (var j = 0; j < S.steps.length; j++) if (S.steps[j].key === cur) { idx = j; break; }
    S.step = idx >= 0 ? idx : Math.min(S.step, S.steps.length - 1);
    if (S.open) paint();
    return true;
  }

  // ==========================================================================
  // NETWORK_FACTS — the single source of truth for "what leaves this Mac".
  //
  // This list is the seed of the Data & Network panel. Each row is a real
  // egress path in the shipping code, not a category:
  //   what    the destination, named
  //   when    the trigger, in the user's terms
  //   data    what is actually in the request
  //   toggle  the EXISTING switch that turns it off, or null when there is
  //           none yet (a null is a promise to add one, and reads as "always").
  //           `def: false` marks a switch that is OFF unless the owner turned
  //           it on: it renders On only for an explicit true, so an unknown
  //           or unread state never shows as On.
  //   off     (toggle null only) how to stop it when that is not a switch on
  //           this screen — shown in the Switch column instead of "always".
  // Adding a network call to Hermes means adding a row here. C52 (2026-10-21
  // audit) re-measured every row against the code: the table had five rows
  // while the Mac talked to a dozen destinations. R2-43 re-measured them
  // again after the merge: the News switch now stops the feed gather (C48),
  // the unattended web research has its own switch, off by default (C50), a
  // fully downloaded model starts offline (C49) and the Hub refreshes only
  // while someone is looking (C53). t_r2_43_network_gates.js checks these
  // rows against the watchtower / launcher code so they cannot drift again.
  // ==========================================================================
  var NETWORK_FACTS = [
    { id: "updates", what: "GitHub",
      when: "A minute after the dashboard starts, then every 6 hours, and when " +
        "you press Check",
      data: "Asks for this app's newest release. No usage data, but a private " +
        "repo is read with your GitHub token (gh auth token, GITHUB_TOKEN) or " +
        "with git and your own git credentials. The key you connect for " +
        "your own repositories is never used for this.", toggle: null },
    { id: "github_repos", what: "GitHub (repositories you connect)",
      when: "Only if you connect GitHub: when you check a key or add a " +
        "repository, when a sandbox is prepared or refreshed, when Hermes " +
        "requests a change (it re-reads the branch from GitHub first), and every 6 " +
        "hours a read-only check of each connected repository's branch " +
        "rules, workflows, the key's expiry and its hermes/ branches",
      data: "Your GitHub key, to read those repositories. Their code comes " +
        "down to this Mac. Checking a key also writes tiny unreferenced test " +
        "objects (“hermes-probe”) to the repositories you connect, and tries " +
        "the same on a few public repositories you did not connect (up to " +
        "three per account, for at most two accounts): that try must fail, " +
        "and if it lands the key reaches too much and is refused. GitHub " +
        "discards these objects. Revoke now sends the key to " +
        "GitHub's revocation service. Nothing else of yours goes up. If " +
        "the 6-hourly check finds a hermes/ branch Hermes did not write, " +
        "Telegram tells you once (repository and branch names), even with " +
        "both notification switches off: it is a security notice.",
      toggle: null, off: "Disconnect GitHub" },
    { id: "github_push", what: "GitHub (changes you approve)",
      when: "Only when you approve a change in the dashboard",
      data: "One commit to a hermes/ branch and, if you chose it, a pull " +
        "request with the title and description shown on the approval card. " +
        "Never to your default branch; nothing is merged. Telegram then " +
        "tells you what was pushed. The pull request ends with one line " +
        "naming this Mac (its host name), the time you approved and the " +
        "change id. If you press Retract, Hermes closes that pull request and " +
        "deletes that hermes/ branch, only while nobody else has moved it. " +
        "The Telegram notice is a security notice: it is sent even with both " +
        "notification switches off and in quiet hours.",
      toggle: null, off: "Pause GitHub writes" },
    { id: "models", what: "Hugging Face",
      when: "When you download a model, and when a model server starts while " +
        "some of that model's files are still missing. A model that is fully " +
        "on this Mac starts offline and is never checked for changes. Also " +
        "when you search or open a model in Browse Hugging Face (Settings › " +
        "Agent & Models)",
      data: "The model's name, or the words you search for. No account or " +
        "token; nothing else of yours.", toggle: null },
    { id: "feeds", what: "News sites",
      when: "About every hour while News is on (the same switch as news " +
        "alerts). With News off, no feed is fetched",
      data: "A plain request to each of about 22 feeds (AI labs, tech press, " +
        "Reddit, world news, and any you add), plus Hacker News and GitHub " +
        "trending for the briefs. Nothing of yours.",
      toggle: { key: "news", label: "News: feeds and alerts" } },
    { id: "widgets", what: "Widget sources",
      when: "While that widget is on your Hub: once when the dashboard starts, " +
        "then only while a window is showing the Hub. Weather is also fetched " +
        "for the header's weather chip, on every view, and for the briefing's " +
        "look-ahead, whenever a city is set",
      data: "Your city to Open-Meteo for weather; your tickers and coins to " +
        "Yahoo Finance and CoinGecko; plain requests to Hacker News, GitHub " +
        "and the news widgets' feeds.",
      toggle: null, off: "Remove the widget; clear the city to stop weather" },
    { id: "websearch", what: "Web search",
      when: "When the agent searches the web for something you asked it, in " +
        "a chat, Quick Ask or Telegram",
      data: "The search words the model chose, to DuckDuckGo and the other " +
        "engines its web tool uses.", toggle: null },
    { id: "webresearch", what: "Web search, unattended",
      when: "Only while Agent web research is on (it is off until you turn it " +
        "on) and News is on: a news-research pass about every hour while a " +
        "model is awake, and a test search every 6 hours, model or not, that " +
        "checks web search still works",
      data: "Search words the model chose for AI-lab and AI-community news, " +
        "and the test search “AI news”, to the same engines.",
      toggle: { key: "web_research", label: "Agent web research", def: false } },
    // E25 (2026-11-09 audit): the widest path had no row. The agent's own tools
    // reach the internet directly, and nothing on this screen is a switch for
    // them: Hermes' approval rules ask about commands it classes as risky
    // (`curl … | sh`), never about a plain `curl`.
    { id: "agent_tools", what: "Any site the agent's tools reach",
      when: "Only inside a turn (a chat, Quick Ask or Telegram), when the agent " +
        "decides to use a tool: a command in its terminal (curl, git, pip, any " +
        "CLI — the claude CLI included), its browser tools, web extract (page " +
        "fetch)",
      data: "Whatever that command or tool sends: an address the model chose, " +
        "which can carry anything the agent has seen in the conversation. A " +
        "plain curl is not asked about; Hermes asks only before commands it " +
        "classes as risky.",
      // `off` sits in the nowrap Switch column: keep it as short as the
      // other rows' or the Settings card's table overflows its ~700 px panel
      // and scrolls the Switch column out of view (CLAUDE.md "Table fit")
      toggle: null,
      off: "No switch: toolsets, risky-command approval" },
    { id: "telegram", what: "Telegram",
      when: "Around the clock while the gateway runs: it stays connected so " +
        "your bot can answer",
      data: "Your messages to your own bot and its replies, which can quote " +
        "anything the assistant can see.", toggle: null,
      off: "hermes gateway stop" },
    { id: "briefings", what: "Telegram briefings",
      when: "Morning brief, midday pulse and evening wrap",
      data: "Today's calendar, your open tasks, For-You picks and the people " +
        "they name, headlines, and a Needs-you count. Just before the " +
        "morning brief the dashboard also refreshes your tickers and coins " +
        "at Yahoo Finance and CoinGecko, widgets or not.",
      toggle: { key: "briefings", label: "Briefings and daily wrap" } },
    { id: "alerts", what: "Telegram alerts",
      when: "When a breaking story or one of your watch rules fires. While News " +
        "is on and a price rule exists, the rules also check Yahoo Finance " +
        "(tickers, indices) and CoinGecko (coins) every 5 minutes, around the " +
        "clock, widgets or not",
      data: "A headline and link, or the rule's line (a price move, a system " +
        "warning). The price checks send your rules' tickers and coins, " +
        "nothing else. Price and system rules have their own switches in " +
        "Watchtower.",
      toggle: { key: "news", label: "News and breaking alerts" } },
    { id: "claude", what: "Claude (Anthropic)",
      when: "When you press Escalate, when the auto-router picks a hard " +
        "question, or when the agent asks Claude through the bridge",
      data: "The question, plus any context the caller adds — the agent's " +
        "think-with-claude skill may add goals from your You-Model. Claude " +
        "runs with no tools, so it cannot read anything else. This switch " +
        "covers the bridge only: it does not stop the agent running the " +
        "claude CLI itself in its terminal (the agent's tools row).",
      toggle: { key: "claude_escalation", label: "Claude escalation", def: false } },
    { id: "foryou_claude", what: "Claude (Anthropic), for For-You",
      when: "About every 2 hours, when new news arrives, and when you " +
        "refresh For-You, only while Claude escalation is on too",
      data: "Your You-Model profile (goals, current projects, what you are " +
        "looking for, interests, preferences, key people's names) and up to " +
        "15 headlines it matched.",
      toggle: { key: "foryou_claude", label: "For-You via Claude", def: false } },
    { id: "google", what: "Google", when: "Only if you connect an account: " +
        "today's calendar every 5 minutes, plus token refreshes",
      data: "Read-only calendar, mail and contacts. The dashboard reads the " +
        "calendar; the agent's Google skill can read mail and contacts with " +
        "the same grant. Nothing can be sent or changed.", toggle: null,
      off: "Disconnect Google" },
    { id: "tts", what: "Microsoft Edge voices",
      when: "Only if the agent uses its text-to-speech tool: Hermes defaults " +
        "to Edge's online voices unless config.yaml names a local one",
      data: "The text to be spoken.", toggle: null,
      off: "Set a local voice" },
    { id: "dictation", what: "Apple (speech model)",
      when: "Once per language, the first time you dictate: macOS downloads " +
        "its on-device speech model",
      data: "A request for the model files. Your voice is transcribed on " +
        "this Mac.", toggle: null, off: "Do not dictate" }
  ];

  // Everything else — chat, tools, memory, search, the model itself — is local.
  var LOCAL_LINES = [
    "The model that answers you runs on this Mac, and there is no account to " +
      "sign in to. Your conversations, notes, messages, files and You-Model stay " +
      "here, except for what a row below says it sends.",
    "The assistant asks before it does anything irreversible, and the Flight " +
      "Recorder keeps an undo trail of what it did.",
    "These are the " + NETWORK_FACTS.length + " things that can reach the " +
      "internet, the agent's own tools included (they go wherever a command " +
      "sends them). The ones with a switch can be turned off on this screen."
  ];

  // ---- glyphs (two-tone, accent fill + currentColor stroke; zero emoji) -----
  var G = {
    shield: '<path d="M12 3 4.5 6.2v5.4c0 4.4 3 8.2 7.5 9.4 4.5-1.2 7.5-5 7.5-9.4V6.2Z" ' +
      'fill="var(--iris,#5B63E6)" opacity=".16"/>' +
      '<path d="M12 3 4.5 6.2v5.4c0 4.4 3 8.2 7.5 9.4 4.5-1.2 7.5-5 7.5-9.4V6.2Z" ' +
      'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>' +
      '<path d="M8.8 11.9 11.2 14.3 15.4 10" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    chip: '<rect x="6" y="6" width="12" height="12" rx="2.4" fill="var(--iris,#5B63E6)" ' +
      'opacity=".18" stroke="currentColor" stroke-width="1.5"/>' +
      '<rect x="9.6" y="9.6" width="4.8" height="4.8" rx="1.2" fill="currentColor"/>' +
      '<path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" fill="none" ' +
      'stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    sliders: '<path d="M4 8h10M18 8h2M4 16h4M12 16h8" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round"/>' +
      '<circle cx="16" cy="8" r="2.4" fill="var(--iris,#5B63E6)" opacity=".25" ' +
      'stroke="currentColor" stroke-width="1.5"/>' +
      '<circle cx="10" cy="16" r="2.4" fill="var(--iris,#5B63E6)" opacity=".25" ' +
      'stroke="currentColor" stroke-width="1.5"/>',
    check: '<circle cx="12" cy="12" r="9" fill="var(--iris,#5B63E6)" opacity=".16"/>' +
      '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.5"/>' +
      '<path d="M8.2 12.2 10.9 15 15.9 9.4" fill="none" stroke="currentColor" ' +
      'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    // an extra step (another module's): a tile being added to the set
    extra: '<rect x="4" y="4" width="16" height="16" rx="4" fill="var(--iris,#5B63E6)" ' +
      'opacity=".16"/>' +
      '<rect x="4" y="4" width="16" height="16" rx="4" fill="none" stroke="currentColor" ' +
      'stroke-width="1.5"/>' +
      '<path d="M12 8.6v6.8M8.6 12h6.8" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round"/>',
    dot: '<circle cx="12" cy="12" r="4" fill="currentColor"/>'
  };

  function svg(path, size) {
    size = size || 16;
    return '<svg class="onb-ic" viewBox="0 0 24 24" width="' + size + '" height="' +
      size + '" aria-hidden="true" focusable="false">' + path + "</svg>";
  }

  // ==========================================================================
  // pure helpers (exported for the headless harness)
  // ==========================================================================

  // "16.1" -> "16.1 GB"; whole numbers lose the .0 ("23.0" -> "23 GB")
  function gb(n) {
    var v = Number(n || 0);
    if (!v) return "0 GB";
    return (Math.round(v * 10) % 10 === 0 ? String(Math.round(v)) : v.toFixed(1)) + " GB";
  }

  // the 1.0.3 fit sentence, verbatim in shape: "needs ~19 GB · this Mac has 64 GB"
  // Same three states and the same tokens as the model menu's .mmfit, so a fit
  // badge means exactly what it means there.
  function fitLine(ram, machineGb, fit) {
    if (!fit || !ram || !machineGb) return "";
    return "needs ~" + ram + " GB · this Mac has " + machineGb + " GB" +
      (fit === "no" ? " · too big to run here"
        : fit === "tight" ? " · tight, expect swapping" : "");
  }

  function fitClass(fit) {
    return "onb-fit" + (fit === "no" ? " bad" : fit === "tight" ? " warn" : "");
  }

  // "Apple M5 Max · 64 GB · 412 GB free · macOS 26.1 · laptop"
  function sysLine(si) {
    si = si || {};
    var bits = [];
    if (si.chip) bits.push(si.chip);
    if (si.ram_gb) bits.push(si.ram_gb + " GB memory");
    if (si.disk_free_gb != null) bits.push(gb(si.disk_free_gb) + " free");
    if (si.macos) bits.push("macOS " + si.macos);
    bits.push(si.laptop ? "laptop" : "desktop");
    return bits.join(" · ");
  }

  // ==========================================================================
  // netCSS(scope) — the network table's styling, for ONE selector.
  //
  // The sheet's CSS() emits netCSS("#onb-sheet"); the Data & Network card
  // (aux_network.js, 1.1.3) emits netCSS("#mind-extra-network"). Same rules,
  // one source: the table markup and the table's look drift together or not at
  // all. Every colour is an index.html token with a light-theme literal
  // fallback, so both callers are theme-correct without a dark-mode rule.
  //
  // The switch rules ride along because the table's live toggles ARE .onb-sw —
  // a caller that gets the markup and not the switch would render a checkbox.
  // ==========================================================================
  function netCSS(scope) {
    var S_ = scope;
    return S_ + ' .onb-tablewrap{overflow-x:auto;margin:0 0 4px}' +
      S_ + ' table.onb-net{width:100%;border-collapse:collapse;font-size:12.5px;' +
        'min-width:520px}' +
      S_ + ' table.onb-net caption{text-align:left;font-size:11px;color:var(--faint,#868DA1);' +
        'padding:0 0 8px}' +
      S_ + ' table.onb-net th,' + S_ + ' table.onb-net td{text-align:left;vertical-align:top;' +
        'padding:9px 12px 9px 0;border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' table.onb-net thead th{font-size:10px;font-weight:700;letter-spacing:.07em;' +
        'text-transform:uppercase;color:var(--faint,#868DA1);padding-top:0}' +
      S_ + ' table.onb-net tbody th{font-weight:620;white-space:nowrap;padding-right:16px}' +
      S_ + ' .onb-net-when{color:var(--muted,#565E72);text-wrap:pretty;min-width:150px}' +
      S_ + ' .onb-net-data{color:var(--muted,#565E72);text-wrap:pretty;min-width:170px}' +
      S_ + ' .onb-net-ctl{white-space:nowrap;padding-right:0}' +
      S_ + ' .onb-net-always{font-size:11px;color:var(--faint,#868DA1)}' +
      // a switchless row's how-to wraps (V01): one nowrap line used to set
      // the Switch column to 272px and push the table into a sideways scroll
      S_ + ' .onb-net-ctl .onb-net-always{display:inline-block;white-space:normal;' +
        'min-width:8em;max-width:11em}' +
      // "Last outbound" — a timestamp, so tabular-nums; quiet, because it is
      // evidence, not a status anyone has to act on.
      S_ + ' .onb-net-last{color:var(--faint,#868DA1);white-space:nowrap;font-size:11px;' +
        'font-variant-numeric:tabular-nums}' +
      S_ + ' table.onb-net tbody tr:last-child th,' +
        S_ + ' table.onb-net tbody tr:last-child td{border-bottom:0}' +
      // ---- switch ---------------------------------------------------------
      S_ + ' .onb-sw{position:relative;display:inline-flex;align-items:center;gap:8px;' +
        'min-height:40px;cursor:pointer;-webkit-user-select:none;user-select:none}' +
      S_ + ' .onb-sw input{position:absolute;opacity:0;width:0;height:0;margin:0}' +
      S_ + ' .onb-sw-t{position:relative;flex:0 0 auto;width:36px;height:21px;border-radius:11px;' +
        'background:color-mix(in srgb,var(--ink,#10131D) 16%,transparent);' +
        'transition-property:background-color;transition-duration:170ms;' +
        'transition-timing-function:ease-out}' +
      S_ + ' .onb-sw-t::after{content:"";position:absolute;top:2.5px;left:2.5px;width:16px;' +
        'height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.28);' +
        'transition-property:transform;transition-duration:170ms;' +
        'transition-timing-function:cubic-bezier(.2,.8,.3,1)}' +
      S_ + ' .onb-sw input:checked + .onb-sw-t{background:var(--iris,#5B63E6)}' +
      S_ + ' .onb-sw input:checked + .onb-sw-t::after{transform:translateX(15px)}' +
      S_ + ' .onb-sw input:focus-visible + .onb-sw-t{outline:2px solid var(--iris,#5B63E6);' +
        'outline-offset:2px}' +
      S_ + ' .onb-sw-l{font-size:11.5px;color:var(--muted,#565E72);min-width:22px}' +
      S_ + ' .onb-sw.is-busy{opacity:.5;pointer-events:none}';
  }

  // ==========================================================================
  // THE NETWORK TABLE — one function, reused by the Data & Network panel.
  //
  // opts.prefs  current pref values, so a row's toggle shows its real state
  // opts.inputs when true, render the toggles as live checkboxes (the sheet
  //             AND the panel); when false, render them as read-only state
  //             words. Everything else is identical.
  // opts.last   optional {factId: "already-formatted string"}. Present => a
  //             "Last outbound" column is added. The strings are formatted by
  //             the CALLER: this module owns what leaves the Mac, not a clock,
  //             and the panel's 12-hour formatter already exists next door.
  // opts.caption overrides the table caption (the panel says it in its own
  //             heading, so it passes "" to drop the duplicate).
  // ==========================================================================
  function networkTableHTML(opts) {
    opts = opts || {};
    var prefs = opts.prefs || {};
    var live = opts.inputs !== false;
    var last = opts.last || null;
    var rows = (opts.facts || NETWORK_FACTS).map(function (f) {
      var ctl;
      if (!f.toggle) {
        ctl = '<span class="onb-net-always">' + E(f.off || "always") + "</span>";
      } else {
        var on = f.toggle.def === false ? prefs[f.toggle.key] === true
                                        : prefs[f.toggle.key] !== false;
        if (live) {
          ctl = '<label class="onb-sw" title="' + E(f.toggle.label) + '">' +
            '<input type="checkbox" data-pref="' + E(f.toggle.key) + '"' +
            (on ? " checked" : "") + ' aria-label="' + E(f.toggle.label) + '">' +
            '<span class="onb-sw-t" aria-hidden="true"></span>' +
            '<span class="onb-sw-l">' + (on ? "On" : "Off") + "</span></label>";
        } else {
          ctl = '<span class="onb-net-always">' + (on ? "On" : "Off") + "</span>";
        }
      }
      return '<tr><th scope="row">' + E(f.what) + "</th>" +
        '<td class="onb-net-when">' + E(f.when) + "</td>" +
        '<td class="onb-net-data">' + E(f.data) + "</td>" +
        (last ? '<td class="onb-net-last">' + E(last[f.id] || "no record") + "</td>" : "") +
        '<td class="onb-net-ctl">' + ctl + "</td></tr>";
    }).join("");
    var cap = (opts.caption === undefined) ? "What can leave this Mac, and when" : opts.caption;
    return '<div class="onb-tablewrap"><table class="onb-net">' +
      (cap ? "<caption>" + E(cap) + "</caption>" : "") +
      '<thead><tr><th scope="col">Goes to</th><th scope="col">When</th>' +
      '<th scope="col">What</th>' +
      (last ? '<th scope="col" class="onb-net-lasth">Last outbound</th>' : "") +
      '<th scope="col">Switch</th></tr></thead>' +
      "<tbody>" + rows + "</tbody></table></div>";
  }

  // ==========================================================================
  // CSS. One <style>, scoped to #onb-sheet (plus the Settings row). All colour
  // comes from index.html's tokens — see the THEMING note in the header.
  // ==========================================================================
  function CSS() {
    var S_ = "#" + SHEET_ID;
    return '<style id="onb-css">' +
      // ---- the sheet ------------------------------------------------------
      S_ + '{position:fixed;inset:0;z-index:9000;display:flex;flex-direction:column;' +
        'background:var(--ground,#E7EAF3);color:var(--ink,#10131D);' +
        '-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;' +
        'font:14px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif}' +
      S_ + '[hidden]{display:none!important}' +
      // a very quiet ground wash so the flat panel is not literally flat
      S_ + '::before{content:"";position:absolute;inset:0;pointer-events:none;' +
        'background:radial-gradient(120% 60% at 50% -10%,' +
        'color-mix(in srgb,var(--iris,#5B63E6) 9%,transparent),transparent 70%)}' +
      S_ + ' *{box-sizing:border-box}' +
      // ---- progress rail --------------------------------------------------
      S_ + ' .onb-rail{position:relative;display:flex;gap:4px;padding:0;margin:0;' +
        'flex:0 0 auto;list-style:none}' +
      S_ + ' .onb-rail li{flex:1 1 0;height:3px;border-radius:0;' +
        'background:color-mix(in srgb,var(--ink,#10131D) 10%,transparent)}' +
      S_ + ' .onb-rail li.on{background:var(--iris,#5B63E6)}' +
      S_ + ' .onb-rail li{transition-property:background-color;transition-duration:260ms;' +
        'transition-timing-function:ease-out}' +
      // ---- scroll body ----------------------------------------------------
      S_ + ' .onb-scroll{position:relative;flex:1 1 auto;overflow-y:auto;overflow-x:hidden;' +
        'display:flex;justify-content:center;padding:34px 24px 26px}' +
      S_ + ' .onb-col{width:100%;max-width:680px}' +
      // ---- header ---------------------------------------------------------
      S_ + ' .onb-eyebrow{display:flex;align-items:center;gap:7px;margin:0 0 12px;' +
        'font-size:10.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;' +
        'color:var(--muted,#565E72)}' +
      S_ + ' .onb-ic{flex:0 0 auto;color:var(--muted,#565E72)}' +
      S_ + ' h1.onb-t{margin:0 0 6px;font-size:26px;line-height:1.18;font-weight:640;' +
        'letter-spacing:-.015em;text-wrap:balance}' +
      S_ + ' p.onb-s{margin:0 0 22px;font-size:13.5px;line-height:1.55;' +
        'color:var(--muted,#565E72);text-wrap:pretty}' +
      S_ + ' p.onb-p{margin:0 0 11px;font-size:13.5px;line-height:1.62;text-wrap:pretty}' +
      S_ + ' p.onb-p:last-of-type{margin-bottom:20px}' +
      // ---- section heads (no cards in cards: a rule + a label, not a box) --
      S_ + ' .onb-h{display:flex;align-items:baseline;gap:9px;margin:24px 0 10px;' +
        'padding-bottom:7px;border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' .onb-h h2{margin:0;font-size:12px;font-weight:680;letter-spacing:.02em}' +
      S_ + ' .onb-h span{font-size:11.5px;color:var(--muted,#565E72)}' +
      // ---- the network table + its switch (scoped copy, see netCSS) --------
      netCSS(S_) +
      // ---- stats row (step 2) ---------------------------------------------
      S_ + ' .onb-stats{display:flex;flex-wrap:wrap;gap:0 26px;margin:0 0 22px;' +
        'padding:0;list-style:none}' +
      S_ + ' .onb-stats li{padding:0 0 4px}' +
      S_ + ' .onb-stat-k{display:block;font-size:10px;font-weight:700;letter-spacing:.07em;' +
        'text-transform:uppercase;color:var(--faint,#868DA1);margin-bottom:2px}' +
      S_ + ' .onb-stat-v{display:block;font-size:14px;font-weight:600;' +
        'font-variant-numeric:tabular-nums}' +
      // ---- the recommendation ---------------------------------------------
      S_ + ' .onb-rec{margin:0 0 6px;padding:16px 17px;border-radius:var(--radius-sm,13px);' +
        'border:1px solid color-mix(in srgb,var(--iris,#5B63E6) 42%,transparent);' +
        'background:color-mix(in srgb,var(--iris,#5B63E6) 10%,transparent)}' +
      S_ + ' .onb-rec h3{margin:0 0 5px;font-size:15px;font-weight:640;letter-spacing:-.01em;' +
        'text-wrap:balance}' +
      S_ + ' .onb-rec .onb-why{margin:0 0 13px;font-size:12.5px;line-height:1.6;' +
        'color:var(--muted,#565E72);text-wrap:pretty}' +
      S_ + ' .onb-lane{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;' +
        'padding:8px 0;border-top:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' .onb-lane:first-of-type{border-top:0;padding-top:0}' +
      S_ + ' .onb-lane-r{font-size:10px;font-weight:700;letter-spacing:.07em;' +
        'text-transform:uppercase;color:var(--faint,#868DA1);width:88px;flex:0 0 auto}' +
      S_ + ' .onb-lane-n{font-size:13px;font-weight:620}' +
      S_ + ' .onb-lane-s{font-size:11.5px;color:var(--muted,#565E72);' +
        'font-variant-numeric:tabular-nums}' +
      S_ + ' .onb-fit{width:100%;margin-top:2px;font-size:10.5px;line-height:1.4;' +
        'color:var(--faint,#868DA1);font-variant-numeric:tabular-nums}' +
      S_ + ' .onb-fit.warn{color:var(--warn,#B9821A)}' +
      S_ + ' .onb-fit.bad{color:var(--bad,#D24C3C)}' +
      S_ + ' .onb-tag{font-size:9.5px;font-weight:700;letter-spacing:.06em;' +
        'text-transform:uppercase;padding:1px 6px;border-radius:20px;' +
        'color:var(--warn,#B9821A);' +
        'background:color-mix(in srgb,var(--warn,#B9821A) 16%,transparent)}' +
      S_ + ' .onb-total{margin:12px 0 0;font-size:12.5px;font-variant-numeric:tabular-nums}' +
      S_ + ' .onb-done-note{margin:12px 0 0;font-size:12.5px;color:var(--ok,#2E9E68)}' +
      // alternatives: a compact list, never a second card inside the first
      S_ + ' ul.onb-alts{margin:0;padding:0;list-style:none}' +
      S_ + ' ul.onb-alts li{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;' +
        'padding:9px 0;border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' ul.onb-alts li:last-child{border-bottom:0}' +
      S_ + ' ul.onb-alts .onb-alt-n{font-size:12.5px;font-weight:600;min-width:110px}' +
      S_ + ' ul.onb-alts .onb-alt-d{flex:1 1 190px;font-size:11.5px;' +
        'color:var(--muted,#565E72);text-wrap:pretty}' +
      S_ + ' ul.onb-alts .onb-alt-s{font-size:11.5px;color:var(--faint,#868DA1);' +
        'font-variant-numeric:tabular-nums;white-space:nowrap}' +
      // a failed download's reason wraps (E22) and its Retry sits at the end
      S_ + ' ul.onb-alts .onb-alt-f{white-space:normal;color:var(--bad,#D24C3C);' +
        'flex:1 1 190px;overflow-wrap:anywhere}' +
      S_ + ' ul.onb-alts li button.onb-b{margin-left:auto}' +
      S_ + ' .onb-dl-act{margin-top:10px}' +
      // ---- rows (preferences + status) ------------------------------------
      S_ + ' .onb-row{display:flex;align-items:center;gap:14px;flex-wrap:wrap;' +
        'min-height:48px;padding:8px 0;' +
        'border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' .onb-row:last-child{border-bottom:0}' +
      S_ + ' .onb-row-txt{flex:1 1 220px;min-width:0}' +
      S_ + ' .onb-row-l{display:block;font-size:13px;font-weight:600}' +
      S_ + ' .onb-row-d{display:block;margin-top:1px;font-size:11.5px;line-height:1.5;' +
        'color:var(--muted,#565E72);text-wrap:pretty}' +
      S_ + ' .onb-row-ctl{flex:0 0 auto;display:flex;align-items:center;gap:8px}' +
      // ---- segmented control (theme, sleep) -------------------------------
      S_ + ' .onb-seg{display:inline-flex;padding:2px;gap:2px;border-radius:10px;' +
        'border:1px solid var(--field-edge,rgba(16,19,29,.12));' +
        'background:var(--field,rgba(255,255,255,.55))}' +
      S_ + ' .onb-seg label{position:relative;display:flex;align-items:center;' +
        'justify-content:center;min-width:56px;min-height:34px;padding:0 11px;' +
        'border-radius:8px;font-size:12px;font-weight:560;cursor:pointer;' +
        'color:var(--muted,#565E72);' +
        'transition-property:background-color,color;transition-duration:150ms;' +
        'transition-timing-function:ease-out}' +
      // the visible pill is 34px tall; the pseudo-element makes the target 44px
      S_ + ' .onb-seg label::after{content:"";position:absolute;left:0;right:0;top:-5px;bottom:-5px}' +
      S_ + ' .onb-seg input{position:absolute;opacity:0;width:0;height:0;margin:0}' +
      S_ + ' .onb-seg label:hover{color:var(--ink,#10131D)}' +
      S_ + ' .onb-seg label:has(input:checked){background:var(--iris,#5B63E6);' +
        'color:var(--iris-ink,#fff)}' +
      S_ + ' .onb-seg label:has(input:focus-visible){outline:2px solid var(--iris,#5B63E6);' +
        'outline-offset:2px}' +
      // ---- time inputs ----------------------------------------------------
      S_ + ' .onb-time{min-height:36px;padding:6px 9px;border-radius:9px;font-size:12.5px;' +
        'color:inherit;font-variant-numeric:tabular-nums;' +
        'border:1px solid var(--field-edge,rgba(16,19,29,.12));' +
        'background:var(--field,rgba(255,255,255,.55))}' +
      S_ + ' .onb-time:focus-visible{outline:2px solid var(--iris,#5B63E6);outline-offset:1px}' +
      S_ + ' .onb-to{font-size:11.5px;color:var(--muted,#565E72)}' +
      // ---- status list (read-only, next step as text) ---------------------
      S_ + ' .onb-st{display:flex;align-items:flex-start;gap:10px;padding:10px 0;' +
        'border-bottom:1px solid var(--hairline,rgba(16,19,29,.10))}' +
      S_ + ' .onb-st:last-child{border-bottom:0}' +
      S_ + ' .onb-st-dot{flex:0 0 auto;width:7px;height:7px;margin-top:6px;border-radius:50%;' +
        'background:var(--faint,#868DA1)}' +
      S_ + ' .onb-st.ok .onb-st-dot{background:var(--ok,#2E9E68)}' +
      S_ + ' .onb-st.wait .onb-st-dot{background:var(--warn,#B9821A)}' +
      S_ + ' .onb-st-b{min-width:0}' +
      S_ + ' .onb-st-l{font-size:12.5px;font-weight:600}' +
      S_ + ' .onb-st-v{font-size:11.5px;color:var(--muted,#565E72);line-height:1.55;' +
        'text-wrap:pretty}' +
      S_ + ' .onb-st-v code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;' +
        'font-size:10.5px;padding:1px 4px;border-radius:5px;' +
        'background:color-mix(in srgb,var(--ink,#10131D) 8%,transparent)}' +
      // ---- summary (step 4) -----------------------------------------------
      S_ + ' ul.onb-sum{margin:0 0 20px;padding:0;list-style:none}' +
      S_ + ' ul.onb-sum li{position:relative;padding:6px 0 6px 20px;font-size:13px;' +
        'line-height:1.55;text-wrap:pretty}' +
      S_ + ' ul.onb-sum li::before{content:"";position:absolute;left:5px;top:.95em;width:5px;' +
        'height:5px;border-radius:50%;background:var(--iris,#5B63E6)}' +
      S_ + ' .onb-key{display:inline-flex;align-items:center;gap:2px;padding:2px 7px;' +
        'border-radius:6px;font-size:12px;font-weight:600;' +
        'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;' +
        'border:1px solid var(--hairline,rgba(16,19,29,.10));' +
        'background:var(--chip,rgba(255,255,255,.5))}' +
      // ---- footer ---------------------------------------------------------
      S_ + ' .onb-foot{flex:0 0 auto;display:flex;align-items:center;gap:10px;' +
        'padding:14px 24px;border-top:1px solid var(--hairline,rgba(16,19,29,.10));' +
        'background:color-mix(in srgb,var(--ground,#E7EAF3) 88%,transparent);' +
        '-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px)}' +
      S_ + ' .onb-foot-in{display:flex;align-items:center;gap:10px;width:100%;' +
        'max-width:680px;margin:0 auto}' +
      S_ + ' .onb-step-n{font-size:11.5px;color:var(--faint,#868DA1);' +
        'font-variant-numeric:tabular-nums;margin-right:auto}' +
      S_ + ' button.onb-b{min-height:40px;padding:0 17px;border-radius:10px;font-size:13px;' +
        'font-weight:560;cursor:pointer;color:inherit;' +
        'border:1px solid var(--field-edge,rgba(16,19,29,.12));' +
        'background:var(--field,rgba(255,255,255,.55));' +
        'transition-property:background-color,border-color,color,transform,opacity;' +
        'transition-duration:150ms;transition-timing-function:ease-out}' +
      S_ + ' button.onb-b:hover:not(:disabled){border-color:var(--iris,#5B63E6)}' +
      S_ + ' button.onb-b:active:not(:disabled){transform:scale(.97)}' +
      S_ + ' button.onb-b:focus-visible{outline:2px solid var(--iris,#5B63E6);outline-offset:2px}' +
      S_ + ' button.onb-b:disabled{opacity:.45;cursor:default}' +
      S_ + ' button.onb-go{border-color:transparent;background:var(--iris,#5B63E6);' +
        'color:var(--iris-ink,#fff);font-weight:620}' +
      S_ + ' button.onb-go:hover:not(:disabled){border-color:transparent;' +
        'background:var(--iris-2,#7A6BEF)}' +
      S_ + ' .onb-err{margin:12px 0 0;font-size:12px;line-height:1.55;color:var(--bad,#D24C3C);' +
        'text-wrap:pretty}' +
      S_ + ' p.onb-muted{color:var(--muted,#565E72)}' +
      // the Ready step's figures: count() fixes their width in ch while the
      // digits run, so they are boxes with tabular digits
      S_ + ' .onb-fig{display:inline-block;text-align:right;font-variant-numeric:tabular-nums}' +
      // ---- motion ---------------------------------------------------------
      // No CSS entrance: a step CHANGE plays through HA() (paint(), motion
      // §8.9). The old @keyframes on .onb-col replayed on every repaint (E12).
      '@media (prefers-reduced-motion:reduce){' +
        S_ + ' .onb-rail li,' + S_ + ' .onb-sw-t,' + S_ + ' .onb-sw-t::after,' +
        S_ + ' button.onb-b,' + S_ + ' .onb-seg label{transition-duration:1ms}' +
        S_ + ' button.onb-b:active:not(:disabled){transform:none}}' +
      // narrow windows: the footer stacks rather than clipping the primary action
      '@media (max-width:560px){' + S_ + ' .onb-scroll{padding:24px 16px 20px}' +
        S_ + ' h1.onb-t{font-size:22px}' +
        S_ + ' .onb-foot{padding:12px 16px}' +
        S_ + ' .onb-step-n{width:100%;margin:0 0 6px}}' +
      // ---- the Settings row ------------------------------------------------
      '#' + CARD_ID + ' .onb-set-row{display:flex;align-items:center;gap:14px;' +
        'flex-wrap:wrap;min-height:44px}' +
      '#' + CARD_ID + ' .onb-set-txt{flex:1 1 220px;min-width:0}' +
      '#' + CARD_ID + ' .onb-set-l{font-size:13px;font-weight:600}' +
      '#' + CARD_ID + ' .onb-set-when{white-space:nowrap}' +
      '#' + CARD_ID + ' .onb-set-d{margin-top:2px;font-size:11.5px;line-height:1.5;' +
        'color:var(--muted);text-wrap:pretty}' +
      '#' + CARD_ID + ' button.onb-set-b{min-height:40px;padding:0 15px;border-radius:10px;' +
        'font-size:12.5px;cursor:pointer;color:inherit;' +
        'border:1px solid var(--hairline,rgba(255,255,255,.12));' +
        'background:var(--glass-2,rgba(255,255,255,.05));' +
        'transition-property:background-color,border-color,transform;' +
        'transition-duration:150ms;transition-timing-function:ease-out}' +
      '#' + CARD_ID + ' button.onb-set-b:hover{border-color:var(--iris)}' +
      '#' + CARD_ID + ' button.onb-set-b:active{transform:scale(.97)}' +
      '@media (prefers-reduced-motion:reduce){' +
        '#' + CARD_ID + ' button.onb-set-b:active{transform:none}}' +
      "</style>";
  }

  // ==========================================================================
  // state
  // ==========================================================================
  var S = {
    open: false, rerun: false, step: 0,
    data: null,                 // last /api/onboarding/state
    prefs: null,                // working copy the sheet edits
    pick: null,                 // {primary, background, alt:bool}
    busy: "", err: "",
    dl: null,                   // {ids:[], done:{}, downloading:{}, started:bool}
    modelsLater: false,         // "I'll choose later" on the models step (C63)
    modelsApplied: null,        // apply's `applied` for the models step's picks
    applied: [],                // human lines for the summary
    lastFocus: null,
    steps: null,                // the live step list: STEPS + extra steps (buildSteps)
    extApplied: {},             // extra step key -> its save()'s `applied` lines
    broken: {},                 // extra step key -> its render threw on the last paint
    advMode: false,             // the models step's body came from hermesAdvisor.step
    shown: false,               // the sheet is on screen (its opening has played)
    painted: null,              // {key, idx}: the step the last paint drew
    footFocus: null,            // data-act of the action-row button that held focus across a disabled spell
    anim: []                    // the step-change handles still running
  };
  var dlTimer = null;
  var saveTok = 0;                // bumped by Skip/Back: a late save() result is dropped
  S.steps = buildSteps();

  function liveSteps() { return (S.steps && S.steps.length) ? S.steps : STEPS; }
  function curStep() { var L = liveSteps(); return L[S.step] || L[0]; }
  function stepKey() { return curStep().key; }
  function stepCount() { return liveSteps().length; }
  function lastStep() { return S.step === stepCount() - 1; }
  function stepDef(key) {
    for (var i = 0; i < STEPS.length; i++) if (STEPS[i].key === key) return STEPS[i];
    return STEPS[0];
  }

  // the context an extra step (and the advisor) is handed
  function hookCtx() {
    return {
      rerun: !!S.rerun,
      data: S.data || {},
      repaint: function () { if (S.open) paint(); },
      setErr: function (m) { setErr(m); }
    };
  }

  // the sheet's error line, updated IN PLACE: an extra step reporting a
  // problem must not re-render (and so wipe) what the owner typed into it
  function setErr(m) {
    S.err = (m == null) ? "" : String(m);
    var n = el();
    var p = n && n.querySelector ? n.querySelector(".onb-err") : null;
    if (p) { p.textContent = S.err; p.hidden = !S.err; }
  }

  // a promise that settles within SAVE_MS, so a save() that never answers
  // cannot hold Next forever (Skip works regardless)
  function within(p, ms) {
    var id = 0;
    var t = new Promise(function (_, rej) {
      try { id = setTimeout(function () { rej(new Error("no answer")); }, ms); } catch (e) {}
    });
    return Promise.race([Promise.resolve(p), t]).then(function (v) {
      try { clearTimeout(id); } catch (e) {} return v;
    }, function (e) {
      try { clearTimeout(id); } catch (x) {} throw e;
    });
  }

  // ==========================================================================
  // network
  // ==========================================================================
  async function jget(url) {
    var r = await fetch(url, { headers: { Accept: "application/json" } });
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
    return j || { ok: false, errors: ["HTTP " + r.status] };
  }

  // ==========================================================================
  // theme — applied immediately, exactly the way index.html's toggle does it
  // ("system" removes the attribute and the key, falling back to the OS media
  // query; anything else stamps data-theme and persists it).
  // ==========================================================================
  function applyTheme(v) {
    var d = D();
    if (!d || !d.documentElement) return;
    try {
      if (v === "light" || v === "dark") {
        d.documentElement.setAttribute("data-theme", v);
        try { localStorage.setItem("hermes_theme", v); } catch (e) {}
      } else {
        d.documentElement.removeAttribute("data-theme");
        try { localStorage.removeItem("hermes_theme"); } catch (e) {}
      }
    } catch (e) {}
  }

  function currentTheme() {
    try {
      var v = localStorage.getItem("hermes_theme");
      if (v === "light" || v === "dark") return v;
    } catch (e) {}
    return "system";
  }

  // ==========================================================================
  // step bodies
  // ==========================================================================
  function head(icon, eyebrow, st) {
    return '<p class="onb-eyebrow">' + svg(icon, 15) + E(eyebrow) + "</p>" +
      '<h1 class="onb-t">' + E(st.title) + "</h1>" +
      '<p class="onb-s">' + E(st.sub) + "</p>";
  }

  function stepWelcome() {
    return head(G.shield, "Private by construction", STEPS[0]) +
      LOCAL_LINES.map(function (l) { return '<p class="onb-p">' + E(l) + "</p>"; }).join("") +
      networkTableHTML({ prefs: S.prefs, inputs: true });
  }

  function stepModels() {
    var d = S.data || {};
    var si = d.sysinfo || {};
    var rec = d.recommendation || {};
    var pick = S.pick || {};
    var h = head(G.chip, "Detected on this Mac", STEPS[1]);

    // quiet stats — facts, not a dashboard
    var stats = [
      ["Chip", si.chip || "—"],
      ["Memory", si.ram_gb ? si.ram_gb + " GB" : "—"],
      ["Free disk", si.disk_free_gb != null ? gb(si.disk_free_gb) : "—"],
      ["macOS", si.macos || "—"],
      ["Form", si.laptop ? "Laptop" : "Desktop"]
    ];
    h += '<ul class="onb-stats">' + stats.map(function (s) {
      return "<li><span class=\"onb-stat-k\">" + E(s[0]) + "</span>" +
        '<span class="onb-stat-v">' + E(s[1]) + "</span></li>";
    }).join("") + "</ul>";

    var byId = {};
    (rec.fits || []).forEach(function (f) { byId[f.id] = f; });
    var primary = pick.primary || rec.primary;
    var background = pick.background !== undefined ? pick.background : rec.background;
    var lanes = [["Assistant", primary], ["Background", background]];

    h += '<div class="onb-rec">';
    h += "<h3>" + E("Recommended for " + (si.ram_gb || "?") + " GB: " +
      ((byId[primary] || {}).label || "—") +
      (background ? " as the assistant, " + ((byId[background] || {}).label || "—") +
        " for background work" : " — one model, no background lane")) + "</h3>";
    h += '<p class="onb-why">' + E(rec.why || "") + "</p>";
    lanes.forEach(function (l) {
      var id = l[1];
      if (!id) {
        if (l[0] !== "Background") return;
        h += '<div class="onb-lane"><span class="onb-lane-r">' + E(l[0]) + "</span>" +
          '<span class="onb-lane-s">' +
          E("none at this memory size — briefings run on the assistant") + "</span></div>";
        return;
      }
      var f = byId[id] || {};
      var tight = l[0] === "Assistant" && rec.tight;
      h += '<div class="onb-lane"><span class="onb-lane-r">' + E(l[0]) + "</span>" +
        '<span class="onb-lane-n">' + E(f.label || id) + "</span>" +
        '<span class="onb-lane-s">' + E(gb(f.size_gb) + " download") + "</span>" +
        (tight ? '<span class="onb-tag">tight</span>' : "") +
        (f.downloaded ? '<span class="onb-lane-s">' + E("· already on this Mac") + "</span>" : "") +
        '<span class="' + fitClass(f.fit) + '">' +
        E(fitLine(f.ram, si.ram_gb, f.fit)) + "</span></div>";
    });

    var ids = [primary, background].filter(Boolean);
    var need = 0, allHave = true;
    ids.forEach(function (id) {
      var f = byId[id] || {};
      if (!f.downloaded) { need += Number(f.size_gb || 0); allHave = false; }
    });
    need = Math.round(need * 10) / 10;
    if (allHave && ids.length) {
      h += '<p class="onb-done-note">' +
        E("Everything recommended is already downloaded on this Mac — nothing to fetch.") +
        "</p>";
    } else {
      h += '<p class="onb-total">' + E(gb(need) + " to download") +
        (si.disk_free_gb != null
          ? ' <span class="onb-lane-s">' + E("· " + gb(si.disk_free_gb) + " free") + "</span>"
          : "") + "</p>";
    }
    h += "</div>";

    // the battery alternative, when the tier offers one
    if (rec.alt) {
      var altOn = !!pick.alt;
      h += '<div class="onb-row" style="margin-top:8px"><div class="onb-row-txt">' +
        '<span class="onb-row-l">' + E(rec.alt.label || "Lighter option") + "</span>" +
        '<span class="onb-row-d">' + E(rec.alt.why || "") + "</span></div>" +
        '<div class="onb-row-ctl"><label class="onb-sw">' +
        '<input type="checkbox" data-alt="1"' + (altOn ? " checked" : "") +
        ' aria-label="' + E(rec.alt.label || "Lighter option") + '">' +
        '<span class="onb-sw-t" aria-hidden="true"></span>' +
        '<span class="onb-sw-l">' + (altOn ? "On" : "Off") + "</span></label></div></div>";
    }

    // alternatives — a compact list, not a grid of cards
    var alts = (rec.fits || []).filter(function (f) { return ids.indexOf(f.id) < 0; });
    if (alts.length) {
      h += '<div class="onb-h"><h2>Other models</h2>' +
        "<span>" + E("Pick any of these later from the model menu") + "</span></div>" +
        '<ul class="onb-alts">' + alts.map(function (f) {
          return "<li><span class=\"onb-alt-n\">" + E(f.label) + "</span>" +
            '<span class="onb-alt-d">' + E(f.note || "") + "</span>" +
            '<span class="onb-alt-s">' + E(gb(f.size_gb)) + "</span>" +
            '<span class="' + fitClass(f.fit) + '">' +
            E(fitLine(f.ram, si.ram_gb, f.fit)) + "</span></li>";
        }).join("") + "</ul>";
    }

    return h + dlHTML();
  }

  // ---- shared by the legacy body and the advisor's --------------------------
  function fitsById() {
    var byId = {};
    (((S.data || {}).recommendation || {}).fits || []).forEach(function (f) {
      if (f && f.id) byId[f.id] = f;
    });
    return byId;
  }

  // fully on this Mac: the recommendation's own flag, or the detected list
  // (the advisor can name a curated model that is not in `fits`)
  function isLocal(id) {
    if (!id) return false;
    var f = fitsById()[id];
    if (f && f.downloaded) return true;
    var have = (((S.data || {}).detect || {}).models_downloaded) || [];
    return Array.isArray(have) && have.indexOf(id) >= 0;
  }

  // a model's display name: the catalog's, the recommendation's, the
  // advisor's plans (they name curated ids outside the catalog), else the id
  function labelOf(id) {
    if (!id) return "";
    var f = fitsById()[id];
    if (f && f.label) return f.label;
    var rec = (S.data || {}).recommendation || {};
    if (rec.primary === id && rec.primary_label) return rec.primary_label;
    if (rec.background === id && rec.background_label) return rec.background_label;
    var res = ((rec.advisor || {}).result) || null, hit = "";
    if (res && typeof res === "object") {
      var plans = [res.recommended, res.current].concat(res.alternatives || []);
      plans.forEach(function (p) {
        if (hit || !p) return;
        [p.primary, p.background].forEach(function (l) {
          if (!hit && l && l.id === id && typeof l.label === "string") hit = l.label;
        });
      });
      (res.wont || []).forEach(function (w) {
        if (!hit && w && w.id === id && typeof w.label === "string") hit = w.label;
      });
    }
    return hit || String(id).split("/").pop();
  }

  // download progress, read off /api/models' own downloading/downloaded flags
  // and its download_error (E22: a failed pull used to read "queued" forever).
  // Its own node, so the 2.5 s poll updates just this (paintDl), never the step.
  function dlFailed(id) { return !!(S.dl && S.dl.failed && S.dl.failed[id]); }
  function dlAnyFailed() {
    return !!(S.dl && S.dl.started && (S.dl.ids || []).some(dlFailed));
  }
  function dlAllDone() {
    return !!(S.dl && S.dl.started && (S.dl.ids || []).length &&
      S.dl.ids.every(function (i) { return (S.dl.done || {})[i]; }));
  }
  // the primary the download request left PENDING (apply no longer activates
  // a model whose download only started), once it is complete on disk
  function dlPendingReady() {
    var ap = (S.dl && S.dl.applied) || {};
    var id = ap.primary;
    return !!(S.dl && S.dl.started && ap.primary_pending === true && id &&
      (S.dl.done || {})[id] && ap.primary_active !== true);
  }
  var ACTIVATE_BTN = '<button type="button" class="onb-b" data-dlact="activate">' +
    "Make it the assistant</button>";
  function dlInner() {
    if (!(S.dl && S.dl.started)) {
      // a re-run: an earlier run's download left its primary pending
      var pp = (S.data || {}).primary_pending;
      if (!pp || typeof pp !== "object" || !pp.id || S.ppDone) return S.ppMsg
        ? '<p class="onb-p">' + E(S.ppMsg) + "</p>" : "";
      var nm = typeof pp.label === "string" && pp.label ? pp.label : labelOf(pp.id);
      // S2 seam: the state read says the pending pick's download FAILED
      // (aux_onboarding.py primary_pending.error, a string; anything else
      // truthy still counts). Complete on disk wins over a stale error, and a
      // Retry just sent hides it until the next state read.
      var pe = pp.ready || S.ppRetry === pp.id ? ""
        : typeof pp.error === "string" ? pp.error.trim()
        : pp.error ? "the download did not finish" : "";
      if (pe) {
        return '<div class="onb-h"><h2>' + E("Download failed") + "</h2>" +
          "<span>" + E(nm + " did not download (" + pe.slice(0, 160) + "). Nothing was " +
            "switched to it — retry, or choose another model.") + "</span></div>" +
          '<div class="onb-dl-act"><button type="button" class="onb-b" data-dlact="ppretry">' +
          "Retry</button></div>";
      }
      if (S.ppRetry === pp.id) {
        return '<div class="onb-h"><h2>' + E("Downloading") + "</h2>" +
          "<span>" + E(nm + " is downloading again — the model menu shows progress. It " +
            "becomes the assistant once it has finished, while the model is not running.") +
          "</span></div>";
      }
      return '<div class="onb-h"><h2>' + E(pp.ready ? "Downloaded" : "Downloading") + "</h2>" +
        "<span>" + E(pp.ready
          ? nm + " is on this Mac. It becomes the assistant the next time the " +
            "model is not running, or now from here."
          : nm + " is still downloading. It becomes the assistant once it has " +
            "finished, while the model is not running.") + "</span></div>" +
        (pp.ready ? '<div class="onb-dl-act">' + ACTIVATE_BTN + "</div>" : "");
    }
    var lines = S.dl.ids.map(function (id) {
      var failed = dlFailed(id);
      var st = S.dl.done[id] ? "downloaded"
        : S.dl.downloading[id] ? "downloading…"
        : failed ? "failed: " + String(S.dl.failed[id]).slice(0, 160) : "queued";
      return "<li><span class=\"onb-alt-n\">" + E(labelOf(id)) + "</span>" +
        '<span class="onb-alt-s' + (failed ? " onb-alt-f" : "") + '">' + E(st) + "</span>" +
        (failed ? '<button type="button" class="onb-b" data-dlact="retry">Retry</button>' : "") +
        "</li>";
    }).join("");
    var running = S.dl.ids.some(function (i) {
      return !S.dl.done[i] && !dlFailed(i);
    });
    var head = running ? ["Downloading", "This continues in the background — you can keep going"]
      : dlAnyFailed() ? ["Download failed", "Nothing was switched to a model that did not " +
          "download. Retry, or choose later."]
      : ["Downloaded", "Everything you picked is on this Mac."];
    var tail = "";
    if (dlPendingReady()) tail = '<div class="onb-dl-act">' + ACTIVATE_BTN + "</div>";
    else if (S.dl.activateMsg) tail = '<p class="onb-p">' + E(S.dl.activateMsg) + "</p>";
    return '<div class="onb-h"><h2>' + E(head[0]) + "</h2>" +
      "<span>" + E(head[1]) + "</span></div><ul class=\"onb-alts\">" + lines + "</ul>" + tail;
  }

  function dlHTML() { return '<div class="onb-dl">' + dlInner() + "</div>"; }

  // ---- the advisor's body (round-3 shared contract 2) ----------------------
  function advisor() {
    var A = null;
    try { A = W.hermesAdvisor; } catch (e) { A = null; }
    var st = A && typeof A === "object" ? A.step : null;
    return st && typeof st === "object" && typeof st.render === "function" ? st : null;
  }

  function modelsCtx() {
    var c = hookCtx();
    c.getPick = function () { return Object.assign({}, S.pick || {}); };
    c.setPick = setPick;
    return c;
  }

  // The advisor's pick. Updates S.pick and repaints ONLY the action row, so
  // the Download button quotes the plan ("Download recommended (~17 GB)")
  // without re-rendering the advisor's own body under it.
  function setPick(p) {
    if (!p || typeof p !== "object" || typeof p.primary !== "string" || !p.primary.trim()) {
      warnOnce("pick", "setPick needs {primary: <model id>}");
      return false;
    }
    var bg = p.background;
    if (bg !== undefined && bg !== null && typeof bg !== "string") {
      warnOnce("pick:bg", "setPick: background must be a model id or null");
      return false;
    }
    var g = p.download_gb;
    S.pick = {
      primary: p.primary.trim(),
      background: typeof bg === "string" && bg.trim() ? bg.trim() : null,
      alt: false,
      download_gb: typeof g === "number" && isFinite(g) && g >= 0 ? g : undefined,
      // the advisor offers an uncensored plan only after the STORED
      // acknowledgement; apply is told so explicitly (ack_uncensored)
      uncensored: p.uncensored === true,
      from: "advisor"
    };
    paintFoot();
    return true;
  }

  // the models step through the advisor, or null for the legacy body
  function advisorBody() {
    var step = advisor();
    if (!step) return null;
    var inner = null;
    try { inner = step.render(modelsCtx()); } catch (e) {
      warnOnce("adv:render", "the model advisor could not render; showing the plain recommendation", e);
      return null;
    }
    if (typeof inner !== "string" || !inner.trim()) return null;
    // The legacy sub line says "nothing here was asked of you", which is
    // false above a questionnaire: the advisor's own sub (or its twin below)
    // takes its place.
    var def = stepDef("models");
    var sub = typeof step.sub === "string" && step.sub.trim() ? step.sub.trim() : ADV_SUB;
    return head(G.chip, "Detected on this Mac", { title: def.title, sub: sub }) +
      '<div class="onb-body onb-adv" data-onb-step="models">' + inner + "</div>" + dlHTML();
  }
  var ADV_SUB = "Detected on this Mac, then matched to what you want to do. " +
    "Nothing downloads until you press Download.";

  // ---- an extra step's body -------------------------------------------------
  function extBody(st) {
    var e = st.ext, inner = null;
    try {
      var h = e.render(hookCtx());
      inner = h == null ? "" : String(h);
      S.broken[e.key] = false;
    } catch (x) {
      warnOnce("render:" + e.key, "the \"" + e.key + "\" step could not render", x);
      S.broken[e.key] = true;
    }
    if (inner === null) {
      inner = '<p class="onb-p onb-muted">' + E("This step could not be shown.") + "</p>";
    }
    return head(G.extra, st.eyebrow || "Optional", st) +
      '<div class="onb-body" data-onb-step="' + E(e.key) + '">' + inner + "</div>";
  }

  function stepPrefs() {
    var d = S.data || {};
    var det = d.detect || {};
    var p = S.prefs || {};
    var h = head(G.sliders, "Preferences", STEPS[2]);

    // theme
    h += '<div class="onb-row"><div class="onb-row-txt">' +
      '<span class="onb-row-l">Appearance</span>' +
      '<span class="onb-row-d">' + E("Applies as you choose it.") + "</span></div>" +
      '<div class="onb-row-ctl">' + seg("theme", currentTheme(), [
        ["system", "System"], ["light", "Light"], ["dark", "Dark"]
      ]) + "</div></div>";

    // idle
    var idleVal = p.idle_enabled === false ? "never" : String(p.idle_min || 10);
    h += '<div class="onb-row"><div class="onb-row-txt">' +
      '<span class="onb-row-l">Sleep the model after</span>' +
      '<span class="onb-row-d">' +
      E("Frees its memory when you are not using it. Your next message wakes " +
        "it automatically.") + "</span></div>" +
      '<div class="onb-row-ctl">' + seg("idle", idleVal, [
        ["5", "5 min"], ["10", "10 min"], ["20", "20 min"], ["never", "Never"]
      ]) + "</div></div>";

    h += row("prewarm", "Prewarm after wake",
      "Warms the model the moment it wakes, so your first message is not slow.",
      p.prewarm !== false);

    // Claude escalation: only shown when the CLI is actually installed —
    // offering a switch for a binary that is not there is a lie about capability
    if (det.claude_cli) {
      h += row("claude_escalation", "Claude escalation",
        "Sends a hard question to Claude, in parallel with the local answer: " +
        "the ones you escalate, the ones the auto-router picks, and the ones " +
        "the agent asks itself. For-You has its own switch on the first " +
        "screen. Off by default — nothing leaves for Anthropic until you " +
        "turn this on.",
        p.claude_escalation === true);
    }

    h += '<div class="onb-h"><h2>Notifications</h2>' +
      "<span>" + E("Master switches — everything else is per-rule in Settings") +
      "</span></div>";
    h += row("briefings", "Briefings",
      "Morning brief, midday pulse and evening wrap.", p.briefings !== false);
    h += row("news", "News and breaking alerts",
      "Watchtower rules and breaking-news pushes.", p.news !== false);

    var qh = p.quiet_hours || {};
    h += '<div class="onb-row"><div class="onb-row-txt">' +
      '<span class="onb-row-l">Quiet hours</span>' +
      '<span class="onb-row-d">' +
      E("Nothing is pushed between these times.") + "</span></div>" +
      '<div class="onb-row-ctl">' +
      '<input class="onb-time" type="time" data-qh="start" value="' +
      E(qh.start || "22:00") + '" aria-label="Quiet hours start">' +
      '<span class="onb-to">to</span>' +
      '<input class="onb-time" type="time" data-qh="end" value="' +
      E(qh.end || "07:00") + '" aria-label="Quiet hours end">' +
      "</div></div>";

    // ---- status: read-only, each with the NEXT STEP as plain text ----------
    h += '<div class="onb-h"><h2>Connections</h2>' +
      "<span>" + E("Nothing here is required to start") + "</span></div>";
    h += statusRows(det);
    return h;
  }

  function statusRows(det) {
    det = det || {};
    var rows = [];
    rows.push(det.telegram_configured
      ? ["ok", "Telegram", "Configured. Message your bot from anywhere and it " +
         "answers with the same assistant."]
      : ["", "Telegram", "Not configured. Create a bot with @BotFather, then put " +
         "TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS in ~/.hermes/.env."]);
    rows.push(det.google_configured
      ? ["ok", "Google", "Connected. Calendar, mail and contacts are read-only; " +
         "nothing can be sent or changed."]
      : ["", "Google", "Not connected. Settings › Connections walks through the " +
         "OAuth steps. Read-only — Hermes can never send mail or change your calendar."]);
    if (det.fda === true) {
      rows.push(["ok", "Full Disk Access", "Granted. Messages appear in the " +
        "Message Center and in search."]);
    } else if (det.fda === false) {
      rows.push(["wait", "Full Disk Access", "Not granted. System Settings › " +
        "Privacy & Security › Full Disk Access › add Hermes Assistant. " +
        "Without it the Message Center stays empty."]);
    } else {
      rows.push(["", "Full Disk Access", "Not reported yet. The app checks on " +
        "launch; if the Message Center stays empty, add Hermes Assistant under " +
        "System Settings › Privacy & Security › Full Disk Access."]);
    }
    rows.push(det.hermes_cli
      ? ["ok", "Hermes agent", "Installed at " + (det.hermes_path || "~/.local/bin/hermes") + "."]
      : ["wait", "Hermes agent", "Not found on PATH. Run ./install.sh from the " +
         "repo — chat cannot run without it."]);
    // the extra steps' own rows (GitHub: connected or not), each guarded
    extras().forEach(function (e) {
      if (typeof e.statusRow !== "function") return;
      var r = null;
      try { r = e.statusRow(det); } catch (x) {
        warnOnce("status:" + e.key, "the \"" + e.key + "\" status row failed", x);
        return;
      }
      if (!r || typeof r.length !== "number" || r.length < 3) return;
      var label = r[1] == null ? "" : String(r[1]);
      if (!label.trim()) return;
      // the class is a fixed vocabulary: it is written into the markup
      rows.push([r[0] === "ok" || r[0] === "wait" ? r[0] : "", label,
                 r[2] == null ? "" : String(r[2])]);
    });
    return rows.map(function (r) {
      return '<div class="onb-st' + (r[0] ? " " + r[0] : "") + '">' +
        '<span class="onb-st-dot" aria-hidden="true"></span>' +
        '<div class="onb-st-b"><div class="onb-st-l">' + E(r[1]) + "</div>" +
        '<div class="onb-st-v">' + E(r[2]) + "</div></div></div>";
    }).join("");
  }

  function seg(name, value, opts) {
    return '<div class="onb-seg" role="radiogroup" aria-label="' + E(name) + '">' +
      opts.map(function (o) {
        return '<label><input type="radio" name="onb-' + E(name) + '" data-seg="' +
          E(name) + '" value="' + E(o[0]) + '"' +
          (String(value) === String(o[0]) ? " checked" : "") + ">" +
          "<span>" + E(o[1]) + "</span></label>";
      }).join("") + "</div>";
  }

  function row(key, label, desc, on) {
    return '<div class="onb-row"><div class="onb-row-txt">' +
      '<span class="onb-row-l">' + E(label) + "</span>" +
      '<span class="onb-row-d">' + E(desc) + "</span></div>" +
      '<div class="onb-row-ctl"><label class="onb-sw">' +
      '<input type="checkbox" data-pref="' + E(key) + '"' + (on ? " checked" : "") +
      ' aria-label="' + E(label) + '">' +
      '<span class="onb-sw-t" aria-hidden="true"></span>' +
      '<span class="onb-sw-l">' + (on ? "On" : "Off") + "</span></label></div></div>";
  }

  // One summary line as markup: escaped, with its figures ("10 minutes",
  // "~23 GB") in .onb-fig boxes the Ready step counts up when it is reached.
  // The box already holds the final value, so no runtime means no change.
  function figLine(s) {
    return E(s).replace(/(^|[\s~(])(\d{1,4}(?:\.\d)?)(?=\s(?:minutes?|GB)\b)/g,
      function (m, pre, num) {
        return pre + '<span class="onb-fig" data-fig="' + num + '">' + num + "</span>";
      });
  }

  function stepDone() {
    var h = head(G.check, "Setup complete", stepDef("done"));
    var lines = S.applied.length ? S.applied : ["Defaults kept — nothing was changed."];
    h += '<ul class="onb-sum">' + lines.map(function (l) {
      return "<li>" + figLine(l) + "</li>";
    }).join("") + "</ul>";
    h += '<p class="onb-p">' +
      'Quick Ask is on <span class="onb-key">' + E("⌃⌥Space") + "</span>" +
      E(" from anywhere on the Mac — ask without leaving what you are doing.") +
      "</p>";
    h += '<p class="onb-p">' +
      E("Everything on this screen lives in Settings, and you can run this setup " +
        "again from Settings › Overview.") + "</p>";
    return h;
  }

  var BODY = { welcome: stepWelcome, models: stepModels, prefs: stepPrefs, done: stepDone };

  // ==========================================================================
  // the sheet
  // ==========================================================================
  function bodyHTML() {
    var st = curStep();
    S.advMode = false;
    if (st.ext) return extBody(st);
    if (st.key === "models") {
      var a = advisorBody();
      if (a != null) { S.advMode = true; return a; }
    }
    return (BODY[st.key] || stepWelcome)();
  }

  // The primary action for the current step: {label, act, secondary}.
  function nextAction() {
    var key = stepKey(), st = curStep();
    var label, act, secondary = "", covered = false;
    if (key === "models") {
      var rec = (S.data || {}).recommendation || {};
      var pick = S.pick || {};
      var fp = pick.primary || rec.primary;
      var fb = pick.background !== undefined ? pick.background : rec.background;
      var ids = [fp, fb].filter(Boolean);
      var allHave = ids.length > 0 && ids.every(isLocal);
      // a started download counts only for the picks it carried: toggling the
      // battery pair afterwards needs its own download (C63 review)
      covered = dlCovers(fp, fb);
      if (allHave || covered) { label = "Continue"; act = "next"; }
      else {
        act = "download";
        // the advisor's plan says what it costs (setPick's download_gb)
        var g = pick.download_gb;
        label = "Download recommended" +
          (typeof g === "number" && isFinite(g) && g > 0 ? " (~" + gb(g) + ")" : "");
      }
      if (act === "download") {
        // its own action: "later" advances WITHOUT sending the picks (C63)
        secondary = '<button type="button" class="onb-b" data-act="later">' +
          "I&rsquo;ll choose later</button>";
      } else if (S.rerun && !covered) {
        // a RE-RUN's Continue applies the recommendation, which may not be the
        // owner's current choice (an alternative brain picked in the model
        // menu): keeping it must be one click, never a silent revert
        secondary = '<button type="button" class="onb-b" data-act="later">' +
          "Keep current models</button>";
      }
    } else if (lastStep()) {
      label = "Start"; act = "finish";
    } else {
      label = "Continue"; act = "next";
      // every extra step can be skipped: Skip never calls its save()
      if (st.ext) {
        secondary = '<button type="button" class="onb-b" data-act="skip">Skip</button>';
      }
    }
    // E23: these two steps post the owner's switches; until a real state has
    // loaded in this open they hold defaults, never the owner's settings
    var blocked = stateBlocked();
    if (blocked) {
      secondary = '<button type="button" class="onb-b" data-act="retry"' +
        (S.busy ? " disabled" : "") + ">" + (S.busy === "retry" ? "Retrying…" : "Retry") +
        "</button>";
    }
    if (S.busy === "download") label = "Starting…";
    if (S.busy === "finish") label = "Finishing…";
    return { label: label, act: act, secondary: secondary, disabled: blocked };
  }

  // E23: the welcome and prefs steps post the whole set of switches they show;
  // with no state read in this open those are hard defaults, so they stay
  // read-only until a Retry loads the real ones
  function stateBlocked() {
    var key = stepKey();
    return !S.stateOk && (key === "welcome" || key === "prefs");
  }
  var STATE_ERR = "Could not read this Mac's setup state, so nothing on this " +
    "screen can be saved yet (it would overwrite your settings with defaults). " +
    "Press Retry.";

  // the action row (the footer's contents): repainted alone by paintFoot()
  function footHTML() {
    var nx = nextAction();
    return '<div class="onb-foot-in">' +
      '<span class="onb-step-n">Step ' + (S.step + 1) + " of " + stepCount() + "</span>" +
      (S.step > 0
        ? '<button type="button" class="onb-b" data-act="back">Back</button>' : "") +
      nx.secondary +
      '<button type="button" class="onb-b onb-go" data-act="' + nx.act + '"' +
      (S.busy || nx.disabled ? " disabled" : "") + ">" + E(nx.label) + "</button></div>";
  }

  function sheetHTML() {
    var body = "";
    try { body = bodyHTML(); } catch (e) { body = ""; }
    var rail = '<ol class="onb-rail" aria-hidden="true">' +
      liveSteps().map(function (_, i) {
        return "<li" + (i <= S.step ? ' class="on"' : "") + "></li>";
      }).join("") + "</ol>";
    return CSS() + rail +
      '<div class="onb-scroll"><div class="onb-col">' + body +
      // always present (hidden while empty) so setErr() can fill it in place
      '<p class="onb-err" role="alert"' + (S.err ? "" : " hidden") + ">" + E(S.err) + "</p>" +
      "</div></div>" +
      '<div class="onb-foot">' + footHTML() + "</div>";
  }

  function el() { var d = D(); return d ? d.getElementById(SHEET_ID) : null; }

  // ---- motion (motion-system §8.9), only through HA() / window.HermesAnim ---
  function anim() {
    try {
      if (typeof HA === "function") { var h = HA(); if (h && typeof h.sequence === "function") return h; }
    } catch (e) {}
    var A = null;
    try { A = W.HermesAnim; } catch (e) { A = null; }
    return A && typeof A.sequence === "function" ? A : null;
  }

  // finish what the last step change started: the nodes it moves are about
  // to be replaced, and a finished handle cleans up after itself
  function settleAnim() {
    var hs = S.anim || [];
    S.anim = [];
    hs.forEach(function (h) { try { if (h && !h.done && h.finish) h.finish(); } catch (e) {} });
  }

  function keep(h) { if (h && !h.done) S.anim.push(h); }

  // the sheet arriving: opacity 0 and scale .98 to its CSS state over dur.pop
  function animOpen(n) {
    var A = anim();
    if (!A) return;
    var dur = (A.T && A.T.dur && A.T.dur.pop) || 0.32;
    try {
      A.sequence("onb-open", function (tl) {
        tl.from(n, { opacity: 0, scale: 0.98, duration: dur });
      });
    } catch (e) {}
  }

  // a step CHANGE: the column slides in from the side it came from, the new
  // rail dot pops, the heading's words rise; on the Ready step the figures
  // count up. A repaint of the same step never gets here.
  function animStep(n, dir, key) {
    var A = anim();
    if (!A || !n || !n.querySelector) return;
    var col = n.querySelector(".onb-col");
    var dots = n.querySelectorAll(".onb-rail li");
    var dot = dots && dots.length > S.step ? dots[S.step] : null;
    var h1 = n.querySelector("h1.onb-t");
    try {
      keep(A.sequence("onb", function (tl) {
        if (col) tl.from(col, { x: 24 * dir, opacity: 0, duration: 0.32 });
        if (dot) tl.from(dot, { scale: 0.6, duration: 0.38, ease: "hermes.gel" }, 0);
      }));
    } catch (e) {}
    try { if (h1 && typeof A.words === "function") keep(A.words(h1, { delay: 0.06 })); } catch (e) {}
    if (key === "done" && typeof A.count === "function") {
      Array.prototype.slice.call(n.querySelectorAll(".onb-sum .onb-fig[data-fig]"))
        .slice(0, 8).forEach(function (f) {
          var raw = f.getAttribute("data-fig") || "";
          var to = Number(raw);
          if (!isFinite(to)) return;
          var dp = raw.indexOf(".") >= 0 ? 1 : 0;
          try {
            keep(A.count(f, to, { from: 0, fmt: function (v) { return Number(v).toFixed(dp); } }));
          } catch (e) {}
        });
    }
  }

  // A repaint asked for WHILE a paint is wiring (an entry's or the advisor's
  // wire() calling ctx.repaint) runs once, after it; one asked for by that
  // follow-up is dropped, so a wire() that always repaints cannot loop.
  var painting = 0, paintAgain = false;
  function paint() {
    if (painting) {
      if (painting === 1) paintAgain = true;
      else warnOnce("repaint-loop", "a step asked to repaint from inside its own repaint; ignored");
      return;
    }
    painting = 1;
    try { paintNow(); } finally { painting = 0; }
    if (paintAgain) {
      paintAgain = false;
      painting = 2;
      try { paintNow(); } finally { painting = 0; }
    }
  }

  function paintNow() {
    var d = D();
    if (!d) return;
    var n = el();
    if (!n) {
      if (!d.body) return;
      n = d.createElement("div");
      n.id = SHEET_ID;
      n.setAttribute("role", "dialog");
      n.setAttribute("aria-modal", "true");
      n.setAttribute("aria-label", "Hermes setup");
      d.body.appendChild(n);
    }
    n.hidden = !S.open;
    if (!S.open) { S.shown = false; S.painted = null; settleAnim(); return; }
    var key = stepKey(), idx = S.step, prev = S.painted;
    // ONLY a step change animates; a toggle, a busy label or the download
    // poll repaints in place with no motion at all (E12)
    var change = !!(S.shown && prev && prev.key !== key);
    var dir = change ? (idx >= prev.idx ? 1 : -1) : 0;
    // E110: an in-place repaint (a switch, a radio) keeps focus on the control
    // that was used; only a step change goes to the primary action
    var keepF = (prev && prev.key === key) ? focusKey(n, d) : null;
    settleAnim();
    try { n.innerHTML = sheetHTML(); } catch (e) { return; }
    S.painted = { key: key, idx: idx };
    wire(n);
    if (keepF) {
      try {
        var same = n.querySelectorAll(keepF.sel);
        var t = same.length ? same[Math.min(keepF.at, same.length - 1)] : null;
        if (t && !t.disabled) t.focus();
      } catch (e) {}
    }
    // focus the primary action so Enter works immediately and a screen reader
    // lands inside the dialog rather than behind it
    try {
      var go = n.querySelector(".onb-go");
      if (go && go.disabled) go = n.querySelector(".onb-foot button:not(:disabled)");
      if (go && (!d.activeElement || !n.contains(d.activeElement))) go.focus();
    } catch (e) {}
    S.footFocus = null;
    if (!S.shown) { S.shown = true; animOpen(n); }
    else if (change) animStep(n, dir, key);
  }

  // E110: which control had focus, as a selector plus its index among the
  // matches (News is a switch on two rows), so a repaint can give it back.
  // Only our own controls; values come from our markup, and anything outside
  // a plain token is not restored (never interpolated into a selector).
  function focusKey(n, d) {
    var ae = null;
    try { ae = d && d.activeElement; } catch (e) { ae = null; }
    if (!ae || ae === d.body || !n.contains(ae) || !ae.getAttribute) return null;
    var ok = function (v) { return typeof v === "string" && /^[A-Za-z0-9_:.-]{1,40}$/.test(v); };
    var sel = null, v;
    if (ok(v = ae.getAttribute("data-pref"))) sel = 'input[data-pref="' + v + '"]';
    else if (ok(v = ae.getAttribute("data-seg"))) {
      var val = ae.getAttribute("value");
      sel = 'input[data-seg="' + v + '"]' + (ok(val) ? '[value="' + val + '"]' : "");
    }
    else if (ok(v = ae.getAttribute("data-qh"))) sel = 'input[data-qh="' + v + '"]';
    else if (ae.getAttribute("data-alt") !== null) sel = "input[data-alt]";
    if (!sel) return null;
    var all = n.querySelectorAll(sel), at = 0;
    for (var i = 0; i < all.length; i++) if (all[i] === ae) { at = i; break; }
    return { sel: sel, at: at };
  }

  // the action row alone (the advisor's pick, a busy label, the poll)
  //
  // Replacing the row drops focus when the button the owner was on is
  // replaced by a DISABLED copy (a busy save / download): the browser parks it
  // on <body>, outside the modal, where Tab leaves the sheet and Esc (the
  // sheet's own keydown) never arrives. paint() used to refocus the primary
  // action on every repaint; this row-only repaint remembers the button that
  // had focus (S.footFocus) across the disabled spell and gives focus back to
  // it, or to the primary action, once it is enabled again. Focus the owner
  // moved somewhere else in the sheet meanwhile (a field in an extra step, the
  // advisor's chips) is never taken.
  function paintFoot() {
    var n = el();
    if (!n || !S.open || !n.querySelector) return;
    var f = n.querySelector(".onb-foot");
    if (!f) return;
    var had = null, inSheet = false, d = D();
    try {
      var ae = d && d.activeElement;
      if (ae && f.contains(ae) && ae.getAttribute) had = ae.getAttribute("data-act");
      inSheet = !!(ae && ae !== d.body && n.contains(ae));
    } catch (e) {}
    if (had) S.footFocus = had;
    else if (inSheet) S.footFocus = null;
    try { f.innerHTML = footHTML(); } catch (e) { return; }
    wireFoot(f);
    var want = had || (!inSheet ? S.footFocus : null);
    if (!want) return;
    try {
      var b = f.querySelector('button[data-act="' + want + '"]');
      // the primary action changes its act when the step's state does
      // (Download -> Continue): fall back to it, never to nothing
      if (!b || b.disabled) b = (!S.busy && f.querySelector(".onb-go")) || null;
      if (b && !b.disabled) { b.focus(); S.footFocus = null; }
    } catch (e) {}
  }

  // the download progress alone (the 2.5 s poll), plus the action row
  function paintDl() {
    var n = el();
    var box = n && n.querySelector ? n.querySelector(".onb-dl") : null;
    var had = null, d = D();
    if (box) {
      try {
        var ae = d && d.activeElement;
        if (ae && box.contains(ae) && ae.getAttribute) had = ae.getAttribute("data-dlact");
      } catch (e) {}
      try { box.innerHTML = dlInner(); } catch (e) {}
      wireDl(n);
    }
    paintFoot();
    // a button the owner was on inside the block: back to it, else to the
    // primary action, never to <body> outside the modal
    if (had) {
      try {
        var b = box.querySelector('button[data-dlact="' + had + '"]') ||
          n.querySelector(".onb-go:not(:disabled)");
        if (b) b.focus();
      } catch (e) {}
    }
  }

  function wireDl(n) {
    if (!n || !n.querySelectorAll) return;
    Array.prototype.slice.call(n.querySelectorAll(".onb-dl button[data-dlact]")).forEach(function (b) {
      b.onclick = function () {
        var a = b.getAttribute("data-dlact");
        if (a === "retry") act("download");
        else if (a === "ppretry") retryPending();
        else if (a === "activate") activatePending();
      };
    });
  }

  // Retry for a re-run's pending pick whose download failed: the model menu's
  // own download route for that one model — no pick is applied again.
  async function retryPending() {
    if (S.busy) return;
    var pp = (S.data || {}).primary_pending;
    var id = pp && typeof pp === "object" ? pp.id : "";
    if (!id || typeof id !== "string") return;
    S.busy = "ppretry"; setErr(""); paintFoot();
    var r = null;
    try { r = await jpost("/api/models/download", { id: id }); } catch (e) {
      r = { ok: false, error: "Could not reach the dashboard." };
    }
    S.busy = "";
    if (r && r.ok !== false) S.ppRetry = id;
    else setErr("The download did not start again: " +
      String((r && (r.error || (Array.isArray(r.errors) && r.errors.join(" · ")))) ||
        "no answer").slice(0, 160) + ".");
    paintDl();
  }

  // "Make it the assistant": the pending primary, once complete on disk. A
  // second apply with download:false — the server activates it only while the
  // model server is down (otherwise it says to switch from the model menu) and
  // restarts nothing. Never automatic.
  async function activatePending() {
    if (S.busy) return;
    var fromDl = !!(S.dl && S.dl.started);
    var id = fromDl ? ((S.dl.applied || {}).primary)
      : (((S.data || {}).primary_pending || {}).id);
    if (!id) return;
    var body = { primary: id, download: false };
    if ((S.pick || {}).uncensored === true && (S.pick || {}).primary === id) body.ack_uncensored = true;
    S.busy = "activate"; setErr(""); paintFoot();
    var r = null;
    try { r = await jpost("/api/onboarding/apply", body); } catch (e) {
      r = { ok: false, errors: ["Could not reach the dashboard."] };
    }
    S.busy = "";
    var ap = (r && r.applied) || {};
    var msg;
    if (ap.primary_active === true) msg = labelOf(id) + " is now the assistant model.";
    else msg = labelOf(id) + " is not the assistant yet" +
      (ap.primary_note ? " — " + ap.primary_note : "") + ".";
    if (r && r.ok === false && (r.errors || []).length) setErr(r.errors.join(" · "));
    if (fromDl) {
      var a0 = S.dl.applied = Object.assign({}, S.dl.applied || {});
      if (ap.primary === id) {
        a0.primary_active = ap.primary_active === true;
        a0.primary_note = ap.primary_note || "";
        a0.primary_pending = ap.primary_active === true ? false : a0.primary_pending;
      }
      S.dl.activateMsg = msg;
    } else {
      if (ap.primary_active === true) S.ppDone = true;
      S.ppMsg = msg;
      if (ap.primary_active === true) S.modelsApplied = ap;
    }
    paintDl();
  }

  function wireFoot(f) {
    if (!f || !f.querySelectorAll) return;
    Array.prototype.slice.call(f.querySelectorAll("button[data-act]")).forEach(function (b) {
      b.onclick = function () { act(b.getAttribute("data-act")); };
    });
  }

  function wire(n) {
    if (!n || !n.querySelectorAll) return;
    n.onkeydown = onKey;
    var foot = n.querySelector(".onb-foot");
    if (foot) wireFoot(foot);
    wireDl(n);
    var st = curStep();
    var body = n.querySelector(".onb-body");
    if (st.ext) {
      // an extra step wires its own body, and nothing of ours runs inside it
      if (body && !S.broken[st.ext.key] && typeof st.ext.wire === "function") {
        try { st.ext.wire(body, hookCtx()); } catch (x) {
          warnOnce("wire:" + st.ext.key, "the \"" + st.ext.key + "\" step could not wire", x);
        }
      }
      return;
    }
    if (st.key === "models" && S.advMode) {
      var step = advisor();
      if (body && step && typeof step.wire === "function") {
        try { step.wire(body, modelsCtx()); } catch (x) {
          warnOnce("adv:wire", "the model advisor could not wire its body", x);
        }
      }
      return;
    }
    var each = function (sel, fn) {
      Array.prototype.slice.call(n.querySelectorAll(sel)).forEach(fn);
    };
    each(".onb-col input[data-pref]", function (i) {
      i.onchange = function () { setPref(i.getAttribute("data-pref"), !!i.checked); };
    });
    each(".onb-col input[data-seg]", function (i) {
      i.onchange = function () {
        if (!i.checked) return;
        var k = i.getAttribute("data-seg");
        if (k === "theme") { applyTheme(i.value); S.prefs.theme = i.value; paint(); }
        else if (k === "idle") setIdle(i.value);
      };
    });
    each(".onb-col input[data-qh]", function (i) {
      i.onchange = function () {
        S.prefs.quiet_hours = S.prefs.quiet_hours || {};
        S.prefs.quiet_hours[i.getAttribute("data-qh")] = i.value;
      };
    });
    each(".onb-col input[data-alt]", function (i) {
      i.onchange = function () {
        var rec = (S.data || {}).recommendation || {};
        var alt = rec.alt || {};
        S.pick = i.checked
          ? { primary: alt.primary, background: alt.background, alt: true }
          : { primary: rec.primary, background: rec.background, alt: false };
        paint();
      };
    });
  }

  function setPref(key, val) {
    S.prefs = S.prefs || {};
    S.prefs[key] = val;
    paint();               // the switch label ("On"/"Off") is part of the markup
  }

  function setIdle(v) {
    S.prefs = S.prefs || {};
    if (v === "never") { S.prefs.idle_enabled = false; }
    else { S.prefs.idle_enabled = true; S.prefs.idle_min = Number(v); }
    paint();
  }

  // ---- keyboard ------------------------------------------------------------
  // Tab is trapped inside the sheet (it is modal); Esc obeys the rule in the
  // header comment; Enter on a non-button falls through to the primary action.
  //
  // ...except on a control that handles Enter itself. The advisor's body (and
  // any extra step) renders such controls inside the sheet — a disclosure's
  // <summary>, a link — and Enter there must open it, never press Download:
  // cancelling the key would also cancel the disclosure's own toggle.
  var ENTER_OWN = 'button,input,select,textarea,summary,a[href],[contenteditable],' +
    '[role="button"],[role="link"]';
  function onKey(ev) {
    if (!S.open || !ev) return;
    if (ev.key === "Escape") {
      ev.stopPropagation();
      if (S.rerun || lastStep()) { ev.preventDefault(); close(); }
      else ev.preventDefault();      // first run: swallowed on purpose
      return;
    }
    if (ev.key === "Tab") { trap(ev); return; }
    if (ev.key === "Enter" && !ev.shiftKey) {
      var t = ev.target;
      var tag = t && t.tagName ? t.tagName.toLowerCase() : "";
      if (tag === "button" || tag === "input" || tag === "select" || tag === "textarea" ||
          tag === "summary") return;
      try { if (t && t.closest && t.closest(ENTER_OWN)) return; } catch (e) {}
      ev.preventDefault();
      var n = el(), go = n && n.querySelector ? n.querySelector(".onb-go") : null;
      if (go && !go.disabled) go.click();
    }
  }

  var FOCUSABLE = 'button:not(:disabled),[href],input:not(:disabled),select,' +
    'textarea,[tabindex]:not([tabindex="-1"])';

  function trap(ev) {
    var n = el();
    if (!n || !n.querySelectorAll) return;
    var d = D();
    var list = Array.prototype.slice.call(n.querySelectorAll(FOCUSABLE))
      .filter(function (x) {
        return x.offsetParent !== null || x.getClientRects().length > 0 ||
          (x.type === "radio" || x.type === "checkbox");
      });
    if (!list.length) return;
    var first = list[0], last = list[list.length - 1];
    var cur = d ? d.activeElement : null;
    if (ev.shiftKey && (cur === first || !n.contains(cur))) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && (cur === last || !n.contains(cur))) {
      ev.preventDefault(); first.focus();
    }
  }

  // Esc and Tab while focus has FALLEN OUT of the sheet. An extra step (the
  // GitHub step's Check token) or the advisor repaints its own body, and the
  // control that had focus is replaced: the browser parks focus on <body>,
  // where the sheet's own onkeydown never hears a key. paintFoot() repairs
  // that for the action row only. Only the dropped case (<body> or nothing) is
  // routed, and only Esc and Tab: Enter from nowhere must not press the
  // primary action. Captured, so the page's own Esc handlers stay out of it.
  function onDocKey(ev) {
    if (!S.open || !ev || (ev.key !== "Escape" && ev.key !== "Tab")) return;
    var n = el(), d = D();
    if (!n || n.hidden) return;
    var ae = d ? d.activeElement : null;
    if (ae && ae !== d.body) return;      // in the sheet (its onkeydown has it) or elsewhere
    onKey(ev);
  }
  try {
    var dk = D();
    if (dk && dk.addEventListener) dk.addEventListener("keydown", onDocKey, true);
  } catch (e) {}

  // ==========================================================================
  // actions
  // ==========================================================================
  async function act(a) {
    S.err = "";
    if (a === "back") {
      saveTok++;                  // a save still pending for this step no longer counts
      if (S.step > 0) { S.step--; S.busy = ""; paint(); }
      return;
    }
    if (a === "skip") {
      // an extra step's Skip: on without calling its save(), whatever it is doing
      saveTok++;
      S.busy = "";
      if (curStep().ext) advance();
      return;
    }
    if (a === "next" || a === "later") {
      if (stateBlocked()) { setErr(STATE_ERR); return; }   // E23: Enter, a stale click
      if (stepKey() === "models") S.modelsLater = (a === "later");
      var at = S.step, key = stepKey();
      var go = await saveStep();
      // Back, Skip or Esc while it saved: that already moved the sheet
      if (go === null || !S.open || S.step !== at || stepKey() !== key) return;
      if (go === false) return;   // stays on the step; the error line says why
      advance();
      return;
    }
    if (a === "retry") { await retryState(); return; }
    if (a === "download") { await startDownload(); return; }
    if (a === "finish") { await finish(); return; }
  }

  function advance() {
    if (S.step < stepCount() - 1) S.step++;
    // the summary is computed at the moment the Ready step is REACHED, so it
    // reports what was actually applied rather than re-deriving it on every
    // repaint
    if (stepKey() === "done") S.applied = summarize();
    paint();
  }

  // An extra step's save(): true = go on, false = stay (its err is shown),
  // null = the owner skipped or went back meanwhile (the result is dropped).
  async function saveExt(st) {
    var e = st.ext;
    if (S.broken[e.key] || typeof e.save !== "function") return true;
    var tok = ++saveTok, r = null, err = "";
    S.busy = "save"; paintFoot();
    try {
      r = await within(e.save(hookCtx()), SAVE_MS);
    } catch (x) {
      warnOnce("save:" + e.key, "the \"" + e.key + "\" step could not save", x);
      err = "This step could not be saved" + (x && x.message ? " (" + x.message + ")" : "") +
        ". Skip goes on without it.";
    }
    if (tok !== saveTok) return null;
    S.busy = "";
    if (r && typeof r === "object") {
      if (Array.isArray(r.applied)) {
        S.extApplied[e.key] = r.applied.filter(function (l) {
          return typeof l === "string" && l.trim();
        }).map(function (l) { return l.trim().slice(0, 300); }).slice(0, 12);
      }
      if (!err && typeof r.err === "string" && r.err.trim()) err = r.err.trim();
    }
    if (err) { setErr(err); paintFoot(); return false; }
    return true;
  }

  // The advisor's answers (POST /api/advisor/answers inside its save()).
  // Advisory: only a validation error it reports ({err}) keeps the step; a
  // save that throws or never answers lets onboarding go on. null = Back,
  // Skip or Esc meanwhile (the same rule saveExt keeps): the caller must then
  // apply nothing, or the picks the owner backed out of are posted anyway.
  async function advisorSave() {
    var step = advisor();
    if (!step || typeof step.save !== "function") return {};
    var tok = ++saveTok;
    S.busy = "save"; paintFoot();
    var r = {};
    try {
      r = await within(step.save(modelsCtx()), SAVE_MS);
    } catch (x) {
      warnOnce("adv:save", "the model advisor could not save its answers", x);
      r = {};
    }
    if (tok !== saveTok || !S.open) return null;
    S.busy = "";
    return r && typeof r === "object" ? r : {};
  }

  // Persist what the CURRENT step changed, through /api/onboarding/apply.
  // Each step sends only its own fields, so a validation error on one screen
  // cannot roll back a choice made on another. Returns false to stay on the
  // step (an extra step's err, the advisor's err, an uncensored pick without
  // its acknowledgement); otherwise the sheet moves on, an error shown.
  async function saveStep() {
    var st = curStep(), k = st.key, body = null, models = false, stay = false;
    var p = S.prefs || {};
    if (st.ext) return await saveExt(st);
    if (k === "models") {
      // the advisor's answers first: a validation error keeps the step
      if (S.advMode) {
        var ar = await advisorSave();
        if (ar === null) return null;
        if (typeof ar.err === "string" && ar.err) { setErr(ar.err); paintFoot(); return false; }
      }
      // C63: "Continue" (every pick already on this Mac) sent nothing, so a
      // re-run that picked a different downloaded primary never reached
      // apply. It now sends the picks, with no download. "I'll choose
      // later" sends nothing, and a download that already started carried
      // the picks in its own request.
      var rec = (S.data || {}).recommendation || {};
      var pick = S.pick || {};
      var bg = pick.background !== undefined ? pick.background : rec.background;
      var pp = pick.primary || rec.primary;
      // only a download that carried THESE picks makes the post redundant —
      // a pair toggled after it started must still reach apply (C63 review)
      if (S.modelsLater || dlCovers(pp, bg)) return true;
      body = { primary: pp, background: bg || null, download: false };
      // an uncensored plan is pickable only after the stored acknowledgement
      // (the advisor's setPick says so); apply checks it again either way
      if (pick.uncensored === true) body.ack_uncensored = true;
      models = true;
      S.modelsApplied = null;     // a failed post must not reuse an older result
    } else if (k === "welcome") {
      body = { briefings: p.briefings !== false, news: p.news !== false,
               web_research: p.web_research === true };
      if ((S.data || {}).detect && S.data.detect.claude_cli) {
        body.claude_escalation = p.claude_escalation === true;
        // For-You's own Claude switch (C35) has its own route, not apply's
        if ((p.foryou_claude === true) !== (S.fyLoaded === true)) {
          try {
            var fr = await jpost("/api/claude/foryou", { enabled: p.foryou_claude === true });
            if (fr && fr.ok !== false) S.fyLoaded = fr.enabled === true;
            else S.err = "Could not save For-You via Claude.";
          } catch (e) { S.err = "Could not save For-You via Claude."; }
        }
      }
    } else if (k === "prefs") {
      body = {
        theme: p.theme || currentTheme(),
        idle_min: p.idle_enabled === false ? "never" : (p.idle_min || 10),
        prewarm: p.prewarm !== false,
        briefings: p.briefings !== false,
        news: p.news !== false,
        quiet_hours: p.quiet_hours || undefined
      };
      if ((S.data || {}).detect && S.data.detect.claude_cli) {
        body.claude_escalation = p.claude_escalation === true;
      }
      // A NEW install starts on the lean prompt budget: the tool schemas are
      // the biggest item in the prefix every fresh conversation prefills, and
      // a personal assistant does not use browser automation, speech, or
      // image/video generation. Sent ONLY on a genuine first run — a re-run
      // must never silently rewrite a budget its owner has already chosen in
      // Settings > Agent & Models, where Full is one click away.
      if (!S.rerun) body.prompt_budget = "lean";
    }
    if (!body) return true;
    S.busy = "save"; paintFoot();
    try {
      var r = await jpost("/api/onboarding/apply", body);
      if (models) S.modelsApplied = (r && r.applied) || {};
      if (r && r.ok === false && (r.errors || []).length) S.err = r.errors.join(" · ");
      // apply refused the whole request (an uncensored pick without its
      // acknowledgement): nothing was applied, so the owner stays here
      if (models && r && r.needs_ack === true) stay = true;
    } catch (e) {
      S.err = "Could not save: " + (e && e.message ? e.message : "network error");
    }
    S.busy = "";
    if (stay) { setErr(S.err); paintFoot(); return false; }
    return true;
  }

  async function startDownload() {
    var rec = (S.data || {}).recommendation || {};
    var pick = S.pick || {};
    var primary = pick.primary || rec.primary;
    var background = pick.background !== undefined ? pick.background : rec.background;
    S.busy = "download"; setErr(""); paintFoot();
    var body = { primary: primary, background: background || null, download: true };
    if (pick.uncensored === true) body.ack_uncensored = true;
    var r = null;
    try { r = await jpost("/api/onboarding/apply", body); } catch (e) {
      r = { ok: false, errors: ["Could not start the download."] };
    }
    S.busy = "";
    if (r && r.ok === false) {
      setErr((r.errors && r.errors.length ? r.errors : ["Could not start the download."]).join(" · "));
      paintFoot();
      return;
    }
    var ids = [primary, background].filter(Boolean);
    // `applied` says whether the pick became the active model (C63) — the
    // summary must not claim it did when the model server was running
    S.dl = { ids: ids, done: {}, downloading: {}, failed: {}, told: {}, started: true,
             applied: (r && r.applied) || {} };
    S.modelsApplied = null;       // this request is now the newest apply result
    paintDl();                    // the progress block and the action row only
    pollModels();
  }

  // Did the started download carry exactly these picks? S.dl.ids is
  // [primary, background] with a missing background dropped.
  function dlCovers(primary, background) {
    var want = [primary, background].filter(Boolean).join("\n");
    // a pull that FAILED covers nothing: the action row offers it again (E22)
    return !!(S.dl && S.dl.started && !dlAnyFailed() &&
      (S.dl.ids || []).join("\n") === want);
  }

  // Progress comes from /api/models' OWN `downloading` / `downloaded` flags —
  // the same source the model menu reads. No second progress protocol. Each
  // tick rewrites the progress block and the action row only — never the
  // step, so nothing re-enters and nothing animates (E12).
  function pollModels() {
    if (dlTimer) { clearInterval(dlTimer); dlTimer = null; }
    dlTimer = setInterval(async function () {
      if (!S.open || !S.dl || !S.dl.started) {
        clearInterval(dlTimer); dlTimer = null; return;
      }
      try {
        var m = await jget("/api/models");
        var dl = S.dl;
        if (!dl || !dl.started) return;
        var any = false;
        dl.failed = dl.failed || {}; dl.told = dl.told || {};
        (m.models || []).forEach(function (x) {
          if (!x || dl.ids.indexOf(x.id) < 0) return;
          dl.done[x.id] = !!x.downloaded;
          dl.downloading[x.id] = !!x.downloading;
          // E22: the download thread's failure (/api/models' download_error)
          var er = !x.downloaded && !x.downloading && typeof x.download_error === "string"
            ? x.download_error.trim() : "";
          dl.failed[x.id] = er;
          if (!er) dl.told[x.id] = false;
          if (x.downloading) any = true;
        });
        var fresh = dl.ids.filter(function (i) { return dl.failed[i] && !dl.told[i]; });
        fresh.forEach(function (i) { dl.told[i] = true; });
        if (fresh.length && stepKey() === "models") {
          setErr("Download failed: " + fresh.map(function (i) {
            return labelOf(i) + " (" + String(dl.failed[i]).slice(0, 160) + ")";
          }).join(" · ") + ". Nothing was switched to it — Retry, or choose later.");
        }
        if (stepKey() === "models") paintDl();
        // stop once nothing is downloading any more: every row is either on
        // this Mac or failed (a failed row used to keep this running forever)
        if (!any && dl.ids.every(function (i) { return dl.done[i] || dl.failed[i]; })) {
          clearInterval(dlTimer); dlTimer = null;
        }
      } catch (e) {}
    }, 2500);
  }

  // ---- the Ready step's summary: each step's own lines ---------------------
  // models, then prefs, then welcome, then every extra step in order (its
  // save()'s `applied` lines, then its summary()).
  function summarize() {
    var out = [];
    [sumModels, sumPrefs, sumWelcome].forEach(function (fn) {
      var lines = [];
      try { lines = fn() || []; } catch (e) { warnOnce("sum:" + fn.name, "a summary line failed", e); }
      lines.forEach(function (l) { out.push(l); });
    });
    liveSteps().forEach(function (st) {
      if (st && st.ext) extLines(st).forEach(function (l) { out.push(l); });
    });
    return out;
  }

  function sumModels() {
    var rec = (S.data || {}).recommendation || {};
    var pick = S.pick || {};
    var out = [];
    var primary = pick.primary || rec.primary;
    var background = pick.background !== undefined ? pick.background : rec.background;
    // what apply actually did with the picks: the Continue request, else the
    // download request (C63). None at all = nothing was sent.
    var ap = S.modelsApplied || (S.dl && S.dl.applied) || null;
    var sent = !!ap;
    var label = labelOf;
    if (ap && S.modelsLater) {
      // "later" sent nothing NOW, but an earlier Continue/download in this
      // run did: report the picks that request applied, not this screen's
      primary = ap.primary || "";
      background = ap.background || null;
    }
    if (!ap) {
      // nothing reached apply (later, or the save failed): never claim a pick
      if (primary || S.modelsLater) {
        out.push("Assistant model: unchanged — pick one any time from the model menu");
      }
    } else if (!primary && S.modelsLater) {
      out.push("Assistant model: unchanged — pick one any time from the model menu");
    } else if (primary && ap.primary !== primary) {
      out.push("Assistant model: unchanged — " + label(primary) + " was not applied");
    } else if (primary) {
      ap = ap || {};
      out.push("Assistant model: " + label(primary) +
        (!background ? ", no background lane"
          : sent && ap.background !== background
            ? ", background lane unchanged"
            : ", background lane: " + label(background)) +
        (ap.primary === primary && ap.primary_active === false
          ? " — " + primaryNote(primary, ap) : "") +
        (ap.background_pending
          ? " — " + (ap.background_note || "the background lane switches once it has stopped")
          : ""));
    }
    if (S.dl && S.dl.started) {
      // E22: what the download really did by now, not what it was asked to do
      var bad = S.dl.ids.filter(dlFailed);
      if (bad.length) {
        out.push("Download failed: " + bad.map(function (i) {
          return labelOf(i) + " (" + String(S.dl.failed[i]).slice(0, 160) + ")";
        }).join(" · ") + ". Nothing was switched to it — retry from the model menu.");
      } else if (dlAllDone()) {
        out.push("Downloaded: " + S.dl.ids.map(labelOf).join(" and ") + ".");
      } else {
        var g = (S.dl.applied || {}).download_gb;
        out.push("Downloading " +
          (typeof g === "number" && isFinite(g) && g > 0 ? "~" + gb(g) + " " : "") +
          "in the background — the model menu shows progress.");
      }
    }
    return out;
  }

  // why the primary is not the assistant yet, as of NOW (E22): a pending pick
  // whose download failed or finished no longer reads "downloading"
  function primaryNote(primary, ap) {
    var dl = S.dl && S.dl.started && (S.dl.ids || []).indexOf(primary) >= 0 ? S.dl : null;
    if (dl && dlFailed(primary)) return "its download failed, so the assistant model is unchanged";
    if (dl && ap.primary_pending === true && (dl.done || {})[primary]) {
      return "downloaded — make it the assistant from the model menu";
    }
    return ap.primary_note || "switch to it from the model menu to use it";
  }

  function sumPrefs() {
    var p = S.prefs || {};
    var out = [];
    out.push("Appearance: " + ({ system: "follows macOS", light: "light", dark: "dark" }[
      p.theme || currentTheme()] || "follows macOS"));
    out.push(p.idle_enabled === false
      ? "The model stays loaded — it will not sleep on its own."
      : "The model sleeps after " + (p.idle_min || 10) +
        " minutes idle and wakes on your next message.");
    out.push("Prewarm after wake: " + (p.prewarm !== false ? "on" : "off"));
    if ((S.data || {}).detect && S.data.detect.claude_cli) {
      out.push("Claude escalation: " + (p.claude_escalation === true ? "on" : "off"));
    }
    var q = p.quiet_hours || {};
    out.push("Briefings " + (p.briefings !== false ? "on" : "off") +
      ", news " + (p.news !== false ? "on" : "off") +
      ", quiet hours " + (q.start || "22:00") + " to " + (q.end || "07:00"));
    return out;
  }

  function sumWelcome() {
    var p = S.prefs || {};
    var out = [];
    if ((S.data || {}).detect && S.data.detect.claude_cli) {
      out.push("For-You via Claude: " + (p.foryou_claude === true ? "on" : "off"));
    }
    out.push("Agent web research: " + (p.web_research === true ? "on" : "off"));
    return out;
  }

  // an extra step's lines: what its save() applied, then its summary(ctx)
  function extLines(st) {
    var e = st.ext, out = [], seen = {};
    var add = function (l) {
      if (typeof l !== "string") return;
      l = l.trim().slice(0, 300);
      if (!l || seen[l]) return;
      seen[l] = 1; out.push(l);
    };
    ((S.extApplied || {})[e.key] || []).forEach(add);
    if (typeof e.summary === "function") {
      try {
        var more = e.summary(hookCtx());
        if (Array.isArray(more)) more.slice(0, 12).forEach(add);
      } catch (x) {
        warnOnce("summary:" + e.key, "the \"" + e.key + "\" summary failed", x);
      }
    }
    return out;
  }

  async function finish() {
    S.busy = "finish"; paintFoot();
    try {
      var r = await jpost("/api/onboarding/done", {});
      // the Settings row reads these: say "last run" now, not after a reload
      if (r && r.ok && S.data) { S.data.done = true; if (r.ts) S.data.done_at = r.ts; }
    } catch (e) {}
    S.busy = "";
    close();
    // hand the user straight to the thing the setup was for: the chat, which
    // lives on the Agent page
    try {
      var d = D();
      if (typeof W.openChat === "function") W.openChat();
      var i = d && d.getElementById ? d.getElementById("input") : null;
      if (i && i.focus) i.focus();
    } catch (e) {}
    mountCard();
  }

  // ==========================================================================
  // open / close
  // ==========================================================================
  async function load() {
    var j = await jget("/api/onboarding/state");
    // E23: a 500 {ok:false} (or an empty answer) is a failed read, not a
    // state — building prefs from it would show and post the defaults
    if (!j || typeof j !== "object" || j.ok !== true) throw new Error("state read failed");
    S.data = j;
    var p = (j && j.prefs) || {};
    S.prefs = {
      theme: currentTheme(),
      idle_min: p.idle_min || 10,
      idle_enabled: p.idle_enabled !== false,
      prewarm: p.prewarm !== false,
      claude_escalation: p.claude_escalation === true,
      foryou_claude: false,
      briefings: p.briefings !== false,
      news: p.news !== false,
      // C50 / R2-43: off unless the backend says an explicit true
      web_research: p.web_research === true,
      quiet_hours: {
        start: (p.quiet_hours || {}).start || "22:00",
        end: (p.quiet_hours || {}).end || "07:00"
      }
    };
    // For-You's own Claude switch is not part of /api/onboarding/state; read
    // it from its route so a re-run shows what is really set (absent = off)
    S.fyLoaded = false;
    try {
      var fy = await jget("/api/claude/foryou");
      S.fyLoaded = !!(fy && fy.enabled === true);
    } catch (e) {}
    S.prefs.foryou_claude = S.fyLoaded;
    var rec = (j && j.recommendation) || {};
    S.pick = { primary: rec.primary, background: rec.background, alt: false };
    return j;
  }

  async function open(opts) {
    opts = opts || {};
    try { S.lastFocus = D() ? D().activeElement : null; } catch (e) {}
    S.rerun = !!opts.rerun;
    S.step = 0; S.err = ""; S.busy = ""; S.dl = null; S.applied = [];
    S.modelsLater = false; S.modelsApplied = null; S.ppDone = false; S.ppMsg = "";
    S.extApplied = {}; S.broken = {}; S.painted = null; S.footFocus = null;
    saveTok++;
    S.stateOk = false;
    try { await load(); S.stateOk = true; } catch (e) {
      S.err = STATE_ERR;
      S.data = S.data || {}; S.prefs = S.prefs || {};
    }
    // the extra steps registered by now (registerStep keeps it current)
    S.steps = buildSteps();
    S.open = true;
    try { if (D() && D().body) D().body.style.overflow = "hidden"; } catch (e) {}
    paint();
  }

  // E23: re-read the state; only a real one unlocks the welcome/prefs steps
  async function retryState() {
    if (S.busy) return;
    S.busy = "retry"; paintFoot();
    var ok = false;
    try { await load(); ok = true; } catch (e) { ok = false; }
    S.busy = "";
    if (!S.open) return;
    if (ok) {
      S.stateOk = true; S.err = "";
      S.steps = buildSteps();
      paint();
    } else {
      setErr(STATE_ERR);
      paintFoot();
    }
  }

  function close() {
    S.open = false;
    saveTok++;
    if (dlTimer) { clearInterval(dlTimer); dlTimer = null; }
    settleAnim();
    S.shown = false; S.painted = null;
    try { if (D() && D().body) D().body.style.overflow = ""; } catch (e) {}
    var n = el();
    if (n) n.hidden = true;
    try { if (S.lastFocus && S.lastFocus.focus) S.lastFocus.focus(); } catch (e) {}
  }

  // ==========================================================================
  // Settings row — "Run setup again", injected the way aux_update injects its
  // card: straight into #sec-overview when the shell exists, otherwise into
  // #view-mind, whose relocator re-homes unknown ids (and the next paint moves
  // it back to Overview). aux_settings_shell.js is never edited.
  // ==========================================================================
  // Inside the panel's .set-body, before the shell's empty
  // [data-legacy-slot] marker (V03): .set-body is the gap:15px flex column
  // that spaces the panel's cards; appended to the <section> itself the card
  // sat flush under them.
  function cardHost() {
    var d = D();
    if (!d) return null;
    var ov = d.getElementById("sec-overview");
    if (ov) {
      var body = null;
      try { body = ov.querySelector ? ov.querySelector(".set-body") : null; } catch (e) { body = null; }
      return body || ov;
    }
    return d.getElementById("view-mind");
  }
  function putInHost(h, n) {
    var anchor = null;
    try { anchor = h.querySelector ? h.querySelector("[data-legacy-slot]") : null; } catch (e) { anchor = null; }
    if (anchor && anchor.parentNode === h) h.insertBefore(n, anchor);
    else h.appendChild(n);
  }

  function cardHTML() {
    var done = (S.data && S.data.done) || false;
    var when = "";
    if (done && S.data && S.data.done_at) {
      try {
        var dt = new Date(S.data.done_at * 1000);
        if (!isNaN(dt.getTime())) {
          var hh = dt.getHours(), mm = dt.getMinutes(), ap = hh >= 12 ? "PM" : "AM";
          hh = hh % 12; if (hh === 0) hh = 12;
          when = " · last run " + (dt.getMonth() + 1) + "/" + dt.getDate() + " " +
            hh + ":" + (mm < 10 ? "0" + mm : mm) + " " + ap;
        }
      } catch (e) {}
    }
    return CSS() +
      '<h2 style="display:flex;align-items:center;gap:7px">' + svg(G.shield, 16) +
      "First-run setup</h2>" +
      // <h2> + .body is the card convention: .body carries the padding (V03)
      '<div class="body"><div class="onb-set-row"><div class="onb-set-txt">' +
      '<div class="onb-set-l">Run setup again</div>' +
      '<div class="onb-set-d">' +
      E("Re-check this Mac, review what leaves it, and revisit the model " +
        "recommendation and preferences.") +
      // the date wraps as one unit, never split across lines (V03)
      (when ? '<span class="onb-set-when">' + E(when) + "</span>" : "") + "</div></div>" +
      '<button type="button" class="onb-set-b" id="onb-rerun">Run setup</button></div></div>';
  }

  function mountCard() {
    var d = D();
    if (!d) return;
    var host = cardHost();
    if (!host) return;
    var n = d.getElementById(CARD_ID);
    if (!n) {
      n = d.createElement("section");
      n.id = CARD_ID;
      n.className = "card glass set-legacy";
      putInHost(host, n);
    } else if (n.parentNode && n.parentNode !== host && d.getElementById("sec-overview")) {
      try { putInHost(host, n); if (n.classList) n.classList.add("set-legacy"); } catch (e) {}
    }
    try { n.innerHTML = cardHTML(); } catch (e) { return; }
    var b = n.querySelector ? n.querySelector("#onb-rerun") : null;
    if (b) b.onclick = function () { open({ rerun: true }); };
  }

  // The done marker alone (?brief=1): the boot check and the Settings row run
  // on every page load and need nothing else, and the full state also ranks
  // models through the advisor. Merged into a full state an open sheet holds.
  var BRIEF_URL = "/api/onboarding/state?brief=1";
  async function loadBrief() {
    var j = await jget(BRIEF_URL);
    if (!j || typeof j !== "object") return j;
    if (S.data && !S.data.brief && S.data.sysinfo) {
      S.data.done = j.done; S.data.done_at = j.done_at; S.data.done_version = j.done_version;
    } else {
      S.data = j;
    }
    return j;
  }

  // chain window.mindExtras exactly like the other aux settings cards
  var prevExtras = W.mindExtras;
  W.mindExtras = async function () {
    if (typeof prevExtras === "function") { try { await prevExtras(); } catch (e) {} }
    try {
      if (!S.data) { try { await loadBrief(); } catch (e) {} }
      mountCard();
    } catch (e) {}
  };

  // ==========================================================================
  // boot — auto-open on a first run, and NEVER when done is true
  // ==========================================================================
  async function boot() {
    var j = null;
    try { j = await jget(BRIEF_URL); } catch (e) { return; }
    if (!j || j.ok === false) return;
    if (!S.data) S.data = j;
    if (j.done === false) { try { await open({ rerun: false }); } catch (e) {} }
  }

  try {
    if (typeof document !== "undefined" && document.readyState === "loading" &&
        document.addEventListener) {
      document.addEventListener("DOMContentLoaded", function () { boot(); }, { once: true });
    } else if (typeof document !== "undefined") {
      boot();
    }
  } catch (e) {}

  // public + headless-harness surface
  W.hermesOnboarding = {
    open: function (o) { return open(o || { rerun: true }); },
    close: close,
    reset: async function () {
      var r = await jpost("/api/onboarding/reset", {});
      try { await loadBrief(); } catch (e) {}
      mountCard();
      return r;
    },
    // the extra-steps hook (see ENTRY above); `steps()` names the live order
    registerStep: registerStep,
    steps: function () { return liveSteps().map(function (s) { return s.key; }); },
    // reusable pieces — the Data & Network card (aux_network.js, 1.1.3) renders
    // the SAME table from the SAME constant with netCSS() re-scoped to its own
    // id, so adding an egress path here updates both surfaces at once.
    networkTableHTML: networkTableHTML, NETWORK_FACTS: NETWORK_FACTS,
    netCSS: netCSS, LOCAL_LINES: LOCAL_LINES,
    statusRows: statusRows, sheetHTML: sheetHTML, cardHTML: cardHTML,
    gb: gb, fitLine: fitLine, fitClass: fitClass, sysLine: sysLine,
    summarize: summarize, applyTheme: applyTheme, currentTheme: currentTheme,
    STEPS: STEPS, state: S,
    go: function (i) { S.step = Math.max(0, Math.min(stepCount() - 1, i | 0)); paint(); }
  };
})();
