"""Thin local FastAPI transport for the agent daemon control surface."""

from __future__ import annotations

import hashlib
import os
import secrets
from typing import Callable, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
import uvicorn

from .config import FleetConfigManager, FleetWatcher
from .logs import LogAggregator
from .supervisor import Supervisor
from .tokens import AgentdTokenStore, current_user_sid


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT_BASE = 18790


def user_scoped_control_port(identity: str | None = None) -> int:
    """Return a stable per-user loopback port to avoid cross-session collisions."""
    if identity is None:
        if os.name == "nt":
            identity = current_user_sid()
        else:
            identity = f"uid:{os.getuid()}"
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    return CONTROL_PORT_BASE + int.from_bytes(digest[:2], "big") % 1000


CONTROL_PORT = user_scoped_control_port()


def create_control_app(
    supervisor: Supervisor,
    manager: FleetConfigManager,
    watcher: FleetWatcher,
    logs: LogAggregator,
    *,
    token_provider: Callable[[], str] | None = None,
    log_token_provider: Callable[[], str] | None = None,
    token_store: AgentdTokenStore | None = None,
    gateway_reachable: Callable[[], bool] | None = None,
) -> FastAPI:
    """Create the v1 transport; all behavior stays in injected collaborators."""
    token_store = token_store or AgentdTokenStore()
    token_provider = token_provider or token_store.control_token
    log_token_provider = log_token_provider or token_store.logs_token
    gateway_reachable = gateway_reachable or (lambda: False)

    def _require(expected: str, authorization: str | None) -> None:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.casefold() != "bearer" or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="invalid bearer token")

    def require_control_token(authorization: str | None = Header(default=None)) -> None:
        _require(token_provider(), authorization)

    def require_log_token(authorization: str | None = Header(default=None)) -> None:
        _require(log_token_provider(), authorization)

    app = FastAPI(title="BitCadence agentd control API", version="1")
    app.state.agentd_supervisor = supervisor
    protected = [Depends(require_control_token)]
    log_protected = [Depends(require_log_token)]

    @app.get("/v1/status", dependencies=protected)
    def get_status() -> dict[str, object]:
        return {
            "daemon": {"state": "running", "config_error": watcher.last_error},
            "workers": supervisor.status(),
            "gateway_reachable": gateway_reachable(),
        }

    @app.get("/v1/workers", dependencies=protected)
    def get_workers() -> list[dict[str, object]]:
        return cast(list[dict[str, object]], supervisor.status())

    def invoke(name: str, action: Callable[[str], None]) -> dict[str, object]:
        try:
            action(name)
            return cast(dict[str, object], supervisor.status(name))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/workers/{name}/start", dependencies=protected)
    def start_worker(name: str) -> dict[str, object]:
        return invoke(name, supervisor.start)

    @app.post("/v1/workers/{name}/stop", dependencies=protected)
    def stop_worker(name: str) -> dict[str, object]:
        return invoke(name, supervisor.stop)

    @app.post("/v1/workers/{name}/restart", dependencies=protected)
    def restart_worker(name: str) -> dict[str, object]:
        return invoke(name, supervisor.restart)

    @app.post("/v1/workers/{name}/reset", dependencies=protected)
    def reset_worker(name: str) -> dict[str, object]:
        return invoke(name, supervisor.reset)

    @app.get("/v1/config", dependencies=protected)
    def get_fleet_config() -> dict[str, object]:
        return manager.snapshot()

    @app.put("/v1/config", dependencies=protected)
    async def put_fleet_config(request: Request) -> dict[str, object]:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
            content = payload.get("content") if isinstance(payload, dict) else None
        else:
            content = (await request.body()).decode("utf-8")
        if not isinstance(content, str):
            raise HTTPException(status_code=422, detail="expected TOML or JSON {content: ...}")
        try:
            watcher.replace(content)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return manager.snapshot()

    @app.post("/v1/reload", dependencies=protected)
    def reload_fleet_config() -> dict[str, object]:
        watcher.poll(force=True)
        if watcher.last_error:
            raise HTTPException(status_code=422, detail=watcher.last_error)
        return manager.snapshot()

    @app.get("/v1/logs", dependencies=log_protected)
    def get_logs(
        worker: str | None = Query(default=None),
        tail: int = Query(default=100, ge=0, le=10_000),
    ) -> dict[str, object]:
        return {"worker": worker, "lines": logs.tail(worker, tail)}

    return app


def serve_control_app(app: FastAPI, *, host: str = CONTROL_HOST, port: int = CONTROL_PORT) -> None:
    """Serve the control transport on the ADR-defined loopback endpoint."""
    if host != CONTROL_HOST:
        raise ValueError(f"agentd must bind to {CONTROL_HOST}, not {host}")
    app.state.agentd_supervisor.startup()
    uvicorn.run(app, host=host, port=port)
