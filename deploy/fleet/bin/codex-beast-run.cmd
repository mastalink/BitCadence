@echo off
REM Runs in its OWN git worktree (C:\AIaton\wt\codex-beast), never the shared
REM checkout. Branch, index and working tree are global per checkout, so an
REM unattended worker sharing one with a human session races it. The pre-commit
REM guard refuses a commit here when BC_FLEET_WORKTREE=1 and the repo is shared.
REM Codex (codex-beast) worker run - invoked by `mco wake` when a job arrives.
REM No -c overrides needed: ~/.codex/config.toml already has the mco (and genesis) MCP servers wired.
REM Start OUTSIDE the repo: the repo's .mcp.json defines mco with claude-beast's
REM identity and would shadow this worker's codex-beast identity from ~/.codex/config.toml.
powershell.exe -NoProfile -WindowStyle Hidden -Command "$ErrorActionPreference='Stop'; $launch = & '{{HOME_FWD}}/.mco/bin/prepare-codex-worker.ps1' -Role codex -Instance codex-beast; if (-not $launch) { exit 0 }; try { $env:BC_FLEET_WORKTREE='1'; $wt = & 'C:\AI\baton\Batoncadence\scripts\fleet-worktree.ps1' -Instance codex-beast; if ($LASTEXITCODE -ne 0 -or -not $wt) { Write-Error 'fleet-worktree failed'; exit 1 }; Set-Location $wt; $ErrorActionPreference='Continue'; Get-Content '{{HOME}}\.mco\prompts\codex-beast.txt' -Raw | & 'C:\nvm4w\nodejs\codex.ps1' exec --model $launch.model -c ('model_reasoning_effort='+$launch.effort) -c features.memories=false --skip-git-repo-check - *>> '{{HOME}}\.mco\logs\codex-beast.log' } finally { $launch.WorkerMutex.ReleaseMutex(); $launch.WorkerMutex.Dispose() }"
