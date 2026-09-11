# ============================================================================
# BitCadence Setup Script (Windows)
# ============================================================================
# One-shot install, modeled on hermes' setup-hermes.sh:
#   1. Locates Python 3.9+ (offers to install it via winget if missing)
#   2. Creates a virtual environment (.venv)
#   3. Installs BitCadence in editable mode
#   4. Creates a safe Local-Only .env (if none exists)
#   5. Creates a "BitCadence" Desktop shortcut that starts the server
#      and opens the console GUI in the default browser
#   6. Offers to launch BitCadence right away
#
# Usage (or just double-click install.bat in the repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# Pure ASCII output on purpose: legacy Windows consoles crash on unicode.
# ============================================================================
param(
    # Unattended mode: never prompt (used by CI / scripted installs).
    [switch]$NoPrompt
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($m) { Write-Host "->  $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[X]  $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  BitCadence Setup" -ForegroundColor Cyan
Write-Host "  ==================" -ForegroundColor Cyan
Write-Host ""

# ----------------------------------------------------------------------------
# 0. Check for updates
# ----------------------------------------------------------------------------
Step "Checking for updates..."

$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Write-Host "     git not found - skipping update check." -ForegroundColor DarkGray
    Write-Host "     To get updates, download the latest ZIP from:" -ForegroundColor DarkGray
    Write-Host "     https://github.com/mastalink/BitCadence" -ForegroundColor DarkGray
} else {
    # Is this folder a git repo?
    $gitDir = git -C $root rev-parse --git-dir 2>$null
    if (-not $gitDir) {
        Write-Host "     This folder is not a git repo (probably a ZIP download)." -ForegroundColor DarkGray
        Write-Host "     To get updates, download the latest ZIP from:" -ForegroundColor DarkGray
        Write-Host "     https://github.com/mastalink/BitCadence" -ForegroundColor DarkGray
    } else {
        # Fetch quietly so we can compare local vs remote
        git -C $root fetch --quiet origin 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "     Could not reach GitHub (offline?) - skipping update check." -ForegroundColor DarkGray
        } else {
            $localHash  = git -C $root rev-parse HEAD 2>$null
            $remoteHash = git -C $root rev-parse origin/main 2>$null
            if ($localHash -eq $remoteHash) {
                Ok "Already up to date"
            } else {
                $behind = [int](git -C $root rev-list "HEAD..origin/main" --count 2>$null)
                $ahead  = [int](git -C $root rev-list "origin/main..HEAD" --count 2>$null)
                if ($behind -gt 0) {
                    Write-Host ""
                    Write-Host "  ============================================================" -ForegroundColor Yellow
                    $word = if ($behind -eq 1) { "update" } else { "updates" }
                    Write-Host "  $behind new $word available on GitHub." -ForegroundColor Yellow
                    Write-Host "  ============================================================" -ForegroundColor Yellow
                    Write-Host ""
                    # Show the last few incoming commit messages so the user knows what changed
                    $log = git -C $root log "HEAD..origin/main" --oneline --no-decorate 2>$null
                    if ($log) {
                        Write-Host "  What's new:" -ForegroundColor Cyan
                        $log -split "`n" | Select-Object -First 8 | ForEach-Object {
                            Write-Host "    $_" -ForegroundColor White
                        }
                        Write-Host ""
                    }
                    if ($NoPrompt) {
                        Write-Host "  Pulling updates (-NoPrompt)..." -ForegroundColor Cyan
                        git -C $root pull --ff-only origin main 2>&1 | ForEach-Object { Write-Host "  $_" }
                        Ok "Updated to latest version"
                    } else {
                        $upd = Read-Host "  Pull updates now? [Y/n]"
                        if ($upd -eq "" -or $upd -match "^[Yy]") {
                            git -C $root pull --ff-only origin main 2>&1 | ForEach-Object { Write-Host "  $_" }
                            Ok "Updated to latest version"
                        } else {
                            Warn "Skipping update - continuing with current version"
                        }
                    }
                } elseif ($ahead -gt 0) {
                    Warn "Your copy is $ahead commit(s) ahead of GitHub (local changes present)"
                }
            }
        }
    }
}
Write-Host ""

# ----------------------------------------------------------------------------
# 1. Find Python 3.9+
# ----------------------------------------------------------------------------
Step "Checking for Python 3.9 or newer..."

function Find-Python {
    foreach ($cand in @("python", "py")) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $exe = $cmd.Source
        $args = @()
        if ($cand -eq "py") { $args = @("-3") }
        & $exe @args -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return ,@($exe) + $args
        }
    }
    return $null
}

