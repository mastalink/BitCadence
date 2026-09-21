# Score execution repair, 2026-09-20

## Authority and destination

The approved Option C design remains authoritative: Score owns dependency
planning, dispatch, independent review and acceptance. VIA is the cloud-only
proof of the product. Desktop workers are optional development capacity, not
production infrastructure. A generic `mco schedule` process does not advance
Score runs. The gateway's conductor sweep does.

## Confirmed faults

- Live gateway `/readyz` reported `score_sweep.configured=false`.
- Maintenance reported `ok=false,error=null`. This is stale/missing progress,
  not evidence that maintenance was disabled; maintenance starts unconditionally.
- Runtime recovery attempts were split across databases under
  `C:/AI/score-runtime`. Several obsolete attempts remain `running`.
- `score_sweep.open_conductor` did not construct the live repository executor,
  although the CLI's explicit `--live-repository-write` path did. Therefore
  merely enabling the sweep could block valid repository results.
- `mco stop` selects every TCP listener on a port, including Tailscale. It must
  not be used to restart this shared-port installation without fixing process
  attribution. Prior automated direct termination was denied; do not bypass it
  through desktop control or another shell.

## Accepted boundary and reconciliation

`C:/AI/score-runtime/via-option-c-continuation-recovery21/runs.db` records
`via-option-c-grok-20260920-21` as accepted. Its independent Grok review passed
commit `1e03b7d306a86ad7f069e0b35c04dc388936d52c`, with no findings.
Preserve its database, digest, artifact root and acceptance events unchanged.
The obsolete standalone job `44579c37-eec6-4839-bc74-e6f7bbb8a6a7`
was cancelled via MCO with that acceptance as the superseding evidence.
Do not sweep all discovered recovery databases or call completed G04 work new work.

## Repair in this change

CLI and gateway now share `configured_conductor`. The gateway's
`MCO_SCORE_LIVE_REPOSITORY_WRITE=true` attaches the existing grant-checked
repository executor. Default is false; malformed settings fail closed.
The flag creates no grant and does not enable cloud deployment. Every effect
still needs its existing run/digest/resource-bound authority.

## Activation and acceptance

1. Independently review this change and pass regression checks.
2. Select exactly one admitted continuation and its immutable database,
   artifact root, conductor identity and scoped grant. Explicitly exclude the
   obsolete recovery attempts. Do not copy rows or change credential hashes.
3. Configure the gateway with `MCO_SCORE_DB`, `MCO_SCORE_ARTIFACT_ROOT`,
   `MCO_SCORE_SWEEP_SECONDS` and, for repository work, the explicit live flag.
   The configured gateway identity must equal the run's identity.
4. Activate the reviewed gateway version with the supported process owner.
   Never kill Tailscale or an unverified PID. Installation alone does not
   replace a manually running server and may fail because its port is occupied.
5. Prove a zero-cost canary advances from start to accepted solely through the
   gateway lifespan, across work and independent review. Then prove a bounded
   granted repository packet, including its conductor-owned commit. A unit
   test, running service, or healthy HTTP response alone is insufficient.
6. Reconcile the pending Jev integration PR against accepted VIA work, stage
   the next repository packets, and let the Score dispatch them. Preserve the
   disabled/shadow-only provider fallback and existing human gates.
7. Complete the reviewed cloud conductor/state/worker deployment lane and
   signed release adapter. Verify independence from Beast before claiming
   production autonomy; retain launch and recurring-spend gates.

This file records repair evidence and remaining acceptance steps. It is not
a grant, cloud launch approval, or a claim that runtime activation is complete.
