"""
Enterprise connector framework.

Connectors bridge BitCadence to the agent surfaces of enterprise platforms
(ServiceNow, Dynatrace, ...). Each connector plays two roles:

1. **Ingestion** - `pull_events()` normalizes platform objects (incidents,
   problems, alerts) into job specs so AI agents can work them through the
   normal lease/execute/approve lifecycle.
2. **Control** - `execute_action(action, params)` pushes decisions back into
   the platform (create/resolve incidents, comment on problems). Connectors
   can also be registered as worker roles, so a job targeted at role
   "servicenow" executes a platform action like any other agent job.

Credentials resolve through the standard config stack (env -> .env ->
AES-256-GCM secret store). Connectors are stateless wrappers over httpx; a
`transport` hook allows offline testing with httpx.MockTransport.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

logger = logging.getLogger("mco.connectors")


class ConnectorError(RuntimeError):
    """Raised when a connector call fails or is misconfigured."""


class BaseConnector(ABC):
    """Contract every enterprise connector implements.

    `name` doubles as the worker role the connector can serve, so MCO jobs can
    be addressed to the platform itself (target_agent_role="servicenow").
    """

    name: str = "base"

    @abstractmethod
    def health(self) -> dict:
        """Cheap reachability/auth probe. Returns {'ok': bool, 'detail': str}."""

    @abstractmethod
    def pull_events(self) -> List[dict]:
        """Normalize open platform objects into job specs.

        Each spec: {external_id, title, description, target_agent_role,
        input_payload} - external_id must be stable for dedupe across syncs.
        """

    @abstractmethod
    def actions(self) -> List[str]:
        """Action names usable with execute_action / connector-role jobs."""

    @abstractmethod
    def execute_action(self, action: str, params: dict) -> dict:
        """Run a control-plane action against the platform."""

    def escalate(self, job: dict, error: str) -> dict:
        """Record an MCO escalation in the platform (e.g. open an incident).

        Connectors without a natural escalation surface may leave this
        unimplemented; the escalation bridge treats that as a no-op.
        """
        raise NotImplementedError(f"{self.name} has no escalation surface")


# ── Registry ──────────────────────────────────────────────────────────────────

_registry: Dict[str, BaseConnector] = {}
_built = False


def register_connector(connector: BaseConnector) -> None:
    _registry[connector.name] = connector
    logger.info(f"Registered connector: {connector.name}")


def get_connector(name: str) -> Optional[BaseConnector]:
    build_connectors()
    return _registry.get((name or "").lower())


def list_connectors() -> List[BaseConnector]:
    build_connectors()
    return list(_registry.values())


def reset_connectors() -> None:
    """Clear the registry (test hook / config reload)."""
    global _built
    _registry.clear()
    _built = False


def build_connectors(force: bool = False) -> List[BaseConnector]:
    """Instantiate every connector whose credentials are configured."""
    global _built
    if _built and not force:
        return list(_registry.values())

    from mco.config import get_config
    config = get_config()

    from mco.connectors.servicenow import ServiceNowConnector
    from mco.connectors.dynatrace import DynatraceConnector

    if config.get("SERVICENOW_INSTANCE_URL"):
        try:
            register_connector(ServiceNowConnector.from_config(config))
        except Exception as e:
            logger.warning(f"ServiceNow connector not loaded: {e}")
    if config.get("DYNATRACE_BASE_URL"):
        try:
            register_connector(DynatraceConnector.from_config(config))
        except Exception as e:
            logger.warning(f"Dynatrace connector not loaded: {e}")

    _built = True
    return list(_registry.values())


def make_connector_executor(connector: BaseConnector):
    """Build an executor so the connector can serve as a worker role.

    Jobs targeted at the connector's role carry
    input_payload={"action": "<name>", "params": {...}}.
    """
    import json

    async def _executor(job: dict, prompt: str):
        payload = job.get("input_payload") or {}
        action = payload.get("action")
        if not action:
            return None, f"Job for connector '{connector.name}' is missing input_payload.action"
        try:
            result = connector.execute_action(action, payload.get("params") or {})
            return json.dumps(result), None
        except Exception as e:
            return None, f"{connector.name}.{action} failed: {e}"

    return _executor
