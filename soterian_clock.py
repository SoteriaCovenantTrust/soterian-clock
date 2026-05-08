#!/usr/bin/env python3
"""
Soterian Floating Clock Widget v2.0

Cross-platform desktop widget displaying current Soterian time with
astronomical data, branded in Petrachora Soteria gold/dark theme.

Features:
    - Compact multi-line display with Soteria branding
    - System tray icon with context menu
    - Background-threaded API fetching (no UI freeze)
    - Fast /api/date primary + rich /api/now secondary
    - Local segment calculation (no API needed)
    - Draggable, always-on-top window
    - Right-click context menu (refresh, copy, open site, quit)
    - Double-click opens time.soteriacovenant.org
    - Offline caching with connection status indicator
    - Cross-platform (Linux, Windows, macOS)
    - Lock file prevents duplicate instances

Usage:
    python3 soterian_floating_clock.py              # Normal
    python3 soterian_floating_clock.py --background  # Daemonize (Linux/macOS)
    python3 soterian_floating_clock.py --tray-only   # Start minimized to tray
"""

import tkinter as tk
import requests
import webbrowser
import os
import json
import sys
import time
import signal
import threading
import platform
import hashlib
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Cross-platform paths
# ---------------------------------------------------------------------------
_SYSTEM = platform.system()  # "Linux", "Windows", "Darwin"


def _config_dir() -> Path:
    """Return the platform-appropriate config directory."""
    if _SYSTEM == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif _SYSTEM == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "soterian-clock"


def _lock_path() -> Path:
    """Return platform-appropriate lock file path."""
    if _SYSTEM == "Windows":
        return _config_dir() / ".lock"
    return Path("/tmp/.soterian_clock.lock")


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "position.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
CACHE_PATH = CONFIG_DIR / "last_time.json"
LOCKFILE = _lock_path()

# Common timezones for the picker (covers major regions)
TIMEZONE_CHOICES = [
    "System Default",
    "UTC",
    "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
    "Canada/Eastern", "Canada/Central", "Canada/Mountain", "Canada/Pacific",
    "Canada/Atlantic", "Canada/Newfoundland",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Moscow",
    "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata", "Asia/Dubai",
    "Australia/Sydney", "Australia/Perth",
    "Pacific/Auckland", "Pacific/Honolulu",
    "America/Mexico_City", "America/Sao_Paulo",
    "Africa/Johannesburg", "Africa/Cairo",
]

REFRESH_FAST = 60        # /api/date refresh interval (seconds)
REFRESH_RICH = 300       # /api/now refresh interval (seconds)
REFRESH_SYNC = 600       # Member sync interval (seconds)
REFRESH_ALERTS = 14400   # Celestial-event check (4h — events don't change quickly)
API_BASE = "https://time.soteriacovenant.org"
API_DATE = f"{API_BASE}/api/date"
API_NOW = f"{API_BASE}/api/now"
API_MOBILE_SNAPSHOT = f"{API_BASE}/api/v1/mobile/snapshot"
MEMBERSHIP_BASE = "https://members.soteriacovenant.org"
MEMBERSHIP_WIDGET_CONNECT = f"{MEMBERSHIP_BASE}/api/v1/widget/connect"
MEMBERSHIP_WIDGET_SYNC = f"{MEMBERSHIP_BASE}/api/v1/widget/sync"
MEMBERSHIP_WIDGET_TIMEZONE = f"{MEMBERSHIP_BASE}/api/v1/widget/timezone"
MEMBERSHIP_WIDGET_DISCONNECT = f"{MEMBERSHIP_BASE}/api/v1/widget/disconnect"
MEMBERSHIP_VERSION = f"{MEMBERSHIP_BASE}/api/v1/version"
CELEBRATIONS_BASE = "https://almanac.soteriacovenant.org"

# Local widget build version. Compared against widgetMinVersionName from the
# membership /version endpoint to surface "upgrade available" in the dashbar
# when the server has moved past the supported floor.
WIDGET_VERSION = "2.9.1"

# How often to re-poll /api/v1/version. A widget left running for weeks
# would never see the upgrade prompt without this, since the v2 launch-time
# handshake fires once and never repeats.
REFRESH_VERSION = 86400  # 24h

# How long a transient dashbar notice (e.g. "Connection revoked") stays
# visible after being raised. Long enough that a user away-from-desk for
# an hour still sees it on return.
NOTICE_TTL = 3600  # 1h


# ---------------------------------------------------------------------------
# Lightweight i18n — scaffolding only as of v2.8.0
# ---------------------------------------------------------------------------
# Strings live in translations/{lang}.json next to this file (or in
# _internal/translations/ inside the PyInstaller bundle). en.json is the
# source-of-truth; missing keys in any other language fall back to English.
# `_t("notice.connection_revoked")` returns the translated string;
# `_t("tray.install_update", version="2.7.0")` does named-format substitution.
#
# Detection order:
#   1. settings.json "language" key (manual override; e.g. "fr")
#   2. LANG environment variable (e.g. "fr_CA.UTF-8" → "fr")
#   3. "en"

_TRANSLATIONS_CACHE: dict = {}


def _translations_dir() -> Path:
    """Find the translations dir whether running under PyInstaller or not."""
    # Bundled mode: sys._MEIPASS / translations
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "translations"
    return Path(__file__).resolve().parent / "translations"


def _detect_language() -> str:
    settings = _safe_read_json(SETTINGS_PATH, default={}) or {}
    pref = (settings.get("language") or "").strip()
    if pref:
        return pref
    env = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "")
    if env:
        # "fr_CA.UTF-8" → "fr"; ignore everything after _ or .
        return env.split(".")[0].split("_")[0].lower() or "en"
    return "en"


def _load_translations(lang: str) -> dict:
    if lang in _TRANSLATIONS_CACHE:
        return _TRANSLATIONS_CACHE[lang]
    path = _translations_dir() / f"{lang}.json"
    data = _safe_read_json(path, default={}) or {}
    _TRANSLATIONS_CACHE[lang] = data
    return data


def _t(key: str, **kwargs) -> str:
    """Translate a dotted key (e.g. "tray.show_clock") to the user's
    language; fall back to English; final fallback is the key itself.
    Named substitutions via .format(**kwargs)."""
    lang = _detect_language()
    for source in (lang, "en"):
        data = _load_translations(source)
        node = data
        for part in key.split("."):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(part)
            if node is None:
                break
        if isinstance(node, str):
            try:
                return node.format(**kwargs)
            except (KeyError, IndexError):
                return node
    return key  # truly missing — leak the key so it's debuggable


def _ver_tuple(v: str) -> tuple:
    """Parse a dotted version like '2.0.1' into a comparable tuple. Non-numeric
    parts compare as 0; good enough for x.y.z, no pre-release semantics."""
    out = []
    for part in (v or "").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)

# ---------------------------------------------------------------------------
# Soterian constants (for local segment calculation)
# ---------------------------------------------------------------------------
SEGMENTS = ["Elyth", "Syrae", "Korun", "Vaeth", "Draven", "Nareth", "Solun", "Orien"]
SEGMENT_ICONS = {
    "Orien": "\U0001F311", "Vaeth": "\U0001F304",
    "Elyth": "\U0001F305", "Syrae": "\u2600\uFE0F",
    "Korun": "\U0001F31E", "Draven": "\U0001F307",
    "Nareth": "\U0001F306", "Solun": "\U0001F30C",
}

# ---------------------------------------------------------------------------
# Brand colours
# ---------------------------------------------------------------------------
BG_COLOR = "#0d0d0d"
FG_GOLD = "#d4af37"
FG_TEXT = "#e8d8b8"
FG_DIM = "#8b7355"
FG_VDIM = "#5a4a3a"
BORDER_COLOR = "#d4af37"

# ---------------------------------------------------------------------------
# Safe I/O helpers (inlined for distribution)
# ---------------------------------------------------------------------------


def _safe_read_json(path: Path, default=None):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, IOError, json.JSONDecodeError, ValueError):
        return default


def _safe_write_json(path: Path, data, indent: int = 2) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        return True
    except (OSError, IOError):
        return False


def _safe_read_text(path: Path, default=None):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, IOError):
        return default


