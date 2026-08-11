#!/usr/bin/env bash
# Install a user LaunchAgent so ترتيب أبو علياء restarts on login / crash (macOS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.tarteeb.abu.alyaa"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
PYTHON="$(command -v python3)"
mkdir -p "$HOME/Library/LaunchAgents"

cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>WorkingDirectory</key><string>${ROOT}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${ROOT}/scripts/keep_alive.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/tarteeb-watchdog.log</string>
  <key>StandardErrorPath</key><string>/tmp/tarteeb-watchdog.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:${HOME}/Library/Python/3.9/bin</string>
  </dict>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed ${PLIST}"
echo "Open http://127.0.0.1:8765"
echo "Unload later: launchctl unload ${PLIST}"
