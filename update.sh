#!/usr/bin/env bash
# update.sh — put a newer Hermes Assistant on this Mac.
#
# Two install shapes, auto-detected:
#   git checkout  — fetch tags, check out the target (a release tag on the
#                   `stable` channel, origin/main on `main`).
#   tarball       — download the release's source tarball, verify its SHA256
#                   against SHA256SUMS (REQUIRED — no sums, or no line for this
#                   tarball, is a hard refusal that --force cannot override),
#                   and rsync it over the install directory. The sums come
#                   from the same release as the tarball, so this detects
#                   corruption, not tampering (nothing is signed).
# Then, in both shapes: re-run ./install-services.sh (re-renders the launchd
# plists and restarts the dashboard + serve; the model servers stay ON-DEMAND),
# and report whether app/ changed — replacing the app bundle is a deliberate,
# separate step because a rebuild drops the app's Full Disk Access grant.
#
# Leaves your ~/.hermes DATA alone (chats, memories, settings, .env, state.db).
# What it does write outside the checkout (2026-10-21 audit C69 — this used to
# claim only the first two):
#   ~/.hermes/logs/update.log, ~/.hermes/dashboard/update-state.json and
#   ~/.hermes/dashboard/update.lock (the lock, held for the whole run);
#   on the tarball path, a copy of the install dir as it was, in
#   ~/.hermes/dashboard/update-backups/ (the newest three are kept);
#   the ~/.hermes/plugins/tool-budget symlink back to this checkout;
#   `tool-budget` in plugins.enabled of ~/.hermes/config.yaml, through
#   hermes-plugins/plugin_enable.py (a timestamped config.yaml.bak-* first);
#   and whatever ./install-services.sh writes — the launchd plists in
#   ~/Library/LaunchAgents, the serve token if it is missing, the on-demand
#   markers on a fresh install. It also restarts the Telegram gateway
#   (ai.hermes.gateway) when launchd has it loaded, so it loads the updated
#   plugins.
#
# Safe to run straight from a terminal:
#   ./update.sh                      # newest release on your channel
#   ./update.sh --target v0.4.0      # a specific release
#   ./update.sh --channel main       # track the development branch
#   ./update.sh --dry-run            # print the plan, change nothing
#   ./update.sh --rebuild-app        # also rebuild + install the .app
set -euo pipefail

# Self-update (2026-11-09 audit E29): bash reads this script through an open
# file descriptor, and `git checkout` / rsync replace it with a NEW file, so
# everything after the code swap used to run from the OUTGOING version's text
# (from 1.3.2 that meant approval-gate was never linked or enabled and the
# gateway never restarted). Now, right after the swap, this process execs the
# INCOMING update.sh with --post-update, which runs the plugin links, the
# services, the gateway restart and the summary from the new code. The update
# lock fd (fd 9, or the one the dashboard passed) survives the exec. The marker
# line below says an update.sh speaks that protocol; a target without it (a
# downgrade to an older release) gets the post-update steps from this text.
# hermes-update-post-protocol: 1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Which repo do releases come from? env → the github.com slug on origin (so a
# clone checks the repo it came from) → the public default.
repo_slug() {
  if [ -n "${HERMES_UPDATE_REPO:-}" ] && [ "${HERMES_UPDATE_REPO#*/}" != "$HERMES_UPDATE_REPO" ]; then
    printf '%s\n' "$HERMES_UPDATE_REPO"; return
  fi
  local url slug
  url="$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)"
  slug="$(printf '%s' "$url" | sed -nE 's#^.*github\.com[:/]+([^/]+)/(.+)$#\1/\2#p')"
  slug="${slug%/}"; slug="${slug%.git}"; slug="${slug%/}"
  if [ -n "$slug" ]; then printf '%s\n' "$slug"; return; fi
  printf '%s\n' "Emran05/hermes-assistant-local"
}
REPO="$(repo_slug)"
HERMES_DIR="$HOME/.hermes"
LOG_DIR="$HERMES_DIR/logs"
STATE_DIR="$HERMES_DIR/dashboard"
LOG="$LOG_DIR/update.log"
STATE="$STATE_DIR/update-state.json"
mkdir -p "$LOG_DIR" "$STATE_DIR"

TARGET="latest"
CHANNEL=""
FORCE=0
DRY=0
REBUILD_APP=0
ASSUME_YES=0
# 0 = a normal run; 1 = --post-update (exec'd by the outgoing update.sh after
# the swap: plugins, services, gateway, summary); 2 = --post-update
# --from-install-services (an OLDER update.sh ran our install-services.sh
# after the swap, which asked for the plugin + gateway steps only)
POST_UPDATE=0

usage() {
  awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"
  cat <<'EOF'

Options:
  --target <latest|vX.Y.Z|main>  what to move to           (default: latest)
  --channel <stable|main>        override the saved channel
  --force                        stash local changes instead of refusing
  --yes                          accepted for compatibility (never prompts)
  --dry-run                      print the plan and exit
  --rebuild-app                  also run app/build-app.sh and install the app
  -h, --help                     this text
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target)      TARGET="${2:-latest}"; shift 2 ;;
    --target=*)    TARGET="${1#*=}"; shift ;;
    --channel)     CHANNEL="${2:-}"; shift 2 ;;
    --channel=*)   CHANNEL="${1#*=}"; shift ;;
    --force)       FORCE=1; shift ;;
    --yes|-y)      ASSUME_YES=1; shift ;;
    --dry-run)     DRY=1; shift ;;
    --rebuild-app) REBUILD_APP=1; shift ;;
    --post-update) [ "$POST_UPDATE" = "2" ] || POST_UPDATE=1; shift ;;
    --from-install-services) POST_UPDATE=2; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "update.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# When the dashboard starts us our stdout IS the log file already; from a