def _safe_write_text(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    except (OSError, IOError):
        return False


# ---------------------------------------------------------------------------
# Widget-token storage — OS keyring with settings.json fallback
# ---------------------------------------------------------------------------
# v2.1.x stored the long-lived widget bearer token in plaintext at
# ~/.config/soterian-clock/settings.json. From v2.2.0 on we use the OS keyring
# (libsecret/SecretService on Linux, Keychain on macOS, Credential Locker on
# Windows). On first launch after the upgrade, an existing legacy token in
# settings.json is migrated into the keyring and removed from disk. If the
# keyring isn't available (headless box without dbus, locked-down sandbox),
# we fall back to settings.json so the widget keeps working — the security
# improvement is best-effort, not a hard requirement.

_KEYRING_SERVICE = "soterian-clock"
_KEYRING_USER = "widget-token"


def _keyring_module():
    """Return the keyring module if importable, else None. Wrapped so we
    don't pay the import cost on platforms/installs without keyring."""
    try:
        import keyring as _kr
        return _kr
    except ImportError:
        return None


def _load_widget_token() -> str:
    """Read the widget token from the keyring; one-shot migrate from
    settings.json if found there (legacy v2.1.x layout)."""
    kr = _keyring_module()
    if kr is not None:
        try:
            tok = kr.get_password(_KEYRING_SERVICE, _KEYRING_USER)
            if tok:
                return tok
        except Exception:
            pass

    # Legacy fallback: settings.json
    settings = _safe_read_json(SETTINGS_PATH, default={}) or {}
    legacy = (settings.get("widget_token") or "").strip()
    if legacy and kr is not None:
        # Migrate to keyring + remove from settings.json
        try:
            kr.set_password(_KEYRING_SERVICE, _KEYRING_USER, legacy)
            settings.pop("widget_token", None)
            _safe_write_json(SETTINGS_PATH, settings)
        except Exception:
            pass  # Keep in settings.json if migration fails
    return legacy


def _save_widget_token(token: str) -> None:
    """Persist the widget token. Prefers keyring; falls back to
    settings.json if keyring is unavailable."""
    kr = _keyring_module()
    if kr is not None:
        try:
            kr.set_password(_KEYRING_SERVICE, _KEYRING_USER, token)
            # Defensive: scrub any stale legacy copy
            settings = _safe_read_json(SETTINGS_PATH, default={}) or {}
            if "widget_token" in settings:
                settings.pop("widget_token", None)
                _safe_write_json(SETTINGS_PATH, settings)
            return
        except Exception:
            pass
    # Fallback: settings.json
    settings = _safe_read_json(SETTINGS_PATH, default={}) or {}
    settings["widget_token"] = token
    _safe_write_json(SETTINGS_PATH, settings)


def _delete_widget_token() -> None:
    """Remove the widget token from both keyring AND settings.json — defensive
    so a stale token can't reappear after re-connecting."""
    kr = _keyring_module()
    if kr is not None:
        try:
            kr.delete_password(_KEYRING_SERVICE, _KEYRING_USER)
        except Exception:
            pass
    settings = _safe_read_json(SETTINGS_PATH, default={}) or {}
    if "widget_token" in settings:
        settings.pop("widget_token", None)
        _safe_write_json(SETTINGS_PATH, settings)


# ---------------------------------------------------------------------------
# Local segment calculation
# ---------------------------------------------------------------------------


def get_current_segment() -> tuple:
    """Calculate segment from current UTC hour. Returns (name, range_str)."""
    hour = datetime.now(timezone.utc).hour
    idx = hour // 3
    name = SEGMENTS[idx % len(SEGMENTS)]
    start = idx * 3
    end = start + 3
    return name, f"{start:02d}:00\u2013{end:02d}:00 UTC"


# ---------------------------------------------------------------------------
# Lock file management
# ---------------------------------------------------------------------------


def already_running() -> bool:
    """Check if another instance is running via lock file."""
    try:
        if LOCKFILE.exists():
            pid_str = _safe_read_text(LOCKFILE)
            if pid_str is not None:
                try:
                    pid = int(pid_str.strip())
                    os.kill(pid, 0)
                    return True
                except (ValueError, OSError):
                    pass
            try:
                LOCKFILE.unlink()
            except OSError:
                pass
        _safe_write_text(LOCKFILE, str(os.getpid()))
    except OSError:
        pass
    return False


def cleanup_lock():
    """Remove lock file on exit."""
    try:
        if LOCKFILE.exists():
            pid_str = _safe_read_text(LOCKFILE)
            if pid_str and pid_str.strip() == str(os.getpid()):
                LOCKFILE.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# System tray icon
# ---------------------------------------------------------------------------


def _create_tray_icon(clock_app):
    """Create a system tray icon with menu. Returns the pystray Icon or None."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    # Tray-icon variants per platform. The OS conventions differ:
    #   macOS: NSStatusItem strongly prefers monochrome "template" images
    #          that the OS auto-recolors for the menu-bar background
    #          (light/dark mode + selected/unselected states). A colorful
    #          icon looks out of place there.
    #   Linux: trays vary wildly (GNOME/KDE/XFCE), but a colorful icon is
    #          conventional and works in both bright and dark themes.
    #   Windows: similar to Linux — tray icons are typically colorful.
    # We draw a transparent-bg monochrome candle silhouette for macOS and
    # the existing gold-circle-S for everywhere else. Both are 64×64 RGBA.
    if _SYSTEM == "Darwin":
        # Monochrome template — opaque pixels are recolored by NSStatusItem.
        # We use solid black; macOS handles light/dark inversion.
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))  # transparent bg
        draw = ImageDraw.Draw(img)
        # Candle silhouette: vertical taper + flame teardrop on top.
        # Body: rounded rectangle ~16 px wide, 28 px tall.
        draw.rectangle([24, 22, 40, 50], fill=(0, 0, 0, 255))
        # Flame: tear drop above the body.
        draw.ellipse([26, 8, 38, 22], fill=(0, 0, 0, 255))
        # Wick: short line connecting flame to body.
        draw.rectangle([31, 18, 33, 24], fill=(0, 0, 0, 255))
    else:
        # Default: gold circle on dark with an "S" hint. Works in
        # tray backgrounds of either contrast.
        img = Image.new("RGBA", (64, 64), (13, 13, 13, 255))
        draw = ImageDraw.Draw(img)
        draw.ellipse([8, 8, 56, 56], fill=(212, 175, 55, 255), outline=(90, 74, 58, 255), width=2)
        draw.text((22, 14), "S", fill=(13, 13, 13, 255))

    def on_show(icon, item):
        clock_app.root.after(0, clock_app.show_window)

    def on_hide(icon, item):
        clock_app.root.after(0, clock_app.hide_window)

    def on_refresh(icon, item):
        clock_app.root.after(0, clock_app.refresh_now)

    def on_open_site(icon, item):
        webbrowser.open(API_BASE)

    def on_open_almanac(icon, item):
        webbrowser.open(CELEBRATIONS_BASE)

    def on_open_inbox(icon, item):
        webbrowser.open(f"{MEMBERSHIP_BASE}/dashboard")

    def on_connect(icon, item):
        clock_app.root.after(0, clock_app._show_connect_dialog)

    def on_disconnect(icon, item):
        clock_app.root.after(0, clock_app._disconnect_membership)

    def on_download_update(icon, item):
        url = clock_app.upgrade_releases_url or "https://github.com/SoteriaCovenantTrust/soterian-clock/releases"
        webbrowser.open(url)

    def on_install_update(icon, item):
        clock_app.root.after(0, clock_app.trigger_self_update)

    def on_about(icon, item):
        clock_app.root.after(0, clock_app._show_about_dialog)

    def on_quit(icon, item):
        icon.stop()
        clock_app.root.after(0, clock_app.quit_app)

    # Dynamic menu items inspect clock_app state at popup time. This is
    # cheaper than rebuilding the icon on every state change, and pystray
    # re-evaluates `text=`, `visible=`, and `checked=` callables each time
    # the menu opens.
    # "Open ..." items collapse into a submenu so the top-level tray menu
    # stays scannable. The Inbox label still carries the unread count so a
    # member doesn't need to expand the submenu to see "you have 3 unread".
    sites_submenu = pystray.Menu(
        pystray.MenuItem("\U0001F4C5  Almanac", on_open_almanac),
        pystray.MenuItem(
            lambda item: (f"\U0001F4EC  Inbox ({clock_app.inbox_unread} unread)"
                          if clock_app.inbox_unread > 0
                          else "\U0001F4EC  Inbox"),
            on_open_inbox,
            visible=lambda item: clock_app.is_connected,
        ),
        pystray.MenuItem("\U0001F551  time.soteriacovenant.org", on_open_site),
    )

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: _t("tray.show_clock"), on_show, default=True),
        pystray.MenuItem(lambda item: _t("tray.hide_clock"), on_hide),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: _t("tray.refresh_now"), on_refresh),
        pystray.MenuItem(
            lambda item: ("\U0001F30D  " + (
                _t("tray.open_sites_with_unread", count=clock_app.inbox_unread)
                if (clock_app.is_connected and clock_app.inbox_unread > 0)
                else _t("tray.open_sites")
            )),
            None,
            sites_submenu,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            lambda item: f"Connected: {clock_app.member_alias or clock_app.member_pma_id}",
            None,
            enabled=False,
            visible=lambda item: clock_app.is_connected,
        ),
        pystray.MenuItem(
            "Disconnect from Membership",
            on_disconnect,
            visible=lambda item: clock_app.is_connected,
        ),
        pystray.MenuItem(
            "Connect to Membership...",
            on_connect,
            visible=lambda item: not clock_app.is_connected,
        ),
        pystray.Menu.SEPARATOR,
        # Install-now path (Linux only). We don't try to detect platform
        # in pystray callable; instead, the action method itself returns a
        # "this platform isn't supported" notice when called on Mac/Win.
        pystray.MenuItem(
            lambda item: f"⬇  Install update (v{clock_app.upgrade_latest_version})",
            on_install_update,
            visible=lambda item: bool(clock_app.upgrade_latest_version) and _SYSTEM == "Linux",
        ),
        pystray.MenuItem(
            lambda item: f"\U0001F310  Open Releases page (v{clock_app.upgrade_latest_version})",
            on_download_update,
            visible=lambda item: bool(clock_app.upgrade_latest_version),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: _t("tray.about", version=WIDGET_VERSION), on_about),
        pystray.MenuItem(lambda item: _t("tray.quit"), on_quit),
    )

    icon = pystray.Icon("soterian-clock", img, "Petrachora Soteria Clock", menu)
    return icon


# ---------------------------------------------------------------------------
# Main widget class
# ---------------------------------------------------------------------------


class SoterianClock:
    """Cross-platform Soterian floating clock widget."""

    def __init__(self, root, start_hidden=False):
        self.root = root
        self.root.title("Petrachora Soteria")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG_COLOR)

        # HiDPI awareness — must run before any widget is built so font
        # sizes (specified in points) get the right point-to-pixel ratio.
        # Tk defaults to ~96 DPI; on a HiDPI display under Wayland with
        # mutter's xwayland-native-scaling enabled (GNOME default for some
        # configs as of 2026), the X11 server reports the true native
        # resolution and Tk renders the dashbar roughly half-size unless
        # we apply the scale ourselves. We honour an explicit override in
        # settings.json ("ui_scale") for users who want to force a value;
        # otherwise we infer from winfo_fpixels and only scale up if the
        # screen is meaningfully HiDPI (>120 DPI).
        try:
            override = float(_safe_read_json(SETTINGS_PATH, default={}).get("ui_scale") or 0)
            if override > 0:
                self.root.tk.call('tk', 'scaling', override)
            else:
                ppi = self.root.winfo_fpixels('1i')  # pixels per inch
                if ppi > 120:
                    self.root.tk.call('tk', 'scaling', ppi / 72.0)
        except Exception as e:
            print(f"[soterian-clock] HiDPI scaling probe failed: {e!r}",
                  file=sys.stderr, flush=True)

        # Thin dashbar strip — auto-width, fixed height
        self.root.resizable(False, False)

        # State
        self.soterian_date = ""
        self.trust_day = ""
        self.trust_year = ""
        self.week = ""
        self.segment = ""
        self.segment_range = ""
        self.sun_sign = ""
        self.moon_phase = ""
        self.is_online = False
        self.is_rich = False  # Whether we have /api/now data
        self._hidden = start_hidden
        self._tray_icon = None
        self._lock = threading.Lock()

        # Load user settings (timezone, membership)
        self._settings = _safe_read_json(SETTINGS_PATH, default={})
        self.user_timezone = self._settings.get("timezone", "System Default")

        # Membership sync state
        self.widget_token = _load_widget_token()
        self.member_alias = self._settings.get("member_alias", "")
        self.member_pma_id = self._settings.get("member_pma_id", "")
        self.member_tier = self._settings.get("member_tier", None)
        self.is_connected = bool(self.widget_token)
        # Inbox unread counts — populated by /widget/sync. Renders as
        # "📬 N" (or "📬 N ❗ K" when urgent > 0) in the dashbar so the
        # always-visible widget doubles as an inbox badge for connected
        # members. Cleared on disconnect.
        self.inbox_unread = 0
        self.inbox_urgent = 0
        # Celestial-event alerts: opt-out via settings.json
        # ("celestial_alerts": false). Tracks fingerprints of already-shown
        # alerts so we don't re-notify on every 4h poll for the same event;
        # bounded to 200 entries to keep settings.json from ballooning.
        self.celestial_alerts_enabled = bool(self._settings.get("celestial_alerts", True))
        self._seen_alerts = set(self._settings.get("seen_alerts", []) or [])
        # Tracks whether the most recent /widget/sync succeeded. Distinct from
        # is_online (calendar API health) so the status dot can surface
        # partial failure: green = both healthy, amber = calendar OK but
        # member sync stale, red = calendar down. Defaults to True so a
        # not-yet-connected widget doesn't render amber on launch.
        self.is_member_online = True
        self.upgrade_available = False
        self.upgrade_min_version = ""    # widgetMinVersionName (floor)
        self.upgrade_latest_version = "" # widgetVersionName (target for auto-update)
        # Populated by the version handshake when the server provides
        # widgetReleasesUrl. Lets the tray + context menu show a "Download
        # Update" item that points at the real GitHub Releases page.
        self.upgrade_releases_url = ""
        # Transient notice surfaced in the dashbar (e.g. token-revoked).
        # Cleared automatically after NOTICE_TTL. Stored as a UTC datetime
        # for unambiguous comparison.
        self.notice_text = ""
        self.notice_until = None

        # Post-update success notice — compare WIDGET_VERSION to the version
        # we ran with last time. If we upgraded since then (auto-update via
        # tray, manual install of a new tarball, anything), raise a transient
        # confirmation so the user knows the upgrade landed. The systemctl
        # restart that auto-update triggers kills the in-flight "✓ Updated"
        # notice from _do_self_update_thread, so this is the canonical signal.
        last_seen = (self._settings.get("last_known_version") or "").strip()
        self._upgraded_from_version = ""  # for About dialog "What's new"
        if last_seen and last_seen != WIDGET_VERSION:
            try:
                if _ver_tuple(WIDGET_VERSION) > _ver_tuple(last_seen):
                    self._upgraded_from_version = last_seen
                    self.notice_text = _t("notice.updated_from", new=WIDGET_VERSION)
                    self.notice_until = datetime.now(timezone.utc) + timedelta(seconds=NOTICE_TTL)
            except Exception:
                pass
        elif not last_seen and not bool(self.widget_token):
            # First-time install hint — give a one-time nudge so a fresh
            # member knows the right-click menu exists. Only when not yet
            # connected (a connected widget already has the alias taking
            # up that slot). Won't re-fire after restart because
            # last_known_version is now set on this same startup.
            self.notice_text = _t("notice.first_run_hint")
            self.notice_until = datetime.now(timezone.utc) + timedelta(seconds=NOTICE_TTL)
        # Persist so the next startup can do the same comparison.
        if last_seen != WIDGET_VERSION:
            self._settings["last_known_version"] = WIDGET_VERSION
            _safe_write_json(SETTINGS_PATH, self._settings)

        # Build UI
        self._build_ui()
        self._load_position()

        if start_hidden:
            self.root.withdraw()

        # Signal handling for clean shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        # Start background fetch threads
        self._schedule_fast_fetch()
        self._schedule_rich_fetch()
        self._schedule_member_sync()

        # Periodic version handshake against Membership /api/v1/version.
        # If the server says we're below widgetMinVersionName, the dashbar
        # surfaces an "Update available" indicator and the tray menu offers
        # a "Download Update" item pointing at widgetReleasesUrl.
        self._schedule_widget_version_check()

        # Celestial-event alerts (4h cadence). Opt-out via settings.json.
        self._schedule_celestial_alerts()

        # Suspend/resume listener (Linux). Subscribes to the systemd-logind
        # PrepareForSleep dbus signal so the widget refreshes immediately on
        # wake-from-sleep instead of waiting up to 60s for the next tick.
        # No-op on macOS / Windows — both have native equivalents that we
        # haven't wired up yet.
        if _SYSTEM == "Linux":
            self._start_suspend_resume_listener()

        # Start system tray
        self._start_tray()

    def _start_suspend_resume_listener(self):
        """Listen for `org.freedesktop.login1.Manager.PrepareForSleep` on
        the system bus. The signal value is True before sleep and False
        after resume — we trigger refresh_now on the resume edge so the
        dashbar catches up immediately instead of showing stale time
        until the next 60s poll. Runs in a background thread; failures
        are silent (jeepney isn't available, no system bus, etc.)."""
        def _listen():
            try:
                from jeepney import DBusAddress, MatchRule, message_bus
                from jeepney.io.blocking import open_dbus_connection
            except ImportError:
                return  # jeepney not bundled — skip cleanly
            try:
                connection = open_dbus_connection(bus="SYSTEM")
                rule = MatchRule(
                    type="signal",
                    sender="org.freedesktop.login1",
                    interface="org.freedesktop.login1.Manager",
                    member="PrepareForSleep",
                )
                connection.send_and_get_reply(message_bus.AddMatch(rule))
                with connection.filter(rule) as queue:
                    while True:
                        msg = queue.get(timeout=None)
                        # Body is (b,) — True=going to sleep, False=just woke
                        if msg.body and len(msg.body) >= 1 and msg.body[0] is False:
                            self.root.after(0, self.refresh_now)
            except Exception as e:
                print(f"[soterian-clock] suspend/resume listener stopped: {e!r}",
                      file=sys.stderr, flush=True)

        threading.Thread(target=_listen, daemon=True).start()

    def _build_ui(self):
        """Build the thin dashbar strip with gold accent."""
        # Outer frame — thin gold line on bottom edge
        self.border_frame = tk.Frame(self.root, bg=BG_COLOR)
        self.border_frame.pack(fill="both", expand=True)

        # Gold accent line (1px bottom border)
        self.accent_line = tk.Frame(self.border_frame, bg=BORDER_COLOR, height=1)
        self.accent_line.pack(side="bottom", fill="x")

        # Inner frame — single horizontal row
        self.inner_frame = tk.Frame(self.border_frame, bg=BG_COLOR, padx=8, pady=3)
        self.inner_frame.pack(fill="both", expand=True)

        # Status dot (left edge)
        self.status_dot = tk.Label(
            self.inner_frame, text="\u25CF", font=("Arial", 7),
            fg="#555555", bg=BG_COLOR,
        )
        self.status_dot.pack(side="left", padx=(0, 6))

        # Main content label — single line with all info
        self.main_label = tk.Label(
            self.inner_frame, text="Loading\u2026",
            font=("Georgia", 10), fg=FG_TEXT, bg=BG_COLOR,
            anchor="w",
        )
        self.main_label.pack(side="left", fill="x", expand=True)

        # Bind interactions to all widgets
        for widget in [self.root, self.border_frame, self.accent_line,
                       self.inner_frame, self.main_label, self.status_dot]:
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<B1-Motion>", self._do_move)
            widget.bind("<Double-Button-1>", self._open_site)
            widget.bind("<Button-3>", self._show_context_menu)

        # Context menu
        self.context_menu = tk.Menu(self.root, tearoff=0,
                                    bg=BG_COLOR, fg=FG_TEXT,
                                    activebackground=FG_GOLD,
                                    activeforeground=BG_COLOR)
        self.context_menu.add_command(label="\u21BB  Refresh Now", command=self.refresh_now)
        self.context_menu.add_command(label="\u2398  Copy Date", command=self._copy_date)
        self.context_menu.add_separator()

        # Timezone submenu
        self.tz_menu = tk.Menu(self.context_menu, tearoff=0,
                               bg=BG_COLOR, fg=FG_TEXT,
                               activebackground=FG_GOLD,
                               activeforeground=BG_COLOR)
        for tz in TIMEZONE_CHOICES:
            self.tz_menu.add_command(
                label=f"{'  \u2713 ' if tz == self.user_timezone else '     '}{tz}",
                command=lambda t=tz: self._set_timezone(t),
            )
        self.context_menu.add_cascade(label="\U0001F30D  Timezone", menu=self.tz_menu)

        self.context_menu.add_separator()

        # Membership connect/disconnect
        if self.is_connected:
            self.context_menu.add_command(
                label=f"\U0001F464  Connected: {self.member_alias or self.member_pma_id}",
                state="disabled",
            )
            self.context_menu.add_command(
                label="\u26D4  Disconnect from Membership",
                command=self._disconnect_membership,
            )
        else:
            self.context_menu.add_command(
                label="\U0001F517  Connect to Membership",
                command=self._show_connect_dialog,
            )

        self.context_menu.add_separator()
        self.context_menu.add_command(label="\U0001F4C5  Open Almanac", command=lambda: webbrowser.open(CELEBRATIONS_BASE))
        if self.is_connected:
            inbox_label = (f"\U0001F4EC  Open Inbox ({self.inbox_unread} unread)"
                           if self.inbox_unread > 0
                           else "\U0001F4EC  Open Inbox")
            self.context_menu.add_command(label=inbox_label,
                                          command=lambda: webbrowser.open(f"{MEMBERSHIP_BASE}/dashboard"))
        self.context_menu.add_command(label="\U0001F310  Open time.soteriacovenant.org", command=lambda: webbrowser.open(API_BASE))
        if self.upgrade_available:
            self.context_menu.add_separator()
            self.context_menu.add_command(
                label=f"\u2b07  Download update (v{self.upgrade_min_version}+)",
                command=lambda: webbrowser.open(self.upgrade_releases_url
                                                or "https://github.com/SoteriaCovenantTrust/soterian-clock/releases"),
            )
        self.context_menu.add_separator()
        self.context_menu.add_command(label="\u2715  Quit", command=self.quit_app)

    # -------------------------------------------------------------------
    # Data fetching (background threads)
    # -------------------------------------------------------------------

    def _fetch_date_data(self):
        """Fetch from /api/date (fast, ~10ms). Runs in background thread."""
        try:
            r = requests.get(API_DATE, timeout=5)
            r.raise_for_status()
            data = r.json()
            seg_name, seg_range = get_current_segment()
            with self._lock:
                self.soterian_date = data.get("soterian_date", "")
                self.trust_day = str(data.get("trust_day", ""))
                self.trust_year = str(data.get("trust_year", ""))
                self.segment = seg_name
                self.segment_range = seg_range
                self.is_online = True
            # Cache
            _safe_write_json(CACHE_PATH, {
                "soterian_date": self.soterian_date,
                "trust_day": self.trust_day,
                "trust_year": self.trust_year,
                "segment": seg_name,
                "segment_range": seg_range,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            })
        except (requests.RequestException, ValueError, KeyError):
            # Try cache
            cached = _safe_read_json(CACHE_PATH, default={})
            if cached and isinstance(cached, dict):
                with self._lock:
                    self.soterian_date = cached.get("soterian_date", self.soterian_date)
                    self.trust_day = cached.get("trust_day", self.trust_day)
                    self.trust_year = cached.get("trust_year", self.trust_year)
                    seg_name, seg_range = get_current_segment()
                    self.segment = seg_name
                    self.segment_range = seg_range
                    self.is_online = False
        # Update UI on main thread
        self.root.after(0, self._update_display)

    def _fetch_rich_data(self):
        """Fetch from /api/now (slow, ~4s). Runs in background thread."""
        try:
            r = requests.get(API_NOW, timeout=10)
            r.raise_for_status()
            data = r.json()
            raw = data.get("raw", {})
            with self._lock:
                self.week = str(data.get("week", ""))
                self.sun_sign = raw.get("sun_sign", "")
                self.moon_phase = raw.get("moon_phase", "")
                self.is_rich = True
        except (requests.RequestException, ValueError, KeyError):
            pass  # Rich data is optional; fast data is sufficient
        self.root.after(0, self._update_display)

    def _schedule_fast_fetch(self):
        """Schedule the fast /api/date fetch in a background thread."""
        thread = threading.Thread(target=self._fetch_date_data, daemon=True)
        thread.start()
        self.root.after(REFRESH_FAST * 1000, self._schedule_fast_fetch)

    def _schedule_rich_fetch(self):
        """Schedule the rich /api/now fetch in a background thread."""
        thread = threading.Thread(target=self._fetch_rich_data, daemon=True)
        thread.start()
        self.root.after(REFRESH_RICH * 1000, self._schedule_rich_fetch)

    def _fetch_member_sync(self):
        """Sync member data from Membership API. Runs in background thread.

        Sets is_member_online so the status dot can distinguish between
        calendar-API failure (red) and member-sync failure (amber). On
        token-revoked (401) we clear the connection entirely AND raise a
        transient dashbar notice so the user knows their connection just
        broke (otherwise the alias would silently disappear); on transient
        network failure we keep the connection but mark the sync stale."""
        if not self.widget_token:
            return
        try:
            r = requests.get(
                MEMBERSHIP_WIDGET_SYNC,
                headers={"X-Widget-Token": self.widget_token},
                timeout=10,
            )
            if r.status_code == 401:
                with self._lock:
                    self.is_connected = False
                    self.is_member_online = False
                    self.widget_token = ""
                    self.notice_text = _t("notice.connection_revoked")
                    self.notice_until = datetime.now(timezone.utc) + timedelta(seconds=NOTICE_TTL)
                _delete_widget_token()
                # Rebuild context menu so it shows "Connect" not "Disconnect"
                self.root.after(0, self._rebuild_context_menu)
                self.root.after(0, self._update_display)
                return

            r.raise_for_status()
            data = r.json().get("data", {})
            with self._lock:
                self.member_alias = data.get("alias", self.member_alias)
                self.member_pma_id = data.get("pma_id", self.member_pma_id)
                self.member_tier = data.get("tier", self.member_tier)
                self.is_connected = True
                self.is_member_online = True
                # Inbox badge — server v2.34.0+ adds these fields; older
                # servers omit them, in which case .get returns 0 and the
                # badge stays hidden.
                self.inbox_unread = int(data.get("inbox_unread", 0) or 0)
                self.inbox_urgent = int(data.get("inbox_urgent", 0) or 0)
                # Sync timezone from membership if set
                server_tz = data.get("timezone")
                if server_tz and server_tz != self.user_timezone:
                    self.user_timezone = server_tz
                    self._settings["timezone"] = server_tz
                    _safe_write_json(SETTINGS_PATH, self._settings)
        except (requests.RequestException, ValueError, KeyError):
            with self._lock:
                self.is_member_online = False
        self.root.after(0, self._update_display)

    def _schedule_member_sync(self):
        """Schedule periodic member data sync."""
        if self.widget_token:
            thread = threading.Thread(target=self._fetch_member_sync, daemon=True)
            thread.start()
        self.root.after(REFRESH_SYNC * 1000, self._schedule_member_sync)

    def _fetch_widget_version(self):
        """Version handshake. Asks Membership /api/v1/version for the minimum
        supported widget version; if we're below it, sets upgrade_available so
        the dashbar can surface the warning and the tray menu can offer a
        "Download Update" item pointing at widgetReleasesUrl. Failures are
        silent — version drift is non-urgent and a missing handshake
        shouldn't disrupt the running widget."""
        try:
            r = requests.get(MEMBERSHIP_VERSION, timeout=10)
            r.raise_for_status()
            data = r.json().get("data", {})
            min_v = (data.get("widgetMinVersionName") or "").strip()
            latest_v = (data.get("widgetVersionName") or "").strip()
            releases_url = (data.get("widgetReleasesUrl") or "").strip()
            # Trigger upgrade indicator if WE are below the floor OR below the latest.
            # min_v gates the "you must upgrade" warning; latest_v drives the auto-
            # update target so we install the newest available, not just the floor.
            if min_v and _ver_tuple(min_v) > _ver_tuple(WIDGET_VERSION):
                with self._lock:
                    self.upgrade_available = True
                    self.upgrade_min_version = min_v
                    self.upgrade_latest_version = latest_v or min_v
                    self.upgrade_releases_url = releases_url
                self.root.after(0, self._rebuild_context_menu)
                self.root.after(0, self._update_display)
            elif latest_v and _ver_tuple(latest_v) > _ver_tuple(WIDGET_VERSION):
                # Above the floor but a newer build is out — softer signal:
                # the dashbar's "⚠ Update" warning is reserved for hard upgrades
                # (below floor); newer-but-optional just enables the tray's
                # "Install update now" item without nagging in the dashbar.
                with self._lock:
                    self.upgrade_latest_version = latest_v
                    self.upgrade_releases_url = releases_url
                self.root.after(0, self._rebuild_context_menu)
        except Exception:
            pass

    def _schedule_widget_version_check(self):
        """Run the version handshake immediately, then once per REFRESH_VERSION
        seconds (24h). Without this a long-running widget would never see an
        upgrade prompt — v2.0.x checked once at startup and never again."""
        threading.Thread(target=self._fetch_widget_version, daemon=True).start()
        self.root.after(REFRESH_VERSION * 1000, self._schedule_widget_version_check)

    def _fetch_celestial_alerts(self):
        """Poll /api/v1/mobile/snapshot for upcoming celestial events. Filter
        to high-priority + days_until ≤ 1 (today/tomorrow). Surface NEW ones
        (fingerprint not in _seen_alerts) as a transient dashbar notice; track
        the fingerprint so we don't re-fire on the next 4h poll for the same
        event. Opt-out via settings.json `celestial_alerts: false`."""
        if not self.celestial_alerts_enabled:
            return
        try:
            r = requests.get(API_MOBILE_SNAPSHOT, timeout=15)
            r.raise_for_status()
            alerts = r.json().get("alerts") or []
            new_for_user = []
            for a in alerts:
                if (a.get("priority") or "") != "high":
                    continue
                try:
                    days = int(a.get("days_until", 999))
                except (ValueError, TypeError):
                    continue
                if days > 1:
                    continue
                fp = f"{a.get('type', '')}|{a.get('body', '')}|{a.get('summary', '')}"
                if fp in self._seen_alerts:
                    continue
                new_for_user.append((fp, a))

            if new_for_user:
                with self._lock:
                    # Surface the FIRST new alert (snapshot returns roughly
                    # chronological — earliest event first). Mark all as seen
                    # so we don't queue notifications.
                    a = new_for_user[0][1]
                    self.notice_text = f"✨ {a.get('summary', '')}"
                    self.notice_until = datetime.now(timezone.utc) + timedelta(seconds=NOTICE_TTL)
                    for fp, _ in new_for_user:
                        self._seen_alerts.add(fp)
                    # Persist the seen-set, bounded to last 200 fingerprints
                    # so settings.json stays small.
                    seen_list = list(self._seen_alerts)
                    if len(seen_list) > 200:
                        seen_list = seen_list[-200:]
                        self._seen_alerts = set(seen_list)
                    self._settings["seen_alerts"] = seen_list
                    _safe_write_json(SETTINGS_PATH, self._settings)
                self.root.after(0, self._update_display)
        except Exception:
            pass

    def _schedule_celestial_alerts(self):
        """Run the celestial-alerts check immediately, then every
        REFRESH_ALERTS seconds (4h). Cheap because the snapshot endpoint
        returns a static-ish daily forecast."""
        if self.celestial_alerts_enabled:
            threading.Thread(target=self._fetch_celestial_alerts, daemon=True).start()
        self.root.after(REFRESH_ALERTS * 1000, self._schedule_celestial_alerts)

    # -------------------------------------------------------------------
    # Self-update from the public Releases page
    # -------------------------------------------------------------------

    def trigger_self_update(self):
        """User-initiated update. Runs the download/extract/swap/restart
        flow on a background thread so the UI doesn't freeze; status is
        surfaced via the dashbar notice mechanism."""
        if not self.upgrade_latest_version:
            return
        threading.Thread(target=self._do_self_update_thread, daemon=True).start()
        with self._lock:
            self.notice_text = _t("notice.downloading_update", version=self.upgrade_latest_version)
            self.notice_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.root.after(0, self._update_display)

    def _do_self_update_thread(self):
        ok, msg = self._do_self_update(self.upgrade_latest_version)
        with self._lock:
            self.notice_text = ("✓ " if ok else "⚠ ") + msg
            self.notice_until = datetime.now(timezone.utc) + timedelta(seconds=NOTICE_TTL)
        self.root.after(0, self._update_display)

    def _verify_tarball_sha256(self, tarball_path: Path, tarball_name: str, sums_url: str):
        """Fetch the release's SHA256SUMS, look up the line for tarball_name,
        and verify the local file's hash matches. Returns (ok, message).

        If SHA256SUMS isn't published on the release (older releases predating
        the CI step that started generating it), we skip with a log line and
        return True — degrading gracefully so legacy releases stay
        installable. The release-side enforcement is the new floor; older
        releases just don't get the extra check.
        """
        try:
            r = requests.get(sums_url, timeout=30)
            if r.status_code == 404:
                print(f"[soterian-clock] SHA256SUMS not published on this release "
                      f"({sums_url}); proceeding without integrity check.",
                      file=sys.stderr, flush=True)
                return (True, "")
            r.raise_for_status()
        except requests.RequestException as e:
            return (False, f"Couldn't fetch SHA256SUMS: {e}")

        # Parse: each non-empty line is "<64-hex>  <filename>".
        # Look up our specific tarball.
        expected = None
        for line in r.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            h, name = parts[0].lower(), parts[1].lstrip("*").strip()
            if name == tarball_name:
                expected = h
                break

        if expected is None:
            return (False, f"SHA256SUMS missing entry for {tarball_name}")

        h = hashlib.sha256()
        with open(tarball_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        actual = h.hexdigest().lower()

        if actual != expected:
            return (False,
                    f"SHA256 mismatch — tarball does not match "
                    f"published hash. Expected {expected[:16]}..., "
                    f"got {actual[:16]}... (aborting)")
        return (True, "")

    def _do_self_update(self, target_version: str):
        """Linux-only: download the matching release tarball from the public
        repo, extract, atomically swap the install dir, restart the systemd
        user unit. Returns (ok: bool, message: str). The widget's running
        process IS replaced by the systemctl restart, so this method
        effectively never returns success — but if the swap fails before the
        restart we surface the error.

        Trust model: HTTPS to github.com (verifies the cert, which is good
        enough for non-sensitive software). A SHA256-manifest-and-verify
        step would tighten this; deferred to a future release."""
        if _SYSTEM != "Linux":
            return (False,
                    f"Auto-install is Linux-only on this build. Open the Releases page to "
                    f"download v{target_version} for {_SYSTEM}.")
        arch = platform.machine()
        if arch != "x86_64":
            return (False,
                    f"No matching x86_64 build for {arch}. Open the Releases page.")

        tarball_name = f"soterian-clock-{target_version}-linux-x86_64.tar.gz"
        release_base = (f"https://github.com/SoteriaCovenantTrust/soterian-clock/"
                        f"releases/download/v{target_version}")
        url = f"{release_base}/{tarball_name}"
        sums_url = f"{release_base}/SHA256SUMS"

        tarball_path = None
        staging = None
        try:
            tarball_path = Path(tempfile.mkstemp(suffix=".tar.gz",
                                                 prefix="soterian-clock-update-")[1])
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(tarball_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)

            # Verify SHA256 against the release's published SHA256SUMS before
            # extracting. Closes the gap where a corrupted download or a
            # MITM that beat HTTPS would have been silently extracted. v2.5.0
            # ran on bare HTTPS-trust; v2.5.1+ adds this check. If the
            # SHA256SUMS file isn't on the release (older releases predating
            # the CI step) we skip with a logged note rather than failing —
            # so the auto-update path keeps working for legacy versions.
            ok, msg = self._verify_tarball_sha256(tarball_path, tarball_name, sums_url)
            if not ok:
                return (False, msg)

            staging = Path(tempfile.mkdtemp(prefix="soterian-clock-extract-"))
            with tarfile.open(tarball_path) as tf:
                tf.extractall(staging)

            # Tarball lays out as soterian-clock/{soterian-clock binary, _internal/, ...}
            new_dir = staging / "soterian-clock"
            if not (new_dir / "soterian-clock").exists():
                return (False, "Tarball missing expected binary; aborting.")

            install_dir = Path.home() / ".local" / "share" / "soterian-clock"
            bak_dir = install_dir.with_suffix(".bak")

            # Defensive: clear any prior .bak so the rename succeeds.
            if bak_dir.exists():
                shutil.rmtree(bak_dir, ignore_errors=True)
            if install_dir.exists():
                install_dir.rename(bak_dir)
            new_dir.rename(install_dir)

            # Ensure the binary is executable post-extraction (PyInstaller
            # bundles preserve it, but be defensive).
            (install_dir / "soterian-clock").chmod(0o755)

            # Restart the systemd user unit. systemctl --user only works
            # when running under a user manager; if the widget was started
            # some other way, restart fails benignly and the user can
            # restart manually. Fire-and-forget — our own process is about
            # to be replaced.
            subprocess.Popen(
                ["systemctl", "--user", "restart", "soterian-clock.service"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return (True, f"Updated to v{target_version}; restarting...")

        except requests.HTTPError as e:
            return (False, f"Download failed: {e.response.status_code} from {url}")
        except requests.RequestException as e:
            return (False, f"Network error during update: {e}")
        except (tarfile.TarError, OSError) as e:
            return (False, f"Extraction/install failed: {e}")
        except Exception as e:
            return (False, f"Update failed: {e!r}")
        finally:
            if tarball_path and tarball_path.exists():
                try:
                    tarball_path.unlink()
                except OSError:
                    pass
            if staging and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def refresh_now(self):
        """Force an immediate refresh of all endpoints."""
        threading.Thread(target=self._fetch_date_data, daemon=True).start()
        threading.Thread(target=self._fetch_rich_data, daemon=True).start()
        if self.widget_token:
            threading.Thread(target=self._fetch_member_sync, daemon=True).start()

    # -------------------------------------------------------------------
    # Display update
    # -------------------------------------------------------------------

    def _update_display(self):
        """Update the single-line dashbar strip. Must run on main thread.

        Wrapped in try/except so a single bad render (Tk error, malformed
        state, surprise exception) cannot kill the after() callback chain
        and leave the widget frozen — the historical silent-death mode
        we hit pre-v2.
        """
        try:
            self._render_display()
        except Exception as e:
            print(f"[soterian-clock] _update_display error: {e!r}",
                  file=sys.stderr, flush=True)

    def _render_display(self):
        with self._lock:
            sections = []

            # Transient notice (e.g. token-revoked). Rendered first so it's
            # the most prominent thing in the dashbar; auto-clears after
            # NOTICE_TTL via the time check below.
            if self.notice_text and self.notice_until:
                if datetime.now(timezone.utc) < self.notice_until:
                    sections.append(self.notice_text)
                else:
                    self.notice_text = ""
                    self.notice_until = None

            # Gregorian date/time in user's chosen timezone
            if self.user_timezone and self.user_timezone != "System Default":
                try:
                    greg_now = datetime.now(ZoneInfo(self.user_timezone))
                except Exception:
                    greg_now = datetime.now()
            else:
                greg_now = datetime.now()
            tz_abbrev = greg_now.strftime("%Z") if self.user_timezone != "System Default" else ""
            greg_str = greg_now.strftime("%a %b %-d, %Y  %H:%M")
            if tz_abbrev:
                greg_str += f" {tz_abbrev}"
            sections.append(greg_str)

            # Brand name
            sections.append("\U0001F56F\uFE0F PETRACHORA SOTERIA")

            # Soterian date
            if self.soterian_date:
                sections.append(self.soterian_date)
            else:
                sections.append("Loading\u2026")

            # Trust day / week
            detail_parts = []
            if self.trust_day:
                detail_parts.append(f"Day {self.trust_day}")
            if self.week:
                detail_parts.append(f"Wk {self.week}")
            if detail_parts:
                sections.append(" \u00B7 ".join(detail_parts))

            # Segment
            if self.segment:
                seg_icon = SEGMENT_ICONS.get(self.segment, "")
                sections.append(f"{seg_icon} {self.segment}")

            # Celestial (from rich data)
            celestial_parts = []
            if self.sun_sign:
                celestial_parts.append(f"\u2600\uFE0F {self.sun_sign}")
            if self.moon_phase:
                celestial_parts.append(f"\u263E {self.moon_phase}")
            if celestial_parts:
                sections.append(" \u00B7 ".join(celestial_parts))

            # Member alias + tier name (when connected). Tier as the human
            # name ("Steward") rather than "T4" — costs a few chars but is
            # actually informative; previously a member would see "T4" and
            # wonder what tier 4 means. Falls back to bare "Tn" if the
            # translation is missing.
            if self.is_connected and self.member_alias:
                tier_str = ""
                if self.member_tier is not None:
                    try:
                        n = int(self.member_tier)
                        name = _t(f"tier.{n}")
                        # _t returns the dotted key on miss; detect that as fallback
                        tier_str = f" · {name}" if name and not name.startswith("tier.") else f" T{n}"
                    except (TypeError, ValueError):
                        tier_str = ""
                sections.append(f"\U0001F464 {self.member_alias}{tier_str}")

            # Inbox badge — only when connected and there's actually unread.
            # Urgent count is appended with ❗ when > 0.
            if self.is_connected and self.inbox_unread > 0:
                badge = f"\U0001F4EC {self.inbox_unread}"
                if self.inbox_urgent > 0:
                    badge += f" ❗ {self.inbox_urgent}"
                sections.append(badge)

            # Soft "upgrade available" indicator (server pushed widgetMinVersionName
            # forward past our local WIDGET_VERSION)
            if self.upgrade_available:
                sections.append(f"\u26A0 Update v{self.upgrade_min_version}+")

            self.main_label.config(text="  \u2502  ".join(sections))

            # Status dot:
            #   green  \u2014 calendar API healthy and (not connected OR member sync healthy)
            #   amber  \u2014 calendar OK but member sync stale (only meaningful when connected)
            #   red    \u2014 calendar API down
            if not self.is_online:
                self.status_dot.config(fg="#bb4444", text="\u25CF")
            elif self.is_connected and not self.is_member_online:
                self.status_dot.config(fg="#bb8844", text="\u25CF")
            else:
                self.status_dot.config(fg="#44bb44", text="\u25CF")

    # -------------------------------------------------------------------
    # Window interactions
    # -------------------------------------------------------------------

    def _start_move(self, event):
        self._drag_start_x = event.x_root
        self._drag_start_y = event.y_root
        self._win_x = self.root.winfo_x()
        self._win_y = self.root.winfo_y()

    def _do_move(self, event):
        dx = event.x_root - self._drag_start_x
        dy = event.y_root - self._drag_start_y
        new_x = self._win_x + dx
        new_y = self._win_y + dy
        self.root.geometry(f"+{new_x}+{new_y}")
        self._save_position(new_x, new_y)

    def _save_position(self, x, y):
        _safe_write_json(CONFIG_PATH, {"x": x, "y": y})

    def _load_position(self):
        pos = _safe_read_json(CONFIG_PATH, default={})
        if isinstance(pos, dict) and isinstance(pos.get("x"), (int, float)) and isinstance(pos.get("y"), (int, float)):
            self.root.geometry(f"+{int(pos['x'])}+{int(pos['y'])}")
        else:
            # Default: centered horizontally, at the top of the screen
            self.root.update_idletasks()
            screen_w = self.root.winfo_screenwidth()
            widget_w = self.root.winfo_reqwidth()
            x = (screen_w - widget_w) // 2
            self.root.geometry(f"+{x}+0")
            self._save_position(x, 0)

    def _open_site(self, event=None):
        webbrowser.open(API_BASE)

    def _show_context_menu(self, event):
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def _set_timezone(self, tz_name):
        """Set the user's timezone, persist it, and sync to Membership if connected."""
        self.user_timezone = tz_name
        self._settings["timezone"] = tz_name
        _safe_write_json(SETTINGS_PATH, self._settings)
        # Rebuild tz menu checkmarks
        self.tz_menu.delete(0, "end")
        for tz in TIMEZONE_CHOICES:
            self.tz_menu.add_command(
                label=f"{'  \u2713 ' if tz == self.user_timezone else '     '}{tz}",
                command=lambda t=tz: self._set_timezone(t),
            )
        self._update_display()
        # Push to Membership in background
        if self.widget_token and tz_name != "System Default":
            def _push_tz():
                try:
                    requests.put(
                        MEMBERSHIP_WIDGET_TIMEZONE,
                        headers={"X-Widget-Token": self.widget_token},
                        json={"timezone": tz_name},
                        timeout=5,
                    )
                except requests.RequestException:
                    pass
            threading.Thread(target=_push_tz, daemon=True).start()

    def _copy_date(self):
        """Copy the current Soterian date to clipboard."""
        if self.soterian_date:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.soterian_date)

    # -------------------------------------------------------------------
    # Membership connect/disconnect
    # -------------------------------------------------------------------

    def _show_connect_dialog(self):
        """Show a dialog for email + password login to Membership."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Connect to Membership")
        dialog.configure(bg=BG_COLOR)

        # Let tkinter auto-size from content, no fixed geometry
        dialog.resizable(True, True)
        dialog.attributes("-topmost", True)
        dialog.transient(self.root)
        dialog.grab_set()

        # Let content determine size, then center
        dialog.update_idletasks()

        tk.Label(dialog, text="Connect to Soteria Membership",
                 font=("Georgia", 14, "bold"), fg=FG_GOLD, bg=BG_COLOR
                 ).pack(pady=(20, 5))
        tk.Label(dialog, text="Sign in with your membership credentials",
                 font=("Georgia", 10), fg=FG_DIM, bg=BG_COLOR
                 ).pack(pady=(0, 15))

        # Email
        tk.Label(dialog, text="Email:", font=("Georgia", 11),
                 fg=FG_TEXT, bg=BG_COLOR, anchor="w").pack(fill="x", padx=40)
        email_entry = tk.Entry(dialog, font=("Georgia", 11), width=35)
        email_entry.pack(padx=40, pady=(2, 10))

        # Password
        tk.Label(dialog, text="Password:", font=("Georgia", 11),
                 fg=FG_TEXT, bg=BG_COLOR, anchor="w").pack(fill="x", padx=40)
        pass_entry = tk.Entry(dialog, font=("Georgia", 11), width=35, show="\u2022")
        pass_entry.pack(padx=40, pady=(2, 10))

        # Status label
        status_label = tk.Label(dialog, text="", font=("Georgia", 10),
                                fg="#bb4444", bg=BG_COLOR)
        status_label.pack(pady=(0, 8))

        def do_connect(event=None):
            email = email_entry.get().strip()
            password = pass_entry.get()
            if not email or not password:
                status_label.config(text="Both fields are required", fg="#bb4444")
                return
            status_label.config(text="Connecting\u2026", fg=FG_DIM)
            dialog.update()

            # Run in background thread
            def _connect():
                try:
                    r = requests.post(MEMBERSHIP_WIDGET_CONNECT, json={
                        "email": email,
                        "password": password,
                        "device_name": f"Desktop - {_SYSTEM}",
                    }, timeout=10)
                    data = r.json()
                    if r.status_code != 200 or data.get("status") != "success":
                        err = data.get("error", {}).get("message", "Connection failed")
                        dialog.after(0, lambda: status_label.config(text=err, fg="#bb4444"))
                        return

                    result = data.get("data", {})
                    with self._lock:
                        self.widget_token = result["widget_token"]
                        self.member_alias = result["member"]["alias"]
                        self.member_pma_id = result["member"]["pma_id"]
                        self.member_tier = result["member"]["tier"]
                        self.is_connected = True

                        # Sync timezone from server if set
                        server_tz = result["member"].get("timezone")
                        if server_tz:
                            self.user_timezone = server_tz

                        # Persist non-sensitive metadata to settings.json;
                        # token goes to OS keyring (or settings.json fallback).
                        self._settings["member_alias"] = self.member_alias
                        self._settings["member_pma_id"] = self.member_pma_id
                        self._settings["member_tier"] = self.member_tier
                        if server_tz:
                            self._settings["timezone"] = server_tz
                        _safe_write_json(SETTINGS_PATH, self._settings)
                    _save_widget_token(self.widget_token)

                    dialog.after(0, lambda: (dialog.destroy(), self._rebuild_context_menu(), self._update_display()))

                except (requests.RequestException, ValueError, KeyError) as e:
                    dialog.after(0, lambda: status_label.config(
                        text="Connection error. Check your network.", fg="#bb4444"))

            threading.Thread(target=_connect, daemon=True).start()

        btn_frame = tk.Frame(dialog, bg=BG_COLOR)
        btn_frame.pack(pady=(0, 15))
        tk.Button(btn_frame, text="Connect", command=do_connect,
                  bg=FG_GOLD, fg=BG_COLOR, font=("Georgia", 11, "bold"),
                  relief="flat", padx=20, pady=5).pack(side="left", padx=8)
        tk.Button(btn_frame, text="Cancel", command=dialog.destroy,
                  bg="#333", fg=FG_TEXT, font=("Georgia", 11),
                  relief="flat", padx=20, pady=5).pack(side="left", padx=8)

        # Enter key submits
        dialog.bind("<Return>", do_connect)
        email_entry.focus_set()

        # Now center on screen after all widgets are packed
        dialog.update_idletasks()
        dw = dialog.winfo_reqwidth()
        dh = dialog.winfo_reqheight()
        x = (dialog.winfo_screenwidth() - dw) // 2
        y = (dialog.winfo_screenheight() - dh) // 2
        dialog.geometry(f"{dw}x{dh}+{x}+{y}")

    def _fetch_whats_new_into_label(self, label, target_version: str):
        """Pull the CHANGELOG section for `target_version` from the public
        repo and stuff it into the given Tk label. Runs in a background
        thread; UI mutations marshalled via root.after."""
        url = (f"https://raw.githubusercontent.com/SoteriaCovenantTrust/"
               f"soterian-clock/main/CHANGELOG.md")
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            section = self._extract_changelog_section(r.text, target_version)
        except Exception as e:
            section = f"(Couldn't load CHANGELOG: {e})"

        def _apply():
            try:
                label.config(text=section)
            except Exception:
                pass  # label may have been destroyed if user closed dialog
        self.root.after(0, _apply)

    @staticmethod
    def _extract_changelog_section(md: str, version: str) -> str:
        """Pull the Keep-a-Changelog section for `version` (matching either
        `## [vX.Y.Z]` or `## [X.Y.Z]`) until the next `##` heading. Returns
        plain-text body trimmed; returns a polite placeholder if not found."""
        import re
        # Match `## [2.9.0]` or `## [v2.9.0]` then capture body until next ##
        pattern = re.compile(
            r"^##\s*\[v?" + re.escape(version) + r"\][^\n]*\n(.*?)(?=^##\s)",
            re.MULTILINE | re.DOTALL,
        )
        m = pattern.search(md)
        if not m:
            return f"(No CHANGELOG section found for v{version}.)"
        body = m.group(1).strip()
        # Trim to keep the dialog reasonable: at most ~25 lines, ~1500 chars.
        lines = body.splitlines()
        if len(lines) > 25:
            lines = lines[:24] + ["…"]
        body = "\n".join(lines)
        if len(body) > 1500:
            body = body[:1480] + "…"
        return body

    def _show_about_dialog(self):
        """Modal About dialog. Surfaces what's running so a member can paste
        the version + commit info into a support thread without ssh-ing into
        their box. Build info comes from constants only — no external probe,
        so it works offline."""
        dialog = tk.Toplevel(self.root)
        dialog.title("About Soterian Clock")
        dialog.configure(bg=BG_COLOR)
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)

        frame = tk.Frame(dialog, bg=BG_COLOR, padx=24, pady=20)
        frame.pack(fill="both", expand=True)

        title = tk.Label(frame, text="\U0001F56F️  Soterian Clock",
                         font=("Georgia", 14, "bold"),
                         fg=FG_GOLD, bg=BG_COLOR)
        title.pack(anchor="w")

        sub = tk.Label(frame, text="Petrachora Soteria — desktop calendar widget",
                       font=("Georgia", 10),
                       fg=FG_TEXT, bg=BG_COLOR)
        sub.pack(anchor="w", pady=(2, 14))

        info_lines = [
            ("Version", WIDGET_VERSION),
            ("Platform", f"{platform.system()} {platform.machine()} ({platform.python_version()})"),
            ("Calendar API", API_BASE),
            ("Membership", MEMBERSHIP_BASE),
            ("Almanac", CELEBRATIONS_BASE),
            ("Connected as", self.member_alias if self.is_connected else "—"),
            ("Member tier", (
                _t(f"tier.{int(self.member_tier)}")
                if self.is_connected and self.member_tier is not None
                else "—"
            )),
            ("Timezone", self.user_timezone),
        ]
        for k, v in info_lines:
            row = tk.Frame(frame, bg=BG_COLOR)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{k}:", font=("Georgia", 9, "bold"),
                     fg=FG_GOLD, bg=BG_COLOR, width=14, anchor="w").pack(side="left")
            tk.Label(row, text=str(v), font=("Georgia", 9),
                     fg=FG_TEXT, bg=BG_COLOR, anchor="w").pack(side="left")

        # "What's new in this version" — only when we just upgraded.
        # Body comes from a background fetch of the public CHANGELOG; we
        # render a placeholder first and replace once the fetch lands so
        # the dialog opens fast even on slow networks.
        if self._upgraded_from_version:
            tk.Frame(frame, bg=BORDER_COLOR, height=1).pack(fill="x", pady=(14, 8))
            tk.Label(frame,
                     text=f"What's new in v{WIDGET_VERSION} (since v{self._upgraded_from_version})",
                     font=("Georgia", 9, "bold"),
                     fg=FG_GOLD, bg=BG_COLOR).pack(anchor="w")
            whats_new = tk.Label(frame, text="Loading from CHANGELOG...",
                                 font=("Georgia", 8), fg="#bbb", bg=BG_COLOR,
                                 justify="left", anchor="w", wraplength=480)
            whats_new.pack(anchor="w", fill="x", pady=(4, 0))
            threading.Thread(
                target=self._fetch_whats_new_into_label,
                args=(whats_new, WIDGET_VERSION),
                daemon=True,
            ).start()

        tk.Frame(frame, bg=BORDER_COLOR, height=1).pack(fill="x", pady=(14, 10))

        tk.Label(frame, text="Source: github.com/SoteriaCovenantTrust/soterian-clock (MIT)",
                 font=("Georgia", 8), fg="#888", bg=BG_COLOR).pack(anchor="w")
        tk.Label(frame, text="© Soteria Covenant Trust",
                 font=("Georgia", 8), fg="#888", bg=BG_COLOR).pack(anchor="w")

        btn = tk.Button(frame, text="Close", command=dialog.destroy,
                        bg=FG_GOLD, fg=BG_COLOR, font=("Georgia", 10, "bold"),
                        relief="flat", padx=20, pady=4)
        btn.pack(pady=(14, 0))

        dialog.bind("<Escape>", lambda e: dialog.destroy())
        dialog.update_idletasks()
        dw = dialog.winfo_reqwidth()
        dh = dialog.winfo_reqheight()
        x = (dialog.winfo_screenwidth() - dw) // 2
        y = (dialog.winfo_screenheight() - dh) // 2
        dialog.geometry(f"{dw}x{dh}+{x}+{y}")

    def _disconnect_membership(self):
        """Revoke widget token and clear member data."""
        def _do_disconnect():
            if self.widget_token:
                try:
                    requests.post(
                        MEMBERSHIP_WIDGET_DISCONNECT,
                        headers={"X-Widget-Token": self.widget_token},
                        timeout=5,
                    )
                except requests.RequestException:
                    pass  # Best-effort revocation
            with self._lock:
                self.widget_token = ""
                self.member_alias = ""
                self.member_pma_id = ""
                self.member_tier = None
                self.is_connected = False
                self.inbox_unread = 0
                self.inbox_urgent = 0
                for key in ("member_alias", "member_pma_id", "member_tier"):
                    self._settings.pop(key, None)
                _safe_write_json(SETTINGS_PATH, self._settings)
            _delete_widget_token()
            self.root.after(0, lambda: (self._rebuild_context_menu(), self._update_display()))

        threading.Thread(target=_do_disconnect, daemon=True).start()

    def _rebuild_context_menu(self):
        """Rebuild the context menu to reflect connect/disconnect state."""
        self.context_menu.delete(0, "end")
        self.context_menu.add_command(label="\u21BB  Refresh Now", command=self.refresh_now)
        self.context_menu.add_command(label="\u2398  Copy Date", command=self._copy_date)
        self.context_menu.add_separator()

        # Timezone submenu
        self.tz_menu = tk.Menu(self.context_menu, tearoff=0,
                               bg=BG_COLOR, fg=FG_TEXT,
                               activebackground=FG_GOLD,
                               activeforeground=BG_COLOR)
        for tz in TIMEZONE_CHOICES:
            self.tz_menu.add_command(
                label=f"{'  \u2713 ' if tz == self.user_timezone else '     '}{tz}",
                command=lambda t=tz: self._set_timezone(t),
            )
        self.context_menu.add_cascade(label="\U0001F30D  Timezone", menu=self.tz_menu)
        self.context_menu.add_separator()

        if self.is_connected:
            self.context_menu.add_command(
                label=f"\U0001F464  Connected: {self.member_alias or self.member_pma_id}",
                state="disabled",
            )
            self.context_menu.add_command(
                label="\u26D4  Disconnect from Membership",
                command=self._disconnect_membership,
            )
        else:
            self.context_menu.add_command(
                label="\U0001F517  Connect to Membership",
                command=self._show_connect_dialog,
            )

        self.context_menu.add_separator()
        self.context_menu.add_command(label="\U0001F4C5  Open Almanac", command=lambda: webbrowser.open(CELEBRATIONS_BASE))
        if self.is_connected:
            inbox_label = (f"\U0001F4EC  Open Inbox ({self.inbox_unread} unread)"
                           if self.inbox_unread > 0
                           else "\U0001F4EC  Open Inbox")
            self.context_menu.add_command(label=inbox_label,
                                          command=lambda: webbrowser.open(f"{MEMBERSHIP_BASE}/dashboard"))
        self.context_menu.add_command(label="\U0001F310  Open time.soteriacovenant.org", command=lambda: webbrowser.open(API_BASE))
        if self.upgrade_available:
            self.context_menu.add_separator()
            self.context_menu.add_command(
                label=f"\u2b07  Download update (v{self.upgrade_min_version}+)",
                command=lambda: webbrowser.open(self.upgrade_releases_url
                                                or "https://github.com/SoteriaCovenantTrust/soterian-clock/releases"),
            )
        self.context_menu.add_separator()
        self.context_menu.add_command(label="\u2715  Quit", command=self.quit_app)

    # -------------------------------------------------------------------
    # Window visibility
    # -------------------------------------------------------------------

    def show_window(self):
        self._hidden = False
        self.root.deiconify()
        self.root.lift()

    def hide_window(self):
        self._hidden = True
        self.root.withdraw()

    # -------------------------------------------------------------------
    # System tray
    # -------------------------------------------------------------------

    def _start_tray(self):
        """Start the system tray icon in a background thread."""
        icon = _create_tray_icon(self)
        if icon is not None:
            self._tray_icon = icon
            tray_thread = threading.Thread(target=icon.run, daemon=True)
            tray_thread.start()

    # -------------------------------------------------------------------
    # Shutdown
    # -------------------------------------------------------------------

    def _signal_handler(self, signum, frame):
        self.quit_app()

    def quit_app(self):
        """Clean shutdown."""
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        cleanup_lock()
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_clock(start_hidden=False):
    root = tk.Tk()
    app = SoterianClock(root, start_hidden=start_hidden)

    # Clean up lock on normal exit
    import atexit
    atexit.register(cleanup_lock)

    root.mainloop()


def _cli_upgrade() -> int:
    """Headless self-update path. Probes /api/v1/version for the latest
    widgetVersionName, runs the same download → SHA256-verify → atomic-swap
    → systemctl restart flow as the tray "Install update" item, but without
    a Tk root or the dashbar. Useful for `cron`, packaging scripts, or a
    user who just wants to upgrade from a terminal.

    Exit codes:
      0 — already up to date OR successfully launched the update
      1 — version probe failed
      2 — update flow failed (download / hash / swap)
    """
    try:
        r = requests.get(MEMBERSHIP_VERSION, timeout=15)
        r.raise_for_status()
        data = r.json().get("data", {})
        latest = (data.get("widgetVersionName") or "").strip()
    except Exception as e:
        print(f"soterian-clock --upgrade: version probe failed: {e}", file=sys.stderr)
        return 1
    if not latest:
        print(f"soterian-clock --upgrade: no widgetVersionName from {MEMBERSHIP_VERSION}",
              file=sys.stderr)
        return 1
    if _ver_tuple(latest) <= _ver_tuple(WIDGET_VERSION):
        print(f"soterian-clock --upgrade: already at v{WIDGET_VERSION} "
              f"(latest is v{latest}). Nothing to do.")
        return 0

    print(f"soterian-clock --upgrade: v{WIDGET_VERSION} → v{latest} ...")

    # Re-use the SoterianClock._do_self_update method without instantiating
    # the full widget (no Tk root). The method only touches `self` for
    # logging; we pass a stub via __new__.
    stub = SoterianClock.__new__(SoterianClock)
    ok, msg = stub._do_self_update(latest)
    print(msg)
    return 0 if ok else 2


if __name__ == "__main__":
    # Headless --upgrade short-circuits before any Tk / lockfile dance —
    # it's safe to run while the regular widget is also running, since
    # the systemctl restart at the end will swap in the new binary.
    if "--upgrade" in sys.argv:
        sys.exit(_cli_upgrade())

    if already_running():
        print("Soterian Clock is already running.")
        sys.exit(0)

    start_hidden = "--tray-only" in sys.argv

    if "--background" in sys.argv and _SYSTEM != "Windows":
        pid = os.fork()
        if pid > 0:
            sys.exit()
        os.setsid()
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")

    run_clock(start_hidden=start_hidden)
