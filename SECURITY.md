# Security Policy

## Reporting a vulnerability

Email **security@bitcadence.ai**. Please do not open a public issue for
anything exploitable. You will get an acknowledgment within 48 hours and a
fix or mitigation plan within 7 days for confirmed issues.

## Supported versions

| Version | Supported |
|---|---|
| latest release (0.2.x) | ✅ |
| older | best effort - upgrade first |

## Security model (summary)

- The gateway binds to `127.0.0.1` by default; exposing it requires a
  deliberate `--host` change and `MCO_LOCAL_TOKEN` / registered agent
  tokens on every request and WebSocket.
- Tokens are stored as SHA-256 hashes only and shown exactly once at
  registration. Scope them to least privilege (`docs/ENTERPRISE.md`).
- The worker's raw shell executor is **off by default**
  (`MCO_ENABLE_SHELL_EXECUTOR` opt-in) - a job payload must not be a remote
  code execution path.
- The audit trail (`agent_job_events`) is append-only at the storage layer
  in both the embedded store and Postgres.
- Secrets live in `~/.mco/.env` or the AES-256-GCM secret store
  (`~/.mco/secrets.enc`); nothing phones home.
- Drumline context injected into prompts is framed as untrusted reference
  data, never as instructions (prompt-injection containment).

Hardening guidance for network exposure, SSO delegation, and air-gapped
installs: `docs/ENTERPRISE.md`, `docs/AIRGAP.md`, and the Security section
of the README.
