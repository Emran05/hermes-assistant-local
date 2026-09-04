#!/usr/bin/env bash
# update.sh — put a newer Hermes Assistant on this Mac.
#
# Two install shapes, auto-detected:
#   git checkout  — fetch tags, check out the target (a release tag on the
#                   `stable` channel, origin/main on `main`).
#   tarball       — download the release's source tarball, verify its SHA256
#                   against SHA256SUMS (REQUIRED — no sums, or no line for this
#                   tarball, is a hard refusal that --force cannot override),
#                   and rsync it over the install directory.
# Then, in both shapes: re-run ./install-services.sh (re-renders the launchd
# plists and restarts the dashboard + serve; the model servers stay ON-DEMAND),
# and report whether app/ changed — replacing the app bundle is a deliberate,
# separate step because a rebuild drops the app's Full Disk Access grant.
#
# NEVER touches ~/.hermes data. The only things it writes outside the checkout
# are ~/.hermes/logs/update.log and ~/.hermes/dashboard/update-state.json.
#
# Safe to run straight from a terminal:
#   ./update.sh                      # newest release on your channel
#   ./update.sh --target v0.4.0      # a specific release
#   ./update.sh --channel main       # track the development branch
#   ./update.sh --dry-run            # print the plan, change nothing
#   ./update.sh --rebuild-app        # also rebuild + install the .app
set -euo pipefail

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
    -h|--help)     usage; exit 0 ;;
    *) echo "update.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# When the dashboard starts us our stdout IS the log file already; from a
# terminal we want both. Either way everything ends up in update.log.
if [ "${HERMES_UPDATE_FROM:-}" != "dashboard" ]; then
  exec > >(tee -a "$LOG") 2>&1
fi

TS() { date "+%Y-%m-%d %I:%M:%S %p"; }
say() { echo "[$(TS)] $*"; }
die() { RESULT_MSG="$*"; say "ERROR: $*"; exit 1; }

echo
say "──────────── update.sh start (target=$TARGET force=$FORCE dry=$DRY) ────────────"

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

write_state() {
  RES_OK="$RESULT_OK" RES_MSG="$RESULT_MSG" RES_TARGET="$TARGET" \
  RES_FROM="$FROM_VER" RES_TO="$TO_VER" RES_FROM_REF="$FROM_REF" \
  RES_TO_REF="$TO_REF" RES_APP="$APP_CHANGED" RES_MODE="${MODE:-unknown}" \
  RES_STATE="$STATE" RES_LOG="$LOG" \
  python3 - <<'PY' 2>/dev/null || true
import json, os, time
state = os.environ["RES_STATE"]
try:
    with open(state) as f:
        cur = json.load(f)
except Exception:
    cur = {}
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
    "finished_at": time.time(),
}
tmp = state + ".tmp"
with open(tmp, "w") as f:
    json.dump(cur, f, indent=1)
os.replace(tmp, state)
PY
}

on_exit() {
  rc=$?
  if [ "$DRY" = "1" ]; then exit $rc; fi
  if [ $rc -ne 0 ] && [ "$RESULT_OK" = "1" ]; then RESULT_OK=0; fi
  write_state
  exit $rc
}
trap on_exit EXIT

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

  AUTH=()   # expanded as ${AUTH[@]+"${AUTH[@]}"}: macOS bash 3.2 treats an empty array as unbound under set -u
  TOK="${HERMES_UPDATE_TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"
  if [ -z "$TOK" ] && command -v gh >/dev/null 2>&1; then
    TOK="$(gh auth token 2>/dev/null || true)"
  fi
  [ -n "$TOK" ] && AUTH=(-H "Authorization: Bearer $TOK")

  if [ "$TARGET" = "latest" ] || [ "$TARGET" = "main" ]; then
    API="https://api.github.com/repos/$REPO/releases/latest"
  else
    API="https://api.github.com/repos/$REPO/releases/tags/$TARGET"
  fi
  say "asking GitHub for the release: $API"
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/hermes-update.XXXXXX")"
  cleanup() { [ -n "${TMP:-}" ] && rm -rf "$TMP"; }
  trap 'cleanup; on_exit' EXIT

  curl -fsSL -H "User-Agent: hermes-assistant-updater" \
       -H "Accept: application/vnd.github+json" ${AUTH[@]+"${AUTH[@]}"} \
       "$API" -o "$TMP/release.json" \
    || die "could not read the release (private repo without a token, offline, or no such tag)"

  REL_JSON="$TMP/release.json" python3 - > "$TMP/plan.env" <<'PY' || die "could not parse the release JSON"
