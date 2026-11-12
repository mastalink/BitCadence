"""Thin local FastAPI transport for the agent daemon control surface.

Privilege (ADR 0002 R7): this API can change an ``exec`` and restart it, which
is code execution as the daemon's user. It therefore never accepts the gateway
token; it has its own owner-only secret, a *separate* one for reading logs
(worker output may contain secrets), and it binds to loopback only. Loopback
is not an authorization boundary - any local process can reach the port - so
the token is what carries the weight.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from typing import Any, Callable, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
import uvicorn

from .config import FleetConfigManager, FleetWatcher, GenerationConflict
from .logs import LogAggregator
from .supervisor import STATE_SCHEMA_VERSION, Supervisor
from .tokens import AgentdTokenStore, current_user_sid


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT_BASE = 18790
# Versions a client can negotiate against (R8): gateway, daemon, console and
# tray are upgraded at different times, so skew is expected, not an accident.
CONTROL_API_VERSION = 1
SUPPORTED_API_VERSIONS = (1,)
API_VERSION_HEADER = "x-bitcadence-agentd-api"


def user_scoped_control_port(identity: str | None = None) -> int:
    """Return a stable per-user loopback port to avoid cross-session collisions."""
    if identity is None:
        if os.name == "nt":
            identity = current_user_sid()
        else:
            identity = f"uid:{os.getuid()}"
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    return CONTROL_PORT_BASE + int.from_bytes(digest[:2], "big") % 1000


_control_port: int | None = None


def control_port() -> int:
    """This user's control port, resolved once on first use."""
    global _control_port
    if _control_port is None:
        _control_port = user_scoped_control_port()
    return _control_port


def __getattr__(name: str) -> Any:
    # ``CONTROL_PORT`` stays importable, but resolving the user's identity (a
    # subprocess on Windows) no longer happens just because something imported
    # this package.
    if name == "CONTROL_PORT":
        return control_port()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def version_document() -> dict[str, object]:
    from mco import __version__
    from mco.fleet import ALLOWED_FIELDS, VALID_MODES

    return {
        "package": __version__,
        "api": CONTROL_API_VERSION,
        "api_supported": list(SUPPORTED_API_VERSIONS),
        "state_schema": STATE_SCHEMA_VERSION,
        "fleet_fields": sorted(ALLOWED_FIELDS),
        "fleet_modes": sorted(VALID_MODES),
    }


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
        if scheme.casefold() != "bearer" or not secrets.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="invalid bearer token")

    def _negotiate(requested: str | None) -> None:
        if requested is None:
            return
        try:
            version = int(requested)
        except ValueError:
            version = -1
        if version not in SUPPORTED_API_VERSIONS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unsupported control API version",
                    "requested": requested,
                    "supported": list(SUPPORTED_API_VERSIONS),
                },
            )

    def require_control_token(
        authorization: str | None = Header(default=None),
        x_bitcadence_agentd_api: str | None = Header(default=None),
    ) -> None:
        _require(token_provider(), authorization)
        _negotiate(x_bitcadence_agentd_api)

    def require_log_token(authorization: str | None = Header(default=None)) -> None:
        _require(log_token_provider(), authorization)

    app = FastAPI(title="BitCadence agentd control API", version="1")
    app.state.agentd_supervisor = supervisor
    protected = [Depends(require_control_token)]
    log_protected = [Depends(require_log_token)]

    def daemon_document() -> dict[str, object]:
        document: dict[str, object] = {
            "state": "running",
            "config_error": watcher.last_error,
            "config_generation": getattr(watcher, "generation", None),
            "logs": getattr(logs, "health", dict)(),
        }
        document.update(getattr(supervisor, "daemon_status", dict)())
        if document.get("draining"):
            document["state"] = "draining"
        document["version"] = version_document()
        return document

    @app.get("/v1/status", dependencies=protected)
    def get_status() -> dict[str, object]:
        return {
            "daemon": daemon_document(),
            "workers": supervisor.status(),
            "gateway_reachable": gateway_reachable(),
        }

    @app.get("/v1/version", dependencies=protected)
    def get_version() -> dict[str, object]:
        return version_document()

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

    @app.post("/v1/drain", dependencies=protected)
    def drain() -> dict[str, object]:
        """Upgrade step: stop spawning and stop every child, against a deadline."""
        supervisor.drain()
        return daemon_document()

    @app.post("/v1/resume", dependencies=protected)
    def resume() -> dict[str, object]:
        supervisor.resume()
        return daemon_document()

    @app.get("/v1/config", dependencies=protected)
    def get_fleet_config(response: Response) -> dict[str, object]:
        snapshot = manager.snapshot()
        response.headers["ETag"] = f'"{snapshot["generation"]}"'
        return snapshot

    @app.put("/v1/config", dependencies=protected)
    async def put_fleet_config(
        request: Request,
        response: Response,
        if_match: str | None = Header(default=None),
    ) -> dict[str, object]:
        content_type = request.headers.get("content-type", "")
        expected = if_match.strip().strip('"') if if_match else None
        if "application/json" in content_type:
            payload = await request.json()
            content = payload.get("content") if isinstance(payload, dict) else None
            if expected is None and isinstance(payload, dict):
                generation = payload.get("generation")
                expected = generation if isinstance(generation, str) else None
        else:
            content = (await request.body()).decode("utf-8")
        if not isinstance(content, str):
            raise HTTPException(status_code=422, detail="expected TOML or JSON {content: ...}")
        if not expected:
            # A writer that cannot say what it read cannot know what it is
            # about to overwrite.
            raise HTTPException(
                status_code=428,
                detail="send the generation you read (If-Match header, or JSON 'generation')",
            )
        try:
            watcher.replace(content, expected)
        except GenerationConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": str(exc), "current_generation": exc.current},
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        snapshot = manager.snapshot()
        response.headers["ETag"] = f'"{snapshot["generation"]}"'
        return snapshot

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


def serve_control_app(
    app: FastAPI, *, host: str = CONTROL_HOST, port: int | None = None
) -> None:
    """Serve the control transport on the ADR-defined loopback endpoint."""
    if host != CONTROL_HOST:
        raise ValueError(f"agentd must bind to {CONTROL_HOST}, not {host}")
    # The singleton lock is taken here, before the socket is bound (R2).
    app.state.agentd_supervisor.startup()
    uvicorn.run(app, host=host, port=port if port is not None else control_port())