# terminal we want both. Either way everything ends up in update.log.
# A --post-update run inherits the outgoing run's stdout, already set up.
if [ "${HERMES_UPDATE_FROM:-}" != "dashboard" ] && [ "$POST_UPDATE" = "0" ]; then
  exec > >(tee -a "$LOG") 2>&1
fi
# One hand-over per update: the post-update run never execs again, and it
# tells install-services.sh not to call back into us (no loop either way).
if [ "$POST_UPDATE" != "0" ]; then
  [ "$DRY" != "1" ] || { echo "update.sh: --post-update cannot be a dry run" >&2; exit 2; }
  export HERMES_UPD_IN_POST=1
fi

TS() { date "+%Y-%m-%d %I:%M:%S %p"; }
say() { echo "[$(TS)] $*"; }
die() { RESULT_MSG="$*"; say "ERROR: $*"; exit 1; }

if [ "$POST_UPDATE" = "0" ]; then
  echo
  say "──────────── update.sh start (target=$TARGET force=$FORCE dry=$DRY) ────────────"
else
  say "post-update steps from the incoming update.sh (mode $POST_UPDATE)"
fi

# ---------------------------------------------------------------------------
# result state — one JSON file the dashboard's /api/update/status reads back
# ---------------------------------------------------------------------------
RESULT_OK=0
RESULT_MSG="interrupted"
FROM_VER="$(tr -d ' \t\n\r' < "$ROOT/VERSION" 2>/dev/null || echo unknown)"
FROM_REF=""
TO_VER="$FROM_VER"
TO_REF=""
APP_CHANGED=0
BACKUP=""          # tarball path: where the pre-update copy of the install went
ROLLBACK_HINT=""   # ...and the one command that puts it back
GATEWAY=""         # restarted | restart_failed | restart_by_hand | not_running | not_needed

write_state() {
  RES_OK="$RESULT_OK" RES_MSG="$RESULT_MSG" RES_TARGET="$TARGET" \
  RES_FROM="$FROM_VER" RES_TO="$TO_VER" RES_FROM_REF="$FROM_REF" \
  RES_TO_REF="$TO_REF" RES_APP="$APP_CHANGED" RES_MODE="${MODE:-unknown}" \
  RES_STATE="$STATE" RES_LOG="$LOG" RES_GATEWAY="$GATEWAY" \
  RES_KEEP_RUNNING="${STATE_KEEP_RUNNING:-0}" \
  python3 - <<'PY' 2>/dev/null || true
import json, os, time
state = os.environ["RES_STATE"]
try:
    with open(state) as f:
        cur = json.load(f)
except Exception:
    cur = {}
if os.environ.get("RES_KEEP_RUNNING") != "1":
    cur["running"] = False
cur["last_result"] = {
    "ok": os.environ["RES_OK"] == "1",
    "message": os.environ["RES_MSG"],
    "target": os.environ["RES_TARGET"],
    "from_version": os.environ["RES_FROM"],
    "to_version": os.environ["RES_TO"],
    "from_ref": os.environ["RES_FROM_REF"],
    "to_ref": os.environ["RES_TO_REF"],
    "app_changed": os.environ["RES_APP"] == "1",
    "mode": os.environ["RES_MODE"],
    "log": os.environ["RES_LOG"],
    "gateway": os.environ["RES_GATEWAY"],
    "finished_at": time.time(),
}
tmp = state + ".tmp"
with open(tmp, "w") as f:
    json.dump(cur, f, indent=1)
os.replace(tmp, state)
PY
}

# cleanup() is redefined by the tarball path, which owns a temp dir. on_exit
# reads $? FIRST and only then runs it: the tarball path used to re-trap with
# 'cleanup; on_exit', so the rc on_exit saw was cleanup's own (0) and every
# tarball failure, a SHA256 refusal included, exited 0.
cleanup() { :; }
on_exit() {
  rc=$?
  cleanup || true
  if [ "$DRY" = "1" ]; then exit $rc; fi
  # called from an older update.sh's install-services.sh step: that script
  # owns update-state.json and will write it when it finishes
  if [ "$POST_UPDATE" = "2" ]; then exit $rc; fi
  if [ $rc -ne 0 ] && [ "$RESULT_OK" = "1" ]; then RESULT_OK=0; fi
  write_state
  exit $rc
}
trap on_exit EXIT

# ---------------------------------------------------------------------------
# process lock (2026-09-10 audit A08) — this script had no lock of its own,
# so a terminal-run update.sh and a dashboard-triggered one (or two of
# either) could race over git, installed files, services, logs and this
# very state file. The dashboard (dashboard/aux_update.py _upd_apply) takes
# an fcntl.flock on update.lock BEFORE spawning us and hands this process the
# already-locked fd, so there is nothing to do here in that case — a second,
# independent flock attempt from THIS SAME process tree on a freshly opened
# fd to the same file would only fail against the one the dashboard already
# holds. A bare terminal run has no such parent, so it takes the identical
# lock itself, right here, on fd 9, for this script's entire remaining
# lifetime — released automatically the moment this process and everything
# it has exec'd (git, curl, rsync, install-services.sh...) finally exits.
# --dry-run changes nothing on disk, so it is exempt and may run alongside a
# real update.
# ---------------------------------------------------------------------------
# A --post-update run is the same update: it holds the lock through the fd it
# inherited across the exec (or from its parent), so it never takes it again —
# a fresh open+flock would fail against its own lock.
if [ "$POST_UPDATE" = "0" ]; then
if [ "$DRY" != "1" ] && [ "${HERMES_UPDATE_FROM:-}" != "dashboard" ]; then
  LOCK_FILE="$STATE_DIR/update.lock"
  exec 9>"$LOCK_FILE" || die "could not open $LOCK_FILE for the update lock"
  python3 -c 'import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
