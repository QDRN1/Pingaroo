"""The test loop.  Runs in its own thread; the UI only reads snapshots and calls
start_session()/stop_session()."""

from __future__ import annotations

import copy
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

from . import clock, config as config_mod
from .backend import Backend
from .netinfo import WifiState
from .shell import CancelToken
from .storage import Storage


class Phase:
    IDLE = "Idle"
    CONNECTING = "Connecting"
    CHECKING = "Checking Internet"
    SPEEDTEST = "Running Speed Test"
    SPEEDTEST_LATENCY = "Speed Test: Latency"
    SPEEDTEST_DOWNLOAD = "Speed Test: Download"
    SPEEDTEST_UPLOAD = "Speed Test: Upload"
    WAITING = "Waiting"
    STOPPING = "Stopping"


@dataclass
class Session:
    id: str
    start_iso: str
    start_mono: float
    cycles: int = 0
    successes: int = 0
    failures: int = 0
    interrupted: int = 0
    consecutive_failures: int = 0
    bytes_down: int = 0
    bytes_up: int = 0
    incomplete_byte_cycles: int = 0


@dataclass
class InternetStatus:
    ok: bool | None = None          # None = never checked
    summary: str = ""
    time_iso: str = ""
    time_mono: float | None = None
    checking: bool = False


