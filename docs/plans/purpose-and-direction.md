# Purpose and direction (2026-09-05)

A step back from the backlog: what Hermes Assistant is *for*, what it is today, which
other shapes would serve the same purpose, and what that implies for the next line of
releases (1.1.x). Written after shipping 1.0.0–1.0.4; the research sweeps that inform
the roadmap section are cited at the end as they land.

## 1. The purpose, stated plainly

Hermes exists so that one person can **delegate to a capable agent without surrendering
their digital life to someone else's servers**. Everything else is instrumental:

- *Local inference* is not the point; it is how the data stays yours.
- *Always-on* is not the point; it is how the agent can notice things and be there before
  you ask.
- *The dashboard* is not the point; it is one window onto the agent's context, attention
  and actions.

So the product is a **sovereign personal agent**: it holds your context, watches what
matters, acts on your Mac under your rules, and gets better over time — and you can audit
and undo what it did.

## 2. What we actually built (honest inventory)

| Layer | What exists | Verdict |
|---|---|---|
| **Trust** | approval loop, permission tiers, Flight Recorder with undo, same-origin API guard, Claude escalation master switch, on-demand model servers, secret-scanned config export | Strongest layer. This is the part nothing mainstream offers. |
| **Surfaces** | dashboard (Hub · Agent · Settings), menu-bar Quick Ask (rebuilt), Telegram, clipboard actions, Shortcuts action bus | Good coverage; voice is the visible gap. |
| **Context** | Calendar + Gmail (read/draft), iMessage via the native helper, granted folders, chat history (now searchable), notes, `USER.md` memory, you-model | Each source is wired, but there is **no unified index** — the agent cannot search across mail, messages, notes and chats in one question, and nothing outside Hermes can use this context. |
| **Attention** | morning brief, midday pulse, evening wrap, watchtower news + breaking, For You, 20 hub widgets | Delivery exists; **triage does not**. The Hub is a grid of feeds (markets, HN, GitHub trending, world clock) — generic dashboard fare that dilutes the assistant. Nothing says "these three things need you now". |
| **Action** | agent tools, computer use, Shortcuts, clipboard transforms, cron (hidden) | Capable but ad hoc; routines are not first-class, and "what did you do while I was away" is only the recorder. |
| **Brains** | Qwen3.8-27B primary (MTP), 9B background lane, optional uncensored 27B, Claude escalation with auto-route | Sound. Efficiency is now measured (baseline docs). |
| **Distribution** | GitHub releases, self-updater, install bootstrap, CI | Works; unsigned app, private working repo → public distribution repo. |

Reading the table: the *moat* is trust; the *gaps* are context depth, attention triage,
routines, and voice. The widget grid is the thing most likely to be mistaken for the
product and least connected to the purpose.

## 3. Other shapes that fulfil the same purpose (abstraction ladder)

Each of these is a legitimate answer to "how else could we serve the purpose?". They are
not mutually exclusive; the question is which to make the *primary* shape.

1. **Assistant app (today).** A chat + widgets app that happens to be local. Familiar, but
   competes on the axis (chat UI) where cloud products are strongest.
2. **Attention router.** The primary surface is a single triage stream — everything inbound
   (mail, messages, calendar changes, alerts, agent results) classified locally into
   *now / today / never*, with one-tap actions (draft reply, snooze, done, delegate to the
   agent). Widgets become secondary. This is what "always-on" was for.
3. **Personal context server.** Hermes becomes infrastructure: a loopback MCP server that
   exposes calendar, mail, messages, notes, granted files, chat history and memory with
   per-source permission tiers — usable by Hermes's own agent *and* by other agents on the
   Mac (Claude Code, Codex, Shortcuts). The dashboard is then a client of Hermes, not the
   whole of it. This is the most defensible long-term shape: the trust layer travels with
   the context.
4. **Trusted agent runtime.** Generalize the trust layer (approvals, recorder, undo,
   permission tiers, guard) so *any* agent binary can run through it. Ambitious; the
   context server is the practical first step toward it.
