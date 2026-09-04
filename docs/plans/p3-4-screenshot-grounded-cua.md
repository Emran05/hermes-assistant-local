# Screenshot-grounded computer-use — design spec (P3)

DEVPLAN Phase 3 item #5 ("Screenshot-grounded computer-use", opportunity row
#14). Every mutating `computer_use` action gets a **before** and **after**
window screenshot archived into the flight recorder; the Console recorder lane
renders them as a two-frame evidence strip on the existing `computer` rows
(which P1.2 already records and truthfully marks `irreversible`). DEVPLAN's
done-bar: *"a recorded action shows before/after frames in the Console
scrubber."*

All facts below were verified against the live tree on 2026-07-05:
`dashboard/server.py` (2458 lines, aux-route registry at ~2039),
`dashboard/aux_recorder.py` (1048 lines), `dashboard/aux_recorder.js` (459
lines), `dashboard/permissions.py`, `dashboard/aux_metrics.py`,
`~/.hermes/hermes-agent` (v0.18.x checkout: `agent/shell_hooks.py`,
`model_tools.py`, `tools/computer_use/{tool,schema,backend,cua_backend}.py`,
`hermes_state.py`, `run_agent.py`, `tui_gateway/server.py`,
`website/docs/user-guide/features/hooks.md`), `/Applications/CuaDriver.app`.

---

## The critical design decisions (and the verified facts that force them)

### 1. WHO takes the screenshot: the cua-driver, via an upstream shell hook — not `screencapture`, not the dashboard

**Post-hoc recovery is impossible.** `run_agent.py` `_persist_session()`
(~line 1841) explicitly strips images before writing state.db: *"Persist
multimodal tool results as their text summary only — base64 images would
bloat the session DB"*; image parts become the literal string `[screenshot]`.
So the P1.2 reconciler leg can never backfill frames — they must be captured
**at action time**, in the agent's process, on every surface.

**The upstream interposition point exists and is supported.** hermes-agent
ships a **shell-hook system** (`website/docs/user-guide/features/hooks.md`
§"Shell Hooks", implementation `agent/shell_hooks.py`): a `hooks:` block in
`~/.hermes/config.yaml` runs an arbitrary script on `pre_tool_call` /
`post_tool_call`, with a `matcher` regex **fullmatch**ed against the tool name
(`shell_hooks.py` line ~193: `compiled_matcher.fullmatch(tool_name)`). The
script gets a JSON payload on stdin:

```json
{"hook_event_name": "pre_tool_call", "tool_name": "computer_use",
 "tool_input": {"action": "click", "element": 7, "app": "TextEdit"},
 "session_id": "…", "cwd": "…",
 "extra": {"task_id": "…", "tool_call_id": "call_abc123", "turn_id": "…"}}
```

`extra.tool_call_id` is documented for both events (`shell_hooks.py` docstring
lines ~69/77) — **the exact same id P1.2 keys `recorder.db.actions.tool_call_id`
on**. `post_tool_call`'s extra additionally carries `result`, `status`
("ok"|"error"|"blocked"), `duration_ms`. Hooks run as a **synchronous
subprocess** (default timeout 60 s, we set 15): the `pre_tool_call` hook
finishes *before the tool executes* — the "before" frame is race-free by
construction, the same property that made upstream checkpoints the right
snapshot source in P1.2. Malformed output, non-zero exit, and timeouts "log a
warning but never abort the agent loop" — fail-open for the agent.

**Registration covers every surface.** `register_from_config(load_config())`
is called in `hermes_cli/main.py` CLI startup (line ~12376 — so `hermes
serve`, `hermes -z`, interactive CLI) and `gateway/run.py` (line ~6693 — the
Telegram gateway). Hub, Telegram, and CLI turns all fire the hook.

**TCC reality.** Screen-capture TCC attaches to the process that calls the
capture API:

- The dashboard's launchd python3 (`com.hermes.dashboard`, Framework Python
  3.12) has **no** Screen Recording grant. `screencapture -x` under launchd
  fails or produces a prompt nobody sees; macOS 15+ additionally re-nags
  monthly for non-App-Store capture. Dead end — P1.2 open question #1 already
  called this.
- **CuaDriver.app (`com.trycua.driver`) already holds Screen Recording +
  Accessibility** (CLAUDE.md "Computer use"; `hermes computer-use doctor`),
  and background per-window capture demonstrably works from these same
  launchd services today — every agent `capture` action proves it.
