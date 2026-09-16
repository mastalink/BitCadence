# S06a: the canary survives being broken — 2026-09-16

## What this proves

C04 showed a Score run reaching `accepted` with nobody watching. It ran on a
healthy board, with healthy workers, and nothing went wrong. This packet breaks
the run on purpose, five ways, and checks that what happens next is the
**governed** outcome rather than merely a surviving one — that the run stops
where it should stop, resumes where it should resume, and above all never
reaches `accepted` on evidence it should have refused.

Seven runs of the C01 canary (`examples/scores/via-score-conductor-canary.score.json`)
on an isolated local board, all at digest `5974bd7a18e2`:

| run | injection | outcome | governed? |
|---|---|---|---|
| `s06a-01-baseline` | none | `accepted` in 45s | control |
| `s06a-02-crash` | worker killed holding the lease | `accepted` after recovery | yes |
| `s06a-03-duplicate` | conductor re-delivers a created job | `accepted` once | yes |
| `s06a-04-dupcomplete` | one attempt reports three times | `accepted` once | yes |
| `s06a-05-reject` | reviewer returns `verdict: fail` | **`blocked`** | yes |
| `s06a-06-tamper` | evidence bytes edited after validation | **`blocked`** | yes |
| `s06a-07-restart` | gateway `SIGKILL`ed mid-run | `accepted` after restart | yes |

The two `blocked` rows are the point of the packet. A canary that only ever
goes green proves the happy path twice; these two prove the conductor can say
no, and stay stopped.

## Isolation

Nothing here touched the live board, the live conductor database, AWS, a deploy
runner, `via-cloud.score.json`, `SCORE_DIGEST` or GitHub Actions, and nothing
was merged to `main` (`origin/main` is `b8978176`, untouched).

- Fresh worktree branched from the integration tip `b121314a` (see
  *Base* below). No product code was modified: `git status --porcelain` is
  empty for the whole run, and this PR adds only documentation and the harness.
- A throwaway `HOME`/`USERPROFILE`, so the LocalStore, conductor database,
  artifact root, tokens and leases are all separate from `~/.mco`. The real
  `~/.mco/score-runs.db` was last written at 00:44Z, hours before this packet.
- Its own gateway on `127.0.0.1:18997`. The live gateway (18789) and C04's
  (18999) were neither contacted nor reconfigured. Every HTTP request in every
  harness log resolves to `127.0.0.1:18997` — 296 of them, no other host.
- `NTFY_TOPIC` empty, so no push left the machine. `MCO_DELIVERY_STALL_SECONDS=0`,
  so the delivery watchdog could not reroute a canary job and be mistaken for
  the conductor sweep.
- Purpose-registered canary identities (`canary-worker-s06a`,
  `canary-review-s06a`) running the fixed non-LLM handlers in
  `score_canary_worker.py`. No LLM was dispatched.
- `MCO_LEASE_TTL_SECONDS=20` instead of the 900s default, so a crashed lease is
  reaped inside C01's 300s task timeout. This is a configuration knob, not a
  code change, and it is the only tuning applied.

### One isolation hazard, found and closed

The first attempt at this harness inherited `MCO_AGENT_TOKEN` from the ambient
shell — the live board credential for `claude-beast`. Because `MCO_GATEWAY_URL`
already pointed at 18997, it never reached the live board, but a conductor
authenticated with a live credential is not an isolated conductor. The harness
now sets a throwaway token explicitly and the committed `env.sh` refuses to run
without one. Worth stating plainly: an isolated port is not isolation on its
own, and the credential is the half that is easy to forget.

## Injection 1 — a worker crashes holding the lease

`crash_worker.py` leases the C01 work job, writes down the claim it is holding,
and calls `os._exit(137)`: no unwinding, no completion, no tidying up. It is
what a `SIGKILL`ed worker leaves behind.

```
15:14:14.653  board      created            local-operator/admin
15:14:15.889  board      leased             canary-worker-s06a       <- pid 40160
              (pid 40160 dies here, holding lease ce814aa1, epoch 1)
15:14:39.041  board      lease_expired      system/reaper   {"lease_ttl_seconds": 20,
                                                             "leased_by": "canary-worker-s06a"}
15:15:02.195  board      write_fenced       canary-worker-s06a
                                            {"reason": "illegal transition pending -> completed"}
15:15:13.013  board      leased             canary-worker-s06a       <- replacement, epoch 3
15:15:13.577  board      status:completed   canary-worker-s06a
15:15:33.709  conductor  accepted           canary-review-s06a
15:15:33.713  conductor  score_accepted
```

