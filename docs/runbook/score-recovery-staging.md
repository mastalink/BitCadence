# Staged blocked-run recovery

`mco.orchestrator.score_recovery.RecoveryStaging` is an inert work-step API.
It does not activate recovery, create jobs, issue authority or send alerts.
The live sweep remains unchanged until the separately approved go step.

```python
staging = RecoveryStaging(conductor.bridge)
proposal = staging.stage(blocked_run_id, board=conductor.board,
                         standing_grant_id=trusted_standing_grant_reference)
alarm = staging.observe_dispatches(conductor_identity,
                                   successfully_dispatched_job_ids)
```

Proposals retain the block reason, immutable policy digest, grant reference,
accepted evidence references and the last review-accepted checkpoint. Validated
work alone is never a checkpoint. Each parent has one durable proposal; SQLite
transactions serialize concurrent callers. Two fresh child IDs are reserved
per lineage; the next block stages escalation to the Chief. A rejected human
gate stages escalation immediately. No run is reset or overwritten.

A grant reference is context, not authority. Current signed grants bind to an
exact run ID. Before activating a proposed child, the go-step implementation
must obtain child-run authorization through a trusted standing-grant service,
revalidate scope and expiry, reverify carried evidence, inspect uncertain
effects and reevaluate human gates. Never edit or transplant a signed grant.
Preserve the original Score definition/digest and retain accepted artifacts in
place. Child creation/dispatch is intentionally absent from this work step.

The activity observer takes successful submitted job IDs, scoped to one
conductor. Feed it every sweep, including empty sweeps. It starts its baseline
on first observation, persists across process restarts, ignores replayed IDs,
and returns one staged alarm at exactly 43,200 seconds without a new job.
A new job clears the episode; another 12-hour gap can alarm again. Plans,
acceptances and heartbeats must not be supplied as submissions. A regressing
or non-finite clock is rejected. The matching go step owns wiring and alert
transport; this API neither sends a phone notification nor enables a service.

Verification: `PYTHONPATH=src python -m pytest tests/test_score_recovery_staging.py
 tests/test_score_conductor.py tests/test_score_sweep.py tests/test_score_bridge.py
 tests/test_score_failure_alerts.py -q` (one command; join the lines).
