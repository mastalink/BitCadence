# Delegation and kill-switch demonstrations

## Delegation

1. This is the live AWS lab through an authenticated SSM tunnel.
2. An operator gives one task to worker-lab.
3. The worker computes a checksum and uses the SDK to create a separate job for reviewer-lab. The operator does not create that follow-up.
4. The reviewer job identifies worker-lab as its source and carries the parent job ID and checksum.
5. Human approval releases the review, and the second EC2 spoke completes it.

This is deterministic computation and delegation, not an autonomous AI reasoning demonstration.

## Kill switch

1. The worker is executing a harmless task bounded to 180 seconds, checking ownership between work units.
2. The operator enables Stop work and saves the gateway settings.
3. The active job becomes halted. The cooperative worker stops at its next checkpoint and withholds its result.
4. A separate diagnostic replays the old ownership claim as a completion request. The expected response is HTTP 409, with the job remaining halted.
5. Turning the switch off resumes intake; it does not restart the halted job. EC2 shutdown is a separate control.

A cooperative checkpoint cannot forcibly interrupt arbitrary external systems. This demo proves the governed worker and result boundary, not universal process termination.

## Repeat the demos

Start the tested AWS workflow revision, wait for acceptance, and connect the SSM tunnel as described in QUICKSTART.md. From the implementation checkout:

```powershell
& C:/AI/baton/Batoncadence/.venv/Scripts/python.exe scripts/cloud_demo.py --action delegate
```

Open the delegated reviewer job in the console and approve it. Then create the finite kill-switch job:

```powershell
& C:/AI/baton/Batoncadence/.venv/Scripts/python.exe scripts/cloud_demo.py --action halt
```

Wait for the helper to say the worker owns the active attempt. In Settings, enable Stop work and save. Inspect the halted job, turn Stop work off and save, then run:

```powershell
& C:/AI/baton/Batoncadence/.venv/Scripts/python.exe scripts/cloud_demo.py --action verify
powershell -File scripts/cloud_lab.ps1 -Action Stop
```

The verify command requires both demos in the same session and fails if delegation, approval, halt, or the late-result fence is missing. Private ownership proof stays in .codex/review; the exported evidence JSON omits it and all credentials.
