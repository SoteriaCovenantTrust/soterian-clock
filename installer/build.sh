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

# ---------------------------------------------------------------------------
# Post-build trim — PyInstaller bundles a lot we don't need
# ---------------------------------------------------------------------------
# pystray's GTK appindicator backend pulls in gdk-pixbuf, which pulls in
# every loader (including AV1/JXL/SVG codecs we never decode), every icon
# theme on the system (Papirus, Adwaita), and animated cursor frames.
# A 4.7 MB binary should not require a 500 MB bundle. We trim aggressively
# but conservatively — only stuff we KNOW the widget doesn't use.

INTERNAL="dist/soterian-clock/_internal"
if [ -d "$INTERNAL" ]; then
    BEFORE_KB=$(du -sk "dist/soterian-clock" 2>/dev/null | awk '{print $1}')

    # Image/video codec libs pulled in by gdk-pixbuf loaders. We use
    # exactly one PNG (the tray icon) — none of these are reachable.
    for lib in libSvtAv1Enc libaom librav1e librsvg-2 libjxl libdav1d libde265 \
               libheif libavif libwebp libwebpmux libwebpdemux \
               libgif libtiff libjasper libopenjp2; do
        find "$INTERNAL" -maxdepth 1 -type f -name "${lib}.so*" -delete 2>/dev/null || true
    done

    # GTK icon theme caches + animated cursor frames. The tray icon is
    # rendered from our own PIL Image, not pulled from a theme — these
    # are dead weight. We keep the theme dirs themselves (some hooks
    # check for existence) but evict the heavy data inside.
    rm -rf "$INTERNAL/share/icons/Papirus" "$INTERNAL/share/icons/Papirus-Dark" \
           "$INTERNAL/share/icons/Papirus-Light" 2>/dev/null || true
    rm -rf "$INTERNAL/share/icons/Adwaita/cursors" 2>/dev/null || true
    # Other icon themes pulled in (Tango, gnome, etc.) — same reasoning.
    for theme in Tango gnome Humanity hicolor-fallback Faenza HighContrast Mint-X breeze-cursors; do
        rm -rf "$INTERNAL/share/icons/$theme" 2>/dev/null || true
    done

    # Locale data we don't ship UI strings for. Keep `en` only; the widget's
    # own UI is English-only as of v2.6.x. (Localization scaffolding tracked
    # separately; until that lands, every other locale dir is unused.)
    if [ -d "$INTERNAL/share/locale" ]; then
        find "$INTERNAL/share/locale" -mindepth 1 -maxdepth 1 -type d \
             ! -name "en" ! -name "en_US" ! -name "C" -exec rm -rf {} + 2>/dev/null || true
    fi

    # gdk-pixbuf loaders for codecs we just deleted (avoids load-time
    # warnings about missing loaders we don't want anyway).
    find "$INTERNAL" -path "*gdk-pixbuf*loaders*" -name "*.so" \
         \( -name "*svg*" -o -name "*tiff*" -o -name "*webp*" -o -name "*jxl*" \
            -o -name "*avif*" -o -name "*heif*" -o -name "*jp2*" \) \
         -delete 2>/dev/null || true

    AFTER_KB=$(du -sk "dist/soterian-clock" 2>/dev/null | awk '{print $1}')
    SAVED_KB=$(( BEFORE_KB - AFTER_KB ))
    echo ""
    echo "Trim:     $((BEFORE_KB / 1024)) MB -> $((AFTER_KB / 1024)) MB  (saved $((SAVED_KB / 1024)) MB)"
fi

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

# Daily diagnostic — runs `soterian-clock --diagnostic` once a day and
# captures the report to journald. Failures (any check returning non-OK)
# show up as a `failed` systemd user unit, which the maintainer's
# morning-brief routine catches via `systemctl --user --failed`.
# Closes the silent-degradation gap: connection breaks, keyring
# inaccessible, calendar API down, etc. surface within ~24h instead of
# whenever the user happens to look at the dashbar.
cat > "$SYSTEMD_USER_DIR/soterian-clock-diagnostic.service" << EOF
[Unit]
Description=Soterian Clock daily self-check

[Service]
Type=oneshot
ExecStart=$INSTALL_DIR/soterian-clock --diagnostic
StandardOutput=journal
StandardError=journal
SyslogIdentifier=soterian-clock-diagnostic
EOF

cat > "$SYSTEMD_USER_DIR/soterian-clock-diagnostic.timer" << EOF
[Unit]
Description=Run Soterian Clock --diagnostic daily

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=30min
Unit=soterian-clock-diagnostic.service

[Install]
WantedBy=timers.target
EOF

