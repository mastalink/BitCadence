"""BitCadence tray / menu-bar app.

pystray - one codebase for Windows tray, macOS menu bar, Linux AppIndicator.
It will not feel fully native on macOS; that is accepted. Do not reach for rumps.

This is a STATUS LIGHT AND A DOOR, not a control panel. Menu: Open Console,
Start all, Stop all, Restart all, per-worker (state + Restart), Quit.
Everything else is a click into the console.

The daemon's control API is local HTTP on 127.0.0.1:18790 (ADR 0002 §4.4).
Approval counts come from the GATEWAY, not the daemon - the daemon knows
about processes, not jobs. A stale count is never shown as live.

Headless servers have no tray. ``mco tray`` exits with a clear message;
the daemon must never depend on this app running.
"""

from __future__ import annotations

import os
import sys
import threading
import subprocess
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import quote

import httpx

from mco.tray.icons import (
    ICON_GREY,
    icon_state_from_status,
    render_icon,
    tooltip_text,
)

DAEMON_DEFAULT = "http://127.0.0.1:18790"
GATEWAY_DEFAULT = "http://127.0.0.1:18789"
POLL_INTERVAL_S = 5.0
HTTP_TIMEOUT_S = 2.0

AGENTD_TASK = "BitCadence-agentd"
AGENTD_LAUNCHD = "com.bitcadence.agentd"
AGENTD_SYSTEMD = "bitcadence-agentd.service"

HEADLESS_MESSAGE = (
    "BitCadence tray needs a display. This is a status light for a logged-in "
    "desktop session; the daemon does not depend on it. On a headless server, "
    "skip `mco tray`."
)

MISSING_EXTRA_MESSAGE = (
    "The tray extra is not installed. Install it with: "
    'pip install "bitcadence[tray]"'
)


@dataclass
class Snapshot:
    """One poll of daemon + gateway. Approval count is None when unknown."""

    icon_state: str
    daemon_reachable: bool
    status: Optional[dict[str, Any]]
    workers: list[dict[str, Any]]
    approval_count: Optional[int]


def display_available() -> bool:
    """False on headless servers so ``mco tray`` can exit cleanly, no traceback."""
    if os.environ.get("MCO_TRAY_FORCE_HEADLESS") == "1":
        return False
    platform = sys.platform
    if platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if platform == "darwin":
        # SSH-only sessions have no window server unless DISPLAY is forwarded.
        if os.environ.get("SSH_CONNECTION") and not os.environ.get("DISPLAY"):
            return False
        return True
    session = (os.environ.get("SESSIONNAME") or "").upper()
    if session == "SERVICES":
        return False
    return True


def load_local_token() -> str:
    """Bearer token from MCO_LOCAL_TOKEN in ~/.mco/.env (same as the gateway)."""
    try:
        from mco.config import get_config
        return (get_config().get("MCO_LOCAL_TOKEN") or "").strip()
    except Exception:
        return (os.environ.get("MCO_LOCAL_TOKEN") or "").strip()


def load_gateway_url() -> str:
    try:
        from mco.config import get_config
        return (get_config().get("MCO_GATEWAY_URL") or GATEWAY_DEFAULT).rstrip("/")
    except Exception:
        return (os.environ.get("MCO_GATEWAY_URL") or GATEWAY_DEFAULT).rstrip("/")


def load_daemon_url() -> str:
    return (os.environ.get("MCO_AGENTD_URL") or DAEMON_DEFAULT).rstrip("/")


def approval_count_from_jobs(jobs: Any) -> int:
    if not isinstance(jobs, list):
        return 0
    return sum(1 for job in jobs if isinstance(job, dict) and job.get("status") == "needs_approval")


