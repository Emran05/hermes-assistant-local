#!/usr/bin/env bash
# install-services.sh — install the model server + dashboard as launchd
# services so the assistant is always on and survives reboots.
# Re-run any time; it replaces existing services. `--uninstall` removes them.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"
LOGS="$HOME/.hermes/logs"; mkdir -p "$LOGS" "$HOME/Library/LaunchAgents"
UID_N="$(id -u)"

MLX_LABEL="com.hermes.mlx-server"
BG_LABEL="com.hermes.mlx-bg"          # background lane: small model on :8081
DASH_LABEL="com.hermes.dashboard"
SERVE_LABEL="com.hermes.serve"
MLX_PLIST="$HOME/Library/LaunchAgents/$MLX_LABEL.plist"
BG_PLIST="$HOME/Library/LaunchAgents/$BG_LABEL.plist"
DASH_PLIST="$HOME/Library/LaunchAgents/$DASH_LABEL.plist"
SERVE_PLIST="$HOME/Library/LaunchAgents/$SERVE_LABEL.plist"

# Pinned auth token shared between `hermes serve` and the dashboard hub
# (hub reads this file; serve gets it via env). 600 perms, loopback-only.
TOKEN_FILE="$HOME/.hermes/dashboard/serve-token"
if [[ ! -s "$TOKEN_FILE" ]]; then
  mkdir -p "$(dirname "$TOKEN_FILE")"
  "$PY" -c 'import secrets; print(secrets.token_urlsafe(32))' > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi
SERVE_TOKEN="$(cat "$TOKEN_FILE")"

# ---------------------------------------------------------------------------
# model on-demand default (2026-09-10 audit A09)
#
# The model services are RunAtLoad/KeepAlive false below, plus a gate at the
# top of mlx-server.sh that refuses an unsolicited start without a fresh
# start token — but until now nothing ever created
# ~/.hermes/dashboard/model-autostart-off, so that gate's "no marker yet"
# branch (a fresh install) fell through to the pre-2026-09-01 always-on
# behavior: `app/main.swift`'s blind `launchctl kickstart` at every app
# launch loaded the ~18GB model with no chat request behind it.
#
# On-demand is now the DEFAULT for a fresh install: no live dashboard state
# (settings.json) and no marker either way means this Mac has never been set
# up, so create the on-demand marker. HERMES_MODEL_ALWAYS_ON=1 is the
# documented opt-out — set it before running this script to keep the model
# always-on instead; the choice is remembered (model-always-on) so a later
# reinstall/update (which re-runs this script without the env var) does not
# silently flip an owner who opted in back to on-demand, and it is baked into
# com.hermes.mlx-server's own plist below so mlx-server.sh sees it on every
# launch, not just this one shell's environment.
DASH_DATA="$HOME/.hermes/dashboard"
mkdir -p "$DASH_DATA"
AUTOSTART_MARKER="$DASH_DATA/model-autostart-off"
ALWAYS_ON_MARKER="$DASH_DATA/model-always-on"
SETTINGS_FILE_PATH="$DASH_DATA/settings.json"

MODEL_ALWAYS_ON=0
if [ "${HERMES_MODEL_ALWAYS_ON:-0}" = "1" ] || [ -f "$ALWAYS_ON_MARKER" ]; then
  MODEL_ALWAYS_ON=1
fi

if [ "$MODEL_ALWAYS_ON" = "1" ]; then
  : > "$ALWAYS_ON_MARKER"
  rm -f "$AUTOSTART_MARKER"
  echo "HERMES_MODEL_ALWAYS_ON=1 — the model server will stay always-on (opted out of the 2026-09-10 on-demand default)."
elif [ ! -e "$SETTINGS_FILE_PATH" ] && [ ! -e "$AUTOSTART_MARKER" ]; then
  : > "$AUTOSTART_MARKER"
  echo "Fresh install: model server set to on-demand by default (touched model-autostart-off)."
  echo "  Set HERMES_MODEL_ALWAYS_ON=1 before running this script to keep it always-on instead."
fi

