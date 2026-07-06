# Research: Great Agent/LLM Interface Patterns (2026)

**Purpose:** Source material for the Agent-page and Settings-page design specs. Every pattern below is (a) cited, (b) rated for fit with Hermes invariants (approval gate un-bypassable, local-first, nothing auto-sends), and (c) translated into build-ready guidance for our stack: stdlib Python + vanilla JS in WKWebView, Liquid Glass, Motion One, zero emoji / bespoke two-tone SVG, 12-hour time, aux-module pattern.

**Method:** Web research across leading agent UIs (Claude Code, Cursor, Devin, Manus, Warp, ChatGPT agent mode, v0/Vercel AI Elements, Chainlit, AG-UI, open-source agent chat UIs) plus 2026 agentic-UX pattern literature (Smashing Magazine, UX Magazine, agentic-design.ai, ambient-agent oversight research).

---

## 1. The dominant layout: conversation + live workspace, side by side

The pattern that has converged across Manus, Devin, ChatGPT agent mode, and Cursor's agent panel: **left ~50% is the chat thread + input; right ~50% is a dynamic viewer showing what the agent is actually doing** — terminal, browser, files, screen. It works because it blends natural-language interaction with direct visual observability: users witness behavior in real time, can intervene, and trust rises because nothing is hidden ([Emerge Haus — "The New Dominant UI Design for AI Agents"](https://www.emerge.haus/blog/the-new-dominant-ui-design-for-ai-agents)).

Manus is the reference implementation: past-task list on the left, chat center, and a **live preview of the agent's computer on the right with a replay dial and a task progress checklist**. Every action gets a log entry with interactive chips linking to search results and screen states ([WorkOS on Manus](https://workos.com/blog/introducing-manus-the-general-ai-agent), [Robonomics — Manus, Devin and the Shape of Agents](https://robonomics.substack.com/p/manus-devin-and-the-shape-of-agents)).

