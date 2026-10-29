#!/bin/bash
# Install the official Ookla Speedtest CLI from Ookla's apt repository and accept the
# license once for root (the service user) so unattended runs never block on a prompt.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "run with sudo" >&2; exit 1; fi

if command -v speedtest >/dev/null && speedtest --version 2>/dev/null | grep -qi ookla; then
  echo "Ookla speedtest already installed: $(speedtest --version | head -1)"
else
  echo "== Adding Ookla apt repository (packagecloud.io/ookla/speedtest-cli)"
  apt-get install -y curl gnupg ca-certificates apt-transport-https debian-archive-keyring >/dev/null
  # Ookla's documented one-liner. Review it at https://packagecloud.io/ookla/speedtest-cli/install
  curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh -o /tmp/ookla-repo.sh
  bash /tmp/ookla-repo.sh
  rm -f /tmp/ookla-repo.sh
  # Remove the unofficial python speedtest-cli if present; it shadows the binary name.
  if dpkg -s speedtest-cli >/dev/null 2>&1; then apt-get remove -y speedtest-cli; fi
  apt-get install -y speedtest
fi

echo "== Verifying"
speedtest --version | head -1
# Accept EULA/GDPR for root without running a full test (lists nearby servers only).
speedtest --accept-license --accept-gdpr -L --format=json >/dev/null 2>&1 || true
if speedtest --help 2>/dev/null | grep -q -- '--interface'; then
  echo "   --interface supported: yes"
else
  echo "   WARNING: this speedtest build does not list --interface; set [speedtest] bind_interface=false"
fi
if speedtest --help 2>/dev/null | grep -q 'jsonl'; then
  echo "   jsonl output supported: yes"
else
  echo "   WARNING: jsonl format not listed in --help; check 'speedtest --help' output"
fi
echo "Done. Nearby servers (pick an id for 'pingaroo server <id>' if you want a pinned server):"
speedtest -L 2>/dev/null | head -15 || true
