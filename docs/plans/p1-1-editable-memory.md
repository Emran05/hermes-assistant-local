# Editable Memory UI — design spec (P1.1)

Workstream 1 of Phase 1 ("Earn trust"), per `docs/DEVPLAN.md` §3. Lets the user
view / add / edit / delete Hermes's memories from the Mind view. Everything the
agent knows becomes readable and correctable; every mutation is snapshotted and
recorded so it is undoable (P1.2 contract included below).

**Ground truth this spec is built on (verified 2026-07-05):**

- Memory dir: `~/.hermes/memories/` — today holds `USER.md` (23 bytes,
  `The codeword is BANANA.`, mode 600) and a zero-byte `USER.md.lock`.
- The agent's memory store is `~/.hermes/hermes-agent/tools/memory_tool.py`:
  - **On-disk format for `USER.md` and `MEMORY.md` is NOT freeform markdown.**
    It is a list of entries joined by `ENTRY_DELIMITER = "\n§\n"` (line 59).
  - **Char budgets**: `user_char_limit` default **1375** (USER.md),
    `memory_char_limit` default **2200** (MEMORY.md); user's
    `~/.hermes/config.yaml` overrides via `memory.memory_char_limit: 2200`
    (line 102; `user_char_limit` not overridden).
  - **Locking**: `_file_lock()` (line 245) opens sidecar `<file>.lock` with
    mode `"a+"` and takes a **blocking `fcntl.flock(fd, LOCK_EX)`**. The
    zero-byte `.lock` file persists forever; its *presence* means nothing —
    only a *held* flock does.
  - **Drift detection** (`_detect_external_drift`, line 704): before any
    mutation the agent re-reads the file; if
    `raw.strip() != ENTRY_DELIMITER.join(parsed_stripped_entries)` OR any
    single entry exceeds the file's char limit, it snapshots to
    `<file>.md.bak.<ts>` and **refuses the mutation**. A naive freeform
    dashboard editor for USER.md would trip this. Our editor is entry-aware
    and serializes byte-identically to the tool's `_write_file`.
  - Writes are atomic temp+fsync+rename (`_write_file`, line 760) — readers
    never see partial files. We mirror this exactly.
  - The agent re-reads from disk **under lock before every memory mutation**
    (line 348 comment: "Re-read from disk under lock to pick up writes from
    other sessions") — so dashboard edits are picked up by the agent's next
    memory operation. The system-prompt memory block is snapshotted at
    session load (`load_from_disk`, line 168), so an *in-flight* chat session
    may not see an edit until its next session load (acceptance test uses a
    fresh `hermes -z` session).
  - At load, entries are scanned by `tools/threat_patterns.py` and poisoned
    entries render as `[BLOCKED: …]` — the agent sanitizes on read, so the
    dashboard does not need its own injection scanner.
- Dashboard surfaces that already exist:
  - `server.py` line 93: `USER_MEM = ~/.hermes/memories/USER.md`; line 139
    `read_memory()` (facts for the hero count + "What it remembers" card);
    line 842 `capabilities()`.
  - `expanders_extra.py` line 1740 `mind_extra()` → `memory_files`
    `[{name, mtime, size}]`, cached 60 s under `_widget_cache["mind_extra"]`.
  - Mind view card `#mem-list` ("What it remembers") in `index.html`
    (~line 844); `loadCapabilities()` renders it (~line 1805) and ends with
    `if(typeof mindExtras==='function')mindExtras().catch(()=>{});`
    (line 1828) — the exact hook pattern we replicate.
  - Static-file route: `elif path in ("/motion.min.js", "/expand.js"):`
    (server.py line 2071). `<script src="/expand.js"></script>` at
    index.html line 2049.
  - exec-include pattern: expanders_extra.py exec'd at server.py lines
    2023–2028, immediately before `class Handler`.

---

## Goal & acceptance criteria

Done means:

1. **View**: Mind view lists every `*.md` in `~/.hermes/memories/` with size,
   12-hour "last edited", and a provenance chip (agent vs you). `USER.md`
   renders as individual editable facts (entries), not one blob.
2. **Correct a fact end-to-end**: edit "The codeword is BANANA." →
   "The codeword is KUMQUAT." in the dashboard, then
   `hermes -z "What is the codeword? Answer with just the word."` returns
   KUMQUAT — and the file still round-trips the agent's parser
   (`raw.strip() == "\n§\n".join(parsed)`), so no `.bak.<ts>` drift file ever
   appears after a subsequent agent memory flush.
3. **Add**: a new fact added to USER.md and a new topic file (e.g.
   `projects.md`) both appear optimistically in the UI, survive a dashboard
   restart, and appear in `mind_extra`'s `memory_files` within 60 s.
4. **Delete is never destructive**: deleting a topic file moves it to
   `~/.hermes/dashboard/memory-trash/` and it can be restored from the UI
   byte-identically. `USER.md` and `MEMORY.md` deletion is refused with 403
   server-side AND the button is absent client-side.
5. **Concurrency-safe**: a save with a stale `base_etag` returns 409 with the
   current content and the UI shows a conflict banner (Reload theirs / Keep
   mine); a save while the agent holds the flock returns 423 and the UI says
   the agent is writing. Neither path can lose either writer's bytes (the
   loser is always snapshotted).
6. **Recorded & undoable**: every create/save/delete/restore appends one line
   to `~/.hermes/dashboard/recorder/memory-edits.jsonl` and (for
   save/delete) stores a pre-image under
   `~/.hermes/dashboard/snapshots/memory/`. Restoring a snapshot by hand
   yields byte-identical content (this is the P1.2 flight-recorder contract).