' || die "an update is already running (lock held on $LOCK_FILE) — wait for it to finish, or check Settings > System & Data in the dashboard"
fi

# ---------------------------------------------------------------------------
# channel
# ---------------------------------------------------------------------------
if [ -z "$CHANNEL" ]; then
  CHANNEL="$(python3 - <<'PY' 2>/dev/null || true
import json, os
p = os.path.expanduser("~/.hermes/dashboard/settings.json")
try:
    with open(p) as f:
        s = json.load(f)
    c = (s.get("update") or {}).get("channel")
except Exception:
    c = None
print(c if c in ("stable", "main") else "stable")
PY
)"
fi
[ -n "$CHANNEL" ] || CHANNEL="stable"
case "$CHANNEL" in stable|main) ;; *) die "unknown channel: $CHANNEL" ;; esac

# ---------------------------------------------------------------------------
# install shape
# ---------------------------------------------------------------------------
if [ -d "$ROOT/.git" ] && command -v git >/dev/null 2>&1; then
  MODE="git"
else
  MODE="tarball"
fi
say "install dir : $ROOT"
say "mode        : $MODE     channel: $CHANNEL     current: $FROM_VER"

# "is $2 strictly newer than $1" — version sort, tolerant of a leading v
is_newer() {
  local a="${1#v}" b="${2#v}"
  [ "$a" = "$b" ] && return 1
  [ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -1)" = "$b" ]
}

# `--target latest` is a no-op when we are already on (or past) that release
up_to_date_guard() {
  local newref="$1"
  [ "$TARGET" = "latest" ] || return 0
  if ! is_newer "$FROM_VER" "${newref#v}"; then
    RESULT_OK=1
    RESULT_MSG="already up to date (v$FROM_VER; newest release is $newref)"
    say "already up to date — running v$FROM_VER, newest release is $newref. Nothing to do."
    say "──────────── update.sh done ────────────"
    exit 0
  fi
}

app_fingerprint() {
  # stable hash of everything under app/ (source only — build/ is generated)
  if [ -d "$ROOT/app" ]; then
    find "$ROOT/app" -type f -not -path "*/build/*" -print0 \
      | LC_ALL=C sort -z | xargs -0 shasum -a 256 2>/dev/null \
      | shasum -a 256 | awk '{print $1}'
  else
    echo none
  fi
}
APP_BEFORE="$(app_fingerprint)"
# what the model lanes run (E129 review): install-services.sh leaves a loaded
# lane alone only when this run vouches that none of it changed
lane_fingerprint() {
  local f
  for f in mlx-server.sh mlx-server-bg.sh mlx-vlm-launch.py; do
    if [ -f "$ROOT/$f" ]; then shasum -a 256 "$ROOT/$f" | awk '{print $1}'; else echo none; fi
  done | shasum -a 256 | awk '{print $1}'
}
LANE_BEFORE="$(lane_fingerprint)"

# ===========================================================================
# GIT PATH
# ===========================================================================
if [ "$MODE" = "git" ]; then
  FROM_REF="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

  DIRTY="$(git -C "$ROOT" status --porcelain --untracked-files=no || true)"
  if [ -n "$DIRTY" ]; then
    if [ "$DRY" = "1" ]; then
      say "NOTE: the checkout is dirty — a real run would refuse (or stash, with --force):"
      echo "$DIRTY" | sed 's/^/        /'
    elif [ "$FORCE" != "1" ]; then
      echo "$DIRTY"
      die "this checkout has uncommitted changes. Commit or stash them, or re-run with --force (which stashes them for you)."
    fi
  fi
  if [ -n "$DIRTY" ] && [ "$FORCE" = "1" ]; then
    say "--force: stashing local changes (recover with: git stash list / git stash pop)"
    [ "$DRY" = "1" ] || git -C "$ROOT" stash push -u -m "update.sh auto-stash $(date +%s)" >/dev/null
  fi

  say "fetching tags from origin"
  if [ "$DRY" != "1" ]; then
    git -C "$ROOT" fetch --tags --prune origin || die "git fetch failed (no network, or no access to $REPO)"
  fi

  if [ "$CHANNEL" = "main" ] || [ "$TARGET" = "main" ]; then
    REF="origin/main"
    say "resolved target: origin/main (main channel)"
    if [ "$DRY" = "1" ]; then
      say "DRY RUN — would: git checkout main && git merge --ff-only origin/main"
    else
      git -C "$ROOT" checkout --quiet main 2>/dev/null \
        || git -C "$ROOT" checkout --quiet -B main --track origin/main
      git -C "$ROOT" merge --ff-only origin/main \
        || die "main has diverged from origin/main (local commits) — resolve by hand; update.sh will not rewrite your history"
    fi
  else
    if [ "$TARGET" = "latest" ]; then
      REF="$(git -C "$ROOT" tag -l 'v*' --sort=-v:refname \
             | grep -E '^v[0-9]+(\.[0-9]+){0,2}$' | head -1 || true)"
      [ -n "$REF" ] || die "no release tags on origin yet — nothing to update to (try --channel main)"
    else
      REF="$TARGET"
    fi
    git -C "$ROOT" rev-parse --verify --quiet "refs/tags/$REF" >/dev/null \
      || die "no such release tag: $REF"
    say "resolved target: $REF"
    up_to_date_guard "$REF"
    if [ "$DRY" = "1" ]; then
      say "DRY RUN — would: git checkout --quiet $REF   (detached HEAD, normal for a release)"
    else
      git -C "$ROOT" checkout --quiet "$REF" || die "git checkout $REF failed"
    fi
  fi