Four governed outcomes, not one:

1. **The dead attempt produced nothing.** No completion, no evidence, and the
   conductor recorded no `validated` while the lease was stranded.
2. **The reaper named it.** `lease_expired` is an authenticated board event
   carrying the TTL and the instance that died — the crash is in the audit
   trail, not inferred from a gap.
3. **The dead attempt can never come back.** Replaying its completion with the
   exact lease it held at crash time is refused:

   ```
   PUT /api/jobs/9e565111... with lease ce814aa1 (epoch 1, board now at epoch 2)
   -> HTTP 409  {"detail":"FENCED: illegal transition pending -> completed"}
   ```

   and the refusal is itself recorded on the job as `write_fenced`. A worker
   that comes back from the dead cannot overwrite its replacement.
4. **The same job recovered, not a new one.** Job id `9e565111` throughout; the
   conductor never re-planned or re-dispatched. The run reached `accepted` 79
   seconds after the crash with exactly one artifact.

Note what did *not* happen: no other identity could take the work. The job
carries `target_agent_id`, so the reclaimed job was re-offered only to
`canary-worker-s06a`. A crash does not widen who may do the work.

## Injection 2 — duplicate delivery, at both layers

Duplicate delivery has two independent failure modes, and they are defended in
two different places, so both were injected.

### The conductor re-creates a job it already created

A conductor that dies between `board.create(...)` and the row recording
`submitted` restarts with the dispatch still marked `sending` and creates the
job again. `dup_dispatch.py` rewinds exactly that window:

```
job 1852e1a7-9181-54e6-be29-dd74d7f62176
  status in conductor db : submitted
  board 'created' events : 1
  -> rewound dispatch row to 'sending' (the conductor-crash window)
  -> re-dispatch returned: ['1852e1a7-9181-54e6-be29-dd74d7f62176']
  board 'created' events after re-dispatch : 1
  board jobs with this id                  : 1
  board jobs titled 'Score s06a-03-duplicate C01 work' : 1
  job status / leased_by                   : completed / canary-worker-s06a
```

The job id is derived from `(org, run, digest, task, phase)`, so the retry is
the *same* job, and `create_with_id` returns the existing row rather than
minting a second one. The re-created job was already `completed` at the time of
the duplicate, and the duplicate did not reset it. The run still recorded one
`validated`, one `accepted`, and reached `accepted`.

### One attempt reports three times

`dup_complete.py` leases the work job and sends three PUTs under the **same
lease**: the honest result, a byte-identical retry (a lost response), and then
a different result.

```
DELIVERY 1 - the honest completion                              HTTP 200
DELIVERY 2 - byte-identical retry after a lost response         HTTP 200
DELIVERY 3 - a DIFFERENT result under the SAME lease            HTTP 409
             {"detail": "FENCED: attempt already reported a different result"}
```

Afterwards the job carries **one** `status:completed` event and one
`write_fenced`, and the conductor recorded one `validated` and one `accepted`.
The retry is absorbed; the divergence is refused. That is the correct pair — a
worker that never learns whether its result landed must be able to ask again,
and must not be able to change its mind.

## Injection 3 — a review that says no

Two different things can go wrong at review, and they block for two different
reasons. Both were injected.

### The reviewer rejects valid evidence

`reject_reviewer.py` returns a well-formed verdict of `fail`, bound to the
exact evidence map — not a malformed review, not a tampered artifact, just a
reviewer that looked and disagreed.

```
15:18:15.483  validated           actor=canary-worker-s06a
15:18:21.043  submitted           C01 review
15:18:27.198  rejected            actor=canary-review-s06a
              findings: ["Artifact bytes and digest verify, but the run is
                         rejected on review: injected S06a failure event."]
run status: blocked        dispatch: review=rejected, work=validated
```

