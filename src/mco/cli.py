"""
BitCadence Typer CLI & Setup Wizard
===================================
Provides user onboarding, credentials encryption, FastAPI serving,
and background daemon listener.
"""

from __future__ import annotations

import os
import sys
import asyncio
import secrets
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("mco.cli")


import typer
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from mco.config import get_config
from mco.security import get_secret_store
from mco.orchestrator.routes import (
    router as jobs_router,
    agents_router,
    events_router,
    version_router,
    register_broadcast_callback,
)
from mco.orchestrator.utils import get_approver_roles
from mco.orchestrator.listener import AgentListener
from mco.notifiers.ntfy import notify, notify_agent_online, notify_agent_offline, get_ntfy_config, notify_gateway_startup

# Initialize typer app and console
app = typer.Typer(help="BitCadence: Multi-Client Agent Orchestrator.")
console = Console()


def get_version() -> str:
    """Installed distribution version (single source of truth: pyproject)."""
    from importlib.metadata import version as _dist_version
    for dist in ("bitcadence", "mco"):  # 'mco' = pre-0.2 editable installs
        try:
            return _dist_version(dist)
        except Exception:
            continue
    return "unknown"


def _version_callback(value: bool):
    if value:
        console.print(f"BitCadence {get_version()}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show the version and exit."),
):
    """BitCadence: Multi-Client Agent Orchestrator."""

# ─────────────────────────────────────────────────────────────────────────────
# 1. Onboarding Setup Wizard
# ─────────────────────────────────────────────────────────────────────────────
@app.command("setup")
def setup_wizard(
    guided: bool = typer.Option(False, "--guided", help="Run the full guided walkthrough."),
    menu: bool = typer.Option(False, "--menu", help="Jump straight to the settings menu."),
):
    """Configure BitCadence - a guided walkthrough or a jump-anywhere settings menu."""
    from mco.setup_wizard import run_setup
    run_setup(guided=guided, menu=menu)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Serve Command (FastAPI HTTP + WebSocket server)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ConnectionIdentity:
    role: str = ""
    instance_id: str = ""
    is_admin: bool = False


@dataclass
class ManagedConnection:
    websocket: WebSocket
    identity: ConnectionIdentity


class ConnectionManager:
    """Manages active WebSocket subscription channels."""

    def __init__(self):
        self.active_connections: list[ManagedConnection] = []

    async def connect(self, websocket: WebSocket, identity: ConnectionIdentity):
        await websocket.accept()
        self.register(websocket, identity)

    def register(self, websocket: WebSocket, identity: ConnectionIdentity):
        self.active_connections.append(ManagedConnection(websocket, identity))

    def disconnect(self, websocket: WebSocket):
        self.active_connections = [
            connection
            for connection in self.active_connections
            if connection.websocket is not websocket
        ]

    async def broadcast(self, message: dict, job: Optional[dict] = None):
        if job is None:
            payload = message.get("payload") or {}
            job = payload.get("job")
        for connection in self.active_connections:
            if not self._can_receive(connection.identity, job):
                continue
            try:
                await connection.websocket.send_json(message)
            except Exception:
                pass

    @staticmethod
    def _can_receive(identity: ConnectionIdentity, job: Optional[dict]) -> bool:
        if identity.is_admin:
            return True
        if not job:
            return False
        target_role = str(job.get("target_agent_role") or "")
        if target_role.lower() != (identity.role or "").lower():
            return False
        target_id = job.get("target_agent_id")
        return not target_id or target_id == identity.instance_id


ws_manager = ConnectionManager()


def _is_admin_scope_role(role: Any) -> bool:
    return str(role or "").lower() in get_approver_roles()


async def server_broadcast_callback(event: str, job: dict) -> None:
    """Callback triggered by REST router updates to notify WebSocket clients."""
    payload = {
        "type": "event",
        "payload": {
            "event": event,
            "job": job
        }
    }
    await ws_manager.broadcast(payload, job)

def create_app() -> FastAPI:
    """Create and configure the FastAPI application server."""
    app_server = FastAPI(
        title="BitCadence Gateway Server",
        description="FastAPI WebSocket and REST Hub for Agent Job Coordination."
    )

    # Authlib keeps OIDC state/PKCE material in the database cache; this
    # signed cookie contains only the transaction marker needed for CSRF
    # correlation. Hosted deployments must provide a stable secret.
    session_secret = get_config().get("MCO_SESSION_SECRET")
    if session_secret:
        if len(str(session_secret)) < 32:
            raise RuntimeError("MCO_SESSION_SECRET must be at least 32 characters")
        from starlette.middleware.sessions import SessionMiddleware
        secure_cookie = str(
            get_config().get("MCO_SESSION_COOKIE_SECURE", "true") or "true"
        ).lower() in {"1", "true", "yes", "on"}
        app_server.add_middleware(
            SessionMiddleware,
            secret_key=str(session_secret),
            session_cookie="mco_oidc_state",
            max_age=600,
            same_site="lax",
            https_only=secure_cookie,
        )

    # Per-token (fallback per-IP) rate limiting - exempts /healthz, configured
    # via MCO_RATE_LIMIT (requests/min, default 120; set to 0 to disable).
    from mco.ratelimit import build_rate_limit_store, RateLimitMiddleware
    _rl_store = build_rate_limit_store()
    if _rl_store is not None:
        app_server.add_middleware(RateLimitMiddleware, store=_rl_store)

    # Mount REST routing
    app_server.include_router(jobs_router)
    app_server.include_router(agents_router)
    app_server.include_router(events_router)
    app_server.include_router(version_router)

    # Enterprise integrations (ServiceNow, Dynatrace, webhooks)
    from mco.orchestrator.integration_routes import integrations_router
    app_server.include_router(integrations_router)

    # Drumline shared context (collective agent memory)
    from mco.orchestrator.context_routes import context_router
    app_server.include_router(context_router)

    # Admin API: agent management, settings, workflow submission (Control Panel)
    from mco.orchestrator.admin_routes import (
        agents_admin_router,
        governance_router,
        llm_connections_router,
        settings_router,
        workflows_router,
    )
    app_server.include_router(agents_admin_router)
    app_server.include_router(governance_router)
    app_server.include_router(settings_router)
    app_server.include_router(workflows_router)
    app_server.include_router(llm_connections_router)

    # Human identity federation and server-managed browser sessions.
    from mco.orchestrator.identity_routes import auth_router, identity_admin_router
    app_server.include_router(identity_admin_router)
    app_server.include_router(auth_router)

    # Prometheus metrics (/metrics)
    from mco.orchestrator.metrics_routes import metrics_router
    app_server.include_router(metrics_router)

    # Unauthenticated liveness/readiness probe for cloud load balancers and
    # orchestrators (K8s, ECS, Cloud Run). Reports DB wiring, never secrets.
    @app_server.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        from mco.orchestrator.routes import get_db_client, kill_switch_active
        client = get_db_client()
        return {
            "status": "ok",
            "database": client is not None,
            "backend": getattr(client, "backend", "supabase") if client is not None else None,
            "paused": kill_switch_active(),
        }

    # Control-plane dashboard (static single page; auth happens via the API token)
    from fastapi.responses import HTMLResponse
    from mco.dashboard import DASHBOARD_HTML

    @app_server.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> str:
        return DASHBOARD_HTML

    # BitCadence Console (full control-plane GUI; auth via API bearer token)
    from mco.console import get_console_html

    @app_server.get("/console", response_class=HTMLResponse, include_in_schema=False)
    async def console_ui() -> str:
        return get_console_html()

    # Flow Control - the live DAG of the board: design intent, run state,
    # approval gates, and the audit trail on one canvas.
    from mco.console import get_flow_html

    @app_server.get("/flow", response_class=HTMLResponse, include_in_schema=False)
    async def flow_ui() -> str:
        return get_flow_html()

    # Register broadcast callback
    register_broadcast_callback(server_broadcast_callback)

    # WebSocket Broadcast route
    @app_server.websocket("/ws/broadcast")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        
        # Wait up to 5 seconds for authentication frame
        authenticated = False
        authenticated_instance_id = None
        authenticated_role = None
        
        from mco.orchestrator.routes import get_db_client
        db_client = get_db_client()
        
        if not db_client:
            # Local-Only mode (no DB). Mirror the HTTP auth path (auth.py):
            # if MCO_LOCAL_TOKEN is set, the WebSocket must present it too;
            # only with no token configured do we fall back to the zero-config
            # loopback bypass. This closes the gap where a token-protected
            # gateway bound to 0.0.0.0 still accepted unauthenticated sockets.
            local_token = (get_config().get("MCO_LOCAL_TOKEN") or "").strip()
            if local_token:
                try:
                    auth_data_str = await asyncio.wait_for(
                        websocket.receive_text(), timeout=5.0)
                    auth_msg = json.loads(auth_data_str)
                    supplied = (auth_msg.get("payload") or {}).get("token", "")
                except Exception:
                    supplied = ""
                if not hmac.compare_digest(str(supplied), local_token):
                    logger.warning("WebSocket rejected: bad/missing MCO_LOCAL_TOKEN.")
                    try:
                        await websocket.send_json({"type": "authenticated",
                            "payload": {"success": False, "error": "Authentication failed"}})
                        await websocket.close()
                    except Exception:
                        pass
                    return
                authenticated = True
                authenticated_role = "admin"
                ws_manager.register(
                    websocket,
                    ConnectionIdentity(role="admin", instance_id="", is_admin=True),
                )
                # Ack success so clients (console, `mco watch`) know they're in
                # without waiting for the first broadcast.
                try:
                    await websocket.send_json({"type": "authenticated",
                                               "payload": {"success": True}})
                except Exception:
                    pass
            else:
                # No token configured: zero-config local use (loopback default).
                logger.warning("No MCO_LOCAL_TOKEN set — accepting local WebSocket without auth.")
                authenticated = True
                authenticated_role = "admin"
                ws_manager.register(
                    websocket,
                    ConnectionIdentity(role="admin", instance_id="", is_admin=True),
                )
        else:
            try:
                # 1. Read first message (should be authenticate)
                auth_data_str = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
                auth_msg = json.loads(auth_data_str)
                msg_type = auth_msg.get("type")
                payload = auth_msg.get("payload") or {}
                
                if msg_type == "authenticate":
                    instance_id = payload.get("instance_id")
                    role = payload.get("role")
                    token = payload.get("token")

                    if token:
                        # Verify by token hash. instance_id, when supplied, must
                        # match the same row; token-only auth (console, `mco
                        # watch`) resolves the identity from the hash alone —
                        # the token is the secret either way.
                        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

                        q = db_client.table("agent_registry")\
                            .select("*")\
                            .eq("auth_token_hash", token_hash)
                        if instance_id:
                            q = q.eq("instance_id", instance_id)
                        res = q.execute()

                        if res.data:
                            row = res.data[0]
                            instance_id = row.get("instance_id") or instance_id
                            role = row.get("role") or role
                            authenticated = True
                            authenticated_instance_id = instance_id
                            authenticated_role = role

                            # Update status to online in database
                            from datetime import datetime, timezone
                            db_client.table("agent_registry").update({
                                "status": "online",
                                "last_seen_at": datetime.now(timezone.utc).isoformat()
                            }).eq("instance_id", instance_id).execute()

                            # Register in ws_manager for broadcast receiving
                            ws_manager.register(
                                websocket,
                                ConnectionIdentity(
                                    role=role or "",
                                    instance_id=instance_id or "",
                                    is_admin=_is_admin_scope_role(role),
                                ),
                            )

                            # Send success frame
                            await websocket.send_json({
                                "type": "authenticated",
                                "payload": {"success": True}
                            })
                            logger.info(f"Agent '{instance_id}' ({role}) successfully authenticated.")

                            # NTFY addon: notify agent online
                            try:
                                notify_agent_online(role, instance_id)
                            except Exception:
                                pass
                        
                if not authenticated:
                    await websocket.send_json({
                        "type": "authenticated",
                        "payload": {"success": False, "error": "Authentication failed"}
                    })
                    await websocket.close()
                    return
                    
            except asyncio.TimeoutError:
                logger.warning("WebSocket authentication timeout (no authentication frame received in 5s).")
                try:
                    await websocket.close()
                except Exception:
                    pass
                return
            except Exception as e:
                logger.error(f"WebSocket authentication error: {e}")
                try:
                    await websocket.close()
                except Exception:
                    pass
                return

        # 2. Main message loop for authenticated socket
        try:
            while True:
                data = await websocket.receive_text()
                # Parse in-flight messages (like acknowledgements, job updates)
                try:
                    msg = json.loads(data)
                    msg_type = msg.get("type")
                    payload = msg.get("payload") or {}

                    if msg_type == "job_update":
                        # Forward/broadcast the update to all connected agents
                        task_id = payload.get("task_id")
                        status = payload.get("status")
                        if task_id and status:
                            job_update = {"id": task_id, "status": status, **payload}
                            await ws_manager.broadcast({
                                "type": "event",
                                "payload": {
                                    "event": "job_pending" if status == "pending" else "job_updated",
                                    "job": job_update
                                }
                            }, job_update)
                except Exception:
                    pass
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
            
            # Set agent to offline on disconnect + ntfy notification
            if authenticated_instance_id and db_client:
                try:
                    db_client.table("agent_registry").update({
                        "status": "offline"
                    }).eq("instance_id", authenticated_instance_id).execute()
                    logger.info(f"Agent '{authenticated_instance_id}' disconnected, set to offline.")
                    
                    # NTFY addon: notify agent offline
                    try:
                        notify_agent_offline(authenticated_role or "unknown", authenticated_instance_id)
                    except Exception:
                        pass
                except Exception as db_err:
                    logger.warning(f"Failed to set agent offline: {db_err}")

    return app_server


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "::1", "localhost", "") or h.startswith("127.")


