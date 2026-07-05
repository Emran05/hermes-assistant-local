# Hermes Assistant — Long-Term Development Plan (2026-07-05)

Companion to `docs/FUTURE.md` (the 4-platform research corpus). Grounded in the
current architecture (`CLAUDE.md`) and the working system as of 2026-07-05:
three views (Hub / Mind / Console), 20 rich widgets with pop-outs, serve-backed
streaming chat with approvals, Telegram gateway, cua-driver computer-use,
model switcher with pause/resume, KV cap + memory watchdog, hub pre-warmer.

---

## 1. Product thesis & positioning

**Hermes is not competing on tokens per second, and it never will.** Every one
of the four research streams lands on the same contrarian conclusion from a
different direction: Reddit's hive-mind "conflates capability with autonomy —
everyone benchmarks raw generation while begging in the same threads for
memory, proactivity, and safe OS control, then ships none of it" (FUTURE.md,
Reddit contrarian take). X's is blunter: decode is bandwidth-bound, "nobody
wins the tok/s war durably," and the defensible magic is **latency-to-meaning
and trust** (X contrarian take). The Substack stream adds the structural
argument: closed models will keep pulling ahead on raw capability, so staking
Hermes' value on capability parity is a losing bet — the durable advantages are
privacy, latency, and always-on presence, which are *structural*, not
model-quality, advantages (Substack pitfalls, Interconnects). We build for the
axis the benchmark crowd systematically undervalues.

**What we uniquely own is the intersection of four things no cloud product can
assemble:** (1) *local-first* — every byte stays on the machine, inference is
unmetered, and "wasteful" overnight background grinding is a feature for us and
a cost center for them (Substack contrarian take); (2) *self-improving* — the
Mind view already surfaces 70+ learned skills, persistent memory, and usage
insights from Hermes' own state, the exact "editable, visible memory" that is
the #1 cited reason users abandon assistants when it's missing (Reddit feature
ideas); (3) *computer-use* — cua-driver operates the whole Mac in the
background without stealing the cursor, something a sandboxed cloud agent
structurally cannot do; (4) *consumer-grade glass UI* — a native Swift/WKWebView
shell with a Liquid Glass dashboard that looks like a product, not a
self-hosted admin panel. The GitHub stream confirms our stdlib-Python +
vanilla-JS + native-shell stack is "the genuinely lighter architecture the
ecosystem keeps rediscovering the hard way" (GitHub contrarian take) — while
Electron/Tauri competitors fight 150MB runtimes and DMG transparency bugs.

**The strategic risk is commoditization, and the answer is trust.** Karpathy's
crowd is already calling this category "Claw" (X pitfalls), Ollama moved to MLX
so every Mac competitor now shares our perf profile (Reddit pitfalls), and
hermes-agent upstream ships our feature space monthly. Raw features will be
copied within weeks. What compounds and can't be copied quickly is *earned
autonomy*: an assistant whose every OS action is recorded and reversible
(flight recorder), whose permissions are graduated rather than binary, whose
memory you can read and edit, and whose completions are verified rather than
asserted. The loudest concrete complaint about agent computer-use is "there is
no undo button — you have to build your own" (Reddit feature ideas). We build
the undo button, and everything else the trust moat implies, before we build
another widget.

**Positioning in one sentence:** *the assistant that remembers you, acts before
you ask, and can be trusted to touch your Mac — because you can see everything
it knows and undo anything it does.* The Ink & Switch local-first ideals
(fast, offline, private, user-owned — Substack, notable projects) are the
marketing frame; the Mind view and the flight recorder are the proof.

---

## 2. Scoring of candidate features

Scale: **Value** = user value to *this* user's daily life (1–5). **Diff** =
differentiation vs. the "Claw" field (1–5). **Effort** S/M/L. Verdicts are
final until a phase review says otherwise.

