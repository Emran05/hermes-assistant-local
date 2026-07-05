# Flight Recorder + Undo — design spec (P1)

Workstream P1.2 of `docs/DEVPLAN.md` Phase 1 ("Earn trust"). Every consequential
agent action becomes a reviewable, timestamped timeline entry in the Console;
file-mutating actions get a pre-write snapshot and a one-click **Undo** that
restores the byte-identical original. Irreversible actions are truthfully and
loudly marked — by a fixed category list, never a judgment call.

All facts below were verified against the live tree on 2026-07-05:
`dashboard/server.py` (2400 lines), `dashboard/hermes_rpc.py`,
`dashboard/expanders_extra.py`, `~/.hermes/hermes-agent` (v0.18.x checkout),
`~/.hermes/state.db` schema, `~/.hermes/config.yaml`.

---

## The critical design question: HOW does the recorder observe actions?

**Decision: (a) ride the existing `tool.start` / `tool.complete` serve events,
in a hybrid with (a2) a state.db reconciler, and delegate before-snapshots to
hermes-agent's OWN upstream checkpoint machinery (currently disabled — we turn
it on).** Not (b) a filesystem watcher, not (c) our own tool wrapping.

### What the source actually shows (do not re-derive; verified)

1. **`tool.start` does NOT carry args.** In
   `~/.hermes/hermes-agent/tui_gateway/server.py` `_on_tool_start()` (line
   ~3351) the emitted payload is `{"tool_id", "name", "context"}` plus
   `args_text` only in verbose sessions. Full `args` (already passed through
   `_redact_tool_args_for_display`) arrive only in **`tool.complete`**
   (`_on_tool_complete()`, line ~3378): `{"tool_id", "name", "args",
   "duration_s", "result", "summary", "result_text?", "inline_diff?"}`.
   → A dashboard-side observer learns the target path *after* the tool ran.
   It can never take the pre-write snapshot itself without a race.

2. **Upstream already wraps tool execution with a synchronous pre-write
   snapshot.** `~/.hermes/hermes-agent/agent/tool_executor.py` (lines ~465–487
   and ~1108–1131): before executing `write_file` / `patch` it calls
   `agent._checkpoint_mgr.ensure_checkpoint(work_dir, f"before {function_name}")`,
   and before a `terminal` command matching `_is_destructive_command()`
   (`agent/tool_dispatch_helpers.py`: `_DESTRUCTIVE_PATTERNS` +
   `_REDIRECT_OVERWRITE` regexes — rm/mv/overwrite-redirect class) it
   checkpoints the command's cwd. Backing store:
   `tools/checkpoint_manager.py` — a single shared **bare git store** at
   `~/.hermes/checkpoints/store/`, one ref per project
   (`refs/hermes/<sha256(abs_workdir)[:16]>`), per-project index files,
   size-capped (500MB default), pruned, with `list_checkpoints()`, `diff()`,
   and `restore(working_dir, commit_hash, file_path=None)` — and `restore()`
   itself takes a "pre-rollback snapshot" first, so **undo-of-the-undo is
   free**. It is config-gated: `checkpoints.enabled` defaults to **False**
   (`hermes_cli/config.py` ~line 1242) and `~/.hermes/checkpoints/` does not
   exist on this machine today. **Enabling it is step 0 of this workstream.**

3. **Serve events only reach the dashboard while `hermes_rpc.run_turn()` holds
   a WS open for a hub turn.** Telegram gateway and CLI sessions run in other
   processes; their events never cross our WS. But **every** surface persists
   its tool calls to `~/.hermes/state.db` (`messages.tool_calls` JSON on
   assistant rows — with raw arguments and the same `tool_call.id` that
   `tool.start` emits as `tool_id`; `role='tool'` rows carry `tool_call_id`,
   `tool_name`, `content` = result). `console_activity()` in
   `expanders_extra.py` already proves this read works live, read-only.

### Why the alternatives lose

- **(b) Filesystem watcher:** stdlib Python has no FSEvents; polling can't
  capture before-content of a delete without mirroring every watched byte;
  and a watcher fundamentally **cannot attribute** a change to the agent vs.
  the user vs. Spotlight. An undo built on that lies. Rejected.
- **(c) Wrapping the tools ourselves** (forking tool plumbing or interposing
  a proxy): hermes-agent upstream moves ~1,700 commits per 2-week release
  (DEVPLAN §5); a fork of `tool_executor` is unmaintainable debt. Upstream
  already interposes at exactly the right point *and* ships the snapshot
  store. We ride it.

### The resulting architecture (three legs, all cheap)

```
                       ┌──────────────────────────────────────────────┐
   hub chat turn ──────► hermes_rpc.run_turn  (tool.start/complete)   │ live, sub-second
                       │        │  RECORDER_HOOK (3-line hook)        │
                       ▼        ▼                                     │
  telegram / CLI ──► state.db ──► recorder_loop (5s poll, mode=ro,   │ catches ALL surfaces,
                                   cursor + tool_call_id dedupe)      │ incl. dashboard downtime
                                                                      │
  write_file/patch/rm ─► upstream CheckpointManager (synchronous,     │ the only race-free
                          pre-execution, ~/.hermes/checkpoints/store) │ before-snapshot
                       └──────────────────────────────────────────────┘
                                    ▼
                    ~/.hermes/dashboard/recorder.db  (append-only actions)
                                    ▼
             GET /api/recorder · POST /api/undo · Console "Flight Recorder" lane
```

