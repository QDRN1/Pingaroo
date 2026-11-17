# Reaching the Pi Through Cloudflare

The screen page (`http://<pi address>:8080`) normally needs the laptop on the same network
as the Pi. A Cloudflare Tunnel publishes that page at a hostname of yours, so it opens from
any network without switching Wi-Fi.

## What This Does And Does Not Do

- The Pi makes an outbound connection to Cloudflare. Nothing is opened on the gateway and it
  works behind carrier NAT.
- The tunnel rides the link under test. **When the cellular link is down, the tunnel is down
  too.** It is not a way to repair a broken APN; for that the laptop still has to join the
  gateway's Wi-Fi (or the Pi's radio has to be on the home network, see below).
- The tunnel's own traffic is small (keepalives). Watching the page moves more: a screen
  image about once a second. Close the page during a run you care about.
- The screen page has **no login of its own**. Cloudflare Access is the login. The setup
  script stops the tunnel if the hostname answers without it.

At home there is a simpler path that needs no Cloudflare and survives a broken APN: test over
an Ethernet cable (see `docs/NETWORK.md`) and leave the Pi's Wi-Fi radio on the home network.
The Pi then answers on its home address while it tests the gateway over the cable.

## Setup

Steps 1 to 3 are in the Cloudflare dashboard (Zero Trust). Menu names move between dashboard
versions; the objects are a **tunnel**, a **public hostname** on it and an **Access
application**.

### 1. Create The Tunnel

Zero Trust > Networks > Tunnels > Create a tunnel > Cloudflared. Name it `pingaroo`. The page
shows an install command ending in a long token that starts with `eyJ`. Copy the token. Do
not paste it into chat, a file in this repo or a commit.

### 2. Add The Public Hostname

On the same tunnel, Public Hostname > Add:

| Field | Value |
|---|---|
| Subdomain / Domain | the hostname you want, for example `pingaroo.example.com` |
| Service Type | `HTTP` |
| URL | `localhost:8080` |

### 3. Put Access In Front Of It

Zero Trust > Access > Applications > Add an application > Self-hosted.

| Field | Value |
|---|---|
| Application Domain | the same hostname, no path |
| Policy Action | `Allow` |
| Include | `Emails` = your own address (one-time PIN sign-in works with no other setup) |

Do this before step 4. Without it the page, and every button on it, is open to anyone who
finds the hostname.

### 4. Start The Tunnel On The Pi

```bash
sudo bash /home/pingaroo/pingaroo-src/scripts/setup-cloudflare.sh --hostname pingaroo.example.com
```

The script installs `cloudflared`, asks for the token (typed input is hidden), stores it in
`/etc/pingaroo/tunnel.token` readable by root only, starts `pingaroo-tunnel.service`, then
asks the hostname from the outside:

- A redirect to a Cloudflare Access sign-in: done.
- A page with no sign-in: the tunnel is stopped and the script says so. Fix step 3 and run it
  again.
- No answer yet: the tunnel is left running and the script prints the check command.

### 5. Check, Status, Remove

```bash
sudo bash /home/pingaroo/pingaroo-src/scripts/setup-cloudflare.sh --check --hostname pingaroo.example.com
sudo bash /home/pingaroo/pingaroo-src/scripts/setup-cloudflare.sh --status
sudo bash /home/pingaroo/pingaroo-src/scripts/setup-cloudflare.sh --remove
```

`--remove` stops the service and deletes the stored token. Delete the tunnel and the Access
application in the dashboard as well.

## Notes

- The tunnel service is separate from `pingaroo.service`. Installing or updating Pingaroo
  does not touch it, and removing the tunnel does not touch testing or logs.
- The check only looks at the hostname given. If you add other public hostnames to the same
  tunnel, each needs its own Access application.
- SSH can go through the same tunnel (a second public hostname with Service Type `SSH`,
  URL `localhost:22`, and its own Access application). It is not set up by the script.
