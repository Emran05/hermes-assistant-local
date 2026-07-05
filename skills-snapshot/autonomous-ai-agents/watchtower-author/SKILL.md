---
name: watchtower-author
description: "Turn any 'tell me when…' ask — or a research conclusion — into a tested, armed, ledgered notify-only Watchtower rule via the local hub API. The agent programs its own reflexes: parse the ask, dedupe, dry-run test, confirm in plain words, arm, and record a ledger entry so every standing alert stays legible and prunable."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [Watchtower, Alerts, Triggers, Notify, Reflexes, Hub, Dashboard, Automation, Monitoring]
    related_skills: [hub-cartographer, cron-conductor]
---

# Watchtower Author

Watchtower is the hub's trigger engine at `http://127.0.0.1:7788/api/watchtower`. Rules you arm are evaluated every ~60 seconds against cached widget data (quotes cache ~5 min). When a rule fires, it sends ONE Telegram message to the locked home channel and/or a row in the hub feed. That is ALL it can ever do.

**Notify-only, by construction.** A rule cannot run a command, call a tool, or reach any other person. The API refuses any rule containing the keys `action`, `command`, `chat_id`, or `target`. Never author a rule expecting it to *do* something, and refuse asks that need an action ("sell my shares if…", "restart the server when…") — offer the approval-gated alternative instead: "Watchtower can only notify. I can ping you the moment it happens and you approve the action, or I can set up a cron job that asks for approval."

All calls below are plain `curl` to loopback — no approval needed, safe in any session. If the `hub-cartographer` skill's `hubctl.sh` is installed you may use it; plain `curl` is always sufficient:

```bash
curl -s http://127.0.0.1:7788/api/watchtower                    # list everything
curl -s -X POST http://127.0.0.1:7788/api/watchtower \
     -H 'Content-Type: application/json' -d '{"op":"...", ...}' # mutate
```

**Probe trick:** an invalid op comes back as `{"ok": false, "error": "unknown op: xyz"}` — a cheap way to confirm the endpoint and dispatcher are alive. The full op list (verified from source) is:
`add_rule, update_rule, toggle_rule, delete_rule, set_quiet_hours, set_daily_cap, set_brief, set_midday, set_breaking, mark_reaction, mute_rule, test_rule`.
The GET response also tells you the truth about capability: `live_types` (can actually fire) vs `stub_types` (parked, never fire).

## 1. Rule types & schema (literal, from the engine source)

Every rule has this envelope. The server assigns `id`, `created_at`, `updated_at` — never send them on add:

| field | meaning | constraints |
|---|---|---|
| `type` | rule kind | one of the live types below |
| `label` | short human name, used verbatim in the notification | ≤80 chars; auto-generated if omitted |
| `params` | per-type condition | see below |
| `cooldown_min` | minimum minutes between fires of THIS rule | 5–1440, default 120 |
| `channels` | where a fire lands | list from `["telegram","hub"]`, default both |
| `enabled` | armed or muted | boolean, default true |

There is **no per-rule quiet-hours or threshold field at the top level** — thresholds live inside `params` (as `threshold_pct` / `value`), and quiet hours are GLOBAL (`set_quiet_hours`, default 22:00–07:00 = 10 PM–7 AM). There is no `tag`/`origin` field either — the rule schema whitelists keys and silently drops extras, so origin context lives in the ledger (section 4), never on the rule.

### ticker_move — any Yahoo Finance symbol (fetches off-watchlist quotes itself)

```json
{"op": "add_rule", "rule": {
  "type": "ticker_move",
  "label": "AMD swings hard",
  "params": {"symbol": "AMD", "threshold_pct": 8.0, "direction": "any"},
  "cooldown_min": 240,
  "channels": ["telegram", "hub"]
}}
```
`symbol`: letters/dot/dash, ≤12 chars, uppercased. `threshold_pct`: 0.1–100 (percent move vs previous close). `direction`: `"any"` | `"up"` | `"down"`.

### index_move — market-index proxies ONLY (evaluated against the cached indices strip: SPY, QQQ, DIA, IWM — no fetch for other symbols)

```json
{"op": "add_rule", "rule": {
  "type": "index_move",
  "label": "Broad market shock",
  "params": {"symbol": "SPY", "threshold_pct": 2.5, "direction": "down"},
  "cooldown_min": 240,
  "channels": ["telegram", "hub"]
}}
```
Same params as ticker_move. If the user says "the market" / "S&P" / "Nasdaq" / "the Dow", map to SPY / QQQ / DIA (IWM for small caps).

### crypto_move — CoinGecko ids, and ONLY coins the crypto widget tracks

```json
{"op": "add_rule", "rule": {
  "type": "crypto_move",
  "label": "Bitcoin 5% swing",
  "params": {"coin": "bitcoin", "threshold_pct": 5.0, "direction": "any"},
  "cooldown_min": 240,
  "channels": ["telegram", "hub"]
}}
```
`coin` is the lowercase CoinGecko id: `bitcoin`, `ethereum`, `solana` (the default tracked set). A coin outside the tracked set never fires ("no quote") — if the user wants another coin, first add it: `curl -s -X POST http://127.0.0.1:7788/api/settings -H 'Content-Type: application/json' -d '{"coins":["bitcoin","ethereum","solana","dogecoin"]}'` (send the FULL list; it replaces).

