# ============================================================================
# BitCadence - Windows bootstrap installer
# ============================================================================
# Usage (run in PowerShell):
#   iwr -useb https://bitcadence.ai/install.ps1 | iex
#
# What this does:
#   1. Detects an existing install (via PATH, common locations, or env var)
#   2. If found: pulls updates, re-runs setup in-place - never double-installs
#   3. If not found: clones to $HOME\BitCadence and runs setup
# ============================================================================
$ErrorActionPreference = "Stop"

$REPO = "https://github.com/mastalink/BitCadence"

Write-Host ""
Write-Host "  BitCadence installer" -ForegroundColor Cyan
Write-Host "  =======================" -ForegroundColor Cyan
Write-Host ""

# ---- 1. Locate an existing install ----------------------------------------
function Find-ExistingInstall {
    # Explicit override wins
    if ($env:BITCADENCE_INSTALL_DIR -and (Test-Path (Join-Path $env:BITCADENCE_INSTALL_DIR ".git"))) {
        return $env:BITCADENCE_INSTALL_DIR
    }

    # mco already on PATH? resolve back to the repo root
    $mcoCmd = Get-Command mco -ErrorAction SilentlyContinue
    if ($mcoCmd) {
        $mcoPath = $mcoCmd.Source
        # Follow .cmd shim -> real exe
        if ($mcoPath -match '\.cmd$') {
            $shimContent = Get-Content $mcoPath -Raw -ErrorAction SilentlyContinue
            if ($shimContent -match '"([^"]+mco\.exe)"') {
                $mcoPath = $matches[1]
            }
        }
        # Expected layout: <repo>\.venv\Scripts\mco.exe
        $candidate = Split-Path (Split-Path (Split-Path $mcoPath))
        if (Test-Path (Join-Path $candidate "pyproject.toml")) {
            $content = Get-Content (Join-Path $candidate "pyproject.toml") -Raw -ErrorAction SilentlyContinue
            if ($content -match "bitcadence|BitCadence|mco") {
                return $candidate
            }
        }
    }

    # Check common install locations
    foreach ($loc in @(
        "$HOME\BitCadence",
        "$HOME\bitcadence",
        "C:\BitCadence",
        "C:\AI\baton\BitCadence"
    )) {
        if ((Test-Path (Join-Path $loc ".git")) -and (Test-Path (Join-Path $loc "pyproject.toml"))) {
            return $loc
        }
    }

    return $null
}

$existing = Find-ExistingInstall

if ($existing) {
    Write-Host "[OK] Found existing BitCadence install at:" -ForegroundColor Green
    Write-Host "     $existing"
    Write-Host ""
    Write-Host "->  Checking for updates..." -ForegroundColor Cyan

    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        git -C $existing fetch --quiet origin 2>$null
        $local  = git -C $existing rev-parse HEAD 2>$null
        $remote = git -C $existing rev-parse origin/main 2>$null
        if ($local -eq $remote) {
            Write-Host "[OK] Already up to date - re-running setup to verify" -ForegroundColor Green
        } else {
            try {
                git -C $existing pull --ff-only origin main 2>$null
                Write-Host "[OK] Updated" -ForegroundColor Green
            } catch {
                Write-Host "[!]  Could not pull (local changes present) - continuing" -ForegroundColor Yellow
            }
        }
    }

    Write-Host ""
    & powershell -ExecutionPolicy Bypass -File (Join-Path $existing "scripts\install.ps1")
    exit
}

# ---- 2. Fresh install -------------------------------------------------------
$DEST = if ($env:BITCADENCE_INSTALL_DIR) { $env:BITCADENCE_INSTALL_DIR } else { "$HOME\BitCadence" }

$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Write-Host "[X]  git is required. Install it from https://git-scm.com/ and retry." -ForegroundColor Red
    Write-Host "     Or download the ZIP directly from: $REPO" -ForegroundColor DarkGray
    exit 1
}

if ((Test-Path $DEST) -and (Get-ChildItem $DEST -ErrorAction SilentlyContinue | Select-Object -First 1)) {
    Write-Host "[X]  $DEST exists and is not empty (and is not a BitCadence repo)." -ForegroundColor Red
    Write-Host "     Move it or set `$env:BITCADENCE_INSTALL_DIR to a different path." -ForegroundColor DarkGray
    exit 1
}

Write-Host "->  Cloning BitCadence to $DEST..." -ForegroundColor Cyan
git clone --depth 1 $REPO $DEST
Write-Host "[OK] Repository ready at $DEST" -ForegroundColor Green
Write-Host ""

& powershell -ExecutionPolicy Bypass -File (Join-Path $DEST "scripts\install.ps1")
