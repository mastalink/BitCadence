# S06a evidence and injection harness

Raw evidence for `docs/SCORE-S06A-LOCAL-CANARY-20260916.md`, plus the scripts
that produced it. Nothing here is imported by product code.

## Evidence

| file | what it shows |
|---|---|
| `00-run-inventory.txt` | every run, its final status, and the sha256 of every artifact written |
| `01-baseline.json` | the clean control run: `accepted` in 45s |
| `02-dead-claim.json` | the lease the crashing worker was holding when it died |
| `02-zombie-refused.txt` | that dead attempt replaying its completion — HTTP 409, fenced |
| `02-crash.json` | the crash run recovering to `accepted`, with `lease_expired` and `write_fenced` on the job |
| `03-duplicate-dispatch.txt` | the conductor re-creating a job it already created — one board job, one `created` event |
| `03-duplicate.json` | that run still settling at `accepted` exactly once |
| `04-duplicate-completion.txt` | one attempt reporting three times: retry absorbed, divergence fenced |
| `04-dupcomplete.json` | one `status:completed`, one `validated`, one `accepted` |
| `05-reject.json` | a `fail` verdict → `rejected` dispatch → **`blocked`** run, never accepted |
| `06-tamper.json` | evidence edited after validation → `validation_blocked: Evidence digest mismatch` |
| `07-restart.json` | gateway hard-killed mid-run; resumed to `accepted` with no duplicate jobs |
| `08-gate.txt` | the full human-gate lifecycle: held, listed, agent refused, human decides, decided once |
| `09-gate-failclosed.txt` | the SQLite conductor refusing a checkpointed score outright |
| `10-finding-silent-stall.txt` | the CLI/sweep config trap, and what readiness actually reports |

`0?-*.json` files are produced by `observe.py`, which opens the conductor
database read-only (`mode=ro`, `SELECT` only) and reads board jobs over the
authenticated API, so observing a run can never advance it.

## Reproducing

```sh
export S6=/path/to/a/throwaway/scratch/dir      # its own HOME, store, artifacts
export WT=/path/to/a/worktree/at/this/commit
export MCO_LOCAL_TOKEN=$(openssl rand -hex 16)  # throwaway; never a live board token
mkdir -p "$S6/home/.mco" "$S6/logs" && : > "$S6/home/.mco/.env"
source "$WT/docs/evidence/s06a/env.sh"
source "$WT/docs/evidence/s06a/gw.sh"
source "$WT/docs/evidence/s06a/workers.sh"
source "$WT/docs/evidence/s06a/watch.sh"

cd "$WT"
"$PYBIN" -m mco.cli register --name canary-worker-s06a --role score-canary-worker
"$PYBIN" -m mco.cli register --name canary-review-s06a --role score-canary-review
# save the printed tokens to $S6/home/.mco/tok-worker and $S6/home/.mco/tok-review
start_gw boot
start_worker score-canary-worker canary-worker-s06a tok-worker wk
start_worker score-canary-review canary-review-s06a tok-review rv
```

Then start a run with `mco score start examples/scores/via-score-conductor-canary.score.json
--run-id <id> --target score-canary-worker=canary-worker-s06a --target
score-canary-review=canary-review-s06a`, `watch_run <id>`, and drive each
injection with its script. The report gives the order and the expected outcome
for each one.

`gw.sh` captures the uvicorn worker pid out of the log rather than the shell's
job pid: on Windows killing the launcher leaves the server holding the port,
which costs a confusing debugging cycle if you let it happen.
