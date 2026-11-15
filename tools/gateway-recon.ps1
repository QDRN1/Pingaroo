#requires -Version 7
<#
.SYNOPSIS
  Read-only capture of the AT&T Internet Air gateway (NCM1120) web UI and status, run
  from a Windows laptop that has to leave its home Wi-Fi to reach the gateway.

.DESCRIPTION
  The gateway's admin page (http://192.168.1.1) is only reachable from its own Wi-Fi.
  This script:
    1. starts an independent watchdog that forces the laptop back to the home Wi-Fi
       after -WatchdogSeconds, whatever happens to this script;
    2. joins the gateway Wi-Fi;
    3. logs in (optional) and downloads the web UI's pages and scripts;
    4. reads status endpoints whose names start with "Get";
    5. rejoins the home Wi-Fi and writes a zip to the Downloads folder.
  It never sends a setting to the gateway.  The only POSTs are Login and, when the web
  UI names one, Logout.  The password is typed at a prompt, held in memory, and never
  written to disk.  Serials, keys, tokens and session ids are masked in saved responses.

.EXAMPLE
  pwsh -ExecutionPolicy Bypass -File .\gateway-recon.ps1 -Check
  Changes nothing: reports Wi-Fi state, saved profiles and where output will go.

.EXAMPLE
  pwsh -ExecutionPolicy Bypass -File .\gateway-recon.ps1
  Full run.  A remote-desktop session drops for one to three minutes on a Wi-Fi-only laptop.
