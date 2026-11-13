# Abliterated models — what is trending, what is good, what fits (2026-10-26)

Research for adding more opt-in uncensored (refusal-removed) models to the
curated roster. Only metadata was read: no weights were downloaded and no
model was loaded. Sources:

- A direct Hugging Face API sweep: 1,151 repos matching *abliterated /
  uncensored / heretic / obliterated*, sorted four ways, with file trees,
  `config.json` and `tokenizer_config.json` for every candidate.
- A 6-angle research sweep: 39 sources read, 6 key claims checked
  adversarially (4 confirmed, 2 refuted on dates and framing).
- A check of `mlx_vlm` 0.6.14's installed model types.

**Most numbers below are self-reported** by the model's author or vendor. No
neutral task benchmark exists for any abliterated Qwen3.8-27B MLX build. The
UGI Leaderboard's table loads dynamically and could not be read; its older
snapshots list no abliterated Qwen entries.

## What is trending (HF API, 30-day downloads, 2026-10-26)

| Rank by attention | Repo | Format | Fits 64 GB? |
|---|---|---|---|
| hottest this week | DeepSeek-V4.1-Flash-1017 abliterated/uncensored (dealignai, orcarouter, huihui, apetersson MLX) | FP8 / GGUF / MLX 343 GB | **no** |
| hottest this month | Qwen3.8-Flash-Next abliterated (SC117, huihui v2, orcarouter, DavidAU) | GGUF; mlx-community oQ5e 128 GB | **no** (125B MoE) |
| most downloaded 27B | HauhauCS/Qwen3.8-27B-Uncensored-Aggressive-MTP-GGUF (2.34M), huihui-ai/…27B-abliterated-GGUF (2.64M), 0bserverx Heretic GGUF (1.12M), DavidAU Heretic merges | GGUF (llama.cpp) | yes, but **not MLX** |
| best-documented 27B | OBLITERATUS/Qwen3.8-27B-OBLITERATED V3 (524k, ♥1,562) | BF16 + GGUF (no MLX in repo) | yes after conversion |
| top MLX 27B by adoption | orcarouter/Qwen3.8-27B-Uncensored-MLX (58k, ♥1,595) — **already in the roster** | MLX 2/4/6/8-bit + its own `mtp/` drafter | yes |
| most popular 9B | HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive (651k, ♥2,377) | BF16 + GGUF | yes after conversion |
| top overall | Qwen-Image-2.1 uncensored builds | image model, not an LLM | n/a |

## Quality evidence (all self-reported unless noted)

- **OBLITERATUS V3.** MMLU 0-shot 82.33% vs stock 84.46% (−2.1 pp). It
  claims no refusals and no "safety lecture" deflections, and thinking mode
  still works. V2 was −0.3 pp but still deflected. Its card recommends
  temperature 0, **repetition_penalty 1.15** ("critical for agents", because
  greedy decoding loops on repeated tool calls) and **no system prompt**
  ("system prompts can reintroduce refusals"). Hermes always sends an
  ~18k-token system prompt, so that last advice cannot be followed here.
- **orcarouter.** Vendor-reported on vLLM FP8: MMLU 84.3 → 84.7, GSM8K
  90.0 → 88.7, MMLU-Pro 77.6 → 76.8. The MLX builds report only weight
  cosine against BF16 (4-bit 0.996). Its own page says 30–50% of answers
  still open with a disclaimer.
