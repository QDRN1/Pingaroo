#!/bin/bash
# Reach the Pi's screen page from anywhere through a Cloudflare Tunnel, so the laptop
# does not have to join the test Wi-Fi.  See docs/CLOUDFLARE.md for the dashboard steps.
#
#   sudo bash setup-cloudflare.sh --hostname pingaroo.example.com   install and start
#   sudo bash setup-cloudflare.sh --check --hostname pingaroo.example.com
#   sudo bash setup-cloudflare.sh --status
#   sudo bash setup-cloudflare.sh --remove
#
# The screen page has no login of its own.  This script therefore refuses to leave the
# tunnel running when the hostname answers without Cloudflare Access in front of it.
#
# The tunnel token is typed at a prompt and stored only in /etc/pingaroo/tunnel.token
# (root, mode 600).  It is never put on a command line, in the unit file or in git.
set -euo pipefail

UNIT=pingaroo-tunnel.service
UNIT_PATH=/etc/systemd/system/$UNIT
TOKEN_FILE=/etc/pingaroo/tunnel.token
MODE=install
HOST=""

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --hostname) HOST="${2:-}"; shift 2 ;;
    --check) MODE=check; shift ;;
    --status) MODE=status; shift ;;
    --remove) MODE=remove; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo" >&2; exit 1; fi

# What the hostname does when asked from the internet with no login.
#   protected   redirects to a Cloudflare Access sign-in (or refuses outright)
#   open        serves a page to anyone
#   unrouted    no answer through Cloudflare yet (DNS, tunnel down, hostname not added)
classify() {  # $1 = HTTP status, $2 = redirect target
  case "$1" in
    301|302|303|307|308)
      case "$2" in
        https://*.cloudflareaccess.com/*) echo protected ;;
        *) echo "unknown" ;;
      esac ;;
    401|403) echo protected ;;
    2??) echo open ;;
    000|404|5??) echo unrouted ;;
    *) echo "unknown" ;;
  esac
}

probe() {  # prints "<verdict> <status> <redirect>"
  local out code redir
  out=$(curl -s -o /dev/null --max-time 20 -w '%{http_code} %{redirect_url}' "https://$HOST/" 2>/dev/null || true)
  code=${out%% *}; redir=${out#* }
  [ -n "$code" ] || code=000
  echo "$(classify "$code" "$redir") $code $redir"
}

check_access() {  # returns 0 protected, 1 open, 2 could not tell
  local verdict code redir tries=0
  while :; do
    read -r verdict code redir <<<"$(probe)"
    [ "$verdict" != unrouted ] || [ "$tries" -ge 6 ] && break
    tries=$((tries + 1)); sleep 10
  done
  case "$verdict" in
    protected)
      echo "   https://$HOST asks for a Cloudflare Access sign-in (HTTP $code): protected"
      return 0 ;;
    open)
      echo "   https://$HOST answers HTTP $code to anyone, with no sign-in: OPEN TO THE INTERNET"
      return 1 ;;
    *)
      echo "   https://$HOST could not be verified (HTTP $code ${redir:+-> $redir})"
      return 2 ;;
  esac
}

case "$MODE" in
  status)
    systemctl --no-pager --lines=8 status "$UNIT" || true
    exit 0 ;;

  remove)
    systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
    rm -f "$UNIT_PATH" "$TOKEN_FILE"
    systemctl daemon-reload
    echo "Tunnel service and stored token removed. The cloudflared package is left installed"
    echo "(remove it with: sudo apt-get remove -y cloudflared). Delete the tunnel and the Access"
    echo "application in the Cloudflare dashboard as well."
    exit 0 ;;

  check)
    [ -n "$HOST" ] || { echo "--check needs --hostname" >&2; exit 2; }
    rc=0; check_access || rc=$?
    if [ "$rc" -eq 1 ] && systemctl is-active --quiet "$UNIT"; then
      systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
      echo "   Tunnel stopped. Add the Access application (docs/CLOUDFLARE.md step 3), then run"
      echo "   this script again with --hostname $HOST"
    fi
    exit "$rc" ;;
