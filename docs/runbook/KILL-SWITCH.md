# Kill switch runbook

One switch: the single file `~/.mco/STOP` (override `MCO_STOP_FILE`). While it exists, the code
that calls the guard starts nothing new. In-flight work finishes; private uploads already scheduled
on YouTube are untouched. **No other file is a signal**: `~/.mco/leo/STOP` (retired Leo) and the old
per-project pause files are not read, and `MCO_LEGACY_PAUSE_FILES` no longer has any effect
(tests/test_killswitch.py::test_only_the_exact_file_is_a_signal proves a configured leo/STOP is ignored).

## Fail-closed behaviour (what is and is not proven)
`stop_active()` uses `os.stat`. A definite "not found" means not stopped; any other OS error
(for example permission denied on the path) is treated as STOPPED. This is covered by
`test_unreadable_state_fails_closed`, which mocks `os.stat` to raise PermissionError. Limitation:
this was verified by mocking only, not against a real ACL-denied directory on Windows.

## Wiring status (honest)
- Wired and unit-tested in this repo: Score conductor sweep (`score_sweep.sweep`), agentd supervisor `_spawn`.
- NOT wired (live outside this repo or have no caller): shepherd, watchdog scheduler, uploader,
  worker wrappers. Each needs `killswitch.guard()` at the top of its loop. Until then the claim
  "every actor stops" is false; only the two above stop.
- Phone: `handle_phone_command` is a helper only. No code in `src/` calls it, so there is no
  authenticated phone transport route today. Sending STOP from a phone does NOT currently reach it:
  transport wiring and end-to-end delivery are UNVERIFIED. Owner identity (`sender_is_owner`) must
  come from that future authenticated transport.

## Set
- Desk: `python -c "from mco.killswitch import set_stop; set_stop('desk')"` or create `~/.mco/STOP`.
- Phone: not available until the transport is wired (see above).

## Clear (owner only)
Nothing automates this; the helper refuses RESUME. The owner deletes `~/.mco/STOP` by hand after
checking why it was set (the file holds source and time).

## Drill
1. Confirm no STOP file. 2. Set it (use `MCO_STOP_FILE` pointing at a temp path for a rehearsal).
3. Run `pytest tests/test_killswitch.py tests/test_agentd_supervisor.py tests/test_score_sweep.py`.
4. Confirm a sweep returns `skipped {"_all": "kill_switch"}` and the supervisor spawns nothing.
5. The owner deletes the file; confirm work resumes. Log each step with a timestamp.
Not drilled live: scheduler, shepherd, uploader, wrappers, phone.
