# Metrics Baseline — design spec (P1.5)

Instrument and expose the DEVPLAN Section 6 success metrics so "fast / smart /
trustworthy" is provable with numbers, not vibes. A lightweight in-process
collector (ring buffers) + JSONL persistence in `~/.hermes/metrics/` + a
`GET /api/metrics` endpoint + a compact "Vitals" strip at the top of the
Console view. Zero new dependencies (stdlib only), zero risk to the chat path
(every metric op is fail-silent), and it follows the exec-include integration
pattern: bulk in a NEW module `dashboard/metrics_extra.py`, only a few quoted
inline hooks in shared files.

DEVPLAN targets this makes visible: **TTFT p50 < 1.5s** (p95 < 3s), hub API
server-side **p95 < 100ms** (currently ~35ms), **idle footprint ≤ 6GB** on the
8B path (MoE ≤ 20GB), plus turn latency, tokens/sec, model load time, approval
counts, undo counts.

---

## Goal & acceptance criteria

Done means:

1. Every dashboard chat turn produces one `kind:"turn"` record with
   `ttft_ms`, `turn_ms`, `est_tok_per_sec`, `path` (`serve`/`oneshot`),
   `ok`, and approval counts — visible in `GET /api/metrics` and appended to
   `~/.hermes/metrics/metrics-YYYY-MM-DD.jsonl` within 1s of turn completion.
2. TTFT is measured at the exact point specified below (first `message.delta`
   that appends text, relative to job creation in `POST /api/chat`), and a
   turn whose approval prompt fired *before* the first token is flagged
   `ttft_clean:false` and excluded from TTFT percentiles (human wait time must
   not poison the p50).
3. RAM envelope is sampled every 120s via `footprint(1)` (never `ps` RSS),
   *reusing the existing 60s `_cached("mlx_ram", …)` entry* so no extra
   `footprint` process runs beyond what `/api/models` already causes; each
   sample is labeled `idle` / `active` / `paused` and `/api/metrics` reports
   `idle_gb_p95` and `active_gb_p95` against the 6GB target.
