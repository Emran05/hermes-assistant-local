# Evaluating 2026-09-08 GitHub trending for Hermes Assistant

Sources: Input A list (given), Input B fetched today via WebFetch from
github.com/trending/{python,swift,typescript,rust} and raw.githubusercontent.com READMEs.
All README summaries below were produced by WebFetch's small-model paraphrase of the
raw README — treat as medium-confidence secondary readings, not verbatim primary text,
unless a quoted phrase is shown.

## Triage: not for us (one line each, grouped)

**Coding-agent / dev-harness tooling (wrong user, not a personal assistant):**
apache/maka (TS agent workspace, server infra); DietrichGebert/ponytail (dev workflow skill);
tt-a1i/archify, cathrynlavery/diagram-design (dev diagram skills); Imbad0202/academic-research-skills
(academic-research specific); coreyhaines31/marketingskills (marketing); ruvnet/ruflo (multi-agent
swarm orchestration, overkill for one local agent); cursor/plugins, openai/plugins (marketplace
registries); anomalyco/opencode, Gitlawb/openclaude, openai/codex (competing coding-agent CLIs);
vercel-labs/portless (local URL/tunnel mgmt for dev); humanlayer/skills, K-Dense-AI/scientific-agent-skills
(niche skill packs, coding/science focused); xingkongliang/skills-manager (cross-tool skill manager,
not needed for a single runtime); vitali87/code-graph-rag, abi/screenshot-to-code (dev code tools).

**Browser/scraping/automation not core to Hermes:** browser-use/browser-use, jo-inc/camofox-browser
(stealth scraping - also ethically dicey), ChromeDevTools/chrome-devtools-mcp (Hermes already ships
its own in-app browser control per hermes-agent v0.21 "Browser Control"); zenbu-labs/terminal-browser.

**Infra / language runtimes / generic dev tools (not agent-specific):** modular/modular, jdx/mise,
ajeetdsouza/zoxide, pnpm/pnpm, sinelaw/fresh, rustdesk/rustdesk, firecracker-microvm/firecracker,
pola-rs/polars, nvm, fmt, zstd, llvm, public-apis, 3b1b/manim, BoundaryML/baml, AlexsJones/llmfit.

**Model/infra not matching Hermes' Qwen3+mlx-vlm stack:** google-research/timesfm (time-series
forecasting, unrelated); unslothai/unsloth (fine-tuning framework, Hermes doesn't train locally);
jingyaogong/minimind (train-from-scratch toy LLM); DeepSeek-v4-Flash recipe; xai-org/x-algorithm
(social feed ranking, irrelevant); cactus-compute/needle (14MB model for wearables/phones - wrong
form factor, Hermes targets Apple Silicon desktop via mlx-vlm already).

**Unrelated content/media tools:** heygen-com/hyperframes (HTML->video), harry0703/MoneyPrinterTurbo,
koharu-rs/koharu (manga translator), every-app/open-seo, megadose/holehe (OSINT lookup tool -
also not something we want to associate Hermes with), AprilNEA/OpenLogi (Logitech mouse driver
replacement), gods-eye-view, AutoHedge, escrcpy, LunaTV, awesome-gpt-image-2, omarchy, THU-MAIC/OpenMAIC.

**macOS app catalogs / adjacent utilities (interesting as prior art, not adoptable code):**
jaywcjlove/awesome-mac, vsouza/awesome-ios, iina/iina, utmapp/UTM, jordanbaird/Ice, p0deje/Maccy,
nikitabobko/AeroSpace, rxhanson/Rectangle, ronitsingh10/FineTune, apple/coreai-models.
Beingpax/VoiceInk (open-source macOS voice-to-text) is close to Hermes' voice-parked problem but
is a full rebuildable Swift app itself, not something we can embed in the frozen shell - noted,
not adopted.

**Local-Apple-Silicon inference servers worth a future look, not deep-dived this pass:**
jundot/omlx (LLM inference server w/ menu-bar management, continuous batching, Apple Silicon,
21.5k monthly stars) and magnitudedev/magnitude (Apple Silicon inference server with model
profiling, TS, 4.2k stars/+2.6k this week) both look architecturally adjacent to mlx-vlm serving
+ Hermes' battery-aware on-demand model loading. Flagging for a follow-up research pass, not
evaluated in depth here (time-boxed).

**Not evaluated / not found:** THU-MAIC/OpenMAIC (no confirmable README fetched in this pass).

---

## Deep dives (13-15 most relevant)

### 1. ayghri/i-have-adhd
- URL: https://github.com/ayghri/i-have-adhd (README: https://raw.githubusercontent.com/ayghri/i-have-adhd/main/README.md)
- License: MIT. Format: single SKILL.md (agent-skill standard), no code/runtime.
- Stars/velocity: ~30k (daily trending) - very high for a brand-new skill repo; treat velocity as
  possibly bot/algorithm-amplified (unverified).
- Maturity: young, but the artifact is trivial (one text file) so maturity risk is low.
- What it does: rewrites agent responses to lead with the next action, numbers multi-step tasks,
  caps lists at 5 items, and forbids preamble/recap/closers ("No preamble. No recap. No closers").
- How it lands in Hermes: extends the prompt-budget toolset profiles / dashboard response
  formatting. Mechanism: vendor the SKILL.md text as a default system-prompt fragment (or a
  selectable "terse mode" toolset profile) applied to Telegram/Quick Ask outputs, since Hermes'
  stated purpose is protecting the owner's attention. Effort: S. Risk: low (pure prompt text,
  no dependency).