unload() {
  launchctl bootout "gui/$UID_N/$1" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
  unload "$MLX_LABEL"; unload "$BG_LABEL"; unload "$DASH_LABEL"; unload "$SERVE_LABEL"
  rm -f "$MLX_PLIST" "$BG_PLIST" "$DASH_PLIST" "$SERVE_PLIST"
  echo "Services removed."
  exit 0
fi

# PATH for the services: python framework bin (mlx), ~/.local/bin (hermes),
# homebrew, system.
SVC_PATH="$(dirname "$PY"):$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Every value interpolated into a plist below is XML-escaped first: a raw '&'
# or '<' in the install path, $HOME or the log dir made the plist invalid XML
# and `launchctl bootstrap` failed (2026-10-08 audit, P3). sed rather than
# ${s//&/...}: bash 5.2's patsub_replacement turns an unquoted '&' in the
# replacement into the matched text. The X_ copies are for XML only — the raw
# variables stay what every file path and the summary use.
xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
                         -e 's/"/\&quot;/g' -e "s/'/\\&apos;/g"
}
X_HERE="$(xml_escape "$HERE")"
X_HOME="$(xml_escape "$HOME")"
X_LOGS="$(xml_escape "$LOGS")"
X_PY="$(xml_escape "$PY")"
X_SVC_PATH="$(xml_escape "$SVC_PATH")"
X_SERVE_TOKEN="$(xml_escape "$SERVE_TOKEN")"

# The two MODEL plists are rendered to a temp file first (2026-11-09 audit
# E129): a model job whose plist did not change and that launchd already has
# loaded is left alone — booting it out stopped a running lane, and a
# background lane started by hand stayed down after every update. Only a
# changed (or not yet loaded) model job is reloaded. The always-on pair is
# reloaded every time, as before.
MLX_NEW="$(mktemp "$HOME/Library/LaunchAgents/.$MLX_LABEL.XXXXXX")"
BG_NEW="$(mktemp "$HOME/Library/LaunchAgents/.$BG_LABEL.XXXXXX")"
trap 'rm -f "$MLX_NEW" "$BG_NEW"' EXIT

cat > "$MLX_NEW" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$MLX_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$X_HERE/mlx-server.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$X_HERE</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$X_SVC_PATH</string>
    <key>HOME</key><string>$X_HOME</string>
