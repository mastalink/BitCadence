# S06b: repository:write Adversarial Proof Run — 2026-09-17

## Executive Summary

REPO-WRITE-1 and REPO-WRITE-2 implemented and unit-tested the foundational safety controls for the live Git repository write adapter (`repository-worktree-commit`), including branch denylists, exact-HEAD fencing, allowed path filters, evidence hashing, and atomic recovery.

This document records the results of **REPO-WRITE-3**: the **S06b-style adversarial proof run**. The objective of this packet is not to test Python functions in memory, but to prove that the conductor and live adapter enforce all safety invariants under **real process-level conditions**: a running FastAPI gateway, a real conductor tick loop, out-of-process worker and reviewer agents communicating over HTTP/Bearer tokens, process hard-kills (`SIGKILL`/`TerminateProcess`), out-of-band repository tampering, and real Git operations against a throwaway repository.

### Summary of Results

Seven runs of the canary score (`via-score-repo-write-canary`) were executed against an isolated local environment on port `18997`:

| Run ID | Scenario | Injection | Outcome | Governed? |
|---|---|---|---|---|
| `s06b-01-baseline` | **Baseline Canary** | None (clean happy-path) | `accepted` in ~40s; commit created & verified | control |
| `s06b-02-crash` | **Worker Crash Mid-Task** | Worker killed holding lease (`os._exit(137)`) | `accepted` after lease TTL expiry & reaper reclamation; dead attempt wrote 0 commits | yes |
| `s06b-03-denied-branch` | **Denied Branch Target** | Score attempts to target `main` | **Refused at `initialize()`** (code 1); 0 runs created in DB; 0 git commands run | yes |
| `s06b-04-exact-head-drift` | **Exact-HEAD Drift** | Out-of-band commit injected before ready signal | **`blocked`**; live adapter detects `exact_head_mismatch`; run locked | yes |
| `s06b-05-rejected-review` | **Rejected Review** | Independent reviewer returns `verdict: fail` | **`blocked`**; dispatch marked `rejected`; 0 retries attempted | yes |
| `s06b-06-gateway-restart` | **Gateway Kill & B03 Recovery** | Gateway hard-killed mid-run; B03 claim recovery | Gateway restarts cleanly; run resumes to `accepted` without double commit; B03 pending claim refuses re-execution (`LiveRecoveryRequired`) | yes |
| `s06b-07-resource-lock` | **Omitted Resource Lock** | Worktree omitted from task `resources` | **Refused at `initialize()`** via CLI; 0 runs created in DB | yes |

Every negative scenario halted cleanly and safely. The system never reached `accepted` on corrupt, unverified, drifted, or unauthorized repository state.

---

## Isolation Architecture

Following the pattern established in S06a (`docs/SCORE-S06A-LOCAL-CANARY-20260916.md`), the entire test run was executed under strict physical isolation:

1. **Git Isolation**:
   - Repository code: Isolated git worktree `C:\AI\baton\wt\score-repo-write-s06b` branched from `codex/score-v1-repo-write` at commit `645dcce`.
   - Target repository: A completely separate throwaway git repository and worktree created at `C:\AI\baton\wt\scratch-s06b\throwaway-repo` and `throwaway-wt` on initial commit `71b5e04d8292a43eb071dd3451d0f911ca5611bb`. No operations touched the BitCadence source repository or any production repository.
2. **Filesystem & State Isolation**:
   - `HOME` and `USERPROFILE` were redirected to `C:\AI\baton\wt\scratch-s06b\home`.
   - The LocalStore database (`store.db`), conductor state database (`score-runs.db`), and score artifact directory were fully isolated within the scratch directory. The ambient `~/.mco` directory was never read or written.
3. **Network Isolation**:
   - Dedicated Gateway running on port `18997` (`127.0.0.1:18997`). The live gateway (`18789`) was neither contacted nor affected.
   - `NTFY_TOPIC` and external notification services were disabled.
4. **Identity & Authentication**:
   - Generated dedicated throwaway Bearer tokens for `worker-1` and `reviewer-1`.
   - Seeded tokens directly into the isolated `agent_registry` table.
   - Ambient `MCO_AGENT_TOKEN` was unset in subprocess environments to prevent credential leakage.
5. **Timing Parameters**:
   - `MCO_LEASE_TTL_SECONDS=10` and `MCO_REAPER_SWEEP_SECONDS=2` were configured in the isolated gateway to allow deterministic lease expiration and reaping testing without long delays.

---

## Invariant Analysis & Detailed Scenarios