- **Heretic (p-e-w's automatic abliteration, optimises KL divergence)** has
  the lowest damage figures:
  - 0bserverx Qwen3.8-27B GGUF: KL 0.0085.
  - Youssofal Qwen3.6-35B-A3B MLX: KL 0.0107.
  - Heretic's own Gemma-3-12B table: KL 0.16, against 0.45 for huihui and
    1.04 for mlabonne.
- **Independent study, the only one found.** A non-peer-reviewed arXiv draft
  (2607.17427, 2026-07-19) on huihui abliterations of Qwen3-30B-A3B and
  Gemma-4-26B-A4B. Instruction following was preserved, but behaviour
  drifted (+7.4 pp optimism). It also found that huihui's Gemma repo ships a
  stale chat template, so rendered prompts are worth checking.

## Candidates that fit this Mac and this stack (`mlx_vlm` 0.6.14)

| # | Repo | Size | Why | Caveats |
|---|---|---|---|---|
| keep | **orcarouter/Qwen3.8-27B-Uncensored-MLX** (in roster) | 16.1 GB | Only MLX build with mass adoption. Ships an MTP drafter made from its own abliterated weights, matching the current stack. Vendor numbers show almost no capability loss. | Disclaimers still appear |
| 1 | **PocketAiHub/Qwen3.8-27B-Abliterated-MLX** (`4bit/`) | 16.1 GB | Clean repo (no code or pickles), ♥133. Refusal direction from layer 53 removed across layers 24–63. Self-reported 0/100 refusals, 12/12 quality, **8/8 tool calls** at 4/6/8-bit, 33 tok/s on M5 Max. | Weights live in subfolders: the roster needs a `model_subfolder` field. No MTP. `npario/…` is a mirror; use the original. |
| 2 | **Jiunsong/SuperQwen3.8-27b-abliterated-MLX-4bit** | 16.1 GB | Best-documented 4-bit on an M5 Max. OBLITERATUS rank-4 recipe; vision and MTP weights left exact, so the stock MTP drafter is plausible. 0/32 harmful refusals, tool calls PASS. | Ships 8 `.py` files under `repro/` (excluded by the curated downloader plus `ignore_patterns: repro/*, evidence/*, assets/*`). Modified chat template. Benchmarked on mlx-vlm 0.6.16, which the pin forbids here. |
| 3 | **Youssofal/Qwen3.6-35B-A3B-Abliterated-Heretic-MLX-4bit** (or froggeric's 20.4 GB build) | 24.6 GB | A different option: a **fast uncensored MoE** with 3B active parameters, so it should decode several times faster than the 27B. Heretic KL 0.0107. Multimodal. `qwen3_5_moe` is supported by 0.6.14. Lists Hermes Agent as an integration. | Older base (Qwen3.6, Apr 2026). Tiny refusal test set (25 prompts). No MTP. |
| 4 | vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit (background lane) | 6.0 GB | Structurally identical to the current 9B lane (`qwen3_5`, the same `TokenizersBackend` tokenizer, 262k context) | No licence field, an anonymous converter, modified template, no evals. Weak. |

### Convert it yourself (better provenance than third-party MLX ports)

The two strongest non-MLX releases can be converted locally with
`mlx_vlm.convert`, in the isolated venv, on AC power, from safetensors only:

- OBLITERATUS V3: ~55 GB BF16 download, giving a ~16 GB 4-bit g64 build in
  the primary's exact format.
- HauhauCS Qwen3.5-9B Aggressive: the most popular uncensored 9B, a good
  background-lane candidate.

The roster would also need a "local build" entry type. This is a larger
feature (see round 3 below).

### Rejected

- **Too big for 64 GB:** DeepSeek-V4.1-Flash-1017, Qwen3.8-Flash-Next and
  GLM-5.3 abliterations (343 GB, 125B MoE and 320B respectively).
- **mlx-community/Qwen3.8-27B-OBLITERATED-OptiQ-4bit:** needs the
  third-party `mlx-optiq` package imported at load, which conflicts with the
  no-remote-code lockdown (C130/F01).
- **DavidAU "Heretic Fable" merges:** fine-tune merges, not pure
  abliterations, and GGUF only.
- **mlasli 8-bit Heretic:** v2.0 was retracted, the GSM8K drop of 14 points
  is unexplained, it is text-only and has no MTP.
- **Low-traction builds:** eternalis oQ4e and the many 0–2-like OBLITERATED
  re-uploads.
- **Foresee "Qwen3.8-9B":** not an official Qwen size.
- **Low-RAM tiers:** most 2B/4B builds (text-only, 8k context, too small for
  the ~18k system prompt).

## What adding them takes (round 3)

1. **Roster entries**, opt-in, never default, and never recommended by
   onboarding. Each carries an **Uncensored** badge and a menu line: "no
   refusals — the approval gate is the only brake". Each is marked
   "unverified until drilled". Every entry uses the existing curated shape
   (`allow_patterns`, `ignore_patterns`, `hf_offline`,
   `trust_remote_code: false`).
2. **`model_subfolder`**, a new roster field (PocketAiHub keeps its weights
   in `4bit/`). The download, `_weights_complete`, `server-backend`, the
   local snapshot resolution in `mlx-vlm-launch.py` and the C130 model pin
   all have to agree on the path.
3. **Per-model sampling defaults** (e.g. `repetition_penalty: 1.15`), which
   these models' authors say agent use needs. First find out how Hermes
   0.18 passes sampling parameters to a custom provider. If it cannot,
   apply them as server-side defaults in the launcher.
4. **A drill on AC before trusting any of them:** the 6-case tool drill, the
   two-stream concurrency probe, and MTP acceptance with the stock drafter
   for #2.
   - Add a benign over-refusal check: borderline but harmless prompts, in
     the style of the XSTest benchmark.
   - Re-check that refusals do not return under Hermes' long system prompt.
5. Optional: `tools/convert-to-mlx.sh` plus a local-build roster type, for
   OBLITERATUS V3 and HauhauCS 9B.

Abliteration removes the model's own refusals. On this Mac that leaves the
approval gate (C111/C116), the permission tiers, and the reduced unattended
toolsets (C36) as the controls on what an agent turn can actually do.