import json, os
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
print("REL_TAG=%s" % tag)
print("REL_SRC=%s" % src)
print("REL_SUMS=%s" % sums)
PY
  # shellcheck disable=SC1090
  . "$TMP/plan.env"
  [ -n "${REL_TAG:-}" ] || die "the release has no tag"
  [ -n "${REL_SRC:-}" ] || die "the release has no source tarball"
  REF="$REL_TAG"
  say "resolved target: $REL_TAG"
  up_to_date_guard "$REL_TAG"

  if [ "$DRY" = "1" ]; then
    say "DRY RUN — would download $REL_SRC, verify it against SHA256SUMS, and rsync it over $ROOT"
  else
    say "downloading source tarball"
    curl -fsSL -H "User-Agent: hermes-assistant-updater" ${AUTH[@]+"${AUTH[@]}"} \
         "$REL_SRC" -o "$TMP/src.tar.gz" || die "download failed"

    # SHA256SUMS is REQUIRED on this path. It used to be best-effort: no
    # SHA256SUMS asset and the updater said so, then rsync --delete'd whatever
    # the download produced over the install. That is the one place an attacker
    # with the release URL (or anyone able to answer for it) gets arbitrary code
    # on this Mac, and "the release forgot to publish sums" is indistinguishable
    # from "the sums were removed". The git path is unaffected — it verifies by
    # fetching a ref from the remote instead.
    #
    # --force deliberately does NOT bypass any of this: it means "stash my dirty
    # tree", a convenience about LOCAL edits. Integrity is not a dirty-tree
    # concern, and a flag people reach for when an update is being awkward is
    # exactly the wrong switch to wire to "skip the signature check".
    [ -n "${REL_SUMS:-}" ] || die "release $REL_TAG publishes no SHA256SUMS asset — refusing to install an unverified tarball (not overridable with --force)"
    say "verifying SHA256"
    curl -fsSL -H "User-Agent: hermes-assistant-updater" ${AUTH[@]+"${AUTH[@]}"} \
         "$REL_SUMS" -o "$TMP/SHA256SUMS" || die "could not download SHA256SUMS"
    GOT="$(shasum -a 256 "$TMP/src.tar.gz" | awk '{print $1}')"
    BASE="$(basename "$REL_SRC")"
    WANT="$(awk -v f="$BASE" '$2 == f || $2 == "*" f {print $1}' "$TMP/SHA256SUMS" | head -1)"
    if [ -z "$WANT" ]; then
      # GitHub's own tarball_url has no filename to match on (the basename is
      # the tag), so fall back to the single *source.tar.gz line — still a real
      # entry from the signed-ish list, never a skip.
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

    say "installing over $ROOT (rsync --delete, excluding .git and generated dirs)"
    rsync -a --delete \
      --exclude '.git/' --exclude 'app/build/' --exclude 'graphify-out/' \
      --exclude 'dashboard/logs/' --exclude '__pycache__/' --exclude '*.pyc' \
      --exclude '.env' --exclude '.DS_Store' \
      "$SRC_DIR"/ "$ROOT"/ || die "rsync failed"
  fi
fi

TO_VER="$(tr -d ' \t\n\r' < "$ROOT/VERSION" 2>/dev/null || echo unknown)"
TO_REF="${REF:-}"
if [ "$MODE" = "git" ] && [ "$DRY" != "1" ]; then
  TO_REF="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi

APP_AFTER="$(app_fingerprint)"
[ "$APP_BEFORE" = "$APP_AFTER" ] || APP_CHANGED=1

# ---------------------------------------------------------------------------
# services — re-render the plists and restart the always-on pair.
# install-services.sh writes the model plists with RunAtLoad/KeepAlive false,
# so bootstrapping them here LOADS but does not START them: the model servers
# stay on-demand exactly as they were.
# ---------------------------------------------------------------------------
if [ "$DRY" = "1" ]; then
  say "DRY RUN — would run ./install-services.sh (dashboard + serve restart; model servers stay on-demand)"
else
  say "re-installing launchd services"
  if [ -x "$ROOT/install-services.sh" ]; then
    "$ROOT/install-services.sh" || die "install-services.sh failed — the code is updated but the services may be down; run ./install-services.sh by hand"
  else
    bash "$ROOT/install-services.sh" || die "install-services.sh failed"
  fi
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
echo
say "SUMMARY"
cat <<EOF

  Hermes Assistant is now at version $TO_VER (${TO_REF:-unknown}), updated from
  $FROM_VER (${FROM_REF:-unknown}) via the $MODE path on the $CHANNEL channel.
  The launchd services were re-rendered and the dashboard and agent backend
  restarted; the two model servers were left on-demand, so nothing loaded a
  model and no RAM was taken. Your data in ~/.hermes was not touched. The
  dashboard comes back on http://127.0.0.1:7788 within a few seconds and the
  app window reloads by itself once it reconnects$( [ "$APP_CHANGED" = "1" ] && [ "$REBUILD_APP" != "1" ] && echo "; app/ changed in this release, so run ./update.sh --rebuild-app when you are ready to replace the bundle and re-grant Full Disk Access" ).
  Full log: $LOG

EOF
say "──────────── update.sh done ────────────"
exit 0