### 2. blader/humanizer
- URL: https://github.com/blader/humanizer (README fetched via raw.githubusercontent.com)
- License: MIT. Format: SKILL.md + install via `npx skills add` or manual copy (the npx step is
  just a convenience installer, not a runtime dependency once vendored).
- Stars: 45k (weekly-trending scale) - unverified/likely inflated for age of repo.
- What it does: detects 25 patterns of "AI writing" across 5 categories (staging vs. stating,
  rhythm by rule, inflation/borrowed authority, formatting by rule, leftover chat artifacts),
  marks them, and rewrites while validating claims against source ("does not make things up").
- How it lands in Hermes: a skill for the memory/drafting path - whenever Hermes drafts an email,
  Telegram reply, or note for the user, this skill's instructions could be attached to make output
  read less like a bot. Mechanism: vendor as a SKILL.md, invoked selectively (not default, since
  it costs extra reasoning tokens on a small local model). Effort: S. Risk: low but the 25-pattern
  prompt is sizeable - test against Qwen3's context/latency budget before making it default.

### 3. multica-ai/andrej-karpathy-skills
- URL: https://github.com/multica-ai/andrej-karpathy-skills
- License: MIT. Format: CLAUDE.md-style guideline file, Cursor rule file.
- What it does: four coding principles (think before coding, simplicity first, surgical changes,
  goal-driven execution) for LLM coding assistants.
- How it lands in Hermes: not for the runtime - for the Hermes *repo's own* CLAUDE.md when using
  Claude Code to develop Hermes itself. Mechanism: append/adapt into the existing project CLAUDE.md.
  Effort: S. Risk: none (dev-process only, zero runtime impact).

### 4. obra/superpowers
- URL: https://github.com/obra/superpowers
- License: MIT. Installed as a Claude-plugin marketplace package; skills fire during a structured
  dev workflow (brainstorm -> plan -> implement -> test -> review) with human approval gates.
- What it does: TDD, debugging, git-worktree, subagent-coordination, and meta "skill creation"
  skills for coding agents.
- How it lands in Hermes: mostly a coding-agent methodology, not applicable to Hermes' end-user
  runtime. The one transferable piece is the "meta" skill-creation skill and the human-approval-gate
  pattern between phases, which parallels Hermes' existing permission-tiers/approvals design -
  worth reading for pattern-matching but nothing to port directly. Effort: N/A (reference only).
  Risk: n/a.

### 5. mattpocock/skills
- URL: https://github.com/mattpocock/skills
- License: not stated in README (check before vendoring text). Installed via Claude Code plugin
  marketplace or `npx skills@latest add`.
- What it does: two-tier skill set (user-invoked: triage, to-spec, handoff, teach; model-invoked:
  tdd, research, writing-for-agents, grilling) targeting coding-agent failure modes.
