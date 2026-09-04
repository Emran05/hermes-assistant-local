# Post-v1 efficiency baseline (2026-09-04)

One deliberate measurement session on AC power, M5 Max 68 GB. The dashboard's primary
lane stayed asleep the whole time: the primary roster model
(`mlx-community/Qwen3.8-27B-4bit`, backend mlx_vlm 0.6.14 / mlx 0.32.1, native MTP
drafter `mlx-community/Qwen3.8-27B-MTP-bf16`, APC exact prefix cache, 6 entries) was
served on a spare port (8090) by a renamed copy of `mlx-vlm-launch.py` with the exact
flags `mlx-server.sh` builds, once per drafter setting, and killed after each run.
Driver: the session scratch `baseline.py` (stdlib; results in `baseline-results.json`).

Method per run: wait for `/v1/models`; one 4-token request to absorb lazy init; **cold
TTFT** = streamed first token on a ~6.5k-token shared system prefix (never seen by this
process); **warm TTFT** = same prefix, different question (APC exact hit); **decode** =
two 260-max-token generations (prose note, Python function), tok/s measured client-side
from first-to-last chunk and read from the server's own `Request completed … decode=`
log line; footprint = `footprint -p` phys_footprint after the runs. Temperature 0,
thinking off. Single run per cell — treat differences under ~5% as noise.

## Results

| Drafter block | Load (s) | Cold TTFT (s) | Warm TTFT (s) | Cold prompt tok | Prefill tok/s (server) | Warm prompt tok | Prose tok/s (client / server) | Code tok/s (client / server) | Footprint (GB) |
|---|---|---|---|---|---|---|---|---|---|
| none | 7.1 | 6.36 | 0.22 | 5443 | 861.5 | 5443 | 30.6 / 30.6 | 30.5 / 30.7 | 19.0 |
| 2 | 5.1 | 6.26 | 0.19 | 5443 | 871.8 | 5443 | 44.5 / 44.7 | 49.7 / 50.0 | 18.0 |
| 3 | 4.1 | 6.36 | 0.18 | 5443 | 858.6 | 5443 | 47.6 / 47.8 | 58.4 / 58.7 | 21.0 |
| 4 | 4.1 | 6.46 | 0.19 | 5443 | 845.6 | 5443 | 44.3 / 44.4 | 63.0 / 63.3 | 19.0 |

## What it says

- **MTP is worth ~1.5-2x on decode** over plain autoregressive (30.6 → 44-48 prose,
  30.5 → 50-63 code) at no TTFT cost; footprint moves within ±2 GB.
- **Block 3 stays the default.** It is the best prose setting (47.6) and second on code
  (58.4). Block 4 wins code by ~8% but loses prose by ~7%; block 2 loses both. This
  matches the repo's earlier decision (block 6 was slower than AR); no roster change.
- **Prefill, not load, is the wake cost.** With the weights already in the page cache the
  server loads in 4-7 s; the cold ~6.5k-token prefix costs ~6.3 s regardless of drafter
  (≈1,000 prompt tok/s end-to-end; the server reports its own prefill rate above), and
  the real Hermes system prompt is ~18k tokens — which is why the first turn after an
  idle-suspend feels like 25-30 s. The warm number (0.2 s) is what every later turn pays.
  → Backlog #1 (**prewarm after wake**) attacks exactly this; #22 (page-cache warm) only
  helps when the page cache has been evicted, which this session did not reproduce.
