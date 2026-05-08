#!/usr/bin/env bash
#
# Build Soterian Clock Widget installer for the current platform.
#
# Usage:
#   cd /opt/soteria_global/engines/calendar
#   bash installer/build.sh
#
# Output:
#   dist/soterian-clock/          — runnable bundle
#   dist/soterian-clock.tar.gz    — distributable archive (Linux)
#   dist/SoterianClock.zip        — distributable archive (macOS/Windows)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

PLATFORM="$(uname -s)"
ARCH="$(uname -m)"

echo "=== Soterian Clock Widget — Build ==="
echo "Platform: $PLATFORM ($ARCH)"
echo "Project:  $PROJECT_DIR"
echo ""

# Check dependencies
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found"; exit 1; }
python3 -c "import PyInstaller" 2>/dev/null || { echo "ERROR: PyInstaller not found. Run: pip3 install pyinstaller"; exit 1; }

# Clean previous builds
rm -rf build/soterian-clock dist/soterian-clock

# Build
echo "Building..."
python3 -m PyInstaller installer/soterian_clock.spec --noconfirm --clean 2>&1 | tail -5

# Verify
if [ ! -f "dist/soterian-clock/soterian-clock" ] && [ ! -f "dist/soterian-clock/soterian-clock.exe" ]; then
    echo "ERROR: Build failed — executable not found"
    exit 1
fi

echo ""
echo "Build successful!"
ls -lh dist/soterian-clock/soterian-clock* 2>/dev/null

# Package
echo ""
echo "Packaging..."

# Single source of truth: WIDGET_VERSION constant in soterian_clock.py.
# Bumping the constant alone flows through to the binary, the tarball name, and
# the membership /api/v1/version handshake.
VERSION="$(awk -F'"' '/^WIDGET_VERSION = / {print $2; exit}' soterian_clock.py)"
if [ -z "$VERSION" ]; then
    echo "ERROR: could not parse WIDGET_VERSION from soterian_clock.py" >&2
    exit 1
fi
echo "Version:  $VERSION"

case "$PLATFORM" in
    Linux)
        ARCHIVE="dist/soterian-clock-${VERSION}-linux-${ARCH}.tar.gz"
        # Include a launcher script. The installer wires the widget under a
        # systemd user unit (Restart=on-failure + journal capture) instead of
        # XDG autostart — the v1 widget died silently mid-April 2026 and stayed
        # dead for 3 weeks because its XDG launcher had no supervisor and no
        # log capture. systemd fixes both.
        cat > dist/soterian-clock/install.sh << 'INSTALLER'
#!/usr/bin/env bash
# Soterian Clock Widget — Linux Installer
set -euo pipefail

INSTALL_DIR="$HOME/.local/share/soterian-clock"
BIN_LINK="$HOME/.local/bin/soterian-clock"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
OLD_AUTOSTART="$HOME/.config/autostart/soterian-clock.desktop"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing Soterian Clock Widget..."

# Copy files
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/soterian-clock"
rm -f "$INSTALL_DIR/install.sh"  # Don't need installer inside install dir

# Create bin symlink
mkdir -p "$(dirname "$BIN_LINK")"
ln -sf "$INSTALL_DIR/soterian-clock" "$BIN_LINK"

# Install systemd user unit
mkdir -p "$SYSTEMD_USER_DIR"
cat > "$SYSTEMD_USER_DIR/soterian-clock.service" << EOF
[Unit]
Description=Soterian Floating Clock Widget
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/soterian-clock
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=soterian-clock

[Install]
WantedBy=graphical-session.target
EOF

# Retire any pre-2.0.3 XDG autostart entry so the widget doesn't double-launch
if [ -f "$OLD_AUTOSTART" ]; then
    mv "$OLD_AUTOSTART" "$OLD_AUTOSTART.bak"
    echo "Retired old XDG autostart: $OLD_AUTOSTART → ${OLD_AUTOSTART}.bak"
fi

# Reload + enable + start
systemctl --user daemon-reload
systemctl --user enable --now soterian-clock.service

echo ""
echo "Installed to:  $INSTALL_DIR"
echo "Symlink:       $BIN_LINK"
echo "Systemd unit:  $SYSTEMD_USER_DIR/soterian-clock.service"
echo ""
echo "The widget is running and will start automatically on each login."
echo "  Status:  systemctl --user status soterian-clock"
echo "  Logs:    journalctl --user -u soterian-clock -f"
echo "  Stop:    systemctl --user disable --now soterian-clock"
INSTALLER
        chmod +x dist/soterian-clock/install.sh

        tar -czf "$ARCHIVE" -C dist soterian-clock
        echo "Archive: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
        ;;

    Darwin)
        ARCHIVE="dist/soterian-clock-${VERSION}-macos-${ARCH}.zip"
        cd dist && zip -r "../$ARCHIVE" soterian-clock && cd ..
        echo "Archive: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
        ;;

    MINGW*|MSYS*|CYGWIN*)
        ARCHIVE="dist/soterian-clock-${VERSION}-windows-${ARCH}.zip"
        cd dist && zip -r "../$ARCHIVE" soterian-clock && cd ..
        echo "Archive: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
        ;;

    *)
        echo "Unknown platform: $PLATFORM — skipping archive"
        ;;
esac

echo ""
echo "=== Done ==="