$pythonCmd = Find-Python
if ($pythonCmd) {
    $ver = & $pythonCmd[0] $pythonCmd[1..($pythonCmd.Count)] --version
    Ok "$ver found"
} else {
    Warn "Python 3.9+ was not found on this computer."
    if ($NoPrompt) { Fail "Python is required. Install it from https://www.python.org/downloads/ and retry." }
    $answer = Read-Host "Install Python automatically with winget? [Y/n]"
    if ($answer -eq "" -or $answer -match "^[Yy]") {
        Step "Installing Python via winget (this can take a few minutes)..."
        winget install --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        # Refresh PATH for this session so the new install is visible
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
        $pythonCmd = Find-Python
        if (-not $pythonCmd) {
            Fail "Python was installed but is not yet visible. Close this window and run install.bat again."
        }
        Ok "Python installed"
    } else {
        Fail "Python is required. Install it from https://www.python.org/downloads/ and run install.bat again."
    }
}

# ----------------------------------------------------------------------------
# 2. Virtual environment
# ----------------------------------------------------------------------------
Step "Setting up the virtual environment (.venv)..."

if (-not (Test-Path (Join-Path $root ".venv\Scripts\python.exe"))) {
    & $pythonCmd[0] $pythonCmd[1..($pythonCmd.Count)] -m venv (Join-Path $root ".venv")
    Ok "Virtual environment created"
} else {
    Ok "Virtual environment already exists"
}

$py = Join-Path $root ".venv\Scripts\python.exe"

# ----------------------------------------------------------------------------
# 3. Install BitCadence
# ----------------------------------------------------------------------------
Step "Installing BitCadence and its dependencies (this can take a minute)..."

# Air-gapped install: an offline\wheels folder (created by
# scripts\make-offline-bundle.ps1 on a connected machine) means we install
# entirely from local wheels - no internet required, nothing leaves the host.
$wheelDir = Join-Path $root "offline\wheels"
if (Test-Path $wheelDir) {
    Write-Host "     Offline wheel bundle detected - installing without network access." -ForegroundColor DarkGray
    & $py -m pip install --no-index --find-links "$wheelDir" --upgrade pip --quiet
    & $py -m pip install --no-index --find-links "$wheelDir" -e "$root" --quiet
    if ($LASTEXITCODE -ne 0) { Fail "Offline installation failed. Rebuild the bundle with make-offline-bundle.ps1 on a machine with the same OS/Python." }
} else {
    & $py -m pip install --upgrade pip --quiet
    & $py -m pip install -e "$root" --quiet
    if ($LASTEXITCODE -ne 0) { Fail "Dependency installation failed. Check your internet connection and retry." }
}
Ok "BitCadence installed"

# ----------------------------------------------------------------------------
# 4. Configuration - global home (~\.mco\.env) so mco works from ANY directory
# ----------------------------------------------------------------------------
$mcoHome = Join-Path $env:USERPROFILE ".mco"
if (-not (Test-Path $mcoHome)) { New-Item -ItemType Directory -Path $mcoHome | Out-Null }
$envPath = Join-Path $mcoHome ".env"
$repoEnv = Join-Path $root ".env"

if ((Test-Path $repoEnv) -and -not (Test-Path $envPath)) {
    # Migrate an older install: one source of truth, in the global home.
    Move-Item $repoEnv $envPath
    Ok "Moved existing configuration to $envPath (works from any directory now)"
}

if (-not (Test-Path $envPath)) {
    # Generate a secure local access token (used in place of a database token).
    $localToken = "mco_tok_" + (& $py -c "import secrets; print(secrets.token_hex(24))")
    Set-Content -Path $envPath -Encoding ascii -Value @(
        "# BitCadence configuration (created by install.ps1)",
        "# Local-Only profile: everything runs on this computer, no database",
        "# or cloud account needed. Run 'mco setup' later to change anything.",
        "MCO_PROFILE=Local-Only",
        "OPERATOR_NAME=$env:USERNAME",
        "MCO_LOCAL_TOKEN=$localToken"
    )
    Ok "Created Local-Only configuration ($envPath) with access token"
} else {
    # Add MCO_LOCAL_TOKEN if it is missing from an existing config
    $envContent = Get-Content $envPath -Raw
    if ($envContent -notmatch 'MCO_LOCAL_TOKEN') {
        $localToken = "mco_tok_" + (& $py -c "import secrets; print(secrets.token_hex(24))")
        Add-Content -Path $envPath -Encoding ascii -Value "MCO_LOCAL_TOKEN=$localToken"
        Ok "Added MCO_LOCAL_TOKEN to existing configuration"
    } else {
        Ok "Configuration already exists at $envPath - leaving it untouched"
    }
}

# ----------------------------------------------------------------------------
# 4b. Put 'mco' on the PATH - works in any terminal, any directory
# ----------------------------------------------------------------------------
Step "Making the 'mco' command available everywhere..."

