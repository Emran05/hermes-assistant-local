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
- **Agent pause/resume** — power row in the model menu. Pause `bootout`s
  com.hermes.mlx-server (KeepAlive means plain kill won't stick) and writes
  `~/.hermes/dashboard/agent-paused`; resume bootstraps the plist. Endpoints
  `/api/agent/pause|resume` (`agent_power()`); `/api/models` carries `paused`
  + cached `ram_gb`. Guards: memory_guard skips while paused, switch_model
  auto-resumes, `/api/chat` fails fast with a friendly reply, app relaunch
  does NOT auto-resume (kickstart no-ops on a booted-out service).
- **Model toggle** — header pill is a switcher (`/api/models`, `/api/models/switch|download|add`). `mlx-server.sh` reads the chosen repo id from `~/.hermes/dashboard/active-model` (falls back to the 30B). Switch = write that file + `hermes config set model.default` + `launchctl kickstart com.hermes.mlx-server`, then poll `/api/health` until the new model loads. Roster seeded (`_SEED_MODELS`, verified HF ids) with Qwen3-30B (current) + lighter Hermes-3-8B / Qwen3-8B / Qwen3-14B / Qwen3-4B; user-extendable via models.json. Downloads run in a bg thread via `huggingface_hub.snapshot_download`. Rationale: 30B MoE resident ~18GB; a dense 8B (~5GB) is plenty for tool-calling since Claude does the coding.
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
  `hermes gateway install`). Bot **@emran_hermes_bot**; only user 8487169327
  (the user's Telegram, display "Enzo Renny") may command it.
  `TELEGRAM_*` in `~/.hermes/.env` (~line 339).
- **Computer use** — cua-driver 0.7.0 (`/Applications/CuaDriver.app`,
  `com.trycua.driver`), TCC granted (Accessibility + Screen Recording).
  `computer_use` is in `_HERMES_CORE_TOOLS`, auto-enabled on all platforms
  when the driver exists. Background control — does not steal cursor/keyboard.
  `hermes computer-use doctor` for the health matrix.
- **Always-on** — `install-services.sh` installs launchd agents
  `com.hermes.mlx-server`, `com.hermes.dashboard`, `com.hermes.serve`
  (RunAtLoad + KeepAlive, logs in `~/.hermes/logs/`). `--uninstall` removes.
  The Telegram gateway service is separate (managed by `hermes gateway`).
- **App** — `/Applications/Hermes Assistant.app`, real Swift/AppKit WKWebView
  shell (source `app/main.swift`, rebuild `app/build-app.sh`).

## Commands
```bash
./install-services.sh                 # (re)install + start the three services
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
