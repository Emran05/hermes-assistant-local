# Model advisor: system spec analysis + "what fits what you want to do"

Design for Hermes Assistant, written 2026-10-26 against
`~/HermesAssistant-worktrees/integration` (branch
`audit-2026-10-21`, HEAD `0bd1585`). Design only: nothing in any checkout, in
`~/.hermes` or in `~/.claude` was edited, no model was loaded, and ports 7788,
8080, 8081 and 9119 were not contacted. The local measurements below came from
read-only `sysctl`, `ioreg`, `pmset`, `system_profiler` and a stdlib Metal probe,
plus reads of the model servers' own logs.

The owner asked for: *"a system spec analysis where we then determine what
models would fit based on what the user wants to do and also sys limits"*. The
same request asks for a GitHub-access onboarding step. That feature is designed
separately. This document covers how the two share `aux_onboarding.*` (§9, C4).

---

## 0. Summary

Today the recommendation comes from **one number, RAM**: `_onb_tier(ram_gb)`
(`dashboard/aux_onboarding.py:279-352`). The per-model badge comes from
**one ratio**, `_model_fit` (`server.py:1955`; resident > 0.85 × RAM means no,
> 0.60 × RAM means tight). Neither looks at the chip, the GPU memory budget,
the cache Hermes keeps resident, the use case, or how long the first reply
takes. This design adds four things:

1. **Detection.** Chip family and variant, GPU cores, memory bandwidth (static
   table), the real GPU working-set budget, power source, fanless or not, and
   thermal state. Every command is cheap, needs no sudo and is cached. Every
   parser is pure.
2. **A six-question use-case questionnaire.** Each answer maps to an explicit
   requirement or weight.
3. **An estimator.** For each (model, Mac) pair it estimates resident memory
   (weights, the KV cache for the turn, the APC prefix-cache entries, runtime
   overhead), decode tok/s and the cold prefill time for Hermes's own setup
   prompt. It is calibrated on this Mac's measured numbers. Every figure is
   labelled as an estimate and shows its formula.
4. **A ranking.** It picks a primary plus background-lane pair, gives a
   one-line "why", and lists what won't fit or is too slow, and why. It is
   surfaced in a new onboarding step, a re-runnable "Model advisor" card in
   Settings, and "why this fits" rows in the model menu. **Nothing switches or
   downloads without the owner's click.**

### Findings from building the estimator

- **The GPU budget can be read with no sudo and no MLX.** A 30-line stdlib
  `ctypes` call to Metal's `MTLCreateSystemDefaultDevice().recommendedMaxWorkingSetSize`
  returned **55,662,788,608 B = 51.84 GiB = 0.81 × 64 GiB** in 0.1 s wall
  (`/usr/bin/python3 -I`, measured 2026-10-26, macOS 26.5.2). That is higher
  than the "~75%" folklore, and it is the same number MLX reports as
  `max_recommended_working_set_size`. `sysctl iogpu.wired_limit_mb` reads `0`,
  meaning the system default.
- **On anything but an M5, prefill time is the problem, not decode speed.**
  Every fresh model session first prefills Hermes's setup prompt: 20,468-20,622
  tokens on this Mac (`aux_promptbudget.py:124`), about 18.5k on the Balanced
  profile new installs start with. The M5's per-core neural accelerators make
  that prefill ~3.6x faster per GPU core than the M4 (llama.cpp #4167: M5 Max
  80 tok/s/core vs M4 Max 22). So the 27B's **~27 s cold first reply here
  becomes ~90 s on a 128 GB M4 Max, ~180 s on a 48 GB M4 Pro and ~230 s on a
  36 GB M3 Pro.** The RAM-only tier recommends the 27B on all three.
- **Decode slows hard with context, and the logs show it.** On this Mac
  (`~/.hermes/logs/mlx-*.log`, 2026-08-18 to 09-10):
  - The 9B background lane gives a median **93 tok/s under 2k tokens of
    context, 42 at 24-32k and 32 at ≥32k** (n=552). CLAUDE.md's "~88 tok/s" is
    the short-prompt number. The lane mostly runs at ≥24k.
  - The 27B with MTP gives a median **53 tok/s under 2k, 37 at 16-24k and 34
    at 24-32k**.
  - So the estimator carries a measured per-1k-token context slope.
- **The calibrated formulas reproduce this Mac within about 11%,
  conservatively.**

  | Quantity | Estimate | Measured |
  |---|---|---|
  | 27B decode, no drafter | 30.6 tok/s | 30.6 |
  | 27B decode with MTP, prose | 47.7 tok/s | 47.8 |
  | 27B decode, 20k context | 35.0 tok/s | 36.7 |
  | 9B decode, short | 84.4 tok/s | 88-93 |
  | 9B decode, ≥32k | 32.7 tok/s | 32.3 |
  | 27B cold prefill, 20.5k tokens | 27.3 s | 26.1 |
  | 9B cold prefill, 24.2k tokens | 12.3 s | 12.3 |
  | 27B resident with 6 APC entries | 26.2 GiB | "~26 GB" |

- **The flat ceiling on ≥64 GB Macs binds before the hardware does.**
  `_mem_ceilings` (`server.py:1897`) is a flat 50/56 GB at ≥64 GB, so a 128 or
  192 GB Mac gets the same ~48 GB plan limit as this one. That is deliberate
  (it bounds the KV balloon), but the advisor has to say so (decision D8).

---

## 1. What exists today (grounding)

| Piece | Where | What it does / why it matters here |
|---|---|---|
| `_onb_sysinfo()` | `aux_onboarding.py:158-177` | chip brand (`sysctl machdep.cpu.brand_string`), `ram_gb` (`_machine_ram_gb`), free disk, macOS, laptop (`pmset -g batt` mentions `InternalBattery`), `os.cpu_count()` |
| `_onb_detect()` | `:237-273` | CLIs present, models downloaded (weights **and** drafter, C70), FDA, Telegram/Google booleans |
| `_onb_tier(ram)` | `:279-352` | PURE RAM tiers: <16 2B · 16-23 4B · 24-35 9B+2B · 36-47 27B (tight)+9B · ≥48 27B+9B. Deliberately stricter than `_model_fit` (whole-system budget) |
| `_ONB_CATALOG` | `:77-101` | four FULL roster shapes, sizes verified against HF by hand 2026-09-05 |
| `_onb_fits` / `_onb_recommend` | `:355-380` | per-model `_model_fit` badges plus download totals |
| `/api/onboarding/apply` | `:592-796` | validated, writes only through existing helpers. Roster writes are additive. The `bg-model` file is the background lane. Activates the primary only while the server is down (C63). Refuses a download if free disk < need + 5 GB |
| `_onb_known_model` | `:550-559` | apply refuses any id that is not in the catalog or the roster |
| `_machine_ram_gb` / `_mem_ceilings` | `server.py:1858` / `:1897` | GiB from `hw.memsize`. ≥64 GB gives (50, 56); below that, (0.72×, 0.82×) with floor (8, 10) |
| `_model_fit` | `server.py:1955` | ok / tight / no against total RAM, one model at a time |
| `_SEED_MODELS` / `_model_registry` | `server.py:3960` / `:4042` | roster: 27B, 27B Uncensored, 9B |
| `_disk_free_gb`, `_catalog_dl_gb`, `_model_download_gb`, `_disk_short` | `server.py:4317/4352/4365/4399` | download size (measured on disk once complete, else the catalog) and the shared 5 GB headroom rule |
| `models_payload()` | `server.py:4438` | per-row `fit`, `download_gb`, `downloaded`; top-level `mem{soft_gb,hard_gb,machine_gb}`, `disk_free_gb`. Re-wrapped by `aux_promotion.py:583-610` (adds drill fields) |
| `download_model`, `_curated_model_ids`, `add_model` | `server.py:4634/4758/4764` | F01/B03 lockdown: curated = seeds ∪ `_ONB_BY_ID`. Others need `confirm:true`. Never `trust_remote_code`, never pickle or `.py` |
| APC tiers | `mlx-server.sh:185-188`, `mlx-server-bg.sh:87-90` | primary entries 6/4/3/2 at ≥64/≥48/≥36/else. Background lane 4/3/2/2 |
| Model menu Details | `index.html:2841` `modelDetailsHTML`, `:2889` `loadModels` | a `<dl>` sibling panel. `mmOpen` keeps it open across the 30 s rebuild |
| Drill results | `aux_promotion.py:82` `_pro_read()`, `PRO_OF=6`, `PRO_THRESHOLD=5` | per-model tool-call drill score, on THIS Mac |
| Model-server log parser | `aux_context.py:287` `_cx_rows(path)`, logs `:112/:117` | per request: model, prompt/cached tokens, prefill and decode tok/s, mtime-cached |
| Prompt-prefix constants | `aux_promptbudget.py:114` `_PB_PREFILL_TOK_S=750`, `:124` `_PB_BASELINE_TOKENS=20622` | the cold prefix and the 27B prefill rate the Prompt-budget card already quotes |
| Settings writes | `server.py:2850` `settings_update`, `:2895` `settings_set` | the only legal way to write settings.json (A01) |
| Settings panels | `aux_settings_shell.js:103-120` | `sec-models` = "Agent & Models". Cards mount directly (`aux_evals.js:60` pattern) |

Invariants this design keeps:
- No network at request time; the catalog is static.
- No model is woken. The advisor never calls `model_online()`, `bg_online()`,
  `mlx_admission()` or anything else that touches :8080/:8081/:9119.
- An aux module never does `from datetime import datetime`, and resolves
  cross-module names at call time.
- No settings write outside `settings_update`.
- Zero emoji. `esc()` on every interpolation. Hit areas ≥40 px.
- `aux_settings_shell.js` is never edited.
- NETWORK_FACTS gains **no** row: the advisor makes no network call, and
  downloads are the existing Hugging Face row.

---

## 2. Measured on this Mac (read-only, 2026-10-26)

