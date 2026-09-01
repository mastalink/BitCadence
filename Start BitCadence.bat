@echo off
REM ============================================================================
REM Start BitCadence
REM ============================================================================
cd /d "%~dp0"
title BitCadence Server

if not exist ".venv\Scripts\python.exe" (
    echo BitCadence is not installed yet. Double-click install.bat first.
    pause
    exit /b 1
)

REM ------------------------------------------------------------------
REM Read MCO_LOCAL_TOKEN from the global config (~\.mco\.env), falling
REM back to a repo-local .env from older installs.
REM ------------------------------------------------------------------
set MCO_LOCAL_TOKEN=
set "MCO_ENV=%USERPROFILE%\.mco\.env"
if not exist "%MCO_ENV%" set "MCO_ENV=.env"
if exist "%MCO_ENV%" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%MCO_ENV%") do (
        if "%%A"=="MCO_LOCAL_TOKEN" set MCO_LOCAL_TOKEN=%%B
    )
)

REM If a server is already running, just open the console.
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18789/healthz -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
    cls
    echo ============================================================
    echo   BitCadence is already running
    echo ============================================================
    echo.
    if not "%MCO_LOCAL_TOKEN%"=="" (
        echo   Your access token:
        echo.
        echo     %MCO_LOCAL_TOKEN%
        echo.
        echo   Copy that token, paste it in the console, click Connect.
        echo   ^(It has been copied to your clipboard.^)
        powershell -NoProfile -Command "Set-Clipboard '%MCO_LOCAL_TOKEN%'" >nul 2>&1
    )
    echo.
    start "" http://127.0.0.1:18789/console
    exit /b 0
)

cls
echo.
echo ============================================================
echo   Starting BitCadence...
echo ============================================================
echo.

REM Copy the token to clipboard and show it BEFORE the server starts
if not "%MCO_LOCAL_TOKEN%"=="" (
    powershell -NoProfile -Command "Set-Clipboard '%MCO_LOCAL_TOKEN%'" >nul 2>&1
    echo   Your access token ^(already copied to clipboard^):
    echo.
    echo     %MCO_LOCAL_TOKEN%
    echo.
    echo   In 5 seconds your browser will open the console.
    echo   Paste the token into the "Agent token" box and click Connect.
    echo.
    echo   Keep this window open while BitCadence is running.
    echo   Close it to stop BitCadence.
    echo.
    echo ============================================================
) else (
    echo   Opening console in a few seconds...
    echo   Keep this window open. Close it to stop BitCadence.
    echo ============================================================
)

REM Open the console after a short delay so the server is ready.
start "" /b cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:18789/console"

".venv\Scripts\python.exe" -m mco.cli serve