esac

# ------------------------------------------------------------------ install
[ -n "$HOST" ] || { echo "--hostname is required, e.g. --hostname pingaroo.example.com" >&2; exit 2; }
case "$HOST" in
  *[!A-Za-z0-9.-]*|.*|*.|*..*) echo "hostname looks wrong: $HOST" >&2; exit 2 ;;
  *.*) ;;
  *) echo "hostname looks wrong: $HOST" >&2; exit 2 ;;
esac

echo "== 1/4 cloudflared"
if command -v cloudflared >/dev/null; then
  echo "   already installed: $(cloudflared --version 2>/dev/null | head -1)"
else
  arch=$(dpkg --print-architecture)
  case "$arch" in
    arm64|armhf|amd64) ;;
    *) echo "unsupported architecture: $arch" >&2; exit 1 ;;
  esac
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  echo "   downloading the $arch package from github.com/cloudflare/cloudflared (about 18 MB)"
  curl -fsSL --max-time 300 -o "$tmp/cloudflared.deb" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$arch.deb"
  dpkg -i "$tmp/cloudflared.deb" >/dev/null
  echo "   installed: $(cloudflared --version | head -1)"
fi
BIN=$(command -v cloudflared)
if ! "$BIN" tunnel run --help 2>/dev/null | grep -q -- '--token-file'; then
  echo "this cloudflared is too old for --token-file; update it: sudo apt-get install -y cloudflared" >&2
  exit 1
fi

echo "== 2/4 tunnel token"
if [ -s "$TOKEN_FILE" ] && [ -t 0 ]; then
  read -r -p "   A token is already stored. Replace it? [y/N] " ans
else
  ans=y
fi
if [ "${ans:-n}" = y ] || [ "${ans:-n}" = Y ]; then
  # Read without echo so the token stays out of the terminal scrollback and shell history.
  read -r -s -p "   Paste the tunnel token from the Cloudflare dashboard, then press Enter: " token
  echo
  token=${token##* }                      # tolerate a pasted "cloudflared service install <token>"
  case "$token" in
    ""|*[!A-Za-z0-9_=+/-]*) echo "that does not look like a tunnel token" >&2; exit 1 ;;
  esac
  if [ "${#token}" -lt 80 ]; then echo "that is too short to be a tunnel token" >&2; exit 1; fi
  install -d -m 755 /etc/pingaroo
  ( umask 077; printf '%s' "$token" > "$TOKEN_FILE" )
  chmod 600 "$TOKEN_FILE"; chown root:root "$TOKEN_FILE"
  unset token
  echo "   stored in $TOKEN_FILE (root only)"
else
  echo "   keeping the stored token"
fi

echo "== 3/4 service"
cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Pingaroo remote access (Cloudflare Tunnel)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$BIN --no-autoupdate tunnel run --token-file $TOKEN_FILE
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null
systemctl restart "$UNIT"
sleep 8
if ! systemctl is-active --quiet "$UNIT"; then
  echo "   the tunnel did not stay up (wrong token?). Last lines:" >&2
  journalctl -u "$UNIT" --no-pager -n 12 >&2 || true
  exit 1
fi
echo "   $UNIT is running"

echo "== 4/4 is the page behind a sign-in?"
rc=0; check_access || rc=$?
case "$rc" in
  0) echo "Done. Open https://$HOST from any network and sign in." ;;
  1) systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
     echo "The tunnel has been STOPPED: the screen page has no login of its own, so it must not"
     echo "be reachable without Cloudflare Access. Add the Access application for $HOST"
     echo "(docs/CLOUDFLARE.md step 3), then run this script again."
     exit 1 ;;
  *) echo "The tunnel is running but the sign-in could not be confirmed from here. Check the public"
     echo "hostname in the dashboard, then run:"
     echo "  sudo bash $(readlink -f "$0") --check --hostname $HOST" ;;
esac
