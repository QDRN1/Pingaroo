#!/bin/bash
# Install Pingaroo on Raspberry Pi OS (Trixie/Bookworm, 64-bit).
# Run from the repository directory:  sudo ./scripts/install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo" >&2
  exit 1
fi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/opt/pingaroo
CFG_DIR=/etc/pingaroo

echo "== OS / architecture"
. /etc/os-release
echo "   $PRETTY_NAME, $(uname -m), kernel $(uname -r)"
case "${VERSION_CODENAME:-}" in
  trixie|bookworm) ;;
  *) echo "   WARNING: untested release '${VERSION_CODENAME:-?}'. Trixie (13) or Bookworm (12) expected." ;;
esac
if [ "$(uname -m)" != "aarch64" ]; then
  echo "   WARNING: 64-bit (aarch64) Raspberry Pi OS expected; got $(uname -m)."
fi
if ! command -v nmcli >/dev/null; then
  echo "   ERROR: NetworkManager (nmcli) not found. Raspberry Pi OS Bookworm+ ships it; this build does not." >&2
  exit 1
fi

echo "== Packages"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3 python3-pil python3-evdev python3-openpyxl fonts-dejavu-core iw iputils-ping curl ca-certificates gnupg
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required (tomllib)"'
echo "   python $PYV ok"

echo "== Migration from the old 'crwb' name (if present)"
if systemctl list-unit-files crwb.service >/dev/null 2>&1 && [ -f /etc/systemd/system/crwb.service ]; then
  systemctl disable --now crwb.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/crwb.service
  echo "   removed crwb.service"
fi
if [ -f /etc/crwb/config.toml ] && [ ! -f "$CFG_DIR/config.toml" ]; then
  mkdir -p "$CFG_DIR"
  cp /etc/crwb/config.toml "$CFG_DIR/config.toml"
  echo "   config copied from /etc/crwb (calibration kept)"
fi
if [ -d /var/lib/crwb ] && [ ! -d /var/lib/pingaroo ]; then
  mv /var/lib/crwb /var/lib/pingaroo
  echo "   logs moved from /var/lib/crwb"
fi
rm -f /usr/local/bin/crwb
rm -rf /opt/crwb

echo "== Application files -> $APP_DIR"
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/pingaroo"
cp -r "$SRC/pingaroo" "$APP_DIR/pingaroo"
cp "$SRC/README.md" "$APP_DIR/README.md"
cp -r "$SRC/docs" "$APP_DIR/docs"
cp -r "$SRC/tests" "$APP_DIR/tests"
cp -r "$SRC/scripts" "$APP_DIR/scripts"
install -m 0755 "$SRC/scripts/pingaroo" /usr/local/bin/pingaroo
find "$APP_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

echo "== Config -> $CFG_DIR/config.toml"
mkdir -p "$CFG_DIR"
if [ ! -f "$CFG_DIR/config.toml" ]; then
  cp "$SRC/config.example.toml" "$CFG_DIR/config.toml"
  echo "   installed default config"
else
  echo "   existing config kept (example at $SRC/config.example.toml)"
fi
chmod 0644 "$CFG_DIR/config.toml"
pingaroo config >/dev/null && echo "   config validates"

echo "== Data directories"
mkdir -p /var/lib/pingaroo
chmod 0755 /var/lib/pingaroo

echo "== systemd"
install -m 0644 "$SRC/systemd/pingaroo.service" /etc/systemd/system/pingaroo.service
systemctl daemon-reload
systemctl enable pingaroo.service >/dev/null
echo "   pingaroo.service enabled (not started yet)"

echo
echo "Installed. Next steps:"
echo "  1. sudo ./scripts/install-speedtest.sh      # Ookla CLI + license acceptance"
echo "  2. sudo ./scripts/setup-wifi.sh             # WPA3 profile, password prompted"
echo "  3. sudo ./scripts/setup-display.sh          # LCD overlay (then reboot)"
echo "  4. sudo pingaroo calibrate-touch                # after reboot, before starting the service"
echo "  5. sudo systemctl start pingaroo && pingaroo status"