| Fact | Command | Result | Wall time |
|---|---|---|---|
| RAM | `/usr/sbin/sysctl -n hw.memsize` | 68719476736 (64 GiB) | <0.01 s |
| Chip | `sysctl -n machdep.cpu.brand_string` | `Apple M5 Max` | <0.01 s |
| Model id | `sysctl -n hw.model` | `Mac17,6` | <0.01 s |
| CPU clusters | `sysctl hw.nperflevels`, `hw.perflevelN.{name,logicalcpu}` | 2 levels: `Super` ×6, `Performance` ×12 (on M5 Pro/Max, perflevel1 is NOT efficiency cores) | <0.01 s |
| GPU cores | `/usr/sbin/ioreg -rc AGXAccelerator` → `"gpu-core-count" = 40` | 40 | 0.02 s |
| Hardware | `/usr/sbin/system_profiler SPHardwareDataType -json` | `machine_name "MacBook Pro"`, `chip_type "Apple M5 Max"`, `number_processors "proc 18:6:0:12"` | 0.12 s |
| GPU wired limit | `sysctl -n iogpu.wired_limit_mb` | `0` (= system default) | <0.01 s |
| **GPU working-set budget** | stdlib ctypes Metal probe (§3.4) | **55,662,788,608 B = 51.84 GiB (0.81 × RAM)**; `maxBufferLength` 41.7e9 B; `hasUnifiedMemory` true | 0.10 s |
| Power | `/usr/bin/pmset -g batt` | `Now drawing from 'Battery Power'` · `InternalBattery-0 41%; discharging` | <0.01 s |
| Power mode | `pmset -g` | `powermode 2` (mapping 0 auto / 1 low / 2 high is believed, **unverified**) | <0.01 s |
| Thermal | `pmset -g therm` | "No thermal warning level has been recorded" (three lines) | <0.01 s |
| Boot id (cache key) | `sysctl -n kern.boottime` | `{ sec = 1789568983, ... }` | <0.01 s |

Measured from the model servers' own logs (`_cx_parse`-compatible regexes;
rows with ≥64 generated tokens):

| Lane, model | Context bucket | n | Median decode tok/s |
|---|---|---|---|
| primary, Qwen3.8-27B-4bit + MTP | <2k | 13 | 53.2 |
|  | 16-24k | 9 | 36.7 |
|  | 24-32k | 7 | 34.1 |
| bg, Qwen3.5-9B-4bit | <2k | 5 | 93.1 |
|  | 2-8k | 10 | 82.0 |
|  | 24-32k | 171 | 42.0 |
|  | ≥32k | 552 | 32.3 |

Cold prefill (rows with ≥4,000 new tokens):
- 27B: median 786 tok/s, 20,468 new tokens, 26.1 s, n=9.
- 9B: median 1,969 tok/s, 24,248 new tokens, 12.3 s, n=152.

---

## 3. (a) Detection additions

All in a new `dashboard/aux_advisor_hw.py`. Every runner calls an absolute path
through one `_adv_run(argv, timeout)` helper. That helper uses the module-level
`subprocess` name, as `_onb_run` does, so harnesses can stub it. Under launchd,
PATH lacks `/usr/sbin`.

### 3.1 Facts, commands, parsers

