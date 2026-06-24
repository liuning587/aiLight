# aiLight Windows one-click setup (PromLight-style)
# Usage:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Autostart

param(
    [switch]$Autostart
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "==> aiLight install ($ProjectRoot)"

Write-Host "==> pip install dependencies"
python -m pip install -r requirements.txt

Write-Host "==> verify hook"
python .cursor/hooks/ailight_hook.py test
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARN: daemon not running yet, will start next"
}

& (Join-Path $ProjectRoot "scripts\start-lightd.ps1") -ProjectRoot $ProjectRoot

python .cursor/hooks/ailight_hook.py test

if ($Autostart) {
    Write-Host "==> register logon autostart (Task Scheduler)"
    & (Join-Path $ProjectRoot "scripts\register-autostart.ps1") -ProjectRoot $ProjectRoot
}

Write-Host ""
Write-Host "=========================================="
Write-Host " aiLight installed"
Write-Host " Project : $ProjectRoot"
Write-Host " Console : http://127.0.0.1:7801/docs"
Write-Host " Hooks   : .cursor/hooks.json + .trae/hooks.json"
if ($Autostart) {
    Write-Host " Autostart: enabled (task aiLight-lightd)"
} else {
    Write-Host " Autostart: powershell -File install.ps1 -Autostart"
}
Write-Host "=========================================="
Write-Host ""
Write-Host "Next steps:"
Write-Host "1) Upload firmware/main.py to ESP32 (mpremote)"
Write-Host "2) Power on board (BLE name aiLight-XXXX)"
Write-Host "3) Restart Cursor or TRAE and open a NEW agent chat"
Write-Host "4) Optional: build exe with scripts\build-lightd.ps1"
