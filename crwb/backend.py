"""Backend seam between the worker and the real network.  The worker only calls
these five methods, so tests swap in a fake."""

from __future__ import annotations

from typing import Callable

from . import netinfo, probes, speedtest, wifi
from .netinfo import WifiState
from .probes import ConnectivityResult
from .shell import CancelToken
from .speedtest import SpeedtestResult


class Backend:
    def wifi_state(self, interface: str, ssid: str) -> WifiState:
        raise NotImplementedError

    def connect(self, cfg: dict, interface: str, cancel: CancelToken) -> tuple[bool, WifiState, str]:
        raise NotImplementedError

    def route_check(self, interface: str, targets: list[str], want_ipv6: bool) -> tuple[bool, str]:
        raise NotImplementedError

    def connectivity(self, cfg: dict, interface: str, state: WifiState,
                     cancel: CancelToken) -> ConnectivityResult:
        raise NotImplementedError

    def speedtest(self, cfg: dict, interface: str, cancel: CancelToken,
                  on_phase: Callable[[str], None]) -> SpeedtestResult:
        raise NotImplementedError

    def speedtest_version(self, cfg: dict) -> str:
        return ""


class RealBackend(Backend):
    def wifi_state(self, interface: str, ssid: str) -> WifiState:
        return netinfo.wifi_state(interface, ssid)

    def connect(self, cfg, interface, cancel):
        return wifi.connect(cfg, interface, cancel)

    def route_check(self, interface: str, targets: list[str], want_ipv6: bool) -> tuple[bool, str]:
        """The kernel's default path for test traffic must be the Wi-Fi interface.
        This is what the speedtest child process (and its DNS lookups) will use."""
        problems: list[str] = []
        checked = 0
        for target in targets:
            info = netinfo.route_for(target)
            if "error" in info:
                problems.append(f"{target}: {info['error']}")
                continue
            checked += 1
            dev = info.get("dev", "")
            if dev != interface:
                problems.append(f"{target} routes via {dev or '?'} not {interface}")
        if want_ipv6:
            info = netinfo.route_for("2606:4700:4700::1111")
            if "error" not in info and info.get("dev", "") != interface:
                problems.append(f"IPv6 default via {info.get('dev', '?')} not {interface}")
        if checked == 0:
            return False, "no usable default route: " + "; ".join(problems)[:140]
        if problems:
            return False, "; ".join(problems)[:160]
        return True, ""

    def connectivity(self, cfg, interface, state, cancel):
        return probes.run_connectivity(cfg, interface, state.gateway, state.dhcp_dns, cancel)

    def speedtest(self, cfg, interface, cancel, on_phase):
        return speedtest.run(cfg, interface, cancel, on_phase)

    def speedtest_version(self, cfg: dict) -> str:
        return speedtest.tool_version(cfg["speedtest"]["command"])
