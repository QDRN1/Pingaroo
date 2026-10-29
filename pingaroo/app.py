"""Application wiring: single instance lock, worker, net monitor, display, touch and
the UI controller (screens, sleep/wake, debounce, state file, control file)."""

from __future__ import annotations

import fcntl
import math
import os
import queue
import signal
import sys
import time

from . import __version__, clock, config as config_mod
from .backend import RealBackend
from .display import ConsoleGuard, Framebuffer, HeadlessFramebuffer, console_on_framebuffer, pick_framebuffer
from .netinfo import pick_interface
from .storage import StateFile, Storage
from .touch import Calibration, TouchEvent, TouchReader, find_touch_device
from .ui import Renderer
from .worker import NetMonitor, Worker, WorkerState


class SingleInstance:
    def __init__(self, path: str) -> None:
        self.path = path
        self.fd: int | None = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = None
            return False
        os.ftruncate(self.fd, 0)
        os.write(self.fd, str(os.getpid()).encode())
        return True

    def release(self) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


class UIController:
    """Owns which screen is showing, the sleep timer, and tap handling."""

    def __init__(self, cfg: dict, worker: Worker, storage: Storage, renderer: Renderer, fb, extra: dict) -> None:
        self.cfg = cfg
        self.worker = worker
        self.storage = storage
        self.renderer = renderer
        self.fb = fb
        self.extra = extra
        self.screen = "main"
        self.page = 0
        self.pages = 1
        self.asleep = False
        self.sleep_seconds = float(cfg["display"]["sleep_seconds"])
        self.debounce_s = int(cfg["touch"]["debounce_ms"]) / 1000.0
        self.tap_max_move = int(cfg["touch"]["tap_max_move_px"])
        self.last_touch_mono = time.monotonic()
        self.last_action_mono = 0.0
        self.consume_until_up = False
        self.down_pos: tuple[int, int] | None = None
        self.down_fired = False
        self.hits = []
        self.last_frame: bytes | None = None
        self.sleep_note = ""
        self.display_note = ""
        self.wake_count = 0
        self.labels = list(cfg["app"]["labels"]) or [cfg["app"]["label"]]

    # ---------------------------------------------------------------- touch
    def handle(self, ev: TouchEvent) -> None:
        if self.asleep:
            if ev.kind == "down":
                self.wake()
                self.consume_until_up = True
            elif ev.kind == "up":
                self.consume_until_up = False
            self.last_touch_mono = ev.t
            return
        self.last_touch_mono = ev.t
        if self.consume_until_up:
            if ev.kind == "up":
                self.consume_until_up = False
            return
        if ev.kind == "down":
            self.down_pos = (ev.x, ev.y)
            self.down_fired = False
            # Fire on press when the finger lands inside a button: feels immediate and
            # does not depend on where a resistive panel reports the release.
            if self._try_action(ev.x, ev.y, ev.t):
                self.down_fired = True
        elif ev.kind == "move":
            if self.down_pos is not None and not self.down_fired:
                # First samples of a resistive touch can be off; a later sample that
                # lands in a button still counts.
                if self._try_action(ev.x, ev.y, ev.t):
                    self.down_fired = True
        elif ev.kind == "up":
            if self.down_pos is None:
                return
            fired = self.down_fired
            x0, y0 = self.down_pos
            self.down_pos = None
            self.down_fired = False
            if fired or ev.x < 0:
                return
            if math.hypot(ev.x - x0, ev.y - y0) > self.tap_max_move:
                return
            self._try_action(ev.x, ev.y, ev.t)

    def _try_action(self, x: int, y: int, t: float) -> bool:
        if t - self.last_action_mono < self.debounce_s:
            return False
        for hit in self.hits:
            if hit.contains(x, y):
                self.last_action_mono = t
                self.perform(hit.action)
                return True
        return False

    def perform(self, action: str) -> None:
        if action == "toggle_run":
            with self.worker.state.lock:
                running = self.worker.state.running
            if running:
                self.worker.stop_session()
            else:
                self.worker.start_session()
        elif action == "details":
            self.screen = "details"
            self.page = 0
        elif action == "back":
            self.screen = "main"
        elif action == "page_up":
            self.page = max(0, self.page - 1)
        elif action == "page_down":
            self.page = min(self.pages - 1, self.page + 1)
        elif action == "switch_wifi":
            wifi = self.worker.state.wifi
            home_ssid = self.cfg["wifi"].get("home_ssid", "")
            target = "test" if (home_ssid and wifi.ssid == home_ssid) else "home"
            self.worker.switch_network(target)
        elif action == "next_label":
            current = self.worker.state.label_override or self.worker.state.label
            if current in self.labels:
                nxt = self.labels[(self.labels.index(current) + 1) % len(self.labels)]
            else:
                nxt = self.labels[0]
            self.worker.set_label_override(nxt)
            self.storage.write_event("label_changed", label=nxt, source="touchscreen")
        self.render(force=True)

    # ---------------------------------------------------------------- sleep
    def wake(self) -> None:
        self.asleep = False
        self.wake_count += 1
        if self.fb:
            self.sleep_note = self.fb.set_blank(False)
        self.last_frame = None
        self.render(force=True)

    def maybe_sleep(self, now: float) -> None:
        if self.sleep_seconds <= 0 or self.asleep or not self.fb:
            return
        if now - self.last_touch_mono >= self.sleep_seconds:
            self.asleep = True
            self.sleep_note = self.fb.set_blank(True)

    # --------------------------------------------------------------- render
    def snapshot(self) -> tuple[dict, dict]:
        snap = self.worker.state.snapshot()
        snap["worker_alive"] = snap["worker_alive"] and self.worker.is_alive()
        st = self.storage.status
        storage = {"last_error": st.last_error, "free_mb": st.free_mb, "low_disk": st.low_disk,
                   "rows_written": st.rows_written, "events_written": st.events_written}
        return snap, storage

    def render(self, force: bool = False) -> None:
        if self.asleep or not self.fb:
            return
        snap, storage = self.snapshot()
        extra = dict(self.extra)
        extra["sleep"] = ("disabled" if self.sleep_seconds <= 0 else
                          f"{int(self.sleep_seconds)} s, method {getattr(self.fb, 'sleep_method', '-')}")
        if self.screen == "details":
            img, self.hits, self.pages = self.renderer.render_details(snap, storage, extra, self.page)
        else:
            img, self.hits = self.renderer.render_main(snap, storage, self.display_note)
        data = img.tobytes()
        if force or data != self.last_frame:
            try:
                self.fb.blit(img)
            except OSError as exc:
                self.display_note = f"display write failed: {exc.strerror}"
            self.last_frame = data


