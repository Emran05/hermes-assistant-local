# Hermes Assistant (this Mac's local AI assistant)

An always-on personal assistant: NousResearch **Hermes Agent** + a local MLX
model + a custom dashboard. All LLM inference is local; only Telegram transport
and explicit agent tool calls (web search etc.) touch the internet.

## Architecture
- **Model server** — `mlx-server.sh` → `mlx-vlm-launch.py` (mlx_vlm, or
  `--mlx-lm` for the mlx-lm fallback) on `127.0.0.1:8080` (OpenAI-compatible).
  Default model `mlx-community/Qwen3.8-27B-4bit` + its MTP drafter (see backends).
- **Hermes Agent v0.18+** — `~/.local/bin/hermes`, config in `~/.hermes/config.yaml`
  (points at :8080, `approvals.mode: manual`). Source checkout:
  `~/.hermes/hermes-agent/`.
- **Serve backend** — `hermes serve --port 9119 --host 127.0.0.1`: JSON-RPC over
  WebSocket at `ws://127.0.0.1:9119/api/ws?token=<t>`. Token pinned via
  `HERMES_DASHBOARD_SESSION_TOKEN` env in the plist (0600 from birth); shared
  secret at `~/.hermes/dashboard/serve-token` (600). The plist sets
  `HERMES_WEB_DIST=/dev/null/…` so 0.18's token-embedding web UI is never
  mounted (C117; no `--skip-build`, it needs a dist; re-verify on upgrade) and
  `HERMES_TUI_CHECKPOINTS=1`, the ONLY way serve snapshots writes for Flight
  Recorder Undo (C114; gateway writes never can). Key RPCs: `session.create`
  (returns `session_id` + durable `stored_session_id`), `session.resume`,
  `prompt.submit {session_id,text}`, `approval.respond {session_id,choice}`.
  Events (`method:"event"`, params `{type,session_id,payload}`):
  `message.delta/complete`, `tool.start/complete`, `status.update`,
  `approval.request`, `error`.
- **Dashboard hub** — `dashboard/server.py` (stdlib-only) on `127.0.0.1:7788`,
  Liquid Glass UI (`dashboard/index.html`). Three tabs in a header segmented
  control: **Hub** (ambient widgets), **Settings** (internal id `tab-mind` /
  `view-mind`, the old "Mind" view; the self-improvement story lives there)
  and **Console** (2026-11-09 audit E148).
  Chat is job-based: POST `/api/chat` → `{job}`; GET `/api/chat/poll?job=` for
  streaming text/tool status/approvals; POST `/api/chat/approve`. Turns run
  through the serve backend via `dashboard/hermes_rpc.py` (hand-rolled RFC 6455
  client, stdlib only) with per-chat serve sessions persisted in the chat JSON
  (`serve_sid` ephemeral, `serve_key` durable). **No one-shot fallback**
  (C36/C118): with serve down the turn is refused, "nothing was run".
  **A reply lost to a restart (2026-11-09 audit E18):** jobs live only in
  memory, so `/api/chat` stores the user message with `awaiting_reply: true`
  and `_finish_chat_job` clears it (`_clear_awaiting_reply`) in the write that
  stores the reply. A conversation whose LAST message still carries the marker,
  stamped before this process started (`_CHAT_PROC_START`), with no turn
  running, lost its reply (`_lost_reply_due`): GET `/api/history` appends a
  `virtual: true` lost-reply row (`kind: "lost_reply"`, `err`) and **never
  writes** — a GET is reachable by cross-site navigation and MCP `chat_get` —
  while the next POST `/api/chat` stores the notice once, in the write that
  adds the next message. Old data without the marker never gets a notice; the
  reply is not fetched back. `chat_jobs_active()` (public, resolved by name)
  is what Update Apply asks before restarting the dashboard (409 `chat_busy`
  unless `force:true`). **Conversation ids are case-insensitive on this disk**
  (APFS: `chats/NEEDSYOU.json` IS the drafts file): every hidden-session check
  casefolds — server.py's `session_hidden(sid)`, and the same `casefold()`
  test in aux_branch, aux_convos and MCP `chat_get` — and the
  one-turn-per-conversation check and delete tombstones key on
  `session_key(sid)` (E69). A new hidden check must casefold too. The main
  window's approval card and Quick Ask's render the command line for line
  (`white-space: pre-wrap`, no inner scroll), mark hidden characters ⟦U+XXXX⟧,
  a bare CR, a blank run of 40 characters OR 80 columns (the server's
  C-longline rule: a tab is 8 columns, U+3000/U+2800 are 2) and 4+ stacked
  combining marks, show the Trust policy's reason (`approval._policy.reason`)
  as one quiet line, and add a "N lines — scroll up and read all of it" line
  above the buttons for a long command: index.html's `shape` and
  aux_quickask.js's `qaShape` share ONE body between `SHAPE-BODY` markers
  (ASCII escapes only, compared line by line by `t_s3_shape_copies.py`), and
  both carry a copy of the hidden-character class — change both or neither
  (E15; `t_e15_approval_multiline.py`). The reason's first part is the pattern
  key, which the approval-gate plugin builds from the AGENT's tool call
  (`computer_use:<action>`), so `permissions.decide()` shows a one-line,
  96-character form of it (`_reason_key`) and the policy's own words always
  follow.
- **Serve turn invariants (`hermes_rpc.py`, 2026-10-21)** — `approval.respond`
  has no request id and resolves serve's OLDEST pending entry, so `_Approvals`
  mirrors serve's FIFO and EVERY verdict (card or policy) goes through it; a
  card has a minted `approval_id`, only the head is answered, once, as
  `"once"`/`"deny"` (never `"approve"`: MCP elicitation reads it as decline);
  TTL `min(HUB_APPROVAL_TTL=290, gateway_timeout − ≤10 s)` → deny; an answer of
  unknown delivery denies all pending; `_chat_approve` 409s `{stale:true}`
  unless the `approval_id` matches (B01/C33). One turn per conversation:
  `/api/chat` 409s `{busy:true, busy_job, reply}` — never `job`, which clients
  read as "your turn started" (B11/C113). `clarify.request` gets a "pick the
  safest option" answer, `sudo`/`secret.request` an EMPTY value (C115). Only
  `message.complete.status == "complete"` is ok (C121); a failed resume seeds
  a new session with the transcript (C23), and so does a conversation with
  earlier replies but NO stored agent session ("no agent session was stored
  for it"; not a Needs-you draft, not a branch its seed hook already seeded —
  E70). An Approve accepted after serve had already dropped the request runs
  nothing and the reply says so, as the TTL path does; a late Deny adds no note
  (E71). `RPCError` = serve answered with an
  error (not "dropped after submission"). Test: `t_b01_approval_gate.py`.
  **The owner's Stop (2026-11-12):** `POST /api/chat/stop {job}` → `_chat_stop`
  → `hermes_rpc.request_stop` (404 `{gone}` unknown, 200 `{already_done}`, a
  second stop 200 `{stopping}` and nothing sent twice). A stop and the decision
  to submit share `_STOP_LOCK` (`_claim_submit`): BEFORE submission the route
  itself ends the job ("Stopped by you before it reached the agent, so nothing
  was run", persisted, `awaiting_reply` cleared; the wake is not undone, a
  session this turn created is closed and never saved); AFTER it `run_turn`
  sends `session.interrupt` once (`_interrupt_once`, retried on a fresh
  connection), denies through the mirror, keeps the streamed text and ends
  with `stopped_reply()` — within `STOP_GRACE` (10 s, `HUB_STOP_GRACE`), which
  nothing extends; an undelivered interrupt or a turn that did not end says the
  session may still finish in the background. **Once `request_stop` returns
  only `deny` leaves for that job:** every approving answer (a card click or a
  Trust "auto") goes through `_Approvals._send()`, claimed under `_STOP_LOCK`
  (`_claim_approve`; never held across the socket call — `_approving` counts a
  send in flight, and a stop that lands mid-send sets `stop_after_approval` so
  the reply says the approval had already gone); `_respond` sends deny only;
  `_publish` shows no card after a stop; `_chat_approve` 409s `stale`. Lock
  order `_approve_lock` → `_STOP_LOCK`. A stopped job has `ok` false and
  `stopped` true (row `stopped: true`, not drawn as an error); the poll adds
  `can_stop`/`stopping`/`stopped`; `_finish_chat_job` claims `_finishing` under
  `_jobs_lock` (the request thread and the worker may both finish a job).
  Claude's parallel auto-route answer for a stopped turn is dropped. UI:
  `#stopbtn` takes `#send`'s slot (`chatJobMark`/`syncStop`/`stopTurn`), Quick
  Ask has the same; Esc is not bound. Tests `t_stop_run_turn.py`,
  `t_stop_route.py`, `t_stop_ui.py` force the interleavings. **Pause**
  (`agent_power`) waits `PAUSE_DOWN_WAIT_S` (20 s) for the server to go and
  sets `_pause_busy` meanwhile: `/api/chat` refuses as paused, Wake now 409s
  `{pausing}`, a running worker waits, and `agent_wake()` itself refuses
  (`t_stop_pause_window.py`, `t_stop_pause_slow_exit.py`).
  **Trust policy verdicts (`permissions.py` `decide()`, 2026-11-09 audit E03):**
  serve reports only the FIRST dangerous pattern a command matches, so
  `decide()` re-scans the command text against `_DANGER_PATTERNS` — a COPY of
  upstream `tools/approval.py` `DANGEROUS_PATTERNS` (Hermes 0.18.0; 73
  regexes, same order and flags, its descriptions = the terminal keys of
  `PATTERN_CLASS`; `t_e03_compound_auto.py` pins it) — and resolves
  most-restrictive over EVERY match, so Never and the floors apply to each
  part. **Auto is honoured only for one simple, fully judged command:** no
  command text, more than `SCAN_MAX` (512) characters, a shell operator or
  substitution (`;`, `&`, `|`, a newline or CR, a backtick, `$(`, `<(`, `>(`, `<<`), a redaction mark serve left
  (`***`, `...`, `[REDACTED`, `«redacted`), `( ) { }`, a control character or
  a quoted / backslashed / `$`-expanded command word each turn Auto into Ask
  (`unjudged`; the reason says why, e.g. "more than one command — asking").
  Two upstream patterns backtrack exponentially and are compiled as
  equivalent unambiguous forms (`_DP_LINEAR`; `t_e03_scanner_evasion.py`
  checks equivalence) — a new `(…)*`/`(…)+` group must be audited the same
  way, because `decide()` is reachable from GET `/api/permissions/test` and
  regex time holds the GIL. **Re-compare the table with upstream on every
  Hermes upgrade** (update `PATTERN_CLASS` with it). Residual by design: Auto
  still approves ONE command ≤512 chars that matches only Auto-tier patterns,
  plain `>`/`<` redirects and `sudo`/`env`/`xargs`/`find -exec` forms included.
  GET `/api/permissions` carries the policy file's `sha`; Review & re-trust
  and any save over an UNTRUSTED policy must send it back (400 without, 409
  when the file changed since it was shown — E115). A config restore MERGES
  the imported policy and lands it UNSIGNED/untrusted, its plan listing every
  from→to tier change (E112).
- **API same-origin guard (2026-09-03)** — the API has no auth token, cookie or
  CSRF token and nearly every route mutates state (`/api/chat` runs the agent,
  `/api/access` grants folders, `/api/shortcuts/run` executes,
  `/api/config/import` overwrites config), so `Handler._guard()` runs
  pre-dispatch in do_GET/do_POST (the only verbs implemented; others get a 501)
  (`server.py` `_request_allowed(method, headers)` is the pure, unit-tested
  decision function). **Peer first (C131):** loopback or
  `HERMES_DASH_ALLOWED_PEERS` (IPs/CIDRs), else 403 `forbidden peer` — but
  containers and other macOS accounts arrive FROM 127.0.0.1, so only the B02/F05
  per-launch secret closes that. Rules: **Host** must be in
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
  ever added.** Denials log one stderr line (method, path, peer, host, origin).
  A port-less Origin (`http://localhost` = port 80, some other server) counts
  only when it is exactly the request's own Host — for
  `HERMES_DASH_ALLOWED_HOSTS` names too — and an `https` Origin is accepted
  only for such a configured name, only as its own Host (a tunnel), never for a
  loopback name (R2-4, E125).
  Every response sends `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'`
  (B17); `Handler.timeout = 20` (C13) is per read, so `header_deadline = 20.0`
  also bounds the request line plus all headers by wall clock and closes an
  idle keep-alive connection at the same 20 s (E122). Aux modules are compiled
  with their real path, so a failure logs `at aux_<name>.py:<line>`, and a 5xx
  an aux route RETURNS logs one `[route] VERB path failed: HTTP <n>: …` line
  (non-printables escaped, capped); wrong-typed query/path/body values are a
  400, never a 500 (E123, E124). **POST bodies are parsed ONCE in
  do_POST's preamble (`_load_body`, B12)** — bad JSON/non-object 400, >8 MB 413,
  stalled 408; `_body_json()` is always a dict; only `/api/upload` reads
  `rfile`, so never read it in a route.
