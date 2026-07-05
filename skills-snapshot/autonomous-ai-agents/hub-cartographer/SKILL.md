---
name: hub-cartographer
description: "Curl atlas for the local dashboard on 127.0.0.1:7788 — read your own recorder/metrics/memory/intel, write only through gated routes"
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [dashboard, loopback, api, recorder, memory, watchtower, metrics, intel]
    related_skills: [hermes-agent]
---

# Hub Cartographer

The dashboard at `http://127.0.0.1:7788` is your own nervous system: your flight
recorder, your latency vitals, your memory files, your trigger rules, your intel
store. Every endpoint below was verified against the dashboard source
(`server.py` + `aux_*.py`) — these are the real routes and real response shapes,
not remembered ones. All calls are plain `curl` to loopback. No auth headers, no
API keys, nothing ever leaves the machine.

Base URL for everything: `http://127.0.0.1:7788`

Helper: `scripts/hubctl.sh` wraps the fiddly bits (etag round-trips, JSON
assembly). Prefer it for memory writes.

## 1. THE MAP

| Endpoint | Method | Gate | What you get / do |
|---|---|---|---|
| `/api/health` | GET | free | `{model_online, hermes_found, hermes_path}` — is anyone home |
| `/api/metrics?days=N` | GET | free | Your vitals: `turns.ttft_ms{p50,p90,p95}`, `turns.turn_ms`, `est_tok_per_sec`, `ram.last{gb,state}` (via macOS `footprint`), `approvals`, `targets`. N clamped 1–30 |
| `/api/mind_drill?days=N` | GET | free | Usage analytics: `tokens_by_day`, `sessions_by_day`, `model_mix`, `skill_usage`, `busiest_day`, `totals` |
| `/api/recorder?limit=N` | GET | free | Your flight log, newest first: `actions[]`, `counts{total,reversible,undone}`. Params: `limit` (max 200), `before=<id>`, `kind=write,shell,...`, `q=<substring>`, `id=<n>` for one action w/ diff |
| `/api/undo` | POST | whitelist-bound | Body `{"id": N}` (+`"force":true` for partial). Refuses anything whose kind is not in `{write, shell}` or marked irreversible — it is a whitelist machine, not a judgment call |
| `/api/memory/list` | GET | free | `{dir, files[], trash[], limits}` — every memory .md with size/mtime/etag, plus soft-deleted trash |
| `/api/memory/file?name=X.md` | GET | free | `{content, etag, kind, core, last_writer}`. Core files (USER.md, MEMORY.md) also return `entries[]`, `char_used`, `char_limit` |
| `/api/memory/create` | POST | name/limit checks | `{"name":"notes.md","content":"..."}` → 409 `exists` if taken. Core names can't be created |
| `/api/memory/save` | POST | **etag-gated** | `{"name":..., "base_etag":..., "content":...}` (freeform) or `{"name":..., "base_etag":..., "entries":[...]}` (core). Stale etag → conflict response carrying the *current* content+etag. Snapshots taken before write |
| `/api/memory/delete` | POST | soft-delete | `{"name":"x.md"}` → moved to trash (restorable). USER.md/MEMORY.md refused with 403 `core_file` |
| `/api/memory/restore` | POST | trash-name check | `{"trash_name":"x.md.1234567890.md"}` (names come from `/api/memory/list` → `trash`) |
| `/api/watchtower` | GET | free | Full config: `rules[]`, `quiet_hours`, `daily_cap`, `brief`, `midday`, `breaking`, `stats`, `recent`, `live_types`, `stub_types` |
| `/api/watchtower` | POST | op-validated | `{"op": "..."}` where op ∈ `add_rule, update_rule, toggle_rule, delete_rule, mute_rule, test_rule, set_quiet_hours, set_daily_cap, set_brief, set_midday, set_breaking, mark_reaction`. Notify-only — rules can ping, never act |
| `/api/watchtower/feed` | GET | free | Recent non-suppressed rule fires: `{fires:[{ts,label,type,text,rule_id}]}` |
| `/api/watchtower/breaking` | GET | free | Breaking-news detector preview |
| `/api/brief/preview` | GET | free | The morning brief as data: `{sections, asof, markets_state, synthesized, degraded}` |
| `/api/intel` | GET | free | Hourly AI/social research store: `{updated, count, curated[], items[≤40], feeds, web_search_available}`. `?gather=1` pokes a background refresh |
| `/api/shortcuts` | GET | free | Installed vs exposed macOS Shortcuts, `pending` tickets, `recent` runs, `policy` |
| `/api/shortcuts/run` | POST | **approval-ticket** | `{"name":"Shortcut Name","input":"..."}` → `{needs_approval:true, ticket:...}`. NOTHING runs now. The user approves in the dashboard; you cannot redeem your own ticket. Not-exposed shortcuts are 403 auto-denied |
| `/api/clip/transform` | POST | structurally tool-free | `{"action":"summarize","text":"..."}` → `{result, model, ms}`. Actions: `summarize, explain, translate (opts:{"to":lang}), rewrite, extract, proofread` — see `/api/clip/actions`. Direct local-model call, no tools field exists |
| `/api/config/snapshot` | GET | free | Current config snapshot as JSON |
| `/api/config/export` | POST | free (writes file) | `{"note":"why"}` → writes a sanitized config snapshot to disk |
| `/api/messages` | GET | free read | Recent iMessage conversations IF the helper app has Full Disk Access. Without it you get a graceful `{available:false, reason:...}` — report that text, don't retry |
| `/api/chat` | POST | approval surface | `{"message":..., "session":"<id>"}` → `{job}`. This spawns a full agent turn — the hub chat itself |
| `/api/chat/poll?job=ID` | GET | free | `{state, text, status, approval, done, reply}` — includes pending approval prompts |
| `/api/chat/approve` | POST | user-only | `{"job":..., "choice":"approve"|"deny"}` — for the human in the dashboard, not for you |

