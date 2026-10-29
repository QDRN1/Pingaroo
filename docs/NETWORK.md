# Network: WPA3 Wi-Fi, Routing Isolation, Troubleshooting

## OS and Stack

- Raspberry Pi OS Lite 64-bit, Trixie (Debian 13). Bookworm (12) also works.
  `scripts/install.sh` prints and checks the release and architecture.
- NetworkManager manages all interfaces (default since Bookworm). wpa_supplicant is
  the backend. The app only uses `nmcli`, `iw`, `ip` and `ping`.

## Wi-Fi Profile (WPA3 Personal)

`scripts/setup-wifi.sh` prompts for the password without echo and writes a keyfile:

```
/etc/NetworkManager/system-connections/Kangaroo Labs.nmconnection   (root, mode 600)
```

with `key-mgmt=sae`, `pmf=3` (management frame protection required, mandatory for
WPA3), `autoconnect=true`, `autoconnect-retries=0` (keep trying), IPv4/IPv6 `auto`,
`route-metric=50`, `dns-priority=-10`. The password never passes through a command
line (bash builtin `printf` writes the file), so it is not in `ps`, shell history,
journal or this repository.

To change the password later, run the script again; it replaces the profile.

## Does This Pi 4B Do WPA3?

Facts, with confidence tags:

- [Certain] WPA3 Personal requires SAE and PMF. NetworkManager expresses it as
  `802-11-wireless-security.key-mgmt=sae` and `pmf=3`.
- [Likely] The Pi 4B onboard radio (Cypress/Infineon CYW43455, driver `brcmfmac`)
  connects to WPA3-SAE networks on Raspberry Pi OS Bookworm and newer with current
  firmware, with SAE performed by wpa_supplicant via the driver's external-auth path.
  Reports exist of connections dropping after many hours with some firmware builds.
- [Guessing] Whether the specific firmware in the 2026-04 Trixie image has the
  long-session deauth issue. Watch `events.jsonl` for repeating `WIFI_FAILED` cycles.

Check before relying on it:

```bash
./scripts/check-wpa3.sh
```

It prints the driver, firmware string, whether `iw list` advertises SAE, external
auth and MFP, the wpa_supplicant and NetworkManager versions, and which visible
networks advertise WPA3. Then `sudo ./scripts/setup-wifi.sh` attempts the connection
and shows the result.

## WPA3 Troubleshooting

Symptom: `Secrets were required, but not provided`, `authentication failed`, or
association loops.

1. Update firmware and kernel: `sudo apt update && sudo apt full-upgrade -y && sudo reboot`.
   The Broadcom firmware (`firmware-brcm80211`) is what gained SAE support.
2. Confirm the router advertises WPA3 for the SSID:
   `nmcli -f SSID,SECURITY device wifi list` should show `WPA3` next to `Kangaroo Labs`.
   If it shows `WPA2 WPA3`, the router runs transitional mode and the Pi may pick WPA2.
3. Read the supplicant:
   `journalctl -u NetworkManager -u wpa_supplicant -n 100 --no-pager`. "SAE: ... not
   supported by driver" or "failed to select authenticated key management" means the
   driver/firmware path does not do SAE.
4. Remedies, in order of preference:
   - Update firmware (step 1) and retry.
   - Use iwd as the NetworkManager backend (`sudo apt install iwd`, set
     `wifi.backend=iwd` in `/etc/NetworkManager/conf.d/`), which several users report
     fixes SAE on the Pi 4. Not covered by the install script.
   - Use a USB Wi-Fi adapter with mainline SAE support (MediaTek MT7921AU or
     MT7612U) and set `[wifi] interface = "wlan1"`.
   - Switch the router to WPA2/WPA3 transitional for this test only, and put that in
     the configuration label (`crwb label "Config A WPA2-transition"`) so results stay
     comparable. `sudo ./scripts/setup-wifi.sh --wpa2` writes a WPA2 profile. The app
     never changes security on its own.

Rejected credentials show up as `WIFI_FAILED` with "authentication failed" in the CSV
`error` column, distinct from "SSID not found".

## Routing: All Test Traffic Through the Cellular Router

Goal: ping, DNS, HTTPS and the speed test (including the speed test's own DNS
lookups) use the Wi-Fi to the cellular router, never Ethernet, another Wi-Fi or a VPN.
Management access over Ethernet should keep working.

Layers used:

1. **Default route.** The Wi-Fi profile has `ipv4.route-metric 50`. NetworkManager
   gives Ethernet 100 and other Wi-Fi 600, so while the test Wi-Fi is up it owns the
   default route. Everything without a more specific route, including the speed test
   binary and its DNS, goes through the router.
2. **Exclusive DNS.** `dns-priority=-10` on the Wi-Fi profile makes NetworkManager put
   only the router's DNS servers in `/etc/resolv.conf` while connected. When the Wi-Fi
   is down, Ethernet's DNS returns.
3. **Interface binding.** The app's pings use `ping -I <wifi>`. Its DNS and HTTPS probes
   open sockets with `SO_BINDTODEVICE`, which fails rather than leaking if the
   interface has no route. The speed test receives `--interface <wifi>`.
4. **Verification.** Before the reachability stage and again right before the speed
   test, the app runs `ip route get 1.1.1.1` (and an IPv6 check when the Wi-Fi has a
   global IPv6 address). If the chosen device is not the Wi-Fi, the cycle is
   `ROUTE_FAILED`. After the speed test, if the Wi-Fi dropped, the IP changed, or the
   tool reports another interface, a successful-looking result becomes `ROUTE_FAILED`.

What happens when the Wi-Fi is down and Ethernet is up: Ethernet is the default
route, the Wi-Fi stage fails first (`WIFI_FAILED`), nothing is probed over Ethernet.
If the Wi-Fi is associated but the route check still names `eth0`, you get
`ROUTE_FAILED`, not a false success.

Consequences you accept with this design:

- While the test Wi-Fi is up, the Pi's own outbound traffic (apt, NTP, SSH you
  initiate from the Pi) also goes through the cellular router. SSH *into* the Pi from
  the same Ethernet subnet keeps working because the reply uses the connected route.
  SSH from a different subnet gets replies routed via the cellular router and breaks.
- To keep the Pi reachable from other subnets, give the Ethernet profile a policy
  rule. Simplest strict alternative when management is on the same LAN segment:

```bash
sudo nmcli connection modify "Wired connection 1" ipv4.never-default yes ipv6.never-default yes
```

  Then Ethernet never provides a default route at all, so there is no fallback path
  even when the Wi-Fi is down.

## Timeouts

| Stage | Default | Config key |
|---|---|---|
| Wi-Fi association + DHCP | 45 s | `wifi.connect_timeout_seconds` |
| Reachability stage total | 30 s | `connectivity.total_timeout_seconds` |
| Each ping run | 8 s | `connectivity.ping_timeout_seconds` |
| Each DNS query | 3 s | `connectivity.dns_timeout_seconds` |
| Each HTTPS probe | 8 s | `connectivity.https_timeout_seconds` |
| Speed test | 180 s | `speedtest.timeout_seconds` |

## IPv6

If the router hands out a global IPv6 address, the app also checks that the IPv6
default route is the Wi-Fi. The speed test may then run over IPv6; the result's
`public_ip` shows which. To force IPv4, set `ipv6.method disabled` on the profile:

```bash
sudo nmcli connection modify "Kangaroo Labs" ipv6.method disabled
```

and note it in the label.