---

## Goal & acceptance criteria

Done means:

1. `hermes config set checkpoints.enabled true` is applied, and after the agent
   writes any file, `~/.hermes/checkpoints/store/HEAD` exists and
   `hermes checkpoints status` reports ≥1 project.
2. **100% of write-class tool calls** (`write_file`, `patch`, `terminal`,
   `execute_code`, `computer_use`, `memory`) that reach `state.db` appear as
   rows in `recorder.db` within 10 s, from **every** surface (hub, Telegram,
   CLI) — verified by count-matching a drill session (DEVPLAN §6 metric).
3. Hub-surface actions appear in the Console recorder lane **during** the turn
   (via the WS hook), not only after the reconciler pass.
4. The file-edit drill passes: user asks the agent (hub chat) to edit
   `/tmp/fr-drill/note.txt`; clicking **Undo** in the Console restores the
   byte-identical original (`cmp` clean against a held-back copy).
5. The deliberate wrong-file **deletion drill** passes: agent `rm`s a file via
   `terminal` (destructive → auto-checkpoint); Undo restores it.
6. Irreversible categories (`computer_use`, network sends, anything with no
   snapshot) are marked by a **fixed table in code** (`TOOL_KIND` /
   `REVERSIBLE_POLICY` below); `/api/undo` structurally refuses them
   (whitelist, not heuristic) with a clear error the UI renders.
7. Undo conflict safety: if the target file changed after the agent's write
   (sha256 mismatch vs. recorded `after_state`), undo refuses with
   `conflict:true` unless the request carries `force:true`, and the UI shows a
   confirm step.
8. All verify gates pass: `python3 -m py_compile` on the three touched Python
   files, `node --check` on `recorder.js`, the headless renderer harness on
   `recorder.js` with live `/api/recorder` JSON, and a dashboard service
   restart + ⌘R leaves Hub/Mind/Console fully working.

---

## Data model

### 1. Action log — SQLite, `~/.hermes/dashboard/recorder.db`

SQLite (not JSONL) because dedupe/reconciliation needs keyed upsert by
`tool_call_id`, the timeline needs indexed paging, and the process already
uses stdlib `sqlite3` everywhere. WAL mode; file chmod `0600` (args can carry
sensitive strings — see Security).

```sql
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS actions(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tool_call_id  TEXT UNIQUE,          -- upstream tool_call.id; dedupe key across WS + state.db
  ts            REAL NOT NULL,        -- action start, epoch seconds
  session       TEXT DEFAULT '',      -- serve sid (ws) or state.db session_id (reconciler)
  source        TEXT DEFAULT '',      -- hub | telegram | cli | dashboard
  tool          TEXT NOT NULL,        -- write_file | patch | terminal | computer_use | ...
  args          TEXT DEFAULT '',      -- JSON, capped 8192 bytes (see edge cases)
  target        TEXT DEFAULT '',      -- primary path, or command head (200 chars), or cua action
  kind          TEXT NOT NULL,        -- write | shell | computer | memory | read | net | agent | other
  reversible    TEXT NOT NULL,        -- yes | partial | no
  status        TEXT NOT NULL,        -- running | done | error | undone
  duration_s    REAL,
  summary       TEXT DEFAULT '',      -- upstream tool.complete summary, capped 500 chars
  snapshot_ref  TEXT DEFAULT '',      -- JSON: {"workdir","commit","short","reason","iso"} or ''
  after_state   TEXT DEFAULT '',      -- JSON: {"exists","size","mtime","sha256"} of target post-action
  undone_ts     REAL,
  undo_note     TEXT DEFAULT '',
  origin        TEXT DEFAULT ''       -- ws | statedb | dashboard
);
CREATE INDEX IF NOT EXISTS idx_actions_ts   ON actions(ts DESC);
CREATE INDEX IF NOT EXISTS idx_actions_kind ON actions(kind, ts DESC);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
-- meta rows: ('statedb_cursor', '<max messages.timestamp processed>'),
--            ('schema_version', '1')
```

### 2. Classification tables (constants in `dashboard/recorder.py`)

```python
TOOL_KIND = {
    "write_file": "write",  "patch": "write",
    "terminal": "shell",    "process": "shell",  "execute_code": "shell",
    "computer_use": "computer",
    "memory": "memory",
    "read_file": "read", "search_files": "read", "vision_analyze": "read",
    "web_search": "net", "web_extract": "net",
    "delegate": "agent", "skill": "agent", "clarify": "agent",
    "todo": "other", "cronjob": "other",
}   # unknown tool -> "other"

# Reversibility POLICY is fixed at classification time (DEVPLAN: "hard-blocked
# lists, not judgment calls"). snapshot presence can only DOWNGRADE, never up.
REVERSIBLE_POLICY = {
    "write":    "yes",       # if snapshot_ref found, else -> "no"
    "shell":    "partial",   # file state restorable IF checkpointed; side
                             # effects (network, launchctl, sends) are not
    "memory":   "no",        # v1: memory versioning is P1.1's surface
    "computer": "no",        # clicks/keystrokes cannot be unwound
    "read": "no", "net": "no", "agent": "no", "other": "no",
}
UNDO_WHITELIST = {"write", "shell"}   # /api/undo refuses everything else
ARGS_CAP, SUMMARY_CAP, HASH_CAP_BYTES = 8192, 500, 32 * 1024 * 1024
```

