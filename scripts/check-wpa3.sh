#!/bin/bash
# Report whether this Pi's Wi-Fi driver, firmware and wpa_supplicant can do WPA3-SAE.
# Usage: ./scripts/check-wpa3.sh [iface]
IFACE="${1:-$(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2=="wifi"{print $1; exit}')}"
echo "Interface: ${IFACE:-none}"
[ -n "$IFACE" ] || exit 1
PHY="$(iw dev "$IFACE" info 2>/dev/null | awk '/wiphy/{print "phy"$2}')"
echo "Driver:    $(basename "$(readlink -f /sys/class/net/"$IFACE"/device/driver 2>/dev/null)" 2>/dev/null || echo unknown)"
echo "Firmware:  $(dmesg 2>/dev/null | grep -i 'brcmfmac.*Firmware' | tail -1 | sed 's/.*Firmware: //' || echo 'see dmesg')"
LIST="$(iw ${PHY:+phy $PHY} list 2>/dev/null || iw list 2>/dev/null)"
grep -qiE 'SAE|sae' <<<"$LIST" && echo "SAE:       advertised by driver" || echo "SAE:       NOT listed by 'iw list' (software SAE via external_auth may still work)"
grep -qi 'external_auth' <<<"$LIST" && echo "ExtAuth:   external_auth supported (software SAE path)" || echo "ExtAuth:   not listed"
grep -qiE 'MFP|PMF' <<<"$LIST" && echo "MFP/PMF:   capable" || echo "MFP/PMF:   not listed (WPA3 requires management frame protection)"
echo "Supplicant: $(wpa_supplicant -v 2>&1 | head -1)"
echo "NetworkManager: $(nmcli --version 2>/dev/null)"
echo "Networks advertising WPA3 (scan):"
nmcli -f SSID,SECURITY,SIGNAL,FREQ device wifi list ifname "$IFACE" 2>/dev/null | grep -i wpa3 | sed 's/^/   /' || echo "   none seen"
