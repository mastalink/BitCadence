@echo off
REM ============================================================================
REM BitCadence One-Click Installer
REM ============================================================================
REM Double-click this file to install BitCadence on Windows.
REM It runs scripts\install.ps1, which:
REM   1. Finds (or installs) Python 3.9+
REM   2. Creates a private virtual environment (.venv)
REM   3. Installs BitCadence and its dependencies
REM   4. Writes a safe Local-Only configuration (.env)
REM   5. Puts a "BitCadence" shortcut on your Desktop
REM ============================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
echo.
pause
