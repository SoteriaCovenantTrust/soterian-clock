# Changelog

All notable changes to the Soterian Clock widget. Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), [Semantic Versioning](https://semver.org/).

## [2.0.3] - 2026-05-06 — First public release

The first member-installable release. The v2 source had been built privately for a month inside the Soteria monorepo and shipped to the maintainer's laptop via PyInstaller; this is the first version published as a standalone GitHub Release with downloadable Linux + macOS + Windows tarballs.

### What's in v2

- **Cross-platform.** Linux, Windows, macOS — platform-aware paths, lock files, autostart wiring.
- **Soteria-branded dashbar.** Single-line strip in gold/dark Georgia serif: Gregorian date+time (in your tz) │ PETRACHORA SOTERIA │ Soterian date │ Day · Wk │ Segment │ Sun sign · Moon phase │ Member alias (when connected).
- **Background-threaded fetching.** `/api/date` (10 ms primary, 60 s refresh) + `/api/now` (rich, 5 min). Tk UI never blocks on the network.
- **System tray icon** with right-click menu (refresh, copy date, timezone picker, member connect/disconnect, open website, quit).
- **Right-click context menu** on the dashbar mirrors the tray menu.
- **Membership sync.** Connect via covenant email + password (Tier 2+); long-lived `X-Widget-Token`. Member alias + tier shown in dashbar; timezone syncs bidirectionally with member profile every 5 min.
- **Offline cache.** Last-known-good `/api/date` response written locally; status dot turns red on calendar-API failure, **amber** on member-sync failure (calendar OK), green when both healthy.
- **Defensive `_update_display`.** Wrapped in try/except + flush-to-stderr so a single bad render cannot freeze the widget — closes the silent-death failure mode the v1 widget hit in April 2026 (3-week outage with no log trail).
- **Version handshake.** Asks Membership `/api/v1/version` for `widgetMinVersionName`; if the server has moved past the local floor, the dashbar appends `⚠ Update vX.Y.Z+`.

### Distribution

Linux installer wires a systemd user unit (`Restart=on-failure`, `StandardOutput=journal`, `WantedBy=graphical-session.target`) — supervised + log-captured by default. Pre-existing XDG autostart entries from earlier installs are auto-retired so the widget doesn't double-launch.

### Development history

Versions v2.0.0 → v2.0.2 were tagged inside the Soteria monorepo (`clock-v2.0.0`, `clock-v2.0.1`, `clock-v2.0.2`) but never produced public artifacts — v2.0.0 had a Windows-CI failure (colon in a checked-in cookie-jar filename, fixed in `661d59a7`); v2.0.1 was a defensive `_update_display` patch (never tagged); v2.0.2 was tagged but failed CI for the same colon-filename issue at a different path, so no built artifact exists. v2.0.3 is the first cumulative release that built clean across all three platforms — and the first one published here.
