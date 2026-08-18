#!/usr/bin/env bash
# install-mlx-vlm-venv.sh — (re)create the ISOLATED mlx-vlm venv used by the
# "mlx_vlm" model-server backend (mlx-server.sh → mlx-vlm-launch.py). Today it
# serves Qwen3.8-27B with its native MTP speculative drafter (~2x decode) and
# APC exact prefix caching.
#
# Why isolated: mlx-vlm needs mlx>=0.32 + transformers 5.x. The framework
# Python's user-site holds mlx-lm 0.31.x, which REQUIRES transformers<5 (see
# CLAUDE.md gotchas) — installing mlx-vlm there would break the default 30B path.
# Same pattern as ~/.hermes/graphify-venv. Safe to re-run.
set -euo pipefail
BASE_PY="${BASE_PY:-/Library/Frameworks/Python.framework/Versions/3.12/bin/python3}"
[ -x "$BASE_PY" ] || BASE_PY="$(command -v python3.12 || command -v python3)"
VENV="$HOME/.hermes/mlx-vlm-venv"
MLX_VLM_VERSION="${MLX_VLM_VERSION:-0.6.14}"   # tested 2026-08-18 with mlx 0.32.1

echo "→ creating $VENV with $BASE_PY"
"$BASE_PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "mlx-vlm==$MLX_VLM_VERSION" jinja2
"$VENV/bin/python" - <<'PY'
import mlx.core as mx, mlx_vlm, sys, os
print(f"   mlx {mx.__version__} · mlx-vlm {mlx_vlm.__version__} · OK")
sys.stdout.flush(); os._exit(0)      # skip mlx 0.32 teardown segfault
PY
echo "→ done. Roster models with backend=mlx_vlm now start through the venv on"
echo "  the next switch (the dashboard writes ~/.hermes/dashboard/server-backend)."
echo "  Drafter weights (e.g. mlx-community/Qwen3.8-27B-MTP-bf16) download with"
echo "  the model from the model menu."