Not for you: `/api/messages/ingest` (token-guarded, helper-app only),
`/api/models/*`, `/api/agent/pause|resume`, `/api/settings` (user-owned config).
Knowing they exist is the point — routing around their gates is not.

## 2. READ RECIPES

Pipe everything through `python3 -m json.tool` so you read structure, not soup.

```bash
# vitals — TTFT p50/p95, RAM footprint, approval counts
curl -s http://127.0.0.1:7788/api/metrics | python3 -m json.tool

# flight recorder — your last 5 actions
curl -s 'http://127.0.0.1:7788/api/recorder?limit=5' | python3 -m json.tool

# one action in detail (with diff, if it was a write)
curl -s 'http://127.0.0.1:7788/api/recorder?id=42' | python3 -m json.tool

# search the recorder — did I touch that file?
curl -s 'http://127.0.0.1:7788/api/recorder?q=USER.md&limit=10' | python3 -m json.tool

# intel store — check BEFORE web_search on AI/social topics
curl -s http://127.0.0.1:7788/api/intel | python3 -m json.tool

# memory inventory, then one file (note the etag — you need it to write)
curl -s http://127.0.0.1:7788/api/memory/list | python3 -m json.tool
curl -s 'http://127.0.0.1:7788/api/memory/file?name=MEMORY.md' | python3 -m json.tool

# watchtower rules + recent fires
curl -s http://127.0.0.1:7788/api/watchtower | python3 -m json.tool
curl -s http://127.0.0.1:7788/api/watchtower/feed | python3 -m json.tool

# the brief, usage analytics, shortcuts inventory
curl -s http://127.0.0.1:7788/api/brief/preview | python3 -m json.tool
curl -s 'http://127.0.0.1:7788/api/mind_drill?days=7' | python3 -m json.tool
curl -s http://127.0.0.1:7788/api/shortcuts | python3 -m json.tool
```

Or with the helper:

```bash
H=~/.hermes/skills/autonomous-ai-agents/hub-cartographer/scripts/hubctl.sh
bash $H get /api/metrics
bash $H recorder-tail 5
bash $H memory-get MEMORY.md
```

## 3. WRITE DISCIPLINE (memory)

Never blind-write a memory file. The save route is etag-gated for a reason —
the user, the dashboard, and you can all touch these files.

The contract, always in this order:

1. **GET first**: `/api/memory/file?name=X.md` → note `etag` and `kind`.
2. **PUT with that etag**: POST `/api/memory/save` with `base_etag` set to what
   you just read. If someone wrote in between, you get a conflict response
   containing the *current* content and etag — merge and retry, never force.