$binDir = Join-Path $env:LOCALAPPDATA "BitCadence\bin"
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
$mcoExe = Join-Path $root ".venv\Scripts\mco.exe"
Set-Content -Path (Join-Path $binDir "mco.cmd") -Encoding ascii -Value @(
    "@echo off",
    "`"$mcoExe`" %*"
)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*BitCadence\bin*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    Ok "'mco' added to your PATH (open a NEW terminal to use it)"
} else {
    Ok "'mco' is already on your PATH"
}

# ----------------------------------------------------------------------------
# 5. Smoke test - make sure the CLI actually imports and runs
# ----------------------------------------------------------------------------
Step "Verifying the installation..."
& $py -m mco.cli --help | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "The mco CLI failed its self-check." }
Ok "CLI self-check passed"

# ----------------------------------------------------------------------------
# 6. Desktop shortcut -> "Start BitCadence.bat"
# ----------------------------------------------------------------------------
Step "Creating the Desktop shortcut..."

$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop "BitCadence.lnk"))
$shortcut.TargetPath = Join-Path $root "Start BitCadence.bat"
$shortcut.WorkingDirectory = $root
$shortcut.Description = "Start BitCadence and open the console GUI"
$shortcut.Save()
Ok "Shortcut 'BitCadence' added to the Desktop"

# ----------------------------------------------------------------------------
# Done - choose your starting mode
# ----------------------------------------------------------------------------
Write-Host ""
Ok "Setup complete!"
Write-Host ""

if ($NoPrompt) {
    Write-Host "Run 'Start BitCadence.bat' to launch." -ForegroundColor Cyan
    exit 0
}

# Read the token we just generated/verified (it's in the global config home)
$localToken = (Get-Content $envPath | Where-Object { $_ -match "^MCO_LOCAL_TOKEN=" }) -replace "^MCO_LOCAL_TOKEN=",""

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  How do you want to start?" -ForegroundColor Cyan
Write-Host "============================================================"
Write-Host ""
Write-Host "  [1] Demo mode    Look around with sample data first." -ForegroundColor Yellow
Write-Host "                   The console shows simulated jobs and agents."
Write-Host "                   You can connect to the live server any time."
Write-Host ""
Write-Host "  [2] Connect now  Get the console talking to this computer" -ForegroundColor Green
Write-Host "                   right away. Takes about 30 seconds."
Write-Host ""
$modeChoice = Read-Host "Choose [1] or [2] (default: 1)"
if ($modeChoice -eq "") { $modeChoice = "1" }

Write-Host ""

if ($modeChoice -eq "2") {
    # ---- CONNECT NOW path ----
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Connect the console to your server" -ForegroundColor Green
    Write-Host "============================================================"
    Write-Host ""
    Write-Host "  Your access token:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "    $localToken" -ForegroundColor White
    Write-Host ""
    if ($localToken) {
        try { Set-Clipboard $localToken } catch {}
        Write-Host "  (Copied to your clipboard.)" -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "  When the browser opens:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "    1. The Gateway URL box already says http://127.0.0.1:18789"
    Write-Host "       -- leave it as-is."
    Write-Host ""
    Write-Host "    2. Click the 'Agent token' box and press Ctrl+V to paste."
    Write-Host ""
    Write-Host "    3. Click Connect."
    Write-Host ""
    Write-Host "  That's it. The console switches from demo data to real data."
    Write-Host ""
    Write-Host "  Profile: Local-Only (everything runs on this computer)." -ForegroundColor DarkGray
    Write-Host "  To add a cloud database later, run:" -ForegroundColor DarkGray
    Write-Host "    .venv\Scripts\python.exe -m mco.cli setup" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Starting BitCadence and opening your browser..." -ForegroundColor Green
    Write-Host "============================================================"
    Write-Host ""
    Start-Process -FilePath (Join-Path $root "Start BitCadence.bat") -WorkingDirectory $root

} else {
    # ---- DEMO MODE path ----
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host "  Demo mode" -ForegroundColor Yellow
    Write-Host "============================================================"
    Write-Host ""
    Write-Host "  The console will open showing sample jobs, agents, and"
    Write-Host "  workflows so you can explore the interface."
    Write-Host ""
    Write-Host "  When you're ready to switch to real data:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "    1. Double-click the BitCadence icon on your Desktop."
    Write-Host "    2. Your browser opens -- look for the Settings panel."
    Write-Host "    3. Leave Gateway URL as http://127.0.0.1:18789"
    Write-Host "    4. Paste your access token in the 'Agent token' box:"
    Write-Host ""
    if ($localToken) {
        Write-Host "         $localToken" -ForegroundColor White
        Write-Host ""
        Write-Host "       (Also saved in .env in the BitCadence folder.)" -ForegroundColor DarkGray
    } else {
        Write-Host "       Run install again - no token was generated." -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "    5. Click Connect." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Yellow
    $launch = Read-Host "Open BitCadence in demo mode now? [Y/n]"
    if ($launch -eq "" -or $launch -match "^[Yy]") {
        Start-Process -FilePath (Join-Path $root "Start BitCadence.bat") -WorkingDirectory $root
    } else {
        Write-Host ""
        Write-Host "When you're ready, double-click the BitCadence icon on your Desktop." -ForegroundColor Cyan
    }
}
