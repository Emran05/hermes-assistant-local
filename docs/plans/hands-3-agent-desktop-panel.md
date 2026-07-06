# hands-3 — Agent Desktop (dashboard live view)

Spec for the dashboard surface that shows what the Hermes agent is doing on the
Mac desktop and in its sandboxed dev sessions. Third of the "give the agent
hands" trio:

- **hands-1** — sandboxed Claude Code dev-agent (throwaway git worktree +
  `sandbox-exec`, spawn / stream / diff / merge). Owns the `dev-session` backend.
- **hands-2** — direct (non-sandboxed) Mac control that rides the approval +
  flight-recorder rails (open Chrome, play a video, screenshot-and-send).
- **hands-3 (this doc)** — the *read-mostly* dashboard panel that surfaces both:
  a live desktop screenshot stream, the dev-session list + streaming output +
  diff/merge affordance, the flight-recorder computer_use timeline, and a small
  set of controls to kick off a Mac-control task or a dev task.

This panel **owns no dangerous capability of its own**. Every consequential
action it can trigger is delegated to hands-1 / hands-2 endpoints, which enforce
the sandbox, the approval gate, and the recorder. Agent Desktop is a window, not
a hand.

Ground-truth references (grepped 2026-07-05, repo at main `7b0a58e`):
- Aux module system: `dashboard/server.py:2107` `register_get(path, fn)`,
  `:2111` `register_post(path, fn)`; aux files `exec()`'d in sorted order at
  `:2135` (`_AUX_FILES`); GET dispatch `:2280`, `_dispatch_aux` `:2287`
  (**normalises every aux return to JSON — aux routes cannot emit raw bytes**).
- Path constants: `server.py:44` `HERE`, `:45` `HOME`, `:46`
  `DATA = ~/.hermes/dashboard`.
- Access grants: `server.py:950` `ACCESS_FILE = DATA/access.json`, `:953`
  `get_access()`, POST `/api/access` handler `:2313` (`op` add/remove, realpath,
  isdir check).
- Recorder: `dashboard/aux_recorder.py:533` `recorder_record_local(tool,
  target, kind, reversible, source, **kw)`; `computer_use` → kind `"computer"`
  (`TOOL_KIND` `:54`), policy `computer → reversible "no"` (`REVERSIBLE_POLICY`
  `:72`), `UNDO_WHITELIST = {"write","shell"}` (`:78`); GET `/api/recorder`
  handler `:809` (supports `?kind=computer`, `?limit`, `?before`, `?q`,
  `?id=` detail with `diff`/`diff_stat`); `register_get("/api/recorder", …)`
  `:1034`, `register_post("/api/undo", …)` `:1035`.
- Permissions: `dashboard/permissions.py:52` `TIERS = ("auto","ask","never")`,
  resolver `:441`, trusted-clamp `:481` (untrusted source can never resolve to
  `auto`).
- Frontend views: `index.html` has exactly three (`view-hub`, `view-mind`,
  `view-console`); `setView()` `:1008` with `map={hub,mind,console}`; tab
  buttons `:1023`; aux JS injects a card into a view and wraps its loader
  (recorder precedent: `aux_recorder.js:184` `getElementById("view-console")`,
  `:198` `insertBefore(card, host.firstChild)`, `:452` wraps `loadConsole`).
  Aux `<script>` tags `index.html:2086-2099`.
- cua delivers screenshots as base64 (`cua_backend.py:900`
  `screenshot_png_b64`) — there is no on-disk frame cache we can read, so
  hands-3 captures its own thumbnails.
- `/usr/sbin/screencapture` present; `sandbox-exec` verified working (mission
  ground truth).

---

## 1. Goal & acceptance criteria

**Goal.** One dashboard surface where the user can, at a glance, see (a) the
current desktop as the agent sees it, (b) which dev-agent sessions are running /
awaiting review, and (c) the recent computer_use action timeline — and from
which the user can start a Mac-control or dev task and review/merge a dev diff.
Everything local-only; zero new dangerous capability.

**Acceptance criteria**

1. A new **Agent Desktop** surface exists (nav tab `#view-desktop`, with a
   documented widget-only fallback) rendered by `aux_desktop.js`, styled Liquid
   Glass, **zero emoji, 12-hour times**.
