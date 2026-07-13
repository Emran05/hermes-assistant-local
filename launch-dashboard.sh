#!/usr/bin/env bash
# launch-dashboard.sh — start the local Hermes dashboard (and the model server
# if it isn't already running), then open it in your browser.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"

DASH_PORT="${DASH_PORT:-7788}"
MODEL_URL="http://127.0.0.1:8080/v1/models"
LOGDIR="$HERE/dashboard/logs"; mkdir -p "$LOGDIR"

# 1. Make sure the local model server is up (Hermes needs it to answer).
if curl -s -o /dev/null --max-time 3 "$MODEL_URL"; then
  echo "✓ model server already running on :8080"
else
  echo "… model server not up — starting mlx-server.sh in the background"
  nohup "$HERE/mlx-server.sh" > "$LOGDIR/mlx-server.log" 2>&1 &
  printf "  waiting for model to load"
  for i in $(seq 1 180); do
    if curl -s -o /dev/null --max-time 3 "$MODEL_URL"; then echo " — ready."; break; fi
    printf "."; sleep 2
    if [ "$i" -eq 180 ]; then echo; echo "  ✗ model server didn't come up; see $LOGDIR/mlx-server.log"; fi
  done
fi

# 2. Start the dashboard and open the browser.
URL="http://127.0.0.1:${DASH_PORT}"
echo "→ opening $URL"
( sleep 1; open "$URL" >/dev/null 2>&1 || true ) &
DASH_PORT="$DASH_PORT" exec python3 "$HERE/dashboard/server.py"
