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
