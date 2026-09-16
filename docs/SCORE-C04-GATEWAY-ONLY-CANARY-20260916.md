# C04: a Score run reaches accepted with nobody watching — 2026-09-16

## What this proves

Two canary runs of `via-score-conductor-canary` went from `initialized` to
`accepted` without anyone typing `mco score tick`. The only command issued
against either run was `mco score start`, which writes one row to the conductor
database and exits without contacting the board at all. Every later step —
plan, dispatch, poll, validate, accept — was performed by the conductor sweep
inside the gateway process wired up in C03 (`0f84caa5`).

C02 got a canary to `accepted` in ~50 seconds, but a person drove it: the run
only moved while a terminal was ticking it. That is the claim C04 retires.

- Run A (`c04-gateway-only-20260916`): started 09:56:05Z, accepted 09:56:31Z —
  **26 seconds, three gateway sweep ticks, zero typed ticks.**
- Run B (`c04-control-sweep-off-20260916`): the A/B control below. Sat at
  `initialized` for 115 seconds with the gateway up and both workers polling,
  and only moved once the sweep was switched on.

## Isolation

Nothing in this packet touched the live board, the live conductor database, AWS,
a deploy runner, `via-cloud.score.json`, `SCORE_DIGEST` or GitHub Actions, and
nothing was merged to `main`.

- Fresh worktree at the C03 merge commit `0f84caa5cf02e8fa492fbaaac10fec68ad476247`,
  working tree unmodified for the whole run (`git status --porcelain` empty).
- A throwaway `HOME`/`USERPROFILE` pointing at a scratch directory, so the
  embedded LocalStore (`local.db`), the conductor database (`score-runs.db`),
  the artifact root, tokens and leases were all created fresh and separate from
  `~/.mco`. `MCO_ENV_FILE` pointed at the scratch `.env`.
- Its own gateway on `127.0.0.1:18999` (the live gateway runs on 18789 and was
  neither stopped nor reconfigured). `NTFY_TOPIC` empty, so no push left the
  machine; `MCO_DELIVERY_STALL_SECONDS=0`, so the delivery watchdog could not
  reroute a canary job and be mistaken for the sweep.
- Two purpose-registered canary identities, `canary-worker-c04` and
  `canary-review-c04`, executing the fixed non-LLM handlers in
  `score_canary_worker.py`. No LLM was dispatched.

The gateway was started through a five-line launcher that calls
`logging.basicConfig(...)` before `mco serve`. That is the whole difference from
a stock start: `mco serve` configures loguru only, so the stdlib loggers the
sweep writes to have no handler and its INFO lines are invisible. No product
code was modified.

## Run A timeline

Conductor database event times (`events.at`, UTC, written by the conductor
itself) against the gateway's own sweep log (pid 71904):

```
09:55:08.244Z  gateway   health INFO Conductor sweep enabled: advancing score runs every 5s
09:56:05.499Z  events    initialized                    <- `mco score start` (the only typed command)
09:56:05.682Z  shell     start command returned; no mco process alive from here on
09:56:08.342Z  events    planned    C01 work
09:56:08.834Z  gateway   httpx INFO HTTP Request: POST http://127.0.0.1:18999/api/jobs "200 OK"
09:56:08.835Z  events    submitted  1818e270-80df-58b3-88d3-1ec0dcbfd226
09:56:09.064Z  gateway   score_sweep INFO Conductor sweep advanced run=c04-gateway-only-20260916 status=running planned=1 dispatched=1
09:56:14.792Z  events    validated  actor=canary-worker-c04
09:56:19.815Z  events    planned    C01 review
09:56:20.295Z  events    submitted  f73ea2dc-27e4-552a-8aba-6176e1148c7c
09:56:20.533Z  gateway   score_sweep INFO Conductor sweep advanced run=c04-gateway-only-20260916 status=running planned=1 dispatched=1
09:56:31.730Z  events    accepted   actor=canary-review-c04
09:56:31.734Z  events    score_accepted digest 5974bd7a18e2f0f9933e19a5524d3937753ecacc28fa9c06bdc045aea89617fe
09:56:31.738Z  gateway   score_sweep INFO Conductor sweep advanced run=c04-gateway-only-20260916 status=accepted accepted=1
```

Four timestamps carry the argument:

1. **09:56:05.682Z** — the `mco score start` process had already exited. Every
   conductor write after this instant was made by some other process.
2. **09:56:08.342Z** — the first `planned` row, 2.7 seconds after that exit.
3. **09:56:08.834Z** — the `POST /api/jobs` that created the work job is logged
   **by `pid=71904`, the gateway's own process**. The conductor's HTTP client
   was running inside the gateway, not in a terminal.
4. **09:56:31.738Z** — the sweep reports the run terminal at `accepted`, four
   milliseconds after the conductor stamped `score_accepted`.

The run was observed by a separate read-only process that opened the SQLite file
with `mode=ro` and only ever `SELECT`ed, so the timeline could not itself have
advanced anything.

## Run B: the A/B control

Run A alone leaves one loose end — perhaps `mco score start` does more than it
claims, or the delivery watchdog moves work on its own. Run B closes it by
changing exactly one variable.