# ===========================================================================
# TARBALL PATH
# ===========================================================================
else
  command -v curl  >/dev/null 2>&1 || die "curl is required for a tarball update"
  command -v rsync >/dev/null 2>&1 || die "rsync is required for a tarball update"

  TOK="${HERMES_UPDATE_TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"
  if [ -z "$TOK" ] && command -v gh >/dev/null 2>&1; then
    TOK="$(gh auth token 2>/dev/null || true)"
  fi
  # The token reaches curl on stdin (-H @- reads header lines from it), never
  # in its argv: any local user can read argv with ps for as long as a
  # download runs. printf is a builtin, so it has no argv of its own either.
  gh_curl() {
    if [ -n "$TOK" ]; then
      printf 'Authorization: Bearer %s\n' "$TOK" | curl -H @- "$@"
    else
      curl "$@"
    fi
  }

  if [ "$TARGET" = "latest" ] || [ "$TARGET" = "main" ]; then
    API="https://api.github.com/repos/$REPO/releases/latest"
  else
    API="https://api.github.com/repos/$REPO/releases/tags/$TARGET"
  fi
  say "asking GitHub for the release: $API"
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/hermes-update.XXXXXX")"
  cleanup() { [ -n "${TMP:-}" ] && rm -rf "$TMP"; }   # on_exit (the EXIT trap) calls it

  gh_curl -fsSL -H "User-Agent: hermes-assistant-updater" \
       -H "Accept: application/vnd.github+json" \
       "$API" -o "$TMP/release.json" \
    || die "could not read the release (private repo without a token, offline, or no such tag)"

  # The tag and the two URLs come from the network, BEFORE the checksum check,
  # and a git refname may legally hold $(...) or ${IFS}. This file used to be
  # SOURCED, so such a tag ran as shell code. Now the values are shape-checked
  # here and read back below as plain data, never evaluated.
  REL_JSON="$TMP/release.json" python3 - > "$TMP/plan.env" 2> "$TMP/plan.err" <<'PY' \
    || die "refusing the release JSON: $(tr -d '\r' < "$TMP/plan.err" | tail -1 | cut -c1-300)"
import json, os, re, sys
rel = json.load(open(os.environ["REL_JSON"]))
tag = rel.get("tag_name") or ""
src = ""
sums = ""
for a in rel.get("assets") or []:
    n = a.get("name") or ""
    if n.endswith("-source.tar.gz") or n == "source.tar.gz":
        src = a.get("browser_download_url") or ""
    if n == "SHA256SUMS":
        sums = a.get("browser_download_url") or ""
if not src:
    src = rel.get("tarball_url") or ""
TAG_RE = re.compile(r"^[vV]?[0-9]+(\.[0-9]+){0,2}(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]+)?/[A-Za-z0-9._~%/+@=-]*"
                    r"(\?[A-Za-z0-9._~%/+@=&-]*)?$")
if not isinstance(tag, str) or (tag and not TAG_RE.match(tag)):
    sys.exit("the release tag %r is not a version tag" % (str(tag)[:80],))
for what, url in (("source tarball", src), ("SHA256SUMS", sums)):
    if not isinstance(url, str) or (url and not URL_RE.match(url)):
        sys.exit("the %s URL %r is not a plain https URL" % (what, str(url)[:120]))