| Fact | Command (no sudo) | Pure parser | Notes |
|---|---|---|---|
| chip family / variant / generation | `sysctl -n machdep.cpu.brand_string` | `_adv_parse_brand(s)` → `{vendor, family:"M5", gen:5, variant:"Max"\|"Pro"\|"Ultra"\|"", supported}` | regex `^Apple (M(\d+))(?: (Pro\|Max\|Ultra))?$`. Contains `Intel` → `supported:false` ("local models need Apple silicon"). Other `Apple …` (A-series, future M6+) → `family:null`, uses the conservative fallback in 3.3 |
| Rosetta | `sysctl -n sysctl.proc_translated` | int | `1` means the dashboard python runs translated. Note only |
| GPU cores | `ioreg -rc AGXAccelerator` (fast); fallback `system_profiler SPDisplaysDataType -json` → `sppci_cores` (a STRING) | `_adv_parse_ioreg_cores(text)` regex `"gpu-core-count" = (\d+)`; `_adv_parse_sp_displays(json)` | unknown → the variant's LOWER binned row (conservative), `cores_source:"assumed"` |
| model id, machine name | `sysctl -n hw.model`; `system_profiler SPHardwareDataType -json` | `_adv_parse_sp_hardware(json)` | `machine_name` contains "MacBook Air" → fanless |
| CPU clusters | `sysctl hw.nperflevels hw.perflevel{0,1}.{name,logicalcpu}` | `_adv_parse_perflevels(map)` | shown, not ranked. Never assume perflevel1 = efficiency |
| form | from the two above + `pmset -g batt` | `_adv_form(machine_name, has_battery)` → `laptop_fanless`\|`laptop`\|`desktop` | |
| power source | `pmset -g batt` | `_adv_parse_batt(text)` → `{source:"ac"\|"battery"\|"unknown", pct, has_battery}` | `Now drawing from 'AC Power'` / `'Battery Power'` |
| Low Power Mode | `pmset -g` | `_adv_parse_pmset(text)` → `{low_power: true\|false\|null}` | `lowpowermode 1` or `powermode 1`. The powermode values are **unverified**; null when absent |
| thermal | `pmset -g therm` | `_adv_parse_therm(text)` → `{limited: bool\|null, cpu_speed_limit, text}` | "No … has been recorded" ×3 → `false`. A `CPU_Speed_Limit` < 100 → `true` |
| GPU budget | §3.4 | `_adv_gpu_budget(ram_gb, wired_mb, metal_bytes)` | |
| RAM | existing `_machine_ram_gb()` (by name) | | GiB |
| disk | existing `_disk_free_gb()` (by name) | | GiB, `f_bavail` |
| macOS | `sw_vers -productVersion` | | recorded with every estimate. The M5 prefill gain is attributed to the neural accelerators (Apple's MLX/M5 post); whether an older macOS loses it is unverified |

Not detected, on purpose:
- Watts. `powermetrics` needs sudo, and published GPU-only numbers undercount
  (Apple dev forum 819927).
- Fan count. The machine name is enough.
- Current memory pressure. `kern.memorystatus_level` and `vm.swapusage` are
  shown as one "right now" line, never ranked.

### 3.2 Caching

| Cache | Contents | Refresh |
|---|---|---|
| `_ADV_STATIC` | chip, cores, model id, machine name, perflevels, RAM, macOS, GPU budget | process lifetime, keyed on `kern.boottime` + `iogpu.wired_limit_mb` (an owner `sudo sysctl iogpu.wired_limit_mb=…` changes the key) |
| `_ADV_DYNAMIC` | power, low power, thermal, disk | 60 s TTL through `_cached` |
| total cold cost | — | ~0.3 s (measured pieces 0.01-0.12 s each), plus 0.1 s for the Metal probe once per boot |
| failure | — | each fact degrades to `null` plus a `source:"unknown"`. Detection never raises |

### 3.3 Chip table (static, keyed by family, variant, GPU cores)

Bandwidth is the Apple/Wikipedia spec. `pp` and `tg` are llama.cpp Llama-2-7B
Q4_0 pp512/tg128 from ggml-org/llama.cpp discussion #4167, fetched 2026-10-26.
Derived columns:
- `e_chip = (tg/bw) / (119.92/614)`: decode bandwidth efficiency relative to
  the reference M5 Max 40c. Model size cancels out.
- `p_index = pp / 3219.99`: prefill compute relative to the reference.

| Family / variant | GPU cores | BW GB/s | pp512 | tg128 | e_chip | p_index | Row source |
|---|---|---|---|---|---|---|---|
| M1 | 7 | 68.25 | 107.81 | 14.19 | 1.07 | 0.033 | measured row |
| M1 | 8 | 68.25 | 117.96 | 14.15 | 1.07 | 0.037 | measured row |
| M1 Pro | 14 / 16 | 200 | 232.55 / 266.25 | 35.52 / 36.41 | 0.91 / 0.93 | 0.072 / 0.083 | measured row |
| M1 Max | 24 / 32 | 400 | 400.26 / 530.06 | 54.61 / 61.19 | 0.70 / 0.78 | 0.124 / 0.165 | measured row |
| M1 Ultra | 48 / 64 | 800 | 772.24 / 1030.04 | 74.93 / 83.73 | 0.48 / 0.54 | 0.240 / 0.320 | measured row |
| M2 | 8 / 10 | 100 | 145.91 / 179.57 | 21.70 / 21.91 | 1.11 / 1.12 | 0.045 / 0.056 | measured row |
| M2 Pro | 16 / 19 | 200 | 294.24 / 341.19 | 37.87 / 38.86 | 0.97 / 0.99 | 0.091 / 0.106 | measured row |
| M2 Max | 30 / 38 | 400 | 537.60 / 671.31 | 60.99 / 65.95 | 0.78 / 0.84 | 0.167 / 0.208 | measured row |
| M2 Ultra | 60 / 76 | 800 | 1013.81 / 1238.48 | 88.64 / 94.27 | 0.57 / 0.60 | 0.315 / 0.385 | measured row |
| M3 | 8 | 102.4 | (scaled) | (scaled) | 1.07 | 0.046 | per-core from the 10c row |
| M3 | 10 | 102.4 | 186.75 | 21.34 | 1.07 | 0.058 | measured row (🟨) |
| M3 Pro | 14 / 18 | 150 | 269.49 / 341.67 | 30.65 / 30.74 | 1.05 | 0.084 / 0.106 | measured row (14c 🟨) |
| M3 Max | 30 / 40 | 300 / 400 | 567.59 / 759.70 | 56.58 / 66.31 | 0.97 / 0.85 | 0.176 / 0.236 | measured row |
| M3 Ultra | 60 / 80 | 819.2 | 1073.09 / 1471.24 | 88.40 / 92.14 | 0.55 / 0.58 | 0.333 / 0.457 | measured row |
| M4 | 8 | 120 | (scaled) | (scaled) | 1.03 | 0.055 | per-core from 10c |
| M4 | 10 | 120 | 221.29 | 24.11 | 1.03 | 0.069 | measured row |
| M4 Pro | 16 / 20 | 273 | 364.06 / 439.78 | 49.64 / 50.74 | 0.93 / 0.95 | 0.113 / 0.137 | measured row |
| M4 Max | 32 / 40 | 410 / 546 | 713.93 / 885.68 | 69.95 / 83.06 | 0.87 / 0.78 | 0.222 / 0.275 | measured row |
| M5 | 8 | 153.6 | (scaled) | (scaled) | 1.06 | 0.180 | per-core from 10c, **low** |
| M5 | 10 | 153.6 | 722.79* | 31.88* | 1.06 | 0.224 | *column mapping inferred by the page reader, **low** |
| M5 Pro | 16 / 20 | 307 | 1340.44 / 1620.64 | 67.59 / 66.33 | 1.13 / 1.11 | 0.416 / 0.503 | measured row |
| M5 Max | 32 | 460 | (scaled) | (scaled) | 1.00 | 0.800 | per-core from 40c |
| **M5 Max** | **40** | **614** | **3219.99** | **119.92** | **1.00** | **1.000** | **reference (this Mac)** |
| M5 Ultra | 64 | 1228.8 | (scaled) | (scaled) | 0.75 | 1.229 | per-core from 80c, **low** |
| M5 Ultra | 80 | 1228.8 | 4944.66 | 179.10 | 0.75 | 1.536 | measured row; memory options inconsistent in sources, **low** |

Rules:
- Unknown cores → the lower binned row.
- An unknown variant of a known family → the base row.
- Unknown `Apple …` → M1 8c (the slowest known row) with
  `confidence:"low"` and a note "estimates assume the slowest Apple-silicon
  chip".
- A family newer than M5 → the M5 row of the same variant as a **floor**, with
  `confidence:"low"`.

The table is **data**: one dict literal. A unit test asserts every row's
derived `e_chip`/`p_index` from the raw columns, so a hand edit cannot leave
them stale.

### 3.4 GPU working-set budget, read for real

`_adv_metal_probe()` runs ONE short-lived subprocess:
`[sys.executable, "-I", "-c", PROBE_SRC]`, timeout 2 s, empty env except PATH.
`PROBE_SRC` uses stdlib `ctypes` only:
1. Load `/usr/lib/libobjc.A.dylib` and
   `/System/Library/Frameworks/Metal.framework/Metal`.
2. Call `MTLCreateSystemDefaultDevice()`.
3. Call `objc_msgSend` for `recommendedMaxWorkingSetSize`, `maxBufferLength`,
   `hasUnifiedMemory` and `name`/`UTF8String`.
4. Print one JSON line.

It is the probe that produced the 51.84 GiB above. Why a subprocess:
- A ctypes misuse cannot crash the dashboard.
- The dashboard process never owns a Metal device.
- No MLX venv is touched, so the probe works on a Mac where the venv is
  missing.
- A LaunchAgent can create a Metal device: the model servers do exactly that
  under launchd.

`_adv_gpu_budget(ram_gb, wired_mb, metal_bytes)` is PURE. It returns
`{gb, source}`, in this precedence:
1. `wired_mb > 0` → `wired_mb/1024`, `source:"iogpu.wired_limit_mb"`, note
   "raised by sudo sysctl; resets at reboot".
2. `metal_bytes > 0` → `metal_bytes/2**30`, `source:"metal"`.
3. Otherwise an assumed fraction: `0.75 × RAM` at ≥36 GB, `0.66 × RAM` below,
   `source:"assumed"`. Apple does not document the fraction. The only measured
   point is 0.81 at 64 GB, so this fallback is conservative. The UI says
   "assumed".

Open question: whether raising `iogpu.wired_limit_mb` also changes
`recommendedMaxWorkingSetSize` is **unverified**. The sysctl wins when it is
set.

### 3.5 The plan limit (what the lanes together may use)

```
limit_gb = min( gpu_budget_gb,                 # Metal / sysctl / assumed
                soft_gb - 2,                   # _mem_ceilings(ram).soft: admission refuses at >= soft;
                                               #   2 GB = one ~20k-token turn of KV growth
                ram_gb - reserve_gb )          # macOS + this app + a browser
reserve_gb = 6.0 if ram >= 24 else 4.5 if ram >= 16 else 3.5      # judgement, labelled
```

`_adv_limits()` reads `_mem_ceilings` by name, so the env overrides
`MLX_SOFT_GB`/`MLX_HARD_GB` flow through. It also reports which term binds
(`binding: "gpu"|"soft"|"reserve"`). The UI says, for example, "Hermes caps
model memory at 50 GB on Macs with 64 GB or more" when `soft` binds on a
128 GB Mac.

Worked limits:

| Machine | BW | e_chip | p_index | GPU budget | soft | **limit** |
|---|---|---|---|---|---|---|
| MacBook Air M1, 8 GB | 68 | 1.07 | 0.037 | 5.3 (assumed) | 8 | **4.5** |
| MacBook Air M2, 16 GB | 100 | 1.12 | 0.056 | 10.6 (assumed) | 12 | **10.0** |
| MacBook Air M4, 24 GB | 120 | 1.03 | 0.069 | 15.8 (assumed) | 17 | **15.0** |
| MacBook Pro M1 Max 32c, 32 GB | 400 | 0.78 | 0.165 | 21.1 (assumed) | 23 | **21.0** |
| MacBook Pro M3 Pro 18c, 36 GB | 150 | 1.05 | 0.106 | 27.0 (assumed) | 26 | **24.0** |
| MacBook Pro M4 Max 32c, 36 GB | 410 | 0.87 | 0.222 | 27.0 (assumed) | 26 | **24.0** |
| Mac mini M4 Pro 20c, 48 GB | 273 | 0.95 | 0.137 | 36.0 (assumed) | 35 | **33.0** |
| **MacBook Pro M5 Max 40c, 64 GB (this Mac)** | 614 | 1.00 | 1.000 | **51.8 (metal)** | 50 | **48.0** |
| Mac Studio M2 Max 38c, 96 GB | 400 | 0.84 | 0.208 | 72.0 (assumed) | 50 | **48.0** |
| MacBook Pro M4 Max 40c, 128 GB | 546 | 0.78 | 0.275 | 96.0 (assumed) | 50 | **48.0** |
| Mac Studio M2 Ultra 76c, 192 GB | 800 | 0.60 | 0.385 | 144.0 (assumed) | 50 | **48.0** |

---

## 4. (b) The use-case questionnaire

Three questions. A fourth appears only on a laptop. Nothing is asked that the
Mac already knows.

**Q1 "What will you use Hermes for?"** Multi-select chips, at least one.

| Key | Label | Default | What it changes |
|---|---|---|---|
| `chat` | Everyday chat and writing | on | quality weight on `q.chat`; turn context 25k |
| `agent` | Getting things done on this Mac (files, apps, the web) | on | quality weight ×1.5 on `q.agent`; **primary floor `q.agent ≥ 4`** (the 4B or larger); MTP mix "agent" |
| `code` | Coding help | off | quality weight ×1.5 on `q.code`; **floor `q.code ≥ 6`** (9B or larger); turn context 40k; MTP mix "code" |
| `docs` | Long documents and big files | off | turn context **131,072**. Needs `ctx ≥ 131072` (all candidates). Note: "raise the context window to 128K in Settings › Agent & Models › Context" (`model.context_length` is 65,536 here; the advisor never writes it) |
| `vision` | Screenshots and images | off | prefers models whose `vision` is true; `null` (unverified) passes with a note |
| `uncensored` | Unfiltered research (no refusals) | off | adds uncensored builds to the candidate set **as alternatives only** (D4). Needs an explicit acknowledgement first (copy below) |

**Q2 "What matters more?"** Segmented, default Balanced.

| Value | w_quality | w_speed |
|---|---|---|
| Faster replies | 0.30 | 0.70 |
| Balanced | 0.55 | 0.45 |
| Best answers | 0.80 | 0.20 |

**Q3 "Where do you use it?"** Laptops only. Default is the current power
source.

| Value | Effect |
|---|---|
| Mostly plugged in | the cold-prefill penalty is halved (an awake model rarely re-reads its setup) |
| Often on battery | adds `w_battery = 0.25` (the other weights are scaled ×0.75); note texts in §5.6 |

On a desktop Q3 is hidden and the answer is plugged in.

The background-lane value is **not asked**. It is read from the existing
watchtower masters (`briefings`/`news`, via `_onb_prefs()` or `_wt_load` by
name). If either is on, the background lane earns its bonus. The plan's note
says: "the background lane runs briefings and news; with both off you can skip
it".

**Uncensored acknowledgement.** The copy is shown inline the first time the
chip is tapped. The chip does not turn on until "Show uncensored options" is
pressed.

> Uncensored models have had their refusal behaviour removed. They will answer
> things the standard model declines, they are no more accurate, and some still
> open with disclaimers. Nothing inside the model stops a harmful request. On
> this Mac the brake is the approval gate, which still asks before risky
> actions such as controlling apps or writing to protected places, and the
> permission tiers. The gate is a path denylist, not a sandbox: commands the
> agent runs in its terminal are not covered by it. These models are never the
> default and appear only as alternatives.
> [Show uncensored options] [Cancel]

**Storage.** `settings.json` key `advisor` (owned by this feature), written
only through `settings_update`:

```json
{"uses": ["chat", "agent"], "priority": "balanced", "power": "ac",
 "uncensored_ack": false, "saved_at": 1798000000}
```

Validation (400 on any failure):
- `uses` ⊆ the six keys, non-empty, deduplicated.
- `priority` ∈ {`faster`, `balanced`, `best`}.
- `power` ∈ {`ac`, `battery`, null}.
- `uncensored` ∈ `uses` requires `uncensored_ack === true`.

Config-as-Code's `CFG_SETTINGS_ALLOW` exports list-valued keys only, so
`advisor` is not exported. That is acceptable (D12).

---

## 5. (c) The estimator

All formulas live in `aux_advisor_hw.py` as pure functions over one catalog
entry plus one chip row. Every output carries the following, and the UI never
shows a bare number:
- `estimate: true`;
- `basis`: `measured-here`, `reference` or `formula`;
- `formula`: a human string with the numbers substituted;
- `range: [lo, hi]`.

### 5.1 Units

- Memory is **GiB** (2^30). That matches `_machine_ram_gb`, `footprint(1)`
  and `MLX_SOFT_GB`.
- Download sizes stay **decimal GB**, the onboarding convention, because that
  is what HF and the disk show.
- Bandwidth is decimal GB/s, so the decode formula uses decimal bytes.
- The catalog stores exact `weights_bytes` and both forms derive from it. A
  27B is 16.05 GB to download and 14.95 GiB of weights. Mixing the two is the
  "16 GB download needs 16 GB of RAM" bug in a new form.

### 5.2 Resident memory per lane

```
resident_gib(model, lane) =
    W                                   # weights_bytes / 2^30
  + D            (primary lane only)    # MTP drafter, drafter_bytes / 2^30
  + ctx_turn × kvb / 2^30               # KV for the in-flight turn
  + N_apc(ram, lane) × (S × kvb + gdn) / 2^30   # exact-prefix cache snapshots
  + 0.5 + 0.03 × W                      # runtime / activations / Metal overhead

kvb = 2 × full_attention_layers × kv_heads × head_dim × 2 B   (bf16 K and V)
gdn = linear_layers × linear_v_heads × k_head_dim × v_head_dim × 2 B   (fixed per sequence)
N_apc: primary 6/4/3/2 at RAM >=64/>=48/>=36/else; bg 4/3/2/2   (mirrors mlx-server*.sh)
S = Hermes cold-prefix tokens (5.4); ctx_turn from the answers: 25k (chat/agent), 40k (code), 131,072 (docs)
```

Notes:
- `kvb` is computed from each model's own `config.json` and asserted in tests.
  27B: 2×16×4×256×2 = 64 KiB/token, matching the project's measured "~64 KB
  per token" for APC.
- The overhead term is calibrated: 27B + MTP + 6 entries at S=20.5k gives
  26.2 GiB against the measured "~26 GB".
- The background 9B estimates 9.6 GiB against CLAUDE.md's older "~7 GB". The
  estimate is conservative; D10 asks to re-measure.

**Plan verdict.** `total = resident(primary) + resident(bg)`.
- `fits` if `total ≤ 0.85 × limit`.
- `tight` if `total ≤ limit`.
- `no` otherwise.

This is a PLAN-level budget, the principle `_onb_tier` already documents.
`_model_fit` is untouched and its per-model badge is still shown next to the
plan. The two answer different questions.

### 5.3 Decode tok/s

```
bytes_tok = active_bytes_per_token (MoE) or weights_bytes (dense), decimal GB
t0_ms     = bytes_tok / (k × e_chip × BW) × 1000 / mtp_mult(uses)
slope_ms  = ctx_slope_ref (measured) or 0.48 × (kvb / 32 KiB) / mtp_mult      # ms per output token per 1k ctx tokens, at 614 GB/s
            × (614 / BW) / e_chip                                             # scaled to this chip (low confidence)
decode    = 1000 / (t0_ms + slope_ms × ctx / 1000)
```

| Constant | Value | Source / confidence |
|---|---|---|
| `k` dense 27B | 0.80 | 30.6 tok/s × 16.05 GB / 614 GB/s (CLAUDE.md, measured AR). Medium |
| `k` dense 9B | 0.857 | ~88 tok/s × 5.98 / 614 (CLAUDE.md; logs <2k median 93). Medium |
| `k` dense ≤4B | 0.65 | **assumption**: fixed per-token overhead dominates small models. Low; calibrate on AC (C5) |
| `k` MoE (on active bytes) | 0.32 | community M5 Max 30-35B-A3B MLX 110-126 tok/s. Low |
| MTP multiplier, 27B, block 3 | chat 1.56 · code 2.06 · agent 1.74 | 30.6→47.8 prose, →63 code (CLAUDE.md); agent from logs <2k (53.2/30.6). Single-stream only (two streams are a loss; F02 would serialise) |
| ctx slope 27B | 0.44 ms/1k | logs: 53.2 → 36.7 → 34.1 tok/s across buckets |
| ctx slope 9B | 0.48 ms/1k | logs: 93 → 82 → 42 → 32 tok/s across buckets |
| `e_chip` | table 3.3 | llama.cpp ratios: a different engine, so this is scaling, not truth |

The MTP multiplier applies **only** where the entry has a drafter whose
multiplier was measured. A drafter that exists but is unmeasured (the 35B-A3B
MTP-bf16) gets 1.0 and a note.

**Measured here overrides the estimate.** `_adv_measured(model_id)` reads
`_cx_rows(_CX_LOG)` and `_cx_rows(_CX_LOG_BG)` by name (file reads, mtime
cached, the parser aux_context already ships). It keeps rows with
`generated_tokens ≥ 64` and takes medians by prompt bucket (<4k = short,
16-32k = turn) when n ≥ 5. Cold prefill rows are those with `new_tokens ≥
4000`. With data present the UI says "measured on this Mac: 41 tok/s (median
of 23 replies)" and the ranking uses it. Rows reflect real contention between
the two lanes; the label says so.

