@echo off
REM Stealth OpenRouter (stealth-beast) worker run ? invoked by mco wake / OS waker service.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{{HOME}}\.mco\bin\stealth-beast-run.ps1"
