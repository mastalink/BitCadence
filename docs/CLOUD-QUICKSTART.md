# Your BitCadence cloud lab

Open `output/playwright/demo-pack/index.html` for the clickable diagram, operating
walkthrough and cost calculator. It works directly from disk, without a server.

## What lives where

Your computer connects through AWS Systems Manager to the hub. The hub coordinates
jobs and approvals. Two separate EC2 spokes execute worker and reviewer jobs over
private TLS. The hub database lives on a retained encrypted EBS disk. Audit evidence
lives in a private S3 bucket with one-day COMPLIANCE retention and seven-day expiry.
GitHub deploys using temporary OIDC credentials; no AWS keys are stored in GitHub.

This is one availability zone with one hub, not a production HA deployment. Human
SSO enrollment is still pending. The current local `batoncadence` profile is the
bootstrap root login; the automated deployment uses its separate restricted role.

## Start a session

1. Open the [verified deployment run](https://github.com/mastalink/BitCadence/actions/runs/33995144144).
2. For the exact tested revision, choose **Re-run all jobs**. This reruns planning,
   resets the external two-hour stop schedule, starts stopped nodes, and repeats
   acceptance. It redeploys that run's commit, not later code. To deploy later code,
   use its own AWS private test lab run on `codex/bitcadence-completion`.
3. Wait for **Verify hub and spoke execution** to pass. First boot can take several
   minutes. A failed run attempts to stop the lab.

The workflow is currently on the candidate branch. Its manual Run workflow button
may not appear until the workflow exists on the default branch; the existing run's
Re-run all jobs control is the available route for this tested revision.

## Connect

In PowerShell:

```powershell
Set-Location C:\AI\baton\wt\bitcadence-completion
powershell -File scripts/cloud_lab.ps1 -Action Status
powershell -File scripts/cloud_lab.ps1 -Action Connect
```

Keep the tunnel running. Open `http://127.0.0.1:18891/console`. In **Settings →
Connection**, use gateway URL `http://127.0.0.1:18891` and the operator token from
AWS Secrets Manager's `bitcadence-lab/operator` secret in us-east-1. Connect and
confirm the sidebar says **Live**. The console starts with simulated data until
connected; that is not evidence of cloud activity. Keep the token out of recordings.

The helper checks account `314086896527` and discovers current instance IDs by
project and role tags. You do not need to copy new IDs after replacement. The
Session Manager plugin is installed in your local BitCadence tools directory.
If AWS says your session expired, run `aws login --profile batoncadence --region
us-east-1` and complete the browser login before retrying.

## Submit and approve

1. Open **Job Board → + New job**.
2. Enter a harmless title and instruction. Choose **reviewer**.
3. Check **Ask me before it runs**, then **Create job**.
4. Open the job. It should say **Needs your OK**.
5. Click **Approve & run**. The reviewer spoke should finish it and add History
   entries identifying the actor. This default lab worker returns a checksum.

The separate cloud acceptance probe also ran a bounded Nova Micro inference and
verified that a worker token cannot approve jobs. Those are distinct checks; the
checksum demo does not demonstrate AI reasoning. Spoke processes are bounded to
ten minutes or ten jobs, so use a fresh session if they have reached their limit.

## Inspect and stop

The job detail shows output and History. **Governance** shows approval decisions.
In S3, match the full job ID to `ledger/JOB-ID/`; check object version, lock mode
and retain-until date. A green completed status alone does not prove retention.

**Settings → Gateway controls → Stop work → Save gateway settings** halts active
attempts and pauses intake. Turning it off does not automatically retry halted jobs.
That switch does not shut down EC2. To stop compute:

```powershell
powershell -File scripts/cloud_lab.ps1 -Action Stop
powershell -File scripts/cloud_lab.ps1 -Action Status
```

Confirm all three machines say **stopped**. Closing the browser/tunnel does not
stop them. Every deployment has an external two-hour stop plus local boot timers.
Do not destroy the stack casually: the data disk and evidence bucket are protected.

## Cost and remaining setup

Estimate $0.06/hour for all three running machines and IPv4 addresses, plus roughly
$6–8/month retained baseline. Model usage, requests, storage, tax and credits affect
the bill. The $25 account tracking budget has no email notifications yet and is not
a hard cap. Alert email and non-root human access remain to be configured.

## Recorded evidence — September 5, 2026

- Cloud run **33995144144** passed unauthenticated access denial, both spokes over
  TLS, operator approval, worker approval denial, Bedrock inference, and versioned
  COMPLIANCE-locked S3 evidence. PostgreSQL acceptance run **33995144142** passed.
- Approval video: live AWS reviewer completed job `13649e3f...` after UI approval.
- Stop/resume video: live settings saved in both directions. It is not a filmed
  stale-worker attack or evidence that an already-running job was interrupted.
- Infrastructure video: an explanatory diagram, not a live AWS status display.
- Console Settings requests an unavailable integrations endpoint (403); external
  connectors are not configured. Browser Babel production-build warning remains.

Recordings are silent screen captures. Storyboard narration and claim boundaries
are in `docs/INVESTOR-DEMO-STORYBOARDS.md`; that plan is broader than what each
actual clip proves.