### 5.4 Cold prefill of Hermes's setup prompt

```
S        = last cold prompt_tokens for this model in the log (measured), else
           20,600 (Full profile) / 18,500 (Balanced, the new-install default) / profile estimate
prefill  = prefill_ref_tps (measured on the reference) or 19,000 / active_params_B × (0.30 if MoE)
cold_s   = S / (prefill × p_index)
```

| Constant | Value | Source |
|---|---|---|
| 27B prefill on reference | 750 tok/s | `aux_promptbudget._PB_PREFILL_TOK_S` (logs 735-810), so the advisor and the Prompt-budget card quote one number |
| 9B prefill on reference | 1,970 tok/s | logs median 1,969, n=152 |
| `K` (dense default) | 19,000 B·tok/s | between 27B (20.2k) and 9B (17.7k). Medium |
| MoE prefill efficiency | 0.30 | community M5 Max A3B ~1,250-2,800 tok/s across 32k-1k. Low |

The plan note fires when `cold_s > 45`: "After the model sleeps, its first
reply re-reads Hermes's ~18.5K-token setup: ~N s here. Keep it awake longer
(idle-suspend) or use the Focused prompt profile." The advisor never changes
either setting.

### 5.5 Ranges

| Basis | Range |
|---|---|
| measured-here | ±10% |
| reference chip, measured constant | ±15% |
| chip from a measured llama.cpp row | ±25% |
| scaled row or low-confidence constant (MoE, ≤4B, M5 8c/10c, Ultra 64c) | ±40% |

### 5.6 Battery and thermals (notes, not numbers)

- **Battery cost** is ordinal, from memory traffic per generated token. That is
  a proxy, not watts.

  | Bytes per token | Label | Example |
  |---|---|---|
  | ≤ 2 GB | light | the A3B MoE, 1.7 GB |
  | ≤ 7 GB | moderate | the 9B, 6 GB |
  | more | heavy | the 27B, 16 GB |

  An always-on background lane adds one step.
- **On battery:** "replies run the GPU hard. Idle-suspend frees the model's
  memory after N min. Low Power Mode is on: expect slower replies (not
  measured)."
- **Fanless Air:** "no fan: long runs (briefings, multi-step agent tasks) slow
  down once it is warm. Reports say 10-25% after 5-30 minutes (not measured
  here)." For the Air, decode for runs longer than 5 min gets a 0.85 factor,
  labelled.
- **`thermal.limited`:** "this Mac reports a CPU speed limit right now (80%)".

### 5.7 Download and disk

- `download_gb(plan)` = Σ `size_gb + draft_size_gb` over models not complete
  (`_model_downloaded` and `_draft_ready`, i.e. the C70 definition).
- `disk_ok` = `free − need ≥ DISK_HEADROOM_GB` (5). This is `_disk_short`'s
  rule. It **fails open** when either side is unknown, the same as the menu.
- A plan that fails `disk_ok` is still shown, with "free up N GB first" and the
  Download button disabled. `download_model()` still enforces the rule
  server-side.

### 5.8 Worked estimates

Primary lane; turn 25k; S = 18,500; plan limit from §3.5. Each cell:
resident GiB and verdict · decode short / 25k-turn tok/s · cold prefill.

| Machine | Qwen3.8-27B | Qwen3.6-35B-A3B | Qwen3.5-9B | Qwen3.5-4B | Qwen3.5-2B |
|---|---|---|---|---|---|
| Air M1, 8 GB | 20.6 no · 6/4 · 673 s | 21.3 no · 14/7 · 266 s | 8.2 no · 10/5 · 256 s | 5.4 no · 14/6 · 106 s | 2.9 fits · 26/13 · 53 s |
| Air M2, 16 GB | 20.6 no · 9/6 · 442 s | 21.3 no · 21/11 · 175 s | 8.2 fits* · 15/8 · 168 s | 5.4 fits · 22/9 · 70 s | 2.9 fits · 40/21 · 35 s |
| Air M4, 24 GB | 20.6 no · 10/7 · 359 s | 21.3 no · 23/13 · 142 s | 8.2 fits · 17/9 · 137 s | 5.4 fits · 25/10 · 57 s | 2.9 fits · 44/23 · 28 s |
| MBP M1 Max 32c, 32 GB | 20.6 tight · 25/17 · 150 s | 21.3 no · 59/32 · 59 s | 8.2 fits · 43/22 · 57 s | 5.4 fits · 63/26 · 24 s | 2.9 fits · 112/57 · 12 s |
| MBP M3 Pro 18c, 36 GB | 21.8 tight · 13/8 · 232 s | 21.7 tight · 29/16 · 92 s | 8.8 fits · 22/11 · 89 s | 6.0 fits · 31/13 · 37 s | 3.1 fits · 56/29 · 18 s |
| MBP M4 Max 32c, 36 GB | 21.8 tight · 29/19 · 111 s | 21.7 tight · 67/37 · 44 s | 8.8 fits · 49/25 · 42 s | 6.0 fits · 72/30 · 18 s | 3.1 fits · 128/66 · 9 s |
| mini M4 Pro 20c, 48 GB | 23.0 fits · 21/14 · 181 s | 22.1 fits · 49/27 · 71 s | 9.4 fits · 36/18 · 69 s | 6.5 fits · 52/22 · 29 s | 3.3 fits · 93/48 · 14 s |
| **MBP M5 Max 40c, 64 GB** | **25.4 fits · 49/32 · 25 s** | 22.9 fits · 115/63 · 10 s | 10.5 fits · 84/43 · 9 s | 7.7 fits · 123/51 · 4 s | 3.8 fits · 219/113 · 2 s |
| Studio M2 Max 38c, 96 GB | 25.4 fits · 27/18 · 118 s | 22.9 fits · 63/35 · 47 s | 10.5 fits · 46/24 · 45 s | 7.7 fits · 68/28 · 19 s | 3.8 fits · 120/62 · 9 s |
| MBP M4 Max 40c, 128 GB | 25.4 fits · 34/22 · 90 s | 22.9 fits · 80/44 · 35 s | 10.5 fits · 58/30 · 34 s | 7.7 fits · 85/35 · 14 s | 3.8 fits · 152/78 · 7 s |
| Studio M2 Ultra 76c, 192 GB | 25.4 fits · 39/26 · 64 s | 22.9 fits · 90/49 · 25 s | 10.5 fits · 66/34 · 24 s | 7.7 fits · 96/40 · 10 s | 3.8 fits · 172/88 · 5 s |

*0.82 of the limit, so it rounds to fits for one model; with the background
lane it does not fit.

Background-lane residents (25k turn): 9B 8.2-9.4 GiB, 4B 5.4-6.5, 2B 2.9-3.3,
by RAM tier.

---

## 6. (d) The catalog

### 6.1 Two layers, no duplicated truth

