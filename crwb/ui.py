"""Screen rendering with Pillow for a 480x320 landscape panel.

Two screens: Main (fits without scrolling) and Details (paged).  Each render returns
the image plus a list of hit rectangles so the controller can map taps to actions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from . import clock

# Theme: high contrast on a dark background.
BG = (12, 14, 18)
PANEL = (28, 32, 40)
TEXT = (240, 240, 240)
MUTED = (160, 166, 176)
GREEN = (60, 200, 100)
AMBER = (245, 180, 40)
RED = (235, 80, 70)
BLUE = (80, 150, 240)
BTN_START = (34, 140, 70)
BTN_STOP = (190, 50, 45)
BTN_NEUTRAL = (55, 62, 78)
BTN_TEXT = (255, 255, 255)


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


class Renderer:
    def __init__(self, cfg: dict) -> None:
        d = cfg["display"]
        self.w = int(d["width"])
        self.h = int(d["height"])
        self.fonts = Fonts(d["font"], d["font_bold"])
        self.title = "Router Worker Bee"
        self.ssid = cfg["wifi"]["ssid"]

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
        draw.rounded_rectangle(rect, radius=8, fill=color)
        self._text(draw, ((x0 + x1) // 2, (y0 + y1) // 2), label, size, BTN_TEXT, bold=True, anchor="mm")
        hits.append(Hit(x0, y0, x1, y1, action))

    # ------------------------------------------------------------------ main
    def render_main(self, snap: dict, storage: dict, display_note: str = "") -> tuple[Image.Image, list[Hit]]:
        img = Image.new("RGB", (self.w, self.h), BG)
        draw = ImageDraw.Draw(img)
        hits: list[Hit] = []
        wifi = snap.get("wifi") or {}
        internet = snap.get("internet") or {}
        session = snap.get("session")
        phase = snap.get("phase", "Idle")
        running = snap.get("running", False)
        worker_alive = snap.get("worker_alive", True)

        # Header
        draw.rectangle((0, 0, self.w, 30), fill=PANEL)
        self._text(draw, (8, 15), self.title, 17, TEXT, bold=True, anchor="lm")
        label = snap.get("label") or "-"
        self._text(draw, (self.w - 8, 15), label, 17, AMBER, bold=True, anchor="rm", max_w=250)

        # Network rows
        y = 36
        if wifi.get("connected"):
            wifi_txt, wifi_col = "Connected", GREEN
        elif phase == "Connecting" and running:
            wifi_txt, wifi_col = "Connecting", AMBER
        elif wifi.get("associated"):
            wifi_txt, wifi_col = f"Wrong SSID ({wifi.get('ssid') or '?'})", AMBER
        else:
            wifi_txt, wifi_col = "Disconnected", RED
        self._text(draw, (8, y), "Wi-Fi", 15, MUTED)
        self._text(draw, (78, y), wifi_txt, 15, wifi_col, bold=True, max_w=165)
        ip = wifi.get("ip") or "No IP"
        self._text(draw, (250, y), "IP", 15, MUTED)
        self._text(draw, (280, y), ip, 15, TEXT if wifi.get("ip") else RED, bold=True, max_w=190)

        y = 58
        self._text(draw, (8, y), "Internet", 15, MUTED)
        if internet.get("checking"):
            inet_txt, inet_col = "Checking", AMBER
        elif internet.get("ok") is None:
            inet_txt, inet_col = "Not Checked Yet", MUTED
        else:
            age = clock.age_text(snap.get("internet_age_s"))
            ok = internet.get("ok")
            inet_txt = ("OK" if ok else "Failed") + f" (last check {age})"
            inet_col = GREEN if ok else RED
        self._text(draw, (78, y), inet_txt, 15, inet_col, bold=True, max_w=394)

        # Results block
        speed = snap.get("last_speed") or {}
        previous = snap.get("speed_is_previous", False)
        draw.rectangle((0, 80, self.w, 164), fill=PANEL)
        cols = [(80, "Download Mbps", speed.get("download_mbps")),
                (240, "Upload Mbps", speed.get("upload_mbps")),
                (400, "Ping ms (speedtest idle)", speed.get("latency_ms"))]
        value_col = MUTED if previous else TEXT
        for cx, caption, value in cols:
            self._text(draw, (cx, 92), caption, 13, MUTED, anchor="mm")
            self._text(draw, (cx, 128), fmt_num(value, 1), 36, value_col, bold=True, anchor="mm")
        if speed.get("partial"):
            self._text(draw, (self.w - 6, 158), "partial result", 11, AMBER, anchor="rb")
        if previous:
            self._text(draw, (6, 158), "previous result (new test running)", 11, AMBER, anchor="lb")

        # Phase / countdown / counters row
        y = 170
        if not worker_alive:
            phase_txt, phase_col = "WORKER STOPPED", RED
        elif running:
            phase_txt, phase_col = phase, AMBER if phase != "Waiting" else BLUE
        else:
            phase_txt, phase_col = phase if phase != "Idle" else "Idle", MUTED
        self._text(draw, (8, y), "Phase", 15, MUTED)
        self._text(draw, (78, y), phase_txt, 15, phase_col, bold=True, max_w=200)
        countdown = snap.get("countdown_s")
        if running and countdown is not None and phase == "Waiting":
            self._text(draw, (self.w - 8, y), "Next test in " + clock.mmss(countdown), 15, BLUE, bold=True, anchor="ra")

        y = 192
        if session:
            counters = (f"Cycles {session['cycles']}   OK {session['successes']}   "
                        f"Failed {session['failures']}")
            if session.get("interrupted"):
                counters += f"   Stopped {session['interrupted']}"
        else:
            counters = "No session yet. Tap Start Test."
        self._text(draw, (8, y), counters, 15, TEXT, max_w=self.w - 16)

        # Latest result timestamp / error line
        y = 214
        last_row = snap.get("last_row") or {}
        if speed and snap.get("last_speed_age_s") is not None and not previous:
            when = (last_row.get("end_time") or "")[11:19]
            self._text(draw, (8, y), f"Last speed test {when} ({clock.age_text(snap.get('last_speed_age_s'))})", 13, MUTED,
                       max_w=self.w - 16)
        elif last_row:
            self._text(draw, (8, y), f"Last attempt {(last_row.get('end_time') or '')[11:19]} "
                       f"{last_row.get('status', '')}", 13, MUTED, max_w=self.w - 16)
        else:
            self._text(draw, (8, y), "No results this session", 13, MUTED)

        y = 232
        err = snap.get("last_error") or ""
        storage_err = (storage or {}).get("last_error") or ""
        if storage_err:
            err = f"LOG WRITE FAILED: {storage_err}"
        elif (storage or {}).get("low_disk"):
            err = f"Low disk space: {storage.get('free_mb')} MB free"
        elif snap.get("worker_crash"):
            err = f"Worker crashed: {snap['worker_crash']}"
        elif not snap.get("interface"):
            err = "No Wi-Fi interface detected"
        elif display_note:
            err = display_note
        if err:
            self._text(draw, (8, y), err, 13, RED, max_w=self.w - 16)

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
        na = "unavailable"

        def v(x, suffix=""):
            return na if x in (None, "", []) else f"{x}{suffix}"

        rows: list[tuple[str, str, tuple]] = []
        rows.append(("WI-FI LINK", "", AMBER))
        rows.append(("SSID", v(wifi.get("ssid")), TEXT))
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

        rows.append(("LAST CONNECTIVITY CHECK", "", AMBER))
        if conn:
            gw = conn.get("gateway")
            if gw:
                rows.append((f"Gateway ping {gw.get('target')}", self._ping_text(gw), GREEN if gw.get("received") else RED))
            for p in conn.get("pings", []):
                rows.append((f"Ping {p.get('target')}", self._ping_text(p), GREEN if p.get("received") else RED))
            dns_ok = conn.get("dns_ok")
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
        draw.rectangle((0, 0, self.w, 30), fill=PANEL)
        self._text(draw, (8, 15), "Details", 17, TEXT, bold=True, anchor="lm")
        self._text(draw, (self.w - 8, 15), f"Page {page + 1}/{pages}   {clock.hhmmss()}", 14, MUTED, anchor="rm")
        y = 36
        for key, value, color in rows[page * self.ROWS_PER_PAGE:(page + 1) * self.ROWS_PER_PAGE]:
            if value == "" and color == AMBER:
                self._text(draw, (8, y + 2), key, 13, AMBER, bold=True)
            else:
                if key:
                    self._text(draw, (8, y + 2), key, 13, MUTED, max_w=150)
                self._text(draw, (164, y + 2), value, 13, color, max_w=self.w - 170)
            y += self.ROW_H
        by0, by1 = 262, 312
        self._button(draw, hits, (8, by0, 120, by1), "Back", "back", BTN_NEUTRAL, 18)
        self._button(draw, hits, (132, by0, 220, by1), "Up", "page_up", BTN_NEUTRAL, 18)
        self._button(draw, hits, (232, by0, 320, by1), "Down", "page_down", BTN_NEUTRAL, 18)
        self._button(draw, hits, (332, by0, 472, by1), "Next Label", "next_label", BTN_NEUTRAL, 16)
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
