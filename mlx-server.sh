#!/usr/bin/env bash
# mlx-server.sh — start the local OpenAI-compatible LLM server for Hermes.
# Run this FIRST (in its own terminal / as a service), then start Hermes.
set -euo pipefail

# --- Model choice -----------------------------------------------------------
# Primary: fast, reliable orchestration model. ~18-20GB in 4-bit, leaves the
# M5's memory free for everything else. This is the right default for an
# assistant whose job is tool-calling, not heavy reasoning.
DEFAULT_MODEL="mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit"
# (Corrected: there is no "Qwen 4.1 / 32B-A3B". The real MoE is Qwen3-30B-A3B,
#  ~3.3B active. The 2507 Instruct refresh natively supports 262k context, so
#  the 65536 ctx below works WITHOUT YaRN rope-scaling hacks.)
# The dashboard's model toggle writes the chosen repo id here; fall back to the
# default when it's absent so the server always has a model to load.
ACTIVE_FILE="$HOME/.hermes/dashboard/active-model"
MODEL="$(cat "$ACTIVE_FILE" 2>/dev/null || echo "$DEFAULT_MODEL")"
[ -z "$MODEL" ] && MODEL="$DEFAULT_MODEL"

# Upgrade option if you find it fumbling multi-step tool chains (needs ~55-60GB,
# tight but fits 64GB headless). Uncomment to use instead:
# MODEL="mlx-community/GLM-4.5-Air-4bit"   # GLM-4.5-Air is 106B-A12B; repo is just "-4bit"

PORT=8080
CTX=65536          # must be >= 64k for Hermes; raise if you have KV headroom

# --- Install once -----------------------------------------------------------
# pip install --upgrade mlx-lm

echo "Starting MLX server: $MODEL on :$PORT (ctx=$CTX)"
# NOTE: use `python3 -m mlx_lm server` — the `mlx_lm.server` script is NOT on
# PATH after a user-site pip install. mlx_lm.server has no --ctx flag; context
# is taken from the model config (Qwen3-2507 = 262k native, so 65536 is fine).
exec python3 -m mlx_lm server \
  --model "$MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --max-tokens 4096 \
  --prompt-cache-size 6 \
  --prompt-cache-bytes 6000000000 \
  --trust-remote-code
# --prompt-cache-* CAPS the in-memory KV/prompt cache so it can't grow
# unbounded and thrash RAM (root cause of the 49GB blowup; the dashboard's
# memory_guard is now just a backstop). 6GB is plenty of prefix-reuse headroom
# for a handful of active sessions. "Resume later" is handled by Hermes's own
# message-history restore (state.db) — KV-cache-to-disk isn't worth it here.
# The server exposes http://127.0.0.1:8080/v1 (OpenAI-compatible), which is
# exactly what config.yaml -> model.base_url points at.
