# Hermes Assistant (this Mac's local AI assistant)

An always-on personal assistant: NousResearch **Hermes Agent** + a local MLX
model + a custom dashboard. All LLM inference is local; only Telegram transport
and explicit agent tool calls (web search etc.) touch the internet.

## Architecture
- **Model server** — `mlx-server.sh` → `python3 -m mlx_lm server` on
  `127.0.0.1:8080` (OpenAI-compatible). Model:
  `mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit` (~18GB, MoE, 3-6s/turn).
- **Hermes Agent v0.18+** — `~/.local/bin/hermes`, config in `~/.hermes/config.yaml`
  (points at :8080, `approvals.mode: manual`). Source checkout:
  `~/.hermes/hermes-agent/`.
- **Serve backend** — `hermes serve --port 9119 --skip-build`: JSON-RPC over
  WebSocket at `ws://127.0.0.1:9119/api/ws?token=<t>`. Token pinned via
  `HERMES_DASHBOARD_SESSION_TOKEN` env in the plist; shared secret lives at
  `~/.hermes/dashboard/serve-token` (600). Key RPCs: `session.create`
  (returns `session_id` + durable `stored_session_id`), `session.resume`,
  `prompt.submit {session_id,text}`, `approval.respond {session_id,choice}`.
  Events (`method:"event"`, params `{type,session_id,payload}`):
  `message.delta/complete`, `tool.start/complete`, `status.update`,
  `approval.request`, `error`.
- **Dashboard hub** — `dashboard/server.py` (stdlib-only) on `127.0.0.1:7788`,
  Liquid Glass UI (`dashboard/index.html`). Two views via a header segmented
  control: **Hub** (ambient widgets) and **Mind** (the self-improvement story).
  Chat is job-based: POST `/api/chat` → `{job}`; GET `/api/chat/poll?job=` for
  streaming text/tool status/approvals; POST `/api/chat/approve`. Turns run
  through the serve backend via `dashboard/hermes_rpc.py` (hand-rolled RFC 6455
  client, stdlib only) with per-chat serve sessions persisted in the chat JSON
  (`serve_sid` ephemeral, `serve_key` durable). Falls back to one-shot
  `hermes -z --continue` if serve is down.
- **API same-origin guard (2026-09-03)** — the API has no auth token, cookie or
  CSRF token and nearly every route mutates state (`/api/chat` runs the agent,
  `/api/access` grants folders, `/api/shortcuts/run` executes,
  `/api/config/import` overwrites config), so `Handler._guard()` runs
  pre-dispatch on EVERY verb (`server.py` `_request_allowed(method, headers)` is
  the pure, unit-tested decision function). Rules: **Host** must be in
  `ALLOWED_HOSTS` = {127.0.0.1, localhost, [::1]} × {with `:DASH_PORT`, without}
  + comma-list env `HERMES_DASH_ALLOWED_HOSTS`, else 403 `{"error":"forbidden
  host"}` — that closes DNS rebinding, and it covers `/` and the static
  `aux_*.js` too so a rebound page can't even load the app shell. **Origin**,
  when present, must be `http://<allowed host>` (checked on GET as well as
  POST; `Origin: null` fails), and **Sec-Fetch-Site: cross-site** is refused on
  state-changing verbs only (a cross-site NAVIGATION to the hub sends that
  header with no Origin and must still work). Requests with NO Origin stay
  allowed — browsers always attach it cross-origin, so this is token-less CSRF
  protection that leaves curl, the launchd scripts and the Swift app's
  MessagesSync `/api/messages/ingest` POST untouched. **No CORS headers are
  ever added.** Denials log one stderr line (method, path, host, origin).
