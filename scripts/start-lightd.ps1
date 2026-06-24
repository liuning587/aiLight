# Start aiLight lightd daemon (stop existing listener on port first)
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$ExePath = "",
    [int]$Port = 7801
)

$ErrorActionPreference = "Stop"

function Test-IsLightdProcess {
    param($Process)
    try {
        if ($Process.ProcessName -ieq "lightd") { return $true }
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($Process.Id)" -ErrorAction SilentlyContinue).CommandLine
        if (-not $cmd) { return $false }
        return ($cmd -match "tools\.lightd") -or ($cmd -match "\\lightd\.exe")
    } catch {
        return $false
    }
}

function Stop-LightdOnPort {
    param([int]$ListenPort)
    Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
            if ($proc -and (Test-IsLightdProcess $proc)) {
                Write-Host "==> stop lightd PID $($proc.Id) on port $ListenPort"
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
}

Stop-LightdOnPort -ListenPort $Port
Start-Sleep -Seconds 1

if ($ExePath -and (Test-Path $ExePath)) {
    Write-Host "==> start lightd exe: $ExePath"
    Start-Process -WindowStyle Hidden -FilePath $ExePath -WorkingDirectory (Split-Path -Parent $ExePath)
} else {
    Set-Location $ProjectRoot
    Write-Host "==> start lightd: python -m tools.lightd ($ProjectRoot)"
    Start-Process -WindowStyle Hidden -FilePath "python" -ArgumentList "-m", "tools.lightd" -WorkingDirectory $ProjectRoot
}

Start-Sleep -Seconds 2
Write-Host "==> console: http://127.0.0.1:$Port"