- How it lands in Hermes: "handoff" and "teach" are productivity skills conceptually close to
  Hermes' Claude-escalation path (handing a task/context to a bigger model) and its Needs-you
  inbox triage. Mechanism: read the actual skill text (not yet fetched at file level) and
  reimplement the concept as SKILL.md, since license is unconfirmed - do not vendor verbatim
  without checking LICENSE file first. Effort: S-M pending license check. Risk: medium
  (unconfirmed license; unverified star count of 257k weekly for a personal skills repo is a
  red flag for trending-page noise/possible spam-star inflation).

### 6. openai/skills
- URL: https://github.com/openai/skills - marked **deprecated** in its own README, pointing users
  to openai/plugins and agentskills.io for the current "Agent Skills open standard."
- License: per-skill `LICENSE.txt`, no single repo license given.
- How it lands in Hermes: low direct value now that it's deprecated; useful only as a pointer to
  the agentskills.io spec for confirming Hermes' SKILL.md format stays standards-compatible.
  Effort: n/a. Risk: none.

### 7. affaan-m/ECC
- URL: https://github.com/affaan-m/ECC
- License: MIT (core); "ECC Pro" is a paid GitHub App add-on.
- Stars: 254k (daily+weekly trending) - flagged as an implausibly high count for what the README
  itself describes as a Claude-Code-specific harness; treat this number as unverified/likely
  inflated (possible star-farming or trending-page anomaly), not evidence of adoption quality.
- What it does: "agent harness operating system" - 68 agents, 286 skills, 94 commands, git hooks,
  and always-loaded "rules," explicitly optimized for Claude Code coding workflows across many
  languages.
- How it lands in Hermes: mostly not applicable - it's built for team coding-agent harnesses and
  contradicts Hermes' "small, stdlib, minimal" philosophy (68 agents / 286 skills is the opposite
  of Hermes' lean toolset-profile design). Two ideas are worth stealing without adopting the
  codebase: (a) "Memory Vault," a portable markdown context-handoff format for multi-harness
  transfer - conceptually close to what Hermes' Claude-escalation path needs when handing context
  to Claude; (b) AgentShield, a standalone secret-scanning CLI (`npx ecc-agentshield`) - the
  *idea* (scan agent config/tool-output for leaked secrets before it leaves the box) is portable,
  but the tool itself requires npm, which violates Hermes' no-Node constraint, so it would have to
  be reimplemented in stdlib Python. Effort: M for a clean-room stdlib reimplementation of either
  idea; L and not recommended if trying to adopt any of the actual codebase. Risk: high license/
  scope-creep risk if more than the two ideas above are pulled in.

### 8. mksglu/context-mode
- URL: https://github.com/mksglu/context-mode
- License: **Elastic License 2.0 (ELv2)** - source-available, not OSI-open; can't be freely
  redistributed/vendored, can be read and reimplemented from scratch.
- Language/runtime: Node.js 22.5+/Bun, TypeScript. Stars: 21.4k trending (both daily+weekly lists),
  +935/week - among the more consistently-growing entries here.
- What it does: intercepts raw tool output before it reaches the model. Runs scripts in sandboxed
  subprocesses, indexes captured output into SQLite with FTS5+BM25, and exposes `ctx_search`/
  `ctx_execute` MCP tools so the agent queries a local index instead of re-reading raw data.
  Claims "56 KB Playwright snapshot -> 299 bytes" and "500 CSV rows: 85.5 KB -> 222 bytes"
  (self-reported marketing examples, not an audited general benchmark). Also does PreCompact/
  SessionStart hook snapshotting to survive context compaction.
