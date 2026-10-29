# Display Setup: 3.5-inch 480x320 SPI Panel with XPT2046 Touch

## Identify the LCD Controller First

The board is marked "Display-F" and "XPT2046 Touch Controller". XPT2046 only names the
touch chip (Linux driver `ads7846`). The LCD controller is separate and decides which
overlay works. Nearly all 3.5-inch 480x320 Pi hats are ILI9486; a few are ILI9488 or
ST7796S. Ways to confirm, in order of reliability:

1. Seller page or box: look for "ILI9486", "ILI9488" or "ST7796".
2. Silkscreen or sticker on the PCB: "MPI3501", "MHS35", "3.5inch RPi Display (A)"
   all mean ILI9486 variants. "Kedei" boards are a different, non-fbtft design.
3. Try the stock overlay below and read `dmesg`. The fbtft driver prints the
   controller name it initialised, or an error if the panel does not respond.

Stock Raspberry Pi overlays that matter here (from `/boot/firmware/overlays/README`):

| Overlay | LCD driver | Touch | Notes |
|---|---|---|---|
| `piscreen` | ILI9486 (fbtft `fb_ili9486`, or DRM with `drm`) | ADS7846/XPT2046 included | Params: `speed`, `rotate`, `fps`, `debug`, `xohms`, `drm`, `invx`, `invy`, `swapxy` |
| `mipi-dbi-spi` | Generic DRM `panel-mipi-dbi` (needs a firmware init file) | none, add `ads7846` separately | For non-ILI9486 panels |
| `ads7846` | n/a | XPT2046 | Params: `cs`, `speed`, `penirq`, `xohms`, `swapxy`, `invx`, `invy`, `xmin`, `xmax`, ... |

Vendor "LCD-show" repositories (waveshare35a, MHS35, tft35a) are not stock. Do not run
their install scripts: they overwrite `config.txt`, `cmdline.txt`, install X11 pieces
and legacy fbcp. If you need one of those overlays, copy only the `.dtbo` file into
`/boot/firmware/overlays/` and reference it with `--overlay <name>` below.

## Enable the Panel

```bash
sudo ./scripts/setup-display.sh && sudo reboot
```

This adds to `/boot/firmware/config.txt`:

```
dtparam=spi=on
dtoverlay=piscreen,speed=16000000,rotate=90,fps=30
```

and `vt.global_cursor_default=0` to `cmdline.txt` (no blinking console cursor on the
LCD when the LCD is the only framebuffer).

After reboot:

```bash
dmesg | grep -iE 'fb_|fbtft|ili9486|ads7846|panel|spi'
```

Expected (fbtft): `fb_ili9486 spi0.0: fbtft_property_value: ...`, `graphics fb1: fb_ili9486 frame buffer, 480x320, 16 bpp`
(or `fb0` when no HDMI is attached), and `ads7846 spi0.1: touchscreen, irq N`.

```bash
crwb check-display --test
```

Draws red, green, blue and white bars on the top half and a yellow box with text on the
bottom half. Text must be readable top-left of the box; if it is upside down use
`--rotate 270`, if portrait use `--rotate 0` or `180` (then the app would need
320x480, which is not the target).

If `dmesg` shows `fb_ili9486 ... init failed` or the panel stays white:
- Try `--speed 8000000` (long ribbon or clone boards).
- The controller is not ILI9486: identify it and use `mipi-dbi-spi` or the vendor
  `.dtbo` for your exact board.
- Re-seat the hat; SPI0 CE0 is the LCD, CE1 is touch on these boards.

DRM alternative: `sudo ./scripts/setup-display.sh --drm` uses the kernel DRM panel
driver instead of fbtft. The app still finds the panel through DRM's `/dev/fbN`
emulation (32 bpp is handled). Use it if fbtft is deprecated on your kernel.

## HDMI and the Console

With HDMI attached, the console stays on `/dev/fb0` (HDMI) and the LCD is `/dev/fb1`.
Without HDMI the LCD becomes `/dev/fb0` and the text console shares it. The app detects
that case (`console_graphics_mode = "auto"`) and switches tty1 to graphics mode while
running, restoring text mode on exit. Set `"never"` if you want the console visible.

The app picks the framebuffer whose size is 480x320, else the first fbtft/DRM panel by
name. Override with `[display] framebuffer = "/dev/fb1"`.

## Touch Calibration

The ads7846 driver reports raw 12-bit coordinates. Axis orientation depends on how the
panel is rotated. Stop the service and run the 4-point calibration:

```bash
sudo systemctl stop crwb
```

```bash
sudo crwb calibrate-touch
```

Tap each cross once. The script derives `swap_xy`, `invert_x`, `invert_y` and the raw
min/max, writes them into `/etc/crwb/config.toml` `[touch]`, and prints them.

```bash
sudo crwb check-touch
```

prints mapped coordinates for 20 s: top-left of the panel should read near `x=0 y=0`,
bottom-right near `x=479 y=319`. Then:

```bash
sudo systemctl start crwb
```

If taps feel offset by a few pixels, `xohms` in the overlay line (60 to 100 is typical
for these boards) changes pressure sensitivity; calibration numbers do not.

Touch controller selection: the app picks the evdev device whose name contains
`ADS7846`; override with `[touch] device = "/dev/input/event0"` or change
`device_name_match`.

## Screen Sleep and Backlight

Most 3.5-inch hats wire the backlight directly to 3.3 V: there is no kernel backlight
device, so sleep means a black frame with the backlight still lit. The app tries, in
order: `/sys/class/backlight/*` (if the overlay defines one), the framebuffer's
`blank` attribute (fbtft usually rejects it), then a black frame. The Details view
"Screen sleep" line reports which method is active.

The first touch after sleep only wakes the screen; the whole gesture is consumed so a
button under the finger does not fire. The screen redraws with current state. Testing
and logging never pause for sleep. Background screen refreshes do not reset the
inactivity timer.

## Verified Versus Assumed

- Verified on the Pi: nothing yet (hardware not available to the build environment).
- Verified in software: RGB565 and XRGB8888 framebuffer encoding, framebuffer and
  touch device selection logic, calibration mapping, tap/debounce/sleep/wake logic.
- Assumed, needs confirmation on the real board: that the panel is ILI9486 and
  responds to the `piscreen` overlay; that touch-to-wake works with the ads7846 driver
  (it should, since wake is driven by any BTN_TOUCH event).
