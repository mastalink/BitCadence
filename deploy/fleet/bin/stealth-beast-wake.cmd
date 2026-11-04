@echo off
REM Stealth-beast wake daemon. Token loaded live from disk so rotation needs no edit.
setlocal
set /p MCO_AGENT_TOKEN=<{{HOME}}\.mco\tokens\stealth-beast.token
set AGENT_ROLE=stealth
set AGENT_INSTANCE_ID=stealth-beast
set MCO_GATEWAY_URL=http://127.0.0.1:18789
"C:\AI\baton\Batoncadence\.venv\Scripts\python.exe" -m mco.cli wake --role stealth --instance stealth-beast --exec "{{HOME_FWD}}/.mco/bin/stealth-beast-run.cmd" --min-interval 10