2. **Live desktop panel**: shows the newest thumbnail captured to
   `~/.hermes/dashboard/desktop/`, its capture time (12-hour), a "local only —
   never sent anywhere" note, and a Pause/Resume + "Capture now" control.
   Auto-refreshes on the existing dashboard poll cadence.
3. Capture pipeline: a periodic `screencapture -x` writes a downscaled JPEG to
   `~/.hermes/dashboard/desktop/`, files `chmod 0600`, ring-buffer capped
   (count + age), served **only** over loopback as a base64 data URI inside
   JSON. No screenshot byte ever leaves the machine.
4. **Dev sessions panel**: lists hands-1 sessions (id, branch/worktree, status,
   started-at 12-hour), streams the tail of Claude Code output for the selected
   one, renders the diff + stat, and exposes **Review → Merge / Abort** wired to
   hands-1 endpoints (never merges by itself; merge always a deliberate POST).
5. **computer_use timeline**: embeds `/api/recorder?kind=computer` rows
   (reuse `aux_recorder.js` render if loaded, else a local compact renderer),
   each marked irreversible.
6. **Controls**: "Run a desktop task" (→ hands-2) and "Start a dev task"
   (→ hands-1, with a folder picked from `get_access()` dirs + the Hermes repo).
   Both go through the normal approval path; neither bypasses it.
7. **Safety**: capture pauses automatically when `mlx_admission` is refusing is
   NOT required (capture is cheap and needs no model), but the *panel* must show
   dev/cua controls as disabled with a reason when the relevant backend is
   unavailable. No control in this panel can (a) write outside a worktree,
   (b) read a secret, (c) auto-approve an untrusted action, or (d) merge without
   an explicit user click. Proven in the test plan.
8. Panel is fault-isolated: any backend error renders an inline "unavailable"
   state and never throws out of the aux loader (matches the `_AUX_FILES`
   `exec()` try/except at `server.py:2138` and the per-widget `safe()` pattern
   at `/api/widgets`).

---

## 2. Architecture (exact components)

```
                          ┌─────────────────────────────────────────┐
  screencapture -x  ───▶  │ desk_capture_tick()  (aux_desktop.py)    │
  (periodic, xhost)       │  → ~/.hermes/dashboard/desktop/*.jpg 0600│
                          │  → ring-buffer prune (count + age)       │
                          └───────────────┬─────────────────────────┘
                                          │ reads newest
  GET /api/desktop/state ◀────────────────┤  (JSON: latest frame as data URI,
  GET /api/desktop/frame ◀────────────────┘   capture meta, dev/cua availability)
  POST /api/desktop/capture (capture now)
  POST /api/desktop/pause  (pause/resume the tick)

  Dev sessions  ── proxied/read from hands-1 ──▶ /api/dev/*   (CONTRACT §10)
  Mac tasks     ── delegated to hands-2      ──▶ /api/mac/*   (CONTRACT §10)
  Timeline      ── reuse existing            ──▶ /api/recorder?kind=computer

                          ┌─────────────────────────────────────────┐
  Browser (loopback)  ◀── │ aux_desktop.js  →  #view-desktop         │
                          │  live frame · dev sessions · timeline ·  │
                          │  controls (Liquid Glass, 12h, no emoji)  │
                          └─────────────────────────────────────────┘
```

Components authored by hands-3:

- `dashboard/aux_desktop.py` — capture tick (background thread, started once at
  import like other aux modules), ring-buffer pruning, and the
  `/api/desktop/*` routes. Consumes `recorder_record_local` to log each capture
  as a `read`-kind action (`source="dashboard"`, target `desktop-frame`).
- `dashboard/aux_desktop.js` — the Agent Desktop surface. Registered as a
  `<script>` in `index.html` after `aux_recorder.js` so it can reuse
  `renderRecorderRows`, `esc`, `relTime`, `animate`, `REDUCE`.
- `index.html` edits (worktree-built, user-merged): add `desktop` to
  `setView()`'s `map`, add a `tab-desktop` button, add an empty
  `<div class="view" id="view-desktop" role="tabpanel" hidden>` container.
- Skill(s): `agent-desktop` recipe (see §6).

