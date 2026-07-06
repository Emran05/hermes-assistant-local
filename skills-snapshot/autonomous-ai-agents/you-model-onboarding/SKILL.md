---
name: you-model-onboarding
description: "Run the ~10-minute adaptive You-Model onboarding interview: seed priors from what you already know (USER.md, recent sessions, this conversation), ask only the highest-uncertainty questions about goals / current work / looking-for / interests / interruption preferences / key people, and after EVERY answer propose the memory write and wait for an explicit yes before saving through the gated hub memory API."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [You-Model, Onboarding, Interview, Memory, Goals, People, Personalization, Proactive]
    related_skills: [hub-cartographer, hermes-agent]
---

# You-Model Onboarding

The You-Model is what lets Hermes stop answering "what is happening?" and start answering
"what is happening **that matters to you**." It lives as plain markdown the user owns and can
edit or delete from the dashboard (Mind → Your Model), in `~/.hermes/memories/`:

| File | Holds |
|---|---|
| `GOALS.md` | explicit objectives + a time horizon |
| `NOW.md` | what they're actively working on this week/month |
| `LOOKING-FOR.md` | open loops: people to meet, roles to hire, things to buy, questions to answer |
| `INTERESTS.md` | topics they care about (weight + as-of date) |
| `PREFERENCES.md` | tone, when interrupting is OK, what counts as noise |
| `people/<slug>.md` | one card per important person: relationship, what they care about, open threads |

Entries inside each file are separated by a line containing only `§`. You never touch these
files directly — every write goes through the hub API below, which snapshots, records and
lock-guards each change.

## The one iron rule (safety)

**Never write memory without an explicit yes, in this conversation, for that specific write.**
The loop is always: user answers → you propose the exact entry text and its target file →
user confirms ("yes" / "save it" / an edit) → only then you write → you confirm it saved.
If the user says no, edits, or ignores the proposal — do not write, adapt, move on.
Never rewrite or replace a whole file during onboarding; you only append entries and create
people cards. No secrets, passwords or keys ever go in memory.

## Step 0 — Seed priors FIRST (read-only, before asking anything)

Do not open with a blank questionnaire. Spend the first ~30 seconds reading what already
exists, so you ask only the highest-uncertainty questions and *confirm* instead of *ask*
where you can ("I have you in the NYC area — still right?").

```bash
bash scripts/seed_priors.sh          # one-shot dump of everything below, read-only
```

Or individually:

```bash
curl -s http://127.0.0.1:7788/api/youmodel                 # what exists + what's already filled
cat ~/.hermes/memories/USER.md                              # the semantic core (identity, hard prefs)
sqlite3 "file:$HOME/.hermes/state.db?mode=ro" \
  "SELECT source, title FROM sessions WHERE title IS NOT NULL AND archived=0 \
   ORDER BY started_at DESC LIMIT 15;"                      # what they've been asking about lately
```

Also mine **the current conversation** — anything the user already told you counts as a prior.
Once Gmail / Calendar / Messages are connected, their signals join this step; today they are
not live, so USER.md + session history + the conversation are your priors. Anything the
youmodel API already shows as filled: skip that question entirely or just confirm it.

If the curl fails, the dashboard hub is down — say so and stop; do NOT fall back to editing
memory files directly (that would bypass the snapshot/recorder safety path).

## Step 1 — Open, and scaffold once

Frame it warmly and honestly, e.g.:

> "I'd like to spend about ten minutes learning what to watch for on your behalf — goals,
> what you're building right now, who matters. Everything I save, I'll show you first, and
> it all stays on this Mac where you can edit or delete it. Ready?"

When they agree, create any missing scaffolding (this never overwrites existing files):

```bash
curl -s -X POST http://127.0.0.1:7788/api/youmodel/seed
```

## Step 2 — The interview: ~8–12 questions, adaptive, one at a time

One question per message. Short, warm, conversational — never a form. Follow up on rich
answers, skip areas the priors already cover, and stop around the 10-minute mark even if
areas remain (you can say "we can fill the rest any time from the Your Model card").
Priority order when time is short: NOW → GOALS → LOOKING-FOR → people → interests → preferences.

Pick from (adapt the wording to the person):

- **Goals + horizon** — "If the next six months go really well, what happened?" ·
  "Which of those is the one you'd protect if you had to drop the rest?"
- **Now** — "What are you actually building or working on *this* week?" ·
  "What's the current blocker on it?"
- **Looking for** (each answer = a standing subscription you'll scan the world for) —
  "Is there anyone you're trying to meet, hire, or get an intro to?" ·
  "Anything you're shopping for or a question you keep meaning to research?"
- **Interests** — "Which 2–3 topics do you always read when they show up?" ·
  "Anything you used to follow that I should treat as stale?"
- **Preferences / interruptions** — "When is it genuinely OK for me to ping you — and what
  should never trigger a ping?" · "What counts as noise to you?"
- **People** — "Who are the handful of people that matter most to what you're doing right
  now — and what does each of them care about?"

## Step 3 — Propose → confirm → write (after EVERY answer)

Propose the entry in plain sight, e.g.:

> "I'd save to **GOALS.md**: `Ship the Hermes dashboard publicly — by October 2026 (as of
> Jul 2026)`. Save it?"

Entry style: one fact per entry, a single short paragraph at most, stamped with an as-of
date (`(as of Jul 2026)`) so stale facts can decay. On yes, write it:

```bash
# append one entry to a typed file (creates the file with its template if missing)
curl -s -X POST http://127.0.0.1:7788/api/youmodel/add \
  -H 'Content-Type: application/json' \
  -d '{"file":"GOALS.md","text":"Ship the Hermes dashboard publicly — by October 2026 (as of Jul 2026)"}'
```

`file` must be one of `GOALS.md` / `NOW.md` / `LOOKING-FOR.md` / `INTERESTS.md` /
`PREFERENCES.md` — or a `people/<slug>.md` card. For a **new person card** (slug =
lowercase-dashes):

```bash
curl -s -X POST http://127.0.0.1:7788/api/memory/create \
  -H 'Content-Type: application/json' \
  -d '{"name":"people/jane-k.md","content":"Jane K — knows the NYC AI scene. Cares about on-device inference. Open thread: owes her a demo (as of Jul 2026)."}'
```

If create answers `{"error":"exists"}` (409), append to the existing card instead via
`/api/youmodel/add` with `"file":"people/jane-k.md"`. A `conflict` or `locked` reply means
another writer touched the file — re-read via `GET /api/youmodel` and retry once.
Confirm each successful write back to the user in a few words ("Saved to GOALS.md."), then
ask the next question.

## Step 4 — Wrap up

Recap what was saved, file by file, in 3–6 lines. Tell them where it lives: the **Mind →
Your Model** card in the dashboard, where every entry can be edited or deleted, and nothing
ever leaves the Mac. Invite corrections: "Anything there I got wrong or you'd rather I
forget?" — and honor deletions immediately (propose, confirm, then remove via the same card
or `/api/memory/save`).