# Retire any pre-2.0.3 XDG autostart entry so the widget doesn't double-launch
if [ -f "$OLD_AUTOSTART" ]; then
    mv "$OLD_AUTOSTART" "$OLD_AUTOSTART.bak"
    echo "Retired old XDG autostart: $OLD_AUTOSTART → ${OLD_AUTOSTART}.bak"
fi

# Reload + enable + start
systemctl --user daemon-reload
systemctl --user enable --now soterian-clock.service
systemctl --user enable --now soterian-clock-diagnostic.timer

echo ""
echo "Installed to:  $INSTALL_DIR"
echo "Symlink:       $BIN_LINK"
echo "Systemd unit:  $SYSTEMD_USER_DIR/soterian-clock.service"
echo ""
echo "The widget is running and will start automatically on each login."
echo "  Status:    systemctl --user status soterian-clock"
echo "  Logs:      journalctl --user -u soterian-clock -f"
echo "  Stop:      systemctl --user disable --now soterian-clock"
echo "  Diagnose:  soterian-clock --diagnostic   (also runs daily via timer)"
echo ""
echo "Uninstall: bash $INSTALL_DIR/uninstall.sh"
INSTALLER
        chmod +x dist/soterian-clock/install.sh

        # Bundle uninstall.sh too — symmetric with install.sh, removes the
        # systemd unit, the install dir, the symlink, and the keyring entry.
        cat > dist/soterian-clock/uninstall.sh << 'UNINSTALLER'
#!/usr/bin/env bash
# Soterian Clock — Linux Uninstaller.
# Reverses what install.sh did. Idempotent: safe to re-run.
set -euo pipefail

INSTALL_DIR="$HOME/.local/share/soterian-clock"
BIN_LINK="$HOME/.local/bin/soterian-clock"
UNIT="$HOME/.config/systemd/user/soterian-clock.service"
DIAG_SVC="$HOME/.config/systemd/user/soterian-clock-diagnostic.service"
DIAG_TIMER="$HOME/.config/systemd/user/soterian-clock-diagnostic.timer"
SETTINGS_DIR="$HOME/.config/soterian-clock"

echo "Uninstalling Soterian Clock..."

# Stop + disable the systemd user unit + the daily diagnostic timer
# (errors are fine — units may already be gone if a prior uninstall ran).
for u in soterian-clock-diagnostic.timer soterian-clock-diagnostic.service soterian-clock.service; do
    if systemctl --user list-unit-files 2>/dev/null | grep -q "^${u}"; then
        systemctl --user disable --now "$u" 2>/dev/null || true
    fi
done
for f in "$UNIT" "$DIAG_SVC" "$DIAG_TIMER"; do
    if [ -f "$f" ]; then
        rm -f "$f"
        echo "  Removed systemd unit:  $f"
    fi
done
systemctl --user daemon-reload 2>/dev/null || true

# Remove the install dir + symlink
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "  Removed install dir:  $INSTALL_DIR"
fi
if [ -L "$BIN_LINK" ]; then
    rm -f "$BIN_LINK"
    echo "  Removed symlink:      $BIN_LINK"
fi

# Remove the widget token from the OS keyring (best-effort: keyring may
# not be installed system-wide; keyring CLI is `secret-tool` on libsecret).
# We try secret-tool first, then python -c keyring as a fallback.
KEYRING_REMOVED="no"
if command -v secret-tool >/dev/null 2>&1; then
    if secret-tool clear service soterian-clock username widget-token 2>/dev/null; then
        KEYRING_REMOVED="yes"
    fi
fi
if [ "$KEYRING_REMOVED" = "no" ] && command -v python3 >/dev/null 2>&1; then
    python3 -c "
try:
    import keyring
    keyring.delete_password('soterian-clock', 'widget-token')
    print('  Removed keyring entry')
except Exception:
    pass
" 2>/dev/null || true
else
    [ "$KEYRING_REMOVED" = "yes" ] && echo "  Removed keyring entry"
fi

# settings.json may hold non-token member metadata + position + ui_scale —
# ask before deleting since the user may want to preserve preferences for
# a future re-install.
if [ -d "$SETTINGS_DIR" ]; then
    echo ""
    read -r -p "Also remove $SETTINGS_DIR (member metadata + window position + alert seen-set)? [y/N] " ans
    if [ "${ans:-n}" = "y" ] || [ "${ans:-n}" = "Y" ]; then
        rm -rf "$SETTINGS_DIR"
        echo "  Removed settings dir: $SETTINGS_DIR"
    else
        echo "  Kept settings dir:    $SETTINGS_DIR"
    fi
fi

echo ""
echo "Done. Re-install with: bash install.sh (from a fresh tarball)."
UNINSTALLER
        chmod +x dist/soterian-clock/uninstall.sh

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
