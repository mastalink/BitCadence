"""J03 shadow operations: Drumline, fleet triage, and notification quality.

Jev may classify, rank, flag, and suggest. It never deletes or rewrites
Drumline history, never changes worker state/stall/retry/crash-loop logic,
never spends or bypasses the ntfy budget, and never authorizes an effect.
Disabled/down Jev constructs no TypeSafe request.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from mco.orchestrator.jev import DecisionReceipt, JevConfig, JevProvider

DRUMLINE_USE_CASE = "bitcadence-ops-drumline"
FLEET_USE_CASE = "bitcadence-ops-fleet"
NOTIFY_USE_CASE = "bitcadence-ops-notify"
QUESTION_SET_VERSION = "1"

DRUMLINE_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "kind": {
        "type": "choice",
        "instructions": "Classify completed-job output. Do not rewrite stored history.",
        "criteria": {
            "fact": "Observable outcome or evidence",
            "decision": "A choice that later agents should keep",
            "handoff": "Transferable remaining work",
            "incident": "Failure or stall that needs operator attention",
        },
    },
    "inject": {
        "type": "noul",
        "instructions": "Should this entry be injected into later recalls?",
    },
    "contradiction": {
        "type": "noul",
        "instructions": "Does this likely contradict earlier Drumline entries?",
    },
    "staleness": {
        "type": "noul",
        "instructions": "Is this likely already stale?",
    },
    "sensitivity": {
        "type": "noul",
        "instructions": "Does this look like a secret, token, or private credential?",
    },
}

FLEET_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "action": {
        "type": "choice",
        "instructions": "Classify the already-computed worker/chain symptom.",
        "criteria": {
            "retry": "Same worker is likely to recover",
            "reroute": "Address the job to a different healthy worker",
            "escalate": "Page an operator; deterministic recovery failed",
            "operator-review": "Needs a human look, not an automatic retry",
            "noise": "Benign or already explained",
        },
    }
}

NOTIFY_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "duplicate": {
        "type": "noul",
        "instructions": "Is this semantically the same as a recent notification?",
    },
    "impact": {
        "type": "choice",
        "instructions": "Urgency/impact annotation. Code still owns ntfy budget.",
        "criteria": {
            "routine": "Informational",
            "degraded": "Work is delayed",
            "urgent": "Operator-facing failure or approval",
        },
    },
}

_metrics: Dict[str, int] = {
    "calls": 0,
    "fallback": 0,
    "disabled": 0,
    "errors": 0,
    "shadow": 0,
}


def metrics_snapshot() -> Dict[str, int]:
    return dict(_metrics)


def reset_metrics() -> None:
    for key in _metrics:
        _metrics[key] = 0


def _count(outcome: str) -> None:
    _metrics["calls"] += 1
    if outcome in _metrics:
        _metrics[outcome] += 1
    elif outcome == "success":
        _metrics["shadow"] += 1
    else:
        _metrics["errors"] += 1


def persist_receipt(db_client: Any, job_id: Optional[str], receipt: DecisionReceipt) -> None:
    """Append a receipt to the immutable audit trail. Never updates or deletes."""
    if db_client is None or not job_id:
        return
    try:
        from mco.orchestrator.audit import record_event
        record_event(
            db_client,
            job_id,
            "jev_shadow_receipt",
            "jev",
            "system",
            receipt.to_dict(),
        )
    except Exception:
        return


def _decide(provider: Optional[JevProvider], use_case: str, questions: Mapping[str, Any], state: Any) -> DecisionReceipt:
    if provider is None:
        provider = JevProvider(JevConfig())
    receipt = provider.decide(
        use_case_id=use_case,
        question_set_version=QUESTION_SET_VERSION,
        state=state,
        questions=questions,
    )
    _count(receipt.outcome)
    return receipt


def annotate_drumline(
    *,
    title: str,
    content: str,
    deterministic_kind: str,
    provider: Optional[JevProvider] = None,
    db_client: Any = None,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Suggest Drumline classification. Caller keeps the stored kind."""
    state = {"title": title, "content": content[:1200], "deterministic_kind": deterministic_kind}
    receipt = _decide(provider, DRUMLINE_USE_CASE, DRUMLINE_QUESTIONS, state)
    persist_receipt(db_client, job_id, receipt)
    suggested = None
    flags = []
    inject = False
    if receipt.outcome in {"shadow", "success"}:
        suggested = (receipt.answers.get("kind") or {}).get("choice")
        inject = float((receipt.answers.get("inject") or {}).get("noul") or 0) >= 0.5
        for name in ("contradiction", "staleness", "sensitivity"):
            if float((receipt.answers.get(name) or {}).get("noul") or 0) >= 0.5:
                flags.append(name)
    return {
        "kind": deterministic_kind,
        "suggested_kind": suggested,
        "inject": inject,
        "flags": flags,
        "receipt": receipt.to_dict(),
        "mutates_history": False,
    }


def annotate_fleet(
    *,
    instance_id: str,
    deterministic_state: str,
    broken: bool,
    stalled: bool,
    provider: Optional[JevProvider] = None,
    db_client: Any = None,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify a symptom after timers/retry/crash-loop state is already known."""
    state = {
        "instance_id": instance_id,
        "deterministic_state": deterministic_state,
        "broken": broken,
        "stalled": stalled,
    }
    receipt = _decide(provider, FLEET_USE_CASE, FLEET_QUESTIONS, state)
    persist_receipt(db_client, job_id, receipt)
    action = None
    if receipt.outcome in {"shadow", "success"}:
        action = (receipt.answers.get("action") or {}).get("choice")
    return {
        "instance_id": instance_id,
        "deterministic_state": deterministic_state,
        "suggested_action": action,
        "receipt": receipt.to_dict(),
        "authorizes_effect": False,
    }


def annotate_notification(
    *,
    title: str,
    message: str,
    priority: int,
    allowed: bool,
    provider: Optional[JevProvider] = None,
) -> Dict[str, Any]:
    """Annotate a notification the budget already accepted or rejected."""
    state = {"title": title, "message": message[:400], "priority": priority, "allowed": allowed}
    receipt = _decide(provider, NOTIFY_USE_CASE, NOTIFY_QUESTIONS, state)
    duplicate = False
    impact = None
    if receipt.outcome in {"shadow", "success"}:
        duplicate = float((receipt.answers.get("duplicate") or {}).get("noul") or 0) >= 0.5
        impact = (receipt.answers.get("impact") or {}).get("choice")
    return {
        "allowed": allowed,
        "semantic_duplicate": duplicate,
        "impact": impact,
        "receipt": receipt.to_dict(),
        "budget_owner": "code",
    }