**Critical companion insight:** do NOT merge conversation and autonomous-activity into one stream. "Combining both conversation and activity into one stream produces an interface that fails as both a conversation and an activity tracker" ([HatchWorks — Agent UX Patterns](https://hatchworks.com/blog/ai-agents/agent-ux-patterns/)). Conversation = what the user and agent said. Activity = what the agent did. Related, linked, but visually distinct lanes.

**Hermes fit:** The Agent page should be a two-lane layout: chat thread (primary) + a collapsible "Stage" rail (live tool/terminal/desktop viewer fed by `/api/console`, `/api/recorder`, `/api/desktop/*`). Hermes already has Console and Desktop as separate tabs — the Agent page borrows *live slices* of them as show-ins, it does not replace them. Autonomous (Watchtower/always-on) activity stays out of the chat transcript; it gets an ambient ticker (Section 5), not chat bubbles.

---

## 2. Rendering tool calls, terminal output, diffs, and web search inline

### 2a. The collapsed-card grammar (Claude Code)
Claude Code's terminal UI established the grammar most agent chats now copy: **each tool call renders as a one-line collapsed result — verb + target + outcome glyph — expandable on click to full output.** A "compact" mode hides tool icons and collapses diffs entirely to cut clutter; large transcripts stay scrollable because rendering is aggressively diffed/optimized ([DeepWiki — Claude Code UI/UX & Terminal Integration](https://deepwiki.com/anthropics/claude-code/3.9-uiux-and-terminal-integration), [Claude Code from Source, Ch. 13 — The Terminal UI](https://claude-code-from-source.com/ch13-terminal-ui/)).

Adoptable rules:
- **Default collapsed, one line each**: `Ran command` / `Searched web` / `Read file` with the argument inline and a status glyph (pending / running / ok / failed / denied).
- **Click anywhere on the row expands**; expansion is per-row, remembered per message.
- **Group consecutive tool calls** under one "N steps" header the way Claude Code groups agent turns — a run of 8 file reads is one collapsible cluster, not 8 cards.
- **A density toggle** (our "compact" equivalent) belongs in the Agent page header, persisted via `/api/settings`.

### 2b. The block model (Warp)
Warp's deepest idea: the transcript is a **`BlockList` — an ordered list of *typed*, self-contained blocks** (terminal blocks = command + output as two grids; rich blocks = anything else). The list doesn't care what's inside a block, only its height; agent reasoning appears as rich blocks and the agent's own shell commands appear as ordinary command/output blocks *in the same stream*, and executed terminal blocks can **collapse into conversation summaries, expandable on demand** ([Warp — The Block Model Behind Warp's Agentic Development Environment](https://www.warp.dev/blog/block-model-behind-warps-agentic-development-environment)).

**Hermes fit:** This maps 1:1 to vanilla JS. Define a typed block registry — `text`, `thinking`, `tool.terminal`, `tool.web_search`, `tool.file_diff`, `tool.computer_use`, `approval`, `brain_switch`, `system` — each type is a render function `renderBlock(block) -> HTMLElement` registered by an aux module. New tool types become new registrations, never new page logic. The `/api/chat` poll payload discriminates by block type exactly like AG-UI's discriminated-union event stream ([AG-UI — Agent User Interaction Protocol](https://docs.ag-ui.com/introduction), [dev.to — Designing Agentic Workflows](https://dev.to/eabait/designing-agentic-workflows-lessons-from-orchestration-context-and-ux-13j)).

### 2c. Per-tool renderers worth copying
- **Terminal**: monospace block with the command line pinned as a header (Warp's command/output split); output clipped to ~12 lines with a "N more lines" expander; ANSI-ish severity tinting; a copy affordance. Long-running commands show elapsed time counting live in 12-hour-friendly `mm:ss`.
- **File diffs**: Cursor/Warp render agent edits as reviewable diff panels — additions/deletions tinted, per-file collapse, and (in Warp) inline comment-and-revise ([Warp docs — Terminal and Agent modes](https://docs.warp.dev/agent-platform/local-agents/interacting-with-agents/terminal-and-agent-modes/), [DevTools Academy — Cursor vs Claude Code](https://www.devtoolsacademy.com/blog/cursor-vs-claudecode)). Hermes: unified diff, hairline-separated hunks, `+`/`-` gutter tinted with the file-ops accent; "Open in Console" deep-link to the recorder entry.
- **Web search**: render as a query line + result chips (favicon-less; bespoke SVG globe glyph + domain + title), the Manus "interactive chips" pattern. Chips open in the default browser — local-first, no embedded remote content.
- **Computer use**: a live thumbnail strip of screenshots (data from `/api/desktop/*`) inside the block; clicking a frame opens the Stage rail scrubbed to that moment. **Invariant:** the computer-use viewer is display-only — no clickable approval control may exist inside anything the agent can screenshot/click; approvals live in chrome the agent's computer-use surface cannot reach.
- **Citations/sources**: expandable source lists on claims, per agentic-design.ai's chat-interface patterns ([Agentic Design — Chat Interface Patterns](https://agentic-design.ai/patterns/ui-ux-patterns/chat-interface-patterns)).

### 2d. Generative UI (v0 / CopilotKit / AI SDK)
The 2026 frontier is agents returning **interactive components instead of prose** — cards, forms, mini-widgets inline in chat ([CopilotKit — Generative UI](https://www.copilotkit.ai/generative-ui), [patterns.dev — AI UI Patterns](https://www.patterns.dev/react/ai-ui-patterns/)). Hermes already has Hub widgets; the adoptable version is: let the agent emit a `widget` block referencing an existing Hub widget renderer (calendar slice, metric sparkline, For-You card) rather than describing data in text. Reuse, don't invent: the block registry calls the same render functions the Hub uses.

---

## 3. "Agent is working": streaming and thinking-state patterns

What the research converges on ([Fuselab — Agent UX 2026](https://fuselabcreative.com/ui-design-for-ai-agents/), [UX Magazine — Secrets of Agentic UX](https://uxmag.com/articles/secrets-of-agentic-ux-emerging-design-patterns-for-human-interaction-with-ai-agents), [Onething — 5 UX Patterns That Work](https://www.onething.design/post/agentic-ai-ux-design)):

1. **Expose phases, not spinners.** Stream distinct states — `thinking`, `tool`, `writing` — as labeled phases: "Searching your files… Analyzing… Drafting." A phase label that changes with reality beats any progress bar. Never show a determinate bar for nondeterminate work (Section 9, anti-patterns).
2. **Thinking is a block, not an animation.** Reasoning traces render as a collapsed, softly-styled `thinking` block ("Thought for 12s") expandable to the trace — visible-by-choice transparency ([Agentic Design — Chat Interface Patterns](https://agentic-design.ai/patterns/ui-ux-patterns/chat-interface-patterns)).
3. **Shimmer sweep for pre-token wait.** The AI-SDK "Shimmer" element — an animated gradient sweeping across the pending status text — is the current-gen standard for TTFT dead air ([Vercel AI Elements — Shimmer](https://ai-sdk.dev/elements/components/shimmer)). In our stack: a CSS `background-clip: text` gradient driven by Motion One `animate()` on `background-position`, applied to the live phase label. This is a *status* treatment only — never shimmer real content.
4. **Real telemetry as the working indicator.** Hermes has something almost nobody else has: a live local model with real tok/s and TTFT (`/api/metrics`). Showing *actual* token velocity as a tiny live figure next to the streaming cursor is honest progress — the premium answer to fake progress.
5. **A breathing presence mark.** One persistent element that encodes agent state by motion: idle = slow 4–6s opacity breath; thinking = tighter oscillation; tool-running = orbital sweep; awaiting approval = held still with an accent ring. Motion One spring-based, `prefers-reduced-motion` respected ([Primotech — Micro-Interactions & Motion 2026](https://primotech.com/ui-ux-evolution-2026-why-micro-interactions-and-motion-matter-more-than-ever/)). This becomes Hermes's signature liveness cue on the Agent page and, miniaturized, in the tab bar.

---

## 4. Timeline, replay, and the flight recorder

Manus lets users **roll back a replay dial and watch each step of a past session**; Devin keeps "a full replay timeline of every command, file diff, and browser tab" ([Robonomics](https://robonomics.substack.com/p/manus-devin-and-the-shape-of-agents), [DataCamp — Devin walkthrough](https://www.datacamp.com/tutorial/devin-ai)). The observability world converged on the same noun Hermes already uses: the **flight recorder** — organize telemetry around the conversation/mission, record the decision path not just the final action, and let users step through it like a DVR ([Honeycomb — Agent Timeline: Flight Recorder for AI Agents](https://www.honeycomb.io/blog/agent-timeline-flight-recorder-for-your-ai-agents), [COAI — The Flight Recorder for AI Agents](https://coairesearch.org/notes/flight-recorder/)).

Adoptable for Hermes (data already exists in `/api/recorder`):
- **Per-response "trace strip"**: under each completed agent message, a thin horizontal strip of dots/segments — one per action taken — hover reveals the action, click scrubs the Stage rail to it. This is the Manus replay dial miniaturized per message.
- **Session replay mode**: a scrubber over the whole conversation replaying recorder events in order (Console already has the timeline; Agent page deep-links into it rather than duplicating it).
- **Undo stays attached.** The Smashing "Action Audit & Undo" pattern: every reversible action shows status (done / in-progress / undone) and a one-click undo with a visible expiry window ([Smashing Magazine — Designing For Agentic AI](https://www.smashingmagazine.com/2026/02/designing-agentic-ai-practical-ux-patterns/)). Hermes recorder already supports undo — surface it on the block, not only in Console.

---

## 5. Always-on background activity without clutter

The ambient-agent literature is unambiguous: **do not narrate the firehose**. Patterns that survive ([Benjamin Prigent — 7 UX Patterns for Human Oversight in Ambient AI Agents](https://www.bprigent.com/article/7-ux-patterns-for-human-oversight-in-ambient-ai-agents), [Zapier — What is ambient AI](https://zapier.com/blog/ambient-ai/), [Medium — Redesigning AI Agent Interfaces for Proactive Interaction](https://medium.com/agenticais/redesigning-ai-agent-interfaces-for-proactive-interaction-dc91d7d26676)):

- **Overview panel, not notifications**: a passively-refreshing status surface — current state (idle/observing/working/paused), last few missions, and pending human tasks surfaced "Inbox Zero"-style. Users glance; the agent never pushes unless a rule says so. (Hermes: Hub keeps this role; the Agent page gets a one-line ambient strip.)
- **Single-line ambient ticker**: on the Agent page, a hairline strip above the composer: "Watchtower: scanned 3 feeds · last pass 2:41 PM" with a quiet crossfade on update. Click expands a popover of the last N background events (from `/api/watchtower` + `/api/recorder`), each deep-linking to Console. Never injected into the chat transcript.
- **Oversight flow for the rare escalation**: when background work genuinely needs the user, it becomes exactly one of five typed asks — inform / approve-reject / choose / provide-context / error-recover — as a card pinned above the composer, not a system message buried in scroll (Prigent). This is also where "notify-only proactive" lives: the card proposes; the user sends.
- **Digest over drip**: batch low-priority findings into the existing brief/midday/breaking schedule rather than real-time pings ([Earlybird Labs — Ambient Agents](https://earlybirdlabs.com/insights/what-are-ambient-agents)).

---

## 6. Multi-model / "which brain" indicators

Multi-model chat products put a model switcher in the header and stamp responses with the model that actually produced them, since routers may differ from the user's selection ([GetStream — Multi-Model AI Chat](https://getstream.io/blog/multi-model-ai-chat/), [OpenRouter — Auto Router](https://openrouter.ai/docs/guides/routing/routers/auto-router)). Hermes's two-brain architecture deserves more than a badge:

- **Per-message brain stamp**: a small two-tone SVG mark on each agent message — one glyph for local Qwen, one for Claude — with hover detail (model id, tok/s or bridge latency, token cost from the Claude Usage tracker). Data: `/api/models` + `/api/metrics` + `/api/claude/bridge`.
- **Escalation as a visible moment**: when the router hands off local → Claude mid-task, render a hairline `brain_switch` divider block: "Escalated to Claude — reasoning depth" with the *why* (the Explainable Rationale pattern: "Because you asked X, I used Y" — [Smashing Magazine](https://www.smashingmagazine.com/2026/02/designing-agentic-ai-practical-ux-patterns/)). Cost transparency at the moment of spend builds trust in the Max-plan budget.
- **The presence mark tints by brain**: local = one accent temperature, Claude = another; the "which brain" state Hermes already tracks drives it live.

---

## 7. Approval and trust surfaces (invariant-critical)

Patterns validated across enterprise testing ([Smashing Magazine](https://www.smashingmagazine.com/2026/02/designing-agentic-ai-practical-ux-patterns/), [HatchWorks](https://hatchworks.com/blog/ai-agents/agent-ux-patterns/), [Aufait — Agentic AI Design Patterns](https://www.aufaitux.com/blog/agentic-ai-design-patterns-guide/)):

- **Intent Preview / plan-and-execute**: before multi-step work, show the numbered plan with per-step visibility and options to proceed / edit / take over. "Without that preview, every autonomous action feels like a surprise the user did not consent to." Hermes: a `plan` block whose steps check off live as the recorder confirms them — real progress, tied to real events.
- **Autonomy Dial**: tiered independence per task class (Observe & Suggest → Plan & Propose → Act with Confirmation → Act Autonomously). Hermes's 17-class Trust tiers ARE this — the Settings spec should present them as an autonomy dial per category, not a permissions matrix.
- **Approval cards stay in trusted chrome**: inline approval cards (existing) render in the chat column with a distinct held-motion state; they must never appear inside the Desktop/computer-use viewport or any surface the agent can act on. Approve via click or keyboard in the WKWebView layer only.
- **Confidence signaling**: solid vs. hatched indicator on plans; low confidence pauses for verification ([Exalt Studio — 7 UX Patterns](https://exalt-studio.com/blog/designing-for-ai-agents-7-ux-patterns-that-drive-engagement)). Adopt sparingly — only where Hermes has a real confidence signal (e.g., router uncertainty), never a decorated random number.
- **Escalation pathway**: ambiguity produces a typed clarifying question ("Did you mean A or B?") as choice chips, not a guess ([Smashing Magazine](https://www.smashingmagazine.com/2026/02/designing-agentic-ai-practical-ux-patterns/)).

---

## 8. What reads premium vs. vibe-coded

From motion research and inspection of the premium tier (Claude Code, Warp, Manus) ([Primotech](https://primotech.com/ui-ux-evolution-2026-why-micro-interactions-and-motion-matter-more-than-ever/), [Bricxlabs — Micro Animation 2026](https://bricxlabs.com/blogs/micro-interactions-2025-examples), [Flex — Motion UI](https://flexagency.cz/en/proc-mikrointerakce-motion-ui-delaji-weby-prijemnejsi/)):

**Premium signals**
- One coherent motion system: every entrance/exit uses the same SPRING curve and 200–500ms envelope; motion is informative (state change), focused (one thing moves), and characterful (a consistent personality). Hermes's global `animate()` + SPRING already enforces this — the Agent page must not introduce ad-hoc CSS transitions.
- Typographic and spatial discipline: one mono face for tool output, hairline separators, consistent 4/8px rhythm, restrained accent use (per-category accent only on the glyph + status edge, not whole cards).
- Bespoke iconography with a shared stroke grammar (two-tone SVG, one stroke width, one corner radius) — the single strongest "designed, not generated" tell. Zero emoji is already law.
- Honest states everywhere: real timestamps ("2:41 PM", 12-hour), real counts, real durations; empty states designed, not blank.
- Density with progressive disclosure: collapsed-by-default, everything expandable, nothing lost.

**Vibe-coded tells (avoid)**
- Gradient-on-everything, glassmorphism without hierarchy, emoji as icons, mixed corner radii, three spinner styles on one screen, toast storms, skeleton shimmer on content that then pops in a different shape, center-aligned everything, fake typing indicators for non-streaming operations.

---

## 9. Anti-patterns (hard flags)

1. **Notification spam / narrating the firehose** — always-on ≠ always-talking; batch, digest, and let the ticker absorb it ([Zapier](https://zapier.com/blog/ambient-ai/), [Prigent](https://www.bprigent.com/article/7-ux-patterns-for-human-oversight-in-ambient-ai-agents)).
2. **Fake progress** — determinate bars, fabricated percentages, decorative "confidence" numbers, spinners that outlive the work. Use phase labels + real telemetry ([Fuselab](https://fuselabcreative.com/ui-design-for-ai-agents/)).
3. **One stream for chat + activity** — fails as both ([HatchWorks](https://hatchworks.com/blog/ai-agents/agent-ux-patterns/)).
4. **Chat-first for everything** — configuration, permissions, and analytics belong in structured surfaces (the Settings page), not conversation ([HatchWorks](https://hatchworks.com/blog/ai-agents/agent-ux-patterns/)).
5. **Approval controls reachable by the agent** — no approve/deny affordance may exist anywhere computer-use can click; Hermes invariant, reinforced by the oversight literature's separation of resolution surfaces from agent workspaces.
6. **Unexplained autonomous actions** — every background action carries a one-line rationale ("Because you said X…") or it doesn't ship ([Smashing Magazine](https://www.smashingmagazine.com/2026/02/designing-agentic-ai-practical-ux-patterns/)).
7. **Raw log dumps as transparency** — expandable structure or nothing; "Why Johnny Can't Use Agents" documents users abandoning agents whose activity displays were unreadable ([arXiv 2509.14528](https://arxiv.org/pdf/2509.14528)).
8. **Motion without settings** — expose reduce-motion; respect `prefers-reduced-motion` in every Motion One call.

---

## 10. Build-ready translation for Hermes

### 10a. Agent page (new tab, replaces chat-as-column as the primary conversation home)

**Layout:** two lanes. Left/main: transcript + composer. Right: "Stage" rail (collapsible; auto-opens when a visual tool runs, spring-slides away when idle 30s). Header: brain indicator + presence mark + density toggle + ambient ticker.

**Component inventory (all as typed blocks via a `registerBlock(type, renderFn)` registry in a new `agent.js` aux module):**

| Component | Data source | States |
|---|---|---|
| Message block (user/agent) | `/api/chat` + poll | streaming, done, error |
| Thinking block | chat poll (thinking events) | live (shimmer label), collapsed ("Thought for Ns"), expanded |
| Terminal block | chat poll + `/api/console` | queued, running (elapsed), ok, failed; clipped/expanded |
| Diff block | chat poll + `/api/recorder` | pending-approval, applied, undone; per-hunk collapse |
| Web-search block | chat poll | searching, results (chips) |
| Computer-use block | `/api/desktop/*` | live thumbnails, finished filmstrip; click → Stage scrub |
| Widget block (generative UI) | reuse Hub widget renderers + `/api/foryou`, `/api/metrics` | loading, rendered |
| Plan block | chat poll | proposed (approve/edit/take-over), executing (live checkoff via recorder), done |
| Approval card | `/api/chat` approve endpoint + `/api/permissions` | held (still, accent ring), approved, denied, expired |
| Brain-switch divider | `/api/models`, `/api/claude/bridge` | one-shot render w/ rationale + cost |
| Trace strip (per response) | `/api/recorder` | dots per action; hover detail; click → Stage/Console |
| Ambient ticker | `/api/watchtower`, `/api/recorder` | rotating line, expandable popover; never in transcript |
| Presence mark | which-brain state + chat phase | idle / thinking / tool / awaiting-approval; brain-tinted |
| Escalation/oversight card | `/api/watchtower` | inform / approve / choose / context / error |

**Wiring:** `agent.js` registers the route via the existing aux-module route registration, wraps the existing chat poll hooks, and adds a block-type dispatcher over the poll payload. Stage rail lazily mounts Console/Desktop render functions (import their existing hooks; no duplicated logic). Density + reduce-motion persisted through `/api/settings`. All animation through global `animate()` + SPRING; shimmer = `background-position` animation on the phase label only.

### 10b. Settings page (Mind migration targets)

Chat-first config is an anti-pattern; Mind's cards are structured-surface material. Migration map for the Settings spec:

| Mind card | Settings section | Pattern upgrade |
|---|---|---|
| Trust (17 classes + audit) | Autonomy | Autonomy Dial per class (Observe→Autonomous), audit link ([Smashing](https://www.smashingmagazine.com/2026/02/designing-agentic-ai-practical-ux-patterns/)) |
| Memory editor | Memory | view/edit/erase with influence transparency ([Aufait](https://www.aufaitux.com/blog/agentic-ai-design-patterns-guide/)) |
| You-Model | Profile | goals/now/people as structured editors |
| Watchtower rules + schedule | Proactive | trigger rule builder (key-operator-value) + digest schedule ([Prigent](https://www.bprigent.com/article/7-ux-patterns-for-human-oversight-in-ambient-ai-agents)) |
| Google connect | Connections | status-first integration list |
| Shortcuts action-bus | Actions | catalog w/ per-action tier |
| Model promotion drills / analytics / tokens | Models & Usage | brain routing policy, spend, drill history (`/api/models`, `/api/metrics`) |
| Config-as-code | Advanced | export/import (`/api/settings`) |

### 10c. Signature differentiators (what makes Hermes unique, not a clone)

1. **Honest velocity**: live local tok/s as the streaming indicator — only a local-first app can do this.
2. **The presence mark**: one breathing, brain-tinted SVG as the whole app's liveness anchor.
3. **Brain-switch dividers with rationale + cost**: two-brain transparency nobody else surfaces.
4. **Trace strip per response**: flight-recorder replay woven into the transcript, not a separate log.
5. **Liquid Glass stage**: tool show-ins as glass panels sliding in on SPRING — the "watch it work" moment framed like a cockpit, with the un-bypassable approval chrome always above the glass.
