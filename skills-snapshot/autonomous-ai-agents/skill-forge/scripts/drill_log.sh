#!/usr/bin/env bash
# drill_log.sh — append structured pass/fail drill records for forged skills.
#
# Usage:
#   drill_log.sh <skill-name> <pass|fail> [iteration] [note...]
#   drill_log.sh --summary [skill-name]     # per-skill trend counts
#
# Records land as JSONL in ${HERMES_HOME:-~/.hermes}/logs/skill-drills.jsonl so
# trends can surface in mind-drill-style reviews.
set -euo pipefail

LOG_DIR="${HERMES_HOME:-$HOME/.hermes}/logs"
LOG_FILE="$LOG_DIR/skill-drills.jsonl"

if [ "${1:-}" = "--summary" ]; then
  FILTER="${2:-}"
  if [ ! -f "$LOG_FILE" ]; then
    echo "no drill records yet ($LOG_FILE)"
    exit 0
  fi
  python3 - "$LOG_FILE" "$FILTER" <<'PY'
import json, sys, collections
path, flt = sys.argv[1], sys.argv[2]
counts = collections.defaultdict(lambda: {"pass": 0, "fail": 0, "last": ""})
with open(path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        s = r.get("skill", "?")
        if flt and s != flt:
            continue
        res = r.get("result", "?")
        if res in ("pass", "fail"):
            counts[s][res] += 1
        counts[s]["last"] = "%s@%s" % (res, r.get("ts", ""))
for s in sorted(counts):
    c = counts[s]
    print("%-30s pass=%-3d fail=%-3d last=%s" % (s, c["pass"], c["fail"], c["last"]))
PY
  exit 0
fi

if [ $# -lt 2 ]; then
  echo "usage: drill_log.sh <skill-name> <pass|fail> [iteration] [note...]" >&2
  echo "       drill_log.sh --summary [skill-name]" >&2
  exit 2
fi

SKILL="$1"
RESULT="$2"
ITER="${3:-1}"
shift $(( $# > 3 ? 3 : $# ))
NOTE="${*:-}"

case "$RESULT" in
  pass|fail) ;;
  *) echo "result must be 'pass' or 'fail' (got: $RESULT)" >&2; exit 2 ;;
esac

mkdir -p "$LOG_DIR"

SKILL="$SKILL" RESULT="$RESULT" ITER="$ITER" NOTE="$NOTE" python3 - <<'PY' >> "$LOG_FILE"
import json, os, datetime
print(json.dumps({
    "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "skill": os.environ["SKILL"],
    "result": os.environ["RESULT"],
    "iteration": int(os.environ.get("ITER") or 1),
    "note": os.environ.get("NOTE", ""),
}, ensure_ascii=False))
PY

echo "logged: $SKILL $RESULT (iteration $ITER) -> $LOG_FILE"