- **Agent pause/resume** — power row in the model menu. Pause `bootout`s
  com.hermes.mlx-server (KeepAlive means plain kill won't stick) and writes
  `~/.hermes/dashboard/agent-paused`; resume bootstraps the plist. Endpoints
  `/api/agent/pause|resume` (`agent_power()`); `/api/models` carries `paused`
  + cached `ram_gb`. Guards: memory_guard skips while paused, switch_model
  auto-resumes, `/api/chat` fails fast with a friendly reply, app relaunch
  does NOT auto-resume (kickstart no-ops on a booted-out service).
- **Idle-suspend (auto RAM reclaim)** — `idle_suspend_loop()` boots
  com.hermes.mlx-server out after `_idle_min()` (default 10, `IDLE_SUSPEND_MIN`
  env / `~/.hermes/dashboard/idle-suspend-min`) of no USER activity, freeing its
  ~26GB, and writes `~/.hermes/dashboard/agent-idle-suspended` (a unix-ts).
  DISTINCT from a manual pause: a pause stays down + fails fast (`agent-paused`);
  an idle-suspend AUTO-WAKES on the next turn. "USER activity" = dashboard
  `/api/chat` (`note_user_activity()`) ∪ newest Telegram/hub turn in state.db —
  background briefing/watchtower does NOT count and never wakes it. The chat
  worker calls `agent_wake()` (bootstrap + poll `/v1/models`, ~30-50s cold
  start) BEFORE the turn; while asleep the loop also wakes on a fresh
  telegram/hub turn (best-effort — the dashboard can't intercept that path, so
  the first Telegram msg after sleep may miss). Guards: memory_guard + the loop
  both skip while suspended; the two marker files never coexist (pause/resume/
  wake clear the other). Endpoints `/api/agent/wake`, `/api/agent/idle_config`
  {enabled,minutes}; `/api/models` carries `idle_suspended`/`idle_enabled`/
  `idle_min`; model menu shows a "sleeping · Wake now" row. Disable: touch
  `~/.hermes/dashboard/idle-suspend-off` (or the settings toggle).
- **Code knowledge graph (Graphify)** — `graphifyy` (official double-y pkg) in
  an ISOLATED venv `~/.hermes/graphify-venv` (never the fragile framework
  Python). `graphify update .` re-extracts the repo via tree-sitter (no LLM,
  ~2.8k nodes/5.1k edges) → `graphify-out/{graph.json,graph.html,GRAPH_REPORT.md}`;
  `graphify watch .` rebuilds on change. `aux_graphify.py` serves it read-only:
  `GET /api/graph/stats` (counts + god-nodes) and `/api/graph/query?q=` (node +
  neighbors — cheap "explain" the agent/dashboard use instead of grepping).
  graph.json is mtime-cached. `graphify install --platform claude|hermes`
  registers the `/graphify` skill into those agents' configs (harness-gated —
  run via `!`). `graphify-out/` is generated (~5MB) — gitignore unless you want
  it versioned.
