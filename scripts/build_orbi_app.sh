#!/usr/bin/env bash
# Build Orbi.app — Orbi on the Mac desktop without a browser (Phase 19.6) — and install
# it in ~/Applications. Usage: scripts/build_orbi_app.sh [--open]
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
OUT="$ROOT/dist/Orbi.app"
rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"

swiftc -O -o "$OUT/Contents/MacOS/Orbi" desktop/Orbi/main.swift \
  -framework AppKit -framework WebKit -framework ServiceManagement

cat > "$OUT/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Orbi</string>
  <key>CFBundleDisplayName</key><string>Orbi</string>
  <key>CFBundleIdentifier</key><string>ai.orbit.orbi</string>
  <key>CFBundleExecutable</key><string>Orbi</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Orbi listens when you click it, so you can talk to ORBIT.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>ORBITRoot</key><string>$ROOT</string>
</dict></plist>
PLIST

codesign --force --deep --sign - "$OUT" >/dev/null 2>&1   # ad-hoc signature (local use)
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/Orbi.app"
cp -R "$OUT" "$HOME/Applications/Orbi.app"
echo "Orbi.app installed in ~/Applications (open it from Launchpad, Spotlight or Finder)."
if [ "${1:-}" = "--open" ]; then open "$HOME/Applications/Orbi.app"; fi
