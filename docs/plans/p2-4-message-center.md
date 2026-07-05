# Message Center via app-reads-chat.db — design spec (P2.4)

Phase 2 "Initiative", workstream #5 (DEVPLAN §Phase 2). Unblocks the Message
Center widget that has shipped `available:false, grant:true` since day one because
the launchd dashboard python (`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`,
per `~/Library/LaunchAgents/com.hermes.dashboard.plist`) cannot hold Full Disk
Access (FDA) — a documented macOS TCC limitation (CLAUDE.md "Not yet done", DEVPLAN
risk table row 1).

## Design in one paragraph (the pivot)

The **signed native app** (`/Applications/Hermes Assistant.app`, `app/main.swift`)
CAN be granted FDA. So the FDA-holding app reads `~/Library/Messages/chat.db`, runs
the exact conversation queries the dashboard already wrote and proved in
`expand_messages()` (`dashboard/expanders_extra.py:553`), decodes the
`attributedBody` blobs, and **POSTs the grouped conversations as JSON** to a new
token-guarded loopback endpoint `POST /api/messages/ingest`. A new aux module
`dashboard/aux_messages.py` stores that JSON (`~/.hermes/dashboard/messages/store.json`,
0600) and **overrides the existing `messages` widget + expander providers** to
serve from the store instead of touching `chat.db`. Net effect: **the dashboard
python never opens `chat.db` and never needs FDA**; only recent decoded
conversations (not the whole message history) ever leave the TCC-protected store;
the widget/pop-out render exactly the shape `RENDER.messages` (index.html) and
`EXPAND_RENDER.messages` (expand.js) already expect. Graceful degradation is
first-class: no app running → "syncing" state; app running but FDA denied →
grant-prompt card with exact steps + a button that opens the FDA settings pane.

