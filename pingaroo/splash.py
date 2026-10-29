"""8-bit boot splash: a pixel-art kangaroo hops in while the service starts."""

from __future__ import annotations

import math
import time

from PIL import Image, ImageDraw

from . import __version__

# Side view, facing right.  '#' body, 'o' eye, 'w' Wi-Fi-pouch glyph, 'p' pouch
KANGAROO = [
    ".........................#...#..",
    ".........................##.##..",
    ".........................#####..",
    "........................#######.",
    ".......................#o######.",
    ".......................#########",
    ".......................######...",
    "........................####....",
    ".......................#####....",
    "......................######....",
    ".....................#######....",
    "....................########....",
    "...................#########....",
    "..................##########.#..",
    ".................###########.##.",
    "................############....",
    "##.............#############....",
    ".###..........####pwwp######....",
    "..####.......#####pppp#####.....",
    "...####....##############.......",
    "....####..##############........",
    ".....###################........",
    "......######......#####.........",
    ".......####........####.........",
    "........####........#####.......",
    ".........#####.......#######....",
    "...........####........#####....",
]

# 5x7 pixel font for the title
GLYPHS = {
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"],
    "G": [".####", "#....", "#....", "#.###", "#...#", "#...#", ".####"],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
}

BG = (12, 10, 36)
SKY = [(12, 10, 36), (26, 18, 64), (52, 24, 84), (120, 48, 90), (220, 110, 70)]
GROUND = (70, 40, 20)
GROUND2 = (110, 62, 28)
FUR = (222, 140, 60)
FUR_DARK = (170, 96, 38)
EYE = (20, 20, 30)
POUCH = (240, 190, 120)
WIFI = (60, 220, 120)
WHITE = (250, 246, 230)
STAR = (255, 240, 180)


def _pixel_text(draw, text, x, y, scale, color):
    for ch in text:
        glyph = GLYPHS.get(ch)
        if glyph:
            for gy, row in enumerate(glyph):
                for gx, c in enumerate(row):
                    if c == "#":
                        draw.rectangle((x + gx * scale, y + gy * scale, x + (gx + 1) * scale - 1,
                                        y + (gy + 1) * scale - 1), fill=color)
        x += 6 * scale


def render_splash(w: int, h: int, t: float, fonts=None, hop: bool = True) -> Image.Image:
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    # banded sunset sky
    band_h = (h - 70) // len(SKY)
    for i, col in enumerate(SKY):
        d.rectangle((0, i * band_h, w, (i + 1) * band_h), fill=col)
    # stars (deterministic) that twinkle
    for k in range(28):
        sx = (k * 97 + 13) % w
        sy = (k * 53 + 7) % (band_h * 2)
        if int(t * 3 + k) % 4:
            d.rectangle((sx, sy, sx + 2, sy + 2), fill=STAR)
    # pixel sun
    cx, cy = w - 90, band_h * 3 + 6
    for r, col in ((26, (255, 170, 60)), (20, (255, 200, 90)), (14, (255, 230, 140))):
        d.rectangle((cx - r, cy - r // 2, cx + r, cy + r // 2), fill=col)
    # ground
    gy = h - 70
    d.rectangle((0, gy, w, h), fill=GROUND)
    for k in range(0, w, 16):
        d.rectangle((k + int(t * 60) % 16, gy, k + int(t * 60) % 16 + 8, gy + 4), fill=GROUND2)
    d.rectangle((0, gy + 4, w, gy + 8), fill=GROUND2)

    # kangaroo hop
    scale = 5
    kw = len(KANGAROO[0]) * scale
    kh = len(KANGAROO) * scale
    hop_y = int(abs(math.sin(t * 4.0)) * 34) if hop else 0
    x0 = 30
    y0 = gy - kh + 6 - hop_y
    # shadow
    sh_w = int(kw * 0.6 - hop_y * 0.6)
    d.rectangle((x0 + 40, gy + 2, x0 + 40 + max(20, sh_w), gy + 6), fill=(50, 28, 14))
    for ry, row in enumerate(KANGAROO):
        for rx, c in enumerate(row):
            if c == ".":
                continue
            col = {"#": FUR, "o": EYE, "p": POUCH, "w": WIFI}[c]
            if c == "#" and (ry > 18 or rx < 8):
                col = FUR_DARK
            d.rectangle((x0 + rx * scale, y0 + ry * scale, x0 + (rx + 1) * scale - 1,
                         y0 + (ry + 1) * scale - 1), fill=col)
    # dust puffs on landing
    if hop and hop_y < 6:
        for k in range(3):
            px = x0 + 60 + k * 18 - int(t * 80) % 10
            d.rectangle((px, gy - 4, px + 4, gy), fill=(150, 110, 70))

    # Wi-Fi arcs rising from the pouch
    arc_phase = int(t * 3) % 4
    ax, ay = x0 + 29 * scale, y0 + 2 * scale
    for i in range(arc_phase):
        r = 8 + i * 8
        d.arc((ax - r, ay - r, ax + r, ay + r), start=225, end=315, fill=WIFI, width=3)

    # title
    _pixel_text(d, "PINGAROO", 232, 40, 5, WHITE)
    _pixel_text(d, "PINGAROO", 229, 37, 5, (40, 30, 60))
    _pixel_text(d, "PINGAROO", 232, 40, 5, WHITE)
    if fonts:
        f = fonts.get(13, True)
        d.text((232, 86), "CELLULAR ROUTER TEST RIG", font=f, fill=STAR)
        d.text((232, 104), f"v{__version__}   hopping onto Wi-Fi" + "." * (int(t * 2) % 4), font=fonts.get(12), fill=WHITE)
        blink = int(t * 2) % 2 == 0
        if blink:
            d.text((w // 2, h - 30), "PRESS START TEST", font=fonts.get(16, True), fill=WHITE, anchor="mm")
    return img


def show_splash(fb, w: int, h: int, fonts, seconds: float = 3.5, fps: float = 12.0) -> None:
    """Blocking animation on the framebuffer; the worker thread keeps running."""
    t0 = time.monotonic()
    frame = 1.0 / fps
    while True:
        t = time.monotonic() - t0
        if t >= seconds:
            break
        try:
            fb.blit(render_splash(w, h, t, fonts))
        except OSError:
            return
        time.sleep(max(0.0, frame - ((time.monotonic() - t0) - t)))
