#!/usr/bin/env bash
# think.sh — reach Claude for HEAVY THINKING via the Hermes hub bridge.
# Usage: think.sh <task> [context] [quick|deep]
#   <task>     the hard question or job (required)
#   [context]  compact You-Model (goals / now / looking-for / people) — optional
#   [depth]    quick (Sonnet, default) or deep (Opus) — optional
#
# Prints Claude's reasoning to stdout. On a bridge refusal (code-gen / harmful)
# it prints the refusal message and exits non-zero. Requires the dashboard hub
# to be up (POST /api/claude/think). Reasoning only — Claude cannot act here.
set -u

HUB="http://127.0.0.1:7788"
TASK="${1:-}"
CONTEXT="${2:-}"
DEPTH="${3:-quick}"

if [ -z "$TASK" ]; then
  echo "usage: think.sh <task> [context] [quick|deep]" >&2
  exit 2
fi

# Build the JSON body safely (python handles all escaping; no jq dependency).
BODY="$(TASK="$TASK" CONTEXT="$CONTEXT" DEPTH="$DEPTH" python3 - <<'PY'
import json, os
print(json.dumps({
    "task": os.environ["TASK"],
    "context": os.environ.get("CONTEXT", ""),
    "depth": os.environ.get("DEPTH", "quick") or "quick",
}))
PY
)"

RESP="$(curl -s --max-time 620 -X POST "$HUB/api/claude/think" \
             -H 'Content-Type: application/json' -d "$BODY")"

if [ -z "$RESP" ]; then
  echo "(bridge unreachable — is the dashboard up at $HUB ?)" >&2
  exit 1
fi

# Pretty-print: emit .text, exit non-zero if not ok / refused.
RESP="$RESP" python3 - <<'PY'
import json, os, sys
try:
    d = json.loads(os.environ["RESP"])
except Exception as e:
    sys.stderr.write("bad response from bridge: %s\n" % e)
    sys.exit(1)
text = d.get("text") or d.get("error") or "(no text)"
sys.stdout.write(text.rstrip() + "\n")
if not d.get("ok"):
    if d.get("refused"):
        sys.stderr.write("[refused: %s]\n" % d.get("reason"))
    sys.exit(1)
PY
