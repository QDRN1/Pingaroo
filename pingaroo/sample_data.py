"""Synthetic snapshots for `pingaroo preview` and layout tests."""

from __future__ import annotations

import copy


def _base() -> dict:
    return {
        "running": False, "phase": "Idle", "current_cycle_id": "", "session": None, "session_elapsed_s": None,
        "wifi": {"interface": "wlan0", "connected": False, "associated": False, "ssid": "", "bssid": "", "ip": "",
                 "prefix": None, "gateway": "", "dns": [], "dhcp_dns": [], "ipv6": [], "signal_dbm": None,
                 "freq_mhz": None, "channel": None, "band": "", "tx_rate_mbps": None, "rx_rate_mbps": None},
        "wifi_age_s": 1.0,
        "internet": {"ok": None, "summary": "", "time_iso": "", "time_mono": None, "checking": False},
        "internet_age_s": None, "last_row": None, "last_row_age_s": None, "last_speed": None,
        "last_speed_age_s": None, "speed_is_previous": False, "last_connectivity": None, "last_error": "",
        "countdown_s": None, "worker_alive": True, "worker_crash": "", "speedtest_version": "1.2.0.84",
        "interface": "wlan0", "interface_note": "auto-detected", "label": "Config A", "clock_synced": True,
        "live": None, "last_router": None, "switching": "", "switch_note": "", "interval_s": 300,
    }


def sample_snapshot() -> dict[str, dict]:
    idle = _base()

    connected = _base()
    connected["wifi"].update(connected=True, associated=True, ssid="Kangaroo Labs", bssid="a4:3e:51:12:34:56",
                             ip="192.168.8.142", prefix=24, gateway="192.168.8.1", dhcp_dns=["192.168.8.1"],
                             signal_dbm=-52, freq_mhz=5180, channel=36, band="5 GHz", tx_rate_mbps=390.0,
                             rx_rate_mbps=433.3)
    session = {"id": "20261029T141501-9f3a", "start_iso": "2026-10-29T14:15:01-05:00", "start_mono": 0.0,
               "cycles": 12, "successes": 10, "failures": 2, "interrupted": 0, "consecutive_failures": 0,
               "bytes_down": 612_000_000, "bytes_up": 148_000_000, "incomplete_byte_cycles": 1}
    speed = {"status": "SUCCESS", "error": "", "partial": False, "download_mbps": 87.4, "upload_mbps": 21.9,
             "latency_ms": 41.3, "jitter_ms": 3.2, "packet_loss_pct": 0.0, "download_bytes": 52_000_000,
             "upload_bytes": 12_300_000, "public_ip": "166.137.12.44", "result_url": "https://www.speedtest.net/result/c/abc",
             "server_id": 18531, "server_name": "US Internet", "server_location": "Minneapolis, MN, United States",
             "server_host": "speedtest.usinternet.com:8080", "server_pinned": False, "tool_interface": "wlan0",
             "tool_version": "1.2.0.84", "duration_s": 34.2, "phases_seen": ["testStart", "ping", "download", "upload"]}
    conn = {"internet_ok": True, "icmp_ok": True, "dns_ok": True, "https_ok": True,
            "gateway": {"target": "192.168.8.1", "sent": 2, "received": 2, "loss_pct": 0.0, "avg_ms": 2.1, "error": ""},
            "pings": [{"target": "1.1.1.1", "sent": 4, "received": 4, "loss_pct": 0.0, "avg_ms": 38.9, "error": ""},
                      {"target": "8.8.8.8", "sent": 4, "received": 3, "loss_pct": 25.0, "avg_ms": 44.1, "error": ""}],
            "dns": [{"name": "detectportal.firefox.com", "server": "192.168.8.1", "addresses": ["34.107.221.82"], "ms": 23.0, "error": ""}],
            "https": [{"url": "https://detectportal.firefox.com/success.txt", "ok": True, "status": 200, "ms": 310.0, "error": ""}],
            "summary": "OK", "duration_s": 4.1}
    row = {"end_time": "2026-10-29T15:12:44-05:00", "status": "SUCCESS", "error": ""}

    waiting = copy.deepcopy(connected)
    waiting.update(running=True, phase="Waiting", session=session, session_elapsed_s=3463, last_speed=speed,
                   last_speed_age_s=184, last_row=row, last_row_age_s=184, last_connectivity=conn, countdown_s=116,
                   internet_age_s=190)
    waiting["internet"].update(ok=True, summary="OK", time_iso="2026-10-29T15:12:10-05:00")

    testing = copy.deepcopy(waiting)
    testing.update(phase="Speed Test: Download", countdown_s=None, speed_is_previous=True,
                   live={"phase": "download", "mbps": 58.3, "progress": 0.62, "latency_ms": 41.3, "t": 0.0})

    checking = copy.deepcopy(waiting)
    checking.update(phase="Checking Internet", countdown_s=None)
    checking["internet"].update(checking=True)

    failed = copy.deepcopy(waiting)
    failed_row = {"end_time": "2026-10-29T15:17:50-05:00", "status": "CONNECTIVITY_FAILED",
                  "error": "HTTPS failed: HTTP 302 (redirect, captive portal?)"}
    failed.update(last_row=failed_row, last_row_age_s=20, countdown_s=280,
                  last_error="CONNECTIVITY_FAILED: HTTPS failed: HTTP 302 (redirect, captive portal?)")
    failed["internet"].update(ok=False, summary="HTTPS failed: HTTP 302 (redirect, captive portal?)")
    failed["internet_age_s"] = 25
    failed["session"] = dict(session, cycles=13, failures=3, consecutive_failures=1)

    waiting["last_router"] = {"cell_carrier": "AT&T", "cell_technology": "5G NSA", "cell_band": "B66", "cell_earfcn": 66986,
                              "cell_cell_id": 12345678, "cell_pci": 301, "cell_tac": 24601, "cell_rsrp": -94, "cell_rsrq": -11,
                              "cell_sinr": 12, "cell_5g_band": "n77", "cell_5g_rsrp": -88, "cell_5g_sinr": 18, "cell_apn": "broadband",
                              "cell_ip": "10.44.1.7", "cell_imei": "353282162196635", "cell_firmware": "NCM1120_1.2.3"}

    disconnected = copy.deepcopy(idle)
    disconnected.update(running=True, phase="Connecting", session=dict(session, cycles=1, successes=0, failures=0))

    return {"idle": idle, "success_waiting": waiting, "testing": testing, "checking": checking,
            "failed": failed, "connecting": disconnected}


def sample_storage(name: str) -> dict:
    st = {"last_error": "", "free_mb": 4210.5, "low_disk": False, "rows_written": 12, "events_written": 96}
    if name == "failed":
        st = dict(st, last_error="write failed results.csv: No space left on device", low_disk=True, free_mb=12.0)
    return st


def sample_extra() -> dict:
    return {"log_dir": "/var/lib/pingaroo", "config_path": "/etc/pingaroo/config.toml", "version": "1.0.0",
            "display": "/dev/fb1 480x320 16bpp (fb_ili9486)", "touch": "/dev/input/event0 (matched name 'ADS7846 Touchscreen')",
            "sleep": "120 s, method blank"}
