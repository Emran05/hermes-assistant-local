#!/usr/bin/env bash
# Hermes Assistant test runner.
#
#   tools/tests/run.sh [unit|live|browser|ac|all]     (default: unit)
#
# Tiers
#   unit     pure python3/node over the modules, stubbed globals, throwaway
#            HOMEs.  No dashboard, no network, no model — and that is ENFORCED,
#            not assumed: the tier runs with lib/usercustomize.py loaded and
#            HERMES_TESTS_OFFLINE=1, which makes the dashboard/model/serve ports
#            unreachable for the whole tier.  This is the tier CI runs.
#   live     needs the dashboard on 127.0.0.1:7788.  Read-only or
#            capture-and-restore; must never wake a model.
#   browser  Playwright against the running dashboard.  Skipped cleanly when
#            Playwright is not installed.
#   ac       anything that loads a model.  Refused unless the Mac is on AC
#            power, and never run in CI.
#
# Stdlib + bash only, like the rest of this repo.  Every suite prints its own
# per-check lines and one final line in the form
#     TESTS <n> passed <n> failed
# which is what this runner reads.  A suite that prints a line starting with
# SKIP and exits 0 is reported as skipped.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
export HERMES_REPO="$ROOT"

TIER="${1:-unit}"
BASE="${HERMES_DASH_BASE:-http://127.0.0.1:7788}"
MODEL_PROCS='mlx-vlm-launc[h]|mlx_lm[ ]server'   # bracketed so pgrep -f never matches this script's own command line

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
die()  { printf '\n%s\n' "$*" >&2; exit 1; }

case "$TIER" in
  unit|live|browser|ac|all) ;;
  -h|--help|help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "unknown tier '$TIER' — use unit | live | browser | ac | all" ;;
esac

# --------------------------------------------------------------------------
# preflight
# --------------------------------------------------------------------------
dashboard_up() { curl -fsS -o /dev/null -m 5 "$BASE/api/models" 2>/dev/null; }

playwright_python() {
  # $PWENV=/path/to/venv wins; otherwise any python3 that can import playwright.
  if [ -n "${PWENV:-}" ] && [ -x "$PWENV/bin/python" ] \
     && "$PWENV/bin/python" -c 'import playwright' 2>/dev/null; then
    printf '%s' "$PWENV/bin/python"; return 0
  fi
  if python3 -c 'import playwright' 2>/dev/null; then
    printf '%s' "python3"; return 0
  fi
  return 1
}

PW_HINT='Playwright is not installed.  Either point PWENV at a venv that has it:
    PWENV=/path/to/venv tools/tests/run.sh browser
or build one:
    python3 -m venv /tmp/pwenv && /tmp/pwenv/bin/pip install playwright \
      && /tmp/pwenv/bin/playwright install chromium'

MODELS_BEFORE="$(pgrep -f "$MODEL_PROCS" | tr '\n' ' ')"

# --------------------------------------------------------------------------
# one tier
# --------------------------------------------------------------------------
TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_SUITES=()
ROWS=()          # "tier|suite|pass|fail|status|secs"

