"""Fake backend for worker tests: scripted outcomes, no network."""

from __future__ import annotations

import copy
import time
from typing import Callable

from pingaroo.backend import Backend
from pingaroo.config import DEFAULTS
from pingaroo.netinfo import WifiState
from pingaroo.probes import ConnectivityResult, DnsResult, HttpsResult, PingResult
from pingaroo.shell import CancelToken
from pingaroo.speedtest import SpeedtestResult


def test_config(**overrides) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    cfg["app"]["interval_seconds"] = 1
    cfg["wifi"]["connect_timeout_seconds"] = 2
    cfg["speedtest"]["timeout_seconds"] = 5
    cfg["_path"] = None
    for dotted, value in overrides.items():
        section, key = dotted.split(".")
        cfg[section][key] = value
    return cfg


def connected_state(ip="192.168.8.142", ssid="Kangaroo Labs", bssid="aa:bb:cc:dd:ee:01") -> WifiState:
    return WifiState(interface="wlan0", nm_state="100 (connected)", connection=ssid, connected=True, associated=True,
                     ssid=ssid, bssid=bssid, ip=ip, prefix=24, gateway="192.168.8.1", dhcp_dns=["192.168.8.1"],
                     signal_dbm=-55, freq_mhz=5180, channel=36, band="5 GHz", tx_rate_mbps=390.0)


def disconnected_state() -> WifiState:
    return WifiState(interface="wlan0", nm_state="30 (disconnected)")


def good_connectivity(icmp=True, https=True, dns=True) -> ConnectivityResult:
    c = ConnectivityResult()
    c.gateway = PingResult("192.168.8.1", 2, 2, 0.0, 1.0, 2.0, 3.0)
    if icmp:
        c.pings = [PingResult("1.1.1.1", 4, 4, 0.0, 30.0, 35.0, 40.0), PingResult("8.8.8.8", 4, 3, 25.0, 31.0, 36.0, 41.0)]
    else:
        c.pings = [PingResult("1.1.1.1", 4, 0, 100.0, error="100% loss"), PingResult("8.8.8.8", 4, 0, 100.0, error="100% loss")]
    if dns:
        c.dns = [DnsResult("detectportal.firefox.com", "192.168.8.1", ["34.107.221.82"], 20.0)]
    else:
        c.dns = [DnsResult("detectportal.firefox.com", "192.168.8.1", [], None, "timeout")]
    if https:
        c.https = [HttpsResult("https://detectportal.firefox.com/success.txt", True, 200, 300.0)]
    else:
        c.https = [HttpsResult("https://detectportal.firefox.com/success.txt", False, 302, 200.0, error="HTTP 302 (redirect, captive portal?)"),
                   HttpsResult("https://connectivitycheck.gstatic.com/generate_204", False, None, None, error="timeout")]
    c.icmp_ok = any(p.ok for p in c.pings)
    c.dns_ok = any(d.ok for d in c.dns)
    c.https_ok = any(h.ok for h in c.https)
    c.internet_ok = c.https_ok
    c.summary = "OK" if c.internet_ok else "HTTPS failed: " + "; ".join(h.error for h in c.https)
    return c


def good_speed() -> SpeedtestResult:
    return SpeedtestResult(status="SUCCESS", download_mbps=87.4, upload_mbps=21.9, latency_ms=41.3, jitter_ms=3.2,
                           packet_loss_pct=0.0, download_bytes=52_000_000, upload_bytes=12_300_000,
                           public_ip="166.137.12.44", result_url="https://www.speedtest.net/result/c/x",
                           server_id=18531, server_name="US Internet", server_location="Minneapolis, MN, United States",
                           server_host="h:8080", tool_interface="wlan0", tool_version="1.2.0.84", duration_s=30.0)


class FakeBackend(Backend):
    """Each hook is a callable or a list of values consumed per call (last repeats)."""

    def __init__(self) -> None:
        self.states: list[WifiState] = [connected_state()]
        self.connect_results: list[tuple[bool, WifiState, str]] = [(True, connected_state(), "")]
        self.route_results: list[tuple[bool, str]] = [(True, "")]
        self.conn_results: list[ConnectivityResult] = [good_connectivity()]
        self.speed_results: list[SpeedtestResult] = [good_speed()]
        self.speed_delay = 0.0
        self.connect_delay = 0.0
        self.conn_delay = 0.0
        self.calls: list[str] = []
        self.speed_phase_hook: Callable | None = None
        self.speed_started = 0
        self.speed_active = 0
        self.max_speed_active = 0

    @staticmethod
    def _next(seq: list):
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]

    def wifi_state(self, interface, ssid):
        self.calls.append("wifi_state")
        return copy.deepcopy(self._next(self.states))

    def connect(self, cfg, interface, cancel: CancelToken, ssid=None, profile=None):
        self.calls.append("connect")
        if self.connect_delay and cancel.wait(self.connect_delay):
            return False, disconnected_state(), "interrupted"
        ok, st, err = self._next(self.connect_results)
        return ok, copy.deepcopy(st), err

    def route_check(self, interface, targets, want_ipv6):
        self.calls.append("route_check")
        return self._next(self.route_results)

    def connectivity(self, cfg, interface, state, cancel):
        self.calls.append("connectivity")
        if self.conn_delay and cancel.wait(self.conn_delay):
            pass
        return copy.deepcopy(self._next(self.conn_results))

    def speedtest(self, cfg, interface, cancel, on_phase):
        self.calls.append("speedtest")
        self.speed_started += 1
        self.speed_active += 1
        self.max_speed_active = max(self.max_speed_active, self.speed_active)
        try:
            on_phase("ping", {"latency_ms": 41.3, "progress": 1.0})
            on_phase("download", {"mbps": 50.0, "progress": 0.5})
            if self.speed_delay and cancel.wait(self.speed_delay):
                return SpeedtestResult(status="INTERRUPTED", error="interrupted by Stop Testing", partial=True,
                                       download_mbps=12.0, download_bytes=1000)
            on_phase("upload", {"mbps": 20.0, "progress": 0.5})
            return copy.deepcopy(self._next(self.speed_results))
        finally:
            self.speed_active -= 1

    def speedtest_version(self, cfg):
        return "fake"

    def router_info(self, cfg):
        self.calls.append("router_info")
        return {"cell_rsrp": -95, "cell_band": "n77", "cell_apn": "broadband"}

    def activate_profile(self, profile, interface, timeout):
        self.calls.append(f"activate:{profile}")
        return True, ""


def wait_until(pred: Callable[[], bool], timeout: float = 5.0, step: float = 0.02) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()
