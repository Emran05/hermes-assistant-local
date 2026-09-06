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
4. **Voice, on demand.** Mic in Quick Ask and chat via a local STT sidecar (parakeet on
   MLX or whisper.cpp), started on first press and exited after idle; "read aloud" via a
   small TTS sidecar. Same on-demand discipline as the model lanes; battery measured.
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
- Time-to-first-answer stays warm (prewarm holds it under ~2 s after wake).
- Context reach: number of sources answerable in one question (today: 1 — chats).
- Battery: on-demand sidecars leave no resident process after idle.

## 6. Sources

Filled in as the sweeps land: personal-context layers / local MCP servers; attention
triage patterns; on-device STT/TTS on Apple Silicon (September 2026).