1. **Roster shape.** These are the existing fields, so anything the advisor
   can add behaves exactly like a seeded entry: `id`, `label`, `ram` (the
   author's resident figure, still what `_model_fit` judges), `ctx`, `note`,
   `thinking`, `template_args`, `backend`, `draft_model`/`draft_subfolder`,
   `draft_kind`, `draft_block_size`, `role`, `allow_patterns`/
   `ignore_patterns`, `hf_offline`, and the download sizes `size_gb`/
   `draft_size_gb`. New optional fields: `uncensored: true` and `license`.
   - For the 4 onboarding entries and the 3 seeds the shape stays where it is
     (`_ONB_CATALOG`, `_SEED_MODELS`).
   - **New** curated ids carry their full shape in `_ADV_EXTRA` (in
     `aux_advisor.py`). server.py's `_curated_model_ids()`, `add_model()` and
     `_catalog_dl_gb()` consult `_ADV_BY_ID` at call time, three small edits
     (C2).
2. **Advisor facts.** `_ADV_FACTS[id]` in `aux_advisor_hw.py`, one record per
   curated id. A test asserts that every id in `_SEED_MODELS ∪ _ONB_CATALOG ∪
   _ADV_EXTRA` has facts, and that the overlapping fields (`ctx`, `size_gb`)
   agree.

Exact data each facts record needs:

```
weights_bytes            int, sum of *.safetensors from https://huggingface.co/api/models/<id>?blobs=true
drafter_bytes            int | 0
verified_at              "YYYY-MM-DD" (date the sizes/config were read by hand)
arch                     "dense" | "moe"
params_b, active_params_b
active_bytes_per_token   MoE only, decimal bytes, with `active_formula` string
full_attention_layers, kv_heads, head_dim          -> kv_bytes_per_token (computed, asserted)
linear_layers, linear_v_heads, linear_k_dim, linear_v_dim -> gdn_state_bytes (computed)
ctx_native               int
vision                   true | false | null (unverified)
mtp                      {kind, block, mult:{chat,code,agent}, measured:bool} | null
k_decode                 float + k_source
ctx_slope_ms_per_1k      float | null (+ source)
prefill_ref_tps          float | null (+ source)
measured_ref             {chip:"M5 Max 40c", macos, mlx_vlm:"0.6.14", decode_short, decode_20k,
                          decode_28k, prefill_tps, n, dates, source} | null
verified                 {drill:"6/6", where:"reference"|"here", date} | null   (overridden by _pro_read())
quality                  {chat, code, agent, docs, vision} ordinal 0-10 + quality_note
uncensored               bool
license, sources[]
```

`quality` is an **ordinal judgement**, not a benchmark. It comes from size,
dense vs MoE, model generation, and the drill evidence. The UI never shows
these numbers, only their consequences ("better for coding"). The owner
reviews the table (D14).

### 6.2 The entries

| id | role | arch | weights / download | drafter | kv/token | GDN state | vision | MTP | verified | quality chat/code/agent | status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `mlx-community/Qwen3.8-27B-4bit` | primary (default) | dense 27B, 64 layers (16 full) | 16,054,546,159 B (14.95 GiB) / 16.1 GB | `Qwen3.8-27B-MTP-bf16` 0.87 GB | 64 KiB | 48×48×128×128×2 ≈ 75 MB | config has `vision_config`; weights unverified | block 3: 1.56/2.06/1.74 measured | drill 6/6 (reference) | 9/10/10 | shipping |
| `orcarouter/Qwen3.8-27B-Uncensored-MLX` | opt-in alternative | same | root 4-bit + `mtp/` ≈ 17 GB | in-repo `mtp/` | 64 KiB | 75 MB | as above | assumed same as the 27B, **unmeasured** | not drilled | 9/9/9 | shipping, uncensored |
| `mlx-community/Qwen3.5-9B-4bit` | background lane | dense 9B, 32 layers (8 full) | 5.98 GB | — | 32 KiB | 24×32×128×128×2 ≈ 25 MB | null | — | "9B tool suite 6/6" (reference) | 7/6/7 | shipping |
| `mlx-community/Qwen3.5-4B-4bit` | small primary / background | dense 4B, 32 layers (8 full) | 3.06 GB | — | 32 KiB | 25 MB | null | — | none | 5/4/4 | catalog |
| `mlx-community/Qwen3.5-2B-4bit` | tiny / background | dense 2B, 24 layers (6 full, 2 KV heads) | 1.75 GB | — | 12 KiB | 18×16×128×128×2 ≈ 9 MB | null | — | none | 3/2/2 | catalog |
| **`mlx-community/Qwen3.6-35B-A3B-4bit`** | **new: fast alternative** | MoE 35B / ~2.9B active: 40 layers (10 full, 2 KV heads), 256 experts, 8 + 1 shared, expert FFN 512 | 20,429,169,263 B / 20.4 GB | `Qwen3.6-35B-A3B-MTP-bf16` (1.71 GB) exists, **not enabled** until measured on 0.6.14 | 20 KiB | 30×32×128×128×2 ≈ 31 MB | true (`image-text-to-text`) | — | none (drill first) | 8/8/8 | candidate (D3) |
| **`Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit`** | **new: uncensored MoE, opt-in** | same, experts 6-bit | 24,624,965,341 B / 24.6 GB | — | 20 KiB | 31 MB | separate `model-vision` file (0.89 GB) | — | none | 7/7/7 | candidate (D4). Alternative: `froggeric/…-Heretic-MLX-4bit` 20,422,478,158 B, same layout as the stock build |

Per-model inputs and their sources:

- **Uncensored 27B entries** share the 27B's architecture facts.
- **Active bytes per token for the MoE** are computed from
  `Qwen/Qwen3.6-35B-A3B` `config.json`:
  - experts: 9 × 3 × 2048 × 512 × 40 = 1.13B;
  - full attention: ≈ 0.27B;
  - GatedDeltaNet projections: ≈ 1.0B;
  - `lm_head`: 0.51B;
  - total ≈ 2.93B parameters × 4.5 bits = **1.65 GB per token**. With the
    Heretic build's 6-bit experts it is ≈ 1.94 GB.
- **KV and state per token**, from each model's official `config.json`
  (`full_attention_interval` 4):
  - 27B: 64 layers, 4 KV heads, head_dim 256, 48 linear value heads;
  - 9B and 4B: 32 layers, 4 KV heads, 32 linear value heads;
  - 2B: 24 layers, 2 KV heads, 16 linear value heads.
- **`qwen3_5_moe` loads on mlx-vlm 0.6.14**, per the 2026-10-26 check in
  `docs/plans/abliterated-models-2026-10.md`.
- **Both MoE builds use the `TokenizersBackend` tokenizer**, so `backend:
  "mlx_vlm"`, exactly like the 9B.

Roster shape for the new stock MoE (`_ADV_EXTRA`):

```python
{"id": "mlx-community/Qwen3.6-35B-A3B-4bit", "label": "Qwen3.6-35B-A3B",
 "ram": 23, "ctx": 262144, "note": "fast MoE · 3B active · Apr-2026 · unverified until drilled",
 "thinking": True, "template_args": {"enable_thinking": False}, "backend": "mlx_vlm",
 "size_gb": 20.4, "license": "apache-2.0"}
```

The Heretic entry is the same shape plus `uncensored: True`, `size_gb: 24.6`,
and `ignore_patterns` restricted to the safetensors, tokenizer, config and
processor files. Neither entry has a drafter or `trust_remote_code`.

**Considered and not offered** (stated in the doc, absent from the UI):

| Candidate | Why not |
|---|---|
| PocketAiHub 27B abliterated | needs a `model_subfolder` field that does not exist yet |
| Jiunsong SuperQwen 27B | ships `repro/*.py`; benchmarked on the forbidden 0.6.16 |
| vanch007 9B abliterated | no licence, anonymous converter |
| `mlx-community/Qwen3.8-27B-OBLITERATED-OptiQ-4bit` | imports `mlx-optiq` at load, conflicts with C130/F01 |
| DeepSeek-V4.1-Flash, Qwen3.8-Flash-Next 125B | larger than any plan limit under the current ceilings |
| 8-bit 27B builds | twice the bytes per token for a small quality gain; can be added later for ≥96 GB Macs if D8 changes |

---

## 7. (e) The ranking

### 7.1 Candidates and pairs

- The primary is any catalog model. An uncensored model is included only when
  `uncensored ∈ uses`.
- The background lane is ∈ {none, 9B, 4B, 2B}. It must be strictly smaller than
  the primary.
- There is no background lane below 24 GB RAM. That rule is inherited from
  `_onb_tier`.
- A plan with a background lane must be **fits**, not tight.

### 7.2 Exclusions (feed the "won't fit / too slow" list)

| Kind | Rule | Reason string (template) |
|---|---|---|
| chip | `supported == false` | "Local models need Apple silicon; this Mac has an Intel chip." |
| memory | `resident(primary) > limit` | "Needs ~22 GB with its cache; this Mac's budget is ~21 GB (GPU working set 21.1 GB)." |
| slow | decode at turn < 6 tok/s, or cold prefill > 180 s | "Too slow here: ~8 tok/s in a typical 25K-token turn and ~4 min to re-read the setup after each wake." |
| small | `q[use] < floor` for `agent` (4) or `code` (6) | "Too small for tool use / coding." |
| vision | `vision` required and the model's `vision == false` | "No image support." |

### 7.3 Score

```
Q  = Σ_u q[u]·w_u / Σ w_u        (w: chat 1, agent 1.5, code 1.5, docs 1, vision 1)
pen= 0 (≤20 s) | 1 (≤45) | 2 (≤90) | 4 (≤150) | 6 (>150)  ; halved when plugged in
V  = 10 · clamp(log2(decode_turn/8)/3, 0, 1) − pen            # 8 tok/s -> 0, 64 tok/s -> 10
B  = −3·(bytes_tok/16 GB) − (bg weights GB / 6) − total/limit # battery only
bg = 0.8 · q_bg.chat/7 (halved if the lane model is unverified) if briefings or news are on
score = w_q·Q + w_s·V + w_b·B + bg − (1.0 if tight)
```

### 7.4 The Recommended slot and alternatives

- **Recommended** is the best **verified, non-uncensored** plan whose verdict
  is fits.
  - "Verified" means the catalog's reference drill or a local `_pro_read()`
    pass at ≥5 of 6. A local fail removes verification.
  - If no verified plan fits, the best non-uncensored plan is recommended,
    badged "not yet verified for tools: run the drill (Settings › Agent &
    Models › Evals)". An uncensored model never takes this slot (D4).
- **Alternatives** are up to two more plans with **different primaries**,
  labelled by what they trade, for example "Faster" or "Lighter on battery".
  An uncensored alternative carries an Uncensored badge and the trade-off
  line.
