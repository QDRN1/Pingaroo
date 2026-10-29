# Validation Matrix

Two kinds of evidence are listed. **Software**: automated tests in `tests/` run in a
Linux container with a fake backend and fake speedtest scripts; they exercise the real
worker, storage, controller and renderer code. **Hardware**: a procedure to run on
the Raspberry Pi 4B with the physical panel. None of the hardware steps has been
performed yet; the build environment has no Pi.

Run the software suite:

```bash
python3 -m unittest discover -s tests -t . -v
```

| Requirement | Software evidence | Hardware procedure (not yet performed) |
|---|---|---|
| Successful connectivity and speed test | `test_worker.CycleOutcomes.test_success`, `test_speedtest.test_success` (fake CLI jsonl) | `sudo crwb once` with the router up; expect `SUCCESS` and a `result_url` |
| Missing Wi-Fi | `test_wifi_missing` | Power off router Wi-Fi; expect `WIFI_FAILED` "SSID not found" within 45 s, next cycle 300 s later |
| Rejected credentials | `test_rejected_credentials`, error mapping in `wifi.explain_nmcli_error` | Re-run `setup-wifi.sh` with a wrong password; expect `WIFI_FAILED` "authentication failed" |
| Wi-Fi connected, no internet | `test_connected_no_internet` | Pull the SIM or disable WAN on the router; expect `CONNECTIVITY_FAILED`, speed test skipped |
| ICMP blocked, HTTPS succeeds | `test_icmp_blocked_https_ok_runs_speedtest` | Block ICMP on the router firewall; expect `SUCCESS` with `icmp_ok=False` |
| Speed-test error / malformed / partial / timeout | `test_speedtest.*` with `error.sh`, `malformed.sh`, `partial.sh`, `hang.sh`; worker: `test_speedtest_error_partial_timeout` | Set `[speedtest] command` to each fake script, `crwb start`, watch the screen and CSV |
| Zero throughput is not a failure | `test_zero_throughput_is_success_not_failure` | n/a |
| Router loss during a test, recovery later | `test_wifi_drop_during_speedtest_is_route_failed`, `test_recovery_on_later_cycle` | Power-cycle the router mid speed test; expect `ROUTE_FAILED` or `SPEEDTEST_FAILED`, then `SUCCESS` on a later cycle |
| Ethernet/VPN present, Wi-Fi down, no false success | `test_route_failed_when_default_path_not_wifi`, `test_tool_used_other_interface`; `SO_BINDTODEVICE` in probes | With Ethernet plugged in and router off: expect `WIFI_FAILED`; with `ip route` default on eth0 and Wi-Fi up: expect `ROUTE_FAILED` |
| 300 s delay after success and failure, no overlap | `Scheduling.test_interval_measured_from_completion_and_no_overlap` (1 s interval, measured completion-to-start, `max_speed_active == 1`), `test_interval_after_failure_too` | Compare `end_time` of row N with `start_time` of row N+1 in the CSV: 300 s |
| Start/Stop responsiveness, repeated taps | `ControllerBehaviour.test_tap_start_then_stop_and_debounce`, `StartStop.test_duplicate_start_rejected`, `test_stop_during_speedtest_records_interrupted` (stop returns < 2 s) | Tap Start, mash the button, tap Stop during a speed test; one session, `INTERRUPTED` row, Idle within 2 s |
| Correct IP updates and stale-result labelling | NetMonitor refreshes Wi-Fi every 3 s; `Layout.test_previous_result_labelled`, `test_unavailable_values_show_dash` | Watch the IP row after reconnect; during a test the old numbers grey out with "previous result" |
| Screen sleep during testing and countdown; work continues | `test_sleep_does_not_stop_worker`, `test_sleep_wake_consumes_gesture` (background render does not reset timer) | Let it sleep mid-test; CSV rows keep appearing; `crwb status` shows progress |
| First touch wakes without activating a button | `test_sleep_wake_consumes_gesture` | Tap exactly on Start/Stop while asleep: screen wakes, loop state unchanged |
| Session counters and preserved logs | `test_new_session_resets_counters_keeps_logs`, `test_label_captured_at_cycle_start` | Stop, Start again: counters reset, `results.csv` keeps old rows |
| Visible log-write failure | `StorageFailures.test_log_write_failure_is_visible`, `test_storage.test_write_error_reported` | `chattr +i /var/lib/crwb/results.csv` (or fill the disk); red `LOG WRITE FAILED` on screen; undo with `chattr -i` |
| Crash recovery marker | `test_orphan_marker_recorded_on_start` | `sudo kill -9 $(pidof -x crwb)` mid-cycle; after restart the CSV has an `INTERRUPTED` / `crash_recovery` row |
| 480x320 layout and touch targets | `Layout.test_main_screens_fit_and_buttons_sized` (all buttons >= 48 px high, inside the frame, non-overlapping), `crwb preview` PNGs reviewed visually | `crwb check-display --test`, `sudo crwb calibrate-touch`, `sudo crwb check-touch` corners |
| Config edits without code changes | `ConfigEdit.*`, `test_label_captured_at_cycle_start` | `crwb label "Config B"`; next CSV row carries it |
| Timezone-aware timestamps, monotonic durations, clock sync flag | `ClockFormat.test_iso_has_timezone`; `clock_synced` column | Boot without network: rows show `clock_synced=False` |
| Child process cleanup | `test_timeout_kills_process_group` checks the orphan `sleep 600` is gone | `sudo systemctl stop crwb` during a speed test; `pgrep speedtest` is empty |
| Duplicate instances | Headless smoke run: second `crwb run` exits 3, `crwb once` refuses while the service holds the lock | Same on the Pi |

Headless end-to-end run performed in the container (real app loop, real CLI, no
NetworkManager present): start via `crwb start`, three `WIFI_FAILED` cycles at a 2 s
interval, stop via `crwb stop`, SIGTERM shutdown, state file reports "Stopped".

## Not Verifiable Without the Hardware

- That the panel is ILI9486 and the `piscreen` overlay drives it (see `docs/DISPLAY.md`).
- Touch axis orientation (calibration tool provided).
- WPA3-SAE association on this Pi's firmware level (`scripts/check-wpa3.sh`).
- Real Ookla CLI output on aarch64 Trixie matching the jsonl schema the parser expects
  (schema used: `type` in `testStart`, `ping`, `download`, `upload`, `result`, `log`;
  `bandwidth` in bytes/s). The parser keeps the raw JSON in `events.jsonl`, so any
  mismatch is auditable; `scripts/install-speedtest.sh` checks `--help` for
  `--interface` and `jsonl`.
