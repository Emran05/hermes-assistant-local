# Config-as-Code Snapshot/Restore — design spec (P1.6)

Make the dashboard's mutable runtime state (which lives OUTSIDE the repo in
`~/.hermes/dashboard/`) versionable INSIDE the repo as a single reviewable,
diffable artifact: `docs/state-snapshot.json`. Export captures a strict
allowlist of config; import restores it (dry-run first, then apply). This is
DEVPLAN table item #26 / Phase 1 workstream 6, and it settles the Section 7
question ("templates or documented restore path") in favor of: **runtime files
stay gitignored in `~/.hermes`; the tracked artifact is the snapshot; restore
is an endpoint, not a copy script.**

Nothing in this feature calls the model, the serve backend, or the network.
It reads/writes local JSON, and (for one knob) shells out to `hermes config
set` — the CLAUDE.md-sanctioned way to touch config.yaml.

---

## Goal & acceptance criteria

Done means:

1. `POST /api/config/export` writes `docs/state-snapshot.json` (repo tree)
   capturing exactly: widget layout order, allowlisted settings keys, model
   roster + active model id, and two config.yaml knobs
   (`model.context_length`, `approvals.mode` — the latter capture-only).
   Output is deterministic (`sort_keys=True, indent=1` + trailing newline) so
   two exports with no state change produce a byte-identical file (clean
   `git diff`).
2. `grep -E '[0-9]{8,10}:[A-Za-z0-9_-]{35}|/Users/' docs/state-snapshot.json`
   returns nothing after any export — and an export whose payload WOULD match
   a secret pattern is refused with `{ok:false, error, where}`, never written.
3. `POST /api/config/import {"dry_run":true}` returns a per-section change
   plan (adds/removes/changed keys, from→to) without touching any file.
4. `POST /api/config/import {"dry_run":false}` applies the snapshot: after
   mutating `tickers` and the widget order live, importing the earlier
   snapshot restores both, `/api/hub` reflects it on the next fetch, and a
   pre-restore backup exists in `~/.hermes/dashboard/snapshot-backups/`.
5. A tampered snapshot with `"approvals.mode": "auto"` (or any value other
   than `manual`) is refused in full — no section applies (fail closed).
6. A snapshot containing a widget id not in the live `WIDGETS` catalog
   imports cleanly: the unknown id is dropped and reported in
   `plan.layout.dropped_unknown` / `applied` warnings, never written.
7. The Hub header gains a "Config" button (bespoke SVG, no emoji) opening a
   glass modal with: drift badges per section, Export, Preview restore
   (dry-run plan table), Apply restore (confirm()-gated). All times 12-hour.
8. `python3 -m py_compile dashboard/server.py dashboard/config_snapshot.py`
   and `node --check` on the extracted JS pass; dashboard restart-verified.

---

## Data model

### The tracked artifact: `docs/state-snapshot.json` (repo path, ≤256 KB)

```json
{
 "agent_config": {
  "approvals.mode": "manual",
  "model.context_length": 65536
 },
 "dashboard": {
  "layout": {
   "order": ["clock", "weather", "markets", "system", "messages", "tasks",
             "hackernews", "briefing", "agent_pulse", "github", "battery",
             "rss", "quicklinks", "notes", "crypto", "today", "reminders",
             "worldclock", "recent", "folders"]
  },
  "models": {
   "active": "mlx-community/Hermes-3-Llama-3.1-8B-4bit",
   "roster": [
    {"id": "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit",
     "label": "Qwen3-30B-A3B", "ram": 18, "note": "MoE · fast · current default"},
    {"id": "mlx-community/Hermes-3-Llama-3.1-8B-4bit",
     "label": "Hermes-3-8B", "ram": 5, "note": "Nous · tuned for tool-calling"}
   ]
  },
  "settings": {
   "weather_city": "Hoboken",
   "tickers": ["SPCX", "AAPL", "NVDA", "MSFT"],
   "starred_tickers": [],
   "coins": [],
   "rss_feeds": [],
   "news_feeds": [],
   "quicklinks": [{"label": "GitHub", "url": "https://github.com"}],
   "timezones": [],
   "permission_tiers": {}
  }
 },
 "exported_at": "2026-07-05T02:14:09-04:00",
 "kind": "hermes-state-snapshot",
 "note": "",
 "schema": 1
}
```

