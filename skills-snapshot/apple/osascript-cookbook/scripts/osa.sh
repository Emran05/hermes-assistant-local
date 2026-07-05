#!/bin/bash
# osa.sh — safe osascript runner. User data rides argv, NEVER string-spliced into scripts.
#
# Usage:
#   scripts/osa.sh [-l JavaScript] -f <script-file> [arg1 arg2 ...]
#   scripts/osa.sh [-l JavaScript] [--] [arg1 arg2 ...] <<'OSA'
#     ...script body (reads argv via `on run argv` / `function run(argv)`)...
#   OSA
#
# Why: interpolating user text into `osascript -e '...'` corrupts silently on
# quotes/newlines/emoji. This wrapper passes every argument through argv, so
# the script body is a constant and user data can contain anything.
#
# Exit-code doctrine: osascript exiting 1 with EMPTY stderr (or a -1743 /
# "Not authorized" / errAEEventNotPermitted message) is almost always a macOS
# Automation (TCC) denial for the target app — NOT a script bug. This wrapper
# detects that and says so, so callers stop retrying and surface it to the user.

LANGOPTS=""
SCRIPT_FILE="-"
while [ $# -gt 0 ]; do
  case "$1" in
    -l) LANGOPTS="$2"; shift 2 ;;
    -f) SCRIPT_FILE="$2"; shift 2 ;;
    --) shift; break ;;
    *)  break ;;
  esac
done

ERRFILE="$(mktemp /tmp/osa.stderr.XXXXXX)" || exit 70
trap 'rm -f "$ERRFILE"' EXIT

if [ -n "$LANGOPTS" ]; then
  /usr/bin/osascript -l "$LANGOPTS" "$SCRIPT_FILE" "$@" 2>"$ERRFILE"
else
  /usr/bin/osascript "$SCRIPT_FILE" "$@" 2>"$ERRFILE"
fi
STATUS=$?

if [ $STATUS -ne 0 ]; then
  if [ ! -s "$ERRFILE" ] || grep -qE '(-1743|-25211|Not authorized|not allowed assistive access|errAEEventNotPermitted)' "$ERRFILE"; then
    {
      echo "OSA-TCC-DENIED (exit $STATUS): this pattern (nonzero exit with empty or 'Not authorized' stderr)"
      echo "almost always means macOS Automation/TCC consent is missing for the target app."
      echo "Do NOT retry in a loop. Tell the user to grant it:"
      echo "  System Settings > Privacy & Security > Automation > (this terminal / hermes) > enable the target app"
      echo "or schedule an ATTENDED first run so the one-time consent dialog can be clicked."
    } >&2
  fi
  cat "$ERRFILE" >&2
  exit $STATUS
fi

# Pass through any non-fatal warnings, keep stdout clean for the script's result.
[ -s "$ERRFILE" ] && cat "$ERRFILE" >&2
exit 0