def _assert_safe_bind(host: str, config) -> None:
    """Refuse to expose the gateway on a network interface without auth.

    A non-loopback bind is reachable from the network. In zero-config mode (no
    MCO_LOCAL_TOKEN and no cloud database) the Local-Only auth fallback would
    grant admin to any caller, so we hard-stop and tell the operator to set a
    token. Localhost (the default) is unaffected — the zero-config experience
    stays intact.
    """
    if _is_loopback_host(host):
        return
    has_token = bool((config.get("MCO_LOCAL_TOKEN") or "").strip())
    url = config.get("SUPABASE_URL")
    key = config.get("SUPABASE_KEY")
    has_cloud_db = bool(url and key and url != "encrypted_in_secret_store")
    if has_token or has_cloud_db:
        return
    console.print(Panel.fit(
        f"[bold red]Refusing to bind to {host} without authentication.[/bold red]\n"
        f"A non-loopback bind is reachable from the network, and no MCO_LOCAL_TOKEN\n"
        f"(or cloud database) is configured — anyone who can reach this port would\n"
        f"get admin access.\n\n"
        f"[bold]Fix one of:[/bold]\n"
        f"  - Set MCO_LOCAL_TOKEN (run 'mco setup'), or\n"
        f"  - Bind to localhost: --host 127.0.0.1 (the default), or\n"
        f"  - Configure a cloud database (Supabase).",
        border_style="red"))
    raise typer.Exit(code=1)


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="The host to bind to."),
    port: int = typer.Option(18789, help="The port to bind to.")
):
    """Start the BitCadence FastAPI WebSocket/REST API Server."""
    from mco.logging_setup import configure_logging
    configure_logging()

    # Trigger dynamic decrypt/load, then refuse unsafe network exposure.
    config = get_config()
    _assert_safe_bind(host, config)

    console.print(Panel.fit(
        f"[bold green]Starting BitCadence Server[/bold green]\n"
        f"Host: http://{host}:{port}\n"
        f"Console: http://{host}:{port}/console\n"
        f"WebSocket: ws://{host}:{port}/ws/broadcast",
        border_style="green"
    ))

    app_server = create_app()

    # Pre-warm the (now memoized) data-plane client so the first request isn't slow.
    from mco.orchestrator.routes import get_db_client
    db = get_db_client()
    if db is not None:
        if getattr(db, "backend", "supabase") == "local":
            console.print("[dim]Embedded LocalStore ready (~/.mco/local.db).[/dim]")
        else:
            console.print("[dim]Supabase client pre-warmed.[/dim]")

    # Initialize ntfy webhook addon if env vars are present
    ntfy_cfg = get_ntfy_config()
    if ntfy_cfg.get("server") and ntfy_cfg.get("topic"):
        try:
            # Gather rich stats for the startup notification
            stats = {
                "host": host,
                "port": port,
                "pid": os.getpid(),
            }

            db_client = get_db_client()
            if db_client:
                try:
                    agents_res = db_client.table("agent_registry").select("status").execute()
                    stats["agent_count"] = len(agents_res.data or [])
                    stats["online_count"] = sum(1 for a in (agents_res.data or []) if a.get("status") == "online")

                    jobs_res = db_client.table("agent_jobs").select("id").eq("status", "pending").execute()
                    stats["pending_jobs"] = len(jobs_res.data or [])
                except Exception:
                    pass

            # Light process info to help detect leaks (mco.exe, node, git, etc.)
            try:
                import psutil
                stats["process_count"] = len(psutil.pids())
            except Exception:
                pass

            notify_gateway_startup(stats)
            console.print(f"[dim]NTFY notifier enabled -> {ntfy_cfg['server']}/{ntfy_cfg['topic']}[/dim]")
        except Exception as ntfy_err:
            console.print(f"[yellow]NTFY notifier init warning: {ntfy_err}[/yellow]")

    # Launch Uvicorn
    # Start background process snapshot reporter (helps catch mco.exe / codex / git / node leaks)
    def _periodic_process_snapshot():
        import threading
        import time
        ntfy_cfg = get_ntfy_config()
        if not (ntfy_cfg.get("server") and ntfy_cfg.get("topic")):
            return
        def _reporter():
            while True:
                try:
                    import psutil
                    snapshot = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "total_processes": len(psutil.pids()),
                        "mco_processes": len([p for p in psutil.process_iter(['name']) if 'mco' in (p.info['name'] or '').lower()]),
                        "node_processes": len([p for p in psutil.process_iter(['name']) if 'node' in (p.info['name'] or '').lower()]),
                        "git_processes": len([p for p in psutil.process_iter(['name']) if 'git' in (p.info['name'] or '').lower()]),
                    }
                    notify(
                        json.dumps(snapshot, indent=2),
                        title="BitCadence Process Snapshot",
                        priority=1,
                        tags=["mco", "process-snapshot", "leak-detection"],
                    )
                except Exception:
                    pass
                time.sleep(600)  # every 10 minutes
        threading.Thread(target=_reporter, daemon=True).start()

    _periodic_process_snapshot()

    # Background enterprise connector sync (opt-in via MCO_SYNC_INTERVAL seconds).
    # Pulls open ServiceNow incidents / Dynatrace problems onto the job board.
    def _periodic_connector_sync():
        import threading
        import time
        try:
            interval = float(config.get("MCO_SYNC_INTERVAL") or 0)
        except (TypeError, ValueError):
            interval = 0
        if interval <= 0:
            return
        from mco.connectors import build_connectors
        from mco.connectors.sync import sync_connector
        connectors = build_connectors()
        if not connectors:
            return
        console.print(f"[dim]Connector sync enabled every {interval:.0f}s: "
                      f"{', '.join(c.name for c in connectors)}[/dim]")

        def _syncer():
            from mco.orchestrator.routes import get_db_client
            while True:
                time.sleep(interval)
                db = get_db_client()
                if not db:
                    continue
                for conn in connectors:
                    try:
                        sync_connector(db, conn)
                    except Exception as sync_err:
                        logger.warning(f"Background sync failed for {conn.name}: {sync_err}")
        threading.Thread(target=_syncer, daemon=True).start()

    _periodic_connector_sync()

    uvicorn.run(app_server, host=host, port=port)


