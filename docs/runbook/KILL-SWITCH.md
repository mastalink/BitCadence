# Kill switch runbook

One switch: the file `~/.mco/STOP` (override `MCO_STOP_FILE`). While it exists the shepherd,
watchdog scheduler, Score conductor sweep, uploader, worker wrappers and agentd supervisor start
nothing new. In-flight work finishes; private uploads already scheduled on YouTube are untouched.
Old per-project pause files may be listed in `MCO_LEGACY_PAUSE_FILES` and act as aliases.
`~/.mco/leo/STOP` (retired Leo) is NOT a signal. If the file state cannot be read, the switch is
treated as set (fail closed).

## Set
- Phone: the owner sends `STOP` (also `STOP ALL`, `KILL`, `HALT`) -> `killswitch.handle_phone_command`.
- Desk: `python -c "from mco.killswitch import set_stop; set_stop('desk')"` or create the file.

## Clear (owner only)
Nothing automates this and the phone command `RESUME` is refused. Joseph deletes `~/.mco/STOP`
by hand after checking why it was set (the file holds source/time).

## Drill
1. Confirm no STOP file. 2. Set it. 3. Run `pytest tests/test_killswitch.py`.
4. Confirm a sweep returns `skipped {"_all": "kill_switch"}` and the supervisor spawns nothing.
5. Joseph deletes the file; confirm work resumes. Log each step with a time.

## Wiring status
Wired: Score sweep, agentd supervisor spawn. Call `killswitch.guard()` at the top of any other
actor; the shepherd, uploader and wrapper scripts live outside this repo and still need the call.