print("REL_TAG=%s" % tag)
print("REL_SRC=%s" % src)
print("REL_SUMS=%s" % sums)
PY
  REL_TAG=""; REL_SRC=""; REL_SUMS=""
  while IFS='=' read -r k v; do
    case "$k" in
      REL_TAG)  REL_TAG="$v" ;;
      REL_SRC)  REL_SRC="$v" ;;
      REL_SUMS) REL_SUMS="$v" ;;
    esac
  done < "$TMP/plan.env"
  [ -n "${REL_TAG:-}" ] || die "the release has no tag"
  [ -n "${REL_SRC:-}" ] || die "the release has no source tarball"
  REF="$REL_TAG"
  say "resolved target: $REL_TAG"
  up_to_date_guard "$REL_TAG"

  if [ "$DRY" = "1" ]; then
    say "DRY RUN — would download $REL_SRC, verify it against SHA256SUMS, and rsync it over $ROOT"
  else
    say "downloading source tarball"
    gh_curl -fsSL -H "User-Agent: hermes-assistant-updater" \
         "$REL_SRC" -o "$TMP/src.tar.gz" || die "download failed"

    # SHA256SUMS is REQUIRED on this path. It used to be best-effort: no
    # SHA256SUMS asset and the updater said so, then rsync --delete'd whatever
    # the download produced over the install.
    #
    # What this check is, and is not (2026-11-09 audit E128): SHA256SUMS is
    # built by the same release job, published on the same release and fetched
    # from the same host, with the same token, as the tarball. So it DETECTS
    # CORRUPTION (a truncated or damaged download, a release published without
    # sums) — it does NOT detect TAMPERING: anyone able to replace the tarball
    # on the release can replace SHA256SUMS too. Nothing here is signed; the
    # trust root is the release repo slug plus GitHub's TLS. The git path has
    # the same trust root (it fetches a ref from origin).
    #
    # --force deliberately does NOT bypass any of this: it means "stash my dirty
    # tree", a convenience about LOCAL edits. A corrupt download is not a
    # dirty-tree concern, and a flag people reach for when an update is being
    # awkward is exactly the wrong switch to wire to "skip the checksum".
    [ -n "${REL_SUMS:-}" ] || die "release $REL_TAG publishes no SHA256SUMS asset — refusing to install an unverified tarball (not overridable with --force)"
    say "verifying SHA256"
    gh_curl -fsSL -H "User-Agent: hermes-assistant-updater" \
         "$REL_SUMS" -o "$TMP/SHA256SUMS" || die "could not download SHA256SUMS"
    GOT="$(shasum -a 256 "$TMP/src.tar.gz" | awk '{print $1}')"
    BASE="$(basename "$REL_SRC")"
    WANT="$(awk -v f="$BASE" '$2 == f || $2 == "*" f {print $1}' "$TMP/SHA256SUMS" | head -1)"
    if [ -z "$WANT" ]; then
      # GitHub's own tarball_url has no filename to match on (the basename is
      # the tag), so fall back to the single *source.tar.gz line — still a real
      # entry from the release's checksum list, never a skip.
      WANT="$(awk '$2 ~ /source\.tar\.gz$/ {print $1}' "$TMP/SHA256SUMS" | head -1)"
    fi
    [ -n "$WANT" ] || die "SHA256SUMS has no entry for $BASE — refusing to install an unverified tarball"
    [ "$GOT" = "$WANT" ] || die "SHA256 mismatch for $BASE (got $GOT, expected $WANT) — refusing"
    say "checksum ok"

    mkdir -p "$TMP/x"
    tar -xzf "$TMP/src.tar.gz" -C "$TMP/x" || die "could not extract the tarball"
    SRC_DIR="$(find "$TMP/x" -mindepth 1 -maxdepth 1 -type d | head -1)"
    [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/install-services.sh" ] \
      || die "the tarball does not look like a Hermes Assistant source tree"

    # The same excludes go to the backup, the install and the rollback, so the
    # three are exact inverses and none of them touches what is excluded. An
    # excluded path is also protected from --delete — that is why the legacy
    # config export docs/state-snapshot.json (export-ignored from the tarball,
    # 2026-10-21 audit C64) is listed: without it every tarball update deleted
    # it.
    EXCL=(--exclude '.git/' --exclude 'app/build/' --exclude 'graphify-out/'
          --exclude 'dashboard/logs/' --exclude '__pycache__/' --exclude '*.pyc'
          --exclude '.env' --exclude '.DS_Store' --exclude '/docs/state-snapshot.json')

    # A tarball install has no git to say what was edited locally, so rsync
    # --delete used to overwrite a local edit, and remove a local file, with
    # no way back — and no way back either if install-services.sh then
    # failed. The whole install dir is copied aside FIRST (it is small; the
    # newest three are kept), and the exact inverse is printed on any failure.
    BACKUP="$STATE_DIR/update-backups/$(date +%Y%m%d-%H%M%S)-v$FROM_VER"
    say "backing up the current install to $BACKUP"
    # A partial copy (a full disk, a file it cannot read) is removed again: it
    # restores nothing, and kept it would count as one of the three newest.
    mkdir -p "$BACKUP" && rsync -a "${EXCL[@]}" "$ROOT"/ "$BACKUP"/ \
      || { rm -rf "$BACKUP"; die "could not back up $ROOT to $BACKUP — nothing was installed"; }
    ROLLBACK_HINT="to put v$FROM_VER back:  rsync -a --delete$(printf ' %q' "${EXCL[@]}") $(printf '%q' "$BACKUP")/ $(printf '%q' "$ROOT")/ && $(printf '%q' "$ROOT/install-services.sh")"

    say "installing over $ROOT (rsync --delete, excluding .git and generated dirs)"
    rsync -a --delete "${EXCL[@]}" "$SRC_DIR"/ "$ROOT"/ \
      || die "rsync failed part-way — $ROLLBACK_HINT"
  fi
fi

TO_VER="$(tr -d ' \t\n\r' < "$ROOT/VERSION" 2>/dev/null || echo unknown)"
TO_REF="${REF:-}"
if [ "$MODE" = "git" ] && [ "$DRY" != "1" ]; then
  TO_REF="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi

APP_AFTER="$(app_fingerprint)"
[ "$APP_BEFORE" = "$APP_AFTER" ] || APP_CHANGED=1
LANE_CHANGED=1
[ "$LANE_BEFORE" = "$(lane_fingerprint)" ] && LANE_CHANGED=0

# ---------------------------------------------------------------------------
# hand over to the INCOMING update.sh (E29). Everything above ran from the
# text this process started with, which is right: it decides what to install.
# Everything below must come from the version just installed, so exec it. An
# exec keeps this PID and every open fd — the update lock included — and runs
# no EXIT trap, so the temp dir is cleaned here and the state goes over in the
# environment. A target whose update.sh lacks the protocol marker (an older
# release) gets the steps below from this text, as before.
# ---------------------------------------------------------------------------
HANDOVER=0
if [ "$DRY" != "1" ] && [ -f "$ROOT/update.sh" ] \
   && grep -qx '# hermes-update-post-protocol: 1' "$ROOT/update.sh" 2>/dev/null; then
  # never exec a script that does not even parse: bash would run it up to the
  # error and stop with the plugins, services and gateway never refreshed
  if "${BASH:-/bin/bash}" -n "$ROOT/update.sh" 2>/dev/null; then
    HANDOVER=1
  else
    say "WARNING: the incoming update.sh does not parse (bash -n) — finishing this update with the post-update steps of the version that started it"
  fi
fi
if [ "$HANDOVER" = "1" ]; then
  # a provisional result, in case the incoming script dies before it writes
  # its own (it overwrites this one when it finishes either way)
  RESULT_OK=0
  RESULT_MSG="the code for v$TO_VER is installed, but the run was handed over to the incoming update.sh, which ended without reporting a result — the plugins, services and Telegram gateway may not be refreshed: run ./update.sh --post-update"
  STATE_KEEP_RUNNING=1 write_state
  cleanup || true
  export HERMES_UPD_FROM_VER="$FROM_VER" HERMES_UPD_FROM_REF="$FROM_REF" \
         HERMES_UPD_TO_REF="$TO_REF" HERMES_UPD_MODE="$MODE" \
         HERMES_UPD_APP_CHANGED="$APP_CHANGED" HERMES_UPD_BACKUP="$BACKUP" \
         HERMES_UPD_ROLLBACK_HINT="$ROLLBACK_HINT" HERMES_UPD_LANE_CHANGED="$LANE_CHANGED"
  say "code is in place — continuing with the incoming update.sh (v$TO_VER)"
  POST_ARGS=(--post-update --target "$TARGET" --channel "$CHANNEL")
  [ "$REBUILD_APP" = "1" ] && POST_ARGS+=(--rebuild-app)
  exec "${BASH:-/bin/bash}" "$ROOT/update.sh" "${POST_ARGS[@]}"
fi
fi   # the normal run's own steps (POST_UPDATE=0) end here

if [ "$POST_UPDATE" = "1" ] && [ -z "${HERMES_UPD_MODE:-}" ] \
   && [ "${HERMES_UPDATE_FROM:-}" != "dashboard" ]; then
  # a hand-run ./update.sh --post-update (no outgoing run handed anything
  # over, so no lock was inherited): take the update lock like a normal run
  LOCK_FILE="$STATE_DIR/update.lock"
  exec 9>"$LOCK_FILE" || die "could not open $LOCK_FILE for the update lock"
  python3 -c 'import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
' || die "an update is already running (lock held on $LOCK_FILE) — wait for it to finish, or check Settings > System & Data in the dashboard"
fi
if [ "$POST_UPDATE" != "0" ]; then
  # the facts the outgoing run established, as it handed them over; a run
  # from an older update.sh's install-services.sh step has none of them
  MODE="${HERMES_UPD_MODE:-unknown}"
  FROM_VER="${HERMES_UPD_FROM_VER:-$FROM_VER}"
  FROM_REF="${HERMES_UPD_FROM_REF:-}"
  TO_VER="$(tr -d ' \t\n\r' < "$ROOT/VERSION" 2>/dev/null || echo unknown)"
  TO_REF="${HERMES_UPD_TO_REF:-}"
  APP_CHANGED="${HERMES_UPD_APP_CHANGED:-0}"
  BACKUP="${HERMES_UPD_BACKUP:-}"
  ROLLBACK_HINT="${HERMES_UPD_ROLLBACK_HINT:-}"
  LANE_CHANGED="${HERMES_UPD_LANE_CHANGED:-1}"
  [ -n "$CHANNEL" ] || CHANNEL="stable"
fi

# ---------------------------------------------------------------------------
# agent plugins — keep ~/.hermes/plugins/tool-budget pointed at this checkout
# and make sure it is in plugins.enabled. Both steps are idempotent: a link
# that already points here and an entry that is already present change nothing
# and write no backup, so an update that ships no plugin change is a no-op.
# Never fatal — a plugin that cannot be linked must not fail an update.
# ---------------------------------------------------------------------------
TB_SRC="$ROOT/hermes-plugins/tool-budget"
TB_DST="$HOME/.hermes/plugins/tool-budget"
if [ ! -f "$TB_SRC/__init__.py" ]; then
  say "tool-budget plugin not in this build — skipping"
elif [ "$DRY" = "1" ]; then
  say "DRY RUN — would link $TB_DST and ensure tool-budget is in plugins.enabled"
else
  mkdir -p "$HOME/.hermes/plugins" 2>/dev/null || true
  if [ -L "$TB_DST" ] && [ "$(readlink "$TB_DST")" = "$TB_SRC" ]; then
    say "tool-budget plugin already linked"
  else
    if [ -e "$TB_DST" ] && [ ! -L "$TB_DST" ]; then
      mv "$TB_DST" "$TB_DST.old-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
      say "moved an existing $TB_DST aside"
    fi
    if ln -sfn "$TB_SRC" "$TB_DST" 2>/dev/null; then
      say "linked tool-budget plugin -> $TB_SRC"
    else
      say "WARNING: could not link the tool-budget plugin"
    fi
  fi
  if [ -f "$HOME/.hermes/config.yaml" ]; then
    if tb_out="$(python3 "$ROOT/hermes-plugins/plugin_enable.py" tool-budget \
                 --config "$HOME/.hermes/config.yaml" 2>&1)"; then
      say "plugins.enabled: $tb_out"
    else
      say "WARNING: could not enable tool-budget: $tb_out"
    fi
  else
    # install.sh says this out loud; here it used to be a silent skip, which
    # left the plugin linked but switched on nowhere and nothing in the log.
    say "WARNING: no ~/.hermes/config.yaml — tool-budget was linked but NOT enabled"
  fi
fi

# approval-gate (audit 2026-10-21 C111/C116): same link + enable, same rules.
AG_SRC="$ROOT/hermes-plugins/approval-gate"
AG_DST="$HOME/.hermes/plugins/approval-gate"
if [ ! -f "$AG_SRC/__init__.py" ]; then
  say "approval-gate plugin not in this build — skipping"
elif [ "$DRY" = "1" ]; then
  say "DRY RUN — would link $AG_DST and ensure approval-gate is in plugins.enabled"
else
  mkdir -p "$HOME/.hermes/plugins" 2>/dev/null || true
  if [ -L "$AG_DST" ] && [ "$(readlink "$AG_DST")" = "$AG_SRC" ]; then
    say "approval-gate plugin already linked"
  else
    if [ -e "$AG_DST" ] && [ ! -L "$AG_DST" ]; then
      mv "$AG_DST" "$AG_DST.old-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
      say "moved an existing $AG_DST aside"
    fi
    if ln -sfn "$AG_SRC" "$AG_DST" 2>/dev/null; then
      say "linked approval-gate plugin -> $AG_SRC"
    else
      say "WARNING: could not link the approval-gate plugin"
    fi
  fi
  if [ -f "$HOME/.hermes/config.yaml" ]; then
    if ag_out="$(python3 "$ROOT/hermes-plugins/plugin_enable.py" approval-gate \
                 --config "$HOME/.hermes/config.yaml" 2>&1)"; then
      say "plugins.enabled: $ag_out"
    else
      say "WARNING: could not enable approval-gate: $ag_out"
    fi
  else
    say "WARNING: no ~/.hermes/config.yaml — approval-gate was linked but NOT enabled"
  fi
fi

# ---------------------------------------------------------------------------
# services — re-render the plists and restart the always-on pair.
# install-services.sh writes the model plists with RunAtLoad/KeepAlive false,
# so bootstrapping them LOADS but does not START them. It leaves a model job
# whose plist AND code did not change alone, so a running lane keeps running
# (E129); a model job whose plist or code DID change is reloaded, which stops
# it if it was running — install-services.sh prints which happened to each
# lane.
# ---------------------------------------------------------------------------
if [ "$POST_UPDATE" = "2" ]; then
  :   # we ARE the tail of install-services.sh, which an older update.sh ran
elif [ "$DRY" = "1" ]; then
  say "DRY RUN — would run ./install-services.sh (dashboard + serve restart; a model server is reloaded only if its plist or its code changed)"
else
  say "re-installing launchd services"
  # this run links the plugins and restarts the gateway itself, so
  # install-services.sh must not call back into update.sh for them (E29)
  export HERMES_UPD_IN_POST=1
  # a loaded model lane is left running only when its code did not change
  # (unknown counts as changed: a hand-run --post-update reloads)
  if [ "${LANE_CHANGED:-1}" = "0" ]; then export HERMES_MODEL_CODE_UNCHANGED=1
  else unset HERMES_MODEL_CODE_UNCHANGED; fi
  if [ -x "$ROOT/install-services.sh" ]; then
    "$ROOT/install-services.sh" || die "install-services.sh failed — the code is updated but the services may be down; run ./install-services.sh by hand${ROLLBACK_HINT:+, or $ROLLBACK_HINT}"
  else
    bash "$ROOT/install-services.sh" || die "install-services.sh failed${ROLLBACK_HINT:+ — $ROLLBACK_HINT}"
  fi
fi

# ---------------------------------------------------------------------------
# the Telegram gateway. Hermes discovers plugins once per process, so a
# gateway that was already running keeps the plugin set and plugin code it
# started with. install-services.sh restarts serve but not the gateway (its
# plist belongs to `hermes gateway install`), so approval-gate, enabled above,
# guarded serve and new `hermes -z` runs while Telegram ran without it until
# the next reboot. Restart it when launchd has it loaded; otherwise say so
# loudly, and record what happened in update-state.json either way.
# ---------------------------------------------------------------------------
GW_LABEL="ai.hermes.gateway"
GW_TARGET="gui/$(id -u)/$GW_LABEL"
# `hermes gateway install` loads the job into gui/<uid> from an Aqua session
# and into user/<uid> otherwise (SSH, Background; its default on macOS 26+),
# and Hermes' own gateway commands probe the two in that order. So does this.
gw_loaded() {
  local d
  for d in "gui/$(id -u)" "user/$(id -u)"; do
    if launchctl print "$d/$GW_LABEL" >/dev/null 2>&1; then
      GW_TARGET="$d/$GW_LABEL"; return 0
    fi
  done
  return 1
}
if [ ! -f "$TB_SRC/__init__.py" ] && [ ! -f "$AG_SRC/__init__.py" ]; then
  GATEWAY="not_needed"   # the gateway loads nothing from a build without plugins
elif [ "$DRY" = "1" ]; then
  say "DRY RUN — would restart the Telegram gateway ($GW_LABEL) if launchd has it loaded, so it loads the updated plugins"
elif gw_loaded; then
  if launchctl kickstart -k "$GW_TARGET" >/dev/null 2>&1; then
    GATEWAY="restarted"
    say "restarted the Telegram gateway so it loads the updated plugins (approval-gate, tool-budget)"
  else
    GATEWAY="restart_failed"
    say "WARNING: could not restart the Telegram gateway. Until it restarts, Telegram runs WITHOUT the updated plugins, approval-gate included. Run:  launchctl kickstart -k $GW_TARGET   (or: hermes gateway restart)"
  fi
elif pgrep -f 'hermes(_cli\.main)? gateway run' >/dev/null 2>&1; then
  GATEWAY="restart_by_hand"
  say "WARNING: a Telegram gateway is running outside launchd, so it was not restarted. Until it is, Telegram runs WITHOUT the updated plugins, approval-gate included. Stop it and start it again (hermes gateway run)."
else
  GATEWAY="not_running"
  say "no Telegram gateway is running — nothing to restart"
fi
GW_FIX="launchctl kickstart -k $GW_TARGET   (or: hermes gateway restart)"   # for the summary

if [ "$POST_UPDATE" = "2" ]; then
  # the older update.sh that called install-services.sh goes on with its own
  # backups, app note and summary
  say "post-update steps done (plugins linked and enabled; gateway: ${GATEWAY:-unknown})"
  exit 0
fi

# The backup is kept after a good install too (a release can misbehave only
# later), but only the newest three, so the folder cannot grow without bound.
# Names start with the timestamp, so a name sort is oldest-first (mtime is
# not: rsync -a stamps each copy with the install dir's own mtime).
if [ -n "$BACKUP" ] && [ -d "$STATE_DIR/update-backups" ]; then
  ls -1 "$STATE_DIR/update-backups" 2>/dev/null | grep -E '^[0-9]{8}-[0-9]{6}-v' \
    | LC_ALL=C sort -r | tail -n +4 \
    | while IFS= read -r old; do rm -rf "$STATE_DIR/update-backups/$old"; done || true
fi

# ---------------------------------------------------------------------------
# the app bundle
# ---------------------------------------------------------------------------
if [ "$REBUILD_APP" = "1" ]; then
  if [ "$DRY" = "1" ]; then
    say "DRY RUN — would run app/build-app.sh and install to /Applications"
  else
    say "rebuilding the app bundle"
    bash "$ROOT/app/build-app.sh" || die "app/build-app.sh failed"
    say "app rebuilt and installed — RE-GRANT FULL DISK ACCESS: System Settings › Privacy & Security › Full Disk Access › remove and re-add Hermes Assistant.app (an ad-hoc rebuild changes the code signature, so macOS treats it as a new app and the Message Center will read nothing until you do)"
  fi
elif [ "$APP_CHANGED" = "1" ]; then
  say "NOTE: app/ changed in this update. The dashboard you see is served over HTTP, so it is already up to date; the native window is not. To pick up the new app:  ./update.sh --rebuild-app   (or  app/build-app.sh  ). Rebuilding is ad-hoc-signed, which DROPS the app's Full Disk Access grant — you must re-add Hermes Assistant.app under System Settings › Privacy & Security › Full Disk Access afterwards, or the Message Center goes blank."
fi

RESULT_OK=1
if [ "$DRY" = "1" ]; then
  RESULT_MSG="dry run only — nothing changed"
  say "DRY RUN COMPLETE — nothing was changed."
  exit 0
fi

RESULT_MSG="updated to ${TO_REF:-$TO_VER} (v$TO_VER) from v$FROM_VER"
NL=$'\n'
MORE=""
case "$GATEWAY" in
  restarted) MORE="  The Telegram gateway was restarted, so it runs the updated plugins too.$NL" ;;
  restart_failed|restart_by_hand)
    MORE="  THE TELEGRAM GATEWAY WAS NOT RESTARTED: until it is, Telegram runs$NL  without the updated plugins (approval-gate included). "
    if [ "$GATEWAY" = "restart_failed" ]; then MORE="${MORE}Run:$NL    $GW_FIX$NL"
    else MORE="${MORE}Stop it and start it again.$NL"; fi ;;