@app.command("start")
def start(
    host: str = typer.Option("127.0.0.1", help="The host to bind to."),
    port: int = typer.Option(18789, help="The port to bind to."),
):
    """Start the gateway in the background (the pair of 'mco stop').

    Unlike 'mco serve' (foreground, for terminals/systemd/Docker), this
    detaches: your terminal stays free, output goes to ~/.mco/logs/gateway.log,
    and 'mco stop' shuts it down.
    """
    import subprocess
    import time

    import psutil
    import requests

    # Refuse unsafe network exposure before backgrounding (visible feedback;
    # serve enforces it too).
    _assert_safe_bind(host, get_config())

    # Refuse to double-start: is something already listening on the port?
    for conn in psutil.net_connections(kind="tcp"):
        if conn.laddr.port == port and conn.status == "LISTEN" and conn.pid:
            console.print(f"[yellow][!] A gateway is already running on port {port} "
                          f"(PID {conn.pid}).[/yellow]")
            console.print(f"    Console: [cyan]http://{host}:{port}/console[/cyan]   "
                          f"Stop it with: [cyan]mco stop --port {port}[/cyan]")
            raise typer.Exit(code=1)

    from mco.service import gateway_log_path
    log_path = gateway_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8", errors="replace")

    cmd = [sys.executable, "-m", "mco.cli", "serve", "--host", host, "--port", str(port)]
    kwargs: dict = {"stdout": log_file, "stderr": subprocess.STDOUT,
                    "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        # Detach fully so closing this terminal doesn't kill the gateway.
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    console.print(f"->  Starting gateway in the background (PID {proc.pid})...")

    # Wait for /healthz so "started" means "answering", not "spawned".
    deadline = time.monotonic() + 20
    healthy = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break  # process died during startup
        try:
            if requests.get(f"http://{host}:{port}/healthz", timeout=1).ok:
                healthy = True
                break
        except Exception:
            time.sleep(0.5)

    if not healthy:
        console.print(f"[red][X] Gateway did not become healthy. "
                      f"See the log: {log_path}[/red]")
        raise typer.Exit(code=1)

    console.print(Panel.fit(
        f"[bold green]BitCadence is running[/bold green]\n"
        f"Console:   http://{host}:{port}/console\n"
        f"Dashboard: http://{host}:{port}/dashboard\n"
        f"Log:       {log_path}\n\n"
        f"Stop it any time with: [bold]mco stop[/bold]",
        border_style="green"
    ))


@app.command("restart")
def restart(
    host: str = typer.Option("127.0.0.1", help="The host to bind to."),
    port: int = typer.Option(18789, help="The port the gateway runs on."),
):
    """Restart the background gateway (stop if running, then start)."""
    import psutil
    running = any(c.laddr.port == port and c.status == "LISTEN" and c.pid
                  for c in psutil.net_connections(kind="tcp"))
    if running:
        stop(port=port, force=False)
    start(host=host, port=port)


service_app = typer.Typer(help="Run BitCadence processes as boot-persistent OS services.")
app.add_typer(service_app, name="service")

fleet_app = typer.Typer(help="Apply declarative per-worker service run modes.")
app.add_typer(fleet_app, name="fleet")

schedule_app = typer.Typer(help="Schedules and loops: what work gets created, and when.")
app.add_typer(schedule_app, name="schedule")


def _print_schedules_missing(path):
    from mco import scheduler
    console.print(f"[yellow]No schedules config found at {path}.[/yellow]")
    console.print("[dim]Create one with:[/dim] [bold]mco schedule init[/bold]")
    console.print(f"[dim]Or write it yourself:[/dim]\n{scheduler.sample_config()}")


def _load_schedules_or_exit(path=None):
    """Load schedules.yaml, printing the friendly guidance on failure."""
    from mco import scheduler
    path = path or scheduler.SCHEDULES_CONFIG_PATH
    try:
        return scheduler.load_config(path)
    except scheduler.ScheduleConfigMissing as exc:
        _print_schedules_missing(exc)
        raise typer.Exit(code=0)
    except scheduler.ScheduleConfigError as exc:
        console.print(f"[red][X] Invalid schedules config:[/red] {exc}")
        raise typer.Exit(code=1)


@schedule_app.command("init")
def schedule_init(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config."),
):
    """Write a starter ~/.mco/schedules.yaml."""
    from mco import scheduler
    path = scheduler.SCHEDULES_CONFIG_PATH
    if path.exists() and not force:
        console.print(f"[yellow]{path} already exists.[/yellow] Use --force to overwrite.")
        raise typer.Exit(code=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scheduler.sample_config(), encoding="utf-8")
    console.print(f"[green][OK][/green] Wrote {path}")
    console.print("[dim]Edit it, then run:[/dim] [bold]mco schedule list[/bold]")


@schedule_app.command("list")
def schedule_list():
    """Show every schedule and loop with its next fire time."""
    from mco import launcher as launcher_mod
    from mco import scheduler
    launchers, schedules = _load_schedules_or_exit()
    if not schedules:
        console.print("[yellow]No schedules or loops defined.[/yellow]")
        return

    states = launcher_mod.load_state()
    now = datetime.now(timezone.utc)
    table = Table(title="BitCadence Schedules")
    for column in ("Name", "Kind", "Trigger", "Launches", "Next run", "Runs", "Bound"):
        table.add_column(column)

    for name in sorted(schedules):
        schedule = schedules[name]
        state = states.get(name)
        next_run = scheduler.next_run_at(schedule, state, now)
        if not schedule.enabled:
            next_text = "[dim]disabled[/dim]"
        elif next_run is None:
            reason = scheduler.exhaustion_reason(schedule, state, now) or "finished"
            next_text = f"[dim]{reason}[/dim]"
        elif next_run <= now:
            next_text = "[green]due now[/green]"
        else:
            next_text = next_run.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        runs = str(state.iterations) if state else "0"
        table.add_row(
            name,
            "loop" if schedule.is_loop else "schedule",
            schedule.describe_trigger(),
            launchers[schedule.launcher].describe(),
            next_text,
            runs,
            schedule.describe_bound(),
        )
    console.print(table)


def _set_schedule_enabled(name: str, enabled: bool) -> None:
    """Flip `enabled` on one schedule/loop, preserving the rest of the file byte-for-byte.

    Deliberately a surgical text edit rather than parse-and-redump: PyYAML's
    safe_dump discards every comment, so a one-word toggle would silently
    destroy the explanatory config the user (or `schedule init`) wrote.
    """
    from mco import scheduler
    path = scheduler.SCHEDULES_CONFIG_PATH
    _, schedules = _load_schedules_or_exit(path)  # validate + friendly errors first
    if name not in schedules:
        console.print(f"[red][X] No schedule or loop named '{name}'.[/red]")
        if schedules:
            console.print(f"[dim]Defined:[/dim] {', '.join(sorted(schedules))}")
        raise typer.Exit(code=1)

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    section, entry_indent, entry_line = None, None, None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            section = stripped.rstrip(":") if stripped.endswith(":") else None
            continue
        if section in ("schedules", "loops") and stripped.rstrip(":") == name and stripped.endswith(":"):
            entry_indent, entry_line = indent, index
            break

    if entry_line is None:  # validated above, so this means an unusual layout
        console.print(f"[red][X] Could not locate '{name}' in {path}.[/red]")
        console.print("[dim]Edit the file directly to set 'enabled'.[/dim]")
        raise typer.Exit(code=1)

    value = "true" if enabled else "false"
    field_indent = entry_indent + 2
    # Walk the entry's own block looking for an existing `enabled:` to replace.
    cursor = entry_line + 1
    while cursor < len(lines):
        line = lines[cursor]
        if line.strip() and (len(line) - len(line.lstrip())) <= entry_indent:
            break  # left this entry's block
        if line.strip().startswith("enabled:"):
            lines[cursor] = f"{' ' * (len(line) - len(line.lstrip()))}enabled: {value}\n"
            break
        if line.strip():
            field_indent = len(line) - len(line.lstrip())
        cursor += 1
    else:
        cursor = len(lines)
    if cursor >= len(lines) or not lines[cursor].strip().startswith("enabled:"):
        lines.insert(entry_line + 1, f"{' ' * field_indent}enabled: {value}\n")

    path.write_text("".join(lines), encoding="utf-8")
    kind = "loop" if schedules[name].is_loop else "schedule"
    console.print(f"[green][OK][/green] {kind} '{name}' is now {'enabled' if enabled else 'disabled'}.")


@schedule_app.command("enable")
def schedule_enable(name: str = typer.Argument(..., help="Schedule or loop name.")):
    """Enable a schedule or loop (without hand-editing YAML)."""
    _set_schedule_enabled(name, True)


@schedule_app.command("disable")
def schedule_disable(name: str = typer.Argument(..., help="Schedule or loop name.")):
    """Disable a schedule or loop, leaving its definition and history intact."""
    _set_schedule_enabled(name, False)


@schedule_app.command("reset")
def schedule_reset(
    name: str = typer.Argument(..., help="Schedule or loop name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Clear a schedule's run history so a finished loop can run again.

    Completion is sticky by design - a loop that ended because its queue
    drained must not resurrect when the queue refills. This is the deliberate
    undo.
    """
    from mco import launcher as launcher_mod
    _, schedules = _load_schedules_or_exit()
    if name not in schedules:
        console.print(f"[red][X] No schedule or loop named '{name}'.[/red]")
        raise typer.Exit(code=1)

    states = launcher_mod.load_state()
    state = states.get(name)
    if not state or (not state.iterations and not state.exhausted_reason):
        console.print(f"[yellow]'{name}' has no run history to clear.[/yellow]")
        return
    if not yes:
        console.print(
            f"[yellow]'{name}' has run {state.iterations} time(s)"
            + (f" and is marked finished ({state.exhausted_reason})" if state.exhausted_reason else "")
            + ".[/yellow]"
        )
        if not typer.confirm("Clear its history so it can run again?"):
            console.print("[dim]Left unchanged.[/dim]")
            return
    states.pop(name, None)
    launcher_mod.save_state(states)
    console.print(f"[green][OK][/green] Cleared run history for '{name}'.")


@schedule_app.command("tick")
def schedule_tick(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would fire without creating jobs."),
):
    """Run one scheduler pass (what `mco schedule run` does on a timer).

    Use this to drive BitCadence from an existing cron/Task Scheduler entry
    instead of running the daemon.
    """
    from mco import launcher as launcher_mod
    _load_schedules_or_exit()  # validate + friendly errors before touching the gateway
    try:
        report = launcher_mod.tick(_gateway_client(), dry_run=dry_run)
    except Exception as exc:
        console.print(f"[red][X] Scheduler tick failed:[/red] {exc}")
        raise typer.Exit(code=1)
    _print_tick_report(report)


def _print_tick_report(report: list) -> None:
    if not report:
        console.print("[dim]Nothing due.[/dim]")
        return
    styles = {
        "fired": "green", "would-fire": "cyan",
        "skipped-overlap": "yellow", "error": "red",
    }
    for row in report:
        action = str(row["action"])
        colour = styles.get(action, "white")
        jobs = f" -> {', '.join(row['job_ids'])}" if row.get("job_ids") else ""
        console.print(f"[{colour}]{action:<16}[/{colour}] {row['schedule']}: {row['detail']}{jobs}")


@schedule_app.command("run")
def schedule_run(
    interval: float = typer.Option(30.0, "--interval", help="Seconds between scheduler passes."),
):
    """Run the scheduler in the foreground, ticking until interrupted."""
    from mco import launcher as launcher_mod
    _load_schedules_or_exit()
    console.print(f"[cyan]Scheduler running[/cyan] (tick every {interval:g}s). Ctrl+C to stop.")
    try:
        launcher_mod.run_forever(
            _gateway_client(), interval=interval, on_report=_print_tick_report
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Scheduler stopped.[/yellow]")


def _port_is_open(base_url: str, timeout: float = 2.0) -> bool:
    """Can we open a TCP connection to this base URL's host:port?"""
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@app.command("gui")
def open_gui(
    flow: bool = typer.Option(False, "--flow", help="Open Flow Control (the live job-dependency canvas)."),
    dashboard: bool = typer.Option(False, "--dashboard", help="Open the minimal dashboard instead of the full console."),
    print_only: bool = typer.Option(False, "--print", help="Print the URL instead of opening a browser."),
):
    """Open the BitCadence console in your browser.

    Until now the CLI only printed the URL and left you to copy it - this is
    the one-step version.
    """
    import webbrowser
    config = get_config()
    base = (config.get("MCO_GATEWAY_URL") or "http://127.0.0.1:18789").rstrip("/")
    page = "flow" if flow else ("dashboard" if dashboard else "console")
    url = f"{base}/{page}"

    # Say plainly when nothing is listening, rather than opening a dead tab.
    # A TCP probe rather than an HTTP GET: it needs no particular endpoint to
    # exist and no auth, so it can't be wrong about a healthy gateway.
    reachable = _port_is_open(base)

    if print_only:
        console.print(url)
        return
    if not reachable:
        console.print(f"[yellow]Nothing is listening at {base}.[/yellow]")
        console.print("[dim]Start it with:[/dim] [bold]mco start[/bold]  [dim](or `mco serve` in the foreground)[/dim]")
        console.print(f"[dim]The console will be at:[/dim] {url}")
        raise typer.Exit(code=1)
    if webbrowser.open(url):
        console.print(f"[green][OK][/green] Opened {url}")
    else:
        console.print(f"[yellow]Could not open a browser.[/yellow] Visit: {url}")


@app.command("launch")
def launch_now(
    name: str = typer.Argument(..., help="Launcher name from ~/.mco/schedules.yaml."),
    approve: bool = typer.Option(
        None, "--approve/--no-approve",
        help="Override the launcher's approval gate for this run.",
    ),
):
    """Fire a launcher right now, by name.

    Identical to what a schedule does at 3am - same code path, same governance -
    so testing a launcher by hand proves the scheduled run too.
    """
    from mco import launcher as launcher_mod
    launchers, _ = _load_schedules_or_exit()
    if name not in launchers:
        console.print(f"[red][X] Unknown launcher '{name}'.[/red]")
        if launchers:
            console.print(f"[dim]Defined:[/dim] {', '.join(sorted(launchers))}")
        raise typer.Exit(code=1)

    try:
        job_ids = launcher_mod.launch(
            launchers[name], _gateway_client(),
            trigger="manual", requires_approval_override=approve,
        )
    except Exception as exc:
        console.print(f"[red][X] Launch failed:[/red] {exc}")
        raise typer.Exit(code=1)
    launcher = launchers[name]
    if launcher.is_local:
        # app/url launchers start something here; they queue nothing.
        console.print(f"[green][OK][/green] Launched '{name}' - {launcher.describe()}")
        return
    plural = "s" if len(job_ids) != 1 else ""
    console.print(f"[green][OK][/green] Launched '{name}' -> {len(job_ids)} job{plural}")
    for job_id in job_ids:
        console.print(f"  [dim]{job_id}[/dim]")


def _print_fleet_missing(path):
    from mco import fleet
    console.print(f"[yellow]No fleet config found at {path}.[/yellow]")
    console.print("[dim]Create one like:[/dim]")
    console.print(fleet.sample_config())


@fleet_app.command("apply")
def fleet_apply():
    """Reconcile OS services to ~/.mco/fleet.toml."""
    from mco import fleet
    try:
        summaries = fleet.apply_fleet()
    except fleet.FleetConfigMissing as exc:
        _print_fleet_missing(exc)
        raise typer.Exit(code=0)
    except fleet.FleetConfigError as exc:
        console.print(f"[red][X] Fleet apply failed:[/red] {exc}")
        raise typer.Exit(code=1)
    if not summaries:
        console.print("[green][OK][/green] Fleet config is empty; no worker services declared.")
        return
    for summary in summaries:
        console.print(summary)


@fleet_app.command("status")
def fleet_status():
    """Show configured workers and their installed/running state."""
    from mco import fleet
    try:
        rows = fleet.fleet_status()
    except fleet.FleetConfigMissing as exc:
        _print_fleet_missing(exc)
        raise typer.Exit(code=0)
    except fleet.FleetConfigError as exc:
        console.print(f"[red][X] Fleet status failed:[/red] {exc}")
        raise typer.Exit(code=1)
    if not rows:
        console.print("[yellow]Fleet config has no workers.[/yellow]")
        return
    table = Table(title="BitCadence Fleet")
    for column in ("Worker", "Role", "Instance", "Mode", "Installed", "Running", "Service"):
        table.add_column(column)
    for row in rows:
        installed = "yes" if row["installed"] else "no"
        running = "yes" if row["running"] else "no"
        table.add_row(
            str(row["worker"]),
            str(row["role"]),
            str(row["instance"] or ""),
            str(row["mode"]),
            installed,
            running,
            str(row["service"]),
        )
    console.print(table)


@fleet_app.command("set")
def fleet_set(
    worker: str = typer.Argument(..., help="Worker table name under [workers]."),
    assignment: str = typer.Argument(..., help="KEY=VALUE assignment, for example mode=waker."),
):
    """Update one worker field in ~/.mco/fleet.toml."""
    from mco import fleet
    try:
        message = fleet.set_worker_value(worker, assignment)
    except fleet.FleetConfigMissing as exc:
        _print_fleet_missing(exc)
        raise typer.Exit(code=1)
    except (fleet.FleetConfigError, ValueError) as exc:
        console.print(f"[red][X] Fleet set failed:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green][OK][/green] {message}")


@service_app.command("install-scheduler")
def service_install_scheduler(
    interval: float = typer.Option(30.0, "--interval", help="Seconds between scheduler passes."),
):
    """Install the schedule/loop scheduler as a boot-persistent OS service.

    Without this the scheduler only runs while a terminal is open - and a
    scheduler that quietly died on reboot is the failure nobody notices until
    the nightly job hasn't run for a week.
    """
    from mco import service
    ok, message = service.install_scheduler(interval=interval)
    if ok:
        console.print(f"[green][OK][/green] {message}")
        console.print("[dim]Check it with:[/dim] mco service status BitCadence-scheduler")
    else:
        console.print(f"[red][X] {message}[/red]")
        raise typer.Exit(code=1)


@service_app.command("install")
def service_install(
    host: str = typer.Option("127.0.0.1", help="Host the service binds to."),
    port: int = typer.Option(18789, help="Port the service binds to."),
):
    """Install the gateway as an OS service that starts on boot/login."""
    from mco import service
    console.print(f"[cyan]Installing via {service.backend_name()}...[/cyan]")
    ok_flag, detail = service.install(host, port)
    if ok_flag:
        console.print(f"[green][OK][/green] {detail}")
        console.print(f"[dim]Console: http://{host}:{port}/console   "
                      f"Remove with: mco service uninstall[/dim]")
    else:
        console.print(f"[red][X] Service install failed:[/red] {detail}")
        raise typer.Exit(code=1)


@service_app.command("install-waker")
def service_install_waker(
    role: str = typer.Argument(..., help="Agent role this waker should watch."),
    exec_command: Optional[str] = typer.Argument(None, help="Shell command to run when jobs are pending."),
    exec_option: Optional[str] = typer.Option(None, "--exec", help="Shell command to run when jobs are pending."),
    instance: Optional[str] = typer.Option(None, "--instance", help="Optional agent instance ID this waker should watch."),
    min_interval: float = typer.Option(10.0, "--min-interval", help="Minimum seconds between spawn starts."),
):
    """Install a self-restarting waker service for one role/instance."""
    from mco import service
    resolved_exec = exec_command or exec_option
    if not resolved_exec:
        console.print("[red][X] Waker service install failed:[/red] missing exec command")
        raise typer.Exit(code=1)
    console.print(f"[cyan]Installing waker via {service.backend_name()}...[/cyan]")
    ok_flag, detail = service.install_waker(role, resolved_exec, instance=instance, min_interval=min_interval)
    if ok_flag:
        console.print(f"[green][OK][/green] {detail}")
    else:
        console.print(f"[red][X] Waker service install failed:[/red] {detail}")
        raise typer.Exit(code=1)


@service_app.command("uninstall")
def service_uninstall(
    selector: Optional[str] = typer.Argument(None, help="Service name or waker role. Defaults to the gateway."),
):
    """Remove the boot-persistent service (does not stop a running gateway)."""
    from mco import service
    ok_flag, detail = service.uninstall(selector or service.SERVICE_NAME)
    if ok_flag:
        console.print(f"[green][OK][/green] {detail}")
    else:
        console.print(f"[red][X] Service uninstall failed:[/red] {detail}")
        raise typer.Exit(code=1)


@service_app.command("status")
def service_status(
    selector: Optional[str] = typer.Argument(None, help="Optional service name or waker role to inspect."),
):
    """Show installed BitCadence services, or one selected service."""
    from mco import service
    if selector is None:
        states = service.status(None)
        if not states:
            console.print(f"[yellow]No BitCadence services found via {service.backend_name()}.[/yellow]")
            raise typer.Exit(code=0)
        for state in states:
            _print_service_status(service.backend_name(), state)
        return
    state = service.status(selector)
    _print_service_status(service.backend_name(), state)


def _print_service_status(backend: str, state: dict[str, object]):
    installed = bool(state.get("installed"))
    running = bool(state.get("running"))
    color = "green" if installed and running else "yellow" if installed else "red"
    console.print(Panel.fit(
        f"[bold]{backend}[/bold]\n"
        f"Name:      {state.get('name', 'BitCadence-gateway')}\n"
        f"Installed: [{color}]{'yes' if installed else 'no'}[/{color}]\n"
        f"Running:   [{color}]{'yes' if running else 'no'}[/{color}]\n"
        f"Last exit: {state.get('last_exit', 'unknown')}",
        border_style=color,
    ))


@service_app.command("restart")
def service_restart(
    selector: Optional[str] = typer.Argument(None, help="Service name or waker role. Defaults to the gateway."),
):
    """Restart the boot-persistent gateway service."""
    from mco import service
    ok_flag, detail = service.restart(selector or service.SERVICE_NAME)
    if ok_flag:
        console.print(f"[green][OK][/green] {detail}")
    else:
        console.print(f"[red][X] Service restart failed:[/red] {detail}")
        raise typer.Exit(code=1)


@service_app.command("logs")
def service_logs(
    selector: Optional[str] = typer.Argument(None, help="Service name or waker role. Defaults to the gateway."),
    lines: int = typer.Option(80, "--lines", "-n", help="Number of recent log lines to print."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Keep streaming new log lines."),
):
    """Tail a service log file."""
    from mco import service
    log_path = service.log_path(selector or service.SERVICE_NAME)
    if not log_path.exists():
        console.print(f"[yellow]No service log found at {log_path}.[/yellow]")
        raise typer.Exit(code=0)
    console.print(f"[dim]Log: {log_path}[/dim]")
    try:
        for line in service.tail_log(selector or service.SERVICE_NAME, lines=lines, follow=follow):
            console.print(line)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


@app.command("stop")
def stop(
    port: int = typer.Option(18789, help="Port the gateway is running on."),
    force: bool = typer.Option(False, "--force", "-f", help="Send SIGKILL immediately instead of graceful SIGTERM."),
):
    """Stop a running BitCadence gateway (by port)."""
    import signal
    import time

    try:
        import psutil
    except ImportError:
        console.print("[red][ERROR] psutil is required for mco stop. Run: pip install psutil[/red]")
        raise typer.Exit(code=1)

    targets: list[psutil.Process] = []
    for conn in psutil.net_connections(kind="tcp"):
        if conn.laddr.port == port and conn.status == "LISTEN" and conn.pid:
            try:
                targets.append(psutil.Process(conn.pid))
            except psutil.NoSuchProcess:
                pass

    if not targets:
        console.print(f"[yellow]No BitCadence process found listening on port {port}.[/yellow]")
        raise typer.Exit(code=0)

    for proc in targets:
        try:
            name = proc.name()
            pid = proc.pid
            console.print(f"[cyan]->  Stopping '{name}' (PID {pid}) on port {port}...[/cyan]")
            if force:
                proc.kill()
                console.print(f"[bold green][OK] PID {pid} killed.[/bold green]")
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                    console.print(f"[bold green][OK] PID {pid} stopped cleanly.[/bold green]")
                except psutil.TimeoutExpired:
                    proc.kill()
                    console.print(f"[yellow][OK] PID {pid} did not exit in 5 s — sent SIGKILL.[/yellow]")
        except psutil.NoSuchProcess:
            console.print(f"[dim]PID {proc.pid} already gone.[/dim]")
        except psutil.AccessDenied:
            console.print(f"[red][ERROR] Access denied for PID {proc.pid}. Try running as administrator.[/red]")
            raise typer.Exit(code=1)


# ─────────────────────────────────────────────────────────────────────────────
# 2b. MCP Server (for IDE/agent GUI integration)
# ─────────────────────────────────────────────────────────────────────────────
@app.command("mcp")
def mcp_serve():
    """Run the MCO dropbox as an MCP stdio server (for Claude/Codex/Antigravity)."""
    # stdio is the MCP transport channel: do NOT write to stdout in this command.
    from mco.mcp_server import run
    run()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Background Listener Daemon
# ─────────────────────────────────────────────────────────────────────────────
@app.command("listen")
def listen(
    role: str = typer.Option("codex", help="Agent execution role (e.g. codex, script)."),
    instance: str = typer.Option("default_agent", help="Unique ID of this client listener instance."),
    config_file: str = typer.Option("agent_config.json", help="Path to local worker config file.")
):
    """Spawn the background daemon client that polls and executes Job Board tasks."""
    console.print(Panel.fit(
        f"[bold blue]Spawning BitCadence Background Daemon[/bold blue]\n"
        f"Role: [green]{role}[/green]\n"
        f"Instance ID: [green]{instance}[/green]",
        border_style="blue"
    ))

    # Bootstrap client listener config overrides
    os.environ["AGENT_ROLE"] = role
    os.environ["AGENT_INSTANCE_ID"] = instance

    # Wire real CLI executors so leased jobs actually run (instead of mock-completing).
    try:
        from mco.orchestrator.executors import register_default_executors
        registered = register_default_executors()
        console.print(f"[dim]Registered executors for roles: {', '.join(registered)}[/dim]")
    except Exception as e:
        console.print(f"[yellow][WARN] Could not register default executors: {e}[/yellow]")

    # Connector roles: a listener for role "servicenow"/"dynatrace" executes
    # platform actions (input_payload.action) instead of running a CLI tool.
    try:
        from mco.connectors import get_connector, make_connector_executor
        from mco.orchestrator.listener import register_executor
        conn = get_connector(role)
        if conn:
            register_executor(role, make_connector_executor(conn))
            console.print(f"[dim]Role '{role}' wired to the {conn.name} connector "
                          f"(actions: {', '.join(conn.actions())}).[/dim]")
    except Exception as e:
        console.print(f"[yellow][WARN] Connector executor not wired: {e}[/yellow]")

    try:
        listener = AgentListener(config_path=config_file)
        asyncio.run(listener.start())
    except KeyboardInterrupt:
        console.print("[yellow]Background listener shut down by user.[/yellow]")
    except Exception as e:
        console.print(f"[red][ERROR] Critical error in listener daemon: {e}[/red]")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Status and Diagnostics
# ─────────────────────────────────────────────────────────────────────────────
@app.command("status")
def status(
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Show all resolved configuration keys, including unrelated process environment.",
    ),
):
    """Print BitCadence health check and diagnostics."""
    config = get_config()
    store = get_secret_store()

    console.print("[bold cyan]=== BitCadence Status Diagnostics ===[/bold cyan]\n")

    # 1. Store state
    store_init = store.is_initialized()
    store_unlocked = store.is_unlocked
    
    store_status_str = "[green]Active / Unlocked[/green]" if store_unlocked else (
        "[yellow]Active / Locked[/yellow]" if store_init else "[white]Not Configured (Plaintext Mode)[/white]"
    )
    
    console.print(f"Encryption Secure Store Path: [bold]{store._path}[/bold]")
    console.print(f"Secure Store Status: {store_status_str}\n")

    # 2. Profile configuration
    profile = config.get("MCO_PROFILE") or "Not Configured"
    console.print(f"Active Environment Profile: [bold green]{profile}[/bold green]\n")

    # 3. Settings table
    table = Table(title="Resolved Configuration Properties", show_header=True, header_style="bold magenta")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")
    
    masked = config.get_masked_config()
    if not show_all:
        allowed_prefixes = ("MCO_", "OPERATOR_", "SUPABASE_")
        masked = {k: v for k, v in masked.items() if k.startswith(allowed_prefixes)}
    for k, v in masked.items():
        table.add_row(k, v)

    console.print(table)


@app.command("upgrade")
def upgrade(
    apply: bool = typer.Option(False, "--apply", help="Actually apply (default: dry-run / show pending)."),
):
    """Apply schema migrations to the configured backend.

    LocalStore needs none. Postgres/Supabase auto-applies when DATABASE_URL
    and a psycopg driver are present; otherwise a combined script is written
    for the Supabase SQL editor.
    """
    from mco import migrations_runner as mig

    all_migs = [n for n, _ in mig.discover()]
    console.print(f"[bold cyan]=== BitCadence Upgrade ===[/bold cyan]")
    console.print(f"Migrations found: {len(all_migs)}\n")

    kind = mig.backend_kind()
    if kind == "none":
        console.print("[yellow]No database configured.[/yellow] Run 'mco setup' first.")
        raise typer.Exit(code=1)
    if kind == "local":
        console.print("[green][OK][/green] Backend is the embedded LocalStore - "
                      "no migrations needed (JSON rows pick up new fields automatically).")
        return

    # Postgres / Supabase
    database_url = (get_config().get("DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if database_url and apply:
        try:
            result = mig.apply_postgres(database_url)
        except Exception as e:
            console.print(f"[red][X] Migration failed:[/red] {e}")
            raise typer.Exit(code=1)
        if result["applied"]:
            for n in result["applied"]:
                console.print(f"[green][OK][/green] applied {n}")
            console.print(f"\n[green]Applied {len(result['applied'])} migration(s) "
                          f"via {result['driver']}.[/green]")
        else:
            console.print("[green][OK][/green] Already up to date - nothing to apply.")
        return

    # No direct connection (or dry-run): emit the pending script.
    _, pending = mig.write_combined_script()
    if not pending:
        console.print("[green][OK][/green] No pending migrations detected.")
        return
    out = Path.home() / ".mco" / "pending_migrations.sql"
    console.print(f"[yellow]{len(pending)} migration(s) pending:[/yellow]")
    for n in pending:
        console.print(f"  - {n}")
    console.print()
    if database_url:
        console.print("Re-run with [bold]--apply[/bold] to apply via DATABASE_URL.")
    else:
        console.print(f"Combined script written to: [bold]{out}[/bold]")
        console.print("[dim]Apply it in the Supabase SQL editor, or set DATABASE_URL "
                      "and run 'mco upgrade --apply'.[/dim]")


@app.command("doctor")
def doctor(
    port: int = typer.Option(18789, help="Gateway port to probe."),
):
    """Diagnose an install end to end: Python, config, secret store, database,
    gateway, agents, vendor CLIs. Exit code 1 if anything is broken."""
    import shutil

    warnings_n = 0
    errors_n = 0

    def ok(label, detail=""):
        console.print(f"[green][OK][/green] {label}" + (f" - {detail}" if detail else ""))

    def warn(label, remedy=""):
        nonlocal warnings_n
        warnings_n += 1
        console.print(f"[yellow][!][/yellow]  {label}")
        if remedy:
            console.print(f"     [dim]{remedy}[/dim]")

    def bad(label, remedy=""):
        nonlocal errors_n
        errors_n += 1
        console.print(f"[red][X][/red]  {label}")
        if remedy:
            console.print(f"     [dim]{remedy}[/dim]")

    console.print("[bold cyan]=== BitCadence Doctor ===[/bold cyan]\n")

    # 1. Python
    v = sys.version_info
    if v >= (3, 9):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        bad(f"Python {v.major}.{v.minor} is too old (3.9+ required)",
            "Re-run the installer; it finds or installs a supported Python.")

    # 2. Config home
    from mco.config import resolve_env_path
    env_path = resolve_env_path()
    config = get_config()
    if env_path.is_file():
        token = (config.get("MCO_LOCAL_TOKEN") or "").strip()
        ok(f"Config: {env_path}",
           "MCO_LOCAL_TOKEN set" if token else "no MCO_LOCAL_TOKEN")
        if not token:
            warn("No MCO_LOCAL_TOKEN - the console cannot authenticate in Local-Only mode",
                 "Run the installer or 'mco setup' to generate one.")
    else:
        warn(f"No config file at {env_path}", "Run 'mco setup' to create one.")

    # 3. Secret store
    store = get_secret_store()
    if not store.is_initialized():
        ok("Secret store: off (plaintext .env mode)")
    elif store.is_unlocked or store.auto_unlock():
        ok("Secret store: unlocked")
    else:
        warn(f"Secret store at {store._path} is locked (no working key)",
             "Run 'mco setup --menu' -> security to unlock or recreate it. "
             "Config still loads from .env meanwhile.")

    # 4. Edition
    from mco.editions import current_edition
    ok(f"Edition: {current_edition()}", "see 'mco edition' for the feature matrix")

    # 5. Database
    from mco.orchestrator.routes import get_db_client, decorate_presence, get_offline_after_seconds
    db = get_db_client()
    if db is None:
        warn("No database (MCO_DISABLE_LOCAL_DB is set?)",
             "Unset MCO_DISABLE_LOCAL_DB or configure Supabase via 'mco setup'.")
    else:
        backend = getattr(db, "backend", "supabase")
        try:
            res = db.table("agent_registry").select("*").execute()
            agents = res.data or []
            if backend == "local":
                ok("Database: embedded LocalStore (~/.mco/local.db)")
            else:
                ok("Database: Supabase reachable")
            threshold = get_offline_after_seconds()
            online = sum(1 for a in agents
                         if decorate_presence(dict(a), threshold)["effective_status"] == "online")
            ok(f"Agents: {len(agents)} registered, {online} online",
               f"threshold {threshold}s")
            # Schema currency: the Drumline dedup migration (content_hash).
            # LocalStore is schema-less JSON, so only Postgres/Supabase can lag.
            if backend != "local":
                try:
                    db.table("agent_context").select("content_hash").limit(1).execute()
                    ok("Migrations: agent_context.content_hash present (dedup active)")
                except Exception:
                    warn("Drumline dedup migration not applied (agent_context.content_hash missing)",
                         "Run 'mco upgrade --apply' (or apply docs/migrations/2026-07_drumline_dedup.sql).")
        except Exception as e:
            bad(f"Database query failed ({backend}): {e}",
                "Check SUPABASE_URL/SUPABASE_KEY, or file permissions on ~/.mco/local.db.")

    # 6. Gateway
    try:
        import requests
        r = requests.get(f"http://127.0.0.1:{port}/healthz", timeout=3)
        if r.ok:
            paused = (r.json() or {}).get("paused")
            ok(f"Gateway: answering on port {port}" + (" (PAUSED by kill switch)" if paused else ""))
            if paused:
                warn("Kill switch is active - no new jobs or leases",
                     "Turn it off in the Control Panel -> Settings -> Governance.")
        else:
            warn(f"Gateway on port {port} answered HTTP {r.status_code}")
    except Exception:
        warn(f"Gateway not running on port {port}",
             "Start it with 'mco start' (background) or 'mco serve' (foreground).")

    # 7. Vendor CLIs (informational - only matters for the roles you use)
    found = []
    for cli_name in ("claude", "codex", "gemini", "git", "docker"):
        found.append(f"{cli_name} [{'green]yes[/green' if shutil.which(cli_name) else 'dim]no[/dim'}]")
    console.print("     Vendor CLIs: " + "  ".join(found))

    # 8. Notifications
    ntfy_cfg = get_ntfy_config()
    if ntfy_cfg.get("server") and ntfy_cfg.get("topic"):
        ok(f"Notifications: ntfy -> {ntfy_cfg['server']}/{ntfy_cfg['topic']}")
    else:
        console.print("     [dim]Notifications: off (set NTFY_TOPIC to enable push alerts)[/dim]")

    console.print()
    if errors_n:
        console.print(f"[red]Result: {errors_n} error(s), {warnings_n} warning(s).[/red]")
        raise typer.Exit(code=1)
    if warnings_n:
        console.print(f"[yellow]Result: {warnings_n} warning(s), no errors.[/yellow]")
    else:
        console.print("[green]Result: everything checks out.[/green]")


@app.command("register")
def register_agent(
    name: str = typer.Option(..., "--name", help="Unique name (instance ID) of the agent."),
    role: str = typer.Option(..., "--role", help="Target role of the agent (e.g. antigravity, codex)."),
    org: str = typer.Option("default", "--org", help="Tenant org the agent belongs to (multi-tenant installs)."),
    scope: list[str] = typer.Option(
        None, "--scope",
        help="Explicit token scope (repeatable, e.g. --scope jobs:read --scope context:read). "
             "Omit for role-derived defaults; 'admin' grants everything."
    ),
):
    """Register a new client agent, generating a secure access token."""
    console.print(f"[bold cyan]Registering new MCO agent...[/bold cyan]")

    from mco.orchestrator.admin_routes import allowed_orgs
    from mco.orchestrator.auth import KNOWN_SCOPES, normalize_scopes
    from mco.orchestrator.routes import get_db_client
    db_client = get_db_client()
    if not db_client:
        console.print("[red][ERROR] Database not configured. Please run 'mco setup' first.[/red]")
        raise typer.Exit(code=1)

    # Orgs are isolation boundaries, minted deliberately - never by typo.
    if org and org not in allowed_orgs():
        console.print(f"[red][ERROR] Org '{org}' is not configured.[/red]")
        console.print(f"Allowed orgs: {', '.join(allowed_orgs())}")
        console.print("Add it first: Control Panel -> Settings -> Tenancy, or set "
                      "MCO_ORGS=acme,beta in ~/.mco/.env")
        raise typer.Exit(code=1)

    scopes = normalize_scopes(scope or [])
    unknown = [s for s in scopes if s not in KNOWN_SCOPES]
    if unknown:
        console.print(f"[red][ERROR] Unknown scope(s): {', '.join(unknown)}[/red]")
        console.print(f"Valid scopes: {', '.join(sorted(KNOWN_SCOPES))}")
        raise typer.Exit(code=1)

    token = "mco_tok_" + secrets.token_hex(24)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    try:
        data = {
            "instance_id": name,
            "role": role,
            "status": "offline",
            "auth_token_hash": token_hash
        }
        if org and org != "default":
            data["org_id"] = org
        if scopes:
            data["scopes"] = scopes
        try:
            res = db_client.table("agent_registry").upsert(data).execute()
        except Exception as first_err:
            if scopes:
                # Pre-migration database without the scopes column: register
                # without explicit scopes (role-derived defaults still apply).
                data.pop("scopes", None)
                res = db_client.table("agent_registry").upsert(data).execute()
                console.print(
                    "[yellow][!] Database has no 'scopes' column yet - registered with "
                    "role-derived defaults. Apply docs/migrations/2026-06_scoped_tokens.sql "
                    "to use explicit scopes.[/yellow]"
                )
            else:
                raise first_err
        if res.data:
            scope_line = f"Scopes: [cyan]{', '.join(scopes)}[/cyan]\n" if scopes else \
                "Scopes: [dim]role-derived defaults[/dim]\n"
            console.print(Panel.fit(
                f"[bold green][OK] Agent '{name}' registered successfully![/bold green]\n\n"
                f"Role: [cyan]{role}[/cyan]\n"
                f"{scope_line}"
                f"Status: [yellow]offline[/yellow]\n\n"
                f"[bold yellow]Save this Access Token securely. It will not be shown again:[/bold yellow]\n"
                f"[bold white]{token}[/bold white]",
                border_style="green"
            ))
        else:
            console.print("[red][ERROR] Database failed to return data on upsert.[/red]")
    except Exception as e:
        console.print(f"[red][ERROR] Failed to register agent in database: {e}[/red]")


@app.command("edition")
def show_edition():
    """Show the active edition (community/team/enterprise) and feature matrix."""
    from rich.table import Table
    from mco.editions import edition_summary

    summary = edition_summary()
    console.print(
        f"\n[bold cyan]BitCadence edition:[/bold cyan] [bold white]{summary['edition']}[/bold white] "
        f"[dim]({summary['source']}; set MCO_EDITION to pin)[/dim]\n"
    )
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Feature")
    table.add_column("Minimum edition")
    table.add_column("Available")
    for feature, info in summary["features"].items():
        mark = "[green]yes[/green]" if info["available"] else "[red]no[/red]"
        table.add_row(feature, info["minimum_edition"], mark)
    console.print(table)


@app.command("agents")
def list_agents():
    """List all registered agents and their current online presence status."""
    console.print("[bold cyan]=== BitCadence Registered Agents ===[/bold cyan]\n")
    
    from mco.orchestrator.routes import get_db_client
    db_client = get_db_client()
    if not db_client:
        console.print("[red][ERROR] Database not configured. Please run 'mco setup' first.[/red]")
        raise typer.Exit(code=1)
        
    try:
        res = db_client.table("agent_registry").select("*").order("instance_id").execute()
        agents = res.data or []
        
        if not agents:
            console.print("[yellow]No agents registered. Use 'mco register' to onboard an agent.[/yellow]")
            return
            
        from mco.orchestrator.routes import decorate_presence, get_offline_after_seconds

        threshold = get_offline_after_seconds()
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Agent ID (Instance ID)", style="cyan")
        table.add_column("Role", style="green")
        table.add_column("Org", style="white")
        table.add_column("Status", style="bold")
        table.add_column("Last Seen", style="white")

        def _ago(secs):
            if secs is None:
                return "never"
            if secs < 90:
                return f"{secs}s ago"
            if secs < 5400:
                return f"{round(secs / 60)}m ago"
            if secs < 129600:
                return f"{round(secs / 3600)}h ago"
            return f"{round(secs / 86400)}d ago"

        for agent in agents:
            agent = decorate_presence(dict(agent), threshold)
            status = agent.get("effective_status", "offline")
            if status == "online":
                status_style = "[green]online[/green]"
            elif status == "disabled":
                status_style = "[yellow]disabled[/yellow]"
            else:
                status_style = "[red]offline[/red]"

            table.add_row(
                agent.get("instance_id", ""),
                agent.get("role", ""),
                agent.get("org_id") or "default",
                status_style,
                _ago(agent.get("last_seen_seconds")),
            )

        console.print(table)
        console.print(f"[dim]Online means heard from within the last {threshold}s "
                      f"(MCO_AGENT_OFFLINE_AFTER).[/dim]")
    except Exception as e:
        console.print(f"[red][ERROR] Failed to query agent registry: {e}[/red]")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Governance: workflows, audit trail, approval gates
# ─────────────────────────────────────────────────────────────────────────────
def _gateway_client():
    """GatewayClient resolved from the full config stack, not just os.environ.

    Reads MCO_GATEWAY_URL / MCO_AGENT_TOKEN / AGENT_ROLE / AGENT_INSTANCE_ID
    from get_config() (which layers .env + the AES-256-GCM secret store over
    the OS environment), so `mco workflow|approve|sync|...` work from any shell
    once the token is in .env or the vault - no per-shell `set` required.

    Local-Only zero-config: the gateway seeds its operator agent from
    MCO_LOCAL_TOKEN, so when no explicit MCO_AGENT_TOKEN is configured we fall
    back to it. Without this, the operator commands (send/approve/workflow/
    sync/audit) return 401 on a fresh Local-Only install whose .env only has
    MCO_LOCAL_TOKEN - the exact papercut a non-technical user hits first.
    """
    from mco.orchestrator.client import GatewayClient
    config = get_config()
    token = config.get("MCO_AGENT_TOKEN") or config.get("MCO_LOCAL_TOKEN") or None
    return GatewayClient(
        base_url=config.get("MCO_GATEWAY_URL") or None,
        token=token,
        role=config.get("AGENT_ROLE") or None,
        instance_id=config.get("AGENT_INSTANCE_ID") or None,
    )


@app.command("send")
def send_job(
    to_role: str = typer.Argument(..., help="Target role's dropbox (e.g. codex, claude)."),
    title: str = typer.Option(..., "--title", "-t", help="Short job title."),
    message: str = typer.Option("", "--message", "-m", help="Instructions (defaults to the title)."),
    instance: str = typer.Option("", "--instance", help="Address one specific instance instead of the whole role."),
    approve: bool = typer.Option(False, "--approve", help="Pause at the human approval gate before execution."),
    retries: int = typer.Option(0, "--retries", help="Retry budget on failure."),
    escalate: str = typer.Option("", "--escalate", help="Role to escalate to after retries are exhausted."),
):
    """Drop a job into an agent's dropbox from the terminal."""
    try:
        res = _gateway_client().send(
            to_role=to_role,
            title=title,
            instructions=message or title,
            to_instance=instance or None,
            requires_approval=approve,
            max_retries=retries,
            escalate_to_role=escalate or None,
        )
        job = (res or {}).get("job") or {}
        if res.get("success") and job.get("id"):
            console.print(f"[green][OK][/green] Job [bold]{job['id']}[/bold] -> {to_role}"
                          f"{' / ' + instance if instance else ''} "
                          f"(status: {job.get('status')})")
            if job.get("status") == "needs_approval":
                console.print(f"[dim]Approve it with: mco approve {job['id']}[/dim]")
        else:
            console.print(f"[red][ERROR] Send failed: {res}[/red]")
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red][ERROR] {e}[/red]")
        console.print("[dim]Is the gateway running? Check with: mco doctor[/dim]")
        raise typer.Exit(code=1)


@app.command("workflow")
def run_workflow(
    file: str = typer.Argument(..., help="Path to a workflow YAML file."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and print the plan without submitting."),
):
    """Submit a declarative YAML workflow (DAG of jobs) to the Job Board."""
    from mco.orchestrator.workflows import load_workflow, topo_order, submit_workflow, WorkflowError

    try:
        workflow = load_workflow(file, allow_path=True)
    except WorkflowError as e:
        console.print(f"[red][ERROR] Invalid workflow: {e}[/red]")
        raise typer.Exit(code=1)

    ordered = topo_order(workflow["steps"])
    console.print(f"[bold cyan]Workflow:[/bold cyan] {workflow['name']} ({len(ordered)} steps)")
    for step in ordered:
        gates = []
        if step.get("requires_approval"):
            gates.append("approval-gated")
        if step.get("max_retries"):
            gates.append(f"retries={step['max_retries']}")
        if step.get("escalate_to_role"):
            gates.append(f"escalates->{step['escalate_to_role']}")
        deps = ", ".join(step.get("depends_on") or []) or "-"
        console.print(f"  - {step['id']} [green]({step['role']})[/green] deps: {deps} {' '.join(gates)}")

    if dry_run:
        console.print("[yellow]Dry run: nothing submitted.[/yellow]")
        return

    try:
        job_ids = submit_workflow(_gateway_client(), workflow)
    except WorkflowError as e:
        console.print(f"[red][ERROR] {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red][ERROR] Failed to submit workflow: {e}[/red]")
        raise typer.Exit(code=1)

    console.print("[bold green][OK] Workflow submitted.[/bold green]")
    for step_id, job_id in job_ids.items():
        console.print(f"  {step_id} -> {job_id}")


def _audit_verify(job_id: str) -> None:
    """Walk a job's audit hash chain locally and print the verdict.

    Verification reads the data plane directly (LocalStore or Supabase) rather
    than going through the gateway, because integrity is a property of the
    stored chain itself, not of any single API response.
    """
    from mco.orchestrator.audit import verify_chain
    from mco.orchestrator.routes import get_db_client

    db_client = get_db_client()
    if db_client is None:
        console.print("[red][ERROR] No data plane configured; cannot verify audit chain.[/red]")
        raise typer.Exit(code=1)

    try:
        report = verify_chain(db_client, job_id)
    except Exception as e:
        console.print(f"[red][ERROR] Failed to verify audit chain: {e}[/red]")
        raise typer.Exit(code=1)

    signed = " (HMAC-signed)" if report.get("signed") else ""
    if report["ok"]:
        console.print(
            f"[bold green][OK][/bold green] Audit chain for job {job_id} is intact: "
            f"{report['count']} event(s) verified{signed}."
        )
        return

    console.print(
        f"[bold red][TAMPERED][/bold red] Audit chain for job {job_id} is BROKEN "
        f"at event #{report['broken_at']} of {report['count']}{signed}."
    )
    if report.get("reason"):
        console.print(f"[red]  {report['reason']}[/red]")
    raise typer.Exit(code=1)


@app.command("audit")
def audit_trail(
    job_id: str = typer.Argument(..., help="Job ID to inspect."),
    verify: bool = typer.Option(
        False, "--verify",
        help="Walk the hash chain and report OK or the first broken link.",
    ),
):
    """Print a job's immutable audit trail (oldest event first)."""
    if verify:
        _audit_verify(job_id)
        return

    try:
        events = _gateway_client().events(job_id)
    except Exception as e:
        console.print(f"[red][ERROR] Failed to fetch audit trail: {e}[/red]")
        raise typer.Exit(code=1)

    if not events:
        console.print("[yellow]No audit events found for this job.[/yellow]")
        return

    table = Table(title=f"Audit Trail: {job_id}", show_header=True, header_style="bold magenta")
    table.add_column("Time", style="white")
    table.add_column("Event", style="cyan")
    table.add_column("Actor", style="green")
    table.add_column("Detail", style="dim")
    for ev in events:
        actor = ev.get("actor_id") or "-"
        if ev.get("actor_role"):
            actor = f"{actor} ({ev['actor_role']})"
        table.add_row(
            str(ev.get("created_at", "")),
            str(ev.get("event", "")),
            actor,
            json.dumps(ev.get("detail") or {}),
        )
    console.print(table)


@app.command("approve")
def approve(job_id: str = typer.Argument(..., help="Job ID awaiting approval.")):
    """Approve a job paused at the human-in-the-loop gate."""
    try:
        res = _gateway_client().approve(job_id)
        console.print(f"[bold green][OK] Job {job_id} approved -> {res['job']['status']}[/bold green]")
    except Exception as e:
        console.print(f"[red][ERROR] Approval failed: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("reject")
def reject(
    job_id: str = typer.Argument(..., help="Job ID awaiting approval."),
    reason: str = typer.Option("", "--reason", help="Why the job was rejected."),
):
    """Reject a job paused at the human-in-the-loop gate (terminal)."""
    try:
        res = _gateway_client().reject(job_id, reason)
        console.print(f"[bold yellow][OK] Job {job_id} rejected -> {res['job']['status']}[/bold yellow]")
    except Exception as e:
        console.print(f"[red][ERROR] Rejection failed: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("retry")
def retry(job_id: str = typer.Argument(..., help="Failed/rejected job ID to re-queue.")):
    """Re-queue a failed or rejected job back to pending (approver-role token)."""
    try:
        res = _gateway_client().retry(job_id)
        console.print(f"[bold green][OK] Job {job_id} re-queued -> {res['job']['status']}[/bold green]")
    except Exception as e:
        console.print(f"[red][ERROR] Retry failed: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("cancel")
def cancel(
    job_id: str = typer.Argument(..., help="Non-terminal job ID to call off."),
    reason: str = typer.Option("", "--reason", help="Why the job was cancelled."),
):
    """Call off a job that hasn't finished yet (approver-role token)."""
    try:
        res = _gateway_client().cancel(job_id, reason)
        console.print(f"[bold yellow][OK] Job {job_id} cancelled -> {res['job']['status']}[/bold yellow]")
    except Exception as e:
        console.print(f"[red][ERROR] Cancel failed: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("archive")
def archive(job_id: str = typer.Argument(..., help="Terminal job ID to hide from the default board view.")):
    """Archive a completed/failed/rejected/cancelled job (reversible)."""
    try:
        res = _gateway_client().archive(job_id)
        console.print(f"[bold green][OK] Job {job_id} archived[/bold green]")
    except Exception as e:
        console.print(f"[red][ERROR] Archive failed: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("unarchive")
def unarchive(job_id: str = typer.Argument(..., help="Archived job ID to restore to the default board view.")):
    """Undo archive."""
    try:
        res = _gateway_client().unarchive(job_id)
        console.print(f"[bold green][OK] Job {job_id} unarchived[/bold green]")
    except Exception as e:
        console.print(f"[red][ERROR] Unarchive failed: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("duplicates")
def duplicates(job_id: str = typer.Argument(..., help="Job ID to check for look-alike or linked jobs.")):
    """List other jobs that look like the same work as this one."""
    try:
        dups = _gateway_client().duplicates(job_id)
    except Exception as e:
        console.print(f"[red][ERROR] Duplicate check failed: {e}[/red]")
        raise typer.Exit(code=1)
    if not dups:
        console.print("[dim]No look-alike or linked jobs found.[/dim]")
        return
    table = Table(title=f"Possible duplicates of {job_id}", show_header=True, header_style="bold magenta")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Status", style="green")
    table.add_column("Relation", style="dim")
    for d in dups:
        table.add_row(str(d.get("id")), str(d.get("title")), str(d.get("status")), str(d.get("relation")))
    console.print(table)


@app.command("reassign")
def reassign(
    job_id: str = typer.Argument(..., help="Failed/rejected/cancelled job ID to redo elsewhere."),
    to_role: str = typer.Option(..., "--to-role", help="Target agent role for the new job."),
    to_instance: str = typer.Option("", "--to-instance", help="Target agent instance (optional)."),
    instructions: str = typer.Option("", "--instructions", help="Override instructions (default: reuse the original)."),
):
    """Clone a failed job onto a new target, link both rows, and archive the
    old one (approver-role token)."""
    try:
        res = _gateway_client().reassign(job_id, to_role, target_agent_id=to_instance or None,
                                          instructions=instructions or None)
        new_id = res["job"]["id"]
        console.print(f"[bold green][OK] Job {job_id} reassigned -> new job {new_id} ({to_role})[/bold green]")
    except Exception as e:
        console.print(f"[red][ERROR] Reassign failed: {e}[/red]")
        raise typer.Exit(code=1)


# ─────────────────────────────────────────────────────────────────────────────
# 5b. Drumline & admin parity
# ─────────────────────────────────────────────────────────────────────────────
@app.command("recall")
def recall_context(
    query: str = typer.Argument("", help="Free-text search over shared context (blank = most recent)."),
    tags: str = typer.Option("", "--tags", help="Comma-separated tag filter."),
    limit: int = typer.Option(5, "--limit", help="Max entries to return."),
    role: str = typer.Option("", "--role", help="Bias results toward this role (role-affine scoring)."),
):
    """Recall the most relevant Drumline shared-context entries."""
    try:
        client = _gateway_client()
        if role:
            client.role = role
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        entries = client.recall(query=query, tags=tag_list or None, limit=limit)
    except Exception as e:
        console.print(f"[red][ERROR] Recall failed: {e}[/red]")
        console.print("[dim]Is the gateway running? Check with: mco doctor[/dim]")
        raise typer.Exit(code=1)

    if not entries:
        console.print("[yellow]No matching context found.[/yellow]")
        return

    table = Table(title="Drumline Recall", show_header=True, header_style="bold magenta")
    table.add_column("Kind", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Content", style="dim")
    table.add_column("Tags", style="green")
    table.add_column("By", style="white")
    for e in entries:
        content = (e.get("content") or "").strip().replace("\n", " ")
        if len(content) > 120:
            content = content[:120] + "…"
        table.add_row(
            str(e.get("kind", "")),
            str(e.get("title", "")),
            content,
            ", ".join(e.get("tags") or []),
            str(e.get("created_by") or "-"),
        )
    console.print(table)


@app.command("remember")
def remember_context(
    title: str = typer.Argument(..., help="Short title for this memory entry."),
    content: str = typer.Argument(..., help="The content to remember."),
    kind: str = typer.Option("fact", "--kind", help="Entry kind: fact, decision, lesson, handoff, or artifact."),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags."),
):
    """Append an entry to the Drumline shared context."""
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        res = _gateway_client().remember(title=title, content=content, kind=kind, tags=tag_list or None)
        entry = (res or {}).get("entry") or {}
        console.print(f"[green][OK][/green] Remembered -> {entry.get('id', '?')}")
    except Exception as e:
        console.print(f"[red][ERROR] Remember failed: {e}[/red]")
        console.print("[dim]Is the gateway running? Check with: mco doctor[/dim]")
        raise typer.Exit(code=1)


@app.command("settings")
def settings_cmd(
    key: str = typer.Argument(None, help="Setting key to read or write (blank = list all)."),
    value: str = typer.Argument(None, help="New value for the key (omit with --unset to clear)."),
    unset: bool = typer.Option(False, "--unset", help="Clear the key back to its default."),
):
    """View or change gateway settings (the Control Panel, from the terminal)."""
    try:
        client = _gateway_client()
        if key is None:
            data = client.settings()
            groups = (data or {}).get("groups") or {}
            for group, rows in groups.items():
                table = Table(title=f"Settings: {group}", show_header=True, header_style="bold magenta")
                table.add_column("Key", style="cyan")
                table.add_column("Type", style="white")
                table.add_column("Label", style="dim")
                table.add_column("Value", style="green")
                for row in rows:
                    if row.get("type") == "secret":
                        display = "•••• (set)" if row.get("value") else "-"
                    else:
                        display = str(row.get("value")) if row.get("value") not in (None, "") else "-"
                    table.add_row(row.get("key", ""), row.get("type", ""), row.get("label", ""), display)
                console.print(table)
            return

        if unset:
            res = client.settings_put({key: None})
            console.print(f"[bold yellow][OK] {key} cleared.[/bold yellow]" if res.get("success")
                          else f"[red][ERROR] Failed to clear {key}: {res}[/red]")
            return

        if value is None:
            data = client.settings()
            groups = (data or {}).get("groups") or {}
            found = None
            for rows in groups.values():
                for row in rows:
                    if row.get("key") == key:
                        found = row
                        break
                if found:
                    break
            if not found:
                console.print(f"[red][ERROR] Unknown setting: {key}[/red]")
                raise typer.Exit(code=1)
            if found.get("type") == "secret":
                display = "•••• (set)" if found.get("value") else "-"
            else:
                display = str(found.get("value")) if found.get("value") not in (None, "") else "-"
            console.print(f"[cyan]{key}[/cyan] = {display}")
            return

        coerced: Any = value
        if value.strip().lower() in ("true", "1", "on"):
            coerced = True
        elif value.strip().lower() in ("false", "0", "off"):
            coerced = False
        res = client.settings_put({key: coerced})
        if res.get("success"):
            console.print(f"[green][OK][/green] {key} updated.")
        else:
            console.print(f"[red][ERROR] Update failed: {res}[/red]")
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red][ERROR] {e}[/red]")
        console.print("[dim]Is the gateway running? Check with: mco doctor[/dim]")
        raise typer.Exit(code=1)


@app.command("orgs")
def list_orgs_cmd():
    """List orgs available for registration (Control Panel tenancy dropdown, from the terminal)."""
    try:
        data = _gateway_client().orgs()
    except Exception as e:
        console.print(f"[red][ERROR] Failed to query orgs: {e}[/red]")
        console.print("[dim]Is the gateway running? Check with: mco doctor[/dim]")
        raise typer.Exit(code=1)

    orgs = data.get("orgs") or []
    in_use = set(data.get("in_use") or [])
    table = Table(title="Orgs", show_header=True, header_style="bold magenta")
    table.add_column("Org", style="cyan")
    table.add_column("In use", style="green")
    for org in orgs:
        table.add_row(org, "yes" if org in in_use else "")
    console.print(table)
    console.print(f"[dim]Host operator: {data.get('host_operator', False)}[/dim]")


@app.command("reset-token")
def reset_token_cmd(
    instance_id: str = typer.Argument(..., help="Instance ID of the agent to rotate."),
    save: bool = typer.Option(
        True, "--save/--no-save",
        help="Write the new token to ~/.mco/tokens/<instance>.token so this machine's waker picks it up.",
    ),
):
    """Rotate an agent's access token. The old token stops working immediately."""
    from mco.waker import agent_token_path

    try:
        res = _gateway_client().reset_token(instance_id)
    except Exception as e:
        console.print(f"[red][ERROR] Token reset failed: {e}[/red]")
        console.print("[dim]Is the gateway running? Check with: mco doctor[/dim]")
        raise typer.Exit(code=1)

    token = res.get("token", "")
    saved_note = ""
    if save and token:
        # Rotation that doesn't update the consumer is how a fleet ends up
        # "online" but unable to authenticate - so write it by default.
        path = agent_token_path(instance_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token, encoding="utf-8")
            try:
                os.chmod(path, 0o600)  # best-effort; a no-op on some filesystems
            except OSError:
                pass
            saved_note = f"\n\n[dim]Saved to {path}[/dim]"
        except OSError as e:
            saved_note = f"\n\n[yellow]Could not write {path}: {e}[/yellow]"

    console.print(Panel.fit(
        f"[bold green][OK] Token rotated for '{instance_id}'.[/bold green]\n\n"
        f"[bold yellow]Save this Access Token securely. It will not be shown again:[/bold yellow]\n"
        f"[bold white]{token}[/bold white]"
        + saved_note,
        border_style="green"
    ))
    if save and token:
        console.print(
            "[dim]Note: any MCP client config that embeds this token "
            "(e.g. ~/.codex/config.toml) must be updated separately.[/dim]"
        )


@app.command("deregister")
def deregister_agent(
    instance_id: str = typer.Argument(..., help="Instance ID of the agent to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Remove an agent registration. Its token stops working immediately."""
    if not yes:
        confirmed = typer.confirm(f"Deregister agent '{instance_id}'? This cannot be undone.")
        if not confirmed:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)
    try:
        _gateway_client().delete_agent(instance_id)
        console.print(f"[bold yellow][OK] Agent {instance_id} deregistered — its token no longer works.[/bold yellow]")
    except Exception as e:
        console.print(f"[red][ERROR] Deregister failed: {e}[/red]")
        console.print("[dim]Is the gateway running? Check with: mco doctor[/dim]")
        raise typer.Exit(code=1)


@app.command("watch")
def watch(
    raw: bool = typer.Option(False, "--raw", help="Print raw event JSON instead of formatted lines."),
):
    """Live-tail job events from the gateway's broadcast feed (Ctrl-C to stop)."""
    config = get_config()
    base = (config.get("MCO_GATEWAY_URL") or "http://127.0.0.1:18789").rstrip("/")
    if base.startswith("https://"):
        ws_url = "wss://" + base[len("https://"):] + "/ws/broadcast"
    elif base.startswith("http://"):
        ws_url = "ws://" + base[len("http://"):] + "/ws/broadcast"
    else:
        ws_url = base + "/ws/broadcast"
    token = config.get("MCO_AGENT_TOKEN") or config.get("MCO_LOCAL_TOKEN") or ""

    async def _tail():
        import websockets
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    # Token-only auth: the gateway resolves identity from the
                    # token hash (or MCO_LOCAL_TOKEN in Local-Only mode).
                    await ws.send(json.dumps({"type": "authenticate",
                                              "payload": {"token": token}}))
                    console.print(f"[green]Watching {ws_url}[/green] [dim](Ctrl-C to stop)[/dim]")
                    backoff = 1.0
                    async for frame in ws:
                        try:
                            msg = json.loads(frame)
                        except (TypeError, ValueError):
                            continue
                        mtype = msg.get("type")
                        payload = msg.get("payload") or {}
                        if mtype == "authenticated":
                            if payload.get("success") is False:
                                console.print("[red][ERROR] WebSocket auth failed — "
                                              "check MCO_AGENT_TOKEN / MCO_LOCAL_TOKEN.[/red]")
                                return
                            continue
                        if raw:
                            console.print_json(json.dumps(msg))
                            continue
                        if mtype == "event":
                            job = payload.get("job") or {}
                            ts = datetime.now().strftime("%H:%M:%S")
                            console.print(
                                f"[dim]{ts}[/dim] [cyan]{payload.get('event', '?')}[/cyan] "
                                f"[bold]{job.get('title', '')}[/bold] "
                                f"[dim]({job.get('status', '')} · {str(job.get('id', ''))[:8]}"
                                f" → {job.get('target_agent_role', '') or '-'})[/dim]")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                console.print(f"[yellow]Disconnected: {e} — retrying in {int(backoff)}s…[/yellow]")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    try:
        asyncio.run(_tail())
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Enterprise integrations
# ─────────────────────────────────────────────────────────────────────────────
def _tail_channel_matches(job: dict, role: str, instance_id: str) -> bool:
    if role:
        target_role = str(job.get("target_agent_role") or "")
        if target_role.lower() != role.lower():
            return False
    if instance_id:
        target_id = job.get("target_agent_id")
        if target_id and target_id != instance_id:
            return False
    return True


def _tail_channel_label(role: str, instance_id: str) -> str:
    if role and instance_id:
        return f"{role}/{instance_id}"
    if role:
        return f"{role}/*"
    if instance_id:
        return f"*/{instance_id}"
    return "all channels"


def _tail_event_line(event: str, job: dict) -> str:
    ts = datetime.now().strftime("%H:%M:%S")
    title = job.get("title") or str(job.get("id") or "")
    source = job.get("source_agent_role") or "-"
    target_role = job.get("target_agent_role") or "-"
    target_id = job.get("target_agent_id") or "*"
    return (
        f"[dim]{ts}[/dim] [cyan]{event or '?'}[/cyan] "
        f"[bold]{title}[/bold] [dim]{source} -> {target_role}/{target_id}[/dim]"
    )


@app.command("tail")
def tail(
    role: Optional[str] = typer.Option(None, "--role", help="Agent role mailbox to tail."),
    instance: Optional[str] = typer.Option(None, "--instance", help="Agent instance mailbox to tail."),
    gateway: Optional[str] = typer.Option(None, "--gateway", help="Gateway HTTP URL."),
    token: Optional[str] = typer.Option(None, "--token", help="Agent or operator bearer token."),
):
    """Live-tail a filtered mailbox feed from the gateway broadcast socket."""
    from mco.waker import websocket_url_from_gateway

    config = get_config()
    resolved_role = role or config.get("AGENT_ROLE") or os.environ.get("AGENT_ROLE") or ""
    resolved_instance = instance or config.get("AGENT_INSTANCE_ID") or os.environ.get("AGENT_INSTANCE_ID") or ""
    resolved_gateway = gateway or config.get("MCO_GATEWAY_URL") or os.environ.get("MCO_GATEWAY_URL") or None
    resolved_token = (
        token
        or config.get("MCO_AGENT_TOKEN")
        or os.environ.get("MCO_AGENT_TOKEN")
        or config.get("MCO_LOCAL_TOKEN")
        or os.environ.get("MCO_LOCAL_TOKEN")
        or ""
    )
    ws_url = websocket_url_from_gateway(resolved_gateway)
    channel = _tail_channel_label(resolved_role, resolved_instance)

    async def _tail():
        import websockets

        backoff = 1.0
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    await ws.send(json.dumps({
                        "type": "authenticate",
                        "payload": {"token": resolved_token},
                    }))
                    console.print(
                        f"[green]Tailing {channel} at {ws_url}[/green] "
                        f"[dim](Ctrl-C to stop)[/dim]"
                    )
                    backoff = 1.0
                    async for frame in ws:
                        try:
                            msg = json.loads(frame)
                        except (TypeError, ValueError):
                            continue
                        mtype = msg.get("type")
                        payload = msg.get("payload") or {}
                        if mtype == "authenticated":
                            if payload.get("success") is False:
                                console.print("[red][ERROR] WebSocket auth failed - "
                                              "check MCO_AGENT_TOKEN / MCO_LOCAL_TOKEN.[/red]")
                                return
                            continue
                        if mtype != "event":
                            continue
                        job = payload.get("job") or {}
                        if not _tail_channel_matches(job, resolved_role, resolved_instance):
                            continue
                        console.print(_tail_event_line(payload.get("event", "?"), job))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                console.print(f"[yellow]Disconnected: {e} - retrying in {int(backoff)}s...[/yellow]")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    try:
        asyncio.run(_tail())
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


@app.command("wake")
def wake(
    exec_command: str = typer.Option(..., "--exec", help="Shell command to run when this worker has pending jobs."),
    role: Optional[str] = typer.Option(None, "--role", help="Agent role to watch for."),
    instance: Optional[str] = typer.Option(None, "--instance", help="Agent instance ID to watch for."),
    gateway: Optional[str] = typer.Option(None, "--gateway", help="Gateway HTTP URL."),
    token: Optional[str] = typer.Option(None, "--token", help="Agent bearer token."),
    min_interval: float = typer.Option(10.0, "--min-interval", help="Minimum seconds between spawn starts."),
):
    """Wake a local worker command when this agent's inbox has pending jobs."""
    from mco.waker import Waker, WakerAuthError, WakerTokenError, resolve_agent_token

    config = get_config()
    resolved_role = role or config.get("AGENT_ROLE") or os.environ.get("AGENT_ROLE") or ""
    resolved_instance = instance or config.get("AGENT_INSTANCE_ID") or os.environ.get("AGENT_INSTANCE_ID") or ""
    resolved_gateway = gateway or config.get("MCO_GATEWAY_URL") or os.environ.get("MCO_GATEWAY_URL") or None
    # Resolves per-instance so several agents can run on one machine; fails
    # loudly and by name rather than silently borrowing the operator token.
    try:
        resolved_token = resolve_agent_token(resolved_instance, explicit=token, config=config)
    except WakerTokenError as e:
        console.print(f"[red][ERROR][/red] {e}")
        raise typer.Exit(code=1)

    waker = Waker(
        exec_command=exec_command,
        role=resolved_role,
        instance_id=resolved_instance,
        gateway_url=resolved_gateway,
        token=resolved_token,
        min_interval=min_interval,
    )
    console.print(f"[green]Waking {resolved_role}/{resolved_instance} from {waker.ws_url}[/green] "
                  "[dim](Ctrl-C to stop)[/dim]")
    try:
        asyncio.run(waker.run_forever())
    except WakerAuthError as e:
        console.print(f"[red][ERROR] {e}[/red]")
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


@app.command("connectors")
def list_connectors_cmd():
    """List configured enterprise connectors and their health (via the gateway)."""
    try:
        rows = _gateway_client().integrations()
    except Exception as e:
        console.print(f"[red][ERROR] Failed to query integrations: {e}[/red]")
        raise typer.Exit(code=1)

    if not rows:
        console.print("[yellow]No connectors configured. Set SERVICENOW_INSTANCE_URL / "
                      "DYNATRACE_BASE_URL (plus credentials) and restart the gateway.[/yellow]")
        return

    table = Table(title="Enterprise Connectors", show_header=True, header_style="bold magenta")
    table.add_column("Connector", style="cyan")
    table.add_column("Health", style="bold")
    table.add_column("Actions", style="dim")
    for row in rows:
        health = row.get("health") or {}
        status = "[green]ok[/green]" if health.get("ok") else f"[red]down[/red] {health.get('detail', '')}"
        table.add_row(row.get("name", ""), status, ", ".join(row.get("actions") or []))
    console.print(table)


@app.command("sync")
def sync_cmd(connector: str = typer.Argument(..., help="Connector name (servicenow, dynatrace).")):
    """Pull open platform objects (incidents/problems) onto the job board."""
    try:
        summary = _gateway_client().sync_connector(connector)
    except Exception as e:
        console.print(f"[red][ERROR] Sync failed: {e}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[bold green][OK] {connector} sync:[/bold green] "
                  f"pulled={summary.get('pulled')} created={len(summary.get('created') or [])} "
                  f"skipped={summary.get('skipped')} (already on the board)")


@app.command("platform")
def platform_action(
    connector: str = typer.Argument(..., help="Connector name (servicenow, dynatrace)."),
    action: str = typer.Argument(..., help="Action name (see 'mco connectors')."),
    params: str = typer.Option("{}", "--params", help="JSON parameters for the action."),
):
    """Run a connector control action directly (requires an approver-role token)."""
    try:
        parsed = json.loads(params)
    except json.JSONDecodeError as e:
        console.print(f"[red][ERROR] --params is not valid JSON: {e}[/red]")
        raise typer.Exit(code=1)
    try:
        res = _gateway_client().platform_action(connector, action, parsed)
        console.print(f"[bold green][OK][/bold green] {json.dumps(res.get('result'), indent=2)}")
    except Exception as e:
        console.print(f"[red][ERROR] Action failed: {e}[/red]")
        raise typer.Exit(code=1)


def main():
    app()


if __name__ == "__main__":
    main()