#>
[CmdletBinding()]
param(
    [string]$GatewaySsid = "Kangaroo Labs",
    [string]$HomeSsid = "",
    [string]$Gateway = "192.168.1.1",
    [string]$User = "attadmin",
    [int]$WatchdogSeconds = 300,
    [int]$BudgetSeconds = 90,
    [switch]$Check,
    [switch]$NoLogin,
    [switch]$SkipWifi
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir = Join-Path $env:TEMP "pingaroo-gateway-recon-$stamp"
$siteDir = Join-Path $outDir 'site'
$apiDir = Join-Path $outDir 'api'
$logFile = Join-Path $outDir 'run.log'
New-Item -ItemType Directory -Force -Path $siteDir, $apiDir | Out-Null

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

function Invoke-Netsh([string]$argLine) {
    # Raw argument string, so name="My SSID" reaches netsh exactly as typed in cmd.
    $psi = [System.Diagnostics.ProcessStartInfo]::new('netsh.exe', $argLine)
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $o = $p.StandardOutput.ReadToEnd()
    $p.WaitForExit()
    return $o
}

function Get-WifiState {
    $out = Invoke-Netsh 'wlan show interfaces'
    $ssid = ''
    $state = ''
    if ($out -match '(?m)^\s*SSID\s*:\s*(.+?)\s*$') { $ssid = $Matches[1] }
    if ($out -match '(?m)^\s*State\s*:\s*(.+?)\s*$') { $state = $Matches[1] }
    return [pscustomobject]@{ Ssid = $ssid; State = $state }
}

function Test-WifiProfile([string]$name) {
    $out = Invoke-Netsh 'wlan show profiles'
    return ($out -match (':\s*' + [regex]::Escape($name) + '\s*(\r?\n|$)'))
}

function Connect-Wifi([string]$name, [int]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    $nextTry = Get-Date
    while ((Get-Date) -lt $deadline) {
        $s = Get-WifiState
        if ($s.Ssid -eq $name -and $s.State -eq 'connected') { return $true }
        if ((Get-Date) -ge $nextTry) {
            Invoke-Netsh ('wlan connect name="{0}"' -f $name) | Out-Null
            $nextTry = (Get-Date).AddSeconds(10)
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Test-Tcp([string]$hostName, [int]$port, [int]$ms = 3000) {
    $c = [System.Net.Sockets.TcpClient]::new()
    try {
        $t = $c.ConnectAsync($hostName, $port)
        if ($t.Wait($ms) -and $c.Connected) { return $true }
        return $false
    } catch { return $false } finally { $c.Dispose() }
}

function Test-Internet {
    # Does the GATEWAY have working internet? Run while joined to its Wi-Fi.  Read-only:
    # these are ordinary client requests to public hosts, bound to the Wi-Fi like the Pi does.
    $out = [ordered]@{}
    # DNS
    try { $a = [System.Net.Dns]::GetHostAddresses('www.cloudflare.com'); $out.dns = "ok ($($a.Count) addrs)" } catch { $out.dns = "FAIL: $($_.Exception.Message)" }
    # TCP 443 to three well-known hosts
    foreach ($h in '1.1.1.1', '8.8.8.8', 'www.google.com') { $out["tcp443_$h"] = if (Test-Tcp $h 443 3000) { 'ok' } else { 'FAIL' } }
    # small HTTPS GET + throughput sample (10 MB from Cloudflare)
    foreach ($u in 'https://www.google.com/generate_204', 'https://1.1.1.1/') {
        try {
            $t0 = Get-Date
            $r = Invoke-WebRequest -Uri $u -TimeoutSec 10 -NoProxy -SkipHttpErrorCheck -MaximumRedirection 2 -ErrorAction Stop
            $out["https $u"] = "HTTP $([int]$r.StatusCode) in $([int]((Get-Date)-$t0).TotalMilliseconds) ms"
        } catch { $out["https $u"] = "FAIL: $($_.Exception.Message)" }
    }
    try {
        $t0 = Get-Date
        $r = Invoke-WebRequest -Uri 'https://speed.cloudflare.com/__down?bytes=10000000' -TimeoutSec 25 -NoProxy -SkipHttpErrorCheck -ErrorAction Stop
        $bytes = 0; if ($r.RawContentLength -gt 0) { $bytes = $r.RawContentLength } elseif ($r.Content) { $bytes = ([byte[]]$r.Content).Length }
        $sec = ((Get-Date) - $t0).TotalSeconds
        if ($sec -gt 0 -and $bytes -gt 0) { $out.download = "{0:N1} Mbps ({1:N1} MB in {2:N1}s)" -f (($bytes*8)/($sec*1e6)), ($bytes/1MB), $sec }
        else { $out.download = "HTTP $([int]$r.StatusCode), $bytes bytes" }
    } catch { $out.download = "FAIL: $($_.Exception.Message)" }
    return $out
}

function Protect-Text([string]$text) {
    # Mask values of sensitive keys in JSON text; key names stay visible.
    $pattern = '"(?<k>[^"]*(?:pass|psk|key|imei|imsi|iccid|msisdn|session|token|serial)[^"]*|sn|SN)"\s*:\s*"(?<v>[^"]*)"'
    $evaluator = [System.Text.RegularExpressions.MatchEvaluator] {
        param($m)
        '"' + $m.Groups['k'].Value + '":"<masked ' + $m.Groups['v'].Value.Length + ' chars>"'
    }
    return [regex]::Replace($text, $pattern, $evaluator, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
}

$session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()

function Get-Gw([string]$path, [string]$method = 'Get', [string]$body = $null, [int]$timeoutSec = 8) {
    $u = "http://$Gateway/" + $path.TrimStart('/')
    $iwr = @{
        Uri = $u; Method = $method; TimeoutSec = $timeoutSec; WebSession = $session
        SkipHttpErrorCheck = $true; NoProxy = $true; ErrorAction = 'Stop'
        Headers = @{ 'X-Requested-With' = 'XMLHttpRequest' }
    }
    if ($body) { $iwr.Body = $body; $iwr.ContentType = 'application/json' }
    try {
        $r = Invoke-WebRequest @iwr
        $content = $r.Content
        if ($content -is [byte[]]) { $content = [System.Text.Encoding]::UTF8.GetString($content) }
        return [pscustomobject]@{ Status = [int]$r.StatusCode; Text = [string]$content }
    } catch {
        return [pscustomobject]@{ Status = 0; Text = "ERROR: $($_.Exception.Message)" }
    }
}

# ------------------------------------------------------------------ preflight
$wifi0 = Get-WifiState
if (-not $HomeSsid) {
    if ($wifi0.Ssid -and $wifi0.Ssid -ne $GatewaySsid) { $HomeSsid = $wifi0.Ssid } else { $HomeSsid = 'LAN Down Under' }
}
$downloads = Join-Path $env:USERPROFILE 'OneDrive - QDRN\Downloads'
if (-not (Test-Path $downloads)) { $downloads = Join-Path $env:USERPROFILE 'Downloads' }
$zipPath = Join-Path $downloads "pingaroo-gateway-recon-$stamp.zip"

Log "PowerShell $($PSVersionTable.PSVersion)  Wi-Fi now: '$($wifi0.Ssid)' ($($wifi0.State))"
Log "gateway SSID '$GatewaySsid'  home SSID '$HomeSsid'  gateway $Gateway  user $User"
Log "output zip: $zipPath"
$haveGw = Test-WifiProfile $GatewaySsid
$haveHome = Test-WifiProfile $HomeSsid
Log "saved Wi-Fi profile for '$GatewaySsid': $haveGw   for '$HomeSsid': $haveHome"
try {
    $up = Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { "$($_.Name) [$($_.InterfaceDescription)]" }
    Log ("adapters up: " + ($up -join '; '))
} catch { Log "adapters: $($_.Exception.Message)" }

if ($Check) {
    Log "CHECK ONLY: nothing was changed."
    if (-not $haveGw) { Log "PROBLEM: no saved profile for '$GatewaySsid'. Join it once by hand, or the script cannot connect." }
    if (-not $haveHome) { Log "PROBLEM: no saved profile for '$HomeSsid'. The laptop could not return to it." }
    return
}
if (-not $SkipWifi -and (-not $haveGw -or -not $haveHome)) {
    Log "ABORT before any change: a Wi-Fi profile is missing (see above)."
    return
}

$hash = $null
if (-not $NoLogin) {
    $sec = Read-Host -AsSecureString "Gateway admin password for '$User' (held in memory only; Enter to skip login)"
    $plain = [System.Net.NetworkCredential]::new('', $sec).Password
    if ($plain) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes("${User}:$plain")
        $hash = -join ([System.Security.Cryptography.SHA256]::HashData($bytes) | ForEach-Object { $_.ToString('x2') })
    } else { Log "no password entered: static capture only" }
    $plain = $null
    $bytes = $null
}

# ------------------------------------------------------------------ watchdog
if (-not $SkipWifi) {
    $wd = Join-Path $env:TEMP "pingaroo-wifi-watchdog-$stamp.ps1"
    @'
param([string]$HomeSsid, [int]$DelaySeconds)
function N([string]$a) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new('netsh.exe', $a)
    $psi.RedirectStandardOutput = $true; $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi); $o = $p.StandardOutput.ReadToEnd(); $p.WaitForExit(); return $o
}
Start-Sleep -Seconds $DelaySeconds
$deadline = (Get-Date).AddMinutes(60)
while ((Get-Date) -lt $deadline) {
    $o = N 'wlan show interfaces'
    $ssid = ''; if ($o -match '(?m)^\s*SSID\s*:\s*(.+?)\s*$') { $ssid = $Matches[1] }
    $state = ''; if ($o -match '(?m)^\s*State\s*:\s*(.+?)\s*$') { $state = $Matches[1] }
    if ($ssid -eq $HomeSsid -and $state -eq 'connected') { break }
    N ('wlan connect name="{0}"' -f $HomeSsid) | Out-Null
    Start-Sleep -Seconds 12
}
'@ | Set-Content -Path $wd -Encoding UTF8
    $pwshPath = (Get-Process -Id $PID).Path
    $wdArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -HomeSsid "{1}" -DelaySeconds {2}' -f $wd, $HomeSsid, $WatchdogSeconds
    $wdProc = Start-Process -FilePath $pwshPath -ArgumentList $wdArgs -WindowStyle Hidden -PassThru
    Log "watchdog started (pid $($wdProc.Id)): forces '$HomeSsid' in $WatchdogSeconds s regardless of this script"
}

# ------------------------------------------------------------------ work
$manifest = [System.Collections.Generic.List[string]]::new()
$apiNames = [System.Collections.Generic.HashSet[string]]::new()
try {
    if (-not $SkipWifi) {
        Log "joining '$GatewaySsid' (a remote session drops here on a Wi-Fi-only laptop)"
        if (-not (Connect-Wifi $GatewaySsid 60)) { throw "could not join '$GatewaySsid' within 60 s" }
        Log "joined '$GatewaySsid'"
    }
    $ok = $false
    for ($i = 0; $i -lt 15 -and -not $ok; $i++) { $ok = Test-Tcp $Gateway 80 2000; if (-not $ok) { Start-Sleep -Seconds 2 } }
    if (-not $ok) { throw "gateway $Gateway port 80 not reachable" }
    Log "gateway reachable"
    $deadline = (Get-Date).AddSeconds($BudgetSeconds)

    # ---- 0. does the gateway have internet right now?  (read-only, public hosts)
    Log "internet check through the gateway..."
    $inet = Test-Internet
    $inet | ConvertTo-Json | Set-Content -Path (Join-Path $outDir 'internet-check.json') -Encoding UTF8
    foreach ($k in $inet.Keys) { Log "  net  $k : $($inet[$k])" }

    # ---- 1. optional login first: the gateway may only serve its pages to a logged-in client
    $loggedIn = $false
    if ($hash) {
        $body = ([ordered]@{ UserName = $User; Password = $hash; token = '' } | ConvertTo-Json -Compress)
        $r = Get-Gw 'cgi-bin/Login' 'Post' $body
        $shown = Protect-Text $r.Text
        Set-Content -Path (Join-Path $apiDir '_Login.response.json') -Value $shown -Encoding UTF8
        $cookieNames = @($session.Cookies.GetCookies([uri]"http://$Gateway/") | ForEach-Object { $_.Name }) -join ', '
        Log "login HTTP $($r.Status): $($shown.Substring(0, [Math]::Min(160, $shown.Length)))  cookies: [$cookieNames]"
        $loggedIn = ($r.Text -match '"Success"\s*:\s*true') -or ($r.Text -match '"Login"\s*:\s*"?1')
        if (-not $loggedIn) { Log "login was not accepted: continuing with whatever the gateway serves without it" }
    }

    # ---- 2. crawl the web UI files
    $queue = [System.Collections.Generic.Queue[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $tokens = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $dirs = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    function Add-GwPath([string]$p) {
        $p = ($p -split '[?#]')[0].Trim().TrimStart('/')
        if (-not $p -or $p.Contains('..') -or $p -match '^(https?:|//|data:|javascript:)') { return }
        if ($p -match '^vendor/' -or $p -notmatch '\.(html?|js|json)$') { return }
        if ($seen.Add($p)) { $queue.Enqueue($p) }
    }
    foreach ($p in 'login.html', 'index.html', 'home.html', 'script/common.js', 'script/login.js', 'script/index.js',
        'script/home.js', 'js/functions.js', 'translate/en.js', 'list_sidebar.html', 'basic_sidebar.html',
        'advanced_sidebar.html', 'expert_sidebar.html', 'setup_sidebar.html') { Add-GwPath $p }
    $fetched = 0
    $saved = 0
    function Invoke-Drain {
        while ($queue.Count -gt 0 -and (Get-Date) -lt $deadline -and $script:fetched -lt 6000) {
            $p = $queue.Dequeue()
            $r = Get-Gw $p
            $script:fetched++
            if ($r.Status -ne 200 -or -not $r.Text) { if ($r.Status -ne 404) { $manifest.Add("$($r.Status)`t$p") }; continue }
            if ($p -match '\.html?$' -and $p -ne 'login.html' -and $r.Text -match 'id="loginPWD"') { continue }  # redirected to login
            $manifest.Add("200`t$p`t$($r.Text.Length)")
            $dest = Join-Path $siteDir ($p -replace '[*:"<>|]', '_')
            New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
            Set-Content -Path $dest -Value $r.Text -Encoding UTF8
            $script:saved++
            $dir = ''
            if ($p.Contains('/')) { $dir = $p.Substring(0, $p.LastIndexOf('/') + 1) }
            foreach ($m in [regex]::Matches($r.Text, '(?i)(?:src|href)\s*=\s*["'']([^"''#\s]+\.(?:js|html?|json)(?:\?[^"'']*)?)["'']')) {
                Add-GwPath $m.Groups[1].Value
                if ($dir) { Add-GwPath ($dir + $m.Groups[1].Value) }
            }
            foreach ($m in [regex]::Matches($r.Text, '["'']((?:[\w\-]+/)+[\w\-.]+\.(?:html?|js|json))(?:\?[^"'']*)?["'']')) {
                Add-GwPath $m.Groups[1].Value
                if ($dir) { Add-GwPath ($dir + $m.Groups[1].Value) }
            }
            foreach ($m in [regex]::Matches($r.Text, 'data-bs-module\s*=\s*["'']([\w\-]+)["'']')) { Add-GwPath ($m.Groups[1].Value + '_sidebar.html') }
            foreach ($m in [regex]::Matches($r.Text, 'path\s*:\s*["''](\w+)["'']')) { [void]$apiNames.Add($m.Groups[1].Value) }
            foreach ($m in [regex]::Matches($r.Text, '(?i)(?:module|id|data-page|data-module)\s*=\s*["'']([a-z][\w\-]{1,40})["'']')) { [void]$tokens.Add($m.Groups[1].Value) }
            foreach ($m in [regex]::Matches($r.Text, '["'']/([a-z][\w\-]{1,30})/["'']\s*:')) { [void]$dirs.Add($m.Groups[1].Value) }
            foreach ($m in [regex]::Matches($r.Text, 'module/([\w\-]+)/')) { [void]$dirs.Add($m.Groups[1].Value) }
        }
    }
    Invoke-Drain
    Log "crawl pass 1: fetched $fetched, saved $saved, API names $($apiNames.Count), menu tokens $($tokens.Count)"

    # ---- 3. the menu builds page paths in code (module/<first>/<second>/<third>.html), so try the
    #         page tree named in this firmware's language file, then sidebar ids under each section
    $knownPages = @(
        'module/network/celllock.html', 'module/network/celllock/celllock.html', 'module/celllock.html', 'module/network/wan.html',
        'module/network/wan/wan.html', 'module/wan.html', 'module/network/wan/internet.html', 'module/wan/internet.html',
        'module/network/wan/ddns.html', 'module/wan/ddns.html', 'module/network/wan/upnp.html', 'module/wan/upnp.html',
        'module/network/wan/port_trigger.html', 'module/wan/port_trigger.html', 'module/network/wan/port_forward.html', 'module/wan/port_forward.html',
        'module/network/wan/dmz.html', 'module/wan/dmz.html', 'module/network/wan/nat_passthrough.html', 'module/wan/nat_passthrough.html',
        'module/network/wan/mac_sec.html', 'module/wan/mac_sec.html', 'module/network/multiwan.html', 'module/network/multiwan/multiwan.html',
        'module/multiwan.html', 'module/network/lte.html', 'module/network/lte/lte.html', 'module/lte.html',
        'module/network/lte/lteview.html', 'module/lte/lteview.html', 'module/network/lteview.html', 'module/network/lte/network_select.html',
        'module/lte/network_select.html', 'module/network/network_select.html', 'module/network/lte/multi_apn.html', 'module/lte/multi_apn.html',
        'module/network/multi_apn.html', 'module/network/lte/antenna.html', 'module/lte/antenna.html', 'module/network/antenna.html',
        'module/network/wlan/antenna.html', 'module/wlan/antenna.html', 'module/parentalcontrol/urlfilters.html', 'module/parentalcontrol/urlfilters/urlfilters.html',
        'module/urlfilters.html', 'module/parentalcontrol/keywordfilters.html', 'module/parentalcontrol/keywordfilters/keywordfilters.html', 'module/keywordfilters.html',
        'module/parentalcontrol/servicefilters.html', 'module/parentalcontrol/servicefilters/servicefilters.html', 'module/servicefilters.html', 'module/security/ipv4/net_service_filter.html',
        'module/ipv4/net_service_filter.html', 'module/security/net_service_filter.html', 'module/system/systemcfg.html', 'module/system/systemcfg/systemcfg.html',
        'module/systemcfg.html', 'module/system/reboot.html', 'module/system/reboot/reboot.html', 'module/reboot.html',
        'module/dashboard/home.html', 'module/dashboard/home/home.html', 'module/home.html', 'module/dashboard/guestwifi.html',
        'module/dashboard/guestwifi/guestwifi.html', 'module/guestwifi.html', 'module/dashboard/status.html', 'module/dashboard/status/status.html',
        'module/status.html', 'module/dashboard/wps.html', 'module/dashboard/wps/wps.html', 'module/wps.html',
        'module/dashboard/connected_devices.html', 'module/dashboard/connected_devices/connected_devices.html', 'module/connected_devices.html', 'module/dashboard/mesh_topology.html',
        'module/dashboard/mesh_topology/mesh_topology.html', 'module/mesh_topology.html', 'module/dashboard/sysInfo.html', 'module/dashboard/sysInfo/sysInfo.html',
        'module/sysInfo.html', 'module/dashboard/installwizard.html', 'module/dashboard/installwizard/installwizard.html', 'module/installwizard.html',
        'module/network/dhcp_lease.html', 'module/network/dhcp_lease/dhcp_lease.html', 'module/dhcp_lease.html', 'module/network/status.html',
        'module/network/status/status.html', 'module/network/traffic_query.html', 'module/network/traffic_query/traffic_query.html', 'module/traffic_query.html',
        'module/network/status_info.html', 'module/network/status_info/status_info.html', 'module/status_info.html', 'module/network/internet.html',
        'module/network/ddns.html', 'module/network/upnp.html', 'module/network/port_trigger.html', 'module/network/port_forward.html',
        'module/network/dmz.html', 'module/network/nat_passthrough.html', 'module/network/mac_sec.html', 'module/network/lan.html',
        'module/network/lan/lan.html', 'module/lan.html', 'module/network/lan/lanip.html', 'module/lan/lanip.html',
        'module/network/lanip.html', 'module/network/lan/ipv6.html', 'module/lan/ipv6.html', 'module/network/ipv6.html',
        'module/network/lan/ipv6info.html', 'module/lan/ipv6info.html', 'module/network/ipv6info.html', 'module/network/lan/devlist.html',
        'module/lan/devlist.html', 'module/network/devlist.html', 'module/network/lan/dhcp_lease.html', 'module/lan/dhcp_lease.html',
        'module/network/wlan.html', 'module/network/wlan/wlan.html', 'module/wlan.html', 'module/network/wlan/general.html',
        'module/wlan/general.html', 'module/network/general.html', 'module/network/wlan/wps.html', 'module/wlan/wps.html',
        'module/network/wps.html', 'module/network/wlan/acl.html', 'module/wlan/acl.html', 'module/network/acl.html',
        'module/network/wlan/radio.html', 'module/wlan/radio.html', 'module/network/radio.html', 'module/network/wlan/guest.html',
        'module/wlan/guest.html', 'module/network/guest.html', 'module/network/wlan/advanced.html', 'module/wlan/advanced.html',
        'module/network/advanced.html', 'module/network/wlan/bandsteering.html', 'module/wlan/bandsteering.html', 'module/network/bandsteering.html',
        'module/network/wlan/dfs_switch.html', 'module/wlan/dfs_switch.html', 'module/network/dfs_switch.html', 'module/network/wlan/ieee_1905.html',
        'module/wlan/ieee_1905.html', 'module/network/ieee_1905.html', 'module/network/wlan/fast_roaming.html', 'module/wlan/fast_roaming.html',
        'module/network/fast_roaming.html', 'module/network/wlan/mesh.html', 'module/wlan/mesh.html', 'module/network/mesh.html',
        'module/network/mesh/mesh.html', 'module/mesh.html', 'module/network/mesh/mesh_basic.html', 'module/mesh/mesh_basic.html',
        'module/network/mesh_basic.html', 'module/network/mesh/mesh_status.html', 'module/mesh/mesh_status.html', 'module/network/mesh_status.html',
        'module/network/mesh/mesh_search.html', 'module/mesh/mesh_search.html', 'module/network/mesh_search.html', 'module/network/ipv6/ipv6.html',
        'module/ipv6.html', 'module/network/multicast.html', 'module/network/multicast/multicast.html', 'module/multicast.html',
        'module/network/routing.html', 'module/network/routing/routing.html', 'module/routing.html', 'module/network/routing/static_routing.html',
        'module/routing/static_routing.html', 'module/network/static_routing.html', 'module/network/routing/dynamic_routing.html', 'module/routing/dynamic_routing.html',
        'module/network/dynamic_routing.html', 'module/network/extender.html', 'module/network/extender/extender.html', 'module/extender.html',
        'module/network/ip_passthrough.html', 'module/network/ip_passthrough/ip_passthrough.html', 'module/ip_passthrough.html', 'module/parentalcontrol/devices.html',
        'module/parentalcontrol/devices/devices.html', 'module/devices.html', 'module/services/usb_overview.html', 'module/services/usb_overview/usb_overview.html',
        'module/usb_overview.html', 'module/security/ipv4.html', 'module/security/ipv4/ipv4.html', 'module/ipv4.html',
        'module/security/ipv4/ipv4_common.html', 'module/ipv4/ipv4_common.html', 'module/security/ipv4_common.html', 'module/security/ipv4/client_acl.html',
        'module/ipv4/client_acl.html', 'module/security/client_acl.html', 'module/security/ipv6.html', 'module/security/ipv6/ipv6.html',
        'module/security/ipv6/ipv6_common.html', 'module/ipv6/ipv6_common.html', 'module/security/ipv6_common.html', 'module/security/ipv6/ipv6_firewall.html',
        'module/ipv6/ipv6_firewall.html', 'module/security/ipv6_firewall.html', 'module/security/vpn.html', 'module/security/vpn/vpn.html',
        'module/vpn.html', 'module/security/vpn/pptp_vpn_server.html', 'module/vpn/pptp_vpn_server.html', 'module/security/pptp_vpn_server.html',
        'module/security/vpn/openvpn_server.html', 'module/vpn/openvpn_server.html', 'module/security/openvpn_server.html', 'module/security/vpn/vpn_client.html',
        'module/vpn/vpn_client.html', 'module/security/vpn_client.html', 'module/qos/airtime_fairness.html', 'module/qos/airtime_fairness/airtime_fairness.html',
        'module/airtime_fairness.html', 'module/qos/atf.html', 'module/qos/atf/atf.html', 'module/atf.html',
        'module/qos/common.html', 'module/qos/common/common.html', 'module/common.html', 'module/qos/queue.html',
        'module/qos/queue/queue.html', 'module/queue.html', 'module/qos/classification.html', 'module/qos/classification/classification.html',
        'module/classification.html', 'module/qos/quene.html', 'module/qos/quene/quene.html', 'module/quene.html',
        'module/qos/smart.html', 'module/qos/smart/smart.html', 'module/smart.html', 'module/qos/802_11_tos.html',
        'module/qos/802_11_tos/802_11_tos.html', 'module/802_11_tos.html', 'module/qos/speed_limit.html', 'module/qos/speed_limit/speed_limit.html',
        'module/speed_limit.html', 'module/qos/iptv.html', 'module/qos/iptv/iptv.html', 'module/iptv.html',
        'module/qos/remarking_wmm.html', 'module/qos/remarking_wmm/remarking_wmm.html', 'module/remarking_wmm.html', 'module/qos/prioritzation.html',
        'module/qos/prioritzation/prioritzation.html', 'module/prioritzation.html', 'module/qos/prioritzation/ssid.html', 'module/prioritzation/ssid.html',
        'module/qos/ssid.html', 'module/qos/prioritzation/server.html', 'module/prioritzation/server.html', 'module/qos/server.html',
        'module/qos/prioritzation/client.html', 'module/prioritzation/client.html', 'module/qos/client.html', 'module/qos/prioritzation/application.html',
        'module/prioritzation/application.html', 'module/qos/application.html', 'module/diagnostic/diagnostic.html', 'module/diagnostic/diagnostic/diagnostic.html',
        'module/diagnostic.html', 'module/system/powersaving.html', 'module/system/powersaving/powersaving.html', 'module/powersaving.html',
        'module/system/config.html', 'module/system/config/config.html', 'module/config.html', 'module/system/firmware.html',
        'module/system/firmware/firmware.html', 'module/firmware.html', 'module/system/log.html', 'module/system/log/log.html',
        'module/log.html', 'module/system/tr069.html', 'module/system/tr069/tr069.html', 'module/tr069.html',
        'module/sms/new_msg.html', 'module/sms/new_msg/new_msg.html', 'module/new_msg.html', 'module/sms/inbox.html',
        'module/sms/inbox/inbox.html', 'module/inbox.html', 'module/sms/outbox.html', 'module/sms/outbox/outbox.html',
        'module/outbox.html', 'module/sms/drafts.html', 'module/sms/drafts/drafts.html', 'module/drafts.html',
        'module/sms/sms_settings.html', 'module/sms/sms_settings/sms_settings.html', 'module/sms_settings.html', 'module/status_info/wireless.html',
        'module/status_info/wireless/wireless.html', 'module/wireless.html', 'module/status_info/wireless/tip.html', 'module/wireless/tip.html',
        'module/status_info/tip.html', 'module/status_info/dhcp_lease.html', 'module/status_info/dhcp_lease/dhcp_lease.html', 'module/status_info/dhcp_lease/tip.html',
        'module/dhcp_lease/tip.html', 'module/status_info/route_table.html', 'module/status_info/route_table/route_table.html', 'module/route_table.html',
        'module/status_info/route_table/tip.html', 'module/route_table/tip.html', 'module/status_info/port_forwarding.html', 'module/status_info/port_forwarding/port_forwarding.html',
        'module/port_forwarding.html', 'module/status_info/port_forwarding/tip.html', 'module/port_forwarding/tip.html', 'module/status_info/connection_list.html',
        'module/status_info/connection_list/connection_list.html', 'module/connection_list.html', 'module/status_info/connection_list/tip.html', 'module/connection_list/tip.html',
        'module/status_info/snooping_table.html', 'module/status_info/snooping_table/snooping_table.html', 'module/snooping_table.html', 'module/status_info/snooping_table/tip.html',
        'module/snooping_table/tip.html', 'module/status_info/current_users.html', 'module/status_info/current_users/current_users.html', 'module/current_users.html',
        'module/status_info/current_users/tip.html', 'module/current_users/tip.html', 'module/status_info/blocked_users.html', 'module/status_info/blocked_users/blocked_users.html',
        'module/blocked_users.html', 'module/status_info/blocked_users/tip.html', 'module/blocked_users/tip.html', 'module/status_info/mesh_info.html',
        'module/status_info/mesh_info/mesh_info.html', 'module/mesh_info.html', 'module/opera/opera_common.html', 'module/opera/opera_common/opera_common.html',
        'module/opera_common.html', 'module/opera/lanIp.html', 'module/opera/lanIp/lanIp.html', 'module/lanIp.html',
        'module/opera/lanIp/tip.html', 'module/lanIp/tip.html', 'module/opera/tip.html', 'module/opera/wlan_list.html',
        'module/opera/wlan_list/wlan_list.html', 'module/wlan_list.html', 'module/opera/wlan_list/tip.html', 'module/wlan_list/tip.html',
        'module/opera/conntype.html', 'module/opera/conntype/conntype.html', 'module/conntype.html', 'module/opera/conntype/tip.html',
        'module/conntype/tip.html', 'module/opera/step2.html', 'module/opera/step2/step2.html', 'module/step2.html',
        'module/opera/step2/tip.html', 'module/step2/tip.html', 'module/opera/step3.html', 'module/opera/step3/step3.html',
        'module/step3.html', 'module/opera/step3/tip.html', 'module/step3/tip.html', 'module/management/access.html',
        'module/management/access/access.html', 'module/access.html', 'module/management/issuelog.html', 'module/management/issuelog/issuelog.html',
        'module/issuelog.html', 'module/management/tcpdump.html', 'module/management/tcpdump/tcpdump.html', 'module/tcpdump.html',
        'module/management/tr069.html', 'module/management/tr069/tr069.html', 'module/management/wifi_certification.html', 'module/management/wifi_certification/wifi_certification.html',
        'module/wifi_certification.html'
    )
    foreach ($p in 'module/network/lan/lan.html','module/network/lan/dhcp_lease.html',
        'module/network/lan/devicelist.html','module/network/ip_passthrough.html',
        'module/security/ipv4/firewall.html','module/network/routing.html',
        'module/system/configuration.html','module/management/access.html') { Add-GwPath $p }
    foreach ($p in $knownPages) { Add-GwPath $p }
    Invoke-Drain
    Log "crawl pass 2 (known pages): fetched $fetched, saved $saved, API names $($apiNames.Count)"
    $sections = 'network', 'security', 'system', 'dashboard', 'management', 'status_info', 'qos', 'services',
        'parentalcontrol', 'sms', 'diagnostic', 'opera'
    $subs = 'lte', 'wan', 'lan', 'wlan', 'multiwan', 'ipv4', 'ipv6', 'vpn'
    foreach ($t in @($tokens | Select-Object -First 150)) {
        Add-GwPath "module/$t.html"
        foreach ($sec in $sections) { Add-GwPath "module/$sec/$t.html" }
        foreach ($sub in $subs) { Add-GwPath "module/network/$sub/$t.html"; Add-GwPath "module/security/$sub/$t.html" }
    }
    Invoke-Drain
    Log "crawl pass 3 (sidebar ids): fetched $fetched, saved $saved, API names $($apiNames.Count)"
    Set-Content -Path (Join-Path $outDir 'api-names.txt') -Value (@($apiNames | Sort-Object) -join "`n") -Encoding UTF8

    # ---- capability map: which Set/Apply/Reboot/Reset calls exist, and the form fields on each page
    $setters = @($apiNames | Where-Object { $_ -match '^(Set|Apply|Reboot|Reset|Diagnose|Scan|Start)' } | Sort-Object)
    Set-Content -Path (Join-Path $outDir 'setters.txt') -Value ($setters -join "`n") -Encoding UTF8
    Log "changeable (Set/Apply/Reboot/Reset) calls seen: $($setters.Count)"
    $fieldLines = [System.Collections.Generic.List[string]]::new()
    Get-ChildItem -Path $siteDir -Recurse -Filter *.html | ForEach-Object {
        $html = Get-Content $_.FullName -Raw
        $rel = $_.FullName.Substring($siteDir.Length).TrimStart('\','/')
        foreach ($m in [regex]::Matches($html, '(?is)<(input|select)\b[^>]*\bid\s*=\s*["'']([^"'']+)["''][^>]*>')) {
            $tag = $m.Groups[1].Value; $id = $m.Groups[2].Value
            $type = ''; if ($m.Value -match 'type\s*=\s*["'']([^"'']+)["'']') { $type = $Matches[1] }
            $ro = if ($m.Value -match 'readonly|disabled') { 'readonly' } else { 'editable' }
            $fieldLines.Add("$rel`t$tag`t$type`t$ro`t$id")
        }
    }
    Set-Content -Path (Join-Path $outDir 'form-fields.tsv') -Value (($fieldLines | Sort-Object) -join "`n") -Encoding UTF8
    Log "form fields catalogued: $($fieldLines.Count)"

    # ---- 4. read-only status calls, then log out
    if ($loggedIn) {
        $always = 'GetLoginStatus', 'GetConfigStatus', 'GetLTEBasicSetting', 'GetLTEStatus', 'GetENDCInfo', 'GetWanIp', 'GetNvInfo'
        $skip = '(?i)fota|upgrade|reboot|reset|restore|factory|scan|speed|diag|ping|trace|backup|export|download|upload|log$'
        $all = @($always) + @($apiNames | Where-Object { $_ -cmatch '^Get' -and $_ -notmatch $skip } | Sort-Object)
        $all = @($all | Select-Object -Unique)
        $cell = '(?i)apn|profile|lte|sim|cell|band|wan|net|endc|antenna|mobile'
        $gets = @($all | Where-Object { $_ -match $cell }) + @($all | Where-Object { $_ -notmatch $cell })
        $gets = @($gets | Select-Object -First 200)
        $n = 0
        $apiDeadline = (Get-Date).AddSeconds(80)
        foreach ($name in $gets) {
            if ((Get-Date) -ge $apiDeadline) { Log "status reads stopped at time budget after $n of $($gets.Count)"; break }
            $wait = 8
            if ($name -match $cell) { $wait = 20 }
            $t0 = Get-Date
            $g = Get-Gw "cgi-bin/$name" 'Get' $null $wait
            $ms = [int]((Get-Date) - $t0).TotalMilliseconds
            Set-Content -Path (Join-Path $apiDir "$name.json") -Value ("HTTP $($g.Status)  ${ms} ms`n" + (Protect-Text $g.Text)) -Encoding UTF8
            $n++
        }
        Log "status reads saved: $n"
        $logout = @($apiNames | Where-Object { $_ -match '(?i)^\w*logout\w*$' } | Select-Object -First 1)
        if ($logout.Count -gt 0) {
            $lo = Get-Gw "cgi-bin/$($logout[0])" 'Post' '{"token":""}'
            $loShown = Protect-Text $lo.Text
            Log "logout via $($logout[0]): HTTP $($lo.Status) $($loShown.Substring(0, [Math]::Min(80, $loShown.Length)))"
        } else { Log "no logout call found in the web UI; the admin session will time out on its own" }
    }
} catch {
    Log "ERROR: $($_.Exception.Message)"
} finally {
    $hash = $null
    if (-not $SkipWifi) {
        Log "returning to '$HomeSsid'"
        $back = Connect-Wifi $HomeSsid 150
        Log "back on '$HomeSsid': $back"
    }
    try {
        Set-Content -Path (Join-Path $outDir 'manifest.txt') -Value ($manifest -join "`n") -Encoding UTF8
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        Compress-Archive -Path (Join-Path $outDir '*') -DestinationPath $zipPath -Force
        Log "DONE. Zip: $zipPath"
    } catch { Log "zip failed: $($_.Exception.Message). Raw files are in $outDir" }
}
