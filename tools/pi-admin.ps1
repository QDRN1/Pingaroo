#requires -Version 7
<#
.SYNOPSIS
  Change the Pingaroo Pi's label and cycle interval over SSH, from a laptop that must
  leave its home Wi-Fi to reach the Pi (the Pi is on the gateway Wi-Fi, Kangaroo Labs).

.DESCRIPTION
  The remote session to the laptop drops the moment it joins Kangaroo Labs, so the Pi
  password is typed ONCE before the hop and held in memory.  The script then:
    watchdog -> join Kangaroo Labs -> ssh in -> run the changes -> return home.
  SSH auth uses the password via SSH_ASKPASS reading an environment variable (no secret
  on disk); sudo on the Pi reads the same password from stdin (sudo -S).  The independent
  Wi-Fi failsafe/watchdog bring the laptop home even if the ssh step hangs.

  Default change: set the label, set the interval, restart the service, start the loop.
  -LabelOnly        set only the label (no interval change, no service restart)
  -IntervalSeconds  cycle interval to write (default 1800 = 30 min)
  -NoRestart        write the interval but do not restart (applies at next restart)
  -Check            verify ssh.exe, pwsh and Wi-Fi profiles; do NOT hop, change nothing

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\pi-admin.ps1 -Label 5GCBB -Check
.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\pi-admin.ps1 -Label 5GCBB -IntervalSeconds 1800
#>
[CmdletBinding()]
param(
    [string]$Label = "5GCBB",
    [int]$IntervalSeconds = 1800,
    [string]$User = "pingaroo",
    [string]$PiIp = "192.168.1.218",
    [string]$GatewaySsid = "Kangaroo Labs",
    [string]$HomeSsid = "",
    [int]$WatchdogSeconds = 180,
    [int]$SshTimeoutSeconds = 90,
    [switch]$LabelOnly,
    [switch]$NoRestart,
    [switch]$Check,
    [switch]$SkipWifi
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir = Join-Path $env:TEMP "pingaroo-pi-admin-$stamp"
$logFile = Join-Path $outDir 'run.log'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}
function Invoke-Netsh([string]$argLine) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new('netsh.exe', $argLine)
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $o = $p.StandardOutput.ReadToEnd(); $p.WaitForExit(); return $o
}
function Get-WifiState {
    $out = Invoke-Netsh 'wlan show interfaces'
    $ssid = ''; $state = ''
    if ($out -match '(?m)^\s*SSID\s*:\s*(.+?)\s*$') { $ssid = $Matches[1] }
    if ($out -match '(?m)^\s*State\s*:\s*(.+?)\s*$') { $state = $Matches[1] }
    return [pscustomobject]@{ Ssid = $ssid; State = $state }
}
function Test-WifiProfile([string]$name) {
    return ((Invoke-Netsh 'wlan show profiles') -match (':\s*' + [regex]::Escape($name) + '\s*(\r?\n|$)'))
}
function Connect-Wifi([string]$name, [int]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec); $nextTry = Get-Date
    while ((Get-Date) -lt $deadline) {
        $s = Get-WifiState
        if ($s.Ssid -eq $name -and $s.State -eq 'connected') { return $true }
        if ((Get-Date) -ge $nextTry) { Invoke-Netsh ('wlan connect name="{0}"' -f $name) | Out-Null; $nextTry = (Get-Date).AddSeconds(10) }
        Start-Sleep -Seconds 2
    }
    return $false
}
function Test-Tcp([string]$h, [int]$port, [int]$ms = 3000) {
    $c = [System.Net.Sockets.TcpClient]::new()
    try { $t = $c.ConnectAsync($h, $port); if ($t.Wait($ms) -and $c.Connected) { return $true }; return $false }
    catch { return $false } finally { $c.Dispose() }
}

# --------------------------------------------------------------- preflight
if ($Label -notmatch '^[A-Za-z0-9][A-Za-z0-9 ._\-]{0,31}$') { Write-Host "Label '$Label' is not a safe label string. Nothing done."; return }
$ssh = (Get-Command ssh.exe -ErrorAction SilentlyContinue)
$wifi0 = Get-WifiState
if (-not $HomeSsid) { if ($wifi0.Ssid -and $wifi0.Ssid -ne $GatewaySsid) { $HomeSsid = $wifi0.Ssid } else { $HomeSsid = 'LAN Down Under' } }
Log "PowerShell $($PSVersionTable.PSVersion)  Wi-Fi now: '$($wifi0.Ssid)' ($($wifi0.State))"
Log "plan: label '$Label'$(if (-not $LabelOnly) { "  interval ${IntervalSeconds}s  restart $(-not $NoRestart)" } else { '  (label only)' })"
Log "Pi: $User@$PiIp   gateway SSID '$GatewaySsid'   home SSID '$HomeSsid'"
Log "ssh.exe: $(if ($ssh) { $ssh.Source } else { 'NOT FOUND' })"
$haveGw = Test-WifiProfile $GatewaySsid; $haveHome = Test-WifiProfile $HomeSsid
Log "saved Wi-Fi profile for '$GatewaySsid': $haveGw   for '$HomeSsid': $haveHome"
try { $up = Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { "$($_.Name) [$($_.InterfaceDescription)]" }; Log ("adapters up: " + ($up -join '; ')) } catch {}

if (-not $ssh) { Log "ABORT: ssh.exe not found. Install the Windows OpenSSH client, then retry. Nothing changed."; return }
if ($Check) { Log "CHECK ONLY: nothing was changed."; if (-not $haveGw) { Log "PROBLEM: no saved profile for '$GatewaySsid'." }; if (-not $haveHome) { Log "PROBLEM: no saved profile for '$HomeSsid'." }; return }
if (-not $SkipWifi -and (-not $haveGw -or -not $haveHome)) { Log "ABORT before any change: a Wi-Fi profile is missing."; return }

