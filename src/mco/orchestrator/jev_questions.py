"""Versioned Jev question-set registries for J03 shadow operations.

Each registry is frozen: changing instructions, criteria, or candidate
meanings requires a new version string. Digests use the same canonical
SHA-256 as ``jev._digest``.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from mco.orchestrator.jev import _digest


DRUMLINE_OPS = "drumline-ops"
WATCHDOG_SYMPTOM = "watchdog-symptom"
NOTIFY_QUALITY = "notify-quality"


_DRUMLINE_OPS_V1: Dict[str, Any] = {
    "use_case_id": DRUMLINE_OPS,
    "version": "1",
    "questions": {
        "kind": {
            "type": "choice",
            "instructions": (
                "Classify the completed output. This is an annotation only; "
                "stored Drumline kinds stay fact, decision, lesson, handoff, or artifact."
            ),
            "criteria": {
                "fact": "A durable statement about the world or system",
                "decision": "A choice that was made and should be reused",
                "handoff": "Transferable outcome of completed work for the next agent",
                "incident": "Operational failure or anomaly (annotation only; never stored)",
            },
        },
        "inject": {
            "type": "choice",
            "instructions": (
                "Should this entry be injected into later prompts? Callers ignore "
                "this suggestion; skip must not prevent remember()."
            ),
            "criteria": {
                "inject": "Worth injecting as shared context",
                "skip": "Low value or noisy; do not inject",
                "review": "Needs an operator to review before injection",
            },
        },
        "contradiction": {
            "type": "noul",
            "instructions": "Possible contradiction with existing shared memory.",
        },
        "staleness": {
            "type": "noul",
            "instructions": "The content looks expired, superseded, or time-bound in a way that may no longer hold.",
        },
        "sensitivity": {
            "type": "noul",
            "instructions": "The content appears to contain secrets, credentials, or other sensitive material.",
        },
        "relevance": {
            "type": "score",
            "instructions": "How relevant is this entry to the current recall query?",
            "criteria": {
                "0": "Unrelated to the recall query",
                "1": "Directly answers or strongly informs the recall query",
            },
        },
    },
}


_WATCHDOG_SYMPTOM_V1: Dict[str, Any] = {
    "use_case_id": WATCHDOG_SYMPTOM,
    "version": "1",
    "questions": {
        "action": {
            "type": "choice",
            "instructions": (
                "After deterministic delivery state is already computed, classify "
                "the symptom. Code owns rekick, reroute, escalate, stall timers, "
                "and crash-loop safeguards; this answer must not authorize an effect."
            ),
            "criteria": {
                "retry": "Re-broadcast / rekick the pending job",
                "reroute": "Move the job to a fallback role",
                "escalate": "Mark undeliverable and notify the operator",
                "operator-review": "A chain stalled or needs a human to resume it",
                "noise": "Not a real delivery failure; ignore",
            },
        },
    },
}


_NOTIFY_QUALITY_V1: Dict[str, Any] = {
    "use_case_id": NOTIFY_QUALITY,
    "version": "1",
    "questions": {
        "duplicate": {
            "type": "noul",
            "instructions": "Semantic duplicate of a recent notification, even if the text is not identical.",
        },
        "urgency": {
            "type": "choice",
            "instructions": (
                "Classify urgency. Rate budgets and urgent-bypass stay with ntfy._allowed; "
                "this answer must not suppress, promote, or demote the actual push."
            ),
            "criteria": {
                "routine": "Ordinary operational traffic",
                "elevated": "Notable but not paging",
                "urgent": "Should reach a person even when routine budget is spent",
                "emergency": "Highest-severity failure or page",
            },
        },
        "impact": {
            "type": "choice",
            "instructions": "Likely operational impact if the event is real.",
            "criteria": {
                "low": "Local or informational",
                "medium": "Degraded delivery or delayed work",
                "high": "Mission stalled, data loss, or operator action required",
            },
        },
    },
}


_REGISTRIES: Dict[str, Dict[str, Any]] = {
    DRUMLINE_OPS: _DRUMLINE_OPS_V1,
    WATCHDOG_SYMPTOM: _WATCHDOG_SYMPTOM_V1,
    NOTIFY_QUALITY: _NOTIFY_QUALITY_V1,
}


def _copy_registry(registry: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "use_case_id": registry["use_case_id"],
        "version": registry["version"],
        "questions": copy.deepcopy(registry["questions"]),
    }


def get_registry(use_case_id: str) -> Dict[str, Any]:
    """Return a copy of the frozen registry for ``use_case_id``."""
    try:
        return _copy_registry(_REGISTRIES[use_case_id])
    except KeyError:
        raise KeyError("unknown Jev use case: %s" % use_case_id)


def registry_digest(use_case_id: str) -> str:
    """Canonical SHA-256 of the frozen questions mapping (same as jev._digest)."""
    try:
        registry = _REGISTRIES[use_case_id]
    except KeyError:
        raise KeyError("unknown Jev use case: %s" % use_case_id)
    return _digest(registry["questions"])


def all_registries() -> Dict[str, Dict[str, Any]]:
    """Return copies of every frozen registry, keyed by use-case id."""
    return {use_case_id: _copy_registry(registry) for use_case_id, registry in _REGISTRIES.items()}