4. `GET /api/hub` server-side latency is recorded per request;
   `/api/metrics` reports its p50/p95 over the window and it reads ~35ms today
   (i.e. the instrumentation itself doesn't regress it past 100ms p95).
5. Model load time is recorded: a model switch or resume produces one
   `kind:"model_load"` record with `ms` and `trigger`; an unattended restart
   (memory guard / crash) produces one with `trigger:"observed"` at ±15s
   resolution.
6. `POST /api/metrics/count {"name":"undo"}` increments a persistent counter
   (this is the hook P1.2's `/undo` will call); invalid names are rejected 400;
   counters survive a dashboard restart via `~/.hermes/metrics/counters.json`.
7. The Console view shows a "Vitals" strip (injected by `/metrics.js`, no
   Console markup edits) with TTFT p50, turn p50, hub p95, RAM, est tok/s,
   approvals, undo count, last model load — with target badges, a 24h/7d
   window toggle, loading/empty/error states, 12-hour times, bespoke SVG icon,
   no emoji.
8. Kill test: `chmod 000 ~/.hermes/metrics` (or fill the disk) and chat still
   works — metrics degrade to ring-only and `/api/metrics` reports
   `"persist_error"`; nothing in the chat path ever raises. Verified with
   `python3 -m py_compile`, `node --check`, curl, and the headless renderer
   harness.

---

## Data model

### Files

```
~/.hermes/metrics/                      (0755, created on module load)
  metrics-YYYY-MM-DD.jsonl              one file per LOCAL day, append-only
  counters.json                         persistent lifetime counters
```

- Daily rotation: filename computed at each write from `time.localtime()`.
- Retention: files older than 30 days deleted on module load and once per day
  from the sampler loop.
- Runaway guard: if today's file exceeds **50 MB**, persistence stops for the
  day (ring buffers keep working; `/api/metrics` reports
  `"persist_error":"size cap"`).
- `counters.json` is written with the existing atomic `write_json()`
  (tmp + `os.replace`), read with `read_json()`, guarded by `_MET_LOCK`.

### JSONL record schemas (one JSON object per line, compact separators)

`kind:"turn"` — one per completed dashboard chat turn:
```json
{"ts": 1751702400.123, "kind": "turn", "job": "ab12cd34ef56",
 "ttft_ms": 1240, "setup_ms": 310, "serve_ttft_ms": 930,
 "turn_ms": 8400, "decode_ms": 7160,
 "est_tokens_out": 420, "est_tok_per_sec": 58.7,
 "path": "serve", "ok": true, "ttft_clean": true,
 "approvals": 1, "approved": 1, "denied": 0,
 "model": "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"}
```
- `ttft_ms` — first streamed token relative to job creation; `null` on the
  oneshot path (no streaming) and on error-before-first-token.
- `setup_ms` — job creation → `prompt.submit` accepted (`null` if the 1-line
  hermes_rpc hook is absent); `serve_ttft_ms = ttft_ms - setup_ms`.
- `decode_ms` — first token → done. `est_tokens_out = len(final_text)/4`
  (integer), `est_tok_per_sec = est_tokens_out / (decode_ms/1000)` — both
  explicitly estimates (chars/4), never presented as exact.
- `path` — `"serve"` iff `prompt.submit` succeeded (detected by presence of
  the `_submitted_ts` stamp, see hooks), else `"oneshot"`.
- No prompt text, no reply text, no session title, no session id — **only**
  the 12-hex job id and numbers.

`kind:"hub_api"` — one per `GET /api/hub` request:
```json
{"ts": 1751702401.0, "kind": "hub_api", "ms": 34.2}
```

`kind:"ram"` — one per sampler tick (120s):
```json
{"ts": 1751702520.0, "kind": "ram", "gb": 17.9, "state": "idle"}
```
`state` ∈ `idle` (no live chat job in the last 120s), `active` (a `CHAT_JOBS`
entry not `done`), `paused` (`agent_paused()` true → `gb` is `null`).

`kind:"model_load"`:
```json
{"ts": 1751702600.0, "kind": "model_load", "ms": 41000,
 "model": "mlx-community/Hermes-3-Llama-3.1-8B-4bit", "trigger": "switch"}
```
`trigger` ∈ `switch` | `resume` | `observed` (passive offline→online
transition seen by the 15s watcher; resolution ±15s).

`kind:"count"` — generic counters (undo, and anything future):
```json
{"ts": 1751702700.0, "kind": "count", "name": "undo", "n": 1}
```

### `counters.json`
```json
{"undo": 3, "approvals_requested": 12, "approvals_approved": 9,
 "approvals_denied": 3, "model_loads": 4, "turns": 87, "turns_err": 2}
```

### In-memory ring buffers (module globals in `metrics_extra.py`)

```python
MET_RINGS = {
    "turn":       collections.deque(maxlen=256),
    "hub_api":    collections.deque(maxlen=512),
    "ram":        collections.deque(maxlen=720),   # 24h at 120s
    "model_load": collections.deque(maxlen=32),
    "count":      collections.deque(maxlen=128),
}
_MET_LOCK = threading.Lock()          # guards rings + counters + file append
_MET_COUNTERS = read_json(COUNTERS_FILE, {})
```

Every record goes ring-first, then best-effort JSONL append. Rings alone
cover the default 24h window; `?days=N` (2–30) additionally parses the last N
daily files (result cached 300s via the existing `_cached`).

---

## Backend

All bulk logic lives in **NEW file `dashboard/metrics_extra.py`**, exec'd into
server.py globals **after** `expanders_extra.py` and still **before
`class Handler`** (exec-include ORDER RULE: our redefinitions of `_new_job`,
`hub_data`, `switch_model`, `agent_power` win because they run last, and we
wrap the post-expanders versions of everything). The module imports its own
deps at the top (exec'd code cannot rely on server.py's function-local
imports):

```python
import collections
import json
import os
import re
import threading
import time
```
(`HOME`, `read_json`, `write_json`, `_cached`, `CHAT_JOBS`, `agent_paused`,
`active_model`, `model_online`, `_mlx_footprint_gb` are already in the shared
globals — same pattern expanders_extra.py uses for `HOME`/`STATE_DB`.)

Guard against double-exec: the whole module body is wrapped in
`if not globals().get("_METRICS_LOADED"):` … ending with
`_METRICS_LOADED = True`.

### Core collector functions (in metrics_extra.py)

```python
def metrics_record(kind, **fields):
    """Ring + JSONL append. NEVER raises — fully try/except'd."""

def metrics_count(name, n=1):
    """Bump persistent counter + emit a kind:'count' record. Never raises."""

def _met_pctl(vals, p):
    """Nearest-rank percentile of a list; None if empty."""
```

`metrics_record` implementation notes: `rec = {"ts": round(time.time(), 3),
"kind": kind, **fields}`; under `_MET_LOCK` append to
`MET_RINGS.get(kind, MET_RINGS["count"])`; then append
`json.dumps(rec, separators=(",", ":")) + "\n"` to today's file inside its own
try/except, setting module global `_MET_PERSIST_ERR` on failure (cleared on
next success).

### TTFT measurement point (exact)

**t0** = job creation: `time.time()` captured in the redefined `_new_job()`
inside the `POST /api/chat` handler thread — i.e. after body parse and the
`agent_paused()` fast-fail, immediately before the `_chat_worker` thread
spawns. This is within single-digit ms of the user's submit reaching the
server, and it is *before* serve session setup, so it matches DEVPLAN's
"submit → first visible token" definition.

**first token** = the first `message.delta` event: in
`hermes_rpc.run_turn()` every delta executes `job["text"] = text`
(hermes_rpc.py line ~252). The job is a `MeteredJob` (dict subclass, below),
so that assignment — the first one where the value is non-empty — stamps
`first_token_ts`. **`ttft_ms = (first_token_ts - t0) * 1000`.** No polling,
no event-stream forking, no hermes_rpc surgery for the primary number.

**Component split** (one optional 1-line hook, quoted below):
`job["_submitted_ts"] = time.time()` right after `prompt.submit` returns in
run_turn. Then `setup_ms = _submitted_ts - t0` (session status/resume/create +
submit RPC) and `serve_ttft_ms = ttft_ms - setup_ms` (pure model prefill +
first decode). The `_submitted_ts` key is invisible to the UI: the poll
endpoint serializes only named fields, never the whole job dict.

**Approval poisoning**: if `approval.request` fires before the first token,
the human's decision time would inflate TTFT. `MeteredJob` stamps
`first_approval_ts`; the turn record gets
`ttft_clean = first_token_ts is None or first_approval_ts is None or
first_token_ts < first_approval_ts`, and `metrics_payload` computes TTFT
percentiles only over `ttft_clean` turns.

### `MeteredJob` and the redefined `_new_job`

```python
class MeteredJob(dict):
    """CHAT_JOBS entry that timestamps its own lifecycle via __setitem__.
    Constructed with the full initial mapping (dict.__init__ bypasses
    __setitem__, so initial empty fields don't count as events)."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.t0 = time.time()
        self.first_token_ts = None
        self.first_approval_ts = None
        self.submitted_ts = None
        self.n_approvals = 0
        self.n_approved = 0
        self.n_denied = 0
        self.recorded = False

    def __setitem__(self, k, v):
        try:
            now = time.time()
            if k == "text" and v and self.first_token_ts is None:
                self.first_token_ts = now
            elif k == "_submitted_ts":
                self.submitted_ts = v
            elif k == "approval" and v:
                self.n_approvals += 1
                if self.first_approval_ts is None:
                    self.first_approval_ts = now
            elif k == "pending_choice":
                if v == "approve": self.n_approved += 1
                elif v == "deny":  self.n_denied += 1
            elif k == "done" and v and not self.recorded:
                self.recorded = True
                _met_finish_turn(self, now)     # builds + records kind:"turn"
        except Exception:
            pass                                # metrics NEVER break a turn
        super().__setitem__(k, v)

    def update(self, *a, **kw):                 # dict.update bypasses
        for k, v in dict(*a, **kw).items():     # __setitem__ — route it back
            self[k] = v
```

Critical CPython detail (why `update` is overridden): `dict.update` on a
subclass does **not** call the overridden `__setitem__`, and both
`hermes_rpc.run_turn` and `_chat_worker` finish jobs via `job.update(...)`.
`job.pop("pending_choice", …)` in run_turn is unaffected (pop needs no hook —
the count was taken at set time in `/api/chat/approve`).

`_met_finish_turn(job, now)` derives: `turn_ms = (now - job.t0)*1000`;
`ttft_ms` / `setup_ms` / `serve_ttft_ms` as above (clamped `>= 0`, `null`
when missing); `path = "serve" if job.submitted_ts else "oneshot"`;
`est_tokens_out = len(job.get("reply") or job.get("text") or "") // 4`;
`decode_ms = (now - job.first_token_ts)*1000` when streamed;
`est_tok_per_sec` only when `decode_ms > 200` (avoid absurd values on tiny
replies); `ok = bool(job.get("ok"))`; `model` from
`_cached("metrics_model", 60, active_model)`. Bumps counters `turns`,
`turns_err`, `approvals_requested/approved/denied`.

Redefined `_new_job` (verbatim behavior of the inline one at server.py:875,
only the constructor changes):

```python
def _new_job(session):
    jid = uuid.uuid4().hex[:12]
    job = MeteredJob({"id": jid, "session": session, "state": "running",
                      "text": "", "status": "", "approval": None, "reply": "",
                      "ok": False, "done": False, "ts": time.time()})
    with _jobs_lock:
        for k in [k for k, v in CHAT_JOBS.items()
                  if v.get("done") and time.time() - v["ts"] > 3600]:
            CHAT_JOBS.pop(k, None)
        CHAT_JOBS[jid] = job
    return job
```
(`uuid` and `_jobs_lock` are in shared globals; add `import uuid` to the
module header anyway for self-sufficiency.)

### Wrapped providers (redefinitions in metrics_extra.py)

```python
_met_orig_hub_data = hub_data
def hub_data():
    t0 = time.perf_counter()
    out = _met_orig_hub_data()
    metrics_record("hub_api", ms=round((time.perf_counter() - t0) * 1000, 1))
    return out

_met_orig_switch_model = switch_model
def switch_model(mid):
    out = _met_orig_switch_model(mid)
    if out.get("ok") and out.get("loading"):
        _met_arm_load_watch(mid, "switch")      # sampler resolves it
    return out

_met_orig_agent_power = agent_power
def agent_power(action):
    out = _met_orig_agent_power(action)
    if action == "resume" and out.get("ok", True):
        _met_arm_load_watch(active_model(), "resume")
    return out
```

`_met_arm_load_watch(model, trigger)` sets module globals
`{"model": model, "trigger": trigger, "ts": time.time()}`; the sampler loop
polls `model_online()` every 15s while armed (max 15 min, then discard) and on
the first `True` emits `kind:"model_load"` with
`ms = (now - armed_ts) * 1000` and bumps counter `model_loads`.

### Sampler thread

```python
def metrics_sampler_loop():
    """15s tick: model-online watcher (armed loads + passive transitions).
    Every 8th tick (120s): one RAM sample. Daily: JSONL GC."""
```

RAM sampling **without spiking cost**: the sample is
`_cached("mlx_ram", 60, _mlx_footprint_gb)` — the *same* cache key
`models_payload()` already uses (server.py:1948), so `footprint(1)` (which
spawns a process and can take seconds) runs **at most once per 60s
machine-wide** no matter how many consumers exist; at a 120s sampler period it
usually costs zero extra spawns beyond the UI's own model-pill polling. When
`agent_paused()`: record `{"gb": null, "state": "paused"}` without touching
footprint at all. State classification: `active` if any `CHAT_JOBS` value has
`done == False` or finished `< 120s` ago, else `idle`. (Telegram/CLI turns
don't create CHAT_JOBS — see edge cases.)

Passive load detection: the 15s `model_online()` tick keeps
`(last_state, offline_since)`; an offline→online transition with no armed
watch emits `model_load` with `trigger:"observed"`,
`ms = (now - offline_since)*1000`. Never runs while `agent_paused()`.

### Endpoints

**GET `/api/metrics`** — query `?days=N` (int, clamp 1–30, default 1).
`days=1` computes from rings only (cheap, no I/O); `days>1` merges rings with
parsed JSONL files, memoized 300s via `_cached(("metrics", days), …)`.

Response `200`:
```json
{"ok": true, "window_days": 1, "since": 1751650000.0,
 "persist_error": null,
 "turns": {"n": 42, "err": 1,
           "ttft_ms":  {"p50": 1210, "p90": 2400, "p95": 2900, "n": 40},
           "turn_ms":  {"p50": 7400, "p90": 21000, "p95": 33000, "n": 42},
           "setup_ms": {"p50": 300, "n": 40},
           "est_tok_per_sec": {"p50": 55.1, "n": 38},
           "paths": {"serve": 41, "oneshot": 1}},
 "hub_api": {"p50": 33.0, "p95": 61.2, "n": 118},
 "ram": {"last": {"gb": 17.9, "state": "idle", "ts": 1751702520.0},
         "idle_gb_p95": 18.2, "active_gb_p95": 19.6, "samples": 640},
 "model": {"active": "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit",
           "last_load": {"ms": 41000, "trigger": "switch", "ts": 1751690000.0},
           "loads": 4},
 "counters": {"undo": 0, "approvals_requested": 3, "approvals_approved": 2,
              "approvals_denied": 1, "turns": 87, "turns_err": 2,
              "model_loads": 4},
 "targets": {"ttft_p50_ms": 1500, "ttft_p95_ms": 3000,
             "hub_p95_ms": 100, "idle_gb": 6, "moe_idle_gb": 20}}
```
Empty state (no data yet): same shape, `"n": 0` everywhere, percentiles
`null`. Errors: never 500 — internal failures return
`{"ok": false, "error": "<msg>"}` with status 200 (matches house style of
fault-tolerant providers). Implemented as `def metrics_payload(q):` taking the
parsed query dict.

**POST `/api/metrics/count`** — body `{"name": "undo", "n": 1}`.
Validation: `name` must match `^[a-z0-9_.-]{1,40}$`; `n` int 1–1000
(default 1). Success `200 {"ok": true, "name": "undo", "total": 4}`.
Bad name/n → `400 {"ok": false, "error": "bad name"}`. Only `name`/`n` are
accepted — arbitrary labels are refused (no user-content sink). Implemented
as `def metrics_count_api(body):` returning the response dict; the route hook
maps `ok:false → 400`. This is the integration point for **P1.2 undo**
(server-side: call `globals().get("metrics_count", lambda *a, **k: None)("undo")`
directly; the HTTP form exists for non-exec'd callers).

### EXACT inline hooks (the only shared-file edits)

**server.py hook 1 — exec-include** (insert immediately after the existing
`expanders_extra.py` exec block that ends `file=sys.stderr)` at ~line 2029,
before `class Handler`):
```python
# Metrics baseline (P1.5): collector, MeteredJob, /api/metrics. Must exec
# AFTER expanders_extra so its redefinitions (hub_data, _new_job, …) win.
try:
    with open(os.path.join(HERE, "metrics_extra.py")) as _f:
        exec(_f.read(), globals())
except Exception as _e:
    print(f"[metrics_extra] failed to load: {type(_e).__name__}: {_e}",
          file=sys.stderr)
```

**server.py hook 2 — GET route** (insert after the `/api/mind_extra` elif,
~line 2132):
```python
        elif path == "/api/metrics":
            q = urllib.parse.parse_qs(parsed.query)
            fn = globals().get("metrics_payload")
            self._json(fn(q) if fn else {"ok": False, "error": "metrics module not loaded"})
```

**server.py hook 3 — POST route** (insert before the `/api/chat` block,
~line 2316):
```python
        if path == "/api/metrics/count":
            fn = globals().get("metrics_count_api")
            out = fn(self._body_json()) if fn else {"ok": False, "error": "unavailable"}
            self._json(out, 200 if out.get("ok") else 400)
            return
```

**server.py hook 4 — sampler thread in `main()`** (next to the existing
`system_sampler_loop` start, ~line 2366):
```python
    if "metrics_sampler_loop" in globals():     # P1.5 metrics baseline
        threading.Thread(target=globals()["metrics_sampler_loop"],
                         daemon=True).start()
```

**server.py hook 5 — static route** (edit the existing tuple at line 2071):
```python
        elif path in ("/motion.min.js", "/expand.js", "/metrics.js"):
```
(`/metrics.js` inherits the `no-store` branch of the existing Cache-Control
conditional, which is what we want.)

**hermes_rpc.py hook (1 line)** — after the `prompt.submit` call at ~line 228:
```python
        srv.call("prompt.submit", {"session_id": sid, "text": prompt},
                 timeout=30)
        job["_submitted_ts"] = time.time()   # metrics P1.5: setup/serve split
```
If a parallel workstream owns hermes_rpc.py at build time, this hook is
**optional** — everything else works; `setup_ms`/`serve_ttft_ms` are `null`
and `path` detection falls back to `"serve" if job.first_token_ts or
job.get("status") != "serve backend unavailable, using one-shot mode" else
"oneshot"` (implement this fallback regardless, so the module is correct with
or without the hook).

**index.html hook (1 line)** — after `<script src="/expand.js"></script>`
(line 2049):
```html
<script src="/metrics.js"></script>
```

---

## Frontend

**New file `dashboard/metrics.js`**, served at `/metrics.js` (hook 5), loaded
after both the inline script and expand.js so its assignments override.

### UX walkthrough

1. User opens the **Console** tab. Above the existing "Agent Console" card, a
   new **Vitals** card appears: a single dense row of stat tiles under a
   header with a bespoke pulse-gauge SVG (two-tone: accent fill +
   currentColor stroke, same recipe as `WICONS`) and a 24h / 7d segmented
   toggle.
2. Tiles (left→right):
   - **TTFT** — `1.2s` (p50), sub-label `p95 2.9s · target <1.5s`. Badge dot:
     green ≤ target, amber ≤ 1.5× target, red beyond.
   - **Turn** — p50 seconds, sub `p95`.
   - **Hub API** — p95 ms, sub `p50 · target <100ms`, badged.
   - **RAM** — last sample `17.9GB · idle`, sub `idle p95 18.2 · target ≤6GB
     (8B) / ≤20GB (MoE)`, badged against the MoE target when the active model
     id contains `A3B` (30B MoE), else the 6GB target. `paused` renders an
     em-dash with sub-label `model paused`.
   - **Tok/s (est)** — p50, sub `estimated · chars/4`.
   - **Approvals** — `3` requested, sub `2 approved · 1 denied`.
   - **Undo** — lifetime counter (from P1.2; shows `0` until it ships).
   - **Model load** — last load `41s · switch`, with the load's local time in
     12-hour format (`9:41 PM`).
3. Toggling **7d** refetches `/api/metrics?days=7` and re-renders; the choice
   persists in `localStorage['hermes_metrics_win']`.
4. Values refresh while the Console is visible, piggybacking the existing
   3s console poll but throttled to one `/api/metrics` fetch per 15s.

### Wiring (no markup in index.html beyond the script tag)

- On load, `metrics.js` builds the card DOM
  (`id="vitals-card"`, `class="card glass"`, `style="grid-column:1/3"`) and
  prepends it into `#view-console`. All styles it needs beyond existing
  classes are injected via one `<style id="vitals-css">` block (tile grid,
  badge dots, toggle) using the existing CSS custom properties — both themes
  come free because only palette tokens are referenced.
- It wraps the inline poller:
  ```js
  const _origLoadConsole = window.loadConsole;
  window.loadConsole = async function(){
    await _origLoadConsole();
    vitalsMaybeRefresh();          // no-op unless 15s have passed
  };
  ```
  This works because the inline `setInterval(...loadConsole()...)` and
  `setView('console')` resolve `loadConsole` at call time — the established
  load-after-override pattern. It also calls `vitalsMaybeRefresh()` once
  immediately if the Console is the restored view.
- Rendering is null-safe by construction: every numeric goes through
  `fmtMs(v)` / `fmtGb(v)` helpers that return `'—'` for `null`/`undefined`
  (the `esc`-on-number throw class is exactly what the headless harness
  checks).

### States

- **Loading**: skeleton tiles (reuse `.skel`).
- **Empty** (`turns.n === 0`): tiles show `—` and the card sub-line reads
  "No turns recorded yet — send a chat message to log the first TTFT."
- **Error** (fetch throws or `ok:false`): card stays, values keep last
  render, a `.tiny` line shows "metrics unavailable"; if `/api/metrics` 404s
  (stale server), the card removes itself.
- **Persist degraded** (`persist_error` non-null): small hairline note
  "in-memory only — persistence error".

### Animations

Motion One via the global `animate()`: on value change,
`animate(el, {opacity:[0.35,1], transform:['translateY(2px)','none']},
{duration:0.3})`; the badge dot gets a one-pulse scale on target-state change.
Guard: `matchMedia('(prefers-reduced-motion: reduce)')` → skip animations.
Nothing animates continuously; no blur is animated (backdrop-filter budget).

---

## Edge cases & failure modes

- **`dict.update` bypass** — covered: `MeteredJob.update` re-routes through
  `__setitem__`; without it every turn-completion (`job.update(reply=…,
  done=True)`) would be invisible. `pop`/`get`/`in` need no hooks.
- **Metrics must never break a turn** — every `MeteredJob` hook body and
  `metrics_record`/`metrics_count` are wrapped in bare `try/except: pass`.
  A bug in metrics manifests as missing data, never as a chat error.
- **Concurrency** — multiple simultaneous jobs each carry their own stamps;
  ring/counter/file mutations are under `_MET_LOCK`; JSONL appends are single
  `write()` calls of one line (atomic enough for a single process; the
  dashboard is one launchd process by design). The reader skips unparsable
  lines (`json.loads` per line in try/except) so a torn write costs one line.
- **Double-record on done** — `recorded` flag; `done=True` can arrive via
  `update` from both run_turn paths and the timeout path, only the first
  records.
- **Approval before first token** — `ttft_clean:false`, excluded from TTFT
  percentiles but still counted in `turn_ms` (the turn genuinely took that
  long) — documented in the payload via per-metric `n`.
- **Oneshot fallback** — no deltas: `ttft_ms:null`, `path:"oneshot"`,
  `turn_ms`/`est_tokens_out` still valid. Detected by missing
  `_submitted_ts` stamp (primary) or the fallback status string (secondary).
- **Model paused** — `/api/chat` returns before `_new_job`: no phantom turns.
  RAM sampler records `state:"paused", gb:null` without spawning `footprint`.
- **`footprint` unavailable/slow** — `_mlx_footprint_gb` already returns
  `None` on any failure (and has its own timeouts); a `None` non-paused sample
  is recorded as `gb:null, state:"idle"|"active"` and excluded from
  percentiles. The 60s shared cache bounds cost even if the UI polls
  `/api/models` aggressively.
- **Telegram/CLI turns** — invisible to CHAT_JOBS: their RAM impact may be
  mislabeled `idle`. Accepted for v1 (labeled limitation; a state.db
  recent-activity check was rejected as too costly per sample). Their TTFT is
  also not measured (dashboard-path metric only) — DEVPLAN's target is the
  hub chat path.
- **Missing/deleted `~/.hermes/metrics/`** — `os.makedirs(exist_ok=True)` on
  load and on each failed append (one retry); permission failure → ring-only
  mode + `persist_error` surfaced. Never raises.
- **Huge files / disk full** — 50MB/day cap checked with `os.path.getsize`
  every ~100 appends (cached count); `OSError` on write sets `persist_error`.
  30-day GC on load + daily in sampler.
- **Malformed counters.json** — `read_json` default `{}`; non-int values
  coerced with `int()` in try/except, else reset to the increment.
- **Clock changes / NTP jumps** — all durations clamped `max(0, …)`;
  a backwards jump yields 0-ish values, not negatives; day rollover mid-write
  just lands the line in the new day's file.
- **`?days=` abuse** — clamp 1–30; non-integer → 1; `days>1` file parse is
  memoized 300s so hammering the endpoint can't cause repeated multi-MB
  parses; per-file line cap (200k lines) guards a corrupt giant file.
- **exec-include failures** — if `metrics_extra.py` fails to exec, server.py
  prints the `[metrics_extra]` stderr line and everything else still works:
  `_new_job` stays the inline version, `/api/metrics` answers
  `{"ok":false,"error":"metrics module not loaded"}`, metrics.js sees no 404
  (endpoint exists) and shows the error state; if the *route hooks* are also
  missing (stale server.py), metrics.js gets a 404 and removes the card.
- **Double exec** — `_METRICS_LOADED` guard prevents re-wrapping
  `hub_data`/`switch_model`/`agent_power` (wrap-of-wrap would double-record).
- **Job GC** — records are emitted at event time, so the 1-hour CHAT_JOBS GC
  never loses data; a job GC'd mid-run (impossible today — GC only removes
  `done`) would simply never emit a turn record.
- **Load watch never resolves** (model fails to come up, MetalGuard
  territory) — armed watch discarded after 15 min; a `model_load` with
  `trigger:"observed"` may fire later when it finally boots; no unbounded
  state.
- **Offline machine** — zero network dependencies; everything is loopback or
  filesystem.
- **WKWebView staleness** — after editing index.html/metrics.js the app needs
  ⌘R (documented gotcha; in the test plan).

---

## Security & safety

- **Local-first**: the collector reads only local process/file state; no
  inference, no metric, no byte leaves the machine. `/api/metrics` binds with
  the existing server on `127.0.0.1:7788` only.
- **No content capture**: turn records contain *numbers plus the model id and
  a random job id* — never prompt text, reply text, tool args, session ids,
  titles, or memory contents. This is enforced by schema (fields are
  explicitly constructed, never `**job`). The JSONL can be `cat`'d to anyone
  without leaking a conversation.
- **No secrets**: nothing under `~/.hermes/.env`, serve-token, or config is
  read or logged. `metrics_extra.py` never touches the WS token.
- **Approvals invariant untouched**: metrics only *observes*
  `approval`/`pending_choice` writes; it cannot respond, auto-approve, or
  reorder — `approvals.mode: manual` semantics are unchanged. The counter is
  evidence *for* the trust story (DEVPLAN: "zero approvals silently dropped"
  becomes checkable: `approvals_requested == approved + denied + pending`).
- **Write surface is inert**: the only mutating endpoint is
  `/api/metrics/count` with a strict name regex and integer cap — it can only
  bump a counter, never execute, never write outside
  `~/.hermes/metrics/counters.json`. It must refuse (400) anything else:
  extra keys ignored, non-conforming names rejected, `n` outside 1–1000
  rejected.
- **No Gmail/Telegram/computer-use interaction** of any kind; nothing here
  expands agent capability — it is read-only observability plus one counter.

---

## Test plan

Static checks:
```bash
python3 -m py_compile ~/HermesAssistant/dashboard/metrics_extra.py \
                      ~/HermesAssistant/dashboard/server.py   # → silence
node --check ~/HermesAssistant/dashboard/metrics.js           # → silence
python3 - <<'EOF'                       # exec-include smoke: module is self-sufficient
import types, collections, threading, time, os, json, re, uuid
g = dict(HOME=os.path.expanduser("~"),
         read_json=lambda p, d: d, write_json=lambda p, o: None,
         _cached=lambda k, t, f: f(), CHAT_JOBS={}, _jobs_lock=threading.Lock(),
         agent_paused=lambda: True, active_model=lambda: "test-model",
         model_online=lambda: False, _mlx_footprint_gb=lambda: None,
         hub_data=lambda: {}, switch_model=lambda m: {"ok": False},
         agent_power=lambda a: {"ok": False}, uuid=uuid)
exec(open("~/HermesAssistant/dashboard/metrics_extra.py").read(), g)
j = g["_new_job"]("s"); j["text"] = "hi"; j.update(reply="hi", ok=True, state="done", done=True)
p = g["metrics_payload"]({})
assert p["ok"] and p["turns"]["n"] >= 1 and p["turns"]["ttft_ms"]["p50"] is not None
print("collector OK", json.dumps(p["turns"]))
EOF
```

Live checks (after `launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard`):
```bash
curl -s 'localhost:7788/api/metrics' | python3 -m json.tool
#   → ok:true, turns.n 0 (fresh), targets block present, persist_error null

# one real turn end-to-end:
J=$(curl -s -X POST localhost:7788/api/chat -H 'Content-Type: application/json' \
    -d '{"message":"say the word ready and nothing else","session":"metricstest"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["job"])')
sleep 20; curl -s "localhost:7788/api/chat/poll?job=$J" | grep -o '"done": *true'
curl -s 'localhost:7788/api/metrics' | python3 -c 'import sys,json;t=json.load(sys.stdin)["turns"];assert t["n"]>=1 and t["ttft_ms"]["p50"]>0;print("TTFT p50 ms:",t["ttft_ms"]["p50"])'

# persistence:
ls ~/.hermes/metrics/                                    # metrics-$(date +%F).jsonl
tail -1 ~/.hermes/metrics/metrics-*.jsonl | python3 -m json.tool   # valid JSON line
grep -c '"kind":"ram"' ~/.hermes/metrics/metrics-*.jsonl # ≥1 after ~2 min uptime

# counters + validation:
curl -s -X POST localhost:7788/api/metrics/count -d '{"name":"undo"}'          # {"ok":true,...,"total":1}
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:7788/api/metrics/count \
     -d '{"name":"../evil"}'                                                    # 400
launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard && sleep 3
curl -s localhost:7788/api/metrics | grep -o '"undo": *1'                       # survived restart

# hub timing + window param:
curl -s localhost:7788/api/hub > /dev/null
curl -s 'localhost:7788/api/metrics?days=7' | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["hub_api"]["n"]>=1;print("hub p95 ms:",d["hub_api"]["p95"])'

# degraded persistence (acceptance #8):
chmod 000 ~/.hermes/metrics && curl -s -X POST localhost:7788/api/chat \
  -H 'Content-Type: application/json' -d '{"message":"hi","session":"metricstest"}' \
  | grep -o '"ok": *true' && curl -s localhost:7788/api/metrics | grep -o '"persist_error": *"[^"]*"'
chmod 755 ~/.hermes/metrics
```

Model load (manual, once): pause the agent from the model menu, resume, then
`curl -s localhost:7788/api/metrics | python3 -c 'import sys,json;print(json.load(sys.stdin)["model"]["last_load"])'`
→ `{"ms": <plausible 20000-90000>, "trigger": "resume", ...}`.

Headless renderer harness (the `esc`-on-number throw class), same pattern used
for expand.js:
```bash
curl -s localhost:7788/api/metrics > /tmp/met.json
node - <<'EOF'
const data = JSON.parse(require('fs').readFileSync('/tmp/met.json'));
const el = () => new Proxy({style:{},classList:{add(){},toggle(){}},dataset:{}}, {
  get(t,k){ return k in t ? t[k] : (k==='appendChild'||k==='prepend'||k==='remove'
    ? ()=>el() : (k==='querySelector'||k==='querySelectorAll' ? ()=>el() : t[k])); },
  set(t,k,v){ t[k]=v; return true; }});
global.window = global; global.localStorage={getItem:()=>null,setItem(){}};
global.document = {createElement:()=>el(),getElementById:()=>el(),
  querySelector:()=>el(),head:el(),body:el()};
global.matchMedia = () => ({matches:false});
global.fetch = async () => ({ok:true,status:200,json:async()=>data});
global.animate = () => {}; global.loadConsole = async () => {};
global.esc = s => String(s); global.$ = () => el();
require('~/HermesAssistant/dashboard/metrics.js');
setTimeout(async () => { await window.loadConsole();
  console.log('metrics.js render OK'); }, 50);
EOF
```
Also run it against the **empty-state** payload (`turns.n:0`, `null`
percentiles) and against `{"ok":false,"error":"x"}` — no throws allowed.

UI check: ⌘R in the app → Console tab → Vitals card renders, toggle 24h/7d
works, times are 12-hour, no emoji, no horizontal scroll at the narrowest
window width.

---

## Effort & sequencing

Total ≈ 1 dev-day equivalent. Order:

1. **`dashboard/metrics_extra.py`** — collector, `MeteredJob`, `_new_job`
   redefinition, wrappers, sampler, `metrics_payload`, `metrics_count_api`
   (~250 lines). Verify with the exec-smoke test before touching server.py.
2. **server.py hooks 1–5 + hermes_rpc 1-liner** (single small commit,
   `dash: wire metrics baseline (P1.5)`). Restart, run live curl checks.
3. **`dashboard/metrics.js` + index.html script tag**; headless harness; ⌘R.
4. **Soak**: leave running; after 24h confirm RAM series + at least one
   `model_load`; DEVPLAN wants "a week of metrics before Phase 2" — start the
   clock immediately, this workstream should land early in Phase 1.

Dependencies / coordination:
- **P1.2 (undo)** *consumes* this: its undo endpoint calls
  `metrics_count("undo")` (guarded `globals().get`). Ship metrics first or
  land the guard-style call regardless of order.
- **P1.4 (approval exercise)** validates the approval counters as a side
  effect — tell that workstream to check `/api/metrics` counters after its
  approve/deny drills (`requested == approved + denied`).
- **Shared-file conflicts**: this workstream edits server.py (4 insertions +
  1 tuple edit), index.html (1 line), hermes_rpc.py (1 line). Do NOT build in
  parallel with another agent editing those files; the bulk
  (metrics_extra.py, metrics.js) is conflict-free by design.
- **P1.5 MetalGuard** (same DEVPLAN workstream, separate build): the passive
  offline→online `model_load` events and `ram` series are its input signals;
  no code dependency either way.

---

## Open questions / risks

1. **Path discrepancy vs DEVPLAN**: Section 6 says
   `~/.hermes/dashboard/metrics.jsonl`; this spec (per the workstream ground
   truth) uses `~/.hermes/metrics/` with daily rotation — strictly better
   (rotation, GC, counters live together). Update DEVPLAN Section 6 wording at
   the Phase 1 boundary.
2. **Tokens are estimated** (chars/4). Real counts exist in state.db
   (`sessions.input_tokens/output_tokens`, already read by `mind_extra`), but
   joining a serve turn to a state.db session row needs the
   `stored_session_id` mapping — deferred; revisit when Phase 3 needs honest
   tok/s for the spec-decode break-even measurement.
3. **Perceived vs server TTFT**: the UI polls every 700ms, so user-perceived
   TTFT ≈ measured + ≤700ms + render. We record server-side (stable,
   comparable); if we later want perceived, metrics.js can time
   submit→first-render client-side and POST it — out of scope v1.
4. **Telegram approval counts** are not captured (dashboard-surface only).
   Acceptable for the baseline; a state.db-derived count could backfill later.
5. **`MeteredJob` observes internals of `hermes_rpc.run_turn`** (`text`,
   `approval`, `done` write patterns). Any refactor of run_turn's job
   contract must keep those writes (they're also the poll API's contract, so
   this is low risk — but note it in CLAUDE.md when landing).
6. **Fallback-path status string match** (secondary oneshot detector) is
   brittle by nature; primary detector (`_submitted_ts` absence) is
   structural. If the hermes_rpc hook is vetoed, `setup_ms` is lost — decide
   at build time whether the 1-liner is acceptable (recommended: yes).
7. **Ring sizes** (256 turns / 720 RAM samples) assume current usage; if the
   menu-bar quick-ask (Phase 2) multiplies turn volume, bump `maxlen` — the
   JSONL already holds everything.
