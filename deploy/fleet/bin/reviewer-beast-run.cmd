@echo off
REM Runs in its OWN git worktree (C:\AIaton\wt\reviewer-beast), never the shared
REM checkout. Branch, index and working tree are global per checkout, so an
REM unattended worker sharing one with a human session races it. The pre-commit
REM guard refuses a commit here when BC_FLEET_WORKTREE=1 and the repo is shared.
REM Reviewer (reviewer-beast) worker run - invoked by the fleet poll service.
REM
REM Token comes from ~/.mco/tokens/reviewer-beast.token, the same per-instance
REM file the waker uses. It previously came from Windows Credential Manager via
REM fetch_token.py - that entry did not exist, so the fetch crashed, codex ran
REM with an empty token, and the agent sat silently unauthenticated for 18 days
REM while Task Scheduler still reported "Last Result: 0".
powershell.exe -NoProfile -WindowStyle Hidden -Command "$ErrorActionPreference='Stop'; $launch = & '{{HOME_FWD}}/.mco/bin/prepare-codex-worker.ps1' -Role reviewer -Instance reviewer-beast; if (-not $launch) { exit 0 }; try { $env:BC_FLEET_WORKTREE='1'; $wt = & 'C:\AI\baton\Batoncadence\scripts\fleet-worktree.ps1' -Instance reviewer-beast; if ($LASTEXITCODE -ne 0 -or -not $wt) { Write-Error 'fleet-worktree failed'; exit 1 }; Set-Location $wt; $tokFile = Join-Path $env:USERPROFILE '.mco\tokens\reviewer-beast.token'; if (-not (Test-Path $tokFile)) { Write-Error \"no token at $tokFile - run: mco reset-token reviewer-beast\"; exit 1 }; $t = (Get-Content $tokFile -Raw).Trim(); if (-not $t) { Write-Error 'token file is empty'; exit 1 }; $ErrorActionPreference='Continue'; Get-Content '{{HOME}}\.mco\prompts\reviewer-beast.txt' -Raw | & 'C:\nvm4w\nodejs\codex.ps1' exec --model $launch.model -c ('model_reasoning_effort='+$launch.effort) -c features.memories=false -c mcp_servers.mco.env.MCO_AGENT_TOKEN=$t -c mcp_servers.mco.env.AGENT_ROLE=reviewer -c mcp_servers.mco.env.AGENT_INSTANCE_ID=reviewer-beast --skip-git-repo-check - *>> '{{HOME}}\.mco\logs\reviewer-beast.log' } finally { $launch.WorkerMutex.ReleaseMutex(); $launch.WorkerMutex.Dispose() }"