### system_metric — this Mac's own vitals

```json
{"op": "add_rule", "rule": {
  "type": "system_metric",
  "label": "Disk nearly full",
  "params": {"metric": "disk_pct", "op": ">", "value": 90},
  "cooldown_min": 720,
  "channels": ["telegram", "hub"]
}}
```
`metric`: `ram_pct` | `cpu_pct` | `disk_pct` | `battery_pct`. `op`: `">"` or `"<"`. `value`: 0–100. (`battery_pct` with `"<"` = low-battery warning.)

### rss_keyword — keyword hit in the News Desk headlines

```json
{"op": "add_rule", "rule": {
  "type": "rss_keyword",
  "label": "News: Anthropic",
  "params": {"keywords": ["anthropic", "claude"], "sections": ["Tech"]},
  "cooldown_min": 120,
  "channels": ["hub"]
}}
```
`keywords`: 1–10 strings, ≤40 chars each, case-insensitive substring match on title+summary. `sections`: optional filter, subset of `["Tech","World","Business","Science","Yours"]`; empty = all sections.

### Stub types — exist but NEVER fire (no data source wired)

`email_important`, `calendar_gap`, `agent_run_done`. Do not arm these as if they worked; tell the user the honest state ("that signal isn't wired yet") and offer a cron-based check instead.

### Global gates (apply to every rule)

- `quiet_hours` — default 22:00–07:00. No non-breaking alert fires inside the window; when speaking to the user always phrase it 12-hour ("10 PM to 7 AM"). Change: `{"op":"set_quiet_hours","start":"22:00","end":"07:00"}` (API wants 24-hour HH:MM).
- `daily_cap` — max fires per day across all rules (default 20). `{"op":"set_daily_cap","n":20}`.
- Dedupe — a rule won't re-fire on the same signature (e.g. same ~1% price bucket, same headline).
- Rules cap: 40 total.
- Suppressed fires still land in the log with a reason: `cooldown`, `dedupe`, `quiet_hours`, `daily_cap`, `disabled`, `deliver_failed`.

Other ops you'll use: `{"op":"update_rule","id":"wt-...","patch":{"cooldown_min":240}}` (patchable: label, enabled, cooldown_min, channels, params) · `{"op":"toggle_rule","id":"...","enabled":false}` · `{"op":"delete_rule","id":"..."}` · `{"op":"mute_rule","id":"..."}` (disables AND marks the last fire as noise) · `{"op":"mark_reaction","rule_id":"...","reaction":"useful"}`.

## 2. Parse the ask — NL phrase → rule

Calibration anchors mirror the built-in Breaking-alerts config: index shock ≥2.5%, single-ticker shock ≥8%, news needs 2-source corroboration, 90-min cooldown, cap 5/day. Don't set user rules dramatically twitchier than these without saying so.

| The user says | Build |
|---|---|
| "tell me if NVDA jumps/drops" (no number) | `ticker_move`, threshold_pct **8** (single-name shock convention), direction from the verb ("jumps"=up, "drops"=down, "moves"=any) |
| "if the market tanks" / "S&P down 2%" | `index_move` SPY, threshold_pct 2.5 (or their number), direction down |
| "BTC moves 5% either way" | `crypto_move` bitcoin, threshold_pct 5, direction any |
| "max once per 4 hours" / "don't spam me" | `cooldown_min: 240` (no phrase → keep default 120; "don't spam" → 240+) |
| "not overnight" / "not while I sleep" | already covered by global quiet hours 10 PM–7 AM — confirm, don't change, unless they name different hours (then `set_quiet_hours`) |
| "when there's news about X" | `rss_keyword` with X + obvious variants as keywords |
| "if my disk / memory / battery…" | `system_metric` with the matching metric; disk>90, ram>90, battery<20 are sane defaults |
| "keep an eye on X for me" (from research you just did) | rule typed by subject; threshold per conventions above; ledger origin = the memory file / brief that motivated it |
| "let me know eventually / in the morning brief" | same rule but `channels: ["hub"]` (section 5) |
| anything requiring an ACTION on fire | **refuse the trigger-action, offer notify + approval-gated follow-up** |

## 3. The arming ritual (never skip a step)