No `score_accepted` was ever written. `max_attempts` is 1, so there is no
retry: the run stops. And it *stays* stopped — twelve seconds and two further
gateway sweeps later the event list was unchanged at seven events. A blocked
run is excluded from the sweep's candidate scan, so nothing resurrects it.
`/readyz` named it: `checks.score_sweep.failing_runs = ["s06a-05-reject"]`.

### The evidence changes under the reviewer

With the reviewer stopped, the validated artifact was edited on disk —
`"status":"canary_ok"` to `"status":"canary_tampered"`, one field:

```
sha256 before: 4746590370c650a81df988a7bf1f6ca0266952c0c4cd265cc9e75be82f9a0fb8
sha256 after : 24e6cf16936e6b19a3ae6f9ed599c80e3d1cd2eeb93d7d42bc931b49c36e6bf0
```

The honest reviewer was then started. The run blocked before its verdict was
ever consulted:

```
15:19:52.894  validation_blocked  {"reason": "Evidence digest mismatch"}
run status: blocked        dispatch: review=submitted (never completed), work=validated
```

The bridge re-hashes the referenced bytes itself when it polls the review, so
evidence that moved between validation and review stops the run at the
conductor, not at the reviewer's discretion. The reviewer's opinion is not the
only thing standing between tampered evidence and an accepted run.

## Injection 4 — one human gate

The canary is zero-budget and carries no checkpoint, and that is the owner
policy working rather than a gap: `required_gates("C01", 0) -> ()`. The owner
is not interrupted for canary work. So the gate was injected the way the policy
says a gate actually arises — a task projecting spend above the owner's cap —
against the real canary score, the real digest, and the same LocalStore the
gateway was serving from.

```
owner policy : via-owner-2026-09-15, cap 15000 cents/month
projected    : 15001  ->  required_gates("C01", 15001) -> ('spend_above_cap',)
digest       : 5974bd7a18e2f0f9933e19a5524d3937753ecacc28fa9c06bdc045aea89617fe
```

**The gate holds the path.** With the gate pending,
`blockers("C01") = ['human_checkpoint']` and `ready() = []`. The task is not
dispatchable.

**The gate is visible and bound.** `GET /api/score/gates` returns it with its
kind, task, status, evidence, and the run digest it is bound to — so a decision
cannot be carried to a different revision of the score.

**An agent cannot decide it.** Posting a decision with an agent bearer token,
including the operator's own admin token:

```
POST /api/score/gates/{id}/decision  ->  HTTP 403
{"detail":"Authenticated human principal required; agent bearer tokens cannot decide Score gates"}
gate status afterwards: pending
```

The model refuses it too, not just the API edge: `approve(..., actor_kind="agent")`
raises `ScoreError: Explicit human principal required`. Two independent refusals.

**A human decision releases it, once.** A caller authenticated as a person
(`auth_method: session`) produces an immutable authorization record:

```
gate_id          88715fff-0584-5c3b-8ab4-a99c8949e524
run_id           s06a-09-gate
digest           5974bd7a18e2f0f9933e19a5524d3937753ecacc28fa9c06bdc045aea89617fe
task_id          C01
decision         approved
human_principal  joseph.arroyo
decided_at       2026-09-16T15:23:12.485859+00:00
```

after which `blockers("C01") = []` and `ready() = ['C01']`. A second decision on
the same gate is refused with `ScoreError: Gate already decided`. And the grant
set is unchanged — `['evidence:review', 'evidence:write']` before and after — so
a checkpoint approval releases a path without widening authority.

**What the conductor that actually runs the canary does with a gate.** The
SQLite bridge does not pause at a checkpoint. It refuses the score outright:

```
the gated variant differs from the canary in exactly one field:
  tasks[0].checkpoint = {"id": "g08_launch_signoff", "reason": "owner sign-off before launch"}
REFUSED at initialize: ScoreError: Only read-only audit authority supported
  runs      rows for s06a-10-gated: 0
  dispatch  rows for s06a-10-gated: 0
  events    rows for s06a-10-gated: 0
```

No run row, no dispatch row, no event, no board job. That is fail-closed, and
it is the honest state of the wiring today: the gate model in `scores.py` and
the durable gate service in `score_policy.py` both work, and the SQLite bridge
that drives real canary runs is not yet connected to either. It refuses gated
work rather than running it ungated, which is the right way to be incomplete —
but it is incomplete, and the cloud canary will need the wiring. See follow-ups.

