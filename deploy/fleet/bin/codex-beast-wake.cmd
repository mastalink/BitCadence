@echo off
setlocal
set /p MCO_AGENT_TOKEN=<{{HOME}}\.mco\tokens\codex-beast.token
set AGENT_ROLE=codex
set AGENT_INSTANCE_ID=codex-beast
set MCO_GATEWAY_URL=http://127.0.0.1:18789
"C:\AI\baton\Batoncadence\.venv\Scripts\python.exe" -m mco.cli wake --role codex --instance codex-beast --exec "{{HOME_FWD}}/.mco/bin/codex-beast-run.cmd" --min-interval 10
