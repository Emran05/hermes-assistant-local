---
name: hermes-search
description: "Search the user's own local index before you search the web — one curl across their chats with you, their scratchpad, Message Center previews, calendar events and the watchtower news store. Loopback, keyless, read-only, milliseconds."
version: 1.0.0
author: Hermes Assistant (local)
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [search, index, fts5, local, chats, notes, messages, calendar, news, cheapest-source-first]
    category: hermes-search
    related_skills: [hub-cartographer]
---

# Hermes Search

The dashboard keeps a **unified local index** (SQLite FTS5,
`~/.hermes/dashboard/index.db`, 0600) over everything the assistant already
holds. One route searches all of it:

```
GET http://127.0.0.1:7788/api/search?q=<words>[&source=<one>][&limit=<n>]
```

No auth header, no key, no network. It answers in single-digit milliseconds and
never runs a model. **Check it before `web_search`** whenever the question could
plausibly be about the user's own life, their past conversations with you, their
notes, their week, or news they already collected.

## 1. Parameters

| Param | Default | Rules |
|---|---|---|
| `q` | required | Free text, clamped to 200 chars. See "How the query is read" |
| `source` | all | One of `chat`, `note`, `message`, `calendar`, `watchtower` (or `all`). Anything else → HTTP 400 |
| `limit` | 20 | Clamped to 50. Junk values fall back to the default rather than erroring |

## 2. The sources

| `source` | What is in it | What `ref` carries |
|---|---|---|
| `chat` | Every dashboard/Telegram conversation, one row per conversation, **user and assistant turns only** — tool lines, approval prompts and the `__prewarm__` session are never indexed | the session id (feed it to `/api/history?session=`) |
| `note` | The dashboard Scratchpad (`notes.json`) | `notes` |
| `message` | Message Center conversation previews — present only when the helper app has Full Disk Access | the handle (phone/email) |
| `calendar` | macOS Calendar events, ±30 days | `today` |
| `watchtower` | The watchtower/intel store: curated picks + raw feed items | the article URL |

A source that is not set up on this Mac simply contributes nothing — there is
no error and no partial failure. If `sources.message` is 0, the helper app does
not have Full Disk Access; say that rather than retrying.

## 3. The response

```json
{
  "ok": true,
  "q": "kumquat",
  "took_ms": 4,
  "sources": {"chat": 2, "note": 1, "message": 1, "calendar": 1, "watchtower": 2},
  "results": [
    {"id": "chat:chat-2026-08-31-qglb4",
     "source": "chat",
     "title": "Kumquat plumbing notes",
     "ts": 1788600000.0,
     "snippet": "…so the kumquat index lands in ~/.hermes/dashboard…",
     "mark_start": 8,
     "mark_len": 7,
     "ref": "chat-2026-08-31-qglb4"}
  ]
}
```

- `results` is ordered by **BM25**, best first, with the title weighted 10× the
  body. `sources` counts the WHOLE match set, so it is right even when you
  passed `source=` or a small `limit`.
- `snippet` is **plain text** — ±60 characters around the first hit, whitespace
  collapsed, `…` where it was cut. It never contains markup.
- `mark_start` / `mark_len` are character offsets **into `snippet`** marking the
  matched word (`mark_start` is `null` when the hit was in the title only).
  That is how the dashboard highlights without ever putting user text on an
  innerHTML path; you rarely need them — read the snippet.
- `ts` is epoch seconds. Render it 12-hour when you show it.

Failure shape is the same object with `"ok": false` and an `"error"` string —
`results` is still an empty list, so the happy path never has to special-case it.

## 4. Recipes

```bash
# the default: everything, ranked
curl -s 'http://127.0.0.1:7788/api/search?q=roof%20estimate' | python3 -m json.tool

# one source only
curl -s 'http://127.0.0.1:7788/api/search?q=dentist&source=calendar' | python3 -m json.tool

# "did we already talk about this?" — chats, wider net
curl -s 'http://127.0.0.1:7788/api/search?q=prefix%20cache&source=chat&limit=50' | python3 -m json.tool

# prefix search: a single trailing * on the LAST word
curl -s 'http://127.0.0.1:7788/api/search?q=embed*' | python3 -m json.tool

# what is actually indexed, and when it was last refreshed
curl -s http://127.0.0.1:7788/api/search/status | python3 -m json.tool
```

Then open what you found:

```bash
# a chat hit -> the full transcript
curl -s 'http://127.0.0.1:7788/api/history?session=<ref>' | python3 -m json.tool
# a message hit -> the Message Center store
curl -s http://127.0.0.1:7788/api/messages | python3 -m json.tool
# a news hit -> ref IS the article URL
```

## 5. How the query is read (so you can predict the result)

- Words are ANDed. `q=roof estimate` finds items containing **both**, anywhere.
- **There are no operators.** `OR`, `NOT`, `NEAR`, `title:`, quotes, parentheses
  and stray `*` are all searched as literal words — the server quotes every
  token before it reaches FTS5, deliberately, so no query can ever be a syntax
  error or reach beyond what was asked. If you want alternatives, run two
  searches and merge them yourself.
- The one exception: a **single trailing `*`** is a prefix search on the last
  word. `q=index*` matches *index, indexing, indexed*. A bare prefix without the
  star matches nothing — whole words only.
- Matching is case- and accent-insensitive (unicode61). It is **not** stemmed:
  `meeting` will not find `meet`. Try the prefix form when a word has obvious
  endings.
- More than 12 words are ignored past the twelfth.

## 6. `/api/search/status`

```bash
curl -s http://127.0.0.1:7788/api/search/status | python3 -m json.tool
```

Returns per-source row counts, `total`, `last_sweep` (epoch) and `last_sweep_ms`,
plus a `detail` block naming any source that is `absent` on this Mac. Use it to
answer "what can you actually see?" honestly, and to tell a genuinely empty
result apart from a source that was never set up.

## Gotchas

- **Freshness.** Chats and the scratchpad are indexed within ~2 seconds of being
  written. Calendar, Message Center and news refresh on a sweep — at dashboard
  start+60s and every 30 minutes. So a calendar event added a minute ago may not
  be there yet; check `last_sweep` before insisting something does not exist.
- **A miss is not proof.** No stemming, no synonyms, whole words only. Before
  concluding "there is nothing about X", try the prefix form and one obvious
  synonym. Then say you could not find it — do not say it does not exist.
- **Read-only.** There is no write route here, and no way to delete an index row
  except by deleting the source item. Nothing you do through this endpoint
  changes the user's data.
- **Never paste a snippet as if it were the whole item.** A snippet is ±60
  characters of context. If you are going to act on a conversation or a message,
  fetch it properly (`/api/history`, `/api/messages`) first.
- **Loopback only.** 127.0.0.1. Never tunnel, proxy or expose this route — the
  index holds the user's private text, which is exactly why the file is 0600.
- **Cheapest source first**, in order: this route → `/api/intel` (the news store
  in full) → your memory files → `web_search`. A local search costs
  milliseconds; a web search costs seconds and adds noise.