Why app-POSTs-decoded-JSON and not app-copies-whole-chat.db-for-python-to-read:
copying the entire `chat.db` (often multi-GB, the user's complete message history)
into the less-protected `~/.hermes` is a real privacy + disk regression, and it
keeps the dashboard in the FDA/TCC-attribution swamp we are trying to leave. The
app is the only process that must ever touch the protected store; it emits the
minimum (14 conversations × one preview line) over loopback. The read of `chat.db`
itself is done against a **read-only point-in-time snapshot** taken with SQLite's
online-backup API, so no WAL locks are held on the live database.

---

## Goal & acceptance criteria (done means…)

1. **Real messages, no FDA error, across a reboot.** With FDA granted to
   `Hermes Assistant.app`, the Message Center widget and its pop-out show real
   recent iMessage/SMS conversations (name, latest preview, unread badge, relative
   time), and still do after a full reboot — the dashboard python logs zero
   `chat.db` access and zero FDA errors (it never opens the db).
2. **Dashboard-only path is independently testable.** `POST /api/messages/ingest`
   with a valid token + sample JSON, then `GET /api/messages`, returns the grouped
   conversations — with the Swift app never involved (curl-drivable end-to-end).
3. **Three degradation states are visually distinct and correct.**
   (a) never synced (no store) → "Waiting for the Hermes app…" syncing state;
   (b) FDA denied (`fda:false` ingest) → grant-prompt card with the exact steps +
   an "Open Full Disk Access settings" button; (c) stale (last sync > 10 min) →
   data renders with a muted "synced Nm ago" badge.
4. **Token-guarded ingest.** `POST /api/messages/ingest` without / with a wrong
   `X-Hermes-Msg-Token` returns `403` and does not mutate the store.
5. **Local-first & minimal-surface, verified.** No network egress (loopback only);
   `store.json` is `0600`; only the fields in the Data-model table are persisted;
   the full `chat.db` never lands in `~/.hermes`; no message is ever sent or drafted
   (read-only; no reply affordance in v1).
6. **Reuse, not rewrite.** The pop-out renderer `EXPAND_RENDER.messages` renders
   the ingested shape **unchanged** (the store's `conversations[]` matches the keys
   it already consumes: `name/group/sender/from_me/unread/attachment/reaction/last/participants/ts`
   + top-level `total_unread/convo_count/today_count`).
7. **Fault isolation.** A malformed/oversized/badly-typed ingest body is rejected
   with a 4xx and never corrupts a good store; an `aux_messages.py` exception never
   takes the hub down (aux loader already `try/except`-wraps module load,
   server.py:2081).
8. **No design-law regressions.** Zero emoji (bespoke SVG only), 12-hour /
   relative time via the existing `relTime` helper, Liquid Glass, Motion One
   `animate()` for row entrance — verified by the headless expand.js eval check
   (CLAUDE.md renderer-verification rule).

---

## Data model (files/tables/JSON — exact shapes)

### Source tables read by the app (`~/Library/Messages/chat.db`, SQLite)
Exactly the tables `expand_messages()` already uses — the Swift SQL is a mechanical
port of its proven queries:
- `chat` — `ROWID`, `guid`, `chat_identifier`, `display_name`, `style`
  (**43 = group**, 45 = single).
- `message` — `ROWID`, `text` (usually NULL on modern macOS), `attributedBody`
  (NSKeyedArchiver/streamtyped blob — decode below), `is_from_me`, `is_read`,
  `date` (**Apple epoch**, see below), `handle_id`, `cache_has_attachments`,
  `associated_message_type` (≠0 ⇒ tapback/reaction).
- `handle` — `ROWID`, `id` (phone/email of the other party).
- `chat_message_join` — `chat_id` ↔ `message_id`.
- `chat_handle_join` — `chat_id` ↔ `handle_id` (participants, for group naming).

**Apple-epoch quirk:** `message.date` is nanoseconds since 2001-01-01
(Unix 978307200). Older (pre-High-Sierra) rows are *seconds*. Convert exactly as
the python does: `unix = 978307200 + (d/1e9 if d > 1e11 else d)`.

**`attributedBody` decode (byte-scan port of `expand_messages._body`):** when
`text` is NULL, find `b"NSString"`, then the next `+`; read the length prefix
(`0x81` ⇒ next 2 bytes are a little-endian u16 length; `0x82` ⇒ next 4 bytes u32;
else 1 byte); slice that many bytes as UTF-8 (`ignore` errors), strip NUL. Fallback:
first printable run after the marker. Empty ⇒ label `"Attachment"` (if
`cache_has_attachments`), else `"Reaction"` (if tapback), else `""`.

### Ingest wire shape — `POST /api/messages/ingest` body (built by the app)
```json
{
  "v": 1,
  "generated_at": 1720170000.0,
  "fda": true,
  "host": "Enzos-MacBook-Pro",
  "conversations": [
    {
      "name": "+15551234567",           // raw handle/ident/display_name; python prettifies
      "ident": "+15551234567",
      "group": false,
      "participants": 1,
      "last": "see you at 6",            // decoded latest-message preview, app caps ≤200 chars
      "from_me": false,
      "sender": "+15551234567",          // "You" if from_me
      "ts": 1719990000.0,               // unix seconds (already converted from Apple epoch)
      "unread": 2,
      "attachment": false,
      "reaction": false,
      "today_count": 3
    }
  ],
  "totals": { "unread": 5, "today": 9 }
}
```
FDA-denied variant (app read failed): `{"v":1,"generated_at":..,"fda":false,"reason":"Full Disk Access needed","conversations":[],"totals":{"unread":0,"today":0}}`.

### Persisted store — `~/.hermes/dashboard/messages/store.json` (0600)
The normalized, validated, **prettified** version of the last good ingest
(python prettifies raw phone handles → `(555) 123-4567`, re-truncates `last` to
140, clamps counts). Fields as above plus `stored_at` (server receipt time).
Nothing else is written to disk; no attachments, no full history, no bodies beyond
one preview per conversation.

### Shared secret — `~/.hermes/dashboard/messages-token` (0600)
32 hex bytes, minted by `aux_messages.py` at module load if absent
(`os.urandom(16).hex()`, atomic write, `chmod 0600`). Read by both the dashboard
(compare) and the app (send). Deliberately **not** the `serve-token` (that is the
agent's WS auth secret — the display app should not hold it). No
`install-services.sh` change needed; app and dashboard run as the same user.

---

## Backend — `dashboard/aux_messages.py` (new aux module)

Self-contained, stdlib-only, exec'd into `server.py` globals by the aux loader
(server.py:2071-2083), sorts after `expand_messages`'s home in `expanders_extra.py`
(loaded first) so its `EXPANDERS["messages"]` / `WIDGETS["messages"]` overrides win.
Uses server globals: `HOME HERE DATA read_json write_json _state_lock _widget_cache
_cached register_get register_post WIDGETS EXPANDERS`. Imports its own
`os re json time hashlib`.

### `POST /api/messages/ingest`  (registered via `register_post`)
- **Auth:** handler reads header token from `ctx`… but `RouteCtx` (server.py:2051)
  only carries `query`/`body`/`raw_path`, **not headers**. Two options, pick one at
  build time and note it here:
  (a) **carry the token in the body** — app includes `"token": "<hex>"` in the JSON;
  handler compares `body["token"]` to the file (simplest, no server.py change);
  (b) extend `RouteCtx`/`_dispatch_aux` to pass `self.headers` and read
  `X-Hermes-Msg-Token`. **Recommended: (a)** — zero core-file edits, matches the
  aux-module contract. Constant-time compare (`hmac.compare_digest`).
- **Request:** the ingest JSON above (with `token`). **Errors:** missing/updated
  token file → `({"ok":false,"error":"no token"},403)`; mismatch →
  `({"ok":false,"error":"forbidden"},403)`; body not a dict / `v`≠1 →
  `({"ok":false,"error":"bad body"},400)`; serialized > 512 KB or > 40
  conversations → `({"ok":false,"error":"too_big"},413)`.
- **Behavior:** validate + clamp each conversation (types, `last`→140 chars,
  `unread`/`today_count`→int≥0, `ts`→float, prettify raw phone `name`/`sender` via a
  ported `_pretty`); write `store.json` atomically under `_state_lock`
  (temp+fsync+`os.replace`, `chmod 0600`); bust `_widget_cache.pop("messages_store",None)`.
- **Response:** `{"ok":true,"stored":<n>,"at":<stored_at>}`.

### `GET /api/messages`  (registered via `register_get`)
- **Request:** none. **Response** (the widget/pop-out contract):
```json
{ "ok": true, "available": true, "grant": false, "never_synced": false,
  "stale": false, "age_s": 41.2, "generated_at": 1720170000.0,
  "conversations": [ … ], "total_unread": 5, "convo_count": 6, "today_count": 9 }
```
- **Degradations:** no store → `{"available":false,"grant":false,"never_synced":true,
  "reason":"Waiting for the Hermes app to sync Messages…"}`; store with `fda:false`
  → `{"available":false,"grant":true,"reason":"Full Disk Access needed to read Messages."}`;
  `now - generated_at > 600` → `stale:true, age_s` set (data still returned).
- **Errors:** unreadable/corrupt store → `{"available":false,"grant":false,
  "reason":"…"}` (never 500 the hub).

### Provider overrides (the reuse win)
At module load, after defining the two functions:
```python
def _msg_store():           # cheap read + staleness, _cached 5s
    return _cached("messages_store", 5, _read_store)
def w_messages_store():     # card provider  → top 6 conversations + counts
    return _msg_store()
def expand_messages_store():# pop-out provider → full store
    return _msg_store()
WIDGETS["messages"]["provider"] = w_messages_store    # was w_messages (server.py:1733)
EXPANDERS["messages"] = expand_messages_store         # was expand_messages (expanders_extra.py:1077)
```
`_read_store()` returns the same `available/grant/never_synced/stale/conversations/…`
shape as `GET /api/messages`. This is what makes the card + pop-out light up with no
renderer rewrite, and it removes the last two `chat.db` reads in the python process.
(The now-dead `w_messages`/`expand_messages` bodies stay in place but are never
called — leave them, or gut them to `return _read_store()` in a follow-up; not
required for this workstream.)

### Background threads
**None required.** Staleness is computed at read time; the store is tiny (≤14
convos) so no GC/pruner thread. (If a "last-synced" heartbeat is ever wanted, add a
module-load thread guarded by a global flag exactly like
`aux_recorder.py:_recorder_thread_started` — not needed for v1.)

### permissions.py interaction
This feature performs **no agent actions and no tool calls**, so it never enters
`permissions.decide()` and cannot violate a tier. It never sends, drafts, or
triggers a turn. The NOTIFY-ONLY invariant (DEVPLAN §Phase 2 "triggers may only
notify") is upheld vacuously: the Message Center only *displays*. A future
"reply to this message" affordance would be a real action and MUST route through
the serve/hub approval path (`hermes_rpc.run_turn` + `approvals.mode:manual` +
`permissions.decide`) — explicitly out of scope for v1 (see Open questions).

---

## App — `app/main.swift` + `app/build-app.sh` (Swift shell changes)

The app already: is ad-hoc signed (`codesign --force --deep -s -`, bundle id
`local.hermes.assistant`), has `NSAllowsLocalNetworking`, uses `URLSession` to hit
`127.0.0.1:7788`, and runs a WKWebView. Two additions:

### 1. `MessagesSync` — a timed FDA read → POST
A new `final class MessagesSync` (or a delegate extension), started from
`applicationDidFinishLaunching` after `startRetry()`:
- Reads `~/.hermes/dashboard/messages-token` once (retains the hex).
- A `Timer.scheduledTimer(withInterval: 60, repeats: true)` (plus one fire ~5 s
  after first successful health check) dispatches `sync()` onto a background
  `DispatchQueue`.
- `sync()`:
  1. **Snapshot (read-only copy, avoids WAL locks):** `import SQLite3`;
     `sqlite3_open_v2(chatdb, &src, SQLITE_OPEN_READONLY, nil)` — if this returns
     `SQLITE_CANTOPEN`/auth error ⇒ **FDA denied** ⇒ POST `{fda:false,…}` and
     return. Else `sqlite3_open(tmpPath, &dst)` in `NSTemporaryDirectory()`;
     `sqlite3_backup_init/step(-1)/finish` for a consistent point-in-time copy;
     close `src`.
  2. **Query the snapshot** with the exact ported queries (chats-by-recency LIMIT
     14; per-chat last message + unread + today + participants — see Data model).
  3. **Decode** `attributedBody` via the byte-scan port; convert Apple-epoch `date`.
  4. Build the `conversations[]` + `totals`, `POST` JSON (with `token`) to
     `http://127.0.0.1:7788/api/messages/ingest`.
  5. `unlink(tmpPath)` (snapshot never persists; nothing lands in `~/.hermes`).
- All off the main thread; failures are logged and retried next tick (last good
  store is preserved server-side).

### 2. External-scheme opener (for the grant button)
`webView(_:decidePolicyFor:)` (main.swift:103) today only routes non-localhost
**http/https** to `NSWorkspace`. Extend it: for any non-localhost scheme that is
not `http/https/about/data` (e.g. `x-apple.systempreferences:`), call
`NSWorkspace.shared.open(url)` and `decisionHandler(.cancel)`. This lets the grant
card's "Open Full Disk Access settings" link
(`x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles`) work.

`build-app.sh` needs no structural change (Info.plist stays as-is; FDA has **no**
programmatic prompt / usage-description — it is granted manually). Rebuild with
`app/build-app.sh` after editing `main.swift`.

---

## Frontend — `dashboard/aux_messages.js` (new) + ONE index.html tag

Auto-served at `/aux_messages.js` (server.py:2126-2141). Loaded **last** so its
reassignments win over `index.html`'s `RENDER` and `expand.js`'s `EXPAND_RENDER`.

**The ONE index.html edit (orchestrator applies):** after line 2055
(`<script src="/aux_config.js"></script>`) add:
```html
<script src="/aux_messages.js"></script>
```

`aux_messages.js` reassigns two globals (both are `const` objects — property
assignment is legal; same technique `aux_recorder.js` uses on `loadConsole`):

### Card — `RENDER.messages = function(b, d){…}` (Hub widget body)
States, in order:
- `d.never_synced` → syncing shell: bespoke SVG spinner glyph + "Waiting for the
  Hermes app to sync Messages…" (subtle, `var(--muted)`).
- `!d.available && d.grant` → compact grant nudge: warning-tinted line "Full Disk
  Access needed" + a small "Grant access" affordance that pops the widget out to
  the full grant card (reuses `openPop`).
- available → top 6 `d.conversations` rendered as message rows (avatar initials via
  the existing `PAL`/`hue` pattern, name, `relTime(ts)`, preview, unread badge) —
  the same visual language the pop-out already uses, condensed. If `d.stale`, a
  trailing muted "· synced {Nm} ago" on the header meta.
- available & empty → `<div class="hint">No recent conversations.</div>`.

### Pop-out — augment `EXPAND_RENDER.messages`
The existing `EXPAND_RENDER.messages` (expand.js:516) **already** renders
`available/grant/conversations/total_unread/convo_count/today_count` and the exact
per-convo keys the store provides — keep it. Wrap it so that: (a) the
`never_synced` state shows the syncing card; (b) a `d.stale` badge ("synced Nm ago")
appears under the stat grid; (c) the grant card's step 2 says **add "Hermes
Assistant.app"** (not "the launchd python") and its button is the
`x-apple.systempreferences:` link above. Everything else is inherited.

### Animations
Motion One `animate()` (global from `/motion.min.js`) on pop-out open: stagger the
conversation rows `opacity 0→1, translateY 6→0`, ~24 ms stagger, respects
`prefers-reduced-motion` (Motion honors it; match existing expand.js usage).

### Design laws
Zero emoji (all glyphs bespoke inline SVG — spinner, attachment clip, group icon
already present); 12-hour/relative time via `relTime`; `esc()` on every dynamic
string (the CLAUDE.md "esc-on-number throw" class of bug — guard `unread`/counts).

---

## Integration points (verified by grep)

| Touchpoint | File:sym | Verified |
|---|---|---|
| aux loader exec's `aux_*.py` into globals | `server.py:2071` `_AUX_FILES` loop | ✓ |
| `register_get`/`register_post` | `server.py:2043-2048` | ✓ |
| `RouteCtx` (`.q1`,`.body`,`.query`; **no headers**) | `server.py:2051` | ✓ (drives token-in-body choice) |
| aux dispatch handles `(dict,status)` tuples | `server.py:2223` `_dispatch_aux` | ✓ |
| aux `.js` auto-served | `server.py:2126` | ✓ |
| Widget registry entry to override | `server.py:1733` `WIDGETS["messages"]` | ✓ |
| Old card provider (replaced) | `server.py:1585` `w_messages` | ✓ |
| Expander registry to override | `expanders_extra.py:1077` `EXPANDERS["messages"]` | ✓ |
| Proven decode + grouping to port | `expanders_extra.py:553` `expand_messages` (`_body`,`_apple_ts`,`_pretty`, chat/last/unread/today/participants queries) | ✓ |
| Card renderer to override | `index.html:1216` `RENDER.messages` | ✓ |
| Pop-out renderer to keep/augment | `expand.js:516` `EXPAND_RENDER.messages` | ✓ |
| The one script tag site | `index.html:2050-2055` (after aux_config.js) | ✓ |
| Frontend helpers available | `relTime`,`esc`,`icon`,`widgetIcon`,`openPop`,`animate` (index.html/motion) | ✓ |
| Server globals for aux | `HOME/HERE/DATA(server.py:44-46)`, `read_json/write_json(70/78)`, `_state_lock(62)`, `_widget_cache(1166)`, `_cached(1169)` | ✓ |
| App HTTP + link routing to extend | `main.swift:103` `decidePolicyFor`, `main.swift:135` `tryConnect` | ✓ |
| App build/sign | `build-app.sh` (ad-hoc, bundle `local.hermes.assistant`) | ✓ |
| Dashboard python identity (why it lacks FDA) | `com.hermes.dashboard.plist` framework python3 | ✓ |
| Token pattern precedent | `install-services.sh:21` serve-token 0600 | ✓ (we mint a separate messages-token) |

---

## Edge cases & failure modes (exhaustive)

- **FDA denied** → `sqlite3_open_v2` returns `SQLITE_CANTOPEN`/authorization error →
  app POSTs `{fda:false}` → `GET` returns `grant:true` → grant card. (Distinguished
  from "no store" so the UI never tells a granted user to grant.)
- **App not running / never synced** → no store.json → `never_synced:true` →
  syncing state. (Note: inside the app's WebView the app is by definition running,
  so this shows mainly in a browser-opened dashboard or in the first ≤60 s after
  launch.)
- **App running, first tick pending** → same never_synced state until first POST.
- **chat.db mid-write / WAL busy** → backup API yields a consistent snapshot;
  if `backup_step` returns `SQLITE_BUSY`, retry next tick, POST nothing (keep last
  store). No lock held on the live db.
- **Huge chat.db (multi-GB)** → `.backup` copies the whole file to temp once/60 s
  then deletes it; acceptable, but if this ever bites, switch step 1 to a read-only
  open of the live db (`SQLITE_OPEN_READONLY`) and skip the copy (works when
  un-contended, exactly what the old python did). Documented as the fallback.
- **attributedBody unparseable** → preview empty → `"Attachment"`/`"Reaction"`/`""`
  fallback (parity with python).
- **Apple-epoch seconds vs ns** → the `>1e11` branch handles both.
- **Tapbacks/reactions** (`associated_message_type≠0`) → labeled "Reaction".
- **Group vs single** → `style==43` or `participants>1`.
- **Token file missing** (dashboard not yet loaded aux, or user deleted it) →
  aux mints it on next load; app's POST 403s until it re-reads → app re-reads token
  file each tick to self-heal.
- **Ingest spoof / junk from another local process** → 403 without a valid token;
  even a valid-token bad body is size/type-clamped, never corrupting a good store.
- **Malformed/oversized body** → 400/413, store untouched.
- **Concurrent POSTs (two app instances)** → atomic write under `_state_lock`,
  last writer wins; harmless (same source).
- **Non-UTF8 in a preview** → decoded with `ignore`/`replace`.
- **Dashboard restart** → store.json persists → widget instantly shows last sync
  with a stale badge until the app's next POST.
- **Reboot** → FDA persists (the whole point); app relaunches (if in Login Items —
  see Open questions) and resyncs.
- **App rebuilt (`build-app.sh`)** → ad-hoc cdhash changes → **macOS may drop the
  FDA grant** → grant card returns until re-granted. Documented in FDA steps;
  durable fix = stable self-signed identity (Open questions).
- **`esc` on a numeric `unread`** → cast to string before `esc` (the known
  renderer-throw class).

---

## Security & safety (upholds every invariant)

- **Read-only, never writes chat.db, never sends/drafts.** No reply path in v1;
  the whole feature cannot mutate Messages or emit a message anywhere. (Gmail
  read+draft-only and Telegram-locked invariants are untouched — this feature does
  not go near them.)
- **Local-first, minimal surface.** Ingest is loopback (`127.0.0.1`); no external
  network anywhere; `store.json` and `messages-token` are `0600`; the **entire
  `chat.db` never leaves the TCC-protected store** — only ≤14 decoded conversation
  previews cross loopback and persist. Snapshot copy lives in the app's temp dir and
  is `unlink`ed immediately.
- **Fields persisted are enumerated** (Data model): sender display, one decoded
  preview per conversation (≤140 chars), from_me, ts, unread/today counts, group
  flag, participant count, ident. **Not** stored: full history, message bodies
  beyond the latest preview, attachment contents, read receipts detail, group
  member lists beyond a count.
- **Token-guarded ingest** blocks other local processes from spoofing the widget.
- **NOTIFY-ONLY boundary respected** (display only; no proactive action; no agent
  turn). Any future proactive/reply capability is gated behind the serve/hub
  approval path + `permissions.decide()` + `approvals.mode:manual` — out of scope.
- **Refuses:** to open Messages, to send/reply, to expose message text to any
  non-loopback path, to run `chat.db` reads in the launchd python, or to copy the
  whole database into `~/.hermes`.

---

## Test plan (no user spam, no `--yolo`)

Dashboard side is fully testable **without the app or FDA** (that is the point of
criterion #2):

```bash
TOK=$(cat ~/.hermes/dashboard/messages-token)        # minted at aux load

# 1. Never-synced state
rm -f ~/.hermes/dashboard/messages/store.json
curl -s localhost:7788/api/messages | python3 -m json.tool
#   expect: available:false, never_synced:true

# 2. Ingest a synthetic conversation set, then read it back
curl -s -XPOST localhost:7788/api/messages/ingest \
  -H 'Content-Type: application/json' \
  -d '{"v":1,"generated_at":'"$(date +%s)"',"fda":true,"host":"test",
       "conversations":[{"name":"+15551234567","ident":"+15551234567","group":false,
       "participants":1,"last":"see you at 6","from_me":false,"sender":"+15551234567",
       "ts":'"$(date +%s)"',"unread":2,"attachment":false,"reaction":false,"today_count":3}],
       "totals":{"unread":2,"today":3},"token":"'"$TOK"'"}'
#   expect: {"ok":true,"stored":1,...}
curl -s localhost:7788/api/messages | python3 -m json.tool
#   expect: available:true, total_unread:2, conversations[0].name "(555) 123-4567"

# 3. Token guard
curl -s -o /dev/null -w '%{http_code}\n' -XPOST localhost:7788/api/messages/ingest \
  -H 'Content-Type: application/json' -d '{"v":1,"conversations":[],"token":"wrong"}'
#   expect: 403 (store unchanged)

# 4. FDA-denied degradation
curl -s -XPOST localhost:7788/api/messages/ingest -H 'Content-Type: application/json' \
  -d '{"v":1,"generated_at":'"$(date +%s)"',"fda":false,"reason":"x","conversations":[],
       "totals":{"unread":0,"today":0},"token":"'"$TOK"'"}'
curl -s localhost:7788/api/messages | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["available"],d["grant"])'
#   expect: False True

# 5. Stale badge — hand-edit store.json generated_at back 15 min, GET → stale:true

# 6. Oversized/malformed → 413/400
```

**Frontend headless check** (CLAUDE.md renderer rule): eval `aux_messages.js` in
node with stubbed `esc/relTime/icon/animate/RENDER/EXPAND_RENDER` + the live
`GET /api/messages` payloads for each state; assert no throw and expected substrings
(grant card, syncing, stale badge, unread badge). Then in the app: ⌘R to reload the
WebView (service restart alone won't refresh it — CLAUDE.md).

**App side (needs the user for the FDA grant only):** after granting FDA (steps
below) and launching, `tail -f ~/.hermes/logs/dashboard.log`, wait ≤60 s, then
`GET /api/messages` shows real conversations; revoke FDA → app POSTs `fda:false`
within a tick → grant card returns. No message is ever sent during any of this.

---

## FDA grant — the NEEDS-YOU steps (exact)

1. Build/install the app: run `app/build-app.sh` → installs
   `/Applications/Hermes Assistant.app`. **Launch it once** so TCC knows the app.
2. Open **System Settings → Privacy & Security → Full Disk Access**.
3. Click **+**, go to **/Applications**, select **Hermes Assistant.app**, click
   Open (or drag the app onto the list). Toggle it **On**.
   *(The grant target is the app, NOT python3 — this is the whole workaround.)*
4. **Quit and reopen** Hermes Assistant (TCC grants apply on next launch).
5. Within ~60 s the Message Center widget fills with real conversations.
- **After any rebuild** (`build-app.sh` re-signs ad-hoc → new cdhash): if the widget
  reverts to the grant card, remove the stale "Hermes Assistant" row in the FDA list
  and re-add it. Durable fix in Open questions.

---

## Effort & sequencing + dependencies + open questions

**Sequencing (independently shippable halves):**
1. **Dashboard first (M):** `aux_messages.py` (ingest + GET + provider overrides +
   token mint) and `aux_messages.js` (card override, pop-out augment, grant/stale
   states) + the one index.html tag. Fully verifiable via curl-injected store and
   the headless renderer check — ships and demos the three degradation states
   before any Swift exists.
2. **App second (M):** `MessagesSync` (SQLite snapshot + ported queries + decode +
   POST) and the external-scheme opener in `main.swift`; rebuild via `build-app.sh`.
   Depends only on the messages-token existing (step 1).
3. **NEEDS-YOU (S):** the FDA grant + relaunch.

**Dependencies:** none hard on other P2 workstreams. Reuses `expand_messages`'s
proven decode/grouping (port), the aux-module + token-file patterns, and the
existing pop-out renderer. No `install-services.sh` or `permissions.json` change.

**Open questions:**
1. **Token transport** — token-in-body (recommended, zero core edits) vs. extend
   `RouteCtx` to pass headers for `X-Hermes-Msg-Token`. Body chosen unless the
   builder prefers the header (a 2-line `_dispatch_aux` change).
2. **Sync cadence** — fixed 60 s vs. on-window-focus (`windowDidBecomeKey`) +
   slower idle timer. 60 s is the simple default.
3. **Stable signing identity** — ad-hoc re-grant-after-rebuild is annoying; a
   self-signed Keychain identity (stable designated requirement) makes the FDA grant
   survive rebuilds. Worth a follow-up; ad-hoc is fine for v1.
4. **App autostart** — for cross-reboot freshness the app should be in Login Items
   (or a LaunchAgent); currently launched manually. Confirm the user wants it
   auto-launched; otherwise the widget is fresh only while the app is open.
5. **"Reply / Ask Hermes about this message"** — deferred; would be the first real
   *action* here and must go through serve/hub approval (NOTIFY-ONLY boundary).

**Multi-platform future (user floated "another device + computer use"):** the
`/api/messages/ingest` contract is **source-agnostic** — every conversation carries
a `service`-style tag and `chat_guid`. A companion device (an iPhone Shortcut, or a
second Mac driving WhatsApp/Signal/Teams via computer-use) could POST its
conversations to the *same* token-guarded endpoint, and the widget would merge them
into one cross-platform Message Center. v1 is local iMessage/SMS only; the endpoint
is intentionally shaped so that future ingest sources need no server changes.
