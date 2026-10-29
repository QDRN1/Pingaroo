"""One bounded connection attempt through NetworkManager."""

from __future__ import annotations

import time

from . import netinfo, shell
from .shell import CancelToken

ERROR_HINTS = (
    ("No network with SSID", "SSID not found (router Wi-Fi off or out of range)"),
    ("Secrets were required", "authentication failed (rejected credentials or WPA3 unsupported)"),
    ("secrets were required", "authentication failed (rejected credentials or WPA3 unsupported)"),
    ("802-11-wireless-security.psk", "authentication failed (password rejected)"),
    ("Timeout", "connection attempt timed out"),
    ("ip-config-unavailable", "associated but no DHCP lease"),
    ("IP configuration could not be reserved", "associated but no DHCP lease"),
    ("Connection activation failed", "activation failed"),
    ("not found", "connection profile not found (run setup-wifi.sh)"),
    ("unknown connection", "connection profile not found (run setup-wifi.sh)"),
    ("device is not managed", "interface not managed by NetworkManager"),
    ("Device or resource busy", "interface busy"),
)


def explain_nmcli_error(text: str) -> str:
    first = ""
    for line in text.strip().splitlines():
        if line.strip():
            first = line.strip()
            break
    for needle, hint in ERROR_HINTS:
        if needle.lower() in text.lower():
            return f"{hint}: {first}"[:200] if first and hint not in first else hint
    return (first or "nmcli failed")[:200]


def connect(cfg: dict, interface: str, cancel: CancelToken,
            timeout: float | None = None) -> tuple[bool, netinfo.WifiState, str]:
    """Bring the configured profile up on `interface` and wait for an IPv4 address.

    Returns (ok, state, error).  A single attempt, bounded by timeout.
    """
    wcfg = cfg["wifi"]
    timeout = timeout or float(wcfg["connect_timeout_seconds"])
    ssid = wcfg["ssid"]
    name = wcfg["connection_name"]
    deadline = time.monotonic() + timeout

    if name not in netinfo.connection_profiles():
        return False, netinfo.wifi_state(interface, ssid), \
            f"NetworkManager profile '{name}' not found (run scripts/setup-wifi.sh)"

    nm_wait = max(5, int(timeout) - 2)
    res = shell.run(["nmcli", "-w", str(nm_wait), "connection", "up", name, "ifname", interface],
                    timeout=timeout, cancel=cancel)
    if cancel.cancelled:
        return False, netinfo.wifi_state(interface, ssid), "interrupted"
    error = ""
    if not res.ok:
        if res.timed_out:
            error = "connection attempt timed out"
        else:
            error = explain_nmcli_error(res.stderr or res.stdout or res.error or "")
    # Even on nmcli failure, poll briefly: NM sometimes reports late.
    while True:
        state = netinfo.wifi_state(interface, ssid)
        if state.connected:
            return True, state, ""
        if cancel.cancelled:
            return False, state, "interrupted"
        if time.monotonic() >= deadline or not res.ok:
            break
        cancel.wait(float(wcfg["poll_seconds"]))
    if not error:
        if state.associated and state.ssid != ssid:
            error = f"associated to wrong SSID '{state.ssid}'"
        elif state.associated and not state.ip:
            error = "associated but no IPv4 address (DHCP)"
        else:
            error = "not connected after attempt"
    return False, state, error
