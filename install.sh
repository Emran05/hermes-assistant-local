#!/usr/bin/env bash
# install.sh — set up Hermes Assistant on a fresh Mac.
#
# What it does (and, deliberately, what it does not):
#   * preflight — macOS version, Apple Silicon, Xcode command line tools, a
#     usable Python. It NEVER installs system software for you; it prints the
#     exact command to run and stops.
#   * creates ~/.hermes/{dashboard,logs} and seeds ~/.hermes/.env and
#     ~/.hermes/config.yaml from the templates in this repo — only if they do
#     not already exist. Your existing config is never overwritten.
#   * checks for the `hermes` CLI (NousResearch Hermes Agent) and points you at
#     the installer if it is missing.
#   * offers the isolated mlx-vlm venv (the fast model backend).
#   * with --app, builds the native window (app/build-app.sh) and installs it.
#   * finally runs ./install-services.sh, which installs the launchd agents.
#
# Usage:
#   ./install.sh                 # everything except the .app
#   ./install.sh --app           # also build + install Hermes Assistant.app
#   ./install.sh --dry-run       # print the plan, change nothing
#   ./install.sh --no-services   # stop before installing launchd services
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_DIR="$HOME/.hermes"
DRY=0
WITH_APP=0
WITH_SERVICES=1
WITH_VENV=""

while [ $# -gt 0 ]; do
  case "$1" in
    --app)         WITH_APP=1; shift ;;
    --dry-run)     DRY=1; shift ;;
    --no-services) WITH_SERVICES=0; shift ;;
    --mlx-venv)    WITH_VENV=1; shift ;;
    --no-mlx-venv) WITH_VENV=0; shift ;;
    -h|--help)
      awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"
      exit 0 ;;
    *) echo "install.sh: unknown option: $1" >&2; exit 2 ;;
  esac
done

B="$(printf '\033[1m')"; N="$(printf '\033[0m')"
DIM="$(printf '\033[2m')"
step()  { echo; echo "${B}==> $*${N}"; }
info()  { echo "    $*"; }
warn()  { echo "    ! $*"; }
plan()  { echo "    ${DIM}[dry-run]${N} would $*"; }
FAILED=0
fail()  { echo "    ✗ $*"; FAILED=1; }
oky()   { echo "    ✓ $*"; }

echo
echo "${B}Hermes Assistant — installer${N}"
echo "    install dir : $ROOT"
echo "    data dir    : $HERMES_DIR   (never overwritten by this script)"
[ "$DRY" = "1" ] && echo "    mode        : DRY RUN — nothing will be changed"

# ===========================================================================
# 1. preflight
# ===========================================================================
step "Preflight"

# --- macOS 14+
OSV="$(sw_vers -productVersion 2>/dev/null || echo 0)"
OSMAJ="${OSV%%.*}"
if [ "${OSMAJ:-0}" -ge 14 ] 2>/dev/null; then
  oky "macOS $OSV"
else
  fail "macOS $OSV — Hermes Assistant needs macOS 14 (Sonoma) or newer."
  info "  Fix: Apple menu › System Settings › General › Software Update."
fi

# --- Apple Silicon
ARCH="$(uname -m 2>/dev/null || echo unknown)"
if [ "$ARCH" = "arm64" ]; then
  oky "Apple Silicon ($ARCH)"
else
  fail "CPU is $ARCH — this build is Apple Silicon only (MLX runs on the Apple GPU/ANE)."
  info "  There is no Intel path: the local model server requires an M-series chip."
fi

# --- Xcode command line tools (swiftc, git)
if xcode-select -p >/dev/null 2>&1 && command -v swiftc >/dev/null 2>&1; then
  oky "Xcode command line tools ($(xcode-select -p))"
else
  fail "Xcode command line tools are missing (needed for swiftc and git)."
  info "  Fix: xcode-select --install     then re-run this script."
fi

# --- Python 3.12+
PY=""
for c in python3.14 python3.13 python3.12 python3; do
  p="$(command -v "$c" 2>/dev/null || true)"
  [ -n "$p" ] || continue
  if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,12) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done
if [ -n "$PY" ]; then
  oky "Python $("$PY" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')  ($PY)"
else
  fail "No Python 3.12 or newer on PATH (the dashboard is stdlib-only, but needs 3.12+)."
  info "  Fix: brew install python@3.12      (or download from python.org)"
  info "  Then re-run this script."
fi

# --- certifi (the framework pythons ship without wired-up root certs)
if [ -n "$PY" ] && ! "$PY" -c 'import certifi' >/dev/null 2>&1; then
  warn "certifi is not installed for $PY — HTTPS calls (weather, news, update"
  info "  checks) may fail with CERTIFICATE_VERIFY_FAILED."
  info "  Fix: $PY -m pip install --user certifi"
fi

# --- optional but expected tools
command -v git   >/dev/null 2>&1 && oky "git"   || warn "git not found — updates will fall back to the tarball path"
command -v curl  >/dev/null 2>&1 && oky "curl"  || warn "curl not found — tarball updates will not work"
command -v rsync >/dev/null 2>&1 && oky "rsync" || warn "rsync not found — tarball updates will not work"

if [ "$FAILED" = "1" ]; then
  echo
  echo "${B}Preflight failed.${N} Fix the ✗ items above and run ./install.sh again."
  echo "Nothing has been changed."
  exit 1
fi

# ===========================================================================
# 2. data directory + config seeds
# ===========================================================================
step "Data directory"
for d in "$HERMES_DIR" "$HERMES_DIR/dashboard" "$HERMES_DIR/logs"; do
  if [ -d "$d" ]; then
    oky "$d (exists)"
  elif [ "$DRY" = "1" ]; then
    plan "create $d"
  else
    mkdir -p "$d" && oky "created $d"
  fi
