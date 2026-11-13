#!/usr/bin/env bash
# Hermes Assistant test runner.
#
#   tools/tests/run.sh [unit|live|browser|ac|all]     (default: unit)
#
# Tiers
#   unit     pure python3/node over the modules, stubbed globals, throwaway
#            HOMEs.  No dashboard, no network, no model — and that is ENFORCED,
#            not assumed: the tier runs with lib/usercustomize.py loaded and
#            HERMES_TESTS_OFFLINE=1, which makes every non-loopback address
#            and the dashboard/model/serve ports unreachable for every Python
#            and Node process that inherits the tier's environment, and
#            GIT_ALLOW_PROTOCOL=file for git.  A child started with a
#            stripped environment (python -I, env={"PATH": ...}, gh_git.py's
#            own git env) is NOT guarded — see do_unit.  This is the tier CI
#            runs.
#   live     needs the dashboard on 127.0.0.1:7788.  Read-only or
#            capture-and-restore; must never wake a model.
#   browser  Playwright against the running dashboard.  Skipped cleanly when
#            Playwright is not installed.
#   ac       anything that loads a model.  Refused unless the Mac is on AC
#            power, and never run in CI.
#
# Stdlib + bash only, like the rest of this repo.  Every suite prints its own
# per-check lines and one final line in the form
#     TESTS <n> passed <n> failed            (optionally: ... <n> skipped)
# which is what this runner reads.  A check that cannot run here prints a line
# "SKIP <reason>" (any indent); a whole suite that cannot run prints "SKIP ..."
# at column 0 with no passing checks and exits 0.  Skips are listed and
# counted, never credited as passes; HERMES_TESTS_FAIL_ON_SKIP=1 (CI) fails any
# skip not on HERMES_TESTS_EXPECTED_SKIPS.  A suite with no summary line, or
# "TESTS 0 passed", is a FAIL.  See tools/tests/README.md.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
export HERMES_REPO="$ROOT"

TIER="${1:-unit}"
BASE="${HERMES_DASH_BASE:-http://127.0.0.1:7788}"

# The model-wake guard.  It used to pgrep the pattern system-wide, so another
# agent's shell, a grep or an editor whose command line merely mentioned
# "mlx_lm server" failed the run (R2-61).  Now: REAL launchers only — a python
# whose script is mlx-vlm-launch.py (not its --local-ready probe) or that runs
# `-m mlx_lm server` — recorded as pid@start-time pairs (a recycled PID is a
# different pair).  It still trips in EITHER direction (tools/tests/README.md):
# a launcher that is new after the run (a test woke or started one) and one
# that was there before and is gone (a test killed a lane) are both test bugs.
model_procs() {
  ps -A -o pid= -o lstart= -o command= 2>/dev/null | python3 -c '
import re, sys
PY = re.compile(r"^(?:\S*/)?[Pp]ython[0-9.]*$")

def script_of(argv):                 # what the interpreter runs, or None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "-c":
            return None              # inline code, not a launcher
        if a == "-m":
            return "-m " + " ".join(argv[i + 1:i + 3])
        if a in ("-X", "-W"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a
    return None

for line in sys.stdin:
    f = line.split(None, 6)
    if len(f) < 7 or not f[0].isdigit():
        continue
    argv = f[6].split()
    if not argv or not PY.match(argv[0]):
        continue                     # a shell, grep, editor... never counts
    s = script_of(argv) or ""
    launch = s.rsplit("/", 1)[-1] == "mlx-vlm-launch.py" and "--local-ready" not in argv
    lm = s == "-m mlx_lm server" or s.split(" ")[:2] == ["-m", "mlx_lm.server"]
    if launch or lm:
        print("%s@%s" % (f[0], "-".join(f[1:6])))
' | sort | tr '\n' ' '
}
new_model_procs() {   # the pairs only $2 has (before/after: new; after/before: gone)
  local x out=""
  for x in $2; do
    case " $1 " in *" $x "*) ;; *) out="$out$x " ;; esac
  done
  printf '%s' "$out"
}

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
die()  { printf '\n%s\n' "$*" >&2; exit 1; }

case "$TIER" in
  unit|live|browser|ac|all) ;;
  -h|--help|help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

MODELS_BEFORE="$(model_procs)"

# --------------------------------------------------------------------------
# one tier
# --------------------------------------------------------------------------
TOTAL_PASS=0
TOTAL_FAIL=0
TOTAL_SKIP=0
FAILED_SUITES=()
EGRESS_NOTES=()
SKIP_ITEMS=()    # "tier/suite|reason"
ROWS=()          # "tier|suite|pass|fail|skip|status|secs"