- **Agent pause/resume** — power row in the model menu. Pause `bootout`s
  com.hermes.mlx-server (KeepAlive means plain kill won't stick) and writes
  `~/.hermes/dashboard/agent-paused`; resume bootstraps the plist. Endpoints
  `/api/agent/pause|resume` (`agent_power()`); `/api/models` carries `paused`
  + cached `ram_gb`. Guards: memory_guard skips while paused, switch_model
  auto-resumes, `/api/chat` fails fast with a friendly reply, app relaunch
  does NOT auto-resume (kickstart no-ops on a booted-out service).
  **Every start/stop of the primary is serialised on `_lifecycle_lock`
  (R2-6, extended by the 2026-11-09 audit E98/E101)** — pause, resume, a
  switch's bootout/bootstrap, the Thinking-toggle restart, "Free memory",
  memory_guard's restart, the always-on watchdog and idle-suspend's bootout —
  and each re-checks the pause (and the asleep marker) INSIDE the lock: a
  pause that lands mid-restart wins, "Free memory" refuses while paused or
  asleep, and idle-suspend re-runs its activity checks right before the
  bootout. The background workers take it NON-blocking (whoever holds it is
  already changing the server; the next tick re-measures). A switch that
  lands under a pause answers `paused: true` and leaves the files naming the
  new model. `agent_wake()` does not take the lock (a ms gap the
  mlx-server.sh pause check makes harmless). **mlx-server.sh itself refuses to
  load while `agent-paused` exists, always-on installs included**, so a pause
  survives a re-login, the app's blind kickstart and an update (E102). Resume,
  a switch, Wake now and the Thinking/Free-memory restarts call
  `note_user_activity()`, so idle-suspend counts from the owner's start (E21).
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
  Direct model-server work (drill, evals) must hold it up: `with
  model_hold(reason, user=):` — the loop treats `_model_held()` like a chat in
  flight (C07). **The drill (`aux_promotion.py`) and the eval suite exclude each
  other** (checked and claimed under aux_evals' `_EV_LOCK`, by name — E117); a
  drill refuses while paused or while a chat turn runs. Its temporary swap
  records a restore point in `~/.hermes/dashboard/drill-restore.json` (0600,
  atomic) and `_pro_restore_original` never undoes the owner: if the active
  model is no longer the drilled one it touches nothing (`skipped: "switched"`),
  if the agent is paused or asleep it re-activates only the original's FILES
  (`_activate_model`, nothing starts), else `switch_model(orig)` (E19). A
  restore point left by a dashboard that died mid-drill is applied the same way
  at the next start, and a successful owner switch while no drill runs deletes
  it (E103). A failed drill or restore is shown to the owner (E119).
  `_mlx_start()` clears the asleep marker (B20); the loop marks
  asleep only once `_mlx_primary_down()` confirms the bootout (C24).
  `model_always_on()` disables idle-suspend and `_boot_model_state()` starts the
  model with the dashboard (C65).
- **Prewarm after wake (backlog #1)** — `agent_wake()` returned as soon as
  `/v1/models` answered, so the model was LOADED but nothing was PREFILLED and
  the user's first real turn paid the cold prefill of the ~18k-token Hermes
  system prompt (~25s; every later turn is ~0.2s off the APC exact-prefix
  cache — see `docs/plans/post-v1-baseline.md`). Now `agent_wake()` fires
  `_prewarm_kick("wake")` the moment `model_online()` FIRST returns true (only
  there — the `wait=False` path returns before the server answers, so there is
  nothing to prefill), and `_prewarm_after_wake()` runs on a detached daemon
  thread. It does ONE trivial turn (`PREWARM_PROMPT` = "Reply with exactly:
  ok") through the SAME `hermes_rpc.run_turn` serve WebSocket the dashboard and
  Telegram use — the byte-identical system prompt is the whole point, a
  `hermes -z` no-op is a different invocation and need not share the trie
  entry. The synthetic turn is invisible to every "is a human using this"
  signal: NOT registered in `CHAT_JOBS`, never calls `note_user_activity()`,
  writes no `chats/*.json` (`PREWARM_SESSION` = `__prewarm__` is skipped by
  `list_sessions()` and refused by `/api/history`), and its serve session
  carries `source: "prewarm"` + title `__prewarm__` — both excluded in
  `_newest_external_turn_ts()` (the title too, in case a future serve build
  ignores the source we pass), or it would reset the idle clock on every wake
  and the model would never sleep again. `run_turn(..., source=)` is the only
  `hermes_rpc.py` change; real turns still default to `"hub"`. Skips when:
  disabled, no serve client, paused, a real chat job in flight, the briefing is
  generating, or `mlx_admission()` refuses. Holds no lock the chat path needs
  (a `/api/chat` arriving mid-prewarm goes straight through — serve handles
  concurrent sessions); 120s cap (`HERMES_PREWARM_TIMEOUT`), one stderr line
  with the elapsed ms. Toggle: settings.json `prewarm: {enabled}` (default
  true) via `GET/POST /api/agent/prewarm {enabled}`; state also rides in
  `models_payload().prewarm {enabled,last_ms,last_at,last_result}`.
- **Code knowledge graph (Graphify)** — `graphifyy` (official double-y pkg) in
  an ISOLATED venv `~/.hermes/graphify-venv` (never the fragile framework
  Python). `graphify update .` re-extracts the repo via tree-sitter (no LLM,
  ~2.8k nodes/5.1k edges when it was first built in July — the checked-in graph
  predates round 3, so a rebuild would be larger; re-measure, E154)
  → `graphify-out/{graph.json,graph.html,GRAPH_REPORT.md}`;
  `graphify watch .` rebuilds on change. `aux_graphify.py` serves it read-only:
  `GET /api/graph/stats` (counts + god-nodes) and `/api/graph/query?q=` (node +
  neighbors — cheap "explain" the agent/dashboard use instead of grepping).
  graph.json is mtime-cached. `graphify install --platform claude|hermes`
  registers the `/graphify` skill into those agents' configs (harness-gated —
  run via `!`). `graphify-out/` is generated (~5MB) — gitignore unless you want
  it versioned.
- **Unified local index (1.1.0)** — `dashboard/aux_index.py`: SQLite **FTS5**
  (stdlib `sqlite3`, no extension, no embeddings, no network) at
  `~/.hermes/dashboard/index.db` (**0600**, and the WAL/-shm sidecars inherit
  it). Closes the §2 gap in `docs/plans/purpose-and-direction.md` — every
  context source was wired but nothing could search ACROSS them. Schema:
  `items(id TEXT PRIMARY KEY, source, ts, title, body, ref, meta)` + an
  **external-content** `items_fts(title, body)` (text stored once, FTS keeps
  only the inverted index) kept in sync by three triggers — a missed `'delete'`
  row is a permanent phantom hit, so never write `items` around them.
  **Five adapters**, one row per unit: `chat` (one per conversation, user+bot
  turns only — tool/approval/status rows and `__prewarm__` are skipped, the
  same invariants aux_convos' search has), `note` (notes.json Scratchpad),
  `message` (Message Center rows; nothing while the app lacks FDA),
  `calendar` (icalBuddy `eventsFrom:/to:` over ±30 days with `-df %Y-%m-%d`,
  falling back to `macos_calendar()`'s today-only data), `watchtower`
  (intel.json items + curated, deduped by URL). **An adapter returns `None` for
  "store absent" (nothing is pruned) and a list for "store present" (anything
  not returned IS pruned)** — that distinction is the whole reason a
  transiently unreadable or half-written intel.json does not silently empty
  the news half of the index. **Freshness, two paths:** `index_touch(source,
  id)` on the write paths the module reaches WITHOUT editing them — `save_chat`
  is wrapped (the runtime-override pattern aux_shortcuts uses for
  `access_preamble`) and `POST /api/notes` is re-registered as an aux route
  (POST_ROUTES is checked before server.py's inline chain); both routes call
  server.py's `notes_post()`, aux_index only adds the touch. A conversation
  delete prunes its row at once (`sync=True`, C58; Hermes' state.db copy
  remains); `_ix_conn()` recreates a deleted index.db (C108). Touches are
  COALESCED on a 2s drain thread so three saves in one turn cost one re-index and no request waits on
  sqlite. Plus a sweep at start+60s and every 30 min that upserts changed rows
  (mtime for file-backed sources, full content compare otherwise, so a
  no-change sweep writes ZERO rows) and prunes deleted ones.
  `GET /api/search?q=&source=&limit=` → `{ok,q,results[{id,source,title,ts,
  snippet,mark_start,mark_len,ref,untrusted}],took_ms,sources{},notice}`
  (`notice`/`untrusted` because the agent reads it via an unfenced terminal
  tool, C39; `HIDDEN_SESSIONS` are never indexed). BM25 with the title
  weighted 10×, `q`≤200 chars, `limit`≤50, `sources` counted over the WHOLE
  match set (so the UI's filter row is right even when filtered).
  `GET /api/search/status` = per-source counts + last sweep. **The server never
  emits markup** — FTS5's own `snippet()` is deliberately unused; a result is
  plain text plus `(mark_start, mark_len)` offsets and the client escapes the
  three slices itself (same contract the chat search already shipped, so
  `cvHi()` was untouched). **Query sanitisation is not optional**: `_ix_tokens`
  keeps `\w+` runs only, double-quotes each (a quoted token is a literal
  phrase, never an operator, so `OR`/`NEAR`/`NOT`/`title:`/a lone `"` are all
  searched as words) and re-attaches one trailing `*` as the prefix operator —
  never interpolate user text into a MATCH expression any other way. UI: the
  sidebar/header search box is now **"Search everything"** with a source filter
  row (All · Chats · Notes · Messages · Calendar · News, with counts), results
  grouped by source with a chip; a chat hit opens the conversation and flashes
  the bubble as before, note→`openPop('notes')`, message→`messages`,
  calendar→`today`, news→its own URL (or the News Feed pop-out). `cvFetch`
  falls back to `/api/sessions/search` when `/api/search` 404s, so a dashboard
  that has not been restarted since the upgrade keeps its old chat-only search
  instead of going dead. Agent-visible via `skills-snapshot/hermes-search/`
  and one stable line in `access_preamble()`. Load order: aux files exec
  SORTED, so aux_index runs BEFORE aux_messages/aux_watchtower — it must not
  read their `MSG_STORE`/`INTEL_FILE` at module load (it resolves them by name
  at sweep time, with its own literals as the fallback).
- **Needs-you inbox (1.1.2)** — `dashboard/aux_needsyou.py` + `aux_needsyou.js`
  (one `<script>` tag in index.html after aux_onboarding.js, plus one CSS line
  `.w[data-cat="assistant"]{--wac:var(--iris)}`). Closes the §2 "Attention"
  gap in `docs/plans/purpose-and-direction.md` — delivery existed, triage did
  not — and implements §4 item 1 / §4b "Needs-you inbox" (attention router v1).
  **Seven collectors, each CONSUMING a store nothing else owns** (`github`, round 3,
  is in the GitHub bullet): `message`
  (Message Center rows unread or incoming in the last 48 h), `calendar`
  (`macos_calendar()`; next event <2 h, overlaps flagged), `watchtower`
  (breaking rows from `watchtower-log.jsonl` **filtered on `suppressed == ""`**
  — that field IS "it passed the masters", `_master_on` is never re-evaluated
  here — plus intel.json `curated`), `approval` (live `CHAT_JOBS` entries with
  `state == "approval"`), `reminder` (a richer osascript than `w_reminders`,
  because names without due dates cannot be triaged; falls back to it),
  `email` (**absent by design** — aux_google holds read-only OAuth but ships no
  message reader; a future provider only has to appear under one of
  `_NY_MAIL_PROVIDERS`). A collector returns `[]` when its store is absent and
  never raises; `sources{}` reports present/absent per source, so the inbox
  degrades one source at a time. **Nothing fetches, polls a feed, or duplicates
  a store.**
  **`_ny_classify(item)` is the §4b decision tree, pure and rules-first**:
  `now` = VIP (two-way history ≤30 days OR a resolved contact) AND a concrete
  time-bound ask (question / deadline ≤24 h / event <2 h / an approval
  waiting); `today` = wants a reply with no hard deadline, a thread you have
  replied in, or an event later today; `never` = automated sender, no history,
  no deadline; **confidence <0.6 collapses to `today`, never to `now`** (a
  low-confidence `never` is a silent miss, so both ends fall to the bucket that
  costs least to be wrong about). `now` is capped at five and the overflow
  falls to `today` rather than vanishing. **Sender order (2026-11-09 audit
  E04), messages and email alike:** (1) the HANDLE says machine (an SMS short
  code, `no-reply`/`notifications`/`alerts@`/`automated` —
  `_NY_AUTOMATED_HANDLE_RE`) → automated; (2) recent two-way history or a
  contact name → a person; (3) only then may the BODY's words
  (`_NY_AUTOMATED_BODY_RE`: "newsletter", "out for delivery", "verification
  code"…) say automated — so a friend's "did you see the school newsletter?"
  is never filed under Never. Trade-off measured on a 60-case table: some
  automated senders (bank/toll-free numbers you replied to, businesses saved as
  contacts, `marketing@` with no trigger word) now land in today, never in now. **TAG, NEVER MOVE**: snooze/done/
  reclassify write only to this module's store, so no upstream row is read,
  archived or deleted and every decision is reversible.
  **The model pass is off by default and never wakes anything.** settings.json
  `needs_you.model_pass` (default false) is checked BEFORE `bg_online()`, so a
  disabled pass does not even probe the lane; enabled + a genuinely online lane
  sends the **`today` bucket only** (never `now` — the model must not
  manufacture urgency) as a 4-shot JSON prompt to `bg_lane()["chat_url"]` with
  a 20 s timeout. It may promote to `now` **only** at confidence ≥0.8 AND with
  a concrete deadline INSIDE the rules' own window (due within the deadline
  window or missed by at most `NY_PAST_DUE_W`) AND a `vip`/`known` sender —
  never an automated sender or a stranger (E74), and demote only at ≥0.8, never a row with a reply ask or
  deadline, `never` only for an automated sender (C42); any failure, malformed
  answer or unknown id leaves the rule result standing. "today/tomorrow…" is a
  deadline only after by/due; a past deadline is live for 6 h (B09/C60).
  **Every needsyou.json read-modify-write goes through `_ny_update(mutate)`**
  (B08/C10). On threaded sources (`_NY_THREADED`, messages) a decision covers
  only the message the row SHOWED (`*_through` stamps; `_ny_supersede` clears
  it when something newer arrives, B04). Messages is `present` only with FDA
  and a fresh sync; blind sources are named via `payload.source_notes` (C18).
  Store `~/.hermes/dashboard/needsyou.json` (**0600**, it holds message
  previews): per-item `bucket/reason/confidence/snoozed_until/done_at/
  reclassified_to`, a `history` map (ident → last time you replied — the VIP
  signal, remembered because the Message Center keeps only the LAST message per
  conversation), and `shown`/`acts` rows pruned to a rolling 7 days. Two
  breaking alerts from one watchtower pass keep separate ids (E73).
  **Metrics (§5 now has a source)**: `now_precision` = acted (done|open|draft)
  within 24 h ÷ shown-as-now, `now_snooze_rate`, `reclass_rate`. A snooze
  counts AGAINST precision, per §5. **`shown` is recorded only where a person
  could see it** (C06/C57): a plain GET (not `?mark=0` — aux routes get
  parse_qs LISTS), the pop-out, and the widget's visible-only `POST
  /api/needsyou/seen {ids[]}` beacon; `w_needsyou` and the brief hook never mark.
  The widget's reload after an act is a READ (`?mark=0`), so it never marks
  every row shown (E79).
  Routes: `GET /api/needsyou` (60 s cache, rebuilt on a background thread,
  **never blocks** — a cold call answers `{building:true}` and kicks the
  thread; **the payload lists `never` rows (≤ `NY_NEVER_MAX` = 50, newest
  first) only for an explicit `?never=1`** — they carry message previews, so
  the widget, scripts and MCP get only `never_count`, while the pop-out
  (`/api/expand`) lists them and they can be re-filed (E04). **The rebuild loop
  runs only while a reader came in the last `NY_CLIENT_IDLE_S` = 180 s** and
  writes needsyou.json only when something changed (`last_seen` at most hourly);
  the brief/midday/evening lines refresh through `_ny_fresh()`. After a long
  idle the first read returns the old payload while a rebuild starts — an
  hourly MCP `needs_you` call may see a stale inbox with no age shown (E97).
  With Briefings off or watchtower.json unreadable, the empty state promises
  no "next brief" (E78)), `POST /api/needsyou/act {id,action:done|snooze|open|reclassify|
  draft,until?,to?,seen?}`, `POST /api/needsyou/seen`, `GET
  /api/needsyou/metrics`. `draft` starts a real chat job on the PRIMARY lane
  **only when `model_online()` is already true** — otherwise `{ok:false,error:
  "model asleep",wakeable:true}` and the UI offers the wake button. Drafts run
  in the reserved **`DRAFT_SESSION` (`needsyou`)** — server.py's
  `HIDDEN_SESSIONS = {__prewarm__, needsyou}` are never listed, read back,
  searched, indexed or chatted in, so MCP never sees one (C38); the quoted
  message rides only in the live prompt, each draft gets a fresh serve session,
  one at a time, and `no_escalate` keeps it from Claude (C37).
  Widget `needsyou` (title "Needs you", size wide, cat **assistant** — a new
  category, hence the new `--wac` line) is **inserted at the FRONT of the
  layout when layout.json has never seen the id** (§4 item 6) and an existing
  user order is never reordered. UI: "Now" group (≤5), collapsed "Today (n)",
  a "Later n · Never n" footer, one quiet trust line ("now precision 78% · 12
  snoozed"); pop-out adds every bucket, reclassify buttons and a "Why?" toggle
  showing the RULE reason even when the model moved a row. Flat rows (no cards
  inside cards), ≥40 px targets, explicit `transition-property`, bucket colours
  straight off `--bad`/`--warn`/`--muted`.
  Rhythm: `_wt_needsyou_line()` in aux_watchtower.py appends one counts line to
  the morning brief (**after** `_brief_compose`'s synthesis, so the LLM rewrite
  cannot drop or reword it), the midday pulse and the evening wrap.
  **Load order:** aux files exec SORTED, so this module runs AFTER aux_messages
  (MSG_STORE exists) but BEFORE aux_watchtower — every watchtower global
  (`INTEL_FILE`, `WT_LOG`, `_wt_log_read`, `_wt_load`) is resolved BY NAME AT
  CALL TIME with local literals as the fallback, the discipline aux_index
  documents.
- **Model toggle** — header pill is a switcher (`/api/models`, `/api/models/switch|download|add`). `mlx-server.sh` reads the chosen repo id from `~/.hermes/dashboard/active-model` (falls back to Qwen3.8-27B). Switch = `_activate_model()` (active-model + chat-template-args/server-backend + `hermes config set model.default`, all or none — C26) + bootout/bootstrap, then poll until the new model loads. Roster seeded (`_SEED_MODELS`) with Qwen3.8-27B (primary/default) + Qwen3.5-9B (background lane) — see roster policy; user-extendable via models.json (`_model_registry()` merges NEW seed entries into an existing models.json by id). **Every models.json read-modify-write goes through `models_update(mutate_fn)`** (under `_state_lock`, read fresh, mutate in place / return a list / return False for no write; an unreadable file raises and nothing is written — 2026-11-09 audit E113): two writers used to lose one another's change. Model ids are matched CASE-INSENSITIVELY for the curated facts — seeds (`_seed_entry`), `_id_get` tables — so the uncensored badge, the sticky flag and both ack gates hold for every spelling, and `add_model` never stores a second row for the same repo in another letter case (E104). The download disk check counts the GB still to come for downloads already in flight and is re-judged at the claim, so two clicks cannot both pass (E105; the menu's own pre-check does not yet). A sampling value too large to be a float is dropped like any other invalid one, never raised (E106). Per-model `template_args` (roster field) → written on switch to `~/.hermes/dashboard/chat-template-args` → mlx-server.sh passes it as `--chat-template-args`; Qwen3.8 defaults `{enable_thinking:false}` (its template thinks at xhigh by default, ~22k tokens on trivial prompts). `POST /api/models/thinking {enabled}` flips it (on = low effort) and restarts the server; the model menu shows a Thinking on/off row when `/api/models`.thinking.supported. Qwen3.8-27B is `model_type qwen3_5` (drill 6/6 on both backends).
- **Model-server backends (`mlx-server.sh`)** — roster entries may set `backend: "mlx_vlm"` (+ `draft_model`/`draft_kind`/`draft_block_size`); the switcher writes `~/.hermes/dashboard/server-backend` (JSON) and mlx-server.sh execs `~/.hermes/mlx-vlm-venv/bin/python mlx-vlm-launch.py` (mlx_vlm.server, OpenAI-compatible, uvicorn); otherwise (or with the venv missing) the mlx-lm fallback runs as `python3 mlx-vlm-launch.py --mlx-lm <mlx_lm server args>` (pgrep `mlx-vlm-launch` matches both). The venv is ISOLATED (`install-mlx-vlm-venv.sh`: mlx-vlm 0.6.14 + mlx 0.32.1 + transformers 5 — never into the framework Python, it breaks mlx-lm). Qwen3.8-27B runs there with its NATIVE MTP drafter `mlx-community/Qwen3.8-27B-MTP-bf16` (0.9GB; the `-MTP-4bit` drafter ships NaN weights, mlx-vlm #1931) → speculative decoding: M5 Max 31 → 58.7 tok/s code / 47.8 prose at block 3 (63 is block 4, which is worse on prose; block 6 is SLOWER than AR; ~88%/55% acceptance; `tools/bench/README.md`, 2026-09-04), Hermes drill cases ~1.4-1.8x faster. `APC_ENABLED=1` (+`APC_EXACT_CACHE_ENTRIES=6`) = exact prefix cache — hybrid SSM models use "exact" whole-prefix snapshots, ~64KB/token; the ~18k-token Hermes system prompt goes 26s cold → 0.4s cached. Prefill is compute-bound at ~630-690 tok/s regardless of `--prefill-step-size` — first turn of a fresh session pays ~25s, nothing else does. `mlx-vlm-launch.py` shims: (1) mlx≥0.32 made `mx.random.state` read-only → mlx-vlm 0.6.14 crashed on every temperature>0 speculative request (`_restore_rng_state`); the launcher no-ops the restore (only RNG-stream separation, sampling stays correct); (2) `MLX_VLM_DEFAULT_REASONING_EFFORT` env → default `reasoning_effort` when thinking is on (template default xhigh); (3) atexit `os._exit(0)` because mlx 0.32 segfaults in the CompileCache destructor at teardown (that's the "Python quit unexpectedly" dialog — harmless but noisy); (4) `_patch_tokenizer_eos` (2026-11-12): after the model loads, the TOKENIZER's eos joins `processor.additional_eos_token_ids` (the batch path's stop set; the StoppingCriteria keeps it across reset) — mlx_vlm otherwise stops only on `config.eos_token_id`, and a fine-tune whose config names `<|endoftext|>` while its template ends turns with `<|im_end|>` (the DeepSeek-V4-Pro 9B builds) streamed a literal `<|im_end|>` after every reply; a no-op for a model that already stops there (checked live on the stock Qwen3.5-9B; `t_launcher_tokenizer_eos.py`). Thinking toggle on this backend = `--enable-thinking --thinking-budget 8192` + effort low. `_mlx_footprint_gb` pgrep matches both backends. mlx_vlm's `/v1/models` also lists every cached model; Hermes must send the exact model id. Downloads run in a bg thread via `huggingface_hub.snapshot_download` and never fetch `*.bin/*.pt/*.pth/*.ckpt/*.pkl/*.pickle`, nor `*.py` unless the entry is `trust_remote_code: true` (`_dl_ignore_patterns`, F01). Rationale: 30B MoE resident ~18GB; a dense 8B (~5GB) is plenty for tool-calling since Claude does the coding.
  **Lockdown (2026-10-21, C130/B03/C49)** — upstream served ANY request-named repo with `--trust-remote-code`, CORS `*`, no auth. `mlx-vlm-launch.py` now pins each server to its `--model` (anything else → 400 before the loader), drops CORS, refuses any `Origin`, a non-loopback Host, `/unload`, `/cache/reset`, `/v1/realtime` and non-GET `/settings`, and requires an optional bearer key (`~/.hermes/dashboard/model-api-key` 0600, owner-created, or `MLX_VLM_SERVER_API_KEY`) everywhere but the GET liveness probes — the dashboard sends it via `_ModelAuthHandler`, Hermes via config.yaml `api_key`. **No loopback call goes through a system web proxy (2026-11-09 audit E20):** urllib reads the macOS proxy and has no loopback exemption, so the process-wide opener server.py installs is `_LoopbackDirectProxyHandler` (a loopback URL connects direct; any other URL keeps the owner's proxy, and when unsure a URL counts as NOT loopback, so the key never goes to a proxy) + `_ModelAuthHandler`. `local_urlopen(req, timeout)` is the documented helper for anything aimed at a loopback service (the lanes, the dashboard): `ProxyHandler({})`, no redirect followed, the model key stamped, and a `ValueError` for a non-loopback URL — the drill and evals use it by name; doctor has its own `ProxyHandler({})` opener. A guard that cannot install exits 78. So active-model and `model.default` must agree (`_activate_model`); a hand-edited id gets a 400, not a hot-swap. **Media must arrive inline as a `data:` URI (E100):** any other image/audio/video reference (a local path, an http(s) URL, raw-base64 `input_audio`) is a 400 before the loader, every body parsed the way the route parses it (BOM/UTF-16 too), so a local process cannot use the server to read files or fetch URLs — `hermes -z` and the API server pass such media through unchanged and now get that 400. `--trust-remote-code` only for a roster entry with literal `trust_remote_code: true`, which no route ever sets. `/api/models/add` validates `org/name` and needs `confirm:true` outside the curated set (a speed bump, not owner auth). `--local-ready <model> [<drafter>]`: with every shard on disk both launchers start `HF_HUB_OFFLINE=1` + `MLX_VLM_LOCAL_ONLY=1`.
  **DO NOT UPGRADE mlx-vlm past 0.6.14 (measured 2026-09-04, backlog #23/#25).** 0.6.16 and 0.6.17 CORRUPT OUTPUT when two requests overlap while the native MTP drafter is loaded — exactly this configuration. Two-concurrent-stream probe: 0.6.14 **6/6 rounds correct**; 0.6.17 **2/6** (token-0 floods of `!`, semantic collapse, early `finish_reason=stop`); 0.6.16 **1/3** plus a hard GPU fault (`kIOGPUCommandBufferCallbackErrorPageFault`). Confounders ruled out: reproduced with the RNG shim disabled, and with 0.6.17 forced back onto mlx 0.32.1 — the regression is in mlx-vlm, 0.6.15-0.6.16. Failures are NON-DETERMINISTIC (a clean round follows a corrupt one on the same process), so in production it is occasional garbage whenever a Telegram turn overlaps a dashboard turn, with nothing in the log. Single-stream the upgrade buys nothing (TTFT/prefill/code decode within noise, prose −8%, footprint −2GB). Re-test before ever bumping: a `baseline.py`-style concurrency probe (two 200-token streams, different prompts) must be **6/6 clean**; `install-mlx-vlm-venv.sh` now pins mlx/mlx-metal/transformers/huggingface_hub too (it let mlx float, so a re-run silently built against 0.32.2) and its header carries the rename-aside rollback plan.
  **MTP does NOT disable continuous batching (#25 answered).** `mlx_vlm/server/generation.py` `ResponseGenerator._run_impl()` (identical in 0.6.14 L1745 / 0.6.17 L1779): `if self.draft_model is not None and self.draft_kind != "mtp": self._run_speculative(); return` — MTP *falls through* to the `BatchGenerator` loop (there is even MTP-specific batch coalescing below it), the opposite of mlx-lm's `is_batchable=False`; `/health` reports `continuous_batching_enabled: true` with the drafter loaded. Nothing queues: two streams both start within ~0.5s. **But MTP is a single-stream win and a two-stream LOSS**: under 2-way load each stream collapses to **21.2 tok/s (42.5 aggregate)** vs **59.8 aggregate with no drafter** — ~29% worse, because speculative decoding and batching compete for the same GPU and the drafter's wasted work is charged twice. Keep it: the primary lane is effectively single-user (one human, one surface at a time) and solo MTP is ~47.8 tok/s vs 30.6; the concurrency that would flip the trade (briefings, watchtower, For-You) is exactly what the 9B background lane already absorbs.
  **Shim verdicts (2026-09-04):** (1) `_patch_rng_restore` can never fire in THIS configuration — `_restore_rng_state` is reached only via `_SpeculativeSamplerRNG`, constructed only by `_run_speculative()`, which MTP does not take (gate above); verified 3/3 sampled requests succeed with the patch commented out on both 0.6.14 and 0.6.17. It is insurance for a NON-MTP drafter (DFlash/EAGLE-3) only. Upstream fixed the bug in 0.6.17 (`mx.random.state[i][:] = value`), but the launcher's probe is stale and would keep no-opping the now-working function — delete the probe if 0.6.17 ever ships. (2) The `atexit`/`os._exit(0)` teardown shim is **still load-bearing on mlx 0.32.1**: SIGINT A/B without it exits `-11` with a real `EXC_BAD_ACCESS … CompileCache::CacheEntry::~CacheEntry()` crash report; mlx **0.32.2 fixes it** (exit 0, no report), so it dies with the same upgrade. SIGTERM cannot tell the difference either way (uvicorn shuts down, then the process ends by signal before module teardown). **The same destructor also fires on a THREAD exit (2026-10-27, two live crash reports):** when mlx_vlm swaps models in process ("New text_generation model requested … Stopping response generator"), the generator thread exits, dyld's thread-local cleanup runs `CompileCache::~CacheEntry` and the server segfaults — the atexit shim cannot cover that. The trigger was two Hermes sessions naming different models (Hermes stores a model per session in state.db; a conversation started before a switch kept the old id). Never let the server swap models in process on mlx 0.32.1: the C130 model pin (any other id is a 400) and `hermes_rpc._align_session_model` (an idle session is moved to the configured model every turn) are what keep it from happening. A crash leaves the launchd job LOADED but stopped, and `launchctl bootstrap` then answers "5: Input/output error" with no "already" in it, so `_mlx_start` checks `launchctl print` before giving up and kickstarts a loaded job.
  **Open question — temperature may be ignored on this path.** With `temperature=1.0, top_p=0.95` and the MTP drafter loaded, three identical prompts returned **byte-identical completions** on both 0.6.14 and 0.6.17, drafter or not. The server appears to sample greedily (or reseed identically per request) on the batch path, i.e. the dashboard's Thinking/temperature knob may not do what it claims. Not investigated — backlog item.
- **Background lane (2026-08-18)** — a SECOND, optional small model, `com.hermes.mlx-bg`
  (on demand since 2026-09-01 and nothing starts it for you — see Services;
  `mlx-server-bg.sh`, :8081, Qwen3.5-9B-4bit via the mlx-vlm venv — its
  tokenizer_config uses transformers-5's `TokenizersBackend`, which mlx-lm's pinned
  transformers<5 can't load; mlx-lm hangs on the first request). server.py:
  `BG_MODEL_URL`, `bg_model()` (`~/.hermes/dashboard/bg-model`), `bg_online()`
  (15s-cached probe), `bg_lane()` → `{lane, chat_url, model, hermes_args}` falling back
  to the primary when :8081 is down. `run_agent(..., lane="bg")` adds `--provider
  custom:bg -m <bg>` (named custom provider `bg` in `~/.hermes/config.yaml`
  `custom_providers:` — the ONLY way to point one `hermes -z` at another base_url;
  `OPENAI_BASE_URL` env is ignored when config has base_url). Producers on the lane:
  briefing (`run_agent(lane="bg")`, tools-free), watchtower synthesis + the opt-in
  intel agent pass (web_search only), For-You
  (`_model_chat_url/_active_model`, `_fy_chat_url/_fy_active_model` consult `bg_lane()`).
  Chat/clip/drill stay on the primary. `_mlx_footprint_gb` sums both lanes;
  `/api/models.bg {model,online,label}`; model menu shows a "Background: …" row. Pause
  parks only the primary (the bg lane is an estimated ~9.3-9.6 GiB with its 4 APC
  entries — the model advisor's figure; the old "~7GB" predates that tier; re-measure
  with footprint). Verified: briefing regenerated on :8081 while :8080 saw zero
  requests; 9B tool suite 6/6. ~88 tok/s is its SHORT-prompt speed: at the 24-40k
  contexts the lane actually runs it decodes at 32-42 tok/s (mlx-bg.log medians: 42.0
  at 24-32k, n=171; 32.3 at ≥32k, n=552).
- **Roster policy (2026-08-18, user call): TWO models** (the default pair; the opt-in
  alternatives — orcarouter since 2026-09-03, five more in round 3 — are below) — Qwen3.8-27B (primary,
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
  **+ five curated opt-in alternatives (round 3, 2026-10-26)** — the stock MoE
  `mlx-community/Qwen3.6-35B-A3B-4bit` (faster; an advisor ALTERNATIVE until a local
  drill passes) and four uncensored builds: `PocketAiHub/Qwen3.8-27B-Abliterated-MLX`
  (weights in `4bit/`), `AutisticAF/Huihui-Qwen3.8-27B-abliterated-mlx-4Bit`
  (TEXT-ONLY: no image or computer-use screenshot turns),
  `Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit` (uncensored MoE) and
  `vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit` (background lane; its repo states
  NO licence and the converter is anonymous). All `verified:false`, no drafter,
  `allow_patterns`-scoped, re-verified at a pinned sha plus a header-only strict-load
  dry run on mlx_vlm 0.6.14 (record `tools/tests/unit/t_r3_roster_hfmeta.json`, checked
  offline by `t_r3_roster_catalog.py`; re-verify by hand before editing an entry). None
  has run on this Mac yet; the two MoE `ram` figures (23, 26) are estimates.
  **+ one more (2026-11-12, owner request):**
  `digiflow/DeepSeek-V4-Pro-Qwen3.5-9B-UNCENSORED-MLX-4bit` @6471ad9 — the MLX build of
  xtools-at's DeepSeek-V4-Pro-Qwen3.5-9B Heretic (the source is bf16 transformers and
  does not run here). Chosen over laewr's mlx_lm build because it keeps the vision
  tower; tied to the abliterated weights by comparing dequantized rows with laewr's
  (fixture `weights_check`). No `role` (usable as the assistant or written to bg-model);
  its template is the source's own, so the catalog test compares it with the xtools-at
  repo (`stock_templates`), not with stock Qwen3.5. Verify a new entry with
  `tools/bench/hf_strict_dry.py` (header-only strict load + processor + tool parser).
  Ran live on 2026-11-12 through mlx-vlm-launch.py on a side port (not drilled through
  aux_promotion): correct answers, the right tool with clean arguments (streamed too),
  a tool result used, image input works, no thinking leak, ~24-30 tok/s for 220 tokens,
  after the eos shim (4) above. `verified` stays false until the 6-case drill passes.
  Seeds cannot be removed from models.json (the migration re-inserts them; hiding one
  would need a roster flag). **New OPTIONAL roster fields, validated wherever they are
  read** — models.json is hand-editable and config-importable, so server.py's
  `_roster_subfolder`/`_roster_sampling` and mlx-vlm-launch.py's
  `_clean_subfolder`/`_clean_sampling` are deliberate COPIES that drift guards in
  `t_r3_roster_launcher.py` keep identical (change all or none):
  * `model_subfolder` — the weights live in a repo subfolder (PocketAiHub: `4bit/`
    beside 2/6/8-bit and bf16 in one 134 GB repo); ≤4 segments, ≤64 chars, no `.`/`..`,
    no absolute path, no backslash. `download_model()` honours a stored allow list only
    when every pattern lives under the subfolder, else pulls `<sub>/*`; an invalid value
    starts nothing and `_model_downloaded()` is False for it (`_model_subfolder_invalid`);
    completeness, `_model_download_gb()` and doctor's model check judge
    `<snapshot>/<sub>` (`_catalog_dl_gb` gives no estimate once the owner repoints a
    seed's subfolder). server-backend now always names its `model` and may carry
    `model_subfolder` (which forces `hf_offline`) and `sampling`; mlx-server.sh applies
    them only when that `model` equals active-model, and both launch scripts export
    `MLX_VLM_MODEL_SUBFOLDER`/`MLX_VLM_SAMPLING_DEFAULTS`. Without the env the launcher
    reads the model's own models.json row (`HERMES_MODELS_FILE` overrides the path), so
    `tools/bench/*` and manual launches load the subfolder too. It resolves the launch id
    to `<snapshot>/<sub>` for the loader, `--local-ready` and the C130 pin aliases (the
    snapshot ROOT is never an alias); a missing subfolder is a load error, never a Hub
    download, and an invalid one exits 78.
  * `sampling` — per-model request defaults applied SERVER-side to fields a request did
    not set (an explicit value or null always wins): temperature 0-2, top_p 0-1, top_k
    0-1000, min_p 0-1, repetition_penalty 1-2, repetition_context_size 1-4096,
    presence/frequency_penalty -2..2 (`_ROSTER_SAMPLING_BOUNDS`); invalid values are
    DROPPED, never clamped; `{}` = none. Hooks: mlx_vlm
    `request_normalization._build_gen_args` (every OpenAI/Anthropic route and the batch
    path), mlx-lm `APIHandler.validate_model_parameters`. Server-side because a Hermes
    custom-provider `extra_body` would miss the dashboard's own model calls (the drill).
    No shipped entry sets it; the temperature open question above may apply here too.
  * `uncensored` — STICKY: a curated seed's `true` beats an edited or imported `false`
    (`_roster_uncensored`). Never the default, never in `_ONB_CATALOG`/`_onb_tier`,
    never the advisor's Recommended slot; the menu shows an Uncensored badge and "no
    refusals — the approval gate is the only brake", and a switch needs the
    acknowledgement (Model advisor bullet).
  * `verified` — true only after the 6-case tool drill. A BADGE ("unverified until
    drilled" while false), never a gate; a config import can set it, so the advisor
    never reads it (it reads drill records).
  * `license` — as published, validated by `_roster_license` (aux_config's regex);
    None = the repo states none.
  Seed-only facts (`_SEED_ONLY_KEYS`: size_gb, draft_size_gb, trust_remote_code) are
  never stored in models.json; `models_payload()` rows carry the normalized
  `uncensored`/`verified`/`model_subfolder`/`sampling`/`license` (`_roster_badges`).
  Pre-round-3 helpers that unit suites ast-extract by fixed name lists
  (`_model_registry`, `_model_downloaded`, `_draft_model_path`, `_catalog_dl_gb`) reach
  the new helpers through `globals().get(...)` — do not "simplify" those lookups
  without updating the suites. A `../` `draft_subfolder` is now refused too.
- **Browse Hugging Face (`aux_hfhub.py` + `aux_hfhub.js`, 2026-11-12)** — find an MLX
  model on the Hub and add it to the roster. `GET /api/models/hf/search?q=&limit=`
  (`filter=mlx`, chat pipelines only, ≤40), `GET /api/models/hf/inspect?id=` and `POST
  /api/models/hf/import {id, confirm:true, sha?, download?}`. **The only outbound path
  is `_hfx_fetch`** (https://huggingface.co, no token, a redirect handler that refuses
  any other host, ≥0.35 s between requests, 10-minute cache, size caps); a
  `Sec-Fetch-Site` of cross-site/same-site gets 403 because a navigation GET would
  otherwise make the dashboard query the Hub. **`_hfx_derive()` is the pure judgement**
  (tests drive it): MLX only (a non-MLX repo lists its `base_model:quantized:` MLX builds),
  no auto_map, not gated/private, safetensors present, a loader for the `model_type` —
  mlx_vlm when the venv has `mlx_vlm/models/<type>/` (a directory listing), else mlx-lm
  (found with `find_spec`, never imported; `-E`, not `-I`, because mlx-lm is a user-site
  install) unless the tokenizer needs transformers 5 — and `_model_fit` not "no". One
  build per repo: root weights take the standard globs, root weights BESIDE other
  builds take an exact file list (≤16, aux_config's limit; `*` crosses `/` in
  huggingface_hub's filter), a single weights subfolder (or the one named `4bit`)
  becomes `model_subfolder` + `hf_offline`. Warnings, never refusals: a non-affine
  quantization mode, no licence, no chat template / no tool section, full precision,
  the mlx-lm fallback, a tight fit, code files in the repo. The derived row carries
  backend/thinking/template_args/ctx/ram (weights GiB × 1.08 + 1)/uncensored (name or
  tags: abliterat/uncensor/heretic/decensor)/`verified:false`/license, never
  trust_remote_code. **Import re-inspects on the server** (the client's shape is never
  used), needs `confirm:true` (a curated id goes through `add_model` instead), 409s a
  `sha` that moved and a case-variant id, writes through `models_update` (re-import
  updates the row in place) and records `{sha, download_gb, …}` in
  `~/.hermes/dashboard/hf-imports.json` (0600) — `_catalog_dl_gb` reads it by name
  (`_hfx_import_size_gb`) so the menu quotes the size and the disk gate judges it. An
  imported uncensored row is behind the switch acknowledgement like a seed. UI: the card
  mounts in `#sec-models` right after the advisor card; `hermesHF.open()` (the model
  menu's "Browse Hugging Face…" row) focuses the LIVE card's search box after a delay —
  opening Settings re-renders the panel and `mindExtras()` mounts a fresh card. Nothing
  is fetched until the owner types, presses "Most downloaded" or pastes a link. A repo
  whose `model.safetensors.index.json` names shards it does not have (an index left over
  from the source model — mlx-community's gemma-3-27b-it-qat-4bit, lmstudio's
  Mistral-Small-3.2 and gemma-3n builds) is REFUSED: mlx_vlm copes, but server.py
  `_weights_complete` and the launcher's `_weights_ready` (mirrors) read the index, so
  it could never count as downloaded — fixing that means changing both. A template in
  `chat_template.json` (Gemma) is read; a config file over 8 MB is skipped, not fatal.
  Tests: `t_hfhub.py`, `t_hfhub_ui.js`.
- **Claude auto-route (`aux_autoroute.py`, 2026-08-18)** — the local model NEVER used
  the think-with-claude skill (bridge log: 4 calls total, all 07-06), so routing is
  now decided in code per chat turn: `ar_score()` over the USER message (+3 explicit
  "think hard/ask claude", +1 per judgment cue, +1 long, +0.5 "?", −1 per mechanical
  cue, −2 very short, −3 command-like); ≥ `min_score` (2.5) → Claude runs IN PARALLEL
  with the local turn (`quick`=Sonnet, `deep`=Opus only on an explicit "think hard"/
  "deep dive"/"go deep"); a `no_escalate` job and text inside a third-party data
  fence are never scored or sent (C37);
  the answer is persisted as a bot message with `deep:{model,ms,reason,…}` and shown
  as the deep-card. Wraps `_chat_worker` only (aux_metrics redefines `_new_job` —
  don't rely on job subclassing); `/api/chat/poll` carries `deep`, UI polls
  `/api/claude/autoroute/job?job=` after done while state=thinking. Modes
  `auto|suggest|off` in settings.json `auto_route` (`GET/POST /api/claude/autoroute`,
  `POST /api/claude/autoroute/score {q}` dry-run). Verified live: hard question →
  local + Sonnet (21s, parallel) persisted in order; routine → local only.
- **Claude escalation master switch (2026-09-03; default OFF and fail-closed
  since 2026-09-10, audit A02)** — `auto_route.mode` only ever gated the
  auto-router, so one global switch: settings.json `claude_escalation:
  {enabled}`, default **false**, ON only for an explicit boolean `true` (missing
  key, non-boolean, unparsable settings.json → OFF); `claude_escalation_enabled()`
  + `GET/POST /api/claude/escalate {enabled}` in `aux_claudebridge.py`. Enforced
  at the CHOKE POINT — the top of `claude_think()`, before `_cb_gate`, the only
  function that shells out to `claude -p` — so it covers the router, the button,
  For-You and anything added later. Refusal = the module's existing shape
  (`{ok:False, refused:True, reason:"escalation_off", text}`), NOT logged to
  claude-bridge-log.jsonl (that log measures USAGE), one stderr line per
  process. `_ar_before` short-circuits on it without mutating the stored mode;
  `AR_DEFAULT_MODE` is `"off"`, `_ar_escalation_on()` and `_onb_prefs()` fail
  CLOSED, the first-run JS treats only `=== true` as on. Settings read fresh per
  call. An unparsable settings.json is surfaced as `config_error` on `GET
  /api/claude/bridge` (`_cb_settings_dict()` reads the file itself) — until the
  next settings write moves it aside (C27); then the key is simply missing (OFF).
  **For-You has its own switch on top (C35, 2026-10-21):** its unattended
  ~hourly pass sent the You-Model profile + headlines, so it also needs
  `foryou_claude: {enabled}` (same rules; `GET/POST /api/claude/foryou`); aux_foryou
  checks both before building profile text and `claude_think(caller="foryou")`
  re-checks; the log records `caller`; entries `_ml_looks_secret` flags are
  withheld (C44). Both POSTs accept only a JSON boolean (`_cb_bool_body`; C59 —
  `bool("false")` used to turn escalation ON). The watchtower ops (`set_master`
  via `_wt_bool_args`) and `POST /api/memory/layer` (E121, the last C59 case)
  now refuse a non-boolean too. **`claude_think()` also refuses a credential
  (2026-11-09 audit E80):** after the escalation check it runs
  `_ml_looks_secret` (resolved by name) over the task AND the context; a hit
  is `reason: "secret_in_prompt"`, and a missing or raising check is
  `secret_check_unavailable` — fail closed, nothing sent; the usage log keeps
  only a redacted summary. For-You drops just the headline that trips the
  intent filter instead of refusing the whole Claude pass (E86).
- **Bridge gate rewrite (`_cb_gate`, 2026-08-18)** — the old keyword regexes refused
  5/18 realistic escalations ("memory leak", "password field", ".env approach", "wipe
  the cache", "should I create a component"). Now INTENT-based: secrets = action verb
  within ~50 chars of a secret noun (or noun … "to send/upload"); destructive =
  imperative rm -rf/wipe/format/delete-everything (question sentences exempt);
  approval-bypass always; codegen = imperative produce-verb + code artefact in a
  NON-question sentence, or filename + write/implement/refactor. Test set:
  25/25 benign allowed, 12/12 harmful + 10/10 codegen refused, injected-context
  caught (`scratchpad t_gate.py` pattern). The tool-lockout remains the real control.
  **The child is allow-listed (B25):** `_cb_argv()` adds `CB_HARDEN` (`--tools
  ""`, `--strict-mcp-config` + an empty `--mcp-config`, `--setting-sources ""`,
  `--permission-mode dontAsk`, `--disable-slash-commands`,
  `--no-session-persistence`; none changes auth); a CLI that rejects a flag
  fails the call (`_cb_flag_error`), never runs unhardened.
- **Notification master toggles (2026-09-01)** — watchtower.json `master:
  {briefings, news}` (normalized in `_wt_load`, flipped via `/api/watchtower`
  op `set_master`): `briefings` gates the 8am/midday/evening ticks, `news`
  gates `_breaking_pass`, EVERY watch rule (C54) and the watchtower's own feed
  egress (`_wt_news_on`, C48; hub widgets still fetch); each push re-reads the
  config at send time (`_wt_push_allowed`, C09); manual "send now" is ungated.
  New op `set_evening` (+`evening` and `master` now in the GET payload — evening previously had NO toggle
  anywhere). UI: two accent master switches + Evening wrap + breaking
  "can override quiet hours" controls in the Mind-view Watchtower card.
  Also: brief/midday/evening ticks now hold during quiet hours, and a wake
  after 6 PM marks the morning brief done WITHOUT sending (the widget still
  refreshes via briefing_loop; the evening wrap covers the day). Every dashboard
  push goes through `_wt_send_telegram`, so both masters off = no background
  push from the dashboard — **except GitHub's security notices**: the drift
  notice and the per-push notice go out whatever the masters say and in quiet
  hours (`GH_SECURITY_NOTICES_IGNORE_MASTERS = True` in aux_github.py, owner
  decision D8; 2026-11-09 audit E77; the NETWORK_FACTS GitHub rows say so).
  Hermes' own cron jobs and gateway replies are OUTSIDE these gates. The
  watchtower ops take only JSON booleans (`_wt_bool_args`, E146), and a held
  Space/Enter on a master or the escalation switch flips it once (ignores
  `KeyboardEvent.repeat`, no second flip while one is in flight; E28).
  **watchtower.json fails closed (C20):** corrupt/unreadable (not absent) keeps
  the last good config with both masters forced OFF + `config_error`; a save
  copies a corrupt file aside, never writes an unreadable one. **Agent web
  research is opt-in (C50):** `web_research.enabled` (explicit `true`, default
  off), run with `toolsets=["search"]` only. Feeds are https-only (C47); brief
  strings are `untrusted.line()`s and a rewrite that adds a URL falls back to
  the deterministic brief (B19/C01).
- **Watchtower/idle bug fixes (2026-09-01, second pass)** — (1)
  `_wt_send_telegram` calls `hermes send --json` (NOT `--quiet`, which prints
  nothing on failure), parses `{error|skipped|success}`, `SEND_TIMEOUT=60` (20s
  lost briefs under load). (2) `_intel_agent_pass` skips (one log line/hour)
  unless a lane is genuinely up — `bg_lane()` silently falls back to the PRIMARY
  when :8081 is down; background work never wakes the model. (3)
  `_slept_through(cur, sched, cutoff)` measures the "mark done without sending"
  cutoff from the SCHEDULED slot (+120 min grace), so late schedules still send.
  (4) `_chat_worker` wakes a model that is down WITHOUT the asleep marker;
  `idle_suspend_loop` self-heals the marker only after ≥60s down across ticks
  with no server process (`_mlx_proc_alive()`), no fresh start token and no job
  in flight (the ~4s bootout→start gap must never mark a loading model). (5)
  Schedule inputs write back the clamped value (`timeSaved()`): the ops clamp
  hours yet answer ok:true.
- **Live System widget** — `/api/sys` (2s cache) + a 5s client updater (`liveSystem`, Hub shown + window visible only, C90) patches the meters in place without a full hub re-render; shows a pulsing "live" dot. Background timers skip while `document.hidden` (C88), and `hub_prewarm_loop` runs only within 180 s of a `/api/hub` client (C53).
- **Markets widget** — Yahoo chart; sparkline is anchored at the previous close so its shape matches the daily %; meta shows "at close · <date>" / "live" from `marketState`+`regularMarketTime` (don't present holiday/last-session data as live).
- **Modular widget hub** — the Hub view is a widget registry, not fixed HTML.
  Backend: `WIDGETS` catalog in server.py (20 in its base dict; aux modules add five more — foryou, needsyou, claude_usage, framing, trends — so 25 at runtime; E149), each with a provider;
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
  timezones) rides in settings.json via `/api/settings`. **`_cached()` never
  lets a FAILED fetch (an `{"error"}` dict, all-error rows, or fn raising)
  replace the last good payload (C12)** — that is served for ≤3× ttl while
  retries back off (60 s, doubling per failure up to the ttl); a fetch that
  RAISED with nothing good to fall back on is backed off the same way and the
  stored exception is re-raised until the next retry (E96), so an outage does
  not hit the network on every hub poll; a provider must say `error` to be treated as failed, and
  `_widget_cache` pop/del/clear also drop the last-good copy. **layout.json is
  `{order, seen}` (B07):** `get_layout()` is a catalog-filtered VIEW, so never
  save it back at module load; `/api/layout` ops go through the locked
  `layout_update()`. **Links open only over http(s) (B18):** `wireLinks()` →
  `httpUrl()`; server `_http_url()` blanks/drops other schemes; Recent Activity
  rows reveal (`/api/files/reveal`), never open. The Swift half is an owner TODO.
- **Three views** (header segmented control, `setView()`): **Hub** (widgets),
  **Settings** (`tab-mind`/`view-mind`: skills/memory/insights and the
  settings shell), **Console**. A stored view name the app does not know falls
  back to the Hub (E91). Customize/Add-widget live in
  the header (`#hubctl`, Hub-only). Model pill doubles as a live agent-state
  indicator (thinking/writing/running-tool) driven by `setAgentState()` from the
  chat stream. The header has no greeting or date line (removed 2026-10-28,
  owner request; the date chip says the date); fullscreen state comes from the
  Swift `NSWindowDelegate` → `:root[data-fullscreen]`.
- **Agent Console** — GET `/api/console` (`console_activity()`): reads state.db
  `messages.tool_calls`/`tool_name` JOIN `sessions.source` → a live timeline of
  every tool the agent runs across ALL surfaces (dashboard/Telegram/CLI), polled
  every 3s. This is the research-validated "watch it work" edge.
- **MLX memory ceiling (stability)** — the KV/prompt cache can balloon under
  concurrent load and take the Mac down. Two layers in server.py: `mlx_admission()`
  (15s-cached footprint) makes /api/chat + _generate_briefing REFUSE new model work
  at/above `MLX_SOFT_GB` (50 here) — and briefing_loop gates itself via
  `_briefing_skip_reason()` (aux_watchtower rebinds `_generate_briefing`): widget
  shown, nothing generating, no chat job, 5 min since the last user turn,
  admitted, model already up (C67); `memory_guard_loop` polls every 30s and above
  `MLX_HARD_GB` (56 here) does a `_mlx_restart()` (bootout→bootstrap — NOT kickstart -k)
  to free the balloon. User override: `/api/model/mem_override {allow}` touches
  `~/.hermes/dashboard/mem-override`; `/api/model/mem_free` = manual cache-clear
  restart (since 1.0.1 it returns `{started}` and `GET /api/model/mem_free/status`
  carries the real `_mlx_restart()` outcome — the menu polls that instead of a blind
  4 s timer; `set_model_thinking()` likewise reports `ok:false` when the restart failed). Surfaced in models_payload().mem + the model-menu memory row. When
  running many model-drilling agents concurrently, expect the ceiling to engage —
  that's it working, not a bug.
  **RAM-AWARE DEFAULTS (1.0.3)** — both numbers used to be hardcoded for this 64GB
  Mac, which on a 16GB Air means a soft ceiling of 50 that never engages while the
  machine swaps to death. `_machine_ram_gb()` reads `hw.memsize` once (GiB, i.e. the
  capacity Apple prints on the box; falls back to 64 if sysctl fails) and the PURE,
  unit-tested (`t_c78_memory_ceilings.py`) `_mem_ceilings(ram_gb, env)` derives the pair:
  **RAM ≥ 64 GB → (50, 56)** — byte-identical to the old constants, and FLAT rather
  than a percentage because the ceiling bounds the KV-cache balloon, whose useful
  size is set by the workload (~2GB per ~20k-token sequence), not by installed RAM;
  **below 64 GB → soft = round(0.72 × RAM), hard = round(0.82 × RAM)**, with
  **hard ≥ soft + 2** and a floor of **(min(8, RAM−2), min(10, RAM))** (round 3,
  advisor D9: below 10 GB the old flat (8, 10) floor put soft at or above ALL of RAM,
  so admission control could never engage on the Mac that needs it most; from 10 GB
  up it is unchanged). So 8→(6,8), 16→(12,14), 24→(17,20),
  32→(23,26), 36→(26,30), 48→(35,39). `MLX_SOFT_GB`/`MLX_HARD_GB` still win and are
  taken verbatim (a soft-only override drags hard to soft+2; unparseable values are
  ignored rather than raising at import). Same idea in the shell: `mlx-server.sh`
  and `mlx-server-bg.sh` read `hw.memsize` and tier `APC_EXACT_CACHE_ENTRIES` +
  `--prompt-cache-size/-bytes` (primary 6/8GB ≥64, 4/5GB ≥48, 3/3.5GB ≥36, else
  2/2GB; the bg lane has its own lower table anchored on today's 4/3GB because it
  shares the Mac with the ~19GB primary). `models_payload().mem.machine_gb` carries
  the number to the UI, and each roster row gains `fit: "ok"|"tight"|"no"`
  (`_model_fit`: no if `ram` > 0.85 × machine, tight if > 0.60 ×) which the model
  menu renders as "needs ~19 GB · this Mac has 64 GB" — muted, `--warn` when tight,
  `--bad` plus an inert row (`onModelClick` refuses it, not just CSS) when it cannot
  fit, so a user never starts a 17GB download for a model their Mac cannot load.
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
    **Rebuilt 2026-09-11, owner-approved** (Full Disk Access was already denied, so
    the rebuild cost nothing): `HermesWebView` drag regions + the two dictation
    usage strings. FDA must be re-granted before the Message Center works again.
    The file is FROZEN again from here.
- **AUX MODULE GOTCHA — never `from datetime import datetime` in an aux_*.py.**
  aux modules exec into shared server.py globals; `from datetime import datetime`
  rebinds the global name `datetime` to the CLASS, so any other code that later
  calls `datetime.datetime(...)` / `datetime.timedelta(...)` silently breaks
  (returns None / raises). Import under a private alias instead:
  `import datetime as _mymod_datetime`. (aux_config/aux_recorder currently do the
  bare import — tolerate it, but new modules must alias.)
- **Third-party text enters a prompt only through `dashboard/untrusted.py`
  (B19/C01/C39/C45/C46)** — stdlib: `line()` (one inert line, newlines/controls
  flattened, ChatML tokens and `<<<`/`>>>` defanged), `body()`, `block(label,
  text)` (a labelled `<<<BEGIN …>>>` data fence) and `NOTICE` (above any fence).
  `neutralise()` splits EVERY run of 3+ `<` or `>` into pairs ("<<<<<" → "<< <<
  <"), last, so no `<<<`/`>>>` survives any input (2026-11-09 audit E75); a
  module's own fallback copy must do the same. Clipboard transforms put the
  clipboard inside the fence too, with the NOTICE in the system prompt
  (aux_clip.py builds its fence from `block()`'s own lines + `neutralise()` so
  indentation survives — E120): text carrying `<<<`, `>>>` or `<|…|>` reaches
  the model with spaces inserted. aux modules reach it by name with a fallback.
  Defence in depth; the approval gate and reduced toolsets are the controls.
- **NEVER write `settings.json` or a `chats/*.json` directly (2026-09-10, audit
  A01/A03).** Both files have many writers, and the old habit — read the whole
  object, do some work, write the whole object back with only the final
  `write_json` under `_state_lock` — is a lost update, not a transaction.
  `write_json`'s tmp+`os.replace` is atomic and that is NOT the same thing.
  * settings.json: `settings_update(mutate_fn)` (read + mutate + write under
    `_state_lock`; `mutate_fn` mutates in place or returns a replacement) or
    `settings_set(**fields)` for a flat merge. Touch only the keys you own, and
    do NOTHING slow inside — no network, no subprocess, no model call; every
    other settings reader/writer in the process is waiting on that lock. Do the
    slow part first and pass in the result (`weather()` geocodes, THEN calls
    `settings_set` with its three keys — it used to write a pre-network
    snapshot back and that is how a background widget refresh could turn the
    Claude bridge back on after an explicit opt-out).
  * conversations: `save_chat_update(session, mutate_fn)` for any
    read-modify-write; append inside the mutate fn
    (`chat["messages"].append(...)`), never assign a list built outside it.
    Every conversation carries a `rev` that each locked save bumps, and the raw
    `save_chat()` now raises if your snapshot's rev is BEHIND the file on disk
    (the append-only length guard cannot see that case — two workers each
    appending one reply to the same N-message copy both save N+1). Messages get
    a stable `id` on save; `truncate=True` still skips both guards and nothing
    passes it. `aux_index.py` wraps BOTH `save_chat` and `save_chat_update` for
    `index_touch`, so a new writer keeps search fresh for free.
  * a chat job's `done` is set ONLY by `_finish_chat_job`, and only AFTER the
    reply is persisted; `persisted: true/false` rides with it and the chat view
    toasts on false. `hermes_rpc.run_turn` sets `state="done"`/`reply`/`ok` and
    must not set `done` itself.
  * `hermes_rpc.run_turn` also stamps `submit_sent`/`submitted`/`tool_events` on
    the job. There is NO one-shot fallback (C36/C118): before submission the job
    ends "nothing was run"; after it, never re-run — finish with the "check the
    conversation before repeating an action" reply.
  * **Corrupt stores are quarantined, never overwritten (C27):**
    `settings_update()` reads via `read_json_for_update()` — an unparsable or
    wrong-typed file is moved aside as `<file>.corrupt-<ts>` and the write
    starts from the default; an unreadable one raises and nothing is written.
    Use it for any new read-modify-write. `write_json()` also moves an
    unparsable target aside, fsyncs before the rename (C107) and never leaves a
    file wider than 0600 (C110).
- **Config-as-Code export/import (`dashboard/aux_config.py`) validates
  `timezones` as `[label, tz]` PAIRS (2026-09-10, audit A10)** —
  `_cfg_clean_settings()` used to accept only bare strings in `timezones`, but
  the world-clock widgets (`expand_worldclock`/`w_worldclock` in server.py)
  have always stored `["label", "Area/City"]` pairs, so a real configuration
  like `[["Paris", "Europe/Paris"]]` silently became `[]` on export and an
  import then restored the default clocks over the owner's chosen ones.
  `_cfg_valid_tz()` now validates the zone against `zoneinfo.available_
  timezones()` (cached; this repo requires Python 3.12+) and falls back to a
  loose Area/City shape only if zoneinfo is ever unavailable; a bare string is
  still accepted for backward compatibility. Invalid pairs (bad zone, empty
  label, wrong shape) are dropped, never rewritten. **The export lives at
  `~/.hermes/dashboard/state-snapshot.json` (C64)**, never in the checkout (the
  old `docs/state-snapshot.json` is only read while no new export exists). The
  models section carries the full roster schema (never `trust_remote_code`) and
  import MERGES into models.json (B27).
- **Store contracts (2026-10-21)** — Scratchpad: notes.json `{text, rev}`;
  `notes_post()` gives 400 non-string text, 413 above `NOTES_MAX_CHARS` (never
  truncates), 409 `{conflict,text,rev}` on a stale `base_rev`; hub-card editor
  state lives in `window.__notesEd` (C19/B05). Recorder: undos serialise on
  `_rec_undo_lock`, `_rec_lock` never spans a `_ckpt()` subprocess (C08).
  **Undo removes a file only when the agent PROVABLY created it (2026-11-09
  audit E01)** — the checkpoint store never snapshots `.env*`, `*.log`,
  `build/`/`dist/`/`venv/`, anything gitignored or files over 10 MB, and
  git's "pathspec did not match" used to send the owner's `.env` to the undo
  trash. `_rec_created_proof()` (PURE) demands ALL of: a `write_file` (a patch
  edits an existing file), a non-gateway source (`_REC_GATEWAY_SOURCES`:
  Telegram & co. are never snapshotted), a regular file left and present, the
  driver's `probe` op on the store answering "not in the snapshot", no case /
  Unicode-form twin there, git saying the store would NOT have skipped it,
  unchanged .gitignore files, under the size cap (`_ckpt_size_cap()`, never
  trusted upward), mode exactly `_REC_NEW_FILE_MODE` 0600 (Hermes' write_file
  creates 0600 and keeps an existing file's mode) and no recorded touch before
  the write. Anything else is refused with `undo_why` (the row says why before
  the click; an undoable one carries `undo_effect` trash|restore), and the
  file is left where it is. Undo of a created-file undo restores from the
  trash and never overwrites (`undo_by` + the trash path in `after_state`,
  trusted only on `origin = 'dashboard'` rows); restores use literal pathspecs
  (`GIT_LITERAL_PATHSPECS=1`), so a file named `*.txt` restores only itself.
  It depends on upstream `_git_env`, `_store_path`, `_normalize_path`,
  `CHECKPOINT_BASE` and the 0600 creation mode — **re-verify on every Hermes
  upgrade** (missing → Undo refuses; a changed mode → every created file
  refuses, failing safe). The one gap left: a file that was itself 0600,
  absent from the matched snapshot (over 10 MB before, or a stale snapshot)
  and never touched by the agent before can still be classed created (closing
  it needs a Hermes write-time hook). The detail pane survives the 3 s poll
  and its diff is cached (`_REC_DIFF_CACHE`, ≤ 32, 120 s, keyed on the file's
  size+mtime, single-file rows only — E33); filter chips are buttons (E93).
  The memory index (memory-meta.json) and config exports/pre-restore backups
  follow the C27/C110 rules too (read_json_for_update; created 0600, backups
  folder 0700 — E114, E116).
  memlayer `facts.archived` 1 = owner, 2 = stale — test `= 0`/`!= 0` (C105). Any
  Trust-panel save over an UNTRUSTED policy shows the re-trust review first
  (every save re-signs the file, C126).
- **Adding a hub widget from an aux module**: mutate `WIDGETS[id]={title,icon,
  size,cat,provider}` + `EXPANDERS[id]=fn` at module load, call
  `layout_inject(id, front=False)` (adds the id only if layout.json's `seen` has
  never held it, so a Remove sticks across restarts), and in aux JS set
  `RENDER[id]` (body), `EXPAND_RENDER[id]` (pop-out), and `WICONS[id]` (icon). See aux_claude_usage.py/.js.
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
- **Chat lives only on the Agent tab (2026-11-12, owner decision)** — the Hub and
  Settings have no chat column, no floating "show chat" button and no
  full-screen chat mode: `setChatMode`, `deck[data-chat]`, `#chat-max`,
  `#chat-hide`, `#chat-restore` and the Settings collapse
  (`hermes_chat_settings`) are gone, and the stored `hermes_chat` /
  `hermes_chat_settings` keys are deleted on load (a saved "full" used to make
  the main window open with the chat covering the widgets — Quick Ask's
  "Continue in main" and the Expand button set it). aux_agent.js still
  re-parents the live `.chatcol` into `#agent-stream` on entering Agent and
  puts it back (hidden) on leaving. **`openChat()`** is the one way anything
  outside the Agent tab reaches the chat: it switches to Agent, and every
  former caller goes through it — `askAbout` (pop-out ask box, selection and
  right-click ask, For-You, clipboard), `hermesOpenConversation` (Needs-you's
  Open, search results), the You-Model interview, the end of first-run setup,
  Quick Ask's "Continue in main" and `openApproval`. **The view is never
  switched on the owner's behalf:** an approval card drawn while they are
  elsewhere raises a 9 s toast that takes a click ("An action is waiting for
  your approval. Click to open Agent." — `toastGo`, inert the moment its line
  is replaced or fades, aux_github.js's contract); the header pill still shows
  the turn's state and the Needs-you approval row leads there too.
  `renderChatSide()`/`#chat-side` stay: the Agent view uses them.
  `/api/sessions` lists the 30 newest + `total`; `?limit=all` lifts the cap
  (C32). Test: `t_hub_no_chat.js`.
- **UI council rules (2026-11-12, three rounds of blind judging; notes at the end of
  `docs/audits/2026-11-12-visual-audit.md`)** — shared type rules every surface now
  follows: read text is 12px or larger, tracked upper-case labels 11px/600, both in
  `var(--muted)`; `var(--faint)` only for decoration and disabled controls (3:1
  minimum); text a person reads reaches 4.5:1 in both themes; one sans, the mono
  only for figures. (The Settings chat collapse this round added was superseded the same
  day: the chat column is gone from Hub and Settings, see "Chat lives only on
  the Agent tab".)
  **Model menu rows** are one title line (name, at most one verification state,
  one right-aligned action pill or quiet state chip), an optional badge line
  (Uncensored badge + state together), a spec line that wraps only between facts,
  and the warnings that must stay visible; the Drill button, drill record and
  licence live in the Details sibling (`aux_promotion.js` appends them there);
  `aria-current` is the row that looks selected and takes focus when the menu
  opens from the keyboard (`t_climb_model_menu_rows.js`). **Trust tiers** are a
  `role=radiogroup` per rule with one tab stop; arrows/Home/End move focus only
  (every pick saves), skip an `aria-disabled` locked Auto, and Space/Enter pick
  through the click path, ignoring `repeat` (`t_climb_trust_keyboard.js`).
  **Settings › Overview "At a glance"** is built in aux_settings_shell.js from
  GETs that never run anything (`/api/models`, `/api/needsyou?mark=0`,
  `/api/messages`, `/api/google/status`, `/api/github/status?scan=0`,
  `/api/version`) plus the doctor result the page already holds — `/api/doctor`
  is never called by it — once per showing, no timer; a failed read drops only
  its row (`t_climb_settings_glance.js`). **Needs-you** folds off or unconnected
  sources behind an "N sources off" toggle (blind sources stay in plain sight)
  and hides "Never 0" and the unmeasured trust line while empty. **Agent state
  words** are Awake/"Watching", "Asleep" (idle-suspended) and "Paused" on every
  surface, from state the page already holds. Small Hub tiles carry a foot line
  of data they already have; quick-action chips are one row plus "+N" from 560px.
- **Scroll-end cue (`scrollCue(el, withEl)` in index.html, visual audit V04)** —
  a scroller that can end mid-row gets `[data-more]` while content is hidden
  below its edge (scroll, ResizeObserver and MutationObserver driven, one rAF
  read, no timer) and CSS draws a short `pointer-events:none` fade there. The
  model menu and the Widget Center carry a sticky zero-height `<i class="scue">`
  kept as the LAST child (put back after every `loadModels()` rebuild); the
  pop-out's fade rides on its `.askrow`, which is `position:sticky;bottom:0`
  so the ask box is always on screen. It is `data-more`, not a class, because
  aux_glass's observer watches `class`; never `mask-image` on a glass surface.
  The model and Preferences menus are 98% opaque: `<header>`'s own
  backdrop-filter makes it the backdrop root, so a menu inside it cannot blur
  the Hub and only the alpha hides it (V06; `.modelmenu` and aux_prefs.js's
  `.prefsmenu` stay identical). **A label on the accent fill is
  `color:var(--iris-ink,#fff)`, never `#fff`** — `--iris` is a LIGHT lavender
  in the dark theme, so white on it reads 2.35:1 — and a TINTED accent fill
  (`color-mix(... var(--iris) 24% ...)`) takes `var(--ink)` instead (V41/V42;
  `t_v41_on_accent_ink.py` scans every dashboard script for the pairing).
- **Rich pop-outs (every widget; the 20 base ones here)** — clicking any widget opens `/api/expand?id=`
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
  `hermes computer-use doctor` for the health matrix. Upstream asks for approval
  ONLY when a CLI callback is registered, so in serve, Telegram and `-z` it is
  ungated except through the approval-gate plugin below (C111).
- **Hermes plugins (`~/.hermes/plugins/`)** — **approval-gate (C111/C116)**:
  `hermes-plugins/approval-gate`, a `pre_tool_call` hook, linked + enabled by
  install.sh/update.sh via `plugin_enable.py` (like tool-budget). It sends
  mutating `computer_use` actions (all but capture/wait/list_apps and a
  non-raising focus_app; unknown ones gated) and `write_file`/`patch` to shell
  rc files, `~/Library/LaunchAgents`, ~/.hermes (except dashboard/inbox),
  `~/.claude` and the install dir through `tools/approval.py`'s own gateway
  queue: serve's `approval.request` card (Trust keys `computer_use:<action>` /
  one per path class) or Telegram's prompt. With NO surface (`-z`, cron, a
  subagent's canned auto-answer) the call is BLOCKED; YOLO and `approvals.mode`
  are ignored; its own errors fail closed. **Path forms (2026-11-09 audit
  E10–E13):** a target is judged in every form it can take — a relative path
  resolved by Hermes' own task resolver (this process's cwd only when that is
  unavailable), an absolute one as written, each with symlinks resolved,
  without a `/System/Volumes/Data` firmlink prefix, and as the kernel names it
  (`F_GETPATH` on the deepest existing ancestor); a path through `/.vol`,
  `/.nofollow`, `/.resolve` or `/.file` is refused outright, reads and searches
  too. A path is judged by the layer that opens it: write_file's `path` by the
  task resolver, while a V4A header and read_file/search_files `path` reach
  Hermes' shell layer RAW, so they are judged where IT puts them (the
  terminal's `$HOME` for `~/`, the terminal env's live cwd for a relative path)
  by asking the live env, failing closed when it cannot; a `~name` that is not
  a user (`~0`, `~-`) is refused. Patch headers are read the way upstream
  `tools/patch_parser.py` reads them (newlines only, a lone CR kept; its
  parser called when importable); a header-shaped line the gate cannot read,
  or one with a control or hidden character (a CRLF end aside), asks under
  its own key **`v4a-unread`** (Trust class unknown, so it always asks). A
  classified V4A target is NEVER dropped on the strength of Hermes' own
  refusal (it checks the raw header against a different cwd — E12); only a
  write_file `path` Hermes itself refuses is left to Hermes. The rc class now
  covers files rc files commonly source (`~/.cargo/env`, `~/.rokit/env`,
  `~/.curlrc`, `~/.wgetrc`, `~/.netrc`, `~/.p10k.zsh`, the alias files,
  `~/.config/zsh`, `~/.oh-my-zsh`, `~/.zprezto`, `~/.orbstack/shell`,
  `~/.opam/opam-init` — E68). The card shows long text as head AND tail with
  the omitted count, says `replace_all`, marks bidi/default-ignorable
  characters ⟦U+XXXX⟧ and stays under 3,000 characters (`_CARD_MAX`; Telegram
  drops past 3,800 — E14); only "once", "session", "always" and "approve"
  approve, any other answer refuses (`_APPROVING`, E67). A payload in the
  MIDDLE of a very long text stays behind the omitted count (inherent). It
  leans on private Hermes internals (`_await_gateway_decision`,
  `_active_environments`, `_resolve_container_task_id`,
  `ShellFileOperations._expand_path`, `env.cwd`, `_file_ops_cache`,
  `_last_known_cwd`, …) — re-verify on upgrade. Hermes' OWN denials outside
  the gate's classes (`~/.aws`, `~/.gnupg`, reads of `~/.hermes/auth.json`)
  still miss the firmlink spelling and case variants — an upstream gap the
  gate does not cover. Opt-out: a key in
  `command_allowlist` (applies unattended too) or disable the plugin. A path
  denylist, not a sandbox (terminal/execute_code, memory/skill tools are not
  covered — round 3 adds only GitHub tripwires on terminal/execute_code/process
  and a read block on the GitHub key folder; see the GitHub bullet). **loop-breaker** (a plain COPY of
  `skills-snapshot/_plugins/loop-breaker`, version 1.1.0) blocks only
  idempotent READ tools on the 3rd identical call per (session, turn) **with
  the same successful answer** and nothing state-changing in between: its
  `post_tool_call` hook hashes each result, so a changed answer restarts the
  count and a failure never counts toward it (three identical failures block
  the next retry) (C134/C135, E66;
  without a post hook it falls back to the old count). Re-copy it to
  `~/.hermes/plugins/loop-breaker` after a change. **tool-budget** wraps
  reads of its spill as `<untrusted_tool_result>` (C41). **update.sh restarts
  the Telegram gateway** (`ai.hermes.gateway`) whenever launchd has it loaded,
  so it loads the updated plugins (E145); after a hand-copied plugin change,
  kickstart it yourself.
- **Services** — `install-services.sh` installs launchd agents. Dashboard +
  serve are always-on (RunAtLoad + KeepAlive); the MODEL services
  (`com.hermes.mlx-server`, `com.hermes.mlx-bg`) are ON-DEMAND since
  2026-09-01 (battery): RunAtLoad=false, KeepAlive=false, plus a gate at the
  top of mlx-server.sh / mlx-server-bg.sh. For the background lane
  (mlx-server-bg.sh, unchanged) the gate is still "with
  `~/.hermes/dashboard/model-autostart-off` present, exit unless a FRESH
  (<180s) `model-start-ok-bg` exists". **The primary's gate (mlx-server.sh)
  was tightened 2026-09-10, audit A09**: nothing ever created that marker, so
  a fresh install (no marker, no state at all) fell through to always-on —
  `app/main.swift`'s `ensureServices()` kickstart at every launch could load
  the ~18GB model with no chat behind it. mlx-server.sh's on-demand check is
  now unconditional (marker present or not): it always requires a FRESH
  `model-start-ok`, unless `HERMES_MODEL_ALWAYS_ON=1` is set — which
  `install-services.sh` bakes into `com.hermes.mlx-server`'s own
  `EnvironmentVariables` when the owner passes that env at install time (once
  opted in, `~/.hermes/dashboard/model-always-on` makes the choice sticky
  across a later reinstall/update that does not re-pass the env). Both
  `install.sh` and `install-services.sh` create `model-autostart-off` on a
  genuinely fresh install (no `settings.json` yet, i.e. this Mac has never
  been set up) purely for `doctor.py` and other tooling that reports on its
  presence — the primary's gate no longer needs it to enforce on-demand, but
  it is still what mlx-server-bg.sh keys off. Only server.py's `_mlx_start()`
  mints the primary token (used by agent_wake / resume / switch_model /
  `_mlx_restart`), so the app's blind `ensureServices()` kickstart at launch
  and login autostart are inert; a crash stays down until the next
  chat/Telegram wake. Nothing mints the bg token — start that lane by
  touching it manually. `main()` marks a down, un-paused model
  idle-suspended at dashboard start so chat/Telegram/"Wake now" wake it
  transparently. A pause now genuinely survives reboots (it didn't before —
  RunAtLoad used to resurrect the model at login). `--uninstall` removes.
  Telegram gateway separate (`hermes gateway`). Logs in `~/.hermes/logs/`.
  install-services.sh bootstraps dashboard + serve before the model plists.
  A LOADED model lane whose plist did not change is left running only when
  `HERMES_MODEL_CODE_UNCHANGED=1` (update.sh sets it when its fingerprint of
  mlx-server.sh / mlx-server-bg.sh / mlx-vlm-launch.py is unchanged across the
  swap); otherwise it is reloaded, which stops a running lane, and the summary
  says which happened to each lane (E129).
- **Doctor (`dashboard/doctor.py`, 23 checks)** — `danger_patterns` compares
  Hermes' installed `DANGEROUS_PATTERNS` with `permissions._DANGER_PATTERNS` by
  reading both files as source (nothing imported; WARN on drift, a quiet pass
  when Hermes' source is absent). `hermes_drift` (F03) reads
  ~/.hermes/config.yaml with a stdlib YAML-subset reader (PyYAML-equivalent for
  what Hermes writes; anything it cannot read is reported by line, never
  guessed) and WARNs on unpinned `approvals.mode`, cloud TTS, `computer_use`
  without approval-gate enabled and on disk, the `local-anything` key, MCP
  servers/sampling and non-loopback providers. Read-only. Config WARNs on
  `*.corrupt-*` copies; Services WARNs while serve would mount its web UI; the
  model/serve/gateway.error logs rotate like dashboard.log (C14).
  `t_k12_docs_drift.py` pins README/RUNBOOK's counts to the code. Its
  loopback probes use a `ProxyHandler({})` opener (E20). A cached "vX is
  available" is re-judged against VERSION, so it never offers what is already
  installed (E127). Dictation WARNs when the helper bundle is gone although it
  ran here before (a heartbeat exists) — `app/build-app.sh` no longer deletes
  it (E126). **The YAML-subset reader is shared:** `hermes-plugins/plugin_enable.py`
  carries a byte-identical COPY of doctor.py's reader (from `_HC_KEY_RE` to the
  end of `_hcfg_parse`), which the config.yaml editors use to verify every
  edit — change both or neither (`t_e16_config_editors.py` checks the copy).
  **The three config.yaml line editors** (`plugin_enable.py`, and
  `aux_promptbudget.py` / `aux_context.py`, which load it by path) call
  `precheck()` first and REFUSE a form they cannot edit safely — a tab in the
  indentation, a second YAML document, the section key quoted, duplicated or
  in flow style, anchors/aliases/tags or a merge key `<<` inside it, a quoted
  or duplicate child key — writing NOTHING and naming the line
  (`ConfigEditError`; plugin_enable.py exits 2 and install/update print
  "could not enable <plugin>: …" with that line; the dashboard
  POSTs answer 409 `{config_error:true}`). Every edit is re-read with the
  copied reader and verified (`verify_edit`) before the backup and the atomic
  replace; a header-line comment, an indentless list and CRLF line ends are
  handled in place (E16/E17). Older behaviour still standing: a symlinked
  config.yaml is replaced by a regular file, `hermes config set` does not take
  `config.yaml.lock`, and plugin_enable's backups are never pruned.
- **Menu-bar Quick Ask (rebuilt 2026-09-04)** — `dashboard/aux_quickask.js` IS the
  popover: the Swift skeleton is frozen (380px wide; height only via bridge
  `{action:"resize",h}` clamped 320..620; bridges `close`/`openMain`/`openApproval`,
  `hermesClip` read → `__clipDeliver`, `hermesClipWrite`; Swift calls `__qaFocus()` on
  show). Status strip = control surface (tap wakes/pauses via /api/agent/*, `Claude
  on|off` pill on /api/claude/escalate, update pill from /api/update/check, `⋯` menu:
  open main / clear session). Input is never disabled by model state — POST /api/chat
  wakes an asleep model, the old health gate locked the popover most of the time.
  `/` filters the action row (clip actions from /api/clip/actions → /api/clip/transform;
  Plan my day; Ask Claude → /api/claude/think quick; Continue in main). Thread renders
  `d.status` tool lines, inline Approve/Deny via /api/chat/approve, and `d.deep` as a
  Claude card (previously dropped). Dual-role contract: inside index.html the file only
  defines `window.hermesQuickAskResume`. Verify headless with the scratch harness
  pattern: serve the skeleton on the dashboard origin via a Playwright route, stub
  `window.webkit.messageHandlers`, and MOCK /api/chat + /api/chat/poll + /api/clip/
  transform so tests never wake the 19GB model.
- **Dictation cleanup is conservative (B16/C124)** — the default `rules` mode
  must never change what was said: filler phrases go only as comma-set asides, a
  self-correction applies only with a close `_dct_splice` anchor (never a lone
  pronoun), doubled words collapse only for `_DCT_STUTTER_WORDS`, never in
  verbatim. **A correction is left exactly as spoken when the kept part holds
  ANY negation** ("Don't send it, no wait, send it" — `_dct_negation_dropped`,
  2026-11-09 audit E27). Single-word fillers match case-sensitively (lower or
  sentence case, never ALL CAPS — "the ER", "AH-64"; "Er" only lower case), a
  hyphen is part of the word ("uh-huh" stays), "mhm" is an answer, not a
  filler, and **verbatim removes no fillers at all** ("echo um" is data — E107). The helper's pure Swift pieces (`PushToTalk`, UTF-16 `AXSplice`) are
  unit-tested by compiling them out of app/dictation/main.swift; the helper
  itself is only typechecked.
- **Keyboard/glass invariants (C02–C05, C92–C100)** — keep `loadModels()`'s
  `mmFocusKey()`/`mmRefocus()` (menu focus survives the 30 s rebuild); a new
  overlay joins index.html's Esc/focus block via an `OVL` entry; Enter/Space
  handlers on div/span controls ignore `KeyboardEvent.repeat`; reduce motion
  wraps `Motion.animate` — never capture it into a local at load; aux_glass.js's
  `--gsy` must never be sticky/inherited (the light layer props the scroll range
  open); no hover lift on `.w`/`.mcard`.
- **Motion system (round 3, `docs/plans/motion-system-2026-10.md`)** — **Vendored,
  never a CDN:** `dashboard/vendor/` holds Motion 14.6.0
  (`motion/motion-14.6.0.min.js`, global `Motion`, MIT), GSAP 3.15.0 core + Flip +
  CustomEase (`gsap/{gsap,Flip,CustomEase}-3.15.0.min.js`) and anime.js 4.5.0
  (`anime/anime-4.5.0.umd.min.js`, MIT), byte for byte from the npm builds and pinned
  in `vendor/MANIFEST.json` (path, file, version, bytes, sha256, npm integrity/resolved,
  licence + its sha256). **GSAP is NOT MIT**: it is under GSAP's Standard "No Charge"
  License (Webflow), effective 2025-04-30 (re-read on the live page 2026-10-26,
  unchanged); `gsap/LICENSE.md` is our NOTICE, every `@license` banner stays
  byte-intact (the sha256 test enforces it), and nothing here may become a no-code
  visual animation builder on GSAP (the licence's prohibited use). `tools/vendor-anim.sh`
  (bash + `python3 -I`, never npm, never the network) is the only writer; `--check`
  re-verifies the committed tree. A version bump edits its PINS, runs it, then edits
  server.py `VENDOR_JS`/`VENDOR_ALIAS` and aux_anim.js `LIBS`/`MOTION_V` together —
  `t_r3_vendor_manifest.py` and `t_anim_runtime.js` fail until all agree
  (`dashboard/vendor/README.md`). **Serving:** `/vendor/*` comes only from the
  exact-key dict `VENDOR_JS` — no path joining, so `..`, `%2e%2e`, `.map`, LICENSE.md
  and MANIFEST.json are 404 and the prefix never falls through to an API route — as
  `application/javascript; charset=utf-8`, `Cache-Control: public, max-age=31536000,
  immutable` and a content-hash ETag (304 on If-None-Match). `/motion.min.js` is
  `VENDOR_ALIAS`: the same Motion 14 file with `no-cache` + ETag, kept because the
  FROZEN Quick Ask skeleton in main.swift still requests it (index.html loads the
  versioned URL); the old `dashboard/motion.min.js` (11.11.13, provenance never
  verified) is gone. The server has no HEAD handler: check headers with `curl -s -o
  /dev/null -D - <url>`, never `curl -I` (501). Motion 14 vs 11: controls have
  `.finished`, x/scale run as one WAAPI transform, and transforms on SVG children now
  render with an inline `transform-box: fill-box`, so a px `transform-origin` pivots
  wrongly (aux_agent's `.sig-ring` is `transform-box:fill-box;transform-origin:50%
  50%`).
  **Runtime `dashboard/aux_anim.js` → `window.HermesAnim` (frozen, `v:1`)**, the only
  way new code animates: an ES5 IIFE loaded right after the vendored Motion tag and
  BEFORE the inline shell; no index.html globals, no interval timer, no HTML-string
  writes. Roles: Motion runs one element or a flat list (`enter`/`exit`/`stagger`/
  `spring`, 13 presets), GSAP runs layout FLIPs and sequences (`flip`/`sequence`/
  `hold`), anime runs values, SVG and words (`count`/`draw`/`words`); CSS keeps every
  infinite loop, hover/press and the popover entrances. GSAP and anime are injected
  from `LIBS` on first need, on the first pointerdown on `#edit-toggle` / `header
  .seg` / `#model-pill`, or by ONE prefetch 2.5 s after load — NEVER under reduced
  motion, one attempt per page life, a failure is permanent. **Rules:** every call is
  synchronous, never throws and never waits. When `!live()` — reduced (`:root
  [data-reduce]`, the in-app switch, OR the OS query, read on every call),
  `document.hidden`, or inside `quiet()` — or the library is missing, it takes the
  INSTANT path: the end state at once (an exit's `done` runs before the call returns,
  `flip` runs only `mutate`, `sequence` runs `opts.instant(T)` then `opts.done`).
  Going hidden settles everything (holds fire) and nothing schedules a timer or rAF
  while hidden. `hold()` is reading time, not motion, so it still waits under reduced
  motion. ONLY transform and opacity animate: `spring()` and GSAP vars (keyframes
  frame by frame) drop any other key with a warning. flip/sequence/count/draw/words
  refuse any target inside `<header>` (aux_window re-measures the drag band on every
  header mutation; the model menu closes with a Motion WAAPI exit, a few style
  writes). Nothing moves on a periodic refresh — pollers repaint inside
  `HA().quiet(...)`; `once(key)` and `{key}` on enter/stagger animate once per page
  life (keys are spent even while quiet or reduced); `count()` to an unchanged value
  only writes the text. Caps: flip 24 targets, stagger 12, count 8, draw 2. Every live
  animation fires one bubbling `hermes:animstart`/`hermes:animend` (`detail{ms,kind}`);
  aux_glass re-measures the pointer light on them (`detail.ms` stretches its window,
  capped at 1.5 s). **Ordering rules (each found against the real libraries):** start
  follow-up motion from a sequence's `done()`, never `tl.call()` inside the builder
  (GSAP's context owns and reverts whatever its callbacks create; a natural-end
  `done()` runs from a microtask AFTER the context revert); an exit's clean-up (the
  prior inline opacity/transform restored, `Motion.releaseElement`) runs BEFORE its
  `done()`, so an enter started there, or from the handle's `finished`, begins from
  the page's own state. `enter` pre-paints its from-state and restores the PRIOR
  inline opacity/transform at the end — never pre-hide with inline `opacity:0`;
  `spring`'s end value persists. `flip()` takes only the OUTERMOST `[data-flip-id]`
  under its scope (`outer()`) and `hubFlip()` also passes `targets:'#wgrid>.w'` —
  Needs-you's own flip ids inside a card would move twice and eat the 24 cap.
  `draw()` snapshots and restores `pathLength`/`stroke-dasharray`/
  `stroke-dashoffset`/`draw` and the inline `stroke-linecap` (anime's revert leaves a
  blank path and an inline butt cap; index.html's `drawIn()` restores the cap too).
  **The GSAP individual-transform trap:** GSAP bakes a running CSS animation on
  `rotate`/`translate`/`scale` into an inline `transform` and leaves
  `translate/rotate/scale: none` inline, which also kills the `:active` scale press —
  never Flip or sequence an element whose CSS animation drives an individual
  transform property (hence the Customize rules below).
  **index.html:** `animate()` routes to `HermesAnim.legacy` (turns the WAAPI `easing`
  Motion ignores into `ease`), `revealStagger()` to `stagger`, and `HA()` returns the
  runtime or `ANIM_NULL` (lands everything at once; its `hold` is one setTimeout).
  `renderHub(pre, how, flipOpts)` = fetch + synchronous `buildFrom(d)`: the first
  default render of the page life is the boot entrance (`once('hub:boot')`), every
  later one and `how='quiet'` are quiet (refreshHub included), and `how='flip'`
  (remove, Widget Center add with `{enter:'pop'}`, Alt+Arrow, `layoutOp`) flips with
  the rebuild itself quiet, so widgets' own renderers replay nothing. Customize toggles
  `.editing` on the SAME cards (`setHubEditing`/`hubCardMode`, no rebuild); the jiggle
  is CSS `rotate`, so `hubFlip()` adds `.flipping` (`.wgrid.editing.flipping
  .w{animation:none}`) for the flight and `#view-hub` carries `data-anim="off"` while
  Customize is on; `hubRemove` hides the leaving card with `visibility:hidden` (never
  display:none — it keeps its slot until Flip has measured); the remove buttons pop
  in via `.editing-in`, which a rebuild drops. Meters (`.meter i`, `.mmmem-bar i`,
  `.cs-ai-membar i`, aux_memory/aux_youmodel) are `transform:scaleX(var(--p))` with
  `--p` 0..1 — writers set `--p`, never `width`; the radius sits on the track. The
  pop-out and the model menu close through `HA().exit` (its done hides them; a reopen
  cancels the exit; without the runtime the pop-out keeps its native WAAPI fade + 170 ms
  timer). The model-swap overlay is `'swap'` sequences chained from `done()` (`swapGen`
  retires stale steps), reading time through `hold()` (`T.hold` ready 1.3 / slow 2.2 /
  fail 2.6 s) and a drawn check mark — no setTimeout. Settings Overview figures
  `count()` from the last shown value and the sparkline draws once per change
  (`sparkIn`, `once('spark:'+hash)`); `springHome()` clears the identity values a
  spring leaves inline. A `.stage>.view` un-hide gets `sequence('view')` (opt out with
  `data-anim="off"`; the boot view is skipped while `readyState !== 'complete'`); the
  old `.view{animation:fade}` is gone.
  **Other surfaces:** aux_needsyou.js and aux_agent.js look `HA()` up by name on every
  call and never touch a library. Needs-you: an ok Done/Snooze/re-file is
  `exit(row, done ? 'dismiss' : 'defer')` (hidden in place), then — once the exit AND
  the reload have both arrived — `flip(host, draw, {duration:.32})` keyed on
  `data-flip-id` (item ids plus `ny-grp-*`, `ny-more-today`, `ny-foot`, `ny-blind`,
  `ny-src`, `ny-err`); only the repaint answering the owner's own act counts the Now
  number down; an error moves nothing and keeps the row;
  `.w-body:has(>.ny-wrap){overflow-x:hidden}` stops the Done slide flashing a
  scrollbar. Agent page: the ticker crossfade is `exit('fadeOut')` then `enter('fade')`
  from the exit handle's `finished`, the sigil tick `spring(ring,{rotate:[a-12,a]})`,
  the EKG blip a 3-keyframe `spring` tween, a show-in card `enter('rise')`; the Record
  rail's 3 s poll runs in `quiet()`. aux_recorder animates only NEW rows
  (`once('rec:'+id)`, ≤12 staggered) and its Undo flash is the `.rec-flash` class (an
  opacity keyframe on an `::after`): no JS library ever tweens a colour. The Claude
  Usage pop-out and the Vitals card count up once per open / 24h-7d switch with
  UNIT-LOCKED formatters (`fmt(final)` byte-identical to the static text); their polls
  repaint with no motion; Vitals waits until its card is on screen
  (IntersectionObserver). Onboarding motion is in the onboarding bullet.
  **Tests:** unit — `tools/tests/unit/t_anim_{runtime,tokens,glass,shell,hub,surfaces,
  popouts}.js` and `t_onboarding_steps.js` on the recording stubs in `_anim_stubs.js`
  (real GSAP and anime cannot run in the mini DOM; the real Motion 14 bundle is
  driven), plus `t_r3_vendor_{manifest,route}.py` and `t_r3_vendor_legacy_calls.js`
  (every remaining legacy Motion call site run on the real file); a suite that evals
  extracted index.html functions must carry `_chat_ui`'s ANIM_NULL/HA block.
  Real engine — `tools/tests/browser/t_anim_real.js`, `t_anim_hub_real.js`,
  `t_anim_surfaces_real.js`, `t_onboarding_real.js`: node 22+ over the DevTools
  protocol on a Playwright-cache Chromium (or `HERMES_CHROME`), each with its own
  loopback server — no dashboard, no Playwright, no model; they SKIP without a Chromium
  or the vendored files, and run.sh's browser tier reaches them only with Python
  Playwright AND a live dashboard, so run them by hand (Commands). Only Chromium has
  been measured: WKWebView/JavaScriptCore frame cost, the full-hub glass Flip, the
  optional `.anim-lite` gate (not implemented) and the design's WebKit frame probe are
  still open — the owner's manual WKWebView pass covers behaviour, not frame budgets.
- **App** — `/Applications/Hermes Assistant.app`, real Swift/AppKit WKWebView
  shell (source `app/main.swift`, rebuild `app/build-app.sh`). The window has no
  native title bar, so `HermesWebView.mouseDown` starts the drag itself from the
  band the page reports over the `hermesWindow` bridge
  (`dashboard/aux_window.js`), with every clickable header rect carved out;
  double-click in the band zooms. Before the page reports, the top 28 px is the
  band, so the splash and the error page are movable too. The live regions are
  mirrored to `~/.hermes/dashboard/window-regions.json` for doctor/shell checks.
  `-webkit-app-region` is Electron-only and has never worked here.
- **Versioning + self-update (2026-09-03)** — repo-root `VERSION` is the single
  source of truth; `app/build-app.sh` stamps it +
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
  ONE card into Settings › System & Data (it mounts inside `#sec-system`'s
  `.set-body`, before the shell's `[data-legacy-slot]`, or falls back to
  `#view-mind` and lets the shell's relocator re-home it — no edit to
  aux_settings_shell.js) plus a dot on the `#tab-mind` gear. **A Settings card
  needs both** (visual audit V02/V03): its content inside a `<div class="body">`
  (that wrapper carries the card padding) and its mount inside the panel's
  `.set-body` (the flex column that spaces cards) — a card appended to the
  section itself touches its neighbours and clips its own text
  (`t_v02_settings_card_mounts.js`; aux_doctor.js still appends outside
  `.set-body`). Applying starts
  root `update.sh` DETACHED (`start_new_session`, log
  `~/.hermes/logs/update.log`) so it survives the dashboard restart
  install-services.sh performs; state in
  `~/.hermes/dashboard/update-state.json`. **Apply refuses while a chat turn
  runs** (409 `chat_busy`, from server.py's `chat_jobs_active()` by name)
  unless the body says `force: true` — the restart would drop that reply
  (E18). A check that reaches GitHub/origin but gets no good answer still
  records `attempted_at`/`attempt_signed` in update-check.json (`checked_at`
  stays the last GOOD answer — E85), and on the `main` channel a checkout with
  local commits on top of origin/main is not "behind" (E130).
  **Post-update hand-over (E29):** bash runs a script through an open fd and
  the swap gives `update.sh` a new inode, so everything after the swap used
  to run from the OUTGOING text. Now the outgoing run, right after the swap,
  `exec`s the INCOMING `update.sh --post-update` (plugins, services, gateway,
  summary) when that file carries the marker line
  `# hermes-update-post-protocol: 1` AND passes `bash -n` (else it finishes
  with its own steps, as for a downgrade to an older release); state goes over
  in `HERMES_UPD_*` env, the update-lock fd survives the exec, and a
  provisional failure state is written first. An OLDER update.sh (1.3.2 and
  earlier) still runs the new `install-services.sh` by path, which then calls
  `update.sh --post-update --from-install-services` (only when an update ran
  it: `HERMES_UPDATE_FROM=dashboard` or an update.sh parent; never from a plain
  install). `HERMES_UPD_IN_POST=1` stops any loop. A hand-run
  `./update.sh --post-update` takes the lock itself and finishes an
  interrupted update; one interrupted after the swap still reads "already up
  to date" next time, so run that by hand. `update.sh` handles both git and
  tarball installs (SHA256SUMS-verified — that catches corruption, not a
  tampered release, since the sums come from the same place — E128), refuses a dirty tree, leaves
  `~/.hermes` DATA alone (it does write update.log/-state/-lock, the plugin
  links + `plugins.enabled`, and what install-services.sh writes — C69), and
  only PRINTS instructions when `app/` changed (a rebuild drops FDA — see the
  Message Center note). `install.sh` is the fresh-Mac bootstrap (seed
  `config.yaml`: only keys 0.18 reads, manual approvals, cron deny, local TTS,
  the `bg` provider, no MCP server — C119); `.github/workflows/{ci,release}.yml`
  gate syntax + home-path hygiene and build/sign/publish on a `v*` tag,
  `release.yml` only after `ci.yml` passes as a required job. **Releasing
  (`tools/release.sh`, B30):** stage exactly what ships (the script never
  stages); every check runs before anything is committed or pushed — the INDEX's
  VERSION + `## [<ver>]` CHANGELOG section, CI's static checks and the strict
  unit tier, a new tag, fast-forward on both remotes, and a scan of everything
  `public` would receive (history and merges included) for home paths and
  never-publish files, whose commit count you confirm.
  **Since 1.4.0 `public/main` is ONE squashed commit per release (owner
  decision, 2026-11-13).** Its tree is the private release commit's tree, its
  only parent is the previous `public/main`, and the private history is never
  pushed there (old commits carry placeholder home paths the history scan
  refuses). So `public/main` is NOT an ancestor of `main`, and tag `v<ver>`
  names a different commit on each remote (same tree; `remote.public.tagOpt`
  is `--no-tags` in this checkout so a fetch does not collide with the local
  tag). `tools/release.sh` does not do this yet: it would push `main` to both
  and stops at its step 4. Until it is reworked, run its checks on the
  candidate by hand, push `main` and the tag to `origin`, then build the public
  commit with `git commit-tree <release>^{tree} -p public/main`, push it to
  `public`'s `main` and tag it there. Never merge `public/main` into `main`,
  and never push `main` to `public`.
  **One process lock across both entry points (2026-09-10, audit A08)** —
  `~/.hermes/dashboard/update.lock` is an `fcntl.flock` reserved BEFORE spawning
  (a loser gets 409 "an update is already running") and held for the updater's
  ENTIRE run: aux_update.py passes the locked fd through `Popen(...,
  pass_fds=(fd,))`, so it survives the exec into update.sh and its children and
  the kernel releases it on exit (no stale-lock cleanup; `_upd_running()`'s PID
  flag is reporting only). A bare terminal `./update.sh` takes the same lock (fd
  9, via a python3 one-liner — macOS has no `flock(1)`), skipped when
  `HERMES_UPDATE_FROM=dashboard`; `--dry-run` is exempt.
- **First-run onboarding (1.1.1)** — `dashboard/aux_onboarding.py` +
  `aux_onboarding.js` (one `<script>` tag in index.html, after aux_update.js).
  A four-step full-viewport sheet that auto-opens when
  `~/.hermes/dashboard/onboarded` is absent and **never** when it is present.
  Asks for nothing: chip (`sysctl machdep.cpu.brand_string`), RAM
  (`_machine_ram_gb`), free disk (`statvfs(~)`, `f_bavail` — the space a
  non-root download can really use), macOS (`sw_vers -productVersion`),
  laptop (`pmset -g batt` mentions `InternalBattery`) and cores are DETECTED,
  and the model recommendation falls out of RAM. Routes: `GET
  /api/onboarding/state`, `POST /api/onboarding/apply|done|reset`.
  **The model catalog is STATIC and there is no network at request time** — the
  four entries (Qwen3.5-2B/4B/9B + Qwen3.8-27B) are FULL roster shapes (backend,
  template_args, drafter) whose ids and download sizes were verified against the
  HF API by hand (2026-09-05: 1.75/3.06/5.98/16.08 GB + the 0.87 GB MTP
  drafter); re-verify by hand if you edit them. `_onb_tier(ram_gb)` is PURE (so
  every machine size is testable without the hardware): `<16` 2B only, "chat
  only, small context"; `16-23` 4B, no bg lane; `24-35` 9B + 2B; `36-47` 27B
  *tight* + 9B (with a battery alternative of 9B + 2B); `>=48` 27B + 9B.
  **It is deliberately stricter than `_model_fit`** — the tier budgets for the
  whole running system (primary + always-on lane + OS + app + browser) while
  `_model_fit` judges ONE model, so a 4B reads `ok` on an 8GB Air and is still
  not recommended there, and the 27B+9B PAIR is tight at 36-47GB though the 27B
  alone reads `ok` from 32GB up. Don't "fix" the divergence: the per-model
  badges in the sheet ARE `_model_fit` (1.0.3 phrasing/tokens, identical to the
  model menu), shown right next to the recommendation, so the honest per-model
  verdict is never hidden. `apply` validates every field and writes ONLY through
  existing helpers — idle marker files, `set_prewarm_enabled`,
  `_cb_set_escalation`, and watchtower's own `set_master`/`set_quiet_hours` ops
  via `watchtower_post_handler` (same clamping, same lock). Roster writes are
  ADDITIVE with the full catalog shape (an `add_model`-style stub would download
  fine then load on the wrong backend with thinking on); the background lane is
  the `bg-model` file; **apply never RESTARTS the model server**; it activates
  the primary pick (`_activate_model`) only while the server is down AND the
  pick is COMPLETE on disk (weights and drafter), and says so in
  `applied.primary_active`/`primary_note` (C63). A pick whose download only
  STARTED is PENDING (2026-11-09 audit E22): `~/.hermes/dashboard/onboarding-primary.pending`
  holds two lines (the id, and active-model as it was at pick time), apply and
  state carry `primary_pending`, the sheet offers "Make it the assistant" (a
  second apply with `download:false`) once it is downloaded, and one watcher
  thread (`_onb_primary_promote`) activates it after the download completes —
  only while the server is down and active-model is unchanged since the pick
  (a menu switch since always wins; a stale pick is dropped). A failed download
  never completes, so it never activates, and the sheet shows the error and
  stops polling (known gap: on a RE-RUN, a pending pick whose download failed
  still reads "still downloading" — the state route lacks the error). On a download request `bg-model` is repointed only at a model
  that is complete or whose download really started (E111). Continue stays off
  until a real state has loaded, so a failed state read never posts defaults
  (E23). "Downloaded" = weights AND
  drafter (C70). A download is refused when free disk `< needed + 5GB`.
  **Secrets:** `telegram_configured`/`google_configured` are BOOLEANS —
  `_onb_env_has()` compares the `~/.hermes/.env` value to `""` inside the
  function and never returns, logs or stores it. `fda` is True/False/**None**
  (the Message Center's own verdict from messages.json); None means "the app has
  not reported yet" and must render as unknown, never as denied. **Load order:**
  aux files exec SORTED, so this module runs BEFORE aux_watchtower — every
  watchtower/foreign global is resolved by name at REQUEST time through
  `_onb_g()`, never captured at module load (the discipline aux_index uses).
  UI notes: the "what leaves this Mac" table is generated from ONE constant
  (`NETWORK_FACTS`) by ONE reusable function (`networkTableHTML`, `inputs:false`
  for a read-only render) — that is deliberately the seed of the Data & Network
  panel (#16), so extend the constant rather than copying the markup. Claude
  escalation is shown only when `claude_cli` is detected. Esc does nothing until
  the final step on a first run, and works everywhere on a re-run
  (`window.hermesOnboarding.open()`; a "Run setup again" row is injected into
  Settings › Overview the aux_update way — **aux_settings_shell.js is never
  edited**). The sheet lives in index.html's own document and therefore INHERITS
  the app palette — re-declaring a fifth copy of the tokens would be the bug.
  **Round 3 (ONB_VERSION 1.4.0).** **Extra-steps hook:** a module adds an optional
  step WITHOUT editing aux_onboarding.js — push an entry onto
  `window.hermesOnboardingSteps` (before or after the file loads; the array is
  adopted, never reassigned) or call `hermesOnboarding.registerStep(entry)` (refuses an
  invalid entry with one warning; a same-key entry replaces the earlier one in place;
  `hermesOnboarding.steps()` lists the live order). Entry `{key
  /^[a-z][a-z0-9_-]{1,23}$/ (not welcome|models|prefs|done), title, sub, eyebrow?,
  after:"prefs" (v1's only slot), optional:true, render(ctx)→html, wire(node, ctx),
  save?(ctx)→Promise<{err?, applied?:string[]}>, statusRow?(det)→[cls,label,text],
  summary?(ctx)→string[]}`; `ctx = {rerun, data, repaint(), setErr(msg)}`. Extra
  steps sit after prefs and before done in registration order ("Step n of m" counts
  them); `save()`'s `err` keeps the step, Skip always goes on and never calls save, a
  save still pending after 20 s counts as failed, `setErr` updates the error line in
  place (typed text survives), `applied` (≤12 lines) and `summary()` reach the Ready
  step, `statusRow` rows join the prefs step's Connections list; anything an entry
  throws is caught and logged once (a render throw shows "This step could not be
  shown."). **Models step:** `window.hermesAdvisor` is looked up at RENDER time
  (aux_advisor.js loads after this file); when `hermesAdvisor.step` exists it renders
  the models body and the sheet keeps its own action row under it, `ctx` adds
  `getPick()`/`setPick({primary, background, download_gb?, uncensored?})` (repaints only
  the action row; `download_gb === 0` = everything already local), Next awaits
  `step.save()` and stops on `{err}`, then posts the picks to `/api/onboarding/apply`
  with `ack_uncensored:true` only for an uncensored pick. No advisor, or one that
  throws, renders the legacy body. **Backend:** the state route's recommendation is
  `_adv_recommend_compat` by name (`_onb_recommend`/`_onb_tier` stay the fallback);
  `GET /api/onboarding/state?brief=1` answers the done marker alone (the per-load boot
  check and the Settings row); `_onb_known_model` and `_onb_cat()` accept `_ADV_BY_ID`
  ids (labels, RAM, the background fit check); apply's uncensored gate is in the Model
  advisor bullet. **Motion (§8.9)** goes through `HA()` only: `sequence('onb-open')` on
  open, `sequence('onb')` + `words(h1)` ONLY on a step change, a count-up on the Ready
  figures, nothing on toggles or the 2.5 s download poll; the CSS keyframe entrance is
  gone. Tests: `t_onboarding_steps.js`, `browser/t_onboarding_real.js` (CDP on the real
  libraries, no Playwright); `browser/t_onboarding_ui.py` needs Python Playwright and
  answers the step modules' scripts with 404 so its legacy assertions stay
  deterministic.
- **Trust made visible (1.1.3)** — backlog #15/#16/#17: the three places the
  product asked to be believed without showing its work.
  **(a) Data & Network card** — `dashboard/aux_network.js` (one `<script>` tag
  in index.html, AFTER aux_onboarding.js). **A module cannot register a new
  Settings panel id**: `aux_settings_shell.js` builds its rail and its twelve
  panels ONCE from a `PANELS` array captured in its IIFE, `ensureShell()`
  early-returns on the second call, and `window.SETTINGS_PANELS` is a read-out,
  not a hook — so this is a CARD at the top of **Settings › Connections**
  (`order:-1` in the panel's flex `.set-body`). It mounts DIRECTLY into
  `#sec-connections` and, unlike aux_update.js, **never falls back to
  `#view-mind`** — the relocator sends an unknown id to sec-system, which is
  right for the update card and wrong for this one. Content is
  `hermesOnboarding.networkTableHTML({prefs, inputs:true, last})` over the SAME
  `NETWORK_FACTS` the first-run sheet uses, so **adding an egress path means
  adding one row to that constant** and both surfaces update; the sheet's table
  CSS was extracted into `netCSS(scope)` (exported) and is emitted here
  re-scoped to `#mind-extra-network`, so markup and styling cannot drift apart.
  The toggles are the EXISTING switches (`setClaudeEscalation()` →
  `/api/claude/escalate`; `POST /api/watchtower {op:"set_master"}`), optimistic
  with revert, and the card listens to `hermes:claude-escalation` so the model
  menu, the Bridge panel and this card never disagree. **NETWORK_FACTS has 17
  rows (C52; round 3 added `github_repos` + `github_push`; the 2026-11-09
  audit added `agent_tools`, E25)** (HF on every online model start, the
  6-hourly web-search probe, the gateway, Edge TTS and Apple's speech model
  included). `agent_tools` is the widest path: the agent's own terminal
  (`curl`, git, pip, the `claude` CLI…), browser tools and web extract, inside
  a turn, with no switch — Hermes asks only before commands it classes as
  risky, never about a plain `curl`. The rows also state the real cadences
  (watch rules poll Yahoo Finance and CoinGecko every 5 minutes while News is
  on and a price rule exists — E84) and that GitHub's notices ignore both
  masters (E77). A switchless row may carry an `off` how-to — keep it as short
  as the others or the Switch column overflows (see Table fit). **"Last outbound" is evidence, not status**: `GET
  /api/network/last` (`aux_network.py`, file reads only) gives per fact id when
  a request really LEFT the Mac (update check, intel `updated`, Telegram
  deliveries, spawned `claude -p` rows by caller, Google); absent = "not
  tracked", null = "no record"; the card re-reads on mount and visibilitychange
  (C72). The lede and footer claim only what the table covers (no
  "everything Hermes sends" / "Everything else runs on this Mac" any more —
  E25), then a read-only "What stays local" list (inference, index, chats,
  notes, recorder). **Table fit:** the
  Settings panel is ~700px and this card adds a fifth column — the sheet's own
  min-widths plus the nowrap row header come to 732px of intrinsic minimum
  against 673px of room, and `.onb-tablewrap{overflow-x:auto}` then silently
  scrolls the **Switch** column out of view. A `max-width` on the table does
  NOT fix that (table-layout:auto overflows a max-width below its minimum);
  relaxing `tbody th` to `white-space:normal` and the When/What `min-width` to
  0 **in the card scope only** does. **Since the 2026-11-12 visual fixes (V01)
  the card no longer draws columns at all:** in the card scope each `tr` is a
  two-line grid (name, "Last outbound" and the switch on top in a fixed 132px
  track whose text wraps; When and What side by side under it), the `thead` is
  visually hidden, and `tableRoles()` in aux_network.js puts explicit ARIA
  table roles on the same elements after every render (WebKit drops table
  semantics once the parts change `display`). `networkTableHTML()`'s markup is
  unchanged and the first-run sheet stays a real four-column table; the shared
  `netCSS` only lets a switchless row's how-to wrap.
  **(b) Per-model details** in the model menu (`.mmi` rows, index.html) —
  context / thinking / backend / drafter / RAM / download size / lane as a
  `<dl>` behind a 40×40 "Details" chevron. The panel is a **SIBLING after
  `.mmi`**, never a child, so expanding does not change the row's height
  (measured identical collapsed and expanded); open rows live in a module-level
  `mmOpen` Set because `loadModels()` rebuilds the menu every 30 s and would
  otherwise collapse what the user just opened. `ctx` (262144 for the whole
  Qwen3.5/3.8 family) is a ROSTER field on `_SEED_MODELS` + `_ONB_CATALOG`, not
  a served one — the menu must answer "how big is its context" for a model that
  is not loaded, and asking the model server would wake it.
  **(c) Download estimate before confirm** — `models_payload()` gains
  `download_gb` per row and `disk_free_gb`/`disk_headroom_gb` once.
  `_model_download_gb()` measures the on-disk snapshot when the model is
  COMPLETE (`_dir_size_gb` uses `os.stat`, which follows HF's blob symlinks —
  `lstat` would report ~137 bytes per shard), main repo plus a separate-repo
  drafter; a PARTIAL download is deliberately not measured (it would understate
  the pull still to come) and falls back to the onboarding catalog's verified
  `size_gb + draft_size_gb`, resolved through `globals().get("_ONB_BY_ID")` at
  call time. 300 s cache. `_disk_free_gb()` is `statvfs(~).f_bavail` in GiB.
  The menu quotes "~17 GB download · 412 GB free" in the confirm and refuses
  with `data-disk="short"` + a `--bad` reason line when
  `free - download < 5 GB`; **`download_model()` enforces the identical rule**
  (`{"ok": false, "error": "not enough free disk"}`) because the route is
  reachable from curl and Quick Ask. Unknown size or unknown free space never
  refuses — same fail-open rule `fit` follows. `_model_fit` is untouched.

- **Model advisor (round 3, `docs/plans/model-advisor-2026-10.md`)** — "what fits
  this Mac, for what you use it for". aux files exec SORTED, so `aux_advisor.py`
  (ranking, answers, routes) is the FIRST aux_*.py to run, BEFORE `aux_advisor_hw.py`
  (detection + estimator): every cross-module name is resolved at CALL time (`_adv_g`
  / `_advhw_g`); shared names are `_adv_*`/`_ADV_*`, private ones `_advr_*` /
  `_advhw_*`, and `t_advisor_hw.py` fails if any other module defines a name
  aux_advisor_hw.py defines.
  **`aux_advisor_hw.py` — no routes, threads, writes or network; never wakes a
  model.** Detection runs sysctl/ioreg/pmset/system_profiler/sw_vers by ABSOLUTE path
  through `_adv_run()` (3-5 s timeouts; the module-level `subprocess`, so harnesses
  stub it), no sudo; every parser is pure and total, and a fact it cannot read is None
  plus a `source`, never a raise. The SPHardware parser is a WHITELIST (the serial
  number, platform UUID and UDID never leave it). GPU budget precedence:
  `iogpu.wired_limit_mb` > 0, then the Metal probe (stdlib ctypes in a `python -I -c`
  child, 2 s, env = PATH only; it creates a Metal DEVICE, never a model — 51.84 GiB =
  0.81 × RAM on this Mac), then an assumed 0.75 × RAM at ≥36 GB / 0.66 × below; a
  FAILED probe is retried on `bust` and at most every 10 min, a good answer is final. Any other static fact a timed-out or failed command left blank is retried the same way (Re-check / `bust`, ≤ every 10 min) instead of being kept until restart (2026-11-09 audit E94; a permanently failing `system_profiler` costs one slow `_adv_hw()` per 10 min).
  Static facts are cached per process, keyed on `kern.boottime` +
  `iogpu.wired_limit_mb` (key re-read ≤ every 60 s); dynamic ones (power, thermal,
  disk) expire after 60 s. A cold `_adv_hw()` holds one lock (~0.3 s + ≤2 s probe):
  call it once per request, never in a loop. **Plan limit** = min(`MLX_SOFT_GB` − 2,
  GPU budget, RAM − reserve), soft from the real `_mem_ceilings` by name, reserve 6 GB
  at ≥24 GB, 4.5 at ≥16, else 3.5 (soft wins ties); verdict fits ≤ 0.85 × limit,
  tight ≤ limit. `_adv_apc_entries` mirrors mlx-server.sh / mlx-server-bg.sh
  (`t_advisor_hw.py` parses both — change all three or none). `_ADV_CHIPS` (38 rows) is
  DATA: llama.cpp #4167 pp512/tg128 + bandwidth, `e_chip`/`p_index` relative to the
  reference M5 Max 40c (a test recomputes them). `_ADV_FACTS` (11 ids) holds exact HF
  byte counts and config numbers read by hand on 2026-10-26; facts never CURATE a
  model — only the seeds, `_ONB_CATALOG` and `_ADV_EXTRA` do. Units: memory GiB,
  downloads decimal GB, bandwidth decimal GB/s. Every estimate carries
  `estimate:true`, a `basis` (measured-here | reference | formula), the substituted
  `formula` and a `range` (±10% measured here, ±15% on the reference chip, ±25% for a
  measured chip row, ±40% for a scaled row or an assumed constant — the 2B, 4B,
  35B-A3B and uncensored-27B speed constants stay labelled assumptions until the AC
  calibration). "Measured here" = medians (n ≥ 5) from this Mac's own model-server
  logs through aux_context's `_cx_rows`, read only when `hw.local`; the setup-prompt
  size S is the newest cold read of a STREAMED conversation turn ≤ 32,000 tokens (a
  pasted document or the compression summariser must never become S). MTP multipliers
  only where measured (stock 27B: chat 1.56, code 1.92, agent 1.74); an unmeasured
  drafter gets 1.0. `tools/bench/advisor_calibrate.py` reads logs only (refuses on
  battery, in CI and in the unit tier; writes nothing; procedure
  `tools/tests/ac/advisor-calibration.md`). The golden fixture
  `tools/tests/unit/fixtures/advisor_golden.json` is regenerated only after a
  DELIBERATE constant change (`t_advisor_estimate.py --write-golden`, review the diff).
  **`aux_advisor.py` — ranking, answers, routes.** It ranks what the estimator says
  over `_adv_catalog()` (seeds, then `_ONB_CATALOG`, then `_ADV_BY_ID`; ids with
  facts): every primary × background lane in {none, 9B, 4B, 2B}, the lane strictly
  smaller, offered only at ≥ 24 GB, and a plan with a lane must FIT. **The lane counts
  as running only when it can run** (`_advr_lane_runs`, files only: a
  `model-start-ok-bg` token or an mlx-bg.log written in the last 48 h — E95);
  otherwise the result's `lane {running:false, optional}` names the lane that would
  fit next to the Recommended primary and a note says nothing starts it. Exclusions, first
  match wins: memory > vision > small (agent quality < 4, code < 6) > slow (< 6 tok/s
  at the turn or a cold setup read > 180 s). Priorities weigh quality vs speed
  (faster 0.30/0.70, balanced 0.55/0.45, best 0.80/0.20); on battery the weights ×0.75
  and battery 0.25. **Recommended = the best VERIFIED usable recommendable plan, else
  the best usable recommendable one** — usable = fits at ≥ 8 tok/s in a typical turn;
  recommendable = never uncensored (owner decision D4), and a `role: alternative`
  build (the stock 35B-A3B) only once a drill verified it (D3); verified = the facts'
  reference drill or a LOCAL drill record in promotion.json (a pass ≥ 5/6 adds it, a
  fail removes it) — the roster's own `verified` is NEVER read. Relaxation docs → code
  → agent while nothing usable survives, always stated in the notes. Alternatives: ≤ 2
  stock plans with different primaries, plus ONE uncensored plan only when
  `uncensored` is a use AND the acknowledgement is STORED. "What you run now" is
  always evaluated. Answers live in settings.json **`advisor`** `{uses, priority,
  power, uncensored_ack, saved_at}`, written only by `POST /api/advisor/answers`
  through `settings_update` (merge, strict types; a failure inside the lock writes
  nothing; `uncensored` in uses needs the ack; `{uncensored_ack:false}` always revokes
  and drops the stored use) and NOT exported by Config-as-Code (`CFG_SETTINGS_ALLOW`
  lacks it). Routes: `GET /api/advisor/state` (`?bust=1` re-checks detection), `GET
  /api/advisor/rank?uses=&priority=&power=` (never writes; it cannot assert the ack, so
  it answers `needs_ack:true`), `POST /api/advisor/answers`. A guarded models_payload
  rebind (`_adv_wrapped`) adds per-row `advice` and a top-level `advisor`, cached 30 s
  as `_widget_cache["adv_advice"]` (dropped on a POST), one `_adv_hw()` per build, no
  port probe, never raises; `fit` stays `_model_fit`'s.
  `_adv_recommend_compat(ram_gb, downloaded)` returns `_onb_recommend`'s exact keys plus
  `advisor` and falls back to `_onb_recommend` whenever the advisor cannot answer.
  `_ADV_EXTRA` is empty today (both MoE ids are seeds; froggeric's Heretic has facts
  but is not curated, D4(b)); `_ADV_BY_ID` is consulted by name by
  `_curated_model_ids`, `add_model` (never stores `verified`) and `_catalog_dl_gb`.
  **`aux_advisor.js` → `window.hermesAdvisor` `{v:1, step, mountCard, explain, fmt,
  ackText}`** (script tag right after aux_evals.js): the onboarding "models" step body
  (one `GET /state` per page life; re-ranks through the pure `GET /rank`, debounced
  150 ms, replace only `.adv-res`, so focus survives); the Settings › Agent & Models
  card `#mind-extra-advisor`, mounted FIRST in `#sec-models` (order:-1, never
  `#view-mind`) with "Last analysed h:mm AM/PM · Re-check" and per-plan buttons that
  act only on their own click (Download behind `confirm()`; "Use as background lane"
  through `/api/onboarding/apply`; "Make it the assistant" only through `onModelClick`
  on the LIVE menu row) — answers are stored only on "Use these answers"; and a guarded
  `modelDetailsHTML` wrapper (`__adv`) adding "Speed here", "First reply after wake",
  "Memory with cache" and "Advisor" to the model menu's Details — TEXT ONLY (the menu
  lives in `<header>`; refusals stay `fit==='no'` + disk-short). Uncensored rows carry
  `data-uncensored="1"`, the Uncensored chip, "no refusals — the approval gate is the
  only brake" and "unverified until drilled" while `verified===false`; `onModelClick`
  asks ONCE (the switch and the acknowledgement together, quoting the server's ack
  text) and keeps the 409 → confirm → one-retry fallback. The questionnaire's
  uncensored use never turns on before the acknowledgement is STORED. Motion: only
  `HermesAnim.enter` after the owner's own change. Tests: `t_advisor_rank.py` (GOLDEN,
  11 machines × 6 profiles; `--print-golden`), `t_advisor_routes.py`,
  `t_advisor_switch_gate.py`, `t_advisor_ui.js`, `browser/t_advisor_ui.py` (Playwright,
  else a stdlib CDP driver, else SKIP).
  **Uncensored gates (server-side, strict boolean, fail closed):** `POST
  /api/models/switch` to a row `_roster_uncensored` (or the advisor facts / extra)
  marks uncensored answers **409** `{ok:false, needs_ack:true, error:"uncensored_ack",
  id, label, message, ack_text}` unless the body carries `ack_uncensored: true` (any
  other value, null included, is a 400) or settings.json `advisor.uncensored_ack` is
  exactly `true`; an unreadable settings file means no ack. `_switch_ack_refusal`
  (server.py) is the check; `switch_model()` itself is ungated, so wake/resume, the
  config import's `apply_active` and the drill's temporary swap can still load one
  (known, by design). `POST /api/onboarding/apply` refuses an uncensored primary OR
  background pick the same way (`_onb_ack_gate`; 409 with NOTHING applied, 400
  `bad_ack` for a non-boolean) — it reads `_roster_uncensored` ∪ `_adv_is_uncensored`
  directly because `t_advisor_switch_gate.py` pins server.py as the only file naming
  `_switch_ack_refusal`, so a new uncensored source goes into both gates or neither.
  The stored ack is global and one-time, and any same-uid process (the agent's
  terminal included) can POST it — the approval gate stays the only brake.
  **What the advisor never does (design §8.4):** no auto-switch, auto-download or
  auto-activation; no restart of either lane; no write to `model.context_length`,
  idle-suspend or the prompt profile (it only suggests); no probe of
  :8080/:8081/:9119 (no `model_online`/`bg_online`/`mlx_admission`); no new egress
  (no NETWORK_FACTS row). Its only write is settings.json `advisor`.
- **GitHub sandboxed changes (round 3, `docs/plans/github-sandbox-2026-10.md`)** — the
  agent works on repositories the owner connected only inside a local sandbox, and
  only as a change REQUEST the owner reviews. **The agent never holds the token.**
  `dashboard/gh_api.py` + `dashboard/gh_git.py` are stdlib sibling modules (imported
  like permissions.py); `aux_ghchanges.py` (pipeline) sorts before `aux_github.py`
  (connector), both before aux_needsyou/aux_network/aux_watchtower, so every
  `_gh_*`/`_ghc_*` and foreign name is resolved at CALL time.
  **Connector (`aux_github.py`, `gh_api.py`).** Only a FINE-GRAINED `github_pat_`
  token is stored (classic `ghp_`/40-hex and app tokens are refused), and only after
  the SCOPE WALL passes: a token that can administer, read secrets/variables, add
  webhooks, run Actions, read or add the account's SSH, GPG or SSH-signing keys
  (`/user/keys`, `/user/gpg_keys`, `/user/ssh_signing_keys` — E50) or reach
  unlisted repositories is refused and NOTHING is
  written (its probes write a tiny unreferenced `hermes-probe` blob to each connected
  repo, and TRY one on up to three public repos per owner, for at most two owners, it
  did NOT connect — that write lands only if the key can reach them, which refuses the
  key; disclosed in NETWORK_FACTS). **`write_reach` is a sample, never a proof
  (E08):** new connects store only `sampled` (a sampled public repo refused the
  write) or `unchecked` (nothing to sample — itself a warning finding); an
  older store's `contained` is reported as `sampled`; the status payload
  carries top-level `write_reach` + a plain `write_reach_note`, and the card
  warns whenever it is not proven. Store `~/.hermes/dashboard/github/` (0700): `credential.json` (0600),
  `state.json`, append-only `ledger.jsonl`, `changes/`, `mirrors/`. The token is NEVER
  in a response, log line, recorder row, state.json, settings.json, config export,
  argv, remote URL or `.git/config`: it reaches git only as `_gh_git_env()`'s
  `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` extraheader triple for ONE
  network call, and HTTP only as an Authorization header to the resolved API base;
  every surfaced upstream string goes through `gh_api.scrub()`. The REST client never
  follows a redirect and fails closed on 5xx, timeout, rate limit or redirect;
  `HERMES_GITHUB_API`/`HERMES_GITHUB_GIT` are honoured only with
  `HERMES_TESTS_OFFLINE=1` AND a loopback/file:// value. Not in Keychain, deliberately
  (a `security`-created item answers the agent's terminal without a prompt; real
  isolation waits for F05's signed-app ACL). Routes: anyone — `GET
  /api/github/status|ambient|create_url`, `POST /api/github/pause {paused:true}`,
  `/repos {op:remove}`, narrowing `/settings`, `/disconnect`, `/revoke`; OWNER-ONLY
  (each handler 403s when `ctx.nonce_ok is False`; `_gh_route_gated` is registered in
  `ROUTE_OWNER_GATES` for F05) — `/connect`, `/repos {op:add}`, `/check`,
  `/drift/ack`, widening `/settings`, `/pause {paused:false}`. **Pause asymmetry:**
  whatever REDUCES capability needs no proof of owner, whatever widens it does —
  so `remove` needs no owner proof either, and a removed repository is no longer
  checked (an agent that removes rows hides later drift or an Actions
  permission there; holds already set, ledger and drift rows survive). Holds
  (expired, unauthorized, forbidden permission) pause writes and cannot be switched
  off by hand (409 `held`). A hold records its CAUSES and is cleared only when
  a check re-observes each cause gone (or by a reconnect); `expired` is never
  stored — `_gh_holds()` computes it from the key's expiry (E51). **A renamed
  or transferred repository (a 3xx on its read) is marked `moved`** instead of
  raising a false "reaches a repository you did not list" hold; `remove`
  DROPS the row (freeing its slot — only active rows, listed or enabled and not removed, count toward the 10: `_gh_row_active`) and
  deletes its mirror, and `forgotten` remembers the removed ids for the reach
  check until the next connect (E07, E53). **Disconnect and revoke delete
  every mirror** (answers carry `mirrors_deleted`/`mirrors_left`/
  `sandboxes_kept`/`cleanup_note`; the sandboxes are the owner's and are kept —
  E09). Owner
  decisions fixed in code: `ping_on_push` and `draft_prs` are always true,
  `max_pushes_day` ≤ 10 (`GH_LIMITS`: 10 repos, 200 files / 5 MB / 2 MB per file / 1 MB
  diff, 2 GB repo, 24 h pending TTL, 3 requests per repo per 10 min). A repo whose
  workflows read secrets on a `hermes/*` push (`workflow_run` counts), or whose
  workflows cannot be read, cannot be enabled. **The 6-hourly check is GET-only**
  (first look 90 s after start; no write reaches GitHub): protection, expiry,
  read-only permission probes, private reach, CI, and DRIFT — a `hermes/*` head the
  ledger did not write gets ONE summary Telegram notice per repository per check,
  sent after `_GH_NET_LOCK` is released (E54). A failed check is retried on a
  back-off of 10, 20, 40… min capped at 6 h (`check_fails`,
  `GH_CHECK_RETRY_BASE`), and a rate-limited workflow read no longer wipes a
  repository's workflow verdict (E52). Drift and push notices ignore the
  watchtower masters (`GH_SECURITY_NOTICES_IGNORE_MASTERS`, E77).
  **Ambient GitHub access** (gh login, git osxkeychain/store/gh helpers, SSH keys, env
  token names) is DETECTED and shown with exact commands for the owner, never changed:
  `gh` is never executed, `security find-internet-password -s github.com` runs without
  `-g`/`-w`, a credential helper's value is reduced to its kind. Only a plain GET
  `/api/github/status` or `/api/github/ambient` may run those local probes (5-minute
  cache; mutating routes answer `ambient: null` until one has); `GET
  /api/github/status?scan=0`, which the UI's poll and the onboarding step send, never
  does. The status payload carries `pending_ids` (≤ 50, newest first) for the UI's poll.
  **Change pipeline (`aux_ghchanges.py`, `gh_git.py`).** Two git stores, ONE trusted:
  the dashboard-owned bare mirror `~/.hermes/dashboard/github/mirrors/<owner>__<repo>.git`
  (its config regenerated from a template before EVERY git call and compared
  with it after: a config changed mid-call is tampering — `gh_git.GitTampered`,
  reported through `gh_git.TAMPER_HOOKS` — and every git transport is pinned
  in the hard flags (`protocol.allow=never`, https always, file only where
  allowed, every per-protocol key set) (E56; a writer that restores the bytes
  before the compare is not detected);
  alternates/commondir/grafts/shallow/info-attributes deleted; `gc.auto=0`, but
  PRUNED: over-limit content is refused 413 `too_large` BEFORE anything is
  snapshotted, a pending/pushing change pins its commit with
  `refs/hermes-hold/<change id>`, and objects a blocked, rejected, withdrawn or
  expired change left are removed with `git prune --expire=now` — E09) and the agent's clone
  `~/HermesSandbox/github/<owner>/<repo>` (dead pushurl `hermes-no-push://…`,
  `credential.helper=`, hooks and ssh off). **The dashboard never runs git with the
  sandbox's `.git`**: it reads only the work tree's files, through a separate
  `GIT_INDEX_FILE` in the mirror. Every git call gets an env built from nothing
  (`PATH=/usr/bin:/bin`, empty HOME, no system/global config) plus
  `gh_git.hard_flags()`; file:// only in the unit tier or to clone our own mirror.
  **The parent is GitHub's API truth, never a local ref** (`rev-list <commit> --not
  <parent>` must be exactly `[commit]`). One push shape,
  `<sha>:refs/heads/hermes/<slug>` (hermes/* only; no force, no tags; slugs
  main/master/head/trunk/default and the default branch's name are refused); retract
  is a `--force-with-lease` compare-and-swap deletion, allowed only while the ledger
  head, GitHub's head and the change's sha all match. **§3.4 checks** (`C-*`, at
  request time): known-secret canaries (secret-keyed `.env` values, serve-token,
  messages-token, the Google token/client secret, `auth.json`, `mcp-tokens/*.json`,
  the GitHub key; raw, base64 at any alignment, hex, URL-encoded; in content, title,
  slug and file names) and PEM blocks are HARD blocks — `.env` values are parsed
  the way python-dotenv parses them (`_ghc_dotenv`: an inline `# comment` cut,
  quotes and escapes, multi-line values), the raw remainder of the line kept
  as a canary too, and a parser failure falls back to the old reading, never
  to fewer canaries (E06); vendor-shaped values are
  warnings that need a tick (aux_convos' regexes by name — missing means FAIL CLOSED);
  workflow paths, gitlinks, the size limits and push-exposed CI block; hidden unicode
  is marked `⟦U+XXXX⟧`; a public repo needs a strict `confirm_public:true`, and
  decide re-reads the visibility from GitHub (`_gh_repo_visibility_fresh`, by
  name) and answers stale when it changed (E55); @mentions
  are neutralised outside text that is certainly code (an @ counts as part of a
  URL only where GFM really autolinks it; `_@user_` and a non-ASCII neighbour
  are still mentions — E60), and PR text keeps its leading indentation (E59).
  **Content the diff cannot show needs a tick (E02):** every file git calls
  binary is a `C-binary` WARN (never `info`); one that is mostly text (≤ 5%
  undecodable/control characters with NULs left out, `GHC_TEXTISH_BAD`) is
  still line-scanned and carries `files[i].text_preview` (a `--text` patch) +
  `text_preview_truncated`, the check carrying `preview`/`preview_truncated`,
  and a file that was TEXT before says so (`was_text`). **`C-longline`** warns
  on an added line over `GHC_LONGLINE` (400) characters or holding a run of
  `GHC_WS_RUN` (40) blank characters / `GHC_WS_COLS` (80) columns before more
  text — every Unicode space, the braille blank and the Hangul fillers count,
  hidden characters inside a run do not end it. The change carries
  `diff_lines` and `diff_truncated`. An edited body over `GHC_BODY_MAX` (8 KB
  after cleaning) is refused 422 `body_too_long` at decide, never cut. A decision carries the change's
  **digest** (repo, target ref, parent, tree, title, body, acks, confirm_public);
  anything else is 409 stale. **Decide re-runs Pause/holds, the Trust tier and
  the daily push budget AFTER taking `_GH_NET_LOCK`** (`_ghc_decide_gate`; an
  approval may wait up to `GHC_NET_WAIT` = 90 s there, and push rows are only
  appended under that lock, so the budget read is exact — E05). **The ledger
  push row is appended BEFORE the push, under
  `_GH_NET_LOCK`**, and compensated (a `ref_delete`, or a push row whose change id ends
  `:undo`, which the push budget skips) when the push provably did not land — so the
  drift check never flags Hermes itself. A change left in `pushing` by a
  restart is settled by `_ghc_reconcile` (under the lock, before a decide or
  retract): GitHub's head is read and the compensating row written when the
  commit never landed (E57; an interrupted push still uses one of the day's
  ten). A successful retract marks every earlier pushed change on that branch
  retracted too (E58); a retract whose mirror is missing builds an empty one
  first (else 503 `mirror_unavailable`). Pushes open DRAFT PRs, ≤ 10 a day, with a
  Telegram notice per push. Fail closed: no Flight Recorder → 503 (a reject alone does
  not need it). Rates also cap GitHub reads at 30 per repo per 10 min. Finished change
  files are kept 30 days, ≤ 300. Trust class **`github-write`** ("GitHub pushes & pull
  requests", the 19th, floor ask). Exports `_ghc_pending`, `_ghc_cancel_all`,
  `_ghc_cancel_repo`, `_ghc_last_push_ts`, `_ghc_delete_mirror(owner, repo)`,
  `_ghc_delete_all_mirrors()`, `_ghc_reconcile(full=None, net_held=False)`. **B02: decide/retract are OWNER-ONLY by a
  SPEED BUMP, not a boundary** — `_ghc_from_dashboard` wants a PRESENT allowed
  `Origin` equal to `Host` plus `Sec-Fetch-Site: same-origin`, read from the new
  `RouteCtx.headers` slot (a lower-case copy, first occurrence wins); any same-uid
  process can forge both until F05's app-held nonce. `decide {decision:"reject"}` and
  `withdraw` are ungated (they only reduce capability). Meanwhile the bounds are the
  token's scope and expiry, hermes/* only, draft PRs, the push budget (held
  under the lock since E05), the per-push Telegram notice and the drift detector. The PR footer names this Mac's host name
  (visible on a public repo).
  **Agent surface.** The agent reaches GitHub only through the skill
  `skills-snapshot/autonomous-ai-agents/github-sandbox/` (SKILL.md +
  `scripts/gh-sandbox.sh`: status, prepare, refresh, request, changes, withdraw — six
  routes, JSON built with `json.dumps` and sent on stdin, no Origin header). **Nothing
  syncs or installs it** (install.sh installs only plugins, and `.gitattributes` marks
  skills-snapshot/ export-ignore, so a release tarball lacks it): copy it to
  `~/.hermes/skills/` by hand (deploy checklist). No skill or script under
  skills-snapshot may name decide, retract, connect, drift/ack or pause off
  (`t_gh_gate.py` scans for it). **The `[context]` line:** aux_github.py wraps
  `access_preamble(user_text="")` and INSERTS `_gh_preamble_line()` right after
  server.py's local-index line (`_GH_PREAMBLE_AFTER`, the last STABLE line) — never
  after the volatile tasks/calendar/memory/clock lines (APC prefix cache);
  disconnected or unprepared, the preamble is byte-for-byte server.py's; cached on the
  stat of credential.json + state.json and a 300 s TTL. **If the local-index line's
  opening words change, update `_GH_PREAMBLE_AFTER`** (the fallback, just before the
  first volatile line, is correct but less precise). **approval-gate additions are
  TRIPWIRES and defence in depth, NOT a boundary** (string matches: encoding, a script
  written first or a renamed remote evade them, and the terminal can still `cat` a
  0600 file it runs as the owner): class `secret` hard-blocks `read_file`,
  `search_files` and MCP read tools on `~/.hermes/dashboard/github/**` in every path
  form, and a search rooted at `~/.hermes` or `~/.hermes/dashboard` (search tools skip
  hidden folders only below their root); class `sandbox-git` ASKS before a write inside
  any `.git` under `~/HermesSandbox` (it sorts before `git`, so a `git` allowlist key
  does not pass it); terminal/execute_code/process tripwires hard-block (never ask,
  ignore `command_allowlist`) under the keys `github:decide`, `github:retract`,
  `github:connect`, `github:unpause`, `github:drift-ack`, `github:credential`,
  `github:gh-token`, `github:credential-fill`, `github:keychain`, `github:push`. Every
  `mcp_*` tool now passes through the gate. **Needs-you `github` source:**
  `_ny_collect_github` consumes `_gh_attention()` (hold/drift → now, an expiring key →
  today, open_to `connections`) and `_ghc_pending()` (each pending change → today,
  open_to `#github-review=<id>`), file reads only, absent while disconnected (in
  aux_needsyou.js, Open calls `hermesGitHub.openReview(id)` / `openCard()`, falling back
  to setting the hash / the Settings-shell opener); ids
  `github:attn:<id>@<since>` / `github:change:<ghc-id>` (long ones hashed); hold/drift
  rows bypass the VIP rule for `now` by the owner's mapping (the five-item cap still
  applies), and `_NY_MODEL_SKIP` keeps EVERY github row out of the optional model pass.
  NETWORK_FACTS rows `github_repos` and `github_push` (their last-outbound comes from
  `_gh_last_read_ts`/`_gh_last_push_ts`).
  **UI (`dashboard/aux_github.js`, one `<script>` tag after aux_network.js) →
  `window.hermesGitHub` v1** (`openCard()`, `openReview(id)`, `closeReview()`, plus the
  pure `connectHTML`/`gate`/`decideBody`/`markHidden`/`ghUrl` the tests drive; guard
  `window.__auxGitHub`). Every call is a same-origin `/api` route; nothing in the page
  talks to GitHub. Four surfaces: **(a) the Settings › Connections card
  `#mind-extra-github`**, mounted DIRECTLY into `#sec-connections`' `.set-body` (never
  `#view-mind`, the aux_network.js rule). Not connected it is the connect wizard — ONE
  component (`connectHTML` + its wiring) shared with the onboarding step: "Open GitHub to
  create the token" opens the prefilled `create_url` INSIDE the click (WebKit lets
  `window.open` through only while the click is handled, so the URL is fetched when the
  pointer reaches or focuses the button; a cold first press may need a second), then
  "Check token". Connected: account and expiry, Pause (strict boolean), Check now,
  Disconnect / Revoke now (behind `confirm()`), repositories with protection/CI chips and
  Enable, sandbox paths (Show in Finder only inside a folder granted in `/api/access`,
  through `/api/files/reveal`), the recommended ruleset with Copy, other GitHub access
  from `GET /api/github/ambient`, waiting changes, recent pushes and drift alerts with
  "That was me". **Token custody in the page:** the token is read out of its password
  field when Check token is pressed, the field is emptied in the same task, and the
  value lives only in that call's local variable until the POST leaves — never in module
  state, a DOM attribute, a log line, localStorage or a re-render; a repaint the owner
  did not cause never wipes a focused field. Only `https://github.com/…` URLs (no
  userinfo) reach `window.open` (`ghUrl`), because the app shell hands `_blank` to
  NSWorkspace, which opens ANY scheme. **(b) The review sheet `#gh-review`** (full
  viewport, `role=dialog`, inherits the palette): every diff line through `esc()`
  (the first 3,000 lines, then "Show the remaining"), every check, the PR text exactly
  as it will be sent. Diff lines WRAP; a blank run of 40 characters or 80
  columns (the cards' rule, above) is drawn as
  ⟦N spaces⟧, 4+ stacked combining marks as ⟦N combining marks⟧ and a bare CR
  as ⟦U+000D⟧ (E02, E61). Hidden characters are shown as `⟦U+XXXX⟧` by a JS mirror of
  `_GHC_HIDDEN_RE`, the title input's 120-character cap mirrors `GHC_TITLE_MAX`
  and `BODY_MAX` mirrors `GHC_BODY_MAX` (an edited description over it is
  refused in the sheet, and a 422 from decide is shown with the edits kept —
  E64) — **change both or neither**. **The sheet approves only what it showed
  (E02):** (1) Approve stays off until every diff line is shown (past
  `DIFF_LINES` = 3,000 the owner must press "Show the remaining"); (2) a file
  counts as SHOWN only when the diff draws it — a text section, or a binary
  section with its WHOLE text preview (never a preview cut short, empty, or a
  header two files share); (3) on top of the server's acks, the sheet asks its
  own ticks keyed `ui:` — `ui:warn:<i>` for a warn check with no ack string,
  `ui:binary` for files not shown that no warn/block `C-binary` names,
  `ui:cut` when the server cut the diff at 1 MB — which are NEVER sent (the
  server checks only its own acks). No header reads "nothing blocks" or "the
  whole change" while anything is not shown. `gate()`: Approve ("Push and open draft PR" / "Push
  branch only") stays disabled until no block check remains, every warn ack is ticked,
  the public-repo confirm is given and there is a title. After the push the
  sheet says "draft" only when GitHub really opened a draft (E62). Decide is a plain same-origin
  `fetch` POST `{id, digest, decision, acks, confirm_public?, title?/body? only when
  edited}` with `credentials: "same-origin"`, so the browser itself attaches the `Origin`
  + `Sec-Fetch-Site` the speed bump wants; every POST adds `X-Hermes-Nonce` when
  `window.hermesNonce` exists (the F05 hand-off). A 403 `owner_only` is explained, never
  retried; Esc and Close never decide; a retract asks `confirm()` first; opening the
  sheet closes `#wpop`/the Widget Center so the overlay watcher cannot pull focus.
  **(c) The toast and the deep link:** `GET /api/github/status?scan=0` at most every
  60 s, only while the page is visible and GitHub is connected. The first read of a page
  life only SEEDS the pending ids (no toast for what already waited); a later NEW id
  raises "Hermes has a GitHub change for you" once — never for the change open in the
  sheet — and `#toast` takes a click only while that line is up (it is
  `pointer-events:none` otherwise) and goes inert the MOMENT the line is
  replaced by another toast or hides (a MutationObserver; E63). `#github-review=<ghc-id>` opens the sheet on load
  and on hashchange, and closing consumes it with `history.replaceState`. **(d) The
  onboarding step `github`**, registered through the extra-steps hook
  (`registerStep` when present, else `window.hermesOnboardingSteps`); its wire() makes
  only its own `?scan=0` status read, and Skip falls back to the hook's own Skip
  button, since the hook has no `ctx.next()`. Motion: `HA()` `enter('fade')` /
  `exit('fadeOut')` only.
  Tests: `t_gh_connect*.py` against `_gh_fake.py` (loopback; never real GitHub),
  `t_gh_sandbox.py`, `t_gh_changes*.py`, `t_gh_gate.py`, `t_gh_needsyou.py`,
  `t_gh_ui.js` (the UI on index.html's real helpers), and `browser/t_gh_review.py`
  (hermetic Playwright: the real index.html with every `/api/**` from fixtures; it SKIPs
  without Python Playwright). The real-GitHub acceptance run is the owner's (deploy
  checklist).

- **Personal context MCP server (1.1.4)** — `dashboard/hermes_mcp.py`, a
  stdio **Model Context Protocol** server (JSON-RPC 2.0, newline-delimited, no
  embedded newlines — `json.dumps` guarantees that, which is why every write
  goes through `_send()`). Ships §4 item 3 / §4b of
  `docs/plans/purpose-and-direction.md`: shape 3, "Hermes becomes
  infrastructure". Install is one line the owner runs — `claude mcp add
  hermes-assistant -- python3 <repo>/dashboard/hermes_mcp.py`; nothing in the
  repo touches `~/.claude.json`. `initialize` echoes a known
  `protocolVersion` (`2025-11-25`/`2025-06-18`/`2025-03-26`/`2024-11-05`; never
  the initialize-less 2026-07-28) and otherwise answers with the latest;
  annotations/outputSchema follow the negotiated revision. It declares
  `capabilities.tools {listChanged:false}` and `serverInfo {name:"hermes-assistant", version:<VERSION>}`; `tools/list`
  is one page (no `nextCursor`), `tools/call` returns
  `content:[{type:"text",text}]` + `isError`, `ping` returns `{}`, an unknown
  method is `-32601`, an unknown **or disabled** tool is `-32602` (indistinguishable
  on purpose), a malformed line is `-32700` and the loop continues — a line
  nested too deep for the decoder included; one incoming line is capped at
  `MAX_LINE` = 1 MiB (the newline not counted), read in bounded chunks and
  answered `-32600` "Request too large", never held whole (E81).
  **It is a PROXY, never a second reader.** No sqlite, no icalBuddy, no
  `chats/*.json`, no `USER.md` — every tool is a `urllib` GET against
  `http://127.0.0.1:7788`, so the dashboard stays the one place that knows the
  store invariants (`PREWARM_SESSION` reserved, FTS query sanitisation, absent
  store ⇒ degrade not raise). A second reader would drift the first time
  either side changed. **It sends NO `Origin` header** — the same-origin guard
  refuses a *present* cross-origin Origin and allows requests carrying none
  (verified live: `Origin: http://evil.example` → 403, no Origin → 200), and
  its `Host` (`127.0.0.1:7788`) is in `ALLOWED_HOSTS`. `HERMES_MCP_DASHBOARD`
  overrides the base URL (that is how the down-case is tested), loopback
  `http://` only, through an empty `ProxyHandler` and no redirects (C55). A
  refused connection becomes `isError:true` with the `launchctl kickstart` line, and
  the server stays alive.
  **Nine tools, one per source (§4b: never one blob tool)** — `hermes_search`
  → `/api/search`; `calendar_next(hours=24)` → `/api/expand?id=today`
  (`expand_today`'s `eventsToday+7`, parsed back into datetimes, all-day
  events kept, in-progress ones marked, 60-min lookback, ≤168 h);
  `calendar_search`/`notes_search`/`messages_search` → `/api/search?source=
  calendar|note|message`; `chats_search` → `/api/sessions/search`;
  `chat_get` → `/api/history?session=`; `needs_you` → `/api/needsyou`;
  `memory_get` → `/api/capabilities`.`memory.facts`. **No new dashboard route
  was needed** — the calendar and memory reads both already existed, so the
  MCP server has no aux module of its own (`dashboard/aux_context.py` exists,
  but it is the context-meter/compaction module, unrelated — E151). `files_search` is **omitted, not stubbed**:
  granted folders have `/api/access` and a `recent_files()` widget provider but
  **no search route**, and inventing one would have meant a dashboard restart.
  **Static launch-time allowlist** `~/.hermes/mcp-allow.json` (0600 from birth
  via `os.open(..., O_EXCL, 0o600)`, created on first run), read ONCE at
  import: scope is the owner's decision at launch, never negotiated at runtime
  by a model. A disabled tool is **not listed**, so it never enters the model's
  context as an option; unknown keys in the file are ignored, and a corrupt
  file falls back to the defaults (messages **off**) rather than to "allow
  everything". **Only a literal JSON `true` enables a tool or write** (`"false"`
  used to enable it, B06). `messages_search:false` also **filters `message` rows
  (and their counts) out of `hermes_search` and `needs_you`** — otherwise
  switching the tool off would hide the tool and leak the content through the
  general one.
  **Output discipline:** ≤ 8 KB per result (`_bound`, cut on a codepoint
  boundary); `_plain()` runs every upstream string through control-character
  stripping, an `&lt;`-escape of `<script`/`<iframe`/`<html`-style openers and
  `_redact()` — the WHOLE aux_convos.py scrubber between the `MIRRORED SECRET
  BLOCK` markers, **byte-identical, copied not imported** (the unit tier
  compares the bytes; since B21 it also catches JSON-quoted keys, `*_KEY=`
  names, Basic auth and prose passwords — some code identifiers get redacted
  too, deliberately — and since the 2026-11-09 audit E82 the unlabeled shapes
  `ya29.`, `1//0`, `GOCSPX-` (Google OAuth), `hf_`, `sk_`/`rk_` live+test
  (Stripe), `npm_`, `glpat-`, `passcode:`/`passphrase:` and the `user:password`
  of a `scheme://user:password@host` URL; like the older `ghp_`/`sk-` shapes
  they miss a token glued to a word character or in upper case). aux_memlayer's
  `_ml_looks_secret` (the memory-fact check, also what the Claude bridge and
  For-You use) runs the same block's prose rule too, so "my password is …" is
  never stored as a fact (E24); it fails closed. Stored third-party fields go through `untrusted.line()`
  under `NOTICE` (C46). **F21:** every tool carries annotations;
  `hermes_search`, `calendar_next` and `needs_you` declare an `outputSchema` and
  every success returns `structuredContent` from the same capped strings —
  **ONE bound (E83):** `_fit()` picks how many items a list result shows so that
  BOTH the text and the structured object fit `MAX_TEXT` (8 KB;
  `MAX_STRUCT = MAX_TEXT`) and both carry exactly those items — with a
  `notice` (never on an error) — change schema and builder together (`t_f21`).
  And a dashboard body that is not JSON is **never forwarded**, which is the only way
  an HTML error page could have reached a model. `chat_get` keeps only
  `role in (user,bot,assistant)` rows with no `tool`/`tool_name`/`approval`/
  `status`/`kind` key — the same two constants aux_convos uses — and never
  reads the chat payload's `serve_sid`/`serve_key` at all.
  **stdout is the protocol channel**: all logging is stderr, one line per call
  (`hermes-mcp tool=<name> ms=<n> ok=<0|1>`).
  Read-only in practice too: `GET /api/needsyou?mark=0` (1.1.4; actually honoured
  only since C57) skips the sighting bookkeeping and the MCP `needs_you()` uses
  it, so only the Hub/pop-out (real eyes) feed `now_precision`; `needs_you`
  prints the dashboard's `source_notes` as a caveat over a blind source.
  **+ seven harness tools (1.2.4)** — `doctor` (`/api/doctor`, plus
  `format:"text"` through the one `text/plain` front door `dash_get_text`
  accepts), `prompt_budget`, `context_recent`, `tool_budget`, `memory_facts`
  (`/api/memory/facts` + `/api/memory/preview`), `evals` and `trace_summary`
  (`days` convenience window, ≤31). Reads stay GETs. **Four of them take ONE
  write argument**, each gated by its own key in a new `writes` block in
  mcp-allow.json — `prompt_budget` (profile), `tool_budget` (max_chars/enabled/
  spill), `memory_facts` (add), `evals` (run) — **all default false**, including
  in an allowlist written before they existed. Rule 4 now applies to arguments
  too: with the key off the argument is **absent from the tool's schema** AND
  refused by the handler (a client may send an off-schema key), and the refusal
  names `writes.<entry>`. **The upstream flags that would restart a service or
  wake a model are never forwarded** — prompt-budget `restart`, tool-budget
  `install`/`restart`, evals `force` — whatever the caller sends; each payload
  is rebuilt key by key. `memory_facts(add=)` re-runs aux_memlayer's own
  `_ml_looks_secret` test (the mirrored block again) BEFORE the round trip, so a
  key never reaches the store's process. `HERMES_MCP_ALLOW` overrides the
  allowlist path — that is how the harness tests a different scope without
  touching the owner's file. Harness: `tools/tests/live/t_mcp.py` (the live
  tier, E150). `needs_you` reads GET `/api/needsyou?mark=0`, which lists
  `never` rows only for an explicit `?never=1`, so the MCP answer carries
  their count, never their previews.
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
./install-services.sh                 # (re)install the four launchd agents; starts dashboard + serve (the two model lanes start on demand)
./update.sh --dry-run                 # what an update would do (stable channel)
cat VERSION; curl -s localhost:7788/api/version   # what's running
launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard   # restart dashboard
tail -f ~/.hermes/logs/{dashboard,serve,gateway}.log        # logs
hermes gateway status                 # Telegram gateway health
hermes -z "hi"                        # test the agent directly
curl -s localhost:8080/v1/models      # is the model up
tools/tests/run.sh                    # unit tests (CI runs exactly this)
tools/tests/run.sh live|browser|all   # the tiers that need the dashboard
node tools/tests/browser/t_anim_real.js   # real Motion/GSAP/anime in headless Chromium (no dashboard)
node tools/tests/browser/t_anim_hub_real.js; node tools/tests/browser/t_anim_surfaces_real.js
node tools/tests/browser/t_onboarding_real.js   # same; all four SKIP without a Chromium
tools/vendor-anim.sh --check          # vendored libraries still match MANIFEST.json
```
**Unit tier rules (C81–C85):** Python and Node refuse all non-loopback egress
plus :7788/8080/8081/9119 (a fresh-process self-check proves it), and Python
also refuses the installed `shortcuts`, `osascript` and `icalBuddy` (the
owner's Shortcuts, Reminders, calendar — a suite uses a stub of that name in a
scratch folder; `t_s3_unit_owner_tools.py`); `SKIP
<reason>` is counted, never credited, and CI fails any skip not in ci.yml's
`HERMES_TESTS_EXPECTED_SKIPS`; `TESTS 0 passed` or no summary line is a FAIL.
Suites use the REAL server.py helpers (`lib/server_real.py`) and real `esc()`,
never copies; UI suites run real JS on a small DOM (`unit/_a11y_dom.js`,
`_k14b_dom.js`). CI also parses index.html's inline `<script>`, typechecks the
Swift app and runs Python 3.12/3.13/3.14. The model-wake guard counts only REAL
launcher processes (a python running mlx-vlm-launch.py — not its `--local-ready`
probe — or `-m mlx_lm server`, as pid@start-time pairs parsed from `ps`; R2-61,
E153) and trips in either direction. Each suite has a time limit
(`HERMES_TESTS_SUITE_TIMEOUT`, default 600 s, 0 disables; the suite runs in its
own process group and the whole group is killed — a child started with
`start_new_session` escapes it) and a hung suite fails by name; CI jobs have
timeouts too (E137). The tier also sets `GIT_ALLOW_PROTOCOL=file`; a child
started with a stripped environment (`python -I`, gh_git.py's own git env) is
NOT covered by the egress guard (E138).

## Hard-won gotchas (do not re-learn these)
- **transformers must stay <5**: mlx-lm 0.31.x *claims* it needs
  transformers>=5 but 5.x crashes it (`AutoTokenizer.register` AttributeError).
  `pip install 'transformers<5'`. Ignore pip's dependency warning.
- `mlx_lm.server` is not on PATH (user-site install); the fallback is `python3
  mlx-vlm-launch.py --mlx-lm <args>` — never a bare `python3 -m mlx_lm server`
  (it serves any request-named model).
- Hermes real config keys: `model.default`, `model.context_length`,
  `approvals.mode`. Use `hermes config set`, don't hand-copy config files.
- `hermes serve` needs neither `web_dist` nor `--skip-build` any more (C117); a
  hand-run `hermes serve` without `HERMES_WEB_DIST` still serves the
  token-bearing web UI when a built dist exists.
- **`hermes -z` is fail-OPEN (C36):** oneshot.py sets `HERMES_YOLO_MODE=1` and
  every non-hardline command is auto-approved outside the interactive CLI and
  gateways — `approvals.mode: manual` changes nothing — and `-z` ignores
  `--continue`. The tool list is the only control: `run_agent(...,
  toolsets=[...])` passes an explicit `--toolsets`; an empty list becomes
  `UNATTENDED_NO_TOOLS` (`clarify`, answered in-process). Never pass
  `toolsets=None` for unattended work.
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
- Google Workspace — `aux_google.py` is READ-ONLY by scope
  (gmail/calendar/contacts `.readonly`, C51; a wider grant is revoked or flagged
  `scope_excess`); never wire send/compose — user's explicit safety posture,
  enforced by absence of the scope (`GOOG_FORBIDDEN`). A dead refresh token
  shows `needs_reauth` (C21). Connecting needs OAuth Desktop JSON from Google
  Cloud Console (browser required).
- Discord (bot token + channel IDs from dev portal; browser required).
- Phase 2 (iMessage/Teams/Slack/Notion) only after user asks.
- The hub approval UI (`/api/chat/approve` + inline buttons) is wired but has
  not yet been exercised by a real dangerous-command approval — verify the
  first time one fires (B01: once, for THAT command; deploy checklist §4).
- macOS TCC: if the agent can't read ~/Documents/~/Desktop when running under
  launchd, grant python3 (or the service) Full Disk Access in System Settings.
