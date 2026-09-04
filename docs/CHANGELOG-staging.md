# Staging changelog — local commits awaiting batched push
Rule: every completed workstream = one local commit + one line here.
Push batches happen only on explicit go-ahead. `main` stays always-runnable
(launchd runs this working tree).

## 2026-07-05
- (run start) P1 "Earn trust" begins — quality-gated, in order. Downloads
  pre-approved; user-action items collect in docs/NEEDS-YOU.md.

## Local commits (unpushed)
- `de5ab7f` docs: research corpus + dev plan
- `d06ae7a` docs: staging changelog, needs-you, brief spec
- `<foundation>` server: aux-module route registry + static aux JS loader (P1 foundation)
  → register_get/register_post + RouteCtx; aux_*.py auto-exec'd; /aux_*.js static.
    Every P1 feature now drops in as a self-contained aux module — no dispatch surgery.
- `<p1.1>` P1.1 Editable Memory — aux_memory.py (734L) + aux_memory.js (679L) + 2 index.html hooks.
  View/add/edit/delete agent memories from Mind view. §-delimiter-aware core-file editor
  (byte-identical to memory_tool, never trips drift detector), freeform topic files, soft-delete
  to trash + restore, snapshots + JSONL recorder (P1.2 contract), etag concurrency (409), flock
  (423), char-limit/path/symlink/casefold guards, core-file delete refusal (403).
  VERIFIED: full curl matrix, headless render, and LIVE `hermes -z` codeword drill —
  agent read a dashboard edit (BANANA→KUMQUAT→BANANA), zero drift, 600 perms preserved.
