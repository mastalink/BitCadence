# ============================================================================
# BitCadence - offline (air-gapped) bundle builder (Windows)
# ============================================================================
# Run this on a CONNECTED machine with the same OS family and Python minor
# version as the target. It produces dist\bitcadence-offline.zip containing
# the full repo plus every wheel needed to install with zero network access.
#
# On the air-gapped target:
#   1. Copy the zip over (USB, file transfer, whatever your policy allows)
#   2. Expand it and double-click install.bat (or run scripts\install.ps1)
#      The installer detects offline\wheels and uses --no-index automatically.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\make-offline-bundle.ps1
# ============================================================================
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "  BitCadence offline bundle builder" -ForegroundColor Cyan
Write-Host "  ====================================" -ForegroundColor Cyan
Write-Host ""

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Write-Host "[X] No Python found. Run install.bat first." -ForegroundColor Red; exit 1 }
    $py = $cmd.Source
}

$stage = Join-Path $root "dist\offline-stage"
$wheels = Join-Path $stage "offline\wheels"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $wheels | Out-Null

Write-Host "->  Downloading all dependency wheels (this machine's platform/Python)..." -ForegroundColor Cyan
& $py -m pip download -d $wheels "$root" --quiet
& $py -m pip download -d $wheels pip setuptools wheel --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "[X] Wheel download failed." -ForegroundColor Red; exit 1 }
$count = (Get-ChildItem $wheels | Measure-Object).Count
Write-Host "[OK] $count wheels downloaded" -ForegroundColor Green

Write-Host "->  Staging the repository (tracked files only)..." -ForegroundColor Cyan
$archive = Join-Path $stage "repo.zip"
git -C $root archive --format=zip -o $archive HEAD
Expand-Archive -Path $archive -DestinationPath $stage -Force
Remove-Item $archive

$zip = Join-Path $root "dist\bitcadence-offline.zip"
if (Test-Path $zip) { Remove-Item $zip }
Write-Host "->  Compressing bundle..." -ForegroundColor Cyan
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip
Remove-Item -Recurse -Force $stage

$size = "{0:N1} MB" -f ((Get-Item $zip).Length / 1MB)
Write-Host ""
Write-Host "[OK] Bundle ready: $zip ($size)" -ForegroundColor Green
Write-Host ""
Write-Host "  Move it to the air-gapped machine, expand it, and run install.bat." -ForegroundColor Cyan
Write-Host "  The installer detects offline\wheels and never touches the network." -ForegroundColor Cyan
