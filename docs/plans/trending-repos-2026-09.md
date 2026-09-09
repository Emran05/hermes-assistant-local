# Trending open-source, evaluated for Hermes Assistant (2026-09-08)

One pass over GitHub trending (all languages + Python/Swift/TypeScript/Rust pages), seven
GitHub searches (MLX serving, local agents on macOS, computer use, MCP servers, memory,
compaction, evals/tracing), and the three harnesses installed on this Mac. Method: Haiku
scouts captured lists, three Sonnet readers evaluated against what Hermes Assistant is
(stdlib dashboard, Hermes Agent runtime, MLX on-demand serving, frozen Swift shell), and the
findings were ranked here. Full evidence with URLs: `docs/plans/research/trending-2026-09/`.

## The installed harnesses (the one "blowing up" is dsh)

| Installed | What | Stars (2026-09-08) | Verdict |
|---|---|---|---|
| `dsh` — DeepSeek Harness rc.7 (npm) + spike in `~/.hermes/dsh` | plugin-everything agent harness | 216k, created 2026-08-13 (about 8k/day) | the surge of the month; 1.2.1 already took its output-retention, spill, compaction and digest-catalog ideas; still to take: permission presets, sandbox-mode contract, one-shot approval audit |
| ECC (Claude Code plugin marketplace) | 285 skills / 65 agents / hooks for Claude Code | 254k, steady | not a fit wholesale (Node, Claude-Code-specific); two ideas worth a stdlib port |
| prime-agent 0.7.0 (npm) | one-tool (persistent IPython) harness, fork of pi-mono | 20k | niche; two ideas for rlm-repl and skills |

## Ranked adoptions

Effort S = a day, M = a few days, L = a release. "Idea" = re-implement in stdlib Python; code
is not copied (licences noted).

| # | From | What we take | Lands in | Effort | Why |
|---|---|---|---|---|---|
| 1 | ml-explore/mlx-vlm 0.7.0 (2026-09-07) | canary the unpin: 0.7.0 ships fixes for phantom KV in batched speculative rollback (#2113), MTP drafter detection (#2120), quantized KV in Qwen MTP verification (#1986) — the bug class behind our 0.6.14 pin | `install-mlx-vlm-venv.sh` behind `tools/bench/concurrency_probe.py` (gate: 6/6 clean rounds) | S | speed and memory headroom; do not retry 0.6.16/17 |
| 2 | NousResearch/hermes-agent v0.21.1 (we run 0.18) | upgrade attempt on a branch: Compression Refinements (proactive pruning, per-turn micro-compaction), Tool Self-Recovery (terminal spill to files), Grounded Citations, Browser Control, Streaming Voice + wake words, instruction-file write approval | runtime; gate with the 1.2.3 eval suite before/after and the plugin-hook audit (`transform_tool_result`, `pre_llm_call`) | L | the biggest single lever; check whether Streaming Voice runs outside the app (TCC) |
| 3 | microsoft/markitdown (MIT) | PDF/Office/HTML/EPub to Markdown for the FTS index (mail attachments, downloads, notes) | `aux_index.py` ingestion via CLI in its own venv | S–M | search over documents, not just mail and chat |
| 4 | dsh permission presets + sandbox-mode + one-shot approval | one named toggle sets sandbox mode and approval policy together; `read-only` / `workspace-write` / `danger-full-access` resolved once per call, fail-closed; paired asked/decided audit events | permission tiers (`permissions.py`), Flight Recorder | M | our tiers exist; the preset UX and the audit pairing do not |
| 5 | ayghri/i-have-adhd, blader/humanizer (MIT SKILL.md) | terse action-first answers as a selectable style; de-bot drafted mail | skills dir + a "reply style" setting for Telegram and Quick Ask | S | fits small local models and the attention-router purpose |
| 6 | akitaonrails/ai-memory (MIT) | capture → consolidate → recall → handoff loop; consolidation pass over facts; handoff note for Claude escalation | memory layer (`aux_memlayer.py`) | S–M | our layer retrieves but never consolidates |
| 7 | sergezuber/FABULA-LLM-5 (MIT) | "done" enforced by a verify step, a judge veto, auto-rewind and a proof-of-done receipt | autonomous/background tasks; receipts into traces | M | small local models claim done too early |
| 8 | Health-Yang/MineEcho "TokenLess" (PolyForm NC — idea only) | per-tool reducer rules (git, npm, docker, generic: keep errors, actionable lines) before head/tail | tool-budget plugin | S | smarter than head/tail for known tool shapes |
| 9 | 0xbrando/dictate, cleanunicorn/earheart, debpalash/VoiceStudio (AGPL, run as separate process) | voice without rebuilding the shell: a companion helper that owns its own mic permission and hands text to Quick Ask over localhost | new optional install + a `/api/quickask/dictation` drop point | M | the voice block was the shell, not the runtime |
| 10 | prime-agent | skills that ship Python installed into the persistent kernel; MCP servers exposed as Python objects instead of tool schemas | rlm-repl / skills loader | M | attacks the tool-schema bloat measured in 1.2.0 |
| 11 | ECC | prompt-defense preamble for fetched content inside `transform_tool_result`; a verification-loop skill (lint/test/secret-grep before "done") | tool-budget plugin, skills | S–M | injection defence is missing today |
| 12 | volcengine/OpenViking (AGPLv3 — idea only) | L0/L1/L2 progressive loading (abstract → overview → full) for facts and skills | memory layer, skills index | M | keeps the prefix small as memory grows |
| 13 | mcpsnoop, agent-hands, nuphus-mcp | MCP traffic sidecar for traces; record-once/replay-many automation artifacts; desktop-automation MCP | traces, Needs-you routines | S–M | later; watch |

## Explicitly not adopting
- OpenViking and MineEcho code (AGPLv3 / PolyForm NC), mksglu/context-mode code (ELv2): ideas
  only.
- ECC wholesale: 68 agents and 286 skills on Node are the complexity this product avoids.
- Rapid-MLX / omlx as a serving swap: self-reported numbers; benchmark on this Mac first.
- Skill-pack star counts as quality signals: several six-figure counts are trending-page noise.

## Proposed next releases
- **1.2.4** — mlx-vlm 0.7.0 canary result (unpin or documented no), markitdown ingestion,
  reply-style skills, dsh permission presets.
- **1.3.0** — Hermes Agent 0.21.1 upgrade on a branch, gated by the eval suite and the
  plugin-hook audit; voice via a companion helper if Streaming Voice cannot run outside the app.
