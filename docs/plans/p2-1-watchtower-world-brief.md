# Watchtower Triggers + 8am World Brief — design spec (P2)

Workstream P2.1. Two linked features in **one aux module** (`dashboard/aux_watchtower.py`
+ `dashboard/aux_watchtower.js`):

1. **World Brief** — 8:00am daily push to Telegram DM + the dashboard Briefing widget,
   composed from *cached* widget data + one local-model synthesis pass.
2. **Watchtower** — a notify-only proactive trigger engine: user-defined watch rules that,
   when they fire, send a Telegram notification. NEVER auto-acts.

Both ride the always-on `com.hermes.dashboard` launchd service and the data the hub already
warms. All names below were grepped against the live tree (paths/functions are real).

---

## Goal & acceptance criteria

Done means, concretely and testably:

1. **Brief fires at 8am.** A background thread in the dashboard process composes and delivers
   the World Brief once per local day at ~08:00; a restart or a laptop asleep at 8am still
   delivers on first wake after 8am (catch-up), and never double-delivers the same day
   (`last_brief_date` guard). Verify: set the clock guard to "now" via `POST /api/brief/send
   {"dry_run":true}` returns the exact text; a `last_brief_date` in `watchtower-state.json`
   flips after a real send.
2. **Brief content matches brief-spec.md order:** (a) your day, (b) world & tech front page,
   (c) market movers, (d) underground signal, (e) look-ahead — composed from cached data, 12-hour
   times, zero emoji, scannable. Verify: `GET /api/brief/preview` returns 5 structured sections;
   the rendered text has those five headers and no emoji (`grep -P '[\x{1F300}-\x{1FAFF}]'` empty).
3. **One synthesis pass, no tools, degrades safely.** Composition builds a deterministic
   structured brief first (always works), then runs exactly one MLX `/v1/chat/completions` call
   for editorial glue; if the model is offline/paused or the output fails the sanity gate, the
   deterministic brief is delivered. Verify: pause the agent (`/api/agent/pause`), run
   `/api/brief/send {"dry_run":true}` → still returns a full brief flagged `"synthesized":false`.
4. **Briefing widget shows the World Brief** with no duplicate generator: the existing
   `/api/briefing` endpoint, `WIDGETS["briefing"]`, `BRIEFING_FILE`, and `briefing_loop()` are
   reused by *rebinding* `_generate_briefing`/`_briefing_payload` in globals (the exec-include
   pattern), not by adding a parallel path. Verify: `GET /api/briefing` returns the world brief.
5. **Three Watchtower trigger types fire on live cached data:** `ticker_move`, `system_metric`,
   `rss_keyword`. Verify with `POST /api/watchtower {"op":"test_rule",...}` dry-runs returning
   `would_fire` + signature + rendered text without sending.
6. **Every firing is logged** with trigger, context, delivery target, suppression reason, and a
   mutable user reaction (`watchtower-log.jsonl`). Verify: after a real/dry fire, the tail of the
   log has the row; `GET /api/watchtower` surfaces per-rule fired/acted/dismissed counts.
7. **No spam:** per-rule cooldown, signature dedupe, quiet-hours suppression, and a global daily
   cap all enforced; a rule that stays true doesn't re-notify until cooldown *and* a materially
   new signature. Verify: fire a rule twice within cooldown → second row is `suppressed:"cooldown"`.
8. **Notify-only boundary holds:** Watchtower never calls a tool, never touches the approval path,
   and the rule schema *refuses* any rule carrying an action beyond notify. A rule cannot redirect
   Telegram to an arbitrary chat_id (home channel only). Verify: `POST add_rule` with an `action`
   key → 400; with a `chat_id` → key ignored.

---

## Data model (exact shapes)

All files live under `DATA = ~/.hermes/dashboard` (a server.py global), mode 0600, atomic
temp+fsync+rename (mirror `permissions._atomic_write`). Written under `_state_lock`.

