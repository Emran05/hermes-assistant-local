#!/bin/bash
# grab.sh — read-only screen capture helper for the screen-oracle skill.
# Saves timestamped PNGs into ~/.hermes/cache/screen-oracle/ and prints the path.
# NEVER clicks, types, or modifies anything. Auto-cleans captures older than 1 hour.
#
# Usage:
#   grab.sh full [display_number]      # whole screen (display 1 by default)
#   grab.sh region X,Y,W,H             # rectangle in screen points
#   grab.sh window "App Name"          # front window of a named app (bounds via System Events)
#   grab.sh frontmost                  # front window of the frontmost app
#   grab.sh list-windows               # list visible apps + window titles + bounds (no capture)
#   grab.sh clean                      # delete ALL cached captures now
#
# Exit codes: 0 ok, 1 usage, 2 capture failed, 3 window lookup failed.

set -u

CACHE="${HERMES_HOME:-$HOME/.hermes}/cache/screen-oracle"
mkdir -p "$CACHE"

# Hygiene: purge anything older than 60 minutes on every invocation.
find "$CACHE" -type f -name '*.png' -mmin +60 -delete 2>/dev/null

TS="$(date +%Y%m%d-%H%M%S)-$$"
CMD="${1:-}"

die() { echo "grab.sh: $*" >&2; exit "${2:-1}"; }

check_output() {
  # screencapture exits 0 even when TCC denies it; verify a real file exists.
  local f="$1"
  if [ ! -s "$f" ]; then
    die "capture produced no file — Screen Recording permission likely missing for this context. Fall back to the computer_use screenshot tool." 2
  fi
  # Warn (do not fail) if the image is suspiciously tiny (possible black/empty frame).
  local bytes
  bytes=$(stat -f%z "$f")
  if [ "$bytes" -lt 20000 ]; then
    echo "WARN: capture is only ${bytes} bytes — may be a black/empty frame (TCC-denied context). Verify with vision_analyze; if black, use the computer_use screenshot tool instead." >&2
  fi
  echo "$f"
}

front_app() {
  osascript -e 'tell application "System Events" to get name of first process whose frontmost is true' 2>/dev/null
}

window_bounds() {
  # Prints "x, y, w, h" (points) of window 1 of the named process, or nothing on failure.
  local app="$1"
  osascript 2>/dev/null <<EOF
tell application "System Events"
  tell (first process whose name is "$app")
    set {x, y} to position of window 1
    set {w, h} to size of window 1
  end tell
end tell
return (x as text) & "," & (y as text) & "," & (w as text) & "," & (h as text)
EOF
}

case "$CMD" in
  full)
    DISP="${2:-1}"
    OUT="$CACHE/full-${TS}.png"
    if [ "$DISP" = "1" ]; then
      screencapture -x "$OUT" || die "screencapture failed" 2
    else
      # -D selects the display number (1-based).
      screencapture -x -D "$DISP" "$OUT" || die "screencapture failed" 2
    fi
    check_output "$OUT"
    ;;

  region)
    RECT="${2:-}"
    [ -n "$RECT" ] || die "usage: grab.sh region X,Y,W,H"
    OUT="$CACHE/region-${TS}.png"
    screencapture -x -R "$RECT" "$OUT" || die "screencapture failed" 2
    check_output "$OUT"
    ;;

  window)
    APP="${2:-}"
    [ -n "$APP" ] || die "usage: grab.sh window \"App Name\""
    BOUNDS="$(window_bounds "$APP")"
    [ -n "$BOUNDS" ] || die "could not read window bounds for \"$APP\" — is it running and does this terminal have Accessibility/Automation permission for System Events?" 3
    SAFE_APP="$(echo "$APP" | tr -c 'A-Za-z0-9' '_' )"
    OUT="$CACHE/window-${SAFE_APP}-${TS}.png"
    screencapture -x -R "$BOUNDS" "$OUT" || die "screencapture failed" 2
    check_output "$OUT"
    ;;

  frontmost)
    APP="$(front_app)"
    [ -n "$APP" ] || die "could not determine frontmost app (System Events permission?)" 3
    exec "$0" window "$APP"
    ;;

  list-windows)
    osascript 2>/dev/null <<'EOF'
tell application "System Events"
  set out to ""
  repeat with p in (every process whose visible is true)
    set pname to name of p
    try
      repeat with w in windows of p
        set {x, y} to position of w
        set {ww, hh} to size of w
        set t to ""
        try
          set t to title of w
        end try
        set out to out & pname & " | " & t & " | " & x & "," & y & "," & ww & "," & hh & linefeed
      end repeat
    end try
  end repeat
end tell
return out
EOF
    ;;

  clean)
    rm -f "$CACHE"/*.png 2>/dev/null
    echo "cleaned $CACHE"
    ;;

  *)
    die "usage: grab.sh {full [n] | region X,Y,W,H | window \"App\" | frontmost | list-windows | clean}"
    ;;
esac