- **Model toggle** — header pill is a switcher (`/api/models`, `/api/models/switch|download|add`). `mlx-server.sh` reads the chosen repo id from `~/.hermes/dashboard/active-model` (falls back to Qwen3.8-27B). Switch = write that file + `hermes config set model.default` + `launchctl kickstart com.hermes.mlx-server`, then poll `/api/health` until the new model loads. Roster seeded (`_SEED_MODELS`) with Qwen3.8-27B (primary/default) + Qwen3.5-9B (background lane) — see roster policy; user-extendable via models.json (`_model_registry()` merges NEW seed entries into an existing models.json by id). Per-model `template_args` (roster field) → written on switch to `~/.hermes/dashboard/chat-template-args` → mlx-server.sh passes it as `--chat-template-args`; Qwen3.8 defaults `{enable_thinking:false}` (its template thinks at xhigh by default, ~22k tokens on trivial prompts). `POST /api/models/thinking {enabled}` flips it (on = low effort) and restarts the server; the model menu shows a Thinking on/off row when `/api/models`.thinking.supported. Qwen3.8-27B is `model_type qwen3_5` (drill 6/6 on both backends).
- **Model-server backends (`mlx-server.sh`)** — roster entries may set `backend: "mlx_vlm"` (+ `draft_model`/`draft_kind`/`draft_block_size`); the switcher writes `~/.hermes/dashboard/server-backend` (JSON) and mlx-server.sh execs `~/.hermes/mlx-vlm-venv/bin/python mlx-vlm-launch.py` (mlx_vlm.server, OpenAI-compatible, uvicorn) instead of `python3 -m mlx_lm server`; missing venv → silently falls back to mlx-lm. The venv is ISOLATED (`install-mlx-vlm-venv.sh`: mlx-vlm 0.6.14 + mlx 0.32.1 + transformers 5 — never into the framework Python, it breaks mlx-lm). Qwen3.8-27B runs there with its NATIVE MTP drafter `mlx-community/Qwen3.8-27B-MTP-bf16` (0.9GB; the `-MTP-4bit` drafter ships NaN weights, mlx-vlm #1931) → speculative decoding: M5 Max 31 → 63 tok/s code / 47 prose (block 3; block 6 is SLOWER than AR; ~88%/55% acceptance), Hermes drill cases ~1.4-1.8x faster. `APC_ENABLED=1` (+`APC_EXACT_CACHE_ENTRIES=6`) = exact prefix cache — hybrid SSM models use "exact" whole-prefix snapshots, ~64KB/token; the ~18k-token Hermes system prompt goes 26s cold → 0.4s cached. Prefill is compute-bound at ~630-690 tok/s regardless of `--prefill-step-size` — first turn of a fresh session pays ~25s, nothing else does. `mlx-vlm-launch.py` shims: (1) mlx≥0.32 made `mx.random.state` read-only → mlx-vlm 0.6.14 crashed on every temperature>0 speculative request (`_restore_rng_state`); the launcher no-ops the restore (only RNG-stream separation, sampling stays correct); (2) `MLX_VLM_DEFAULT_REASONING_EFFORT` env → default `reasoning_effort` when thinking is on (template default xhigh); (3) atexit `os._exit(0)` because mlx 0.32 segfaults in the CompileCache destructor at teardown (that's the "Python quit unexpectedly" dialog — harmless but noisy). Thinking toggle on this backend = `--enable-thinking --thinking-budget 8192` + effort low. `_mlx_footprint_gb` pgrep matches both backends. mlx_vlm's `/v1/models` also lists every cached model; Hermes must send the exact model id (both backends load whatever id the request names). Downloads run in a bg thread via `huggingface_hub.snapshot_download`. Rationale: 30B MoE resident ~18GB; a dense 8B (~5GB) is plenty for tool-calling since Claude does the coding.
- **Background lane (2026-08-18)** — a SECOND always-on small model, `com.hermes.mlx-bg`
  (`mlx-server-bg.sh`, :8081, Qwen3.5-9B-4bit via the mlx-vlm venv — its
  tokenizer_config uses transformers-5's `TokenizersBackend`, which mlx-lm's pinned
  transformers<5 can't load; mlx-lm hangs on the first request). server.py:
  `BG_MODEL_URL`, `bg_model()` (`~/.hermes/dashboard/bg-model`), `bg_online()`
  (15s-cached probe), `bg_lane()` → `{lane, chat_url, model, hermes_args}` falling back
  to the primary when :8081 is down. `run_agent(..., lane="bg")` adds `--provider
  custom:bg -m <bg>` (named custom provider `bg` in `~/.hermes/config.yaml`
  `custom_providers:` — the ONLY way to point one `hermes -z` at another base_url;
  `OPENAI_BASE_URL` env is ignored when config has base_url). Producers on the lane:
  briefing (`run_agent(lane="bg")`), watchtower synthesis + intel agent pass, For-You
  (`_model_chat_url/_active_model`, `_fy_chat_url/_fy_active_model` consult `bg_lane()`).
  Chat/clip/drill stay on the primary. `_mlx_footprint_gb` sums both lanes;
  `/api/models.bg {model,online,label}`; model menu shows a "Background: …" row. Pause
  parks only the primary (bg is ~7GB). Verified: briefing regenerated on :8081 while
  :8080 saw zero requests; 9B tool suite 6/6, ~88 tok/s.
- **Roster policy (2026-08-18, user call): TWO models** — Qwen3.8-27B (primary,
  active, hermes `model.default`) + Qwen3.5-9B (background lane). Everything else was
  removed from models.json AND the HF cache (Qwen3-30B-A3B, Hermes-3-8B deleted;
  re-download from the menu if ever needed). The only official Qwen3.8 sizes are 27B
  and a 2.4T MoE — there is no small 3.8; 3.5-9B is the nearest sibling (same
  architecture/template). **+ one opt-in alternative brain (2026-09-03, user call):
  `orcarouter/Qwen3.8-27B-Uncensored-MLX`** — the abliterated ("jailbroken", no
  refusals) build of the same 27B, MLX 4-bit g64, identical layout/tokenizer/template
  to the primary (mlx_vlm backend, Thinking toggle, template_args all carry over).
  Never the default — pick it in the model menu. Optional roster fields it introduced
  (seeds backfill them into models.json): `ignore_patterns`/`allow_patterns` (passed
  to `snapshot_download` by `download_model()` — the repo holds 2/4/6/8-bit
  subfolders, 95GB, plus a root mirror of 4-bit; we pull root + `mtp/` ≈ 17GB),
  `draft_subfolder` (drafter INSIDE the repo — `_draft_model_path()` resolves `mtp/`
  to the local snapshot path written into `server-backend`; `_draft_ready()` gates
  `downloaded`), `hf_offline` (→ `server-backend.hf_offline` → mlx-server.sh exports
  `HF_HUB_OFFLINE=1` + `MLX_VLM_LOCAL_ONLY=1`; the latter makes `mlx-vlm-launch.py`'s
  `_patch_local_snapshot_resolution()` map a repo id to `refs/main` → `snapshots/<sha>`
  itself, because huggingface_hub ≥1.x throws `IncompleteSnapshotError` for a partial
  mirror even offline, and online `get_model_path()` would fetch the skipped 62GB of
  subfolders at every start). Same change: `download_model()` runs through
  `_hf_python()` (venv → framework → PATH python) — the dashboard's Homebrew python
  has no huggingface_hub, so menu downloads had been failing silently. And
  `_model_downloaded()` now means COMPLETE: `_weights_complete()` checks every file in
  `model.safetensors.index.json` exists in the snapshot (HF links a shard only when its
  blob finishes) — the old "any .safetensors under the cache dir" flipped `downloaded`
  true the moment the first shard (or the 0.85GB `mtp/` drafter) landed, so the menu
  offered "switch" mid-download.
- **Claude auto-route (`aux_autoroute.py`, 2026-08-18)** — the local model NEVER used
  the think-with-claude skill (bridge log: 4 calls total, all 07-06), so routing is
  now decided in code per chat turn: `ar_score()` over the USER message (+3 explicit
  "think hard/ask claude", +1 per judgment cue, +1 long, +0.5 "?", −1 per mechanical
  cue, −2 very short, −3 command-like); ≥ `min_score` (2.5) → Claude runs IN PARALLEL
  with the local turn (`quick`=Sonnet, `deep`=Opus only on an explicit "think hard");
  the answer is persisted as a bot message with `deep:{model,ms,reason,…}` and shown
  as the deep-card. Wraps `_chat_worker` only (aux_metrics redefines `_new_job` —
  don't rely on job subclassing); `/api/chat/poll` carries `deep`, UI polls
  `/api/claude/autoroute/job?job=` after done while state=thinking. Modes
  `auto|suggest|off` in settings.json `auto_route` (`GET/POST /api/claude/autoroute`,
  `POST /api/claude/autoroute/score {q}` dry-run). Verified live: hard question →
  local + Sonnet (21s, parallel) persisted in order; routine → local only.
- **Claude escalation master switch (2026-09-03)** — `auto_route.mode` only ever
  gated the auto-router, so the manual Escalate button and For-You kept spending
  the Max plan with routing off. One global switch now: settings.json
  `claude_escalation: {enabled}` (default **true**), helper
  `claude_escalation_enabled()` + `GET/POST /api/claude/escalate {enabled}` in
  `aux_claudebridge.py`. Enforced at the CHOKE POINT — the top of
  `claude_think()`, before `_cb_gate` — because that is the only function that
  shells out to `claude -p`, so the one check covers the router, the button,
  For-You and anything added later. Refusal reuses the module's existing shape
  (`{ok:False, refused:True, reason:"escalation_off", text}`) so every caller
  handles it with code it already has; deliberately NOT written to
  claude-bridge-log.jsonl (that log and the bridge card's `recent_24h` measure
  Claude USAGE, and a switched-off call spends nothing) — one stderr line the
  first time per process instead. `aux_autoroute._ar_before` also short-circuits
  on it so a disabled bridge spawns no thread and shows no `deep` spinner, WITHOUT
  mutating the stored mode; `GET /api/claude/autoroute` carries `claude_escalation`.
  Settings read fresh per call (the file is tiny) — no restart, no cache.
- **Bridge gate rewrite (`_cb_gate`, 2026-08-18)** — the old keyword regexes refused
  5/18 realistic escalations ("memory leak", "password field", ".env approach", "wipe
  the cache", "should I create a component"). Now INTENT-based: secrets = action verb
  within ~50 chars of a secret noun (or noun … "to send/upload"); destructive =
  imperative rm -rf/wipe/format/delete-everything (question sentences exempt);
  approval-bypass always; codegen = imperative produce-verb + code artefact in a
  NON-question sentence, or filename + write/implement/refactor. Test set:
  25/25 benign allowed, 12/12 harmful + 10/10 codegen refused, injected-context
  caught (`scratchpad t_gate.py` pattern). The tool-lockout remains the real control.
- **Notification master toggles (2026-09-01)** — watchtower.json `master:
  {briefings, news}` (normalized in `_wt_load`, flipped via `/api/watchtower`
  op `set_master`): `briefings` gates the 8am/midday/evening ticks, `news`
  gates `_breaking_pass` + rss_keyword rules. New op `set_evening` (+`evening`
  and `master` now in the GET payload — evening previously had NO toggle
  anywhere). UI: two accent master switches + Evening wrap + breaking
  "can override quiet hours" controls in the Mind-view Watchtower card.
  Also: brief/midday/evening ticks now hold during quiet hours, and a wake
  after 6 PM marks the morning brief done WITHOUT sending (the widget still
  refreshes via briefing_loop; the evening wrap covers the day). ALL Telegram
  pushes flow through `_wt_send_telegram` (verified: only call site sending),
  so these gates are the complete off-switch; `~/.hermes/cron/` ticker runs
  but has zero jobs.
- **Watchtower/idle bug fixes (2026-09-01, second pass)** — (1) `_wt_send_telegram`
  calls `hermes send --json` (NOT `--quiet`: quiet mode prints nothing on failure,
  so every delivery problem logged as a bare "exit 1"), parses `{error|skipped|
  success}`, `SEND_TIMEOUT=60` (20s timed out under load and the day's brief was
  lost — the date guard flips after compose). (2) `_intel_agent_pass` skips (one
  log line/hour) unless a lane is genuinely up: `bg_lane()` silently falls back
  to the PRIMARY when :8081 is down, so with on-demand models it spawned an
  hourly `hermes -z` against a paused/asleep model (109 × 180s timeouts in the
  log). Background work never wakes the model. (3) `_slept_through(cur, sched,
  cutoff)` replaces the absolute "after 18:00 / 22:00 mark done without sending"
  cutoffs in the brief/midday/evening ticks — measured from the SCHEDULED slot
  (+120 min grace), because a brief set to ≥18:00 or an evening wrap at ≥22:00
  could never send. (4) `_chat_worker` wakes the model when it is down WITHOUT
  the asleep marker (crash / external bootout / refused start), not only when
  marked; `idle_suspend_loop` self-heals that marker only after the down
  state persists >=60s across ticks with no server process (`_mlx_proc_alive()`
  pgrep), no fresh start token and no job in flight — the ~4s bootout->start
  gap of switch/restart/resume must never mark a loading model (a stale marker
  while online would silence memory_guard and stop idle-suspend). (5) Schedule time inputs write back the
  clamped value (`timeSaved()` in aux_watchtower.js) — the ops clamp hours
  (brief 0-23, midday 11-17, evening 16-23) yet always answer ok:true, so a
  typed 06:00 evening silently became 16:00 while the field still showed 06:00.
- **Live System widget** — `/api/sys` (2s cache) + a 3s client updater (`liveSystem`) patches the meters in place without a full hub re-render; shows a pulsing "live" dot.
- **Markets widget** — Yahoo chart; sparkline is anchored at the previous close so its shape matches the daily %; meta shows "at close · <date>" / "live" from `marketState`+`regularMarketTime` (don't present holiday/last-session data as live).
- **Modular widget hub** — the Hub view is a widget registry, not fixed HTML.
  Backend: `WIDGETS` catalog (20 widgets) in server.py, each with a provider;
  `/api/hub` returns the enabled layout + fetched data (each provider `safe()`-
  wrapped + cached), `/api/catalog` lists all, `/api/layout` POST does
  add/remove/move/set, layout persists in `layout.json`. Frontend: `renderHub()`
  builds widget shells + a per-id `RENDER{}` map; "Widget Center" modal adds
  widgets; Customize toggles edit mode (reorder/remove). Per-category accent
  color (`data-cat` → `--wac`) + distinct body layouts differentiate widget
  types. New widgets: markets (Yahoo chart, sparklines), crypto (CoinGecko),
  hackernews (HN API), rss, github trending, messages (local iMessage chat.db,
  needs Full Disk Access), agent_pulse (state.db recent sessions), worldclock,
  reminders (osascript), notes, quicklinks. Network providers use `_ssl_context`
  (certifi) + `_cached`. Widget config (tickers/coins/rss_feeds/quicklinks/
  timezones) rides in settings.json via `/api/settings`.
- **Three views** (header segmented control, `setView()`): **Hub** (widgets),
  **Mind** (skills/memory/insights), **Console**. Customize/Add-widget live in
  the header (`#hubctl`, Hub-only). Model pill doubles as a live agent-state
  indicator (thinking/writing/running-tool) driven by `setAgentState()` from the
  chat stream. Header greeting is time-aware; fullscreen state comes from the
  Swift `NSWindowDelegate` → `:root[data-fullscreen]`.
- **Agent Console** — GET `/api/console` (`console_activity()`): reads state.db
  `messages.tool_calls`/`tool_name` JOIN `sessions.source` → a live timeline of
  every tool the agent runs across ALL surfaces (dashboard/Telegram/CLI), polled
  every 3s. This is the research-validated "watch it work" edge.
- **MLX memory ceiling (stability)** — the KV/prompt cache can balloon under
  concurrent load and take the Mac down. Two layers in server.py: `mlx_admission()`
  (15s-cached footprint) makes /api/chat + _generate_briefing REFUSE new model work
  at/above `MLX_SOFT_GB` (default 50); `memory_guard_loop` polls every 30s and above
  `MLX_HARD_GB` (56) does a `_mlx_restart()` (bootout→bootstrap — NOT kickstart -k)
  to free the balloon. User override: `/api/model/mem_override {allow}` touches
  `~/.hermes/dashboard/mem-override`; `/api/model/mem_free` = manual cache-clear
  restart. Surfaced in models_payload().mem + the model-menu memory row. When
  running many model-drilling agents concurrently, expect the ceiling to engage —
  that's it working, not a bug.
- **Message Center (P2.4)** — the APP (holds FDA; launchd python cannot) reads
  ~/Library/Messages/chat.db via MessagesSync in main.swift: SQLite online-backup
  snapshot (no WAL locks), 60s timer, POSTs token-guarded /api/messages/ingest
  (token ~/.hermes/dashboard/messages-token 0600). aux_messages.py rebinds
  WIDGETS["messages"] provider + EXPANDERS["messages"].
  * Apple epoch: message.date = NANOSECONDS since 2001-01-01 on modern macOS
    (seconds pre-High-Sierra): unix = 978307200 + (d/1e9 if d > 1e11 else d).
  * attributedBody: modern rows leave text NULL; body is an NSKeyedArchiver blob —
    byte-scan: find b"NSString", skip to next '+', length prefix 0x81→u16LE /
    0x82→u32LE / else 1 byte, slice UTF-8. Same logic in expand_messages (py) and
    MessagesSync.decodeBody (Swift) — CHANGE BOTH OR NEITHER.
  * FDA probe = open(2) on chat.db returns EPERM when denied.
  * ⚠ REBUILDING THE APP DROPS THE FDA GRANT (ad-hoc cdhash changes) — after any
    build-app.sh run, the user must re-add the app in Full Disk Access. main.swift
    is FROZEN after P2.4 for this reason; batch Swift changes.
- **AUX MODULE GOTCHA — never `from datetime import datetime` in an aux_*.py.**
  aux modules exec into shared server.py globals; `from datetime import datetime`
  rebinds the global name `datetime` to the CLASS, so any other code that later
  calls `datetime.datetime(...)` / `datetime.timedelta(...)` silently breaks
  (returns None / raises). Import under a private alias instead:
  `import datetime as _mymod_datetime`. (aux_config/aux_recorder currently do the
  bare import — tolerate it, but new modules must alias.)
- **Adding a hub widget from an aux module**: mutate `WIDGETS[id]={title,icon,
  size,cat,provider}` + `EXPANDERS[id]=fn` at module load, append `id` to the
  layout order if absent, and in aux JS set `RENDER[id]` (body), `EXPAND_RENDER[id]`
  (pop-out), and `WICONS[id]` (icon). See aux_claude_usage.py/.js.
- **WKWebView JS dialogs** — the app implements `runJavaScriptAlert/Confirm/
  TextInputPanel` (NSAlert sheets). WITHOUT them WKWebView silently returns
  false/nil for `confirm()`/`alert()`/`prompt()` — which is why the model
  switch/pause buttons (gated on `confirm()`) "didn't work". Any new `confirm`
  in the web UI now works in the app.
- **Pop-outs open instantly + abortable** — `openPop` no longer awaits
  `/api/hub` (uses `window.lastHub` cached meta), shows a loader shell, fetches
  only `/api/expand` via an AbortController; `closePop` aborts it (bumps
  `popToken`) so slow feeds don't queue a backlog. Clicking outside cancels.
- **Custom widget icons** — `widgetIcon(id)` / `WICONS` map: bespoke two-tone
  SVG per widget (accent fill + currentColor stroke), used in widget shells,
  pop-out headers, and the Widget Center. Add a `WICONS[id]` when adding a widget.
- **exec-include ORDER RULE** — `expanders_extra.py` is exec'd **just before
  `class Handler`** (after every inline def). It MUST stay there: functions it
  redefines (console_activity, expand_markets, expand_rss, expand_weather,
  expand_system…) only win because they run last. Its header imports
  collections/concurrent.futures/datetime/sqlite3 — exec'd code can't rely on
  server.py's function-local imports. New wave-2 routes: `/api/markets/search`,
  `/api/mind_extra`; `system_sampler_loop` thread starts in `main()`.
- **Chat modes** — deck[data-chat]: "" split · "hidden" widgets-only (FAB
  restores) · "full" = TRUE fullscreen chat with a 252px sidebar
  (conversations + Tools quick-actions + model status; `renderChatSide()`),
  persisted in localStorage `hermes_chat`.
- **Rich pop-outs (all 20 widgets)** — clicking any widget opens `/api/expand?id=`
  detail. Providers: the first ~10 live inline in server.py (`EXPANDERS` dict);
  the rest are in **`dashboard/expanders_extra.py`**, which server.py `exec()`s
  into its own globals (so helpers resolve) and which ends with
  `EXPANDERS.update({...})` (this is why `markets` etc. are the richer versions).
  Frontend renderers: inline `EXPAND_RENDER{}` for the first ~10, plus
  **`dashboard/expand.js`** (served at `/expand.js`, loaded last so its
  assignments win) for the 11 agent-built ones. To add/edit a rich widget:
  edit those two aux files, not the giant inline blocks. Both were built by an
  `ultracode` Workflow (one agent per widget). VERIFY renderers headless by
  evaling expand.js in node with stubbed helpers + live `/api/expand` data
  (catches the `esc`-on-number class of throw). Weather glyphs are custom SVG
  (`weatherGlyph`), NO emoji anywhere, all times 12-hour.
- **Contextual ask** — right-click any widget/row or select text → "Ask Hermes
  about this" injects `Regarding "<data>" — ` into chat; widget cards pop out
  (`openPop`/`#wpop`) with a built-in ask box. `widgetGist()` builds per-widget
  context. Widget grid uses `grid-auto-flow:dense` to pack any enabled set.
- **Capabilities surface** — GET `/api/capabilities` (server.py `capabilities()`)
  reads Hermes's OWN state, not a copy: skills scanned from
  `~/.hermes/skills/**/SKILL.md` frontmatter (grouped by category folder),
  memory facts from `~/.hermes/memories/USER.md`, usage/insights from a
  read-only `state.db` query (`mode=ro`, WAL-safe) — sessions, tool calls,
  tokens, per-platform split, 14-day activity series. The Mind view renders
  growth stats, a category-filtered skill browser, remembered facts, and an
  inline-SVG activity sparkline. This is how the "gets better over time" story
  is made visible; the machinery itself is Hermes's (curator prunes skills on a
  7-day cycle, memory flushes each session).
- **Telegram gateway** — launchd `ai.hermes.gateway.plist` (installed via
  `hermes gateway install`). Your own bot (created with @BotFather); the
  allowlist is `TELEGRAM_ALLOWED_USERS` in `~/.hermes/.env` — only the numeric
  user ids listed there may command it. All `TELEGRAM_*` keys live in that file.
- **Computer use** — cua-driver 0.7.0 (`/Applications/CuaDriver.app`,
  `com.trycua.driver`), TCC granted (Accessibility + Screen Recording).
  `computer_use` is in `_HERMES_CORE_TOOLS`, auto-enabled on all platforms
  when the driver exists. Background control — does not steal cursor/keyboard.
  `hermes computer-use doctor` for the health matrix.
- **Services** — `install-services.sh` installs launchd agents. Dashboard +
  serve are always-on (RunAtLoad + KeepAlive); the MODEL services
  (`com.hermes.mlx-server`, `com.hermes.mlx-bg`) are ON-DEMAND since
  2026-09-01 (battery): RunAtLoad=false, KeepAlive=false, plus a gate at the
  top of mlx-server.sh / mlx-server-bg.sh — with
  `~/.hermes/dashboard/model-autostart-off` present the script exits unless a
  FRESH (<180s) start token exists (`model-start-ok` / `model-start-ok-bg`).
  Only server.py's `_mlx_start()` mints the primary token (used by
  agent_wake / resume / switch_model / `_mlx_restart`), so the app's blind
  `ensureServices()` kickstart at launch and login autostart are inert; a
  crash stays down until the next chat/Telegram wake. Nothing mints the bg
  token — start that lane by touching it manually. `main()` marks a down,
  un-paused model idle-suspended at dashboard start so chat/Telegram/"Wake
  now" wake it transparently. A pause now genuinely survives reboots (it
  didn't before — RunAtLoad used to resurrect the model at login). Delete
  `model-autostart-off` + rerun install-services.sh with true/true to restore
  always-on. `--uninstall` removes. Telegram gateway separate (`hermes
  gateway`). Logs in `~/.hermes/logs/`.
- **App** — `/Applications/Hermes Assistant.app`, real Swift/AppKit WKWebView
  shell (source `app/main.swift`, rebuild `app/build-app.sh`).
- **Versioning + self-update (2026-09-03)** — repo-root `VERSION` (currently
  1.0.0) is the single source of truth; `app/build-app.sh` stamps it +
  `git rev-parse --short HEAD` into `CFBundleShortVersionString`/
  `CFBundleVersion` (and honours `HERMES_SKIP_INSTALL=1`, which CI uses to build
  without touching /Applications). `dashboard/aux_update.py` serves
  `/api/version`, `/api/update/check|status`, POST `/api/update/apply|channel`;
  the check tries GitHub Releases (ETag + 6h cache in
  `~/.hermes/dashboard/update-check.json`, ≤5s budget), then the same call with
  a token (`GITHUB_TOKEN`/`HERMES_UPDATE_TOKEN`/`gh auth token` — for a private
  repo), then `git ls-remote --tags origin`; a daemon thread re-checks at
  boot+60s and every 6h. **Release repo slug** (`_upd_repo()` in aux_update.py,
  `repo_slug()` in update.sh): env `HERMES_UPDATE_REPO` → the github.com slug
  parsed off `git remote get-url origin` → default
  `Emran05/hermes-assistant-local` (the PUBLIC repo releases are published
  from; this private working repo is never hardcoded). Channel
  `update.channel` = `stable` (tags) | `main` (origin/main, git checkouts only)
  in settings.json. `aux_update.js` renders
  ONE card into Settings › System & Data (it appends to `#sec-system`, or to
  `#view-mind` and lets the shell's relocator re-home it — no edit to
  aux_settings_shell.js) plus a dot on the `#tab-mind` gear. Applying starts
  root `update.sh` DETACHED (`start_new_session`, log
  `~/.hermes/logs/update.log`) so it survives the dashboard restart
  install-services.sh performs; state in
  `~/.hermes/dashboard/update-state.json`. `update.sh` handles both git and
  tarball installs (SHA256SUMS-verified), refuses a dirty tree, never touches
  `~/.hermes` data, and only PRINTS instructions when `app/` changed (a rebuild
  drops FDA — see the Message Center note). `install.sh` is the fresh-Mac
  bootstrap; `.github/workflows/{ci,release}.yml` gate syntax + home-path
  hygiene and build/sign/publish on a `v*` tag.

- **DeepSeek Harness (`dsh`) spike — 2026-08-18, NOT integrated** — installed
  locally (not global) at `~/.hermes/dsh` (`@deepseek-ai/dsh@0.1.0-rc.7`, MIT,
  Node 22 via nvm, 306MB); `DSH_HOME=~/.hermes/dsh/home` holds `settings.yaml`
  (pi-ai route `hermes-local` → `http://127.0.0.1:8080/v1`, `api:
  openai-completions`, both roster model ids; needs a dummy `apiKeyEnv` —
  `HERMES_LOCAL_API_KEY=local-anything` — pi-ai refuses keyless routes) and
  `cordis.patch.yml` (agent-default-model → hermes-local/Qwen3-30B). Verified:
  `dsh --profile headless "<task>"` did a real shell tool round-trip on the local
  30B and answered correctly in 7.8s. Telemetry off (`DSH_TELEMETRY_MODE=DISABLED`,
  also the default). Web UI: `dsh web` on :3080; programmatic: Python SDK
  (`deepseek-harness-sdk`, JSON-RPC stdio) — that's the hook if it ever gets a
  dashboard surface. Model ids sent by dsh must match the loaded model (see
  backend note above).

## Commands
```bash
./install.sh --dry-run                # fresh-Mac bootstrap (preflight + plan)
./install-services.sh                 # (re)install + start the three services
./update.sh --dry-run                 # what an update would do (stable channel)
cat VERSION; curl -s localhost:7788/api/version   # what's running
launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard   # restart dashboard
tail -f ~/.hermes/logs/{dashboard,serve,gateway}.log        # logs
hermes gateway status                 # Telegram gateway health
hermes -z "hi"                        # test the agent directly
curl -s localhost:8080/v1/models      # is the model up
```

## Hard-won gotchas (do not re-learn these)
- **transformers must stay <5**: mlx-lm 0.31.x *claims* it needs
  transformers>=5 but 5.x crashes it (`AutoTokenizer.register` AttributeError).
  `pip install 'transformers<5'`. Ignore pip's dependency warning.
- `mlx_lm.server` binary is not on PATH (user-site install) — always
  `python3 -m mlx_lm server`.
- Hermes real config keys: `model.default`, `model.context_length`,
  `approvals.mode`. Use `hermes config set`, don't hand-copy config files.
- `hermes serve --skip-build` needs `hermes_cli/web_dist` to exist — one-time
  `npm install --workspace web && npm run build -w web` in
  `~/.hermes/hermes-agent` (node via nvm v22).
- `hermes -z` is non-interactive: approval-needing actions fail closed. The
  hub chat now goes through serve (interactive approvals); `-z` is only the
  fallback path and for cron-style one-shots.
- launchd bootout→bootstrap needs `sleep 3` or "Bootstrap failed: 5".
- Framework Python 3.12 lacks SSL roots — dashboard uses certifi in
  `_ssl_context()`. `sysctl` needs `/usr/sbin` in PATH under launchd.
- Serve auth on loopback: `Authorization: Bearer <token>` or `?token=` on the
  WS URL. No auth = 401 on all `/api/*`.
- **The hub runs in a WKWebView** (native app). After editing index.html you
  must reload the WebView (⌘R in the app) — restarting the dashboard service
  alone won't refresh an already-open window. WebKit constraints shaped the
  Liquid Glass CSS: no SVG `feDisplacementMap` edge-refraction (renders blank);
  glass = `backdrop-filter: blur+saturate` + inset specular + hairline + depth
  shadow + `::before` sheen. An ambient aurora (`body::before`, slow drift,
  frozen under reduced-motion) sits behind so the glass has something to refract.
- **Theme toggle trap**: `:root[data-theme="light"]` and `["dark"]` must each
  re-declare the FULL palette. Setting only `color-scheme` leaves a dark-OS
  `@media (prefers-color-scheme:dark)` block's color tokens active while the UI
  claims to be light. (Hit and fixed 2026-07-04.)

## Not yet done (needs the user's accounts)
- Google Workspace (read + draft only, never wire SMTP/send — user's explicit
  safety posture; enforced by absence of send capability). Needs OAuth Desktop
  JSON from Google Cloud Console (browser required).
- Discord (bot token + channel IDs from dev portal; browser required).
- Phase 2 (iMessage/Teams/Slack/Notion) only after user asks.
- The hub approval UI (`/api/chat/approve` + inline buttons) is wired but has
  not yet been exercised by a real dangerous-command approval — verify the
  first time one fires.
- macOS TCC: if the agent can't read ~/Documents/~/Desktop when running under
  launchd, grant python3 (or the service) Full Disk Access in System Settings.