### `watchtower.json` — rules + config
```json
{
  "version": 1,
  "quiet_hours": {"start": "22:00", "end": "07:00"},
  "daily_cap": 20,
  "brief": {"enabled": true, "hour": 8, "minute": 0, "channels": ["telegram", "hub"]},
  "rules": [
    {
      "id": "wt-1720000000-a1b2",
      "type": "ticker_move",
      "enabled": true,
      "label": "NVDA moves >5%",
      "params": {"symbol": "NVDA", "threshold_pct": 5.0, "direction": "any"},
      "cooldown_min": 120,
      "channels": ["telegram", "hub"],
      "created_at": 1720000000.0,
      "updated_at": 1720000000.0
    }
  ]
}
```
Per-type `params` (validated + clamped at write time):
- `ticker_move`   `{symbol:"NVDA", threshold_pct:0.1–100, direction:"any|up|down"}` — reads the
  markets cache (`expand_markets()` → `watchlist`/`indices`, each item `{symbol,price,pct,...}`).
- `index_move`    same shape, evaluated against `indices` (SPY/QQQ/DIA/IWM).
- `crypto_move`   `{coin:"bitcoin", threshold_pct, direction}` — reads `w_crypto()` (`coins:[{id,price,pct}]`).
- `system_metric` `{metric:"ram_pct|cpu_pct|disk_pct|battery_pct", op:">"|"<", value:0–100}` —
  reads `_sys_sample()` (`{cpu_pct,ram_pct,...}`) + `expand_battery()` (`{pct,charging,...}`) + a `df` call for disk.
- `rss_keyword`   `{keywords:["outage","acquires"], sections:["Tech","World","Business","Science"]}` —
  scans the news_desk cache (`expand_rss()` → `sections:[{name,items:[{title,url,source,ts}]}]`).
- `email_important` `{}` — schema present, evaluator **stubbed** (returns no-fire, reason
  `"gmail not connected"`) until P2.5 lands read-only Gmail; **read-only, never send**.
- `calendar_gap` `{min_gap_min:120}` / `agent_run_done` `{}` — schema present, evaluator stubbed
  until the underlying signal is confirmed live; documented, not shipped in v1's "three types."

`channels` ∈ `{"telegram","hub"}` only. Any `action`/`chat_id`/`target`/`command` key on a rule is
**rejected** (notify-only invariant enforced structurally, not by convention).

### `watchtower-state.json` — dedupe/cooldown/cap state
```json
{
  "version": 1,
  "last_brief_date": "2026-07-05",
  "day": {"date": "2026-07-05", "sent": 3},
  "fires": {
    "wt-1720000000-a1b2": {"last_fired": 1720100000.0, "last_signature": "NVDA:-5.4:down", "count": 2}
  }
}
```

### `watchtower-log.jsonl` — append-only fire log (the precision-guardrail data)
One JSON object per line (1 MB single-generation rotation, like `permissions._log_append`):
```json
{"ts":1720100000.0,"rule_id":"wt-...","type":"ticker_move","label":"NVDA moves >5%",
 "signature":"NVDA:-5.4:down","context":{"symbol":"NVDA","pct":-5.4,"price":118.2},
 "channels":["telegram","hub"],"delivered":["telegram","hub"],
 "suppressed":"","reaction":""}
```
`suppressed` ∈ `""|"cooldown"|"dedupe"|"quiet_hours"|"daily_cap"|"disabled"|"deliver_failed"`.
`reaction` ∈ `""|"useful"|"noise"` (set later from the Mind card; drives precision).

### Reused: `BRIEFING_FILE = DATA/briefing.json`
The composer writes the same file the widget already reads, extending its shape:
```json
{"ok":true,"reply":"<rendered brief markdown>","generated_at":1720100000.0,
 "kind":"world_brief","synthesized":true,
 "sections":{"day":{...},"world":{...},"markets":{...},"underground":{...},"lookahead":{...}}}
```
`_briefing_payload()` (rebound) returns `reply`/`generated_at`/`generating` as before, so the
existing Briefing widget and pop-out render unchanged.

---

## Backend