- How it lands in Hermes: this is the closest direct competitor-concept to Hermes' *existing*
  tool-output budget plugin (head/tail + spill-to-disk) and context/cache-hit meter. The
  incremental idea worth taking: persist spilled tool output into a **SQLite FTS5 index**
  (Python's stdlib `sqlite3` module supports FTS5) so the agent can later `search` prior tool
  output instead of re-running a tool or re-reading a spilled file linearly, and snapshot/restore
  session state around compaction events the way Hermes' compression controls do. Mechanism:
  clean-room reimplementation in stdlib Python (sqlite3 + subprocess), not a code port (license
  forbids). Effort: M. Risk: medium - must not copy code/text under ELv2; the "98%" figure should
  not be repeated as a Hermes-side promise since it's benchmark-specific.

### 9. volcengine/OpenViking
- URL: https://github.com/volcengine/OpenViking
- License: **AGPLv3 for the main codebase**, Apache-2.0 for CLI/examples only.
- Language: Python 3.10+, server + CLI, multi-provider LLM support (OpenAI, Volcengine, Kimi, GLM,
  local Ollama). Stars: 36k monthly.
- What it does: exposes memory/resources/skills as a virtual filesystem under `viking://`
  (browsable with `ls`/`find` instead of vector-store queries), with three-tier progressive
  context loading: L0 one-sentence abstract, L1 ~2k-token overview, L2 full detail loaded only
  when needed. README claims integrations for "Claude Code, Codex, Cursor, Hermes, and others" -
  **unverified whether "Hermes" here means NousResearch/hermes-agent specifically or a different
  project**; treat this integration claim as unconfirmed.
- How it lands in Hermes: the AGPLv3 core means the actual codebase should not be vendored into
  or run as a linked service by Hermes without accepting AGPL network-copyleft obligations for
  the whole product - **do not integrate the code**. The L0/L1/L2 progressive-loading idea,
  however, is directly applicable to Hermes' existing memory layer (facts + FTS5): retrieval
  could return one-line abstracts first, then let the plugin/tool-budget layer decide whether to
  pull the full fact record, reducing token cost on recall. Effort: M (idea-only port to stdlib
  Python). Risk: license risk is high if anyone is tempted to just run OpenViking as a sidecar;
  keep it strictly to the architecture idea.

### 10. akitaonrails/ai-memory
- URL: https://github.com/akitaonrails/ai-memory
- License: MIT. Language: Rust (needs 1.95+), ships as a compiled CLI. Stars: 6.1k monthly,
  +4.7k this month - fast-growing but young.
- What it does: capture -> consolidate -> recall -> handoff pipeline for cross-agent-CLI memory.
  Lifecycle hooks observe prompts/tool calls; at session end, observations become markdown wiki
  pages in a git-backed repo; SQLite (with FTS5) indexes are rebuilt from those files for full-text
  + entity + optional vector search; explicit "handoff" commands carry context to a different
  agent/tool. Quote: "the source of truth is a git-backed wiki of ordinary `.md` files: `grep` it,
  open it in Obsidian, edit it by hand, `rsync` it."
- How it lands in Hermes: architecturally the closest match of anything reviewed to Hermes'
  existing memory layer (facts + FTS5) and its optional escalation-to-Claude path. Two integration
  mechanisms, either viable: (a) call the compiled Rust binary as an external CLI from Python
  (subprocess) for the handoff step specifically - i.e., when Hermes escalates a task to Claude,
  dump local memory into the same git-backed-markdown wiki format so the escalated Claude session
  can `grep`/read it directly; (b) reimplement just the capture/consolidate/recall loop natively in
  Python stdlib (sqlite3 FTS5 + plain .md files), since the architecture doesn't require Rust-
  specific capability. Effort: S (call as CLI) to M (native port). Risk: low - MIT, simple format,
  no lock-in (plain markdown + rebuildable SQLite index matches Hermes' "sovereign, offline" ethos
  well).

### 11. semantica-agi/semantica
- URL: https://github.com/semantica-agi/semantica
- License: MIT. Python 3.8+. Stars: 12.4k monthly.
- What it does: full knowledge-graph pipeline (ingest -> parse -> extract -> dedupe -> KG ->
  ontology/reasoning/provenance/decisions -> polyglot storage in Neo4j/Oxigraph/Jena/Neptune),
  targeted at regulated-industry auditability and explainability, not personal-agent memory.
- How it lands in Hermes: not recommended - it is built for enterprise data-lineage/compliance use
  cases with heavyweight graph-database backends, the opposite of a single-user stdlib-only local
  agent. The only weakly-transferable idea is "conflict detection before facts merge," which could
  loosely inform de-duplication logic in Hermes' facts store, but this does not justify adopting
  any of the framework. Effort: not recommended (S if only borrowing the dedup-before-merge idea
  as a one-line design note). Risk: scope mismatch, not license.

### 12. microsoft/markitdown
- URL: https://github.com/microsoft/markitdown
- License: MIT (the WebFetch summary above said "not explicitly stated" but this is Microsoft's
  well-known MIT-licensed converter - verify the LICENSE file at adoption time to be certain, but
  MIT is the known real-world license and should be double-checked, not assumed, per this
  research's own honesty rule). Python 3.10+, official Microsoft repo, actively maintained
  (182k stars daily-trending scale).
- What it does: converts PDF, Office docs (Word/Excel/PowerPoint), images, audio, HTML, CSV, JSON,
  XML, ZIP, YouTube URLs, and EPub to Markdown, via CLI (`markitdown file.pdf -o out.md`) or Python
  library, with optional per-format extras (`pip install 'markitdown[pdf,docx]'`) so the core stays
  light.
- How it lands in Hermes: directly extends the FTS5 search over chat/mail/calendar - when a user
  drops a PDF/Word attachment or the mail client surfaces an Office document, Hermes could shell
  out to `markitdown` (installed in an isolated venv, invoked as a subprocess, not imported into
  the stdlib-only dashboard process) to normalize it to Markdown before indexing into FTS5.
  Mechanism: call CLI as an external tool, matching Hermes' existing pattern of keeping the
  dashboard itself stdlib-only while shelling out to helper processes. Effort: S (CLI subprocess
  call) to M (if bundling a managed venv for the optional per-format extras). Risk: low - official
  Microsoft-maintained, permissively licensed, purely additive.

### 13. debpalash/VoiceStudio
- URL: https://github.com/debpalash/VoiceStudio
- License: AGPL-3.0 (app); default OmniVoice model weights are CC-BY-NC (non-commercial - matters
  if Hermes is ever sold/commercialized). Stack: Rust/Tauri + React/TS UI + Python/FastAPI backend.
  Stars: 21.3k monthly.
- What it does: voice cloning, dubbing, transcription, TTS/ASR across "16 TTS engines - 11 ASR
  engines - 646-language catalogue," fully offline after setup, no CLI but exposes a local
  REST/WebSocket + OpenAI-compatible audio API on `localhost:3900`.
- How it lands in Hermes: voice is parked because the Swift shell can't be rebuilt, but VoiceStudio
  doesn't need to be embedded - it runs as its own always-on local process, and Hermes (stdlib
  Python) can be a pure HTTP client of its OpenAI-compatible localhost API using `urllib`, with no
  new dependency in the dashboard process. This is a legitimate stdlib-compatible path to add
  voice output/input to the Telegram gateway or Quick Ask without touching the frozen shell or the
  product's Node/npm constraint (Tauri/React only exist inside VoiceStudio's own process, never
  imported into Hermes). Effort: M (optional integration: user installs/runs VoiceStudio
  separately; Hermes adds an opt-in "voice" skill/setting that POSTs to `localhost:3900`). Risk:
  medium - heavyweight app to ask users to also run (8-20GB disk, optional 8GB+ VRAM recommended),
  AGPL is fine for HTTP-only interop but would not be fine if any VoiceStudio code were ever
  vendored into Hermes.