```
09:58:55.190Z  gateway restarted with MCO_SCORE_SWEEP_SECONDS=0
               /readyz -> checks.score_sweep = {"ok": true, "configured": false}
09:59:05.820Z  events    initialized                    <- `mco score start` for run B
   ... 115 seconds. Gateway up, 14 worker /api/jobs/pending polls served,
       both canary identities online, and the run's only event is `initialized`.
10:00:55.280Z  gateway stopped
10:01:00.203Z  gateway restarted, MCO_SCORE_SWEEP_SECONDS=5, nothing else changed
10:01:01.147Z  gateway   health INFO Conductor sweep enabled: advancing score runs every 5s
10:01:01.156Z  events    planned    C01 work            <- 9ms after the sweep starts
10:01:01.918Z  gateway   score_sweep INFO Conductor sweep advanced run=c04-control-sweep-off-20260916 ...
10:01:18.592Z  events    validated  actor=canary-worker-c04
10:01:23.627Z  events    planned    C01 review
10:01:41.057Z  events    accepted   actor=canary-review-c04
10:01:41.060Z  events    score_accepted
10:01:41.064Z  gateway   score_sweep INFO Conductor sweep advanced run=c04-control-sweep-off-20260916 status=accepted accepted=1
```

A started run with a healthy board and idle workers stays exactly where it is
until the sweep exists; it reaches `accepted` 40 seconds after the sweep is
enabled. The sweep is the cause, not a bystander.

## Board evidence

Both of run A's jobs were created by the conductor credential (`local-operator`)
and completed by the two distinct canary identities, which is what
`ScoreBridge._poll` demands before it will accept anything:

```
1818e270-80df-58b3-88d3-1ec0dcbfd226  Score c04-gateway-only-20260916 C01 work
  created   09:56:08.833Z  actor=local-operator/admin
  leased    09:56:09.706Z  actor=canary-worker-c04/score-canary-worker
  completed 09:56:10.181Z  actor=canary-worker-c04/score-canary-worker

f73ea2dc-27e4-552a-8aba-6176e1148c7c  Score c04-gateway-only-20260916 C01 review
  created   09:56:20.292Z  actor=local-operator/admin
  leased    09:56:28.661Z  actor=canary-review-c04/score-canary-review
  completed 09:56:29.134Z  actor=canary-review-c04/score-canary-review
```

Final state — `mco score status --run-id c04-gateway-only-20260916`:

```
via-score-conductor-canary run c04-gateway-only-20260916 digest 5974bd7a18e2 - status accepted
accepted 1/1 - launch requires C01 - launched: yes
C01 review  accepted   f73ea2dc
C01 work    validated  1818e270
```

Evidence artifact, written by the worker and re-hashed by the reviewer:

```
score-runs/c04-gateway-only-20260916/canary_artifact.json
sha256 ffaaf2fedb4aa6d747022b3c9a6e942715dd552b05ea488471731ae98e9ac3d9
{"magic":"canary-c01-deterministic","phase":"canary_artifact","run_id":"c04-gateway-only-20260916",
 "score_id":"via-score-conductor-canary","status":"canary_ok","task_id":"C01"}
```

`/readyz` reported `checks.score_sweep = {"ok": true, "error": null,
"interval_seconds": 5, "failing_runs": []}` throughout run A: the sweep drove two
runs to acceptance without ever making the gateway unready, and named no failing
run.

## Every command issued against the isolated environment

There is no `mco score tick` in this list, and there was none in the run.

```
mco register --name canary-worker-c04 --role score-canary-worker
mco register --name canary-review-c04 --role score-canary-review
mco serve --host 127.0.0.1 --port 18999                      (via the logging launcher)
mco score start examples/scores/via-score-conductor-canary.score.json \
    --run-id c04-gateway-only-20260916 \
    --target score-canary-worker=canary-worker-c04 \
    --target score-canary-review=canary-review-c04
mco score start ... --run-id c04-control-sweep-off-20260916   (the control run)
mco score status --run-id c04-gateway-only-20260916           (read-only)
python -m mco.orchestrator.score_canary_worker --role score-canary-worker ...
python -m mco.orchestrator.score_canary_worker --role score-canary-review ...
```

A process listing taken while run A was still settling showed only the gateway,
the two canary workers and the read-only observer — no CLI conductor process
existed at any point after `score start` returned.

## Tests

`pytest tests/test_score_sweep.py tests/test_score_conductor.py
tests/test_score_canary.py` — **61 passed, 0 failed, 0 errors** (2.9s), against
the unmodified worktree.

## What this does not authorize

This is the canary lane: a fixed non-LLM handler pair, a zero-budget score, a
read-only audit scope, and a local gateway. It says the sweep advances a run
unattended and settles it correctly. It says nothing about production launch
authority, a cloud-hosted conductor, LLM workers, or any score with a budget,
checkpoint or deploy capability. `MCO_SCORE_SWEEP_SECONDS` remains `0` by
default, and turning it on in a real deployment is a separate decision with its
own review.