Not authored here (consumed): hands-1 `/api/dev/*`, hands-2 `/api/mac/*`,
existing `/api/recorder`.

---

## 3. Data model

**On-disk capture store** — `~/.hermes/dashboard/desktop/` (dir created 0700):

- `frame-<epoch_ms>.jpg` — downscaled JPEG (target long-edge ≤ 1280px, quality
  ~55) written 0600. `screencapture -x -t jpg` (no shutter sound; `-x`); if a
  further downscale is wanted use `sips -Z 1280` on the captured file (both
  ship on macOS, no external dep).
- `meta.json` (0600) — `{ "paused": bool, "interval_s": int, "last_ts":
  float, "last_file": str, "count": int, "err": str }`. Single small file the
  tick rewrites atomically (temp + rename), read by the state endpoint.

**Ring-buffer policy** (constants at top of `aux_desktop.py`):
`DESK_MAX_FRAMES = 20`, `DESK_MAX_AGE_S = 900` (15 min), `DESK_INTERVAL_S = 6`
default (only ticks while the panel has been polled recently — see §4 idle
gate). Prune runs every tick: delete oldest beyond count, delete anything older
than max-age.

**Recorder rows** — captures logged via `recorder_record_local("computer_use"?
no)`: capture is a *read* of the screen, so log as
`recorder_record_local("screencapture", "desktop-frame", kind="read",
reversible="no", source="dashboard", summary="desktop thumbnail (local only)")`.
Rate-limited: log at most one capture row per ~60s to avoid flooding the
timeline (the frames themselves are the record; the recorder row is provenance).

**No new schema.** Dev-session and Mac-task state live in hands-1 / hands-2;
hands-3 stores nothing about them.

---

## 4. Backend — aux module + endpoints (exact names)

File: `dashboard/aux_desktop.py`. Follows the aux contract: no edits to
`server.py`; registers routes via `register_get` / `register_post`; every
handler returns JSON (or `(obj, status)`); never raises out of import.

Constants / paths (reuse the module globals injected by `exec()` — `DATA`,
`HOME`, `read_json`, `write_json`, `_state_lock`, `recorder_record_local`):

```
DESK_DIR       = os.path.join(DATA, "desktop")          # ~/.hermes/dashboard/desktop
DESK_META      = os.path.join(DESK_DIR, "meta.json")
DESK_MAX_FRAMES, DESK_MAX_AGE_S, DESK_INTERVAL_S = 20, 900, 6
DESK_IDLE_STOP_S = 30      # stop capturing if the panel hasn't polled in 30s
DESK_LONG_EDGE, DESK_JPEG_Q = 1280, 55
```

**Endpoints**

- `GET /api/desktop/state` → `register_get`
  Returns capture metadata + backend availability, **no image bytes**:
  ```
  { "ok": true,
    "paused": false, "interval_s": 6,
    "last_ts": 1751745123.4, "count": 7,
    "capturable": true,            # screencapture present & not erroring
    "note": "Captures are stored locally only and never sent anywhere.",
    "dev_available": true,         # hands-1 reachable (probe, cached 5s)
    "mac_available": true,         # hands-2 reachable
    "recorder_available": true }
  ```
  Side effect: stamps a `last_polled` timestamp used by the idle gate (so the
  capture tick only runs while someone is watching).