| # | Feature | Source | Value | Diff | Effort | Risk | Verdict |
|---|---------|--------|-------|------|--------|------|---------|
| 1 | **Editable memory UI** (read/edit/pin/delete facts in Mind) | FUTURE Reddit + GitHub (`/journey`) + X | 5 | 5 | M | Low — Mind view + `/api/capabilities` already surface the data; delta is write endpoints + UI | **NOW** — #1 abandonment reason when missing; we're 70% there |
| 2 | **Computer-use flight recorder + undo** (action log, pre-write snapshot, revert) | FUTURE Reddit + X (`/undo`) + Substack | 5 | 5 | M/L | Med — snapshot scope creep; v1 = file ops only | **NOW** — the trust unlock for the whole computer-use story |
| 3 | **Graduated permission tiers** (auto-run read-only / confirm-once / always-confirm) | FUTURE Reddit | 5 | 4 | S/M | Low — extends the existing approvals path | **NOW** — kills the binary approve-everything/yolo false choice |
| 4 | **Exercise + harden the approval loop** (incl. approve-from-Telegram, stable tool_call_id resume) | CLAUDE.md debt + FUTURE X (simonw llm #1480/1481) | 4 | 3 | S/M | Low | **NOW** — wired but never fired for real; unverified safety code is not safety |
| 5 | **Watchtower proactive triggers** (calendar gap, ticker star threshold, battery, run-complete) | FUTURE Reddit + Substack | 5 | 4 | M | Med — annoyance risk; needs precision guardrails | **NEXT** (Phase 2) — "initiative vs. reactive chatbot" is the top-cited gap |
| 6 | **Morning brief pushed via Telegram** (Watchtower's first trigger) | FUTURE Reddit | 4 | 3 | S | Low — briefing loop already exists | **NEXT** (Phase 2) |
| 7 | **Menu-bar quick-ask** (global hotkey, tiny panel) | Roadmap + FUTURE X (voice pairing) | 4 | 3 | S/M | Low — Swift shell already exists | **NEXT** (Phase 2) — the "always at hand" daily-use moment |
| 8 | **Clipboard actions** (act on copied text: summarize/translate/task-ify) | Roadmap | 4 | 2 | S | Low | **NEXT** (Phase 2) — cheap, daily-use |
| 9 | **Message Center FDA fix** (signed app reads chat.db → JSON for dashboard) | CLAUDE.md debt | 4 | 3 | M | Med — TCC attribution quirks (memory: launchd python can't inherit FDA) | **NEXT** (Phase 2) — the app-reads-messages approach is the only viable path |
| 10 | **Shortcuts action-bus** (agent drives macOS Shortcuts as tools) | Roadmap | 5 | 4 | M | Med — needs graduated permissions first (#3) | **NEXT** (Phase 3) — turns Hermes into hands, not just eyes |
| 11 | **Tiny restraint-router / two-tier model routing** (sub-2B decides *whether* to act; 8B/30B executes) | FUTURE Reddit (tool-calling benchmark) + Substack (Willison/Lambert) | 4 | 4 | M | Med — routing bugs look like model bugs | **NEXT** (Phase 3) — restraint is the metric that matters for agents |
| 12 | **Per-model tool-call parsers + model-promotion eval gate** | FUTURE Reddit (5/21 models emit non-standard formats) | 4 | 3 | S/M | Low | **NEXT** (Phase 3) — prerequisite for safely swapping models; parsing moved a model rank 13→#1 |
| 13 | **MoE "performance profile"** (Qwen3-30B-A3B — already on disk — as the heavy-session model; 8B stays always-on default) | FUTURE Reddit + X (MoE sweet spot on 64GB) | 4 | 2 | S | Low — switcher already does this manually | **NEXT** (Phase 3) — formalize as a routing tier, not a manual toggle |
| 14 | **Screenshot-context for cua-driver** (window screenshots grounding actions) | FUTURE GitHub (Screeny MCP) | 3 | 3 | S | Low | **NEXT** (Phase 3) — pairs with flight recorder evidence |
| 15 | **Local voice quick-ask** (whisper.cpp + Piper, hold-hotkey, sentence-level TTS) | Roadmap + FUTURE X (1–1.5s round-trip) | 4 | 4 | M | Med — latency tuning is fiddly | **NEXT** (Phase 4) — the single most demo-able local-first moment |
| 16 | **Corpus RAG via local-only MCP** (mcp-local-rag over folders/PDFs) | Roadmap + FUTURE GitHub | 4 | 3 | S/M | Low — vet the MCP (supply-chain rot) | **NEXT** (Phase 4) |
| 17 | **Agent-authored widgets** (constrained `json_schema` widget spec → dashboard renders) | FUTURE GitHub (glance custom-api pattern) + X (constrained decoding) | 4 | 5 | M | Med — spec design is the hard part | **NEXT** (Phase 4) — generative-UI as moat; constrained decoding de-risks it |
| 18 | **`/learn` skill-distillation button** ("learn this workflow" from a finished session) | FUTURE GitHub + X (hermes v0.18 `/learn`) | 4 | 4 | S/M | Low — upstream primitive exists; we add the dashboard surface | **NEXT** (Phase 4) — makes self-improvement user-drivable |
| 19 | **Verification-gated task completion** (tasks can't move to Done without a passing check) | FUTURE GitHub + X (v0.18 completion contracts) | 4 | 4 | M | Med — depends on task-board usage patterns | **LATER** — right idea, wait until the task board carries real agent work |
| 20 | **Long-horizon background jobs surfaced on the task board** (overnight grinding, cross-session progress) | FUTURE Substack (Interconnects) | 4 | 4 | M | Med | **LATER** — needs #19's verification story first to be trustworthy |
| 21 | **Background subagent fan-out on the Console timeline** | FUTURE GitHub (hermes `delegate_task`) | 3 | 3 | M | Low | **LATER** — visualize it when we actually fan out |
| 22 | **Editable plans widget** (edit/reorder agent steps mid-run) | FUTURE Substack (Latent Space) | 3 | 3 | M | Med — serve API may not expose plan state cleanly | **LATER** — revisit after graduated permissions bed in |
| 23 | **Proactive-trigger eval harness** (replay synthetic timelines) | FUTURE Substack (arXiv 2604.00842, unverified) | 3 | 3 | M/L | Med — source unverified | **LATER** — Phase 2 ships a lightweight fire-log + precision review instead |
| 24 | **Multi-day drill-downs** (usage/analytics over weeks in Mind) | Roadmap | 3 | 2 | S | Low — `/api/mind_extra` already has tokens_by_day | **NEXT** (Phase 2) — small, rounds out the analytics story |
| 25 | **Magazine/feed layout pass** (denser glanceable feed ordering) | FUTURE Reddit (Glance lesson) | 3 | 2 | S | Low | **LATER** — we already had a density pass; do it when layout friction is felt, not speculatively |
| 26 | **Config-as-code hygiene** (layout.json/settings.json/models.json committed, documented, diffable) | FUTURE Reddit (Homepage's durable advantage) | 3 | 2 | S | Low | **NOW** — repo just initialized; costs an hour, reinforces identity |
| 27 | **Google Workspace OAuth (read + draft, never send)** | CLAUDE.md debt | 4 | 2 | S (user-gated) | Low — safety posture = absence of send capability | **NEXT** (Phase 2, user does the browser step) |
| 28 | **Discord gateway** | CLAUDE.md debt | 2 | 1 | S (user-gated) | Low | **LATER** — user hasn't pushed for it; Telegram covers mobile |
| 29 | **Bluetooth/AirPods, Wi-Fi, now-playing tiles** | Roadmap | 2 | 1 | S | Low | **LATER** — widget filler; no differentiation |
| 30 | **Tiered hot/cold KV cache (omlx front-end or homegrown SSD spill)** | FUTURE GitHub (jundot/omlx) | 3 | 2 | L (S if omlx) | Med — new serving dependency | **LATER** — our `--prompt-cache-bytes` cap already killed the 49GB blowup; adopt only if context pain returns |
| 31 | **Council / Mixture-of-Agents model in the switcher** | FUTURE GitHub (hermes v0.18 `moa`) | 2 | 2 | M | Med | **NEVER** — a cloud-model council breaks the local-first identity and solves a capability problem we've explicitly chosen not to compete on (Substack contrarian take) |
| 32 | **Multi-Mac tensor-parallel cluster mode** | FUTURE X (RDMA over TB5) | 1 | 3 | L | High | **NEVER** — one Mac exists; the research itself calls it "a $10K party trick 99% of users will never wire up" (X contrarian take) |
| 33 | **MemGPT-style tiered memory architecture** | FUTURE Substack (pitfall) | 2 | 1 | L | High — auditability regression | **NEVER** — research verdict: steeper curve, harder to audit than "rules you wrote over plain files"; our file-based memory is legitimately defensible. Add tiers only if retrieval measurably bottlenecks |
| 34 | **Tauri/Electron shell migration** | FUTURE GitHub (pitfall #13415) | 1 | 1 | L | High — transparency breaks in DMG builds | **NEVER** — we already own a native Swift/WKWebView shell; migrating is strictly downside |
| 35 | **KV-cache persist-to-disk for session restore** (old task #23) | Memory/research 2026-07-04 | 2 | 1 | M | Med | **NEVER** — already investigated and rejected; byte-capped cache + prefix reuse is the right shape. (Distinct from #30's spill, which is also LATER) |
| 36 | **Higgsfield image-gen MCP** | Setup history | 1 | 1 | S | Med — OAuth DCR structurally broken from automation | **NEVER** — skipped by user decision; image gen is not the product |
| 37 | **Beeper/Matrix-based message center** | Setup history | 2 | 1 | L | Med | **NEVER** — already rejected as heavy; local-first chat.db read (#9) is the answer |
| 38 | **Copying glance/homepage widget code** | FUTURE GitHub (license note) | – | – | – | **Legal** | **NEVER** — AGPL-3.0/GPL-3.0; architecture ideas only, no code lifting |

---

## 3. The plan — four phases

Dates assume start 2026-07-06, ~1–2 weeks each, solo pace with agent leverage.
Each phase ends with: tag, CLAUDE.md updated, all services restart-verified.

### Phase 1 (Jul 6 → Jul 15) — "Earn trust"
**Theme:** before Hermes gets more autonomy or more initiative, everything it
knows must be editable and everything it does must be reversible. This is the
moat (Section 1) and every later phase depends on it.

Workstreams:
1. **Editable memory (Mind view).** New endpoints to edit/pin/delete facts in
   `~/.hermes/memories/USER.md` (and list memory files via the existing
   `mind_extra` surface); Mind UI gets inline edit/delete/pin affordances.
   *Done means:* I can correct a wrong remembered fact from the dashboard and
   the agent's next turn reflects it; a deleted fact never resurfaces after the
   session-end memory flush.
2. **Flight recorder v1 + undo.** Persistent append-only action log for every
   `computer_use` and file-writing tool call (timestamp, tool, args, screenshot
   via existing driver where cheap); pre-write snapshot of touched files/folders
   (copy-on-write into `~/.hermes/dashboard/snapshots/`, size-capped, GC'd);
   `/undo` restores the last N file snapshots. Console gets a "recorder" lane.
   *Done means:* I ask the agent to edit a file, then click Undo in the
   Console, and the byte-identical original is back; a deliberate wrong-file
   deletion drill is fully recovered. Irreversible categories (financial,
   `rm -rf`, sends) are hard-blocked lists, not judgment calls (FUTURE Reddit
   pitfalls).
3. **Graduated permission tiers.** Per-tool trust levels in settings.json:
   `auto` (read-only tools), `remember` (confirm once, then auto for that
   tool+context), `always` (irreversible). Approval prompts show the tier and a
   "remember this" choice. *Done means:* read-only tools never prompt; a
   remembered write tool prompts exactly once; the tier table is visible and
   editable in the dashboard.
4. **Fire the approval loop for real.** Stage a genuinely dangerous command
   through hub chat AND through Telegram; verify approve/deny/resume including
   serve-session persistence across a dashboard restart. *Done means:* both
   surfaces have completed one real approve and one real deny, and the behavior
   is documented in CLAUDE.md.
5. **MetalGuard + baseline metrics.** Wrap the mlx path against the known
   Metal-OOM hard crash (mlx-lm issue #854 — uncaught C++ exception kills the
   server; FUTURE GitHub pitfalls): watchdog detects crash-loops and degrades
   gracefully (pause + friendly chat error, not KeepAlive thrash). Start
   logging the Section 6 metrics (TTFT, turn latency, footprint) to a local
   metrics.jsonl. *Done means:* killing the model server mid-turn produces a
   readable error in chat, not a hang; a week of metrics exists before Phase 2.
6. **Config-as-code hygiene.** Commit and document layout.json / settings.json
   / models.json handling (what's tracked vs. gitignored), per FUTURE Reddit's
   Homepage lesson. *Done means:* a fresh clone + install-services.sh + secrets
   restore reproduces the setup, and the README section says so.

**Explicitly NOT in Phase 1:** Watchtower, voice, action-bus, any new widgets,
any model-routing changes. No new capabilities until existing ones are safe.

### Phase 2 (Jul 16 → Jul 26) — "Initiative"
**Theme:** Hermes starts conversations. The consistent gap named across Reddit
is "initiative vs. reactive chatbot" (FUTURE Reddit feature ideas) — but the
hard problem is not being annoying, so precision is instrumented from day one.

Workstreams:
1. **Watchtower trigger engine.** A dashboard-server loop evaluating declarative
   triggers from settings.json: calendar gap detected, starred-ticker threshold
   crossed (starred_tickers already exists), battery low while unplugged,
   agent background run finished, morning-brief time. Each firing is logged
   (trigger, context, delivered-where, user-reaction). *Done means:* three
   trigger types fire correctly over a live week; every firing is in the log;
   quiet hours are respected.
2. **Delivery surfaces.** Triggers deliver to (a) a Hub notification lane and
   (b) Telegram via the existing gateway. Morning brief becomes a pushed
   Telegram message, not just a widget. *Done means:* I get the brief on my
   phone before I open the laptop, ≥5 of 7 days.
3. **Precision guardrail (the lightweight eval).** Weekly Watchtower review
   card in Mind: fired/acted-on/dismissed per trigger, with one-tap "mute this
   trigger." This is the pragmatic stand-in for the full replay harness
   (deferred, table #23). *Done means:* per-trigger precision is visible and a
   noisy trigger can be muted in one click.
4. **Menu-bar quick-ask + clipboard actions.** Swift shell grows an
   NSStatusItem: global hotkey opens a small ask panel (routes to the existing
   job-based chat API); clipboard-aware actions (summarize/translate/make-task
   from copied text). *Done means:* hotkey → answer streaming in under 2s
   perceived, without the main window; a copied paragraph becomes a task in two
   clicks.
5. **Message Center unblock.** The signed native app (which holds FDA properly,
   unlike launchd-spawned Python — known TCC attribution limitation) reads
   chat.db on a timer and writes recent messages to a JSON the dashboard
   consumes. *Done means:* Messages widget shows real iMessages with no FDA
   errors across a reboot.
6. **Multi-day drill-downs + Google OAuth (user-gated).** Extend Mind analytics
   to multi-week views on the existing tokens_by_day/skill_usage data; walk the
   user through Google OAuth Desktop JSON for read+draft-only Workspace (send
   stays structurally absent — user's safety posture, CLAUDE.md). *Done means:*
   a 30-day usage view renders; Gmail read + draft works with zero send paths.

**Explicitly NOT in Phase 2:** action-bus, model routing, voice, agent-authored
widgets, verification-gated tasks. Watchtower triggers may only *notify*, never
*act* — acting proactively waits for Phase 3's permission-integrated bus.

### Phase 3 (Jul 27 → Aug 9) — "Hands & speed"
**Theme:** Hermes acts on the Mac through a governed action bus, and the model
stack gets the routing shape the research says is right (restraint router +
MoE executor — FUTURE Reddit contrarian take), instrumented end-to-end.

Workstreams:
1. **Shortcuts action-bus.** Curated macOS Shortcuts exposed as tools with
   graduated permissions from Phase 1 (read-only auto; reversible remembered;
   irreversible always-confirm). Every bus action lands in the flight recorder.
   *Done means:* five useful Shortcuts callable from chat/Telegram; each shows
   tier-correct approval behavior; each appears in the recorder timeline.
2. **Two-tier routing.** A sub-2B restraint router (candidates: Qwen3-1.7B
   class per the tool-calling benchmark, FUTURE Reddit) decides tool/no-tool
   and dispatches simple actions; escalation goes to the active main model.
   Formalize the **MoE performance profile**: Qwen3-30B-A3B (already on disk)
   for heavy/agentic sessions, Hermes-3-8B (or successor) always-on. *Done
   means:* simple commands ("what's frontmost", "add a task") complete in <3s
   end-to-end; router restraint is spot-checked against ~20 canned prompts;
   escalation is visible in the Console.
3. **Per-model tool-call parsers + promotion gate.** Fallback parsers for
   non-standard tool-call formats (5/21 models emit them — FUTURE Reddit
   pitfalls) and a small canned tool-calling eval that any model must pass in
   the switcher before it can be promoted to the agent loop. Include a
   per-model KV-precision setting (quant behavior is model-specific — FUTURE
   Reddit pitfalls). *Done means:* adding a new model to models.json runs the
   gate automatically and the switcher shows pass/fail before promotion.
4. **Speculative decoding.** Wire `draft_model` + `num_draft_tokens` (flags
   already exist in mlx-lm server — FUTURE GitHub perf) with a small
   same-family draft; instrument acceptance rate live and auto-disable below
   the 0.65 break-even (FUTURE Substack perf). Pair a *quantized* draft with
   quantized targets (DFlash caveat, FUTURE Reddit perf). *Done means:*
   measured decode speedup ≥1.5x on the main chat path or the feature is
   cleanly off with the measurement recorded.
5. **Screenshot-grounded computer-use.** Window-screenshot context (Screeny-
   style, vetted for license/maintenance — MCP supply-chain rot, FUTURE GitHub
   pitfalls) feeding cua-driver actions, with screenshots archived to the
   flight recorder as before/after evidence. *Done means:* a recorded action
   shows before/after frames in the Console scrubber.

**Explicitly NOT in Phase 3:** voice, corpus RAG, agent-authored widgets,
`--prompt-concurrency` batching (blocked on re-validating KV isolation — the
cross-contamination history in mlx-lm #965, FUTURE GitHub pitfalls).

### Phase 4 (Aug 10 → Aug 23) — "Self-extension"
**Theme:** Hermes extends itself — its knowledge (RAG), its skills (`/learn`),
its own UI (agent-authored widgets) — and gets a voice. This is the
"self-improving" pillar made user-visible.

Workstreams:
1. **Agent-authored widgets.** A declarative widget spec (`{source, template,
   refresh, category}` — glance custom-api architecture as inspiration only,
   AGPL, no code lifting) rendered by a tiny generic template function;
   generation via mlx-lm's `json_schema` constrained decoding so the model
   *cannot* emit an invalid spec (FUTURE X feature ideas). "Ask Hermes to build
   a widget" in the Widget Center. *Done means:* the agent builds a working
   novel widget (e.g., a niche API tracker) from one prompt; it survives
   restart; a malformed spec is impossible by construction.
2. **`/learn` from the dashboard.** One-click "learn this workflow" on a
   completed multi-step session (upstream `/learn` primitive, hermes v0.18 —
   FUTURE GitHub/X), with the new skill appearing in the Mind skill browser
   tagged as user-taught. *Done means:* a real repeated workflow is distilled
   once and successfully reused in a later session.
3. **Corpus RAG (local-only).** mcp-local-rag or equivalent — vetted, pinned —
   indexing user-granted folders; wired as a grounded-search tool with
   citations into chat. *Done means:* "what did that PDF in ~/Documents say
   about X" answers with the right file cited, fully offline.
4. **Local voice quick-ask v1.** whisper.cpp STT + Piper TTS behind the Phase 2
   menu-bar hotkey; sentence-level TTS streaming (the known 0.3–0.7s perceived-
   latency trick — FUTURE X feature ideas). *Done means:* hold-hotkey → spoken
   answer begins in ≤2s on the 8B path; everything stays on-device.
5. **Trust-loop closure.** Review the quarter's flight-recorder and Watchtower
   logs; promote well-behaved remembered tools to auto where earned; write the
   Phase 5 plan from measured usage rather than speculation. *Done means:* a
   one-page review exists with real numbers against Section 6 targets.

**Explicitly NOT in Phase 4:** verification-gated task board, long-horizon
background jobs, subagent fan-out UI, editable plans (all LATER — they need
the trust + routing substrate to bed in first), Discord, cluster anything.

---

## 4. Performance & reliability track (continuous)

Runs alongside all phases. Ordered by expected impact per unit effort.

| Item | Status / action | Expected impact | Grounding |
|------|-----------------|-----------------|-----------|
| **KV cap flags** (`--prompt-cache-size 6`, `--prompt-cache-bytes 6GB`) | **Already shipped** in mlx-server.sh; keep, and re-verify after every mlx-lm upgrade | Root fix for the 49GB footprint blowup; bounded memory forever | FUTURE GitHub perf (#854/#906/#910); CLAUDE.md |
| **MetalGuard OOM handling** | Phase 1: wrap/watch for the uncaught Metal-OOM crash; graceful degrade instead of KeepAlive crash-loops | Zero whole-server hard crashes from memory pressure | FUTURE GitHub pitfalls (mlx-lm #854 OPEN) |
| **Prefix-stable system prompt** | Audit `access_preamble()`/briefing injection for byte-instability (timestamps, dynamic lists); move volatile content to the end of the prompt or into the user turn so mlx-lm's prompt cache hits | Reported 8k-token prefix: ~31s cold → ~3.4s warm; directly cuts our TTFT on every hub/contextual-ask turn | FUTURE X perf (mlx-lm SERVER.md; MLX dev guide) |
| **Speculative decoding** (`draft_model` + `num_draft_tokens`) | Phase 3; instrument acceptance, auto-off <0.65; quantized draft with quantized target | 1.5–2.4x decode on the chat path, near-free (flags exist) | FUTURE Substack perf (LM Studio guidance); FUTURE Reddit perf (DFlash caveat) |
| **KV-cache quantization (kv4)** | Adopt **only from a tagged mlx-lm release** (the `server-kv-bits` branch is buggy — `swapaxes` error); store per-model KV precision, never global | ~3.2x context headroom at zero-to-negative perf cost on our class of hardware | FUTURE Substack perf (mlx #3134); FUTURE GitHub pitfalls (#1082); FUTURE Reddit pitfalls (q8_0 not uniform across models) |
| **End-to-end latency benchmarking, not tok/s** | Metrics.jsonl from Phase 1: TTFT + total-turn-time per model/quant; watch 4-bit CoT token inflation — consider 6–8-bit for the planner if inflation shows | Catches the "4-bit is faster per token but slower per answer" trap almost nobody measures | FUTURE Substack perf (arXiv 2606.25519 token inflation); X contrarian (TTFT is the axis) |
| **Backdrop-filter budget** | Continuous CSS discipline: cap simultaneously-blurred glass layers, blur radius ≲20px, promote glass with `translateZ(0)`/`will-change`, never animate blur radius, prefer static pre-blur on scrolling surfaces | Smooth 60fps hub scroll; Safari/WebKit blur is an order of magnitude slower than Chrome and is our top jank risk with 20 glass widgets | FUTURE Substack perf (WebKit backdrop-filters doc); FUTURE X perf; FUTURE Reddit perf (Glance lesson) |
| **Native vibrancy experiment** | Try one NSVisualEffectView behind a transparent WKWebView in the Swift shell, replacing the largest full-screen blur passes (aurora backdrop) | Blur done once on the compositor instead of per-frame in WebKit → lower GPU/energy | FUTURE GitHub perf (tauri window-vibrancy pattern, applied to our own shell) |
| **Batching (`--prompt-concurrency`)** | LATER, and only after re-validating KV isolation under concurrency on our version | Up to ~2.2x aggregate when Telegram + dashboard hit the model together | FUTURE Substack perf; FUTURE GitHub pitfalls (mlx-lm #965 cross-contamination history) |
| **Python server patterns** | Keep the stdlib single-process design: `safe()`-wrapped providers, `_cached` TTLs, `_hub_pool` fan-out, pre-warmer. Resist frameworks. If chat polling ever feels heavy, SSE is the one upgrade to consider | Reliability comes from boring code; the stack's lightness is itself a research-validated differentiator | FUTURE GitHub contrarian take; CLAUDE.md architecture |
| **mlx-lm release tracking** | Watch @awnihannun + mlx-lm releases; re-test the `transformers<5` pin on every upgrade | Days-not-months adoption of KV/spec-decode wins; avoid the known import crash | FUTURE X notable; CLAUDE.md gotchas |

---

## 5. Risks & watch-items

| Risk | Likelihood / impact | Mitigation |
|------|---------------------|------------|
| **macOS TCC changes break automation on every major release** — Accessibility/Screen Recording grants revoked or re-prompted; macOS 26 broke permission inheritance for child processes; TCC DB is SIP-protected so grants cannot be scripted | High / High — cua-driver and Message Center are directly exposed | Runtime permission preflight (`hermes computer-use doctor` pattern) surfaced as a Hub health tile; graceful re-request flows that *open* the right Settings pane (never pretend to fix); FDA-dependent reads live in the signed app, not launchd Python (Phase 2 #5). FUTURE Reddit pitfalls; FUTURE X pitfalls (claude-code #50735, Peekaboo #75) |
| **mlx-lm churn** — flags renamed, Metal-OOM crash still open upstream, quantized-KV branch buggy, `transformers<5` pin fragile | Med / High — it's the engine | Pin versions; upgrade deliberately with a smoke test (model load + one tool-call turn + footprint check); MetalGuard (Phase 1); adopt KV-quant only from tagged releases. FUTURE GitHub pitfalls (#854, #1082); CLAUDE.md gotchas |
| **hermes-agent upstream velocity** — ~1,700 commits per 2-week release window, 25k+ open issues; weekly breaking-change risk | High / Med | Stay pinned to a known-good tag (currently v0.18.x); diff release notes before any upgrade; upgrade only at phase boundaries; treat upstream as a feature-validation radar (they shipped `/learn`, `/journey`, `/undo` before us — watch what sticks). FUTURE GitHub pitfalls |
| **Model licensing** — Hermes-3-8B inherits the Llama 3 Community License: "Built with Llama" attribution, acceptable-use policy, output-training restrictions; every model swap changes the license | Med / Low (personal use) but reputational if shipped | Add the attribution string to the app About/Mind view now; verify base license on every switcher addition (make it a field in models.json + part of the Phase 3 promotion gate). FUTURE Substack pitfalls; FUTURE X pitfalls (Hermes-4 HF license) |
| **Quantization regressions are model-specific** — a quant harmless on Qwen wrecks Gemma-class; KV q8_0 is not universally "free" | Med / Med — silent agent-quality regression | Per-model KV-precision setting + the canned tool-calling promotion gate (Phase 3 #3) before any model reaches the agent loop. FUTURE Reddit pitfalls |
| **Irreversible computer-use action destroys user data** — documented cases of agents rm-ing wrong files | Low / Severe — one incident ends trust permanently | Flight recorder + snapshots + undo (Phase 1) *before* the action bus (Phase 3); hard-blocked category list (financial/sends/mass-delete); graduated tiers keep irreversible = always-confirm. FUTURE Reddit feature ideas + pitfalls |
| **Watchtower becomes annoying and gets muted wholesale** | Med / Med — kills the initiative pillar | Precision logging + per-trigger mute from day one (Phase 2 #3); triggers notify-only until Phase 3; quiet hours default-on. FUTURE Substack feature ideas |
| **Category commoditization ("Claw")** — the field converges on our shape; Ollama-on-MLX equalizes raw perf | High / Med | Compete on the trust/memory/UI moat (Section 1), not features-per-week; track macos26/agent and hermes-agent as benchmarks; keep the stdlib+native lightness as a felt difference. FUTURE X pitfalls + Reddit pitfalls |
| **MCP supply-chain rot** — 22k+ servers, many abandoned; we're wiring MCPs into an agent with OS control | Med / High | Vet license + maintenance + pin versions for every MCP (RAG, screenshots); local-only MCPs strongly preferred; MCP additions go through the same graduated-permission tiers. FUTURE GitHub pitfalls |
| **WKWebView rendering debt** — glass layers accrete until the hub janks; theme-palette trap recurs | Med / Low-Med | Backdrop-filter budget (Section 4) enforced at review time; the full-palette re-declaration rule stays documented in CLAUDE.md. CLAUDE.md gotchas |

---

## 6. Metrics of success

Logged to `~/.hermes/dashboard/metrics.jsonl` starting Phase 1; reviewed at
each phase boundary. Targets are for the always-on 8B-class path unless noted.

**Fast (latency-to-meaning, per the X contrarian take):**
- Hub open → rendered: server-side p95 **< 100ms** (pre-warmer currently ~35ms; don't regress), perceived cold-open < 300ms.
- Chat TTFT (submit → first visible token): p50 **< 1.5s**, p95 < 3s. This is the number spec-decoding and prefix-stability move.
- Simple routed command (Phase 3+): **< 3s** end-to-end.
- Voice quick-ask (Phase 4): speech-end → audio-start **≤ 2s**.
- Hub scroll: no dropped-frame bursts with all 20 widgets enabled (spot-check via Safari Web Inspector timeline after any glass change).

**Smart (total-time-to-answer, not tok/s):**
- Multi-step tool turn (e.g., "check my calendar and the weather, then suggest a slot"): p50 **< 12s** on the MoE profile.
- Track total-turn-time per model/quant to catch 4-bit CoT inflation (FUTURE Substack perf) — a quant change may not regress p50 turn time >10% and be kept.
- Model-promotion gate pass rate recorded for every model in the roster.

**Trustworthy:**
- **Zero** irreversible-action incidents, ever (the only metric with no tolerance).
- 100% of write-class tool calls appear in the flight recorder; monthly undo drill passes.
- Watchtower precision (acted-on ÷ fired) **≥ 70%** per active trigger; anything below 40% for two weeks gets muted or redesigned.
- Approval loop: zero approvals silently dropped; approve-from-Telegram round-trip < 15s.

**Resource envelope:**
- Idle footprint (8B active): **≤ 6GB** via `footprint` (ps under-reports MLX — CLAUDE.md); MoE profile ≤ 20GB.
- Memory watchdog (32GB ceiling) fires **0 times** in a normal week — every firing is investigated, not shrugged at.
- All three launchd services + gateway: no unexplained restarts across a week of logs.

**Daily-use moments (the product truth):**
- Morning brief read (Telegram or Hub) ≥ **5/7 days**.
- ≥ **3** quick-asks/contextual-asks per day (menu-bar, right-click ask, chat).
- ≥ **1** Watchtower-initiated interaction acted on per day by Phase 3.
- Mind view: remembered-fact count grows week-over-week AND at least one user edit/prune per week (memory that's never curated is memory that's not trusted).

---

## 7. Version control & release rhythm

The launchd services run **from this working tree** — a broken checkout is a
broken assistant. The rules below exist to protect the always-on property.

**Branching:**
- `main` is always-runnable: services restart clean, hub renders, one chat turn works. Never leave `main` broken overnight.
- Small, low-risk changes (widget copy, CSS, docs) commit straight to `main`.
- Risky work (server.py surgery, Swift shell, mlx-server.sh, anything touching approvals/permissions) happens on short-lived `feat/<name>` or `fix/<name>` branches, merged same-or-next day. No long-running branches — this is a solo tool, drift is pure cost.
- After any merge touching services: `./install-services.sh` or targeted `kickstart`, plus ⌘R in the app (CLAUDE.md: WebView doesn't refresh on service restart), before walking away.

**Commits:**
- Convention: `area: imperative summary` — areas: `dash` (server.py/index.html/expanders/expand.js), `app` (Swift shell), `mlx` (mlx-server.sh/model roster), `agent` (hermes config/skills/gateway), `docs`, `ops` (launchd/install).
- One logical change per commit; the message names *what broke or what's now possible*, not the diff.
- Upstream pins are commits: bumping the hermes-agent tag or mlx-lm version is its own commit citing the release notes reviewed.
- Secrets never enter the repo (`~/.hermes/.env`, serve-token stay outside; .gitignore guards the rest). Config-as-code (layout/settings/models JSON) is tracked *as templates or with a documented restore path* — Phase 1 #6 settles which.

**Tags & releases:** a "release" for a personal tool = *a checkpoint you'd
confidently roll back to*. Concretely:
- Tag `v0.N` at each phase boundary after the phase's "done means" checks pass and CLAUDE.md is updated to match reality (CLAUDE.md is the release notes).
- Tag `v0.N.M` for any mid-phase state worth returning to (e.g., right before an mlx-lm or hermes-agent upgrade — the rollback point is the release).
- Phase-boundary checklist: all acceptance criteria demoed live; metrics reviewed against Section 6; services + gateway restart-verified; one full approval round-trip still works; tag pushed to the (private) remote if one exists.
- Rhythm: aim for a tag every 1–2 weeks matching the phase cadence — if three weeks pass without a taggable state, the phase is over-scoped and gets cut, not extended.

---

*Grounding note: all FUTURE.md citations reference `docs/FUTURE.md` sections
(Reddit / GitHub / Substack / X — feature ideas, perf, pitfalls, contrarian
takes). Architecture facts reference `CLAUDE.md` as of 2026-07-05. Where the
research corpus was stale against reality (e.g., it assumed we weren't passing
`--prompt-cache-bytes`; we are), this plan follows reality.*