run_tier() {
  local tier="$1" dir="$HERE/$1" py="${2:-python3}"
  local f name cmd rc secs line p fl status log
  local files=()

  [ -d "$dir" ] || { echo "  (no $tier/ directory)"; return 0; }
  while IFS= read -r f; do files+=("$f"); done < <(
    find "$dir" -maxdepth 1 -type f \
      \( -name '*.py' -o -name '*.js' -o -name '*.mjs' -o -name '*.sh' \) \
      ! -name '_*' | sort)

  if [ ${#files[@]} -eq 0 ]; then
    echo "  (no suites in $tier/)"
    return 0
  fi

  for f in "${files[@]}"; do
    name="$(basename "$f")"
    case "$name" in
      *.py)  cmd=("$py" "$f") ;;
      *.mjs|*.js) cmd=(node "$f") ;;
      *.sh)  cmd=(bash "$f") ;;
      *) continue ;;
    esac

    log="$(mktemp -t hermes-test-XXXXXX)"
    printf '\n\033[1m── %s/%s\033[0m\n' "$tier" "$name"
    local t0=$SECONDS
    "${cmd[@]}" 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    secs=$((SECONDS - t0))

    line="$(grep -E '^TESTS [0-9]+ passed [0-9]+ failed' "$log" | tail -1)"
    if [ -n "$line" ]; then
      p="$(printf '%s' "$line" | awk '{print $2}')"
      fl="$(printf '%s' "$line" | awk '{print $4}')"
    else
      p=0; fl=0
    fi

    if grep -qE '^SKIP' "$log" && [ "$rc" -eq 0 ]; then
      status="SKIP"
    elif [ -z "$line" ]; then
      # no canonical summary line at all: a suite that silently produced zero
      # assertions must never count as PASS, whatever its exit code was.
      status="FAIL"
      fl=1
      FAILED_SUITES+=("$tier/$name (no TESTS summary line)")
    elif [ "$rc" -eq 0 ] && [ "$fl" -eq 0 ]; then
      status="PASS"
    else
      status="FAIL"
      [ "$fl" -eq 0 ] && fl=1          # a crash before the summary line
      FAILED_SUITES+=("$tier/$name")
    fi

    TOTAL_PASS=$((TOTAL_PASS + p))
    TOTAL_FAIL=$((TOTAL_FAIL + fl))
    ROWS+=("$tier|$name|$p|$fl|$status|${secs}s")
    rm -f "$log"
  done
}

# --------------------------------------------------------------------------
# tiers
# --------------------------------------------------------------------------
SKIPPED_TIERS=()

do_unit() {
  bold "== unit =="
  local pp_before="${PYTHONPATH:-}"
  local no_before="${NODE_OPTIONS:-}"
  export HERMES_TESTS_OFFLINE=1
  export PYTHONPATH="$HERE/lib${PYTHONPATH:+:$PYTHONPATH}"
  # a closed port, for anything that reads a base URL from the environment
  export HERMES_DASH_BASE="http://127.0.0.1:9"
  export HERMES_MCP_DASHBOARD="http://127.0.0.1:9"
  # Node has no usercustomize equivalent, so preload the same guard's JS
  # sibling — see lib/offline-guard.js and the README's "node guard" note.
  export NODE_OPTIONS="--require $HERE/lib/offline-guard.js${no_before:+ $no_before}"
  if python3 -c 'import usercustomize,sys; sys.exit(0 if getattr(usercustomize,"HERMES_OFFLINE_GUARD",False) else 1)' 2>/dev/null; then
    echo "  offline guard active (ports ${HERMES_TESTS_BLOCKED_PORTS:-7788,8080,9119} refused)"
  else
    echo "  WARNING: this python3 did not load tools/tests/lib/usercustomize.py —"
    echo "           the unit tier is running WITHOUT the offline guard."
    if [ "${CI:-}" = "true" ] || [ "${HERMES_TESTS_STRICT:-}" = "1" ]; then
      die "the offline guard did not load — refusing to run the unit tier
  unguarded (CI=true or HERMES_TESTS_STRICT=1 requires it).  Check that
  tools/tests/lib is really on PYTHONPATH and that no other sitecustomize/
  usercustomize shadows it."
    fi
  fi
  if node -e 'require(process.argv[1])' "$HERE/lib/offline-guard.js" 2>/dev/null; then
    echo "  node offline guard preloaded (http/https/fetch refused on the same ports)"
  else
    echo "  WARNING: node did not load tools/tests/lib/offline-guard.js —"
    echo "           Node unit suites are running WITHOUT the offline guard."
    if [ "${CI:-}" = "true" ] || [ "${HERMES_TESTS_STRICT:-}" = "1" ]; then
      die "the node offline guard did not load — refusing to run the unit
  tier unguarded (CI=true or HERMES_TESTS_STRICT=1 requires it)."
    fi
  fi
  run_tier unit
  unset HERMES_TESTS_OFFLINE HERMES_DASH_BASE HERMES_MCP_DASHBOARD
  if [ -n "$pp_before" ]; then export PYTHONPATH="$pp_before"; else unset PYTHONPATH; fi
  if [ -n "$no_before" ]; then export NODE_OPTIONS="$no_before"; else unset NODE_OPTIONS; fi
}