# Per-suite time limit (E137).  macOS has no timeout(1), so a few lines of
# perl run the suite in its OWN process group, and on expiry TERM (then KILL)
# the whole group — a child that kept the pipe to tee open would otherwise
# hang the run after its parent died.  Generous by default (the slowest suite
# takes ~15 s); HERMES_TESTS_SUITE_TIMEOUT=<seconds> overrides, 0 disables.
SUITE_TIMEOUT="${HERMES_TESTS_SUITE_TIMEOUT:-600}"
case "$SUITE_TIMEOUT" in ''|*[!0-9]*) SUITE_TIMEOUT=600 ;; esac
TIMEOUT_RC=124
TIMEOUT_MARK="TIMEOUT run.sh:"
run_limited() {
  if [ "$SUITE_TIMEOUT" -eq 0 ] || ! command -v perl >/dev/null 2>&1; then
    "$@"; return $?
  fi
  perl -e '
    my ($t, $rc, $mark) = (shift, shift, shift);
    my $pid = fork();
    die "fork: $!\n" unless defined $pid;
    if ($pid == 0) {
      setpgrp(0, 0);                # off the terminal group: never read it
      open(STDIN, "<", "/dev/null");
      exec { $ARGV[0] } @ARGV or exit 127;
    }
    setpgrp($pid, $pid);            # either side may win the race; both agree
    my $grp = sub { my $s = shift; kill $s, -$pid; kill $s, $pid; };
    $SIG{INT}  = sub { $grp->("INT");  waitpid($pid, 0); exit 130; };
    $SIG{TERM} = sub { $grp->("TERM"); waitpid($pid, 0); exit 143; };
    $SIG{ALRM} = sub {
      print "\n$mark the suite ran longer than ${t}s and was stopped\n";
      $grp->("TERM"); sleep 2; $grp->("KILL");
      waitpid($pid, 0); exit $rc;
    };
    alarm $t;
    waitpid($pid, 0);
    my $st = $?;
    alarm 0;
    $grp->("KILL");                 # nothing the suite left behind outlives it
    exit(($st & 127) ? 128 + ($st & 127) : ($st >> 8));
  ' "$SUITE_TIMEOUT" "$TIMEOUT_RC" "$TIMEOUT_MARK" "$@"
}

