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
