# Flash aiLight firmware to ESP32-C3 via mpremote
param(
    [string]$Port = "",
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$Firmware = Join-Path $ProjectRoot "firmware\main.py"
if (-not (Test-Path $Firmware)) {
    throw "Firmware not found: $Firmware"
}

Write-Host "==> ensure mpremote"
python -m pip install mpremote -q

function Get-SerialPorts {
    Get-CimInstance Win32_SerialPort |
        Where-Object { $_.DeviceID -match '^COM\d+$' } |
        Select-Object -ExpandProperty DeviceID
}

if (-not $Port) {
    $ports = @(Get-SerialPorts)
    if ($ports.Count -eq 0) {
        throw "No COM port found. Connect ESP32 via USB or pass -Port COMx"
    }
    if ($ports.Count -eq 1) {
        $Port = $ports[0]
        Write-Host "==> auto-selected port: $Port"
    } else {
        Write-Host "Multiple COM ports detected:"
        for ($i = 0; $i -lt $ports.Count; $i++) {
            Write-Host "  [$i] $($ports[$i])"
        }
        $choice = Read-Host "Select port index (0-$($ports.Count - 1))"
        $Port = $ports[[int]$choice]
    }
}

Write-Host "==> upload firmware to $Port"
mpremote connect $Port fs cp $Firmware :main.py
if ($LASTEXITCODE -ne 0) {
    throw "mpremote upload failed (is the port in use by another app?)"
}

Write-Host "==> reset board"
mpremote connect $Port reset
Write-Host ""
Write-Host "Done. Board should advertise as aiLight-XXXX over BLE."
Write-Host "Next: run install.ps1 or start-lightd.ps1, then bind at http://127.0.0.1:7801"