class WorkerState:
    """All mutable state the UI needs, behind one lock."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.running = False
        self.phase = Phase.IDLE
        self.current_cycle_id = ""
        self.session: Session | None = None
        self.wifi = WifiState()
        self.wifi_time_mono: float | None = None
        self.internet = InternetStatus()
        self.last_row: dict | None = None
        self.last_row_mono: float | None = None
        self.last_speed: dict | None = None          # SpeedtestResult.to_dict()
        self.last_speed_mono: float | None = None
        self.speed_is_previous = False               # True while a new test runs
        self.last_connectivity: dict | None = None
        self.last_error = ""
        self.next_start_mono: float | None = None
        self.worker_alive = True
        self.worker_crash = ""
        self.speedtest_version = ""
        self.interface = ""
        self.interface_note = ""
        self.label = ""
        self.label_override = ""
        self.clock_synced: bool | None = None

    def snapshot(self) -> dict:
        with self.lock:
            now = time.monotonic()
            return {
                "running": self.running,
                "phase": self.phase,
                "current_cycle_id": self.current_cycle_id,
                "session": asdict(self.session) if self.session else None,
                "session_elapsed_s": (now - self.session.start_mono) if self.session else None,
                "wifi": self.wifi.to_dict(),
                "wifi_age_s": (now - self.wifi_time_mono) if self.wifi_time_mono is not None else None,
                "internet": asdict(self.internet),
                "internet_age_s": (now - self.internet.time_mono) if self.internet.time_mono is not None else None,
                "last_row": copy.deepcopy(self.last_row),
                "last_row_age_s": (now - self.last_row_mono) if self.last_row_mono is not None else None,
                "last_speed": copy.deepcopy(self.last_speed),
                "last_speed_age_s": (now - self.last_speed_mono) if self.last_speed_mono is not None else None,
                "speed_is_previous": self.speed_is_previous,
                "last_connectivity": copy.deepcopy(self.last_connectivity),
                "last_error": self.last_error,
                "countdown_s": max(0.0, self.next_start_mono - now) if self.next_start_mono else None,
                "worker_alive": self.worker_alive,
                "worker_crash": self.worker_crash,
                "speedtest_version": self.speedtest_version,
                "interface": self.interface,
                "interface_note": self.interface_note,
                "label": self.label_override or self.label,
                "clock_synced": self.clock_synced,
            }


class Worker:
    def __init__(self, cfg: dict, backend: Backend, storage: Storage, state: WorkerState | None = None) -> None:
        self.cfg = cfg
        self.backend = backend
        self.storage = storage
        self.state = state or WorkerState()
        self.cancel = CancelToken()
        self._wake = threading.Event()
        self._shutdown = False
        self._thread = threading.Thread(target=self._main, name="crwb-worker", daemon=True)
        self._start_lock = threading.Lock()
        self.cycle_running = False
        self.state.label = config_mod.read_label(cfg)

    # ------------------------------------------------------------- public API
    def start(self) -> None:
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def start_session(self) -> bool:
        """Begin a new session and run the first cycle immediately.  Returns False if
        already running (duplicate start)."""
        with self._start_lock:
            with self.state.lock:
                if self.state.running:
                    return False
                sid = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]
                self.state.session = Session(id=sid, start_iso=clock.iso(), start_mono=time.monotonic())
                self.state.running = True
                self.state.last_error = ""
                self.state.next_start_mono = None
                self.cancel.reset()
            self.storage.write_event("session_start", session_id=sid,
                                     label=self.state.label_override or config_mod.read_label(self.cfg),
                                     interval_seconds=self.cfg["app"]["interval_seconds"])
            self._wake.set()
            return True

    def stop_session(self) -> bool:
        with self.state.lock:
            if not self.state.running:
                return False
            self.state.running = False
            self.state.phase = Phase.STOPPING
            self.state.next_start_mono = None
        self.cancel.cancel()
        self._wake.set()
        return True

    def shutdown(self) -> None:
        self._shutdown = True
        self.stop_session()
        self.cancel.cancel()
        self._wake.set()

    def set_label_override(self, label: str) -> None:
        with self.state.lock:
            self.state.label_override = label

    def refresh_wifi(self) -> WifiState:
        st = self.backend.wifi_state(self.state.interface, self.cfg["wifi"]["ssid"])
        with self.state.lock:
            self.state.wifi = st
            self.state.wifi_time_mono = time.monotonic()
        return st

    # ------------------------------------------------------------ main loop
    def _main(self) -> None:
        try:
            while not self._shutdown:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                if self._shutdown:
                    break
                with self.state.lock:
                    running = self.state.running
                if not running:
                    with self.state.lock:
                        if self.state.phase == Phase.STOPPING:
                            self.state.phase = Phase.IDLE
                    continue
                self._run_session()
        except Exception as exc:  # pragma: no cover - last-resort guard
            with self.state.lock:
                self.state.worker_alive = False
                self.state.worker_crash = f"{type(exc).__name__}: {exc}"[:160]
                self.state.running = False
                self.state.phase = Phase.IDLE
            self.storage.write_event("worker_crash", error=self.state.worker_crash)
            raise

    def _run_session(self) -> None:
        interval = float(self.cfg["app"]["interval_seconds"])
        while True:
            with self.state.lock:
                if not self.state.running or self._shutdown:
                    break
            end_mono = self._run_cycle()
            with self.state.lock:
                if not self.state.running or self._shutdown:
                    break
                self.state.phase = Phase.WAITING
                self.state.next_start_mono = end_mono + interval
            # Wait exactly `interval` seconds after the attempt ended, Stop-aware.
            self.cancel.wait(max(0.0, end_mono + interval - time.monotonic()))
            with self.state.lock:
                self.state.next_start_mono = None
                if not self.state.running:
                    break
        session = self.state.session
        with self.state.lock:
            self.state.phase = Phase.IDLE
            self.state.running = False
            self.state.next_start_mono = None
            self.state.internet.checking = False
        if session:
            self.storage.write_event("session_stop", session_id=session.id, cycles=session.cycles,
                                     successes=session.successes, failures=session.failures,
                                     interrupted=session.interrupted)

    # ----------------------------------------------------------------- cycle
    def _set_phase(self, phase: str) -> None:
        with self.state.lock:
            if self.state.running:
                self.state.phase = phase

    def _run_cycle(self) -> float:
        """Run one attempt.  Returns the monotonic time the attempt ended."""
        st = self.state
        cfg = self.cfg
        ssid = cfg["wifi"]["ssid"]
        iface = st.interface
        t0 = time.monotonic()
        start_iso = clock.iso()
        with st.lock:
            session = st.session
            session.cycles += 1
            cycle_id = f"{session.id}-{session.cycles:04d}"
            st.current_cycle_id = cycle_id
            label = st.label_override or config_mod.read_label(cfg)
            st.label = label
            st.clock_synced = clock.clock_synced()
            st.last_error = ""
        self.cycle_running = True
        row: dict = {
            "session_id": session.id, "cycle_id": cycle_id, "label": label,
            "start_time": start_iso, "status": "", "failed_stage": "", "error": "",
            "clock_synced": st.clock_synced, "interface": iface,
        }
        detail: dict = {}
        self.storage.write_marker({"cycle_id": cycle_id, "start_time": start_iso, "label": label,
                                   "session_id": session.id})
        self.storage.write_event("cycle_start", cycle_id=cycle_id, session_id=session.id, label=label,
                                 interface=iface, clock_synced=st.clock_synced)

        def finish(status: str, stage: str, error: str, speed: dict | None = None) -> float:
            end_mono = time.monotonic()
            row.update({"status": status, "failed_stage": stage if status != "SUCCESS" else "",
                        "error": error[:200], "end_time": clock.iso(),
                        "duration_s": round(end_mono - t0, 1)})
            with st.lock:
                if st.running and status != "INTERRUPTED":
                    row["next_attempt_time"] = clock.iso_in(float(cfg["app"]["interval_seconds"]))
                else:
                    row["next_attempt_time"] = ""
                if status == "SUCCESS":
                    session.successes += 1
                    session.consecutive_failures = 0
                elif status == "INTERRUPTED":
                    session.interrupted += 1
                else:
                    session.failures += 1
                    session.consecutive_failures += 1
                if speed is not None:
                    if speed.get("download_bytes") is not None and speed.get("upload_bytes") is not None:
                        session.bytes_down += int(speed["download_bytes"])
                        session.bytes_up += int(speed["upload_bytes"])
                    else:
                        session.incomplete_byte_cycles += 1
                        if speed.get("download_bytes"):
                            session.bytes_down += int(speed["download_bytes"])
                        if speed.get("upload_bytes"):
                            session.bytes_up += int(speed["upload_bytes"])
                st.last_row = dict(row)
                st.last_row_mono = end_mono
                st.last_error = "" if status == "SUCCESS" else f"{status}: {error}"[:120]
                st.current_cycle_id = ""
                st.speed_is_previous = False
            ok_event = self.storage.write_event("cycle_end", **row, detail=detail)
            ok_csv = self.storage.write_result_row(row)
            self.storage.clear_marker()
            if not (ok_event and ok_csv):
                with st.lock:
                    st.last_error = f"LOG WRITE FAILED: {self.storage.status.last_error}"[:120]
            self.cycle_running = False
            return end_mono

        def interrupted(stage: str, speed: dict | None = None) -> float:
            return finish("INTERRUPTED", stage, "stopped by user during " + stage, speed)

        # ---- stage 1-3: Wi-Fi association + IP
        self._set_phase(Phase.CONNECTING)
        wstate = self.backend.wifi_state(iface, ssid)
        self._store_wifi(wstate)
        if not wstate.connected:
            if self.cancel.cancelled:
                return interrupted("wifi")
            ok, wstate, err = self.backend.connect(cfg, iface, self.cancel)
            self._store_wifi(wstate)
            if self.cancel.cancelled:
                return interrupted("wifi")
            if not ok:
                detail["wifi"] = wstate.to_dict()
                self.storage.write_event("wifi_result", cycle_id=cycle_id, ok=False, error=err)
                self._fill_network(row, wstate)
                return finish("WIFI_FAILED", "wifi", err or "Wi-Fi connection failed")
        self._fill_network(row, wstate)
        detail["wifi"] = wstate.to_dict()
        self.storage.write_event("wifi_result", cycle_id=cycle_id, ok=True, ssid=wstate.ssid,
                                 ip=wstate.ip, bssid=wstate.bssid, signal_dbm=wstate.signal_dbm)

        # ---- route check: the default path for test traffic must be the Wi-Fi
        route_ok, route_err = self.backend.route_check(iface, list(cfg["connectivity"]["ping_targets"]),
                                                       bool(wstate.ipv6))
        detail["route"] = {"ok": route_ok, "error": route_err}
        self.storage.write_event("route_result", cycle_id=cycle_id, ok=route_ok, error=route_err)
        if not route_ok:
            return finish("ROUTE_FAILED", "route", route_err)
        if self.cancel.cancelled:
            return interrupted("route")

        # ---- stage 4-5: connectivity
        self._set_phase(Phase.CHECKING)
        with st.lock:
            st.internet.checking = True
        conn = self.backend.connectivity(cfg, iface, wstate, self.cancel)
        conn_d = conn.to_dict()
        detail["connectivity"] = conn_d
        with st.lock:
            st.internet = InternetStatus(ok=conn.internet_ok, summary=conn.summary, time_iso=clock.iso(),
                                         time_mono=time.monotonic(), checking=False)
            st.last_connectivity = conn_d
        self._fill_connectivity(row, conn)
        self.storage.write_event("connectivity_result", cycle_id=cycle_id, ok=conn.internet_ok,
                                 summary=conn.summary, icmp_ok=conn.icmp_ok, dns_ok=conn.dns_ok,
                                 https_ok=conn.https_ok)
        if self.cancel.cancelled:
            return interrupted("connectivity")
        if not conn.internet_ok:
            return finish("CONNECTIVITY_FAILED", "connectivity", conn.summary)

        # ---- stage 6: speed test, with path re-checks around it
        pre = self.backend.wifi_state(iface, ssid)
        self._store_wifi(pre)
        if not pre.connected or pre.ip != wstate.ip:
            return finish("ROUTE_FAILED", "speedtest_precheck",
                          f"Wi-Fi changed before speed test (ssid={pre.ssid or '-'}, ip={pre.ip or 'none'})")
        route_ok, route_err = self.backend.route_check(iface, list(cfg["connectivity"]["ping_targets"]),
                                                       bool(pre.ipv6))
        if not route_ok:
            return finish("ROUTE_FAILED", "speedtest_precheck", route_err)
        self._set_phase(Phase.SPEEDTEST)
        with st.lock:
            st.speed_is_previous = True

        def on_phase(kind: str) -> None:
            self._set_phase({"ping": Phase.SPEEDTEST_LATENCY, "download": Phase.SPEEDTEST_DOWNLOAD,
                             "upload": Phase.SPEEDTEST_UPLOAD}.get(kind, Phase.SPEEDTEST))

        sres = self.backend.speedtest(cfg, iface, self.cancel, on_phase)
        speed = sres.to_dict()
        raw = speed.pop("raw", None)
        detail["speedtest"] = speed
        detail["speedtest_raw"] = raw
        post = self.backend.wifi_state(iface, ssid)
        self._store_wifi(post)
        self._fill_speed(row, sres)
        with st.lock:
            st.last_speed = speed
            st.last_speed_mono = time.monotonic()
            st.speed_is_previous = False
        status, error = sres.status, sres.error
        if status == "INTERRUPTED" or self.cancel.cancelled:
            return interrupted("speedtest", speed)
        changed = []
        if not post.connected:
            changed.append("Wi-Fi dropped")
        elif post.ip != wstate.ip:
            changed.append(f"IP changed {wstate.ip}->{post.ip}")
        if post.bssid and wstate.bssid and post.bssid != wstate.bssid:
            detail["bssid_changed"] = f"{wstate.bssid}->{post.bssid}"
        if sres.tool_interface and sres.tool_interface != iface:
            changed.append(f"tool used interface {sres.tool_interface}")
        self.storage.write_event("speedtest_result", cycle_id=cycle_id, status=status, error=error,
                                 download_mbps=sres.download_mbps, upload_mbps=sres.upload_mbps,
                                 latency_ms=sres.latency_ms, server_id=sres.server_id,
                                 partial=sres.partial, connection_changed=changed)
        if changed:
            note = "connection changed during speed test: " + ", ".join(changed)
            if status == "SUCCESS":
                return finish("ROUTE_FAILED", "speedtest", note, speed)
            return finish(status, "speedtest", f"{error} ({note})", speed)
        if status == "SUCCESS":
            return finish("SUCCESS", "", "", speed)
        return finish(status, "speedtest", error or status, speed)

    # --------------------------------------------------------------- helpers
    def _store_wifi(self, wstate: WifiState) -> None:
        with self.state.lock:
            self.state.wifi = wstate
            self.state.wifi_time_mono = time.monotonic()

    @staticmethod
    def _fill_network(row: dict, w: WifiState) -> None:
        row.update({"ssid": w.ssid, "bssid": w.bssid, "local_ip": w.ip, "gateway": w.gateway,
                    "signal_dbm": w.signal_dbm, "band": w.band, "channel": w.channel,
                    "link_rate_mbps": w.tx_rate_mbps})

    @staticmethod
    def _fill_connectivity(row: dict, c) -> None:
        good_dns = next((d for d in c.dns if d.ok), None)
        good_https = next((h for h in c.https if h.ok), c.https[-1] if c.https else None)
        row.update({
            "dns_ok": c.dns_ok, "dns_server": good_dns.server if good_dns else "",
            "dns_ms": good_dns.ms if good_dns else None,
            "https_ok": c.https_ok, "https_url": good_https.url if good_https else "",
            "https_status": good_https.status if good_https else None,
            "https_ms": good_https.ms if good_https else None,
            "icmp_ok": c.icmp_ok,
            "gateway_ping_ms": c.gateway.avg_ms if c.gateway else None,
            "gateway_ping_loss_pct": c.gateway.loss_pct if c.gateway else None,
        })
        for idx in (1, 2):
            if len(c.pings) >= idx:
                p = c.pings[idx - 1]
                row[f"ping{idx}_target"] = p.target
                row[f"ping{idx}_avg_ms"] = p.avg_ms
                row[f"ping{idx}_loss_pct"] = p.loss_pct

    @staticmethod
    def _fill_speed(row: dict, s) -> None:
        row.update({
            "download_mbps": s.download_mbps, "upload_mbps": s.upload_mbps, "latency_ms": s.latency_ms,
            "jitter_ms": s.jitter_ms, "packet_loss_pct": s.packet_loss_pct,
            "download_bytes": s.download_bytes, "upload_bytes": s.upload_bytes, "public_ip": s.public_ip,
            "server_id": s.server_id, "server_name": s.server_name, "server_location": s.server_location,
            "server_host": s.server_host, "result_url": s.result_url, "speedtest_version": s.tool_version,
            "speedtest_partial": s.partial,
        })


class NetMonitor(threading.Thread):
    """Refreshes live Wi-Fi/IP state every few seconds so the screen tracks the link
    between cycles.  It never probes the internet."""

    def __init__(self, worker: Worker, period: float = 3.0) -> None:
        super().__init__(name="crwb-netmon", daemon=True)
        self.worker = worker
        self.period = period
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.worker.cycle_running:
                    self.worker.refresh_wifi()
            except Exception:
                pass
            self._stop.wait(self.period)

    def stop(self) -> None:
        self._stop.set()
