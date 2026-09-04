# Speculative Decoding on the mlx_lm Server — design spec (P3.3 / DEVPLAN Phase 3 #4)

Wire mlx_lm's built-in draft-model speculative decoding behind a measured,
reversible toggle, and settle the DEVPLAN acceptance question with real
numbers: **"measured decode speedup ≥1.5x on the main chat path or the
feature is cleanly off with the measurement recorded"** (DEVPLAN Phase 3 #4).

**Honest headline, stated up front:** speculative decoding is **supported
today** for our MoE target — verified by reading the *installed*
`mlx_lm 0.31.3` source (`~/Library/Python/3.12/lib/python/site-packages/
mlx_lm/server.py`), not release notes — but the *economics* for
Qwen3-30B-A3B are expected to be **marginal to net-negative**, and this spec
says so rather than promising 2.4x. The 2.4x-class wins in FUTURE.md are for
**dense** targets. The likely outcome of this workstream is: the 30B
measurement lands below 1.5x, the toggle stays **OFF** for the 30B with the
measurement recorded (explicitly a passing outcome per DEVPLAN), and the same
machinery gives a real win later on the dense 8B profile. The alternatives
that actually move our headline metric (TTFT) — prompt-cache/prefix
stability — are specified in "Alternatives" and are part of the acceptance.

---

## Research findings: what mlx_lm supports TODAY (verified 2026-07-05)

All claims below are grepped from the installed package
(`mlx_lm 0.31.3`, `mlx 0.31.2`, Apple **M5 Max / 64GB**), file
`.../site-packages/mlx_lm/server.py` (line numbers from that file) and
`generate.py`.

1. **`--draft-model` and `--num-draft-tokens` exist on the server CLI**
   (server.py ~1782–1792). Defaults: `--draft-model None`,
   `--num-draft-tokens 3`. The generate CLI (`python3 -m mlx_lm generate`)
   has the same two flags and prints real `tokens-per-sec` — our
   ground-truth measurement tool.
2. **MoE targets are mechanically supported.** The speculative path
   (`speculative_generate_step`, generate.py ~473–654) is
   architecture-agnostic: draft proposes k tokens, target verifies k+1 in
   one forward, caches are rewound via `trim_prompt_cache`. Requirement is
   only a *trimmable* cache; Qwen3-30B-A3B uses standard `KVCache` — fine.
   There is no MoE check or MoE codepath anywhere in the server.
3. **Vocab check is warning-only** (server.py ~363–370): mismatched draft
   tokenizer logs "Speculative decoding may not work as expected" and
   proceeds. We must enforce pairing ourselves (pairing table below).
   Verified locally: Qwen3-30B-A3B-Instruct-2507-4bit `vocab_size` =
   **151936**; all small Qwen3 models share it. Hermes-3-Llama-3.1-8B =
   **128256** (Llama-3.x family).
4. **A draft model disables batching**: `is_batchable = draft_model is None`
   (server.py ~371); requests then go through `_serve_single`
   (server.py ~922). No impact today — `mlx-server.sh` does not pass
   `--prompt-concurrency` — but draft-model mode and the FUTURE.md batching
   item (~2.2x aggregate) are **mutually exclusive**. Document, don't fight.
5. **Prompt cache still works with a draft**: `_serve_single` uses
   `prompt_cache.fetch_nearest_cache` and appends the draft's cache
   (`cache += make_prompt_cache(self.model_provider.draft_model)`,
   server.py ~970–973). Cache entries are keyed by
   `(model_path, adapter_path, draft_model_path)` — so toggling the draft
   changes the key (irrelevant in practice: we restart the server to toggle,
   and the cache is in-memory only). Draft KV counts against our existing
   `--prompt-cache-bytes 6000000000` cap; a 0.6B draft's KV is negligible.
6. **Acceptance rate is NOT exposed.** `from_draft` exists per token inside
   `stream_generate` (generate.py ~716–748) but the server drops it; no
   log line, no API field. DEVPLAN's "instrument acceptance rate live and
   auto-disable below 0.65" **cannot be done through the server today**
   without patching site-packages (which we will not do). Honest reframe:
   acceptance is measured **offline** with a small harness that calls
   `stream_generate` directly and counts `from_draft` (Test plan §C); the
   *online* break-even instrument is P1.5's `est_tok_per_sec` before/after.
7. **Per-request overrides exist but are a trap**: the request body accepts
   `draft_model` / `num_draft_tokens` (server.py ~1164–1166), but a
   *different* `draft_model` string changes the model key and triggers a
   **full model reload** mid-request (server.py ~387–400). We toggle at the
   server-launch level only; never per-request.
8. **TTFT cost is real**: with a draft, the draft model *also prefills the
   full prompt* (`_prefill(draft_model, draft_cache, y)`, generate.py ~604).
   On our ~6–10k-token agent prompts a 0.6B draft adds a few hundred ms of
   prefill before the first token. Spec decoding trades TTFT for decode
   speed — and DEVPLAN Section 6 says TTFT is the axis that matters. The
   decision rule below therefore gates on TTFT too.