$( [ "$MODEL_ALWAYS_ON" = "1" ] && printf '    <key>HERMES_MODEL_ALWAYS_ON</key><string>1</string>\n' )
  </dict>
  <!-- On-demand (2026-09-01): the model does NOT start at login and is not
       kept alive; the dashboard starts it (bootstrap+kickstart, with a start
       token for mlx-server.sh's gate) when the user actually needs it. A
       HERMES_MODEL_ALWAYS_ON key above (2026-09-10 audit A09) means the
       owner explicitly opted out of that gate at install time. -->
  <key>RunAtLoad</key><false/>
  <key>KeepAlive</key><false/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$X_LOGS/mlx-server.log</string>
  <key>StandardErrorPath</key><string>$X_LOGS/mlx-server.log</string>
</dict></plist>
EOF

# Background lane — a second small model server (:8081) for briefing /
# watchtower / news producers so the primary model stays warm for the user.
cat > "$BG_NEW" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$BG_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$X_HERE/mlx-server-bg.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$X_HERE</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$X_SVC_PATH</string>
    <key>HOME</key><string>$X_HOME</string>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>KeepAlive</key><false/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$X_LOGS/mlx-bg.log</string>
  <key>StandardErrorPath</key><string>$X_LOGS/mlx-bg.log</string>
</dict></plist>
EOF

cat > "$DASH_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$DASH_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$X_PY</string><string>$X_HERE/dashboard/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$X_HERE</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$X_SVC_PATH</string>
    <key>HOME</key><string>$X_HOME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$X_LOGS/dashboard.log</string>
  <key>StandardErrorPath</key><string>$X_LOGS/dashboard.log</string>
</dict></plist>
EOF

# HERMES_TUI_CHECKPOINTS: serve builds dashboard-chat agents with checkpoints
# from this env var ONLY (config.yaml's checkpoints.enabled reaches the CLI
# alone), and without them the Flight Recorder can never Undo a dashboard
# write (2026-10-21 audit C114). It is necessary, not sufficient: Hermes
# snapshots the nearest folder holding a project marker (.git, package.json,
# pyproject.toml, Makefile, ...) above the written file and skips that folder
# when it is $HOME or /. So a write inside a repo is undoable, while one in
# ~/Desktop or ~/Documents is not when $HOME itself holds a marker (this
# Mac's does: ~/package.json). Terminal commands run in serve's cwd, $HOME,
# and are skipped the same way.
#
# hermes serve WITHOUT its web UI (2026-10-21 audit C117).  Hermes' SPA mount
# answers an unauthenticated GET / with index.html carrying
# window.__HERMES_SESSION_TOKEN__, and its CORS / WebSocket origin rules admit
# every loopback port, so any page a local dev server hands the browser could
# read this token and drive the agent.  The hub only ever speaks /api/ws
# (dashboard/hermes_rpc.py), so serve does not need the UI at all.  0.18 has no
# flag for that; web_server.py reads HERMES_WEB_DIST at import, and pointed at
# a path that can never exist (/dev/null is not a directory) its mount_spa()
# registers a JSON 404 instead of the SPA.  `--skip-build` has to go with it:
# that flag REQUIRES <dist>/index.html and exits 1 without it, while an
# explicit HERMES_WEB_DIST already skips the npm build.  The plist is created
# 0600 from its first byte (umask, on a fresh inode — `cat >` onto an older
# 0644 copy would keep that mode), not chmod'ed after: it embeds the token.
SERVE_NO_WEB_UI="/dev/null/hermes-serve-has-no-web-ui"
( umask 077
rm -f "$SERVE_PLIST"
cat > "$SERVE_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$SERVE_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$X_HOME/.local/bin/hermes</string><string>serve</string>
    <string>--port</string><string>9119</string>
    <string>--host</string><string>127.0.0.1</string>
  </array>
  <key>WorkingDirectory</key><string>$X_HOME</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$X_SVC_PATH</string>
    <key>HOME</key><string>$X_HOME</string>
    <key>HERMES_DASHBOARD_SESSION_TOKEN</key><string>$X_SERVE_TOKEN</string>
    <key>HERMES_TUI_CHECKPOINTS</key><string>1</string>
    <key>HERMES_WEB_DIST</key><string>$SERVE_NO_WEB_UI</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$X_LOGS/serve.log</string>
  <key>StandardErrorPath</key><string>$X_LOGS/serve.log</string>
</dict></plist>
EOF
)
chmod 600 "$SERVE_PLIST"   # embeds the auth token (already 0600; belt and braces)

# (re)load — give launchd a beat between bootout and bootstrap, or it can
# fail with "Bootstrap failed: 5: Input/output error" while the old
# process is still terminating.
# The always-on pair is bootstrapped first (2026-10-08 audit, P3): under
# `set -e` one "Bootstrap failed: 5" on a model plist used to end the script
# before the dashboard was loaded at all.
job_loaded() { launchctl print "gui/$UID_N/$1" >/dev/null 2>&1; }
# stage_model <new> <plist> <label> -> prints keep | reload | recode | load
#   keep    same plist, loaded, and the caller (update.sh) vouched that the
#           lane's code did not change: not touched (a running lane keeps
#           running)
#   reload  the plist changed while loaded: booted out and bootstrapped
#   recode  same plist, loaded, but the lane's code changed — or nobody said it
#           did not (a plain reinstall, an older update.sh): reloaded, because
#           a running lane would otherwise keep the launcher it started with
#           (an always-on primary until the next reboot)
#   load    not loaded: bootstrapped (nothing to stop)
stage_model() {
  local new="$1" dst="$2" label="$3" loaded=0
  job_loaded "$label" && loaded=1
  if [ "$loaded" = "1" ] && [ -f "$dst" ] && cmp -s "$new" "$dst"; then
    if [ "${HERMES_MODEL_CODE_UNCHANGED:-}" = "1" ]; then
      rm -f "$new"; echo keep; return
    fi
    rm -f "$new"; echo recode; return
  fi
  chmod 644 "$new"; mv -f "$new" "$dst"
  if [ "$loaded" = "1" ]; then echo reload; else echo load; fi
}
MLX_ACT="$(stage_model "$MLX_NEW" "$MLX_PLIST" "$MLX_LABEL")"
BG_ACT="$(stage_model "$BG_NEW" "$BG_PLIST" "$BG_LABEL")"

case "$MLX_ACT" in reload|recode) unload "$MLX_LABEL" ;; esac
case "$BG_ACT" in reload|recode) unload "$BG_LABEL" ;; esac
unload "$DASH_LABEL"; unload "$SERVE_LABEL"
sleep 3
launchctl bootstrap "gui/$UID_N" "$DASH_PLIST"
launchctl bootstrap "gui/$UID_N" "$SERVE_PLIST"
[ "$MLX_ACT" = "keep" ] || launchctl bootstrap "gui/$UID_N" "$MLX_PLIST"
[ "$BG_ACT" = "keep" ] || launchctl bootstrap "gui/$UID_N" "$BG_PLIST"
model_note() {   # what happened to one model lane, in words
  case "$1" in
    keep)   echo "unchanged — left exactly as it was (a running lane kept running)" ;;
    reload) echo "its plist changed — reloaded, which STOPPED it if it was running" ;;
    recode) echo "reloaded so it runs this version's code, which STOPPED it if it was running" ;;
    load)   echo "loaded (it was not loaded before)" ;;
  esac
}

