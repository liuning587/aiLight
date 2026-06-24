# Register / unregister aiLight lightd Windows logon autostart (Task Scheduler)
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$ExePath = "",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$TaskName = "aiLight-lightd"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

$startScript = Join-Path $PSScriptRoot "start-lightd.ps1"
if (-not (Test-Path $startScript)) {
    throw "Missing $startScript"
}

$argList = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`" -ProjectRoot `"$ProjectRoot`""
if ($ExePath) {
    $argList += " -ExePath `"$ExePath`""
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argList
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Registered logon autostart: $TaskName"
Write-Host "  Project : $ProjectRoot"
if ($ExePath) { Write-Host "  Exe     : $ExePath" }
Write-Host ""
Write-Host "To remove: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
