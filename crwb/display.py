"""Linux framebuffer output.  The UI is rendered with Pillow and written straight to
/dev/fbN as RGB565 (16 bpp) or XRGB8888 (32 bpp), so it works with the fbtft driver
and with DRM fbdev emulation (panel-mipi-dbi) alike.  No X11 or Wayland needed."""

from __future__ import annotations

import fcntl
import glob
import os
from dataclasses import dataclass

from PIL import Image

SYS_FB = "/sys/class/graphics"
KDSETMODE = 0x4B3A
KD_TEXT = 0x00
KD_GRAPHICS = 0x01
FBIOGET_CON2FBMAP = 0x460F


@dataclass
class FbInfo:
    path: str
    name: str
    width: int
    height: int
    bpp: int
    stride: int

    @property
    def index(self) -> int:
        try:
            return int(os.path.basename(self.path).replace("fb", ""))
        except ValueError:
            return -1


def _read(path: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return default


def list_framebuffers() -> list[FbInfo]:
    out: list[FbInfo] = []
    for sys_path in sorted(glob.glob(f"{SYS_FB}/fb*")):
        name = os.path.basename(sys_path)
        dev = f"/dev/{name}"
        if not os.path.exists(dev):
            continue
        size = _read(f"{sys_path}/virtual_size", "0,0").split(",")
        try:
            w, h = int(size[0]), int(size[1])
        except (ValueError, IndexError):
            w, h = 0, 0
        bpp = int(_read(f"{sys_path}/bits_per_pixel", "0") or 0)
        stride = int(_read(f"{sys_path}/stride", "0") or 0) or (w * bpp // 8)
        out.append(FbInfo(dev, _read(f"{sys_path}/name"), w, h, bpp, stride))
    return out


def pick_framebuffer(configured: str, width: int, height: int) -> tuple[FbInfo | None, str]:
    fbs = list_framebuffers()
    if configured:
        for fb in fbs:
            if fb.path == configured:
                return fb, "configured"
        return None, f"configured framebuffer {configured} not present"
    if not fbs:
        return None, "no framebuffer devices"
    for fb in fbs:  # exact size match wins (the SPI panel)
        if (fb.width, fb.height) == (width, height):
            return fb, f"matched {width}x{height}"
    for fb in fbs:  # then an fbtft / tiny DRM panel by name
        low = fb.name.lower()
        if low.startswith("fb_") or "ili" in low or "st77" in low or "mipi" in low or "spi" in low:
            return fb, f"matched driver name {fb.name}"
    return None, "no framebuffer matches the display (sizes: " + \
        ", ".join(f"{fb.path}={fb.width}x{fb.height}" for fb in fbs) + ")"


class Framebuffer:
    def __init__(self, info: FbInfo) -> None:
        self.info = info
        self.fd = os.open(info.path, os.O_RDWR)
        self.blanked = False
        self._backlight = self._find_backlight()
        self.sleep_method = "backlight" if self._backlight else "blank"
        self.blank_supported: bool | None = None

    @staticmethod
    def _find_backlight() -> str | None:
        for path in sorted(glob.glob("/sys/class/backlight/*")):
            if os.path.exists(f"{path}/bl_power") or os.path.exists(f"{path}/brightness"):
                return path
        return None

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass

    def _encode(self, img: Image.Image) -> bytes:
        info = self.info
        if img.size != (info.width, info.height):
            img = img.resize((info.width, info.height))
        if info.bpp == 16:
            data = img.convert("RGB").tobytes("raw", "BGR;16")
            row_bytes = info.width * 2
        elif info.bpp == 32:
            data = img.convert("RGB").tobytes("raw", "BGRX")
            row_bytes = info.width * 4
        elif info.bpp == 24:
            data = img.convert("RGB").tobytes("raw", "BGR")
            row_bytes = info.width * 3
        else:
            raise RuntimeError(f"unsupported framebuffer depth {info.bpp} bpp")
        if info.stride and info.stride != row_bytes:
            pad = b"\0" * (info.stride - row_bytes)
            data = b"".join(data[i:i + row_bytes] + pad for i in range(0, len(data), row_bytes))
        return data

    def blit(self, img: Image.Image) -> None:
        data = self._encode(img)
        os.pwrite(self.fd, data, 0)

    def set_blank(self, blank: bool) -> str:
        """Blank or unblank.  Returns a note describing what actually happened."""
        self.blanked = blank
        note = ""
        if self._backlight:
            try:
                with open(f"{self._backlight}/bl_power", "w") as fh:
                    fh.write("4" if blank else "0")   # FB_BLANK_POWERDOWN / UNBLANK
                note = "backlight " + ("off" if blank else "on")
            except OSError:
                try:
                    max_b = _read(f"{self._backlight}/max_brightness", "255")
                    with open(f"{self._backlight}/brightness", "w") as fh:
                        fh.write("0" if blank else max_b)
                    note = "backlight " + ("off" if blank else "on")
                except OSError:
                    self._backlight = None
        if not note:
            sys_blank = f"{SYS_FB}/{os.path.basename(self.info.path)}/blank"
            try:
                with open(sys_blank, "w") as fh:
                    fh.write("4" if blank else "0")
                self.blank_supported = True
                note = "fb blank " + ("on" if blank else "off")
            except OSError:
                self.blank_supported = False
                note = "black frame only (backlight stays on)"
            if blank:
                self.blit(Image.new("RGB", (self.info.width, self.info.height), (0, 0, 0)))
        return note


class HeadlessFramebuffer:
    """Writes each frame to a PNG; used by `crwb preview` and the tests."""

    def __init__(self, out_path: str, width: int, height: int) -> None:
        self.out_path = out_path
        self.info = FbInfo(out_path, "headless", width, height, 24, width * 3)
        self.blanked = False
        self.sleep_method = "none"
        self.blank_supported = None

    def blit(self, img: Image.Image) -> None:
        img.save(self.out_path)

    def set_blank(self, blank: bool) -> str:
        self.blanked = blank
        return "headless"

    def close(self) -> None:
        pass


def console_on_framebuffer(fb_index: int) -> bool | None:
    """True if the kernel console (tty1) is mapped to this framebuffer, None if unknown."""
    try:
        fd = os.open("/dev/fb0", os.O_RDONLY)
    except OSError:
        return None
    try:
        import struct
        buf = struct.pack("II", 1, 0)
        res = fcntl.ioctl(fd, FBIOGET_CON2FBMAP, buf)
        _, mapped = struct.unpack("II", res)
        return mapped == fb_index
    except OSError:
        return None
    finally:
        os.close(fd)


class ConsoleGuard:
    """Switch tty1 to graphics mode so the text console stops drawing over the LCD
    when the LCD is the only framebuffer.  Restored on exit."""

    def __init__(self, tty: str = "/dev/tty1") -> None:
        self.tty = tty
        self.fd: int | None = None

    def enable(self) -> bool:
        try:
            self.fd = os.open(self.tty, os.O_RDWR)
            fcntl.ioctl(self.fd, KDSETMODE, KD_GRAPHICS)
            return True
        except OSError:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            return False

    def restore(self) -> None:
        if self.fd is None:
            return
        try:
            fcntl.ioctl(self.fd, KDSETMODE, KD_TEXT)
        except OSError:
            pass
        os.close(self.fd)
        self.fd = None