do_live() {
  bold "== live =="
  if ! dashboard_up; then
    if [ "$TIER" = "all" ]; then
      echo "  SKIP: the dashboard is not answering on $BASE"
      SKIPPED_TIERS+=("live (dashboard down)")
      return 0
    fi
    die "the live tier needs the dashboard: no answer from $BASE/api/models
Start it with  launch-dashboard.sh  (or launchctl kickstart -k gui/\$UID/com.hermes.dashboard)."
  fi
  echo "  dashboard is up at $BASE"
  echo "  every suite here must put back any owner setting it touches."
  run_tier live
}

do_browser() {
  bold "== browser =="
  local py
  if ! py="$(playwright_python)"; then
    echo "  SKIP: $PW_HINT"
    SKIPPED_TIERS+=("browser (no playwright)")
    return 0
  fi
  if ! dashboard_up; then
    if [ "$TIER" = "all" ]; then
      echo "  SKIP: the dashboard is not answering on $BASE"
      SKIPPED_TIERS+=("browser (dashboard down)")
      return 0
    fi
    die "the browser tier needs the dashboard: no answer from $BASE/api/models"
  fi
  echo "  playwright python: $py"
  run_tier browser "$py"
}

do_ac() {
  bold "== ac =="
  local ps
  ps="$(pmset -g ps 2>/dev/null | head -1)"
  case "$ps" in
    *"AC Power"*) : ;;
    *) die "REFUSED: the ac tier loads a model and this Mac is on battery.
  pmset says: ${ps:-unknown}
Plug in and run it again — and never run this tier in CI." ;;
  esac
  echo "  on AC power: $ps"
  run_tier ac
}

case "$TIER" in
  unit)    do_unit ;;
  live)    do_live ;;
  browser) do_browser ;;
  ac)      do_ac ;;
  all)     do_unit; do_live; do_browser ;;
esac

# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------
printf '\n'
bold "════════════════════════════ summary ════════════════════════════"
printf '%-8s %-24s %6s %6s  %-5s %s\n' TIER SUITE PASS FAIL "" TIME
for r in "${ROWS[@]:-}"; do
  [ -n "$r" ] || continue
  IFS='|' read -r t n p f s d <<<"$r"
  printf '%-8s %-24s %6s %6s  %-5s %s\n' "$t" "$n" "$p" "$f" "$s" "$d"
done
printf '%-8s %-24s %6s %6s\n' "" "TOTAL" "$TOTAL_PASS" "$TOTAL_FAIL"

for s in "${SKIPPED_TIERS[@]:-}"; do
  [ -n "$s" ] && echo "skipped tier: $s"
done

MODELS_AFTER="$(pgrep -f "$MODEL_PROCS" | tr '\n' ' ')"
if [ "$TIER" != "ac" ] && [ "$MODELS_BEFORE" != "$MODELS_AFTER" ]; then
  echo
  echo "!! a model server appeared during this run — that is a bug in a test."
  echo "   before: [${MODELS_BEFORE:-none}]   after: [${MODELS_AFTER:-none}]"
  TOTAL_FAIL=$((TOTAL_FAIL + 1))
  FAILED_SUITES+=("model-wake guard")
fi

if [ ${#FAILED_SUITES[@]} -gt 0 ]; then
  echo
  echo "FAILED: ${FAILED_SUITES[*]}"
  exit 1
fi
echo
echo "all green"
exit 0
