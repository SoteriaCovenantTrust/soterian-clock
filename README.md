# Soterian Clock

Cross-platform desktop widget for the **Petrachora Soteria** calendar — a 13-month / 364-day solar calendar with vernal-equinox epoch (March 20 2025), real-time astronomical data, and member sync against the [Soteria Covenant](https://soteriacovenant.org).

A thin always-on-top dashbar shows:

- Gregorian date + time in your chosen timezone
- Soterian date (`Luxen, Veritas 21, Year 2 of the Root`)
- Trust day / week / segment
- Sun sign · moon phase
- Your member alias (when connected to Membership)

A system-tray icon gives you refresh, timezone picker, member connect/disconnect, and quit.

## Install

### Linux

```bash
tar -xzf soterian-clock-*-linux-x86_64.tar.gz
cd soterian-clock
./install.sh
```

The installer drops the binary in `~/.local/share/soterian-clock/`, symlinks `~/.local/bin/soterian-clock`, and registers a **systemd user unit** (`Restart=on-failure`, output captured to journald). The widget starts immediately and on every login.

Status / logs / stop:

```bash
systemctl --user status soterian-clock
journalctl --user -u soterian-clock -f
systemctl --user disable --now soterian-clock
```

### Windows

Extract the zip, run `soterian-clock.exe`. Optionally drop a shortcut into `shell:startup` to launch on login.

### macOS

Extract the zip, move `soterian-clock` into `/Applications`. Add it to `System Settings → General → Login Items` for autostart.

## Connecting to your Membership account

Right-click the dashbar → **Connect to Membership**. Enter your covenant email + password (Tier 2+ members only). Your alias appears in the dashbar; your timezone syncs bidirectionally with your member profile.

The connection uses a long-lived `X-Widget-Token`; revoke from the dashbar's right-click menu (Disconnect) or from the Membership portal's Connected Devices view.

## Privacy

The widget polls two public endpoints from your covenant install:

- `https://time.soteriacovenant.org/api/date` — Soterian date (10 ms)
- `https://time.soteriacovenant.org/api/now` — sun sign + moon phase (~4 s, every 5 min)

If you connect to Membership, it also polls:

- `https://members.soteriacovenant.org/api/v1/widget/sync` — your alias + tier + standing (every 10 min)

No telemetry, no third-party services, no analytics. The widget caches the last good response locally so it keeps showing meaningful data when offline.

## Build from source

```bash
git clone https://github.com/SoteriaCovenantTrust/soterian-clock
cd soterian-clock
pip install -r requirements.txt pyinstaller
bash installer/build.sh
```

The bundle lands in `dist/soterian-clock/`. Single source of truth for the version string is the `WIDGET_VERSION` constant at the top of `soterian_clock.py`; `installer/build.sh` greps it and propagates through the binary, the tarball name, and the GitHub Release artifact name.

## License

MIT — see [LICENSE](LICENSE).
