@echo off
REM agy slot worker run, invoked by `mco wake`. See agy-slot-run.ps1.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{{HOME}}\.mco\bin\agy-slot-run.ps1" -Instance agy-studio-5