**There is no human auth path on this profile at all.** Checked, not assumed:

```
MCO_TRUSTED_HEADER_AUTH  = None
edition has trusted_header_auth = False   (enterprise feature)
OIDC session configured  = False
```

The human decision above was made through the service with a session-shaped
principal, because on a Local-Only install no human *can* reach
`/api/score/gates/{id}/decision` — agent tokens are refused by design and
neither human path is available. This is a fact the cloud canary has to plan
for, not a defect in the gate.

## Injection 5 — the gateway is killed mid-run

The gateway was `Stop-Process -Force`d 41 milliseconds after the work job was
submitted — no shutdown hook, no chance to finish a sweep.

```
15:20:23.842  work job submitted
15:20:23.883  HARD KILL of gateway pid 45592
15:20:26       /readyz -> unreachable
              conductor state: ['initialized','planned','submitted'], run=running
15:20:51      after 25s down: ['initialized','planned','submitted']   (unchanged)
15:21:04.682  gateway back up (pid 80904)
15:21:10.129  validated
15:21:15.716  submitted   C01 review
15:21:27.381  accepted
15:21:27.385  score_accepted
```

Through a 39-second outage the run did not drift: three events before, three
events after, run still `running`, nothing half-written. On restart the
conductor resumed from the database and settled the run 23 seconds later. Both
jobs show `created=1`, `completed=1`, `lease_epoch=1` — the restart created no
duplicate job, re-dispatched nothing, and cost no lease churn. The durable
state is the run; the process is not.

## Evidence trail

Raw evidence is in `docs/evidence/s06a/` alongside the harness that produced
it. Every artifact the canary wrote, hashed at rest:

```
score-runs/s06a-01-baseline/canary_artifact.json     0054e438e22500de803d4d62f3b3cfae67c6c732664904d112cb65e3c8bfda1d
score-runs/s06a-02-crash/canary_artifact.json        90858d046f15708cdffc6ad02aeed1f3c90b3f916fda434a5f08a235f82c717d
score-runs/s06a-03-duplicate/canary_artifact.json    77415402d323c0ef91f51972fc9f4bc0687b71f7700e37a9c8232d3fef8881fd
score-runs/s06a-04-dupcomplete/canary_artifact.json  1d9ebca089895b5c4e3542836bff01536c133c8da34409ec1de1db5add30672e
score-runs/s06a-05-reject/canary_artifact.json       60ca524acc6bacb83d884a7d1cb059ce381535f7a3bb82100dfb8d3cca9a8fb4
score-runs/s06a-06-tamper/canary_artifact.json       24e6cf16936e6b19a3ae6f9ed599c80e3d1cd2eeb93d7d42bc931b49c36e6bf0
score-runs/s06a-07-restart/canary_artifact.json      507ab164a82157c1b588da7586cec6cb59bb78a467f606c206611cc237846fdd
```

`s06a-06-tamper`'s artifact hashes to the tampered value, which is the point:
the file on disk is the edited one, and that is why the run is blocked.

To reproduce: set `S6` to a scratch directory and `WT` to a worktree at this
commit, generate a throwaway `MCO_LOCAL_TOKEN`, then source `env.sh`, `gw.sh`,
`workers.sh` and `watch.sh` and run the injection scripts in the order above.

## A configuration trap worth fixing

`mco score start` resolves `--db` and `--artifact-root` from `Path.home()` and
never reads `MCO_SCORE_DB` / `MCO_SCORE_ARTIFACT_ROOT`, which is where the
gateway sweep reads them (`score_sweep.get_database` / `get_artifact_root`).
Configure the gateway by environment — the documented way — and the documented
CLI invocation starts runs bound to a different artifact root, which the sweep
then refuses forever:

```
one sweep pass -> ticked=[] advanced=[] blocked={}
                 errors={'s06a-11-rootmismatch': 'ScoreError: Run artifact root mismatch'}
run status after four more gateway sweeps: running, events: ['initialized']
```

This is not hypothetical: it is what happened to this packet's first baseline
attempt and it cost a full setup cycle.

