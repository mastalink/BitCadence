# VIA G01 cloud audit checkpoint receipt

Run: `via-g01-cloud-20260914`. Score digest: `982b0b0ff22ef3e496098c60d1375519d6a316ce1f140fd862ca73c8b6bc68fb`.

Status: **accepted read-only cloud-audit checkpoint**. This does not complete VIA G01 or authorize deployment.

- Work job `c310b307-01ee-5519-b4c2-2635d95354d4` completed under the dedicated auditor identity.
- Evidence `via-readonly-audit-20260914T212041698172Z.json` was validated at SHA-256 `8e98e16f6a31d75c79547c05c20fa2b61d587977591d7343ae312ff0464e77a1`.
- Independent review job `e7816a32-ad1d-552d-b97a-d3a36e5316c0` accepted the exact evidence under the dedicated reviewer identity.

Observed using fixed AWS read APIs and public HTTPS GETs:

- The VIA pilot instance is running; SSM reports it online.
- The deployed instance has IMDSv2 tokens required.
- No CodeBuild projects are configured.
- The `via-pilot` stack exposes the expected EC2, CloudFront, S3 evidence, budget, IAM profile/role and network resources.
- The evidence bucket’s bounded backup listing has 13 entries; latest inspected backup was 2026-09-14T04:33:23Z and 1,670,869 bytes. This is not a restore verification.
- `via-pilot-Availability` and `via-pilot-BackupHealthy` were both `OK`.
- The public API is healthy. Its Lorain confession query returned 24 churches, four confession occurrences across two churches.

The interactive administrator identity is root and was correctly flagged as unsuitable for autonomous work. No durable least-privilege audit/deployment runner was proven. The audit did not query the host, database migration head, backup restore, cloud collection path or Android device; these remain unknown. No SSM command, deploy, restart, IAM mutation, purchase or production-data mutation occurred.

The accepted next bounded work is to design and obtain a least-privilege AWS workload role or cloud runner, then run a separately versioned Score to verify host release state and recovery. Do not reuse root credentials in agent jobs.