- Therefore the hook captures **through the driver**, using the identical
  plumbing the agent uses: `tools/computer_use/cua_backend.py`
  `CuaDriverBackend()` → MCP over stdio (`cua-driver mcp`,
  `_CUA_DRIVER_CMD = os.environ.get("HERMES_CUA_DRIVER_CMD", "cua-driver")`,
  line ~76). Binary verified: symlink
  `~/.local/bin/cua-driver → /Applications/CuaDriver.app/Contents/MacOS/cua-driver`.
  The hook sets `HERMES_CUA_DRIVER_CMD=/Applications/CuaDriver.app/Contents/MacOS/cua-driver`
  explicitly so launchd PATH differences can never bite.
  **Zero new TCC grants. No NEEDS-YOU item.**

### 2. WHERE frames meet the recorder: a shots directory joined by `tool_call_id`, attached by a dashboard-side pass

The hook runs in the agent's processes; the recorder lives in the dashboard
process. They meet on disk: the hook writes
`~/.hermes/dashboard/cua-shots/<tcid>.{before,after}.jpg` + `<tcid>.meta.json`,
and a new aux module in the dashboard attaches them to the matching
`actions` row (INSERTed by P1.2's ws leg or reconciler — whichever lands
first; the join is order-independent). No cross-process SQLite writes from
the hook, no new IPC.

### 3. Aux-module purity: zero edits to aux_recorder.py, one script tag in index.html

`server.py` exec-loads `aux_*.py` in sorted order (line ~2071) —
`aux_shots.py` sorts **after** `aux_recorder.py`, so at load time it can see
recorder globals (`_rec_conn`, `_rec_lock`, `REC_DB`, `TOOL_KIND`) and,
crucially, can **re-register** `GET_ROUTES["/api/recorder"]` with a wrapping
handler (`register_get` is a plain dict assignment — last writer wins), the
same "loaded last so its assignments win" pattern expand.js and aux JS already
use. Aux routes are JSON-only (`Handler._dispatch_aux` normalises every return
through `self._json`) — so images travel as base64 data-URIs inside JSON,
lazily fetched; no binary route, no server.py edits.

---

## Goal & acceptance criteria

Done means:

1. **Every mutating `computer_use` action** (any `action` outside the safe set
   `{capture, wait, list_apps}` — mirrors `_SAFE_ACTIONS`,
   `tools/computer_use/tool.py` line 80) that executes on **any** surface
   (hub, Telegram, CLI) produces a before frame and an after frame in
   `~/.hermes/dashboard/cua-shots/`, keyed by `tool_call_id`, within the
   action's own wall-clock (pre-hook blocks until the before frame is on
   disk).
2. Within 20 s of the action row existing in `recorder.db`, its `shots`
   column is populated and `GET /api/recorder` lists the row with
   `"shots": {"before": true, "after": true}`.
