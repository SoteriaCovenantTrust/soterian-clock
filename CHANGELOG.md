# Changelog

All notable changes to the Soterian Clock widget. Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), [Semantic Versioning](https://semver.org/).

## [2.5.0] - 2026-05-08 — OS keyring + inbox badge + celestial alerts + self-update

Five independent feature improvements that compose into "the widget is a member's covenant dashboard". Telescopes intermediate v2.2.0 / v2.3.0 / v2.4.0 development versions into a single first external 2.x release.

### Added — security
- **Token storage in OS keyring.** v2.1.x stored the long-lived widget bearer token in plaintext at `~/.config/soterian-clock/settings.json`. From v2.5.0 the token lives in the platform keyring — libsecret/SecretService on Linux, Keychain on macOS, Credential Locker on Windows. Existing legacy tokens auto-migrate from settings.json on first launch and are removed from disk. If the keyring isn't available (headless box without dbus, locked-down sandbox), settings.json is the fallback so the widget keeps working.

### Added — UX
- **Inbox unread badge in the dashbar.** Connected members see "📬 N" right of their alias (with "❗ K" appended when K urgent messages are pending). Updates on every `/widget/sync` poll (10 min). Tray menu + right-click context menu add an "Open Inbox" item with the unread count in the label.
- **Celestial-event notifications.** Polls the `/api/v1/mobile/snapshot` calendar endpoint every 4 h; high-priority events with `days_until ≤ 1` (today/tomorrow) raise a 1-hour dashbar notice — e.g. "✨ Mercury Station Retrograde in Pisces on 2026-02-26". Fingerprints of shown alerts are persisted to settings.json (bounded to last 200) so the same event doesn't re-fire across restarts. Opt-out via `"celestial_alerts": false` in settings.json.

### Added — distribution
- **Self-update via the GitHub Releases page.** Tray menu adds an "⬇ Install update (vX.Y.Z)" item when the version handshake reports a newer release; clicking it downloads the matching tarball over HTTPS, atomically swaps the install dir (preserving the previous version at `~/.local/share/soterian-clock.bak`), and triggers `systemctl --user restart soterian-clock.service` — replacing the running process with the new binary in one click. Linux x86_64 only for now; macOS / Windows show "Open Releases page" instead. Trust model: HTTPS to github.com (cert-verified). SHA256 manifest verification deferred.
- **Version handshake captures both floor and latest.** v2.1.x only captured `widgetMinVersionName` (the "you must upgrade" floor); v2.5.0 also captures `widgetVersionName` (the latest available release) so auto-update installs the newest build, not just the floor.

### Notes for self-builders
- New runtime dependency: `keyring>=25.0.0`. The PyInstaller spec adds `keyring` + `keyring.backends.SecretService` / `macOS` / `Windows` to `hiddenimports` so the bundle has all three backends and picks the right one at runtime per platform.

## [2.1.1] - 2026-05-07 — HiDPI awareness + tray "Open Almanac" + revocation notice

### Fixed
- **Widget no longer renders half-size on HiDPI displays.** Tkinter defaults to ~96 DPI; on a HiDPI display under Wayland with mutter's `xwayland-native-scaling` experimental feature enabled (a GNOME default in some 2026 configurations), the X server reports the true native resolution and Tk renders the dashbar at roughly half the expected size. The widget now probes `winfo_fpixels('1i')` at launch and calls `tk scaling` when ppi > 120 so font points get the right point-to-pixel ratio. An explicit `ui_scale` override in `settings.json` takes precedence (set e.g. `2.0` to force a specific scale).
- **`_update_display` is now exception-safe.** Body extracted to `_render_display()` and wrapped in try/except in `_update_display()`, with errors flushed to stderr (so they reach journald under the systemd user unit). A single bad render — Tk error, malformed state, surprise exception — can no longer kill the Tk `after()` callback chain and leave the widget frozen.

### Added
- **Token-revocation surfaces in the dashbar.** When `/widget/sync` returns 401 (token revoked from the Membership portal, or member account deactivated), the widget raises a transient "⚠ Membership connection revoked — reconnect via menu" notice for 1 hour. Previously the alias just silently disappeared.
- **Periodic version recheck.** `_fetch_widget_version` now runs on a 24 h cadence, not just at startup. A widget left running for weeks now eventually sees upgrade prompts without needing a restart.
- **`Open Almanac`** entrypoint in the tray + right-click context menu — direct link to the Soterian Almanac at `https://almanac.soteriacovenant.org`.
- **Tray menu Connect/Disconnect is dynamic.** Uses pystray's `visible=lambda` callable so the same tray icon shows the appropriate item depending on current connection state.
- **`Download update` item** appears in both menus when the version handshake reports the local build is below `widgetMinVersionName`, opening the Releases page directly. (Superseded in v2.5.0 by the in-place self-update flow.)

### Changed
- **Tri-state status indicator.** The dashbar's status dot is now green (both calendar API + member sync healthy), amber (calendar OK but member sync stale), or red (calendar API down). Previously a member whose connection to `members.soteriacovenant.org` broke would see their alias quietly stale forever with the dot still green.

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
