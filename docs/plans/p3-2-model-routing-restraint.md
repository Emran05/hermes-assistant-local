# Restraint Router + Per-Model Parsers + Promotion Gate — design spec (P3.2/P3.3)

Workstreams DEVPLAN Phase 3 #2 ("Two-tier routing") and #3 ("Per-model
tool-call parsers + promotion gate"), REFRAMED per current reality:
**Qwen3-30B-A3B is already the active default** (`DEFAULT_MODEL` in
server.py:1837, `mlx-server.sh` DEFAULT_MODEL, `~/.hermes/dashboard/active-model`),
so the "MoE performance profile" half of the original workstream is done.
What remains is **restraint**: stop burning the 30B (TTFT p50 965ms baseline,
P1.5) on turns a 3GB model answers in ~300ms — greetings, short factual
Q&A, clipboard transforms — while every agentic turn stays on the 30B via
the untouched serve path.

Grounding notes (all verified in-tree 2026-07-05):

- Chat flow today: `POST /api/chat` (server.py:2394) → `_new_job(session)`
  (redefined by aux_metrics.py:286 as a `MeteredJob`) → thread on
  `_chat_worker(job, session, prompt)` (server.py:897) →
  `hermes_rpc.run_turn(job, chat, prompt, save_meta)`; on any exception the
  worker falls back to one-shot `run_agent()` (`hermes -z`). Both `_new_job`
  and `_chat_worker` are resolved **by name from module globals at call
  time**, so an aux module exec'd later can redefine them without editing
  server.py (this is exactly how aux_metrics installed MeteredJob — the
  exec-include order rule from CLAUDE.md).
- Aux loader: `_AUX_FILES = ["expanders_extra.py"] + sorted(aux_*.py)`
  (server.py:2071), exec'd into server.py globals **before** `class Handler`.
  Alphabetical order matters: a new `aux_router.py` sorts after
  `aux_metrics.py` and `aux_clip.py`, so it may wrap names they defined.
- Route registry: `register_get(path, fn)` / `register_post(path, fn)`
  (server.py:2043/2047), handlers get a `RouteCtx` (`ctx.q1()`, `ctx.body`)
  and return dict → 200 or `(dict, status)`.
- Model plumbing: `_SEED_MODELS` (server.py:1878) already contains
  `mlx-community/Qwen3-4B-Instruct-2507-4bit` (ram 3, "ultra-light") and
  `mlx-community/Qwen3-8B-4bit` (ram 5); `_model_registry()` /
  `models.json`, `_model_downloaded(mid)` (scans HF cache for
  .safetensors), `download_model(mid)` (bg thread,
  `huggingface_hub.snapshot_download`), `switch_model(mid)`
  (bootout→sleep 3→bootstrap `com.hermes.mlx-server`), `active_model()`
  reads `~/.hermes/dashboard/active-model`.
- Clipboard actions (aux_clip.py) already prove the direct-completions
  pattern: they POST to `/v1/chat/completions` derived from `MODEL_URL`
  with **no tools field**, model = `active_model()`. That means every clip
  transform currently wakes the 30B — prime routing target.
- Metrics (aux_metrics.py): `metrics_record(kind, **fields)` /
  `metrics_count(name, n)` never raise; `_met_finish_turn` emits
  kind:"turn" with `ttft_ms/setup_ms/serve_ttft_ms/turn_ms/path/model/...`;
  `path` is inferred as "serve"/"oneshot" (aux_metrics.py:248-257).
  `MET_TARGETS = {"ttft_p50_ms": 1500, "ttft_p95_ms": 3000, ...}`.
- Permissions engine (`permissions.py`): `decide(payload)` →
  `{tier, class, ...}`, TIERS = ("auto","ask","never"), consumed inside
  `hermes_rpc.run_turn` (hermes_rpc.py:284). The router path never
  executes tools, so it sits entirely BELOW the permission surface — it
  must never gain a tool-execution capability without going through serve.
- Flight recorder (aux_recorder.py): `recorder_record_local(tool, target,
  kind, reversible, ...)` (line 533) is the local-event hook;
  `recorder_ws_event(sid, etype, payload)` consumes serve events.
