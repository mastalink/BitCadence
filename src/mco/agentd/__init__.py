"""BitCadence's one-per-user worker supervisor."""

from typing import Any

from .config import FleetConfigManager, FleetWatcher, GenerationConflict
from .control import CONTROL_HOST, control_port, create_control_app, serve_control_app
from .logs import LogAggregator
from .supervisor import Supervisor, WorkerState, backoff_delay
from .tokens import AgentdTokenStore

__all__ = [
    "FleetConfigManager",
    "FleetWatcher",
    "GenerationConflict",
    "LogAggregator",
    "Supervisor",
    "WorkerState",
    "backoff_delay",
    "AgentdTokenStore",
    "CONTROL_HOST",
    "CONTROL_PORT",
    "control_port",
    "create_control_app",
    "serve_control_app",
]


def __getattr__(name: str) -> Any:
    # Resolved on first use, not at import: see control.__getattr__.
    if name == "CONTROL_PORT":
        return control_port()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
