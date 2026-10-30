# Pingaroo Mobile (Android)

A no-ads, no-accounts signal meter for walking a building with a 5G phone. One screen,
two sources:

- **Gateway**: reads the AT&T Internet Air (NCM1120) gateway's own modem over its
  Wi-Fi every 2 s (RSRP, SINR, RSRQ, RSSI, band, PCI, TAC, cell id, APN, WAN IP). This
  is ground truth for placing the gateway. Carry the gateway on 12 V power and keep the
  phone on Kangaroo Labs.
- **Phone**: the phone's own 5G NR and LTE measurements from Android. A fast scout
  for which side of the building faces the serving site. Not the same radio as the
  gateway; treat it as a proxy.

**Boot screen**: the same 8-bit kangaroo as the Pi, sunset, stars, hop. Tap to skip.

**Main screen**:
- A verdict banner (EXCELLENT / GOOD / FAIR / POOR / INTERFERENCE) with a 5-bar meter,
  computed from SINR first and RSRP second.
- Signal tiles: RSRP, SINR, RSRQ, RSSI, each with its own colour scale and a level bar.
  RSRP: green -80 or better, lime to -90, amber to -100, red below. SINR: green 20+,
  lime 10+, amber 3+, red below. RSRQ: green -10 or better, amber to -15, red below.
- Cell tiles: tech, band, PCI, cell id, TAC, ARFCN, bandwidth, operator, APN, WAN IP.
- LTE anchor tiles when the device reports one (NSA): band, PCI, cell, RSRP, RSRQ, SNR, EARFCN.
- A rolling sparkline of RSRP (blue) and SINR (amber), last 90 samples.
- **Save Snapshot** asks "Where are you?", then writes one row tagged `event=snapshot`
  with that label and the GPS fix. Use it at each spot you evaluate.
- **Log To CSV** records every 2-second sample continuously; **Share CSV** sends the file.

- **Both** source: polls the gateway and the phone radio together, shows the two 2x2
  blocks stacked, and a comparison row: Δ RSRP and Δ SINR (phone minus gateway) and
  whether both are on the same cell (PCI match). Snapshots and logging record both.
- Context strip: which Wi-Fi the phone is on and whether the gateway answers (with
  round-trip ms), or operator and tech on the Phone source; and "updated Ns ago",
  turning amber past 10 s and red past 30 s.
- Pause/Resume freezes the readings; trend arrows on RSRP and SINR show the change
  since the previous sample.
- Session tiles: elapsed time and sample count, RSRP and SINR min/avg/max, best spot
  so far (highest SINR snapshot).
- Spots screen: every snapshot this session ranked by SINR, best marked, tap for the
  full detail, remove, share.
- Menu (☰): Snapshots, Share CSV, Settings, New session, Check for updates, About.
- Settings: gateway URL, poll interval (1/2/5 s), audio tone that follows signal
  quality (for walking without looking), keep screen on, auto update check.
- **Force 2.4 GHz radio** (menu): asks Android to connect to the gateway's 2.4 GHz
  access point by BSSID and binds the gateway reads to it, for gateways that only
  answer admin requests on 2.4 GHz. Needs the Wi-Fi password in Settings (stored on
  the phone only); Android shows a consent sheet; Release puts things back.
- Error states explain themselves: permission missing or location off get a tap-to-fix.
- Version and build number in the footer and on the boot screen; label, source and
  settings persist across launches.

The tile grid is 3 wide on the cover screen and 4 wide on the inner screen.

Tested target: Samsung Galaxy Z Fold, Android 14 or newer. Minimum Android 10.

## Updates and Signing

The app checks GitHub for a newer `android/releases/version.json` and offers it in a
banner. Tapping it opens the APK in the browser; the browser downloads it and you tap
the download to install. The app never installs packages itself (no install
permission), which keeps Google Play Protect quiet.

Signing: if `android/keystore.properties` (git-ignored) points at a keystore, release
and debug builds are signed with it so builds from any machine install over each
other. Keep that keystore and its properties file outside git; without them a build
falls back to the local debug key, and Android then requires an uninstall to switch.

## Install the Prebuilt APK

1. Copy `releases/pingaroo-mobile-debug.apk` to the phone (download from GitHub, or
   AirDrop-style share from a laptop).
2. Open it. Android asks to allow installs from this source; allow it once.
3. First launch asks for Location and Phone permissions. Location is required by
   Android to read cell identity and signal; nothing is sent anywhere.

## Build It Yourself

Android Studio: File > Open > this `android` folder, connect the phone with USB
debugging on, press Run. Or from a terminal with the Android SDK installed:

```bash
cd android
./gradlew assembleDebug
```

APK lands at `app/build/outputs/apk/debug/app-debug.apk`.

## If the Gateway Source Says "VPN IN THE WAY"

A VPN on the phone (WireGuard, Proton, corporate) captures traffic to 192.168.1.1 and
Android's VPN lockdown blocks apps from binding to Wi-Fi. Disconnect the VPN while
reading the gateway, or exclude 192.168.1.0/24 from the tunnel and turn off "Block
connections without VPN" for that VPN app. The Gateway probe prints "VPN active".

## Notes

- The app contacts only the gateway address you set (default `http://192.168.1.1`).
  No other network traffic, no analytics.
- In 5G NSA, Android often reports the LTE anchor as the registered cell and the NR
  carrier as a secondary cell; the app shows both. Cell id for NR may be blank on some
  firmwares.
- CSV lives in `Android/data/io.qdrn.pingaroo.mobile/files/`. Columns match the
  Pi's `cell_*` columns where they overlap, so the two logs can be joined on time.
