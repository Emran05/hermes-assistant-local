#!/usr/bin/env bash
# ttft_after_wake.sh — how long the FIRST real turn takes after the primary
# model server wakes from an idle-suspend.
#
# Promoted from the session-scratch ttft_after_wake_v2.sh that measured backlog
# #1 (prewarm after wake): 29.1 s to first token before, 1.7 s after. It walks
# the exact path a user takes — POST /api/agent/wake, wait for :8080, wait for
# the prewarm turn to finish, then send ONE real /api/chat turn and time the
# first token — and reports four numbers:
#
#   wake->online   launchd bootstrap + weight load
#   prewarm        the synthetic turn that pays the ~18k-token cold prefill
#   first token    what the human actually waits for, AFTER prewarm
#   turn total     end of that turn
#
# THE PREWARM BARRIER IS THE POINT. agent_wake() kicks the prewarm turn the
# instant /v1/models answers; firing the real turn ~1 s later would race that
# ~25 s prefill and measure a cold turn again. So we wait until GET
# /api/agent/prewarm reports a finished run (last_result non-null) and only then
# send the turn — that is the state a user is in when they wake the hub and
# start typing.
#
# BATTERY RULE (CLAUDE.md, docs/plans/post-v1-backlog.md): the model services
# are on-demand. This script REFUSES to run off AC power, and unless
# --no-unload it boots the lane back out afterwards and leaves the
# idle-suspend marker so the next real turn wakes it transparently.
set -euo pipefail

TAG="after"
DASH="${HERMES_DASH_URL:-http://127.0.0.1:${DASH_PORT:-7788}}"
MODEL_URL="http://127.0.0.1:${MLX_PORT:-8080}/v1/models"
OUT="${HOME}/.hermes/bench/results.jsonl"
DASH_DIR="${HOME}/.hermes/dashboard"
LABEL="com.hermes.mlx-server"
FORCE_AC=0
UNLOAD=1
DRY=0

usage() {
  cat <<'USAGE'
Usage: tools/bench/ttft_after_wake.sh [options]

Measures first-token latency for the first real turn after the primary model
server wakes, with the prewarm barrier in between.

Options:
  --tag NAME        label for this run (default "after"; use "before"/"after"
                    when A/B-ing a prewarm change)
  --dash URL        dashboard base URL (default $HERMES_DASH_URL, else
                    http://127.0.0.1:${DASH_PORT:-7788})
  --out FILE        JSONL results file (default ~/.hermes/bench/results.jsonl)
  --i-am-on-ac      skip the pmset AC check (only when pmset is unavailable)
  --no-unload       leave the model loaded when finished (you then own the
                    battery rule: launchctl bootout gui/$(id -u)/com.hermes.mlx-server)
  --dry-run         print the plan and exit; wakes nothing, sends nothing
  -h, --help        this message

AC POWER ONLY. This wakes an ~19 GB model. Run it plugged in, once, and let it
unload afterwards.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:?--tag needs a value}"; shift 2 ;;
    --dash) DASH="${2:?--dash needs a value}"; shift 2 ;;
    --out) OUT="${2:?--out needs a value}"; shift 2 ;;
    --i-am-on-ac) FORCE_AC=1; shift ;;
    --no-unload) UNLOAD=0; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 64 ;;
  esac
done

