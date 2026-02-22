#!/bin/bash
set -e

echo "=== SysMon Installer ==="
echo ""

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "Error: SysMon only supports macOS."
    exit 1
fi

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is required."
    echo "Install Xcode Command Line Tools: xcode-select --install"
    exit 1
fi

echo "[1/3] Installing dependencies..."
pip3 install --user -r requirements.txt

echo "[2/3] Creating SysMon.app..."
APP_DIR="$HOME/Applications/SysMon.app"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Info.plist
cat > "$APP_DIR/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>SysMon</string>
    <key>CFBundleIdentifier</key>
    <string>com.user.sysmon</string>
    <key>CFBundleName</key>
    <string>SysMon</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
PLIST

# Launcher binary (compile small C wrapper)
cat > /tmp/_sysmon_launcher.c << CEOF
#include <unistd.h>
#include <fcntl.h>
int main(void) {
    int fd = open("/dev/null", O_WRONLY);
    if (fd >= 0) { dup2(fd, 2); close(fd); }
    execl("/usr/bin/python3", "python3",
          "${SCRIPT_DIR}/ram_widget.py", NULL);
    return 1;
}
CEOF
cc -O2 -o "$APP_DIR/Contents/MacOS/SysMon" /tmp/_sysmon_launcher.c
rm /tmp/_sysmon_launcher.c

echo "[3/3] Registering app..."
# Remove quarantine if present
xattr -rd com.apple.quarantine "$APP_DIR" 2>/dev/null || true

echo ""
echo "=== Installation complete! ==="
echo ""
echo "  Launch: open ~/Applications/SysMon.app"
echo "  Or search 'SysMon' in Spotlight"
echo ""
echo "  To start on login:"
echo "    osascript -e 'tell application \"System Events\" to make login item at end with properties {path:\"$APP_DIR\", hidden:true}'"
echo ""