- **Not done here:** the 6-case tool drill of the Uncensored entry (backlog #10) — the
  drill runs through the dashboard's primary lane, which stayed asleep on purpose.

## Repro

```bash
# scratch driver used for this session (kept out of the repo; see baseline.py in the
# session scratchpad). Equivalent by hand, per block B in none/2/3/4:
~/.hermes/mlx-vlm-venv/bin/python <copy-of-mlx-vlm-launch.py> --model mlx-community/Qwen3.8-27B-4bit \
  --host 127.0.0.1 --port 8090 --max-tokens 1024 --trust-remote-code \
  --draft-model mlx-community/Qwen3.8-27B-MTP-bf16 --draft-kind mtp --draft-block-size $B
# then POST /v1/chat/completions (stream) with a fixed ~6.5k-token system prefix twice
# (cold, warm) and two 260-token generations; read prefill=/decode= from the server log.
```

---

# Concurrency and mlx-vlm 0.6.17 (2026-09-04)

Second measurement session, same machine and method (M5 Max 68 GB / 64 GiB, AC power,
primary lane asleep throughout — everything ran on spare port **8090** through a renamed
launcher copy, killed after each run; the live venv was only *executed*, never modified).
Driver: `baseline_next.py` (= `baseline.py` with the interpreter/output names
parameterised, plus a concurrency phase) and `conc_probe.py`. Answers backlog **#23**
(upgrade evaluation) and **#25** (does native MTP disable continuous batching?).

## Venvs compared

| package | live venv | `venv-next` | `venv-616` | `venv-617mlx1` |
|---|---|---|---|---|
| mlx-vlm | **0.6.14** | 0.6.17 | 0.6.16 | 0.6.17 |
| mlx / mlx-metal | **0.32.1** | 0.32.2 | 0.32.2 | **0.32.1** |
| transformers | 5.15.0 | 5.16.1 | 5.16.1 | 5.16.1 |
| huggingface_hub | 1.28.0 | 1.30.0 | 1.30.0 | 1.30.0 |

0.6.16/0.6.17 only declare `mlx>=0.32.0`, so pip takes the newest 0.32.x. `venv-617mlx1`
exists to separate "mlx-vlm regression" from "mlx 0.32.2 regression".

## Single-stream — 0.6.14 vs 0.6.17 (one run per cell, <5% is noise)

| metric | none @0.6.14 | none @0.6.17 | block 3 @0.6.14 | block 3 @0.6.17 |
|---|---|---|---|---|
| Load (s) | 7.1 | 4.1 | 4.1 | 4.1 |
| Cold TTFT (s) | 6.36 | 6.51 | 6.36 | 6.20 |
| Warm TTFT (s) | 0.22 | 0.22 | 0.18 | 0.16 |
| Prefill tok/s (server, cold) | 861.5 | 843.0 | 858.6 | 881.0 |
| Prose tok/s (client / server) | 30.6 / 30.6 | 30.5 / 30.5 | **47.6 / 47.8** | 43.7 / 43.7 |
| Code tok/s (client / server) | 30.5 / 30.7 | 30.4 / 30.5 | 58.4 / 58.7 | 58.9 / 59.2 |
| Footprint (GB) | 19.0 | 17.0 | 21.0 | 19.0 |

**A wash.** TTFT, prefill and code decode are within noise or marginally better;
footprint is ~2 GB lower in both arms; prose decode under MTP is down ~8% (47.6 → 43.7),
the only cell outside the noise band, and it is a single run. Nothing here motivates an
upgrade on its own.

## Two concurrent 200-token streams, different prompts

"Interleaved" = both first tokens arrive before either request finishes. **Every arm was
interleaved — nothing ever queued.**

| config | rounds clean | per-stream decode | aggregate | verdict |
|---|---|---|---|---|
| **0.6.14 block none** | 3/3 | 29.9 tok/s | **59.8** | correct |
| **0.6.14 block 3 (MTP)** | **6/6** | 21.2 tok/s | **42.5** | correct |
| 0.6.17 block none | 3/3 | 29.2 tok/s | 58.3 | correct |
| 0.6.17 block 3 (mlx 0.32.2) | **2/6** | 39.5 tok/s | 79 | **CORRUPT** |
| 0.6.17 block 3 (mlx 0.32.1) | **2/3** | 39.6 tok/s | 79 | **CORRUPT** |
| 0.6.16 block 3 (mlx 0.32.2) | **1/3** | ~37.6 tok/s | ~75 | **CORRUPT + GPU fault** |

Single-stream solo controls were correct and full-length in **every** arm, 0.6.17
included — the defect is concurrency-specific.

## What it says

- **#25 answered: MTP keeps continuous batching.** The gate in
  `mlx_vlm/server/generation.py` `ResponseGenerator._run_impl()` — identical in 0.6.14
  (L1745) and 0.6.17 (L1779) — is
  `if self.draft_model is not None and self.draft_kind != "mtp": self._run_speculative(); return`,
  so `draft_kind == "mtp"` **falls through** to the `BatchGenerator` loop (with
  MTP-specific batch coalescing a few lines below). Only non-MTP drafters (DFlash,
  EAGLE-3) divert to the sequential round loop. This is the opposite of mlx-lm's
  `is_batchable=False`. `/health` confirms `continuous_batching_enabled: true` in every
  arm. Hub + Telegram + bg concurrency will not serialise.
- **But MTP is a two-stream loss.** Per stream it collapses from ~47.8 tok/s solo to
  **21.2 tok/s**, and the **42.5** aggregate is **~29% below** the no-drafter **59.8**.
  Speculative decoding and batching compete for the same GPU; the drafter's wasted work
  is charged twice. Keep MTP anyway: the primary lane is effectively single-user, and the
  concurrency that would flip the trade (briefings, watchtower, For-You) already lives on
  the 9B background lane.
- **#23 answered: do not upgrade.** 0.6.16/0.6.17 corrupt output under exactly the
  concurrency #25 was meant to unlock — token-0 floods (`'When!!!!!!!!!…'`), semantic
  collapse with an early `finish_reason=stop`, and on 0.6.16 a hard
  `[METAL] … kIOGPUCommandBufferCallbackErrorPageFault`. Not the RNG shim (reproduced
  with it disabled), not mlx 0.32.2 (0.6.17 on 0.32.1 still corrupts), not the probe
  (6/6 clean on 0.6.14 MTP, 3/3 on both versions with the drafter off). The regression is
  in mlx-vlm, 0.6.15-0.6.16. Failures are non-deterministic, which is worse than a hard
  error: occasional garbage answers whenever two turns overlap, with nothing in the log.
- **Shims.** `_patch_rng_restore` can never fire here — `_restore_rng_state` is reached
  only through `_SpeculativeSamplerRNG`, which only `_run_speculative()` constructs, and
  MTP does not take that path; 3/3 sampled requests succeed with the patch commented out
  on *both* versions. It is insurance for a non-MTP drafter. The `atexit`/`os._exit(0)`
  teardown shim is still load-bearing on mlx 0.32.1 (SIGINT without it: exit `-11`,
  `EXC_BAD_ACCESS … CompileCache::CacheEntry::~CacheEntry()`); mlx 0.32.2 fixes it.
- **Open question (new backlog item).** With `temperature=1.0, top_p=0.95`, three
  identical prompts returned **byte-identical** completions on 0.6.14 and 0.6.17, with
  and without a drafter — the server appears to sample greedily (or reseed identically
  per request) on this path.
- **Acted on now:** `install-mlx-vlm-venv.sh` pinned mlx-vlm only, so **mlx floated** — a
  re-run today would have built 0.6.14 against mlx 0.32.2 / transformers 5.16.1 /
  huggingface_hub 1.30.0, which is not what production runs and silently changes teardown
  behaviour. All four are now pinned to the live venv's versions.

## Retest before any future upgrade

Require **6/6 clean concurrent rounds** on `conc_probe.py` before touching the live venv,
then the rename-aside rollback plan in the `install-mlx-vlm-venv.sh` header:
`launchctl` stop → `mv ~/.hermes/mlx-vlm-venv ~/.hermes/mlx-vlm-venv.0.6.14` (rename,
never delete) → re-run the installer with the new pins → 6-case tool drill + `conc_probe`
6/6 + a `b3-ttft-bench.py` pass. Rollback is one rename. At that point both shims go too.