### 14. vorssaint/vorssaint-utils
- URL: https://github.com/vorssaint/vorssaint-utils
- License: GPL-3.0-or-later (source), separate trademark terms for branding. Swift. Stars: 17.2k
  monthly, +12.3k this month (fast-growing).
- What it does: modular macOS menu-bar toolkit - clipboard history+search, scratchpad, command
  bar (app launch + calculator), screenshot/recording, system monitors, window snapping; features
  are independently installable, uninstalled ones consume no resources.
- How it lands in Hermes: the existing Swift shell is frozen and cannot be rebuilt, so none of this
  code can be merged into the current app. It's useful only as a features-inspiration list for
  Hermes' menu-bar Quick Ask (e.g., a scratchpad or command-bar-style quick action list are close
  analogues to Quick Ask's existing role) and as a candidate reference if a *new, separate* small
  Swift menu-bar binary is ever built alongside Hermes rather than inside the frozen shell.
  GPL-3.0 means no code can be copied into anything that isn't itself GPL-compatible. Effort: L
  (new artifact, not a modification) - lowest priority of the deep-dived items given the frozen-
  shell constraint. Risk: license (GPL) plus added maintenance surface.

### 15. NousResearch/hermes-agent (the runtime itself - what recent releases add)
- URL: https://github.com/NousResearch/hermes-agent/releases
- Hermes Assistant is pinned to v0.18; latest is **v0.21.1 (2026-09-07)**, three-and-a-half minor
  versions ahead, including v0.21.0 "The Pantheon Release" (2026-08-31, ~5,800 commits) and v0.20.0
  "The Herald Release" (2026-08-03).
