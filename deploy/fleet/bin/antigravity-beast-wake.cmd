@echo off
REM Antigravity (antigravity-beast) waker -- polls the board and runs the
REM worker when a job arrives. Token read live from disk so a rotation needs
REM no edit here.
setlocal
set /p MCO_AGENT_TOKEN=<{{HOME}}\.mco\tokens\antigravity-beast.token
set AGENT_ROLE=antigravity
set AGENT_INSTANCE_ID=antigravity-beast
set MCO_GATEWAY_URL=http://127.0.0.1:18789
"C:\AI\baton\Batoncadence\.venv\Scripts\python.exe" -m mco.cli wake --role antigravity --instance antigravity-beast --exec "{{HOME_FWD}}/.mco/bin/antigravity-beast-run.cmd" --min-interval 10
