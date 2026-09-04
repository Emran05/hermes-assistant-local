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
#
# ---------------------------------------------------------------------------
# WHY EVERY VERSION BELOW IS PINNED (2026-09-04 evaluation, backlog #23/#25)
# ---------------------------------------------------------------------------
# 1. DO NOT MOVE TO mlx-vlm 0.6.16 OR 0.6.17. Both are clean single-stream, and
#    both CORRUPT OUTPUT when two requests are in flight while the native MTP
#    drafter is loaded — which is exactly this configuration. Measured on a
#    2-concurrent-stream probe: 0.6.14 was 6/6 rounds correct; 0.6.17 was 2/6
#    (token-0 floods of "!", semantic collapse, early `finish_reason=stop`) and
#    0.6.16 additionally hard-faulted the GPU (kIOGPUCommandBufferCallbackError
#    PageFault). Reproduced with the launcher's RNG shim disabled and with 0.6.17
#    forced back onto mlx 0.32.1, so it is neither the shim nor mlx — the
#    regression is in mlx-vlm, somewhere in 0.6.15-0.6.16. Failures are
#    NON-DETERMINISTIC (a clean round follows a corrupt one on the same process),
#    so in production it would surface as occasional garbage answers whenever a
#    Telegram turn overlaps a dashboard turn, with nothing in the log to explain
#    it. The upgrade also buys nothing single-stream (TTFT/prefill/code decode
#    within noise, prose -8%, footprint -2GB).
# 2. mlx IS PINNED TOO. mlx-vlm only declares `mlx>=0.32.0`, so re-running this
#    script with mlx floating silently builds 0.6.14 against mlx 0.32.2 — a
#    DIFFERENT teardown path (0.32.2 fixes the CompileCache destructor segfault
#    that mlx-vlm-launch.py's atexit `os._exit(0)` shim exists to dodge; on
#    0.32.1 that shim is still load-bearing — without it a clean SIGINT exits
#    -11 with a crash report). Pinning keeps the venv matching what was measured.
# 3. transformers / huggingface_hub are pinned to reproduce the live venv
#    exactly; unpinned, a rebuild drifts to 5.16.1 / 1.30.0.
#
# TO UPGRADE (when upstream fixes batched-MTP correctness): bump the two
# versions below, then follow the rename-aside rollback plan — launchctl stop
# the model service, `mv ~/.hermes/mlx-vlm-venv ~/.hermes/mlx-vlm-venv.0.6.14`
# (RENAME, never delete), re-run this script, and gate acceptance on 6/6 clean
# concurrent rounds plus the 6-case tool drill. If anything fails, rm the new
# venv and mv the old one back — no other file changes are involved, so rollback
# is one rename. Keep the old venv until a few days of normal use are clean.
# Moving to 0.6.17 + mlx 0.32.2 also means DELETING `_patch_rng_restore()` and
# the atexit/`_hard_exit` block in mlx-vlm-launch.py: upstream fixed the RNG
# restore, and the launcher's stale probe would otherwise keep no-opping a
# function that works.
set -euo pipefail
BASE_PY="${BASE_PY:-/Library/Frameworks/Python.framework/Versions/3.12/bin/python3}"
[ -x "$BASE_PY" ] || BASE_PY="$(command -v python3.12 || command -v python3)"
VENV="$HOME/.hermes/mlx-vlm-venv"
MLX_VLM_VERSION="${MLX_VLM_VERSION:-0.6.14}"   # 0.6.16/0.6.17 corrupt output when 2 requests
                                               # overlap with the MTP drafter — see #23/#25
MLX_VERSION="${MLX_VERSION:-0.32.1}"           # pin mlx too: it floats otherwise, and a re-run
                                               # today would silently take 0.32.2 (untested here)

echo "→ creating $VENV with $BASE_PY"
"$BASE_PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "mlx-vlm==$MLX_VLM_VERSION" \
    "mlx==$MLX_VERSION" "mlx-metal==$MLX_VERSION" \
    "transformers==5.15.0" "huggingface_hub==1.28.0" jinja2
"$VENV/bin/python" - <<'PY'
import mlx.core as mx, mlx_vlm, sys, os
print(f"   mlx {mx.__version__} · mlx-vlm {mlx_vlm.__version__} · OK")
sys.stdout.flush(); os._exit(0)      # skip mlx 0.32 teardown segfault
PY
echo "→ done. Roster models with backend=mlx_vlm now start through the venv on"
echo "  the next switch (the dashboard writes ~/.hermes/dashboard/server-backend)."
echo "  Drafter weights (e.g. mlx-community/Qwen3.8-27B-MTP-bf16) download with"
echo "  the model from the model menu."