now() { python3 -c 'import time;print(time.time())'; }
jget() { python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
k=sys.argv[1]
v=d.get(k)
print("" if v is None else v)' "$1" 2>/dev/null || true; }

echo "dashboard   : $DASH"
echo "model probe : $MODEL_URL"
echo "results     : $OUT"
echo "tag         : $TAG"

if [ "$DRY" = 1 ]; then
  cat <<DRYRUN

[dry-run] would, in order:
  1. refuse if a model server is already resident (this measures a COLD wake)
  2. refuse unless pmset reports AC power
  3. POST $DASH/api/agent/prewarm {"enabled":true}
  4. POST $DASH/api/agent/wake and poll $MODEL_URL until it answers
  5. poll GET $DASH/api/agent/prewarm until last_result is non-null (barrier)
  6. POST $DASH/api/chat "Reply with exactly: awake" and poll
     $DASH/api/chat/poll?job=... for the first token, then to done
  7. print wake->online / prewarm / first token / turn total, append one JSON
     line to $OUT, delete the bench chat session
  8. launchctl bootout gui/$(id -u)/$LABEL, write the idle-suspend marker
[dry-run] nothing was woken, sent or written.
DRYRUN
  exit 0
fi

# --- guards -----------------------------------------------------------------
if pgrep -f 'mlx_lm server|mlx-vlm-launch' >/dev/null 2>&1; then
  echo "a model server is already running — this measures a COLD wake; stop it first:" >&2
  echo "  launchctl bootout gui/$(id -u)/$LABEL" >&2
  exit 2
fi
if [ "$FORCE_AC" != 1 ]; then
  if ! pmset -g batt 2>/dev/null | grep -q "AC Power"; then
    echo "not on AC power — refusing (battery rule). Plug in, or pass --i-am-on-ac." >&2
    exit 2
  fi
fi

SES="ttft-bench-$(date +%s)"

# prewarm must be ON and idle before we start, or the barrier below never trips.
curl -s -m 10 -X POST "$DASH/api/agent/prewarm" -H 'Content-Type: application/json' \
     -d '{"enabled":true}' >/dev/null || true

t0="$(now)"
curl -s -m 200 -X POST "$DASH/api/agent/wake" -H 'Content-Type: application/json' \
     -d '{}' >/dev/null || true
for _ in $(seq 1 120); do
  if curl -s -m 2 -o /dev/null "$MODEL_URL"; then break; fi
  sleep 1
done
t1="$(now)"

# --- barrier: wait for the prewarm turn to finish (max ~180 s) ---------------
PW_RESULT=""; PW_MS=""
for _ in $(seq 1 360); do
  body="$(curl -s -m 5 "$DASH/api/agent/prewarm" || true)"
  PW_RESULT="$(printf '%s' "$body" | jget last_result)"
  PW_MS="$(printf '%s' "$body" | jget last_ms)"
  [ -n "$PW_RESULT" ] && break
  sleep 0.5
done
t2="$(now)"

# --- the real turn ----------------------------------------------------------
job="$(curl -s -m 30 -X POST "$DASH/api/chat" -H 'Content-Type: application/json' \
       -d "{\"message\":\"Reply with exactly: awake\",\"session\":\"$SES\"}" | jget job)"
if [ -z "$job" ]; then echo "no job id from /api/chat" >&2; exit 1; fi

tf=""
for _ in $(seq 1 600); do
  r="$(curl -s -m 5 "$DASH/api/chat/poll?job=$job" || true)"
  txt="$(printf '%s' "$r" | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
print(len((d.get("text") or "").strip()))' 2>/dev/null || echo 0)"
  fin="$(printf '%s' "$r" | jget done)"
  if [ "${txt:-0}" -gt 0 ] && [ -z "$tf" ]; then tf="$(now)"; fi
  [ "$fin" = "True" ] && break
  sleep 0.25
done
t3="$(now)"

mkdir -p "$(dirname "$OUT")"
python3 - "$TAG" "$t0" "$t1" "$t2" "${tf:-0}" "$t3" "${PW_RESULT:-}" "${PW_MS:-}" "$OUT" <<'PY'
import json, sys, time
tag = sys.argv[1]
t0, t1, t2, tf, t3 = map(float, sys.argv[2:7])
res, ms, out = sys.argv[7], sys.argv[8], sys.argv[9]
ms = float(ms) if ms else None
rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "script": "ttft_after_wake", "tag": tag,
       "wake_online_s": round(t1 - t0, 2),
       "prewarm_result": res or None,
       "prewarm_s": round(ms / 1000.0, 2) if ms else None,
       "prewarm_wall_s": round(t2 - t1, 2),
       "first_token_after_prewarm_s": round(tf - t2, 2) if tf else None,
       "turn_total_s": round(t3 - t2, 2)}
print("[%s] wake->online %.1fs | prewarm %s %s (wall %.1fs) | first token after prewarm %s | turn total %.1fs"
      % (tag, rec["wake_online_s"], rec["prewarm_result"],
         ("%.1fs" % rec["prewarm_s"]) if rec["prewarm_s"] else "n/a",
         rec["prewarm_wall_s"],
         ("%.1fs" % rec["first_token_after_prewarm_s"]) if rec["first_token_after_prewarm_s"] else "n/a",
         rec["turn_total_s"]))
with open(out, "a") as f:
    f.write(json.dumps(rec) + "\n")
print("appended 1 line to " + out)
PY

curl -s -m 10 -X POST "$DASH/api/sessions/delete" -H 'Content-Type: application/json' \
     -d "{\"session\":\"$SES\"}" >/dev/null || true

# --- battery rule: put the lane back to sleep -------------------------------
if [ "$UNLOAD" = 1 ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  sleep 3
  printf '%s' "$(date +%s)" > "$DASH_DIR/agent-idle-suspended"
  rm -f "$DASH_DIR/agent-paused" 2>/dev/null || true
  if pgrep -f 'mlx_lm server|mlx-vlm-launch' >/dev/null 2>&1; then
    echo "WARN: a model server is still running"
  else
    echo "model unloaded; idle marker written (next real turn wakes it)"
  fi
else
  echo "--no-unload: the model is still loaded. Battery rule:"
  echo "  launchctl bootout gui/$(id -u)/$LABEL"
fi