New module **`dashboard/aux_watchtower.py`** — exec'd into server.py globals by the aux loader
(server.py:2071-2083). It sorts **after** `aux_trust.py`, so it loads last and may safely rebind
`_generate_briefing`/`_briefing_payload` (same technique `expanders_extra.py` uses to override
`console_activity`/`expand_markets`). It imports its own stdlib deps (`os,sys,json,time,re,threading,
subprocess,urllib.request,datetime,socket`) and defines only new names (`_wt_*`, `WT_*`, `_brief_*`).
Uses server globals: `HOME HERE DATA read_json write_json _state_lock _widget_cache _cached
model_online agent_paused get_settings get_tasks weather macos_calendar w_crypto w_hackernews
w_github EXPANDERS ACTIVE_MODEL_FILE DEFAULT_MODEL MODEL_URL HERMES register_get register_post`.

Handlers take one `ctx` (`ctx.q1("k","def")` GET, `ctx.body` POST), return a `dict` (→200) or
`(dict, status)` tuple.

### Endpoints

**`GET /api/brief/preview`** → `{ok, sections:{day,world,markets,underground,lookahead}, asof,
markets_state, synthesized:false}`. Builds the *deterministic* structured brief from cache; never
runs the model, never sends. Errors: `{ok:true, sections:{...}, degraded:[...]}` listing any section
that had no data (never 500 — a dead feed degrades one section).

**`POST /api/brief/send`** body `{dry_run:bool}` →
`{ok, text, synthesized, delivered:["telegram"], dry_run}`. `dry_run:true` composes (incl. the one
synthesis pass) and returns the exact text **without** sending — the primary no-spam test path.
`dry_run:false` composes + delivers via `hermes send` + writes `BRIEFING_FILE`; does **not** flip
`last_brief_date` (that's the scheduler's job) so manual sends don't cancel the 8am push.
Errors: `{ok:false,error:"send_failed",detail}` if the subprocess exits non-zero (still writes the widget).

**`GET /api/watchtower`** → `{ok, quiet_hours, daily_cap, brief, rules:[...],
stats:{<rule_id>:{fired,useful,noise,precision,last_fired}}, recent:[<last 20 log rows>]}`.
Drives the Mind management card.

**`POST /api/watchtower`** body `{op,...}` — ops:
- `add_rule {rule}` → validate/clamp → append → `{ok, rule}`; `400 {ok:false,error}` on bad type,
  bad params, or a forbidden `action`/`chat_id` key.
- `update_rule {id, patch}` / `toggle_rule {id, enabled}` / `delete_rule {id}` → `{ok}` / `404`.
- `set_quiet_hours {start,end}` / `set_brief {enabled,hour,minute,channels}` / `set_daily_cap {n}` → `{ok}`.
- `mark_reaction {ts|rule_id, reaction:"useful|noise"}` → updates the log row → `{ok}` (feeds precision).
- `mute_rule {id}` → convenience for `toggle_rule enabled:false` + logs a `noise` reaction → `{ok}`.
- `test_rule {rule}` → **dry-run evaluate now** against live cache → `{ok, would_fire, signature,
  context, text}`; never sends, never mutates state. The safe way to author a rule.

**`GET /api/watchtower/feed`** → `{ok, fires:[{ts,label,type,text,rule_id} ...最近]}` — the
lightweight source for the Hub notification lane (last ~15 delivered, non-suppressed fires).

Validation floors (write-time clamps, all reject rather than silently accept out-of-range):
`threshold_pct` 0.1–100, `cooldown_min` 5–1440, `daily_cap` 1–200, `value` 0–100,
`keywords` ≤10 items ≤40 chars each, `symbol`/`coin` `^[A-Za-z.\-]{1,12}$`, `label` ≤80 chars,
`quiet_hours` `HH:MM` 24h. `rules` capped at 40.

### Background thread — `watchtower_loop()`