`target` extraction: `args.get("path")` for write/patch/read;
`(args.get("command") or "")[:200]` for terminal/process/execute_code;
`args.get("action") or args.get("coordinate") and "click"` style summary for
computer_use; `""` otherwise.

### 3. Snapshot store — upstream's, not ours

`~/.hermes/checkpoints/store/` (bare git; layout constants verified in
`tools/checkpoint_manager.py`: `_STORE_DIRNAME="store"`,
`_REFS_PREFIX="refs/hermes"`, per-project ref = `refs/hermes/` +
`sha256(abs_workdir)[:16]`, `_MAX_FILES=50_000` staging guard,
`max_file_size_mb=10` per-file filter, `max_total_size_mb=500` cap,
`auto_prune` on). We never write this store directly; we call the manager (see
driver below). Snapshot semantics: **once per turn per workdir** (first
mutating call wins — `conversation_loop.py:635` calls `new_turn()`), so
several writes in one turn share one commit; undo therefore restores
turn-start state. Documented in the UI detail pane as "snapshot taken at turn
start".

Snapshot→action association (at record time, best-effort; re-verified at undo
time): derive `workdir` from `target` with a local reimplementation of
`get_working_dir_for_path()` (walk up for markers `.git pyproject.toml
package.json Cargo.toml go.mod Makefile pom.xml .hg Gemfile`, else parent —
12 lines, stdlib, must match upstream to hash to the same ref); driver `list`;
pick the newest commit with `reason` starting `"before "` and ISO timestamp
within `[action.ts - 900, action.ts + 60]`. Miss ⇒ `snapshot_ref=''` and
`reversible` downgraded to `"no"` with `undo_note="no snapshot captured"`.

### 4. Undo-trash for created-file undo

`~/.hermes/dashboard/undo-trash/` — when undoing a `write_file` that **created**
a file (path absent from the checkpoint commit), "undo" = move the file to
`undo-trash/<epoch>-<basename>` (never hard-delete). Size-capped GC: entries
older than 14 days removed by `recorder_loop`.

---

## Backend

Everything lives in **NEW file `dashboard/recorder.py`**, exec-included into
server.py globals immediately AFTER the `expanders_extra.py` exec block and
before `class Handler` (preserving the exec-include ORDER RULE — recorder.py
redefines nothing, so order vs. expanders_extra is safe, but it must run
before `class Handler` and after the helpers it uses). It imports its own
deps at top: `import hashlib, json, os, re, shutil, sqlite3, subprocess, sys,
threading, time, urllib.parse`.

### Module-level wiring inside recorder.py (no server.py edits needed for these)

```python
REC_DB = os.path.join(DATA, "recorder.db")          # DATA exists in server globals
HERMES_SRC = os.path.join(HOME, ".hermes", "hermes-agent")
_rec_lock = threading.Lock()

# live hub-surface feed: hermes_rpc is already imported by server.py
try:
    if hermes_rpc:
        hermes_rpc.RECORDER_HOOK = recorder_ws_event
except Exception:
    pass
```

### Checkpoint driver — subprocess, never in-process import

The dashboard stays stdlib-only; `tools/checkpoint_manager` pulls
`hermes_constants`, `hermes_cli._subprocess_compat`, `utils` from the checkout.
So all checkpoint ops run through a short-lived subprocess:

```python
_CKPT_DRIVER = r"""
import json, sys
sys.path.insert(0, %r)  # HERMES_SRC
from tools.checkpoint_manager import CheckpointManager
req = json.load(sys.stdin); mgr = CheckpointManager(enabled=True)
op = req["op"]
if op == "list":    out = mgr.list_checkpoints(req["workdir"])
elif op == "diff":  out = mgr.diff(req["workdir"], req["commit"])
elif op == "restore":
    out = mgr.restore(req["workdir"], req["commit"], req.get("file") or None)
else: out = {"error": "bad op"}
json.dump(out, sys.stdout)
""" % (HERMES_SRC,)

def _ckpt(op, **kw):
    try:
        p = subprocess.run([sys.executable, "-c", _CKPT_DRIVER],
                           input=json.dumps(dict(op=op, **kw)),
                           capture_output=True, text=True, timeout=60)
        return json.loads(p.stdout or "{}")
    except Exception as e:
        return {"error": f"checkpoint driver: {type(e).__name__}: {e}"}
```

