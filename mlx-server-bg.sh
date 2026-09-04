#!/usr/bin/env bash
# mlx-server-bg.sh — the BACKGROUND lane: a second, always-on, small local
# model on :8081 that every non-interactive producer uses (briefing, watchtower
# intel/news, For-You candidates, trends…) so the primary :8080 model (Qwen3.8-27B)
# is never touched by "updates and news drops" and stays warm for the user.
#
# Model: ~/.hermes/dashboard/bg-model (repo id) → default Qwen3.5-9B-4bit — same
# GatedDeltaNet family + chat template as Qwen3.8 (XML tool calls, thinking
# control), ~6GB resident, fast. Thinking is forced OFF (template default is on):
# this lane is for volume, not depth. The dashboard's bg lane (server.py
# bg_lane()) falls back to :8080 when this service is down, so it's optional.
#
# Served through the isolated mlx-vlm venv (mlx-vlm-launch.py) — the 9B
# checkpoints ship `tokenizer_class: TokenizersBackend` (a transformers-5 class),
# which mlx-lm's pinned transformers<5 cannot load ("Tokenizer class
# TokenizersBackend does not exist" → the mlx_lm server hangs on the first
# request). Falls back to mlx-lm only if the venv is missing.
set -euo pipefail

# --- On-demand gate (2026-09-01) --------------------------------------------
# Same gate as mlx-server.sh: with model-autostart-off present, the background
# lane only starts on an explicit fresh token. Nothing in the dashboard mints
# the bg token automatically — to run this lane while on-demand mode is active:
#   touch ~/.hermes/dashboard/model-start-ok-bg && \
#   launchctl kickstart gui/$(id -u)/com.hermes.mlx-bg
GATE_FILE="$HOME/.hermes/dashboard/model-autostart-off"
START_TOKEN="$HOME/.hermes/dashboard/model-start-ok-bg"
if [ -f "$GATE_FILE" ]; then
  fresh=0
  if [ -f "$START_TOKEN" ]; then
    now="$(date +%s)"
    mt="$(stat -f %m "$START_TOKEN" 2>/dev/null || echo 0)"
    if [ $(( now - mt )) -le 180 ]; then fresh=1; fi
  fi
  if [ "$fresh" = 1 ]; then
    rm -f "$START_TOKEN"
  else
    echo "[mlx-bg] on-demand mode: no fresh start token — not loading the model"
    exit 0
  fi
fi

DEFAULT_BG_MODEL="mlx-community/Qwen3.5-9B-4bit"
BG_FILE="$HOME/.hermes/dashboard/bg-model"
MODEL="$(cat "$BG_FILE" 2>/dev/null || echo "$DEFAULT_BG_MODEL")"
[ -z "$MODEL" ] && MODEL="$DEFAULT_BG_MODEL"
PORT="${BG_PORT:-8081}"
VLM_PY="$HOME/.hermes/mlx-vlm-venv/bin/python"

# --- RAM-aware prompt-cache sizing (1.0.3) -----------------------------------
# Mirrors the tiering in mlx-server.sh (same sysctl read, same GiB units, same
# fallback to 64GB when sysctl is unavailable), but with its OWN, lower table:
# this lane serves a ~6GB 9B model and shares the Mac with the ~19GB primary, so
# it must never claim the primary's budget. The >=64 row is exactly today's
# 4 entries / 3GB — nothing changes on the machine this was tuned on. Env wins.
RAM_BYTES="$(/usr/sbin/sysctl -n hw.memsize 2>/dev/null || true)"
# Same silent-fallback fix as mlx-server.sh — see the note there.
case "$RAM_BYTES" in ''|*[!0-9]*)
  echo "[mlx-bg] hw.memsize unreadable — assuming 64 GB for cache sizing" >&2
  RAM_BYTES=68719476736 ;;
esac
RAM_GB=$(( RAM_BYTES / 1073741824 ))
if   [ "$RAM_GB" -ge 64 ]; then APC_ENTRIES_DEFAULT=4; PROMPT_CACHE_BYTES=3000000000
elif [ "$RAM_GB" -ge 48 ]; then APC_ENTRIES_DEFAULT=3; PROMPT_CACHE_BYTES=2500000000
elif [ "$RAM_GB" -ge 36 ]; then APC_ENTRIES_DEFAULT=2; PROMPT_CACHE_BYTES=2000000000
else                            APC_ENTRIES_DEFAULT=2; PROMPT_CACHE_BYTES=1500000000
fi

if [ -x "$VLM_PY" ]; then
  echo "Starting MLX-VLM background server: $MODEL on :$PORT (thinking off)"
  export APC_ENABLED=1
  export APC_EXACT_CACHE_ENTRIES="${APC_EXACT_CACHE_ENTRIES:-$APC_ENTRIES_DEFAULT}"
  echo "[mlx-bg] ${RAM_GB}GB Mac → APC_EXACT_CACHE_ENTRIES=$APC_EXACT_CACHE_ENTRIES"
  exec "$VLM_PY" "$(dirname "$0")/mlx-vlm-launch.py" \
    --model "$MODEL" --host 127.0.0.1 --port "$PORT" \
    --max-tokens 4096 --trust-remote-code
fi

echo "Starting MLX background server (mlx-lm fallback): $MODEL on :$PORT"
exec python3 -m mlx_lm server \
  --model "$MODEL" --host 127.0.0.1 --port "$PORT" \
  --max-tokens 4096 --prompt-cache-size "$APC_ENTRIES_DEFAULT" \
  --prompt-cache-bytes "$PROMPT_CACHE_BYTES" \
  --chat-template-args '{"enable_thinking": false}' --trust-remote-code
