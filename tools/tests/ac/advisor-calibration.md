# Model advisor calibration (owner-run, AC only, never CI)

The model advisor (`dashboard/aux_advisor_hw.py`) estimates resident memory,
decode speed and the cold read of Hermes's setup prompt for every curated
model on any Mac. Only two of its models are **measured**: the Qwen3.8-27B
(primary) and the Qwen3.5-9B (background lane), both on this M5 Max 40c. The
others carry labelled assumptions:

| Model | Speed constant `k` | Context slope | Reference prefill | Tool drill |
|---|---|---|---|---|
| Qwen3.5-2B | 0.65, **assumed** | formula | 19,000 / 2B, formula | none |
| Qwen3.5-4B | 0.65, **assumed** | formula | 19,000 / 4B, formula | none |
| Qwen3.6-35B-A3B | 0.32 on active bytes, **assumed** | formula | 19,000 / 3B x 0.30, formula | none |
| Qwen3.8-27B Uncensored | the 27B's, **transferred** | formula (MTP unmeasured) | the 27B's | none |

Decision D2 (verified-first) means an undrilled model can never take the
advisor's Recommended slot without a "not yet verified" badge. This procedure
replaces those assumptions with measurements. It loads models, so it is
owner-run, on AC power, and **never in CI**: nothing in `run.sh ac` runs it
(this directory holds no suites), and the script refuses on battery, in CI
and inside the unit tier.

## Before you start

- [ ] The Mac is on **AC power** (`pmset -g batt` says `AC Power`).
- [ ] No model server is resident: `pgrep -f 'mlx-vlm-launch|mlx_lm server'`
      prints nothing. Pause the agent from the model menu if it is awake.
- [ ] Nobody is mid-turn (dashboard, Telegram, Quick Ask).
- [ ] Free disk: 2B 1.75 GB · 4B 3.06 GB · 35B-A3B 20.4 GB, plus the 5 GB
      headroom every download keeps.
- [ ] Read `tools/bench/README.md` (the battery rule and the unload
      procedure). Only unload a lane **you** woke.

## 1. Baseline: what the logs already say

```bash
tools/bench/advisor_calibrate.py --dry-run          # the plan, reads nothing
tools/bench/advisor_calibrate.py                    # the 27B and the 9B from the live logs
tools/bench/advisor_calibrate.py --json > /tmp/advisor-before.json
```

It reads `~/.hermes/logs/mlx-server.log` and `mlx-bg.log` (last 8 MB of each),
detects this Mac through the advisor itself (sysctl, ioreg, pmset,
system_profiler and the Metal probe; no sudo), and prints per model:
decode tok/s by context bucket, a fit `1000/decode = t0 + slope x ctx/1000`,
`k_effective`, the slope scaled to the reference chip, the cold prefill rate,
the estimator's prediction for each bucket against the measurement, and the
delta of each fitted constant against `_ADV_FACTS`. It writes nothing.

## 2. Per model: 2B, 4B, then the 35B-A3B

Repeat for `mlx-community/Qwen3.5-2B-4bit`, `mlx-community/Qwen3.5-4B-4bit`
and (only once it is curated, decision D3) `mlx-community/Qwen3.6-35B-A3B-4bit`.

1. **Download** it from the model menu (an explicit click; the confirm quotes
   the size and the free disk).
2. **Short-context decode and prefill**, on a private server on :8090 that the
   dashboard's idle-suspend and memory guard cannot see:

   ```bash
   tools/bench/decode_bench.py --restart-with none --model <id> --runs 3
   ```

   It writes the private server's log to
   `~/.hermes/bench/decode_bench-server-blocknone.log`.
3. **Read it back** through the advisor's parser:

   ```bash
   tools/bench/advisor_calibrate.py --model <id> \
       --log ~/.hermes/bench/decode_bench-server-blocknone.log
   ```

   The bench's decode replies carry a prompt of a few dozen tokens, so its log
   has ONE context bucket. The script takes `t0` (hence `k_effective`) from it,
   subtracting the formula's context slope (a few percent at that size), and
   prints no slope: for that the model has to serve real turns at 16-40k tokens
   (step 5). The bench's cold read of its ~6.5k-token prefix (5.4k tokens) is
   one prefill-rate sample per run, because the private server's log is
   rewritten every run: repeat the run three times and take the median of
   `prefill.ref_tps`. That rate is measured at 5.4k tokens, and a prefill is
   faster on a shorter prompt (the 27B: 861 tok/s there, 750 at the 20k-token
   setup the advisor needs), so scale a bench rate by 0.87 (the 27B's ratio, an
   assumption for the others) or, better, take it from the real lane's log.