- **Relaxation.** For each use case in the order `docs` → `code` → `agent`: if
  no plan with decode ≥ 8 tok/s and verdict fits survives, that use case is
  dropped and stated, for example "Long documents (128K tokens) would run
  below 8 tok/s here; sized for normal turns instead" or "Tool use needs a
  bigger model than this Mac can hold; Hermes runs chat-first here". A wish
  the Mac cannot meet is said out loud rather than silently dragging the plan
  down.
- **Current setup.** The plan that is running now (active model + `bg_model()`)
  is always evaluated and shown first as "What you run now", with its own
  verdict. It is never hidden by the recommendation.

### 7.5 The "why" line

The "why" line is a deterministic template, filled in priority order. The
first match supplies the opening clause; at most two more clauses follow:

1. `tight` → "Loads, but uses N of M GB: expect swapping under heavy use."
2. A verified primary with a background lane → "Fits with room to spare (N of
   M GB): ~X tok/s, first reply after wake ~Y s; the 9B lane keeps briefings
   off the assistant."
3. Relaxed → the relaxation sentence.
4. An unverified primary → "… not yet verified for tools."

Examples:
- This Mac: "Fits with room to spare (36 of 48 GB): ~32 tok/s in a typical
  turn, first reply after wake ~25 s; the 9B lane keeps briefings off the
  assistant."
- M3 Pro 36 GB: "The fastest verified brain here: ~11 tok/s in a typical turn.
  The 27B would load but needs ~4 min to re-read its setup after each wake."

### 7.6 Expected output of these constants

Default answers (chat + agent, Balanced, plugged in, briefings on). Produced by
the design-time reference implementation in that session's scratch folder
(`adv_ref.py` for the chip table, `adv_final.py` for the §5.8 estimates,
`adv_rank2.py` for the plans). Those scripts are session scratch and may be
gone by round 3, so C1 re-derives the golden fixture from §3.3, §5 and §7.3.
Its decode and prefill constants match §5.8; the plans come from
`adv_rank2.py` with S = 18,000 and `k` 0.80 for the uncensored 27B. Bracket
numbers are (plan total of limit, GB · decode at 25k · cold prefill).

| Machine | `_onb_tier` today | **Advisor: Recommended** | Alternatives | Won't fit / too slow |
|---|---|---|---|---|
| Air M1 8 GB | 2B | 2B, chat-first (3/4.5 · 13 tok/s · 52 s), unverified | — | 27B, 35B, 9B, 4B: memory |
| Air M2 16 GB | 4B | 4B (6/10 · 9 · 68 s), unverified | 9B solo, tight (8 · 164 s) | 27B, 35B: memory; 2B: too small for tools |
| Air M4 24 GB | 9B + 2B | 9B + 2B (12/15 · 9 · 133 s) + idle-suspend note | 4B + 2B (10 · 55 s, unverified) | 27B, 35B: memory |
| MBP M1 Max 32 GB | 9B + 2B | 9B + 4B (14/21 · 22 · 56 s) | 4B + 2B | 27B: memory (21.8 > 21.0); 35B: memory |
| MBP M3 Pro 36 GB | **27B tight + 9B** | **9B + 4B** (15/24 · 11 · 86 s) | 35B-A3B solo, tight (16 · 89 s); 4B + 2B | **27B: too slow (8 tok/s, 226 s)** |
| MBP M4 Max 32c 36 GB | **27B tight + 9B** | **9B + 4B** (15/24 · 25 · 41 s) | 35B-A3B solo, tight (37 · 43 s); 4B + 2B | — (27B ranked out: tight, 111 s) |
| mini M4 Pro 48 GB | **27B + 9B** | **9B + 4B** (16/33 · 18 · 67 s) | 35B-A3B + 2B (27 · 69 s, unverified); 27B + 2B (14 · 176 s) | — |
| **MBP M5 Max 64 GB** | 27B + 9B | **27B + 9B** (36/48 · 32 · 24 s): today's roster | 35B-A3B + 9B (63 · 10 s, unverified); 9B + 4B (43 · 9 s) | — |
| Studio M2 Max 96 GB | 27B + 9B | 27B + 9B (36/48 · 18 · 115 s) + idle-suspend note | 35B-A3B + 9B (35 · 45 s); 9B + 4B | — |
| MBP M4 Max 128 GB | 27B + 9B | 27B + 9B (36/48 · 22 · 87 s) | 35B-A3B + 9B (44 · 34 s); 9B + 4B | — |
| Studio M2 Ultra 192 GB | 27B + 9B | 27B + 9B (36/48 · 26 · 62 s) | 35B-A3B + 9B (49 · 25 s); 9B + 4B | — |

The Mac mini M4 Pro row comes out differently in the two tables. In §5.8 its
27B cold prefill is 181 s, just past the 180 s "too slow" cut, so it would be
excluded. In this table, with S = 18,000, it is 176 s and stays in as an
alternative. The cut sits right on top of this machine.

Variants on this Mac:

| Answers | Recommended | Alternatives |
|---|---|---|
| Faster | 9B + 4B (43 tok/s, 9 s) | 35B-A3B + 9B (63, 10 s, unverified); 27B + 9B |
| Often on battery | 9B + 4B | 35B-A3B + 9B; 27B + 9B |
| + Coding, Best answers | 27B + 9B (28 tok/s at a 40k turn) | — |
| + Long documents | 27B + 4B (40/48 GB, 13 tok/s at 128K) + "raise the context window" note | — |
| + Unfiltered research | 27B + 9B (unchanged) | + "Qwen3.6-35B-A3B Heretic + 9B: Uncensored, ~58 tok/s, unverified" |

Borderline cases the owner should look at (they come from the verified-first
rule, D2):
- Air M4 24 GB: the verified 9B has a 2.4x slower first reply than the
  unverified 4B.
- M4 Pro 48 GB: the 27B sits right at the 180 s "too slow" cut (see the note
  after the table above).

Drilling the 2B, the 4B and the MoE on AC (C5) changes both.

---

## 8. (f) UI

All UI is in a new `dashboard/aux_advisor.js`, which exports
`window.hermesAdvisor`. One `<script>` tag goes in index.html after
`aux_evals.js`. It follows the design laws: zero emoji; bespoke two-tone SVG;
`esc()` on every interpolation; every global typeof-guarded so a headless
harness can eval the file; explicit `transition-property`; ≥40 px targets;
reduced motion respected; theme tokens inherited from index.html, never
re-declared; 12-hour times.

### 8.1 Onboarding: step 2 becomes "Your Mac, and the brain that fits it"

The same step key `models`, so the sheet stays at four steps, or five with the
GitHub step. When `hermesAdvisor.step` exists, `aux_onboarding.js` delegates
the body to it (C4). Otherwise the current body renders unchanged. Layout, top
to bottom, single column under 760 px and two columns above:

1. **Detected.** Quiet facts, the existing `.onb-stats` look: Chip "Apple M5
   Max · 40-core GPU" · Memory "64 GB" · GPU budget "51.8 GB" · Bandwidth
   "614 GB/s" · Free disk · Power "On battery, 41%" · Form "MacBook Pro". An
   "assumed" source shows as a muted "(assumed)" with a `title` explaining it.
2. **"What will you use it for?"**
   - Chips are real checkboxes in a `role="group"`; Space toggles.
   - Q2 is a radio segmented control; arrow keys move.
   - Q3 appears only on a laptop.
   - Each change calls `GET /api/advisor/rank?…` (debounced 150 ms, pure, no
     writes) and re-renders block 3 in place.
3. **Recommended.**
   - The plan heading reads "Qwen3.8-27B as the assistant, Qwen3.5-9B for
     background work", followed by the "why" line.
   - Two lane rows show: resident, decode short/turn, first reply after wake,
     download size, and a "verified for tools" or "not yet verified" tag.
   - The `_model_fit` badge (unchanged phrasing) sits next to each lane.
   - Then the total line: "~17 GB to download · 412 GB free".
   - A "How we estimate" disclosure lists each formula with the numbers
     substituted, plus the basis and range.
4. **Alternatives.** Up to two flat rows (no cards inside cards). Each has a
   radio "Use this instead", which sets `S.pick`.
5. **Won't fit / too slow.** Collapsed: "4 models won't work well here", which
   expands to the reasons.
6. **Notes.** Relaxations, prefill/idle advice, battery and Air notes.

**Next does exactly what it does today:**
- It posts the answers (`POST /api/advisor/answers`).
- It hands `{primary, background}` to the existing apply path.
- Download only when the owner presses the existing "Download recommended"
  button, which now says "Download recommended (~17 GB)".
- No switch. Apply's C63 rule (activate only while the server is down) is
  untouched.

### 8.2 Settings › Agent & Models: "Model advisor" card

- Mounted directly into `#sec-models` with `order:-1`, so it is the first card
  in the panel. It never falls back to `#view-mind`.
- It renders the same component as the step, plus "What you run now" at the
  top, with the verdict and "Last analysed 3:04 PM · Re-check".
- Re-check busts the dynamic cache; the static cache stays.

Buttons per plan. Each is an explicit click, and none fires on load or poll:

| Button | Calls | Guard |
|---|---|---|
| Add to model menu | `POST /api/models/add {id}` (curated, so no `confirm` body needed) | — |
| Download (~X GB · Y GB free) | `/api/models/add`, then `/api/models/download` | `confirm()` (WKWebView implements it); disabled with a reason when `disk_ok` is false; the server re-checks |
| Use as background lane | `POST /api/onboarding/apply {background:id}`, which writes `bg-model` (the only writer) | "takes effect the next time the background lane starts" |
| Make it the assistant | if downloaded: the existing model-menu switch path (`/api/models/switch` + its existing `confirm`) | otherwise disabled: "download it first" |

### 8.3 Model menu: "why this fits"

- `aux_advisor.js` wraps the global `modelDetailsHTML` (`index.html:2841`),
  guarded (`typeof … === "function" && !fn.__adv`). Using the expand.js
  "loaded later wins" pattern means no index.html markup changes.
- When the row carries `advice` (added by the `models_payload` rebind, §9), it
  appends four `<dt>/<dd>` pairs inside the existing `<dl>`:
  - "Speed here" — "~49 tok/s short · ~32 in a 25K turn (estimate)", or
    "41 tok/s measured here (23 replies)".
  - "First reply after wake" — "~27 s to read the 20.5K-token setup".
  - "Memory with cache" — "~26 GB of a 48 GB budget".
  - "Advisor" — "recommended for you", "fits with the 9B lane", or "too slow
    here: ~4 min first read".
