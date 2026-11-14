#requires -Version 7
<#
.SYNOPSIS
  Failsafe for a remotely-operated laptop: if it has been off its home Wi-Fi for
  -AwayMinutes, force it back.  Runs as a Windows scheduled task every minute,
  independent of any other script or window.

.DESCRIPTION
  -Install   copy this script to %LOCALAPPDATA%\PingarooFailsafe and register the task
  -Status    show the task, its last tick and the recent log
  -Remove    unregister the task
  -Tick      one check (what the task runs; not meant to be typed)
  -DryRun    with -Install: log what would be done, never change the Wi-Fi

  "Away" means the Wi-Fi is on any other network, or not connected at all.  The task
  runs as the signed-in user, so it works while that user is logged on (a remote session
  counts).  After a reboot with nobody logged on, Windows' own auto-connect applies.

  It covers the Wi-Fi not returning home.  It does not cover a VPN kill switch blocking
  traffic once the laptop is back on the home Wi-Fi.

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\wifi-failsafe.ps1 -Install
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status,
    [switch]$Tick,
    [string]$HomeSsid = "LAN Down Under",
    [int]$AwayMinutes = 5,
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'
$taskName = 'PingarooWifiFailsafe'
$dir = Join-Path $env:LOCALAPPDATA 'PingarooFailsafe'
$stateFile = Join-Path $dir 'state.json'
$logFile = Join-Path $dir 'failsafe.log'
$installedCopy = Join-Path $dir 'wifi-failsafe.ps1'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $logFile -Value $line
    if (-not $Tick) { Write-Host $line }
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

function Save-State($awaySince, [string]$note) {
    $away = $null
    if ($awaySince) { $away = ([datetime]$awaySince).ToString('o') }
    $o = [ordered]@{
        awaySince = $away
        lastNote = $note
        lastTick = (Get-Date).ToString('o')
        homeSsid = $HomeSsid
        awayMinutes = $AwayMinutes
        dryRun = [bool]$DryRun
    }
    $o | ConvertTo-Json | Set-Content -Path $stateFile -Encoding UTF8
}

# ---------------------------------------------------------------------- tick
if ($Tick) {
    $now = Get-Date
    $s = Get-WifiState
    $awaySince = $null
    $lastNote = ''
    if (Test-Path $stateFile) {
        try {
            $st = Get-Content $stateFile -Raw | ConvertFrom-Json
            if ($st.awaySince) { $awaySince = [datetime]$st.awaySince }
            $lastNote = [string]$st.lastNote
        } catch { $awaySince = $null }
    }
    $atHome = ($s.Ssid -eq $HomeSsid -and $s.State -eq 'connected')
    if ($atHome) {
        if ($awaySince) { Log "back on '$HomeSsid' (was away since $($awaySince.ToString('HH:mm:ss')))" }
        elseif ($lastNote -ne 'home') { Log "on '$HomeSsid': ok" }
        Save-State $null 'home'
        return
    }
    if (-not $awaySince) {
        Log "away from '$HomeSsid' (Wi-Fi: '$($s.Ssid)' $($s.State)); will force back from $($now.AddMinutes($AwayMinutes).ToString('HH:mm:ss'))"
        Save-State $now 'away'
        return
    }
    $mins = [math]::Round(($now - $awaySince).TotalMinutes, 1)
    if ($mins -ge $AwayMinutes) {
        if ($DryRun) {
            Log "DRY RUN: away $mins min (Wi-Fi: '$($s.Ssid)' $($s.State)); would force '$HomeSsid' now"
        } else {
            $o = Invoke-Netsh ('wlan connect name="{0}"' -f $HomeSsid)
            Log "away $mins min (Wi-Fi: '$($s.Ssid)' $($s.State)): forcing '$HomeSsid' -> $($o.Trim())"
        }
    }
    Save-State $awaySince 'away'
    return
}

# -------------------------------------------------------------------- status
if ($Status) {
    $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($t) {
        $i = $t | Get-ScheduledTaskInfo
        Write-Host "task '$taskName': $($t.State)   last run $($i.LastRunTime)   result $($i.LastTaskResult)   next $($i.NextRunTime)"
        Write-Host "runs: $($t.Actions[0].Arguments)"
    } else { Write-Host "task '$taskName' is NOT installed" }
    if (Test-Path $stateFile) { Write-Host "state: $((Get-Content $stateFile -Raw) -replace '\s+', ' ')" }
    $w = Get-WifiState
    Write-Host "Wi-Fi now: '$($w.Ssid)' ($($w.State))   time now: $(Get-Date -Format 'HH:mm:ss')"
    if (Test-Path $logFile) { Write-Host "--- last log lines ---"; Get-Content $logFile -Tail 12 | ForEach-Object { Write-Host $_ } }
    return
}

# -------------------------------------------------------------------- remove
if ($Remove) {
    $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($t) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false; Log "task removed" } else { Write-Host "task '$taskName' was not installed" }
    if (Test-Path $stateFile) { Remove-Item $stateFile -Force }
    return
}

# ------------------------------------------------------------------- install
if ($Install) {
    if ($AwayMinutes -lt 1) { Write-Host "AwayMinutes must be at least 1"; return }
    if ($PSCommandPath -ne $installedCopy) { Copy-Item -Path $PSCommandPath -Destination $installedCopy -Force }
    if (Test-Path $stateFile) { Remove-Item $stateFile -Force }
    $pwshPath = (Get-Process -Id $PID).Path
    $arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Tick -HomeSsid "{1}" -AwayMinutes {2}' -f $installedCopy, $HomeSsid, $AwayMinutes
    if ($DryRun) { $arg += ' -DryRun' }
    $action = New-ScheduledTaskAction -Execute $pwshPath -Argument $arg
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(15) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 7)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    try {
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force -ErrorAction Stop `
            -Description "Pingaroo: force Wi-Fi back to '$HomeSsid' after $AwayMinutes min away. Remove with wifi-failsafe.ps1 -Remove." | Out-Null
    } catch {
        Write-Host "INSTALL FAILED: $($_.Exception.Message)"
        return
    }
    $mode = 'LIVE'
    if ($DryRun) { $mode = 'DRY RUN (never changes the Wi-Fi)' }
    Log "installed [$mode]: every minute; force '$HomeSsid' after $AwayMinutes min away; runs for 7 days or until removed"
    Write-Host "Check it is ticking in about two minutes:  pwsh -NoProfile -ExecutionPolicy Bypass -File `"$installedCopy`" -Status"
    return
}

Write-Host "Use -Install, -Status or -Remove.  See the header of this file."
