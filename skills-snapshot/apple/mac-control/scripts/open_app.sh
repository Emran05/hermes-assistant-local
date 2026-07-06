#!/bin/bash
# open_app.sh — launch a Mac app (optionally at a URL) with `open -a`.
#
# Low-risk, but hardened against flag/argument injection:
#   * app name must NOT start with '-' (blocks `open -a "-FooBar"` style flags)
#   * optional URL must be http(s):// ONLY (no file://, no custom schemes, no
#     `-` flags) — so this can't be turned into an arbitrary-URL / file opener
#   * runs `open` with `--` so nothing after it is parsed as an option
#
# Usage:
#   open_app.sh "Google Chrome"
#   open_app.sh "Google Chrome" "https://www.youtube.com/results?search_query=lofi"
#   open_app.sh "Notes"
#
# This does NOT click anything. To drive the app after it opens, prefer the
# osascript-cookbook recipes; fall back to computer_use only for non-scriptable
# pixel UIs (every computer_use action is recorded + irreversible-marked).
set -euo pipefail

APP="${1:-}"
URL="${2:-}"

if [ -z "$APP" ]; then
  echo "open_app: usage: open_app.sh \"App Name\" [https://url]" >&2
  exit 2
fi
case "$APP" in
  -*) echo "open_app: refusing app name starting with '-' (flag-injection guard)" >&2; exit 2 ;;
esac

if [ -n "$URL" ]; then
  case "$URL" in
    http://*|https://*) : ;;
    *) echo "open_app: URL must be http(s):// only (got: $URL)" >&2; exit 2 ;;
  esac
  /usr/bin/open -a "$APP" -- "$URL"
  echo "Opened $APP at $URL"
else
  /usr/bin/open -a "$APP"
  echo "Opened $APP"
fi
