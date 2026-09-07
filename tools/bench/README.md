# tools/bench — deliberate measurement scripts

Four scripts, promoted out of session scratch so the numbers in
`docs/plans/post-v1-baseline.md` can be reproduced and compared instead of
re-derived. Stdlib Python and plain bash only — same constraint as the
dashboard.

Three of them load or use a model. **One does not**, and it is the one to reach
for first.

| script | what it measures | loads a model? |
|---|---|---|
| `prompt_size.py` | per-turn prompt / cached / completion tokens and the server's own prefill+decode rates, read out of the MLX server log | **no** — reads a file |
| `decode_bench.py` | decode tok/s and TTFT (cold + warm) for a lane, optionally once per MTP draft block size | yes |
| `ttft_after_wake.sh` | first-token latency of the first real turn after an idle-suspend wake, with the prewarm barrier | yes (wakes the primary) |
| `concurrency_probe.py` | two simultaneous decode streams — interleaving, per-stream and aggregate tok/s, and output **correctness** under load | yes |

## The battery rule

From `CLAUDE.md` and `docs/plans/post-v1-backlog.md`: the model services are
**on-demand** (`RunAtLoad=false`, `KeepAlive=false`, plus the start-token gate at
the top of `mlx-server.sh` / `mlx-server-bg.sh`). Measurements load ~19 GB and
run the GPU hot, so:

1. **AC power only.** Every script that generates checks `pmset -g batt` for
   `AC Power` and refuses otherwise. `--i-am-on-ac` exists for the case where
   `pmset` is unavailable — it is not a way to bench on battery.
2. **Unload afterwards.** The repo's own stop command is a launchd `bootout`

> Caution: only unload a lane **you** woke for a measurement. Never bootout a lane the owner's chat woke — the dashboard's idle-suspend loop owns that lifecycle.
   (a plain `kill` does not stick when KeepAlive is set — see `agent_power()` /
   `_mlx_restart()` in `dashboard/server.py`):

   ```bash
   launchctl bootout gui/$(id -u)/com.hermes.mlx-server     # primary lane :8080
   launchctl bootout gui/$(id -u)/com.hermes.mlx-bg         # background lane :8081
   ```

   Then leave the dashboard's idle marker so the next real turn wakes it
   transparently rather than staying down like a manual pause:

   ```bash
   printf '%s' "$(date +%s)" > ~/.hermes/dashboard/agent-idle-suspended
   rm -f ~/.hermes/dashboard/agent-paused
   ```

   `ttft_after_wake.sh` does exactly that at the end (skip with `--no-unload`);
   `decode_bench.py --unload-after` does the same. Equivalent from the UI:
   `POST /api/agent/pause` (model menu → power row) — but that writes
   `agent-paused`, which stays down and makes `/api/chat` fail fast, so it is the
   wrong end state for a bench.
3. **Never re-enable model autostart.** `~/.hermes/dashboard/model-autostart-off`
   must stay in place. `decode_bench.py --restart-with` and
   `concurrency_probe.py` therefore never touch launchd at all: they start a
   *private* server on port 8090 from a copy of `mlx-vlm-launch.py` renamed to
   `vlm-bench-launch.py`, so `pgrep -f "mlx_lm server|mlx-vlm-launch"` — the
   pattern `_mlx_proc_alive()` / `_mlx_footprint_gb()` use — never sees it and
   idle-suspend and the memory guard keep their own view of the world.

Every script takes `--dry-run`, which prints the plan and sends nothing. Use it
first.

## Where results go

Appended as JSON lines to `~/.hermes/bench/results.jsonl` (created on demand;
override with `--out`). One line per measured phase for `decode_bench.py`, one
line per run for the other two. Nothing is ever written inside the repo.

Configuration is discovered, never hard-coded: `GET /api/models` on the
dashboard (`$HERMES_DASH_URL`, else `127.0.0.1:$DASH_PORT` defaulting to 7788),
falling back to `~/.hermes/dashboard/{active-model,bg-model,models.json,server-backend}`.

## Invocations

```bash
# What is a turn costing right now? Loads nothing, safe on battery.
tools/bench/prompt_size.py
tools/bench/prompt_size.py --lane bg --limit 40
tools/bench/prompt_size.py --json

# Decode + TTFT against the lane that is already up (no extra load).
tools/bench/decode_bench.py --runs 3
tools/bench/decode_bench.py --lane bg --runs 2 --no-cold

# The block-size sweep that produced the table below. AC ONLY: this starts and
# kills one private server per value, so it is minutes of full-GPU work.
tools/bench/decode_bench.py --restart-with none,2,3,4 --runs 3

# First-token latency after a wake, with the prewarm barrier. AC ONLY.
# Run it with every model already unloaded — it refuses otherwise.
tools/bench/ttft_after_wake.sh --tag after

# Correctness + throughput under 2-way load. AC ONLY.
# THE UPGRADE GATE: 6/6 clean rounds before ~/.hermes/mlx-vlm-venv is touched.
tools/bench/concurrency_probe.py --block 3 --rounds 6
tools/bench/concurrency_probe.py --block none --rounds 3          # control
tools/bench/concurrency_probe.py --venv /tmp/venv-next/bin/python --block 3 --rounds 6
```

## Reference numbers already on record

Compare a new run against these before concluding anything changed. All were
measured on the same machine (M5 Max, 68 GB / 64 GiB), on AC, with the primary
lane asleep and the model served on spare port 8090 through a renamed launcher
copy. **Single run per cell — treat differences under ~5% as noise.**

