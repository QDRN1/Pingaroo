"""Cellular/modem data from the router's own management interface.

The Pi is only a Wi-Fi client; it cannot see the cellular modem.  Everything here
comes from the router's local web API, when one is configured.  `type = "none"`
(default) records nothing and never invents values.

`type = "http_json"`: optional login (form POST or HTTP basic), then GET each of
`status_paths`; the responses are merged and `fields` maps CSV columns to dotted
JSON paths, e.g.  cell_rsrp = "mobile.signal.rsrp".  The raw JSON is stored in the
JSONL detail for every cycle so unmapped values are not lost.
"""

from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

ROUTER_COLUMNS = [
    "cell_carrier", "cell_technology", "cell_band", "cell_bandwidth_mhz", "cell_earfcn", "cell_cell_id",
    "cell_pci", "cell_tac", "cell_rsrp", "cell_rsrq", "cell_sinr", "cell_rssi", "cell_apn", "cell_ip",
    "cell_imei", "cell_imsi", "cell_iccid", "cell_firmware", "cell_uptime", "cell_5g_band", "cell_5g_rsrp",
    "cell_5g_rsrq", "cell_5g_sinr", "cell_5g_pci",
]


def read_secret(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def dig(data, path: str):
    """Dotted path lookup with list indexes: 'a.b.0.c'.  Returns None if missing."""
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            else:
                return None
        else:
            return None
    return cur


_SECRET_KEY = re.compile(r"(password|passwd|psk|passphrase|secret|^key$|_key$|token|sessionid)", re.I)
_SSID_WITH_PSK = re.compile(r"^(?P<ssid>[^/]+)/.+\((?P<mode>[^)]*)\)$")


def redact(obj):
    """Strip credentials from router JSON before it is logged.  Password-like keys
    become 'REDACTED'; NCM1120 '<ssid>/<psk>(mode)' strings keep only the SSID."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SECRET_KEY.search(str(k)):
                out[k] = "REDACTED" if v not in ("", None) else v
            elif isinstance(v, str) and str(k).upper().endswith("_WLAN"):
                m = _SSID_WITH_PSK.match(v)
                out[k] = f"{m.group('ssid')} ({m.group('mode')})" if m else v
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def _coerce(value):
    if isinstance(value, (int, float)) or value is None:
        return value
    text = str(value).strip()
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:dBm|dB|MHz|Mbps|%)?", text)
    if m:
        num = float(m.group(1))
        return int(num) if num.is_integer() else num
    return text


class HttpJsonRouter:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg["router"]
        self.base = self.cfg["base_url"].rstrip("/")
        self.timeout = float(self.cfg["timeout_seconds"])
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.headers = {"User-Agent": "pingaroo/1.0", "Accept": "application/json, */*"}

    def _request(self, path: str, data: bytes | None = None, extra: dict | None = None) -> tuple[int, bytes]:
        url = path if path.startswith("http") else self.base + "/" + path.lstrip("/")
        req = urllib.request.Request(url, data=data, headers={**self.headers, **(extra or {})})
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read() if exc.fp else b""

    def login(self) -> str:
        c = self.cfg
        password = read_secret(c["password_file"])
        if c.get("password_hash") == "sha256" and password and not (len(password) == 64 and all(ch in "0123456789abcdef" for ch in password.lower())):
            password = hashlib.sha256(password.encode()).hexdigest()
        if c["login_method"] == "basic":
            token = base64.b64encode(f"{c['username']}:{password}".encode()).decode()
            self.headers["Authorization"] = f"Basic {token}"
            return ""
        if not c["login_path"]:
            return ""
        if not password:
            return f"router password file {c['password_file']} missing or empty"
        fields = dict(c["login_fields"])
        payload = {fields.get("username", "username"): c["username"], fields.get("password", "password"): password}
        for k, v in fields.items():
            if k not in ("username", "password"):
                payload[k] = v
        if c["login_method"] == "ncm" and "token" not in payload:
            payload["token"] = ""
        if c["login_method"] in ("json", "ncm"):
            body = json.dumps(payload).encode()
            status, raw = self._request(c["login_path"], body,
                                        {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"})
        else:
            body = urllib.parse.urlencode(payload).encode()
            status, raw = self._request(c["login_path"], body, {"Content-Type": "application/x-www-form-urlencoded"})
        if status >= 400:
            return f"login HTTP {status}"
        # Routers that answer 200 with a JSON success/failure flag (e.g. AT&T NCM1120).
        try:
            jd = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(jd, dict):
                if jd.get("Success") is False or str(jd.get("Login", "1")) in ("0", "False", "false"):
                    return "login rejected (wrong password?)"
        except ValueError:
            pass
        # Some firmwares return a token in JSON; keep it as a bearer header if present.
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            token = data.get("token") or data.get("access_token") or data.get("sessionId") if isinstance(data, dict) else None
            if token:
                self.headers["Authorization"] = f"Bearer {token}"
        except ValueError:
            pass
        return ""

    def fetch(self) -> dict:
        out: dict = {}
        login_err = self.login()
        if login_err:
            # Not fatal: some firmwares (AT&T NCM1120) serve the status reads without a
            # session.  Record it and continue; only an empty fetch is an error.
            out["login_error"] = login_err
        merged: dict = {}
        raw_pages: dict = {}
        for path in self.cfg["status_paths"]:
            status, raw = self._request(path)
            text = raw.decode("utf-8", "replace")
            if status >= 400:
                out.setdefault("errors", []).append(f"{path}: HTTP {status}")
                continue
            try:
                data = json.loads(text)
            except ValueError:
                out.setdefault("errors", []).append(f"{path}: not JSON ({text[:60]!r})")
                continue
            data = redact(data)
            raw_pages[path] = data
            if isinstance(data, dict):
                merged.update(data)
            else:
                merged[path] = data
        if not raw_pages:
            parts = out.get("errors", [])
            if out.get("login_error"):
                parts = [out["login_error"]] + parts
            out["error"] = "; ".join(parts) or "no status pages configured"
            return out
        for column, path in self.cfg["fields"].items():
            if path.startswith("page:"):
                page, _, sub = path[5:].partition(":")
                value = dig(raw_pages.get(page), sub) if page in raw_pages else None
            else:
                value = dig(merged, path)
            out[column] = _coerce(value)
        out["raw"] = raw_pages
        if out.get("errors"):
            out["error"] = "; ".join(out["errors"])
        return out


def fetch(cfg: dict) -> dict:
    kind = cfg["router"]["type"]
    if kind == "none":
        return {}
    if kind == "http_json":
        try:
            return HttpJsonRouter(cfg).fetch()
        except Exception as exc:  # network errors, bad config: a result, not a crash
            return {"error": f"{type(exc).__name__}: {exc}"[:160]}
    return {"error": f"unknown router type {kind}"}
