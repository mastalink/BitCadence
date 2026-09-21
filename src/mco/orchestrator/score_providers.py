"""Deterministic Score provider selection. No LLM, no network, no dispatch.

Catalog rows are approved per score digest. This module classifies and ranks
identities; it never writes score_tasks.status=accepted and never treats
llm_connections as a catalog.

S01a owns the durable tables. Callers persist the returned event payload.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Literal, Mapping, Optional, Sequence, Tuple

Phase = Literal["work", "review"]
HealthClass = Literal[
    "ok",
    "provider_unavailable",
    "quota_exhausted",
    "identity_offline",
    "cost_exceeded",
    "authority_missing",
]
PathStatus = Literal["dispatch", "waiting_provider", "blocked"]
IndependenceClass = Literal["same_provider", "cross_provider"]

DEFAULT_WEIGHTS = {
    "capability_fit": 30,
    "independence": 25,
    "authority_tightness": 20,
    "availability": 15,
    "cost": 10,
}


class ProviderSelectError(ValueError):
    """Invalid catalog, task, or policy input."""


@dataclass(frozen=True)
class Provider:
    instance_id: str
    role: str
    provider: str
    capabilities: FrozenSet[str]
    independence_class: IndependenceClass
    authority_scopes: FrozenSet[str]
    approved_digests: FrozenSet[str]
    remaining_cost_cents: int
    online: bool = True
    schedulable: bool = True
    outage: bool = False
    quota_reset_at: Optional[int] = None
    cost_weight: int = 0


@dataclass(frozen=True)
class Run:
    digest: str
    grants: FrozenSet[str]
    authorized_budget_cents: int
    remaining_budget_cents: int
    independence_class: IndependenceClass = "same_provider"


@dataclass(frozen=True)
class Task:
    task_id: str
    role: str
    review_role: str
    capabilities: FrozenSet[str]
    max_cost_cents: int
    attempt: int = 1
    author_id: Optional[str] = None
    author_provider: Optional[str] = None
    prior_author_ids: FrozenSet[str] = field(default_factory=frozenset)
    head_shas: FrozenSet[str] = field(default_factory=frozenset)
    contribution_instance_ids: FrozenSet[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Classified:
    provider: Provider
    health: HealthClass
    score: Optional[int] = None
    rejected: Optional[str] = None


@dataclass(frozen=True)
class Selection:
    status: PathStatus
    error_class: Optional[str]
    reason: Optional[str]
    chosen: Optional[Provider]
    wake_at: Optional[int]
    classified: Tuple[Classified, ...]
    weights: Mapping[str, int]
    event: Mapping[str, object]


def _require_weights(weights: Mapping[str, int]) -> Mapping[str, int]:
    if set(weights) != set(DEFAULT_WEIGHTS):
        raise ProviderSelectError("weights keys must match DEFAULT_WEIGHTS")
    if any(type(v) is not int or v < 0 for v in weights.values()):
        raise ProviderSelectError("weights must be non-negative integers")
    if sum(weights.values()) != 100:
        raise ProviderSelectError("weights must sum to 100")
    return weights


def _needed_role(task: Task, phase: Phase) -> str:
    if phase == "work":
        return task.role
    if phase == "review":
        return task.review_role
    raise ProviderSelectError(f"unknown phase: {phase}")


def _grant_covers(run: Run, task: Task) -> bool:
    return set(task.capabilities) <= set(run.grants)


def _review_excluded(provider: Provider, task: Task, run: Run) -> Optional[str]:
    if provider.instance_id == task.author_id:
        return "author_of_attempt"
    if provider.instance_id in task.prior_author_ids:
        return "author_of_prior_attempt"
    if provider.instance_id in task.contribution_instance_ids:
        return "contribution_set"
    if (
        run.independence_class == "cross_provider"
        and task.author_provider
        and provider.provider == task.author_provider
    ):
        return "same_provider"
    return None


def _classify(provider: Provider, task: Task, run: Run) -> HealthClass:
    if provider.outage:
        return "provider_unavailable"
    if not provider.online and not provider.schedulable:
        return "identity_offline"
    if provider.quota_reset_at is not None:
        return "quota_exhausted"
    if not _grant_covers(run, task) or not (set(task.capabilities) <= set(provider.authority_scopes)):
        return "authority_missing"
    if task.max_cost_cents > provider.remaining_cost_cents or task.max_cost_cents > run.remaining_budget_cents:
        return "cost_exceeded"
    return "ok"


def _rank(provider: Provider, task: Task, run: Run, weights: Mapping[str, int], phase: Phase) -> int:
    extra_caps = len(provider.capabilities - task.capabilities)
    capability_fit = weights["capability_fit"] if extra_caps == 0 else max(0, weights["capability_fit"] - extra_caps)
    if phase == "review" and provider.instance_id != task.author_id:
        independence = weights["independence"]
        if run.independence_class == "cross_provider" and provider.provider != task.author_provider:
            independence = weights["independence"]
        elif run.independence_class == "same_provider":
            independence = weights["independence"]
    else:
        independence = weights["independence"] if phase == "work" else 0
    extra_auth = len(provider.authority_scopes - task.capabilities)
    tightness = weights["authority_tightness"] if extra_auth == 0 else max(0, weights["authority_tightness"] - extra_auth)
    availability = weights["availability"] if provider.online else weights["availability"] // 2
    cost = weights["cost"] if provider.remaining_cost_cents >= task.max_cost_cents else 0
    return capability_fit + independence + tightness + availability + cost


def _filter_catalog(catalog: Sequence[Provider], task: Task, run: Run, phase: Phase) -> Tuple[Tuple[Provider, Optional[str]], ...]:
    needed = _needed_role(task, phase)
    kept = []
    for provider in catalog:
        if run.digest not in provider.approved_digests:
            continue
        if provider.role != needed:
            continue
        if not (set(task.capabilities) <= set(provider.capabilities)):
            continue
        if not (provider.online or provider.schedulable):
            kept.append((provider, None))
            continue
        excluded = _review_excluded(provider, task, run) if phase == "review" else None
        kept.append((provider, excluded))
    return tuple(kept)


def select(
    task: Task,
    phase: Phase,
    catalog: Sequence[Provider],
    run: Run,
    *,
    weights: Mapping[str, int] = DEFAULT_WEIGHTS,
    jev_provider: Optional[Any] = None,
) -> Selection:
    """Pick exactly one identity or pause visibly. Never silent reassignment."""
    used_weights = _require_weights(weights)
    if phase == "review" and task.role == task.review_role:
        raise ProviderSelectError("work and review roles must differ")
    classified: list[Classified] = []
    for provider, excluded in _filter_catalog(catalog, task, run, phase):
        if excluded:
            classified.append(Classified(provider=provider, health="ok", rejected=excluded))
            continue
        health = _classify(provider, task, run)
        score = _rank(provider, task, run, used_weights, phase) if health == "ok" else None
        classified.append(Classified(provider=provider, health=health, score=score))

    available = [row for row in classified if row.health == "ok" and not row.rejected]
    if not available:
        exhausted = [row for row in classified if row.health == "quota_exhausted" and row.provider.quota_reset_at is not None]
        if exhausted:
            wake_at = min(row.provider.quota_reset_at for row in exhausted if row.provider.quota_reset_at is not None)
            event = {
                "kind": "waiting_provider",
                "task_id": task.task_id,
                "phase": phase,
                "error_class": "provider_unavailable",
                "wake_at": wake_at,
                "alternatives_rejected": tuple(
                    {"instance_id": row.provider.instance_id, "health": row.health, "rejected": row.rejected}
                    for row in classified
                ),
            }
            return Selection(
                status="waiting_provider",
                error_class="provider_unavailable",
                reason="quota_exhausted",
                chosen=None,
                wake_at=wake_at,
                classified=tuple(classified),
                weights=used_weights,
                event=event,
            )
        event = {
            "kind": "no_qualified_provider",
            "task_id": task.task_id,
            "phase": phase,
            "error_class": "no_qualified_provider",
            "alternatives_rejected": tuple(
                {"instance_id": row.provider.instance_id, "health": row.health, "rejected": row.rejected}
                for row in classified
            ),
        }
        return Selection(
            status="blocked",
            error_class="no_qualified_provider",
            reason="no_qualified_provider",
            chosen=None,
            wake_at=None,
            classified=tuple(classified),
            weights=used_weights,
            event=event,
        )

    available.sort(
        key=lambda row: (
            -(row.score or 0),
            row.provider.remaining_cost_cents,
            row.provider.instance_id,
        )
    )
    chosen = available[0].provider
    event = {
        "kind": "provider_selected",
        "task_id": task.task_id,
        "phase": phase,
        "instance_id": chosen.instance_id,
        "provider": chosen.provider,
        "independence_class": chosen.independence_class,
        "weights": dict(used_weights),
        "score": available[0].score,
        "alternatives_rejected": tuple(
            {
                "instance_id": row.provider.instance_id,
                "health": row.health,
                "rejected": row.rejected,
                "score": row.score,
            }
            for row in classified
            if row.provider.instance_id != chosen.instance_id
        ),
    }
    if jev_provider is not None and getattr(getattr(jev_provider, "config", None), "mode", "disabled") != "disabled":
        try:
            from mco.orchestrator.jev import GLOBAL_JEV_METRICS, evaluate_shadow_shortlist
            candidates = [row.provider for row in available]
            shadow_res = evaluate_shadow_shortlist(
                jev_provider,
                {"task_id": task.task_id, "role": _needed_role(task, phase), "capabilities": list(task.capabilities)},
                candidates,
            )
            if shadow_res is not None:
                receipt, jev_pick = shadow_res
                disagreed = bool(jev_pick and jev_pick != chosen.instance_id)
                if disagreed:
                    GLOBAL_JEV_METRICS.record_disagreement()
                event["shadow_annotation"] = {
                    "receipt": receipt.to_dict(),
                    "disagreed": disagreed,
                    "deterministic_chosen": chosen.instance_id,
                    "jev_pick": jev_pick,
                }
        except Exception:
            pass
    return Selection(
        status="dispatch",
        error_class=None,
        reason=None,
        chosen=chosen,
        wake_at=None,
        classified=tuple(classified),
        weights=used_weights,
        event=event,
    )


def canary_catalog(digest: str) -> Tuple[Provider, Provider, Provider]:
    """Two fixed non-LLM identities plus a permanently unavailable fixture."""
    caps = frozenset({"evidence:write", "evidence:review"})
    worker = Provider(
        instance_id="score-canary-worker",
        role="score-canary-worker",
        provider="fixed-handler",
        capabilities=caps,
        independence_class="same_provider",
        authority_scopes=caps,
        approved_digests=frozenset({digest}),
        remaining_cost_cents=0,
    )
    reviewer = Provider(
        instance_id="score-canary-review",
        role="score-canary-review",
        provider="fixed-handler-review",
        capabilities=caps,
        independence_class="same_provider",
        authority_scopes=caps,
        approved_digests=frozenset({digest}),
        remaining_cost_cents=0,
    )
    unavailable = Provider(
        instance_id="score-canary-unavailable",
        role="score-canary-worker",
        provider="fixture-down",
        capabilities=caps,
        independence_class="same_provider",
        authority_scopes=caps,
        approved_digests=frozenset({digest}),
        remaining_cost_cents=0,
        outage=True,
    )
    return worker, reviewer, unavailable
