# Operations: Logs, Statuses, Settings, Recovery

## Log Files

`/var/lib/crwb/results.csv` has one row per finished attempt. Columns:

| Column | Meaning |
|---|---|
| `session_id`, `cycle_id` | Session (new on every Start Test) and cycle number within it |
| `label` | Configuration label captured at cycle start |
| `start_time`, `end_time` | ISO 8601 with timezone offset |
| `duration_s` | Monotonic-clock duration of the attempt |
| `status` | One of the statuses below |
| `failed_stage` | `wifi`, `route`, `connectivity`, `speedtest_precheck`, `speedtest`, `crash_recovery`, empty on success |
| `error` | Readable reason |
| `next_attempt_time` | Planned start of the next cycle (empty if stopped) |
| `clock_synced` | `True`/`False` from timedatectl, empty if unknown |
| `interface`, `ssid`, `bssid`, `local_ip`, `gateway`, `signal_dbm`, `band`, `channel`, `link_rate_mbps` | Wi-Fi link at test time |
| `dns_ok`, `dns_server`, `dns_ms` | First successful DNS lookup bound to the Wi-Fi |
| `https_ok`, `https_url`, `https_status`, `https_ms` | HTTPS probe that decided reachability |
| `icmp_ok`, `gateway_ping_ms`, `gateway_ping_loss_pct` | ICMP summary and gateway ping |
| `ping1_*`, `ping2_*` | Per-target average latency and loss |
| `download_mbps`, `upload_mbps` | Ookla `bandwidth` (bytes/s) x 8 / 1,000,000 |
| `latency_ms`, `jitter_ms`, `packet_loss_pct` | Speed-test idle latency, jitter, loss (blank if the tool omitted them) |
| `download_bytes`, `upload_bytes` | Bytes transferred by the speed test |
| `public_ip`, `server_id`, `server_name`, `server_location`, `server_host`, `result_url` | From the tool |
| `speedtest_version`, `speedtest_partial` | Tool version; `True` when numbers come from progress lines of an unfinished test |

Blank means not measured. `0.0` throughput is a real measurement, not a failure.

`/var/lib/crwb/events.jsonl` has one JSON object per line with `time` and `event`:
`app_start`, `app_stop`, `display_attached`, `touch_attached`, `session_start`,
`session_stop`, `cycle_start`, `wifi_result`, `route_result`, `connectivity_result`,
`speedtest_result`, `cycle_end` (includes the full CSV row plus a `detail` object with
every probe and the raw speed-test JSON), `cycle_orphaned`, `label_changed`,
`control`, `worker_crash`, `once_start`.

Rotation: when `results.csv` exceeds `csv_max_bytes` (5 MB) it is renamed
`results.<stamp>.csv` and a fresh file with a header starts; the newest
`retention_files` (10) rotated files are kept. Same for `events.jsonl` (20 MB).
Nothing is deleted on restart or on Start Test.

Write failures: every append is fsynced. On failure the main screen shows
`LOG WRITE FAILED: <reason>` in red, `crwb status` shows `STORAGE:`, and the JSONL
gets an error once writing works again. Free space is checked after every write and
shown in Details; below `low_disk_mb` (100 MB) a warning appears.

## Statuses

| Status | Meaning | Counted as |
|---|---|---|
| `SUCCESS` | Wi-Fi, route, reachability and speed test all completed | success |
| `WIFI_FAILED` | Not associated with the SSID or no IPv4 after one bounded attempt | failure |
| `ROUTE_FAILED` | Default path is not the Wi-Fi, or the connection changed around the speed test | failure |
| `CONNECTIVITY_FAILED` | No HTTPS success through the Wi-Fi (captive portal, DNS, TLS, timeout) | failure |
| `SPEEDTEST_FAILED` | Tool error, malformed output, pinned server unavailable | failure |
| `SPEEDTEST_TIMEOUT` | Tool exceeded the cap and was killed; partial numbers kept | failure |
| `INTERRUPTED` | Stop Testing (or a crash, `failed_stage=crash_recovery`) during a cycle | interrupted |

Session counters on screen: cycles, OK, failed, and stopped (interrupted) when non-zero.

## Changing Settings

All settings live in `/etc/crwb/config.toml`. Edit by hand or with the CLI, which
edits the file in place and validates it:

```bash
crwb label "APN test"          # applies at the next cycle start, no restart
crwb interval 600              # seconds from completion to next start
crwb server 18531              # pin an Ookla server id (speedtest -L lists them)
crwb server auto
crwb sleep 300                 # screen sleep seconds, or: crwb sleep off
crwb autostart on              # start the loop at boot
sudo systemctl restart crwb    # apply interval / sleep / autostart / display / touch changes
```

Predefined labels for the on-screen Next Label button: `app.labels` in the config.
The on-screen selection overrides the file label until the service restarts; both
are logged with a `label_changed` or `cycle_start` event.

## Interfaces

The app uses the first NetworkManager Wi-Fi device. If the Pi has more than one
(USB adapter), set `[wifi] interface = "wlan1"`. The Details view shows which was
chosen and why.

## Crash and Restart Behaviour

- systemd `Restart=always`, 5 s delay. Single instance enforced with a lock in
  `/run/crwb/lock`; a second `crwb run` or `crwb once` exits with code 3.
- `/var/lib/crwb/cycle_in_progress.json` exists only while a cycle runs. If present at
  startup, the previous cycle is recorded as `INTERRUPTED` / `crash_recovery` and the
  marker is cleared.
- Fresh boot: Idle unless `autostart = true`. Old results are not shown as live; the
  screen says "No session yet".
- Child processes (`ping`, `speedtest`) run in their own process groups and are
  killed on Stop, timeout and shutdown. The unit uses `KillMode=mixed` as a backstop.
- If the worker thread dies, the screen shows `WORKER STOPPED` and `crwb status`
  reports `worker alive: False`; restart the service.
- The display and touch device are re-probed every 5 s until found, so the app
  starts (and tests) even if the panel driver loads late or is missing.

## Running One Cycle by Hand

```bash
sudo systemctl stop crwb
sudo crwb once            # prints the result row; exit 0 on SUCCESS, 1 otherwise
sudo systemctl start crwb
```

Or, with the service running, `crwb start` and `crwb stop` drive the loop exactly
like the touch buttons.

## Simulating Speed-Test Outcomes

Point `[speedtest] command` at one of `tests/fake_speedtest/*.sh` (success, error,
malformed, partial, hang, zero, wrong_iface) to exercise the UI and logging without
using cellular data, then restore `command = "speedtest"`.