done

seed() {   # seed <template> <dest> <mode>
  local src="$1" dst="$2" mode="$3"
  if [ -e "$dst" ]; then
    oky "$(basename "$dst") already exists — left untouched"
    return
  fi
  if [ ! -f "$src" ]; then
    warn "template missing: $src"
    return
  fi
  if [ "$DRY" = "1" ]; then
    plan "copy $(basename "$src") -> $dst (mode $mode)"
  else
    cp "$src" "$dst"
    chmod "$mode" "$dst"
    oky "seeded $dst (mode $mode) — EDIT IT before first use"
  fi
}
seed "$ROOT/env.example" "$HERMES_DIR/.env" 600
seed "$ROOT/config.yaml" "$HERMES_DIR/config.yaml" 644

# ===========================================================================
# 3. the Hermes agent CLI
# ===========================================================================
step "Hermes Agent CLI"
if command -v hermes >/dev/null 2>&1 || [ -x "$HOME/.local/bin/hermes" ]; then
  oky "hermes found ($(command -v hermes 2>/dev/null || echo "$HOME/.local/bin/hermes"))"
else
  warn "the \`hermes\` CLI is not installed."
  info "  Hermes Assistant is a dashboard + services around NousResearch's Hermes"
  info "  Agent; install it first (see RUNBOOK.md § 2):"
  info ""
  info "      curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
  info ""
  info "  Then point it at the local model:  hermes model   (choose \"custom\","
  info "  base_url http://127.0.0.1:8080/v1) and set up the Telegram gateway per"
  info "  RUNBOOK.md § 4. The dashboard runs without it, but chat will not."
fi

# ===========================================================================
# 4. model server backend (optional isolated venv)
# ===========================================================================
step "Fast model backend (optional)"
if [ -d "$HOME/.hermes/mlx-vlm-venv" ]; then
  oky "mlx-vlm venv already installed"
elif [ "$DRY" = "1" ]; then
  plan "offer to run ./install-mlx-vlm-venv.sh"
else
  info "The default roster model (Qwen3.8-27B) runs on the mlx-vlm backend with"
  info "its speculative drafter — roughly twice the tokens/sec. It needs an"
  info "ISOLATED venv (~/.hermes/mlx-vlm-venv, a few hundred MB) so it cannot"
  info "break the mlx-lm install. Without it, models fall back to mlx-lm."
  if [ -n "$WITH_VENV" ]; then
    ans="$WITH_VENV"
  elif [ -t 0 ]; then
    printf "    Install it now? [y/N] "
    read -r reply || reply=""
    case "$reply" in y|Y|yes|YES) ans=1 ;; *) ans=0 ;; esac
  else
    ans=0
  fi
  if [ "$ans" = "1" ]; then
    bash "$ROOT/install-mlx-vlm-venv.sh" || warn "the venv install failed — you can re-run ./install-mlx-vlm-venv.sh later"
  else
    info "Skipped. Run ./install-mlx-vlm-venv.sh whenever you want it."
  fi
fi

# ===========================================================================
# 5. the app bundle (opt-in)
# ===========================================================================
step "Native app window"
if [ "$WITH_APP" != "1" ]; then
  info "Skipped (pass --app to build it). Without the app, open the dashboard in"
  info "a browser at http://127.0.0.1:7788 once the services are running."
elif [ "$DRY" = "1" ]; then
  plan "run app/build-app.sh and copy Hermes Assistant.app to /Applications"
else
  info "building (swiftc, ~30s)…"
  bash "$ROOT/app/build-app.sh"
  echo
  info "${B}First launch — Gatekeeper.${N} The app is ad-hoc signed, not notarised by"
  info "Apple, so double-clicking it shows \"cannot be opened because the developer"
  info "cannot be verified\". Right-click (or Control-click) Hermes Assistant.app in"
  info "/Applications › Open › Open. macOS remembers the choice; you only do it once"
  info "per build."
  info ""
  info "${B}Full Disk Access.${N} The Message Center reads ~/Library/Messages/chat.db,"
  info "which macOS protects. Grant it in System Settings › Privacy & Security ›"
  info "Full Disk Access › + › Hermes Assistant.app, then relaunch the app. Note"
  info "that REBUILDING the app changes its ad-hoc signature, so macOS treats it as"
  info "a new app and you must remove and re-add it there after every rebuild."
fi

# ===========================================================================
# 6. services
# ===========================================================================
step "Background services"
if [ "$WITH_SERVICES" != "1" ]; then
  info "Skipped (--no-services). Run ./install-services.sh when you are ready."
elif [ "$DRY" = "1" ]; then
  plan "run ./install-services.sh — dashboard :7788 + agent backend :9119 always-on"
  info "  (the two model servers are installed ON-DEMAND and start on first use)"
else
  bash "$ROOT/install-services.sh"
fi

# ===========================================================================
# done
# ===========================================================================
echo
if [ "$DRY" = "1" ]; then
  echo "${B}Dry run complete — nothing was changed.${N}"
  exit 0
fi
cat <<EOF

${B}Done.${N}

  1. Edit your secrets:      \$EDITOR ~/.hermes/.env      (chmod 600 already set)
     At minimum TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_USERS if you want the
     Telegram reach-in; everything else is optional. See RUNBOOK.md.
  2. Open the dashboard:     http://127.0.0.1:7788
  3. Pick a model in the header pill and let it download (~17 GB) the first
     time. The model servers stay OFF until you actually chat — that is the
     battery-saving design, not a bug.
  4. Updates:                Settings › System & Data › Software update,
     or ./update.sh from this directory.

  Logs:      ~/.hermes/logs/{dashboard,serve,mlx-server}.log
  Troubles:  RUNBOOK.md  (and \`hermes doctor\`)

EOF