- Features shipped since v0.18 that Hermes currently runs without (per release-notes summary,
  medium confidence - verify against actual CHANGELOG before upgrading):
  - **Compression Refinements** (v0.20.0): proactive pruning, per-turn micro-compaction,
    guaranteed N-message tail, configurable thresholds - directly extends Hermes' existing
    "compression controls."
  - **Tool Self-Recovery** (v0.20.0): terminal output spills to readable files, `patch` detects
    already-applied edits, searches probe for near-misses - directly overlaps/extends Hermes'
    existing tool-output budget plugin (head/tail + spill-to-disk); may need reconciliation, not
    pure addition, if upgraded.
  - **Grounded Citations skill** (v0.20.0): claims backed by verifiable sources, fact-checking
    mode - aligns with Hermes' FTS5 retrieval and memory layer; could reduce hallucination risk on
    Needs-you inbox triage answers.
  - **Persistent Cron Memory, Live Subagent Control (steerable `delegate_task`), MCP Command
    Center, Streaming Voice + wake words, A2A protocol, Bot Mode** (v0.20.0/v0.21.0): larger
    features, more relevant to multi-agent/voice-forward deployments than Hermes' current
    single-owner, text-first design; lower immediate priority but worth scanning for anything
    that plugs into the existing permission-tiers/approvals system (Live Subagent Control's
    "course-correct mid-flight, stop early with partial results" sounds adjacent to Hermes' undo
    Flight Recorder and is worth a closer read).
  - **Security Hardening** (v0.21.0): protected agent-instruction files now require write approval,
    deep secret-redaction sweep - directly relevant to Hermes' permission-tiers design, likely
    worth adopting on upgrade regardless of the rest.
- How it lands in Hermes: this is a runtime dependency version bump, not a "port an idea" exercise.
  Recommended action: **evaluate and plan an upgrade path from v0.18 to v0.21.1**, specifically
  auditing whether `transform_tool_result` and `pre_llm_call` plugin hook signatures changed across
  ~5,800 commits, and whether the new Tool Self-Recovery / Compression Refinements conflict with
  Hermes' existing custom tool-output-budget and compression-control plugins before adopting. Effort:
  M-L (large version gap, hook-compatibility audit required, likely plugin rework). Risk:
  medium-high given commit volume; do not upgrade blind.

## Contradictions & caveats

- **Star/velocity numbers across nearly every repo in this sweep are implausibly large for their
  apparent age/niche** (ECC 254k, mattpocock/skills 257k weekly, andrej-karpathy-skills 211k,
  ponytail 132k weekly, i-have-adhd 30k/day for a single SKILL.md file). None of these were
  independently re-verified against GitHub's live star count in this pass; treat every star figure
  in this report, including those pulled from Input A, as **self-reported/trending-page marketing
  signal, not a quality proxy.**
- **OpenViking's claimed "Hermes" integration is unverified** - could refer to NousResearch's
  hermes-agent or an unrelated project of the same name; do not treat it as confirmed compatibility.
- **License conflicts to respect if any of the above move from "idea" to "code":** context-mode is
  ELv2 (source-available, not permissive - no code copying); OpenViking's core is AGPLv3 (network
  copyleft risk if vendored/linked); VoiceStudio is AGPL-3.0 (fine as an HTTP client, not fine to
  vendor code); vorssaint-utils is GPL-3.0 (no code copying into non-GPL code); mattpocock/skills'
  license was not visible in the fetched README and should be checked before vendoring any skill
  text verbatim.
- **context-mode's "98% reduction" and similar headline numbers are self-reported, benchmark-
  specific marketing figures** (specific to a 56KB Playwright snapshot and a 500-row CSV example),
  not general guarantees applicable to Hermes' own tool-output shapes.
- **markitdown's exact license was not confirmed from the fetched README text** (the WebFetch
  summary said "not explicitly stated") even though it is publicly known to be MIT-licensed;
  verify the LICENSE file directly before depending on it, per this task's own accuracy standard.
- All README content in this report was read via WebFetch's summarization pass (a smaller model
  paraphrasing the raw README), not verbatim primary-source text except where quoted; for any item
  moving to implementation, re-read the raw README/LICENSE directly rather than relying on this
  summary.