- `<p1.2>` P1.2 Flight Recorder + Undo — aux_recorder.py (1048L) + aux_recorder.js (459L)
  + hermes_rpc.py hook (RECORDER_HOOK, 2 touches) + 1 index.html script tag.
  Rides hermes-agent's OWN git checkpoint store (enabled checkpoints.enabled=true, store live).
  Three legs: ws tool.start/complete (live), 5s state.db reconciler (ALL surfaces), upstream
  pre-write snapshots (race-free). recorder.db (WAL,0600,tool_call_id-dedupe). Console "Flight
  Recorder" lane with per-kind reversibility chips + Undo. /api/recorder + /api/undo (whitelist
  refusal for irreversible, sha256 conflict+force, single-file restore / created-file→undo-trash,
  undo-is-itself-undoable). VERIFIED: 176 actions reconciled from state.db, byte-identical undo
  round-trip via direct-checkpoint harness, all refusal paths, headless render. ws-live leg +
  approved-write drill deferred to P1.4 (needs UI approval; -z can't approve under manual mode).
  NOTE: enabling checkpoints.enabled means the agent now git-snapshots before every file write.
- `<p1.3+p1.4>` P1.3 Graduated Permission Tiers + P1.4 approval-loop drill.
  permissions.py (740L engine) + aux_permissions.py (49L routes) + aux_trust.js (398L Trust panel)
  + hermes_rpc.py enforcement branch (decide→respond, sends only once/deny) + 1 index.html tag.
  17 action-classes over 73 dangerous-pattern keys; AUTO/ASK/NEVER tiers with safety FLOORS
  (critical classes can never be auto), tamper-detection sidecar (auto→ask on out-of-band edit),
  audit JSONL. SHIPPED DEFAULT = all-ask (installing changes nothing; user graduates trust in panel).
  Also fixed switch_model: kickstart -k → bootout/bootstrap (the real "switch button" bug;
  KeepAlive service wasn't reloading the new model).
  LIVE-DRILLED all 3 tiers through real Qwen3-30B turns: ASK→card fired→deny→blocked;
  NEVER→auto-denied; AUTO→auto-approved+command ran; command_allowlist unchanged (interop held);
  P1.2 ws-leg captured 3 origin=ws terminal rows. Audit log shows asked/user-deny/auto-denied/auto-approved.
  KEY FINDING: Hermes-3-8B does NOT reliably call tools (deflects); Qwen3-30B-A3B does. See docs/FINDINGS.md.
- `<p1.5>` P1.5 Metrics Baseline — aux_metrics.py (732L) + aux_metrics.js (310L) + 1 index.html tag
  + 1 hermes_rpc line (_submitted_ts for setup/serve TTFT split). Console "Vitals" strip: TTFT p50/p95,
  turn latency, hub-API latency, RAM envelope via footprint(1), model load time, tokens/sec, approvals
  (from permissions-log.jsonl), undo (from recorder.db). Ring buffers + JSONL persistence in ~/.hermes/metrics/.
  TTFT measured via _new_job MeteredJob override (no run_turn surgery). VERIFIED live: TTFT p50 965ms
  (<1.5s target) on Qwen3-30B, approvals {3,1,2} match the P1.4 drills, RAM via footprint, counters survive restart.
- `<p1.6>` P1.6 Config-as-Code — aux_config.py (709L) + aux_config.js (458L) + 1 index.html tag
  + docs/state-snapshot.json (tracked artifact). Export/import a deterministic snapshot of layout,
  settings, model roster+active, and permission policy. STRICT allowlist + denylist; output secret-scan
  HARD-REFUSES export if any token/key/home-path/secret shape is detected (proven: injected a token-shaped
  quicklink → export 400, file untouched). Import fail-closed: approvals.mode!=manual refused, unknown
  section/widget refused/dropped, schema-validated, pre-restore backup. VERIFIED: deterministic (identical
  md5 across exports), secret-leak checks all clean, import round-trip reverts a drifted setting.

## PHASE 1 "EARN TRUST" COMPLETE (2026-07-05)
All 6 workstreams + foundation done & verified. 8 py modules compile, 5 aux JS served, 8 endpoints
healthy, zero load errors. Trust surface: editable memory, flight recorder+undo, graduated permissions
(live-drilled), metrics, config-as-code. Model on Qwen3-30B (8B can't tool-call — see FINDINGS.md).
Staged locally, unpushed — awaiting go-ahead for a batched push.
- `<p2.1>` P2.1 Watchtower + 8am World Brief — aux_watchtower.py (1642L) + aux_watchtower.js (581L)
  + 1 index.html tag. World Brief: deterministic 5-section compose (your day / world & tech / market
  movers w/ why / underground signal / look-ahead) from CACHED data + one tool-free synthesis pass,
  rebinds _generate_briefing (no duplicate); 8am scheduler thread w/ date-guard + catch-up-on-wake;
  Telegram via `hermes send --to telegram` (home channel, no chat_id, no LLM). Watchtower: notify-only
  trigger engine, 5 live evaluators (ticker/index/crypto move, system_metric, rss_keyword) + 3 stubs,
  quiet-hours/cooldown/dedupe/daily-cap, fire log w/ useful/noise precision, Mind card CRUD+test-preview.
  Schema REFUSES action/command/chat_id/target (notify-only enforced structurally). VERIFIED dry-run only:
  5-section brief renders (real headlines/movers, 12h, zero emoji), degrades to deterministic when model
  paused, all 3 gate types, schema refusals. NO real Telegram sent (deferred to user). Today's 8am push
  suppressed; auto-resumes 8am 2026-07-06.
- `<p2.2+p2.3>` P2.2 Menu-bar Quick-Ask + P2.3 Clipboard Actions.
  main.swift 241→506L (NSStatusItem template spark glyph, .transient NSPopover chat reusing /api/chat,
  Carbon ⌃⌥Space global hotkey w/o Accessibility TCC, SMAppService login item, NSPasteboard bridge,
  approval-hands-out-to-main-window) + build-app.sh (+Carbon +ServiceManagement).
  aux_clip.py (250L, /api/clip/transform — DIRECT-to-model transforms, NO tools field = safe by
  construction, loopback-only) + aux_clip.js (437L command sheet, ⌘⇧V) + aux_quickask.js (298L popover)
  + 2 index.html tags. VERIFIED: Swift compiles+installs, real Qwen transform (summarize/translate/extract),
  loopback+no-tools proven, guards (400/413), menubar chat multi-turn persists, app launches clean.
  NEEDS human QA: menu-bar click, ⌃⌥Space hotkey, Open-at-Login (can't automate clicks).
- `<claude-usage>` Claude Max usage/rate-limit tracker — aux_claude_usage.py (450L) + .js (299L)
  + 1 index.html tag. Hub widget reading ~/.claude/**/*.jsonl (read-only, 60s cache, 8-day scan bound):
  current 5-hour rolling window (ccusage-style blocks) w/ token split + reset countdown, today, 7-day
  sparkline, per-model + per-project, ≈API-equivalent cost, optional soft-cap gauge (no official Max
  cap published → shows usage vs your own busiest block). Registers via WIDGETS/EXPANDERS + RENDER/
  EXPAND_RENDER/WICONS + layout inject. VERIFIED: today's output_tokens EXACT-match vs independent
  tally, live numbers, all render cases. Fixed a shared-global datetime-rebind gotcha (now in CLAUDE.md).
- `<brief-v2>` World Brief v2 (user feedback) — aux_watchtower.py →2098L + expand_markets extended.
  (1) LINKS on every item ([title](url) — verified all 38 convert cleanly through the real Telegram
  MarkdownV2 formatter; synthesis has a URL-retention guard). (2) AFTER-HOURS markets: includePrePost
  chart fetch → honest phase REGULAR/PRE/POST/CLOSED + post/pre price/pct (also fixed marketState=None).
  (3) HOURLY INTEL: intel_loop thread — 14 keyless AI/social RSS feeds (labs, TechCrunch/Verge AI,
  Willison/Import AI/Zvi, r/LocalLLaMA…) + one local-model curation pass/hour w/ URL validation →
  intel.json (0600, dedupe, 72h); new "AI & Labs" brief section + enriched underground signal; 8am
  composes from the STORE (no fresh network). (4) TRUE AGENT SEARCH ENABLED: installed ddgs 9.14.4
  into the hermes venv (pre-approved) — web_search_tool returns real results; fixed the probe's
  multi-line-JSON parse bug → web_search_available:true → hourly `hermes -z` research pass active.
  Chunked >4096 Telegram sends (no truncation). Formatting polish deferred (TODO in code, per user).
- `<p2.5>` P2.5 Google connect (grant-ready) — aux_google.py (562L) + aux_google.js (421L) + 1 tag.
  PKCE OAuth wizard (paste client JSON → consent → paste redirect), scopes HARD-NARROWED to
  gmail.readonly+calendar+contacts.readonly (Google has NO draft-without-send scope, so read-only IS
  the no-send enforcement — at Google's auth layer, 3-deep: URL assertion, exchange scope-wall w/
  auto-revoke on violation, include_granted_scopes=false). Token byte-compatible with the hermes
  google-workspace skill (identical key set, Credentials.from_authorized_user_file parses). What works
  once user connects: gmail search/get/labels, calendar list/create/delete, contacts; send/drive/docs
  403 at Google. User steps in NEEDS-YOU.md.
- `<midday+breaking>` Midday Pulse + Breaking alerts (user-directed) — aux_watchtower.py →2572L, .js →617L.
  Midday: once/day ~3PM (configurable 11-17h), fires ONLY when ≥2 buckets of genuinely-new content since
  8am (intraday movers ≥1.5% in live sessions only / fresh news / fires), skipped silently otherwise,
  catch-up till 6PM, deterministic + linked. Breaking: 60s scan of cached data, 3 high-bar classes
  (index ≥2.5% / ticker ≥8% live-session only; severe news corroborated by ≥2 sources; AI-lab major
  event), per-story dedupe + 90min class cooldown + daily cap 5 + quiet-hours override (configurable).
  Agent web_search confirmed feeding intel (30→43 items, curated before the brief). All gates
  unit-verified offline; zero real sends during build. Midday armed for today ~3PM.
- `<p2.6a>` P2.6a Mind multi-day drill-downs — aux_mind_drill.py (188L) + .js (350L) + 1 tag.
  14d/30d/60d range toggles on the Mind fuel + model-mix cards (/api/mind_drill, state.db mode=ro,
  clamped 7-90, 300s cache), busiest-day callout, in-place chart re-render with the exact expand.js
  idioms, sticky range, shimmer/error states. Phase 2 now HONESTLY complete (this was the forgotten
  half of DEVPLAN Phase-2 #6 the plan-judge caught).
- `<p2.4>` P2.4 Message Center — aux_messages.py (317L) + aux_messages.js (206L) + main.swift +315L
  (MessagesSync: FDA probe via open(2) EPERM, SQLite online-backup snapshot (no WAL locks), 14-convo
  query, attributedBody byte-scan decode, apple-epoch convert, 60s POST w/ 0600 token guard). Dashboard:
  token-guarded ingest (403/400/413/cap-200), 0600 store, provider/expander REBIND (no dup widget).
  VERIFIED: full state matrix, guards, privacy (no bodies in logs), REAL app POSTed fda:false → grant
  card renders from the live pipe. main.swift now FROZEN (rebuild drops the FDA grant — CLAUDE.md).
  PHASE 2 COMPLETE pending user FDA grant.
- `<p3-b3>` P3 B3 prefix-stable prompt + KV-cache sizing (TTFT). access_preamble() reordered:
  stable lines first, volatile last (grants→invariant→tasks→calendar→wall-clock-minute) — measured
  11× warm-prefill difference at 9.3k tok (2.92s volatile-first → 0.26s volatile-last). mlx-server.sh
  --prompt-cache-bytes 6GB→8GB (each ~20k agent seq ≈2GB KV; 6GB held only 3 while ≥4 producers churn
  the LRU re-paying ~8s cold prefill; 8GB holds 4, ~26GB footprint stays under the 32GB guard).
  Analysis + repeatable bench in docs/plans/b3-*. Applied to files; restart-verify deferred until the
  promotion-gate agent finishes swapping models (avoids collision). Hermes system prompt already
  prefix-stable (no upstream change).
- `<p3.1>` P3.1 Shortcuts action-bus — aux_shortcuts.py (29K) + .js (18K) + permissions.py 18th class
  "shortcuts-run" (floor ask) + 2 index.html tags + aux_trust "18 classes". Allowlist model (NOTHING
  agent-visible until user exposes it in the Mind Shortcuts card), /api/shortcuts/run gates every run
  through permissions.decide (ask→single-use 5min ticket → confirm → run; never→deny; unexposed→403),
  recorder rows per attempt, risk chips (Spam Text/Text Last Image flagged as messaging). access_preamble
  rebind steers the agent to the bus. VERIFIED live: unexposed→403, exposed→needs_approval+ticket.
  Residual risk R1 (raw terminal shortcuts run ungated) documented in FINDINGS.md.
- `<p3-b2>` P3 B2 model promotion gate — aux_promotion.py (26K) + .js (9K). /api/models/drill runs a
  6-case tool-calling eval DIRECTLY against the mlx server (no agent), stores promotion.json, decorates
  the roster with drill badges + license notes ("Built with Llama" attribution). switch_model wrapper
  warns when switching to a failed/undrilled model (never blocks). PROVEN: Qwen3-30B 6/6 PASS,
  Hermes-3-8B 1/6 FAIL — the gate discriminates exactly as FINDINGS predicted (8B can't tool-call).
- `<mem-ceiling>` HARD MLX memory ceiling (user: "can't take more than ~50GB unless you allow it").
  Two-layer: (1) ADMISSION CONTROL — mlx_admission() checks a 15s-cached footprint; at/over MLX_SOFT_GB
  (default 50) /api/chat + _generate_briefing REFUSE new model work with a clear "memory high, resend"
  message so the KV cache can't balloon the Mac down; user override via /api/model/mem_override {allow}
  (touches ~/.hermes/dashboard/mem-override). (2) HARD WATCHDOG — memory_guard_loop now polls 30s (was
  300s — balloons spike fast) and above MLX_HARD_GB (56) does a RELIABLE bootout→bootstrap restart (was
  kickstart -k, which doesn't reload the KeepAlive service) to free the balloon + clear cache. /api/model/
  mem_free = manual clear. models_payload surfaces {soft_gb,hard_gb,over,override}. VERIFIED e2e: at a
  10GB test-ceiling chat refused (16GB>10GB) with the message; override let it through; restored to 50.
- `<capability-skills>` 8 agent-authored skills (arms & legs) — installed in ~/.hermes/skills, snapshotted
  to repo skills-snapshot/ for version control. skill-forge (agent writes its own new skills),
  hub-cartographer (masters its own dashboard API), mirror-check (post-task self-QA + nightly journal),
  cron-conductor (safe scheduled autonomy), watchtower-author (turns 'tell me when' → live rules),
  osascript-cookbook (control any Mac app), screen-oracle (vision 'what am I looking at'), deep-dive
  (triangulated citation-locked research). All valid frontmatter, helper scripts, ride the P1 approval/
  recorder rails. Roadmap (incl. later tiers) in docs/CAPABILITY-ROADMAP.md.
- `<chat-ui>` Fullscreen chat: (1) "Local AI" panel in the tools sidebar — live tokens/sec (ticks up
  during generation, chars/3.6 est), TTFT p50, and Memory X/50GB with a bar (from /api/metrics +
  /api/models), refreshed 5s + on stream end. (2) Default to a FRESH conversation on every open (past
  chats stay reachable via the sidebar/dropdown) — was resuming the last session. Verified: JS clean,
  live turn populated 84 tok/s / TTFT, fresh-session init.
- `<plans>` PROACTIVE-INTELLIGENCE-PLAN.md (the "think for me" north star — You-Model + reasoning loop
  + warm-intro engine from the user's own network; Phase-1 build-first is grant-free) + agent-hands
  design specs (Mac control / sandboxed self-upgrade / Agent Desktop) + adversarial SECURITY review
  (dev-agent needs P0 fixes before build — deny-read secrets, egress-pin, orchestrator-commits,
  out-of-band merge confirm; Mac control + panel building now).
- `<hands-mac>` Mac control skill + Agent Desktop panel — apple/mac-control skill (open apps, media,
  screenshot→Telegram PREVIEW-BY-DEFAULT/opt-in send MC_CONFIRM_SEND, target hard-locked to --to telegram,
  no chat_id) + aux_desktop.py (/api/desktop/shots|shot|timeline|capture; screenshots 0600, loopback-only
  data-URIs, on-demand only) + aux_desktop.js (#view-desktop tab: live shots + computer_use timeline +
  Capture-now + task box). §4.9 enforced (panel exposes no self-approvable action). NOTE: build agent
  accidentally sent ONE real screenshot to the user's own Telegram during testing → script now opt-in-send.
- `<phase1-proactive>` Proactive Intelligence Phase 1 — the "think for me" layer.
  aux_youmodel.py/.js (typed You-Model: GOALS/NOW/LOOKING-FOR/INTERESTS/PREFERENCES.md + people/*.md;
  Mind "Your Model" card; /api/youmodel seed/add) + you-model-onboarding skill (adaptive ~10-min
  interview, seed-priors-first, propose→confirm→write, never writes without a yes) + aux_memory.py
  people/ subdir enablement. aux_foryou.py/.js (the reasoning loop: cheap lexical pre-filter on ALL
  intel → ONE batched Qwen why-you pass on top 15, mlx_admission-gated → ranked moves w/ why_you +
  matched_goal/person + suggested_action → foryou.json; "For You" hub widget/Agent-Inbox w/ useful/noise;
  notify-only). aux_watchtower.py: brief now LEADS with a "For you" section (degrades to empty until
  onboarding). VERIFIED: endpoints live, foryou widget in hub, empty-state fallback + personalized
  reasoning proven (seeded goal → 0.95-scored moves w/ real why-you). Next: user onboarding lights it up.
  + claude-bridge-system-prompt.md (researched deep-reasoning prompt for the two-brain Claude Bridge).
- `<loop-breaker>` HARD loop-breaker guard (hermes plugin ~/.hermes/plugins/loop-breaker/, enabled via
  plugins.enabled). pre_tool_call hook blocks the 3rd+ IDENTICAL tool call (name+args) within a turn
  → {"action":"block",...} nudging the agent to vary or conclude. Fixes the 54x-identical-web_search
  loop at 2 wasted calls instead of 50. Durable (plugin, not an upstream edit). Unit-verified: allows 2,
  blocks 3rd+, resets per turn / new query. Snapshotted to skills-snapshot/_plugins/.
  + UI-RESTRUCTURE-PLAN.md (Hub·Agent·Settings design + agent-page show-in taxonomy + settings IA).
- `<ui-restructure>` Hub·Agent·Settings restructure (B0-B3). aux_agent.js (940L) + aux_agent.py
  (/api/agent/pulse join) = the flagship AGENT page: wraps setView (retab, alias console/desktop→agent,
  hermes_view migration), re-parents the chat on enter/return-on-exit (Hub split-chat untouched),
  status hero w/ the two-brain "Sigil" (splits on Claude escalation) + display-only brain badge (live
  tok/s, /api/claude/bridge poll), SHOWIN_RENDER dispatcher wrapping setAgentState → inline tool cards
  (streaming dark TERMINAL card marquee; 8 stubs for B4; unknown→fail-open), heartbeat ticker. NO approval
  control in any show-in card (invariant held). aux_settings_shell.js (731L) = SETTINGS page: 236px nav
  (5 groups/12 panels) + search + kill switch + a relocator that re-homes every Mind card into its panel
  (wrap mindExtras + MutationObserver, unknown→System, idempotent, fail-open) — ZERO edits to existing
  modules. index.html B0: Mind→Settings label+gear + 4 base-section ids + 2 tags. Rollback = remove a tag.
  VERIFIED: both headless harnesses (agent 8/8, settings 91/91), all endpoints 200, JS clean. Needs ⌘R
  visual QA. Console/Desktop fold into the Agent rails in B5 (built as stubs); tabs kept until parity.
- `<ui-batch2>` Header/Agent-page follow-ups (user feedback). aux_agent.js →1279L: (1) B5 rails
  fold-in — Record rail reuses the flight recorder (relocates #recorder-card), Screen rail reuses
  aux_desktop's renderDesktop (watch-only) w/ red LIVE dot + one-shot auto-switch on computer_use;
  (2) stale Console/Desktop/Settings TABS hidden (CSS, DOM kept, setView aliases + auto-open rail) →
  top bar now Hub·Agent; (3) full CLAUDE DIALOGUE viewer (#ag-deep) from /api/claude/recent — expand
  the exact task asked + Claude's full response, model/duration/12h, display-only; (4) ESCALATE TO
  CLAUDE button on every bot reply → POST /api/claude/think depth:deep → appended "Claude · deep"
  answer card (graceful on refusal). aux_prefs.js (430L): hides #themebtn, adds a prefs gear →
  macOS-style dropdown (Appearance Light/Dark/Auto, reduce-motion (real CSS), Pause/Resume,
  System Settings…→setView('mind'), Proactive deep-link, About+live model) — only functional controls.
  Load order settings_shell→prefs→agent. VERIFIED: agent 13/13 + prefs 36/36 harness, all endpoints 200.
- `<idle-suspend>` Auto RAM reclaim — the model server now sleeps when nobody's
  using it. server.py: `idle_suspend_loop()` boots com.hermes.mlx-server out after
  10 min (`_idle_min()`, override via idle-suspend-min / IDLE_SUSPEND_MIN) of no
  USER activity (dashboard /api/chat ∪ newest telegram/hub state.db turn — background
  briefing/watchtower excluded), freeing ~22-26GB, marker `agent-idle-suspended`
  (DISTINCT from the manual-pause `agent-paused`; the two never coexist). The chat
  worker `agent_wake()`s (bootstrap + poll /v1/models) transparently before the turn;
  memory_guard + the loop skip while suspended; while asleep the loop also wakes on a
  fresh telegram/hub turn (best-effort). New /api/agent/wake + /api/agent/idle_config;
  /api/models carries idle_suspended/idle_enabled/idle_min; index.html model menu shows
  a distinct "sleeping · Wake now" state. VERIFIED end-to-end: forced a real suspend
  (mlx_lm gone, RAM freed, correct marker, pause-file absent) → chat auto-woke it →
  replied. Needs ⌘R for the menu state. Directly addresses the KV-cache/MLX ceiling.
- `<graphify>` Code knowledge-graph memory layer. Official `graphifyy` in an isolated
  venv ~/.hermes/graphify-venv (never the framework Python); `graphify update .` →
  graphify-out/graph.json (2794 nodes / 5086 edges / 163 communities, tree-sitter,
  no LLM). aux_graphify.py serves it read-only: GET /api/graph/stats (counts +
  god-nodes) and /api/graph/query?q= (node + neighbors — cheap "explain" instead of
  grepping; ~71x fewer tokens/query per the tool's benchmark). mtime-cached. VERIFIED:
  both endpoints 200 (query found this session's own agent_idle_suspended()). Remaining
  (harness-gated, run via `!`): graphify install --platform claude|hermes to register
  the /graphify skill into each agent's config. graphify-out/ (~5MB) → gitignore.
- `<qwen3.8>` Qwen3.8-27B in the model roster (2026-08-18). Researched: released
  2026-08-14, `mlx-community/Qwen3.8-27B-4bit` is `model_type: qwen3_5` (dense 27B,
  hybrid GatedDeltaNet/full-attn 64L, 262k ctx, vision tower dropped by mlx-lm's
  sanitize) — the INSTALLED mlx-lm 0.31.3 loads it; the only config delta vs Qwen3.5
  (`output_gate_type: swish`) is the DeltaNet gate mlx already uses. Deliberately the
  NON-MTP repo: `-MTP-4bit` hits mlx-lm≤0.31.3's double-RMSNorm-shift bug (#1197/#1623,
  fixed on main 2026-08-18, unreleased) and ships NaN drafter weights (mlx-vlm #1931).
  Its template thinks by default at reasoning_effort=xhigh (~22k think tokens on a
  trivial prompt) → roster default `template_args {enable_thinking:false}`; new
  `_write_template_args()` on switch → `~/.hermes/dashboard/chat-template-args`, read
  by mlx-server.sh as `--chat-template-args`. `_model_registry()` now merges new seed
  entries into an existing models.json. New POST /api/models/thinking {enabled[,id]}
  (`set_model_thinking`: persists, restarts server if active; on = low effort);
  /api/models carries `thinking {supported,enabled}`; model menu shows a "Thinking:
  on/off" row for thinking-capable models. VERIFIED: standalone generate coherent
  (31.7 tok/s decode, 15.5GB peak); server: simple/nested/restraint/chain/streaming
  tool_calls all parsed (qwen3_coder parser), per-request thinking → `reasoning`
  field, prefill ~580 tok/s cold / prefix-cached 0.5s for 12.6k tok, footprint 21GB
  (27 peak); `hermes -m … -z` real terminal tool turn correct (24 .py files); official
  drill 6/6 via a real switch (log shows template-args applied) + restore + re-pause.
  Trade-off vs the 30B-A3B: ~4-5x slower per token, first-turn prefill of Hermes's
  ~15k system prompt ≈25s (then cached) — a deliberate upgrade choice, not the new
  default. Needs ⌘R for the menu row.
- `<qwen3.8-mtp>` Qwen3.8-27B ~2x decode via native MTP speculative decoding
  (2026-08-18). Research (13-agent sweep + direct verification): the lever Mac
  users use on the Qwen3.5/3.6/3.8 DeltaNet family is the model's own MTP head
  (mlx-lm PR #990 unmerged/needs re-quant; MTPLX 2.24x M5 Max claim; mlx.fast
  leaderboard ~85 tok/s) — mlx-vlm 0.6.14 ships it (`--draft-kind mtp`), KV-quant
  and ZMLX fusion are +1-8% noise, separate draft models hurt. Built: isolated venv
  `~/.hermes/mlx-vlm-venv` (`install-mlx-vlm-venv.sh`), `mlx-vlm-launch.py`
  (RNG-restore shim for mlx 0.32's read-only random.state — else every temp>0
  request crashed; default reasoning_effort env; atexit os._exit to dodge the
  mlx teardown segfault that popped "Python quit unexpectedly" dialogs), roster
  `backend/draft_model/draft_kind/draft_block_size` fields + `_model_registry()`
  key backfill, `_write_template_args()` now also writes `server-backend`,
  `download_model` fetches the drafter, `downloaded` requires it, mlx-server.sh
  branches on the backend file (APC_ENABLED=1, APC_EXACT_CACHE_ENTRIES=6),
  footprint pgrep matches both. MEASURED (M5 Max): mlx_vlm.generate AR 31.3 →
  MTP block3/4 63.4 tok/s code (88% acc), prose 47.3 (55%), block 6 22.8 (worse
  than AR → default 3); server: 6/6 tool suite at temp 0 AND 0.7 (streaming
  tool_calls ok, per-request enable_thinking → reasoning field), 12.6k-token
  prefix 21s cold → 0.4s cached (12611 cached_tokens), footprint 19GB/24 peak,
  cold prefill 625-690 tok/s (step size 512/2048/8192 no effect), `hermes -z`
  real terminal turn correct; official drill via launchd path 6/6 (cases 1.4-1.8x
  faster than the mlx-lm run), restore + re-pause clean; SIGTERM exit leaves no
  crash report. Fallback: venv missing → mlx-lm path (still works, no MTP).
- `<dsh-spike>` DeepSeek Harness feasibility (2026-08-18, uncommitted, outside
  repo): `@deepseek-ai/dsh@0.1.0-rc.7` at ~/.hermes/dsh, DSH_HOME=~/.hermes/dsh/home
  with a `hermes-local` openai-completions route → :8080 (dummy apiKeyEnv
  required) + agent-default-model patch. Headless task on the local Qwen3-30B:
  real shell tool round-trip, correct answer, 7.8s. Not wired into the dashboard;
  integration depth is the user's call (CLI-only / dashboard surface via Python
  SDK / replace Hermes — not recommended).
- `<claude-routing>` Fixed the two-brain routing (2026-08-18). (1) `_cb_gate`
  rewritten intent-based (was refusing 5/18 benign escalations as codegen/harmful):
  25/25 benign pass, 22/22 true positives + injected-context still refused. (2) New
  `aux_autoroute.py`: deterministic per-turn scorer decides when a chat question also
  goes to Claude (parallel Sonnet; Opus only on "think hard"), answer persisted +
  rendered as the deep-card (index.html streamJob/pollDeep/deepCardHTML,
  aux_agent.js window.hermesDeep), modes auto|suggest|off, /api/claude/autoroute*.
  (3) Escalate button now sends the FULL question (was first line). VERIFIED live:
  hard question → local + Claude in parallel, persisted in order; routine → local.
- `<two-model-roster>` Roster = Qwen3.8-27B (primary, MTP) + Qwen3.5-9B (background
  lane); Qwen3-30B-A3B + Hermes-3-8B removed from models.json and DELETED from the HF
  cache (user call). New `com.hermes.mlx-bg` service (:8081, mlx-server-bg.sh via the
  mlx-vlm venv — 9B checkpoints need transformers-5 tokenizer class), server.py
  `bg_lane()` + `run_agent(lane="bg")`, briefing/watchtower/intel/For-You routed to it
  (falls back to primary when down), footprint sums both lanes, `/api/models.bg`,
  model-menu "Background:" row, `custom_providers: [bg]` in ~/.hermes/config.yaml,
  install-services.sh installs the bg service. Primary switched to Qwen3.8 through
  the real switch path (hermes model.default updated). VERIFIED: briefing regenerated
  on :8081 (primary saw 0 requests); 9B tool suite 6/6 at ~88 tok/s, 7GB.
- `<on-demand-model>` (2026-09-01) Model servers are ON-DEMAND: plists RunAtLoad/
  KeepAlive false, `model-autostart-off` gate in mlx-server*.sh (fresh start token
  minted only by server.py `_mlx_start` — wake/resume/switch/restart), `main()` marks
  a down un-paused model asleep at dashboard start. Watchtower `master {briefings,
  news}` toggles + `set_evening` op + evening/breaking-override controls in the
  Mind-view card; brief/midday/evening hold during quiet hours. User state: both
  masters OFF, model paused.
- `<bugfix-2026-09-01>` Second pass, verified live: `hermes send --json` (quiet
  hid every error) + 60s send budget; intel agent pass no longer spawns `hermes -z`
  against a paused/asleep model (109×180s timeouts in dashboard.log; log shows
  "intel agent pass skipped" once/hour now); `_slept_through` relative cutoff so a
  brief ≥18:00 / evening ≥22:00 can actually send (9/9 cases); chat wakes a model
  that is down without the asleep marker + idle loop self-heals the marker
  (`_mlx_proc_alive`); schedule time inputs echo the clamped value.
