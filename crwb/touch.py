"""Touch input from the XPT2046/ADS7846 controller via evdev.

The kernel ads7846 driver exposes raw ABS_X/ABS_Y (0..4095) plus BTN_TOUCH.  Raw
values are mapped to screen pixels with the calibration in [touch]."""

from __future__ import annotations

import glob
import queue
import threading
import time
from dataclasses import dataclass


@dataclass
class TouchEvent:
    kind: str          # "down" | "move" | "up"
    x: int
    y: int
    t: float           # monotonic


@dataclass
class Calibration:
    width: int
    height: int
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    swap_xy: bool
    invert_x: bool
    invert_y: bool

    @classmethod
    def from_config(cls, cfg: dict) -> "Calibration":
        t = cfg["touch"]
        d = cfg["display"]
        return cls(int(d["width"]), int(d["height"]), int(t["x_min"]), int(t["x_max"]),
                   int(t["y_min"]), int(t["y_max"]), bool(t["swap_xy"]),
                   bool(t["invert_x"]), bool(t["invert_y"]))

    def map(self, raw_x: int, raw_y: int) -> tuple[int, int]:
        if self.swap_xy:
            raw_x, raw_y = raw_y, raw_x
        span_x = max(1, self.x_max - self.x_min)
        span_y = max(1, self.y_max - self.y_min)
        fx = (raw_x - self.x_min) / span_x
        fy = (raw_y - self.y_min) / span_y
        if self.invert_x:
            fx = 1.0 - fx
        if self.invert_y:
            fy = 1.0 - fy
        x = int(round(min(1.0, max(0.0, fx)) * (self.width - 1)))
        y = int(round(min(1.0, max(0.0, fy)) * (self.height - 1)))
        return x, y


def find_touch_device(configured: str, name_match: str) -> tuple[str, str]:
    """Return (path, note).  Prefers the configured path, then a device whose name
    matches, then any device with BTN_TOUCH + ABS_X."""
    try:
        import evdev
    except ImportError:
        return "", "python3-evdev not installed"
    if configured:
        return configured, "configured"
    candidates = sorted(glob.glob("/dev/input/event*"))
    fallback = ""
    for path in candidates:
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        try:
            caps = dev.capabilities()
            has_touch = evdev.ecodes.EV_KEY in caps and evdev.ecodes.BTN_TOUCH in caps[evdev.ecodes.EV_KEY]
            abs_codes = {c if isinstance(c, int) else c[0] for c in caps.get(evdev.ecodes.EV_ABS, [])}
            has_abs = evdev.ecodes.ABS_X in abs_codes
            if name_match and name_match.lower() in dev.name.lower():
                return path, f"matched name '{dev.name}'"
            if has_touch and has_abs and not fallback:
                fallback = path
        finally:
            dev.close()
    if fallback:
        return fallback, "first device with BTN_TOUCH + ABS_X"
    return "", "no touch device found"


class TouchReader(threading.Thread):
    """Reads evdev events and emits mapped TouchEvents onto a queue."""

    def __init__(self, path: str, calibration: Calibration, out: "queue.Queue[TouchEvent]") -> None:
        super().__init__(name="crwb-touch", daemon=True)
        self.path = path
        self.cal = calibration
        self.out = out
        self.error = ""
        self._stop = threading.Event()
        self.raw_range: tuple[int, int, int, int] | None = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            import evdev
            from evdev import ecodes
        except ImportError:
            self.error = "python3-evdev not installed"
            return
        while not self._stop.is_set():
            try:
                dev = evdev.InputDevice(self.path)
            except OSError as exc:
                self.error = f"cannot open {self.path}: {exc.strerror}"
                time.sleep(2)
                continue
            self.error = ""
            try:
                ax = dev.absinfo(ecodes.ABS_X)
                ay = dev.absinfo(ecodes.ABS_Y)
                self.raw_range = (ax.min, ax.max, ay.min, ay.max)
            except Exception:
                pass
            raw_x = raw_y = None
            down = False
            try:
                for ev in dev.read_loop():
                    if self._stop.is_set():
                        break
                    if ev.type == ecodes.EV_ABS:
                        if ev.code == ecodes.ABS_X:
                            raw_x = ev.value
                        elif ev.code == ecodes.ABS_Y:
                            raw_y = ev.value
                    elif ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_TOUCH:
                        if ev.value == 1:
                            down = True
                            pending = "down"
                        else:
                            down = False
                            pending = "up"
                        if raw_x is not None and raw_y is not None:
                            x, y = self.cal.map(raw_x, raw_y)
                            self.out.put(TouchEvent(pending, x, y, time.monotonic()))
                        elif pending == "up":
                            self.out.put(TouchEvent("up", -1, -1, time.monotonic()))
                    elif ev.type == ecodes.EV_SYN and down and raw_x is not None and raw_y is not None:
                        x, y = self.cal.map(raw_x, raw_y)
                        self.out.put(TouchEvent("move", x, y, time.monotonic()))
            except OSError as exc:
                self.error = f"touch device error: {exc.strerror}"
                time.sleep(1)
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
