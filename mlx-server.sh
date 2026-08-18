#!/usr/bin/env bash
# mlx-server.sh — start the local OpenAI-compatible LLM server for Hermes.
# Run this FIRST (in its own terminal / as a service), then start Hermes.
set -euo pipefail

# --- Model choice -----------------------------------------------------------
# Primary: fast, reliable orchestration model. ~18-20GB in 4-bit, leaves the
# M5's memory free for everything else. This is the right default for an
# assistant whose job is tool-calling, not heavy reasoning.
DEFAULT_MODEL="mlx-community/Qwen3.8-27B-4bit"
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

# --- Per-model chat-template kwargs -----------------------------------------
# The dashboard writes ~/.hermes/dashboard/chat-template-args (JSON) on every
# model switch / thinking toggle from the roster entry's "template_args"
# (e.g. Qwen3.8-27B: {"enable_thinking": false} — its template thinks at
# reasoning_effort=xhigh by default, ~22k tokens on trivial prompts, useless
# in a tool loop). Absent/empty file → "{}" (template defaults).
TEMPLATE_ARGS_FILE="$HOME/.hermes/dashboard/chat-template-args"
TEMPLATE_ARGS="$(cat "$TEMPLATE_ARGS_FILE" 2>/dev/null || echo "{}")"
[ -z "$TEMPLATE_ARGS" ] && TEMPLATE_ARGS="{}"

# --- Install once -----------------------------------------------------------
# pip install --upgrade mlx-lm

# --- Backend selection ------------------------------------------------------
# The dashboard writes ~/.hermes/dashboard/server-backend (JSON) on every switch:
#   {"backend":"mlx_vlm","draft_model":...,"draft_kind":"mtp","draft_block_size":3,
#    "enable_thinking":false,"reasoning_effort":"low"}
# backend "mlx_vlm" = the isolated venv (~/.hermes/mlx-vlm-venv, mlx>=0.32 +
# transformers 5 — NEVER install that into the framework Python: it breaks
# mlx-lm) via mlx-vlm-launch.py, which adds native MTP speculative decoding
# (~2x decode on Qwen3.8-27B) and APC exact prefix caching. Anything else (or a
# missing venv) = python3 -m mlx_lm server as before.
BACKEND_FILE="$HOME/.hermes/dashboard/server-backend"
VLM_PY="$HOME/.hermes/mlx-vlm-venv/bin/python"
BACKEND="mlx_lm"; DRAFT_MODEL=""; DRAFT_KIND="mtp"; DRAFT_BLOCK="3"
VLM_THINK="0"; VLM_EFFORT=""
if [ -f "$BACKEND_FILE" ] && [ -x "$VLM_PY" ]; then
  eval "$(python3 - "$BACKEND_FILE" <<'PYEOF'
import json, shlex, sys
try:
    c = json.load(open(sys.argv[1]))
except Exception:
    c = {}
print("BACKEND=" + shlex.quote(str(c.get("backend") or "mlx_lm")))
print("DRAFT_MODEL=" + shlex.quote(str(c.get("draft_model") or "")))
print("DRAFT_KIND=" + shlex.quote(str(c.get("draft_kind") or "mtp")))
print("DRAFT_BLOCK=" + shlex.quote(str(c.get("draft_block_size") or 3)))
print("VLM_THINK=" + ("1" if c.get("enable_thinking") else "0"))
print("VLM_EFFORT=" + shlex.quote(str(c.get("reasoning_effort") or "")))
PYEOF
)"
fi

if [ "$BACKEND" = "mlx_vlm" ]; then
  echo "Starting MLX-VLM server: $MODEL on :$PORT draft=$DRAFT_MODEL ($DRAFT_KIND, block $DRAFT_BLOCK) thinking=$VLM_THINK effort=$VLM_EFFORT"
  VLM_ARGS=(--model "$MODEL" --host 127.0.0.1 --port "$PORT" --max-tokens 4096 --trust-remote-code)
  [ -n "$DRAFT_MODEL" ] && VLM_ARGS+=(--draft-model "$DRAFT_MODEL" --draft-kind "$DRAFT_KIND" --draft-block-size "$DRAFT_BLOCK")
  # thinking on → --enable-thinking (+ effort default via the launcher shim; the
  # template's own default is xhigh) and a hard token budget as a safety net.
  [ "$VLM_THINK" = "1" ] && VLM_ARGS+=(--enable-thinking --thinking-budget 8192)
  # APC = exact prefix cache. Hybrid SSM/attention models run in "exact" mode
  # (whole-prefix snapshots, not K/V blocks); APC_EXACT_CACHE_ENTRIES bounds
  # how many distinct prefixes stay resident (default 2 → thrashes with hub
  # chats + Telegram + briefing all inserting). ~64KB/token of KV for this model
  # → an 18k-token agent prefix ≈ 1.2GB, so 6 entries ≈ the 8GB we give mlx-lm.
  export APC_ENABLED=1
  export APC_EXACT_CACHE_ENTRIES="${APC_EXACT_CACHE_ENTRIES:-6}"
  [ -n "$VLM_EFFORT" ] && export MLX_VLM_DEFAULT_REASONING_EFFORT="$VLM_EFFORT"
  exec "$VLM_PY" "$(dirname "$0")/mlx-vlm-launch.py" "${VLM_ARGS[@]}"
fi

echo "Starting MLX server: $MODEL on :$PORT (ctx=$CTX) template-args=$TEMPLATE_ARGS"
# NOTE: use `python3 -m mlx_lm server` — the `mlx_lm.server` script is NOT on
# PATH after a user-site pip install. mlx_lm.server has no --ctx flag; context
# is taken from the model config (Qwen3-2507 = 262k native, so 65536 is fine).
exec python3 -m mlx_lm server \
  --model "$MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --max-tokens 4096 \
  --prompt-cache-size 6 \
  --prompt-cache-bytes 8000000000 \
  --chat-template-args "$TEMPLATE_ARGS" \
  --trust-remote-code
# --prompt-cache-* CAPS the in-memory KV/prompt cache so it can't grow
# unbounded and thrash RAM (root cause of the 49GB blowup; the dashboard's
# memory_guard is now just a backstop). Measured (P3.B3): each ~20k-token agent
# sequence costs ~2GB of KV, so 6GB held only ~3 sequences while >=4 producers
# (hub chats, menubar, briefing -z regens, watchtower/intel) insert them — LRU
# churn kept re-paying the ~8s cold prefill. 8GB holds 4; steady footprint ~26GB
# stays under the 32GB memory_guard restart line. Drop back to 6GB if switching
# to GLM-4.5-Air (106B leaves no headroom). "Resume later" is Hermes's own
# message-history restore (state.db) — KV-cache-to-disk isn't worth it here.
# The server exposes http://127.0.0.1:8080/v1 (OpenAI-compatible), which is
# exactly what config.yaml -> model.base_url points at.
