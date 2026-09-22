"""Usage-aware Codex routing with Jev as a bounded semantic advisor.

Jev describes the task. Deterministic code owns quotas, model eligibility,
parallelism, and execution. Nothing in this module switches a running Codex
session, spawns an agent, or sends a BitCadence job.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from mco.orchestrator.jev_ops import annotate_codex_task_route


MODELS = (
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-6-astra",
)
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")

ASTRA_MIN_FIVE_HOUR_REMAINING = 30.0
ASTRA_MIN_WEEKLY_REMAINING = 20.0
PARALLEL_MIN_FIVE_HOUR_REMAINING = 20.0
CONSERVE_AT_FIVE_HOUR_REMAINING = 15.0

_MODEL_FOR_COMPLEXITY = {
    "small": "gpt-5.6-luna",
    "standard": "gpt-5.6-terra",
    "complex": "gpt-5.6-sol",
    "frontier": "gpt-6-astra",
}
_EFFORT_FOR_NEED = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "extreme": "xhigh",
}


def _percent(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, number))


def _flag(value: Any, threshold: float = 0.6) -> bool:
    return isinstance(value, (int, float)) and float(value) >= threshold


def _available(values: Optional[Iterable[str]]) -> tuple[str, ...]:
    if values is None:
        return MODELS
    allowed = tuple(model for model in values if model in MODELS)
    return allowed or MODELS[:-1]


def _fallback_model(preferred: str, available: tuple[str, ...]) -> str:
    if preferred in available:
        return preferred
    order = list(MODELS)
    start = order.index(preferred) if preferred in order else order.index("gpt-5.6-terra")
    for index in range(start, -1, -1):
        if order[index] in available:
            return order[index]
    return available[0]


def apply_codex_policy(
    annotation: dict,
    *,
    five_hour_remaining_percent: Any = None,
    weekly_remaining_percent: Any = None,
    context_pressure: str = "unknown",
    available_models: Optional[Iterable[str]] = None,
) -> dict:
    """Convert semantic annotations into a deterministic advisory route."""
    five_hour = _percent(five_hour_remaining_percent)
    weekly = _percent(weekly_remaining_percent)
    available = _available(available_models)
    complexity = annotation.get("complexity") or "standard"
    reasoning_need = annotation.get("reasoning_need") or "medium"
    execution = annotation.get("execution_shape") or "root_only"

    preferred = _MODEL_FOR_COMPLEXITY.get(complexity, "gpt-5.6-terra")
    effort = _EFFORT_FOR_NEED.get(reasoning_need, "medium")
    context_pressure = str(context_pressure or "unknown").strip().lower()

    astra_reasons = []
    if five_hour is None or five_hour < ASTRA_MIN_FIVE_HOUR_REMAINING:
        astra_reasons.append("five-hour capacity is unknown or below 30% remaining")
    if weekly is None or weekly < ASTRA_MIN_WEEKLY_REMAINING:
        astra_reasons.append("weekly capacity is unknown or below 20% remaining")
    if context_pressure == "high":
        astra_reasons.append("context pressure is high")
    if "gpt-6-astra" not in available:
        astra_reasons.append("Astra is unavailable")
    astra_eligible = not astra_reasons
    if preferred == "gpt-6-astra" and not astra_eligible:
        preferred = "gpt-5.6-sol"
        effort = "max" if reasoning_need == "extreme" else "high"

    conserving = five_hour is not None and five_hour < CONSERVE_AT_FIVE_HOUR_REMAINING
    if conserving and preferred in {"gpt-5.6-sol", "gpt-6-astra"}:
        preferred = "gpt-5.6-terra"
        effort = "high" if reasoning_need in {"high", "extreme"} else "medium"

    model = _fallback_model(preferred, available)
    parallel_allowed = five_hour is None or five_hour >= PARALLEL_MIN_FIVE_HOUR_REMAINING
    max_parallel = 3 if parallel_allowed else 1
    if execution == "subagents" and not parallel_allowed:
        execution = "root_only"
    elif execution == "hybrid" and not parallel_allowed:
        execution = "bitcadence"

    prompt_guidance = []
    if _flag(annotation.get("needs_current_information")):
        prompt_guidance.append("verify current external information before acting")
    if _flag(annotation.get("needs_workspace_evidence")):
        prompt_guidance.append("inspect the actual workspace or runtime and cite concrete evidence")
    if _flag(annotation.get("needs_acceptance_criteria")):
        prompt_guidance.append("state a measurable success condition in delegated task packets")
    if _flag(annotation.get("needs_clarification")):
        prompt_guidance.append("ask only for a missing choice that materially changes the outcome")

    receipt = annotation.get("receipt")
    return {
        "recommended_model": model,
        "reasoning_effort": effort,
        "execution_shape": execution,
        "max_parallel": max_parallel,
        "prompt_guidance": prompt_guidance,
        "task_kind": annotation.get("task_kind"),
        "complexity": complexity,
        "jev_outcome": getattr(receipt, "outcome", "unavailable"),
        "jev_model": getattr(receipt, "model", None),
        "confidence": annotation.get("confidence") or {},
        "capacity": {
            "five_hour_remaining_percent": five_hour,
            "weekly_remaining_percent": weekly,
            "astra_eligible": astra_eligible,
            "astra_ineligible_reasons": astra_reasons,
            "conserving": conserving,
        },
        "applied": False,
    }


def route_codex_task(
    *,
    task: str,
    context: str = "",
    current_model: str = "",
    context_pressure: str = "unknown",
    five_hour_remaining_percent: Any = None,
    weekly_remaining_percent: Any = None,
    available_models: Optional[Iterable[str]] = None,
    db: Any = None,
) -> dict:
    annotation = annotate_codex_task_route(
        None,
        task=task[:6000],
        context=context[:3000] or None,
        current_model=current_model or None,
        context_pressure=context_pressure,
        db=db,
    )
    return apply_codex_policy(
        annotation,
        five_hour_remaining_percent=five_hour_remaining_percent,
        weekly_remaining_percent=weekly_remaining_percent,
        context_pressure=context_pressure,
        available_models=available_models,
    )
