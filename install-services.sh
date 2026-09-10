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

cat > "$MLX_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$MLX_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$HERE/mlx-server.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$SVC_PATH</string>
    <key>HOME</key><string>$HOME</string>
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
  <key>StandardOutPath</key><string>$LOGS/mlx-server.log</string>
  <key>StandardErrorPath</key><string>$LOGS/mlx-server.log</string>
</dict></plist>
EOF

# Background lane — a second small model server (:8081) for briefing /
# watchtower / news producers so the primary model stays warm for the user.
cat > "$BG_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$BG_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$HERE/mlx-server-bg.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$SVC_PATH</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>KeepAlive</key><false/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/mlx-bg.log</string>
  <key>StandardErrorPath</key><string>$LOGS/mlx-bg.log</string>
</dict></plist>
EOF

cat > "$DASH_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$DASH_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$HERE/dashboard/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$SVC_PATH</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/dashboard.log</string>
  <key>StandardErrorPath</key><string>$LOGS/dashboard.log</string>
</dict></plist>
EOF

cat > "$SERVE_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$SERVE_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$HOME/.local/bin/hermes</string><string>serve</string>
    <string>--port</string><string>9119</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--skip-build</string>
  </array>
  <key>WorkingDirectory</key><string>$HOME</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$SVC_PATH</string>
    <key>HOME</key><string>$HOME</string>
    <key>HERMES_DASHBOARD_SESSION_TOKEN</key><string>$SERVE_TOKEN</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/serve.log</string>
  <key>StandardErrorPath</key><string>$LOGS/serve.log</string>
</dict></plist>
EOF
chmod 600 "$SERVE_PLIST"   # embeds the auth token

# (re)load — give launchd a beat between bootout and bootstrap, or it can
# fail with "Bootstrap failed: 5: Input/output error" while the old
# process is still terminating.
unload "$MLX_LABEL"; unload "$BG_LABEL"; unload "$DASH_LABEL"; unload "$SERVE_LABEL"
sleep 3
launchctl bootstrap "gui/$UID_N" "$MLX_PLIST"
launchctl bootstrap "gui/$UID_N" "$BG_PLIST"
launchctl bootstrap "gui/$UID_N" "$DASH_PLIST"
launchctl bootstrap "gui/$UID_N" "$SERVE_PLIST"

echo "✓ installed:"
if [ "$MODEL_ALWAYS_ON" = "1" ]; then
  echo "    $MLX_LABEL   (model server :8080, ALWAYS-ON — HERMES_MODEL_ALWAYS_ON=1, log: $LOGS/mlx-server.log)"
else
  echo "    $MLX_LABEL   (model server :8080, ON-DEMAND — starts on first use, log: $LOGS/mlx-server.log)"
fi
echo "    $BG_LABEL       (background model :8081, ON-DEMAND, log: $LOGS/mlx-bg.log)"
echo "    $DASH_LABEL  (dashboard   :7788, always-on, log: $LOGS/dashboard.log)"
echo "    $SERVE_LABEL     (agent backend :9119, always-on, log: $LOGS/serve.log)"
echo "  Dashboard + serve start at login; the model servers stay OFF until a"
echo "  chat/Telegram turn (or the menu's Wake now) starts them."
echo "  Manage:  launchctl kickstart -k gui/$UID_N/$DASH_LABEL   (restart)"
echo "           $0 --uninstall                                   (remove)"
