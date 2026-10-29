"""Configuration loading, defaults, validation and small in-place edits.

The config file is TOML (read with the standard library tomllib).  Every key has a
default so a partial file is fine.  Nothing secret lives here: the Wi-Fi password is
stored only in NetworkManager's keyfile store.
"""

from __future__ import annotations

import copy
import os
import re
import tomllib
from typing import Any

DEFAULT_CONFIG_PATH = "/etc/pingaroo/config.toml"

DEFAULTS: dict[str, Any] = {
    "app": {
        "label": "Config A",
        "labels": ["Config A", "Config B", "APN test"],
        "autostart": False,
        "interval_seconds": 300,
    },
    "wifi": {
        "ssid": "Kangaroo Labs",
        "connection_name": "Kangaroo Labs",
        "home_profile": "",
        "home_ssid": "",
        "interface": "",
        "connect_timeout_seconds": 45,
        "poll_seconds": 1.0,
    },
    "connectivity": {
        "ping_targets": ["1.1.1.1", "8.8.8.8"],
        "ping_count": 4,
        "ping_interval_seconds": 0.3,
        "ping_timeout_seconds": 8,
        "gateway_ping_count": 2,
        "dns_servers": ["1.1.1.1", "8.8.8.8"],
        "dns_timeout_seconds": 3,
        "https_timeout_seconds": 8,
        "total_timeout_seconds": 30,
        "https_primary": {
            "url": "https://detectportal.firefox.com/success.txt",
            "expect_status": 200,
            "expect_body": "success",
        },
        "https_fallback": {
            "url": "https://connectivitycheck.gstatic.com/generate_204",
            "expect_status": 204,
            "expect_body": "",
        },
    },
    "speedtest": {
        "command": "speedtest",
        "server_id": 0,
        "timeout_seconds": 180,
        "bind_interface": True,
        "extra_args": [],
    },
    "router": {
        "type": "none",
        "base_url": "http://192.168.1.1",
        "username": "attadmin",
        "password_file": "/etc/pingaroo/router.secret",
        "login_path": "",
        "login_method": "form",
        "login_fields": {"username": "username", "password": "password"},
        "status_paths": [],
        "timeout_seconds": 8,
        "fields": {},
    },
    "display": {
        "framebuffer": "",
        "width": 480,
        "height": 320,
        "sleep_seconds": 120,
        "font": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "font_bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "console_graphics_mode": "auto",
        "refresh_seconds": 1.0,
        "splash_seconds": 4.0,
    },
    "touch": {
        "device": "",
        "device_name_match": "ADS7846",
        "swap_xy": True,
        "invert_x": False,
        "invert_y": True,
        "x_min": 200,
        "x_max": 3900,
        "y_min": 200,
        "y_max": 3900,
        "debounce_ms": 350,
        "tap_max_move_px": 60,
    },
    "logging": {
        "directory": "/var/lib/pingaroo",
        "runtime_directory": "/run/pingaroo",
        "csv_max_bytes": 5_000_000,
        "jsonl_max_bytes": 20_000_000,
        "retention_files": 10,
        "low_disk_mb": 100,
    },
}

STATUSES = (
    "SUCCESS",
    "WIFI_FAILED",
    "ROUTE_FAILED",
    "CONNECTIVITY_FAILED",
    "SPEEDTEST_FAILED",
    "SPEEDTEST_TIMEOUT",
    "INTERRUPTED",
)


class ConfigError(Exception):
    pass


