# Router Adapter: Cellular Data From the AT&T Internet Air (NCM1120)

The Pi is a Wi-Fi client. RSRP, RSRQ, SINR, band, cell ID, PCI, APN, IMEI and the rest
exist only inside the router, so Pingaroo reads them from the router's local web
interface. Nothing is invented: with `type = "none"` the cellular columns stay blank.

Router under test: AT&T Internet Air for Business 5G Gateway, model NCM1120D2-D323,
manufactured by Askey (FCC ID H8NNCM1120). Web UI at `http://192.168.1.1` or
`http://attwifi.manager`, username `attadmin`, password `admin` by default (change
it). The user guide documents the pages (Network > Status > Mobile, Network Map >
4G/5G, APN Settings, System Information) but not the JSON endpoints behind them, so
the first step is to capture them once in a browser.

## Confirmed for This Gateway (NCM1120D2-D323)

The API was captured from the gateway web UI. A ready-to-use block is in
`examples/router-ncm1120.toml`; merge its `[router]` and `[router.fields]` into
`/etc/pingaroo/config.toml`. Summary:

- Login: `POST http://192.168.1.1/cgi-bin/Login` with JSON
  `{"UserName":"attadmin","Password":"<sha256 of the admin password>","token":""}`;
  a success returns `{"Success":true,"Login":1,"sessionID":"..."}`. The adapter does
  the SHA-256 for you (`password_hash = "sha256"`); store the plaintext admin
  password with `sudo pingaroo router-password`.
- The session is tracked by the client's IP after login, so the status reads are plain
  GETs with no cookie.
- Cellular data comes from these endpoints (all under `/cgi-bin/`):
  `GetENDCInfo` (5G NR band, RSRP, RSRQ, SINR, RSSI, PCI, TAC, GCI = cell id),
  `GetLTEStatus` (operator, net type, IMEI, IMSI, ICCID, SKU),
  `GetLTEBasicSetting` (APN name, auth, PDP type), `GetWanIp` (cellular WAN IP),
  `GetNvInfo` (firmware, product id, uptime), `GetStatistic` (data volumes).
- Example values seen on this unit: band N77, RSRP -72 dBm, RSRQ -9 dB, SINR ~2 dB,
  RSSI -63 dBm, PCI 265, TAC 17939, cell id 83691327, APN 5gcbroadband, 5G+.
- The sector is not exposed directly. The gNB/cell split from the GCI 83691327:
  with a common cell-per-site factor the GCI yields the gNB id and sector index
  (your RF engineering can confirm the factor for AT&T NSA).

Setup once the Pi can reach the admin page (on 2.4 GHz, see "Band note" below):

```bash
sudo pingaroo router-password
```

```bash
sudo nano /etc/pingaroo/config.toml    # paste the [router] block from examples/router-ncm1120.toml
sudo systemctl restart pingaroo
```

Verify:

```bash
sudo pingaroo router-probe /cgi-bin/GetENDCInfo /cgi-bin/GetLTEStatus
```

**Band note:** on this gateway the admin interface answers only to clients on the
2.4 GHz radio of Kangaroo Labs; 5 GHz clients get an IP and internet but time out on
`192.168.1.1:80`. The Pi associates to whichever band the gateway steers it to. If
`router-probe` times out, the Pi is on 5 GHz. Options: force the Pi's profile to
2.4 GHz, or accept that cellular data is read only when it lands on 2.4 GHz. Set a
fixed band on the Pi's Kangaroo Labs profile with:

```bash
sudo nmcli connection modify "Kangaroo Labs" 802-11-wireless.band bg
sudo systemctl restart pingaroo
```

Note that `band bg` forces 2.4 GHz, which lowers the Wi-Fi link rate and therefore the
speed-test ceiling; record that in the label when you do it.

---

## Step 1: Capture the API Once (Laptop on Kangaroo Labs)

1. Join a laptop to the Kangaroo Labs Wi-Fi. Open Chrome or Edge, press F12,
   select the **Network** tab, tick **Preserve log**, filter **Fetch/XHR**.
