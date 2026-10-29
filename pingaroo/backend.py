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

    def connect(self, cfg: dict, interface: str, cancel: CancelToken,
                ssid: str | None = None, profile: str | None = None) -> tuple[bool, WifiState, str]:
        raise NotImplementedError

    def route_check(self, interface: str, targets: list[str], want_ipv6: bool) -> tuple[bool, str]:
        raise NotImplementedError

    def connectivity(self, cfg: dict, interface: str, state: WifiState,
                     cancel: CancelToken) -> ConnectivityResult:
        raise NotImplementedError

    def speedtest(self, cfg: dict, interface: str, cancel: CancelToken,
                  on_phase: Callable[[str, dict], None]) -> SpeedtestResult:
        raise NotImplementedError

    def speedtest_version(self, cfg: dict) -> str:
        return ""

    def router_info(self, cfg: dict) -> dict:
        """Cellular/modem data from the router's own interface, {} if not configured."""
        return {}

    def activate_profile(self, profile: str, interface: str, timeout: float) -> tuple[bool, str]:
        raise NotImplementedError


class RealBackend(Backend):
    def wifi_state(self, interface: str, ssid: str) -> WifiState:
        return netinfo.wifi_state(interface, ssid)

    def connect(self, cfg, interface, cancel, ssid=None, profile=None):
        return wifi.connect(cfg, interface, cancel, ssid=ssid, profile=profile)

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

    def router_info(self, cfg: dict) -> dict:
        from . import router
        return router.fetch(cfg)

    def activate_profile(self, profile: str, interface: str, timeout: float) -> tuple[bool, str]:
        from . import shell
        res = shell.run(["nmcli", "-w", str(int(timeout)), "connection", "up", profile, "ifname", interface],
                        timeout=timeout + 5)
        if res.ok:
            return True, ""
        return False, wifi.explain_nmcli_error(res.stderr or res.stdout or res.error or "")
