@echo off
REM Runs in its OWN git worktree (C:\AIaton\wt\grok-beast), never the shared
REM checkout. Branch, index and working tree are global per checkout, so an
REM unattended worker sharing one with a human session races it. The pre-commit
REM guard refuses a commit here when BC_FLEET_WORKTREE=1 and the repo is shared.
REM Grok (grok-beast) worker run - invoked by `mco wake` when a job arrives.
powershell.exe -NoProfile -WindowStyle Hidden -Command "$env:BC_FLEET_WORKTREE='1'; $wt = & 'C:\AI\baton\Batoncadence\scripts\fleet-worktree.ps1' -Instance grok-beast; if ($LASTEXITCODE -ne 0 -or -not $wt) { Write-Error 'fleet-worktree failed'; exit 1 }; Set-Location $wt; & '{{HOME}}\.grok\bin\grok.exe' --prompt-file '{{HOME}}\.mco\prompts\grok-beast.txt' --permission-mode bypassPermissions --output-format plain *>> '{{HOME}}\.mco\logs\grok-beast.log'"