- mlx_lm 0.31.3 (installed, verified): the server itself owns per-model
  tool-call parsing. `tokenizer_utils.load()` reads
  `tokenizer_config.get("tool_parser_type", _infer_tool_parser(chat_template))`
  and imports `mlx_lm.tool_parsers.<type>`; installed parsers:
  `json_tools, qwen3_coder, mistral, kimi_k2, glm47, gemma4,
  function_gemma, pythonic, longcat, minimax_m2`. Qwen3's template
  (`<tool_call>` + `tool_call.name`) infers **json_tools**. If NO parser
  infers, `tokenizer.has_tool_calling` is False and the server **rejects
  the request**: "Received tools but model does not support tool calling"
  (mlx_lm/server.py:537-539). So "per-model parsers" for us = a **gate
  that verifies parser inference + live tool-call behavior per model**,
  not parser code inside hermes-agent (hermes-agent just consumes OpenAI
  `tool_calls`, with `_repair_tool_call_arguments` in
  agent/message_sanitization.py:185 as its own malformed-args repair).
- Memory guard: `_mlx_footprint_gb()` (server.py:1057) does
  `pgrep -f mlx_lm` and footprints **pids[0] only**; `memory_guard_loop`
  restarts `com.hermes.mlx-server` above `MLX_RESTART_GB` (32). A second
  mlx_lm process makes pids[0] ambiguous — must be fixed (see Edge cases).
- P1.4 drill precedent: docs/plans/p1-4-approval-loop-test.md — canary-dir
  arming, observing `state:"approval"` + payload via `/api/chat/poll`,
  `approval-log.jsonl`. The promotion gate's stage-3 canary reuses this
  shape with **auto-DENY only**.

---

## 1. Goal & acceptance criteria

**Goal.** Trivial dashboard turns and clipboard transforms are answered by a
small always-resident model (default Qwen3-4B-Instruct-2507-4bit, download
pre-approved) on a second loopback mlx_lm server, with automatic escalation
to the 30B serve path; and no model can become the agent default without
passing an automated tool-call drill (promotion gate).

Acceptance criteria (each provable, no user interaction required except AC7):

- **AC1 — routed turns are fast:** a routed turn ("hey", "what's 340*12?",
  "define ephemeral") completes end-to-end (POST /api/chat → done job) with
  TTFT p50 ≤ 400ms and full reply ≤ 1.5s, measured by the existing P1.5
  turn records (new `path:"local"` value). DEVPLAN's "<3s simple commands"
  bar is beaten with margin.
- **AC2 — agentic turns are untouched:** any turn the classifier does not
  positively claim goes through `hermes_rpc.run_turn` exactly as today;
  diff-level guarantee: server.py, hermes_rpc.py, permissions.py are NOT
  edited (aux-module redefinition only).
- **AC3 — escalation works and is visible:** a sentinel/heuristic escalation
  re-dispatches to serve within ≤ 1s added latency, the chat UI shows
  "escalated to Qwen3-30B", and a kind:"route" metric records
  `decision:"escalate"` with a reason.
- **AC4 — restraint spot-check passes:** `dashboard/router_canary.py` runs
  the 24-prompt canned set (12 must-route-small, 12 must-route-main incl.
  all tool-implying phrasings) with ≥ 22/24 correct decisions, offline
  (heuristics) plus ≥ 20/24 with the live sidecar.
- **AC5 — sidecar death is invisible:** with the sidecar stopped, every
  turn routes to serve; no turn ever errors or blocks on the router
  (health probe timeout 1s, cached).
- **AC6 — promotion gate enforced:** `switch_model` refuses (HTTP 200,
  `{ok:false, error:"gate not passed", gate:{...}}`) to activate a model
  whose `gate.status != "pass"` in models.json, unless `force:true`; the
  model menu shows pass/fail/untested per model and a "Run gate" action.
  Adding a model via `/api/models/add` marks it `untested` and (if
  downloaded) auto-queues a gate run — DEVPLAN #3 "done means" verbatim.
- **AC7 — gate catches a real dud:** the gate run against the full seeded
  roster produces a report per model; any model whose chat template fails
  parser inference (`has_tool_calling` False) is FAILED at stage 1 with
  the exact mlx_lm rejection quoted. (Hermes-3-Llama-3.1-8B is the live
  test subject — its Llama-3.1 template's parser status is currently
  unverified; the gate answers it empirically.)
- **AC8 — metrics tell the story:** `/api/metrics` gains a `router` block
  (routed/escalated/fallback counts today + p50 routed TTFT + est.
  30B-seconds saved) and the Mind/metrics UI renders it.

---

## 2. Data model

New files (all under `~/.hermes/dashboard/`, same conventions as
models.json / settings.json — `read_json`/`write_json`, `_state_lock` not
required for router-owned files):

