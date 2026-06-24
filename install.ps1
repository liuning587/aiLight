# aiLight Windows one-click setup (PromLight-style)
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1

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

Write-Host "==> stop existing lightd on port 7801"
Get-NetTCPConnection -LocalPort 7801 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

Write-Host "==> start lightd daemon"
Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList "-m", "tools.lightd" -WorkingDirectory $ProjectRoot

Start-Sleep -Seconds 2
python .cursor/hooks/ailight_hook.py test

Write-Host ""
Write-Host "=========================================="
Write-Host " aiLight installed"
Write-Host " Project : $ProjectRoot"
Write-Host " Console : http://127.0.0.1:7801"
Write-Host " Hooks   : .cursor/hooks.json (project-level)"
Write-Host "=========================================="
Write-Host ""
Write-Host "Next steps:"
Write-Host "1) Upload firmware/main.py to ESP32 (mpremote)"
Write-Host "2) Power on board (BLE name aiLight-XXXX)"
Write-Host "3) Restart Cursor and open a NEW agent chat"
Write-Host "4) Optional: edit devices.json / config.json"
