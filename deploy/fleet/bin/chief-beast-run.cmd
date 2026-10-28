@echo off
REM BC_FLEET_WORKTREE=1 arms the pre-commit guard: if this worker ever ends up
REM in the shared checkout, a commit there is refused rather than racing a human.
REM THE CHIEF (chief-beast) worker run - invoked by `mco wake` when a job arrives.
REM
REM Starts in {{HOME}}, NOT the repo. Codex lets a project .mcp.json shadow
REM ~/.codex/config.toml, and C:\AI\baton\Batoncadence\.mcp.json defines the mco
REM server with claude-beast's identity - a codex worker started there silently
REM adopts the wrong agent identity (attribution corruption while the token is
REM live). THE CHIEF never edits code, so it has no reason to be in the repo.
REM
REM   - per-instance token read from ~/.mco/tokens/chief-beast.token
REM   - features.memories=false so the worker cannot lease stale task ids from
REM     unrelated Codex sessions
REM   - genesis MCP comes from ~/.codex/config.toml at user scope; no override
REM     needed, and it must stay reachable - it is THE CHIEF's memory of Joseph.
powershell.exe -NoProfile -WindowStyle Hidden -Command "$ErrorActionPreference='Stop'; $launch = & '{{HOME_FWD}}/.mco/bin/prepare-codex-worker.ps1' -Role chief -Instance chief-beast; if (-not $launch) { exit 0 }; try { $env:BC_FLEET_WORKTREE='1'; Set-Location '{{HOME}}'; $tokFile = Join-Path $env:USERPROFILE '.mco\tokens\chief-beast.token'; if (-not (Test-Path $tokFile)) { Write-Error \"no token at $tokFile - run: mco reset-token chief-beast\"; exit 1 }; $t = (Get-Content $tokFile -Raw).Trim(); if (-not $t) { Write-Error 'token file is empty'; exit 1 }; $ErrorActionPreference='Continue'; Get-Content '{{HOME}}\.mco\prompts\chief-beast.txt' -Raw | & 'C:\nvm4w\nodejs\codex.ps1' exec --model $launch.model -c ('model_reasoning_effort='+$launch.effort) -c features.memories=false -c mcp_servers.mco.tool_timeout_sec=180 -c mcp_servers.mco.env.MCO_HTTP_TIMEOUT=170 -c mcp_servers.mco.env.MCO_AGENT_TOKEN=$t -c mcp_servers.mco.env.AGENT_ROLE=chief -c mcp_servers.mco.env.AGENT_INSTANCE_ID=chief-beast --skip-git-repo-check - *>> '{{HOME}}\.mco\logs\chief-beast.log' } finally { $launch.WorkerMutex.ReleaseMutex(); $launch.WorkerMutex.Dispose() }"
