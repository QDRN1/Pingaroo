#!/bin/bash
# Publish a new Pingaroo Mobile build: copies the debug APK into releases/ and writes
# version.json from the values in app/build.gradle.kts.  Run after `gradle assembleDebug`.
#   ./release.sh "What changed"
set -euo pipefail
cd "$(dirname "$0")"
NOTES="${1:-}"
APK=app/build/outputs/apk/debug/app-debug.apk
[ -f "$APK" ] || { echo "build first: gradle assembleDebug" >&2; exit 1; }
CODE=$(grep -E '^\s*versionCode' app/build.gradle.kts | grep -oE '[0-9]+')
NAME=$(grep -E '^\s*versionName' app/build.gradle.kts | grep -oE '"[^"]+"' | tr -d '"')
cp "$APK" releases/pingaroo-mobile-debug.apk
cat > releases/version.json <<EOT
{
  "versionCode": $CODE,
  "versionName": "$NAME",
  "apk": "android/releases/pingaroo-mobile-debug.apk",
  "notes": "$NOTES"
}
EOT
echo "releases/version.json -> v$NAME ($CODE); commit and push android/releases/"
