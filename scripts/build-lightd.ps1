# Build standalone lightd.exe with PyInstaller (optional; requires Python + pip)
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

Write-Host "==> install build dependencies"
python -m pip install -r requirements.txt pyinstaller

$distDir = Join-Path $ProjectRoot "dist"
$entry = Join-Path $ProjectRoot "scripts\lightd_entry.py"
$sep = ";"

$addData = @(
    "tools\lightd\console.html$sep tools\lightd",
    "tools\lightd\docs.html$sep tools\lightd",
    "docs\使用说明.md$sep docs"
)

$addDataArgs = $addData | ForEach-Object { "--add-data"; $_ }

Write-Host "==> pyinstaller"
python -m PyInstaller `
    --onefile `
    --name lightd `
    --distpath $distDir `
    --workpath (Join-Path $ProjectRoot "build\pyinstaller") `
    --specpath (Join-Path $ProjectRoot "build\pyinstaller") `
    --clean `
    @addDataArgs `
    --hidden-import bleak `
    --hidden-import bleak.backends.winrt `
    $entry

$exe = Join-Path $distDir "lightd.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: $exe not found"
}

# Ship writable config next to exe
foreach ($name in @("config.json", "devices.json")) {
    $src = Join-Path $ProjectRoot $name
    $dst = Join-Path $distDir $name
    if ((Test-Path $src) -and -not (Test-Path $dst)) {
        Copy-Item $src $dst
    }
}

Write-Host ""
Write-Host "=========================================="
Write-Host " Built: $exe"
Write-Host " Copy dist\lightd.exe + config.json + devices.json to target folder"
Write-Host " Autostart: scripts\register-autostart.ps1 -ExePath `"$exe`""
Write-Host "=========================================="