# ---------------------------------------------------------------------------
# The first self-update hop from an update.sh that predates the post-update
# hand-over (2026-11-09 audit E29). An older update.sh (1.3.2 and earlier)
# keeps running its OWN text after the code swap, so it never links or
# enables the plugins this release ships (approval-gate) and never restarts
# the Telegram gateway — but it does run THIS file by path. So when an update
# runs us, we ask the incoming update.sh for exactly those steps.
#   * "An update runs us" = the dashboard's updater environment
#     (HERMES_UPDATE_FROM=dashboard) or a parent process that is an update.sh.
#     A plain ./install-services.sh, or install.sh, never gets here: neither
#     links plugins nor restarts the gateway, as before.
#   * A current update.sh does those steps itself and exports
#     HERMES_UPD_IN_POST=1 before calling us, so nothing runs twice and
#     nothing loops.
#   * Never fatal: the services above are already in place.
# ---------------------------------------------------------------------------
called_by_update() {
  [ "${HERMES_UPDATE_FROM:-}" = "dashboard" ] && return 0
  local pcmd
  pcmd="$(ps -o command= -p "$PPID" 2>/dev/null || true)"
  case " $pcmd " in
    *"/update.sh "*|*" update.sh "*) return 0 ;;
  esac
  return 1
}
if [ "${HERMES_UPD_IN_POST:-}" != "1" ] && [ -f "$HERE/update.sh" ] && called_by_update; then
  echo "an older update.sh is running this file — asking the incoming update.sh to link the plugins and restart the Telegram gateway"
  HERMES_UPD_IN_POST=1 bash "$HERE/update.sh" --post-update --from-install-services \
    || echo "WARNING: the post-update plugin/gateway step failed — run ./install.sh or: ./update.sh --post-update"
fi

echo "✓ installed:"
if [ "$MODEL_ALWAYS_ON" = "1" ]; then
  echo "    $MLX_LABEL   (model server :8080, ALWAYS-ON — HERMES_MODEL_ALWAYS_ON=1, log: $LOGS/mlx-server.log)"
else
  echo "    $MLX_LABEL   (model server :8080, ON-DEMAND — starts on first use, log: $LOGS/mlx-server.log)"
fi
echo "    $BG_LABEL       (background model :8081, OFF — nothing starts it; see mlx-server-bg.sh, log: $LOGS/mlx-bg.log)"
echo "  model lanes: primary $(model_note "$MLX_ACT"); background $(model_note "$BG_ACT")."
if [ "$BG_ACT" = "reload" ] || [ "$BG_ACT" = "recode" ]; then
  echo "  The background lane only runs when you start it by hand — start it again if you had."
fi
echo "    $DASH_LABEL  (dashboard   :7788, always-on, log: $LOGS/dashboard.log)"
echo "    $SERVE_LABEL     (agent backend :9119, always-on, log: $LOGS/serve.log)"
echo "  Dashboard + serve start at login; the primary model stays OFF until a"
echo "  chat/Telegram turn (or the menu's Wake now) starts it. The background"
echo "  lane runs only when you start it by hand; until then background work"
echo "  uses the primary while it is awake."
echo "  Manage:  launchctl kickstart -k gui/$UID_N/$DASH_LABEL   (restart)"
echo "           $0 --uninstall                                   (remove)"
