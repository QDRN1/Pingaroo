"""Read-only network introspection via nmcli, iw and ip.  No state changes here."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field

from . import shell


@dataclass
class WifiState:
    interface: str = ""
    nm_state: str = ""           # e.g. "100 (connected)"
    connection: str = ""         # active NM connection name
    connected: bool = False      # associated to the expected SSID with an IPv4 address
    associated: bool = False     # associated to any SSID
    ssid: str = ""
    bssid: str = ""
    ip: str = ""                 # IPv4 without prefix
    prefix: int | None = None
    gateway: str = ""
    dns: list[str] = field(default_factory=list)
    dhcp_dns: list[str] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    signal_dbm: int | None = None
    freq_mhz: int | None = None
    channel: int | None = None
    band: str = ""
    tx_rate_mbps: float | None = None
    rx_rate_mbps: float | None = None
    error: str = ""
    kind: str = "wifi"           # "wifi" | "ethernet": what sort of link this describes
    carrier: bool | None = None  # ethernet only: is a cable connected to a live port

    def to_dict(self) -> dict:
        return asdict(self)


def _nmcli_terse(args: list[str], timeout: float = 8) -> dict[str, list[str]]:
    """Run nmcli -t ... and return {FIELD: [values]} with escaped colons restored."""
    res = shell.run(["nmcli", "-t", "-m", "multiline"] + args, timeout=timeout)
    out: dict[str, list[str]] = {}
    if not res.ok:
        return out
    for line in res.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.replace("\\:", ":")
        key = re.sub(r"\[\d+\]$", "", key.strip())
        out.setdefault(key, []).append(value.strip())
    return out


def list_wifi_interfaces() -> list[str]:
    res = shell.run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"], timeout=8)
    found: list[str] = []
    if res.ok:
        for line in res.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "wifi":
                found.append(parts[0])
    if not found:
        try:
            for name in sorted(os.listdir("/sys/class/net")):
                if os.path.isdir(f"/sys/class/net/{name}/wireless"):
                    found.append(name)
        except OSError:
            pass
    return found


def pick_interface(configured: str) -> tuple[str, str]:
    """Return (interface, note).  Configured name wins; otherwise the first Wi-Fi
    device NetworkManager knows about."""
    if configured:
        return configured, "configured"
    found = list_wifi_interfaces()
    if not found:
        return "", "no Wi-Fi interface detected"
    note = "auto-detected" if len(found) == 1 else f"auto-detected (first of {', '.join(found)})"
    return found[0], note


def list_ethernet_interfaces() -> list[str]:
    res = shell.run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"], timeout=8)
    found: list[str] = []
    if res.ok:
        for line in res.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "ethernet":
                found.append(parts[0])
    if not found:
        try:
            for name in sorted(os.listdir("/sys/class/net")):
                base = f"/sys/class/net/{name}"
                # a physical wired port: has a backing device, is not wireless
                if os.path.exists(f"{base}/device") and not os.path.isdir(f"{base}/wireless") \
                        and _read_sys(f"{base}/type") == "1":
                    found.append(name)
        except OSError:
            pass
    return found


def pick_ethernet_interface(configured: str) -> tuple[str, str]:
    if configured:
        return configured, "configured"
    found = list_ethernet_interfaces()
    if not found:
        return "", "no Ethernet interface detected"
    note = "auto-detected" if len(found) == 1 else f"auto-detected (first of {', '.join(found)})"
    return found[0], note


def link_mode(cfg: dict) -> str:
    """The configured setting: "auto", "wifi" or "ethernet"."""
    return str((cfg.get("link") or {}).get("mode", "auto"))


def pick_test_interface(cfg: dict) -> tuple[str, str]:
    """The interface the app starts on: the wired port when link.mode is "ethernet",
    otherwise the Wi-Fi radio ("auto" moves to the cable per cycle when it is live)."""
    if link_mode(cfg) == "ethernet":
        return pick_ethernet_interface(cfg["link"].get("ethernet_interface", ""))
    return pick_interface(cfg["wifi"]["interface"])


def _read_sys(path: str) -> str:
    try:
        with open(path, "r", encoding="ascii") as fh:
            return fh.read().strip()
    except (OSError, ValueError):
        return ""


def iw_link(interface: str) -> dict:
    """Parse `iw dev <if> link`."""
    info: dict = {}
    res = shell.run(["iw", "dev", interface, "link"], timeout=5)
    if not res.ok:
        return info
    text = res.stdout
    if text.strip().startswith("Not connected"):
        return info
    m = re.search(r"Connected to ([0-9a-fA-F:]{17})", text)
    if m:
        info["bssid"] = m.group(1).lower()
    m = re.search(r"^\s*SSID:\s*(.*)$", text, re.M)
    if m:
        info["ssid"] = m.group(1).strip()
    m = re.search(r"^\s*freq:\s*(\d+)", text, re.M)
    if m:
        info["freq_mhz"] = int(m.group(1))
    m = re.search(r"^\s*signal:\s*(-?\d+)\s*dBm", text, re.M)
    if m:
        info["signal_dbm"] = int(m.group(1))
    m = re.search(r"^\s*tx bitrate:\s*([\d.]+)\s*MBit/s", text, re.M)
    if m:
        info["tx_rate_mbps"] = float(m.group(1))
    m = re.search(r"^\s*rx bitrate:\s*([\d.]+)\s*MBit/s", text, re.M)
    if m:
        info["rx_rate_mbps"] = float(m.group(1))
    return info


def freq_to_channel(freq: int | None) -> tuple[int | None, str]:
    if not freq:
        return None, ""
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5, "2.4 GHz"
    if freq == 2484:
        return 14, "2.4 GHz"
    if 5160 <= freq <= 5885:
        return (freq - 5000) // 5, "5 GHz"
    if 5955 <= freq <= 7115:
        return (freq - 5950) // 5, "6 GHz"
    return None, f"{freq} MHz"


def wifi_state(interface: str, expected_ssid: str) -> WifiState:
    st = WifiState(interface=interface)
    if not interface:
        st.error = "no interface"
        return st
    fields = _nmcli_terse(["-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,"
                           "IP4.DNS,IP6.ADDRESS,DHCP4.OPTION", "device", "show", interface])
    if not fields:
        st.error = "nmcli returned nothing (interface missing?)"
    _fill_ip(st, fields)
    link = iw_link(interface)
    st.ssid = link.get("ssid", "")
    st.bssid = link.get("bssid", "")
    st.signal_dbm = link.get("signal_dbm")
    st.freq_mhz = link.get("freq_mhz")
    st.channel, st.band = freq_to_channel(st.freq_mhz)
    st.tx_rate_mbps = link.get("tx_rate_mbps")
    st.rx_rate_mbps = link.get("rx_rate_mbps")
    st.associated = bool(st.bssid)
    st.connected = st.associated and st.ssid == expected_ssid and bool(st.ip)
    return st


def _fill_ip(st: WifiState, fields: dict[str, list[str]]) -> None:
    st.nm_state = (fields.get("GENERAL.STATE") or [""])[0]
    st.connection = (fields.get("GENERAL.CONNECTION") or [""])[0]
    for addr in fields.get("IP4.ADDRESS", []):
        ip, _, prefix = addr.partition("/")
        if ip:
            st.ip = ip
            st.prefix = int(prefix) if prefix.isdigit() else None
            break
    st.gateway = (fields.get("IP4.GATEWAY") or [""])[0]
    st.dns = [d for d in fields.get("IP4.DNS", []) if d]
    st.ipv6 = [a for a in fields.get("IP6.ADDRESS", []) if a and not a.startswith("fe80")]
    for opt in fields.get("DHCP4.OPTION", []):
        if opt.startswith("domain_name_servers"):
            _, _, val = opt.partition("=")
            st.dhcp_dns = val.strip().split()


def ethernet_state(interface: str) -> WifiState:
    """The wired port, in the same shape as wifi_state so the test loop can use either.
    connected = cable live and an IPv4 address.  There is no SSID, BSSID or signal."""
    st = WifiState(interface=interface, kind="ethernet", band="Ethernet")
    if not interface:
        st.error = "no interface"
        return st
    carrier = _read_sys(f"/sys/class/net/{interface}/carrier")
    st.carrier = (carrier == "1") if carrier in ("0", "1") else None
    if st.carrier is False:
        return st            # no cable: nothing more to ask, and this runs every few seconds
    fields = _nmcli_terse(["-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,"
                           "IP4.DNS,IP6.ADDRESS,DHCP4.OPTION", "device", "show", interface])
    if not fields:
        st.error = "nmcli returned nothing (interface missing?)"
    _fill_ip(st, fields)
    speed = _read_sys(f"/sys/class/net/{interface}/speed")
    if speed.lstrip("-").isdigit() and int(speed) > 0:
        st.tx_rate_mbps = float(speed)
        st.rx_rate_mbps = float(speed)
    # carrier unknown (file unreadable): fall back to NetworkManager's word
    st.associated = st.carrier if st.carrier is not None else st.nm_state.startswith("100")
    st.connected = bool(st.associated) and bool(st.ip)
    return st


def route_for(target: str) -> dict:
    """`ip -j route get <target>` as a dict (dev, gateway, prefsrc) or {} on failure."""
    family = "-6" if ":" in target else "-4"
    res = shell.run(["ip", "-j", family, "route", "get", target], timeout=5)
    if not res.ok:
        return {"error": (res.stderr or res.error or "ip route get failed").strip()}
    try:
        data = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return {"error": "unparseable ip output"}
    return data[0] if data else {"error": "no route"}


def connection_profiles() -> list[str]:
    res = shell.run(["nmcli", "-t", "-f", "NAME", "connection", "show"], timeout=8)
    if not res.ok:
        return []
    return [line.replace("\\:", ":") for line in res.stdout.splitlines() if line]


def disk_free_mb(path: str) -> float | None:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    return st.f_bavail * st.f_frsize / 1_000_000