(Fallback if the driver import ever breaks on upstream churn — documented, not
built in v1: direct `git --git-dir=~/.hermes/checkpoints/store` plumbing
against `refs/hermes/<sha256(abs_workdir)[:16]>` with the per-project index at
`store/indexes/<hash>`.)

### Public functions (called from the inline hooks)

**`recorder_ws_event(sid, etype, payload)`** — called from `hermes_rpc.run_turn`
on every `tool.start` / `tool.complete`. Never raises (whole body in
try/except). On `tool.start`: INSERT (OR IGNORE on `tool_call_id=payload["tool_id"]`)
a row `status='running'`, `origin='ws'`, `source='hub'`, `session=sid`,
`tool=payload["name"]`, `target` from `payload.get("context","")` (best-effort;
real args come later), kind/reversible from tables. On `tool.complete`: UPDATE
the row (or INSERT if start was missed) with `args` (JSON, capped), real
`target`, `duration_s`, `summary`, `status='done'`; if `kind in
("write","shell")` also compute `after_state` (stat + sha256 if ≤32MB) and
associate `snapshot_ref` via `_ckpt("list", ...)` in a fire-and-forget thread
so the chat loop never blocks (>50ms budget).

**`recorder_loop()`** — daemon thread (started from `main()` hook). Every 5 s:
open `state.db` read-only (`file:...?mode=ro`, `timeout=2.0` — same pattern as
`console_activity()`), select
`messages m JOIN sessions s ON m.session_id=s.id WHERE m.timestamp > ?cursor
AND (m.tool_calls IS NOT NULL OR m.tool_name IS NOT NULL) ORDER BY m.timestamp
LIMIT 500`. For assistant rows: parse `tool_calls` JSON, for each call
`{id, function:{name, arguments}}` → INSERT OR IGNORE by `tool_call_id=id`
(`origin='statedb'`, `source=s.source or 'cli'`), and UPDATE-fill `args`/
`target` on existing ws rows where empty (`COALESCE` semantics). For
`role='tool'` rows: match `tool_call_id`, set `status='done'`, summary from
`content[:500]`, then after_state/snapshot association for write/shell rows
missing them. Advance `meta.statedb_cursor` only after a clean batch. Also:
mark rows `running` older than 10 min as `status='error'`,
`undo_note='no completion observed'`; GC `undo-trash/` older than 14 days.

**`recorder_api(qs)`** — GET handler.
- List mode (no `id`): params `limit` (default 50, max 200), `before`
  (id cursor for paging), `kind` (CSV filter), `q` (LIKE filter on
  target/tool). Response:

```json
{"actions": [{"id": 412, "ts": 1751692347.1, "source": "hub", "tool": "write_file",
              "target": "/tmp/fr-drill/note.txt", "kind": "write",
              "reversible": "yes", "status": "done", "duration_s": 0.4,
              "summary": "Wrote 214 bytes", "has_snapshot": true,
              "undone_ts": null, "origin": "ws"}],
 "counts": {"total": 91, "reversible": 34, "undone": 2},
 "checkpoints_enabled": true, "recorder_ok": true}
```

  `checkpoints_enabled` read from `~/.hermes/config.yaml` via
  `HERMES config get`-free path: cheap regex over the yaml
  (`re.search(r"checkpoints:\s*\n(?:[ \t].*\n)*?[ \t]+enabled:\s*true", ...)`)
  cached 60 s with the existing `_cached()` helper.
- Detail mode (`?id=N`): full row incl. `args` (parsed JSON), `after_state`,
  `snapshot_ref`, plus on-demand `diff` (driver `diff`, capped 20 KB, only
  when `snapshot_ref` present). Errors: unknown id → `{"error":"not found"}`
  (HTTP 200 with error body — matches house style, e.g. `/api/mind_extra`).

**`recorder_undo(body)`** — POST handler. Body:
`{"id": 412, "force": false}`. Sequence, under `_rec_lock`:
1. Load row; refuse if missing (`{"ok":false,"error":"unknown action"}`),
   already `undone` (`"already undone"`), `kind not in UNDO_WHITELIST` or
   `reversible == "no"` (`{"ok":false,"error":"irreversible",
   "detail":"computer-use actions cannot be undone"}`).
2. Conflict check: if `after_state.sha256` present and file exists and current
   sha256 differs → `{"ok":false,"conflict":true,"error":"file changed since
   the agent wrote it"}` unless `force`.
3. If `snapshot_ref` empty → re-attempt association once (checkpoint may have
   landed late); still empty → `{"ok":false,"error":"no snapshot available"}`.
4. Single-file restore first: `_ckpt("restore", workdir=..., commit=...,
   file=relpath(target, workdir))`. If the driver reports the pathspec is
   absent from the commit AND `after_state.exists` was true → **created-file
   undo**: move `target` into `undo-trash/` (response notes the trash path).
   For `shell` rows with no single target: whole-dir restore
   (`file=None`) — response carries `"scope":"directory"` and the UI confirmed
   this beforehand (see Frontend).
