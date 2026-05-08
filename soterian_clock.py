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
from datetime import datetime, timezone
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

REFRESH_FAST = 60       # /api/date refresh interval (seconds)
REFRESH_RICH = 300      # /api/now refresh interval (seconds)
REFRESH_SYNC = 600      # Member sync interval (seconds)
API_BASE = "https://time.soteriacovenant.org"
API_DATE = f"{API_BASE}/api/date"
API_NOW = f"{API_BASE}/api/now"
MEMBERSHIP_BASE = "https://members.soteriacovenant.org"
MEMBERSHIP_WIDGET_CONNECT = f"{MEMBERSHIP_BASE}/api/v1/widget/connect"
MEMBERSHIP_WIDGET_SYNC = f"{MEMBERSHIP_BASE}/api/v1/widget/sync"
MEMBERSHIP_WIDGET_TIMEZONE = f"{MEMBERSHIP_BASE}/api/v1/widget/timezone"
MEMBERSHIP_WIDGET_DISCONNECT = f"{MEMBERSHIP_BASE}/api/v1/widget/disconnect"
MEMBERSHIP_VERSION = f"{MEMBERSHIP_BASE}/api/v1/version"

# Local widget build version. Compared against widgetMinVersionName from the
# membership /version endpoint to surface "upgrade available" in the dashbar
# when the server has moved past the supported floor.
WIDGET_VERSION = "2.0.3"


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

    # Draw a small gold circle on dark background as the tray icon
    img = Image.new("RGBA", (64, 64), (13, 13, 13, 255))
    draw = ImageDraw.Draw(img)
    # Gold circle with dark border
    draw.ellipse([8, 8, 56, 56], fill=(212, 175, 55, 255), outline=(90, 74, 58, 255), width=2)
    # Small "S" hint via a simple line
    draw.text((22, 14), "S", fill=(13, 13, 13, 255))

    def on_show(icon, item):
        clock_app.root.after(0, clock_app.show_window)

    def on_hide(icon, item):
        clock_app.root.after(0, clock_app.hide_window)

    def on_refresh(icon, item):
        clock_app.root.after(0, clock_app.refresh_now)

    def on_open_site(icon, item):
        webbrowser.open(API_BASE)

    def on_quit(icon, item):
        icon.stop()
        clock_app.root.after(0, clock_app.quit_app)

    menu = pystray.Menu(
        pystray.MenuItem("Show Clock", on_show, default=True),
        pystray.MenuItem("Hide Clock", on_hide),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Refresh Now", on_refresh),
        pystray.MenuItem("Open Website", on_open_site),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
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
        self.widget_token = self._settings.get("widget_token", "")
        self.member_alias = self._settings.get("member_alias", "")
        self.member_pma_id = self._settings.get("member_pma_id", "")
        self.member_tier = self._settings.get("member_tier", None)
        self.is_connected = bool(self.widget_token)
        # Tracks whether the most recent /widget/sync succeeded. Distinct from
        # is_online (calendar API health) so the status dot can surface
        # partial failure: green = both healthy, amber = calendar OK but
        # member sync stale, red = calendar down. Defaults to True so a
        # not-yet-connected widget doesn't render amber on launch.
        self.is_member_online = True
        self.upgrade_available = False
        self.upgrade_min_version = ""

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

        # One-shot version handshake against Membership /api/v1/version.
        # If the server says we're below widgetMinVersionName, the dashbar
        # surfaces an "Update available" indicator.
        threading.Thread(target=self._fetch_widget_version, daemon=True).start()

        # Start system tray
        self._start_tray()

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
        self.context_menu.add_command(label="\U0001F310  Open Website", command=lambda: webbrowser.open(API_BASE))
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
        token-revoked (401) we clear the connection entirely; on transient
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
                    self._settings.pop("widget_token", None)
                    _safe_write_json(SETTINGS_PATH, self._settings)
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
        """One-shot version handshake. Asks Membership /api/v1/version what the
        minimum supported widget version is; if we're below it, sets
        upgrade_available so the dashbar can surface the warning. Failures are
        silent — version drift is non-urgent and a missing handshake shouldn't
        block startup."""
        try:
            r = requests.get(MEMBERSHIP_VERSION, timeout=10)
            r.raise_for_status()
            data = r.json().get("data", {})
            min_v = (data.get("widgetMinVersionName") or "").strip()
            if min_v and _ver_tuple(min_v) > _ver_tuple(WIDGET_VERSION):
                with self._lock:
                    self.upgrade_available = True
                    self.upgrade_min_version = min_v
                self.root.after(0, self._update_display)
        except Exception:
            pass

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

            # Member alias (when connected)
            if self.is_connected and self.member_alias:
                sections.append(f"\U0001F464 {self.member_alias}")

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

                        # Persist
                        self._settings["widget_token"] = self.widget_token
                        self._settings["member_alias"] = self.member_alias
                        self._settings["member_pma_id"] = self.member_pma_id
                        self._settings["member_tier"] = self.member_tier
                        if server_tz:
                            self._settings["timezone"] = server_tz
                        _safe_write_json(SETTINGS_PATH, self._settings)

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
                for key in ("widget_token", "member_alias", "member_pma_id", "member_tier"):
                    self._settings.pop(key, None)
                _safe_write_json(SETTINGS_PATH, self._settings)
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
        self.context_menu.add_command(label="\U0001F310  Open Website", command=lambda: webbrowser.open(API_BASE))
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


if __name__ == "__main__":
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
