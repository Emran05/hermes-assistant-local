#!/bin/bash
# capture_send.sh — screenshot the Mac and send it to the USER'S OWN Telegram.
#
# SAFETY — the send target is HARD-LOCKED to `--to telegram` (the user's home
# channel). This script NEVER accepts an agent-supplied chat_id / recipient /
# platform. There is no argument that can change where the image goes. This is
# the code-enforced half of the mac-control send invariant; do not "fix" it by
# adding a target flag.
#
# SEND IS OPT-IN. By default this script only PREVIEWS (captures + prints the
# exact hard-locked send command) and sends NOTHING — a send is a consequential
# action and must never auto-fire. To actually deliver, the caller must set
# MC_CONFIRM_SEND=1, which it should do ONLY after the user has said "send it"
# (or after the permission gate has approved it).
#
# Usage:
#   capture_send.sh [caption...]                    # PREVIEW only (no send)
#   MC_CONFIRM_SEND=1 capture_send.sh [caption...]  # actually send (after user OK)
#   MC_DISPLAY=2 capture_send.sh [caption...]       # capture display 2 (integer)
#
# The capture path is ALWAYS a server-chosen scratch file under
# ~/.hermes/cache/mac-control/ — never a user/agent-supplied path — so this can
# never be used to exfiltrate an arbitrary on-disk file into Telegram.
set -euo pipefail

CACHE="${HERMES_HOME:-$HOME/.hermes}/cache/mac-control"
mkdir -p "$CACHE"
chmod 700 "$CACHE" 2>/dev/null || true

# prune our own scratch captures older than 1 hour (no silent screenshot archive)
find "$CACHE" -name 'shot-*.png' -type f -mmin +60 -delete 2>/dev/null || true

SHOT="$CACHE/shot-$(date +%s).png"

# Build the full argv as one (never-empty) array so `set -u` is happy on the
# stock macOS bash 3.2 (empty-array expansion under `set -u` errors there).
# -x = no shutter sound; capture steals no window.
CAP=(/usr/sbin/screencapture -x)
# display index: integers only (guards against flag/path injection into argv)
if [ -n "${MC_DISPLAY:-}" ]; then
  case "$MC_DISPLAY" in
    ''|*[!0-9]*) echo "capture_send: MC_DISPLAY must be an integer" >&2; exit 2 ;;
    *) CAP+=(-D "$MC_DISPLAY") ;;
  esac
fi
CAP+=("$SHOT")
"${CAP[@]}"

# never send a 0-byte / missing "screenshot" (TCC-denied capture writes nothing)
if [ ! -s "$SHOT" ]; then
  echo "capture_send: screenshot is empty — Screen Recording (TCC) likely not granted." >&2
  echo "Run: hermes computer-use doctor" >&2
  rm -f "$SHOT"
  exit 1
fi

CAPTION="${*:-}"
# The message body carries MEDIA:<path> so hermes attaches the PNG. Caption text
# is appended AFTER the media token. Target is the constant 'telegram'.
MSG="MEDIA:$SHOT"
[ -n "$CAPTION" ] && MSG="MEDIA:$SHOT $CAPTION"

if [ -z "${MC_CONFIRM_SEND:-}" ]; then
  # DEFAULT: preview only — show exactly what WOULD be sent, send nothing.
  echo "[preview] captured: $SHOT ($(wc -c < "$SHOT" | tr -d ' ') bytes)"
  echo "[preview] would run: hermes send --to telegram \"$MSG\""
  echo "[preview] target is hard-locked to your Telegram home channel."
  echo "[preview] no send performed — re-run with MC_CONFIRM_SEND=1 after the user approves."
  exit 0
fi

echo "Sending 1 screenshot to your Telegram home channel..."
# HARD-LOCKED: --to telegram, home channel. No agent-supplied target, ever.
hermes send --to telegram "$MSG"
echo "Sent: $SHOT"
