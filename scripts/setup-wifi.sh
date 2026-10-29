#!/bin/bash
# Create (or replace) the NetworkManager profile for the cellular router's WPA3 Wi-Fi.
# The password is prompted (not echoed), written only into NetworkManager's root-only
# keyfile store, and never appears in argv, shell history, logs or this repo.
#
#   sudo ./scripts/setup-wifi.sh [--ssid "Kangaroo Labs"] [--name "Kangaroo Labs"] [--iface wlan0]
#                                [--wpa2]   # transitional fallback (see docs/NETWORK.md)
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "run with sudo" >&2; exit 1; fi

SSID="Kangaroo Labs"
NAME=""
IFACE=""
KEYMGMT="sae"
while [ $# -gt 0 ]; do
  case "$1" in
    --ssid) SSID="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --iface) IFACE="$2"; shift 2 ;;
    --wpa2) KEYMGMT="wpa-psk"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
NAME="${NAME:-$SSID}"

if [ -z "$IFACE" ]; then
  IFACE="$(nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="wifi"{print $1; exit}')"
fi
if [ -z "$IFACE" ]; then echo "no Wi-Fi interface found (nmcli device status)" >&2; exit 1; fi
echo "Interface: $IFACE   SSID: $SSID   Profile: $NAME   Security: $([ "$KEYMGMT" = sae ] && echo 'WPA3 Personal (SAE)' || echo 'WPA2 Personal (PSK)')"

# Prompt without echo; read twice to catch typos.
read -r -s -p "Wi-Fi password: " PSK; echo
read -r -s -p "Repeat password: " PSK2; echo
if [ "$PSK" != "$PSK2" ]; then echo "passwords differ" >&2; exit 1; fi
if [ "${#PSK}" -lt 8 ] || [ "${#PSK}" -gt 63 ]; then echo "WPA passphrase must be 8..63 characters" >&2; exit 1; fi

FILE="/etc/NetworkManager/system-connections/${NAME}.nmconnection"
UUID="$(cat /proc/sys/kernel/random/uuid)"
# Remove any older profile of the same name so there is exactly one.
if nmcli -t -f NAME connection show | grep -Fxq "$NAME"; then
  nmcli connection delete "$NAME" >/dev/null || true
fi
umask 077
# bash builtin printf: the passphrase never becomes a process argument.
printf '%s\n' \
"[connection]" \
"id=${NAME}" \
"uuid=${UUID}" \
"type=wifi" \
"interface-name=${IFACE}" \
"autoconnect=true" \
"autoconnect-retries=0" \
"autoconnect-priority=50" \
"" \
"[wifi]" \
"mode=infrastructure" \
"ssid=${SSID}" \
"" \
"[wifi-security]" \
"key-mgmt=${KEYMGMT}" \
"pmf=$([ "$KEYMGMT" = sae ] && echo 3 || echo 1)" \
"psk=${PSK}" \
"" \
"[ipv4]" \
"method=auto" \
"# Lower metric than Ethernet (100) so this Wi-Fi is the default route when up." \
"route-metric=50" \
"# Negative priority: while connected, only this connection's DNS servers are used." \
"dns-priority=-10" \
"" \
"[ipv6]" \
"addr-gen-mode=default" \
"method=auto" \
"route-metric=50" \
"dns-priority=-10" \
> "$FILE"
chmod 600 "$FILE"
chown root:root "$FILE"
unset PSK PSK2
nmcli connection reload
echo "Profile written to $FILE (root-only)."

echo "== Driver / supplicant WPA3 capability"
"$(dirname "$0")/check-wpa3.sh" "$IFACE" || true

echo "== Attempting connection (45 s)"
if nmcli -w 45 connection up "$NAME" ifname "$IFACE"; then
  nmcli -t -f GENERAL.STATE,IP4.ADDRESS,IP4.GATEWAY device show "$IFACE"
  iw dev "$IFACE" link | sed 's/^/   /'
  echo "Connected. Run: crwb check-wifi"
else
  echo "Connection failed. See docs/NETWORK.md 'WPA3 troubleshooting'. Useful:"
  echo "   journalctl -u NetworkManager -u wpa_supplicant -n 80 --no-pager"
  echo "   nmcli -f SSID,SECURITY,SIGNAL device wifi list ifname $IFACE"
  exit 1
fi
