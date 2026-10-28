@echo off
setlocal
set /p MCO_AGENT_TOKEN=<{{HOME}}\.mco\tokens\claude-cio.token
set AGENT_ROLE=cio
set AGENT_INSTANCE_ID=claude-cio
set MCO_GATEWAY_URL=http://127.0.0.1:18789
"C:\AI\baton\Batoncadence\.venv\Scripts\python.exe" -m mco.cli wake --role cio --instance claude-cio --exec "{{HOME_FWD}}/.mco/bin/claude-cio-run.cmd" --min-interval 10
