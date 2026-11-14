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

function Get-Gw([string]$path, [string]$method = 'Get', [string]$body = $null) {
    $u = "http://$Gateway/" + $path.TrimStart('/')
    $iwr = @{
        Uri = $u; Method = $method; TimeoutSec = 8; WebSession = $session
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
        'script/home.js', 'js/functions.js', 'translate/en.js') { Add-GwPath $p }
    $fetched = 0
    $saved = 0
    function Invoke-Drain {
        while ($queue.Count -gt 0 -and (Get-Date) -lt $deadline -and $script:fetched -lt 2500) {
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
            foreach ($m in [regex]::Matches($r.Text, 'path\s*:\s*["''](\w+)["'']')) { [void]$apiNames.Add($m.Groups[1].Value) }
            foreach ($m in [regex]::Matches($r.Text, '(?i)(?:module|id|data-page|data-module)\s*=\s*["'']([a-z][\w\-]{1,40})["'']')) { [void]$tokens.Add($m.Groups[1].Value) }
            foreach ($m in [regex]::Matches($r.Text, '["'']/([a-z][\w\-]{1,30})/["'']\s*:')) { [void]$dirs.Add($m.Groups[1].Value) }
            foreach ($m in [regex]::Matches($r.Text, 'module/([\w\-]+)/')) { [void]$dirs.Add($m.Groups[1].Value) }
        }
    }
    Invoke-Drain
    Log "crawl pass 1: fetched $fetched, saved $saved, API names $($apiNames.Count), menu tokens $($tokens.Count)"

    # ---- 3. the menu builds page paths in code, so also try module/<section>/<page>.html
    foreach ($d in 'lte', 'wan', 'network', 'internet', 'mobile', 'cellular', 'basic', 'advanced', 'system', 'management',
        'status_info', 'wlan', 'lan', 'security', 'home', 'opera', 'wizard') { [void]$dirs.Add($d) }
    foreach ($t in 'apn', 'apn_setting', 'apn_settings', 'lte', 'lte_setting', 'lte_basic', 'basic_setting', 'profile',
        'profiles', 'mobile', 'cellular', 'internet', 'dial', 'connection', 'wan_setting', 'network_setting') { [void]$tokens.Add($t) }
    $ordered = @($tokens | Sort-Object { if ($_ -match '(?i)apn|lte|cell|mobile|wan|internet|profile|dial|network|basic|setting') { 0 } else { 1 } }, { $_ })
    $ordered = @($ordered | Select-Object -First 90)
    foreach ($t in $ordered) {
        Add-GwPath "module/$t.html"
        Add-GwPath "module/$t/$t.html"
        Add-GwPath "script/$t.js"
        Add-GwPath "script/module/$t.js"
        foreach ($d in $dirs) { Add-GwPath "module/$d/$t.html"; Add-GwPath "script/$d/$t.js" }
    }
    Invoke-Drain
    Log "crawl pass 2: fetched $fetched, saved $saved, API names $($apiNames.Count)"
    Set-Content -Path (Join-Path $outDir 'api-names.txt') -Value (@($apiNames | Sort-Object) -join "`n") -Encoding UTF8

    # ---- 4. read-only status calls, then log out
    if ($loggedIn) {
        $always = 'GetLoginStatus', 'GetConfigStatus', 'GetLTEBasicSetting', 'GetLTEStatus', 'GetENDCInfo', 'GetWanIp', 'GetNvInfo'
        $skip = '(?i)fota|upgrade|reboot|reset|restore|factory|scan|speed|diag|ping|trace|backup|export|download|upload|log$'
        $gets = @($always) + @($apiNames | Where-Object { $_ -cmatch '^Get' -and $_ -notmatch $skip } | Sort-Object)
        $gets = @($gets | Select-Object -Unique | Select-Object -First 160)
        $n = 0
        $apiDeadline = (Get-Date).AddSeconds(60)
        foreach ($name in $gets) {
            if ((Get-Date) -ge $apiDeadline) { Log "status reads stopped at time budget"; break }
            $g = Get-Gw "cgi-bin/$name"
            Set-Content -Path (Join-Path $apiDir "$name.json") -Value ("HTTP $($g.Status)`n" + (Protect-Text $g.Text)) -Encoding UTF8
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
