@echo off
REM Claude (claude-cio) CIO/Chief-approval sidecar worker run - invoked by `mco wake` when a job arrives.
REM Thin shim over the shared, parameterized runner
REM (BitCadence scripts/workers/claude-worker-run.ps1). BC_HOME tells it where
REM the BitCadence checkout lives, so no path is baked into the runner.
set BC_HOME=C:\AI\baton\Batoncadence
REM Per-run log (last 50 kept) so a locked shared log can never block a launch.
set RUNLOG=%TEMP%\claude-cio-run-%RANDOM%.log
for /f "delims=" %%i in ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{{HOME}}\.mco\bin\new-run-log.ps1" -Name claude-cio') do set RUNLOG=%%i
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{{HOME}}\.mco\bin\claude-worker-run.ps1" -Instance claude-cio -Role cio -Model opus >> "%RUNLOG%" 2>&1
