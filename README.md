# Soterian Clock

Cross-platform desktop widget for the **Petrachora Soteria** calendar — a 13-month / 364-day solar calendar with vernal-equinox epoch (March 20 2025), real-time astronomical data, and member sync against the [Soteria Covenant](https://soteriacovenant.org).

An always-on-top single-line dashbar shows:

- Gregorian date + time in your chosen timezone
- Soterian date (e.g. `Luxen, Veritas 21, Year 2 of the Root`)
- Trust day · week · segment
- Sun sign · moon phase
- Your member alias + tier (when connected)
- Inbox unread count when you have new messages
- Transient celestial-event notices (eclipses, planetary stations, ingresses)

A system-tray icon and right-click context menu give you refresh, timezone picker, almanac/inbox shortcuts, member connect/disconnect, in-place self-update, an About dialog, and quit.

## Install

### Linux

```bash
tar -xzf soterian-clock-*-linux-x86_64.tar.gz
cd soterian-clock
./install.sh
```

The installer drops the binary in `~/.local/share/soterian-clock/`, symlinks `~/.local/bin/soterian-clock`, and registers a **systemd user unit** (`Restart=on-failure`, output captured to journald). The widget starts immediately and on every login.

Status / logs / stop / uninstall:

```bash
systemctl --user status soterian-clock
journalctl --user -u soterian-clock -f
systemctl --user disable --now soterian-clock
bash ~/.local/share/soterian-clock/uninstall.sh   # full clean uninstall
```

### Windows

Extract the zip, run `soterian-clock.exe`. Optionally drop a shortcut into `shell:startup` to launch on login.

### macOS

Extract the zip, move `soterian-clock` into `/Applications`. Add it to `System Settings → General → Login Items` for autostart.

## Connecting to your Membership account

Right-click the dashbar → **Connect to Membership**. Enter your covenant email + password (Tier 2+ members only).

What you get when connected:

- Your alias + tier badge in the dashbar (`👤 A.J.S. T4`)
- Bidirectional timezone sync with your member profile
- Inbox unread badge (`📬 3`, with `❗ K` for urgent)
- A "📬 Open Inbox" shortcut in the tray menu
- The "✨ ..." celestial notices stay visible in the dashbar

The connection uses a long-lived `X-Widget-Token` stored in your **OS keyring** — libsecret/SecretService on Linux, Keychain on macOS, Credential Locker on Windows. (If your platform doesn't have a keyring, the token falls back to plaintext in `settings.json`.) Revoke from the widget's right-click menu, the tray menu, or remotely from the [Membership portal's Connected Devices view](https://members.soteriacovenant.org/dashboard).

## Updating

The widget polls Membership every 24 h for the latest published version. When a newer release is available, the tray menu adds:

- **`⬇ Install update (vX.Y.Z)`** — Linux: downloads the matching tarball over HTTPS, verifies its SHA256 against the release's published `SHA256SUMS`, atomically swaps the install dir, and restarts the systemd unit.
- **`🌐 Open Releases page`** — same thing, manual path; works on all platforms.

After the update lands, the widget surfaces a `✓ Updated to vX.Y.Z (was vA.B.C)` confirmation in the dashbar on its next start.

You can also reach the [Releases page](https://github.com/SoteriaCovenantTrust/soterian-clock/releases) directly any time.

## Privacy

The widget polls these endpoints:

- `https://time.soteriacovenant.org/api/date` — Soterian date (~10 ms, every 60 s)
- `https://time.soteriacovenant.org/api/now` — sun sign + moon phase (~4 s, every 5 min)
- `https://time.soteriacovenant.org/api/v1/mobile/snapshot` — celestial alerts (every 4 h, opt-out via `celestial_alerts: false` in settings.json)

If you connect to Membership, it also polls:

- `https://members.soteriacovenant.org/api/v1/widget/sync` — your alias, tier, timezone, inbox unread count (every 10 min)
- `https://members.soteriacovenant.org/api/v1/version` — version handshake (every 24 h)

No telemetry beyond your own member's `last_sync_at` timestamp (which the Membership portal shows you in your Connected Devices view). No third-party services. No analytics. The widget caches the last good response locally so it keeps showing meaningful data when offline.

## Settings

User preferences live in `~/.config/soterian-clock/settings.json` (Linux/macOS path; Windows uses `%APPDATA%\soterian-clock\settings.json`). All keys are optional; the widget reads what's there and applies defaults to anything missing.

| Key | Type | Default | Notes |
|---|---|---|---|
| `timezone` | string | `"System Default"` | IANA timezone name. Auto-syncs with your member profile when connected. |
| `ui_scale` | float | auto-detected | Manual override for HiDPI scaling. Widget probes screen DPI at startup; if the inferred scale is wrong, set this explicitly (e.g. `2.0`). |
| `celestial_alerts` | bool | `true` | Set to `false` to silence the 4-hourly celestial-event check. |
| `member_alias`, `member_pma_id`, `member_tier` | str/int | — | Cached from `/widget/sync`; survive offline restarts. |
| `seen_alerts` | list | `[]` | Fingerprints of celestial alerts already shown — bounded to last 200 to keep the file small. |
| `last_known_version` | string | — | Used for the post-update "✓ Updated to vX" confirmation; written on every startup. |

The widget token (after Membership connect) is stored in the OS keyring under service `soterian-clock`, username `widget-token` — **not** in `settings.json` (unless your platform has no keyring backend).

## Build from source

```bash
git clone https://github.com/SoteriaCovenantTrust/soterian-clock
cd soterian-clock
pip install -r requirements.txt pyinstaller
bash installer/build.sh
```

The bundle lands in `dist/soterian-clock/`. Single source of truth for the version string is the `WIDGET_VERSION` constant at the top of `soterian_clock.py`; `installer/build.sh` greps it and propagates through the binary, the tarball name, and the GitHub Release artifact name.

### Tests

```bash
pip install pytest
python -m pytest tests/
```

Tests cover the version comparator, keyring + settings.json fallback for token storage, and SHA256 verification of the auto-update download. Tk-dependent code is covered by the live integration testing on the maintainer's machine — there's no automated UI test suite. The CI workflow runs the unit tests on every tag push before invoking the matrix build, so a regression in the test-covered code paths blocks the release.

## License

MIT — see [LICENSE](LICENSE).