3. The Console recorder lane shows a two-frame thumbnail strip on `computer`
   rows; expanding the row shows labelled BEFORE/AFTER frames side by side
   with capture timestamps; clicking a frame opens a full-size overlay.
   (DEVPLAN's "Console scrubber" done-bar.)
4. Safe actions (`capture`/`wait`/`list_apps`) produce **no** shots (no
   doubled screenshots, no wasted disk).
5. A **denied** approval still yields a coherent record: before frame present,
   after frame present, detail pane shows the action's `status` from the
   hook's `extra` ("blocked"/"error") — evidence that nothing happened.
6. Fail-open proven: with CuaDriver.app renamed away, computer_use actions
   still execute normally (hook exits fast, agent unaffected); rows show
   "no frames captured" in the detail pane, never an error that blocks a turn.
7. Undo semantics unchanged: `/api/undo` on a `computer` row still returns
   `{"ok":false,"error":"irreversible","detail":"computer-use actions cannot
   be undone"}` (aux_recorder.py `_irreversible_detail`). Shots are evidence,
   never a restore source.
8. GC proven: shots older than the TTL and shots beyond the size cap are
   removed by the hourly sweep; `du -sh` on the shots dir stays under the cap
   after a synthetic flood.
9. Static gates pass: `python3 -m py_compile` on `aux_shots.py` and the hook
   driver, `node --check dashboard/aux_shots.js`, the headless renderer
   harness on aux_shots.js with live `/api/recorder` JSON, `hermes hooks
   doctor` clean, and a dashboard restart + ⌘R leaves Hub/Mind/Console fully
   working.

---

## Data model

### 1. Shots store — `~/.hermes/dashboard/cua-shots/` (dir 0700, files 0600)

Written by the hook (agent processes), read/GC'd by the dashboard. Flat dir,
filename-keyed — atomic, no index to corrupt:

```
<tcid>.before.jpg      # sanitized tool_call_id: re.sub(r'[^A-Za-z0-9._-]','_',tcid)[:80]
<tcid>.after.jpg
<tcid>.meta.json       # written last (tmp+rename) => presence signals "pair complete"
```

`meta.json` (v1):

```json
{"v": 1, "tool_call_id": "call_abc123", "session_id": "…",
 "action": "click", "app": "TextEdit", "window_title": "Untitled",
 "before": {"ok": true,  "w": 1512, "h": 945, "bytes": 214301,
            "ts": 1751692347.1, "capture_ms": 1420},
 "after":  {"ok": true,  "w": 1512, "h": 945, "bytes": 220118,
            "ts": 1751692349.4, "capture_ms": 1180,
            "status": "ok", "tool_duration_ms": 812},
 "error": ""}
```

Image policy (enforced by the hook driver with the venv's Pillow — verified
`PIL 12.2.0` importable in `~/.hermes/hermes-agent/venv`):
- full frame: JPEG quality 60, long edge downscaled to ≤1440 px
  (typical 150–400 KB; hard per-file cap 1.5 MB — above it, requality to 40);
- thumbnail: **derived on demand** by the dashboard (Pillow-free: the detail
  endpoint serves the full frame; the lane thumb is the same file rendered
  small — see Frontend; no second file on disk, half the GC surface).

Retention (constants in `aux_shots.py`): `SHOT_TTL = 14*86400` (matches
P1.2's undo-trash TTL), `SHOT_STORE_CAP = 500*1024*1024` oldest-first
(matches the checkpoint store's cap), GC hourly.

### 2. `recorder.db` — one new column, lazy-migrated

```python
# aux_shots.py, at load, guarded:
try:
    con.execute("ALTER TABLE actions ADD COLUMN shots TEXT DEFAULT ''")
except sqlite3.OperationalError:
    pass   # already migrated
```

`shots` holds JSON once attached:

```json
{"before": {"file": "call_abc123.before.jpg", "w": 1512, "h": 945,
            "bytes": 214301, "ts": 1751692347.1},
 "after":  {"file": "call_abc123.after.jpg", "w": 1512, "h": 945,
            "bytes": 220118, "ts": 1751692349.4},
 "action": "click", "app": "TextEdit", "status": "ok", "note": ""}
```

Partial capture is representable (`before` only / `after` only) and rendered
honestly. `note` carries failure reasons ("window closed before after-frame",
"driver unavailable").

### 3. Config — `hooks:` block in `~/.hermes/config.yaml` + allowlist

```yaml
hooks:
  pre_tool_call:
    - matcher: "computer_use"          # fullmatch — exactly this tool
      command: "~/.hermes/agent-hooks/cua-shot.py"
      timeout: 15
  post_tool_call:
    - matcher: "computer_use"
      command: "~/.hermes/agent-hooks/cua-shot.py"
      timeout: 15
```

Consent: **do NOT set `hooks_auto_accept: true`** (blanket future-hook
consent — wrong posture). Instead merge two explicit entries into
`~/.hermes/shell-hooks-allowlist.json` in the documented format (docs
§"Manual allowlisting" — an `approvals` array of `{event, command}` pairs;
the command string must match exactly; a sha256-keyed object is explicitly
NOT accepted):

```json
{"approvals": [
  {"event": "pre_tool_call",  "command": "~/.hermes/agent-hooks/cua-shot.py"},
  {"event": "post_tool_call", "command": "~/.hermes/agent-hooks/cua-shot.py"}
]}
```

Merge (read-modify-write), never clobber. Both files ride the P1.6
config-as-code flow (diffed, secret-scanned, committed) so the change is
reviewable — never a silent hand-hack. Restart `com.hermes.serve` and the
gateway to register (docs: restart needed for channels to pick up new hooks).

---

## Backend

### A. The hook driver — NEW `~/.hermes/agent-hooks/cua-shot.py` (~180 lines)

One script, both events (it branches on `hook_event_name` from stdin — one
command string means exactly two allowlist entries). Shebang:
`#!~/.hermes/hermes-agent/venv/bin/python` (the venv has
Pillow + the checkout's deps; the dashboard's stdlib-only rule does not apply
here — this runs in agent-land, same as the P1.2 checkpoint driver).

Flow:

```
read stdin JSON → payload
if tool_name != "computer_use": print {}; exit           # belt: matcher already filters
action = payload["tool_input"].get("action")
if action in {"capture","wait","list_apps"}: print {}; exit   # safe set — no evidence needed
tcid = sanitize(payload["extra"]["tool_call_id"])         # documented extra key
phase = "before" if hook_event_name == "pre_tool_call" else "after"
if phase == "after": time.sleep(0.5)                      # let the UI settle
os.environ.setdefault("HERMES_CUA_DRIVER_CMD",
    "/Applications/CuaDriver.app/Contents/MacOS/cua-driver")
sys.path.insert(0, "~/.hermes/hermes-agent")
from tools.computer_use.cua_backend import CuaDriverBackend, cua_driver_binary_available
if not cua_driver_binary_available(): write_meta(error="driver unavailable"); print {}; exit
cap = CuaDriverBackend().capture(mode="vision", app=payload["tool_input"].get("app"))
  # mode="vision" = plain window PNG, no AX walk (schema.py: "vision is a
  # plain screenshot") — captures the target app's window if the model named
  # one, else the frontmost window; per-window, background, no focus steal
decode png_b64 → Pillow → downscale ≤1440, JPEG q60 → tmp file → os.rename
if phase == "after": merge {status, tool_duration_ms} from extra; write meta.json (tmp+rename)
print "{}"                                                # ALWAYS {} — never a block decision
```

Hard rules: every branch ends in `print("{}")` and exit 0; total budget under
the 15 s timeout; any exception → best-effort meta error note, still `{}`.
The hook is an **observer with a camera** — it must never veto, delay-fail, or
alter a tool call (the shell-hook contract makes even a crash harmless, but we
don't lean on that).

Latency honesty: each shot is a fresh venv-python + `cua-driver mcp` stdio
handshake + one capture — measure, expect ~1–3 s; ×2 per action. Computer-use
actions are already seconds-scale and approval-gated, so v1 accepts this;
open question #1 tracks the persistent-helper optimization if the drill
measures worse.

### B. NEW `dashboard/aux_shots.py` (~300 lines) — exec'd into server.py globals

Follows every CLAUDE.md aux rule: imports its own stdlib deps under private
aliases (**never** `from datetime import datetime` — the aux-module gotcha),
defines only new names, registers routes via `register_get`/`register_post`,
never takes the hub down (module body wrapped by the loader's try/except).

Uses these verified server/recorder globals: `DATA` (`~/.hermes/dashboard`),
`HOME`, `register_get`, `_cached`, and from aux_recorder (loaded earlier in
sorted order): `_rec_conn`, `_rec_lock`, `_rec_init`, `TOOL_KIND`,
`recorder_api_handler`, `metrics_record`/`metrics_count` (aux_metrics.py,
also already loaded — `aux_metrics.py` < `aux_recorder.py` < `aux_shots.py`
in sort order).

**1. Attach loop** — daemon thread (guarded by a `_shots_thread_started`
global, same pattern as aux_recorder's), every 15 s:

- scan `cua-shots/*.meta.json` (and orphan `.jpg`s older than 60 s without
  meta — partial writes from a killed hook);
- for each, `UPDATE actions SET shots=? WHERE tool_call_id=? AND shots=''`
  under `_rec_lock` (order-independent join: if the row isn't there yet —
  ws-leg lag, reconciler 5 s cadence — leave the files; retry next pass);
- on first attach: `metrics_record("cua_shot", action=…, before_ms=…,
  after_ms=…, bytes=…)` and `metrics_count("cua_shots_attached")` — the
  P1.5 collector's real API (`aux_metrics.py` lines 128/140);
- hourly GC: TTL sweep, then oldest-first delete until under
  `SHOT_STORE_CAP`; files whose `tcid` matches an `actions` row keep their
  DB `shots` JSON but the detail endpoint reports `"expired": true` when the
  file is gone (the row's text record outlives its pixels — honest, like
  "snapshot pruned").

**2. Route wrap** — at module load:

```python
_orig_recorder_api = recorder_api_handler
def shots_recorder_api(ctx):
    out = _orig_recorder_api(ctx)
    # list mode: decorate rows with {"shots":{"before":bool,"after":bool}}
    # detail mode (?id=): parse the row's shots JSON into out["shots"], add
    #   "expired" flags per phase by stat()ing the files
    return out
register_get("/api/recorder", shots_recorder_api)   # last writer wins in GET_ROUTES
```

(One SELECT of `id,tool_call_id,shots` for the listed ids — no N+1; the list
response stays small because booleans, not pixels, ride the 3 s poll.)

**3. Image endpoint** — `register_get("/api/recorder/shot", …)`:

`GET /api/recorder/shot?id=412&phase=before&max=480`
→ `{"ok":true, "data":"data:image/jpeg;base64,…", "w":1512, "h":945,
    "bytes":214301, "ts":1751692347.1}`

- `id` = actions rowid; resolves the filename from the row's `shots` JSON —
  the client never supplies a path (no traversal surface; the only file ever
  opened is `os.path.join(SHOTS_DIR, basename_from_db)` after a
  `os.path.basename == value` check);
- `max` (optional, ≤480) requests a downscaled thumb: stdlib-only dashboard
  can't Pillow-resize, so thumbs are produced by `sips -Z <max>` (macOS
  built-in, no TCC, subprocess list-args) into a `cua-shots/.thumb/` cache,
  cached forever (immutable inputs), GC'd with its parent;
- missing/expired → `{"ok":false,"error":"frame expired"}` — HTTP 200 error
  body, house style;
- responses are per-frame and on-demand — the base64 cost (~300 KB full,
  ~40 KB thumb) never rides the recorder poll.

### C. What does NOT change

- `aux_recorder.py`: untouched. `TOOL_KIND["computer_use"]="computer"`,
  `REVERSIBLE_POLICY["computer"]="no"`, `UNDO_WHITELIST={"write","shell"}`,
  `recorder_undo_handler`'s structural refusal — all exactly as shipped.
- `hermes_rpc.py`, `server.py`: untouched (routes via the registry, thread via
  aux_shots' own guard).
- Upstream checkout: untouched — the hook is configuration, not a fork.

---

## Frontend

### NEW `dashboard/aux_shots.js` (~260 lines) + ONE line in index.html

`<script src="/aux_shots.js"></script>` after the existing
`<script src="/aux_recorder.js"></script>` (index.html line ~2052; the
`/aux_*.js` static route already serves it with `Cache-Control: no-store`).
Loaded last ⇒ its reassignments win.

**Lane thumbnails** — wrap `recRender` (global function, reassignment wins for
all later callers including the `loadConsole` chain aux_recorder.js already
wired at its lines 452–459):

```js
var _origRecRender = recRender;
recRender = function(){
  _origRecRender.apply(this, arguments);
  shotsDecorateRows();          // rows with kind==="computer" && shots
};
```

`shotsDecorateRows()` finds `.rec-row[data-kind="computer"]` whose action (via
`recState.last.actions`) has `shots.before||shots.after`, and injects a
`.shot-strip` (two 64×40 rounded frames, hairline border, 4 px gap) between
`.rec-main` and `.rec-src`. Frames lazy-load: fetch
`/api/recorder/shot?id=N&phase=P&max=480` → set `img.src = d.data`; cache
data-URIs in `shotCache` keyed `id:phase` so the 3 s poll re-render costs
zero refetches. Missing phase → dashed placeholder box. No emoji; CSS
injected once (`shotsInjectCss`, same pattern as `recInjectCss`).

**Detail pane evidence** — wrap `recDetail` the same way: after the original
pane renders, append an `evidence` section for computer rows:

- header row: `<div class="k">evidence</div>` + a muted
  "local only — these frames never leave this Mac" hint;
- BEFORE / AFTER frames side by side (each `max-width:48%`), label chips with
  absolute 12-hour capture time (`recClock` exists) and the after frame's
  `+1.8s` delta; `status:"blocked"` renders an amber "action was denied —
  frames show nothing changed" note (acceptance #5);
- `expired:true` → placeholder "frame expired (14-day retention)";
- click a frame → full-size overlay `#shotpop` (fixed, backdrop blur,
  `z-index` above the card): fetches the phase without `max`, Esc / click-out
  closes; respects `REDUCE` (no zoom animation), all strings through `recE()`
  (the esc-on-number-safe wrapper aux_recorder.js already defines).

**States**: loading skeleton frames; fetch error → placeholder + retry on next
expand; rows without shots (pre-feature history, safe actions) render exactly
as today — zero regression for non-computer rows.

---

## Integration points (verified names)

| Point | Verified symbol / location |
|---|---|
| Hook events + payload | `agent/shell_hooks.py`: `hook_event_name`, `tool_name`, `tool_input`, `extra.tool_call_id` (docstring ~69/77); matcher `fullmatch` ~193; `_TOP_LEVEL_PAYLOAD_KEYS` payload builder ~525 |
| Hook firing site | `model_tools.py` `handle_function_call()`: pre ~1014/1018, `_emit_post_tool_call_hook` ~853 (passes `tool_call_id`, `duration_ms`, `status`) |
| Hook registration | `hermes_cli/main.py` ~12376 (CLI/serve/-z), `gateway/run.py` ~6693 (gateway, `accept_hooks=False` ⇒ allowlist required) |
| Safe-action set | `tools/computer_use/tool.py:80` `_SAFE_ACTIONS = frozenset({"capture","wait","list_apps"})` |
| Capture backend | `tools/computer_use/cua_backend.py` `CuaDriverBackend().capture(mode, app)` → `CaptureResult.png_b64` (`backend.py:41`); env override `HERMES_CUA_DRIVER_CMD` (~76); `cua_driver_binary_available()` (~216) |
| Driver binary | `/Applications/CuaDriver.app/Contents/MacOS/cua-driver` (symlinked at `~/.local/bin/cua-driver`); TCC: Screen Recording + Accessibility already granted (CLAUDE.md) |
| Images stripped from state.db | `run_agent.py` `_persist_session` ~1841 (`_is_multimodal_tool_result` → text summary; image parts → `"[screenshot]"`) — why capture-at-action-time is mandatory |
| Recorder row + key | `aux_recorder.py`: `actions.tool_call_id UNIQUE`, `TOOL_KIND`, `REVERSIBLE_POLICY`, `_rec_conn`/`_rec_lock`, `recorder_api_handler`, `recorder_undo_handler`, `_irreversible_detail("computer")` |
| Aux registry | `server.py` ~2039–2081: `register_get/register_post`, `RouteCtx.q1`, `_AUX_FILES` sorted exec order (`aux_recorder.py` < `aux_shots.py`), `_dispatch_aux` JSON-only |
| Console lane | `aux_recorder.js`: `recRender` (256), `recDetail` (333), `recState`, `recE` (15), `recClock` (22), `data-kind="computer"` row styling (144), loadConsole wrap (452) |
| Metrics | `aux_metrics.py`: `metrics_record(kind, **fields)` (128), `metrics_count(name, n=1)` (140) |
| Permission tiers | `dashboard/permissions.py`: 17-class taxonomy — no computer-use class today (classes are terminal-pattern-keyed); shots create the *evidence base* a future computer-use class needs, but this spec does not touch `CLASS_META` |
| CLI verifiers | `hermes hooks list` / `hermes hooks test <event> --for-tool computer_use --payload-file F` / `hermes hooks doctor` (docs §"The hermes hooks CLI") |

---

## Edge cases & failure modes

- **Driver missing/hung**: `cua_driver_binary_available()` fast-path exits;
  a hung MCP handshake dies at the 15 s hook timeout — upstream logs a
  warning, the action proceeds. Row later shows "no frames captured".
- **Approval denied / tool error**: pre fired (before frame exists), post
  fires with `status:"blocked"|"error"` (documented extra) — meta records it;
  UI renders the denial note. No orphan: meta is written by the post leg; if
  the post hook never fires (process killed mid-action), the orphan sweep
  builds a before-only meta after 60 s.
- **Window closed by the action itself** (clicked "Quit"): after-capture of
  `app=X` fails → after `{ok:false}`, note "window closed before after-frame";
  before frame still tells the story.
- **`app` unspecified**: capture falls to the frontmost window (upstream
  semantics) — same window the action targeted in the common case; meta
  records `app`/`window_title` actually captured so the evidence is
  self-describing. Multi-monitor: per-window capture, single image — fine.
- **Parallel tool calls**: hook fires per call (docs: "3 tools in parallel →
  fires 3 times"); tcid-keyed files can't collide.
- **Model's own `capture` calls**: skipped (safe set) — no double-screenshot
  loops, and a som/vision capture the model requested is its context, not our
  evidence.
- **recorder row late or never** (reconciler cursor behind, dashboard down for
  hours): files wait; attach loop retries each pass; TTL eventually reaps
  never-matched files. Dashboard downtime loses zero frames (hook doesn't
  depend on the dashboard being up).
- **tcid collision after sanitization** (theoretical): second writer appends
  `-2` (`os.path.exists` loop, like `_to_trash`).
- **Disk full / shots dir unwritable**: hook meta-error path, `{}` out —
  fail-open; attach loop logs once, not per-pass.
- **Huge windows** (5K display): downscale to 1440 long-edge caps bytes;
  per-file 1.5 MB hard cap with quality fallback.
- **sips missing/failing** (it ships with macOS): thumb endpoint falls back to
  serving the full frame (bigger payload, still correct).
- **Hook script edited after consent**: allowlist keys on command string, not
  hash — `hermes hooks doctor` flags mtime drift; CHANGELOG-staging notes any
  edit (config-as-code discipline).
- **Upstream bump moves `CuaDriverBackend` or the hook wire format** (v0.18.x
  pinned; DEVPLAN §5 upgrade-at-phase-boundaries): the hook driver is the
  single seam — re-run `hermes hooks test` + the capture smoke after every
  bump, same policy as the P1.2 checkpoint driver.
- **Reduced motion / theme**: no new animation beyond opacity; frames render
  identically in light/dark (they're photos); overlay honors `REDUCE`.

## Security & safety (invariants)

- **Local-first absolute**: frames are written to `~/.hermes/dashboard/
  cua-shots` (0700/0600), served only on `127.0.0.1:7788`, and are **never**
  forwarded — not to Telegram, not into World Brief, not into model context,
  not into state.db. The hook adds zero network I/O; the driver conversation
  is stdio on-box.
- **Pixels can contain anything** (password managers, messages, banking):
  treated as secrets-grade data — 0600, 14-day TTL, 500 MB cap, no thumbnails
  in list payloads, no filenames derived from content. Open question #2
  proposes a never-capture app denylist.
- **The hook can never act**: it always emits `{}` — structurally incapable of
  blocking, rewriting, or approving a tool call; approval flow
  (`approvals.mode: manual`, serve `approval.request`) is untouched upstream
  of it and unaware of it.
- **Undo whitelist untouched**: `computer` stays outside `UNDO_WHITELIST`;
  shots never make a row "undoable" — evidence ≠ reversibility, and the UI
  copy never implies otherwise.
- **Consent is explicit and narrow**: two exact `(event, command)` allowlist
  entries; `hooks_auto_accept` stays `false`; the `hooks:` block and allowlist
  ride config-as-code review. `--yolo` remains forbidden; no Gmail send
  surface; Telegram lock untouched.
- **No path traversal**: shot filenames come only from the DB row the server
  itself wrote; client supplies row ids and an enum phase.
- **Fail-open for the agent, fail-closed for evidence claims**: a capture
  failure never blocks the action; the UI never fabricates a frame — missing
  means "missing", expired means "expired".

## Test plan (no --yolo, no real sends, drills on throwaway apps)

```bash
# 0. install: hook script (chmod 755), hooks: block, allowlist merge; then
launchctl kickstart -k gui/$(id -u)/com.hermes.serve
hermes gateway restart
hermes hooks list                    # both entries, consent=approved
hermes hooks doctor                  # exec bit, allowlist, JSON validity all green

# 1. static gates
python3 -m py_compile dashboard/aux_shots.py ~/.hermes/agent-hooks/cua-shot.py
node --check dashboard/aux_shots.js

# 2. hook smoke WITHOUT the agent (synthetic payload — proves TCC + driver path)
cat > /tmp/shot-payload.json <<'EOF'
{"hook_event_name":"pre_tool_call","tool_name":"computer_use",
 "tool_input":{"action":"click","coordinate":[100,100],"app":"Finder"},
 "session_id":"drill","cwd":"/tmp","extra":{"tool_call_id":"drill-001"}}
EOF
hermes hooks test pre_tool_call --for-tool computer_use --payload-file /tmp/shot-payload.json
ls -la ~/.hermes/dashboard/cua-shots/drill-001.before.jpg     # exists, 0600, <1.5MB
file ~/.hermes/dashboard/cua-shots/drill-001.before.jpg       # JPEG, sane dimensions

# 3. safe-action skip
#    (same payload with "action":"capture") -> hooks test -> NO new file

# 4. live drill (hub surface, benign app):
open -a TextEdit
#   hub chat: "Using computer_use, click in the TextEdit window and type: drill"
#   approve each action in the dashboard when prompted (approval loop is P1.4-proven)
sleep 20
curl -s 'localhost:7788/api/recorder?kind=computer&limit=5' | python3 -m json.tool
#   expect: rows with "shots":{"before":true,"after":true}
ID=<row id>
curl -s "localhost:7788/api/recorder/shot?id=$ID&phase=before&max=480" | python3 -c \
  "import json,sys;d=json.load(sys.stdin);assert d['ok'] and d['data'].startswith('data:image/jpeg;base64,');print('THUMB OK',d['w'],d['h'])"

# 5. deny path: trigger another action, DENY it in the approval sheet
#   -> row exists, detail shows status blocked, both frames present, pixels identical-ish

# 6. cross-surface: Telegram "click ..." drill (locked to user <YOUR_TELEGRAM_USER_ID>) OR
hermes -z "computer_use: press key cmd+s in TextEdit"   # -z fails closed on approval — expected;
#   the pre hook fired anyway -> before frame present, post status:"error"  (acceptance #5 analog)

# 7. invariants regression
curl -s -X POST localhost:7788/api/undo -d "{\"id\":$ID}"    # {"ok":false,"error":"irreversible"}
#   TextEdit doc discarded by hand; nothing was sent anywhere; no Gmail, no Telegram writes

# 8. fail-open: sudo-free driver outage sim
mv ~/.local/bin/cua-driver /tmp/cua-driver.bak   # break the PATH symlink only
#   (hook still finds the app bundle via HERMES_CUA_DRIVER_CMD — so instead:)
HERMES_CUA_DRIVER_CMD=/nonexistent hermes hooks test pre_tool_call --for-tool computer_use \
  --payload-file /tmp/shot-payload.json          # exits fast, {} out, no crash
mv /tmp/cua-driver.bak ~/.local/bin/cua-driver

# 9. GC: touch -t an old shot pair; wait for the hourly sweep (or call the GC fn
#    in a python3 -c against aux_shots constants) -> files gone; detail shows "expired"

# 10. headless renderer harness (expand.js pattern)
curl -s 'localhost:7788/api/recorder?limit=50' > /tmp/rec.json
node -e 'const fs=require("fs");global.window={};global.document=undefined;
  global.recState={last:JSON.parse(fs.readFileSync("/tmp/rec.json","utf8"))};
  global.recRender=function(){};global.recDetail=async function(){};
  global.recE=s=>String(s==null?"":s);global.recClock=()=>"";
  eval(fs.readFileSync("dashboard/aux_shots.js","utf8"));
  console.log("LOAD OK — no throw with DOM absent");'

# 11. full app pass: ⌘R in the app — Hub/Mind/Console render, recorder lane shows
#     strips on computer rows, expand shows frames, overlay opens/closes, chat streams,
#     TTFT unchanged (metrics_payload p50 within noise of the 965ms baseline)
```

## Effort & sequencing

1. **Hook driver + config + allowlist + `hermes hooks test` smoke** (~2 h).
   Independently valuable: frames land on disk with zero dashboard changes.
   This step measures real capture latency (open question #1 input).
2. **`aux_shots.py`**: migration, attach loop, route wrap, shot endpoint,
   GC, metrics (~2.5 h). Verifiable by curl before any UI exists (steps 4/7).
3. **`aux_shots.js` + index.html script tag** (~2 h incl. headless harness +
   overlay polish). The index.html line and the two `~/.hermes` config files
   are the only shared-file touches → orchestrator integrates them; the two
   new aux files + hook script are agent-buildable in isolation.
4. **Drill suite + CLAUDE.md note + CHANGELOG-staging entry** (~1 h).

Total ≈ 7–8 h. Dependencies: P1.2 recorder (shipped), cua-driver TCC
(granted), P1.4 approval loop (proven). **No dependency on P3.1–P3.4** — under
quality-gated in-order this sits at its DEVPLAN slot (Phase 3 #5), but it can
be pulled earlier without risk if the trust story wants it sooner (it pairs
naturally with the Shortcuts action-bus review, which will want the same
evidence pattern). No user steps: no new TCC, no OAuth, nothing for
NEEDS-YOU.md — config consent is handled by explicit allowlist entries
reviewed via config-as-code.

## What it unlocks for trust

DEVPLAN's risk table calls the nightmare by name: *"Irreversible computer-use
action destroys user data … one incident ends trust permanently."* Today the
recorder's `computer` rows are honest but blind — "click element #7,
irreversible" is a text claim. After this: every irreversible action carries
pixel proof of what the screen showed the instant before the agent acted and
what it showed after. That converts three things:

- **Disputes become checkable** — "did it click the right thing?" is settled
  by frames, not recollection.
- **Autonomy becomes earnable** — a future computer-use permission class
  (P1.3's taxonomy deliberately left this out) can gate tier promotion on *N
  evidenced clean actions*, the same way the model-promotion gate (Phase 3 #3)
  gates model swaps. Evidence is the currency graduated permissions spend.
- **The Console becomes a story, not a log** — the "watch it work" edge that
  research validated for the timeline now extends to the physical desktop,
  which is exactly the surface where trust is scarcest.

## Open questions / risks

1. **Per-shot latency** (fresh venv python + MCP handshake, ~1–3 s ×2 per
   action). Measure in step 1. If median >2 s: (a) persistent capture helper
   (long-lived `cua-driver mcp` session owned by a tiny daemon the hook pings
   over a unix socket), or (b) drop the after-frame for `scroll`/`key` class
   actions. Decide on data, not vibes.
2. **Sensitive-window denylist**: should a fixed app list (Keychain Access,
   1Password, Banking sites' app wrappers) suppress capture entirely?
   Proposed: yes, fixed list in the hook (mirrors the "hard-blocked lists,
   not judgment calls" doctrine) — needs the user's list. Pixels of a
   revealed password field are the one artifact worse than no evidence.
3. **Feed the after-frame back to the agent** for self-verification
   ("did my click land?") — Phase 4 territory (pairs with /learn and the
   promotion gate); explicitly out of scope here to keep the hook an observer.
4. **Retention numbers**: 14 d / 500 MB copied from P1.2's undo-trash and the
   checkpoint store. Revisit at Phase 3 review with real `du` data.
5. **Upstream churn**: shell-hook wire format and `CuaDriverBackend` are the
   two seams (v0.18.x pinned). Re-run test-plan steps 2 and 4 after every
   hermes-agent bump — same policy, same drill, as the checkpoint driver.
6. **Screeny-style MCP alternative** (DEVPLAN row #14 provenance): rejected
   for v1 — a second capture stack means a second TCC grant, a second
   supply-chain vet (MCP rot risk, DEVPLAN §risks), and no capability the
   in-house driver lacks. Recorded here so the decision isn't relitigated
   from the FUTURE doc.
