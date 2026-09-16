"""Governed Score side-effect adapters (S05, dry-run implementation).

The conductor never accepts a command or callable from a work packet.  A
trusted process constructs this executor with named wrappers and an explicit
allowlist.  This packet intentionally permits dry-run wrappers only; live
repository mutation, builds, deploys, and notifications remain unsupported.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping

from mco.orchestrator.score_authority import GrantService
from mco.orchestrator.scores import ScoreError


class AdapterError(ScoreError):
    """Base error for rejected adapter operations."""


class RecoveryRequired(AdapterError):
    """An earlier effect is unresolved, so replay is unsafe."""


class EffectStatus(str, Enum):
    SUCCEEDED = "succeeded"
    RETRY_SAFE_FAILURE = "retry_safe_failure"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class AdapterResult:
    status: EffectStatus
    observed_state: Mapping[str, Any] = field(default_factory=dict)
    detail: str = ""
    partial_effect: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Operation:
    org_id: str
    run_id: str
    digest: str
    task_id: str
    attempt: int
    adapter: str
    action: str
    resource: str
    environment: str
    owner_principal: str
    desired_state: Mapping[str, Any]
    cost_cents: int = 0
    dry_run: bool = True
    notification_event: Mapping[str, Any] | None = None

    @property
    def id(self) -> str:
        material = ":".join((
            "score-adapter-v1", self.org_id, self.run_id, self.digest,
            self.task_id, str(self.attempt), self.adapter, self.action,
            self.resource, self.environment,
            hashlib.sha256(json.dumps(
                self.desired_state, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True,
            ).encode()).hexdigest(),
        ))
        return str(uuid.uuid5(uuid.NAMESPACE_URL, material))


@dataclass(frozen=True)
class AdapterSpec:
    name: str
    kind: str
    actions: frozenset[str]
    replay_requires_recheck: bool = False
    compensation_supported: bool = False
    dry_run_only: bool = True

    def __post_init__(self) -> None:
        if self.kind not in {"repository", "build", "deploy", "notify"}:
            raise AdapterError("adapter_kind_not_allowed")
        if not self.name or not self.actions:
            raise AdapterError("adapter_allowlist_entry_incomplete")
        if self.kind in {"deploy", "notify"} and not self.replay_requires_recheck:
            raise AdapterError("deploy_and_notify_must_recheck_before_replay")


@dataclass(frozen=True)
class AdapterWrapper:
    """Trusted functions installed by the conductor, never worker payloads."""

    inspect: Callable[[Operation], Mapping[str, Any]]
    invoke: Callable[[Operation], AdapterResult]
    compensate: Callable[[Operation, Mapping[str, Any]], AdapterResult] | None = None


S05_DRY_RUN_ALLOWLIST = {
    "repository-worktree-preview": AdapterSpec(
        "repository-worktree-preview", "repository",
        frozenset({"repository:prepare"}), compensation_supported=True,
    ),
    "build-verification-preview": AdapterSpec(
        "build-verification-preview", "build",
        frozenset({"build:verify"}), compensation_supported=True,
    ),
    "deployment-preview": AdapterSpec(
        "deployment-preview", "deploy", frozenset({"deploy:preview"}),
        replay_requires_recheck=True, compensation_supported=True,
    ),
    "owner-notification-preview": AdapterSpec(
        "owner-notification-preview", "notify", frozenset({"notify:owner"}),
        replay_requires_recheck=True,
    ),
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _desired(observed: Mapping[str, Any], desired: Mapping[str, Any]) -> bool:
    """Desired-state checks are exact and intentionally not worker-defined."""
    return isinstance(observed, Mapping) and all(observed.get(k) == v for k, v in desired.items())


class ScoreAdapterExecutor:
    """Execute only trusted, allowlisted, dry-run wrappers with durable receipts."""

    def __init__(self, db: Any, *, grant_service: GrantService,
                 allowlist: Mapping[str, AdapterSpec],
                 wrappers: Mapping[str, AdapterWrapper],
                 now: Callable[[], datetime] | None = None):
        if not isinstance(grant_service, GrantService):
            raise AdapterError("durable_grant_service_required")
        if set(wrappers) != set(allowlist):
            raise AdapterError("adapter_registry_must_exactly_match_allowlist")
        for name, spec in allowlist.items():
            if name != spec.name or not isinstance(wrappers[name], AdapterWrapper):
                raise AdapterError("invalid_adapter_registry")
            if not spec.dry_run_only:
                raise AdapterError("live_adapters_not_supported_in_s05")
            if spec.compensation_supported and wrappers[name].compensate is None:
                raise AdapterError("compensation_wrapper_required")
        self.db = db
        self.grants = grant_service
        self.allowlist = dict(allowlist)
        self.wrappers = dict(wrappers)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _validate(self, op: Operation) -> tuple[AdapterSpec, AdapterWrapper, str]:
        for value in (
            op.org_id, op.run_id, op.digest, op.task_id, op.adapter,
            op.action, op.resource, op.environment, op.owner_principal,
        ):
            if not isinstance(value, str) or not value.strip():
                raise AdapterError("adapter_operation_binding_incomplete")
        if type(op.attempt) is not int or op.attempt < 1:
            raise AdapterError("adapter_attempt_invalid")
        if type(op.cost_cents) is not int or op.cost_cents < 0:
            raise AdapterError("adapter_cost_invalid")
        if not isinstance(op.desired_state, Mapping) or not op.desired_state:
            raise AdapterError("desired_state_required")
        spec = self.allowlist.get(op.adapter)
        if spec is None or op.action not in spec.actions:
            raise AdapterError("adapter_or_action_not_allowlisted")
        if not op.dry_run or not spec.dry_run_only:
            raise AdapterError("live_effect_forbidden_in_s05")
        if spec.kind == "notify":
            require_quiet_notification(op.notification_event or {})
        _grant, identity = self.grants.require(
            org_id=op.org_id, run_id=op.run_id, digest=op.digest,
            action=op.action, resource=op.resource,
            environment=op.environment, cost_cents=op.cost_cents,
            owner_principal=op.owner_principal, now=self.now(),
        )
        return spec, self.wrappers[op.adapter], identity

    def _events(self, op: Operation) -> list[dict]:
        return (
            self.db.table("score_events").select("*")
            .eq("org_id", op.org_id).eq("run_id", op.run_id)
            .eq("task_id", op.task_id).execute().data or []
        )

    def _receipt(self, op: Operation, *, phase: str, status: str,
                 grant_identity: str, observed: Mapping[str, Any],
                 detail: str = "") -> dict:
        receipt = {
            "protocol": "score-adapter-receipt/v1",
            "operation_id": op.id,
            "phase": phase,
            "status": status,
            "adapter": op.adapter,
            "action": op.action,
            "resource": op.resource,
            "environment": op.environment,
            "attempt": op.attempt,
            "dry_run": True,
            "desired_state_sha256": _sha(op.desired_state),
            "observed_state": copy.deepcopy(dict(observed)),
            "observed_state_sha256": _sha(observed),
            "grant_identity": grant_identity,
            "detail": str(detail or ""),
        }
        self.db.table("score_events").insert({
            "org_id": op.org_id, "run_id": op.run_id,
            "kind": f"adapter_{phase}", "task_id": op.task_id,
            "digest": op.digest, "actor": "score-conductor",
            "payload": receipt,
        }).execute()
        return receipt

    def _completed(self, op: Operation) -> dict | None:
        for event in reversed(self._events(op)):
            payload = event.get("payload") or {}
            if (payload.get("operation_id") == op.id
                    and payload.get("phase") in {"after", "reconciled"}
                    and payload.get("status") in {"succeeded", "already_desired"}):
                return payload
        return None

    def _recovery(self, op: Operation) -> dict | None:
        rows = (
            self.db.table("score_recovery").select("*")
            .eq("approval_or_attempt_id", op.id).eq("org_id", op.org_id)
            .execute().data or []
        )
        return rows[0] if rows else None

    def execute(self, op: Operation) -> dict:
        spec, wrapper, grant_identity = self._validate(op)
        completed = self._completed(op)
        if completed:
            return dict(completed, replayed=True)

        recovery = self._recovery(op)
        if recovery and recovery.get("decision") == "pending":
            observed = dict(wrapper.inspect(op))
            if _desired(observed, op.desired_state):
                self.db.table("score_recovery").update({"decision": "inspected"}).eq(
                    "approval_or_attempt_id", op.id,
                ).eq("decision", "pending").execute()
                return self._receipt(
                    op, phase="reconciled", status="already_desired",
                    grant_identity=grant_identity, observed=observed,
                    detail="uncertain effect found in desired state; invocation not replayed",
                )
            raise RecoveryRequired("uncertain_effect_requires_inspection_or_compensation")

        observed_before = dict(wrapper.inspect(op))
        if spec.replay_requires_recheck and _desired(observed_before, op.desired_state):
            return self._receipt(
                op, phase="reconciled", status="already_desired",
                grant_identity=grant_identity, observed=observed_before,
                detail="desired state already present; invocation skipped",
            )
        self._receipt(
            op, phase="before", status="observed",
            grant_identity=grant_identity, observed=observed_before,
        )

        try:
            result = wrapper.invoke(op)
        except Exception as exc:
            # An arbitrary exception cannot prove that no effect occurred.
            result = AdapterResult(
                EffectStatus.UNCERTAIN, observed_state=observed_before,
                detail=f"wrapper_exception:{type(exc).__name__}",
                partial_effect={"exception_type": type(exc).__name__},
            )
        if not isinstance(result, AdapterResult):
            raise AdapterError("adapter_returned_invalid_result")
        after = dict(result.observed_state)
        if result.status is EffectStatus.SUCCEEDED:
            if not _desired(after, op.desired_state):
                result = AdapterResult(
                    EffectStatus.UNCERTAIN, after,
                    "success_claim_did_not_reach_desired_state",
                    {"claimed_status": "succeeded"},
                )
            else:
                return self._receipt(
                    op, phase="after", status="succeeded",
                    grant_identity=grant_identity, observed=after,
                    detail=result.detail,
                )
        if result.status is EffectStatus.RETRY_SAFE_FAILURE:
            return self._receipt(
                op, phase="after", status="retry_safe_failure",
                grant_identity=grant_identity, observed=after,
                detail=result.detail,
            )

        uncertain = {
            "operation": copy.deepcopy(op.__dict__),
            "adapter_kind": spec.kind,
            "partial_effect": copy.deepcopy(dict(result.partial_effect)),
            "observed_state": copy.deepcopy(after),
            "compensation_supported": spec.compensation_supported,
            "detail": result.detail,
        }
        try:
            self.db.table("score_recovery").insert({
                "approval_or_attempt_id": op.id, "org_id": op.org_id,
                "uncertain_effect": uncertain, "decision": "pending",
            }).execute()
        except Exception:
            # A concurrent replay may have persisted the same recovery row.
            existing = self._recovery(op)
            if not existing or existing.get("uncertain_effect") != uncertain:
                raise
        return self._receipt(
            op, phase="after", status="uncertain",
            grant_identity=grant_identity, observed=after,
            detail=result.detail,
        )

    def compensate(self, op: Operation) -> dict:
        spec, wrapper, grant_identity = self._validate(op)
        recovery = self._recovery(op)
        if not recovery or recovery.get("decision") != "pending":
            raise RecoveryRequired("pending_uncertain_effect_not_found")
        if not spec.compensation_supported or wrapper.compensate is None:
            raise RecoveryRequired("uncertain_effect_requires_owner_action")
        effect = recovery.get("uncertain_effect") or {}
        observed = dict(wrapper.inspect(op))
        if not effect.get("partial_effect") and not _desired(observed, op.desired_state):
            decision = "inspected"
            result = AdapterResult(EffectStatus.SUCCEEDED, observed, "no partial effect present")
        else:
            result = wrapper.compensate(op, copy.deepcopy(effect.get("partial_effect") or {}))
            if not isinstance(result, AdapterResult) or result.status is not EffectStatus.SUCCEEDED:
                raise RecoveryRequired("compensation_not_confirmed")
            decision = "compensated"
        updated = (
            self.db.table("score_recovery").update({"decision": decision})
            .eq("approval_or_attempt_id", op.id).eq("decision", "pending")
            .execute().data or []
        )
        if not updated:
            raise RecoveryRequired("compensation_lost_concurrent_race")
        return self._receipt(
            op, phase="compensated", status=decision,
            grant_identity=grant_identity,
            observed=dict(result.observed_state), detail=result.detail,
        )

    def rollback(self, op: Operation) -> dict:
        """Rollback a known partial effect through its declared compensation."""
        return self.compensate(op)


_OWNER_GATES = frozenset({"g08_launch_signoff", "spend_above_cap"})


def require_quiet_notification(event: Mapping[str, Any]) -> str:
    """Return the allowed notification class or reject routine chatter."""
    if not isinstance(event, Mapping):
        raise AdapterError("notification_event_required")
    kind = event.get("kind")
    if kind == "human_gate" and event.get("gate_kind") in _OWNER_GATES:
        return "human_gate"
    if (kind == "owner_action_blocker" and event.get("irrecoverable") is True
            and event.get("owner_action_required") is True):
        return "owner_action_blocker"
    if kind == "milestone" and event.get("completed") is True:
        return "milestone"
    raise AdapterError("quiet_operation_suppresses_notification")
