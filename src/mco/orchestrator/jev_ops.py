"""Bounded Jev SHADOW assistance for Drumline, delivery, and ntfy.

These paths treat Jev as annotation-only even when the global mode is
assist or active. No answer mutates durable truth or authorizes an effect.
``applied`` is always False; callers ignore suggestions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from mco.orchestrator.jev import (
    DecisionReceipt,
    JevConfig,
    JevProvider,
    _fallback_receipt,
    build_provider,
)
from mco.orchestrator.jev_questions import (
    CLAUDE_CODE_MODEL_ROUTE,
    CODEX_TASK_ROUTE,
    DRUMLINE_OPS,
    NOTIFY_QUALITY,
    WATCHDOG_SYMPTOM,
    get_registry,
)

MODEL_TIERS = ("haiku", "sonnet", "opus")

logger = logging.getLogger("mco.jev_ops")

# Tests assign a provider here so hook paths that pass provider=None never
# inherit a developer machine's MCO_JEV_MODE or touch the network.
provider_override: Optional[JevProvider] = None

_LIVE_OUTCOMES = frozenset({"shadow", "success"})

_PRIORITY_TO_URGENCY = (
    (5, "emergency"),
    (4, "urgent"),
    (3, "elevated"),
    (0, "routine"),
)
_URGENCY_TO_PRIORITY = {
    "routine": 2,
    "elevated": 3,
    "urgent": 4,
    "emergency": 5,
}

_metrics = {
    "calls": 0,
    "latency_ms_sum": 0,
    "fallbacks": 0,
    "errors": 0,
    "disabled": 0,
    "shadow": 0,
    "disagreement": 0,
}


def reset_metrics() -> None:
    for key in _metrics:
        _metrics[key] = 0


def metrics_snapshot() -> Dict[str, int]:
    return dict(_metrics)


def provider_from_config(config: Any = None, db: Any = None, org_id: str = "default") -> JevProvider:
    """Build a provider from config. Never raises; disabled is the safe default."""
    try:
        if config is None:
            from mco.config import get_config
            config = get_config()
        return build_provider(config, db, org_id)
    except Exception:
        logger.debug("Jev provider construction failed; using disabled default", exc_info=True)
        return JevProvider(JevConfig())


def _resolve_provider(provider: Optional[JevProvider], db: Any = None, config: Any = None) -> JevProvider:
    if provider is not None:
        return provider
    if provider_override is not None:
        return provider_override
    return provider_from_config(config, db=db)


def suggestion_must_not_authorize(annotation: Mapping[str, Any]) -> bool:
    """Shadow annotations never authorize an effect."""
    return annotation.get("applied") is False


def _choice(receipt: DecisionReceipt, name: str) -> Optional[str]:
    answer = (receipt.answers or {}).get(name) or {}
    value = answer.get("choice")
    return str(value) if isinstance(value, str) else None


def _noul(receipt: DecisionReceipt, name: str) -> Optional[float]:
    answer = (receipt.answers or {}).get(name) or {}
    value = answer.get("noul")
    return float(value) if isinstance(value, (int, float)) else None


def _score(receipt: DecisionReceipt, name: str) -> Optional[float]:
    answer = (receipt.answers or {}).get(name) or {}
    value = answer.get("score")
    return float(value) if isinstance(value, (int, float)) else None


def _urgency_from_priority(priority: int) -> str:
    try:
        value = int(priority)
    except (TypeError, ValueError):
        value = 0
    for threshold, label in _PRIORITY_TO_URGENCY:
        if value >= threshold:
            return label
    return "routine"


def _note(outcome: str, latency_ms: Optional[int], disagreed: bool) -> None:
    _metrics["calls"] += 1
    _metrics["latency_ms_sum"] += int(latency_ms or 0)
    if outcome == "disabled":
        _metrics["disabled"] += 1
    elif outcome == "fallback":
        _metrics["fallbacks"] += 1
    elif outcome == "shadow":
        _metrics["shadow"] += 1
    if disagreed:
        _metrics["disagreement"] += 1


def _persist(provider: JevProvider, db: Any, job_id: Optional[str], receipt: DecisionReceipt, suggestion: Any) -> None:
    if not job_id or db is None:
        return
    if getattr(getattr(provider, "config", None), "mode", "disabled") == "disabled":
        return
    try:
        from mco.orchestrator.audit import record_event
        record_event(
            db, job_id, "jev_decision", "system", "jev",
            {"receipt": receipt.to_dict(), "suggestion": suggestion},
        )
    except Exception:
        logger.debug("jev_decision audit persist failed; leaving parent operation intact", exc_info=True)


def _decide(
    provider: Optional[JevProvider],
    use_case_id: str,
    state: Any,
    *,
    job_id: Optional[str] = None,
    db: Any = None,
    config: Any = None,
) -> tuple[JevProvider, DecisionReceipt, bool]:
    """Run one decision. Never raises. live=True when answers may be read."""
    registry = get_registry(use_case_id)
    questions = registry["questions"]
    version = registry["version"]
    resolved = _resolve_provider(provider, db=db, config=config)
    try:
        receipt = resolved.decide(
            use_case_id=use_case_id,
            question_set_version=version,
            state=state,
            questions=questions,
        )
    except Exception:
        _metrics["errors"] += 1
        logger.debug("Jev decide failed; using deterministic fallback", exc_info=True)
        cfg = getattr(resolved, "config", None) or JevConfig()
        receipt = _fallback_receipt(
            cfg, use_case_id, version, questions, state, "fallback", "invalid_response",
        )
    live = receipt.outcome in _LIVE_OUTCOMES
    return resolved, receipt, live


def annotate_drumline_output(
    provider: Optional[JevProvider],
    *,
    title: str,
    content: str,
    deterministic_kind: str,
    query: Optional[str] = None,
    job_id: Optional[str] = None,
    db: Any = None,
) -> dict:
    """Classify completed output. Never changes stored kind, content, weight, or tags."""
    state = {
        "title": title,
        "content": content,
        "deterministic_kind": deterministic_kind,
        "query": query,
    }
    resolved, receipt, live = _decide(provider, DRUMLINE_OPS, state, job_id=job_id, db=db)
    kind = _choice(receipt, "kind") if live else None
    inject = _choice(receipt, "inject") if live else None
    suggestion = {"kind": kind, "inject": inject} if live else None
    disagreed = bool(kind and kind != deterministic_kind)
    _note(receipt.outcome, receipt.latency_ms, disagreed)
    _persist(resolved, db, job_id, receipt, suggestion)
    return {
        "kind": kind,
        "inject": inject,
        "flags": {
            "contradiction": _noul(receipt, "contradiction") if live else None,
            "staleness": _noul(receipt, "staleness") if live else None,
            "sensitivity": _noul(receipt, "sensitivity") if live else None,
        },
        "relevance": _score(receipt, "relevance") if live else None,
        "receipt": receipt,
        "applied": False,
    }


def annotate_watchdog(
    provider: Optional[JevProvider],
    *,
    deterministic_action: str,
    state: dict,
    job_id: Optional[str] = None,
    db: Any = None,
) -> dict:
    """Annotate an already-computed delivery action. Never changes the action."""
    payload = dict(state or {})
    payload["deterministic_action"] = deterministic_action
    resolved, receipt, live = _decide(provider, WATCHDOG_SYMPTOM, payload, job_id=job_id, db=db)
    action = _choice(receipt, "action") if live else None
    suggestion = {"action": action} if live else None
    disagreed = bool(action and action != deterministic_action)
    _note(receipt.outcome, receipt.latency_ms, disagreed)
    _persist(resolved, db, job_id, receipt, suggestion)
    return {
        "action": action,
        "receipt": receipt,
        "applied": False,
    }


def annotate_notification(
    provider: Optional[JevProvider],
    *,
    title: Optional[str],
    message: str,
    deterministic_priority: int,
    recent: Optional[list] = None,
    job_id: Optional[str] = None,
    db: Any = None,
) -> dict:
    """Annotate a notification that ``_allowed`` already admitted. Never changes the push."""
    state = {
        "title": title,
        "message": message,
        "deterministic_priority": deterministic_priority,
        "recent": recent or [],
    }
    resolved, receipt, live = _decide(provider, NOTIFY_QUALITY, state, job_id=job_id, db=db)
    duplicate = _noul(receipt, "duplicate") if live else None
    urgency = _choice(receipt, "urgency") if live else None
    impact = _choice(receipt, "impact") if live else None
    suggested_priority = _URGENCY_TO_PRIORITY.get(urgency) if urgency else None
    suggestion = {"duplicate": duplicate, "urgency": urgency, "impact": impact} if live else None
    deterministic_urgency = _urgency_from_priority(deterministic_priority)
    disagreed = bool(urgency and urgency != deterministic_urgency)
    _note(receipt.outcome, receipt.latency_ms, disagreed)
    _persist(resolved, db, job_id, receipt, suggestion)
    return {
        "duplicate": duplicate,
        "urgency": urgency,
        "impact": impact,
        "suggested_priority": suggested_priority,
        "receipt": receipt,
        "applied": False,
    }


def annotate_model_route(
    provider: Optional[JevProvider],
    *,
    task: str,
    context: Optional[str] = None,
    deterministic_tier: str = "sonnet",
    job_id: Optional[str] = None,
    db: Any = None,
) -> dict:
    """Suggest a Claude Code model tier for an upcoming task. Never switches
    a running session or spawns anything; a session (or /model) still
    decides. An out-of-set answer is treated as no suggestion."""
    state = {
        "task": task,
        "context": context,
        "deterministic_tier": deterministic_tier,
    }
    resolved, receipt, live = _decide(provider, CLAUDE_CODE_MODEL_ROUTE, state, job_id=job_id, db=db)
    tier = _choice(receipt, "tier") if live else None
    if tier not in MODEL_TIERS:
        tier = None
    suggestion = {"tier": tier} if live else None
    disagreed = bool(tier and tier != deterministic_tier)
    _note(receipt.outcome, receipt.latency_ms, disagreed)
    _persist(resolved, db, job_id, receipt, suggestion)
    return {
        "tier": tier,
        "receipt": receipt,
        "applied": False,
    }


def _answer_confidence(receipt: DecisionReceipt, name: str) -> Optional[float]:
    answer = (receipt.answers or {}).get(name) or {}
    value = answer.get("confidence")
    return float(value) if isinstance(value, (int, float)) else None


def annotate_codex_task_route(
    provider: Optional[JevProvider],
    *,
    task: str,
    context: Optional[str] = None,
    current_model: Optional[str] = None,
    context_pressure: str = "unknown",
    job_id: Optional[str] = None,
    db: Any = None,
) -> dict:
    """Describe a Codex task without choosing spend or authorizing execution.

    Jev judges semantic complexity, prompt gaps, and coordination shape. A
    deterministic policy outside this function owns model eligibility, usage
    limits, budgets, concurrency, and every actual spawn or BitCadence send.
    """
    state = {
        "task": task,
        "context": context,
        "current_model": current_model,
        "context_pressure": context_pressure,
    }
    resolved, receipt, live = _decide(provider, CODEX_TASK_ROUTE, state, job_id=job_id, db=db)
    choices = {
        name: _choice(receipt, name) if live else None
        for name in ("task_kind", "complexity", "reasoning_need", "execution_shape")
    }
    flags = {
        name: _noul(receipt, name) if live else None
        for name in (
            "needs_current_information",
            "needs_workspace_evidence",
            "needs_acceptance_criteria",
            "needs_clarification",
        )
    }
    suggestion = {**choices, **flags} if live else None
    _note(receipt.outcome, receipt.latency_ms, False)
    _persist(resolved, db, job_id, receipt, suggestion)
    return {
        **choices,
        **flags,
        "confidence": {
            name: _answer_confidence(receipt, name) if live else None
            for name in choices
        },
        "receipt": receipt,
        "applied": False,
    }
