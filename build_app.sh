#!/bin/bash
# Build LAIN-tools.app in the current directory. The app is self-contained
# (includes venv and scripts) so you can move it to Applications and use
# "Launch at Login" from the menu.

set -e
APP_NAME="LAIN-tools"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ROOT="$SCRIPT_DIR/${APP_NAME}.app"
CONTENTS="$APP_ROOT/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

echo "Building ${APP_NAME}.app in $SCRIPT_DIR"

# Remove existing build
rm -rf "$APP_ROOT"

# Create bundle structure
mkdir -p "$MACOS" "$RESOURCES"

# Launcher script (must use same name as .app for CFBundleExecutable)
# Resolves Resources to absolute path and surfaces errors so the app doesn't fail silently
cat > "$MACOS/$APP_NAME" << 'LAUNCHER'
#!/bin/bash
set -e
BIN="$(dirname "$0")"
RESOURCES="$(cd "$BIN/../Resources" && pwd)"
cd "$RESOURCES" || exit 1
export VIRTUAL_ENV="$RESOURCES/venv"
export PATH="$RESOURCES/venv/bin:$PATH"

PYTHON="$RESOURCES/venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  # Fallback: try python3 in venv (some venvs only have python3)
  if [ -x "$RESOURCES/venv/bin/python3" ]; then
    PYTHON="$RESOURCES/venv/bin/python3"
  else
    osascript -e "display alert \"LAIN-tools\" message \"Python not found in app. Re-run build_app.sh from the project folder.\" as critical"
    exit 1
  fi
fi

LOG=$(mktemp)
if ! "$PYTHON" "./lain_tools.py" >> "$LOG" 2>&1; then
  ERR=$(cat "$LOG" | head -20 | tr '\n' ' ' | sed 's/"/\\"/g')
  osascript -e "display alert \"LAIN-tools failed to start\" message \"${ERR:0:500}\" as critical" 2>/dev/null || true
  rm -f "$LOG"
  exit 1
fi
rm -f "$LOG"
LAUNCHER
chmod +x "$MACOS/$APP_NAME"

# Copy app files
cp "$SCRIPT_DIR/lain_tools.py" "$RESOURCES/"
cp "$SCRIPT_DIR/lan_scan.py" "$RESOURCES/"
cp "$SCRIPT_DIR/requirements.txt" "$RESOURCES/"
[ -f "$SCRIPT_DIR/icon.png" ] && cp "$SCRIPT_DIR/icon.png" "$RESOURCES/"

# Create a fresh venv inside the app and install deps
# Build psutil from source so the C extension matches this Python and path (avoids .so load failures in app bundle)
echo "Creating venv and installing dependencies..."
"${SCRIPT_DIR}/venv/bin/python3" -m venv "$RESOURCES/venv"
"$RESOURCES/venv/bin/pip" install --quiet --upgrade pip
"$RESOURCES/venv/bin/pip" install --quiet rumps speedtest-cli
"$RESOURCES/venv/bin/pip" install --quiet --no-binary psutil psutil

# Info.plist (menu bar app: no Dock icon)
cat > "$CONTENTS/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key>
	<string>$APP_NAME</string>
	<key>CFBundleIdentifier</key>
	<string>com.lain.tools</string>
	<key>CFBundleName</key>
	<string>$APP_NAME</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0.0</string>
	<key>LSUIElement</key>
	<true/>
	<key>NSHighResolutionCapable</key>
	<true/>
</dict>
</plist>
EOF

echo "Done. Launching the app..."
open "$APP_ROOT"

echo "You can drag $APP_ROOT to Applications. Use the app's menu: Launch at Login — to start at login."