2. Browse to `http://192.168.1.1`, log in.
3. In the Network tab find the login request. Note its **path** (for example
   `/cgi-bin/luci/api/login` or `/goform/login`), the **method**, and the **form
   field names** for username and password. Do not copy the password value.
4. Open Network > Status > Mobile and Network Map > 4G/5G. Each page makes one or
   more JSON requests. For each: right-click the request, **Copy > Copy as cURL**, and
   also open the **Response** tab and copy the JSON body.
5. Paste the request paths, the login details (fields only) and the JSON bodies into
   the chat. The adapter mapping is then a few config lines.

If the UI uses HTTP basic auth instead of a form, the login request will show an
`Authorization: Basic` header; use `login_method = "basic"`.

## Step 2: Store the Password on the Pi

```bash
sudo pingaroo router-password
```

Writes `/etc/pingaroo/router.secret`, root only, mode 600. Never put it in the config.

## Step 3: Probe From the Pi (Must Be on Kangaroo Labs)

```bash
sudo pingaroo wifi --switch test
```

```bash
sudo pingaroo router-probe --no-login /api/system/info
```

Replace the path with what the browser showed. With a login configured in
`[router]`, drop `--no-login`. The command prints the JSON so field names can be
mapped. Try the obvious candidates if the browser capture is unavailable:
`/api/status`, `/api/mobile`, `/cgi-bin/status.cgi`, `/data/status.json`.

## Step 4: Map Fields

Example (paths are illustrative until captured):

```toml
[router]
type = "http_json"
base_url = "http://192.168.1.1"
username = "attadmin"
login_path = "/cgi-bin/login"
login_method = "form"
login_fields = { username = "username", password = "password" }
status_paths = ["/api/mobile/status", "/api/system/info"]

[router.fields]
cell_carrier    = "mobile.operator"
cell_technology = "mobile.network_type"
cell_band       = "mobile.lte.band"
cell_earfcn     = "mobile.lte.earfcn"
cell_cell_id    = "mobile.lte.cell_id"
cell_pci        = "mobile.lte.pci"
cell_rsrp       = "mobile.lte.rsrp"
cell_rsrq       = "mobile.lte.rsrq"
cell_sinr       = "mobile.lte.sinr"
cell_5g_band    = "mobile.nr.band"
cell_5g_rsrp    = "mobile.nr.rsrp"
cell_5g_sinr    = "mobile.nr.sinr"
cell_apn        = "mobile.apn"
cell_ip         = "mobile.wan_ip"
cell_imei       = "page:/api/system/info:imei"
cell_firmware   = "page:/api/system/info:firmware_version"
```

`page:<path>:<json.path>` reads from one specific response; a bare path reads from
the merged responses. Values like `"-95 dBm"` are converted to numbers. Then:

```bash
sudo systemctl restart pingaroo
```

Every cycle now records the cellular columns in the CSV and the full raw JSON under
`detail.router.raw` in `events.jsonl`, so engineering gets everything the router
reported even if it was not mapped to a column. The Details screen shows a
"Cellular (from router)" section.

## What Engineering Gets Per Cycle

Wi-Fi link (BSSID, dBm, band, channel, link rate), gateway and public IP, pings to
two targets with loss, DNS and HTTPS outcomes, Ookla server, download, upload, idle
latency, jitter, loss, bytes, result URL, plus the cellular set above and the raw
router JSON. `pingaroo export` turns it into an Excel workbook with a per-label
summary (attempts, success rate, mean/median/min/max down, up, latency, Wi-Fi dBm,
RSRP).

## Not Possible From the Pi

- Sector-level data (sector ID, antenna azimuth) is not exposed by consumer
  gateways; cell ID and PCI are the closest, and the eNB/gNB ID plus sector index
  can be derived from the cell ID (eNB ID = cell ID / 256, sector = cell ID % 256 for LTE).
- Neighbour-cell measurements, unless the Askey UI happens to return them.
- Anything while the Pi is on the home Wi-Fi: the router's LAN is only reachable
  when the Pi is on Kangaroo Labs.
