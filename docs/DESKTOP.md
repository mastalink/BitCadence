# BitCadence local desktop manager

Windows now has a native control window and notification-area icon. Open **BitCadence**
from the Desktop or Start menu. You can pin that shortcut to the taskbar using Windows.
The app launches the local gateway, scheduler and configured workers without CMD windows.

## First use

1. If workers were installed as scheduled tasks, choose **Move workers into app**.
   This disables their recurring task triggers, stops their current executions, and
   starts the fleet under the desktop supervisor. Do this between jobs: it terminates
   running executor processes. Scheduled task definitions are retained for recovery.
2. Otherwise choose **Start all**. The gateway must pass readiness before workers start.
3. Choose **Open console** for the job board, approvals and application settings.
   Existing authentication and the configured data store are preserved.

**Start/Stop/Restart selected** controls one component. **Stop all** disables the
configured worker task triggers and stops the fleet, scheduler and gateway, in that order.
Workers set to `mode = "off"` remain disabled. **Fleet settings** opens the existing
`~/.mco/fleet.toml`; save it, choose **Reload settings**, then start affected workers.
Changed workers stop when configuration is reloaded.

Closing the window hides it in the tray. Double-click the tray icon or open the shortcut
again to restore it. **Exit** in the tray stops processes launched by this app and their
descendants. Existing external workers remain running until explicitly stopped or moved.
The application currently starts manually at login; it does not add a login trigger.

The table distinguishes owned processes from external processes. Discovery matches
the exact CLI command, role and instance (or exact polling launcher); MCP sessions and
the separate review gateway are excluded. Duplicate launches are refused. A Windows
Job Object kills owned children if the manager crashes, and orphan recovery verifies
creation time and executable path before touching a recorded PID.

Unified and per-component logs rotate under `~/.mco/desktop/logs/`. The window shows
the latest 100 lines. Processes that are still external retain their original logs.
The worker supervisor retries failures with backoff and stops after five crashes in
five minutes. **Start selected** resets that crash limit.

## Install from a checkout

With BitCadence dependencies already installed in a Python environment:

```powershell
./scripts/install_desktop.ps1 -Python ./.venv/Scripts/python.exe
```

This installs Pillow/pystray into the checkout's ignored `.codex/desktopdeps` folder,
generates the icon, and creates Desktop and Start-menu shortcuts. Keep the checkout
and selected Python environment in place; the shortcuts run that source checkout.
For a package installation, use `pip install "bitcadence[desktop]"`, then run
`bitcadence-desktop`. Python must include Tk support.

The desktop interface itself is Windows-only. This does not change Linux/cloud
service deployment. AWS CLI is not needed for this local application.
