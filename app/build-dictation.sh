#!/usr/bin/env bash
# build-dictation.sh — build the "Hermes Dictation" push-to-talk helper.
#
# A SEPARATE bundle from Hermes Assistant.app, deliberately.  The main app holds
# Full Disk Access and its ad-hoc signature must not change (see CLAUDE.md:
# rebuilding it drops that grant), so dictation — which needs its own
# Microphone and Accessibility grants and will be rebuilt often — lives in its
# own bundle that can be rebuilt freely.  This script NEVER touches
# app/build-app.sh, "Hermes Assistant.app", or /Applications.
#
# AD-HOC SIGNING CAVEAT.  `codesign -s -` gives the bundle no stable
# TeamIdentifier, so TCC keys the Microphone and Accessibility grants to the
# code's cdhash — which changes on every rebuild.  EVERY REBUILD THEREFORE
# RESETS BOTH PERMISSIONS and they must be granted again in System Settings.
# Creating a self-signed code-signing identity in the login keychain and passing
# it as HERMES_SIGN_ID gives a stable identity, and the grants then survive
# rebuilds.  This script does not create certificates for you; that is the
# owner's decision to make once, by hand.
#
#   bash app/build-dictation.sh                      # build into app/build
#   HERMES_SIGN_ID="Hermes Dev" bash app/...         # sign with a real identity
#
# NOTE: app/build-app.sh clears the WHOLE app/build directory before it compiles,
# so building the main app removes this bundle. Re-run this script afterwards —
# and expect to re-grant Microphone and Accessibility, since that is a rebuild.
#
# The bundle is left in app/build.  Nothing is installed and nothing is
# launched: the helper must be started by the user (double-click, or "Start at
# login" in its own menu), because a GUI launch is what makes it the
# responsible process for the microphone prompt.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BUILD="$HERE/build"
APP="$BUILD/Hermes Dictation.app"
BIN="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"
SRC="$HERE/dictation/main.swift"

# Same version stamp as build-app.sh: repo-root VERSION + short git sha.
VER="$(tr -d ' \t\n\r' < "$ROOT/VERSION" 2>/dev/null || true)"
[ -n "$VER" ] || VER="0.0.0"
SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || true)"
BUILDVER="$VER${SHA:+ +$SHA}"
BUILDVER="${BUILDVER// /}"

# SpeechAnalyzer is macOS 26; SFSpeechRecognizer is the fallback below that, so
# the deployment target is 14.0 and the availability checks in main.swift decide
# at runtime.  Without an explicit -target, swiftc would pin the deployment
# target to whatever macOS built it and the fallback branch would be dead code.
TARGET="${HERMES_DICTATION_TARGET:-$(uname -m)-apple-macosx14.0}"

command -v swiftc >/dev/null 2>&1 || {
  echo "swiftc not found — install the Xcode command line tools (xcode-select --install)" >&2
  exit 1
}

rm -rf "$APP"; mkdir -p "$BIN" "$RES"

echo "→ compiling (target $TARGET)"
swiftc -O -target "$TARGET" -o "$BIN/HermesDictation" "$SRC" \
  -framework AppKit -framework AVFoundation -framework Speech \
  -framework ApplicationServices -framework CoreGraphics -framework Carbon \
  -framework ServiceManagement

echo "→ Info.plist (version $VER, build $BUILDVER)"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Hermes Dictation</string>
  <key>CFBundleDisplayName</key><string>Hermes Dictation</string>
  <key>CFBundleIdentifier</key><string>local.hermes.dictation</string>
  <key>CFBundleExecutable</key><string>HermesDictation</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VER</string>
  <key>CFBundleVersion</key><string>$BUILDVER</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Hermes Dictation listens while you hold the dictation hotkey so it can turn your speech into text. Audio is transcribed on this Mac and never leaves it.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>Hermes Dictation transcribes what you say using the on-device speech recogniser. Nothing is sent to a server.</string>
  <key>NSAppTransportSecurity</key><dict>
    <key>NSAllowsLocalNetworking</key><true/>
  </dict>
</dict></plist>
EOF

SIGN_ID="${HERMES_SIGN_ID:--}"
if [ "$SIGN_ID" = "-" ]; then
  echo "→ signing (ad-hoc — permissions reset on every rebuild, see the header)"
else
  echo "→ signing ($SIGN_ID)"
fi
codesign --force --deep -s "$SIGN_ID" "$APP"

echo "✓ built: $APP"
echo
echo "  Next, BY HAND (this script never launches it — a GUI launch is what makes"
echo "  the helper the responsible process for the microphone prompt):"
echo "    open \"$APP\""
echo "  then grant Microphone and Accessibility in System Settings > Privacy &"
echo "  Security when it asks. Hold Right Option to dictate."