(Keys shown sorted because the file IS written sorted.)

### Allowlist (the ONLY things captured — everything else is implicitly denied)

| Section | Source of truth | Captured fields | Validation on import |
|---|---|---|---|
| `dashboard.layout` | `~/.hermes/dashboard/layout.json` via `get_layout()` | `order` (list of widget ids) | every id must be in live `WIDGETS`; unknown ids dropped + reported; ≤64 entries |
| `dashboard.settings` | `~/.hermes/dashboard/settings.json` via `get_settings()` | `weather_city`, `tickers`, `starred_tickers`, `coins`, `rss_feeds`, `news_feeds`, `quicklinks`, `timezones`, `permission_tiers` | city str ≤80; lists capped [:20] (mirrors the `/api/settings` POST handler); tickers `^[A-Z0-9.^=-]{1,12}$`; quicklinks `{label≤40, url}` with url `^https?://`; rss/news feeds `^https?://`; timezones str ≤64; `permission_tiers` dict pass-through ≤8 KB serialized (P1.3's key — captured only if present, validated only as shape, so P1.6 works before or after P1.3 lands) |
| `dashboard.models` | `models.json` via `_model_registry()` + `active_model()` | `roster` `[{id,label,ram,note}]`, `active` (repo id string) | id `^[\w.-]+/[\w.-]+$` ≤120; label ≤64; ram number/null; note ≤120; ≤24 entries. `active` restore is opt-in (see import) |
| `agent_config` | `~/.hermes/config.yaml` (stdlib scanner, `_config_model_default()` pattern) | `model.context_length` (int), `approvals.mode` (str, capture/verify ONLY — never applied) | context_length int in [1024, 262144]; approvals.mode must equal `"manual"` or the whole import is refused |

Deliberately NOT capturing `model.default` in agent_config: it duplicates
`dashboard.models.active` (the `active-model` file falls back to it). Single
authority = `dashboard.models.active`; applying it goes through the existing
`switch_model()` which already runs `hermes config set model.default`.

### Denylist (hard-excluded; the redaction scan enforces it as defense-in-depth)

- `~/.hermes/.env` (TELEGRAM_*), `~/.hermes/dashboard/serve-token` — never read.
- `access.json` (granted folders = absolute-path leaks), `chats/`,
  `tasks.json`, `notes.json`, `briefing.json`, `inbox/`, `metrics.jsonl`.
- `~/.hermes/memories/**` (USER.md is personal data, not config — P1.1's turf),
  `state.db`, skills.
- `weather_lat`/`weather_lon` (precise home coordinates). Only `weather_city`
  is captured; the existing `/api/settings` handler already pops lat/lon when
  city is set, so restore re-derives them — same behavior, no coordinate leak.
- Any absolute path, hostname, or username string anywhere in the payload.

### Local backup dir (NOT in the repo): `~/.hermes/dashboard/snapshot-backups/`

Before any non-dry-run import, the live state is exported (same builder) to
`snapshot-backups/pre-restore-<YYYYmmdd-HHMMSS>.json`. Keep newest 5, delete
older (simple mtime GC in the same call). One-step recovery from a bad restore.

---

## Backend

**New module: `~/HermesAssistant/dashboard/config_snapshot.py`**
exec'd into server.py globals (expanders_extra pattern). It may USE server
globals defined above the exec point (`read_json`, `write_json`, `get_layout`,
`save_layout`, `get_settings`, `SETTINGS_FILE`, `LAYOUT_FILE`, `MODELS_FILE`,
`WIDGETS`, `_model_registry`, `active_model`, `switch_model`,
`_model_downloaded`, `_state_lock`, `_widget_cache`, `HERMES`, `_hermes_env`,
`HERE`, `HOME`, `DATA`) but MUST import its own stdlib deps at the top
(exec'd code can't rely on server.py's function-local imports):

```python
# config_snapshot.py — P1.6 config-as-code snapshot/restore.
# exec()'d into server.py globals just before class Handler.
import json, os, re, shutil, subprocess, time
from datetime import datetime
```

Module constants:

```python
SNAPSHOT_SCHEMA = 1
SNAPSHOT_KIND = "hermes-state-snapshot"
REPO_ROOT = os.path.dirname(HERE)                       # dashboard/ -> repo
SNAPSHOT_PATH = os.path.join(REPO_ROOT, "docs", "state-snapshot.json")
SNAPSHOT_RELPATH = "docs/state-snapshot.json"           # what we report (no abs paths in JSON)
SNAP_BACKUPS = os.path.join(DATA, "snapshot-backups")
SNAP_MAX_BYTES = 256 * 1024
SETTINGS_ALLOW = ("weather_city", "tickers", "starred_tickers", "coins",
                  "rss_feeds", "news_feeds", "quicklinks", "timezones",
                  "permission_tiers")
AGENT_KEYS = ("model.context_length", "approvals.mode")
# hard-refuse patterns (export AND import):
SECRET_PATTERNS = [
    (r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b", "telegram-bot-token"),
    (r"\bsk-[A-Za-z0-9_-]{20,}\b",      "api-key"),
    (r"\bghp_[A-Za-z0-9]{30,}\b",       "github-token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "slack-token"),
    (r"\bAIza[0-9A-Za-z_-]{30,}\b",     "google-key"),
    (r"/Users/[^\"\\s]+",               "absolute-path"),
]
# warn-only (export proceeds, warning surfaced):
ENTROPY_PATTERN = r"\b[A-Za-z0-9+/=_-]{48,}\b"
```

Functions (all real names the build step should use):

- `_cfg_read_yaml_keys()` — stdlib scan of `~/.hermes/config.yaml` for
  `model.context_length` and `approvals.mode` (two-level `section:` +
  indented `key:` scanner, same approach as `_config_model_default()`).
  Returns `{"model.context_length": int|None, "approvals.mode": str|None}`.
- `snapshot_build(note="")` — assembles the snapshot dict from
  `get_layout()`, `get_settings()` (allowlisted keys only, absent keys
  omitted), `_model_registry()` + `active_model()`, `_cfg_read_yaml_keys()`.
  `exported_at` = local ISO with offset.
- `_snapshot_scan(obj)` — `json.dumps(obj)` then run SECRET_PATTERNS →
  `(hard_hits, warnings)`; hard hit = list of `{pattern_name, excerpt≤12ch}`
  (excerpt truncated so the scan result itself can't leak the secret).
- `_snapshot_dump(obj, path)` — atomic write: `json.dumps(obj, indent=1,
  sort_keys=True) + "\n"` to `path + ".tmp"`, `os.replace`. Held under
  `_state_lock`.
- `snapshot_validate(snap)` — schema/kind/size/shape checks + per-field rules
  from the allowlist table + `_snapshot_scan` + the approvals.mode==manual
  gate. Returns `(clean_snapshot, errors, warnings)` where `clean_snapshot`
  has unknown widget ids dropped (recorded) and all caps applied.
- `snapshot_diff(snap)` — compares clean snapshot vs live state; returns the
  `plan` object (shape below).
- `_snapshot_backup()` — `snapshot_build()` → dump to
  `SNAP_BACKUPS/pre-restore-<ts>.json`, GC to newest 5.
- `snapshot_get(qs)` — GET handler body.
- `snapshot_post(path, body)` — POST dispatcher for export/import.

### Endpoints

**`GET /api/config/snapshot`** — status + preview + drift. No params needed
(`qs` accepted for future use). Response:

```json
{"ok": true,
 "preview": { /* full snapshot_build() output */ },
 "file": {"exists": true, "path": "docs/state-snapshot.json",
          "bytes": 2481, "exported_at": "2026-07-05T02:14:09-04:00",
          "valid": true},
 "drift": {"layout": "in_sync", "settings": "drifted",
           "models": "in_sync", "agent_config": "in_sync"},
 "warnings": []}
```

`drift` values: `in_sync` | `drifted` | `missing` (no file) | `invalid`
(file exists but fails `snapshot_validate`). Errors never 500: any internal
exception → `{"ok": false, "error": "<type>: <msg>"}` (the module is
`safe()`-spirited like every provider).

**`POST /api/config/export`** — body `{"note": "optional ≤200ch"}` (or `{}`).
Flow: `snapshot_build(note)` → `_snapshot_scan` → hard hit? refuse →
ensure `docs/` exists → `_snapshot_dump(snap, SNAPSHOT_PATH)`. Response:

```json
{"ok": true, "path": "docs/state-snapshot.json", "bytes": 2481,
 "sections": ["layout", "settings", "models", "agent_config"],
 "warnings": []}
```

Errors (`ok:false`, HTTP 400 via the hook): `"secret-like value detected
(telegram-bot-token)"` + `"where"`; `"write failed: <OSError>"`.
The server NEVER runs git — committing is the user's move (DEVPLAN §7);
the UI shows the suggested command as copyable text.

**`POST /api/config/import`** — body:

```json
{"dry_run": true,
 "sections": ["layout", "settings", "models", "agent_config"],
 "apply_active_model": false,
 "snapshot": null}
```

All fields optional. Defaults: `dry_run=true` (fail-safe — omitting it never
mutates), `sections`=all, `apply_active_model=false`, `snapshot=null` → read
`SNAPSHOT_PATH` (inline `snapshot` object allowed for restoring a pasted /
older version; it goes through the exact same `snapshot_validate`).

Dry-run response (`plan` shape — this is the contract the UI renders):

```json
{"ok": true, "dry_run": true,
 "plan": {
  "layout": {"changed": true, "adds": ["crypto"], "removes": [],
             "reordered": true, "dropped_unknown": ["old_widget"]},
  "settings": {"changed_keys": {"tickers": {"from": ["AAPL"], "to": ["AAPL","NVDA"]}}},
  "models": {"roster_changed": false, "added": [], "removed": [],
             "active": {"from": "mlx-community/Hermes-3-Llama-3.1-8B-4bit",
                        "to": "mlx-community/Qwen3-8B-4bit",
                        "downloaded": true, "will_apply": false}},
  "agent_config": {"changes": {"model.context_length": {"from": 65536, "to": 32768}},
                   "verify_only": {"approvals.mode": "manual"}}},
 "warnings": []}
```

Apply flow (dry_run=false), in order, all file writes via `write_json` under
`_state_lock`:
1. `snapshot_validate` — any error refuses EVERYTHING (no partial apply).
2. `_snapshot_backup()`.
3. layout → `save_layout({"order": cleaned})`.
4. settings → merge allowlisted keys onto live `get_settings()` (keys absent
   from the snapshot are left untouched; setting `weather_city` pops
   `weather_lat`/`weather_lon`, mirroring the `/api/settings` handler), write
   `SETTINGS_FILE`, then `_widget_cache.clear()`.
5. models roster → `write_json(MODELS_FILE, {"models": cleaned_roster})`.
6. active model → ONLY if `apply_active_model` and `snap active != live
   active`: require `_model_downloaded(mid)` else report
   `"active_model": "not_downloaded"`; call `switch_model(mid)` (it already
   handles paused→resume + kickstart + `hermes config set model.default`).
   Never triggers a download.
7. agent_config → if `model.context_length` differs:
   `subprocess.run([HERMES, "config", "set", "model.context_length",
   str(v)], capture_output=True, text=True, timeout=30, env=_hermes_env())`.
   `approvals.mode` is NEVER written; if live config.yaml says anything
   other than `manual`, add warning `"live approvals.mode is not manual"`.

Apply response:

```json
{"ok": true, "dry_run": false,
 "applied": {"layout": true, "settings": ["tickers", "quicklinks"],
             "models": true, "active_model": "skipped",
             "agent_config": ["model.context_length"]},
 "backup": "snapshot-backups/pre-restore-20260705-021409.json",
 "warnings": []}
```

`active_model` ∈ `applied | skipped | not_downloaded | failed:<err>`.
Import errors (`ok:false`, 400): `no snapshot file`, `snapshot invalid:
<reason>`, `snapshot too large`, `approvals.mode must be manual`,
`secret-like value detected`, `unknown section <s>`.

### EXACT inline hooks in server.py (the ONLY server.py edits)

1. Exec-include — insert IMMEDIATELY AFTER the existing `expanders_extra`
   try/except block (so it's still before `class Handler`, and after every
   inline def; per the exec-include ORDER RULE it must stay there):

```python
# P1.6 config snapshot/restore — same exec-include pattern as above.
try:
    with open(os.path.join(HERE, "config_snapshot.py")) as _f:
        exec(_f.read(), globals())
except Exception as _e:  # never let an aux file take the hub down
    print(f"[config_snapshot] failed to load: {type(_e).__name__}: {_e}",
          file=sys.stderr)
```

2. GET route — insert after the `elif path == "/api/mind_extra":` pair
   (mirrors its `globals().get` guard so a failed exec degrades gracefully):

```python
        elif path == "/api/config/snapshot":
            fn = globals().get("snapshot_get")
            self._json(fn(urllib.parse.parse_qs(parsed.query)) if fn
                       else {"ok": False, "error": "snapshot module not loaded"})
```

3. POST route — insert directly above `if path == "/api/chat":`:

```python
        if path in ("/api/config/export", "/api/config/import"):
            fn = globals().get("snapshot_post")
            out = (fn(path, self._body_json()) if fn
                   else {"ok": False, "error": "snapshot module not loaded"})
            self._json(out, 200 if out.get("ok") else 400)
            return
```

4. Static route — extend the existing tuple on line ~2071:

```python
        elif path in ("/motion.min.js", "/expand.js", "/config.js"):
```

Total inline surface: ~14 lines, no shared-file surgery beyond dispatch.

---

## Frontend

**New file: `~/HermesAssistant/dashboard/config.js`**,
served at `/config.js`, loaded AFTER the inline script and after expand.js so
it can use `esc`, `animate`, `icon`, `$`, `renderHub` — and its assignments
win.

**Minimal index.html hook (1 line)** — after the existing line 2049
`<script src="/expand.js"></script>` add:

```html
<script src="/config.js"></script>
```

Everything else is built by config.js at runtime (precedent: the model-swap
modal at index.html ~line 1751 is created via `document.createElement` with
zero markup hooks).

### UX walkthrough

- On load, config.js appends a button to `#hubctl` (so it inherits the
  existing Hub-only visibility toggling in `setView()` for free):

```js
const hc = document.getElementById('hubctl');
if (hc) {
  hc.insertAdjacentHTML('beforeend',
    '<button class="ghost" id="cfg-btn" title="Snapshot / restore config">' +
    CFG_ICON + '<span class="lbl">Config</span></button>');
  document.getElementById('cfg-btn').onclick = openCfgPanel;
}
```

  `CFG_ICON` is a bespoke two-tone SVG (archive-box glyph: accent-filled lid
  + currentColor stroked box — matches the `WICONS` style; NO emoji).

- `openCfgPanel()` creates (once) `#cfgmodal` with the existing `.modal` /
  `.sheet` classes, animates it in with the global Motion One wrapper:
  `animate(sheet, {opacity:[0,1], transform:['scale(.97)','scale(1)']},
  {duration:.22, easing:'ease-out'})`, then `cfgRefresh()`.

- **Status header** — from `GET /api/config/snapshot`: snapshot file line
  ("docs/state-snapshot.json · 2.4 KB · exported Jul 5, 2:14 AM" — 12-hour
  via a local `fmt12(iso)` helper) and four drift pills (layout / settings /
  models / agent config) colored: in_sync = ok-green tint, drifted = amber,
  missing/invalid = muted/red. Pills pulse once on refresh
  (`animate(pill, {scale:[0.9,1]}, {duration:.3})`).

- **Export row** — "Export snapshot" button + optional note input (≤200ch).
  POST `/api/config/export` → on success: green flash, refreshed status, and
  a copyable hint line:
  `git add docs/state-snapshot.json && git commit -m "ops: state snapshot"`.
  On `ok:false`: inline red error text (e.g. the secret-scan refusal) — the
  modal stays open, nothing dismisses silently.

- **Restore section** — "Preview restore" → POST import `{dry_run:true}` →
  renders the plan as a compact table via `cfgPlanRows(plan)` (a PURE
  function returning an HTML string — deliberately DOM-free so the headless
  harness can test it): one row per section, changed keys with from→to,
  `dropped_unknown` shown as a warning row. Checkbox "Also switch active
  model" (maps to `apply_active_model`; disabled with hint when
  `plan.models.active.downloaded === false`). "Apply restore" button appears
  only after a preview and is `confirm()`-gated ("Restore snapshot from
  Jul 5, 2:14 AM? A pre-restore backup will be kept.") — confirm() works in
  the WKWebView (CLAUDE.md: JS dialog handlers are implemented).
  After a successful apply: call `renderHub()` (widgets re-render with
  restored layout/settings), refresh the model pill via the existing models
  poll if `active_model === "applied"`, show the `applied` summary.

- **States**: loading = shimmer rows; `drift.missing` = empty state
  ("No snapshot yet. Export writes docs/state-snapshot.json so your layout,
  watchlists and model roster are versioned with the repo."); fetch failure =
  inline error + Retry button; plan with zero changes = "Snapshot matches
  live state — nothing to restore."

- **Design laws honored**: zero emoji (SVG only), 12-hour times, dense
  single-sheet layout, Liquid Glass via existing `.modal .sheet` styling,
  Motion One `animate()` for sheet-in, pill pulse, and plan-table stagger
  (`animate(rows, {opacity:[0,1], y:[6,0]}, {delay: stagger(0.03)})` guarded
  with a fallback when `stagger` isn't global — plain loop with per-row
  delay).

---

## Edge cases & failure modes

- **Concurrent writes**: apply holds `_state_lock` around each `write_json`
  (same discipline as `/api/settings`); a simultaneous `/api/settings` POST
  serializes — last writer wins per file, no torn JSON (atomic
  tmp+`os.replace` everywhere).
- **Import racing export**: both funnel through `_state_lock` for writes;
  the snapshot read is a single `read_json` of an atomically-replaced file —
  worst case it sees the previous complete version, never a partial.
- **Snapshot file missing** → import: `{ok:false, error:"no snapshot file"}`;
  GET reports `drift: all "missing"`, UI shows the empty state.
- **Malformed / truncated JSON** in the file → `read_json` default → `invalid`
  drift status; import refuses with `snapshot invalid: not valid JSON`.
- **Oversized file** (>256 KB): refused before parsing (`os.path.getsize`
  check) — protects against a runaway/hostile file in the repo.
- **`permission_tiers` giant blob**: serialized-size cap 8 KB, else refused
  (`snapshot invalid: permission_tiers too large`).
- **Widget catalog drift** (snapshot from an older/newer checkout): unknown
  ids dropped + reported (mirrors `get_layout()`'s own self-heal); an order
  that becomes empty after cleaning → refuse (`layout empty after
  validation`) rather than blanking the hub.
- **Roster entry for a model that isn't downloaded**: fine — roster is just
  the menu; `downloaded` is computed live by `models_payload()`. Only
  `apply_active_model` checks `_model_downloaded` and reports
  `not_downloaded` instead of switching. Never auto-downloads (~GB pulls
  must stay user-initiated).
- **Model paused** during apply with `apply_active_model`: `switch_model()`
  already resumes (documented behavior); the plan's `will_apply` plus the
  confirm() means the user opted in. Without the flag, paused state is
  untouched.
- **`hermes` binary missing / `config set` fails**: agent_config step catches,
  reports `agent_config: []` + warning `"hermes config set failed: <err>"`;
  dashboard sections still applied (they're independent).
- **Repo `docs/` missing** (weird checkout): `os.makedirs(exist_ok=True)`
  before dump. **Read-only repo / disk full**: OSError → `{ok:false,
  error:"write failed: ..."}`, tmp file cleaned up by `os.replace` never
  running (leftover `.tmp` is harmless and gitignored by pattern? it is NOT —
  builder must `os.remove` the tmp on failure in a finally).
- **Backup GC**: `snapshot-backups` capped at 5 by mtime; GC failures are
  warnings, never block the restore.
- **Offline / model down / serve down**: irrelevant by design — no network,
  no inference in this feature. Works with everything else on fire.
- **exec load failure of config_snapshot.py**: server still boots (try/except
  hook), endpoints answer `{ok:false, error:"snapshot module not loaded"}`,
  UI shows its error state. The hub is never taken down (matches the
  expanders_extra contract).
- **Double-click Export / Apply**: buttons disabled while in flight
  (`btn.disabled=true` until the fetch settles); backend is idempotent
  anyway (same state → byte-identical file / empty plan).
- **Inline `snapshot` in import body**: passes through the identical
  validate+scan path; body size is already bounded by `_body_json()`
  handling — add an explicit 512 KB reject in `snapshot_post` for safety.

---

## Security & safety

- **Secrets never enter the repo** — twice over: (1) the allowlist simply
  never reads `.env`, `serve-token`, `access.json`, chats, memories, or
  state.db; (2) `_snapshot_scan` hard-refuses telegram-token / api-key /
  absolute-path shaped strings in the OUTPUT (defense against a secret
  pasted into, say, a quicklink label). Scan excerpts are truncated to 12
  chars so error responses can't leak the secret either.
- **`approvals.mode: manual` is an invariant, not a setting**: capture-only
  on export, refuse-if-not-manual on import, never written by apply. A
  snapshot cannot be used to silently flip Hermes to auto-approve.
- **Gmail read+draft-only / Telegram user-lock untouched**: those live in
  `.env` and gateway config — outside the allowlist, unreachable by this
  feature. `permission_tiers` (P1.3) IS captured, but restoring it goes
  through the same validated settings path the tiers UI itself uses; it can
  only ever narrow/rearrange tiers the user already expressed, and P1.3's
  own rule (irreversible = always-confirm floor) is enforced by the P1.3
  reader, not by what's stored.
- **Local-first**: zero network calls; the artifact lands in the local repo;
  pushing it anywhere remains a manual git action by the user.
- **No absolute paths / usernames in the artifact** (the `/Users/` pattern is
  a hard refusal) — the snapshot is portable and leak-free by construction.
- **Must refuse**: non-manual approvals.mode; secret-pattern hits; snapshots
  >256 KB; empty-after-validation layout; unknown top-level sections
  (forward-compat: `schema` bump required to add sections — schema 2 files
  are refused by schema-1 code with a clear error, never partially applied).

---

## Test plan

```bash
# 0. static checks
python3 -m py_compile dashboard/server.py dashboard/config_snapshot.py   # exit 0
node --check dashboard/config.js                                          # exit 0

# 1. restart + module loaded
launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard && sleep 2
grep -c "config_snapshot] failed" ~/.hermes/logs/dashboard.log            # expect 0

# 2. status + preview
curl -s localhost:7788/api/config/snapshot | python3 -m json.tool
# expect ok:true, preview.dashboard.settings.tickers == live settings.json,
# file.exists false on first run, drift all "missing"

# 3. export → deterministic + clean
curl -s -X POST localhost:7788/api/config/export -d '{}' | python3 -m json.tool
test -f docs/state-snapshot.json && echo OK
grep -E '[0-9]{8,10}:[A-Za-z0-9_-]{35}|/Users/|weather_lat' docs/state-snapshot.json
# expect NO output (secrets/paths/coords absent)
md5 docs/state-snapshot.json; curl -s -X POST localhost:7788/api/config/export -d '{}' >/dev/null; md5 docs/state-snapshot.json
# expect identical hashes (deterministic re-export)

# 4. drift + dry-run plan
curl -s -X POST localhost:7788/api/settings -d '{"tickers":["AAPL","TSLA"]}'
curl -s localhost:7788/api/config/snapshot | python3 -c "import sys,json; print(json.load(sys.stdin)['drift']['settings'])"   # "drifted"
curl -s -X POST localhost:7788/api/config/import -d '{"dry_run":true}' | python3 -m json.tool
# expect plan.settings.changed_keys.tickers from ["AAPL","TSLA"] to snapshot value; NO files changed

# 5. apply + backup
curl -s -X POST localhost:7788/api/config/import -d '{"dry_run":false}' | python3 -m json.tool
python3 -c "import json;print(json.load(open('$HOME/.hermes/dashboard/settings.json'))['tickers'])"  # snapshot value restored
ls ~/.hermes/dashboard/snapshot-backups/ | tail -1                        # pre-restore-*.json exists

# 6. fail-closed gates
python3 - <<'EOF'
import json; p='docs/state-snapshot.json'; s=json.load(open(p))
s['agent_config']['approvals.mode']='auto'; json.dump(s,open(p,'w'))
EOF
curl -s -X POST localhost:7788/api/config/import -d '{"dry_run":false}'
# expect ok:false, "approvals.mode must be manual", HTTP 400; settings.json unchanged
python3 - <<'EOF'
import json; p='docs/state-snapshot.json'; s=json.load(open(p))
s['agent_config']['approvals.mode']='manual'
s['dashboard']['settings']['quicklinks']=[{"label":"x","url":"https://x.com/<YOUR_TELEGRAM_USER_ID>:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}]
json.dump(s,open(p,'w'))
EOF
curl -s -X POST localhost:7788/api/config/import -d '{"dry_run":true}'
# expect ok:false secret-like value detected (telegram-bot-token)
curl -s -X POST localhost:7788/api/config/export -d '{}' >/dev/null      # regenerate clean file

# 7. unknown-widget resilience
python3 - <<'EOF'
import json; p='docs/state-snapshot.json'; s=json.load(open(p))
s['dashboard']['layout']['order'].append('bogus_widget'); json.dump(s,open(p,'w'))
EOF
curl -s -X POST localhost:7788/api/config/import -d '{"dry_run":true}' | grep bogus_widget
# expect it ONLY inside dropped_unknown

# 8. headless renderer harness (the expand.js pattern)
node - <<'EOF'
const fs=require('fs');
global.esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
global.document={getElementById:()=>null,createElement:()=>({style:{},classList:{add(){}}}),body:{appendChild(){}}};
global.window={}; global.animate=()=>{};
eval(fs.readFileSync('dashboard/config.js','utf8'));
const plan={layout:{changed:true,adds:['crypto'],removes:[],reordered:true,dropped_unknown:['bogus']},
 settings:{changed_keys:{tickers:{from:['AAPL'],to:['AAPL',7]}}},   // number → catches esc-on-number throws
 models:{roster_changed:false,added:[],removed:[],active:{from:'a/b',to:'c/d',downloaded:false,will_apply:false}},
 agent_config:{changes:{},verify_only:{'approvals.mode':'manual'}}};
const html=cfgPlanRows(plan);
if(!html.includes('bogus')||!html.includes('tickers')) throw new Error('plan render missing rows');
console.log('cfgPlanRows OK', html.length, 'chars');
EOF

# 9. UI smoke (manual): ⌘R the app → Hub → "Config" button visible; open modal;
#    Export; edit a ticker in the markets pop-out; reopen Config → settings pill
#    amber; Preview → plan row; Apply (confirm sheet appears — WKWebView native);
#    hub re-renders with old ticker; all timestamps 12-hour; zero emoji anywhere.
```

---

## Effort & sequencing

Total ≈ S (DEVPLAN scores it S; this design keeps it there): ~½ day backend,
~½ day frontend + verify.

1. **`config_snapshot.py`** — build/scan/dump/validate/diff (pure functions
   first; testable via `python3 -c` before any route exists).
2. **server.py hooks** (4 tiny edits above) + curl tests 2–7. server.py and
   index.html are shared files — these edits are the ONLY ones and must not
   run in parallel with another agent's edits to the same files.
3. **config.js** + the 1-line index.html script tag + harness test 8 + smoke 9.
4. **Docs**: add a "Config as code" paragraph to CLAUDE.md (export → commit →
   restore flow) and tick DEVPLAN Phase 1 #6 — that's the "README says so"
   acceptance from the phase plan.

Dependencies: none hard. **P1.3** (permission tiers) — `permission_tiers` is
pass-through, so P1.6 can ship first; when P1.3 lands, confirm its
settings.json key name and add it to `SETTINGS_ALLOW` if it differs (one-line
change). **P1.1** (editable memory) — explicitly out of scope here; memory is
data, not config. **P1.5** metrics.jsonl — excluded (runtime telemetry).
Fits the DEVPLAN §7 release rhythm: an export right before each phase tag is
the natural checkpoint artifact.

---

## Open questions / risks

- **Is `weather_city` alone acceptable?** We drop lat/lon for privacy; the
  city string is still mildly personal but is already the user's explicit
  setting and the repo is private. If the repo ever goes public, add an
  `exclude: ["settings.weather_city"]` export option (schema stays 1 —
  omitted keys are already legal).
- **`permission_tiers` key name** is a guess at P1.3's storage; pass-through
  design means a rename costs one constant. Coordinate at P1.3 review.
- **Snapshot in `docs/` vs a `config/` dir**: single file in docs/ chosen for
  diffability and zero directory sprawl; if P2+ wants per-section files
  (e.g., Watchtower triggers), bump to schema 2 with a `config/` split then —
  schema gate already refuses forward files cleanly.
- **`hermes config set` side effects**: it rewrites config.yaml (comments may
  not survive — upstream behavior). We only ever set `model.context_length`
  through it, same as the model switcher already does for `model.default`;
  acceptable, but note it in CLAUDE.md.
- **Should apply auto-`git commit`?** No — DEVPLAN §7 and the global rule say
  commits are user-initiated; the UI's copyable command keeps the loop tight
  without the server touching git. Revisit only if the user asks for
  one-click checkpointing.
- **Restore of a snapshot exported on another machine** (future second Mac):
  model roster ids are portable; `downloaded` gates the switch; layout and
  settings are machine-independent. No blocker, but untested until a second
  machine exists.