**`router.json`** — router config + rolling counters:
```json
{
  "enabled": false,
  "model": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
  "port": 8081,
  "mode": "heuristic+sentinel",      // "off" | "heuristic" | "heuristic+sentinel"
  "holdback_tokens": 16,
  "max_context_msgs": 6,
  "clip_enabled": true,               // route clipboard actions to sidecar
  "counters": {"small": 0, "escalated": 0, "fallback": 0}
}
```

**`router-model`** — plain repo-id file read by `mlx-router-server.sh`
(mirror of the `active-model` file pattern, mlx-server.sh:17).

**models.json entries gain a `gate` object** (absent = untested):
```json
{"id": "...", "label": "...", "ram": 5, "note": "...",
 "gate": {"status": "pass",            // "pass" | "fail" | "untested" | "running"
          "score": 9, "of": 10,
          "parser": "json_tools",       // inferred tool_parser_type or null
          "ts": 1751700000,
          "report": "gate/mlx-community--Qwen3-8B-4bit.json"}}
```

**`gate/<slug>.json`** — full per-model gate report: stage results, each
drill prompt, raw tool_calls returned, pass/fail per check, timings.

**Metrics records** (JSONL via existing `metrics_record`, new kinds — the
ring fallback `MET_RINGS.get(kind, MET_RINGS["count"])` already tolerates
unknown kinds):
- `kind:"route"` — `{job, decision: "small"|"main"|"escalate"|"fallback",
  reason: "greeting-re"|"len"|"toolword"|"sentinel"|"sidecar-down"|...,
  classify_ms, model}`
- `kind:"gate"` — `{model, stage, ok, score, of, ms}`
- counters via `metrics_count`: `route_small`, `route_escalated`,
  `route_fallback`, `gate_runs`.

**Chat JSON** (`chats/<session>.json`) gains an optional
`routed_pending: [[q, a], ...]` list — routed exchanges not yet known to
the serve session (see Edge case E5).

---

## 3. Backend

### 3.1 Sidecar model server (new service)

- **`mlx-router-server.sh`** (repo root, sibling of mlx-server.sh): same
  skeleton; reads `~/.hermes/dashboard/router-model` (fallback
  Qwen3-4B-2507), `--port 8081 --max-tokens 1024 --prompt-cache-size 2
  --prompt-cache-bytes 1000000000 --trust-remote-code`. ~2.5-3GB resident;
  30B (18GB) + 4B fits the 64GB machine trivially.
- **`com.hermes.mlx-router` launchd agent** added to install-services.sh
  (RunAtLoad + KeepAlive, log `~/.hermes/logs/mlx-router.log`), installed
  **disabled-by-default**: install-services.sh only bootstraps it when
  `router.json` has `enabled:true` (checked with a python3 -c one-liner,
  same stdlib-only ethos). Enabling from the UI bootstraps it; disabling
  boots it out (reuse the bootout→sleep 3→bootstrap dance and its
  "Bootstrap failed: 5" gotcha from `agent_power`, server.py:1847).
- Sidecar is **loopback-only, tools are never sent to it** — requests are
  plain `/v1/chat/completions` with a system prompt + ≤6 context messages.

### 3.2 `aux_router.py` (new aux module — the router itself)

Follows every CLAUDE.md aux rule: `import datetime as _rt_datetime` (never
bare — the aux-module gotcha), only new `_rt_*`/`router_*` names, wraps by
capture-then-redefine.

**Load-time:**
```python
_rt_prev_chat_worker = _chat_worker      # capture server.py original
def _chat_worker(job, session, prompt):  # redefinition wins at Thread(target=...) resolution
    if not _rt_should_route(job, session, prompt):
        return _rt_prev_chat_worker(job, session, prompt)
    if not _rt_run_small(job, session, prompt):      # False => escalate
        return _rt_prev_chat_worker(job, session, prompt)
```
(`/api/chat` resolves `_chat_worker` from globals when building the Thread,
after all aux files are exec'd — same mechanism aux_metrics uses for
`_new_job`. `aux_router.py` sorts after `aux_metrics.py`, so jobs are
already MeteredJobs.)

**`_rt_should_route(job, session, prompt)` — deterministic pre-pass, <1ms:**
returns False (→ serve) unless ALL hold:
1. `router.json` enabled, mode != off, and sidecar healthy
   (`_cached("rt_health", 15, probe)` — GET `:8081/v1/models`, 1s timeout).
2. The **user message** (strip the `access_preamble()` prefix — split on
   the final `\n\n` after the last `[context]` line; attachments add a
   `[context] The user attached` line → never route) is ≤ 280 chars.