4. **Tool drill** (6 cases, `aux_promotion`): the model menu's drill button,
   or `POST /api/models/drill {"model_id": "<id>"}`. It switches the primary
   temporarily and restores it. A pass is >= 5/6. The advisor reads the
   result from `promotion.json` itself, so no copy is needed for
   "verified here".
5. **Context slope (optional, days):** run it as the background lane for a
   few days (Settings › Agent & Models › the advisor card › Use as background
   lane, or the `bg-model` file). Briefings and news run at 24-40k tokens, so
   `mlx-bg.log` fills every bucket. Then:

   ```bash
   tools/bench/advisor_calibrate.py --model <id>
   ```

6. **Two streams** (the 35B-A3B only, before it can ever be a primary):

   ```bash
   tools/bench/concurrency_probe.py --model mlx-community/Qwen3.6-35B-A3B-4bit --block none --rounds 3
   ```

   It must be clean on every round on mlx-vlm 0.6.14. Its MTP drafter
   (`mlx-community/Qwen3.6-35B-A3B-MTP-bf16`) stays off until it is measured
   the same way. That needs a roster entry with `draft_model`, then
   `decode_bench.py --restart-with none,2,3` and the probe at the best block.
7. **Unload** per `tools/bench/README.md`. `decode_bench.py --restart-with`
   kills its own private server. Restore the idle marker only if you woke a
   real lane.

## 3. Record the numbers in the facts

Edit `dashboard/aux_advisor_hw.py`, `_ADV_FACTS[<id>]`:

| Field | Set it to | And |
|---|---|---|
| `k_decode` | `k_effective` from a run with no drafter (block `none`) | `k_anchor_tps` = 1000 / t0, `k_confidence: "measured"`, `k_source` naming the run and the date |
| `ctx_slope_ms_per_1k` | `slope_ref_ms_per_1k` (printed only when >= 2 buckets have >= 3 replies each; a one-bucket bench log gives none) | `ctx_slope_with_mtp: false` for a no-drafter run, `ctx_slope_source` |
| `prefill_ref_tps` | `prefill.ref_tps` from real 20k-token reads (a bench rate x 0.87) | `prefill_confidence: "measured"`, `prefill_source` |
| `measured_ref` | the bucket medians, n, dates, chip, mlx-vlm version | |
| `verified` | `{"drill": "6/6", "where": "reference", "date": "YYYY-MM-DD", "source": ...}` after a pass | |

Then:

```bash
python3 tools/tests/unit/t_advisor_estimate.py --write-golden   # regenerate the fixture
git diff tools/tests/unit/fixtures/advisor_golden.json          # review EVERY change
HERMES_TESTS_STRICT=1 tools/tests/run.sh unit
```

`t_advisor_estimate.py` re-derives `k` from `k_anchor_tps x GB / 614`, so the
two must agree. The §5.8 design-table cross-check will flag the cells that
moved more than rounding plus 3%. Update `DESIGN_58` in the test for those
cells **in the same change**, with a line in its comment saying they come from
the measurement and not from the design.

## 4. Re-measure what CLAUDE.md quotes (decision D10)

- **9B decode at its real contexts.** `advisor_calibrate.py` prints the >=32k
  bucket. CLAUDE.md's "~88 tok/s" is the short-prompt figure; the lane mostly
  runs at 24-40k (32-42 tok/s measured).
- **9B footprint with 4 APC entries.** The estimate is 9.3-9.6 GiB against the
  older "~7 GB". With the background lane warm, run
  `footprint -p $(pgrep -f 'mlx-vlm-launch.*8081')` (needs no sudo for your
  own process) after a few briefings.
- **27B MTP code multiplier.** The advisor uses 58.7 / 30.6 = 1.92 at block 3
  (the 2026-09-04 table in `tools/bench/README.md`). CLAUDE.md's "63 code" is
  the block-4 figure. Re-run `decode_bench.py --restart-with none,3` if the
  venv or the drafter ever changes.

## What this never does

- It never runs from `run.sh` (any tier) or in CI.
- It never changes the active model, the background lane, `config.yaml`,
  idle-suspend or the prompt profile without your click.
- It never writes to `_ADV_FACTS`. You copy the numbers you trust.
- `advisor_calibrate.py` loads, wakes and contacts nothing. Only the
  `tools/bench/` scripts named above load a model, and only on AC.