5. **Routine engine.** Recurring, approved playbooks with dry-run and undo ("every morning
   collect X, draft Y, ask before sending"). Hermes-agent has cron and skills; the product
   would make them visible, editable and auditable.
6. **Fleet of specialists.** Replace "one 27B generalist" with on-demand small models per
   job — embeddings for search, STT/TTS for voice, a vision model for screenshots — managed
   by the same roster/on-demand/idle machinery. This is the efficiency direction as much
   as a feature direction.

Recommendation: keep shape 1 as the shell, and move the *center of gravity* to **2 + 3**,
with 6 as the enabling substrate and 5 as the follow-on. Shape 4 stays a north star.

## 4. What that changes concretely (the 1.1 line)

Ordered by leverage; each is a small release or two.

1. **"Needs you" inbox (attention router v1).** One stream on the Hub above the widgets:
   unread mail that looks like it wants a reply, messages mentioning you, the next
   calendar conflict, watchtower alerts that passed the master toggles, agent results
   awaiting review. Classification on the 9B lane with a fixed schema (`now|today|later|
   never`, one-line reason); actions: draft reply (agent), snooze, done, open. Ship with a
   precision counter so the user can see whether "now" earns trust. Widgets stay, below.
2. **Unified local index + search.** SQLite FTS5 over chats, notes, exported docs, mail
   subjects/snippets, messages — one `/api/search` and one search box; the agent gets the
   same as a tool. Embeddings later if FTS is not enough (a small MLX embedding model on
   the background lane; measured before adoption).
3. **Personal context MCP server (read-only first).** Loopback MCP exposing the sources
   above with the permission tiers Hermes already has; token-guarded like
   `/api/messages/ingest`. Lets Claude Code use Hermes's context; makes shape 3 real.
4. **Voice, on demand — parked.** See §4b: the microphone must be owned by the signed
   app, which is frozen; ships with the next deliberate app rebuild.
5. **Routines.** A Settings › Routines panel over hermes cron: see, edit, dry-run, run now,
   with recorder links; "turn this conversation into a routine".
6. **Hub re-centering.** Brief + Needs-you + agenda + "what I did" first; feeds second.
   No widget is removed; the default layout changes.
7. **Data & Network panel** (#16) and per-model details (#17): trust made visible.

Explicitly *not* pursued: multi-cloud provider grids, a vector-RAG stack before FTS proves
insufficient, persona marketplaces, cloud sync of anything (see the backlog's rejected
list).

## 5. How we will know it is working

- "Needs you" precision: of items marked *now*, the share acted on within the day (target
  ≥ 70% after two weeks; snoozes and dismissals count against it).
  **Source (shipped 1.1.2): `GET /api/needsyou/metrics` → `now_precision`**, computed by
  `_ny_metrics()` in `dashboard/aux_needsyou.py` over a rolling 7 days from the `shown`
  and `acts` rows in `~/.hermes/dashboard/needsyou.json`. Acting = done | open | draft
  within 24 h of the item first being *shown* as *now*; a snooze is counted separately as
  `now_snooze_rate` and does **not** count as acting, exactly as the target above states.
  A sighting is recorded when a payload is handed to a client, not when it is built, so
  items nobody ever saw never enter the denominator. Manual re-filing is `reclass_rate` —
  the drift signal §4b asks for. All three are on the Hub in one quiet line ("now
  precision 78% · 12 snoozed"), which is the point: the counter is visible to the person
  whose trust it is measuring.
- Time-to-first-answer stays warm (prewarm holds it under ~2 s after wake).
- Context reach: number of sources answerable in one question (today: 1 — chats).
- Battery: on-demand sidecars leave no resident process after idle.

## 4b. Concrete designs (from the three sweeps, 2026-09-05)

**Unified index (1.1.0).** SQLite FTS5 from the stdlib `sqlite3` — one table
`items(id, source, ts, title, body, ref, meta_json)` with an FTS5 external-content index
over `title, body`; sources: chats, notes, Message Center rows, calendar events (osascript
export), watchtower items, exported docs. Incremental indexing on write paths plus a
nightly sweep; `GET /api/search?q=&source=` returns ranked rows with BM25 and plain-text
snippets + match offsets (the client escapes and highlights — same contract as the chat
search). Vectors only if FTS proves insufficient: `sqlite-vec` (single loadable extension)
with Reciprocal Rank Fusion, embeddings from `nomic-embed-text-v2` (137M, MIT, 8k context)
on the background lane, or `all-MiniLM-L6-v2` on CPU — measured before adoption.

**Needs-you inbox (shipped 1.1.2 — 1.1.1 went to first-run onboarding).** One record per
inbound unit:
`{id, source: imessage|calendar|news|approval|reminder|email, sender, summary, received_at,
requires_reply, deadline_ts, sender_tier: vip|known|unknown|automated, bucket: now|today|
later|never, confidence, reason, suggested_action: reply_draft|snooze|delegate|done|none}`.
Rules a 9B model applies reliably, as a decision tree with 4-shot JSON output, not
free-form urgency: **now** = VIP sender (two-way history ≤30 days or contact flag) AND a
concrete time-bound ask (question/deadline ≤24 h, event <2 h, an approval waiting);
**today** = needs a reply but no hard deadline, or a thread you have replied in, or an
event later today; **never** = automated sender, no history, no deadline language →
digest only. Confidence <0.6 falls to *today*, never *now* (false urgency is the trust
killer every reviewed product is criticized for). Tag, never move or archive (reversible).
Rhythm: morning brief = full now+today with reasons; midday pulse = delta only; evening
wrap = what was deferred. Actions: Reply (agent-drafted, never auto-sent), Snooze,
Delegate (agent task), Done. Trust metrics: precision of *now*, snooze rate on *now*,
manual reclassification rate (drift → refresh the examples). Gmail is not connected on
this Mac (OAuth pending), so v1 runs on iMessage, calendar, watchtower, approvals,
reminders and degrades per available source.

**Personal context MCP server (1.1.3).** One tool per source, never one blob tool:
`calendar.search/next`, `messages.search`, `notes.search`, `files.search(folder)`,
`chats.search`, `memory.get`; write-capable tools (`mail.draft`) are separate tools with
their own flag. Scope is a static launch-time allowlist (which folders, chats, calendars),
never negotiated at runtime by the model; loopback only, token-guarded like
`/api/messages/ingest`; the same permission tiers the dashboard already enforces. Lesson
from Rewind → Limitless → Meta: local storage is not a privacy guarantee by itself; the
per-source access control has to live in the software.

**Voice (parked behind an owner decision).** The right stack is torch-free MLX:
`parakeet-mlx` (Parakeet TDT 0.6B v3, ~1 GB, ~60x real time on M3-class, streaming) for
STT and `mlx-audio` Kokoro-82M for TTS, each as an on-demand sidecar that exits after idle.
The blocker is TCC: microphone permission is attributed to the responsible process, and a
launchd-started Python is attributed to the interpreter binary (re-prompted or silently
denied after every Python upgrade). The mic must be captured by the signed app, which is
frozen and has no microphone entitlement or usage string today. Voice therefore ships only
with the next deliberate app rebuild (batched with other Swift changes; requires
re-granting Full Disk Access). Until then: no voice.

## 6. Sources

- Context layer / MCP (2026-09-05 sweep): Apple Intelligence App Intents and Foundation
  Models docs (WWDC26), Screenpipe docs and its incognito-capture bug write-up, Rewind →
  Limitless → Meta coverage (Dec 2025), Khoj, Reor, mem0/OpenMemory status, the apple-mcp
  family (EventKit/JXA/chat.db pattern), Raycast AI manual, Open WebUI RAG/memory docs, MCP
  security guidance (per-tool least privilege; the `.startsWith()` path-check flaw), MLX
  embedding benchmarks (nomic-embed-text-v2, bge-m3, mxbai), sqlite-vec + FTS5 hybrid
  write-ups (Alex Garcia, sqlite.ai).
- Attention triage (2026-09-05 sweep): Superhuman Auto Labels / Split Inbox and its 2025-26
  criticism, Shortwave bundles, Gmail's Gemini "suggested to-dos / catch up" split (Jan
  2026) and the sticky-importance complaint, Apple Mail categories + Priority (on-device),
  Notion Mail, Hey's Screener/Imbox/Feed/Paper Trail, Slack recaps (extractive), Teams
  Meeting Recap, Sunsama/Motion/Reclaim spectrum, Perplexity Email Assistant, arXiv
  2605.15680 (few-shot triage with 8B models), local zero-shot classification throughput.
- On-device speech (2026-09-05 sweep): parakeet-mlx, whisper.cpp / Lightning Whisper MLX
  (vendor-reported speeds), Apple SpeechAnalyzer (macOS 26, Swift-only), Moonshine and
  Kyutai (PyTorch-first), mlx-audio (Kokoro-82M, CSM, Dia, Orpheus), AVSpeechSynthesizer
  premium voices, Piper (fork now GPL-3.0), TCC responsible-process behaviour for
  launchd-spawned interpreters and the audio-input entitlement requirement.