- `GET /api/desktop/frame?n=0` → newest (n=1 previous, …). Returns the frame as
  a **data URI in JSON** (the dispatcher can only emit JSON — this is by
  design, keeps it loopback-only and mirrors cua's `screenshot_png_b64`):
  ```
  { "ok": true, "ts": 1751745123.4,
    "data_uri": "data:image/jpeg;base64,…", "file": "frame-...jpg" }
  ```
  Reads only from `DESK_DIR`; `os.path.realpath` + prefix-check so `n`/any param
  can never escape the dir. Missing → `{ "ok": false, "reason": "no frames" }`.

- `POST /api/desktop/capture` → capture one frame now (bounded; respects the
  same downscale + 0600 + prune). Returns new `state`.

- `POST /api/desktop/pause` (body `{"paused": true|false}`) → toggle the tick;
  persists to `meta.json`.

- `GET /api/desktop/dev` (thin, cached proxy of hands-1 `/api/dev/sessions`) and
  `GET /api/desktop/dev/detail?id=…` (proxy of hands-1 session detail + diff).
  **Proxy-only, read-only**: hands-3 forwards, never mutates. Mutations
  (spawn/merge/abort) are POSTed by the frontend **directly to hands-1's own
  endpoints**, so hands-1 owns approval/sandbox and hands-3 holds no write path.
  If hands-1 is not yet merged, these return `{ "ok": false, "reason":
  "dev-agent backend not installed" }` and the panel shows the disabled state.

**Capture tick** — `desk_capture_tick()` on a daemon thread started at import
(guarded by a module-level `_desk_started` flag, like other aux background
work). Loop: if `paused` or `now - last_polled > DESK_IDLE_STOP_S`, sleep and
continue (idle gate — no captures when nobody is looking). Else run
`screencapture -x -t jpg` to a temp path, `sips -Z DESK_LONG_EDGE`, `chmod
0600`, atomic-rename into `DESK_DIR`, prune, update `meta.json`, rate-limited
recorder row. Every failure is caught and written to `meta["err"]`; the thread
never dies.

**Why no core-server edit for image serving:** `_dispatch_aux`
(`server.py:2287`) JSON-encodes every aux return. Rather than patch the core
handler to stream `image/jpeg`, hands-3 ships the frame as a base64 data URI in
JSON. This keeps hands-3 entirely within the additive aux contract (no risk to
the running hub) and keeps frames on the loopback JSON path only.

---

## 5. Frontend — the Agent Desktop surface

File: `dashboard/aux_desktop.js`. Mirrors `aux_recorder.js` conventions: a
top-level render function callable headless, inline styles injected once, all
strings through `esc()`, 12-hour times via a local `deskClock()`
(`toLocaleString("en-US", {hour:"numeric", minute:"2-digit", hour12:true})`),
bespoke two-tone SVG glyphs (no emoji), `animate`/`REDUCE` honored.

**Preferred integration — a real nav tab (`#view-desktop`).** Requires three
small `index.html` edits (all inside the worktree, user-merged):
1. `setView()` map (`:1010`): `{hub:'view-hub', mind:'view-mind',
   console:'view-console', desktop:'view-desktop'}`, and
   `if(v==='desktop')loadDesktop();`.
2. A `tab-desktop` button next to `tab-console` (`:1023`) wired
   `$('tab-desktop').onclick=()=>setView('desktop')`.
3. `<div class="view" id="view-desktop" role="tabpanel" hidden></div>` beside
   the other views (`:829`+).
`aux_desktop.js` then fills `#view-desktop` and defines `loadDesktop()`.

**Fallback (zero index.html edits)** — if we choose not to touch the core
template: inject a large "Agent Desktop" card at the top of `#view-console`
(exactly the recorder pattern, `insertBefore(card, host.firstChild)`) and wrap
`loadConsole` so it rides the existing 3s poll. Same content, lives under
Console. Decide in §11.

**Panel layout** (single column, Liquid Glass `--glass` / `--glass-2` /
`--hairline` / `--ground` / `--faint` tokens already in `index.html`):

1. **Live desktop** card: the newest frame (`<img>` with the data URI,
   `max-width:100%`), capture time + "N frames buffered", a subtle
   **"Local only — captures never leave this Mac"** line, and Pause/Resume +
   "Capture now" buttons. A small strip of the last few thumbnails (click to
   pin an older frame). If `capturable:false`, show the reason (e.g. Screen
   Recording permission not granted) instead of a broken image.
2. **Dev sessions** card: rows from `/api/desktop/dev` (id, branch, status
   pill, started 12-hour). Selecting a row streams its Claude Code output tail
   and shows the diff/stat. **Review → Merge** and **Abort** POST directly to
   hands-1. Merge shows a confirm ("merges branch X into main; live hub restarts
   on your action"). Disabled with a reason when `dev_available:false`.
3. **Desktop activity** card: the computer_use timeline. If `renderRecorderRows`
   is defined (aux_recorder.js loaded first), call it with
   `/api/recorder?kind=computer` rows; else a compact local list. Each row
   carries the irreversible marker.
4. **Controls** card: "Run a desktop task" (free-text → POST hands-2
   `/api/mac/task`) and "Start a dev task" (free-text + a folder `<select>`
   populated from the granted dirs + `~/HermesAssistant` → POST hands-1
   `/api/dev/spawn`). Both surface the returned approval state; neither is a
   fire-and-forget bypass.

Polling: `loadDesktop()` hits `/api/desktop/state`; if not paused and a frame
is newer than the shown one, swaps the `<img>`. Rides the existing dashboard
cadence; no new timer if integrated into an existing view's loader.

---

## 6. Skill(s) to author

Skills live in `~/.hermes/skills/<cat>/<name>/SKILL.md` (+ `scripts/`). hands-3
authors one, referencing (not duplicating) hands-1/hands-2 skills.

**`ops/agent-desktop/SKILL.md`** — "See and drive the Agent Desktop panel."
Recipes:
- *Show me the desktop / what's on screen now*: `POST /api/desktop/capture`
  then read `/api/desktop/frame?n=0`; describe. Note it is local-only.
- *What are my dev agents doing?*: read `/api/desktop/dev`, summarize statuses;
  for a session, read `.../dev/detail?id=` and summarize the diff. Never merge
  on the user's behalf without an explicit instruction; even then, state that
  merge restarts the live hub and route it through the approval path.
- *Recent desktop actions*: read `/api/recorder?kind=computer`.
- Privacy guardrail block: captures are 0600, ring-buffered, loopback-only,
  auto-pruned; the skill must never copy a frame elsewhere, attach it to a
  Telegram/Gmail send, or paste its bytes into a reply — it references the
  frame, it does not exfiltrate it.
- Cross-refs: for *doing* a desktop action use hands-2's Mac-control skill; for
  *starting/merging* code work use hands-1's dev-agent skill. This skill is the
  read/observe layer.

`scripts/` may include a tiny `desktop_state.sh` (curl loopback
`/api/desktop/state` and pretty-print) for the agent's terminal tool.

---

## 7. Safety model (invariant by invariant)

**INV-1 — Self-upgrade must never break the running assistant.**
hands-3 never edits the live install and never merges. Merge/abort are POSTs the
*user* triggers, handled by hands-1, which does the worktree→diff→user-merge
dance. The panel's only write to the live tree is the merge click, and that is
hands-1's guarded endpoint, not hands-3 code. *Cannot happen:* a background
poll or a rogue model reply silently merging — the merge path requires a
deliberate POST originated by a user click; hands-3 exposes no auto-merge timer.

**INV-2 — Sandboxed Claude Code writes only inside its worktree.**
hands-3 spawns no Claude Code and grants no write scope. It proxies hands-1
read endpoints and forwards the user's spawn request; the `sandbox-exec`
containment is entirely hands-1's. *Cannot happen:* hands-3 widening the sandbox
— it has no code that constructs a sandbox profile or a `--dangerously-skip-
permissions` command line.

**INV-3 — Never expose/read/exfiltrate secrets** (`~/.hermes/.env`,
`serve-token`, `google_token.json`, `messages-token`).
The capture pipeline reads only the screen bitmap via `screencapture` and writes
only under `DESK_DIR`. The frame endpoint is realpath-pinned to `DESK_DIR` — no
param can traverse to a secret. Dev-detail is a proxy of hands-1's already-
redacted output; hands-3 does not itself read repo files or env. Frames are
served only over loopback in JSON and marked "local only"; the skill forbids
attaching a frame to any send. *Residual risk:* a secret typed on screen could
appear in a thumbnail — mitigated by 0600, 15-min/20-frame auto-prune, loopback-
only, no external send, and the option to Pause. Documented in §8.

**INV-4 — Consequential Mac actions still surface through approval + recorder.**
hands-3's "Run a desktop task" is a thin POST to hands-2, which owns the
approval gate and records each computer_use action irreversible. hands-3 itself
records every *capture* as a `read`-kind recorder row. *Cannot happen:* a
consequential action firing without a recorder row or approval — hands-3 has no
computer_use capability; it can only ask hands-2, which gates.

**INV-5 — No `--yolo` of the Hermes approval gate; Gmail send never; Telegram
locked to the one user; local-first.**
hands-3 sends nothing and calls no Gmail/Telegram path. The controls delegate to
hands-1/hands-2 which keep `approvals.mode: manual` in front of them (see
`permissions.py` layering note `:9`). The trusted-source clamp
(`permissions.py:481`) means even a task hands-3 forwards can never resolve to
`auto` for an untrusted origin.

**INV-6 — Respect the memory ceiling (`mlx_admission`).**
Capture uses `screencapture`/`sips` only — no local model, negligible RAM, safe
under memory pressure. The panel does not itself launch dev-agent/cua work; when
it forwards a start-request, hands-1/hands-2 apply their own admission backoff.
The idle gate (no capture unless the panel was polled in the last 30s) keeps the
pipeline near-zero-cost when unwatched.

**INV-7 — Fault isolation.**
Every route is try/except → JSON error; the capture thread swallows all
exceptions into `meta["err"]`; the aux file is `exec()`'d under the
`server.py:2138` try/except so a syntax/runtime fault prints and is skipped
rather than taking the hub down. *Cannot happen:* Agent Desktop crashing the
live dashboard.

---

## 8. Edge cases

- **Screen Recording (TCC) not granted** → `screencapture` yields a black/empty
  or errored frame. Detect (nonzero exit or tiny file) → `capturable:false` +
  reason; panel shows a "grant Screen Recording to Terminal/Hermes" note, not a
  broken image.
- **Multi-display** → `screencapture -x` grabs the main display by default;
  document that only the main display is thumbnailed in v1 (cua `switch_display`
  exists but multi-display capture is an open question, §11).
- **Sensitive content on screen** (passwords, DMs) → mitigations in INV-3;
  Pause is one click; frames auto-prune in ≤15 min.
- **hands-1 / hands-2 not yet merged** → probes return unavailable; dev/control
  cards render disabled with a clear reason; capture + timeline still work.
- **Large diff** → hands-1 already caps diff at ~20KB (`aux_recorder.py:801`
  precedent); render with an `overflow-x:auto` scroll container, never widen the
  page.
- **Rapid captures flooding the recorder** → capture-row rate-limit (~1/min);
  frames are the record, rows are provenance.
- **Disk growth** → ring-buffer count+age prune every tick; JPEG q55 long-edge
  1280 keeps each frame well under ~200KB; ≤20 frames ⇒ a few MB max.
- **Data-URI size in JSON** → one frame per state poll only when newer; the
  thumbnail strip uses smaller frames or lazy-fetches by `n`.
- **Reduced motion** → honor `REDUCE` (frame swaps without animation).
- **Clock** → all times 12-hour, matching the recorder/hub.

---

## 9. Test plan (all safe; no real destructive op, no `--yolo`, no live merge)

1. **Aux loads clean**: start a scratch-port dashboard from a worktree; confirm
   `[aux_desktop.py] failed to load` does *not* appear; `/api/desktop/state`
   returns `ok:true`.
2. **Capture + prune**: `POST /api/desktop/capture` a few times; assert files
   appear in `DESK_DIR` **mode 0600**, count never exceeds `DESK_MAX_FRAMES`,
   files older than `DESK_MAX_AGE_S` are gone. `GET /api/desktop/frame?n=0`
   returns a valid `data:image/jpeg;base64,` URI.
3. **Frame path can't escape** (path-traversal): call `frame` with hostile
   `n`/injected params and assert the realpath-prefix check refuses anything
   outside `DESK_DIR`; assert no route reads `~/.hermes/.env` or any token.
4. **Loopback only**: confirm the server binds loopback (existing behavior) and
   that no code path emails/Telegrams/uploads a frame; grep the module for any
   send/`urlopen`/socket to a non-loopback host — expect none.
5. **Idle gate**: stop polling `/api/desktop/state` for > `DESK_IDLE_STOP_S`;
   assert `last_ts` stops advancing (no captures while unwatched); resume and
   assert it restarts.
6. **Recorder provenance**: after captures, `GET /api/recorder?kind=read`
   (or filter) shows `screencapture`/`desktop-frame` rows, rate-limited.
7. **Fault isolation**: inject a raising handler / corrupt `meta.json`; assert
   the endpoint returns a JSON error and the hub stays up; corrupt-load the aux
   file and confirm the `exec()` try/except skips it.
8. **Prove the sandbox blocks an escape** (delegated, dry): drive hands-1's
   test harness under `sandbox-exec` — have the spawned Claude Code attempt a
   write **outside** the worktree (e.g. touch `~/.hermes/.env` or
   `~/HermesAssistant/dashboard/server.py`) and assert it is **denied** by the
   profile while a write **inside** the worktree succeeds. Agent Desktop then
   shows that session as failed/blocked with the denial surfaced — proving the
   panel observes but never widens containment. (No real merge; scratch branch.)
9. **No auto-merge**: verify there is no timer/poll path that POSTs merge; merge
   only fires from an explicit button click and shows the confirm.
10. **Backend-absent UX**: with hands-1/hands-2 stubbed unreachable, assert the
    dev/controls cards render disabled with reasons and the panel still shows
    frames + timeline.
11. **Frontend**: headless-render `renderDesktop(state, frames, sessions)` →
    non-empty HTML, zero emoji (assert no emoji codepoints), 12-hour times,
    page body does not scroll horizontally (diff/timeline in
    `overflow-x:auto`).

---

## 10. Coordination contract with hands-1 / hands-2

hands-3 consumes these; names to be agreed with those specs (proposed, aligned
to the `/api/<area>/...` convention already used by `/api/recorder`,
`/api/access`):

**From hands-1 (dev-agent) — hands-3 reads, and the frontend POSTs directly:**
- `GET  /api/dev/sessions` → `[{id, branch, worktree, status, started_ts,
  title}]`.
- `GET  /api/dev/session?id=` → `{…, output_tail, diff, diff_stat, blocked}`.
- `POST /api/dev/spawn` `{task, folder}` → `{id, approval}` (folder must be one
  of `get_access()` dirs or `~/HermesAssistant`; hands-1 validates).
- `POST /api/dev/merge` `{id}` and `POST /api/dev/abort` `{id}` — user-clicked.

**From hands-2 (Mac control):**
- `POST /api/mac/task` `{task}` → `{job, approval}` (rides approval + recorder).

hands-3 provides thin cached read-proxies (`/api/desktop/dev*`) so the panel has
one origin, but never proxies the mutating POSTs — those hit hands-1/hands-2
directly so approval/sandbox stay with the owner.

---

## 11. Sequencing

1. `aux_desktop.py` capture pipeline + `/api/desktop/state|frame|capture|pause`
   (self-contained; testable with no hands-1/2). Ship + test §9.1–9.7 first.
2. `aux_desktop.js` live-frame card + `#view-desktop` (or fallback card) +
   timeline embed. Test §9.11.
3. Wire dev-sessions read-proxy + controls behind availability probes (works
   disabled until hands-1/2 land). Test §9.10.
4. Integrate with hands-1 once its endpoints exist; run the delegated
   sandbox-escape test §9.8 end-to-end.
5. Author the `agent-desktop` skill (§6).
6. Decide nav-tab vs fallback-card (§11 open) and finalize `index.html` edits.

Each step is additive and independently revertible; nothing before step 4 needs
hands-1/hands-2 merged.

---

## 12. Open questions

- **Nav tab vs. widget card**: a real `#view-desktop` tab is cleaner but edits
  the core `index.html` (`setView`, tab bar, view container). The recorder-style
  injected card needs zero core edits. Recommend the tab for a first-class
  surface, but gate on the user's tolerance for `index.html` churn.
- **Multi-display**: v1 captures the main display only. Worth capturing all
  displays (montage) or letting the panel pick via cua `switch_display`?
- **Frame delivery**: data-URI-in-JSON keeps hands-3 fully additive but is
  slightly heavier than a raw `image/jpeg` route. If the core server later grows
  a sanctioned static/binary aux hook, migrate the frame endpoint to it.
- **Retention knobs**: expose `DESK_MAX_FRAMES` / `DESK_MAX_AGE_S` / interval in
  the config-as-code surface (`aux_config.py`) or keep them constants?
- **Recorder linkage**: link a computer_use timeline row to the exact frame
  captured nearest its `ts` (nice provenance) — needs a ts-join; defer to v2.
- **Should capture pause automatically** when a known-sensitive app (password
  manager, banking) is frontmost? Possible via cua window title; privacy-positive
  but adds a dependency — flag for the user.