def menu_structure(workers: list[dict[str, Any]], daemon_reachable: bool) -> list[dict[str, Any]]:
    """Serializable menu tree. Used by tests and by the pystray builder.

    Per-worker entries are submenus showing state with a Restart action.
    No settings, no forms.
    """
    items: list[dict[str, Any]] = [
        {"label": "Open Console", "action": "open_console"},
        {"label": "Start all", "action": "start_all"},
        {"label": "Stop all", "action": "stop_all"},
        {"label": "Restart all", "action": "restart_all"},
    ]
    if workers:
        for worker in workers:
            name = str(worker.get("name") or worker.get("instance") or "worker")
            state = str(worker.get("state") or "unknown")
            items.append({
                "label": f"{name} ({state})",
                "action": "submenu",
                "children": [{"label": "Restart", "action": "restart", "worker": name}],
            })
    else:
        hint = "Daemon not running" if not daemon_reachable else "No workers"
        items.append({"label": hint, "enabled": False})
    items.append({"label": "Quit", "action": "quit"})
    return items


def start_daemon_service() -> tuple[bool, str]:
    """Ask the OS to start BitCadence-agentd. Never raises."""
    try:
        if os.name == "nt":
            cmd = ["schtasks", "/Run", "/TN", AGENTD_TASK]
        elif sys.platform == "darwin":
            cmd = ["launchctl", "start", AGENTD_LAUNCHD]
        else:
            cmd = ["systemctl", "--user", "start", AGENTD_SYSTEMD]
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        detail = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode == 0:
            return True, detail or f"Started {AGENTD_TASK}"
        return False, detail or f"exit {completed.returncode}"
    except Exception as exc:
        return False, str(exc)


def _request(
    method: str,
    url: str,
    token: str,
    timeout: float = HTTP_TIMEOUT_S,
) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=timeout) as client:
        response = client.request(method, url, headers=headers)
        response.raise_for_status()
        if not response.content:
            return None
        try:
            return response.json()
        except Exception:
            return None