esac
if [ -n "$BACKUP" ]; then
  MORE="$MORE  The previous install was copied aside first (the newest three are kept):$NL    $BACKUP$NL  $ROLLBACK_HINT$NL"
fi
echo
say "SUMMARY"
cat <<EOF

  Hermes Assistant is now at version $TO_VER (${TO_REF:-unknown}), updated from
  $FROM_VER (${FROM_REF:-unknown}) via the $MODE path on the $CHANNEL channel.
  The launchd services were re-rendered and the dashboard and agent backend
  restarted. A model server was reloaded only if its launchd plist or its code
  changed (the "model lanes" line above says which); nothing loaded a model. A lane
  that was reloaded was stopped: the primary wakes on the next chat turn, a
  background lane you started by hand must be started again. Your chats, memories and settings in ~/.hermes
  were not touched (the tool-budget and approval-gate plugin links and
  plugins.enabled were refreshed). The
  dashboard comes back on http://127.0.0.1:7788 within a few seconds and the
  app window reloads by itself once it reconnects$( [ "$APP_CHANGED" = "1" ] && [ "$REBUILD_APP" != "1" ] && echo "; app/ changed in this release, so run ./update.sh --rebuild-app when you are ready to replace the bundle and re-grant Full Disk Access" ).
${MORE}  Full log: $LOG

EOF
say "──────────── update.sh done ────────────"
exit 0
