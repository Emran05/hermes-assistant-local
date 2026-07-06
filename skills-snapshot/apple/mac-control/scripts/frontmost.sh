#!/bin/bash
# frontmost.sh — print the name of the frontmost (active) application.
# Pure read. Used to prove an `open -a` worked, and to check "am I about to
# drive the Hermes dashboard's own approval UI?" (see SKILL.md safety §5).
set -euo pipefail
/usr/bin/osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true'
