# Cellular Router Worker Bee

A small, self-contained test appliance for a Raspberry Pi 4B with a 3.5-inch 480x320
touchscreen. It joins a cellular router's Wi-Fi, checks real internet reachability
through that Wi-Fi only, runs an Ookla speed test, logs everything locally, waits
300 seconds, and repeats until you tap Stop Testing.

Target: Raspberry Pi OS Lite 64-bit (Debian 13 "Trixie", image 2026-04-21 or newer;
Bookworm also works). No desktop session is used: the UI draws directly to the
kernel framebuffer and reads touches from evdev.

## What It Does Each Cycle

1. Verify association with the configured SSID and an IPv4 address.
2. If not connected, make one bounded connection attempt (45 s default).
3. Wi-Fi failed: record `WIFI_FAILED`, skip the rest.
4. Verify the kernel's default route for test traffic is the Wi-Fi (else `ROUTE_FAILED`).
5. Reachability through the Wi-Fi only: gateway ping, pings to 1.1.1.1 and 8.8.8.8,
   DNS on a socket bound to the Wi-Fi, HTTPS to a known-content endpoint with a fallback.
   Captive portals and redirects count as failure. ICMP alone cannot block the speed test.
6. Reachability failed: record `CONNECTIVITY_FAILED` with per-probe diagnostics.
7. One Ookla speed test with structured output, 180 s hard cap, interface re-checked
   before and after.
8. Write a CSV row and JSONL events. Statuses: `SUCCESS`, `WIFI_FAILED`, `ROUTE_FAILED`,
   `CONNECTIVITY_FAILED`, `SPEEDTEST_FAILED`, `SPEEDTEST_TIMEOUT`, `INTERRUPTED`.
9. Wait exactly 300 s from the end of the attempt, then start the next cycle.

Stop Testing works in every phase, kills child processes, records `INTERRUPTED` if a
cycle was in progress, and returns to Idle keeping the last results on screen.

## Design Choices

- **Framebuffer UI, no X11/Wayland.** The SPI panel appears as `/dev/fbN`; the app
  renders with Pillow and writes RGB565 frames. Fewer packages, nothing to log in,
  works identically with the fbtft or DRM panel driver.
- **Root systemd service.** Needed for the framebuffer, evdev, `SO_BINDTODEVICE`,
  tty graphics mode and `nmcli connection up` without a login session.
- **Routing.** The Wi-Fi profile gets route metric 50 (Ethernet is 100) and exclusive
  DNS priority, so while it is up it is the default path for everything, including
  the speed test's own DNS. Probes additionally bind to the interface, the speed test
  gets `--interface`, and the route is verified before each stage. If the default path
  is not the Wi-Fi, the cycle is `ROUTE_FAILED`, never a false success. Details and a
  stricter option: `docs/NETWORK.md`.
- **Wi-Fi password** is stored only in NetworkManager's root-only keyfile, entered
  through a silent prompt. It is never in config, argv, logs or this repo.
- **Measured throughput includes the Pi-to-router Wi-Fi link.** The Pi 4B radio
  (802.11ac, single stream) is often the bottleneck above roughly 100 Mbps. Treat
  results as "what a Wi-Fi client of this router sees", not the cellular link alone.

## Install (Copy and Paste)

Flash Raspberry Pi OS Lite 64-bit with SSH enabled and no Wi-Fi configured in
Imager. Boot with Ethernet for management, then:

