#!/bin/bash
# Enable the 3.5" SPI LCD (ILI9486 + XPT2046/ADS7846) via the stock `piscreen` overlay.
#   sudo ./scripts/setup-display.sh                # fbtft driver, rotate=90 landscape
#   sudo ./scripts/setup-display.sh --rotate 270   # flipped landscape
#   sudo ./scripts/setup-display.sh --drm          # DRM/KMS panel driver instead of fbtft
#   sudo ./scripts/setup-display.sh --overlay waveshare35a   # vendor overlay already in /boot/firmware/overlays
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "run with sudo" >&2; exit 1; fi
ROTATE=90
OVERLAY=piscreen
SPEED=16000000
EXTRA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rotate) ROTATE="$2"; shift 2 ;;
    --overlay) OVERLAY="$2"; shift 2 ;;
    --speed) SPEED="$2"; shift 2 ;;
    --drm) EXTRA=",drm"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
CFG=/boot/firmware/config.txt
[ -f "$CFG" ] || CFG=/boot/config.txt
CMD=/boot/firmware/cmdline.txt
[ -f "$CMD" ] || CMD=/boot/cmdline.txt
cp "$CFG" "$CFG.crwb-backup-$(date +%Y%m%d%H%M%S)"

# Remove earlier crwb display lines, then append the new block.
sed -i '/^# crwb display begin/,/^# crwb display end/d' "$CFG"
if [ "$OVERLAY" = piscreen ]; then
  LINE="dtoverlay=piscreen,speed=${SPEED},rotate=${ROTATE},fps=30${EXTRA}"
else
  LINE="dtoverlay=${OVERLAY},rotate=${ROTATE},speed=${SPEED},fps=30"
fi
cat >> "$CFG" <<EOT
# crwb display begin
dtparam=spi=on
${LINE}
# crwb display end
EOT
echo "Added to $CFG:"
echo "   $LINE"

# Keep the console cursor from blinking over the UI if the LCD is the only framebuffer.
if ! grep -q 'vt.global_cursor_default=0' "$CMD"; then
  sed -i '1 s/$/ vt.global_cursor_default=0/' "$CMD"
  echo "Added vt.global_cursor_default=0 to $CMD"
fi
echo
echo "Reboot, then verify:"
echo "   dmesg | grep -iE 'fb_|fbtft|ili9486|ads7846|panel'"
echo "   crwb check-display --test"
echo "   sudo crwb check-touch"
echo "If nothing appears, see docs/DISPLAY.md to identify the LCD controller."
