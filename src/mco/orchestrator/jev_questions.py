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
CLAUDE_CODE_MODEL_ROUTE = "claude-code-model-route"
CODEX_TASK_ROUTE = "codex-task-route"
SERVICE_JOB_FIT = "service_job_fit"



# Version 2: TypeSafe requires score criteria as an ordered list; v1's dict form was rejected.
_DRUMLINE_OPS_V2: Dict[str, Any] = {
    "use_case_id": DRUMLINE_OPS,
    "version": "2",
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
            "criteria": [
                "Unrelated to the recall query",
                "Directly answers or strongly informs the recall query",
            ],
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


_CLAUDE_CODE_MODEL_ROUTE_V1: Dict[str, Any] = {
    "use_case_id": CLAUDE_CODE_MODEL_ROUTE,
    "version": "1",
    "questions": {
        "tier": {
            "type": "choice",
            "instructions": (
                "A Claude Code session is about to work on the given task "
                "description (and, when present, a short excerpt of recent "
                "conversation for scope). Recommend the cheapest model tier "
                "adequate for the task. This is advisory only: the session, "
                "or a person via /model, still chooses the actual model; no "
                "answer switches a running session or spawns anything."
            ),
            "criteria": {
                "haiku": (
                    "Small, mechanical, or narrow: quick lookups, formatting, "
                    "short summaries, simple file edits, routine status checks"
                ),
                "sonnet": (
                    "Typical day-to-day engineering: multi-file changes, "
                    "debugging, code review, moderate design decisions"
                ),
                "opus": (
                    "High-stakes or heavily ambiguous: architecture decisions, "
                    "security-sensitive changes, long multi-step autonomous "
                    "work, or anything with a costly failure mode"
                ),
            },
        },
    },
}


_CODEX_TASK_ROUTE_V1: Dict[str, Any] = {
    "use_case_id": CODEX_TASK_ROUTE,
    "version": "1",
    "questions": {
        "task_kind": {
            "type": "choice",
            "instructions": "What kind of work does `task` primarily require?",
            "criteria": {
                "answer": "Answer, explain, summarize, or advise without changing an external system.",
                "research": "Inspect current facts, documentation, logs, or a codebase before answering.",
                "implementation": "Create or modify code, configuration, documents, or other artifacts.",
                "review": "Independently evaluate correctness, security, evidence, or readiness.",
                "operations": "Change or diagnose a running service, deployment, account, or infrastructure.",
                "monitoring": "Wait for, watch, or repeatedly reconcile changing external state.",
            },
        },
        "complexity": {
            "type": "choice",
            "instructions": (
                "How much semantic and technical complexity does the requested outcome require, "
                "independent of model cost or remaining account usage?"
            ),
            "criteria": {
                "small": "Narrow, mechanical, reversible, or answerable from one clear source.",
                "standard": "Ordinary professional work with several steps or a modest amount of context.",
                "complex": "Ambiguous or cross-cutting work requiring planning, tools, validation, and edge cases.",
                "frontier": "Exceptionally difficult architecture, incident, security, or long-horizon work where failure is costly.",
            },
        },
        "reasoning_need": {
            "type": "choice",
            "instructions": "What depth of reasoning is needed to complete `task` reliably?",
            "criteria": {
                "low": "Direct execution or lookup with little ambiguity.",
                "medium": "Several connected decisions with normal verification.",
                "high": "Complex logic, competing hypotheses, important edge cases, or independent review.",
                "extreme": "The hardest multi-system or high-stakes reasoning where additional depth materially reduces risk.",
            },
        },
        "execution_shape": {
            "type": "choice",
            "instructions": (
                "Which execution shape best fits `task`? Judge decomposability and duration, not account usage. "
                "BitCadence means durable work that should survive this conversational turn."
            ),
            "criteria": {
                "root_only": "One agent can complete it efficiently in the current conversation.",
                "subagents": "Two or more bounded independent investigations or implementation tracks can run in parallel.",
                "bitcadence": "The work is long-running, unattended, cross-provider, queued, or needs durable lease and receipt tracking.",
                "hybrid": "The current agent should coordinate immediate work plus durable BitCadence execution or independent review.",
            },
        },
        "needs_current_information": {
            "type": "noul",
            "instructions": "Does a correct response require information that may have changed recently?",
        },
        "needs_workspace_evidence": {
            "type": "noul",
            "instructions": "Must the agent inspect the actual workspace, repository, logs, or runtime before deciding?",
        },
        "needs_acceptance_criteria": {
            "type": "noul",
            "instructions": "Would an explicit success condition materially improve execution of `task`?",
        },
        "needs_clarification": {
            "type": "noul",
            "instructions": "Is a missing user choice likely to change the requested outcome materially?",
        },
    },
}


SERVICE_JOB_FIT_VERSION = "1.0.0"

_SERVICE_JOB_FIT_V1: Dict[str, Any] = {
    "use_case_id": SERVICE_JOB_FIT,
    "version": SERVICE_JOB_FIT_VERSION,
    "questions": {
        "fleet_fit": {
            "type": "score",
            "instructions": "Can our fleet deliver a tested, checkable code/automation result?",
            "criteria": [
                "No fit: Requires physical presence, hardware we do not have, or non-software manual labor",
                "Weak fit: Highly subjective, creative, or untestable deliverables without clear code boundaries",
                "Moderate fit: Standard software development or scripting, but complex or ambiguous test boundary",
                "Strong fit: Well-defined coding, API integration, or automation task with clear verification path",
                "Ideal fit: Fully automatable, deterministic, checkable software or script with unambiguous test suite",
            ],
        },
        "value": {
            "type": "score",
            "instructions": "Rate the financial value of this job relative to realistic required engineering effort.",
            "criteria": [
                "Severely underpriced: Disproportionately high effort for negligible budget; unprofitable",
                "Below market: Budget is lower than standard effort warrants",
                "Fair: Compensation is proportionate to expected development and review time",
                "Profitable: Attractive budget with reasonable, well-bounded scope and good margins",
                "Exceptional: High budget for cleanly bounded, highly automatable deliverable",
            ],
        },
        "clarity": {
            "type": "score",
            "instructions": "Are acceptance criteria, requirements, and completion conditions inferable from the description?",
            "criteria": [
                "Vague or incoherent: Lacks actionable details, specifications, or clear deliverables",
                "Ambiguous: General goal stated, but crucial technical requirements and acceptance criteria are missing",
                "Adequate: Core requirements outlined; scope is inferable with standard engineering assumptions",
                "Clear: Specific requirements, deliverables, and expectations are articulated clearly",
                "Exemplary clarity: Precise specifications, explicit inputs/outputs, and definitive acceptance criteria",
            ],
        },
        "client_quality": {
            "type": "score",
            "instructions": "Evaluate the reputation, payment verification, spending history, and hire rate of the client.",
            "criteria": [
                "High risk: Unverified payment, poor ratings, dispute history, or unrealistic punitive terms",
                "Unproven: New account with zero spend, unverified payment, or no historical track record",
                "Average: Verified payment with modest past spend or acceptable rating and hire rate",
                "Established: Verified payment, strong hire rate, 4.5+ rating, and substantial lifetime spend",
                "Premier: Top-tier client with verified payment, high hire rate, excellent ratings, and $10k+ spend",
            ],
        },
        "competition": {
            "type": "score",
            "instructions": "Assess competition level and likelihood of proposals standing out based on proposals count.",
            "criteria": [
                "Overcrowded: 50+ proposals already submitted; low visibility probability",
                "High competition: 20 to 50 proposals submitted",
                "Moderate competition: 10 to 19 proposals submitted",
                "Low competition: 5 to 9 proposals submitted",
                "Open opportunity: Less than 5 proposals submitted; high visibility likelihood",
            ],
        },
        "delivery_risk": {
            "type": "score",
            "instructions": "Assess the risk of delivery failure, scope creep, hostile requirements, or unverified dependencies.",
            "criteria": [
                "Extreme risk: High probability of scope creep, hostile client demands, or untestable third-party blockers",
                "Elevated risk: Significant third-party dependencies, undocumented APIs, or shifting specifications",
                "Moderate risk: Standard project complexity with ordinary integration uncertainties",
                "Low risk: Well-defined scope using mature technologies with minimal external risk factors",
                "Minimal risk: Self-contained, fully testable, robustly bounded deliverable with near-zero external dependencies",
            ],
        },
    },
}


_REGISTRIES: Dict[str, Dict[str, Any]] = {
    DRUMLINE_OPS: _DRUMLINE_OPS_V2,
    WATCHDOG_SYMPTOM: _WATCHDOG_SYMPTOM_V1,
    NOTIFY_QUALITY: _NOTIFY_QUALITY_V1,
    CLAUDE_CODE_MODEL_ROUTE: _CLAUDE_CODE_MODEL_ROUTE_V1,
    CODEX_TASK_ROUTE: _CODEX_TASK_ROUTE_V1,
    SERVICE_JOB_FIT: _SERVICE_JOB_FIT_V1,
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