```bash
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

```bash
sudo apt install -y git
git clone https://github.com/<you>/cellular-router-worker-bee.git
cd cellular-router-worker-bee
sudo ./scripts/install.sh
```

```bash
sudo ./scripts/install-speedtest.sh
```

```bash
sudo ./scripts/setup-wifi.sh
```

Power off, attach the display, power on, then enable the LCD overlay and reboot:

```bash
sudo ./scripts/setup-display.sh && sudo reboot
```

After reboot, verify the panel and calibrate touch, then start:

```bash
crwb check-display --test
```

```bash
sudo crwb calibrate-touch
```

```bash
sudo systemctl start crwb && crwb status
```

If the display shows nothing or the touch axes are wrong, see `docs/DISPLAY.md`.
If Wi-Fi does not connect, see `docs/NETWORK.md`.

## Everyday Commands

| Task | Command |
|---|---|
| Service start / stop / restart | `sudo systemctl start crwb` / `stop` / `restart` |
| Service status and log | `systemctl status crwb` and `journalctl -u crwb -n 50` |
| Live app status | `crwb status` (add `--json` for everything) |
| Start / stop the test loop (same as the buttons) | `crwb start` / `crwb stop` |
| One diagnostic cycle in the foreground | `sudo systemctl stop crwb && sudo crwb once` |
| Recent results | `crwb results -n 20` |
| Recent events | `crwb events -n 50` |
| Change the configuration label | `crwb label "Config B"` (applies at the next cycle) |
| Change the interval | `crwb interval 300 && sudo systemctl restart crwb` |
| Pin or unpin a speed-test server | `speedtest -L`, then `crwb server 18531` or `crwb server auto` |
| Screen sleep timeout | `crwb sleep 120`, `crwb sleep off`, then restart |
| Autostart the loop at boot | `crwb autostart on` |
| Wi-Fi and routing check | `crwb check-wifi` |
| WPA3 capability report | `./scripts/check-wpa3.sh` |
| Raw touch coordinates | `sudo crwb check-touch` |
| Effective configuration | `crwb config` |
| Retrieve logs to a PC | `scp bee@workerbee:/var/lib/crwb/results.csv .` |

## Files

| Path | Purpose |
|---|---|
| `/etc/crwb/config.toml` | Configuration (TOML, no secrets). Example: `config.example.toml` |
| `/var/lib/crwb/results.csv` | One row per finished attempt, including failures and interruptions |
| `/var/lib/crwb/events.jsonl` | Lifecycle events, per-probe diagnostics, raw speed-test JSON |
| `/var/lib/crwb/results.<stamp>.csv` | Rotated logs (size-based, retention configurable) |
| `/run/crwb/state.json` | Live state snapshot (1 Hz), read by `crwb status` |
| `/opt/crwb` | Installed application |
| `/etc/NetworkManager/system-connections/Kangaroo Labs.nmconnection` | Wi-Fi profile with password, mode 600 |

CSV columns and JSONL event types are listed in `docs/OPERATIONS.md`.

## Screen

Main screen (480x320, fits without scrolling): title and label, Wi-Fi status and the
Wi-Fi IP (or "No IP"), internet status with the age of the last check, download /
upload / speed-test idle latency, phase, countdown (MM:SS) during the wait, session
counters, last-result timestamp or last error, a large Start Test / Stop Testing
button and a Details button. Old measurements are greyed and labelled "previous
result" while a new test runs.

Details view: Wi-Fi link (SSID, BSSID, dBm, band, channel, link rate), IP, gateway,
router DNS, public IP, per-target ping, DNS and HTTPS outcomes, speed-test server,
jitter and loss, session timing and counters, recorded test traffic, disk space,
device and config paths. Back, Up, Down and Next Label buttons.

The screen sleeps after 120 s without a touch (configurable, `0` disables). The first
touch only wakes it. Testing, logging and Wi-Fi are unaffected by sleep. If the panel
has no backlight control, the screen is driven black but the backlight stays lit.

## After a Crash or Power Loss

systemd restarts the service within 5 s. On start the app records any cycle that was
in progress as `INTERRUPTED` with `failed_stage=crash_recovery`, writes an
`app_start` event, and sits in Idle unless `autostart = true`. Historical logs are
never touched. Timestamps carry the timezone and a `clock_synced` flag so results
from an unsynchronized clock are identifiable.

## Development and Tests

```bash
python3 -m unittest discover -s tests -t .
```

```bash
python3 -m crwb --config config.example.toml preview --out preview/
```

The suite uses a fake backend and fake speedtest scripts (`tests/fake_speedtest/`)
to cover every status, interruption, timeout, partial result, log-write failure, sleep
and wake behaviour, and the 480x320 layout. What was verified in software versus what
still needs the physical Pi is tracked in `docs/VALIDATION.md`.

## Future Notifications

Not implemented. The integration point is `events.jsonl`: a separate watcher can
tail it for `cycle_end` events and send email or push notifications without
touching the test loop.