### Why the 30B MoE expectation is "marginal, possibly negative"

- Qwen3-30B-A3B has only **~3.3B active parameters**; 4-bit decode is
  already memory-bandwidth-cheap. The draft:target cost ratio with a dense
  0.6B draft is roughly 1:3–1:5, far from the 1:10+ where 2x lives.
- MoE verification is disproportionately expensive: verifying k+1 tokens in
  one forward activates the **union of routed experts across those
  positions**, so the verify pass streams far more weights than one decode
  step — eroding exactly the batching gain spec decoding relies on.
- External corroboration (same A3B family shape): a public RTX 3090
  benchmark of llama.cpp speculative decoding on Qwen3.6-35B-A3B with a
  vocab-matched 0.8B draft found **no configuration beat baseline**
  ([github.com/thc1006/qwen3.6-speculative-decoding-rtx3090](https://github.com/thc1006/qwen3.6-speculative-decoding-rtx3090),
  [write-up](https://hackmd.io/ODXuOQNzSiyUITz7g9mtBw)). Apple-Silicon MLX
  numbers may differ (that's why we measure), but the mechanism (bandwidth-
  bound A3B + expert-union verify) is hardware-independent.
- FUTURE.md's own caveats point the same way: the 4.1x DFlash result
  degrades to ~1.9x when drafts bottleneck against 4-bit targets (FUTURE.md
  Reddit perf), and DFlash needs a *trained per-target draft head* — none
  published for Qwen3-30B-A3B-Instruct-2507 — plus a serving stack that is
  not `python3 -m mlx_lm server`. Watch item, not a plan.

Where spec decoding **should** win on this machine: the dense
**Hermes-3-8B profile** (DEVPLAN #13's always-on/light tier) with a
Llama-3.2-1B draft — the classic ~1.5–2x shape. This spec builds the
machinery once, pairing-table-driven, so the 8B experiment is free later.

---

## Goal & acceptance criteria

Done means:

1. `mlx-server.sh` reads an optional draft-model config file and launches
   `python3 -m mlx_lm server` with `--draft-model` + `--num-draft-tokens`
   **only when** (a) the file names a draft, (b) the draft pairs with the
   active target per the pairing table, and (c) the draft's weights are
   already on disk (never trigger an HF download at service boot).
   With the file absent/empty/invalid, the launch command is byte-identical
   to today's.
2. `GET /api/specdec` reports `{enabled, draft, num_draft_tokens,
   recommended pair for the active model, draft_downloaded, last bench}`;
   `POST /api/specdec` enables/disables (writing the file and restarting
   the mlx service via the existing `switch_model` machinery) and
   `POST /api/specdec/download` fetches the paired draft in a background
   thread. All three degrade gracefully if the aux module failed to load.
3. A written **A/B measurement** exists in
   `~/.hermes/metrics/specdec-bench.json` produced by the Test-plan
   procedure: chat-path `est_tok_per_sec` p50 and `ttft_ms` p50 (P1.5
   metrics), CLI true tokens-per-sec, and offline acceptance rate, for
   draft-off vs draft-on at `num_draft_tokens` ∈ {2, 3, 4}.
4. **Decision rule applied and recorded**: keep ON iff chat-path
   `est_tok_per_sec` p50 improves **≥1.5x** AND `ttft_ms` p50 regresses
   **≤10%**. Otherwise the toggle ends OFF, the numbers stay in the bench
   file and in `docs/FINDINGS.md`, and DEVPLAN Phase 3 #4 is satisfied via
   its explicit "cleanly off with the measurement recorded" branch.
5. A small "Speculative Decoding" card in the Console view (below Vitals)
   shows state, pair, bench results, and the toggle — no emoji, 12-hour
   times, both themes, works in the WKWebView (⌘R noted).
6. Chat keeps working through every state: draft on, draft off, draft file
   corrupt, draft weights missing, module failed to load. Nothing here can
   take the model server down permanently (worst case = one clean restart).
7. The agent gains **no new tool**: `/api/specdec` is a dashboard-user
   surface; permissions.py, the recorder contract, and the approval loop are
   untouched.

Explicit non-goals: DFlash/dflash-mlx integration (no published head for
our target; different serving stack); patching mlx_lm site-packages;
per-request draft switching; `--prompt-concurrency` batching (LATER per
DEVPLAN, and mutually exclusive with a draft anyway); auto-disable-on-live-
acceptance (impossible through today's server API — see finding 6).

---

## Data model

### 1. `~/.hermes/dashboard/draft-model` (new; sibling of `active-model`)

Single line, space-separated: `<hf_repo_id> [num_draft_tokens]`.

```
mlx-community/Qwen3-0.6B-4bit 3
```

Absent, empty, or unpaired-with-active-target ⇒ speculative decoding OFF.
Written only by `aux_specdec.py` (atomic: existing `write_json` pattern is
JSON-only, so use tmp-file + `os.replace` directly). Deleted on `disable`.

### 2. Pairing table (constant in `aux_specdec.py`, not settings.json)

```python
# target-id prefix            -> (draft repo id, why)
SPECDEC_PAIRS = {
    "mlx-community/Qwen3-30B-A3B-Instruct-2507": (
        "mlx-community/Qwen3-0.6B-4bit",        # vocab 151936 == target's
        "same tokenizer family; smallest usable draft"),
    "mlx-community/Qwen3-14B":  ("mlx-community/Qwen3-0.6B-4bit", "same family"),
    "mlx-community/Qwen3-8B":   ("mlx-community/Qwen3-0.6B-4bit", "same family"),
    "mlx-community/Hermes-3-Llama-3.1-8B": (
        "mlx-community/Llama-3.2-1B-Instruct-4bit",  # vocab 128256 == target's
        "Llama-3.x tokenizer; ChatML template mismatch noted in open Qs"),
}
```

4-bit drafts pair with 4-bit targets on purpose (FUTURE.md Reddit perf:
a bf16 draft bottlenecks a quantized target). Targets with no entry
(e.g. Qwen3-4B — drafting for a 3GB model is pointless) report
`pair: null` and the UI says "no compatible draft".

### 3. `~/.hermes/metrics/specdec-bench.json` (bench harness output)

```json
{"ts": 1751700000.0, "target": "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit",
 "draft": "mlx-community/Qwen3-0.6B-4bit",
 "chat_path": {
   "off":  {"tok_per_sec_p50": 62.0, "ttft_ms_p50": 980, "n": 8},
   "on_3": {"tok_per_sec_p50": 66.0, "ttft_ms_p50": 1290, "n": 8}},
 "cli":  {"off": 71.2, "on_2": 68.9, "on_3": 74.0, "on_4": 70.1},
 "acceptance": {"n_tokens": 1200, "rate": 0.58},
 "decision": "off", "reason": "1.06x < 1.5x gate; ttft +31%",
 "mlx_lm": "0.31.3"}
```

Read by `GET /api/specdec`; written by the bench procedure (orchestrator-run
curl/python, not by the server itself). Lives under `~/.hermes/metrics/`
next to P1.5's JSONL (same retention exemption: it's one small file, no GC).

### 4. No changes to settings.json, models.json, layout.json, config.yaml.

Drafts are deliberately **not** added to the models.json roster — they must
never appear in the model switcher as selectable main models.

---

## Backend

### A. `mlx-server.sh` changes (exact)

The plist runs `/bin/bash mlx-server.sh` (install-services.sh:49-50) —
that's **bash 3.2**, so the empty-array-under-`set -u` expansion must use
the `${arr[@]+...}` idiom or the service crash-loops on the OFF path.

Insert after the `MODEL=` block (line ~18):

```bash
# --- Speculative decoding (P3.3) ---------------------------------------------
# Optional draft model for mlx_lm speculative decoding. Controlled by the
# dashboard via ~/.hermes/dashboard/draft-model ("<repo_id> [num_tokens]").
# Guards: file present AND draft already downloaded (never hit the network at
# service boot — offline boot must stay clean). Pairing/vocab checks live in
# aux_specdec.py, which is the only writer of this file.
DRAFT_FILE="$HOME/.hermes/dashboard/draft-model"
DRAFT_ARGS=()
if [ -s "$DRAFT_FILE" ]; then
  read -r DRAFT_MODEL DRAFT_N _ < "$DRAFT_FILE" || true
  if [ -n "${DRAFT_MODEL:-}" ]; then
    # HF cache layout: models--org--name. Attach only if weights exist locally.
    DRAFT_DIR="$HOME/.cache/huggingface/hub/models--${DRAFT_MODEL//\//--}"
    if [ -d "$DRAFT_DIR" ]; then
      case "${DRAFT_N:-3}" in (''|*[!0-9]*) DRAFT_N=3;; esac
      DRAFT_ARGS=(--draft-model "$DRAFT_MODEL" --num-draft-tokens "${DRAFT_N:-3}")
      echo "Speculative decoding ON: draft=$DRAFT_MODEL n=${DRAFT_N}"
    else
      echo "draft-model set but not downloaded ($DRAFT_MODEL) — starting without"
    fi
  fi
fi
```

And change the exec (line ~34) to append the args (bash-3.2-safe):

```bash
exec python3 -m mlx_lm server \
  --model "$MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --max-tokens 4096 \
  --prompt-cache-size 6 \
  --prompt-cache-bytes 6000000000 \
  --trust-remote-code \
  ${DRAFT_ARGS[@]+"${DRAFT_ARGS[@]}"}
```

No other flag changes. `--num-draft-tokens` default of 3 (upstream) is also
our file default.

### B. New aux module `dashboard/aux_specdec.py` (~180 lines)

Follows the aux contract exactly (exec'd into server.py globals by the
`_AUX_FILES` loop at server.py:2069-2083, sorted order — `aux_specdec.py`
loads **after** `aux_metrics.py`, so wrapping `switch_model` here wraps the
metrics-wrapped version and the load-watch keeps firing). Rules honored:

- **No `from datetime import datetime`** (CLAUDE.md aux gotcha) — this
  module needs only `os, time, threading, subprocess, sys, json, re`.
- Double-exec guard: `if not globals().get("_SPECDEC_LOADED"):` … ends with
  `_SPECDEC_LOADED = True`.
- Uses shared globals already proven available to aux modules:
  `DATA`, `HOME`, `read_json`, `write_json`, `register_get`,
  `register_post`, `active_model`, `switch_model`, `agent_paused`,
  `CHAT_JOBS`, `_cached`, and (guarded) `metrics_count` from aux_metrics.

Core functions:

```python
DRAFT_FILE = os.path.join(DATA, "draft-model")          # DATA = ~/.hermes/dashboard
BENCH_FILE = os.path.join(HOME, ".hermes", "metrics", "specdec-bench.json")

def _specdec_pair(target_id):        # -> (draft_id, why) | (None, reason)
def _specdec_state():                # parse DRAFT_FILE -> (draft, n) | (None, 3)
def _draft_downloaded(repo_id):     # HF cache dir test (same rule as mlx-server.sh)
def specdec_payload(ctx=None):      # GET /api/specdec
def specdec_post(ctx):              # POST /api/specdec {"action": ...}
def specdec_download(ctx):          # POST /api/specdec/download
```

`specdec_post` actions:

- `{"action":"enable"}` — refuse (`409`-style `{"ok":false}`) if:
  active model has no pair, draft not downloaded, or any `CHAT_JOBS` entry
  has `done == False` (don't yank the server out from under a live turn).
  Else write `DRAFT_FILE` (tmp + `os.replace`, `<pair> <n>`), then call
  **`switch_model(active_model())`** — verified behavior at
  server.py:1965-2003: same-id switch still performs the full
  bootout → `sleep 3` → bootstrap cycle, which is the *only* reliable way
  to restart the KeepAlive service (kickstart demonstrably doesn't reload —
  existing comment at server.py:1981). mlx-server.sh re-reads both
  `active-model` and `draft-model` on boot. Returns
  `{"ok": true, "enabled": true, "loading": true}`.
- `{"action":"disable"}` — remove `DRAFT_FILE`, same restart path.
- `{"action":"set_tokens","n":2|3|4}` — rewrite file (clamp 1–8), restart
  only if currently enabled.
- Fire `metrics_count("specdec_on"/"specdec_off")` (guarded
  `globals().get`) so the P1.5 JSONL timeline carries state-flip markers —
  this is how the bench parser knows which turns ran in which mode without
  touching aux_metrics.py.

`specdec_download`: background `threading.Thread` running
`huggingface_hub.snapshot_download` via the same
`subprocess.run([sys.executable, "-c", ...])` shape as server.py's
`download_model` (~line 2005+), with a module-level status dict
(`_specdec_dl`) reported by the GET. Not routed through `download_model`
itself because that would seed the draft into the switcher roster.

**Model-switch hygiene** (wrapper, same pattern aux_metrics uses):

```python
_specdec_orig_switch_model = switch_model
def switch_model(mid):
    try:
        cur, n = _specdec_state()
        if cur:
            pair, _ = _specdec_pair(mid)
            if pair != cur:              # new target unpaired with old draft
                if pair and _draft_downloaded(pair):
                    _specdec_write(pair, n)     # follow to the new pair
                else:
                    _specdec_clear()            # fail safe: OFF
    except Exception:
        pass
    return _specdec_orig_switch_model(mid)
```

Draft file is corrected **before** the wrapped call restarts the service,
so a target switch can never boot with a vocab-mismatched draft (mlx_lm
would only warn — finding 3 — so this guard is ours to enforce).

Routes registered at module bottom (verified aux registry contract,
server.py:2039-2048):

```python
register_get("/api/specdec",           specdec_payload)
register_post("/api/specdec",          specdec_post)
register_post("/api/specdec/download", specdec_download)
```

### C. No server.py edits. No hermes_rpc.py edits. No permissions.py edits.

The entire backend rides the aux registry. Shared-file footprint of this
whole workstream: `mlx-server.sh` (above) + **one** `<script>` line in
index.html (below).

---

## Frontend

**New file `dashboard/aux_specdec.js`** — served automatically by the
existing `/aux_*.js` static route (server.py:2127); needs one index.html
line after the current aux block (line 2059):

```html
<script src="/aux_specdec.js"></script>
```

UX (mirrors the Vitals pattern — aux_metrics.js injects `#vitals-card` into
`#view-console`; this card appends after it):

1. Console view gains a compact **"Speculative Decoding"** card
   (`id="specdec-card"`, `class="card glass"`): status line
   ("OFF — draft available: Qwen3-0.6B-4bit" / "ON — Qwen3-0.6B-4bit, 3
   draft tokens" / "no compatible draft for <model>"), a two-tone SVG bolt
   icon (WICONS recipe: accent fill + currentColor stroke — no emoji).
2. Controls: **Download draft** (visible when pair exists but not
   downloaded; shows "downloading…" from the GET's status),
   **Enable/Disable** button gated by `confirm()` — works in the app, the
   Swift shell implements the JS dialog handlers (CLAUDE.md) — warning text:
   "Restarts the model server (~30–60s). Chat is unavailable while it
   reloads." Token stepper 2/3/4 (visible when enabled).
3. Bench strip: if `last_bench` present, render off/on tok/s, TTFT delta,
   acceptance rate, and the recorded decision, timestamps in 12-hour local.
   Empty state: "No A/B recorded yet — run the bench procedure in
   docs/plans/p3-3-speculative-decoding.md."
4. States: loading skeleton (`.skel`), error line "specdec unavailable" if
   the GET fails or `ok:false`, card removes itself on 404 (stale server).
   All styling via existing CSS custom properties → both themes free.
   Values refresh by piggybacking the console poll exactly like
   aux_metrics.js does (wrap `window.loadConsole`, throttle to 15s) — the
   wrap chain is load-order-safe because aux_specdec.js loads after
   aux_metrics.js.

Nothing on the Hub view; this is an operator control, not a widget — so no
`WIDGETS`/`EXPANDERS`/`RENDER`/`EXPAND_RENDER`/`WICONS` registrations.

---

## Integration points (verified names)

| Surface | Verified name / location |
|---|---|
| Server launch | `mlx-server.sh` → `exec python3 -m mlx_lm server` (line 34); run by launchd via `/bin/bash` (install-services.sh:49-50, **bash 3.2**) |
| Active-model channel | `ACTIVE_MODEL_FILE = ~/.hermes/dashboard/active-model` (server.py:1836); `active_model()` (server.py:1938) — `draft-model` is its sibling |
| Reliable restart | `switch_model(mid)` (server.py:1965): bootout → `sleep 3` → bootstrap of `com.hermes.mlx-server` (`MLX_LABEL`, `MLX_PLIST` at server.py:1836-1838); same-id call restarts cleanly |
| Aux registry | `_AUX_FILES` sorted exec loop (server.py:2069-2083); `register_get`/`register_post`/`RouteCtx` (server.py:2039-2060) |
| Aux JS serving | `/aux_*.js` static branch (server.py:2127); script tags in index.html:2051-2059 |
| Metrics (P1.5, built as **`aux_metrics.py`**, not the spec'd `metrics_extra.py`) | `metrics_record`/`metrics_count` (aux_metrics.py:128,140); turn fields `est_tok_per_sec`, `decode_ms`, `ttft_ms`, `model` (aux_metrics.py:268-277); `GET /api/metrics` (registered aux_metrics.py:724); JSONL at `~/.hermes/metrics/metrics-YYYY-MM-DD.jsonl` |
| Vitals card anchor | `#vitals-card` prepended into `#view-console` (aux_metrics.js:126-133) |
| Pause/resume for CLI bench | `agent_power("pause"/"resume")` (server.py:1848+); `agent_paused()` / `PAUSE_FILE` |
| mlx_lm flags | `--draft-model` (default None), `--num-draft-tokens` (default 3) — installed 0.31.3, server.py:1782-1792; per-request keys exist but are unused by us (finding 7) |
| KV cap interplay | `--prompt-cache-size 6 --prompt-cache-bytes 6000000000` already in mlx-server.sh:39-40; draft KV appended to cached lists (mlx_lm server.py:970-973) |
| CHAT_JOBS live-turn guard | `CHAT_JOBS` + `done` flag (shared globals, used identically by aux_metrics) |
| Models on disk today | HF cache has only Qwen3-30B-A3B-2507-4bit + Hermes-3-8B-4bit — the 0.6B draft (~0.4GB) needs one download |

---

## Edge cases

- **bash 3.2 + `set -u` + empty array**: plain `"${DRAFT_ARGS[@]}"` aborts
  the script when OFF → KeepAlive crash-loop of the model server. The
  `${DRAFT_ARGS[@]+"${DRAFT_ARGS[@]}"}` idiom is mandatory; the OFF-path
  launch test below exists specifically to catch this.
- **Draft named but not downloaded**: mlx-server.sh dir-test skips the flags
  (never a boot-time HF download; offline boot stays clean). GET reports
  `enabled_requested but not active` via `draft_downloaded:false`.
- **Corrupt draft file** (binary garbage, extra fields): `read -r a b _`
  tolerates extra tokens; non-numeric `n` coerced to 3 by the `case` guard;
  a garbage repo id fails the dir test → OFF. `specdec_payload` re-parses
  defensively and reports `"invalid"` rather than throwing.
- **Vocab mismatch**: upstream only warns (finding 3) → enforced by the
  pairing table; `enable` refuses when `_specdec_pair(active_model())`
  doesn't match, and the `switch_model` wrapper re-pairs or clears on every
  target change *before* the restart.
- **Enable during a live turn**: refused while any `CHAT_JOBS` entry is not
  `done` (the restart would kill the in-flight generation). Telegram/CLI
  turns don't create CHAT_JOBS (known P1.5 limitation) — accepted residual
  risk, same class as a manual model switch today, and the confirm() text
  says the server restarts.
- **Restart fails** (launchd error 5 timing): `switch_model` already retries
  bootstrap with `sleep 3`; on persistent failure its error propagates
  through our POST response, and KeepAlive is the backstop. Worst case
  equals today's model-switch worst case — no new failure mode.
- **Draft raises footprint**: +~0.4GB weights + small KV for the 0.6B —
  still inside DEVPLAN's MoE ≤20GB envelope; the memory watchdog and P1.5
  `ram` series are unchanged and will show it.
- **TTFT regression while ON**: expected (finding 8) and gated: the decision
  rule rejects >10% TTFT p50 regression even if tok/s improves.
- **Batching future**: `is_batchable=False` with a draft (finding 4). If the
  LATER `--prompt-concurrency` item ever lands, it must check
  `draft-model` is absent; noted in that item's DEVPLAN row when we get
  there.
- **mlx_lm upgrade churn** (DEVPLAN risk table): flags verified on 0.31.3
  only. Add to the existing upgrade smoke ritual: after any mlx-lm bump,
  re-grep `--draft-model` in the installed server.py and re-run the OFF-path
  launch test. `transformers<5` pin unaffected (no new Python deps).
- **Module fails to exec**: the aux loop prints `[aux_specdec.py] failed to
  load` and everything else keeps working; the JS card sees 404 and removes
  itself; mlx-server.sh behavior is independent (file-driven).
- **User hand-edits draft-model to an arbitrary repo**: dir test still
  gates on downloaded weights; a downloaded-but-mismatched draft would run
  with upstream's warning — the GET surfaces `pair_mismatch:true` and the
  card shows a warning line. We do not fight a root-equivalent local user.

---

## Security & safety (invariants)

- **Local-first absolute**: the only network egress in this workstream is
  the explicit, user-clicked draft download from Hugging Face (same channel
  as existing model downloads). Serving, benching, metrics — all loopback
  (`127.0.0.1:8080` / `:7788`) and filesystem.
- **No new agent capability**: `/api/specdec` is not a tool, not in
  `_HERMES_CORE_TOOLS`, not reachable through the approval loop;
  permissions.py tiers, recorder contract, and `approvals.mode: manual` are
  untouched. Spec decoding is **lossless by construction** (target verifies
  every draft token; rejected tokens are resampled from the target), so
  agent output quality/safety posture is unchanged — worth stating because
  it means the Phase 3 promotion gate does NOT need a re-run for the same
  target model with a draft attached (open Q3 tracks double-checking this).
- **No secrets**: nothing reads `~/.hermes/.env`, serve-token, or Telegram
  config. The draft file contains a public HF repo id and an integer.
- **Gmail send / Telegram invariants**: not touched, not adjacent.
- **No push**: all work stays in local commits per the batching rule.
- **Restart honesty**: every path that restarts the model server goes
  through the one proven sequence (`switch_model`'s bootout/bootstrap) —
  no new process-management code, no `kickstart -k` regressions.
- **`--yolo` forbidden**: nothing here invokes the hermes CLI at all.

---

## Test plan (no --yolo, no real sends, all local)

### Static

```bash
python3 -m py_compile ~/HermesAssistant/dashboard/aux_specdec.py
node --check ~/HermesAssistant/dashboard/aux_specdec.js
bash -n ~/HermesAssistant/mlx-server.sh
# exec-smoke (module self-sufficiency, mirrors the P1.5 harness):
python3 - <<'EOF'
import os, threading, json
g = dict(DATA="/tmp/sd-test", HOME=os.path.expanduser("~"),
         read_json=lambda p,d: d, write_json=lambda p,o: None,
         register_get=lambda p,f: None, register_post=lambda p,f: None,
         active_model=lambda: "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit",
         switch_model=lambda m: {"ok": True}, agent_paused=lambda: False,
         CHAT_JOBS={}, _cached=lambda k,t,f: f())
os.makedirs("/tmp/sd-test", exist_ok=True)
exec(open("~/HermesAssistant/dashboard/aux_specdec.py").read(), g)
p = g["specdec_payload"]()
assert p["ok"] and p["pair"] == "mlx-community/Qwen3-0.6B-4bit"
print("specdec module OK")
EOF
```

### Launch-path (the crash-loop guard — run BEFORE touching the live service)

```bash
# OFF path must be byte-safe under bash 3.2 + set -u:
rm -f ~/.hermes/dashboard/draft-model
bash -u -c 'DRAFT_ARGS=(); echo ok ${DRAFT_ARGS[@]+"${DRAFT_ARGS[@]}"}'   # → ok
# Dry-run both states by stubbing exec (temporary EXEC_ECHO=1 branch or:)
DRAFT_FILE=~/.hermes/dashboard/draft-model
echo "mlx-community/Qwen3-0.6B-4bit 3" > $DRAFT_FILE
bash -c 'source /dev/stdin <<< "$(sed "s/^exec /echo WOULD-RUN: /" ~/HermesAssistant/mlx-server.sh)"' \
  | grep -c "draft-model"        # 1 iff draft downloaded, else 0 + skip notice
rm $DRAFT_FILE
```

### Live API (after `launchctl kickstart -k gui/$(id -u)/com.hermes.dashboard`)

```bash
curl -s localhost:7788/api/specdec | python3 -m json.tool     # ok:true, pair, enabled:false
curl -s -X POST localhost:7788/api/specdec -d '{"action":"enable"}'
#   → refused until draft downloaded ({"ok":false,"error":"draft not downloaded"})
curl -s -X POST localhost:7788/api/specdec/download -d '{}'   # bg download ~0.4GB
# poll GET until draft_downloaded:true, then:
curl -s -X POST localhost:7788/api/specdec -d '{"action":"enable"}'   # ok, loading:true
sleep 45 && grep "Speculative decoding ON" ~/.hermes/logs/mlx-server.log
curl -s localhost:8080/v1/models        # server back up with draft attached
# one real turn end-to-end still streams:
curl -s -X POST localhost:7788/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"say ready","session":"specdectest"}'
# disable path returns to a byte-identical baseline launch:
curl -s -X POST localhost:7788/api/specdec -d '{"action":"disable"}'
```

### Measurement (the actual deliverable — writes specdec-bench.json)

A. **Chat-path A/B via P1.5** (the DEVPLAN-sanctioned instrument). For each
state (off, on n=3): record `T0=$(date +%s)`, run 8 canned prompts through
`POST /api/chat` (fixed wording, ~150-word answers, dedicated session
`specdecbench`, wait for `done` between turns), then parse today's
`~/.hermes/metrics/metrics-$(date +%F).jsonl` for `kind:"turn"` records with
`ts > T0`, taking p50 of `est_tok_per_sec` and `ttft_ms` (`ttft_clean` only).
The `specdec_on`/`specdec_off` count markers bracket the windows in the same
file. Known caveat carried from P1.5: `est_tok_per_sec` is chars/4 — fine
for A/B ratios on identical prompts (both sides share the bias).

B. **CLI ground truth** (real token counts). The 30B can't be resident
twice (18GB × 2 on 64GB with the server up), so: pause first.

```bash
curl -s -X POST localhost:7788/api/agent/pause
M=mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit
D=mlx-community/Qwen3-0.6B-4bit
P="Explain how tides work in about 250 words."
python3 -m mlx_lm generate --model $M -p "$P" -m 300 2>&1 | grep tokens-per-sec
for N in 2 3 4; do
  python3 -m mlx_lm generate --model $M --draft-model $D \
    --num-draft-tokens $N -p "$P" -m 300 2>&1 | grep tokens-per-sec
done
curl -s -X POST localhost:7788/api/agent/resume
```

C. **Offline acceptance rate** (the honest stand-in for DEVPLAN's live-
acceptance idea, finding 6): small script (also run while paused) using
`mlx_lm.utils.load` × 2 + `mlx_lm.stream_generate(..., draft_model=...)`,
counting `resp.from_draft` over ≥1000 generated tokens across 5 prompts →
`acceptance.rate` in the bench file. Compare against the 0.65 break-even
(FUTURE.md Substack perf / LM Studio guidance).

D. **Decision**: apply the rule (≥1.5x tok/s p50 AND ≤10% TTFT p50
regression on the chat path; CLI + acceptance as corroboration), write
`decision`/`reason` into specdec-bench.json, mirror one paragraph into
`docs/FINDINGS.md`, leave the toggle in the decided state, ⌘R the app and
verify the card shows the recorded bench.

### Regression

`curl -s localhost:7788/api/metrics` still `ok:true`; Vitals card intact;
one full chat turn + one approval round-trip unaffected; all three services
survive `install-services.sh` re-run; `grep -c "failed to load"
~/.hermes/logs/dashboard.log` gained no new lines.

---

## Effort & sequencing

Total ≈ 1 dev-day including the measurement soak.

1. `mlx-server.sh` guard + launch-path tests (0.5h). **Commit
   `mlx: optional draft-model for speculative decoding (P3.3)`** — inert
   until the file exists, safe to land first.
2. `dashboard/aux_specdec.py` + exec-smoke (2–3h). No shared-file edits.
3. `dashboard/aux_specdec.js` + the one index.html script line + ⌘R QA (1h).
4. Draft download (~0.4GB) + measurement procedure A–D (1–2h wall-clock,
   mostly waiting on restarts) + FINDINGS.md entry + bench JSON committed
   fact (the JSON itself lives in `~/.hermes/metrics/`, quoted in FINDINGS).

Coordination:
- **Shared files touched**: `mlx-server.sh`, `index.html` (1 line). Do not
  run concurrently with another agent editing either; `aux_specdec.*` are
  conflict-free by construction (orchestrator integrates shared files, per
  CLAUDE.md workflow).
- **Depends on**: P1.5 metrics (shipped — aux_metrics.py). Nothing else.
- **Blocks / informs**: the restraint-router workstream (a 0.6B/1.7B Qwen3
  is both the router candidate AND the draft candidate — one download can
  serve both experiments); the LATER batching item (mutually exclusive with
  a draft, finding 4).
- **Timing within the deadline window**: steps 1–3 are low-risk and can land
  now; step 4 restarts the model server several times — schedule it away
  from the 8am World Brief window so a bench restart never eats the brief.

---

## Open questions

1. **Draft/target distribution mismatch**: Qwen3-0.6B is the hybrid-thinking
   base; the 2507 target is non-thinking post-trained. Acceptance may sit
   below 0.65 for style tokens alone. If measured acceptance is poor, try
   `mlx-community/Qwen3-1.7B-4bit` (same vocab) once before concluding —
   but its worse draft:target ratio makes a win even less likely; cap the
   experiment at those two drafts.
2. **8B profile follow-up**: when the DEVPLAN #13 light-tier work lands,
   re-run this exact bench with Hermes-3-8B + Llama-3.2-1B-Instruct-4bit
   (vocab 128256 both — verified from local config.json). Watch the ChatML
   template mismatch (Hermes-3 uses ChatML; Llama-3.2-Instruct doesn't) —
   `mlx-community/Hermes-3-Llama-3.2-3B-4bit` is the same-template fallback
   despite the mediocre 3B:8B ratio. This is where the ≥1.5x branch of
   DEVPLAN Phase 3 #4 most plausibly cashes out.
3. **Promotion-gate interaction**: spec decoding is lossless in theory
   (target verifies every token), so the Phase 3 model-promotion gate
   shouldn't need a re-run when a draft attaches — confirm once with a
   tool-calling smoke turn while ON before writing that rule into the gate
   spec.
4. **DFlash watch item**: dflash-mlx claims ~4.1x with trained draft heads
   (FUTURE.md), and z-lab publishes heads for some A3B models — none for
   our exact 2507 target today, and it isn't `mlx_lm server`-compatible.
   Re-check at the Phase 4 boundary; adopt only via the MCP/supply-chain
   vetting rules if it ever fits.
5. **`prefill_step_size`**: mlx_lm exposes `--prefill-step-size` (default
   512). Untested lever for our long agent prompts on M5 Max; if TTFT work
   (Alternatives below) gets picked up, bench 512 vs 1024 vs 2048 in the
   same session — zero risk, one flag.

---

## Alternatives (the levers that likely matter more for this MoE)

Recorded here because the task's honest answer is "supported, probably not
worth it for the 30B" — these are where the same effort buys real latency:

1. **Prefix-stable system prompt** (DEVPLAN Section 4, already prioritized):
   the server's prompt cache (`--prompt-cache-size 6`, 6GB byte cap —
   already shipped) only hits on byte-identical prefixes. Auditing
   `access_preamble()`/briefing injection for timestamps and volatile lists
   and moving them to the end of the prompt is the single biggest TTFT
   lever we know of (reported 8k-token prefix: ~31s cold → ~3.4s warm,
   FUTURE.md X perf) — it attacks the metric spec decoding *worsens*.
2. **Batch settings** (`--prompt-concurrency`/`--decode-concurrency`):
   stays LATER per DEVPLAN (mlx-lm #965 KV-isolation history) and is
   incompatible with a draft model (finding 4) — a deliberate either/or to
   be decided by which profile wins: draft-assisted single-stream (8B path)
   or batched multi-surface (Telegram + dashboard concurrency).
3. **KV-cache quantization** (kv4): ~3.2x context headroom, adopt only from
   a tagged mlx-lm release per DEVPLAN Section 4 — orthogonal to this spec.

Sources: installed `mlx_lm 0.31.3` source (primary);
[thc1006/qwen3.6-speculative-decoding-rtx3090](https://github.com/thc1006/qwen3.6-speculative-decoding-rtx3090);
[HackMD A3B spec-decode write-up](https://hackmd.io/ODXuOQNzSiyUITz7g9mtBw);
[z-lab DFlash example](https://huggingface.co/z-lab/Qwen3.6-35B-A3B-DFlash);
`docs/FUTURE.md` (Reddit/Substack/X perf sections); `docs/DEVPLAN.md`
Sections 3–6.
