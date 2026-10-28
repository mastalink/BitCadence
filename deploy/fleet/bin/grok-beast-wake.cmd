@echo off
setlocal
set /p MCO_AGENT_TOKEN=<{{HOME}}\.mco\tokens\grok-beast.token
set AGENT_ROLE=grok
set AGENT_INSTANCE_ID=grok-beast
set MCO_GATEWAY_URL=http://127.0.0.1:18789
"C:\AI\baton\Batoncadence\.venv\Scripts\python.exe" -m mco.cli wake --role grok --instance grok-beast --exec "{{HOME_FWD}}/.mco/bin/grok-beast-run.cmd" --min-interval 10