def _deep_merge(base: dict, override: dict, path: str = "") -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        full = f"{path}.{key}" if path else key
        if key not in base:
            raise ConfigError(f"Unknown config key: {full}")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"Config key {full} must be a table")
            out[key] = _deep_merge(base[key], value, full)
        else:
            out[key] = value
    return out


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def validate(cfg: dict) -> None:
    app = cfg["app"]
    _require(isinstance(app["label"], str) and app["label"].strip(), "app.label must be a non-empty string")
    _require(isinstance(app["labels"], list) and all(isinstance(x, str) for x in app["labels"]),
             "app.labels must be a list of strings")
    _require(isinstance(app["autostart"], bool), "app.autostart must be true or false")
    _require(isinstance(app["interval_seconds"], (int, float)) and app["interval_seconds"] >= 0,
             "app.interval_seconds must be a number >= 0")

    wifi = cfg["wifi"]
    _require(isinstance(wifi["ssid"], str) and wifi["ssid"], "wifi.ssid must be set")
    _require(isinstance(wifi["connection_name"], str) and wifi["connection_name"],
             "wifi.connection_name must be set")
    _require(wifi["connect_timeout_seconds"] > 0, "wifi.connect_timeout_seconds must be > 0")

    con = cfg["connectivity"]
    _require(isinstance(con["ping_targets"], list) and 1 <= len(con["ping_targets"]) <= 4,
             "connectivity.ping_targets must list 1 to 4 IP addresses")
    _require(1 <= con["ping_count"] <= 20, "connectivity.ping_count must be 1..20")
    _require(con["total_timeout_seconds"] >= 5, "connectivity.total_timeout_seconds must be >= 5")
    for name in ("https_primary", "https_fallback"):
        ep = con[name]
        _require(isinstance(ep, dict) and str(ep.get("url", "")).startswith("https://"),
                 f"connectivity.{name}.url must start with https://")

    sp = cfg["speedtest"]
    _require(isinstance(sp["server_id"], int) and sp["server_id"] >= 0,
             "speedtest.server_id must be an integer (0 = automatic)")
    _require(sp["timeout_seconds"] >= 30, "speedtest.timeout_seconds must be >= 30")

    rt = cfg["router"]
    _require(rt["type"] in ("none", "http_json"), "router.type must be none or http_json")
    _require(isinstance(rt["status_paths"], list), "router.status_paths must be a list")
    _require(isinstance(rt["fields"], dict), "router.fields must be a table of column = json.path")

    disp = cfg["display"]
    _require(disp["width"] > 0 and disp["height"] > 0, "display size must be positive")
    _require(disp["sleep_seconds"] >= 0, "display.sleep_seconds must be >= 0 (0 disables sleep)")
    _require(disp["console_graphics_mode"] in ("auto", "always", "never"),
             "display.console_graphics_mode must be auto, always or never")

    log = cfg["logging"]
    _require(log["retention_files"] >= 1, "logging.retention_files must be >= 1")


def load(path: str | None = None) -> dict:
    """Load config from path (or default), merged over DEFAULTS and validated."""
    path = path or os.environ.get("PINGAROO_CONFIG", DEFAULT_CONFIG_PATH)
    data: dict = {}
    if os.path.exists(path):
        with open(path, "rb") as fh:
            try:
                data = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{path}: {exc}") from exc
    cfg = _deep_merge(DEFAULTS, data)
    validate(cfg)
    cfg["_path"] = path
    return cfg


def read_label(cfg: dict) -> str:
    """Re-read only app.label from disk so label changes apply at the next cycle
    without restarting.  Falls back to the loaded value on any error."""
    path = cfg.get("_path")
    try:
        if path and os.path.exists(path):
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
            label = data.get("app", {}).get("label")
            if isinstance(label, str) and label.strip():
                return label.strip()
    except Exception:
        pass
    return str(cfg["app"]["label"])


def _toml_repr(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_repr(v) for v in value) + "]"
    raise ConfigError(f"Cannot encode {value!r} to TOML")


def set_value(path: str, section: str, key: str, value: Any) -> None:
    """Set [section] key = value in the TOML file, editing in place.

    Only top-level sections and simple scalar/list values are supported, which is all
    the CLI needs.  Comments and other lines are preserved.
    """
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    section_re = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    new_line = f"{key} = {_toml_repr(value)}"

    in_section = False
    section_start = None
    replaced = False
    insert_at = None
    for idx, line in enumerate(lines):
        m = section_re.match(line)
        if m:
            if in_section and insert_at is None:
                insert_at = idx
            in_section = m.group("name").strip() == section
            if in_section:
                section_start = idx
            continue
        if in_section and key_re.match(line):
            lines[idx] = new_line
            replaced = True
            break
    if not replaced:
        if section_start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"[{section}]")
            lines.append(new_line)
        else:
            if insert_at is None:
                insert_at = len(lines)
            # back up over trailing blank lines so the key stays inside the section
            while insert_at > section_start + 1 and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines.insert(insert_at, new_line)
    text = "\n".join(lines) + "\n"
    # validate before writing
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Refusing to write invalid TOML: {exc}") from exc
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