### Injection 1: Baseline Happy-Path (`s06b-01-baseline`)
- **Action**: Conductor launched `s06b-01-baseline`. `worker-1` leased `write-task-1`, modified `src/canary.txt`, and reported `{"ready": true}` without committing. Conductor staged allowed paths (`src/*`), verified HEAD (`71b5e04`), committed using the live Git adapter (`a232eb1...`), and dispatched `reviewer-1`. `reviewer-1` inspected git HEAD via `git cat-file`, validated file contents, and submitted `{"verdict": "pass"}`.
- **Outcome**: Conductor marked run `accepted`.
- **Evidence**: `docs/evidence/repo-write-proof/logs/01-baseline.json`.

### Injection 2: Worker Crash Mid-Task (`s06b-02-crash`)
- **Action**: `worker-1` leased `write-task-1` at lease epoch 1. Immediately after acquiring the lease, the worker process executed `os._exit(137)` (simulating `kill -9` / process crash).
- **Outcome**:
  - The dead worker attempt made **zero** git commits.
  - The gateway background reaper swept expired leases after the 10-second TTL, bumped the lease epoch from 1 to 2, and returned the job to `pending`.
  - A replacement worker leased the job at epoch 3, performed the file write, and completed successfully.
  - `reviewer-1` verified the commit and the run transitioned to `accepted`.
- **Evidence**: `docs/evidence/repo-write-proof/logs/02-crash.json`.

### Injection 3: Attempt to Target Denied Branch (`s06b-03-denied-branch`)
- **Action**: A score was generated with `commit.target_branch = "main"`. The run was started via the CLI (`mco score start`).
- **Outcome**:
  - `ScoreBridge.initialize()` invoked `verify_not_denied_branch("main")` and raised `LiveAdapterError("denied_target_branch:main")`.
  - The CLI aborted with exit code 1 (`Could not start the run: Denied target branch: denied_target_branch:main`).
  - **Zero runs** were created in `score-runs.db`, and **zero Git commands** were executed.
- **Evidence**: `docs/evidence/repo-write-proof/logs/03-denied-branch.txt`.

### Injection 4: Exact-HEAD Drift Detection (`s06b-04-exact-head-drift`)
- **Action**: `worker-1` modified `src/canary.txt`, and before signaling completion, injected an out-of-band commit directly into the worktree HEAD (`ad025a4...`), changing HEAD away from the expected `71b5e04d...`.
- **Outcome**:
  - Conductor polled the completed work job and called `LiveScoreAdapterExecutor.execute()`.
  - The live adapter checked `verify_exact_head(worktree_path, expected_before_sha)` and raised `LiveAdapterError("exact_head_mismatch:...")`.
  - Conductor recorded `event="validation_blocked"` with reason `exact_head_mismatch` and transitioned the run state directly to `blocked`.
  - The run remained permanently locked; no commit was made on the drifted HEAD.
- **Evidence**: `docs/evidence/repo-write-proof/logs/04-exact-head-drift.json`.

### Injection 5: Rejected Review (`s06b-05-rejected-review`)
- **Action**: Worker completed work and the live adapter committed `6fe8b79...`. Conductor dispatched review to `reviewer-1`. `reviewer-1` executed in `--mode fail` and returned `{"verdict": "fail", "findings": ["Canary reviewer deliberate rejection"]}`.
- **Outcome**:
  - Conductor marked the review dispatch as `rejected`.
  - Because canary tasks do not define `on_reject`, the conductor transitioned the run to `blocked`.
  - The harness polled for 5 seconds and verified that zero retry dispatches were scheduled and the run remained `blocked`.
- **Evidence**: `docs/evidence/repo-write-proof/logs/05-rejected-review.json`.

### Injection 6: Gateway Restart Mid-Commit & B03 Atomic Claim Recovery (`s06b-06-gateway-restart`)
- **Action**:
  1. `worker-1` completed work. Immediately before adapter execution finished, the Gateway process was hard-killed using `TerminateProcess`.
  2. The gateway was restarted on port `18997`. Conductor resumed polling, safely committed changes, dispatched review, and reached `accepted` without double commits.
  3. **B03 Atomic Claim Isolation**: To verify that crash recovery requires human intervention if an in-flight operation's effect is uncertain, the harness simulated an in-flight crash by inserting a pending claim into `score_recovery` matching the operation tuple `(run_id, task_id, adapter, action, resource)`. When `LiveScoreAdapterExecutor.execute()` was called, it raised `LiveRecoveryRequired("uncertain_effect_requires_inspection_or_compensation")`.