Readiness does surface it — `SweepResult.failing` carries per-run tick errors as
well as blocked runs, so `/readyz` named `s06a-11-rootmismatch` in
`checks.score_sweep.failing_runs` while the run's own status was still
`running`, and the gateway correctly stayed `ok: true` because the sweep itself
is healthy. So this is a usability trap, not a blind spot. The fix is to make
the CLI read the same two environment variables the sweep reads, so there is
one answer to "where does this run's state live" instead of two.

## Follow-ups

1. **Wire the gate into the conductor.** The SQLite bridge refuses checkpointed
   scores instead of pausing at them. The gate model (`scores.SandboxRun`) and
   the durable gate service (`score_policy.GateService`) both work and are both
   unreachable from a real run. The cloud canary cannot ask the owner for G08
   sign-off until this is connected.
2. **Give a human a way in.** On a Local-Only install no human principal can
   decide a gate over HTTP: agent tokens are refused by design, trusted-header
   auth is an enterprise feature and OIDC is unconfigured. Whatever hosts the
   cloud canary needs one working human auth path before a gate can matter.
3. **Make the CLI and the sweep read one config.** See above.
4. **Make the effect-scope claim atomic before any adapter goes live.** Carried
   forward unchanged from the S05 merge: the cross-attempt pending-recovery
   guard in `score_adapters.py` is a scan-then-claim, so two simultaneously
   live attempts on the same effect scope can both invoke. Nothing in S06a
   exercises an adapter — `ScoreAdapterExecutor` is still called from no
   conductor — so this packet neither tests nor relies on that guard.

## What this does not authorize

Still the canary lane: fixed non-LLM handlers, a zero-budget score, read-only
audit authority, a local gateway, and a throwaway store. It now says that the
conductor stops correctly when work is rejected, tampered with, or gated, and
resumes correctly when a worker or the gateway dies. It says nothing about
production launch authority, a cloud-hosted conductor, LLM workers, adapters
with real effects, or any score with a budget or a deploy capability.
`MCO_SCORE_SWEEP_SECONDS` remains `0` by default.

## Base and tests

**Base.** The packet named `22b12602` (the S05 merge) as the base. That SHA is
stale: C03 (`0f84caa5`, PR #82) and C04 (`b121314a`, PR #83) have since landed
on `codex/score-v1`, and `22b12602` is their ancestor. This branch is cut from
the integration tip `b121314a2f7d201ab98db62fa8c90483a05abc99`, which is also
the only base on which this packet makes sense — the gateway sweep it exercises
is C03's, and the unattended run it stresses is C04's.

**Tests.** `PYTHONPATH="<worktree>\src;...\.codex\desktopdeps" python -m pytest -q
--ignore=tests/test_install_sh_tty.py` on this branch:

**1216 collected, 1203 passed, 13 skipped, exit 0.**
`tests/test_install_sh_tty.py::test_pipe_no_prompt_survives` is the declared
Windows-only exclusion.

Two deviations to name explicitly, neither caused by this packet — it changes
no product code.

1. **The count differs from the quoted baseline** of 1169 passed / 13 skipped /
   1182 collected. That baseline was taken at the S05 merge; C03 (#82) and C04
   (#83) have since added 34 tests. Skips are unchanged at 13, all of them
   PostgreSQL/PostgREST acceptance tests wanting `BC_TEST_POSTGREST_URL`.
2. **One intermittent failure, seen once.** The suite was run three times end to
   end: runs 1 and 3 exited 0; run 2 failed
   `tests/test_agentd_supervisor.py::test_worker_that_keeps_failing_latches_again_after_cooldown`
   and exited 1. That test then passed 5/5 run alone and 3/3 with its whole
   file. It is an `agentd` supervisor test driven by a `FakeClock` and a
   `tmp_path` latch, unrelated to Score, and this branch is the base commit plus
   documentation — so the flake exists on `codex/score-v1` itself and is not
   introduced here. Recorded rather than retried away: a test that fails 1 in 3
   full-suite runs and never in isolation is worth someone's attention, and the
   run-2 traceback was lost to a `tail` that kept only the summary, so a
   reviewer chasing it will need to reproduce it rather than read it here.
