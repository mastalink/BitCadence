"""BitCadence tray / menu-bar app.

pystray - one codebase for Windows tray, macOS menu bar, Linux AppIndicator.
It will not feel fully native on macOS; that is accepted. Do not reach for rumps.

This is a STATUS LIGHT AND A DOOR, not a control panel. Menu: Open Console,
Start all, Stop all, Restart all, per-worker (state + mode + Restart, and
Reset when crashlooped), Quit. Everything else is a click into the console.

Two credentials, never mixed (ADR 0002 R7):
  daemon control API  -> ~/.mco/agentd.token
  gateway (approvals) -> the existing gateway token
If agentd.token is missing, the daemon is treated as unreachable (grey icon).
There is no fallback to the gateway token.

/v1/logs is a separate capability. The tray does not put logs in the menu; if
it ever fetches them, a missing or rejected log token degrades to "no logs"
instead of erroring.

The control endpoint is per-user, not per-machine (ADR 0002 R1): loopback,
port namespaced by the logged-in user. Workers do not run before login.

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
from pathlib import Path
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

AGENTD_TOKEN_PATH = Path.home() / ".mco" / "agentd.token"
AGENTD_LOGS_TOKEN_PATH = Path.home() / ".mco" / "agentd.logs.token"

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

_CRASHLOOPED = "crashlooped"


@dataclass
class Snapshot:
    """One poll of daemon + gateway. Approval count is None when unknown."""

    icon_state: str
    daemon_reachable: bool
    status: Optional[dict[str, Any]]
    workers: list[dict[str, Any]]
    approval_count: Optional[int]
    logs: Optional[list[str]] = None


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


def _read_token_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_agentd_control_token() -> str:
    """Daemon control bearer. Never falls back to the gateway token.

    A missing or empty ``~/.mco/agentd.token`` returns "" so the tray paints
    grey rather than sending ``MCO_LOCAL_TOKEN`` at the daemon. This function
    does not create the file — that is the daemon's job.
    """
    env = (os.environ.get("MCO_AGENTD_TOKEN") or "").strip()
    if env:
        return env
    return _read_token_file(AGENTD_TOKEN_PATH)


def load_agentd_logs_token() -> str:
    """Separate log-read capability (ADR 0002 R7). Empty means no log access."""
    env = (os.environ.get("MCO_AGENTD_LOGS_TOKEN") or "").strip()
    if env:
        return env
    return _read_token_file(AGENTD_LOGS_TOKEN_PATH)


def load_gateway_token() -> str:
    """Gateway bearer for approval counts. Distinct from the daemon token."""
    try:
        from mco.config import get_config
        cfg = get_config()
        token = (
            cfg.get("MCO_AGENT_TOKEN")
            or cfg.get("MCO_LOCAL_TOKEN")
            or ""
        )
        if token:
            return str(token).strip()
    except Exception:
        pass
    return (
        os.environ.get("MCO_AGENT_TOKEN")
        or os.environ.get("MCO_LOCAL_TOKEN")
        or ""
    ).strip()


def load_gateway_url() -> str:
    try:
        from mco.config import get_config
        return (get_config().get("MCO_GATEWAY_URL") or GATEWAY_DEFAULT).rstrip("/")
    except Exception:
        return (os.environ.get("MCO_GATEWAY_URL") or GATEWAY_DEFAULT).rstrip("/")


def load_daemon_url() -> str:
    """Per-user loopback URL. Env override wins; otherwise the namespaced port."""
    env = (os.environ.get("MCO_AGENTD_URL") or "").strip()
    if env:
        return env.rstrip("/")
    from mco.agentd.control import CONTROL_HOST, CONTROL_PORT
    return f"http://{CONTROL_HOST}:{CONTROL_PORT}"


def approval_count_from_jobs(jobs: Any) -> int:
    if not isinstance(jobs, list):
        return 0
    return sum(1 for job in jobs if isinstance(job, dict) and job.get("status") == "needs_approval")


def worker_name(worker: dict[str, Any]) -> str:
    return str(worker.get("name") or worker.get("instance") or "worker")


def worker_state(worker: dict[str, Any]) -> str:
    return str(worker.get("state") or "unknown")


def worker_mode(worker: dict[str, Any]) -> str:
    """Fleet schema field. Disabled is mode='off'; there is no 'enabled'."""
    return str(worker.get("mode") or "").strip()


def worker_menu_label(worker: dict[str, Any]) -> str:
    name = worker_name(worker)
    state = worker_state(worker)
    mode = worker_mode(worker)
    if mode:
        return f"{name} ({state}, {mode})"
    return f"{name} ({state})"


def is_crashlooped(worker: dict[str, Any]) -> bool:
    return worker_state(worker).lower().replace("-", "").replace("_", "") == _CRASHLOOPED


def menu_structure(workers: list[dict[str, Any]], daemon_reachable: bool) -> list[dict[str, Any]]:
    """Serializable menu tree. Used by tests and by the pystray builder.

    Per-worker entries are submenus showing observed state and fleet mode,
    with Restart, plus Reset when the worker is crashlooped (a latched
    terminal state — not a transient retry). No settings, no forms.
    """
    items: list[dict[str, Any]] = [
        {"label": "Open Console", "action": "open_console"},
        {"label": "Start all", "action": "start_all"},
        {"label": "Stop all", "action": "stop_all"},
        {"label": "Restart all", "action": "restart_all"},
    ]
    if workers:
        for worker in workers:
            name = worker_name(worker)
            children = [{"label": "Restart", "action": "restart", "worker": name}]
            if is_crashlooped(worker):
                children.append({"label": "Reset crash loop", "action": "reset", "worker": name})
            items.append({
                "label": worker_menu_label(worker),
                "action": "submenu",
                "children": children,
            })
    else:
        hint = "Daemon not running" if not daemon_reachable else "No workers"
        items.append({"label": hint, "enabled": False})
    items.append({"label": "Quit", "action": "quit"})
    return items


def start_daemon_service() -> tuple[bool, str]:
    """Ask the OS to start BitCadence-agentd for this user session. Never raises."""
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
        daemon_token: str = "",
        gateway_token: str = "",
        logs_token: str = "",
        poll_interval: float = POLL_INTERVAL_S,
        http_get: Optional[Callable[..., Any]] = None,
        http_post: Optional[Callable[..., Any]] = None,
        service_starter: Optional[Callable[[], tuple[bool, str]]] = None,
        opener: Optional[Callable[[str], Any]] = None,
    ):
        self.daemon_url = daemon_url.rstrip("/")
        self.gateway_url = gateway_url.rstrip("/")
        self.daemon_token = daemon_token
        self.gateway_token = gateway_token
        self.logs_token = logs_token
        self.poll_interval = poll_interval
        self._http_get = http_get
        self._http_post = http_post
        self._service_starter = service_starter or start_daemon_service
        self._opener = opener or webbrowser.open
        self._snapshot = Snapshot(
            icon_state=ICON_GREY,
            daemon_reachable=False,
            status=None,
            workers=[],
            approval_count=None,
            logs=None,
        )
        self._stop = threading.Event()
        self._icon = None

    def _get(self, url: str, token: str) -> Any:
        if self._http_get is not None:
            return self._http_get(url, token)
        return _request("GET", url, token)

    def _post(self, url: str, token: str) -> Any:
        if self._http_post is not None:
            return self._http_post(url, token)
        return _request("POST", url, token)

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
            logs=self._snapshot.logs,
        )
        return self._snapshot

    def _poll_daemon(self) -> tuple[Optional[dict[str, Any]], bool]:
        if not self.daemon_token:
            return None, False
        try:
            data = self._get(f"{self.daemon_url}/v1/status", self.daemon_token)
        except Exception:
            return None, False
        if not isinstance(data, dict):
            return None, False
        return data, True

    def _poll_approvals(self) -> Optional[int]:
        """Live count from the gateway, or None when it cannot be reached.

        Never returns a cached value - a failed poll hides the badge.
        Uses the gateway token, never the daemon token.
        """
        if not self.gateway_token:
            return None
        try:
            data = self._get(f"{self.gateway_url}/api/jobs", self.gateway_token)
        except Exception:
            return None
        if data is None:
            return None
        if isinstance(data, dict) and "jobs" in data:
            data = data.get("jobs")
        if not isinstance(data, list):
            return None
        return approval_count_from_jobs(data)

    def fetch_logs(self, worker: str | None = None, tail: int = 100) -> Optional[list[str]]:
        """Best-effort log tail. Not shown in the menu.

        Control access does not imply log access. Missing token, 401/403, or
        any transport error returns None — never raises, never retries with
        the control token.
        """
        if not self.logs_token:
            return None
        query = f"tail={int(tail)}"
        if worker:
            query += f"&worker={quote(worker, safe='')}"
        try:
            data = self._get(f"{self.daemon_url}/v1/logs?{query}", self.logs_token)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        lines = data.get("lines")
        if not isinstance(lines, list):
            return None
        return [str(line) for line in lines]

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

    def reset_worker(self, name: str, *args: Any) -> None:
        """Clear a latched crashlooped state. No-op if the daemon is gone."""
        if not self._snapshot.daemon_reachable:
            return
        self._post_named(name, "reset")

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
        name = worker_name(worker)
        if name:
            self._post_named(name, action)

    def _post_named(self, name: str, action: str) -> None:
        if not self.daemon_token:
            return
        path = f"{self.daemon_url}/v1/workers/{quote(name, safe='')}/{action}"
        try:
            self._post(path, self.daemon_token)
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
                name = worker_name(worker)
                submenu_items = [
                    pystray.MenuItem("Restart", self._restart_callback(name)),
                ]
                if is_crashlooped(worker):
                    submenu_items.append(
                        pystray.MenuItem("Reset crash loop", self._reset_callback(name)),
                    )
                items.append(
                    pystray.MenuItem(
                        worker_menu_label(worker),
                        pystray.Menu(*submenu_items),
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

    def _reset_callback(self, name: str):
        def _cb(*args: Any) -> None:
            self.reset_worker(name)
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
        daemon_token=load_agentd_control_token(),
        gateway_token=load_gateway_token(),
        logs_token=load_agentd_logs_token(),
    )
    app.run()


if __name__ == "__main__":
    main()
