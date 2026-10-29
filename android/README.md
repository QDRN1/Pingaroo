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

Every sample is tagged with the GPS fix and the location label you type. Turn on
**Log To CSV** to record, **Share CSV** to send the file (email, Drive, etc.). Big
numbers colour by SINR: green 10 dB or better, amber 3 to 10, red under 3.

Tested target: Samsung Galaxy Z Fold, Android 14 or newer. Minimum Android 10.

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

## Notes

- The app contacts only the gateway address you set (default `http://192.168.1.1`).
  No other network traffic, no analytics.
- In 5G NSA, Android often reports the LTE anchor as the registered cell and the NR
  carrier as a secondary cell; the app shows both. Cell id for NR may be blank on some
  firmwares.
- CSV lives in `Android/data/io.qdrn.pingaroo.mobile/files/`. Columns match the
  Pi's `cell_*` columns where they overlap, so the two logs can be joined on time.
