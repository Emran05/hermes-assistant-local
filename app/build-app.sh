#!/usr/bin/env bash
# build-app.sh — build the native Hermes Assistant.app and install it.
# Produces a self-contained, ad-hoc-signed app bundle. Re-run after changes.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BUILD="$HERE/build"
APP="$BUILD/Hermes Assistant.app"
BIN="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"

# version stamp — repo-root VERSION file + short git sha (see update.sh /
# aux_update.py, which read the same VERSION for the update checker).
VER="$(tr -d ' \t\n\r' < "$ROOT/VERSION" 2>/dev/null || true)"
[ -n "$VER" ] || VER="0.0.0"
SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || true)"
BUILDVER="$VER${SHA:+ +$SHA}"
BUILDVER="${BUILDVER// /}"

rm -rf "$BUILD"; mkdir -p "$BIN" "$RES"

echo "→ compiling"
swiftc -O -o "$BIN/HermesAssistant" "$HERE/main.swift" \
  -framework AppKit -framework WebKit -framework Carbon -framework ServiceManagement

echo "→ icon"
swift "$HERE/render-icon.swift" "$BUILD/icon-1024.png" >/dev/null
ICONSET="$BUILD/AppIcon.iconset"; mkdir -p "$ICONSET"
for sz in 16 32 128 256 512; do
  sips -z $sz $sz       "$BUILD/icon-1024.png" --out "$ICONSET/icon_${sz}x${sz}.png"      >/dev/null
  sips -z $((sz*2)) $((sz*2)) "$BUILD/icon-1024.png" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns"

echo "→ Info.plist (version $VER, build $BUILDVER)"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Hermes Assistant</string>
  <key>CFBundleDisplayName</key><string>Hermes Assistant</string>
  <key>CFBundleIdentifier</key><string>local.hermes.assistant</string>
  <key>CFBundleExecutable</key><string>HermesAssistant</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VER</string>
  <key>CFBundleVersion</key><string>$BUILDVER</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict>
    <key>NSAllowsLocalNetworking</key><true/>
  </dict>
</dict></plist>
EOF

echo "→ signing (ad-hoc)"
codesign --force --deep -s - "$APP"

# CI / release builds only want the bundle in app/build — they package it
# themselves and must not touch /Applications.
if [ "${HERMES_SKIP_INSTALL:-0}" = "1" ]; then
  echo "✓ built: $APP  (HERMES_SKIP_INSTALL=1 — not installed)"
  exit 0
fi

# install: prefer /Applications, fall back to ~/Applications
DEST="/Applications"
[ -w "$DEST" ] || DEST="$HOME/Applications"
rm -rf "$DEST/Hermes Assistant.app"
cp -R "$APP" "$DEST/"
# remove the old script-based launcher if it lingers in the other location
[ "$DEST" = "/Applications" ] && rm -rf "$HOME/Applications/Hermes Assistant.app" || true

echo "✓ installed: $DEST/Hermes Assistant.app"