def check_orphaned_cycle(storage: Storage) -> None:
    """After a crash or power loss a cycle marker may be left behind.  Record it so the
    gap is visible in both logs, then clear it."""
    marker = storage.read_marker()
    if not marker:
        return
    row = {
        "session_id": marker.get("session_id", ""), "cycle_id": marker.get("cycle_id", ""),
        "label": marker.get("label", ""), "start_time": marker.get("start_time", ""),
        "end_time": clock.iso(), "duration_s": None, "status": "INTERRUPTED",
        "failed_stage": "crash_recovery",
        "error": "process ended unexpectedly during this cycle (recorded at next start)",
    }
    storage.write_event("cycle_orphaned", **row)
    storage.write_result_row(row)
    storage.clear_marker()


def run_app(cfg: dict, headless: bool = False, png_path: str | None = None) -> int:
    lcfg = cfg["logging"]
    lock = SingleInstance(os.path.join(lcfg["runtime_directory"], "lock"))
    if not lock.acquire():
        print("another pingaroo instance is running (lock held)", file=sys.stderr)
        return 3

    storage = Storage(cfg)
    storage.ensure_directory()
    storage.check_disk()
    check_orphaned_cycle(storage)

    state = WorkerState()
    state.interface, state.interface_note = pick_interface(cfg["wifi"]["interface"])
    backend = RealBackend()
    state.speedtest_version = backend.speedtest_version(cfg)
    state.clock_synced = clock.clock_synced()
    worker = Worker(cfg, backend, storage, state)
    storage.write_event("app_start", version=__version__, config=cfg.get("_path"), interface=state.interface,
                        interface_note=state.interface_note, speedtest_version=state.speedtest_version,
                        clock_synced=state.clock_synced, autostart=cfg["app"]["autostart"], headless=headless)
    worker.start()
    monitor = NetMonitor(worker)
    monitor.start()

    renderer = Renderer(cfg)
    statefile = StateFile(lcfg["runtime_directory"])
    extra = {"log_dir": lcfg["directory"], "config_path": cfg.get("_path"), "version": __version__,
             "display": "headless" if headless else "not attached", "touch": "headless" if headless else "not attached"}

    fb = None
    console = ConsoleGuard()
    touch_q: "queue.Queue[TouchEvent]" = queue.Queue()
    touch_reader: TouchReader | None = None
    fb_note = ""
    next_hw_retry = 0.0

    def attach_hardware(now: float) -> None:
        nonlocal fb, touch_reader, fb_note, next_hw_retry
        if now < next_hw_retry:
            return
        next_hw_retry = now + 5.0
        if fb is None:
            info, fb_note = pick_framebuffer(cfg["display"]["framebuffer"], cfg["display"]["width"], cfg["display"]["height"])
            if info:
                try:
                    fb = Framebuffer(info)
                    extra["display"] = f"{info.path} {info.width}x{info.height} {info.bpp}bpp ({info.name})"
                    mode = cfg["display"]["console_graphics_mode"]
                    if mode == "always" or (mode == "auto" and console_on_framebuffer(info.index) in (True, None) and info.index == 0):
                        console.enable()
                    storage.write_event("display_attached", **extra, note=fb_note)
                    if cfg["display"]["splash_seconds"] > 0:
                        from .splash import show_splash
                        show_splash(fb, info.width, info.height, renderer.fonts,
                                    float(cfg["display"]["splash_seconds"]))
                        while not touch_q.empty():
                            touch_q.get_nowait()
                        controller.last_touch_mono = time.monotonic()
                    controller.fb = fb
                    controller.render(force=True)
                except OSError as exc:
                    fb_note = f"cannot open {info.path}: {exc.strerror}"
            if fb is None:
                extra["display"] = "not attached: " + fb_note
        if touch_reader is None:
            path, note = find_touch_device(cfg["touch"]["device"], cfg["touch"]["device_name_match"])
            if path:
                touch_reader = TouchReader(path, Calibration.from_config(cfg), touch_q)
                touch_reader.start()
                extra["touch"] = f"{path} ({note})"
                storage.write_event("touch_attached", device=path, note=note)
            else:
                extra["touch"] = "not attached: " + note

    if headless:
        fb = HeadlessFramebuffer(png_path, cfg["display"]["width"], cfg["display"]["height"]) if png_path else None
    controller = UIController(cfg, worker, storage, renderer, fb, extra)

    stop_flag = {"stop": False}

    def _on_signal(signum, _frame):
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    if cfg["app"]["autostart"]:
        worker.start_session()

    refresh = float(cfg["display"]["refresh_seconds"])
    next_tick = time.monotonic()
    try:
        while not stop_flag["stop"]:
            now = time.monotonic()
            if not headless:
                attach_hardware(now)
            timeout = max(0.0, next_tick - now)
            try:
                ev = touch_q.get(timeout=timeout)
                controller.handle(ev)
                continue
            except queue.Empty:
                pass
            with state.lock:
                live_now = state.live is not None and state.running
            next_tick = time.monotonic() + (min(refresh, 0.5) if live_now else refresh)
            cmd = statefile.pop_control()
            if cmd == "start":
                if worker.start_session():
                    storage.write_event("control", command="start", source="cli")
            elif cmd == "stop":
                if worker.stop_session():
                    storage.write_event("control", command="stop", source="cli")
            elif cmd.startswith("switch:"):
                worker.switch_network(cmd.split(":", 1)[1])
            controller.maybe_sleep(time.monotonic())
            controller.render()
            if touch_reader is not None and touch_reader.error:
                extra["touch"] = f"error: {touch_reader.error}"
            snap, st = controller.snapshot()
            snap["storage"] = st
            snap["screen"] = controller.screen
            snap["asleep"] = controller.asleep
            snap["display"] = extra.get("display")
            snap["touch"] = extra.get("touch")
            snap["pid"] = os.getpid()
            snap["interval_s"] = cfg["app"]["interval_seconds"]
            snap["time"] = clock.iso()
            statefile.write(snap)
            if not worker.is_alive() and not state.worker_crash:
                state.worker_alive = False
                state.worker_crash = "worker thread exited"
    finally:
        storage.write_event("app_stop", reason="signal" if stop_flag["stop"] else "exit")
        worker.shutdown()
        monitor.stop()
        if touch_reader:
            touch_reader.stop()
        deadline = time.monotonic() + 8
        while worker.cycle_running and time.monotonic() < deadline:
            time.sleep(0.1)
        if fb is not None:
            try:
                if controller.asleep:
                    fb.set_blank(False)
                fb.blit(renderer.render_message("Pingaroo Stopped", ["The service is not running.",
                                                                        "sudo systemctl start pingaroo"]))
            except Exception:
                pass
            fb.close()
        console.restore()
        statefile.write({"running": False, "phase": "Stopped", "worker_alive": False, "time": clock.iso(),
                         "note": "service not running"})
        lock.release()
    return 0