3. **Core files (USER.md, MEMORY.md) take `entries`, not `content`.** Entries
   are `'\n§\n'`-joined server-side. An entry may never contain `§`. To append
   one fact: GET → take `entries[]` → append your string → save the whole list.
4. **Byte-identical rewrites**: if you are re-saving entries you did not change,
   pass them back *exactly* as returned — same strings, same order. The memory
   guard tracks writer identity by content hash; gratuitous reformatting looks
   like drift.

`hubctl.sh memory-put` does the etag round-trip for you:

```bash
# freeform file: new content on stdin
echo "project notes v2" | bash $H memory-put notes.md

# core file: entries on stdin, one per line separated by a lone § line
bash $H memory-put MEMORY.md <<'EOF'
User prefers 12-hour timestamps
§
Dashboard lives on 127.0.0.1:7788
EOF
```

Watchtower writes go through the single POST with `op`:

```bash
# always test_rule before add_rule — it dry-fires without saving
curl -s -X POST http://127.0.0.1:7788/api/watchtower \
  -H 'Content-Type: application/json' \
  -d '{"op":"test_rule","rule":{"type":"ticker_move","label":"NVDA 5%","params":{"symbol":"NVDA","pct":5},"channels":["hub"],"cooldown_min":60,"enabled":true}}' \
  | python3 -m json.tool
# then swap "test_rule" -> "add_rule" for the same body
```

Rule types that actually fire today: `ticker_move`, `index_move`, `crypto_move`,
`system_metric` (metrics: `ram_pct, cpu_pct, disk_pct, battery_pct`),
`rss_keyword`. Channels: `telegram`, `hub`. Everything is notify-only.

## 4. CHEAPEST SOURCE FIRST

Before any `web_search` on AI news, model releases, or social/tech chatter:

1. `/api/intel` — the hourly research store already gathered it. `curated[]` is
   the good stuff; `items[]` is the raw feed. Check `updated` for freshness.
2. Your memory files (`/api/memory/file?name=MEMORY.md`) — you may already know.
3. `/api/brief/preview` — markets and headlines are already synthesized there.

Only if all three come up dry (or stale for the question at hand) do you spend a
web search. Loopback JSON is milliseconds and free; a search is seconds and
noise.

## 5. GROUND TRUTH RULE

Before you tell the user "I did X" — especially for file writes, shell commands,
or anything that ran in an earlier turn — check the flight recorder:

```bash
curl -s 'http://127.0.0.1:7788/api/recorder?limit=10' | python3 -m json.tool
```

If the action is not in `actions[]`, you did not do it. Say so. The recorder is
the authoritative log of your own behavior; your memory of a turn is not.
Same rule for undo: `/api/recorder?id=N` first to see `reversible` and the diff,
then `/api/undo` — and accept its refusal as final (whitelist is `write` and
`shell` only, by design).

## Gotchas

- **Loopback only.** The dashboard binds 127.0.0.1. Never tunnel, proxy, or
  expose it — local-first is an invariant, not a preference.
- **JSON escaping in `curl -d`**: single-quote the whole payload. If the payload
  itself contains apostrophes or quotes, use a heredoc instead of inline `-d`:
  ```bash
  curl -s -X POST http://127.0.0.1:7788/api/clip/transform \
    -H 'Content-Type: application/json' --data-binary @- <<'EOF'
  {"action":"summarize","text":"it's got apostrophes, \"quotes\", the lot"}
  EOF
  ```
- **`/api/shortcuts/run` always needs a ticket.** The first POST only mints one
  (`needs_approval:true`). The user approves in the dashboard (Mind view →
  Shortcuts); tickets are single-use and expire in ~5 minutes. Never present a
  ticket as if the run happened.
- **`/api/messages` may be degraded.** If `available:false`, the `reason` field
  explains (usually Full Disk Access not granted). Relay that; don't loop.
- **Timestamps**: API responses carry epoch seconds. When you show a time to the
  user, render it 12-hour (e.g. 3:42 PM), matching the rest of the assistant.
- **`/api/chat` spawns a real agent turn** — treat it as expensive and gated,
  not a data read. Poll with `/api/chat/poll?job=...`; approvals surface there
  for the *user* to decide.