One daemon thread, started at module load guarded by a global flag (exact pattern of
`aux_recorder`'s `_recorder_thread_started`):
```python
if not globals().get("_watchtower_thread_started"):
    globals()["_watchtower_thread_started"] = True
    threading.Thread(target=watchtower_loop, daemon=True).start()
```
Loop body (sleep 60s between passes):
1. **Brief tick** — if `brief.enabled` and local `now` ≥ today 08:00 and `last_brief_date != today`:
   prime the slow expander caches (call `expand_rss`, `EXPANDERS["markets"]`, `w_github` once so the
   8am compose hits warm data), compose + deliver the brief, set `last_brief_date = today`. This is
   the catch-up-on-wake behavior (fires on first pass after 8am if not yet sent).
2. **Rule pass** — for each enabled rule: evaluate against the *cached* provider (all providers are
   `_cached()`, so this rides the 45s `hub_prewarm_loop` warmth — **zero extra network** beyond it) →
   `(fire, signature, context)` → gate through quiet-hours / cooldown / signature-dedupe / daily-cap →
   on pass, deliver + append log + update fire state; on suppress, append log with `suppressed=reason`.

Rebinding at module load (the "refactor, don't duplicate" step):
```python
globals()["_generate_briefing"] = _wt_generate_briefing   # world-brief composer
globals()["_briefing_payload"]  = _wt_briefing_payload    # unchanged shape, reads BRIEFING_FILE
```
so the pre-existing `briefing_loop()` (server.py:1047) keeps the widget fresh with the world brief
every `BRIEFING_REFRESH_MIN` minutes, and `/api/briefing` (server.py:2198) needs no edit.

### Delivery

- **Telegram:** `subprocess.run([HERMES,"send","--to","telegram","--quiet",text], timeout=20)`.
  Verified subcommand: `hermes send` pipes text to the configured platform, **no LLM, no agent
  loop**, reusing the gateway's home-channel creds (`~/.hermes/.env`), which are locked to user
  <YOUR_TELEGRAM_USER_ID>. We pass no chat_id → cannot be redirected. We never read the bot token. Telegram's
  4096-char limit is enforced by trimming the rendered brief/notification.
- **Hub lane:** the fire is appended to the log; `GET /api/watchtower/feed` + the aux JS render it.

### How it respects `permissions.py`

Watchtower is **notify-only**, so the enforcement seam is *avoided by construction*: it never emits
an `approval.request`, never calls `decide()`, never runs a tool. The spec's forward-compat note:
any future rule that would *act* must route the action through the hub chat serve path
(`hermes_rpc.run_turn` → `approval.request` → `permissions.decide()` → `approval.respond`), exactly
where P1.3 already enforces tiers. v1 refuses `action` at write time so that path can't be reached
accidentally. The synthesis pass uses a plain chat-completion (no tools), so it produces nothing to
approve and cannot fake tool success (the FINDINGS.md `-z`/manual-approval trap does not apply).

---

## Frontend

New file **`dashboard/aux_watchtower.js`**, auto-served at `/aux_watchtower.js`. Loaded **after**
`/expand.js` and the other aux JS so it can wrap the existing hooks. **The ONE index.html edit
(applied by the orchestrator)** is a single script tag after server.py's aux JS block (index.html:2055):
```html
<script src="/aux_watchtower.js"></script>
```

Two surfaces, both reusing global helpers (`esc`, `animate` (Motion One), `revealStagger`, `REDUCE`),
all `typeof`-guarded like `aux_trust.js`, zero emoji (bespoke SVG), 12-hour time:

1. **Mind view — "Watchtower" card** (primary). Chain `window.mindExtras` (the exact pattern of
   `aux_trust.js`:18-22): `var prev=window.mindExtras; window.mindExtras=async()=>{await prev?.();
   await watchtowerPanel();}`. The card renders into `#view-mind`:
   - rule list with per-rule enable toggle, label, type glyph, cooldown, and a precision meter
     (`acted ÷ fired`, from `stats`);
   - an "Add watch" composer (type picker → type-specific param fields → live `test_rule` preview
     showing "would fire now / rendered text");
   - quiet-hours + daily-cap + 8am-brief controls;
   - a recent-fires list with one-tap **Useful / Noise** (→ `mark_reaction`) and **Mute** (→ `mute_rule`).
   States: loading skeleton, empty ("No watches yet"), error banner (fetch failed), untrusted-free
   (no policy coupling). Animations: `revealStagger` on the rule rows, a subtle pulse on a fresh fire.

2. **Hub — signal lane** (lightweight). A small "signal" indicator in the hub chrome polling
   `GET /api/watchtower/feed`; clicking opens a glass popover listing recent fires (each links out).
   Optional for v1; the Mind card is the source of truth. It does **not** re-render the widget grid.

The Briefing **widget/pop-out is unchanged** — it already reads `/api/briefing`, which now returns
the world brief. `expand.js`'s briefing renderer needs no edit (markdown body).

---

## Integration points (all verified by grep)

Touched/reused, with real names:
- `dashboard/server.py`: `_generate_briefing` (1027), `_briefing_payload` (1744), `briefing_loop`
  (1047), `BRIEFING_FILE` (997), `BRIEFING_REFRESH_MIN` (56), `/api/briefing` (2198),
  `/api/briefing/refresh` (2244), `WIDGETS["briefing"]` (1732), `register_get`/`register_post`
  (2043/2047), `RouteCtx.q1` (2060), aux loader (2071-2083), aux JS serving (2126-2128),
  `_cached` (1169), `_widget_cache` (1166), `_state_lock` (62), `model_online` (938),
  `agent_paused` (1843), `MODEL_URL` (54), `HERMES`, `ACTIVE_MODEL_FILE` (1836), `DEFAULT_MODEL`
  (1837), `get_settings` (1158), `get_tasks` (1142), `weather` (1208), `macos_calendar` (1249),
  `w_crypto` (1528), `w_hackernews` (1441), `w_github` (1544), `hub_prewarm_loop` (1779),
  `read_json`/`write_json`, `DATA` (46).
- `dashboard/expanders_extra.py`: `expand_markets` (6, → `EXPANDERS["markets"]`, keys
  `indices`/`watchlist` each `{symbol,price,pct,...}`), `expand_rss` (1199, news_desk →
  `sections:[{name,items:[{title,url,source,ts}]}]`), `expand_battery` (71, `{pct,charging,...}`),
  `_sys_sample` (1269, `{cpu_pct,ram_pct,...}`), `EXPANDERS` dict.
- `dashboard/index.html`: aux JS block (2050-2055) — the single added `<script>` tag.
- `dashboard/aux_trust.js`: the `window.mindExtras` wrap pattern to copy.
- `~/.local/bin/hermes send --to telegram` (verified `--help`): delivery, bot-token path, no LLM.
- Settings: `starred_tickers`/`tickers` in `settings.json` (server.py:2330) — `ticker_move` defaults
  its symbol list from these; a rule's explicit `symbol` overrides.

New files only: `dashboard/aux_watchtower.py`, `dashboard/aux_watchtower.js`,
`~/.hermes/dashboard/watchtower.json`, `watchtower-state.json`, `watchtower-log.jsonl`. **No edit to
server.py's dispatch chain** (routes registered via the aux loader); the sole index.html change is one line.

---

## Edge cases & failure modes (exhaustive)

- **Model offline / agent paused at 8am** → skip synthesis; deliver the deterministic brief
  (`synthesized:false`). Never block or skip the send on a dead model.
- **Laptop asleep at 8:00** → `last_brief_date` guard + "≥ today 08:00" condition deliver on first
  wake pass; never double-send (guard flips on success only).
- **Restart at 07:59–08:01** → guard is date-based, so at most one send/day regardless of restarts.
- **Markets closed / weekend** → read `state` from `expand_markets` (`REGULAR|CLOSED|PRE|POST`);
  label movers "at last close · <date>", never present stale as live (CLAUDE.md markets rule).
- **A ticker rule references a symbol not in the watchlist** → evaluator fetches that one symbol via
  a cached single-quote helper; if unreachable, no-fire with `context.error`, logged, not spammed.
- **All news feeds down** → `expand_rss` returns `{error}`; world/underground sections show
  "no fresh headlines"; brief still sends. `rss_keyword` rules no-fire (no data ≠ fire).
- **Telegram send fails** (gateway down, exit≠0) → log `suppressed:"deliver_failed"`, still write the
  widget; retry once on the next pass (bounded by cooldown so no storm).
- **Rule stays true across passes** (ticker parked at −6%) → signature dedupe + cooldown: re-notify
  only after `cooldown_min` **and** a materially different signature (rounded pct bucket).
- **Quiet hours wrap midnight** (22:00→07:00) → handled by "start>end means overnight window."
  Overnight fires are suppressed but **surfaced in tomorrow's brief section (a)** as "overnight flags"
  (read from the fire log since 22:00) — the elegant tie between the two features.
- **Daily cap hit** → further fires `suppressed:"daily_cap"` and logged; cap resets on date change.
- **DST / travel** → all time logic uses `time.localtime`; the 8am guard is on local date+hour.
- **Corrupt `watchtower.json`** → `read_json` default `{version:1,rules:[]}`; a bad file never
  crashes the loop (all evaluators wrapped, never raise into the loop, mirror `recorder_loop`).
- **Telegram 4096-char limit** → brief/notification trimmed with an ellipsis; the full brief stays in
  the widget.
- **Concurrent writes** (two POSTs) → all mutations under `_state_lock` with read-modify-write.
- **Synthesis returns meta/garbage** → `_brief_is_sane()` (reuse `_briefing_is_sane` logic:
  no role tags, has section headers) → fall back to deterministic.

---

## Security & safety (upholds every invariant)

- **NOTIFY-ONLY (the core proactive boundary):** Watchtower emits *text notifications only*. It never
  calls a tool, never emits an approval request, never routes through `permissions.decide()`. The rule
  schema *structurally refuses* any `action`/`command`/`chat_id`/`target` key (400 at write time), so
  a rule cannot be authored to act or to redirect delivery. Forward path for acting is documented as
  Phase-3-only via the hub serve/approval seam.
- **Telegram locked to the one user:** delivery uses `hermes send --to telegram` with **no chat_id**
  → the gateway's home channel (user <YOUR_TELEGRAM_USER_ID>). A rule cannot supply a chat_id. We never read or
  handle the bot token (it stays in `~/.hermes/.env` 600; `hermes send` reads it, not us).
- **Gmail read+draft only, never send:** `email_important` is a *read-only* evaluator, stubbed until
  P2.5; there is no code path that sends or drafts from Watchtower. SMTP is never wired.
- **`approvals.mode: manual` untouched;** the synthesis pass is a tool-free chat completion → nothing
  to approve, and it cannot fake tool success (the `-z`/manual trap in FINDINGS.md is avoided by not
  using tools at all).
- **Local-first:** the loop reads only already-cached local providers (zero extra network); the only
  egress is the Telegram send the user configured. No secrets pass through the module.
- **File safety:** atomic 0600 writes under `~/.hermes/dashboard`; log rotates at 1 MB; params
  validated/clamped to prevent abuse or a runaway loop.
- **Anti-annoyance (the named Watchtower risk):** quiet hours default-on (22:00–07:00), per-rule
  cooldown, signature dedupe, global daily cap, one-tap mute, and per-rule precision visible in Mind —
  the DEVPLAN §3 precision guardrail, from day one.
- **What it refuses:** rules with an action; delivery to an arbitrary chat; sending when the schema is
  malformed; any synthesis output that fails the sanity gate; more than `daily_cap` notifications/day.

---

## Test plan (no spam, no --yolo)

All dry-run first; a single real send only to confirm the wire once.

1. **Deterministic brief, no model, no send:**
   `curl -s localhost:7788/api/brief/preview | jq '.sections|keys'` → `["day","lookahead","markets",
   "underground","world"]`. `grep -P '[\x{1F300}-\x{1FAFF}]'` on the output → empty (zero emoji).
2. **Full compose without sending:**
   `curl -s -XPOST localhost:7788/api/brief/send -d '{"dry_run":true}' | jq '{synthesized,len:(.text|length)}'`
   → returns text; `synthesized:true` when model up.
3. **Degrade path:** `curl -XPOST localhost:7788/api/agent/pause` then repeat (2) → `synthesized:false`,
   text still present. Resume after.
4. **Rule authoring dry-run (no send, no mutate):**
   `curl -s -XPOST localhost:7788/api/watchtower -d '{"op":"test_rule","rule":{"type":"system_metric",
   "params":{"metric":"ram_pct","op":">","value":1}}}' | jq '{would_fire,signature,text}'` →
   `would_fire:true` (RAM is always >1%), rendered text, nothing sent.
5. **Dedupe/cooldown/quiet-hours are unit-testable offline:** monkeypatch the cached provider to a
   fixed value and call the evaluator + gate twice → assert second is `suppressed:"cooldown"`; set
   `quiet_hours` to wrap "now" → assert `suppressed:"quiet_hours"`. (Run in a throwaway python that
   `exec`s the module against a temp DATA dir; no Telegram.)
6. **Log + precision:** after a dry fire, `tail -1 ~/.hermes/dashboard/watchtower-log.jsonl` has the
   row; `POST mark_reaction {reaction:"useful"}`; `GET /api/watchtower | jq '.stats'` shows precision.
7. **One real wire check (once):** `hermes send --to telegram --quiet "watchtower test"` → arrives on
   the phone; then `POST /api/brief/send {"dry_run":false}` once to confirm the brief renders on
   Telegram. Do **not** loop this.
8. **Scheduler without waiting for 8am:** temporarily `set_brief {hour:<current+0>, minute:<now+1>}`
   in a dev DATA dir, watch the loop deliver once and flip `last_brief_date`; revert. Never point the
   dev instance at the real Telegram home channel more than once.
9. **No --yolo, ever:** synthesis is tool-free by design, so no approval gate is disabled.

---

## Effort & sequencing + dependencies + open questions

**Sequencing (single module, ~4 build sessions):**
- S1 — Brief composer: deterministic 5-section builder from cache → `_wt_render()`; one MLX
  `/v1/chat/completions` synthesis pass (`_brief_synthesize`) with sanity gate + deterministic
  fallback; rebind `_generate_briefing`/`_briefing_payload`; `/api/brief/preview` + `/api/brief/send`.
- S2 — Scheduler thread: `watchtower_loop` brief-tick with date guard + catch-up + expander pre-warm;
  Telegram delivery helper.
- S3 — Watchtower core: rule store + validators; the three live evaluators (`ticker_move`,
  `system_metric`, `rss_keyword`) + stubs; gates (quiet/cooldown/dedupe/cap); fire log; state.
- S4 — Endpoints: `GET/POST /api/watchtower`, `/api/watchtower/feed`; wire the loop's rule pass.
- S5 — Frontend `aux_watchtower.js`: Mind card (CRUD, test-preview, precision, reactions, mute) +
  optional hub lane; the one index.html `<script>` tag.

**Dependencies (all live except noted):** Telegram gateway (LIVE, `hermes send` verified); hub
prewarm cache + providers (LIVE); MLX model at `127.0.0.1:8080` (LIVE); `permissions.py` (respected by
the notify-only boundary); `com.hermes.dashboard` always-on service (LIVE — no new launchd plist).
Gated: `email_important`/`calendar_gap`/`agent_run_done` evaluators light up when P2.5 Gmail and a
confirmed calendar/agent-run signal land (schema ships now, evaluators stubbed).

**Scheduler decision (justified):** an **in-process module-load time-check thread** wins over a
launchd `StartCalendarInterval` timer and over `hermes cron`. Decisive reason: **the cached widget
data the brief must compose from lives in the dashboard process's memory** (`_widget_cache`, warmed by
`hub_prewarm_loop`). A launchd/cron process is a *separate* process — it can't read that cache and
would re-fetch the network at 8am, violating brief-spec's "no extra network at 8am." The in-process
thread also matches the four existing precedents (`briefing_loop`, `recorder_loop`, `hub_prewarm_loop`,
`system_sampler_loop`), needs no install step, and is restarted for free by the service's KeepAlive.
`hermes cron` additionally runs `hermes -z` (tool-capable, slower, subject to the manual-approval
trap) — wrong tool for a tool-free synthesis. Precision cost (a 60s poll vs. an exact cron minute) is
irrelevant for a "before I open the laptop" brief and is what enables catch-up-on-wake.

**Open questions:**
1. Should the Briefing widget show the world brief all day, or revert to a lighter personal briefing
   after N hours? (Spec assumes world brief all day, refreshed by `briefing_loop`.)
2. Hub signal lane in v1, or Mind-card-only? (Spec ships Mind card as source of truth, lane optional.)
3. If fire volume grows, migrate `watchtower-log.jsonl` → a small sqlite like `recorder.db`? (JSONL is
   fine at expected volume; revisit if a rule set gets chatty.)
4. Exact timezone behavior while traveling — pin the brief to the Mac's local tz (current assumption)
   or a user-set home tz?
