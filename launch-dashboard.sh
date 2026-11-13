#!/usr/bin/env bash
# launch-dashboard.sh — start the local Hermes dashboard in this terminal and
# open it in your browser. A manual launcher; the always-on service is
# com.hermes.dashboard (install-services.sh), and when that is already
# answering this only opens the browser.
#
# It does not start the model. The model servers are on demand: mlx-server.sh
# refuses to load without a fresh start token, which only the dashboard mints
# (2026-09-10 audit A09), and the first chat turn wakes the model. This script
# used to run mlx-server.sh anyway, which the gate refused with exit 0, and then
# polled :8080 for up to six minutes. It also logged into the repo under
# dashboard/logs; output now goes to ~/.hermes/logs/dashboard.log, beside the
# service's own log.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"

DASH_PORT="${DASH_PORT:-7788}"
URL="http://127.0.0.1:${DASH_PORT}"
LOGDIR="$HOME/.hermes/logs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/dashboard.log"

# Already serving (normally the launchd service)? Then just open it: a second
# server.py on the same port would only die with "address already in use".
if curl -s -o /dev/null --max-time 3 "$URL/api/version"; then
  echo "✓ dashboard already running on :$DASH_PORT — opening $URL"
  open "$URL" >/dev/null 2>&1 || true
  exit 0
fi

echo "→ starting the dashboard on :$DASH_PORT (log: $LOG) and opening $URL"
echo "  the model is on demand: the first chat turn wakes it"
( sleep 1; open "$URL" >/dev/null 2>&1 || true ) &
exec > >(tee -a "$LOG") 2>&1
DASH_PORT="$DASH_PORT" exec python3 "$HERE/dashboard/server.py"