- Each value gets a `title` carrying the formula.
- The panel stays a sibling of `.mmi`, so the row height is unchanged.
- **The advisor verdict is advisory.** `onModelClick` keeps refusing only
  `fit === "no"` and disk-short (D7).

### 8.4 What never happens

- No auto-switch, no auto-download, no auto-activation.
- No restart of either lane.
- No write to `model.context_length`, idle-suspend or the prompt profile.
  The advisor only suggests these.
- No probe of :8080/:8081/:9119.
- No new egress.

---

## 9. API and integration points

New aux files exec in sorted order: `aux_advisor.py` runs before
`aux_advisor_hw.py`, which runs before `aux_agent.py` and `aux_onboarding.py`.
So every cross-module name, in both directions, is resolved at **call time**
through a `_adv_g(name, default)` helper, the `_onb_g` discipline.

| Route | Shape | Side effects |
|---|---|---|
| `GET /api/advisor/state` | `{ok, version, hw{chip{brand,family,variant,gen,gpu_cores,cores_source,bandwidth_gbs,e_chip,p_index,row,confidence},ram_gb,cpu{levels[]},macos,model_id,machine_name,form,power{source,pct,low_power},thermal{limited,text},disk_free_gb,supported,unsupported_reason}, limits{gpu_budget_gb,gpu_budget_source,soft_gb,hard_gb,reserve_gb,limit_gb,binding}, prefix{tokens,source,profile}, answers{…,saved_at}, current{plan}, result{recommended,alternatives[],wont[{id,label,kind,reason}],notes[],relaxed[]}, models{id: estimate}, formulas{…}, constants{…}}` | none (reads files and runs the cached detection) |
| `GET /api/advisor/rank?uses=chat,agent&priority=balanced&power=ac&unc=0` | `{ok, result}` for unsaved answers | none |
| `POST /api/advisor/answers` | `{uses[],priority,power,uncensored_ack}` → `{ok, answers}` or `400 {ok:false, errors[]}` | `settings_update` on key `advisor` only |
| `models_payload()` rebind (guarded `_adv_wrapped`) | adds `advice{resident_gb,decode_short_tps,decode_turn_tps,prefill_s,verdict_solo,basis,why}` per row, plus top-level `advisor{limit_gb,gpu_budget_gb,current{total_gb,verdict}}` | none. Must never raise; a hw cache miss computes ≤0.3 s once |

Estimate object:

```
{resident_gb, decode_short_tps, decode_turn_tps, turn_ctx, prefill_s, prefix_tokens,
 battery:"light"|"moderate"|"heavy", estimate:true,
 basis{decode,prefill,resident}, range{decode:[lo,hi],prefill:[lo,hi]},
 measured{decode_short,decode_turn,prefill_s,n,since}|null,
 formula{decode,prefill,resident}}
```

Plan object:

```
{primary{id,label}, background{id,label}|null, verdict, total_gb, limit_gb, score,
 why, notes[], download_gb, disk_ok, downloaded, verified, uncensored,
 estimates{primary, background}}
```

Onboarding compatibility:
- `_adv_recommend_compat(ram_gb, downloaded)` returns the **exact key set of
  `_onb_recommend`** (`tier` = `"advisor"`, `primary`, `background`, `tight`,
  `alt`, `why`, `note`, `download_gb`, `total_gb`, `already_downloaded`,
  `fits`) plus `advisor{…}`.
- So `aux_onboarding.py` can swap in one line (`_onb_g("_adv_recommend_compat")
  or` the old path) and the legacy JS keeps working before the new JS lands.

---

## 10. (g) Tests

All unit tests run in `tools/tests/run.sh unit`. They run offline: the
usercustomize guard makes the dashboard, model and serve ports unreachable,
which **enforces** no-wake. Each test prints the `TESTS n passed n failed`
summary line.

**`tools/tests/unit/t_advisor_hw.py`** (C1):
- Brand parser: every chip name in table 3.3, plus `Intel(R) Core(TM) i9…`,
  `Apple A18 Pro`, `Apple M6 Max`, an empty string and garbage.
- ioreg text with and without `gpu-core-count`. `system_profiler` JSON with
  `sppci_cores` as a string. `pmset -g batt` for AC, battery and desktop (no
  `InternalBattery`). `pmset -g` with `lowpowermode`/`powermode`. `pmset -g
  therm` with "No … recorded" and a `CPU_Speed_Limit = 80` sample. Perflevels
  named Super/Performance.
- Chip table: derived `e_chip`/`p_index` recomputed from the raw columns for
  every row. Unknown cores → the lower binned row.
- `_adv_gpu_budget`: sysctl > 0 wins; Metal next; then the assumed fractions
  at 8/16/24/32/36/48/64/96/128/192.
- `_adv_limits` for all ten RAM sizes, with and without
  `MLX_SOFT_GB`/`MLX_HARD_GB` env (through the real `_mem_ceilings`), with the
  `binding` reported.
- `_adv_apc_entries` **parsed against `mlx-server.sh` and `mlx-server-bg.sh`**:
  a drift guard that reads the `if [ "$RAM_GB" -ge …` lines.
- The Metal probe subprocess is stubbed: success JSON, timeout, non-zero exit,
  garbage. Detection never raises; it degrades to `source:"assumed"`.

**`tools/tests/unit/t_advisor_estimate.py`** (C1):
- kv and GDN bytes per token from the config numbers for every facts entry
  (27B = 65,536; 9B/4B = 32,768; 2B = 12,288; 35B-A3B = 20,480).
- **Calibration golden** on (M5 Max 40c, 64 GB), each within the stated
  tolerance:

  | Quantity | Expected | Tolerance |
  |---|---|---|
  | 27B AR | 30.6 | ±5% |
  | 27B MTP prose | 47.8 | ±5% |
  | 27B @20k | 36.7 | ±12% |
  | 9B short | 88 | ±10% |
  | 9B ≥32k | 32.3 | ±12% |
  | 27B cold 20.5k | 25-29 s | — |
  | 9B cold 24.2k | 12.3 ±1.5 s | — |
  | 27B resident (6 entries) | 26 ±2 GiB | — |

- The §5.8 table (11 machines × 5 models) as a golden fixture
  (`tools/tests/unit/fixtures/advisor_golden.json`), ±1%.
- Properties:
  - decode is monotone non-decreasing in bandwidth and non-increasing in
    context;
  - resident is monotone in context and APC entries;
  - MTP multiplier > 1 only where `mtp.measured`;
  - every estimate has `estimate:true`, a non-empty `formula` and
    `lo ≤ value ≤ hi`;
  - everything is JSON-serialisable;
  - no I/O inside the pure functions (subprocess and open are patched to
    raise).

**`tools/tests/unit/t_advisor_rank.py`** (C2):
- The §7.6 golden: Recommended primary and background ids for 11 machines × 6
  answer profiles (Balanced, Faster, battery, code+best, docs, uncensored).
- Properties:
  - Recommended is never uncensored and never `no`;
  - uncensored ids are absent unless `uncensored` + ack;
  - a background lane only at ≥24 GB, only when the plan fits, and smaller
    than the primary;
  - more RAM (same chip family) never yields a worse Recommended verdict;
  - relaxation notes appear exactly when the use case is unsatisfiable;
  - every `wont` entry has a non-empty reason and a `kind`;
  - `vision` required filters `vision:false`;
  - a local drill fail (`_pro_read` stub) removes "verified";
  - a local pass at ≥5/6 adds it.
- "What you run now" is always present when an active model exists.

**`tools/tests/unit/t_advisor_routes.py`** (C2). Full server.py load against a
throwaway HOME, the `t_onboarding.py` pattern:
- `/api/advisor/state` and `/rank` shape.
- `urllib.request.urlopen` is patched to raise. This proves no model or serve
  contact, and that `model_online`/`bg_online` are not on the advisor path.
  `models_payload`'s own pre-existing `bg_online` call is stubbed separately.
- The subprocess allowlist is sysctl, ioreg, pmset, system_profiler, sw_vers
  and the `-I` probe. Anything else fails the test.
- POST validation matrix; uncensored without ack → 400.
- A `settings_update` spy shows only the `advisor` key changes.
- `/rank` leaves the settings.json mtime unchanged.
- `download_model`, `switch_model`, `_activate_model` and `_onb_set_bg` are
  never called (recorders).
- The `models_payload` rebind adds `advice` and survives a broken catalog
  entry.

**`tools/tests/unit/t_advisor_catalog.py`** (C2, F01/B03 regression):
- A curated add of each `_ADV_EXTRA` id needs no `confirm`, stores the FULL
  shape (`backend: "mlx_vlm"`, `template_args`), never `trust_remote_code`,
  and the download ignore patterns still exclude pickle and `.py`.
- `_catalog_dl_gb` quotes the extras.
- A non-curated id still needs `confirm:true`.
- `_model_fit` output is unchanged for every row.

**`tools/tests/unit/t_advisor_ui.js`** (C3, node + `_fakedom.js`):
- Pure formatters (estimate label, range text, formula text, 12-hour time).
- Render the step and the card for the 16 GB and 64 GB fixtures. Checks:
  - every number carries "~" or "estimate" or "measured here";
  - "How we estimate" contains the substituted formula;
  - the uncensored chip shows the acknowledgement copy and stays off until
    confirmed;
  - Download calls `confirm()` and then fetches `/api/models/add` and
    `/api/models/download`, in order, only on click;
  - `/api/models/switch` is never fetched without a click;
  - a model label containing `<img src=x onerror=…>` renders inert;
  - the `modelDetailsHTML` wrapper adds four rows only when `advice` is
    present, keeps the original rows, is idempotent across two loads, and is
    a no-op when `modelDetailsHTML` is absent.

**`tools/tests/browser/t_advisor_ui.py`** (C3, Playwright, the
`t_onboarding_ui.py` route-fixture pattern; `/api/models/download` stubbed to
a hard failure):
- Step 2 for 16 GB, 36 GB M3 Pro and 64 GB fixtures, in both themes,
  screenshotted.
- Answers re-rank live, within ≤1 debounce.
- The Settings card mounts into `#sec-models` as the first card.
- Model menu rows show "why this fits".
- Keyboard: chips toggle on Space, segmented control on arrow keys, Tab order
  intact, focus visible.
- `prefers-reduced-motion`.
- A narrow (≤760 px) single-column layout, with no horizontal scroll.

**`tools/tests/unit/t_onboarding.py` and the browser sheet test** (C4):
- `_onb_tier` tables are unchanged (it stays as the fallback).
- `_onb_recommend` delegates when the advisor is loaded and keeps its key set.
- The step registry: order, late registration, a `save()` rejection keeps the
  sheet on the step with the error shown.