run_tier() {
  local tier="$1" dir="$HERE/$1" py="${2:-python3}"
  local f name cmd rc secs line p fl status log elog
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
    elog="$(mktemp -t hermes-egress-XXXXXX)"
    printf '\n\033[1m── %s/%s\033[0m\n' "$tier" "$name"
    local t0=$SECONDS
    HERMES_TESTS_SUITE="$name" HERMES_TESTS_EGRESS_LOG="$elog" \
      run_limited "${cmd[@]}" 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    secs=$((SECONDS - t0))
    if [ "$rc" -eq "$TIMEOUT_RC" ] && grep -q "^$TIMEOUT_MARK" "$log"; then
      # a hung suite is a FAIL with its name on it, never a run that waits
      # forever (or burns CI's six-hour default) — E137
      FAILED_SUITES+=("$tier/$name (timed out after ${SUITE_TIMEOUT}s)")
      TOTAL_FAIL=$((TOTAL_FAIL + 1))
      ROWS+=("$tier|$name|0|1|0|FAIL|${secs}s")
      rm -f "$log" "$elog"
      continue
    fi
    # the offline guard logs every refusal here (unit tier only); a refusal is
    # harmless now, but a suite that keeps trying to reach the internet is
    # leaning on something it must not, so say which one
    if [ -s "$elog" ]; then
      EGRESS_NOTES+=("$tier/$name: $(wc -l < "$elog" | tr -d ' ') refused — e.g. $(head -1 "$elog" | cut -d' ' -f3- | cut -c1-90)")
    fi
    rm -f "$elog"

    line="$(grep -E '^TESTS [0-9]+ passed [0-9]+ failed' "$log" | tail -1)"
    p=0; fl=0; sk=0
    if [ -n "$line" ]; then
      p="$(printf '%s' "$line" | awk '{print $2}')"
      fl="$(printf '%s' "$line" | awk '{print $4}')"
      sk="$(printf '%s' "$line" | awk '$7=="skipped"{print $6}')"
    fi
    # Skips are counted, never credited (C83).  A skipped CHECK is a line
    # "SKIP <reason>" (any indent); the optional summary form
    # "TESTS <p> passed <f> failed <s> skipped" wins when it is printed.
    local skiplines nsk
    skiplines="$(grep -E '^[[:space:]]*SKIP([^A-Za-z0-9_]|$)' "$log" \
                 | sed -E 's/^[[:space:]]*SKIP[: ]*//' | cut -c1-160)"
    nsk=0
    [ -n "$skiplines" ] && nsk="$(printf '%s\n' "$skiplines" | grep -c '')"
    [ -n "$sk" ] || sk="$nsk"
    [ "$sk" -ge "$nsk" ] || sk="$nsk"

    if [ "$rc" -eq 0 ] && [ "$fl" -eq 0 ] && [ "$p" -eq 0 ] \
       && grep -qE '^SKIP' "$log"; then
      # the whole suite could not run here (and said so at column 0)
      status="SKIP"
      [ "$sk" -ge 1 ] || sk=1
    elif [ -z "$line" ]; then
      # no canonical summary line at all: a suite that silently produced zero
      # assertions must never count as PASS, whatever its exit code was.
      status="FAIL"
      fl=1
      FAILED_SUITES+=("$tier/$name (no TESTS summary line)")
    elif [ "$rc" -ne 0 ] || [ "$fl" -ne 0 ]; then
      # a SKIP line never hides a failure (it used to win over both)
      status="FAIL"
      [ "$fl" -eq 0 ] && fl=1          # a crash before the summary line
      FAILED_SUITES+=("$tier/$name")
    elif [ "$p" -eq 0 ]; then
      status="FAIL"
      fl=1
      FAILED_SUITES+=("$tier/$name (TESTS 0 passed: no assertion ran)")
    else
      status="PASS"
    fi

    if [ "$sk" -gt 0 ]; then
      if [ -n "$skiplines" ]; then
        while IFS= read -r why; do
          SKIP_ITEMS+=("$tier/$name|$why")
        done <<<"$skiplines"
      else
        SKIP_ITEMS+=("$tier/$name|$sk check(s) skipped (no reason printed)")
      fi
    fi

    TOTAL_PASS=$((TOTAL_PASS + p))
    TOTAL_FAIL=$((TOTAL_FAIL + fl))
    TOTAL_SKIP=$((TOTAL_SKIP + sk))
    ROWS+=("$tier|$name|$p|$fl|$sk|$status|${secs}s")
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
  local nus_before="${PYTHONNOUSERSITE-__unset__}"
  local gap_before="${GIT_ALLOW_PROTOCOL-__unset__}"
  export HERMES_TESTS_OFFLINE=1
  # git is neither Python nor Node, so the guards below never see it: limit
  # every git that inherits this environment to local repositories (E138).
  # NOT covered (no guard reaches them): a child started with a stripped
  # environment — gh_git.py builds git's env from nothing; `python -I` or
  # env={"PATH": ...} children skip usercustomize; a node child without
  # NODE_OPTIONS — and any other binary (curl, ...). Those suites stay offline
  # only through their own fakes (file:// bases, shims on PATH).
  export GIT_ALLOW_PROTOCOL=file
  export PYTHONPATH="$HERE/lib${PYTHONPATH:+:$PYTHONPATH}"
  # the guard is a usercustomize, which PYTHONNOUSERSITE switches off
  unset PYTHONNOUSERSITE
  # a closed port, for anything that reads a base URL from the environment
  export HERMES_DASH_BASE="http://127.0.0.1:9"
  export HERMES_MCP_DASHBOARD="http://127.0.0.1:9"
  # Node has no usercustomize equivalent, so preload the same guard's JS
  # sibling — see lib/offline-guard.js and the README's "node guard" note.
  export NODE_OPTIONS="--require $HERE/lib/offline-guard.js${no_before:+ $no_before}"
  # Both checks run a FRESH interpreter with the tier's environment and ask
  # whether start-up loaded the guard — they never import/require it
  # themselves, which would succeed even when start-up skipped it (C82).
  if python3 "$HERE/lib/guard_selfcheck.py"; then
    echo "  offline guard active (non-loopback refused; ports ${HERMES_TESTS_BLOCKED_PORTS:-7788,8080,8081,9119} refused; git limited to file://)"
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
  if node "$HERE/lib/guard_selfcheck.js"; then
    echo "  node offline guard preloaded (sockets, http/https and fetch refused the same way)"
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
  if [ "$nus_before" != "__unset__" ]; then export PYTHONNOUSERSITE="$nus_before"; fi
  if [ "$gap_before" != "__unset__" ]; then export GIT_ALLOW_PROTOCOL="$gap_before"
  else unset GIT_ALLOW_PROTOCOL; fi
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
Start it with  launchctl kickstart -k gui/\$UID/com.hermes.dashboard  (not
launch-dashboard.sh: it also waits minutes for a model the on-demand gate will
not start — and nothing in the live tier needs a model)."
  fi
  echo "  dashboard is up at $BASE"
  echo "  every suite here must put back any owner setting it touches."
  # the suites must talk to the dashboard checked above: do_unit unsets the
  # variable on its way out, so in `all` a suite would otherwise fall back to
  # its own default (the live :7788) even when $BASE named another one
  export HERMES_DASH_BASE="$BASE"
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
  export HERMES_DASH_BASE="$BASE"     # see do_live
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
printf '%-8s %-24s %6s %6s %6s  %-5s %s\n' TIER SUITE PASS FAIL SKIP "" TIME
for r in "${ROWS[@]:-}"; do
  [ -n "$r" ] || continue
  IFS='|' read -r t n p f k s d <<<"$r"
  printf '%-8s %-24s %6s %6s %6s  %-5s %s\n' "$t" "$n" "$p" "$f" "$k" "$s" "$d"
done
printf '%-8s %-24s %6s %6s %6s\n' "" "TOTAL" "$TOTAL_PASS" "$TOTAL_FAIL" "$TOTAL_SKIP"

# --------------------------------------------------------------------------
# skips: reported, never credited (C83).  With HERMES_TESTS_FAIL_ON_SKIP=1
# (CI sets it) a skip that is not on the HERMES_TESTS_EXPECTED_SKIPS list fails
# the run — otherwise CI quietly runs a smaller suite than the owner's Mac.
# The list is one entry per line (or ';'-separated): "<suite>" matches any
# skip in that suite, "<suite>: <text>" only a reason containing <text>.
# --------------------------------------------------------------------------
skip_expected() {   # $1 = suite basename, $2 = reason
  local entry es et
  while IFS= read -r entry; do
    entry="$(printf '%s' "$entry" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
    [ -n "$entry" ] || continue
    case "$entry" in \#*) continue ;; esac
    es="${entry%%:*}"
    if [ "$es" = "$entry" ]; then et=""; else et="${entry#*:}"; fi
    et="$(printf '%s' "$et" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
    [ "$es" = "$1" ] || continue
    if [ -z "$et" ] || [[ "$2" == *"$et"* ]]; then return 0; fi
  done < <(printf '%s\n' "${HERMES_TESTS_EXPECTED_SKIPS:-}" | tr ';' '\n')
  return 1
}
if [ ${#SKIP_ITEMS[@]} -gt 0 ]; then
  echo
  echo "skipped (${#SKIP_ITEMS[@]}):"
  for it in "${SKIP_ITEMS[@]}"; do
    who="${it%%|*}"; why="${it#*|}"
    if skip_expected "${who##*/}" "$why"; then
      echo "  SKIP $who — $why   (expected)"
    else
      echo "  SKIP $who — $why"
      if [ "${HERMES_TESTS_FAIL_ON_SKIP:-}" = "1" ]; then
        FAILED_SUITES+=("$who (unexpected SKIP: $why)")
      fi
    fi
  done
fi

for s in "${SKIPPED_TIERS[@]:-}"; do
  [ -n "$s" ] && echo "skipped tier: $s"
done
for s in "${EGRESS_NOTES[@]:-}"; do
  [ -n "$s" ] && echo "egress refused by the offline guard: $s"
done

MODELS_AFTER="$(model_procs)"
MODELS_NEW="$(new_model_procs "$MODELS_BEFORE" "$MODELS_AFTER")"
MODELS_GONE="$(new_model_procs "$MODELS_AFTER" "$MODELS_BEFORE")"
if [ "$TIER" != "ac" ] && { [ -n "$MODELS_NEW" ] || [ -n "$MODELS_GONE" ]; }; then
  echo
  echo "!! a model server appeared or went away during this run — that is a bug in a test."
  echo "   new: [${MODELS_NEW% }]   gone: [${MODELS_GONE% }]   before: [${MODELS_BEFORE:-none}]   after: [${MODELS_AFTER:-none}]"
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
