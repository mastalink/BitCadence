"""Thin local FastAPI transport for the agent daemon control surface."""

from __future__ import annotations

import secrets
from typing import Callable, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
import uvicorn

from mco.config import get_config

from .config import FleetConfigManager, FleetWatcher
from .logs import LogAggregator
from .supervisor import Supervisor


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 18790


def create_control_app(
    supervisor: Supervisor,
    manager: FleetConfigManager,
    watcher: FleetWatcher,
    logs: LogAggregator,
    *,
    token_provider: Callable[[], str] | None = None,
    gateway_reachable: Callable[[], bool] | None = None,
) -> FastAPI:
    """Create the v1 transport; all behavior stays in injected collaborators."""
    token_provider = token_provider or (
        lambda: str(get_config().get("MCO_LOCAL_TOKEN") or "").strip()
    )
    gateway_reachable = gateway_reachable or (lambda: False)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        expected = token_provider()
        if not expected:
            return
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.casefold() != "bearer" or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="invalid bearer token")

    app = FastAPI(title="BitCadence agentd control API", version="1")
    protected = [Depends(require_token)]

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

    @app.get("/v1/logs", dependencies=protected)
    def get_logs(
        worker: str | None = Query(default=None),
        tail: int = Query(default=100, ge=0, le=10_000),
    ) -> dict[str, object]:
        return {"worker": worker, "lines": logs.tail(worker, tail)}

    return app


def serve_control_app(app: FastAPI, *, host: str = CONTROL_HOST, port: int = CONTROL_PORT) -> None:
    """Serve the control transport on the ADR-defined loopback endpoint."""
    uvicorn.run(app, host=host, port=port)
