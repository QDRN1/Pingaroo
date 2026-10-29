"""Screen rendering with Pillow for a 480x320 landscape panel.

Two screens: Main (fits without scrolling) and Details (paged).  Each render returns
the image plus a list of hit rectangles so the controller can map taps to actions.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from . import clock

# Theme: high contrast on a dark background.
BG = (10, 13, 20)
BG2 = (18, 24, 34)
PANEL = (26, 33, 46)
PANEL_EDGE = (44, 54, 72)
TEXT = (242, 244, 248)
MUTED = (150, 158, 172)
GREEN = (72, 210, 110)
AMBER = (250, 184, 48)
RED = (240, 84, 72)
BLUE = (92, 160, 250)
CYAN = (70, 200, 220)
BTN_START = (30, 150, 76)
BTN_STOP = (196, 52, 46)
BTN_NEUTRAL = (54, 64, 86)
BTN_TEXT = (255, 255, 255)
BAR_BG = (40, 48, 64)


@dataclass
class Hit:
    x0: int
    y0: int
    x1: int
    y1: int
    action: str

    def contains(self, x: int, y: int) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


class Fonts:
    def __init__(self, regular: str, bold: str) -> None:
        self._regular = regular if os.path.exists(regular) else None
        self._bold = bold if os.path.exists(bold) else self._regular
        self.cache: dict = {}
        self.missing = self._regular is None

    def get(self, size: int, bold: bool = False):
        key = (size, bold)
        if key not in self.cache:
            path = self._bold if bold else self._regular
            if path:
                self.cache[key] = ImageFont.truetype(path, size)
            else:
                self.cache[key] = ImageFont.load_default(size=size)
        return self.cache[key]


def fmt_num(value, digits: int = 1) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000
    return "-"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def signal_bars(dbm: int | None) -> int:
    if dbm is None:
        return 0
    if dbm >= -55:
        return 4
    if dbm >= -65:
        return 3
    if dbm >= -75:
        return 2
    return 1


class Renderer:
    def __init__(self, cfg: dict) -> None:
        d = cfg["display"]
        self.w = int(d["width"])
        self.h = int(d["height"])
        self.fonts = Fonts(d["font"], d["font_bold"])
        self.title = "Pingaroo"
        self.ssid = cfg["wifi"]["ssid"]
        self.home_ssid = cfg["wifi"].get("home_ssid", "")

    # ------------------------------------------------------------- utilities
    def _text(self, draw, xy, text, size, fill=TEXT, bold=False, anchor="la", max_w=None):
        font = self.fonts.get(size, bold)
        if max_w is not None:
            text = self._ellipsize(draw, text, font, max_w)
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)

    @staticmethod
    def _ellipsize(draw, text, font, max_w):
        if draw.textlength(text, font=font) <= max_w:
            return text
        while len(text) > 1 and draw.textlength(text + "…", font=font) > max_w:
            text = text[:-1]
        return text + "…"

    def _button(self, draw, hits, rect, label, action, color, size=20):
        x0, y0, x1, y1 = rect
        shadow = tuple(max(0, c - 18) for c in color)
        draw.rounded_rectangle((x0, y0 + 3, x1, y1 + 3), radius=10, fill=shadow)
        draw.rounded_rectangle(rect, radius=10, fill=color)
        hi = tuple(min(255, c + 22) for c in color)
        draw.rounded_rectangle((x0 + 2, y0 + 2, x1 - 2, y0 + (y1 - y0) // 2), radius=8, fill=hi)
        draw.rounded_rectangle((x0 + 2, y0 + (y1 - y0) // 2 - 6, x1 - 2, y1 - 2), radius=8, fill=color)
        self._text(draw, ((x0 + x1) // 2, (y0 + y1) // 2), label, size, BTN_TEXT, bold=True, anchor="mm")
        hits.append(Hit(x0, y0, x1, y1, action))

    @staticmethod
    def _icon_wifi(draw, x, y, bars, color, dim=(60, 68, 84)):
        """Four signal bars, 22x16, bottom-left at (x, y+16)."""
        for i in range(4):
            h = 5 + i * 4
            bx = x + i * 6
            fill = color if i < bars else dim
            draw.rectangle((bx, y + 16 - h, bx + 4, y + 16), fill=fill)

    @staticmethod
    def _icon_globe(draw, x, y, color):
        """Globe 18x18 at (x, y)."""
        draw.ellipse((x, y, x + 18, y + 18), outline=color, width=2)
        draw.ellipse((x + 5, y, x + 13, y + 18), outline=color, width=1)
        draw.line((x + 1, y + 9, x + 17, y + 9), fill=color, width=1)
        draw.line((x + 3, y + 4, x + 15, y + 4), fill=color, width=1)
        draw.line((x + 3, y + 14, x + 15, y + 14), fill=color, width=1)

    @staticmethod
    def _icon_dot(draw, x, y, color, r=5):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)

    def _spinner(self, draw, cx, cy, r, color, t):
        """Three orbiting dots, phase from wall time so each redraw moves it."""
        for k in range(3):
            a = (t * 2.6 + k * 2.094)
            px = cx + r * math.cos(a)
            py = cy + r * math.sin(a)
            rad = 3 if k == 0 else 2
            draw.ellipse((px - rad, py - rad, px + rad, py + rad), fill=color)

    def _progress_bar(self, draw, rect, fraction, color, t, label=""):
        x0, y0, x1, y1 = rect
        draw.rounded_rectangle(rect, radius=4, fill=BAR_BG)
        if fraction is None:
            # indeterminate: sweeping highlight
            w = x1 - x0
            pos = (t * 160) % (w + 80) - 40
            sx0, sx1 = max(x0, x0 + pos), min(x1, x0 + pos + 80)
            if sx1 > sx0:
                draw.rounded_rectangle((sx0, y0, sx1, y1), radius=4, fill=color)
        else:
            f = max(0.0, min(1.0, fraction))
            fx = x0 + int((x1 - x0) * f)
            if fx > x0:
                draw.rounded_rectangle((x0, y0, fx, y1), radius=4, fill=color)
                # moving sheen inside the filled part
                sheen = fx - 24 - int((t * 90) % max(1, fx - x0))
                if sheen > x0:
                    draw.rounded_rectangle((max(x0, sheen), y0, min(fx, sheen + 24), y1), radius=4,
                                           fill=tuple(min(255, c + 60) for c in color))
        if label:
            self._text(draw, ((x0 + x1) // 2, (y0 + y1) // 2), label, 11, TEXT, bold=True, anchor="mm")

    # ------------------------------------------------------------------ main
    def render_main(self, snap: dict, storage: dict, display_note: str = "",
                    now: float | None = None) -> tuple[Image.Image, list[Hit]]:
        t = time.time() if now is None else now
        img = Image.new("RGB", (self.w, self.h), BG)
        draw = ImageDraw.Draw(img)
        hits: list[Hit] = []
        wifi = snap.get("wifi") or {}
        internet = snap.get("internet") or {}
        session = snap.get("session")
        phase = snap.get("phase", "Idle")
        running = snap.get("running", False)
        worker_alive = snap.get("worker_alive", True)
        live = snap.get("live") or None
        testing_live = bool(live) and running

        # Header: title | label chip | date/time
        draw.rectangle((0, 0, self.w, 32), fill=PANEL)
        draw.line((0, 32, self.w, 32), fill=PANEL_EDGE)
        self._icon_dot(draw, 14, 16, AMBER, 6)
        self._icon_dot(draw, 14, 16, BG, 2)
        self._text(draw, (26, 16), self.title, 18, TEXT, bold=True, anchor="lm")
        label = snap.get("label") or "-"
        font = self.fonts.get(13, True)
        lw = min(170, int(draw.textlength(label, font=font)) + 16)
        cx = 150
        draw.rounded_rectangle((cx, 7, cx + lw, 25), radius=9, fill=(70, 52, 14))
        self._text(draw, (cx + lw // 2, 16), label, 13, AMBER, bold=True, anchor="mm", max_w=lw - 12)
        now_dt = clock.now()
        self._text(draw, (self.w - 8, 16), now_dt.strftime("%a %b %d  %H:%M:%S"), 14, TEXT, anchor="rm")

        # Network rows
        y = 40
        home = bool(self.home_ssid) and wifi.get("ssid") == self.home_ssid and self.home_ssid != self.ssid
        target = self.ssid
        if snap.get("switching"):
            dest = self.home_ssid if snap["switching"] == "home" else self.ssid
            wifi_txt, wifi_col = f"Switching to {dest or snap['switching']}", AMBER
        elif wifi.get("connected"):
            wifi_txt, wifi_col = f"Connected: {wifi.get('ssid')}", GREEN
        elif home and wifi.get("ip"):
            wifi_txt, wifi_col = f"Home: {wifi.get('ssid')}", BLUE
        elif phase == "Connecting" and running:
            wifi_txt, wifi_col = f"Connecting: {target}", AMBER
        elif wifi.get("associated") and not wifi.get("ip"):
            wifi_txt, wifi_col = f"No IP yet: {wifi.get('ssid') or '?'}", AMBER
        elif wifi.get("associated"):
            wifi_txt, wifi_col = f"Other: {wifi.get('ssid') or '?'}", AMBER
        else:
            wifi_txt, wifi_col = f"Not connected (target {target})", RED
        self._icon_wifi(draw, 8, y + 1, signal_bars(wifi.get("signal_dbm")) if wifi.get("associated") else 0, wifi_col)
        self._text(draw, (36, y), "Wi-Fi", 15, MUTED)
        dbm_txt = f"{wifi['signal_dbm']} dBm" if (wifi.get("signal_dbm") is not None and wifi.get("associated")) else ""
        self._text(draw, (90, y), wifi_txt, 15, wifi_col, bold=True, max_w=(236 if dbm_txt else 250))
        if dbm_txt:
            self._text(draw, (334, y + 2), dbm_txt, 12, MUTED)
        ip = wifi.get("ip") or "No IP"
        self._text(draw, (self.w - 8, y), ip, 15, TEXT if wifi.get("ip") else RED, bold=True, anchor="ra", max_w=130)

        y = 62
        if internet.get("checking"):
            inet_txt, inet_col = "Checking", AMBER
        elif internet.get("ok") is None:
            inet_txt, inet_col = "Not Checked Yet", MUTED
        else:
            age = clock.age_text(snap.get("internet_age_s"))
            ok = internet.get("ok")
            inet_txt = ("OK" if ok else "Failed") + f"  (last check {age})"
            inet_col = GREEN if ok else RED
        self._icon_globe(draw, 9, y + 1, inet_col)
        self._text(draw, (36, y), "Internet", 15, MUTED)
        self._text(draw, (102, y), inet_txt, 15, inet_col, bold=True, max_w=370)

        # Results block
        speed = snap.get("last_speed") or {}
        previous = snap.get("speed_is_previous", False)
        draw.rounded_rectangle((4, 86, self.w - 4, 170), radius=10, fill=PANEL, outline=PANEL_EDGE)
        cols = [(84, "Download Mbps", "download", speed.get("download_mbps")),
                (240, "Upload Mbps", "upload", speed.get("upload_mbps")),
                (396, "Ping ms (speedtest idle)", "ping", speed.get("latency_ms"))]
        live_phase = (live or {}).get("phase") if testing_live else None
        for cx, caption, key, value in cols:
            is_live = live_phase == key
            self._text(draw, (cx, 98), caption, 12, AMBER if is_live else MUTED, anchor="mm")
            if is_live:
                if key == "ping":
                    shown = fmt_num(live.get("latency_ms"), 1)
                else:
                    shown = fmt_num(live.get("mbps"), 1)
                self._text(draw, (cx, 130), shown, 34, AMBER, bold=True, anchor="mm")
            else:
                value_col = MUTED if previous else TEXT
                self._text(draw, (cx, 130), fmt_num(value, 1), 34, value_col, bold=True, anchor="mm")
        if testing_live:
            frac = (live or {}).get("progress")
            name = {"ping": "Latency", "download": "Download", "upload": "Upload", "start": "Starting"}.get(live_phase, "Speed test")
            pct = f"{int(frac * 100)}%" if isinstance(frac, (int, float)) else ""
            self._progress_bar(draw, (14, 152, self.w - 14, 164), frac if live_phase in ("download", "upload") else None,
                               BLUE if live_phase == "download" else CYAN if live_phase == "upload" else AMBER, t,
                               f"{name} {pct}".strip())
        else:
            if speed.get("partial"):
                self._text(draw, (self.w - 10, 164), "partial result", 11, AMBER, anchor="rb")
            if previous:
                self._text(draw, (12, 164), "previous result (new test running)", 11, AMBER, anchor="lb")
            elif speed and snap.get("last_speed_age_s") is not None:
                last_row = snap.get("last_row") or {}
                when = (last_row.get("end_time") or "")[11:19]
                self._text(draw, (12, 164), f"measured {when} ({clock.age_text(snap.get('last_speed_age_s'))})", 11, MUTED, anchor="lb")

        # Phase / countdown row
        y = 178
        if not worker_alive:
            phase_txt, phase_col = "WORKER STOPPED", RED
        elif running:
            phase_txt, phase_col = phase, AMBER if phase != "Waiting" else BLUE
        else:
            phase_txt, phase_col = phase, MUTED
        self._text(draw, (8, y), "Phase", 15, MUTED)
        self._text(draw, (62, y), phase_txt, 15, phase_col, bold=True, max_w=210)
        if running and phase not in ("Waiting", "Idle"):
            self._spinner(draw, 290, y + 9, 6, phase_col, t)
        countdown = snap.get("countdown_s")
        if running and countdown is not None and phase == "Waiting":
            self._text(draw, (self.w - 8, y), "Next test in " + clock.mmss(countdown), 15, BLUE, bold=True, anchor="ra")
            total = max(1.0, float(snap.get("interval_s") or 300))
            self._progress_bar(draw, (8, y + 22, self.w - 8, y + 27), 1.0 - min(1.0, countdown / total), BLUE, t)

        y = 208
        if session:
            parts = [("Cycles", session["cycles"], TEXT), ("OK", session["successes"], GREEN),
                     ("Failed", session["failures"], RED if session["failures"] else TEXT)]
            if session.get("interrupted"):
                parts.append(("Stopped", session["interrupted"], AMBER))
            x = 8
            for name, val, col in parts:
                self._text(draw, (x, y), name, 13, MUTED)
                x += int(draw.textlength(name, font=self.fonts.get(13)) + 5)
                self._text(draw, (x, y - 1), str(val), 15, col, bold=True)
                x += int(draw.textlength(str(val), font=self.fonts.get(15, True)) + 16)
        else:
            self._text(draw, (8, y), "No session yet. Tap Start Test.", 14, MUTED)

        # error / note line
        y = 232
        err = snap.get("last_error") or ""
        err_col = RED
        storage_err = (storage or {}).get("last_error") or ""
        if storage_err:
            err = f"LOG WRITE FAILED: {storage_err}"
        elif (storage or {}).get("low_disk"):
            err = f"Low disk space: {storage.get('free_mb')} MB free"
        elif snap.get("worker_crash"):
            err = f"Worker crashed: {snap['worker_crash']}"
        elif not snap.get("interface"):
            err = "No Wi-Fi interface detected"
        elif snap.get("switch_note"):
            err, err_col = snap["switch_note"], AMBER
        elif display_note:
            err = display_note
        if err:
            self._text(draw, (8, y), err, 13, err_col, max_w=self.w - 16)

        # Buttons
        by0, by1 = 258, 312
        if running:
            self._button(draw, hits, (8, by0, 330, by1), "Stop Testing", "toggle_run", BTN_STOP, 22)
        else:
            self._button(draw, hits, (8, by0, 330, by1), "Start Test", "toggle_run", BTN_START, 22)
        self._button(draw, hits, (344, by0, 472, by1), "Details", "details", BTN_NEUTRAL, 18)
        return img, hits

    # --------------------------------------------------------------- details
    def details_lines(self, snap: dict, storage: dict, extra: dict) -> list[tuple[str, str, tuple]]:
        """Return (key, value, color) rows for the Details view."""
        wifi = snap.get("wifi") or {}
        session = snap.get("session")
        speed = snap.get("last_speed") or {}
        conn = snap.get("last_connectivity") or {}
        last_row = snap.get("last_row") or {}
        router = snap.get("last_router") or {}
        na = "unavailable"

        def v(x, suffix=""):
            return na if x in (None, "", []) else f"{x}{suffix}"

        rows: list[tuple[str, str, tuple]] = []
        rows.append(("WI-FI LINK", "", AMBER))
        same_net = bool(self.home_ssid) and self.home_ssid == self.ssid
        if wifi.get("associated"):
            tag = ("  [test network]" if wifi.get("ssid") == self.ssid else
                   "  [home network]" if wifi.get("ssid") and wifi.get("ssid") == self.home_ssid else "  [other network]")
            rows.append(("Connected SSID", v(wifi.get("ssid")) + tag, GREEN if wifi.get("connected") else TEXT))
        else:
            rows.append(("Connected SSID", f"not connected (target {self.ssid})", RED))
        rows.append(("Test network", self.ssid + ("  (same as home: run setup-wifi.sh)" if same_net else ""), AMBER if same_net else TEXT))
        rows.append(("Home network", v(self.home_ssid), TEXT))
        rows.append(("Interface", v(snap.get("interface")) + (f" ({snap.get('interface_note')})" if snap.get("interface_note") else ""), TEXT))
        rows.append(("BSSID", v(wifi.get("bssid")), TEXT))
        rows.append(("Signal", v(wifi.get("signal_dbm"), " dBm") + "  (Wi-Fi link, not cellular)", TEXT))
        band = wifi.get("band") or ""
        ch = wifi.get("channel")
        rows.append(("Band / channel", (f"{band} ch {ch}" if band and ch else na), TEXT))
        rate = wifi.get("tx_rate_mbps")
        rows.append(("Link rate", (f"tx {fmt_num(rate, 0)} Mbps" + (f", rx {fmt_num(wifi.get('rx_rate_mbps'), 0)} Mbps" if wifi.get("rx_rate_mbps") else "")) if rate else na, TEXT))
        rows.append(("Local IP", v(wifi.get("ip")) + (f"/{wifi.get('prefix')}" if wifi.get("ip") and wifi.get("prefix") else ""), TEXT))
        rows.append(("Gateway", v(wifi.get("gateway")), TEXT))
        rows.append(("Router DNS (DHCP)", v(", ".join(wifi.get("dhcp_dns") or [])), TEXT))
        rows.append(("IPv6", v(", ".join(wifi.get("ipv6") or [])), TEXT))
        rows.append(("Public IP (last test)", v(speed.get("public_ip")), TEXT))

        rows.append(("CELLULAR (FROM ROUTER)", "", AMBER))
        if router and not router.get("error") and any(k.startswith("cell_") and router.get(k) is not None for k in router):
            pairs = [("Carrier / tech", " ".join(str(router.get(k)) for k in ("cell_carrier", "cell_technology") if router.get(k))),
                     ("Band / EARFCN", " / ".join(str(router.get(k)) for k in ("cell_band", "cell_earfcn") if router.get(k) is not None)),
                     ("Cell ID / PCI / TAC", " / ".join(str(router.get(k)) for k in ("cell_cell_id", "cell_pci", "cell_tac") if router.get(k) is not None)),
                     ("RSRP / RSRQ / SINR", " / ".join(f"{router.get(k)}" for k in ("cell_rsrp", "cell_rsrq", "cell_sinr") if router.get(k) is not None)),
                     ("5G band / RSRP / SINR", " / ".join(f"{router.get(k)}" for k in ("cell_5g_band", "cell_5g_rsrp", "cell_5g_sinr") if router.get(k) is not None)),
                     ("APN", str(router.get("cell_apn") or "")), ("WAN IP", str(router.get("cell_ip") or "")),
                     ("IMEI / ICCID", " / ".join(str(router.get(k)) for k in ("cell_imei", "cell_iccid") if router.get(k))),
                     ("Firmware", str(router.get("cell_firmware") or ""))]
            for k, val in pairs:
                if val:
                    rows.append((k, val, TEXT))
        elif router.get("error"):
            rows.append(("Router query", f"failed: {router['error']}", RED))
        else:
            rows.append(("Status", "no router adapter configured (see docs/ROUTER.md)", MUTED))

        rows.append(("LAST CONNECTIVITY CHECK", "", AMBER))
        if conn:
            gw = conn.get("gateway")
            if gw:
                rows.append((f"Gateway ping {gw.get('target')}", self._ping_text(gw), GREEN if gw.get("received") else RED))
            for p in conn.get("pings", []):
                rows.append((f"Ping {p.get('target')}", self._ping_text(p), GREEN if p.get("received") else RED))
            good = [d for d in conn.get("dns", []) if d.get("addresses")]
            if good:
                rows.append(("DNS", f"OK via {good[0].get('server')} ({fmt_num(good[0].get('ms'), 0)} ms)", GREEN))
            else:
                errs = sorted({d.get("error") for d in conn.get("dns", []) if d.get("error")})
                rows.append(("DNS", "failed: " + (", ".join(errs) or "no servers"), RED))
            for h in conn.get("https", []):
                host = h.get("url", "").split("/")[2] if "//" in h.get("url", "") else h.get("url", "")
                txt = f"OK {h.get('status')} ({fmt_num(h.get('ms'), 0)} ms)" if h.get("ok") else f"failed: {h.get('error')}"
                rows.append((f"HTTPS {host}", txt, GREEN if h.get("ok") else RED))
            rows.append(("Checked", v(snap.get("internet", {}).get("time_iso", "")[11:19]), TEXT))
        else:
            rows.append(("Status", "no check performed yet", MUTED))

        rows.append(("LAST SPEED TEST", "", AMBER))
        if speed:
            rows.append(("Status", v(speed.get("status")), GREEN if speed.get("status") == "SUCCESS" else RED))
            server = ", ".join(x for x in (speed.get("server_name"), speed.get("server_location")) if x)
            if speed.get("server_id") is not None:
                server += f" (id {speed.get('server_id')})"
            rows.append(("Server", v(server) + ("  [pinned]" if speed.get("server_pinned") else ""), TEXT))
            rows.append(("Download / upload", f"{fmt_num(speed.get('download_mbps'), 2)} / {fmt_num(speed.get('upload_mbps'), 2)} Mbps"
                         + ("  (partial)" if speed.get("partial") else ""), TEXT))
            rows.append(("Idle latency / jitter", f"{fmt_num(speed.get('latency_ms'), 1)} / {fmt_num(speed.get('jitter_ms'), 1)} ms", TEXT))
            rows.append(("Packet loss", v(speed.get("packet_loss_pct"), " %"), TEXT))
            rows.append(("Bytes down / up", f"{fmt_bytes(speed.get('download_bytes'))} / {fmt_bytes(speed.get('upload_bytes'))}", TEXT))
            rows.append(("Tool version", v(speed.get("tool_version")), TEXT))
            if speed.get("error"):
                rows.append(("Error", speed.get("error"), RED))
        else:
            rows.append(("Status", "no speed test yet", MUTED))
        if last_row.get("error") and last_row.get("status") != "SUCCESS":
            rows.append(("Latest cycle error", f"{last_row.get('status')}: {last_row.get('error')}", RED))

        rows.append(("SESSION", "", AMBER))
        if session:
            rows.append(("Session ID", session["id"], TEXT))
            rows.append(("Started", session["start_iso"][11:19] + "  elapsed " + clock.hms(snap.get("session_elapsed_s") or 0), TEXT))
            rows.append(("Cycles", f"{session['cycles']} total, {session['successes']} OK, {session['failures']} failed, "
                         f"{session['interrupted']} interrupted", TEXT))
            rows.append(("Consecutive failures", str(session["consecutive_failures"]), RED if session["consecutive_failures"] else TEXT))
            total = fmt_bytes(session["bytes_down"] + session["bytes_up"])
            acct = f"{total} (down {fmt_bytes(session['bytes_down'])}, up {fmt_bytes(session['bytes_up'])})"
            rows.append(("Recorded test traffic", acct, TEXT))
            if session["incomplete_byte_cycles"]:
                rows.append(("", f"incomplete: {plural(session['incomplete_byte_cycles'], 'cycle')} lacked byte totals", AMBER))
            rows.append(("", "Speed-test traffic only, not total cellular usage", MUTED))
        else:
            rows.append(("Status", "no session started", MUTED))
        rows.append(("Clock synced (NTP)", {True: "yes", False: "NO - timestamps unreliable", None: na}[snap.get("clock_synced")],
                     RED if snap.get("clock_synced") is False else TEXT))

        rows.append(("STORAGE / SYSTEM", "", AMBER))
        rows.append(("Log directory", v(extra.get("log_dir")), TEXT))
        rows.append(("Free disk", (f"{storage.get('free_mb')} MB" if storage.get("free_mb") is not None else na),
                     RED if storage.get("low_disk") else TEXT))
        rows.append(("Rows / events written", f"{storage.get('rows_written', 0)} / {storage.get('events_written', 0)}", TEXT))
        if storage.get("last_error"):
            rows.append(("Storage error", storage["last_error"], RED))
        rows.append(("Display", v(extra.get("display")), TEXT))
        rows.append(("Touch", v(extra.get("touch")), TEXT))
        rows.append(("Screen sleep", v(extra.get("sleep")), TEXT))
        rows.append(("Config file", v(extra.get("config_path")), TEXT))
        rows.append(("Version", v(extra.get("version")), TEXT))
        return rows

    @staticmethod
    def _ping_text(p: dict) -> str:
        if p.get("error") and not p.get("received"):
            return f"{p.get('error')} (loss {fmt_num(p.get('loss_pct'), 0)} %)"
        return f"avg {fmt_num(p.get('avg_ms'), 1)} ms, loss {fmt_num(p.get('loss_pct'), 0)} % ({p.get('received', 0)}/{p.get('sent', 0)})"

    ROWS_PER_PAGE = 11
    ROW_H = 20

    def render_details(self, snap: dict, storage: dict, extra: dict, page: int) -> tuple[Image.Image, list[Hit], int]:
        rows = self.details_lines(snap, storage, extra)
        pages = max(1, (len(rows) + self.ROWS_PER_PAGE - 1) // self.ROWS_PER_PAGE)
        page = max(0, min(page, pages - 1))
        img = Image.new("RGB", (self.w, self.h), BG)
        draw = ImageDraw.Draw(img)
        hits: list[Hit] = []
        draw.rectangle((0, 0, self.w, 32), fill=PANEL)
        draw.line((0, 32, self.w, 32), fill=PANEL_EDGE)
        self._text(draw, (8, 16), "Details", 18, TEXT, bold=True, anchor="lm")
        self._text(draw, (self.w - 8, 16), f"Page {page + 1}/{pages}   {clock.now().strftime('%b %d %H:%M:%S')}", 13, MUTED, anchor="rm")
        y = 38
        for key, value, color in rows[page * self.ROWS_PER_PAGE:(page + 1) * self.ROWS_PER_PAGE]:
            if value == "" and color == AMBER:
                self._text(draw, (8, y + 2), key, 13, AMBER, bold=True)
                draw.line((8, y + 18, self.w - 8, y + 18), fill=PANEL_EDGE)
            else:
                if key:
                    self._text(draw, (8, y + 2), key, 13, MUTED, max_w=150)
                self._text(draw, (164, y + 2), value, 13, color, max_w=self.w - 170)
            y += self.ROW_H
        by0, by1 = 262, 312
        wifi = snap.get("wifi") or {}
        on_home = bool(self.home_ssid) and wifi.get("ssid") == self.home_ssid
        same_net = bool(self.home_ssid) and self.home_ssid == self.ssid
        self._button(draw, hits, (8, by0, 92, by1), "Back", "back", BTN_NEUTRAL, 16)
        self._button(draw, hits, (100, by0, 172, by1), "Up", "page_up", BTN_NEUTRAL, 16)
        self._button(draw, hits, (180, by0, 252, by1), "Down", "page_down", BTN_NEUTRAL, 16)
        self._button(draw, hits, (260, by0, 356, by1), "Next Label", "next_label", BTN_NEUTRAL, 14)
        if not self.home_ssid or same_net:
            # Nothing to switch between yet: show why instead of a button that reconnects the same network.
            draw.rounded_rectangle((364, by0, 472, by1), radius=10, fill=(40, 46, 60))
            self._text(draw, (418, by0 + 18), "No test net" if same_net else "No home net", 12, MUTED, bold=True, anchor="mm")
            self._text(draw, (418, by0 + 36), "setup-wifi.sh" if same_net else "wifi --home-current", 10, MUTED, anchor="mm")
        else:
            self._button(draw, hits, (364, by0, 472, by1), "Test Wi-Fi" if on_home else "Home Wi-Fi", "switch_wifi",
                         (40, 90, 150), 14)
        return img, hits, pages

    def render_message(self, title: str, lines: list[str], color=TEXT) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h), BG)
        draw = ImageDraw.Draw(img)
        self._text(draw, (self.w // 2, 60), title, 24, color, bold=True, anchor="mm")
        y = 110
        for line in lines:
            self._text(draw, (self.w // 2, y), line, 15, TEXT, anchor="mm", max_w=self.w - 20)
            y += 24
        return img