7. **Guarded**: core-file saves over the char limit are rejected 400 (with a
   live char meter client-side); freeform files cap at 128 KB (413); invalid
   names, path traversal, symlinks, non-`.md` are rejected 400; works fully
   with the model paused/offline (pure file ops, zero inference).
8. **Verified**: `python3 -m py_compile` on both Python files, `node --check`
   on memory.js, the curl matrix in §Test plan passes, and the headless
   render harness renders the panel from live API JSON without throwing.
   Zero emoji, bespoke SVG only, 12-hour times, Motion One entrances.

---

## Data model

### Files on disk

```
~/.hermes/memories/                      # the agent's dir — we add NOTHING here
  USER.md                                # core store, entries joined by "\n§\n", 600
  USER.md.lock                           # agent's flock sidecar (we flock the same fd path)
  MEMORY.md                              # core store (may not exist yet)
  <topic>.md                             # freeform topic files (agent- or user-created)

~/.hermes/dashboard/                     # all dashboard-owned state lives OUTSIDE memories/
  memory-meta.json                       # provenance index (schema below)
  memory-trash/                          # soft-deleted files: <orig>.<epoch>.md
  snapshots/memory/                      # pre-write images: <orig>.<epoch>.md   (P1.2 dir)
  recorder/memory-edits.jsonl            # append-only mutation log (P1.2 contract)
```

Rationale: nothing new inside `~/.hermes/memories/` — a future upstream change
that globs that dir can never ingest our trash/meta. `.trash`-in-place was
considered and rejected for that reason. Trash/snapshot naming:
`<original-name>.<unix-epoch-int>.md` (e.g. `projects.md.1751700123.md`).

### File classes

| class | files | editor | serialization | limit |
|---|---|---|---|---|
| `entries` (core) | `USER.md`, `MEMORY.md` | per-fact rows | `"\n§\n".join(stripped nonempty entries)` — byte-identical to `memory_tool._write_file` | char budget: USER 1375 / MEMORY 2200, overridable by `memory.user_char_limit` / `memory.memory_char_limit` in `~/.hermes/config.yaml` |
| `freeform` | every other `*.md` | raw markdown textarea | bytes as typed, UTF-8 | 131072 bytes (128 KB) |

### `memory-meta.json` (provenance — the "lightest scheme")