1. **List first, dedupe.** `curl -s http://127.0.0.1:7788/api/watchtower` — read `rules[]`. If an existing rule covers the same subject, offer to `update_rule` (tighten threshold/cooldown) instead of adding a near-duplicate.
2. **Construct the payload** per section 1. Omit `id`. Keep `label` short and human — it is the first line of the notification.
3. **Dry-run with `test_rule` BEFORE arming.** `{"op":"test_rule","rule":{...same rule object...}}`. This is a pure dry-run — verified in source: it never sends anything and never saves anything, so it is always safe to run. Read the response: `would_fire` tells you if the condition is true right now; `context` proves the data source resolves (a `"no quote"` / `"no reading"` / `"no headlines"` error means the rule would silently never fire — fix the symbol/coin/metric before arming, never arm a rule whose test shows a data error); `live: false` means you picked a stub type — stop.
4. **Confirm the exact firing condition back to the user in plain words**, including cooldown and quiet hours, e.g.: "I'll ping you on Telegram if AMD moves more than 8% in a session — checked about once a minute against 5-minute-delayed quotes — at most once per 4 hours, and never between 10 PM and 7 AM. Sound right?" Wait for a yes if you're in a conversation; if you're acting on a standing instruction, state it and proceed.
5. **Arm it.** `{"op":"add_rule","rule":{...}}` — the response returns the saved rule with its `id` (`wt-...`). Keep that id.
6. **Ledger it** (section 4) so the alert carries its context forever.

## 4. The ledger — every reflex stays legible

The ledger is a memory file the hub's memory API manages: `watchtower-ledger.md` in `~/.hermes/memories/`. One `§`-separated entry per rule: id → plain-English meaning → origin.

Create once (409 "exists" is fine — means it's already there):
```bash
curl -s -X POST http://127.0.0.1:7788/api/memory/create \
  -H 'Content-Type: application/json' \
  -d '{"name":"watchtower-ledger.md","content":"# Watchtower ledger — rule id → meaning → origin\n"}'
```

Append an entry (etag-safe read-modify-write; the file is freeform, so send full `content`):
```bash
# 1. read current content + etag
curl -s 'http://127.0.0.1:7788/api/memory/file?name=watchtower-ledger.md'
# 2. save: old content + "\n§\n" + new entry, with base_etag from step 1
curl -s -X POST http://127.0.0.1:7788/api/memory/save \
  -H 'Content-Type: application/json' \
  -d '{"name":"watchtower-ledger.md","base_etag":"<etag from step 1>","content":"<old content>\n§\nwt-1751234567-ab12 — ping if AMD moves ≥8% any direction, cooldown 4h — origin: user ask 2026-07-05 (research note: hermes-setup-state.md) — armed 2026-07-05"}'
```
On a 409 `conflict`, re-read and retry with the fresh etag. Entry format: `<rule-id> — <plain-English firing condition + cooldown> — origin: <the ask, or the memory file / brief that motivated it> — armed <date>`. When you delete a rule, don't delete its ledger line — append ` — RETIRED <date> (<why>)` so history survives.

**"What alerts do I have?"** — answer instantly from the ledger (read the file), then cross-check `rules[]` from the GET for enabled/disabled state and `stats` (fired/useful/noise per rule). Never make the user read JSON.

## 5. Brief-vs-ping routing

Not everything deserves a phone buzz. Route by urgency:

- **Instant ping**: `channels: ["telegram","hub"]` — money moving, system about to fall over, time-critical news.
- **Morning-brief material**: `channels: ["hub"]` — the fire lands in the Watchtower feed (`/api/watchtower/feed`) and recent fires surface as "Overnight flags" in the 8 AM World Brief's "Your day" section; no Telegram message at all. Use for slow-burn watches ("keep an eye on", "let me know eventually", research follow-ups).
- Preview what the brief would say right now: `curl -s http://127.0.0.1:7788/api/brief/preview` (GET, read-only). Never POST `/api/brief/send` without `"dry_run": true` unless the user explicitly asks for a brief right now.

## 6. Hygiene — monthly audit

Keep the reflex set pruned. Schedule a monthly audit with your cron tool (or the `cron-conductor` skill if installed): schedule `0 9 1 * *` (9:00 AM on the 1st), prompt along these lines:

> Load the watchtower-author skill. GET /api/watchtower and read ~/.hermes/memories/watchtower-ledger.md (via /api/memory/file). Reconcile: (a) rules with no ledger entry → add one from the rule's label; (b) ledger entries whose rule id no longer exists → mark RETIRED; (c) rules whose stats show fired>3 with reaction noise, or zero fires in 60+ days → propose pruning. Send me ONE Telegram summary with your proposals. Do not delete anything without my approval.

The audit only reads and messages — deletions go through the user.

## Gotchas

- **Notify-only is structural.** `action`/`command`/`chat_id`/`target` keys are rejected by the API. Don't try to smuggle intent into a label; refuse action-triggering asks and offer the approval-gated alternative.
- **`test_rule` never sends** (dry-run in source) — run it freely. What DOES send: `add_rule` once the live loop finds the condition true, and `/api/brief/send` without `dry_run`. Treat those with care.
- **Extra rule keys are silently dropped** — don't invent fields like `quiet_hours` or `origin` on a rule; they won't persist. Global quiet hours + the ledger cover both.
- **`index_move` only sees SPY/QQQ/DIA/IWM**; any other symbol belongs in `ticker_move`. `crypto_move` only sees tracked coins — test_rule's `"no quote"` is your tell.
- **Quiet hours to the user in 12-hour words, to the API in 24-hour HH:MM.**
- **Always dedupe before add** — 40-rule cap, and two rules on the same subject double-ping.
- Telegram delivery goes only to the single locked home channel; a rule can never message anyone else.
