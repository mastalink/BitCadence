# Private, bounded AWS hub-and-spoke lab

This is the lower-cost test profile, separate from the full EPCOT demonstration.
It uses account `314086896527`, region `us-east-1`, one `t3.small` hub and two
`t3.micro` spokes. Each machine has its own IAM role and requires IMDSv2.
Spokes can read only their own application token and the hub's public certificate;
they cannot approve jobs or read the operator token. Bedrock permission is limited
to Amazon Nova Micro in this region.

The security groups expose no ports to internet clients. Public IPv4 addresses
provide outbound access without a NAT gateway. Spokes reach the hub over private
TLS with certificate verification. Browser access uses an authenticated SSM tunnel
to the hub's loopback HTTP listener; no domain or public certificate is required.

## Deployment

The `../bootstrap` foundation is installed once with the account bootstrap identity.
It creates fixed runtime roles, a GitHub OIDC provider and deployment role, private
encrypted/versioned Terraform state storage, Secrets Manager entries and an immutable
ECR repository. No AWS access keys are stored in GitHub. The deployment role trusts
only `mastalink/BitCadence` at `codex/bitcadence-completion`; it cannot create IAM roles
or change its own permissions. Treat write access to that branch as deployment access.

The **AWS private test lab** workflow builds the exact commit, runs a real local TLS
smoke test with stubbed AWS services, validates resource bounds, then applies the plan
and runs acceptance over SSM. The cloud probe verifies authentication, both spokes,
approval gating, one bounded model inference and compliance-locked evidence. Failed
deployments attempt to stop all tagged lab machines and remain visibly failed.

Every test deployment schedules an external stop in two hours. Each node also has
a two-hour systemd stop timer on every boot. Spoke processes stop after ten minutes
or ten jobs, whichever comes first. Model calls accept at most 4,000 prompt characters
and 128 output tokens. CPU credits use standard mode to avoid surplus credit charges.

## Data and evidence

The hub database and TLS key live on a separate encrypted 8 GiB gp3 disk that Terraform
protects from deletion. Root disks are encrypted too. This is a single-zone test lab,
not a highly available production deployment; the data disk is not a backup. Evidence
is encrypted in a private S3 bucket, locked in COMPLIANCE mode for one day, and expires
after seven days. Objects cannot be deleted during their lock period. The full EPCOT
profile remains the path for RDS, multi-zone infrastructure and its complete chaos suite.

## Cost envelope

Approximate US East list-price envelope, before credits/tax: about **$0.06 per hour**
while all three machines run, including their IPv4 addresses. Persistent disks total
52 GiB, about **$4.16/month**; four Secrets Manager entries add **$1.60/month**.
Allow roughly **$6–8/month while stopped**, plus image/evidence storage and requests.
Short test sessions and bounded inference add to that. These are estimates, not a
billing cap. An alert email and budget amount are needed to configure cost notifications.

Sources checked September 5, 2026: [EC2 T3](https://aws.amazon.com/ec2/instance-types/t3/),
[IPv4](https://aws.amazon.com/vpc/pricing/), [gp3](https://aws.amazon.com/ebs/general-purpose/),
[Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/).

Stopping instances stops compute charges; disks, secrets and stored images remain.
The shutdown schedule and local timers are safeguards, not an account-wide spending cap.
There is no NAT gateway, RDS, ALB, managed Grafana or managed Prometheus in this profile.

## Operator access

Install AWS's Session Manager plugin, then use an authorized human AWS profile:

```powershell
aws ssm start-session --profile YOUR-OPERATOR-PROFILE --region us-east-1 --target HUB-INSTANCE-ID --document-name AWS-StartPortForwardingSession --parameters 'portNumber=18789,localPortNumber=18891'
```

Open `http://127.0.0.1:18891/console`. Retrieve the operator token through Secrets
Manager using an authorized human identity; do not paste it into logs or issue trackers.
Human SSO enrollment is separate from the working GitHub deployment identity.

Do not blindly destroy this configuration: the data volume and evidence bucket are
intentionally protected. A teardown should inventory retained data and active locks first.