$sec = Read-Host -AsSecureString "Password for $User@$PiIp (SSH + sudo; held in memory only)"
$plain = [System.Net.NetworkCredential]::new('', $sec).Password
if (-not $plain) { Log "ABORT: no password entered. Nothing changed."; return }

# build the remote command (no spaces in label that need quoting unless the label has them)
$lbl = "'" + ($Label -replace "'", "'\''") + "'"
$steps = "pingaroo label $lbl"
if (-not $LabelOnly) {
    $steps += "; pingaroo interval $IntervalSeconds"
    if (-not $NoRestart) { $steps += "; systemctl restart pingaroo; sleep 3; pingaroo start" }
}
$steps += "; echo '--- VERIFY ---'; pingaroo label; pingaroo interval; systemctl is-active pingaroo"
$remote = "sudo -S -p '' bash -lc " + "`"$steps`""

# --------------------------------------------------------------- watchdog
if (-not $SkipWifi) {
    $wd = Join-Path $env:TEMP "pingaroo-wifi-watchdog-$stamp.ps1"
    @'
param([string]$HomeSsid, [int]$DelaySeconds)
function N([string]$a) { $psi=[System.Diagnostics.ProcessStartInfo]::new('netsh.exe',$a); $psi.RedirectStandardOutput=$true; $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true; $p=[System.Diagnostics.Process]::Start($psi); $o=$p.StandardOutput.ReadToEnd(); $p.WaitForExit(); return $o }
Start-Sleep -Seconds $DelaySeconds
$deadline=(Get-Date).AddMinutes(60)
while((Get-Date) -lt $deadline){ $o=N 'wlan show interfaces'; $ssid=''; if($o -match '(?m)^\s*SSID\s*:\s*(.+?)\s*$'){$ssid=$Matches[1]}; $state=''; if($o -match '(?m)^\s*State\s*:\s*(.+?)\s*$'){$state=$Matches[1]}; if($ssid -eq $HomeSsid -and $state -eq 'connected'){break}; N ('wlan connect name="{0}"' -f $HomeSsid) | Out-Null; Start-Sleep -Seconds 12 }
'@ | Set-Content -Path $wd -Encoding UTF8
    $pwshPath = (Get-Process -Id $PID).Path
    $wdArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -HomeSsid "{1}" -DelaySeconds {2}' -f $wd, $HomeSsid, $WatchdogSeconds
    $wdProc = Start-Process -FilePath $pwshPath -ArgumentList $wdArgs -WindowStyle Hidden -PassThru
    Log "watchdog started (pid $($wdProc.Id)): forces '$HomeSsid' in $WatchdogSeconds s regardless of this script"
}

# askpass helper: echoes an env var, so the password is never written to disk
$askCmd = Join-Path $outDir "askpass.cmd"
Set-Content -Path $askCmd -Value '@echo %PINGAROO_PW%' -Encoding Ascii

# --------------------------------------------------------------- work
try {
    if (-not $SkipWifi) {
        Log "joining '$GatewaySsid' (a remote session drops here on a Wi-Fi-only laptop)"
        if (-not (Connect-Wifi $GatewaySsid 60)) { throw "could not join '$GatewaySsid' within 60 s" }
        Log "joined '$GatewaySsid'"
    }
    $ok = $false
    for ($i = 0; $i -lt 15 -and -not $ok; $i++) { $ok = Test-Tcp $PiIp 22 2000; if (-not $ok) { Start-Sleep -Seconds 2 } }
    if (-not $ok) { throw "Pi $PiIp port 22 not reachable" }
    Log "Pi reachable on 22; running changes over ssh..."

    $job = Start-ThreadJob -ScriptBlock {
        param($ssh, $askCmd, $pw, $user, $ip, $remote)
        $env:PINGAROO_PW = $pw
        $env:SSH_ASKPASS = $askCmd
        $env:SSH_ASKPASS_REQUIRE = 'force'
        $env:DISPLAY = 'localhost:0'
        $args = @(
            '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=10',
            '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
            '-o', 'NumberOfPasswordPrompts=1', "$user@$ip", $remote
        )
        $pw | & $ssh @args 2>&1 | Out-String
    } -ArgumentList $ssh.Source, $askCmd, $plain, $User, $PiIp, $remote

    if (Wait-Job $job -Timeout $SshTimeoutSeconds) {
        $out = Receive-Job $job
        foreach ($line in ($out -split "`r?`n")) { if ($line.Trim()) { Log "  pi| $($line.TrimEnd())" } }
    } else {
        Log "ssh did not finish within $SshTimeoutSeconds s; stopping it and returning home"
        Stop-Job $job -ErrorAction SilentlyContinue
        Get-Process ssh -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $ssh.Source } | Stop-Process -ErrorAction SilentlyContinue
    }
    Remove-Job $job -Force -ErrorAction SilentlyContinue
} catch {
    Log "ERROR: $($_.Exception.Message)"
} finally {
    $plain = $null
    $env:PINGAROO_PW = $null
    if (Test-Path $askCmd) { Remove-Item $askCmd -Force -ErrorAction SilentlyContinue }
    if (-not $SkipWifi) {
        Log "returning to '$HomeSsid'"
        Log "back on '$HomeSsid': $(Connect-Wifi $HomeSsid 150)"
    }
    Log "log: $logFile"
}
