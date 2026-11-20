@echo off
REM OpenCode (opencode-beast) waker -- polls the board and runs the worker when
REM a job arrives. Mirrors codex-beast-wake.cmd. Token loaded live from disk so
REM a rotation is picked up without editing this file.
setlocal
set /p MCO_AGENT_TOKEN=<{{HOME}}\.mco\tokens\opencode-beast.token
set AGENT_ROLE=opencode
set AGENT_INSTANCE_ID=opencode-beast
set MCO_GATEWAY_URL=http://127.0.0.1:18789
"C:\AI\baton\Batoncadence\.venv\Scripts\python.exe" -m mco.cli wake --role opencode --instance opencode-beast --exec "{{HOME_FWD}}/.mco/bin/opencode-beast-run.cmd" --min-interval 10
