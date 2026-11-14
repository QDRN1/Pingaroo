#requires -Version 7
<#
.SYNOPSIS
  Set the APN on the AT&T Internet Air gateway (NCM1120) and connect its data session,
  from a Windows laptop that has to leave its home Wi-Fi to reach the gateway.

.DESCRIPTION
  Sends exactly what the gateway's own Mobile-WAN page sends (captured from its web UI,
  firmware 254311.SDK3.0.2):
    POST cgi-bin/SetLTEBasicSetting  { Enable_AutoConn, Enable_Roam, Enable_Airplane, Profile_Name,
                                       Enable_AutoAPN, APN_Name, username, Password, AuthType, PdpType, token }
    POST cgi-bin/SetDataConn         { Enable_LTE: true, token }           (the page's Connect button)
  Every write needs a token: GET GetKey, GET GetToken, then AES-128-CTR of the token with the
  first 16 characters of the key as both key and IV, Base64.  Before any write the script
  proves that pipeline on the gateway with a read-only call that also requires a token.

  Order: self-test (no network) -> watchdog -> join gateway Wi-Fi -> login -> read current
  settings -> token proof -> set APN -> read back -> connect data -> poll -> logout -> home.
  If anything fails before the first write, nothing on the gateway is changed.

  The admin password is typed at a prompt and held in memory only.

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\gateway-set-apn.ps1 -Apn 5gcbroadband -Check
  Runs the encryption self-test and Wi-Fi preflight.  Changes nothing, does not hop.

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\gateway-set-apn.ps1 -Apn 5gcbroadband
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Apn,
    [string]$GatewaySsid = "Kangaroo Labs",
    [string]$HomeSsid = "",
    [string]$Gateway = "192.168.1.1",
    [string]$User = "attadmin",
    [int]$WatchdogSeconds = 300,
    [int]$WorkSeconds = 200,
    [switch]$NoConnect,
    [switch]$Check,
    [switch]$SkipWifi
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir = Join-Path $env:TEMP "pingaroo-gateway-setapn-$stamp"
$logFile = Join-Path $outDir 'run.log'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

function Invoke-Netsh([string]$argLine) {
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
    $pattern = '"(?<k>[^"]*(?:pass|psk|key|imei|imsi|iccid|msisdn|session|token|serial)[^"]*|sn|SN)"\s*:\s*"(?<v>[^"]*)"'
    $evaluator = [System.Text.RegularExpressions.MatchEvaluator] {
        param($m)
        '"' + $m.Groups['k'].Value + '":"<masked ' + $m.Groups['v'].Value.Length + ' chars>"'
    }
    return [regex]::Replace($text, $pattern, $evaluator, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
}

function Protect-Token([string]$token, [string]$key) {
    # AES-128-CTR, key = IV = first 16 characters of the gateway key (UTF-8), no padding, Base64.
    # Matches CryptoJS mode.CTR as shipped on the gateway: only the last 32-bit word of the
    # counter is incremented.
    $kb = [System.Text.Encoding]::UTF8.GetBytes($key.Substring(0, 16))
    if ($kb.Length -ne 16) { throw "gateway key is not 16 single-byte characters" }
    $data = [System.Text.Encoding]::UTF8.GetBytes($token)
    $aes = [System.Security.Cryptography.Aes]::Create()
    try {
        $aes.Mode = [System.Security.Cryptography.CipherMode]::ECB
        $aes.Padding = [System.Security.Cryptography.PaddingMode]::None
        $aes.Key = $kb
        $enc = $aes.CreateEncryptor()
        $counter = [byte[]]$kb.Clone()
        $out = [byte[]]::new($data.Length)
        $ks = [byte[]]::new(16)
        for ($off = 0; $off -lt $data.Length; $off += 16) {
            [void]$enc.TransformBlock($counter, 0, 16, $ks, 0)
            $n = [Math]::Min(16, $data.Length - $off)
            for ($i = 0; $i -lt $n; $i++) { $out[$off + $i] = [byte]($data[$off + $i] -bxor $ks[$i]) }
            for ($j = 15; $j -ge 12; $j--) {
                if ($counter[$j] -eq 255) { $counter[$j] = 0 } else { $counter[$j] = [byte]($counter[$j] + 1); break }
            }
        }
        return [Convert]::ToBase64String($out)
    } finally { $aes.Dispose() }
}

function Test-TokenCrypto {
    # Known answers computed independently with OpenSSL and Python's cryptography library.
    $cases = @(
        @{ k = '0123456789abcdefXXXXXXXXXXXXXXXX'; t = 'a3f1c29e5b7d4086a3f1c29e5b7d4086a3f1c29e5b7d4086a3f1c29e5b7d4086'
            e = 'E0EYuX3uxGQ1xS8MTTmNU5D+rSEeeKf0A8d5Mig+e9gKBoId1o28V0NgM5NpgKWZV/MxGolE2rHG8VqhV2f60A==' },
        @{ k = 'Kq7ZpW2mN8xR4vTsYYYYYYYYYYYYYYYY'; t = 'token-with-odd-length-0123456789-abcdef'
            e = 'i92BqCflxGV4sVzelmlrrbHxHZ9qaUL8bVivhp1temaObwUM3ASe' }
    )
    foreach ($c in $cases) {
        $got = ''
        try { $got = Protect-Token $c.t $c.k } catch { $got = "ERROR: $($_.Exception.Message)" }
        if ($got -cne $c.e) { Log "SELF-TEST FAILED: expected $($c.e) got $got"; return $false }
    }
    return $true
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

function Get-GwJson([string]$name, [int]$timeoutSec = 8) {
    $r = Get-Gw "cgi-bin/$name" 'Get' $null $timeoutSec
    $obj = $null
    if ($r.Status -eq 200) { try { $obj = $r.Text | ConvertFrom-Json } catch { $obj = $null } }
    $shown = Protect-Text $r.Text
    Log "GET $name -> HTTP $($r.Status) $($shown.Substring(0, [Math]::Min(260, $shown.Length)))"
    return $obj
}

function Short([string]$s, [int]$n = 200) { return $s.Substring(0, [Math]::Min($n, $s.Length)) }

$script:gwKey = $null
function Send-GwWrite([string]$name, $data, [int]$timeoutSec = 20) {
    # Fresh token for every POST, encrypted the way the page does it.
    $t = Get-Gw 'cgi-bin/GetToken' 'Get' $null 10
    $tok = $null
    try { $tok = ($t.Text | ConvertFrom-Json).token } catch { $tok = $null }
    if (-not $tok) { Log "POST $name NOT SENT: no token (HTTP $($t.Status) $(Short (Protect-Text $t.Text) 120))"; return $null }
    $payload = [ordered]@{}
    foreach ($k in $data.Keys) { $payload[$k] = $data[$k] }
    $payload['token'] = Protect-Token ([string]$tok) $script:gwKey
    $body = $payload | ConvertTo-Json -Compress
    $shownBody = Protect-Text $body
    Log "POST $name $shownBody"
    $r = Get-Gw "cgi-bin/$name" 'Post' $body $timeoutSec
    Log "POST $name -> HTTP $($r.Status) $(Short (Protect-Text $r.Text) 240)"
    return $r
}

# ------------------------------------------------------------------ preflight
$Apn = $Apn.Trim()
$blocked = 'nrphone', 'enhancedphone', 'nxtgenphone', 'phone', 'pta', 'wap.cingular', 'firstnet-phone', 'ndo', 'endo', 'ims'
if ($Apn -notmatch '^[A-Za-z0-9][A-Za-z0-9.\-_]{0,62}$') { Write-Host "APN '$Apn' has characters an APN cannot contain. Nothing done."; return }
if ($blocked -contains $Apn.ToLower()) { Write-Host "APN '$Apn' is on the gateway page's own not-allowed list. Nothing done."; return }

$wifi0 = Get-WifiState
if (-not $HomeSsid) {
    if ($wifi0.Ssid -and $wifi0.Ssid -ne $GatewaySsid) { $HomeSsid = $wifi0.Ssid } else { $HomeSsid = 'LAN Down Under' }
}
$downloads = Join-Path $env:USERPROFILE 'OneDrive - QDRN\Downloads'
if (-not (Test-Path $downloads)) { $downloads = Join-Path $env:USERPROFILE 'Downloads' }
$zipPath = Join-Path $downloads "pingaroo-gateway-setapn-$stamp.zip"

Log "PowerShell $($PSVersionTable.PSVersion)  Wi-Fi now: '$($wifi0.Ssid)' ($($wifi0.State))"
Log "target APN '$Apn'  connect data after: $(-not $NoConnect)  gateway SSID '$GatewaySsid'  home SSID '$HomeSsid'  gateway $Gateway  user $User"
$cryptoOk = Test-TokenCrypto
Log "token encryption self-test: $(if ($cryptoOk) { 'PASSED (2 of 2 known answers)' } else { 'FAILED' })"
$haveGw = Test-WifiProfile $GatewaySsid
$haveHome = Test-WifiProfile $HomeSsid
Log "saved Wi-Fi profile for '$GatewaySsid': $haveGw   for '$HomeSsid': $haveHome"
try {
    $up = Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { "$($_.Name) [$($_.InterfaceDescription)]" }
    Log ("adapters up: " + ($up -join '; '))
} catch { Log "adapters: $($_.Exception.Message)" }

if (-not $cryptoOk) { Log "ABORT: the encryption self-test failed, so no write could be accepted. Nothing was changed."; return }
if ($Check) { Log "CHECK ONLY: nothing was changed."; return }
if (-not $SkipWifi -and (-not $haveGw -or -not $haveHome)) { Log "ABORT before any change: a Wi-Fi profile is missing."; return }

$sec = Read-Host -AsSecureString "Gateway admin password for '$User' (held in memory only)"
$plain = [System.Net.NetworkCredential]::new('', $sec).Password
if (-not $plain) { Log "ABORT: no password entered. Nothing was changed."; return }
$bytes = [System.Text.Encoding]::UTF8.GetBytes("${User}:$plain")
$hash = -join ([System.Security.Cryptography.SHA256]::HashData($bytes) | ForEach-Object { $_.ToString('x2') })
$plain = $null
$bytes = $null

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
$result = [ordered]@{ wrote_apn = $false; apn_before = '?'; apn_after = '?'; sent_connect = $false; data_conn = '?'; wan_ip = ''; network = '' }
$loggedIn = $false
try {
    if (-not $SkipWifi) {
        Log "joining '$GatewaySsid' (a remote session drops here on a Wi-Fi-only laptop)"
        if (-not (Connect-Wifi $GatewaySsid 60)) { throw "could not join '$GatewaySsid' within 60 s" }
        Log "joined '$GatewaySsid'"
    }
    $ok = $false
    for ($i = 0; $i -lt 15 -and -not $ok; $i++) { $ok = Test-Tcp $Gateway 80 2000; if (-not $ok) { Start-Sleep -Seconds 2 } }
    if (-not $ok) { throw "gateway $Gateway port 80 not reachable" }
    $deadline = (Get-Date).AddSeconds($WorkSeconds)
    function Left { return [int]($deadline - (Get-Date)).TotalSeconds }

    # ---- 1. login
    $body = ([ordered]@{ UserName = $User; Password = $hash; token = '' } | ConvertTo-Json -Compress)
    $r = Get-Gw 'cgi-bin/Login' 'Post' $body 10
    Log "login -> HTTP $($r.Status) $(Short (Protect-Text $r.Text) 160)"
    $loggedIn = ($r.Text -match '"Success"\s*:\s*true') -or ($r.Text -match '"Login"\s*:\s*"?1')
    if (-not $loggedIn) { throw "login was not accepted; nothing was changed" }

    # ---- 2. key, current settings
    $k = Get-Gw 'cgi-bin/GetKey' 'Get' $null 10
    try { $script:gwKey = [string]($k.Text | ConvertFrom-Json).Key } catch { $script:gwKey = $null }
    if (-not $script:gwKey -or $script:gwKey.Length -lt 16) { throw "gateway did not return a usable key (HTTP $($k.Status)); nothing was changed" }
    Log "key received ($($script:gwKey.Length) chars)"

    $cur = $null
    for ($try = 1; $try -le 2 -and -not $cur; $try++) { $cur = Get-GwJson 'GetLTEBasicSetting' 30 }
    $userName = ''
    $apnPass = ''
    $auth = 'none'
    $pdp = 'ipv4v6'
    if ($cur) {
        $result.apn_before = [string]$cur.APN_Name
        if ($null -ne $cur.UserName) { $userName = [string]$cur.UserName }
        if ($null -ne $cur.Password) { $apnPass = [string]$cur.Password }
        if ($cur.AuthType) { $auth = [string]$cur.AuthType }
        if ($cur.PdpType) { $pdp = [string]$cur.PdpType }
        Log "current: APN '$($cur.APN_Name)'  AutoAPN $($cur.Enable_AutoAPN)  DataConn $($cur.Enable_DataConn)  Auth $auth  PDP $pdp"
    } else {
        Log "current settings did not answer; using the values captured earlier today (no username, no password, auth none, ipv4v6)"
    }

    # ---- 3. prove the token pipeline with a read-only call that also needs a token
    $probe = Send-GwWrite 'GetCurrentInfo' ([ordered]@{ Freq = 1 }) 10
    if (-not $probe -or $probe.Status -ne 200 -or $probe.Text -match 'token_error|session_id_error') {
        throw "the gateway rejected the token on a read-only call; no setting was sent, nothing was changed"
    }
    Log "token accepted by the gateway on a read-only call"

    # ---- 4. set the APN exactly as the Mobile-WAN page's Apply does
    $already = $cur -and ([string]$cur.APN_Name).ToLower() -eq $Apn.ToLower() -and ([string]$cur.Enable_AutoAPN) -ne '1'
    if ($already) {
        Log "APN is already '$($cur.APN_Name)': not rewriting it"
        $result.apn_after = [string]$cur.APN_Name
    } else {
        if ((Left) -lt 60) { throw "not enough time left to write safely; nothing was changed" }
        $data = [ordered]@{
            Enable_AutoConn = '0'; Enable_Roam = '0'; Enable_Airplane = '0'; Profile_Name = ' '
            Enable_AutoAPN = '0'; APN_Name = $Apn; username = $userName; Password = $apnPass
            AuthType = $auth; PdpType = $pdp
        }
        $w = Send-GwWrite 'SetLTEBasicSetting' $data 45
        $result.wrote_apn = $true
        if ($w -and $w.Text -match 'token_error|session_id_error') { throw "gateway rejected the APN write (token or session)" }
        Start-Sleep -Seconds 10          # the page waits 10 s before re-reading
        $after = $null
        for ($try = 1; $try -le 3 -and -not $after -and (Left) -gt 25; $try++) { $after = Get-GwJson 'GetLTEBasicSetting' 25 }
        if ($after) { $result.apn_after = [string]$after.APN_Name } else { Log "could not read the APN back yet" }
    }

    # ---- 5. connect the data session (the page's Connect button)
    if (-not $NoConnect) {
        $dc = Get-GwJson 'GetDataConn' 15
        if ($dc) { $result.data_conn = [string]$dc.Enable_DataConn }
        if ($result.data_conn -ne '1') {
            if ((Left) -lt 20) { Log "no time left to send Connect" } else {
                $c = Send-GwWrite 'SetDataConn' ([ordered]@{ Enable_LTE = $true }) 30
                $result.sent_connect = $true
                Start-Sleep -Seconds 10      # the page waits 10 s here too
            }
        } else { Log "data session already enabled" }
    }

    # ---- 6. watch it come up
    while ((Left) -gt 12) {
        $dc = Get-GwJson 'GetDataConn' 10
        if ($dc) { $result.data_conn = [string]$dc.Enable_DataConn }
        $ip = Get-GwJson 'GetWanIp' 10
        if ($ip) { $result.wan_ip = [string]$ip.ConnIP }
        $st = Get-GwJson 'GetLTEStatus' 12
        if ($st) { $result.network = "$($st.operator) $($st.net_type)".Trim() }
        if ($result.wan_ip) { break }
        Start-Sleep -Seconds 6
    }
    if ($result.apn_after -eq '?' -and (Left) -gt 5) {
        $after = Get-GwJson 'GetLTEBasicSetting' ([Math]::Max(5, (Left) - 3))
        if ($after) { $result.apn_after = [string]$after.APN_Name }
    }
} catch {
    Log "ERROR: $($_.Exception.Message)"
} finally {
    $hash = $null
    if ($loggedIn) {
        $lo = Get-Gw 'cgi-bin/Logout' 'Post' '{"token":""}' 8
        Log "logout -> HTTP $($lo.Status) $(Short (Protect-Text $lo.Text) 80)"
    }
    if (-not $SkipWifi) {
        Log "returning to '$HomeSsid'"
        $back = Connect-Wifi $HomeSsid 150
        Log "back on '$HomeSsid': $back"
    }
    Log "RESULT  APN before: '$($result.apn_before)'  APN now: '$($result.apn_after)'  APN write sent: $($result.wrote_apn)"
    Log "RESULT  Connect sent: $($result.sent_connect)  data session: $($result.data_conn)  WAN IP: '$($result.wan_ip)'  network: '$($result.network)'"
    try {
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        Compress-Archive -Path (Join-Path $outDir '*') -DestinationPath $zipPath -Force
        Log "DONE. Log zip: $zipPath"
    } catch { Log "zip failed: $($_.Exception.Message). Log is in $outDir" }
}