One dashboard-owned JSON index; **no frontmatter** (would pollute what the
model reads — memory content goes verbatim into the system prompt) and **no
per-file sidecars** (N extra files in a dir we don't own). Written via the
existing `write_json()` temp+rename under `_state_lock`.

```json
{
  "v": 1,
  "files": {
    "USER.md": {
      "created_by": "agent",
      "created_at": 1751587740,
      "last_user_edit": {"at": 1751830000, "etag": "9a1b2c3d4e5f6a7b"},
      "ops": [
        {"op": "save", "at": 1751830000, "pre_etag": "0f343b0931126a20",
         "post_etag": "9a1b2c3d4e5f6a7b",
         "snapshot": "~/.hermes/dashboard/snapshots/memory/USER.md.1751830000.md"}
      ]
    }
  },
  "trash": {
    "projects.md.1751700123.md": {"orig": "projects.md", "deleted_at": 1751700123,
      "size": 812, "etag": "77aa88bb99cc00dd"}
  }
}
```

- `etag` = `hashlib.sha1(raw_bytes).hexdigest()[:16]`. It is both the
  optimistic-concurrency token and the provenance probe.
- `ops` capped at 20 per file (oldest dropped).
- **Provenance derivation at read time** (never stored as a bare flag, so it
  can't go stale): current file sha1 == `last_user_edit.etag` → last writer
  **user** at `last_user_edit.at`; else if a meta entry exists → **agent**
  (someone else changed it since our last write) at file mtime; no meta entry
  at all → **agent** (file predates the dashboard or was agent-created).
  Files created via `/api/memory/create` get `created_by: "user"`.

### Recorder line (`memory-edits.jsonl`) — the P1.2 contract

One JSON object per line, append-only, written after a successful mutation:

```json
{"ts": 1751830000.412, "surface": "dashboard", "domain": "memory",
 "op": "save", "file": "USER.md", "by": "user",
 "pre_etag": "0f343b0931126a20", "post_etag": "9a1b2c3d4e5f6a7b",
 "pre_snapshot": "~/.hermes/dashboard/snapshots/memory/USER.md.1751830000.md",
 "bytes": 25}
```

`op` ∈ `create | save | delete | restore`. For `create`, `pre_etag` and
`pre_snapshot` are `null`. For `delete`, `pre_snapshot` is the trash path
(the trash file IS the pre-image). P1.2's undo reads this file newest-first
and restores `pre_snapshot` → `file` (or un-trashes). Nothing in P1.1 blocks
on P1.2 existing.

### Caps & constants (module-level in `memory_api.py`)

```python
MEM_DIR       = os.path.join(HOME, ".hermes", "memories")
DASH_DIR      = os.path.join(HOME, ".hermes", "dashboard")
MEM_TRASH     = os.path.join(DASH_DIR, "memory-trash")
MEM_SNAP      = os.path.join(DASH_DIR, "snapshots", "memory")
MEM_META      = os.path.join(DASH_DIR, "memory-meta.json")
MEM_RECORDER  = os.path.join(DASH_DIR, "recorder", "memory-edits.jsonl")
ENTRY_DELIM   = "\n§\n"                       # MUST match memory_tool.ENTRY_DELIMITER
CORE_FILES    = {"USER.md": ("user_char_limit", 1375),
                 "MEMORY.md": ("memory_char_limit", 2200)}
MEM_NAME_RE   = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,62}\.md$")
MAX_FREEFORM  = 131072      # 128 KB per freeform file
MAX_EDITABLE  = 524288      # refuse to open >512 KB in the editor at all
MAX_FILES     = 500
TRASH_MAX_N   = 100         # prune oldest beyond this
TRASH_MAX_B   = 20 * 1024 * 1024
SNAP_MAX_N    = 200         # interim GC until P1.2 owns snapshots
LOCK_TRIES    = 4           # 4 × 150 ms non-blocking flock attempts → 423
```

---

## Backend

### New module: `dashboard/memory_api.py` (exec-included, ~300 lines)

Per the CLAUDE.md integration pattern: exec'd into server.py's globals so it
may use `HOME`, `HERE`, `read_json`, `write_json`, `_state_lock`,
`_widget_cache` — but it imports **all of its own stdlib deps** at the top
(`import os, re, json, time, fcntl, hashlib, shutil, tempfile, urllib.parse`),
because exec'd code cannot rely on server.py's function-local imports. It
defines **only new names** (`memory_route`, `_mem_*`) so it cannot clobber
anything `expanders_extra.py` won (it is exec'd after it; see hook 1).

Module-load side effects (wrapped in try/except): `os.makedirs(...,
exist_ok=True)` for `MEM_TRASH`, `MEM_SNAP`, `os.path.dirname(MEM_RECORDER)`
with mode 0o700; GC any orphaned `.dashmem_*` temp files in `MEM_DIR` from a
crashed prior write.

Function inventory (real names the build must use):

| function | purpose |
|---|---|
| `_mem_valid_name(name)` | `MEM_NAME_RE` match, reject `..`, reject leading `.` (regex already does), reject `.lock`/`.tmp` suffixes |
| `_mem_path(name)` | validated `os.path.join(MEM_DIR, name)`; `os.path.realpath` must start with `realpath(MEM_DIR) + os.sep`; `os.path.islink` → refuse |
| `_mem_etag(raw)` | `hashlib.sha1(raw).hexdigest()[:16]` |
| `_mem_char_limit(name)` | for core files: regex-scan `~/.hermes/config.yaml` for `^\s*(user_char_limit|memory_char_limit):\s*(\d+)` (stdlib, no yaml dep), fall back to defaults in `CORE_FILES` |
| `_mem_lock(name)` | contextmanager mirroring the agent's protocol: `open(path + ".lock", "a+")`, then up to `LOCK_TRIES` × `fcntl.flock(fd, LOCK_EX \| LOCK_NB)` with 150 ms sleeps; on failure raise `_MemLocked`; always `LOCK_UN` + close in `finally`. Compatible with the agent's blocking `LOCK_EX` — the agent simply waits the few ms we hold it |
| `_mem_atomic_write(path, raw)` | `tempfile.mkstemp(dir=MEM_DIR, prefix=".dashmem_")`, `os.fchmod(fd, 0o600)`, write, `flush`, `os.fsync`, `os.replace` — byte-for-byte the agent's `_write_file` discipline, plus 600 perms matching USER.md |
| `_mem_meta()` / `_mem_meta_save(m)` | `read_json(MEM_META, {"v":1,"files":{},"trash":{}})` / `write_json` under `with _state_lock:` |
| `_mem_writer(name, raw, meta)` | provenance derivation per Data model |
| `_mem_snapshot(name, raw)` | write `MEM_SNAP/<name>.<int(time.time())>.md`, GC to `SNAP_MAX_N` |
| `_mem_record(op, name, by, pre_etag, post_etag, snap, nbytes)` | append one JSONL line; failure is logged to stderr, never fails the request |
| `_mem_bust()` | `_widget_cache.pop("mind_extra", None)` after every mutation (the 60 s cache would otherwise show stale `memory_files`) |
| `_mem_list()`, `_mem_file(qs)`, `_mem_create(b)`, `_mem_save(b)`, `_mem_delete(b)`, `_mem_restore(b)` | the six handlers |
| `memory_route(method, path, query, body)` | dispatcher; returns `(obj, status)`; whole body in try/except → `({"ok": False, "error": "internal: <msg>"}, 500)` so a bug can never take the Handler down |

### Endpoints

All under `/api/memory/*`, dispatched by `memory_route`. All error bodies are
`{"ok": false, "error": "<code>", ...}`.

#### `GET /api/memory/list`

Response 200:

```json
{"ok": true, "dir": "~/.hermes/memories",
 "files": [
   {"name": "USER.md", "kind": "entries", "core": true,
    "size": 23, "mtime": 1751587740.0, "etag": "0f343b0931126a20",
    "entry_count": 1, "char_used": 23, "char_limit": 1375,
    "last_writer": "agent", "last_writer_at": 1751587740.0,
    "created_by": "agent", "locked": false,
    "preview": "The codeword is BANANA."}
 ],
 "trash": [
   {"trash_name": "projects.md.1751700123.md", "orig": "projects.md",
    "deleted_at": 1751700123, "size": 812}
 ],
 "limits": {"max_file_bytes": 131072, "max_files": 500}}
```

- Scan `os.scandir(MEM_DIR)`, regular files ending `.md` only (skips `.lock`,
  `.bak.*`, dotfiles, dirs, symlinks). Sort: core files first, then by mtime
  desc. `preview` = first entry / first non-empty line, ≤120 chars.
- `locked` = non-blocking flock probe (acquire LOCK_NB, release immediately;
  `True` only if the probe fails — i.e. someone *holds* it right now).
- `char_used` = `len(raw.decode())` for core files; omitted for freeform.
- Trash list read from meta, reconciled against actual files in `MEM_TRASH`
  (missing file → drop meta row), sorted `deleted_at` desc, max 20 returned.
- Errors: `MEM_DIR` missing → create it (0o700) and return empty list.

#### `GET /api/memory/file?name=USER.md`

Response 200:

```json
{"ok": true, "name": "USER.md", "kind": "entries", "core": true,
 "content": "The codeword is BANANA.",
 "entries": ["The codeword is BANANA."],
 "etag": "0f343b0931126a20", "mtime": 1751587740.0, "size": 23,
 "char_used": 23, "char_limit": 1375,
 "last_writer": "agent", "last_writer_at": 1751587740.0, "created_by": "agent"}
```

- `entries` present only for `kind:"entries"`; parsed exactly like the
  agent: `[e.strip() for e in raw.split("\n§\n")]`, empties dropped.
- Errors: 400 `bad_name`; 404 `missing`; 413 `too_big_to_edit` (size >
  `MAX_EDITABLE`, message tells the user to edit it in a text editor);
  422 `not_utf8` (decode fails strictly — we refuse to edit rather than
  silently mangle bytes with `errors="replace"`).

#### `POST /api/memory/create` — body `{"name": "projects.md", "content": ""}`

- Validate name; refuse if a file with the same **casefolded** name exists
  (APFS default is case-insensitive — `"Notes.md"` vs `"notes.md"` would
  silently collide): scan existing names with `.lower()` compare → 409
  `exists`. Refuse when `len(files) >= MAX_FILES` → 400 `too_many_files`.
  Content > `MAX_FREEFORM` → 413. Names in `CORE_FILES` are allowed (this is
  how MEMORY.md gets created before the agent ever writes it) and are created
  with entries serialization.
- Under `_mem_lock`: if path now exists → 409 (TOCTOU guard); atomic write;
  meta `created_by:"user"`, `last_user_edit`; `_mem_record("create", ...)`;
  `_mem_bust()`.
- Response 200: `{"ok": true, "file": {<same row shape as list>}}`.

#### `POST /api/memory/save`

Body for core files: `{"name": "USER.md", "base_etag": "0f343b0931126a20",
"entries": ["The codeword is KUMQUAT.", "Prefers 12-hour time."]}`
Body for freeform: `{"name": "projects.md", "base_etag": "…", "content": "# Projects\n…"}`

Server pipeline (the order is the safety story):

1. Validate name/path; core file → require `entries` (list of str), strip
   each, drop empties, `payload = "\n§\n".join(entries)`; enforce
   `len(payload) <= _mem_char_limit(name)` → else 400
   `{"error": "over_limit", "char_used": N, "char_limit": M}`. Freeform →
   require `content` str, UTF-8 encode, `<= MAX_FREEFORM` → else 413.
2. `_mem_lock(name)` — busy → 423 `{"error": "locked", "hint": "Hermes is
   writing to this file — try again in a moment."}`.
3. Read current bytes. Missing → 404 `missing` (UI offers re-create).
   `_mem_etag(current) != base_etag` → 409:
   ```json
   {"ok": false, "error": "conflict",
    "current": {"content": "…", "entries": ["…"], "etag": "…",
                "mtime": 1751830100.2, "last_writer": "agent"}}
   ```
4. `_mem_snapshot(name, current)` → pre-image path (the undo guarantee
   exists BEFORE the write).
5. `_mem_atomic_write(path, payload_bytes)`.
6. Update meta (`last_user_edit = {at, etag}`, push `ops` row), record
   JSONL, `_mem_bust()`.
7. Response 200: `{"ok": true, "etag": "9a1b…", "mtime": 1751830000.4,
   "size": 25, "char_used": 25, "last_writer": "user"}`.

#### `POST /api/memory/delete` — body `{"name": "projects.md"}`

- Name in `CORE_FILES` → **403** `{"error": "core_file",
  "hint": "USER.md and MEMORY.md are Hermes's core memory and can be emptied but never deleted."}`.
- 404 `missing`; 423 on lock busy.
- Under lock: `shutil.move(path, MEM_TRASH/<name>.<epoch>.md)` (move keeps
  bytes; never `os.remove`), also move a stale `<name>.lock`? **No** — leave
  lock sidecars alone (harmless, agent recreates). Write meta trash row,
  `_mem_record("delete", …, pre_snapshot=<trash path>)`, `_mem_bust()`,
  prune trash to `TRASH_MAX_N`/`TRASH_MAX_B` oldest-first (pruning is the
  ONLY hard delete in the whole feature, and only ever of already-trashed
  copies).
- Response 200: `{"ok": true, "trash_name": "projects.md.1751700123.md"}`.

#### `POST /api/memory/restore` — body `{"trash_name": "projects.md.1751700123.md"}`

- Validate `trash_name` against meta + regex
  `^[A-Za-z0-9][A-Za-z0-9._ -]{0,62}\.md\.\d{10}\.md$`; realpath containment
  inside `MEM_TRASH`; 404 if gone.
- Target = `orig`; if target exists (agent recreated it), restore as
  `<stem>-restored.md`, `-restored-2.md`, … (casefold-checked).
- Move back, meta update, `_mem_record("restore", …)`, `_mem_bust()`.
- Response 200: `{"ok": true, "name": "projects.md"}`.

### EXACT inline hooks in `server.py` (the only shared-file surgery)

**Hook 1 — exec include.** Immediately AFTER the existing expanders_extra
block (after line 2028's `print`, before the `# HTTP` comment at 2031).
Placed after so `expanders_extra`'s last-wins redefinitions are untouched;
`memory_api.py` defines only new names:

```python
# Memory CRUD (P1.1) — same exec-include pattern; defines only memory_* names.
try:
    with open(os.path.join(HERE, "memory_api.py")) as _f:
        exec(_f.read(), globals())
except Exception as _e:  # never let an aux file take the hub down
    print(f"[memory_api] failed to load: {type(_e).__name__}: {_e}",
          file=sys.stderr)
```

**Hook 2 — static route for the JS.** Line 2071, one-token edit:

```python
        elif path in ("/motion.min.js", "/expand.js", "/memory.js"):
```

(The existing branch already sends `no-store` for everything but
motion.min.js, so memory.js edits show on ⌘R.)

**Hook 3 — GET dispatch.** Insert directly after the `/api/mind_extra`
branch (after line 2132), before `elif path == "/api/hub":`:

```python
        elif path.startswith("/api/memory/"):
            fn = globals().get("memory_route")
            obj, st = (fn("GET", path, parsed.query, None) if fn
                       else ({"ok": False, "error": "memory api unavailable"}, 503))
            self._json(obj, st)
```

**Hook 4 — POST dispatch.** Insert in `do_POST` right after the
`/api/briefing/refresh` block (after line 2169):

```python
        if path.startswith("/api/memory/"):
            fn = globals().get("memory_route")
            obj, st = (fn("POST", path, "", self._body_json()) if fn
                       else ({"ok": False, "error": "memory api unavailable"}, 503))
            self._json(obj, st)
            return
```

Total server.py delta: ~15 lines, no existing line modified except 2071.

---

## Frontend

### New file: `dashboard/memory.js`, served at `/memory.js` (~400 lines)

Loaded LAST so it can see every inline helper (`esc`, `relTime`, `$`,
`revealStagger`, `animate`, `REDUCE`) and so its assignments would win if it
ever needed to override — same rule as expand.js. It injects its own
`<style id="memcss">` block at load (keeps index.html hooks minimal and the
CSS colocated with its renderer).

### EXACT inline hooks in `index.html` (2 lines)

**Hook 5 — script tag.** After line 2049:

```html
<script src="/expand.js"></script>
<script src="/memory.js"></script>
```

**Hook 6 — render hook.** In `loadCapabilities()`, directly after the
existing line 1828 (mirrors the mindExtras hook pattern exactly):

```js
  if(typeof mindExtras==='function')mindExtras().catch(()=>{});
  if(typeof renderMemoryPanel==='function')renderMemoryPanel(c.memory);
```

### UX walkthrough (Mind view)

`renderMemoryPanel()` takes over the existing "What it remembers" card
(`#mem-list`'s parent `section.card.glass`): upgrades it to `span2`
(`card.classList.add('span2')`) for editor room, keeps the h2/brain icon, and
replaces the body with the manager. The plain facts list `loadCapabilities`
just painted is replaced in the same frame (no flicker worth engineering
around). Everything below is rendered from `GET /api/memory/list`.

**Layout inside the card body** (two zones, density-first):

1. **File strip** — horizontal chip row: one chip per file
   (`USER.md` labelled "Core facts", `MEMORY.md` "Agent notes", topic files
   by name minus `.md`), each chip shows a 6 px provenance dot (accent
   `--wac`-style: iris = you, dimmed foreground = agent) and the active chip
   is filled. Trailing ghost chip: bespoke SVG plus icon, "New file" — click
   → `prompt('Name the new memory file (letters, numbers, dashes):')`
   (WKWebView prompt() works per CLAUDE.md), client-validates, POSTs create,
   optimistically appends chip and opens it.
2. **Detail zone** for the selected file:
   - **Meta line**: provenance chip ("Hermes wrote this · Jul 3, 6:09 PM" /
     "You edited · Jul 5, 1:42 AM" — 12-hour via
     `new Date(ts*1000).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',hour12:true})`),
     size, and for core files a **char budget meter** (thin bar,
     `char_used/char_limit`, turns `--warn` >85 %, `--bad` at 100 %).
   - **Core files (`kind:"entries"`)** — fact rows: each entry is a row with
     the fact text, a pencil SVG (swap text for an `<input>` + save/cancel)
     and an x SVG (remove that entry). Below, an "Add a fact…" composer
     input + Add button. Every mutation = local entries array change →
     `POST /api/memory/save {entries, base_etag}` → optimistic row update
     with a 300 ms "saving" shimmer; rollback + error banner on failure.
   - **Freeform files** — monospace `<textarea>` (min-height 180 px,
     autogrows), char count, Save / Revert buttons appearing only when
     dirty. Save posts `{content, base_etag}`.
   - **Danger row** (freeform only): "Delete file" ghost button →
     `confirm('Move projects.md to the dashboard trash? You can restore it.')`
     → POST delete → chip fades out (`animate` opacity/scale, 200 ms). For
     core files the row instead shows a hint: "Core memory can be emptied,
     never deleted."
3. **Trash disclosure** — collapsed row at the card foot:
   "Recently deleted (2)" with chevron; expanded rows show orig name,
   deleted 12-hour time, restore SVG button (counterclockwise arrow) →
   POST restore → file reappears in the strip.

**Live-conflict awareness while editing**: while the panel is visible and a
file is open, poll `GET /api/memory/file?name=` every 15 s (single
`setInterval`, cleared on view switch — reuse the `setView` visibility, i.e.
only poll when `#view-mind` is not hidden). If etag changed and the editor is
NOT dirty → silently refresh rows + provenance ("Hermes just updated this").
If dirty → amber banner: "Hermes updated this file while you were editing —
[Load theirs] [Keep mine]". *Keep mine* re-fetches the fresh etag and saves
over it (the agent's version was already snapshotted by the server in step 4
of the save pipeline, so nothing is lost).

**States**:
- *Loading*: existing `.skel` skeleton rows.
- *Empty dir*: the existing hint copy ("Nothing stored yet…") plus the New
  file chip still available.
- *Error (fetch failed / 503 module missing)*: quiet banner "Memory panel
  unavailable — <error>", retry button; the rest of Mind is untouched.
- *409 conflict*: banner described above.
- *423 locked*: banner "Hermes is writing to this file — try again in a
  moment", auto-retries once after 1.5 s.
- *413/over_limit*: inline red hint under the composer/meter; Save disabled
  client-side once the meter is full (server still enforces).
- *Model paused/offline*: fully functional — file ops only; no gating on
  `/api/health`.

**Animations** (Motion One `animate()`, all skipped under `REDUCE`):
fact rows stagger in via existing `revealStagger(rows, 45)`; editor
open/close = opacity+`translateY(6px)` 220 ms ease-out; char meter width
animates on change; deleted chip scale-out 200 ms; conflict banner slides
down 180 ms. Zero emoji anywhere; every glyph is inline two-tone SVG
(pencil, brain, plus, x, trash, restore-arrow, lock) following the `WICONS`
two-tone convention (accent fill @ .16 opacity + currentColor stroke).

**Function inventory (memory.js)**: `renderMemoryPanel(mem)`,
`memFetchList()`, `memOpen(name)`, `memRenderEntries(file)`,
`memRenderFreeform(file)`, `memSave(name, patchFn)` (serializes entries or
content + etag handling + optimistic UI + 409/423 branches),
`memAddFact(text)`, `memEditFact(i, text)`, `memDeleteFact(i)`,
`memCreate(name)`, `memDelete(name)`, `memRestore(trashName)`,
`memConflictBanner(cur)`, `memMeter(used, limit)`, `mem12(ts)`,
`memIcon(kind)`. Panel state in a single `const MEMP = {list:null, sel:null,
dirty:false, etag:null, timer:null}`.

---

## Edge cases & failure modes

**Concurrency & locks**
- Agent holds flock during a save → 423 after ~600 ms of retries; UI
  auto-retry once; never blocks the HTTP thread longer than ~0.7 s.
- Stale zero-byte `USER.md.lock` with no holder → flock succeeds instantly
  (presence ≠ held; flock dies with its process — no stale-lock recovery
  needed, ever).
- Agent writes between our read and our lock: prevented — etag compare
  happens AFTER lock acquisition (save pipeline order).
- Two dashboard tabs / user + Telegram-triggered agent flush racing: loser
  gets 409 with winner's content; winner's pre-image is snapshotted.
- Dashboard restarted mid-write: `os.replace` is atomic on APFS — file is
  old or new, never partial; orphaned `.dashmem_*` temps GC'd at next module
  load.
- `ThreadingHTTPServer` = concurrent handler threads: meta writes serialized
  under `_state_lock`; file writes serialized under per-file flock.

**Agent interplay**
- Agent session loaded its memory snapshot before your edit → that
  conversation may answer from stale memory until its next session load or
  memory-tool op (which re-reads under lock). Documented in UI copy?
  No — too noisy; documented in CLAUDE.md update + acceptance test uses a
  fresh session.
- Agent's session-end flush overwrites a mid-session user edit: possible by
  design (agent re-read picks our edit up only if flush happens after our
  write). The 15 s etag poll surfaces it; our pre-write snapshot + the
  agent's own `.bak` drift path mean no bytes are ever lost.
- Dashboard-written core file must never trip `_detect_external_drift`:
  guaranteed by construction — we strip entries, drop empties, join with
  `"\n§\n"`, and enforce the same char limit the agent budgets against.
  Round-trip identity is asserted in the test plan.
- Entry containing the literal `§` on its own line: would re-split in the
  agent's parser. Server-side guard: reject any entry containing
  `"\n§\n"` or equal to `"§"` → 400 `bad_entry` (matches what the agent's
  own drift check calls "oddly-encoded delimiters").
- Threat-pattern content pasted by the user: we save it verbatim; the agent
  blocks it at load (`[BLOCKED: …]`). Not our layer; noted in Security.

**Files & data**
- File deleted on disk while editor open → save returns 404 → UI offers
  "Re-create with your content" (POST create).
- Agent creates a new topic file while panel open → appears at next list
  refresh (on view switch or after any mutation; plus the 60 s mind_extra
  card).
- Huge file (>512 KB): list shows it with size + "too large to edit here"
  on open (413); never loaded into the DOM.
- Non-UTF8 bytes: 422, refuse editing (never `errors="replace"`-and-save,
  which would corrupt).
- `MEMORY.md` doesn't exist yet (true today): shown as a ghost chip
  ("Agent notes — not created yet"); opening offers create; created via the
  normal create path with entries serialization.
- APFS case-insensitivity: casefold uniqueness at create/restore.
- Name edge cases rejected by regex: leading dots, `/`, `\`, `..`, `.lock`,
  16 KB names, empty, non-`.md`, unicode control chars (regex is an
  allowlist).
- Trash collision (same file deleted twice in one second): epoch suffix per
  delete; if the exact trash name exists, append `-2`.
- Restore target recreated by agent: restores to `<stem>-restored.md`.
- meta JSON corrupted/deleted: `read_json` default `{}` → all provenance
  degrades gracefully to "agent"; nothing errors.
- Disk full: temp write fails → original untouched → 500 with message;
  snapshot failure BEFORE write aborts the save (undo guarantee is not
  optional).
- Clock skew/mtime weirdness: provenance keys off sha1 first, mtime second.

**Environment**
- serve/model down or paused: irrelevant — zero inference in this feature;
  the panel is one of the few things that must work during an outage.
- `memory_api.py` fails to exec (syntax error): server boots fine (try/
  except), `/api/memory/*` returns 503, panel shows its error state, rest of
  dashboard unaffected — same failure envelope as expanders_extra.
- WKWebView stale after deploy: ⌘R required (CLAUDE.md gotcha; in the
  runbook below).

---

## Security & safety

- **Local-first**: pure filesystem CRUD on loopback-only `127.0.0.1:7788`;
  zero network, zero inference, zero data leaving the machine. Works
  offline by construction.
- **Never hard-delete**: delete = move to dashboard trash; the only
  unlink in the feature is trash pruning of already-trashed copies past
  100 files / 20 MB. `USER.md`/`MEMORY.md` refuse deletion server-side
  (403) regardless of what any client sends.
- **Path containment**: allowlist name regex + realpath-prefix check +
  symlink refusal on every path-taking endpoint (list scan also skips
  symlinks). No endpoint accepts a path — only a validated basename.
- **Size/DoS guards**: 128 KB freeform cap, char-limit core cap, 512 KB
  edit ceiling, 500-file cap, body parsed by the existing `_body_json`
  (Content-Length-bounded), lock acquisition bounded at ~600 ms so handler
  threads can't pile up behind the agent.
- **Approvals invariant untouched**: this feature adds no agent tools and
  no new write paths FOR the agent — it's the user's own hands on the
  user's own files. `approvals.mode: manual` and the Gmail/Telegram
  invariants are unaffected.
- **Secrets**: UI hint under the composer: "Memory is injected into the
  model's context — don't store passwords or keys here." We do not scan
  content (the agent's `threat_patterns` layer sanitizes at load); we also
  never log content — recorder lines carry etags and snapshot *paths*, not
  bodies, and snapshots/trash live in `~/.hermes/dashboard/` (0o700, same
  protection class as the memory dir itself).
- **Format fidelity as safety**: entry-aware serialization means the
  dashboard can never put core memory into a state the agent's drift
  detector treats as tampering — the user's editing surface and the agent's
  store cannot drift apart.
- **Must refuse**: core-file delete (403); path traversal / symlinks /
  non-`.md` (400); over-limit saves (400/413); blind overwrites (409 unless
  the client explicitly re-reads); writes while a writer holds the lock
  (423); editing non-UTF8 (422).

---

## Test plan

All from repo root `~/HermesAssistant`. Deploy = restart
dashboard + ⌘R in the app.

```bash
# 0. static checks
python3 -m py_compile dashboard/server.py dashboard/memory_api.py   # → silence
node --check dashboard/memory.js                                     # → silence

# 1. restart + module loaded
launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard && sleep 2
curl -s localhost:7788/api/memory/list | python3 -m json.tool
#   → ok:true, files[0].name=="USER.md", kind entries, core true,
#     char_limit 1375, last_writer "agent", trash []

# 2. read one
curl -s 'localhost:7788/api/memory/file?name=USER.md'
#   → entries ["The codeword is BANANA."], etag E0 (note it)

# 3. edit the fact (E0 from step 2)
curl -s -X POST localhost:7788/api/memory/save -d \
 '{"name":"USER.md","base_etag":"E0","entries":["The codeword is KUMQUAT."]}'
#   → ok:true, last_writer "user", new etag E1
cat ~/.hermes/memories/USER.md          # → exactly: The codeword is KUMQUAT.
stat -f '%Lp' ~/.hermes/memories/USER.md  # → 600

# 4. round-trip fidelity (the drift-detector contract)
python3 -c "raw=open('$HOME/.hermes/memories/USER.md').read();\
p=[e.strip() for e in raw.split('\n§\n') if e.strip()];\
assert raw.strip()=='\n§\n'.join(p), 'DRIFT'; print('round-trip OK')"

# 5. the acceptance test — agent sees the edit (fresh session)
hermes -z "What is the codeword? Answer with just the word."   # → KUMQUAT
ls ~/.hermes/memories/*.bak.* 2>/dev/null                      # → nothing, ever

# 6. stale-etag conflict
curl -s -X POST localhost:7788/api/memory/save -d \
 '{"name":"USER.md","base_etag":"E0","entries":["stale write"]}' -w '\n%{http_code}\n'
#   → 409, error "conflict", current.entries==["The codeword is KUMQUAT."]

# 7. lock contention (hold the agent's flock in bg, then save)
python3 -c "import fcntl,time;f=open('$HOME/.hermes/memories/USER.md.lock','a+');\
fcntl.flock(f,fcntl.LOCK_EX);time.sleep(5)" & sleep 0.3
curl -s -X POST localhost:7788/api/memory/save -d \
 '{"name":"USER.md","base_etag":"E1","entries":["x"]}' -w '\n%{http_code}\n'
#   → 423, error "locked"  (finishes in <1s, well before the 5s holder exits)

# 8. guards
curl -s -X POST localhost:7788/api/memory/delete -d '{"name":"USER.md"}' -w '\n%{http_code}\n'   # → 403 core_file
curl -s -X POST localhost:7788/api/memory/create -d '{"name":"../evil.md","content":""}' -w '\n%{http_code}\n'  # → 400 bad_name
python3 - <<'EOF'      # over_limit: 1376+ chars into USER.md → 400
import json,urllib.request
body=json.dumps({"name":"USER.md","base_etag":"E1","entries":["x"*1400]}).encode()
r=urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:7788/api/memory/save",body),timeout=5)
EOF
#   → HTTP 400, error "over_limit", char_limit 1375

# 9. create → delete → trash → restore round-trip
curl -s -X POST localhost:7788/api/memory/create -d '{"name":"projects.md","content":"# Projects\n- Hermes P1"}'
curl -s -X POST localhost:7788/api/memory/delete -d '{"name":"projects.md"}'   # → trash_name T
ls ~/.hermes/dashboard/memory-trash/                                           # → T present
curl -s -X POST localhost:7788/api/memory/restore -d '{"trash_name":"T"}'      # → ok, name projects.md
diff <(printf '# Projects\n- Hermes P1') ~/.hermes/memories/projects.md        # → identical

# 10. recorder + snapshots (P1.2 contract)
tail -5 ~/.hermes/dashboard/recorder/memory-edits.jsonl | python3 -c \
 "import sys,json;[json.loads(l) for l in sys.stdin];print('jsonl OK')"
ls ~/.hermes/dashboard/snapshots/memory/    # → USER.md.<ts>.md pre-images
# byte-identical undo check: snapshot of step 3's pre-image == "The codeword is BANANA."

# 11. cache bust: memory_files reflects projects.md immediately
curl -s localhost:7788/api/mind_extra | python3 -c \
 "import sys,json;print([f['name'] for f in json.load(sys.stdin)['memory_files']])"

# 12. restore the real fact when done
#     (save entries back to ["The codeword is BANANA."] via the API)
```

**Headless render harness** (the expand.js pattern, catches renderer throws):

```bash
node -e '
const html=[];global.window={lastHub:{}};global.matchMedia=()=>({matches:true});
global.REDUCE=true;global.esc=s=>String(s);global.relTime=()=>"just now";
global.animate=()=>({finished:Promise.resolve()});global.revealStagger=()=>{};
const store={};global.document={getElementById:id=>store[id]||(store[id]={id,innerHTML:"",classList:{add(){},remove(){}},querySelectorAll:()=>[],appendChild(){},style:{},addEventListener(){},remove(){}} ),createElement:t=>({tagName:t,innerHTML:"",style:{},classList:{add(){}},appendChild(){},setAttribute(){},addEventListener(){},remove(){}}),head:{appendChild(){}},querySelectorAll:()=>[]};
global.$=id=>document.getElementById(id);
global.fetch=async u=>({json:async()=>require("child_process").execSync("curl -s http://127.0.0.1:7788"+u.replace(/^http.*7788/,""))+""}).json?0:0;
' # …the real harness lives at dashboard/tools/mem-harness.js (build step writes it):
node dashboard/tools/mem-harness.js
# harness: loads memory.js via new Function(fs.readFileSync(...)), feeds LIVE
# curl'd /api/memory/list + /api/memory/file JSON through renderMemoryPanel,
# asserts: no throw; generated HTML contains "USER.md" and "KUMQUAT";
# conflict branch renders when fed a canned 409 body; empty-list branch
# renders the hint. Exit 0 = pass.
```

**Manual UI pass** (in the app, ⌘R first): edit a fact, add a fact, watch the
char meter, create/delete/restore a topic file, verify confirm() sheet
appears, verify 12-hour timestamps, toggle reduced-motion and re-check, and
run one edit while `python3` holds the flock (banner + auto-retry).

---

## Effort & sequencing

Total ≈ 1.5–2 agent-days. Order:

1. **`dashboard/memory_api.py` + server.py hooks 1–4** (~half day).
   Curl-verifiable without any frontend (test plan steps 0–11 minus UI).
   Build `_mem_lock`/`_mem_atomic_write` first and test against the live
   agent flock protocol before anything else — it's the riskiest seam.
2. **`dashboard/memory.js` + index.html hooks 5–6** (~half day): list +
   entries editor + freeform editor + states.
3. **Trash/restore UI, conflict/lock banners, 15 s etag poll, animations,
   headless harness** (~half day).
4. **E2E acceptance + docs** (~2 h): codeword drill (test steps 3–5),
   CLAUDE.md gets a "Editable memory" bullet + the session-snapshot-staleness
   note; DEVPLAN P1.1 checked off.

Dependencies: **none on other P1 items.** P1.2 (flight recorder) *consumes*
`memory-edits.jsonl` + `snapshots/memory/` — the schemas above are the
contract, so P1.1 should merge before P1.2 starts its recorder lane. P1.3/
P1.4 untouched. Branch per DEVPLAN §7: `feat/p1-editable-memory`, commits
`dash: …`.

---

## Open questions / risks

1. **System-prompt snapshot staleness**: assumed memory loads at session
   create (memory_tool `load_from_disk`); if serve reloads it per turn, the
   acceptance test gets stronger for free. Verify during build by editing
   mid-session and asking in the SAME hub chat; document whichever is true
   in CLAUDE.md.
2. **Upstream pin drift**: `ENTRY_DELIMITER`, char-limit defaults (1375/
   2200), and the drift heuristic are mirrored from hermes-agent v0.18.x.
   Any upstream bump (DEVPLAN risk table: ~1,700 commits/release) must
   re-verify these three against `tools/memory_tool.py` — add that to the
   phase-boundary upgrade checklist.
3. **Topic files and the agent**: the memory tool only manages USER.md/
   MEMORY.md; how topic files get *read* by the agent (skills? file tools?)
   is unverified. If the agent never reads them, the UI copy for topic files
   should say "notes for you, visible to Hermes on request" — check
   `~/.hermes/skills/` for a memory-related skill during build.
4. **`read_memory()` facts vs entries**: server.py line 139 splits USER.md
   by *lines*, so a multi-line entry counts as several "facts" in the hero
   stat. Cosmetic; optionally redefine `read_memory` inside memory_api.py
   (exec order lets us win) to split on `"\n§\n"` — decide at build time,
   zero-risk either way.
5. **Meta `ops` vs recorder duplication**: both exist by design (meta = fast
   provenance lookups; JSONL = P1.2's append-only source of truth). If P1.2
   grows a query API, meta `ops` can shrink to just `last_user_edit`.
6. **Trash pruning is a hard delete** (of trashed copies only, past 100
   files/20 MB). If P1.2 wants infinite undo, bump caps or teach its GC to
   own this dir — flagged for the P1.2 spec.
7. **prompt() for new-file names** relies on the Swift shell's
   `runJavaScriptTextInputPanel` (CLAUDE.md says implemented). If it feels
   clunky, v1.1 swaps to an inline input chip — no API change.
