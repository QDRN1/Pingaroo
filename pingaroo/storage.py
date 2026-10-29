"""Local result storage: CSV summary rows, JSONL detail events, rotation, disk checks.

Every write is flushed and fsynced.  A failed write sets `last_error`, which the UI
shows as a storage failure; it never claims success silently.
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
from dataclasses import dataclass

from . import clock, netinfo
from .router import ROUTER_COLUMNS

CSV_COLUMNS = [
    "session_id", "cycle_id", "label", "start_time", "end_time", "duration_s", "status",
    "failed_stage", "error", "next_attempt_time", "clock_synced", "interface", "ssid", "bssid",
    "local_ip", "gateway", "signal_dbm", "band", "channel", "link_rate_mbps",
    "dns_ok", "dns_server", "dns_ms", "https_ok", "https_url", "https_status", "https_ms",
    "icmp_ok", "gateway_ping_ms", "gateway_ping_loss_pct",
    "ping1_target", "ping1_avg_ms", "ping1_loss_pct", "ping2_target", "ping2_avg_ms", "ping2_loss_pct",
    "download_mbps", "upload_mbps", "latency_ms", "jitter_ms", "packet_loss_pct",
    "download_bytes", "upload_bytes", "public_ip", "server_id", "server_name", "server_location",
    "server_host", "result_url", "speedtest_version", "speedtest_partial",
] + ROUTER_COLUMNS + ["router_error"]


@dataclass
class StorageStatus:
    last_error: str = ""
    last_error_time: str = ""
    free_mb: float | None = None
    low_disk: bool = False
    rows_written: int = 0
    events_written: int = 0


class Storage:
    def __init__(self, cfg: dict) -> None:
        lcfg = cfg["logging"]
        self.directory = lcfg["directory"]
        self.csv_path = os.path.join(self.directory, "results.csv")
        self.jsonl_path = os.path.join(self.directory, "events.jsonl")
        self.csv_max = int(lcfg["csv_max_bytes"])
        self.jsonl_max = int(lcfg["jsonl_max_bytes"])
        self.retention = int(lcfg["retention_files"])
        self.low_disk_mb = float(lcfg["low_disk_mb"])
        self.status = StorageStatus()
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- helpers
    def ensure_directory(self) -> bool:
        try:
            os.makedirs(self.directory, exist_ok=True)
            return True
        except OSError as exc:
            self._fail(f"cannot create {self.directory}: {exc.strerror}")
            return False

    def _fail(self, msg: str) -> None:
        self.status.last_error = msg[:160]
        self.status.last_error_time = clock.iso()

    def check_disk(self) -> None:
        free = netinfo.disk_free_mb(self.directory if os.path.isdir(self.directory) else "/")
        self.status.free_mb = round(free, 1) if free is not None else None
        self.status.low_disk = free is not None and free < self.low_disk_mb

    def _rotate(self, path: str, max_bytes: int) -> None:
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if size < max_bytes:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base, ext = os.path.splitext(path)
        os.replace(path, f"{base}.{stamp}{ext}")
        prefix = os.path.basename(base) + "."
        rotated = sorted(
            f for f in os.listdir(self.directory)
            if f.startswith(prefix) and f.endswith(ext) and f != os.path.basename(path))
        for old in rotated[:-self.retention] if len(rotated) > self.retention else []:
            try:
                os.remove(os.path.join(self.directory, old))
            except OSError:
                pass

    def _append(self, path: str, text: str, max_bytes: int, header: str | None = None) -> bool:
        with self._lock:
            if not self.ensure_directory():
                return False
            try:
                self._rotate(path, max_bytes)
                new_file = not os.path.exists(path) or os.path.getsize(path) == 0
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                try:
                    payload = ((header or "") if new_file else "") + text
                    data = payload.encode("utf-8")
                    written = os.write(fd, data)
                    if written != len(data):
                        raise OSError("short write")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if self.status.last_error:
                    self.status.last_error = ""
                return True
            except OSError as exc:
                self._fail(f"write failed {os.path.basename(path)}: {exc.strerror or exc}")
                return False
            finally:
                self.check_disk()

    # ------------------------------------------------------------------ writes
    def write_event(self, event: str, **fields) -> bool:
        record = {"time": clock.iso(), "event": event}
        record.update(fields)
        line = json.dumps(record, default=str, separators=(",", ":")) + "\n"
        ok = self._append(self.jsonl_path, line, self.jsonl_max)
        if ok:
            self.status.events_written += 1
        return ok

    def write_result_row(self, row: dict) -> bool:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        clean = {k: ("" if v is None else v) for k, v in row.items()}
        for col in CSV_COLUMNS:
            clean.setdefault(col, "")
        writer.writerow(clean)
        header_buf = io.StringIO()
        csv.DictWriter(header_buf, fieldnames=CSV_COLUMNS).writeheader()
        ok = self._append(self.csv_path, buf.getvalue(), self.csv_max, header=header_buf.getvalue())
        if ok:
            self.status.rows_written += 1
        return ok

    # ------------------------------------------------------------------- reads
    def read_last_rows(self, n: int = 10) -> list[dict]:
        if not os.path.exists(self.csv_path):
            return []
        with open(self.csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        return rows[-n:]

    # ---------------------------------------------- in-progress cycle marker
    @property
    def marker_path(self) -> str:
        return os.path.join(self.directory, "cycle_in_progress.json")

    def write_marker(self, data: dict) -> None:
        try:
            self.ensure_directory()
            tmp = self.marker_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.marker_path)
        except OSError:
            pass

    def clear_marker(self) -> None:
        try:
            os.remove(self.marker_path)
        except OSError:
            pass

    def read_marker(self) -> dict | None:
        try:
            with open(self.marker_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None


class StateFile:
    """Atomic JSON snapshot in the runtime directory for `pingaroo status`."""

    def __init__(self, runtime_dir: str) -> None:
        self.dir = runtime_dir
        self.path = os.path.join(runtime_dir, "state.json")
        self.control_path = os.path.join(runtime_dir, "control")

    def write(self, data: dict) -> None:
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, default=str)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def read(self) -> dict | None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def pop_control(self) -> str:
        """Return and remove a pending control command ('start'/'stop'), or ''."""
        try:
            with open(self.control_path, encoding="utf-8") as fh:
                cmd = fh.read().strip().lower()
            os.remove(self.control_path)
            return cmd
        except OSError:
            return ""

    def push_control(self, cmd: str) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.control_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(cmd + "\n")
        os.replace(tmp, self.control_path)
