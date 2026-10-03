# OS Services & Fleet Configuration

## Goal
Configure BitCadence processes (gateway, scheduler, wakers) as boot-persistent operating system services, declare worker fleet topologies in `fleet.toml`, and reconcile services via `mco fleet apply`.

> **See Also:** For in-depth coverage of background deputies, the `claude-cio` Chief sidecar, elevated admin-pack flows, health verification levels, and locked-log/duplicate-waker troubleshooting, see [**Sidecars & Autonomous Fleet Daemons**](sidecars-and-fleet-daemons.md).

---

## Step-by-Step Instructions

### 1. Declarative Fleet Configuration (`fleet.toml`)
BitCadence defines worker deployment topologies in `~/.mco/fleet.toml`:

```toml
[workers.codex-builder]
role = "codex"
instance = "codex-beast"
mode = "waker"                 # "waker" (event-driven), "poll" (timer), or "off"
exec = "C:/AI/BitCadence/scripts/workers/codex-worker-run.ps1"
min_interval = 10
poll_interval = 1800
background = false             # Set true for unattended S4U Windows Task

[workers.claude-cio]
role = "chief"
instance = "claude-cio"
mode = "waker"
exec = "python -m mco.orchestrator.cio_runner"
min_interval = 10
background = true              # Survives user sign-out
```

### 2. Applying Fleet Run Modes
Apply the configuration to spawn and align worker processes:

```powershell
# Apply declarative fleet settings
mco fleet apply

# Inspect current fleet status
mco fleet status

# Change an individual worker mode
mco fleet set codex-builder mode off
```

### 3. Installing OS Services
To survive system restarts and user logouts, install BitCadence components as native OS services:

**On Windows:**
Installs via Windows Task Scheduler or Windows Service Control Manager:
```powershell
# Install the gateway service
mco service install

# Install the background scheduler service
mco service install-scheduler

# Install the event waker service
mco service install-waker
```

**On Linux:**
Creates and enables `systemd` user units (`~/.config/systemd/user/bitcadence.service`):
```bash
mco service install
systemctl --user daemon-reload
systemctl --user enable --now bitcadence
```

### 4. Service Diagnostics and Lifecycle
```powershell
# Check service execution status
mco service status

# Restart services after code updates
mco service restart

# View unified service logs
mco service logs
```

---

## What You'll See

- **Self-Healing Supervision:** If a worker crashes, the supervisor restarts it with exponential backoff (up to 5 crashes in 5 minutes before pausing to avoid rapid crash loops).
- **Background Execution:** Services run without open terminal windows or user session dependencies.

---

## If It Goes Wrong

### 1. "Access is denied when installing service"
- **Cause:** Installing system-wide services requires administrative privileges.
- **Fix:** Open PowerShell as Administrator, or use user-level task installation.

### 2. "Workers crash repeatedly on startup"
- **Cause:** Missing environment variables or Python dependencies in the service execution context.
- **Fix:** Inspect logs with `mco service logs` to view standard error traces.