- A first run is still not dismissable before the last step.

**AC tier (owner-run, never CI; C5):** `tools/tests/ac/advisor-calibration.md`.
- The procedure: `decode_bench.py --restart-with none` for the 2B, the 4B and
  the 35B-A3B, plus the aux_promotion 6-case drill and the two-stream
  `concurrency_probe.py`.
- It records results into the facts' `measured_ref`/`verified`.

---

## 11. (h) Decisions for the owner

1. **D1: Replace the RAM-only recommendation in onboarding with the advisor**
   (`_onb_tier` stays as the fallback and keeps its tests). Visible effect:
   36-48 GB Macs without an M5 get 9B + 4B instead of the 27B. Recommended:
   yes.
2. **D2: Verified-first for the Recommended slot.** Only the 27B and the 9B
   count as verified today, from the reference drill. Should the 2B, the 4B
   and the 35B-A3B be drilled on AC on this Mac, so the small and fast tiers
   can be recommended without a badge? This needs the owner, because it loads
   models. Recommended: yes.
3. **D3: Add the stock `mlx-community/Qwen3.6-35B-A3B-4bit`** (20.4 GB,
   apache-2.0) to the curated catalog as a "faster" alternative, never the
   default. Do this only after an AC drill and a two-stream probe on mlx-vlm
   0.6.14. Its MTP drafter stays off until measured.
4. **D4: Uncensored policy.**
   - (a) Uncensored builds never take the Recommended slot, only alternatives,
     behind the acknowledged chip.
   - (b) Which uncensored MoE to curate: Youssofal Heretic (24.6 GB,
     documented KL 0.0107) or froggeric Heretic (20.4 GB, more downloads, same
     layout as the stock build). Or none for now.
5. **D5: The Metal probe.** A stdlib `ctypes` subprocess using the dashboard's
   own python, versus the MLX venv's `mx.metal.device_info()`, versus fallback
   fractions only. Recommended: ctypes (no venv dependency; 0.1 s measured).
6. **D6: "Measured here"** reads the local model-server logs (file reads,
   aux_context's parser). Recommended: yes.
7. **D7: The advisor verdict stays advisory.** It never makes a model-menu
   row inert; `_model_fit` + disk-short remain the only hard gates.
   Recommended: yes.
8. **D8: Flat 50/56 GB ceilings at ≥64 GB** cap plans on 96-192 GB Macs at
   ~48 GB. Keep them (the advisor explains the cap) or let the advisor suggest
   `MLX_SOFT_GB` for big Macs? Recommended: keep.
9. **D9: `_mem_ceilings` floor (8, 10) on an 8 GB Mac** makes soft equal all
   of RAM, so admission control never engages. The advisor's own limit is
   fine. Fix the floor in a separate change?
10. **D10: CLAUDE.md drift.** "~88 tok/s" for the 9B lane is its short-prompt
    speed; the measured median at the 24-40k contexts it actually runs is
    32-42 tok/s. "bg is ~7GB" predates the 4-entry APC tier (estimate
    9.6 GiB). Update the text and re-measure the footprint?
11. **D11: Long documents.** The advisor only *suggests* raising
    `model.context_length` (65,536 today) to 131,072. It never writes it.
    Agreed?
12. **D12: Answers in `settings.json` `advisor`**, not exported by
    Config-as-Code. Agreed, or add an export rule?
13. **D13: Slow-prefill Macs** (cold > 45 s). Suggestion only, or should the
    onboarding prefs step pre-select a longer idle-suspend and the Focused
    prompt profile?
14. **D14: Review the quality ordinal table** (§6.2) and the scoring constants
    (§7.3). They are judgement, and the §7.6 goldens lock them in.

---

## 12. (i) Round-3 implementation clusters (disjoint owned files)

| # | Cluster | Owned files (nothing outside this list) | Work |
|---|---|---|---|
| **C1** | advisor-hw-estimator | NEW `dashboard/aux_advisor_hw.py`; NEW `tools/tests/unit/t_advisor_hw.py`, `tools/tests/unit/t_advisor_estimate.py`, `tools/tests/unit/fixtures/advisor_golden.json` | §3 detection (pure parsers + cached runners + Metal probe), the §3.3 chip table, `_ADV_FACTS` for every curated id, the §5 estimator with formula strings and ranges, `_adv_measured()` over `_cx_rows` (resolved by name). Exports `_adv_hw()`, `_adv_limits()`, `_adv_estimate(mid, hw, answers, prefix)`, `_adv_apc_entries()`, `_ADV_FACTS`, `_ADV_CHIPS`. No routes. |
| **C2** | advisor-rank-api | NEW `dashboard/aux_advisor.py`; `dashboard/server.py` (only `_curated_model_ids`, `add_model`, `_catalog_dl_gb` consult `globals().get("_ADV_BY_ID")`); NEW `tools/tests/unit/t_advisor_rank.py`, `t_advisor_routes.py`, `t_advisor_catalog.py` | `_ADV_EXTRA`/`_ADV_BY_ID` roster shapes (stock MoE; Heretic MoE gated by D3/D4), answers validation and storage via `settings_update`, the §7 ranking + "why" templates + relaxation + current plan, the three routes, the `models_payload` rebind (`advice`), `_adv_recommend_compat()`. Resolves C1's names at call time. |
| **C3** | advisor-ui | NEW `dashboard/aux_advisor.js`; `dashboard/index.html` (ONE `<script src="/aux_advisor.js">` after `aux_evals.js`, nothing else); NEW `tools/tests/unit/t_advisor_ui.js`, `tools/tests/browser/t_advisor_ui.py` | `window.hermesAdvisor` = `{step:{render,wire,save}, mountCard(), explain(), formatters}`; the §8.2 Settings card; the §8.3 `modelDetailsHTML` wrapper; questionnaire with the uncensored acknowledgement; explicit-click buttons over the existing routes. |
| **C4** | onboarding-seam (**shared with the GitHub-onboarding feature: the single round-3 owner of these files for both designs**) | `dashboard/aux_onboarding.py`, `dashboard/aux_onboarding.js`, `tools/tests/unit/t_onboarding.py`, `tools/tests/unit/t_c63_onboarding_primary.py`, `tools/tests/unit/t_c70_onboarding_drafter.py`, `tools/tests/unit/t_c63_onboarding_summary_js.js`, `tools/tests/browser/t_onboarding_ui.py` | (1) A generic JS step registry: `hermesOnboarding.registerStep({key,title,sub,after,render(S),wire(node,S,repaint),save(S)→Promise<{ok,error?,pick?}>,summary(S)→[lines]})`. Late registration repaints; `save` rejection keeps the step. The GitHub feature registers its step from ITS own file and adds its own routes and NETWORK_FACTS row through its own cluster. (2) Step `models` delegates to `hermesAdvisor.step` when present; the legacy body stays as the fallback. (3) `_onb_recommend` delegates to `_adv_recommend_compat` via `_onb_g`. (4) `_onb_known_model` also accepts `_ADV_BY_ID` ids. (5) Bump `ONB_VERSION`; `stepDone` summary lines come from each step's `summary()`. (6) Update the existing onboarding tests. |
| **C5** | docs-and-calibration | `CLAUDE.md`, `CHANGELOG.md`, NEW `docs/plans/model-advisor.md` (this design, promoted), NEW `tools/bench/advisor_calibrate.py`, NEW `tools/tests/ac/advisor-calibration.md` | CLAUDE.md section "Model advisor (1.4.x)" (invariants: advisory only, no-wake, units, verified-first, uncensored never recommended), the D10 corrections if approved, the CHANGELOG entry. `advisor_calibrate.py` reads the two model logs (no model) and prints measured `k`/slope/prefill per model for the facts table. The AC procedure is for the owner (decode_bench + drill + concurrency probe for the 2B/4B/35B-A3B). |

Interfaces between clusters:
- C1 → C2: the pure function names and the facts/estimate shapes in §9. C2's
  tests stub C1 by name, so the two can be built in parallel.
- C2 → C3: the JSON shapes in §9. C3's tests use fixtures.
- C2 → C4: `_adv_recommend_compat` and `_ADV_BY_ID`.
- C3 → C4: the `hermesAdvisor.step` object.
- C4 → GitHub feature: `registerStep`.

Merge order: C1 → C2 → C3 → C4 (C4's delegation is lazy, so any order is safe
at runtime); C5 last.

---

## 13. Risks

- **Off-reference accuracy.** Only this M5 Max is measured. Other chips are
  scaled by llama.cpp ratios (a different engine). The context-slope scaling
  and the MoE and ≤4B constants are low confidence. Mitigation: ranges,
  "estimate" labels, the measured-here override, the AC calibration script, and
  goldens that make any constant change deliberate.
- **The Metal probe.** Behaviour under future macOS, or with the GPU in an odd
  state, is untested beyond this Mac. It is isolated in a 2 s subprocess and
  falls back to "assumed".
  `recommendedMaxWorkingSetSize` is per-device and per-process, while the two
  lanes are separate processes. The design compares their SUM against one
  budget, which is conservative. Its interaction with
  `iogpu.wired_limit_mb` is unverified.
- **The B03/F01 lockdown surface.** C2 touches `_curated_model_ids` and
  `add_model`. `t_advisor_catalog.py` must prove extras get full shapes, no
  `trust_remote_code`, and that the ignore patterns hold.
- **Onboarding contention.** Both features need `aux_onboarding.*`. C4 must be
  ONE cluster across both designs. If the GitHub design also claims these
  files, the orchestrator merges the two work lists into one C4. index.html
  gains one `<script>` line from each feature, a trivial textual merge.
- **Poll cost.** `models_payload` is polled every 30 s. The rebind must stay
  pure, cached and exception-safe, and must add no port probes.
- **Recommendation churn.** Existing installs never see the sheet again (the
  done marker), and the card only advises, so nothing changes under anyone.
  New installs on 36-48 GB non-M5 Macs get a smaller primary than before. That
  is intended (D1).
- **The new MoE is unverified.** It is an older generation (Qwen3.6, Apr
  2026), its tool-calling is undrilled, and its MTP drafter on 0.6.14 is
  unknown. Its quality ordinal is a guess until drilled.
- **The temperature-honesty open question** (F13) does not affect the
  estimator, but "Best answers" vs "Faster" assumes the model settings do what
  they claim.
- **F02** (primary-lane serialisation) would make MTP strictly single-stream.
  That matches the estimator's assumption, and it makes queue time a new
  latency the advisor does not model.
