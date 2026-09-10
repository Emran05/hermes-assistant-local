# ac — the tier that is allowed to load a model

**This directory is deliberately empty of suites.** Nothing in `tools/tests/`
starts, wakes or prefills a model, and nothing new here may either unless it
lands in this directory.

The model services on this Mac are on-demand (`RunAtLoad=false`,
`KeepAlive=false`) precisely so the laptop is not carrying ~19 GB and a hot GPU
while nobody is using it. A test that loads a model costs a cold start, a
noticeable chunk of battery, and — if it runs while the owner is mid-turn — a
lane fight. So:

* `run.sh ac` **refuses to run at all unless `pmset` reports AC Power.**
* The `ac` tier is never run in CI, and `.github/workflows/ci.yml` runs
  `run.sh unit` only.
* `run.sh` snapshots `pgrep -f 'mlx-vlm-launch|mlx_lm server'` before and after
  every other tier and fails the run if a model server appeared — so a test that
  drifts into waking one is caught in the tier it drifted in.

## The measurement scripts live in `tools/bench/`

They are the real AC-only work, and they are run by hand, not by this runner:

| script | loads a model? |
|---|---|
| `tools/bench/prompt_size.py` | no — it reads the MLX server log |
| `tools/bench/decode_bench.py` | yes |
| `tools/bench/ttft_after_wake.sh` | yes — wakes the primary lane |
| `tools/bench/concurrency_probe.py` | yes |

Each one enforces the AC check itself and `tools/bench/README.md` carries the
unload-afterwards procedure (a launchd `bootout`, then restore the dashboard's
idle marker so the next real turn wakes the lane transparently). Read that
before running any of them.

If a genuine regression test ever needs a live model — a decode-correctness
check, say — put it here, make it print `SKIP` and exit 0 when the model is not
already running, and say in its docstring what it costs to run.
