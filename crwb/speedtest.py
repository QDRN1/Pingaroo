"""Run the Ookla Speedtest CLI with structured (jsonl) output and a hard timeout.

Units, per Ookla's JSON schema: `bandwidth` is bytes per second, `bytes` is bytes
transferred, latency/jitter are milliseconds, `packetLoss` is percent.  Mbps is
computed as bandwidth * 8 / 1_000_000 and the raw values are kept in the JSONL log so
the conversion can be audited.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import shell
from .shell import CancelToken

_version_cache: dict = {}


@dataclass
class SpeedtestResult:
    status: str = "SPEEDTEST_FAILED"       # SUCCESS | SPEEDTEST_FAILED | SPEEDTEST_TIMEOUT | INTERRUPTED
    error: str = ""
    partial: bool = False
    download_mbps: float | None = None
    upload_mbps: float | None = None
    latency_ms: float | None = None        # idle latency reported by the tool
    jitter_ms: float | None = None
    packet_loss_pct: float | None = None
    download_bytes: int | None = None
    upload_bytes: int | None = None
    public_ip: str = ""
    result_url: str = ""
    server_id: int | None = None
    server_name: str = ""
    server_location: str = ""
    server_host: str = ""
    server_pinned: bool = False
    tool_interface: str = ""
    tool_version: str = ""
    raw: dict | None = None
    duration_s: float = 0.0
    phases_seen: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def tool_version(command: str) -> str:
    if command in _version_cache:
        return _version_cache[command]
    res = shell.run([command, "--version"], timeout=5)
    version = ""
    if res.ok or res.stdout:
        first = (res.stdout or res.stderr).strip().splitlines()
        if first:
            m = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", first[0])
            version = m.group(1) if m else first[0][:40]
    elif res.error:
        version = ""
    _version_cache[command] = version
    return version


def bytes_per_second_to_mbps(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    return round(v * 8 / 1_000_000, 2)


def _num(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def apply_result_json(res: SpeedtestResult, data: dict) -> None:
    """Fill a result from an Ookla `type: result` object."""
    dl = data.get("download") or {}
    ul = data.get("upload") or {}
    ping = data.get("ping") or {}
    server = data.get("server") or {}
    iface = data.get("interface") or {}
    res.download_mbps = bytes_per_second_to_mbps(dl.get("bandwidth"))
    res.upload_mbps = bytes_per_second_to_mbps(ul.get("bandwidth"))
    res.download_bytes = _int(dl.get("bytes"))
    res.upload_bytes = _int(ul.get("bytes"))
    res.latency_ms = _num(ping.get("latency"))
    res.jitter_ms = _num(ping.get("jitter"))
    res.packet_loss_pct = _num(data.get("packetLoss"))
    res.public_ip = str(iface.get("externalIp") or "")
    res.tool_interface = str(iface.get("name") or "")
    res.result_url = str((data.get("result") or {}).get("url") or "")
    res.server_id = _int(server.get("id"))
    res.server_name = str(server.get("name") or "")
    res.server_location = ", ".join(x for x in (server.get("location"), server.get("country")) if x)
    res.server_host = str(server.get("host") or "")
    res.raw = data


def apply_progress_json(res: SpeedtestResult, data: dict) -> None:
    """Keep the latest partial numbers from a progress line."""
    kind = data.get("type")
    if kind in ("download", "upload"):
        block = data.get(kind) or {}
        mbps = bytes_per_second_to_mbps(block.get("bandwidth"))
        nbytes = _int(block.get("bytes"))
        if kind == "download":
            res.download_mbps, res.download_bytes = mbps, nbytes
        else:
            res.upload_mbps, res.upload_bytes = mbps, nbytes
        res.partial = True
    if kind in ("ping", "download", "upload", "testStart"):
        ping = data.get("ping") or {}
        if ping.get("latency") is not None:
            res.latency_ms = _num(ping.get("latency"))
            res.jitter_ms = _num(ping.get("jitter"))
        server = data.get("server") or {}
        if server and res.server_id is None:
            res.server_id = _int(server.get("id"))
            res.server_name = str(server.get("name") or "")
            res.server_location = ", ".join(x for x in (server.get("location"), server.get("country")) if x)
            res.server_host = str(server.get("host") or "")


def build_command(cfg: dict, interface: str) -> list[str]:
    scfg = cfg["speedtest"]
    cmd = [scfg["command"], "--accept-license", "--accept-gdpr", "--format=jsonl", "--progress=yes"]
    if scfg["bind_interface"] and interface:
        cmd += ["--interface", interface]
    if int(scfg["server_id"]) > 0:
        cmd += ["--server-id", str(int(scfg["server_id"]))]
    cmd += [str(x) for x in scfg.get("extra_args", [])]
    return cmd


def run(cfg: dict, interface: str, cancel: CancelToken,
        on_phase: Callable[[str], None] | None = None) -> SpeedtestResult:
    scfg = cfg["speedtest"]
    timeout = float(scfg["timeout_seconds"])
    res = SpeedtestResult(server_pinned=int(scfg["server_id"]) > 0)
    res.tool_version = tool_version(scfg["command"])
    cmd = build_command(cfg, interface)
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
    except FileNotFoundError:
        res.error = f"speedtest command not found: {scfg['command']} (run scripts/install-speedtest.sh)"
        return res
    except OSError as exc:
        res.error = f"cannot start speedtest: {exc}"
        return res

    lines: list[str] = []
    err_lines: list[str] = []
    final: dict | None = None
    log_errors: list[str] = []
    lock = threading.Lock()

    def _read_stdout():
        nonlocal final
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                with lock:
                    lines.append(line)
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                kind = data.get("type")
                with lock:
                    if kind == "result":
                        final = data
                    elif kind == "log":
                        if str(data.get("level", "")).lower() in ("error", "critical", "warning"):
                            log_errors.append(str(data.get("message", ""))[:160])
                    else:
                        apply_progress_json(res, data)
                        if kind and kind not in res.phases_seen:
                            res.phases_seen.append(kind)
                if on_phase and kind in ("ping", "download", "upload"):
                    on_phase(kind)
        except ValueError:
            pass

    def _read_stderr():
        try:
            for line in proc.stderr:
                err_lines.append(line.rstrip())
        except ValueError:
            pass

    t_out = threading.Thread(target=_read_stdout, daemon=True)
    t_err = threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    deadline = t0 + timeout
    outcome = "exit"
    while proc.poll() is None:
        if cancel.cancelled:
            outcome = "cancel"
            shell.kill_process_group(proc)
            break
        if time.monotonic() >= deadline:
            outcome = "timeout"
            shell.kill_process_group(proc)
            break
        time.sleep(0.1)
    t_out.join(timeout=3)
    t_err.join(timeout=3)
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except Exception:
            pass
    res.duration_s = round(time.monotonic() - t0, 1)

    with lock:
        if outcome == "cancel":
            res.status = "INTERRUPTED"
            res.error = "interrupted by Stop Testing"
            return res
        if outcome == "timeout":
            res.status = "SPEEDTEST_TIMEOUT"
            res.error = f"speed test exceeded {int(timeout)} s and was terminated"
            return res
        if final is not None:
            apply_result_json(res, final)
            res.partial = False
            if res.download_mbps is None and res.upload_mbps is None:
                res.status = "SPEEDTEST_FAILED"
                res.error = "result lacked download and upload bandwidth"
                return res
            res.status = "SUCCESS"
            return res
        # No result object: classify the failure.
        res.status = "SPEEDTEST_FAILED"
        detail = ""
        if log_errors:
            detail = log_errors[-1]
        elif err_lines:
            detail = " ".join(l for l in err_lines if l.strip())[-160:]
        elif lines:
            detail = f"malformed output (no result object): {lines[-1][:100]}"
        else:
            detail = f"no output (exit code {proc.returncode})"
        if res.server_pinned and re.search(r"server|configuration|connect", detail, re.I):
            detail = f"pinned server {int(scfg['server_id'])} unavailable: {detail}"
        if proc.returncode not in (0, None) and "exit" not in detail:
            detail = f"{detail} (exit code {proc.returncode})"
        res.error = detail[:200]
        return res