class TrayApp:
    """Polls the daemon + gateway and drives the status light."""

    def __init__(
        self,
        daemon_url: str = DAEMON_DEFAULT,
        gateway_url: str = GATEWAY_DEFAULT,
        token: str = "",
        poll_interval: float = POLL_INTERVAL_S,
        http_get: Optional[Callable[..., Any]] = None,
        http_post: Optional[Callable[..., Any]] = None,
        service_starter: Optional[Callable[[], tuple[bool, str]]] = None,
        opener: Optional[Callable[[str], Any]] = None,
    ):
        self.daemon_url = daemon_url.rstrip("/")
        self.gateway_url = gateway_url.rstrip("/")
        self.token = token
        self.poll_interval = poll_interval
        self._http_get = http_get or (lambda url: _request("GET", url, self.token))
        self._http_post = http_post or (lambda url: _request("POST", url, self.token))
        self._service_starter = service_starter or start_daemon_service
        self._opener = opener or webbrowser.open
        self._snapshot = Snapshot(
            icon_state=ICON_GREY,
            daemon_reachable=False,
            status=None,
            workers=[],
            approval_count=None,
        )
        self._stop = threading.Event()
        self._icon = None

    def refresh(self) -> Snapshot:
        status, reachable = self._poll_daemon()
        workers = list((status or {}).get("workers") or []) if reachable else []
        approvals = self._poll_approvals()
        state = icon_state_from_status(status, reachable)
        self._snapshot = Snapshot(
            icon_state=state,
            daemon_reachable=reachable,
            status=status,
            workers=workers,
            approval_count=approvals,
        )
        return self._snapshot

    def _poll_daemon(self) -> tuple[Optional[dict[str, Any]], bool]:
        try:
            data = self._http_get(f"{self.daemon_url}/v1/status")
        except Exception:
            return None, False
        if not isinstance(data, dict):
            return None, False
        return data, True

    def _poll_approvals(self) -> Optional[int]:
        """Live count from the gateway, or None when it cannot be reached.

        Never returns a cached value - a failed poll hides the badge.
        """
        try:
            data = self._http_get(f"{self.gateway_url}/api/jobs")
        except Exception:
            return None
        if data is None:
            return None
        if isinstance(data, dict) and "jobs" in data:
            data = data.get("jobs")
        if not isinstance(data, list):
            return None
        return approval_count_from_jobs(data)

    def start_all(self, *args: Any) -> None:
        snap = self._snapshot
        if not snap.daemon_reachable:
            self._service_starter()
            return
        for worker in snap.workers:
            self._post_worker(worker, "start")

    def stop_all(self, *args: Any) -> None:
        if not self._snapshot.daemon_reachable:
            return
        for worker in self._snapshot.workers:
            self._post_worker(worker, "stop")

    def restart_all(self, *args: Any) -> None:
        if not self._snapshot.daemon_reachable:
            self._service_starter()
            return
        for worker in self._snapshot.workers:
            self._post_worker(worker, "restart")

    def restart_worker(self, name: str, *args: Any) -> None:
        if not self._snapshot.daemon_reachable:
            return
        self._post_named(name, "restart")

    def open_console(self, *args: Any) -> None:
        self._opener(f"{self.gateway_url}/console")

    def quit(self, icon=None, item=None) -> None:
        self._stop.set()
        target = icon if icon is not None else self._icon
        if target is not None:
            try:
                target.stop()
            except Exception:
                pass

    def _post_worker(self, worker: dict[str, Any], action: str) -> None:
        name = str(worker.get("name") or worker.get("instance") or "")
        if name:
            self._post_named(name, action)

    def _post_named(self, name: str, action: str) -> None:
        path = f"{self.daemon_url}/v1/workers/{quote(name, safe='')}/{action}"
        try:
            self._http_post(path)
        except Exception:
            return

    def run(self) -> None:
        import pystray

        self.refresh()
        icon = pystray.Icon(
            "bitcadence",
            render_icon(self._snapshot.icon_state, self._snapshot.approval_count),
            tooltip_text(self._snapshot.icon_state, self._snapshot.approval_count),
            menu=self._build_menu(pystray),
        )
        self._icon = icon
        thread = threading.Thread(target=self._poll_loop, args=(icon, pystray), daemon=True)
        thread.start()
        icon.run()

    def _poll_loop(self, icon, pystray) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                snap = self.refresh()
                icon.icon = render_icon(snap.icon_state, snap.approval_count)
                icon.title = tooltip_text(snap.icon_state, snap.approval_count)
                icon.menu = self._build_menu(pystray)
                if hasattr(icon, "update_menu"):
                    icon.update_menu()
            except Exception:
                continue

    def _build_menu(self, pystray):
        snap = self._snapshot
        items = [
            pystray.MenuItem("Open Console", self.open_console, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start all", self.start_all),
            pystray.MenuItem("Stop all", self.stop_all),
            pystray.MenuItem("Restart all", self.restart_all),
            pystray.Menu.SEPARATOR,
        ]
        if snap.workers:
            for worker in snap.workers:
                name = str(worker.get("name") or worker.get("instance") or "worker")
                state = str(worker.get("state") or "unknown")
                items.append(
                    pystray.MenuItem(
                        f"{name} ({state})",
                        pystray.Menu(
                            pystray.MenuItem(
                                "Restart",
                                self._restart_callback(name),
                            ),
                        ),
                    )
                )
        else:
            hint = "Daemon not running" if not snap.daemon_reachable else "No workers"
            items.append(pystray.MenuItem(hint, None, enabled=False))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", self.quit))
        return pystray.Menu(*items)

    def _restart_callback(self, name: str):
        def _cb(*args: Any) -> None:
            self.restart_worker(name)
        return _cb


def preflight() -> Optional[str]:
    """Return an error message if the tray cannot run, else None."""
    if not display_available():
        return HEADLESS_MESSAGE
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return MISSING_EXTRA_MESSAGE
    return None


def main() -> None:
    message = preflight()
    if message:
        print(message, file=sys.stderr)
        raise SystemExit(1)
    app = TrayApp(
        daemon_url=load_daemon_url(),
        gateway_url=load_gateway_url(),
        token=load_local_token(),
    )
    app.run()


if __name__ == "__main__":
    main()
