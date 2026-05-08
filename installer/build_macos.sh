#!/usr/bin/env bash
#
# Soterian Clock Widget — macOS Build
#
# Prerequisites:
#   - Python 3.10+ installed
#   - pip3 install pyinstaller pystray Pillow requests
#
# Usage:
#   cd engines/calendar
#   bash installer/build_macos.sh
#
# Output:
#   dist/soterian-clock-2.0.0-macos-{arch}.zip
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

ARCH="$(uname -m)"
VERSION="2.0.0"

echo "=== Soterian Clock Widget — macOS Build ==="
echo "Architecture: $ARCH"

command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found"; exit 1; }
python3 -c "import PyInstaller" 2>/dev/null || { echo "ERROR: PyInstaller not found. Run: pip3 install pyinstaller"; exit 1; }

# Clean
rm -rf build/soterian-clock dist/soterian-clock

# Build
echo "Building..."
python3 -m PyInstaller installer/soterian_clock.spec --noconfirm --clean 2>&1 | tail -5

if [ ! -f "dist/soterian-clock/soterian-clock" ]; then
    echo "ERROR: Build failed"
    exit 1
fi

echo ""
echo "Build successful!"

# Package
ARCHIVE="dist/soterian-clock-${VERSION}-macos-${ARCH}.zip"
cd dist && zip -r "../$ARCHIVE" soterian-clock && cd ..
echo "Archive: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

echo ""
echo "=== Done ==="
echo ""
echo "To install: extract the zip, move soterian-clock folder to /Applications"
echo "To autostart: System Settings → General → Login Items → add soterian-clock"
