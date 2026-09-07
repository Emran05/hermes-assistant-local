# 1.2 line — "a real harness" (brief, 2026-09-07)

Owner's ask: make Hermes feel like a real agent harness, borrow from popular open-source
harnesses, and optimize for **memory and speed** on the local model.

## What "harness" means here
The runtime around the model: tool execution + sandboxing, session/context management
(compaction, checkpoints), memory layers, model routing, caching, tracing/observability,
evals, health/doctor, plugins, budgets. Hermes already has: permission tiers + approvals,
Flight Recorder (undo), same-origin guard, on-demand model lanes, prewarm, MTP, auto-route
to Claude, drill (6-case eval), self-updater, MCP server, FTS index.

## Research first (Sonnet researchers, in parallel; cite sources; September 2026)
1. Open-source harnesses to mine for portable ideas: Goose (Block), OpenHands, OpenAI Codex
   CLI, Aider, Cline/Roo, mini-swe-agent, DeepSeek Harness `dsh` (already spiked in
   ~/.hermes/dsh — see CLAUDE.md), Pi agent, Agent Zero, Open Interpreter, Hermes Agent
   0.21 itself (installed 0.18). For each: context compaction strategy, tool-output
   truncation, lazy tool/skill loading, prompt caching use, session checkpoints/resume,
   doctor/health command, tracing format, eval suite, plugin surface, budget meters.
2. Memory + speed for local LLM serving (MLX): system-prompt size vs TTFT (ours is ~18k
   tokens — the single biggest per-turn cost; prewarm hides it only after wake), lazy skill
   catalogs, compact tool schemas, KV/prefix cache policies, unload/idle policies, smaller
   specialist models for classification/embeddings, Letta/mem0-style memory layers that keep
   prompts short (episodic + facts + recency decay), token budgets per tool result.

## Candidate deliverables (rank after research; ship as 1.2.x, measured before/after)
- **System-prompt diet**: measure the Hermes system prompt token count and composition;
  lazy-load skills (index + on-demand SKILL.md fetch), compact tool schemas, trim volatile
  context → target ≥30% fewer prefix tokens; measure cold TTFT and per-turn prefill.
- **`hermes-assistant doctor`** (script + Settings card): services, model lanes, venv pins,
  FDA, disk, tokens present, index/needsyou/onboarding state, last release check — one
  screen, one command; mirrors what every OSS harness ships.
- **Bench in repo**: promote the scratch `baseline.py` / `ttft_after_wake_v2.sh` into
  `tools/bench/` with a README; results land in docs/plans and the CHANGELOG.
- **Context/compaction surface**: expose Hermes-agent compression (threshold/target) and
  session checkpoints in Settings › Agent & Models with the measured token savings; a
  "conversation size" meter in the chat header.
- **Memory layer v1**: facts + episodic notes with recency, backed by the FTS index, injected
  as a small "what I know about you" block instead of whole histories (measure prompt tokens).
- **Tool-result budgets**: cap and summarize long tool outputs before they enter context
  (configurable), with the Recorder keeping the full text.
- **Evals**: extend the 6-case drill into a scheduled local eval suite with a history chart.
- **Tracing export**: Recorder → JSONL/OTel-shaped export for external analysis.
- **Model routing v2**: classify/route cheap tasks to the 9B lane automatically (measured).

## Rules (unchanged)
Battery: model servers on-demand; measurements only on AC and unloaded after; never edit
app/main.swift; never force-push; push origin + public; tag == v$(cat VERSION); Opus for
coding, Sonnet for reading/research; scratch harnesses in the session scratchpad.
