@echo off
REM Reviewer (reviewer-beast) waker -- polls the board and runs the worker when
REM a job arrives. Executor is codex in review mode; the run wrapper reads the
REM token live from disk, so a rotation needs no edit here.
setlocal
set /p MCO_AGENT_TOKEN=<{{HOME}}\.mco\tokens\reviewer-beast.token
set AGENT_ROLE=reviewer
set AGENT_INSTANCE_ID=reviewer-beast
set MCO_GATEWAY_URL=http://127.0.0.1:18789
"C:\AI\baton\Batoncadence\.venv\Scripts\python.exe" -m mco.cli wake --role reviewer --instance reviewer-beast --exec "{{HOME_FWD}}/.mco/bin/reviewer-beast-run.cmd" --min-interval 10
