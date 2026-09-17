# Score Repository:Write Adversarial Proof Harness (S06b)

This directory contains the complete adversarial verification suite for the `repository:write` capability under the BitCadence Conductor (`score-v1` protocol), fulfilling the requirements of REPO-WRITE-3.

## Overview

The harness executes 7 governed injection scenarios against an isolated Gateway, Conductor, LocalStore, and a dedicated throwaway git repository/worktree.

| # | Scenario | Tested Invariant | Governed Outcome |
|---|---|---|---|
| 1 | **Baseline Canary** | Happy-path execution with live adapter git commit | `accepted` in ~40s, real commit created and verified |
| 2 | **Worker Crash Mid-Task** | Process hard kill (`SIGKILL` / exit 137) holding task lease | Dead attempt creates 0 git state; lease reaped after TTL; replacement worker completes; run `accepted` |
| 3 | **Denied Branch Target** | Attempt to target protected branch (`main`) | Hard failure at `initialize()`; 0 runs created; 0 git commands run |
| 4 | **Exact-Head Drift** | Out-of-band commit on worktree HEAD before worker completion | Live adapter detects `exact_head_mismatch`; run permanently transitions to `blocked` |
| 5 | **Rejected Review** | Independent reviewer verdict `fail` | Conductor marks dispatch `rejected`; run permanently transitions to `blocked`; no retry |
| 6 | **Gateway Restart Mid-Commit** | Gateway hard-killed during task lifecycle & B03 atomic claim recovery | Gateway restarts cleanly; run resumes to `accepted` without double commit; B03 pending claim refuses re-execution (`LiveRecoveryRequired`) |
| 7 | **Omitted Resource Lock** | Task targets worktree but omits worktree path from `resources` | Refused at `initialize()` via real CLI path; 0 runs created |

## Structure

- `env.ps1` / `env.sh`: Environment definitions (port 18997, isolated `$HOME`, 10s lease TTL, 2s sweep).
- `launch_gateway.py`: Isolated Gateway launcher using stdlib logging.
- `grant_and_score.py`: Score definition generator and cryptographic grant issuer via `GrantService`.
- `repo_write_worker.py`: Non-LLM deterministic worker process implementing normal, crash, and drift modes.
- `repo_write_reviewer.py`: Non-LLM deterministic reviewer process implementing pass and fail modes with `git cat-file` validation.
- `observe.py`: Read-only multi-layer evidence dumper (Conductor DB, Board API, LocalStore, Git log).
- `run_proof.py`: Master automated runner executing all 7 injections with clean resets and assertions.
- `logs/`: Directory storing captured evidence JSON dumps and execution logs (`01-baseline.json` through `07-resource-lock.txt`).
- `00-run-inventory.txt`: SHA-256 digests of all generated evidence artifacts.

## Running the Suite

### Prerequisites
- Python 3.11+
- Git 2.40+
- Activated environment with BitCadence dependencies

### Execution
From the root of the repository worktree:

```powershell
python docs/evidence/repo-write-proof/run_proof.py
```

All 7 scenarios run autonomously end-to-end, resetting test state between injections and generating cryptographic evidence records in `logs/` and `00-run-inventory.txt`.