5. On success: UPDATE row `status='undone'`, `undone_ts`, `undo_note`
   (`restored_to` short hash or trash path); INSERT a new action row
   `{tool:'undo', kind:'write', source:'dashboard', origin:'dashboard',
   target, status:'done', reversible:'yes'}` — the undo itself is on the
   record (and upstream's pre-rollback snapshot makes it re-undoable).
6. Response: `{"ok":true,"restored_to":"a1b2c3d4","file":"/tmp/fr-drill/note.txt",
   "note":"pre-rollback snapshot taken — undo is itself undoable"}`.

### EXACT inline hooks (the ONLY edits to shared files)

**`dashboard/server.py` — 4 touches:**

1. Immediately after the existing `expanders_extra` exec block (line ~2029),
   add:

```python
# Flight recorder (P1.2): action log + undo. Same exec-include pattern;
# must stay before `class Handler` and after expanders_extra.
try:
    with open(os.path.join(HERE, "recorder.py")) as _f:
        exec(_f.read(), globals())
except Exception as _e:  # never let an aux file take the hub down
    print(f"[recorder] failed to load: {type(_e).__name__}: {_e}",
          file=sys.stderr)
```

2. In `do_GET`, the static-file tuple (line ~2071) gains one entry:

```python
        elif path in ("/motion.min.js", "/expand.js", "/recorder.js"):
```

3. In `do_GET`, after the `/api/mind_extra` elif (line ~2132), add:

```python
        elif path == "/api/recorder":
            fn = globals().get("recorder_api")
            self._json(fn(urllib.parse.parse_qs(parsed.query)) if fn
                       else {"error": "recorder not loaded"})
```

4. In `do_POST`, directly above the final `self.send_error(404)`, add:

```python
        if path == "/api/undo":
            fn = globals().get("recorder_undo")
            self._json(fn(self._body_json()) if fn
                       else {"ok": False, "error": "recorder not loaded"})
            return
```

   and in `main()`, after the `system_sampler_loop` conditional:

```python
    if "recorder_loop" in globals():   # P1.2 flight recorder reconciler
        threading.Thread(target=globals()["recorder_loop"],
                         daemon=True).start()
```

**`dashboard/hermes_rpc.py` — 2 touches:**

1. Module top (after `TURN_TIMEOUT = ...`):

```python
RECORDER_HOOK = None   # set by dashboard/recorder.py; called (sid, etype, payload)
```

2. In `run_turn()`'s event loop, immediately after
   `payload = ev.get("payload") or {}` (line ~249):

```python
            if RECORDER_HOOK and etype in ("tool.start", "tool.complete"):
                try:
                    RECORDER_HOOK(sid, etype, payload)
                except Exception:
                    pass   # recorder must never break a turn
```

**One-time config change (step 0, not code):**

```bash
hermes config set checkpoints.enabled true     # real key, verified in hermes_cli/config.py
```

---

## Frontend

### UX walkthrough

Console view (`#view-console`) gains a **Flight Recorder** card above the
existing activity timeline card. Header: bespoke two-tone SVG (a rounded
"black box" rectangle with a rewind arrow — accent fill + currentColor stroke,
same construction as `WICONS`), title "Flight Recorder", a live counter
("34 reversible · 2 undone"), and filter chips: `All · Writes · Shell ·
Computer · Undone`. Rows, densest-first:

```
[icon] write_file   /tmp/fr-drill/note.txt        hub   [reversible]  2:41 pm   [Undo]
[icon] terminal     rm -rf ~/old-exports          cli   [partial]     2:38 pm   [Undo…]
[icon] computer_use click "Submit" in Safari      hub   [irreversible] 2:31 pm
```

- Reversibility chip: green `reversible`, amber `partial — files only`, red
  `irreversible`. Undone rows get a struck-through target + violet `undone`
  chip and no button.
- Clicking a row expands an inline detail pane (args JSON pretty-printed in
  `.mono`, summary, snapshot short-hash + "snapshot taken at turn start" note,
  lazy-loaded diff via `/api/recorder?id=N`).
- **Undo** click → `confirm()` (native NSAlert sheet — works in the WKWebView
  per CLAUDE.md) with a per-kind message; `partial`/directory-scope restores
  get the scarier copy ("restores every file in <dir> to turn start; command
  side effects are NOT undone"). Then `POST /api/undo`. Success: row flips to
  undone with a Motion One `animate()` background flash (150ms, accent →
  transparent); the new `undo` action row appears at top on next poll.
  `conflict:true` response → second `confirm()` ("file changed since — force
  restore?") → retry with `force:true`. Error → inline red note in the row,
  no modal.
- If `checkpoints_enabled:false` in the payload: an amber banner pinned atop
  the card — "Snapshots are off. Run `hermes config set checkpoints.enabled
  true` and restart the agent services — until then nothing new is
  undoable." (text selectable for copy).
- All times 12-hour via the existing `relTime()`; zero emoji; strings through
  `esc()` with `String()` coercion first (the known esc-on-number throw).

### Files & hooks

- **New file `dashboard/recorder.js`** (~350 lines), served at `/recorder.js`
  (static-route hook above, `Cache-Control: no-store` like expand.js).
  Loaded LAST so its assignments win. It:
  1. injects its card into `#view-console` (before `#console-timeline`'s
     parent card) on first `loadConsole` call — **zero index.html layout
     edits**;
  2. wraps the existing poller:
     `const _origLoadConsole = loadConsole; loadConsole = async function(){
     await _origLoadConsole(); await loadRecorder(); }` — rides the existing
     3-second `setInterval` gate (console visible, no modal open) for free;
  3. defines `loadRecorder()` (fetch `/api/recorder?limit=50` + current filter,
     render), `recUndo(id)`, `recDetail(id)`, an in-module `recState`
     (filter, expanded id, last JSON for diffing to avoid re-render churn).
- **`dashboard/index.html` — 1 line**, after the existing
  `<script src="/expand.js"></script>` (line 2049):

```html
<script src="/recorder.js"></script>
```

### States

- **Loading:** skeleton rows (reuse `.skel`).
- **Empty:** hint div — "No recorded actions yet. Every file the agent writes,
  every command it runs, lands here with an Undo where one is possible."
- **Fetch error / dashboard offline:** keep last render, append tiny
  `.hint` "recorder feed unreachable — retrying"; never blank a populated
  timeline.
- **Recorder module failed to load server-side** (`{"error":"recorder not
  loaded"}`): card shows a single red hint row with the error.
- **Reduced motion:** no flash animation (Motion One respects the global
  pattern already used; guard with `matchMedia('(prefers-reduced-motion)')`
  like the aurora does).

---

## Edge cases & failure modes

- **state.db locked / WAL churn:** open `mode=ro` with `timeout=2.0`; on
  `sqlite3.Error` skip the cycle (cursor unmoved — no loss). Never open RW.
- **recorder.db corruption:** on `sqlite3.DatabaseError` at init, rename to
  `recorder.db.corrupt-<ts>` and recreate empty; log to stderr. Timeline
  history is evidence, not safety-critical state — snapshots live in git.
- **Duplicate delivery (ws row then statedb row):** `tool_call_id UNIQUE` +
  `INSERT OR IGNORE`; statedb pass only fills NULL/empty columns on existing
  ws rows.
- **tool.start with no tool.complete** (turn crash, serve restart, approval
  denied): reconciler flips `running` → `error` after 10 min. If state.db
  later shows a result row, it flips back to `done` (UPDATE is
  status-agnostic on fill).
- **Multiple writes to one file in one turn:** they share one checkpoint;
  undo of any of them restores turn-start bytes. Detail pane says so
  explicitly; conflict check (sha vs. `after_state` of the *latest* row)
  protects against surprising the user.
- **Concurrent double-undo:** `_rec_lock` + status recheck inside the lock;
  second caller gets `"already undone"`.
- **Huge args / results:** args JSON capped at 8 KB (`…truncated` marker key),
  summary 500 chars; sha256 skipped for files >32 MB (`after_state.sha256:null`
  → conflict check degrades to size+mtime comparison).
- **Huge/hostile directories:** upstream refuses to stage >50k-file dirs and
  >10 MB files — the action records with `snapshot_ref=''`, reversible
  downgraded, `undo_note` explains ("directory too large to snapshot").
- **Snapshot pruned before undo** (500 MB cap, auto-prune, 14-day retention):
  driver `restore` returns commit-not-found → response
  `{"ok":false,"error":"snapshot was pruned"}`, row updated
  `reversible='no'`, `undo_note='snapshot pruned'`. UI drops the button.
- **Checkpoints disabled / store absent:** every write records with the amber
  banner active; nothing lies about undoability (`has_snapshot:false`).
- **Driver import breaks on upstream bump** (pinned v0.18.x, but §5 risk):
  `_ckpt` returns `{"error": ...}`; logging continues, undo degrades to a
  clear error. `py_compile` won't catch this — the test plan exercises the
  driver directly.
- **Workdir hash mismatch** (our `get_working_dir_for_path` clone drifting
  from upstream): association misses ⇒ safe degrade to `reversible:'no'`,
  never a wrong-directory restore — the driver resolves paths through the
  real manager, which validates `file_path` is inside `working_dir`.
- **Non-UTF8 / binary targets:** args arrive as JSON from upstream (already
  display-redacted on the ws path); state.db `tool_calls` parsed with
  `errors='replace'` semantics (json.loads on stored TEXT; on failure store
  `args=''` and keep the row).
- **Serve down / model paused / offline:** hub turns fall back to `hermes -z`
  (no ws events) — reconciler still captures everything from state.db. No
  network is used by the recorder at all; fully offline-correct.
- **Dashboard restarted mid-turn:** ws rows for that turn are lost from RAM
  only; cursor-based reconciler backfills from state.db on next boot
  (cursor persists in `meta`).
- **Clock skew between git ISO timestamps and epoch ts:** association window
  is generous (−900 s/+60 s) and keyed on reason prefix; worst case is a
  missed association (safe direction).
- **User edits file after agent, then clicks Undo:** conflict check (criterion
  7) — refuse, offer force.
- **Undo of a file inside `~/.hermes` itself** (e.g. agent edited config.yaml):
  allowed — it's a file write with a snapshot like any other; the detail pane
  shows the path prominently.

## Security & safety

- **Local-first upheld:** recorder adds zero network I/O. Everything reads/
  writes under `~/.hermes/`. The checkpoint driver runs the local checkout
  with no env passthrough beyond default; subprocess uses list-args + JSON
  stdin — no shell interpolation anywhere.
- **Secrets discipline:** ws-path args are upstream-redacted
  (`_redact_tool_args_for_display`); state.db-path args are raw, so
  `recorder.db` is chmod `0600` at creation and args are never forwarded to
  any remote surface (no Telegram, no web). The recorder never reads
  `~/.hermes/.env` or the serve token.
- **Undo is a whitelist machine:** only `kind ∈ {write, shell}` with a live
  `snapshot_ref` can restore; `computer_use`, `net`, `memory`, unknown tools
  are structurally refused — a fixed table in code, per DEVPLAN ("hard-blocked
  lists, not judgment calls"). Restores go through upstream
  `restore()`, which validates the commit hash format and confines
  `file_path` to the checkpoint's working dir (`_validate_file_path`) — no
  path traversal via crafted rows.
- **Invariants untouched:** no Gmail surface, no Telegram sends, no approval
  bypass (the hook only *observes* events; `approval.request` handling in
  `run_turn` is unchanged), `approvals.mode: manual` unchanged. The recorder
  must refuse to ever *execute* agent tools — it replays file bytes from
  snapshots, nothing else.
- **Fail-open for the agent, fail-closed for undo:** recorder exceptions can
  never block a tool call or a chat turn (every hook wrapped); undo refuses
  on any doubt (missing snapshot, hash conflict, pruned commit).

## Test plan

```bash
# 0. enable upstream snapshots (step 0) and restart agent-side services
hermes config set checkpoints.enabled true
grep -A2 '^checkpoints:' ~/.hermes/config.yaml          # expect: enabled: true
launchctl kickstart -k gui/$(id -u)/com.hermes.serve

# 1. static gates
python3 -m py_compile dashboard/server.py dashboard/recorder.py dashboard/hermes_rpc.py
node --check dashboard/recorder.js

# 2. driver smoke (before any UI work)
python3 - <<'EOF'
import json, subprocess, sys, os
drv = open(os.path.expanduser("~/hh_drv.json"),"w")  # ad hoc: reuse _CKPT_DRIVER inline in real run
EOF
# in practice: python3 -c "$(python3 -c 'import re;print(open("dashboard/recorder.py").read())' )" — build step runs
# echo '{"op":"list","workdir":"/tmp/fr-drill"}' | python3 -c "<_CKPT_DRIVER>"   -> expect JSON list, no traceback

# 3. restart dashboard, check surfaces
launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard
curl -s 'localhost:7788/api/recorder' | python3 -m json.tool     # actions:[], checkpoints_enabled:true, recorder_ok:true

# 4. edit drill (hub surface, live ws rows + undo)
mkdir -p /tmp/fr-drill && printf 'v1\n' > /tmp/fr-drill/note.txt && cp /tmp/fr-drill/note.txt /tmp/fr-drill/.held
#   hub chat: "Replace the contents of /tmp/fr-drill/note.txt with v2"
curl -s 'localhost:7788/api/recorder?kind=write' | python3 -m json.tool
#   expect: write_file row, target=/tmp/fr-drill/note.txt, reversible:"yes", has_snapshot:true, origin:"ws"
ID=<that id>
curl -s -X POST localhost:7788/api/undo -d "{\"id\":$ID}" | python3 -m json.tool   # ok:true
cmp /tmp/fr-drill/note.txt /tmp/fr-drill/.held && echo BYTE-IDENTICAL              # acceptance #4

# 5. deletion drill (destructive terminal -> checkpoint -> undo)
printf 'precious\n' > /tmp/fr-drill/keep.txt
#   hub chat: "run: rm /tmp/fr-drill/keep.txt"  (approve when prompted)
curl -s -X POST localhost:7788/api/undo -d '{"id":<shell row id>}'                  # ok:true, scope handling
test -f /tmp/fr-drill/keep.txt && echo RESTORED                                     # acceptance #5

# 6. cross-surface coverage (reconciler)
hermes -z "write the word hello to /tmp/fr-drill/cli.txt"
sleep 10; curl -s 'localhost:7788/api/recorder?q=cli.txt' | python3 -m json.tool    # origin:"statedb", source:"cli"

# 7. refusal + conflict paths
curl -s -X POST localhost:7788/api/undo -d '{"id":<computer_use row id>}'           # {"ok":false,"error":"irreversible"}
echo 'user edit' >> /tmp/fr-drill/note.txt
curl -s -X POST localhost:7788/api/undo -d '{"id":'$ID'}'                           # already undone OR conflict:true on a fresh row
curl -s -X POST localhost:7788/api/undo -d '{"id":999999}'                          # {"ok":false,"error":"unknown action"}

# 8. headless render harness (same pattern as expand.js verification)
curl -s 'localhost:7788/api/recorder?limit=50' > /tmp/rec.json
node -e '
  const fs=require("fs");
  global.window={}; global.document={getElementById:()=>null,createElement:()=>({style:{},classList:{add(){}},setAttribute(){},appendChild(){}}),querySelector:()=>null};
  global.$=()=>null; global.esc=s=>String(s); global.icon=()=>""; global.relTime=()=>""; global.animate=()=>({});
  global.loadConsole=async()=>{}; global.curView="console"; global.setInterval=()=>{};
  eval(fs.readFileSync("dashboard/recorder.js","utf8"));
  renderRecorderRows(JSON.parse(fs.readFileSync("/tmp/rec.json","utf8")).actions);   // must not throw (esc-on-number class)
  console.log("RENDER OK");'

# 9. coverage metric (acceptance #2): counts must match for the drill window
sqlite3 "file:$HOME/.hermes/state.db?mode=ro" "SELECT COUNT(*) FROM messages WHERE tool_calls IS NOT NULL AND timestamp> strftime('%s','now')-3600;"
sqlite3 ~/.hermes/dashboard/recorder.db "SELECT COUNT(DISTINCT tool_call_id) FROM actions WHERE ts> strftime('%s','now')-3600 AND tool_call_id IS NOT NULL;"

# 10. regression: full app pass — ⌘R in the app; Hub renders, Mind renders,
#     Console shows BOTH lanes, chat turn streams, /api/chat/approve still works.
```

## Effort & sequencing

Build order (each step independently verifiable):
1. **Step 0 — enable checkpoints + driver smoke** (½ h). Unblocks everything;
   proves the upstream store populates on this machine (`hermes checkpoints
   status`).
2. **recorder.py: DB + classification + `recorder_loop` reconciler +
   `recorder_api`** (core, ~3 h, ~450 lines). Testable with only server.py
   hooks 1–3 + `main()` hook — recorder is already useful (timeline of all
   surfaces) with zero hermes_rpc changes.
3. **`_ckpt` driver + snapshot association + `recorder_undo`** (~2 h).
   Drills 4–7 pass from `curl` before any UI exists.
4. **hermes_rpc.py hook** (10 min) — upgrades hub rows from 5 s-delayed to
   live.
5. **recorder.js + index.html script tag** (~2 h incl. headless harness).
6. **Full drill suite + CLAUDE.md note** (~1 h).

Dependencies: none on P1.1 (editable memory) — but P1.1's memory-write
endpoints SHOULD append `{tool:'memory', source:'dashboard',
origin:'dashboard'}` rows via a one-line call to `recorder_record_local()`
(exported for exactly this) so the timeline is complete; coordinate the
function name. P1.3 (graduated tiers) will consume `TOOL_KIND`/`REVERSIBLE_POLICY`
as its tier seed — keep them top-of-file constants. P1.4 (approval drill)
doubles as recorder drill #5 — schedule together. Shared-file conflict watch:
server.py hooks land in four distinct, small regions; do not run this
workstream's server.py edits concurrently with P1.1's.

## Open questions / risks

1. **Screenshot evidence for `computer_use` rows** (DEVPLAN "screenshot via
   existing driver where cheap"): the dashboard's launchd python3 lacks Screen
   Recording TCC, and `screencapture` under launchd will fail or prompt.
   v1 ships without screenshots (rows are marked irreversible regardless);
   Phase 3 #5 (screenshot-grounded computer use) is the right home. If wanted
   sooner: a settings-gated best-effort `screencapture -x` behind a TCC grant
   to the dashboard service — default off.
2. **Memory-edit undo** is deliberately `reversible:'no'` in v1 — USER.md
   versioning belongs to P1.1's surface (`USER.md.lock` + its own history).
   Revisit after P1.1 lands: if P1.1 keeps `.bak` generations, recorder rows
   with `tool='memory'` can link to them.
3. **`_tool_progress_enabled(sid)` gating** in tui_gateway: hub sessions
   demonstrably emit tool events today (the model pill's running-tool state
   feeds off them), but if a future serve config turns tool progress off, the
   ws leg goes dark — the reconciler leg keeps acceptance #2 true. Assert the
   ws leg in the drill anyway (acceptance #3).
4. **Snapshot granularity is per-turn, not per-write** (upstream
   `ensure_checkpoint` semantics). Fine for v1's trust story ("restore what
   this turn touched"); per-write granularity would need upstream changes —
   track as a possible upstream contribution, not a fork.
5. **Upstream API churn on `CheckpointManager`** (v0.18.x pinned; DEVPLAN §5
   says upgrade only at phase boundaries): the driver is the single seam —
   re-run test-plan step 2 after every hermes-agent bump; the documented
   git-plumbing fallback exists if the import surface moves.
6. **recorder.db growth:** ~1 KB/row; at hundreds of tool calls/day this is
   MBs/month. v1 adds no pruning; if it matters, add a 90-day sweep to
   `recorder_loop` (one DELETE) — decide at Phase 1 review with real numbers.
7. **`terminal` reversibility copy:** "partial — files only" must be honest;
   a `git push` or `curl -X POST` undone-by-checkout is NOT undone. The
   detail-pane copy and the confirm() text both say "command side effects are
   not reversed" — wordsmith at build time, but never soften it.