3. No tool-implying lexicon hit (word-boundary regex, curated list:
   file/folder/open/run/install/search/browse/download/send/email/message/
   telegram/remind/schedule/calendar/task/screenshot/click/terminal/undo/
   delete/create/write/save/look up/latest/current/today's news/price/
   weather/my …). The list ships in aux_router.py as `_RT_TOOLWORDS` and
   the canary (AC4) pins its behavior.
4. Positive-claim check: matches one of the trivial shapes —
   greeting/thanks/acknowledgement REs; pure-arithmetic/unit-convert RE;
   "what is/define/explain <short noun phrase>" with no deictic reference
   to prior tool output; explicit transform of pasted text ("rewrite:",
   "translate:", "summarize this: <text>").
5. Session isn't mid-agentic-thread: chat JSON has no `serve_sid`, OR the
   last bot message did not follow a tool-using turn (cheap proxy: no
   `routed_pending` needed and previous job wasn't `state:"approval"`).
   When in doubt → serve. Restraint means **route small only on positive
   evidence**; the 30B is the safe default.

**`_rt_run_small(job, session, prompt)` — the small turn:**
- System prompt (server-owned, like aux_clip's catalog): "You are Hermes's
  quick-reply half. Answer directly, ≤120 words, plain text. You have NO
  tools, NO file/web/calendar access, and must not pretend otherwise. If
  the request needs tools, actions, files, current data, or anything about
  the user's world, output exactly `<<ESCALATE>>` as your entire reply."
- Context: last `max_context_msgs` messages from chat JSON + local time
  line only (NOT folder grants/tasks/calendar from `access_preamble` — the
  small model can't act on them and they'd leak agent-context into a
  no-tool path).
- POST `:8081/v1/chat/completions`, `stream:true`, `temperature 0.3`,
  `max_tokens 300`, urllib with 20s total timeout (loopback, plain http —
  aux_clip precedent, no `_ssl_context`).
- **Holdback buffer:** accumulate the first `holdback_tokens` (16) deltas
  (~100-200ms) before first write to `job["text"]`. If `<<ESCALATE>>`
  appears in the buffer → abort stream, `metrics_record("route",
  decision="escalate", reason="sentinel")`, return False (job["text"] was
  never touched, so MeteredJob's `first_token_ts` isn't polluted — the
  escalated serve turn's TTFT stays honest). After the buffer flushes,
  sentinel occurrences are stripped, never honored (E4).
- On success: `job["status"] = "answered locally · " + label`,
  `job["_route_path"] = "local"`, finish the job exactly like
  `_chat_worker` does (`job.update(reply=..., ok=True, state="done",
  done=True)` then `_finish_chat_job`), append the Q/A to
  `routed_pending`, bump counters, `metrics_record("route",
  decision="small", ...)`.
- On ANY exception/timeout/HTTP≥400 → `decision:"fallback"`, return False.
  The router can only ever make a turn take the path it takes today.

**`_rt_run_turn_context` (serve-history repair):** wrap
`hermes_rpc.run_turn` call-site indirectly — since `_chat_worker` is ours
now, before delegating to `_rt_prev_chat_worker` drain `routed_pending`
into the prompt: `"[context] Quick exchanges I answered locally since your
last turn: Q: … A: … \n\n" + prompt`, then clear the list in chat JSON.

**Clipboard integration:** `aux_clip.py` sorts BEFORE aux_router, so
aux_router captures and redefines aux_clip's POST handler is not possible
via name (it's registered in `POST_ROUTES`). Instead: aux_router replaces
the routed entry directly — `POST_ROUTES["/api/clip/run"]` (verify exact
path at build time from aux_clip.py's register_post call) with a wrapper
that rewrites the target URL to `:8081` when `clip_enabled` and healthy,
else calls the captured original. Clip stays no-tools by construction
(aux_clip.py:13), so this is a pure latency win.

**One small sanctioned edit to aux_metrics.py** (we own it, P1.5): in
`_met_finish_turn`, before the path inference:
`path = job.get("_route_path") or <existing inference>`. Three lines;
keeps every routed turn inside the same turn-record stream, targets, and
percentile math.

### 3.3 Endpoints (all via register_get/register_post — zero server.py edits)

- `GET /api/router` → config + health (`sidecar: "up"|"down"|"loading"`),
  counters, today's route stats from the metrics ring.
- `POST /api/router` → `{op:"enable"|"disable"|"set", ...fields}`;
  enable bootstraps the launchd agent (download-checks the model first via
  `_model_downloaded`; if absent returns
  `{ok:false, error:"model not downloaded", downloadable:true}` and the UI
  offers the existing `/api/models/download` flow — Qwen3-4B download is
  pre-approved per NEEDS-YOU).
- `POST /api/router/test` → `{prompt}` dry-run: returns the decision +
  reason WITHOUT executing (powers the canary and a debug row in the UI).

### 3.4 `aux_promotion.py` + `dashboard/model_gate.py` (promotion gate)

`model_gate.py` is a standalone stdlib+mlx_lm script (runnable as
`python3 dashboard/model_gate.py run <model-id>`) so the drill works
without the dashboard, P1.4-canary style; `aux_promotion.py` wraps it into
routes and enforcement.

**Stage 1 — static parser probe (no model load, <1s):**
locate the HF cache snapshot (`_hf_cache_dir` pattern, server.py:1902),
read `tokenizer_config.json` chat template, replicate mlx_lm's exact
inference (`from mlx_lm.tokenizer_utils import _infer_tool_parser` — same
installed package the server uses, so drift-proof), honor an explicit
`tool_parser_type` key. Result: parser name or **FAIL** ("mlx_lm will
reject tools: 'model does not support tool calling'"). A failed stage 1 is
terminal — the model may still be used for the ROUTER sidecar (no tools)
but can never be agent default.

**Stage 2 — live tool-call drill (sandboxed port 8082, ~2-4 min):**
- RAM pre-check: candidate `ram` + current `_mlx_footprint_gb()` must
  leave ≥ 8GB headroom, else the gate refuses with "pause the main model
  first" (UI offers the existing pause from the power row).
- Spawn `python3 -m mlx_lm server --model <id> --port 8082 --max-tokens
  1024` as a plain subprocess (NOT launchd — dies with the gate), poll
  `:8082/v1/models` up to 180s.
- Run the canned drill: 10 prompts against a fixed synthetic 3-tool schema
  (`get_weather(city)`, `create_task(text)`, `run_terminal(cmd)` — schema
  frozen in `model_gate.py`, mirrors the arg shapes hermes-agent's real
  tools use). 8 must-call cases scored on: `tool_calls` array present and
  parsed by the server (not raw `<tool_call>` text leaking into
  `content`), correct function name, arguments valid JSON matching the
  schema. 2 **restraint cases** ("hi there", "what's 2+2?") must return
  content with NO tool_calls. Pass = ≥ 8/10 **including both restraint
  cases** (a model that can't not-call is worse than one that fumbles
  args). Kill the subprocess in a finally.
- Multi-turn check (1 extra case): send back a synthetic tool result,
  require a coherent final message — catches template round-trip breaks.

**Stage 3 — post-switch approval canary (auto-DENY, runs after an actual
switch, not before):** one real `/api/chat` turn asking the agent to
delete a file in a canary dir (P1.4 harness shape), poll for
`state:"approval"`, assert a well-formed payload, **respond deny**, assert
graceful completion. Proves the new model drives the full serve → 
`permissions.decide` → approval pipeline. Logged to `gate/<slug>.json` and
`approval-log.jsonl`.

**Enforcement:** `aux_promotion.py` captures then redefines
`switch_model` — note aux_metrics.py:340 ALREADY wraps `switch_model` for
load-watch; `aux_promotion` (sorts after aux_metrics) captures the
metrics-wrapped version, preserving the chain:
gate-check → metrics load-watch → original. Refusal returns
`{ok:false, error:"gate not passed", gate:{...}}`; `force:true` in the
POST body bypasses with a `metrics_count("gate_forced")` breadcrumb.
`add_model` is wrapped the same way to stamp `gate:{status:"untested"}`
and queue a run if downloaded.

**Routes:** `GET /api/models/gate?id=` (report), `POST /api/models/gate`
`{id}` (run stages 1-2 in a bg thread, `status:"running"` meanwhile),
gate state rides along in `models_payload()` output (aux_promotion wraps
`models_payload` to merge — same capture pattern).

---

## 4. Frontend

All UI in a new `aux_router.js` (auto-served at `/aux_router.js` — the
`/aux_*.js` static rule exists at server.py:2127) plus minimal hooks:

- **Model menu** (index.html `loadModels()` ~line 1671, opened from
  `#model-pill` at line 795): a new "Restraint router" section under the
  roster — toggle (enable/disable), small-model picker (registry entries
  with `ram ≤ 5`), live line "today: 14 quick · 3 escalated · ~41s of 30B
  saved", and a health dot reusing the pill-dot idiom. Per-model rows gain
  a gate chip: PASS (green) / FAIL (red, tooltip = reason) / untested
  (grey "Run gate" button → POST /api/models/gate, chip flips to a
  spinner via the existing 1.5s models poll at index.html:1732). The
  Switch button on a non-passed model shows the refusal reply and offers
  "Run gate first" (primary) / "Switch anyway" (secondary, sends
  force:true after the existing `confirm()` — safe in-app since the
  WKWebView dialog handlers exist, CLAUDE.md).
- **Chat bubbles:** routed replies get a subtle footer tag "· Qwen3-4B ·
  local" (from `job["status"]` already streamed by `/api/chat/poll`;
  `streamJob()` shows status lines today — only a persistent class on the
  final bubble is new). Escalations surface the existing status line
  mechanism: "escalated to Qwen3-30B…" then the normal
  thinking/writing states from `setAgentState()` (index.html:1666).
- **Metrics view (aux_metrics.js):** one new card — routed-vs-serve turn
  split (7d), routed TTFT p50 vs serve TTFT p50, escalation rate. Data
  from the extended `/api/metrics` router block.
- **No new widget** — this is a chat-path feature; the model pill +
  metrics card are its whole surface. (If a widget is ever wanted, follow
  the WIDGETS/EXPANDERS/RENDER/WICONS recipe from CLAUDE.md verbatim.)

---

## 5. Integration points (verified names)

| Touch | Mechanism | Verified anchor |
|---|---|---|
| Chat pre-pass | redefine `_chat_worker` in aux_router.py | server.py:897 def, :2422 Thread(target=`_chat_worker`) resolves from globals |
| Job metering | jobs are MeteredJob before router sees them | aux_metrics.py:286 `_new_job` redefinition; aux load order server.py:2071 |
| Turn metrics | `job["_route_path"]` + 3-line patch in `_met_finish_turn` | aux_metrics.py:222-277, path inference :248-257 |
| Route metrics | `metrics_record` / `metrics_count` (never raise) | aux_metrics.py:128/:140 |
| HTTP routes | `register_get`/`register_post` + RouteCtx | server.py:2043-2067; aux_permissions.py as the thin-registrar exemplar |
| Sidecar mgmt | launchd bootstrap/bootout w/ sleep-3 retry | `agent_power` server.py:1847-1876; switch_model :1977-1995 |
| Model registry | `_model_registry`, `models.json`, `_model_downloaded`, `download_model` | server.py:1894-2028 |
| Switch guard | capture-and-wrap `switch_model`, `models_payload`, `add_model` | aux_metrics.py:340 precedent (already wraps switch_model) |
| Parser truth | `mlx_lm.tokenizer_utils._infer_tool_parser` + `tool_parsers/*` | installed mlx_lm 0.31.3; server rejection at mlx_lm/server.py:537 |
| Clip rerouting | replace `POST_ROUTES` entry for aux_clip's action route | aux_clip.py:40-49 CLIP_URL derivation, :192 `"model": active_model()` |
| Approval canary | P1.4 harness shape, `/api/chat/poll`, deny path | docs/plans/p1-4-approval-loop-test.md; hermes_rpc.py:284 `_perm.decide` |
| Recorder | none required (router executes no tools); gate stage-3 lands in approval-log | aux_recorder.py:533 `recorder_record_local` available if we later log gate runs |
| Prompt strip | `access_preamble()` prefix format (`[context]` lines + `\n\n`) | server.py:957-990, :2415-2419 |

## 6. Edge cases

- **E1 — sidecar down/loading:** health probe (1s timeout, 15s cache) →
  all turns to serve, `decision:"fallback"`. KeepAlive restarts it; UI dot
  goes amber ("loading") while `/v1/models` 503s.
- **E2 — memory guard confusion (MUST FIX):** `_mlx_footprint_gb` uses
  `pgrep -f mlx_lm` pids[0]; with two servers it may footprint the 4B and
  blind the 32GB guard (or misreport `ram_gb` in the pill). Fix inside
  aux_router.py by redefining `_mlx_footprint_gb` (resolved by name in
  `memory_guard_loop` each 300s tick and via `_cached("mlx_ram",...)`) to
  select the pid whose cmdline contains `--port 8080`
  (`ps -o command= -p <pid>`). Add a parallel light guard for the sidecar
  (restart above 6GB — 4B with capped prompt-cache should never get there).
- **E3 — agent paused:** `/api/chat` already fails fast when
  `agent_paused()` (server.py:2403) BEFORE `_chat_worker`, so the router
  correctly stays silent while paused — a paused Hermes must not keep
  chatting through the side door. Clip actions likewise gate on
  `model_online()` today; the clip wrapper keeps that check against the
  MAIN server so pause semantics don't change.
- **E4 — sentinel discipline:** `<<ESCALATE>>` honored ONLY inside the
  16-token holdback; later occurrences stripped from output (a model
  quoting the sentinel mid-answer must not trigger a second, contradictory
  reply). Escalation never re-enters the router (single-shot flag on the
  job).
- **E5 — serve-history divergence:** routed turns never reach the serve
  session's state.db history. Next serve turn gets the `routed_pending`
  recap prepended (§3.2); cap at 8 pairs / 2000 chars, oldest dropped.
  Without this, "actually, book that" after a routed exchange confuses the
  30B.
- **E6 — router model == active model:** if the user switches the MAIN
  model to the same small model (allowed post-gate), routing is a no-op
  latency-wise; auto-disable with a note in `/api/router` payload.
- **E7 — Telegram/CLI surfaces:** the gateway and `hermes -z` never pass
  through `/api/chat`; they are explicitly OUT of scope (Telegram is
  locked). The Console (`/api/console`, state.db) shows serve tool
  activity only — routed turns appear in metrics + chat tags instead,
  and that asymmetry is documented in the Mind card copy.
- **E8 — gate while chat is busy:** stage 2 runs on port 8082 and never
  touches the active server, so chatting continues; the RAM pre-check
  (§3.4) prevents a 30B+14B+30B-KV squeeze. `_met_chat_active`-style
  check (aux_metrics.py:366) defers auto-queued gate runs while a turn is
  live.
- **E9 — quick-ask (P2.2):** menu-bar quick-ask posts to the same
  `/api/chat`, so it inherits routing for free — its one-liners are the
  router's best customers.
- **E10 — thinking models:** Qwen3-2507-Instruct is non-thinking; if the
  user picks a thinking-variant sidecar later, `<think>` blocks blow the
  holdback window. Strip `<think>...</think>` in the stream reader (mlx_lm
  exposes think tokens via the tokenizer; a regex on the buffered text
  is sufficient at 4B scale).

## 7. Security & safety (invariants)

1. **The router adds capability nowhere.** Sidecar requests carry no
   `tools` field ever (aux_clip precedent); the small model cannot
   execute, read files, or send anything. Escalation lands in the same
   serve path with the same `permissions.decide()` gates (hermes_rpc.py:284)
   and the same manual `approvals.mode`.
2. **Fail toward the status quo.** Every router error path returns the
   turn to today's exact `_chat_worker` behavior. No new failure mode can
   lose a user message (job is finished by exactly one path; the holdback
   buffer guarantees no partial small-model text before commitment).
3. **Loopback only.** Sidecar binds 127.0.0.1:8081; gate server
   127.0.0.1:8082; no new listening surface beyond localhost, no auth
   change to serve (Bearer token untouched).
4. **No context leakage downward.** Folder grants, task list, calendar
   lines from `access_preamble()` are withheld from the small model; it
   gets time + recent chat only.
5. **Gate never uses --yolo, never auto-approves.** Stage 3 responds
   **deny** exclusively; stages 1-2 hit a sandboxed completions endpoint
   with synthetic tools that do not exist in hermes-agent. Gmail-send
   remains nonexistent; Telegram untouched.
6. **Forced switches leave a trail:** `gate_forced` counter + a
   `metrics_record("gate", stage:"forced-switch")` line; the pill shows a
   persistent amber "ungated model" chip while an unpassed model is
   active (trust view can surface it later).
7. **License field honored:** gate report records the model's license id
   (from HF cache `config.json`/README when present) — DEVPLAN risk table
   row 317 makes this a promotion-gate field (Hermes-3-8B = Llama 3
   Community License → About-view attribution reminder in the report).

## 8. Test plan (no --yolo, no real sends, drills auto-DENY)

1. **Unit, offline (no models):** `python3 dashboard/router_canary.py
   classify` — 24 canned prompts (checked into the script; includes the
   DEVPLAN "~20 canned prompts" restraint set) through
   `/api/router/test`; assert AC4 ≥ 22/24. Runs in CI-less pre-commit
   fashion like the expand.js node-eval check (CLAUDE.md).
2. **Holdback/sentinel unit:** feed a fake SSE stream (recorded fixtures:
   normal answer, immediate sentinel, mid-text sentinel, empty, garbage
   JSON) into `_rt_run_small`'s reader via a stub urlopen; assert
   job["text"] never contains sentinel text and escalations leave
   `first_token_ts` None.
3. **Sidecar live:** enable router with Qwen3-4B (download pre-approved;
   if not yet on disk this is a NEEDS-YOU-free auto step), run
   `router_canary.py live`: 6 trivial prompts → assert `path:"local"`
   turn records, TTFT p50 ≤ 400ms (AC1); 3 escalation prompts ("what's
   in my Downloads folder?") → assert `decision:"escalate"` +
   serve completion (AC3).
4. **Kill-switch:** `launchctl bootout gui/$UID/com.hermes.mlx-router`,
   send 3 trivial prompts → all `decision:"fallback"`, zero errors (AC5);
   re-bootstrap, confirm recovery within the 15s health cache.
5. **Gate roster run:** `python3 dashboard/model_gate.py run` for
   Qwen3-4B, Qwen3-8B (downloads pre-approved), and Hermes-3-8B if/once
   downloaded; verify stage-1 parser output per model, stage-2 scores,
   reports written, models.json stamped, model-menu chips render (AC6,
   AC7). Verify `switch_model` refusal on an untested id via curl, then
   force-path via curl, then check the `gate_forced` counter.
6. **Stage-3 canary (deny-only):** after gating Qwen3-8B, switch to it
   (off-hours), run the approval canary with deny, assert
   approval-log.jsonl lines, switch back to 30B. Uses the P1.4 harness
   contract exactly; no destructive action ever executes.
7. **Metrics/E2E:** after 3+5, `curl /api/metrics` shows the router
   block; Mind card renders in the app (reload WebView — ⌘R gotcha).
8. **Regression:** the P1.5 baseline drill re-run confirms serve-path
   TTFT unchanged (router disabled vs enabled-but-escalating within
   noise); memory_guard fix verified by asserting `_mlx_footprint_gb`
   returns the 8080 process's ~18GB with both servers up.

## 9. Effort & sequencing (agent-buildable; user steps: none hard-required)

| Step | What | Size | Depends |
|---|---|---|---|
| R0 | memory-guard pid fix + `_route_path` patch in aux_metrics (3 lines) | XS | — |
| R1 | mlx-router-server.sh + launchd unit + install-services.sh hook + Qwen3-4B download | S | — |
| R2 | aux_router.py: classifier + `/api/router*` + `_chat_worker` wrap + holdback/sentinel | M | R0 |
| R3 | router_canary.py + fixtures; tune `_RT_TOOLWORDS` until AC4 | S | R2 |
| R4 | aux_router.js model-menu section + chat tags + metrics card | S | R2 |
| R5 | clip rerouting wrapper | XS | R2 |
| G1 | model_gate.py stages 1-2 + reports | M | — (parallel to R*) |
| G2 | aux_promotion.py enforcement + gate chips in menu | S | G1 |
| G3 | stage-3 deny canary + roster run + docs/CHANGELOG | S | G1, R1 |

Estimated 2-2.5 focused days; R-track and G-track are independent aux
modules (two agents per the aux-module pattern, orchestrator integrates
the one shared 3-line aux_metrics patch and install-services.sh). Suggested
order inside the deadline: R0-R3 (the user-visible speed win + canary
proof), then G1-G2 (gate enforced), then R4/R5 polish, G3 last (needs an
off-hours model switch).

## 10. Open questions

1. **Sidecar default: 4B vs 8B?** Spec says Qwen3-4B-2507 (3GB, fastest,
   pre-approved). If canary quality on "explain X" prompts disappoints,
   Qwen3-8B is one config-file change — but 8B halves the latency win.
   Decide from router_canary transcripts, not vibes.
2. **DEVPLAN's "sub-2B" framing:** Qwen3-1.7B as a *decider-only* model is
   moot here — our decider is regex+sentinel (0ms/cheap). Is a learned
   sub-2B classifier ever worth it, or do we close that DEVPLAN line as
   "restraint achieved via heuristics + sentinel"? (Recommend: close it;
   revisit only if escalation-rate metrics look bad.)
3. **Speculative decoding interplay (P3.4):** the sidecar's 4B could later
   double as the `draft_model` for the 30B — but mlx_lm drafts in-process,
   not cross-server, so that's a separate flag on com.hermes.mlx-server.
   Keep RAM budgeting in one place when that spec lands.
4. **Should Watchtower/World-Brief cron turns route?** They use `hermes -z`
   (out of scope today). Hourly research loops are agentic anyway; leave
   on 30B.
5. **KV-precision per model (DEVPLAN #3 tail):** models.json could carry
   `kv_bits`, but mlx_lm server exposes KV quant flags globally, not
   per-request — defer to the speculative-decoding spec where server
   flags are already being touched.
6. **Gate threshold for the RESTRAINT cases** — both-must-pass is strict;
   if every roster model fails one restraint case, is 9/10 with one
   restraint miss acceptable? Collect the first roster run, then freeze
   the bar in this doc.