- **Outcome**: Proved that gateway restarts do not cause duplicate commits, and unconfirmed in-flight claims strictly block re-execution until resolved.
- **Evidence**: `docs/evidence/repo-write-proof/logs/06-gateway-restart.json`.

### Injection 7: Omitted Resource Lock (`s06b-07-resource-lock`)
- **Action**: A score was generated configuring a `repository:write` commit on `throwaway-wt`, but the worktree path was omitted from the task's `resources` list. The run was submitted through the CLI (`mco score start`).
- **Outcome**:
  - `ScoreBridge.initialize()` checked `wt_path in t["resources"]` and rejected the score with:
    `ScoreError("Task 'write-task-1' with repository:write must include worktree_path in resources")`.
  - CLI exited with code 1; **zero runs** were created in `score-runs.db`.
- **Evidence**: `docs/evidence/repo-write-proof/logs/07-resource-lock.txt`.

---

## Discovered Hazards and Fixes Applied

During the development and execution of the adversarial proof harness, three significant hazards were discovered and resolved:

### 1. Timing Hazard in Denied Branch Check
- **Observation**: Initially, branch denylist validation was performed only when constructing the live adapter execution payload during conductor polling. If an invalid branch was targeted, the run would be accepted into the database, dispatches could occur, and git inspection commands (`rev-parse`, `status`) would run against the target worktree before the error was caught.
- **Fix**: Added `verify_not_denied_branch(target_branch)` directly into `ScoreBridge.initialize()` in `src/mco/orchestrator/score_bridge.py`. Any attempt to start a score targeting `main`, `master`, `deploy/*`, etc., fails fast at CLI invocation time before any run record is written or any Git process is spawned.

### 2. Strict Opt-In Live Authority & Harness Process Wiring
- **Observation**: Commit `0d49e20` initially introduced a dynamic fallback method `ScoreBridge.get_live_executor()` that auto-constructed live adapters when a grant key was present in configuration. This violated the opt-in-only architectural guarantee established in REPO-WRITE-2, which requires `ScoreBridge` instances to remain read-only audit-only unless a live executor is explicitly threaded at construction.
- **Fix**: Removed `ScoreBridge.get_live_executor()` and any automatic fallback from `score_bridge.py`. Production code (`cli.py`, `score_sweep.py`) remains untouched with read-only authority by default. The isolated adversarial proof harness explicitly constructs `LiveScoreAdapterExecutor` and threads it into `open_bridge(..., live_executor=...)` via its own harness launcher (`launch_gateway.py`) and CLI entrypoint (`harness_cli.py`), completely preserving production opt-in invariants.

### 3. CLI Terminal Line Wrapping in Automated Assertions
- **Observation**: In real process execution, Click and Rich format error messages according to terminal width, inserting newline breaks (e.g. `Task 'write-task-1' with repository:write must \ninclude worktree_path in resources`). Direct substring searches in process output failed due to formatted line breaks.
- **Fix**: Normalized whitespace (`" ".join(combined_out.split())`) in CLI output assertions to ensure reliable matching across different console environments.

---

## Verification & Artifacts

All 318 tests across the 14 score test files (`tests/test_scores.py` and `tests/test_score_*.py`) passed (100% passing, 0 failures, 0 regressions).

All generated evidence logs and their cryptographic SHA-256 digests are recorded in `docs/evidence/repo-write-proof/00-run-inventory.txt`:

```text
01-baseline.json                 245267 bytes  sha256:68c6a08ba076cf03710e6bd40529d550c005e407b4a7d91122a57680868e2945
02-crash.json                    326050 bytes  sha256:a60fa9e58d6945227619504365bfe1b578a06f1f11962880d7e820b78804d578
03-denied-branch.txt                 80 bytes  sha256:7b6d5acef4dcc11b3648337d4bac2ddbe75120a5481b92b833304f12eb56abee
04-exact-head-drift.json          99561 bytes  sha256:34f874053f5a53753e59e81cdb8e984ba7c98ee55cf1019ac4c5fc12c49d9857
05-rejected-review.json          192755 bytes  sha256:f9d11466de674f6614990501a80388ba66d78a2bd4a68d8c2b89adcd9ac53af4
06-gateway-restart.json          209416 bytes  sha256:e0e3733759af1fddbae5c2d8f6657a0ccd1667dd612fdbe9aaacfd60345a5ea4
07-resource-lock.txt                116 bytes  sha256:64b7b337ed586bac1ef241f0b1ce5bcf020ea346ac68593aac7c83730819320f
```