### Decode + TTFT by MTP draft block size — 2026-09-04

`mlx-community/Qwen3.8-27B-4bit`, mlx-vlm 0.6.14 / mlx 0.32.1, drafter
`mlx-community/Qwen3.8-27B-MTP-bf16`, APC exact cache 6 entries, temperature 0,
thinking off. Source: `docs/plans/post-v1-baseline.md` (scratch `baseline.py`,
now `decode_bench.py --restart-with none,2,3,4`).

| block | load s | cold TTFT s | warm TTFT s | cold prompt tok | prefill tok/s | prose tok/s (client/server) | code tok/s (client/server) | footprint GB |
|---|---|---|---|---|---|---|---|---|
| none | 7.1 | 6.36 | 0.22 | 5443 | 861.5 | 30.6 / 30.6 | 30.5 / 30.7 | 19.0 |
| 2 | 5.1 | 6.26 | 0.19 | 5443 | 871.8 | 44.5 / 44.7 | 49.7 / 50.0 | 18.0 |
| **3 (default)** | 4.1 | 6.36 | 0.18 | 5443 | 858.6 | **47.6 / 47.8** | 58.4 / 58.7 | 21.0 |
| 4 | 4.1 | 6.46 | 0.19 | 5443 | 845.6 | 44.3 / 44.4 | **63.0 / 63.3** | 19.0 |

MTP is worth ~1.5-2x on decode at no TTFT cost. Block 3 stays the roster
default: best prose, second on code. Prefill, not load, is the wake cost.

### Two concurrent 200-token streams — 2026-09-04

Source: `docs/plans/post-v1-baseline.md` § *Concurrency and mlx-vlm 0.6.17*
(scratch `conc_probe.py`, now `concurrency_probe.py`). "Clean" = both streams
interleaved, both full length, no error, no character flood.

| config | rounds clean | per-stream tok/s | aggregate tok/s | verdict |
|---|---|---|---|---|
| **0.6.14 block none** | 3/3 | 29.9 | 59.8 | correct |
| **0.6.14 block 3 (live venv)** | **6/6** | 21.2 | 42.5 | correct |
| 0.6.17 block none | 3/3 | 29.2 | 58.3 | correct |
| 0.6.17 block 3 (mlx 0.32.2) | 2/6 | 39.5 | 79 | **CORRUPT** |
| 0.6.17 block 3 (mlx 0.32.1) | 2/3 | 39.6 | 79 | **CORRUPT** |
| 0.6.16 block 3 (mlx 0.32.2) | 1/3 | ~37.6 | ~75 | **CORRUPT + GPU fault** |

Two facts to carry forward: MTP keeps continuous batching (nothing queues), and
MTP is a two-stream *loss* — 42.5 aggregate against 59.8 with no drafter. It is
kept because the primary lane is effectively single-user; concurrent producers
live on the 9B background lane. And backlog #23 is parked on this table:
**do not upgrade mlx-vlm past 0.6.14** until a candidate is 6/6 clean here.

### Single-stream 0.6.14 vs 0.6.17 — 2026-09-04

| metric | none @0.6.14 | none @0.6.17 | block 3 @0.6.14 | block 3 @0.6.17 |
|---|---|---|---|---|
| cold TTFT s | 6.36 | 6.51 | 6.36 | 6.20 |
| warm TTFT s | 0.22 | 0.22 | 0.18 | 0.16 |
| prose tok/s | 30.6 | 30.5 | **47.6** | 43.7 |
| code tok/s | 30.5 | 30.4 | 58.4 | 58.9 |
| footprint GB | 19.0 | 17.0 | 21.0 | 19.0 |

### First token after an idle-suspend wake — 2026-09-04

Source: `CHANGELOG.md` [1.0.1], backlog #1 (scratch `ttft_after_wake_v2.sh`, now
`ttft_after_wake.sh`). Measured through the real path: wake → prewarm barrier →
one real `/api/chat` turn.

| | first token of the next real turn |
|---|---|
| before prewarm-after-wake | **29.1 s** |
| after prewarm-after-wake | **1.7 s** |

The prewarm turn itself costs ~27 s in the background right after the wake —
that is the ~18k-token system-prompt prefill being paid where nobody is waiting.

### Prompt size (context for all of the above)

The baseline's cold TTFT used a synthetic ~6.5k-token prefix (5443 prompt
tokens). The real Hermes system prompt was ~18k tokens on 2026-09-04, which is
why a cold first turn felt like 25-30 s. Run `prompt_size.py` to see what it is
today — as of 2026-09-07 the median hub turn sent ~24k prompt tokens with ~98%
served from the APC exact prefix cache.

## Notes on what these scripts do NOT do

- They never wake a lane. If the lane is asleep, `decode_bench.py` says so and
  exits; only `ttft_after_wake.sh` wakes anything, because waking is the thing it
  measures.
- They never modify `~/.hermes/mlx-vlm-venv`. To evaluate a candidate mlx-vlm,
  build a throwaway venv and pass `--venv /path/to/it/bin/python`; the rollback
  plan for the live venv is the rename-aside procedure in the
  `install-mlx-vlm-venv.sh` header.
- `concurrency_probe.py --sampled-probe` and `--teardown-probe` are the two
  leftover shim probes from backlog #23 (does a `temperature>0` request survive
  the RNG shim; does the process exit cleanly on SIGTERM without the
  `os._exit(0)` teardown shim). Backlog #27 — identical prompts at
  `temperature=1.0` returning byte-identical completions — is still open, and
  `--sampled-probe` is where to start on it.
